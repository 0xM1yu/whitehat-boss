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
    _EXIT_CHARS,
    _MAX_DROPPED_LISTED,
    _MAX_ROWS,
    _ROW_CHARS,
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
_NONE_TAIL = (
    " (syntactic scan of this function; aliases beyond one assignment hop, "
    "MIN/MAX-style clamps and switch are not tracked)"
)


def _none(names: str) -> str:
    """가드를 못 찾았을 때 모델이 보는 줄 — 「없음」이 아니라 **어떤 이름으로 찾았는지**와 못 보는
    것을 적는다(독립 검토: 맨 「none found」는 별칭·클램프·switch 앞에서 거짓이었다)."""
    return f"GUARDS: no condition before the sink mentions {names}{_NONE_TAIL}"


NONE_FOUND = _none("dst, n, src")  # `_func`의 기본 sink `memcpy(dst, src, n)`


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


def test_no_guard_prints_a_line_that_names_the_names_it_looked_for_and_what_it_cannot_see() -> None:
    """「none found」만 쓰면 별칭·클램프·switch 앞에서 거짓이었다(독립 검토 — 실제 GT sink 82개 중
    「없음」 12개에서 3개가 틀렸다). 맞춰 본 이름과 못 보는 것을 모델이 보는 줄에 적는다."""
    src = _func("memcpy(dst, src, n);", "return 0;")
    scan = _scan(src, "memcpy(")
    assert scan.block == (
        "GUARDS: no condition before the sink mentions dst, n, src (syntactic scan of this "
        "function; aliases beyond one assignment hop, MIN/MAX-style clamps and switch are not "
        "tracked)"
    )
    assert "none found" not in scan.block
    assert (scan.shown, scan.dropped, scan.names) == (0, (), ("dst", "n", "src"))


def test_the_names_in_the_none_line_are_capped() -> None:
    args = ", ".join(f"v{i:02d}" for i in range(13))
    src = _func(f"memcpy({args});")
    line = _scan(src, "memcpy(").block
    assert "mentions v00, v01, v02, v03, v04, v05, v06, v07, v08, v09, ... +3 more (" in line


def test_rows_over_the_cap_keep_the_nearest_to_the_sink_and_list_the_dropped_ones() -> None:
    total = _MAX_ROWS + 3
    src = _func(*(f"if (n > {i}) bad();" for i in range(1, total + 1)), "memcpy(dst, src, n);")
    scan = _scan(src, "memcpy(")
    assert [row.split(" | ")[1] for row in scan.rows] == [
        f"if (n > {i})" for i in range(4, total + 1)
    ]
    assert [row.split(" | ")[1] for row in scan.dropped] == [f"if (n > {i})" for i in (1, 2, 3)]
    assert scan.dropped_listing == "\n".join(scan.dropped)
    # 모델이 목록을 다 본 것으로 읽지 않게 — 뺀 행이 있으면 블록 안에 개수를 적는다
    lines = scan.block.split("\n")
    assert lines[0] == GUARDS_HEADER and lines[1:-1] == list(scan.rows)
    assert lines[-1] == "... +3 more rows omitted" and len(lines) == _MAX_ROWS + 2


def test_early_exit_rows_and_rows_that_enclose_the_sink_outrank_nearer_plain_rows() -> None:
    """12행 상한이 가장 쓸모 있는 행을 말없이 버렸다(독립 검토: 상한을 넘는 블록의 71%에서 조기
    탈출·sink를 품은 행이 빠졌다). 조기 탈출 행과 sink를 품은 조건을 먼저 남기고, 그다음 sink에
    가까운 순이다."""
    plain = [f"if (n > {i}) bad();" for i in range(1, _MAX_ROWS + 3)]
    exiting = _func("if (n > MAX) return -1;", *plain, "memcpy(dst, src, n);")
    scan = _scan(exiting, "memcpy(")
    assert _row(exiting, "if (n > MAX)", "if (n > MAX) [exit: return -1]") in scan.rows
    assert len(scan.rows) == _MAX_ROWS and len(scan.dropped) == 3
    assert [row.split(" | ")[1] for row in scan.dropped] == [f"if (n > {i})" for i in (1, 2, 3)]
    enclosing = _func("while (n > 0) {", *plain, "    memcpy(dst, src, n);", "}")
    scan = _scan(enclosing, "memcpy(")
    assert _row(enclosing, "while (n > 0)", "while (n > 0)") in scan.rows
    assert [row.split(" | ")[1] for row in scan.dropped] == [f"if (n > {i})" for i in (1, 2, 3)]
    # 보여 줄 때는 소스 순서다
    lines = [int(row.split(":")[1].split(" ")[0]) for row in scan.rows]
    assert lines == sorted(lines)


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
    assert _scan(src, "memcpy(").block == _none("dst, src")  # strlen·size_t는 이름이 아니다


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
    assert _scan(src, "memcpy(").block == _none("BUFSIZE, dst, src")


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


