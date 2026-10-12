"""코드 위치 — 하네스 `src/schemas/common.Location`의 독립판.

하네스 쪽은 pydantic 모델이다(frozen · extra forbid). 여기서는 같은 네 필드를 가진 frozen
dataclass로 둔다 — `index_graph`·`source_definitions`·`attach`가 쓰는 것은 필드 읽기와
해시(딕셔너리 키)뿐이라서다. 하네스의 `line_end >= line_start` 검증은 그대로 옮겼다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    file: str
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None

    def __post_init__(self) -> None:
        if not self.file:
            raise ValueError("Location.file is empty")
        for value in (self.line_start, self.line_end):
            if value is not None and value < 1:
                raise ValueError(f"line must be >= 1: {value}")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError(f"line_end({self.line_end}) < line_start({self.line_start})")
