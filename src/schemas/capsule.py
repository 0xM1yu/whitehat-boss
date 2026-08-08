"""ContextCapsule 스키마 (knowledge-base.md §4, 파이프라인 3단계 산출물)."""

from pydantic import Field, model_validator

from src.schemas.common import (
    HarnessModel,
    Identifier,
    Location,
    NonEmptyStr,
    Sha256Digest,
)
from src.schemas.enums import CapsuleLevel


class CapsuleSection(HarnessModel):
    """캡슐을 구성하는 섹션 하나와 그 토큰 비용.

    `kind` 값은 knowledge-base.md §4 예시 기준: `target_function` / `callers` /
    `callees` / `types` / `static_signal` / `known_facts`. 사실상 닫힌 집합이지만
    Progressive Expansion(§5)이 어떤 섹션을 더 붙일지 아직 확정되지 않아 열어 둔다.
    """

    kind: NonEmptyStr
    tokens: int = Field(ge=0)
    # 섹션이 여러 항목을 묶을 때의 개수(예: callers 3개).
    items: int | None = Field(default=None, ge=0)


class ContextCapsule(HarnessModel):
    """후보 하나에 대응하는 최소 분석 문맥.

    **전이 시 주의**: 확장은 `HarnessModel.transition()`으로 한다. `model_copy`는
    아래 예산 검증기를 우회하므로 상한을 넘긴 캡슐이 조용히 만들어진다.
    """

    capsule_id: Identifier
    run_id: Identifier
    # 문서 예시의 target은 {file, symbol}이고 이는 Location의 부분집합이라 재사용한다.
    target: Location
    level: CapsuleLevel
    budget_tokens: int = Field(gt=0)
    sections: list[CapsuleSection] = Field(default_factory=list)
    evidence_refs: list[Identifier] = Field(default_factory=list)
    # artifact digest + symbol + 정책 버전으로 만든 캐시 키 (knowledge-base.md §6).
    cache_key: Sha256Digest
    # Progressive Context Expansion 횟수 (knowledge-base.md §5).
    # **상한은 여기서 강제하지 않는다.** 문서가 "초기값 3, 실측 후 조정"으로 둔 정책값이고
    # engineering-guide.md §8.5는 임계값을 config에서 읽으라고 정한다. 상한 판정은
    # 오케스트레이터가 config를 보고 한다(초과 시 후보를 queued로).
    expansion_count: int = Field(default=0, ge=0)

    @property
    def used_tokens(self) -> int:
        return sum(section.tokens for section in self.sections)

    @model_validator(mode="after")
    def _within_budget(self) -> "ContextCapsule":
        """섹션 토큰 합이 이 캡슐의 예산을 넘지 않아야 한다.

        architecture.md §3의 3단계 통과 조건("capsule 토큰 상한 이내")이다. 이것은
        config에서 오는 정책 임계값이 아니라 **캡슐 자신이 들고 있는 `budget_tokens`와
        `sections` 사이의 자기 일관성**이므로(Location의 line_end >= line_start와 동급)
        스키마에서 막는다. 예산을 넘겼다면 캡슐을 만들 게 아니라 섹션을 줄여야 한다.
        """
        used = self.used_tokens
        if used > self.budget_tokens:
            raise ValueError(
                f"섹션 토큰 합({used})이 budget_tokens({self.budget_tokens})를 초과한다"
            )
        return self
