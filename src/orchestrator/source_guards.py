"""Auditor `SOURCE`의 GUARDS 블록 — sink 앞에 있는 가드를 **구문만으로** 모은다(`auditor_guards`).

Joern의 데이터흐름은 검사를 무시한다 — `if (len > MAX) return;`는 sink의 조상이 아니라서
슬라이스에도, 슬라이스가 걸친 함수를 이어 붙인 SOURCE에도 「범위가 검사됐는가」가 구조로 안
실린다. 검수자가 자주 요청한 정보(범위·검증)인데 재료는 앵커 함수의 원문에 이미 있다. 그래서
sink 호출의 인자 이름을 언급하는 조건을 줄번호와 함께 모아 SOURCE에 붙인다 — `if`·`while`·
`for`·삼항·assert 계열, 그리고 `if`가 곧바로 탈출(`return`·`goto`·`break`·`continue`·
`abort`·`exit`)하면 그 탈출문.

## 이것이 주장하는 것과 안 하는 것

**지배(dominate)를 주장하지 않는다.** 구문 훑기라서 그 조건이 sink 경로 위에 있는지, 우회되는지,
다른 가지의 것인지 모른다 — 머리줄이 `GUARDS (syntactic — may not dominate)`인 이유다.

**「없음」을 단언하지 않는다.** 하나도 못 찾으면 맞춰 본 이름을 줄에 적는다 — `GUARDS: no
condition before the sink mentions <이름들> (syntactic scan of this function; …)`. 이름과 못 보는
것(별칭 한 걸음 너머·클램프·switch)이 모델이 보는 줄에 있어야 한다. 독립 검토가 실제 sink 82개를
훑어 보니 「없음」 12개 중 3개가 틀렸다(로컬 · run ID 없음 · 재현 안 함 — 세 꼴: 지역 변수로 받은
클램프(`n = len`), 합으로 만든 용량 검사(`total = a + b`), 포인터 오프셋 읽기를 거친 크기 검사).
맨 「none found」는 거짓이었다.

## 어떻게 잇나 — 이름, 그리고 대입 한 걸음

sink 인자에 나온 식별자(필드 이름 포함 · 함수 이름·키워드·`_t` 타입 제외) 중 하나를 조건이
언급하면 후보다. 거기에 **대입 한 걸음**을 더한다 — 앵커 함수 안 sink 앞의 `x = <식>`에서
(가) `x`가 인자 이름이면 식 안의 이름(인자를 **계산한** 이름: `n = len - off`의 `len`·`off`),
(나) 식 안에 인자 이름이 있으면 `x`(인자에서 **나온** 이름: `limit = n * 2`의 `limit`)가 이름이
된다. 한 걸음뿐이다(두 걸음 너머 `b = a; a = n`의 `b`는 안 이어진다). 대입은 `=`·`+=` 같은
복합 대입·초기화가 있는 선언·`for`의 초기식이고, `x`는 뒤따르는 `.f`·`->f`까지 센다(`[i]` 첨자는
출처가 아니라 뺀다). 한 줄이 `_SCAN_CHARS`를 넘으면 그 줄은 앞쪽에서만 대입을 찾는다(압축·생성
코드의 긴 점 사슬에서 대입 정규식이 제곱 시간이 든다).

**별칭으로만 이어진 행에는 `[alias: m←n]`이 붙는다** — 조건이 인자 이름을 직접 말하지 않고 대입
한 걸음으로 이어진 이름(`m = n + 1`의 `m`)만 말한 행이다. `좌변←우변`이 그 이름을 이은 대입이고(소스
에서 처음 나온 것, 이름 순으로 `_ALIAS_SHOWN`개까지) 모델이 그 조건을 인자 자신의 검사로 읽지 않게
한다. 인자 이름을 직접 말하는 행에는 안 붙는다.

## 한계

- 앵커 함수 **안**만 본다 — 호출자의 검증·피호출 함수 안의 검증은 안 본다.
- **창 앵커에는 블록을 안 붙인다**(`guards_skipped=window`) — 품는 함수를 모르면 앵커가 entry
  앞뒤 고정 창이고, 그 창에는 **다른 함수**의 조건이 든다(독립 검토: 조건 행이 든 창 블록의
  33%에 남의 행이 있었다).
- **원문 줄이 어긋날 수 있는 파일은 안 훑는다**(`guards_skipped=formfeed`·`bare_cr`) —
  `read_source`는 `splitlines()`로 줄을 세서 폼피드·수직탭·`\\x1c`~`\\x1e`·`\\x85`·U+2028·
  U+2029에서도 끊지만 슬라이서·인덱서는 줄바꿈만 센다(#259). 그런 글자가 함수 앞·안에 있으면
  원문이 그만큼 위로 밀려 sink 줄이 다른 줄이다(독립 검토: 폼피드 하나로 훑은 114건 중 30건이
  틀렸고 7건은 「없음」이 됐다). 정렬 검사로는 못 걸러서 파일 **원문**(`read_source`가 줄 범위
  없이 주는 것)에서 함수 끝 줄까지를 본다. 함수 뒤의 글자는 상관없다. `bare_cr`는 같은 어긋남의
  다른 갈래다 — `read_source`가 파일을 텍스트로 읽으며 `\\r`을 `\\n`으로 접어 `\\r\\r\\n`이 줄 둘이
  되는데 인덱서는 `\\n`만 센다. 접힌 원문에는 그 `\\r`이 없어서 **원본 바이트**
  (`RunContext.read_raw`)에서 `\\n`을 안 단 `\\r`을 찾는다. 원본을 못 읽으면(미배선·`None`) 이
  검사는 못 한다.
- 전처리를 안 한다 — `#if`의 두 가지가 다 보이고(조건식은 안 본다), 매크로 안의 가드는 이름이
  assert 계열(`assert`·`CHECK`·`WARN_ON` 등)일 때만 센다. `MIN(n, cap)` 같은 클램프 매크로·
  `switch`/`case`·`if constexpr`는 모른다.
- sink를 품은 조건식은 가드로 안 센다 — `if (n < cap && copy(…))`에서 `n < cap`은 sink 앞의
  단락 평가 피연산자지만 같은 조건식 안이라 훑지 않는다.
- 탈출은 `if` 몸통의 **문장 머리**에 있을 때만 표시한다 — `else` 가지·더 안쪽 블록·안쪽 `if`/
  반복문의 몸통(`if (s) return`)·멤버 호출(`x.abort()`)의 탈출은 조건부이거나 이 가드의 것이
  아니라 표시하지 않는다(조건은 싣는다).
- 연산자 sink(`a[i] = x` — 호출이 없다)는 줄 머리의 `if (…)`만 sink 앞으로 센다 —
  `else if (…) a[i] = x;`·`{ if (…) … }` 한 줄은 놓친다. 호출의 수신 객체(`v.push_back(x)`의
  `v`)는 인자가 아니라 안 센다.
- 원시 문자열(`R"(…)"`)은 모른다. 문자열·주석·전처리 줄을 지우고 괄호를 맞추는 훑기다.

## 행이 많을 때

`_MAX_ROWS`를 넘으면 **직접 이름 행**(조건이 sink 인자 이름을 직접 말하는 행)을 먼저 남긴다 — 그
안에서 조기 탈출 행과 sink를 품은 조건이 먼저, 그다음 sink에 가까운 순이다. 별칭으로만 이어진 행은
그 뒤다. 보여 줄 때는 소스 순서다(독립 검토: 상한을 넘는 블록의 71%에서 조기 탈출·sink를 품은
행이 말없이 빠졌다 · 별칭 조건이 상한을 채워 69개 sink에서 직접 이름 행 174개를 밀어냈다 —
로컬 · run ID 없음). 뺀 행이 있으면 블록 끝에 `... +N more rows omitted`를 적고 — 그 줄도
블록의 일부라 `runner`의 예산 계산에 들어간다 — 뺀 행은 trace `guards_dropped`가 든다.

## 어디에 붙나

`runner._auditor_source`가 entry 함수 바로 뒤에 붙인다. **Case B만**이다 — 슬라이스의 `entry`가
sink 호출 줄이고 `entry.symbol`이 그 호출 이름이다(`slice_refs`). Case A(진입점에서 앞으로
자른 것)의 entry는 함수 자신이라 sink를 하네스가 모른다. 안 붙인 사유(`case_a`·`window`·
`formfeed`·`bare_cr`·`no_sink`)는 trace `auditor_source`의 `guards_skipped`가 든다.

⚠ **PoV도 이 블록을 받는다.** 러너가 Auditor가 본 SOURCE 문자열을 `state.finding_sources`로
물려 주고(`runner._step_auditor`) 8단계 PoV가 `pov._WIDE_INPUT_VERSIONS`인 판에서 그것을 그대로
프롬프트에 싣는다. 그래서 옵션을 켜면 PoV 프롬프트에도 GUARDS가 든다.
"""

