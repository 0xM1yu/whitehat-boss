"""HybridRetriever — 광역 수집 → embedding → reranker 재선별 (knowledge-base.md §3).

`FakeRetriever`가 보조 섹션을 고정 64토큰으로 채우는 자리에, 실제로 검색해 온 코드
조각을 넣는다. 토큰 수도 items 수도 실측이므로 캡슐 예산 초과 경로와 cache hit ratio가
진짜 값이 된다.

**`callers`/`callees`/`types` 섹션을 만들지 않는다.** knowledge-base.md §4의 레벨별
구성에는 있지만, 그 세 가지는 call graph에서 나와야 하고 1단계 Index는 지금
`FakeToolRunner`를 거친다 — 실제 심볼 테이블이 없다. 없는 그래프에서 나온 척 섹션을
채우면 Evidence Gate E2와 토큰 회계가 동시에 거짓말을 한다. 그래서 검색으로 실제
확보한 것만 `related`로 내보내고, 그래프 파생 섹션은 실 인덱서가 생길 때까지 비운다.
`CapsuleSection.kind`가 `NonEmptyStr`로 열려 있는 것이 이 확장을 허용한다(스키마 주석).
→ open-questions.md D13

**결정론**: 파일 순회는 정렬하고, 동점은 입력 인덱스 오름차순으로 깬다. 같은 입력에
같은 캡슐이 나오지 않으면 engineering-guide.md §5의 재현성이 깨진다.
"""

import math
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from src.ids import IdFactory, sha256_text
from src.interfaces.retrieval import EmbeddingClient, RerankerClient
from src.interfaces.retriever import Query
from src.paths import safe_join
from src.schemas.capsule import CapsuleSection, ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel
from src.schemas.finding import MissingEvidence

# 캡슐 구성 규칙이 다르면 캐시 키도 달라야 한다(knowledge-base.md §6). Fake와 같은 값을
# 쓰면 두 구현이 만든 캡슐이 같은 키를 공유해 조건 비교가 오염된다.
CAPSULE_POLICY_VERSION = "hybrid-v1"

_CHARS_PER_TOKEN = 4
_AUX_SECTION_TOKENS = 64

# 광역 수집 범위. 저장소 전체를 임베딩하면 스카우트 한 건에 수천 건을 호출하게 된다.
# 상한에 걸려 잘린 경우는 `on_truncate` 콜백으로 드러낸다 — 조용히 자르면 "전부 봤다"로
# 읽힌다.
MAX_CHUNKS = 400
CHUNK_LINES = 40

# embedding이 좁히는 폭 → reranker가 최종 선별하는 폭.
DEFAULT_TOP_K = 32
DEFAULT_TOP_N = 5

SOURCE_SUFFIXES = frozenset(
    {".py", ".c", ".cc", ".cpp", ".h", ".hpp", ".java", ".js", ".ts", ".go", ".rs", ".php", ".rb"}
)
SKIP_DIRS = frozenset({".git", "__pycache__", "artifacts", "node_modules", ".venv", "venv"})

# 레벨별 섹션 구성. LIGHT는 검색하지 않는다 — Scout는 후보 전량에 대해 도는 단계라
# 여기서 임베딩을 부르면 model-serving.md §2의 "Scout는 크게 배칭" 전제가 무너진다.
LEVEL_SECTIONS: dict[CapsuleLevel, tuple[str, ...]] = {
    CapsuleLevel.LIGHT: ("target_function", "static_signal"),
    CapsuleLevel.EXPANDED: ("target_function", "related", "static_signal"),
    CapsuleLevel.FOCUSED: ("target_function", "related", "known_facts"),
}

EXPANSION_ORDER = ("related", "known_facts", "static_signal")

CacheHook = Callable[[str, bool], None]
TruncateHook = Callable[[int, int], None]


