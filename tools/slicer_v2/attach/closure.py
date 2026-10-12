"""Auditor 입력에 붙일 **정의 클로저** — 타입은 구조체까지, 상수는 끝까지 (사용자 결정 2026-10-04).

하네스 `definitions_of`(이름 하나에 정의가 하나뿐일 때만, 재귀 없음)를 대신한다. 그 규칙으로는
jhead에서 싱크 버퍼 `ImageInfo.Comments[MAX_COMMENT_SIZE]`가 든 `ImageInfo_t` 정의가 빠졌다 —
`ImageInfo_t ImageInfo;`까지만 붙고 그 안의 타입 이름을 다시 찾지 않았다.

## 무엇을 따라가나

1. **이름** — 실린 함수 본문의 이름(하네스와 같은 `definition_names` — 지역·필드 전용 이름 제외)을
   매크로 → enum 상수 → 타입 → 전역 변수 순으로 찾는다.
2. **타입 — 구조체에 닿을 때까지.** 슬라이스 줄이 쓰는 변수(지역·파라미터·전역)의 선언 타입에서
   시작해 typedef 사슬을 따라가다 몸체가 있는 struct·union·enum 정의에서 멈춘다. 구조체 필드의
   타입은 더 펼치지 않는다 —
3. **단, 슬라이스 줄에 나온 필드 경로는 펼친다.** `Info.hdr.depth`면 `Info`의 구조체에서 `hdr`
   필드의 타입, 그 구조체에서 `depth` 필드의 타입까지 붙인다.
4. **상수 — 끝까지.** 붙인 정의(구조체·typedef·선언·매크로 값)와 슬라이스 줄에 나오는 매크로 ·
   enum 상수 · const 전역을 붙이고, 그 값이 다른 상수를 쓰면 그것도 붙인다. 한 줄 `#define`이 같은
   접두어의 `#define`들과 빈 줄 없이 붙어 있으면 그 묶음째 붙인다(`Closure.macro_family`).
5. **저장소 밖** — 저장소에 정의가 없는 타입과 대문자 상수는 시스템 헤더에서 찾는다(`system.py`).
   거기도 없으면 「찾지 못함」으로 적는다.

## 정의가 여럿이면

- `extern` 선언보다 정의(초기화·저장 공간이 있는 선언)를 쓴다.
- 전역 변수는 실린 코드의 파일이 `#include`로 닿는 파일의 정의만 — 아니면 닿는 파일에 그 이름의 `extern`
  선언이 있을 때만(같은 이름의 다른 프로그램 전역을 안 붙인다).
- 비제품 경로(`tests/` 등)는 제품 정의가 있으면 뺀다(하네스 `is_nonproduct_path`).
- 같은 파일 → 그 파일이 `#include`로 닿는 파일 → 저장소의 **헤더 파일** 순으로 좁힌다. 어디에도 없으면
  붙이지 않는다(다른 프로그램 `.c`의 같은 이름 정의를 안 붙인다).
- 그래도 여럿이면 **다 붙이고** 블록마다 `conditional definition k of n` 주석을 단다(`#ifdef`
  분기 등 — 빌드 설정을 몰라 고를 근거가 없다). 서로 다른 파일 `AMBIGUOUS_FILES`개를 넘으면
  이름만으로는 못 가르는 흔한 이름이라 붙이지 않고 `ambiguous`에 적는다.

## 근거 줄 정책(`policy="evidence"`, 개선안 DEF-C3·C4·C5·C7·C9, 2026-10-10)

`carried`가 실린 블록 전체가 아니라 **근거 줄**이다 — 취약 함수·슬라이스 함수와 그것을 잇는 블록은 보이는
줄 전체, 보조 블록(피호출·래퍼·보조 함수·쓰기 창)은 증거 줄과 머리만, 가드 본문은 빼고(attach `seed_blocks`).
그 위에서

- 상수 큐에 hop을 단다 — 근거 줄의 이름이 1, 붙인 정의가 쓰는 이름은 부모 + 1. `EVIDENCE_HOPS`를
  넘으면 배열 크기(`[...]` 안)이거나 값·검사 정의(`value_like` 매크로·enum) 안에 쓰인 이름만,
  `EVIDENCE_MAX_HOPS`를 넘으면 아무것도 안 붙인다.
- 필드 접근 자리(`p->type`의 `type`)·구조체 멤버 선언 이름·근거 줄의 지역·`#if` 줄 이름은 큐에 안 넣는다.
- 구조체·enum·큰 표는 **붙이기 전에** 근거 줄이 쓰는 멤버만 남기고(`_trim`) 그 남은 줄로만 다시 찾는다 —
  잘린 멤버가 쓰던 매크로가 고아로 붙지 않게. `sizeof`·캐스트·메모리 복사 인자의 타입은 자르지 않는다.
- 매크로 묶음(`macro_family`)은 편다 — 필수 정보 목록(nessary_info.md 5)이 비트 플래그 묶음 전체를
  요구한다(이름만으로 값이나 비트 관계를 추정하지 않게).
"""

import heapq
import re
from collections import deque
from collections.abc import Callable
from pathlib import Path

from common import Location
from index_graph import is_nonproduct_path
from source_definitions import _C_KEYWORDS, _names, code_only, definition_names

AMBIGUOUS_FILES = 4
MACRO_FAMILY_LINES = 16
# 근거 줄 정책의 상수 hop 상한 — 넘으면 배열 크기·값·검사 정의만(hop ≤ MAX)
EVIDENCE_HOPS = 2
EVIDENCE_MAX_HOPS = 4
# 근거 줄 정책에서 멤버만 남길 타입·enum의 최소 줄 수 — 이하면 통째로. 필수 정보 목록(nessary_info.md 1)이
# 9줄짜리 cJSON 구조체를 「정의 전체」로 요구한다 — attach의 사후 축약(BIG_TYPE_LINES)과 같은 40
TRIM_LINES = 40
# 근거 줄 정책에서 머리·끝만 남길 전역 표(초기화)의 최소 줄 수
TABLE_TRIM_LINES = 20
# 구조체 멤버 선언의 이름 — `char name[LEN];`·`int flag : 1;`·`int a, b;`
MEMBER_DECL = re.compile(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?::\s*\w+\s*)?[;,]")
BRACKETS = re.compile(r"\[([^\[\]]*)\]")
# 값 매크로 — 대치식이 수·연산자·sizeof·다른 이름뿐인 `#define`(함수형 포함). 문장·블록·호출은 아니다
DEFINE_PARTS = re.compile(r"\s*#\s*define\s+[A-Za-z_]\w*(\([^)]*\))?(.*)$", re.DOTALL)


CHECK = re.compile(r"[<>]=?|==|!=|\bsizeof\b")
# 별칭 판정에서 빼는 타입 낱말 — `((size_t)LIMIT)`도 이름 하나를 넘기는 별칭이다
CAST_WORDS = frozenset({"const", "unsigned", "signed", "char", "short", "int", "long", "size_t", "ssize_t",
                        "uint8_t", "uint16_t", "uint32_t", "uint64_t", "int8_t", "int16_t", "int32_t",
                        "int64_t", "void", "struct", "union", "enum"})


