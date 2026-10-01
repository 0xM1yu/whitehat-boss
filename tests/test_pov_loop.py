"""PoV 툴 루프(Option C)의 계약 — 루프 제어 스키마 `PovAction`부터.

council(적대적 3인) + 사용자 확정 = Option C(2026-09-30): 모델은 매 홉 `PovAction` 하나를
낸다. `gen`은 **기존 `PovSpec`을 실어** 순수 `assemble_poc`가 인프로세스로 바이트를 만든다
(새 gen 샌드박스 도구 없음). `inspect`는 진단 도구(asan/build/poc)를 돌리고, `submit`은 루프를
끝낸다. raw 임의 바이트는 `PovSpec`의 `literal` 세그먼트가 이미 덮는다.

아래는 스키마 validator + 루프(`_run_pov_hops`)의 계약을 함께 본다.

이 파일은 스키마 validator부터 검증한다 — `PovSegment._check_kind`가 다른 kind의 필드
혼입을 거절하듯(test_poc_assembly), `PovAction`도 액션별로 쓰지 않는 필드가 실리면 거절해야
한다(조용히 무시하면 오타·혼동이 통과한다).
"""

import dataclasses
from typing import get_args

import pytest
from pydantic import ValidationError

from src.adapters.fake_tools import TOOL_KINDS
from src.interfaces.model_client import ModelGatewayError, ModelResult, SchemaParseError, Usage
from src.orchestrator import runner
from src.orchestrator.context import RunContext
from src.orchestrator.trace import EventKind
from src.schemas.enums import PovStopReason, Role
from src.schemas.pov import PovAction, PovSegment, PovSpec

# `ctx`는 dev_fake로 배선된 픽스처, `_pov_state`는 finding+캡슐을 담은 state 헬퍼다
# (test_ne_loop이 test_pipeline_e2e에서 헬퍼를 끌어 쓰는 것과 같은 방식).
from tests.test_nodes import _pov_state, ctx  # noqa: F401


def _spec() -> PovSpec:
    return PovSpec(
        segments=[PovSegment(kind="repeat", byte=0x41, count=8)],
        rationale="test spec",
    )


def test_gen_action_carries_a_spec() -> None:
    action = PovAction(action="gen", spec=_spec(), rationale="try overflow")
    assert action.action == "gen"
    assert action.spec is not None
    assert action.tool is None


def test_inspect_action_carries_a_tool() -> None:
    action = PovAction(action="inspect", tool="build", rationale="inspect crash")
    assert action.action == "inspect"
    assert action.tool == "build"
    assert action.spec is None


def test_submit_action_carries_neither_spec_nor_tool() -> None:
    action = PovAction(action="submit", rationale="candidate reaches the bug")
    assert action.action == "submit"
    assert action.spec is None
    assert action.tool is None


def test_gen_requires_a_spec() -> None:
    with pytest.raises(ValidationError):
        PovAction(action="gen", rationale="no spec")


def test_gen_rejects_a_stray_tool() -> None:
    # gen이 쓰는 필드는 spec뿐 — tool이 섞이면 거절(조용히 무시 금지).
    with pytest.raises(ValidationError):
        PovAction(action="gen", spec=_spec(), tool="asan", rationale="mixed")


def test_inspect_requires_a_tool() -> None:
    with pytest.raises(ValidationError):
        PovAction(action="inspect", rationale="no tool")


def test_inspect_rejects_a_stray_spec() -> None:
    with pytest.raises(ValidationError):
        PovAction(action="inspect", tool="build", spec=_spec(), rationale="mixed")


def test_submit_rejects_a_stray_spec() -> None:
    with pytest.raises(ValidationError):
        PovAction(action="submit", spec=_spec(), rationale="mixed")


def test_submit_rejects_a_stray_tool() -> None:
    with pytest.raises(ValidationError):
        PovAction(action="submit", tool="poc", rationale="mixed")


def test_rationale_is_required_and_non_empty() -> None:
    with pytest.raises(ValidationError):
        PovAction(action="submit", rationale="")


