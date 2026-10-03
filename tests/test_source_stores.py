"""Auditor `SOURCE`의 `STORES` 블록 (`src/orchestrator/source_stores.py`, 옵션 `auditor_stores`).

검수에서 「이 길이·버퍼·필드는 **어디서** 정해지나」를 묻는 행이 있었다(근거와 크기는 모듈
docstring). 그 답(쓰기 지점)은 인덱스(`field_assigns`·`assigns`·`calls`)에 이미 있는데 Auditor
`SOURCE`가 안 썼다. 이 파일은 그 조각 중 **순수한 쪽**을 고정한다 — 어느 이름을 찾고(슬라이스
줄에서), 찾은 행을 어떻게 고르고(같은 파일 먼저 · 제품 경로 먼저 · 모호 표시 · 상한) 어떻게
적는가(`file:line | 원문 줄 | 술어`). 인덱스 쪽(`stores_of`)은 `tests/test_index_graph.py`가, 러너에
붙는 자리는 `tests/test_slice_targets.py`가 든다.

아래쪽 테스트는 **진짜 인덱서**가 쓴 index-v2를 `GraphIndex`로 읽어 이 모듈에 넣는다 — 표의 열을
추측하지 않았다는 대조다(`tests/test_indexer.py`의 같은 이름 경계 테스트와 같은 생각이다).
"""

import json
import re
from pathlib import Path

import pytest

from src.adapters.index_graph import GraphIndex
from src.orchestrator import source_stores
from src.orchestrator.source_stores import HEADER, stores_block
from src.schemas.candidate import SliceRef
from src.schemas.common import Location
from src.schemas.stores import StoreSite
from tests.test_indexer import indexer

# 두 파일의 원문. 아래 행 번호는 이 목록 그대로다(1부터).
_A_C = """\
#include <stdlib.h>
#include <string.h>

struct ctx { char *buf; size_t len; struct hdr { int len; } h; };
struct other { int len; };

static char *g_buf;
static size_t g_len = 0;

static void grow(struct ctx *c, size_t n)
{
    c->len = n * 2;
    c->buf = (char *)malloc(c->len + 1);
    g_buf = malloc(
        n + 1);
    g_len += n;
    c->h.len = 4;
}

int copy(struct ctx *c, const char *src, size_t n)
{
    char *local = malloc(n);
    size_t g_len = 3;
    local = NULL;
    memcpy(c->buf + c->len, src, n);
    return g_len;
}
"""
_B_C = """\
#include <stdlib.h>
static size_t g_len;
extern char *g_buf;
void other_fn(struct other *o)
{
    o->len = 5;
    g_len = 7;
    g_buf = calloc(1, 16);
}
"""

_COPY = Location(file="a.c", symbol="copy", line_start=20, line_end=27)
_GROW = Location(file="a.c", symbol="grow", line_start=10, line_end=18)


def _lines(text: str) -> list[str]:
    return text.split("\n")[:-1]


_FILES = {"a.c": _lines(_A_C), "b.c": _lines(_B_C)}


def _ref(lines: list[tuple[str, int]], entry: tuple[str, int] = ("a.c", 25)) -> SliceRef:
    """Case B 슬라이스 — entry는 sink 호출(`memcpy`) 줄이다."""
    file, line = entry
    return SliceRef(
        slice_id="s", location=Location(file=file, symbol="copy", line_start=line),
        given_end="sink", entry=Location(file=file, symbol="memcpy", line_start=line),
        lines=tuple(lines),
    )


def _site(file: str, line: int, name: str = "len", **kw) -> StoreSite:  # noqa: ANN003
    return StoreSite(
        file=file, line=line, func=kw.pop("func", "f"), name=name,
        field=kw.pop("field", True), **kw,
    )


def _run(
    sites: dict[tuple[str, bool], list[StoreSite]],
    lines: list[tuple[str, int]] | None = None,
    *,
    carried: tuple[Location, ...] = (_COPY,),
    room: int = 100_000,
    measure=len,  # noqa: ANN001
    files: dict[str, list[str]] | None = None,
    entry: tuple[str, int] = ("a.c", 25),
):  # noqa: ANN202
    """가짜 인덱스 조회(이름·필드 여부 → 행)로 블록을 만든다. 부른 대상은 `asked`에 쌓인다.
    `lines`가 `None`이면 sink 줄 하나, `[]`면 슬라이스 줄이 없는 슬라이스다."""
    texts = files or _FILES
    asked: list = []

    def stores_of(target):  # noqa: ANN001, ANN202
        asked.append(target)
        return sites(target) if callable(sites) else sites.get((target.name, target.field), [])

    def read(loc: Location) -> str:
        assert loc.line_start is None, "파일 통째로만 읽는다 — 줄 범위 읽기는 번호가 어긋날 수 있다"
        return "\n".join(texts[loc.file]) + "\n"

    stores = stores_block(
        _ref([("a.c", 25)] if lines is None else lines, entry), list(carried),
        read_source=read, stores_of=stores_of, measure=measure, room=room,
    )
    return stores, asked


_MORE = re.compile(r"/\* \.\.\. \+(\d+) more rows not shown \.\.\. \*/")


def _rows(stores) -> list[str]:  # noqa: ANN001
    """헤더와 끝의 `+N more` 줄을 뺀 데이터 행."""
    assert stores.text.startswith(HEADER + "\n"), stores.text
    return [line for line in stores.text.split("\n")[1:] if not _MORE.fullmatch(line)]


def _paths(stores) -> list[str]:  # noqa: ANN001
    """데이터 행의 `file:line` 앞머리."""
    return [row.split(" | ")[0] for row in _rows(stores)]


def _more_line(stores) -> str | None:  # noqa: ANN001
    """블록 끝의 `+N more rows not shown` 줄(없으면 `None`)."""
    last = stores.text.split("\n")[-1] if stores.text else ""
    return last if _MORE.fullmatch(last) else None


def test_a_field_write_becomes_a_row_with_its_source_line_and_a_predicate() -> None:
    """sink 줄 `memcpy(c->buf + c->len, …)`의 `len`이 다른 함수(grow)에서 쓰이는 곳 — 원문 줄과 한
    줄 술어. 이미 실린 함수(`copy`) 밖이라 행이 된다."""
    stores, _ = _run({("len", True): [_site("a.c", 12, func="grow", lhs="c->len")]})

    assert _rows(stores) == ["a.c:12 | c->len = n * 2; | writes ->len"]
    assert stores.rows == 1


