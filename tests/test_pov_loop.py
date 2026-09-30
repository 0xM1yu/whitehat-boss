"""PoV 툴 루프(Phase 3, Option C)의 계약 — 루프 제어 스키마 `PovAction`부터.

council(적대적 3인) + 사용자 확정 = Option C(2026-09-30): 모델은 매 홉 `PovAction` 하나를
낸다. `gen`은 **기존 `PovSpec`을 실어** 순수 `assemble_poc`가 인프로세스로 바이트를 만든다
(새 gen 샌드박스 도구 없음). `inspect`는 진단 도구(gdb/build)를 돌리고, `submit`은 루프를
끝낸다. raw 임의 바이트는 `PovSpec`의 `literal` 세그먼트가 이미 덮는다.

이 파일은 스키마 validator부터 검증한다 — `PovSegment._check_kind`가 다른 kind의 필드
혼입을 거절하듯(test_poc_assembly), `PovAction`도 액션별로 쓰지 않는 필드가 실리면 거절해야
한다(조용히 무시하면 오타·혼동이 통과한다).
"""

import dataclasses

import pytest
from pydantic import ValidationError

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
    action = PovAction(action="inspect", tool="gdb", rationale="inspect crash")
    assert action.action == "inspect"
    assert action.tool == "gdb"
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
        PovAction(action="inspect", tool="gdb", spec=_spec(), rationale="mixed")


def test_submit_rejects_a_stray_spec() -> None:
    with pytest.raises(ValidationError):
        PovAction(action="submit", spec=_spec(), rationale="mixed")


def test_submit_rejects_a_stray_tool() -> None:
    with pytest.raises(ValidationError):
        PovAction(action="submit", tool="poc", rationale="mixed")


def test_rationale_is_required_and_non_empty() -> None:
    with pytest.raises(ValidationError):
        PovAction(action="submit", rationale="")


# --- 루프 (슬라이스 2·3) — `_run_ne_hops`를 미러한 모델-주도 PoV 툴 루프 ----------
#
# dev_fake는 hop0 gen → hop1 submit을 낸다(`fake_model._build_pov_action`이 history 길이로
# 홉을 가른다). 정지 사유 각각은 스크립트한 모델로 확인한다(`test_ne_loop`의 방식).


def _gen(*, count: int = 8, rationale: str = "try overflow") -> PovAction:
    """서로 다른 gen 액션 — `count`로 스펙을 갈라 REPEAT_SPEC과 구분한다."""
    return PovAction(
        action="gen",
        spec=PovSpec(
            segments=[PovSegment(kind="repeat", byte=0x41, count=count)],
            rationale=f"spec-{count}",
        ),
        rationale=rationale,
    )


class _ScriptedModel:
    """홉마다 정해진 `PovAction`(또는 예외)을 낸다. 목록이 다하면 마지막 것을 반복한다."""

    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)
        self._i = 0
        self.prompts: list[tuple[Role, object]] = []

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        self.prompts.append((role, prompt))
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


def test_pov_loop_runs_gen_then_submit_and_returns_the_last_poc(ctx: RunContext) -> None:  # noqa: F811
    """dev_fake: hop0 gen → hop1 submit. 마지막 gen의 PoC를 돌려주고 SUBMITTED로 선다.

    POV_HOP가 홉마다(0=gen, 1=submit) 나고, submit 홉이 stop_reason=submitted다. 최종
    POV_GENERATED가 그 PoC의 바이트 크기를 싣는다(단발 `_run_pov`와 같은 종단 이벤트)."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(ctx, state, finding, capsule)

    assert reason is PovStopReason.SUBMITTED
    assert isinstance(poc, bytes) and len(poc) > 0
    hops = [e for e in ctx.trace.events if e.kind is EventKind.POV_HOP]
    assert [h.data["hop"] for h in hops] == [0, 1]
    assert [h.data["action"] for h in hops] == ["gen", "submit"]
    assert hops[1].data["stop_reason"] == PovStopReason.SUBMITTED.value
    gen = [e for e in ctx.trace.events if e.kind is EventKind.POV_GENERATED]
    assert gen and gen[-1].data["poc_bytes"] == len(poc)
    assert not any(e.kind is EventKind.POV_SKIPPED for e in ctx.trace.events)


def test_pov_loop_feeds_tool_evidence_into_the_next_hop(ctx: RunContext) -> None:  # noqa: F811
    """gen 홉이 도구를 돌려 Evidence를 만들고, 그 관측이 다음 홉(submit) 프롬프트에 실린다.

    hop0의 도구 실행(TOOL_CALL)이 있고, hop1 프롬프트 body에 그 관측(`ok=`)이 되먹임된다 —
    `_run_ne_hops`가 resolver 결과를 다음 홉 user 턴으로 보내는 것과 같은 자리."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    runner._run_pov_hops(ctx, state, finding, capsule)

    tool_calls = [e for e in ctx.trace.events if e.kind is EventKind.TOOL_CALL]
    assert tool_calls, "gen 홉이 도구를 안 불렀다"
    povs = [p for role, p in ctx.models.prompts if role is Role.POV]
    assert len(povs) == 2
    assert "ok=" in povs[1].body  # hop0 도구 관측이 hop1 body로 되먹임됐다