# --- 루프 — `_run_ne_hops`를 미러한 모델-주도 PoV 툴 루프 --------------------------
#
# dev_fake는 hop0 gen → hop1 submit을 낸다(`fake_model._build_pov_action`이 history 길이로
# 홉을 가른다). 정지 사유·분기 각각은 스크립트한 모델로 확인한다(`test_ne_loop`의 방식).


def _gen(*, count: int = 8, rationale: str = "try overflow") -> PovAction:
    """서로 다른 gen 액션 — `count`로 세그먼트를 갈라 **바이트**가 달라진다(REPEAT 회피)."""
    return PovAction(
        action="gen",
        spec=PovSpec(
            segments=[PovSegment(kind="repeat", byte=0x41, count=count)],
            rationale=f"spec-{count}",
        ),
        rationale=rationale,
    )


def _gen_same_bytes(rationale: str) -> PovAction:
    """바이트는 같고 `rationale`만 다른 gen — REPEAT 판정이 rationale에 안 흔들리는지 본다."""
    return PovAction(
        action="gen",
        spec=PovSpec(
            segments=[PovSegment(kind="repeat", byte=0x41, count=8)],
            rationale=rationale,
        ),
        rationale=rationale,
    )


class _ScriptedModel:
    """홉마다 정해진 `PovAction`(또는 예외)을 낸다. 목록이 다하면 마지막 것을 반복한다.

    `history`를 홉마다 기록해 되묻기 사슬(user body + assistant raw)이 실제로 쌓이는지 본다."""

    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)
        self._i = 0
        self.prompts: list[tuple[Role, object]] = []
        self.histories: list[tuple[object, ...]] = []

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        self.prompts.append((role, prompt))
        self.histories.append(tuple(history))
        action = self._actions[min(self._i, len(self._actions) - 1)]
        self._i += 1
        if isinstance(action, Exception):
            raise action
        return ModelResult(
            value=action,
            usage=Usage(input_tokens=1, output_tokens=1, cached_tokens=0),
            model="scripted",
            raw_text=action.model_dump_json(),
        )

    def last_for(self, role: Role):  # noqa: ANN201
        return next(p for seen, p in reversed(self.prompts) if seen is role)


def _scripted(ctx: RunContext, actions: list[object]) -> RunContext:  # noqa: F811
    return dataclasses.replace(ctx, models=_ScriptedModel(actions))


def test_every_pov_tool_is_a_real_tool() -> None:
    """`PovAction.tool` Literal의 모든 값은 ToolRunner 화이트리스트(`TOOL_KINDS`) 안이어야 한다.

    없는 도구를 광고하면(예: 예전의 `gdb`) inspect가 tool_policy로 죽고 홉을 버린다 — 이 검사가
    그 회귀를 잡는다(적대적 리뷰 2026-09-30, gdb 있을 때 빨개졌을 것)."""
    tools: set[str] = set()
    for arg in get_args(PovAction.model_fields["tool"].annotation):
        tools |= set(get_args(arg))  # Literal[...]의 값들; NoneType은 get_args가 빈 튜플
    assert tools, "PovAction.tool Literal에서 값을 못 뽑았다"
    assert tools <= set(TOOL_KINDS), f"화이트리스트 밖 pov 도구: {sorted(tools - set(TOOL_KINDS))}"


def _hops(ctx: RunContext) -> list[object]:  # noqa: F811
    return [e for e in ctx.trace.events if e.kind is EventKind.POV_HOP]


