"""Auditor `SOURCE`의 GUARDS 블록 — sink 앞에 있는 가드를 **구문만으로** 모은다(`auditor_guards`).

Joern의 데이터흐름은 검사를 무시한다 — `if (len > MAX) return;`는 sink의 조상이 아니라서
슬라이스에도, 슬라이스가 걸친 함수를 이어 붙인 SOURCE에도 「범위가 검사됐는가」가 구조로 안
실린다. 검수자가 자주 요청한 정보(범위·검증)인데 재료는 앵커 함수의 원문에 이미 있다. 그래서
sink 호출의 인자 이름을 언급하는 조건을 줄번호와 함께 모아 SOURCE에 붙인다 — `if`·`while`·
`for`·삼항·assert 계열, 그리고 `if`가 곧바로 탈출(`return`·`goto`·`break`·`continue`·
`abort`·`exit`)하면 그 탈출문.

## 이것이 주장하는 것과 안 하는 것

**지배(dominate)를 주장하지 않는다.** 구문 훑기라서 그 조건이 sink 경로 위에 있는지, 우회되는지,
다른 가지의 것인지 모른다 — 머리줄이 `GUARDS (syntactic — may not dominate)`인 이유다. 가드가
없을 때의 `GUARDS: none found before sink …`는 LLM의 단언이 아니라 **이 훑기의 결과**이고,
아래 한계 안에서만 참이다.

- 앵커 함수 **안**만 본다 — 호출자의 검증·피호출 함수 안의 검증은 안 본다. 품는 함수를 모르는
  창 앵커면 「없음」의 범위를 `window`라고 적는다(함수 머리가 창 밖일 수 있다).
- 원문이 슬라이서의 줄과 어긋나면 훑지 않는다(`_aligned`) — `read_source`는 `splitlines()`로
  줄을 세서(#259) 함수 앞의 폼피드 같은 글자만큼 원문이 위로 밀리고, 그러면 sink 줄이 다른
  줄이라 「없음」이 거짓이 된다. 함수 이름이 첫 두 줄에 없거나 마지막 줄이 `}`로 안 끝나면
  어긋난 것이다. 함수 **안**의 폼피드도 마지막 줄이 빠져 같이 걸리지만 그 앞 줄이 우연히 `}`면
  못 거르고, 창 앵커는 이 검사를 못 한다.
- 이름으로만 잇는다 — sink 인자에 나온 식별자(필드 이름 포함 · 함수 이름·키워드·`_t` 타입 제외)
  중 하나를 조건이 언급하면 후보다. `n = h->len;`을 거쳐 `n`을 쓰는데 가드는 `h->len`을 보는
  식의 **별칭·복사는 안 따라간다** — 한쪽이 별칭 이름이면 안 이어지고, 거꾸로 `len` 같은 흔한
  필드 이름은 다른 객체의 조건과도 이어진다. 호출의 수신 객체(`v.push_back(x)`의 `v`)는 인자가
  아니라 안 센다.
- 전처리를 안 한다 — `#if`의 두 가지가 다 보이고(조건식은 안 본다), 매크로 안의 가드는 이름이
  assert 계열(`assert`·`BUG_ON` 등)일 때만 센다. `MIN(n, cap)` 같은 클램프 매크로는 안 센다.
- sink를 품은 조건식은 가드로 안 센다 — `if (n < cap && copy(…))`에서 `n < cap`은 sink 앞의
  단락 평가 피연산자지만 같은 조건식 안이라 훑지 않는다.
- 탈출은 `if` 몸통의 **문장 머리**에 있을 때만 표시한다 — `else` 가지·더 안쪽 블록·안쪽 `if`/
  반복문의 몸통(`if (s) return`)·멤버 호출(`x.abort()`)의 탈출은 조건부이거나 이 가드의 것이
  아니라 표시하지 않는다(조건은 싣는다).
- 연산자 sink(`a[i] = x` — 호출이 없다)는 줄 머리의 `if (…)`만 sink 앞으로 센다 —
  `else if (…) a[i] = x;`·`{ if (…) … }` 한 줄은 놓친다.
- C 문법의 훑기다 — 문자열·주석·전처리 줄을 지우고 괄호를 맞춘다. 원시 문자열(`R"(…)"`)·
  `if constexpr`·`switch`/`case`는 모른다.

## 어디에 붙나

`runner._auditor_source`가 entry 함수 바로 뒤에 붙인다. **Case B만**이다 — 슬라이스의 `entry`가
sink 호출 줄이고 `entry.symbol`이 그 호출 이름이다(`slice_refs`). Case A(진입점에서 앞으로
자른 것)의 entry는 함수 자신이라 sink를 하네스가 모른다.
"""

