"""Finding / MissingEvidence / ReproSpec 스키마 (agent-contracts.md §3)."""

from typing import Literal

from pydantic import Field, model_validator

from src.schemas.common import (
    Cwe,
    HarnessModel,
    Identifier,
    NonEmptyStr,
    ProducedBy,
)
from src.schemas.enums import FindingState, PathStepKind

# Finding이 가질 수 있는 상태 (architecture.md §4). Candidate 쪽 값(candidate/triaged)은
# Auditor가 Finding을 만드는 시점에 이미 지나갔으므로 제외한다.
FindingStateValue = Literal[
    FindingState.HYPOTHESIZED,
    FindingState.QUEUED,
    FindingState.VALIDATED,
    FindingState.PROVEN,
    FindingState.REPORTED,
]

# 근거 없이 도달할 수 없는 상태. Evidence Gate E3 이상이 필요한 구간이다.
_EVIDENCE_BACKED_STATES = frozenset(
    {FindingState.VALIDATED, FindingState.PROVEN, FindingState.REPORTED}
)


class PathStep(HarnessModel):
    """source→sink 경로의 한 단계."""

    step: PathStepKind
    symbol: NonEmptyStr
    note: str | None = None


class MissingEvidence(HarnessModel):
    """Auditor가 "코드에서 확인 불가"라고 판단해 요청한 근거 (agent-contracts.md §2.2).

    `blocking=True`면 이 근거 없이는 Evidence Gate를 통과시키지 않는다.
    """

    missing_id: Identifier
    run_id: Identifier
    finding_id: Identifier
    need: NonEmptyStr
    how_to_obtain: NonEmptyStr
    blocking: bool


class ReproSpec(HarnessModel):
    """재현 명세.

    제안: architecture.md §4의 E3 조건이 "Critic이 반박 실패 + **재현 명세 존재**"이고
    §3의 8단계 입력이 "`Finding` + 재현 명세"인데, 문서에 이 구조체가 없다.
    8단계 Tool Validation이 무엇을 실행하고 무엇을 성공으로 볼지가 없으면 E3→E4 판정이
    사람 판단이 되므로 신설했다.
    """

    # 샌드박스에서 순서대로 실행할 단계. 실행 자체는 ToolRunner가 한다.
    steps: list[NonEmptyStr] = Field(min_length=1)
    # 무엇이 관측되면 재현 성공인가. 예: "ASAN heap-buffer-overflow at parse_header".
    expected_signal: NonEmptyStr


class Finding(HarnessModel):
    """Auditor가 세운 취약점 가설과 그 이후의 상태.

    **전이 시 주의**: `model_copy(update=...)`는 아래 검증기를 우회한다. 상태 전이는
    `HarnessModel.transition()`으로 한다 — 그래야 `proven`으로 올릴 때 재현 명세와
    근거가 있는지 실제로 확인된다.
    """

    finding_id: Identifier
    run_id: Identifier
    candidate_id: Identifier
    title: NonEmptyStr
    cwe: Cwe
    state: FindingStateValue
    confidence: float = Field(ge=0.0, le=1.0)
    path: list[PathStep] = Field(default_factory=list)
    rca_draft: str = ""
    evidence_refs: list[Identifier] = Field(default_factory=list)
    missing_evidence: list[Identifier] = Field(default_factory=list)
    produced_by: ProducedBy
    repro_spec: ReproSpec | None = None

    @model_validator(mode="after")
    def _promoted_states_need_backing(self) -> "Finding":
        """validated 이상은 재현 명세와 근거를 동반해야 한다.

        architecture.md §4: E3(`validated`)의 조건 자체가 "재현 명세 존재"이고, E4
        (`proven`)는 재현 로그를 요구한다. 근거 0건짜리 `validated`는 이 시스템의
        전제(Evidence 중심 판정)를 정면으로 뒤집으므로 만들어질 수 없어야 한다.
        `hypothesized`/`queued` 초안 단계에는 요구하지 않는다.
        """
        if self.state not in _EVIDENCE_BACKED_STATES:
            return self
        if self.repro_spec is None:
            raise ValueError(f"state={self.state}에는 repro_spec이 필요하다 (architecture §4 E3)")
        if not self.evidence_refs:
            raise ValueError(f"state={self.state}에는 evidence_refs가 최소 1건 필요하다")
        if not self.path:
            raise ValueError(f"state={self.state}에는 source→sink 경로가 필요하다")
        return self
