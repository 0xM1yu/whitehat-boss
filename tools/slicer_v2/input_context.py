"""Select the input-decoding functions that sit next to a slice (targeted mode, `--index`).

The first stage (`input_evidence`) exports a small set of cross-method CPG edges. This module uses
those edges and the harness's tree-sitter index (`tools/harness-tools/indexer.py`, index-v2 or
later) to follow the relevant calls; it does not classify functions by names such as `parse_*`.
The syntax signals are deliberately conservative: a candidate must read or advance a pointer and
test or convert what it reads. A decoder is admitted when the caller sits on a dependency/anchor
line, or when a selected decoder passes it output state or shares its input cursor.

The result is data, not source text: `build_input_context` returns the JSON that the slicer stores
as `slices[].analysis.input_context` (shape in docs/slicer-README.md). What to show, where, and in
which order is the consumer's decision -- this module only says which functions it found, why, and
whether a cap stopped it.

Every cap and threshold below is the PR #638 author's choice (`slicer_v2/attach/input_context.py`
and `attach.py`) with **no measurement behind it** (CLAUDE.md section 0); each says so where it is
defined. A cap that was reached is reported in `cap_hit`/`cap_reasons`; an absent reason does not
mean the cap was far away.
"""

from __future__ import annotations

import re
from collections import deque

from input_paths import cpg_input_paths, cpg_proof_for
from input_text import Location, code_only, contains, lines_of, scope

VERSION = 1
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
KEYWORDS = frozenset({"if", "for", "while", "switch", "return", "sizeof", "defined"})
# These are syntax/standard-library hints, not names of project-specific parsers.
CHAR_TEST = re.compile(
    r"\b(?:is(?:alnum|alpha|digit|xdigit|space|punct)|strto(?:l|ul|ll|ull|d|f)|"
    r"atoi|atol|sscanf|scanf|memcmp|strncmp|"
    r"[A-Za-z_]\w*(?:digit|hex|alnum|char_class)\w*)\s*\(",
    re.I,
)
SOURCE_READ = re.compile(r"\b(?:fread|read|pread|recv|recvfrom|fgets|getline|getc|fgetc)\s*\(")

# --- caps and thresholds. All unmeasured: the PR gave no corpus, no sweep, no n. ---------------
# Call hops followed below a decoder looking for nested decoders.
MAX_DEPTH = 3
# Decoders a run may add (`choose_input_functions`).
DEFAULT_MAX_FUNCTIONS = 4
# Decoders the grammar-check pass may add (`choose_input_contract`).
DEFAULT_GRAMMAR_FUNCTIONS = 3
# Characters of function bodies the two passes may add together.
DEFAULT_MAX_CHARS = 50_000
# String-keyed function-table rows reported.
DEFAULT_MAX_DISPATCH_ROWS = 4
# Structural score a function needs to count as a decoder (`_input_signals` weights are the
# author's; the threshold 5 is not derived from them).
MIN_SCORE = 5
# A validator longer than this is "a broad expression parser", not a terminal grammar check.
MAX_VALIDATOR_LINES = 160
# A function table longer than this is skipped as a dispatch-table candidate.
MAX_TABLE_LINES = 160
# A carried function longer than this is not promoted as a whole.
MAX_PROMOTED_LINES = 160
# Same-directory files searched for a definition the index missed. Not flagged when it bites.
MAX_NEIGHBOR_FILES = 80
# Lines scanned past an index-reported end for the closing brace of a function. Not flagged.
MAX_REPAIR_LINES = 1000
# Unresolved checked calls quoted in the diagnostics.
UNRESOLVED_SAMPLE = 20
# --------------------------------------------------------------------------------------------

CAP_REASONS = (
    "dispatch_rows",
    "evidence",
    "grammar_budget",
    "match_depth",
    "max_chars",
    "max_functions",
    "path_depth",
)


class Source:
    """Repository text by file, split the way Joern counts lines (`input_text.lines_of`).

    `files` maps a repo-relative path to its lines -- exactly what the slicer already holds in
    `Slicer.sources`, so a function is read once from the same text the CPG was built from. A file
    that is not there reads as empty *and is recorded* in `missing`, so "no include / no
    definition" is never silently "could not read".
    """

    def __init__(self, files: dict[str, list[str]]):
        self.files = files
        self.missing: set[str] = set()
        self._code_rows: dict[str, list[str]] = {}

    def read(self, location: Location) -> str:
        rows = self.files.get(location.file)
        if rows is None:
            self.missing.add(location.file)
            return ""
        if location.line_start is None:
            return "\n".join(rows)
        end = location.line_end if location.line_end is not None else location.line_start
        return "\n".join(rows[location.line_start - 1 : end])

    def code_rows(self, file: str) -> list[str]:
        """`code_only` of the whole file, split into lines, computed once per file."""
        if file not in self._code_rows:
            self._code_rows[file] = lines_of(code_only(self.read(Location(file=file))))
        return self._code_rows[file]


