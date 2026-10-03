"""1단계 인덱서 산출물이 캡슐의 그래프 섹션이 되는 경로 (D13).

`index.json`이 **있는 경로와 없는 경로 둘 다** 태운다. 가르는 것은 `retriever`가 아니라
**`tool_runner`다** — `docker`·`singularity`인 프로필이 내고 `fake`인 쪽(`dev_hybrid`·
`dev_fake`, 그리고 2026-09-24에 지운 옛 `single_gpu_12g`)은 안 낸다. 한쪽만 태우면 나머지
절반은 아무도 안 보는
코드가 된다.

**프로필 이름을 열거하지 않는다** — 예전 판이 다섯을 적었는데 그때도 이미 넷이 빠져
있었고, 프로필이 늘 때마다 또 틀린다(#139가 같은 이유로 개수를 걷어냈다). 조건을 적으면
목록은 `profiles.yaml`이 답한다.

**여기서 고정하는 핵심은 "없다"와 "0건"이 구별된다는 것이다.** 조용히 빈 섹션을 내면
"그래프가 비었다"와 "인덱서가 안 돌았다"가 같은 모양이 되고, 그것이 이 PR이 없애려던
거짓말과 같은 종류다.
"""

import json
from pathlib import Path

import pytest

from src.adapters.container_tools import index_output_path
from src.adapters.factory import build_deps
from src.adapters.fake_retriever import FakeRetriever
from src.adapters.hybrid_retriever import HybridRetriever
from src.adapters.index_graph import (
    GRAPH_SECTION_KINDS,
    STATUS_LOADED,
    STATUS_MALFORMED,
    STATUS_MISSING,
    STATUS_UNSUPPORTED,
    SUPPORTED_INDEX_VERSIONS,
    V2_TABLES,
    GraphIndex,
    IndexSummary,
    fit_names,
)
from src.adapters.vllm_retrieval import FakeEmbeddingClient, FakeRerankerClient
from src.config import load_config
from src.graph_entry import name_of, render_entry
from src.ids import IdFactory
from src.interfaces.retriever import Query
from src.orchestrator.nodes import auditor
from src.orchestrator.nodes.scout import describe_sections, describe_target
from src.orchestrator.prompt_loader import build_facts, load_prompt
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel, Role
from src.schemas.finding import MissingEvidence
from src.schemas.stores import StoreTarget
from src.tokens import estimate_tokens

RUN_ID = "run_test_0001"

# 호출 사슬이 있는 최소 인덱스.
#
#   main.c:12      main         → parse_header  (외부에서 parser.c로 들어오는 호출)
#   parser.c:18    parse_header → helper        (parser.c 안에서 안으로 — 본문에 이미 있다)
#   parser.c:20    parse_header → memcpy        (symbols에 없는 외부 함수)
#
# 세 행이 각각 `callers` 포함 / `callees` 제외 / `callees` 포함을 만든다.
_INDEX_V1 = {
    "version": "index-v1",
    "files": ["main.c", "parser.c"],
    "skipped": [],
    "symbols": [
        {"name": "main", "file": "main.c", "line_start": 5, "line_end": 15},
        {"name": "helper", "file": "parser.c", "line_start": 3, "line_end": 6},
        {"name": "parse_header", "file": "parser.c", "line_start": 9, "line_end": 22},
    ],
    "calls": [
        {"caller": "main", "callee": "parse_header", "file": "main.c", "line": 12},
        {"caller": "parse_header", "callee": "helper", "file": "parser.c", "line": 18},
        {"caller": "parse_header", "callee": "memcpy", "file": "parser.c", "line": 20},
    ],
    "unresolved_calls": 4,
    "unresolved_symbols": 2,
    "parse_errors": ["main.c"],
}

_CALLER_ENTRY = "main main.c:12"
_CALLEE_ENTRY = "memcpy parser.c:20"

# 심볼 단위 target (W2). `parse_header`의 줄 범위가 곧 캡슐 본문이다.
_PARSE_HEADER = Location(file="parser.c", symbol="parse_header", line_start=9, line_end=22)


def _file_target(file: str) -> Location:
    """인덱스가 없는 프로필이 쓰는 파일 단위 target. 파일 전체가 본문이다."""
    return Location(file=file)


