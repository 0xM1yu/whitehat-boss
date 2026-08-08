"""6단계 Evidence Gate — 근거 등급을 매기고 등급을 전이로 옮긴다.

판정 자체는 `gate.evaluate()`(순수 함수)가 하고, 등급 → 전이 매핑은 정책이 한다
(전이 표 F-2/F-3/F-4). 이 노드는 둘을 잇고 그 사이의 기록을 남긴다.

**애매한 경우에도 모델을 부르지 않는다.** architecture.md §3은 6단계에 "애매한 경우에만
모델 호출"을 열어 두지만 v1에서는 닫는다 — 판정 경로에 모델이 끼면 결정론이 깨지고
제거율이 조건마다 달라진다. E1↔E2 경계는 `gate_ambiguous`로 기록만 하고 E1으로 확정된다.
"""

from src.orchestrator import gate
from src.orchestrator.context import RunContext
from src.orchestrator.gate import GateResult
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.stages import Stage
from src.schemas.common import Location
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence


def run(
    ctx: RunContext,
    *,
    finding: Finding,
    evidences: tuple[Evidence, ...] = (),
    missings: tuple[MissingEvidence, ...] = (),
    candidate_location: Location | None = None,
) -> tuple[Finding, GateResult]:
    """등급을 매기고 그 결과를 전이로 적용한 Finding을 돌려준다.

    `evidences`는 이 Finding이 참조하는 것만 골라 넘긴다 — 다른 Finding의 근거로
    등급이 올라가면 제거율이 통째로 무의미해진다.
    """
    with ctx.stage(Stage.GATE):
        result = ctx.evaluate_gate(
            finding,
            gate.evidences_for(finding, evidences),
            missings=missings,
            stage=Stage.GATE,
            candidate_location=candidate_location,
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
