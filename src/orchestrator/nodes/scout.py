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
    role: Role = ROLE,
) -> Candidate:
    """후보 하나를 만들고 트리아지 결과까지 적용해 돌려준다.

    `role`은 Scout 자리를 고른다 — 풀 없는 단일 `scout`(기본)이거나 풀 멤버
    (`scout_memory`·`scout_boundary`·`scout_injection`). **어느 쪽이든 단계는 4단계
    (`Stage.SCOUT`)이고 임계값·전이 경로는 같다** — 바뀌는 것은 어느 프롬프트·모델을
    부르는가뿐이다. `Candidate.scout_member` stamp는 노드가 아니라 `RunContext.call_model`이
    이 `role`에서 만든다(ADR 0010 §3, `candidate.py` 필드 주석).
    """
    candidate_id = ctx.ids.next("candidate")
    with ctx.stage(Stage.SCOUT):
        candidate = _ask_model(
            ctx,
            candidate_id=candidate_id,
            artifact=artifact,
            capsule=capsule,
            evidences=evidences,
            previous=previous,
            role=role,
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
    role: Role,
) -> Candidate:
    """프롬프트를 만들어 모델에 묻는다. 실패는 사유 코드로 기록하고 그대로 올린다."""
    template = load_prompt(role, ctx.config.model_for(role.value).prompt_template_version)
    source = ctx.read_source(capsule.target)
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
            role=role,
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


def describe_target(target: Location) -> str:
    """프롬프트 본문의 사람이 읽는 대상 표기. 사실 블록에는 구조화된 값이 따로 간다."""
    lines = ""
    if target.line_start is not None:
        lines = f" (줄 {target.line_start}-{target.line_end or target.line_start})"
    return f"`{target.file}` 의 `{target.symbol or '(파일 전체)'}`{lines}"


def describe_sections(capsule: ContextCapsule) -> str:
    """캡슐 섹션의 **본문까지** 프롬프트에 렌더한다 (W1).

    예전에는 `callers(312 tok)` 같은 **이름표만** 냈다. 그래서 #63이 실제 호출 그래프를
    캡슐에 넣은 뒤에도 모델은 그것을 보지 못했고, "못 봐서 못 찾은 건지 봤는데 못 찾은
    건지"가 구별되지 않았다 — 그 상태에서 잰 성능 수치는 하네스가 아니라 Fake 어댑터를
    잰 값이다.

    **같은 내용이 `build_facts()`의 사실 블록에도 간다.** 중복이 아니라 계약이다:
    body.md가 "아래 JSON이 참조 가능한 사실 전부다"라고 못박으므로, 산문에만 있는 것은
    모델이 무시해도 되는 값이 된다. `{{SOURCE}}`가 이미 같은 이유로 양쪽에 실린다
    (`bench.sized_prompt` docstring이 그 2배 계상을 적어 뒀다).

    **본문이 없는 섹션은 이름표로 남는다.** `target_function`(원문은 `{{SOURCE}}`가
    싣는다)·`static_signal`·`known_facts`(데이터원이 아직 없다)가 그렇다 —
    없는 본문을 지어내지 않는다.
    """
    if not capsule.sections:
        return "(없음)"
    lines: list[str] = []
    for section in capsule.sections:
        counted = "" if section.items is None else f", {section.items}건"
        lines.append(f"- {section.kind} ({section.tokens} tok{counted})")
        # 여러 줄짜리 본문(예: `related`의 코드 블록)은 **모든 줄**을 들여쓴다.
        # `f"    {entry}"`는 첫 줄만 붙어 나머지가 왼쪽에 붙던 자리다(그래프 줄은 한 줄이라
        # 안 드러났다).
        for entry in section.body:
            lines.extend(f"    {line}" for line in entry.splitlines())
    return "\n".join(lines)
