"""NE(need_evidence) resolver — Scout가 후속 턴에 요청한 심볼을 정규화·조회하고,
그 결과를 RETRIEVED/UNFILLED 텍스트로 렌더한다 (#63 후속, ADR 0012 결정 2·3).

명세 원천은 Notion 「Scout RAG baseline」이다(스냅샷
`…/ddaa755d-…/scratchpad/notion_rag_baseline_2026-09-26.txt` — 바이트 대조 통과). 이 모듈의
문자열은 그 원문을 그대로 옮긴 것이고, 의역하지 않는다.

`resolve()`가 조립 지점이다 — `GraphIndex.table()`(조회)과 `read_lines`(파일 읽기) 훅을
받아 `Resolution`을 낸다. 계약(`ResolveQuery`·`Resolution`·`UnfilledReason` 등)은
`src/schemas/need_evidence.py`·`src/schemas/enums.py`에 있다 — 이 모듈은 그것을 그대로 쓴다.

## 명세 밖이거나 해석이 든 자리

- 동점 처리(`(file, line)` 오름차순) — `pick_best_candidate`. 결정론을 위해 정했다.
- `closest_name`의 cutoff, `_LIBC_SYMBOLS`의 목록 — 명세가 값을 안 준다.
- 선언 줄에서 타입 이름을 뽑는 휴리스틱(체인 라벨 7) — `_type_name_from_line`.
- 대입 주의문(라벨 3·13이 있을 때만)의 정확한 삽입 자리 — 골격 원문(스냅샷 :370-381)은
  위치를 산문으로만 설명하고, hop 1 예(라벨 4뿐)에는 주의문이 없어 위치를 못 보여준다.
  RETRIEVED 조각들과 UNFILLED 사이로 뒀다 — **추측**이다.
- **"같은 함수"를 아는 방법** — `ResolveQuery`에 현재 함수 필드가 없다(계약 고정). 지역
  변수·파라미터 검색은 `target_file` 안에서 이름이 겹치면 `visible`에 보이는 선언을
  우선한다(`_resolve_declaration`) — 파라미터의 caller site 예(스냅샷 §4.4.2 4-3)가 같은
  방식으로 "슬라이스에 쓰인 F"를 고른다.
- UNFILLED 사유 사이의 우선순위(원문이 순서를 안 준다) — libc를 가장 먼저 본다(그러지
  않으면 `strdup`이 `calls` 표에도 잡혀 outside_repo로 잘못 읽힌다 — hop 1 예가 그 근거),
  다음 outside_repo(호출은 있음), 다음 closest_name(비슷한 철자), 마지막 not_found.
- **Already in SOURCE 줄이 실리는 자리** — 골격(§5.6)에 Already 전용 자리가 없다. 둘 다
  "요청한 것을 그대로 안 준다"는 통지라 UNFILLED와 같은 자리에 already를 먼저, Not
  resolved를 뒤에 넣는다(`resolve()`) — **추측**이다.
"""

import difflib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from src.schemas.enums import UnfilledReason
from src.schemas.need_evidence import Resolution, ResolveQuery, RetrievedBlock, Unfilled
from src.schemas.scout_response import RequestedContext

# --- UNFILLED 문구 -----------------------------------------------------------
#
# 사유 문구는 마침표 없이 둔다 — 공통 틀 `Not resolved: <kind>:<target> (<문구>).`이 마침표를
# 닫는다(명세 원문은 `libc`·`not_found`처럼 문구 끝에 마침표가 있는 사유도 있어, 여기서 뗀다).
# `closest_name`·`not_identifier`만 `detail`을 쓴다.
_REASON_PHRASES: Mapping[UnfilledReason, str] = {
    UnfilledReason.OUTSIDE_REPO: "called in this repository but defined outside it",
    UnfilledReason.NOT_FOUND: "no definition found in the repository",
    UnfilledReason.CLOSEST_NAME: "closest name in repository: {detail}",
    UnfilledReason.LIBC: "C standard library symbol",
    UnfilledReason.PLACEHOLDER: (
        "the target was a placeholder, so there was nothing to look up; name the symbol you need"
    ),
    UnfilledReason.NO_TARGET: "no target was given",
    UnfilledReason.NO_IDENTIFIER: "the target held no identifier",
    UnfilledReason.NOT_IDENTIFIER: "could not read a symbol name out of it (got '{detail}')",
}


def render_unfilled(*, kind: str, target: str, reason: UnfilledReason, detail: str = "") -> str:
    """UNFILLED 한 줄 — `Not resolved: <kind>:<target> (<사유>).`(§5.6).

    `target`은 호출부가 쥔 **원문 그대로**(모델이 보낸 raw target)를 넣는다 — 정규화된 값이
    아니다. 명세의 거부 예 둘(`unknown`·`stl:error`)이 원문을 그대로 보여준다.
    """
    phrase = _REASON_PHRASES[reason].format(detail=detail)
    return f"Not resolved: {kind}:{target} ({phrase})."


# --- target 정규화 ----------------------------------------------------------
#
# §5.2 8단계 + 0단계(`@file:L…` 접미 제거 — 아래 주석). 성공은 (base, chain), 실패는
# (사유 키, 상세값) — 둘 다 2-튜플이라 `is_rejected()`로 가른다(두 번째 원소가 str이면 실패).

_PLACEHOLDER_VALUES = frozenset({"unknown", "n/a", "none", "null", "-", "tbd"})
# 0단계 — 프롬프트(`prompts/scout_memory/v3/prefix.md:127`)가 허용하는 접미
# (`@file:Lstart[-Lend]`)이지만 명세 8단계 표에는 없다. 정규화 맨 앞에서 떼고 점수엔 안 쓴다
# — 안 떼면 이 접미를 붙인 target이 전부 식별자 검증(8단계)에서 거부된다.
_LOCATION_SUFFIX_RE = re.compile(r"@\S*:L\d+(?:-\d+)?$")
# ponytail: 명세가 "등"이라 적어 목록을 안 준다 — 흔한 C 집계 타입 키워드로 한정했다.
_AGGREGATE_TYPE_PREFIXES = ("struct", "union", "enum")
_C_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")