import bisect
import re
from dataclasses import dataclass
from typing import Literal, NamedTuple

from src.schemas.candidate import SliceRef
from src.schemas.common import Location

# SOURCE에 붙는 블록의 머리줄 — 정확히 이 문자열이다(테스트가 핀한다). 「가드」라고만 쓰지 않고
# 「구문상 · 지배 안 할 수 있음」까지 머리에 박는다 — 모델이 이 목록을 증명으로 읽지 않게.
GUARDS_HEADER = "GUARDS (syntactic — may not dominate)"
_NONE_FOUND = "GUARDS: none found before sink (syntactic scan of the anchor {scope})"

# 싣는 행 수 · trace에 목록으로 남길 뺀 행 수 · 행 하나·탈출문 하나의 글자 수. **정한 값이지 잰
# 값이 아니다** — 블록이 SOURCE의 한 줌에 머물게 하는 폭이다. 행이 넘치면 sink에 **가까운** 행을
# 남긴다(가장 가까운 검사가 그 sink의 것일 가능성이 크다 — 이것도 가정이다).
_MAX_ROWS = 12
_MAX_DROPPED_LISTED = 50
_ROW_CHARS = 160
_EXIT_CHARS = 40

# 주석·전처리 줄·(literals면) 문자열·문자 리터럴을 같은 길이의 공백으로 지운다 — 줄바꿈은 남겨
# offset과 줄 번호가 원문과 같다. 전처리 줄이 먼저다(`#error don't`의 아포스트로피가 문자
# 리터럴을 열지 않게).
_DIRECTIVE = re.compile(r"^[ \t]*#(?:[^\n]*\\\n)*[^\n]*", re.MULTILINE)
_LITERAL_OR_COMMENT = re.compile(
    r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.DOTALL
)
_NON_NEWLINE = re.compile(r"[^\n]")

_IDENT = re.compile(r"\b[A-Za-z_]\w*")
_CALL_AHEAD = re.compile(r"\s*\(")
_COND_KEYWORD = re.compile(r"\b(if|while|for)\s*\(")
# assert 계열 — 이름에 `assert`가 든 것(대소문자 무관: `g_assert`·`DEBUGASSERT`·`ck_assert_int_eq`)·
# `CHECK`가 든 것(`CHECK_LT`·`ND_TCHECK_LEN` — tcpdump의 경계 검사)과 `BUG_ON`·`WARN_ON`·
# `ENSURE`·`REQUIRE`·`VERIFY`로 시작하는 것·`g_return_*`.
_ASSERT_LIKE = re.compile(
    r"\b(\w*(?i:assert)\w*|\w*CHECK\w*|BUG_ON\w*|WARN_ON\w*|(?:ENSURE|REQUIRE|VERIFY)\w*"
    r"|g_return_\w+)\s*\("
)
_EXIT = re.compile(r"\b(?:return|goto|break|continue)\b|\b(?:abort|exit)\s*\(")
_QUESTION = re.compile(r"\?")
_LEADING = re.compile(r"\s*(?:return\b\s*)?")

# 인자·조건에서 「이름」으로 안 세는 것 — C·C++ 키워드와 상수·캐스트 낱말. 함수 이름(뒤에 `(`)과
# `_t`로 끝나는 타입도 따로 뺀다(`_names`).
_NOT_DATA = frozenset(
    "auto break case char const continue default do double else enum extern float for goto if "
    "inline int long register restrict return short signed sizeof static struct switch typedef "
    "union unsigned void volatile while bool true false NULL nullptr this new delete alignof "
    "offsetof typeof static_cast reinterpret_cast const_cast dynamic_cast".split()
)


