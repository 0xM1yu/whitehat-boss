"""Auditor `SOURCE`의 정의 조회(#349 (f) — `auditor_definitions`)가 **무엇의 정의를 찾는가**를
거르고, 찾은 정의를 **어떤 순서로** 싣는지를 정한다. `runner._auditor_source`가 부른다.

## 왜 거르는가

예전에는 실린 함수 본문의 식별자 토큰을 **전부**(C 키워드만 빼고) `definitions_of`에 던졌다. 지역
변수·파라미터·구조체 필드 이름이 다른 파일의 매크로·타입·파일 변수와 철자가 같으면, 그 이름의
정의가 저장소에 **하나뿐**이라는 이유로 그 정의가 붙었다 — `za`가 다른 파일의 전역 `za`를,
`->archive`가 다른 파일의 `struct archive`를 끌어오는 식이다. 글자 상한은 한정돼 있어서 그 정의가
진짜 정의의 자리를 먹는다.

## 무엇을 거르는가 — 둘이고, 둘째에 예외가 하나 있다

1. **필드 접근 자리**(`p->len`·`q.size`)에만 나온 이름 — 구조체 필드지 같은 철자의 매크로·전역이
   아니다.
2. 실린 함수 **자신의** 지역·파라미터(`GraphIndex.locals_of`) — 함수마다 따로 뺀다. 합집합에서
   빼면 한 함수의 지역 때문에 다른 함수가 전역으로 쓰는 같은 이름이 사라진다.
   ⚠ **예외: `struct X`·`union X`·`enum X`의 `X`는 남긴다.** 태그는 변수와 이름공간이 달라서
   `struct archive *archive`의 태그 `archive`는 지역 변수 `archive`가 있어도 구조체 정의를
   가리킨다 — 그 정의가 필드 배치를 알려 주는 바로 그 정보다.

같은 철자가 여러 파일에 있을 때 비제품 경로를 한 단계 낮추는 것은 정의 조회 쪽의 일이다 —
`GraphIndex.definitions_of(demote_nonproduct=True)`.

⚠ **이름 토큰 근사다.** 지역이 안쪽 블록에서만 선언돼도 함수 전체에서 뺀다(`locals` 표가 함수
단위다). 주석·문자열 속 낱말도 토큰이 된다(예전과 같다).

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
# 필드 접근 `->x`·`.x`의 `x`(group 1). 연산자와 이름 사이 공백(`p -> len`)은 받고 줄바꿈은
# 안 받는다.
_FIELD_ACCESS = re.compile(r"(?:->|\.)[ \t]*([A-Za-z_][A-Za-z0-9_]*)")
# 태그 자리의 이름 — 변수와 이름공간이 다르다(모듈 docstring).
_TAG = re.compile(r"\b(?:struct|union|enum)\s+([A-Za-z_][A-Za-z0-9_]*)")

LocalsOf = Callable[[str, str], frozenset[str] | None]
ReadSource = Callable[[Location], str]


def bare_names(text: str) -> set[str]:
    """`text`에서 필드 접근(`->x`·`.x`)이 **아닌 자리에** 한 번이라도 나온 식별자 — C 키워드 제외.

    필드로도 쓰인 이름(`len = p->len;`의 `len`)은 남는다 — 앞 `len`은 변수(또는 매크로) 자리다.
    """
    fields = {match.start(1) for match in _FIELD_ACCESS.finditer(text)}
    return {m.group() for m in _IDENT.finditer(text) if m.start() not in fields} - _C_KEYWORDS


def definition_names(carried: Iterable[tuple[Location, str]], locals_of: LocalsOf) -> set[str]:
    """실린 함수들(위치, 원문)이 쓰는 이름 중 **정의를 찾을 만한 것** — 정의 조회에 넘길 집합.

    함수마다 `bare_names`에서 그 함수의 지역·파라미터를 빼고(`locals_of` — `None`이면 뺄 것을
    모른다) 태그 이름(`struct X`의 `X`)은 되돌려 놓는다. `symbol`이 없는 창 anchor는 어느 함수의
    지역인지 몰라 필드 접근만 거른다.
    """
    names: set[str] = set()
    for location, text in carried:
        own = locals_of(location.file, location.symbol) if location.symbol else None
        names |= (bare_names(text) - (own or frozenset())) | set(_TAG.findall(text))
    return names


def slice_line_names(read_source: ReadSource, lines: Iterable[tuple[str, int]]) -> set[str]:
    """슬라이스가 지나는 줄 `(file, line)`에 나온 이름 — `bare_names`와 같은 규칙.

    파일마다 슬라이스 줄의 처음~끝을 **한 번에** 읽는다(`read_source` 하나). 1보다 작은 줄 번호와
    읽은 범위를 벗어난 줄은 건너뛴다 — 순서를 정하는 어림이라 죽을 이유가 없다.
    """
    by_file: dict[str, set[int]] = {}
    for file, line in lines:
        if line >= 1:
            by_file.setdefault(file, set()).add(line)
    names: set[str] = set()
    for file, wanted in by_file.items():
        first = min(wanted)
        rows = read_source(Location(file=file, line_start=first, line_end=max(wanted))).split("\n")
        picked = [rows[n - first] for n in sorted(wanted) if n - first < len(rows)]
        names |= bare_names("\n".join(picked))
    return names


def slice_names_first(
    definitions: list[Location], read_source: ReadSource, lines: Iterable[tuple[str, int]]
) -> list[Location]:
    """슬라이스 줄에 나온 이름의 정의를 앞으로 — 나머지는 원래 순서다(안정 정렬). 정의가 둘
    미만이면 순서가 없어 파일을 읽지 않는다."""
    if len(definitions) < 2:
        return definitions
    on_slice = slice_line_names(read_source, lines)
    return sorted(definitions, key=lambda definition: definition.symbol not in on_slice)
