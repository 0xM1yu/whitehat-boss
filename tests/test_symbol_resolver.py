"""`src/adapters/symbol_resolver.py`의 조회·동명 점수·`resolve()` 조립 테스트
(#95 후속, ADR 0012 결정 2).

렌더·정규화 순수 함수는 `tests/test_symbol_render.py`가 본다. 여기는 실 인덱서
(`tools/harness-tools/indexer.py`)로 만든 픽스처 저장소를 `GraphIndex`에 태워 `resolve()`가
13 라벨·UNFILLED 8사유·동명 점수·95% 중복·`max_chars` first-fit을 실제로 조립하는지 본다.

## 발견 — hop 1 예의 대입 블록은 실 인덱서로 재현할 수 없다

명세 hop 1 예의 세 번째 조각은 `Buf[GIF_STAMP_LEN] = '\\0';`(라벨 4, "assignment in the
same function")인데, 그 LHS는 **배열 첨자 대입**(`subscript_expression`)이다. 인덱서는
`assigns` 표에 **원소 대입을 올리지 않는다** — `_record_assignment`가 `assigns`에는 `left.type`이
`identifier`일 때만 기록한다. 원소 대입은 index-v3(#304)부터 **`writes` 표에 따로** 들지만
`resolve()`는 그 표를 아직 안 읽는다. 그래서 실 인덱서로 이 파일을 인덱싱하면 `assigns`가
**비어 있고**, `resolve()`가 `assigns`만 보는 한 이 조각은 나오지
않는다 — 아래 `test_resolve_finds_the_hop1_declaration_macro_named_and_element_assignment_blocks`가
이것을 실측으로 고정했다(⏵ 아래 — 지금은 그 검사가 반대를 박는다). 렌더 계층의 바이트 일치는
`test_symbol_render.py`의
`test_render_followup_reproduces_the_hop1_example_byte_for_byte`가 이미 증명했다(조각을
직접 넣었다) — 그 격차가 정확히 이 한 줄이다. `_resolve_same_function_assignment`가
**평범한 이름 대입**(`i = n;`)에는 맞게 동작하는 것은
`test_resolve_finds_a_declaration_and_its_same_function_assignment`가 보인다.

⏵ 2026-10-10 — resolver가 index-v3 `writes`를 읽는다(#304). 위 발견은 그 전의 기록이다. 이제 실
인덱서(index-v3 이상)로 hop 1 예의 세 조각이 **다** 나온다 — 남은 격차는 **순서**뿐이다: 명세 예는
선언 → 대입 → 매크로이고 resolver는 선언 → 매크로 → 대입이다(`_resolve_declaration`의 순서 —
`writes`가 없는 인덱스의 출력 바이트를 안 바꾸려고 그대로 뒀다).
`test_resolve_reproduces_the_hop1_example_up_to_the_order_of_two_fragments`가 그 격차를 바이트로
박는다. v2 인덱스(`writes` 없음)에서는 예전 그대로 대입 조각이 없고 `Resolution.writes_available`이
`False`다(아래 #304 절).
"""

import importlib.util
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from src.adapters.factory import build_deps
from src.adapters.index_graph import (
    STATUS_LOADED,
    STATUS_MALFORMED,
    V3_TABLES,
    V4_TABLES,
    GraphIndex,
)
from src.adapters.slices import split_source
from src.adapters.symbol_resolver import (
    _CALLER_SITE_RADIUS,
    _GLOBAL_ASSIGN_RADIUS,
    Candidate,
    _clip,
    pick_best_candidate,
    resolve,
    score_candidate,
)
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


# --- _clip 경계 (5.4 [LOW], 리뷰 회귀 방지 — 로직 자체는 맞다고 확인됐다) -----------


