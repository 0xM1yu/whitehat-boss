"""`src/adapters/symbol_resolver.resolve()`의 조회·조립 (#63 후속, ADR 0012 결정 2).

A 단계(`tests/test_symbol_resolver.py`)는 렌더·정규화 순수 함수를 본다. 여기는 실 인덱서
(`tools/harness-tools/indexer.py`)로 만든 픽스처 저장소를 `GraphIndex`에 태워 `resolve()`가
13 라벨·UNFILLED 8사유·동명 점수·95% 중복·`max_chars` first-fit을 실제로 조립하는지 본다.

## 발견 — hop 1 예의 대입 블록은 실 인덱서로 재현할 수 없다

명세 hop 1 예의 세 번째 조각은 `Buf[GIF_STAMP_LEN] = '\\0';`(라벨 4, "assignment in the
same function")인데, 그 LHS는 **배열 첨자 대입**(`subscript_expression`)이다. 인덱서
(`indexer.py` 모듈 docstring "v2 표가 못 잡는 것")는 `assigns` 표에 **원소 대입을 올리지
않는다** — `_record_assignment`가 `left.type`이 `identifier`나 `field_expression`일
때만 기록한다. 그래서 실 인덱서로 이 파일을 인덱싱하면 `assigns`가 **비어 있고**,
`resolve()`가 table 훅만 쓰는 한(명세 밖 텍스트 재파싱을 하지 않는 한) 이 조각은 나오지
않는다 — 아래 `test_resolve_finds_the_hop1_declaration_and_macro_named_blocks_...`가 이것을
실측으로 고정한다. 렌더 계층의 바이트 일치는 `test_symbol_resolver.py`의
`test_render_followup_reproduces_the_hop1_example_byte_for_byte`가 이미 증명했다(조각을
직접 넣었다) — 그 격차가 정확히 이 한 줄이다. `_resolve_same_function_assignment`가
**평범한 이름 대입**(`i = n;`)에는 맞게 동작하는 것은
`test_resolve_finds_a_declaration_and_its_same_function_assignment`가 보인다.
"""

import importlib.util
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from src.adapters.factory import build_deps
from src.adapters.index_graph import GraphIndex
from src.adapters.slices import split_source
from src.adapters.symbol_resolver import resolve
from src.config import load_config
from src.paths import safe_join
from src.schemas.enums import UnfilledReason
from src.schemas.need_evidence import ResolveQuery
from src.schemas.scout_response import RequestedContext

REPO_ROOT = Path(__file__).resolve().parent.parent
_INDEXER_PATH = REPO_ROOT / "tools" / "harness-tools" / "indexer.py"
_HOP1_ROOT = REPO_ROOT / "tests" / "fixtures" / "ne_repo"
_NE2_ROOT = REPO_ROOT / "tests" / "fixtures" / "ne_repo2"


