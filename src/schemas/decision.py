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


class DecisionDraft(HarnessModel):
    """Critic 구조화 출력 **파싱용** — 모델이 정하는 필드만 담는다.

    `Decision.verdict`를 스키마에 `enum`으로 강제하면 grammar 마스크가 모델의 맞는
    추론을 틀린 토큰으로 덮는다(gpt-oss-20b 실측 2026-09-12: enum 강제 2/5, enum 제거
    5/5 — **원천: PR #182 본문, 원문 미커밋**. 같은 날 커밋된
    `docs/experiments/harness/2026-09-12-critic-decisiondraft-budget-atom.md`는 **다른
    실험**이고 §5가 그 사실의 자리다).
    그래서 Critic 요청은 이 스키마로 보내 `verdict`를 자유 문자열로 받고,
    `to_decision()`이 `Verdict.from_text`로 표준값에 매핑해 `Decision`을 만든다.

    **ID·`produced_by`는 여기 없다 — 하네스가 주입한다.** 모델에 provenance를 맡기면
    오염된다(실측: `produced_by.model`이 `"ChatGPT"` 등으로 나온다). `decision_id`·`run_id`·
    `finding_id`·`produced_by`는 `RunContext`와 실제 호출 모델에서 결정적으로 넣는다.
    """

    verdict: NonEmptyStr = Field(
        description="Your verdict. Prefer exactly one of: uphold, rebut, need_evidence."
    )
    rebuttal_axis: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr
    evidence_refs: list[Identifier] = Field(default_factory=list)

    def to_decision(
        self,
        *,
        decision_id: Identifier,
        run_id: Identifier,
        finding_id: Identifier,
        produced_by: ProducedBy,
    ) -> Decision:
        """자유 `verdict`를 표준값으로 매핑하고, 하네스가 주는 ID·provenance로 `Decision`을 만든다.

        `verdict`를 못 읽으면(`from_text`가 `None`) **`need_evidence`(보류)** 다 —
        미해석 verdict를 `uphold`(지지)로 소비하지 않는다. 그 사건은 호출자가 trace로 남긴다.
        """
        verdict = Verdict.from_text(self.verdict)
        return Decision(
            decision_id=decision_id,
            run_id=run_id,
            finding_id=finding_id,
            verdict=verdict if verdict is not None else Verdict.NEED_EVIDENCE,
            rebuttal_axis=self.rebuttal_axis,
            rationale=self.rationale,
            evidence_refs=self.evidence_refs,
            produced_by=produced_by,
        )

    def verdict_interpreted(self) -> bool:
        """자유 `verdict`가 표준값으로 읽혔나. `False`면 `to_decision`이 보류로 보낸다."""
        return Verdict.from_text(self.verdict) is not None
