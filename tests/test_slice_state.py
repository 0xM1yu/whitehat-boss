"""`auditor_slice_state` — 슬라이스의 정적 상태(분석 종류 · degraded · 절단 · 경계 사유)를
Auditor 모델에 보인다 (이슈 #438 선택지 (b) · S20 Phase 14).

선택지 (a)(PR #448)는 그 신호를 로더가 싣고 `auditor_source` 이벤트의 `gate_reasons`로만
남겼다 - 모델 입력은 안 바꿨다. (b)는 **같은 정적 사실**을 FACTS의 opt-in 키 `slice_state`로
모델에 보인다(LLM이 만든 주장은 없다). 켜는 것은 측정 결정이다(D101 「닫히는 조건」의 켠 run ↔
끈 run 짝 비교) - 코드 기본 0 · `config/budgets.yaml` 0이다.

묶는 것:
1. 순수 함수 `slice_state.facts` - 「모름」은 `null`이지 `false`가 아니다(CLAUDE.md §0).
2. 옵션 - 코드 기본 0 · budgets 0 · 끄면 `flatten()`에 키가 없다.
3. `build_facts` - 안 주면 키가 없다(Auditor 밖의 역할은 바이트 그대로).
4. e2e(로더 → 러너 → 노드 → 프롬프트) - 끄면 슬라이스가 무엇을 들었든 Auditor 입력(SOURCE·FACTS)이
   같고, 켜면 신호마다 FACTS와 `auditor_source` 이벤트에 선다. 슬라이스 샘플은 저장소의 것이다
   (`tests/test_source_gate.py`의 절단 Case B 픽스처 — 슬라이서가 쓰는 모양 그대로, CLAUDE.md §2.5).
"""

import json
from pathlib import Path

import pytest

from slicer_source import cluster
from src.adapters.slices import load_slices, slice_refs
from src.config import AUDITOR_SOURCE_OPTIONS, Thresholds, load_config
from src.orchestrator import prompt_loader, slice_state
from src.schemas.candidate import SliceRef
from src.schemas.common import Location
from tests.test_pipeline_e2e import _run, _with_index
from tests.test_slice_targets import _prompts, _slices_file, _source_events
from tests.test_slices import _SOURCES, _backward, _document, _slice, _write
from tests.test_source_gate import _cut_case_b

_ENTRY_PARAMETER = ("entry_parameter", "libavdevice/libavformat/libavformat/avio.h", 540)
_ENTRY_PARAMETER_ROW = {
    "reason": "entry_parameter",
    "location": {"file": "libavdevice/libavformat/libavformat/avio.h", "line": 540},
}


# 슬라이서가 세 키를 다 적은 슬라이스(Case B `backward_ddg`의 모양) - `*_recorded`는 로더가 센다.
_RECORDED = {
    "truncated_recorded": True, "truncation_reasons_recorded": True, "boundaries_recorded": True,
}
# 상한 둘(`sinks_capped` · `cross_method_flow_truncated`)을 적지 않는 슬라이서 종류(Case B 역추적
# 등)의 모름 - `false`가 아니라 `null`이다.
_NO_CAPS = {"sinks_capped": None, "cross_method_flow_truncated": None}


def _ref(given_end: str = "sink", **fields) -> SliceRef:  # noqa: ANN003
    entry = Location(file="a.c", line_start=10, symbol="memcpy")
    return SliceRef(
        slice_id="s1", location=Location(file="a.c", line_start=10, symbol="f"),
        given_end=given_end, entry=entry, lines=(("a.c", 10),), **fields,
    )


# === 1. 순수 함수 ================================================================================


def test_a_slice_the_slicer_fully_described_reports_its_facts() -> None:
    state = slice_state.facts(_ref(analysis_kind="backward_ddg", **_RECORDED))

    assert state == {
        "analysis_kind": "backward_ddg", "degraded": False, "truncated": False,
        "truncation_reasons": [], "boundary_reasons": [], **_NO_CAPS,
    }


