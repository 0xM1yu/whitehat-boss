"""Export bounded CPG evidence for input handling next to a sink DDG slice.

This is intentionally a graph inventory, not a list of parser names.  The
attach stage decides which of the discovered calls actually belongs in the
POV source, using the retained handler and the same input state.
"""

from __future__ import annotations

import json

from slicer_queries import envelope, joined_literal, literal


def input_evidence_query(
    repo: str, points: list[tuple[str, int]], *,
    max_methods: int = 400, max_depth: int = 6,
) -> str:
    """Inventory reads, guards and resolved calls around DDG-retained methods.

    The CPG is queried while it is still open.  Only methods containing a DDG
    point seed the walk; call descendants are bounded and are *candidates*, not
    automatically selected source text.
    """
    encoded = joined_literal(json.dumps([list(p) for p in points], ensure_ascii=True))
    body = r"""
      val repoPrefix = __REPO__
      def filename(m: nodes.Method): String = m.filename.replace('\\', '/')
        .stripPrefix(repoPrefix).stripPrefix("./")
      val points = ujson.read(__POINTS__).arr.map(p => (p(0).str, p(1).num.toInt)).toSet
      val byFile = points.groupBy(_._1).view.mapValues(_.map(_._2)).toMap
      val roots = cpg.method.internal.filter(m => m.name != "<global>" &&
        m.lineNumber.exists(start =>
        byFile.getOrElse(filename(m), Set.empty[Int]).exists(line =>
          line >= start && m.lineNumberEnd.forall(_ >= line)))).l.distinct
        .sortBy(m => (filename(m), m.lineNumber.getOrElse(-1), m.fullName))
      val queue = scala.collection.mutable.Queue.empty[(nodes.Method, Int)]
      roots.foreach(m => queue.enqueue((m, 0)))
      val seen = scala.collection.mutable.LinkedHashMap.empty[Long, (nodes.Method, Int)]
      val edges = scala.collection.mutable.ArrayBuffer.empty[ujson.Value]
      var truncated = false
      while (queue.nonEmpty) {
        val (m, depth) = queue.dequeue()
        if (!seen.contains(m.id)) {
          if (seen.size >= __MAX_METHODS__) truncated = true
          else {
            seen(m.id) = ((m, depth))
            if (depth < __MAX_DEPTH__) {
              m.call.filterNot(_.name.startsWith("<operator>")).l
                .sortBy(c => (c.lineNumber.getOrElse(-1), c.columnNumber.getOrElse(-1), c.code))
                .foreach(c => c.callee.filterNot(_.isExternal).l
                  .sortBy(x => (filename(x), x.lineNumber.getOrElse(-1), x.fullName))
                  .foreach(target => {
                    edges += ujson.Obj("caller" -> m.id.toString,
                      "callee" -> target.id.toString,
                      "file" -> filename(m), "line" -> c.lineNumber.getOrElse(-1),
                      "code" -> c.code,
                      "args" -> ujson.Arr.from(c.argument.l.sortBy(_.argumentIndex).map(a =>
                        ujson.Obj("index" -> a.argumentIndex,
                          "names" -> ujson.Arr.from(a.ast.isIdentifier.l.map(_.name).distinct
                            .map(ujson.Str(_)))))))
                    if (!seen.contains(target.id) && seen.size + queue.size < __MAX_METHODS__)
                      queue.enqueue((target, depth + 1))
                    else if (!seen.contains(target.id)) truncated = true
                  }))
            }
          }
        }
      }
      def accessIdentifiers(call: nodes.Call): List[nodes.Identifier] =
        call.ast.isIdentifier.l
      val methods = seen.values.toList.map { case (m, depth) =>
        val reads = m.ast.isCall.filter(c =>
          c.name == "<operator>.indirection" ||
          c.name == "<operator>.indirectIndexAccess").l
          .flatMap(c => accessIdentifiers(c).map(i =>
            (i.name, i.lineNumber.getOrElse(-1), i.typeFullName)))
          .distinct.take(120)
        val guards = m.controlStructure.l.flatMap(c => {
          val accessed = c.condition.ast.isCall.filter(x =>
            x.name == "<operator>.indirection" ||
            x.name == "<operator>.indirectIndexAccess").l
          val names = accessed.flatMap(accessIdentifiers).map(_.name).distinct
          if (names.isEmpty) Nil else List(ujson.Obj(
            "line" -> c.lineNumber.getOrElse(-1),
            "names" -> ujson.Arr.from(names.map(ujson.Str(_))),
            "kind" -> c.controlStructureType))
        }).take(80)
        ujson.Obj("id" -> m.id.toString, "file" -> filename(m),
          "name" -> m.name, "full_name" -> m.fullName,
          "line_start" -> m.lineNumber.getOrElse(-1),
          "line_end" -> m.lineNumberEnd.getOrElse(-1),
          "depth" -> depth,
          "parameters" -> ujson.Arr.from(m.parameter.l.map(p =>
            ujson.Obj("index" -> p.index, "name" -> p.name))),
          "ddg_lines" -> ujson.Arr.from(byFile.getOrElse(filename(m), Set.empty[Int])
            .filter(line => m.lineNumber.exists(_ <= line) && m.lineNumberEnd.forall(_ >= line))
            .toList.sorted.map(ujson.Num(_))),
          "reads" -> ujson.Arr.from(reads.map { case (name, line, typ) =>
            ujson.Obj("name" -> name, "line" -> line, "type" -> typ) }),
          "guards" -> ujson.Arr.from(guards))
      }
      ujson.Obj("version" -> 1, "status" -> "ok",
        "root_count" -> roots.size, "method_count" -> methods.size,
        "truncated" -> truncated,
        "methods" -> ujson.Arr.from(methods),
        "calls" -> ujson.Arr.from(edges.distinct))
    """
    return envelope(
        "input_evidence",
        body.replace("__REPO__", literal(repo.rstrip("/") + "/"))
        .replace("__POINTS__", encoded)
        .replace("__MAX_METHODS__", str(max_methods))
        .replace("__MAX_DEPTH__", str(max_depth)),
        timed=True,
    )
