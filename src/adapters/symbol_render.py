"""NE(need_evidence) resolver의 순수 렌더·정규화 규칙 — 명세 §4.3(target 정규화)·
§4.4.4(중복 제거)·§4.4.5(RETRIEVED/UNFILLED 렌더)를 코드로 옮긴다 (ADR 0012 결정 2·3).
조회(`table()`)도 파일 읽기도 하지 않는다 — 값을 받아 문자열이나 판정을 돌려줄 뿐이다.

명세 원천은 팀 Notion 「실험팀 › Scout RAG baseline」
(
2026-09-26 판독 — 2026-09-26 판독본과 바이트 대조 통과)이다. 이 모듈의 문자열은 그 원문을
그대로 옮긴 것이고, 의역하지 않는다.

조회·점수·`resolve()` 조립은 `src/adapters/symbol_resolver.py`에 있다 — 그 모듈이 이
모듈을 가져다 쓴다. 계약(`ResolveQuery`·`Resolution`·`UnfilledReason` 등)은
`src/schemas/need_evidence.py`·`src/schemas/enums.py`에 있다.

## 명세 밖이거나 해석이 든 자리

- `closest_name`의 cutoff, `_LIBC_SYMBOLS`의 목록 — 명세가 값을 안 준다.
- 대입 주의문(라벨 3·13이 있을 때만)의 정확한 삽입 자리 — 골격 원문(명세 §4.4.5 골격)은
  위치를 산문으로만 설명하고, hop 1 예(라벨 4뿐)에는 주의문이 없어 위치를 못 보여준다.
  RETRIEVED 조각들과 UNFILLED 사이로 뒀다 — **추측**이다.
"""

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence

from src.schemas.enums import UnfilledReason

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
    """UNFILLED 한 줄 — `Not resolved: <kind>:<target> (<사유>).`(명세 §4.4.5).

    `target`은 호출부가 쥔 **원문 그대로**(모델이 보낸 raw target)를 넣는다 — 정규화된 값이
    아니다. 명세의 거부 예 둘(`unknown`·`stl:error`)이 원문을 그대로 보여준다.
    """
    phrase = _REASON_PHRASES[reason].format(detail=detail)
    return f"Not resolved: {kind}:{target} ({phrase})."


# --- target 정규화 ----------------------------------------------------------
#
# 명세 §4.3 8단계 + 0단계(`@file:L…` 접미 제거 — 아래 주석). 성공은 (base, chain), 실패는
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
    """명세 §4.3 정규화. 성공: `(base, chain)`. 실패: `(사유, 상세값)`.

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
    """비슷한 철자의 심볼 이름 하나(명세 §4.4.5 UNFILLED `closest_name`). 없으면 `None`."""
    matches = difflib.get_close_matches(target, list(candidates), n=1, cutoff=_CLOSEST_NAME_CUTOFF)
    return matches[0] if matches else None


# ISO C 표준 라이브러리 + 흔한 POSIX 함수. 명세는 목록을 안 준다(명세 §4.4.5). ponytail: 전체
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
    """명세 §4.4.5 UNFILLED `libc` 판정 — `_LIBC_SYMBOLS` 안에 있는가."""
    return name in _LIBC_SYMBOLS


# --- RETRIEVED 조각 렌더 -----------------------------------------------------
#
# 13 라벨(명세 §4.4.5, 원문 그대로). 3·7·13은 템플릿이다 — 호출부(B 단계)가 `.format()`한다.

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
# in"이라 안 걸린다 — 원문(명세 §4.4.5 주의문 조건 문단)이 주의문을 붙이는 "대입 블록"을
# "target에 들어갈 수 있는 경우의 수 집합"이라 적고, 라벨 13 설명도 "가능한 값의 집합"이라고
# 적는데 그 집합에 해당하는 것은 라벨 3·13뿐이다(같은 함수 안 대입 1줄인 라벨 4는 집합이 아니다).
_ASSIGNMENT_NOTE_LABEL_PREFIX = "assignment to "


def render_fragment(*, name: str, label: str, file: str, lines: Sequence[tuple[int, str]]) -> str:
    """조각 하나 — 명세 §4.4.5 원문 틀. `lines`는 (원본 줄번호, 원본 줄 텍스트) 순서 그대로.

    줄번호는 4칸 오른쪽 맞춤. 원본 줄이 빈 문자열이면 `| ` 뒤 공백을 안 붙인다(명세 예
    실측 — 명세 hop 1 예의 빈 줄 자리가 그렇게 비어 있다).
    """
    header = f"/* --- retrieved: {name} ({label}) --- */"
    banner = f"/* ===== {file} ===== */"
    body = [f"{line_no:>4}| {text}" if text else f"{line_no:>4}|" for line_no, text in lines]
    return "\n".join((header, banner, *body))


def render_already_in_source(
    *, kind: str, name: str, file: str, line_start: int, line_end: int
) -> str:
    """`Already in SOURCE: <kind>:<이름> at <파일>:L<시작>-<끝>.`(명세 §4.4.4)."""
    return f"Already in SOURCE: {kind}:{name} at {file}:L{line_start}-{line_end}."


# 명세 §4.4.4 — 95% 이상 겹치면 중복. 정수 교차곱으로 봐 부동소수 경계(94.999...%) 오판을 피한다.
_DUPLICATE_THRESHOLD_NUMERATOR = 95
_DUPLICATE_THRESHOLD_DENOMINATOR = 100


def is_duplicate_in_source(block_lines: frozenset[int], visible_lines: frozenset[int]) -> bool:
    """블록 줄 집합이 SOURCE에 보이는 줄과 95% 이상 겹치면 참(명세 §4.4.4). 빈 블록은 거짓이다."""
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
    """라벨 3·13 블록이 하나라도 있으면 대입 주의문을 넣는다(명세 §4.4.5, 원문 근거는 위
    모듈 상수 주석).

    라벨 4(`assignment in the same function`)만으로는 넣지 않는다 — 원문(명세 §4.4.5 주의문
    조건 문단)의 "대입 블록(target에 들어갈 수 있는 경우의 수 집합)"과 라벨 13 설명의
    "가능한 값의 집합"에 해당하는 것은 라벨 3·13뿐이다. 아직 필드/전역변수 이름이 안 채워진
    템플릿(`LABEL_ASSIGNMENT_TO_FIELD` 등)과 이미 채워진 라벨 둘 다 이 접두 검사로 맞는다.
    """
    return any(label.startswith(_ASSIGNMENT_NOTE_LABEL_PREFIX) for label in labels)


def render_followup(
    *,
    hop: int,
    fragments: Sequence[str],
    unfilled_lines: Sequence[str],
    include_assignment_note: bool,
) -> str:
    """명세 §4.4.5 골격 그대로 조립한 후속 턴 전체 텍스트.

    빈 줄 배치는 명세 §4.4.5 「실제 출력 전문」(hop 1 예)에서 역산했다 — 조각과 UNFILLED 줄은
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
