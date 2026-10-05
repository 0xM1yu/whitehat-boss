"""`auditor_source_gate` — Auditor `SOURCE`가 어디까지 믿을 입력인지를 사유 코드로 남긴다.

세 층을 본다. ① 순수 함수(`orchestrator/source_gate.py`)가 사유마다 하나씩 ② 임계가 꺼져 있으면
`auditor_source` 이벤트와 Auditor 입력이 **그대로**이고 켜면 `gate_reasons`가 붙는다(관측 전용 —
입력은 안 바뀐다) ③ 슬라이서가 낸 절단·경계·싱크 cap이 로더를 지나 이벤트까지 간다.

러너 배선의 세부(상한·정의·피호출)는 `tests/test_slice_targets.py`의 `_unit` 도우미로
`_auditor_source`를 직접 부르고, 로더에서 이벤트까지의 길은 같은 파일의 e2e 도우미(`_run`·
`_slices_file`)로 돌린다.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import AUDITOR_SOURCE_OPTIONS, load_config
from src.orchestrator import runner, source_gate
from src.schemas.candidate import SliceRef
from src.schemas.common import Location
from tests.test_pipeline_e2e import _run, _with_index
from tests.test_slice_targets import (
    _CASE_B,
    _SPANS_TWO,
    _U_F,
    _U_FILE,
    _U_G,
    _prompts,
    _slice,
    _slices_file,
    _source_events,
    _unit,
)

_FN = Location(file="a.c", symbol="f", line_start=1, line_end=20)  # 감싸는 함수를 찾은 앵커
_WINDOW = Location(file="a.c", line_start=1, line_end=60)  # 못 찾아 세운 창 — 심볼이 없다
# 실제 슬라이서 산출(`slices.json`)의 `entry_parameter` 경계에서 옮긴 이름 — 저장소 상대 경로인데
# Joern이 인클루드 디렉터리를 겹쳐 붙여 보고한 꼴이다.
_ENTRY_PARAMETER = ("entry_parameter", "libavdevice/libavformat/libavformat/avio.h", 540)


# 슬라이서가 끝마다 적는 `analysis_kind`(`slicer_pipeline.py`) — 이것이 있어야 분석 사실을 **본**
# 슬라이스다. 없으면(옛 슬라이스) 게이트가 `ANALYSIS_UNKNOWN`을 낸다.
_KIND = {"sink": "backward_ddg", "source": "parameter_paths"}


def _ref(given_end: str = "sink", **fields) -> SliceRef:
    entry = Location(file="a.c", line_start=10, symbol="memcpy")
    fields.setdefault("analysis_kind", _KIND[given_end])
    return SliceRef(
        slice_id="s1", location=Location(file="a.c", line_start=10, symbol="f"),
        given_end=given_end, entry=entry, lines=(("a.c", 10),), **fields,
    )


# --- ① 순수 함수 — 사유마다 하나 ----------------------------------------------------------


def test_a_clean_full_slice_with_an_enclosing_function_has_no_reasons() -> None:
    """빈 목록은 「나열한 검사가 하나도 안 걸렸다」이다 — 「SOURCE가 완전하다」가 아니다(꺼 둔
    정의·피호출 단계는 검사 자체가 안 돈다)."""
    assert source_gate.reasons(_ref(), _FN) == []


@pytest.mark.parametrize("given_end", ["sink", "source"])
def test_a_slice_without_analysis_facts_is_unknown_not_clean(given_end: str) -> None:
    """`analysis_kind`가 비었다 = 슬라이서가 분석 사실을 안 적었다(옛 `slices.json`). 그러면
    `truncated=False`·경계 없음은 「안 잘렸다」가 아니라 「모른다」다 — 사유 없이 빈 목록을 내면
    다 본 깨끗한 슬라이스(위 검사)와 같은 문자열이 되어 없음과 모름이 안 갈린다(CLAUDE.md §0)."""
    assert source_gate.reasons(_ref(given_end, analysis_kind=""), _FN) == ["ANALYSIS_UNKNOWN"]


def test_a_degraded_slice_is_flagged() -> None:
    """anchor-only 대체 슬라이스(#307)는 dataflow 문맥이 없다."""
    assert source_gate.reasons(_ref(degraded=True), _FN) == ["SLICE_DEGRADED"]


def test_a_truncated_slice_names_what_cut_it() -> None:
    """사유마다 낱말을 `+`로 잇는다 — 이벤트가 사유를 쉼표로 이으므로 안에 쉼표를 안 둔다."""
    ref = _ref(truncated=True, truncation_reasons=("backward_depth", "call_depth"))

    assert source_gate.reasons(ref, _FN) == ["SLICE_TRUNCATED:backward_depth+call_depth"]


def test_a_truncated_slice_without_a_reason_list_is_still_flagged() -> None:
    """Case A는 사유 목록을 안 낸다(슬라이서의 flows 질의 — 로더가 빈 튜플로 싣는다). 사유가 없다고
    절단을 안 알리면 Case A의 절단이 조용해진다 — 낱말 없는 `SLICE_TRUNCATED`로 알리고 사유를 지어
    채우지는 않는다."""
    assert source_gate.reasons(_ref("source", truncated=True), _FN) == ["SLICE_TRUNCATED"]


def test_a_backward_slice_that_reached_a_parameter_without_a_call_edge_reports_it() -> None:
    """Case B 역추적의 한 갈래가 CPG에 호출 간선(`callIn`)이 없는 함수의 파라미터
    (`entry_parameter`)에 닿았다 — 호출자가 **없다**는 뜻이 아니라 CPG가 호출자를 **못 이었다**는
    뜻이다(`slicer_backward.py`가 그 조건에서만 이 경계를 적는다). 「검증이 없다」도 아니다."""
    assert source_gate.reasons(_ref(boundaries=(_ENTRY_PARAMETER,)), _FN) == [
        "NO_CALLER_IN_CPG"
    ]


def test_the_other_boundary_kinds_are_not_reported_as_a_missing_call_edge() -> None:
    """슬라이서가 내는 나머지 경계(`dependency_root`·`external_or_unresolved_call` —
    `slicer_backward.py`)는 호출 간선이 없다는 뜻이 아니다."""
    boundaries = (("dependency_root", "a.c", 5), ("external_or_unresolved_call", "a.c", 7))

    assert source_gate.reasons(_ref(boundaries=boundaries), _FN) == []


def test_a_forward_slice_never_reports_a_missing_call_edge() -> None:
    """Case A(`source` 끝)는 호출자 쪽으로 안 올라가는 분석이다 — 슬라이서가 경계를 역추적에서만
    적으므로 이 조합은 지금 안 나오지만, 나온다면 호출 간선 부재가 아니라 다른 뜻이라 안 쓴다."""
    assert source_gate.reasons(_ref("source", boundaries=(_ENTRY_PARAMETER,)), _FN) == []


def test_an_anchor_without_a_symbol_is_a_window_fallback() -> None:
    """entry를 품는 함수를 못 찾아 앞뒤 창을 읽었다(`_auditor_target`) — 인덱스가 없을 때도, 있어도
    entry가 어느 정의에도 안 들 때도 같다. 창은 심볼이 없는 위치다."""
    assert source_gate.reasons(_ref(), _WINDOW) == ["WINDOW_FALLBACK"]


def test_a_sink_set_the_flow_query_capped_is_flagged() -> None:
    """Case A flows 질의가 전역 싱크 집합을 `FLOWS_SINK_CAP`에서 잘랐다(`analysis.sinks_capped` —
    로더가 싣는다). `truncated`(경로 수 상한)와 별개라 절단이 아닌 슬라이스에도 선다."""
    assert source_gate.reasons(_ref("source", sinks_capped=True), _FN) == ["FLOW_SINKS_CAPPED"]


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        pytest.param({"uncovered_lines": 3}, ["SLICE_LINES_UNCOVERED"], id="uncovered"),
        pytest.param({"functions_dropped": 1}, ["SOURCE_DROPPED"], id="function"),
        pytest.param({"callees_dropped": 1}, ["CALLEE_DROPPED"], id="callee"),
        pytest.param({"callers_dropped": 1}, ["CALLER_DROPPED"], id="caller"),
        pytest.param({"definitions_dropped": 2}, ["DEFINITION_DROPPED"], id="definition"),
    ],
)
def test_what_the_source_assembly_could_not_read_or_dropped_is_flagged(
    counts: dict, expected: list
) -> None:
    """`_auditor_source`가 세는 다섯 — 인덱스 함수에 안 든 슬라이스 줄(`uncovered_lines`) · 총
    상한에 걸려 빠진 함수(`dropped`) · 피호출(`callees_dropped`) · 호출자 창(`callers_dropped` —
    호출자가 있는데 입력에 없다) · 정의(`definitions_dropped`). 0이면
    사유가 없다(위 빈 목록 검사가 기본값 0으로 그것을 본다)."""
    assert source_gate.reasons(_ref(), _FN, **counts) == expected


