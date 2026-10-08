"""StateStore 인터페이스 (engineering-guide.md §3).

저장소 구현(SQLite vs DuckDB)은 open-questions.md ADR 0001에서 확정한다. 이 Protocol에
대고 개발하면 그 결정이 노드 코드에 닿지 않는다.
"""

from typing import Literal, Protocol

from src.schemas.artifact import Artifact
from src.schemas.candidate import Candidate
from src.schemas.capsule import ContextCapsule
from src.schemas.decision import Decision
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence
from src.schemas.run import RunRecord

# 저장 가능한 객체 종류. `common.ID_PREFIXES`의 키와 같은 집합이어야 한다
# (tests/test_schemas.py가 검사한다). 자유 문자열로 두면 오타가 조용히 빈 결과를 낸다.
ObjectKind = Literal[
    "artifact",
    "candidate",
    "evidence",
    "finding",
    "missing_evidence",
    "decision",
    "capsule",
    "run",
]

StoredObject = (
    Artifact
    | Candidate
    | Evidence
    | Finding
    | MissingEvidence
    | Decision
    | ContextCapsule
    | RunRecord
)


class StateStore(Protocol):
    """run·candidate·evidence·metric 저장.

    engineering-guide.md §4: 노드는 상태를 이 인터페이스를 통해서만 읽고 쓴다.
    오케스트레이터를 LangGraph에서 다른 것으로 옮겨도 노드 코드가 그대로 남는 근거다.
    """

    def put(self, obj: StoredObject) -> str: ...

    def get(self, kind: ObjectKind, id: str) -> StoredObject | None: ...

    def query(self, kind: ObjectKind, **filters: object) -> list[StoredObject]: ...

    def close(self) -> None:
        """잡은 자원(파일 핸들 등)을 놓는다. 두 번 불러도 된다.

        run을 연 쪽(CLI)이 부른다 — 열린 sqlite 파일은 Windows에서 그 디렉터리 정리를
        `PermissionError`로 막는다(#156).
        """
        ...