@pytest.mark.parametrize(
    ("line", "radius", "total", "expected"),
    [
        (1, _CALLER_SITE_RADIUS, 10, (1, 4)),  # 라벨 9(caller site ±3) — 파일 1행 근처
        (10, _CALLER_SITE_RADIUS, 10, (7, 10)),  # 라벨 9 — 마지막 줄 근처
        (1, _GLOBAL_ASSIGN_RADIUS, 10, (1, 2)),  # 라벨 4·13(대입 ±1) — 파일 1행 근처
        (10, _GLOBAL_ASSIGN_RADIUS, 10, (9, 10)),  # 라벨 4·13 — 마지막 줄 근처
    ],
)
def test_clip_keeps_caller_site_and_assignment_radii_inside_the_file(line, radius, total, expected):
    assert _clip(line - radius, line + radius, total) == expected


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
    assert resolution.index_available is False  # 조회를 안 했다 — 「못 찾음」과 가른다(#315)


def test_an_empty_v2_function_table_is_an_index_that_finds_nothing_not_a_missing_index():
    """`GraphIndex.table()`은 `None`(없음·v1)과 `{}`(v2인데 그 표가 0건)를 일부러 가른다. 빈 표는
    색인이 **있는** 것이라 조회가 돌고 「못 찾음」이 남아야 한다 — `is None`을 `not`으로 바꾸는
    변이를 이 검사가 잡는다(#542 리뷰)."""
    resolution = resolve(
        _query(requests=(_request("function", "anything"),), target_file="a.c"),
        table=lambda _name: {},
        read_lines=lambda _file: [],
    )

    assert resolution.index_available is True
    assert [item.reason.value for item in resolution.unfilled] == ["not_found"]


def test_a_record_without_line_start_makes_the_index_unavailable_instead_of_crashing(
    tmp_path: Path,
) -> None:
    """깨진 레코드는 resolver에 닿기 전에 `GraphIndex`가 거절한다 — 색인 통째로 `malformed`가 되고
    resolver는 예외 없이 `index_available=False`를 낸다. 그래서 Auditor `_lookup`은
    `(OSError, ValueError)`만 잡는다(`KeyError`·`TypeError`는 resolver 버그라 삼키지 않는다)."""
    index = _indexer.build_index(sorted(_HOP1_ROOT.rglob("*.c")), _HOP1_ROOT)
    name, records = next(iter(index["functions"].items()))
    path = tmp_path / "index.json"
    # 양성 대조: 손대지 않은 색인은 읽힌다 - 아래 `malformed`가 픽스처 탓이 아님을 보인다.
    path.write_text(json.dumps(index), encoding="utf-8")
    assert GraphIndex(path).summary.status == STATUS_LOADED
    records[0].pop("line_start")
    path.write_text(json.dumps(index), encoding="utf-8")
    graph = GraphIndex(path)

    resolution = resolve(
        _query(requests=(_request("function", name),), target_file="a.c"),
        table=graph.table,
        read_lines=lambda _file: [],
    )

    assert graph.summary.status == STATUS_MALFORMED
    assert resolution.index_available is False


# --- hop 1 예 — 실 인덱서 픽스처 (⏵ 2026-10-10 #304 — 원소 대입도 된다) ------------


@pytest.fixture
def hop1_graph(tmp_path: Path) -> GraphIndex:
    return _graph_index(_HOP1_ROOT, tmp_path)


def test_resolve_finds_the_hop1_declaration_macro_named_and_element_assignment_blocks(
    hop1_graph: GraphIndex,
) -> None:
    """라벨 10(`Buf` 선언)·11(`GIF_STAMP_LEN`)·4(원소 대입 88행). 라벨 4는 `assigns`가 아니라
    index-v3 `writes`에서 온다(#304 — ⏵ 2026-10-10 전에는 이 검사가 라벨 4가 **없다**를 박았다)."""
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
    assert by_label["assignment in the same function"].lines == (88,)
    assert by_label["assignment in the same function"].file == target_file
    assert resolution.writes_available is True

    assert len(resolution.unfilled) == 1
    assert resolution.unfilled[0].reason == UnfilledReason.LIBC
    assert resolution.unfilled[0].target == "strdup"
    assert resolution.unfilled[0].text == (
        "Not resolved: function:strdup (C standard library symbol)."
    )


