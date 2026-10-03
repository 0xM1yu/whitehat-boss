"""Auditor `SOURCE`의 「닫힘」 블록 — 앵커 함수가 부르는 매크로·얇은 전달 함수를 **실제 구현까지**
따라간다(옵션 `auditor_closure`, 기본 꺼짐 — 슬라이서 개선 계획 WP-S2).

**왜 있나.** 83샘플 검수([로컬실측] run ID 없음 — 계획 §1 #8)에서 검수자가 가장 자주 요청한 정보가
피호출 함수의 본문·계약이다(미해결 사유 209건 중 66행 — 키워드 규칙이라 겹친다). 앵커가
`xmalloc(n)`·`png_free(p)` 같은 매크로나 한 줄 전달 함수를 부르면 SOURCE에는 그 껍데기만 있다.
`auditor_callee_hops`도 껍데기 본문을 한 단계 붙일 뿐이라 경계·NULL 계약이 든 진짜 구현에 안 닿는다.

**값은 어디서 나오나** [로컬실측: E4 — 83샘플의 정답 sink 함수 82개, 저장소 밖
`slicer_e4/PREVALENCE.md`, run ID 없음. 이 코드를 돌린 것이 아니라 인덱스로 센 「보일 것이 있는가」
다]. 정의 본문이 다른 저장소 매크로를 부르는 매크로(사슬 ≥ 2)를 쓰는 함수가 25개(30.5%)이고, 접은
호출이 인덱스에 있는 본문에 닿아 새로 보태는 함수는 6개(7.3%)다. 둘 중 하나라도 해당하면 29개
(35.4%). **몫의 대부분이 매크로 사슬이다** — 전달 함수 접기는 값이 작은 쪽이지만 이미 짜여 테스트돼
있어 둔다.

**무엇을 하나.** 앵커 본문이 부르는 이름마다 사슬을 걷는다.

1. 이름이 **매크로**면 정의 원문으로 가른다(`classify_macro`).
   - rename `#define A B`
   - 단일 호출 전달 `#define A(x) B(x, 1)` — 본문이 호출 하나이고 인자가 파라미터·형변환·상수
     (숫자·문자열·`NULL`·`__LINE__` 같은 대문자 식별자)뿐
   - 불투명 — 그 밖 전부
2. 이름이 **함수**면 「전달자」인지 본다. 본문이 `[return] [(형변환)] g(인자…);` **한 문장**이고
   인자가 위와 같은 파라미터·상수인 짧은 함수(`WRAPPER_MAX_LINES`줄 이하)다.
3. rename·전달 단계는 비용 0으로 접고 다음 이름으로 넘어간다 — 깊이 `FOLD_DEPTH`까지, 방문
   집합으로 순환을 끊는다. **첫 실제 본문에서 멈춘다.** 전달자마다 다음 단계가 하나뿐이라 0-1 BFS는
   사슬 걷기로 줄어든다.
4. 불투명 매크로는 호출 대상이 없다 — 전개가 곧 구현이라 `#define` 원문을 그대로 싣는다. 그 본문이
   쓰는 매크로의 정의도 `MACRO_CHAIN_DEPTH`단까지 싣는다.

**접지 않는 것.** `if`·`?:`·`&&`가 든 본문(NULL 검사·오류 반환)과 `abort`·`exit`류 호출은 접지
않는다 — 그것이 Auditor가 필요한 계약이다. 접히지 않은 함수가 사슬의 끝(「실제 본문」)이 되어
통째로 실린다.

## 블록 모양 (프롬프트 범례가 인용하는 곳)

단위 하나가 블록 하나다. 첫 줄이 사슬이고 그 아래가 끝의 원문이다(배너는 다른 SOURCE 블록과 같다).

```
/* CLOSURE: xmalloc[macro] -> safe_alloc -> raw_alloc */
/* ===== main.c:3-9 ===== */
void *raw_alloc(size_t n) { ... }
```

- `a -> b -> c` — a가 인자를 b로, b가 c로 **넘길 뿐**이다(각 단계가 위 기준의 전달자). 마지막이
  첫 실제 본문이다. 자기 파라미터를 같은 순서로 그대로 넘기지 않으면(상수를 얹거나 순서를 바꾸거나
  뺀다) 받는 쪽 이름 뒤에 그 인자가 붙는다 — `NEW[macro] -> alloc_n((size_t)(n), 1)`.
- `x[macro]` — 매크로다. 끝이 매크로면 그 `#define` 원문(줄 이음 포함)과 본문이 쓰는 매크로의
  정의가 아래에 붙는다.
- `(body above)`·`(definition above)` — 그 원문은 이 블록보다 앞의 SOURCE에 이미 있다.
- `(no definition in the repo index)` — 사슬이 인덱스에 정의가 없는 이름(libc 등)으로 끝났다.
  본문은 없다.
- `(cycle)` — 전달이 제자리로 돌아와 끊었다.
- `(ambiguous: several definitions in the repo index)` — 그 이름의 정의가 여럿이라(`#if` 양쪽 가지
  · 같은 이름 여러 파일) 고르지 않고 거기서 멈췄다. 호출 이름 자체가 모호하면 단위를 안 만들고
  이벤트의 `closure_ambiguous`가 센다.

⚠ **구문만 본다.** 전처리도 타입 해석도 안 하고 정규식과 괄호 세기로 읽는다. 정의가 `#if` 안에
하나뿐이면 인덱스는 그것을 모른다 — 활성 여부는 이 블록이 말하지 않는다. 이름 바꾸기가 인덱스에 없는
이름으로 끝나면(`#define close _close` 같은 플랫폼·접두사 매크로 — E4가 본 대표 사례) 「정의
없음」으로 끝난다. 함수 포인터와 `p->f(…)` 호출은 따라가지 않는다. 「접힌 단계가 검사를 안 한다」는
위 엄격한 모양을 만족한다는 뜻이지 의미를 증명한 것이 아니다.

예산은 SOURCE의 총 상한을 그대로 쓴다 — 단위 하나(글 전체)가 안 들면 빠지고, 사유와 함께 이벤트가
목록으로 든다(`Closure.event`). 순수 모듈이다: 어댑터를 import 하지 않고 조회·읽기·배너 형식·셈법을
받는다.
"""

