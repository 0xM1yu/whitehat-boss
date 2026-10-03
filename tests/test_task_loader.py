"""CyberGym Level 1 과제 로더 테스트 (PoV 배선 — Phase 4).

과제(`repo-vul.tar.gz` + `description.txt`)를 하네스 입력으로 안전하게 적재한다.
대상은 **신뢰 못 하는 소스**라 tar 해제는 경로 이탈·dest 밖 링크·특수 파일을 거부하고, 절대경로
멤버는 거부하지 않고 dest 안으로 눌러 담는다(`task_loader` 모듈 docstring과 같은 서술).
"""

import io
import os
import random
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


def _truncated_gz(path: Path) -> None:
    """중간에서 끊긴 gz — 다운로드가 끊겼을 때의 모양이다. 해제는 앞 멤버를 풀고 나서 죽는다."""
    rnd = random.Random(1)
    _make_tar(path, {f"f{i}.c": rnd.randbytes(60_000) for i in range(3)})
    path.write_bytes(path.read_bytes()[: path.stat().st_size * 6 // 10])


@pytest.mark.parametrize(
    "make",
    [
        # HuggingFace LFS 포인터를 `curl -L` 없이 받으면 정확히 이 모양이다(`tarfile.ReadError`).
        pytest.param(
            lambda p: p.write_text("version https://git-lfs.github.com/spec/v1\n", "utf-8"),
            id="not-gzip",
        ),
        pytest.param(lambda p: p.write_bytes(b""), id="empty"),
        pytest.param(_truncated_gz, id="truncated"),
    ],
)
def test_safe_extract_names_an_unreadable_archive_as_unreadable(tmp_path: Path, make) -> None:
    """🔴 못 읽는 아카이브(비-gzip·빈 파일·끊긴 gz)는 `tarfile.ReadError`·`EOFError`가 **새지 않고**
    `UnsafeArchiveError`로 올라온다 — 호출자가 「이 과제를 못 쓴다」를 한 사건으로 다룬다.

    `except (tarfile.TarError, EOFError)`를 지우면 호출자(CLI)의 이중 방어가 가려 주던 구멍이 여기서
    드러난다(#381 리뷰 LOW — 단위 테스트가 0이었다). 예외 클래스 이름은 단언하지 않는다 — 끊긴
    gz는 인터프리터 판에 따라 `EOFError`이거나 `ReadError`다."""
    archive = tmp_path / "repo-vul.tar.gz"
    make(archive)

    with pytest.raises(task_loader.UnsafeArchiveError, match="읽을 수 없다"):
        task_loader.safe_extract(archive, tmp_path / "out")


def test_safe_extract_names_an_unsafe_member_as_unsafe_not_as_unreadable(tmp_path: Path) -> None:
    """안전 위반(`FilterError`)과 손상은 **다른 사유**다 — 문구가 갈린다.

    `FilterError`는 `TarError`의 하위라 `except` 순서를 바꾸면 안전 위반이 「읽을 수 없다」로
    읽힌다. 호출자가 `UnsafeArchiveError`만 잡아도 사유를 못 가르게 되는 변이가 여기서 죽는다."""
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = 3
        t.addfile(info, io.BytesIO(b"pwn"))

    with pytest.raises(task_loader.UnsafeArchiveError, match="안전하지 않은") as excinfo:
        task_loader.safe_extract(archive, tmp_path / "out")

    assert "읽을 수 없다" not in str(excinfo.value)


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
    상한이 던지는 `UnsafeArchiveError`까지 같은 이유로 가려진다. 필터 없이 푸는 폴백은 만들지
    않는다(대상은 못 믿는 소스다) — dest도 만들지 않는 것으로 「풀기 전」을 고정한다.
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


# --- 과제 디렉터리 허용 목록 -------------------------------------------------------------------
#
# 저자 데이터셋의 과제 폴더는 **다섯 파일**이다 — 허용된 둘(`repo-vul.tar.gz`·`description.txt`)에
# 정답 자료 셋(`repo-fix.tar.gz`·`patch.diff`·`error.txt`). HF `sunblaze-ucb/cybergym` 리비전
# `bde190d`의 `data/<종류>/<id>/` 목록을 1,507개 폴더 전부에서 읽었고(2026-10-03) 전부 같은 다섯
# 파일이며 중첩은 0건이다. **이름만 쟀다** — 아카이브 내용은 안 열었다(모듈 docstring).
# `data/<종류>/<id>/*`로 받으면 다섯이 다 떨어지고, 그 디렉터리로 도는 run은 무효다. **정답 자료의
# 실물은 이 파일에 없다** — 가드는 이름만 보므로 아래 내용은 전부 무해한 자리표시자다.

_PLACEHOLDER = b"placeholder, not real task material\n"


def _task_dir(
    tmp_path: Path, *, extras: tuple[str, ...] = (), empty_dirs: tuple[str, ...] = ()
) -> Path:
    """허용된 두 파일(`repo-vul.tar.gz`·`description.txt`)에 `extras`(상대 경로 — 하위 디렉터리는
    같이 만든다)와 `empty_dirs`를 얹은 과제 디렉터리를 만든다."""
    task = tmp_path / "task"
    task.mkdir()
    _make_tar(task / task_loader.REPO_ARCHIVE_NAME, {"parser.c": b"void f(){}\n"})
    (task / task_loader.DESCRIPTION_FILENAME).write_text(
        "CWE-122 heap overflow\n", encoding="utf-8"
    )
    for relative in extras:
        target = task / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_PLACEHOLDER)
    for name in empty_dirs:
        (task / name).mkdir()
    return task


def test_load_task_accepts_a_directory_of_exactly_the_two_expected_files(tmp_path: Path) -> None:
    """가드가 정상 과제를 막지 않는다 — 허용된 두 파일뿐인 디렉터리는 그대로 적재된다."""
    task = _task_dir(tmp_path)

    source_root, description = task_loader.load_task(task, tmp_path / "work")

    assert (source_root / "parser.c").exists()
    assert description == "CWE-122 heap overflow\n"


def test_load_task_accepts_the_authors_generator_output_of_four_files(tmp_path: Path) -> None:
    """🔴 저자 생성기(`gen_task`)의 산출은 받는다 — Level 1 둘에 `README.md`·`submit.sh`를 더한
    **네 파일**.

    `submit.sh`는 채점기 신원(서버 · `task_id` · `agent_id`)의 **유일한 출처**다. 생성기를 마스크
    맵으로 돌렸으면 `task_id`는 마스킹된 값이라 하네스가 다시 만들 수 없고,
    `--oracle-submit`(#408)의 `read_submit_metadata`가 이 파일을 과제 디렉터리에서 읽는다. 허용
    목록이 둘뿐이던 판은 채점할 수 있는 번들을 전부 거부했다 — 두 PR이 서로 다른 곳에 있어 CI가 못
    잡는 결함이다. 이 단언이 허용 목록을 다시 둘로 좁히는 변경을 빨갛게 한다."""
    task = _task_dir(tmp_path, extras=("README.md", "submit.sh"))

    source_root, description = task_loader.load_task(task, tmp_path / "work")

    assert (source_root / "parser.c").exists()
    assert description == "CWE-122 heap overflow\n"


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("patch.diff", id="fix-diff"),
        pytest.param("repo-fix.tar.gz", id="patched-source-tarball"),
        pytest.param("error.txt", id="crash-log"),
    ],
)
def test_load_task_refuses_dataset_answer_material_and_names_it(tmp_path: Path, name: str) -> None:
    """저자 데이터셋이 과제 옆에 두는 정답 자료 셋(수정 diff · 패치된 소스 tarball · 크래시 로그)이
    디렉터리에 있으면 거부하고, 메시지가 그 파일명을 든다 — 무엇을 지울지 알려야 고친다.

    **풀기 전에** 막는다 — `work_dir`이 안 생긴다. 거부한 과제가 run 디렉터리에 뭔가를 남기면
    「거부했는데 시작됐다」가 된다.
    """
    task = _task_dir(tmp_path, extras=(name,))

    with pytest.raises(task_loader.UnexpectedTaskFilesError) as refused:
        task_loader.load_task(task, tmp_path / "work")

    assert name in str(refused.value)
    assert not (tmp_path / "work").exists(), "거부했는데 work_dir을 만들었다 — 풀기 전에 막는다"