def value_like(code: str, strict: bool = False) -> bool:
    """`#define`(주석·문자열 뺀 원문)가 **값이나 검사**를 담나 — 근거 줄 정책에서 그 안에 쓰인 이름은 hop
    상한을 넘어도 따라간다(값·경계를 계산하는 사슬이라서).

    - 식 매크로: 대치식에 수·연산자·sizeof가 있다(`(1 << 4)`, `256`, `(HDR + 4)`). 다른 이름 하나로
      바꾸기만 하는 별칭(`#define adler32 z_adler32`)은 아니다.
    - 문장 매크로(`;`·`{` 있음): 본문에 비교나 sizeof가 있다 — 경계 검사(zlib `PULL`의 `if (have == 0)`).
    - `strict`(보조 블록의 증거 줄 판정)면 함수형 매크로는 비교·sizeof가 있어야 한다 — `unlikely(x)`·
      `container_of(…)`처럼 감싸기만 하는 매크로를 쓰는 줄이 모두 증거가 되지 않게."""
    match = DEFINE_PARTS.match(code)
    if not match:
        return False
    body = match.group(2).replace("\\\n", " ").replace("->", " ")
    if not body.strip():
        return False
    if re.search(r"[;{}]", body) or (strict and match.group(1) is not None):
        return bool(CHECK.search(body))
    return bool(re.search(r"(?<!\w)\d|\bsizeof\b|[-+*/%<>|&^~!?]", body))


def _alias(code: str) -> bool:
    """`#define`가 다른 이름 하나로 넘기기만 하나 — `#define NAME_LEN (MAX_NAME)`·`((size_t)LIMIT)`."""
    match = DEFINE_PARTS.match(code)
    if not match:
        return False
    body = match.group(2).replace("\\\n", " ")
    if re.search(r"(?<!\w)\d|[-+%<>|&^~!?;{}=]|\bsizeof\b", body.replace("*)", ")")):
        return False
    return len([n for n in IDENT.findall(body) if n not in CAST_WORDS]) == 1
CAST_TAG = re.compile(r"(?:\(\s*(?:const\s+)?|sizeof\s*\(\s*)(struct|union|enum)\s+([A-Za-z_]\w*)\s*\**\s*\)")
# typedef 이름 캐스트 `(hdr_t *)buf` — typedef로 확인된 이름만 쓴다
TYPEDEF_CAST = re.compile(r"\(\s*(?:const\s+)?([A-Za-z_]\w*)\s*\*+\s*\)")
SIZEOF_VAR = re.compile(r"\bsizeof\s*\(?\s*\**\s*([A-Za-z_]\w*)")
# `container_of(ptr, struct foo, member)`·`list_entry(…)`의 멤버 인자 — 필드 접근이다
MEMBER_MACRO = re.compile(r"\b(?:container_of|list_entry|list_first_entry|list_last_entry|hlist_entry|"
                          r"offsetof)\s*\((?:[^,()]|\([^()]*\))*,(?:[^,()]|\([^()]*\))*?,?\s*([A-Za-z_]\w*)\s*\)")
FIELD_ACCESS = re.compile(r"(?:->|\.)\s*([A-Za-z_]\w*)")
# 함수 포인터 멤버 `int (*read)(void *buf, size_t n);` — 이름은 `(*` 뒤, 파라미터 목록은 멤버가 아니다
FUNC_POINTER = re.compile(r"\(\s*\*\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*\)\s*\(")
ATTRIBUTE = re.compile(r"__attribute__\s*\(\((?:[^()]|\([^()]*\))*\)\)")
MEMORY_CALL = re.compile(r"\b(?:memcpy|memmove|memset|memcmp|fread|read|recv|recvfrom|strncpy|strcpy|"
                         r"strncat|strcat|snprintf|sprintf)\s*\(([^;]*)\)")
HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx", ".h++", ".inc", ".inl", ".ih"})
DEFINE_LINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)")
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
CONSTANT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
INCLUDE = re.compile(r'^\s*#\s*include\s*([<"])([^>"]+)[>"]', re.MULTILINE)
# 필드 경로 — `a.b->c`. 첨자는 미리 지운다(`a[i].b` → `a.b`).
FIELD_PATH = re.compile(r"\b([A-Za-z_]\w*)((?:\s*(?:\.|->)\s*[A-Za-z_]\w*)+)")
SUBSCRIPT = re.compile(r"\[[^\[\]]*\]")
# 함수형 매크로의 파라미터 — 정의를 찾을 이름이 아니다(`#define container_of(ptr, TYPE, MEMBER)`).
MACRO_HEAD = re.compile(r"^\s*#\s*define\s+[A-Za-z_]\w*\(([^)]*)\)", re.MULTILINE)
# 조건부 컴파일 줄 — 거기 나온 이름은 값이 아니라 빌드 설정 스위치일 수 있다.
CONDITIONAL = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif|elifdef|elifndef)\b(.*)$", re.MULTILINE)
# 컴파일러가 미리 정의하는 이름 — 저장소에도 헤더에도 정의가 없는 것이 정상이다.
PREDEFINED = frozenset({
    "__FILE__", "__LINE__", "__DATE__", "__TIME__", "__COUNTER__", "__func__", "__FUNCTION__",
    "__PRETTY_FUNCTION__", "__VA_ARGS__", "__VA_OPT__", "__STDC__", "__STDC_VERSION__",
    "__cplusplus", "__GNUC__", "__clang__", "defined",
})


