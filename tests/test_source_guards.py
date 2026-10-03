"""`auditor_guards` — Auditor `SOURCE`의 GUARDS 블록(`src/orchestrator/source_guards.py`).

Joern의 데이터흐름은 검사를 무시한다 — `if (len > MAX) return;`는 sink의 조상이 아니라 슬라이스에도
SOURCE에도 「범위가 검사됐는가」가 구조로 안 실린다. 이 옵션은 앵커 함수의 원문을 구문만으로 훑어
sink 호출의 인자 이름을 언급하는 조건을 줄번호와 함께 싣는다. **지배(dominate)를 주장하지 않는다.**

앞 절은 훑는 함수(`scan_function`)를 문자열로 직접 부른다 — 걸러내는 갈래마다 그 갈래 하나만
건드리는 입력을 따로 만들어, 그 갈래가 죽으면 그 테스트가 죽게 한다. 뒤 절은
`runner._auditor_source`를 직접 불러 옵션이 꺼졌을 때 입력·이벤트가 그대로이고 켜졌을 때 블록이
붙는 것을 본다.
"""

import textwrap
import time
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import AUDITOR_SOURCE_OPTIONS, load_config
from src.orchestrator import runner
from src.orchestrator.source_guards import (
    _MAX_DROPPED_LISTED,
    _MAX_ROWS,
    GUARDS_HEADER,
    scan_function,
    scan_guards,
)
from src.schemas.candidate import SliceRef
from src.schemas.common import Location
from tests.test_pipeline_e2e import _run, _with_index
from tests.test_slice_targets import (
    _CASE_A,
    _CASE_B,
    _PARSER,
    _banner,
    _prompts,
    _slice,
    _slices_file,
    _source_events,
)

FILE = "a.c"
FIRST = 10  # 아래 `_src`·`_func`가 만든 원문의 첫 줄 번호
NONE_FOUND = "GUARDS: none found before sink (syntactic scan of the anchor function)"


def _src(text: str) -> str:
    """들여쓰기를 걷고 앞뒤 빈 줄을 뗀다 — 첫 줄이 `FIRST`번 줄이다."""
    return textwrap.dedent(text).strip("\n")


def _func(*body: str) -> str:
    """줄 목록으로 함수를 만든다 — 줄이 많은 입력에서 들여쓰기가 틀어지지 않게."""
    head = ["int f(char *dst, const char *src, size_t n)", "{"]
    return "\n".join([*head, *(f"    {line}" for line in body), "}"])


def _line(src: str, needle: str) -> int:
    """`needle`을 처음 품은 줄의 번호. 줄 번호를 손으로 세지 않는다."""
    for offset, line in enumerate(src.split("\n")):
        if needle in line:
            return FIRST + offset
    raise AssertionError(f"{needle!r}가 든 줄이 없다")


def _row(src: str, needle: str, text: str) -> str:
    return f"{FILE}:{_line(src, needle)} | {text}"


def _scan(src: str, sink: str, name: str | None = "memcpy", **kw):  # noqa: ANN202
    return scan_function(
        src, file=FILE, first_line=FIRST, sink_line=_line(src, sink), sink_name=name, **kw
    )


_BASIC = _src("""
    static int parse(struct hdr *h, const char *src)
    {
        char buf[64];
        size_t len = h->len;
        if (flag)
            counter++;
        if (len > sizeof(buf))
            return -1;
        memcpy(buf, src, len);
        if (len > 10)
            log_it();
        return 0;
    }
""")


# --- 훑기: 무엇이 가드로 올라오고 무엇이 안 올라오나 ----------------------------------------


def test_a_guard_before_the_sink_is_listed_with_the_exit_it_guards() -> None:
    scan = _scan(_BASIC, "memcpy(buf")
    assert scan is not None
    assert scan.rows == (
        _row(_BASIC, "if (len > sizeof", "if (len > sizeof(buf)) [exit: return -1]"),
    )
    assert scan.block == "\n".join([GUARDS_HEADER, *scan.rows])
    assert scan.block.splitlines()[0] == "GUARDS (syntactic — may not dominate)"
    assert (scan.shown, scan.dropped) == (1, ())


