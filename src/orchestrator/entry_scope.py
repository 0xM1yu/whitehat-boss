"""슬라이스 타깃의 entry 한 줄을 「원본 소스로 읽을 범위」로 넓힌다 — Auditor와 Scout가 함께 쓴다.

슬라이스 타깃의 위치는 entry(줄 하나, `line_end` 없음)라 `read_source`에 그대로 주면 그 한 줄만
간다. 입력을 원본 소스로 선언하는 프롬프트(Auditor, Scout v4 — `meta.input: source`)는 entry를 품는
함수를 받아야 한다. 러너와 노드가 같은 규칙을 쓰도록 여기 한 곳에 둔다(노드는 `runner`를 import하지
않는다).
"""

from src.schemas.common import Location

# entry를 품는 심볼을 모를 때 읽는 entry 앞뒤 줄 수. **잰 값이 아니라 설계 상수다** — 함수 하나
# 정도를 덮고(대개 수십 줄) 프롬프트를 파일 크기와 무관하게 묶는 폭이다. 헤더의 `static inline`
# 함수는 2026-09-26부터 인덱서가 받아 여기로 오지 않는다(`_INDEX_SUFFIXES`). Scout도 같은 폭을 쓴다
# (#529) — 새 상수를 짓지 않는다.
ENTRY_WINDOW_LINES = 50


def spans(location: Location, line: int) -> bool:
    start = location.line_start
    return start is not None and start <= line <= (location.line_end or start)


def narrowest_symbol(symbols: list[Location], line: int) -> Location | None:
    """`line`을 품는 심볼 중 가장 좁은 것. 없으면 `None`. `symbols`는 한 파일의 것이다."""
    return min(
        (symbol for symbol in symbols if spans(symbol, line)),
        key=lambda symbol: (symbol.line_end or symbol.line_start or 0) - (symbol.line_start or 0),
        default=None,
    )


def enclosing_scope(
    symbols: list[Location] | None, entry: Location, window: int | None = None
) -> Location:
    """entry 줄을 품는 가장 좁은 심볼. 없으면 entry 앞뒤 `window`줄(기본 `ENTRY_WINDOW_LINES`).

    인덱스가 없을 때만이 아니라 **있어도** 심볼을 못 찾는다 — entry가 어느 정의에도 안 드는 줄
    (프로토타입·전역·매크로)일 수 있다. 그때 파일 전체를 주지 않고 창을 준다(파일 전체는 상한이
    없어 프롬프트가 서버 창을 넘는다). 창의 `line_end`는 파일 끝을 넘을 수 있다 — 읽기가 자른다.
    entry에 줄이 없으면(`slice_refs`가 늘 채우므로 실제로는 안 온다) 파일 전체다.
    """
    line = entry.line_start
    if line is None:
        return Location(file=entry.file)
    enclosing = narrowest_symbol(symbols or [], line)
    if enclosing is not None:
        return enclosing
    width = ENTRY_WINDOW_LINES if window is None else window
    return Location(file=entry.file, line_start=max(1, line - width), line_end=line + width)