def test_load_task_refuses_an_unrelated_extra_file(tmp_path: Path) -> None:
    """허용 목록이지 차단 목록이 아니다 — 정답 자료 이름 셋을 몰라도 **모르는 파일은 거부한다.**

    이 단언이 가드를 「이름 셋짜리 차단 목록」으로 되돌리는 변경을 빨갛게 한다."""
    task = _task_dir(tmp_path, extras=("notes-from-a-colleague.md",))

    with pytest.raises(task_loader.UnexpectedTaskFilesError) as refused:
        task_loader.load_task(task, tmp_path / "work")

    assert "notes-from-a-colleague.md" in str(refused.value)


@pytest.mark.parametrize(
    ("extras", "empty_dirs", "offenders"),
    [
        pytest.param(("poc",), (), ("poc",), id="reference-poc"),
        pytest.param(("binaries/magic_fuzzer.fix",), (), ("binaries",), id="nested-directory"),
        pytest.param((), ("stray-empty-dir",), ("stray-empty-dir",), id="empty-directory"),
        pytest.param((".DS_Store",), (), (".DS_Store",), id="dotfile-os-litter"),
        pytest.param((".gitkeep",), (), (".gitkeep",), id="dotfile-gitkeep"),
        pytest.param(
            ("patch.diff", "error.txt", "repo-fix.tar.gz"),
            (),
            ("patch.diff", "error.txt", "repo-fix.tar.gz"),
            id="all-three-answer-files-together",
        ),
    ],
)
def test_load_task_refuses_every_entry_outside_the_allowlist_whatever_its_kind(
    tmp_path: Path,
    extras: tuple[str, ...],
    empty_dirs: tuple[str, ...],
    offenders: tuple[str, ...],
) -> None:
    """허용된 네 이름이 아니면 **종류를 가리지 않고** 거부하고, 걸린 이름을 **전부** 든다.

    경계를 우연에 두지 않으려고 한 줄씩 못박는다. 참조 PoC(`poc`)와 중첩 디렉터리(`binaries/`)는
    저자 생성기 `ARVO_FILES`가 **선언한** 이름이다(데이터셋 폴더 1,507개의 목록에는 하나도 없었다 —
    그래도 허용 목록 밖이라 거부된다, 허용 목록을 고른 이유). 빈 디렉터리와 점 파일(`.DS_Store`·
    `.gitkeep` 포함)도 거부한다 — 예외를 두면 숨기기 좋은 자리가 생긴다. 정답 자료 셋을 **한꺼번에**
    둔 줄은 메시지가 셋 다 드는지 본다 — 첫 이름만 들어도 앞 줄들은 초록이다. 메시지는 허용 이름도
    적는다(무엇이 되는지 모르면 못 고친다). 파일 **내용**은 되풀이하지 않는다.
    """
    task = _task_dir(tmp_path, extras=extras, empty_dirs=empty_dirs)

    with pytest.raises(task_loader.UnexpectedTaskFilesError) as refused:
        task_loader.load_task(task, tmp_path / "work")

    message = str(refused.value)
    assert all(name in message for name in offenders)
    assert all(name in message for name in sorted(task_loader.TASK_FILES))
    assert _PLACEHOLDER.decode("utf-8").strip() not in message


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("PATCH.DIFF", id="answer-name-in-upper-case"),
        pytest.param("Error.TXT", id="answer-name-in-mixed-case"),
        pytest.param("README.MD", id="allowed-name-in-upper-case"),
        pytest.param("SUBMIT.SH", id="allowed-script-name-in-upper-case"),
    ],
)
def test_the_allowlist_match_is_exact_so_case_cannot_change_the_verdict(
    tmp_path: Path, name: str
) -> None:
    """이름 비교는 **정확히 같은 글자만** 받는다 — 대소문자를 접지 않는다.

    CI는 Ubuntu(대소문자를 구분한다)이고 작성자는 Windows(구분하지 않는다)다. 접는 비교는 한쪽에서만
    참인 가정이 된다. 정답 자료 이름의 대문자판(`PATCH.DIFF`)은 어느 FS에서든 목록 밖이라 거부되고,
    **허용된 이름의 대문자판**(`README.MD`)도 거부된다 — 비교를 접는 쪽으로 바꾸면 뒤의 둘이 통과해
    이 테스트가 죽는다.
    ⚠ `Description.txt`·`Repo-Vul.tar.gz` 같은 변형은 못 쓴다 — 구분하지 않는 FS에서는 픽스처의 진짜
    파일과 **같은 파일**이 되어 입력이 성립하지 않는다.
    """
    task = _task_dir(tmp_path, extras=(name,))

    with pytest.raises(task_loader.UnexpectedTaskFilesError) as refused:
        task_loader.check_task_dir(task)

    assert name in str(refused.value)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("description.txt.bak", id="allowed-name-plus-backup-suffix"),
        pytest.param("description.txt~", id="allowed-name-plus-editor-tilde"),
        pytest.param("repo-vul.tar.gz.part", id="interrupted-download-of-the-archive"),
        pytest.param("repo-vul.tar.gz.orig", id="archive-name-plus-orig"),
        pytest.param("old-description.txt", id="allowed-name-as-a-suffix"),
        pytest.param("submit.sh.bak", id="script-name-plus-backup-suffix"),
        pytest.param("README.md.orig", id="readme-name-plus-orig"),
        pytest.param("README", id="proper-prefix-of-an-allowed-name"),
        pytest.param("repo-vul.tar", id="proper-prefix-of-the-archive-name"),
    ],
)
def test_load_task_refuses_a_name_that_only_resembles_an_allowed_one(
    tmp_path: Path, name: str
) -> None:
    """허용된 이름을 **품은** 이름(또는 그 이름의 앞부분)도 거부한다 — 비교는 접두·접미·부분
    문자열이 아니라 **정확히 같은 이름**이다.

    에디터 백업(`.bak`·`~`) · 끊긴 다운로드(`.part`) · `patch`가 남기는 `.orig`는 실제로 생기는
    모양이다. 비교를 `startswith`·`in`으로 바꾸면 이 이름들이 통과하고 이 테스트가 죽는다 — 위의
    테스트들은 허용 이름을 품지 않는 이름만 써서 그 변이를 못 문다.
    """
    task = _task_dir(tmp_path, extras=(name,))

    with pytest.raises(task_loader.UnexpectedTaskFilesError) as refused:
        task_loader.load_task(task, tmp_path / "work")

    assert name in str(refused.value)
    assert not (tmp_path / "work").exists()


