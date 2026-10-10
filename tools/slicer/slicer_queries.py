"""Joern queries for the offline slicer; no harness imports or generated model queries.

Each builder returns one self-contained Scala block: candidate locals never survive in the
REPL. The per-candidate builders (flows, context, backward) are also the source that
`slicer_library` compiles once per session into functions; the run sends calls to those.
Most stages return their JSON as base64 in the block's result, since println can go to
server logs. The candidate-enumeration stages and inventory (`FILE_TRANSPORT_STAGES`) instead
write the payload to a file and return only its byte count -- their payload can outgrow what the
REPL renders as a single string value (issues #219 and #404, see `file_envelope`).
"""

import json
from collections.abc import Collection

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

# Injection sinks (2026-09-26 injection candidate-capture measurement). The injection eval set's
# GT sinks are command/SQL execution, not memory writes: sample_13's sink `make_tunnel` calls
# `apr_proc_create` (process spawn) and matched NONE of READERS/WRITERS/operator patterns, so it
# was never a Case B candidate. This mirrors WRITERS -- a call to one of these is a sink whose
# arguments (all of them, like WRITERS -- backward_b seeds every sink arg, not only the command/
# query string) are backtracked. Names are the standard command-exec and
# DB-query APIs plus `apr_proc_create` (the one observed in the measured miss). Measured on a local
# candidate-capture run (no persisted run_id); the durable figure comes from the full slicing run.
EXEC_SINKS = (
    "system|popen|wsystem|_wsystem|"
    "execl|execlp|execle|execv|execvp|execvpe|execve|execveat|"
    "posix_spawn|posix_spawnp|apr_proc_create|CreateProcess|CreateProcessA|CreateProcessW|"
    "sqlite3_exec|sqlite3_prepare|sqlite3_prepare_v2|sqlite3_prepare_v3|sqlite3_prepare16|"
    "mysql_query|mysql_real_query|PQexec|PQexecParams|PQprepare"
)
CASE_B_EXEC_CAP = 2000

# Operator-anchored sinks (#219 follow-up). Operator/deref sinks are the biggest GT bucket
# (25/40 = deref-or-field 12 + array-index 10 + pointer-arith 3). The write-context anchors
# narrow-and-bound: array-index WRITES (subset of the 10), deref/field WRITES (`p->x=`·`*p=`·
# `s.f=`, subset of the 12 -- added 2026-09-26), and pointer arithmetic (3). Array-index and
# deref/field READS are still NOT anchored (a documented recall limit). The ~2x reads-to-writes
# ratio below was measured for ARRAY-INDEX only (mostly benign bounds-checked reads); the
# deref/field read/write ratio is NOT measured -- do not assume ~2x there. Raw operators explode,
# so narrow BEFORE taking, then cap. All
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
# PER-ANCHOR bound (not a single total): this cap is applied to indexWrites, derefWrites AND
# ptrArith separately, so the operator population is bounded by 3*cap (<=6000), and with WRITERS
# (CASE_B_WRITER_CAP) and EXEC_SINKS (CASE_B_EXEC_CAP) the new-sink total is bounded by <=10,000
# (writers 2000 + exec 2000 + 3 operator types * 2000). A repo past a cap gets bounded
# (not full) coverage for that anchor.
CASE_B_OPERATOR_CAP = 2000

# Per-method anchor budget (candidate starvation fix). Each cap above used to be a FLAT
# `.take(cap)` over the whole CPG in traversal order, so on a large repo a few busy functions
# fill the first `cap` anchors and a GT function's anchors -- which exist -- never get taken
# (they rank past `cap`); raising the cap only papers that over at the price of a candidate
# explosion. Budgeting instead takes at most this many anchors PER enclosing method (grouped by
# method id) BEFORE the global cap, ROUND-ROBIN (1 anchor from every method, then a 2nd from
# every method, ... up to this many rounds). Round-robin so the ceiling is spent breadth-first:
# every method gets its first anchor before any method gets a second, so a GT method is captured
# as long as it is among the first `cap` methods by id -- it no longer has to win a global
# first-N race that busy functions dominate. The cap stays the worst-case ceiling and raising it
# does NOT reintroduce the explosion, because within-function redundancy (many field writes in one
# function) collapses to this budget.
# The empirical level-2 recall/candidate figures that chose k=16 -- and that confirmed it recovers
# the starved GT with no regression and no explosion vs the flat cap -- are a slicer QUALITY
# measurement. Per docs/experiments/README.md that kind of measurement's durable source is the
# team Notion (not this file, not docs/experiments/). This comment carries the mechanism only; the
# k=16 value's numbers live in the team Notion.
CASE_B_PER_METHOD = 16