@pytest.mark.parametrize("given_end", ["sink", "source"])
def test_a_slice_without_analysis_facts_is_unknown_never_false(given_end: str) -> None:
    """`analysis_kind`가 비었다 = 슬라이서가 분석 사실을 안 적었다(옛 `slices.json`). 그러면
    `truncated=False`·경계 없음은 「안 잘렸다」가 아니라 「모른다」다 - 모르는 값을 `false`·`[]`로
    내면 다 본 슬라이스와 같은 문자열이 된다(CLAUDE.md §0)."""
    state = slice_state.facts(_ref(given_end, analysis_kind=""))

    assert state == {
        "analysis_kind": None, "degraded": None, "truncated": None,
        "truncation_reasons": None, "boundary_reasons": None, **_NO_CAPS,
    }
    assert all(value is None for value in state.values())


def test_a_flag_that_is_set_is_reported_even_when_the_analysis_kind_is_unknown() -> None:
    """모름은 `false`만 막는다 - 서 있는 신호(`True`·사유 있는 목록)는 분석 종류가 비어도 지운 채
    내지 않는다."""
    state = slice_state.facts(
        _ref(degraded=True, truncated=True, truncation_reasons=("backward_depth",),
             boundaries=(_ENTRY_PARAMETER,))
    )

    assert state == {
        "analysis_kind": None, "degraded": True, "truncated": True,
        "truncation_reasons": ["backward_depth"], "boundary_reasons": ["entry_parameter"],
        **_NO_CAPS,
    }


def test_the_anchor_only_fallback_is_degraded() -> None:
    state = slice_state.facts(_ref(analysis_kind="degraded_anchor_only", degraded=True))

    assert (state["analysis_kind"], state["degraded"]) == ("degraded_anchor_only", True)
    # 아무것도 적히지 않았으면 나머지는 모름이다 - `false`·`[]`가 아니다.
    assert (state["truncated"], state["truncation_reasons"], state["boundary_reasons"]) == (
        None, None, None,
    )


def test_a_cut_names_what_cut_it_and_case_a_cuts_without_a_reason_list() -> None:
    backward = slice_state.facts(
        _ref(analysis_kind="backward_ddg", truncated=True,
             truncation_reasons=("backward_depth", "backward_states"), **_RECORDED)
    )
    forward = slice_state.facts(_ref(
        "source", analysis_kind="parameter_paths", truncated=True, truncated_recorded=True,
    ))

    assert (backward["truncated"], backward["truncation_reasons"]) == (
        True, ["backward_depth", "backward_states"]
    )
    # Case A는 사유 목록을 안 적는다 - 절단은 알리고, 사유는 `[]`(없다)가 아니라 `null`(안
    # 적었다)이다.
    assert (forward["truncated"], forward["truncation_reasons"]) == (True, None)


def test_boundary_reasons_are_the_distinct_reasons_in_a_stable_order() -> None:
    """`entry_parameter`는 호출자가 **없다**는 뜻이 아니라 CPG가 호출 간선을 **못 이었다**는 뜻이다
    (그 풀이는 프롬프트가 든다). 여기서는 사유 철자만 슬라이서가 쓴 그대로 낸다."""
    boundaries = (
        ("dependency_root", "a.c", 5), _ENTRY_PARAMETER, ("dependency_root", "a.c", 9),
        ("external_or_unresolved_call", "a.c", 7),
    )

    state = slice_state.facts(_ref(analysis_kind="backward_ddg", boundaries=boundaries))

    assert state["boundary_reasons"] == [
        "dependency_root", "entry_parameter", "external_or_unresolved_call"
    ]