def _write_index(artifacts_root: Path, document: object) -> Path:
    """컨테이너 러너가 쓰는 **바로 그 경로**에 놓는다.

    테스트가 경로를 따로 조립하면 실제 배선이 어긋나도 초록불이 나온다.
    """
    path = index_output_path(artifacts_root, RUN_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = document if isinstance(document, str) else json.dumps(document)
    path.write_text(body, encoding="utf-8")
    return path


def _graph(artifacts_root: Path, document: object = _INDEX_V1, **kwargs) -> GraphIndex:
    if document is not None:
        _write_index(artifacts_root, document)
    return GraphIndex(index_output_path(artifacts_root, RUN_ID), **kwargs)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """인덱스가 가리키는 파일들. 캡슐의 `target_function` 토큰이 여기서 나온다."""
    root = tmp_path / "src"
    root.mkdir()
    (root / "parser.c").write_text("void parse_header(void) { helper(); }\n", encoding="utf-8")
    (root / "main.c").write_text("int main(void) { parse_header(); }\n", encoding="utf-8")
    (root / "orphan.c").write_text("static int lonely(void) { return 0; }\n", encoding="utf-8")
    return root


def _fake(source_root: Path, graph: GraphIndex) -> FakeRetriever:
    return FakeRetriever(root=source_root, ids=IdFactory(RUN_ID), run_id=RUN_ID, graph=graph)


def _hybrid(source_root: Path, graph: GraphIndex) -> HybridRetriever:
    return HybridRetriever(
        root=source_root,
        ids=IdFactory(RUN_ID),
        run_id=RUN_ID,
        embedder=FakeEmbeddingClient(),
        reranker=FakeRerankerClient(),
        graph=graph,
    )


def _query(file: str = "parser.c", budget: int = 4096) -> Query:
    return Query(run_id=RUN_ID, target=Location(file=file), keywords=[], budget_tokens=budget)


def _section(capsule, kind: str):
    return next((s for s in capsule.sections if s.kind == kind), None)


# --- index.json이 없는 경로 (dev_hybrid·dev_fake) -------------------------------


def test_capsule_omits_graph_sections_when_no_index_file_exists(source: Path, tmp_path: Path):
    """인덱서가 안 돌았으면 섹션을 만들지 않는다. 0건 섹션으로 내면 빈 그래프와 같아진다."""
    capsule = _fake(source, _graph(tmp_path, document=None)).search(_query(), CapsuleLevel.EXPANDED)

    assert [s.kind for s in capsule.sections if s.kind in GRAPH_SECTION_KINDS] == []


def test_missing_index_is_reported_as_missing_not_as_empty_graph(tmp_path: Path):
    seen: list[IndexSummary] = []
    graph = _graph(tmp_path, document=None, on_index=seen.append)

    assert graph.names_for("callers", _file_target("parser.c")) is None
    assert [s.status for s in seen] == [STATUS_MISSING]


def test_dev_hybrid_capsule_is_unchanged_when_no_indexer_ran(source: Path, tmp_path: Path):
    """`dev_hybrid`는 `tool_runner: fake`라 index.json이 없다.

    이 프로필의 캡슐이 이번 변경으로 달라지면 안 된다 — 달라지면 변경 전후 지표를
    비교할 수 없는 프로필이 하나 더 늘어난다.
    """
    capsule = _hybrid(source, _graph(tmp_path, document=None)).search(
        _query(), CapsuleLevel.EXPANDED
    )

    assert [s.kind for s in capsule.sections] == ["target_function", "related", "static_signal"]


# --- 형식 판정 -----------------------------------------------------------------


def test_unknown_index_version_is_refused_without_raising(tmp_path: Path):
    """`indexer.py`가 "읽는 쪽이 모르는 버전은 거절한다"고 정했다. 거절하는 쪽이 여기다.

    예외를 던지면 도구 형식 불일치가 run 전체의 장애가 된다.
    """
    graph = _graph(tmp_path, {**_INDEX_V1, "version": "index-v99"})

    assert graph.names_for("callers", _file_target("parser.c")) is None
    assert graph.summary.status == STATUS_UNSUPPORTED


def test_malformed_index_json_is_refused_without_raising(tmp_path: Path):
    graph = _graph(tmp_path, "{ this is not json")

    assert graph.names_for("callees", _file_target("parser.c")) is None
    assert graph.summary.status == STATUS_MALFORMED


def test_index_with_the_right_version_but_missing_fields_is_refused(tmp_path: Path):
    """버전만 맞고 필드가 없으면 부분 결과를 쓰지 않는다 — 어디까지 믿을지 알 수 없다."""
    graph = _graph(tmp_path, {"version": "index-v1", "symbols": []})

    assert graph.names_for("callers", _file_target("parser.c")) is None
    assert graph.summary.status == STATUS_MALFORMED


# --- 간선 선택 규칙 -------------------------------------------------------------


def test_callers_come_from_other_files_only(tmp_path: Path):
    """같은 파일 안의 호출자는 `target_function`이 이미 담고 있다. 두 번 세면 토큰이 부푼다."""
    assert _graph(tmp_path).names_for("callers", _file_target("parser.c")) == [_CALLER_ENTRY]


def test_callee_defined_in_the_target_file_is_not_listed_twice(tmp_path: Path):
    """`helper`는 parser.c에 정의돼 있어 본문에 이미 있다 — 간선의 반대쪽 끝이 캡슐 안이다."""
    callees = _graph(tmp_path).names_for("callees", _file_target("parser.c"))

    assert not any(name.startswith("helper ") for name in callees)


def test_external_callee_not_in_the_symbol_table_is_kept(tmp_path: Path):
    """`memcpy`는 symbols에 없다 = 본문 안에 없는 것이 확실하다. 빼면 실재하는 간선을 숨긴다."""
    assert _graph(tmp_path).names_for("callees", _file_target("parser.c")) == [_CALLEE_ENTRY]


# --- 헤더에 정의된 함수 (index-v2 재측정 (a), 2026-09-26) --------------------------
#
#   a.c:5      caller       → helper   (외부에서 헤더 함수로 들어오는 호출)
#   util.h:2   helper       → memcpy   (헤더 함수 본문에서 나가는 호출)
#
# 캡슐 대상이 헤더(`.h`)에 정의된 함수여도(Auditor 앵커가 그렇게 될 수 있다,
# `runner._auditor_target`) 그래프 섹션이 비지 않는지를 index-v2 보고서가 안 잰 경로다
# (2026-09-26-index-v2-atom.md §5 「말하지 않는 것」).
_INDEX_WITH_HEADER = {
    "version": "index-v1",
    "files": ["a.c", "util.h"],
    "skipped": [],
    "symbols": [
        {"name": "caller", "file": "a.c", "line_start": 4, "line_end": 6},
        {"name": "helper", "file": "util.h", "line_start": 1, "line_end": 3},
    ],
    "calls": [
        {"caller": "caller", "callee": "helper", "file": "a.c", "line": 5},
        {"caller": "helper", "callee": "memcpy", "file": "util.h", "line": 2},
    ],
    "unresolved_calls": 0,
    "unresolved_symbols": 0,
    "parse_errors": [],
}

_HELPER_TARGET = Location(file="util.h", symbol="helper", line_start=1, line_end=3)


def test_callers_into_a_header_defined_function_are_counted(tmp_path: Path):
    """캡슐 대상이 헤더에 정의된 함수(`helper`)여도 그 함수로 들어오는 외부 호출이 `callers`에
    실린다 — 정의 파일이 `.c`인가 `.h`인가는 판정(`_defined_in_body`)이 안 본다."""
    graph = _graph(tmp_path, _INDEX_WITH_HEADER)

    assert graph.names_for("callers", _HELPER_TARGET) == ["caller a.c:5"]


def test_callees_from_a_header_defined_function_are_counted(tmp_path: Path):
    """헤더 함수 본문에서 나가는 호출(`memcpy`)도 `callees`에 실린다 — 호출 지점이 헤더 파일
    자신인 경우다."""
    graph = _graph(tmp_path, _INDEX_WITH_HEADER)

    assert graph.names_for("callees", _HELPER_TARGET) == ["memcpy util.h:2"]


# --- 슬라이스 본문 (#220 ①) ------------------------------------------------------
#
# 슬라이스 타깃의 본문은 연속 범위가 아니라 슬라이스의 (file, line) 집합이다 — 프롬프트가 싣는
# 것이 그 렌더라서다. 「본문 안에 정의」는 정의의 **범위**가 그 집합과 겹치는가다.


def test_a_slice_body_counts_a_definition_whose_range_it_overlaps(tmp_path: Path):
    """{12, 18, 20}은 `parse_header`(9–22)의 머리 줄 9를 안 품지만 그 범위 안이다 — `main`의
    호출이 callers에 남는다. 시작 줄로 판정하면 빠진다(K&R 머리·손으로 만든 줄 집합의 모양).
    callees는 보이는 줄의 호출이다 — `helper`(3–6)는 본문과 안 겹쳐 남는다. parser.c:12는
    main.c:12와 **줄 번호만** 같다 — 파일을 안 보고 줄만 보면 `main`의 호출이 본문 안으로
    읽혀 빠진다."""
    graph = _graph(tmp_path)
    body = frozenset({("parser.c", 12), ("parser.c", 18), ("parser.c", 20)})
    entry = Location(file="parser.c", line_start=20)

    assert graph.names_for("callers", entry, body) == [_CALLER_ENTRY]
    assert graph.names_for("callees", entry, body) == ["helper parser.c:18", _CALLEE_ENTRY]


def test_a_slice_body_spans_files_and_drops_edges_whose_both_ends_it_shows(tmp_path: Path):
    """군집 슬라이스는 파일을 가로지른다. main.c:12의 `main → parse_header`는 양 끝이 다 본문이라
    어느 섹션에도 없다 — 이미 원문에 있다."""
    graph = _graph(tmp_path)
    body = frozenset({("main.c", 12), ("parser.c", 20)})
    entry = Location(file="main.c", line_start=12)

    assert graph.names_for("callers", entry, body) == []
    assert graph.names_for("callees", entry, body) == [_CALLEE_ENTRY]


def test_a_slice_body_cannot_tell_two_definitions_apart_by_file(tmp_path: Path):
    """⚠ **이것은 한계를 박은 핀이지 바람직한 동작이 아니다**(#288 리뷰 LOW-2).

    「본문 안에 정의」 판정은 **이름만** 본다. 호출 지점 판정이 `(file, line)`으로 파일을 보는 것과
    대비된다(바로 위 두 테스트). 군집 슬라이스가 파일을 가로지르면 `shown`이 여러 파일의 이름을
    한 집합에 담아, 같은 이름이 두 파일에 정의돼 있을 때 어느 쪽으로 가는 호출인지 구별하지 못한다.

    여기서 `parse_header`는 parser.c(9–22, 슬라이스와 겹침)와 other.c(40–60, 안 겹침) 둘에 있다.
    z.c:3의 `zeta → parse_header`가 other.c 쪽을 가리켜도 callers에 실린다.

    **인덱스의 `calls`가 callee를 파일 없는 이름으로만 들어 지금은 고칠 수 없다** —
    `{"caller": ..., "callee": ..., "file": ..., "line": ...}`의 file·line은 **호출 지점**이지
    정의 위치가 아니다. 인덱스가 callee 파일을 들게 되면 이 테스트가 빨개진다. 그때
    `_rules`의 `shown`을 `(file, name)`으로 좁히고 이 핀을 뒤집는다.
    """
    document = {
        **_INDEX_V1,
        "files": ["main.c", "parser.c", "other.c", "z.c"],
        "symbols": [
            *_INDEX_V1["symbols"],
            {"name": "parse_header", "file": "other.c", "line_start": 40, "line_end": 60},
        ],
        "calls": [
            *_INDEX_V1["calls"],
            {"caller": "zeta", "callee": "parse_header", "file": "z.c", "line": 3},
        ],
    }
    graph = _graph(tmp_path, document)
    body = frozenset({("parser.c", 20)})
    entry = Location(file="parser.c", line_start=20)

    callers = graph.names_for("callers", entry, body)

    # `zeta`의 호출이 other.c의 동명 정의를 가리켜도 구별하지 못해 실린다.
    assert "zeta z.c:3" in callers
    # 그 실림이 **겹침 때문**임을 같이 박는다 — 슬라이스가 `helper`(3-6)만 지나면 `shown`에
    # `parse_header`가 없어 같은 간선이 안 실린다. 이 대비가 없으면 위 단언이 「늘 실린다」와
    # 구별되지 않아 핀이 성질을 안 문다.
    helper_only = frozenset({("parser.c", 4)})
    assert "zeta z.c:3" not in graph.names_for(
        "callers", Location(file="parser.c", line_start=4), helper_only
    )


def test_graph_entries_are_sorted_for_determinism(tmp_path: Path):
    """같은 입력에 같은 캡슐이 나오지 않으면 재현성이 깨진다(engineering-guide.md §5)."""
    document = {
        **_INDEX_V1,
        "calls": [
            {"caller": "zeta", "callee": "parse_header", "file": "z.c", "line": 3},
            {"caller": "alpha", "callee": "parse_header", "file": "a.c", "line": 7},
        ],
    }

    assert _graph(tmp_path, document).names_for("callers", _file_target("parser.c")) == [
        "alpha a.c:7",
        "zeta z.c:3",
    ]


# --- "없다" vs "0건" -----------------------------------------------------------


def test_file_with_no_edges_gets_a_zero_item_section(source: Path, tmp_path: Path):
    """봤는데 간선이 0건이면 `tokens=0, items=0` 섹션을 **발행한다.**"""
    capsule = _fake(source, _graph(tmp_path)).search(_query("orphan.c"), CapsuleLevel.EXPANDED)
    callers = _section(capsule, "callers")

    assert callers is not None
    assert (callers.tokens, callers.items) == (0, 0)


def test_zero_edge_section_is_distinguishable_from_a_missing_index(source: Path, tmp_path: Path):
    """이 구별이 이 PR의 요점이다. 두 경우가 같은 모양이면 아무것도 고쳐지지 않았다."""
    zero = _fake(source, _graph(tmp_path)).search(_query("orphan.c"), CapsuleLevel.EXPANDED)
    absent = _fake(source, _graph(tmp_path / "empty", document=None)).search(
        _query("orphan.c"), CapsuleLevel.EXPANDED
    )

    assert _section(zero, "callers") is not None
    assert _section(absent, "callers") is None


# --- 실측 토큰과 예산 -----------------------------------------------------------


def test_callers_section_tokens_come_from_the_call_graph_not_a_constant(
    source: Path, tmp_path: Path
):
    """고정 64토큰이던 자리다. 상수로 두면 캡슐 크기가, 그러면 비용 지표가 거짓이 된다."""
    capsule = _fake(source, _graph(tmp_path)).search(_query(), CapsuleLevel.EXPANDED)
    callers = _section(capsule, "callers")

    assert callers.items == 1
    assert callers.tokens == len(_CALLER_ENTRY) // 4
    assert callers.tokens != 64


def test_graph_section_records_how_many_entries_actually_fit_the_budget(tmp_path: Path):
    """`min()`으로 숫자만 깎으면 items는 전체인데 tokens는 잘린 값이라 앞뒤가 안 맞는다."""
    names = ["aaaaaaaa a.c:1", "bbbbbbbb b.c:2", "cccccccc c.c:3"]
    per_entry = len(names[0]) // 4

    assert fit_names(names, per_entry * 2) == (per_entry * 2, 2, names[:2])
    assert fit_names(names, 10_000) == (per_entry * 3, 3, names)


def test_fit_names_returns_the_lines_it_counted_not_just_the_count(tmp_path: Path):
    """토큰을 센 줄을 그대로 돌려줘야 캡슐이 그 줄을 프롬프트에 실을 수 있다.

    예전에는 `(토큰합, 개수)`만 내고 문자열을 버렸다. 그래서 캡슐은 "몇 토큰짜리
    callers가 k건"까지만 알고 **그 k건이 무엇인지는 몰랐다** — 모델에게 보여줄 것이
    없었다(W1).
    """
    names = ["aaaaaaaa a.c:1", "bbbbbbbb b.c:2", "cccccccc c.c:3"]
    tokens, items, fitted = fit_names(names, len(names[0]) // 4 * 2)

    assert fitted == names[:2]
    assert items == len(fitted)
    assert tokens == sum(estimate_tokens(line) for line in fitted)


def test_render_and_parse_are_fixed_together(tmp_path: Path):
    """`render_entry()`와 `name_of()`가 왕복해야 한다.

    둘이 갈리면 형식을 바꾼 사람이 한쪽만 고치고 `callers` 사실 블록이 조용히 틀린
    이름을 낸다 — 렌더러 옆에 파서를 둔 이유가 이 테스트다.
    """
    assert name_of(render_entry("memcpy", "util.c", 42)) == "memcpy"
    assert name_of(render_entry("parse_header", "src/deep/path.c", 7)) == "parse_header"
    # 형식이 어긋난 줄을 조용히 빈 문자열로 만들지 않는다.
    assert name_of("bare_name") == "bare_name"


def test_budget_exhausted_section_is_not_emitted_as_zero_items(source: Path, tmp_path: Path):
    """붙일 것이 있는데 예산이 없는 것은 "0건 관측"이 아니다. 섞으면 위 구별이 무너진다."""
    target_tokens = max(1, len((source / "parser.c").read_text("utf-8")) // 4)
    capsule = _fake(source, _graph(tmp_path)).search(
        _query(budget=target_tokens), CapsuleLevel.EXPANDED
    )

    assert _section(capsule, "callers") is None


def test_graph_sections_vanish_when_target_function_eats_the_budget(source: Path, tmp_path: Path):
    """**알려진 한계의 박제 — 회귀 방지가 아니다.**

    index.json이 멀쩡한데도 `target_function`이 레벨 예산을 다 먹으면 그래프 섹션이
    발행되지 못해 "index.json이 없음"과 **같은 모양**이 된다. 지금 target이
    `Location(file=...)`이라 `read_symbol_source()`가 파일 전체를 돌려주기 때문이고,
    실측 임계는 focused(8,000토큰) 32KB / expanded(24,000토큰) 96KB다.

    **심볼 단위 캡슐(D13 후속)이 들어오면 이 동작이 바뀌는 것이 의도다** —
    `target_function`이 심볼 하나로 좁혀져 예산이 남는다. 그때 이 테스트를 고친다.
    """
    big = source / "parser.c"
    big.write_text("void parse_header(void) { helper(); }\n" * 3000, encoding="utf-8")
    budget = max(1, len(big.read_text("utf-8")) // 4) // 2

    capsule = _fake(source, _graph(tmp_path)).search(_query(budget=budget), CapsuleLevel.EXPANDED)

    assert [s.kind for s in capsule.sections] == ["target_function"]
    assert capsule.used_tokens == budget


# --- expand -------------------------------------------------------------------


def test_expand_records_the_real_entry_count_not_one(source: Path, tmp_path: Path):
    """예전에는 어떤 섹션이든 `items=1`이었다 — 확장 경로만 계속 지어낸 값을 냈다.

    **호출자가 2건 이상이어야 이 테스트가 뜻을 갖는다.** 1건짜리 인덱스를 쓰면 하드코딩된
    `items=1`도 우연히 맞아 통과한다(변이 검사가 그 구멍을 잡았다).
    """
    entries = ["alpha a.c:7", "main main.c:12"]
    document = {
        **_INDEX_V1,
        "calls": [
            {"caller": "main", "callee": "parse_header", "file": "main.c", "line": 12},
            {"caller": "alpha", "callee": "parse_header", "file": "a.c", "line": 7},
        ],
    }
    retriever = _fake(source, _graph(tmp_path, document))
    capsule = retriever.search(_query(), CapsuleLevel.LIGHT)
    need = MissingEvidence(
        missing_id="mev_0001",
        run_id=RUN_ID,
        finding_id="find_0001",
        need="호출부 문맥",
        how_to_obtain="retrieval 확장",
        blocking=False,
    )

    callers = _section(retriever.expand(capsule, need), "callers")

    assert callers.items == 2
    assert callers.tokens == sum(len(entry) // 4 for entry in entries)


# --- 두 구현의 일치 -------------------------------------------------------------


def test_fake_and_hybrid_render_the_same_graph_sections_for_the_same_index(
    source: Path, tmp_path: Path
):
    """갈리면 `dev_fake`와 `dev_docker`의 결과를 같은 표에서 비교할 수 없다.

    `rules/harness-c.yaml` 첫 주석이 정적 룰셋에 대해 편 논법과 같다.
    """
    fake = _fake(source, _graph(tmp_path)).search(_query(), CapsuleLevel.EXPANDED)
    hybrid = _hybrid(source, _graph(tmp_path)).search(_query(), CapsuleLevel.EXPANDED)

    def graph_only(capsule):
        return [s.model_dump() for s in capsule.sections if s.kind in GRAPH_SECTION_KINDS]

    assert graph_only(fake), "그래프 섹션이 비어 있으면 이 비교가 공허하다"
    assert graph_only(fake) == graph_only(hybrid)


@pytest.mark.parametrize("profile", ["dev_fake", "dev_hybrid"])
def test_build_deps_wires_the_index_into_the_retriever(profile: str, source: Path, tmp_path: Path):
    """**배선까지 지나야 한다.** 어댑터를 직접 만들어 검사하면 배선 누락을 못 잡는다.

    이 저장소가 정확히 그 결함을 겪었다 — `factory`가 Retriever를 Fake로 하드코딩해서
    프로필에 적힌 embedding·reranker가 한 번도 호출되지 않았고, 어댑터 단위 테스트는
    전부 초록이었다(`test_retrieval.py::test_pipeline_actually_calls_embedding_and_reranker`).
    `_Wiring`에서 `graph=` 한 줄이 빠지면 인덱서 산출물을 아무도 읽지 않는 상태가 된다.
    """
    artifacts_root = tmp_path / "artifacts"
    _write_index(artifacts_root, _INDEX_V1)

    deps = build_deps(
        load_config(profile), run_id=RUN_ID, source_root=source, artifacts_root=artifacts_root
    )
    capsule = deps.retriever.search(_query(), CapsuleLevel.EXPANDED)

    assert _section(capsule, "callers") is not None, f"{profile}: 그래프가 배선되지 않았다"
    assert _section(capsule, "callers").items == 1


def test_two_instances_produce_the_same_graph_sections(source: Path, tmp_path: Path):
    first = _fake(source, _graph(tmp_path)).search(_query(), CapsuleLevel.EXPANDED)
    second = _fake(source, _graph(tmp_path)).search(_query(), CapsuleLevel.EXPANDED)

    assert [s.model_dump() for s in first.sections] == [s.model_dump() for s in second.sections]


# --- types --------------------------------------------------------------------


def test_types_section_is_not_emitted_because_index_v1_has_no_type_data(
    source: Path, tmp_path: Path
):
    """index-v1은 symbols·calls만 낸다. 데이터원 없이 섹션을 내면 지어낸 값이다.

    채우려면 인덱서에 struct·class·typedef 수집을 넣고 `INDEX_VERSION`을 올려야 한다 —
    index-v2가 그 표를 냈지만 캡슐로는 싣지 않는다(아래 v2 절).
    """
    assert "types" not in GRAPH_SECTION_KINDS
    for level in CapsuleLevel:
        capsule = _fake(source, _graph(tmp_path)).search(_query(), level)
        assert _section(capsule, "types") is None


def test_graph_section_kind_that_does_not_exist_is_a_programming_error(tmp_path: Path):
    """오타로 부른 섹션이 조용히 `None`을 내면 "그래프가 없다"로 읽힌다."""
    with pytest.raises(ValueError, match="그래프 섹션이 아니다"):
        _graph(tmp_path).names_for("types", _file_target("parser.c"))


# --- 훅과 trace ----------------------------------------------------------------


def test_unresolved_counts_reach_the_hook_exactly_once_per_run(tmp_path: Path):
    """후보 수에 비례해 실리면 같은 값이 중복돼 재현성 비교가 지저분해진다."""
    seen: list[IndexSummary] = []
    graph = _graph(tmp_path, on_index=seen.append)

    graph.names_for("callers", _file_target("parser.c"))
    graph.names_for("callees", _file_target("parser.c"))
    graph.names_for("callers", _file_target("main.c"))

    assert len(seen) == 1
    assert seen[0].status == STATUS_LOADED
    assert (seen[0].unresolved_calls, seen[0].unresolved_symbols) == (4, 2)


def test_index_summary_trace_fields_are_the_metric_column_names():
    """필드 이름이 곧 지표의 열 이름이다. 어댑터에서 이름이 바뀌면 집계가 조용히 끊긴다."""
    fields = IndexSummary(status=STATUS_LOADED).as_trace_fields()

    assert set(fields) == {
        "status",
        "files",
        "skipped",
        "symbols",
        "calls",
        "unresolved_calls",
        "unresolved_symbols",
        "parse_errors",
    }


# --- 프롬프트로 가는 경로 (W1·W4) -----------------------------------------------
#
# 여기까지가 이 모듈의 존재 이유다. 캡슐이 실측 그래프를 담아도 **모델이 그것을 못 보면**
# "못 봐서 못 찾은 건지 봤는데 못 찾은 건지"가 구별되지 않고, 그 상태의 성능 수치는
# 하네스가 아니라 Fake 어댑터를 잰 값이다.


def _rendered_body(source_root: Path, artifacts_root: Path, role: Role = Role.SCOUT) -> str:
    """실제 노드와 **같은 함수들**로 프롬프트 body를 만든다.

    노드를 부르지 않고 여기서 조립하는 이유는 `RunContext` 없이도 렌더 결과를 볼 수 있어야
    PR 본문에 붙일 덤프를 뽑기 쉽기 때문이다. 쓰는 함수(`build_facts`·`describe_sections`)는
    `scout._ask_model`·`auditor._ask_model`이 부르는 것과 같다.
    """
    capsule = _fake(source_root, _graph(artifacts_root)).search(_query(), CapsuleLevel.EXPANDED)
    template = load_prompt(role, "v1")
    fields = {
        "target": describe_target(capsule.target),
        "capsule_sections": describe_sections(capsule),
        "source": "void parse_header(void) { helper(); }",
    }
    if role is Role.AUDITOR:
        fields["candidate"] = "cand_0001 · 추정 CWE: CWE-787 · 트리아지 신호: (없음)"
    return template.render(
        facts=build_facts(
            run_id=RUN_ID,
            ids={"candidate_id": "cand_0001"},
            artifact_id="art_0001",
            target=capsule.target,
            capsule=capsule,
            source=fields["source"],
            callers=auditor.caller_names(capsule),
        ),
        **fields,
    ).body


def test_capsule_carries_the_lines_it_counted(source: Path, tmp_path: Path):
    """섹션이 토큰만 세고 줄을 버리면 프롬프트에 실을 것이 없다 (W1)."""
    capsule = _fake(source, _graph(tmp_path)).search(_query(), CapsuleLevel.EXPANDED)

    callers, callees = _section(capsule, "callers"), _section(capsule, "callees")

    assert callers.body == [_CALLER_ENTRY]
    assert callees.body == [_CALLEE_ENTRY]
    # 스키마 검증기가 강제하는 것을 여기서도 확인한다 — 회계와 내용은 같은 값이어야 한다.
    assert callers.items == len(callers.body)
    assert callers.tokens == sum(estimate_tokens(line) for line in callers.body)


def test_rendered_prompt_actually_carries_the_graph_section_bodies(source: Path, tmp_path: Path):
    """**W1이 됐다는 유일한 증거.** 렌더된 프롬프트 안에 호출 지점이 글자로 들어 있다.

    두 자리 모두 본다: 산문(`{{CAPSULE_SECTIONS}}`)과 사실 블록(`{{FACTS}}`). body.md가
    "아래 JSON이 참조 가능한 사실 전부다"라고 못박으므로 **산문에만 있으면 모델에게
    무시하라고 지시한 자리에 넣은 것이 된다** — `{{SOURCE}}`가 양쪽에 실리는 것과 같은
    이유다.
    """
    for role in (Role.SCOUT, Role.AUDITOR):
        body = _rendered_body(source, tmp_path, role)
        prose, facts = body.split("```json")

        for entry in (_CALLER_ENTRY, _CALLEE_ENTRY):
            assert entry in prose, f"{role.value}: 산문에 {entry!r}가 없다"
            assert entry in facts, f"{role.value}: 사실 블록에 {entry!r}가 없다"


def test_prompt_dump(source: Path, tmp_path: Path, capsys):
    """`pytest -s tests/test_index_graph.py::test_prompt_dump`로 실물을 눈으로 본다.

    단언 없이 출력만 하는 테스트가 아니다 — 덤프에 그래프 섹션이 없으면 실패한다.
    PR 본문에 붙이는 출력이 **검사된 출력**이어야 나중에 조용히 비어도 알 수 있다.
    """
    body = _rendered_body(source, tmp_path, Role.SCOUT)
    with capsys.disabled():
        print("\n===== scout/v1 body (index.json 있음) =====")
        print(body)
        print("===== end =====")
    assert _CALLER_ENTRY in body


def test_callers_fact_is_derived_from_the_capsule_not_from_a_second_source(
    source: Path, tmp_path: Path
):
    """`callers` 사실은 캡슐의 `callers` 섹션에서 나온다 (W4).

    예전에는 러너가 `auditor.run(callers=...)`을 안 넘겨 **항상 빈 튜플**이었고,
    `FakeModelClient`의 `context.callers[0]` 분기가 죽은 코드였다.
    """
    capsule = _fake(source, _graph(tmp_path)).search(_query(), CapsuleLevel.EXPANDED)

    assert auditor.caller_names(capsule) == (name_of(_CALLER_ENTRY),)
    # 그래프가 없으면 지어내지 않는다.
    empty = _fake(source, _graph(tmp_path / "none", document=None)).search(
        _query(), CapsuleLevel.EXPANDED
    )
    assert auditor.caller_names(empty) == ()


def test_auditor_section_name_matches_the_adapter():
    """노드는 어댑터를 import 할 수 없어 문자열이 양쪽에 따로 있다. 갈리면 사실이 빈다."""
    assert auditor.CALLERS_SECTION in GRAPH_SECTION_KINDS


def test_both_retrievers_emit_identical_graph_sections(source: Path, tmp_path: Path):
    """두 구현의 그래프 분기가 갈리면 `dev_fake`↔`dev_docker` 비교가 성립하지 않는다.

    예전에는 이 분기가 양쪽에 복사돼 있었고 두 docstring이 서로에게 "갈리면 안 된다"고
    부탁하고 있었다. 지금은 `index_graph.graph_section()` 한 함수를 부르므로 갈릴 자리가
    없는데, 그 사실을 여기서 고정한다 — 다음 사람이 다시 복사하면 여기서 깨진다.
    """
    for level in (CapsuleLevel.EXPANDED, CapsuleLevel.FOCUSED):
        fake = _fake(source, _graph(tmp_path)).search(_query(), level)
        hybrid = _hybrid(source, _graph(tmp_path)).search(_query(), level)

        def graph_only(capsule):  # noqa: ANN001, ANN202
            return [s.model_dump() for s in capsule.sections if s.kind in GRAPH_SECTION_KINDS]

        assert graph_only(fake) == graph_only(hybrid), f"{level.value}에서 갈렸다"

# --- 심볼 단위 target: 본문 판정이 줄 범위로 바뀐다 (W2) --------------------------


def test_symbol_target_keeps_the_sibling_call_that_a_file_target_hides(tmp_path: Path):
    """**이 대비가 W2의 핵심이다.**

    `helper`는 parser.c에 정의돼 있어 파일 단위 target에서는 "본문에 이미 있다"로 빠진다.
    심볼 단위 target에서 본문은 `parse_header`의 줄 범위(9~22)뿐이고 `helper`의 정의는
    3~6줄이라 **본문 밖**이다 — 그때도 빼면 실재하는 간선을 숨긴다.
    """
    graph = _graph(tmp_path)

    file_callees = graph.names_for("callees", _file_target("parser.c"))
    symbol_callees = graph.names_for("callees", _PARSE_HEADER)

    assert file_callees == [_CALLEE_ENTRY]
    assert symbol_callees == ["helper parser.c:18", _CALLEE_ENTRY]


def test_symbol_target_still_drops_self_recursion(tmp_path: Path):
    """자기 자신으로 돌아오는 호출은 본문 안이다. 규칙이 느슨해진 것이 아니다."""
    document = {
        **_INDEX_V1,
        "calls": [
            *_INDEX_V1["calls"],
            {"caller": "parse_header", "callee": "parse_header", "file": "parser.c", "line": 15},
        ],
    }

    callers = _graph(tmp_path, document).names_for("callers", _PARSE_HEADER)

    assert callers == [_CALLER_ENTRY]


def test_symbol_target_selects_only_edges_into_that_symbol(tmp_path: Path):
    """`helper`를 target으로 잡으면 `parse_header`로 들어오는 호출은 이 캡슐 것이 아니다."""
    helper = Location(file="parser.c", symbol="helper", line_start=3, line_end=6)

    assert _graph(tmp_path).names_for("callers", helper) == ["parse_header parser.c:18"]
    # helper 본문(3~6줄)에서 나가는 호출은 인덱스에 없다. "봤는데 0건"이다.
    assert _graph(tmp_path).names_for("callees", helper) == []


# --- symbols_for: 후보 목록의 데이터원 (W2) --------------------------------------


def test_symbols_for_returns_one_location_per_symbol_in_index_order(tmp_path: Path):
    """후보 순서가 곧 ID 발급 순서다. index.json의 (file, line_start, name) 순서를 따른다."""
    locations = _graph(tmp_path).symbols_for("parser.c")

    assert [(loc.symbol, loc.line_start, loc.line_end) for loc in locations] == [
        ("helper", 3, 6),
        ("parse_header", 9, 22),
    ]


def test_symbols_for_separates_no_index_from_no_symbols(tmp_path: Path):
    """`None`("인덱서가 안 돌았다")과 `[]`("봤는데 심볼 0건")를 섞지 않는다.

    러너가 이 둘에 다르게 대응한다 — 앞은 파일 단위 폴백, 뒤는 후보를 만들지 않는다.
    섞이면 `.py`처럼 인덱서에 안 넘어가는 파일과 인덱서가 죽은 run이 같은 모양이 된다.
    """
    assert _graph(tmp_path, document=None).symbols_for("parser.c") is None
    assert _graph(tmp_path).symbols_for("header.h") == []


def test_symbol_capsule_carries_only_that_symbols_source(source: Path, tmp_path: Path):
    """심볼 단위 캡슐의 `target_function`이 파일 전체가 아니다 — W2가 고치려던 것이다."""
    (source / "big.c").write_text(
        "\n".join(f"void fn_{i}(void) {{ }}" for i in range(200)), encoding="utf-8"
    )
    target = Location(file="big.c", symbol="fn_1", line_start=2, line_end=2)
    query = Query(run_id=RUN_ID, target=target, keywords=[], budget_tokens=4096)

    capsule = _fake(source, _graph(tmp_path)).search(query, CapsuleLevel.LIGHT)
    whole_file = _fake(source, _graph(tmp_path)).search(
        Query(run_id=RUN_ID, target=_file_target("big.c"), keywords=[], budget_tokens=4096),
        CapsuleLevel.LIGHT,
    )

    assert _section(capsule, "target_function").tokens < _section(
        whole_file, "target_function"
    ).tokens


# --- D20: LIGHT 가 그래프를 싣는다 -----------------------------------------------


def test_light_capsule_carries_the_call_graph(source: Path, tmp_path: Path):
    """D20을 (a)로 확정했다. scout이 그래프를 얻을 경로가 구조적으로 0개이던 것을 연다."""
    capsule = _fake(source, _graph(tmp_path)).search(_query(), CapsuleLevel.LIGHT)

    assert [s.kind for s in capsule.sections if s.kind in GRAPH_SECTION_KINDS] == [
        "callers",
        "callees",
    ]


def test_both_retrievers_put_the_graph_at_the_same_level(source: Path, tmp_path: Path):
    """두 구현이 갈리면 같은 index.json에서 다른 캡슐이 나와 조건 비교가 오염된다."""
    fake = _fake(source, _graph(tmp_path)).search(_query(), CapsuleLevel.LIGHT)
    hybrid = _hybrid(source, _graph(tmp_path)).search(_query(), CapsuleLevel.LIGHT)

    graph_kinds = [
        [s.kind for s in capsule.sections if s.kind in GRAPH_SECTION_KINDS]
        for capsule in (fake, hybrid)
    ]
    assert graph_kinds[0] == graph_kinds[1] == ["callers", "callees"]


# --- index-v2 (#63): 조회 표를 더해도 캡슐은 그대로다 -------------------------------
#
# v2는 v1 필드를 그대로 두고 NE resolver가 쓸 조회 표를 더한다. `calls`만 모양이 바뀐다 —
# 간선 목록이 callee → 호출 지점(+ 인자)으로 묶였다. 아래 `_INDEX_V2`는 `_INDEX_V1`과 **같은
# 심볼·같은 간선**을 v2 모양으로 적은 쌍둥이다. 쌍둥이 관계는 손으로 적은 데이터다 — 변환 함수로
# 만들면 읽는 쪽과 같은 오류를 공유해 대조가 동어반복이 된다.
_INDEX_V2 = {
    **{key: value for key, value in _INDEX_V1.items() if key != "calls"},
    "version": "index-v2",
    "calls": {
        "parse_header": [{"file": "main.c", "line": 12, "caller": "main", "args": ["buf"]}],
        "helper": [{"file": "parser.c", "line": 18, "caller": "parse_header", "args": []}],
        "memcpy": [
            {"file": "parser.c", "line": 20, "caller": "parse_header", "args": ["d", "s", "n"]}
        ],
    },
    "functions": {
        "main": [{"file": "main.c", "line_start": 5, "line_end": 15, "params": []}],
        "helper": [{"file": "parser.c", "line_start": 3, "line_end": 6, "params": []}],
        "parse_header": [
            {"file": "parser.c", "line_start": 9, "line_end": 22, "params": ["src"]}
        ],
    },
    # 캡슐로 새면 눈에 띄도록 표마다 실제 항목을 둔다 — 빈 표면 「안 샜다」가 공허하다.
    "macros": {"HDR_LEN": [{"file": "parser.c", "line_start": 1, "line_end": 1}]},
    "types": {
        "header": [{"file": "parser.c", "line_start": 2, "line_end": 2, "kind": "struct"}]
    },
    "file_vars": {"g_hdr": [{"file": "main.c", "line_start": 3, "line_end": 3}]},
    "fields": {"len": [{"type": "header", "file": "parser.c", "line_start": 2, "line_end": 2}]},
    "locals": {"parser.c": {"parse_header": {"src": [9], "buf": [10]}}},
    "assigns": {"parser.c": {"parse_header": {"n": [11]}}},
    "field_assigns": {
        "len": [
            {"file": "main.c", "line": 13, "func": "main", "lhs": "h->len", "rhs": "n", "op": "="}
        ]
    },
}


def _v1_v2(tmp_path: Path) -> tuple[GraphIndex, GraphIndex]:
    return _graph(tmp_path / "v1"), _graph(tmp_path / "v2", _INDEX_V2)


def test_the_reader_still_accepts_index_v1():
    """v1 문서는 계속 읽힌다 — 이미지가 v1인 run의 산출물이 남아 있다."""
    assert set(SUPPORTED_INDEX_VERSIONS) == {"index-v1", "index-v2"}


def test_v2_calls_give_the_same_graph_edges_as_the_v1_list(tmp_path: Path):
    """**캡슐 그래프 섹션의 데이터원이 v1 목록에서 v2 묶음으로 바뀌어도 간선이 같다.**

    파일·심볼·슬라이스 본문 세 종류의 본문에서 callers·callees 둘 다 대조한다. 비교가 공허하지
    않도록 간선이 실제로 나오는 자리가 섞여 있다(마지막 단언).
    """
    v1, v2 = _v1_v2(tmp_path)
    targets = [
        (_file_target("parser.c"), None),
        (_file_target("main.c"), None),
        (_PARSE_HEADER, None),
        (Location(file="parser.c", symbol="helper", line_start=3, line_end=6), None),
        (
            Location(file="parser.c", line_start=20),
            frozenset({("parser.c", 12), ("parser.c", 18), ("parser.c", 20)}),
        ),
        (Location(file="main.c", line_start=12), frozenset({("main.c", 12), ("parser.c", 20)})),
    ]

    for kind in GRAPH_SECTION_KINDS:
        for target, body in targets:
            assert v2.names_for(kind, target, body) == v1.names_for(kind, target, body), (
                kind,
                target,
                body,
            )
    assert v2.summary == v1.summary
    assert v2.names_for("callers", _PARSE_HEADER) == [_CALLER_ENTRY]


def test_v2_tables_never_reach_the_capsule(source: Path, tmp_path: Path):
    """**조회 표는 `table()`로만 나간다 — 캡슐 섹션을 키우지 않는다.**

    Scout 프롬프트가 서빙 창을 넘는 결함이 열려 있고(그 거절된 프롬프트에서 Capsule이 8,621자를
    먹었다) 표는 NE resolver가 **요청받았을 때** 줄 것이다. 두 Retriever · 모든 레벨 · 파일/심볼
    대상에서 v2 문서의 캡슐이 v1 쌍둥이의 캡슐과 **바이트까지 같다**(`model_dump` — 섹션의 줄
    본문을 포함한다).

    이 테스트가 무는 것은 캡슐이다. 노드가 훅으로 표를 따로 읽어 프롬프트에 붙이는 경로는 여기서
    안 잡힌다 — 그런 훅은 지금 0개다(`table()`을 부르는 곳이 `src/`에 없다).
    """
    queries = [
        _query(),
        _query("main.c"),
        Query(run_id=RUN_ID, target=_PARSE_HEADER, keywords=[], budget_tokens=4096),
    ]
    for make in (_fake, _hybrid):
        for level in CapsuleLevel:
            for query in queries:
                one = make(source, _graph(tmp_path / "v1")).search(query, level)
                two = make(source, _graph(tmp_path / "v2", _INDEX_V2)).search(query, level)
                assert [s.model_dump() for s in two.sections] == [
                    s.model_dump() for s in one.sections
                ], (make.__name__, level, query.target)
    assert GRAPH_SECTION_KINDS == ("callers", "callees")


def test_table_is_none_when_the_index_has_no_v2_tables(tmp_path: Path):
    """`None`은 **v2 표가 없다**는 뜻이다 — 인덱스가 없거나, 깨졌거나, 표가 없는 형식(v1)이다.

    v1 문서에서 `{}`를 내면 「표를 봤는데 0건」과 섞인다. NE resolver가 그 둘에 다르게 대응해야
    한다 — 앞은 「인덱스가 v2가 아니다」를 기록하고, 뒤는 「못 찾았다」를 모델에게 알린다.
    """
    assert _graph(tmp_path / "none", document=None).table("macros") is None
    assert _graph(tmp_path / "v1").table("macros") is None
    assert _graph(tmp_path / "broken", "{ not json").table("macros") is None


def test_an_empty_v2_table_is_zero_rows_not_missing(tmp_path: Path):
    """v2인데 그 표가 비었으면 빈 매핑이다 — `None`이 아니다(바로 위 테스트와 짝)."""
    graph = _graph(tmp_path, {**_INDEX_V2, "macros": {}})

    assert graph.table("macros") == {}
    assert graph.table("macros") is not None


def test_table_returns_the_rows_read_only(tmp_path: Path):
    """조회 표는 run 동안 모든 캡슐·후보가 공유한다. 호출자가 고치면 다음 조회가 오염된다."""
    macros = _graph(tmp_path, _INDEX_V2).table("macros")

    assert macros["HDR_LEN"][0]["line_start"] == 1
    with pytest.raises(TypeError):
        macros["NEW"] = []
    with pytest.raises(TypeError):
        macros["HDR_LEN"][0]["line_start"] = 99


def test_every_v2_table_is_reachable(tmp_path: Path):
    """`V2_TABLES`의 이름은 전부 조회된다.

    목록과 읽기가 어긋나면 조회가 `KeyError`로 죽는다."""
    graph = _graph(tmp_path, _INDEX_V2)

    assert {name: bool(graph.table(name)) for name in V2_TABLES} == dict.fromkeys(V2_TABLES, True)


def test_an_unknown_table_name_is_a_programming_error(tmp_path: Path):
    """오타로 부른 표가 조용히 `None`을 내면 「v2 표가 없다」로 읽힌다.

    `names_for`가 모르는 kind를 거절하는 것과 같다."""
    with pytest.raises(ValueError, match="조회 표가 아니다"):
        _graph(tmp_path, _INDEX_V2).table("src")


def _string_line_numbers():
    """기록이 든 표마다 줄 칸 하나를 `"12"`로 바꾼 문서.

    표·칸은 **픽스처에서 센다** — 소스의 키 목록에서 세면 읽는 쪽이 빠뜨린 칸을 이 목록도
    같이 빠뜨린다. `"12"`는 v1 읽기의 `int()`를 통과하는 모양이다."""
    for table, rows in _INDEX_V2.items():
        if not isinstance(rows, dict) or table in ("locals", "assigns"):
            continue
        name, records = next(iter(rows.items()))
        for key in records[0]:
            if key.startswith("line"):
                row = {**records[0], key: "12"}
                yield pytest.param(
                    {**_INDEX_V2, table: {**rows, name: [row, *records[1:]]}},
                    id=f"{table}.{key}-is-a-string",
                )


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param({**_INDEX_V2, "macros": []}, id="table-not-a-mapping"),
        pytest.param(
            {key: value for key, value in _INDEX_V2.items() if key != "fields"}, id="table-missing"
        ),
        pytest.param(
            {**_INDEX_V2, "macros": {"HDR_LEN": [{"file": "parser.c", "line_start": 1}]}},
            id="row-missing-a-key",
        ),
        pytest.param({**_INDEX_V2, "locals": {"parser.c": ["buf"]}}, id="nested-not-a-mapping"),
        pytest.param({**_INDEX_V2, "calls": _INDEX_V1["calls"]}, id="v2-with-v1-calls"),
        pytest.param({**_INDEX_V1, "calls": _INDEX_V2["calls"]}, id="v1-with-v2-calls"),
        *_string_line_numbers(),
        pytest.param(
            {
                **_INDEX_V2,
                "macros": {"HDR_LEN": [{"file": "parser.c", "line_start": True, "line_end": 1}]},
            },
            id="record-line-is-a-bool",
        ),
        pytest.param(
            {**_INDEX_V2, "locals": {"parser.c": {"parse_header": {"src": [True]}}}},
            id="nested-line-is-a-bool",
        ),
    ],
)
def test_a_misshapen_document_is_refused_whole(broken: dict, tmp_path: Path):
    """버전은 맞는데 모양이 다르면 **통째로** 거절한다 — v1이 필드 누락을 거절하는 것과 같다.

    절반만 읽힌 표가 남으면 어디까지 믿을지 알 수 없다. 그래프도 표도 후보도 함께 내려간다.

    **줄 번호가 정수가 아닌 것도 모양이 다른 것이다**(PR #295 리뷰 LOW-1). `table()`은 값을
    고치지 않고 내주므로 키만 보면 `"12"`가 resolver까지 가서, 줄 범위를 세는 자리에서 죽거나
    정수와의 비교가 늘 거짓이라 「못 찾았다」가 된다. JSON `true`도 막는다 — `bool`이 `int`의
    하위형이라 `isinstance`로 보면 통과한다.
    """
    graph = _graph(tmp_path, broken)

    assert graph.summary.status == STATUS_MALFORMED
    assert graph.names_for("callers", _PARSE_HEADER) is None
    assert graph.symbols_for("parser.c") is None
    assert graph.table("macros") is None


def test_neighbor_files_joins_callers_and_callee_definitions(tmp_path: Path) -> None:
    """D93 (#336): 호출자 파일 + 본문 안 호출의 피호출자 정의 파일. 대상 파일 자신은 뺀다.

    `parse_header`(parser.c)를 main.c의 `main`이 부르고, 본문 안에서 `helper`(parser.c — 자기
    파일이라 빠짐)와 `memcpy`(정의 없음 — 파일 없음)를 부른다.
    """
    assert _graph(tmp_path).neighbor_files(_PARSE_HEADER) == ["main.c"]


def test_neighbor_files_is_none_without_a_graph(tmp_path: Path) -> None:
    assert _graph(tmp_path, None).neighbor_files(_PARSE_HEADER) is None


_CALLEE_INDEX = {
    "version": "index-v1",
    "files": ["a.c", "b.c", "c.c"],
    "skipped": [],
    "symbols": [
        {"name": "entry", "file": "a.c", "line_start": 1, "line_end": 10},
        {"name": "helper", "file": "a.c", "line_start": 12, "line_end": 15},
        {"name": "helper", "file": "b.c", "line_start": 1, "line_end": 3},
        {"name": "only_b", "file": "b.c", "line_start": 5, "line_end": 8},
        {"name": "twice", "file": "b.c", "line_start": 10, "line_end": 12},
        {"name": "twice", "file": "c.c", "line_start": 1, "line_end": 3},
        {"name": "outside", "file": "c.c", "line_start": 5, "line_end": 6},
    ],
    "calls": [
        {"caller": "entry", "callee": "only_b", "file": "a.c", "line": 3},
        {"caller": "entry", "callee": "helper", "file": "a.c", "line": 5},
        {"caller": "entry", "callee": "twice", "file": "a.c", "line": 6},
        {"caller": "entry", "callee": "entry", "file": "a.c", "line": 7},
        {"caller": "entry", "callee": "memcpy", "file": "a.c", "line": 8},
        {"caller": "helper", "callee": "outside", "file": "a.c", "line": 13},
    ],
    "unresolved_calls": 0,
    "unresolved_symbols": 0,
    "parse_errors": [],
}


def test_callees_of_resolves_calls_in_the_body_to_definitions(tmp_path: Path) -> None:
    """#349 (a): 본문 안 호출 → 정의. 같은 파일 정의가 이기고(helper → a.c), 다른 파일은 정의가
    하나뿐일 때만(only_b), 여럿이면 싣지 않는다(twice). 재귀·정의 없는 이름(memcpy)·본문 밖 호출
    (helper 안의 outside)은 빠진다. 순서는 본문 안 첫 호출 줄 순이다."""
    target = Location(file="a.c", symbol="entry", line_start=1, line_end=10)

    found = _graph(tmp_path, _CALLEE_INDEX).callees_of(target)

    assert found == [
        Location(file="b.c", symbol="only_b", line_start=5, line_end=8),
        Location(file="a.c", symbol="helper", line_start=12, line_end=15),
    ]


def test_callees_of_is_none_without_a_graph(tmp_path: Path) -> None:
    target = Location(file="a.c", symbol="entry", line_start=1, line_end=10)
    assert _graph(tmp_path, None).callees_of(target) is None


def test_definitions_of_finds_macros_types_and_file_vars(tmp_path: Path) -> None:
    """#349 (f): 이름 → 정의 위치. 같은 파일 우선, 다른 파일은 하나뿐일 때만, 모르는 이름은 빠진다.
    순서는 (파일, 시작 줄)이다."""
    found = _graph(tmp_path, _INDEX_V2).definitions_of(
        ["header", "HDR_LEN", "g_hdr", "nope"], "parser.c"
    )

    assert found == [
        Location(file="main.c", symbol="g_hdr", line_start=3, line_end=3),
        Location(file="parser.c", symbol="HDR_LEN", line_start=1, line_end=1),
        Location(file="parser.c", symbol="header", line_start=2, line_end=2),
    ]


def test_definitions_of_skips_an_ambiguous_name_and_prefers_the_near_file(tmp_path: Path) -> None:
    twice = {
        **_INDEX_V2,
        "macros": {
            "HDR_LEN": [
                {"file": "a.h", "line_start": 1, "line_end": 1},
                {"file": "b.h", "line_start": 1, "line_end": 1},
            ],
            "MAX": [
                {"file": "parser.c", "line_start": 5, "line_end": 5},
                {"file": "main.c", "line_start": 7, "line_end": 7},
            ],
        },
    }

    found = _graph(tmp_path, twice).definitions_of(["HDR_LEN", "MAX"], "parser.c")

    assert found == [Location(file="parser.c", symbol="MAX", line_start=5, line_end=5)]


def test_definitions_of_is_none_without_v2_tables(tmp_path: Path) -> None:
    assert _graph(tmp_path).definitions_of(["header"], "parser.c") is None


def test_call_sites_of_finds_calls_into_the_target(tmp_path: Path) -> None:
    """#349 (b): 대상 함수를 부르는 호출 지점. 다른 파일의 호출은 대상 이름의 정의가 하나뿐일
    때만(only_b ← a.c:3), 여럿이면 같은 파일 호출만(helper ← a.c:5 — b.c에도 helper가 있다),
    같은 이름이 여러 파일이면 다른 파일 호출은 빠진다(twice ← a.c:6). 재귀(entry ← a.c:7)는
    본문 안이라 빠진다."""
    graph = _graph(tmp_path, _CALLEE_INDEX)

    def sites(file: str, name: str, start: int, end: int) -> list[Location] | None:
        return graph.call_sites_of(Location(file=file, symbol=name, line_start=start, line_end=end))

    assert sites("b.c", "only_b", 5, 8) == [
        Location(file="a.c", symbol="entry", line_start=3, line_end=3)
    ]
    assert sites("a.c", "helper", 12, 15) == [
        Location(file="a.c", symbol="entry", line_start=5, line_end=5)
    ]
    assert sites("b.c", "helper", 1, 3) == []
    assert sites("c.c", "twice", 1, 3) == []
    assert sites("a.c", "entry", 1, 10) == []


def test_call_sites_of_is_none_without_a_graph(tmp_path: Path) -> None:
    target = Location(file="a.c", symbol="entry", line_start=1, line_end=10)
    assert _graph(tmp_path, None).call_sites_of(target) is None



def test_call_sites_of_orders_same_file_then_same_directory_then_the_rest(
    tmp_path: Path,
) -> None:
    """읽는 쪽이 앞 몇 개만 쓰므로 가까운 호출자가 먼저다(#351 리뷰 — 경로 알파벳순이던 것)."""
    doc = {
        "version": "index-v1",
        "files": ["lib/x.c", "lib/y.c", "a/b.c", "z.c"],
        "skipped": [],
        "symbols": [{"name": "t", "file": "lib/x.c", "line_start": 1, "line_end": 3}],
        "calls": [
            {"caller": "m", "callee": "t", "file": "a/b.c", "line": 4},
            {"caller": "m", "callee": "t", "file": "z.c", "line": 2},
            {"caller": "m", "callee": "t", "file": "lib/y.c", "line": 9},
            {"caller": "m", "callee": "t", "file": "lib/x.c", "line": 20},
        ],
        "unresolved_calls": 0,
        "unresolved_symbols": 0,
        "parse_errors": [],
    }
    target = Location(file="lib/x.c", symbol="t", line_start=1, line_end=3)

    found = _graph(tmp_path, doc).call_sites_of(target)

    assert [loc.file for loc in found] == ["lib/x.c", "lib/y.c", "a/b.c", "z.c"]


def test_callees_of_orders_by_the_first_call_even_when_rows_come_later(tmp_path: Path) -> None:
    """같은 callee의 호출이 여럿이면 가장 이른 줄로 정렬한다 — 행 순서가 아니다(`min` 분기)."""
    doc = {
        **_CALLEE_INDEX,
        "calls": [
            {"caller": "entry", "callee": "only_b", "file": "a.c", "line": 8},
            {"caller": "entry", "callee": "helper", "file": "a.c", "line": 5},
            {"caller": "entry", "callee": "only_b", "file": "a.c", "line": 2},
        ],
    }
    target = Location(file="a.c", symbol="entry", line_start=1, line_end=10)

    found = _graph(tmp_path, doc).callees_of(target)

    assert [loc.symbol for loc in found] == ["only_b", "helper"]


def test_definitions_of_takes_the_first_table_that_has_the_name(tmp_path: Path) -> None:
    """같은 이름이 매크로와 타입 둘 다에 있으면 `DEFINITION_TABLES` 순서(매크로 먼저)로 정한다."""
    doc = {
        **_INDEX_V2,
        "macros": {"BOTH": [{"file": "parser.c", "line_start": 1, "line_end": 1}]},
        "types": {
            "BOTH": [{"file": "parser.c", "line_start": 7, "line_end": 9, "kind": "struct"}]
        },
    }

    found = _graph(tmp_path, doc).definitions_of(["BOTH"], "parser.c")

    assert found == [Location(file="parser.c", symbol="BOTH", line_start=1, line_end=1)]



def test_call_sites_of_counts_definitions_not_files(tmp_path: Path) -> None:
    """정의가 한 파일 안에 둘(`#if` 양쪽 가지)이어도 둘이다 — 다른 파일의 호출은 어느 정의로
    가는지 모르므로 안 싣는다. 같은 파일 호출은 싣는다(#351 자체 검토 LOW-2: 파일 수로 세던 것)."""
    doc = {
        "version": "index-v1",
        "files": ["a.c", "b.c"],
        "skipped": [],
        "symbols": [
            {"name": "foo", "file": "a.c", "line_start": 1, "line_end": 3},
            {"name": "foo", "file": "a.c", "line_start": 6, "line_end": 8},
        ],
        "calls": [
            {"caller": "m", "callee": "foo", "file": "b.c", "line": 4},
            {"caller": "n", "callee": "foo", "file": "a.c", "line": 20},
        ],
        "unresolved_calls": 0,
        "unresolved_symbols": 0,
        "parse_errors": [],
    }
    target = Location(file="a.c", symbol="foo", line_start=1, line_end=3)

    found = _graph(tmp_path, doc).call_sites_of(target)

    assert [loc.file for loc in found] == ["a.c"]


# --- stores_of: Auditor `STORES` 블록의 쓰기 지점 ------------------------------------------------
#
# 표의 열은 **진짜 인덱서가 쓴 값**에서 옮겼다(`tests/test_source_stores.py`가 같은 원문을 인덱서로
# 직접 돌려 대조한다). 다만 `copy`의 지역 `g_len`에 대한 대입(`assigns` 24줄)은 이 표에서 더했다 —
# 지역이 같은 이름의 전역을 가리는 자리를 고정하려는 것이다. 원문의 행 번호:
#
#   a.c  4  struct ctx { char *buf; size_t len; struct hdr { int len; } h; };
#   a.c  5  struct other { int len; };
#   a.c  7  static char *g_buf;       8  static size_t g_len = 0;
#   a.c 10-18 grow   12 c->len = n * 2;    13 c->buf = (char *)malloc(c->len + 1);
#                    14 g_buf = malloc(<줄바꿈> n + 1);    16 g_len += n;    17 c->h.len = 4;
#   a.c 20-27 copy   22 char *local = malloc(n);  23 size_t g_len = 3;  24 g_len = 0; local = NULL;
#                    25 memcpy(c->buf + c->len, src, n);
#   b.c  2  static size_t g_len;      3  extern char *g_buf;
#   b.c 4-9 other_fn 6 o->len = 5;    7  g_len = 7;    8  g_buf = calloc(1, 16);
_STORES_INDEX = {
    **_INDEX_V2,
    "files": ["a.c", "b.c"],
    "symbols": [
        {"name": "grow", "file": "a.c", "line_start": 10, "line_end": 18},
        {"name": "copy", "file": "a.c", "line_start": 20, "line_end": 27},
        {"name": "other_fn", "file": "b.c", "line_start": 4, "line_end": 9},
    ],
    "calls": {
        "calloc": [{"file": "b.c", "line": 8, "caller": "other_fn", "args": ["1", "16"]}],
        "malloc": [
            {"file": "a.c", "line": 13, "caller": "grow", "args": ["c->len + 1"]},
            {"file": "a.c", "line": 14, "caller": "grow", "args": ["n + 1"]},
            {"file": "a.c", "line": 22, "caller": "copy", "args": ["n"]},
        ],
        "memcpy": [
            {"file": "a.c", "line": 25, "caller": "copy", "args": ["c->buf + c->len", "src", "n"]}
        ],
    },
    "functions": {
        "grow": [{"file": "a.c", "line_start": 10, "line_end": 18, "params": ["c", "n"]}],
        "copy": [{"file": "a.c", "line_start": 20, "line_end": 27, "params": ["c", "src", "n"]}],
        "other_fn": [{"file": "b.c", "line_start": 4, "line_end": 9, "params": ["o"]}],
    },
    "file_vars": {
        "g_buf": [
            {"file": "a.c", "line_start": 7, "line_end": 7},
            {"file": "b.c", "line_start": 3, "line_end": 3},
        ],
        "g_len": [
            {"file": "a.c", "line_start": 8, "line_end": 8},
            {"file": "b.c", "line_start": 2, "line_end": 2},
        ],
    },
    "fields": {
        "buf": [{"type": "ctx", "file": "a.c", "line_start": 4, "line_end": 4}],
        "len": [
            {"type": "ctx", "file": "a.c", "line_start": 4, "line_end": 4},
            {"type": "hdr", "file": "a.c", "line_start": 4, "line_end": 4},
            {"type": "other", "file": "a.c", "line_start": 5, "line_end": 5},
        ],
    },
    "locals": {
        "a.c": {
            "grow": {"c": [10], "n": [10]},
            "copy": {"c": [20], "g_len": [23], "local": [22], "n": [20], "src": [20]},
        },
        "b.c": {"other_fn": {"o": [4]}},
    },
    "assigns": {
        "a.c": {
            "copy": {"g_len": [24], "local": [24]},
            "grow": {"g_buf": [14], "g_len": [16]},
        },
        "b.c": {"other_fn": {"g_buf": [8], "g_len": [7]}},
    },
    "field_assigns": {
        "buf": [
            {"file": "a.c", "line": 13, "func": "grow", "lhs": "c->buf",
             "rhs": "(char *)malloc(c->len + 1)", "op": "="}
        ],
        "len": [
            {"file": "b.c", "line": 6, "func": "other_fn", "lhs": "o->len", "rhs": "5", "op": "="},
            {"file": "a.c", "line": 17, "func": "grow", "lhs": "c->h.len", "rhs": "4", "op": "="},
            {"file": "a.c", "line": 12, "func": "grow", "lhs": "c->len", "rhs": "n * 2", "op": "="},
        ],
    },
}


def _stores(
    tmp_path: Path,
    name: str,
    *,
    field: bool = False,
    file: str = "a.c",
    line: int = 25,
    lhs: str | None = None,
    doc: object = _STORES_INDEX,
):  # noqa: ANN202
    return _graph(tmp_path, doc).stores_of(
        StoreTarget(name=name, field=field, file=file, line=line, lhs=lhs)
    )


def test_stores_of_finds_the_writes_of_a_field_by_its_name(tmp_path: Path) -> None:
    """`field_assigns`의 필드 이름 키 — 파일·줄·함수·좌변 철자·연산자. 순서는 (파일, 줄)이다(표의 행
    순서와 무관 — 여기선 b.c가 표 앞에 있다)."""
    found = _stores(tmp_path, "len", field=True, lhs="c->len")

    assert [(s.file, s.line, s.func, s.lhs, s.op, s.field) for s in found] == [
        ("a.c", 12, "grow", "c->len", "=", True),
        ("a.c", 17, "grow", "c->h.len", "=", True),
        ("b.c", 6, "other_fn", "o->len", "=", True),
    ]
    assert {s.name for s in found} == {"len"}


def test_stores_of_has_no_struct_type_to_key_a_field_write_by() -> None:
    """`field_assigns` 행에는 객체의 구조체 타입이 없다 — (구조체, 필드)로 짚을 열이 없으니 쓰기는
    이름으로만 이을 수 있다. 이 가정이 깨지면(인덱서가 타입 열을 더하면) 아래 모호 판정을 다시
    본다."""
    from src.adapters.index_graph import _RECORD_KEYS

    assert _RECORD_KEYS["field_assigns"] == ("file", "line", "func", "lhs", "rhs", "op")


def test_stores_of_marks_every_write_of_a_field_name_several_structs_declare(
    tmp_path: Path,
) -> None:
    """`len`은 ctx·hdr·other 셋이 선언한다 — 이름만으로는 어느 구조체의 쓰기인지 모른다. 찾는 줄과
    좌변 철자가 같은 쓰기(`c->len`)도 이름 키의 한 행일 뿐이라 **전부** 모호하다."""
    found = _stores(tmp_path, "len", field=True, lhs="c->len")

    assert [(s.line, s.ambiguous) for s in found] == [(12, True), (17, True), (6, True)]


def test_stores_of_does_not_mark_a_field_only_one_struct_declares(tmp_path: Path) -> None:
    """`buf`는 인덱스에서 ctx 하나(타입·파일 하나)만 선언한다 — 이름이 곧 (구조체, 필드) 키라 철자가
    달라도(`other->buf`를 찾는 줄) 모호하다고 안 한다."""
    (found,) = _stores(tmp_path, "buf", field=True, lhs="other->buf")

    assert (found.line, found.ambiguous) == (13, False)


def test_stores_of_marks_a_field_no_indexed_struct_declares(tmp_path: Path) -> None:
    """선언한 구조체가 인덱스에 없는 필드(시스템 헤더·C++ 클래스의 것)는 이름이 유일한 키다 —
    모호하다."""
    doc = {
        **_STORES_INDEX,
        "field_assigns": {
            "st_size": [
                {"file": "b.c", "line": 6, "func": "other_fn", "lhs": "sb.st_size", "rhs": "0",
                 "op": "="}
            ]
        },
    }

    (found,) = _stores(tmp_path, "st_size", field=True, doc=doc)

    assert found.ambiguous is True


def test_stores_of_marks_a_field_of_an_anonymous_struct_and_a_struct_declared_twice(
    tmp_path: Path,
) -> None:
    """선언이 하나여도 타입이 없거나(익명 구조체 — `type: null`), 같은 구조체 이름이 두 파일에 따로
    정의돼 있으면(파일 지역 구조체가 흔하다) 이름이 키가 못 된다."""
    write = {"file": "a.c", "line": 12, "func": "grow", "lhs": "c->w", "rhs": "1", "op": "="}
    anonymous = {
        **_STORES_INDEX,
        "fields": {"w": [{"type": None, "file": "a.c", "line_start": 4, "line_end": 4}]},
        "field_assigns": {"w": [write]},
    }
    twice = {
        **_STORES_INDEX,
        "fields": {
            "w": [
                {"type": "node", "file": "a.c", "line_start": 4, "line_end": 4},
                {"type": "node", "file": "b.c", "line_start": 4, "line_end": 4},
            ]
        },
        "field_assigns": {"w": [write]},
    }

    assert [s.ambiguous for s in _stores(tmp_path, "w", field=True, doc=anonymous)] == [True]
    assert [s.ambiguous for s in _stores(tmp_path / "t", "w", field=True, doc=twice)] == [True]


def _field_doc(declared: list[tuple[str, str]], writes: list[tuple[str, int]], name: str = "pos"):
    """`name` 필드의 인덱스 — `declared`는 (구조체 이름, 선언 파일), `writes`는 (쓰기 파일, 줄)."""
    return {
        **_STORES_INDEX,
        "fields": {
            name: [
                {"type": kind, "file": file, "line_start": 1, "line_end": 1}
                for kind, file in declared
            ]
        },
        "field_assigns": {
            name: [
                {"file": file, "line": line, "func": "f", "lhs": f"x->{name}", "rhs": "0",
                 "op": "="}
                for file, line in writes
            ]
        },
    }


def test_stores_of_flags_a_single_indexed_struct_that_lives_in_a_far_directory(
    tmp_path: Path,
) -> None:
    """libzip `lib/zip_algorithm_zstd.c`의 `ctx->in.pos` — 진짜 구조체는 인덱스 밖 헤더(`zstd.h`)에
    있고 인덱스에 든 `pos`는 **관계없는** ossfuzz의 구조체 하나뿐이다. 선언이 하나여도 그 파일이
    찾는 줄(lib/)과 같은 디렉터리가 아니면 같은 필드라고 못 한다 — 쓰기가 어디에 있든 전부
    모호하다."""
    doc = _field_doc(
        [("cursor", "ossfuzz/fuzz.c")],
        [("lib/zip_algorithm_zstd.c", 246), ("ossfuzz/fuzz.c", 4)],
    )

    found = _stores(tmp_path, "pos", field=True, file="lib/zip_algorithm_zstd.c", doc=doc)

    assert [(s.file, s.ambiguous) for s in found] == [
        ("lib/zip_algorithm_zstd.c", True),
        ("ossfuzz/fuzz.c", True),
    ]


def test_stores_of_does_not_flag_a_single_struct_declared_next_to_both_use_and_write(
    tmp_path: Path,
) -> None:
    """구조체가 `lib/zipint.h` 하나이고 찾는 줄(`lib/zip_close.c`)과 쓰기(`lib/zip_open.c`)가 같은
    디렉터리면 모호하다고 안 한다 — 같은 파일이어도 마찬가지다(같은 파일은 같은 디렉터리다)."""
    doc = _field_doc(
        [("zip", "lib/zipint.h")], [("lib/zip_open.c", 3), ("lib/zip_close.c", 9)], name="flags"
    )

    found = _stores(tmp_path, "flags", field=True, file="lib/zip_close.c", doc=doc)

    assert [(s.file, s.ambiguous) for s in found] == [
        ("lib/zip_close.c", False),
        ("lib/zip_open.c", False),
    ]


def test_stores_of_flags_a_single_struct_declared_near_only_the_write_or_only_the_use(
    tmp_path: Path,
) -> None:
    """「둘 다 볼 수 있다」는 **둘 다**다 — 헤더가 쓰기 쪽 디렉터리에만(또는 찾는 줄 쪽에만) 있으면
    다른 쪽은 그 구조체를 본다는 보장이 없다. 같은 선언·같은 쓰기 줄이어도 찾는 줄이 어디냐로
    갈린다."""
    doc = _field_doc([("zip", "lib/zipint.h")], [("lib/zip_open.c", 3), ("src/other.c", 8)])

    from_lib = _stores(tmp_path, "pos", field=True, file="lib/zip_close.c", doc=doc)
    from_src = _stores(tmp_path / "s", "pos", field=True, file="src/use.c", doc=doc)

    assert [(s.file, s.ambiguous) for s in from_lib] == [
        ("lib/zip_open.c", False),  # 선언·쓰기·찾는 줄이 모두 lib/
        ("src/other.c", True),  # 쓰기가 src/라 헤더를 본다는 보장이 없다
    ]
    assert [(s.file, s.ambiguous) for s in from_src] == [
        ("lib/zip_open.c", True),  # 찾는 줄(src/)이 헤더(lib/)를 본다는 보장이 없다
        ("src/other.c", True),
    ]


def test_stores_of_marks_nonproduct_writes_only_when_the_use_is_in_product_code(
    tmp_path: Path,
) -> None:
    """`is_nonproduct_path`(디렉터리 성분만 본다 — `lib/test_util.c`는 제품이다)로 비제품 쓰기를
    가른다. 찾는 줄이 제품 경로일 때만 단다 — 찾는 줄이 `examples/` 안이면 정의 조회의 강등과 같이
    안 민다."""
    writes = [
        ("examples/zran.c", 110), ("fuzz/fuzz_x.c", 3), ("regress/r.c", 4),
        ("lib/test_util.c", 5), ("inflate.c", 164),
    ]
    doc = _field_doc([("state", "inflate.c")], writes, name="len")

    product_use = _stores(tmp_path, "len", field=True, file="deflate.c", doc=doc)
    example_use = _stores(tmp_path / "e", "len", field=True, file="examples/zran.c", doc=doc)

    assert {s.file: s.nonproduct for s in product_use} == {
        "examples/zran.c": True, "fuzz/fuzz_x.c": True, "regress/r.c": True,
        "lib/test_util.c": False, "inflate.c": False,
    }
    assert not any(s.nonproduct for s in example_use)


def test_stores_of_returns_every_write_of_a_flood_and_marks_each_of_them(tmp_path: Path) -> None:
    """흔한 필드 이름(`size`) — 구조체 셋이 선언하고 쓰기가 열 곳이다. 인덱스는 **다 돌려주되 하나도
    모호하지 않다고 하지 않는다**(자르는 것은 쓰는 쪽 몫이다 — `source_stores`의 이름별 상한)."""
    writes = {
        "a.c": ("p", [30, 31, 32]),
        "b.c": ("q", [10, 11, 12, 13]),
        "c.c": ("r", [5, 6, 7]),
    }
    doc = {
        **_STORES_INDEX,
        "fields": {
            "size": [
                {"type": kind, "file": file, "line_start": 1, "line_end": 1}
                for kind, file in (("ctx", "a.c"), ("conn", "b.c"), ("node", "c.c"))
            ]
        },
        "field_assigns": {
            "size": [
                {"file": file, "line": line, "func": "f", "lhs": f"{obj}->size", "rhs": "1",
                 "op": "="}
                for file, (obj, lines) in writes.items()
                for line in lines
            ]
        },
    }

    found = _stores(tmp_path, "size", field=True, file="a.c", lhs="p->size", doc=doc)

    assert len(found) == 10 > 8
    assert all(s.ambiguous for s in found)
    assert [(s.file, s.line) for s in found] == sorted((s.file, s.line) for s in found)


def test_stores_of_finds_the_assignments_of_a_file_scope_variable(tmp_path: Path) -> None:
    """`assigns` — 이름 → (파일, 함수, 줄). `copy`의 24줄 `g_len = 0`은 그 함수의 **지역** `g_len`
    (`locals` 23줄)이 가리므로 전역에 대한 쓰기가 아니다."""
    found = _stores(tmp_path, "g_len", file="b.c", line=7)

    assert [(s.file, s.line, s.func, s.field) for s in found] == [
        ("a.c", 16, "grow", False),
        ("b.c", 7, "other_fn", False),
    ]


def test_stores_of_marks_a_same_named_variable_in_another_file_as_ambiguous(
    tmp_path: Path,
) -> None:
    """`g_len`은 a.c·b.c 둘 다에 파일 전역이 있다(`static`일 수 있다 — 인덱스는 저장 클래스를 안
    든다). **찾는 줄과 같은 파일**의 쓰기만 그 변수라고 말할 수 있고, 다른 파일의 쓰기는 이름만으로
    같은 변수라고 못 한다 — 싣되 모호하다."""
    found = _stores(tmp_path, "g_len", file="b.c", line=7)

    assert [(s.file, s.ambiguous) for s in found] == [("a.c", True), ("b.c", False)]


def test_stores_of_does_not_resolve_a_static_across_files_by_name_alone(tmp_path: Path) -> None:
    """같은 질문을 a.c쪽에서 하면 뒤집힌다 — 다른 파일(b.c)의 쓰기가 모호하다."""
    found = _stores(tmp_path, "g_len", file="a.c", line=8)

    assert [(s.file, s.ambiguous) for s in found] == [("a.c", False), ("b.c", True)]


def test_stores_of_skips_a_name_that_is_local_where_it_is_used(tmp_path: Path) -> None:
    """찾는 줄(a.c:25 — `copy` 안)에서 파라미터(`n`)와 전역을 가리는 지역(`g_len`)은 쓰기 지점을
    찾을 대상이 아니다 — 그 쓰기들은 같은 함수 안이고 이미 보인다. `[]`다(`None`이 아니다)."""
    assert _stores(tmp_path, "n", line=25) == []
    assert _stores(tmp_path, "g_len", line=26) == []


def test_stores_of_skips_a_name_the_index_does_not_know_as_a_variable(tmp_path: Path) -> None:
    """파일 스코프 선언이 없는 이름(매크로·enum 상수·시스템 헤더의 것)은 변수로 못 다룬다 —
    어딘가에서 대입되고 있어도(`optind = 0`) 그 이름이 어느 변수인지 인덱스는 모른다."""
    doc = {
        **_STORES_INDEX,
        "assigns": {"b.c": {"other_fn": {"optind": [9], "g_len": [7]}}},
    }

    assert _stores(tmp_path, "NULL") == []
    assert _stores(tmp_path, "return") == []
    assert _stores(tmp_path, "optind", file="b.c", line=8, doc=doc) == []
    assert [s.line for s in _stores(tmp_path, "g_len", file="b.c", line=8, doc=doc)] == [7]


def test_stores_of_without_an_enclosing_function_does_not_check_locals(tmp_path: Path) -> None:
    """찾는 줄이 어느 함수에도 안 들면(파일 스코프 줄) 지역 판정을 건너뛴다 — 8줄은 전역 선언."""
    found = _stores(tmp_path, "g_len", line=8)

    assert [(s.file, s.line) for s in found] == [("a.c", 16), ("b.c", 7)]


def test_stores_of_attaches_the_allocator_call_on_the_same_line(tmp_path: Path) -> None:
    """쓰기 줄에 같은 함수의 할당기 호출이 있으면 크기 식(`calls`의 `args`)과 함께 붙는다 — 필드
    쓰기 (13줄 `c->buf = malloc(c->len + 1)`)와 변수 대입(줄바꿈된 14줄 `g_buf = malloc(<줄바꿈> n +
    1)`, `calls`가 호출 시작 줄 14로 센다) 둘 다. 할당기가 아닌 호출(`memcpy`)은 안 붙는다."""
    (buf,) = _stores(tmp_path, "buf", field=True, lhs="c->buf")
    assert (buf.alloc, buf.alloc_args) == ("malloc", ("c->len + 1",))

    found = _stores(tmp_path, "g_buf", file="b.c", line=8)
    by_line = {(s.file, s.line): s for s in found}
    assert by_line[("a.c", 14)].alloc_args == ("n + 1",)
    assert (by_line[("b.c", 8)].alloc, by_line[("b.c", 8)].alloc_args) == ("calloc", ("1", "16"))

    plain = _stores(tmp_path, "len", field=True, lhs="c->len")
    assert {s.alloc for s in plain} == {None}


def test_stores_of_ignores_an_allocator_call_that_another_function_made_on_that_line(
    tmp_path: Path,
) -> None:
    """같은 줄이어도 **호출한 함수가 쓰기와 같아야** 붙는다 — 표가 파일·줄만으로 맞지 않게 한다."""
    doc = {
        **_STORES_INDEX,
        "calls": {
            **_STORES_INDEX["calls"],
            "malloc": [{"file": "a.c", "line": 13, "caller": "elsewhere", "args": ["9"]}],
        },
    }

    (buf,) = _stores(tmp_path, "buf", field=True, lhs="c->buf", doc=doc)

    assert buf.alloc is None


def test_stores_of_knows_xmalloc_style_allocator_names(tmp_path: Path) -> None:
    """`malloc`뿐 아니라 접두·접미가 붙은 할당기(`xmalloc`·`av_mallocz`·`g_strdup`·`reallocarray`)도
    표의 `calls` 키 이름으로 찾는다 — `malloc_usable_size`처럼 할당기가 아닌 이름은 아니다."""
    site = {"file": "a.c", "line": 13, "caller": "grow", "args": ["8"]}
    names = ("xmalloc", "av_mallocz", "g_strdup", "reallocarray", "OPENSSL_malloc", "g_malloc0")
    for name in names:
        doc = {**_STORES_INDEX, "calls": {name: [site]}}
        (buf,) = _stores(tmp_path, "buf", field=True, lhs="c->buf", doc=doc)
        assert (buf.alloc, buf.alloc_args) == (name, ("8",)), name

    doc = {**_STORES_INDEX, "calls": {"malloc_usable_size": [site]}}
    (buf,) = _stores(tmp_path, "buf", field=True, lhs="c->buf", doc=doc)
    assert buf.alloc is None


def test_stores_of_is_none_without_v2_tables(tmp_path: Path) -> None:
    """index-v1(표 없음)이거나 인덱스가 없으면 `None` — 「쓰기가 없다」(`[]`)와 갈린다."""
    assert _stores(tmp_path, "len", field=True, doc=_INDEX_V1) is None
    assert _stores(tmp_path / "none", "len", field=True, doc=None) is None


def test_stores_of_is_empty_for_a_field_nobody_writes(tmp_path: Path) -> None:
    assert _stores(tmp_path, "never_written", field=True) == []


# --- Auditor `SOURCE` 정의 조회 위생: `locals_of` · 비제품 경로 강등 -------------------------
#
# 정의 조회(`definitions_of`·`callees_of`·`call_sites_of`)는 「저장소에서 하나뿐일 때만」 싣는다.
# 비제품 경로(`tests/`·`examples/` 등)에 같은 이름이 있으면 그 판정이 막힌다 — 강등은 그 정의를
# **제품 정의 뒤로 미루는 것**이지 지우는 것이 아니다. 기본(꺼짐)은 옛 동작이고, 켜는 곳은
# `runner._auditor_source` 하나다.


def test_locals_of_gives_the_locals_and_params_of_one_function(tmp_path: Path) -> None:
    """`locals` 표는 파라미터를 포함한다(`indexer.py` 표). 함수 하나의 이름 집합이고, 그 파일의 다른
    함수 · 모르는 파일은 빈 집합이다 — `None`이 아니다(그쪽은 「v2 표가 없다」)."""
    graph = _graph(tmp_path, _INDEX_V2)

    assert graph.locals_of("parser.c", "parse_header") == frozenset({"src", "buf"})
    assert graph.locals_of("parser.c", "helper") == frozenset()
    assert graph.locals_of("nope.c", "parse_header") == frozenset()


def test_locals_of_is_none_without_v2_tables(tmp_path: Path) -> None:
    """인덱스 없음 · v1 · 깨짐이면 `None`이다 — `definitions_of`와 같은 「없다」·「0건」 구별."""
    assert _graph(tmp_path / "v1").locals_of("parser.c", "parse_header") is None
    assert _graph(tmp_path / "none", document=None).locals_of("parser.c", "parse_header") is None
    assert _graph(tmp_path / "bad", "{ not json").locals_of("parser.c", "parse_header") is None


def test_is_nonproduct_path_looks_at_directories_not_file_names() -> None:
    """디렉터리 성분만 본다 — `tests/t.c`는 비제품이고 `lib/test_util.c`는 아니다. 대소문자는
    접는다."""
    from src.adapters.index_graph import is_nonproduct_path

    for path in ("tests/t.c", "lib/Test/x.c", "third_party/z/y.h", "fuzz/f.c", "a/fuzzers/f.c"):
        assert is_nonproduct_path(path), path
    # 다른 플랫폼·빌드 시스템 사본 — libzip 계열의 `xcode/config.h`가 실제 모양이다.
    for path in ("xcode/config.h", "a/windows/io.c", "win32/z.h", "msvc/zipconf.h"):
        assert is_nonproduct_path(path), path
    for path in ("lib/test_util.c", "src/a.c", "a.c", "lib/testing_util/x.c", "docs.c"):
        assert not is_nonproduct_path(path), path
    assert not is_nonproduct_path("lib/windows.c"), "파일 이름은 안 본다"


def test_is_nonproduct_path_matches_fuzz_dirs_by_name_not_by_bare_prefix() -> None:
    """퍼징 디렉터리는 이름(`fuzz`·`fuzzer(s)`·`fuzzing`·`oss-fuzz`)과 구분자 붙은 접두
    (`fuzz_`·`fuzz-`)로 본다. 맨 `fuzz` 접두는 `src/fuzzy/`(퍼지 매칭 — 제품)까지 먹었다(PR #444
    리뷰). `unittest(s)`·`testsuite`는 FT Level 1 270표본 잡음 경로 집계에 나온 이름이다."""
    from src.adapters.index_graph import is_nonproduct_path

    for path in (
        "fuzz/f.c", "a/fuzzer/f.c", "a/fuzzing/f.c", "oss-fuzz/build.c", "fuzz_targets/t.c",
        "a/fuzz-corpus/t.c", "a/unittest/u.c", "unittests/u.c", "testsuite/s.c",
    ):
        assert is_nonproduct_path(path), path
    for path in ("src/fuzzy/match.c", "lib/fuzzyfind/x.c", "ios/port.c", "android/jni.c"):
        assert not is_nonproduct_path(path), path


# 같은 이름이 제품(`lib/`)과 비제품(`examples/`)에 둘 있고(`DUP`), 비제품에만 있고(`ONLY_EX`),
# 제품에만 있다(`ONLY_LIB`). `examples/`는 사전순으로 `lib/`보다 앞이라 기본 순서와 강등 순서가
# 갈린다.
_MACROS_ACROSS_DIRS = {
    "macros": {
        "DUP": [
            {"file": "lib/a.h", "line_start": 1, "line_end": 1},
            {"file": "examples/e.h", "line_start": 1, "line_end": 1},
        ],
        "ONLY_EX": [{"file": "examples/e.h", "line_start": 2, "line_end": 2}],
        "ONLY_LIB": [{"file": "lib/b.h", "line_start": 3, "line_end": 3}],
    }
}


def test_definitions_of_demotes_nonproduct_rows_when_asked(tmp_path: Path) -> None:
    """켜면 제품 정의가 비제품 정의보다 앞서 정한다 — `DUP`은 제품 정의가 하나라 그것으로 정해진다
    (꺼진 쪽은 둘이라 건너뛴다). **삭제가 아니다**: 제품 정의가 없는 `ONLY_EX`도 실리고 순서만 제품
    정의 뒤로 밀린다(상한이 차면 뒤가 먼저 빠진다)."""
    graph = _graph(tmp_path, {**_INDEX_V2, **_MACROS_ACROSS_DIRS})

    found = graph.definitions_of(
        ["DUP", "ONLY_EX", "ONLY_LIB"], "lib/x.c", demote_nonproduct=True
    )

    assert found == [
        Location(file="lib/a.h", symbol="DUP", line_start=1, line_end=1),
        Location(file="lib/b.h", symbol="ONLY_LIB", line_start=3, line_end=3),
        Location(file="examples/e.h", symbol="ONLY_EX", line_start=2, line_end=2),
    ]


def test_definitions_of_ignores_the_path_when_the_keyword_is_off(tmp_path: Path) -> None:
    """키워드가 없으면 옛 동작 그대로다 — 제품·비제품이 같은 후보라 `DUP`은 건너뛰고, 순서는 (파일,
    시작 줄)이다(`examples/`가 `lib/`보다 앞선다). NE resolver 등 다른 호출이 이 기본을 기댄다."""
    graph = _graph(tmp_path, {**_INDEX_V2, **_MACROS_ACROSS_DIRS})

    found = graph.definitions_of(["DUP", "ONLY_EX", "ONLY_LIB"], "lib/x.c")

    assert [(loc.file, loc.symbol) for loc in found] == [
        ("examples/e.h", "ONLY_EX"),
        ("lib/b.h", "ONLY_LIB"),
    ]


def test_definitions_of_does_not_demote_when_the_anchor_itself_is_nonproduct(
    tmp_path: Path,
) -> None:
    """앵커가 `examples/` 안이면 강등하지 않는다 — 그 코드가 쓰는 정의는 형제 비제품 경로에 있는 게
    정상이다. 켜도 꺼진 것과 같은 결과다(`DUP` 건너뜀 · (파일, 줄) 순)."""
    graph = _graph(tmp_path, {**_INDEX_V2, **_MACROS_ACROSS_DIRS})
    names = ["DUP", "ONLY_EX", "ONLY_LIB"]

    assert graph.definitions_of(names, "examples/x.c", demote_nonproduct=True) == (
        graph.definitions_of(names, "examples/x.c")
    )
    assert [loc.symbol for loc in graph.definitions_of(names, "examples/x.c")] == [
        "ONLY_EX",
        "ONLY_LIB",
    ]


# 표를 가로지른 강등(PR #444 리뷰 MEDIUM). 두 모양 다 FT Level 1 270표본 관측에서 왔다 —
# ImageMagick `MagickWand/tests/script-token-test.c:67`의 `#define RelinquishMagickMemory(p)
# (free(p),NULL)`이 `MagickCore`의 제품 함수 `RelinquishMagickMemory`를 덮었다. 강등이 표 하나
# 안에서만 걸리면 제품 쪽이 함수(정의 표 밖)거나 다른 표에 있을 때 경쟁에 못 든다.
_ACROSS_TABLES = {
    **_INDEX_V2,
    "symbols": [
        *_INDEX_V2["symbols"],
        {
            "name": "RelinquishMagickMemory",
            "file": "MagickCore/memory.c",
            "line_start": 10,
            "line_end": 20,
        },
    ],
    "macros": {
        **_INDEX_V2["macros"],
        "RelinquishMagickMemory": [
            {"file": "MagickWand/tests/x.c", "line_start": 67, "line_end": 67}
        ],
        "node": [{"file": "tests/t.h", "line_start": 1, "line_end": 1}],
        "ONLY_TEST": [{"file": "tests/t.h", "line_start": 2, "line_end": 2}],
    },
    "types": {
        **_INDEX_V2["types"],
        "node": [{"file": "lib/t.h", "line_start": 4, "line_end": 6, "kind": "typedef"}],
    },
}


def test_definitions_of_demotes_across_tables_and_against_product_functions(
    tmp_path: Path,
) -> None:
    """제품 정의가 **어느 표에든** 또는 제품 함수로 있으면 비제품 행은 세 표 모두에서 후보가
    아니다 — 제품 typedef `node`(types)가 비제품 매크로 `node`(macros)를 이기고, 제품 쪽이 함수뿐인
    `RelinquishMagickMemory`는 테스트 매크로가 안 붙는다(함수 본문은 `callees_of` 몫). 제품 정의가
    없는 `ONLY_TEST`는 그대로 실린다 — 삭제가 아니다."""
    graph = _graph(tmp_path, _ACROSS_TABLES)
    names = ["RelinquishMagickMemory", "node", "ONLY_TEST"]

    found = graph.definitions_of(names, "MagickCore/blob.c", demote_nonproduct=True)

    assert found == [
        Location(file="lib/t.h", symbol="node", line_start=4, line_end=6),
        Location(file="tests/t.h", symbol="ONLY_TEST", line_start=2, line_end=2),
    ]


def test_definitions_of_keeps_the_table_order_when_demotion_is_off(tmp_path: Path) -> None:
    """키워드가 꺼지면(그리고 앵커가 비제품이면) 옛 동작 그대로다 — 행이 있는 첫 표에서 정하므로
    테스트 매크로 둘이 나온다. 꺼진 run의 바이트 동일이 여기 걸린다."""
    graph = _graph(tmp_path, _ACROSS_TABLES)
    names = ["RelinquishMagickMemory", "node", "ONLY_TEST"]
    old = [
        Location(file="MagickWand/tests/x.c", symbol="RelinquishMagickMemory", line_start=67,
                 line_end=67),
        Location(file="tests/t.h", symbol="node", line_start=1, line_end=1),
        Location(file="tests/t.h", symbol="ONLY_TEST", line_start=2, line_end=2),
    ]

    assert graph.definitions_of(names, "MagickCore/blob.c") == old
    assert graph.definitions_of(names, "tests/blob.c", demote_nonproduct=True) == old


# `entry`(lib/a.c)가 2줄에서 `only_test`(tests/에만 있다)를, 3줄에서 `dup`(lib/b.c와 tests/t.c
# 둘)을 부른다. `only_test`(tests/t.c)도 `dup`을 부르는데 그 파일이 `dup`을 자기가 정의한다.
# `t_entry`(tests/u.c)는 `dup`을 정의하지 않는 비제품 파일에서 `dup`을 부른다.
_CALLS_ACROSS_DIRS = {
    "version": "index-v1",
    "files": ["lib/a.c", "lib/b.c", "tests/t.c", "tests/u.c"],
    "skipped": [],
    "symbols": [
        {"name": "entry", "file": "lib/a.c", "line_start": 1, "line_end": 10},
        {"name": "dup", "file": "lib/b.c", "line_start": 1, "line_end": 3},
        {"name": "dup", "file": "tests/t.c", "line_start": 1, "line_end": 3},
        {"name": "only_test", "file": "tests/t.c", "line_start": 5, "line_end": 7},
        {"name": "t_entry", "file": "tests/u.c", "line_start": 1, "line_end": 10},
    ],
    "calls": [
        {"caller": "entry", "callee": "only_test", "file": "lib/a.c", "line": 2},
        {"caller": "entry", "callee": "dup", "file": "lib/a.c", "line": 3},
        {"caller": "only_test", "callee": "dup", "file": "tests/t.c", "line": 6},
        {"caller": "t_entry", "callee": "dup", "file": "tests/u.c", "line": 2},
    ],
    "unresolved_calls": 0,
    "unresolved_symbols": 0,
    "parse_errors": [],
}
_ENTRY = Location(file="lib/a.c", symbol="entry", line_start=1, line_end=10)


def test_callees_of_demotes_nonproduct_definitions_when_asked(tmp_path: Path) -> None:
    """켜면 `dup`이 제품 정의(lib/b.c) 하나로 정해지고, 제품 정의가 없는 `only_test`는 **남되** 뒤로
    밀린다 — 첫 호출 줄 순(2·3줄)이면 `only_test`가 앞이다."""
    graph = _graph(tmp_path, _CALLS_ACROSS_DIRS)

    found = graph.callees_of(_ENTRY, demote_nonproduct=True)

    assert [(loc.file, loc.symbol) for loc in found] == [
        ("lib/b.c", "dup"),
        ("tests/t.c", "only_test"),
    ]


def test_callees_of_ignores_the_path_when_the_keyword_is_off(tmp_path: Path) -> None:
    """기본은 옛 동작이다 — `dup`은 정의가 둘이라 건너뛰고 `only_test`만 남는다. 앵커가 비제품이면
    켜도 같다(`t_entry`는 `tests/u.c` 안이고 `dup` 정의는 다른 파일 둘이라 건너뛴다)."""
    graph = _graph(tmp_path, _CALLS_ACROSS_DIRS)

    assert [loc.symbol for loc in graph.callees_of(_ENTRY)] == ["only_test"]
    t_entry = Location(file="tests/u.c", symbol="t_entry", line_start=1, line_end=10)
    assert graph.callees_of(t_entry) == []
    assert graph.callees_of(t_entry, demote_nonproduct=True) == []


def test_call_sites_of_demotes_nonproduct_definitions_when_asked(tmp_path: Path) -> None:
    """켜면 `dup`의 제품 정의가 하나(lib/b.c)라 다른 파일의 호출이 실린다(꺼진 쪽은 정의가 둘이라
    다른 파일 호출을 안 싣는다). 단 `dup`의 **비제품 정의가 따로 있어**(tests/t.c) 비제품 호출 --
    그 정의를 가진 파일의 호출(`only_test`)과 어느 `dup`으로 가는지 모르는 `t_entry`(tests/u.c)
    -- 은 안 싣는다. 제품 호출 `entry`만 남는다."""
    graph = _graph(tmp_path, _CALLS_ACROSS_DIRS)
    dup = Location(file="lib/b.c", symbol="dup", line_start=1, line_end=3)

    found = graph.call_sites_of(dup, demote_nonproduct=True)

    assert found == [Location(file="lib/a.c", symbol="entry", line_start=3, line_end=3)]


def test_call_sites_of_ignores_the_path_when_the_keyword_is_off(tmp_path: Path) -> None:
    """기본은 옛 동작이다 — 정의가 둘이면 다른 파일의 호출은 하나도 안 싣는다. 앵커가 비제품이어도
    켠 것이 꺼진 것과 같다."""
    graph = _graph(tmp_path, _CALLS_ACROSS_DIRS)
    dup = Location(file="lib/b.c", symbol="dup", line_start=1, line_end=3)
    mock = Location(file="tests/t.c", symbol="dup", line_start=1, line_end=3)

    assert graph.call_sites_of(dup) == []
    assert graph.call_sites_of(mock, demote_nonproduct=True) == graph.call_sites_of(mock)


def _call_sites_doc(symbols: list[dict], calls: list[dict]) -> dict:
    files = sorted({row["file"] for row in [*symbols, *calls]})
    return {
        "version": "index-v1",
        "files": files,
        "skipped": [],
        "symbols": symbols,
        "calls": calls,
        "unresolved_calls": 0,
        "unresolved_symbols": 0,
        "parse_errors": [],
    }


def _def(name: str, file: str) -> dict:
    return {"name": name, "file": file, "line_start": 1, "line_end": 5}


def _call(caller: str, file: str) -> dict:
    return {"caller": caller, "callee": "dup", "file": file, "line": 2}


def test_call_sites_of_puts_nonproduct_sites_last_even_when_they_sort_first(
    tmp_path: Path,
) -> None:
    """`examples/`는 사전순으로 `lib/`보다 **앞**이라 (파일, 줄) 순서만 보면 비제품 호출이 먼저다 --
    강등 키가 그것을 뒤로 민다. 위 픽스처(`lib/` < `tests/`)는 키를 지워도 같은 순서라 그 키를 못
    물었다."""
    doc = _call_sites_doc(
        [_def("dup", "src/dup.c"), _def("use", "lib/use.c"), _def("ex", "examples/ex.c")],
        [_call("use", "lib/use.c"), _call("ex", "examples/ex.c")],
    )
    graph = _graph(tmp_path, doc)
    dup = Location(file="src/dup.c", symbol="dup", line_start=1, line_end=5)

    assert [loc.file for loc in graph.call_sites_of(dup)] == ["examples/ex.c", "lib/use.c"]
    assert [loc.file for loc in graph.call_sites_of(dup, demote_nonproduct=True)] == [
        "lib/use.c",
        "examples/ex.c",
    ]


def test_call_sites_of_skips_nonproduct_callers_when_a_nonproduct_copy_exists(
    tmp_path: Path,
) -> None:
    """`lib/dup.c`(대상) · `tests/mock/dup.c`(모의 구현) · `tests/mock/user.c`(모의 구현을 부르는
    이웃). 강등을 켜면 제품 정의가 하나라 다른 파일 호출이 실리는데(꺼진 쪽은 정의가 둘이라
    `[]`), **비제품 정의가 따로 있으면 비제품 호출은 어느 `dup`으로 가는지 모른다** -- 이웃
    (`tests/mock/`)이든 다른 디렉터리(`tests/other/`)든 안 싣는다. 제품 호출(`lib/use.c`)만
    남는다."""
    doc = _call_sites_doc(
        [
            _def("dup", "lib/dup.c"),
            _def("use", "lib/use.c"),
            _def("dup", "tests/mock/dup.c"),
            _def("mock_user", "tests/mock/user.c"),
            _def("other_user", "tests/other/user.c"),
        ],
        [
            _call("use", "lib/use.c"),
            _call("mock_user", "tests/mock/user.c"),
            _call("other_user", "tests/other/user.c"),
        ],
    )
    graph = _graph(tmp_path, doc)
    dup = Location(file="lib/dup.c", symbol="dup", line_start=1, line_end=5)

    assert graph.call_sites_of(dup) == []
    assert graph.call_sites_of(dup, demote_nonproduct=True) == [
        Location(file="lib/use.c", symbol="use", line_start=2, line_end=2)
    ]
