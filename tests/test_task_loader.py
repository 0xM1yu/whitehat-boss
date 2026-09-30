"""CyberGym Level 1 과제 로더 테스트 (PoV 배선 — Phase 4).

과제(`repo-vul.tar.gz` + `description.txt`)를 하네스 입력으로 안전하게 적재한다.
대상은 **신뢰 못 하는 소스**라 tar 해제는 경로 이탈·링크·절대경로를 거부한다.
"""

import io
import os
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


def test_safe_extract_rejects_an_absolute_symlink_member(tmp_path: Path) -> None:
    """dest 밖(여기서는 절대경로)을 가리키는 심볼릭 링크는 거부한다 — 링크를 타고 밖을 읽거나
    덮을 수 있다. (dest 안 상대 링크는 data 필터가 허용한다 — 이 검사의 범위 밖이다.)"""
    tar = tmp_path / "link.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        info = tarfile.TarInfo(name="evil-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        t.addfile(info)

    with pytest.raises(task_loader.UnsafeArchiveError):
        task_loader.safe_extract(tar, tmp_path / "out")


def test_safe_extract_contains_absolute_path_members_under_dest(tmp_path: Path) -> None:
    """절대경로 멤버는 **거부가 아니라** dest 안으로 눌러 담긴다(data 필터 동작) — 밖에 안 써진다.
    로더 docstring이 이 동작을 정확히 적게 하는 핀이다(예전엔 「거부」로 과대주장했다)."""
    tar = tmp_path / "abs.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        payload = b"x"
        info = tarfile.TarInfo(name="/abs_evil.txt")
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))

    root = task_loader.safe_extract(tar, tmp_path / "out")

    assert (root / "abs_evil.txt").exists()  # 절대경로가 dest 안으로 눌러 담김


def test_safe_extract_rejects_a_decompression_bomb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """해제 크기 상한을 넘는 아카이브는 거부한다 — data 필터가 안 묶는 자원 상한(디스크 폭탄)."""
    tar = tmp_path / "bomb.tar.gz"
    _make_tar(tar, {"big.bin": b"x" * 5000})
    monkeypatch.setattr(task_loader, "_MAX_TOTAL_BYTES", 1000)

    with pytest.raises(task_loader.UnsafeArchiveError):
        task_loader.safe_extract(tar, tmp_path / "out")


def test_safe_extract_rejects_too_many_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """멤버 수 상한을 넘는 아카이브는 거부한다 — 수많은 작은 파일도 자원 폭탄이다."""
    tar = tmp_path / "many.tar.gz"
    _make_tar(tar, {f"f{i}.c": b"x" for i in range(10)})
    monkeypatch.setattr(task_loader, "_MAX_MEMBERS", 3)

    with pytest.raises(task_loader.UnsafeArchiveError):
        task_loader.safe_extract(tar, tmp_path / "out")


def _link_member(name: str, target: str, kind: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.type = kind
    info.linkname = target
    return info


def _fifo_member() -> tarfile.TarInfo:
    info = tarfile.TarInfo(name="pipe")
    info.type = tarfile.FIFOTYPE
    return info


@pytest.mark.parametrize(
    "member",
    [
        pytest.param(lambda: _link_member("h", "../outside.txt", tarfile.LNKTYPE), id="hardlink"),
        pytest.param(lambda: _link_member("s", "../../etc/passwd", tarfile.SYMTYPE), id="symlink"),
        pytest.param(_fifo_member, id="fifo"),
    ],
)
def test_safe_extract_rejects_links_and_special_members_by_its_own_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member
) -> None:
    """dest 밖을 가리키는 하드·심볼릭 링크(상대경로)와 특수 파일(FIFO)도 거부하고 만들지 않는다
    — 위 검사 밖의 갈래다.

    **인터프리터의 기본 추출 필터를 `fully_trusted`로 되돌려 놓고** 본다. 3.14부터 인자 없는
    기본이 `data`라, 로더의 명시 `filter="data"`를 지워도 위 검사들은 3.14에서 전부 초록이다
    (3.11~3.13의 기본은 `fully_trusted`다 — tarfile 문서). 이 검사는 로더 **자신의** 인자가 유일한
    방어일 때도 거부하는지를 어느 판에서든 본다. 필터 이름이 아니라 결과(예외 종류 · 멤버
    미생성)를 단언한다. 하드 링크가 가리킬 dest 밖 파일은 실제로 둔다(없으면 링크가 안 생긴다).
    ⚠ 인터프리터 필터 자체의 우회 결함은 이 검사가 못 본다 — 로더가 필터를 거는지까지만 본다.
    """
    monkeypatch.setattr(
        tarfile.TarFile, "extraction_filter", staticmethod(tarfile.fully_trusted_filter)
    )
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
    info = member()
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        t.addfile(info)

    with pytest.raises(task_loader.UnsafeArchiveError):
        task_loader.safe_extract(archive, tmp_path / "out")

    assert not os.path.lexists(tmp_path / "out" / info.name), "거부한 멤버가 dest에 생겼다"


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