def _raw(text: str):  # noqa: ANN202
    """`ctx.read_source`의 대역 — 줄 범위가 없는 위치(`Location(file)`)는 파일 전체 원문이다."""
    return lambda _loc: text


def test_the_slice_entry_names_the_sink_and_every_other_case_is_skipped_with_a_reason() -> None:
    anchor = Location(file=FILE, symbol="parse", line_start=FIRST, line_end=FIRST + 12)
    sink = _line(_BASIC, "memcpy(buf")
    scan = scan_guards(_ref(sink), anchor, _BASIC, _raw(_BASIC))
    assert scan.skipped is None and scan.shown == 1
    assert scan.event_fields() == {"guards": 1, "guards_dropped": ""}
    # Case A: 하네스가 아는 것은 entry 함수뿐이고 sink는 모른다 — 훑지 않고, 안 훑은 사유를 남긴다
    case_a = scan_guards(_ref(sink, given_end="source"), anchor, _BASIC, _raw(_BASIC))
    assert (case_a.skipped, case_a.block) == ("case_a", "")
    assert case_a.event_fields() == {"guards_skipped": "case_a"}
    # sink 줄이 원문 밖이거나 sink에서 이름을 못 뽑으면 훑을 수 없다
    assert scan_guards(_ref(999), anchor, _BASIC, _raw(_BASIC)).skipped == "no_sink"


def test_a_window_anchor_gets_no_block_because_the_text_may_hold_other_functions() -> None:
    """품는 함수를 모르면 앵커가 entry 앞뒤 고정 창이다 — 그 창에는 **다른 함수**의 조건이 든다.
    독립 검토의 끝에서 끝까지 실행: 다른 함수의 `if (n > 64) return -1`이 가드 없는 sink 위에
    올랐다(조건 행이 든 창 블록의 33%에 남의 행). 창이면 블록을 안 만들고 trace에 사유를 남긴다."""
    src = _src("""
        int g(int n)
        {
            if (n > 64)
                return -1;
            return n;
        }
        void copy_plain(char *dst, const char *src, int n)
        {
            memcpy(dst, src, n);
        }
    """)
    window = Location(file=FILE, line_start=FIRST, line_end=FIRST + 9)
    scan = scan_guards(_ref(_line(src, "memcpy(")), window, src, _raw(src))
    assert (scan.skipped, scan.block) == ("window", "")
    assert scan.event_fields() == {"guards_skipped": "window"}
    # 같은 원문을 함수 앵커로 읽으면 훑는다 — 사유는 앵커의 종류뿐이다
    function = Location(file=FILE, symbol="copy_plain", line_start=FIRST + 6, line_end=FIRST + 9)
    body = "\n".join(src.split("\n")[6:])
    assert scan_guards(_ref(_line(src, "memcpy(")), function, body, _raw(src)).skipped is None


_BREAKS = ["\f", "\v", "\x1c", "\x1d", "\x1e", "\x85", chr(0x2028), chr(0x2029)]
_FN = [
    "int f(char *dst, const char *src, size_t n)",
    "{",
    "    if (n > MAX)",
    "        return -1;",
    "    memcpy(dst, src, n);",
    "    return 0;",
    "}",
]


def _as_read_source_does(file_text: str, start: int, end: int) -> str:
    """`read_symbol_source`가 하는 그대로 — `splitlines()`로 줄을 세어 자른다(#259)."""
    return "\n".join(file_text.splitlines()[start - 1 : end])


