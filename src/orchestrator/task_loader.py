"""CyberGym Level 1 과제 로더 (PoV 배선 — Phase 4).

Level 1 과제는 `repo-vul.tar.gz`(취약 저장소) + `description.txt`(취약점 설명) +
`README.md` + `submit.sh`로 온다. 이 모듈은 그중 저장소를 **안전하게** 풀어
`preprocess.run`이 받을 `source_root`를 만들고, 설명 텍스트를 읽어 돌려준다.

**대상은 신뢰 못 하는 소스다.** tar 해제는 stdlib의 `data` 필터(PEP 706, 3.11.4+ · 이 저장소는
3.14)로 경로 이탈(zip-slip)과 dest 밖을 가리키는(절대·외부) 링크·장치/특수 파일을 막고, 절대경로
멤버는 dest 안으로 눌러 담는다 — 손으로 링크·`..`를 훑는 것보다 정확하다. 그 위에 **크기·개수
상한**을 더해 디스크 폭탄을 막는다(`data` 필터는 해제 크기를 안 묶는다 — 작은 gz가 거대하게 풀린다).
"""

import tarfile
from pathlib import Path

DESCRIPTION_FILENAME = "description.txt"
REPO_ARCHIVE_NAME = "repo-vul.tar.gz"

# 안전 상한 — 실측이 아니라 신뢰 못 하는 입력의 자원 상한이다(PovSpec 8 MiB 상한과 같은 성격).
# `data` 필터는 해제 크기를 안 묶으므로, 작은 gz가 GB로 풀려 디스크를 채우는 것을 여기서 막는다.
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_MEMBERS = 100_000


class UnsafeArchiveError(Exception):
    """아카이브 멤버가 안전 필터에 걸렸거나(경로 이탈·dest 밖 링크·특수 파일) 크기·개수 상한을
    넘었다. 신뢰 못 하는 과제를 풀 때만 나며, 이 예외가 나면 해제를 중단한다."""


def safe_extract(tar_path: Path, dest: Path) -> Path:
    """`tar_path`를 `dest` 아래로 안전하게 풀고 그 루트를 돌려준다.

    멤버를 **하나씩** 풀며 누적 크기·개수를 센다 — 상한을 넘기는 멤버는 풀기 **전에** 멈춰
    디스크 폭탄이 자리를 다 먹기 전에 끊는다(한 번에 `extractall`하면 그 검사 지점이 없다).
    각 멤버는 stdlib `data` 필터가 판정한다 — 경로 이탈·dest 밖 링크·특수 파일을
    `tarfile.FilterError`로 막고, 절대경로는 dest 안으로 눌러 담는다. 두 사건을 다
    `UnsafeArchiveError`로 감싸 호출자가 「안전하지 않은 과제」를 한 사건으로 다루게 한다.

    ⚠ 상한을 넘겨 중단하면 그 앞 멤버들은 dest에 남는다 — 호출자는 새 `work_dir`을 쓴다
    (`load_task`가 그렇게 부른다). 같은 dest를 재사용하는 호출자는 남은 부분 파일을 물려받는다.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    count = 0
    try:
        with tarfile.open(tar_path, "r:*") as tar:
            for member in tar:
                count += 1
                if count > _MAX_MEMBERS:
                    raise UnsafeArchiveError(f"멤버 수가 상한을 넘는다(> {_MAX_MEMBERS})")
                total += max(member.size, 0)
                if total > _MAX_TOTAL_BYTES:
                    raise UnsafeArchiveError(
                        f"해제 크기가 상한을 넘는다(> {_MAX_TOTAL_BYTES} B) — 디스크 폭탄 방어"
                    )
                tar.extract(member, dest, filter="data")
    except tarfile.FilterError as exc:
        raise UnsafeArchiveError(f"안전하지 않은 아카이브 멤버: {exc}") from exc
    return dest


def read_description(task_dir: Path) -> str | None:
    """과제 디렉터리의 `description.txt`를 읽는다. 없으면 `None`(지어내지 않는다 — §0).

    설명은 사람이 읽는 텍스트라 디코딩 오류는 대체 문자로 넘긴다(바이트가 중요한 PoC와
    다르다) — 한 파일의 인코딩이 run 전체를 멈추게 하지 않는다.
    """
    path = Path(task_dir) / DESCRIPTION_FILENAME
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def load_task(task_dir: Path, work_dir: Path) -> tuple[Path, str | None]:
    """Level 1 과제 디렉터리를 `(source_root, description)`으로 편다.

    `repo-vul.tar.gz`를 `work_dir` 아래로 안전 해제한 것이 `source_root`이고, 이를
    `preprocess.run(source_root=...)`에 그대로 넘긴다. `description`은 캡슐의
    `vuln_description` 섹션이 된다(PoV 배선). 저장소 아카이브가 없으면 조용히
    빈 소스로 넘어가지 않고 `FileNotFoundError`로 멈춘다.
    """
    task_dir = Path(task_dir)
    archive = task_dir / REPO_ARCHIVE_NAME
    if not archive.is_file():
        raise FileNotFoundError(f"과제에 {REPO_ARCHIVE_NAME}가 없다: {task_dir}")
    source_root = safe_extract(archive, Path(work_dir))
    return source_root, read_description(task_dir)
