"""7단계 Independent Critic — 상위 Finding만 다른 계열 모델로 반박한다.

전이 표 F-5~F-9. 한 호출에서 두 사건이 순서대로 일어난다: 먼저 Critic 판정을 기록하고,
그다음 게이트가 E3이면 `validated`로 올린다. 그래서 F-5(무전이 `critic_upheld`)와
F-8(`validated` `critic_upheld`)이 같은 사유로 다른 상태에 간다.

**Auditor의 원문 추론을 전달하지 않는다**(agent-contracts.md §2.3). 프롬프트에 실리는
것은 주장·경로·근거 ID뿐이고 `rca_draft`는 넘기지 않는다 — 앞 모델의 설명을 보여 주면
반박이 아니라 재진술이 돌아온다.

**정규화는 여기 한 곳에서만 한다.** 근거 없는 `rebut`을 `uphold`로 바꾸는 판단이 두 곳에
있으면 "근거 없는 반박 비율"의 분자가 갈라진다. 노드가 `gate.normalize()`를 부르고,
`gate.was_normalized()`면 `critic_normalized`를 남긴 뒤 **정규화된 값**을 정책에 넘긴다.
"""

from src.orchestrator import gate
from src.orchestrator.context import RunContext
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.prompt_loader import build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.schemas.decision import Decision
from src.schemas.enums import Role
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence

ROLE = Role.CRITIC


def select(ctx: RunContext, findings: list[Finding]) -> list[Finding]:
    """Critic에 올릴 상위 Finding. 개수 상한은 config에서 온다(`critic_top_n`).

    Critic은 가장 비싼 단계이므로 상한이 있어야 한다(architecture.md §3 "상위 Finding만").
    confidence 동점은 `finding_id`로 가른다 — 정렬이 흔들리면 재현 실행이 갈라진다.
    """
    ordered = sorted(findings, key=lambda f: (-f.confidence, f.finding_id))
    return ordered[: ctx.config.thresholds.critic_top_n]


def run(
    ctx: RunContext,
    *,
    finding: Finding,
    evidences: tuple[Evidence, ...] = (),
    missings: tuple[MissingEvidence, ...] = (),
) -> tuple[Finding, Decision]:
    """반박을 받아 정규화하고, 재판정한 등급까지 반영해 전이시킨다."""
    decision_id = ctx.ids.next("decision")
    own = gate.evidences_for(finding, evidences)
    with ctx.stage(Stage.CRITIC):
        decision = _ask_model(ctx, decision_id=decision_id, finding=finding, evidences=own)
        ctx.store.put(decision)
        if gate.was_normalized(decision):
            ctx.record_critic_normalized(decision, stage=Stage.CRITIC)
        verdict = gate.normalize(decision)

        result = ctx.evaluate_gate(finding, own, [decision], missings, stage=Stage.CRITIC)
        action = ctx.decide(
            RoutingRequest(
                point=DecisionPoint.CRITIC_VERDICT,
                stage=Stage.CRITIC,
                subject_kind="finding",
                subject_id=finding.finding_id,
                signals={
                    "verdict": verdict.value,
                    "grade": result.grade.value,
                    "has_repro_spec": finding.repro_spec is not None,
                },
            )
        )
        return ctx.apply_action(finding, action, stage=Stage.CRITIC), decision


def _ask_model(
    ctx: RunContext, *, decision_id: str, finding: Finding, evidences: list[Evidence]
) -> Decision:
    template = load_prompt(ROLE, ctx.config.model_for(ROLE.value).prompt_template_version)
    prompt = template.render(
        facts=build_facts(
            run_id=ctx.run_id,
            ids={"finding_id": finding.finding_id, "decision_id": decision_id},
            evidences=evidences,
        ),
        claim=f"{finding.title} ({finding.cwe})",
        path=_describe_path(finding),
        evidence=_describe_evidence(evidences),
    )
    result = ctx.call_model(
        role=ROLE,
        prompt=prompt,
        schema=Decision,
        stage=Stage.CRITIC,
        subject_kind="finding",
        subject_id=finding.finding_id,
    )
    return result.value


def _describe_path(finding: Finding) -> str:
    """주장의 경로만 전달한다. `rca_draft`는 의도적으로 빠져 있다."""
    if not finding.path:
        return "(경로 없음)"
    return "\n".join(f"- {step.step.value}: `{step.symbol}`" for step in finding.path)


def _describe_evidence(evidences: list[Evidence]) -> str:
    if not evidences:
        return "(근거 없음)"
    return "\n".join(
        f"- {ev.evidence_id} · {ev.kind.value} · {'ok' if ev.ok else 'failed'} · {ev.source}"
        for ev in evidences
    )