def _anchor_and_text(file_text: str) -> tuple[Location, int, str]:
    """슬라이서·인덱서가 세는 줄(`\\n`만)로 앵커·sink 줄을 잡고, 원문은 `splitlines()`로 자른다."""
    real = file_text.split("\n")
    start = next(i for i, line in enumerate(real) if line.startswith("int f(")) + 1
    end = next(i for i, line in enumerate(real) if i >= start and line == "}") + 1
    sink = real.index("    memcpy(dst, src, n);") + 1
    anchor = Location(file=FILE, symbol="f", line_start=start, line_end=end)
    return anchor, sink, _as_read_source_does(file_text, start, end)


@pytest.mark.parametrize("char", _BREAKS)
@pytest.mark.parametrize("where", ["before", "inside"])
def test_a_file_with_a_break_only_splitlines_sees_before_or_inside_the_function_is_skipped(
    char: str, where: str
) -> None:
    """`read_source`는 `splitlines()`로 줄을 세는데 슬라이서·인덱서는 줄바꿈만 센다(#259) — 함수
    앞·안에 폼피드 같은 글자가 있으면 원문이 밀려 sink 줄이 다른 줄이다. 독립 검토: 폼피드 하나로
    훑은 114건 중 30건이 틀렸고 7건은 「없음」이 됐다. 이름·마지막 줄 검사는 우연히 맞아 못
    걸러서, 파일 원문에 그런 글자가 있으면 훑지 않는다(`formfeed`)."""
    fn = list(_FN)
    if where == "inside":
        fn.insert(2, char)
    head = ["int g(void)", "{", "}", char if where == "before" else "", ""]
    file_text = "\n".join([*head, *fn]) + "\n"
    anchor, sink, text = _anchor_and_text(file_text)
    scan = scan_guards(_ref(sink), anchor, text, _raw(file_text))
    assert (scan.skipped, scan.block) == ("formfeed", "")
    assert scan.event_fields() == {"guards_skipped": "formfeed"}


@pytest.mark.parametrize("char", ["\f", chr(0x2028)])
def test_a_break_after_the_function_or_a_plain_file_does_not_stop_the_scan(char: str) -> None:
    file_text = "\n".join(["int g(void)", "{", "}", "", *_FN, char, "int h(void) { return 0; }"])
    anchor, sink, text = _anchor_and_text(file_text + "\n")
    scan = scan_guards(_ref(sink), anchor, text, _raw(file_text + "\n"))
    assert scan.skipped is None and scan.shown == 1


def test_a_function_named_on_its_second_line_or_closed_with_a_comment_is_scanned() -> None:
    """GNU 꼴(`static int` 다음 줄에 이름)과 `} /* … */`로 닫는 함수도 훑는다."""
    src = _src("""
        static int
        parse (const char *src, size_t n)
        {
            memcpy(buf, src, n);
        } /* parse */
    """)
    anchor = Location(file=FILE, symbol="parse", line_start=FIRST, line_end=FIRST + 4)
    assert scan_guards(_ref(_line(src, "memcpy(")), anchor, src, _raw(src)).skipped is None


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


def test_guards_on_case_a_adds_no_block_and_says_why_in_the_event() -> None:
    ctx, events = _ctx(_FILES, [_ANCHOR], auditor_guards=1)
    ref = _ref(_SINK, given_end="source", entry_symbol="parse")
    assert runner._auditor_source(ctx, ref, _ANCHOR, "cand") == _BASIC
    guard_keys = {key: value for key, value in events[-1].items() if key.startswith("guards")}
    assert guard_keys == {"guards_skipped": "case_a"}