def _symlink_or_skip(link: Path, target: Path) -> None:
    """`link`를 `target`을 가리키는 심링크로 만든다. 못 만들면 그 환경에서는 입력이 성립하지
    않으므로 건너뛴다 — Windows는 권한(개발자 모드)이 없으면 `OSError`(WinError 1314)다."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"이 환경은 심링크를 못 만든다({type(exc).__name__})")


@pytest.mark.parametrize("kind", ["file", "directory", "dangling"])
def test_load_task_refuses_a_symlink_outside_the_allowlist_whatever_it_points_at(
    tmp_path: Path, kind: str
) -> None:
    """심링크도 **이름으로** 거부한다 — 가리키는 곳이 파일이든 디렉터리든 끊겨 있든.

    스캔을 `entry.is_file() and ...`로 좁히는 「일반 파일만 본다」 최적화는 디렉터리를 가리키거나
    끊어진 링크를 빠뜨린다 — `is_file()`이 링크를 따라가 거짓을 내기 때문이다. 앞의 디렉터리
    테스트는 진짜 디렉터리만 써서 그 변이를 못 문다. 모듈 docstring이 「디렉터리·점 파일·심링크는
    예외 없이 거부한다」고 적는다.
    """
    task = _task_dir(tmp_path)
    if kind == "file":
        target = tmp_path / "elsewhere.txt"
        target.write_bytes(_PLACEHOLDER)
    elif kind == "directory":
        target = tmp_path / "elsewhere-dir"
        target.mkdir()
    else:
        target = tmp_path / "does-not-exist"
    _symlink_or_skip(task / "stray-link", target)

    with pytest.raises(task_loader.UnexpectedTaskFilesError) as refused:
        task_loader.load_task(task, tmp_path / "work")

    assert "stray-link" in str(refused.value)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("a\nb", id="newline"),
        pytest.param("a\x1b[31mb", id="escape"),
        pytest.param("a\u2028b", id="unicode-line-separator"),
        pytest.param("a\x85b", id="next-line"),
    ],
)
def test_the_refusal_escapes_control_characters_in_a_file_name(tmp_path: Path, name: str) -> None:
    """파일명은 **`repr`로** 메시지에 넣는다 — 개행·ESC가 든 이름이 로그에 줄을 위조한다(리뷰가
    Linux에서 재현한 것). 메시지 전체가 출력 가능한 글자여야 하고, 이름은 이스케이프된 꼴로 든다.

    ⚠ NTFS는 개행·ESC를 이름에 못 쓴다 — 그 둘은 Windows에서 건너뛰고 CI의 Ubuntu에서 돈다. 줄 구분
    문자 둘(U+2028 · U+0085)은 NTFS에도 써서 어디서나 돈다.
    """
    task = _task_dir(tmp_path)
    try:
        (task / name).write_bytes(_PLACEHOLDER)
    except OSError:
        pytest.skip("이 파일시스템은 그 문자를 이름에 못 쓴다")

    with pytest.raises(task_loader.UnexpectedTaskFilesError) as refused:
        task_loader.check_task_dir(task)

    message = str(refused.value)
    assert message.isprintable(), f"메시지에 제어·줄 구분 문자가 새어 나왔다: {message!r}"
    assert repr(name) in message


def test_the_refusal_message_can_be_printed_on_a_cp949_console(tmp_path: Path) -> None:
    """운영자 콘솔은 cp949다 — `—`(U+2014)·`⚠`(U+26A0)는 그 stdout을 죽인다. 거부 문구는 찍히는
    문자열이라 그 글자를 쓰지 않는다(독스트링·주석은 안 찍히므로 쓴다)."""
    task = _task_dir(tmp_path, extras=("patch.diff",))

    with pytest.raises(task_loader.UnexpectedTaskFilesError) as refused:
        task_loader.check_task_dir(task)

    # 경로는 개발자 환경의 것이라 뺀다(사용자 이름의 글자가 cp949 밖일 수 있다) — 문구만 본다.
    str(refused.value).replace(repr(str(task)), "<task-dir>").encode("cp949")


@pytest.mark.parametrize("kind", ["missing", "a-file"])
def test_check_task_dir_does_not_read_an_unlistable_path_as_clean(
    tmp_path: Path, kind: str
) -> None:
    """목록을 **못 읽으면** 통과가 아니라 `OSError`다 — 검사를 못 한 것을 「깨끗하다」로 읽지
    않는다.

    `iterdir()`를 `try/except OSError: return`으로 감싸는 「친절한」 고침은 권한이 없거나 경로가
    어긋난 과제를 검사 없이 통과시킨다. CLI는 이 예외를 받아 exit 2로 거부한다
    (`tests/test_pipeline_e2e.py`)."""
    if kind == "missing":
        path = tmp_path / "no-such-task"
    else:
        path = tmp_path / "plain-file"
        path.write_bytes(_PLACEHOLDER)

    with pytest.raises(OSError):
        task_loader.check_task_dir(path)