def test_a_guard_after_the_sink_is_ignored() -> None:
    src = _func("memcpy(dst, src, n);", "if (n > 16)", "    return -1;", "return 0;")
    scan = _scan(src, "memcpy(")
    assert scan.rows == ()
    assert scan.block == NONE_FOUND


def test_a_guard_on_a_variable_the_sink_does_not_use_is_ignored() -> None:
    src = _func("if (verbose)", '    puts("copying");', "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").block == NONE_FOUND


@pytest.mark.parametrize("access", ["->", "."])
def test_a_guard_on_a_field_of_a_sink_argument_is_listed(access: str) -> None:
    """sink 인자 `p->data`와 **다른** 필드를 언급하는 조건(`p->len`·`p.len`)도 `p`를 언급한다 —
    `len`이 인자에 없으니 필드 이름이 아니라 `p`로 이어진다."""
    src = _func(
        f"if (p{access}len > 64)",
        "    return -1;",
        f"memcpy(dst, p{access}data, n);",
    )
    assert _scan(src, "memcpy(").rows == (
        _row(src, "if (p", f"if (p{access}len > 64) [exit: return -1]"),
    )


def test_the_arguments_of_a_multi_line_sink_call_are_all_read() -> None:
    """`off`는 둘째 줄에만 나온다 — 호출이 시작하는 줄만 읽으면 그 가드를 놓친다."""
    src = _func(
        "if (off > 8)",
        "    return;",
        "memcpy(dst,",
        "       src + off,",
        "       n);",
    )
    assert _scan(src, "memcpy(").rows == (_row(src, "if (off", "if (off > 8) [exit: return]"),)


def test_no_guard_prints_the_none_found_line_naming_what_was_scanned() -> None:
    src = _func("memcpy(dst, src, n);", "return 0;")
    scan = _scan(src, "memcpy(")
    assert scan.block == NONE_FOUND
    assert (scan.shown, scan.dropped) == (0, ())
    # 함수 경계를 모르는 창이면 「함수」라고 쓰지 않는다 — 없다는 단언의 범위가 거짓이 된다
    window = _scan(src, "memcpy(", scope="window")
    assert window.block == "GUARDS: none found before sink (syntactic scan of the anchor window)"


def test_rows_over_the_cap_keep_the_nearest_to_the_sink_and_list_the_dropped_ones() -> None:
    total = _MAX_ROWS + 3
    src = _func(*(f"if (n > {i}) bad();" for i in range(1, total + 1)), "memcpy(dst, src, n);")
    scan = _scan(src, "memcpy(")
    assert [row.split(" | ")[1] for row in scan.rows] == [
        f"if (n > {i})" for i in range(4, total + 1)
    ]
    assert [row.split(" | ")[1] for row in scan.dropped] == [f"if (n > {i})" for i in (1, 2, 3)]
    assert scan.dropped_listing == "\n".join(scan.dropped)
    assert scan.block.count("\n") == _MAX_ROWS  # 머리줄 + 행 `_MAX_ROWS`개 = 줄바꿈 `_MAX_ROWS`개


def test_the_dropped_listing_is_itself_capped_and_says_how_many_more() -> None:
    total = _MAX_ROWS + _MAX_DROPPED_LISTED + 5
    src = _func(*(f"if (n > {i}) bad();" for i in range(1, total + 1)), "memcpy(dst, src, n);")
    scan = _scan(src, "memcpy(")
    assert len(scan.dropped) == _MAX_DROPPED_LISTED + 5
    listing = scan.dropped_listing.split("\n")
    assert len(listing) == _MAX_DROPPED_LISTED + 1
    assert listing[:-1] == list(scan.dropped[:_MAX_DROPPED_LISTED])
    assert listing[-1] == "... +5 more"


def test_comments_and_string_literals_are_not_conditions() -> None:
    src = _func(
        "// if (n > 3) return -1;",
        "/* if (n > 4)",
        "     return -2; */",
        'log("if (n > 5) return");',
        "memcpy(dst, src, n);",
    )
    assert _scan(src, "memcpy(").block == NONE_FOUND


