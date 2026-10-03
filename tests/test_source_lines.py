"""#446: 원문을 읽는 쪽이 인덱서와 같은 줄 좌표를 쓴다.

인덱서(tree-sitter)는 `\\n`에서만 행을 넘긴다. `str.splitlines()`는 폼피드에서도 끊어
폼피드마다 한 줄씩 밀렸다 -> ImageMagick `MagickCore/xml-tree.c`(폼피드 26개)에서 `ParseEntities`
범위가 주석부터 시작하고 끝이 잘렸다. 아래 픽스처는 GNU 스타일 소스처럼 함수 사이에 `\\f` 줄을 둔다.
"""

from pathlib import Path

import pytest

from src.adapters.fake_retriever import read_symbol_source as fake_read
from src.adapters.fake_tools import FakeToolRunner
from src.adapters.hybrid_retriever import read_symbol_source as hybrid_read
from src.adapters.index_graph import source_lines
from src.ids import IdFactory
from src.schemas.common import Location

# 줄 번호는 `\n`만 센 값(인덱서 좌표): first 1-4, `\f` 5, second 6-9, `\f` 10, third 11-14.
SOURCE = (
    "static int first(void)\n{\n  return 1;\n}\n"
    "\f\n"
    "static int second(void)\n{\n  return 2;\n}\n"
    "\f\n"
    "static int third(char *p)\n{\n  return strcpy(p, \"x\") != 0;\n}\n"
)
THIRD = 'static int third(char *p)\n{\n  return strcpy(p, "x") != 0;\n}'


@pytest.fixture(name="root")
def formfeed_root(tmp_path: Path) -> Path:
    (tmp_path / "ff.c").write_bytes(SOURCE.encode("utf-8"))
    return tmp_path


def test_the_fixture_really_shifts_under_splitlines() -> None:
    """픽스처가 결함을 재현한다 -> 폼피드 둘이 `splitlines()`에서 두 줄을 더 만든다."""
    assert len(SOURCE.splitlines()) == len(SOURCE.split("\n")) - 1 + 2


@pytest.mark.parametrize("read", [fake_read, hybrid_read], ids=["fake", "hybrid"])
def test_a_range_after_form_feeds_is_the_function_the_index_named(root: Path, read) -> None:
    assert read(root, Location(file="ff.c", symbol="third", line_start=11, line_end=14)) == THIRD


@pytest.mark.parametrize("read", [fake_read, hybrid_read], ids=["fake", "hybrid"])
def test_crlf_files_still_read_without_carriage_returns(tmp_path: Path, read) -> None:
    (tmp_path / "crlf.c").write_bytes(SOURCE.replace("\n", "\r\n").encode("utf-8"))
    got = read(tmp_path, Location(file="crlf.c", symbol="third", line_start=11, line_end=14))
    assert got == THIRD


def test_source_lines_breaks_only_at_newline() -> None:
    assert source_lines("a\fb\nc\r\nd\x85e") == ["a\fb", "c", "d\x85e"]


def test_the_static_hit_line_is_an_index_line(root: Path) -> None:
    """fake 정적 도구가 내는 줄 번호를 `read_symbol_source`가 같은 줄로 읽는다."""
    runner = FakeToolRunner(ids=IdFactory("run_test"), run_id="run_test", root=root)
    hit = runner._first_static_location(root)
    assert hit is not None and hit.line_start == 13
    assert "strcpy" in fake_read(root, Location(file="ff.c", line_start=hit.line_start))
