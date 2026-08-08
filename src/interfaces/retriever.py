"""Retriever 인터페이스 (engineering-guide.md §3, knowledge-base.md §3·§5)."""

from typing import Protocol

from pydantic import Field

from src.schemas.capsule import ContextCapsule
from src.schemas.common import HarnessModel, Identifier, Location, NonEmptyStr
from src.schemas.enums import CapsuleLevel
from src.schemas.finding import MissingEvidence


class Query(HarnessModel):
    """캡슐을 만들 대상과 검색 힌트.

    knowledge-base.md §3의 2단계 구조(광역 수집 → reranker 재선별)는 구현체 내부
    사정이다. 호출자는 "무엇에 대해, 얼마 안에" 만들지만 지정한다.
    """

    run_id: Identifier
    target: Location
    # FTS·embedding 검색에 쓸 힌트. 비어 있으면 symbol·graph 경로만 쓴다.
    keywords: list[NonEmptyStr] = Field(default_factory=list)
    # 만들 캡슐의 토큰 상한. config에서 온 값이 여기로 전달된다.
    budget_tokens: int = Field(gt=0)


class Retriever(Protocol):
    """Hybrid retrieval과 Progressive Context Expansion.

    `expand`는 캡슐을 제자리 변경하지 않고 새 캡슐을 반환한다. 확장 횟수 상한 판정은
    호출자(오케스트레이터)가 config를 보고 하며, 상한을 넘기면 후보를 `queued`로 둔다
    (knowledge-base.md §5, `ReasonCode.CAPSULE_EXPANSION_EXHAUSTED`).
    """

    def search(self, query: Query, level: CapsuleLevel) -> ContextCapsule: ...

    def expand(self, capsule: ContextCapsule, need: MissingEvidence) -> ContextCapsule: ...
