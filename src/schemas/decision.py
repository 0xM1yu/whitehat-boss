"""Decision 스키마 (agent-contracts.md §3, 파이프라인 7단계 Critic 출력)."""

from pydantic import Field

from src.schemas.common import (
    HarnessModel,
    Identifier,
    NonEmptyStr,
    ProducedBy,
)
from src.schemas.enums import RebuttalAxis, Verdict

# `Decision.ungrounded_reason`의 값 - `critic_normalized` trace의 `reason`이 그대로 쓴다(ADR 0015).
EMPTY_AXIS = "empty_axis"
INVALID_AXIS = "invalid_axis"


class Decision(HarnessModel):
    """Critic의 반박 판정.

    **근거 없는 반박을 스키마에서 막지 않는다.** agent-contracts.md §2.3은 근거 없는 반박을
    **`need_evidence`로 처리하되 사유와 함께 지표에 기록한다**고 정한다(ADR 0015 - 그 문서의 옛
    규칙은 취소선이 됐다). Critic의 구조화 출력은
    이 객체로 직접 파싱되므로, 여기서 ValidationError를 던지면 "근거 없는 반박"이
    "스키마 파싱 실패"로 뭉개져 지표를 잃는다. 두 실패는 모델 비교에서 다른 의미다.

    따라서 Decision은 Critic이 말한 것을 그대로 담고, 정규화(`rebut` → `need_evidence`)와
    지표 기록은 오케스트레이터가 `is_grounded`를 보고 수행한다(PR 2).
    이 분담은 확정이 아니다 — docs/agent-contracts.md §3 제안 항목 참조.

    ⏵ 2026-10-13 (ADR 0015) — 정규화 목적지가 `uphold`에서 **`need_evidence`**로 바뀌었다. 근거
    없는 반박이 `uphold`로 읽히면 게이트 `p3`이 서서 Finding이 `validated`까지 올라간다 —
    「Critic이 반박을 못 해서 승격」이다. 근거의 정의도 넓어졌다(`is_grounded`).
    """

    decision_id: Identifier
    run_id: Identifier
    finding_id: Identifier
    verdict: Verdict
    # 반박 축. 어휘는 `RebuttalAxis` 열 값이다(agent-contracts.md §2.3). ⏵ 2026-10-13 (ADR 0015) —
    # 열거형 승격은 **했다**, 다만 필드 타입은 자유 문자열 그대로다: 스키마에서 `enum`으로 강제하면
    # grammar 마스크가 모델의 맞는 답을 덮는다(아래 `DecisionDraft` 주석 — gpt-oss-20b 실측
    # 2026-09-12: 강제 2/5, 제거 5/5). 어휘 검사는 파싱 뒤 `is_grounded`가 한다.
    rebuttal_axis: list[NonEmptyStr] = Field(default_factory=list)
    rationale: NonEmptyStr
    evidence_refs: list[Identifier] = Field(default_factory=list)
    produced_by: ProducedBy

    @property
    def invalid_axes(self) -> list[str]:
        """`RebuttalAxis`로 못 읽은 반박 축 원문. 순서·중복을 그대로 둔다."""
        return [axis for axis in self.rebuttal_axis if RebuttalAxis.from_text(axis) is None]

    @property
    def ungrounded_reason(self) -> str | None:
        """반박이 근거를 못 갖춘 사유 - 갖췄으면 `None` (agent-contracts.md §2.3 · ADR 0015).

        근거는 둘 중 하나다. ① `evidence_refs`가 비어 있지 않다 - v6(2026-10-14 전의 서빙 판)의
        `rebut`은 등록된 ID를 인용한다. ② `rebuttal_axis`가 비어 있지 않고 **전부**
        `RebuttalAxis`다 - v7은 `rebut`에 ID를 금지하고 축마다 SOURCE 코드 인용을 요구한다.
        ⚠ **②는 v7 전용이 아니다** — 이 술어에 판 구분이 없어 v6 `rebut`도 ID 없이 유효 축만
        들면 ①을 안 거쳐 ②로 근거가 된다(ADR 0015의 v6 뒤집힘 표). 위 v6/v7 꼬리표는
        「그 규약을 요구하는 프롬프트」를 가리키지 술어의 적용 범위가 아니다.
        ⚠ ②는 **모델의 자기 신고**다. 인용한 코드가 SOURCE에 실재하는지는 보지 않는다(ADR 0015 P0b,
        보류). 어휘 안인 축이 근거의 자격이지 근거의 참이 아니다.
        """
        if self.evidence_refs:
            return None
        if not self.rebuttal_axis:
            return EMPTY_AXIS
        if self.invalid_axes:
            return INVALID_AXIS
        return None

    @property
    def is_grounded(self) -> bool:
        """반박이 근거를 동반하는가 (agent-contracts.md §2.3).

        `bool(evidence_refs) or (rebuttal_axis가 비어 있지 않고 전부 RebuttalAxis)`. `False`인
        `rebut`은 오케스트레이터가 **`need_evidence`**로 정규화하고 그 사건을 사유와 함께 지표에
        남긴다 (⏵ 2026-10-13, ADR 0015 - 전엔 `uphold`로 정규화했다). 여기서 판정만 제공하고
        처리는 하지 않는다.
        """
        return self.ungrounded_reason is None


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