def test_resolve_reproduces_the_hop1_example_up_to_the_order_of_two_fragments(
    hop1_graph: GraphIndex,
) -> None:
    """실 인덱서 → 실 resolver의 `followup_text`가 명세 hop 1 예(`test_symbol_render`의
    `_HOP1_EXPECTED` — 렌더 계층만 본 바이트 일치)와 **조각 둘의 순서만** 다르다(모듈 docstring
    ⏵ 2026-10-10). 순서를 명세대로 바꾸면 이 검사가 빨개진다 — 그때는 기대값을 명세 그대로
    고친다."""
    from tests.test_symbol_render import _HOP1_EXPECTED

    query = _query(
        requests=(_request("variable", "Buf"), _request("function", "strdup")),
        target_file="android-gif-drawable/src/main/c/giflib/dgif_lib.c",
        visible={},
    )

    resolution = resolve(query, table=hop1_graph.table, read_lines=_read_lines(_HOP1_ROOT))

    assignment = (
        "/* --- retrieved: Buf (assignment in the same function) --- */\n"
        "/* ===== android-gif-drawable/src/main/c/giflib/dgif_lib.c ===== */\n"
        "  88| \tBuf[GIF_STAMP_LEN] = '\\0';\n\n"
    )
    macro = (
        "/* --- retrieved: GIF_STAMP_LEN (macro named in the declaration) --- */\n"
        "/* ===== android-gif-drawable/src/main/c/giflib/gif_lib.h ===== */\n"
        "  17| #define GIF_STAMP_LEN sizeof(GIF_STAMP) - 1\n\n"
    )
    assert assignment + macro in _HOP1_EXPECTED  # 기대값 조각이 명세 예와 바이트로 같다
    assert resolution.followup_text == _HOP1_EXPECTED.replace(
        assignment + macro, macro + assignment
    )


# --- #304 — index-v3 `writes`(원소·역참조 쓰기)를 대입 라벨 3·4·13에 싣는다 -----------------
#
# 실 인덱서로 인덱싱하는 합성 저장소다(`ne_repo2`에 함수를 더하면 다른 검사의 `closest_name`·줄
# 번호가 흔들려 따로 둔다). 줄 번호는 아래 소스의 실제 줄이다(1-index).

_WRITES_C = """\
#define CAP 8
struct hdr {
    char *buf;
    int len;
};
static char g_tbl[CAP];
void fill(char *dst, struct hdr *h)
{
    char tmp[CAP] = {0};
    int k = 0;
    tmp[0] = 'a';
    k = 2;
    *dst = tmp[0];
    tmp[k] = 'b'; tmp[1] = 'c';
    h->len = k;
    h->buf[0] = 'd';
}
void other(void)
{
    char tmp[CAP];
    tmp[3] = 'z';
    g_tbl[1] = 'e';
    g_tbl[2] = 'f';
}
void reset(void)
{
    char *p = 0;
    p = g_tbl; *p = 0;
}
void clash(struct hdr *h)
{
    char len[CAP];
    len[0] = 'g';
}
static char *g_ptr;
void swap(void)
{
    g_ptr = g_tbl; *g_ptr = 0;
}
"""


def _writes_graph(tmp_path: Path, *, version: str | None = None) -> GraphIndex:
    """`_WRITES_C`를 실 인덱서로 인덱싱한다. `version="index-v2"`면 v3 이상 표를 떼어 #304 전
    이미지가 내던 v2 문서로 만든다(v2 표는 v3에서 한 글자도 안 바뀌었다 — `indexer.py` v3 절)."""
    return _files_graph(tmp_path, {"w.c": _WRITES_C}, version=version)


def _files_graph(
    tmp_path: Path, files: dict[str, str], *, version: str | None = None
) -> GraphIndex:
    root = tmp_path / "repo"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    index = _indexer.build_index([root / name for name in files], root)
    if version is not None:
        index["version"] = version
        for name in V3_TABLES + V4_TABLES:
            index.pop(name, None)
    out = tmp_path / "index.json"
    out.write_text(json.dumps(index), encoding="utf-8")
    return GraphIndex(out)


