"""FakeRetriever — embedding·reranker 없이 캡슐을 만드는 결정론적 Retriever.

knowledge-base.md §3의 2단계 구조(광역 수집 → reranker 재선별)는 구현체 내부 사정이고,
호출자는 "무엇에 대해, 얼마 안에"만 지정한다. Fake는 재선별 대신 파일에서 잘라 온
텍스트의 길이로 섹션 토큰을 채운다 — **토큰 수가 실제 내용에서 나와야** 캡슐 예산 초과
경로와 cache hit ratio가 진짜 값이 된다.

`ContextCapsule`은 섹션 텍스트를 담지 않는 **매니페스트**다(스키마에 본문 필드가 없다).
프롬프트에 실을 원문은 호출자가 `read_symbol_source()`로 따로 읽는다. 이 분담은
`src/schemas/`를 넓히지 않으려는 선택이다(roadmap.md §3 전원 공지 대상).
"""

from collections.abc import Callable
from pathlib import Path

from src.ids import IdFactory, sha256_text
from src.interfaces.retriever import Query
from src.schemas.capsule import CapsuleSection, ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel
from src.schemas.finding import MissingEvidence

# knowledge-base.md §6: capsule cache 키에 들어가는 정책 버전. 캡슐 구성 규칙이 바뀌면 올린다.
CAPSULE_POLICY_VERSION = "v1"

_CHARS_PER_TOKEN = 4

# 본문이 없는 보조 섹션의 고정 비용. 실제 retrieval이 들어오면 실측으로 바뀐다.
_AUX_SECTION_TOKENS = 64

# 레벨별 섹션 구성 (knowledge-base.md §4).
# light=Scout용, expanded=Auditor용, focused=Critic용(반박 축 관련 섹션만).
LEVEL_SECTIONS: dict[CapsuleLevel, tuple[str, ...]] = {
    CapsuleLevel.LIGHT: ("target_function", "static_signal"),
    CapsuleLevel.EXPANDED: ("target_function", "callers", "callees", "types", "static_signal"),
    CapsuleLevel.FOCUSED: ("target_function", "callers", "known_facts"),
}

# Progressive Context Expansion의 확장 순서 (knowledge-base.md §5).
EXPANSION_ORDER = ("callers", "callees", "types", "known_facts")

# 캐시 이벤트 콜백 (cache_key, hit). trace 기록은 오케스트레이터가 하므로 어댑터는
# 오케스트레이터를 import 하지 않고 콜백만 부른다(engineering-guide.md §3).
CacheHook = Callable[[str, bool], None]


class FakeRetriever:
    """`Retriever` 구현. 같은 target·level에는 항상 같은 캡슐을 낸다."""

    def __init__(
        self,
        *,
        root: Path,
        ids: IdFactory,
        run_id: str,
        on_cache: CacheHook | None = None,
    ) -> None:
        self.root = Path(root)
        self.ids = ids
        self.run_id = run_id
        self.on_cache = on_cache
        self._cache: dict[str, ContextCapsule] = {}

    def search(self, query: Query, level: CapsuleLevel) -> ContextCapsule:
        cache_key = self.cache_key_for(query, level)
        cached = self._cache.get(cache_key)
        if self.on_cache is not None:
            self.on_cache(cache_key, cached is not None)
        if cached is not None:
            return cached

        source = read_symbol_source(self.root, query.target)
        capsule = ContextCapsule(
            capsule_id=self.ids.next("capsule"),
            run_id=self.run_id,
            target=query.target,
            level=level,
            budget_tokens=query.budget_tokens,
            sections=_sections_for(level, source, query.budget_tokens),
            cache_key=cache_key,
        )
        self._cache[cache_key] = capsule
        return capsule

    def expand(self, capsule: ContextCapsule, need: MissingEvidence) -> ContextCapsule:
        """섹션을 하나 더 붙인 **새** 캡슐을 반환한다.

        확장 횟수 상한 판정은 호출자가 config를 보고 한다(knowledge-base.md §5).
        여기서는 예산을 넘기지 않는 선에서만 붙이고, 붙일 것이 없으면 그대로 돌려준다 —
        "상한 초과"와 "더 붙일 것이 없음"은 다른 사건이므로 섞지 않는다.

        `model_copy`가 아니라 `transition()`을 쓴다. 전자는 캡슐 예산 검증기를 우회한다.
        """
        present = {section.kind for section in capsule.sections}
        remaining = capsule.budget_tokens - capsule.used_tokens
        for kind in EXPANSION_ORDER:
            if kind in present:
                continue
            tokens = min(_AUX_SECTION_TOKENS, remaining)
            if tokens <= 0:
                break
            added = [*capsule.sections, CapsuleSection(kind=kind, tokens=tokens, items=1)]
            return capsule.transition(
                sections=[section.model_dump() for section in added],
                expansion_count=capsule.expansion_count + 1,
                evidence_refs=[*capsule.evidence_refs, need.missing_id],
            )
        return capsule

    def cache_key_for(self, query: Query, level: CapsuleLevel) -> str:
        """artifact digest + symbol + 캡슐 정책 버전 (knowledge-base.md §6).

        level도 키에 넣는다 — 같은 심볼이라도 Scout용과 Auditor용 캡슐은 다른 물건이다.
        """
        target = query.target
        digest = sha256_text(_read_file(self.root / target.file))
        return sha256_text(f"{digest}|{target.symbol or ''}|{level.value}|{CAPSULE_POLICY_VERSION}")


def read_symbol_source(root: Path, target: Location) -> str:
    """대상 심볼의 원문. 줄 범위가 없으면 파일 전체다.

    `ContextCapsule`이 본문을 담지 않으므로 프롬프트를 만드는 쪽도 이 함수를 쓴다.
    두 곳이 서로 다르게 읽으면 캡슐의 토큰 수와 실제 프롬프트가 어긋난다.
    """
    text = _read_file(root / target.file)
    if target.line_start is None:
        return text
    lines = text.splitlines()
    end = target.line_end or target.line_start
    return "\n".join(lines[max(target.line_start - 1, 0) : end])


def _read_file(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text("utf-8", errors="replace")


def _sections_for(level: CapsuleLevel, source: str, budget: int) -> list[CapsuleSection]:
    """예산을 넘기지 않는 선에서 레벨별 섹션을 채운다.

    `ContextCapsule`은 섹션 합이 예산을 넘으면 생성 자체가 실패한다. 넘겼다면 캡슐을
    만들 게 아니라 섹션을 줄여야 하므로(스키마 주석) 여기서 자른다.
    """
    sections: list[CapsuleSection] = []
    used = 0
    for kind in LEVEL_SECTIONS[level]:
        if kind == "target_function":
            cost = max(1, len(source) // _CHARS_PER_TOKEN)
        else:
            cost = _AUX_SECTION_TOKENS
        tokens = min(cost, budget - used)
        if tokens <= 0:
            break
        sections.append(CapsuleSection(kind=kind, tokens=tokens))
        used += tokens
    return sections
