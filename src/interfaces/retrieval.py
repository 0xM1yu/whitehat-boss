"""EmbeddingClient·RerankerClient 인터페이스 (knowledge-base.md §3의 2단계 구조).

**`ModelClient`와 왜 분리하는가.** `ModelClient.complete(role, prompt, schema)`는
"프롬프트를 주고 스키마를 강제해 구조화 출력을 받는다"는 계약이다. 임베딩은 벡터를,
리랭커는 점수를 돌려주므로 그 시그니처에 들어가지 않는다.

`Role`에 `EMBEDDING`/`RERANKER`를 추가하는 방법도 있으나 택하지 않았다. `Role`은
agent-contracts.md §1의 **실행 계약 역할**이고 프롬프트 버전·출력 스키마·재시도 정책이
붙는 개념이다. 프롬프트도 스키마도 없는 검색 부품을 같은 열거형에 넣으면 그 계약이
흐려지고, `RunRecord.prompt_versions`처럼 Role을 키로 쓰는 기록에 빈 항목이 생긴다.

대신 모델 선택은 `ModelEntry.endpoint`가 하는 기존 규칙을 그대로 쓴다 — 프로필의
`roles.embedding` / `roles.reranker`가 registry 키를 가리키고, factory가 그 endpoint로
아래 구현을 만든다. 그래서 이 두 역할도 `fake://`면 자동으로 Fake가 된다.
"""

from collections.abc import Sequence
from typing import Protocol

from pydantic import Field

from src.schemas.common import HarnessModel


class Ranked(HarnessModel):
    """리랭커가 매긴 순위 한 건.

    문서 본문이 아니라 **입력 순서의 인덱스**를 돌려준다. 본문을 돌려주면 호출자가
    원본과 대조해 어느 후보였는지 되짚어야 하고, 리랭커가 문자열을 정규화했을 때
    대조가 깨진다.
    """

    index: int = Field(ge=0)
    score: float


class EmbeddingClient(Protocol):
    """텍스트 → 벡터. 광역 수집 결과를 1차로 좁히는 데 쓴다.

    배치로 받는다. 후보 수백 건을 한 건씩 호출하면 왕복 지연이 곧 병목이 되고,
    model-serving.md §2가 embedding을 "크게 배칭"으로 규정한 이유가 그것이다.
    """

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class RerankerClient(Protocol):
    """(query, documents) → 상위 `top_n` 순위.

    embedding이 좁힌 후보를 cross-encoder로 재선별한다(knowledge-base.md §3 2단계).
    `top_n`이 문서 수보다 크면 있는 만큼만 돌려준다 — 부족을 오류로 만들면 후보가
    적은 저장소에서 검색 자체가 실패한다.
    """

    def rerank(self, *, query: str, documents: Sequence[str], top_n: int) -> list[Ranked]: ...