def test_the_event_fields_are_flat_scalars_and_keep_unknown_as_none() -> None:
    """`TraceValue`는 스칼라다 - 사유 목록은 쉼표로 이은 문자열이고(`gate_reasons`와 같은
    방식), 모름은 `None`이다(빈 문자열은 「사유가 없다」)."""
    known = slice_state.event_fields(slice_state.facts(
        _ref(analysis_kind="backward_ddg", truncated=True, truncation_reasons=("backward_depth",),
             boundaries=(_ENTRY_PARAMETER,))
    ))
    clean = slice_state.event_fields(
        slice_state.facts(_ref(analysis_kind="backward_ddg", **_RECORDED))
    )
    unknown = slice_state.event_fields(slice_state.facts(_ref()))

    assert known == {
        "slice_state_analysis_kind": "backward_ddg", "slice_state_degraded": False,
        "slice_state_truncated": True, "slice_state_truncation_reasons": "backward_depth",
        "slice_state_boundary_reasons": "entry_parameter",
        "slice_state_sinks_capped": None, "slice_state_cross_method_flow_truncated": None,
    }
    assert clean["slice_state_truncation_reasons"] == ""
    assert clean["slice_state_boundary_reasons"] == ""
    assert set(unknown.values()) == {None}
    assert known["slice_state_sinks_capped"] is None
    assert known["slice_state_cross_method_flow_truncated"] is None


# === 2. 옵션 =====================================================================================


def test_the_option_is_a_declared_source_option_and_off_by_default_in_code() -> None:
    assert "auditor_slice_state" in AUDITOR_SOURCE_OPTIONS
    assert Thresholds.model_fields["auditor_slice_state"].default == 0
    with pytest.raises(ValueError):
        load_config("harness_pool").thresholds.transition(auditor_slice_state=2)


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid", "dev_fake"])
def test_the_committed_config_leaves_it_off_until_a_paired_run_decides(profile: str) -> None:
    """켜는 것은 측정 결정이다 - D101의 닫히는 조건이 켠 run ↔ 끈 run 짝 비교를 요구한다."""
    thresholds = load_config(profile).thresholds

    assert thresholds.auditor_slice_state == 0
    assert "auditor_slice_state" not in thresholds.flatten(), "꺼진 run의 manifest 모양이 그대로다"
    assert "auditor_slice_state" not in thresholds.auditor_source_options_on()
    assert "auditor_slice_state" in thresholds.transition(auditor_slice_state=1).flatten()


# === 3. build_facts ==============================================================================


def test_the_facts_key_is_declared_and_absent_unless_given() -> None:
    assert "slice_state" in prompt_loader.FACT_KEYS
    plain = prompt_loader.build_facts(run_id="r", ids={})
    given = prompt_loader.build_facts(
        run_id="r", ids={}, slice_state={"analysis_kind": None, "truncated": None}
    )

    assert "slice_state" not in plain
    assert given["slice_state"] == {"analysis_kind": None, "truncated": None}
    assert {k: v for k, v in given.items() if k != "slice_state"} == plain
    # `None`을 `null`로 싣지 않는다 - 안 준 것과 모르는 것이 갈린다(키 없음 ≠ 값이 `null`인 키).
    assert "slice_state" not in prompt_loader.build_facts(run_id="r", ids={}, slice_state=None)


# === 4. e2e: 로더 → 러너 → 노드 → 프롬프트 ========================================================


def _slice_state_option(value: int):  # noqa: ANN202
    def configure(config):  # noqa: ANN001, ANN202
        return config.transition(
            thresholds=config.thresholds.transition(auditor_slice_state=value)
        )

    return configure


def _auditor_input(tmp_path: Path, name: str, item: dict, value: int):  # noqa: ANN202
    path = _slices_file(tmp_path, [item])
    _, trace_path = _run(
        tmp_path, name, slices_path=path, before=_with_index(),
        configure=_slice_state_option(value),
    )
    ((body, facts),) = _prompts(trace_path, "auditor")
    (event,) = _source_events(trace_path)
    return body, facts, event, trace_path