def test_pov_loop_runs_gen_then_submit_and_returns_the_last_poc(ctx: RunContext) -> None:  # noqa: F811
    """dev_fake: hop0 gen → hop1 submit. 마지막 gen의 PoC를 돌려주고 SUBMITTED로 선다.

    POV_HOP가 홉마다(0=gen, 1=submit) 나고, submit 홉이 stop_reason=submitted다. 최종
    POV_GENERATED **하나**가 그 PoC 바이트 크기를 싣는다(submit 종단만 GENERATED)."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(ctx, state, finding, capsule)

    assert reason is PovStopReason.SUBMITTED
    assert isinstance(poc, bytes) and len(poc) > 0
    hops = _hops(ctx)
    assert [h.data["hop"] for h in hops] == [0, 1]
    assert [h.data["action"] for h in hops] == ["gen", "submit"]
    assert hops[1].data["stop_reason"] == PovStopReason.SUBMITTED.value
    gen = [e for e in ctx.trace.events if e.kind is EventKind.POV_GENERATED]
    assert len(gen) == 1 and gen[0].data["poc_bytes"] == len(poc)  # 종단 이벤트 정확히 하나
    assert not any(e.kind is EventKind.POV_SKIPPED for e in ctx.trace.events)


def test_pov_loop_feeds_the_exact_tool_observation_into_the_next_hop(
    ctx: RunContext,  # noqa: F811
) -> None:
    """gen 홉의 도구 결과가 **그대로** 다음 홉 프롬프트로 되먹임된다.

    hop1 body를 hop0 POV_HOP 이벤트(tool·tool_ok·exit_code·evidence_kind)에서 재구성한 관측과
    **정확히** 대조한다 — `ok=`가 있나 정도의 동어반복이 아니라, 관측이 실제 Evidence를
    반영하는지 본다(적대적 리뷰: ok 하드코딩·action 오기·상수 되먹임을 잡는다)."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    before = sum(1 for e in ctx.trace.events if e.kind is EventKind.TOOL_CALL)

    runner._run_pov_hops(ctx, state, finding, capsule)

    after = sum(1 for e in ctx.trace.events if e.kind is EventKind.TOOL_CALL)
    assert after - before >= 1, "gen 홉이 도구를 안 불렀다(루프 안 TOOL_CALL 0)"
    hop0 = _hops(ctx)[0]
    expected = (
        f"tool={hop0.data['tool']} action=gen ok={hop0.data['tool_ok']} "
        f"exit={hop0.data['exit_code']} kind={hop0.data['evidence_kind']}"
    )
    povs = [p for role, p in ctx.models.prompts if role is Role.POV]
    assert len(povs) == 2
    assert povs[1].body == expected


