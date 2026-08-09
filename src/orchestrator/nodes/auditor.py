"""5단계 Primary Auditor — 가설과 근거 경로를 세운다.

전이 표 F-1(Finding 생성) + C-6/C-7(후보 쪽 실패 전이). 후보는 이 시점에 `triaged`이므로
파싱 실패·예산 소진이 그대로 표의 행이 된다.

**MissingEvidence 객체는 노드가 만든다.** 구조화 출력 스키마가 `Finding` 하나여서
모델은 미리 발급된 `missing_id`만 참조하고, 실제 객체는 여기서 템플릿 문구로 만든다
(S1 계약, C4에서 확정). `blocking=True`인 이유: 모델이 이 ID를 참조했다는 것은 "코드에서
확인 불가"라고 판단했다는 뜻이고, 그것이 곧 Evidence Gate E2가 막아야 할 상태다
(계획 D-h: E2의 "guard 부재"를 blocking MissingEvidence 부재로 환원).
"""

from src.orchestrator.budget import BudgetExhausted
from src.orchestrator.context import RunContext
from src.orchestrator.nodes.scout import describe_sections, describe_target
from src.orchestrator.prompt_loader import build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.schemas.candidate import Candidate
from src.schemas.capsule import ContextCapsule
from src.schemas.enums import FindingState, ReasonCode, Role
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence

ROLE = Role.AUDITOR

# 노드가 만드는 MissingEvidence의 문구. 모델이 자유 텍스트로 요청하게 두면 같은 요청이
# 매번 다르게 쓰여 집계가 안 된다 — 무엇이 없어서 막혔는지는 세어야 하는 값이다.
_NEED = "대상 경로를 코드만으로 확정할 수 없다: 정적분석 신호 또는 호출 그래프 근거"
_HOW = "2단계 신호 수집을 대상 파일로 다시 실행하거나 index 도구로 호출자를 확인한다"


def run(
    ctx: RunContext,
    *,
    candidate: Candidate,
    capsule: ContextCapsule,
    evidences: tuple[Evidence, ...] = (),
    callers: tuple[str, ...] = (),
) -> tuple[Finding, list[MissingEvidence]]:
    """가설 Finding과 그것이 요청한 MissingEvidence를 만들어 저장한다."""
    ids = {
        "candidate_id": candidate.candidate_id,
        "finding_id": ctx.ids.next("finding"),
        "missing_id": ctx.ids.next("missing_evidence"),
    }
    with ctx.stage(Stage.AUDITOR):
        finding = _ask_model(
            ctx,
            ids=ids,
            candidate=candidate,
            capsule=capsule,
            evidences=evidences,
            callers=callers,
        )
        ctx.create(finding, reason=ReasonCode.AUDITOR_HYPOTHESIZED, stage=Stage.AUDITOR)
        missings = _materialize_missing(ctx, finding)
        for missing in missings:
            ctx.store.put(missing)
    return finding, missings


def _ask_model(
    ctx: RunContext,
    *,
    ids: dict[str, str],
    candidate: Candidate,
    capsule: ContextCapsule,
    evidences: tuple[Evidence, ...],
    callers: tuple[str, ...],
) -> Finding:
    template = load_prompt(ROLE, ctx.config.model_for(ROLE.value).prompt_template_version)
    source = ctx.read_source(capsule.target)
    prompt = template.render(
        facts=build_facts(
            run_id=ctx.run_id,
            ids=ids,
            artifact_id=candidate.artifact_id,
            target=capsule.target,
            capsule=capsule,
            source=source,
            evidences=evidences,
            callers=callers,
        ),
        candidate=_describe_candidate(candidate),
        target=describe_target(capsule.target),
        capsule_sections=describe_sections(capsule),
        source=source,
    )
    try:
        result = ctx.call_model(
            role=ROLE,
            prompt=prompt,
            schema=Finding,
            stage=Stage.AUDITOR,
            subject_kind="finding",
            subject_id=ids["finding_id"],
        )
    except BudgetExhausted:
        _queue_candidate(ctx, candidate, ReasonCode.BUDGET_EXHAUSTED)
        raise
    except Exception:
        _queue_candidate(ctx, candidate, ReasonCode.SCHEMA_PARSE_FAILED)
        raise
    return result.value


def _queue_candidate(ctx: RunContext, candidate: Candidate, reason: ReasonCode) -> None:
    """표 C-6·C-7. 후보를 폐기하지 않고 사유와 함께 queued로 둔다."""
    if candidate.state is not FindingState.TRIAGED:
        return
    ctx.apply(candidate, to=FindingState.QUEUED, reason=reason, stage=Stage.AUDITOR)


def _materialize_missing(ctx: RunContext, finding: Finding) -> list[MissingEvidence]:
    return [
        MissingEvidence(
            missing_id=missing_id,
            run_id=ctx.run_id,
            finding_id=finding.finding_id,
            need=_NEED,
            how_to_obtain=_HOW,
            blocking=True,
        )
        for missing_id in finding.missing_evidence
    ]


def _describe_candidate(candidate: Candidate) -> str:
    cwes = ", ".join(candidate.suspected_cwe) or "(없음)"
    reasons = ", ".join(candidate.reason_codes) or "(없음)"
    return f"{candidate.candidate_id} · 추정 CWE: {cwes} · 트리아지 신호: {reasons}"
