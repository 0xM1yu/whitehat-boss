"""HybridRetriever — 광역 수집 → embedding → reranker 재선별 (knowledge-base.md §3).

`FakeRetriever`가 보조 섹션을 고정 64토큰으로 채우는 자리에, 실제로 검색해 온 코드
조각을 넣는다. 토큰 수도 items 수도 실측이므로 캡슐 예산 초과 경로와 cache hit ratio가
진짜 값이 된다.

**`callers`/`callees`는 1단계 인덱서 산출물에서 온다**(`adapters/index_graph.py`, #63).
`FakeRetriever`와 **같은 모듈**을 쓴다 — 두 구현이 같은 index.json에서 다른 섹션을 내면
`dev_fake`와 `dev_docker`의 결과를 비교할 수 없다. index.json이 없으면(`dev_hybrid`는
`tool_runner: fake`다) 그 섹션을 **만들지 않는다**: 조용히 빈 섹션을 내면 "그래프가
비었다"와 "인덱서가 안 돌았다"가 구별되지 않는다.

**검색·그래프 섹션의 본문이 모두 캡슐에 실린다.** `callers`/`callees`의 줄이
`CapsuleSection.body`에 담겨 프롬프트로 가고(`index_graph.graph_section()`), `related`도
같다 — `_fit_related`가 조각의 `file:line`+코드를 줄로 돌려주고 검색·확장 두 경로가 그것을
`body`에 넣는다(이슈 #63). 예전에는 `(토큰, 개수)`만 세고 코드를 버려,
embedding·reranker가 고른 코드가 프롬프트에 한 줄도 실리지 않았다. 확장 경로의 부분
절단(`min(pool[used].tokens, remaining)`)도 없앴다 — 조각은 통째로 들어가거나 안 들어간다.

**`related`는 항목당 크기가 가장 큰 섹션이다.** 그래프 줄은 `memcpy parser.c:24` 한 줄이지만
`related` 조각은 수십 줄짜리 코드 블록이다. 그리고 이 본문은 산문(`scout.describe_sections`)과
사실 블록(`build_facts`) 양쪽에 실리므로(#67의 2배 계상 — `callers`/`callees`와 같은 계약),
**선언 N토큰짜리 `related`가 실제 프롬프트에서는 대략 2N을 차지한다.** 토큰 추정 과소(#96)까지
얹으면 더 커진다. 그래서 캡슐 구성이 바뀐 그 변경은 `CAPSULE_POLICY_VERSION`을 올렸다(아래).

**이 문단이 예고한 조건이 2026-09-04에 성립했다.** 오래 "지금은 hybrid가 dev 전용
(`dev_hybrid`·`dev_hybrid_docker`)이라 무해하지만 실서빙 hybrid 프로필이 생기면 예산을
캡슐 선언값이 아니라 이 사실로 잡아야 한다"고 적어 왔는데, `local_balanced_hybrid`가 그
프로필이었다(2026-09-24에 지웠고 지금 그 자리는 `harness_pool_hybrid`다 — `config/profiles.yaml`).
**그 프로필은 `budget: default`를 그대로 쓴다** — 대조군 `harness_pool`과 축을 하나로 유지하려는
선택이지 그 예산이 이 2배 계상을 감안했다는 판정이 아니다. 여기서 예산을 재잡으려면 실제로
실린 토큰을 먼저 재야 하고, 그 측정은 아직 없다. 근본 해법은 위 문단이 이미 가리킨 #67이다 (⏵
2026-10-12 — #67은 닫혔고 v13·v14는
Auditor FACTS의 `source` 사본만 걷었다. `related`의 2N은 hybrid에서 그대로다 · ⏵ 2026-10-13 —
서빙 판 v15·v16·v17·v18도 같은 선언(`facts_source: false`)이라 걷는 것이 그 사본 하나뿐인 것도
같다).

**`types`는 만들지 않는다.** knowledge-base.md §4의 구성에는 있지만 index-v1에는 타입
데이터가 없었고, index-v2의 `types` 표는 캡슐로 싣지 않는다 — NE resolver 몫이다
(`index_graph.GRAPH_SECTION_KINDS` 주석). 없는 그래프에서 나온 척 섹션을 채우면 토큰 회계가
거짓말을 한다.

`CapsuleSection.kind`가 `NonEmptyStr`로 열려 있는 것이 이 확장을 허용한다(스키마 주석).
→ 이슈 #63

**결정론**: 파일 순회는 정렬하고, 동점은 입력 인덱스 오름차순으로 깬다. 같은 입력에
같은 캡슐이 나오지 않으면 engineering-guide.md §5의 재현성이 깨진다.
"""