def normalize_target(raw: str) -> tuple[str, tuple[str, ...]] | tuple[UnfilledReason, str]:
    """§5.2 정규화. 성공: `(base, chain)`. 실패: `(사유, 상세값)`.

    상세값은 `not_identifier`에서만 쓴다(정규화가 남긴, 식별자가 아닌 값 — 「got '<값>'」에
    그대로 들어간다). 나머지 세 실패 사유는 고정 문구라 상세값이 빈 문자열이다.
    """
    if not raw:
        return (UnfilledReason.NO_TARGET, "")
    value = _LOCATION_SUFFIX_RE.sub("", raw)  # 0단계
    if value.strip().lower() in _PLACEHOLDER_VALUES:  # 1단계
        return (UnfilledReason.PLACEHOLDER, "")
    value = re.sub(r"\([^)]*\)", "", value)  # 2단계: 괄호 제거
    value = re.sub(r"\[[^\]]*\]", "", value)  # 3단계: 배열 첨자 제거
    parts = value.split(None, 1)
    if len(parts) == 2 and parts[0] in _AGGREGATE_TYPE_PREFIXES:  # 4단계
        value = parts[1]
    base, *chain = re.split(r"->|::|\.", value)  # 5단계
    base = base.lstrip("*").strip()  # 6단계
    if base:
        base = base.split(None, 1)[0]  # 7단계: 공백 뒤 서술 제거
    if not base:
        return (UnfilledReason.NO_IDENTIFIER, "")
    if not _C_IDENTIFIER_RE.match(base):  # 8단계
        return (UnfilledReason.NOT_IDENTIFIER, base)
    return (base, tuple(c.strip() for c in chain))


def is_rejected(result: tuple[str, tuple[str, ...]] | tuple[UnfilledReason, str]) -> bool:
    """`normalize_target`의 결과가 거부인가 — 두 번째 원소가 `str`이면 거부(사유)다."""
    return isinstance(result[1], str)


# --- 조회 실패 판정 보조(B 단계가 부른다) -----------------------------------

# ponytail: 명세에 cutoff 값이 없다 — `difflib.get_close_matches` 기본값을 그대로 정한 값으로 쓴다.
_CLOSEST_NAME_CUTOFF = 0.6


def closest_name(target: str, candidates: Iterable[str]) -> str | None:
    """비슷한 철자의 심볼 이름 하나(§5.6 UNFILLED `closest_name`). 없으면 `None`."""
    matches = difflib.get_close_matches(target, list(candidates), n=1, cutoff=_CLOSEST_NAME_CUTOFF)
    return matches[0] if matches else None


# ISO C 표준 라이브러리 + 흔한 POSIX 함수. 명세는 목록을 안 준다(§5.6). ponytail: 전체
# libc를 담지 않는다 — 새 오탐·누락이 나오면 추가하되 완전한 목록으로 키우지 않는다.
_LIBC_SYMBOLS = frozenset(
    {
        "malloc", "calloc", "realloc", "free", "exit", "abort", "atexit",
        "atoi", "atol", "atoll", "atof", "strtol", "strtoul", "strtod",
        "rand", "srand", "qsort", "bsearch", "system", "getenv", "abs",
        "printf", "fprintf", "sprintf", "snprintf", "vprintf", "vfprintf", "vsnprintf",
        "scanf", "fscanf", "sscanf", "fopen", "fdopen", "freopen", "fclose",
        "fread", "fwrite", "fgets", "fputs", "fgetc", "fputc", "getchar", "putchar",
        "gets", "puts", "perror", "feof", "ferror", "fflush", "fseek", "ftell", "rewind",
        "strlen", "strcpy", "strncpy", "strcat", "strncat", "strcmp", "strncmp",
        "strchr", "strrchr", "strstr", "strtok", "strdup", "strndup",
        "memcpy", "memmove", "memset", "memcmp", "memchr",
        "isalpha", "isdigit", "isspace", "isalnum", "isupper", "islower",
        "toupper", "tolower",
        "sqrt", "pow", "floor", "ceil", "fabs", "sin", "cos", "tan", "log", "exp",
        "getline", "asprintf", "vasprintf",
        "read", "write", "open", "close", "fork", "execve", "execvp", "wait", "waitpid",
        "pipe", "dup", "dup2", "kill", "signal", "sleep", "usleep",
        "gettimeofday", "time", "localtime", "gmtime", "strftime",
    }
)  # fmt: skip


def is_libc_symbol(name: str) -> bool:
    """§5.6 UNFILLED `libc` 판정 — `_LIBC_SYMBOLS` 안에 있는가."""
    return name in _LIBC_SYMBOLS


# --- RETRIEVED 조각 렌더 -----------------------------------------------------
#
# 13 라벨(§5.6, 원문 그대로). 3·7·13은 템플릿이다 — 호출부(B 단계)가 `.format()`한다.

LABEL_FUNCTION_BODY = "function body"
LABEL_DECLARATION_FILE_SCOPE = "declaration (file scope)"
LABEL_ASSIGNMENT_TO_FIELD = "assignment to .{field}"
LABEL_ASSIGNMENT_SAME_FUNCTION = "assignment in the same function"
LABEL_CALLEE_BODY = "callee body"
LABEL_MACRO = "macro"
LABEL_TYPE_OF = "type of {requested}"
LABEL_TYPE_DEFINITION = "type definition"
LABEL_CALLER_SITE = "caller site"
LABEL_DECLARATION_LOCAL = "declaration (local)"
LABEL_MACRO_NAMED_IN_DECLARATION = "macro named in the declaration"
LABEL_STRUCT_DECLARES_FIELD = "struct that declares this field"
LABEL_ASSIGNMENT_TO_GLOBAL = "assignment to {global_name}"