def test_a_comment_inside_a_condition_does_not_swallow_the_rest_of_it() -> None:
    """줄 끝 주석이 든 여러 줄 조건을 한 줄로 접으면 주석이 뒤 문장을 삼킨다 — 주석을 먼저
    지운다."""
    src = _func(
        "if (n > MAX   // bytes",
        "    || !dst)",
        "    return -1;",
        "memcpy(dst, src, n);",
    )
    assert _scan(src, "memcpy(").rows == (
        _row(src, "if (n > MAX", "if (n > MAX || !dst) [exit: return -1]"),
    )


def test_a_guard_on_the_sink_line_counts_only_when_it_comes_before_the_call() -> None:
    """`if (n <= 16) memcpy(...)`의 조건은 sink보다 앞이다 — 줄이 같아도 놓치면 「없음」이 거짓이
    된다. 같은 줄에서 호출 **뒤**에 오는 조건은 가드가 아니다."""
    src = _func("if (n <= 16) memcpy(dst, src, n);", "memcpy(dst, src, n); if (n > 4) log_it();")
    first = _scan(src, "if (n <= 16)")
    assert first.rows == (_row(src, "if (n <= 16)", "if (n <= 16)"),)
    second = _scan(src, "memcpy(dst, src, n); if")
    assert second.rows == (_row(src, "if (n <= 16)", "if (n <= 16)"),)  # 뒤의 `if (n > 4)`는 없다


def test_a_condition_that_contains_the_sink_call_is_not_its_own_guard() -> None:
    src = _func("if (memcmp(memcpy(dst, src, n), src, n) != 0)", "    return -1;")
    assert _scan(src, "memcpy(").block == NONE_FOUND