@dataclass(frozen=True)
class GuardScan:
    """훑은 결과. `rows`는 SOURCE에 싣는 행, `dropped`는 행 상한에 걸려 뺀 행(둘 다
    `파일:줄 | 조건`, 소스 순서)이다. `scope`는 「없음」 문장이 가리키는 범위(`function`·
    `window`)다."""

    rows: tuple[str, ...]
    dropped: tuple[str, ...]
    scope: Literal["function", "window"]

    @property
    def shown(self) -> int:
        return len(self.rows)

    @property
    def block(self) -> str:
        """SOURCE에 붙는 글자. 행이 없으면 「없음」 한 줄이다 — 훑은 결과이지 모델의 단언이
        아니다."""
        if not self.rows:
            return _NONE_FOUND.format(scope=self.scope)
        return "\n".join((GUARDS_HEADER, *self.rows))

    @property
    def dropped_listing(self) -> str:
        """trace에 남길 뺀 행 목록(줄바꿈으로 이음). `_MAX_DROPPED_LISTED`행까지고 넘치면 끝에
        개수를 적는다. 뺀 것이 없으면 빈 문자열이다."""
        listed = self.dropped[:_MAX_DROPPED_LISTED]
        more = len(self.dropped) - len(listed)
        return "\n".join((*listed, f"... +{more} more") if more else listed)


class _Found(NamedTuple):
    # 이 구성이 시작하는 offset — 키워드·삼항 조건의 첫 글자·assert 매크로 이름.
    at: int
    # 조건식의 [lo, hi) — sink가 이 안에 있으면 가드가 아니라 sink 자신이다.
    lo: int
    hi: int
    # 행의 본문(조건 + 어떤 구성인지).
    text: str
    # `if`면 조건을 닫는 `)` 다음 offset(몸통이 시작하는 곳), 아니면 -1.
    body: int
    # sink 이름과 맞춰 볼 글자의 [names_from, hi) — 보통 `lo`, 대입 삼항은 대입 왼쪽부터.
    names_from: int


def scan_guards(ref: SliceRef, anchor: Location, text: str) -> GuardScan | None:
    """슬라이스 타깃의 entry 함수 원문 `text`에서 sink 앞의 가드를 훑는다. 훑을 수 없으면 `None`."""
    line = ref.entry.line_start
    # ponytail: Case A(`given_end == "source"`)는 entry가 진입점 함수 자신이라 sink를 하네스가
    # 모른다 — 건너뛴다. 슬라이서가 슬라이스에 `source_sink`(source·sink로 본 위치 — 슬라이서 쪽
    # 별도 변경)를 싣고 로더가 그것을 `SliceRef`까지 나르면, 여기서 그 sink 위치를 `sink_line`으로
    # `scan_function`을 부르면 된다.
    if ref.given_end != "sink" or line is None:
        return None
    # 품는 함수를 아는 앵커는 원문이 그 함수와 맞는지 먼저 본다 — 어긋났으면 훑지 않는다.
    if anchor.symbol and not _aligned(text, anchor.symbol):
        return None
    return scan_function(
        text,
        file=anchor.file,
        first_line=anchor.line_start or 1,
        sink_line=line,
        sink_name=ref.entry.symbol,
        # `_auditor_target`은 품는 함수를 모르면 이름 없는 앞뒤 창을 준다
        scope="function" if anchor.symbol else "window",
    )


def _aligned(text: str, symbol: str) -> bool:
    """함수 원문이 슬라이서의 줄과 맞게 잘렸는가. `read_source`는 `splitlines()`로 줄을 세는데
    슬라이서·인덱서는 줄바꿈만 세서(#259), 함수 앞에 폼피드 같은 글자가 k개 있으면 원문이 k줄 위로
    밀린다 — 함수 이름이 첫 줄에서 밀리고 마지막 줄(`}`)이 빠진다. 그런 원문의 sink 줄은 다른
    줄이라 「없음」이 거짓이고 행 번호도 k만큼 틀린다. 이름이 첫 두 줄에 없거나 마지막 줄이 `}`로
    안 끝나면 어긋난 것이다(주석은 지우고 본다)."""
    masked = _blank(text, literals=True)
    head = masked.split("\n", 2)[:2]
    return any(symbol in line for line in head) and masked.rstrip().endswith("}")