# Materialization guard (#311 review): budget() groups its input by method, so it needs the list
# materialized -- unlike the old flat `.take(cap)`, which stopped the lazy traversal at `cap`.
# Cap each anchor traversal at this bound BEFORE `.l` so a pathological/huge CPG cannot OOM or
# time out the whole case_b query before any candidate is produced. It is far above any per-type
# count seen (writers/index/ptr are single-digit thousands), so it does not bite normal repos --
# a repo past it gets the first-N by traversal order for that type (a documented extreme-case
# limit, same shape as the ceiling).
CASE_B_MATERIALIZE_CAP = 40000

# The flows query targets memory-write sink calls instead of every call argument in the repo
# (#219). `cpg.call.argument` grows with the whole CPG, so on a large repo the per-candidate
# dataflow to all of them exceeds the query deadline and NO slice is emitted. Restricting the
# sink universe to the dominant GT sink types (writers + command/SQL-exec sinks + write/pointer
# operators -- the same calls Case B enumerates) keeps the dataflow bounded. The argument set is
# capped again after the union so one huge repo cannot reinflate it. ⚠ This narrows recall:
# sinks past the cap, and the local-array/double-free categories Case B also flags, are not
# targeted as flow sinks (candidate enumeration still captures them). The before/after miss-rate
# is measured on ATOM.
FLOWS_SINK_CAP = 2000


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


# Phase timing for the per-candidate stages (slicer performance investigation). `mark(k)` adds
# the time since the previous mark to phase k, so marks sit only BETWEEN strict statements
# (built with `.l`/`.toList`) -- a lazy traversal forced later would land in the wrong phase.
# `within(k)` is inclusive (overlaps the marks) and is named `*_inclusive` for that reason.
# The client unwraps `value` and writes the rest to the progress log only (`Slicer.query`):
# timings differ run to run, so they must never reach the checkpoint or `slices`.
TIMING = """
val __timings = scala.collection.mutable.LinkedHashMap.empty[String, Long]
val __counts = scala.collection.mutable.LinkedHashMap.empty[String, Long]
val __start = System.nanoTime
var __last = __start
def mark(k: String): Unit = {
  val now = System.nanoTime
  __timings(k) = __timings.getOrElse(k, 0L) + (now - __last)
  __last = now
}
def count(k: String, n: Long = 1): Unit = __counts(k) = __counts.getOrElse(k, 0L) + n
def within[A](k: String)(f: => A): A = {
  val s = System.nanoTime
  val r = f
  __timings(k) = __timings.getOrElse(k, 0L) + (System.nanoTime - s)
  r
}
"""
TIMED_RESULT = (
    "ujson.Obj(\"value\" -> result, "
    "\"timings_ms\" -> ujson.Obj.from(__timings.map { case (k, v) => k -> ujson.Num(v / 1e6) }), "
    "\"counts\" -> ujson.Obj.from(__counts.map { case (k, v) => k -> ujson.Num(v.toDouble) }), "
    "\"body_ms\" -> ujson.Num((System.nanoTime - __start) / 1e6))"
)
TIMED_KEYS = frozenset({"value", "timings_ms", "counts", "body_ms"})


def statements(body: str, *, timed: bool = False) -> str:
    """The envelope's Scala statements up to `result`; slicer_library reuses them verbatim."""
    return (
        "import io.shiftleft.semanticcpg.language._; "
        "import io.joern.dataflowengineoss.language._; "
        "import io.shiftleft.codepropertygraph.generated.nodes; "
        + (TIMING if timed else "")
        + f"val result = {{ {body} }}; "
    )


def envelope(stage: str, body: str, *, timed: bool = False) -> str:
    return (
        "{ "
        + statements(body, timed=timed)
        + f'"__SLICER_{stage}__" + java.util.Base64.getEncoder.encodeToString('
        + f"ujson.write({TIMED_RESULT if timed else 'result'})"
        + ".getBytes(java.nio.charset.StandardCharsets.UTF_8)) + "
        '"__END_SLICER__" }'
    )


# Client-substituted placeholder for the out-of-band payload path. The client (JoernClient)
# replaces this with a JSON string literal of a real, writable path before sending.
OUT_PATH_TOKEN = "__SLICER_OUT_PATH__"

# Stages whose query is built with `file_envelope` (payload via file, not the REPL return).
# The client keys file transport on this, not on the token's presence in the query text --
# a repo file or symbol literally named like the token would otherwise be a false positive.
# Must list exactly the stages that call `file_envelope`.
# inventory too (#404): its `files` list is every source file in the CPG, which on a large repo
# (2026-10-02 vast, wazuh · hdf5) overran the REPL the same way and came back empty, so the
# whole prepare failed before any candidate was discovered.
FILE_TRANSPORT_STAGES = frozenset({"case_a", "case_b", "inventory"})