def test_the_reasons_come_in_one_stable_order() -> None:
    """슬라이스(분석 사실 없음·대체·절단·싱크 cap) → 대상(창) → `SOURCE` 조립(못 읽은 줄·빠진
    함수·피호출·호출자 창) → CPG에
    호출 간선 없음 → 정의 순이다 — 같은 입력이면 같은 문자열이라 run끼리 이벤트를 대조할 수
    있다."""
    ref = _ref(
        analysis_kind="", degraded=True, truncated=True, truncation_reasons=("backward_depth",),
        sinks_capped=True, boundaries=(_ENTRY_PARAMETER,),
    )

    assert source_gate.reasons(
        ref, _WINDOW, uncovered_lines=1, functions_dropped=1, callees_dropped=1,
        callers_dropped=1, definitions_dropped=1,
    ) == [
        "ANALYSIS_UNKNOWN",
        "SLICE_DEGRADED",
        "SLICE_TRUNCATED:backward_depth",
        "FLOW_SINKS_CAPPED",
        "WINDOW_FALLBACK",
        "SLICE_LINES_UNCOVERED",
        "SOURCE_DROPPED",
        "CALLEE_DROPPED",
        "CALLER_DROPPED",
        "NO_CALLER_IN_CPG",
        "DEFINITION_DROPPED",
    ]