def test_with_the_option_off_the_auditor_input_is_the_same_whatever_the_slice_carries(
    tmp_path: Path,
) -> None:
    """끄면(기본) FACTS에 키가 없고 Auditor 입력 전체(본문 = SOURCE·FACTS)와
    `auditor_source` 이벤트가 절단·경계를 든 슬라이스에서도 안 든 슬라이스와 **바이트 동일**이다
    - 로더가 싣게 된 값이 기본 경로로 새지 않는다(D101 「닫히는 조건」)."""
    cut_body, cut_facts, cut_event, _ = _auditor_input(tmp_path, "cut", _cut_case_b(), 0)
    old_body, old_facts, old_event, _ = _auditor_input(
        tmp_path, "old", _cut_case_b(with_facts=False), 0
    )

    assert "slice_state" not in cut_facts and "slice_state" not in old_facts
    assert cut_body == old_body
    # `gate_reasons`(`auditor_source_gate` 1 - 관측 전용)는 슬라이스가 든 것을 사유로 남기는 이벤트
    # 필드라 둘이 다르다. 이 옵션이 만드는 차이가 아니므로 뺀다.
    assert {k: v for k, v in cut_event.items() if k != "gate_reasons"} == {
        k: v for k, v in old_event.items() if k != "gate_reasons"
    }
    assert not [key for key in cut_event if key.startswith("slice_state")]


def test_with_the_option_on_each_signal_reaches_the_facts_and_the_event(tmp_path: Path) -> None:
    """절단 Case B(슬라이서가 쓴 모양 그대로): `backward_ddg` · 절단 사유 둘 · 호출 간선 없는
    파라미터 경계. FACTS의 값과 이벤트의 값이 같은 것에서 나온다 - 이벤트는 **모델에게 보인
    것**이다."""
    _, facts, event, _ = _auditor_input(tmp_path, "on", _cut_case_b(), 1)

    assert facts["slice_state"] == {
        "analysis_kind": "backward_ddg", "degraded": False, "truncated": True,
        "truncation_reasons": ["backward_depth", "backward_states"],
        "boundary_reasons": ["entry_parameter"], **_NO_CAPS,
    }
    assert {k: v for k, v in event.items() if k.startswith("slice_state")} == {
        "slice_state_analysis_kind": "backward_ddg", "slice_state_degraded": False,
        "slice_state_truncated": True,
        "slice_state_truncation_reasons": "backward_depth,backward_states",
        "slice_state_boundary_reasons": "entry_parameter",
        "slice_state_sinks_capped": None, "slice_state_cross_method_flow_truncated": None,
    }


def test_with_the_option_on_an_old_slice_says_unknown_not_false(tmp_path: Path) -> None:
    """슬라이서가 분석 사실을 안 적은 슬라이스(`analysis_kind` 없음)는 FACTS도 이벤트도
    모름(`null`)이다 - `truncated: false`가 아니다."""
    _, facts, event, _ = _auditor_input(tmp_path, "old", _cut_case_b(with_facts=False), 1)

    assert facts["slice_state"] == {
        "analysis_kind": None, "degraded": None, "truncated": None,
        "truncation_reasons": None, "boundary_reasons": None, **_NO_CAPS,
    }
    assert event["slice_state_truncated"] is None
    assert event["slice_state_analysis_kind"] is None


def test_with_the_option_on_only_the_auditor_facts_change(tmp_path: Path) -> None:
    """opt-in 키는 Auditor 자리만 받는다 - 다른 역할의 FACTS 바이트는 옵션과 무관하다(켜도 Scout·
    Critic·PoV 프롬프트에 `slice_state`가 없다)."""
    path = _slices_file(tmp_path, [_cut_case_b()])
    _, trace_path = _run(
        tmp_path, "on", slices_path=path, before=_with_index(),
        configure=_slice_state_option(1),
    )

    seen = {}
    for role in ("scout", "critic", "pov", "auditor"):
        seen[role] = [facts for _, facts in _prompts(trace_path, role)]
    assert seen["auditor"] and all("slice_state" in facts for facts in seen["auditor"])
    for role in ("scout", "critic", "pov"):
        assert all("slice_state" not in facts for facts in seen[role]), role
    assert sum(len(v) for v in seen.values()) > len(seen["auditor"]), "다른 역할도 돌았다"


def test_the_trace_event_for_the_off_run_is_json_with_no_slice_state_keys(tmp_path: Path) -> None:
    _, _, _, trace_path = _auditor_input(tmp_path, "off", _cut_case_b(), 0)

    text = trace_path.read_text(encoding="utf-8")
    assert "slice_state" not in text
    assert all(json.loads(line) for line in text.splitlines() if line.strip())


