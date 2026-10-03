"""Auditor `SOURCE` 정의 조회의 위생(#349 (f)) — 무엇의 정의를 찾을지 거르고, 슬라이스 줄의 이름을
먼저 싣는다. `src/orchestrator/source_definitions.py`의 순수 함수만 본다. `_auditor_source`에 꽂힌
모양은 `test_slice_targets.py`가 본다.

핀은 **저장소의 실형태**에서 왔다 — 지어낸 샘플은 잡히는 모양만 고르게 된다. 필드 접근 `->len`·
`.size`는 `src/adapters/slices.py`·libzip 계열 소스(`za->nentry`, `st.size`)의 모양이고, 태그와 같은
이름의 지역 변수 `struct archive *archive`는 `examples/in-memory.c`·`src/zipcmp.c`의 모양이다.
"""

from src.orchestrator.source_definitions import (
    bare_names,
    code_only,
    definition_names,
    macro_alias_target,
    slice_line_names,
    slice_names_first,
)
from src.schemas.common import Location


def _loc(file: str, symbol: str | None, start: int, end: int) -> Location:
    return Location(file=file, symbol=symbol, line_start=start, line_end=end)


# --- bare_names: 필드 접근 자리는 식별자 사용이 아니다 ----------------------------------------


def test_a_name_used_only_as_a_field_is_not_a_bare_name() -> None:
    """`p->len`·`q.size`의 `len`·`size`는 구조체 필드다 — 같은 철자의 매크로·타입·전역을 가리키지
    않는다. `->`·`.` 앞뒤 공백도 같다(`r -> tail`)."""
    text = "p->len = n;\nq.size++;\nr -> tail = s . head;"

    assert bare_names(text) == {"p", "n", "q", "r", "s"}


def test_a_name_used_bare_anywhere_stays_even_if_it_is_also_a_field() -> None:
    """`len = p->len;`의 앞 `len`은 변수(또는 매크로) 자리다 — 필드로도 쓰였다고 빼지 않는다."""
    assert bare_names("len = p->len;") == {"len", "p"}


def test_c_keywords_are_not_names() -> None:
    assert bare_names("if (x) return NULL; else sizeof(int);") == {"x"}


def test_a_gnu_case_range_is_not_a_field_access() -> None:
    """`case LOW ... HIGH:`의 `...`는 점 셋이다 -- 뒤 `HIGH`가 필드로 읽혀 빠지던 것(점 하나만 필드
    접근이다). 공백 없는 `LOW...HIGH`도 같다. 같은 줄의 진짜 필드 접근은 그대로 거른다."""
    assert bare_names("case LOW ... HIGH: x = p->len;") == {"LOW", "HIGH", "x", "p"}
    assert bare_names("case LOW...HIGH: break;") == {"LOW", "HIGH"}


# --- definition_names: 함수마다 그 함수의 지역·파라미터를 뺀다 -------------------------------


def _locals(table: dict[tuple[str, str], set[str]]):  # noqa: ANN202
    return lambda file, func: frozenset(table[(file, func)]) if (file, func) in table else None


def test_a_functions_locals_and_params_are_not_looked_up() -> None:
    """`n`·`p`는 파라미터, `za`는 지역이다 — 다른 파일의 `za` 변수·`n` 매크로를 가리키지 않는다.
    전역 `GLOBAL_MAX`는 남는다."""
    text = "int f(struct s *p, int n)\n{\n    int za = p->len + n;\n    return GLOBAL_MAX + za;\n}"
    f = _loc("u.c", "f", 1, 5)

    used = definition_names([(f, text)], _locals({("u.c", "f"): {"p", "n", "za"}}))

    assert "GLOBAL_MAX" in used
    assert not used & {"p", "n", "za", "len"}


