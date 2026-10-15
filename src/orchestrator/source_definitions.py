"""Auditor `SOURCE`의 정의 조회(#349 (f) — `auditor_definitions`)가 **무엇의 정의를 찾는가**를
거르고, 찾은 정의를 **어떤 순서로** 싣는지를 정한다. `runner._auditor_source`가 부른다.

## 왜 거르는가

예전에는 실린 함수 본문의 식별자 토큰을 **전부**(C 키워드만 빼고) `definitions_of`에 던졌다. 지역
변수·파라미터·구조체 필드 이름이 다른 파일의 매크로·타입·파일 변수와 철자가 같으면, 그 이름의
정의가 저장소에 **하나뿐**이라는 이유로 그 정의가 붙었다 — `za`가 다른 파일의 전역 `za`를,
`->archive`가 다른 파일의 `struct archive`를 끌어오는 식이다. 주석과 문자열 속 낱말도 똑같이
토큰이 됐다. 글자 상한은 한정돼 있어서 그 정의가 진짜 정의의 자리를 먹는다.

## 무엇을 거르는가 — 셋이고, 셋째에 예외가 셋 있다

1. **주석·문자열·문자 리터럴 속 낱말**(`code_only`) — 코드가 아니다. `/* clamp to LIMIT */`나
   `"BUF_SIZE exceeded"`의 낱말이 같은 철자의 매크로·전역 정의를 끌어왔다. 코드에도 나온 낱말은
   그대로 찾는다 — 낱말을 막는 것이 아니라 **자리**를 본다.
2. **필드 접근 자리**(`p->len`·`q.size`)에만 나온 이름 — 구조체 필드지 같은 철자의 매크로·전역이
   아니다. 점은 하나만 본다 — GNU `case LOW ... HIGH:`의 `...`는 필드 접근이 아니다.
3. 실린 함수 **자신의** 지역·파라미터(`GraphIndex.locals_of`) — 함수마다 따로 뺀다. 합집합에서
   빼면 한 함수의 지역 때문에 다른 함수가 전역으로 쓰는 같은 이름이 사라진다.
   ⚠ **예외 셋 — 지역과 철자가 같아도 지역을 가리키지 않는 자리다**(`_outer_names`):
   - `struct X`·`union X`·`enum X`의 `X` — 태그는 변수와 이름공간이 달라서 `struct archive
     *archive`의 태그 `archive`는 지역 변수 `archive`가 있어도 구조체 정의를 가리킨다. 그 정의가
     필드 배치를 알려 주는 바로 그 정보다.
   - `x(` — 호출 자리. 전처리는 범위를 안 봐서 `int min, max;` 옆의 `max(a, b)`는 함수형 매크로
     호출이다.
   - 함수 안 `extern T x;` — 전역을 가리키는 선언이다. 인덱서는 함수 안 선언을 전부 지역으로 적는다.

같은 철자가 여러 파일에 있을 때 비제품 경로를 한 단계 낮추는 것은 정의 조회 쪽의 일이다 —
`GraphIndex.definitions_of(demote_nonproduct=True)`.

⚠ **이름 토큰 근사다.** 지역이 안쪽 블록에서만 선언돼도 함수 전체에서 뺀다(`locals` 표가 함수
단위다). 못 거르는 것은 `code_only`·`definition_names` 위의 `ponytail:`에 적었다.

## 어떤 순서로 싣는가

슬라이스 **줄**에 나온 이름의 정의가 먼저다(`slice_names_first`). 정의는 (파일, 시작 줄) 순으로
오고 총 상한이 차면 뒤가 먼저 빠지므로, 슬라이스와 무관한 이름의 정의가 슬라이스가 쓰는 정의의
자리를 먹지 않게 한다. 나머지는 원래 순서다(안정 정렬).
"""

import re
from collections.abc import Callable, Iterable

from src.schemas.common import Location

