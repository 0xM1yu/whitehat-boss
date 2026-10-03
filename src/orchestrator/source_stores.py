"""Auditor `SOURCE`의 `STORES` 블록 — 슬라이스의 필드·전역이 **어디서 쓰이는지** (`auditor_stores`).

검수에서 「이 길이·버퍼·필드는 어디서 정해지나」(호출부·할당)를 묻는 행이 많았다 — 83샘플 검수의
미해결 사유 209건을 키워드로 센 집계(데이터팀 CSV `sample_causes_round3.partial.csv`, 저장소 밖
로컬 · run ID 없음 · 키워드가 겹쳐 합이 100%를 넘는다)에서 호출부·할당은 42행(51%)이고 피호출
본문·계약(66행)과 범위·검증(57행) 다음이다. 가장 흔한 요청이라는 뜻은 아니다 — s500 1,199표본의
사유 태그(로컬 집계 · run ID 없음)에서는 함수·매크로·타입·API 동작(C4) 1,008건이 버퍼 용량·입력
범위·호출 전 검증(C5) 920건보다 많다. 답은 인덱스에 이미 있다 — `field_assigns`(구조체 필드
쓰기)·`assigns`(변수 대입)·`calls`(할당기 호출과 크기 식). 그런데 `SOURCE`는 슬라이스가 걸친
함수의 본문만 싣고 그 표를 안 썼다(NE resolver만 썼다). 이 블록이 그 틈을 채운다(첫 줄은 `HEADER`):

    /* ===== STORES (index name matches, direct assignments only, ...) ===== */
    util.c:81 | ctx->len = n * 2; | writes ->len
    util.c:97 | buf = malloc(ctx->len + 1); | allocates buf (malloc: ctx->len + 1)
    /* ... +3 more rows not shown ... */

여기는 **순수 쪽**이다 — 인덱스 조회(`stores_of`)와 원문 읽기(`read_source`)를 콜러블로 받는다. 구체
인덱스(`GraphIndex.stores_of`)는 러너가 몰라도 되게 어댑터에 있고, 둘 사이를 건너는 값은
`src/schemas/stores.py`다.

## 무엇을 찾는가

슬라이스의 줄(`SliceRef.lines` + entry 줄)에서 **필드**(`x->len`·`x.len`의 `len`)와 **변수**(맨
식별자)를 뽑는다. entry에서 가까운 줄이 먼저다 — Case B에서 entry는 sink 호출 줄이라 그 인자가 맨
앞이고, 상한이 먼저 자르는 것은 먼 쪽이다. 변수가 지역·파라미터인지, 파일 스코프 변수인지는
**인덱스가 가린다**(`locals`·`file_vars`) — 여기서는 식별자 후보만 낸다.

## 무엇을 싣는가

- 쓰기가 **SOURCE에 실제로 보이는 줄** 안이면 안 싣는다(`visible`) — 정의·피호출 단계가 겹침을
  거르는 것과 같다. `auditor_entry_trim`으로 줄어든 entry 함수는 **남은 토막만** 보이는 줄이다.
  센다(`in_source`): 「못 찾았다」와 「이미 보인다」가 갈려야 한다.
- 순서: 같은 파일의 쓰기 → 제품 경로 쓰기(`StoreSite.nonproduct`가 아닌 것 — 예제·테스트·fuzz
  쓰기는 뒤로 미룬다) → 모호하지 않은 것 → 좌변 철자가 같은 것 → (파일, 줄). **이름마다**
  `_PER_NAME`행까지(몇 파일에서 봐도 합이다), 블록은 `_STORES_CHARS`와 `SOURCE` 상한의 남은
  몫(`room`)까지 — **글자 상한이 토큰 상한을 묶어 주지 않는다**(`measure`가 그 단위).
- 못 실은 행이 있으면 블록 끝에 `+N more rows not shown` 한 줄이 붙는다(모델이 「이게 전부」로 읽지
  않게 — 그 줄 자리는 미리 남겨 둔다). 못 실은 행은 trace에도 개수가 아니라 `file:line 이름`
  **목록**으로 남는다(`Stores.trace_fields`).

## 한계 (구문만 본다 · 이름으로 잇는다)

`buf[i] = x`·`*p = x`·`i++`·지정 초기화·함수에 `&x`를 넘겨 쓰는 경우는 인덱스가 쓰기로 안 센다.
그래서 **행이 없다는 것은 쓰기가 없다는 증거가 아니다.** 필드 쓰기 행(`field_assigns`)에는 객체의
구조체 타입이 없어 (구조체, 필드)로 못 짚는다 — 이름 키라서 흔한 필드(`len`·`next`)는 쓰기가
수십 곳이고 대부분 다른 구조체의 것이다. 인덱스가 `ambiguous`를 **달지 않는** 필드 행의 조건은
둘이다: 그 필드를 선언한 구조체가 인덱스에서 (타입, 파일) 하나이고, 그 파일이 쓰기 파일과 찾는
줄의 파일 **둘 다와 같은 디렉터리**(include 정보가 인덱스에 없어 「둘 다 볼 수 있다」를 이것으로
근사한다)일 때. 그 밖은 `ambiguous`다. ⚠ 달리지 않았어도 증명이 아니다 — 진짜 구조체가 인덱스 밖
헤더에 있고 같은 디렉터리의 다른 구조체가 같은 필드 이름을 가지면 못 가린다. 여기서는 이름마다
`_PER_NAME`행으로 자른다. 할당기 호출은 **줄 단위**로 이어진 것이라(`x = f(malloc(n))`도 같은 줄)
원문 줄의 우변이 그 호출일 때만 `allocates`라 쓰고 아니면 「같은 줄에 호출이 있다」고만 쓴다.
ponytail: 멀티라인 문장은 괄호가 안 닫힐 때 **다음 한 줄**만 잇는다. 인덱서가 쓰기 좌변 객체의
타입과 include를 행에 실으면(열 추가 = 인덱스 판 올림) 모호 표시가 대부분 걷힌다.
"""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.schemas.candidate import SliceRef
from src.schemas.common import Location
from src.schemas.stores import StoreSite, StoreTarget