# 대입 주의문은 라벨 3·13에만 건다(아래 `needs_assignment_note`) — 둘 다 이 접두로 시작한다
# (필드는 뒤에 `.`이 더 붙을 뿐이다). 라벨 4 `assignment in the same function`은 "assignment
# in"이라 안 걸린다 — 원문(스냅샷 :378)이 주의문을 붙이는 "대입 블록"을 "target에 들어갈 수
# 있는 경우의 수 집합"이라 적고, 라벨 13 설명도 "가능한 값의 집합"이라고 적는데 그 집합에
# 해당하는 것은 라벨 3·13뿐이다(같은 함수 안 대입 1줄인 라벨 4는 집합이 아니다).
_ASSIGNMENT_NOTE_LABEL_PREFIX = "assignment to "


def render_fragment(*, name: str, label: str, file: str, lines: Sequence[tuple[int, str]]) -> str:
    """조각 하나 — §5.6 원문 틀. `lines`는 (원본 줄번호, 원본 줄 텍스트) 순서 그대로.

    줄번호는 4칸 오른쪽 맞춤. 원본 줄이 빈 문자열이면 `| ` 뒤 공백을 안 붙인다(명세 예
    실측 — 스냅샷의 빈 줄 자리가 그렇게 비어 있다).
    """
    header = f"/* --- retrieved: {name} ({label}) --- */"
    banner = f"/* ===== {file} ===== */"
    body = [f"{line_no:>4}| {text}" if text else f"{line_no:>4}|" for line_no, text in lines]
    return "\n".join((header, banner, *body))


def render_already_in_source(
    *, kind: str, name: str, file: str, line_start: int, line_end: int
) -> str:
    """`Already in SOURCE: <kind>:<이름> at <파일>:L<시작>-<끝>.`(§5.5)."""
    return f"Already in SOURCE: {kind}:{name} at {file}:L{line_start}-{line_end}."


# §5.5 — 95% 이상 겹치면 중복. 정수 교차곱으로 봐 부동소수 경계(94.999...%) 오판을 피한다.
_DUPLICATE_THRESHOLD_NUMERATOR = 95
_DUPLICATE_THRESHOLD_DENOMINATOR = 100


def is_duplicate_in_source(block_lines: frozenset[int], visible_lines: frozenset[int]) -> bool:
    """블록 줄 집합이 SOURCE에 보이는 줄과 95% 이상 겹치면 참(§5.5). 빈 블록은 거짓이다."""
    if not block_lines:
        return False
    overlap = len(block_lines & visible_lines)
    return (
        overlap * _DUPLICATE_THRESHOLD_DENOMINATOR
        >= len(block_lines) * _DUPLICATE_THRESHOLD_NUMERATOR
    )


# --- 후속 턴 전체 렌더 --------------------------------------------------------

_ASSIGNMENT_NOTE = (
    "Note: assignment blocks show every place this value can be set in the repository.\n"
    "They are not in execution order, and the host does not know which one reaches the\n"
    "code under review."
)


def needs_assignment_note(labels: Iterable[str]) -> bool:
    """라벨 3·13 블록이 하나라도 있으면 대입 주의문을 넣는다(§5.6, 원문 근거는 위 모듈 상수 주석).

    라벨 4(`assignment in the same function`)만으로는 넣지 않는다 — 원문(스냅샷 :378)의
    "대입 블록(target에 들어갈 수 있는 경우의 수 집합)"과 라벨 13 설명의 "가능한 값의
    집합"에 해당하는 것은 라벨 3·13뿐이다. 아직 필드/전역변수 이름이 안 채워진 템플릿
    (`LABEL_ASSIGNMENT_TO_FIELD` 등)과 이미 채워진 라벨 둘 다 이 접두 검사로 맞는다.
    """
    return any(label.startswith(_ASSIGNMENT_NOTE_LABEL_PREFIX) for label in labels)


def render_followup(
    *,
    hop: int,
    fragments: Sequence[str],
    unfilled_lines: Sequence[str],
    include_assignment_note: bool,
) -> str:
    """§5.6 골격 그대로 조립한 후속 턴 전체 텍스트.

    빈 줄 배치는 명세 hop 1 출력 예(스냅샷 :424-445)에서 역산했다 — 조각과 UNFILLED 줄은
    각각 뒤에 빈 줄 하나를 달고, 그것들을 이어 붙인 사이에 골격이 빈 줄을 더 넣지 않는다.
    대입 주의문의 삽입 자리(조각들과 UNFILLED 사이)는 모듈 docstring에 적었듯 추측이다.
    끝에는 줄바꿈을 안 붙인다(이 저장소의 다른 렌더 함수들과 같은 관례 — `slices.render`).
    """
    retrieved = "".join(f"{fragment}\n\n" for fragment in fragments)
    note = f"{_ASSIGNMENT_NOTE}\n\n" if include_assignment_note else ""
    unfilled = "".join(f"{line}\n\n" for line in unfilled_lines)
    return (
        f"## Retrieved context (hop {hop})\n"
        "\n"
        "The host resolved the requests from your previous turn against the original "
        "repository. Each block\n"
        "below is verbatim source and carries its own file and line numbers.\n"
        "\n"
        f"{retrieved}"
        f"{note}"
        f"{unfilled}"
        "Judge the same candidate again with `SOURCE` and the `RETRIEVED` blocks read "
        "together, and output\n"
        "one complete ScoutResponse JSON. Your signal may stay what it was or change; "
        "decide from the code\n"
        "in front of you."
    )


# --- 동명 점수 ---------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """동명 점수 매김 후보 하나 — 이미 표에서 조회된 행. B 단계(`resolve()`)가 조립한다."""

    definition_kind: str
    file: str
    line: int


def score_candidate(
    candidate: Candidate, *, requested_kind: str, target_file: str, visible_files: frozenset[str]
) -> int:
    """§5.3 점수 — +3 kind 일치 · +3 target_file 일치 · +2 SOURCE에 보임."""
    score = 0
    if candidate.definition_kind == requested_kind:
        score += 3
    if candidate.file == target_file:
        score += 3
    if candidate.file in visible_files:
        score += 2
    return score