def scan_function(
    text: str,
    *,
    file: str,
    first_line: int,
    sink_line: int,
    sink_name: str | None,
    scope: Literal["function", "window"] = "function",
) -> GuardScan | None:
    """`text`(파일의 `first_line`줄부터 이어진 원문)에서 `sink_line`의 sink **앞** 가드를 훑는다.

    「앞」은 줄이 아니라 **위치**다 — 같은 줄의 `if (n <= 16) memcpy(…)`는 호출 앞이라 센다.
    sink 줄이 원문 밖이거나 sink에서 이름을 하나도 못 뽑으면 `None`이다(훑지 못한 것을 「없음」이라
    쓰지 않는다).
    """
    starts = [0, *(m.end() for m in re.finditer("\n", text))]
    index = sink_line - first_line
    if not 0 <= index < len(starts):
        return None
    masked, code = _blank(text, literals=True), _blank(text, literals=False)
    stop = starts[index + 1] - 1 if index + 1 < len(starts) else len(text)
    names, cutoff = _sink(masked, starts[index], stop, sink_name)
    if not names:
        return None
    rows: list[str] = []
    for found in _found(code, masked):
        if found.at >= cutoff or found.lo <= cutoff < found.hi:
            continue
        if not _names(masked[found.names_from : found.hi]) & names:
            continue
        line = first_line + bisect.bisect_right(starts, found.at) - 1
        tail = ""
        if found.body >= 0 and (exit_ := _exit_after(code, masked, found.body)):
            tail = f" [exit: {exit_}]"
        rows.append(f"{file}:{line} | {_clip(found.text, _ROW_CHARS)}{tail}")
    return GuardScan(rows=tuple(rows[-_MAX_ROWS:]), dropped=tuple(rows[:-_MAX_ROWS]), scope=scope)


def _blank(text: str, *, literals: bool) -> str:
    """주석·전처리 줄(`literals`면 문자열·문자 리터럴도)을 같은 길이의 공백으로 바꾼다. 행에
    **보일** 글자(`literals=False`)는 문자열을 남기고 주석만 지운다 — 줄 끝 주석이 접힌 조건의
    뒤를 삼키지 않게."""

    def erase(match: re.Match[str]) -> str:
        return _NON_NEWLINE.sub(" ", match.group())

    def erase_literal_or_comment(match: re.Match[str]) -> str:
        return erase(match) if literals or match.group().startswith("/") else match.group()

    return _LITERAL_OR_COMMENT.sub(erase_literal_or_comment, _DIRECTIVE.sub(erase, text))


def _names(fragment: str) -> set[str]:
    """`fragment`에서 데이터 이름으로 셀 식별자 — 키워드·함수 이름(뒤에 `(`)·`_t` 타입은 뺀다.
    필드 이름(`p->len`의 `len`)은 센다."""
    return {
        word
        for match in _IDENT.finditer(fragment)
        if (word := match.group()) not in _NOT_DATA
        and not word.endswith("_t")
        and not _CALL_AHEAD.match(fragment, match.end())
    }