def _writes_resolve(graph: GraphIndex, tmp_path: Path, target: str, target_file: str = "w.c"):
    query = _query(requests=(_request("variable", target),), target_file=target_file)
    return resolve(query, table=graph.table, read_lines=_read_lines(tmp_path / "repo"))


def _labelled(resolution, label: str) -> list[tuple[int, ...]]:
    return [block.lines for block in resolution.blocks if block.label == label]


def test_label_4_merges_element_writes_in_line_order_within_the_function(tmp_path: Path):
    """`tmp`(지역 배열): 선언 9행(라벨 10) + 같은 함수의 원소 대입 11·14행(라벨 4). 9행 선언
    초기화(`init`)는 라벨 4에 다시 안 싣고, 다른 함수(`other`)의 같은 이름 `tmp[3]`(21행)도 안 든다.
    한 줄에 원소 대입이 둘(14행)이면 조각 하나다."""
    resolution = _writes_resolve(_writes_graph(tmp_path), tmp_path, "tmp")

    assert _labelled(resolution, "declaration (local)") == [(9,)]
    assert _labelled(resolution, "assignment in the same function") == [(11,), (14,)]
    assert resolution.writes_available is True


def test_label_4_carries_a_deref_write_through_a_parameter(tmp_path: Path):
    """`dst`(파라미터): `*dst = tmp[0];`(13행, `deref`)이 라벨 4로 든다."""
    resolution = _writes_resolve(_writes_graph(tmp_path), tmp_path, "dst")

    assert _labelled(resolution, "assignment in the same function") == [(13,)]


def test_label_4_merges_name_assigns_and_drops_a_same_line_duplicate(tmp_path: Path):
    """`p`: 28행에 이름 대입(`p = g_tbl`)과 역참조 쓰기(`*p = 0`)가 같이 있다 — 조각은 하나다.
    27행 선언 초기화는 라벨 10이 준다. `k`(이름 대입만 — `writes`에 원소·역참조 행이 없다)는
    예전 그대로 12행 하나다(대조군)."""
    graph = _writes_graph(tmp_path)

    p = _writes_resolve(graph, tmp_path, "p")
    assert _labelled(p, "declaration (local)") == [(27,)]
    assert _labelled(p, "assignment in the same function") == [(28,)]

    k = _writes_resolve(graph, tmp_path, "k")
    assert _labelled(k, "assignment in the same function") == [(12,)]


def test_label_13_carries_element_writes_to_a_file_scope_array(tmp_path: Path):
    """`g_tbl`(파일 전역 배열): 이름 대입이 없고 원소 대입 22·23행이 ±1로 든다. 순서는 (파일, 줄).
    `reset`의 `p = g_tbl`은 `p`에 쓰는 것이라 안 든다."""
    resolution = _writes_resolve(_writes_graph(tmp_path), tmp_path, "g_tbl")

    assert _labelled(resolution, "declaration (file scope)") == [(6,)]
    assert _labelled(resolution, "assignment to g_tbl") == [(21, 22, 23), (22, 23, 24)]
    assert "Note: assignment blocks" in resolution.followup_text  # 라벨 13 — 주의문이 붙는다


def test_label_13_keeps_name_assigns_first_and_drops_a_same_line_write(tmp_path: Path):
    """`g_ptr`(파일 전역 포인터): 38행에 이름 대입(`g_ptr = g_tbl`)과 역참조 쓰기(`*g_ptr = 0`)가
    같이 있다 — 라벨 13 조각은 하나다(±1)."""
    resolution = _writes_resolve(_writes_graph(tmp_path), tmp_path, "g_ptr")

    assert _labelled(resolution, "declaration (file scope)") == [(35,)]
    assert _labelled(resolution, "assignment to g_ptr") == [(37, 38, 39)]