def pick_best_candidate(
    candidates: Sequence[Candidate],
    *,
    requested_kind: str,
    target_file: str,
    visible_files: frozenset[str],
) -> Candidate | None:
    """§5.3 동명 점수로 후보 하나를 고른다. 후보가 없으면 `None`.

    동점 처리(`(file, line)` 오름차순)는 명세 밖이다 — ponytail: 결정론을 위해 정했다.
    """
    if not candidates:
        return None

    def key(c: Candidate) -> tuple[int, str, int]:
        return (
            -score_candidate(
                c,
                requested_kind=requested_kind,
                target_file=target_file,
                visible_files=visible_files,
            ),
            c.file,
            c.line,
        )

    return min(candidates, key=key)


# --- resolve(): 조회·조립 ----------------------------------------------------
#
# 여기부터는 `table`(조회)·`read_lines`(파일 읽기) 훅만 쓴다 — 인덱스·파일에 직접 손대지
# 않는다. 두 훅 다 factory가 만든다(`GraphIndex.table`·대상 트리 읽기).

# 반복되는 훅 시그니처의 줄임 별칭 — 함수 서명이 100칸을 넘기지 않게 한다.
_Table = Callable[[str], Mapping[str, object] | None]
_ReadLines = Callable[[str], Sequence[str]]
_Visible = Mapping[str, frozenset[int]]

# ponytail: v3 kind 어휘의 "variable" — 선언(지역·전역) 동명 점수의 `definition_kind`다.
_VARIABLE_KIND = "variable"

# caller site 반경 — 원문이 갈린다: 라벨 표(스냅샷 :395)는 ±3, 본문 4-3 문단(:308)은
# "앞뒤 5줄"이라 적는다. 라벨 표가 이 블록 자체의 정의라 ±3을 쓴다.
_CALLER_SITE_RADIUS = 3
# 라벨 4·13 — 대입 줄 반경. 라벨 4(같은 함수)는 그 줄 하나, 라벨 13(전역)은 ±1(명세 표).
_GLOBAL_ASSIGN_RADIUS = 1

# 요청 개수 상한(리뷰 5.1 [HIGH]) — v3 prefix 세 좌석이 모델에게 허락하는 상한과 같다:
# `prompts/scout_memory/v3/prefix.md:146`·`scout_injection/v3/prefix.md:146`이 똑같이
# "at most 3 actionable requests", `scout_boundary/v3/prefix.md:135`은 "always present,
# at most 3 entries"로 문구는 갈려도 숫자는 셋 다 3이다(2026-09-26 세 파일 확인). 없으면
# 모델이 규칙을 어기거나 파싱이 더 실었을 때 `resolve()`가 그대로 다 돈다 — 리뷰어가
# 존재하지 않는 target 500개로 재현했다(1.1초, `max_chars=15000`인데 `followup_text`
# 44,800자, 전부 UNFILLED 줄). 스키마에 `max_length`를 걸지 않는 이유는 비객체 가드를
# 기각한 것과 같다 — 걸면 응답 전체가 파싱 실패가 된다.
_MAX_REQUESTS = 3

_TYPE_TOKEN_RE = re.compile(r"[A-Za-z_]\w*")
_MACRO_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]{2,}")


def _record_range(record: Mapping[str, object]) -> tuple[int, int]:
    start = int(record["line_start"])
    end = record.get("line_end")
    return start, int(end) if end is not None else start


def _clip(lo: int, hi: int, total: int) -> tuple[int, int]:
    """파일 밖으로 안 나가게 자른다(1-index)."""
    return max(lo, 1), min(hi, total)


def _read_span(
    read_lines: _ReadLines, file: str, lo: int, hi: int
) -> tuple[tuple[int, str], ...] | None:
    """[lo, hi] 구간을 (줄번호, 원본 줄)로. 못 읽으면 `None` — 호출부가 그 블록만 건너뛴다.

    대상 파일이 사라졌거나 대상 루트 밖을 가리키면 `read_lines`가 `OSError`/`ValueError`를
    올린다(`safe_join`의 `PathEscape`가 `ValueError`다) — 여기서 삼켜 그 블록만 빠진다.
    """
    try:
        lines = read_lines(file)
    except (OSError, ValueError):
        return None
    lo, hi = _clip(lo, hi, len(lines))
    if lo > hi:
        return None
    return tuple((n, lines[n - 1]) for n in range(lo, hi + 1))


@dataclass(frozen=True)
class _Block:
    """RETRIEVED 조각 하나 — `RetrievedBlock`(줄번호만)으로 줄이기 전의 중간 표현.

    `request_kind`는 이 블록을 만든 요청의 모델 kind다 — 95% 중복이면 `Already in SOURCE`
    줄의 `<kind>` 자리에 쓴다(그 줄도 UNFILLED처럼 요청의 kind를 그대로 쓴다).
    """

    name: str
    label: str
    file: str
    text_lines: tuple[tuple[int, str], ...]
    request_kind: str = ""

    @property
    def lines(self) -> tuple[int, ...]:
        return tuple(n for n, _ in self.text_lines)

    def render(self) -> str:
        return render_fragment(
            name=self.name, label=self.label, file=self.file, lines=self.text_lines
        )


def _record_block(
    name: str, label: str, record: Mapping[str, object], read_lines: _ReadLines
) -> list[_Block]:
    lo, hi = _record_range(record)
    span = _read_span(read_lines, str(record["file"]), lo, hi)
    if span is None:
        return []
    return [_Block(name=name, label=label, file=str(record["file"]), text_lines=span)]


def _type_name_from_line(line_text: str, types_table: Mapping[str, object]) -> str | None:
    """선언 줄에서 `types` 표에 있는 식별자를 찾는다 — 체인 라벨 7의 휴리스틱(명세 밖).

    선언 줄의 식별자를 왼쪽부터 보고 `types`에 등록된 첫 토큰을 쓴다 — 변수 이름 자신은
    거의 항상 `types`에 없어 자연히 걸러진다. 한계: 같은 줄에 타입 이름이 둘 이상 있으면
    (드묾) 왼쪽 것을 고른다 — ponytail: 결정론을 위한 선택이고 명세에 없다.
    """
    for token in _TYPE_TOKEN_RE.findall(line_text):
        if token in types_table:
            return token
    return None