def test_a_local_of_one_function_is_still_looked_up_for_the_other() -> None:
    """지역은 **그 함수의** 것이다 — 합집합에서 빼면 `g`가 전역 `x`를 쓰는 것까지 사라진다."""
    f_text = "void f(void) { int x = 1; use(x); }"
    g_text = "void g(void) { use(x); }"
    f, g = _loc("u.c", "f", 1, 1), _loc("u.c", "g", 3, 3)

    used = definition_names([(f, f_text), (g, g_text)], _locals({("u.c", "f"): {"x"}}))

    assert "x" in used


def test_a_struct_tag_with_a_locals_name_is_still_looked_up() -> None:
    """`struct archive *archive`의 태그 `archive`는 변수 `archive`와 이름공간이 다르다 — 지역이어도
    **구조체 정의를 찾아야 한다**(필드 배치가 Auditor가 쓰는 정보다). 태그 자리에 안 나온 지역은
    그대로 빠진다."""
    text = "void f(void) { struct archive *archive = open(); archive->n = 1; flag = 2; }"
    f = _loc("u.c", "f", 1, 1)

    used = definition_names([(f, text)], _locals({("u.c", "f"): {"archive", "flag"}}))

    assert "archive" in used
    assert "flag" not in used


def test_a_union_or_enum_tag_counts_the_same_way() -> None:
    text = "void f(void) { union u u; enum e e; }"
    f = _loc("u.c", "f", 1, 1)

    used = definition_names([(f, text)], _locals({("u.c", "f"): {"u", "e"}}))

    assert {"u", "e"} <= used


def test_no_index_means_nothing_to_remove() -> None:
    """`locals_of`가 `None`(v2 표 없음)을 주면 필드 접근만 거른다 — 지역은 알 길이 없다."""
    text = "void f(void) { za = p->len; }"
    f = _loc("u.c", "f", 1, 1)

    assert definition_names([(f, text)], lambda _file, _func: None) == {"f", "za", "p"}


def test_a_window_anchor_has_no_function_to_ask_about() -> None:
    """entry를 품는 심볼을 모르는 창 anchor(`symbol` 없음)는 지역 표를 묻지 않는다."""
    asked: list[tuple[str, str]] = []

    def locals_of(file: str, func: str) -> frozenset[str]:
        asked.append((file, func))
        return frozenset()

    window = Location(file="u.c", line_start=1, line_end=3)

    used = definition_names([(window, "za = p->len;")], locals_of)

    assert asked == [] and used == {"za", "p"}


def test_a_block_scope_extern_names_the_global_not_a_local() -> None:
    """인덱서는 함수 안 `extern int g_hdr;`도 지역으로 적는다(함수 안 `declaration`은 전부) -- 그
    이름은 **전역을 가리킨다.** 파일 변수 정의를 찾아야 하고, 같은 함수의 진짜 지역 `za`는 그대로
    빠진다."""
    text = "void f(void) { extern int g_hdr; int za = g_hdr; }"
    f = _loc("u.c", "f", 1, 1)

    used = definition_names([(f, text)], _locals({("u.c", "f"): {"g_hdr", "za"}}))

    assert "g_hdr" in used
    assert "za" not in used


def test_a_local_called_with_parens_still_resolves_the_function_like_macro() -> None:
    """`int min, max;`옆의 `max(a, b)` -- 전처리는 범위를 안 봐서 `max(`는 함수형 매크로 호출이다.
    지역 `max`는 괄호로 불렸으니 남고, 괄호로 안 불린 지역 `min`은 그대로 빠진다."""
    text = "int f(int min, int max) { return max(min, 1); }"
    f = _loc("u.c", "f", 1, 1)

    used = definition_names([(f, text)], _locals({("u.c", "f"): {"min", "max"}}))

    assert "max" in used
    assert "min" not in used


def test_a_field_call_is_not_a_macro_call() -> None:
    """`ops->read(read)`의 `read(`는 함수 포인터 필드 호출이라 매크로가 아니다 -- 지역 `read`를
    되살리지 않는다(필드 자리의 호출은 호출 규칙에서 뺀다)."""
    text = "void f(void) { int read = 0; ops->read(read); }"
    f = _loc("u.c", "f", 1, 1)

    used = definition_names([(f, text)], _locals({("u.c", "f"): {"read"}}))

    assert "read" not in used


