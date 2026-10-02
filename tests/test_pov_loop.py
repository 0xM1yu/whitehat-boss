"""PoV 툴 루프(Option C)의 계약 — 루프 제어 스키마 `PovAction`부터.

council(적대적 3인) + 사용자 확정 = Option C(2026-09-30): 모델은 매 홉 `PovAction` 하나를
낸다. `gen`은 **기존 `PovSpec`을 실어** 순수 `assemble_poc`가 인프로세스로 바이트를 만든다
(새 gen 샌드박스 도구 없음). `inspect`는 진단 도구(asan/build/gdb/poc)를 돌리고, `submit`은 루프를
끝낸다. raw 임의 바이트는 `PovSpec`의 `literal` 세그먼트가 이미 덮는다.
⏵ 2026-10-02 — `gdb`가 화이트리스트에 들어왔다(이슈 #360 · PR #327 `5cf5c20`).

아래는 스키마 validator + 루프(`_run_pov_hops`)의 계약을 함께 본다.

이 파일은 스키마 validator부터 검증한다 — `PovSegment._check_kind`가 다른 kind의 필드
혼입을 거절하듯(test_poc_assembly), `PovAction`도 액션별로 쓰지 않는 필드가 실리면 거절해야
한다(조용히 무시하면 오타·혼동이 통과한다).
"""

import dataclasses
import json
from functools import partial
from typing import get_args

import pytest
from pydantic import ValidationError

from src.adapters.fake_tools import TOOL_KINDS
from src.adapters.slices import split_source
from src.adapters.symbol_resolver import resolve
from src.interfaces.model_client import ModelGatewayError, ModelResult, SchemaParseError, Usage
from src.orchestrator import runner
from src.orchestrator.context import RunContext
from src.orchestrator.prompt_loader import PROMPTS_DIR
from src.orchestrator.trace import EventKind
from src.schemas.candidate import SliceRef
from src.schemas.common import Location
from src.schemas.enums import PovStopReason, Role
from src.schemas.pov import PovAction, PovSegment, PovSpec

# `ctx`는 dev_fake로 배선된 픽스처, `_pov_state`는 finding+캡슐을 담은 state 헬퍼다
# (test_ne_loop이 test_pipeline_e2e에서 헬퍼를 끌어 쓰는 것과 같은 방식).
from tests.test_nodes import BENIGN_TARGET, _pov_state, _root_of, _triage, ctx  # noqa: F401
from src.orchestrator.nodes import preprocess


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


def _with_pov_version(ctx: RunContext, version: str) -> RunContext:  # noqa: F811
    """pov 좌석의 `prompt_template_version`만 바꾼 `ctx` — `test_nodes.py`의
    `test_critic_wiring_includes_evidence_body`와 같은 패턴.

    넓은 입력(`nodes.pov._WIDE_INPUT_VERSIONS`)은 v2가 아니면 안 실리므로, 그것을 직접 보는
    검사는 ctx를 v3로 옮겨야 한다 — 기본 `dev_fake` 프로필의 pov 좌석은 v2다."""
    key = ctx.config.model_key_for(Role.POV.value)
    models = {
        **ctx.config.models,
        "fake_pov_wide": ctx.config.models[key].transition(prompt_template_version=version),
    }
    roles = {**ctx.config.profile.roles, Role.POV.value: "fake_pov_wide"}
    return dataclasses.replace(
        ctx,
        config=ctx.config.transition(
            models=models, profile=ctx.config.profile.transition(roles=roles)
        ),
    )


def test_every_pov_tool_is_a_real_tool() -> None:
    """`PovAction.tool` Literal의 모든 값은 ToolRunner 화이트리스트(`TOOL_KINDS`) 안이어야 한다.

    없는 도구를 광고하면 inspect가 tool_policy로 죽고 홉을 버린다 — 이 검사가 그 회귀를 잡는다
    (적대적 리뷰 2026-09-30).

    ⏵ **2026-10-02 — 이 docstring이 들던 예가 뒤집혔다.** 전 판은 *"예: 예전의 `gdb`"*라 적었는데
    PR #327(`5cf5c20`, 2026-10-01)이 `gdb`를 `TOOL_KINDS`에 넣어 **더는 반례가 아니다**. 이 검사가
    무는 것은 **철자가 아니라 포함 관계**이고, 지금 반례가 될 것은 `TOOL_KINDS`에 없는 **새 이름**
    (`valgrind`·`strace` 등)을 Literal에 넣는 변이다."""
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