def _pick_record(
    records: Sequence[Mapping[str, object]],
    *,
    definition_kind: str,
    requested_kind: str,
    target_file: str,
    visible_files: frozenset[str],
) -> Mapping[str, object] | None:
    """§5.3 점수로 같은 형식 안 여러 정의 중 하나를 고른다(`score_candidate` 재사용).

    비었으면 `None`.
    """
    if not records:
        return None

    def key(record: Mapping[str, object]) -> tuple[int, str, int]:
        candidate = Candidate(
            definition_kind=definition_kind,
            file=str(record["file"]),
            line=int(record["line_start"]),
        )
        score = score_candidate(
            candidate,
            requested_kind=requested_kind,
            target_file=target_file,
            visible_files=visible_files,
        )
        return (-score, candidate.file, candidate.line)

    return min(records, key=key)


def _declaration_site(
    base: str,
    *,
    table: _Table,
    target_file: str,
    visible: _Visible,
) -> tuple[str, str, int] | None:
    """base가 지역변수·파라미터로 선언된 (함수, 파일, 줄) — `target_file` 안에서만 찾는다.

    `ResolveQuery`에 "현재 함수" 필드가 없다(계약 고정) — `target_file` 안에 같은 이름이
    여럿이면 `visible`에 보이는 선언을 우선한다(스냅샷 §4.4.2 4-3의 caller-site 예가
    "슬라이스에 쓰인 F"로 고르는 것과 같은 생각이다). 그래도 여럿이면 함수 이름·줄 순 —
    ponytail: 결정론을 위한 선택이고 명세에 없다.
    """
    file_locals = (table("locals") or {}).get(target_file, {})
    matches = [
        (func, min(names[base]))
        for func, names in file_locals.items()
        if base in names and names[base]
    ]
    if not matches:
        return None
    visible_lines = visible.get(target_file, frozenset())
    func, line = min(matches, key=lambda item: (item[1] not in visible_lines, item[0], item[1]))
    return (func, target_file, line)


def _is_parameter(
    func: str, base: str, *, table: _Table, file: str
) -> bool:
    records = (table("functions") or {}).get(func, ())
    return any(
        record.get("file") == file and base in record.get("params", ()) for record in records
    )


def _resolve_caller_sites(
    func: str, *, table: _Table, read_lines: _ReadLines
) -> list[_Block]:
    """라벨 9 — base를 파라미터로 가진 함수 F의 호출부 ±3(라벨 표 값)."""
    blocks: list[_Block] = []
    for site in (table("calls") or {}).get(func, ()):
        line = int(site["line"])
        span = _read_span(
            read_lines, str(site["file"]), line - _CALLER_SITE_RADIUS, line + _CALLER_SITE_RADIUS
        )
        if span is not None:
            blocks.append(
                _Block(name=func, label=LABEL_CALLER_SITE, file=str(site["file"]), text_lines=span)
            )
    return blocks


def _resolve_macro_named_in_declaration(
    line_text: str,
    *,
    table: _Table,
    read_lines: _ReadLines,
    target_file: str,
    visible_files: frozenset[str],
) -> list[_Block]:
    """라벨 11 — 선언 줄의 대문자 식별자를 `macros`에서 찾는다.

    2단계 — target이 함수·구조체가 아닐 때만 호출부가 이 함수를 부른다. 동명 매크로가
    여럿이면 `_pick_record`가 §5.3 점수로 고른다(리뷰 5.2 [HIGH]) — 호출부(지역·전역
    선언 분기)가 그 선언이 실제로 있는 `target_file`·`visible_files`를 넘긴다. 이 값을
    하드코딩(빈 문자열·빈 집합)하면 동점 처리인 `(file, line)` 사전순으로만 떨어져 모듈의
    다른 동명 조회 자리(`_resolve_flat`·전역 분기·`_resolve_chain`·`_resolve_field_fallback`)
    와 다른 결과가 난다.
    """
    macros_table = table("macros") or {}
    blocks: list[_Block] = []
    for token in _MACRO_TOKEN_RE.findall(line_text):
        record = _pick_record(
            macros_table.get(token, ()),
            definition_kind="macro",
            requested_kind="macro",
            target_file=target_file,
            visible_files=visible_files,
        )
        if record is not None:
            blocks.extend(
                _record_block(token, LABEL_MACRO_NAMED_IN_DECLARATION, record, read_lines)
            )
    return blocks


def _resolve_same_function_assignment(
    base: str,
    func: str,
    file: str,
    *,
    table: _Table,
    read_lines: _ReadLines,
) -> list[_Block]:
    """라벨 4 — 같은 함수 안 대입 줄(1줄, 반경 0)."""
    lines = (table("assigns") or {}).get(file, {}).get(func, {}).get(base, ())
    blocks: list[_Block] = []
    for line in lines:
        span = _read_span(read_lines, file, int(line), int(line))
        if span is not None:
            blocks.append(
                _Block(name=base, label=LABEL_ASSIGNMENT_SAME_FUNCTION, file=file, text_lines=span)
            )
    return blocks


def _resolve_global_assignment(
    base: str, *, table: _Table, read_lines: _ReadLines
) -> list[_Block]:
    """라벨 13 — 파일 전역 변수 대입을 저장소 전체에서 찾는다(±1, 명세 표)."""
    label = LABEL_ASSIGNMENT_TO_GLOBAL.format(global_name=base)
    blocks: list[_Block] = []
    for file, funcs in (table("assigns") or {}).items():
        for names in funcs.values():
            for line in names.get(base, ()):
                span = _read_span(
                    read_lines,
                    file,
                    int(line) - _GLOBAL_ASSIGN_RADIUS,
                    int(line) + _GLOBAL_ASSIGN_RADIUS,
                )
                if span is not None:
                    blocks.append(_Block(name=base, label=label, file=file, text_lines=span))
    return blocks