def test_label_3_carries_field_based_element_writes(tmp_path: Path):
    """체인 `h->buf`: 마지막 필드 `buf`에 필드 대입(`field_assigns`)은 없고 필드 기저 원소 대입
    `h->buf[0] = 'd'`(16행)가 ±1로 든다. `h->len`은 필드 대입(15행)만 — 대조군이다. `clash`의
    지역 배열 `len[0] = 'g'`(33행)는 `writes`에서 같은 키 `len` 아래 **이름 기저**로 든다 — 필드
    `.len`의 대입이 아니라서 라벨 3에 안 든다."""
    graph = _writes_graph(tmp_path)

    buf = _writes_resolve(graph, tmp_path, "h->buf")
    assert _labelled(buf, "assignment to .buf") == [(15, 16, 17)]

    length = _writes_resolve(graph, tmp_path, "h->len")
    assert _labelled(length, "assignment to .len") == [(14, 15, 16)]


# 아래 다섯 핀은 `_WRITES_C`로는 안 죽는 변이를 문다(S13 적대적 리뷰 4 — 변이를 넣어 빨개짐을 봤다).

_PIN_A_C = """\
void f(void)
{
    char *p;
    p = 0;
    p[0] = 1;
    p = 0;
    p[1] = 2;
}
"""
_PIN_B_C = """\
void f(void)
{
    char *p;
    p = 0;
    p[0] = 1;
    p = 0;
    p[1] = 2;
    p[2] = 3;
}
"""


def test_label_4_ignores_a_same_named_function_in_another_file(tmp_path: Path):
    """`a.c`·`b.c`에 함수 `f`·변수 `p`가 같다. `b.c` 8행 쓰기가 `a.c` 8행(`}`)으로 새면 안 된다."""
    graph = _files_graph(tmp_path, {"a.c": _PIN_A_C, "b.c": _PIN_B_C})

    resolution = _writes_resolve(graph, tmp_path, "p", target_file="a.c")

    assert _labelled(resolution, "assignment in the same function") == [(4,), (5,), (6,), (7,)]


def test_label_4_interleaves_name_assigns_and_element_writes_by_line(tmp_path: Path):
    """`p = 0;`(4·6행, `assigns`)과 `p[i] = …`(5·7행, `writes`)가 번갈아 온다 — 줄 순이다."""
    graph = _files_graph(tmp_path, {"a.c": _PIN_A_C})

    resolution = _writes_resolve(graph, tmp_path, "p", target_file="a.c")

    assert _labelled(resolution, "assignment in the same function") == [(4,), (5,), (6,), (7,)]


def test_label_3_drops_a_field_based_write_on_the_same_line_as_a_field_assign(tmp_path: Path):
    """4행 `h->buf = q; h->buf[0] = 1;` — 필드 대입과 필드 기저 쓰기가 한 줄이다. 조각은 하나."""
    source = (
        "struct hdr { char *buf; };\n"
        "void g(struct hdr *h, char *q)\n"
        "{\n"
        "    h->buf = q; h->buf[0] = 1;\n"
        "}\n"
    )
    graph = _files_graph(tmp_path, {"c.c": source})

    resolution = _writes_resolve(graph, tmp_path, "h->buf", target_file="c.c")

    assert _labelled(resolution, "assignment to .buf") == [(3, 4, 5)]


def test_writes_available_is_true_for_a_v3_index_with_no_element_writes(tmp_path: Path):
    """`writes` 표가 **빈 채로** 있다(`{}`) — 인덱스는 원소 쓰기를 알고 그것이 0건이다. 비었다고
    `False`로 읽으면 「인덱스가 모른다」로 오독된다."""
    source = "void k(void)\n{\n    int n;\n    n = 1;\n}\n"
    graph = _files_graph(tmp_path, {"d.c": source})
    assert graph.table("writes") == {}

    resolution = _writes_resolve(graph, tmp_path, "n", target_file="d.c")

    assert _labelled(resolution, "assignment in the same function") == [(4,)]
    assert resolution.writes_available is True


