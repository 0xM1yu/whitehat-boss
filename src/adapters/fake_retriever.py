"""FakeRetriever — embedding·reranker 없이 캡슐을 만드는 결정론적 Retriever.

knowledge-base.md §3의 2단계 구조(광역 수집 → reranker 재선별)는 구현체 내부 사정이고,
호출자는 "무엇에 대해, 얼마 안에"만 지정한다. Fake는 재선별 대신 파일에서 잘라 온
텍스트의 길이로 섹션 토큰을 채운다 — **토큰 수가 실제 내용에서 나와야** 캡슐 예산 초과
경로와 cache hit ratio가 진짜 값이 된다.

`ContextCapsule`은 **매니페스트이면서 그래프 섹션의 본문을 담는다.** `callers`/`callees`는
`CapsuleSection.body`에 줄이 그대로 실리고, 스키마 검증기가 `tokens`와 그 줄들의 일치를
강제한다. `target_function`의 원문은 여전히 담지 않는다 — 호출자가 `read_symbol_source()`로
따로 읽는다. 파일 전체를 캡슐에 복사하면 매니페스트가 아니라 사본이 된다.

**`callers`/`callees`는 1단계 인덱서 산출물에서 온다**(`adapters/index_graph.py`, D13).
`HybridRetriever`와 **같은 모듈**을 쓴다 — 두 구현이 같은 index.json에서 다른 섹션을 내면
`dev_fake`와 `dev_docker`의 결과를 비교할 수 없다(`rules/harness-c.yaml` 첫 주석과 같은
논법). index.json을 내는 프로필이 전부 `retriever: fake`이므로(`dev_docker`·
`dev_singularity`·`local_balanced`·`b1_single`) **그 연결이 실제로 도는 곳이 이 파일이다.**
"""

from collections.abc import Callable
from pathlib import Path

from src.adapters.index_graph import (
    GRAPH_SECTION_KINDS,
    GraphIndex,
    graph_section,
)
from src.ids import IdFactory, sha256_text
from src.interfaces.retriever import Query
from src.paths import safe_join
from src.schemas.capsule import CapsuleSection, ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel
from src.schemas.finding import MissingEvidence
from src.tokens import estimate_tokens

# knowledge-base.md §6: capsule cache 키에 들어가는 정책 버전. 캡슐 구성 규칙이 바뀌면 올린다.
#
# **v1 → v2 (2026-08-20)**: `types`를 없애고 `callers`/`callees`를 실제 호출 그래프에서
# 채우도록 바꿨다. 올리지 않으면 구성이 다른 캡슐이 이전 실행과 같은 키를 공유한다.
CAPSULE_POLICY_VERSION = "v2"

# 본문이 없는 보조 섹션의 고정 비용.
#
# **`static_signal`·`known_facts`에만 남아 있다.** 그래프 파생 섹션은 인덱서 산출물에서
# 실측으로 바뀌었고(`index_graph`), 이 둘은 정적 신호와 Evidence Store에서 와야 하므로
# 아직 데이터원이 붙지 않았다 — 붙는 순간 여기서 빠진다.
_AUX_SECTION_TOKENS = 64

# 레벨별 섹션 구성 (knowledge-base.md §4).
# light=Scout용, expanded=Auditor용, focused=Critic용(반박 축 관련 섹션만).
#
# **`types`가 없다.** knowledge-base.md §4의 목표 구성에는 있지만 index-v1이 타입 데이터를
# 내지 않는다(`indexer.build_index()`는 symbols·calls뿐). 이름만 남겨 두면 다음 사람이
# "채워야 하는데 비었다"로 읽고, 고정값으로 채우면 없는 근거를 지어내는 것이라 뺀다.
# 채우려면 인덱서에 타입 수집을 넣고 `INDEX_VERSION`을 올려야 한다(D13).
LEVEL_SECTIONS: dict[CapsuleLevel, tuple[str, ...]] = {
    CapsuleLevel.LIGHT: ("target_function", "static_signal"),
    CapsuleLevel.EXPANDED: ("target_function", "callers", "callees", "static_signal"),
    CapsuleLevel.FOCUSED: ("target_function", "callers", "known_facts"),
}