def _assigned_result(line: str, callee: str) -> str | None:
    """Name receiving a call result, including `if ((value = call(...)))`."""
    before = re.split(rf"\b{re.escape(callee)}\s*\(", code_only(line), maxsplit=1)[0]
    match = re.search(r"\b([A-Za-z_]\w*)\s*=\s*$", before)
    return match.group(1) if match else None


def _result_is_checked(rows: list[str], line_offset: int, name: str) -> bool:
    """The result controls acceptance or is passed on to the caller."""
    tail = "\n".join(rows[line_offset:])
    token = re.escape(name)
    return bool(
        re.search(rf"\b(?:if|while|switch|return)\b[^;{{}}]*\b{token}\b", tail)
        or re.search(rf"\breturn\s+{token}\b", tail)
    )


def _flow_lines(cross_method_flow: list[dict] | None) -> set[tuple[str, int]]:
    lines = set()
    for edge in cross_method_flow or []:
        for side in ("from", "to"):
            point = edge.get(side, {})
            if isinstance(point.get("file"), str) and type(point.get("line")) is int:
                lines.add((point["file"], point["line"]))
    return lines


def choose_input_contract(
    index: dict,
    source: Source,
    carried: list[Location],
    dependency: set[tuple[str, int]],
    anchors: set[tuple[str, int]],
    cross_method_flow: list[dict] | None = None,
    *,
    max_functions: int = DEFAULT_GRAMMAR_FUNCTIONS,
    max_chars: int = DEFAULT_MAX_CHARS,
    already_full: set[tuple[str, int]] | None = None,
    diagnostics: dict | None = None,
    cpg_paths: dict[tuple[str, int], dict] | None = None,
) -> list[dict]:
    """Follow grammar-producing calls beside the sink's data-dependency path.

    A string/number decoder may determine whether input is accepted without contributing to the
    sink value. Start from a retained input handler's checked call results, then follow returned
    values through short wrappers. Only chains ending in a function that reads the same raw input
    cursor are admitted. This keeps unrelated helpers out of the small grammar budget.
    """
    if not index or not max_functions or not max_chars:
        return []
    raw_cursors = (
        {p["input_state"] for p in cpg_paths.values()}
        if cpg_paths is not None
        else _raw_cursor_globals(index, carried, source)
    )
    if not raw_cursors:
        if diagnostics is not None:
            diagnostics["status"] = "no_retained_input_cursor"
        return []
    flow_lines = _flow_lines(cross_method_flow)
    seeds = dependency | anchors | flow_lines
    by_file: dict[str, list[tuple[str, dict]]] = {}
    already_full = already_full or set()

    def returned_chain(
        owner: Location, name: str, row: dict, depth: int, visited: set[tuple[str, int]]
    ) -> list[dict]:
        if depth > MAX_DEPTH:
            return []
        location, macro = _resolve(index, source, name, owner.file)
        if location is None or scope(location) in visited:
            return []
        body = source.read(location)
        score, cursors = _input_signals(body)
        proof = cpg_proof_for(location, cpg_paths) if cpg_paths is not None else None
        if cpg_paths is not None and proof is None:
            return []
        record = {
            "location": location,
            "score": score,
            "chars": 0 if scope(location) in already_full else len(body),
            "call_file": row["file"],
            "call_line": row["line"],
            "callee": name,
            "macro": macro,
            "depth": depth,
            "input_cursors": sorted(cursors),
            "joern_flow": (row["file"], row["line"]) in flow_lines,
            "reason": "checked input result or returned decoder value",
        }
        if score >= MIN_SCORE and (
            cursors & raw_cursors or proof and proof["method_state"] in cursors
        ):
            return [record]
        # A wrapper often checks/copies a local result while the nested decoder alone advances
        # the input cursor (e.g. a C-string validator). Require that the nested result affects
        # the wrapper's return/guard.
        rows = lines_of(code_only(body))
        for nested_name, nested_row in _calls_in(index, location, source, by_file):
            offset = nested_row["line"] - (location.line_start or 1)
            if not 0 <= offset < len(rows):
                continue
            local = _assigned_result(rows[offset], nested_name)
            if local is None or not _result_is_checked(rows, offset, local):
                continue
            nested = returned_chain(
                location, nested_name, nested_row, depth + 1, visited | {scope(location)}
            )
            if nested:
                return [record, *nested]
        return []

    chains: list[tuple[int, list[dict]]] = []
    validators: list[tuple[int, list[dict]]] = []
    unresolved: list[dict] = []
    for owner in carried:
        owner_body = source.read(owner)
        _score, owner_cursors = _input_signals(owner_body)
        if not owner_cursors & raw_cursors or not any(
            contains(owner, file, line) for file, line in seeds
        ):
            continue
        rows = lines_of(code_only(owner_body))
        calls = _calls_in(index, owner, source, by_file)
        for name, row in calls:
            if (row["file"], row["line"]) in seeds:
                continue  # The ordinary dependency-path selector owns this call.
            offset = row["line"] - (owner.line_start or 1)
            if not 0 <= offset < len(rows):
                continue
            local = _assigned_result(rows[offset], name)
            if local is None or not _result_is_checked(rows, offset, local):
                continue
            chain = returned_chain(owner, name, row, 0, set())
            if chain:
                chains.append((row["line"], chain))
            else:
                unresolved.append({"file": row["file"], "line": row["line"], "callee": name})
        starts = [
            line
            for line, chain in chains
            if chain[0]["call_file"] == owner.file and contains(owner, owner.file, line)
        ]
        if starts:
            for name, row in calls:
                if row["line"] <= min(starts):
                    continue
                location, macro = _resolve(index, source, name, owner.file)
                if location is None:
                    continue
                body = source.read(location)
                score, cursors = _input_signals(body)
                proof = cpg_proof_for(location, cpg_paths) if cpg_paths is not None else None
                if (location.line_end or 0) - (location.line_start or 0) + 1 > MAX_VALIDATOR_LINES:
                    continue  # A broad expression parser is not a terminal grammar check.
                if score < MIN_SCORE or not (
                    cursors & raw_cursors or proof and proof["method_state"] in cursors
                ):
                    continue
                if cpg_paths is not None:
                    if not proof or not proof["self_guard_lines"]:
                        continue
                elif not _source_input_guard(body, cursors & raw_cursors):
                    continue
                validators.append(
                    (
                        row["line"],
                        [
                            {
                                "location": location,
                                "score": score,
                                "chars": 0 if scope(location) in already_full else len(body),
                                "call_file": row["file"],
                                "call_line": row["line"],
                                "callee": name,
                                "macro": macro,
                                "depth": 0,
                                "input_cursors": sorted(cursors),
                                "joern_flow": (row["file"], row["line"]) in flow_lines,
                                "reason": "input acceptance or failure check",
                            }
                        ],
                    )
                )
    # Source order is the parser's acceptance order. Duplicated calls to the same decoder (e.g.
    # an optional second filename) need one body only.
    chains.sort(key=lambda item: (item[1][0]["call_file"], item[0]))
    validators.sort(key=lambda item: (item[1][0]["call_file"], item[0]))
    chosen: list[dict] = []
    seen = set(already_full)
    used_chars = 0
    skipped_budget = 0
    for _line, chain in chains + validators:
        pending = [record for record in chain if scope(record["location"]) not in seen]
        cost = sum(record["chars"] for record in pending)
        if len(chosen) + len(pending) > max_functions or used_chars + cost > max_chars:
            skipped_budget += 1
            continue
        chosen.extend(pending)
        used_chars += cost
        seen.update(scope(record["location"]) for record in pending)
    if diagnostics is not None:
        diagnostics.update(
            status="selected" if chosen else "no_grammar_chain",
            raw_cursors=sorted(raw_cursors),
            checked_result_chains=len(chains),
            validation_calls=len(validators),
            selected_functions=len(chosen),
            skipped_budget=skipped_budget,
            unresolved_checked_calls=unresolved[:UNRESOLVED_SAMPLE],
        )
    return chosen


