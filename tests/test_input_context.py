"""tools/slicer_v2 input_context / input_evidence / input_paths -- offline.

No Joern here. The CPG side is a hand-written evidence document in the shape `input_evidence`
exports (so what these tests show is the Python selection over such a document, not that the Scala
query produces it). The syntax side is real: `tests/fixtures/input_context/*.c` are verbatim
excerpts of libmagic's apprentice.c (see their headers) and the index is built by the harness's
own `tools/harness-tools/indexer.py` when tree-sitter is installed.
"""

import importlib.util
import json
import random
import re
import time
from pathlib import Path

import pytest

import input_context as ic
import input_evidence as ie
import input_paths as ip
import input_text as it
from src.orchestrator import source_definitions as harness_text
from src.schemas.common import Location as HarnessLocation

FIXTURES = Path(__file__).parent / "fixtures" / "input_context"
EXCERPT = FIXTURES / "apprentice_excerpt.c"
FRAGMENT = FIXTURES / "apprentice_read_fragment.c"
INDEXER = Path(__file__).parents[1] / "tools" / "harness-tools" / "indexer.py"


def fixture_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split("\n")


def line_with(path: Path, needle: str) -> tuple[int, str]:
    """(1-based line number, text) of the first fixture line containing `needle`."""
    for number, text in enumerate(fixture_lines(path), 1):
        if needle in text:
            return number, text
    raise AssertionError(f"{needle!r} not in {path.name}")


