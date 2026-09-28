"""Per-candidate query logic compiled once per Joern session (slicer performance).

Every per-candidate query used to be a self-contained Scala block, so the REPL compiled the
whole flows/context/backward logic on every request. Measured on zlib (local WSL, same CPG,
same inputs): a context query spent a median 0.646 s outside its body, and a trivial query
0.011 s, so the difference is compilation. Here the logic is defined once as functions of
one object, and each request compiles only a call carrying the candidate's data.

The function bodies are GENERATED from the self-contained builders (`flows_query`,
`context_query`, `backward_b_query`) by swapping placeholder values for parameters, so the
logic lives in one place and the builders stay the reference their tests pin. Each swap must
match exactly once, or construction fails.

Only functions live in the session; candidate data stays inside each call. The object name
carries a hash of its source, so a session holding a library built from other code or
settings is never reused -- a missing object fails the query as a compile error.
"""

import hashlib
import json

import slicer_queries as queries
from slicer_backward import backward_b_query

SCOPES = "scopes: List[(String, String, Int)]"
# Placeholders: values no real candidate carries, each appearing once in the generated text.
PLACEHOLDER_SCOPES = [{"file": "__LIB_FILE__", "full_name": "__LIB_FULL_NAME__", "line": 999004}]
PLACEHOLDER_POINTS = [("__LIB_POINT__", 0)]


def as_function(stage: str, text: str, name: str, params: str, swaps: list) -> str:
    """Turn a timed envelope query into `def name(params): String` (the timed JSON)."""
    for old, new in swaps:
        if text.count(old) != 1:
            raise ValueError(f"{stage}: placeholder {old!r} occurs {text.count(old)} times")
        text = text.replace(old, new)
    # Cut at the envelope's own seams, not at hard-coded text: reframing `envelope` moves the
    # cut with it instead of emitting Scala that only a live server rejects (#322 review).
    opening, closing = queries.envelope(stage, "\0", timed=True).split("\0")
    if not (text.startswith(opening) and text.endswith(closing)):
        raise ValueError(f"{stage}: not a timed envelope")
    body = text[len(opening) : len(text) - len(closing)]
    return (
        f"def {name}({params}): String = "
        f"{{ {queries.statements(body, timed=True)} ujson.write({queries.TIMED_RESULT}) }}"
    )


def scope_list(scopes: list[dict]) -> str:
    items = (
        f"({queries.literal(s['file'])}, {queries.literal(s['full_name'])}, {s['line']})"
        for s in scopes
    )
    return "List(" + ", ".join(items) + ")"


class Library:
    def __init__(
        self,
        repo: str,
        max_flows: int | None,
        limits: dict[str, int],
        max_depth: int | None,
        max_nodes: int | None,
        max_call_depth: int,
    ):
        # From method_selector itself, so a predicate edit reaches the library too (#322 review).
        scope_filter = queries.method_selector("scopes")
        flows = as_function(
            "flows",
            queries.flows_query(PLACEHOLDER_SCOPES, max_flows, limits),
            "flows",
            SCOPES,
            [(queries.method_selector(PLACEHOLDER_SCOPES), scope_filter)],
        )
        context = as_function(
            "context",
            queries.context_query(PLACEHOLDER_POINTS, repo),
            "context",
            "pointsJson: String",
            [(queries.joined_literal(json.dumps(PLACEHOLDER_POINTS, ensure_ascii=True)),
              "pointsJson")],
        )
        template = {
            "entry": {"symbol": "__LIB_SYMBOL__"},
            "scopes": PLACEHOLDER_SCOPES,
            "selector": {"line": 999001, "column": 999002, "code": "__LIB_CODE__",
                         "ast_path": [999003]},
        }
        backward = as_function(
            "backward",
            backward_b_query(template, max_depth=max_depth, max_nodes=max_nodes,
                             max_call_depth=max_call_depth),
            "backward",
            "symbol: String, line: Int, column: Int, code: String, path: List[Int], " + SCOPES,
            [
                (queries.literal("__LIB_SYMBOL__"), "symbol"),
                ("== 999001", "== line"),
                ("== 999002", "== column"),
                (queries.literal("__LIB_CODE__"), "code"),
                ("List(999003)", "path"),
                (queries.method_selector(PLACEHOLDER_SCOPES), scope_filter),
            ],
        )
        body = f"{flows}\n{context}\n{backward}"
        self.name = "SlicerLib_" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        # Top level, not inside a block: a block-local object would not outlive this request.
        self.definition = (
            f"object {self.name} {{ {body} }}\n"
            '"__SLICER_library__" + java.util.Base64.getEncoder.encodeToString('
            '"true".getBytes(java.nio.charset.StandardCharsets.UTF_8)) + "__END_SLICER__"'
        )

    def call(self, stage: str, arguments: str) -> str:
        return (
            f'{{ "__SLICER_{stage}__" + java.util.Base64.getEncoder.encodeToString('
            f"{self.name}.{stage}({arguments})"
            '.getBytes(java.nio.charset.StandardCharsets.UTF_8)) + "__END_SLICER__" }'
        )

    def flows(self, scopes: list[dict]) -> str:
        return self.call("flows", scope_list(scopes))

    def context(self, points: list) -> str:
        return self.call("context", queries.joined_literal(json.dumps(points, ensure_ascii=True)))

    def backward(self, candidate: dict) -> str:
        s = candidate["selector"]
        return self.call(
            "backward",
            f"{queries.literal(candidate['entry']['symbol'])}, {s['line']}, {s['column']}, "
            f"{queries.literal(s['code'])}, List({','.join(map(str, s['ast_path']))}), "
            + scope_list(candidate["scopes"]),
        )