def test_guards_on_a_window_anchor_adds_no_block_and_says_why_in_the_event() -> None:
    """품는 함수를 모르는 앵커(entry 앞뒤 창)에는 다른 함수의 조건이 든다 — 블록을 안 붙인다."""
    ctx, events = _ctx(_FILES, [], auditor_guards=1)  # 인덱스에 심볼이 없다
    window = Location(file=FILE, line_start=FIRST, line_end=FIRST + 12)
    source = runner._auditor_source(ctx, _ref(_SINK), window, "cand")
    assert source == _BASIC and GUARDS_HEADER not in source
    guard_keys = {key: value for key, value in events[-1].items() if key.startswith("guards")}
    assert guard_keys == {"guards_skipped": "window"}


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
        [_banner("parser.c", 19, 26), *_PARSER[18:26], _none("buf, len, src")]
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
    assert {k: v for k, v in event.items() if k.startswith("guards")} == {
        "guards_skipped": "case_a"
    }


# --- 독립 검토가 짚은 것 — 가짜 탈출 표시 · 줄이 어긋난 원문 · 안 잡히던 갈래 --------------------


@pytest.mark.parametrize(
    ("body", "tail"),
    [
        # 몸통의 **문장 머리**에 있는 탈출만 센다 — 안쪽 `if`·반복문의 몸통·`else`·멤버 호출은 아님
        (["if (n > MAX) { if (strict) return -1; log_it(); }"], ""),
        (["if (n > MAX) { for (i = 0; i < n; i++) if (src[i] == 0) break; n = i; }"], ""),
        (["if (n > MAX) { if (a) b = 1; else return -1; }"], ""),
        (["if (n > MAX) txn.abort();"], ""),
        (["if (n > MAX) txn->exit(1);"], ""),
        (["if (n > MAX) { log_it(); return -1; }"], " [exit: return -1]"),
        (["if (n > MAX) { if (a) { x(); } return -1; }"], " [exit: return -1]"),
    ],
)
def test_an_exit_counts_only_when_it_starts_a_statement_of_the_guarded_body(
    body: list[str], tail: str
) -> None:
    """안쪽 `if (strict) return`의 탈출이 바깥 가드의 것으로 읽혀 가짜 `[exit: …]`가 붙었다. 독립
    검토가 curl·zlib·libxml2에서 `if` 행의 약 10%가 그랬다고 보고했다(재측정 안 함 · 로컬 · run
    ID 없음)."""
    src = _func(*body, "memcpy(dst, src, n);")
    first = _scan(src, "memcpy(").rows[0]  # 안쪽 구성의 행이 뒤따를 수 있다 — 바깥 `if`가 첫 행이다
    assert first == _row(src, "if (n > MAX)", f"if (n > MAX){tail}")