class Closure:
    """인덱스(raw index.json)와 저장소 위의 정의 조회·클로저."""

    def __init__(self, index: dict, repo: Path, read_source: Callable[[Location], str]):
        self.index = index
        self.repo = repo
        self.read_source = read_source
        self.files = set(index.get("files", []))
        self.by_name: dict[str, list[str]] = {}
        for file in self.files:
            self.by_name.setdefault(Path(file).name, []).append(file)
        self._includes: dict[str, set[str]] = {}
        self._system_includes: dict[str, set[str]] = {}
        self._texts: dict[str, list[str]] = {}
        self._depths: dict[str, list[int]] = {}

    # --- include 그래프 ----------------------------------------------------------------

    def _direct_includes(self, file: str) -> tuple[set[str], set[str]]:
        if file in self._includes:
            return self._includes[file], self._system_includes[file]
        local, system = set(), set()
        try:
            text = (self.repo / file).read_text("utf-8", errors="replace")
        except OSError:
            text = ""
        for _quote, target in _active_includes(text):
            resolved = self._resolve(target, file)
            if resolved:
                local |= resolved
            else:
                system.add(target)
        self._includes[file], self._system_includes[file] = local, system
        return local, system

    def _resolve(self, target: str, including: str) -> set[str]:
        here = _normalize(f"{Path(including).parent.as_posix()}/{target}")
        candidates = self.by_name.get(Path(target).name, [])
        if here in candidates:
            return {here}
        return {c for c in candidates if c == target or c.endswith("/" + target)}

    def reachable(self, file: str) -> set[str]:
        """`file`과 그것이 include로 (전이적으로) 닿는 저장소 파일."""
        seen = {file}
        queue = deque([file])
        while queue:
            for nxt in self._direct_includes(queue.popleft())[0]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def system_headers(self, files: set[str]) -> list[str]:
        """`files`가 include로 닿는 범위에서 저장소에 없는 `#include` 대상."""
        found: set[str] = set()
        for file in files:
            for reached in self.reachable(file):
                found |= self._direct_includes(reached)[1]
        return sorted(found)

    # --- 행 고르기 ---------------------------------------------------------------------

    def select(
        self, rows: list[dict], near_file: str, also: set[str] | frozenset = frozenset()
    ) -> tuple[list[dict], str | None]:
        """정의 행 고르기 — (고른 행들, 못 고른 사유). 모듈 docstring 「정의가 여럿이면」.
        `also`는 near_file 말고도 「닿는」 파일로 칠 파일들(실린 코드 블록이 있는 파일과 그 include)."""
        if not rows:
            return [], "missing"
        if any(not r.get("extern") for r in rows):
            rows = [r for r in rows if not r.get("extern")]
        if not is_nonproduct_path(near_file):
            product = [r for r in rows if not is_nonproduct_path(r["file"])]
            rows = product or rows
        # 다른 아키텍처의 같은 이름 정의는 뺀다 — xen `arch/arm/mm.c`의 sink에 `asm-x86/p2m.h`·`asm-x86/mm.h`의
        # 변형이 `conditional definition k of n`으로 함께 붙었다(2026-10-10 4회차). 아키텍처 성분이 없는 행(공용
        # 헤더)은 그대로, near_file에 아키텍처가 없으면 거르지 않는다
        here_arch = _arch(near_file)
        if here_arch and len(rows) > 1:
            same = [r for r in rows if _arch(r["file"]) in (None, here_arch)]
            rows = same or rows
        here = [r for r in rows if r["file"] == near_file]
        if not here:
            reach = self.reachable(near_file) | set(also)
            here = [r for r in rows if r["file"] in reach]
        if not here:
            # include로 안 닿으면 **헤더의 정의만** — include 경로를 못 푼 진짜 정의일 수 있다. 다른
            # 프로그램 .c 파일의 같은 이름(WavPack cli/*.c의 `#define fopen`, audition의 타입)은 아니다.
            here = [r for r in rows if Path(r["file"]).suffix.lower() in HEADER_SUFFIXES]
            if not here:
                return [], "unreachable"
        chosen = here
        if len({r["file"] for r in chosen}) > AMBIGUOUS_FILES:
            return [], "ambiguous"
        unique = {(r["file"], r["line_start"], r["line_end"]): r for r in chosen}
        return sorted(unique.values(), key=lambda r: (r["file"], r["line_start"])), None

    def rows(self, table: str, name: str) -> list[dict]:
        return list(self.index.get(table, {}).get(name, []))

    def macro_rows(self, name):
        return [self.macro_family(row, name) for row in self.rows("macros", name)]

    def _file_lines(self, file: str) -> list[str]:
        if file not in self._texts:
            try:
                text = (self.repo / file).read_text("utf-8", errors="replace")
            except OSError:
                text = ""
            self._texts[file] = text.split("\n")
        return self._texts[file]

    def macro_family(self, row: dict, name: str) -> dict:
        """한 줄짜리 `#define`이 같은 접두어의 `#define`들과 빈 줄 없이 붙어 있으면 그 묶음 전체.

        enum 상수를 enum 전체로 붙이는 것과 같은 이유다 — 플래그·종류 상수 묶음(cJSON
        `cJSON_False`..`cJSON_Object` = `1 << 0`..`1 << 6`)은 하나만 보면 다른 값과 겹치는지 모른다.
        접두어는 이름의 첫 `_`까지(없으면 묶지 않는다). 묶음이 `MACRO_FAMILY_LINES`줄을 넘으면 묶지
        않고 그 줄 하나만 쓴다."""
        prefix = name[: name.find("_") + 1] if "_" in name.strip("_") else ""
        if not prefix or row["line_start"] != row["line_end"]:
            return row
        lines = self._file_lines(row["file"])

        def member(number: int) -> bool:
            if not 1 <= number <= len(lines):
                return False
            match = DEFINE_LINE.match(lines[number - 1])
            return bool(match) and match.group(1).startswith(prefix) and not lines[
                number - 1].rstrip().endswith("\\")

        start = end = row["line_start"]
        while member(start - 1):
            start -= 1
        while member(end + 1):
            end += 1
        # 상한보다 긴 묶음은 묶지 않는다 — 이름마다 다른 16줄 창이 서로 겹쳐, 붙이는 쪽(attach)의 겹침
        # 검사가 뒤의 것을 버렸다(jhead `M_COM`·`TAG_*` 정의가 통째로 빠짐).
        if end - start + 1 > MACRO_FAMILY_LINES:
            return row
        return {**row, "line_start": start, "line_end": end}

    def enumerator_rows(self, name):
        return self.rows("enumerators", name)

    def global_rows(self, name):
        """함수 밖 선언 — 단 중괄호 안(깊이 > 0)에서 시작하는 행은 뺀다. tree-sitter가 함수 머리를
        못 읽으면 그 본문의 지역 선언이 `func` 없이 남아 전역처럼 보였다(jq decNumber.c의 `exp`·
        `cut`·`up`). 들여쓰기로는 못 가른다 — jhead는 전역을 들여 쓴다(`       int ShowTags = FALSE;`)."""
        # typedef 이름은 파일 범위 변수일 수 없다 — 매크로로 시작하는 여러 줄 원형(`XMLPUBFUN xmlNodePtr XMLCALL` +
        # 다음 줄 `f(...)`)의 첫 줄이 반환 타입 이름의 전역으로 색인돼 머리만 있는 빈 정의 블록이 붙었다(검수
        # 2026-10-10 r8 libxml2). 태그(`types`)는 같은 이름의 전역이 합법이라(`struct stat stat;`) 보지 않는다
        if self.rows("typedef_targets", name):
            return []
        return [
            r for r in self.rows("var_types", name)
            if r.get("func") is None and not self._inside_braces(r)
        ]

    def self_alias_global(self, row: dict, name: str) -> bool:
        """함수 `name`의 기호 별칭 선언(`extern __typeof (f) f __attribute((alias("f__internal_alias")))`) —
        정의 있는 함수와 같은 이름의 `extern` 선언은 변수가 아니다(C에서 함수와 전역은 이름을 나누지 않는다)."""
        if not self.rows("functions", name):
            return False
        if row.get("extern"):
            return True
        text = code_only(self.read_source(_location(row, name)))
        return bool(re.search(rf"__typeof__?\s*\(\s*{re.escape(name)}\s*\)|__attribute__?\s*\(\(\s*alias", text))

    def rename_macro(self, row: dict, name: str) -> bool:
        """함수 `name`을 저장소 어디에도 없는 이름으로 바꾸기만 하는 `#define name TARGET`(링크 별칭) — 호출은 이미
        색인된 본문으로 간다. TARGET이 저장소에 있으면(`#define gf_strdup gf_mem_strdup`) 진짜 우회라 둔다."""
        if not self.rows("functions", name):
            return False
        match = DEFINE_PARTS.match(code_only(self.read_source(_location(row, name))).strip())
        if not match or match.group(1):
            return False
        target = match.group(2).strip()
        if not re.fullmatch(r"[A-Za-z_]\w*", target):
            return False
        return not any(self.rows(table, target)
                       for table in ("functions", "macros", "var_types", "types", "typedef_targets"))

    def _inside_braces(self, row: dict) -> bool:
        depths = self._brace_depths(row["file"])
        number = row.get("line_start") or 0
        return 1 <= number <= len(depths) and depths[number - 1] > 0

    def _brace_depths(self, file: str) -> list[int]:
        """줄마다 그 줄이 시작할 때의 `{` 깊이 — 주석·문자열을 빼고 센다. `extern "C" {`·
        `namespace … {`의 괄호는 세지 않는다(그 안의 선언은 전역이다)."""
        if file not in self._depths:
            depths, depth, skipped = [], 0, []
            raw = self._file_lines(file)
            code = code_only("\n".join(raw)).split("\n")
            for number, line in enumerate(code):
                depths.append(depth)
                wrapper = re.match(r'\s*(?:extern\s*"C"|namespace\b)', raw[number] if number < len(raw) else "")
                for char in line:
                    if char == "{":
                        skipped.append(bool(wrapper))
                        depth += not wrapper
                    elif char == "}" and skipped:
                        depth -= not skipped.pop()
            self._depths[file] = depths
        return self._depths[file]

    def type_rows(self, ref: dict) -> list[dict]:
        """타입 참조 → 정의 행. `type`이면 typedef, 태그면 그 종류의 몸체 있는 정의."""
        name, kind = ref["name"], ref["kind"]
        if kind == "type":
            return self.rows("typedef_targets", name)
        return [r for r in self.rows("types", name) if r.get("kind") == kind]