import math
import posixpath
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from src.adapters.index_graph import (
    GRAPH_SECTION_KINDS,
    GraphIndex,
    SliceBody,
    graph_section,
    source_lines,
)
from src.ids import IdFactory, sha256_text
from src.interfaces.retrieval import (
    EmbeddingClient,
    RerankerClient,
    RetrievalResult,
)
from src.interfaces.retriever import Query
from src.paths import safe_join
from src.schemas.capsule import CapsuleCuts, CapsuleSection, ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel
from src.schemas.finding import MissingEvidence
from src.tokens import estimate_tokens

# 캡슐 구성 규칙이 다르면 캐시 키도 달라야 한다(knowledge-base.md §6). Fake와 같은 값을
# 쓰면 두 구현이 만든 캡슐이 같은 키를 공유해 조건 비교가 오염된다.
#
# **hybrid-v1 → hybrid-v2 (2026-08-20)**: `callers`/`callees`가 구성에 들어왔다.
# **hybrid-v2 → hybrid-v3 (W2/#95)**: LIGHT에 그래프가 들어갔고 target이 심볼로 좁혀졌다.
# **hybrid-v3 → hybrid-v4 (2026-08-23)**: `related`에 `body`가 생기고 조각당 비용이
#   `chunk.tokens`에서 `estimate_tokens(file:line\n+text)`로 커졌다 — 같은 키가 다른
#   구성을 덮으면 안 되므로 올린다(#25가 v3, 이 변경이 v4 — 서로 다른 구성이다).
# **hybrid-v4 → hybrid-v5 (2026-09-25, ADR 0012 결정 1 · #220 ②)**: 슬라이스 질의
#   (`Query.slice_id`)면 `target_function`이 슬라이스 렌더를 세고 키에 slice id·슬라이서
#   `run_signature`가 붙는다 — Fake의 v3→v4와 같은 변경이다
#   (`fake_retriever.CAPSULE_POLICY_VERSION` 주석).
# **hybrid-v5 → hybrid-v6 (2026-09-25, #220 ①)**: 슬라이스 질의의 그래프 섹션이 슬라이스의 줄
#   집합을 본문으로 센다 — Fake의 v4→v5와 같은 변경이다.
# **hybrid-v6 → hybrid-v7 (2026-09-30, #336)**: 후보 풀을 대상 근처부터 채운다(같은 파일 →
#   그래프 이웃 → 같은 폴더 → 나머지, C/C++ 대상은 C/C++ 조각만) — 같은 키에서 `related`에
#   드는 조각이 바뀐다.
# **hybrid-v7 → hybrid-v8 (2026-10-15)**: 코퍼스·계열 판정의 확장자 집합이 넓어졌다(`.c++` `.cp`
#   `.tcc` 추가 · 대문자 철자 `.CPP`·`.C`는 소문자로 내려 비교) — 같은 키에서 `related`에 드는
#   조각이 바뀐다.
CAPSULE_POLICY_VERSION = "hybrid-v8"

# 본문이 없는 보조 섹션의 고정 비용. `static_signal`·`known_facts`에만 남아 있다 —
# 그래프 파생 섹션은 `index_graph`가 실측으로 채운다(fake_retriever와 같은 상태).
_AUX_SECTION_TOKENS = 64

# 광역 수집 범위. 저장소 전체를 임베딩하면 스카우트 한 건에 수천 건을 호출하게 된다.
# 상한에 걸려 잘린 경우는 `on_truncate` 콜백으로 드러낸다 — 조용히 자르면 "전부 봤다"로
# 읽힌다.
#
# **어느 400인가(#336).** 예전에는 경로 정렬 순서의 앞 400조각이었다. 큰 저장소에서는
# 대상 파일·폴더 조각이 하나도 안 들었다(2026-09-29 vast 실측: qemu 59,881조각 중 앞 44개 파일,
# suricata 14,164조각 중 앞 47개 파일). 지금은 `_collect`가 대상 근처부터 채운다 —
# 같은 파일 → 호출 그래프 이웃 파일 → 같은 폴더 → 나머지. 상한 값은 그대로다.
MAX_CHUNKS = 400
CHUNK_LINES = 40

# embedding이 좁히는 폭 → reranker가 최종 선별하는 폭.
DEFAULT_TOP_K = 32
DEFAULT_TOP_N = 5

