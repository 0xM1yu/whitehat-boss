"""Link a retained input read to the methods that continue reading it, from exported CPG facts.

`input_evidence` exports, per method, the pointers it reads, the conditions that test such a read
and the resolved call edges with their argument names. `cpg_input_paths` turns that into proofs:
"this method reads the same input cursor that a retained handler tests, through calls from it".
`input_context` admits only functions that have such a proof when the evidence is available.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from input_text import Location

# Call hops walked from a retained handler to find the methods that continue reading its cursor.
# The PR #638 author's choice; unmeasured (no corpus, no sweep). Reaching it sets
# `diagnostics["path_depth_cap_reached"]`.
MAX_PATH_DEPTH = 6
# A parsed type name is taken as a byte/char buffer when it names one of these. Syntax hint, not
# measured against a corpus.
BYTE_TYPE_HINTS = ("char", "byte", "uint8")


def _terminal_search(
    state_edges: dict[tuple[str, str], list[tuple[tuple[str, str], dict]]],
    reads_state: Callable[[tuple[str, str]], bool],
) -> Callable[
    [tuple[str, str], frozenset[tuple[str, str]]], tuple[list[tuple[str, str]] | None, bool]
]:
    """A search for a path "node -> ... -> a node that reads the state" over the call edges,
    simple (no node twice). The first path in edge order wins, as before the memo.

    Without a memo the search re-walks every dead end once per way of reaching it (a layered graph
    of N nodes and fan-out 4 took 1.2 s at N=36 and over a minute at N=50 with fan-out 5 -- the PR
    #638 review's synthetic reproduction), so:
    - a failure is cached only when no branch below it was cut by `active` (a cut result depends
      on the caller's path, a clean one does not);
    - a success is cached only when no branch below it was cut either, and reused only while none
      of its nodes is on the current path.
    ponytail: a graph whose dead ends are all cycle cuts is still walked in full; a reachability
    prefilter is the upgrade if a real index produces one.
    """
    dead: set[tuple[str, str]] = set()
    found: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def terminal_path(
        node: tuple[str, str], active: frozenset[tuple[str, str]]
    ) -> tuple[list[tuple[str, str]] | None, bool]:
        if node in active:
            return None, True
        if node in dead:
            return None, False
        cached = found.get(node)
        if cached is not None and not active.intersection(cached):
            return cached, False
        # Numeric accumulation can read the cursor outside a guard.
        if reads_state(node):
            return [node], False
        cut = False
        for child, _edge in state_edges.get(node, []):
            continuation, child_cut = terminal_path(child, active | {node})
            if continuation:
                path = [node, *continuation]
                cut = cut or child_cut
                if not cut:  # a branch cut by `active` could win on another path
                    found[node] = path
                return path, cut
            cut = cut or child_cut
        if not cut:
            dead.add(node)
        return None, cut

    return terminal_path


def cpg_input_paths(
    evidence: dict | None,
    index: dict,
    carried: list[Location],
    diagnostics: dict | None = None,
) -> dict[tuple[str, int], dict]:
    """Link sink-retained input reads to called methods using exported CPG facts.

    A CPG global identifier may resolve to a synthetic LOCAL in each method, with a different
    node ID. A repository-level declaration from the syntax index permits the same name to cross
    that boundary. Parameters instead cross only through the CPG call's argument-to-parameter
    positions.
    """
    if not evidence or evidence.get("status") != "ok":
        return {}
    methods = {
        m["id"]: m
        for m in evidence.get("methods", [])
        if isinstance(m, dict) and isinstance(m.get("id"), str)
    }
    calls: dict[str, list[dict]] = {}
    for edge in evidence.get("calls", []):
        if edge.get("caller") in methods and edge.get("callee") in methods:
            calls.setdefault(edge["caller"], []).append(edge)
    for rows in calls.values():
        rows.sort(key=lambda e: (e["file"], e["line"], e["callee"]))
    globals_ = index.get("file_vars", {})
    depth_cap_reached = False

    def local_global(name: str, owner_file: str) -> bool:
        """Reject equal-named globals in unrelated repository subprojects."""
        directory = owner_file.rsplit("/", 1)[0]
        return any(
            row.get("file") == owner_file
            or row.get("file", "").rsplit("/", 1)[0] == directory
            or row.get("file", "").startswith(directory + "/")
            for row in globals_.get(name, [])
        )

    def retained(m: dict) -> bool:
        return bool(m.get("ddg_lines")) and any(
            loc.file == m["file"]
            and loc.symbol == m["name"]
            and loc.line_start is not None
            and abs(loc.line_start - m["line_start"]) <= 3
            for loc in carried
        )

    def reads(m: dict, name: str) -> list[dict]:
        return [r for r in m.get("reads", []) if r.get("name") == name]

    def guards(m: dict, name: str) -> list[int]:
        return [
            g["line"]
            for g in m.get("guards", [])
            if name in g.get("names", []) and type(g.get("line")) is int
        ]

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
            if not any(hint in typ for hint in BYTE_TYPE_HINTS):
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
        source_states = sorted(
            {(tier, root["id"], name) for tier, root, name in source_states if tier == best_tier}
        )
    for tier, root_id, state in source_states:
        root = methods[root_id]
        initial = (root["id"], state)
        queue = deque([initial])
        visited: set[tuple[str, str]] = set()
        parents: dict[tuple[str, str], tuple[tuple[str, str], dict] | None] = {initial: None}
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
            if depth >= MAX_PATH_DEPTH:
                depth_cap_reached = depth_cap_reached or bool(calls.get(method_id))
                continue
            for edge in calls.get(method_id, []):
                target = methods[edge["callee"]]
                next_states = set()
                if (
                    tier == 0
                    and current == state
                    and all(p.get("name") != state for p in target.get("parameters", []))
                ):
                    next_states.add(current)
                for arg in edge.get("args", []):
                    if current not in arg.get("names", []):
                        continue
                    next_states.update(
                        p["name"]
                        for p in target.get("parameters", [])
                        if p.get("index") == arg.get("index") and p.get("name")
                    )
                for next_state in sorted(next_states):
                    child = (target["id"], next_state)
                    state_edges.setdefault(node, []).append((child, edge))
                    if child not in parents:
                        parents[child] = (node, edge)
                        queue.append(child)

        terminal_path = _terminal_search(
            state_edges, lambda node: bool(reads(methods[node[0]], node[1]))
        )

        for node in sorted(visited):
            continuation, _cut = terminal_path(node, frozenset())
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
                "root": {
                    "file": root["file"],
                    "symbol": root["name"],
                    "line": root["line_start"],
                    "ddg_lines": root.get("ddg_lines", []),
                },
                "path": [
                    {
                        "file": methods[p]["file"],
                        "symbol": methods[p]["name"],
                        "line": methods[p]["line_start"],
                    }
                    for p, _ in prefix
                ],
                "call_lines": path_calls,
                "terminal": {
                    "file": terminal["file"],
                    "symbol": terminal["name"],
                    "line": terminal["line_start"],
                    "read_lines": sorted({r["line"] for r in reads(terminal, continuation[-1][1])}),
                },
                "self_guard_lines": guards(method, node[1]),
                "method_state": node[1],
                "source": "cpg_ddg_call_control",
            }
    if diagnostics is not None:
        diagnostics["path_depth_cap_reached"] = depth_cap_reached
    by_scope: dict[tuple[str, int], dict] = {}
    for method_id, proof in proofs.items():
        method = methods[method_id]
        by_scope[(method["file"], method["line_start"])] = proof
    return by_scope


def cpg_proof_for(location: Location, paths: dict[tuple[str, int], dict]) -> dict | None:
    """Look up a CPG path even if syntax and CPG disagree on a return-type line."""
    exact = paths.get((location.file, location.line_start or -1))
    if exact is not None:
        return exact
    options = [
        proof
        for (file, line), proof in paths.items()
        if file == location.file
        and abs(line - (location.line_start or -100)) <= 3
        and proof["path"][-1]["symbol"] == location.symbol
    ]
    return options[0] if len(options) == 1 else None
