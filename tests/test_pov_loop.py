"""PoV 툴 루프(Phase 3, Option C)의 계약 — 루프 제어 스키마 `PovAction`부터.

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