def test_with_the_option_on_a_run_without_slices_adds_no_key(tmp_path: Path) -> None:
    """슬라이스 없는 후보(`--slices` 없는 run)는 상태를 낼 근거가 없다 - 모르는 것을 `null` 일곱으로
    채워 싣지 않고 키 자체가 없다(「안 줬다」와 「슬라이스가 모른다」는 다른 신호다)."""
    _, trace_path = _run(tmp_path, "noslice", configure=_slice_state_option(1))

    prompts = _prompts(trace_path, "auditor")
    assert prompts, "Auditor가 돌았다"
    assert all("slice_state" not in facts for _, facts in prompts)
    assert _source_events(trace_path) == [], "슬라이스 없는 후보는 `auditor_source`도 안 낸다"


# === 5. 로더 → 상태: 슬라이서가 그 키를 적었는가 ==================================================
# 슬라이서 종류마다 어느 키를 쓰는지가 다르다. 아래 `analysis`는 슬라이서 소스가 쓰는 모양 그대로다
# (각 줄 옆의 소스 핀이 그 모양이 아직 소스에 있음을 묻는다 - 지어낸 샘플이 아니다).

_REPO = Path(__file__).resolve().parent.parent


def _loaded_state(tmp_path: Path, item: dict) -> dict:
    refs = slice_refs(load_slices(_write(tmp_path, _document([item]))))
    return slice_state.facts(refs[0])


def test_case_a_parameter_paths_records_neither_reasons_nor_boundaries(tmp_path: Path) -> None:
    """Case A의 flows 질의는 `flow_count`·`truncated`·`sinks_capped`·`sinks_kept`·`lines`를 내고
    사유·경계는 안 낸다(`slicer_queries.flows_query`). 그러므로 `truncated`는 적혔지만 사유·경계는
    `[]`(없다)가 아니라 `null`(안 적었다)이다."""
    queries = (_REPO / "tools/slicer/slicer_queries.py").read_text(encoding="utf-8")
    assert '"sinks_capped" -> sinksCapped, "sinks_kept" -> sinks.size,' in queries
    assert "truncation_reasons" not in queries and "boundaries" not in queries
    item = _slice(
        "slice_0001", "A", {("main.c", 1)}, _SOURCES, truncated=True,
        analysis_kind="parameter_paths",
        analysis={"flow_count": 10, "truncated": True, "sinks_capped": True, "sinks_kept": 2063,
                  "lines": [{"file": "main.c", "line": 1}]},
    )

    assert _loaded_state(tmp_path, item) == {
        "analysis_kind": "parameter_paths", "degraded": False, "truncated": True,
        "truncation_reasons": None, "boundary_reasons": None,
        "sinks_capped": True, "cross_method_flow_truncated": None,
    }


def test_case_b_backward_ddg_records_both_and_empty_means_none(tmp_path: Path) -> None:
    """Case B 역추적은 `truncation_reasons`·`boundaries`를 늘 적는다(`slicer_backward.py`) - 비어
    있으면 「적었는데 없다」라 `[]`다."""
    backward = (_REPO / "tools/slicer/slicer_backward.py").read_text(encoding="utf-8")
    assert '"boundaries" -> ujson.Arr.from(boundaries' in backward
    assert '"truncation_reasons" -> ujson.Arr.from(cuts' in backward
    clean = _slice(
        "slice_0001", "B", {("main.c", 1)}, _SOURCES, truncated=False,
        analysis_kind="backward_ddg", analysis=_backward(),
    )
    cut = _slice(
        "slice_0002", "B", {("main.c", 1)}, _SOURCES, truncated=True,
        analysis_kind="backward_ddg",
        analysis=_backward(truncated=True, truncation_reasons=["call_depth"],
                           boundaries=[_ENTRY_PARAMETER_ROW]),
    )

    assert _loaded_state(tmp_path, clean) == {
        "analysis_kind": "backward_ddg", "degraded": False, "truncated": False,
        "truncation_reasons": [], "boundary_reasons": [], **_NO_CAPS,
    }
    assert _loaded_state(tmp_path, cut) == {
        "analysis_kind": "backward_ddg", "degraded": False, "truncated": True,
        "truncation_reasons": ["call_depth"], "boundary_reasons": ["entry_parameter"], **_NO_CAPS,
    }