import bisect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from src.schemas.candidate import SliceRef
from src.schemas.common import Location

# SOURCE에 붙는 블록의 머리줄 — 정확히 이 문자열이다(테스트가 핀한다). 「가드」라고만 쓰지 않고
# 「구문상 · 지배 안 할 수 있음」까지 머리에 박는다 — 모델이 이 목록을 증명으로 읽지 않게.
GUARDS_HEADER = "GUARDS (syntactic — may not dominate)"
# 하나도 못 찾았을 때 — 맞춰 본 이름과 못 보는 것을 같이 적는다(맨 「none found」는 거짓이었다).
_NO_CONDITION = (
    "GUARDS: no condition before the sink mentions {names} (syntactic scan of this function; "
    "aliases beyond one assignment hop, MIN/MAX-style clamps and switch are not tracked)"
)
_MORE_ROWS = "... +{count} more rows omitted"

# 싣는 행 수 · trace에 목록으로 남길 뺀 행 수 · 「없음」 줄에 적는 이름 수 · 행 하나·탈출문 하나의
# 글자 수. **정한 값이지 잰 값이 아니다** — 블록이 SOURCE의 한 줌에 머물게 하는 폭이다.
_MAX_ROWS = 12
_MAX_DROPPED_LISTED = 50
_MAX_NAMES_SHOWN = 10
_ROW_CHARS = 160
_EXIT_CHARS = 40
# 별칭 행의 `[alias: …]`에 적는 대입 수(조건 하나가 별칭 이름을 여럿 말해도 이만큼만) · 별칭 대입을
# 찾으려고 훑는 한 줄의 글자 수(압축·생성 코드의 한 줄이 대입 정규식을 제곱 시간으로 돌리지 않게
# 줄마다 앞쪽만 본다). 둘 다 정한 값이다.
_ALIAS_SHOWN = 3
_SCAN_CHARS = 2_000