def test_the_block_opens_with_its_own_banner() -> None:
    """`SOURCE`의 다른 블록(`/* ===== 파일:줄 ===== */`)과 모양은 같고 이름이 다르다 — 프롬프트가 이
    블록을 파일 범위 배너로 읽지 않게 한다."""
    stores, _ = _run({("len", True): [_site("a.c", 12, func="grow", lhs="c->len")]})

    assert HEADER.startswith("/* ===== STORES") and HEADER.endswith("===== */")
    assert stores.text.splitlines()[0] == HEADER


def test_the_header_says_what_an_unmarked_row_does_not_prove() -> None:
    """머리가 행을 읽는 법을 말한다 — `[ambiguous]`는 같은 구조체·변수라고 확인하지 못했다는 뜻이고,
    표시가 **없는** 행도 증명이 아니다(이름이 같은 직접 대입일 뿐 — 모듈 docstring의 한계). 문서만이
    아니라 모델이 보는 줄에 있어야 한다. 철자를 핀하는 테스트다."""
    assert "[ambiguous] = owner not confirmed" in HEADER
    assert "unmarked row is not proof" in HEADER


def test_a_plain_assignment_becomes_a_row() -> None:
    """파일 전역 `g_len`의 대입 — 연산자가 `+=`여도 원문 줄이 그것을 든다."""
    sites = {("g_len", False): [_site("a.c", 16, "g_len", func="grow", field=False)]}

    stores, _ = _run(sites, [("b.c", 7)], entry=("b.c", 7), carried=())

    assert _rows(stores) == ["a.c:16 | g_len += n; | assigns g_len"]


def test_an_allocation_row_shows_the_allocator_and_its_size_expression() -> None:
    """`c->buf = malloc(c->len + 1)` — 크기 식이 술어에 실린다. 연산자가 `=`면 덧붙이지 않는다."""
    allocation = _site(
        "a.c", 13, "buf", func="grow", lhs="c->buf", alloc="malloc", alloc_args=("c->len + 1",)
    )

    stores, _ = _run({("buf", True): [allocation]})

    assert _rows(stores) == [
        "a.c:13 | c->buf = (char *)malloc(c->len + 1); | allocates ->buf (malloc: c->len + 1)"
    ]


def test_a_two_argument_allocator_lists_both_arguments() -> None:
    allocation = _site(
        "b.c", 8, "g_buf", func="other_fn", field=False, alloc="calloc", alloc_args=("1", "16")
    )

    stores, _ = _run({("g_buf", False): [allocation]}, [("a.c", 25), ("a.c", 7)])

    assert _rows(stores)[0].endswith("| allocates g_buf (calloc: 1, 16)")


def test_a_compound_assignment_to_a_field_names_its_operator() -> None:
    """`b->x -= i`를 「x에 i를 넣었다」로 읽지 않게 한다(인덱서의 `op` 열)."""
    site = _site("a.c", 12, func="grow", lhs="c->len", op="+=")

    stores, _ = _run({("len", True): [site]})

    assert _rows(stores)[0].endswith("| writes ->len (+=)")


def test_a_member_of_a_value_struct_is_spelled_with_a_dot() -> None:
    site = _site("a.c", 17, func="grow", lhs="c->h.len")

    stores, _ = _run({("len", True): [site]})

    assert _rows(stores)[0].endswith("| writes .len")


def test_rows_of_the_same_file_come_before_other_files() -> None:
    """같은 파일 먼저, 그다음 저장소 전체 — 인덱스가 돌려준 순서와도, 파일 이름 순서와도, 좌변
    철자와도 무관하다. 이름을 본 곳이 b.c(`o->len`)이면, 철자가 같고 알파벳으로도 앞서는 a.c의
    쓰기보다 철자가 다른 b.c의 쓰기가 앞이다."""
    sites = {
        ("len", True): [
            _site("a.c", 12, func="grow", lhs="o->len"),
            _site("b.c", 6, func="other_fn", lhs="x->len"),
        ]
    }

    stores, _ = _run(sites, [("b.c", 6)], entry=("b.c", 6), carried=())

    assert [row.split(" | ")[0] for row in _rows(stores)] == ["b.c:6", "a.c:12"]


def test_an_ambiguous_row_is_marked_and_comes_after_the_unambiguous_one() -> None:
    """이름만으로 이어진 행은 `[ambiguous]`를 달고, 같은 파일·같은 철자·앞선 줄이어도 모호하지 않은
    행 뒤로 간다."""
    sites = {
        ("len", True): [
            _site("a.c", 12, func="grow", lhs="c->len", ambiguous=True),
            _site("a.c", 17, func="grow", lhs="x->len"),
        ]
    }

    stores, _ = _run(sites)

    assert _rows(stores) == [
        "a.c:17 | c->h.len = 4; | writes ->len",
        "a.c:12 | c->len = n * 2; | writes ->len [ambiguous]",
    ]


def test_among_ambiguous_rows_the_same_spelling_comes_first_ignoring_spaces() -> None:
    """모호한 행끼리는 좌변이 찾는 줄과 같은 철자인 쓰기가 앞이다(공백 무시) — 같은 객체일 법하다는
    약한 신호일 뿐이라 모호 표시는 그대로 남는다. 줄 번호 순(12 → 13)을 뒤집는 것으로 가려낸다."""
    sites = {
        ("len", True): [
            _site("a.c", 12, func="grow", lhs="p->len", ambiguous=True),
            _site("a.c", 13, func="grow", lhs="c -> len", ambiguous=True),
        ]
    }

    stores, _ = _run(sites)

    rows = _rows(stores)
    assert [row.split(" | ")[0] for row in rows] == ["a.c:13", "a.c:12"]
    assert all(row.endswith("[ambiguous]") for row in rows)