# 실린 원문에서 이름을 뽑는 데 쓴다 — 파싱이 아니라 토큰 근사다. 키워드는 정의 조회에서 뺀다.
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_C_KEYWORDS = frozenset(
    "auto break case char const continue default do double else enum extern float for goto if "
    "inline int long register restrict return short signed sizeof static struct switch typedef "
    "union unsigned void volatile while bool true false NULL".split()
)
# 코드가 아닌 자리 — 줄 주석 · 블록 주석 · 문자열 · 문자 리터럴(이스케이프 포함). 왼쪽부터 한 번에
# 훑는다: 먼저 시작한 쪽이 이기므로 문자열 속 `//`·`/*`와 주석 속 따옴표가 서로를 안 건드린다.
# ponytail: C의 어휘 규칙만 본다. C++ 원시 문자열 `R"(...)"`은 안 본다(안의 따옴표가 짝을 깨면
# 그 줄은 코드로 남는다) · 인코딩 접두 `L"…"`의 `L`과 `#include <a/b.h>`의 경로는 이름으로 남는다 ·
# 읽은 범위가 블록 주석 한가운데서 시작하면(`slice_line_names` — 슬라이스 첫 줄 앞에서 열린
# 주석) 그 속은 코드로 읽힌다. 순서를 정하는 어림이라 둔다 — 올리는 길은 읽는 범위를 슬라이스 첫
# 줄이 아니라 그 줄을 품는 함수의 시작에 맞추는 것.
# 수 리터럴(`num` — 전처리 수 토큰 근사)도 한 덩어리로 먼저 먹고 **그대로 둔다**: C23·C++14 자릿수
# 구분자 `1'000`·`0xFF'FF`의 `'`가 문자 리터럴을 열면 뒤 코드를 다음 `'`까지 삼킨다. 이름 끝 숫자
# (`u8'x'`의 `8`)는 수의 시작이 아니다 — 앞 글자가 이름 글자면 수로 안 본다.
_NOT_CODE = re.compile(
    r"(?P<num>(?<![A-Za-z0-9_])[0-9](?:'(?=[A-Za-z0-9_])|[A-Za-z0-9_.])*)"  # 수 — 지우지 않는다
    r"|//(?:\\.|[^\n\\])*"  # 줄 주석 — 줄 끝 `\`는 다음 줄까지 잇는다
    r"|/\*.*?(?:\*/|\Z)"  # 블록 주석 — 안 닫혔으면 끝까지
    r'|"(?:\\.|[^"\\\n])*"'  # 문자열 리터럴
    r"|'(?:\\.|[^'\\\n])*'",  # 문자 리터럴
    re.DOTALL,
)
# 필드 접근 `->x`·`.x`의 `x`(group 1). 연산자와 이름 사이 공백(`p -> len`)은 받고 줄바꿈은
# 안 받는다. 점은 **하나짜리만** 본다 — GNU `case LOW ... HIGH:`의 `...`는 필드 접근이 아니다.
_FIELD_ACCESS = re.compile(r"(?:->|(?<!\.)\.(?!\.))[ \t]*([A-Za-z_][A-Za-z0-9_]*)")
# 지역과 철자가 같아도 **지역을 가리키지 않는** 자리(모듈 docstring): 태그 · 호출 `x(` · 함수 안
# `extern` 선언문.
_TAG = re.compile(r"\b(?:struct|union|enum)\s+([A-Za-z_][A-Za-z0-9_]*)")
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(")
_EXTERN = re.compile(r"\bextern\b([^;{}]*);")
# `#define NAME` / `#define NAME(params)` 머리(group 1 이름 · group 2 파라미터 목록). 별칭 판정이
# 쓴다(`macro_alias_target`).
_DEFINE_HEAD = re.compile(r"\A\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)(\([^)]*\))?")

LocalsOf = Callable[[str, str], frozenset[str] | None]
ReadSource = Callable[[Location], str]


def code_only(text: str) -> str:
    """주석·문자열·문자 리터럴을 공백 하나로 바꾼다. 안에 든 줄바꿈은 남긴다 — 줄 번호로 고르는 쪽
    (`slice_line_names`)이 안 어긋나고, `a/*c*/b`가 `ab`로 붙지 않는다. 수 리터럴은 그대로 둔다
    (`_NOT_CODE`의 `num` — 자릿수 구분자 `'`를 문자 리터럴로 안 읽게 먼저 먹을 뿐이다)."""
    return _NOT_CODE.sub(
        lambda m: m.group() if m.group("num") else " " + "\n" * m.group().count("\n"), text
    )


def _field_starts(code: str) -> set[int]:
    return {match.start(1) for match in _FIELD_ACCESS.finditer(code)}


def _names(code: str) -> set[str]:
    """`code_only`를 거친 원문에서 필드 접근이 **아닌 자리에** 나온 식별자 — C 키워드 제외."""
    fields = _field_starts(code)
    return {m.group() for m in _IDENT.finditer(code) if m.start() not in fields} - _C_KEYWORDS


def bare_names(text: str) -> set[str]:
    """`text`의 **코드 자리**에서 필드 접근(`->x`·`.x`)이 **아닌 곳에** 한 번이라도 나온 식별자 —
    C 키워드 제외. 주석·문자열·문자 리터럴 속 낱말은 안 센다.

    필드로도 쓰인 이름(`len = p->len;`의 `len`)은 남는다 — 앞 `len`은 변수(또는 매크로) 자리다.
    """
    return _names(code_only(text))


def macro_alias_target(text: str) -> str | None:
    """매크로 정의 원문이 **다른 이름 하나로 바꿔 넘기는** 꼴이면 그 이름 — 아니면 `None`(C4).

    대치 목록이 식별자로 시작하면(`#define ND_TCHECK(p) ND_TCHECK2(p, sizeof(*(p)))` ->
    `ND_TCHECK2`, `#define gf_strdup gf_mem_strdup` -> `gf_mem_strdup`) 그 첫 식별자를 별칭 대상으로
    본다. `do {`·`(`·숫자·문자열로 시작하는 매크로는 식만 담은 것이라 `None`이다. 자기 이름 ·
    파라미터 · C 키워드면 `None`이다. 주석·문자열은 먼저 지우고 줄 잇기(`\\` 줄바꿈)는
    공백으로 본다.
    """
    head = _DEFINE_HEAD.match(text)
    if head is None:
        return None
    replacement = code_only(text[head.end() :]).replace("\\\n", " ").strip()
    first = _IDENT.match(replacement)
    if first is None:
        return None
    params = set(_IDENT.findall(head.group(2) or ""))
    name = first.group()
    if name == head.group(1) or name in params or name in _C_KEYWORDS:
        return None
    return name


def _outer_names(code: str) -> set[str]:
    """지역과 철자가 같아도 **지역을 가리키지 않는** 자리의 이름 — 태그(`struct X`) · 호출(`x(`,
    필드 호출 `p->x(`은 아니다) · 함수 안 `extern` 선언문의 이름(모듈 docstring). `code_only`를 거친
    원문을 받는다."""
    fields = _field_starts(code)
    called = {m.group(1) for m in _CALL.finditer(code) if m.start(1) not in fields}
    declared = {name for statement in _EXTERN.findall(code) for name in _IDENT.findall(statement)}
    return (set(_TAG.findall(code)) | called | declared) - _C_KEYWORDS


# ponytail: 못 거르는 둘. ① 지역과 **같은 이름의 typedef**(`node *node`의 앞 `node`는 타입이다) —
# 문장 머리·파라미터 목록의 타입 자리를 구문 없이 못 가른다(`n * n`과 글자로 같다). 올리는 길은
# 인덱서가 선언의 타입 이름도 `locals`에 싣는 것이고, 실측에서 그 모양이 얼마나 되는지 본 뒤에 한다.
# ② **함수 포인터 지역을 이름으로 부르는 호출**(`cb(x)`) — 호출 규칙이 그 이름을 되살려 정의 조회가
# 옛 동작 그대로다(늘지는 않는다). 같은 이름의 함수형 매크로가 있을 때만 되살리려면 정의 줄을 읽어
# `#define NAME(`인지 봐야 하는데 `macros` 표는 함수형 여부를 안 든다.
def definition_names(carried: Iterable[tuple[Location, str]], locals_of: LocalsOf) -> set[str]:
    """실린 함수들(위치, 원문)이 쓰는 이름 중 **정의를 찾을 만한 것** — 정의 조회에 넘길 집합.

    함수마다 코드 자리의 이름(`code_only` → `_names`)에서 그 함수의 지역·파라미터를 빼되(`locals_of`
    — `None`이면 뺄 것을 모른다) 지역을 가리키지 않는 자리의 이름(`_outer_names`)은 되돌려 놓는다.
    `symbol`이 없는 창 anchor는 어느 함수의 지역인지 몰라 지역은 못 뺀다.
    """
    names: set[str] = set()
    for location, text in carried:
        code = code_only(text)
        own = locals_of(location.file, location.symbol) if location.symbol else None
        names |= _names(code) - ((own or frozenset()) - _outer_names(code))
    return names


def slice_line_names(read_source: ReadSource, lines: Iterable[tuple[str, int]]) -> set[str]:
    """슬라이스가 지나는 줄 `(file, line)`에 나온 이름 — `bare_names`와 같은 규칙.

    파일마다 슬라이스 줄의 처음~끝을 **한 번에** 읽는다(`read_source` 하나). 주석·문자열은 읽은
    범위 전체에서 먼저 지운 뒤(줄바꿈이 남아 줄이 안 어긋난다) 슬라이스 줄만 고른다. 1보다 작은 줄
    번호와 읽은 범위를 벗어난 줄은 건너뛴다 — 순서를 정하는 어림이라 죽을 이유가 없다.
    """
    names: set[str] = set()
    for code in _slice_line_code(read_source, lines):
        names |= _names(code)
    return names


def slice_line_fields(read_source: ReadSource, lines: Iterable[tuple[str, int]]) -> set[str]:
    """슬라이스가 지나는 줄에 **필드로** 나온 이름(`p->len`·`s.buf`의 `len`·`buf`) — 집합 판이다.
    읽는 규칙은 `slice_line_names`와 같다. 쓰기 지점 창(C5(a))은 순서가 필요해 `slice_line_refs`를
    쓴다(PR #468 리뷰 MEDIUM-3)."""
    return {
        match.group(1)
        for code in _slice_line_code(read_source, lines)
        for match in _FIELD_ACCESS.finditer(code)
    }


def slice_line_refs(
    read_source: ReadSource, lines: Iterable[tuple[str, int]]
) -> list[tuple[str, bool]]:
    """슬라이스 줄에 나온 이름과 필드를 **처음 나온 순서로** — `(이름, 필드인가)`. 쓰기 지점 창의
    우선순위다(C5(a)): 알파벳 순이면 앞쪽 이름이 창을 먼저 먹고 길이 필드가 굶는다(PR #468 리뷰
    MEDIUM-3). 이름 쪽은 `slice_line_names`, 필드 쪽은 `slice_line_fields`와 같은 집합이다(같은
    읽기 규칙 · 파일은 처음 나온 순, 파일 안은 줄 순). 같은 낱말이 이름으로도 필드로도 나오면 둘 다
    든다."""
    found: dict[tuple[str, bool], None] = {}
    for code in _slice_line_code(read_source, lines):
        fields = _field_starts(code)
        for match in _IDENT.finditer(code):
            is_field = match.start() in fields
            if is_field or match.group() not in _C_KEYWORDS:
                found.setdefault((match.group(), is_field), None)
    return list(found)


def _slice_line_code(read_source: ReadSource, lines: Iterable[tuple[str, int]]) -> list[str]:
    """파일마다 슬라이스 줄만 골라 `code_only`를 거친 원문 — `slice_line_names`의 읽기 규칙."""
    by_file: dict[str, set[int]] = {}
    for file, line in lines:
        if line >= 1:
            by_file.setdefault(file, set()).add(line)
    picked_per_file: list[str] = []
    for file, wanted in by_file.items():
        first = min(wanted)
        text = read_source(Location(file=file, line_start=first, line_end=max(wanted)))
        rows = code_only(text).split("\n")
        picked = [rows[n - first] for n in sorted(wanted) if n - first < len(rows)]
        picked_per_file.append("\n".join(picked))
    return picked_per_file


def slice_names_first(
    definitions: list[Location], read_source: ReadSource, lines: Iterable[tuple[str, int]]
) -> list[Location]:
    """슬라이스 줄에 나온 이름의 정의를 앞으로 — 나머지는 원래 순서다(안정 정렬). 정의가 둘
    미만이면 순서가 없어 파일을 읽지 않는다."""
    if len(definitions) < 2:
        return definitions
    on_slice = slice_line_names(read_source, lines)
    return sorted(definitions, key=lambda definition: definition.symbol not in on_slice)