# 주석·전처리 줄·(literals면) 문자열·문자 리터럴을 같은 길이의 공백으로 지운다 — 줄바꿈은 남겨
# offset과 줄 번호가 원문과 같다. 전처리 줄이 먼저다(`#error don't`의 아포스트로피가 문자
# 리터럴을 열지 않게). **수 리터럴(`num`)이 맨 앞 갈래다** — 한 덩어리로 먹고 지우지 않는다: C23·
# C++14 자릿수 구분자 `1'000`·`0xFF'FF`의 `'`가 문자 리터럴을 열면 같은 줄의 다음 `'`까지(그 사이의
# 이름까지) 지운다. 수 모양은 `source_definitions._NOT_CODE`의 `num`과 같다(이름 끝 숫자 `u8'x'`의
# `8`은 수의 시작이 아니다).
_DIRECTIVE = re.compile(r"^[ \t]*#(?:[^\n]*\\\n)*[^\n]*", re.MULTILINE)
_LITERAL_OR_COMMENT = re.compile(
    r"(?P<num>(?<![A-Za-z0-9_])[0-9](?:'(?=[A-Za-z0-9_])|[A-Za-z0-9_.])*)"
    r"|//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'",
    re.DOTALL,
)
_NON_NEWLINE = re.compile(r"[^\n]")
# `str.splitlines()`는 줄바꿈 말고도 이 글자들에서 끊는다 — 슬라이서·인덱서는 줄바꿈만 센다(#259).
_SPLITLINES_ONLY = re.compile("[\v\f\x1c\x1d\x1e\x85" + chr(0x2028) + chr(0x2029) + "]")
# `\n`을 안 단 `\r`(`\r\n`의 짝이 아닌 CR) — 원본 바이트에서 찾는다(`_lines_may_drift`).
_BARE_CR = re.compile(rb"\r(?!\n)")

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
# 대입 — 왼쪽 값(식별자에 `.f`·`->f`·`[i]`가 이어진 것)과 `=`·`+=`·`<<=` 같은 연산자. `==`·
# `!=`·`<=`·`>=`는 안 걸린다(`=` 뒤가 `=`이거나 앞 글자가 연산자 집합 밖이다).
_ASSIGN = re.compile(
    r"([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*|\s*\[[^\]\n]*\])*)\s*(?:<<|>>|[-+*/%&|^])?=(?!=)"
)
_INDEX = re.compile(r"\[[^\]\n]*\]")

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
    `파일:줄 | 조건`, 소스 순서), `names`는 맞춰 본 이름 전부(인자 이름 + 대입 한 걸음)다.
    `skipped`가 있으면 훑지 않은 것이다 — 사유는 `case_a`·`window`·`formfeed`·`bare_cr`·
    `no_sink`."""

    rows: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    skipped: str | None = None

    @property
    def shown(self) -> int:
        return len(self.rows)

    @property
    def block(self) -> str:
        """SOURCE에 붙는 글자. 훑지 않았으면 빈 문자열이다. 행이 없으면 맞춰 본 이름을 적은 한
        줄이다 — 「없음」이 아니라 훑은 결과이고 못 보는 것을 같이 말한다. 행을 뺐으면 끝에 개수를
        적는다."""
        if self.skipped is not None:
            return ""
        if not self.rows:
            shown = self.names[:_MAX_NAMES_SHOWN]
            more = len(self.names) - len(shown)
            names = ", ".join(shown) + (f", ... +{more} more" if more else "")
            return _NO_CONDITION.format(names=names)
        lines = [GUARDS_HEADER, *self.rows]
        if self.dropped:
            lines.append(_MORE_ROWS.format(count=len(self.dropped)))
        return "\n".join(lines)

    @property
    def dropped_listing(self) -> str:
        """trace에 남길 뺀 행 목록(줄바꿈으로 이음). `_MAX_DROPPED_LISTED`행까지고 넘치면 끝에
        개수를 적는다. 뺀 것이 없으면 빈 문자열이다."""
        listed = self.dropped[:_MAX_DROPPED_LISTED]
        more = len(self.dropped) - len(listed)
        return "\n".join((*listed, f"... +{more} more") if more else listed)

    def event_fields(self) -> dict[str, int | str]:
        """trace `auditor_source`에 더할 필드 — 훑었으면 `guards`·`guards_dropped`, 안 훑었으면
        `guards_skipped`(사유). 옵션이 꺼졌으면 이 객체 자체가 없어 필드도 없다."""
        if self.skipped is not None:
            return {"guards_skipped": self.skipped}
        return {"guards": self.shown, "guards_dropped": self.dropped_listing}


class _Found(NamedTuple):
    # 이 구성이 시작하는 offset — 키워드·삼항 조건의 첫 글자·assert 매크로 이름.
    at: int
    # 조건식의 [lo, hi) — sink가 이 안에 있으면 가드가 아니라 sink 자신이다.
    lo: int
    hi: int
    # 행의 본문(조건 + 어떤 구성인지).
    text: str
    # `if`·`while`·`for`면 조건을 닫는 `)` 다음 offset(몸통이 시작하는 곳), 아니면 -1.
    body: int
    # sink 이름과 맞춰 볼 글자의 [names_from, hi) — 보통 `lo`, 대입 삼항은 대입 왼쪽부터.
    names_from: int
    # `if`면 몸통의 탈출을 본다(반복문의 `break`는 가드의 탈출이 아니다).
    is_if: bool


class _Row(NamedTuple):
    at: int  # 구성이 시작하는 offset — 행을 소스 순서로 세우고 sink와의 거리를 잰다
    text: str  # `파일:줄 | 조건 [exit: …] [alias: …]`
    first: bool  # 조기 탈출 행이거나 sink를 품은 조건이다 — 상한이 차면 먼저 남긴다
    direct: bool  # 조건이 sink 인자 이름을 직접 말한다 — 별칭으로만 이어진 행이면 거짓이다


def scan_guards(
    ref: SliceRef,
    anchor: Location,
    text: str,
    read_source: Callable[[Location], str],
    read_raw: Callable[[str], bytes | None] = lambda _file: None,
) -> GuardScan:
    """슬라이스 타깃의 entry 함수 원문 `text`에서 sink 앞의 가드를 훑는다. 훑지 않으면 `skipped`에
    사유가 든 빈 결과다 — 옵션이 켜진 run은 안 훑은 후보도 사유를 trace에 남긴다. `read_raw`는
    파일의 원본 바이트다(`RunContext.read_raw` — `None`이면 못 읽은 것이고 그 검사는 텍스트로만
    한다)."""
    # ponytail: Case A(`given_end == "source"`)는 entry가 진입점 함수 자신이라 sink를 하네스가
    # 모른다 — 건너뛴다. 슬라이서가 슬라이스에 `source_sink`(source·sink로 본 위치 — 슬라이서 쪽
    # 별도 변경)를 싣고 로더가 그것을 `SliceRef`까지 나르면, 여기서 그 sink 위치를 `sink_line`으로
    # `scan_function`을 부르면 된다.
    if ref.given_end != "sink":
        return GuardScan(skipped="case_a")
    # `_auditor_target`은 품는 함수를 모르면 이름 없는 앞뒤 창을 준다 — 그 창에는 다른 함수의 조건이
    # 든다.
    if not anchor.symbol:
        return GuardScan(skipped="window")
    line = ref.entry.line_start
    if line is None:
        return GuardScan(skipped="no_sink")
    drift = _lines_may_drift(read_source, read_raw, anchor)
    if drift is not None:
        return GuardScan(skipped=drift)
    scan = scan_function(
        text,
        file=anchor.file,
        first_line=anchor.line_start or 1,
        sink_line=line,
        sink_name=ref.entry.symbol,
    )
    return scan if scan is not None else GuardScan(skipped="no_sink")


def _lines_may_drift(
    read_source: Callable[[Location], str],
    read_raw: Callable[[str], bytes | None],
    anchor: Location,
) -> str | None:
    """원문 줄이 인덱서가 센 줄과 어긋날 수 있는 사유(`formfeed` · `bare_cr`), 없으면 `None`. 함수
    끝 줄까지만 본다.

    - `formfeed`: 파일 원문에 `splitlines()`만 끊는 글자가 있다(#259). 줄 범위가 없는 위치는 파일
      전체 원문이라 `read_source`가 그 글자를 안 지운 채 준다 — 함수 원문은 이미 `splitlines()`로
      잘려 그 글자가 없다.
    - `bare_cr`: **원본 바이트**에 `\\n`을 안 단 `\\r`이 있다(`\\r\\r\\n` · 옛 Mac 줄바꿈).
      `read_source`는 파일을 텍스트로 읽어 `\\r`을 `\\n`으로 접으므로 접힌 원문에는 그 글자가
      없다 — `\\r\\r\\n`은 줄 둘이 되는데 인덱서는 `\\n`만 세 줄 하나다. 그래서 `read_raw`로
      바이트를 따로 본다."""
    text = read_source(Location(file=anchor.file))
    upto = anchor.line_end
    prefix = text if upto is None else "\n".join(text.split("\n", upto)[:upto])
    if _SPLITLINES_ONLY.search(prefix) is not None:
        return "formfeed"
    raw = read_raw(anchor.file)
    if raw is not None and _BARE_CR.search(_through_line(raw, upto)) is not None:
        return "bare_cr"
    return None


def _through_line(raw: bytes, upto: int | None) -> bytes:
    """원본 바이트에서 `upto`째 줄까지 — 그 줄 끝의 `\\n`을 포함한다(안 그러면 CRLF의 마지막
    `\\r`이 `\\n` 없이 남아 짝 없는 CR로 읽힌다). 줄 범위가 없거나 파일이 그보다 짧으면 전부다."""
    if upto is None:
        return raw
    parts = raw.split(b"\n", upto)
    return b"\n".join(parts[:upto]) + b"\n" if len(parts) > upto else raw


def scan_function(
    text: str, *, file: str, first_line: int, sink_line: int, sink_name: str | None
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
    direct, cutoff = _sink(masked, starts[index], stop, sink_name)
    if not direct:
        return None
    links = _aliases(masked, cutoff, direct)
    names = direct | links.keys()
    rows: list[_Row] = []
    for found in _found(code, masked):
        if found.at >= cutoff or found.lo <= cutoff < found.hi:
            continue
        seen = _names(masked[found.names_from : found.hi])
        if not seen & names:
            continue
        body = _body(masked, found.body) if found.body >= 0 else None
        exit_ = _exit_in(code, masked, *body) if body and found.is_if else None
        encloses = body is not None and body[0] <= cutoff < body[1]
        line = first_line + bisect.bisect_right(starts, found.at) - 1
        tail = f" [exit: {exit_}]" if exit_ else ""
        is_direct = bool(seen & direct)
        if not is_direct:
            tail += _alias_tag(seen, links)
        row = f"{file}:{line} | {_clip(found.text, _ROW_CHARS)}{tail}"
        rows.append(_Row(found.at, row, exit_ is not None or encloses, is_direct))
    shown, dropped = _pick(rows, cutoff)
    return GuardScan(
        rows=tuple(row.text for row in shown),
        dropped=tuple(row.text for row in dropped),
        names=tuple(sorted(names)),
    )


def _alias_tag(seen: set[str], links: dict[str, str]) -> str:
    """조건이 말한 별칭 이름들을 이은 대입(`좌변←우변`)을 적은 표시 — 별칭으로만 이어진 행에 붙는다.
    이름 순이고 `_ALIAS_SHOWN`개까지다."""
    linked = [links[name] for name in sorted(seen & links.keys())]
    more = ", ..." if len(linked) > _ALIAS_SHOWN else ""
    return f" [alias: {', '.join(linked[:_ALIAS_SHOWN])}{more}]"


def _pick(rows: list[_Row], cutoff: int) -> tuple[list[_Row], list[_Row]]:
    """상한을 넘으면 **직접 이름 행**을 먼저 남기고(그 안에서 조기 탈출 행과 sink를 품은 조건이
    먼저, 그다음 sink에 가까운 순), 별칭으로만 이어진 행은 그 뒤다 — 별칭 조건이 상한을 채워
    인자 자신의 가드를 밀어내지 않게. 돌려주는 둘은 다 소스 순서다."""
    if len(rows) <= _MAX_ROWS:
        return rows, []
    ranked = sorted(
        range(len(rows)),
        key=lambda i: (not rows[i].direct, not rows[i].first, cutoff - rows[i].at),
    )
    kept = set(ranked[:_MAX_ROWS])
    shown = [row for i, row in enumerate(rows) if i in kept]
    dropped = [row for i, row in enumerate(rows) if i not in kept]
    return shown, dropped


def _blank(text: str, *, literals: bool) -> str:
    """주석·전처리 줄(`literals`면 문자열·문자 리터럴도)을 같은 길이의 공백으로 바꾼다. 행에
    **보일** 글자(`literals=False`)는 문자열을 남기고 주석만 지운다 — 줄 끝 주석이 접힌 조건의
    뒤를 삼키지 않게."""

    def erase(match: re.Match[str]) -> str:
        return _NON_NEWLINE.sub(" ", match.group())

    def erase_literal_or_comment(match: re.Match[str]) -> str:
        if match["num"]:
            # 수는 지우지 않는다. 이름을 뽑는 쪽(`literals`)은 구분자 `'`만 `_`로 바꿔 `0xFF'FF`의
            # `FF`가 이름으로 안 읽히게 한다 — 같은 길이다.
            return match.group().replace("'", "_") if literals else match.group()
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
    못 찾으면(`STRCPY(…)`처럼 매크로가 감싼 sink) 그 줄의 이름 전부이고 경계는 줄의 첫 글자 바로
    뒤다(줄 머리의 `if (…)`만 앞으로 센다).
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


def _short_lines(text: str) -> str:
    """`_SCAN_CHARS`를 넘는 줄은 그 뒤를 같은 길이의 공백으로 — offset은 그대로다. 대입 정규식은 긴
    점 사슬(`a.b.c. … `)에서 시작 위치마다 끝까지 되훑어 제곱 시간이 든다."""
    return "\n".join(
        line if len(line) <= _SCAN_CHARS else line[:_SCAN_CHARS] + " " * (len(line) - _SCAN_CHARS)
        for line in text.split("\n")
    )


def _aliases(masked: str, cutoff: int, names: set[str]) -> dict[str, str]:
    """sink 인자의 이름과 대입 **한 번**으로 이어진 이름 → 그 이름을 이은 대입(`좌변←우변`, `m = n +
    1`이면 `m←n`). 앵커 함수 안 sink 앞(`cutoff` 앞)의 `x = <식>`에서 `x`가 인자 이름이면 식 안의
    이름(인자를 계산한 이름)이, 식 안에 인자 이름이 있으면 `x`(인자에서 나온 이름)가 별칭이다. 두
    걸음 너머는 안 따라간다. 같은 이름을 잇는 대입이 여럿이면 소스에서 처음 나온 것이다.

    **한 줄이 `_SCAN_CHARS`를 넘으면 그 줄은 앞쪽에서만 대입을 찾는다**(대입의 오른쪽 식은 줄 길이와
    무관하게 끝까지 읽는다)."""
    links: dict[str, str] = {}
    for match in _ASSIGN.finditer(_short_lines(masked[:cutoff])):
        target = _names(_INDEX.sub(" ", match.group(1)))
        value = _names(masked[match.end() : _value_end(masked, match.end(), cutoff)])
        for left in sorted(target & names):
            for right in sorted(value - names):
                links.setdefault(right, f"{left}←{right}")
        for right in sorted(value & names):
            for left in sorted(target - names):
                links.setdefault(left, f"{left}←{right}")
    return links


def _value_end(masked: str, start: int, stop: int) -> int:
    """대입의 오른쪽 식이 끝나는 곳 — 괄호 밖 `;`·`,`, 짝 없는 닫는 괄호, 아니면 `stop`."""
    depth = 0
    for i in range(start, stop):
        char = masked[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif char in ";," and depth == 0:
            return i
    return stop


def _found(code: str, masked: str) -> list[_Found]:
    """원문에서 조건을 품는 구성(if·while·for · assert 계열 · 삼항)을 소스 순서로 모은다."""
    found: list[_Found] = []
    for match in _COND_KEYWORD.finditer(masked):
        opened = match.end() - 1
        close = _close_of(masked, opened)
        if close >= 0:
            text = f"{match.group(1)} ({_squash(code[opened + 1 : close])})"
            found.append(
                _Found(
                    match.start(), opened + 1, close, text, close + 1, opened + 1,
                    match.group(1) == "if",
                )
            )
    for match in _ASSERT_LIKE.finditer(masked):
        opened = match.end() - 1
        close = _close_of(masked, opened)
        if close >= 0:
            text = f"{match.group(1)}({_squash(code[opened + 1 : close])})"
            found.append(_Found(match.start(), opened + 1, close, text, -1, opened + 1, False))
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
        found.append(_Found(lo, lo, question.start(), text, -1, names_from, False))
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


def _body(masked: str, start: int) -> tuple[int, int]:
    """조건 바로 뒤(`start`부터) 몸통의 안쪽 [lo, hi) — 블록 하나나 문장 하나(첫 `;`까지)."""
    i = start
    while i < len(masked) and masked[i].isspace():
        i += 1
    if i < len(masked) and masked[i] == "{":
        close = _close_of(masked, i)
        return i + 1, close if close >= 0 else len(masked)
    end = masked.find(";", i)
    return i, end if end >= 0 else len(masked)


def _exit_in(code: str, masked: str, lo: int, hi: int) -> str | None:
    """`if` 몸통 [lo, hi)에 곧바로 든 조기 탈출문. 몸통의 **문장 머리**(`{`·`}`·`;`·`:` 다음)에
    있는 것만 센다 — 더 안쪽 블록의 탈출도, 안쪽 `if`·반복문·`else`의 몸통(`if (s) return`)도,
    멤버 호출(`x.abort()`)도 이 가드의 탈출이 아니다. 없으면 `None`."""
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
