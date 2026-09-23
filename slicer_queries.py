"""Joern queries for the offline slicer; no harness imports or generated model queries.

Each request is one Scala block: candidate locals never survive in the REPL.
Most stages return their JSON as base64 in the block's result, since println can go to
server logs. The candidate-enumeration stages (`FILE_TRANSPORT_STAGES`) instead write the
payload to a file and return only its byte count -- their payload can outgrow what the REPL
renders as a single string value (issue #219, see `file_envelope`).
"""

import json

READERS = (
    "fread|read|pread|readv|recv|recvfrom|recvmsg|fgets|fgetc|getc|getchar|"
    "getline|scanf|fscanf|vscanf|gets|getenv|fgetws|fgetwc|mmap"
)

# Case B (b) sink expansion (issue #219). The GT sinks are dominated by memory-write
# operations that the local-array-argument and double-free patterns miss. WRITERS mirrors
# READERS: calls to these copy/format functions are sinks whose arguments (dest, source,
# size) are backtracked. The 40-GT-sink survey put ~5 sinks as direct calls to these; the
# validation run (which sinks these newly catch vs baseline) is recorded in
# docs/experiments/harness/2026-09-22-case-b-operator-anchors-atom.md (run_id: none).
WRITERS = (
    "memcpy|memmove|memset|strcpy|strncpy|strcat|strncat|stpcpy|bcopy|"
    "sprintf|snprintf|vsprintf|vsnprintf|wmemcpy|wmemmove|wcscpy|wcsncpy|wcscat"
)
# The writer set can be large on big repos. #250 moved the case_a/case_b payload to a file
# (`file_envelope`), so it no longer overruns the REPL return -- but the cap stays: it bounds
# the candidate count, and so the downstream per-candidate work. Repos past it get bounded
# (not full) coverage.
CASE_B_WRITER_CAP = 2000

# Operator-anchored sinks (#219 follow-up). Operator/deref sinks are the biggest GT bucket
# (25/40 = deref-or-field 12 + array-index 10 + pointer-arith 3). This PR anchors the two of
# those it can narrow and bound: array-index WRITES (a subset of the 10) and pointer arithmetic
# (3) -- the "13/40" the PR body counts. Deref/field (12) and array-index READS are NOT anchored
# (a documented recall limit). Raw operators explode, so narrow BEFORE taking, then cap. All
# counts below are ATOM measurements recorded in
# docs/experiments/harness/2026-09-22-case-b-operator-anchors-atom.md (run_id: none). WavPack
# (1020 methods):
#   - array index a[i]: 3042 raw -> 550 when restricted to WRITE targets (a[i] = ...).
#     Kept: writes (the OOB-write sink). Left out: variable-index reads (~1178 raw, ~2x the
#     writes and mostly benign bounds-checked reads) -- a documented recall limit.
#   - pointer arithmetic p +/- i: raw is ~3300; narrowed to a pointer-ish operand it measured
#     1125 on WavPack (of 3309 raw) and 473 on pcre2 (of 1831 raw). "Pointer-ish" is two axes
#     because evalType alone is unreliable: a raw-pointer type (contains "*") OR an operand
#     that is itself a memory access (field/deref/index/addr-of). The second axis is what
#     catches typedef'd pointers -- pcre2's `mb->end_subject - eptr` has operand evalType
#     ANY/PCRE2_SPTR (no "*"), but the operand IS an indirectFieldAccess, so it qualifies.
#     Integer-only arithmetic (both operands plain ints) drops out.
# PER-ANCHOR bound (not a single total): this cap is applied to indexWrites AND to ptrArith
# separately, so the operator population is bounded by 2*cap (<=4000), and with WRITERS
# (CASE_B_WRITER_CAP) the new-sink total is bounded by <=6000. A repo past a cap gets bounded
# (not full) coverage for that anchor.
CASE_B_OPERATOR_CAP = 2000


def literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


# A class file holds a string constant in a CONSTANT_Utf8 entry whose length field is
# 16 bits, so 65,535 bytes is a hard ceiling scalac reports as "String constant is too
# long for the JVM". Escaping can double a chunk, hence the quarter-size split.
JVM_STRING_LIMIT = 65535


