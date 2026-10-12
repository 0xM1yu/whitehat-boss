"""Select input-decoding functions connected to a slice value.

The first stage exports a small set of cross-method CPG edges.  This module
uses those edges and the existing tree-sitter index to follow relevant calls;
it does not classify functions by names such as ``parse_*``.  Syntax signals
are deliberately conservative: a candidate must read/advance a pointer and
test or convert what it reads.  The caller must occur on a dependency/anchor
line, or a selected decoder must pass it output state or share its input cursor.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable

from common import Location
from source_definitions import code_only

IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
KEYWORDS = frozenset({"if", "for", "while", "switch", "return", "sizeof", "defined"})
# These are syntax/standard-library hints, not names of project-specific parsers.
CHAR_TEST = re.compile(
    r"\b(?:is(?:alnum|alpha|digit|xdigit|space|punct)|strto(?:l|ul|ll|ull|d|f)|"
    r"atoi|atol|sscanf|scanf|memcmp|strncmp|"
    r"[A-Za-z_]\w*(?:digit|hex|alnum|char_class)\w*)\s*\(", re.I)
SOURCE_READ = re.compile(r"\b(?:fread|read|pread|recv|recvfrom|fgets|getline|getc|fgetc)\s*\(")
MAX_DEPTH = 3
DEFAULT_MAX_FUNCTIONS = 4
DEFAULT_MAX_CHARS = 50_000


def _assigned_result(line: str, callee: str) -> str | None:
    """Name receiving a call result, including `if ((value = call(...)))`."""
    before = re.split(rf"\b{re.escape(callee)}\s*\(", code_only(line), maxsplit=1)[0]
    match = re.search(r"\b([A-Za-z_]\w*)\s*=\s*$", before)
    return match.group(1) if match else None


def _result_is_checked(body: str, line_offset: int, name: str) -> bool:
    """The result controls acceptance or is passed on to the caller."""
    rows = code_only(body).split("\n")
    tail = "\n".join(rows[line_offset:])
    token = re.escape(name)
    return bool(re.search(rf"\b(?:if|while|switch|return)\b[^;{{}}]*\b{token}\b", tail)
                or re.search(rf"\breturn\s+{token}\b", tail))


def choose_input_contract(
    index: dict, graph, read_source: Callable[[Location], str],
    carried: list[Location], dependency: set[tuple[str, int]],
    anchors: set[tuple[str, int]], cross_method_flow: list[dict] | None = None,
    *, max_functions: int = 3, max_chars: int = DEFAULT_MAX_CHARS,
    already_full: set[tuple[str, int]] | None = None,
    diagnostics: dict | None = None,
    cpg_paths: dict[tuple[str, int], dict] | None = None,
) -> list[dict]:
    """Follow grammar-producing calls beside the sink's data-dependency path.

    A string/number decoder may determine whether input is accepted without
    contributing to the sink value.  Start from a retained input handler's
    checked call results, then follow returned values through short wrappers.
    Only chains ending in a function that reads the same raw input cursor are
    admitted.  This keeps unrelated helpers out of the small grammar budget.
    """
    if not index or not max_functions or not max_chars:
        return []
    raw_cursors = ({p["input_state"] for p in cpg_paths.values()}
                   if cpg_paths is not None else
                   _raw_cursor_globals(index, carried, read_source))
    if not raw_cursors:
        if diagnostics is not None:
            diagnostics["status"] = "no_retained_input_cursor"
        return []
    flow_lines = set()
    for edge in cross_method_flow or []:
        for side in ("from", "to"):
            point = edge.get(side, {})
            if isinstance(point.get("file"), str) and type(point.get("line")) is int:
                flow_lines.add((point["file"], point["line"]))
    seeds = dependency | anchors | flow_lines
    by_file: dict[str, list[tuple[str, dict]]] = {}
    already_full = already_full or set()

    def returned_chain(owner: Location, name: str, row: dict,
                       depth: int, visited: set[tuple[str, int]]) -> list[dict]:
        if depth > MAX_DEPTH:
            return []
        location, macro = _resolve(index, graph, read_source, name, owner.file)
        if location is None or _scope(location) in visited:
            return []
        body = read_source(location)
        score, cursors = _input_signals(body)
        proof = cpg_proof_for(location, cpg_paths) if cpg_paths is not None else None
        if cpg_paths is not None and proof is None:
            return []
        record = {"location": location, "score": score,
                  "chars": 0 if _scope(location) in already_full else len(body),
                  "call_file": row["file"], "call_line": row["line"],
                  "callee": name, "macro": macro, "depth": depth,
                  "input_cursors": sorted(cursors),
                  "joern_flow": (row["file"], row["line"]) in flow_lines,
                  "reason": "checked input result or returned decoder value"}
        if score >= 5 and (cursors & raw_cursors or
                           proof and proof["method_state"] in cursors):
            return [record]
        # A wrapper often checks/copies a local result while the nested
        # decoder alone advances the input cursor (e.g. a C-string validator).
        # Require that the nested result affects the wrapper's return/guard.
        for nested_name, nested_row in _calls_in(index, location, read_source, by_file):
            offset = nested_row["line"] - (location.line_start or 1)
            lines = code_only(body).split("\n")
            if not 0 <= offset < len(lines):
                continue
            local = _assigned_result(lines[offset], nested_name)
            if local is None or not _result_is_checked(body, offset, local):
                continue
            nested = returned_chain(location, nested_name, nested_row, depth + 1,
                                    visited | {_scope(location)})
            if nested:
                return [record, *nested]
        return []

    chains: list[tuple[int, list[dict]]] = []
    validators: list[tuple[int, list[dict]]] = []
    unresolved: list[dict] = []
    for owner in carried:
        owner_body = read_source(owner)
        _score, owner_cursors = _input_signals(owner_body)
        if not owner_cursors & raw_cursors or not any(
                _contains(owner, file, line) for file, line in seeds):
            continue
        rows = code_only(owner_body).split("\n")
        calls = _calls_in(index, owner, read_source, by_file)
        for name, row in calls:
            if (row["file"], row["line"]) in seeds:
                continue  # The ordinary dependency-path selector owns this call.
            offset = row["line"] - (owner.line_start or 1)
            if not 0 <= offset < len(rows):
                continue
            local = _assigned_result(rows[offset], name)
            if local is None or not _result_is_checked(owner_body, offset, local):
                continue
            chain = returned_chain(owner, name, row, 0, set())
            if chain:
                chains.append((row["line"], chain))
            else:
                unresolved.append({"file": row["file"], "line": row["line"],
                                   "callee": name})
        starts = [line for line, chain in chains
                  if chain[0]["call_file"] == owner.file
                  and _contains(owner, owner.file, line)]
        if starts:
            for name, row in calls:
                if row["line"] <= min(starts):
                    continue
                location, macro = _resolve(index, graph, read_source, name, owner.file)
                if location is None:
                    continue
                body = read_source(location)
                score, cursors = _input_signals(body)
                proof = cpg_proof_for(location, cpg_paths) if cpg_paths is not None else None
                if (location.line_end or 0) - (location.line_start or 0) + 1 > 160:
                    continue  # A broad expression parser is not a terminal grammar check.
                if score < 5 or not (cursors & raw_cursors or proof and
                                     proof["method_state"] in cursors):
                    continue
                if cpg_paths is not None:
                    if not proof or not proof["self_guard_lines"]:
                        continue
                elif not _source_input_guard(body, cursors & raw_cursors):
                    continue
                validators.append((row["line"], [{
                    "location": location, "score": score,
                    "chars": 0 if _scope(location) in already_full else len(body),
                    "call_file": row["file"], "call_line": row["line"],
                    "callee": name, "macro": macro, "depth": 0,
                    "input_cursors": sorted(cursors),
                    "joern_flow": (row["file"], row["line"]) in flow_lines,
                    "reason": "input acceptance or failure check",
                }]))
    # Source order is the parser's acceptance order.  Duplicated calls to the
    # same decoder (e.g. an optional second filename) need one body only.
    chains.sort(key=lambda item: (item[1][0]["call_file"], item[0]))
    validators.sort(key=lambda item: (item[1][0]["call_file"], item[0]))
    chosen: list[dict] = []
    seen = set(already_full)
    used_chars = 0
    skipped_budget = 0
    for _line, chain in chains + validators:
        pending = [record for record in chain if _scope(record["location"]) not in seen]
        cost = sum(record["chars"] for record in pending)
        if len(chosen) + len(pending) > max_functions or used_chars + cost > max_chars:
            skipped_budget += 1
            continue
        chosen.extend(pending)
        used_chars += cost
        seen.update(_scope(record["location"]) for record in pending)
    if diagnostics is not None:
        diagnostics.update(status="selected" if chosen else "no_grammar_chain",
                           raw_cursors=sorted(raw_cursors),
                           checked_result_chains=len(chains),
                           validation_calls=len(validators),
                           selected_functions=len(chosen),
                           skipped_budget=skipped_budget,
                           unresolved_checked_calls=unresolved[:20])
    return chosen


def choose_input_dispatch(index: dict, read_source: Callable[[Location], str],
                          handlers: set[str], *, max_rows: int = 4) -> list[dict]:
    """Find string-keyed function-table rows that connect input to handlers."""
    if not index or not handlers:
        return []
    candidates: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for table, definitions in index.get("file_vars", {}).items():
        for definition in definitions:
            start, end = definition.get("line_start"), definition.get("line_end")
            if not isinstance(start, int) or not isinstance(end, int) or end - start > 160:
                continue
            file = definition["file"]
            body = read_source(Location(file=file, line_start=start, line_end=end))
            guards: list[tuple[str, str | None]] = []
            for offset, line in enumerate(body.split("\n")):
                stripped = line.strip()
                if re.match(r"#\s*(?:if|ifdef|ifndef)\b", stripped):
                    guards.append((stripped, None))
                elif re.match(r"#\s*(?:else|elif)\b", stripped) and guards:
                    guards[-1] = (guards[-1][0], stripped)
                elif re.match(r"#\s*endif\b", stripped) and guards:
                    guards.pop()
                for handler in handlers:
                    match = re.search(rf'\{{\s*"([^"\n]+)"\s*,\s*{re.escape(handler)}\b', line)
                    if not match:
                        continue
                    number = start + offset
                    if (file, number) in seen:
                        continue
                    seen.add((file, number))
                    registration = next((call["line"]
                        for calls in index.get("calls", {}).values() for call in calls
                        if call["file"] == file and table in call.get("args", [])
                        and not start <= call["line"] <= end), None)
                    header = body.split("\n", 1)[0].strip()
                    evidence = f"/* {file}:{start} */ {header}\n"
                    for opening, branch in guards:
                        evidence += f"/* conditional */ {opening}\n"
                        if branch:
                            evidence += f"/* conditional */ {branch}\n"
                    evidence += f"/* {file}:{number} */ {line.strip()}"
                    if registration is not None:
                        use = read_source(Location(file=file, line_start=registration,
                                                   line_end=registration)).strip()
                        evidence += f"\n/* {file}:{registration} */ {use}"
                    candidates.append({"file": file, "line": number, "handler": handler,
                                "directive": match.group(1), "conditional": bool(guards),
                                "table": table, "registration_line": registration,
                                "source": evidence})
    candidates.sort(key=lambda item: (item["conditional"],
                                       item["registration_line"] is None,
                                       item["file"], item["line"]))
    out: list[dict] = []
    bound: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = candidate["handler"], candidate["directive"]
        if key in bound:
            continue
        bound.add(key)
        out.append(candidate)
        if len(out) >= max_rows:
            break
    return out


def _scope(location: Location) -> tuple[str, int]:
    return location.file, location.line_start or -1


def _source_input_guard(body: str, cursors: set[str]) -> bool:
    """Legacy JSON fallback: a conditional directly inspects the input state."""
    code = code_only(body)
    return any(re.search(rf"\b(?:if|while|for|switch)\s*\([^;{{}}]*"
                         rf"(?:\*\s*{re.escape(name)}\b|\b{re.escape(name)}\s*\[)", code)
               for name in cursors)


def cpg_input_paths(evidence: dict | None, index: dict,
                    carried: list[Location]) -> dict[tuple[str, int], dict]:
    """Link sink-retained input reads to called methods using exported CPG facts.

    A CPG global identifier may resolve to a synthetic LOCAL in each method,
    with a different node ID.  A repository-level declaration from the syntax
    index permits the same name to cross that boundary.  Parameters instead
    cross only through the CPG call's argument-to-parameter positions.
    """
    if not evidence or evidence.get("status") != "ok":
        return {}
    methods = {m["id"]: m for m in evidence.get("methods", [])
               if isinstance(m, dict) and isinstance(m.get("id"), str)}
    calls: dict[str, list[dict]] = {}
    for edge in evidence.get("calls", []):
        if edge.get("caller") in methods and edge.get("callee") in methods:
            calls.setdefault(edge["caller"], []).append(edge)
    for rows in calls.values():
        rows.sort(key=lambda e: (e["file"], e["line"], e["callee"]))
    globals_ = index.get("file_vars", {})

    def local_global(name: str, owner_file: str) -> bool:
        """Reject equal-named globals in unrelated repository subprojects."""
        directory = owner_file.rsplit("/", 1)[0]
        return any(row.get("file") == owner_file or
                   row.get("file", "").rsplit("/", 1)[0] == directory or
                   row.get("file", "").startswith(directory + "/")
                   for row in globals_.get(name, []))

    def retained(m: dict) -> bool:
        return bool(m.get("ddg_lines")) and any(
            loc.file == m["file"] and loc.symbol == m["name"]
            and loc.line_start is not None
            and abs(loc.line_start - m["line_start"]) <= 3
            for loc in carried)

    def reads(m: dict, name: str) -> list[dict]:
        return [r for r in m.get("reads", []) if r.get("name") == name]

    def guards(m: dict, name: str) -> list[int]:
        return [g["line"] for g in m.get("guards", [])
                if name in g.get("names", []) and type(g.get("line")) is int]

    proofs: dict[str, dict] = {}
    roots = [m for m in methods.values() if retained(m)]
    source_states: list[tuple[int, dict, str]] = []
    for root in roots:
        guarded = {name for g in root.get("guards", []) for name in g.get("names", [])}
        parameters = {p.get("name") for p in root.get("parameters", [])}
        for read in root.get("reads", []):
            name, typ = read.get("name"), read.get("type", "")
            if name not in guarded or not isinstance(name, str):
                continue
            if not ("char" in typ or "byte" in typ or "uint8" in typ):
                continue
            if name in parameters and ("*" in typ or "[" in typ):
                tier = 1  # caller-supplied input buffer
            elif local_global(name, root["file"]) and "*" in typ:
                tier = 0  # declared in the handler's source subsystem
            elif "*" in typ or "[" in typ:
                tier = 2  # local alias, when no stronger origin is visible
            else:
                continue
            source_states.append((tier, root, name))
    if source_states:
        best_tier = min(tier for tier, _root, _name in source_states)
        source_states = sorted({(tier, root["id"], name)
                                for tier, root, name in source_states if tier == best_tier})
    for _tier, root_id, state in source_states:
            root = methods[root_id]
            initial = (root["id"], state)
            queue = deque([initial])
            visited: set[tuple[str, str]] = set()
            parents: dict[tuple[str, str], tuple[tuple[str, str], dict] | None] = {
                initial: None}
            state_edges: dict[tuple[str, str], list[tuple[tuple[str, str], dict]]] = {}
            while queue:
                method_id, current = node = queue.popleft()
                if node in visited:
                    continue
                visited.add(node)
                depth = 0
                parent = parents[node]
                while parent is not None:
                    depth += 1
                    parent = parents[parent[0]]
                if depth >= 6:
                    continue
                for edge in calls.get(method_id, []):
                    target = methods[edge["callee"]]
                    next_states = set()
                    if _tier == 0 and current == state and all(
                            p.get("name") != state for p in target.get("parameters", [])):
                        next_states.add(current)
                    for arg in edge.get("args", []):
                        if current not in arg.get("names", []):
                            continue
                        next_states.update(p["name"] for p in target.get("parameters", [])
                                           if p.get("index") == arg.get("index")
                                           and p.get("name"))
                    for next_state in sorted(next_states):
                        child = (target["id"], next_state)
                        state_edges.setdefault(node, []).append((child, edge))
                        if child not in parents:
                            parents[child] = (node, edge)
                            queue.append(child)

            def terminal_path(node: tuple[str, str], active: set[tuple[str, str]]
                              ) -> list[tuple[str, str]] | None:
                if node in active:
                    return None
                method = methods[node[0]]
                # Numeric accumulation can read the cursor outside a guard.
                if reads(method, node[1]):
                    return [node]
                for child, _edge in state_edges.get(node, []):
                    continuation = terminal_path(child, active | {node})
                    if continuation:
                        return [node, *continuation]
                return None

            for node in visited:
                continuation = terminal_path(node, set())
                if not continuation or node[0] in proofs:
                    continue
                prefix = [node]
                path_calls = []
                parent = parents[node]
                while parent is not None:
                    prefix.append(parent[0])
                    path_calls.append(parent[1]["line"])
                    parent = parents[parent[0]]
                prefix.reverse()
                path_calls.reverse()
                method = methods[node[0]]
                terminal = methods[continuation[-1][0]]
                proofs[node[0]] = {
                    "input_state": state,
                    "root": {"file": root["file"], "symbol": root["name"],
                             "line": root["line_start"],
                             "ddg_lines": root.get("ddg_lines", [])},
                    "path": [{"file": methods[p]["file"],
                              "symbol": methods[p]["name"],
                              "line": methods[p]["line_start"]} for p, _ in prefix],
                    "call_lines": path_calls,
                    "terminal": {"file": terminal["file"],
                                 "symbol": terminal["name"],
                                 "line": terminal["line_start"],
                                 "read_lines": sorted({r["line"] for r in reads(
                                     terminal, continuation[-1][1])})},
                    "self_guard_lines": guards(method, node[1]),
                    "method_state": node[1],
                    "source": "cpg_ddg_call_control",
                }
    by_scope: dict[tuple[str, int], dict] = {}
    for method_id, proof in proofs.items():
        method = methods[method_id]
        by_scope[(method["file"], method["line_start"])] = proof
    return by_scope


def cpg_proof_for(location: Location,
                  paths: dict[tuple[str, int], dict]) -> dict | None:
    """Look up a CPG path even if syntax and CPG disagree on a return-type line."""
    exact = paths.get(_scope(location))
    if exact is not None:
        return exact
    options = [proof for (file, line), proof in paths.items()
               if file == location.file and abs(line - (location.line_start or -100)) <= 3
               and proof["path"][-1]["symbol"] == location.symbol]
    return options[0] if len(options) == 1 else None


def _contains(location: Location, file: str, line: int) -> bool:
    return location.file == file and (location.line_start or 0) <= line <= (location.line_end or 0)


def _lexical_function(read_source: Callable[[Location], str], file: str, name: str) -> Location | None:
    """Recover a definition missed by tree-sitter in a macro-heavy C file."""
    code = code_only(read_source(Location(file=file)))
    match = re.search(
        rf"^[^\S\n]*(?:[A-Za-z_][\w\s\*]*?\s|\*)?\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        code, re.M,
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
                return Location(file=file, symbol=name,
                                line_start=code.count("\n", 0, match.start()) + 1,
                                line_end=code.count("\n", 0, offset) + 1)
    return None


def _resolve(index: dict, graph, read_source: Callable[[Location], str], name: str,
             owner_file: str) -> tuple[Location | None, str | None]:
    """Resolve a direct call or a function-like macro with one defined target."""
    original = name
    if name not in index.get("functions", {}):
        targets: set[str] = set()
        for macro in index.get("macros", {}).get(name, []):
            body = code_only(read_source(Location(file=macro["file"],
                                                  line_start=macro["line_start"],
                                                  line_end=macro["line_end"])))
            body = body.replace("\\\n", " ")
            # Do not read calls from the macro parameter list as callees.
            body = re.sub(r"^\s*#\s*define\s+\w+\s*(?:\([^)]*\))?", "", body, count=1)
            targets |= {n for n in CALL.findall(body) if n in index.get("functions", {})}
        if len(targets) == 1:
            name = next(iter(targets))
    candidates = graph.functions_named([name], owner_file) or []
    candidates = [c for c in candidates if c.symbol and c.line_start is not None]
    def rank(c: Location) -> tuple[bool, bool]:
        return (c.file != owner_file,
                c.file.rsplit("/", 1)[0] != owner_file.rsplit("/", 1)[0])
    candidates.sort(key=lambda c: (*rank(c), c.file, c.line_start or 0))
    if candidates:
        # A same-name overload or another translation unit at the same rank
        # cannot be identified from a name-only call record.  Do not promote an
        # arbitrary body in that case.
        if sum(rank(c) == rank(candidates[0]) for c in candidates) > 1:
            return None, None
        return _repair_function_end(index, read_source, candidates[0]), original if name != original else None
    # A syntax error elsewhere in a C file can make tree-sitter miss a whole
    # function.  Recover a unique definition in the caller's file or its
    # directory; a name-only match in multiple files is intentionally unsafe.
    local = _lexical_function(read_source, owner_file, name)
    if local is not None:
        return local, original if name != original else None
    directory = owner_file.rsplit("/", 1)[0] + "/"
    neighbors = list(dict.fromkeys(
        row["file"] for row in index.get("calls", {}).get(name, [])
        if row["file"].startswith(directory) and row["file"] != owner_file))
    neighbors += [file for file in index.get("files", [])
                  if file.startswith(directory) and file.endswith((".c", ".cc", ".cpp"))
                  and file != owner_file and file not in neighbors]
    matches = [_lexical_function(read_source, file, name) for file in neighbors[:80]]
    unique = [location for location in matches if location is not None]
    return (unique[0] if len(unique) == 1 else None), original if name != original else None


def _repair_function_end(index: dict, read_source: Callable[[Location], str],
                         location: Location) -> Location:
    """Extend an index span ending on an indented brace inside a C function.

    Preprocessor alternatives can confuse tree-sitter's end position.  A real
    file-scope C function normally closes with a brace in column zero.  Stop
    at the next indexed function so an unrelated definition cannot be pulled
    into this body.  Ambiguous cases retain the indexed span.
    """
    if location.line_start is None or location.line_end is None:
        return location
    rows = code_only(read_source(Location(file=location.file))).split("\n")
    if location.line_end > len(rows):
        return location
    if re.fullmatch(r"\s*}\s*", rows[location.line_end - 1]) is None:
        return location
    if re.fullmatch(r"}\s*", rows[location.line_end - 1]):
        return location
    following = [row["line_start"] for definitions in index.get("functions", {}).values()
                 for row in definitions if row.get("file") == location.file
                 and isinstance(row.get("line_start"), int)
                 and row["line_start"] > location.line_start]
    stop = min(following, default=min(len(rows) + 1, location.line_end + 1000))
    for number in range(location.line_end + 1, min(stop, len(rows) + 1)):
        if re.fullmatch(r"}\s*", rows[number - 1]):
            return Location(file=location.file, symbol=location.symbol,
                            line_start=location.line_start, line_end=number)
    return location


def _input_signals(code: str) -> tuple[int, set[str]]:
    """Return a structural decoding score and the pointer/cursor names it reads."""
    body = code_only(code)
    reads = set(re.findall(r"\*\s*([A-Za-z_]\w*)\s*(?:\+\+|--)?", body))
    reads |= set(re.findall(r"\b([A-Za-z_]\w*)\s*\[[^\]]+\]", body))
    moves = {name for name in reads if re.search(
        rf"(?:\b{re.escape(name)}\s*(?:\+\+|--|[+\-]=|=(?!=))|"
        rf"(?:\+\+|--)\s*{re.escape(name)}\b)", body)}
    # A buffer supplied as a parameter can be indexed without moving a cursor.
    checked = {name for name in reads if re.search(
        rf"\b(?:if|while|for|switch)\s*\([^;]*\b{re.escape(name)}\b", body)}
    cursors = moves | checked
    if not reads and not SOURCE_READ.search(body) and not CHAR_TEST.search(body):
        return 0, set()
    tests = len(re.findall(r"\b(?:if|while|for|switch)\s*\(", body))
    literals = len(re.findall(r"(?:==|!=|<=|>=|<|>)\s*(?:'[^']'|\d+)", body))
    score = 2 * bool(reads) + 2 * bool(moves) + bool(checked) + bool(tests) + \
        min(2, literals) + 2 * bool(CHAR_TEST.search(body)) + 2 * bool(SOURCE_READ.search(body))
    if re.search(r"\breturn\b|(?:->|\.)\s*\w+\s*=(?!=)", body):
        score += 1
    return score, cursors


def _calls_in(index: dict, location: Location, read_source: Callable[[Location], str],
              by_file: dict[str, list[tuple[str, dict]]]) -> list[tuple[str, dict]]:
    # The index is keyed by *callee*, not caller.  Cache the files actually
    # encountered so several decoder functions in one file do not rescan the
    # whole repository's call table for each function.
    if location.file not in by_file:
        by_file[location.file] = [(name, row)
                                  for name, rows in index.get("calls", {}).items() for row in rows
                                  if row["file"] == location.file]
    out = [(name, row) for name, row in by_file[location.file]
           if _contains(location, row["file"], row["line"])]
    if not out and location.line_start is not None:
        # A broken parse can omit the *entire* caller from the index.  In that
        # case its calls are absent too, even when their targets are indexed.
        # Recover only calls to known functions in this one bounded body.
        body = code_only(read_source(location))
        known = set(index.get("functions", {}))
        for offset, line in enumerate(body.splitlines()):
            for name in CALL.findall(line):
                if name in known and name not in KEYWORDS:
                    out.append((name, {"file": location.file,
                                       "line": location.line_start + offset,
                                       "caller": location.symbol,
                                       "args": [line]}))
    return sorted(out, key=lambda pair: (pair[1]["line"], pair[0]))


def _raw_cursor_globals(index: dict, carried: list[Location],
                        read_source: Callable[[Location], str]) -> set[str]:
    """Global cursors used to inspect input characters in the retained functions."""
    globals_ = set(index.get("file_vars", {}))
    found = set()
    tokens = re.compile(
        r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
        re.S,
    )
    character_test = re.compile(
        r"(?:\*\s*([A-Za-z_]\w*)|([A-Za-z_]\w*)\s*\[[^\]\n]+\])"
        r"\s*(?:==|!=|<=|>=|<|>)\s*(?:'[^']*'|0\b)"
    )
    for owner in carried:
        body = tokens.sub(
            lambda match: re.sub(r"[^\n]", " ", match.group())
            if match.group().startswith("/") else match.group(),
            read_source(owner),
        )
        for match in character_test.finditer(body):
            name = match.group(1) or match.group(2)
            if name in globals_:
                found.add(name)
    return found


def choose_input_functions(
    index: dict, graph, read_source: Callable[[Location], str],
    carried: list[Location], dependency: set[tuple[str, int]],
    anchors: set[tuple[str, int]], cross_method_flow: list[dict] | None = None,
    *, max_functions: int = DEFAULT_MAX_FUNCTIONS,
    max_chars: int = DEFAULT_MAX_CHARS,
    already_full: set[tuple[str, int]] | None = None,
    cpg_paths: dict[tuple[str, int], dict] | None = None,
) -> list[dict]:
    """Choose bounded input-decoder context with auditable reasons.

    ``cross_method_flow`` is exported from the live Joern CPG by stage one.
    Old slice documents still work through their data-dependency lines.
    """
    if not index or not max_functions or not max_chars:
        return []
    flow_lines = set()
    for edge in cross_method_flow or []:
        for side in ("from", "to"):
            point = edge.get(side, {})
            if isinstance(point.get("file"), str) and type(point.get("line")) is int:
                flow_lines.add((point["file"], point["line"]))
    seeds = set(dependency) | set(anchors) | flow_lines
    raw_cursors = ({p["input_state"] for p in cpg_paths.values()}
                   if cpg_paths is not None else
                   _raw_cursor_globals(index, carried, read_source))
    by_file: dict[str, list[tuple[str, dict]]] = {}
    roots = []
    for owner in carried:
        for name, row in _calls_in(index, owner, read_source, by_file):
            if (row["file"], row["line"]) in seeds:
                location, macro = _resolve(index, graph, read_source, name, owner.file)
                if location is None:
                    continue
                body = read_source(location)
                score, cursors = _input_signals(body)
                roots.append((owner, name, row, 0, None,
                              (location, macro, body, score, cursors)))
    # A decoder sharing the retained raw-input cursor leads the queue.  This
    # prevents filename/path helpers on the same slice from consuming the small
    # parser budget before the numeric or string decoder is traversed.
    roots.sort(key=lambda item: (
        not bool(item[5][4] & raw_cursors),
        (item[2]["file"], item[2]["line"]) not in flow_lines,
        -item[5][3], item[2]["file"], item[2]["line"], item[1],
    ))
    remaining_roots = deque(roots)
    queue = deque()
    result: list[dict] = []
    visited: set[tuple[str, int]] = set()
    attempted: set[tuple[tuple[str, int], tuple[str, ...], tuple[str, ...]]] = set()
    total_chars = 0
    already_full = already_full or set()
    while (queue or remaining_roots) and len(result) < max_functions:
        if not queue:
            queue.append(remaining_roots.popleft())
        owner, name, row, depth, parent, cached = queue.popleft()
        if cached is None:
            location, macro = _resolve(index, graph, read_source, name, owner.file)
            if location is None:
                continue
            body = read_source(location)
            score, cursors = _input_signals(body)
        else:
            location, macro, body, score, cursors = cached
        if location is None or _scope(location) in visited:
            continue
        proof = cpg_proof_for(location, cpg_paths) if cpg_paths is not None else None
        if cpg_paths is not None and proof is None:
            continue
        attempt = (_scope(location), tuple(sorted(parent["cursors"])) if parent else (),
                   tuple(sorted(parent["params"])) if parent else ())
        if attempt in attempted:
            continue
        attempted.add(attempt)
        if score < 5 or not cursors:
            continue
        if parent is not None:
            parent_cursors = parent["cursors"]
            args = " ".join(row.get("args", []))
            # A nested decoder must share an input cursor, or receive the
            # parent decoder's value/object through an argument.
            parent_params = set(parent["params"])
            shared_cursor = bool(cursors & parent_cursors)
            value_argument = bool(set(IDENT.findall(args)) & parent_params)
            if not (proof or shared_cursor or value_argument and
                    (CHAR_TEST.search(code_only(body)) or SOURCE_READ.search(code_only(body)))):
                continue
        visited.add(_scope(location))
        added_chars = 0 if _scope(location) in already_full else len(body)
        if total_chars + added_chars > max_chars:
            continue
        record = {
            "location": location, "score": score, "chars": added_chars,
            "call_file": row["file"], "call_line": row["line"],
            "callee": name, "macro": macro, "depth": depth,
            "input_cursors": sorted(cursors),
            "joern_flow": (row["file"], row["line"]) in flow_lines,
        }
        if _scope(location) not in already_full:
            result.append(record)
            total_chars += added_chars
        head = code_only(body).split("{", 1)[0]
        params = set(IDENT.findall(head[head.find("(") + 1: head.rfind(")")])) if "(" in head else set()
        parent_info = {"cursors": cursors, "params": params}
        if depth < MAX_DEPTH:
            nested = _calls_in(index, location, read_source, by_file)
            for nested_name, nested_row in nested:
                if nested_name != name and nested_name not in KEYWORDS:
                    queue.append((location, nested_name, nested_row, depth + 1,
                                  parent_info, None))
    return result