def test_a_flood_of_same_named_writes_across_structs_is_capped_and_listed() -> None:
    """같은 이름의 필드 쓰기가 구조체 넷·파일 넷에 열 곳이다(8행 초과) — 이름 키는 어느 것이 같은
    필드인지 못 가르므로 이름마다 `_PER_NAME`(4)행까지만 싣는다: 찾는 줄과 같은 파일(a.c) 먼저,
    그다음 같은 철자(`c->len`), 그다음 (파일, 줄). 못 실은 여섯은 **목록**으로 trace에 남는다."""
    files = {**_FILES, "b.c": ["x"] * 40, "c.c": ["y"] * 40, "d.c": ["z"] * 40}
    sites = [
        _site("a.c", 17, func="grow", lhs="c->h.len", ambiguous=True),
        _site("a.c", 12, func="grow", lhs="c->len", ambiguous=True),
        *[_site("b.c", n, func="f", lhs="q->len", ambiguous=True) for n in (10, 11, 12)],
        *[_site("c.c", n, func="g", lhs="r->len", ambiguous=True) for n in (5, 6, 7)],
        _site("d.c", 3, func="h", lhs="t->len", ambiguous=True),
        _site("d.c", 4, func="h", lhs="t->len", ambiguous=True),
    ]
    assert len(sites) == 10 > 8

    stores, _ = _run({("len", True): sites}, files=files)

    rows = _rows(stores)
    assert [row.split(" | ")[0] for row in rows] == ["a.c:12", "a.c:17", "b.c:10", "b.c:11"]
    assert all(row.endswith("[ambiguous]") for row in rows)
    assert stores.rows == 4 == source_stores._PER_NAME
    assert stores.dropped == (
        "b.c:12 len", "c.c:5 len", "c.c:6 len", "c.c:7 len", "d.c:3 len", "d.c:4 len"
    )
    fields = stores.trace_fields()
    assert (fields["stores"], fields["stores_dropped"]) == (4, 6)
    assert json.loads(str(fields["stores_dropped_rows"])) == list(stores.dropped)
    assert _more_line(stores) == source_stores._more(6)


def test_the_same_name_seen_in_two_files_shares_one_cap() -> None:
    """슬라이스가 `len`을 두 파일(a.c·b.c)에서 쓰면 이름은 파일마다 한 번씩 물리지만 상한은 **이름**
    하나의 것이다 — 두 조회가 서로 다른 쓰기를 돌려줘도(예: 한쪽에서는 지역이라 건너뛴 변수)
    파일마다 4행씩 8행이 되지 않는다."""
    files = {**_FILES, "c.c": ["y"] * 40, "d.c": ["z"] * 40}

    def by_file(target):  # noqa: ANN001, ANN202
        other = {"a.c": "c.c", "b.c": "d.c"}[target.file]
        return [_site(other, n, func="g", lhs="r->len", ambiguous=True) for n in range(1, 5)]

    def only_len(target):  # noqa: ANN001, ANN202
        return by_file(target) if (target.name, target.field) == ("len", True) else []

    stores, asked = _run(only_len, [("a.c", 25), ("b.c", 6)], files=files)

    assert len([t for t in asked if t.name == "len"]) == 2
    assert stores.rows == source_stores._PER_NAME == 4
    assert stores.dropped == ("d.c:1 len", "d.c:2 len", "d.c:3 len", "d.c:4 len")


def test_a_write_inside_a_function_the_source_already_carries_is_not_repeated() -> None:
    """이미 실린 함수 본문에 있는 쓰기는 또 적지 않는다(`definitions`·피호출 단계가 겹침을 거르는
    것과 같다). 센다 — 「못 찾았다」와 「이미 보인다」가 갈려야 한다."""
    sites = {("len", True): [_site("a.c", 12, func="grow", lhs="c->len")]}

    stores, _ = _run(sites, carried=(_COPY, _GROW))

    assert stores.text == "" and stores.rows == 0
    assert stores.in_source == 1


def test_a_missing_index_gives_no_block_and_is_asked_once() -> None:
    """`stores_of`가 `None`(인덱스 없음·v1)이면 블록도 행도 없다 — 예외도 아니다. 이름마다 다시 묻지
    않는다(없는 인덱스에 수백 번 묻는 일이 없게)."""
    asked: list = []

    def none(target):  # noqa: ANN001, ANN202
        asked.append(target)

    stores = stores_block(
        _ref([("a.c", 25)]), [_COPY], read_source=lambda _loc: _A_C,
        stores_of=none, measure=len, room=1_000,
    )

    assert (stores.text, stores.rows, stores.dropped) == ("", 0, ())
    assert len(asked) == 1


def test_stores_zero_tells_a_missing_index_from_a_lookup_that_found_nothing() -> None:
    """`stores: 0`은 셋이다 — 인덱스가 없어(v1·`stores_of`가 `None`) **못 본** 것 · 인덱스에
    물었는데 쓰기가 없는 것 · 물을 이름이 슬라이스 줄에 없는 것. 「0건」과 「못 봤다」를 섞지
    않게 `stores_lookup`이 가른다(`closure_lookup`과 같은 계약 — 값은 `index`·`none`에 하나 더
    `no_targets`다)."""
    absent, _ = _run(lambda _target: None)
    found_nothing, _ = _run({})
    no_names, _ = _run({}, [], files={"a.c": ["#include <a.h>"]}, entry=("a.c", 1))

    assert [s.rows for s in (absent, found_nothing, no_names)] == [0, 0, 0]
    assert absent.trace_fields()["stores_lookup"] == "none"
    assert found_nothing.trace_fields()["stores_lookup"] == "index"
    assert no_names.trace_fields()["stores_lookup"] == "no_targets"


def test_the_same_write_reached_through_two_files_is_one_row() -> None:
    """`len`을 a.c와 b.c에서 둘 다 보면 이름은 파일마다 한 번씩 물어 같은 쓰기가 두 번 돌아온다 —
    행은 하나이고 이미 실린 함수 안이라 건너뛴 것도 한 번만 센다."""
    sites = {("len", True): [_site("a.c", 12, func="grow", lhs="c->len")]}

    stores, asked = _run(sites, [("a.c", 25), ("b.c", 6)])

    assert len([t for t in asked if t.name == "len"]) == 2
    assert stores.rows == 1 and stores.dropped == ()
    covered, _ = _run(sites, [("a.c", 25), ("b.c", 6)], carried=(_COPY, _GROW))
    assert covered.in_source == 1


def test_a_carried_location_without_lines_covers_its_whole_file() -> None:
    """줄 범위가 없는 실린 위치(파일 통째)는 그 파일의 모든 줄을 덮는다."""
    sites = {("len", True): [_site("a.c", 12, func="grow", lhs="c->len")]}

    stores, _ = _run(sites, carried=(Location(file="a.c"),))

    assert stores.in_source == 1 and stores.rows == 0