def test_label_13_puts_name_assigns_before_element_writes_on_different_lines(tmp_path: Path):
    """`g_p[0] = 1;`(4행)이 `g_p = q;`(8행)보다 앞인데 이름 대입이 먼저 실린다 — `writes`가 없는
    인덱스(v2)의 출력이 v3 출력의 앞부분으로 남는 순서다(`_resolve_global_assignment` 주석)."""
    source = (
        "char *g_p;\nvoid a(void)\n{\n    g_p[0] = 1;\n}\nvoid b(char *q)\n{\n    g_p = q;\n}\n"
    )
    graph = _files_graph(tmp_path, {"e.c": source})

    resolution = _writes_resolve(graph, tmp_path, "g_p", target_file="e.c")

    assert _labelled(resolution, "assignment to g_p") == [(7, 8, 9), (3, 4, 5)]


def test_label_13_keeps_same_line_writes_in_different_files(tmp_path: Path):
    """`x.c`·`y.c` 4행에 같은 전역 쓰기 `g_p[0] = 1;`이 있다 — 중복 제거 키는 (파일, 줄)이라
    조각이 둘이다. 줄만으로 거르면 둘째 파일의 쓰기가 사라진다."""
    x = "char *g_p;\nvoid a(void)\n{\n    g_p[0] = 1;\n}\n"
    y = "extern char *g_p;\nvoid b(void)\n{\n    g_p[0] = 1;\n}\n"
    graph = _files_graph(tmp_path, {"x.c": x, "y.c": y})

    resolution = _writes_resolve(graph, tmp_path, "g_p", target_file="x.c")

    blocks = [b for b in resolution.blocks if b.label == "assignment to g_p"]
    assert [(b.file, b.lines) for b in blocks] == [("x.c", (3, 4, 5)), ("y.c", (3, 4, 5))]


def test_a_v2_index_keeps_the_old_output_and_says_writes_are_unavailable(tmp_path: Path):
    """v2 문서(`writes` 없음 — #304 전 이미지의 산출물)에서는 대입 라벨이 예전 그대로 이름·필드
    대입만 내고 `writes_available=False`로 그 공백을 적는다. v3 이상 문서와 **같은 소스**라 차이는
    `writes` 표 하나다."""
    v2 = _writes_graph(tmp_path, version="index-v2")
    assert v2.table("writes") is None
    assert v2.table("assigns")  # v2 표는 그대로 읽힌다

    tmp = _writes_resolve(v2, tmp_path, "tmp")
    assert _labelled(tmp, "declaration (local)") == [(9,)]
    assert _labelled(tmp, "assignment in the same function") == []
    assert tmp.writes_available is False
    assert tmp.index_available is True  # 색인은 있다 — 없는 것은 `writes` 표 하나다

    assert _labelled(_writes_resolve(v2, tmp_path, "k"), "assignment in the same function") == [
        (12,)
    ]
    assert _labelled(_writes_resolve(v2, tmp_path, "p"), "assignment in the same function") == [
        (28,)
    ]
    assert _labelled(_writes_resolve(v2, tmp_path, "g_tbl"), "assignment to g_tbl") == []
    assert _labelled(_writes_resolve(v2, tmp_path, "h->buf"), "assignment to .buf") == []


def test_writes_available_is_false_without_any_index():
    resolution = resolve(
        _query(requests=(_request("variable", "x"),), target_file="a.c"),
        table=lambda _name: None,
        read_lines=lambda _file: [],
    )

    assert resolution.writes_available is False


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


def test_resolve_carries_the_already_in_source_line_into_the_followup_alongside_a_kept_block(
    ne2_graph, ne2_read_lines
):
    """§4.4.4 — `g_buf`의 선언 줄만 이미 보이면(대입 줄은 안 보임) 선언은 `already`로 빠지고
    대입은 블록으로 남는다. 둘 다 후속 턴에 실려야 한다 — 조각이 먼저, Already 줄이 뒤다."""
    query = _query(
        requests=(_request("variable", "g_buf"),),
        target_file="main.c",
        visible={"main.c": frozenset({13})},
    )

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert [b.label for b in resolution.blocks] == ["assignment to g_buf"]
    assert resolution.already == ("Already in SOURCE: variable:g_buf at main.c:L13-13.",)
    fragment_marker = "/* --- retrieved: g_buf (assignment to g_buf) --- */"
    already_line = "Already in SOURCE: variable:g_buf at main.c:L13-13."
    assert fragment_marker in resolution.followup_text
    assert already_line in resolution.followup_text
    assert resolution.followup_text.index(fragment_marker) < resolution.followup_text.index(
        already_line
    )