# 블록의 첫 줄 — `SOURCE`의 다른 블록(`/* ===== 파일:줄 ===== */`)과 모양은 같고 이름이 다르다.
# 프롬프트가 이 줄을 파일 범위 배너로 읽지 않게 하고, 행이 무엇인지(이름이 같은 직접 대입만 · 전부
# 아님 · `[ambiguous]` = 같은 구조체·변수의 것이라고 확인하지 못함)를 블록 안에서 말한다.
HEADER = (
    "/* ===== STORES (index name matches, direct assignments only, not exhaustive; "
    "[ambiguous] = owner not confirmed) ===== */"
)

# 블록 하나의 글자 상한 — 정의 하나(`runner._AUDITOR_DEFINITION_CHARS`)와 같은 크기다. **정한 값이지
# 잰 값이 아니다.** `auditor_source_tokens`가 양수여도 이것은 글자다(하나가 너무 큰지를 거르는 값 —
# 전체 상한만 토큰으로 센다).
_STORES_CHARS = 2_000
# 이름 하나가 보일 수 있는 행 수 — 그 이름을 몇 파일에서 봐도 합이다. 흔한 필드(`len`·`next`)가
# 쓰기 수십 곳으로 블록을 다 먹지 않게 한다. 근거: 83샘플의 정답 sink 줄 82개 중 18개(21.9%)가 바깥
# 쓰기 8행 넘는 필드를 건드렸다(2026-10-03 vast 박스 slicer-dev의 인덱스 집계 — run ID 없음, 원자료
# 저장소 밖 `slicer_e4/PREVALENCE.md`). 정한 값이지 효과를 잰 값이 아니다.
_PER_NAME = 4
# 인덱스에 묻는 이름의 상한 — 수천 줄짜리 슬라이스에서 질의 수를 묶는다. 넘친 이름은 조용히 버리지
# 않고 센다. 정한 값이다.
_TARGETS = 300
# 행에 싣는 원문 줄의 글자 상한 — 생성 코드·긴 매크로 줄이 블록을 먹지 않게. 정한 값이다.
_LINE_CHARS = 160
# 이름을 뽑으려고 훑는 한 줄의 글자 상한 — 압축된 줄이 정규식을 오래 돌리지 않게. 정한 값이다.
_SCAN_CHARS = 2_000
# trace에 **목록으로** 남기는 못 실은 행 수. 넘치면 `+N more` 한 항목이고 개수(`stores_dropped`)는
# 전부다.
_DROPPED_LISTED = 50

