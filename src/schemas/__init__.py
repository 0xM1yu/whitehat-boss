"""에이전트 간에 오가는 구조체 전부.

roadmap.md §3: `src/schemas/`와 `src/interfaces/`는 트랙 간 유일한 결합점이므로
**변경 시 전원 공지 대상**이다.

각 트랙은 개별 모듈이 아니라 이 패키지에서 import 한다 — 파일이 갈라져도
import 경로가 흔들리지 않게 하기 위해서다.
"""

from src.schemas.artifact import Artifact
from src.schemas.candidate import Candidate, CandidateState
from src.schemas.capsule import CapsuleSection, ContextCapsule
from src.schemas.common import (
    ID_PREFIXES,
    Cwe,
    HarnessModel,
    Identifier,
    Location,
    NonEmptyStr,
    ProducedBy,
    Sha256Digest,
    UtcModel,
    to_utc_iso,
)
from src.schemas.decision import Decision
from src.schemas.enums import (
    ArtifactKind,
    CapsuleLevel,
    EvidenceGrade,
    EvidenceKind,
    FindingState,
    PathStepKind,
    ReasonCode,
    Role,
    Verdict,
)
from src.schemas.evidence import Evidence
from src.schemas.finding import (
    Finding,
    FindingStateValue,
    MissingEvidence,
    PathStep,
    ReproSpec,
)
from src.schemas.run import CONDITION_PATTERN, Budget, RunRecord, Sampling

__all__ = [
    "CONDITION_PATTERN",
    "ID_PREFIXES",
    "Artifact",
    "ArtifactKind",
    "Budget",
    "Candidate",
    "CandidateState",
    "CapsuleLevel",
    "CapsuleSection",
    "ContextCapsule",
    "Cwe",
    "Decision",
    "Evidence",
    "EvidenceGrade",
    "EvidenceKind",
    "Finding",
    "FindingState",
    "FindingStateValue",
    "HarnessModel",
    "Identifier",
    "Location",
    "MissingEvidence",
    "NonEmptyStr",
    "PathStep",
    "PathStepKind",
    "ProducedBy",
    "ReasonCode",
    "ReproSpec",
    "Role",
    "RunRecord",
    "Sampling",
    "Sha256Digest",
    "UtcModel",
    "Verdict",
    "to_utc_iso",
]