def test_each_file_is_read_once_and_windows_line_endings_are_tolerated() -> None:
    """파일은 한 번만 읽는다 — 같은 파일의 행·슬라이스 줄이 여럿이어도. CRLF 원문의 줄 번호는
    그대로이고 줄 끝의 `\\r`은 행에 안 남는다."""
    reads: list[str] = []
    crlf = _A_C.replace("\n", "\r\n")

    def read(loc: Location) -> str:
        reads.append(loc.file)
        return crlf

    sites = [
        _site("a.c", 12, func="grow", lhs="c->len"),
        _site("a.c", 17, func="grow", lhs="c->h.len"),
    ]
    stores = stores_block(
        _ref([("a.c", 25), ("a.c", 24)]), [_COPY], read_source=read,
        stores_of=lambda t: sites if (t.name, t.field) == ("len", True) else [],
        measure=len, room=10_000,
    )

    assert reads == ["a.c"]
    assert _rows(stores)[0] == "a.c:12 | c->len = n * 2; | writes ->len"
    assert "\r" not in stores.text


def test_names_come_from_the_slice_lines_nearest_the_entry_first() -> None:
    """찾는 이름의 순서 — entry 줄이 먼저, 그다음 entry에 가까운 줄. 상한이 먼저 자르는 것은 먼
    쪽이다. 같은 줄 안에서는 적힌 순서다(sink 인자 순서)."""
    _, asked = _run({}, [("a.c", 22), ("a.c", 24), ("a.c", 25), ("a.c", 26)])

    names = [target.name for target in asked if target.name not in {"return", "NULL", "char"}]
    assert names[:4] == ["buf", "len", "src", "n"]
    # 24줄과 26줄은 entry에서 같은 거리(1)다 — 같으면 줄 번호 순이라 24줄의 `local`이 26줄의
    # `g_len`보다 앞.
    assert names.index("local") < names.index("g_len")
    assert [t.field for t in asked if t.name in {"buf", "len"}] == [True, True]


def test_a_name_is_looked_up_once_per_file() -> None:
    """같은 이름이 여러 줄에 나와도 한 번만 묻는다 — 널리 쓰이는 필드는 행이 수천이다."""
    _, asked = _run({}, [("a.c", 22), ("a.c", 25), ("a.c", 26)])

    assert sum(1 for t in asked if (t.name, t.field) == ("n", False)) == 1


def test_a_lookup_carries_the_spelling_and_the_line_it_was_seen_on() -> None:
    """인덱스가 모호 판정에 쓰는 철자(`lhs`)와 지역 판정에 쓰는 줄·파일."""
    _, asked = _run({})

    (length,) = [t for t in asked if t.name == "len"]
    assert (length.field, length.file, length.line, length.lhs) == (True, "a.c", 25, "c->len")
    (src,) = [t for t in asked if t.name == "src"]
    assert (src.field, src.line, src.lhs) == (False, 25, None)


def test_comments_and_string_literals_name_nothing() -> None:
    files = {"a.c": ['    log("c->secret"); /* p->hidden */ // q->gone', "    x = y;"]}

    _, asked = _run({}, [("a.c", 1)], files=files, entry=("a.c", 1))

    assert "secret" not in {t.name for t in asked}
    assert not {"hidden", "gone"} & {t.name for t in asked}
    assert "log" not in {t.name for t in asked}  # 호출 대상은 변수가 아니다


def test_a_digit_separator_does_not_open_a_char_literal() -> None:
    """C23·C++14 자릿수 구분자(`1'000` · `0xFF'FF`)의 `'`는 문자 리터럴이 아니다 — 열면 같은
    줄의 다음 `'`까지, 그 사이의 쓰기(`c->len`)까지 삼킨다. 수의 꼬리(`0xFF'FF`의 `FF`)는
    변수 이름이 아니다. 진짜 문자 리터럴(`'x'`)은 여전히 이름을 안 만든다."""
    files = {"a.c": [
        "    n = 1'000; c->len = 2'000;",
        "    m = 0xFF'FF; d->cap = 1'000'000;",
        "    k = 'x'; e->after = 1;",
    ]}

    _, asked = _run({}, [("a.c", 1), ("a.c", 2), ("a.c", 3)], files=files, entry=("a.c", 1))

    names = {(t.name, t.field) for t in asked}
    assert {("len", True), ("cap", True), ("after", True)} <= names
    assert {("FF", False), ("x", False)}.isdisjoint(names)


def test_preprocessor_lines_and_numeric_literals_name_nothing() -> None:
    """`#include <a.h>`의 `a.h`는 필드 접근이 아니고, `0x1F`·`1e5`의 꼬리는 식별자가 아니다."""
    files = {"a.c": ["#include <a.h>", "    n = 0x1F + 1e5 + k;"]}

    _, asked = _run({}, [("a.c", 1), ("a.c", 2)], files=files, entry=("a.c", 2))

    names = {t.name for t in asked}
    assert "h" not in names and "x1F" not in names and "e5" not in names
    assert {"n", "k"} <= names


def test_a_very_long_line_is_scanned_only_up_to_a_bound() -> None:
    """압축된 줄이 정규식을 오래 돌리지 않게 — 한계 뒤의 이름은 찾지 않는다."""
    files = {"a.c": ["    early = 1;" + " " * source_stores._SCAN_CHARS + "late = 2;"]}

    _, asked = _run({}, [("a.c", 1)], files=files, entry=("a.c", 1))

    assert {t.name for t in asked} == {"early"}


def test_a_designated_initializer_field_is_not_a_variable() -> None:
    """`.len = n`의 `len`은 지정 초기화의 필드다 — 변수 `len`을 묻지 않는다."""
    files = {"a.c": ["    struct s v = { .len = n };"]}

    _, asked = _run({}, [("a.c", 1)], files=files, entry=("a.c", 1))

    assert ("len", False) not in {(t.name, t.field) for t in asked}


def test_a_field_name_is_looked_up_once_per_file_even_with_two_spellings() -> None:
    """필드는 철자(`a->len`·`b->len`)가 달라도 이름 하나로 묻는다 — 첫(가장 가까운) 철자를 든다."""
    files = {"a.c": ["    a->len = 1;", "    b->len = 2;"]}

    _, asked = _run({}, [("a.c", 1), ("a.c", 2)], files=files, entry=("a.c", 1))

    (length,) = [t for t in asked if t.name == "len"]
    assert length.lhs == "a->len"