# --- 임계 -------------------------------------------------------------------------------


def test_the_gate_stays_out_of_the_run_record_until_turned_on() -> None:
    """0이면 `flatten()`에 키가 없다 — 기본 꺼짐 run의 `RunRecord.thresholds` 모양이
    예전 manifest와 같다(`scout_slice_trim` 선례). 관측 전용이라 프롬프트 meta `source_options`가
    서술할 입력 옵션이 아니다 — `AUDITOR_SOURCE_OPTIONS`에 안 들고 켜도 켜진 입력 옵션 집합이 안
    바뀐다(`cli`가 안 막는다). 집합을 `()`로 박지 않고 게이트를 켜기 전 것과 비교한다 — 입력
    옵션의 기본값(`config/budgets.yaml`)이 바뀌어도 이 성질은 그대로다."""
    # ⏵ 2026-10-05 — `config/budgets.yaml`이 `auditor_source_gate: 1`을 선언해 **기본값이 1이
    # 됐다.** 전 판은 여기서 커밋된 기본값이 0이라고 단언했는데, 그 단언이 무는 것은
    # 「기본이 꺼짐」이고 이 검사의 명제는 **`flatten()`의 모양**이다. 두 값을 직접 만든다.
    thresholds = load_config("dev_fake").thresholds.transition(auditor_source_gate=0)
    assert thresholds.auditor_source_gate == 0
    assert "auditor_source_gate" not in thresholds.flatten()

    on = thresholds.transition(auditor_source_gate=1)

    assert on.flatten()["auditor_source_gate"] == 1.0
    assert "auditor_source_gate" not in AUDITOR_SOURCE_OPTIONS
    assert on.auditor_source_options_on() == thresholds.auditor_source_options_on()
    with pytest.raises(ValidationError):
        thresholds.transition(auditor_source_gate=2)