def choose_input_dispatch(
    index: dict,
    source: Source,
    handlers: set[str],
    *,
    max_rows: int = DEFAULT_MAX_DISPATCH_ROWS,
    diagnostics: dict | None = None,
) -> list[dict]:
    """Find string-keyed function-table rows that connect input to handlers."""
    if not index or not handlers:
        return []
    candidates: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for table, definitions in index.get("file_vars", {}).items():
        for definition in definitions:
            start, end = definition.get("line_start"), definition.get("line_end")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or end - start > MAX_TABLE_LINES
            ):
                continue
            file = definition["file"]
            rows = lines_of(source.read(Location(file=file, line_start=start, line_end=end)))
            guards: list[tuple[str, str | None]] = []
            for offset, line in enumerate(rows):
                stripped = line.strip()
                if re.match(r"#\s*(?:if|ifdef|ifndef)\b", stripped):
                    guards.append((stripped, None))
                elif re.match(r"#\s*(?:else|elif)\b", stripped) and guards:
                    guards[-1] = (guards[-1][0], stripped)
                elif re.match(r"#\s*endif\b", stripped) and guards:
                    guards.pop()
                for handler in sorted(handlers):
                    match = re.search(rf'\{{\s*"([^"\n]+)"\s*,\s*{re.escape(handler)}\b', line)
                    if not match:
                        continue
                    number = start + offset
                    if (file, number) in seen:
                        continue
                    seen.add((file, number))
                    registration = next(
                        (
                            call["line"]
                            for calls in index.get("calls", {}).values()
                            for call in calls
                            if call["file"] == file
                            and table in call.get("args", [])
                            and not start <= call["line"] <= end
                        ),
                        None,
                    )
                    candidates.append(
                        {
                            "file": file,
                            "line": number,
                            "handler": handler,
                            "directive": match.group(1),
                            "conditional": bool(guards),
                            "table": table,
                            "registration_line": registration,
                        }
                    )
    candidates.sort(
        key=lambda item: (
            item["conditional"],
            item["registration_line"] is None,
            item["file"],
            item["line"],
            item["handler"],
        )
    )
    out: list[dict] = []
    bound: set[tuple[str, str]] = set()
    capped = False
    for candidate in candidates:
        key = candidate["handler"], candidate["directive"]
        if key in bound:
            continue
        if len(out) >= max_rows:
            capped = True
            break
        bound.add(key)
        out.append(candidate)
    if diagnostics is not None:
        diagnostics["rows_capped"] = capped
    return out


