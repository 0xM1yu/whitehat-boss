"""1단계 인덱서 산출물이 캡슐의 그래프 섹션이 되는 경로 (D13).

`index.json`이 **있는 경로와 없는 경로 둘 다** 태운다. 없는 쪽이 `dev_hybrid`·`dev_fake`,
있는 쪽이 컨테이너 프로필(`dev_docker`·`dev_singularity`·`local_balanced`·`b1_single`·
`dev_hybrid_docker`)이다. 한쪽만 태우면 나머지 절반은 아무도 안 보는 코드가 된다.

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
    GraphIndex,
    IndexSummary,
    fit_names,
)
from src.adapters.vllm_retrieval import FakeEmbeddingClient, FakeRerankerClient
from src.config import load_config
from src.ids import IdFactory
from src.interfaces.retriever import Query
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel
from src.schemas.finding import MissingEvidence

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

    assert graph.names_for("callers", "parser.c") is None
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

    assert graph.names_for("callers", "parser.c") is None
    assert graph.summary.status == STATUS_UNSUPPORTED


def test_malformed_index_json_is_refused_without_raising(tmp_path: Path):
    graph = _graph(tmp_path, "{ this is not json")

    assert graph.names_for("callees", "parser.c") is None
    assert graph.summary.status == STATUS_MALFORMED


def test_index_with_the_right_version_but_missing_fields_is_refused(tmp_path: Path):
    """버전만 맞고 필드가 없으면 부분 결과를 쓰지 않는다 — 어디까지 믿을지 알 수 없다."""
    graph = _graph(tmp_path, {"version": "index-v1", "symbols": []})

    assert graph.names_for("callers", "parser.c") is None
    assert graph.summary.status == STATUS_MALFORMED


# --- 간선 선택 규칙 -------------------------------------------------------------


def test_callers_come_from_other_files_only(tmp_path: Path):
    """같은 파일 안의 호출자는 `target_function`이 이미 담고 있다. 두 번 세면 토큰이 부푼다."""
    assert _graph(tmp_path).names_for("callers", "parser.c") == [_CALLER_ENTRY]


def test_callee_defined_in_the_target_file_is_not_listed_twice(tmp_path: Path):
    """`helper`는 parser.c에 정의돼 있어 본문에 이미 있다 — 간선의 반대쪽 끝이 캡슐 안이다."""
    callees = _graph(tmp_path).names_for("callees", "parser.c")

    assert not any(name.startswith("helper ") for name in callees)


def test_external_callee_not_in_the_symbol_table_is_kept(tmp_path: Path):
    """`memcpy`는 symbols에 없다 = 본문 안에 없는 것이 확실하다. 빼면 실재하는 간선을 숨긴다."""
    assert _graph(tmp_path).names_for("callees", "parser.c") == [_CALLEE_ENTRY]


def test_graph_entries_are_sorted_for_determinism(tmp_path: Path):
    """같은 입력에 같은 캡슐이 나오지 않으면 재현성이 깨진다(engineering-guide.md §5)."""
    document = {
        **_INDEX_V1,
        "calls": [
            {"caller": "zeta", "callee": "parse_header", "file": "z.c", "line": 3},
            {"caller": "alpha", "callee": "parse_header", "file": "a.c", "line": 7},
        ],
    }

    assert _graph(tmp_path, document).names_for("callers", "parser.c") == [
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

    assert fit_names(names, per_entry * 2) == (per_entry * 2, 2)
    assert fit_names(names, 10_000) == (per_entry * 3, 3)


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

    채우려면 인덱서에 struct·class·typedef 수집을 넣고 `INDEX_VERSION`을 올려야 한다.
    """
    assert "types" not in GRAPH_SECTION_KINDS
    for level in CapsuleLevel:
        capsule = _fake(source, _graph(tmp_path)).search(_query(), level)
        assert _section(capsule, "types") is None


def test_graph_section_kind_that_does_not_exist_is_a_programming_error(tmp_path: Path):
    """오타로 부른 섹션이 조용히 `None`을 내면 "그래프가 없다"로 읽힌다."""
    with pytest.raises(ValueError, match="그래프 섹션이 아니다"):
        _graph(tmp_path).names_for("types", "parser.c")


# --- 훅과 trace ----------------------------------------------------------------


def test_unresolved_counts_reach_the_hook_exactly_once_per_run(tmp_path: Path):
    """후보 수에 비례해 실리면 같은 값이 중복돼 재현성 비교가 지저분해진다."""
    seen: list[IndexSummary] = []
    graph = _graph(tmp_path, on_index=seen.append)

    graph.names_for("callers", "parser.c")
    graph.names_for("callees", "parser.c")
    graph.names_for("callers", "main.c")

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