def test_pov_loop_stops_at_max_hop(ctx: RunContext) -> None:  # noqa: F811
    """모델이 매 홉 다른 gen만 내면(submit 없음) `max_hops`에서 선다 — 무한 gen을 막는다."""
    scripted = _scripted(ctx, [_gen(count=n) for n in range(1, 13)])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.MAX_HOP
    assert isinstance(poc, bytes) and len(poc) > 0  # 마지막 gen의 PoC는 남는다
    hops = [e for e in scripted.trace.events if e.kind is EventKind.POV_HOP]
    assert hops[-1].data["stop_reason"] == PovStopReason.MAX_HOP.value


def test_pov_loop_stops_on_a_repeated_spec(ctx: RunContext) -> None:  # noqa: F811
    """직전과 똑같은 spec을 또 내면 REPEAT_SPEC으로 선다 — 같은 스펙 재생성을 막는다."""
    scripted = _scripted(ctx, [_gen(count=8), _gen(count=8)])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.REPEAT_SPEC
    assert isinstance(poc, bytes) and len(poc) > 0  # hop0의 PoC는 남는다


def test_pov_loop_absorbs_a_schema_parse_error(ctx: RunContext) -> None:  # noqa: F811
    """스키마 파싱 실패는 흡수해 PARSE_FAILED로 서고 pov_skipped를 남긴다(finding 안 죽인다)."""
    scripted = _scripted(ctx, [SchemaParseError("bad pov action json")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    poc, reason = runner._run_pov_hops(scripted, state, finding, capsule)

    assert reason is PovStopReason.PARSE_FAILED
    assert poc is None
    skipped = [e for e in scripted.trace.events if e.kind is EventKind.POV_SKIPPED]
    assert skipped and skipped[-1].data["reason"] == PovStopReason.PARSE_FAILED.value
    assert not any(e.kind is EventKind.POV_GENERATED for e in scripted.trace.events)


def test_pov_loop_reraises_a_non_droppable_gateway_error(ctx: RunContext) -> None:  # noqa: F811
    """크기 아닌 400(체계적 거절)은 흡수하지 않고 올려 보낸다 — run의 사실이다(#296과 같은 판정)."""
    scripted = _scripted(ctx, [ModelGatewayError("server down", status=None)])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    with pytest.raises(ModelGatewayError):
        runner._run_pov_hops(scripted, state, finding, capsule)


def test_pov_classify_exception_absorbs_per_finding_but_not_server_events() -> None:
    """`_run_pov`의 추가형 계약대로 — per-finding 실패는 흡수, 서버 사건은 올린다."""
    assert runner._pov_classify_exception(SchemaParseError("x")) is PovStopReason.PARSE_FAILED
    # 버릴 수 있는 게이트웨이(422)는 GATEWAY_ERROR로 흡수한다.
    got = runner._pov_classify_exception(ModelGatewayError("systematic", status=422))
    assert got is PovStopReason.GATEWAY_ERROR
    # status 없음(서버 쪽 사건)은 흡수하지 않는다(None) — 루프가 그대로 올려 보낸다(#296).
    assert runner._pov_classify_exception(ModelGatewayError("down", status=None)) is None
