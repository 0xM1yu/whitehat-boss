"""CyberGym Level 1 과제 로더 테스트 (PoV 배선 — Phase 4).

과제(`repo-vul.tar.gz` + `description.txt`)를 하네스 입력으로 안전하게 적재한다.
대상은 **신뢰 못 하는 소스**라 tar 해제는 경로 이탈·링크·절대경로를 거부한다.
"""

import io
import tarfile
from pathlib import Path

import pytest

from src.orchestrator import task_loader


def _make_tar(path: Path, files: dict[str, bytes]) -> None:
    """정상 tar.gz를 만든다 — 멤버 이름은 루트 기준 상대경로."""
    with tarfile.open(path, "w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


def test_safe_extract_unpacks_a_normal_archive(tmp_path: Path) -> None:
    tar = tmp_path / "repo-vul.tar.gz"
    _make_tar(tar, {"src/a.c": b"int a;\n", "README.md": b"hi\n"})

    root = task_loader.safe_extract(tar, tmp_path / "out")

    assert (root / "src" / "a.c").read_text(encoding="utf-8") == "int a;\n"
    assert (root / "README.md").exists()


def test_safe_extract_rejects_path_traversal(tmp_path: Path) -> None:
    """`..`로 dest 밖을 노리는 멤버는 거부한다 — zip-slip 방어(대상은 못 믿는 소스)."""
    tar = tmp_path / "evil.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        payload = b"pwn"
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))

    with pytest.raises(task_loader.UnsafeArchiveError):
        task_loader.safe_extract(tar, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists(), "경로 이탈 파일이 dest 밖에 써졌다"


def test_safe_extract_rejects_symlink_members(tmp_path: Path) -> None:
    """심볼릭 링크 멤버는 거부한다 — 해제 후 링크를 타고 밖을 읽거나 덮을 수 있다."""
    tar = tmp_path / "link.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        info = tarfile.TarInfo(name="evil-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        t.addfile(info)

    with pytest.raises(task_loader.UnsafeArchiveError):
        task_loader.safe_extract(tar, tmp_path / "out")


def test_read_description_returns_text_or_none(tmp_path: Path) -> None:
    task = tmp_path / "task"
    task.mkdir()
    assert task_loader.read_description(task) is None  # 없으면 None(지어내지 않음)

    (task / task_loader.DESCRIPTION_FILENAME).write_text(
        "Heap overflow in parse_header when len > 64.\n", encoding="utf-8"
    )
    assert task_loader.read_description(task) == "Heap overflow in parse_header when len > 64.\n"


def test_load_task_untars_repo_and_reads_description(tmp_path: Path) -> None:
    """Level 1 과제 디렉터리(repo-vul.tar.gz + description.txt)를 (소스 루트, 설명)으로 편다."""
    task = tmp_path / "task"
    task.mkdir()
    _make_tar(task / "repo-vul.tar.gz", {"parser.c": b"void f(){}\n"})
    desc = task / task_loader.DESCRIPTION_FILENAME
    desc.write_text("CWE-122 heap overflow\n", encoding="utf-8")

    source_root, description = task_loader.load_task(task, tmp_path / "work")

    assert (source_root / "parser.c").exists()
    assert description == "CWE-122 heap overflow\n"


def test_load_task_requires_the_repo_archive(tmp_path: Path) -> None:
    """`repo-vul.tar.gz`가 없으면 명확히 실패한다 — 조용히 빈 소스로 넘어가지 않는다."""
    task = tmp_path / "task"
    task.mkdir()
    (task / task_loader.DESCRIPTION_FILENAME).write_text("x\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        task_loader.load_task(task, tmp_path / "work")
