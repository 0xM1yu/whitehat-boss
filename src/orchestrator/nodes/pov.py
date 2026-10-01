"""8단계 PoV — Auditor가 세운 Finding에서 PoC를 만드는 모델-주도 툴 루프 노드 (Option C).

매 홉 모델이 `PovAction`(gen/inspect/submit) 하나를 낸다. `gen`은 `PovSpec`을 실어 순수 함수
`assemble_poc`(러너)가 결정론적으로 바이트를 만들고, 그 PoC를 도구로 대상에서 실행해 관측을 다음
홉으로 되먹인다(fork B-iii — 모델-작성 코드 실행 없음). 러너 8단계가 `validate.run`과 나란히
`_run_pov_hops`로 이 노드의 홉 헬퍼를 부른다(추가형). 이 노드는 프롬프트 렌더 + 모델 호출 + 도구
실행 헬퍼만 든다 — 루프 제어·정지 판정·이벤트는 러너(`_run_pov_hops`)에 있다.

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
from src.orchestrator.prompt_loader import build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.schemas.capsule import ContextCapsule
from src.schemas.enums import Role
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding
from src.schemas.pov import PovAction

ROLE = Role.POV
# 이 역할의 구조화 출력 스키마 — 매 홉 `PovAction`이다(gen/inspect/submit). `_call_hop`이 이걸
# 요청하고, bench·계약 검사(`NODE_OUTPUT_SCHEMAS`/`ROLE_SPEC`)도 같은 값을 본다 — 한 역할 한 스키마.
OUTPUT_SCHEMA = PovAction


def _render_prompt(
    ctx: RunContext,
    *,
    finding: Finding,
    capsule: ContextCapsule,
    source: Callable[[], str] | None,
) -> Prompt:
    """hop0 프롬프트를 렌더한다 — 사실 블록(FACTS)이 여기 실린다.

    `open_hop`이 부른다. 후속 홉은 이걸 다시 렌더하지 않고 prefix·version만 재사용한다
    (`continue_hop` — 사실은 hop0 본문에 한 번만 실린다).
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
        schema=OUTPUT_SCHEMA,
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
