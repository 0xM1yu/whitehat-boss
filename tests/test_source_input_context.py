"""슬라이서의 `analysis.input_context`를 Auditor `SOURCE`로 싣는다 (S20 Phase 13 · council
2026-10-14).

결정(사용자 최종 승인 2026-10-14): `functions[]` 중 `status == "selected"` ∧ `basis == "cpg"`인
것만,
쓰기 지점 창 **뒤에** 같은 상한·`seen` 기계로 붙인다. `syntax`
근거·`dispatches`·`carried_decoders`는
이 판에서 싣지 않고 이벤트에 수만 남긴다. 옵션은 `auditor_input_context`(기본 0 · `budgets.yaml` 1).

**입력 샘플은 생산자에서 온다** — `tests/test_input_context.py`가 쓰는 실제 `build_input_context`와
libmagic
발췌(`tests/fixtures/input_context/apprentice_excerpt.c`)로 만든다(CLAUDE.md §2.5 「핀 샘플은
저장소에서
온다」). 모양을 지어내면 통과하는 모양만 고르게 된다. tree-sitter가 없으면 이 파일은 건너뛴다.

묶는 것:
1. 로더 — 키 없음 `None` ≠ `none_selected` · 모양이 틀리면 로드에서 죽는다.
2. `_auditor_source` — 본문이 쓰기 지점 창 뒤에 붙고 이벤트 수가 맞는다.
3. 바이트 동일 — 옵션 0이면 SOURCE·이벤트가 `input_context` 유무와 무관, 옵션 1이어도 키가 없으면
SOURCE 동일.
4. `syntax` 근거는 본문을 안 더하고 건너뜀으로 센다. 5. 이미 실린 범위는 중복 안 한다.
6. 켠 옵션을 서술하지 않는 Auditor 판(v15)이면 cli가 막는다.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")

from src import cli  # noqa: E402
from src.adapters.slices import SliceInputError, load_slices, slice_refs  # noqa: E402
from src.config import AUDITOR_SOURCE_OPTIONS, load_config  # noqa: E402
from src.orchestrator import runner  # noqa: E402
from src.schemas.common import Location  # noqa: E402
from tests.test_input_context import (  # noqa: E402
    EXCERPT,
    eatsize_call_line,
    evidence_for,
    fixture_lines,
    hextoint_line,
    index,  # noqa: F401 - 모듈 범위 fixture
    source,  # noqa: F401 - fixture
)
from tests.test_input_context import run as produce  # noqa: E402
from tests.test_slice_targets import _unit  # noqa: E402
from tests.test_slices import _backward, _document, _slice, _write  # noqa: E402

FILE = "apprentice_excerpt.c"
_OTHER = ["void setup(struct s *x) {", "  int k = 0;", "  l = k;", "  return;", "}"]


@pytest.fixture
def contexts(index, source) -> dict[str, dict]:  # noqa: F811
    """생산자가 실제로 낸 `input_context` 넷."""
    call = eatsize_call_line()
    return {
        "cpg": produce(index, source, evidence_for(index), lines=[call]),
        "syntax": produce(index, source, lines=[call]),
        "none": produce(index, source, lines=[hextoint_line()]),
        "capped": produce(index, source, lines=[call], max_chars=1),
    }


def _locations(built) -> list[Location]:  # noqa: ANN001
    return [
        Location(file=FILE, symbol=name, line_start=row["line_start"], line_end=row["line_end"])
        for name, rows in built["functions"].items()
        for row in rows
    ]


def _load(tmp_path: Path, context: dict | None, points=None):  # noqa: ANN202
    """`slices.json` 한 슬라이스를 로더로 읽어 `SliceRef`를 낸다 — 러너가 받는 모양 그대로다."""
    sources = {FILE: fixture_lines(EXCERPT)}
    extra = {} if context is None else {"input_context": context}
    item = _slice(
        "slice_0001", "B", points or {(FILE, eatsize_call_line()), (FILE, hextoint_line() + 1)},
        sources, analysis=_backward(**extra),
    )
    return slice_refs(load_slices(_write(tmp_path, _document([item]))))[0]


# === 1. 로더 =====================================================================================


def test_a_missing_key_is_none_and_none_selected_is_a_value(tmp_path, contexts) -> None:
    assert _load(tmp_path, None).input_context is None
    loaded = _load(tmp_path, contexts["none"]).input_context
    assert loaded is not None and loaded.status == "none_selected" and loaded.functions == ()


def test_a_cpg_selection_loads_with_the_functions_the_producer_listed(tmp_path, contexts) -> None:
    loaded = _load(tmp_path, contexts["cpg"]).input_context
    produced = contexts["cpg"]["functions"][0]
    assert (loaded.status, loaded.basis, loaded.version) == ("selected", "cpg", 1)
    assert (loaded.cap_hit, loaded.cap_reasons) == (False, ())
    (fn,) = loaded.functions
    assert (fn.file, fn.symbol, fn.line_start, fn.line_end) == (
        produced["file"], produced["symbol"], produced["line_start"], produced["line_end"],
    )
    assert fn.symbol == "eatsize"


def test_cap_hit_is_carried_as_the_producer_wrote_it(tmp_path, contexts) -> None:
    """빈 선택에 서 있는 `cap_hit`은 「잘린 탐색일 수 있다」다 — 0이 아니라 모름. 그대로 든다."""
    capped = contexts["capped"]
    assert capped["cap_hit"] is True and capped["cap_reasons"] == ["max_chars"]
    loaded = _load(tmp_path, capped).input_context
    assert (loaded.cap_hit, loaded.cap_reasons, loaded.functions) == (True, ("max_chars",), ())


def _broken(kind: str, context: dict) -> object:
    """읽는 키 하나만 틀어 놓은 사본 — 틀린 모양마다 로드가 죽어야 한다."""
    bad = json.loads(json.dumps(context))
    if kind == "not_an_object":
        return ["selected"]
    if kind == "version":
        bad["version"] = 2
    elif kind == "status":
        bad["status"] = "maybe"
    elif kind == "basis":
        bad["basis"] = "guess"
    elif kind == "cap_hit_string":
        bad["cap_hit"] = "false"
    elif kind == "cap_hit_missing":
        del bad["cap_hit"]  # 없는 키를 `false`로 읽으면 「상한에 안 닿았다」는 안 잰 사실이 된다
    elif kind == "cap_hit_null":
        bad["cap_hit"] = None
    elif kind == "cap_reasons_missing":
        del bad["cap_reasons"]
    elif kind == "cap_reasons_int":
        bad["cap_reasons"] = [3]
    elif kind == "functions_string":
        bad["functions"] = "eatsize"
    elif kind == "function_no_file":
        del bad["functions"][0]["file"]
    elif kind == "line_start_string":
        bad["functions"][0]["line_start"] = "44"
    elif kind == "line_start_bool":
        bad["functions"][0]["line_start"] = True
    elif kind == "range_reversed":
        bad["functions"][0]["line_start"], bad["functions"][0]["line_end"] = 60, 40
    elif kind == "dispatches_string":
        bad["dispatches"] = "x"
    return bad


@pytest.mark.parametrize(
    "kind",
    ["not_an_object", "version", "status", "basis", "cap_hit_string", "cap_hit_missing",
     "cap_hit_null", "cap_reasons_missing", "cap_reasons_int",
     "functions_string", "function_no_file", "line_start_string", "line_start_bool",
     "range_reversed", "dispatches_string"],
)
def test_a_malformed_input_context_stops_the_load(tmp_path, contexts, kind) -> None:
    with pytest.raises(SliceInputError):
        _load(tmp_path, _broken(kind, contexts["cpg"]))


# === 2~5. `_auditor_source` =====================================================================


def _files() -> dict[str, list[str]]:
    return {FILE: fixture_lines(EXCERPT), "other.c": _OTHER}


def _source(built, tmp_path, context, *, lines, **opts):  # noqa: ANN001, ANN202
    """`(SOURCE, 마지막 auditor_source 이벤트)` — entry는 `parse_op_modifier`, 쓰기 창이 `other.c`에
    선다."""
    setup = Location(file="other.c", symbol="setup", line_start=1, line_end=5)
    symbols = [*_locations(built), setup]
    entry = next(s for s in symbols if s.symbol == "parse_op_modifier")
    ctx, events, _, _ = _unit(
        _files(), symbols,
        writes={"l": [Location(file="other.c", symbol="setup", line_start=3, line_end=3)]},
        **opts,
    )
    ref = _load(tmp_path, context, points=set(lines))
    return runner._auditor_source(ctx, ref, entry, "cand"), events[-1]


def _CALL_AND_HEXTOINT() -> set[tuple[str, int]]:  # noqa: N802
    return {(FILE, eatsize_call_line()), (FILE, hextoint_line() + 1)}


_ON = {"auditor_input_context": 1, "auditor_write_sites": 6}
_WRITE_ONLY = {"auditor_write_sites": 6}


def test_a_cpg_function_body_follows_the_write_window_and_the_counts_match(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    lines = _CALL_AND_HEXTOINT()
    base, _ = _source(index, tmp_path, None, lines=lines, **_WRITE_ONLY)
    got, event = _source(index, tmp_path, contexts["cpg"], lines=lines, **_ON)
    decoder = "/* ===== apprentice_excerpt.c:44-65 ===== */"
    window = "/* ===== other.c:1-5 ===== */"

    assert decoder in got and decoder not in base
    assert window in got and got.index(window) < got.index(decoder), "쓰기 지점 창 뒤여야 한다"
    assert got.startswith(base), "붙이기는 맨 뒤에만 일어난다"
    assert "eatsize(const char **p)" in got
    assert event["input_context"] == "selected" and event["input_context_basis"] == "cpg"
    assert (event["input_context_listed"], event["input_context_added"]) == (1, 1)
    assert (event["input_context_skipped_syntax"], event["input_context_skipped_carried"],
            event["input_context_skipped_size"]) == (0, 0, 0)
    assert event["input_context_chars"] == len(got) - len(base)
    assert event["input_context_cap_hit"] is False
    assert event["write_windows"] == 1
    # 파생 수: 새 블록 종류가 `functions`에 섞이지 않는다 - entry·hextoint 둘.
    assert event["functions"] == 2


def test_with_the_option_off_source_and_event_ignore_the_input_context(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    lines = _CALL_AND_HEXTOINT()
    without = _source(index, tmp_path, None, lines=lines, **_WRITE_ONLY)
    with_key = _source(index, tmp_path, contexts["cpg"], lines=lines, **_WRITE_ONLY)
    assert with_key == without
    assert not any(key.startswith("input_context") for key in with_key[1])


def test_with_the_option_on_but_no_key_the_source_is_unchanged(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    lines = _CALL_AND_HEXTOINT()
    off_source, off_event = _source(index, tmp_path, None, lines=lines, **_WRITE_ONLY)
    on_source, on_event = _source(index, tmp_path, None, lines=lines, **_ON)
    assert on_source == off_source
    extra = {k: v for k, v in on_event.items() if k not in off_event}
    assert extra == {"input_context": "absent"}, "키 없음은 수를 0으로 채우지 않는다"
    assert {k: v for k, v in on_event.items() if k in off_event} == off_event


def test_none_selected_is_reported_apart_from_absent_and_keeps_cap_hit(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    lines = _CALL_AND_HEXTOINT()
    off_source, _ = _source(index, tmp_path, None, lines=lines, **_WRITE_ONLY)
    source_text, event = _source(index, tmp_path, contexts["none"], lines=lines, **_ON)
    assert source_text == off_source
    assert event["input_context"] == "none_selected" and event["input_context_added"] == 0
    assert event["input_context_cap_hit"] is False
    capped_source, capped = _source(index, tmp_path, contexts["capped"], lines=lines, **_ON)
    assert capped_source == off_source
    assert (capped["input_context_cap_hit"], capped["input_context_cap_reasons"]) == (
        True, "max_chars"
    )


def test_a_syntax_basis_adds_no_body_and_is_counted_as_skipped(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    lines = _CALL_AND_HEXTOINT()
    base, _ = _source(index, tmp_path, None, lines=lines, **_WRITE_ONLY)
    assert contexts["syntax"]["functions"], "전제 - 생산자는 syntax에서도 함수를 고른다"
    got, event = _source(index, tmp_path, contexts["syntax"], lines=lines, **_ON)
    assert got == base
    assert (event["input_context"], event["input_context_basis"]) == ("selected", "syntax")
    assert (event["input_context_listed"], event["input_context_added"]) == (1, 0)
    assert event["input_context_skipped_syntax"] == 1


def test_a_range_already_carried_is_not_duplicated(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    """슬라이스 줄이 `eatsize` 안(47)에도 있어 그 함수가 이미 실렸다. 생산자의
    `identical_to_auditor_input`은 거짓인데도(= 슬라이스 자신의 함수만 가리킨다) 다시 안 붙인다."""
    assert contexts["cpg"]["identical_to_auditor_input"] is False
    lines = {(FILE, eatsize_call_line()), (FILE, 47)}
    base, _ = _source(index, tmp_path, None, lines=lines, **_WRITE_ONLY)
    got, event = _source(index, tmp_path, contexts["cpg"], lines=lines, **_ON)
    assert got == base
    assert got.count("eatsize(const char **p)") == 1
    assert (event["input_context_added"], event["input_context_skipped_carried"]) == (0, 1)


def test_a_write_window_that_only_overlaps_the_decoder_does_not_block_it(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    """쓰기 지점 창이 디코더 안에 선다(디코더가 이름 `l`을 쓴다 - 47줄). 창은 함수의 일부일 뿐이라
    함수
    전체가 이미 든 것이 **아니다** - 완전한 함수가 붙고(창과 몇 줄 중복), 건너뜀으로 안 센다."""
    symbols = _locations(index)
    entry = next(s for s in symbols if s.symbol == "parse_op_modifier")
    ctx, events, _, _ = _unit(
        _files(), symbols, auditor_input_context=1, auditor_write_sites=6,
        writes={"l": [Location(file=FILE, symbol="eatsize", line_start=47, line_end=47)]},
    )
    ref = _load(tmp_path, contexts["cpg"], points={(FILE, eatsize_call_line())})
    got = runner._auditor_source(ctx, ref, entry, "cand")
    event = events[-1]
    assert event["write_windows"] == 1, "전제 - 창이 먼저 디코더 안에 섰다"
    assert "/* ===== apprentice_excerpt.c:44-65 ===== */" in got, "완전한 함수가 붙는다"
    assert (event["input_context_added"], event["input_context_skipped_carried"]) == (1, 0)
    assert got.count("*p = l;") == 1 and got.count("const char *l = *p;") == 2, (
        "창(44~50줄 쪽)과 겹친 줄은 두 번 나온다 - 중복은 받아들인다"
    )


def test_a_decoder_that_is_already_the_anchor_is_skipped_as_carried(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    """슬라이서가 고른 함수가 이미 entry(anchor) 함수 자신이다 - 전체가 든 것이라 건너뛴다."""
    symbols = _locations(index)
    entry = next(s for s in symbols if s.symbol == "parse_op_modifier")
    own = json.loads(json.dumps(contexts["cpg"]))
    own["functions"][0].update(
        symbol=entry.symbol, line_start=entry.line_start, line_end=entry.line_end
    )
    ctx, events, _, _ = _unit(_files(), symbols, auditor_input_context=1)
    ref = _load(tmp_path, own, points={(FILE, eatsize_call_line())})
    got = runner._auditor_source(ctx, ref, entry, "cand")
    assert "eatsize(const char **p)" not in got
    event = events[-1]
    assert (event["input_context_added"], event["input_context_skipped_carried"]) == (0, 1)


def test_containment_is_decided_by_the_union_of_the_seen_ranges() -> None:
    """한 범위가 덮으면 건너뛰고, 이어 붙은 여러 범위의 합집합이 덮어도 건너뛴다. 틈이 있거나 일부만
    겹치면 건너뛰지 않는다. 파일 전체(범위 없음)가 든 파일은 다 든 것이다."""
    from src.orchestrator.source_input_context import _contained

    def at(first: int, last: int, file: str = FILE) -> Location:
        return Location(file=file, line_start=first, line_end=last)

    target = at(44, 65)
    assert _contained(target, [at(40, 70)])
    assert _contained(target, [at(44, 50), at(51, 65)]), "합집합이 덮는다"
    assert _contained(target, [at(51, 70), at(30, 52)]), "순서와 무관하다"
    assert not _contained(target, [at(44, 50), at(52, 65)]), "한 줄(51) 틈"
    assert not _contained(target, [at(44, 50)]), "일부만 겹친다"
    assert not _contained(target, [at(1, 100, "other.c")])
    assert _contained(target, [Location(file=FILE)])
    assert not _contained(target, [])


_SETUP_LINES = [f"s{i} t{i}" for i in range(1, 16)]  # other.c 1~15 - 심볼 `setup` 하나
_SETUP = Location(file="other.c", symbol="setup", line_start=1, line_end=15)


def _window_world(built, tmp_path, decoder, context, *, status="selected", cap=0):  # noqa: ANN001, ANN202
    """`other.c`의 `setup`(1~15)에 쓰기 지점 창(8줄 → 5~11줄)이 서고, 슬라이서가 고른 함수 `decoder`
    (심볼 표에는 없다)가 그 창 안이나 밖에 있다. `(SOURCE, 이벤트)`."""
    symbols = [*_locations(built), _SETUP]
    entry = next(s for s in symbols if s.symbol == "parse_op_modifier")
    files = {**_files(), "other.c": _SETUP_LINES}
    tokens = {"auditor_source_tokens": cap} if cap else {}
    site = Location(file="other.c", symbol="setup", line_start=8, line_end=8)
    ctx, events, _, _ = _unit(
        files, symbols, auditor_input_context=1, auditor_write_sites=6, writes={"l": [site]},
        **tokens,
    )
    context = json.loads(json.dumps(context))
    context["status"] = status
    context["functions"] = [decoder]
    ref = _load(tmp_path, context, points={(FILE, eatsize_call_line())})
    return runner._auditor_source(ctx, ref, entry, "cand"), events[-1]


def _function(first: int, last: int, file: str = "other.c") -> dict:
    return {
        "kind": "decoder", "file": file, "symbol": "tiny", "line_start": first, "line_end": last,
    }


def test_a_decoder_inside_a_shown_window_is_carried(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    """창(5~11)이 SOURCE에 **실렸고** 디코더(6~8)가 그 안에 든다 - 전체가 이미 보이니 건너뛴다."""
    got, event = _window_world(index, tmp_path, _function(6, 8), contexts["cpg"])
    assert "/* ===== other.c:5-11 ===== */" in got and "other.c:6-8" not in got
    assert event["write_windows"] == 1
    assert (event["input_context_added"], event["input_context_skipped_carried"]) == (0, 1)


def test_a_decoder_inside_a_size_dropped_window_is_not_carried(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    """같은 배치에서 상한이 창(7줄)만 못 싣게 조인다. 창은 안 보이는데 `seen`에는 든다 -
    디코더(3줄)가 그 안에 들어도 **실린 것이 아니다**: 건너뛴 것(carried)으로 세지 않고 붙는다.
    상한이 디코더도 못 싣게 더 조이면 `skipped_size`로 센다."""
    words = lambda text: len(text.split())  # noqa: E731
    join = "\n".join
    entry = Location(file=FILE, line_start=13, line_end=28)
    tiny = Location(file="other.c", line_start=6, line_end=8)
    wide = Location(file="other.c", line_start=5, line_end=11)
    entry_block = runner._block(entry, join(fixture_lines(EXCERPT)[12:28]))
    decoder_block = runner._block(tiny, join(_SETUP_LINES[5:8]))
    window_block = runner._block(wide, join(_SETUP_LINES[4:11]))
    assert words(decoder_block) < words(window_block), "전제 - 디코더가 창보다 작다"
    fits = words(entry_block) + 1 + words(decoder_block)

    got, event = _window_world(index, tmp_path, _function(6, 8), contexts["cpg"], cap=fits)
    assert (event["write_windows"], event["write_windows_dropped"]) == (0, 1), "전제 - 창이 잘렸다"
    assert "/* ===== other.c:6-8 ===== */" in got
    assert (event["input_context_added"], event["input_context_skipped_carried"]) == (1, 0)

    _, tight = _window_world(index, tmp_path, _function(6, 8), contexts["cpg"], cap=fits - 1)
    assert (
        tight["input_context_added"],
        tight["input_context_skipped_carried"],
        tight["input_context_skipped_size"],
    ) == (0, 0, 1)


def test_a_none_selected_status_adds_nothing_even_if_functions_are_listed(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    """합성 입력 - 생산자는 `none_selected`에 함수를 안 싣지만 상태가 판정이다. 목록에 있어도 안
    붙인다."""
    decoder = _function(12, 14)  # 어느 창에도 안 든다 - 상태를 안 보면 붙는다
    got, event = _window_world(index, tmp_path, decoder, contexts["cpg"], status="none_selected")
    assert "other.c:12-14" not in got
    selected, _ = _window_world(index, tmp_path, decoder, contexts["cpg"])
    assert "/* ===== other.c:12-14 ===== */" in selected, "전제 - selected면 붙는다"
    assert event["input_context"] == "none_selected"
    assert (event["input_context_listed"], event["input_context_added"]) == (1, 0)


def test_a_body_that_reads_empty_is_skipped_and_counted(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    """파일 끝을 넘는 범위는 빈 문자열을 읽는다 - 배너만 싣지 않고 `skipped_empty`로 센다."""
    got, event = _window_world(index, tmp_path, _function(40, 50), contexts["cpg"])
    assert "other.c:40-50" not in got
    assert (event["input_context_added"], event["input_context_skipped_empty"]) == (0, 1)


def test_a_body_over_the_shared_cap_is_skipped_and_counted(
    index, tmp_path, contexts  # noqa: F811
) -> None:
    lines = _CALL_AND_HEXTOINT()
    tight = {**_WRITE_ONLY, "auditor_source_tokens": 5}
    base, _ = _source(index, tmp_path, None, lines=lines, **tight)
    got, event = _source(index, tmp_path, contexts["cpg"], lines=lines,
                         auditor_input_context=1, **tight)
    assert got == base
    assert (event["input_context_added"], event["input_context_skipped_size"]) == (0, 1)


def test_the_new_option_is_a_source_option_defaulting_to_off() -> None:
    assert "auditor_input_context" in AUDITOR_SOURCE_OPTIONS
    config = load_config("harness_pool")
    assert config.thresholds.auditor_input_context == 1
    assert "auditor_input_context" in config.thresholds.auditor_source_options_on()
    assert load_config("dev_fake").thresholds.transition(
        **{key: 0 for key in AUDITOR_SOURCE_OPTIONS}
    ).auditor_input_context == 0


# === 6. 서술하지 않는 판이면 cli가 막는다
# ============================================================


def _with_auditor_version(version: str):  # noqa: ANN202
    config = load_config("harness_pool")
    key = config.model_key_for("auditor")
    entry = config.models[key].transition(prompt_template_version=version)
    return config.transition(models={**config.models, key: entry})


def test_v15_does_not_describe_the_option_so_the_run_and_doctor_refuse_it() -> None:
    config = _with_auditor_version("v15")
    assert cli._undescribed_source_options(config) == ["auditor_input_context(auditor/v15)"]
    (check,) = cli._check_source_options(config)
    assert check.severity.name == "ERROR" and "auditor_input_context" in check.message


def test_v16_describes_it_and_the_off_state_never_blocks() -> None:
    assert cli._undescribed_source_options(_with_auditor_version("v16")) == []
    config = _with_auditor_version("v15")
    off = config.transition(thresholds=config.thresholds.transition(auditor_input_context=0))
    assert cli._undescribed_source_options(off) == []