# 한 줄에서 코드가 아닌 것 — 문자열·문자 리터럴·주석. 여기 든 `a->b`는 필드 접근이 아니다.
_NOISE = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|/\*.*?\*/|//.*")
# `a->b.c`에서 객체 사슬(`a->b.`)과 **마지막** 필드(`c`). 중간 필드(`b`)는 따로 안 센다.
_CHAIN = re.compile(r"((?:[A-Za-z_]\w*\s*(?:->|\.)\s*)+)([A-Za-z_]\w*)")
# 맨 식별자 — 숫자 리터럴의 꼬리(`0x1F`의 `x1F`)와 필드(`.len`·`->len`)는 뺀다.
_IDENT = re.compile(r"(?<![\w.])(?<!->)[A-Za-z_]\w*")


@dataclass(frozen=True)
class Stores:
    """`stores_block`의 결과. `text`는 블록(`HEADER` 한 줄 + 행) — 행이 없으면 빈 문자열이다."""

    text: str
    rows: int
    # 못 실은 행의 `file:line 이름` — 이름별 상한·블록 상한·남은 몫·읽을 수 없는 줄 **전부**다.
    dropped: tuple[str, ...]
    # 이미 실린 함수 안이라 건너뛴 행 수.
    in_source: int
    # `_TARGETS`를 넘어 인덱스에 묻지 않은 이름 수.
    skipped_targets: int

    def trace_fields(self) -> dict[str, str | int]:
        """`auditor_source` 이벤트에 얹는 필드 — **옵션이 켜졌을 때만** 낸다(꺼진 run의 이벤트는
        그대로다).

        trace 값은 스칼라뿐이라 못 실은 행은 JSON 배열 **문자열**이다. 앞 `_DROPPED_LISTED`개만
        싣고 넘치면 `+N more`를 덧붙인다 — 개수(`stores_dropped`)는 잘리지 않는다."""
        listed = list(self.dropped[:_DROPPED_LISTED])
        if len(self.dropped) > _DROPPED_LISTED:
            listed.append(f"+{len(self.dropped) - _DROPPED_LISTED} more")
        return {
            "stores": self.rows,
            "stores_dropped": len(self.dropped),
            "stores_dropped_rows": json.dumps(listed, ensure_ascii=False),
            "stores_in_source": self.in_source,
            "stores_targets_skipped": self.skipped_targets,
        }


def stores_block(
    ref: SliceRef,
    visible: Sequence[Location],
    *,
    read_source: Callable[[Location], str],
    stores_of: Callable[[StoreTarget], list[StoreSite] | None],
    measure: Callable[[str], int],
    room: int,
) -> Stores:
    """슬라이스의 필드·변수가 쓰이는 곳을 `STORES` 블록으로.

    `visible`은 SOURCE에 **실제로 보이는** 줄 범위들이다 — 실린 함수(entry 함수 + 상한에 들어간
    함수)이고, `auditor_entry_trim`으로 줄어든 entry 함수는 남은 토막만이다. 상한에 걸려 **빠진**
    함수나 줄여서 뺀 줄의 쓰기는 안 보이므로 행이 될 수 있다. `read_source`는 파일 통째로만 읽는다
    (`Location(file=…)`) — 줄 범위 읽기는 `splitlines` 번호가 인덱서(tree-sitter)의 줄 번호와
    어긋날 수 있다. `measure`·`room`은 `SOURCE` 상한의 단위(글자 또는 토큰)와 남은 몫이다 — 블록은
    `_STORES_CHARS` **그리고** `room` 안이어야 한다. 인덱스가 없으면(`stores_of`가 `None`) 블록도
    행도 없다.
    """
    cache: dict[str, list[str]] = {}

    def lines_of(file: str) -> list[str]:
        if file not in cache:
            cache[file] = read_source(Location(file=file)).split("\n")  # CRLF의 `\r`은 줄 끝 공백
        return cache[file]

    targets, skipped = _targets(ref, lines_of)
    shown: list[StoreSite] = []
    dropped: list[str] = []
    in_source = 0
    taken: set[tuple[str, int, str]] = set()
    kept: dict[tuple[bool, str], int] = {}  # 이름(필드·변수) → 지금까지 보이기로 한 행 수
    for target in targets:
        sites = stores_of(target)
        if sites is None:
            break  # 인덱스가 없다 — 이름마다 물을 이유가 없다
        name = (target.field, target.name)
        for site in sorted(sites, key=lambda s: _rank(s, target)):
            key = (site.file, site.line, site.name)
            if key in taken:
                continue  # 다른 파일에서 본 같은 이름이 이미 낸 행
            taken.add(key)
            if _covered(visible, site):
                in_source += 1
            elif kept.get(name, 0) == _PER_NAME:
                dropped.append(_label(site))
            else:
                kept[name] = kept.get(name, 0) + 1
                shown.append(site)
    rows: list[str] = []
    for site in shown:
        row = _row(site, lines_of)
        # 못 실은 행이 하나라도 있으면 끝에 `_more` 줄이 붙는다 — 그 자리를 미리 뺀다(상한 위로 못
        # 나가게). 숫자는 지금까지 못 실은 행 + 아직 안 본 행을 다 센 상한값이라 줄이 안 짧다.
        tail = _more(len(dropped) + len(shown))
        if row is None or _too_big("\n".join([HEADER, *rows, row, tail]), measure, room):
            dropped.append(_label(site))
        else:
            rows.append(row)
    text = ""
    if rows:
        text = "\n".join([HEADER, *rows, *([_more(len(dropped))] if dropped else [])])
    return Stores(text, len(rows), tuple(dropped), in_source, skipped)