def test_a_typedef_named_like_a_local_is_a_known_limit() -> None:
    """**알려진 한계**(`definition_names`의 ponytail): `node *node`의 앞 `node`는 typedef인데
    지역으로 읽혀 안 찾는다 -- `n * n`과 글자로는 같아서 문장 머리·파라미터 목록의 타입 자리를
    구문 없이 못 가른다. 고치면(예: 인덱서가 선언의 타입 이름도 `locals`에 싣는다) 이 테스트를
    뒤집는다."""
    text = "void f(void) { node *node = get(); node->next = 0; }"
    f = _loc("u.c", "f", 1, 1)

    used = definition_names([(f, text)], _locals({("u.c", "f"): {"node"}}))

    assert "node" not in used


# --- 주석·문자열·문자 리터럴은 코드가 아니다 --------------------------------------------------
#
# E4(83표본의 GT sink 함수, 로컬·run ID 없음)가 정의 1,023건 중 15.0%(153건)를 「주석·문자열에만
# 나온 낱말」로 센 것이 이 절의 출처다 -- 본문 원문을 그대로 토큰으로 쪼개서 생긴다.


def _names_of(text: str) -> set[str]:
    """지역 표 없이(`None`) 이름만 본다 -- 이 절은 지역이 아니라 **코드 자리**를 묻는다."""
    return definition_names([(_loc("u.c", "f", 1, 9), text)], lambda _file, _func: None)


def test_a_word_only_in_a_comment_is_not_a_name() -> None:
    """`/* */`·`//` 안의 낱말은 같은 철자의 매크로·전역 정의를 끌어오지 않는다. 코드의 `run`은
    남는다."""
    text = "void f(void) {\n    /* clamp to FOO_MAX */\n    // see BAR_LIMIT\n    run();\n}"

    used = _names_of(text)

    assert "run" in used
    assert not used & {"FOO_MAX", "BAR_LIMIT", "clamp", "see"}


def test_a_word_only_in_a_string_or_char_literal_is_not_a_name() -> None:
    text = 'void f(void) { log("BUF_SIZE exceeded"); c = \'Z\'; run(); }'

    used = _names_of(text)

    assert {"log", "c", "run"} <= used
    assert not used & {"BUF_SIZE", "exceeded", "Z"}


def test_a_word_in_code_and_in_a_comment_still_resolves() -> None:
    """주석에도 나오지만 **코드에도** 나온 낱말은 그대로 찾는다 -- 주석을 지우는 것이지 낱말을
    금지하는 것이 아니다."""
    text = "void f(void) {\n    /* LIMIT is checked below */\n    if (n > LIMIT) bail();\n}"

    assert "LIMIT" in _names_of(text)


def test_a_word_in_code_and_in_a_string_still_resolves() -> None:
    assert "LIMIT" in _names_of('void f(void) { log("LIMIT hit"); use(LIMIT); }')


def test_escapes_and_quote_characters_do_not_end_a_literal_early() -> None:
    r"""`\"`로 이스케이프한 따옴표 · `'\''` · `'"'`는 리터럴을 일찍 닫지 않는다 -- 닫히면 뒤의
    낱말이 코드로 읽히거나(`HIDDEN_A`) 뒤 코드가 문자열로 먹힌다(`keep`·`VISIBLE`)."""
    text = r"""void f(void) { puts("say \"HIDDEN_A\" now"); c = '\''; d = '"'; keep(VISIBLE); }"""

    used = _names_of(text)

    assert {"keep", "VISIBLE", "c", "d"} <= used
    assert not used & {"HIDDEN_A", "say", "now"}