def test_pov_loop_observation_reflects_a_failing_tool(ctx: RunContext) -> None:  # noqa: F811
    """도구가 ok=False를 내면 되먹임 관측도 ok=False다 — 관측이 결과를 하드코딩하지 않는다.

    workdir를 없는 경로로 두면 fake poc 도구가 `ok=False`를 낸다(`_run_repro` 소스 없음)."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    state.workdirs[finding.finding_id] = "no_such_target_dir"

    runner._run_pov_hops(ctx, state, finding, capsule)

    povs = [p for role, p in ctx.models.prompts if role is Role.POV]
    assert len(povs) == 2
    assert "ok=False" in povs[1].body


def test_pov_loop_threads_history_across_hops(ctx: RunContext) -> None:  # noqa: F811
    """hop1은 hop0의 (user body, assistant raw) 두 턴을 history로 받는다(되묻기 사슬)."""
    scripted = _scripted(ctx, [_gen(count=8), PovAction(action="submit", rationale="done")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    runner._run_pov_hops(scripted, state, finding, capsule)

    model = scripted.models
    assert model.histories[0] == ()  # hop0은 사실 블록만
    hop1_history = model.histories[1]
    assert [t.speaker for t in hop1_history] == ["user", "assistant"]
    assert hop1_history[0].content == model.prompts[0][1].body  # hop0 user body
    assert hop1_history[1].content == _gen(count=8).model_dump_json()  # hop0 assistant raw


def test_pov_loop_stops_at_max_hop(ctx: RunContext) -> None:  # noqa: F811
    """모델이 매 홉 다른 gen만 내면(submit 없음) `max_hops`에서 선다 — 무한 gen을 막는다.

    홉 수는 정확히 `max_hops + 1`(hop0..max_hops)이고, submit이 아니므로 종단은 POV_SKIPPED
    (부분 PoC를 실어)다 — GENERATED가 아니다."""
    max_hops = ctx.config.thresholds.max_hops
    scripted = _scripted(ctx, [_gen(count=n) for n in range(1, max_hops + 5)])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.MAX_HOP
    assert isinstance(poc, bytes) and len(poc) > 0  # 마지막 gen의 PoC는 남는다
    hops = _hops(scripted)
    assert [h.data["hop"] for h in hops] == list(range(max_hops + 1))
    assert hops[-1].data["stop_reason"] == PovStopReason.MAX_HOP.value
    skipped = [e for e in scripted.trace.events if e.kind is EventKind.POV_SKIPPED]
    assert skipped and skipped[-1].data["reason"] == PovStopReason.MAX_HOP.value
    assert skipped[-1].data["poc_bytes"] == len(poc)  # 부분 PoC를 잃지 않는다
    assert not any(e.kind is EventKind.POV_GENERATED for e in scripted.trace.events)


def test_pov_loop_stops_on_a_repeated_poc(ctx: RunContext) -> None:  # noqa: F811
    """직전 gen과 같은 PoC 바이트를 또 내면 REPEAT_SPEC으로 선다."""
    scripted = _scripted(ctx, [_gen(count=8), _gen(count=8)])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.REPEAT_SPEC
    assert isinstance(poc, bytes) and len(poc) > 0  # hop0의 PoC는 남는다


def test_pov_loop_repeat_detection_ignores_rationale(ctx: RunContext) -> None:  # noqa: F811
    """같은 바이트를 `rationale`만 바꿔 재생성해도 REPEAT_SPEC이다 — 판정은 조립 바이트로 한다.

    적대적 리뷰(2026-09-30): spec JSON 비교는 바이트에 안 들어가는 rationale에 흔들려, 모델이
    설명만 바꾸면 반복을 못 잡고 MAX_HOP까지 헛돌았다. 바이트 비교로 고쳤다."""
    scripted = _scripted(ctx, [_gen_same_bytes("first wording"), _gen_same_bytes("second wording")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    _, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.REPEAT_SPEC


def _gen_literal_same_bytes(rationale: str) -> PovAction:
    """`_gen(count=8)`과 **같은 8바이트**(b"AAAAAAAA")를 `literal` hex로 만드는 gen — 세그먼트
    종류가 repeat이 아니라 literal이라 spec JSON은 다르지만 조립 바이트는 같다."""
    return PovAction(
        action="gen",
        spec=PovSpec(
            segments=[PovSegment(kind="literal", hex="41" * 8)],
            rationale=rationale,
        ),
        rationale=rationale,
    )


def test_pov_loop_repeat_detection_catches_a_different_encoding(ctx: RunContext) -> None:  # noqa: F811
    """직전 gen과 **다른 인코딩**(repeat → literal)이라도 조립 **바이트**가 같으면 REPEAT_SPEC이다.

    `_gen(count=8)`은 repeat 세그먼트로, `_gen_literal_same_bytes`는 literal hex로 같은 8바이트
    (b"AAAAAAAA")를 만든다 — spec JSON은 다르다(kind·필드가 다름). 반복 판정을 spec 표현(세그먼트
    kind 등)으로 하면 이 둘을 못 잡지만, 조립 바이트로 하면 잡는다 — `_run_pov_hops` docstring이
    약속한 「다른 인코딩·같은 바이트도 잡는다」가 이것이다
    (`test_pov_loop_repeat_detection_ignores_rationale`은 같은 kind·다른 rationale만 본다).

    한계: literal↔repeat 두 kind만 본다(int 세그먼트·여러 세그먼트 조합은 안 본다). Fake 도구·
    dev_fake 경로만 돈다 — 트레이스 필드 전수가 아니다. 세 번째 액션 `submit`은 판정이 바이트가
    아니라 spec으로 새면 hop1에서 안 걸리고 hop2에서 그 submit으로 SUBMITTED가 되게 해, 변이가
    `_ScriptedModel`의 마지막-액션 반복에 가려 REPEAT_SPEC로 되살아나지 않게 못 박는다."""
    scripted = _scripted(
        ctx,
        [_gen(count=8), _gen_literal_same_bytes("literal wording"),
         PovAction(action="submit", rationale="done")],
    )
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    # hop1(literal)이 hop0(repeat)과 같은 바이트라 여기서 선다
    assert reason is PovStopReason.REPEAT_SPEC
    assert poc == b"AAAAAAAA"  # hop0(repeat)의 PoC가 최종 후보로 남는다


def test_pov_loop_runs_inspect_then_submit(ctx: RunContext) -> None:  # noqa: F811
    """inspect 홉이 도구를 돌리고 submit으로 끝난다. gen이 없으면 최종 PoC가 없어 POV_SKIPPED다."""
    scripted = _scripted(
        ctx, [PovAction(action="inspect", tool="asan", rationale="look"),
              PovAction(action="submit", rationale="done")]
    )
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.SUBMITTED
    assert poc is None  # gen이 없었다
    hops = _hops(scripted)
    assert [h.data["action"] for h in hops] == ["inspect", "submit"]
    assert hops[0].data["tool"] == "asan"  # inspect가 그 도구를 돌렸다
    skipped = [e for e in scripted.trace.events if e.kind is EventKind.POV_SKIPPED]
    assert skipped and skipped[-1].data["reason"] == PovStopReason.SUBMITTED.value
    assert not any(e.kind is EventKind.POV_GENERATED for e in scripted.trace.events)


def test_pov_loop_absorbs_a_hop0_parse_error(ctx: RunContext) -> None:  # noqa: F811
    """hop0 스키마 파싱 실패는 흡수해 PARSE_FAILED로 서고 pov_skipped를 남긴다(finding 안 죽임)."""
    scripted = _scripted(ctx, [SchemaParseError("bad pov action json")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.PARSE_FAILED
    assert poc is None
    skipped = [e for e in scripted.trace.events if e.kind is EventKind.POV_SKIPPED]
    assert skipped and skipped[-1].data["reason"] == PovStopReason.PARSE_FAILED.value
    assert not any(e.kind is EventKind.POV_GENERATED for e in scripted.trace.events)


def test_pov_loop_absorbs_a_hop1_failure_and_keeps_the_partial_poc(ctx: RunContext) -> None:  # noqa: F811
    """hop≥1 실패도 흡수한다. hop0 gen이 만든 부분 PoC는 POV_SKIPPED에 실려 남는다(안 잃는다).

    NE 루프가 hop≥1 흡수를 따로 검증하듯, pov도 hop0 성공 + hop1 실패를 본다(적대적 리뷰:
    이 경로가 무검증이었다 — hop≥1 예외 미흡수 변이가 살아남았다)."""
    scripted = _scripted(ctx, [_gen(count=8), SchemaParseError("bad at hop1")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.PARSE_FAILED
    assert isinstance(poc, bytes) and len(poc) > 0  # hop0의 PoC를 잃지 않는다
    skipped = [e for e in scripted.trace.events if e.kind is EventKind.POV_SKIPPED]
    assert skipped and skipped[-1].data["reason"] == PovStopReason.PARSE_FAILED.value
    assert skipped[-1].data["poc_bytes"] == len(poc)
    assert not any(e.kind is EventKind.POV_GENERATED for e in scripted.trace.events)


def test_pov_loop_reraises_a_server_side_gateway_error(ctx: RunContext) -> None:  # noqa: F811
    """서버 쪽 사건(status 없음·5xx = not droppable)은 흡수하지 않고 올린다 — run의 사실이다."""
    scripted = _scripted(ctx, [ModelGatewayError("server down", status=None)])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    with pytest.raises(ModelGatewayError):
        runner._run_pov_hops(scripted, state, finding, capsule)


def test_pov_hop_join_key_matches_between_model_call_and_pov_hop(
    ctx: RunContext,  # noqa: F811
) -> None:
    """`PovHop.hop`(→ `ctx.call_model(hop=...)` → `model_call.data['hop']`)과 `POV_HOP.data['hop']`
    (러너 `_run_pov_hops`의 로컬 `hop` 변수)은 **같은 홉의 조인 키**라고 정본이 약속한다
    (`context.py`의 `model_call` 사슬 = (subject_id, role, hop) · `trace.py` `POV_HOP` = hop).
    두 값이 다른 변수(`prev.hop + 1` vs 로컬 카운터)에서 나와 어긋나도 루프 동작은 안 깨진다 —
    그래서 이 계약은 hop별로 두 이벤트의 `hop`을 직접 대조해야만 잡힌다."""
    scripted = _scripted(ctx, [_gen(count=8), PovAction(action="submit", rationale="done")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    runner._run_pov_hops(scripted, state, finding, capsule)

    pov_hops = [h.data["hop"] for h in _hops(scripted)]
    model_calls = [
        e.data["hop"]
        for e in scripted.trace.events
        if e.kind is EventKind.MODEL_CALL and e.data.get("role") == Role.POV.value
    ]
    assert pov_hops == [0, 1]
    assert model_calls == pov_hops, (
        f"model_call.hop={model_calls} != pov_hop.hop={pov_hops} — 같은 홉을 가리켜야 한다"
    )


def test_pov_hop_event_tool_ok_reflects_a_failing_tool_directly(ctx: RunContext) -> None:  # noqa: F811
    """`POV_HOP` 트레이스 이벤트 자신의 `tool_ok`가 실패한 도구 결과를 **그 필드에서 직접** 반영해야
    한다 — 모델에게 보내는 관측 문구(`ok=False` in body)가 맞는 것과는 별개다. 그 문구는
    `_pov_observation`이 같은 `evidence`에서 독립적으로 다시 만들므로, `_emit_pov_hop`이 자기
    `data['tool_ok']`를 하드코딩해도 문구 쪽 검사는 못 잡는다."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    state.workdirs[finding.finding_id] = "no_such_target_dir"

    runner._run_pov_hops(ctx, state, finding, capsule)

    hop0 = _hops(ctx)[0]
    assert hop0.data["action"] == "gen"
    # gen 홉은 `poc` 도구를 돌린다 — `POV_HOP.tool`이 그 도구를 **직접** 실어야 한다. gen의 도구를
    # `"build"`로 바꿔도 이 스위트의 두 workdir(정상 트리아지·없는 디렉터리)에서는 poc·build가 같은
    # 부울로 귀결돼 `tool_ok`만으론 못 가른다 — 그래서 `tool` 필드를 직접 단언한다.
    assert hop0.data["tool"] == "poc"
    assert hop0.data["tool_ok"] is False, (
        f"실패한 도구 호출인데 POV_HOP.tool_ok={hop0.data['tool_ok']!r}다"
    )