def _more(count: int) -> str:
    """못 실은 행이 있다는 블록 끝 한 줄 — 모델이 「행이 이게 전부」로 읽지 않게 한다."""
    return f"/* ... +{count} more rows not shown ... */"


def _rank(site: StoreSite, target: StoreTarget) -> tuple[bool, bool, bool, bool, str, int]:
    """이름을 본 파일의 쓰기가 먼저, 그다음 제품 경로(예제·테스트·fuzz 쓰기는 뒤 — 이름마다 행 수
    상한이 있어 앞선 비제품 행이 제품 행을 밀어내지 않게), 그다음 모호하지 않은 것, 그다음 좌변이
    같은 철자인 것(공백 무시 — 같은 객체일 법하다는 약한 신호일 뿐 구조체가 같다는 증거는 아니다),
    그다음 (파일, 줄)."""
    spelled = target.lhs or ""  # `_names`가 이미 공백 없이 적는다
    return (
        site.file != target.file,
        site.nonproduct,
        site.ambiguous,
        "".join(site.lhs.split()) != spelled,
        site.file,
        site.line,
    )


def _too_big(text: str, measure: Callable[[str], int], room: int) -> bool:
    # `or`의 순서가 값을 정한다 — 글자 상한이 먼저 걸러야 토큰 세기(`measure`)를 안 부른다.
    return len(text) > _STORES_CHARS or measure(text) > room


def _label(site: StoreSite) -> str:
    return f"{site.file}:{site.line} {site.name}"


def _covered(visible: Sequence[Location], site: StoreSite) -> bool:
    """쓰기 줄이 SOURCE에 이미 보이는 범위 안인가. 줄 범위가 없으면 그 파일 전체다."""
    return any(
        loc.file == site.file
        and (
            loc.line_start is None
            or loc.line_start <= site.line <= (loc.line_end or loc.line_start)
        )
        for loc in visible
    )


def _targets(
    ref: SliceRef, lines_of: Callable[[str], list[str]]
) -> tuple[list[StoreTarget], int]:
    """찾을 이름들 — entry 줄이 먼저, 그다음 entry에서 가까운 줄(같은 파일 먼저), 같은 줄 안에서는
    적힌 순서다. **파일마다 이름 하나**로 줄인다(첫 — 가장 가까운 — 줄이 이긴다): 흔한 필드는 쓰기가
    수천 행이라 줄마다 물으면 같은 답을 수십 번 만든다. 어느 함수에서 지역이고 다른 함수에서 전역인
    변수는 가장 가까운 줄의 해석을 따른다(ponytail: 함수까지 키에 넣으면 질의 수가 는다).

    `(대상 목록, _TARGETS를 넘어 안 물은 수)`."""
    entry_file, entry_line = ref.entry.file, ref.entry.line_start or 0
    points = sorted(
        {*ref.lines, (entry_file, entry_line)},
        key=lambda p: (p[0] != entry_file, abs(p[1] - entry_line), p[0], p[1]),
    )
    first: dict[tuple[bool, str, str], tuple[int, str | None]] = {}
    for file, line in points:
        for field, name, lhs in _names(_code(lines_of(file), line)):
            first.setdefault((field, name, file), (line, lhs))
    ordered = list(first.items())
    return (
        [
            StoreTarget(name=name, field=field, file=file, line=line, lhs=lhs)
            for (field, name, file), (line, lhs) in ordered[:_TARGETS]
        ],
        max(0, len(ordered) - _TARGETS),
    )


