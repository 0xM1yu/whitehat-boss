"""6단계 Evidence Gate — 근거 등급을 매기고 등급을 전이로 옮긴다.

판정 자체는 `gate.evaluate()`(순수 함수)가 하고, 등급 → 전이 매핑은 정책이 한다
(전이 표 F-2/F-3/F-4). 이 노드는 둘을 잇고 그 사이의 기록을 남긴다.

**애매한 경우에도 모델을 부르지 않는다.** architecture.md §3은 6단계에 "애매한 경우에만
모델 호출"을 열어 두지만 v1에서는 닫는다 — 판정 경로에 모델이 끼면 결정론이 깨지고
제거율이 조건마다 달라진다. E1↔E2 경계는 `gate_ambiguous`로 기록만 하고 E1으로 확정된다.
"""

from collections.abc import Sequence

from src.orchestrator import gate
from src.orchestrator.context import RunContext
from src.orchestrator.gate import GateResult
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.stages import Stage
from src.schemas.common import Location
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence


def attach_observed(ctx: RunContext, finding: Finding, evidences: Sequence[Evidence]) -> Finding:
    """하네스가 **관측한** Evidence를 Finding의 근거로 붙인다. 8단계도 이 함수를 쓴다.

    상태 변경이 아니라 데이터 보강이므로 `transition()`을 직접 쓴다(검증은 그대로 돈다).
    8단계에서 이걸 하지 않으면 게이트가 그 단계의 산출물을 못 보고 E4가 성립하지 않는다.

    **`ok`로 걸러내지 않는다.** 실패한 Evidence는 등급에 안 세지만(`gate.evaluate`의 `usable`)
    Critic 프롬프트에는 실려야 한다 — 「도구가 실패했다」도 심사 재료다.

    **⚠ 같은 `(kind, obj_id)`로 upsert하므로 5단계가 저장한 `evidence_refs`가 여기서 덮인다**
    (이슈 #554). 부착 뒤 `state.db`에서는 모델이 몇 건 인용했는지 셀 수 없다. 그 수는 **부착 전에**
    `run()`이 세어 `gate_eval.cited_by_model`로 남긴다 - `state.db`를 세던 옛 방법은 쓰지 않는다.
    """
    new_refs = [ev.evidence_id for ev in evidences if ev.evidence_id not in finding.evidence_refs]
    if not new_refs:
        return finding
    updated = finding.transition(evidence_refs=[*finding.evidence_refs, *new_refs])
    ctx.store.put(updated)
    return updated


def run(
    ctx: RunContext,
    *,
    finding: Finding,
    evidences: tuple[Evidence, ...] = (),
    missings: tuple[MissingEvidence, ...] = (),
    candidate_location: Location | None = None,
) -> tuple[Finding, GateResult]:
    """등급을 매기고 그 결과를 전이로 적용한 Finding을 돌려준다.

    `evidences`는 **이 Finding의 타깃에서 관측된 것**을 넘긴다(러너는 `item.target.evidences` —
    artifact 범위다). 범위를 넓히면 다른 Finding의 근거로 등급이 올라가 제거율이 통째로
    무의미해진다. 이 노드는 **받은 것만** 쓴다 — 저장소에서 더 끌어오지 않는다.

    **등급을 매기기 전에 그것을 `evidence_refs`에 붙인다 (이슈 #552).** 붙이지 않으면
    `evidences_for`가 모델이 인용한 것만 남기므로 **심사 대상이 심사 여부를 정한다** —
    `evidence_refs=[]`로 나온 Finding은 관측된 정적 신호가 있어도 E0가 되어 Critic을 못 본다.
    실측(`run_20261007_0001`): Finding 5건이 그렇게 전부 E0 → `queued`였고 7단계 이벤트가
    0건이었다. 같은 풀을 붙이면 5건 전부 E1이다.
    """
    with ctx.stage(Stage.GATE):
        # 부착이 목록을 덮기 **전**에 센다(이슈 #554). 이 시점의 `evidence_refs`가 모델의 원본이다.
        cited_by_model = len(finding.evidence_refs)
        finding = attach_observed(ctx, finding, evidences)
        result = ctx.evaluate_gate(
            finding,
            gate.evidences_for(finding, evidences),
            missings=missings,
            stage=Stage.GATE,
            candidate_location=candidate_location,
            cited_by_model=cited_by_model,
        )
        action = ctx.decide(
            RoutingRequest(
                point=DecisionPoint.GATE_GRADE,
                stage=Stage.GATE,
                subject_kind="finding",
                subject_id=finding.finding_id,
                signals={"grade": result.grade.value},
            )
        )
        return ctx.apply_action(finding, action, stage=Stage.GATE), result