def test_the_anchor_only_fallback_records_no_boundaries(tmp_path: Path) -> None:
    """`degraded_slice`의 `analysis`는 `lines`·`flow_count`·`truncated`·`degraded`뿐이다 - 경계도
    사유도 없다(`slicer_pipeline.py`). 둘 다 `null`이다."""
    pipeline = (_REPO / "tools/slicer/slicer_pipeline.py").read_text(encoding="utf-8")
    shape = '"analysis": {"lines": [], "flow_count": 0, "truncated": False, "degraded": True},'
    assert shape in pipeline
    item = _slice(
        "slice_0001", "B", {("main.c", 1)}, _SOURCES, truncated=False, degraded=True,
        analysis_kind="degraded_anchor_only",
        analysis={"lines": [], "flow_count": 0, "truncated": False, "degraded": True},
    )

    assert _loaded_state(tmp_path, item) == {
        "analysis_kind": "degraded_anchor_only", "degraded": True, "truncated": False,
        "truncation_reasons": None, "boundary_reasons": None, **_NO_CAPS,
    }


def test_the_targeted_degraded_fallback_records_boundaries_but_no_reasons(tmp_path: Path) -> None:
    """대상 지정 모드의 `degraded_lexical`은 `boundaries: []`를 적고 `truncation_reasons`는 안
    적는다(`slicer_target.py`) - 한 슬라이스 안에서도 키마다 갈린다."""
    targeted = (_REPO / "tools/slicer_v2/slicer_target.py").read_text(encoding="utf-8")
    assert '"analysis_kind": "degraded_lexical",' in targeted
    assert '"boundaries": [],\n                "target_reached": False,' in targeted
    item = _slice(
        "slice_0001", "B", {("main.c", 1)}, _SOURCES, truncated=False, degraded=True,
        analysis_kind="degraded_lexical",
        analysis={"lines": [], "flow_count": None, "truncated": False, "degraded": True,
                  "boundaries": [], "target_reached": False, "reason": "no match"},
    )

    state = _loaded_state(tmp_path, item)
    assert (state["boundary_reasons"], state["truncation_reasons"]) == ([], None)


def test_a_missing_truncated_key_is_unknown_even_for_a_known_kind(tmp_path: Path) -> None:
    """로더는 없는 `truncated`를 `False`로 채운다(`slices._flag`). 그 기본값을 「안 잘렸다」로 내면
    안 된다 - 키가 없으면 `null`이다."""
    item = _slice(
        "slice_0001", "B", {("main.c", 1)}, _SOURCES, analysis_kind="backward_ddg",
        analysis=_backward(),
    )
    assert "truncated" not in item

    state = _loaded_state(tmp_path, item)

    assert state["truncated"] is None
    assert (state["truncation_reasons"], state["boundary_reasons"]) == ([], [])


def test_a_merged_slice_records_a_key_when_any_member_wrote_it(tmp_path: Path) -> None:
    """클러스터가 합친 슬라이스의 분석은 구성원마다 있다(`member_analysis`) - 대표가 안 적었어도
    흡수된 구성원이 적었으면 적힌 것이다(합친 `truncated`가 구성원의 OR인 것과 같은 길)."""
    def member(sid: str, points: set, **analysis) -> dict:
        return _slice(
            sid, "B", points, _SOURCES, flow_count=None, truncated=False,
            analysis_kind="backward_ddg", analysis=analysis,
            line_provenance={"data_dependency": [], "context": [],
                             "candidate_anchor": [list(p) for p in sorted(points)]},
        )

    bare = member("slice_0001", {("main.c", n) for n in range(1, 8)}, lines=[], truncated=False)
    full = member("slice_0002", {("main.c", 5), ("main.c", 6)}, **_backward())
    (merged,) = cluster([bare, full], 0.95, _SOURCES)
    assert "member_analysis" in merged
    assert "boundaries" not in merged["analysis"], "대표는 사유·경계를 안 적었다"

    state = _loaded_state(tmp_path, merged)

    assert (state["truncation_reasons"], state["boundary_reasons"]) == ([], [])