def _code(lines: list[str], line: int) -> str:
    """그 줄의 코드 — 주석·문자열을 빼고, 전처리 줄(`#include <a.h>`)은 비운다. 줄이 없으면 빈
    문자열."""
    if not 0 < line <= len(lines) or lines[line - 1].lstrip().startswith("#"):
        return ""
    return _NOISE.sub(" ", lines[line - 1][:_SCAN_CHARS])


def _names(code: str) -> list[tuple[bool, str, str | None]]:
    """한 줄에서 쓰기 지점을 찾을 이름 — `(필드인가, 이름, 필드의 철자)`, 적힌 순서. 호출 대상
    (`f(`)은 변수가 아니다."""
    found = [
        (m.start(), True, m.group(2), "".join(m.group(0).split())) for m in _CHAIN.finditer(code)
    ]
    rest = _CHAIN.sub(lambda m: " " * len(m.group(0)), code)  # 필드 사슬은 지워 변수로 또 안 센다
    found += [
        (m.start(), False, m.group(0), None)
        for m in _IDENT.finditer(rest)
        if not rest[m.end() :].lstrip().startswith("(")
    ]
    return [(field, name, lhs) for _, field, name, lhs in sorted(found, key=lambda item: item[0])]


def _row(site: StoreSite, lines_of: Callable[[str], list[str]]) -> str | None:
    """`file:line | 원문 줄 | 술어`. 줄을 못 읽으면(인덱스와 사본이 어긋남) `None` — 빈 행을
    지어내지 않는다."""
    lines = lines_of(site.file)
    if not 0 < site.line <= len(lines):
        return None
    text = lines[site.line - 1].strip()
    if text.count("(") > text.count(")") and site.line < len(lines):
        # 괄호가 안 닫힌 문장은 다음 한 줄(±1줄)까지만 잇는다
        text += " " + lines[site.line].strip()
    text = " ".join(text.split())
    predicate = _predicate(site, text)  # 자르기 전 문장으로 — 잘린 `...`가 우변 판정을 안 흔들게
    if len(text) > _LINE_CHARS:
        text = text[: _LINE_CHARS - 3] + "..."
    return f"{site.file}:{site.line} | {text} | {predicate}"


def _predicate(site: StoreSite, text: str) -> str:
    """한 줄 술어 — `writes ->len` · `assigns g_len` · `allocates ->buf (malloc: c->len + 1)`.
    이름만으로 이은 행은 `[ambiguous]`가 붙는다. `text`는 그 행의 문장(공백을 접은 원문)이다.

    할당기는 인덱스가 **줄 단위**로 이은 것이다 — 문장의 우변이 그 호출일 때(`= (cast) malloc(`)만
    `allocates`라 쓰고, 아니면(`x = f(malloc(n))` · 삼항 · 다른 문장) 「같은 줄에 호출이 있다」고만
    쓴다."""
    what = site.name
    if site.field:
        what = ("." if re.search(r"\.\s*\w+\s*$", site.lhs) else "->") + site.name
    verb = f"assigns {what}"
    if site.field:
        verb = f"writes {what}" + (f" ({site.op})" if site.op != "=" else "")
    body = verb
    if site.alloc:
        args = ", ".join(site.alloc_args)
        call = re.escape(site.alloc)
        direct = re.search(r"(?<![=!<>])=\s*(?:\([^()]*\)\s*)*" + call + r"\s*\(", text)
        body = (
            f"allocates {what} ({site.alloc}: {args})"
            if direct
            else f"{verb}; same line calls {site.alloc}({args})"
        )
    return body + (" [ambiguous]" if site.ambiguous else "")