def _resolve_field_assignment(
    field: str, *, table: _Table, read_lines: _ReadLines
) -> list[_Block]:
    """라벨 3 — 체인 마지막 필드의 대입을 저장소 전체에서 찾는다(±1, 명세 표)."""
    label = LABEL_ASSIGNMENT_TO_FIELD.format(field=field)
    blocks: list[_Block] = []
    for record in (table("field_assigns") or {}).get(field, ()):
        line = int(record["line"])
        span = _read_span(
            read_lines,
            str(record["file"]),
            line - _GLOBAL_ASSIGN_RADIUS,
            line + _GLOBAL_ASSIGN_RADIUS,
        )
        if span is not None:
            blocks.append(
                _Block(name=field, label=label, file=str(record["file"]), text_lines=span)
            )
    return blocks


def _resolve_callee_body(
    base: str,
    *,
    table: _Table,
    read_lines: _ReadLines,
    visible: _Visible,
) -> list[_Block]:
    """라벨 5 — 슬라이스 안에서 base를 인자로 넘기는 호출이 보이면 그 callee 전체(#63 baseline
    §4.4.2 5번 — `doit(text4)`가 슬라이스에 보이면 `text4`가 아니라 `doit`의 본문을 준다)."""
    functions_table = table("functions") or {}
    blocks: list[_Block] = []
    seen: set[str] = set()
    for callee, sites in (table("calls") or {}).items():
        if callee in seen:
            continue
        for site in sites:
            file = str(site["file"])
            line = int(site["line"])
            if line not in visible.get(file, frozenset()) or base not in site.get("args", ()):
                continue
            record = _pick_record(
                functions_table.get(callee, ()),
                definition_kind="function",
                requested_kind="function",
                target_file=file,
                visible_files=frozenset(visible),
            )
            if record is not None:
                blocks.extend(_record_block(callee, LABEL_CALLEE_BODY, record, read_lines))
                seen.add(callee)
            break
    return blocks


def _resolve_declaration(
    base: str,
    request: RequestedContext,
    *,
    table: _Table,
    read_lines: _ReadLines,
    target_file: str,
    visible: _Visible,
) -> list[_Block]:
    """선언(라벨 2·10) + 딸린 블록 — 파라미터면 caller site(라벨 9), 대문자 식별자면 매크로
    (라벨 11), 지역이면 같은 함수 대입(라벨 4)·전역이면 저장소 전체 대입(라벨 13)."""
    blocks: list[_Block] = []
    visible_files = frozenset(visible)
    site = _declaration_site(base, table=table, target_file=target_file, visible=visible)
    if site is not None:
        func, file, line = site
        span = _read_span(read_lines, file, line, line)
        if span is not None:
            decl = _Block(name=base, label=LABEL_DECLARATION_LOCAL, file=file, text_lines=span)
            blocks.append(decl)
            blocks.extend(
                _resolve_macro_named_in_declaration(
                    span[0][1],
                    table=table,
                    read_lines=read_lines,
                    target_file=file,
                    visible_files=visible_files,
                )
            )
            blocks.extend(
                _resolve_same_function_assignment(
                    base, func, file, table=table, read_lines=read_lines
                )
            )
        if _is_parameter(func, base, table=table, file=file):
            blocks.extend(_resolve_caller_sites(func, table=table, read_lines=read_lines))
        return blocks

    global_record = _pick_record(
        (table("file_vars") or {}).get(base, ()),
        definition_kind=_VARIABLE_KIND,
        requested_kind=request.kind,
        target_file=target_file,
        visible_files=visible_files,
    )
    if global_record is not None:
        blocks.extend(
            _record_block(base, LABEL_DECLARATION_FILE_SCOPE, global_record, read_lines)
        )
        line = int(global_record["line_start"])
        global_file = str(global_record["file"])
        span = _read_span(read_lines, global_file, line, line)
        if span is not None:
            blocks.extend(
                _resolve_macro_named_in_declaration(
                    span[0][1],
                    table=table,
                    read_lines=read_lines,
                    target_file=global_file,
                    visible_files=visible_files,
                )
            )
        blocks.extend(_resolve_global_assignment(base, table=table, read_lines=read_lines))
    return blocks


def _declared_type_name(
    base: str,
    *,
    table: _Table,
    read_lines: _ReadLines,
    target_file: str,
    visible: _Visible,
) -> str | None:
    """base 선언 줄에서 타입 이름을 찾는다 — 지역변수·파라미터 먼저, 없으면 파일 전역."""
    site = _declaration_site(base, table=table, target_file=target_file, visible=visible)
    if site is not None:
        _func, file, line = site
        span = _read_span(read_lines, file, line, line)
    else:
        record = _pick_record(
            (table("file_vars") or {}).get(base, ()),
            definition_kind=_VARIABLE_KIND,
            requested_kind=_VARIABLE_KIND,
            target_file=target_file,
            visible_files=frozenset(visible),
        )
        span = None
        if record is not None:
            line = int(record["line_start"])
            span = _read_span(read_lines, str(record["file"]), line, line)
    if not span:
        return None
    return _type_name_from_line(span[0][1], table("types") or {})


def _field_own_type(
    field: str,
    struct_record: Mapping[str, object],
    *,
    table: _Table,
    read_lines: _ReadLines,
) -> str | None:
    """체인을 이어가려고 필드 자신의 타입을 찾는다 — struct 본문에서 그 필드 줄을 찾아 같은
    휴리스틱을 다시 쓴다(`fields` 표는 필드 줄이 아니라 **선언한 struct의 범위**만 준다)."""
    lo, hi = _record_range(struct_record)
    span = _read_span(read_lines, str(struct_record["file"]), lo, hi)
    if span is None:
        return None
    pattern = re.compile(rf"\b{re.escape(field)}\b")
    for _line_no, text in span:
        if pattern.search(text):
            return _type_name_from_line(text, table("types") or {})
    return None


