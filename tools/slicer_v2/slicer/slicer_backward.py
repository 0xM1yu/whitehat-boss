"""Sink-first DDG traversal, with explicit call-site stacks and bounded expansion.

The result is a dependency graph, not reachableByFlows path enumeration. Node
counts must never be reported as flow_count.
"""

from slicer_queries import CALL_IDENTITY, LOCATION, envelope, literal, method_selector


def backward_b_query(
    candidate: dict, *, max_depth: int | None, max_nodes: int | None, max_call_depth: int
) -> str:
    selector = candidate["selector"]
    path = "List(" + ",".join(str(n) for n in selector["ast_path"]) + ")"
    return envelope(
        "backward",
        LOCATION
        + CALL_IDENTITY
        + f"""
      val methods = {method_selector(candidate["scopes"])}
      val matches = methods.flatMap(_.ast.isCall.l).filter(c =>
        c.name == {literal(candidate["entry"]["symbol"])} &&
        c.lineNumber.getOrElse(-1) == {selector["line"]} &&
        c.columnNumber.getOrElse(-1) == {selector["column"]} &&
        c.code == {literal(selector["code"])} && astPath(c) == {path}).distinct
      require(matches.size == 1, "Sink selector is missing or ambiguous")
      val sink = matches.head
      mark("select_sink")
      case class State(node: nodes.CfgNode, stack: List[nodes.Call], depth: Int)
      def key(n: nodes.CfgNode): String = within("key_inclusive") {{
        count("key_calls")
        ujson.write(ujson.Arr(
          n.location.filename, n.lineNumber.getOrElse(-1), n.columnNumber.getOrElse(-1),
          n.label, n.code, Option(n.method).map(_.fullName).getOrElse(""),
          ujson.Arr.from(n match {{ case a: nodes.AstNode => astPath(a); case _ => Nil }})))
      }}
      def stateKey(s: State): String = key(s.node) + s.stack.map(key).mkString("[", ",", "]")
      val queue = scala.collection.mutable.Queue.empty[State]
      val seen = scala.collection.mutable.Set.empty[String]
      val nodesSeen = scala.collection.mutable.Map.empty[String, nodes.CfgNode]
      val edges = scala.collection.mutable.Set.empty[(String, String)]
      val cuts = scala.collection.mutable.Set.empty[String]
      val boundaries = scala.collection.mutable.Map.empty[String, ujson.Value]
      def boundary(n: nodes.CfgNode, reason: String): Unit = {{
        val value = ujson.Obj("reason" -> reason, "location" -> loc(n))
        boundaries.update(ujson.write(value), value)
      }}
      def offer(s: State): Unit = {{
        val k = stateKey(s)
        if (!seen.contains(k)) {{
          if (s.depth > {max_depth if max_depth is not None else 2147483646})
            cuts += "backward_depth"
          else if (seen.size >= {max_nodes if max_nodes is not None else 2147483646})
            cuts += "backward_states"
          else {{ seen += k; queue.enqueue(s) }}
        }}
      }}
      sink.argument.l.sortBy(key).foreach(n => offer(State(n, Nil, 0)))
      while (queue.nonEmpty) {{
        val current = queue.dequeue()
        val n = current.node
        nodesSeen.update(key(n), n)
        val next = scala.collection.mutable.ListBuffer.empty[State]
        n.start.ddgIn.l.sortBy(key).foreach(p =>
          next += State(p, current.stack, current.depth + 1))
        n match {{
          case p: nodes.MethodParameterIn =>
            val callers = if (current.stack.nonEmpty) current.stack.take(1)
                          else p.method.callIn.l.sortBy(key)
            if (callers.isEmpty) boundary(p, "entry_parameter")
            callers.foreach(c => c.argument.l.filter(_.argumentIndex == p.index).foreach(a =>
              next += State(a, current.stack.drop(1), current.depth + 1)))
          case c: nodes.Call if !c.name.startsWith("<operator>") =>
            val callees = c.callee.filterNot(_.isExternal).l
            if (callees.isEmpty) boundary(c, "external_or_unresolved_call")
            else if (current.stack.size >= {max_call_depth}) cuts += "call_depth"
            else callees.foreach(m => m.ast.isReturn.l.foreach(r =>
              next += State(r, c :: current.stack, current.depth + 1)))
          case _ => ()
        }}
        if (next.isEmpty) boundary(n, "dependency_root")
        next.toList.sortBy(stateKey).foreach(s => {{
          offer(s)
          if (seen.contains(stateKey(s))) edges += ((stateKey(s), stateKey(current)))
        }})
      }}
      mark("traversal")
      val locations = nodesSeen.toList.sortBy(_._1).flatMap {{ case (_, n) => n match {{
        case a: nodes.AstNode if a.lineNumber.exists(_ > 0) => List(loc(a))
        case _ => Nil
      }} }}
      mark("locations")
      ujson.Obj("flow_count" -> ujson.Null, "truncated" -> cuts.nonEmpty,
        "lines" -> ujson.Arr.from(locations), "node_count" -> nodesSeen.size,
        "state_count" -> seen.size, "dependency_count" -> edges.size,
        "boundaries" -> ujson.Arr.from(boundaries.toList.sortBy(_._1).map(_._2)),
        "truncation_reasons" -> ujson.Arr.from(cuts.toList.sorted))
    """,
        timed=True,
    )