def test_each_name_shows_at_most_a_few_rows_and_the_rest_are_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이름마다 상한(`_PER_NAME`) — 흔한 필드 하나가 블록을 다 안 먹게. 넘친 행은 **목록**이다."""
    monkeypatch.setattr(source_stores, "_PER_NAME", 2)
    many = [_site("b.c", n, func="g", lhs="o->len") for n in (20, 21, 22, 23)]
    files = {**_FILES, "b.c": ["x"] * 30}

    stores, _ = _run({("len", True): many}, files=files)

    assert stores.rows == 2
    assert stores.dropped == ("b.c:22 len", "b.c:23 len")


def test_rows_the_block_cap_cuts_are_dropped_and_listed_in_the_trace_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """블록의 글자 상한. 못 실은 행은 개수가 아니라 `file:line 이름` 목록으로 trace에 남는다 —
    「0건」과 「잘렸다」가 갈린다."""
    monkeypatch.setattr(source_stores, "_PER_NAME", 50)
    # 헤더 + 첫 행(39자) + 끝의 `+N more` 줄 자리만 들어가는 폭 — 둘째 행부터는 어느 것도 안 든다
    # (61·47·35자). 못 실은 행이 생기면 그 줄이 붙으므로 자리를 미리 뺀다.
    first = "a.c:12 | c->len = n * 2; | writes ->len"
    monkeypatch.setattr(
        source_stores, "_STORES_CHARS", len("\n".join([HEADER, first, source_stores._more(4)]))
    )
    sites = [_site("a.c", n, func="grow", lhs="c->len") for n in (12, 13, 14, 16)]

    stores, _ = _run({("len", True): sites})

    assert stores.rows == 1
    assert _rows(stores) == [first]
    assert _more_line(stores) == source_stores._more(3)
    assert len(stores.text) <= source_stores._STORES_CHARS
    assert stores.dropped == ("a.c:13 len", "a.c:14 len", "a.c:16 len")
    fields = stores.trace_fields()
    assert fields["stores"] == 1 and fields["stores_dropped"] == 3
    assert json.loads(str(fields["stores_dropped_rows"])) == list(stores.dropped)


def test_the_dropped_list_in_the_trace_is_bounded_but_the_count_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_stores, "_PER_NAME", 50)
    monkeypatch.setattr(source_stores, "_STORES_CHARS", 1)
    monkeypatch.setattr(source_stores, "_DROPPED_LISTED", 2)
    sites = [_site("a.c", n, func="grow", lhs="c->len") for n in (12, 13, 14, 16)]

    stores, _ = _run({("len", True): sites})

    fields = stores.trace_fields()
    assert fields["stores_dropped"] == 4
    assert json.loads(str(fields["stores_dropped_rows"])) == ["a.c:12 len", "a.c:13 len", "+2 more"]


def test_the_block_must_fit_the_rooms_unit_not_only_its_own_char_cap() -> None:
    """`room`은 `SOURCE` 상한의 남은 몫이다 — 글자 상한이 아니라 `measure`(토큰일 수도 있다)로 잰다.
    단어 수로 재면 헤더만으로 이미 모자란다."""
    sites = {("len", True): [_site("a.c", 12, func="grow", lhs="c->len")]}

    stores, _ = _run(sites, room=3, measure=lambda text: len(text.split()))

    assert stores.rows == 0 and stores.text == ""
    assert stores.dropped == ("a.c:12 len",)


def test_a_statement_that_continues_on_the_next_line_shows_that_line_too() -> None:
    """`g_buf = malloc(` 다음 줄에 크기 식이 있다 — 괄호가 안 닫히면 다음 줄 하나(±1줄)만 잇는다."""
    sites = {("g_buf", False): [_site("a.c", 14, "g_buf", func="grow", field=False)]}

    stores, _ = _run(sites, [("a.c", 25), ("a.c", 7)])

    assert _rows(stores)[0].startswith("a.c:14 | g_buf = malloc( n + 1); |")


def test_a_very_long_source_line_is_cut() -> None:
    files = {**_FILES, "b.c": ["x = " + "y" * 400 + ";"]}
    sites = {("len", True): [_site("b.c", 1, func="g", lhs="o->len")]}

    stores, _ = _run(sites, files=files)

    (row,) = _rows(stores)
    text = row.split(" | ")[1]
    assert len(text) == source_stores._LINE_CHARS and text.endswith("...")


def test_a_row_whose_line_cannot_be_read_is_dropped_not_invented() -> None:
    """인덱스는 있다고 하는데 그 줄이 사본에 없다(drift) — 빈 행을 지어내지 않고 목록에 남긴다."""
    sites = {("len", True): [_site("b.c", 99, func="g", lhs="o->len")]}

    stores, _ = _run(sites)

    assert stores.rows == 0 and stores.dropped == ("b.c:99 len",)


def test_targets_past_the_bound_are_counted_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_stores, "_TARGETS", 2)
    files = {"a.c": ["    a = b + c + d;"]}

    stores, asked = _run({}, [("a.c", 1)], files=files, entry=("a.c", 1))

    assert len(asked) == 2 and stores.skipped_targets == 2
    assert stores.trace_fields()["stores_targets_skipped"] == 2


def test_the_entry_line_is_scanned_even_when_the_slice_lists_no_lines() -> None:
    """entry 줄은 슬라이스의 일부다 — `_auditor_source`가 그 줄을 늘 anchor 안 줄로 세는 것과 같다.
    버린 것이 없으면 목록은 빈 JSON 배열이다(「없다」가 아니라 「0건」이다)."""
    stores, asked = _run({}, [])

    assert {t.name for t in asked} >= {"buf", "len", "src", "n"}
    assert stores.text == "" and stores.trace_fields()["stores_dropped_rows"] == "[]"


# zlib에서 잰 모양 — 찾는 줄(deflate.c)의 필드 `len`에 같은 이름의 쓰기가 제품 파일(inflate.c)과
# 예제(examples/zran.c) 둘에 있는데, 경로 순으로는 예제가 앞선다.
_PRODUCT_FILES = {**_FILES, "examples/zran.c": ["z"] * 120, "inflate.c": ["i"] * 170}


def _example_and_product(**kw) -> list[StoreSite]:  # noqa: ANN003
    return [
        _site("examples/zran.c", 110, func="main", lhs="s->len", nonproduct=True, **kw),
        _site("inflate.c", 164, func="inflate", lhs="s->len", **kw),
    ]


def test_product_rows_come_before_example_rows_even_when_the_example_sorts_first() -> None:
    """경로 순으로는 `examples/zran.c`가 `inflate.c`보다 앞이다 — 그래도 제품 쓰기가 먼저다."""
    stores, _ = _run({("len", True): _example_and_product()}, files=_PRODUCT_FILES)

    assert _paths(stores) == ["inflate.c:164", "examples/zran.c:110"]


def test_an_example_row_does_not_push_a_product_row_out_of_the_name_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이름마다 행 수 상한이 있어(여기선 1) 앞선 행만 남는다 — 예제 행이 앞서면 제품 행이 잘린다.
    제품 행이 남고 예제 행은 **목록**으로 간다."""
    monkeypatch.setattr(source_stores, "_PER_NAME", 1)

    stores, _ = _run({("len", True): _example_and_product()}, files=_PRODUCT_FILES)

    assert [row.split(" | ")[0] for row in _rows(stores)] == ["inflate.c:164"]
    assert stores.dropped == ("examples/zran.c:110 len",)