# --- ② 러너 — 꺼지면 그대로, 켜면 `gate_reasons` ------------------------------------------


def _gate_event(gate: int, anchor: Location = _U_F, lines=(("u.c", 2),), **opts) -> dict:
    """`_auditor_source`를 직접 불러 마지막 `auditor_source` 이벤트를 돌려준다. `opts`는 슬라이스
    필드(`degraded`·`truncated`…)가 아니라 `_unit`의 임계·조회 대역이다."""
    ref_fields = {"analysis_kind": "backward_ddg", **opts.pop("ref_fields", {})}
    ctx, events, _, _ = _unit(_U_FILE, [_U_F, _U_G], auditor_source_gate=gate, **opts)
    ref = SliceRef(
        slice_id="s", location=anchor, given_end="sink", entry=anchor, lines=tuple(lines),
        **ref_fields,
    )
    runner._auditor_source(ctx, ref, anchor, "cand")
    return events[-1]


_CUT = {
    "degraded": True, "truncated": True, "truncation_reasons": ("backward_depth",),
    "boundaries": (_ENTRY_PARAMETER,),
}


def test_with_the_gate_off_the_event_is_what_it_was_whatever_the_slice_carries() -> None:
    """꺼져 있으면(기본) `gate_reasons`가 없고 이벤트가 절단·경계를 든 슬라이스에서도 안 든
    슬라이스와 같다 — 로더가 싣게 된 값이 기본 경로로 새지 않는다(바이트 동일)."""
    event = _gate_event(0, ref_fields=_CUT)

    assert "gate_reasons" not in event
    assert event == _gate_event(0)


def test_with_the_gate_on_the_event_carries_the_reasons_joined_by_commas() -> None:
    """이벤트 값은 스칼라여야 한다(`TraceValue`) — 사유 목록을 쉼표로 이은 문자열이다(`unfilled`·
    `truncated` 같은 이름 목록과 같은 방식)."""
    event = _gate_event(1, ref_fields=_CUT)

    assert event["gate_reasons"] == (
        "SLICE_DEGRADED,SLICE_TRUNCATED:backward_depth,NO_CALLER_IN_CPG"
    )


def test_with_the_gate_on_a_clean_slice_says_so_with_an_empty_string() -> None:
    """켠 run에서 빈 문자열은 「나열한 검사가 하나도 안 걸렸다」이고 필드가 **없는** 것은 「게이트
    꺼짐」이다 — 둘이 같은 신호면 꺼진 run과 걸린 게 없는 run을 못 가른다."""
    assert _gate_event(1)["gate_reasons"] == ""


def test_with_the_gate_on_a_window_anchor_is_reported() -> None:
    window = Location(file="u.c", line_start=1, line_end=3)

    assert _gate_event(1, anchor=window)["gate_reasons"] == "WINDOW_FALLBACK"


@pytest.mark.parametrize(
    ("lines", "opts", "counter", "expected"),
    [
        pytest.param(
            (("u.c", 2), ("u.c", 15)), {}, "uncovered_lines", "SLICE_LINES_UNCOVERED",
            id="uncovered",
        ),
        pytest.param(
            (("u.c", 2), ("u.c", 25)), {"auditor_source_tokens": 20}, "dropped",
            "SOURCE_DROPPED", id="function",
        ),
    ],
)
def test_with_the_gate_on_unread_lines_and_a_cap_dropped_function_are_reported(
    lines: tuple, opts: dict, counter: str, expected: str
) -> None:
    """15번 줄은 어느 인덱스 함수(`f` 1–3 · `g` 20–29)에도 안 들어 못 읽는다. `g`(10줄 = 20단어 +
    배너)는 토큰 상한 20에 안 들어 빠진다 — 이벤트가 숫자로만 남기던 `uncovered_lines`·`dropped`가
    1이 되는 그 경우를 게이트가 사유로 올린다."""
    event = _gate_event(1, lines=lines, **opts)

    assert event[counter] == 1
    assert event["gate_reasons"] == expected