def test_resolve_still_sends_a_followup_when_every_request_is_already_in_source(
    ne2_graph, ne2_read_lines
):
    """§4.4.4 — 요청이 전부 이미 보이면 `blocks`는 비지만, Already 알림은 후속 턴에 실려야
    한다. 빈 후속 턴을 내보내면 모델이 왜 아무것도 안 왔는지 모른다."""
    query = _query(
        requests=(_request("variable", "g_buf"),),
        target_file="main.c",
        visible={"main.c": frozenset({13, 36, 37, 38})},
    )

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert resolution.blocks == ()
    assert resolution.already == (
        "Already in SOURCE: variable:g_buf at main.c:L13-13.",
        "Already in SOURCE: variable:g_buf at main.c:L36-38.",
    )
    assert resolution.followup_text != ""
    assert "## Retrieved context (hop 1)" in resolution.followup_text
    assert "Already in SOURCE: variable:g_buf at main.c:L13-13." in resolution.followup_text
    assert "Already in SOURCE: variable:g_buf at main.c:L36-38." in resolution.followup_text


def test_resolve_keeps_already_lines_inside_the_budget(ne2_graph, ne2_read_lines):
    """Already 줄도 `max_chars`에 든다 — 전역 대입이 전부 SOURCE와 겹치면 요청 하나가 Already
    줄을 수십 개 낼 수 있어서다(#305 리뷰). 넘친 줄은 수만 한 줄로 남기고 `already`에는 다 둔다."""
    first = "Already in SOURCE: variable:g_buf at main.c:L13-13."
    second = "Already in SOURCE: variable:g_buf at main.c:L36-38."
    query = _query(
        requests=(_request("variable", "g_buf"),),
        target_file="main.c",
        visible={"main.c": frozenset({13, 36, 37, 38})},
        max_chars=len(first) + 2,
    )

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert resolution.already == (first, second)
    assert first in resolution.followup_text
    assert second not in resolution.followup_text
    assert "(+1 more Already in SOURCE line omitted for budget.)" in resolution.followup_text


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
    `_unfilled_lookup_reason`이 사유를 고른다. `ghost` 자신은 후보에서
    뺀다(`_all_symbol_names`가 표의 키를 그대로 모아 렌더 성공 여부를 안 보므로, 빼지
    않으면 "가장 비슷한 이름"이 자기 자신이 되어 사유가 무의미해진다). `ghosts`처럼
    실제로 비슷한 다른 이름이 있으면 그것이 나와야 한다. "찾았지만 못 읽었다"를 구분하는
    아홉 번째 사유는 명세 8개에 없어 만들지 않았다 — ponytail: 이 정도로 조용하지 않은
    것으로 충분하다.
    """

    def stale_table(name: str):
        if name == "functions":
            return {
                "ghost": [{"file": "missing.c", "line_start": 1, "line_end": 1, "params": []}],
                "ghosts": [{"file": "main.c", "line_start": 1, "line_end": 1, "params": []}],
            }
        return {}

    query = _query(requests=(_request("function", "ghost"),), target_file="main.c")

    resolution = resolve(query, table=stale_table, read_lines=ne2_read_lines)

    assert resolution.blocks == ()
    assert len(resolution.unfilled) == 1
    assert resolution.unfilled[0].reason == UnfilledReason.CLOSEST_NAME
    assert resolution.unfilled[0].text == (
        "Not resolved: function:ghost (closest name in repository: ghosts)."
    )


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


def test_resolve_processes_only_the_first_three_requests(ne2_graph, ne2_read_lines):
    """요청 개수 상한. v3 prefix 세 좌석 다 모델에게 최대 3개까지만 허락한다
    (`prompts/scout_memory/v3/prefix.md:146` 「at most 3 actionable requests」·
    `scout_injection/v3/prefix.md:146` 같은 문구·`scout_boundary/v3/prefix.md:135`
    「always present, at most 3 entries」 — 문구는 갈려도 숫자는 셋 다 3). 상한이 없으면
    존재하지 않는 target 500개에 1.1초·`followup_text` 44,800자였다 — resolve()는 앞 3개만 본다."""
    targets = [f"no_such_symbol_{i}" for i in range(5)]
    query = _query(
        requests=tuple(_request("function", t) for t in targets),
        target_file="main.c",
    )

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert [item.target for item in resolution.unfilled] == targets[:3]
    assert targets[3] not in resolution.followup_text
    assert targets[4] not in resolution.followup_text


def test_resolve_prefers_the_function_in_the_target_file_when_names_collide(
    ne2_graph, ne2_read_lines
):
    """명세 §4.4.3 동명 점수 — `init`이 `scoring/a_main.c`·`scoring/a_parser.c`(둘 다 function) ·
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