def test_a_same_file_row_still_comes_before_a_product_row_of_another_file() -> None:
    """같은 파일 먼저가 제품 경로 먼저보다 앞선다 — 비제품 쓰기(`nonproduct`)여도 찾는 줄과 같은
    파일이면 다른 파일의 제품 쓰기보다 앞에 온다(찾는 줄이 비제품 파일이면 어댑터는 `nonproduct`를
    안 달지만, 이 순서가 그것과 무관하게 서 있어야 한다)."""
    use = ["    use(s->len);" if n == 100 else "z" for n in range(1, 121)]
    files = {"examples/zran.c": use, "inflate.c": ["i"] * 170}
    sites = [
        _site("inflate.c", 164, func="inflate", lhs="s->len"),
        _site("examples/zran.c", 110, func="main", lhs="s->len", nonproduct=True),
    ]

    stores, _ = _run(
        {("len", True): sites}, [("examples/zran.c", 100)], files=files,
        entry=("examples/zran.c", 100), carried=(),
    )

    assert _paths(stores) == ["examples/zran.c:110", "inflate.c:164"]


def test_the_block_ends_with_a_more_line_only_when_rows_were_dropped() -> None:
    """못 실은 행이 없으면 줄이 안 붙는다. 있으면(개수 상한이든 크기 상한이든) 끝에 한 줄 — 모델이
    행 목록을 「전부」로 읽지 않게."""
    one, _ = _run({("len", True): [_site("a.c", 12, func="grow", lhs="c->len")]})

    assert _more_line(one) is None and len(_rows(one)) == 1


def test_the_more_line_takes_its_room_from_the_budget_so_the_block_never_overflows() -> None:
    """못 실은 행이 생기면 붙는 `+N more` 줄의 자리를 미리 뺀다 — 행 하나가 줄 없이는 딱 들어가도
    줄까지 넣으면 넘치는 몫에서는 그 행이 못 실리고, 몫이 그 줄만큼 늘면 실린다."""
    sites = [
        _site("a.c", 12, func="grow", lhs="c->len"),
        _site("a.c", 13, func="grow", lhs="c->len"),  # 첫 행보다 길다
    ]
    first = "a.c:12 | c->len = n * 2; | writes ->len"
    with_more = len("\n".join([HEADER, first, source_stores._more(1)]))

    tight, _ = _run({("len", True): sites}, room=with_more - 1)
    exact, _ = _run({("len", True): sites}, room=with_more)

    assert tight.text == "" and tight.rows == 0
    assert _rows(exact) == [first] and _more_line(exact) == source_stores._more(1)
    assert len(exact.text) == with_more


def test_a_direct_allocation_is_called_one_but_a_call_nested_on_the_line_is_not() -> None:
    """할당기 호출은 인덱스가 **줄 단위**로 이은 것이다 — 문장의 우변이 그 호출일 때만
    `allocates`다. `x = f(malloc(n))`·삼항·캐스트가 낀 모양은 가른다: 캐스트는 직접, 나머지는 「같은
    줄에 호출이 있다」고만 쓴다."""
    lines = [
        "    c->buf = (char *)malloc(n);",
        "    c->buf = wrap(malloc(n));",
        "    c->buf = ok ? malloc(n) : NULL;",
        "    if (c->buf == malloc(n)) c->buf = NULL;",
    ]
    files = {**_FILES, "m.c": lines}
    alloc = {"alloc": "malloc", "alloc_args": ("n",), "lhs": "c->buf"}
    sites = [_site("m.c", n, "buf", func="f", **alloc) for n in (1, 2, 3, 4)]

    stores, _ = _run({("buf", True): sites}, [("a.c", 25), ("a.c", 22)], files=files)

    predicates = [row.rsplit(" | ", 1)[1] for row in _rows(stores)]
    assert predicates == [
        "allocates ->buf (malloc: n)",
        "writes ->buf; same line calls malloc(n)",
        "writes ->buf; same line calls malloc(n)",
        "writes ->buf; same line calls malloc(n)",
    ]


def test_the_allocation_check_reads_the_whole_statement_not_the_cut_row_text() -> None:
    """행의 원문 줄은 `_LINE_CHARS`에서 잘리지만 직접 대입 판정은 자르기 전 문장으로 한다 — 긴
    캐스트 뒤의 `malloc(`도 직접 대입이다."""
    cast = "u" * (source_stores._LINE_CHARS + 40)
    files = {**_FILES, "m.c": [f"    p = ({cast} *)malloc(n);"]}
    site = _site("m.c", 1, "p", func="f", field=False, alloc="malloc", alloc_args=("n",))

    stores, _ = _run({("p", False): [site]}, [("a.c", 25), ("m.c", 1)], files=files)

    (row,) = _rows(stores)
    assert row.split(" | ")[1].endswith("...")
    assert row.endswith("| allocates p (malloc: n)")