import json
import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass

from src.schemas.common import CallableDef, Location

# 전달 단계를 접는 깊이 상한 — 순환이 아니어도 끝없이 이어지는 것을 막는 안전판이다. **정한 값이지
# 잰 값이 아니다**(계획 §7 S2 — 조사 보고서의 안전판 6을 그대로 썼다).
FOLD_DEPTH = 6
# 불투명 매크로의 정의 사슬 깊이(루트 포함). **정한 값이지 잰 값이 아니다.**
MACRO_CHAIN_DEPTH = 4
# 이 줄 수를 넘는 함수는 전달자로 안 본다 — 읽지도 않는다(큰 함수를 읽는 비용을 안 낸다). 한 문장
# 전달자는 주석을 끼워도 몇 줄이다. **정한 값이지 잰 값이 아니다.**
WRAPPER_MAX_LINES = 10
# 사슬 줄에 보이는 넘기는 인자의 글자 상한 — 넘으면 잘라 `...`을 붙인다. 정한 값이지 잰 값이 아니다.
ARGS_CHARS = 60
# 단위 하나 안의 블록 하나의 글자 상한 — 러너의 피호출(`_AUDITOR_CALLEE_CHARS`)·정의
# (`_AUDITOR_DEFINITION_CHARS`) 상한과 **같은 값이다**. 러너를 import 할 수 없어(순환) 숫자를 따로
# 들고 있어서 `tests/test_source_closure.py`가 둘이 갈리면 잡는다. 정한 값이지 잰 값이 아니다.
BODY_CHARS = 4_000
MACRO_CHARS = 2_000
# 이벤트가 목록으로 싣는 빠진 단위의 수 — 넘으면 개수(`closure_dropped`)만 남는다.
DROPPED_LISTED = 50

# 호출 대상이 아닌 낱말과 돌아오지 않는 호출 — 전달자로 안 본다. 후자는 `abort`류라 접으면 조용히
# 넘기는 것으로 읽힌다(계약이다). **정한 목록이다**(libc·GCC 내장 + 흔한 커널식 이름).
_NOT_A_CALL = frozenset("if while for switch return sizeof do else case goto".split())
_NORETURN = frozenset(
    "abort exit _exit _Exit quick_exit longjmp siglongjmp assert __assert_fail __builtin_trap "
    "__builtin_unreachable panic BUG BUG_ON".split()
)