@pytest.mark.parametrize(
    "exit_stmt", ["break", "continue", "abort()", "exit(1)", "goto fail", "return", "return -1"]
)
def test_every_early_exit_kind_is_reported(exit_stmt: str) -> None:
    src = _func(f"if (n > MAX) {exit_stmt};", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (
        _row(src, "if (n > MAX)", f"if (n > MAX) [exit: {exit_stmt}]"),
    )


@pytest.mark.parametrize(
    "macro",
    [
        "CHECK(n < 8)",
        "DCHECK(n < 8)",
        "CHECK_LT(n, 8)",
        "ND_TCHECK_LEN(src, n)",
        "ENSURE(n < 8)",
        "REQUIRE_NOT_NULL(src)",
        "VERIFY(n < 8)",
        "WARN_ON(n > 8)",
        "WARN_ON_ONCE(n > 8)",
        "g_return_val_if_fail(n < 8, -1)",
        "DEBUGASSERT(n < 8)",
        "ck_assert_int_lt(n, 8)",
    ],
)
def test_assert_like_macro_names_are_guards(macro: str) -> None:
    """이름에 `assert`가 든 것(대소문자 무관)·`CHECK`가 든 것(`ND_TCHECK_LEN` — tcpdump의 경계
    검사)·`ENSURE`·`REQUIRE`·`VERIFY`·`BUG_ON`·`WARN_ON`으로 시작하는 것·`g_return_*`."""
    src = _func(f"{macro};", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (_row(src, macro, macro),)


def test_a_long_condition_and_a_long_exit_are_clipped() -> None:
    cond = " || ".join(f"n == {i}" for i in range(40))
    src = _func(f"if ({cond})", f"    return {'x' * 100};", "memcpy(dst, src, n);")
    (row,) = _scan(src, "memcpy(").rows
    condition, exit_ = row.split(" | ", 1)[1].split(" [exit: ")
    assert len(condition) == _ROW_CHARS and condition.endswith("...")
    assert len(exit_.removesuffix("]")) == _EXIT_CHARS and exit_.endswith("...]")


def test_a_comparison_operator_is_not_an_assignment_boundary_of_a_ternary() -> None:
    src = _func("n = n >= MAX ? MAX : n;", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (_row(src, "n = n >=", "n >= MAX ? ... : ..."),)


def test_a_row_keeps_the_string_literals_of_its_condition() -> None:
    cond = 'strcmp(name, "a b") != 0 && n > 3'
    src = _func(f"if ({cond})", "    return -1;", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (
        _row(src, "if (strcmp", f"if ({cond}) [exit: return -1]"),
    )


def test_two_calls_of_the_sink_name_on_the_sink_line_both_contribute_names() -> None:
    src = _func("if (m > 4)", "    return;", "memcpy(a, b, n); memcpy(c, d, m);")
    assert _scan(src, "memcpy(a").rows == (_row(src, "if (m > 4)", "if (m > 4) [exit: return]"),)


def test_the_sink_name_must_start_a_word_and_stay_on_the_sink_line() -> None:
    """`memcopy(`는 `copy(`가 아니다 · 아래 줄의 같은 이름 호출은 이 sink가 아니다."""
    src = _func("if (k > 3)", "    return;", "x = memcopy(a, k); copy(dst, src, n);")
    assert _scan(src, "copy(dst", name="copy").block == NONE_FOUND
    later = _func("if (m > 4)", "    return;", "memcpy(dst, src, n);", "memcpy(x, y, m);")
    assert _scan(later, "memcpy(dst").block == NONE_FOUND


def test_keywords_do_not_link_a_guard_to_the_sink() -> None:
    """`sizeof x`(괄호 없는 꼴 — `sizeof(`는 호출처럼 보여 따로 빠진다)의 `sizeof`는 이름 아님."""
    src = _func("if (other > sizeof tmp)", "    return;", "memcpy(dst, src, sizeof buf);")
    assert _scan(src, "memcpy(").block == _none("buf, dst, src")  # `sizeof`는 이름에 없다


def test_an_operator_sink_counts_a_guard_that_leads_its_own_line_only() -> None:
    op = "<operator>.indirectIndexAccess"
    src = _func("if (i < n) a[i] = 1;", "a[i] = 2; if (i > 7) log_it();")
    compact = _row(src, "if (i < n) a[i]", "if (i < n)")
    assert _scan(src, "if (i < n) a[i]", name=op).rows == (compact,)
    # 이전 줄의 `if`는 앞이고, 같은 줄에서 sink 뒤에 오는 `if (i > 7)`은 아니다
    assert _scan(src, "a[i] = 2;", name=op).rows == (compact,)


# --- 별칭 한 걸음 — 실제 GT sink에서 「없음」이 틀렸던 세 꼴(독립 검토) --------------------------
#
# 아래 셋은 82개 GT sink 중 「없음」 12개에서 틀렸던 3개(gd_io_dp.c:290 · xmlIO.c:362 ·
# ttcpal.c:133)의 **꼴**을 줄여 새로 쓴 것이다 — 인자 이름만으로는 가드와 안 이어지고, 대입 한 번을
# 거쳐야 이어진다. 원문 복사가 아니다(각 저장소의 그 커밋을 읽어 꼴을 맞췄다).

_GD_GETBUF = _src("""
    static int dynamicGetbuf(gdIOCtxPtr ctx, void *buf, int len)
    {
        int rlen, remain;
        dpIOCtxPtr dctx;
        dynamicPtr *dp;

        dctx = (dpIOCtxPtr) ctx;
        dp = dctx->dp;

        remain = dp->logicalSize - dp->pos;
        if (remain >= len) {
            rlen = len;
        } else {
            if (remain <= 0) {
                return 0;
            }
            rlen = remain;
        }

        memcpy(buf, (void *) ((char *)dp->data + dp->pos), rlen);
        dp->pos += rlen;
        return rlen;
    }
""")

_XML_ESCAPE = _src("""
    static xmlChar *escapeText(const xmlChar *string, size_t size)
    {
        xmlChar *buffer, *out;
        const xmlChar *cur, *base;
        const char *repl;
        size_t used, replSize, unescapedSize, totalSize;

        buffer = xmlMalloc(size + 1);
        if (buffer == NULL)
            return(NULL);
        out = buffer;
        cur = string;

        while (*cur != 0) {
            base = cur;
            cur += 1;
            unescapedSize = cur - base;
            replSize = 5;
            repl = "&amp;";

            used = out - buffer;
            totalSize = unescapedSize + replSize;

            if (totalSize > size - used) {
                xmlChar *tmp;
                size_t newSize;

                if ((size > (SIZE_MAX - 1) / 2) ||
                    (totalSize > (SIZE_MAX - 1) / 2 - size)) {
                    xmlFree(buffer);
                    return(NULL);
                }
                newSize = size + totalSize;
                tmp = xmlRealloc(buffer, newSize + 1);
                if (tmp == NULL) {
                    xmlFree(buffer);
                    return(NULL);
                }
                buffer = tmp;
                size = newSize;
                out = buffer + used;
            }

            memcpy(out, base, unescapedSize);
            out += unescapedSize;
            memcpy(out, repl, replSize);
            out += replSize;
        }

        *out = 0;
        return(buffer);
    }
""")

_FT_CPAL = _src("""
    FT_Error tt_face_load_cpal(TT_Face face, FT_Stream stream)
    {
        FT_Byte  *table = NULL;
        FT_Byte  *p     = NULL;
        Cpal     *cpal  = NULL;
        FT_ULong  colors_offset;
        FT_ULong  table_size;
        FT_Error  error;

        error = face->goto_table(face, TTAG_CPAL, stream, &table_size);
        if (error)
            goto NoCpal;

        if (table_size < CPAL_V0_HEADER_BASE_SIZE)
            goto InvalidTable;

        if (FT_FRAME_EXTRACT(table_size, table))
            goto NoCpal;

        p = table;
        cpal->version = FT_NEXT_USHORT(p);
        if (cpal->version > 1)
            goto InvalidTable;

        colors_offset = FT_NEXT_ULONG(p);
        if (colors_offset >= table_size)
            goto InvalidTable;

        cpal->color_indices = p;
        cpal->colors = (FT_Byte*)(table + colors_offset);

        if (cpal->version == 1)
        {
            FT_ULong type_offset, label_offset;

            p += face->palette_data.num_palettes * 2;

            type_offset  = FT_NEXT_ULONG(p);
            label_offset = FT_NEXT_ULONG(p);
        }
        return 0;
    }
""")


def _rows_text(scan) -> list[str]:  # noqa: ANN001
    """행에서 `파일:줄 | ` 앞머리를 뗀 본문들."""
    return [row.split(" | ", 1)[1] for row in scan.rows]


def test_a_clamp_through_a_sink_argument_alias_is_found_in_the_gd_getbuf_shape() -> None:
    """gd_io_dp.c:290 — sink 인자는 `rlen`이고 가드는 `remain`·`len`을 본다. `rlen = len;`·
    `rlen = remain;`이 그 둘을 인자의 이름으로 만든다(인자를 계산한 이름)."""
    scan = _scan(_GD_GETBUF, "memcpy(buf")
    assert _rows_text(scan) == ["if (remain >= len)", "if (remain <= 0) [exit: return 0]"]
    assert {"len", "remain", "rlen"} <= set(scan.names)


def test_a_capacity_check_on_a_value_derived_from_an_argument_is_found_in_the_xml_shape() -> None:
    """xmlIO.c:362 — sink `memcpy(out, repl, replSize)`의 용량 검사는 `totalSize`·`used`를 본다.
    `totalSize = unescapedSize + replSize;`·`used = out - buffer;`가 그 둘을 이름으로 만든다
    (인자에서 나온 이름)."""
    scan = _scan(_XML_ESCAPE, "memcpy(out, repl")
    rows = _rows_text(scan)
    assert "if (totalSize > size - used)" in rows
    assert any(
        row.startswith("if ((size > (SIZE_MAX - 1) / 2) || (totalSize")
        and row.endswith("[exit: return(NULL)]")
        for row in rows
    )
    assert not scan.block.startswith("GUARDS: no condition")


@pytest.mark.parametrize("name", ["<operator>.indirectIndexAccess", "FT_NEXT_ULONG"])
def test_the_table_size_checks_are_found_in_the_freetype_cpal_shape(name: str) -> None:
    """ttcpal.c:133 — sink 줄 `type_offset = FT_NEXT_ULONG(p)`의 이름은 `type_offset`·`p`뿐인데
    `table_size` 검사는 `colors_offset`·`table`을 본다. `p = table;`·`colors_offset =
    FT_NEXT_ULONG(p);`가 이어 준다."""
    scan = _scan(_FT_CPAL, "type_offset  = FT_NEXT_ULONG(p)", name=name)
    rows = _rows_text(scan)
    assert "if (colors_offset >= table_size) [exit: goto InvalidTable]" in rows
    assert "if (FT_FRAME_EXTRACT(table_size, table)) [exit: goto NoCpal]" in rows
    assert "if (cpal->version == 1)" in rows  # sink를 품은 조건이다


def test_a_name_used_to_compute_a_sink_argument_counts() -> None:
    src = _func("n = len - off;", "if (len > cap)", "    return -1;", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (
        _row(src, "if (len > cap)", "if (len > cap) [exit: return -1]"),
    )


def test_a_name_assigned_from_a_sink_argument_counts() -> None:
    src = _func("limit = n * 2;", "if (limit > cap)", "    return -1;", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (
        _row(src, "if (limit > cap)", "if (limit > cap) [exit: return -1]"),
    )


@pytest.mark.parametrize(
    ("assign", "guarded"),
    [("n -= off;", "off"), ("n <<= off;", "off"), ("size_t n = len - off;", "len")],
)
def test_compound_assignments_and_declarations_with_initializers_alias_too(
    assign: str, guarded: str
) -> None:
    src = _func(assign, f"if ({guarded} > cap)", "    return;", "memcpy(dst, src, n);")
    assert _scan(src, "memcpy(").rows == (
        _row(src, f"if ({guarded} > cap)", f"if ({guarded} > cap) [exit: return]"),
    )


def test_the_alias_hop_is_one_and_only_before_the_sink() -> None:
    two_hops = _func("a = n;", "b = a;", "if (b > cap)", "    return;", "memcpy(dst, src, n);")
    scan = _scan(two_hops, "memcpy(")
    assert scan.block == _none("a, dst, n, src")  # `a`는 한 걸음, `b`는 두 걸음이라 안 이어진다
    after = _func("if (len > cap)", "    return;", "memcpy(dst, src, n);", "n = len;")
    assert _scan(after, "memcpy(").block == NONE_FOUND  # 뒤의 대입은 안 본다


def test_a_member_target_links_through_its_root_but_an_index_does_not() -> None:
    member = _func(
        "face->size = table_size;",
        "if (table_size > cap)",
        "    return;",
        "memcpy(dst, face->buf, n);",
    )
    assert _scan(member, "memcpy(").rows == (
        _row(member, "if (table_size > cap)", "if (table_size > cap) [exit: return]"),
    )
    # `table[n] = value;`에서 `n`은 첨자일 뿐 — `value`가 `n`의 출처인 것은 아니다
    indexed = _func("table[n] = value;", "if (value > cap)", "    return;", "memcpy(dst, src, n);")
    assert _scan(indexed, "memcpy(").block == NONE_FOUND


@pytest.mark.parametrize("op", [">=", "<=", "==", "!="])
def test_comparison_operators_are_not_assignments(op: str) -> None:
    """`n >= m`을 대입으로 읽으면 `m`이 이름이 되어 무관한 `if (m > cap)`까지 올라온다."""
    src = _func(
        f"if (n {op} m)", "    return;", "if (m > cap)", "    return;", "memcpy(dst, src, n);"
    )
    assert _rows_text(_scan(src, "memcpy(")) == [f"if (n {op} m) [exit: return]"]