def _cluster_member(sid: str, case: str, points: set, kind: str, analysis: dict) -> dict:
    return _slice(
        sid, case, points, _SOURCES, flow_count=None, truncated=False, analysis_kind=kind,
        analysis=analysis,
        line_provenance={"data_dependency": [], "context": [],
                         "candidate_anchor": [list(p) for p in sorted(points)]},
    )


_BIG = {("main.c", n) for n in range(1, 8)}  # 대표(줄이 더 많다)
_SMALL = {("main.c", 5), ("main.c", 6)}  # 대표에 흡수된다


@pytest.mark.parametrize("order", ["big_first", "small_first"])
def test_a_merged_slice_whose_representative_recorded_keeps_it_over_a_bare_member(
    tmp_path: Path, order: str
) -> None:
    """위 핀의 반대 순서: **대표는 사유·경계를 적었고 흡수된 구성원은 안 적었다**(`backward_ddg`
    대표가 `degraded_anchor_only`를 흡수하는 모양 - 후자는 `analysis`에 둘 다 없다). 구성원 중
    하나라도 적었으면 적힌 것이라 `[]`·`[]`다. 마지막 구성원의 값만 읽으면 `null`이 돼 죽는다."""
    big = _cluster_member("slice_0001", "B", _BIG, "backward_ddg", _backward())
    small = _cluster_member(
        "slice_0002", "B", _SMALL, "degraded_anchor_only",
        {"lines": [], "flow_count": 0, "truncated": False, "degraded": True},
    )
    (merged,) = cluster([big, small] if order == "big_first" else [small, big], 0.95, _SOURCES)
    assert "boundaries" in merged["analysis"], "대표가 적었다"
    assert set(merged["member_analysis"]) == {"key_slice_0001", "key_slice_0002"}

    state = _loaded_state(tmp_path, merged)

    assert (state["truncation_reasons"], state["boundary_reasons"]) == ([], [])


# === 6. 상한 둘: 슬라이서가 적은 종류만 true/false, 아니면 null ==================================


@pytest.mark.parametrize("capped", [True, False])
def test_case_a_reports_the_sink_cap_it_recorded_true_or_false(
    tmp_path: Path, capped: bool
) -> None:
    """Case A flows 질의는 `sinks_capped`를 **늘** 적는다(`slicer_queries.py`) - 안 잘렸으면
    `false`다. 잘렸는데 `truncated`가 `false`인 슬라이스가 모델에게 `truncated: false`만
    보이던 구멍(리뷰 MEDIUM-1)을 닫는다. `cross_method_flow_truncated`는 이 종류가 안
    적으니 `null`이다."""
    queries = (_REPO / "tools/slicer/slicer_queries.py").read_text(encoding="utf-8")
    assert '"sinks_capped" -> sinksCapped, "sinks_kept" -> sinks.size,' in queries
    item = _slice(
        "slice_0001", "A", {("main.c", 1)}, _SOURCES, truncated=False,
        analysis_kind="parameter_paths",
        analysis={"flow_count": 3, "truncated": False, "sinks_capped": capped,
                  "sinks_kept": 2000 if capped else 12, "lines": [{"file": "main.c", "line": 1}]},
    )

    state = _loaded_state(tmp_path, item)

    assert state["truncated"] is False, "흐름 수 상한에는 안 닿았다 - 그래서 별도 키가 필요하다"
    assert state["sinks_capped"] is capped
    assert state["cross_method_flow_truncated"] is None


