"""FakeRetriever — embedding·reranker 없이 캡슐을 만드는 결정론적 Retriever.

knowledge-base.md §3의 2단계 구조(광역 수집 → reranker 재선별)는 구현체 내부 사정이고,
호출자는 "무엇에 대해, 얼마 안에"만 지정한다. Fake는 재선별 대신 파일에서 잘라 온
텍스트의 길이로 섹션 토큰을 채운다 — **토큰 수가 실제 내용에서 나와야** 캡슐 예산 초과
경로와 cache hit ratio가 진짜 값이 된다.

`ContextCapsule`은 **매니페스트이면서 그래프 섹션의 본문을 담는다.** `callers`/`callees`는
`CapsuleSection.body`에 줄이 그대로 실리고, 스키마 검증기가 `tokens`와 그 줄들의 일치를
강제한다. `target_function`의 원문은 여전히 담지 않는다 — 호출자가 `read_symbol_source()`로
(슬라이스 타깃이면 `read_slice`로) 따로 읽는다. 파일 전체를 캡슐에 복사하면 매니페스트가 아니라
사본이 된다.

**`callers`/`callees`는 1단계 인덱서 산출물에서 온다**(`adapters/index_graph.py`, #63).
`HybridRetriever`와 **같은 모듈**을 쓴다 — 두 구현이 같은 index.json에서 다른 섹션을 내면
`dev_fake`와 `dev_docker`의 결과를 비교할 수 없다(`rules/harness-c.yaml` 첫 주석과 같은
논법). index.json을 낼지는 `retriever`가 아니라 **`tool_runner`가 정한다** —
`docker`·`singularity`가 내고 `fake`는 안 낸다(`container_tools.INDEX_FILE`). 그렇게
내는 프로필은 **대부분** `retriever: fake`라 **그 연결이 주로 도는 곳이 이 파일이다.**
예외는 `dev_hybrid_docker`·`harness_pool_hybrid`이고 그 둘은 같은 index.json을
`HybridRetriever`로 태운다. **개수는 적지 않는다** — 프로필이 늘고 줄 때마다 틀린다.
"""

from collections.abc import Callable
from pathlib import Path