def test_pov_classify_exception_follows_the_additive_droppable_contract() -> None:
    """옛 `_run_pov`의 `_is_droppable` 계약대로 — per-finding 실패는 흡수, 서버 사건은 올린다.

    ⚠ NE 루프와 다르다: 크기 아닌 400을 NE는 올려 보내지만 pov는 흡수한다(추가형이라 체계적
    실패가 run을 멈추면 안 된다)."""
    clf, R = runner._pov_classify_exception, PovStopReason
    assert clf(SchemaParseError("x")) is R.PARSE_FAILED
    # 버릴 수 있는 게이트웨이(422)·크기 아닌 400은 GATEWAY_ERROR로 흡수한다(NE는 400을 올려 보냄).
    assert clf(ModelGatewayError("x", status=422)) is R.GATEWAY_ERROR
    assert clf(ModelGatewayError("x", status=400)) is R.GATEWAY_ERROR
    assert clf(ModelGatewayError("big", status=413)) is R.CONTEXT_OVERFLOW  # 크기 거절은 따로 센다
    # status 없음(서버 쪽 사건)은 흡수하지 않는다(None) — 루프가 그대로 올려 보낸다.
    assert clf(ModelGatewayError("down", status=None)) is None


# --- 이웃 변이 갭 — 새 테스트가 무는 축 옆에서 살아남던 변이들 (독립 검토 M-10) -----------