def _source_input_guard(body: str, cursors: set[str]) -> bool:
    """Legacy fallback (no CPG): a conditional directly inspects the input state."""
    code = code_only(body)
    return any(
        re.search(
            rf"\b(?:if|while|for|switch)\s*\([^;{{}}]*"
            rf"(?:\*\s*{re.escape(name)}\b|\b{re.escape(name)}\s*\[)",
            code,
        )
        for name in cursors
    )


def _lexical_function(source: Source, file: str, name: str) -> Location | None:
    """Recover a definition missed by tree-sitter in a macro-heavy C file."""
    code = "\n".join(source.code_rows(file))
    match = re.search(
        rf"^[^\S\n]*(?:[A-Za-z_][\w\s\*]*?\s|\*)?\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        code,
        re.M,
    )
    if match is None:
        return None
    depth = 0
    for offset in range(match.end() - 1, len(code)):
        if code[offset] == "{":
            depth += 1
        elif code[offset] == "}":
            depth -= 1
            if depth == 0:
                return Location(
                    file=file,
                    symbol=name,
                    line_start=code.count("\n", 0, match.start()) + 1,
                    line_end=code.count("\n", 0, offset) + 1,
                )
    return None


def _indexed_definition(index: dict, name: str, owner_file: str) -> Location | None:
    """The one definition of `name` that can be told apart by name alone, or None.

    The caller's own file wins; otherwise the name must be defined exactly once in the repository.
    A same-name overload or another translation unit cannot be identified from a name-only call
    record, so an ambiguous name resolves to nothing rather than to an arbitrary body (the rule of
    `src.adapters.index_graph.GraphIndex.functions_named`, which the harness side uses).
    """
    rows = [
        r
        for r in index.get("functions", {}).get(name, [])
        if isinstance(r.get("file"), str)
        and isinstance(r.get("line_start"), int)
        and isinstance(r.get("line_end"), int)
    ]
    candidates = [r for r in rows if r["file"] == owner_file] or rows
    if len(candidates) != 1:
        return None
    row = candidates[0]
    return Location(
        file=row["file"], symbol=name, line_start=row["line_start"], line_end=row["line_end"]
    )


def _resolve(
    index: dict, source: Source, name: str, owner_file: str
) -> tuple[Location | None, str | None]:
    """Resolve a direct call or a function-like macro with one defined target."""
    original = name
    if name not in index.get("functions", {}):
        targets: set[str] = set()
        for macro in index.get("macros", {}).get(name, []):
            body = code_only(
                source.read(
                    Location(
                        file=macro["file"],
                        line_start=macro["line_start"],
                        line_end=macro["line_end"],
                    )
                )
            )
            body = body.replace("\\\n", " ")
            # Do not read calls from the macro parameter list as callees.
            body = re.sub(r"^\s*#\s*define\s+\w+\s*(?:\([^)]*\))?", "", body, count=1)
            targets |= {n for n in CALL.findall(body) if n in index.get("functions", {})}
        if len(targets) == 1:
            name = next(iter(targets))
    macro_name = original if name != original else None
    defined = _indexed_definition(index, name, owner_file)
    if defined is not None:
        return _repair_function_end(index, source, defined), macro_name
    # A syntax error elsewhere in a C file can make tree-sitter miss a whole function. Recover a
    # unique definition in the caller's file or its directory; a name-only match in multiple
    # files is intentionally unsafe.
    local = _lexical_function(source, owner_file, name)
    if local is not None:
        return local, macro_name
    directory = owner_file.rsplit("/", 1)[0] + "/"
    neighbors = list(
        dict.fromkeys(
            row["file"]
            for row in index.get("calls", {}).get(name, [])
            if row["file"].startswith(directory) and row["file"] != owner_file
        )
    )
    neighbors += [
        file
        for file in index.get("files", [])
        if file.startswith(directory)
        and file.endswith((".c", ".cc", ".cpp"))
        and file != owner_file
        and file not in neighbors
    ]
    matches = [_lexical_function(source, file, name) for file in neighbors[:MAX_NEIGHBOR_FILES]]
    unique = [location for location in matches if location is not None]
    return (unique[0] if len(unique) == 1 else None), macro_name