def _resolve_chain(
    base: str,
    chain: Sequence[str],
    request: RequestedContext,
    *,
    table: _Table,
    read_lines: _ReadLines,
    target_file: str,
    visible: _Visible,
) -> list[_Block]:
    """체인(§4.4.2 3-2) — base의 타입을 따라 왼쪽부터, 지나는 타입 정의 전부(라벨 7) +
    마지막 필드의 대입(라벨 3). base 자체를 못 찾으면(선언도 전역도 없음) 빈 목록이다 —
    그 경우는 조회 실패 판정(`_unfilled_lookup_reason`)으로 넘어간다."""
    type_name = _declared_type_name(
        base, table=table, read_lines=read_lines, target_file=target_file, visible=visible
    )
    if type_name is None:
        return []
    blocks: list[_Block] = []
    types_table = table("types") or {}
    visible_files = frozenset(visible)
    for i, field in enumerate(chain):
        record = _pick_record(
            types_table.get(type_name, ()),
            definition_kind="type",
            requested_kind=request.kind,
            target_file=target_file,
            visible_files=visible_files,
        )
        if record is None:
            break
        blocks.extend(
            _record_block(
                type_name, LABEL_TYPE_OF.format(requested=request.target), record, read_lines
            )
        )
        if i == len(chain) - 1:
            blocks.extend(_resolve_field_assignment(field, table=table, read_lines=read_lines))
            break
        type_name_next = _field_own_type(field, record, table=table, read_lines=read_lines)
        if type_name_next is None:
            break
        type_name = type_name_next
    return blocks


def _resolve_field_fallback(
    base: str,
    request: RequestedContext,
    *,
    table: _Table,
    read_lines: _ReadLines,
    target_file: str,
    visible: _Visible,
) -> list[_Block]:
    """라벨 12 — base가 어디서도 안 나왔는데 필드 이름이면(예: `stl->facet_start` 대신 그냥
    `facet_start`) 그 필드를 선언한 struct의 정의를 base로 준다(스냅샷 §4.4.2 3-2 후반)."""
    record = _pick_record(
        (table("fields") or {}).get(base, ()),
        definition_kind="field",
        requested_kind=request.kind,
        target_file=target_file,
        visible_files=frozenset(visible),
    )
    if record is None:
        return []
    return _record_block(str(record["type"]), LABEL_STRUCT_DECLARES_FIELD, record, read_lines)


def _resolve_flat(
    base: str,
    request: RequestedContext,
    *,
    table: _Table,
    read_lines: _ReadLines,
    target_file: str,
    visible: _Visible,
) -> list[_Block]:
    """체인이 없는 target — §4.4.1 우선순위(function > macro > type > local > global >
    field > unknown)로 **맞는 형식을 전부** 쓴다. field(라벨 12)만 예외로, 다른 형식이
    하나도 안 맞았을 때만 본다."""
    visible_files = frozenset(visible)
    blocks: list[_Block] = []

    func_record = _pick_record(
        (table("functions") or {}).get(base, ()),
        definition_kind="function",
        requested_kind=request.kind,
        target_file=target_file,
        visible_files=visible_files,
    )
    if func_record is not None:
        blocks.extend(_record_block(base, LABEL_FUNCTION_BODY, func_record, read_lines))

    macro_record = _pick_record(
        (table("macros") or {}).get(base, ()),
        definition_kind="macro",
        requested_kind=request.kind,
        target_file=target_file,
        visible_files=visible_files,
    )
    if macro_record is not None:
        blocks.extend(_record_block(base, LABEL_MACRO, macro_record, read_lines))

    type_record = _pick_record(
        (table("types") or {}).get(base, ()),
        definition_kind="type",
        requested_kind=request.kind,
        target_file=target_file,
        visible_files=visible_files,
    )
    if type_record is not None:
        blocks.extend(_record_block(base, LABEL_TYPE_DEFINITION, type_record, read_lines))

    blocks.extend(
        _resolve_declaration(
            base,
            request,
            table=table,
            read_lines=read_lines,
            target_file=target_file,
            visible=visible,
        )
    )
    blocks.extend(_resolve_callee_body(base, table=table, read_lines=read_lines, visible=visible))

    if not blocks:
        blocks.extend(
            _resolve_field_fallback(
                base,
                request,
                table=table,
                read_lines=read_lines,
                target_file=target_file,
                visible=visible,
            )
        )

    return blocks


def _all_symbol_names(table: _Table) -> frozenset[str]:
    """`closest_name`이 비교할 이름 전부 — 표 다섯의 키를 합친다."""
    names: set[str] = set()
    for name in ("functions", "macros", "types", "file_vars", "fields"):
        value = table(name)
        if value:
            names.update(value.keys())
    return frozenset(names)


def _unfilled_lookup_reason(base: str, *, table: _Table) -> tuple[UnfilledReason, str]:
    """조회에 실패했을 때 사유 넷 중 하나 — 원문이 순서를 안 줘서 이 저장소가 정한다
    (모듈 docstring). libc를 가장 먼저 본다: `strdup`도 `calls` 표에 잡히므로, outside_repo를
    먼저 보면 hop 1 예의 `strdup`이 거기로 잘못 읽힌다.

    `closest_name`의 후보에서 `base` 자신은 뺀다(리뷰 5.3 [MEDIUM]) — 인덱스에는 있는데
    파일을 못 읽어 여기까지 왔을 때(`_all_symbol_names`는 표의 키를 그대로 모아 렌더
    성공 여부를 안 본다), 빼지 않으면 "가장 비슷한 이름"이 매번 자기 자신이 되어
    `closest_name` 사유가 무의미해진다.
    """
    if is_libc_symbol(base):
        return UnfilledReason.LIBC, ""
    if base in (table("calls") or {}):
        return UnfilledReason.OUTSIDE_REPO, ""
    match = closest_name(base, _all_symbol_names(table) - {base})
    if match is not None:
        return UnfilledReason.CLOSEST_NAME, match
    return UnfilledReason.NOT_FOUND, ""


