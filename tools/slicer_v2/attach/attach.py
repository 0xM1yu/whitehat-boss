#!/usr/bin/env python3
"""슬라이스에 원본 함수와 메타 정보를 붙인다 — 하네스 `runner._auditor_source`의 독립판.

출처: whitehat-boss `origin/main` f4544db의 `src/orchestrator/runner.py`
(`_auditor_target` · `_auditor_source` · `_write_sites` · `_clip_around` · `_caller_window` ·
`_overlaps` · `_narrowest_symbol` · `_spans` · `_block` · `_is_short`)와
`config/budgets.yaml`의 Auditor `SOURCE` 옵션 값.

## 하네스와 같은 것

붙이는 규칙과 순서는 그대로다.

1. entry 함수 — 슬라이스 entry 줄을 품는 가장 좁은 인덱스 심볼(없으면 entry 앞뒤 50줄 창)
2. 슬라이스 줄마다 그 줄을 품는 가장 좁은 심볼 (파일, 시작 줄) 순
3. 정의 — **정의 클로저(`closure.py`)로 바꿨다**(사용자 결정 2026-10-04): 타입은 구조체까지 ·
   슬라이스 줄의 필드 경로는 펼침 · 상수는 끝까지 · 정의가 여럿이면 다 붙이고 주석 · 저장소 밖
   상수·타입은 시스템 헤더 값(`system.py`). 하네스는 이름 하나에 정의가 하나뿐일 때만, 재귀 없이 붙였다
4. 피호출 함수 본문 1단 + 짧은 래퍼 너머(2단) + 별칭 매크로가 부르는 함수 + 필드 경유 간접 호출
5. 호출자 창 — entry 함수를 부르는 지점 앞뒤 10줄(지점 8곳까지)
6. 쓰기 지점 창 — 슬라이스 줄의 이름·필드에 쓰는 지점 앞뒤 3줄(6곳까지)

## 노이즈·누락 규칙 (2026-10-04, 하네스와 다름)

cJSON `parse_string`(hs-c8df261bb6b02dbd) 결과를 필수 정보 목록과 맞대 바꿨다.

- **정의는 마지막에 모든 코드 블록에서 찾는다** — 피호출·호출자·쓰기 지점 블록이 쓰는 이름도
  정의가 붙는다(예전엔 entry·슬라이스 함수만 봤다)
- **머리** — entry 파일의 `#include`와, 실린 코드가 부르는 함수의 파일 스코프 전방 선언
- **호출자 사슬** — entry가 `static`이 아니어도 1단은 붙이고, 부른 함수가 `static`이면 그 호출자까지
  (`CALLER_DEPTH`단까지) 올라간다. 외부에 열린 함수에 닿으면 거기서부터 지점 하나씩 `SPINE_DEPTH`단
  더(척추) — 입력이 실제로 어디서 오는지
- 호출·쓰기 지점의 함수가 `SMALL_FUNCTION`줄 이하면 창 대신 **함수 전체**, 길면 창 + 함수 머리
- 호출자 블록이 부르는 `HELPER_LINES`줄 이하 보조 함수의 본문
- entry 밖 슬라이스 함수는 `SMALL_FUNCTION`줄 이하면 전체, 길면 **머리 + 슬라이스 줄만**
- **시작 줄(sink/source 줄)이 부르는 함수는 크기와 상관없이 본문 전체**, 그 안의 피호출은 크기 규칙대로
- **그 밖의 피호출은 슬라이스 줄의 호출**(entry 포함) + entry의 `HELPER_LINES`줄 이하 보조 함수. 본문은
  `SMALL_FUNCTION`줄 이하만, 길면 머리(시그니처)만
- **필드 쓰기 지점은 안 붙인다** — 이름(전역) 쓰기만. 대신 슬라이스 줄이 값을 대입한 필드가 메모리 쓰기
  호출의 인자로 쓰이는 곳(`tainted_use`)을 붙인다

옵션 값은 하네스 기본(`config/budgets.yaml` — definitions 1 · callee_hops 1 · callee_wrappers 1 ·
indirect_callees 1 · caller_lines 10 · write_sites 6)이다.

## 하네스와 다른 것 — 사용자 결정(2026-10-04) 「상한 없이 다 붙인다」

- 전체 상한(하네스 20,000토큰)·피호출 하나 4,000자·정의 하나 2,000자 **글자/토큰 상한을 없앴다.**
  그래서 `dropped`·`callees_dropped`·`definitions_dropped`·`*_dropped`는 늘 0이다(키는 남긴다 —
  하네스 이벤트와 같은 모양으로 읽히게).
- entry 함수 줄이기(`auditor_entry_trim`)는 상한이 없으니 안 한다.
- **개수 규칙은 남겼다** — 호출자 지점 8곳 · 쓰기 지점 6곳 · 간접 후보 필드당 3개 · 래퍼 판정
  (10줄 이하 · 호출 1~2개 · 2단). 크기 상한이 아니라 「무엇을 붙일지」의 선택
  규칙이라서다. CLI로 바꿀 수 있다.
"""

import argparse
import json
import posixpath
import re
import sys
from collections.abc import Callable
from pathlib import Path

from closure import AMBIGUOUS_FILES, Closure, collect, value_like
from common import Location
from index_graph import GraphIndex, is_nonproduct_path, source_lines
from source_definitions import code_only, macro_alias_target, slice_line_refs
from input_context import (choose_input_contract, choose_input_dispatch,
                           choose_input_functions, cpg_input_paths, cpg_proof_for,
                           _input_signals, _raw_cursor_globals)
from system import SystemHeaders

# 하네스 runner.py의 상수 — 값과 뜻이 같다. 글자 상한 셋은 위 docstring대로 뺐다.
WINDOW_LINES = 50
CALLER_SITES = 8
WRAPPER_LINES = 10
WRAPPER_CALLS = 2
WRAPPER_DEPTH = 2
INDIRECT_MAX = 3
WRITE_LINES = 3

# 노이즈·누락 규칙(2026-10-04, cJSON parse_string 표본으로 잼 — attach() docstring).
SMALL_FUNCTION = 40  # 호출·쓰기 지점의 함수가 이 줄 수 이하면 창 대신 함수 전체
HELPER_LINES = 15  # 호출자 블록이 부르는 함수 중 본문을 싣는 짧은 보조 함수
CALLER_DEPTH = 4  # 호출자 사슬 최대 단계 — static이 아닌 함수에서 먼저 멈춘다
SPINE_DEPTH = 3  # 그 뒤 지점 하나씩 더 올라가는 단계(입력의 출처)
PROVENANCE_LINES = 8  # 긴 호출자에서 호출 인자의 출처로 더 붙이는 줄
PROVENANCE_EXTRA = 4  # 그 뒤 단에서 찾은 출처 줄의 상한
PROVENANCE_HOPS = 3  # 그 출처 줄의 오른쪽 변수(`payload = buf`의 buf)를 다시 찾는 단 수
PROVENANCE_CALLEE_LINES = 80  # 그 줄이 부르는 함수(출력 인자를 채우는)의 본문 상한
GUARD_LIMIT = 6  # 골라 실는 함수에서 데이터 흐름 변수를 검사하는 가드 수(P1·P2, 검수 2026-10-05)
CALLER_GUARD_LINES = 8  # 긴 호출자 블록에서 호출 인자를 검사하는 가드 줄 수(P3)
STATEMENT_LINES = 8  # 여러 줄 호출 문장을 `;`까지 읽는 상한
TAINTED_LINES = 60  # 오염된 인자를 받는 큰 피호출에서 고른 줄이 이보다 많으면 머리만
SUSPECT_LINES = 1000  # 이보다 긴 entry는 인덱서가 경계를 잘못 읽은 것으로 보고 어휘로 다시 찾는다
BRACKET_LINES = 3  # 거슬러 오른 함수에서 사슬 호출 앞뒤로 준비·정리 호출을 남길 거리
ENTRY_LINES = 150  # 이보다 긴 entry는 슬라이스 함수처럼 고른 줄만
RESULT_LINES = 30  # 긴 조회·검사 함수에서 결과를 정하는 줄을 골라 실을 때의 상한
HEADER_LINES = 40  # 함수 머리(시그니처)를 찾는 줄 수
BIG_TYPE_LINES = 40  # 이보다 긴 구조체·enum 정의는 실린 코드가 쓰는 멤버 줄만
TABLE_LINES = 20  # 이보다 긴 전역 데이터 표는 슬라이스 줄이 안 쓰면 선언 머리만
STATIC = re.compile(r"\bstatic\b")
INCLUDE_LINE = re.compile(r"^\s*#\s*include\b")
# 파일 스코프 전방 선언 한 줄 — `static const char *parse_value(cJSON *item, ...);`
PROTOTYPE = re.compile(r"^[^=;{}()#]*?\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*;$")
CALL_NAME = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
CALL_KEYWORDS = frozenset({"if", "for", "while", "switch", "return", "sizeof", "defined"})
# 슬라이스 줄에서 값이 대입되는 필드 — `p->f = …`·`s.f[i] = …` (`==`·`<=`·`>=`·`!=` 제외)
TAINTED_FIELD = re.compile(r"(?:->|\.)\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)?(?<![=!<>])=(?!=)")
# 메모리에 쓰는 호출 — 오염된 필드가 그 인자로 쓰이면 범위 밖 쓰기 후보
DANGEROUS_CALL = re.compile(
    r"memcpy|memmove|memset|strcpy|strncpy|strcat|strncat|stpcpy|bcopy|sprintf|snprintf|"
    r"vsprintf|vsnprintf|wmemcpy|wmemmove|wcscpy|wcsncpy|wcscat|read|fread|recv|recvfrom"
)

# 하네스 config/budgets.yaml의 Auditor SOURCE 옵션(정본 v7이 이 값을 선언한다).
DEFAULT_OPTIONS = {
    "definitions": 1,
    "callee_hops": 1,
    "callee_wrappers": 1,
    "indirect_callees": 1,
    "caller_lines": 10,
    "write_sites": 6,
    "caller_sites": CALLER_SITES,
    # 정의를 찾을 줄 — "evidence"(근거 줄 정책: 데이터 의존·시작/끝 줄 · 그 변수를 검사하는 조건 줄 ·
    # 그 변수의 선언 줄 · 보조 블록의 증거 줄, closure.py 「근거 줄 정책」) 또는 "all"(실린 블록 전체 —
    # 2026-10-05까지의 동작). 정의는 출력의 30~36%이고 그 절반 넘게가 근거 줄 밖에서 시드됐다
    # (result_eval/slicer_improvement_plan.md §1.1·§2.5)
    "definition_seed": "evidence",
    # 남길 호출자 척추 줄기 수 — 1(기본, 2026-10-10 개선안 AX2) 또는 0(예전처럼 frontier마다 전부)
    "spine_chains": 1,
    # 저장소에 정의가 없는 빌드 스위치의 `#ifdef` 구역을 본문에서 뺀다 — 1(기본) 또는 0
    "drop_undefined_switches": 1,
    # sink·source 줄이 접근하는 필드를 해제하는 상대 지점 수 — 3(기본, 2026-10-10 7회차), 0이면 안 붙인다
    "release_sites": 3,
    # 로그·추적 호출 한 문장을 본문에서 빼고 정의도 찾지 않는다 — 0(기본) 또는 1. 8회차에 켰다가 리뷰(2026-10-10)에서
    # 가드 탈출 매크로(`debug_return_int`)·중괄호 없는 if 본문·해제 호출(`xmlFreeCatalogEntry`)·`ilog2`를 지우는 근거
    # 손실이 확인돼 껐다
    "drop_log_statements": 0,
    # Input-decoding functions selected from the slice value path.  Small ones
    # stay whole; large ones keep cursor/value statements and their syntax.
    "input_parser_functions": 4,
    "input_parser_chars": 50_000,
    "input_parser_full_lines": 160,
    "input_grammar_functions": 3,
}
# 보조 블록(피호출·호출자·쓰기 지점)에서 정의를 찾을 줄 — 읽기·복사·할당 호출 · sizeof · 첨자 · 크기 비교
# (개선안 DEF-C3의 EVID). `->`·`<<`·`>>`는 비교가 아니다
EVIDENCE_LINE = re.compile(
    r"\b\w*(?:read|recv|copy|cpy|alloc|dup|cat|printf|memset|memmove|free)\w*\s*\("
    r"|\bsizeof\b|\[[^\]]+\]|(?<![-<>=])(?:<=?|>=?)(?![<>=])|!=|\bnew\b"
    r"|\.(?:resize|reserve|assign|insert)\s*\(|std::(?:copy|fill)",
    re.IGNORECASE,
)
# 보이는 줄 전체에서 정의를 찾는 블록 — 취약 함수·슬라이스 함수(핵심)와 그것을 잇는 블록(연결). 이 밖의
# 보조 블록은 EVIDENCE_LINE 줄과 함수 머리만(개선안 DEF-C3의 CORE · CONNECT · AUX)
CORE_KINDS = frozenset({"entry_function", "slice_function", "slice_lines"})
CONNECT_KINDS = frozenset({"caller_window", "caller_chain", "tainted_callee", "sink_callee",
                           "input_parser",  # parser lines are chosen before definition closure
                           "flow_callee", "flow_wrapper_callee",  # 오염된 인자를 받는 피호출·그 래퍼 사슬
                           "field_filler", "callee_signature", "release_site", "release_callee", "result_callee", "member_declaration", "size_provider",
                           "write_window"})  # 쓰기 지점은 그 쓰기 줄이 실린 이유라 축약하지 않는다(리뷰 2026-10-10)
# 해제 계열 호출 이름 — 해제 상대 지점(`release_site`)을 찾는다
# `put`은 낱말 경계로만(`kref_put`·`put_device`) — `output`·`fputs`·`compute`가 해제로 잡혔다(리뷰 2026-10-10)
# 해제 낱말 뒤에 소문자가 이어지면 다른 낱말이다(`freerdp_*`·`freeze`·`deleted`·`released`) — 대문자·`_`·끝·`p`
# (`av_freep`)만(검수 2026-10-10 r8: FreeRDP 해제 없는 함수가 해제 지점으로 골라졌다)
RELEASE_CALL = re.compile(r"\w*(?:[Ff]ree|FREE|[Rr]elease|RELEASE|[Dd]estroy|DESTROY|[Uu]nref|UNREF|[Dd]ecref|DECREF"
                          r"|[Dd]ispose|DISPOSE|[Dd]elete|DELETE)(?:p(?![a-z]))?(?![a-z])\w*"
                          r"|\w*kref_put\w*|(?:\w*_)?put_(?:device|page|ref)\w*|\w*_put_ref\w*"
                          # 할당자 범위 해제(wireshark `wmem_leave_scope`) — 그 범위의 모든 할당이 풀린다(검수 r10)
                          r"|\w*leave_\w*scope\w*|\w*pool_(?:destroy|clear|reset)\w*")
# 해제 상대 지점을 켜는 취약점 분류 — 해제 후 사용·이중 해제·누수·경쟁 조건(검수 2026-10-10 r7: 경계 오류 gpac에
# 무관한 해제 지점 4곳이 붙었다)
RELEASE_CWES = frozenset({"CWE-416", "CWE-415", "CWE-401", "CWE-362", "CWE-911", "CWE-672"})
# 수명 계열 — 객체 변수의 해제 줄·해제 함수 본문까지 싣는다(경쟁 조건 362는 아니다: 대개 파일·권한 경쟁)
LIFETIME_CWES = frozenset({"CWE-416", "CWE-415", "CWE-401", "CWE-911", "CWE-672"})
RELEASE_LINES = 3  # 함수 하나에서 객체 변수를 해제하는 줄 상한


def _bare_arg(text: str) -> str:
    """인자에서 공백·캐스트·`&`·바깥 괄호를 걷어낸 것 — 해제 대상 비교용."""
    text = re.sub(r"\s+", "", text)
    while True:
        # 공백을 지웠으니 캐스트는 `(이름*)` 한 덩어리다 — 낱말을 겹쳐 나누는 정규식은 긴 인자에서 백트래킹이 폭발했다
        # (Espruino 2단계 30분, 2026-10-10 r11)
        stripped = re.sub(r"^\([A-Za-z_]\w*\**\)", "", text)
        stripped = stripped.lstrip("&")
        if stripped.startswith("(") and stripped.endswith(")"):
            stripped = stripped[1:-1]
        if stripped == text:
            return text
        text = stripped
# 보조 블록을 줄일 때 더 남길 줄 — 해제·락·참조 카운트 호출, 필드 대입, NULL 대입(패치가 흔히 고치는 줄)
AUX_EVIDENCE = re.compile(r"(?i)\b\w*(?:lock|unlock|ref|refcnt|refcount)\w*\s*\(|(?:->|\.)\s*\w+(?:\[[^\]]*\])?\s*=(?!=)"
                          r"|=\s*NULL\s*;")