ARCH_PART = re.compile(r"(?:^|/)(?:arch/([^/]+)/|include/asm-([^/]+)/|asm-([^/]+)/)")
# 같은 아키텍처의 다른 이름 — 경로마다 쓰는 이름이 다르다(linux `arch/x86` · `asm-x86`, `arch/arm64`)
ARCH_ALIASES = {"x86_64": "x86", "i386": "x86", "amd64": "x86", "aarch64": "arm64", "arm32": "arm"}


def _arch(path: str) -> str | None:
    """경로의 아키텍처 성분(`arch/arm/…`·`include/asm-x86/…`의 arm·x86) — 없으면 None."""
    match = ARCH_PART.search(path.lower())
    if not match:
        return None
    name = next(group for group in match.groups() if group)
    return ARCH_ALIASES.get(name, name)


def _normalize(path: str) -> str:
    parts: list[str] = []
    for part in path.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return "/".join(parts)


def _location(row: dict, symbol: str) -> Location:
    return Location(
        file=row["file"], symbol=symbol, line_start=row["line_start"], line_end=row["line_end"]
    )


# 다른 플랫폼을 고르는 매크로 — 그 참 분기의 #include는 따라가지 않는다(분석 대상은 x86_64 Linux 빌드,
# 시스템 헤더도 그 gcc로 읽는다). held-out curl: curl_setup.h의 `#ifdef WIN32 #include "config-win32.h"`
# 등으로 다른 플랫폼 설정 파일의 정의가 붙었다.
PLATFORM = re.compile(
    r"\b(?:_?WIN32|_WIN64|_MSC_VER|__MINGW32__|__CYGWIN__|_WIN32_WCE|WINAPI_FAMILY|__APPLE__|"
    r"__MACH__|macintosh|__VMS|VMS|MSDOS|__DJGPP__|__OS400__|AMIGA|__amigaos__|__riscos__|NETWARE|"
    r"__NOVELL_LIBC__|__SYMBIAN32__|TPF|__TANDEM|OS2|__OS2__|__WATCOMC__|__BORLANDC__|__ANDROID__|"
    r"__HAIKU__|__BEOS__|__QNX__|__sun|__hpux|_AIX|__vxworks)\b"
)


def _active_includes(text: str) -> list[tuple[str, str]]:
    """`INCLUDE.findall`과 같은 (따옴표, 대상) 목록 — 단 플랫폼 매크로를 요구하는 조건 분기
    (`#ifdef WIN32`·`#if defined(__APPLE__)`의 참 분기, `#ifndef …`의 `#else`) 안의 것은 뺀다."""
    found = []
    stack: list[bool] = []  # 이 분기가 다른 플랫폼 전용인가
    for line in text.split("\n"):
        directive = re.match(r"\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)", line)
        if directive:
            kind, rest = directive.groups()
            platform = bool(PLATFORM.search(rest)) and "!" not in rest
            if kind in ("ifdef", "if"):
                stack.append(platform)
            elif kind == "ifndef":
                stack.append(False)
            elif kind == "elif" and stack:
                stack[-1] = platform
            elif kind == "else" and stack:
                stack[-1] = False
            elif kind == "endif" and stack:
                stack.pop()
            continue
        if any(stack):
            continue
        match = INCLUDE.match(line)
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def _defined_in(name: str, code: str) -> bool:
    """`code`(주석·문자열 뺀 원문)에 `name`의 정의 꼴이 있나 — `#define`, enum 상수(`NAME =`·
    `NAME,`·`NAME }`), typedef 이름(`} NAME;`·`typedef … NAME;`), 초기화 있는 선언(`TYPE NAME =`)."""
    n = re.escape(name)
    return re.search(
        rf"#\s*define\s+{n}\b"
        rf"|^\s*{n}\s*(?:=(?!=)[^,;]*)?\s*(?:,|\}}|$)"
        rf"|\}}\s*{n}\s*;"
        rf"|\btypedef\b[^;]*\b{n}\s*;"
        rf"|\b[A-Za-z_]\w*[\s\*]+{n}\s*(?:\[[^\]]*\]\s*)*=(?!=)",
        code, re.MULTILINE,
    ) is not None


def _code_names(text: str) -> set[str]:
    return set(IDENT.findall(code_only(text))) - _C_KEYWORDS


def _members(row: str) -> set[str]:
    """구조체 몸체 한 줄이 선언하는 멤버 이름 — 함수 포인터(`(*read)(…)`, 파라미터 이름은 아니다)와
    `__attribute__`가 붙은 선언 포함."""
    row = ATTRIBUTE.sub(" ", row)
    pointers = set(FUNC_POINTER.findall(row))
    return pointers if pointers else set(MEMBER_DECL.findall(row))


def _enumerators(text: str) -> set[str]:
    """enum 몸체(의 일부)에서 열거자 이름 — 항목마다 `=` 왼쪽의 이름만(`K_A = BASE`의 BASE는 값이다)."""
    found = set()
    for item in re.split(r"[{},]", text):
        match = re.match(r"\s*([A-Za-z_]\w*)\s*(?:=|$)", item)
        if match:
            found.add(match.group(1))
    return found - _C_KEYWORDS


