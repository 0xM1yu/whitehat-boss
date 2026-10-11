"""에이전트 간에 오가는 구조체 전부.

`src/schemas/`와 `src/interfaces/`는 여러 모듈이 함께 의존하는 유일한 결합점이다.

각 모듈은 개별 파일이 아니라 이 패키지에서 import 한다 — 파일이 갈라져도
import 경로가 흔들리지 않게 하기 위해서다.
"""

from src.schemas.artifact import Artifact
from src.schemas.auditor import (
    AuditorAction,
    AuditorProcedureAction,
    AuditorProcedureAuditAlwaysAction,
    AuditorProcedureNullableCweAction,
    AuditProcedure,
    AuditProcedureAuditAlways,
    AuditProcedureNullableCwe,
)
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
from src.schemas.decision import Decision, DecisionDraft
from src.schemas.enums import (
    ArtifactKind,
    CapsuleLevel,
    EvidenceGrade,
    EvidenceKind,
    FindingState,
    PathStepKind,
    ReasonCode,
    ReproOrigin,
    Role,
    Verdict,
)
from src.schemas.evidence import Evidence
from src.schemas.finding import (
    AuditBlock,
    AuditBlockWithNoSignal,
    AuditDefense,
    AuditDefenseWithNoSignal,
    AuditOrigin,
    AuditPoint,
    AuditRefutation,
    AuditRefutationWithNoSignal,
    Finding,
    FindingStateValue,
    MissingEvidence,
    PathStep,
    ReproSpec,
)
from src.schemas.pov import PovAction, PovActionNamed, PovSegment, PovSpec
from src.schemas.run import CONDITION_PATTERN, Budget, RunRecord, Sampling
from src.schemas.scout_response import AnalysisSummary, ScoutResponse

__all__ = [
    "CONDITION_PATTERN",
    "ID_PREFIXES",
    "AnalysisSummary",
    "Artifact",
    "ArtifactKind",
    "AuditBlock",
    "AuditBlockWithNoSignal",
    "AuditDefense",
    "AuditDefenseWithNoSignal",
    "AuditOrigin",
    "AuditPoint",
    "AuditProcedure",
    "AuditProcedureAuditAlways",
    "AuditProcedureNullableCwe",
    "AuditRefutation",
    "AuditRefutationWithNoSignal",
    "AuditorAction",
    "AuditorProcedureAction",
    "AuditorProcedureAuditAlwaysAction",
    "AuditorProcedureNullableCweAction",
    "Budget",
    "Candidate",
    "CandidateState",
    "CapsuleLevel",
    "CapsuleSection",
    "ContextCapsule",
    "Cwe",
    "Decision",
    "DecisionDraft",
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
    "PovAction",
    "PovActionNamed",
    "PovSegment",
    "PovSpec",
    "ProducedBy",
    "ReasonCode",
    "ReproOrigin",
    "ReproSpec",
    "Role",
    "RunRecord",
    "Sampling",
    "ScoutResponse",
    "Sha256Digest",
    "UtcModel",
    "Verdict",
    "to_utc_iso",
]