@pytest.mark.parametrize(
    ("guard", "text"),
    [
        ("for (i = 0; i < n; i++) {", "for (i = 0; i < n; i++)"),
        ("while (n > 0) {", "while (n > 0)"),
        ("n = n > MAX ? MAX : n;", "n > MAX ? ... : ..."),
        ("n = (n > MAX) ? MAX : n;", "(n > MAX) ? ... : ..."),
        ("return n > MAX ? 0 : 1;", "n > MAX ? ... : ..."),
        ("assert(n < 64);", "assert(n < 64)"),
        ("BUG_ON(n > 64);", "BUG_ON(n > 64)"),
        ("g_return_if_fail(n < 64);", "g_return_if_fail(n < 64)"),
    ],
)
def test_loops_ternaries_and_assert_like_macros_are_guards_too(guard: str, text: str) -> None:
    src = _func(guard, *(["}"] if guard.endswith("{") else []), "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (_row(src, guard, text),)


@pytest.mark.parametrize(
    "clamp",
    [
        "size_t n = slen > dlen ? dlen : slen;",
        "n += flag ? 1 : 2;",
        "n = (slen > dlen) ? dlen : slen;",
    ],
)
def test_a_ternary_that_assigns_a_sink_argument_is_a_guard_even_if_its_condition_names_others(
    clamp: str,
) -> None:
    """`n = slen > dlen ? dlen : slen`은 `n`을 조인다 — 조건이 `slen`·`dlen`만 말해도 sink 인자
    `n`의 값을 정하는 분기다. 대입의 왼쪽 이름도 센다(`target = cond ? a : b` 꼴만)."""
    src = _func(clamp, "memcpy(dst, src, n);")
    scan = _scan(src, "memcpy(")
    assert len(scan.rows) == 1 and scan.rows[0].startswith(f"{FILE}:{_line(src, clamp)} | ")
    # 대입이 아닌 자리의 삼항은 이웃 이름을 끌어오지 않는다 — 인자 `n`이 조건에 없으면 가드가 아니다
    other = _func("log(n, flag ? 1 : 2);", "memcpy(dst, src, n);")
    assert _scan(other, "memcpy(").rows == ()


def test_a_long_function_full_of_assignment_ternaries_scans_in_linear_time() -> None:
    """대입 삼항마다 대입 왼쪽의 문장 시작을 프리픽스 전체에서 거슬러 찾으면 제곱이다 — 삼항
    10만 줄이 81초였고 고친 뒤 0.9초다(2026-10-03 로컬 프로브 · run ID 없음). 4만 줄은 고친 쪽이
    0.3초 안팎이고 되돌리면 20초라, 느린 장비에서도 안 흔들리는 상한을 둔다."""
    src = _func(*(["a = b ? c : d;"] * 40_000), "memcpy(dst, src, n);")
    started = time.perf_counter()
    assert _scan(src, "memcpy(").block == NONE_FOUND
    assert time.perf_counter() - started < 5.0


def test_a_loop_that_closes_after_the_sink_is_not_a_guard() -> None:
    src = _func("do {", "    memcpy(dst, src, n);", "} while (n--);")
    assert _scan(src, "memcpy(").block == NONE_FOUND


def test_an_operator_sink_reads_the_names_on_its_own_line() -> None:
    """Case B의 sink는 `a[i] = x` 같은 연산자일 수 있다(Joern 이름 `<operator>…`) — 호출이 없으니
    그 줄의 이름 전부가 sink의 이름이다. 줄 앞 조건만 가드로 센다."""
    src = _func(
        "if (other)",
        "    return;",
        "if (idx >= sizeof(buf))",
        "    return;",
        "buf[idx] = val;",
    )
    scan = _scan(src, "buf[idx] =", name="<operator>.indirectIndexAccess")
    assert scan.rows == (_row(src, "if (idx >=", "if (idx >= sizeof(buf)) [exit: return]"),)


def test_only_an_exit_directly_in_the_guarded_body_is_reported() -> None:
    """안쪽 블록의 탈출은 조건부라 「이 가드가 탈출한다」고 쓰지 않는다 — 조건만 싣는다."""
    src = _func(
        "if (n > MAX) {",
        "    if (strict) {",
        "        return -1;",
        "    }",
        "    log_it();",
        "}",
        "if (n > LIMIT) {",
        "    log_it();",
        "    goto fail;",
        "}",
        "memcpy(dst, src, n);",
    )
    assert _scan(src, "memcpy(").rows == (
        _row(src, "if (n > MAX)", "if (n > MAX)"),
        _row(src, "if (n > LIMIT)", "if (n > LIMIT) [exit: goto fail]"),
    )


def test_callee_names_and_type_names_do_not_make_a_guard() -> None:
    """`strlen`은 인자 이름이 아니라 함수고 `size_t`는 타입이다 — 그 낱말로 아무 조건이나 안
    줍는다."""
    src = _func(
        "if (strlen(other) > 5)",
        "    return -1;",
        "if ((size_t)other[0] > 5)",
        "    return -2;",
        "memcpy(dst, src, (size_t)strlen(src));",
    )
    assert _scan(src, "memcpy(").block == NONE_FOUND


def test_a_preprocessor_line_is_not_a_condition() -> None:
    """`#if (BUFSIZE > 64)`는 `if (`로 읽히기 쉽다 — 전처리 줄(이어진 `#define` 본문 포함)은
    지운다."""
    src = _func(
        "#if (BUFSIZE > 64)",
        "x();",
        "#endif",
        "#define CHECK_LEN(x) \\",
        "    if ((x) > BUFSIZE) \\",
        "        return -1",
        "memcpy(dst, src, BUFSIZE);",
    )
    assert _scan(src, "memcpy(").block == NONE_FOUND


def test_text_cut_mid_condition_gives_no_row_and_no_crash() -> None:
    """창 앵커는 조건 한가운데서 잘릴 수 있다 — 짝이 안 맞는 괄호는 구성으로 안 센다."""
    src = _func("if (n > 3", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").block == NONE_FOUND


def test_text_cut_right_after_the_sink_still_reports_the_guard() -> None:
    """`;`도 `}`도 없이 끝나는 원문 — 몸통의 끝을 못 찾아도 조건 행은 남는다."""
    src = "\n".join(["void f(char *dst, const char *src, size_t n)", "{", "    if (n > 3)",
                     "        memcpy(dst, src, n)"])
    assert _scan(src, "memcpy(").rows == (_row(src, "if (n > 3)", "if (n > 3)"),)


def test_a_scope_operator_in_a_ternary_condition_is_part_of_the_condition() -> None:
    """`::`의 `:`를 라벨 경계로 읽으면 조건이 `std::` 뒤에서 잘린다."""
    src = _func("n = std::min(a, b) > cap ? cap : n;", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (
        _row(src, "n = std::min", "std::min(a, b) > cap ? ... : ..."),
    )


def test_a_ternary_inside_a_condition_and_a_stray_question_mark_do_not_break_the_scan() -> None:
    src = _func("if (n > MAX ? 1 : 0)", "    return;", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (
        _row(src, "if (n > MAX", "if (n > MAX ? 1 : 0) [exit: return]"),
        _row(src, "if (n > MAX", "n > MAX ? ... : ..."),
    )
    assert _scan(_func("x = ? ;", "memcpy(dst, src, n);"), "memcpy(").block == NONE_FOUND


def test_a_sink_line_outside_the_text_or_without_names_gives_no_scan() -> None:
    """훑을 수 없을 때 「없음」을 쓰지 않는다 — 블록 자체를 안 만든다."""
    kw = {"file": FILE, "first_line": FIRST, "sink_name": "memcpy"}
    assert scan_function(_BASIC, sink_line=1, **kw) is None
    assert scan_function(_BASIC, sink_line=999, **kw) is None
    src = _func("memset(0, 0, 16);")
    assert _scan(src, "memset(", name="memset") is None


# --- 슬라이스 → 훑기 -------------------------------------------------------------------


def _ref(
    sink_line: int,
    *,
    given_end: str = "sink",
    entry_symbol: str = "memcpy",
    lines: tuple[tuple[str, int], ...] | None = None,
) -> SliceRef:
    entry = Location(file=FILE, line_start=sink_line, symbol=entry_symbol)
    return SliceRef(
        slice_id="s",
        location=Location(file=FILE, line_start=sink_line, symbol="parse"),
        given_end=given_end,
        entry=entry,
        lines=lines or ((FILE, sink_line),),
        degraded=False,
    )


def test_the_slice_entry_names_the_sink_and_case_a_has_none_to_scan() -> None:
    anchor = Location(file=FILE, symbol="parse", line_start=FIRST, line_end=FIRST + 12)
    sink = _line(_BASIC, "memcpy(buf")
    scan = scan_guards(_ref(sink), anchor, _BASIC)
    assert scan is not None and scan.shown == 1
    # Case A: 하네스가 아는 것은 entry 함수뿐이고 sink는 모른다 — 훑지 않는다
    assert scan_guards(_ref(sink, given_end="source"), anchor, _BASIC) is None
    # 품는 함수를 모르는 창 앵커면 「없음」의 범위를 창이라고 쓴다
    window = Location(file=FILE, line_start=FIRST, line_end=FIRST + 12)
    plain = _func("memcpy(dst, src, n);")
    none = scan_guards(_ref(_line(plain, "memcpy(")), window, plain)
    assert none is not None and none.block.endswith("(syntactic scan of the anchor window)")


# --- `_auditor_source`에 붙는 자리 --------------------------------------------------------


def _ctx(files: dict[str, list[str]], symbols: list[Location], *, counter=None, **options):  # noqa: ANN001, ANN202
    """`_auditor_source`를 직접 부르는 최소 ctx. 임계값은 **실제 `Thresholds`**다 — 옵션 이름이
    config와 러너 사이에서 어긋나면 조용히 꺼지지 않고 여기서 터진다."""
    events: list[dict] = []

    def read(loc: Location) -> str:
        lines = files[loc.file]
        return "\n".join(lines[(loc.line_start or 1) - 1 : loc.line_end or len(lines)])

    ctx = types.SimpleNamespace(
        symbols_for=lambda file: [s for s in symbols if s.file == file],
        read_source=read,
        callees_of=lambda _loc: [],
        definitions_of=lambda _names, _file: [],
        call_sites_of=lambda _loc: [],
        counter_for_role=lambda _role: counter,
        config=types.SimpleNamespace(
            thresholds=load_config("dev_fake").thresholds.transition(**options)
        ),
        trace=types.SimpleNamespace(emit=lambda *a, **kw: events.append(kw)),
    )
    return ctx, events


_FILES = {FILE: [""] * (FIRST - 1) + _BASIC.split("\n")}
_ANCHOR = Location(file=FILE, symbol="parse", line_start=FIRST, line_end=FIRST + 12)
_SINK = _line(_BASIC, "memcpy(buf")
# `_BASIC` 뒤(40~41줄)에 함수 하나 더 — 슬라이스가 그 줄을 지나면 SOURCE에 같이 실린다
_OTHER = Location(file=FILE, symbol="other", line_start=40, line_end=41)
_FILES_WITH_OTHER = {
    FILE: _FILES[FILE] + [""] * (40 - len(_FILES[FILE]) - 1) + ["void other(void)", "{}"]
}


def test_guards_off_keeps_the_source_and_the_event_as_they_were() -> None:
    ctx, events = _ctx(_FILES, [_ANCHOR])
    source = runner._auditor_source(ctx, _ref(_SINK), _ANCHOR, "cand")
    assert source == _BASIC  # 함수 하나 — 배너도 없다(결정 1-b 그대로)
    assert not [key for key in events[-1] if key.startswith("guards")]
    assert events[-1]["functions"] == 1


def test_guards_on_adds_the_block_right_after_the_entry_function() -> None:
    ctx, events = _ctx(_FILES, [_ANCHOR], auditor_guards=1)
    source = runner._auditor_source(ctx, _ref(_SINK), _ANCHOR, "cand")
    guard = _row(_BASIC, "if (len > sizeof", "if (len > sizeof(buf)) [exit: return -1]")
    # 배너가 붙는다 — GUARDS의 `파일:줄`을 코드 줄에 되짚을 곳이 배너뿐이다
    assert source == f"/* ===== a.c:10-22 ===== */\n{_BASIC}\n{GUARDS_HEADER}\n{guard}"
    event = events[-1]
    assert event["functions"] == 1  # GUARDS는 함수가 아니다
    assert (event["guards"], event["guards_dropped"]) == (1, "")
    assert event["chars"] == len(source)


def test_guards_on_places_the_block_before_the_other_carried_functions() -> None:
    ctx, _ = _ctx(_FILES_WITH_OTHER, [_ANCHOR, _OTHER], auditor_guards=1)
    ref = _ref(_SINK, lines=((FILE, _SINK), (FILE, 41)))
    source = runner._auditor_source(ctx, ref, _ANCHOR, "cand")
    assert source.index(GUARDS_HEADER) < source.index("/* ===== a.c:40-41 ===== */")


def test_guards_on_case_a_adds_nothing() -> None:
    ctx, events = _ctx(_FILES, [_ANCHOR], auditor_guards=1)
    ref = _ref(_SINK, given_end="source", entry_symbol="parse")
    assert runner._auditor_source(ctx, ref, _ANCHOR, "cand") == _BASIC
    assert not [key for key in events[-1] if key.startswith("guards")]


def test_the_guard_block_counts_against_the_cap_before_the_other_functions() -> None:
    """GUARDS는 entry 함수 몫이라 상한에 안 밀린다 — 대신 그 크기를 먼저 세어, 뒤에 붙는 함수가
    그만큼 덜 든다. 켜기 전에 딱 맞던 함수가 켜면 빠진다."""
    words = lambda text: len(text.split())  # noqa: E731 — 단어 수 카운터
    anchor_block = runner._block(_ANCHOR, _BASIC, end=FIRST + 12)
    other_block = runner._block(_OTHER, "void other(void)\n{}")
    cap = words(anchor_block) + 1 + words(other_block)  # 켜기 전에는 딱 맞는 상한
    ref = _ref(_SINK, lines=((FILE, _SINK), (FILE, 41)))
    for guards, dropped in ((0, 0), (1, 1)):
        ctx, events = _ctx(
            _FILES_WITH_OTHER,
            [_ANCHOR, _OTHER],
            counter=words,
            auditor_source_tokens=cap,
            auditor_guards=guards,
        )
        runner._auditor_source(ctx, ref, _ANCHOR, "cand")
        assert events[-1]["dropped"] == dropped, guards


def test_guards_are_scanned_in_the_entry_function_before_it_is_trimmed() -> None:
    """`auditor_entry_trim`이 함수 머리 쪽을 잘라내도 거기 있던 가드는 행으로 남는다 — 잘려 안
    보이는 검사가 「없음」으로 읽히면 안 된다."""
    big = _func("if (n > MAX)", "    return -1;", *(["x = x + 1;"] * 60), "memcpy(dst, src, n);")
    last = FIRST + big.count("\n")
    anchor = Location(file=FILE, symbol="f", line_start=FIRST, line_end=last)
    ctx, _ = _ctx(
        {FILE: [""] * (FIRST - 1) + big.split("\n")},
        [anchor],
        counter=lambda text: len(text.split()),
        auditor_source_tokens=30,
        auditor_entry_trim=1,
        auditor_guards=1,
    )
    source = runner._auditor_source(ctx, _ref(_line(big, "memcpy(")), anchor, "cand")
    assert "lines omitted" in source
    assert source.count("if (n > MAX)") == 1  # 잘린 본문에는 없고 GUARDS 행에만 있다
    assert _row(big, "if (n > MAX)", "if (n > MAX) [exit: return -1]") in source


def test_the_guards_option_is_a_default_off_switch_the_prompt_gate_knows() -> None:
    assert "auditor_guards" in AUDITOR_SOURCE_OPTIONS
    thresholds = load_config("dev_fake").thresholds
    assert thresholds.auditor_guards == 0
    assert "auditor_guards" not in thresholds.flatten()
    assert thresholds.auditor_source_options_on() == ()
    on = thresholds.transition(auditor_guards=1)
    assert on.flatten()["auditor_guards"] == 1.0
    assert on.auditor_source_options_on() == ("auditor_guards",)
    with pytest.raises(ValidationError):
        thresholds.transition(auditor_guards=2)


# --- 끝에서 끝까지: 모델이 실제로 받는 프롬프트 -------------------------------------------


def _guards(value: int):  # noqa: ANN202
    def configure(config):  # noqa: ANN001, ANN202
        return config.transition(thresholds=config.thresholds.transition(auditor_guards=value))

    return configure


def test_the_auditor_prompt_carries_the_guards_block_of_a_case_b_slice(tmp_path: Path) -> None:
    """`parser.c`의 `parse_header`는 sink(`memcpy`) 앞에 검사가 없다 — 그 「없음」이 모델이 받는
    SOURCE에 훑은 결과로 실린다. 사실 블록(FACTS)과 `{{SOURCE}}` 자리가 같은 글자다."""
    path = _slices_file(tmp_path, [_slice(*_CASE_B)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(), configure=_guards(1))

    ((body, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(
        [_banner("parser.c", 19, 26), *_PARSER[18:26], NONE_FOUND]
    )
    assert facts["source"] in body
    (event,) = _source_events(trace_path)
    assert (event["functions"], event["guards"], event["guards_dropped"]) == (1, 0, "")


def test_guards_on_leave_a_case_a_slice_as_it_was(tmp_path: Path) -> None:
    path = _slices_file(tmp_path, [_slice(*_CASE_A)])

    _, trace_path = _run(tmp_path, slices_path=path, before=_with_index(), configure=_guards(1))

    ((_, facts),) = _prompts(trace_path, "auditor")
    assert facts["source"] == "\n".join(_PARSER[18:26])
    (event,) = _source_events(trace_path)
    assert not [key for key in event if key.startswith("guards")]