class Chunk:
    """수집된 코드 조각 하나. 스키마가 아니라 어댑터 내부 자료구조다."""

    __slots__ = ("file", "line_start", "text", "tokens")

    def __init__(self, file: str, line_start: int, text: str) -> None:
        self.file = file
        self.line_start = line_start
        self.text = text
        self.tokens = max(1, len(text) // _CHARS_PER_TOKEN)


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
        top_k: int = DEFAULT_TOP_K,
        top_n: int = DEFAULT_TOP_N,
        max_chunks: int = MAX_CHUNKS,
    ) -> None:
        self.root = Path(root)
        self.ids = ids
        self.run_id = run_id
        self.embedder = embedder
        self.reranker = reranker
        self.on_cache = on_cache
        self.on_truncate = on_truncate
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

        source = read_symbol_source(self.root, query.target)
        kinds = LEVEL_SECTIONS[level]
        selected: list[Chunk] = []
        if "related" in kinds:
            selected = self._retrieve(query)
        self._pool[cache_key] = selected

        capsule = ContextCapsule(
            capsule_id=self.ids.next("capsule"),
            run_id=self.run_id,
            target=query.target,
            level=level,
            budget_tokens=query.budget_tokens,
            sections=_sections_for(kinds, source, selected, query.budget_tokens),
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
            extra = min(pool[used].tokens, remaining)
            if extra <= 0:
                return capsule
            sections[related] = CapsuleSection(
                kind="related",
                tokens=sections[related].tokens + extra,
                items=used + 1,
            )
            return self._transition(capsule, sections, need)

        present = {section.kind for section in sections}
        for kind in EXPANSION_ORDER:
            if kind in present:
                continue
            if kind == "related" and not pool:
                continue
            tokens = min(_AUX_SECTION_TOKENS, remaining)
            if tokens <= 0:
                break
            sections.append(CapsuleSection(kind=kind, tokens=tokens, items=1))
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
        return sha256_text(f"{digest}|{target.symbol or ''}|{level.value}|{CAPSULE_POLICY_VERSION}")

    # --- 내부 ------------------------------------------------------------

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
        vectors = self.embedder.embed([text, *(chunk.text for chunk in candidates)])
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

        ranked = self.reranker.rerank(
            query=text,
            documents=[chunk.text for chunk in narrowed],
            top_n=self.top_n,
        )
        ordered = sorted(ranked, key=lambda r: (-r.score, r.index))
        return [narrowed[r.index] for r in ordered if 0 <= r.index < len(narrowed)]

    def _collect(self, query: Query) -> list[Chunk]:
        """키워드로 1차 필터. 힌트가 없으면 전체(상한까지)가 후보다."""
        corpus = self._load_corpus()
        target_file = query.target.file
        keywords = [k.lower() for k in query.keywords]
        picked = [
            chunk
            for chunk in corpus
            # 대상 자신은 target_function 섹션이 이미 담고 있다. 두 번 세면 토큰이 부풀고
            # reranker 상위가 자기 자신으로 채워진다.
            if chunk.file != target_file
            and (not keywords or any(k in chunk.text.lower() for k in keywords))
        ]
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


def _chunk_tree(root: Path) -> Iterable[Chunk]:
    """소스 파일을 고정 줄 수로 자른다. 순회는 정렬해 결정론을 지킨다."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        lines = path.read_text("utf-8", errors="replace").splitlines()
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
    lines = text.splitlines()
    end = target.line_end or target.line_start
    return "\n".join(lines[max(target.line_start - 1, 0) : end])


def _read_source(root: Path, relative: str) -> str:
    path = safe_join(root, relative)
    if not path.is_file():
        return ""
    return path.read_text("utf-8", errors="replace")


def _sections_for(
    kinds: Sequence[str], source: str, selected: list[Chunk], budget: int
) -> list[CapsuleSection]:
    """예산을 넘기지 않는 선에서 섹션을 채운다.

    `related`의 토큰은 실제로 선별된 조각의 길이 합이다 — 고정값을 쓰면 캡슐 예산
    초과 경로가 영원히 안 타고, 그러면 그 경로가 검증되지 않는다.
    """
    sections: list[CapsuleSection] = []
    used = 0
    for kind in kinds:
        if kind == "target_function":
            cost, items = max(1, len(source) // _CHARS_PER_TOKEN), None
        elif kind == "related":
            if not selected:
                continue
            cost, items = _fit_related(selected, budget - used)
            if items == 0:
                continue
        else:
            cost, items = _AUX_SECTION_TOKENS, None
        tokens = min(cost, budget - used)
        if tokens <= 0:
            break
        sections.append(CapsuleSection(kind=kind, tokens=tokens, items=items))
        used += tokens
    return sections


def _fit_related(selected: list[Chunk], remaining: int) -> tuple[int, int]:
    """예산에 들어가는 만큼만 담는다. (토큰 합, 담은 개수)."""
    total = 0
    count = 0
    for chunk in selected:
        if total + chunk.tokens > remaining:
            break
        total += chunk.tokens
        count += 1
    return total, count
