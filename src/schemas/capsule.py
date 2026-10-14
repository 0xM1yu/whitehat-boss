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
from src.tokens import estimate_tokens


class CapsuleSection(HarnessModel):
    """캡슐을 구성하는 섹션 하나와 그 토큰 비용.

    `kind` 값은 knowledge-base.md §4 예시 기준: `target_function` / `callers` /
    `callees` / `types` / `static_signal` / `known_facts`. 사실상 닫힌 집합이지만
    Progressive Expansion(§5)이 어떤 섹션을 더 붙일지 아직 확정되지 않아 열어 둔다.

    **그 목록은 목표이고 `types`는 아무 구현도 내지 않는다** — index-v1에 타입 데이터가 없어
    두 Retriever의 `LEVEL_SECTIONS`에서 뺐고, index-v2의 `types` 표는 캡슐이 아니라 NE resolver가
    요청받았을 때 준다(이슈 #63).
    스키마는 계속 허용한다; 막는 것은 이 클래스의 일이 아니다.

    `items`는 **실제로 담은 개수**다. 예산에 안 들어간 항목은 세지 않으므로
    `items=3`이 "전부 3건"인지 "여럿 중 3건"인지는 여기서 구별되지 않는다(#63) — 그 구별은
    `ContextCapsule.cuts`(`CapsuleCuts`)가 든다(#663).

    ## `body`가 비어 있는 것과 내용이 없는 것은 다르다

    `body`는 **프롬프트에 실제로 실리는 줄**이다. 비어 있어도 섹션은 성립한다:

    | 섹션 | `body` | 뜻 |
    | --- | --- | --- |
    | `callers`(간선 3개) | 3줄 | 이 줄들이 프롬프트에 그대로 간다 |
    | `callers`(간선 0건) | `[]` | 그래프를 봤는데 간선이 없다 (`tokens=0, items=0`) |
    | `target_function` | `[]` | 본문은 있지만 캡슐이 담지 않는다 (아래 주) |
    | `static_signal`·`known_facts` | `[]` | 데이터원이 아직 없다. 고정 64토큰 자리표시자 |

    `target_function`의 원문은 프롬프트를 만드는 쪽이 `read_symbol_source()`로
    (`input: slice` 프롬프트의 슬라이스 타깃 Scout는 `read_slice`로) 따로 읽는다 — 파일 전체를
    캡슐에 복사하면 매니페스트가 아니라 사본이 된다.

    즉 `body=[]`를 "본문이 없다"로 읽으면 안 되고 "이 섹션은 본문을 여기 담지 않는다"로
    읽어야 한다. 지어낸 줄로 채우지 않는 것이 이 구별의 값이다.
    """

    kind: NonEmptyStr
    tokens: int = Field(ge=0)
    # 섹션이 여러 항목을 묶을 때의 개수(예: callers 3개).
    items: int | None = Field(default=None, ge=0)
    # 프롬프트에 실리는 줄. 담는 섹션만 채운다(위 표).
    body: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _tokens_match_body(self) -> "CapsuleSection":
        """본문을 담았다면 `tokens`·`items`가 그 본문에서 나온 값이어야 한다.

        **이 검증기가 매니페스트와 내용의 드리프트를 구조적으로 불가능하게 만든다.**
        이 저장소가 반복해서 잡아온 결함이 전부 "숫자와 내용이 각각 계산되어 갈라진다"
        계열이었다(`fit_names`가 개수만 `min()`으로 깎던 것, 확장 경로의 `items=1`,
        `gpu_seconds`가 CPU 시간을 세던 것). 한 객체 안에서 서로를 강제하면 그 부류가
        생기지 않는다.

        빈 `body`에는 걸리지 않는다 — 위 표의 나머지 세 줄이 전부 정당한 상태다.
        `tokens`를 예산에 맞춰 `min()`으로 깎는 경로가 본문 있는 섹션에 닿으면 여기서
        죽는다. 그것이 의도다: 깎을 거라면 줄도 함께 버려야 한다.
        """
        if not self.body:
            return self
        expected = sum(estimate_tokens(line) for line in self.body)
        if self.tokens != expected:
            raise ValueError(
                f"{self.kind} 섹션의 tokens({self.tokens})가 본문에서 계산한 값"
                f"({expected})과 다르다. 본문을 줄였으면 tokens도 줄여야 한다"
            )
        if self.items != len(self.body):
            raise ValueError(
                f"{self.kind} 섹션의 items({self.items})가 본문 줄 수({len(self.body)})와 "
                "다르다. items는 실제로 담은 개수다"
            )
        return self


