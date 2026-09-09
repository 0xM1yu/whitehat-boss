"""4단계 Scout / Triage — 후보를 만들고 승격 여부를 정책에 묻는다.

전이 표 C-1(생성) → C-2/C-3(승격·보류). 임계값(`scout_threshold`)은 노드가 아니라
정책이 본다. 탈락한 후보는 폐기하지 않고 `queued`로 남는다.

`previous`는 `queued`에서 재평가로 돌아온 후보다(표 C-8). 그 경우에만 C-4(파싱 실패)와
C-7(예산 소진)이 성립한다 — 첫 호출에서는 아직 후보 객체가 없어 전이시킬 대상이
없으므로 예외가 그대로 올라가고 러너가 run을 정리한다.
"""

from src.orchestrator.budget import BudgetExhausted
from src.orchestrator.context import RunContext
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.prompt_loader import build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.schemas.artifact import Artifact
from src.schemas.candidate import Candidate
from src.schemas.capsule import ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import FindingState, ReasonCode, Role
from src.schemas.evidence import Evidence

ROLE = Role.SCOUT


def run(
    ctx: RunContext,
    *,
    artifact: Artifact,
    capsule: ContextCapsule,
    evidences: tuple[Evidence, ...] = (),
    previous: Candidate | None = None,
) -> Candidate:
    """후보 하나를 만들고 트리아지 결과까지 적용해 돌려준다."""
    candidate_id = ctx.ids.next("candidate")
    with ctx.stage(Stage.SCOUT):
        candidate = _ask_model(
            ctx,
            candidate_id=candidate_id,
            artifact=artifact,
            capsule=capsule,
            evidences=evidences,
            previous=previous,
        )
        ctx.create(candidate, reason=None, stage=Stage.SCOUT)
        action = ctx.decide(
            RoutingRequest(
                point=DecisionPoint.SCOUT_TRIAGE,
                stage=Stage.SCOUT,
                subject_kind="candidate",
                subject_id=candidate.candidate_id,
                signals={"score": candidate.score},
            )
        )
        return ctx.apply_action(candidate, action, stage=Stage.SCOUT)


def _ask_model(
    ctx: RunContext,
    *,
    candidate_id: str,
    artifact: Artifact,
    capsule: ContextCapsule,
    evidences: tuple[Evidence, ...],
    previous: Candidate | None,
) -> Candidate:
    """프롬프트를 만들어 모델에 묻는다. 실패는 사유 코드로 기록하고 그대로 올린다."""
    template = load_prompt(ROLE, ctx.config.model_for(ROLE.value).prompt_template_version)
    raw_source = ctx.read_source(capsule.target)
    source = (
        _with_source_line_numbers(raw_source, capsule.target)
        if template.source_line_numbers
        else raw_source
    )
    prompt = template.render(
        facts=build_facts(
            run_id=ctx.run_id,
            ids={"candidate_id": candidate_id},
            artifact_id=artifact.artifact_id,
            target=capsule.target,
            capsule=capsule,
            source=source,
            evidences=evidences,
        ),
        target=describe_target(capsule.target),
        capsule_sections=describe_sections(capsule),
        source=source,
    )
    try:
        result = ctx.call_model(
            role=ROLE,
            prompt=prompt,
            schema=Candidate,
            stage=Stage.SCOUT,
            subject_kind="candidate",
            subject_id=candidate_id,
        )
    except BudgetExhausted:
        _queue_previous(ctx, previous, ReasonCode.BUDGET_EXHAUSTED)
        raise
    except Exception:
        _queue_previous(ctx, previous, ReasonCode.SCHEMA_PARSE_FAILED)
        raise
    return result.value


def _queue_previous(ctx: RunContext, previous: Candidate | None, reason: ReasonCode) -> None:
    """재평가 중이던 후보만 전이시킨다. 첫 호출에는 대상이 없다(표 C-4·C-7)."""
    if previous is None or previous.state is not FindingState.CANDIDATE:
        return
    ctx.apply(previous, to=FindingState.QUEUED, reason=reason, stage=Stage.SCOUT)


def _with_source_line_numbers(source: str, target: Location) -> str:
    """프롬프트 표시용 원본 줄 번호를 붙인다. 원본 파일은 수정하지 않는다."""
    if not source:
        return source

    first_line = target.line_start or 1
    return "\n".join(
        f"{first_line + offset}: {line}"
        for offset, line in enumerate(source.splitlines())
    )


def describe_target(target: Location) -> str:
    """프롬프트 본문의 사람이 읽는 대상 표기. 사실 블록에는 구조화된 값이 따로 간다."""
    lines = ""
    if target.line_start is not None:
        lines = f" (줄 {target.line_start}-{target.line_end or target.line_start})"
    return f"`{target.file}` 의 `{target.symbol or '(파일 전체)'}`{lines}"


def describe_sections(capsule: ContextCapsule) -> str:
    if not capsule.sections:
        return "(없음)"
    return ", ".join(f"{section.kind}({section.tokens} tok)" for section in capsule.sections)