SOURCE_SUFFIXES = frozenset(
    {".py", ".c", ".cc", ".cpp", ".cxx", ".c++", ".cp", ".h", ".hh", ".hpp", ".hxx", ".tcc",
     ".java", ".js", ".ts", ".go", ".rs", ".php", ".rb"}
)
SKIP_DIRS = frozenset({".git", "__pycache__", "artifacts", "node_modules", ".venv", "venv"})

# 대상이 이 계열이면 후보도 이 계열 조각만 쓴다(#336). C 대상에 `.py`·`.rs` 조각이 섞이던
# 자리다. 다른 언어 대상은 예전처럼 `SOURCE_SUFFIXES` 전체를 본다.
# ⏵ 2026-10-15 — `preprocess`가 SOURCE로 읽는 C++ 철자(`.c++`·`.cp`·`.tcc`)를 두 집합 모두에
# 더했다. 빠지면 그 파일은 코퍼스에 없거나 대상 계열 판정에서 빠진다.
# 두 집합은 소문자 철자만 든다 — 비교하는 쪽이 `suffix.lower()`로 내린다(`preprocess.classify`·
# `fake_tools.sources`·`container_tools._wanted`와 같은 축). 원문으로 비교하면 `.CPP`·`.C` 대상이
# 계열 판정에서 빠져 C 대상에 `.py`·`.rs` 조각이 다시 섞이고(#336), 그 파일은 코퍼스에도 안 든다.
C_FAMILY_SUFFIXES = frozenset(
    {".c", ".cc", ".cpp", ".cxx", ".c++", ".cp", ".h", ".hh", ".hpp", ".hxx", ".tcc"}
)

# 레벨별 섹션 구성. LIGHT는 검색하지 않는다 — Scout는 후보 전량에 대해 도는 단계라
# 여기서 임베딩을 부르면 model-serving.md §2의 "Scout는 크게 배칭" 전제가 무너진다.
#
# **그래프 섹션의 자리는 `FakeRetriever.LEVEL_SECTIONS`와 같아야 한다**(LIGHT·EXPANDED에
# callers·callees, FOCUSED에 callers). 갈리면 같은 index.json에서 두 구현이 다른 캡슐을
# 내고 `dev_fake`↔`dev_docker` 비교가 성립하지 않는다. `related`는 이쪽에만 있는데,
# 그건 검색으로 확보한 것이라 애초에 Fake에 대응물이 없다.
#
# **LIGHT의 그래프 두 칸은 위 "검색하지 않는다"의 예외가 아니다** — 그래프 섹션은
# 임베딩도 reranker도 부르지 않고 이미 로드된 index.json에서 읽는다. 근거는
# `fake_retriever.LEVEL_SECTIONS` 주석에 한 번만 적었다(#95을 (a)로 확정).
LEVEL_SECTIONS: dict[CapsuleLevel, tuple[str, ...]] = {
    CapsuleLevel.LIGHT: ("target_function", "callers", "callees", "static_signal"),
    CapsuleLevel.EXPANDED: ("target_function", "related", "callers", "callees", "static_signal"),
    CapsuleLevel.FOCUSED: ("target_function", "related", "callers", "known_facts"),
}

EXPANSION_ORDER = ("related", "callers", "callees", "known_facts", "static_signal")

CacheHook = Callable[[str, bool], None]
TruncateHook = Callable[[int, int], None]

# 검색 호출의 비용을 올리는 훅: (component, result, 입력 문서 수).
# 오케스트레이터를 import 하지 않으려고 콜백으로 둔다(engineering-guide.md §3).
UsageHook = Callable[[str, RetrievalResult, int], None]

# `retrieval_call` 이벤트의 `component` 값. trace에서 두 부품의 비용을 나눠 세려면
# 문자열이 한 곳에서 나와야 한다.
RETRIEVAL_EMBEDDING = "embedding"
RETRIEVAL_RERANKER = "reranker"


class Chunk:
    """수집된 코드 조각 하나. 스키마가 아니라 어댑터 내부 자료구조다."""

    # `tokens`는 뒀다 지웠다 — `_fit_related`가 조각당 비용을 `estimate_tokens(_chunk_body())`
    # (file:line + 코드)로 재고부터, 어디서도 `chunk.tokens`(코드만)를 읽지 않는다.
    __slots__ = ("file", "line_start", "text")

    def __init__(self, file: str, line_start: int, text: str) -> None:
        self.file = file
        self.line_start = line_start
        self.text = text