def _squash(fragment: str) -> str:
    return " ".join(fragment.split())


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _close_of(masked: str, start: int) -> int:
    """`masked[start]`의 `(`·`{`에 짝이 되는 닫는 괄호의 인덱스. 짝이 없으면 -1."""
    opener = masked[start]
    closer = ")" if opener == "(" else "}"
    depth = 0
    for i in range(start, len(masked)):
        if masked[i] == opener:
            depth += 1
        elif masked[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _sink(masked: str, line_start: int, line_end: int, name: str | None) -> tuple[set[str], int]:
    """(sink가 쓰는 이름들, 「이 앞」의 경계 offset).

    sink가 이 줄의 호출이면 그 호출의 **인자**(여러 줄에 걸쳐도 닫는 괄호까지)의 이름이고 경계는
    호출 이름의 시작이다. 연산자 sink(`a[i] = x` — Joern 이름이 `<operator>…`)거나 줄에서 호출을
    못 찾으면 그 줄의 이름 전부이고 경계는 줄의 첫 글자 바로 뒤다(줄 머리의 `if (…)`만 앞으로 센다).
    """
    if name and name.isidentifier():
        hits = list(re.compile(rf"\b{name}\s*\(").finditer(masked, line_start, line_end))
        if hits:
            names: set[str] = set()
            for hit in hits:
                close = _close_of(masked, hit.end() - 1)
                names |= _names(masked[hit.end() : close if close >= 0 else len(masked)])
            return names, hits[0].start()
    first = next((i for i in range(line_start, line_end) if not masked[i].isspace()), line_start)
    return _names(masked[line_start:line_end]), first + 1


def _found(code: str, masked: str) -> list[_Found]:
    """원문에서 조건을 품는 구성(if·while·for · assert 계열 · 삼항)을 소스 순서로 모은다."""
    found: list[_Found] = []
    for match in _COND_KEYWORD.finditer(masked):
        opened = match.end() - 1
        close = _close_of(masked, opened)
        if close >= 0:
            text = f"{match.group(1)} ({_squash(code[opened + 1 : close])})"
            body = close + 1 if match.group(1) == "if" else -1
            found.append(_Found(match.start(), opened + 1, close, text, body, opened + 1))
    for match in _ASSERT_LIKE.finditer(masked):
        opened = match.end() - 1
        close = _close_of(masked, opened)
        if close >= 0:
            text = f"{match.group(1)}({_squash(code[opened + 1 : close])})"
            found.append(_Found(match.start(), opened + 1, close, text, -1, opened + 1))
    found.extend(_ternaries(code, masked))
    return sorted(found)


def _ternaries(code: str, masked: str) -> list[_Found]:
    """`cond ? a : b`의 `cond`. `?`에서 왼쪽으로 문장·블록·인자·대입·라벨 경계까지 거슬러 올라간다.

    경계가 대입(`n = cond ? a : b`)이면 그 대입의 왼쪽(`n`)도 sink 이름과 맞춰 본다 — `n`을 조이는
    분기는 조건이 다른 이름만 말해도 `n`의 가드다. 인자·`return` 자리의 삼항은 이웃 이름을
    안 끌어온다.
    """
    found: list[_Found] = []
    for question in _QUESTION.finditer(masked):
        i, depth = question.start() - 1, 0
        while i >= 0:
            char = masked[i]
            if char in ")]":
                depth += 1
            elif char in "([":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and _ends_condition(masked, i):
                break
            i -= 1
        lo = _LEADING.match(masked, i + 1).end()  # 항상 맞는다 — 앞 공백·`return`을 건너뛴다
        if lo >= question.start():
            continue
        names_from = lo
        if i >= 0 and masked[i] == "=":
            # 대입 왼쪽의 문장 시작 — `;` 뒤, 그 사이의 `{`·`}` 뒤. 블록 경계는 앞 문장 안에서만
            # 찾는다(프리픽스 전체를 거슬러 찾으면 삼항이 많은 긴 함수에서 제곱이다).
            names_from = masked.rfind(";", 0, i) + 1
            names_from = max(
                names_from,
                masked.rfind("{", names_from, i) + 1,
                masked.rfind("}", names_from, i) + 1,
            )
        text = f"{_squash(code[lo : question.start()])} ? ... : ..."
        found.append(_Found(lo, lo, question.start(), text, -1, names_from))
    return found


def _ends_condition(masked: str, i: int) -> bool:
    """삼항 조건의 왼쪽 경계인가 — `;{},?`·`:`(`::`은 아니다)·대입 `=`(`==`·`!=`·`<=`·`>=`은
    아니다)."""
    char = masked[i]
    if char in ";{},?":
        return True
    before, after = masked[i - 1 : i], masked[i + 1 : i + 2]
    if char == ":":
        return before != ":" and after != ":"
    if char == "=":
        return before not in ("=", "!", "<", ">") and after != "="
    return False


def _exit_after(code: str, masked: str, start: int) -> str | None:
    """`if (…)`(몸통이 `start`부터) 바로 뒤 몸통 — 블록 하나나 문장 하나 — 에 곧바로 든 조기
    탈출문. 몸통의 **문장 머리**(`{`·`}`·`;`·`:` 다음)에 있는 것만 센다 — 더 안쪽 블록의 탈출도,
    안쪽 `if`·반복문·`else`의 몸통(`if (s) return`)도, 멤버 호출(`x.abort()`)도 이 가드의 탈출이
    아니다. 없으면 `None`."""
    i = start
    while i < len(masked) and masked[i].isspace():
        i += 1
    if i < len(masked) and masked[i] == "{":
        close = _close_of(masked, i)
        lo, hi = i + 1, close if close >= 0 else len(masked)
    else:
        end = masked.find(";", i)
        lo, hi = i, end if end >= 0 else len(masked)
    # 몸통 안 중괄호 깊이 — 앞 탈출문에서 이어 세므로 몸통이 커도 한 번 훑는다.
    depth, at = 0, lo
    for match in _EXIT.finditer(masked, lo, hi):
        depth += masked.count("{", at, match.start()) - masked.count("}", at, match.start())
        at = match.start()
        before = at - 1
        while before >= lo and masked[before].isspace():
            before -= 1
        if depth == 0 and (before < lo or masked[before] in "{};:"):
            end = masked.find(";", at, hi)
            return _clip(_squash(code[at : end if end >= 0 else hi]), _EXIT_CHARS)
    return None