def test_a_string_ending_in_an_escaped_backslash_closes() -> None:
    r"""`"a\\"`는 백슬래시 하나를 담고 **닫힌다** -- 이스케이프를 `\"`로만 읽으면 뒤 줄을 먹는다."""
    used = _names_of('void f(void) { p = "a\\\\"; keep(VISIBLE); }')

    assert {"keep", "VISIBLE"} <= used


def test_a_comment_marker_in_a_string_and_a_quote_in_a_comment_are_not_special() -> None:
    """문자열 속 `//`(`"http://HOST_NAME"`)는 주석이 아니고 주석 속 따옴표(`it's`·`"trap`)는
    리터럴을 열지 않는다 -- 왼쪽부터 한 번에 훑어 먼저 시작한 쪽이 이긴다."""
    text = (
        'void f(void) { u = "http://HOST_NAME"; keep(A); /* it\'s a "trap */ keep(B);\n'
        "    // don't\n    keep(C); }"
    )

    used = _names_of(text)

    assert {"keep", "A", "B", "C", "u"} <= used
    assert not used & {"HOST_NAME", "it", "trap", "don"}


def test_a_line_comment_continues_over_a_backslash_newline() -> None:
    r"""줄 끝 `\`는 다음 줄을 잇는다 -- C에서 `// ... \`의 다음 줄도 같은 주석이다(`HIDDEN_C`). 잇는
    `\`가 없으면 다음 줄은 코드다(`keep`)."""
    text = "// note \\\n   HIDDEN_C();\nkeep(A);"

    assert _names_of(text) == {"keep", "A"}


def test_an_unterminated_block_comment_runs_to_the_end() -> None:
    """읽은 범위가 주석 한가운데서 끝나도(`slice_line_names`) 열린 주석은 끝까지 주석이다."""
    assert _names_of("keep(A); /* HIDDEN never closed") == {"keep", "A"}


def test_code_only_keeps_the_line_count_and_separates_tokens() -> None:
    """지운 자리는 공백 하나이고 안에 든 줄바꿈은 남는다 -- 줄 번호로 고르는 쪽
    (`slice_line_names`)이 안 어긋나고, `a/*c*/b`가 `ab`로 붙지 않는다."""
    assert code_only("a/*c*/b") == "a b"
    assert code_only("x /* a\nb\nc */ y").count("\n") == 2
    assert code_only('s = "a"; // tail\nt') == "s =  ;  \nt"


# --- slice_names_first: 슬라이스 줄에 나온 이름의 정의가 먼저 ---------------------------------


def _reader(files: dict[str, list[str]]):  # noqa: ANN202
    reads: list[Location] = []

    def read(loc: Location) -> str:
        reads.append(loc)
        lines = files[loc.file]
        return "\n".join(lines[(loc.line_start or 1) - 1 : loc.line_end])

    return read, reads


_FILE = {"u.c": [f"line{i} tok{i}" for i in range(1, 11)]}


def test_slice_line_names_come_from_the_slice_lines_only() -> None:
    """3·7번 줄의 이름만 — 사이 줄(4~6)의 이름은 아니다. 파일마다 한 번, 줄 범위 하나로 읽는다."""
    read, reads = _reader(_FILE)

    names = slice_line_names(read, [("u.c", 7), ("u.c", 3)])

    assert names == {"line3", "tok3", "line7", "tok7"}
    assert reads == [Location(file="u.c", line_start=3, line_end=7)]


def test_slice_line_names_skip_comments_and_keep_the_rows_aligned() -> None:
    """슬라이스 줄 3은 두 줄짜리 블록 주석(2~3)의 **끝줄**이다 -- 그 낱말(`HIDDEN2`)은 이름이
    아니고, 주석이 여러 줄이어도 4번 줄은 4번 줄이다(지운 자리가 줄바꿈을 남긴다)."""
    read, _ = _reader({"u.c": ["x = A;", "/* HIDDEN", "   more HIDDEN2 */", "y = B;"]})

    assert slice_line_names(read, [("u.c", 1), ("u.c", 3), ("u.c", 4)]) == {"x", "A", "y", "B"}