def _resolve_request(
    request: RequestedContext,
    *,
    table: _Table,
    read_lines: _ReadLines,
    target_file: str,
    visible: _Visible,
) -> tuple[list[_Block], Unfilled | None]:
    """요청 하나 — 정규화 실패면 형식 사유, 성공이면 체인/평면 경로, 둘 다 못 채우면 조회
    실패 사유. `request.kind`는 각 블록에 실어 95% 중복 판정의 `Already in SOURCE` 줄에 쓴다."""
    normalized = normalize_target(request.target)
    if is_rejected(normalized):
        reason, detail = normalized
        return [], _unfilled(request, reason, detail)

    base, chain = normalized
    if chain:
        blocks = _resolve_chain(
            base,
            chain,
            request,
            table=table,
            read_lines=read_lines,
            target_file=target_file,
            visible=visible,
        )
    else:
        blocks = _resolve_flat(
            base,
            request,
            table=table,
            read_lines=read_lines,
            target_file=target_file,
            visible=visible,
        )

    if blocks:
        return [replace(b, request_kind=request.kind) for b in blocks], None

    reason, detail = _unfilled_lookup_reason(base, table=table)
    return [], _unfilled(request, reason, detail)


def _unfilled(request: RequestedContext, reason: UnfilledReason, detail: str) -> Unfilled:
    text = render_unfilled(kind=request.kind, target=request.target, reason=reason, detail=detail)
    return Unfilled(kind=request.kind, target=request.target, reason=reason, text=text)


def _fit_budget(blocks: Sequence[_Block], max_chars: int) -> tuple[list[_Block], list[str]]:
    """§4.4.4 — 블록 단위 first-fit. 넘친 블록은 통째로 빼고 이름을 `truncated`에.

    글자 상한은 폭주 차단이지 토큰 상한이 아니다(글자 상한은 토큰을 안 묶는다 — 모델마다
    글자/토큰 비가 다르다). `ResolveQuery.max_chars`는 루프가 남은 예산에서 계산해 넘긴다 —
    여기서는 그 값을 그대로 쓴다.
    """
    fitted: list[_Block] = []
    truncated: list[str] = []
    used = 0
    for block in blocks:
        cost = len(block.render()) + 2  # 뒤에 붙는 빈 줄("\n\n") 몫까지 어림
        if used + cost > max_chars:
            truncated.append(block.name)
            continue
        fitted.append(block)
        used += cost
    return fitted, truncated


def resolve(
    query: ResolveQuery,
    *,
    table: Callable[[str], Mapping[str, object] | None],
    read_lines: _ReadLines,
) -> Resolution:
    """§4 조회·조립 — `ResolveQuery`를 받아 `Resolution`을 낸다.

    `table("functions") is None`이면(v1 인덱스·인덱스 없음) 빈 `Resolution`을 돌려준다 —
    factory는 run 전에 인덱스 판을 모르므로 훅은 항상 꽂히고, 판단은 여기서 한다. 루프
    (Phase 5)는 빈 결과를 "하나도 못 줌"(`nothing_resolved`)으로 본다.

    `query.requests`가 `_MAX_REQUESTS`(3)를 넘으면 앞 3개만 처리한다(리뷰 5.1 [HIGH] —
    상수 주석). 넘친 요청은 이 호출의 결과에서 조용히 빠지지만, 루프의 홉 이벤트가 모델이
    보낸 `requests` 전체를 그대로 들고 있어 기록에서는 사라지지 않는다. 한 요청이 블록을
    여럿 낼 수 있어(예: 선언+대입) `blocks`·`already`의 합까지 3 이하로 묶이는 것은 아니다
    — `unfilled`만 요청당 최대 하나라 그 수는 처리한 요청 수(3 이하)를 넘지 않는다.
    """
    if table("functions") is None:
        return Resolution(followup_text="", blocks=(), unfilled=(), already=(), truncated=())

    all_blocks: list[_Block] = []
    unfilled: list[Unfilled] = []
    for request in query.requests[:_MAX_REQUESTS]:
        blocks, missed = _resolve_request(
            request,
            table=table,
            read_lines=read_lines,
            target_file=query.target_file,
            visible=query.visible,
        )
        all_blocks.extend(blocks)
        if missed is not None:
            unfilled.append(missed)

    kept: list[_Block] = []
    already: list[str] = []
    for block in all_blocks:
        visible_here = query.visible.get(block.file, frozenset())
        if is_duplicate_in_source(frozenset(block.lines), visible_here):
            lo, hi = min(block.lines), max(block.lines)
            already.append(
                render_already_in_source(
                    kind=block.request_kind,
                    name=block.name,
                    file=block.file,
                    line_start=lo,
                    line_end=hi,
                )
            )
        else:
            kept.append(block)

    fitted, truncated = _fit_budget(kept, query.max_chars)

    fragments = [block.render() for block in fitted]
    # 알림 줄 = already + not-resolved(리뷰 §1) — 골격(§5.6)에 Already 전용 자리는 없고,
    # 둘 다 "요청한 것을 그대로 안 준다"는 통지라 같은 UNFILLED 자리에 already를 먼저,
    # Not resolved를 뒤에 넣는다(모듈 docstring "명세 밖이거나 해석이 든 자리").
    alert_lines = [*already, *(item.text for item in unfilled)]
    note = needs_assignment_note(block.label for block in fitted)
    followup_text = (
        render_followup(
            hop=query.hop,
            fragments=fragments,
            unfilled_lines=alert_lines,
            include_assignment_note=note,
        )
        if fragments or alert_lines
        else ""
    )

    return Resolution(
        followup_text=followup_text,
        blocks=tuple(
            RetrievedBlock(name=b.name, label=b.label, file=b.file, lines=b.lines) for b in fitted
        ),
        unfilled=tuple(unfilled),
        already=tuple(already),
        truncated=tuple(truncated),
    )