def _repair_function_end(index: dict, source: Source, location: Location) -> Location:
    """Extend an index span ending on an indented brace inside a C function.

    Preprocessor alternatives can confuse tree-sitter's end position. A real file-scope C function
    normally closes with a brace in column zero. Stop at the next indexed function so an unrelated
    definition cannot be pulled into this body. Ambiguous cases retain the indexed span.
    """
    if location.line_start is None or location.line_end is None:
        return location
    rows = source.code_rows(location.file)
    if location.line_end > len(rows):
        return location
    if re.fullmatch(r"\s*}\s*", rows[location.line_end - 1]) is None:
        return location
    if re.fullmatch(r"}\s*", rows[location.line_end - 1]):
        return location
    following = [
        row["line_start"]
        for definitions in index.get("functions", {}).values()
        for row in definitions
        if row.get("file") == location.file
        and isinstance(row.get("line_start"), int)
        and row["line_start"] > location.line_start
    ]
    stop = min(following, default=min(len(rows) + 1, location.line_end + MAX_REPAIR_LINES))
    for number in range(location.line_end + 1, min(stop, len(rows) + 1)):
        if re.fullmatch(r"}\s*", rows[number - 1]):
            return Location(
                file=location.file,
                symbol=location.symbol,
                line_start=location.line_start,
                line_end=number,
            )
    return location


def _input_signals(code: str) -> tuple[int, set[str]]:
    """Return a structural decoding score and the pointer/cursor names it reads."""
    body = code_only(code)
    reads = set(re.findall(r"\*\s*([A-Za-z_]\w*)\s*(?:\+\+|--)?", body))
    reads |= set(re.findall(r"\b([A-Za-z_]\w*)\s*\[[^\]]+\]", body))
    moves = {
        name
        for name in reads
        if re.search(
            rf"(?:\b{re.escape(name)}\s*(?:\+\+|--|[+\-]=|=(?!=))|"
            rf"(?:\+\+|--)\s*{re.escape(name)}\b)",
            body,
        )
    }
    # A buffer supplied as a parameter can be indexed without moving a cursor.
    checked = {
        name
        for name in reads
        if re.search(rf"\b(?:if|while|for|switch)\s*\([^;]*\b{re.escape(name)}\b", body)
    }
    cursors = moves | checked
    if not reads and not SOURCE_READ.search(body) and not CHAR_TEST.search(body):
        return 0, set()
    tests = len(re.findall(r"\b(?:if|while|for|switch)\s*\(", body))
    literals = len(re.findall(r"(?:==|!=|<=|>=|<|>)\s*(?:'[^']'|\d+)", body))
    score = (
        2 * bool(reads)
        + 2 * bool(moves)
        + bool(checked)
        + bool(tests)
        + min(2, literals)
        + 2 * bool(CHAR_TEST.search(body))
        + 2 * bool(SOURCE_READ.search(body))
    )
    if re.search(r"\breturn\b|(?:->|\.)\s*\w+\s*=(?!=)", body):
        score += 1
    return score, cursors


def _calls_in(
    index: dict, location: Location, source: Source, by_file: dict[str, list[tuple[str, dict]]]
) -> list[tuple[str, dict]]:
    # The index is keyed by *callee*, not caller. Cache the files actually encountered so several
    # decoder functions in one file do not rescan the whole repository's call table for each.
    if location.file not in by_file:
        by_file[location.file] = [
            (name, row)
            for name, rows in index.get("calls", {}).items()
            for row in rows
            if row["file"] == location.file
        ]
    out = [
        (name, row)
        for name, row in by_file[location.file]
        if contains(location, row["file"], row["line"])
    ]
    if not out and location.line_start is not None:
        # A broken parse can omit the *entire* caller from the index. In that case its calls are
        # absent too, even when their targets are indexed. Recover only calls to known functions
        # in this one bounded body.
        body = code_only(source.read(location))
        known = set(index.get("functions", {}))
        for offset, line in enumerate(lines_of(body)):
            for name in CALL.findall(line):
                if name in known and name not in KEYWORDS:
                    out.append(
                        (
                            name,
                            {
                                "file": location.file,
                                "line": location.line_start + offset,
                                "caller": location.symbol,
                                "args": [line],
                            },
                        )
                    )
    return sorted(out, key=lambda pair: (pair[1]["line"], pair[0]))


_TOKENS = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", re.S)
_CHARACTER_TEST = re.compile(
    r"(?:\*\s*([A-Za-z_]\w*)|([A-Za-z_]\w*)\s*\[[^\]\n]+\])"
    r"\s*(?:==|!=|<=|>=|<|>)\s*(?:'[^']*'|0\b)"
)


