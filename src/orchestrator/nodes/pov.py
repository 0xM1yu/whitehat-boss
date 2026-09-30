"""8단계 PoV — Auditor가 세운 Finding에서 PoC 구성 스펙을 세운다 (council 2026-09-30).

단발판이다. 모델이 `PovSpec`을 내면 순수 함수 `assemble_poc`가 결정론적으로 바이트를
만든다(fork B-iii). 러너가 8단계에서 이 노드를 `validate.run`과 나란히 부른다(추가형 —
`runner._run_pov`). 실도구 실행(asan/poc)·모델-주도 툴 루프·상태 전이는 후속(호스트 필요)이라
여기서는 **Finding → PovSpec → 바이트**까지가 계약이다.

`Stage.VALIDATE` 아래에서 돈다 — 새 단계 번호를 만들지 않는다(Stage는 0~9 고정).
구체 어댑터 모듈을 import하지 않는다(orchestrator 규약, tests/test_policy.py).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.interfaces.model_client import ChatTurn, Prompt
from src.interfaces.tool_runner import ToolArgs
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.sandbox import DEFAULT_SANDBOX
from src.orchestrator.nodes.scout import describe_target
from src.orchestrator.poc_assembly import assemble_poc
from src.orchestrator.prompt_loader import build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.schemas.capsule import ContextCapsule
from src.schemas.enums import Role
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding
from src.schemas.pov import PovAction, PovSpec

ROLE = Role.POV
# 요청하는 구조화 출력 스키마. 모델은 바이트가 아니라 **구성 스펙**을 낸다(pov.PovSpec docstring).
# 단발판이 쓴다 — 툴 루프(Phase 3)는 매 홉 `PovAction`을 요청한다(`_call_hop`).
OUTPUT_SCHEMA = PovSpec


def run(
    ctx: RunContext,
    *,
    finding: Finding,
    capsule: ContextCapsule,
    source: Callable[[], str] | None = None,
) -> tuple[Finding, bytes]:
    """Finding에서 PoC 구성 스펙을 받아 바이트로 조립해 돌려준다.

    `source`가 없으면 캡슐 대상의 원문을 읽는다(auditor와 같은 콜러블 규약 — 읽기를 8단계
    구간 안에서 한다). 바이트를 파일로 쓰거나 실행·게이트에 거는 것은 러너 배선(P3/P5)의
    몫이다. 그래서 여기서는 상태 전이를 하지 않고 `(finding, poc)`를 그대로 돌려준다.
    """
    with ctx.stage(Stage.VALIDATE):
        spec = _ask_model(ctx, finding=finding, capsule=capsule, source=source)
        poc = assemble_poc(spec)
    return finding, poc


def _ask_model(
    ctx: RunContext,
    *,
    finding: Finding,
    capsule: ContextCapsule,
    source: Callable[[], str] | None,
) -> PovSpec:
    prompt = _render_prompt(ctx, finding=finding, capsule=capsule, source=source)
    result = ctx.call_model(
        role=ROLE,
        prompt=prompt,
        schema=OUTPUT_SCHEMA,
        stage=Stage.VALIDATE,
        subject_kind="finding",
        subject_id=finding.finding_id,
    )
    return result.value


def _render_prompt(
    ctx: RunContext,
    *,
    finding: Finding,
    capsule: ContextCapsule,
    source: Callable[[], str] | None,
) -> Prompt:
    """hop0 프롬프트를 렌더한다 — 사실 블록(FACTS)이 여기 실린다.

    단발 `_ask_model`과 툴 루프 `open_hop`이 공유한다. 후속 홉은 이걸 다시 렌더하지 않고
    prefix·version만 재사용한다(`continue_hop` — 사실은 hop0 본문에 한 번만 실린다).
    """
    version = ctx.config.model_for(ROLE.value).prompt_template_version
    template = load_prompt(ROLE, version)
    text = ctx.read_source(capsule.target) if source is None else source()
    return template.render(
        facts=build_facts(
            run_id=ctx.run_id,
            ids={"finding_id": finding.finding_id},
            target=capsule.target,
            capsule=capsule,
            source=text,
        ),
        finding=_describe_finding(finding),
        target=describe_target(capsule.target),
        source=text,
    )


@dataclass(frozen=True)
class PovHop:
    """PoV 툴 루프 홉 하나의 결과 — `scout.ScoutSession`의 최소판.

    `history`는 **다음 홉으로 보낼** 문답 전부다(이 홉의 user body + assistant raw 포함).
    `prefix`·`version`은 hop0에서 고정돼 후속 홉이 재사용한다 — system prefix는 홉마다 다시
    붙는 메시지라 다시 렌더하지 않는다(`interfaces/model_client.ChatTurn` 주석).
    """

    action: PovAction
    hop: int
    raw_text: str
    prefix: str
    version: str
    history: tuple[ChatTurn, ...]


def open_hop(
    ctx: RunContext,
    *,
    finding: Finding,
    capsule: ContextCapsule,
    source: Callable[[], str] | None = None,
) -> PovHop:
    """hop0 — 사실 블록이 실린 첫 프롬프트로 `PovAction`을 받는다."""
    prompt = _render_prompt(ctx, finding=finding, capsule=capsule, source=source)
    return _call_hop(ctx, finding=finding, prompt=prompt, history=(), hop=0)


def continue_hop(ctx: RunContext, *, finding: Finding, prev: PovHop, followup: str) -> PovHop:
    """hop k(= `prev.hop + 1`) — 도구 관측(`followup`)을 다음 user 턴으로 보낸다.

    사실 블록은 hop0 본문(`history[0]`)에 한 번만 실리므로 여기서는 다시 렌더하지 않고
    prefix·version을 재사용한다(`scout.continue_session`과 같은 규율).
    """
    prompt = Prompt(prefix=prev.prefix, body=followup, prompt_version=prev.version)
    return _call_hop(
        ctx, finding=finding, prompt=prompt, history=prev.history, hop=prev.hop + 1
    )


def _call_hop(
    ctx: RunContext,
    *,
    finding: Finding,
    prompt: Prompt,
    history: Sequence[ChatTurn],
    hop: int,
) -> PovHop:
    """모델 호출 하나 — hop0·hop k 공통. 예외는 그대로 올린다(호출자가 흡수 여부를 정한다)."""
    result = ctx.call_model(
        role=ROLE,
        prompt=prompt,
        schema=PovAction,
        stage=Stage.VALIDATE,
        subject_kind="finding",
        subject_id=finding.finding_id,
        history=history,
        hop=hop,
    )
    raw = result.raw_text or ""
    new_history = (
        *history,
        ChatTurn(speaker="user", content=prompt.body),
        ChatTurn(speaker="assistant", content=raw),
    )
    return PovHop(
        action=result.value,
        hop=hop,
        raw_text=raw,
        prefix=prompt.prefix,
        version=prompt.prompt_version,
        history=new_history,
    )


def run_tool(ctx: RunContext, *, finding: Finding, tool: str, workdir: str) -> Evidence:
    """PoV 홉에서 도구를 돌린다 — gen이 만든 PoC를 대상에서 실행/진단(asan·poc·build).

    ⚠ **PoC 바이트를 파일로 쓰지 않는다.** fake 도구는 workdir의 대상 소스 존재만 보고
    ok를 낸다(`fake_tools._run_repro`) — dev_fake는 루프 배선(gen→도구→Evidence→되먹임)을
    고정하는 자리다. 조립된 바이트는 `_run_pov_hops`의 반환값·`POV_GENERATED`(bytes·sha256)로
    남는다. 컨테이너 경로(호스트)에서 PoC를 마운트 파일로 넘길 때 바이트를 materialize한다
    (`validate._call`이 `[tool, *steps]`를 넘기는 자리와 같은 계약).
    """
    return ctx.call_tool(
        tool=tool,
        args=ToolArgs(argv=[tool], workdir=workdir),
        sandbox=DEFAULT_SANDBOX,
        stage=Stage.VALIDATE,
        subject_id=finding.finding_id,
    )


def _describe_finding(finding: Finding) -> str:
    """PoV 프롬프트에 실을 Finding 요약. 경로 단계별 심볼은 후속 프롬프트 버전에서 넓힌다."""
    return (
        f"{finding.finding_id} · CWE: {finding.cwe} · {finding.title}\n"
        f"근거(rca): {finding.rca_draft}\n"
        f"경로 단계 수: {len(finding.path)}"
    )
