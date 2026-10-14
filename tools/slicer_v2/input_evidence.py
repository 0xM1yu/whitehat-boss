"""Export bounded CPG evidence for input handling next to a sink DDG slice (targeted mode).

This is a graph inventory, not a list of parser names: while the CPG is still open it records,
for the methods that hold a DDG point and the internal callees reachable from them, which
pointers are read (`*p`, `p[i]`), which conditions test such a read, and the resolved call
edges with their argument names. `input_context` decides, from that and the syntax index, which
functions are worth showing.

Every cap below is an author-chosen bound (PR #638, `slicer_v2/slicer/input_evidence.py`) with
**no measurement behind it** (CLAUDE.md section 0): none of them was derived from a corpus and
none was swept. A run that reaches one says so in `truncation_reasons`; a consumer must not read
an absent reason as "the cap was never near".
"""

from __future__ import annotations

import json

from slicer_queries import envelope, joined_literal, literal

VERSION = 1
# Methods kept in one inventory (the DDG-holding methods plus their callees, breadth first).
# Unmeasured -- the PR gave no corpus count.
MAX_METHODS = 400
# Call depth walked from the DDG-holding methods. Unmeasured.
MAX_DEPTH = 6
# Pointer reads kept per method. Unmeasured.
MAX_READS = 120
# Input-testing conditions kept per method. Unmeasured.
MAX_GUARDS = 80
REASONS = ("methods", "depth", "reads", "guards")


def input_evidence_query(
    repo: str,
    points: list[tuple[str, int]],
    *,
    max_methods: int = MAX_METHODS,
    max_depth: int = MAX_DEPTH,
) -> str:
    """Inventory reads, guards and resolved calls around DDG-retained methods.

    Only methods containing a DDG point seed the walk; call descendants are bounded and are
    *candidates*, not automatically selected source text. Every list is sorted by a key that does
    not depend on the traversal's hash order, so the same CPG and points give the same text.
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
      val cuts = scala.collection.mutable.Set.empty[String]
      def internalCalls(m: nodes.Method) = m.call.filterNot(_.name.startsWith("<operator>")).l
        .sortBy(c => (c.lineNumber.getOrElse(-1), c.columnNumber.getOrElse(-1), c.code))
      while (queue.nonEmpty) {
        val (m, depth) = queue.dequeue()
        if (!seen.contains(m.id)) {
          if (seen.size >= __MAX_METHODS__) cuts += "methods"
          else {
            seen(m.id) = ((m, depth))
            if (depth < __MAX_DEPTH__) {
              internalCalls(m).foreach(c => c.callee.filterNot(_.isExternal).l
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
                    else if (!seen.contains(target.id)) cuts += "methods"
                  }))
            } else if (internalCalls(m).exists(c => c.callee.filterNot(_.isExternal).nonEmpty)) {
              // The walk stops here although this method calls an internal function.
              cuts += "depth"
            }
          }
        }
      }
      def accessIdentifiers(call: nodes.Call): List[nodes.Identifier] =
        call.ast.isIdentifier.l
      val methods = seen.values.toList.map { case (m, depth) =>
        val readsAll = m.ast.isCall.filter(c =>
          c.name == "<operator>.indirection" ||
          c.name == "<operator>.indirectIndexAccess").l
          .flatMap(c => accessIdentifiers(c).map(i =>
            (i.name, i.lineNumber.getOrElse(-1), i.typeFullName)))
          .distinct
        val readsCut = readsAll.size > __MAX_READS__
        if (readsCut) cuts += "reads"
        val reads = readsAll.take(__MAX_READS__)
        val guardsAll = m.controlStructure.l.flatMap(c => {
          val accessed = c.condition.ast.isCall.filter(x =>
            x.name == "<operator>.indirection" ||
            x.name == "<operator>.indirectIndexAccess").l
          val names = accessed.flatMap(accessIdentifiers).map(_.name).distinct
          if (names.isEmpty) Nil else List(ujson.Obj(
            "line" -> c.lineNumber.getOrElse(-1),
            "names" -> ujson.Arr.from(names.map(ujson.Str(_))),
            "kind" -> c.controlStructureType))
        })
        val guardsCut = guardsAll.size > __MAX_GUARDS__
        if (guardsCut) cuts += "guards"
        val guards = guardsAll.take(__MAX_GUARDS__)
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
          "reads_truncated" -> readsCut,
          "guards" -> ujson.Arr.from(guards),
          "guards_truncated" -> guardsCut)
      }
      ujson.Obj("version" -> __VERSION__, "status" -> "ok",
        "root_count" -> roots.size, "method_count" -> methods.size,
        "truncated" -> cuts.nonEmpty,
        "truncation_reasons" -> ujson.Arr.from(cuts.toList.sorted.map(ujson.Str(_))),
        "methods" -> ujson.Arr.from(methods),
        "calls" -> ujson.Arr.from(edges.distinct))
    """
    return envelope(
        "input_evidence",
        body.replace("__REPO__", literal(repo.rstrip("/") + "/"))
        .replace("__POINTS__", encoded)
        .replace("__MAX_METHODS__", str(max_methods))
        .replace("__MAX_DEPTH__", str(max_depth))
        .replace("__MAX_READS__", str(MAX_READS))
        .replace("__MAX_GUARDS__", str(MAX_GUARDS))
        .replace("__VERSION__", str(VERSION)),
        timed=True,
    )


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_int(value: object) -> bool:
    return type(value) is int


def valid_input_evidence(data: object) -> bool:
    """Shape check run before anything consumes the payload (`slicer.validate_payload`)."""
    if not isinstance(data, dict):
        return False
    methods, calls = data.get("methods"), data.get("calls")
    reasons = data.get("truncation_reasons")
    if not (
        data.get("version") == VERSION
        and data.get("status") == "ok"
        and type(data.get("truncated")) is bool
        and isinstance(reasons, list)
        and all(reason in REASONS for reason in reasons)
        and data["truncated"] == bool(reasons)
        and isinstance(methods, list)
        and isinstance(calls, list)
    ):
        return False
    return all(
        isinstance(m, dict)
        and _is_str(m.get("id"))
        and _is_str(m.get("file"))
        and _is_str(m.get("name"))
        and _is_int(m.get("line_start"))
        and _is_int(m.get("line_end"))
        and isinstance(m.get("reads"), list)
        and isinstance(m.get("guards"), list)
        and type(m.get("reads_truncated")) is bool
        and type(m.get("guards_truncated")) is bool
        for m in methods
    ) and all(
        isinstance(c, dict)
        and _is_str(c.get("caller"))
        and _is_str(c.get("callee"))
        and _is_str(c.get("file"))
        and _is_int(c.get("line"))
        and isinstance(c.get("args"), list)
        for c in calls
    )