class CapsuleCuts(HarnessModel):
    """`search`(캡슐 최초 조립) 시점의 예산 절단 — **내용이 실제로 갈린 자리**만 센다 (#663).

    캡슐만 봐서는 `items=k`가 잘린 값인지, 섹션이 왜 없는지 알 수 없었다. 채움률
    (`used_tokens` 대 `budget_tokens`)도 못 쓴다 — 섹션이 통째로 빠지면 `used`가 예산 아래로
    내려가 오히려 여유로워 보인다.

    **세지 않는 것(함정):** `_sections_for`의 `min(cost, remaining)`는 본문이 없는 자리표시자의
    토큰만 깎아 내용을 안 바꾼다. `retrieval_truncated`는 청크 개수 상한(`MAX_CHUNKS`) 절단이라
    다른 질문이다. 확장(`expand`)이 붙인 것도 반영하지 않는다 — 최초 조립만이다.

    **프롬프트에 실리지 않는다** — `build_facts`·`describe_sections`는 `sections`만 읽는다.
    """

    # 예산 break로 **만들지 못한** kind — break 지점의 kind와 `LEVEL_SECTIONS`에서 그 뒤 전부다.
    # ⚠ **상한이다.** 뒤 kind 중에는 break가 없었어도 안 실렸을 것(인덱스 없음·선별 0건)이 섞일 수
    # 있다 — break는 그 뒤를 시도하지 않으므로 하네스도 그것을 모른다.
    dropped_sections: list[NonEmptyStr] = Field(default_factory=list)
    # `graph_section`이 `stop=True`를 냈다 — 실을 항목은 있는데 한 줄도 예산에 안 들었다.
    graph_stopped: bool = False
    # 그래프 섹션에서 **못 담은 항목 수**(`len(names) - items`). 섹션이 일부만 담겼을 때와
    # `graph_stopped`일 때(전부) 모두 센다. 인덱스가 없는 것(`None`)은 세지 않는다.
    # ⚠ **하한이다** - 예산 break 뒤의 그래프 섹션은 시도하지 않아 그 항목은 안 세어진다
    # (`dropped_sections`가 상한인 것과 짝이다).
    graph_skipped: int = Field(default=0, ge=0)
    # 리랭커가 선별했는데 `related`에 안 든 조각 수(`len(selected) - items`). 선별 **전**에 잘린
    # 조각(`top_n`·`MAX_CHUNKS`)은 세지 않는다. 본문(코드)이 갈리는 자리는 이것과 `graph_skipped`
    # 둘이다(그래프 섹션도 `body` 줄을 떨군다 - 이슈 #663 본문은 이쪽만 셌다). 그래프 stop 뒤에
    # 통째로 빠진 섹션의 본문은 여기 안 세어지고 `dropped_sections`에만 남는다.
    related_skipped: int = Field(default=0, ge=0)

    @property
    def any_cut(self) -> bool:
        return bool(
            self.dropped_sections
            or self.graph_stopped
            or self.graph_skipped
            or self.related_skipped
        )


class ContextCapsule(HarnessModel):
    """대상 하나에 대응하는 최소 분석 문맥.

    ⚠ **후보 하나당이 아니다** — Scout 풀에서는 대상 하나의 캡슐을 멤버 셋이 **공유한다**
    (ADR 0010 §3). 후보 수로 곱하지 않는다.

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
    # artifact digest + symbol + 정책 버전으로 만든 캐시 키 (knowledge-base.md §6). 슬라이스 질의면
    # slice id와 슬라이서 `run_signature`도 든다(두 Retriever의 `cache_key_for()`).
    cache_key: Sha256Digest
    # Progressive Context Expansion 횟수 (knowledge-base.md §5).
    # **상한은 여기서 강제하지 않는다.** 문서가 "초기값 3, 실측 후 조정"으로 둔 정책값이고
    # engineering-guide.md §8.5는 임계값을 config에서 읽으라고 정한다. 상한 판정은
    # 오케스트레이터가 config를 보고 한다(초과 시 후보를 queued로).
    expansion_count: int = Field(default=0, ge=0)
    # `search` 시점 예산 절단 표지 (#663). **`None`은 「기록 안 했다」**(이 필드가 생기기 전에
    # 저장된 캡슐)이고 「안 잘렸다」는 `CapsuleCuts()`(빈 값)다 — 둘을 섞으면 안 쟀다가 안 잘렸다로
    # 읽힌다(CLAUDE.md §0). 두 Retriever의 `search()`가 항상 채운다.
    #
    # ⚠ **뒤처진 쪽은 이 캡슐을 못 읽는다** — `extra="forbid"`라 이 키(`null`이어도)를 모르는 옛
    # 코드의 `ContextCapsule`은 로드에서 죽는다. 반대(옛 데이터를 새 코드가 읽기)는 기본값
    # `None`으로 산다.
    cuts: CapsuleCuts | None = None

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