class _LegacyTarfile:
    """CPython 3.11.0~3.11.3의 `tarfile` **모양**을 흉내 내는 대역(PEP 706 이전) — 시뮬레이션이다.

    실제 3.11.0~3.11.3 인터프리터로 돌린 것이 아니다 — 로컬·CI는 그 패치를 안 돈다(CI의
    `python-version: "3.11"`은 최신 3.11.x로 풀린다). 로더가 만지는 이름만 옛 모양으로 바꾼다:
    `data_filter`·`FilterError` 등이 없고 `TarFile.extract()`가 `filter=`를 `TypeError`로 거부한다
    (docs.python.org/3.11/library/tarfile.html: *Changed in version 3.11.4: Added the filter
    parameter*). 두 오류 문구는 3.11.0 인터프리터가 내는 문구와 같게 맞췄다.
    ⚠ `tarfile.TarFile`은 지금 인터프리터의 것 그대로다 — 실제 3.11.0~3.11.3에는 없는
    `TarFile.extraction_filter`로 판별하는 (실제로는 유효한) 가드는 이 대역에서 거짓 빨강이다.
    """

    _ABSENT = frozenset({"data_filter", "tar_filter", "fully_trusted_filter", "FilterError"})

    def open(self, *args: object, **kwargs: object):  # noqa: A003, ANN201 - 모듈 API 이름
        tar = tarfile.open(*args, **kwargs)
        real_extract = tar.extract

        def extract(member, path="", set_attrs=True, *, numeric_owner=False, **rest):  # noqa: ANN001, ANN202
            if rest:
                raise TypeError(
                    f"TarFile.extract() got an unexpected keyword argument '{next(iter(rest))}'"
                )
            return real_extract(member, path, set_attrs, numeric_owner=numeric_owner)

        tar.extract = extract
        return tar

    def __getattr__(self, name: str):  # noqa: ANN204
        if name in self._ABSENT:
            raise AttributeError(f"module 'tarfile' has no attribute '{name}'")
        return getattr(tarfile, name)


def test_safe_extract_refuses_an_interpreter_without_extraction_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """추출 필터가 없는 인터프리터(3.11.0~3.11.3 — `_LegacyTarfile` 시뮬레이션)에서는 **풀기
    전에** 이유를 대며 거부한다.

    그 거부가 없으면 첫 멤버에서 `TypeError`로 죽고, 그 예외가 올라가며 `except
    tarfile.FilterError`의 식이 평가돼 `AttributeError`로 **바뀐다** — 원인이 가려지고, 크기·개수
    상한이 던지는 `UnsafeArchiveError`까지 같은 이유로 가려진다(PR #343 리뷰 M-1). 필터 없이 푸는
    폴백은 만들지 않는다(대상은 못 믿는 소스다) — dest도 만들지 않는 것으로 「풀기 전」을 고정한다.
    """
    archive = tmp_path / "repo-vul.tar.gz"
    _make_tar(archive, {"a.c": b"int a;\n"})
    monkeypatch.setattr(task_loader, "tarfile", _LegacyTarfile())

    with pytest.raises(RuntimeError, match=r"3\.11\.4"):
        task_loader.safe_extract(archive, tmp_path / "out")

    assert not (tmp_path / "out").exists(), "거부했는데 dest를 만들었다"


def test_load_task_refuses_an_interpreter_without_extraction_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`load_task`도 같은 거부를 감싸지 않고 그대로 올린다 — 과제 입구에서 원인이 보이고,
    `work_dir`도 만들지 않는다(풀기 전 거부)."""
    task = tmp_path / "task"
    task.mkdir()
    _make_tar(task / task_loader.REPO_ARCHIVE_NAME, {"a.c": b"int a;\n"})
    monkeypatch.setattr(task_loader, "tarfile", _LegacyTarfile())

    with pytest.raises(RuntimeError, match=r"3\.11\.4"):
        task_loader.load_task(task, tmp_path / "work")

    assert not (tmp_path / "work").exists(), "거부했는데 work_dir을 만들었다"
