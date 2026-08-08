"""두 StateStore 구현이 공유하는 매핑 규칙.

`ObjectKind`(문자열)와 스키마 클래스, 그리고 각 스키마의 ID 필드 이름을 잇는다.
두 구현이 이 매핑을 따로 들고 있으면 한쪽만 갱신되는 사고가 난다 — 이 표는
`src/schemas/`에 종류가 추가될 때 손대야 하는 유일한 지점이다.
"""

from src.interfaces.state_store import ObjectKind, StoredObject
from src.schemas.artifact import Artifact
from src.schemas.candidate import Candidate
from src.schemas.capsule import ContextCapsule
from src.schemas.decision import Decision
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence
from src.schemas.run import RunRecord

# ObjectKind → (스키마 클래스, ID 필드명). 키 집합은 `common.ID_PREFIXES`와 같아야 하고
# tests/test_schemas.py가 이미 그 동치를 검사한다.
KIND_MAP: dict[str, tuple[type, str]] = {
    "artifact": (Artifact, "artifact_id"),
    "candidate": (Candidate, "candidate_id"),
    "evidence": (Evidence, "evidence_id"),
    "finding": (Finding, "finding_id"),
    "missing_evidence": (MissingEvidence, "missing_id"),
    "decision": (Decision, "decision_id"),
    "capsule": (ContextCapsule, "capsule_id"),
    "run": (RunRecord, "run_id"),
}


def resolve_kind(obj: StoredObject) -> str:
    """저장 객체의 `ObjectKind`를 되찾는다.

    `type(obj) is model`로 정확 일치만 본다 — 상속 관계로 판정하면 스키마가
    파생될 때 엉뚱한 종류로 저장된다.
    """
    for kind, (model, _) in KIND_MAP.items():
        if type(obj) is model:
            return kind
    raise TypeError(f"저장할 수 없는 객체 종류: {type(obj).__name__}")


def object_id(obj: StoredObject, kind: str) -> str:
    _, id_field = KIND_MAP[kind]
    return str(getattr(obj, id_field))


def model_for(kind: ObjectKind) -> type:
    return KIND_MAP[kind][0]


def filters_match(obj: StoredObject, filters: dict[str, object]) -> bool:
    """`query(kind, **filters)`의 필터 의미.

    필드 동등 비교만 한다. 없는 필드를 주면 조용히 빈 결과를 내지 않고 실패한다 —
    오타 필터가 "결과 0건"으로 보이면 파이프라인이 조용히 멈춘 것과 구별되지 않는다.
    """
    for key, expected in filters.items():
        if not hasattr(obj, key):
            raise AttributeError(f"{type(obj).__name__}에 필드 {key!r}이 없다")
        if getattr(obj, key) != expected:
            return False
    return True
