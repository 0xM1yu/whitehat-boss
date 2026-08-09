"""Candidate 스키마 (agent-contracts.md §3, 파이프라인 4단계 Scout 출력)."""

from typing import Literal

from pydantic import Field

from src.schemas.common import Cwe, HarnessModel, Identifier, Location, NonEmptyStr
from src.schemas.enums import FindingState

# Candidate가 가질 수 있는 상태. 상태 머신은 하나지만(enums.FindingState) 각 객체가
# 취할 수 있는 값은 다르다 — architecture.md §4 기준 Candidate는 여기까지다.
# `queued`는 4단계에서 임계값 미달로 탈락한 후보가 머무는 상태다(즉시 폐기하지 않는다).
CandidateState = Literal[
    FindingState.CANDIDATE,
    FindingState.TRIAGED,
    FindingState.QUEUED,
]


class Candidate(HarnessModel):
    """Scout/Triage가 만든 후보.

    `reason_codes`는 `enums.ReasonCode`(상태 전이 사유)와 **다른 축**이다. 문서 예시의
    `memcpy_unbounded`·`changed_in_diff`는 전이 사유가 아니라 트리아지 신호이고,
    agent-contracts.md §2.4가 열거형 고정을 요구하는 대상은 Evidence Gate의 전이 사유다.
    Scout 휴리스틱은 계속 늘어나므로 지금 열거형으로 닫지 않는다.
    """

    candidate_id: Identifier
    run_id: Identifier
    artifact_id: Identifier
    location: Location
    suspected_cwe: list[Cwe] = Field(default_factory=list)
    # Scout 점수. 승격 임계값(scout_threshold)은 config에서 온다(engineering-guide.md §8.5).
    score: float = Field(ge=0.0, le=1.0)
    reason_codes: list[NonEmptyStr] = Field(default_factory=list)
    evidence_refs: list[Identifier] = Field(default_factory=list)
    state: CandidateState