@pytest.mark.parametrize(
    ("opts", "counter", "expected"),
    [
        pytest.param(
            {"auditor_definitions": 1, "definitions": lambda _n, _f: [_U_G]},
            "definitions_dropped", "DEFINITION_DROPPED", id="definition",
        ),
        pytest.param(
            {"auditor_callee_hops": 1, "callees": {"f": [_U_G]}},
            "callees_dropped", "CALLEE_DROPPED", id="callee",
        ),
        pytest.param(
            {"auditor_caller_lines": 5,
             "sites": {"f": [Location(file="u.c", symbol="g", line_start=25, line_end=25)]}},
            "callers_dropped", "CALLER_DROPPED", id="caller",
        ),
    ],
)
def test_with_the_gate_on_what_the_cap_dropped_is_reported(
    opts: dict, counter: str, expected: str
) -> None:
    """`g`(10줄 = 20단어 + 배너)는 토큰 상한 20에 안 든다 — 이벤트의 `*_dropped` 카운터가 1이 되는
    그 경우를 게이트가 사유로 올린다(상한이 먹은 정의·피호출·호출자 창이 입력에서 빠졌다는
    신호). 호출자 창은 `g`의 25번 줄이 `f`를 부르는 지점 앞뒤 5줄을 `g`(20–29)로 자른 것이다."""
    event = _gate_event(1, auditor_source_tokens=20, **opts)

    assert event[counter] == 1
    assert event["gate_reasons"] == expected


# --- ③ 로더 → 러너 → 이벤트 (e2e) ---------------------------------------------------------


def _gate(value: int):  # noqa: ANN202
    def configure(config):  # noqa: ANN001, ANN202
        return config.transition(
            thresholds=config.thresholds.transition(auditor_source_gate=value)
        )

    return configure


def _cut_case_b(*, with_facts: bool = True) -> dict:
    """슬라이서가 쓰는 모양 그대로 절단·경계를 얹은 Case B 픽스처 — 경계의 `file`은 저장소 상대
    경로다(`lines`·`entry`처럼 정리된 것은 아니고 Joern이 보고한 그대로 — 실제 산출에서 본
    꼴)."""
    item = _slice(*_CASE_B)
    if with_facts:
        item.update(
            truncated=True, analysis_kind="backward_ddg",
            analysis={
                "flow_count": None, "truncated": True, "lines": [], "node_count": 2,
                "state_count": 2, "dependency_count": 1,
                "boundaries": [{
                    "reason": "entry_parameter",
                    "location": {"file": "parser.c", "line": 19},
                }],
                "truncation_reasons": ["backward_depth", "backward_states"],
            },
        )
    return item


def test_the_slicers_cut_and_boundary_reach_the_auditor_source_event(tmp_path: Path) -> None:
    """슬라이서가 `slices.json`에 쓴 절단·경계가 로더(`Slice`→`SliceRef`)와 러너를 지나 이벤트에
    사유로 남는다 — 어느 한 겹이라도 버리면 이 사유가 안 나온다. entry(24)는 `parse_header`
    (19–26) 안이라 앵커는 함수이고 `WINDOW_FALLBACK`은 없다."""
    path = _slices_file(tmp_path, [_cut_case_b()])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(), configure=_gate(1))

    (event,) = _source_events(trace_path)
    assert event["gate_reasons"] == (
        "SLICE_TRUNCATED:backward_depth+backward_states,NO_CALLER_IN_CPG"
    )


def test_without_an_index_the_event_says_the_auditor_read_a_window(tmp_path: Path) -> None:
    """인덱스가 없으면 entry를 품는 함수를 모른다 — Auditor는 entry 앞뒤 창을 읽었고
    (`_auditor_target`) 게이트가 그것을 `WINDOW_FALLBACK`으로 남긴다. 사유 순서는 슬라이스 → 대상
    → CPG 호출 간선 없음이다."""
    path = _slices_file(tmp_path, [_cut_case_b()])

    _, trace_path = _run(tmp_path, slices_path=path, configure=_gate(1))

    (event,) = _source_events(trace_path)
    assert event["gate_reasons"] == (
        "SLICE_TRUNCATED:backward_depth+backward_states,WINDOW_FALLBACK,NO_CALLER_IN_CPG"
    )