from src.adapters.index_graph import (
    GRAPH_SECTION_KINDS,
    GraphIndex,
    SliceBody,
    graph_section,
    source_lines,
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
#
# **v2 → v3 (W2/#95)**: LIGHT에 `callers`/`callees`가 들어갔고(#95 (a)) target이 파일에서
# 심볼로 좁혀졌다. 둘 다 같은 키에 다른 캡슐을 만드는 변경이다.
#
# **v3 → v4 (2026-09-25, ADR 0012 결정 1 · #220 ②)**: 슬라이스 질의(`Query.slice_id`)면
# `target_function`이 슬라이스 렌더를 세고, 키에 slice id와 슬라이서 `run_signature`가 붙는다.
# 같은 entry 위치에 다른 캡슐이 생기는 변경이다 — 키 조립 규칙이 바뀌었으므로 올린다.
#
# **v4 → v5 (2026-09-25, #220 ①)**: 슬라이스 질의의 `callers`/`callees`가 entry 한 줄이 아니라
# 슬라이스의 줄 집합을 본문으로 센다(`index_graph.names_for`). 같은 키에 다른 그래프 섹션이다.
CAPSULE_POLICY_VERSION = "v5"

# 본문이 없는 보조 섹션의 고정 비용.
#
# **`static_signal`·`known_facts`에만 남아 있다.** 그래프 파생 섹션은 인덱서 산출물에서
# 실측으로 바뀌었고(`index_graph`), 이 둘은 정적 신호와 Evidence Store에서 와야 하므로
# 아직 데이터원이 붙지 않았다 — 붙는 순간 여기서 빠진다.
_AUX_SECTION_TOKENS = 64

# 레벨별 섹션 구성 (knowledge-base.md §4).
# light=Scout용, expanded=Auditor용, focused=Critic용(반박 축 관련 섹션만).
#
# **`types`가 없다.** knowledge-base.md §4의 목표 구성에는 있지만 index-v1에는 타입 데이터가
# 없었고, index-v2의 `types` 표는 **캡슐로 싣지 않는다** — NE resolver가 모델이 요청했을 때
# 준다(`index_graph.GRAPH_SECTION_KINDS` 주석, #63). 이름만 남겨 두면 다음 사람이 "채워야
# 하는데 비었다"로 읽고, 고정값으로 채우면 없는 근거를 지어내는 것이라 뺀다.
# **LIGHT에 그래프 섹션이 들어간 이유 (#95을 (a)로 확정, 2026-08-23).**
#
# 그전까지 LIGHT는 `("target_function", "static_signal")`이었고 `EXPANDED`부터 그래프가
# 붙었다. `run_20260821_0007`에서 그 결과가 실측으로 확정됐다 — `graph_index
# status=loaded`(심볼 4·호출 5)인데 `memcpy parser.c:24`를 담은 프롬프트는 auditor 것
# 하나뿐이었고 **scout 프롬프트 둘에는 없었다.** scout이 그래프를 얻을 경로가 구조적으로
# 0개였다.
#
# 얇게 둔 근거는 주석에 적혀 있었고(`hybrid_retriever`: "LIGHT는 검색하지 않는다 — Scout는
# 후보 전량에 대해 도는 단계라 여기서 임베딩을 부르면 …") 그것은 **임베딩 호출 비용**에
# 관한 것이다. 그래프 섹션은 임베딩을 부르지 않는다 — 이미 로드된 index.json에서 읽는다
# (`index_graph.names_for()`). 실측 비용은 `callees` 10토큰 / LIGHT 예산 4,000 = 0.25%다.
#
# **(b) `expand()` 배선은 지금 불가능하다.** Scout의 구조화 출력이 `Candidate` 하나라
# "근거가 부족하다"를 담을 칸이 없고(`nodes/auditor.py`가 `MissingEvidence`를 노드가
# 만드는 이유를 적고 있다), `expand()`는 한 번도 돈 적이 없어 판정 규칙을 보정할 데이터가
# 없다. 그 항목은 #95 관측 ③으로 계속 열려 있다 — LIGHT를 두껍게 한 것이지 사다리를
# 배선한 것이 아니다. knowledge-base.md §5가 사다리의 나머지 칸을 그대로 들고 있다.
LEVEL_SECTIONS: dict[CapsuleLevel, tuple[str, ...]] = {
    CapsuleLevel.LIGHT: ("target_function", "callers", "callees", "static_signal"),
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
        read_slice: Callable[[str], str] | None = None,
        slice_signature: str | None = None,
        slice_lines: Callable[[str], SliceBody] | None = None,
    ) -> None:
        self.root = Path(root)
        self.ids = ids
        self.run_id = run_id
        self.on_cache = on_cache
        # 그래프가 주입되지 않은 호출부(어댑터 단위 테스트)는 경로 없는 인덱스를 쓴다 —
        # `status=missing`이 되어 그래프 섹션이 부재한다. 빈 그래프를 지어내지 않는다.
        self.graph = graph if graph is not None else GraphIndex(None)
        # 슬라이스 id → 렌더. Scout `SOURCE`와 **같은 조회**다(`build_deps`가 둘에 같은 것을
        # 넣는다) — 다르면 캡슐의 토큰 수와 실제 프롬프트가 어긋난다(`read_symbol_source` 주석).
        self.read_slice = read_slice
        # 그 렌더를 낸 슬라이서 run의 `run_signature`. 캐시 키에 든다(`cache_key_for`).
        self.slice_signature = slice_signature
        # 슬라이스 id → 그 슬라이스의 (file, line) 집합. 슬라이스 질의의 그래프 섹션 본문이다
        # (`index_graph.names_for`, #220 ①) — `read_slice`와 같이 `build_deps`가 넣는다.
        self.slice_lines = slice_lines
        self._cache: dict[str, ContextCapsule] = {}

    def search(self, query: Query, level: CapsuleLevel) -> ContextCapsule:
        cache_key = self.cache_key_for(query, level)
        cached = self._cache.get(cache_key)
        if self.on_cache is not None:
            self.on_cache(cache_key, cached is not None)
        if cached is not None:
            return cached

        source = self._target_source(query)
        capsule = ContextCapsule(
            capsule_id=self.ids.next("capsule"),
            run_id=self.run_id,
            target=query.target,
            level=level,
            budget_tokens=query.budget_tokens,
            sections=_sections_for(
                level,
                source,
                query.budget_tokens,
                self.graph,
                query.target,
                self._slice_body(query),
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
                # ponytail: 캡슐이 slice id를 안 들어 슬라이스 캡슐도 entry 한 줄로 센다(#220 ①의
                # 줄 집합이 아니다). 호출 지점이 0이라 둔다 — 배선할 때 캡슐에 slice id를 싣는다.
                section, stop = graph_section(kind, self.graph, capsule.target, remaining)
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
        """artifact digest + symbol + 줄 + 캡슐 정책 버전 (knowledge-base.md §6).

        level도 키에 넣는다 — 같은 심볼이라도 Scout용과 Auditor용 캡슐은 다른 물건이다.

        **`line_start`도 넣는다(W2).** 인덱서가 파일 스코프를 구분하지 않아 같은 파일에
        동명 `static` 함수가 둘 있을 수 있고(모듈 docstring의 알려진 오차), 이름만으로는
        그 둘이 한 키를 공유해 **다른 심볼의 캡슐이 재사용된다.**

        **슬라이스 질의면 slice id와 슬라이서 `run_signature`가 붙는다(#220 ②).** target은
        entry라 entry가 같은 두 슬라이스가 위치만으로는 한 키가 되고, slice id는 슬라이서 run
        안의 순번이라(`slice_{number:04d}`) 다른 run에서는 같은 id가 다른 슬라이스일 수 있다.
        """
        target = query.target
        digest = sha256_text(_read_source(self.root, target.file))
        symbol = f"{target.symbol or ''}@{target.line_start or ''}"
        key = f"{digest}|{symbol}|{level.value}|{CAPSULE_POLICY_VERSION}"
        if query.slice_id is not None:
            key = f"{key}|{query.slice_id}@{self.slice_signature}"
        return sha256_text(key)

    def _slice_body(self, query: Query) -> SliceBody | None:
        """그래프 섹션의 본문. 슬라이스 질의면 그 슬라이스의 줄 집합이다(#220 ① — `names_for`)."""
        if query.slice_id is None:
            return None
        if self.slice_lines is None:
            raise ValueError(f"슬라이스 {query.slice_id} 질의인데 slice_lines가 배선되지 않았다")
        return self.slice_lines(query.slice_id)

    def _target_source(self, query: Query) -> str:
        """`target_function`이 셀 본문. 슬라이스 타깃이면 슬라이스 렌더다.

        `input: slice` 프롬프트의 Scout가 받는 그것이다. `input: source` 선언은 함수 본문을 받으므로
        이 토큰 수와 프롬프트가 어긋난다 — 알려진 한계(#529).
        """
        if query.slice_id is None:
            return read_symbol_source(self.root, query.target)
        if self.read_slice is None:
            raise ValueError(f"슬라이스 {query.slice_id} 질의인데 read_slice가 배선되지 않았다")
        return self.read_slice(query.slice_id)


def read_symbol_source(root: Path, target: Location) -> str:
    """대상 심볼의 원문. 줄 범위가 없으면 파일 전체다.

    `ContextCapsule`이 본문을 담지 않으므로 프롬프트를 만드는 쪽도 이 함수를 쓴다.
    두 곳이 서로 다르게 읽으면 캡슐의 토큰 수와 실제 프롬프트가 어긋난다.
    """
    text = _read_source(root, target.file)
    if target.line_start is None:
        return text
    lines = source_lines(text)
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


def read_raw_source(root: Path, relative: str) -> bytes | None:
    """대상 파일의 **원본 바이트**. `read_symbol_source`는 텍스트 모드로 읽어 `\\r`·`\\r\\n`을
    `\\n`으로 접으므로 `\\r\\r\\n`이나 옛 Mac 줄바꿈은 거기서 안 보인다 — 인덱서는 `\\n`만 세서
    줄 번호가 어긋나는데, GUARDS의 줄 정렬 검사(`auditor_guards`)가 그 모양을 보려고 이것을
    쓴다. 없는 파일은 `None`이고 루트 밖은 `read_symbol_source`처럼 `PathEscape`다."""
    path = safe_join(root, relative)
    return path.read_bytes() if path.is_file() else None


def _sections_for(
    level: CapsuleLevel,
    source: str,
    budget: int,
    graph: GraphIndex,
    target: Location,
    slice_body: SliceBody | None = None,
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
            section, stop = graph_section(kind, graph, target, remaining, slice_body)
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