class HybridRetriever:
    """`Retriever` 구현. embedding·reranker를 실제로 호출한다."""

    def __init__(
        self,
        *,
        root: Path,
        ids: IdFactory,
        run_id: str,
        embedder: EmbeddingClient,
        reranker: RerankerClient,
        on_cache: CacheHook | None = None,
        on_truncate: TruncateHook | None = None,
        on_usage: UsageHook | None = None,
        graph: GraphIndex | None = None,
        top_k: int = DEFAULT_TOP_K,
        top_n: int = DEFAULT_TOP_N,
        max_chunks: int = MAX_CHUNKS,
        read_slice: Callable[[str], str] | None = None,
        slice_signature: str | None = None,
        slice_lines: Callable[[str], SliceBody] | None = None,
    ) -> None:
        self.root = Path(root)
        self.ids = ids
        self.run_id = run_id
        self.embedder = embedder
        self.reranker = reranker
        # 주입되지 않으면 경로 없는 인덱스다 — `status=missing`이라 그래프 섹션이
        # 부재한다. 빈 그래프를 지어내지 않는다(`FakeRetriever`와 같은 처리).
        self.graph = graph if graph is not None else GraphIndex(None)
        # 슬라이스 타깃의 렌더 조회와 그 슬라이서 run의 신원 — `FakeRetriever`와 같은 계약이다.
        self.read_slice = read_slice
        self.slice_signature = slice_signature
        # 슬라이스 id → 그 슬라이스의 (file, line) 집합. 슬라이스 질의의 그래프 섹션 본문이다
        # (`index_graph.names_for`, #220 ①) — `read_slice`와 같이 `build_deps`가 넣는다.
        self.slice_lines = slice_lines
        self.on_cache = on_cache
        self.on_truncate = on_truncate
        self.on_usage = on_usage
        self.top_k = top_k
        self.top_n = top_n
        self.max_chunks = max_chunks
        self._cache: dict[str, ContextCapsule] = {}
        # expand()가 재검색 없이 다음 후보를 꺼내 쓰도록 캡슐별 선별 결과를 남긴다.
        # expand의 인자는 (capsule, need)뿐이라 Query를 다시 만들 수 없다.
        self._pool: dict[str, list[Chunk]] = {}
        self._corpus: list[Chunk] | None = None

    # --- Retriever Protocol ---------------------------------------------

    def search(self, query: Query, level: CapsuleLevel) -> ContextCapsule:
        cache_key = self.cache_key_for(query, level)
        cached = self._cache.get(cache_key)
        if self.on_cache is not None:
            self.on_cache(cache_key, cached is not None)
        if cached is not None:
            return cached

        source = self._target_source(query)
        kinds = LEVEL_SECTIONS[level]
        selected: list[Chunk] = []
        if "related" in kinds:
            selected = self._retrieve(query)
        self._pool[cache_key] = selected

        sections, cuts = _sections_for(
            kinds,
            source,
            selected,
            query.budget_tokens,
            self.graph,
            query.target,
            self._slice_body(query),
        )
        capsule = ContextCapsule(
            capsule_id=self.ids.next("capsule"),
            run_id=self.run_id,
            target=query.target,
            level=level,
            budget_tokens=query.budget_tokens,
            sections=sections,
            cuts=cuts,
            cache_key=cache_key,
        )
        self._cache[cache_key] = capsule
        return capsule

    def expand(self, capsule: ContextCapsule, need: MissingEvidence) -> ContextCapsule:
        """선별 풀에서 한 건 더 붙이거나, 없으면 다음 보조 섹션을 붙인다.

        `FakeRetriever.expand`와 같은 계약이다 — 예산을 넘기지 않는 선에서만 붙이고,
        붙일 것이 없으면 **그대로 돌려준다**. "상한 초과"와 "더 붙일 것이 없음"은 다른
        사건이라 섞지 않는다(상한 판정은 호출자가 config를 보고 한다).
        """
        remaining = capsule.budget_tokens - capsule.used_tokens
        if remaining <= 0:
            return capsule

        sections = list(capsule.sections)
        pool = self._pool.get(capsule.cache_key, [])
        related = next((i for i, s in enumerate(sections) if s.kind == "related"), None)

        if related is not None and (used := sections[related].items or 0) < len(pool):
            body = _chunk_body(pool[used])
            cost = estimate_tokens(body)
            # 조각은 통째로 들어가거나 안 들어간다 — 부분 절단(`min`)은 본문 없이 토큰만
            # 더해 조각을 반쪽만 세던 것이라, 본문이 실리는 지금은 검증기에서 죽는다.
            #
            # **안 들어가면 `related`는 그대로 두고 아래로 흘려보낸다.** `return`하면
            # `EXPANSION_ORDER`의 나머지(callers·callees·known_facts·static_signal)에
            # 못 닿는다 — 조각 하나가 예산에 안 맞는다고 64토큰짜리 보조 섹션까지 포기할
            # 이유가 없다(그래프 분기와 같은 결). 게다가 채움이 멈춘 이유가 "다음 조각이
            # 안 들어가서"이고 그 뒤 다른 섹션이 예산을 더 먹으므로 remaining은 늘 그 조각보다
            # 작다 — `return`을 두면 `related`에 남은 조각이 있는 한 expand가 영구 no-op이 된다.
            if cost > 0 and cost <= remaining:
                sections[related] = CapsuleSection(
                    kind="related",
                    tokens=sections[related].tokens + cost,
                    items=used + 1,
                    body=[*sections[related].body, body],
                )
                return self._transition(capsule, sections, need)

        present = {section.kind for section in sections}
        for kind in EXPANSION_ORDER:
            if kind in present:
                continue
            if kind == "related" and not pool:
                continue
            if kind in GRAPH_SECTION_KINDS:
                # 그래프 분기는 `FakeRetriever.expand()`와 **같은 함수**를 부른다.
                # ponytail: 캡슐이 slice id를 안 들어 슬라이스 캡슐도 entry 한 줄로 센다(#220 ①의
                # 줄 집합이 아니다). 호출 지점이 0이라 둔다 — 배선할 때 캡슐에 slice id를 싣는다.
                section, stop, _ = graph_section(kind, self.graph, capsule.target, remaining)
                if section is None:
                    if stop:
                        break
                    # 그래프가 없다. 없는 섹션을 지어내지 않고 다음 후보로 넘어간다.
                    continue
            else:
                # `items`를 1로 박지 않는다. `search()`가 보조 섹션에 `items=None`을
                # 주므로 여기서 1을 넣으면 같은 섹션이 경로에 따라 다른 값을 갖는다.
                tokens = min(_AUX_SECTION_TOKENS, remaining)
                if tokens <= 0:
                    break
                section = CapsuleSection(kind=kind, tokens=tokens)
            sections.append(section)
            return self._transition(capsule, sections, need)
        return capsule

    def cache_key_for(self, query: Query, level: CapsuleLevel) -> str:
        """artifact digest + symbol + level + 정책 버전 (knowledge-base.md §6).

        keywords는 키에 넣지 않는다. 같은 심볼·레벨이면 같은 캡슐이라는 것이 캐시의
        전제이고, 호출자가 힌트를 조금 바꿀 때마다 캐시가 통째로 비면 hit ratio가
        측정 대상이 아니라 잡음이 된다.
        """
        target = query.target
        digest = sha256_text(_read_source(self.root, target.file))
        # `line_start`를 함께 넣는 이유는 `FakeRetriever.cache_key_for()`에 적었다 —
        # 동명 `static` 함수 둘이 한 키를 공유하는 것을 막는다(W2). 슬라이스 신원이 붙는
        # 이유도 같은 자리에 적었다(#220 ②).
        symbol = f"{target.symbol or ''}@{target.line_start or ''}"
        key = f"{digest}|{symbol}|{level.value}|{CAPSULE_POLICY_VERSION}"
        if query.slice_id is not None:
            key = f"{key}|{query.slice_id}@{self.slice_signature}"
        return sha256_text(key)

    # --- 내부 ------------------------------------------------------------

    def _slice_body(self, query: Query) -> SliceBody | None:
        """그래프 섹션의 본문. 슬라이스 질의면 그 슬라이스의 줄 집합이다(#220 ① — `names_for`)."""
        if query.slice_id is None:
            return None
        if self.slice_lines is None:
            raise ValueError(f"슬라이스 {query.slice_id} 질의인데 slice_lines가 배선되지 않았다")
        return self.slice_lines(query.slice_id)

    def _target_source(self, query: Query) -> str:
        """`target_function`이 셀 본문 — `FakeRetriever._target_source`와 같은 계약이다."""
        if query.slice_id is None:
            return read_symbol_source(self.root, query.target)
        if self.read_slice is None:
            raise ValueError(f"슬라이스 {query.slice_id} 질의인데 read_slice가 배선되지 않았다")
        return self.read_slice(query.slice_id)

    def _transition(
        self, capsule: ContextCapsule, sections: list[CapsuleSection], need: MissingEvidence
    ) -> ContextCapsule:
        """`model_copy`가 아니라 `transition()`을 쓴다 — 전자는 예산 검증기를 우회한다."""
        return capsule.transition(
            sections=[section.model_dump() for section in sections],
            expansion_count=capsule.expansion_count + 1,
            evidence_refs=[*capsule.evidence_refs, need.missing_id],
        )

    def _retrieve(self, query: Query) -> list[Chunk]:
        """광역 수집 → embedding 상위 K → reranker 상위 N."""
        candidates = self._collect(query)
        if not candidates:
            return []

        text = _query_text(query)
        embedded = self.embedder.embed([text, *(chunk.text for chunk in candidates)])
        self._report(RETRIEVAL_EMBEDDING, embedded, len(candidates))
        vectors = embedded.vectors
        if len(vectors) != len(candidates) + 1:
            # 길이가 어긋나면 유사도가 엉뚱한 조각에 붙는다. 조용히 자르지 않는다.
            raise ValueError(
                f"embedding 응답 수가 입력과 다르다: {len(vectors)} != {len(candidates) + 1}"
            )
        query_vec, chunk_vecs = vectors[0], vectors[1:]

        scored = sorted(
            enumerate(chunk_vecs),
            key=lambda pair: (-_cosine(query_vec, pair[1]), pair[0]),
        )
        narrowed = [candidates[i] for i, _ in scored[: self.top_k]]

        reranked = self.reranker.rerank(
            query=text,
            documents=[chunk.text for chunk in narrowed],
            top_n=self.top_n,
        )
        self._report(RETRIEVAL_RERANKER, reranked, len(narrowed))
        ordered = sorted(reranked.ranked, key=lambda r: (-r.score, r.index))
        return [narrowed[r.index] for r in ordered if 0 <= r.index < len(narrowed)]

    def _report(self, component: str, result: RetrievalResult, candidates: int) -> None:
        """검색 호출 비용을 훅으로 올린다.

        훅이 없으면 조용히 넘어간다 — 어댑터 단위 테스트는 오케스트레이터 없이 돌고,
        그때 비용을 받을 곳이 없는 것은 정상이다. 파이프라인에서 훅이 안 붙는 사고는
        `LateUsageHook`이 바인딩 전 호출을 터뜨려서 잡는다.
        """
        if self.on_usage is not None:
            self.on_usage(component, result, candidates)

    def _collect(self, query: Query) -> list[Chunk]:
        """키워드로 1차 필터. 힌트가 없으면 전체(상한까지)가 후보다.

        **상한 안에 무엇이 드는지는 순서가 정한다(#336).** 대상 근처부터 채운다 —
        같은 파일(0) → 호출 그래프 이웃 파일(1) → 같은 폴더(2) → 나머지(3). 같은 파일 안은
        대상 줄에서 가까운 조각부터, 다른 층 안은 코퍼스 순서(경로 정렬)다(안정 정렬이라 결정론).
        같은 파일이 상한의 절반을 넘으면 절반만 먼저 두고 나머지 층 뒤로 보낸다 — 대상 파일
        하나가 풀을 다 먹어 이웃·폴더 조각이 0이 되지 않게. 그래프가 없으면 1층이 빈다.
        대상이 C/C++면 C/C++ 조각만 본다.

        `on_truncate`의 수는 언어 필터 **뒤** 후보 수다 — 필터로 버린 조각은 안 센다.
        """
        corpus = self._load_corpus()
        target = query.target
        keywords = [k.lower() for k in query.keywords]
        is_c_family = Path(target.file).suffix.lower() in C_FAMILY_SUFFIXES
        family = C_FAMILY_SUFFIXES if is_c_family else None
        picked = [
            chunk
            for chunk in corpus
            # 대상 자신은 target_function 섹션이 이미 담고 있다. 두 번 세면 토큰이 부풀고
            # reranker 상위가 자기 자신으로 채워진다.
            if not _overlaps_body(chunk, target)
            and (family is None or Path(chunk.file).suffix.lower() in family)
            and (not keywords or any(k in chunk.text.lower() for k in keywords))
        ]
        near = set(self.graph.neighbor_files(target) or ())
        folder = posixpath.dirname(target.file)

        def tier(chunk: Chunk) -> int:
            if chunk.file == target.file:
                return 0
            if chunk.file in near:
                return 1
            if posixpath.dirname(chunk.file) == folder:
                return 2
            return 3

        line = target.line_start

        def order(chunk: Chunk) -> tuple[int, int]:
            rank = tier(chunk)
            distance = abs(chunk.line_start - line) if rank == 0 and line is not None else 0
            return rank, distance

        picked.sort(key=order)
        half = self.max_chunks // 2
        same = [chunk for chunk in picked if chunk.file == target.file]
        if len(same) > half:
            picked = same[:half] + [c for c in picked if c.file != target.file] + same[half:]
        if len(picked) > self.max_chunks and self.on_truncate is not None:
            self.on_truncate(len(picked), self.max_chunks)
        return picked[: self.max_chunks]

    def _load_corpus(self) -> list[Chunk]:
        if self._corpus is None:
            self._corpus = list(_chunk_tree(self.root))
        return self._corpus