def test_an_allocation_on_the_same_line_belongs_to_the_write_it_is_assigned_to() -> None:
    """인덱서는 같은 줄의 할당기 호출을 그 줄의 **모든** 쓰기 행에 붙인다. 문장이 둘이면
    (`a->len = n; a->buf = malloc(n);`) 호출은 그 호출이 오른쪽인 쓰기(`buf`)의 것이고 `len`은
    같은 줄에 호출이 있다고만 쓴다 — 줄 전체에서 `= malloc(`을 찾으면 `len`이 할당을 받는
    것으로 읽힌다. 변수도 같다. 한 줄에서 두 객체가 같은 필드를 쓰면 좌변 철자가 가른다."""
    files = {**_FILES, "m.c": [
        "    a->len = n; a->buf = malloc(n);",
        "    size = n; p = (char *)malloc(n);",
        "    a->buf = NULL; b->buf = malloc(n);",
        "    a->buf = NULL; b->buf = malloc(n);",
    ]}
    alloc = {"alloc": "malloc", "alloc_args": ("n",)}
    sites = {
        ("len", True): [_site("m.c", 1, "len", lhs="a->len", **alloc)],
        ("buf", True): [
            _site("m.c", 1, "buf", lhs="a->buf", **alloc),
            _site("m.c", 3, "buf", lhs="a->buf", **alloc),
            _site("m.c", 4, "buf", lhs="b->buf", **alloc),
        ],
        ("size", False): [_site("m.c", 2, "size", field=False, **alloc)],
        ("p", False): [_site("m.c", 2, "p", field=False, **alloc)],
    }

    stores, _ = _run(sites, [("a.c", 25), ("m.c", 1), ("m.c", 2)], files=files)

    assert sorted(
        (row.split(" | ")[0], row.rsplit(" | ", 1)[1]) for row in _rows(stores)
    ) == [
        ("m.c:1", "allocates ->buf (malloc: n)"),
        ("m.c:1", "writes ->len; same line calls malloc(n)"),
        ("m.c:2", "allocates p (malloc: n)"),
        ("m.c:2", "assigns size; same line calls malloc(n)"),
        ("m.c:3", "writes ->buf; same line calls malloc(n)"),
        ("m.c:4", "allocates ->buf (malloc: n)"),
    ]


def test_only_the_ranges_that_remain_in_the_source_count_as_visible() -> None:
    """줄여서 토막만 남은 함수(`auditor_entry_trim`)는 남은 범위만 「이미 보인다」다 — 12줄은 남은
    토막 안이라 건너뛰고, 17줄은 뺀 구간이라 행이 된다. 범위 둘을 같이 줘도 각각 판정한다."""
    sites = {
        ("len", True): [
            _site("a.c", 12, func="grow", lhs="c->len"),
            _site("a.c", 17, func="grow", lhs="c->h.len"),
        ]
    }
    pieces = (
        Location(file="a.c", line_start=10, line_end=13),
        Location(file="a.c", line_start=40, line_end=45),
    )

    stores, _ = _run(sites, carried=pieces)

    assert [row.split(" | ")[0] for row in _rows(stores)] == ["a.c:17"]
    assert stores.in_source == 1


# --- 진짜 인덱서 → GraphIndex → 이 모듈 -------------------------------------------------------


def _real_block(  # noqa: ANN202
    tmp_path: Path, lines, carried, entry=("a.c", 25), sources=("a.c", "b.c")  # noqa: ANN001
):
    """`sources`(기본 위 `a.c`·`b.c`)를 **실제 인덱서**로 인덱싱해 `GraphIndex`로 읽는다."""
    out = tmp_path / "out" / "index.json"
    status = indexer.main(
        ["--out", str(out), "--root", str(tmp_path), *[str(tmp_path / name) for name in sources]]
    )
    assert status == 0
    graph = GraphIndex(out)

    def read(loc: Location) -> str:
        return (tmp_path / loc.file).read_text(encoding="utf-8")

    return stores_block(
        _ref(lines, entry), carried, read_source=read, stores_of=graph.stores_of,
        measure=len, room=100_000,
    )


def _write_sample(tmp_path: Path) -> None:
    (tmp_path / "a.c").write_text(_A_C, encoding="utf-8")
    (tmp_path / "b.c").write_text(_B_C, encoding="utf-8")


def test_the_real_index_answers_where_the_sink_arguments_are_written(tmp_path: Path) -> None:
    """`memcpy(c->buf + c->len, src, n)`(a.c:25) — `buf`은 구조체 하나(ctx)만 선언해 이름이 곧 키라
    grow의 할당(크기 식 `c->len + 1`)이 모호하지 않다. `len`은 구조체 셋(ctx·hdr·other)이 선언해
    쓰기 셋이 **전부** `[ambiguous]`다(철자가 같은 `c->len`이 앞설 뿐). 지역·파라미터(`src`·`n`·
    `local`)와 지역이 가린 `g_len`은 대상이 아니다."""
    _write_sample(tmp_path)

    stores = _real_block(
        tmp_path, [("a.c", 22), ("a.c", 24), ("a.c", 25), ("a.c", 26)], [_COPY]
    )

    assert _rows(stores) == [
        "a.c:13 | c->buf = (char *)malloc(c->len + 1); | allocates ->buf (malloc: c->len + 1)",
        "a.c:12 | c->len = n * 2; | writes ->len [ambiguous]",
        "a.c:17 | c->h.len = 4; | writes .len [ambiguous]",
        "b.c:6 | o->len = 5; | writes ->len [ambiguous]",
    ]
    assert stores.in_source == 0 and stores.dropped == ()


def test_the_real_index_has_no_struct_type_on_a_field_write_row(tmp_path: Path) -> None:
    """모호 판정이 서는 자리 — 진짜 인덱서의 `field_assigns` 행에 객체의 구조체 타입 열이 **없다**
    (file·line·func·lhs·rhs·op). 인덱서가 타입을 싣게 되면 이 테스트가 깨지고, 이름 키 대신 (구조체,
    필드)로 짚도록 `stores_of`를 다시 본다."""
    _write_sample(tmp_path)
    out = tmp_path / "index.json"
    assert indexer.main(["--out", str(out), "--root", str(tmp_path), str(tmp_path / "a.c")]) == 0

    rows = json.loads(out.read_text(encoding="utf-8"))["field_assigns"]["len"]

    assert rows and all(set(row) == {"file", "line", "func", "lhs", "rhs", "op"} for row in rows)


# 구조체 넷이 같은 필드 이름 `len`을 선언하고 `->len` 쓰기가 아홉 곳이다(8행 초과) — 이름만으로는
# 어느 것이 sink의 `p->len`과 같은 필드인지 못 가른다.
_FLOOD_F = """\
struct a { int len; };
struct b { int len; };
void fa(struct a *p)
{
    p->len = 1;
}
void fb(struct b *q)
{
    q->len = 2;
    q->len += 3;
}
int use(struct a *p, char *d, char *s)
{
    memcpy(d, s, p->len);
    return 0;
}
"""
_FLOOD_G = """\
struct c { long len; };
void gc(struct c *r)
{
    r->len = 4;
    r->len = 5;
}
void gd(struct c *r)
{
    r->len = 6;
}
"""
_FLOOD_H = """\
struct d { short len; };
void hd(struct d *t)
{
    t->len = 7;
    t->len = 8;
    t->len = 9;
}
"""