def _shape(code: str) -> str:
    """정의 원문의 꼴 — `macro` · `enum` · `record`(struct·union 몸체) · `table`(`= {` 초기화) · `other`."""
    if DEFINE_PARTS.match(code):
        return "macro"
    if "{" not in code:
        return "other"
    head = code[: code.find("{")]
    if "=" in head:
        return "table"
    if re.search(r"\benum\b", head):
        return "enum"
    if re.search(r"\b(?:struct|union)\b", head):
        return "record"
    return "other"


def _declared_inside(code: str) -> set[str]:
    """`{` 안에서 **선언되는** 이름 — 구조체·union 멤버(`char name[LEN];`)와 enum 열거자. 정의를 찾을
    이름이 아니다(멤버 `type`이 같은 철자의 매크로·enum을 끌어왔다). 초기화 목록·enum 값식·매크로 본문의
    이름(`{ HDR_LEN, … }`·`K_A = BASE`·`return ERR_RANGE;`)은 값이라 여기 들지 않는다(리뷰 2026-10-10)."""
    shape = _shape(code)
    body = code[code.find("{") + 1:] if "{" in code else ""
    if shape == "enum":
        return _enumerators(body)
    if shape == "record":
        return {n for row in body.split("\n") for n in _members(row)} - _C_KEYWORDS
    return set()


def _trim(text: str, start: int, kind: str, used: set[str], table_indexed: "bool | str | None",
          fields: set[str] | None = None) -> set[int] | None:
    """근거 줄 정책의 정의 축약 — 남길 줄 번호(절대), 또는 `None`(통째로).

    - 구조체·union(`TRIM_LINES`줄 초과): 머리(`{` 줄까지)·끝 줄·근거 줄이 **필드로 접근하는**(`fields`,
      없으면 `used`) 멤버를 선언하는 줄, 그 줄을 감싼 중첩 블록의 머리와 닫는 줄. 이름만 같은 변수
      (파라미터 `b`와 멤버 `int b;`)로는 남기지 않는다.
    - enum(`TRIM_LINES`줄 초과): 쓰인 열거자마다 그 앞의 가장 가까운 `=` 줄(없으면 첫 열거자)부터 그
      열거자까지 전부 — 암묵 값을 셀 수 있게. 그 사슬에 전처리 줄이 끼고 `TRIM_LINES`줄보다 길면 쓰인 열거자 줄만.
    - 전역 표(`= {` 초기화, `TABLE_TRIM_LINES`줄 초과): 근거 줄이 첨자·필드·`sizeof`·인자로 쓰면
      (`table_indexed`) 통째로, 아니면 머리·끝만. 매크로는 자르지 않는다."""
    rows = text.split("\n")
    code = code_only(text).split("\n")
    total = len(rows)
    if not any("{" in c for c in code):
        return None
    shape = _shape("\n".join(code))
    if shape == "macro" or shape == "other":
        return None
    if shape == "table":
        if total <= TABLE_TRIM_LINES or table_indexed is True or table_indexed == "element":
            return None
        keep = {0, total - 1}
        if table_indexed == "whole":
            # 표를 통째로 넘기거나 크기만 쓴다(`sizeof(T)/sizeof(T[0])`·`add_vars(T)`) — 머리, 첫 원소, 끝 두 원소
            # (끝 표시 `{NullS, NullS, SHOW_LONG}`), 닫는 줄. 몇 개인지는 생략 표시의 줄 수로 보인다(검수 2026-10-10
            # r8 mariadb `status_vars` 통째 29K)
            first = next((i for i, c in enumerate(code) if "{" in c), 0)
            keep |= set(range(first + 1))
            entries = [i for i in range(first + 1, total - 1) if code[i].strip()]
            keep |= set(entries[:1]) | set(entries[-2:])
        return {start + i for i in keep}
    if total <= TRIM_LINES:
        return None
    first = next(i for i, c in enumerate(code) if "{" in c)
    keep = set(range(first + 1)) | {total - 1}
    chain_start = first + 1
    for i in range(first + 1, total - 1):
        row = code[i].strip()
        if shape == "enum":
            if "=" in row:
                chain_start = i
            if _enumerators(row) & used:
                chain = range(chain_start, i + 1)
                if any(rows[k].lstrip().startswith("#") for k in chain) and len(chain) > TRIM_LINES:
                    # 사슬에 전처리 줄이 끼면 값은 빌드 설정에 달린다 — 짧으면 사슬째(분기마다 셀 수 있게), 길면 쓰인
                    # 열거자 줄만. 통째로는 안 붙인다: neomutt 262줄 옵션 enum이 `option(OPTNOCURSES)` 하나로 통째
                    # 붙었다(2026-10-10 r10)
                    keep.add(i)
                    continue
                keep.update(chain)
        elif _members(row) & (fields if fields is not None else used):
            keep.add(i)
    # 남긴 멤버를 감싼 중첩 `struct {`·`union {`의 머리와 닫는 줄
    stack: list[int] = []
    parents: dict[int, list[int]] = {}
    closers: dict[int, int] = {}
    for i, row in enumerate(code):
        parents[i] = list(stack)
        for char in row:
            if char == "{":
                stack.append(i)
            elif char == "}" and stack:
                closers[stack.pop()] = i
    for i in list(keep):
        for opener in parents[i]:
            keep.add(opener)
            keep.add(closers.get(opener, opener))
    if len(keep) >= total:
        return None
    return {start + i for i in keep}


def _table_used(name: str, code: str) -> str | None:
    """근거 줄이 전역 표 `name`을 어떻게 쓰나 — `"element"`(원소·필드 접근: 통째로), `"whole"`(크기·인자로 통째
    넘김: 머리와 첫·끝 원소), `None`(머리·끝만)."""
    n = re.escape(name)
    if re.search(rf"\b{n}\s*(?:\[|\.|->)", code):
        return "element"
    if re.search(rf"\bsizeof\s*\(?\s*{n}\b|[(,]\s*&?\s*{n}\s*[,)]", code):
        return "whole"
    return None


def _call_args(text: str) -> list[str]:
    """`text` 안 메모리 복사·읽기 호출의 인자 중 이름 하나(`&x`·`x`·`*x`)인 것 — 그 선언 타입은 자르지 않는다."""
    names = []
    for args in MEMORY_CALL.findall(text):
        for arg in args.split(","):
            match = re.fullmatch(r"\s*[&*]?\s*([A-Za-z_]\w*)\s*", arg)
            if match:
                names.append(match.group(1))
    return names


def field_paths(text: str) -> list[tuple[str, list[str]]]:
    """슬라이스 줄의 필드 경로 — (바탕 이름, [필드…])."""
    code = code_only(text)
    while SUBSCRIPT.search(code):
        code = SUBSCRIPT.sub("", code)
    found = []
    for match in FIELD_PATH.finditer(code):
        fields = re.findall(r"[A-Za-z_]\w*", match.group(2))
        found.append((match.group(1), fields))
    return found