def _query_text(query: Query) -> str:
    """검색 질의 문자열. 심볼과 키워드가 모두 비면 파일 경로가 유일한 신호다."""
    parts = [query.target.symbol or "", *query.keywords]
    joined = " ".join(part for part in parts if part).strip()
    return joined or query.target.file


def _overlaps_body(chunk: Chunk, target: Location) -> bool:
    """이 조각이 캡슐 본문과 겹치는가. 겹치면 검색 후보에서 뺀다.

    **`index_graph.names_for()`가 고친 것과 같은 전제 위에 있던 자리다(W2).** 예전
    조건은 `chunk.file != target.file`이었고, 그것은 target이 파일 전체라 파일 안의
    모든 조각이 이미 본문이라는 전제였다. 심볼 단위 target에서는 같은 파일의 다른
    함수가 본문 밖이므로, 그 조건을 그대로 두면 **가장 가까운 문맥을 검색에서 지운다.**

    `line_start`가 없으면 파일 전체가 본문이라 예전 동작이 그대로 남는다.
    """
    if chunk.file != target.file:
        return False
    if target.line_start is None:
        return True
    chunk_end = chunk.line_start + CHUNK_LINES - 1
    body_end = target.line_end or target.line_start
    return chunk.line_start <= body_end and target.line_start <= chunk_end