def _gen_byte(*, byte: int, count: int, rationale: str = "try") -> PovAction:
    """`repeat` 세그먼트로 `byte`를 `count`번 — 바이트 내용·길이를 둘 다 고를 수 있는 gen."""
    return PovAction(
        action="gen",
        spec=PovSpec(
            segments=[PovSegment(kind="repeat", byte=byte, count=count)],
            rationale=f"b{byte}-c{count}",
        ),
        rationale=rationale,
    )


def test_pov_loop_repeat_needs_the_same_bytes_not_just_the_same_length(
    ctx: RunContext,  # noqa: F811
) -> None:
    """같은 **길이**·다른 바이트인 두 gen은 REPEAT가 아니다 — 판정은 바이트 내용이지 길이가 아니다.

    반복 판정을 `len(poc)==len(last_poc)`로 바꾸는 변이가 여기서 죽는다: 실 모델이 같은 길이로 값만
    바꿔 가며 시도하는 흔한 경우를 길이 비교면 REPEAT로 오판한다. 한계: 8바이트 두 gen만 본다."""
    scripted = _scripted(
        ctx,
        [_gen_byte(byte=0x41, count=8), _gen_byte(byte=0x42, count=8),
         PovAction(action="submit", rationale="done")],
    )
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    _, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.SUBMITTED  # 길이만 같아선 REPEAT가 아니다