def test_a_capped_case_a_slice_reports_its_flow_sink_cap_end_to_end(tmp_path: Path) -> None:
    """Case A 슬라이스의 `analysis.sinks_capped`가 로더를 지나 이벤트에 `FLOW_SINKS_CAPPED`로
    남는다 — 사유 목록을 안 내는 Case A의 절단(낱말 없는 `SLICE_TRUNCATED`)과 같이 나온다.
    슬라이서의 flows 질의가 내는 키(`flow_count`·`truncated`·`sinks_capped`·`sinks_kept`·
    `lines`) 그대로다."""
    item = _slice(*_SPANS_TWO)
    item.update(
        truncated=True, analysis_kind="parameter_paths",
        analysis={"flow_count": 10, "truncated": True, "sinks_capped": True,
                  "sinks_kept": 2063, "lines": []},
    )
    path = _slices_file(tmp_path, [item])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(), configure=_gate(1))

    (event,) = _source_events(trace_path)
    assert event["gate_reasons"] == "SLICE_TRUNCATED,FLOW_SINKS_CAPPED"


def test_an_old_slice_without_the_analysis_facts_only_gets_the_reasons_the_run_knows(
    tmp_path: Path,
) -> None:
    """옛 슬라이스(키가 없다)는 절단·경계 사유가 안 나온다 — 모르는 것을 사유로 만들지 않는다.
    대신 `ANALYSIS_UNKNOWN` 하나가 나온다 — 빈 문자열이면 절단·경계를 다 보고 안 걸린 슬라이스와
    안 갈린다. 인덱스가 있으면 대상도 함수라 다른 사유는 없다."""
    path = _slices_file(tmp_path, [_cut_case_b(with_facts=False)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(), configure=_gate(1))

    (event,) = _source_events(trace_path)
    assert event["gate_reasons"] == "ANALYSIS_UNKNOWN"


def test_the_gate_never_changes_what_the_auditor_reads_and_off_leaves_the_old_event(
    tmp_path: Path,
) -> None:
    """관측 전용이다. 같은 슬라이스를 (옛 슬라이스 · 사실 있음/꺼짐 · 사실 있음/켜짐)으로 돌려
    Auditor가 받은 프롬프트(본문·FACTS)가 셋 다 같음을 본다 — 꺼진 두 run의 이벤트도 같고, 켠
    run의 이벤트는 `gate_reasons` 하나만 더 든다(로더가 싣게 된 값이 꺼진 run의 입력·이벤트를
    못 바꾼다)."""
    for name in ("old", "cut"):
        (tmp_path / name).mkdir()
    old = _slices_file(tmp_path / "old", [_cut_case_b(with_facts=False)])
    cut = _slices_file(tmp_path / "cut", [_cut_case_b()])
    runs = {
        # ⏵ 2026-10-05 — `budgets.yaml`이 게이트를 1로 켜서 이 둘도 `gate_reasons`를 들고
        # 왔다. 「꺼진 run」이 이 검사의 전제라 명시로 0을 준다(기본값에 안 기댄다).
        "old": _run(tmp_path, "old", slices_path=old, before=_with_index(), configure=_gate(0)),
        "off": _run(tmp_path, "off", slices_path=cut, before=_with_index(), configure=_gate(0)),
        "on": _run(tmp_path, "on", slices_path=cut, before=_with_index(), configure=_gate(1)),
    }

    prompts = {name: _prompts(trace, "auditor") for name, (_, trace) in runs.items()}
    events = {name: _source_events(trace) for name, (_, trace) in runs.items()}

    assert prompts["old"] == prompts["off"] == prompts["on"]
    assert events["old"] == events["off"]
    assert all("gate_reasons" not in event for event in events["off"])
    (on_event,) = events["on"]
    assert "gate_reasons" in on_event
    assert {k: v for k, v in on_event.items() if k != "gate_reasons"} == events["off"][0]