def test_the_real_index_caps_a_flood_of_same_named_field_writes(tmp_path: Path) -> None:
    """E4가 잰 모양 — sink 줄(`memcpy(d, s, p->len)`, f.c:14)의 필드 `len`에 이름이 같은 쓰기가
    구조체 넷에 걸쳐 아홉 곳이다. 하나도 `(구조체, 필드)`로 안 갈리므로 **전부 `[ambiguous]`**이고,
    이름마다 넷까지만: 같은 파일(f.c)의 셋(찾는 줄과 같은 철자 `p->len`이 앞) 다음 g.c의 첫 쓰기. 못
    실은 다섯은 목록으로 남는다."""
    (tmp_path / "f.c").write_text(_FLOOD_F, encoding="utf-8")
    (tmp_path / "g.c").write_text(_FLOOD_G, encoding="utf-8")
    (tmp_path / "h.c").write_text(_FLOOD_H, encoding="utf-8")
    use = Location(file="f.c", symbol="use", line_start=12, line_end=16)

    stores = _real_block(
        tmp_path, [("f.c", 14)], [use], entry=("f.c", 14), sources=("f.c", "g.c", "h.c")
    )

    assert _rows(stores) == [
        "f.c:5 | p->len = 1; | writes ->len [ambiguous]",
        "f.c:9 | q->len = 2; | writes ->len [ambiguous]",
        "f.c:10 | q->len += 3; | writes ->len (+=) [ambiguous]",
        "g.c:4 | r->len = 4; | writes ->len [ambiguous]",
    ]
    assert stores.dropped == ("g.c:5 len", "g.c:9 len", "h.c:4 len", "h.c:5 len", "h.c:6 len")
    assert stores.rows + len(stores.dropped) == 9 > 8
    assert _more_line(stores) == source_stores._more(5)


def test_the_real_index_marks_a_same_named_static_in_another_file_as_ambiguous(
    tmp_path: Path,
) -> None:
    """b.c의 `g_buf = calloc(1, 16)`(8행)을 부르는 슬라이스 — `g_buf`는 a.c에도 파일 전역이 있다.
    a.c의 쓰기(14행, 줄바꿈된 `malloc(\\n n + 1)`)는 다른 파일이라 이름만으로는 같은 변수인지 모른다
    — 싣되 `[ambiguous]`를 단다. 같은 파일(b.c:8) 쓰기는 이미 실린 `other_fn` 안이다."""
    _write_sample(tmp_path)
    other_fn = Location(file="b.c", symbol="other_fn", line_start=4, line_end=9)

    stores = _real_block(tmp_path, [("b.c", 8)], [other_fn], entry=("b.c", 8))

    assert _rows(stores) == [
        "a.c:14 | g_buf = malloc( n + 1); | allocates g_buf (malloc: n + 1) [ambiguous]"
    ]
    assert stores.in_source == 1


# libzip에서 본 모양(`zip_algorithm_zstd.c`의 `ctx->in.pos`) — 진짜 구조체(`ZSTD_inBuffer`)는 인덱스
# 밖 헤더(`zstd.h`)에 있고, 인덱스에 든 `pos`는 **관계없는** ossfuzz의 구조체 하나뿐이다.
_FUZZ_C = """\
struct cursor { int pos; };
void fuzz_advance(struct cursor *c)
{
    c->pos = 0;
}
"""
_ZSTD_C = """\
void reset(Ctx *ctx)
{
    ctx->in.pos = 0;
}
void process(Ctx *ctx, char *d, char *s)
{
    memcpy(d, s, ctx->in.pos);
}
"""
# 같은 디렉터리 안에서 닫히는 모양 — 구조체는 lib/zipint.h 하나, 쓰기(zip_open.c)와 찾는
# 줄(zip_close.c)이 같은 lib/에 있다.
_ZIPINT_H = "struct zip { int flags; };\n"
_ZIP_OPEN_C = "void set(struct zip *z)\n{\n    z->flags = 1;\n}\n"
_ZIP_CLOSE_C = "int use(struct zip *z, char *d, char *s)\n{\n    memcpy(d, s, z->flags);\n}\n"


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def test_the_real_index_flags_a_single_indexed_struct_that_is_far_from_the_use(
    tmp_path: Path,
) -> None:
    """인덱스에서 `pos`를 선언한 구조체가 하나(ossfuzz/fuzz.c)뿐이어도 그것이 쓰기·찾는 줄과 **같은
    디렉터리가 아니면**(include 정보가 인덱스에 없다) 같은 필드라고 못 한다 — 두 쓰기가 다
    `[ambiguous]`다. 예전 규칙(선언이 하나면 모호하지 않다)은 둘 다 달지 않았다."""
    tree = {"ossfuzz/fuzz.c": _FUZZ_C, "lib/zip_algorithm_zstd.c": _ZSTD_C}
    _write_tree(tmp_path, tree)
    process = Location(file="lib/zip_algorithm_zstd.c", symbol="process", line_start=5, line_end=8)

    stores = _real_block(
        tmp_path, [("lib/zip_algorithm_zstd.c", 7)], [process],
        entry=("lib/zip_algorithm_zstd.c", 7), sources=tuple(tree),
    )

    assert _rows(stores) == [
        "lib/zip_algorithm_zstd.c:3 | ctx->in.pos = 0; | writes .pos [ambiguous]",
        "ossfuzz/fuzz.c:4 | c->pos = 0; | writes ->pos [ambiguous]",
    ]


def test_the_real_index_does_not_flag_a_single_struct_declared_next_to_both_use_and_write(
    tmp_path: Path,
) -> None:
    """구조체가 lib/zipint.h 하나이고 쓰기(lib/zip_open.c)와 찾는 줄(lib/zip_close.c)이 같은 lib/에
    있으면 모호하다고 안 한다 — 위 경우와 짝이다(디렉터리 판정이 모든 것을 다는 것도, 아무것도 안
    다는 것도 아니다)."""
    tree = {
        "lib/zipint.h": _ZIPINT_H, "lib/zip_open.c": _ZIP_OPEN_C, "lib/zip_close.c": _ZIP_CLOSE_C
    }
    _write_tree(tmp_path, tree)
    use = Location(file="lib/zip_close.c", symbol="use", line_start=1, line_end=4)

    stores = _real_block(
        tmp_path, [("lib/zip_close.c", 3)], [use], entry=("lib/zip_close.c", 3), sources=tuple(tree)
    )

    assert _rows(stores) == ["lib/zip_open.c:3 | z->flags = 1; | writes ->flags"]