def collect(
    closure: Closure,
    *,
    near_file: str,
    carried: list[tuple[Location, str]],
    slice_lines: list[tuple[str, int]],
    function_at: Callable[[str, int], str | None],
    locals_of: Callable[[str, str], frozenset[str] | None],
    system: "object | None" = None,
    policy: str = "all",
) -> dict:
    """붙일 정의들과 기록. 반환:

    - `blocks`: [(위치, 원문, 종류, 주석 또는 None, 남긴 줄 또는 None)] — 붙일 순서. 남긴 줄은 근거 줄
      정책이 축약한 정의(`_trim`)의 절대 줄 번호이고, 원문도 그 줄만 담는다.
    - `system`: 시스템 헤더에서 찾은 매크로·typedef 원문들(없으면 빈 목록)
    - `stats`: 종류별 수 · `ambiguous` · `not_found` · `asked`(조회한 이름)

    `policy`: `"all"`은 `carried`(실린 블록 전체)의 모든 이름에서 끝까지, `"evidence"`는 `carried`가
    근거 줄이고 hop 상한·붙이기 전 축약을 쓴다(모듈 docstring 「근거 줄 정책」).
    """
    evidence = policy == "evidence"
    blocks: list[tuple[Location, str, str, str | None, set[int] | None]] = []
    # 붙인 행 → (blocks의 자리, 남긴 줄 — None이면 통째로)
    attached: dict[tuple[str, int, int], tuple[int, set[int] | None]] = {}
    looked: set[tuple[str, str]] = set()
    ambiguous: set[str] = set()
    missing_types: set[str] = set()
    missing_constants: set[str] = set()
    unexpanded: set[str] = set()
    hop_limited: set[str] = set()
    switches: set[str] = set()  # `#if`·`#ifdef` 줄에 나온 이름
    # 상수 큐 — (이름, hop, 까닭: dim · value · other). 근거 줄 정책은 hop 순서(우선순위 큐)로 꺼낸다: 먼저
    # 들어간 hop 2 항목이 같은 이름의 hop 1보다 먼저 처리돼 자식이 상한에 걸렸다(리뷰 2026-10-10)
    constants: deque[tuple[str, int, str]] = deque()
    heap: list[tuple[int, int, str, str]] = []
    pushed = [0]

    def push(name: str, hop: int, reason: str) -> None:
        if evidence:
            pushed[0] += 1
            heapq.heappush(heap, (hop, pushed[0], name, reason))
        else:
            constants.append((name, hop, reason))

    counts = {"variable": 0, "type": 0, "field_type": 0, "constant": 0, "conditional": 0, "trimmed": 0,
              "self_alias": 0}
    # 근거 줄이 쓰는 이름(필드 이름 포함) — 축약에서 남길 열거자
    seed_code = "\n".join(code_only(text) for _where, text in carried) if evidence else ""
    used = set(IDENT.findall(seed_code))
    # 근거 줄이 필드로 접근하는 이름 — 구조체에서 남길 멤버. `p->len`·`s.name`·`offsetof(T, m)`·
    # `container_of(p, T, m)`, 그리고 근거 줄이 쓰는 매크로 접근자의 본문(`SB_BLOCK(s)` → `(s)->block_size`)
    accessed = set(FIELD_ACCESS.findall(seed_code)) | set(MEMBER_MACRO.findall(seed_code))
    if evidence:
        for name in used:
            for row in closure.rows("macros", name)[:AMBIGUOUS_FILES]:
                body = code_only(closure.read_source(_location(row, name)))
                accessed |= set(FIELD_ACCESS.findall(body)) | set(MEMBER_MACRO.findall(body))

    def attach_rows(rows: list[dict], name: str, kind: str, trim: bool = True) -> list[str]:
        """행들을 붙이고, 붙인 원문들을 돌려준다(상수 클로저가 다시 훑는다). 근거 줄 정책이면 축약한
        줄만 담고, 이미 축약해 붙인 행을 다른 이름으로 다시 물으면 그 이름의 줄까지 넓혀 **새로 드러난 줄**만
        돌려준다 — 같은 enum의 두 번째 열거자·값 기준(`E_LAST = E_BASE + 64`의 E_BASE)이 빠졌다(리뷰 2026-10-10)."""
        texts = []
        # 내용(주석·공백 무시)이 같은 정의는 하나만 — 두 헤더에 똑같이 있는 것(WavPack
        # include/wavpack.h · src/wavpack_local.h의 `WavpackHeader`)을 "빌드 설정에 따라 다른
        # 정의 1 of 2 / 2 of 2"로 보이게 했다
        distinct: dict[str, dict] = {}
        for row in rows:
            body = re.sub(r"\s+", "", code_only(closure.read_source(_location(row, name))))
            distinct.setdefault(body, row)
        rows = list(distinct.values())
        total = len(rows)
        for number, row in enumerate(rows, 1):
            key = (row["file"], row["line_start"], row["line_end"])
            location = _location(row, name)
            full = closure.read_source(location)
            rows_of = full.split("\n")
            indexed = _table_used(name, seed_code)
            if key in attached:
                index_of, before = attached[key]
                if not evidence or before is None:
                    continue
                after = None if not trim else _trim(full, row["line_start"], kind, used | {name}, indexed,
                                                     accessed | {name})
                grown = None if after is None else before | after
                if grown == before:
                    continue
                old = blocks[index_of]
                text = full if grown is None else "\n".join(rows_of[n - row["line_start"]] for n in sorted(grown))
                blocks[index_of] = (old[0], text, old[2], old[3], grown)
                attached[key] = (index_of, grown)
                fresh = set(range(row["line_start"], row["line_end"] + 1)) if grown is None else grown
                texts.append("\n".join(rows_of[n - row["line_start"]] for n in sorted(fresh - before)))
                continue
            text, kept = full, None
            if evidence and trim:
                kept = _trim(full, row["line_start"], kind, used | {name}, indexed, accessed)
                if kept is not None:
                    text = "\n".join(rows_of[n - row["line_start"]] for n in sorted(kept))
                    counts["trimmed"] += 1
            note = (
                f"/* conditional definition {number} of {total} for {name} "
                "(build configuration unknown) */"
                if total > 1
                else None
            )
            counts["conditional"] += total > 1
            counts[kind] += 1
            attached[key] = (len(blocks), kept)
            blocks.append((location, text, kind, note, kept))
            texts.append(text)
        return texts

    def queue_constants(text: str, hop: int = 1, exclude: frozenset[str] = frozenset(),
                        inherited: str = "other") -> None:
        code = code_only(text)
        params = {p for m in MACRO_HEAD.findall(code) for p in IDENT.findall(m)}
        for line in CONDITIONAL.findall(code):
            switches.update(set(IDENT.findall(line)) - PREDEFINED)
        if not evidence:
            for name in sorted(_code_names(text) - params - PREDEFINED):
                push(name, hop, "other")
            return
        # `#if` 줄의 이름(빌드 스위치)·필드 접근 자리·멤버·열거자 선언 이름·근거 줄의 지역은 큐에 안 넣는다
        plain = CONDITIONAL.sub("", code)
        dims = {n for inner in BRACKETS.findall(plain) for n in IDENT.findall(inner)}
        names = _names(plain) - params - PREDEFINED - _declared_inside(plain) - exclude
        # 부모 정의가 값·검사(`value_like` 매크로, enum 몸체)면 그 안의 이름은 값을 계산하는 사슬이다. 이름
        # 하나로 넘기는 별칭(`#define NAME_LEN (MAX_NAME)`)은 부모의 까닭을 물려준다 — 배열 크기 사슬이
        # 별칭에서 끊겼다(리뷰 2026-10-10)
        # 함수형 매크로는 비교·sizeof가 있어야 값·검사다(strict) — `zlog_debug(...)`처럼 숫자 인자만 넘기는 감싸기가
        # 값으로 쳐져 DEBUG → zlog_* → _zlog_ecref 사슬이 hop 4까지 열렸다(검수 2026-10-10 frr +37%)
        value = value_like(plain.strip(), strict=True) or _shape(plain) == "enum"
        reason = "value" if value else inherited if _alias(plain.strip()) else "other"
        for name in sorted(names):
            push(name, hop, "dim" if name in dims else reason)

    owners_of: dict[tuple[str, str], list[str]] = {}
    protected: set[str] = set()  # 자르지 않는 타입 이름 — sizeof·캐스트·메모리 복사 인자의 타입
    protected_seen: set[str] = set()  # 보호해서 통째로 붙인 타입(사슬 포함) — 시스템 헤더 접기에서도 뺀다

    def resolve_type(ref: dict, kind: str = "type", hop: int = 1, protect: bool = False) -> list[str]:
        """타입 참조를 구조체까지 붙이고, 그 사슬의 이름들(필드 표의 `owner`로 찾을 이름)을 돌려준다.
        같은 타입을 다시 물으면 붙이지 않고 기억해 둔 이름들만 준다."""
        if ref["kind"] == "builtin":
            return []
        key = (ref["kind"], ref["name"])
        if key in owners_of:
            return owners_of[key]
        owners_of[key] = owners = [ref["name"]]  # 순환이면 여기서 끊긴다
        looked.add(key)
        rows, why = closure.select(closure.type_rows(ref), near_file, allowed)
        # 태그로 보호된 구조체를 typedef 이름으로 먼저 물어도(`ntlmv2_resp_t *r` + `(struct ntlmv2_resp *)`) 보호
        protect = protect or ref["name"] in protected or any(
            t["name"] in protected for row in rows for t in row.get("targets", []))
        if protect:
            protected_seen.add(ref["name"])
        if why in ("missing", "unreachable"):
            missing_types.add(ref["name"])
            return owners
        if why == "ambiguous":
            ambiguous.add(ref["name"])
            return owners
        for text in attach_rows(rows, ref["name"], kind, trim=not protect):
            queue_constants(text, hop + 1)
        for row in rows:
            if ref["kind"] != "type":
                continue
            if row.get("has_body"):  # typedef struct tag {...} T — 필드의 owner는 태그일 수 있다
                owners.extend(t["name"] for t in row.get("targets", []))
            else:
                for target in row.get("targets", []):
                    owners.extend(resolve_type(target, kind, hop, protect))
        return owners

    # 전역 변수는 실린 코드의 파일이 #include로 닿는 파일의 것만 — 아니면 그 전역을 `extern`으로 선언한
    # 파일이 닿을 때만. 같은 이름의 다른 프로그램 전역이 붙었다(libexpat: 도구 gennmtab.c의 `name` 128줄).
    allowed: set[str] = set()
    for reach_from in {near_file} | {where.file for where, _ in carried}:
        allowed |= closure.reachable(reach_from)

    def global_select(name: str) -> tuple[list[dict], str | None]:
        rows = closure.global_rows(name)
        # 함수의 기호 별칭 선언은 변수가 아니다 — libxml2 elfgcchack.h가 부르는 함수마다 한 줄씩 붙었다(검수 r8)
        aliases_ = [r for r in rows if closure.self_alias_global(r, name)]
        if aliases_:
            counts["self_alias"] += len(aliases_)
            rows = [r for r in rows if r not in aliases_]
        if not rows:
            return [], "missing"
        declared = any(r.get("extern") and r["file"] in allowed for r in rows)
        picked = [r for r in rows if declared or r["file"] in allowed]
        if not picked:
            return [], "unreachable"
        # 닿는 헤더에 extern 선언이 있으면 정의는 같은 프로그램의 다른 .c에 있어도 그것이다
        # (jhead `ImageInfo`·`Sections` — jhead.h의 extern, 정의는 jhead.c·jpgfile.c). 단 닿지 않는 .c
        # 여럿에 정의가 있으면 플랫폼·빌드별 변형이라 고를 근거가 없다 — 선언만 붙인다(libtiff
        # `_TIFFerrorHandler`: tif_unix.c · tif_win32.c · tif_wince.c).
        if declared:
            defined = [r for r in picked if not r.get("extern")]
            if defined and not any(r["file"] in allowed for r in defined) and \
                    len({r["file"] for r in defined}) > 1:
                return closure.select([r for r in picked if r.get("extern")], near_file, allowed)
        return closure.select(picked, near_file,
                              allowed | ({r["file"] for r in picked} if declared else set()))

    def variable_types(name: str, file: str, line: int, hop: int = 1) -> list[dict]:
        """이름의 선언 타입 — 그 줄의 함수의 지역·파라미터, 아니면 전역. 전역이면 선언도 붙인다."""
        function = function_at(file, line)
        if function:
            own = [
                r for r in closure.rows("var_types", name)
                if r.get("func") == function and r["file"] == file
            ]
            if own:
                return [t for r in own for t in r.get("types", [])]
        rows, _ = global_select(name)
        if ("global", name) not in looked and rows:
            looked.add(("global", name))
            for text in attach_rows(rows, name, "variable"):
                queue_constants(text, hop + 1)
        return [t for r in rows for t in r.get("types", [])]

    if evidence:
        # 크기·배치를 읽어야 하는 타입은 자르지 않는다 — `sizeof(struct X)`·`(T *)` 캐스트·`sizeof(*p)`·
        # 메모리 복사·읽기 인자(`fread(&hdr, …)`)의 선언 타입(개선안 DEF-C9)
        for where, text in carried:
            code = code_only(text)
            protected.update(tag for _kind, tag in CAST_TAG.findall(code))
            # typedef 이름 그대로의 `sizeof(hdr_t)`·`(hdr_t *)buf`(리뷰 2026-10-10)
            protected.update(name for name in set(SIZEOF_VAR.findall(code)) | set(TYPEDEF_CAST.findall(code))
                             if closure.rows("typedef_targets", name) or closure.rows("types", name))
            line = where.line_start or 1
            function = function_at(where.file, line)
            for name in set(SIZEOF_VAR.findall(code)) | set(_call_args(code)):
                own = [r for r in closure.rows("var_types", name)
                       if r.get("func") == function and r["file"] == where.file] if function else []
                rows = own or closure.global_rows(name)
                for ref in (t for r in rows for t in r.get("types", [])):
                    if ref["kind"] != "builtin":
                        protected.add(ref["name"])

    # 1. 슬라이스 줄의 변수 → 타입 → 구조체, 그리고 필드 경로
    for file, line in slice_lines:
        text = closure.read_source(Location(file=file, line_start=line, line_end=line))
        locals_here = None
        function = function_at(file, line)
        if function:
            locals_here = locals_of(file, function) or frozenset()
        # 근거 줄의 지역(`len`)은 같은 철자의 매크로·enum을 찾지 않는다
        queue_constants(text, 1, (locals_here or frozenset()) if evidence else frozenset())
        for name in sorted(_code_names(text)):
            is_local = bool(locals_here and name in locals_here)
            if not is_local and not closure.global_rows(name):
                continue
            for ref in variable_types(name, file, line):
                resolve_type(ref)
        for base, fields in field_paths(text):
            owners = []
            for ref in variable_types(base, file, line):
                owners += resolve_type(ref)
            for field in fields:
                refs = [
                    t
                    for r in closure.rows("field_types", field)
                    if r.get("owner") in set(owners)
                    for t in r.get("types", [])
                ]
                owners = []
                for ref in refs:
                    owners += resolve_type(ref, "field_type")
                if not owners:
                    break

    # 2. 실린 함수 본문의 이름 — 하네스와 같은 이름 집합, 새 고르기 규칙
    for _where, text in carried:
        for line in CONDITIONAL.findall(code_only(text)):
            switches.update(set(IDENT.findall(line)) - PREDEFINED)
    names = definition_names(carried, locals_of) - PREDEFINED
    for name in sorted(names):
        push(name, 1, "other")
        if ("global", name) not in looked and closure.global_rows(name):
            looked.add(("global", name))
            rows, why = global_select(name)
            if why == "ambiguous":
                ambiguous.add(name)
            for text in attach_rows(rows, name, "variable"):
                queue_constants(text, 2)
        if closure.rows("typedef_targets", name):
            resolve_type({"kind": "type", "name": name})
    # 캐스트·sizeof의 구조체 태그 — `(struct ntlmv2_resp *)buf`는 변수 선언이 아니라 위 규칙으로 안
    # 잡혔다(held-out ksmbd: 실제 쓰기 길이를 정하는 구조체). 태그 이름은 definition_names가 뺀다.
    for _where, text in carried:
        for kind, tag in CAST_TAG.findall(code_only(text)):
            resolve_type({"kind": kind, "name": tag}, protect=True)

    # 3. 상수 — 「모든 이름」 정책은 끝까지, 근거 줄 정책은 hop 상한까지
    while constants or heap:
        if constants:
            name, hop, reason = constants.popleft()
        else:
            hop, _seq, name, reason = heapq.heappop(heap)
        if evidence and (hop > EVIDENCE_MAX_HOPS or (hop > EVIDENCE_HOPS and reason == "other")):
            if ("constant", name) not in looked:
                hop_limited.add(name)
            continue
        if ("constant", name) in looked:
            continue
        looked.add(("constant", name))
        hop_limited.discard(name)
        for table, getter in (
            ("macro", closure.macro_rows),
            ("enumerator", closure.enumerator_rows),
        ):
            raw = getter(name)
            if not raw:
                continue
            if table == "macro":
                # 함수 이름을 저장소에 없는 이름으로 바꾸기만 하는 매크로(링크 별칭)는 붙이지 않는다 — 다 그렇면
                # 찾은 것으로 친다(못 찾음·열거자로 넘기지 않는다)
                renames = [closure.rename_macro(row, name) for row in closure.rows("macros", name)]
                if len(renames) == len(raw) and any(renames):
                    counts["self_alias"] += sum(renames)
                    raw = [row for row, rename in zip(raw, renames) if not rename]
                    if not raw:
                        break
            # enum 상수의 행은 그것이 든 enum 정의 전체 범위다 — 값이 앞 상수에 기대므로 통째로 붙인다
            # (근거 줄 정책은 쓰인 열거자와 그 앞 `=` 줄만 — `_trim`).
            rows, why = closure.select(raw, near_file, allowed)
            if why == "unreachable" and table == "macro":
                continue  # 안 닿는 매크로 — 같은 이름의 닿는 enum 상수가 있을 수 있다(jhead `READ_ANY`)
            if why == "ambiguous":
                ambiguous.add(name)
            if why == "unreachable" and CONSTANT_NAME.match(name):
                missing_constants.add(name)  # 저장소 정의가 안 닿으면 시스템 헤더에서 찾는다
            for text in attach_rows(rows, name, "constant"):
                queue_constants(text, hop + 1, inherited=reason)
            break
        else:
            const_rows = [r for r in closure.global_rows(name) if r.get("const")]
            if const_rows:
                rows, why = closure.select(const_rows, near_file, allowed)
                for text in attach_rows(rows, name, "constant"):
                    queue_constants(text, hop + 1, inherited=reason)
            elif (
                CONSTANT_NAME.match(name)
                and not closure.global_rows(name)
                and not closure.rows("functions", name)
                and not closure.rows("types", name)
            ):
                missing_constants.add(name)
            elif closure.rows("typedef_targets", name) and ("type", name) not in looked:
                # 붙인 정의(구조체 필드 등)에 나온 타입 이름 — 정의는 있지만 필드 타입은 펼치지 않는다
                # (모듈 docstring 2). 「못 찾음」과 따로 적는다 — 조용히 사라지지 않게(libexpat
                # `PREFIX defaultPrefix;`, 리뷰 2026-10-04 v17)
                unexpanded.add(name)

    # 4. 저장소 밖 — 시스템 헤더. `#if` 줄에만 나온 정의 없는 이름은 빌드 설정 스위치(정의 안 됨)로
    #    따로 적는다 — 값을 찾을 상수가 아니다.
    config_unset = sorted(missing_constants & switches)
    missing_constants -= switches
    system_blocks: list[str] = []
    not_found = sorted(missing_types | missing_constants)
    system_meta = None
    if system is not None and not_found:
        headers = closure.system_headers({near_file} | {loc.file for loc, _ in carried})
        system_blocks, resolved, system_meta = system.lookup(
            headers, sorted(missing_constants), sorted(missing_types),
            **({"members_used": used, "protected": protected | protected_seen} if evidence else {}),
        )
        not_found = sorted(set(not_found) - resolved)
    # 붙인 정의·실린 코드 안에 정의가 보이는 이름은 「못 찾음」이 아니다 — enum 안의 `READ_ANY = 5`,
    # 붙인 config 헤더의 `#define TIFF_UINT8_T`, 실린 함수의 지역 `const int EXIF_MAX_SIZE = …`
    shown = "\n".join([code_only(text) for _l, text, _k, _n, _kept in blocks]
                      + [code_only(text) for _l, text in carried])
    not_found = [name for name in not_found if not _defined_in(name, shown)]
    return {
        "blocks": blocks,
        "system": system_blocks,
        "stats": {
            **counts,
            "policy": policy,
            "system_definitions": len(system_blocks),
            "system": system_meta,
            "ambiguous": sorted(ambiguous),
            "not_found": not_found,
            "unexpanded_types": sorted(unexpanded - {key[1] for key in looked if key[0] == "type"}),
            "config_unset": config_unset,
            "hop_limited": sorted(hop_limited),
            "protected_types": sorted(protected),
        },
        "asked": {name for kind, name in looked if kind == "constant"},
    }
