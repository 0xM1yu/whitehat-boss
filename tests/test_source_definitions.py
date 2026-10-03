"""Auditor `SOURCE` 정의 조회의 위생(#349 (f)) — 무엇의 정의를 찾을지 거르고, 슬라이스 줄의 이름을
먼저 싣는다. `src/orchestrator/source_definitions.py`의 순수 함수만 본다. `_auditor_source`에 꽂힌
모양은 `test_slice_targets.py`가 본다.

핀은 **저장소의 실형태**에서 왔다 — 지어낸 샘플은 잡히는 모양만 고르게 된다. 필드 접근 `->len`·
`.size`는 `src/adapters/slices.py`·libzip 계열 소스(`za->nentry`, `st.size`)의 모양이고, 태그와 같은
이름의 지역 변수 `struct archive *archive`는 `examples/in-memory.c`·`src/zipcmp.c`의 모양이다.
"""

from src.orchestrator.source_definitions import (
    bare_names,
    definition_names,
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