# Progressive Context Expansion의 확장 순서 (knowledge-base.md §5).
EXPANSION_ORDER = ("callers", "callees", "known_facts")

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
        graph: GraphIndex | None = None,
    ) -> None:
        self.root = Path(root)
        self.ids = ids
        self.run_id = run_id
        self.on_cache = on_cache
        # 그래프가 주입되지 않은 호출부(어댑터 단위 테스트)는 경로 없는 인덱스를 쓴다 —
        # `status=missing`이 되어 그래프 섹션이 부재한다. 빈 그래프를 지어내지 않는다.
        self.graph = graph if graph is not None else GraphIndex(None)
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
            sections=_sections_for(
                level, source, query.budget_tokens, self.graph, query.target.file
            ),
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

        **`items`를 1로 박지 않는다.** 예전에는 어떤 섹션이든 `items=1`이었는데, 그건
        `search()`가 보조 섹션에 `items=None`을 주는 것과도 어긋나고 그래프 섹션에서는
        실제 개수와도 어긋나는 값이었다 — 확장 경로만 계속 지어낸 값을 냈다.
        """
        present = {section.kind for section in capsule.sections}
        remaining = capsule.budget_tokens - capsule.used_tokens
        for kind in EXPANSION_ORDER:
            if kind in present:
                continue
            if kind in GRAPH_SECTION_KINDS:
                # 그래프 분기는 `HybridRetriever.expand()`와 **같은 함수**를 부른다.
                section, stop = graph_section(
                    kind, self.graph, capsule.target.file, remaining
                )
                if section is None:
                    if stop:
                        # 붙일 것은 있는데 예산이 없다. "더 붙일 것이 없음"과 다른
                        # 사건이므로 0건 섹션으로 위장하지 않고 확장을 멈춘다.
                        break
                    # 그래프가 없다. 없는 섹션을 지어내지 않고 다음 후보로 넘어간다.
                    continue
            else:
                tokens = min(_AUX_SECTION_TOKENS, remaining)
                if tokens <= 0:
                    break
                section = CapsuleSection(kind=kind, tokens=tokens)
            added = [*capsule.sections, section]
            return capsule.transition(
                sections=[existing.model_dump() for existing in added],
                expansion_count=capsule.expansion_count + 1,
                evidence_refs=[*capsule.evidence_refs, need.missing_id],
            )
        return capsule

    def cache_key_for(self, query: Query, level: CapsuleLevel) -> str:
        """artifact digest + symbol + 캡슐 정책 버전 (knowledge-base.md §6).

        level도 키에 넣는다 — 같은 심볼이라도 Scout용과 Auditor용 캡슐은 다른 물건이다.
        """
        target = query.target
        digest = sha256_text(_read_source(self.root, target.file))
        return sha256_text(f"{digest}|{target.symbol or ''}|{level.value}|{CAPSULE_POLICY_VERSION}")


def read_symbol_source(root: Path, target: Location) -> str:
    """대상 심볼의 원문. 줄 범위가 없으면 파일 전체다.

    `ContextCapsule`이 본문을 담지 않으므로 프롬프트를 만드는 쪽도 이 함수를 쓴다.
    두 곳이 서로 다르게 읽으면 캡슐의 토큰 수와 실제 프롬프트가 어긋난다.
    """
    text = _read_source(root, target.file)
    if target.line_start is None:
        return text
    lines = text.splitlines()
    end = target.line_end or target.line_start
    return "\n".join(lines[max(target.line_start - 1, 0) : end])


def _read_source(root: Path, relative: str) -> str:
    """대상 루트 아래 파일만 읽는다. 루트 탈출은 `PathEscape`로 실패한다(src/paths.py).

    없는 파일은 빈 내용이다 — 인덱스가 가리키는 파일이 사라진 것은 신호 부재이고,
    루트 밖을 가리키는 것은 그와 다른 사건이라 섞지 않는다.
    """
    path = safe_join(root, relative)
    if not path.is_file():
        return ""
    return path.read_text("utf-8", errors="replace")


def _sections_for(
    level: CapsuleLevel, source: str, budget: int, graph: GraphIndex, target_file: str
) -> list[CapsuleSection]:
    """예산을 넘기지 않는 선에서 레벨별 섹션을 채운다.

    `ContextCapsule`은 섹션 합이 예산을 넘으면 생성 자체가 실패한다. 넘겼다면 캡슐을
    만들 게 아니라 섹션을 줄여야 하므로(스키마 주석) 여기서 자른다.

    **`HybridRetriever._sections_for()`와 그래프 분기가 같아야 한다.** 두 구현이 같은
    index.json에서 다른 섹션을 내면 `dev_fake`↔`dev_docker` 비교가 성립하지 않는다.
    """
    sections: list[CapsuleSection] = []
    used = 0
    for kind in LEVEL_SECTIONS[level]:
        remaining = budget - used
        if kind in GRAPH_SECTION_KINDS:
            # 그래프 분기는 `HybridRetriever._sections_for()`와 **같은 함수**를 부른다.
            # 복사본 둘이 갈리면 같은 index.json에서 두 구현이 다른 캡슐을 낸다.
            section, stop = graph_section(kind, graph, target_file, remaining)
            if section is None:
                if stop:
                    # 붙일 것은 있는데 예산이 없다.
                    break
                # 인덱서가 안 돌았거나 형식이 어긋났다 — 섹션을 만들지 않는다.
                # 0건 섹션으로 내면 "그래프가 비었다"와 구별되지 않는다.
                continue
            sections.append(section)
            used += section.tokens
            continue
        cost = estimate_tokens(source) if kind == "target_function" else _AUX_SECTION_TOKENS
        # **`cost == 0`은 "예산이 없다"가 아니라 "그래프를 봤는데 간선이 0건이다"이다.**
        # 두 사건을 한 조건으로 묶으면(예전의 `if tokens <= 0: break`) 빈 그래프가 예산
        # 소진으로 기록되고, 그러면 캡슐만 보고는 인덱서가 안 돈 것과 구별되지 않는다.
        #
        # 그래프 섹션이 위에서 먼저 빠지므로 **아래 `min()`은 본문 있는 섹션에 닿지
        # 않는다.** 닿으면 줄은 그대로 두고 토큰만 깎는 것이 되어 `CapsuleSection`의
        # 검증기에서 죽는다 — 그 사실이 두 함수 사이의 암묵이 아니라 구조로 있어야 한다.
        if cost > 0 and remaining <= 0:
            break
        tokens = min(cost, remaining)
        sections.append(CapsuleSection(kind=kind, tokens=tokens))
        used += tokens
    return sections
