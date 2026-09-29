"""8단계 PoV — Auditor가 세운 Finding에서 PoC 구성 스펙을 세운다 (council 2026-09-30).

단발판이다. 모델이 `PovSpec`을 내면 순수 함수 `assemble_poc`가 결정론적으로 바이트를
만든다(fork B-iii). 러너 8단계 라우팅·실도구 실행(asan/poc)·상태 전이는 후속(P3/P5)이라
여기서는 **Finding → PovSpec → 바이트**까지가 계약이다.

`Stage.VALIDATE` 아래에서 돈다 — 새 단계 번호를 만들지 않는다(Stage는 0~9 고정).
구체 어댑터 모듈을 import하지 않는다(orchestrator 규약, tests/test_policy.py).
"""

from collections.abc import Callable

from src.orchestrator.context import RunContext
from src.orchestrator.nodes.scout import describe_target
from src.orchestrator.poc_assembly import assemble_poc
from src.orchestrator.prompt_loader import build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.schemas.capsule import ContextCapsule
from src.schemas.enums import Role
from src.schemas.finding import Finding
from src.schemas.pov import PovSpec

ROLE = Role.POV
# 요청하는 구조화 출력 스키마. 모델은 바이트가 아니라 **구성 스펙**을 낸다(pov.PovSpec docstring).
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
    version = ctx.config.model_for(ROLE.value).prompt_template_version
    template = load_prompt(ROLE, version)
    text = ctx.read_source(capsule.target) if source is None else source()
    prompt = template.render(
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
    result = ctx.call_model(
        role=ROLE,
        prompt=prompt,
        schema=OUTPUT_SCHEMA,
        stage=Stage.VALIDATE,
        subject_kind="finding",
        subject_id=finding.finding_id,
    )
    return result.value


def _describe_finding(finding: Finding) -> str:
    """PoV 프롬프트에 실을 Finding 요약. 경로 단계별 심볼은 후속 프롬프트 버전에서 넓힌다."""
    return (
        f"{finding.finding_id} · CWE: {finding.cwe} · {finding.title}\n"
        f"근거(rca): {finding.rca_draft}\n"
        f"경로 단계 수: {len(finding.path)}"
    )