# 조회: (이름들, 가까운 파일) → 이름 → 정의. `GraphIndex.callables_of`가 그 모양이다.
Callables = Callable[[Iterable[str], str], dict[str, CallableDef] | None]
Reader = Callable[[Location], str]
Block = Callable[[Location, str], str]
Measure = Callable[[str], int]

# 문자열·문자 리터럴이 먼저라 그 안의 `//`·`/*`는 주석으로 안 읽힌다.
_LEXEME = re.compile(r'"(?:\\.|[^"\\\n])*"' r"|'(?:\\.|[^'\\\n])*'" r"|/\*.*?\*/|//[^\n]*", re.S)
_CALL = re.compile(r"(?<![\w.])(?<!->)([A-Za-z_]\w*)\s*\(")
_IDENT = re.compile(r"[A-Za-z_]\w*")
_DEFINE = re.compile(r"\s*#\s*define\s+([A-Za-z_]\w*)(\([^)]*\))?\s*(.*)", re.S)
_CALL_HEAD = re.compile(r"(?:return\b\s*)?(?:\(\s*[A-Za-z_][\w\s\*]*\)\s*)*([A-Za-z_]\w*)\s*\(")
_CAST = re.compile(r"\(\s*([A-Za-z_][\w\s\*]*)\)\s*(?=\S)")
# 상수: 숫자 · 문자열·문자 리터럴 · `true`/`false` · 대문자 식별자(`NULL`·`__LINE__`·`FLAG_X`).
_LITERAL = re.compile(
    r"""-?[0-9][\w.]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|true|false|[A-Z_][A-Z0-9_]*"""
)
_BANNER = re.compile(r"/\* ===== (.+):(\d+)-(\d+) ===== \*/")


# --- 원문 읽기 도우미: 주석·따옴표·괄호 ---------------------------------------------------------


def _without_comments(text: str) -> str:
    """주석을 공백 하나로 바꾼다. 문자열·문자 리터럴 안의 `//`는 주석이 아니다."""
    return _LEXEME.sub(lambda m: m[0] if m[0][0] in "\"'" else " ", text)


def _code_only(text: str) -> str:
    """주석을 공백으로, 문자열·문자 리터럴을 빈 따옴표로 — 안의 `foo(`가 호출로 안 걸리게."""
    return _LEXEME.sub(lambda m: '""' if m[0][0] in "\"'" else " ", text)


def _skip_quoted(text: str, at: int) -> int:
    """`text[at]`이 여는 따옴표일 때 닫는 따옴표 **다음** 위치. 같은 줄에 안 닫히면 그 따옴표만
    건너뛴다."""
    quote, i = text[at], at + 1
    while i < len(text) and text[i] != "\n":
        if text[i] == "\\":
            i += 2
        elif text[i] == quote:
            return i + 1
        else:
            i += 1
    return at + 1