def test_slice_line_names_skip_field_access_and_missing_lines() -> None:
    read, _ = _reader({"u.c": ["p->len = q;", "short"]})

    # 9번 줄은 파일 밖이다 — 읽은 범위가 모자라도 죽지 않는다. 0번 줄은 `Location`이 못 만든다.
    assert slice_line_names(read, [("u.c", 1), ("u.c", 9), ("u.c", 0)]) == {"p", "q"}


def test_definitions_named_on_the_slice_come_first_in_their_old_order() -> None:
    """슬라이스 줄에 나온 이름(`CCC`)의 정의가 앞으로 오고, 나머지는 원래 순서(안정 정렬)다."""
    files = {"u.c": ["x = AAA;", "y = CCC;", "z = 0;"]}
    read, _ = _reader(files)
    aaa, bbb, ccc = (_loc("h.h", name, i, i) for i, name in enumerate(("AAA", "BBB", "CCC"), 1))

    ordered = slice_names_first([aaa, bbb, ccc], read, [("u.c", 2)])

    assert [d.symbol for d in ordered] == ["CCC", "AAA", "BBB"]


def test_nothing_is_read_when_there_is_nothing_to_order() -> None:
    """정의가 둘 미만이면 순서가 없다 — 파일을 읽지 않는다."""
    read, reads = _reader(_FILE)
    only = _loc("h.h", "AAA", 1, 1)

    assert slice_names_first([], read, [("u.c", 2)]) == []
    assert slice_names_first([only], read, [("u.c", 2)]) == [only]
    assert reads == []


# --- macro_alias_target: 이름만 바꿔 넘기는 매크로의 대상 (C4) -----------------------------------
# 양성 핀은 팀 검수 83표본이 든 래퍼·별칭 예(tcpdump `ND_TCHECK` → `ND_TCHECK2` · freetype
# `FT_FRAME_EXTRACT` → `FT_Stream_ExtractFrame` · gpac `gf_strdup` → `gf_mem_strdup`)의 모양이다.


def test_a_rename_macro_names_its_target() -> None:
    assert macro_alias_target("#define ND_TCHECK(p) ND_TCHECK2(p, sizeof(*(p)))") == "ND_TCHECK2"
    assert macro_alias_target("#define gf_strdup gf_mem_strdup") == "gf_mem_strdup"
    assert (
        macro_alias_target(
            "#define FT_FRAME_EXTRACT( size, label ) "
            "FT_Stream_ExtractFrame( stream, size, (FT_Byte**)&label )"
        )
        == "FT_Stream_ExtractFrame"
    )


def test_a_continued_line_and_a_comment_before_the_target_are_skipped() -> None:
    """줄 잇기(백슬래시 줄바꿈)와 대치 목록 앞 주석은 건너뛰고 첫 식별자를 본다."""
    continued = "#  define A " + chr(92) + chr(10) + "    B(1)"
    assert macro_alias_target(continued) == "B"
    assert macro_alias_target("#define A /* old */ B(x)") == "B"


def test_an_expression_or_constant_macro_is_not_an_alias() -> None:
    """식·상수·문장 매크로는 별칭이 아니다 — 대치 목록이 식별자로 시작하지 않거나, 그 식별자가
    자기 이름·파라미터·C 키워드다."""
    assert macro_alias_target("#define MAX(a, b) ((a) > (b) ? (a) : (b))") is None
    assert macro_alias_target("#define X do { f(); } while (0)") is None
    assert macro_alias_target("#define N 64") is None
    assert macro_alias_target('#define S "abc"') is None
    assert macro_alias_target("#define ID(x) x") is None
    assert macro_alias_target("#define SZ(x) sizeof(x)") is None
    assert macro_alias_target("#define R R") is None
    assert macro_alias_target("int f(void);") is None