def _raw_cursor_globals(index: dict, carried: list[Location], source: Source) -> set[str]:
    """Global cursors used to inspect input characters in the retained functions."""
    globals_ = set(index.get("file_vars", {}))
    found = set()
    for owner in carried:
        body = _TOKENS.sub(
            lambda match: (
                re.sub(r"[^\n]", " ", match.group())
                if match.group().startswith("/")
                else match.group()
            ),
            source.read(owner),
        )
        for match in _CHARACTER_TEST.finditer(body):
            name = match.group(1) or match.group(2)
            if name in globals_:
                found.add(name)
    return found


def choose_input_functions(
    index: dict,
    source: Source,
    carried: list[Location],
    dependency: set[tuple[str, int]],
    anchors: set[tuple[str, int]],
    cross_method_flow: list[dict] | None = None,
    *,
    max_functions: int = DEFAULT_MAX_FUNCTIONS,
    max_chars: int = DEFAULT_MAX_CHARS,
    already_full: set[tuple[str, int]] | None = None,
    cpg_paths: dict[tuple[str, int], dict] | None = None,
    diagnostics: dict | None = None,
) -> list[dict]:
    """Choose bounded input-decoder context with auditable reasons.

    `cross_method_flow` is the list of cross-method DDG edges exported from the live CPG by the
    traversal; without it the data-dependency lines alone seed the search.
    """
    if not index or not max_functions or not max_chars:
        return []
    flow_lines = _flow_lines(cross_method_flow)
    seeds = set(dependency) | set(anchors) | flow_lines
    raw_cursors = (
        {p["input_state"] for p in cpg_paths.values()}
        if cpg_paths is not None
        else _raw_cursor_globals(index, carried, source)
    )
    by_file: dict[str, list[tuple[str, dict]]] = {}
    roots = []
    for owner in carried:
        for name, row in _calls_in(index, owner, source, by_file):
            if (row["file"], row["line"]) in seeds:
                location, macro = _resolve(index, source, name, owner.file)
                if location is None:
                    continue
                body = source.read(location)
                score, cursors = _input_signals(body)
                roots.append((owner, name, row, 0, None, (location, macro, body, score, cursors)))
    # A decoder sharing the retained raw-input cursor leads the queue. This prevents filename/path
    # helpers on the same slice from consuming the small parser budget before the numeric or
    # string decoder is traversed.
    roots.sort(
        key=lambda item: (
            not bool(item[5][4] & raw_cursors),
            (item[2]["file"], item[2]["line"]) not in flow_lines,
            -item[5][3],
            item[2]["file"],
            item[2]["line"],
            item[1],
        )
    )
    remaining_roots = deque(roots)
    queue: deque = deque()
    result: list[dict] = []
    visited: set[tuple[str, int]] = set()
    attempted: set[tuple[tuple[str, int], tuple[str, ...], tuple[str, ...]]] = set()
    total_chars = 0
    skipped_chars = 0
    depth_cap_reached = False
    already_full = already_full or set()
    while (queue or remaining_roots) and len(result) < max_functions:
        if not queue:
            queue.append(remaining_roots.popleft())
        owner, name, row, depth, parent, cached = queue.popleft()
        if cached is None:
            location, macro = _resolve(index, source, name, owner.file)
            if location is None:
                continue
            body = source.read(location)
            score, cursors = _input_signals(body)
        else:
            location, macro, body, score, cursors = cached
        if location is None or scope(location) in visited:
            continue
        proof = cpg_proof_for(location, cpg_paths) if cpg_paths is not None else None
        if cpg_paths is not None and proof is None:
            continue
        attempt = (
            scope(location),
            tuple(sorted(parent["cursors"])) if parent else (),
            tuple(sorted(parent["params"])) if parent else (),
        )
        if attempt in attempted:
            continue
        attempted.add(attempt)
        if score < MIN_SCORE or not cursors:
            continue
        if parent is not None:
            parent_cursors = parent["cursors"]
            args = " ".join(row.get("args", []))
            # A nested decoder must share an input cursor, or receive the parent decoder's
            # value/object through an argument.
            parent_params = set(parent["params"])
            shared_cursor = bool(cursors & parent_cursors)
            value_argument = bool(set(IDENT.findall(args)) & parent_params)
            if not (
                proof
                or shared_cursor
                or value_argument
                and (CHAR_TEST.search(code_only(body)) or SOURCE_READ.search(code_only(body)))
            ):
                continue
        visited.add(scope(location))
        added_chars = 0 if scope(location) in already_full else len(body)
        if total_chars + added_chars > max_chars:
            skipped_chars += 1
            continue
        record = {
            "location": location,
            "score": score,
            "chars": added_chars,
            "call_file": row["file"],
            "call_line": row["line"],
            "callee": name,
            "macro": macro,
            "depth": depth,
            "input_cursors": sorted(cursors),
            "joern_flow": (row["file"], row["line"]) in flow_lines,
        }
        if scope(location) not in already_full:
            result.append(record)
            total_chars += added_chars
        head = code_only(body).split("{", 1)[0]
        params = (
            set(IDENT.findall(head[head.find("(") + 1 : head.rfind(")")])) if "(" in head else set()
        )
        parent_info = {"cursors": cursors, "params": params}
        nested = [
            (nested_name, nested_row)
            for nested_name, nested_row in _calls_in(index, location, source, by_file)
            if nested_name != name and nested_name not in KEYWORDS
        ]
        if depth < MAX_DEPTH:
            for nested_name, nested_row in nested:
                queue.append((location, nested_name, nested_row, depth + 1, parent_info, None))
        elif nested:
            depth_cap_reached = True
    if diagnostics is not None:
        diagnostics.update(
            selected_functions=len(result),
            skipped_chars=skipped_chars,
            # The walk stopped with candidates still queued.
            max_functions_reached=len(result) >= max_functions and bool(queue or remaining_roots),
            match_depth_cap_reached=depth_cap_reached,
        )
    return result


