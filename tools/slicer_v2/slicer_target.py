#!/usr/bin/env python3
"""Slice from a named sink and source (targeted mode; Python 3.11+, standard library only).

`slicer.py` discovers its candidates itself. This module skips discovery and starts at the places
a spec file names. It is the part of the PR #638 `slicer_v2` that `tools/slicer` does not have; it
imports the current `tools/slicer` modules instead of carrying copies of them.

- **backward** -- from the sink node along the data dependency (DDG) toward where the data comes
  from. A branch that reaches the source node stops there; a branch that goes to another input (a
  length variable, say) stays. Without a source nothing stops a branch early. Function boundaries
  are crossed with a call-site stack (parameter -> the caller's argument, call -> the callee's
  return), the rule of `slicer_backward`, plus the extensions in `BACKWARD_STEP` (operator
  operands, recursion boundary, ascent fan-out). `case: "B"`.
- **forward** -- the mirror, from the source node along where the data is used, stopping at the
  sink. When the sink is reached only the nodes on the way to it are kept (`pruned_to_target`);
  when it is not, only the source function's own nodes (`pruned_to_start_method`). Without a
  source there is no forward slice. `case: "F"`; the harness loader reads it as a slice whose
  given end is the source (`src/adapters/slices.py`).

Context completion and source recovery use the discovery mode's functions (`context_query` fixed
point, `recover_points`, `assemble`).

What it adds for a consumer that wants to know where an input is decoded:

- `slices[].analysis.input_evidence` -- bounded CPG facts around the slice (`input_evidence.py`).
- `slices[].analysis.input_context` -- with `--index`, the decoder functions found next to the
  slice (`input_context.py`). The object only *names* functions; nothing here renders source.

## Where the start and stop nodes come from

The spec gives (file, line, function, symbol string); nothing else is read -- no ground truth, no
CWE. Only the **start and stop nodes** are chosen from it, and the slice is not corrected toward
any expected answer. The sink and source objects are recorded in `settings.spec`, which the
`run_signature` covers:

1. Line: within spec line +-2, the line that contains the symbol string (whitespace ignored),
   nearest to the spec line. None -> the line holding the most (at least half) of the symbol's
   identifiers; none -> the spec line itself (both `symbol_on_line: false`).
2. Node: the nodes on that line (CALL, IDENTIFIER, LITERAL, METHOD_PARAMETER_IN, RETURN) come from
   Joern and are matched to the symbol -- same code > a call of that name > an identifier or
   parameter of that name > the smallest node containing the symbol > the largest node inside it.
   Equal scores all become start nodes (at most `MAX_TIED_NODES`).

Whether an Auditor Finding's location and symbol string pick the same nodes as a hand-written spec
is **unmeasured**: this module was written against specs, not against Findings.

## Use

```
python slicer_target.py --repo /work/repo --spec spec.json --out slices.json \\
  --joern-endpoint http://127.0.0.1:16242 --joern-version-command /opt/joern/joern-cli/joern \\
  [--index index.json] [--backward-depth 30 --backward-nodes 5000]
```

spec.json: `{"sink": {"file","line","line_end","function","symbol","function_span"},
"source": {...} | null}`. Only `file` and `line` are required; `function_span` ([first, last]) is
the line range of the function the location belongs to, used to retry inside it.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

# The modules this one extends live in the sibling tools/slicer; run as a script, only this
# directory is on the path.
_SLICER_DIR = str(Path(__file__).resolve().parents[1] / "slicer")
if _SLICER_DIR not in sys.path:
    sys.path.insert(0, _SLICER_DIR)

import slicer as base  # noqa: E402
import slicer_queries as queries  # noqa: E402
from input_context import Source, build_input_context  # noqa: E402
from input_evidence import input_evidence_query, valid_input_evidence  # noqa: E402
from slicer_errors import QueryError  # noqa: E402
from slicer_source import assemble, normalize_points, recover_points, relative_file  # noqa: E402

# Directory names that rank a caller last when the ascent from a parameter fans out. The same list
# as src/adapters/index_graph.py (`_NONPRODUCT_DIRS`, `_FUZZ_PREFIXES`), restated because this tool
# does not import the harness; tests/test_slicer_target.py pins the two together. The list is
# chosen, not measured (the harness file says so too).
NONPRODUCT_DIRS = frozenset(
    {
        "test", "tests", "testing", "unittest", "unittests", "testsuite", "regress",
        "example", "examples", "sample", "samples", "demo", "demos", "contrib", "doc", "docs",
        "third_party", "third-party", "thirdparty", "vendor",
        "fuzz", "fuzzer", "fuzzers", "fuzzing", "oss-fuzz",
        "xcode", "windows", "win32", "win64", "msvc",
    }
)  # fmt: skip
FUZZ_PREFIXES = ("fuzz_", "fuzz-")
NONPRODUCT_PATTERN = (
    "(?:^|/)(?:"
    + "|".join(
        [re.escape(d) for d in sorted(NONPRODUCT_DIRS)]
        + [re.escape(p) + "[^/]*" for p in FUZZ_PREFIXES]
    )
    + ")/"
)

# How far from the spec line the symbol string is looked for. The PR #638 author's choice;
# unmeasured.
LINE_TOLERANCE = 2
# Start nodes taken from one line when several match equally (`a[i] = a[i] + 1` repeats an
# identifier); more than this means the match was the whole line, not the symbol. Unmeasured.
MAX_TIED_NODES = 8
# Lines scanned for a retry inside `function_span`, and the share of the symbol's identifiers a
# line must hold to count. Unmeasured.
RETRY_LINES = 3
IDENT_SHARE = 2  # a line must hold at least 1/IDENT_SHARE of the symbol's identifiers
LOCATE_LABELS = ("CALL", "IDENTIFIER", "LITERAL", "METHOD_PARAMETER_IN", "RETURN")
IDENT = re.compile(r"[A-Za-z_]\w*")
INDEX_VERSIONS = ("index-v2", "index-v3", "index-v4")

# The slice `case` each direction is written with. The harness loader (src/adapters/slices.py)
# maps it to the Scout's `given_end`: B (a sink-first slice) -> sink, A and F (a slice that starts
# at a source) -> source.
CASE_OF = {"backward": "B", "forward": "F"}
TARGET_VERSION = "target-v1"
TARGET_LIMITATIONS = [
    "targeted mode: start/stop nodes are matched from a symbol string on a line; a symbol that "
    "matches nothing falls back to the outermost nodes of that line",
    "forward: data entering a call through an argument continues into the call result and, "
    "for an assignment, its target; an internal callee's parameter is followed, but output "
    "arguments of external calls (memcpy dst from src) are not modelled",
    "forward: a call chosen as the source seeds the call and all its arguments, so later uses "
    "of non-tainted arguments are included (over-approximation)",
    "a branch stops where it reaches the other end (source for backward, sink for forward); "
    "other branches run until the dependency ends or the depth/state/call-depth limits",
    "forward: when the sink is reached, only the states that lead to it are kept "
    "(pruned_to_target), plus the visited states in the sink's own method at or after the sink "
    "line (kept_after_target); what the tainted data does in other functions after the sink is "
    "not in the forward slice",
    "the stop node's line is recorded as line_provenance.target_anchor even when the walk does "
    "not reach it (it is not added to the slice lines)",
    "forward: when the sink is not reached (e.g. it depends on the source only through a "
    "control dependence), only the source function's own nodes are kept (pruned_to_start_method)",
    "targeted context keeps only returns before the last slice line of their function, does not "
    "add repo-wide assignments to referenced variables, and adds method-wide goto labels but not "
    "case/default labels of switches the slice does not touch",
    "build configuration: macros that must be defined for the start/stop lines to be compiled "
    "(#ifdef X / #if X / #if defined(X) true branch, #ifndef X #else branch; not names starting "
    "with '_') are passed to c2cpg as --define (target.defines); every other macro stays "
    "undefined, so code under other configurations is still absent",
]

CONDITIONAL = re.compile(r"\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)")
# `#if NAME` / `#if defined(NAME)` / `#if defined NAME` -- only a condition of one name is solved
SINGLE_NAME = re.compile(
    r"\s*(?:defined\s*\(\s*([A-Za-z_]\w*)\s*\)|defined\s+([A-Za-z_]\w*)|([A-Za-z_]\w*))\s*$"
)
WRITE_THROUGH = re.compile(
    r"\*\s*\(?\s*([A-Za-z_]\w*)\s*(?:\+\+|--)?\s*\)?\s*=(?!=)"
    r"|\b([A-Za-z_]\w*)\s*\[[^\]]*\]\s*=(?!=)"
)
# Comments, strings and char literals of one line, blanked before a line is read as code.
_LINE_NOISE = re.compile(r"/\*.*?\*/|//.*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")


def valid_locate(data: object) -> bool:
    """Shape of the `locate` payload: the nodes on one line, each with its method."""
    return isinstance(data, list) and all(
        isinstance(row, dict)
        and isinstance(row.get("label"), str)
        and isinstance(row.get("code"), str)
        and isinstance(row.get("name"), str)
        and type(row.get("line")) is int
        and type(row.get("column")) is int
        and isinstance(row.get("ast_path"), list)
        and all(type(n) is int for n in row["ast_path"])
        and isinstance(row.get("method"), dict)
        and base.valid_location(row["method"])
        and isinstance(row["method"].get("full_name"), str)
        and type(row["method"].get("line_end")) is int
        for row in data
    )


base.STAGE_VALIDATORS.update(
    locate=valid_locate,
    input_evidence=valid_input_evidence,
    cleanup=lambda data: data is True,
)


def activating_defines(lines: list[str], target: int) -> list[str]:
    """Names of the preprocessor conditions around line `target` that must be *defined* for the
    line to be compiled.

    c2cpg treats an undefined macro as undefined and drops the branch, so a line inside `#ifdef X`
    got no node at all. Only the true branch of `#ifdef X` / `#if X` / `#if defined(X)` and the
    `#else` branch of `#ifndef X` are read; a branch that is alive only when the macro is *not*
    defined (`#ifndef X` true, `#ifdef X` false) stays as it is by default. Names that start with
    an underscore (`_WIN32`, `__APPLE__`: compiler and platform macros) are never turned on --
    code for other platforms would all come alive."""
    stack: list[list] = []  # [name | None, must be defined for the line to live]
    joined = ""
    for raw in lines[: max(0, target - 1)]:
        joined = joined + raw
        if raw.rstrip().endswith("\\"):
            joined = joined.rstrip()[:-1]
            continue
        line, joined = joined, ""
        match = CONDITIONAL.match(line)
        if match is None:
            continue
        kind, rest = match.group(1), re.sub(r"/\*.*?\*/|//.*", "", match.group(2))
        if kind in ("ifdef", "ifndef"):
            found = IDENT.match(rest.strip())
            stack.append([found[0] if found else None, kind == "ifdef"])
        elif kind == "if":
            single = SINGLE_NAME.match(rest)
            name = next((g for g in single.groups() if g), None) if single else None
            stack.append([name, True])
        elif kind == "elif":
            if stack:
                stack[-1] = [None, True]
        elif kind == "else":
            if stack and stack[-1][0] is not None:
                stack[-1][1] = not stack[-1][1]
        elif kind == "endif" and stack:
            stack.pop()
    return [name for name, wanted in stack if name and wanted and not name.startswith("_")]


def _code(lines: list[str], number: int) -> str:
    return _LINE_NOISE.sub("''", lines[number - 1]) if 1 <= number <= len(lines) else ""


def pointer_steps(lines: list[str], sink_lines: list[int], low: int, high: int) -> set[int]:
    """Lines in `low..high` of the sink's function that move, or write through, a pointer the sink
    lines write through (`*fn = ...`, `p[i] = ...`): `fn++`, `fn += n`, `--fn`, `*fn++ = '.'`.

    Joern's DDG does not link `fn++` (postIncrement) to the definition of `fn`, so the pointer
    movement that decides the write position was missing from the slice. This is a lexical
    supplement and is recorded apart, as `line_provenance.pointer_steps`."""
    names = {a or b for n in sink_lines for a, b in WRITE_THROUGH.findall(_code(lines, n))}
    found: set[int] = set()
    for name in sorted(names):
        v = re.escape(name)
        step = re.compile(
            rf"\b{v}\s*(?:\+\+|--|[+\-]=(?!=))|(?:\+\+|--)\s*{v}\b"
            rf"|\*\s*\(?\s*{v}\s*(?:\+\+|--)?\s*\)?\s*=(?!=)|\b{v}\s*\[[^\]]*\]\s*=(?!=)"
        )
        found |= {
            n for n in range(max(1, low), min(len(lines), high) + 1) if step.search(_code(lines, n))
        }
    return found


def field_writes(lines: list[str], sink_lines: list[int], low: int, high: int) -> set[int]:
    """Lines in `low..high` (the sink's function, before the sink) that write a field of a variable
    the sink lines pass whole to a call (`LoadGif(gif)` -> `gif->input.bytes = buffer`).

    Joern's DDG does not link a field write to the definition of the object, so the backward walk
    missed what went into it. A lexical supplement, recorded as `line_provenance.field_writes`."""
    names: set[str] = set()
    for n in sink_lines:
        for call in re.finditer(r"\b[A-Za-z_]\w*\s*\(([^()]*)\)", _code(lines, n)):
            for arg in call.group(1).split(","):
                whole = re.fullmatch(r"\s*&?\s*([A-Za-z_]\w*)\s*", arg)
                if whole:
                    names.add(whole.group(1))
    found: set[int] = set()
    for name in sorted(names):
        write = re.compile(rf"\b{re.escape(name)}\s*(?:->|\.)\s*[A-Za-z_][\w.\->\[\]\s]*?=(?!=)")
        found |= {
            n
            for n in range(max(1, low), min(len(lines), high) + 1)
            if n not in sink_lines and write.search(_code(lines, n))
        }
    return found


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "").rstrip(";")


def _distance(number: int, first: int, last: int) -> int:
    return 0 if first <= number <= last else min(abs(number - first), abs(number - last))


def pick_line(sources: dict, spec: dict) -> tuple[int, bool]:
    """The line holding the symbol: within +-LINE_TOLERANCE of the spec's lines, nearest to them.
    Returns (line, found)."""
    lines = sources.get(spec["file"])
    first, last = spec["line"], spec.get("line_end") or spec["line"]
    symbol = squash(spec.get("symbol") or "")
    if not lines or not symbol:
        return first, False
    low, high = max(1, first - LINE_TOLERANCE), min(len(lines), last + LINE_TOLERANCE)
    span = spec.get("function_span")
    if span:
        # The function's own range: a spec line outside it searches the whole range, otherwise
        # the +-tolerance window is cut to it.
        if not span[0] <= first <= span[1]:
            low, high = max(1, span[0]), min(len(lines), span[1])
        else:
            low, high = max(low, span[0]), min(high, span[1])
    found = [
        (_distance(number, first, last), number)
        for number in range(low, high + 1)
        if symbol in squash(lines[number - 1])
    ]
    if not found:
        # No line holds the symbol string verbatim: take the code line holding the most of its
        # identifiers (at least 1/IDENT_SHARE of them). Comment and blank lines have no nodes and
        # used to end in a degraded slice.
        wanted = set(IDENT.findall(spec.get("symbol") or ""))
        scored = []
        for number in range(low, high + 1):
            code = re.sub(r"/\*.*?\*/|//.*", "", lines[number - 1])
            overlap = len(wanted & set(IDENT.findall(code)))
            if wanted and overlap * IDENT_SHARE >= len(wanted):
                scored.append((-overlap, _distance(number, first, last), number))
        if scored:
            return min(scored)[2], False
        return first, False
    return min(found)[1], True


def score(node: dict, symbol: str) -> int:
    """How well a node matches the symbol. 0 = not a candidate."""
    code, name, label = squash(node["code"]), node["name"], node["label"]
    if not symbol:
        return 0
    if code == symbol:
        return 1000
    if IDENT.fullmatch(symbol):
        if label == "CALL" and name == symbol:
            return 900
        if label in ("IDENTIFIER", "METHOD_PARAMETER_IN") and name == symbol:
            return 800
    if symbol in code:
        return 500 - min(len(code), 400)
    if code in symbol and len(code) >= 2 and label != "LITERAL":
        return 100 + min(len(code), 300)
    return 0


def choose(rows: list[dict], spec: dict) -> tuple[list[dict], dict]:
    """Pick the start/stop nodes from the `locate` rows. Returns (rows, note)."""
    note = {"candidates_on_line": len(rows)}
    if not rows:
        return [], {**note, "match": "no_node_on_line"}

    # Several methods can hold one line (C++ lambdas): the spec's function first, then the
    # narrowest.
    def method_rank(row: dict) -> tuple:
        m = row["method"]
        return (m["symbol"] != spec.get("function"), m["line_end"] - m["line"])

    best_method = min(rows, key=method_rank)["method"]
    rows = [r for r in rows if r["method"] == best_method]
    symbol = squash(spec.get("symbol") or "")
    scored = [(score(r, symbol), r) for r in rows]
    top = max(s for s, _ in scored)
    if top > 0:
        picked = [r for s, r in scored if s == top]
        match = "symbol"
    else:
        # No symbol match: the outermost nodes of the line (statement level); the whole line
        # becomes the start.
        depth = min(len(r["ast_path"]) for r in rows)
        picked = [r for r in rows if len(r["ast_path"]) == depth]
        match = "line_outermost"
    picked = sorted(picked, key=lambda r: (r["column"], r["ast_path"]))
    note.update(match=match, score=top, tied=len(picked), method=best_method["full_name"])
    # The start node's function differs from the spec's: that line has no node of the spec's
    # function.
    if spec.get("function") and best_method["symbol"] != spec["function"]:
        note["function_mismatch"] = {"spec": spec["function"], "picked": best_method["symbol"]}
    if len(picked) > MAX_TIED_NODES:
        note["tied_truncated"] = len(picked) - MAX_TIED_NODES
        picked = picked[:MAX_TIED_NODES]
    return picked, note


def in_span(method: dict, span: list[int]) -> bool:
    """The node's method overlaps the spec's function range (up to 3 header lines off, for a
    return type on its own line)."""
    return span[0] - 3 <= method["line"] <= span[1] and method["line_end"] > span[0]


def relocation_lines(
    lines: list[str], spec: dict, span: list[int], skip: int, limit: int = RETRY_LINES
) -> list[int]:
    """Lines inside the function range to retry: lines holding the symbol, operation sites before
    conditions that only test it (so a later added check `if (len > max) return -1;` does not
    become the start), then nearest to the spec line. No such line: the two lines holding the
    most identifiers of the symbol."""
    symbol = squash(spec.get("symbol") or "")
    first = spec["line"]
    low, high = max(1, span[0]), min(len(lines), span[1])
    ranked = []
    for number in range(low, high + 1):
        if number == skip:
            continue
        code = re.sub(r"/\*.*?\*/|//.*", "", lines[number - 1])
        if symbol and symbol in squash(code):
            cond_only = bool(re.match(r"\s*(?:\}\s*)?(?:else\s+)?(?:if|while)\s*\(", code))
            ranked.append((cond_only, abs(number - first), number))
    if ranked:
        return [n for _c, _d, n in sorted(ranked)[:limit]]
    wanted = set(IDENT.findall(spec.get("symbol") or ""))
    scored = []
    for number in range(low, high + 1):
        if number == skip:
            continue
        code = re.sub(r"/\*.*?\*/|//.*", "", lines[number - 1])
        overlap = len(wanted & set(IDENT.findall(code)))
        if wanted and overlap * IDENT_SHARE >= len(wanted):
            scored.append((-overlap, abs(number - first), number))
    return [n for _o, _d, n in sorted(scored)[:2]]


def selector(row: dict) -> dict:
    return {
        "file": row["method"]["file"],
        "method_full_name": row["method"]["full_name"],
        "method_line": row["method"]["line"],
        "label": row["label"],
        "line": row["line"],
        "column": row["column"],
        "code": row["code"],
        "ast_path": row["ast_path"],
    }


PREFIX = r"""
      val repoPrefix = __REPO__
      def fname(n: nodes.AstNode): String = n.location.filename.replace('\\', '/')
        .stripPrefix(repoPrefix).stripPrefix("./")
"""


def locate_query(repo: str, file: str, line: int) -> str:
    labels = ", ".join(queries.literal(label) for label in LOCATE_LABELS)
    body = (
        queries.LOCATION
        + queries.CALL_IDENTITY
        + PREFIX.replace("__REPO__", queries.literal(repo.rstrip("/") + "/"))
        + r"""
      val wanted = __FILE__
      val line = __LINE__
      val labels = Set(__LABELS__)
      val ms = cpg.method.internal.filter(m => fname(m) == wanted &&
        m.lineNumber.exists(_ <= line) && m.lineNumberEnd.exists(_ >= line)).l
      ujson.Arr.from(ms.flatMap(m => m.ast.l.filter(n =>
        n.lineNumber.getOrElse(-1) == line && labels.contains(n.label)).map(n => ujson.Obj(
          "label" -> n.label, "code" -> n.code,
          "name" -> (n match {
            case c: nodes.Call => c.name
            case i: nodes.Identifier => i.name
            case p: nodes.MethodParameterIn => p.name
            case _ => ""
          }),
          "line" -> n.lineNumber.getOrElse(-1), "column" -> n.columnNumber.getOrElse(-1),
          "ast_path" -> ujson.Arr.from(astPath(n)),
          "method" -> ujson.Obj("file" -> fname(m), "line" -> m.lineNumber.getOrElse(-1),
            "line_end" -> m.lineNumberEnd.getOrElse(-1), "full_name" -> m.fullName,
            "symbol" -> m.name)))))
    """.replace("__FILE__", queries.literal(file))
        .replace("__LINE__", str(int(line)))
        .replace("__LABELS__", labels)
    )
    return queries.envelope("locate", body)


# The start/stop nodes are found again from their selector -- the axis of upstream
# `backward_b_query`'s sink selection (method, line, column, code, AST path) plus the label.
# Anything but exactly one node kills the query.
FIND_NODES = r"""
      def findNodes(raw: String): List[nodes.CfgNode] = ujson.read(raw).arr.toList.flatMap { s =>
        val ms = cpg.method.internal.filter(m => m.fullName == s("method_full_name").str &&
          m.lineNumber.getOrElse(-1) == s("method_line").num.toInt &&
          fname(m) == s("file").str).l
        val path = s("ast_path").arr.map(_.num.toInt).toList
        val found = ms.flatMap(_.ast.l).filter(n =>
          n.label == s("label").str && n.lineNumber.getOrElse(-1) == s("line").num.toInt &&
          n.columnNumber.getOrElse(-1) == s("column").num.toInt && n.code == s("code").str &&
          astPath(n) == path).distinct.collect { case c: nodes.CfgNode => c }
        require(found.size == 1, "Target selector is missing or ambiguous: " + ujson.write(s))
        found
      }.distinct
"""

# The traversal text is the PR #638 one, unchanged. "It was run on a live Joern there" is
# unrecorded in this repo -- no run ID, no report, no reproduction procedure -- and there is no
# Joern in this repo's tests, so nothing here re-checks it. The same holds for every case named in
# the comments below (cJSON, jasper, libtiff): they come from that run and are not measured here.
# One text serves both directions; with `__PRUNE__` = false (backward)
# the pruning, `local` and `afterTarget` parts are inert and the result keeps every visited
# state.
TRAVERSAL = r"""
      val starts = findNodes(__STARTS__)
      require(starts.nonEmpty, "No start node")
      val stops = findNodes(__STOPS__)
      mark("select")
      val stopIds: Set[Long] = stops.flatMap(n => n match {
        case a: nodes.AstNode => a.ast.l.map(_.id)
        case _ => Nil
      }).toSet
      case class State(node: nodes.CfgNode, stack: List[nodes.Call], depth: Int, up: Int = 0)
      def key(n: nodes.CfgNode): String = within("key_inclusive") {
        count("key_calls")
        ujson.write(ujson.Arr(
          n.location.filename, n.lineNumber.getOrElse(-1), n.columnNumber.getOrElse(-1),
          n.label, n.code, Option(n.method).map(_.fullName).getOrElse(""),
          ujson.Arr.from(n match { case a: nodes.AstNode => astPath(a); case _ => Nil })))
      }
      def stateKey(s: State): String = key(s.node) + s.stack.map(key).mkString("[", ",", "]")
      // Ascent fan-out ranking: callers in the parameter's own file first, then product paths
      // (index_graph.is_nonproduct_path, injected), then key order.
      val nonProductRe = __NONPRODUCT__.r
      def callerRank(c: nodes.Call, home: String): (Int, Int, String) = {
        val file = Option(c.method).map(_.filename).getOrElse("")
        (if (file == home) 0 else 1,
          if (nonProductRe.findFirstIn(file.toLowerCase).isDefined) 1 else 0, key(c))
      }
      // Methods active on this state's call stack (the current one and every caller pushed) --
      // descending into one of them again is recursion.
      def activeMethodIds(s: State, n: nodes.CfgNode): Set[Long] =
        (Option(n.method).toList ++ s.stack.flatMap(c => Option(c.method))).map(_.id).toSet
      val queue = scala.collection.mutable.Queue.empty[State]
      val seen = scala.collection.mutable.Set.empty[String]
      val nodesSeen = scala.collection.mutable.Map.empty[String, nodes.CfgNode]
      val edges = scala.collection.mutable.Set.empty[(String, String)]
      val cuts = scala.collection.mutable.Set.empty[String]
      val boundaries = scala.collection.mutable.Map.empty[String, ujson.Value]
      val reached = scala.collection.mutable.Map.empty[String, ujson.Value]
      // For pruning: which node each visited state is, which states hit a stop node, and which
      // node each boundary belongs to.
      val stateNode = scala.collection.mutable.Map.empty[String, nodes.CfgNode]
      val reachedStates = scala.collection.mutable.Set.empty[String]
      val boundaryNode = scala.collection.mutable.Map.empty[String, String]
      def boundary(n: nodes.CfgNode, reason: String): Unit = {
        val value = ujson.Obj("reason" -> reason, "location" -> loc(n))
        boundaries.update(ujson.write(value), value)
        boundaryNode.update(ujson.write(value), key(n))
      }
      def offer(s: State): Unit = {
        val k = stateKey(s)
        if (!seen.contains(k)) {
          if (s.depth > __MAX_DEPTH__)
            cuts += "__DIRECTION___depth"
          else if (seen.size >= __MAX_NODES__)
            cuts += "__DIRECTION___states"
          else { seen += k; queue.enqueue(s) }
        }
      }
      __SEED__
      while (queue.nonEmpty) {
        val current = queue.dequeue()
        val n = current.node
        nodesSeen.update(key(n), n)
        stateNode.update(stateKey(current), n)
        if (stopIds.contains(n.id)) {
          reached.update(key(n), loc(n))
          reachedStates += stateKey(current)
          boundary(n, "__REACHED__")
        } else {
          val next = scala.collection.mutable.ListBuffer.empty[State]
          __STEP__
          if (next.isEmpty) boundary(n, "__LEAF__")
          next.toList.sortBy(stateKey).foreach(s => {
            offer(s)
            if (seen.contains(stateKey(s))) edges += ((stateKey(current), stateKey(s)))
          })
        }
      }
      mark("traversal")
      // __PRUNE__: once the other end was reached, keep only the states that lead to it (reverse
      // reachability over the traversal edges) -- a branch that never reaches the target is not on
      // a source->sink path. Not reached -> nothing to prune against, keep everything.
      // The walk back stops at a start node: a path that comes round to the start again (recursion
      // -- cJSON parse_object -> parse_string -> the source assignment) is the direct start->target
      // path already kept, and without the stop every branch of the cycle would count as leading
      // to the target.
      val startIds: Set[Long] = starts.flatMap(n => n match {
        case c: nodes.Call => c.id :: c.argument.l.map(_.id)
        case _ => List(n.id)
      }).toSet
      val pruned = __PRUNE__ && reachedStates.nonEmpty
      // Target not reached (the link is a control dependence -- libexpat copyString: the loop
      // `while (s[charsRequired] != 0)` decides charsRequired; a DDG walk never gets there): keep
      // only the start method's own nodes. Without a target to prune against, the walk otherwise
      // follows the value into every caller and their callers.
      val startMethods: Set[String] = starts.flatMap(n => Option(n.method)).map(_.fullName).toSet
      val local = __PRUNE__ && reachedStates.isEmpty
      val keep: Set[String] = if (local) stateNode.toList.collect {
        case (k, n) if Option(n.method).exists(m => startMethods.contains(m.fullName)) => k
      }.toSet else if (!pruned) stateNode.keySet.toSet else {
        val preds = edges.toList.groupMap(_._2)(_._1)
        val out = scala.collection.mutable.Set.empty[String] ++ reachedStates
        val work = scala.collection.mutable.Queue.empty[String] ++ reachedStates
        while (work.nonEmpty) {
          val s = work.dequeue()
          if (!stateNode.get(s).exists(n => startIds.contains(n.id)))
            preds.getOrElse(s, Nil).foreach(p => if (out.add(p)) work.enqueue(p))
        }
        out.toSet
      }
      // After the target: states the walk already visited in the target's own method at or after
      // the target line. Pruning to source->target paths dropped what the tainted value does right
      // after the sink -- the actual over-read/-write (WavPack `bytecnt -= 2` and the second read,
      // netconsd `msg->ncfrag_off = nf_off`, libmspack `*fn = '\0'`).
      val reachedNodes = reachedStates.toList.flatMap(stateNode.get)
      val afterTarget: Set[String] = if (!pruned) Set.empty[String] else stateNode.toList.collect {
        case (k, n) if reachedNodes.exists(r =>
          Option(r.method).map(_.fullName) == Option(n.method).map(_.fullName) &&
          n.lineNumber.getOrElse(-1) >= r.lineNumber.getOrElse(Int.MaxValue)) => k
      }.toSet
      val kept = (keep ++ afterTarget).toList.flatMap(stateNode.get).map(n => key(n) -> n).toMap
      // Keep the interprocedural edges while the CPG is still open.  The attach stage runs
      // after this project/server has been closed, so it cannot reconstruct these links from
      // the line-only slice.  Restrict the payload to retained states and a bounded number of
      // cross-method edges; intra-method line membership is already in `lines`.
      def flowPoint(n: nodes.CfgNode): ujson.Value = ujson.Obj(
        "file" -> n.location.filename, "line" -> n.lineNumber.getOrElse(-1),
        "method" -> Option(n.method).map(_.fullName).getOrElse(""),
        "label" -> n.label, "code" -> n.code)
      val crossEdges = edges.toList.filter { case (a, b) => keep.contains(a) && keep.contains(b) }
        .flatMap { case (a, b) => (stateNode.get(a), stateNode.get(b)) match {
          case (Some(x), Some(y))
            if x.isInstanceOf[nodes.AstNode] && y.isInstanceOf[nodes.AstNode] &&
              Option(x.method).map(_.id) != Option(y.method).map(_.id) =>
              Some(ujson.Obj("from" -> flowPoint(x), "to" -> flowPoint(y)))
          case _ => None
        }}.groupBy(v => ujson.write(v)).toList.sortBy(_._1).map(_._2.head)
      val crossEdgeLimit = __CROSS_EDGE_LIMIT__
      // Lines that hold only after-target nodes (no node on the source->target path).
      val keepLines = keep.toList.flatMap(stateNode.get)
        .map(n => (n.location.filename, n.lineNumber.getOrElse(-1))).toSet
      val afterOnly = (afterTarget -- keep).toList.flatMap(stateNode.get)
        .filterNot(n => keepLines.contains((n.location.filename, n.lineNumber.getOrElse(-1))))
        .collect { case a: nodes.AstNode if a.lineNumber.exists(_ > 0) => loc(a) }
      val locations = kept.toList.sortBy(_._1).flatMap { case (_, n) => n match {
        case a: nodes.AstNode if a.lineNumber.exists(_ > 0) => List(loc(a))
        case _ => Nil
      } }
      mark("locations")
      ujson.Obj("flow_count" -> ujson.Null, "truncated" -> cuts.nonEmpty,
        "lines" -> ujson.Arr.from(locations), "node_count" -> kept.size,
        "state_count" -> seen.size, "dependency_count" -> edges.size,
        "pruned_to_target" -> pruned, "pruned_to_start_method" -> local,
        "kept_after_target" -> (afterTarget -- keep).size,
        "after_target_lines" -> ujson.Arr.from(afterOnly),
        "nodes_before_prune" -> nodesSeen.size,
        "cross_method_flow" -> ujson.Arr.from(crossEdges.take(crossEdgeLimit)),
        "cross_method_flow_truncated" -> (crossEdges.size > crossEdgeLimit),
        "boundaries" -> ujson.Arr.from(boundaries.toList.sortBy(_._1)
          .filter { case (k, _) => boundaryNode.get(k).exists(kept.contains) }.map(_._2)),
        "truncation_reasons" -> ujson.Arr.from(cuts.toList.sorted),
        "stop_requested" -> stops.nonEmpty,
        "target_reached" -> reached.nonEmpty,
        "reached" -> ujson.Arr.from(reached.toList.sortBy(_._1).map(_._2)))
"""
# Cross-method edges kept in the payload. The PR #638 author's bound; unmeasured. A slice that
# hit it says so in `analysis.cross_method_flow_truncated`.
CROSS_EDGE_LIMIT = 256

# Backward: the extension of upstream `slicer_backward`'s rules.
BACKWARD_SEED = r"""
      starts.sortBy(key).foreach(n => n match {
        case c: nodes.Call if c.argument.nonEmpty =>
          c.argument.l.sortBy(key).foreach(a => offer(State(a, Nil, 0)))
        case _ => offer(State(n, Nil, 0))
      })
"""
BACKWARD_STEP = r"""
          n.start.ddgIn.l.sortBy(key).foreach(p =>
            next += State(p, current.stack, current.depth + 1, current.up))
          n match {
            case p: nodes.MethodParameterIn =>
              // An ascent with no stack goes to callers; with many of them (a logging helper
              // called from everywhere -- libarchive `safe_fprintf`) every caller's argument
              // chain came in.
              // Keep __ASCENT_FANOUT__ (0 = all), ranked by callerRank.
              val callers = if (current.stack.nonEmpty) current.stack.take(1)
                            else {
                              val ranked = p.method.callIn.l
                                .sortBy(c => callerRank(c, p.method.filename))
                              if (__ASCENT_FANOUT__ > 0 && ranked.size > __ASCENT_FANOUT__) {
                                cuts += "__DIRECTION___ascent_fanout"
                                ranked.take(__ASCENT_FANOUT__)
                              } else ranked
                            }
              // An ascent (empty stack: any caller) counts against __MAX_ASCENT__; returning from a
              // call the walk descended into does not.
              val up = current.up + (if (current.stack.isEmpty) 1 else 0)
              if (callers.isEmpty) boundary(p, "entry_parameter")
              else if (up > __MAX_ASCENT__) boundary(p, "ascent_limit")
              else callers.foreach(c => c.argument.l.filter(_.argumentIndex == p.index).foreach(a =>
                next += State(a, current.stack.drop(1), current.depth + 1, up)))
            // An operator call that is not an assignment (`*dstptr2`, `a[i]`, `p->f`, `x + y`): go
            // into its operands -- the operator node itself often has no incoming DDG edge, so the
            // walk stopped at the sink (jasper `*dstptr2 = *srcptr2`: 2 nodes -- from the PR #638
            // live-Joern run, unrecorded: no run ID or reproduction procedure in this repo).
            case c: nodes.Call
                if c.name.startsWith("<operator>") && !c.name.contains("ssignment") =>
              c.argument.l.sortBy(key).foreach(a =>
                next += State(a, current.stack, current.depth + 1, current.up))
            case c: nodes.Call if !c.name.startsWith("<operator>") =>
              val callees = c.callee.filterNot(_.isExternal).l
              val active = activeMethodIds(current, n)
              if (callees.isEmpty) boundary(c, "external_or_unresolved_call")
              else if (current.stack.size >= __MAX_CALL_DEPTH__) cuts += "call_depth"
              else callees.foreach(m =>
                // recursion (review plan S1-C2): the same function again only repeats its own lines
                if (active.contains(m.id)) boundary(c, "recursive_call")
                else m.ast.isReturn.l.foreach(r =>
                  next += State(r, c :: current.stack, current.depth + 1, current.up)))
            case _ => ()
          }
"""

# Forward: the mirror of backward. DDG forward + argument -> its call (result), an assignment's
# target, an internal callee's parameter; return -> the call site (every caller when the stack
# is empty).
FORWARD_SEED = r"""
      starts.sortBy(key).foreach(n => {
        offer(State(n, Nil, 0))
        n match {
          case c: nodes.Call => c.argument.l.sortBy(key).foreach(a => offer(State(a, Nil, 0)))
          case _ => ()
        }
      })
"""
FORWARD_STEP = r"""
          // Forward DDG = outgoing REACHING_DEF edges (schema accessor; ddgIn has no forward twin).
          n._reachingDefOut.collect { case c: nodes.CfgNode => c }.toList.sortBy(key).foreach(p =>
            next += State(p, current.stack, current.depth + 1, current.up))
          // The callee expression of a function-pointer call (`intf->sendrecv` in
          // `rsp = intf->sendrecv(intf, &req)` -- ipmitool) is linked to its call by a RECEIVER
          // edge, not as an argument/AST child the step below sees: the call's value is the
          // walk's next node.
          n._receiverIn.collect { case c: nodes.Call => c }.toList.sortBy(key).foreach(c =>
            next += State(c, current.stack, current.depth + 1, current.up))
          n match {
            case r: nodes.Return =>
              val sites = if (current.stack.nonEmpty) current.stack.take(1)
                          else r.method.callIn.l.sortBy(key)
              if (sites.isEmpty) boundary(r, "exit_return")
              sites.foreach(c =>
                next += State(c, current.stack.drop(1), current.depth + 1, current.up))
            // argumentIndex 0 = the receiver/callee expression of a call (`intf->sendrecv` in
            // `rsp = intf->sendrecv(intf, &req)` -- ipmitool): its value is the call's too.
            case e: nodes.Expression if e.argumentIndex >= 0 =>
              e.astParentOption match {
                case Some(c: nodes.Call) =>
                  next += State(c, current.stack, current.depth + 1, current.up)
                  if (c.name.contains("assignment") && e.argumentIndex != 1)
                    c.argument.l.filter(_.argumentIndex == 1).foreach(t =>
                      next += State(t, current.stack, current.depth + 1, current.up))
                  if (!c.name.startsWith("<operator")) {
                    val callees = c.callee.filterNot(_.isExternal).l
                    if (callees.isEmpty) boundary(c, "external_or_unresolved_call")
                    else if (current.stack.size >= __MAX_CALL_DEPTH__) cuts += "call_depth"
                    else {
                      val active = activeMethodIds(current, n)
                      callees.foreach(m =>
                        if (active.contains(m.id)) boundary(c, "recursive_call")  // S1-C2
                        else m.parameter.l.filter(_.index == e.argumentIndex)
                          .foreach(p =>
                            next += State(p, c :: current.stack, current.depth + 1, current.up)))
                    }
                  }
                case _ => ()
              }
            case _ => ()
          }
"""


def traversal_query(
    direction: str,
    repo: str,
    starts: list[dict],
    stops: list[dict],
    *,
    max_depth: int | None,
    max_nodes: int | None,
    max_call_depth: int,
    max_ascent: int | None = None,
    ascent_fanout: int = 0,
) -> str:
    no_limit = 2147483646
    # Forward prunes to source->sink paths. Backward keeps its other branches: an input that is not
    # the spec's source (a length, an index) still decides whether the sink overflows.
    seed, step, reached, leaf, prune = (
        (BACKWARD_SEED, BACKWARD_STEP, "source_reached", "dependency_root", "false")
        if direction == "backward"
        else (FORWARD_SEED, FORWARD_STEP, "sink_reached", "dependency_leaf", "true")
    )
    body = (
        queries.LOCATION
        + queries.CALL_IDENTITY
        + PREFIX.replace("__REPO__", queries.literal(repo.rstrip("/") + "/"))
        + FIND_NODES
        + TRAVERSAL.replace("__SEED__", seed).replace("__STEP__", step)
    )
    body = (
        body.replace("__STARTS__", queries.joined_literal(json.dumps(starts, ensure_ascii=True)))
        .replace("__STOPS__", queries.joined_literal(json.dumps(stops, ensure_ascii=True)))
        .replace("__MAX_DEPTH__", str(max_depth if max_depth is not None else no_limit))
        .replace("__MAX_NODES__", str(max_nodes if max_nodes is not None else no_limit))
        .replace("__MAX_CALL_DEPTH__", str(int(max_call_depth)))
        .replace("__MAX_ASCENT__", str(max_ascent if max_ascent is not None else no_limit))
        .replace("__ASCENT_FANOUT__", str(int(ascent_fanout or 0)))
        .replace("__CROSS_EDGE_LIMIT__", str(CROSS_EDGE_LIMIT))
        .replace("__NONPRODUCT__", queries.literal(NONPRODUCT_PATTERN))
        .replace("__DIRECTION__", direction)
        .replace("__REACHED__", reached)
        .replace("__LEAF__", leaf)
        .replace("__PRUNE__", prune)
    )
    return queries.envelope(direction, body, timed=True)


class TargetSlicer(base.Slicer):
    """The discovery slicer's source loading, CPG build, inventory and query accounting, with the
    candidate discovery and candidate loop replaced by a backward slice from the spec's sink and,
    when the spec has a source, a forward slice from it."""

    def __init__(
        self, repo, client: base.JoernClient, spec: dict, index: dict | None = None, **kwargs
    ):
        super().__init__(repo, threshold=1.0, max_flows=None, client=client, **kwargs)
        self.spec = spec
        self.index = index
        self.close_project = True
        self.result["known_limitations"] += TARGET_LIMITATIONS
        self.result["target"] = {"version": TARGET_VERSION, "spec": spec}

    def locate(self, role: str) -> tuple[list[dict], dict]:
        spec = self.spec[role]
        line, on_line = pick_line(self.sources, spec)
        note = {"role": role, "spec_line": spec["line"], "line": line, "symbol_on_line": on_line}
        if spec["file"] not in self.sources:
            return [], {**note, "match": "file_not_in_repo"}
        rows, _ = self.query("locate", locate_query(self.repo.as_posix(), spec["file"], line))
        picked, chosen = choose(rows, spec)
        span = spec.get("function_span")
        if span and (not picked or not in_span(picked[0]["method"], span)):
            # Picked outside the spec's function, or no node on the line: retry on the lines of
            # the range that hold the symbol; keep the first pick if none works.
            for retry in relocation_lines(self.sources.get(spec["file"]) or [], spec, span, line):
                rows2, _ = self.query(
                    "locate", locate_query(self.repo.as_posix(), spec["file"], retry)
                )
                picked2, chosen2 = choose(rows2, spec)
                if picked2 and in_span(picked2[0]["method"], span):
                    chosen2["start_relocated"] = {
                        "from": line,
                        "to": retry,
                        "reason": chosen.get("match") if not picked else "outside_spec_function",
                    }
                    picked, chosen, line = picked2, chosen2, retry
                    note["line"] = retry
                    break
        if span:
            chosen["in_spec_function"] = bool(picked) and in_span(picked[0]["method"], span)
        note.update(chosen)
        note["nodes"] = [
            {k: r[k] for k in ("label", "code", "name", "line", "column")} for r in picked
        ]
        return picked, note

    def direction(
        self, direction: str, starts: list[dict], stops: list[dict], start_role: str
    ) -> dict:
        query = traversal_query(
            direction,
            self.repo.as_posix(),
            [selector(r) for r in starts],
            [selector(r) for r in stops],
            max_depth=self.settings.get("backward_depth"),
            max_nodes=self.settings.get("backward_nodes"),
            max_call_depth=self.result["joern_engine_config"]["maxCallDepth"],
            max_ascent=self.settings.get("max_ascent"),
            ascent_fanout=self.settings.get("ascent_fanout") or 0,
        )
        self.active_candidate = direction
        self.candidate_started = time.perf_counter()
        payload, _ = self.query(direction, query)
        # The Joern project may be built from a materialized subset. Persist repository-relative
        # paths so a consumer can use the full repository index.
        flow = []
        for edge in payload.get("cross_method_flow", []):
            sides = []
            for side in ("from", "to"):
                point = dict(edge.get(side) or {})
                point["file"] = relative_file(point.get("file", ""), self.repo)
                if point["file"] not in self.sources or type(point.get("line")) is not int:
                    break
                sides.append(point)
            if len(sides) == 2:
                flow.append({"from": sides[0], "to": sides[1]})
        payload["cross_method_flow"] = flow
        anchors = {(self.spec[start_role]["file"], r["line"]) for r in starts}
        anchors = {p for p in anchors if p[0] in self.sources}
        # The other end's line: known from the start even when the walk never reaches it (a flow
        # through a field). Recorded, not added to the slice lines.
        stop_role = "source" if start_role == "sink" else "sink"
        target_anchor = sorted(
            (self.spec[stop_role]["file"], r["line"])
            for r in stops
            if self.spec.get(stop_role) and self.spec[stop_role]["file"] in self.sources
        )
        dependency = self.points(payload["lines"])
        # The sink DDG cannot see sibling grammar checks (a void end-of-input validator, for
        # example). Export the bounded CPG call/control facts while the graph is open.
        evidence = None
        if dependency:
            try:
                evidence, _ = self.query(
                    "input_evidence", input_evidence_query(self.repo.as_posix(), sorted(dependency))
                )
            except QueryError as exc:
                evidence = {"version": 1, "status": "query_failed", "error": str(exc)[:300]}
            payload["input_evidence"] = evidence
        # Pointer movement and writes through that pointer, in the sink's function: backward up to
        # the sink; forward after the source line (same function) or the whole sink function.
        sink_rows = starts if direction == "backward" else stops
        sink_spec = self.spec["sink"]
        steps: set[tuple[str, int]] = set()
        if sink_rows and sink_spec["file"] in self.sources:
            method = sink_rows[0].get("method") or {}
            low, high = method.get("line", -1), method.get("line_end", -1)
            sink_lines = sorted({r["line"] for r in sink_rows})
            if direction == "backward":
                high = max(sink_lines)
            elif any(low <= r["line"] <= high for r in starts):
                low = min(r["line"] for r in starts)
            if low > 0 and high >= low:
                steps = {
                    (sink_spec["file"], n)
                    for n in pointer_steps(self.sources[sink_spec["file"]], sink_lines, low, high)
                }
        dependency |= steps
        # Backward: field writes of an object the sink passes whole to a call, before the sink.
        fields: set[tuple[str, int]] = set()
        if direction == "backward" and sink_rows and sink_spec["file"] in self.sources:
            method = sink_rows[0].get("method") or {}
            low = method.get("line", -1)
            sink_lines = sorted({r["line"] for r in sink_rows})
            if low > 0:
                fields = {
                    (sink_spec["file"], n)
                    for n in field_writes(
                        self.sources[sink_spec["file"]], sink_lines, low, max(sink_lines) - 1
                    )
                }
        dependency |= fields
        points = anchors | dependency
        state = {"points": sorted(points)}
        self.expand(state)
        points = {tuple(p) for p in state["points"]}
        if self.index is not None:
            payload["input_context"] = build_input_context(
                evidence,
                self.index,
                Source(self.sources),
                dependency=dependency,
                anchors=anchors,
                cross_method_flow=flow,
            )
        entry_row = starts[0]
        entry = {
            "file": self.spec[start_role]["file"],
            "line": entry_row["line"],
            "symbol": entry_row["name"] or entry_row["code"],
        }
        return {
            "id": direction,
            "candidate_key": direction,
            "case": CASE_OF[direction],
            "direction": direction,
            "entry": entry,
            "members": [direction],
            "absorbed": [],
            "lines": [{"file": f, "line": n} for f, n in sorted(points)],
            "code": assemble(points, self.sources),
            "flow_count": None,
            "truncated": payload["truncated"],
            "analysis_kind": f"{direction}_ddg",
            "analysis": payload,
            "measurement_notes": {"flow_count": "Dependency graph; paths were not enumerated"},
            "source_sink": self.endpoints(direction, entry, payload),
            "line_provenance": {
                "data_dependency": sorted(dependency),
                "candidate_anchor": sorted(anchors),
                "context": sorted(points - dependency - anchors),
                "target_anchor": target_anchor,
                "pointer_steps": sorted(steps),
                "field_writes": sorted(fields),
                # Lines kept only because they follow the sink in its own function.
                "after_target": sorted(self.points(payload.get("after_target_lines") or [])),
            },
        }

    def expand(self, state: dict) -> None:
        """The discovery mode's `expand_context` fixed point -- context query plus source recovery
        -- with the three differences that keep a targeted slice small: only the returns before the
        function's last slice line (a guard that can stop the flow reaching it), no repo-wide
        assignments to referenced variables (the DDG walk already brought the data dependency), and
        no `case`/`default` labels of switches the slice does not touch. With the discovery
        rules two data lines of cJSON `parse_string` grew to 51 of its 60 lines -- a count from the
        PR #638 live-Joern run, unrecorded: no run ID or reproduction procedure in this repo."""
        points = {tuple(p) for p in state["points"]}
        guard_points = sorted(points)  # start points + data-dependency lines: the guard bound
        while True:
            old = points.copy()
            data, _ = self.query(
                "context",
                queries.context_query(
                    sorted(points),
                    self.repo.as_posix(),
                    guard_returns_only=True,
                    guard_points=guard_points,
                    follow_assignments=False,
                    case_labels=False,
                ),
            )
            points |= self.points(data)
            points = recover_points(points, self.sources)
            state["points"] = sorted(points)
            if old == points:
                return

    def degraded(self, direction: str, role: str, note: dict, other: dict | None) -> dict | None:
        """A slice without a graph, for when Joern found no node on the start line.

        c2cpg that cannot read a function body (libtiff `NeXTDecode`: a 95-line function with 12
        AST nodes -- counts from the PR #638 live-Joern run, unrecorded: no run ID or reproduction
        procedure in this repo) leaves no start node and so no slice. The two lines the spec gives
        and the syntax around them are still known from the source; the result is marked
        `degraded: true` and has no data dependency, like upstream `degraded_slice`."""
        spec = self.spec[role]
        if spec["file"] not in self.sources:
            return None
        anchors = {(spec["file"], note.get("line") or spec["line"])}
        far_role = "source" if role == "sink" else "sink"
        if other is not None and self.spec.get(far_role):
            far = self.spec[far_role]
            if far["file"] in self.sources:
                anchors.add((far["file"], other.get("line") or far["line"]))
        anchors = {p for p in anchors if 1 <= p[1] <= len(self.sources[p[0]])}
        if not anchors:
            return None
        points = recover_points(anchors, self.sources)
        line = note.get("line") or spec["line"]
        entry = {"file": spec["file"], "line": line, "symbol": spec.get("symbol") or ""}
        self.increment("candidates_degraded")
        return {
            "id": direction,
            "candidate_key": direction,
            "case": CASE_OF[direction],
            "direction": direction,
            "entry": entry,
            "members": [direction],
            "absorbed": [],
            "lines": [{"file": f, "line": n} for f, n in sorted(points)],
            "code": assemble(points, self.sources),
            "flow_count": None,
            "truncated": False,
            "degraded": True,
            "analysis_kind": "degraded_lexical",
            "analysis": {
                "lines": [],
                "flow_count": None,
                "truncated": False,
                "degraded": True,
                "boundaries": [],
                "target_reached": False,
                "reason": note.get("match"),
            },
            "measurement_notes": {
                "degraded": f"Joern located no node ({note.get('match')}); the spec's lines and "
                "their lexical structure only -- no data dependency"
            },
            "source_sink": {
                "basis": f"degraded_{direction}",
                "sources": [],
                "sinks": [],
                "pairs": [],
                "target_reached": False,
            },
            "line_provenance": {
                "data_dependency": [],
                "candidate_anchor": sorted(anchors),
                "context": sorted(points - anchors),
            },
        }

    def endpoints(self, direction: str, entry: dict, payload: dict) -> dict:
        def keep(raw: dict, kind: str) -> dict | None:
            mapped, _ = normalize_points([raw], self.sources, self.repo)
            if not mapped:
                return None
            file, line = mapped.pop()
            return {"file": file, "line": line, "kind": kind}

        start = keep(entry, "target_" + ("sink" if direction == "backward" else "source"))
        ends = [
            point
            for b in payload["boundaries"]
            if (point := keep(b["location"], b["reason"])) is not None
        ]
        starts = [start] if start else []
        ordered = sorted(ends, key=lambda p: (p["file"], p["line"], p["kind"]))
        return {
            "basis": f"targeted_{direction}",
            "sources": ordered if direction == "backward" else starts,
            "sinks": starts if direction == "backward" else ordered,
            "pairs": [],
            "target_reached": payload["target_reached"],
        }

    def scopes(self, role: str, rows: list[dict]) -> list[dict]:
        """The enclosing function of the start node, in the shape of a discovery-mode candidate's
        `scopes` (`{file, line, symbol, full_name}`). With no located node the spec's function
        name is used when it gave one (the method's own line is then unknown: the spec line)."""
        if rows:
            method = rows[0]["method"]
            return [
                {
                    "file": method["file"],
                    "line": method["line"],
                    "symbol": method["symbol"],
                    "full_name": method["full_name"],
                }
            ]
        spec = self.spec[role]
        if spec.get("function"):
            return [
                {
                    "file": spec["file"],
                    "line": spec["line"],
                    "symbol": spec["function"],
                    "full_name": spec["function"],
                }
            ]
        return []

    def run(self) -> dict:
        started = time.perf_counter()
        try:
            self.load_sources()
            self.prepare_checkpoint()
            # The macros that keep the start/stop lines alive go to c2cpg as definitions.
            self.defines = sorted(
                {
                    name
                    for role in ("sink", "source")
                    if (spec := self.spec.get(role)) and spec["file"] in self.sources
                    for name in activating_defines(self.sources[spec["file"]], spec["line"])
                }
            )
            self.result["target"]["defines"] = self.defines
            self.build_cpg()
            self.inventory()
            sinks, sink_note = self.locate("sink")
            sources, source_note = ([], None)
            if self.spec.get("source"):
                sources, source_note = self.locate("source")
            self.result["target"]["located"] = {"sink": sink_note, "source": source_note}
            # The start/stop nodes go where the manifest of candidates would be, so a reader finds
            # them under the same key -- with the `scopes` the discovery mode writes, which is
            # where the harness loader takes the enclosing function's name from.
            self.result["candidate_manifest"] = {
                "backward": {
                    "start": "sink",
                    "stop": "source",
                    "nodes": sink_note,
                    "scopes": self.scopes("sink", sinks),
                },
                **(
                    {
                        "forward": {
                            "start": "source",
                            "stop": "sink",
                            "nodes": source_note,
                            "scopes": self.scopes("source", sources),
                        }
                    }
                    if source_note
                    else {}
                ),
            }
            if not sinks:
                self.errors.append(f"Sink node not located: {sink_note.get('match')}")
                self.completed.append(self.degraded("backward", "sink", sink_note, source_note))
            else:
                self.completed.append(self.safely("backward", sinks, sources, "sink"))
            if self.spec.get("source"):
                if not sources:
                    self.errors.append(f"Source node not located: {source_note.get('match')}")
                    self.completed.append(
                        self.degraded("forward", "source", source_note, sink_note)
                    )
                else:
                    self.completed.append(self.safely("forward", sources, sinks, "source"))
            self.completed = [item for item in self.completed if item is not None]
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
        except KeyboardInterrupt:
            self.errors.append("Interrupted by user; server execution may continue")
        finally:
            self.active_candidate = None
            self.candidate_started = None
            if self.cpg_ready and self.close_project:
                self.cleanup()
            self.finish_target(started)
        return self.result

    def cleanup(self) -> None:
        """Close and delete the project this run opened, so one server running many samples does
        not pile up graphs. A failure is recorded in the diagnostics and does not change the
        result. `close` and `delete` are Joern console commands; they have not been run against a
        real Joern in this repository."""
        query = queries.envelope(
            "cleanup",
            'val name = workspace.getActiveProject.map(_.name).getOrElse(""); '
            "scala.util.Try(close(name)); scala.util.Try(delete(name)); ujson.Bool(true)",
        )
        try:
            self.query("cleanup", query)
        except QueryError as exc:
            self.result["diagnostics"].append({"stage": "cleanup", "error": str(exc)})

    def safely(self, direction: str, starts: list, stops: list, role: str) -> dict | None:
        try:
            return self.direction(direction, starts, stops, role)
        except QueryError as exc:
            self.increment("candidates_failed")
            self.errors.append(f"{direction} slice failed: {exc}")
            return None

    def finish_target(self, started: float) -> None:
        """Upstream `finish` without the clustering: the two directions are different slices."""
        if self.cpg_ready:
            truncated = [c for c in self.completed if c["truncated"]]
            if truncated:
                # As the discovery mode does: a cut slice makes the run `partial`, not `ok`.
                self.errors.append(f"Analysis truncated for {len(truncated)} completed candidates")
            self.measure(
                "backward_truncated",
                sum(c["truncated"] for c in self.completed if c["direction"] == "backward"),
            )
            self.measure("slices_before_clustering", len(self.completed))
            self.measure("slices_after_clustering", len(self.completed))
            self.result["slices"] = self.completed
            for key in ("candidates_case_a", "candidates_case_b", "flows_truncated"):
                self.result["measurement_notes"][key] = "Targeted mode: no candidate discovery"
            self.result["status"] = "partial" if self.errors else "ok"
        if self.errors:
            self.result["error"] = "; ".join(dict.fromkeys(self.errors))
        self.measure("total_seconds", time.perf_counter() - started)


def validate_spec(spec: object) -> str | None:
    """The first problem in the spec file, or None. Run before any Joern work."""
    if not isinstance(spec, dict) or not isinstance(spec.get("sink"), dict):
        return "spec needs a sink object"
    for role in ("sink", "source"):
        end = spec.get(role)
        if end is None and role == "source":
            continue
        if not isinstance(end, dict):
            return f"{role}: not an object"
        if not isinstance(end.get("file"), str) or not end["file"]:
            return f"{role}.file: a repo-relative path is required"
        for key in ("line", "line_end"):
            value = end.get(key)
            if (key == "line" or value is not None) and (type(value) is not int or value < 1):
                return f"{role}.{key}: a positive integer is required"
        for key in ("function", "symbol"):
            if end.get(key) is not None and not isinstance(end[key], str):
                return f"{role}.{key}: a string is required"
        span = end.get("function_span")
        if span is not None and not (
            isinstance(span, list)
            and len(span) == 2
            and all(type(n) is int and n >= 1 for n in span)
            and span[0] <= span[1]
        ):
            return f"{role}.function_span: [first, last] with 1 <= first <= last"
    return None


def load_index(path: Path) -> tuple[dict | None, dict | None, str | None]:
    """Read the harness syntax index (`tools/harness-tools/indexer.py`). Returns (index,
    {"version", "sha256"}, error). index-v2 and later carry the tables this module reads."""
    try:
        raw = path.read_bytes()
        index = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        return None, None, f"--index unreadable: {exc}"
    if not isinstance(index, dict) or index.get("version") not in INDEX_VERSIONS:
        return None, None, f"--index version is not one of {', '.join(INDEX_VERSIONS)}"
    for table in ("functions", "calls", "macros", "file_vars"):
        if not isinstance(index.get(table), dict):
            return None, None, f"--index has no {table} table"
    if not isinstance(index.get("files"), list):
        return None, None, "--index has no files list"
    return index, {"version": index["version"], "sha256": hashlib.sha256(raw).hexdigest()}, None


def code_files(here: Path, base_dir: Path) -> list[Path]:
    """The Python whose bytes go into `run_signature` (`code_hashes`): the canon slicer modules,
    this directory's `slicer*.py`, and its `input_*.py`. The `input_*` modules write
    `analysis.input_evidence`/`input_context`, so a change in them is a different run -- a glob of
    `slicer*.py` alone would leave their output outside the signature."""
    return sorted(
        [*base_dir.glob("slicer*.py"), *here.glob("slicer*.py"), *here.glob("input_*.py")]
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    result.add_argument("--repo", required=True, type=Path)
    result.add_argument("--spec", required=True, type=Path, help="sink (and source) JSON")
    result.add_argument("--out", required=True, type=Path)
    result.add_argument(
        "--index",
        type=Path,
        help="the harness syntax index (index-v2 or later): adds analysis.input_context",
    )
    result.add_argument("--compile-commands", type=Path)
    result.add_argument("--joern-endpoint", default="http://localhost:16240")
    result.add_argument("--joern-version-command", type=Path)
    result.add_argument("--progress", type=Path)
    result.add_argument("--heartbeat-seconds", type=base.positive_seconds, default=30)
    result.add_argument("--query-timeout-seconds", type=base.positive_seconds, default=None)
    result.add_argument("--candidate-timeout-seconds", type=base.positive_seconds)
    result.add_argument("--backward-depth", type=base.positive_int, help="both directions")
    result.add_argument("--backward-nodes", type=base.positive_int, help="both directions")
    result.add_argument(
        "--max-ascent",
        type=base.positive_int,
        help="backward: caller levels climbed from the sink with no call stack",
    )
    result.add_argument(
        "--ascent-fanout",
        type=int,
        default=0,
        help="backward: callers kept per no-stack ascent (0 = all); ranked same file, product "
        "path, key",
    )
    for option in ("--max-call-depth", "--max-args-to-allow", "--max-output-args-expansion"):
        result.add_argument(option, type=base.positive_int, default=None)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"spec unreadable: {exc}", file=sys.stderr)
        return 1
    problem = validate_spec(spec)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    index, index_identity = None, None
    if args.index:
        index, index_identity, problem = load_index(args.index)
        if problem:
            print(problem, file=sys.stderr)
            return 1
    payload_dir = (args.out.parent / ".slicer-payloads").resolve()
    client = base.JoernClient(
        args.joern_endpoint, args.query_timeout_seconds, payload_dir=payload_dir
    )
    limits = {
        k: v
        for k, v in (
            ("maxCallDepth", args.max_call_depth),
            ("maxArgsToAllow", args.max_args_to_allow),
            ("maxOutputArgsExpansion", args.max_output_args_expansion),
        )
        if v is not None
    }
    job = TargetSlicer(args.repo, client, spec, index, engine_limits=limits)
    compile_commands_sha256 = None
    if args.compile_commands:
        try:
            compile_commands_sha256 = hashlib.sha256(args.compile_commands.read_bytes()).hexdigest()
        except OSError:
            compile_commands_sha256 = None
    job.settings = {
        "mode": "targeted",
        # The spec picks the lines that get cut, and `defines` follows from it and the sources, so
        # two runs that cut different lines must not share a run_signature (PR #638 review).
        "spec": {role: spec[role] for role in ("sink", "source") if role in spec},
        "query_timeout_seconds": args.query_timeout_seconds,
        "engine_overrides": limits,
        "backward_depth": args.backward_depth,
        "backward_nodes": args.backward_nodes,
        "max_ascent": args.max_ascent,
        "ascent_fanout": args.ascent_fanout,
        "candidate_timeout_seconds": args.candidate_timeout_seconds,
        "compile_commands": args.compile_commands.as_posix() if args.compile_commands else None,
        "compile_commands_sha256": compile_commands_sha256,
        # The index steers analysis.input_context, so a different index is a different run.
        "index": index_identity,
    }
    job.result["settings"] = job.settings
    job.compile_commands = args.compile_commands.resolve() if args.compile_commands else None
    job.compile_db_dir = payload_dir
    job.code_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in code_files(Path(__file__).parent, Path(base.__file__).parent)
    }
    job.heartbeat_seconds = args.heartbeat_seconds
    try:
        payload_dir.mkdir(parents=True, exist_ok=True)
        if args.progress:
            args.progress.parent.mkdir(parents=True, exist_ok=True)
            job.progress = args.progress.open("a", encoding="utf-8", newline="\n")
        version, note = base.read_version(args.joern_version_command, args.query_timeout_seconds)
        job.result["joern_version"] = version
        if version is None:
            job.result["measurement_notes"]["joern_version"] = note
        result = job.run()
        base.write_result(args.out, result)
        return {"ok": 0, "partial": 2, "failed": 1}[result["status"]]
    except (OSError, ValueError) as exc:
        print(f"Cannot write or prepare slicer result: {exc}", file=sys.stderr)
        job.result["error"] = str(exc)
        try:
            base.write_result(args.out, job.result)
        except (OSError, ValueError):
            pass
        return 1
    finally:
        if job.progress:
            job.progress.close()


if __name__ == "__main__":
    raise SystemExit(main())