def test_pov_hop0_carries_the_same_source_the_auditor_saw(ctx: RunContext) -> None:  # noqa: F811
    """🔴 러너가 **Auditor가 본 `SOURCE`를 PoV에 넘긴다**(사용자 지적 2026-10-02).

    `runner._run_pov_hops`에서 `open_hop(source=…)`을 빼는 변이가 여기서 죽는다 — 빼면 노드가
    `ctx.read_source(capsule.target)`(= **entry 함수 하나**)로 떨어지고 센티넬이 사라진다.

    **왜 이 핀이 필요한가.** `open_hop`은 2026-10-02 전에도 `source=`를 받았다(`nodes/pov.py:86`,
    기본값 `None`) — **러너가 안 넘겼을 뿐이고 그걸 무는 검사가 없었다.** 그래서 「파라미터가
    있다」가 「값이 흐른다」를 보장하지 않았다. 이 검사는 **흐르는지**를 본다.

    PoV가 넓은 소스를 봐야 하는 이유: CyberGym Level 1의 지배적 실패는 "취약 경로 미도달"이고 그걸
    가르는 것은 앞단 파서의 포맷 조건이다 — 그 파서가 entry 함수 밖에 있으면 PoV는 매직·헤더를
    영영 못 본다.

    ⚠ **v3로 돈다** — 기본 `dev_fake`의 pov 좌석은 v2이고, v2는 `state.finding_sources`를 안
    본다(적대적 리뷰 2026-10-02 MEDIUM-4 — v2는 이 PR 전과 바이트가 같아야 한다)."""
    sentinel = "SENTINEL_WIDE_SOURCE_FROM_AUDITOR"
    scripted = _scripted(_with_pov_version(ctx, "v3"), [PovAction(action="submit", rationale="done")])
    # ⚠ `_pov_state`는 **원래 ctx**로 돈다 — scripted 모델은 PoV 홉만 대신한다(앞 단계가 PovAction을
    # 받으면 죽는다).
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    calls = 0

    def _wide() -> str:
        nonlocal calls
        calls += 1
        return f"/* {sentinel} */\nint parse_header(void) {{ return 0; }}\n"

    state.finding_sources[finding.finding_id] = runner._Once(_wide)

    runner._run_pov_hops(scripted, state, finding, capsule)

    _, prompt = scripted.models.prompts[0]
    assert sentinel in prompt.body, "hop0 SOURCE에 Auditor가 본 원문이 없다"
    # `_Once`라 한 번만 부른다 — 두 번 부르면 `auditor_source` 이벤트·인덱스 조회가 중복된다.
    assert calls == 1


def _resolution(*, name: str = "LLVMFuzzerTestOneInput", file: str = "") -> object:
    """resolver 출력 하나 — 진입점 조회의 성공/실패를 흉내낸다.

    ⚠ **`followup_text`에 본문을 넣지 않는다.** 러너는 그것을 **안 쓴다**(쓰면 NE 후속 턴 포장이
    FACTS에 실린다 — 적대적 리뷰 2026-10-02 H-1). 러너가 쓰는 것은 `blocks[].file`·`lines`이고
    원문은 `ctx.read_source`로 직접 읽는다. 그래서 이 스텁도 **위치만** 준다."""
    from src.schemas.need_evidence import Resolution, RetrievedBlock

    if not name:
        # 통째로 빈 `Resolution` = 인덱스가 없거나 v1이다(`unfilled`조차 빈 것이 그 서명).
        return Resolution(followup_text="", blocks=(), unfilled=(), already=(), truncated=())
    return Resolution(
        followup_text="",
        blocks=(RetrievedBlock(name=name, label="function", file=file, lines=(1, 2)),),
        unfilled=(),
        already=(),
        truncated=(),
    )