def _proof(location: Location, cpg_paths: dict | None) -> dict | None:
    return cpg_proof_for(location, cpg_paths) if cpg_paths is not None else None


def _function_record(choice: dict, kind: str, proof: dict | None) -> dict:
    location = choice["location"]
    return {
        "kind": kind,
        "file": location.file,
        "symbol": location.symbol,
        "line_start": location.line_start,
        "line_end": location.line_end,
        "chars": choice["chars"],
        "score": choice["score"],
        "call_file": choice["call_file"],
        "call_line": choice["call_line"],
        "callee": choice["callee"],
        "macro": choice["macro"],
        "depth": choice["depth"],
        "input_cursors": choice["input_cursors"],
        "joern_flow": choice["joern_flow"],
        "reason": choice.get("reason"),
        "cpg": proof,
    }


def _carried_decoders(
    carried: list[Location],
    source: Source,
    relevant: set[tuple[str, int]],
    raw_cursors: set[str],
    cpg_paths: dict[tuple[str, int], dict] | None,
) -> list[dict]:
    """Slice-holding functions that themselves read the input cursor: the consumer should show
    them whole instead of only their slice lines."""
    found = []
    for function in carried:
        length = (function.line_end or 0) - (function.line_start or 0) + 1
        if length > MAX_PROMOTED_LINES:
            continue
        if not any(contains(function, file, line) for file, line in relevant):
            continue
        score, cursors = _input_signals(source.read(function))
        if score < MIN_SCORE or not cursors & raw_cursors:
            continue
        proof = cpg_proof_for(function, cpg_paths) if cpg_paths is not None else None
        if cpg_paths is not None and not proof:
            continue
        found.append(
            {
                "kind": "carried_decoder",
                "file": function.file,
                "symbol": function.symbol,
                "line_start": function.line_start,
                "line_end": function.line_end,
                "score": score,
                "input_cursors": sorted(cursors & raw_cursors),
                "reason": "retained value path reads the input cursor",
                "cpg": proof,
            }
        )
    return found


def carried_from_index(index: dict, lines: set[tuple[str, int]]) -> list[Location]:
    """Without a CPG: the indexed functions that hold one of the slice lines, in file/line order."""
    found = {}
    for name, rows in index.get("functions", {}).items():
        for row in rows:
            start, end = row.get("line_start"), row.get("line_end")
            if not (type(start) is int and type(end) is int and 1 <= start <= end):
                continue
            if any(file == row.get("file") and start <= line <= end for file, line in lines):
                found[(row["file"], start, name)] = Location(
                    file=row["file"], symbol=name, line_start=start, line_end=end
                )
    return [found[key] for key in sorted(found)]


def carried_locations(evidence: dict) -> list[Location]:
    """The methods of the slice itself: those that hold a DDG point, in file/line order."""
    found = []
    for method in evidence.get("methods", []):
        start, end = method.get("line_start"), method.get("line_end")
        if (
            method.get("ddg_lines")
            and type(start) is int
            and type(end) is int
            and 1 <= start <= end
        ):
            found.append(
                Location(file=method["file"], symbol=method["name"], line_start=start, line_end=end)
            )
    return sorted(found, key=lambda loc: (loc.file, loc.line_start or 0, loc.symbol or ""))