def file_envelope(stage: str, body: str) -> str:
    """Same as `envelope`, but the payload travels through a FILE, not the REPL return.

    The candidate-enumeration payload (case_a/case_b) can reach several thousand rows on
    large repos, and so can inventory's file list (#404). Returned inline as one base64
    string, that value overruns what Joern's REPL renders as a single result, which comes
    back empty -- the envelope markers are present but the payload between them is gone,
    so the stage aborts with 0 slices
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


def import_query(
    repo: str,
    project: str,
    compilation_database: str | None = None,
    include_root: bool = False,
) -> str:
    # With a compilation database, c2cpg processes only the compiled translation units with
    # their real flags (compile-aware import): dead #ifdef branches and uncompiled files drop
    # out. Without one, every source file is imported as before.
    # `include_root` (--import-include-root) also hands c2cpg the repo root as a header include
    # path. c2cpg 4.0.605 (`Main.scala`): `--include <path>` is `opt[String]("include")
    # .unbounded()`, "header include paths"; `ParserConfig` makes each `Paths.get(_)
    # .toAbsolutePath`, so `repo` must already be absolute. Without any include path c2cpg does not
    # follow project headers reached through `<...>` or from another directory, so code behind a
    # header-config guard, and header-only C++ libraries, stay out of the graph. NOT `--define`:
    # E3 probe (2026-10-03, Joern 4.0.605; notes outside the repo): one dummy `--define` alone cut
    # the internal methods by 37.7% (curl), 62.8% (libxml2) and 21.5% (vim) -- read as c2cpg
    # no longer parsing inactive branches; the effect is measured, that cause is the reading of it.
    arguments = []
    if compilation_database is not None:
        arguments += ["--compilation-database", compilation_database]
    if include_root:
        arguments += ["--include", repo]
    extra = ", args=List(" + ", ".join(literal(a) for a in arguments) + ")" if arguments else ""
    return envelope(
        "import",
        f"""
      val imported = importCode.c(inputPath={literal(repo)}, projectName={literal(project)}{extra})
      require(imported.metaData.nonEmpty, "Imported CPG has no metadata")
      ujson.Obj("imported" -> true)
    """,
    )


def import_cpg_query(cpg: str, project: str) -> str:
    """Load a CPG saved by a `--prepare-only` run instead of parsing the repo again (#368).
    Same stage and payload as `import_query`, so the rest of the run cannot tell them apart."""
    return envelope(
        "import",
        f"""
      val imported = importCpg({literal(cpg)}, {literal(project)})
      require(imported.exists(_.metaData.nonEmpty), "Loaded CPG has no metadata")
      ujson.Obj("imported" -> true)
    """,
    )


def save_cpg_query(out: str) -> str:
    """Persist the active project's graph (overlays included) and copy it to `out` (#368).
    No REPLACE_EXISTING: an existing `out` may be the graph running shards are reading, so the
    copy fails (FileAlreadyExistsException) rather than overwrite it."""
    return envelope(
        "save_cpg",
        f"""
      save
      val source = workspace.getActiveProject.get.path.resolve("cpg.bin")
      val target = java.nio.file.Path.of({literal(out)})
      java.nio.file.Files.copy(source, target)
      ujson.Obj("saved" -> true, "bytes" -> java.nio.file.Files.size(target))
    """,
    )


def inventory_query(limits: dict[str, int] | None = None) -> str:
    return file_envelope(
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
# and the whole slice aborted (0 slices). Measured at the earlier cap 2,000 (before #300):
# capping turned sample_01's abort into 2,198 candidates (2,000 api + 198 readers on THAT
# sample). At cap 4,000 sample_01's api set -- which hit the cap -- can reach up to 4,000, so this
# 2,198 is a cap-2,000 figure, not the current cap's. Both `api` and `readers` are capped:
# readers is not guaranteed small (it is every internal method calling read/getc/getline/mmap/…),
# so an uncapped readers set could overrun the same way on a repo with many such methods.
# A repo past the cap gets bounded coverage, not a failed slice. NOTE: `.take` keeps the first N
# in CPG traversal order, which is not a documented/stable order, so a capped run's coverage is
# an *unspecified* first-N subset (see known_limitations / measurement_notes, and D77 §4.5).
# Since D90 (#318) `api` is sorted write-context-first before its `.take`, so the kept set is
# "hot methods first", but within each layer (hot, then the rest) the cut is still that
# unspecified traversal order. `readers` is not sorted.
# With `--priority-case-a` a listed layer goes in front of "hot" (listed, hot, the rest), so a hot
# method survives the cap only while |listed| + |hot| fits in it -- the D90 sentence quoted in the
# query below ("If |hot ∩ api| <= CAP ...") is the flag-off rule. It stays as is: it is query text,
# and rewording it would change the flag-off text the measurements ran (README, Case A).
# The stdout-overrun above is the pre-#250 mechanism; #250 moved the payload to a file, so the
# cap now bounds the candidate count (and downstream per-candidate work), not the transport.
# (The K=1 filter uses `.take(LIMIT+1).size` so a common name like `read` short-circuits at
#  LIMIT+1 calls instead of enumerating its whole call set — semantically == `.size <= LIMIT`.)
# ⏵ 2026-09-26 — 2,000 → 4,000. 2026-09-26 injection 후보포착(level-2) 재측정에서 injection
# sample_13 source `ra_svn_open`(subversion, READER 직접 호출)이 2,000 캡에선 Case A 후보로 안
# 잡히다가 4,000에서 잡혔다 — **source 회수(+4)는 확정(level-2)**이다. 단 `.take`가 불안정 CPG
# traversal 순서라 이 회수는 readers 총수 < 4,000일 때만 보장된다(위 「unspecified first-N」).
# **미측정으로 남은 것은 full-slice(level-3) 비용뿐**이다 — 늘어난 후보의 per-candidate flows_a
# (Joern/Python 슬라이서 단계라 하네스 모델 장부의 gpu_seconds가 아니라 query deadline·wall 시간·
# candidate timeout으로 잰다)에 별도 상한이 없어, 후보가 2배가 되면 애초에 이 캡이 막으려던
# 0-slices 실패로 되돌 수 있는지는 level-3 실측이 필요하다(#250 뒤 캡은 그 비용을 묶는다 — 위).
# 그 비용이 감당 안 되면 되돌린다.
CASE_A_CANDIDATE_CAP = 4000


def candidates_a_query(priority_files: Collection[str] | None = None, repo: str = "") -> str:
    """Case A discovery. `priority_files` is `--priority-case-a`: repo-relative files whose methods
    rank ahead of the rest before CASE_A_CANDIDATE_CAP and skip CASE_A_CALL_LIMIT. None or empty
    gives the default text, byte for byte. `repo` is the root `context_query` also strips from
    CPG file names, so a name c2cpg wrote absolute still matches the repo-relative list."""
    # The three places the flag touches. By default each stays what it always was, so the text
    # without a list is the text before the flag existed (tests/test_slicer.py pins the line diff).
    definitions, rank = "", "if (hotIds.contains(m.id)) 0 else 1"
    admit = (
        f"cpg.call.nameExact(m.name).take({CASE_A_CALL_LIMIT} + 1).size "
        f"<= {CASE_A_CALL_LIMIT})"
    )
    if priority_files:
        if not repo:
            raise ValueError("priority_files needs the repo root to normalize CPG file names")
        # Joined at run time like context_query's points: a long list (every file of a big repo)
        # must not become one oversized string constant.
        listed = joined_literal("\n".join(sorted(set(priority_files))))
        prefix = literal(repo.rstrip("/") + "/")
        definitions = f"""
      // --priority-case-a: a method defined in a listed file -- an exact match on the
      // repo-relative name, as prioritize() matches candidates -- is ranked ahead of the "hot"
      // layer before the cap and is exempt from the call-count limit below. It still needs a
      // body and a parameter. The list is a sorted set so the text does not depend on set order.
      val prioFiles = {listed}.split({literal(chr(10))}).toSet
      def isPrio(m: nodes.Method): Boolean = prioFiles.contains(
        m.filename.replace('\\\\', '/').stripPrefix({prefix}).stripPrefix("./"))"""
        admit = "isPrio(m) || " + admit
        rank = "if (isPrio(m)) 0 else if (hotIds.contains(m.id)) 1 else 2"
    return file_envelope(
        "case_a",
        LOCATION
        + f"""
      // D90 (#318): write-context-first ordering before the flat cap. Methods that WRITE memory
      // ("hot": call a WRITER/EXEC sink, OR assign into an index/field/deref target -- the same
      // write-context as case_b's indexWrites/derefWrites) are kept ahead of the rest. Plain
      // field reads are excluded -- too common to discriminate. This is a REORDER, not a
      // starvation fix in general: same cap, same count. If |hot ∩ api| <= CAP every hot method
      // survives the cap; if it exceeds CAP the cut falls inside the hot layer, again in CPG
      // traversal order, and every non-hot method is dropped. hotIds is computed once (Set), so
      // the sort key is an O(1) lookup. Only Long ids are materialized (no node lists), so the
      // uncapped `.l` here costs time, not the memory the case_b MCAP guards; a call whose
      // enclosing method is null (malformed/partial CPG) is skipped, as in case_b's budget().
      val hotIds = (cpg.call.name("({WRITERS}|{EXEC_SINKS})")
        .filter(c => Option(c.method).isDefined).method.id.l ++
        cpg.call.nameExact("<operator>.assignment").filter(_.argument.argumentIndex(1).isCall
          .name("<operator>.(indirectIndexAccess|indirectFieldAccess|fieldAccess|indirection)")
          .nonEmpty).filter(c => Option(c.method).isDefined).method.id.l).toSet{definitions}
      // An empty-named method (c2cpg parsing a header without build info, e.g. libexpat
      // xmlrole.h -- full_name `<unresolvedNamespace>.`) passes a `<`-prefix filter, and ONE
      // such row failed `valid_candidate` and voided the whole run (#347; since #361 the Python
      // side drops and counts such a row as `candidates_invalid` instead). Both lists drop them
      // here, silently -- the drop is listed in LIMITATIONS, not counted.
      // A method whose body block holds no statement -- a prototype (a header or forward
      // declaration reaches the CPG as an internal METHOD) or a definition with an empty body --
      // cannot yield a flow, yet it took a cap slot: 3,906 of 21,478 Case A candidates (18%) on
      // the 10 repos of the C2 measurement (2026-10-04, Joern 4.0.605; report outside the repo).
      // Both lists skip it; for readers that is a no-op (a method that calls a reader has a
      // body), kept so the rule is one sentence: Case A never takes a method without a body.
      val api = cpg.method.internal.filterNot(m => m.name.isEmpty || m.name.startsWith("<"))
        .filter(_.astChildren.isBlock.astChildren.nonEmpty)
        .filter(m =>
          {admit}
        .filter(_.parameter.size > 0).l
        .sortBy(m => {rank})
        .take({CASE_A_CANDIDATE_CAP})
      val readers = cpg.method.internal.filter(_.name.nonEmpty)
        .filter(_.call.name("({READERS})").nonEmpty)
        .filter(_.astChildren.isBlock.astChildren.nonEmpty)
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
      // Only methods that actually call free can be free-wrappers, so filter to those first
      // (#219). Scanning the rest walks every method's AST and builds a dataflow engine per
      // parameter for nothing -- on a large C++ CPG that per-method cost is what pushes case_b
      // past the query deadline (sample_40). The filter is exact: a method with no free() call
      // cannot reach one, so no candidate is lost.
      val dealloc = cpg.method.internal.filter(_.ast.isCall.nameExact("free").nonEmpty).l
        .flatMap(m => m.parameter.l.filter(p =>
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
      // Per-method budget (CASE_B_PER_METHOD): round-robin at most k anchors per enclosing
      // method BEFORE the global cap, so a GT function is never starved by the flat traversal
      // take (see the CASE_B_PER_METHOD note in slicer_queries). ROUND-ROBIN, not per-method
      // depth-first: take the 1st anchor of EVERY method, then the 2nd of every method, ... up
      // to k rounds, then the ceiling. So under the ceiling the FIRST thing spent is one anchor
      // per method (max method coverage -> a GT method needs only to be among the first `ceil`
      // methods by id, not to win a global first-N race). Deterministic: methods sorted by id,
      // each method's anchors keep CPG order.
      def budget(cs: List[nodes.Call], k: Int, ceil: Int): List[nodes.Call] = {
        // Guard a null enclosing method (a call not attached to a method in a malformed/partial
        // CPG) so `.method.id` cannot NPE and drop the whole case_b query (#311 review).
        val groups = cs.filter(c => Option(c.method).isDefined)
          .groupBy(_.method.id).toList.sortBy(_._1).map(_._2.take(k))
        (0 until k).flatMap(i => groups.flatMap(g => g.lift(i))).take(ceil).toList
      }
      // .take(MCAP) bounds materialization BEFORE .l (#311 review): budget needs the list to group
      // by method, so unlike the old flat .take(cap) it would otherwise materialize the whole set.
      val writers = budget(cpg.call.name("(__WRITERS__)").take(__MCAP__).l, __KPM__, __WCAP__)
      val execs = budget(cpg.call.name("(__EXECS__)").take(__MCAP__).l, __KPM__, __ECAP__)
      // Operator anchors, narrowed before budgeting so the budget bounds a real subset (#219).
      val indexWrites = budget(cpg.call.nameExact("<operator>.indirectIndexAccess")
        .filter(c => c.inCall.nameExact("<operator>.assignment").argument
          .argumentIndex(1).contains(c)).take(__MCAP__).l, __KPM__, __OPCAP__)
      // Deref/field WRITES (`p->x = ...`, `*p = ...`, `s.f = ...`): same assignment-LHS narrowing
      // as indexWrites -- biggest GT sink bucket, raw explodes, so restrict to WRITE targets
      // (keeps the OOB-write case, drops far-more-common reads). Routed through budget() like the
      // other operator anchors so per-method budgeting bounds it too (#308 x #311 reconciliation).
      val derefWrites = budget(
        cpg.call.name("<operator>.(indirectFieldAccess|fieldAccess|indirection)")
        .filter(c => c.inCall.nameExact("<operator>.assignment").argument
          .argumentIndex(1).contains(c)).take(__MCAP__).l, __KPM__, __OPCAP__)
      val ptrArith = budget(cpg.call.name("<operator>.(addition|subtraction)")
        .filter(c => c.argument.evalType.exists(_.contains("*")) ||
          c.argument.isCall.name("<operator>.(indirectFieldAccess|fieldAccess|" +
            "indirection|indirectIndexAccess|addressOf)").nonEmpty)
        .take(__MCAP__).l, __KPM__, __OPCAP__)
      val operators = (indexWrites ++ derefWrites ++ ptrArith).distinct
      ujson.Arr.from((arrays ++ repeatedCalls ++ writers ++ execs ++ operators).distinct.map(c => {
        val anchors = (List(c) ++ repeated.filter(_.contains(c)).flatten).distinct
        ujson.Obj("symbol" -> c.name, "file" -> c.location.filename,
          "line" -> c.lineNumber.getOrElse(-1), "scope" -> scope(c.method),
          "selector" -> callSelector(c),
          "reasons" -> ujson.Arr.from(List(
            Option.when(arrays.contains(c))("local_array_argument"),
            Option.when(repeatedCalls.contains(c))("repeated_release"),
            Option.when(writers.contains(c))("dangerous_write"),
            Option.when(execs.contains(c))("injection_sink"),
            Option.when(indexWrites.contains(c))("index_write"),
            Option.when(derefWrites.contains(c))("deref_write"),
            Option.when(ptrArith.contains(c))("pointer_arithmetic")).flatten),
          "trigger_arguments" -> ujson.Arr.from(c.argument.l.filter(a =>
            a.start.isIdentifier.refsTo.collect { case l: nodes.Local => l }
              .exists(_.typeFullName.contains("[")) ||
            (repeatedCalls.contains(c) && positions.getOrElse(c.name, Nil)
              .contains(a.argumentIndex)) ||
            writers.contains(c) || execs.contains(c) || operators.contains(c)).map(argumentInfo)),
          "sink_arguments" -> ujson.Arr.from(c.argument.l.map(argumentInfo)),
          "anchors" -> ujson.Arr.from(anchors.map(loc)))
      }))
    """
    )
    body = (
        body.replace("__WRITERS__", WRITERS)
        .replace("__EXECS__", EXEC_SINKS)
        .replace("__WCAP__", str(CASE_B_WRITER_CAP))
        .replace("__ECAP__", str(CASE_B_EXEC_CAP))
        .replace("__OPCAP__", str(CASE_B_OPERATOR_CAP))
        .replace("__KPM__", str(CASE_B_PER_METHOD))
        .replace("__MCAP__", str(CASE_B_MATERIALIZE_CAP))
    )
    return file_envelope("case_b", body)


def method_selector(scopes: list[dict] | str) -> str:
    """Select the candidate's methods. A str instead names a Scala `List[(file, fullName, line)]`
    value (slicer_library's parameter), so both forms share the one predicate below."""

    def match(file: str, full_name: str, line: object) -> str:
        return (f"(m.filename == {file} && m.fullName == {full_name} && "
                f"m.lineNumber.getOrElse(-1) == {line})")

    if isinstance(scopes, str):
        predicate = f"{scopes}.exists {{ case (f, fn, l) => {match('f', 'fn', 'l')} }}"
    else:
        predicate = " || ".join(
            match(literal(s["file"]), literal(s["full_name"]), s["line"]) for s in scopes
        )
    return f"cpg.method.internal.filter(m => {predicate}).l"


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
      mark("methods")
      val srcs = methods.sortBy(m => (m.filename, m.lineNumber.getOrElse(-1), m.fullName))
        .flatMap(_.parameter.l.sortBy(p => (p.index, p.code, p.typeFullName)))
      mark("sources")
      def nodeKey(n: nodes.AstNode): String = ujson.write(ujson.Arr(
        n.location.filename, n.lineNumber.getOrElse(-1), n.columnNumber.getOrElse(-1), n.code))
      // Recall-preserving hybrid (#252 review HIGH-2/3). Inside the candidate methods EVERY
      // call argument is a sink -- cheap (few methods) and it keeps the deref/field and
      // index-READ GT sink types that a memory-write-only set drops, and keeps the live-probe
      // fixtures' calls anchored. Globally, narrow to memory-write and command/SQL-exec calls
      // (capped) so the per-candidate cross-repo dataflow stays under the deadline (#219). Cheap
      // name/AST filters only (no dataflow) -- keep the writers/execs/operators NAME+FILTER
      // definitions in sync with candidates_b_query. The collection WRAPPER intentionally differs:
      // flows uses flat `.take(cap)` here, candidates_b uses per-method round-robin budget() (#311)
      // -- do not "sync" that away (test_flows_and_case_b_share pins only the shared name/filter).
      val localSinks = methods.flatMap(m => m.ast.isCall.argument.l)
      mark("local_sinks")
      val writerSinks = cpg.call.name("({WRITERS})").take({CASE_B_WRITER_CAP}).l
      val execSinks = cpg.call.name("({EXEC_SINKS})").take({CASE_B_EXEC_CAP}).l
      val indexWrites = cpg.call.nameExact("<operator>.indirectIndexAccess")
        .filter(c => c.inCall.nameExact("<operator>.assignment").argument
          .argumentIndex(1).contains(c)).take({CASE_B_OPERATOR_CAP}).l
      val derefWrites = cpg.call.name("<operator>.(indirectFieldAccess|fieldAccess|indirection)")
        .filter(c => c.inCall.nameExact("<operator>.assignment").argument
          .argumentIndex(1).contains(c)).take({CASE_B_OPERATOR_CAP}).l
      val ptrArith = cpg.call.name("<operator>.(addition|subtraction)")
        .filter(c => c.argument.evalType.exists(_.contains("*")) ||
          c.argument.isCall.name("<operator>.(indirectFieldAccess|fieldAccess|" +
            "indirection|indirectIndexAccess|addressOf)").nonEmpty).take({CASE_B_OPERATOR_CAP}).l
      val globalArgs = (writerSinks ++ execSinks ++ indexWrites ++ derefWrites ++ ptrArith).distinct
        .flatMap(_.argument).filter(n => Option(n.method).nonEmpty).distinct
      val sinksCapped = globalArgs.size > {FLOWS_SINK_CAP}
      val globalKept = globalArgs.sortBy(n => (
        nodeKey(n), n.argumentIndex, n.method.fullName)).take({FLOWS_SINK_CAP})
      mark("global_sinks")
      val sinks = (localSinks ++ globalKept)
        .filter(n => Option(n.method).nonEmpty).distinct
        .sortBy(n => (nodeKey(n), n.argumentIndex, n.method.fullName))
      mark("sink_sort")
      // Each invocation builds an engine. Batch by enclosing method instead of
      // rebuilding it for every argument; the cap stops subsequent method batches.
      val batches = sinks.groupBy(_.method).toList.sortBy {{ case (m, _) =>
        (m.filename, m.lineNumber.getOrElse(-1), m.fullName)
      }}.map(_._2)
      count("batches", batches.size)
      mark("batches")
      // distinctBy path (#252 review): the broadened local sink set can yield several flows
      // with an identical location sequence (same evidence); deduping by serialized path both
      // removes the redundant flow and makes the order deterministic (a sort tie between two
      // identical paths was leaving `analysis.lines` order run-dependent -> non-reproducible).
      val observed = (if (srcs.isEmpty) Iterator.empty else batches.iterator).flatMap(batch =>
        batch.iterator.reachableByFlows(srcs)(flowContext).toList
          .distinctBy(p => ujson.write(ujson.Arr.from(p.elements.map(nodeKey))))
          .sortBy(p => ujson.write(ujson.Arr.from(p.elements.map(nodeKey))))){limit}.toList
      mark("dataflow")
      val kept = {retained}
      // Canonical line set (#252 review): the dense local sink set makes reachableByFlows
      // return path variants whose element ORDER differs across fresh CPGs, so the raw
      // flatMap dump was run-dependent. The consumer only uses these as a point SET, so emit
      // a deduped, sorted set -- same points, reproducible bytes. (Slice points already matched.)
      val keptLines = kept.flatMap(_.elements).distinctBy(n => nodeKey(n))
        .sortBy(n => nodeKey(n)).map(loc)
      mark("lines")
      // Where each kept flow starts and ends: the head is the candidate-method parameter it was
      // traced from (a `srcs` member), the last element the sink argument it reached. Recorded
      // so a slice can be compared with a GT source/sink without re-deriving it from `lines`.
      // Pairs of kept flows only -- a truncated run's endpoints are those of the kept prefix.
      // Optional extra (#433 review): computed inside Try so a runtime failure here leaves the
      // flows result intact -- the payload then carries `endpoints_error` instead of
      // `endpoints` and the slice records its source/sink as unavailable, not as zero.
      // Try catches NonFatal at RUN time only; a compile error still fails the whole query.
      val keptEndpoints = scala.util.Try {{
        kept.filter(_.elements.nonEmpty)
          .map(p => (p.elements.head, p.elements.last))
          .distinctBy {{ case (s, t) => (nodeKey(s), nodeKey(t)) }}
          .sortBy {{ case (s, t) => (nodeKey(s), nodeKey(t)) }}
          .map {{ case (s, t) => ujson.Obj("source" -> loc(s), "sink" -> loc(t)) }}
      }}
      val flowsResult = ujson.Obj("flow_count" -> kept.size, "truncated" -> {truncated},
        "sinks_capped" -> sinksCapped, "sinks_kept" -> sinks.size,
        "lines" -> ujson.Arr.from(keptLines))
      keptEndpoints match {{
        case scala.util.Success(pairs) => flowsResult.value("endpoints") = ujson.Arr.from(pairs)
        case scala.util.Failure(err) =>
          flowsResult.value("endpoints_error") = ujson.Str(String.valueOf(err))
      }}
      flowsResult
    """,
        timed=True,
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
      // A method is relevant when some point of its file lies in [lineNumber, lineNumberEnd].
      // Points are grouped by file once and each method's filename is built once; the smallest
      // point line >= the method's start decides (no start -> out, no end -> open-ended).
      val pointLines: Map[String, Array[Int]] = points.groupBy(_._1).map {{ case (f, ps) =>
        f -> ps.toArray.map(_._2).sorted }}
      val relevantMethods = cpg.method.internal.filter(m => m.lineNumber.exists {{ lineStart =>
        val start: Int = lineStart
        pointLines.get(filename(m)).exists {{ lines =>
          val found = java.util.Arrays.binarySearch(lines, start)
          val first = if (found >= 0) found else -found - 1
          first < lines.length && m.lineNumberEnd.forall(_ >= lines(first))
        }}
      }}).l
      mark("relevant_methods")
      val all = relevantMethods.flatMap(_.ast.l).filter(sourceNode).distinct
      mark("method_ast")
      val selected = scala.collection.mutable.Set.empty[nodes.AstNode]
      selected ++= all.filter(n => points.contains((filename(n),
        n.lineNumber.getOrElse(-1))))
      mark("initial_select")
      var changed = true
      while (changed) {{
        val before = selected.size
        count("iterations")
        val parents = selected.toList.flatMap(ancestors)
        // `parents` repeats a method/control once per selected descendant, and each copy below
        // walks its whole AST again (zlib sample: 58% of context time). The walks only add to
        // the `selected` set, so distinct changes the cost, not the result.
        val methods = (selected.toList ++ parents).collect {{ case m: nodes.Method => m }}.distinct
        val controls = parents.collect {{ case c: nodes.ControlStructure => c }}.distinct
        selected ++= parents
        mark("ancestors")
        selected ++= controls.flatMap(_.condition.ast.l)
        mark("conditions")
        // Labels/jumps are siblings in the AST, not ancestors of data-flow nodes.
        // Retain their guards on the next fixed-point pass as well.
        selected ++= controls.flatMap(_.ast.l).filter(n =>
          n.isInstanceOf[nodes.JumpTarget] || (n match {{
            case c: nodes.ControlStructure =>
              Set("BREAK", "CONTINUE", "GOTO").contains(c.controlStructureType)
            case _ => false
          }}))
        mark("control_jumps")
        selected ++= methods.flatMap(_.ast.l).filter(n =>
          n.isInstanceOf[nodes.JumpTarget] || (n match {{
            case c: nodes.ControlStructure => c.controlStructureType == "GOTO"
            case _ => false
          }}))
        mark("method_jumps")
        selected ++= methods.flatMap(_.ast.isReturn.l)
        mark("returns")
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
        mark("declarations")
        // 퍼지 파싱(특히 C++)이 인자 없는 assignment 노드를 남기면 `a.target`(= argument(1))이
        // 빈 iterator에서 next로 죽는다(#219 후속, sample_40 context 단계 NoSuchElement).
        // target이 실재하는 assignment만 본다 — 인자 없는 노드는 선언을 참조할 수도 없다.
        selected ++= cpg.assignment.filter(a => a.argument.argumentIndex(1).nonEmpty &&
          a.target.ast.isIdentifier.refsTo.exists(d => declarationIds.contains(d.id))).l
        mark("assignments")
        changed = selected.size != before
      }}
      ujson.Arr.from(selected.toList.map(loc))
    """,
        timed=True,
    )