@pytest.fixture(scope="module")
def index() -> dict:
    pytest.importorskip("tree_sitter")
    spec = importlib.util.spec_from_file_location("harness_indexer", INDEXER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_index([EXCERPT], FIXTURES)


@pytest.fixture
def source() -> ic.Source:
    return ic.Source({"apprentice_excerpt.c": fixture_lines(EXCERPT)})


# --- the two harness definitions input_text restates -----------------------------------------


def test_code_only_is_the_harness_code_only():
    samples = [
        EXCERPT.read_text(encoding="utf-8"),
        FRAGMENT.read_text(encoding="utf-8"),
        "int a = 1'000; /* c */ char c = '\\''; // x \\\n y\nreturn 0;",
        "/* never closed",
    ]
    assert it._NOT_CODE.pattern == harness_text._NOT_CODE.pattern
    assert it._NOT_CODE.flags == harness_text._NOT_CODE.flags
    for text in samples:
        assert it.code_only(text) == harness_text.code_only(text)


def test_location_has_the_harness_fields():
    assert [f for f in it.Location.__dataclass_fields__] == list(HarnessLocation.model_fields)


def test_lines_are_counted_on_newline_only():
    # A form feed is inside a line for Joern; splitlines() would cut there and shift every
    # later line number (#259).
    text = "a\x0cb\nc"
    assert it.lines_of(text) == ["a\x0cb", "c"]
    assert len(text.splitlines()) == 3


# --- regex pins: positives and negatives, all from the real excerpts ---------------------------


@pytest.mark.parametrize("needle", ["isdigit(", "strtoull("])
def test_char_test_matches_real_decoder_calls(needle):
    _, text = line_with(EXCERPT, needle)
    assert ic.CHAR_TEST.search(text)


@pytest.mark.parametrize(
    "needle",
    [
        "eatsize(&l);",  # a call that is not a character test
        "file_signextend(ms, m, val);",
        "isascii(",  # real: not one of the tested classes
        "return -1;",
    ],
)
def test_char_test_rejects_real_non_decoder_lines(needle):
    _, text = line_with(EXCERPT, needle)
    assert not ic.CHAR_TEST.search(text)


def test_char_test_name_rule_needs_a_prefix_before_the_word():
    # The `...digit|hex|alnum|char_class...` rule wants at least one character before the word, so
    # the real `hextoint(` is NOT a hit (and neither is `hexdump(`, which the PR #638 review named
    # as an over-match -- checked: it does not match). `is_hex(` and `parse_digits(` do. Pinned as
    # the behaviour it is; whether the rule is too broad or too narrow needs a measurement this
    # PR does not have.
    _, real = line_with(EXCERPT, "hextoint(")
    assert not ic.CHAR_TEST.search(real)
    assert not ic.CHAR_TEST.search("hexdump(buf)")
    assert ic.CHAR_TEST.search("is_hex(c)")
    assert ic.CHAR_TEST.search("parse_digits(p)")


def test_source_read_matches_the_syscall_not_the_close():
    _, read = line_with(FRAGMENT, "read(fd")
    _, close = line_with(FRAGMENT, "close(fd)")
    _, badread = line_with(FRAGMENT, "file_badread(")
    assert ic.SOURCE_READ.search(read)
    assert not ic.SOURCE_READ.search(close)
    assert not ic.SOURCE_READ.search(badread)  # `read` inside another name


def test_input_signals_on_real_functions(index, source):
    functions = index["functions"]
    eatsize = functions["eatsize"][0]
    body = source.read(
        ic.Location(
            file="apprentice_excerpt.c",
            line_start=eatsize["line_start"],
            line_end=eatsize["line_end"],
        )
    )
    score, cursors = ic._input_signals(body)
    assert score >= ic.MIN_SCORE
    assert {"l", "p"} <= cursors
    assert ic._input_signals("") == (0, set())


# --- selection over the real index ------------------------------------------------------------


def eatsize_call_line() -> int:
    return line_with(EXCERPT, "eatsize(&l);")[0]


def hextoint_line() -> int:
    return line_with(EXCERPT, "if (isdigit(")[0]


def run(index, source, evidence=None, *, lines, **options) -> dict:
    return ic.build_input_context(
        evidence,
        index,
        source,
        dependency={("apprentice_excerpt.c", n) for n in lines},
        anchors=set(),
        **options,
    )


def test_syntax_mode_selects_the_decoder_called_from_the_slice(index, source):
    out = run(index, source, lines=[eatsize_call_line()])
    assert out["basis"] == "syntax" and out["evidence_status"] == "unavailable"
    assert out["status"] == "selected"
    assert [(f["symbol"], f["kind"]) for f in out["functions"]] == [("eatsize", "decoder")]
    assert out["carried"][0]["symbol"] == "parse_op_modifier"


def test_identical_to_auditor_input_pair(index, source):
    # hextoint calls nothing: the selection adds nothing to the slice's own function.
    same = run(index, source, lines=[hextoint_line()])
    assert same["status"] == "none_selected"
    assert same["identical_to_auditor_input"] is True
    more = run(index, source, lines=[eatsize_call_line()])
    assert more["identical_to_auditor_input"] is False


def test_cap_hit_pair(index, source):
    free = run(index, source, lines=[eatsize_call_line()])
    assert free["cap_hit"] is False and free["cap_reasons"] == []
    # One character of budget: the decoder is found and then skipped for size.
    capped = run(index, source, lines=[eatsize_call_line()], max_chars=1)
    assert capped["functions"] == []
    assert capped["cap_hit"] is True and capped["cap_reasons"] == ["max_chars"]
    # An empty selection with a cap hit reads as identical -- cap_hit says not to trust that.
    assert capped["identical_to_auditor_input"] is True


def test_the_object_is_deterministic(index, source):
    a = run(index, source, lines=[eatsize_call_line(), hextoint_line()])
    b = run(index, source, lines=[hextoint_line(), eatsize_call_line()])
    assert json.dumps(a) == json.dumps(b)


def test_caps_are_reported_as_unmeasured(index, source):
    out = run(index, source, lines=[eatsize_call_line()])
    assert out["caps_measured"] is False
    assert out["caps"]["max_functions"] == ic.DEFAULT_MAX_FUNCTIONS


def evidence_for(index: dict) -> dict:
    """What `input_evidence` would export for parse_op_modifier -> eatsize (hand-written)."""
    fn = {name: rows[0] for name, rows in index["functions"].items()}
    return {
        "version": 1,
        "status": "ok",
        "root_count": 1,
        "method_count": 2,
        "truncated": False,
        "truncation_reasons": [],
        "methods": [
            {
                "id": "1",
                "file": "apprentice_excerpt.c",
                "name": "parse_op_modifier",
                "full_name": "parse_op_modifier",
                "line_start": fn["parse_op_modifier"]["line_start"],
                "line_end": fn["parse_op_modifier"]["line_end"],
                "depth": 0,
                "parameters": [{"index": 3, "name": "lp"}],
                "ddg_lines": [eatsize_call_line()],
                "reads": [{"name": "l", "line": eatsize_call_line(), "type": "const char *"}],
                "reads_truncated": False,
                "guards": [{"line": eatsize_call_line(), "names": ["l"], "kind": "IF"}],
                "guards_truncated": False,
            },
            {
                "id": "2",
                "file": "apprentice_excerpt.c",
                "name": "eatsize",
                "full_name": "eatsize",
                "line_start": fn["eatsize"]["line_start"],
                "line_end": fn["eatsize"]["line_end"],
                "depth": 1,
                "parameters": [{"index": 1, "name": "p"}],
                "ddg_lines": [],
                "reads": [
                    {"name": "p", "line": fn["eatsize"]["line_start"] + 2, "type": "const char **"}
                ],
                "reads_truncated": False,
                "guards": [],
                "guards_truncated": False,
            },
        ],
        "calls": [
            {
                "caller": "1",
                "callee": "2",
                "file": "apprentice_excerpt.c",
                "line": eatsize_call_line(),
                "code": "eatsize(&l)",
                "args": [{"index": 1, "names": ["l"]}],
            }
        ],
    }


def test_cpg_mode_links_the_decoder_through_the_call(index, source):
    evidence = evidence_for(index)
    assert ie.valid_input_evidence(evidence)
    out = run(index, source, evidence, lines=[eatsize_call_line()])
    assert out["basis"] == "cpg" and out["evidence_status"] == "ok"
    assert [f["symbol"] for f in out["functions"]] == ["eatsize"]
    proof = out["functions"][0]["cpg"]
    assert proof["source"] == "cpg_ddg_call_control"
    assert [p["symbol"] for p in proof["path"]] == ["parse_op_modifier", "eatsize"]
    assert out["input_states"] == ["l"]


def test_truncated_evidence_is_a_cap_hit(index, source):
    evidence = evidence_for(index) | {"truncated": True, "truncation_reasons": ["methods"]}
    out = run(index, source, evidence, lines=[eatsize_call_line()])
    assert out["cap_hit"] is True and "evidence" in out["cap_reasons"]


def test_a_failed_evidence_query_falls_back_to_the_syntax_index(index, source):
    failed = {"version": 1, "status": "query_failed", "error": "boom"}
    out = run(index, source, failed, lines=[eatsize_call_line()])
    assert out["basis"] == "syntax" and out["evidence_status"] == "query_failed"


def test_an_unreadable_file_is_reported_not_swallowed(index):
    out = run(index, ic.Source({}), lines=[eatsize_call_line()])
    assert "apprentice_excerpt.c" in out["unreadable_files"]


# --- terminal_path memo ------------------------------------------------------------------------


def naive_terminal_path(state_edges, reads_state, node, active):
    """The search as it was before the memo (PR #638 attach/input_context.py)."""
    if node in active:
        return None
    if reads_state(node):
        return [node]
    for child, _edge in state_edges.get(node, []):
        continuation = naive_terminal_path(state_edges, reads_state, child, active | {node})
        if continuation:
            return [node, *continuation]
    return None


def test_the_memoized_search_equals_the_naive_one_on_random_graphs():
    rng = random.Random(638)
    for _ in range(300):
        nodes = [(str(i), "s") for i in range(rng.randint(2, 9))]
        edges = {
            n: [(rng.choice(nodes), {}) for _ in range(rng.randint(0, 3))] for n in nodes
        }  # cycles and self loops included
        terminals = {n for n in nodes if rng.random() < 0.25}
        search = ip._terminal_search(edges, terminals.__contains__)
        for node in nodes:
            got, _cut = search(node, frozenset())
            assert got == naive_terminal_path(edges, terminals.__contains__, node, frozenset())


def test_the_search_is_not_exponential_on_a_layered_dead_end_graph():
    # 10 layers of 5 nodes, every node linked to every node of the next layer, no terminal: 5**10
    # paths for the naive search (it took over a minute at 50 nodes with fan-out 5 in the review).
    layers = [[(f"{i}.{j}", "s") for j in range(5)] for i in range(10)]
    edges = {n: [(m, {}) for m in layers[i + 1]] for i in range(9) for n in layers[i]}
    search = ip._terminal_search(edges, lambda node: False)
    started = time.perf_counter()
    assert search(layers[0][0], frozenset())[0] is None
    assert time.perf_counter() - started < 2


# --- input_evidence ----------------------------------------------------------------------------


def test_the_evidence_query_is_deterministic_and_carries_the_caps():
    a = ie.input_evidence_query("/r", [("a.c", 2), ("a.c", 9)])
    assert a == ie.input_evidence_query("/r", [("a.c", 2), ("a.c", 9)])
    for number in (ie.MAX_METHODS, ie.MAX_DEPTH, ie.MAX_READS, ie.MAX_GUARDS):
        assert re.search(rf"\b{number}\b", a)
    assert not re.findall(r"__[A-Z]+(?:_[A-Z]+)*__", a.replace("__END_SLICER__", ""))
    for flag in ("reads_truncated", "guards_truncated", "truncation_reasons"):
        assert flag in a


def test_the_evidence_validator_pins_the_truncation_fields(index):
    good = evidence_for(index)
    assert ie.valid_input_evidence(good)
    assert not ie.valid_input_evidence(good | {"truncated": True})  # reasons say otherwise
    assert not ie.valid_input_evidence(good | {"truncation_reasons": ["nonsense"]})
    broken = json.loads(json.dumps(good))
    del broken["methods"][0]["reads_truncated"]
    assert not ie.valid_input_evidence(broken)
    assert not ie.valid_input_evidence(good | {"version": 2})
    assert not ie.valid_input_evidence(None)