def _match_paren(text: str, open_at: int) -> int:
    """`text[open_at]`('(')의 짝 `)`의 위치, 없으면 -1. 따옴표 안의 괄호는 세지 않는다."""
    depth, i = 0, open_at
    while i < len(text):
        char = text[i]
        if char in "\"'":
            i = _skip_quoted(text, i)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_args(inner: str) -> list[str]:
    """최상위 쉼표로 가른다 — 괄호·대괄호·중괄호·따옴표 안의 쉼표는 안 가른다. 빈 입력은 인자
    0개다."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(inner):
        char = inner[i]
        if char in "\"'":
            i = _skip_quoted(inner, i)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(inner[start:i].strip())
            start = i + 1
        i += 1
    last = inner[start:].strip()
    if last or parts:
        parts.append(last)
    return parts


# --- 전달 판정: 한 문장 · 인자가 파라미터·상수 --------------------------------------------------


def _core(arg: str, params: Collection[str]) -> str:
    """인자에서 바깥 괄호와 형변환을 벗긴 알맹이."""
    arg = arg.strip()
    while True:
        if arg.startswith("(") and _match_paren(arg, 0) == len(arg) - 1:
            arg = arg[1:-1].strip()
            continue
        cast = _CAST.match(arg)
        # 형변환의 「타입」 자리에 파라미터가 있으면 `(a)(b)`처럼 호출이다 — 벗기지 않는다.
        if cast is not None and not any(word in params for word in re.findall(r"\w+", cast[1])):
            arg = arg[cast.end() :]
            continue
        return arg


def _is_forwarded(arg: str, params: Collection[str]) -> bool:
    """인자가 파라미터이거나 그 형변환·괄호이거나 상수인가. 계산·필드 접근·소문자 전역은 아니다 —
    그것은 전달이 아니라 값을 만드는 것이라 접으면 안 된다."""
    core = _core(arg, params)
    return core in params or _LITERAL.fullmatch(core) is not None


def _args_suffix(args: list[str], params: tuple[str, ...]) -> str:
    """전달이 자기 파라미터를 같은 순서로 그대로 넘기는 것이 아니면(상수를 얹거나 순서를 바꾸거나
    뺀다) 그 인자 — 접으면서 값이 사라지는 것을 막는다. 그대로 넘기면 빈 문자열이다."""
    if [_core(arg, params) for arg in args] == list(params):
        return ""
    shown = ", ".join(" ".join(arg.split()) for arg in args)
    return f"({shown if len(shown) <= ARGS_CHARS else shown[: ARGS_CHARS - 3] + '...'})"


def _forward(statement: str, params: tuple[str, ...]) -> tuple[str, list[str]] | None:
    """`statement`가 `[return] [(형변환)] 이름(인자…);` **한 문장**이고 인자가 전부 파라미터·상수면
    (이름, 인자들), 아니면 `None`. 호출 뒤에 무엇이든 더 붙으면(`?:`·`&&`·`, x`) 전달이 아니다."""
    text = statement.strip()
    head = _CALL_HEAD.match(text)
    if head is None or head[1] in _NOT_A_CALL or head[1] in _NORETURN:
        return None
    open_at = head.end() - 1
    close = _match_paren(text, open_at)
    if close < 0 or text[close + 1 :].strip() not in ("", ";"):
        return None
    args = _split_args(text[open_at + 1 : close])
    return (head[1], args) if all(_is_forwarded(arg, params) for arg in args) else None


def _function_forward(
    where: Location, params: tuple[str, ...], read: Reader
) -> tuple[str, str] | None:
    """함수가 전달자면 (다음 이름, 넘기는 인자 표시). 줄 수가 한도를 넘으면 읽지도 않는다."""
    start = where.line_start
    if start is None or (where.line_end or start) - start + 1 > WRAPPER_MAX_LINES:
        return None
    clean = _without_comments(read(where))
    begin, end = clean.find("{"), clean.rfind("}")
    if begin < 0 or end < begin:
        return None
    forward = _forward(clean[begin + 1 : end], params)
    return None if forward is None else (forward[0], _args_suffix(forward[1], params))


def _parse_define(text: str) -> tuple[str, tuple[str, ...] | None, str] | None:
    """`#define` 원문 → (이름, 파라미터 | 객체형이면 `None`, 본문). 주석·줄 이음·공백을 접은 뒤
    읽는다. 가변 인자(`...`)는 `__VA_ARGS__`가 파라미터로 든다."""
    flat = " ".join(re.sub(r"\\\r?\n", " ", _without_comments(text)).split())
    match = _DEFINE.fullmatch(flat)
    if match is None:
        return None
    name, raw, body = match.groups()
    if raw is None:
        return name, None, body
    params: list[str] = []
    for part in raw[1:-1].split(","):
        word = part.strip().removesuffix("...").strip()
        if part.strip().endswith("..."):
            params.append("__VA_ARGS__")
        if _IDENT.fullmatch(word):
            params.append(word)
    return name, tuple(params), body


def _classify(text: str) -> tuple[str, str | None, str]:
    """`classify_macro`와 같고 끝에 접을 때 넘기는 인자 표시(`_args_suffix`)가 붙는다."""
    parsed = _parse_define(text)
    if parsed is None:
        return "opaque", None, ""
    _, params, body = parsed
    if params is None:
        ident = _IDENT.fullmatch(body) is not None and body not in _NOT_A_CALL
        return ("rename", body, "") if ident else ("opaque", None, "")
    inner = body
    if inner.startswith("(") and _match_paren(inner, 0) == len(inner) - 1:
        inner = inner[1:-1].strip()
    forward = _forward(inner, params)
    if forward is None:
        return "opaque", None, ""
    return "forward", forward[0], _args_suffix(forward[1], params)


def classify_macro(text: str) -> tuple[str, str | None]:
    """`#define` 원문 → (`"rename"` | `"forward"` | `"opaque"`, 다음 이름 | `None`).

    - **rename** `#define A B` — 본문이 식별자 하나(객체형)
    - **forward** `#define A(x) B(x, 1)` — 본문이 호출 하나(바깥 괄호는 벗긴다)이고 인자가 전부
      파라미터·형변환·상수 — 다음 이름 `B`로 접는다
    - **opaque** 그 밖 전부 — 문장 매크로(`do { … } while (0)`·`if (…) goto …`)·`##`·`#x`·계산한
      인자·빈 정의·함수 포인터 호출. 호출 대상이 없으니 정의 원문이 구현이다
    """
    kind, target, _ = _classify(text)
    return kind, target


# --- 사슬 걷기 ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Chain:
    """접은 단계들(`(이름, 종류, 받은 인자 표시)`)과 끝. 끝은 `function`(첫 실제 본문)·
    `macro`(불투명 — 전개가 구현)·`external`(인덱스에 정의가 없다)·`cycle` 중 하나고 `suffix`는 끝이
    받은 인자 표시다."""

    hops: tuple[tuple[str, str, str], ...]
    end: str
    name: str
    where: Location | None
    suffix: str = ""


def _walk(name: str, found: CallableDef | None, callables_of: Callables, read: Reader) -> _Chain:
    """`name`에서 시작해 접히는 동안 따라간다 — 전달자마다 다음 단계가 하나뿐이라 BFS가 사슬
    걷기가 된다. 접는 비용은 0이고 첫 실제 본문(전달자가 아닌 함수 · 불투명 매크로)에서 멈춘다."""
    hops: list[tuple[str, str, str]] = []
    visited: set[str] = set()
    suffix = ""
    while True:
        if name in visited:
            return _Chain(tuple(hops), "cycle", name, None, suffix)
        visited.add(name)
        if found is None:
            return _Chain(tuple(hops), "external", name, None, suffix)
        kind, where, params = found
        if kind == "ambiguous":
            return _Chain(tuple(hops), "ambiguous", name, None, suffix)
        step = None
        if len(hops) < FOLD_DEPTH:
            if kind == "macro":
                _, target, passed = _classify(read(where))
                step = (target, passed) if target else None
            else:
                step = _function_forward(where, params, read)
        if step is None:
            return _Chain(tuple(hops), kind, name, where, suffix)
        hops.append((name, kind, suffix))
        name, suffix = step
        found = (callables_of([name], where.file) or {}).get(name)


def _macro_chain(root: Location, callables_of: Callables, read: Reader) -> list[Location]:
    """불투명 매크로의 정의와 그 본문이 쓰는 매크로의 정의들 — 너비 우선, 루트 포함
    `MACRO_CHAIN_DEPTH`단까지. 같은 층에서는 이름 순이다(결정론)."""
    found, level, names = [root], [root], {root.symbol}
    for _ in range(MACRO_CHAIN_DEPTH - 1):
        nxt: list[Location] = []
        for where in level:
            parsed = _parse_define(read(where))
            if parsed is None:
                continue
            used = set(_IDENT.findall(parsed[2])) - names - set(parsed[1] or ())
            defs = callables_of(used, where.file) or {}
            for name in sorted(defs):
                kind, there, _ = defs[name]
                if kind == "macro":
                    names.add(name)
                    found.append(there)
                    nxt.append(there)
        level = nxt
    return found


def shown_ranges(blocks: Iterable[str]) -> list[Location]:
    """SOURCE 블록들이 이미 싣고 있는 범위 — 블록 첫 줄의 배너(`/* ===== 파일:시작-끝 ===== */`)로
    안다. 러너의 `seen`을 안 쓰는 까닭: 그것은 상한에 걸려 **뺀** 함수도 담아서 「위에 있다」고
    적으면 거짓이 된다."""
    ranges = []
    for text in blocks:
        match = _BANNER.match(text)
        if match is not None:
            ranges.append(Location(file=match[1], line_start=int(match[2]), line_end=int(match[3])))
    return ranges


# --- 단위 조립과 예산 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Closure:
    """닫힘의 결과. `blocks`는 SOURCE에 그대로 붙일 단위들, `carried`는 그 안에서 **새로** 실린
    원문의 위치(러너가 뒤 단계의 겹침 검사에 쓴다), `size`는 러너의 셈법(`measure` + 이음 줄바꿈
    1)으로 센 크기, `dropped`는 못 실은 단위의 목록이다. `indexed`는 인덱스 조회가 있었는가 —
    「봤는데 0건」과 「못 봤다」를 가른다. `ambiguous`는 정의가 여럿이라 못 따라간 호출 이름의 수다
    — 단위가 0이어도 「닫을 것이 없다」가 아니라 「못 가렸다」일 수 있어서 센다."""

    blocks: tuple[str, ...] = ()
    carried: tuple[Location, ...] = ()
    size: int = 0
    dropped: tuple[dict[str, str | int], ...] = ()
    indexed: bool = True
    ambiguous: int = 0

    def event(self) -> dict[str, str | int]:
        """`auditor_source` 이벤트에 더할 필드 — 옵션이 켜졌을 때만 부른다. 빠진 단위는 목록(JSON
        문자열 — trace 값은 스칼라뿐이다)으로 남기고 개수는 따로 센다."""
        listed = json.dumps(
            list(self.dropped[:DROPPED_LISTED]), ensure_ascii=False, separators=(",", ":")
        )
        return {
            "closure": len(self.blocks),
            "closure_dropped": len(self.dropped),
            "closure_dropped_units": listed,
            "closure_ambiguous": self.ambiguous,
            "closure_lookup": "index" if self.indexed else "none",
        }


# ponytail: **값 자리** 매크로(`buf[BUFSIZE]` — `(` 없이 쓴 식별자)는 시작점이 아니다. [로컬실측: E4
# 정답 sink 함수 82개와 raw 행을 내가 이은 것 — 저장소 밖, run ID 없음] 매크로를 쓰는 52개 중 50개가
# 호출 자리 매크로를 쓰고(`c_mac` ≥ 1), E4의 사슬 ≥ 2 함수 25개는 전부 호출 자리 매크로가 함께 있다.
# 사슬 ≥ 2인 매크로가 값 자리인 경우를 따로 센 값은 없다. 한 단계 정의는 `auditor_definitions`가
# 이미 싣고, 지역 변수·필드가 매크로 이름을 가리는 문제(locals 위생)가 값 자리에는 있다 — 잰 뒤에
# 붙인다.
def _call_names(text: str, skip: str | None) -> list[str]:
    """앵커 본문이 부르는 이름 — 처음 나온 순서, 중복 없이. 주석·문자열 안과 `p->f(`·`p.f(`(멤버)는
    호출이 아니다. 키워드(`if (`)는 인덱스에 없으니 조회가 거른다."""
    names = dict.fromkeys(_CALL.findall(_code_only(text)))
    return [name for name in names if name != skip]


def _covered(where: Location, shown: Iterable[Location]) -> bool:
    return any(where.overlaps(other) for other in shown)


# 본문 없이 끝나는 사슬의 끝 표시.
_END_NOTES = {
    "cycle": " (cycle)",
    "external": " (no definition in the repo index)",
    "ambiguous": " (ambiguous: several definitions in the repo index)",
}


def _label(name: str, kind: str, suffix: str) -> str:
    """사슬 줄의 한 칸 — 매크로면 `[macro]`, 받은 인자가 있으면 뒤에 붙는다."""
    return f"{name}[macro]{suffix}" if kind == "macro" else f"{name}{suffix}"


def _render(
    chain: _Chain,
    callables_of: Callables,
    read: Reader,
    block: Block,
    shown: list[Location],
) -> tuple[str, list[Location], bool, str]:
    """사슬 하나 → (글, 새로 싣는 위치들, 블록 하나가 자기 상한을 넘었나, 화살표 사슬)."""
    arrows = [_label(name, kind, suffix) for name, kind, suffix in chain.hops]
    parts: list[str] = []
    new: list[Location] = []
    oversize = False
    note = ""
    if chain.end == "function":
        arrows.append(_label(chain.name, "function", chain.suffix))
        if _covered(chain.where, shown):
            note = " (body above)"
        else:
            parts.append(block(chain.where, read(chain.where)))
            new.append(chain.where)
            oversize = len(parts[0]) > BODY_CHARS
    elif chain.end == "macro":
        arrows.append(_label(chain.name, "macro", chain.suffix))
        for where in _macro_chain(chain.where, callables_of, read):
            if _covered(where, shown):
                continue
            parts.append(block(where, read(where)))
            new.append(where)
            oversize = oversize or len(parts[-1]) > MACRO_CHARS
        if _covered(chain.where, shown):
            note = " (definition above)"
    else:
        arrows.append(chain.name + chain.suffix)
        note = _END_NOTES[chain.end]
    chain_text = " -> ".join(arrows)
    head = f"/* CLOSURE: {chain_text}{note} */"
    return "\n".join([head, *parts]), new, oversize, chain_text


def build_closure(
    anchor: Location,
    anchor_text: str,
    *,
    callables_of: Callables,
    read: Reader,
    block: Block,
    blocks: Iterable[str],
    measure: Measure,
    room: int,
) -> Closure:
    """앵커 함수가 부르는 매크로·전달 함수의 닫힘 단위들.

    `anchor_text`는 SOURCE에 실린 앵커 원문이고(줄인 구간 표시를 뺀 것), `blocks`는 지금까지
    SOURCE에 실린 블록들(배너로 무엇이 이미 실렸는지 안다 — `shown_ranges`)이다. `block`은 러너의
    배너 형식(`runner._block`)이고 `measure`·`room`은 러너의 셈법과 남은 몫이다 — 단위 하나는
    `1 + measure(글)`(이음 줄바꿈)을 센다. 순서는 앵커 본문에서 이름이 처음 나온 순서다.

    접은 단계가 하나도 없는 함수 호출(`raw_alloc` 같은 실제 함수를 바로 부름)과 인덱스에 없는 이름은
    단위를 안 만든다 — 피호출 본문은 `auditor_callee_hops`의 일이다.
    """
    names = _call_names(anchor_text, anchor.symbol)
    known = callables_of(names, anchor.file)
    if known is None:
        return Closure(indexed=False)
    memo: dict[Location, str] = {}

    def text_of(where: Location) -> str:
        if where not in memo:
            memo[where] = read(where)
        return memo[where]

    shown = shown_ranges(blocks)
    out: list[str] = []
    carried: list[Location] = []
    dropped: list[dict[str, str | int]] = []
    used = 0
    ambiguous = sum(1 for name in names if name in known and known[name][0] == "ambiguous")
    for name in names:
        if name not in known:
            continue
        chain = _walk(name, known[name], callables_of, text_of)
        if not chain.hops and chain.end != "macro":
            continue
        # 인덱스는 가리키는데 원문이 비면(파일이 사라짐) 본문 없는 배너를 싣지 않는다.
        if chain.end in ("function", "macro") and not text_of(chain.where).strip():
            chain = _Chain(chain.hops, "external", chain.name, None, chain.suffix)
        text, new, oversize, arrows = _render(chain, callables_of, text_of, block, shown)
        if not chain.hops and chain.end == "macro" and not new:
            continue  # 정의가 이미 다 실렸고 따라갈 사슬도 없다 — 보탤 것이 없다(빠진 것이 아니다)
        cost = 1 + measure(text)
        if oversize or used + cost > room:
            reason = "block_cap" if oversize else "cap"
            dropped.append({"chain": arrows, "reason": reason, "chars": len(text)})
            continue
        out.append(text)
        used += cost
        carried.extend(new)
        shown.extend(new)
    return Closure(tuple(out), tuple(carried), used, tuple(dropped), ambiguous=ambiguous)