def _chunk_tree(root: Path) -> Iterable[Chunk]:
    """소스 파일을 고정 줄 수로 자른다. 순회는 정렬해 결정론을 지킨다."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        lines = source_lines(path.read_text("utf-8", errors="replace"))
        relative = path.relative_to(root).as_posix()
        for start in range(0, len(lines), CHUNK_LINES):
            text = "\n".join(lines[start : start + CHUNK_LINES]).strip()
            if text:
                yield Chunk(relative, start + 1, text)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def read_symbol_source(root: Path, target: Location) -> str:
    """대상 심볼의 원문. `fake_retriever`와 같은 계약이다 — 캡슐의 토큰 수와 실제
    프롬프트가 어긋나지 않도록 프롬프트를 만드는 쪽도 같은 함수를 쓴다."""
    text = _read_source(root, target.file)
    if target.line_start is None:
        return text
    lines = source_lines(text)
    end = target.line_end or target.line_start
    return "\n".join(lines[max(target.line_start - 1, 0) : end])


def _read_source(root: Path, relative: str) -> str:
    path = safe_join(root, relative)
    if not path.is_file():
        return ""
    return path.read_text("utf-8", errors="replace")


def _sections_for(
    kinds: Sequence[str],
    source: str,
    selected: list[Chunk],
    budget: int,
    graph: GraphIndex,
    target: Location,
    slice_body: SliceBody | None = None,
) -> tuple[list[CapsuleSection], CapsuleCuts]:
    """예산을 넘기지 않는 선에서 섹션을 채운다. `(섹션, 절단 표지)`.

    **표지는 내용이 갈린 자리에서만 남긴다**(#663) — 두 `break`(그래프 `stop`·보조 섹션 예산 0),
    그래프 섹션의 못 담은 항목, `_fit_related`가 못 담은 조각. `min(cost, remaining)`는 내용을 안
    바꿔 세지 않는다. 그래프 분기는 `FakeRetriever._sections_for()`와 같은 표지를 남긴다.

    `related`의 토큰은 실제로 선별된 조각의 길이 합이다 — 고정값을 쓰면 캡슐 예산
    초과 경로가 영원히 안 타고, 그러면 그 경로가 검증되지 않는다.

    **그래프 분기는 `FakeRetriever._sections_for()`와 같아야 한다.** 두 구현이 같은
    index.json에서 다른 섹션을 내면 `dev_fake`↔`dev_docker` 비교가 성립하지 않는다.
    """
    sections: list[CapsuleSection] = []
    used = 0
    dropped: list[str] = []
    graph_stopped = False
    graph_skipped = 0
    related_skipped = 0
    for index, kind in enumerate(kinds):
        remaining = budget - used
        items: int | None = None
        if kind in GRAPH_SECTION_KINDS:
            # 그래프 분기는 `FakeRetriever._sections_for()`와 **같은 함수**를 부른다.
            # 복사본 둘이 갈리면 같은 index.json에서 두 구현이 다른 캡슐을 낸다.
            section, stop, skipped = graph_section(kind, graph, target, remaining, slice_body)
            graph_skipped += skipped
            if section is None:
                if stop:
                    graph_stopped = True
                    dropped = list(kinds[index:])
                    break
                # 인덱서가 안 돌았거나 형식이 어긋났다 — 섹션을 만들지 않는다.
                # 0건 섹션으로 내면 "그래프가 비었다"와 구별되지 않는다.
                continue
            sections.append(section)
            used += section.tokens
            continue
        if kind == "target_function":
            cost = estimate_tokens(source)
        elif kind == "related":
            if not selected:
                continue
            cost, count, body = _fit_related(selected, remaining)
            related_skipped += len(selected) - count
            if count == 0:
                continue
            # 그래프 섹션과 같은 이유로 **여기서 섹션을 완성해 `continue`한다** —
            # `_fit_related`가 이미 `cost <= remaining`을 보장하므로, 아래 공용
            # `min()` 줄에 본문 있는 섹션이 닿으면 토큰만 깎여 검증기에서 죽는다.
            sections.append(
                CapsuleSection(kind=kind, tokens=cost, items=count, body=body)
            )
            used += cost
            continue
        else:
            cost = _AUX_SECTION_TOKENS
        # **`cost == 0`은 "예산이 없다"가 아니라 "그래프를 봤는데 간선이 0건이다"이다.**
        # 두 사건을 한 조건으로 묶으면(예전의 `if tokens <= 0: break`) 빈 그래프가 예산
        # 소진으로 기록되고, 그러면 캡슐만 보고는 인덱서가 안 돈 것과 구별되지 않는다.
        #
        # 그래프 섹션이 위에서 먼저 빠지므로 **아래 `min()`은 본문 있는 섹션에 닿지
        # 않는다.** 닿으면 줄은 두고 토큰만 깎는 것이 되어 검증기에서 죽는다.
        if cost > 0 and remaining <= 0:
            dropped = list(kinds[index:])
            break
        tokens = min(cost, remaining)
        sections.append(CapsuleSection(kind=kind, tokens=tokens, items=items))
        used += tokens
    cuts = CapsuleCuts(
        dropped_sections=dropped,
        graph_stopped=graph_stopped,
        graph_skipped=graph_skipped,
        related_skipped=related_skipped,
    )
    return sections, cuts


def _chunk_body(chunk: Chunk) -> str:
    """`related` 섹션의 본문 한 줄. 코드 앞에 `file:line`을 붙여 위치를 함께 준다."""
    return f"{chunk.file}:{chunk.line_start}\n{chunk.text}"


def _fit_related(selected: list[Chunk], remaining: int) -> tuple[int, int, list[str]]:
    """예산에 들어가는 만큼만 담는다. (토큰 합, 담은 개수, **담은 줄**).

    `index_graph.fit_names`와 같은 계약이다 — 담은 줄을 버리지 않고 함께 돌려주고,
    호출자가 `CapsuleSection.body`에 넣는다. 예전에는 `(토큰합, 개수)`만 내고 조각의
    코드를 버려, embedding·reranker가 고른 코드가 프롬프트에 한 줄도 실리지 않았다
    (이슈 #63). 반환 토큰 합은 `remaining` 이하이므로 호출자가 다시 `min()`을
    걸면 안 된다 — 줄은 두고 토큰만 깎는 것이 되어 스키마 검증기에서 죽는다.
    """
    total = 0
    count = 0
    fitted: list[str] = []
    for chunk in selected:
        body = _chunk_body(chunk)
        cost = estimate_tokens(body)
        if total + cost > remaining:
            break
        total += cost
        count += 1
        fitted.append(body)
    return total, count, fitted