def test_pov_loop_repeat_compares_only_the_immediately_previous_gen(
    ctx: RunContext,  # noqa: F811
) -> None:
    """A→B→A(진동)는 REPEAT가 아니다 — 판정은 **직전** gen과만 비교한다(「직전 gen」 계약).

    직전이 아니라 이전 모든 gen과 비교하는 변이가 여기서 죽는다: 세 번째 A가 첫 A와 같아도 직전(B)과
    다르므로 반복이 아니다. 한계: A-B-A 한 패턴만 본다."""
    a = _gen_byte(byte=0x41, count=8)
    b = _gen_byte(byte=0x42, count=8)
    scripted = _scripted(ctx, [a, b, a, PovAction(action="submit", rationale="done")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    _, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.SUBMITTED  # 직전과만 비교 — A-B-A는 반복 아님


def test_pov_loop_repeat_survives_an_inspect_between_two_identical_gens(
    ctx: RunContext,  # noqa: F811
) -> None:
    """gen(A) → inspect → gen(A)는 REPEAT다 — inspect 홉이 직전 gen의 `last_poc`를 안 지운다.

    inspect 분기가 `last_poc`를 지우는 변이가 여기서 죽는다: 지우면 두 번째 A가 반복으로 안 잡혀
    루프가 이어진다. inspect는 새 PoC를 안 만들므로 「직전 gen」은 그대로 첫 A다."""
    a = _gen_byte(byte=0x41, count=8)
    scripted = _scripted(
        ctx,
        [a, PovAction(action="inspect", tool="asan", rationale="look"), a,
         PovAction(action="submit", rationale="done")],
    )
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    _, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.REPEAT_SPEC  # inspect가 사이에 있어도 A==A는 반복


def test_pov_loop_inspect_runs_the_tool_the_model_chose_not_a_default(
    ctx: RunContext,  # noqa: F811
) -> None:
    """inspect 홉은 **모델이 고른** 도구를 돌린다 — 기본값(asan)으로 굳지 않는다.

    inspect를 asan으로 고정하는 변이가 여기서 죽는다: 기존 inspect 테스트는 asan만 써서 그 변이와
    구별이 안 됐다. `build`를 골라 `POV_HOP.tool`이 build인지 직접 본다."""
    scripted = _scripted(
        ctx,
        [PovAction(action="inspect", tool="build", rationale="check build"),
         PovAction(action="submit", rationale="done")],
    )
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    runner._run_pov_hops(scripted, state, finding, capsule)

    hop0 = _hops(scripted)[0]
    assert hop0.data["action"] == "inspect"
    assert hop0.data["tool"] == "build"  # asan 고정 변이면 여기서 죽는다


def test_pov_hop_join_key_matches_across_more_than_two_hops(
    ctx: RunContext,  # noqa: F811
) -> None:
    """조인 키 일치를 3홉 이상에서도 본다 — 2홉만 보면 hop≥2에서만 어긋나는 변이를 놓친다.

    `PovHop.hop`을 `min(prev.hop+1, 1)`(hop≥2에서 고정)·`prev.hop+1 if prev.hop<2 else prev.hop`
    (hop≥3에서 고정)으로 바꾸는 변이가 여기서 죽는다: 2홉(0,1)만 보면 그 갈래가 안 드러난다."""
    scripted = _scripted(
        ctx,
        [_gen_byte(byte=0x41, count=1), _gen_byte(byte=0x42, count=2),
         _gen_byte(byte=0x43, count=3), PovAction(action="submit", rationale="done")],
    )
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    runner._run_pov_hops(scripted, state, finding, capsule)

    pov_hops = [h.data["hop"] for h in _hops(scripted)]
    model_calls = [
        e.data["hop"]
        for e in scripted.trace.events
        if e.kind is EventKind.MODEL_CALL and e.data.get("role") == Role.POV.value
    ]
    assert pov_hops == [0, 1, 2, 3]
    assert model_calls == pov_hops, (
        f"model_call.hop={model_calls} != pov_hop.hop={pov_hops} — 3홉 이상에서도 같아야 한다"
    )


def test_pov_loop_with_max_hops_zero_stops_before_submitting(
    ctx: RunContext,  # noqa: F811
) -> None:
    """현재 동작 고정 — `max_hops=0`이면 pov는 hop0(gen) 뒤 곧장 MAX_HOP이라 submit에 못 닿는다:
    `pov_generated` 0, 종단은 `POV_SKIPPED`. NE 루프를 끄는 설정(`max_hops=0`)이 PoV 산출을 조용히
    0으로 만드는 결합을 못 박는다 — pov 전용 상한 분리는 설정 결정(지금은 미결)이다."""
    ctx.config = ctx.config.transition(
        thresholds=ctx.config.thresholds.transition(max_hops=0)
    )
    scripted = _scripted(ctx, [_gen(count=8), PovAction(action="submit", rationale="done")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    _, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.MAX_HOP
    assert [h.data["hop"] for h in _hops(scripted)] == [0]  # hop0에서 멈춘다
    assert not any(e.kind is EventKind.POV_GENERATED for e in scripted.trace.events)


def test_pov_action_rejects_an_unknown_action() -> None:
    """`PovAction.action`은 gen/inspect/submit만 받는다 — 표에 없는 액션(예: abort)은 거절한다.

    `action` Literal에 다른 값을 더하는 변이가 여기서 죽는다(Literal은 통과시켜도 `_check_action`의
    허용-필드 표에 없어 검증이 샌다). 한계: 스키마 계약만 본다."""
    with pytest.raises(ValidationError):
        PovAction(action="abort", rationale="unknown action")


def test_pov_schema_caps_are_the_documented_values() -> None:
    """models.yaml·agent-contracts가 인용하는 상한을 **값째** 고정한다 — PoC 8 MiB · hex 16 Mi자.

    상수를 임포트해 `+1`만 재는 테스트는 값이 바뀌어도 초록이라(상한 키우는 변이가 샌다) 값 자체를
    단언한다. 한계: 이 둘(총 바이트·hex 길이)만, 성질이 아니라 숫자를 고정한다."""
    from src.schemas.pov import _MAX_POC_BYTES

    assert _MAX_POC_BYTES == 8 * 1024 * 1024  # 8 MiB
    hex_caps = [m.max_length for m in PovSegment.model_fields["hex"].metadata
                if getattr(m, "max_length", None) is not None]
    assert hex_caps == [2 * _MAX_POC_BYTES]  # hex 16 Mi자 = 바이트 2자


def test_pov_stop_reason_and_event_kind_spellings_are_pinned() -> None:
    """정지 사유·홉 이벤트의 **값 문자열**을 고정한다 — trace 주석·직렬화가 이 철자에 의존한다.

    값 철자를 바꾸는 변이가 여기서 죽는다. 한계: wire 철자를 단언하지 성질을 단언하지 않는다."""
    assert PovStopReason.MAX_HOP.value == "max_hop"
    assert PovStopReason.REPEAT_SPEC.value == "repeat_spec"
    assert PovStopReason.SUBMITTED.value == "submitted"
    assert EventKind.POV_HOP.value == "pov_hop"
