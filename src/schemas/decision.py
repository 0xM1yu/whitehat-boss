"""Decision 스키마 (agent-contracts.md §3, 파이프라인 7단계 Critic 출력)."""

from pydantic import Field

from src.schemas.common import (
    HarnessModel,
    Identifier,
    NonEmptyStr,
    ProducedBy,
)
from src.schemas.enums import Verdict


class Decision(HarnessModel):
    """Critic의 반박 판정.

    **근거 없는 반박을 스키마에서 막지 않는다.** agent-contracts.md §2.3은 "근거 없는
    반박은 `uphold`로 처리하되 **지표에 기록한다**"고 정한다. Critic의 구조화 출력은
    이 객체로 직접 파싱되므로, 여기서 ValidationError를 던지면 "근거 없는 반박"이
    "스키마 파싱 실패"로 뭉개져 지표를 잃는다. 두 실패는 모델 비교에서 다른 의미다.

    따라서 Decision은 Critic이 말한 것을 그대로 담고, 정규화(`rebut` → `uphold`)와
    지표 기록은 오케스트레이터가 `is_grounded`를 보고 수행한다(PR 2).
    이 분담은 확정이 아니다 — docs/agent-contracts.md §3 제안 항목 참조.
    """

    decision_id: Identifier
    run_id: Identifier
    finding_id: Identifier
    verdict: Verdict
    # 반박 축. agent-contracts.md §2.3 체크리스트: guard / reachability / aliasing /
    # lifetime / 컴파일 타임 조건·플래그 / 입력 도달성. 열거형 승격은 문서 제안 항목.
    rebuttal_axis: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr
    evidence_refs: list[Identifier] = Field(default_factory=list)
    produced_by: ProducedBy

    @property
    def is_grounded(self) -> bool:
        """반박이 근거를 동반하는가 (agent-contracts.md §2.3).

        `False`인 `rebut`은 오케스트레이터가 `uphold`로 정규화하고 그 사건을 지표에
        남긴다. 여기서 판정만 제공하고 처리는 하지 않는다.
        """
        return bool(self.evidence_refs)