def _load_indexer():
    """저장소 경로에서 직접 불러온다 — `tools/`는 패키지가 아니다(`test_indexer.py`와 같은 방법)."""
    spec = importlib.util.spec_from_file_location("harness_indexer_for_resolver", _INDEXER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_indexer = _load_indexer()


def _graph_index(root: Path, tmp_path: Path) -> GraphIndex:
    """`root` 아래 `.c`/`.h`를 실 인덱서로 인덱싱하고 `GraphIndex`로 읽는다."""
    paths = sorted(root.rglob("*.c")) + sorted(root.rglob("*.h"))
    index = _indexer.build_index(paths, root)
    out = tmp_path / "index.json"
    out.write_text(json.dumps(index), encoding="utf-8")
    return GraphIndex(out)


def _read_lines(root: Path):
    """factory의 `_read_target_lines`와 같은 규칙(`split_source` 재사용, `"\n"`로만 나눈다)."""

    def read(relative: str) -> Sequence[str]:
        return split_source(safe_join(root, relative).read_bytes())

    return read


def _request(kind: str, target: str) -> RequestedContext:
    return RequestedContext(kind=kind, target=target, purpose="test", request_code="test")


def _query(**kwargs) -> ResolveQuery:
    base = {"requests": (), "hop": 1, "target_file": "", "visible": {}, "max_chars": 15_000}
    return ResolveQuery(**(base | kwargs))


# --- v1/인덱스 없음 -----------------------------------------------------------


def test_resolve_returns_an_empty_resolution_when_there_is_no_v2_index():
    resolution = resolve(
        _query(requests=(_request("function", "anything"),), target_file="a.c"),
        table=lambda _name: None,
        read_lines=lambda _file: [],
    )

    assert resolution.followup_text == ""
    assert resolution.blocks == ()
    assert resolution.unfilled == ()
    assert resolution.already == ()
    assert resolution.truncated == ()


# --- hop 1 예 — 실 인덱서 픽스처 (선언·매크로 명명은 되고, 원소 대입은 안 된다) -----


@pytest.fixture
def hop1_graph(tmp_path: Path) -> GraphIndex:
    return _graph_index(_HOP1_ROOT, tmp_path)


def test_resolve_finds_the_hop1_declaration_and_macro_named_blocks_but_not_the_assignment(
    hop1_graph: GraphIndex,
) -> None:
    """모듈 docstring의 발견 — 라벨 10(`Buf` 선언)·11(`GIF_STAMP_LEN`)은 되고, 라벨 4(원소
    대입)는 실 인덱서의 `assigns` 표에 없어서 안 된다."""
    target_file = "android-gif-drawable/src/main/c/giflib/dgif_lib.c"
    query = _query(
        requests=(_request("variable", "Buf"), _request("function", "strdup")),
        target_file=target_file,
        visible={},
    )

    resolution = resolve(query, table=hop1_graph.table, read_lines=_read_lines(_HOP1_ROOT))

    by_label = {b.label: b for b in resolution.blocks}
    assert by_label["declaration (local)"].lines == (44,)
    assert by_label["declaration (local)"].file == target_file
    assert by_label["macro named in the declaration"].name == "GIF_STAMP_LEN"
    assert by_label["macro named in the declaration"].lines == (17,)
    assert "assignment in the same function" not in by_label  # 위 docstring의 발견

    assert len(resolution.unfilled) == 1
    assert resolution.unfilled[0].reason == UnfilledReason.LIBC
    assert resolution.unfilled[0].target == "strdup"
    assert resolution.unfilled[0].text == (
        "Not resolved: function:strdup (C standard library symbol)."
    )


# --- ne_repo2 — 나머지 라벨·UNFILLED·동명 점수 ------------------------------


@pytest.fixture
def ne2_graph(tmp_path: Path) -> GraphIndex:
    return _graph_index(_NE2_ROOT, tmp_path)


@pytest.fixture
def ne2_read_lines():
    return _read_lines(_NE2_ROOT)


def test_resolve_finds_a_declaration_and_its_same_function_assignment(ne2_graph, ne2_read_lines):
    """라벨 10·4 — `i`는 `use_box` 안 지역변수고 24행에서 평범한 이름 대입이 된다(원소 대입이
    아니라 실 인덱서의 `assigns`에 잡힌다 — 위 hop1 발견과 대조되는 대조군)."""
    query = _query(requests=(_request("variable", "i"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    by_label = {b.label: b for b in resolution.blocks}
    assert by_label["declaration (local)"].lines == (23,)
    assert by_label["assignment in the same function"].lines == (24,)
    assert resolution.unfilled == ()


def test_resolve_gives_the_callee_body_when_the_declaration_is_already_visible(
    ne2_graph, ne2_read_lines
):
    """§4.4.2 5번(text4/doit 예)과 같은 모양 — `i`의 선언·대입 줄이 이미 SOURCE에 보이면
    `Already in SOURCE`로 빠지고, `helper_a(i)` 호출이 보이는 한 그 본문(라벨 5)이 남는다."""
    query = _query(
        requests=(_request("variable", "i"),),
        target_file="main.c",
        visible={"main.c": frozenset({23, 24, 26})},
    )

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert [b.label for b in resolution.blocks] == ["callee body"]
    assert resolution.blocks[0].name == "helper_a"
    assert resolution.blocks[0].lines == (16, 17, 18, 19)
    assert len(resolution.already) == 2  # 선언 줄 하나 + 대입 줄 하나


def test_resolve_gives_the_caller_site_for_a_parameter(ne2_graph, ne2_read_lines):
    """라벨 10 + 9 — `n`은 `use_box`의 파라미터라 시그니처 한 줄 + 호출부 ±3(라벨 표 값)을 준다."""
    query = _query(requests=(_request("variable", "n"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    by_label = {b.label: b for b in resolution.blocks}
    assert by_label["declaration (local)"].lines == (21,)
    assert by_label["caller site"].name == "use_box"
    assert by_label["caller site"].lines == (29, 30, 31, 32, 33, 34, 35)


def test_resolve_gives_the_global_declaration_and_its_repo_wide_assignment(
    ne2_graph, ne2_read_lines
):
    """라벨 2 + 13 — `g_buf`는 파일 전역 변수라 선언 한 줄 + 저장소 전체 대입 ±1을 준다."""
    query = _query(requests=(_request("variable", "g_buf"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    by_label = {b.label: b for b in resolution.blocks}
    assert by_label["declaration (file scope)"].lines == (13,)
    assert by_label["assignment to g_buf"].lines == (36, 37, 38)


def test_resolve_walks_a_two_hop_chain_to_the_field_assignment(ne2_graph, ne2_read_lines):
    """체인 — `b->h->len`. `b`(Box*) → `h`의 타입(header) → `len`의 대입(field_assigns, ±1).

    지나는 타입 정의 둘(라벨 7, `Box`·`header`) + 마지막 필드의 대입(라벨 3) 셋을 낸다.
    """
    query = _query(requests=(_request("variable", "b->h->len"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    type_of_blocks = [b for b in resolution.blocks if b.label == "type of b->h->len"]
    assert [(b.name, b.lines) for b in type_of_blocks] == [
        ("Box", (8, 9, 10, 11)),
        ("header", (3, 4, 5, 6)),
    ]
    field_blocks = [b for b in resolution.blocks if b.label == "assignment to .len"]
    assert [(b.name, b.lines) for b in field_blocks] == [("len", (24, 25, 26))]


def test_resolve_walks_a_chain_that_starts_from_a_global_variable(ne2_graph, ne2_read_lines):
    """체인의 base가 지역이 아니라 파일 전역인 경로 — `g_default_box.x`.

    `_declared_type_name`이 `file_vars`로 떨어지는 자리(지역·파라미터가 아닌 경우)다.
    `x`엔 `field_assigns`가 없어 타입 블록 하나만 남는다.
    """
    query = _query(requests=(_request("variable", "g_default_box.x"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert len(resolution.blocks) == 1
    assert resolution.blocks[0].name == "Box"
    assert resolution.blocks[0].label == "type of g_default_box.x"
    assert resolution.blocks[0].lines == (8, 9, 10, 11)


def test_resolve_gives_up_a_chain_when_the_base_type_cannot_be_found(ne2_graph, ne2_read_lines):
    """체인의 base가 선언도 전역도 아니면 조회 실패로 떨어진다 — `_resolve_chain`이 빈 목록을
    낸 뒤 `_unfilled_lookup_reason`이 사유를 고른다."""
    query = _query(requests=(_request("variable", "nosuchvar->field"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert resolution.blocks == ()
    assert len(resolution.unfilled) == 1
    assert resolution.unfilled[0].reason == UnfilledReason.NOT_FOUND


def test_resolve_falls_back_to_the_struct_that_declares_a_bare_field(ne2_graph, ne2_read_lines):
    """라벨 12 — 모델이 `h->len` 대신 그냥 `len`이라고 요청하는 경우(§4.4.2 3-2 후반)."""
    query = _query(requests=(_request("variable", "len"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert len(resolution.blocks) == 1
    block = resolution.blocks[0]
    assert block.name == "header"
    assert block.label == "struct that declares this field"
    assert block.lines == (3, 4, 5, 6)


def test_resolve_gives_the_type_definition_with_no_chain(ne2_graph, ne2_read_lines):
    """라벨 8 — 체인 없는 `type:Box`."""
    query = _query(requests=(_request("type", "Box"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert len(resolution.blocks) == 1
    assert (resolution.blocks[0].name, resolution.blocks[0].label) == ("Box", "type definition")


def test_resolve_gives_the_macro_definition_line_with_no_chain(ne2_graph, ne2_read_lines):
    """라벨 6 — 체인 없는 `macro:MAX_ITEMS`(라벨 11의 "선언 옆 매크로"와 다른, 직접 요청 경로)."""
    query = _query(requests=(_request("macro", "MAX_ITEMS"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert len(resolution.blocks) == 1
    assert (resolution.blocks[0].name, resolution.blocks[0].label) == ("MAX_ITEMS", "macro")
    assert resolution.blocks[0].lines == (1,)


def test_resolve_rejects_a_placeholder_target_end_to_end(ne2_graph, ne2_read_lines):
    """정규화 실패(형식 사유)가 조회를 타지 않고 그대로 `Unfilled`가 되는 경로 —
    `_resolve_request`의 `is_rejected(normalized)` 분기."""
    query = _query(requests=(_request("variable", "unknown"),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert resolution.blocks == ()
    assert len(resolution.unfilled) == 1
    assert resolution.unfilled[0].reason == UnfilledReason.PLACEHOLDER


def test_resolve_skips_a_block_whose_file_no_longer_exists(ne2_read_lines):
    """색인이 가리키는 파일이 그 뒤 지워졌으면(오래된 인덱스) 그 블록만 건너뛴다 — 인덱스
    전체를 손으로 짜서 이 경계 사례를 직접 겨냥한다(실 인덱서로는 만들 수 없는 상태다).

    빠진 블록은 조용히 사라지지 않는다 — `_resolve_flat`이 아무것도 못 채웠으니
    `_unfilled_lookup_reason`이 사유를 고르고, `ghost`는 `_all_symbol_names`(표의 키를
    그대로 모은다 — 렌더 성공 여부를 안 본다)에 여전히 있어 자기 자신과
    `closest_name`으로 맞는다. "찾았지만 못 읽었다"를 구분하는 아홉 번째 사유는 명세
    8개에 없어 만들지 않았다 — ponytail: 이 정도로 조용하지 않은 것으로 충분하다.
    """

    def stale_table(name: str):
        if name == "functions":
            return {"ghost": [{"file": "missing.c", "line_start": 1, "line_end": 1, "params": []}]}
        return {}

    query = _query(requests=(_request("function", "ghost"),), target_file="main.c")

    resolution = resolve(query, table=stale_table, read_lines=ne2_read_lines)

    assert resolution.blocks == ()
    assert len(resolution.unfilled) == 1
    assert resolution.unfilled[0].reason == UnfilledReason.CLOSEST_NAME


@pytest.mark.parametrize(
    ("target", "expected_reason", "expected_detail"),
    [
        ("undefined_sink", UnfilledReason.OUTSIDE_REPO, ""),
        ("helper_b", UnfilledReason.CLOSEST_NAME, "helper_a"),
        ("totally_unrelated_xyz", UnfilledReason.NOT_FOUND, ""),
    ],
)
def test_resolve_picks_the_right_unfilled_reason_when_lookup_fails(
    ne2_graph, ne2_read_lines, target, expected_reason, expected_detail
):
    query = _query(requests=(_request("function", target),), target_file="main.c")

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert resolution.blocks == ()
    assert len(resolution.unfilled) == 1
    item = resolution.unfilled[0]
    assert item.reason == expected_reason
    if expected_detail:
        assert expected_detail in item.text


def test_resolve_prefers_the_function_in_the_target_file_when_names_collide(
    ne2_graph, ne2_read_lines
):
    """§5.3 동명 점수 — `init`이 `scoring/a_main.c`·`scoring/a_parser.c`(둘 다 function) ·
    `scoring/a_other.c`(variable)에 있다. target_file과 같은 파일이 이긴다(A 8점 > B 5점)."""
    query = _query(
        requests=(_request("function", "init"),),
        target_file="scoring/a_main.c",
        visible={
            "scoring/a_main.c": frozenset({1}),
            "scoring/a_parser.c": frozenset({1}),
        },
    )

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    function_blocks = [b for b in resolution.blocks if b.label == "function body"]
    assert [b.file for b in function_blocks] == ["scoring/a_main.c"]


def test_resolve_truncates_blocks_that_do_not_fit_the_budget(ne2_graph, ne2_read_lines):
    """`max_chars`가 너무 작으면 블록을 통째로 빼고 이름을 `truncated`에 남긴다."""
    query = _query(requests=(_request("variable", "i"),), target_file="main.c", max_chars=1)

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert resolution.blocks == ()
    assert resolution.truncated == ("i", "i")  # 선언 + 같은 함수 대입, 둘 다 넘친다
    assert resolution.followup_text == ""  # 블록도 unfilled도 없으면 빈 문자열(§4)


# --- factory 배선 ------------------------------------------------------------


def test_factory_wires_resolve_requests_and_it_actually_resolves(tmp_path: Path) -> None:
    """`build_deps`가 항상 `resolve_requests`를 꽂고, 그 hook을 부르면 실제로 조회된다."""
    paths = sorted(_NE2_ROOT.rglob("*.c"))
    index = _indexer.build_index(paths, _NE2_ROOT)
    run_id = "run_test_resolver_wiring"
    from src.adapters.container_tools import index_output_path

    out = index_output_path(tmp_path / "artifacts", run_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index), encoding="utf-8")

    deps = build_deps(
        load_config("dev_fake"),
        run_id=run_id,
        source_root=_NE2_ROOT,
        artifacts_root=tmp_path / "artifacts",
    )

    assert deps.resolve_requests is not None
    resolution = deps.resolve_requests(
        _query(requests=(_request("variable", "g_buf"),), target_file="main.c")
    )
    assert any(b.label == "declaration (file scope)" for b in resolution.blocks)