def build_input_context(
    evidence: dict | None,
    index: dict,
    source: Source,
    *,
    dependency: set[tuple[str, int]],
    anchors: set[tuple[str, int]],
    cross_method_flow: list[dict] | None = None,
    max_functions: int = DEFAULT_MAX_FUNCTIONS,
    grammar_functions: int = DEFAULT_GRAMMAR_FUNCTIONS,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_dispatch_rows: int = DEFAULT_MAX_DISPATCH_ROWS,
) -> dict:
    """The `slices[].analysis.input_context` object (docs/slicer-README.md has the shape).

    The functions of the slice itself ("carried") are the methods that hold a DDG point (from the
    CPG evidence, else from the index); the selection never re-adds them (`already_full`) and
    reports the ones that read the input cursor themselves as `carried_decoders`. Deterministic:
    the same evidence, index and sources give the same object.
    """
    caps = {
        "max_functions": max_functions,
        "grammar_functions": grammar_functions,
        "max_chars": max_chars,
        "max_dispatch_rows": max_dispatch_rows,
        "max_depth": MAX_DEPTH,
        "min_score": MIN_SCORE,
    }
    base = {"version": VERSION, "caps": caps, "caps_measured": False}
    cpg = isinstance(evidence, dict) and evidence.get("status") == "ok"
    evidence_status = evidence.get("status") if isinstance(evidence, dict) else None
    path_report: dict = {}
    if cpg:
        carried = carried_locations(evidence)
        cpg_paths = cpg_input_paths(evidence, index, carried, path_report)
        raw_cursors = {p["input_state"] for p in cpg_paths.values()}
    else:
        # No CPG evidence (no dependency lines, or the query failed): the same selection from the
        # syntax index alone -- the functions holding a slice line, global cursors found by
        # looking at character tests. Weaker (no proof that a decoder is reached from the
        # handler), and said so in `basis`.
        carried = carried_from_index(index, set(dependency) | set(anchors))
        cpg_paths = None
        raw_cursors = _raw_cursor_globals(index, carried, source)
    present = {scope(loc) for loc in carried}
    contract_report: dict = {}
    grammar = choose_input_contract(
        index,
        source,
        carried,
        set(dependency),
        set(anchors),
        cross_method_flow,
        max_functions=grammar_functions,
        max_chars=max_chars,
        already_full=present,
        diagnostics=contract_report,
        cpg_paths=cpg_paths,
    )
    grammar_scopes = {scope(choice["location"]) for choice in grammar}
    function_report: dict = {}
    regular = choose_input_functions(
        index,
        source,
        carried,
        set(dependency),
        set(anchors),
        cross_method_flow,
        max_functions=max_functions,
        max_chars=max(0, max_chars - sum(choice["chars"] for choice in grammar)),
        already_full=present | grammar_scopes,
        cpg_paths=cpg_paths,
        diagnostics=function_report,
    )
    dispatch_report: dict = {}
    dispatches = choose_input_dispatch(
        index,
        source,
        {loc.symbol for loc in carried if loc.symbol},
        max_rows=max_dispatch_rows,
        diagnostics=dispatch_report,
    )
    functions = []
    for choice in grammar:
        functions.append(_function_record(choice, "grammar", _proof(choice["location"], cpg_paths)))
    for choice in regular:
        functions.append(_function_record(choice, "decoder", _proof(choice["location"], cpg_paths)))
    carried_decoders = _carried_decoders(
        carried, source, set(dependency) | set(anchors), raw_cursors, cpg_paths
    )
    reasons = {
        "evidence": bool(cpg and evidence.get("truncated")),
        "path_depth": bool(path_report.get("path_depth_cap_reached")),
        "grammar_budget": bool(contract_report.get("skipped_budget")),
        "max_functions": bool(function_report.get("max_functions_reached")),
        "max_chars": bool(function_report.get("skipped_chars")),
        "match_depth": bool(function_report.get("match_depth_cap_reached")),
        "dispatch_rows": bool(dispatch_report.get("rows_capped")),
    }
    cap_reasons = sorted(name for name, hit in reasons.items() if hit)
    selected = bool(functions or carried_decoders or dispatches)
    return {
        **base,
        "status": "selected" if selected else "none_selected",
        "basis": "cpg" if cpg else "syntax",
        "evidence_status": evidence_status or "unavailable",
        # True when a bound in `caps` (or the evidence's own) stopped the search; `cap_reasons`
        # names which. An empty selection with `cap_hit` set may be a truncated search.
        "cap_hit": bool(cap_reasons),
        "cap_reasons": cap_reasons,
        # True when the selection adds nothing to the functions of the slice itself, so a
        # consumer that shows those functions already shows everything found here.
        "identical_to_auditor_input": not selected,
        "carried": [
            {"file": loc.file, "symbol": loc.symbol, "line_start": loc.line_start}
            for loc in carried
        ],
        "input_states": sorted(raw_cursors),
        "linked_methods": len(cpg_paths) if cpg_paths is not None else None,
        "unreadable_files": sorted(source.missing),
        "contract": contract_report,
        "functions": functions,
        "carried_decoders": carried_decoders,
        "dispatches": dispatches,
    }