def test_resolve_prefers_the_macro_in_the_target_file_when_names_collide(ne2_graph, ne2_read_lines):
    """라벨 11(macro named in the declaration)의 동명 점수. `LIMIT`이
    `scoring/a_wrong.c`(target 아님·안 보임)와 `scoring/target.c`(target_file·visible) 둘에
    정의돼 있다. `cap`(target.c 5행)의 선언 줄에서 `LIMIT`을 찾으면 target.c의 정의가
    나와야 한다 — 위 함수 동명 충돌 테스트의 매크로 판."""
    query = _query(
        requests=(_request("variable", "cap"),),
        target_file="scoring/target.c",
        visible={"scoring/target.c": frozenset({5})},
    )

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    macro_blocks = [b for b in resolution.blocks if b.label == "macro named in the declaration"]
    assert [b.file for b in macro_blocks] == ["scoring/target.c"]


def test_resolve_truncates_blocks_that_do_not_fit_the_budget(ne2_graph, ne2_read_lines):
    """`max_chars`가 너무 작으면 블록을 통째로 빼고 이름을 `truncated`에 남긴다."""
    query = _query(requests=(_request("variable", "i"),), target_file="main.c", max_chars=1)

    resolution = resolve(query, table=ne2_graph.table, read_lines=ne2_read_lines)

    assert resolution.blocks == ()
    assert resolution.truncated == ("i", "i")  # 선언 + 같은 함수 대입, 둘 다 넘친다
    assert resolution.followup_text == ""  # 블록도 unfilled도 없으면 빈 문자열(§4)


# --- score_candidate / pick_best_candidate: 명세 §4.4.3 ----------------------


def test_pick_best_candidate_reproduces_the_spec_example_8_5_0():
    a = Candidate(definition_kind="function", file="src/main.c", line=10)
    b = Candidate(definition_kind="function", file="src/parser.c", line=20)
    c = Candidate(definition_kind="variable", file="src/other.c", line=30)
    visible = frozenset({"src/main.c", "src/parser.c"})

    def scored(candidate: Candidate) -> int:
        return score_candidate(
            candidate, requested_kind="function", target_file="src/main.c", visible_files=visible
        )

    assert scored(a) == 8
    assert scored(b) == 5
    assert scored(c) == 0

    winner = pick_best_candidate(
        [a, b, c], requested_kind="function", target_file="src/main.c", visible_files=visible
    )
    assert winner is a


def test_pick_best_candidate_breaks_ties_by_file_then_line():
    tied_a = Candidate(definition_kind="function", file="src/b.c", line=5)
    tied_b = Candidate(definition_kind="function", file="src/a.c", line=99)

    winner = pick_best_candidate(
        [tied_a, tied_b],
        requested_kind="function",
        target_file="nowhere.c",
        visible_files=frozenset(),
    )

    assert winner is tied_b  # 점수는 3:3 동점 — "src/a.c" < "src/b.c"라 tied_b가 이긴다.


def test_pick_best_candidate_returns_none_for_an_empty_list():
    winner = pick_best_candidate(
        [], requested_kind="function", target_file="x.c", visible_files=frozenset()
    )
    assert winner is None


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