@pytest.mark.parametrize("capped", [True, False])
def test_the_targeted_mode_reports_its_cross_method_cap_true_or_false(
    tmp_path: Path, capped: bool
) -> None:
    """대상 지정 모드(`tools/slicer_v2`)는 `cross_method_flow_truncated`를 적는다. `sinks_capped`는
    안 적는 종류라 `null`이다."""
    targeted = (_REPO / "tools/slicer_v2/slicer_target.py").read_text(encoding="utf-8")
    assert '"cross_method_flow_truncated" -> (crossEdges.size > crossEdgeLimit),' in targeted
    item = _slice(
        "slice_0001", "B", {("main.c", 1)}, _SOURCES, truncated=False,
        analysis_kind="forward_ddg",
        analysis=_backward(cross_method_flow=[], cross_method_flow_truncated=capped),
    )

    state = _loaded_state(tmp_path, item)

    assert state["cross_method_flow_truncated"] is capped
    assert state["sinks_capped"] is None


def test_a_slice_that_records_neither_cap_reports_null_not_false(tmp_path: Path) -> None:
    """키가 없는 분석(Case B 역추적 · 옛 슬라이스 · 대체 슬라이스)은 「안 잘렸다」가 아니라
    모름이다 - 로더가 없는 불리언을 `False`로 채운다(`slices._flag`)는 사실이 새면 안 된다. 서 있는
    신호는 적었는지와 무관하게 지우지 않는다(`SliceRef`를 직접 만든 순수 함수 쪽)."""
    backward = _slice(
        "slice_0001", "B", {("main.c", 1)}, _SOURCES, truncated=False,
        analysis_kind="backward_ddg", analysis=_backward(),
    )
    old = _slice("slice_0002", "A", {("main.c", 1)}, _SOURCES)

    for item in (backward, old):
        state = _loaded_state(tmp_path, item)
        assert (state["sinks_capped"], state["cross_method_flow_truncated"]) == (None, None)
    standing = slice_state.facts(_ref(sinks_capped=True, cross_method_flow_truncated=True))
    assert (standing["sinks_capped"], standing["cross_method_flow_truncated"]) == (True, True)


@pytest.mark.parametrize(
    ("key", "case", "kind", "extra"),
    [
        ("sinks_capped", "A", "parameter_paths", {"flow_count": 1, "truncated": False,
                                                    "sinks_kept": 9, "lines": []}),
        ("cross_method_flow_truncated", "B", "backward_ddg", _backward()),
    ],
)
@pytest.mark.parametrize("order", ["big_first", "small_first"])
@pytest.mark.parametrize(
    ("big_value", "small_value", "expected"),
    [
        (None, False, False),   # 대표는 안 적고 흡수된 쪽이 적었다 - 적힌 것이다
        (False, None, False),   # 대표가 적고 흡수된 쪽이 안 적었다 - 마지막 값만 읽으면 null이 된다
        (None, True, True),
        (True, None, True),
        (True, False, True),    # OR
        (None, None, None),     # 둘 다 안 적었다 - 모름
    ],
)
def test_a_merged_slice_ors_each_cap_over_its_members_and_records_it_if_any_did(
    tmp_path: Path, key: str, case: str, kind: str, extra: dict, order: str,
    big_value: bool | None, small_value: bool | None, expected: bool | None,
) -> None:
    """합친 슬라이스의 `analysis`는 대표의 것이고 구성원별 분석은 `member_analysis`에 있다 - 상한도
    `truncated`·사유·경계와 같은 길이다: 값은 구성원의 OR, 적었는지는 하나라도 적었으면 참."""
    def member(sid: str, points: set, value: bool | None) -> dict:
        analysis = {**extra, **({} if value is None else {key: value})}
        return _cluster_member(sid, case, points, kind, analysis)

    big = member("slice_0001", _BIG, big_value)
    small = member("slice_0002", _SMALL, small_value)
    (merged,) = cluster([big, small] if order == "big_first" else [small, big], 0.95, _SOURCES)
    assert set(merged["member_analysis"]) == {"key_slice_0001", "key_slice_0002"}

    assert _loaded_state(tmp_path, merged)[key] is expected