def joined_literal(value: str, size: int = JVM_STRING_LIMIT // 4) -> str:
    """Rebuild one long string from several literals at run time.

    `"a" + "b"` will not do: the compiler folds adjacent literals back into the single
    oversized constant this exists to avoid. `mkString` is a call, so it cannot fold.
    """
    parts = [value[index : index + size] for index in range(0, len(value), size)] or [""]
    return "List(" + ", ".join(literal(part) for part in parts) + ").mkString"


def envelope(stage: str, body: str) -> str:
    return (
        "{ import io.shiftleft.semanticcpg.language._; "
        "import io.joern.dataflowengineoss.language._; "
        "import io.shiftleft.codepropertygraph.generated.nodes; "
        f"val result = {{ {body} }}; "
        f'"__SLICER_{stage}__" + java.util.Base64.getEncoder.encodeToString('
        "ujson.write(result).getBytes(java.nio.charset.StandardCharsets.UTF_8)) + "
        '"__END_SLICER__" }'
    )


# Client-substituted placeholder for the out-of-band payload path. The client (JoernClient)
# replaces this with a JSON string literal of a real, writable path before sending.
OUT_PATH_TOKEN = "__SLICER_OUT_PATH__"

# Stages whose query is built with `file_envelope` (payload via file, not the REPL return).
# The client keys file transport on this, not on the token's presence in the query text --
# a repo file or symbol literally named like the token would otherwise be a false positive.
# Must list exactly the stages that call `file_envelope`.
FILE_TRANSPORT_STAGES = frozenset({"case_a", "case_b"})


def file_envelope(stage: str, body: str) -> str:
    """Same as `envelope`, but the payload travels through a FILE, not the REPL return.

    The candidate-enumeration payload (case_a/case_b) can reach several thousand rows on
    large repos. Returned inline as one base64 string, that value overruns what Joern's
    REPL renders as a single result, which comes back empty -- the envelope markers are
    present but the payload between them is gone, so the stage aborts with 0 slices
    (issue #219; the per-set `.take` cap in #241 only halved it, since api+readers can
    still sum to ~2x the cap). Here Joern writes `ujson.write(result)` to `OUT_PATH_TOKEN`
    and the block returns only the byte count, so the REPL return stays tiny regardless of
    how many candidates there are. The client reads the file and checks the byte count.

    Only the byte count is the block's rendered value; `__payload` lives inside a nested
    block so the huge array is never echoed.
    """
    return (
        "{ import io.shiftleft.semanticcpg.language._; "
        "import io.joern.dataflowengineoss.language._; "
        "import io.shiftleft.codepropertygraph.generated.nodes; "
        f'"__SLICER_{stage}__" + {{ '
        f"val __payload = ujson.write({{ {body} }})"
        ".getBytes(java.nio.charset.StandardCharsets.UTF_8); "
        f"java.nio.file.Files.write(java.nio.file.Paths.get({OUT_PATH_TOKEN}), __payload); "
        "__payload.length }.toString + "
        '"__END_SLICER__" }'
    )


def engine_context(limits: dict[str, int] | None = None) -> str:
    args = ", ".join(f"{key} = {value}" for key, value in sorted((limits or {}).items()))
    return (
        "val flowContext = io.joern.dataflowengineoss.queryengine.EngineContext(config = "
        f"io.joern.dataflowengineoss.queryengine.EngineConfig({args}));\n"
    )


LOCATION = """
def loc(n: nodes.AstNode): ujson.Value = ujson.Obj(
  "file" -> n.location.filename, "line" -> n.lineNumber.getOrElse(-1))
def scope(m: nodes.Method): ujson.Value = ujson.Obj(
  "file" -> m.filename, "symbol" -> m.name, "full_name" -> m.fullName,
  "line" -> m.lineNumber.getOrElse(-1))
"""

CALL_IDENTITY = """
def astPath(n: nodes.AstNode): List[Int] = {
  var current = Option(n)
  val path = scala.collection.mutable.ListBuffer.empty[Int]
  while (current.nonEmpty && !current.get.isInstanceOf[nodes.Method]) {
    path.prepend(current.get.order)
    current = current.get.astParentOption
  }
  path.toList
}
def callSelector(c: nodes.Call): ujson.Value = ujson.Obj(
  "line" -> c.lineNumber.getOrElse(-1), "column" -> c.columnNumber.getOrElse(-1),
  "code" -> c.code, "ast_path" -> ujson.Arr.from(astPath(c)))
def argumentInfo(a: nodes.Expression): ujson.Value = ujson.Obj(
  "index" -> a.argumentIndex, "code" -> a.code,
  "line" -> a.lineNumber.getOrElse(-1), "column" -> a.columnNumber.getOrElse(-1))
"""

ANCESTORS = """
      def sourceNode(n: nodes.AstNode): Boolean = n match {
        // C frontend emits METHOD_REF/TYPE_DECL on each function's first line.
        // Their parents are the synthetic <global>, not that source function.
        case _: nodes.MethodRef | _: nodes.TypeDecl | _: nodes.NamespaceBlock |
             _: nodes.Namespace | _: nodes.File => false
        case m: nodes.Method => !m.name.startsWith("<")
        case b: nodes.Block => b.code != "<empty>"
        case _ => true
      }
      def ancestors(n: nodes.AstNode): List[nodes.AstNode] = {
        // A method is already the scope boundary. Climbing from it reaches a
        // TYPE_DECL/NAMESPACE_BLOCK whose AST contains unrelated sibling methods.
        if (n.isInstanceOf[nodes.Method]) return Nil
        val seen = scala.collection.mutable.Set.empty[Long]
        val result = scala.collection.mutable.ListBuffer.empty[nodes.AstNode]
        var parent = n.astParentOption
        while (parent.nonEmpty && sourceNode(parent.get) && seen.add(parent.get.id)) {
          val p = parent.get
          result += p
          parent = if (p.isInstanceOf[nodes.Method]) None else p.astParentOption
        }
        result.toList
      }
"""


def import_query(repo: str, project: str) -> str:
    return envelope(
        "import",
        f"""
      val imported = importCode.c(inputPath={literal(repo)}, projectName={literal(project)})
      require(imported.metaData.nonEmpty, "Imported CPG has no metadata")
      ujson.Obj("imported" -> true)
    """,
    )


def inventory_query(limits: dict[str, int] | None = None) -> str:
    return envelope(
        "inventory",
        engine_context(limits)
        + """
      ujson.Obj(
        "server" -> ujson.Obj("java_version" -> System.getProperty("java.version"),
          "process" -> java.lang.management.ManagementFactory.getRuntimeMXBean.getName,
          "started_ms" -> java.lang.management.ManagementFactory.getRuntimeMXBean.getStartTime,
          "heap_max_bytes" -> Runtime.getRuntime.maxMemory()),
        "files" -> ujson.Arr.from(cpg.file.name.l.distinct.sorted),
        "methods_internal" -> cpg.method.internal.size,
        "calls" -> cpg.call.filterNot(_.name.startsWith("<operator>")).size,
        "calls_unresolved" -> cpg.call.filterNot(_.name.startsWith("<operator>"))
          .filter(_.callee.isEmpty).size,
        "unknown_nodes" -> cpg.unknown.size,
        "flow_sinks_without_method" -> cpg.call.argument.dedup
          .count(n => Option(n.method).isEmpty),
        "engine_config" -> ujson.Obj(
          "maxCallDepth" -> flowContext.config.maxCallDepth,
          "maxArgsToAllow" -> flowContext.config.maxArgsToAllow,
          "maxOutputArgsExpansion" -> flowContext.config.maxOutputArgsExpansion),
        "overlays" -> ujson.Arr.from(cpg.metaData.overlays.l.distinct.sorted))
    """,
    )


# Case A entry-point recovery (issue #219). The original filter kept only methods that are
# never called (`.isEmpty`) — an "uncalled API" heuristic. But a message handler dispatched
# once (e.g. TLS `client_handle_hello`) IS called, so `.isEmpty` dropped exactly the source
# entry points a memory-safety pass wants. Keeping methods called at most K times recovers
# them. Measured on the eval-release samples (ATOM 2026-09-22): sample_24 (Picotls) 501 → 748
# case_a symbols (+247, incl. `client_handle_hello`·`handle_input_tls12`·…); sample_02 21 → 43.
CASE_A_CALL_LIMIT = 1
# The relaxed set explodes on very large repositories: uncapped, sample_01 (OpenBabel) produced
# a case_a payload big enough to overrun Joern's stdout, so the result envelope was truncated
# and the whole slice aborted (0 slices). Measured: capping turns sample_01's abort into 2,198
# candidates (2,000 api + 198 readers on THAT sample). Both `api` and `readers` are capped:
# readers is not guaranteed small (it is every internal method calling read/getc/getline/mmap/…),
# so an uncapped readers set could overrun the same way on a repo with many such methods.
# A repo past the cap gets bounded coverage, not a failed slice. NOTE: `.take` keeps the first N
# in CPG traversal order, which is not a documented/stable order, so a capped run's coverage is
# an *unspecified* first-N subset (see known_limitations / measurement_notes, and D77 §4.5).
# The stdout-overrun above is the pre-#250 mechanism; #250 moved the payload to a file, so the
# cap now bounds the candidate count (and downstream per-candidate work), not the transport.
# (The K=1 filter uses `.take(LIMIT+1).size` so a common name like `read` short-circuits at
#  LIMIT+1 calls instead of enumerating its whole call set — semantically == `.size <= LIMIT`.)
CASE_A_CANDIDATE_CAP = 2000


def candidates_a_query() -> str:
    return file_envelope(
        "case_a",
        LOCATION
        + f"""
      val api = cpg.method.internal.filterNot(_.name.startsWith("<"))
        .filter(m =>
          cpg.call.nameExact(m.name).take({CASE_A_CALL_LIMIT} + 1).size <= {CASE_A_CALL_LIMIT})
        .filter(_.parameter.size > 0).take({CASE_A_CANDIDATE_CAP}).l
      val readers = cpg.method.internal.filter(_.call.name("({READERS})").nonEmpty)
        .take({CASE_A_CANDIDATE_CAP}).l
      ujson.Arr.from((api ++ readers).distinct.map(m => ujson.Obj(
        "symbol" -> m.name, "file" -> m.filename, "line" -> m.lineNumber.getOrElse(-1),
        "scope" -> scope(m), "anchors" -> ujson.Arr(loc(m)))))
    """,
    )


def candidates_b_query(limits: dict[str, int] | None = None) -> str:
    body = (
        LOCATION
        + CALL_IDENTITY
        + engine_context(limits)
        + """
      val arrays = cpg.call.filterNot(_.name.startsWith("<operator>"))
        .filter(c => c.argument.isIdentifier.refsTo.collect { case l: nodes.Local => l }
          .exists(_.typeFullName.contains("["))).l
      // Keep the parameter index: a wrapper may free its second argument, not its first.
      val dealloc = cpg.method.internal.l.flatMap(m => m.parameter.l.filter(p =>
        m.ast.isCall.nameExact("free").argument(1)
          .reachableByFlows(List(p))(flowContext).hasNext).map(p => (m.name, p.index)))
      val positions = (dealloc :+ ("free", 1)).groupMap(_._1)(_._2)
      val releases = cpg.call.l.filter(c => positions.contains(c.name)).flatMap(c =>
        c.argument.isIdentifier.filter(i => positions(c.name).contains(i.argumentIndex))
          .flatMap(i => i.refsTo.map(d => ((c.method.id, d.id), c))).l)
      // Declaration identity prevents same-spelling variables in nested scopes merging.
      val repeated = releases.groupMap(_._1)(_._2).values
        .map(_.distinct).filter(_.size >= 2).toList
      val repeatedCalls = repeated.flatten.distinct
      val writers = cpg.call.name("(__WRITERS__)").take(__WCAP__).l
      // Operator anchors, narrowed before .take so the cap bounds a real subset (#219).
      val indexWrites = cpg.call.nameExact("<operator>.indirectIndexAccess")
        .filter(c => c.inCall.nameExact("<operator>.assignment").argument
          .argumentIndex(1).contains(c)).take(__OPCAP__).l
      val ptrArith = cpg.call.name("<operator>.(addition|subtraction)")
        .filter(c => c.argument.evalType.exists(_.contains("*")) ||
          c.argument.isCall.name("<operator>.(indirectFieldAccess|fieldAccess|" +
            "indirection|indirectIndexAccess|addressOf)").nonEmpty).take(__OPCAP__).l
      val operators = (indexWrites ++ ptrArith).distinct
      ujson.Arr.from((arrays ++ repeatedCalls ++ writers ++ operators).distinct.map(c => {
        val anchors = (List(c) ++ repeated.filter(_.contains(c)).flatten).distinct
        ujson.Obj("symbol" -> c.name, "file" -> c.location.filename,
          "line" -> c.lineNumber.getOrElse(-1), "scope" -> scope(c.method),
          "selector" -> callSelector(c),
          "reasons" -> ujson.Arr.from(List(
            Option.when(arrays.contains(c))("local_array_argument"),
            Option.when(repeatedCalls.contains(c))("repeated_release"),
            Option.when(writers.contains(c))("dangerous_write"),
            Option.when(indexWrites.contains(c))("index_write"),
            Option.when(ptrArith.contains(c))("pointer_arithmetic")).flatten),
          "trigger_arguments" -> ujson.Arr.from(c.argument.l.filter(a =>
            a.start.isIdentifier.refsTo.collect { case l: nodes.Local => l }
              .exists(_.typeFullName.contains("[")) ||
            (repeatedCalls.contains(c) && positions.getOrElse(c.name, Nil)
              .contains(a.argumentIndex)) ||
            writers.contains(c) || operators.contains(c)).map(argumentInfo)),
          "sink_arguments" -> ujson.Arr.from(c.argument.l.map(argumentInfo)),
          "anchors" -> ujson.Arr.from(anchors.map(loc)))
      }))
    """
    )
    body = (
        body.replace("__WRITERS__", WRITERS)
        .replace("__WCAP__", str(CASE_B_WRITER_CAP))
        .replace("__OPCAP__", str(CASE_B_OPERATOR_CAP))
    )
    return file_envelope("case_b", body)


def method_selector(scopes: list[dict]) -> str:
    predicates = [
        f"(m.filename == {literal(s['file'])} && "
        f"m.fullName == {literal(s['full_name'])} && "
        f"m.lineNumber.getOrElse(-1) == {s['line']})"
        for s in scopes
    ]
    return "cpg.method.internal.filter(m => " + " || ".join(predicates) + ").l"


def flows_query(
    scopes: list[dict], max_flows: int | None, limits: dict[str, int] | None = None
) -> str:
    # One extra flow proves truncation; exactly N completed flows are not truncated.
    limit = "" if max_flows is None else f".take({max_flows + 1})"
    retained = "observed" if max_flows is None else f"observed.take({max_flows})"
    truncated = "false" if max_flows is None else f"(observed.size > {max_flows})"
    return envelope(
        "flows",
        LOCATION
        + engine_context(limits)
        + f"""
      val methods = {method_selector(scopes)}
      require(methods.nonEmpty, "Candidate method disappeared")
      val srcs = methods.sortBy(m => (m.filename, m.lineNumber.getOrElse(-1), m.fullName))
        .flatMap(_.parameter.l.sortBy(p => (p.index, p.code, p.typeFullName)))
      def nodeKey(n: nodes.AstNode): String = ujson.write(ujson.Arr(
        n.location.filename, n.lineNumber.getOrElse(-1), n.columnNumber.getOrElse(-1), n.code))
      val sinks = cpg.call.argument.filter(n => Option(n.method).nonEmpty).l.distinct.sortBy(n => (
        nodeKey(n), n.argumentIndex, n.method.fullName))
      // Each invocation builds an engine. Batch by enclosing method instead of
      // rebuilding it for every argument; the cap stops subsequent method batches.
      val batches = sinks.groupBy(_.method).toList.sortBy {{ case (m, _) =>
        (m.filename, m.lineNumber.getOrElse(-1), m.fullName)
      }}.map(_._2)
      val observed = (if (srcs.isEmpty) Iterator.empty else batches.iterator).flatMap(batch =>
        batch.iterator.reachableByFlows(srcs)(flowContext).toList.sortBy(p =>
          ujson.write(ujson.Arr.from(p.elements.map(nodeKey))))){limit}.toList
      val kept = {retained}
      ujson.Obj("flow_count" -> kept.size, "truncated" -> {truncated},
        "lines" -> ujson.Arr.from(kept.flatMap(_.elements).map(loc)))
    """,
    )


def context_query(points: list[tuple[str, int]], repo: str = "") -> str:
    encoded = joined_literal(json.dumps(points, ensure_ascii=True))
    return envelope(
        "context",
        LOCATION
        + ANCESTORS
        + f"""
      val points = ujson.read({encoded}).arr.map(p => (p(0).str, p(1).num.toInt)).toSet
      def filename(n: nodes.AstNode): String = n.location.filename.replace('\\\\', '/')
        .stripPrefix({literal(repo.rstrip("/") + "/")}).stripPrefix("./")
      val relevantMethods = cpg.method.internal.filter(m => points.exists {{ case (f, line) =>
        filename(m) == f && m.lineNumber.exists(_ <= line) &&
          m.lineNumberEnd.forall(_ >= line)
      }}).l
      val all = relevantMethods.flatMap(_.ast.l).filter(sourceNode).distinct
      val selected = scala.collection.mutable.Set.empty[nodes.AstNode]
      selected ++= all.filter(n => points.contains((filename(n),
        n.lineNumber.getOrElse(-1))))
      var changed = true
      while (changed) {{
        val before = selected.size
        val parents = selected.toList.flatMap(ancestors)
        val methods = (selected.toList ++ parents).collect {{ case m: nodes.Method => m }}
        val controls = parents.collect {{ case c: nodes.ControlStructure => c }}
        selected ++= parents
        selected ++= controls.flatMap(_.condition.ast.l)
        // Labels/jumps are siblings in the AST, not ancestors of data-flow nodes.
        // Retain their guards on the next fixed-point pass as well.
        selected ++= controls.flatMap(_.ast.l).filter(n =>
          n.isInstanceOf[nodes.JumpTarget] || (n match {{
            case c: nodes.ControlStructure =>
              Set("BREAK", "CONTINUE", "GOTO").contains(c.controlStructureType)
            case _ => false
          }}))
        selected ++= methods.flatMap(_.ast.l).filter(n =>
          n.isInstanceOf[nodes.JumpTarget] || (n match {{
            case c: nodes.ControlStructure => c.controlStructureType == "GOTO"
            case _ => false
          }}))
        selected ++= methods.flatMap(_.ast.isReturn.l)
        val ids = selected.toList.flatMap {{
          case i: nodes.Identifier => List(i)
          // Only expressions we actually need may contribute descendants. A type
          // declaration can share a line with an entry, but owns sibling methods.
          case c: nodes.Call => c.ast.isIdentifier.l
          case r: nodes.Return => r.ast.isIdentifier.l
          case _ => Nil
        }}
        val declarations = ids.flatMap(_.refsTo.l).collect {{
          case l: nodes.Local => l
          case p: nodes.MethodParameterIn => p
        }}
        selected ++= declarations
        val declarationIds = declarations.map(_.id).toSet
        selected ++= cpg.assignment.filter(a => a.target.ast.isIdentifier.refsTo
          .exists(d => declarationIds.contains(d.id))).l
        changed = selected.size != before
      }}
      ujson.Arr.from(selected.toList.map(loc))
    """,
    )