RELEASE_SITES = 3
# 오염 필드 사용(`tainted_use`)을 켜는 취약점 분류 — 범위 밖 읽기·쓰기·정수 오류
WRITE_CWES = frozenset(f"CWE-{n}" for n in (119, 120, 121, 122, 125, 126, 131, 190, 191, 680, 787, 788, 805, 806))
FIELD_SITES = 2  # 필드 하나의 사용 지점 상한
# 할당 호출 이름 — 할당 크기를 정하는 함수(`size_provider`)를 찾는다. `free` 계열은 아니다
ALLOC_CALL = re.compile(r"(?i)(?!\w*free)\w*(?:alloc|new)\w*")
SIZE_PROVIDERS = 3
# 대입의 왼쪽 — 이름 또는 필드 경로의 마지막 이름(`buf = …`·`s->data = …`·`*out = …`)
LHS_FIELD = re.compile(r"\s*(?:[A-Za-z_][\w\s\*]*?[\s\*])?\**\s*(?:[A-Za-z_]\w*\s*(?:->|\.)\s*)*"
                       r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*=(?!=)")
LHS_NAME = re.compile(r"\s*(?:[A-Za-z_][\w\s\*]*?[\s\*])?\**\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*=(?!=)")


def read_symbol_source(root: Path, target: Location) -> str:
    """하네스 `read_symbol_source`와 같은 읽기 — 줄 범위가 없으면 파일 전체."""
    path = root / target.file
    if not path.is_file():
        return ""
    text = path.read_text("utf-8", errors="replace")
    if target.line_start is None:
        return text
    lines = source_lines(text)
    end = target.line_end or target.line_start
    return "\n".join(lines[max(target.line_start - 1, 0) : end])


def _is_short(location: Location) -> bool:
    if location.line_start is None or location.line_end is None:
        return False
    return location.line_end - location.line_start + 1 <= WRAPPER_LINES


def _overlaps(a: Location, b: Location) -> bool:
    if a.file != b.file:
        return False
    if a.line_start is None or b.line_start is None:
        return True
    return a.line_start <= (b.line_end or b.line_start) and b.line_start <= (
        a.line_end or a.line_start
    )


def _spans(location: Location, line: int) -> bool:
    start = location.line_start
    return start is not None and start <= line <= (location.line_end or start)


def _narrowest_symbol(symbols: list[Location], line: int) -> Location | None:
    return min(
        (symbol for symbol in symbols if _spans(symbol, line)),
        key=lambda symbol: (symbol.line_end or symbol.line_start or 0) - (symbol.line_start or 0),
        default=None,
    )


def _block(location: Location, text: str, end: int | None = None) -> str:
    start = location.line_start or 1
    end = end or start + text.count("\n")
    return f"/* ===== {location.file}:{start}-{end} ===== */\n{text}"


def _caller_window(graph: GraphIndex, site: Location, lines: int) -> Location:
    line = site.line_start or 1
    start, end = line - lines, line + lines
    enclosing = _narrowest_symbol(graph.symbols_for(site.file) or [], line)
    if enclosing is not None and enclosing.line_start is not None:
        start = max(start, enclosing.line_start)
        end = min(end, enclosing.line_end or end)
    return Location(file=site.file, symbol=site.symbol, line_start=max(1, start), line_end=end)


def _clip_around(window: Location, line: int, seen: list[Location]) -> Location | None:
    start, end = window.line_start or line, window.line_end or line
    for other in seen:
        if other.file != window.file:
            continue
        if other.line_start is None:
            return None
        other_end = other.line_end or other.line_start
        if other.line_start <= line <= other_end:
            return None
        if other_end < line:
            start = max(start, other_end + 1)
        else:
            end = min(end, other.line_start - 1)
    return Location(file=window.file, symbol=window.symbol, line_start=start, line_end=end)


def _write_sites(
    graph: GraphIndex,
    read_source: Callable[[Location], str],
    lines: list[tuple[str, int]],
    carried: list[Location],
    near_file: str,
    *,
    fields: bool = True,
    file_vars: dict | None = None,
) -> list[Location]:
    own: set[str] = set()
    cpp = near_file.lower().endswith((".cpp", ".cc", ".cxx", ".c++"))
    for function in carried:
        if function.symbol:
            own |= graph.locals_of(function.file, function.symbol) or frozenset()
    found: dict[tuple[str, int], Location] = {}
    for name, is_field in slice_line_refs(read_source, lines):
        if is_field and not fields:
            continue
        if is_field:
            sites = graph.field_writes_of(name, near_file) or []
        elif name in own:
            continue
        elif cpp and file_vars is not None and not any(
                r["file"] == near_file or r["file"].lower().endswith((".h", ".hh", ".hpp", ".hxx"))
                for r in file_vars.get(name) or ()):
            # C++ 파일에서 파일 범위 선언이 없는 이름은 암묵 멤버(`mHandle` = `this->mHandle`)다 — 같은 파일의 모든
            # 대입(`setHandle`·`freeNode`)이 쓰기 지점으로 붙었다(검수 2026-10-10 r9 Android av)
            continue
        else:
            sites = [
                site
                for site in graph.writes_of(name, near_file) or []
                if not (site.symbol and name in (graph.locals_of(site.file, site.symbol) or ()))
            ]
        for site in sites:
            found.setdefault((site.file, site.line_start or 0), site)
    rank: dict[tuple[str, str | None], int] = {}
    order: list[tuple[int, int, Location]] = []
    for position, site in enumerate(found.values()):
        key = (site.file, site.symbol)
        order.append((rank.get(key, 0), position, site))
        rank[key] = rank.get(key, 0) + 1
    return [site for _, _, site in sorted(order, key=lambda item: item[:2])]


def entry_function(graph: GraphIndex, entry_file: str, entry_line: int) -> Location:
    """하네스 `_auditor_target` — entry 줄을 품는 가장 좁은 심볼, 없으면 앞뒤 50줄 창."""
    symbols = graph.symbols_for(entry_file) or []
    enclosing = _narrowest_symbol(symbols, entry_line)
    if enclosing is not None:
        # 그 범위 안에서 다른 함수 정의가 시작하면 인덱서가 함수 경계를 잘못 읽은 것이다(Espruino `jsvar.c:741-3766`,
        # 102K자 — 검수 2026-10-10). entry 줄 앞뒤의 그 정의들 사이로 줄인다
        inner = [s for s in symbols if s is not enclosing and s.line_start is not None
                 and (enclosing.line_start or 0) < s.line_start <= (enclosing.line_end or 0)]
        if inner:
            before = [s.line_end for s in inner if (s.line_end or 0) < entry_line]
            after = [s.line_start for s in inner if s.line_start > entry_line]
            low = max([enclosing.line_start or 1] + [n + 1 for n in before if n])
            high = min([enclosing.line_end or entry_line] + [n - 1 for n in after])
            return Location(file=entry_file, symbol=enclosing.symbol, line_start=low, line_end=high)
        return enclosing
    return Location(
        file=entry_file,
        line_start=max(1, entry_line - WINDOW_LINES),
        line_end=entry_line + WINDOW_LINES,
    )


def _length(location: Location) -> int:
    start = location.line_start or 0
    return (location.line_end or start) - start + 1


def _has_member(code: str) -> bool:
    """줄인 타입 정의(`code_only`)에 머리·닫는 줄·생략 표시 말고 남은 줄이 있나."""
    rows = [r.strip() for r in code.split("\n")]
    opened = False
    for row in rows:
        if not opened:
            opened = "{" in row
            if opened and row[row.index("{") + 1:].strip(" ;}"):
                return True
            continue
        if row and not re.fullmatch(r"\}\s*[A-Za-z_]?[\w\s,*]*;?", row):
            return True
    return False


def _short(symbol: str | None) -> str:
    """색인 이름의 호출 이름 — C++ `Class::method`는 호출 표에 `method`로 있다."""
    return (symbol or "").rsplit("::", 1)[-1]


def _variadic(read_source: Callable[[Location], str], function: Location) -> bool:
    """`function`의 머리가 가변 인자(`...`)를 받고 본문이 `va_start`로 넘기는가 — `va_list` 래퍼."""
    if function.line_start is None:
        return False
    head = code_only(read_source(Location(file=function.file, line_start=function.line_start,
                                          line_end=_header_end(read_source, function))))
    return "..." in head and "va_start" in code_only(read_source(function))


def _header_end(read_source: Callable[[Location], str], function: Location) -> int:
    """함수 머리(시그니처)의 끝 줄 — 시작 줄부터 처음 `{`가 나오는 줄(`HEADER_LINES`줄 안). 없으면 시작 줄.
    5줄로 막았을 때는 파라미터를 한 줄씩 쓰는 머리(held-out libsixel `load_gif` 12줄)가 반환형 줄만 남고
    함수 이름 줄부터 생략됐다."""
    start = function.line_start or 1
    last = min(function.line_end or start, start + HEADER_LINES)
    code = code_only(read_source(Location(file=function.file, line_start=start, line_end=last)))
    for offset, line in enumerate(code.split("\n")):
        if "{" in line:
            return start + offset
    return start


def _case_label(read_source: Callable[[Location], str], function: Location, line: int) -> int | None:
    """`line`을 감싼 가장 가까운 `case X:`·`default:` 레이블 줄(같은 중괄호 깊이, 위로) — 없으면 None."""
    if function.line_start is None or not line:
        return None
    start = function.line_start
    rows = code_only(read_source(function)).split("\n")
    offset = line - start
    if not 0 <= offset < len(rows):
        return None
    depth = 0
    for back in range(offset - 1, -1, -1):
        text = rows[back]
        depth += text.count("}") - text.count("{")
        if depth < 0:
            # 블록을 열고 나가는 줄 — 갈래 블록(`case X: {`)이면 그 레이블, 아니면(`if (…) {`) 바깥에서 계속 찾는다.
            # `switch (…) {`를 넘으면 그 switch의 갈래가 아니다
            if re.match(r"^\s*(?:case\b[^:]*|default\s*):(?!:)", text):
                return start + back
            if re.match(r"^\s*switch\b", text):
                return None
            depth = 0
        if depth == 0 and re.match(r"^\s*(?:case\b[^:]*|default\s*):(?!:)", text):
            return start + back
    return None


def _is_static(read_source: Callable[[Location], str], function: Location) -> bool:
    """함수가 `static`(파일 안 전용)인가 — 머리에서 함수 이름 앞에 `static`이 있는가."""
    if function.line_start is None or not function.symbol:
        return False
    head = code_only(read_source(Location(
        file=function.file, line_start=function.line_start,
        line_end=_header_end(read_source, function),
    )))
    at = re.search(rf"\b{re.escape(function.symbol)}\s*\(", head)
    return bool(STATIC.search(head[: at.start()] if at else head))


HEADER_EXT = (".h", ".hh", ".hpp", ".hxx", ".inc", ".ipp", ".tcc")


_BARE_ARGS: dict[int, dict[str, list[tuple[str, int]]]] = {}


def _bare_args(index: dict) -> dict[str, list[tuple[str, int]]]:
    """호출 인자로 이름 하나만 넘긴 것 — 이름 → [(피호출, 위치)]. 색인마다 한 번(큰 저장소에서 호출할 때마다 전체
    인자를 훑어 Espruino 2단계가 30분을 넘겼다, 2026-10-10 r11)."""
    key = id(index)
    if key not in _BARE_ARGS:
        table: dict[str, list[tuple[str, int]]] = {}
        for callee, rows in index.get("calls", {}).items():
            for row in rows:
                for position, arg in enumerate(row.get("args", [])):
                    text = arg.strip()
                    if re.fullmatch(r"[A-Za-z_]\w*", text) or "(" in text or "&" in text:
                        bare = _bare_arg(text)
                        if re.fullmatch(r"[A-Za-z_]\w*", bare):
                            table.setdefault(bare, []).append((callee, position))
        _BARE_ARGS.clear()
        _BARE_ARGS[key] = table
    return _BARE_ARGS[key]


def _indirect_sites(index: dict, function: Location) -> list[tuple[Location, str]]:
    """직접 호출자가 없는 함수 `function`을 함수 포인터로 부르는 곳 — [(호출 지점, 호출에 쓴 이름)].

    등록 호출(`register(ctxt, "name", F)`)에서 F를 받는 파라미터의 typedef T를 찾고(하나뿐일 때만), T 타입의
    지역·파라미터 변수 v로 부르는 호출(`v(…)`, 인자 수가 F의 파라미터 수와 같다)과, F를 묶는 필드(`fn_bindings`)로
    부르는 필드 호출을 돌려준다. 호출자 사슬이 콜백으로 등록된 함수에서 끊겨 디스패처의 분기(libxml2
    `xmlXPathCompOpEval`의 `func(ctxt, op->value)`)가 안 보였다(검토 2026-10-10 r11)."""
    name = _short(function.symbol)
    functions = index.get("functions", {})
    rows_f = [r for r in functions.get(function.symbol or "", []) + functions.get(name, [])
              if r.get("file") == function.file]
    if not rows_f:
        return []
    arity = len(rows_f[0].get("params") or [])
    var_types = index.get("var_types", {})
    typedefs: set[str] = set()
    for callee, position in _bare_args(index).get(name, ()):
        params = next((r.get("params") or [] for r in functions.get(callee, [])), [])
        if position < len(params):
            typedefs |= {t["name"] for r in var_types.get(params[position], [])
                         if r.get("func") == callee and r.get("param")
                         for t in r.get("types", []) if t.get("kind") == "type"}
    found: list[tuple[Location, str]] = []
    if len(typedefs) == 1:
        (typedef,) = typedefs
        holders = {(r.get("func"), variable) for variable, rows in var_types.items() for r in rows
                   if r.get("func") and any(t.get("name") == typedef for t in r.get("types", []))}
        for func, variable in sorted(holders):
            for row in index.get("calls", {}).get(variable, []):
                if row.get("caller") == func and len(row.get("args", [])) == arity \
                        and not is_nonproduct_path(row["file"]):
                    found.append((Location(file=row["file"], symbol=func, line_start=row["line"],
                                           line_end=row["line"]), variable))
    # 구조체 필드로 묶인 호출(`ops->open(…)` — 연산 표)은 따르지 않는다: 같은 필드로 부르는 디스패처가 모든 연산에
    # 공통이라 neomutt mailbox 연산 사슬 200줄이 붙었다(2026-10-10 r12)
    unique: dict[tuple[str, int], tuple[Location, str]] = {}
    for site, called in found:
        unique.setdefault((site.file, site.line_start or 0), (site, called))
    return list(unique.values())


class _Visible:
    """그래프 조회에서 다른 파일의 `static` 함수(헤더 밖)를 뺀다 — 그 파일 밖에서는 부를 수 없다. 이름이 저장소에
    하나뿐이라는 규칙이 다른 프로그램 파일의 `static` 함수를 골랐다(검토 2026-10-10 r11: mariadb `my_time` →
    tokudb xz `message.c`, `gettimeofday` → `client/mysqlslap.c`)."""

    def __init__(self, graph: GraphIndex, read_source: Callable[[Location], str]) -> None:
        self._graph = graph
        self._read = read_source
        self._static: dict[tuple[str, int | None], bool] = {}

    def __getattr__(self, name: str):
        return getattr(self._graph, name)

    def _keep(self, location: Location, near_file: str) -> bool:
        if location.file == near_file or location.file.lower().endswith(HEADER_EXT):
            return True
        key = (location.file, location.line_start)
        if key not in self._static:
            self._static[key] = _is_static(self._read, location)
        return not self._static[key]

    def functions_named(self, names, near_file, **options):
        found = self._graph.functions_named(names, near_file, **options)
        return None if found is None else [f for f in found if self._keep(f, near_file)]

    def callees_of(self, target, **options):
        found = self._graph.callees_of(target, **options)
        return None if found is None else [f for f in found if self._keep(f, target.file)]


def _site_blocks(
    graph: GraphIndex,
    read_source: Callable[[Location], str],
    site: Location,
    lines: int,
) -> tuple[list[Location], Location | None]:
    """호출·쓰기 지점 하나의 블록과 그것을 감싼 함수. 함수가 `SMALL_FUNCTION`줄 이하면 함수 전체 —
    앞뒤 N줄 창은 짧은 함수를 머리 없는 조각(`{`로 시작)으로 잘랐다. 길면 창이고, 창이 머리를
    못 품으면 머리 줄을 따로 붙인다(어느 함수의 조각인지 보이게)."""
    line = site.line_start or 1
    enclosing = _narrowest_symbol(graph.symbols_for(site.file) or [], line)
    if enclosing is not None and enclosing.line_start is not None:
        if _length(enclosing) <= SMALL_FUNCTION:
            return [enclosing], enclosing
    window = _caller_window(graph, site, lines)
    if enclosing is None or enclosing.line_start is None:
        return [window], enclosing
    out = [window]
    if (window.line_start or 0) > enclosing.line_start:
        header_end = min(_header_end(read_source, enclosing), (window.line_start or 1) - 1)
        out.insert(0, Location(file=site.file, symbol=enclosing.symbol,
                               line_start=enclosing.line_start, line_end=header_end))
    return out, enclosing


def _balanced(read_source: Callable[[Location], str], function: Location, keep: set[int],
              heads: bool = True) -> set[int]:
    """괄호 짝(`_brace_pairs`)과 중괄호 없는 제어문의 본문(`_close_braceless`)을 고정점까지 — 머리만 남고 한
    문장 본문이 생략되면 다음 문장이 그 본문으로 읽혔다(검수 2026-10-10 r8: `if (out->close) /* … */ if (out->drop)`,
    mupdf `if (ctx->locks.lock != fz_lock_default)` 뒤 생략)."""
    out = _brace_pairs(read_source, function, keep, heads)
    if not heads or function.line_start is None:
        return out
    rows = code_only(read_source(function)).split("\n")
    for _round in range(STATEMENT_LINES):
        more = _close_braceless(rows, function.line_start, out)
        if more <= out:
            break
        out = _brace_pairs(read_source, function, out | more, heads)
    return out


BARE_HEAD = re.compile(r"^\s*(?:\}\s*)?(?:else|do)\s*$")
IF_HEAD = re.compile(r"^\s*(?:\}\s*)?(?:else\s+)?if\s*\(")


def _close_braceless(rows: list[str], start: int, keep: set[int]) -> set[int]:
    """`keep`의 제어문 머리(`if (…)`·`for (…)`·`while (…)`·`else`·`do`) 뒤가 비어 있으면 그 본문 — 다음 코드 줄이
    `{` 혼자면 그 줄(짝 `}`는 `_brace_pairs`가), 아니면 한 문장(첫 `;`까지, `STATEMENT_LINES`줄). 남긴 `else`의 앞
    `if`가 중괄호 없이 끝났으면 그 문장과 머리도. `rows`는 함수의 `code_only` 줄들."""
    end = start + len(rows) - 1
    padded = [""] * (start - 1) + rows

    def text(number: int) -> str:
        return rows[number - start] if start <= number <= end else ""

    def next_code(number: int) -> int | None:
        for k in range(number + 1, end + 1):
            if text(k).strip():
                return k
        return None

    def statement(first: int) -> set[int]:
        span, depth = set(), 0
        for k in range(first, min(end, first + STATEMENT_LINES) + 1):
            span.add(k)
            depth += text(k).count("(") - text(k).count(")")
            if depth <= 0 and (";" in text(k) or text(k).rstrip().endswith("}")):
                break
        return span

    out: set[int] = set()
    for number in sorted(keep):
        row = text(number)
        last = None
        if GUARD_HEAD.match(row):
            last, _cond = _condition_span(padded, number)
            if not text(last).rstrip().endswith(")"):
                last = None  # 머리 줄에 본문(`{`·문장)이 이어진다
        elif BARE_HEAD.match(row):
            last = number
        if last is not None:
            body = next_code(last)
            if body is not None and body not in keep:
                out |= {body} if text(body).strip().startswith("{") else statement(body)
        # 남긴 `else` 앞의 `if`가 중괄호 없이 끝났으면 그 한 문장과 머리
        if re.match(r"^\s*else\b", row):
            back = number - 1
            while back >= start and not text(back).strip():
                back -= 1
            if back >= start and not text(back).rstrip().endswith("}"):
                for k in range(back, max(start, back - 2 * STATEMENT_LINES) - 1, -1):
                    out.add(k)
                    if IF_HEAD.match(text(k)):
                        break
    return out - keep


def _brace_pairs(read_source: Callable[[Location], str], function: Location, keep: set[int],
                 heads: bool = True) -> set[int]:
    """`keep`에 괄호 짝 줄을 더한다 — 남긴 줄이 연 `{`의 닫는 `}` 줄, 남긴 줄이 닫는 `}`의 여는 줄.

    `if (…) {`만 남고 짝 `}`가 생략 표시로 사라져 조각 블록의 괄호 수가 안 맞았다(jhead
    `ReadJpegSections` `{` 8 / `}` 1) — 사람은 `process_COM(Data, itemlen)`이 어느 `case` 안인지
    못 읽고, 파서·모델에는 깨진 C였다. 짝 줄을 더하면 다시 짝이 필요할 수 있어 고정점까지."""
    start = function.line_start or 1
    code = code_only(read_source(function)).split("\n")
    # `#if`의 첫 분기만 중괄호를 센다 — 분기마다 같은 머리(`if (…) {`)가 있으면 짝이 어긋나 함수 뒷부분 전체가
    # 엉뚱한 블록 안으로 잡혔다
    braces: list[str] = []
    pp: list[str] = []
    for line in code:
        directive = re.match(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b", line)
        if directive:
            word = directive.group(1)
            if word.startswith("if"):
                pp.append("first")
            elif word in ("elif", "else") and pp:
                pp[-1] = "skip"
            elif word == "endif" and pp:
                pp.pop()
            braces.append("")
            continue
        braces.append("" if "skip" in pp else "".join(ch for ch in line if ch in "{}"))
    partner: dict[int, set[int]] = {}
    stack: list[int] = []
    for offset, chars in enumerate(braces):
        number = start + offset
        for char in chars:
            if char == "{":
                stack.append(number)
            elif char == "}" and stack:
                opener = stack.pop()
                if opener != number:
                    partner.setdefault(opener, set()).add(number)
                    partner.setdefault(number, set()).add(opener)
    # 남긴 줄을 감싸는 블록의 머리(`for (…){`·`} else {`·`do {`)와 `switch` 안의 `case`·`default:` 레이블 —
    # 가드로 고르지 않은 제어문 머리가 빠져 본문만 남으면 구조가 깨져 보였다(검수 v34: jhead `for (a=0;;a++){`가
    # 빠지고 `marker = fgetc`/`break`만, picotcp `if (!zerocopy) {`/`else`가 빠져 두 대입이 잇달아 보임)
    blocks = [(opener, closer) for opener, closers in partner.items() for closer in closers if closer > opener]
    depth_at: list[int] = []
    depth = 0
    for chars in braces:
        depth_at.append(depth)
        depth += chars.count("{") - chars.count("}")
    label_re = re.compile(r"^\s*(?:case\b[^:]*|default\s*):(?!:)")

    def case_label(number: int) -> int | None:
        # 같은 깊이에서 위로 가장 가까운 레이블 — 더 얕은 줄(블록 밖)을 만나면 멈춘다
        offset = number - start
        if label_re.match(code[offset]) or code[offset].strip().startswith("}"):
            return None  # 레이블 자신·닫는 줄(`switch`의 `}`) — 앞 case로 번지지 않게
        for back in range(offset - 1, -1, -1):
            if depth_at[back] < depth_at[offset]:
                return None
            if depth_at[back] == depth_at[offset] and label_re.match(code[back]):
                return start + back
        return None

    def head_of(opener: int) -> set[int]:
        # 여는 줄 앞부분의 괄호가 덜 열렸으면(여러 줄 조건 `if (a &&\n b) {`·`for (i = 0;\n i < n;\n i++)`)
        # 괄호가 맞을 때까지, `{` 혼자인 줄이면 바로 위 문장 머리까지 위로
        heads = {opener}
        offset = opener - start
        if not 0 <= offset < len(code):
            return heads
        line = code[offset]
        part = line[: line.rfind("{")] if "{" in line else line
        balance = part.count("(") - part.count(")")
        lone = not part.strip()
        for back in range(offset - 1, max(-1, offset - 1 - 2 * STATEMENT_LINES), -1):
            if balance >= 0 and not lone:
                break
            text = code[back].strip()
            if text.startswith("#"):
                break
            if balance >= 0 and (not text or text.endswith((";", "{", "}", ":"))):
                break  # `{` 혼자인 줄 위가 이미 끝난 문장
            heads.add(start + back)
            balance += code[back].count("(") - code[back].count(")")
            lone = False
        return heads

    out = set(keep)
    todo = list(out)
    while todo:
        number = todo.pop()
        extra = set(partner.get(number, ()))
        if not heads:
            for other in extra:
                if other not in out:
                    out.add(other)
                    todo.append(other)
            continue
        for opener, closer in blocks:
            if opener < number < closer:
                extra |= head_of(opener)
        offset = number - start
        if 0 <= offset < len(code) and depth_at[offset] > 1:
            label = case_label(number)
            if label is not None:
                extra.add(label)
        # 따로 떨어진 `else` 줄은 바로 위 `}`(앞 `if`의 닫는 줄 — 짝으로 그 머리까지)와 함께
        if 0 <= offset < len(code) and re.match(r"^\s*else\b", code[offset]):
            for back in range(offset - 1, -1, -1):
                if code[back].strip():
                    if code[back].strip().endswith("}"):
                        extra.add(start + back)
                    break
        for other in extra:
            if other not in out:
                out.add(other)
                todo.append(other)
    return out


def _param_names(read_source: Callable[[Location], str], function: Location) -> list[str]:
    """함수 머리의 파라미터 이름 — 위치 순서(1부터의 인자 번호와 같은 순서)."""
    code = code_only(read_source(function))
    head = code[: code.find("{")] if "{" in code else code
    if "(" not in head:
        return []
    names = []
    for param in _depth0_split(head[head.find("(") + 1: head.rfind(")")]):
        found = re.findall(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$", param.strip())
        names.append(found[0] if found and found[0] != "void" else "")
    return names


def _tainted_lines(read_source: Callable[[Location], str], function: Location,
                   params: set[str]) -> set[int]:
    """`params`(오염된 인자를 받는 파라미터)가 흐르는 줄 — 어휘 전파: 그 이름이 나오는 줄을 남기고,
    오른쪽에 나오는 대입의 왼쪽 이름(`resp = (struct ntlmv2_resp *)buf` → resp)도 이름에 더한다(두 바퀴)."""
    start = function.line_start or 1
    rows = code_only(read_source(function)).split("\n")
    names = set(params) - {""}
    keep: set[int] = set()
    for _round in range(2):
        for offset, row in enumerate(rows):
            used = set(IDENT_RE.findall(row))
            if not used & names:
                continue
            keep.add(start + offset)
            assign = re.match(r"\s*(?:[A-Za-z_][\w\s\*]*?[\s\*])?\**\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*=(?!=)(.*)", row)
            if assign and set(IDENT_RE.findall(assign.group(2))) & names:
                names.add(assign.group(1))
    return keep


FIELD_OF = re.compile(r"(?:->|\.)\s*([A-Za-z_]\w*)")
# 레이블만 있는 줄 — 구조로 싣지만 정의 시드에서는 뺀다(`case PB_LTYPE_BOOL:`의 `#define` 블록이 붙었다)
CASE_LABEL = re.compile(r"^\s*case\b[^:]*:(?!:)\s*$")
GUARD_HEAD = re.compile(r"^\s*(?:\}\s*)?(?:else\s+)?(if|while|for)\s*\(")
EXIT_STMT = re.compile(r"^\s*(?:return\b|goto\s+[A-Za-z_]\w*|break\s*;|continue\s*;|(?:exit|abort|_exit)\s*\()")
# 가드 본문의 처리 문장 — 탈출, 또는 호출 한 문장(오류 처리: jhead `ErrFatal("…")`, `ERROR(…)`). v32는
# 탈출만 실어 jhead `if (Data == NULL){ … }`의 본문이 생략 표시에 가려져 검사가 무엇을 하는지 안 보였다
# 호출은 이름이 오류 처리로 보일 때만 — 상태를 바꾸는 호출(libsixel `gif_parse_colortable(s, …)`)은 v33에서
# 가드 본문으로 실려 그 함수 본문까지 붙었다
CALL_HEAD = re.compile(r"^\s*([A-Za-z_]\w*)\s*\(")
# 상수를 넣는 대입 — 대문자 이름·수·문자·NULL(음수·캐스트 포함)
CONST_STORE = re.compile(r"^[A-Za-z_][\w.>\-\[\]]*\s*=\s*(?:\([^()]*\)\s*)?-?\s*(?:[A-Z_][A-Z0-9_]*|\d\w*|'[^']*'|NULL)\s*;")
ERRORISH = re.compile(r"(?i)err|fail|fatal|abort|die|panic|throw|raise|warn|bail|invalid|overflow|assert|exit")


def _handler(text: str) -> bool:
    text = text.strip().lstrip("{").strip()
    if not text or text.startswith("}"):
        return False
    if EXIT_STMT.match(text):
        return True
    call = CALL_HEAD.match(text)
    return bool(call and ERRORISH.search(call.group(1)))


def _guard_lines(read_source: Callable[[Location], str], function: Location, names: set[str],
                 limit: int, before: int | None = None) -> set[int]:
    """`names`(데이터 흐름 변수)를 검사하는 조건 줄과 그 조기 탈출 — 일반 규칙(정답 데이터를 안 쓴다).

    - 조건: `if`/`while`/`for`의 괄호 안에 `names` 중 하나가 나오는 문장 — 여러 줄 조건은 괄호가 닫히는
      줄까지 전부. 앞에서부터 `limit`개(`before`가 있으면 그 줄 앞의 것만).
    - 조기 탈출: 조건 바로 뒤가 중괄호 없는 `return`/`goto`/`break`/`continue`/`exit(…)` 한 문장이거나,
      `{ … }` 안이 두 문장 이하이고 탈출을 품으면 그 줄들. `goto L`이면 같은 함수의 `L:` 줄도.
    40줄 넘는 함수를 데이터 흐름 줄만 실을 때 가드가 사라져, 패치 샘플에서는 수정 자체(추가된 검사)가
    생략 표시에 가려졌다(검수 2026-10-05, patch_omit_audit: 299건, 그중 판정을 바꾼 출력 9개)."""
    start = function.line_start or 1
    rows = code_only(read_source(function)).split("\n")
    end = start + len(rows) - 1
    params = frozenset(_param_names(read_source, function))
    # 후보를 다 모은 뒤 흐름 토큰과 바로 맞는 가드를 먼저(주소를 넘긴 지역의 필드로만 맞는 가드는 뒤) —
    # 위에서부터 `limit`개를 채우면 앞쪽의 약한 가드가 뒤의 실제 검사를 밀어냈다(v38 ipmitool `ipmi_csum` 검사)
    candidates = []
    number = start
    while number <= end:
        if before is not None and number >= before:
            break
        if not GUARD_HEAD.match(rows[number - start]):
            number += 1
            continue
        last, cond, body, handled = _guard_unit(rows, start, end, number)
        tokens = _flow_tokens(cond)
        if handled and _flow_match(tokens, names, params):
            strong = _flow_match(tokens, {n for n in names if not n.startswith("&")})
            candidates.append((0 if strong else 1, number, last, body))
        number = last + 1
    keep: set[int] = set()
    labels: set[str] = set()
    for _rank, number, last, body in sorted(candidates)[:limit]:
        keep |= set(range(number, last + 1)) | set(body)
        for k in [last, *body]:
            labels |= set(re.findall(r"\bgoto\s+([A-Za-z_]\w*)", rows[k - start]))
    for label in labels:
        for k in range(start, end + 1):
            if re.match(rf"^\s*{re.escape(label)}\s*:(?!:)", rows[k - start]):
                keep.add(k)
                break
    return keep


def _handled_guard_units(read_source: Callable[[Location], str], function: Location, lines: set[int]) -> set[int]:
    """`lines` 안의 if/while/for 중 처리 본문(탈출·오류 처리)이 있는 것 — 조건 줄 전부, 본문, `goto` 레이블."""
    if function.line_start is None:
        return set()
    start = function.line_start
    rows = code_only(read_source(function)).split("\n")
    end = start + len(rows) - 1
    out: set[int] = set()
    labels: set[str] = set()
    for number in sorted(lines):
        if start <= number <= end and GUARD_HEAD.match(rows[number - start]):
            last, _cond, body, handled = _guard_unit(rows, start, end, number)
            if handled:
                out |= set(range(number, last + 1)) | set(body)
                for k in [last, *body]:
                    labels |= set(re.findall(r"\bgoto\s+([A-Za-z_]\w*)", rows[k - start]))
    for label in labels:
        for k in range(start, end + 1):
            if re.match(rf"^\s*{re.escape(label)}\s*:(?!:)", rows[k - start]):
                out.add(k)
                break
    return out


def _condition_lines(read_source: Callable[[Location], str], function: Location, lines: set[int]) -> set[int]:
    """`lines` 중 if/while/for 머리의 조건 줄 — 괄호가 닫히는 줄까지(본문은 아니다)."""
    if function.line_start is None:
        return set()
    start = function.line_start
    rows = code_only(read_source(function)).split("\n")
    end = start + len(rows) - 1
    out: set[int] = set()
    for number in sorted(lines):
        if start <= number <= end and GUARD_HEAD.match(rows[number - start]):
            last, _cond, _body, _handled = _guard_unit(rows, start, end, number)
            out |= set(range(number, last + 1))
    return out


def _complete_guards(read_source: Callable[[Location], str], function: Location, keep: set[int]) -> set[int]:
    """이미 실린 조건 줄(`if (!rsp) {` — 데이터 의존이라 실린 검사)의 처리 본문(탈출·오류 처리)을 더한다
    (개선안 G1). 조건만 남고 `return -1;`이 생략 표시에 가려져 검사가 무엇을 하는지 안 보였다(v38 ipmitool)."""
    start = function.line_start or 1
    rows = code_only(read_source(function)).split("\n")
    end = start + len(rows) - 1
    out = set(keep)
    labels: set[str] = set()
    for number in sorted(keep):
        if not (start <= number <= end) or not GUARD_HEAD.match(rows[number - start]):
            continue
        last, _cond, body, handled = _guard_unit(rows, start, end, number)
        if handled:
            out |= set(range(number, last + 1)) | set(body)
            for k in [last, *body]:
                labels |= set(re.findall(r"\bgoto\s+([A-Za-z_]\w*)", rows[k - start]))
    for label in labels:
        for k in range(start, end + 1):
            if re.match(rf"^\s*{re.escape(label)}\s*:(?!:)", rows[k - start]):
                out.add(k)
                break
    return out


def _guard_unit(rows: list[str], start: int, end: int, number: int) -> tuple[int, str, list[int], bool]:
    """`number`에서 시작하는 if/while/for 조건 — (조건 끝 줄, 조건 문자열, 처리 본문 줄들, 처리 본문인가).

    처리 본문: 같은 줄의 탈출·오류 호출, `{ … }` 안이 두 문장 이하이고 탈출을 품거나 모두 오류 호출, 또는
    마지막이 탈출인 네 문장 이하의 정리 호출, 또는 중괄호 없는 오류 처리 한 문장."""
    cond, depth, last, opened = "", 0, number, False
    for k in range(number, min(end, number + STATEMENT_LINES) + 1):
        text = rows[k - start]
        for ch in text[text.find("(") if k == number else 0:]:
            cond += ch
            if ch == "(":
                depth, opened = depth + 1, True
            elif ch == ")":
                depth -= 1
        last = k
        if opened and depth <= 0:
            break
    tail = rows[last - start]
    after = tail[tail.rfind(")") + 1:] if ")" in tail else ""
    body: list[int] = []
    handled = False
    if _handler(after):
        handled = True  # 같은 줄의 탈출·오류 처리
    elif after.strip().startswith("{") or (last + 1 <= end and rows[last + 1 - start].strip().startswith("{")):
        first = last if after.strip().startswith("{") else last + 1
        inner, depth2 = [], 0
        for k in range(first, min(end, first + 8) + 1):
            text = after if k == last else rows[k - start]
            depth2 += text.count("{") - text.count("}")
            inner.append(k)
            if depth2 <= 0:
                break
        texts = [(after if k == last else rows[k - start]) for k in inner]
        # 문장 머리(앞 내용 줄이 `;`·`{`로 끝난 줄)가 모두 처리 문장이어야 — 대입이 섞이면 흐름 줄이다
        content = [t.strip().strip("{}").strip() for t in texts]
        content = [t for t in content if t]
        heads = [t for i, t in enumerate(content) if i == 0 or content[i - 1].endswith((";", "{"))]
        statements = sum(t.count(";") for t in texts)
        # 정리 후 탈출(`_XEatDataWords(…); UnlockDisplay(…); SyncHandle(); return 0;`) — 마지막이 탈출이면
        # 네 문장까지. v34는 두 문장 상한에 걸려 40fc6305 F의 `rep.length` 검사가 다시 가려졌다
        # 정리 문장은 호출만(대입이 섞이면 흐름·성공 경로), `else`가 끼면 if/else 전체라 거른다(v36 리뷰)
        # 상수를 넣는 오류 표시 대입(`ctxt->error = XPATH_INVALID_OPERAND;`·`ret = -1;`)도 정리 문장이다 — 그런
        # 검사(`if (ctxt->valueNr < op->value) { 오류 출력; 오류 표시; return; }`)가 처리 가드로 안 잡혔다(r11)
        cleanup = (bool(heads) and statements <= 4 and EXIT_STMT.match(heads[-1])
                   and all((CALL_HEAD.match(t) and not re.match(r"^(?:if|for|while|switch|return)\b", t))
                           or CONST_STORE.match(t) for t in heads[:-1]))
        if depth2 <= 0 and heads and not any(re.match(r"^\}?\s*else\b", t) for t in content) and (
                (statements <= 2 and (any(EXIT_STMT.match(t) for t in heads) or all(_handler(t) for t in heads)))
                or cleanup):
            body, handled = [k for k in inner if k != last], True
    elif last + 1 <= end and _handler(rows[last + 1 - start]):
        # 중괄호 없는 한 문장 — `;`까지(여러 줄 호출)
        handled = True
        for k in range(last + 1, min(end, last + STATEMENT_LINES) + 1):
            body.append(k)
            if ";" in rows[k - start]:
                break
    # 처리 문장이 없는 조건(본문이 일반 흐름)은 가드로 싣지 않는다 — 본문이 생략 표시로 가려진 `if (…) {`·
    # `for (…)`만 남아 노이즈였다(v33 libsixel `if (code == clear) {`·WavPack `if (wpmd->byte_length == 16) {`).
    # 그 조건이 실린 줄을 감싸면 `_balanced`가 따로 싣는다
    return last, cond, body, handled


CHAIN_RE = re.compile(r"[A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)+")
NOT_FLOW = CALL_KEYWORDS | {"NULL", "sizeof", "return", "int", "char", "unsigned", "void", "const", "struct",
                            "size_t", "long", "short", "static"}


def _flow_tokens(text: str) -> set[str]:
    """데이터 흐름 토큰 — 접근 경로 전체(`wpmd->byte_length`)와 경로에 안 든 변수 이름(`bytecnt`).

    경로의 밑(`wps`·`conn`)만으로 맞추면 문맥 포인터의 다른 필드 검사(WavPack `wps->wphdr.flags`)가
    가드로 실려 v34 출력이 16% 늘었다. 호출 이름(`name(`)·키워드는 뺀다."""
    chains = {re.sub(r"\s+", "", c) for c in CHAIN_RE.findall(text)}
    rest = CHAIN_RE.sub(" ", text)
    calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", text))
    # 주소를 넘긴 변수(`&rep`) — 그 필드 검사(`rep.length`)를 흐름으로 본다(`_flow_match`)
    addressed = {"&" + name for name in re.findall(r"&\s*([A-Za-z_]\w*)(?!\s*(?:->|\.|\[|\w))", text)}
    return chains | addressed | (set(IDENT_RE.findall(rest)) - calls - NOT_FLOW)


def _flow_match(cond: set[str], flow: set[str], params: frozenset[str] = frozenset()) -> bool:
    """조건 토큰이 흐름 토큰과 같거나, 조건의 경로가 흐름 경로의 앞부분(`wpmd->data` ⊂ `wpmd->data->x`),
    또는 조건의 경로가 **주소를 넘긴 지역** 변수의 필드(`rep.length` — 흐름 줄에 `&rep`). 파라미터로 받은
    문맥 포인터(`wps`·`conn`)와 그 지역 별칭(`wps = wpc->streams[0]`)의 필드는 계속 뺀다
    (검수 v34: 40fc6305 F `if (rep.length > INT_MAX >> 2)`; v36 리뷰: 별칭 필드가 가드 슬롯을 먹었다)."""
    if cond & flow:
        return True
    if any(f.startswith(c) and f[len(c):len(c) + 1] in ("-", ".") for c in cond for f in flow):
        return True
    return any(("&" + re.match(r"[A-Za-z_]\w*", c).group(0)) in flow
               and re.match(r"[A-Za-z_]\w*", c).group(0) not in params
               for c in cond if CHAIN_RE.fullmatch(c))


def _flow_names(read_source: Callable[[Location], str], file: str, lines: set[int]) -> set[str]:
    """그 줄들의 데이터 흐름 토큰(`_flow_tokens`) — 가드를 고를 때 쓴다."""
    names: set[str] = set()
    for n in lines:
        names |= _flow_tokens(code_only(read_source(Location(file=file, line_start=n, line_end=n))))
    return names


def _field_lines(read_source: Callable[[Location], str], function: Location,
                 fields: set[str]) -> set[int]:
    """`function`에서 `fields`(`->f`·`.f`)를 다루는 줄과, 그 줄에서 값을 받은 변수가 쓰이는 줄
    (`portptr = strrchr(conn->host.name, ':')` → `*portptr = '\\0'`)."""
    start = function.line_start or 1
    rows = code_only(read_source(function)).split("\n")
    seed = {start + i for i, row in enumerate(rows) if set(FIELD_OF.findall(row)) & fields}
    derived = set()
    for number in seed:
        assign = re.match(r"\s*(?:[A-Za-z_][\w\s\*]*?[\s\*])?\**\s*([A-Za-z_]\w*)\s*=(?!=)",
                          rows[number - start])
        if assign:
            derived.add(assign.group(1))
    return seed | (_tainted_lines(read_source, function, derived) if derived else set())


def _writes_field(body: str, field: str) -> bool:
    """`body`(주석 뺀 코드)가 `->field`·`.field`(그 아래 경로 포함)에 쓰는가 — 대입·복합 대입·`++`/`--`,
    또는 쓰기 호출(`DANGEROUS_CALL`·`*scanf`)의 인자로 넘김(`sscanf(url, "…", conn->host.name, path)`)."""
    f = re.escape(field)
    path = rf"(?:->|\.)\s*{f}\b(?:\s*(?:->|\.)\s*\w+|\s*\[[^\]]*\])*"
    if re.search(path + r"\s*(?:[+\-*/%|&^]?=(?!=)|\+\+|--)", body):
        return True
    if re.search(rf"(?:\+\+|--)\s*\w+{path}", body):
        return True
    return re.search(
        rf"\b(?:{DANGEROUS_CALL.pattern}|\w*scanf)\s*\([^;]*?(?:->|\.)\s*{f}\b", body
    ) is not None


def _field_fillers(index: dict, graph: GraphIndex, read_source: Callable[[Location], str],
                   called: str, site: Location, enclosing: Location) -> list[tuple[int, Location, set[str]]]:
    """호출 지점 앞, 같은 함수에서 **호출 인자의 필드를 채우는 호출** — (줄, 그 함수, 필드들).

    `fix_hostname(data, conn, &conn->host)`의 `&conn->host` → 그 앞의 `parse_remote_port(data, conn)`·
    `parseurlandfillconn(data, conn, …)`처럼 `conn`을 통째로 받아 본문에서 `->host`를 다루는 호출.
    값이 구조체 필드를 거쳐 오면 인자 변수의 대입·주소 전달 규칙(`_provenance`)으로는 안 보였다(held-out
    curl forward: 빈 hostname이 되는 경로)."""
    line = site.line_start or 0
    pairs: set[tuple[str, str]] = set()
    for row in index.get("calls", {}).get(called, []):
        if row["file"] == site.file and row["line"] == line:
            for arg in row.get("args", []):
                pairs |= set(re.findall(r"([A-Za-z_]\w*)\s*(?:->|\.)\s*([A-Za-z_]\w*)", code_only(arg)))
    if not pairs:
        return []
    found = []
    for callee, rows in index.get("calls", {}).items():
        if callee == called:
            continue
        for row in rows:
            if row["file"] != site.file or row.get("caller") != enclosing.symbol:
                continue
            if not (enclosing.line_start or 0) <= row["line"] < line:
                continue
            args = [code_only(a).strip() for a in row.get("args", [])]
            fields = {f for base, f in pairs if any(re.fullmatch(rf"&?\s*{re.escape(base)}", a)
                                                       for a in args)}
            if not fields:
                continue
            for function in graph.functions_named([callee], site.file) or []:
                body = code_only(read_source(function))
                # 그 필드에 **쓰는** 함수만 — 읽기만 하는 함수(curl `ConnectionExists`·`detect_proxy`·
                # `reuse_conn`)까지 붙어 forward가 29K → 39K로 늘었다(v29)
                used = {f for f in fields if _writes_field(body, f)}
                if used:
                    found.append((row["line"], function, used))
    return found


def _statement_starts(read_source: Callable[[Location], str], function: Location,
                      keep: set[int]) -> set[int]:
    """`keep`의 줄이 여러 줄 문장의 뒷줄이면 문장 시작 줄까지 더한다 — 앞 줄이 `;`·`{`·`}`·`:`로 끝나지
    않으면 이어지는 문장이다(최대 `STATEMENT_LINES`줄 위로). 골라 실은 블록에서 `conn->host.name, path)) {`
    처럼 문장 끝만 남아 읽을 수 없었다(held-out curl v27)."""
    start = function.line_start or 1
    rows = code_only(read_source(function)).split("\n")
    out = set(keep)
    for number in sorted(keep):
        current = number
        for _step in range(STATEMENT_LINES):
            previous = current - 1
            if previous < start or previous - start >= len(rows):
                break
            text = rows[previous - start].strip()
            if not text or text.endswith((";", "{", "}", ":")) or text.startswith("#"):
                break
            out.add(previous)
            current = previous
    return out


SWITCH_DIRECTIVE = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)$")
SINGLE_SWITCH = re.compile(r"^\s*!?\s*(?:defined\s*\(\s*([A-Za-z_]\w*)\s*\)|defined\s+([A-Za-z_]\w*))\s*$")
# 죽은 구역으로 다룰 스위치 이름 — 디버그·추적·시험용만. 저장소에 정의가 없다는 것만으로는 모른다: 컴파일러·
# 빌드 시스템이 정하는 이름(`__linux__`·`HAVE_*`·`CONFIG_*`·`_WIN32`)이 그 꼴이다(리뷰 2026-10-10)
DEBUG_SWITCH = re.compile(r"(?i)(?:^|_)(?:DEBUG|DBG|TRACE|TRACING|TEST|TESTING|VERBOSE|DUMP|PROFILE|PROFILING"
                          r"|STATS|LOGGING|PARANOID|MEMCHECK|LEAK_CHECK)(?:_|$)")
SYSTEM_SWITCH = re.compile(r"^_|^(?:HAVE|CONFIG|USE|ENABLE|WITH|HAS|BUILD|PLATFORM|OS)_")


def _dead_switch_lines(rows: list[str], start: int, end: int, defined: set[str], keep: set[int],
                       names: set[str] | None = None) -> set[int]:
    """`rows`(파일의 `code_only` 줄들) `start..end` 안에서 저장소에 정의가 없는 스위치 하나로만 정해지는 죽은
    구역의 줄 — `#ifdef X`/`#if defined(X)`의 참 쪽(그리고 그 지시문과 짝 `#endif`), `#ifndef X`/`#if !defined(X)`의
    `#else` 쪽. 다른 조건(`#if X > 1`·`&&`)은 모르니 둔다. `keep` 줄이 든 조건 묶음은 통째로 둔다.
    `names`가 있으면 구역을 뺀 스위치 이름을 거기 더한다."""
    groups: list[dict] = []
    stack: list[dict] = []
    for number in range(start, min(end, len(rows)) + 1):
        match = SWITCH_DIRECTIVE.match(rows[number - 1])
        if not match:
            if stack:
                stack[-1]["branches"][-1][1].append(number)
            continue
        kind, rest = match.groups()
        if kind in ("ifdef", "ifndef", "if"):
            name, negate = None, kind == "ifndef"
            if kind in ("ifdef", "ifndef"):
                name = rest.strip().split()[0] if rest.strip() else None
            else:
                single = SINGLE_SWITCH.match(rest)
                if single:
                    name, negate = single.group(1) or single.group(2), rest.strip().startswith("!")
            dead_name = (name and name not in defined and DEBUG_SWITCH.search(name)
                         and not SYSTEM_SWITCH.match(name))
            group = {"head": number, "name": name if dead_name else None,
                     "negate": negate, "branches": [["if", []]], "directives": [number], "end": None}
            if stack:
                stack[-1]["branches"][-1][1].append(number)
            stack.append(group)
        elif kind in ("elif", "else") and stack:
            stack[-1]["directives"].append(number)
            stack[-1]["branches"].append([kind, []])
            if kind == "elif":
                stack[-1]["name"] = None  # 여러 갈래 — 값을 모른다
        elif kind == "endif" and stack:
            group = stack.pop()
            group["directives"].append(number)
            group["end"] = number
            if stack:
                stack[-1]["branches"][-1][1].extend(group["directives"])
                for _kind, lines in group["branches"]:
                    stack[-1]["branches"][-1][1].extend(lines)
            groups.append(group)
    dead: set[int] = set()
    for group in groups:
        if group["name"] is None or group["end"] is None:
            continue
        body = {n for _kind, lines in group["branches"] for n in lines} | set(group["directives"])
        if body & keep:
            continue
        # 중괄호 없는 제어문 바로 뒤의 구역은 그 본문일 수 있다 — 지우면 다음 문장이 본문으로 읽힌다(검수 2026-10-10 r8)
        before = group["head"] - 1
        while before >= start and not rows[before - 1].strip():
            before -= 1
        if before >= start and (BARE_HEAD.match(rows[before - 1]) or (
                GUARD_HEAD.match(rows[before - 1]) and rows[before - 1].rstrip().endswith(")"))):
            continue
        live_branch = 1 if not group["negate"] else 0  # 스위치가 정의 안 됨: #ifdef면 else 쪽이 산다
        # 죽는 분기의 중괄호 짝이 안 맞으면(`#ifdef X` 아래 `if (a && b) {`만 있는 꼴) 지우지 않는다 — 조건 줄만
        # 빠지고 중괄호가 남아 구조가 깨졌다(검수 2026-10-10 mupdf)
        if any(sum(rows[n - 1].count("{") - rows[n - 1].count("}") for n in lines) != 0
               for index, (_kind, lines) in enumerate(group["branches"]) if index != live_branch):
            continue
        if names is not None:
            names.add(group["name"])
        dead |= set(group["directives"])
        for index, (_kind, lines) in enumerate(group["branches"]):
            if index != live_branch:
                dead |= set(lines)
    return dead


def _directive_lines(rows: list[str], start: int, keep: set[int]) -> set[int]:
    """`keep` 줄을 감싸는 전처리 조건 묶음의 지시문 줄(`#if…`·`#elif`·`#else`·`#endif`) — `rows`는 함수의
    `code_only` 줄들(`start`부터)."""
    stack: list[list[int]] = []  # 열린 묶음마다 그 지시문 줄들(닫히면 `#endif`까지 채워진다)
    open_at: dict[int, list[list[int]]] = {}
    continued: list[int] | None = None  # `\`로 이어지는 지시문 — 뒷줄도 그 묶음의 지시문이다(리뷰 2026-10-10)
    for offset, row in enumerate(rows):
        number = start + offset
        if continued is not None:
            continued.append(number)
            if not row.rstrip().endswith("\\"):
                continued = None
            continue
        match = SWITCH_DIRECTIVE.match(row)
        if not match:
            open_at[number] = list(stack)
            continue
        kind = match.group(1)
        group = None
        if kind in ("ifdef", "ifndef", "if"):
            group = [number]
            stack.append(group)
        elif kind in ("elif", "else") and stack:
            group = stack[-1]
            group.append(number)
        elif kind == "endif" and stack:
            group = stack.pop()
            group.append(number)
        if group is not None and row.rstrip().endswith("\\"):
            continued = group
    out: set[int] = set()
    for number in keep:
        for group in open_at.get(number, []):
            out |= set(group)
    return out


def _elided(read_source: Callable[[Location], str], function: Location, keep: set[int],
            marked: set[tuple[str, int]] | None = None) -> str:
    """`function`의 `keep` 줄만 — 건너뛴 자리에 `/* ... N lines omitted ... */`.

    여러 줄 블록 주석이 생략 경계에 걸리면 닫고(` */`) 다시 연다(`/* `) — 주석이 열린 채 잘려 뒤의 코드와
    생략 표시까지 주석으로 읽혔다(검수 2026-10-10)."""
    body = read_source(function).split("\n")
    start = function.line_start or 1
    inside = _comment_states(body)
    out: list[str] = []
    previous = None
    ordered = sorted(keep)
    def indent(number: int, before: int) -> str:
        # 생략 표시의 들여쓰기 — 다음 남긴 줄의 것, 그 줄이 닫는 `}`면 앞 줄의 것(앞 줄이 `{`로 끝나면 한 단 더).
        # 4칸 고정이라 원래 구조와 어긋났다(검수 2026-10-10 r8)
        row = body[number - start] if 0 <= number - start < len(body) else ""
        if row.strip() and not row.strip().startswith("}"):
            return row[: len(row) - len(row.lstrip())]
        prev = body[before - start] if 0 <= before - start < len(body) else ""
        lead = prev[: len(prev) - len(prev.lstrip())]
        if prev.rstrip().endswith("{"):
            lead += "\t" if "\t" in lead else "    "
        return lead

    for position, number in enumerate(ordered):
        if previous is not None and number > previous + 1:
            out.append(f"{indent(number, previous)}/* ... {number - previous - 1} lines omitted ... */")
        offset = number - start
        if 0 <= offset < len(body):
            row = body[offset]
            begins_inside, ends_inside = inside[offset]
            if begins_inside and previous != number - 1:
                row = "/* " + row.lstrip()
            following = ordered[position + 1] if position + 1 < len(ordered) else None
            if ends_inside and following != number + 1:
                row = row.rstrip() + " */"
            if marked and (function.file, number) in marked and row.strip() \
                    and not row.rstrip().endswith("\\") and not ends_inside:
                row = f"{row.rstrip()}  {SLICE_MARK}"
            out.append(row)
        previous = number
    return "\n".join(out)


LOG_CALL = re.compile(r"(?i)\w*(?:log|trace|debug|dbg|warn|verbose)\w*")
NOT_LOG = re.compile(r"(?i)^(?:\w*printf\w*|assert\w*|\w*login\w*|\w*logic\w*|catalog\w*|\w*dialog\w*|\w*blog\w*)$")
FLOW_CHANGE = re.compile(r"\b(?:return|goto|abort|exit|_exit|assert\w*|panic|BUG\w*)\b|\b(?:break|continue)\s*;")
# 주소를 넘기는 인자(`&x`) — 값을 쓰는 호출이라 로그로 다루지 않는다(검수 2026-10-10 r8 mariadb `make_default_log_name`)
ADDRESS_ARG = re.compile(r"[(,]\s*&\s*[A-Za-z_(]")


def _log_statement_lines(rows: list[str], start: int, shown: set[int], keep: set[int],
                         acts: Callable[[str], bool] | None = None) -> set[int]:
    """`shown` 줄 중 로그·추적 호출 한 문장(`name(...);`, 여러 줄이면 `;`까지)의 줄 — `keep` 줄이 든 문장과
    문장 앞에 다른 코드가 있는 줄(`if (x) log(...);`)과 주소를 넘기는 문장은 뺀다."""
    out: set[int] = set()
    numbers = sorted(shown)
    index = 0
    while index < len(numbers):
        number = numbers[index]
        offset = number - start
        head = re.match(r"^\s*([A-Za-z_]\w*)\s*\(", rows[offset]) if 0 <= offset < len(rows) else None
        # `acts(name)` — 정의가 흐름을 바꾸는 매크로(`STREAM_BOUND_WARN` → `assert`)는 로그가 아니다
        if not head or not LOG_CALL.fullmatch(head.group(1)) or NOT_LOG.match(head.group(1)) \
                or (acts is not None and acts(head.group(1))):
            index += 1
            continue
        span, depth, done = [], 0, False
        for k in range(offset, min(len(rows), offset + STATEMENT_LINES)):
            span.append(start + k)
            depth += rows[k].count("(") - rows[k].count(")")
            if depth <= 0 and rows[k].rstrip().endswith(";"):
                done = True
                break
        # 주소를 넘기는 문장(`log_name(&x, …)`)은 값을 쓰는 호출이다 — 로그로 다루지 않는다
        if done and not set(span) & keep and set(span) <= shown                 and not any(ADDRESS_ARG.search(rows[n - start]) for n in span):
            out |= set(span)
        index += 1
    return out


def _comment_states(rows: list[str]) -> list[tuple[bool, bool]]:
    """줄마다 (블록 주석 안에서 시작하나, 블록 주석 안에서 끝나나) — 문자열·문자 리터럴·`//`는 건너뛴다."""
    states = []
    inside = False
    for row in rows:
        begins = inside
        i, quote = 0, None
        while i < len(row):
            pair = row[i:i + 2]
            if inside:
                if pair == "*/":
                    inside, i = False, i + 2
                    continue
            elif quote:
                if row[i] == "\\":
                    i += 2
                    continue
                if row[i] == quote:
                    quote = None
            elif pair == "/*":
                inside, i = True, i + 2
                continue
            elif pair == "//":
                break
            elif row[i] in "\"'":
                quote = row[i]
            i += 1
        states.append((begins, inside))
    return states


def _provenance(
    index: dict, graph: GraphIndex, called: str, site: Location, enclosing: Location,
    read_source: Callable[[Location], str] | None = None,
    split: bool = False,
) -> set[int] | tuple[set[int], set[int]]:
    """호출 지점 앞, 같은 함수 안에서 그 호출의 **인자 변수가 어디서 오는지** 보여 주는 줄.

    인자 변수에 대입·쓰기하는 줄(`Data = malloc(itemlen)`), 그 변수를 주소로 넘기는 호출 줄
    (`read_metadata_buff(&wpmd, …)` — 출력 인자), 메모리 쓰기 호출의 인자로 넘기는 줄
    (`fread(Data+2, …)`). 호출 지점에 가까운 것부터 `PROVENANCE_LINES`줄까지."""
    line = site.line_start or 0
    rows = [r for r in index.get("calls", {}).get(called, [])
            if r["file"] == site.file and r["line"] == line]
    names: set[str] = set()
    for row in rows:
        for arg in row.get("args", []):
            names |= set(re.findall(r"\b[A-Za-z_]\w*\b", code_only(arg))) - CALL_KEYWORDS
    names -= {called}
    found: set[int] = set()
    by_hop: list[set[int]] = []
    # 대입 줄의 오른쪽 변수도 다시 찾는다(최대 PROVENANCE_HOPS단) — netconsd `ncrx.c` main의
    # `payload = buf`까지만 보이고 buf를 채우는 `recvfrom(fd, buf, …)`(입력 출처)이 빠졌다
    seen_names: set[str] = set()
    for _hop in range(PROVENANCE_HOPS):
        found = set()
        by_hop.append(found)
        fresh = names - seen_names
        if not fresh:
            break
        seen_names |= fresh
        more: set[str] = set()
        for name in fresh:
            for write in graph.writes_of(name, site.file) or []:
                if write.symbol == enclosing.symbol and (write.line_start or 0) < line:
                    found.add(write.line_start)
                    if read_source is not None:
                        text = code_only(read_source(Location(
                            file=site.file, line_start=write.line_start, line_end=write.line_start)))
                        rhs = text.split("=", 1)[1] if re.search(r"[^=!<>]=(?!=)", text) else ""
                        more |= set(re.findall(r"\b[A-Za-z_]\w*\b", rhs)) - CALL_KEYWORDS
        for callee, calls in index.get("calls", {}).items():
            for row in calls:
                if row["file"] != site.file or row.get("caller") != enclosing.symbol:
                    continue
                if not (enclosing.line_start or 0) <= row["line"] < line:
                    continue
                args = [code_only(a) for a in row.get("args", [])]
                # 주소로 넘기는 것은 변수 전체만(`&wpmd`) — `&parser->m_mem`처럼 필드의 주소를 넘기는
                # 초기화 호출(libexpat `poolInit`·`dtdCreate`)은 인자 값의 출처가 아니다
                # 인자 변수의 **필드에** 할당·읽기 결과를 넣는 줄(`g.out = sixel_allocator_malloc(…)`) —
                # 넘치는 버퍼의 할당 크기다. v23에서 입력 읽기 호출을 1단에서 빼자 held-out libsixel의
                # 이 줄이 빠졌다(jhead `malloc(itemlen)`과 같은 종류의 회귀)
                if ORIGIN_CALL.fullmatch(callee) and read_source is not None:
                    text = code_only(read_source(Location(
                        file=site.file, line_start=row["line"], line_end=row["line"])))
                    if any(re.search(rf"\b{re.escape(n)}\s*(?:\.|->)\s*[A-Za-z_]\w*\s*"
                                     rf"(?:\[[^\]]*\]\s*)*=(?!=)", text) for n in fresh):
                        found.add(row["line"])
                        continue
                if any(re.fullmatch(rf"\s*&\s*{re.escape(n)}\s*", a) for a in args for n in fresh) or (
                    # 입력 읽기 호출(recvfrom 등)은 2단부터 — 1단은 v21a와 같게(직접 출처를 안 밀어내게)
                    (DANGEROUS_CALL.fullmatch(callee) or (_hop > 0 and ORIGIN_CALL.fullmatch(callee)))
                    and any(re.search(rf"\b{re.escape(n)}\b", a) for a in args for n in fresh)
                ):
                    found.add(row["line"])
        names |= {n for n in more if index.get("calls", {}).get(n) is None}
    # 직접 출처(1단)는 기존대로 `PROVENANCE_LINES`줄, 그 뒤 단에서 찾은 줄은 `PROVENANCE_EXTRA`줄 더 —
    # 한 상한을 나눠 쓰면 전이로 찾은 줄이 직접 출처(jhead `Data = malloc(itemlen)`)를 밀어냈다(v22)
    direct = set(sorted(by_hop[0])[-PROVENANCE_LINES:]) if by_hop else set()
    farther = set().union(*by_hop[1:]) - direct if len(by_hop) > 1 else set()
    farther = set(sorted(farther)[-PROVENANCE_EXTRA:])
    return (direct, farther) if split else direct | farther


def _lexical_function(
    read_source: Callable[[Location], str], file: str, name: str
) -> Location | None:
    """인덱스에 없는 함수 정의를 그 파일에서 어휘로 찾는다 — `name(...) {`에서 짝 `}`까지.

    tree-sitter가 함수 정의를 놓칠 때가 있다(jq decNumber.c의 `decToString`: 호출은 인덱스에 있는데
    정의 행이 없다). 그러면 래퍼 너머의 실제 쓰기 함수 본문이 빠졌다."""
    code = code_only(read_source(Location(file=file)))
    match = re.search(
        rf"^[^\S\n]*(?:[A-Za-z_][\w\s\*]*?\s|\*)?\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        code, re.M,
    )
    if match is None:
        return None
    depth = 0
    for offset in range(match.end() - 1, len(code)):
        if code[offset] == "{":
            depth += 1
        elif code[offset] == "}":
            depth -= 1
            if depth == 0:
                return Location(file=file, symbol=name,
                                line_start=code.count("\n", 0, match.start()) + 1,
                                line_end=code.count("\n", 0, offset) + 1)
    return None


SLICE_MARK = "/* <- slice */"
IDENT_RE = re.compile(r"[A-Za-z_]\w*")


def _member_names(row: str) -> set[str]:
    """한 줄(주석 뺀)이 선언하는 멤버 이름 — 필드(`int len;`·`char buf[16];`·`unsigned f:1;`),
    함수 포인터 필드(`int (*read)(…);`), enum 상수(`FOO = 1,`·`BAR,`)."""
    names = set(re.findall(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?::\s*\w+\s*)?[;,]", row))
    names |= set(re.findall(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\)", row))
    enumerator = re.match(r"\s*([A-Za-z_]\w*)\s*(?:=|,|$)", row)
    if enumerator:
        names.add(enumerator.group(1))
    return names
DECL_HEAD = re.compile(
    r"^\s*(?:(?:const|static|register|unsigned|signed|volatile|struct|union|enum|long|short)\s+)*"
    r"([A-Za-z_]\w*)\b[\s\*]+(?=[A-Za-z_*])"
)
NOT_TYPES = frozenset({"return", "goto", "case", "else", "do", "sizeof", "typedef", "if", "while",
                       "for", "switch", "break", "continue", "default"})


def _depth0_split(text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in text:
        depth += char in "([{"
        depth -= char in ")]}"
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    return parts + [current]


def _lexical_locals(text: str) -> frozenset[str]:
    """함수 원문에서 어휘로 읽은 파라미터·지역 선언 이름 — 인덱스가 그 함수를 모를 때만 쓴다.
    `TYPE a, *b, c[3] = …;` 꼴의 줄과 머리의 `(…)` 파라미터. 넉넉히 잡는다(지역으로 잘못 잡으면
    그 이름의 정의를 안 찾을 뿐이다)."""
    code = code_only(text)
    names: set[str] = set()
    head = code[: code.find("{")] if "{" in code else ""
    if "(" in head:
        for param in _depth0_split(head[head.find("(") + 1: head.rfind(")")]):
            found = re.findall(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$", param.strip())
            names.update(found)
    for statement in re.split(r"[;{}]", code[len(head):]):
        match = DECL_HEAD.match(statement)
        if match is None or match.group(1) in NOT_TYPES:
            continue
        for declarator in _depth0_split(statement[match.end():]):
            name = re.match(r"[\s\*]*([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:=|$)", declarator)
            if name:
                names.add(name.group(1))
    return frozenset(names)


ORIGIN_CALL = re.compile(
    r".*(?:alloc|malloc|calloc|realloc|strdup|read|fread|recv|recvfrom|fgets|getline|mmap).*"
)


def _origin_score(index: dict | None, graph: GraphIndex, called: Location, site: Location) -> int:
    """호출 지점의 인자 출처 줄 중 할당·읽기 호출이 있는 줄 수 — 척추가 입력의 출처를 고르는 점수."""
    if index is None or not called.symbol:
        return 0
    enclosing = _narrowest_symbol(graph.symbols_for(site.file) or [], site.line_start or 1)
    if enclosing is None:
        return 0
    origin = _provenance(index, graph, called.symbol, site, enclosing)
    return len({
        row["line"] for name, rows in index.get("calls", {}).items() if ORIGIN_CALL.fullmatch(name)
        for row in rows if row["file"] == site.file and row["line"] in origin
    })


INT_LITERAL = re.compile(r"(?i)^(?:0x[0-9a-f]+|0[0-7]*|[1-9]\d*)[ul]*$")
CASE_HEAD = re.compile(r"^\s*case\s+(.+?)\s*:(?!:)")
DEFAULT_HEAD = re.compile(r"^\s*default\s*:(?!:)")
CASE_END = re.compile(r"^\s*(?:break|continue)\s*;|^\s*return\b|^\s*goto\s+\w+\s*;")


def _const_value(index: dict, read_source: Callable[[Location], str], text: str, depth: int = 0) -> int | None:
    """정수 상수식의 값 — 리터럴, 정의가 모두 같은 값의 매크로(2단계까지), 열거자, 그것들의 `|`·`+`. 모르면 None."""
    text = text.strip()
    while True:
        stripped = re.sub(r"^\(\s*(?:unsigned|signed|int|long|short|char|const|guint\w*|gint\w*|u?int\d+_t|size_t|\s)+\)", "",
                          text).strip()
        if stripped.startswith("(") and stripped.endswith(")") and _balanced_text(stripped[1:-1]):
            stripped = stripped[1:-1].strip()
        if stripped == text:
            break
        text = stripped
    for op in ("|", "+"):
        parts = _depth0_split_op(text, op)
        if len(parts) > 1:
            values = [_const_value(index, read_source, part, depth) for part in parts]
            if any(v is None for v in values):
                return None
            total = 0
            for v in values:
                total = (total | v) if op == "|" else (total + v)
            return total
    if INT_LITERAL.match(text):
        return int(re.sub(r"(?i)[ul]+$", "", text), 0)
    if not re.fullmatch(r"[A-Za-z_]\w*", text) or depth > 2:
        return None
    rows = index.get("macros", {}).get(text) or []
    if rows:
        values = set()
        for row in rows[:AMBIGUOUS_FILES]:
            body = code_only(read_source(Location(file=row["file"], line_start=row["line_start"],
                                                  line_end=row["line_end"])))
            match = re.match(rf"\s*#\s*define\s+{re.escape(text)}\s+(.+?)\s*$", body.replace("\\\n", " "), re.S)
            values.add(_const_value(index, read_source, match.group(1), depth + 1) if match else None)
        return values.pop() if len(values) == 1 and None not in values else None
    for row in (index.get("enumerators", {}).get(text) or [])[:1]:
        body = code_only(read_source(Location(file=row["file"], line_start=row["line_start"], line_end=row["line_end"])))
        inner = body[body.find("{") + 1: body.rfind("}")] if "{" in body else ""
        value = -1
        for item in _depth0_split_op(inner, ","):
            name, _, expr = item.partition("=")
            name = name.strip()
            if not name:
                continue
            value = _const_value(index, read_source, expr, depth + 1) if expr.strip() else (
                None if value is None else value + 1)
            if name == text:
                return value
    return None


def _balanced_text(text: str) -> bool:
    depth = 0
    for char in text:
        depth += (char == "(") - (char == ")")
        if depth < 0:
            return False
    return depth == 0


def _depth0_split_op(text: str, op: str) -> list[str]:
    """괄호 깊이 0의 `op`로 나눈다(`||`·`&&`는 나누지 않는다)."""
    parts, depth, current, i = [], 0, "", 0
    while i < len(text):
        char = text[i]
        depth += (char in "([{") - (char in ")]}")
        if char == op and depth == 0 and text[i:i + 2] != op * 2 and (i == 0 or text[i - 1] != op):
            parts.append(current)
            current = ""
        else:
            current += char
        i += 1
    parts.append(current)
    return [part for part in parts if part.strip()] if len(parts) > 1 else [text]


def _const_dead_lines(index: dict, read_source: Callable[[Location], str], function: Location,
                      arg_values: dict[int, set[int]]) -> tuple[set[int], dict]:
    """파라미터 k에 상수만 들어오는(`arg_values[k]` = 값들) 함수의 `switch (p)`·`switch (p & M)`에서 그 값이 안 닿는 `case`
    묶음의 줄 — (죽은 줄, 기록). 레이블·마스크를 하나라도 못 풀면 그 switch는 건드리지 않는다."""
    if function.line_start is None:
        return set(), {}
    params = next((r.get("params") or [] for r in index.get("functions", {}).get(function.symbol or "", [])
                   + index.get("functions", {}).get(_short(function.symbol), []) if r.get("file") == function.file), [])
    start = function.line_start
    rows = code_only(read_source(function)).split("\n")
    padded = [""] * (start - 1) + rows
    dead: set[int] = set()
    note: dict = {}
    for offset, row in enumerate(rows):
        if not re.match(r"^\s*(?:\}\s*)?switch\s*\(", row):
            continue
        number = start + offset
        last, cond = _condition_span(padded, number)
        expr = cond.strip()
        if expr.startswith("(") and expr.endswith(")"):
            expr = expr[1:-1].strip()
        match = re.fullmatch(r"\(?\s*([A-Za-z_]\w*)\s*\)?\s*(?:&\s*(.+))?", expr)
        if not match or match.group(1) not in params:
            continue
        k = params.index(match.group(1))
        if k not in arg_values:
            continue
        mask = _const_value(index, read_source, match.group(2)) if match.group(2) else None
        if match.group(2) and mask is None:
            continue
        values = {v & mask if mask is not None else v for v in arg_values[k]}
        # 본문 — `{`부터 짝 `}`까지, 깊이 1의 레이블로 묶음을 나눈다
        depth, opened, groups = 0, False, []
        written = re.compile(rf"\b{re.escape(match.group(1))}\s*(?:[-+*/|&^]?=(?!=)|\+\+|--)|&\s*{re.escape(match.group(1))}\b")
        if any(written.search(r) for r in rows):
            continue
        end = None
        for line in range(last, start + len(rows)):
            text = padded[line - 1]
            label_depth = depth
            if opened and label_depth == 1 and (CASE_HEAD.match(text) or DEFAULT_HEAD.match(text)):
                head = CASE_HEAD.match(text)
                label = head.group(1) if head else None
                if groups and not groups[-1]["body"]:
                    groups[-1]["labels"].append(label)
                    groups[-1]["lines"].append(line)
                else:
                    groups.append({"labels": [label], "lines": [line], "body": False})
                rest = text[(head or DEFAULT_HEAD.match(text)).end():]
                if rest.strip():
                    groups[-1]["body"] = True
            elif opened and groups and line != last:
                groups[-1]["lines"].append(line)
                if text.strip() and text.strip() != "}":
                    groups[-1]["body"] = True
            for char in text if line != last else text[text.rfind(")") + 1:] if ")" in text else text:
                if char == "{":
                    depth += 1
                    opened = True
                elif char == "}":
                    depth -= 1
            if opened and depth == 0:
                end = line
                break
        if end is None or not groups:
            continue
        if groups[-1]["lines"] and groups[-1]["lines"][-1] == end:
            groups[-1]["lines"].pop()
        resolved = []
        for group in groups:
            labels = [None if label is None else _const_value(index, read_source, label) for label in group["labels"]]
            if any(label is not None and value is None for label, value in zip(group["labels"], labels)):
                resolved = None
                break
            resolved.append(labels)
        if resolved is None:
            continue
        live = [any(v in values for v in labels if v is not None) for labels in resolved]
        if not any(live):
            live = [None in labels for labels in resolved]
        # 갈래 끝이 탈출이 아니면 다음 갈래로 이어진다(fallthrough)
        for index_, group in enumerate(groups[:-1]):
            if live[index_]:
                body = [padded[n - 1] for n in group["lines"] if padded[n - 1].strip() and padded[n - 1].strip() != "}"]
                if not body or not CASE_END.search(body[-1]):
                    live[index_ + 1] = True
        cut = {n for group, keep in zip(groups, live) if not keep for n in group["lines"]}
        if cut:
            dead |= cut
            note = {"param": match.group(1), "values": sorted(values),
                    "kept_labels": [l for g, keep in zip(groups, live) if keep for l in g["labels"]],
                    "dropped_labels": len([l for g, keep in zip(groups, live) if not keep for l in g["labels"]])}
    return dead, note



SMART_POINTER = r"(?:std::)?(?:unique_ptr|shared_ptr|scoped_ptr|auto_ptr)\s*<[^;]*>\s*"
OWNER_RHS = re.compile(r"=\s*(?:nullptr\b|NULL\b|std::move\s*\(|new\b|\w*(?:Create|make_unique|make_shared)\w*\s*[(<])")


def _owner_release_sites(index: dict, graph, read_source: Callable[[Location], str], anchor: Location,
                         anchors: set[tuple[str, int]], cwe: str | None) -> list[tuple[Location, str]]:
    """C++ 소유 멤버의 해제 지점 — [(지점, 멤버 이름)], 순서: 소유권을 넘기는 `return std::move(X)`, 넘겨받은 새
    소유자의 해제, 같은 클래스의 `reset`·`delete`, 같은 클래스의 재대입. 수명 계열 CWE·C++ 진입 파일·클래스 메서드만."""
    if cwe not in LIFETIME_CWES or not anchor.file.lower().endswith((".cpp", ".cc", ".cxx")) \
            or "::" not in (anchor.symbol or ""):
        return []
    klass = anchor.symbol.rsplit("::", 1)[0]
    assigns = index.get("assigns", {})
    field_calls = index.get("field_calls", {}) if isinstance(index.get("field_calls"), dict) else {}

    def text_of(file: str) -> str:
        try:
            return code_only(read_source(Location(file=file)))
        except Exception:
            return ""

    def owner_typed(file: str, cls: str, name: str) -> bool:
        base = posixpath.join(posixpath.dirname(file), cls.rsplit("::", 1)[-1])
        for ext in (".h", ".hpp", ".hh"):
            header = text_of(base + ext)
            if header:
                return bool(re.search(SMART_POINTER + rf"{re.escape(name)}\s*[;={{]", header))
        body = text_of(file)
        return bool(re.search(rf"\bdelete\s*(?:\[\s*\])?\s*(?:this->)?{re.escape(name)}\b|"
                              rf"\b{re.escape(name)}\s*\.\s*reset\s*\(", body))

    def releases(file: str, cls: str, name: str, skip: set[int]) -> tuple[list[int], list[int], list[int]]:
        body = text_of(file).split("\n")
        moved = [i + 1 for i, row in enumerate(body)
                 if re.search(rf"\breturn\s+(?:std::move\s*\(\s*(?:this->)?{re.escape(name)}\s*\)|"
                              rf"(?:this->)?{re.escape(name)}\s*\.\s*release\s*\()", row)]
        direct = [i + 1 for i, row in enumerate(body)
                  if re.search(rf"\bdelete\s*(?:\[\s*\])?\s*(?:this->)?{re.escape(name)}\b", row)]
        direct += [r["line"] for rows in field_calls.values() for r in rows
                   if r.get("file") == file and re.fullmatch(rf"(?:this->)?{re.escape(name)}\s*(?:\.|->)\s*(?:reset|swap)",
                                                             (r.get("expr") or "").strip())]
        assigned = []
        for func, names in (assigns.get(file) or {}).items():
            if not func.startswith(cls + "::") or func == cls + "::" + cls.rsplit("::", 1)[-1] \
                    or func == anchor.symbol:
                continue
            for line in names.get(name, []):
                if 0 < line <= len(body) and OWNER_RHS.search(body[line - 1]):
                    assigned.append(line)
        return ([n for n in moved if n not in skip], sorted(set(direct) - skip), sorted(set(assigned) - skip))

    owners: set[str] = set()
    local = graph.locals_of(anchor.file, anchor.symbol) or frozenset()
    for file, line in sorted(anchors):
        if file != anchor.file:
            continue
        code = code_only(read_source(Location(file=file, line_start=line, line_end=line)))
        owners |= set(re.findall(r"\b([A-Za-z_]\w*)\s*\.\s*(?:get|release|data)\s*\(", code))
        for name in set(re.findall(r"\b[A-Za-z_]\w*\b", code)) - set(local):
            if any(func.startswith(klass + "::") and func != anchor.symbol and name in names
                   for func, names in (assigns.get(file) or {}).items()):
                owners.add(name)
    owners = {o for o in owners if o not in local and owner_typed(anchor.file, klass, o)}
    sites: list[tuple[Location, str]] = []

    def site(file: str, line: int) -> Location:
        enclosing = _narrowest_symbol(graph.symbols_for(file) or [], line)
        return Location(file=file, symbol=enclosing.symbol if enclosing else None, line_start=line, line_end=line)

    hop_sites, same_direct, same_assigned = [], [], []
    for name in sorted(owners):
        moved, direct, assigned = releases(anchor.file, klass, name, set())
        for line in moved:
            sites.append((site(anchor.file, line), name))
            mover = _narrowest_symbol(graph.symbols_for(anchor.file) or [], line)
            short = _short(mover.symbol) if mover else ""
            if not short:
                continue
            for row in index.get("calls", {}).get(short, []) + field_calls.get(short, []):
                row_text = code_only(read_source(Location(file=row["file"], line_start=row["line"],
                                                          line_end=row["line"])))
                taken = re.match(rf"^\s*(?:this->)?([A-Za-z_]\w*)\s*=\s*[^;]*\b{re.escape(short)}\s*\(", row_text)
                caller = row.get("caller") or ""
                if not taken or "::" not in caller:
                    continue
                cls2 = caller.rsplit("::", 1)[0]
                owner2 = taken.group(1)
                if not owner_typed(row["file"], cls2, owner2):
                    continue
                _m, direct2, assigned2 = releases(row["file"], cls2, owner2, {row["line"]})
                body2 = text_of(row["file"]).split("\n")
                first = [n for n in assigned2 if re.search(r"=\s*(?:std::move\s*\(|nullptr\b|NULL\b)", body2[n - 1])]
                # 넘겨받는 대입 줄(`m_Encrypt = parser.TakeEncrypt()`) 자체가 먼저 — 새 소유자가 누구인지 잇는 줄이다(판정
                # 2026-10-10 r17b podofo: 소유권 이동 지점이 없었다)
                hop_sites += [(site(row["file"], row["line"]), owner2)]
                hop_sites += [(site(row["file"], n), owner2) for n in direct2 + first
                              + [n for n in assigned2 if n not in first]]
        same_direct += [(site(anchor.file, n), name) for n in direct]
        same_assigned += [(site(anchor.file, n), name) for n in assigned]
    return sites + hop_sites + same_direct + same_assigned


def _call_args(text: str) -> list[tuple[str, list[str]]]:
    """문장 안 호출마다 (이름, 인자들) — 괄호 짝으로 읽는다."""
    out = []
    for match in CALL_NAME.finditer(text):
        name = match.group(1)
        if name in CALL_KEYWORDS:
            continue
        depth, i = 0, match.end() - 1
        for j in range(i, len(text)):
            depth += (text[j] == "(") - (text[j] == ")")
            if depth == 0:
                out.append((name, [a.strip() for a in _depth0_split(text[i + 1:j]) if a.strip()]))
                break
    return out


def _arg_root(arg: str) -> str | None:
    """인자의 바탕 이름 — `&x`·`x->f`·`x[i]`·`(T *)x`의 x."""
    found = re.match(r"^[\s&*]*(?:\([^()]*\)\s*)?[\s&*]*([A-Za-z_]\w*)", arg)
    return found.group(1) if found else None


def _statement_lines(rows: list[str], start: int, number: int) -> list[int]:
    """`number`에서 시작하는 문장의 줄 — 괄호가 맞고 `;`·`{`·`}`로 끝나는 줄까지(`STATEMENT_LINES`줄)."""
    out, depth = [], 0
    for k in range(number, min(start + len(rows), number + STATEMENT_LINES)):
        text = rows[k - start]
        out.append(k)
        depth += text.count("(") - text.count(")")
        if depth <= 0 and text.rstrip().endswith((";", "{", "}")):
            break
    return out


def _focused_input_lines(read_source: Callable[[Location], str], function: Location,
                         cursors: set[str], nested_calls: set[str]) -> set[int]:
    """Keep input reads, their derived values, grammar guards, and output stores.

    This is deliberately a line-level context, not a second data-flow proof.
    Omitted spans stay explicit in the rendered source.  Nested decoder calls
    are always retained so the reader can follow the selected call chain.
    """
    start = function.line_start or 1
    end = function.line_end or start
    rows = code_only(read_source(function)).split("\n")
    header_end = _header_end(read_source, function)
    names = set(cursors)
    # Local assignments transfer a character or digit from the input cursor.
    # Do not treat struct field names as variables.
    assignment = re.compile(r"(?<![.>])\b([A-Za-z_]\w*)\s*(?<![=<>!])=(?!=)\s*([^;]+)")
    for _ in range(4):
        before = set(names)
        for row in rows:
            for match in assignment.finditer(row):
                if set(IDENT_RE.findall(match.group(2))) & names:
                    names.add(match.group(1))
        if names == before or len(names) > 24:
            break
    # A switch on a scalar parameter often decides the accepted radix or
    # delimiter.  Keep that parameter's tests as well as cursor reads.
    params = set(_param_names(read_source, function))
    for row in rows:
        switch = re.search(r"\bswitch\s*\(\s*([A-Za-z_]\w*)\s*\)", row)
        if switch and switch.group(1) in params:
            names.add(switch.group(1))
    chosen = _tainted_lines(read_source, function, names)
    calls = set()
    for offset, row in enumerate(rows):
        number = start + offset
        if number > header_end and any(
                re.search(rf"\b{re.escape(name)}\s*\(", row)
                for name in nested_calls | {function.symbol or ""} if name):
            calls.add(number)
            chosen.update(_statement_lines(rows, start, number))
        # Include final stores reached from the input-derived local value even
        # when the receiver itself is a separate output parameter.
        if re.search(r"(?:->|\.)\s*[A-Za-z_]\w*\s*=(?!=)", row) \
                and set(IDENT_RE.findall(row.split("=", 1)[-1])) & names:
            chosen.update(_statement_lines(rows, start, number))
    if end - start + 1 > 400 and calls:
        # In a large dispatch parser, one shared input cursor can be read by
        # many unrelated cases.  Retain its reads around the selected child
        # decoder calls; case/guard ancestry is restored below.
        chosen = {number for number in chosen
                  if any(abs(number - call) <= 18 for call in calls)}
        chosen |= calls
    chosen |= _guard_lines(read_source, function, names, 48)
    if end - start + 1 > 400 and calls:
        chosen = {number for number in chosen
                  if any(abs(number - call) <= 18 for call in calls)} | calls
    if end - start + 1 <= 400:
        # The terminal return may be independent of the cursor token while it
        # still conveys the parser's output and success/failure convention.
        for number in range(max(start, end - 15), end + 1):
            row = rows[number - start]
            if re.search(r"\breturn\b|\bresolve_\w+\s*\(", row):
                chosen.update(_statement_lines(rows, start, number))
    chosen |= _controlling_heads(read_source, function, chosen)
    # Preserve the case labels governing retained statements, including a
    # consecutive run of labels such as case '1': ... case '9':.
    case_head = re.compile(r"^\s*(?:case\b[^:]*|default)\s*:")
    switch_head = re.compile(r"^\s*switch\s*\(")
    for number in sorted(chosen | calls):
        if not start <= number <= end:
            continue
        row = rows[number - start]
        indent = len(row) - len(row.lstrip())
        case = None
        for previous in range(number - 1, max(start - 1, number - 160), -1):
            prior = rows[previous - start]
            if case_head.match(prior) and len(prior) - len(prior.lstrip()) < indent:
                case = previous
                break
        if case is None:
            continue
        chosen.add(case)
        previous = case - 1
        while previous >= start and (not rows[previous - start].strip()
                                     or case_head.match(rows[previous - start])):
            if case_head.match(rows[previous - start]):
                chosen.add(previous)
            previous -= 1
        case_indent = len(rows[case - start]) - len(rows[case - start].lstrip())
        for previous in range(case - 1, max(start - 1, case - 160), -1):
            prior = rows[previous - start]
            if switch_head.match(prior) and len(prior) - len(prior.lstrip()) < case_indent:
                chosen.add(previous)
                break
    chosen = _statement_starts(read_source, function, chosen)
    chosen |= set(range(start, header_end + 1)) | {end}
    chosen = _complete_guards(read_source, function, chosen)
    chosen = _balanced(read_source, function, chosen)
    # Finish retained multi-line statements; otherwise an omitted RHS can
    # turn a useful conversion into a misleading fragment.
    for number in sorted(chosen):
        row = rows[number - start].strip()
        if row and not row.startswith(("#", "case ", "default:", "/*", "*", "//", "}")) \
                and not row.endswith(("{", ":")):
            chosen.update(_statement_lines(rows, start, number))
    padded = [""] * (start - 1) + rows
    for number in range(start, end + 1):
        if not GUARD_HEAD.match(rows[number - start]):
            continue
        last, _condition = _condition_span(padded, number)
        if chosen.intersection(range(number, last + 1)):
            chosen.update(range(number, last + 1))
    return _balanced(read_source, function, chosen)


def _ascent_plan(read_source: Callable[[Location], str], anchor: Location, spanned: list[Location],
                 dependency: set[tuple[str, int]]) -> dict:
    """역방향 슬라이스가 호출자로 거슬러 올라간 함수 — {함수: {"down": 그 함수가 부르는 사슬 이름들, "seeds": 그 호출
    인자들}}. 사슬은 entry에서 시작해, 데이터 의존 줄에서 사슬의 함수를 부르는 함수를 더해 가며 넓힌다. 사슬을
    부르지 않는 함수(내려간 피호출)는 들지 않는다."""
    chain = {_short(anchor.symbol)}
    plan: dict = {}
    changed = True
    while changed:
        changed = False
        for function in spanned:
            if function in plan or function == anchor or function.line_start is None:
                continue
            rows = code_only(read_source(function)).split("\n")
            down, seeds = set(), []
            for f, n in sorted(dependency):
                if f != function.file or not _spans(function, n):
                    continue
                text = " ".join(rows[k - function.line_start] for k in _statement_lines(rows, function.line_start, n))
                for name, args in _call_args(text):
                    if name in chain:
                        down.add(name)
                        seeds += args
            if down:
                plan[function] = {"down": down, "seeds": seeds}
                chain.add(_short(function.symbol))
                changed = True
    return plan


def _narrow_ascended(read_source: Callable[[Location], str], graph, index: dict, function: Location, flow: set[int],
                     plan: dict, sink_fields: set[str], carried_names: set[str], cwe: str | None,
                     must: set[int]) -> set[int] | None:
    """호출자로 거슬러 올라간 긴 함수의 데이터 의존 줄을 사슬 호출의 인자가 실제로 흐르는 줄로 줄인다. Joern은 인자를
    받는 모든 호출이 그 변수를 다시 정의한다고 보고 그 호출의 다른 인자까지 잇는다 — 문맥 포인터(`ctxt`·`opts`)를
    받는 앞선 호출과 그 결과가 줄줄이 붙었다(libxml2 `xmlXPathNodeCollectAndTest` 298줄, 검토 2026-10-10 r13).

    남기는 문장(고정점까지): (K1) 사슬 함수 호출, (K2) 왼쪽 바탕이 `needed`인 대입(필드 경로면 파라미터가 아니거나
    관련 필드일 때) — 오른쪽 이름을 `needed`에 더한다, (K3) `needed`를 받는 강한 호출(사슬·실린 함수·sink 필드를 쓰는
    함수·위험 호출) — 인자 바탕을 `needed`에, 또는 `&x`·지역 x를 받는 호출(전파 없음), (K4) 해제 호출(해제 계열 CWE).
    `needed`는 사슬 호출 인자의 바탕에서 시작한다. 그 뒤 `needed`를 돌려주는 `return`, 바탕이 `needed`인 sink 필드 줄
    (`GUARD_LIMIT`줄까지), 남긴 줄 바로 앞의 레이블. 늘어난 것이 없으면 None(예전대로)."""
    if function.line_start is None:
        return None
    start = function.line_start
    rows = code_only(read_source(function)).split("\n")
    params = set(_param_names(read_source, function))
    down = set(plan["down"])
    needed = {r for r in (_arg_root(a) for a in plan["seeds"]) if r}
    relevant = set(sink_fields) | {f for a in plan["seeds"] for f in FIELD_OF.findall(a)}
    statements: dict[int, list[int]] = {}
    for n in sorted(flow):
        head = min(_statement_starts(read_source, function, {n}))
        if head not in statements:
            statements[head] = _statement_lines(rows, start, head)
    writer_cache: dict[str, bool] = {}

    def writer(name: str) -> bool:
        if name not in writer_cache:
            writer_cache[name] = any(r.get("func") == name for f in sink_fields
                                     for r in index.get("field_assigns", {}).get(f, ())) or any(
                _writes_field(code_only(read_source(loc)), f)
                for loc in graph.functions_named([name], function.file) or [] for f in sink_fields)
        return writer_cache[name]

    kept: set[int] = set()
    changed = True
    while changed:
        changed = False
        for head, span in statements.items():
            if head in kept:
                continue
            text = " ".join(rows[k - start] for k in span if 0 <= k - start < len(rows))
            calls = _call_args(text)
            keep = False
            # 사슬 함수뿐 아니라 실린 슬라이스 함수를 부르는 줄도 — 경로를 잇는 중간 호출(`xmlXPathCompOpEvalToBoolean`·
            # `preloadobjstms`)이 빠졌다(검수 2026-10-10 rounds_r12_eval.md)
            if any(name in down or name in carried_names for name, _a in calls):
                keep = True
                for _name, args in calls:
                    needed |= {r for r in map(_arg_root, args) if r}
            assign = re.match(r"\s*(?:[A-Za-z_][\w\s\*]*?[\s\*])?\**\s*([A-Za-z_]\w*)((?:\s*(?:->|\.)\s*[A-Za-z_]\w*"
                              r"|\s*\[[^\]]*\])*)\s*[-+*/|&^]?=(?!=)(.*)", text)
            if not keep and assign and assign.group(1) in needed:
                path = assign.group(2) or ""
                fields = set(re.findall(r"(?:->|\.)\s*([A-Za-z_]\w*)", path))
                if not fields or assign.group(1) not in params or fields & relevant:
                    keep = True
                    needed |= set(IDENT_RE.findall(assign.group(3))) - {n for n, _a in calls} - CALL_KEYWORDS
            if not keep:
                for name, args in calls:
                    roots = {r for r in map(_arg_root, args) if r}
                    if not roots & needed:
                        continue
                    # 본문에서 사슬 함수를 직접 부르는 함수도 강하다 — 같은 상태를 건드린다(`xmlXPathCompOpEvalToBoolean`이
                    # `valuePop`을 부른다, 검수 2026-10-10 rounds_r12_eval.md: 경로를 잇는 그 호출 줄이 잘렸다)
                    if name in down or name in carried_names or DANGEROUS_CALL.fullmatch(name) or writer(name) \
                            or any(r.get("caller") == name for d in down for r in index.get("calls", {}).get(d, ())):
                        keep = True
                        needed |= roots
                    elif any(a.lstrip().startswith("&") and _arg_root(a) in needed for a in args) or (
                            roots & needed) - params:
                        keep = True
            if not keep and cwe in RELEASE_CWES and any(RELEASE_CALL.fullmatch(n) for n, _a in calls):
                keep = True
            if keep:
                kept |= set(span)
                kept.add(head)
                changed = True

    def statement_text(span: list[int]) -> str:
        return " ".join(rows[k - start] for k in span if 0 <= k - start < len(rows))

    # 사슬 호출 바로 앞뒤(`BRACKET_LINES`줄)에서 `needed`를 받는 호출 문장 — 준비·정리(`xmlXPathSetFrame`·`PopFrame`).
    # 파라미터만 받아 버려지던 줄인데 수정이 흔히 거기 들어간다(검수 2026-10-10 rounds_r12_eval.md: libxml2 patch 핵심 줄)
    chain_lines = {head for head, span in statements.items()
                   if any(name in down for name, _a in _call_args(statement_text(span)))}
    for head, span in statements.items():
        if head in kept or not any(abs(head - c) <= BRACKET_LINES for c in chain_lines):
            continue
        if any({_arg_root(a) for a in args} & needed for _n, args in _call_args(statement_text(span))):
            kept |= set(span)
    for head, span in statements.items():
        text = statement_text(span)
        if re.match(r"\s*return\b", text) and set(IDENT_RE.findall(text)) & needed:
            kept |= set(span)
    added = 0
    for offset, row in enumerate(rows):
        if added >= GUARD_LIMIT:
            break
        roots = set(re.findall(r"\b([A-Za-z_]\w*)\s*(?:->|\.)\s*(?:" + "|".join(map(re.escape, sink_fields)) + r")\b",
                               row)) if sink_fields else set()
        if roots & needed and start + offset not in kept:
            kept.add(start + offset)
            added += 1
    for n in sorted(kept):
        if n - 1 >= start and re.match(r"^\s*[A-Za-z_]\w*\s*:\s*$", rows[n - 1 - start]) \
                and not re.match(r"^\s*default\s*:", rows[n - 1 - start]):
            kept.add(n - 1)
    kept |= must
    if not kept - must:
        return None
    return kept


def _lexical_enclosing(rows: list[str], line: int) -> tuple[int, int, str | None] | None:
    """`line`을 품는 함수의 어휘 범위 — 위로 열 0의 `{`(그 위 머리 줄들 포함), 아래로 열 0의 `}`. (시작, 끝, 이름)."""
    if not 1 <= line <= len(rows):
        return None
    brace = next((i for i in range(line, 0, -1) if rows[i - 1].startswith("{")), None)
    close = next((i for i in range(line, len(rows) + 1) if rows[i - 1].startswith("}")), None)
    if brace is None or close is None or any(rows[i - 1].startswith("}") for i in range(brace, line)):
        return None
    head = brace
    for i in range(brace - 1, max(0, brace - 12), -1):
        text = rows[i - 1].rstrip()
        if not text or text.endswith((";", "}")) or text.startswith(("#", "/", " *", "*")):
            break
        head = i
    name = re.findall(r"([A-Za-z_]\w*)\s*\(", " ".join(rows[head - 1:brace]))
    return head, close, (name[0] if name else None)


def _inline_setter(index: dict, graph, read_source: Callable[[Location], str], anchor: Location,
                   anchors: list[tuple[str, int]], add, add_window, counts: dict) -> None:
    """시작 줄의 `recv->M(…)`이 색인에 없는 헤더 안 한 줄 인라인 메서드면 그 줄·그 메서드가 넣는 멤버의 선언·그 멤버로
    부르는 곳(`FIELD_SITES`곳). 헤더 후보는 entry 디렉터리, 같은 이름의 한 줄 정의가 하나뿐일 때만."""
    folder = posixpath.dirname(anchor.file)
    headers = [f for f in index.get("files") or [] if isinstance(f, str) and posixpath.dirname(f) == folder
               and f.lower().endswith((".h", ".hpp", ".hh"))]
    for file, line in anchors:
        if file != anchor.file:
            continue
        code = code_only(read_source(Location(file=file, line_start=line, line_end=line)))
        for match in re.finditer(r"\b[A-Za-z_]\w*\s*(?:->|\.)\s*([A-Za-z_]\w*)\s*\(", code):
            method = match.group(1)
            if graph.functions_named([method], anchor.file) or index.get("functions", {}).get(method):
                continue
            found = []
            pattern = re.compile(rf"^\s*(?:inline\s+)?[\w:<>\*&\s]+\b{re.escape(method)}\s*\(([^;{{}}]*)\)\s*"
                                 rf"(?:const\s*)?\{{([^{{}}]*)\}}\s*$")
            for header in headers:
                rows = read_source(Location(file=header)).split("\n")
                found += [(header, i + 1, m) for i, row in enumerate(rows) for m in [pattern.match(row)] if m]
            if len(found) != 1:
                continue
            header, number, defined = found[0]
            params = {p.split()[-1].strip("*&") for p in defined.group(1).split(",") if p.strip()}
            store = re.match(r"^\s*(?:this->)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;\s*$", defined.group(2))
            if not store or store.group(2) not in params:
                continue
            member = store.group(1)
            if add("callee", "sink_callee", Location(file=header, symbol=method, line_start=number, line_end=number)):
                counts["inline_sink_callees"] = counts.get("inline_sink_callees", 0) + 1
            rows = read_source(Location(file=header)).split("\n")
            declared = [i + 1 for i, row in enumerate(rows)
                        if re.search(rf"[\w>\*&]\s*\b{re.escape(member)}\s*;", code_only(row))]
            for decl in declared[:1]:
                add("callee", "member_declaration", Location(file=header, symbol=member, line_start=decl,
                                                              line_end=decl))
            base = posixpath.splitext(posixpath.basename(header))[0]
            field_calls = index.get("field_calls", {}) if isinstance(index.get("field_calls"), dict) else {}
            uses = sorted({(r["file"], r["line"], r.get("caller")) for rows_ in field_calls.values() for r in rows_
                           if posixpath.splitext(posixpath.basename(r.get("file", "")))[0] == base
                           and not r.get("file", "").lower().endswith((".h", ".hpp", ".hh"))
                           and re.match(rf"\s*(?:this->)?{re.escape(member)}\s*(?:->|\.)", r.get("expr") or "")})
            for use_file, use_line, caller in uses[:FIELD_SITES]:
                if add_window("write_window", Location(file=use_file, symbol=caller, line_start=use_line,
                                                       line_end=use_line), field=member, use_site=True):
                    counts["use_sites"] = counts.get("use_sites", 0) + 1
            return


def _controlling_heads(read_source: Callable[[Location], str], function: Location, lines: set[int]) -> set[int]:
    """`lines`를 거느리는 if/while/for 조건 줄 — 감싼 블록의 머리(`_balanced`가 더하는 줄 중 조건 머리)와, 중괄호 없는
    본문이면 바로 위 코드 줄의 머리(여러 줄 조건이면 그 시작 줄)."""
    if function.line_start is None or not lines:
        return set()
    start = function.line_start
    rows = code_only(read_source(function)).split("\n")
    out: set[int] = set()
    for n in _balanced(read_source, function, set(lines)) - set(lines):
        if 0 <= n - start < len(rows) and GUARD_HEAD.match(rows[n - start]):
            out.add(n)
    for n in lines:
        back = n - 1
        while back >= start and not rows[back - start].strip():
            back -= 1
        if back < start:
            continue
        head = min(_statement_starts(read_source, function, {back}))
        if GUARD_HEAD.match(rows[head - start]) and rows[back - start].rstrip().endswith(")"):
            out.add(head)
    return out


def _typed_field_reads(index: dict, read_source: Callable[[Location], str], anchor: Location,
                       points: set[tuple[str, int]], field: str, limit: int) -> list[Location]:
    """시작·끝 줄이 `b->field = …`로 저장한 객체(b)의 타입과 같은 타입 변수에서 `field`를 읽는 줄 — 같은 디렉터리 파일들,
    다른 파일 먼저(같은 파일은 호출 인자 규칙이 본다). 타입을 모르면 빈 목록."""
    var_types = index.get("var_types", {})

    def type_of(name: str, file: str, func: str | None) -> set[str]:
        return {t.get("name") for r in var_types.get(name, []) if r.get("file") == file
                and (func is None or r.get("func") == func) for t in r.get("types", []) if t.get("name")}

    wanted: set[str] = set()
    for file, line in points:
        code = code_only(read_source(Location(file=file, line_start=line, line_end=line)))
        for base in re.findall(rf"\b([A-Za-z_]\w*)\s*->\s*{re.escape(field)}\s*=(?!=)", code):
            wanted |= type_of(base, file, anchor.symbol if file == anchor.file else None)
    if not wanted:
        return []
    folder = posixpath.dirname(anchor.file)
    reader = re.compile(rf"\b([A-Za-z_]\w*)\s*->\s*{re.escape(field)}\b(?!\s*=[^=])")
    found: list[Location] = []
    files = sorted((f for f in index.get("files") or [] if isinstance(f, str) and posixpath.dirname(f) == folder
                    and f != anchor.file and f.lower().endswith((".c", ".cc", ".cpp"))))
    for file in files:
        text = read_source(Location(file=file))
        if field not in text:
            continue
        rows = code_only(text).split("\n")
        for i, row in enumerate(rows):
            for base in reader.findall(row):
                if type_of(base, file, None) & wanted:
                    found.append(Location(file=file, line_start=i + 1, line_end=i + 1))
                    break
            if len(found) >= limit:
                return found
    return found


def _entry_flow_calls(index: dict, read_source: Callable[[Location], str], graph, anchor: Location,
                      anchors: set[tuple[str, int]], dependency: set[tuple[str, int]]) -> tuple[set[str], set[str]]:
    """entry 안의 호출 중 (시작·끝 줄의 지역 변수를 인자로 받는 것, 데이터 의존 줄의 지역 변수에 결과를 넣는 것)."""
    if anchor.line_start is None or not anchor.symbol:
        return set(), set()
    own = set(graph.locals_of(anchor.file, anchor.symbol) or ()) | set(_param_names(read_source, anchor))
    span = _span(anchor)

    def names_on(points: set[tuple[str, int]]) -> set[str]:
        out: set[str] = set()
        for file, number in points:
            if file == anchor.file and number in span:
                out |= set(IDENT_RE.findall(code_only(read_source(Location(file=file, line_start=number,
                                                                             line_end=number)))))
        return out & own

    anchor_lines = {n for f, n in anchors if f == anchor.file}
    anchor_vars = names_on(anchors)
    dep_vars = names_on(dependency)
    takers: set[str] = set()
    feeders: set[str] = set()
    for name, rows in index.get("calls", {}).items():
        if name in CALL_KEYWORDS:
            continue
        for row in rows:
            if row["file"] != anchor.file or row["line"] not in span or row["line"] in anchor_lines:
                continue
            # 시작·끝 줄 변수 둘 이상을 함께 받는 호출만(버퍼와 개수) — 하나면 문맥 포인터(`session`)를 받는 거의 모든
            # 호출이 붙었다(2026-10-10 r14 gnutls 피호출 320→989줄)
            if len({_arg_root(a) for a in row.get("args", [])} & anchor_vars) >= 2:
                takers.add(name)
            code = code_only(read_source(Location(file=anchor.file, line_start=row["line"], line_end=row["line"])))
            fed = re.match(rf"\s*\**\s*([A-Za-z_]\w*)(?:\s*(?:->|\.)\s*\w+|\s*\[[^\]]*\])*\s*=\s*"
                           rf"(?:\([^()]*\)\s*)?{re.escape(name)}\s*\(", code)
            if fed and fed.group(1) in dep_vars:
                feeders.add(name)
    return takers, feeders


PRUNE_BLOCK = 6  # 이보다 짧은 빈 블록은 그대로 둔다


def _empty_blocks(read_source: Callable[[Location], str], function: Location, flow: set[int]) -> set[int]:
    """`function`에서 `flow` 줄을 하나도 품지 않은 가장 큰 블록들의 안쪽 줄 — `{ … }` 본문(여는·닫는 줄은 둔다)과
    `switch` 안의 `case`·`default` 묶음(레이블 줄은 둔다). `PRUNE_BLOCK`줄 미만은 두고, 전처리 줄이 낀 블록도 둔다."""
    if function.line_start is None:
        return set()
    start = function.line_start
    rows = code_only(read_source(function)).split("\n")
    blocks: list[tuple[int, int]] = []
    stack: list[int] = []
    for offset, row in enumerate(rows):
        for char in row:
            if char == "{":
                stack.append(start + offset)
            elif char == "}" and stack:
                opener = stack.pop()
                if start + offset - opener - 1 >= PRUNE_BLOCK:
                    blocks.append((opener + 1, start + offset - 1))
    # case 묶음 — 레이블부터 같은 깊이의 다음 레이블 또는 블록 끝 앞까지
    depth_at, depth = [], 0
    for row in rows:
        depth_at.append(depth)
        depth += row.count("{") - row.count("}")
    labels = [i for i, row in enumerate(rows) if CASE_HEAD.match(row) or DEFAULT_HEAD.match(row)]
    for position, i in enumerate(labels):
        end = len(rows) - 1
        for j in range(i + 1, len(rows)):
            if depth_at[j] < depth_at[i] or (j in labels and depth_at[j] == depth_at[i]):
                end = j - 1
                break
        # 묶음 끝의 닫는 줄(`switch`의 `}`)은 묶음이 아니다
        while end > i and rows[end].strip().startswith("}") and \
                rows[end].count("}") > rows[end].count("{") and depth_at[end] == depth_at[i]:
            end -= 1
        if end - i >= PRUNE_BLOCK:
            blocks.append((start + i + 1, start + end))
    out: set[int] = set()
    for low, high in sorted(blocks, key=lambda b: b[0] - b[1]):
        inner = set(range(low, high + 1))
        if inner & flow or inner & out or any(rows[n - start].lstrip().startswith("#") for n in inner):
            continue
        out |= inner
    return out


def _calls_in(index: dict | None, file: str, lines: set[int]) -> list[str]:
    """index 호출 표에서 `file`의 그 줄들에 있는 호출의 이름 — 이름 순."""
    return sorted(
        name
        for name, rows in (index or {}).get("calls", {}).items()
        if any(row["file"] == file and row["line"] in lines for row in rows)
    )


def _span(location: Location) -> set[int]:
    start = location.line_start or 1
    return set(range(start, (location.line_end or start) + 1))


def _condition_span(code: list[str], number: int) -> tuple[int, str]:
    """`code`(파일의 `code_only` 줄들)의 `number` 줄에서 시작하는 if/while/for/switch 조건 —
    (괄호가 닫히는 줄, 괄호 안 문자열). 여러 줄 조건은 `STATEMENT_LINES`줄까지."""
    cond, depth, opened, last = "", 0, False, number
    for k in range(number, min(len(code), number + STATEMENT_LINES) + 1):
        text = code[k - 1]
        for char in text[text.find("("):] if k == number else text:
            cond += char
            if char == "(":
                depth, opened = depth + 1, True
            elif char == ")":
                depth -= 1
                if opened and depth <= 0:
                    break  # 괄호가 닫히면 끝 — 한 줄 `if (x) use(y);`의 처리 문장까지 조건으로 읽었다
        last = k
        if opened and depth <= 0:
            break
    return last, cond


def _signature_text(read_source: Callable[[Location], str], head: Location, function: Location) -> str:
    """함수 머리만 싣는 블록의 원문 — 머리 끝 줄의 여는 `{`를 닫아 생략을 적는다. 머리만 실으면 `{`로
    끝나 본문이 잘린 코드처럼 보였다(검토 2026-10-10: ossec `_log`, mongoose `integrity_check`)."""
    text = read_source(head).rstrip()
    omitted = max(0, (function.line_end or head.line_end or 0) - (head.line_end or 0) - 1)
    marker = f"{{ /* ... body omitted ({omitted} lines) ... */ }}"
    # 여는 `{`는 머리의 마지막 코드 줄에서만 — 주석·문자열·앞줄의 `{`(매개변수 안 `struct {` 등)를 자르면
    # 머리가 반쪽이 됐다(리뷰 2026-10-10)
    rows = text.split("\n")
    code = code_only(text).split("\n")
    for index in range(len(code) - 1, -1, -1):
        if code[index].strip():
            brace = code[index].rfind("{")
            if brace >= 0 and not code[index][brace + 1:].strip():
                return "\n".join(rows[:index] + [rows[index][:brace] + marker])
            break
    return text + " " + marker


def _prologue(
    read_source: Callable[[Location], str], file: str, called: set[str]
) -> list[Location]:
    """`file`의 `#include` 줄과, 실린 코드가 부르는 함수의 파일 스코프 전방 선언 줄 — 연속한 줄끼리
    블록 하나. include가 표준 헤더 타입·함수(`size_t`·`memcpy`)가 어디서 오는지 보여 준다."""
    text = read_source(Location(file=file))
    if not text:
        return []
    raw = text.split("\n")
    code = code_only(text).split("\n")
    picked: list[int] = []
    depth = 0
    for number, (line, masked) in enumerate(zip(raw, code), 1):
        if depth == 0:
            if INCLUDE_LINE.match(line):
                picked.append(number)
            else:
                proto = PROTOTYPE.match(masked.strip())
                if proto and proto.group(1) in called:
                    picked.append(number)
        depth = max(0, depth + masked.count("{") - masked.count("}"))
    blocks: list[Location] = []
    for number in picked:
        last = blocks[-1] if blocks else None
        if last is not None and all(not raw[n - 1].strip() for n in range(last.line_end + 1, number)):
            blocks[-1] = Location(file=file, symbol="prologue", line_start=last.line_start,
                                  line_end=number)
        else:
            blocks.append(Location(file=file, symbol="prologue", line_start=number,
                                   line_end=number))
    return blocks


def attach(
    graph: GraphIndex,
    repo: Path,
    entry: tuple[str, int],
    lines: list[tuple[str, int]],
    options: dict | None = None,
    *,
    index: dict | None = None,
    system: SystemHeaders | None = None,
    anchors: set[tuple[str, int]] | None = None,
    dependency: set[tuple[str, int]] | None = None,
    after_target: set[tuple[str, int]] | None = None,
    entry_spans: list[tuple[str, str | None, int, int]] | None = None,
    backward: bool = False,
    cross_method_flow: list[dict] | None = None,
    input_evidence: dict | None = None,
) -> tuple[str, dict]:
    """슬라이스 하나의 Auditor `SOURCE`와 그 계측. `entry`는 (파일, 줄), `lines`는 슬라이스 줄.

    `index`는 raw index.json(정의 클로저가 하네스 로더가 모르는 표를 읽는다), `system`은 저장소 밖
    상수·타입을 찾을 시스템 헤더 — `None`이면 그 단계를 건너뛴다.

    **코드를 먼저 다 고르고 정의는 마지막에 그 전체에서 찾는다.** 예전에는 entry·슬라이스 함수만
    보고 정의를 찾은 뒤 피호출·호출자·쓰기 지점을 붙여서, 그 블록들이 쓰는 매크로·전역·타입은
    정의 없이 이름만 남았다(cJSON: `parse_value`의 `cJSON_NULL` · `cJSON_InitHooks`의
    `cJSON_free`·`cJSON_Hooks` · `cJSON_Delete`의 `cJSON_IsReference`)."""
    opts = {**DEFAULT_OPTIONS, **(options or {})}

    def read_source(target: Location) -> str:
        return read_symbol_source(repo, target)

    graph = _Visible(graph, read_source)
    with_definitions = opts["definitions"]
    hygiene = {"demote_nonproduct": True} if with_definitions else {}
    wrappers = opts["callee_wrappers"]
    counts = dict.fromkeys(
        (
            "definitions",
            "alias_definitions",
            "callees",
            "callee_signatures",
            "sink_callees",
            "wrapper_callees",
            "alias_callees",
            "indirect_callees",
            "indirect_skipped",
            "callers",
            "call_sites",
            "caller_depth",
            "caller_helpers",
            "write_windows",
            "write_sites_found",
            "tainted_uses",
            "prologue_lines",
        ),
        0,
    )

    anchor = entry_function(graph, *entry)
    # spec의 GT 함수 범위(MAP-C2)가 entry 줄을 품는데 인덱스의 가장 좁은 심볼이 그보다 넓으면(C++
    # `namespace peg {` 전체 57-289 — 인덱스가 헤더 안의 인라인 함수를 모른다) 그 범위를 진입 함수로
    # (검수 v39: 5c02d458 노이즈 약 90%)
    for span_file, span_symbol, low, high in entry_spans or []:
        if span_file == entry[0] and low <= entry[1] <= high and (
                anchor.line_start is None or anchor.line_start < low - 3 or (anchor.line_end or 0) > high + 3):
            anchor = Location(file=span_file, symbol=span_symbol or anchor.symbol, line_start=low, line_end=high)
            break
    # 비정상적으로 긴 entry(`SUSPECT_LINES`줄 초과)는 인덱서가 함수 경계를 잘못 읽은 것이다 — 매크로가 많은 파일에서
    # 한 함수가 파일 대부분(gnutls `_gnutls_send_finished` 878-3656, 2,778줄)으로 잡혔다(판정 2026-10-10 r15b: 확정 불가).
    # entry 줄에서 위로 열 0의 `{`와 그 머리, 아래로 열 0의 `}`를 찾아 그 범위로 줄인다
    if _length(anchor) > SUSPECT_LINES:
        lexical = _lexical_enclosing(read_source(Location(file=anchor.file)).split("\n"), entry[1])
        if lexical and lexical[1] - lexical[0] + 1 < _length(anchor) and \
                (anchor.line_start or 0) <= lexical[0] and lexical[1] <= (anchor.line_end or lexical[1]):
            anchor = Location(file=anchor.file, symbol=lexical[2] or anchor.symbol,
                              line_start=lexical[0], line_end=lexical[1])
    # entry가 제품 코드면 비제품 경로(tests/·docs/·examples/ …)의 슬라이스 줄은 싣지 않는다 — backward가
    # 호출자를 타고 시험·예제 프로그램의 main까지 갔다(held-out mongo-c-driver · picotcp)
    if not is_nonproduct_path(entry[0]):
        dropped = [p for p in lines if is_nonproduct_path(p[0])]
        lines = [p for p in lines if not is_nonproduct_path(p[0])]
    else:
        dropped = []
    symbols = {file: graph.symbols_for(file) or [] for file in {file for file, _ in lines}}
    spanned: dict[Location, None] = {}
    uncovered = 0
    for file, line in lines:
        if file == anchor.file and _spans(anchor, line):
            continue
        symbol = _narrowest_symbol(symbols[file], line)
        if symbol is None:
            uncovered += 1
        else:
            spanned[symbol] = None
    anchor_text = read_source(anchor)
    anchor_end = (anchor.line_start or 1) + anchor_text.count("\n")

    seen: list[Location] = [anchor]
    # 섹션별 (위치, 원문, part) — 마지막에 entry · 슬라이스 함수 · 머리 · 정의 · 피호출 · 호출자 ·
    # 쓰기 지점 순으로 잇는다.
    sections: dict[str, list[tuple[Location, str, dict]]] = {
        key: [] for key in ("code", "callee", "caller", "write")
    }
    sections["code"].append((anchor, anchor_text, _part("entry_function", anchor)))

    def add(section: str, kind: str, location: Location, text: str | None = None, **extra) -> bool:
        if any(_overlaps(location, other) for other in seen):
            return False
        seen.append(location)
        sections[section].append((
            location, read_source(location) if text is None else text,
            {**_part(kind, location), **extra},
        ))
        return True

    def add_window(kind: str, site: Location, window: Location | None = None, **extra) -> bool:
        """쓰기·사용·해제 지점 하나 — 감싼 함수가 짧으면 함수 전체, 길면 그 함수의 **골라 실은 줄**(머리 + 지점
        앞뒤 창, 문장 시작·괄호 짝·닫는 `}`까지). 창을 따로 떼어 실으면 문장 중간에서 시작하고 닫는 괄호 없이
        끊기고, 머리 블록과 떨어져 다른 함수 소속처럼 보였다(검수 2026-10-10 r7). 그 함수가 이미 골라 실렸으면
        그 블록에 줄을 더한다."""
        blocks_, enclosing = _site_blocks(graph, read_source, site, WRITE_LINES)
        # 지점 줄 — 정의는 이 줄의 이름만 찾는다(창 전체가 아니라, 검수 2026-10-10 r9)
        extra = {**extra, "site_lines": [site.line_start]}
        if window is not None:
            blocks_ = [window]
        if enclosing is None or enclosing.line_start is None or blocks_ == [enclosing]:
            added = False
            for block in blocks_:
                added |= add("write", kind, block, **extra)
            return added
        wanted: set[int] = set()
        for block in blocks_:
            wanted |= _span(block)
        wanted &= _span(enclosing)
        keep =set(range(enclosing.line_start, _header_end(read_source, enclosing) + 1)) | wanted
        for key in sections:
            for position, (location, _text, part) in enumerate(sections[key]):
                if location.file != enclosing.file or not _overlaps(location, enclosing):
                    continue
                if not part.get("kept_lines") or location.line_start != enclosing.line_start:
                    return False  # 이미 함수째 보인다
                if wanted <= set(part["kept_lines"]):
                    return False
                merged = _balanced(read_source, location, _statement_starts(
                    read_source, location, set(part["kept_lines"]) | wanted))
                merged |= {location.line_end or location.line_start}
                sites_ = sorted(set(part.get("site_lines") or ()) | set(extra["site_lines"]))
                sections[key][position] = (location, _elided(read_source, location, merged),
                                           {**part, "kept_lines": sorted(merged), "site_lines": sites_})
                return True
        keep = _balanced(read_source, enclosing, _statement_starts(read_source, enclosing, keep))
        keep |= {enclosing.line_end or enclosing.line_start}
        return add("write", kind, enclosing, text=_elided(read_source, enclosing, keep),
                   kept_lines=sorted(keep), **extra)

    slice_by_file: dict[str, set[int]] = {}
    for file, line in lines:
        slice_by_file.setdefault(file, set()).add(line)
    slice_names = {name for name, is_field in slice_line_refs(read_source, lines) if not is_field}
    shown_lines: dict[tuple[str, str | None], set[int]] = {}
    # 가드로만 실린 줄(조건·처리 문장) — 보여 주기만 하고 정의·피호출은 찾지 않는다. v32에서 가드 조건의
    # 이름(curl `conn->httpversion`·`HTTPREQ_*`)이 구조체 필드 타입과 enum을 끌어와 출력이 19% 늘었다
    guard_only: set[tuple[str, int]] = set()

    def guarded(file: str, guards: set[int], others: set[int]) -> set[int]:
        guard_only.update((file, n) for n in guards - others)
        return guards
    # entry 밖의 슬라이스 함수 — 짧으면 전체, 길면 **머리 + 슬라이스 줄만**. 슬라이스가 호출자를 타고
    # 올라가 지나간 큰 함수(jhead `ProcessFile` 454줄 · `main` 317줄)가 통째로 붙던 것이 노이즈의
    # 대부분이었다. 그 함수에서 취약점에 닿는 줄(데이터 의존 · 그 문맥)은 슬라이스 줄에 이미 있다.
    carried = [anchor]
    # 역방향이면 호출자로 거슬러 올라간 긴 함수를 사슬 인자가 흐르는 줄로 줄인다(`_narrow_ascended`)
    plan = _ascent_plan(read_source, anchor, list(spanned), dependency if dependency is not None else set(lines)) \
        if backward and index is not None and opts.get("narrow_ascent", 1) else {}
    sink_fields = set(FIELD_OF.findall(code_only(read_source(Location(file=entry[0], line_start=entry[1],
                                                                      line_end=entry[1])))))
    carried_names = {_short(s.symbol) for s in spanned}
    for symbol in sorted(spanned, key=lambda symbol: (symbol.file, symbol.line_start or 0)):
        carried.append(symbol)
        if _length(symbol) <= SMALL_FUNCTION:
            add("code", "slice_function", symbol)
            continue
        # 데이터 의존·시작 줄만(있으면) — 문맥 보충 줄(감싼 제어문·조건·선언)까지 실으면 백워드가 거슬러
        # 올라간 먼 호출자가 다시 커졌다(jhead `create_EXIF_internal` 142줄 · `saveAttributes`).
        chosen = dependency if dependency is not None else set(lines)
        flow = {n for f, n in chosen if f == symbol.file} & _span(symbol)
        must = {n for f, n in set(anchors or ()) if f == symbol.file} & _span(symbol)
        if symbol in plan and not must:
            narrowed = _narrow_ascended(read_source, graph, index, symbol, flow, plan[symbol], sink_fields,
                                        carried_names, opts.get("cwe"), must)
            if narrowed is not None and len(narrowed) < len(flow):
                counts["ascent_narrowed"] = counts.get("ascent_narrowed", 0) + 1
                counts["ascent_lines_dropped"] = counts.get("ascent_lines_dropped", 0) + len(flow - narrowed)
                flow = narrowed
        keep = flow | set(
            range(symbol.line_start or 1, _header_end(read_source, symbol) + 1)
        ) | {symbol.line_end or symbol.line_start or 1}
        # 데이터 흐름 변수를 검사하는 가드와 그 조기 탈출(P1·P2) — 패치 샘플에서는 이 검사가 수정의 근거다
        keep |= guarded(symbol.file, _guard_lines(read_source, symbol, _flow_names(read_source, symbol.file, flow),
                                                  GUARD_LIMIT), keep)
        keep = _statement_starts(read_source, symbol, keep)
        # 실린 조건의 처리 본문(G1)
        keep |= guarded(symbol.file, _complete_guards(read_source, symbol, keep), keep)
        keep = _balanced(read_source, symbol, keep)
        # 블록 하나 — 건너뛴 줄 자리에 `/* ... N lines omitted ... */`. 연속 구간마다 머리를 달면
        # 함수 하나가 수십 조각으로 쪼개져 읽기 어려웠다(jhead `ProcessFile` 50조각).
        add("code", "slice_lines", symbol, text=_elided(read_source, symbol, keep),
            kept_lines=sorted(keep), **({"narrowed_ascent": True} if symbol in plan and flow != (
                {n for f, n in chosen if f == symbol.file} & _span(symbol)) else {}))
        # 피호출은 실린 줄의 호출만 — 안 실린 줄의 호출 대상 본문은 부르는 곳 없이 떠 있었다
        shown_lines[(symbol.file, symbol.symbol)] = keep

    # 긴 entry(`ENTRY_LINES`줄 초과) — 통째 대신 슬라이스 함수와 같이: 머리·끝, 데이터 의존·시작/끝 줄, 그 흐름 변수의
    # 가드와 처리 본문, 문장 시작·괄호 짝. 무관한 갈래(openssl RSA·KRB5·DH 갈래 248줄, gnutls 핸드셰이크 상태 기계)가
    # 출력의 큰 몫이었다(판정 2026-10-10 r14b: 노이즈 80~92%의 절반이 entry 본문)
    # 데이터 의존·시작 줄을 하나도 품지 않은 큰 블록(`{…}` 본문·`case` 묶음, `PRUNE_BLOCK`줄 이상)만 뺀다 — 직선 코드와
    # 조건 머리는 그대로(의존 줄만 남기면 수정 줄인 새 검사·GT 원인 줄이 빠졌다, 2026-10-10 r15 시험: 수정 줄 42/50).
    # 1단계가 sink를 못 찾아 의존 줄이 없으면 하지 않는다
    entry_dep = {n for f, n in (dependency or ()) if f == anchor.file} & _span(anchor) - {entry[1]}
    # 기본 꺼짐 — 검수(2026-10-10 rounds_r12_eval.md)가 r15b에서 openssl DH 갈래의 `sess_cert == NULL` 검사가 빠져 판정이
    # 내려간 것을 짚어 보류했다. 켜면 시작·끝 줄의 필드(`->f`)를 언급하는 줄도 흐름으로 쳐 그 블록은 남긴다
    if opts.get("narrow_entry", 0) and anchor.line_start is not None and _length(anchor) > ENTRY_LINES \
            and entry_dep:
        flow = entry_dep | ({n for f, n in (anchors or ()) if f == anchor.file} | {entry[1]}) & _span(anchor)
        sink_fields_: set[str] = set()
        for f, n in set(anchors or ()) | {(anchor.file, entry[1])}:
            if f == anchor.file:
                sink_fields_ |= set(FIELD_OF.findall(code_only(read_source(Location(file=f, line_start=n, line_end=n)))))
        if sink_fields_:
            mention = re.compile(r"(?:->|\.)\s*(?:" + "|".join(map(re.escape, sorted(sink_fields_))) + r")\b")
            body_rows = code_only(anchor_text).split("\n")
            flow |= {anchor.line_start + i for i, row in enumerate(body_rows) if mention.search(row)}
        keep = _span(anchor) - _empty_blocks(read_source, anchor, flow)
        if len(keep) < 0.8 * _length(anchor):
            location, _text, part = sections["code"][0]
            sections["code"][0] = (location, _elided(read_source, location, keep),
                                   {**part, "kept_lines": sorted(keep), "entry_narrowed": True})
            shown_lines[(anchor.file, anchor.symbol)] = keep
            counts["entry_lines_dropped"] = _length(anchor) - len(keep)

    def carry_callee(callee: Location, kind: str) -> bool:
        if add("callee", kind, callee):
            counts["callees"] += 1
            return True
        return False

    def through_wrapper(callee: Location, depth: int, kind: str = "wrapper_callee") -> None:
        if not wrappers or depth > WRAPPER_DEPTH or not _is_short(callee):
            return
        inner = graph.callees_of(callee, **hygiene) or []
        if not 1 <= len(inner) <= WRAPPER_CALLS:
            return
        # 가변 인자 래퍼(`TIFFGetField(…, ...)` → `TIFFVGetField(…, ap)`)는 단계로 세지 않는다 — 인자를
        # `va_list`로 넘기기만 해서 실제 일은 그 안쪽이 한다(검수 2026-10-10 r7: libtiff 안쪽이 머리만)
        step = 0 if _variadic(read_source, callee) else 1
        for target in inner:
            # 래퍼 너머도 피호출과 같은 크기 규칙 — 길면 머리만. 10줄 래퍼가 57줄 함수 본문을 통째로 끌어왔다
            # (2026-10-10 1회차 jsish `jsi_ValueCopyMove`, 리뷰 「크기 상한이 없는 통로」)
            if _length(target) > SMALL_FUNCTION:
                signature(target)
                continue
            if carry_callee(target, kind):  # 사슬 전체가 처음 피호출의 종류(흐름 여부)를 물려받는다
                counts["wrapper_callees"] += 1
                through_wrapper(target, depth + step, kind)

    # 피호출 — 슬라이스 줄의 호출(entry 포함)과 entry의 짧은 보조 함수(`HELPER_LINES`줄 이하). 본문은
    # `SMALL_FUNCTION`줄 이하만, 그보다 길면 **머리(시그니처)만** — 큰 피호출로 흐르는 데이터 의존은
    # 역방향 추적이 이미 그 함수의 슬라이스 줄로 가져왔다(jhead `ProcessGpsInfo` 209줄 · `DoCommand`).
    def signature(function: Location) -> bool:
        if function.line_start is None:
            return False
        head = Location(file=function.file, symbol=function.symbol, line_start=function.line_start,
                        line_end=_header_end(read_source, function))
        if add("callee", "callee_signature", head, text=_signature_text(read_source, head, function)):
            counts["callee_signatures"] += 1
            return True
        return False

    # 긴 피호출이라도 데이터 의존 줄에서 **오염된 인자**(다른 의존 줄의 변수)를 받으면, 머리만 붙이지
    # 않고 그 파라미터가 흐르는 줄을 골라 붙인다 — held-out ksmbd `ksmbd_auth_ntlmv2`(실제 쓰기)·
    # picotcp `pico_transport_receive`(첫 free)가 시그니처만 남아 확정할 수 없었다.
    def call_taint(owner: Location, callee: Location) -> set[str]:
        """`owner`의 데이터 의존 줄에서 `callee`를 부를 때 오염된 인자(다른 의존 줄의 변수)를 받는
        파라미터 이름들 — 없으면 빈 집합."""
        if index is None or not dependency or not callee.symbol:
            return set()
        dep = {n for f, n in dependency if f == owner.file} & _span(owner)

        def statement(line: int) -> set[int]:
            # 호출 문장의 줄들 — `;`까지(인자가 여러 줄이면 데이터 의존 줄은 인자 줄일 수 있다: held-out
            # ksmbd `ret = ksmbd_auth_ntlmv2(conn, sess, …,` 618행, 의존 줄은 `nt_len - …` 620행)
            out = set()
            for number in range(line, min(line + STATEMENT_LINES, (owner.line_end or line)) + 1):
                out.add(number)
                if ";" in code_only(read_source(Location(file=owner.file, line_start=number,
                                                         line_end=number))):
                    break
            return out

        rows = [r for r in index.get("calls", {}).get(_short(callee.symbol), [])
                if r["file"] == owner.file and statement(r["line"]) & dep]
        params = _param_names(read_source, callee)
        hit: set[str] = set()
        for row in rows:
            tracked = set()
            for number in dep - statement(row["line"]):
                tracked |= set(IDENT_RE.findall(code_only(read_source(
                    Location(file=owner.file, line_start=number, line_end=number)))))
            tracked -= CALL_KEYWORDS | {_short(callee.symbol)}
            for position, arg in enumerate(row.get("args", [])):
                if position < len(params) and set(IDENT_RE.findall(code_only(arg))) & tracked:
                    hit.add(params[position])
        hit.discard("")
        return hit

    def result_lines(function: Location) -> bool:
        """긴 조회·검사 함수(`SMALL_FUNCTION`줄 초과) — 결과를 정하는 줄만: `return` 줄, 할당 줄, 처리 가드, 머리·끝
        줄과 괄호 짝. `RESULT_LINES`줄을 넘으면 실패(머리만). 본문 통째(80줄까지)는 neomutt·jasper에서 40~60줄 함수가
        줄줄이 붙어 +15%였다(2026-10-10 r12)."""
        if function.line_start is None:
            return False
        rows = code_only(read_source(function)).split("\n")
        start = function.line_start
        pick = {start + i for i, row in enumerate(rows)
                if re.match(r"\s*return\b", row) or EVIDENCE_LINE.search(row) and ALLOC_CALL.search(row)}
        pick = _statement_starts(read_source, function, pick)
        pick |= _handled_guard_units(read_source, function, set(_span(function)))
        keep = pick | set(range(start, _header_end(read_source, function) + 1)) | {function.line_end or start}
        keep = _balanced(read_source, function, keep)
        if len(keep) > RESULT_LINES:
            return False
        if add("callee", "result_callee", function, text=_elided(read_source, function, keep),
               kept_lines=sorted(keep)):
            counts["result_callees"] = counts.get("result_callees", 0) + 1
        return True

    # sink 쪽 필드 — entry의 데이터 의존 줄과 시작·끝 줄에 나오는 `->f`·`.f`의 f(curl `host->name`의 name)
    sink_fields: set[str] = set()
    for file, number in {(f, n) for f, n in (dependency or set()) | (anchors or set())
                         if f == anchor.file and _spans(anchor, n)}:
        sink_fields |= set(re.findall(r"(?:->|\.)\s*([A-Za-z_]\w*)", code_only(read_source(
            Location(file=file, line_start=number, line_end=number)))))

    def tainted_callee(owner: Location, callee: Location) -> bool:
        hit = call_taint(owner, callee)
        if not hit:
            return False
        flow = _tainted_lines(read_source, callee, hit)
        # 문맥 포인터(`data`·`conn`)는 모든 호출에 넘어가 파라미터가 함수 대부분에 퍼진다 — 그러면 sink 쪽
        # 필드를 다루는 줄만(held-out curl `parseurlandfillconn` 340줄 → `conn->host.name = …`). entry
        # 밖의 호출은 늘 그 필드 줄만 — v24는 ipmitool 6개(830줄 중 348줄)·jasper 리프팅 함수가 붙었다
        if owner is not anchor or len(flow) > TAINTED_LINES:
            flow = {n for n in flow if set(re.findall(
                r"(?:->|\.)\s*([A-Za-z_]\w*)", code_only(read_source(Location(
                    file=callee.file, line_start=n, line_end=n))))) & sink_fields}
            # 그 줄에서 값을 받은 변수가 쓰이는 줄도(`portptr = strrchr(conn->host.name, ':')` →
            # `*portptr = '\0'` — 포트를 잘라 hostname을 끝내는 줄, curl 확정의 근거였다)
            derived = set()
            for n in flow:
                assign = re.match(r"\s*(?:[A-Za-z_][\w\s\*]*?[\s\*])?\**\s*([A-Za-z_]\w*)\s*=(?!=)",
                                  code_only(read_source(Location(file=callee.file, line_start=n,
                                                                 line_end=n))))
                if assign:
                    derived.add(assign.group(1))
            if derived:
                flow |= _tainted_lines(read_source, callee, derived)
            if not flow or len(flow) > TAINTED_LINES:
                return False  # 머리만
        flow = _statement_starts(read_source, callee, flow)
        flow |= guarded(callee.file, _guard_lines(read_source, callee, _flow_names(read_source, callee.file, flow),
                                                  GUARD_LIMIT), flow)
        flow |= guarded(callee.file, _complete_guards(read_source, callee, flow), flow)
        keep = flow | set(
            range(callee.line_start or 1, _header_end(read_source, callee) + 1)
        ) | {callee.line_end or callee.line_start or 1}
        keep = _balanced(read_source, callee, keep)
        if add("callee", "tainted_callee", callee, text=_elided(read_source, callee, keep),
               kept_lines=sorted(keep), tainted_params=sorted(hit)):
            counts["tainted_callees"] = counts.get("tainted_callees", 0) + 1
            return True
        return False

    # 시작 줄(sink/source 줄) 자체가 부르는 함수는 취약점의 일부다 — 크기와 상관없이 본문 전체, 그
    # 안의 피호출은 `SMALL_FUNCTION`줄 이하면 본문·길면 머리. ngiflib는 sink가 `LoadGif(gif)` 호출이고
    # 실제 범위 밖 읽기는 그 안의 `GetByte`였다 — 큰 피호출을 머리만 붙이는 규칙이 그 근거를 지웠다.
    # 시작 줄과 반대쪽 끝 줄(닿은 sink·source) 모두 — forward의 시작 줄은 source라, sink 줄의 호출
    # (ngiflib `LoadGif(gif)`)은 반대쪽 끝에서만 보인다.
    if opts["callee_hops"] and index is not None:
        ends: dict[str, set[int]] = {}
        for file, number in {entry, *(anchors or set())}:
            ends.setdefault(file, set()).add(number)
        targets = [
            function
            for file, numbers in sorted(ends.items())
            for function in graph.functions_named(
                _calls_in(index, file, numbers), file, **hygiene) or []
        ]
        end_lines = {(file, number) for file, numbers in ends.items() for number in numbers} | set(lines)

        def const_args(function: Location) -> dict[int, set[int]]:
            """실린 줄에서 `function`을 부르는 곳의 인자 위치별 상수 값 — 모든 호출이 상수인 위치만."""
            rows = [r for r in index.get("calls", {}).get(_short(function.symbol), [])
                    if (r["file"], r["line"]) in end_lines]
            if not rows:
                return {}
            out: dict[int, set[int]] = {}
            for k in range(max(len(r.get("args", [])) for r in rows)):
                values = {_const_value(index, read_source, r["args"][k]) if k < len(r.get("args", [])) else None
                          for r in rows}
                if None not in values:
                    out[k] = values
            return out

        def sink(function: Location, depth: int) -> None:
            if function == anchor:
                return
            # 상수 인자 갈래 가지치기 — 실린 호출이 모두 상수(`ENC_ASCII`)를 넘기면 그 값이 안 닿는 `case` 묶음은 본문·
            # 피호출·정의에서 뺀다. 문자셋 표 17개와 갈래별 도우미가 출력의 69%였다(검토 2026-10-10 r12 wireshark)
            span = set(_span(function))
            dead, note = _const_dead_lines(index, read_source, function, const_args(function)) \
                if _length(function) > SMALL_FUNCTION else (set(), {})
            marked_here = {n for f, n in set(dependency or ()) | set(anchors or ()) if f == function.file}
            if dead & marked_here or (opts.get("cwe") in LIFETIME_CWES and any(
                    RELEASE_CALL.fullmatch(c) for c in _calls_in(index, function.file, dead))):
                dead, note = set(), {}
            if dead:
                keep = _balanced(read_source, function, span - dead)
                if not add("callee", "sink_callee", function, text=_elided(read_source, function, keep),
                           kept_lines=sorted(keep), const_pruned=note):
                    return
                counts["callees"] += 1
                counts["const_pruned"] = counts.get("const_pruned", 0) + 1
                span = keep
            elif not carry_callee(function, "sink_callee"):
                return
            counts["sink_callees"] += 1
            live_names = set(_calls_in(index, function.file, span))
            inners = [c for c in graph.callees_of(function, **hygiene) or [] if _short(c.symbol) in live_names]
            known = {c.symbol for c in inners}
            # 본문의 호출 중 인덱스가 정의를 모르는 것 — 같은 파일에서 어휘로 찾는다
            for name in _calls_in(index, function.file, span):
                if name not in known and not (index.get("functions") or {}).get(name):
                    found = _lexical_function(read_source, function.file, name)
                    if found is not None:
                        inners.append(found)
            # 그 함수의 파라미터(와 파라미터에서 값을 받은 지역)를 인자로 받는 호출만 따라간다 — `malloc(size)` 안의 락·
            # 스핀락·인터럽트 마스킹(`malloc_lock()`)처럼 인자와 무관한 내부 호출이 출력의 큰 몫이었다(판정 2026-10-10 r19b
            # optee 노이즈 85%). 호출 행을 모르는 함수(어휘로 찾은 것)는 둔다
            params = set(_param_names(read_source, function)) - {""}
            if params and function.line_start is not None:
                derived = set(params)
                body_rows = code_only(read_source(function)).split("\n")
                for _round in range(2):
                    for row in body_rows:
                        lhs = LHS_NAME.match(row)
                        if lhs and set(IDENT_RE.findall(row[lhs.end():])) & derived:
                            derived.add(lhs.group(1))

                def takes(inner: Location) -> bool:
                    rows_ = [r for r in index.get("calls", {}).get(_short(inner.symbol), [])
                             if r["file"] == function.file and _spans(function, r["line"])]
                    return not rows_ or any({_arg_root(a) for a in r.get("args", [])} & derived for r in rows_)

                kept_inners = [c for c in inners if takes(c)]
                counts["sink_inner_dropped"] = counts.get("sink_inner_dropped", 0) + len(inners) - len(kept_inners)
                inners = kept_inners
            step = 0 if _variadic(read_source, function) else 1
            for inner in inners:
                if _is_short(function) and depth < WRAPPER_DEPTH:
                    # 짧은 래퍼면 그 안쪽이 실제 일을 한다 — 크기와 상관없이 본문(jq
                    # `decNumberToString` 2줄 → `decToString`이 몇 글자를 쓰는지 정한다)
                    sink(inner, depth + step)
                elif _length(inner) <= SMALL_FUNCTION:
                    if carry_callee(inner, "callee"):
                        through_wrapper(inner, 1)
                else:
                    signature(inner)

        for function in targets:
            sink(function, 1)

        # 헤더 안 한 줄 인라인 메서드(`inline void SetEncrypt(PdfEncrypt* e) { m_Encrypt = e; }`) — 색인에 없어 시작 줄의
        # `obj->SetEncrypt(m_Encrypt.get())`가 무엇을 하는지 안 보였다(판정 2026-10-10 r13b~r15b podofo 조건부). 수명
        # 계열에서, 시작 줄 메서드 호출 하나가 풀리지 않았을 때만: entry 디렉터리 헤더에서 그 이름의 한 줄 정의가 하나뿐이고
        # 본문이 파라미터를 멤버에 넣으면 그 줄, 그 멤버의 선언 줄, 같은 이름 .cpp에서 그 멤버로 부르는 곳(`FIELD_SITES`곳)
        if opts.get("cwe") in LIFETIME_CWES and anchor.file.lower().endswith((".cpp", ".cc", ".cxx")) \
                and not counts.get("sink_callees"):
            _inline_setter(index, graph, read_source, anchor, sorted(anchors or set()), add, add_window, counts)

    if opts["callee_hops"]:
        for owner in carried:
            condition_names: set[str] = set()
            if index is None:
                callees = graph.callees_of(owner, **hygiene) or []
            else:
                visible_lines = shown_lines.get(
                    (owner.file, owner.symbol), slice_by_file.get(owner.file, set())) & _span(owner)
                on_slice = visible_lines - {n for f, n in guard_only if f == owner.file}
                # 실린 조건(가드·감싸는 if/while) 안의 호출 — 조건의 뜻을 정하는 함수다(검수 2026-10-10: media
                # `is_secure_session()` 정의가 없어 조건부). 가드 본문(처리 문장)의 호출은 여전히 뺀다
                heads = _condition_lines(read_source, owner,
                                         visible_lines | {n for f, n in guard_only if f == owner.file})
                if owner is anchor:
                    # 시작·끝 줄을 거느리는 조건 — 감싼 블록의 머리와 중괄호 없는 본문 바로 위의 머리. sink가
                    # `if (_gnutls_abort_handshake(session, ret) == 0) STATE = STATE0;`의 본문인데 그 조건 줄이 슬라이스
                    # 줄이 아니라 판정 함수 본문이 빠졌다(판정 2026-10-10 r16 gnutls 확정 불가)
                    heads |= _controlling_heads(read_source, owner, ({n for f, n in (anchors or ()) if f == owner.file}
                                                                     | {entry[1]}) & _span(owner))
                # 조건 괄호 안의 호출만 — 한 줄 `if (x) foo();`의 처리 문장 호출이 같이 잡혔다(리뷰 2026-10-10)
                file_code = code_only(read_source(Location(file=owner.file))).split("\n")
                in_condition: set[str] = set()
                for number in heads:
                    if GUARD_HEAD.match(file_code[number - 1] if number <= len(file_code) else ""):
                        in_condition |= set(CALL_NAME.findall(_condition_span(file_code, number)[1]))
                condition_names = set(_calls_in(index, owner.file, heads)) & in_condition
                names = sorted(set(_calls_in(index, owner.file, on_slice)) | condition_names)
                callees = [
                    c for c in graph.functions_named(names, owner.file, **hygiene) or []
                    if not (c.file == owner.file and c.symbol == owner.symbol)
                ]
                # 조건 속 호출 중 색인이 정의를 모르는 것(매크로가 많은 파일에서 파싱이 깨진 함수) — 같은 파일에서 어휘로
                # (판정 2026-10-10 r17 gnutls `_gnutls_abort_handshake`가 색인에 없었다)
                for name in sorted(condition_names - {_short(c.symbol) for c in callees}):
                    if not (index.get("functions") or {}).get(name):
                        found = _lexical_function(read_source, owner.file, name)
                        if found is not None and found not in callees:
                            callees.append(found)
                if owner is anchor:
                    callees += [
                        c for c in graph.callees_of(owner, **hygiene) or []
                        if _length(c) <= HELPER_LINES and c not in callees
                    ]
                    # entry 안에서 시작·끝 줄의 지역 변수를 인자로 받는 호출(`copy_in_attrs(…, param_count, params)` —
                    # 넘친 버퍼에 실제로 쓰는 함수), 데이터 의존 줄의 변수에 결과를 넣는 호출(`*ppir = more_pkt()` — 그
                    # 포인터가 NULL일 수 있는지). 그 줄이 1단계 슬라이스 줄이 아니라 피호출에서 빠졌다(판정 2026-10-10 r13b:
                    # optee·ntp 조건부)
                    takers, feeders = _entry_flow_calls(index, read_source, graph, anchor, anchors or set(),
                                                        dependency if dependency is not None else set(lines))
                    callees += [c for c in graph.functions_named(sorted(takers | feeders), owner.file, **hygiene) or []
                                if c not in callees and not (c.file == owner.file and c.symbol == owner.symbol)]
                    condition_names |= feeders
            # 실린 줄에서 변수를 주소로 넘기는 호출 — 그 변수를 채우는 함수라 본문이 필요하다
            # (WavPack `read_metadata_buff(&wpmd, …)`가 wpmd의 byte_length·data를 정한다)
            fillers = set()
            if index is not None:
                shown = (shown_lines.get((owner.file, owner.symbol),
                                         slice_by_file.get(owner.file, set())) & _span(owner)) - {
                    n for f, n in guard_only if f == owner.file}
                fillers = {
                    name for name, rows in index.get("calls", {}).items()
                    for row in rows
                    if row["file"] == owner.file and row["line"] in shown
                    and any(re.match(r"\s*&", a) for a in row.get("args", []))
                }
            # 반환값을 쓰는 조회·검사 함수 — 데이터 의존·시작 줄에서 결과를 받거나(`h = findBufferHeader(id)`) 실린
            # 조건에서 결과를 검사하는(`if (!xcf_load_old_path(info, image))`) 호출. 결과가 무엇일 수 있는지(NULL·
            # 실패)가 판정 근거다 — 본문이 없어 조건부였다(검수 2026-10-10 r9·r10: Android av 판정이 이것으로 올랐다,
            # gimp patch는 수정 줄이 검사하는 함수 본문이 없었다)
            returners: set[str] = set()
            if index is not None:
                data_lines = {n for f, n in (set(dependency or ()) | set(anchors or ())) if f == owner.file} \
                    & _span(owner)
                for name, rows in index.get("calls", {}).items():
                    for row in rows:
                        if row["file"] != owner.file or row["line"] not in data_lines:
                            continue
                        code = code_only(read_source(Location(file=owner.file, line_start=row["line"],
                                                             line_end=row["line"])))
                        if re.search(rf"=\s*(?:\([^()]*\)\s*)?{re.escape(name)}\s*\(", code) or \
                                re.search(rf"\breturn\s*\(?\s*{re.escape(name)}\s*\(", code):
                            returners.add(name)
                returners |= condition_names
            for callee in callees:
                # entry 밖(백워드가 지나간 호출자 등)에서는 오염된 인자를 받는 호출만 — 그 함수의 실린 줄이
                # 부르는 함수를 다 붙이면 held-out curl `create_conn`에서 피호출 30여 개(연결 캐시·목록
                # 함수)가, libmspack `kwajd_open`에서 `kwajd_close`가 붙었다
                if (owner is not anchor and dependency and not call_taint(owner, callee)
                        and _short(callee.symbol) not in condition_names):
                    continue
                if _length(callee) <= SMALL_FUNCTION or (
                    _short(callee.symbol) in fillers and _length(callee) <= PROVENANCE_CALLEE_LINES
                ):
                    # 오염된 인자를 받는 호출(슬라이스 흐름이 들어가는 곳)과 그 래퍼 사슬은 연결 블록 — 근거 줄
                    # 정책이 보조 블록처럼 증거 줄만 남기면 흐름이 실제로 닿는 줄이 빠졌다(wt fuzz-eval:
                    # `eval` → `evalPluralCase` 21줄에서 `parse(...)`가 생략)
                    flow = bool(call_taint(owner, callee))
                    if carry_callee(callee, "flow_callee" if flow else "callee"):
                        through_wrapper(callee, 1, "flow_wrapper_callee" if flow else "wrapper_callee")
                elif not tainted_callee(owner, callee) and not (
                        _short(callee.symbol) in returners and result_lines(callee)):
                    signature(callee)
        if opts["indirect_callees"]:
            for owner in [anchor]:  # 함수 포인터 대상은 entry의 호출만(위와 같은 이유)
                for _field, targets in graph.indirect_calls_of(owner, **hygiene) or []:
                    # 같은 파일의 대상만 — 필드 이름(`read`)이 같다는 것만으로 다른 모듈의 구현(libmspack
                    # kwajd의 `sys->read`에 cabd.c `cabd_sys_read` 63줄·oabd.c)이 붙었다
                    fresh = [t for t in targets if t.file == owner.file
                             and not any(_overlaps(t, o) for o in seen)]
                    if len(fresh) > INDIRECT_MAX:
                        counts["indirect_skipped"] += 1
                        continue
                    for target in fresh:
                        if _length(target) > SMALL_FUNCTION:  # 피호출과 같은 크기 규칙
                            signature(target)
                            continue
                        if carry_callee(target, "indirect_callee"):
                            counts["indirect_callees"] += 1
                            through_wrapper(target, 1)

    # 호출자 사슬 — entry를 부르는 곳, 그리고 부른 함수가 `static`(파일 안 전용)이면 그것을 부르는
    # 곳까지 올라간다. 외부에 열린 함수에서 멈춘다: 입력은 거기서 들어온다(cJSON: parse_string ←
    # parse_value ← cJSON_ParseWithOpts).
    # 외부에 열린 함수에서 사슬이 끝나면 거기서부터 **지점 하나씩** `SPINE_DEPTH`단 더 올라간다(척추) —
    # 입력이 실제로 어디서 오는지(cJSON: cJSON_ParseWithOpts ← cJSON_Parse ← test.c doit ← dofile:
    # 파일 내용을 malloc(len+1)+NUL로 읽어 넘김)가 그 위에 있다. 지점을 하나만 따라가므로 호출자가 많은
    # 공개 API에서도 퍼지지 않고, 척추 블록의 보조 함수 본문은 붙이지 않는다.
    caller_lines = opts["caller_lines"]
    # 척추 줄기 — 외부에 열린 frontier마다 줄기가 하나씩 생겼다(검수 개선안 AX2: 11c1848d frontier 3개 →
    # 척추 블록 5개, mongo 시험 main 6개). `spine_chains`(기본 1)개만 남긴다: 인자 출처에 할당·읽기가
    # 있는 줄기 → 제품 경로만 지나는 줄기 → 출처 점수 → 먼저 생긴 줄기 순. 0이면 예전처럼 전부
    spine_cands: dict[tuple[str, str | None], list[tuple[str, str, Location, dict]]] = {}
    spine_origin: dict[tuple[str, str | None], int] = {}

    def put(section: str, kind: str, location: Location, chain, **extra) -> bool:
        if kind == "caller_spine" and opts.get("spine_chains", 1):
            spine_cands.setdefault(chain, []).append((section, kind, location, extra))
            return False
        return add(section, kind, location, **extra)

    if caller_lines and anchor.symbol:
        frontier: list[tuple[Location, int | None, tuple | None]] = [(anchor, None, None)]
        climbed = {(anchor.file, anchor.symbol)}
        for depth in range(1, CALLER_DEPTH + SPINE_DEPTH + 1):
            nxt: list[tuple[Location, int | None, tuple | None]] = []
            for function, spine, chain in frontier:
                if depth > 1 and spine is None and not _is_static(read_source, function):
                    spine = SPINE_DEPTH
                    chain = (function.file, function.symbol)
                if spine == 0 or (spine is None and depth > CALLER_DEPTH):
                    continue
                sites = graph.call_sites_of(function, **hygiene) or []
                site_names: dict[tuple[str, int], str] = {}
                if not sites and index is not None and spine != 0 and depth > 1:
                    # 함수 포인터로만 불리는 함수(콜백 등록) — 같은 타입 변수·묶인 필드로 부르는 곳
                    indirect = _indirect_sites(index, function)
                    sites = [site for site, _called in indirect]
                    site_names = {(site.file, site.line_start or 0): called for site, called in indirect}
                    counts["indirect_caller_sites"] = counts.get("indirect_caller_sites", 0) + len(sites)
                counts["call_sites"] += len(sites)
                # 제품 경로의 호출 지점을 먼저 — 시험·예제 main은 그것뿐일 때만(held-out mongo-c-driver)
                if not is_nonproduct_path(anchor.file):
                    sites = [s for s in sites if not is_nonproduct_path(s.file)] or sites
                chosen_sites = sites[: opts["caller_sites"]]
                if spine is not None:
                    # 척추는 지점 하나 — 인자 출처에 할당·읽기(malloc·fread 등)가 있는 지점을 먼저.
                    # 첫 지점만 따르면 jhead `process_EXIF`의 호출자로 EXIF를 다시 쓰는 함수가 골려
                    # 버퍼 할당(`ReadJpegSections`의 `malloc(itemlen)`)이 안 보였다.
                    chosen_sites = sorted(
                        chosen_sites,
                        key=lambda s: -_origin_score(index, graph, function, s),
                    )[:1]
                    for s in chosen_sites:
                        spine_origin[chain] = spine_origin.get(chain, 0) + _origin_score(index, graph, function, s)
                for site in chosen_sites:
                    found, enclosing = _site_blocks(graph, read_source, site, caller_lines)
                    kind = ("caller_spine" if spine is not None
                            else "caller_window" if depth == 1 else "caller_chain")
                    added = False
                    if (index is not None and enclosing is not None and function.symbol
                            and _length(enclosing) > SMALL_FUNCTION):
                        # 긴 호출자 — 창 + **호출 인자의 출처**(그 함수 안, 호출 앞)를 한 블록으로.
                        # 창만으로는 jhead `ReadJpegSections`의 `Data = malloc(itemlen)`·`fread(Data+2, …)`,
                        # WavPack의 `read_metadata_buff(&wpmd, …)`가 창 밖이라 빠졌다.
                        direct, farther = _provenance(index, graph, function.symbol, site,
                                                      enclosing, read_source, split=True)
                        origin = direct | farther
                        # 인자의 필드를 채우는 앞선 호출(척추 블록 제외 — 다른 프로그램일 수 있다)
                        fillers = [] if spine is not None else _field_fillers(
                            index, graph, read_source, function.symbol, site, enclosing)
                        origin |= {number for number, _f, _u in fillers}
                        # 호출 인자를 검사하는 가드(P3) — 창(±10줄) 밖이어도, 호출 지점에 가까운 것부터 8줄까지
                        site_line = site.line_start or 0
                        called_as = site_names.get((site.file, site_line), _short(function.symbol))
                        args = {a for r in (index.get("calls", {}).get(called_as, [])
                                            + (index.get("field_calls", {}).get(called_as, [])
                                               if isinstance(index.get("field_calls"), dict) else []))
                                if r["file"] == site.file and r["line"] == site_line
                                for a in r.get("args", [])}
                        # 호출 지점이 `switch`의 갈래 안이면 그 갈래 레이블 줄 — 어느 경우의 호출인지 보이게
                        site_case = _case_label(read_source, enclosing, site_line)
                        arg_names = set().union(*(_flow_tokens(code_only(a)) for a in args)) if args else set()
                        guards = sorted(_guard_lines(read_source, enclosing, arg_names, 50,
                                                     before=site_line), reverse=True)[:CALLER_GUARD_LINES]
                        window = set().union(*(_span(b) for b in found)) | origin | (
                            {site_case} if site_case else set())
                        keep = _statement_starts(
                            read_source, enclosing, window | guarded(enclosing.file, set(guards), window))
                        keep |= guarded(enclosing.file, _complete_guards(read_source, enclosing, keep), keep)
                        keep = _balanced(read_source, enclosing,
                                         keep | {enclosing.line_end or enclosing.line_start or 1})
                        added = put("caller", kind, enclosing, chain,
                                    text=_elided(read_source, enclosing, keep),
                                    kept_lines=sorted(keep), provenance=sorted(direct),
                                    provenance_far=sorted(farther),
                                    field_fillers=sorted({n for n, _f, _u in fillers}),
                                    depth=depth)
                        for _number, filler, used in fillers:
                            lines_ = _field_lines(read_source, filler, used)
                            if not lines_ or len(lines_) > TAINTED_LINES:
                                continue
                            lines_ |= guarded(filler.file, _guard_lines(
                                read_source, filler, _flow_names(read_source, filler.file, lines_), GUARD_LIMIT),
                                lines_)
                            lines_ = _statement_starts(read_source, filler, lines_)
                            lines_ |= guarded(filler.file, _complete_guards(read_source, filler, lines_), lines_)
                            fkeep = lines_ | set(
                                range(filler.line_start or 1, _header_end(read_source, filler) + 1)
                            ) | {filler.line_end or filler.line_start or 1}
                            fkeep = _balanced(read_source, filler, fkeep)
                            if add("callee", "field_filler", filler,
                                   text=_elided(read_source, filler, fkeep),
                                   kept_lines=sorted(fkeep), fields=sorted(used)):
                                counts["field_fillers"] = counts.get("field_fillers", 0) + 1
                    else:
                        for block in found:
                            added |= put("caller", kind, block, chain, depth=depth)
                    if added:
                        counts["callers"] += 1
                        counts["caller_depth"] = max(counts["caller_depth"], depth)
                    if enclosing is not None and enclosing.symbol:
                        key = (enclosing.file, enclosing.symbol)
                        if key not in climbed:
                            climbed.add(key)
                            nxt.append((enclosing, None if spine is None else spine - 1, chain))
            frontier = nxt
            if not frontier:
                break
        if spine_cands:
            def chain_score(chain):
                product = not any(is_nonproduct_path(loc.file) for _s, _k, loc, _e in spine_cands[chain])
                origin = spine_origin.get(chain, 0)
                return (origin > 0, product, origin)
            ranked = sorted(spine_cands, key=chain_score, reverse=True)  # 안정 정렬 — 동점은 먼저 생긴 줄기
            for chain in ranked[: opts.get("spine_chains", 1)]:
                for section, kind, location, extra in spine_cands[chain]:
                    if add(section, kind, location, **extra):
                        counts["callers"] += 1
                        counts["caller_depth"] = max(counts["caller_depth"], extra.get("depth", 0))
            counts["spine_chains_dropped"] = max(0, len(ranked) - opts.get("spine_chains", 1))
        # 호출자 블록이 부르는 짧은 보조 함수(`HELPER_LINES`줄 이하)의 본문 — 할당·해제 같은 짧은
        # 함수는 이름만으로 무엇을 하는지 모른다(cJSON `cJSON_Delete`의 소유권 플래그 검사).
        # 척추 블록은 뺀다 — 시험 프로그램이 부르는 출력·정리 함수까지 끌려온다.
        if opts["callee_hops"] and index is not None:
            for location, _text, _part_ in list(sections["caller"]):
                # 인자 출처 줄이 부르는 함수는 `SMALL_FUNCTION`줄까지 본문 — 출력 인자를 채우는 함수
                # (WavPack `read_metadata_buff`가 wpmd의 길이·데이터를 정한다)
                # 척추 블록은 뺀다 — 시험·도구 프로그램이 객체를 만드는 함수(netconsd `ncrx_create`)가 끌려온다
                origin = set(_part_.get("provenance", []))
                if origin and _part_["kind"] != "caller_spine":
                    for name in _calls_in(index, location.file, origin):
                        for function in graph.functions_named([name], location.file,
                                                              **hygiene) or []:
                            if _length(function) <= PROVENANCE_CALLEE_LINES and add(
                                "callee", "caller_helper", function
                            ):
                                counts["caller_helpers"] += 1
                if _part_["kind"] == "caller_spine":
                    continue
                shown = set(_part_.get("kept_lines") or _span(location)) - {
                    n for f, n in guard_only if f == location.file}
                for name in _calls_in(index, location.file, shown):
                    # 해제 계열 보조 함수만(수명 계열 CWE이거나 분류를 모를 때) — 시험·생성 스텁(`gen_*`·`test_*`)·
                    # 메모리 집계·`archive_entry_set_*` 같은 보조 함수가 줄줄이 붙었다(2026-10-10 r11 감사: libxml2-b
                    # 호출자 보조 136줄). 출발점이던 cJSON `cJSON_Delete`(소유권 플래그 검사)는 해제 계열이다
                    if not RELEASE_CALL.fullmatch(name) or (
                            opts.get("cwe") and opts.get("cwe") not in RELEASE_CWES):
                        continue
                    for function in graph.functions_named([name], location.file, **hygiene) or []:
                        if _length(function) <= HELPER_LINES and add(
                            "callee", "caller_helper", function
                        ):
                            counts["caller_helpers"] += 1

    # 쓰기 지점 — 이름(전역 등)에 쓰는 곳만. 필드 쓰기는 같은 필드를 쓰는 저장소 전체의 무관한
    # 생성자·복제 함수를 끌어왔다(cJSON: `item->type`에 쓰는 cJSON_Create* · cJSON_Duplicate).
    # 찾는 이름은 슬라이스 줄 + entry 함수 본문의 것 — entry가 쓰는 전역 함수 포인터가 어디서 바뀌는지
    # (cJSON `cJSON_malloc` ← `cJSON_InitHooks`)는 슬라이스 줄에 그 호출이 없어도 필요하다.
    write_sites = opts["write_sites"]
    if write_sites:
        # entry의 다른 줄은 가드(슬라이스 줄을 거르는 조건)와 함수 포인터 변수 호출(`cJSON_malloc(...)` — 정의가
        # 함수가 아닌 이름의 호출)만 — entry 전체 줄의 이름으로 쓰기 지점을 찾아 무관한 전역의 쓰기가 붙었다(r11)
        defined_functions = set(index.get("functions", {})) if index is not None else set()
        extra_lines = []
        for n in sorted(_span(anchor)):
            if (anchor.file, n) in guard_only:
                extra_lines.append((anchor.file, n))
                continue
            calls_here = CALL_NAME.findall(code_only(read_source(
                Location(file=anchor.file, line_start=n, line_end=n))))
            if any(c not in CALL_KEYWORDS and c not in defined_functions
                   and (index is None or c in index.get("file_vars", {})) for c in calls_here):
                extra_lines.append((anchor.file, n))
        write_lines = list(dict.fromkeys([*lines, *extra_lines]))
        sites = _write_sites(graph, read_source, write_lines, carried, anchor.file, fields=False,
                             file_vars=(index or {}).get("file_vars"))
        counts["write_sites_found"] = len(sites)
        tried = 0
        for site in sites:
            if tried == write_sites:
                break
            found, enclosing = _site_blocks(graph, read_source, site, WRITE_LINES)
            window = None
            if len(found) > 1 or enclosing is None or _length(enclosing) > SMALL_FUNCTION:
                window = _caller_window(graph, site, WRITE_LINES)
            if add_window("write_window", site, window):
                tried += 1
                counts["write_windows"] += 1

    # NULL 역참조(CWE-476) — 시작·끝 줄에서 `->`로 역참조되는 포인터 필드(`s->session->sess_cert->x`의 session·sess_cert)를
    # 채우는 곳. 그 필드가 NULL로 남을 수 있는지가 판정 근거다(판정 2026-10-10 r13b: gpac `traf->tfhd`·openssl `sess_cert`
    # 조건부). 같은 파일·디렉터리 먼저, 필드당 `FIELD_SITES`곳, 모두 `RELEASE_SITES`곳까지
    if index is not None and opts.get("cwe") == "CWE-476":
        deref_fields: list[str] = []
        for file, line in sorted(anchors or ()):
            code = code_only(read_source(Location(file=file, line_start=line, line_end=line)))
            for field in re.findall(r"(?:->|\.)\s*([A-Za-z_]\w*)\s*(?=->|\[)", code):
                if field not in deref_fields:
                    deref_fields.append(field)
        placed = 0
        for field in deref_fields:
            per_field = 0
            for site in graph.field_writes_of(field, anchor.file) or []:
                if placed >= RELEASE_SITES or per_field >= FIELD_SITES:
                    break
                if is_nonproduct_path(site.file) or (site.file == anchor.file and _spans(anchor, site.line_start or 0)):
                    continue
                # 그 함수에서 쓰기 앞의 조기 반환(`return`)과 그 가드 — 쓰기 전에 빠져나가 필드가 NULL로 남는 길이다(판정
                # 2026-10-10 r17b openssl: `ssl3_get_key_exchange`의 `reuse_message = 1; return 1;`이 안 보였다)
                enclosing = _narrowest_symbol(graph.symbols_for(site.file) or [], site.line_start or 0)
                window = None
                if enclosing is not None and enclosing.line_start is not None and _length(enclosing) > SMALL_FUNCTION:
                    rows_ = code_only(read_source(enclosing)).split("\n")
                    exits = [enclosing.line_start + i for i, row in enumerate(rows_)
                             if enclosing.line_start + i < (site.line_start or 0) and EXIT_STMT.match(row)
                             and re.match(r"\s*return\b", row)][-GUARD_LIMIT:]
                    if exits:
                        lines_ = set(range(max(enclosing.line_start, (site.line_start or 0) - WRITE_LINES),
                                           (site.line_start or 0) + WRITE_LINES + 1))
                        lines_ |= {n for e in exits for n in _controlling_heads(read_source, enclosing, {e})} | set(exits)
                        window = Location(file=site.file, symbol=enclosing.symbol, line_start=min(lines_),
                                          line_end=max(lines_))
                if add_window("write_window", site, field=field):
                    placed += 1
                    per_field += 1
                    counts["field_store_sites"] = counts.get("field_store_sites", 0) + 1
                    if window is not None:
                        for exit_line in exits:
                            add_window("write_window", Location(file=site.file, symbol=enclosing.symbol,
                                                                line_start=exit_line, line_end=exit_line), field=field)
        # 그 필드를 NULL과 비교하는 다른 함수(검증) — `if (!p->f)`·`p->f == NULL`. 필드가 비었을 때 어디서 걸러지는지(혹은
        # 안 걸러지는지)가 판정 근거다(판정 r17b gpac: `traf_Read`의 `!ptr->tfhd` 검사가 없었다). 같은 디렉터리, 필드당 2곳
        folder = posixpath.dirname(anchor.file)
        for field in deref_fields:
            check = re.compile(rf"!\s*[\w>\-.]*(?:->|\.)\s*{re.escape(field)}\b(?!\s*(?:->|\.|\[))|"
                               rf"(?:->|\.)\s*{re.escape(field)}\s*[=!]=\s*(?:NULL|0|nullptr)\b")
            checks = 0
            # 그 필드를 채우는 파일 먼저(같은 구조체의 읽기·검증 함수가 거기 있다), 필드당 한 곳 — 다섯 곳을 붙이자 gpac이
            # +87%였다(2026-10-10 r18)
            writer_files = {w.file for w in graph.field_writes_of(field, anchor.file) or []}
            for file in sorted((f for f in index.get("files") or [] if isinstance(f, str)
                                and posixpath.dirname(f) == folder and f.lower().endswith((".c", ".cc", ".cpp"))),
                               key=lambda f: (f not in writer_files, f)):
                if checks >= 1:
                    break
                text = read_source(Location(file=file))
                if field not in text:
                    continue
                for i, row in enumerate(code_only(text).split("\n")):
                    if checks >= 1:
                        break
                    if check.search(row) and GUARD_HEAD.match(row) and not (
                            file == anchor.file and _spans(anchor, i + 1)):
                        enclosing = _narrowest_symbol(graph.symbols_for(file) or [], i + 1)
                        if add_window("write_window", Location(file=file, symbol=enclosing.symbol if enclosing else None,
                                                               line_start=i + 1, line_end=i + 1), field=field,
                                      null_check=True):
                            checks += 1
                            counts["null_check_sites"] = counts.get("null_check_sites", 0) + 1

    # 해제 후 사용(수명 계열) — 시작·끝 줄이 필드에 저장한 값(`dcv->private_data = s`)을 다른 곳에서 읽는 지점: 그 필드를
    # 인자로 넘기거나(`f(x->private_data)`) 그 필드로 부르는(`x->m_Encrypt->Is…()`) 호출. 실제 사용이 없어 조건부였다
    # (판정 2026-10-10 r13b wireshark·podofo). 같은 파일·디렉터리 먼저 `FIELD_SITES`곳
    if index is not None and opts.get("cwe") in LIFETIME_CWES:
        stored: list[str] = []
        anchor_points = {(f, n) for f, n in (anchors or ())}
        for file, line in sorted(anchor_points):
            code = code_only(read_source(Location(file=file, line_start=line, line_end=line)))
            stored += [f for f in TAINTED_FIELD.findall(code) if f not in stored]
        here = posixpath.dirname(anchor.file)
        for field in stored:
            pattern = re.compile(rf"(?:->|\.)\s*{re.escape(field)}\b")
            reads = []
            for call, rows in index.get("calls", {}).items():
                for row in rows:
                    if any(pattern.search(a) for a in row.get("args", [])):
                        reads.append(row)
            field_calls = index.get("field_calls", {}) if isinstance(index.get("field_calls"), dict) else {}
            for rows in field_calls.values():
                for row in rows:
                    if pattern.search(row.get("expr") or "") and not (row.get("expr") or "").rstrip().endswith(field):
                        reads.append(row)
            # 같은 파일만 — 필드 이름만 같은 다른 dissector(`packet-acse.c`의 `private_data`)가 골라졌다(판정 r14b)
            reads = [r for r in reads if r["file"] == anchor.file and (r["file"], r["line"]) not in anchor_points
                     and not _spans(anchor, r["line"])]
            reads.sort(key=lambda r: (r["file"] != anchor.file, posixpath.dirname(r["file"]) != here, r["file"], r["line"]))
            used = 0
            for row in reads:
                if used >= FIELD_SITES:
                    break
                site = Location(file=row["file"], symbol=row.get("caller"), line_start=row["line"], line_end=row["line"])
                if add_window("write_window", site, field=field, use_site=True):
                    used += 1
                    counts["use_sites"] = counts.get("use_sites", 0) + 1
            # 다른 파일의 읽기 — 같은 디렉터리에서 저장한 객체와 **같은 타입** 변수의 그 필드를 읽는 줄(`str = dcv->private_data`).
            # 필드 이름만 같은 다른 구조체는 타입으로 거른다(판정 2026-10-10 r16 wireshark: 실제 사용이 다른 dissector에 있다)
            if used < FIELD_SITES:
                for site in _typed_field_reads(index, read_source, anchor, anchor_points, field, FIELD_SITES - used):
                    if add_window("write_window", site, field=field, use_site=True):
                        used += 1
                        counts["use_sites"] = counts.get("use_sites", 0) + 1

    # 오염된 필드의 위험한 사용 — 슬라이스 줄이 값을 대입한 필드(`msg->ncfrag_off = nf_off`)가 다른 함수의
    # 메모리 쓰기 호출 인자로 쓰이는 곳(`memcpy(dst->text + src->ncfrag_off, ...)`). DDG는 구조체 필드를
    # 거쳐 다른 함수로 가는 흐름을 못 따라가서, 실제 범위 밖 쓰기가 슬라이스에 안 들어왔다(netconsd).
    # 필드 쓰기 지점 창(같은 필드에 쓰는 저장소 전체의 생성자들)과 달리 위험 호출의 인자만 본다.
    # 메모리 범위 오류 계열에서만 — 이중 해제(libxml2 CWE-415)에 `nodeTab` 복사 지점 11곳이 붙었다(검수 2026-10-10
    # r8). 분류를 모르면(정답 없는 실행) 예전처럼 켠다
    cwe = opts.get("cwe")
    if index is not None and write_sites and (not cwe or cwe in WRITE_CWES):
        # 필드 순서 — 시작·끝 줄의 필드, entry 함수 줄의 필드, 나머지(이름순 첫 필드가 몫을 다 쓰던 것을 막는다).
        # 필드마다 `FIELD_SITES`곳까지
        rank: dict[str, int] = {}
        for file, line in lines:
            code = code_only(read_source(Location(file=file, line_start=line, line_end=line)))
            level = 0 if (file, line) in (anchors or ()) else (1 if file == anchor.file and _spans(anchor, line) else 2)
            for name in TAINTED_FIELD.findall(code):
                rank[name] = min(rank.get(name, level), level)
        # 슬라이스·entry가 있는 파일 안만 — 같은 필드 이름을 쓰는 다른 프로그램(netconsd
        # `util/netconsblaster.c`·`modules/logger.cc`, WavPack `cli/wavpack.c`)의 호출을 끌어왔다
        program_files = {file for file, _line in lines} | {anchor.file}
        found = 0
        for name in sorted(rank, key=lambda n: (rank[n], n)):
            field = re.compile(rf"(?:->|\.)\s*{re.escape(name)}\b")
            per_field = 0
            for call, rows in sorted(index.get("calls", {}).items()):
                if not DANGEROUS_CALL.fullmatch(call):
                    continue
                for row in rows:
                    if found >= write_sites or per_field >= FIELD_SITES or row["file"] not in program_files \
                            or not any(field.search(a) for a in row.get("args", [])):
                        continue
                    site = Location(file=row["file"], symbol=row.get("caller"),
                                    line_start=row["line"], line_end=row["line"])
                    if add_window("tainted_use", site, field=name):
                        found += 1
                        per_field += 1
                        counts["tainted_uses"] += 1

    # 할당 크기를 정하는 함수 — sink 버퍼에서 출발한다: sink·시작 줄의 변수(이름 또는 마지막 필드)에 할당 호출
    # (`*alloc*`·`*new*`) 결과를 넣는 대입을 그 줄을 품은 함수에서 찾고, 그 할당의 크기 인자 안 호출과 할당 앞에서
    # 크기 인자 이름에 값을 넣는 호출(`n = make_weights(...)`)의 본문을 붙인다. 지역 배열이면 할당이 없으니 아무것도
    # 안 붙는다. 할당 줄이 안 보이면 그 블록에 더한다(검수 2026-10-10 r7: 경로 위 아무 할당이나 붙었다 — MapServer)
    if index is not None and opts["callee_hops"]:
        buffers: set[str] = set()
        for file, line in sorted(anchors or ()):
            code = code_only(read_source(Location(file=file, line_start=line, line_end=line)))
            buffers |= {c.split("->")[-1].split(".")[-1].strip() for c in CHAIN_RE.findall(code)}
            buffers |= set(IDENT_RE.findall(CHAIN_RE.sub(" ", code))) - CALL_KEYWORDS
        for key in ("code",):
            for position, (owner, text, part) in enumerate(list(sections[key])):
                if owner.line_start is None:
                    continue
                body = code_only(read_source(owner)).split("\n")
                providers: list[str] = []
                alloc_lines: set[int] = set()
                for name, rows in index.get("calls", {}).items():
                    if not ALLOC_CALL.fullmatch(name):
                        continue
                    for row in rows:
                        if row["file"] != owner.file or not _spans(owner, row["line"]):
                            continue
                        target = LHS_FIELD.match(body[row["line"] - owner.line_start])
                        if not target or target.group(1) not in buffers:
                            continue
                        alloc_lines.add(row["line"])
                        for argument in row.get("args", [])[:3]:
                            providers += [c for c in CALL_NAME.findall(argument) if c not in CALL_KEYWORDS]
                            for word in set(IDENT_RE.findall(argument)) - CALL_KEYWORDS:
                                assign = re.compile(rf"\b{re.escape(word)}\s*(?:[-+*]?=)(?!=)(.*)")
                                for text_row in body[: row["line"] - owner.line_start]:
                                    match = assign.search(text_row)
                                    if match:
                                        providers += [c for c in CALL_NAME.findall(match.group(1))
                                                      if c not in CALL_KEYWORDS]
                if not alloc_lines:
                    continue
                if part.get("kept_lines") and not alloc_lines <= set(part["kept_lines"]):
                    keep = _statement_starts(read_source, owner, set(part["kept_lines"]) | alloc_lines)
                    part = {**part, "kept_lines": sorted(keep)}
                    sections[key][position] = (owner, _elided(read_source, owner, keep), part)
                names = list(dict.fromkeys(p for p in providers if not ALLOC_CALL.fullmatch(p)))
                order = {name: index_ for index_, name in enumerate(names)}
                found_functions = sorted((f for f in graph.functions_named(names, owner.file, **hygiene) or []
                                          if f != owner), key=lambda f: order.get(f.symbol, len(order)))
                for function in found_functions[:SIZE_PROVIDERS]:
                    if _length(function) <= PROVENANCE_CALLEE_LINES:
                        if add("callee", "size_provider", function):
                            counts["size_providers"] = counts.get("size_providers", 0) + 1
                    else:
                        signature(function)

    # 같은 객체를 해제하는 상대 지점 — sink·source 줄이 접근하는 필드(`gt->status`)를 해제 계열 호출
    # (`free`·`release`·`put`·`destroy`·`unref` …)의 인자로 넘기는 곳. 해제 후 사용·경쟁 조건은 그 상대편이
    # 있어야 확정된다(검수 2026-10-10: xen `gnttab_unpopulate_status_frames`가 없어 조건부). 진입 파일과 최상위
    # 디렉터리가 같은 제품 경로만, `RELEASE_SITES`곳까지
    if index is not None and opts.get("release_sites", RELEASE_SITES) and opts.get("cwe") in RELEASE_CWES:
        # 같은 객체만 — 시작·끝 줄의 접근 경로(`gt->status`)를 그대로(캐스트·`&` 무시) 넘기는 해제, 또는 그 경로의
        # 원소·필드(`gt->status[i]`·`gt->status->x`). `[`가 바로 붙은 경로는 담는 배열이지 객체가 아니다 — 필드 이름만
        # 같거나 배열을 해제하는 곳(`xmlFree(ctxt->valueTab)`)이 골라졌다(검수 2026-10-10 r8 libxml2)
        chains: set[str] = set()
        for file, line in sorted(anchors or ()):
            code = code_only(read_source(Location(file=file, line_start=line, line_end=line)))
            for match in CHAIN_RE.finditer(code):
                if not code[match.end():].lstrip().startswith("["):
                    chains.add(re.sub(r"\s+", "", match.group(0)))
        top = anchor.file.split("/")[0]
        # 할당자 핸들 — 시작·끝 줄에서 인자 없이 부르는 짧은 함수가 파일 범위 변수를 돌려주면(`wmem_packet_scope()` →
        # `packet_scope`) 그 변수, 그리고 그 줄 호출에 이름 하나로 넘긴 파일 범위 변수. 그 핸들을 비우는 해제
        # (`wmem_free_all(packet_scope)`)가 객체의 해제다(검토 2026-10-10 r13 wireshark)
        arena_owners: set[str] = set()
        if opts.get("cwe") in LIFETIME_CWES:
            file_vars = index.get("file_vars", {})
            own_names = set(graph.locals_of(anchor.file, anchor.symbol) or ()) if anchor.symbol else set()

            def file_scope(name: str, where: str) -> bool:
                # 그 파일에 선언된 파일 범위 변수만 — 다른 파일의 같은 이름 전역(파라미터 `tvb`)이 핸들로 잡혀 다른
                # dissector의 `tvb` 해제 지점이 골라졌다(2026-10-10 r13 wireshark)
                return name not in own_names and any(r.get("file") == where for r in file_vars.get(name) or ())

            for file, line in sorted(anchors or ()):
                for call, rows in index.get("calls", {}).items():
                    for row in rows:
                        if row["file"] != file or row["line"] != line:
                            continue
                        for arg in row.get("args", []):
                            bare = _bare_arg(arg)
                            if file_scope(bare, file):
                                arena_owners.add(bare)
                        if row.get("args"):
                            continue
                        found_fn = graph.functions_named([call], file, **hygiene) or []
                        if len(found_fn) == 1 and _length(found_fn[0]) <= HELPER_LINES:
                            body = code_only(read_source(found_fn[0]))
                            for ident in re.findall(r"\breturn\s+([A-Za-z_]\w*)\s*;", body):
                                if file_scope(ident, found_fn[0].file):
                                    arena_owners.add(ident)
        candidates = []
        for call, rows in sorted(index.get("calls", {}).items()):
            if not RELEASE_CALL.fullmatch(call):
                continue
            for row in rows:
                if (row["file"].split("/")[0] != top or is_nonproduct_path(row["file"])
                        or (row["file"] == anchor.file and _spans(anchor, row["line"]))):
                    continue
                args = [_bare_arg(a) for a in row.get("args", [])]
                hit = next((c for c in sorted(chains) for a in args
                            if a == c or (a.startswith(c) and a[len(c):len(c) + 1] in ("[", "-", "."))), None)
                if not hit and arena_owners:
                    hit = next((a for a in args if a in arena_owners), None)
                if hit:
                    candidates.append(((row["file"] != anchor.file, row["file"], row["line"]), row, hit))
        found = 0
        limit = opts.get("release_sites", RELEASE_SITES)
        for _rank, row, name in sorted(candidates, key=lambda c: c[0]):
            if found >= limit:
                break
            site = Location(file=row["file"], symbol=row.get("caller"),
                            line_start=row["line"], line_end=row["line"])
            if add_window("release_site", site, field=name):
                found += 1
                counts["release_sites"] = counts.get("release_sites", 0) + 1
                # 할당자 핸들을 비우는 함수가 파라미터 없는 짧은 래퍼면(`wmem_leave_packet_scope`) 그것을 부르는 곳 하나도
                wrapper = row.get("caller")
                wrapper_rows = index.get("functions", {}).get(wrapper or "", [])
                if name in arena_owners and wrapper != anchor.symbol and wrapper_rows \
                        and not wrapper_rows[0].get("params") \
                        and wrapper_rows[0]["line_end"] - wrapper_rows[0]["line_start"] + 1 <= HELPER_LINES:
                    for up in sorted(index.get("calls", {}).get(wrapper, []), key=lambda r: (r["file"], r["line"])):
                        if found >= limit:
                            break
                        if up["file"].split("/")[0] == top and not is_nonproduct_path(up["file"]):
                            if add_window("release_site", Location(file=up["file"], symbol=up.get("caller"),
                                                                   line_start=up["line"], line_end=up["line"]),
                                          field=name):
                                found += 1
                                counts["release_sites"] += 1
                            break
        # C++ 소유 멤버(`std::unique_ptr<T> m_x`)의 해제 — 이름 붙은 해제 호출이 없다: 재대입(`m_x = nullptr`·
        # `= std::move(y)`)·`reset`·`delete`·소유권을 넘기는 `return std::move(m_x)`와 그것을 받은 새 소유자의 재대입
        # (검토 2026-10-10 r13 podofo: `m_Encrypt.get()`으로 빌려 준 객체가 다른 소유자에서 풀렸다)
        for site, name in _owner_release_sites(index, graph, read_source, anchor, anchors or set(),
                                               opts.get("cwe")):
            if found >= limit:
                break
            if add_window("release_site", site, field=name):
                found += 1
                counts["owner_release_sites"] = counts.get("owner_release_sites", 0) + 1

    # 수명 계열(이중 해제·해제 후 사용·누수) — 객체 변수를 해제하는 줄과 해제 함수 본문. 역방향 슬라이스는 객체를
    # 정의하는 줄(`tmp = valuePop(ctxt)`)까지만 오고 그 뒤의 해제 줄은 생략 구간에 들어갔다. 해제 함수(131줄)는
    # 머리만 붙었다(검수 2026-10-10 r8 libxml2 — 필수 정보: 해제 본문·소유권 검사)
    if index is not None and opts["callee_hops"] and opts.get("cwe") in LIFETIME_CWES:
        def row_code(file: str, number: int) -> str:
            return code_only(read_source(Location(file=file, line_start=number, line_end=number)))

        # 객체 변수: 시작·끝 줄의 `v = <경로>[…]`·`v = <경로>`의 v, 그 줄에서 해제하는 인자, 그리고 entry가 v를
        # 돌려주면 carried 함수에서 `w = entry(…)`의 w
        objects: dict[tuple[str, str | None], dict[str, int]] = {}  # (파일, 함수) → 이름 → 정의 줄
        returned: set[str] = set()
        for file, line in sorted(anchors or ()):
            code = row_code(file, line)
            owner = _narrowest_symbol(graph.symbols_for(file) or [], line)
            key_ = (file, owner.symbol if owner else None)
            lhs = LHS_NAME.match(code)
            if lhs and re.match(r"\s*(?:\([^()]*\)\s*)?[A-Za-z_][\w\s>.-]*(?:\[[^\]]*\])*\s*;",
                                code[lhs.end():]):
                objects.setdefault(key_, {})[lhs.group(1)] = line
            for call in CALL_NAME.findall(code):
                if RELEASE_CALL.fullmatch(call):
                    for row in index.get("calls", {}).get(call, []):
                        if row["file"] == file and row["line"] == line:
                            for arg in row.get("args", []):
                                if re.fullmatch(r"[A-Za-z_]\w*", _bare_arg(arg)):
                                    objects.setdefault(key_, {})[_bare_arg(arg)] = line - 1
        entry_vars = set(objects.get((anchor.file, anchor.symbol), {}))
        if entry_vars and anchor.line_start is not None:
            for number in _span(anchor):
                ret = re.match(r"\s*return\s*\(?\s*([A-Za-z_]\w*)\s*\)?\s*;", row_code(anchor.file, number))
                if ret and ret.group(1) in entry_vars:
                    returned.add(ret.group(1))
        carried_blocks = [location for key in ("code", "caller") for location, _t, part in sections[key]
                          if part["kind"] in ("entry_function", "slice_function", "slice_lines",
                                              "caller_window", "caller_chain") and location.line_start is not None]
        if returned and anchor.symbol:
            short = anchor.symbol.split("::")[-1]
            for row in index.get("calls", {}).get(short, []):
                owner = next((b for b in carried_blocks if b.file == row["file"] and _spans(b, row["line"])), None)
                lhs = LHS_NAME.match(row_code(row["file"], row["line"])) if owner else None
                if owner and lhs:
                    objects.setdefault((owner.file, owner.symbol), {})[lhs.group(1)] = row["line"]
        # sink가 읽는 담는 배열(`ctxt->valueTab[…]`)에 파라미터 값을 넣는 함수(`valuePush(ctxt, value)`)에 carried
        # 블록이 넘기는 변수도 객체다 — 그 객체가 배열을 거쳐 다른 함수에서 꺼내져 해제된다. 이중 해제의 다른 쪽
        # (`xmlXPathReleaseObject(xpctxt, contextObj)`)이 생략 구간에 있었다(검토 2026-10-10 r11 libxml2)
        containers: set[str] = set()
        for file, line in sorted(anchors or ()):
            containers |= set(re.findall(r"(?:->|\.)\s*([A-Za-z_]\w*)\s*\[", row_code(file, line)))
        writers: dict[str, int] = {}
        for field in sorted(containers):
            for write in index.get("writes", {}).get(field, []):
                params = next((r.get("params") or [] for r in index.get("functions", {}).get(write.get("func"), [])
                               if r.get("file") == write.get("file")), [])
                rhs = (write.get("rhs") or "").strip()
                if write.get("base") == "field" and rhs in params:
                    writers[write["func"]] = params.index(rhs)
        for writer, position in sorted(writers.items()):
            for row in index.get("calls", {}).get(writer, []):
                block = next((b for b in carried_blocks if b.file == row["file"] and _spans(b, row["line"])), None)
                args = row.get("args", [])
                if block is None or position >= len(args):
                    continue
                value = _bare_arg(args[position])
                if re.fullmatch(r"[A-Za-z_]\w*", value):
                    objects.setdefault((block.file, block.symbol), {}).setdefault(value, row["line"])
        released: dict[str, set[int]] = {}  # 해제 함수 → 객체를 넘긴 인자 위치
        for block in carried_blocks:
            names = objects.get((block.file, block.symbol)) or {}
            if not names:
                continue
            added_here = 0
            for call, rows in sorted(index.get("calls", {}).items()):
                if not RELEASE_CALL.fullmatch(call):
                    continue
                for row in rows:
                    if row["file"] != block.file or not _spans(block, row["line"]):
                        continue
                    args = [_bare_arg(a) for a in row.get("args", [])]
                    target = next((a for a in args if a in names and row["line"] > names[a]), None)
                    if target is None or added_here >= RELEASE_LINES:
                        continue
                    site = Location(file=row["file"], symbol=block.symbol,
                                    line_start=row["line"], line_end=row["line"])
                    add_window("release_site", site, site, field=target)
                    added_here += 1
                    counts["release_lines"] = counts.get("release_lines", 0) + 1
                    released.setdefault(call, set()).add(args.index(target))
        # 그 해제 함수 — 짧으면(`PROVENANCE_CALLEE_LINES`줄) 본문, 길면 객체 파라미터가 흐르는 줄 중 해제 호출·필드
        # 대입·NULL 대입·`return`·그 파라미터 검사와 가드·괄호 짝(`TAINTED_LINES`까지), 넘으면 머리
        for name in sorted(released):
            for function in graph.functions_named([name], anchor.file, **hygiene) or []:
                if function.line_start is None:
                    continue
                for position, (location, _text, part) in enumerate(list(sections["callee"])):
                    if part["kind"] == "callee_signature" and location.file == function.file \
                            and location.line_start == function.line_start:
                        sections["callee"].pop(position)
                        seen[:] = [other for other in seen if other is not location]
                        break
                if _length(function) <= PROVENANCE_CALLEE_LINES:
                    if add("callee", "release_callee", function):
                        counts["release_callees"] = counts.get("release_callees", 0) + 1
                    continue
                names_ = _param_names(read_source, function)
                params = {names_[k] for k in released[name] if k < len(names_)} - {""}
                flow = _tainted_lines(read_source, function, params)
                body = code_only(read_source(function)).split("\n")
                pick = {n for n in flow
                        if any(RELEASE_CALL.fullmatch(c) for c in CALL_NAME.findall(body[n - function.line_start]))
                        or AUX_EVIDENCE.search(body[n - function.line_start])
                        or re.match(r"\s*(?:return\b|if\s*\()", body[n - function.line_start])}
                pick = _statement_starts(read_source, function, pick)
                pick |= _guard_lines(read_source, function, params, GUARD_LIMIT)
                pick |= _complete_guards(read_source, function, pick)
                keep = pick | set(range(function.line_start, _header_end(read_source, function) + 1)) | {
                    function.line_end or function.line_start}
                keep = _balanced(read_source, function, keep)
                if len(keep) > TAINTED_LINES:
                    signature(function)
                    continue
                if add("callee", "release_callee", function, text=_elided(read_source, function, keep),
                       kept_lines=sorted(keep)):
                    counts["release_callees"] = counts.get("release_callees", 0) + 1

    # Input decoders can be longer than SMALL_FUNCTION and therefore appear only
    # as signatures, or as slice_lines with the grammar/number conversion elided.
    # Promote only decoders called on the value path.  This runs before definition
    # closure so the newly visible constants and types receive definitions too.
    if index is not None and opts.get("input_parser_functions", 4):
        cpg_paths = (cpg_input_paths(input_evidence, index, carried)
                     if input_evidence and input_evidence.get("status") == "ok" else None)
        raw_cursors = ({p["input_state"] for p in cpg_paths.values()}
                       if cpg_paths is not None else
                       _raw_cursor_globals(index, carried, read_source))
        counts["input_cpg"] = {
            "status": input_evidence.get("status") if input_evidence else "unavailable",
            "candidate_methods": input_evidence.get("method_count", 0) if input_evidence else 0,
            "linked_methods": len(cpg_paths) if cpg_paths is not None else 0,
            "input_states": sorted(raw_cursors),
            "truncated": bool(input_evidence.get("truncated")) if input_evidence else False,
        }
        relevant = set(dependency or lines) | set(anchors or ())
        for position, (function, _text, part) in enumerate(sections["code"]):
            if part["kind"] != "slice_lines" or _length(function) > opts.get("input_parser_full_lines", 160):
                continue
            if not any(file == function.file and _spans(function, number) for file, number in relevant):
                continue
            score, cursors = _input_signals(read_source(function))
            if score < 5 or not (cursors & raw_cursors):
                continue
            if cpg_paths is not None and not cpg_proof_for(function, cpg_paths):
                continue
            upgraded = {key: value for key, value in part.items() if key != "kept_lines"}
            upgraded.update(kind="slice_function", parser_evidence={
                "reason": "retained value path reads the input cursor",
                "score": score, "input_cursors": sorted(cursors & raw_cursors),
            })
            sections["code"][position] = (function, read_source(function), upgraded)
            counts["input_parsers_promoted"] = counts.get("input_parsers_promoted", 0) + 1
        already_full = {(loc.file, loc.line_start)
                        for key in ("code", "callee", "caller", "write")
                        for loc, text_, part in sections[key]
                        if loc.line_start is not None and not part.get("kept_lines")
                        and part["kind"] in ((CORE_KINDS | CONNECT_KINDS) - {"callee_signature"})
                        and text_.strip() == read_source(loc).strip()}
        grammar_report: dict = {}
        grammar = choose_input_contract(
            index, graph, read_source, carried, set(dependency or lines), set(anchors or ()),
            cross_method_flow,
            max_functions=opts.get("input_grammar_functions", 3),
            max_chars=opts.get("input_parser_chars", 50_000),
            already_full=already_full,
            diagnostics=grammar_report,
            cpg_paths=cpg_paths,
        )
        counts["input_contract"] = grammar_report
        grammar_scopes = {(choice["location"].file, choice["location"].line_start)
                          for choice in grammar}
        regular = choose_input_functions(
            index, graph, read_source, carried, set(dependency or lines), set(anchors or ()),
            cross_method_flow,
            max_functions=opts.get("input_parser_functions", 4),
            max_chars=max(0, opts.get("input_parser_chars", 50_000)
                          - sum(choice["chars"] for choice in grammar)),
            already_full=already_full | grammar_scopes,
            cpg_paths=cpg_paths,
        )
        selected = grammar + regular
        nested_by_owner: dict[tuple[str, int], set[str]] = {}
        for choice in selected:
            for owner in carried + [item["location"] for item in selected]:
                if owner.line_start is not None and owner.file == choice["call_file"] \
                        and _spans(owner, choice["call_line"]):
                    nested_by_owner.setdefault((owner.file, owner.line_start), set()).add(choice["callee"])
        for choice in selected:
            function = choice["location"]
            focused = _length(function) > opts.get("input_parser_full_lines", 160)
            decoder_cursors = set(choice.get("input_cursors", ()))
            kept = _focused_input_lines(read_source, function,
                                       decoder_cursors & raw_cursors or decoder_cursors,
                                       nested_by_owner.get((function.file, function.line_start), set())) \
                if focused else None
            parser_text = _elided(read_source, function, kept) if kept is not None else read_source(function)
            matches = [(key, pos, loc, part)
                       for key in ("code", "callee", "caller", "write")
                       for pos, (loc, _text, part) in enumerate(sections[key])
                       if loc.file == function.file and loc.line_start == function.line_start]
            if any(loc.line_end == function.line_end and not part.get("kept_lines")
                   and part["kind"] in ("entry_function", "slice_function", "sink_callee",
                                        "flow_callee", "input_parser")
                   for _key, _pos, loc, part in matches):
                continue
            details = {"parser_evidence": {
                **{key: value for key, value in choice.items() if key != "location"},
                **({"cpg": proof} if (proof := cpg_proof_for(function, cpg_paths or {})) else {}),
            }}
            if matches:
                # Replace all presentations of this function, including a signature
                # and any windows; appending a second body would duplicate code.
                primary = next(((key, pos, loc, part) for key, pos, loc, part in matches
                                if key == "code"), matches[0])
                kind = "slice_function" if primary[3]["kind"] == "slice_lines" else \
                    "input_parser" if primary[0] != "code" else primary[3]["kind"]
                extra = {k: v for k, v in primary[3].items()
                         if k not in ("kept_lines", "aux_display", "dead_lines")}
                extra.update(details)
                extra["kind"] = "input_parser" if focused else kind
                extra["line_end"] = function.line_end
                if kept is not None:
                    extra["kept_lines"] = sorted(kept)
                for key in ("code", "callee", "caller", "write"):
                    rebuilt = []
                    for pos, (loc, text_, part) in enumerate(sections[key]):
                        if loc.file == function.file and loc.line_start == function.line_start:
                            if key == primary[0] and pos == primary[1]:
                                rebuilt.append((function, parser_text, extra))
                        else:
                            rebuilt.append((loc, text_, part))
                    sections[key] = rebuilt
                seen[:] = [loc for loc in seen if not (loc.file == function.file
                                                     and loc.line_start == function.line_start)]
                seen.append(function)
            else:
                if not add("callee", "input_parser", function, text=parser_text,
                           **({"kept_lines": sorted(kept)} if kept is not None else {}), **details):
                    continue
            counts["input_parsers_promoted"] = counts.get("input_parsers_promoted", 0) + 1
            if choice in grammar:
                counts["input_grammar_promoted"] = counts.get("input_grammar_promoted", 0) + 1
            if focused:
                counts["input_parsers_focused"] = counts.get("input_parsers_focused", 0) + 1

        # String-keyed dispatch tables are not call edges.  Preserve only the
        # initializer row naming a retained handler and the table's insertion
        # site, rather than including every directive in the whole table.
        handlers = {loc.symbol for loc in carried if loc.symbol}
        handlers |= {loc.symbol for loc, _text, _part_ in sections["caller"] if loc.symbol}
        for binding in choose_input_dispatch(index, read_source, handlers):
            location = Location(file=binding["file"], line_start=binding["line"],
                                line_end=binding["line"])
            if add("callee", "input_dispatch", location, text=binding["source"],
                   handler=binding["handler"], table=binding["table"],
                   registration_line=binding["registration_line"]):
                counts["input_dispatches"] = counts.get("input_dispatches", 0) + 1

    # 정의되지 않은 빌드 스위치의 구역(`#ifdef JSI_MEM_DEBUG` … `#endif`)은 본문에서도 뺀다 — 스위치는 정의
    # 안 됨으로 다루면서(설정 매크로) 그 조건에 걸린 디버그 본문은 남아 반쪽이었다(검토 2026-10-10). 정의를
    # 찾기 전에 해서 그 구역의 이름은 정의도 찾지 않는다. 슬라이스 줄(데이터 의존·시작/끝)이 든 구역은 둔다
    dropped_switches: set[str] = set()
    if opts.get("drop_undefined_switches", 1) and index is not None:
        defined = set(index.get("macros", {}))
        marked_lines = set(dependency or ()) | set(anchors or ())
        for key in ("code", "callee", "caller", "write"):
            for position, (location, text, part) in enumerate(sections[key]):
                # 머리 블록은 본문이 없다 — 고쳐 쓰면 `{ /* ... body omitted ... */ }`가 지워졌다(리뷰 2026-10-10)
                if location.line_start is None or part.get("kind") == "callee_signature":
                    continue
                rows = code_only(read_source(Location(file=location.file))).split("\n")
                dead = _dead_switch_lines(rows, location.line_start, location.line_end or location.line_start,
                                          defined, {n for f, n in marked_lines if f == location.file},
                                          dropped_switches)
                shown = set(part.get("kept_lines") or _span(location))
                if not dead & shown:
                    continue
                keep = shown - dead
                part = {**part, "kept_lines": sorted(keep), "undefined_switch_lines": len(dead & shown),
                        "dead_lines": sorted(dead)}
                sections[key][position] = (location, _elided(read_source, location, keep), part)
                counts["undefined_switch_lines"] = counts.get("undefined_switch_lines", 0) + len(dead & shown)

    # 로그·추적 호출 한 문장(`zlog_debug(...)`·`frrtrace(...)`·`flog_warn(...)`)은 본문에서 빼고 정의도 찾지
    # 않는다 — 짧은 보조 함수를 통째로 보이자 로그 매크로 정의가 줄줄이 붙었다(2026-10-10 3회차 재현: frr 정의
    # 98→196줄 중 로그·추적 몫). 슬라이스 줄(데이터 의존·시작/끝)은 둔다 — 포맷 문자열 취약점은 로그 호출일 수 있다
    if opts.get("drop_log_statements", 0):
        marked_lines = set(dependency or ()) | set(anchors or ())
        for key in ("code", "callee", "caller", "write"):
            for position, (location, text, part) in enumerate(sections[key]):
                if location.line_start is None:
                    continue
                shown = set(part.get("kept_lines") or _span(location))
                rows = code_only(read_source(location)).split("\n")
                # 가드 단위(조건 + 처리 본문 + 탈출) 안의 로그는 둔다 — 패치가 경계 검사 본문에 넣은 `TRACE_(...)`가
                # 지워져 held-out pjproject patch의 수정 줄 8/8 → 4/8(2026-10-10)
                drop = _log_statement_lines(rows, location.line_start, shown,
                                            {n for f, n in marked_lines if f == location.file}
                                            | _handled_guard_units(read_source, location, shown))
                if not drop:
                    continue
                keep = shown - drop
                part = {**part, "kept_lines": sorted(keep), "log_lines": len(drop)}
                sections[key][position] = (location, _elided(read_source, location, keep), part)
                counts["log_lines"] = counts.get("log_lines", 0) + len(drop)

    definitions: list[tuple[str, dict]] = []
    trailer: list[tuple[str, dict | None]] = []
    definition_stats: dict = {}
    aliases: set[str] = set()
    asked: set[str] = set()
    if with_definitions and index is not None:
        # 정의 클로저(closure.py) — 위에서 고른 **모든** 코드 블록이 쓰는 이름에 대해. 별칭 매크로가
        # 새 함수를 실으면 그 함수의 이름도 다시 찾는다(최대 3바퀴).
        def function_at(file: str, line: int) -> str | None:
            symbol = _narrowest_symbol(graph.symbols_for(file) or [], line)
            return symbol.symbol if symbol else None

        closure = Closure(index, repo, read_source)
        evidence = opts.get("definition_seed", "evidence") == "evidence"
        value_macros: dict[str, bool] = {}
        raw_lines: dict[str, list[str]] = {}
        code_lines: dict[str, list[str]] = {}

        def file_rows(file: str) -> tuple[list[str], list[str]]:
            """파일의 (원문 줄들, `code_only` 줄들) — 여러 줄 주석을 파일째 가린다."""
            if file not in raw_lines:
                text = read_source(Location(file=file))
                raw_lines[file] = text.split("\n")
                code_lines[file] = code_only(text).split("\n")
            return raw_lines[file], code_lines[file]

        def line_code(file: str, number: int) -> str:
            code = file_rows(file)[1]
            return code[number - 1] if 1 <= number <= len(code) else ""

        marked_seed = set(dependency or ()) | set(anchors or ())
        acts_cache: dict[str, bool] = {}

        def macro_acts(name: str) -> bool:
            """로그처럼 보이는 매크로의 정의가 흐름을 바꾸나(`return`·`goto`·`abort`·`exit`·`assert`·`break`) — 그러면
            로그 문장이 아니다(검수 2026-10-10 r10: frr `STREAM_BOUND_WARN`은 경계 검사 뒤 `assert`)."""
            if name not in acts_cache:
                acts_cache[name] = any(
                    FLOW_CHANGE.search(code_only(read_source(Location(
                        file=r["file"], line_start=r["line_start"], line_end=r["line_end"]))))
                    for r in (index.get("macros", {}).get(name) or [])[:AMBIGUOUS_FILES])
            return acts_cache[name]

        def seed_blocks() -> list[tuple[Location, str]]:
            """근거 줄 정책의 정의 시드 — 블록마다 정의를 찾을 줄만(closure.py 「근거 줄 정책」, 개선안 DEF-C3).

            - 핵심(`CORE_KINDS`)·연결(`CONNECT_KINDS`) 블록: 보이는 줄 전체. 필수 정보 목록(nessary_info.md
              1~5)이 취약 함수 본문 어디서든 쓰인 전역·매크로·타입을 요구한다 — 데이터 의존 줄만 시드로 하면
              cJSON `firstByteMark`·`cJSON_malloc`·`global_ep`·타입 매크로가 빠졌다(2026-10-10 실측 9/15)
            - 보조 블록(그 밖의 피호출·래퍼·보조 함수·쓰기 창): `HELPER_LINES`줄 이하면 보이는 줄 전체. 길면
              **보이는 본문부터 줄인다** — 증거 줄(`EVIDENCE_LINE`), 값 매크로(대치식이 수·연산·sizeof)를 쓰는
              줄, 그 줄들이 쓰는 값을 대입하는 줄(두 번), 함수 머리, 끝 줄과 괄호 짝만 보이고(`aux_display`)
              그 보이는 줄 전체가 시드다. 본문과 정의가 어긋나지 않게(검토 2026-10-10)
            - 척추 블록: 없음(예전과 같다)
            - 줄을 골라 실은 블록(`kept_lines`)에서는 가드 본문(처리 문장)과 `case X:` 레이블 줄을 뺀다. 가드
              **조건** 줄은 남긴다 — `if (n > MAX_ENTRIES)`의 경계 상수(개선안 DEF-C9의 guard_cond 분리)."""
            # 가드 조건 줄 — 실린 줄의 모든 if/while/for 머리에서 괄호가 닫히는 줄까지. 머리가 이미 흐름 줄이라
            # guard_only가 아니어도 그 여러 줄 조건의 뒷줄(`    MAX_EXTRA) {`)은 조건이다(리뷰 2026-10-10)
            guard_cond: set[tuple[str, int]] = set()
            for key in ("code", "callee", "caller", "write"):
                for location, _text, part in sections[key]:
                    for number in part.get("kept_lines") or ():
                        if GUARD_HEAD.match(line_code(location.file, number)):
                            last, _cond = _condition_span(file_rows(location.file)[1], number)
                            guard_cond |= {(location.file, n) for n in range(number, last + 1)}
            guard_body = guard_only - guard_cond

            def uses_value_macro(file: str, number: int) -> bool:
                for word in IDENT_RE.findall(line_code(file, number)):
                    if word not in value_macros:
                        rows = index.get("macros", {}).get(word) or []
                        # strict — `unlikely(x)`·`container_of(…)`처럼 감싸기만 하는 함수형 매크로는 아니다
                        value_macros[word] = any(
                            value_like(code_only(read_source(Location(
                                file=r["file"], line_start=r["line_start"], line_end=r["line_end"]))), strict=True)
                            for r in rows[:AMBIGUOUS_FILES])
                    if value_macros[word]:
                        return True
                return False

            out: list[tuple[Location, str]] = []
            for key in ("code", "callee", "caller", "write"):
                for position, (location, _text, part) in enumerate(sections[key]):
                    kind = part["kind"]
                    # 척추는 정의를 찾지 않는다. 쓰기 창은 **쓰는 줄**(`site_lines`, 여러 줄 문장이면 그 시작까지)의 이름만
                    # — 창 줄에 같이 나온 전역 12개·로그 매크로가 붙었다(검수 2026-10-10 r9 libtiff)
                    if kind == "caller_spine" or location.line_start is None:
                        continue
                    if kind == "write_window":
                        sites_ = {n for n in part.get("site_lines") or () if n} & set(
                            part.get("kept_lines") or _span(location))
                        sites_ = _statement_starts(read_source, location, sites_) if sites_ else set()
                        # 함수 머리(파라미터 타입)도 — 쓰는 값이 파라미터에서 온다(cJSON `cJSON_InitHooks(cJSON_Hooks* hooks)`의
                        # `cJSON_Hooks`, nessary_info 항목 3이 빠졌다 — 2026-10-10 r20 필수 정보 검사 14/15)
                        enclosing = _narrowest_symbol(graph.symbols_for(location.file) or [], location.line_start)
                        if sites_ and enclosing is not None and enclosing.line_start == location.line_start:
                            sites_ |= set(range(location.line_start, _header_end(read_source, location) + 1)) & set(
                                part.get("kept_lines") or _span(location))
                        if sites_:
                            out.append((location, _elided(read_source, location, sites_)))
                        continue
                    shown = set(part.get("kept_lines") or _span(location))
                    if kind == "input_parser":
                        # Look up definitions only for input checks, value
                        # conversions, and the signature.  Incidental names in
                        # a parser are not all separate context requirements.
                        shown = {n for n in shown if EVIDENCE_LINE.search(line_code(location.file, n))
                                 or AUX_EVIDENCE.search(line_code(location.file, n))
                                 or GUARD_HEAD.match(line_code(location.file, n))
                                 or uses_value_macro(location.file, n)}
                        shown |= set(range(location.line_start,
                                           _header_end(read_source, location) + 1))
                    if kind == "entry_function" and opts.get("entry_seed_prune", 0):
                        # entry 본문은 그대로 보이고, 데이터 의존·시작 줄이 하나도 없는 큰 블록·`case` 묶음에서만 정의를 찾지
                        # 않는다 — sink 뒤 무관한 갈래의 상수·표(optee `switch (objectType)`의 TEE_TYPE_*·속성 표)가 정의
                        # 노이즈의 큰 몫이었다(판정 2026-10-10 r19b). 기본 꺼짐 — sink 뒤 갈래의 상수도 필수일 수 있다(cJSON
                        # `firstByteMark`, nessary_info 항목 2가 빠졌다: score.py 14/15)
                        flow_here = {n for f, n in set(dependency or ()) | set(anchors or ()) | {tuple(entry)}
                                     if f == location.file} & shown
                        # 마지막 흐름 줄 **뒤**의 블록만 — 앞쪽의 흐름 없는 갈래는 값을 정하는 경로일 수 있다(cJSON parse_string의
                        # `firstByteMark` 갈래를 빼자 필수 정보 항목 2가 빠졌다, 2026-10-10 r20 검사)
                        if flow_here - {entry[1]}:
                            after = max(flow_here)
                            shown -= {n for n in _empty_blocks(read_source, location, flow_here) if n > after}
                    if part.get("aux_display"):
                        pass  # 앞 라운드에서 보일 줄을 이미 정했다 — 보이는 줄 전체가 시드
                    elif kind not in CORE_KINDS | CONNECT_KINDS and _length(location) > HELPER_LINES:
                        found = {n for n in shown if EVIDENCE_LINE.search(line_code(location.file, n))
                                 or AUX_EVIDENCE.search(line_code(location.file, n))
                                 or any(RELEASE_CALL.fullmatch(c) for c in CALL_NAME.findall(line_code(location.file, n)))
                                 or uses_value_macro(location.file, n)}
                        names = {w for n in found for w in IDENT_RE.findall(line_code(location.file, n))}
                        for _ in range(2):
                            for n in sorted(shown):
                                lhs = LHS_NAME.match(line_code(location.file, n))
                                if n not in found and lhs and lhs.group(1) in names:
                                    found.add(n)
                                    names |= set(IDENT_RE.findall(line_code(location.file, n)))
                        # 함수 머리(파라미터 타입) — 블록이 함수 시작에서 시작할 때만. 쓰기·tainted_use 창은 함수
                        # 한가운데서 시작해 첫 `{`까지의 본문이 머리로 잡혔다(리뷰 2026-10-10)
                        enclosing = _narrowest_symbol(graph.symbols_for(location.file) or [], location.line_start)
                        if enclosing is not None and enclosing.line_start == location.line_start:
                            found |= set(range(location.line_start, _header_end(read_source, location) + 1)) & shown
                        # 처리 본문이 있는 가드(조건 + 탈출·오류 처리 + 중괄호)는 한 단위로 남긴다 — 실패를 반환값으로
                        # 알리는 호출의 검사(`if (! xcf_load_old_path (info, image)) return FALSE;`)가 patch 쪽 수정
                        # 줄인데 생략돼 판정이 확인가능 → 조건부로 떨어졌다(검수 2026-10-10 r6 gimp)
                        found |= _handled_guard_units(read_source, location, shown)
                        # 보이는 본문을 먼저 정한다 — 증거 줄·머리·끝 줄과 괄호 짝. 본문은 다 보이는데 정의는
                        # 증거 줄 것만 붙어 `SIGINIT`·`umm_heap`처럼 정의 없는 이름이 남았다(검토 2026-10-10)
                        # 문장 시작까지 — 여러 줄 호출의 꼬리(`O_RDONLY, MYF(MY_WME))))`)만 남았다(검수 2026-10-10 r8)
                        display = _balanced(read_source, location, _statement_starts(
                            read_source, location, (found | {location.line_end or location.line_start}) & shown))
                        part = {**part, "kept_lines": sorted(display), "aux_display": True}
                        sections[key][position] = (location, _elided(read_source, location, display), part)
                        shown = display
                    if part.get("kept_lines"):
                        # 줄을 골라 실은 블록에서만 — 통째로 실린 함수의 `case X:`는 실제 분기 값이다(zlib
                        # `case ZLIB_FILEFUNC_SEEK_SET:`). 예전 동작(`definition_text`)과 같은 범위
                        shown = {n for n in shown if (location.file, n) not in guard_body
                                 and not CASE_LABEL.match(line_code(location.file, n))}
                    # 로그·추적 호출 한 문장은 본문에는 두고 정의만 찾지 않는다 — 로그 매크로 사슬(`CLOG_*`·
                    # `kDebugAll`)이 정의의 큰 몫이었다(r9 Android av 정의 30→59줄). 본문에서 빼면 값을 쓰는 호출
                    # (`make_default_log_name(&x)`)·디버그 검사가 사라졌다(검수 2026-10-10 r8). 슬라이스 줄과 주소를
                    # 넘기는(`&x`) 문장은 시드에 둔다 — 형식 문자열 취약점·출력 인자
                    rows_here = code_only(read_source(location)).split("\n")
                    log_lines = _log_statement_lines(
                        rows_here, location.line_start, shown,
                        # 슬라이스 줄의 로그 문장은 형식 문자열 취약점(CWE-134)이거나 분류를 모를 때만 시드에 둔다 — slice
                        # 표시가 붙은 로그 줄로 로그 매크로 사슬이 다시 붙었다(검수 2026-10-10 r10 Android av patch B)
                        {n for f, n in marked_seed if f == location.file}
                        if opts.get("cwe") in (None, "CWE-134") else set(), macro_acts)
                    shown -= log_lines
                    if shown:
                        out.append((location, _elided(read_source, location, shown)))
            return out

        result = None
        definition_seen: list[Location] = []
        for _round in range(3):
            # 척추 블록(입력 출처 — 다른 프로그램의 main 등)은 정의를 찾지 않는다 — 그 프로그램의
            # 타입·매크로(WavPack winamp `HANDLE`·cli `fopen`)가 끌려왔다
            def definition_text(location: Location, text: str, part: dict) -> str:
                # 가드로만 실린 줄은 정의를 찾지 않는다(위 `guard_only`)
                # `switch` 구조로만 실린 `case X:` 레이블도 — 레이블 상수(`PB_LTYPE_*`)의 `#define`이 줄줄이 붙었다
                kept = set(part.get("kept_lines") or ())
                drop = {n for f, n in guard_only if f == location.file} & kept
                if kept:
                    start = location.line_start or 1
                    rows = code_only(read_source(location)).split("\n")
                    drop |= {n for n in kept if 0 <= n - start < len(rows) and CASE_LABEL.match(rows[n - start])}
                return _elided(read_source, location, kept - drop) if drop else text

            code_blocks = [
                (location, definition_text(location, text, part))
                for key in ("code", "callee", "caller", "write")
                for location, text, part in sections[key]
                if part["kind"] != "caller_spine"
            ]
            # 슬라이스 줄 중 **출력에 실린 줄만** — 긴 함수는 데이터 의존 줄만 실으므로, 안 실린 줄의
            # 이름에 정의를 붙이면 아무 블록도 안 쓰는 정의가 떴다(jhead `M_*` · WavPack 헤더 상수)
            visible = set()
            for key in ("code", "callee", "caller", "write"):
                for location, _text, part in sections[key]:
                    visible |= {(location.file, n)
                                for n in (part.get("kept_lines") or _span(location))}
            texts_of = {(location.file, location.symbol): text for location, text in code_blocks}

            def locals_of(file: str, symbol: str) -> frozenset[str] | None:
                # 인덱스가 모르는 함수(어휘로 찾은 jq `decToString`)는 본문의 선언에서 — 아니면 그
                # 지역(`exp`·`cut`·`up`)이 이름으로 남아 다른 함수의 같은 이름이 정의로 붙었다
                known = graph.locals_of(file, symbol)
                if known or (file, symbol) not in texts_of:
                    return known
                return _lexical_locals(texts_of[(file, symbol)])

            carried_blocks = seed_blocks() if evidence else code_blocks
            # sink 뒤에만 남은 줄(forward `after_target`)의 변수 타입·필드 경로는 펼치지 않는다 —
            # jhead process_COM의 `strcpy(ImageInfo.Comments, …)`가 `ImageInfo_t` 구조체를 끌어왔다
            seed_lines = [point for point in lines
                          if point in visible and point not in (after_target or ())
                          and not CASE_LABEL.match(code_only(read_source(Location(
                              file=point[0], line_start=point[1], line_end=point[1]))))]
            result = collect(
                closure,
                near_file=anchor.file,
                carried=carried_blocks,
                slice_lines=seed_lines,
                function_at=function_at,
                locals_of=locals_of,
                system=system,
                policy="evidence" if evidence else "all",
            )
            if evidence:
                # 라운드마다 collect가 전체를 다시 계산하므로(별칭 피호출이 늘면 남길 멤버도 는다) 앞 라운드의
                # 정의 블록을 걷어 내고 이번 결과로 다시 짓는다 — 1라운드 축약 블록이 seen에 남아 넓어지지
                # 않았다(리뷰 2026-10-10). 「모든 이름」 정책은 예전처럼 덧붙인다(출력 순서 그대로)
                seen[:] = [other for other in seen if other not in definition_seen]
                definition_seen.clear()
                definitions.clear()
                counts["definitions"] = 0
            for location, text, kind, note, trimmed in result["blocks"]:
                if any(_overlaps(location, other) for other in seen):
                    continue
                seen.append(location)
                definition_seen.append(location)
                kept_lines = None
                if evidence:
                    # 정의 클로저가 붙이기 전에 줄였다(closure `_trim`) — 남긴 줄을 괄호 짝만 맞춰 보인다
                    if trimmed is not None:
                        kept_lines = _balanced(read_source, location, set(trimmed), heads=False)
                        text = _elided(read_source, location, kept_lines)
                        # 멤버가 하나도 안 남은 타입(`struct tiff { /* … */ };`)은 내보내지 않는다 — 머리·끝만으로는
                        # 아무것도 확인할 수 없다(검수 2026-10-10 r9 libtiff)
                        if kind in ("type", "field_type") and not _has_member(code_only(text)):
                            counts["empty_definitions"] = counts.get("empty_definitions", 0) + 1
                            continue
                elif (kind == "variable" and _length(location) > TABLE_LINES and "{" in text
                        and location.symbol not in slice_names):
                    # 큰 데이터 표(전역 배열 초기화) — 슬라이스 줄이 안 쓰면 선언 머리만. jhead
                    # `TagTable`(106줄, 태그 이름 출력용)은 경계 판단에 안 쓰인다.
                    rows = text.split("\n")
                    text = "\n".join([rows[0], f"    /* ... {len(rows) - 2} lines of initializer "
                                      "omitted (not used on slice lines) ... */", rows[-1]])
                if not evidence and kind in ("type", "field_type") and _length(location) > BIG_TYPE_LINES:
                    # 큰 구조체·enum — 머리·끝 줄과 실린 코드가 쓰는 멤버의 줄만(괄호 짝 유지). held-out
                    # curl `fix_hostname` backward의 98K 중 64K가 urldata.h의 200줄짜리 구조체 통째였다
                    used = set(IDENT_RE.findall("\n".join(code_only(t) for _l, t in code_blocks)))
                    start = location.line_start or 1
                    keep = {start, location.line_end or start}
                    for offset, row in enumerate(code_only(text).split("\n")):
                        if _member_names(row) & used:
                            keep.add(start + offset)
                    # 타입 몸체에는 감싸는 머리 규칙을 안 쓴다 — 중첩 `union {`·`struct {`·`} Name;`이 줄줄이 붙었다
                    kept_lines = _balanced(read_source, location, keep, heads=False)
                    text = _elided(read_source, location, kept_lines)
                block = _block(location, text, end=location.line_end)
                if kept_lines is not None:
                    head, _, body = block.partition("\n")
                    block = f"{head[:-len(' ===== */')]} (selected lines) ===== */\n{body}"
                if note:
                    head, _, body = block.partition("\n")
                    block = f"{head}\n{note}\n{body}"
                definitions.append((block, {**_part("definition", location),
                                            "definition_kind": kind,
                                            "conditional": note is not None,
                                            **({"kept_lines": sorted(kept_lines)}
                                               if kept_lines is not None else {})}))
                counts["definitions"] += 1
                if wrappers and (target := macro_alias_target(text)) is not None:
                    aliases.add(target)
            asked = result["asked"]
            fresh = False
            if opts["callee_hops"] and (forwarded := aliases & asked):
                for function in graph.functions_named(sorted(forwarded), anchor.file,
                                                      **hygiene) or []:
                    if _length(function) > SMALL_FUNCTION:
                        # 피호출과 같은 크기 규칙 — 길면 머리만(libexpat XmlInitUnknownEncoding 71줄)
                        if signature(function):
                            fresh = True
                    elif carry_callee(function, "alias_callee"):
                        counts["alias_callees"] += 1
                        through_wrapper(function, 1)
                        fresh = True
            if not fresh:
                break
        if result["system"]:
            trailer.append((
                f"/* ===== system headers ({result['stats']['system']['platform']}) ===== */\n"
                + "\n".join(result["system"]),
                {"kind": "system_definitions", "file": None, "symbol": None,
                 "line_start": None, "line_end": None, "count": len(result["system"])},
            ))
        unset = sorted(set(result["stats"]["config_unset"]) | dropped_switches)
        if unset:
            trailer.append((
                "/* build-configuration macros with no definition in repo (treated as undefined"
                + ("; their #ifdef regions omitted" if dropped_switches else "") + "): "
                + ", ".join(unset) + " */",
                None,
            ))
        if result["stats"]["not_found"]:
            trailer.append((
                "/* not found in repo or system headers: "
                + ", ".join(result["stats"]["not_found"]) + " */",
                None,
            ))
        # 펼치지 않은 필드 타입(`unexpanded_types`)은 계측에만 — 출력에 이름을 늘어놓으면 libtiff
        # 26개·libexpat 20개짜리 주석 줄이 노이즈가 됐다(v19)
        counts["alias_definitions"] = len(aliases & asked)
        definition_stats = result["stats"]

    # 머리 — entry 파일의 #include와 실린 코드가 부르는 함수의 전방 선언.
    called = {
        name
        for key in ("code", "callee", "caller", "write")
        for _, text, _ in sections[key]
        for name in CALL_NAME.findall(code_only(text))
    } - CALL_KEYWORDS
    prologue: list[tuple[str, dict]] = []
    # 근거 줄 정책에서도 그대로 — 필수 정보 목록(nessary_info.md 8)이 `#include` 연결과 내부 전방 선언을
    # 보존하라고 요구한다(개선안 DEF-C8의 「include 목록 한 줄로」는 그 요구와 어긋나 적용하지 않는다)
    for location in _prologue(read_source, anchor.file, called):
        if any(_overlaps(location, other) for other in seen):
            continue
        seen.append(location)
        prologue.append((_block(location, read_source(location)), _part("prologue", location)))
        counts["prologue_lines"] += _length(location)

    # 통째로 실은 함수에서 슬라이스가 지나는 줄(데이터 의존 + 시작·끝 줄) 끝에 `/* <- slice */`.
    # entry 함수를 원문 그대로 실으면 슬라이서가 고른 줄이 출력에 안 보였고, source·sink가 한
    # 함수에 있는 표본은 backward·forward 출력이 바이트까지 같았다(cJSON·libmspack — 리뷰 #7·#8).
    marked = set(dependency or ()) | set(anchors or ())

    def mark(location: Location, text: str) -> str:
        start = location.line_start or 1
        rows = text.split("\n")
        # 블록 주석 안에서 끝나는 줄에는 달지 않는다 — 표시의 `*/`가 열린 주석을 닫아 뒤가 코드로 읽혔다(리뷰 2026-10-10)
        states = _comment_states(rows)
        for offset, row in enumerate(rows):
            if (location.file, start + offset) in marked and row.strip() \
                    and not row.rstrip().endswith("\\") and not states[offset][1]:
                rows[offset] = f"{row.rstrip()}  {SLICE_MARK}"
        return "\n".join(rows)

    def render(location: Location, text: str, part: dict) -> str:
        if part.get("kept_lines") and location.line_start is not None:
            # 줄을 골라 실은 블록은 여기서 다시 짓는다 — 남긴 줄을 감싼 `#if`/`#else`/`#endif`를 같이 보이고(지시문
            # 없이 두 분기가 다 보여 `free(str)`가 두 번 불리는 것처럼 읽혔다, 검수 2026-10-10 openssl), 정의 안 된
            # 스위치로 지운 줄(`dead_lines`)은 다시 넣지 않고, slice 표시는 접은 뒤 줄에 맞춰 단다
            keep = set(part["kept_lines"])
            body = code_only(read_source(location)).split("\n")
            keep |= _directive_lines(body, location.line_start, keep)
            keep -= set(part.get("dead_lines") or ())
            text = _elided(read_source, location, keep,
                           marked if part["kind"] in ("entry_function", "slice_function") else None)
        elif part["kind"] in ("entry_function", "slice_function"):
            text = mark(location, text)
        if part.get("kept_lines"):
            # 함수에서 고른 줄만 실은 블록 — 범위는 함수 전체, 실린 줄은 part `kept_lines`
            label = "slice lines only" if part["kind"] == "slice_lines" else "selected lines"
            return (f"/* ===== {location.file}:{location.line_start}-{location.line_end} "
                    f"({label}) ===== */\n{text}")
        if part["kind"] == "input_dispatch":
            return f"/* ===== {location.file} (selected input dispatch lines) ===== */\n{text}"
        return _block(location, text, end=anchor_end if location is anchor else None)

    ordered: list[tuple[str, dict | None]] = [
        (render(location, text, part), part) for location, text, part in sections["code"]
    ]
    ordered += prologue + definitions + trailer
    for key in ("callee", "caller", "write"):
        ordered += [(render(location, text, part), part)
                    for location, text, part in sections[key]]
    blocks = [text for text, _ in ordered]
    parts = [part for _, part in ordered if part is not None]

    source = "\n".join(blocks) if len(blocks) > 1 else anchor_text
    stats = {
        "functions": sum(p["kind"] in ("entry_function", "slice_function") or
                         p["kind"].endswith("callee") or p["kind"] in ("caller_helper", "input_parser")
                         for p in parts),
        "uncovered_lines": uncovered,
        "nonproduct_lines_dropped": len(dropped),
        "chars": len(source),
        "anchor_chars": len(anchor_text),
        **counts,
        # 상한을 없앴으니 0이다 — 하네스 이벤트와 같은 키로 읽히게 남긴다.
        "dropped": 0,
        "callees_dropped": 0,
        "definitions_dropped": 0,
        "callers_dropped": 0,
        "write_windows_dropped": 0,
        "options": opts,
        "definition_closure": definition_stats,
    }
    return source, {"stats": stats, "parts": parts, "entry_function": _part("entry", anchor)}


def _part(kind: str, location: Location) -> dict:
    return {
        "kind": kind,
        "file": location.file,
        "symbol": location.symbol,
        "line_start": location.line_start,
        "line_end": location.line_end,
    }


def attach_document(
    document: dict,
    graph: GraphIndex,
    repo: Path,
    options: dict | None,
    *,
    index: dict | None = None,
    system: SystemHeaders | None = None,
    pov_options: dict | None = None,
) -> dict:
    """Keep v1 Auditor in `attached`; optionally add parser context in `pov_attached`."""
    root = repo.as_posix().rstrip("/") + "/"
    for item in document.get("slices", []):
        entry = item["entry"]
        lines = [(p["file"], p["line"]) for p in item["lines"]]
        # 시작·끝 줄: 시작점(candidate_anchor — degraded면 양 끝 GT 줄) + 순회가 닿은 반대쪽 끝 노드
        anchors = {tuple(p) for key in ("candidate_anchor", "target_anchor")
                   for p in item.get("line_provenance", {}).get(key, [])}
        for point in item.get("analysis", {}).get("reached", []) or []:
            file = str(point.get("file", "")).replace("\\", "/").removeprefix(root).removeprefix("./")
            if file and type(point.get("line")) is int:
                anchors.add((file, point["line"]))
        provenance = item.get("line_provenance", {})
        dependency = {tuple(p) for key in ("data_dependency", "candidate_anchor")
                      for p in provenance.get(key, [])} if provenance else None
        after_target = {tuple(p) for p in provenance.get("after_target", [])} if provenance else set()
        spec = (document.get("target") or {}).get("spec") or {}
        cwe = (document.get("target") or {}).get("cwe")
        spans = [(r["file"], r.get("function"), r["function_span"][0], r["function_span"][1])
                 for r in spec.values() if isinstance(r, dict) and r.get("function_span")]
        common = dict(index=index, system=system, anchors=anchors, dependency=dependency,
                      after_target=after_target, entry_spans=spans,
                      backward=item.get("direction") == "backward",
                      cross_method_flow=item.get("analysis", {}).get("cross_method_flow", []),
                      input_evidence=item.get("analysis", {}).get("input_evidence"))
        base_options = {**(options or {}), **({"cwe": cwe} if cwe else {})}
        text, meta = attach(graph, repo, (entry["file"], entry["line"]), lines,
                            {**base_options, "input_parser_functions": 0}, **common)
        item["attached"] = {"source": text, **meta}
        if pov_options is not None:
            text, meta = attach(graph, repo, (entry["file"], entry["line"]), lines,
                                {**base_options, **pov_options}, **common)
            item["pov_attached"] = {"source": text, **meta}
        else:
            item.pop("pov_attached", None)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--slices", required=True, type=Path, help="slice-v2 JSON")
    parser.add_argument("--repo", required=True, type=Path, help="슬라이스를 만든 저장소")
    parser.add_argument("--index", required=True, type=Path, help="indexer.py가 만든 index.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--compiler", default="gcc", help="시스템 헤더를 읽을 C 전처리기")
    parser.add_argument("--no-system", action="store_true", help="시스템 헤더 조회를 끈다")
    parser.add_argument("--with-pov", action="store_true", help="기존 Auditor와 별도로 pov_attached도 생성")
    for key, value in DEFAULT_OPTIONS.items():
        if isinstance(value, str):
            parser.add_argument("--" + key.replace("_", "-"), default=value, choices=("evidence", "all"))
        else:
            parser.add_argument("--" + key.replace("_", "-"), type=int, default=value)
    args = parser.parse_args(argv)
    graph = GraphIndex(args.index)
    summary = graph.summary
    if summary.status != "loaded":
        print(f"index not usable: {summary.status}", file=sys.stderr)
        return 2
    document = json.loads(args.slices.read_text(encoding="utf-8"))
    options = {key: getattr(args, key) for key in DEFAULT_OPTIONS}
    index = json.loads(args.index.read_text(encoding="utf-8"))
    attach_document(
        document, graph, args.repo.resolve(), options,
        index=index, system=None if args.no_system else SystemHeaders(args.compiler),
        pov_options={} if args.with_pov else None,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
