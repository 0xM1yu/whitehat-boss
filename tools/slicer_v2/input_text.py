"""Source-text helpers shared by the input-context modules (`input_paths`, `input_context`).

The slicer is a standalone tool that does not import the harness (docs/slicer-README.md), so the
two harness definitions it needs are restated here -- and pinned to the originals by
tests/test_input_context.py, so a change on one side fails the other's test instead of drifting:

- `Location` is `src.schemas.common.Location` without the pydantic model (same four fields).
- `code_only` is `src.orchestrator.source_definitions.code_only` (same regex, same output).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    file: str
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None


# Numbers are eaten first and kept: C23/C++14 digit separators (`1'000`) must not open a char
# literal that would swallow the code up to the next quote. A line comment continues over a
# trailing backslash; a block comment that is never closed runs to the end of the text.
_NOT_CODE = re.compile(
    r"(?P<num>(?<![A-Za-z0-9_])[0-9](?:'(?=[A-Za-z0-9_])|[A-Za-z0-9_.])*)"
    r"|//(?:\\.|[^\n\\])*"
    r"|/\*.*?(?:\*/|\Z)"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'",
    re.DOTALL,
)


def code_only(text: str) -> str:
    """Comments, strings and char literals become one space; the newlines inside them stay, so a
    line number taken from the result is the line number in the source."""
    return _NOT_CODE.sub(
        lambda m: m.group() if m.group("num") else " " + "\n" * m.group().count("\n"), text
    )


def lines_of(text: str) -> list[str]:
    """Split the way Joern and the slicer count lines: on `\\n` only.

    `str.splitlines()` also cuts at form feed, vertical tab, U+2028 and others, which shifts every
    later line number against the CPG and the syntax index (tools/slicer/slicer.py `load_sources`
    and src/adapters/slices.py `split_source` state the same rule, #259).
    """
    return text.split("\n")


def scope(location: Location) -> tuple[str, int]:
    """Identity of a function in the index: its file and first line."""
    return location.file, location.line_start or -1


def contains(location: Location, file: str, line: int) -> bool:
    return location.file == file and (location.line_start or 0) <= line <= (location.line_end or 0)