def test_pov_facts_carry_the_fuzz_entrypoint(ctx: RunContext) -> None:  # noqa: F811
    """🔴 FACTS가 **퍼즈 진입점**(`LLVMFuzzerTestOneInput`)을 싣는다.

    CyberGym Level 1은 libFuzzer 하네스에 먹일 바이트를 만드는 과제다 — **바이트가 어디로
    들어가는지**(진입점의 `data`/`size`를 무엇이 파싱하는지)를 모르면 PoV는 앞단 포맷을 맞출 수
    없고, 문헌이 꼽는 지배적 실패가 바로 그 "취약 경로 미도달"이다. 2026-10-02 실측으로
    `LLVMFuzzerTestOneInput`은 `src/`·`prompts/` 전체에 **0건**이었다.

    조회는 **이미 있는 resolver**(`ctx.resolve_requests`)로 **위치만** 받고, 원문은
    `ctx.read_source`가 읽는다 — 새 훅·새 어댑터 배선 없이 이름으로 함수를 찾는 수단이 resolver
    뿐이다(`definitions_of`는 macros·types·file_vars만 본다, `src/adapters/index_graph.py:111`)."""
    scripted = _scripted(_with_pov_version(ctx, "v3"), [PovAction(action="submit", rationale="done")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    # 실제로 **읽을 수 있는** 파일을 가리켜야 한다 — 못 읽으면 `found=False`가 맞는 동작이다.
    scripted = dataclasses.replace(
        scripted, resolve_requests=lambda _q: _resolution(file=capsule.target.file)
    )

    runner._run_pov_hops(scripted, state, finding, capsule)

    _, prompt = scripted.models.prompts[0]
    facts = json.loads(prompt.body.split("```json")[1].split("```")[0])
    entry = facts["fuzz_entrypoint"]
    assert entry["found"] is True
    assert entry["name"] == "LLVMFuzzerTestOneInput"
    assert entry["file"] == capsule.target.file
    assert entry["lines"] == [1, 2]
    # 원문이 **디스크에서** 왔다 — resolver의 `followup_text`가 아니다(그건 빈 문자열로 줬다).
    # `prompt.body`로 비교하지 않는다: FACTS는 JSON으로 인코딩돼 줄바꿈이 이스케이프된다.
    assert entry["source"].strip(), "원문이 비었다 — read_source 경로가 끊겼다"
    expected = ctx.read_source(Location(file=capsule.target.file, line_start=1, line_end=2))
    assert entry["source"] == expected, "source가 read_source의 그 범위와 다르다"


def test_pov_facts_carry_the_entrypoint_body_not_the_ne_followup_wrapper(
    ctx: RunContext,  # noqa: F811
) -> None:
    """🔴 FACTS에 **NE 루프의 후속 턴 포장**이 실리면 안 된다 (적대적 리뷰 2026-10-02 H-1).

    첫 판은 `resolution.followup_text`를 `source`에 실었다. 그것은 함수 본문이 아니라
    `render_followup`이 만든 **다음 턴 전체**다 — `## Retrieved context (hop 1)` 머리, 줄번호
    gutter, 그리고 꼬리에 *"output one complete ScoutResponse JSON"*. `body.md`가 FACTS를 *"참조
    가능한 사실 전부"*라 선언하므로, 거기 실린 **다른 역할의 지시**를 PoV가 사실로 읽는다.

    앞의 두 검사는 `_resolution` 스텁이 `followup_text=<본문>`을 **지어내서** 이 결함을 못 잡았다.
    여기서는 `Resolution`을 **진짜 모양으로**(포장이 든 `followup_text` + 위치 블록) 주고, FACTS에
    **포장이 아니라 원문 줄**이 실리는지 본다."""
    from src.schemas.need_evidence import Resolution, RetrievedBlock

    wrapper = (
        "## Retrieved context (hop 1)\n"
        "In your previous turn you asked for context.\n"
        "  10 | int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {\n"
        "Judge the same candidate again and output one complete ScoutResponse JSON.\n"
    )
    scripted = _scripted(_with_pov_version(ctx, "v3"), [PovAction(action="submit", rationale="done")])
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    target = capsule.target

    def _resolve(_query):  # noqa: ANN001, ANN202
        return Resolution(
            followup_text=wrapper,
            blocks=(
                RetrievedBlock(
                    name="LLVMFuzzerTestOneInput",
                    label="function",
                    file=target.file,
                    lines=(1, 2),
                ),
            ),
            unfilled=(),
            already=(),
            truncated=(),
        )

    scripted = dataclasses.replace(scripted, resolve_requests=_resolve)

    runner._run_pov_hops(scripted, state, finding, capsule)

    _, prompt = scripted.models.prompts[0]
    facts = json.loads(prompt.body.split("```json")[1].split("```")[0])
    entry = facts["fuzz_entrypoint"]
    assert "ScoutResponse" not in prompt.body, "NE 후속 턴의 지시가 PoV 프롬프트에 실렸다"
    assert "Retrieved context" not in entry["source"], "NE 후속 턴 머리가 source에 실렸다"
    # 위치는 범위로 줄여 싣는다 — 블록이 걸친 줄 집합이 아니라 lo..hi다.
    assert entry["lines"] == [1, 2]
    # 원문은 `ctx.read_source`가 읽은 대상 파일의 그 범위다(빈 문자열이면 found=False).
    assert entry["found"] is (entry["source"] != "")


def test_pov_facts_record_why_the_fuzz_entrypoint_is_missing(ctx: RunContext) -> None:  # noqa: F811
    """진입점을 못 찾으면 **빈 값 + 사유**다 — 키를 조용히 빼지 않는다(CLAUDE.md §0).

    키가 없으면 "안 찾았다"와 "찾았는데 없다"가 구별되지 않는다. `0`·빈 값은 **미실측
    표시**이고 사유가 같이 있어야 한다."""
    scripted = _scripted(_with_pov_version(ctx, "v3"), [PovAction(action="submit", rationale="done")])
    scripted = dataclasses.replace(scripted, resolve_requests=lambda _q: _resolution(name=""))
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    runner._run_pov_hops(scripted, state, finding, capsule)

    _, prompt = scripted.models.prompts[0]
    facts = json.loads(prompt.body.split("```json")[1].split("```")[0])
    entry = facts["fuzz_entrypoint"]
    assert entry["found"] is False
    assert entry["source"] == ""
    assert entry["reason"], "못 찾은 사유가 비어 있다 — §0 위반"


def test_pov_facts_surface_the_resolvers_own_unfilled_reason(ctx: RunContext) -> None:  # noqa: F811
    """🔴 적대적 리뷰 2026-10-02 LOW(재현) — 함수가 **인덱스 표에는 있는데 파일을 못 읽어서**
    못 찾은 경우도, 전 판은 "인덱스의 함수 표에 없다"는 **틀린** 사유를 적었다.

    `resolve()`의 `_unfilled_lookup_reason` docstring이 스스로 적듯("인덱스에는 있는데 파일을
    못 읽어 여기까지 왔을 때"), `resolution.unfilled`가 비지 않은 경우는 "표에 이름이 없다"
    하나가 아니다 — closest_name·not_found·libc·outside_repo 어느 것도 될 수 있다. 이 검사는
    resolver가 실제로 낸 `Unfilled.text`가 그대로 사유에 실리는지 본다(추측으로 하나를 고르지
    않는다)."""
    from src.schemas.enums import UnfilledReason
    from src.schemas.need_evidence import Resolution, Unfilled

    distinguishing_text = "Not resolved: function:LLVMFuzzerTestOneInput (closest: LLVMFuzzerInitialize)"
    scripted = _scripted(_with_pov_version(ctx, "v3"), [PovAction(action="submit", rationale="done")])
    scripted = dataclasses.replace(
        scripted,
        resolve_requests=lambda _q: Resolution(
            followup_text="",
            blocks=(),
            unfilled=(
                Unfilled(
                    kind="function",
                    target="LLVMFuzzerTestOneInput",
                    reason=UnfilledReason.CLOSEST_NAME,
                    text=distinguishing_text,
                ),
            ),
            already=(),
            truncated=(),
        ),
    )
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    runner._run_pov_hops(scripted, state, finding, capsule)

    _, prompt = scripted.models.prompts[0]
    facts = json.loads(prompt.body.split("```json")[1].split("```")[0])
    entry = facts["fuzz_entrypoint"]
    assert entry["found"] is False
    assert distinguishing_text in entry["reason"], (
        f"resolver의 실제 사유가 안 실리고 뭉뚱그린 사유만 왔다: {entry['reason']!r}"
    )


def test_fuzz_entrypoint_is_not_dropped_by_the_resolvers_own_render_overhead(
    ctx: RunContext,  # noqa: F811
) -> None:
    """🔴 적대적 리뷰 2026-10-02 MEDIUM-1(재현) — resolver의 렌더 비용이 **원문보다 먼저** 상한에
    걸려 `_fuzz_entrypoint`의 「앞부분을 남긴다」 자르기가 죽은 코드였다.

    `resolve()`의 `_fit_budget`은 **헤더·배너·줄번호 gutter까지 더한 렌더 길이**를 `max_chars`와
    비교한다(`render_fragment`). 그래서 **원문이 4,000자 미만**이어도 줄 수가 많으면(gutter
    overhead) 렌더가 4,000을 넘어 블록이 통째로 `truncated`에 빠진다 — `_fuzz_entrypoint`는 그러면
    `block is None`에서 멈추고 `ctx.read_source`를 **아예 안 부른다.** 즉 `len(source) >
    _FUZZ_ENTRYPOINT_CHARS` 자르기(`:1248`)는 `block`이 **잡혔을 때만** 돌고, 블록이 잡혔다는
    것은 렌더 비용이 이미 상한 밑이라는 뜻이라 원문은 반드시 그보다 **더** 작다 — 그 자리는
    산수로 못 닿는 죽은 코드다.

    이 픽스처는 원문 100줄 · 3,899자(4,000 미만)인데 렌더(헤더+배너+gutter)는 4,590자다.
    고치기 전에는 resolver 호출에 `max_chars=4000`을 그대로 넘겨 블록이 드롭된다
    (`found=False` + "잘렸다"). 고친 뒤에는 resolver에는 위치만 받을 수 있게 넉넉한 상한을 주고,
    실제 글자 상한은 `ctx.read_source` 뒤 자르기 하나로만 건다."""
    line = "    processByte(buf[i], &state, flag);"
    body = [line] * 100
    raw = "\n".join(body)
    assert len(raw) < 4000, "픽스처 전제 — 원문은 상한 밑이어야 한다"
    (_root_of(ctx) / "fuzz.c").write_text(raw + "\n", encoding="utf-8")

    def table(name: str):  # noqa: ANN001, ANN202
        if name != "functions":
            return None
        return {"LLVMFuzzerTestOneInput": ({"file": "fuzz.c", "line_start": 1, "line_end": 100},)}

    def read_lines(relative: str):  # noqa: ANN001, ANN202
        return split_source((_root_of(ctx) / relative).read_bytes())

    wired = dataclasses.replace(
        ctx, resolve_requests=partial(resolve, table=table, read_lines=read_lines)
    )

    result = runner._fuzz_entrypoint(wired, "fuzz.c")

    assert result["found"] is True, (
        f"resolver의 렌더 비용이 원문 상한보다 먼저 걸렸다 — reason={result['reason']!r}"
    )
    assert result["reason"] == "", "원문이 상한 밑인데 잘린 사유가 남았다"
    assert "processByte" in str(result["source"])
    assert result["lines"] == [1, 100]


def test_pov_facts_say_so_when_there_is_no_resolver_at_all(ctx: RunContext) -> None:  # noqa: F811
    """resolver 자체가 없는 run(인덱스 v1·미로드)도 **사유가 다르다**.

    `ctx.resolve_requests is None`은 "조회할 수단이 없었다"이고 빈 `Resolution`은 "조회했는데
    없었다"다. 둘을 같은 사유로 적으면 나중에 무엇을 고쳐야 하는지 못 가른다."""
    scripted = _scripted(_with_pov_version(ctx, "v3"), [PovAction(action="submit", rationale="done")])
    scripted = dataclasses.replace(scripted, resolve_requests=None)
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    runner._run_pov_hops(scripted, state, finding, capsule)

    _, prompt = scripted.models.prompts[0]
    facts = json.loads(prompt.body.split("```json")[1].split("```")[0])
    entry = facts["fuzz_entrypoint"]
    assert entry["found"] is False
    assert "resolve_requests" in entry["reason"]  # ⏵ 2026-10-02 LOW 정정 후 문구(runner.py)


def test_other_roles_facts_do_not_gain_the_fuzz_entrypoint_key() -> None:
    """🔴 `fuzz_entrypoint`는 **PoV만** 싣는다 — 다른 역할의 프롬프트는 한 바이트도 안 바뀐다.

    `build_facts`를 인자 없이 부르면 키가 **없어야** 한다(`slice_criterion`과 같은 opt-in 규율,
    `prompt_loader.py:345`). 무조건 싣는 변이가 여기서 죽는다 — 그러면 Scout·Auditor·Critic
    프롬프트가 같이 바뀌어 **이전 run과 직접 비교할 수 없게 된다**(`docs/evaluation.md:69`)."""
    from src.orchestrator.prompt_loader import build_facts

    facts = build_facts(run_id="r", ids={})

    assert "fuzz_entrypoint" not in facts


def test_pov_v2_render_is_byte_identical_to_main_before_this_pr(ctx: RunContext) -> None:  # noqa: F811
    """🔴 적대적 리뷰 2026-10-02 MEDIUM-4 — 배선된 pov v2는 **이 PR 전(main `d85ea62`)과 바이트가
    같아야 한다**(`docs/evaluation.md` §2.3-4, v2로 돈 run이 비교 대상 0건이라도 다음 v2 run까지
    조용히 입력을 바꿀 이유는 없다). 넓은 입력은 `_WIDE_INPUT_VERSIONS`(지금은 v3)에서만 켠다.

    `state.finding_sources`에 센티넬을 채워도, `_fuzz_entrypoint`가 뭘 찾아도, `finding.path`가
    비어 있지 않아도 — v2 렌더(FINDING·SOURCE·FACTS)는 **이 PR의 입력을 하나도 안 받는다.**
    FACTS에 `fuzz_entrypoint` 키 자체가 없는 것도 같이 본다(opt-in — 키가 있으면 그것만으로도
    main과 바이트가 달라진다)."""
    sentinel = "SENTINEL_SHOULD_NEVER_REACH_V2"
    scripted = _scripted(ctx, [PovAction(action="submit", rationale="done")])  # 기본은 v2
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    assert finding.path, "픽스처 전제 — 경로 단계가 있어야 '안 실린다'는 것이 의미가 있다"
    state.finding_sources[finding.finding_id] = runner._Once(lambda: sentinel)
    scripted = dataclasses.replace(
        scripted, resolve_requests=lambda _q: _resolution(file=capsule.target.file)
    )

    runner._run_pov_hops(scripted, state, finding, capsule)

    _, prompt = scripted.models.prompts[0]
    assert sentinel not in prompt.body, "v2가 state.finding_sources의 넓은 SOURCE를 받았다"
    # 심볼 문자열 자체는 title·rca에도 우연히 나타날 수 있다(픽스처가 `safe_copy`를 쓴다) —
    # wide 전용 **구조**(경로 목록 헤더)로 가른다.
    assert "경로(source→sink):" not in prompt.body, "v2 FINDING에 경로 목록이 생겼다"
    assert "재현 근거(repro_spec)" not in prompt.body, "v2 FINDING에 repro_spec 유무 줄이 생겼다"
    facts = json.loads(prompt.body.split("```json")[1].split("```")[0])
    assert "fuzz_entrypoint" not in facts, "v2 FACTS에 fuzz_entrypoint 키가 생겼다"


def _asan_evidence() -> object:
    """`_pov_observation`에 넘길 Evidence 하나.

    `payload_ref`는 `NonEmptyStr`이라 **항상 있다**(`src/schemas/evidence.py:35`) —
    「참조가 없다」는 상태는 스키마가 허용하지 않는다. 없는 것은 **로그**이고,
    그것은 `read_payload`가 `None`으로 답한다.
    """
    from datetime import UTC, datetime

    from src.schemas.enums import EvidenceKind
    from src.schemas.evidence import Evidence

    return Evidence(
        evidence_id="ev_0001",
        run_id="run_20260101_0001",
        artifact_id="art_0001",
        kind=EvidenceKind.SANITIZER,
        source="asan",
        ok=True,
        exit_code=1,
        payload_ref="log/asan.txt",
        digest="sha256:" + "0" * 64,
        observed_at=datetime(2026, 10, 2, tzinfo=UTC),
    )


def test_pov_observation_carries_the_tool_output_tail() -> None:
    """🔴 관측이 **도구 출력 꼬리**를 싣는다 — 스칼라 여섯만 주던 판에서 넓혔다(S6c ③-5).

    프롬프트는 *"관측을 읽고 실패 이유를 판단한 뒤 스펙을 고쳐라"*라 지시하는데, ASan이 어떤 위반을
    어느 오프셋에서 잡았는지가 전부 payload 로그에만 있어 **읽을 것이 없었다.**

    **꼬리**인 이유: ASan은 끝에 `SUMMARY:` 요약이 오고 gdb도 수명주기 줄이 뒤에 온다. 앞을 자르면
    컴파일 잡음만 남는다 — 앞을 싣는 변이가 여기서 죽는다."""
    report = "x" * 5000 + "\nSUMMARY: AddressSanitizer: heap-buffer-overflow at 0x602000000018\n"

    text = runner._pov_observation(
        "gen", _asan_evidence(), read_payload=lambda _ref: report
    )

    assert "tool=asan action=gen ok=True" in text
    assert "SUMMARY: AddressSanitizer: heap-buffer-overflow" in text, "꼬리가 안 실렸다"
    assert "앞 " in text and "자 생략" in text, "자른 사실을 안 적었다"
    assert len(text) < 3000, "상한을 안 걸었다 — 폭주 차단이 목적이다"


def test_pov_observation_is_one_line_when_there_is_no_log() -> None:
    """로그가 없으면 **전 판과 바이트가 같은 한 줄**이다.

    `read_payload`가 `None`을 주는 것은 정상 상태다(`dev_fake`는 로그 파일을 안 쓴다 —
    `context.read_payload` 주석). 그때 빈 꼬리 머리(`--- asan 출력 꼬리 ---`)를 붙이면 「로그가
    없다」와 「로그가 비었다」가 같아 보인다. 빈 문자열도 같게 다룬다."""
    no_log = runner._pov_observation(
        "inspect", _asan_evidence(), read_payload=lambda _ref: None
    )
    empty_log = runner._pov_observation(
        "inspect", _asan_evidence(), read_payload=lambda _ref: ""
    )

    assert "\n" not in no_log
    assert no_log == empty_log
    assert "꼬리" not in no_log


def test_pov_once_wrapper_calls_the_inner_callable_only_once() -> None:
    """`runner._Once`는 감싼 콜러블을 한 번만 부르고 같은 문자열을 돌려준다.

    이 검사는 `_Once` **클래스의 성질**을 문다. `_step_auditor`가 실제로 그것으로 감싸는지는
    **`tests/test_slice_targets.py`가 문다** — `_Once`를 통과 함수로 바꾸면 거기서 **15건이
    빨개진다**(`auditor_source` 이벤트가 둘이 되는 것을 보는 검사들, 예: `:476`).

    ⏵ **정정 2026-10-02**: 이 docstring의 전 판은 *"`_step_auditor`를 구동하는 테스트가 0건이라 그
    자리는 미핀이다"*라 적었다. **틀렸다** — 적대적 리뷰가 인프로세스 변이로 그 15건을 빨갛게
    만들어 보였다. 「미핀」이라 적은 쪽이 거짓이었고, 핀은 이미 있었다(CLAUDE.md §0 — 센 것과 안 센
    것을 섞지 않는다. 안 세고 「없다」고 적은 것이 그 위반이다).

    ⚠ **그 15건이 묻는 것은 "두 번 안 부르는가"뿐이다.** `_Once`를 통째로 걷어 **그 줄 자체**
    (`state.finding_sources[finding.finding_id] = source`)를 지워도 이 15건은 하나도 안
    빨개진다 — `_Once`가 없으면 그 줄도 같이 사라지는 변이라 구별이 안 된다. "**키가 생기는가**"는
    `test_step_auditor_wires_state_finding_sources_for_a_slice_target`가 문다(적대적 리뷰
    2026-10-02 MEDIUM-5 재현 — 605 passed로 그 공백을 직접 확인했다)."""
    calls = 0

    def _inner() -> str:
        nonlocal calls
        calls += 1
        return f"text-{calls}"

    once = runner._Once(_inner)

    assert once() == "text-1"
    assert once() == "text-1"  # 두 번째도 같은 문자열
    assert calls == 1


def test_step_auditor_wires_state_finding_sources_for_a_slice_target(
    ctx: RunContext,  # noqa: F811
) -> None:
    """🔴 적대적 리뷰 2026-10-02 MEDIUM-5(재현) — `_step_auditor`의
    `state.finding_sources[finding.finding_id] = source` 줄을 통째로 지워도 이 PR의 기존 검사는
    하나도 안 빨개진다(605 passed, 이 파일 기준 — 직접 돌려 확인했다).

    이유: PoV 쪽 검사(`test_pov_hop0_carries_the_same_source_the_auditor_saw` 등)는 전부
    `_pov_state`로 **손으로** 캡슐·finding을 만들고 `state.finding_sources[...]`도 손으로
    채운다 — `_step_auditor` **자신이** 그 줄을 쓰는지는 안 본다. `test_slice_targets.py`의 15건은
    `_Once` 감싸기(두 번 안 부르는지)만 묻지, 애초에 **키가 생기는지**는 안 묻는다.

    이 검사는 `_step_auditor`를 손이 아니라 **실제 경로**(`_InFlight`·`_Target(slice=...)`)로
    직접 돌려 `state.finding_sources`에 그 finding의 키가 생기는지, 그리고 그 값이
    `_auditor_target`·`_auditor_source`를 **같은 인자로** 다시 부른 것과 같은 문자열인지 본다."""
    candidate, _capsule, evidences = _triage(ctx, BENIGN_TARGET)
    artifacts = preprocess.run(ctx, source_root=_root_of(ctx))
    artifact = next(a for a in artifacts if a.path == BENIGN_TARGET.file)
    ref = SliceRef(
        slice_id="slice_test",
        location=BENIGN_TARGET,
        given_end="source",
        entry=BENIGN_TARGET,
        lines=((BENIGN_TARGET.file, BENIGN_TARGET.line_start),),
    )
    target = runner._Target(
        artifact=artifact,
        location=BENIGN_TARGET,
        evidences=evidences,
        signal_rank=2,
        slice=ref,
    )
    item = runner._InFlight(target=target, reached=runner._Reached(), candidate=candidate)
    state = runner._Accumulator()

    runner._step_auditor(ctx, item, state)

    finding = item.finding
    assert finding is not None
    assert finding.finding_id in state.finding_sources, (
        "state.finding_sources가 안 채워졌다 — _step_auditor의 배선이 끊겼다 "
        "(8단계 PoV가 entry 함수 하나로 되돌아간다)"
    )
    anchor = runner._auditor_target(ctx, target)
    expected = runner._auditor_source(ctx, ref, anchor, candidate.candidate_id)
    assert state.finding_sources[finding.finding_id]() == expected


def test_pov_finding_block_carries_the_path_symbols_not_just_the_count(
    ctx: RunContext,  # noqa: F811
) -> None:
    """FINDING 블록이 **경로 단계의 심볼**을 싣는다 — 개수만 싣던 변이가 여기서 죽는다.

    프롬프트는 *"경로 단계로 source에서 sink까지의 흐름을 확인하라"*고 지시하는데
    (`prompts/pov/*/prefix.md` 도달 절차 3) 전 판은 `len(finding.path)`만 줬다. 이름이 없으면
    `SOURCE`의 어느 함수가 앞단 파서이고 어느 것이 sink인지 PoV가 못 가른다."""
    scripted = _scripted(_with_pov_version(ctx, "v3"), [PovAction(action="submit", rationale="done")])
    state, finding = _pov_state(ctx)  # ⚠ 앞 단계는 원래 ctx로 — 위 검사의 같은 이유
    capsule = state.finding_capsules[finding.finding_id]
    assert finding.path, "픽스처 finding에 경로 단계가 없으면 이 검사가 동어반복이다"

    runner._run_pov_hops(scripted, state, finding, capsule)

    _, prompt = scripted.models.prompts[0]
    for step in finding.path:
        assert step.symbol in prompt.body, f"경로 심볼 {step.symbol!r}이 FINDING 블록에 없다"
    # `repro_spec`은 **유무만** — 본문이 실리면 참조 PoC 누출 경로가 열린다.
    assert "재현 근거(repro_spec):" in prompt.body


def test_pov_loop_can_inspect_with_gdb(ctx: RunContext) -> None:  # noqa: F811
    """PoV가 **gdb로 현 후보를 들여다볼 수 있다**(이슈 #360 · 사용자 결정 2026-10-02).

    `gdb`를 `PovAction.tool` Literal에서 빼는 변이가 여기서 죽는다 — `_scripted`가 `PovAction`을
    **구성하는 순간** ValidationError가 나므로 홉이 하나도 안 돈다. 위
    `test_every_pov_tool_is_a_real_tool`은 **포함 관계**(Literal ⊆ TOOL_KINDS)만 보므로 Literal이
    좁아지는 쪽은 잡지 못한다 — 그 방향을 이 검사가 막는다.

    ⚠ 이 검사가 무는 것은 **루프가 모델이 고른 gdb를 그대로 돌린다**는 것까지다. 실컨테이너에서
    gdb가 대상을 띄웠는지 가르는 것은 `judge()`의 stdout 갈래이고
    (`src/adapters/container_tools.py:576-581`) 그쪽은 dev_fake가 재현하지 않는다."""
    scripted = _scripted(
        ctx,
        [PovAction(action="inspect", tool="gdb", rationale="크래시 지점을 본다"),
         PovAction(action="submit", rationale="done")],
    )
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]

    runner._run_pov_hops(scripted, state, finding, capsule)

    hop0 = _hops(scripted)[0]
    assert hop0.data["action"] == "inspect"
    assert hop0.data["tool"] == "gdb"
    # `TOOL_KINDS`가 gdb를 CRASH로 분류하므로 Evidence kind가 그것이어야 한다(#327).
    assert hop0.data["evidence_kind"] == TOOL_KINDS["gdb"].value


def test_v3_prefix_does_not_claim_gdb_sees_the_models_poc_bytes() -> None:
    """🔴 적대적 리뷰 2026-10-02 MEDIUM-3(코드 판독) — `prompts/pov/v3/prefix.md`가 gdb를
    「경로에 닿았나를 가르는 수단」이라 **무조건** 서술하는데, 지금 코드에서는 거짓이다.

    `nodes.pov.run_tool`이 `ToolArgs(argv=[tool], workdir=workdir)`만 넘긴다 — PoC 바이트가
    gdb 실행에 **안 들어간다**(`docs/agent-contracts.md`도 바로 다음 줄에서 같은 한계를 적는다).
    그래서 gdb는 지금은 "당신이 방금 만든 그 입력으로 어디까지 갔는가"에 답을 못 한다 — 입력
    없이 대상을 띄우는 것과 같다. v3는 아직 미배선(disk-only, `meta.yaml`)이라 지금 run에는
    안 닿지만, 배선되는 순간(#354 이후) 모델에게 거짓 전제를 주게 된다 — 지금 고친다.

    이 검사는 **철자가 아니라 성질**을 문다: gdb 절이 「경로에 닿았나」 주장을 하려면 그 옆에
    PoC가 아직 안 들어간다는 한계(또는 조건)가 같이 있어야 한다."""
    prefix = (PROMPTS_DIR / "pov" / "v3" / "prefix.md").read_text(encoding="utf-8")
    gdb_bullet_start = prefix.index("`gdb` — ")
    gdb_bullet_end = prefix.index("`build` — ")
    gdb_bullet = prefix[gdb_bullet_start:gdb_bullet_end]

    assert "경로에 닿았나" in gdb_bullet, "픽스처 전제 — 이 절이 그 주장을 담고 있어야 한다"
    assert "안 들어간다" in gdb_bullet or "들어가지 않는다" in gdb_bullet, (
        "gdb 절이 「경로에 닿았나」를 단정하면서 PoC 바이트가 아직 gdb 입력으로 안 들어간다는 "
        "한계를 안 적었다 — 지금 코드에서 거짓 전제를 모델에게 준다"
    )


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
