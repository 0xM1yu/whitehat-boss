"""CyberGym Level 1 과제 로더 (PoV 배선 — Phase 4).

Level 1 과제 디렉터리는 **정확히 두 파일**이다 — `repo-vul.tar.gz`(취약 저장소) +
`description.txt`(취약점 설명). 이 모듈은 그중 저장소를 **안전하게** 풀어
`preprocess.run`이 받을 `source_root`를 만들고, 설명 텍스트를 읽어 돌려준다.

**그 둘 밖의 항목은 전부 거부한다**(`check_task_dir` — 허용 목록 `TASK_FILES`). 저자 데이터셋은
과제 옆에 정답 자료를 같이 둔다 — 패치된 저장소 `repo-fix.tar.gz` · 수정 diff `patch.diff` ·
크래시 로그 `error.txt`(HF `sunblaze-ucb/cybergym`의 `data/<종류>/<id>/` 목록 실측, 2026-10-02 —
`arvo:1065`·`oss-fuzz:368076871` **두 과제만** 쟀고 둘 다 정확히 다섯 파일이다).
`data/<종류>/<id>/*`로 받으면 그것들이 과제 옆에 떨어지고, 그 디렉터리로 도는 run은 무효다.
차단 목록이 아니라 허용 목록인 이유가 그것이다 — 아는 정답 자료만이 아니라 **모르는 항목도** 막는다.
⚠ 저자 생성기(`gen_task`)의 산출은 위 둘에 `README.md`·`submit.sh`를 더한 네 파일이다(저자
`arvo_task.py`의 `prepare_arvo_files`, 커밋 `c6fe202`) — 이 로더는 그것도 거부한다. 생성기 산출
디렉터리를 그대로 넘기지 말고 두 파일만 놓은 디렉터리를 넘기거나, `TASK_FILES`를 의식해서 넓힌다.

**대상은 신뢰 못 하는 소스다.** tar 해제는 stdlib의 `data` 필터(PEP 706 — 3.11.4에서 들어왔다)로
경로 이탈(zip-slip)과 dest 밖을 가리키는(절대·외부) 링크·장치/특수 파일을 막고, 절대경로
멤버는 dest 안으로 눌러 담는다 — 손으로 링크·`..`를 훑는 것보다 정확하다. 그 위에 **크기·개수
상한**을 더해 디스크 폭탄을 막는다(`data` 필터는 해제 크기를 안 묶는다 — 작은 gz가 거대하게 풀린다).

**Python 하한은 3.11.4다**(`pyproject.toml`의 `requires-python`). 그 선언은 설치 도구(pip)가
설치할 때 읽는 메타데이터라 체크아웃에서 도는 하네스(`python -m pytest`·`python -m src.cli`)를
막지 못한다 — 그래서 그 아래 인터프리터(`filter=`가 없다)에서는 `safe_extract`가 풀기 **전에**
`RuntimeError`로 거부한다. 필터 없이 푸는 폴백은 없다.
⚠ 하한은 API가 생긴 판이지 필터에 결함이 없는 판이 아니다 — `data` 필터 자체의 링크 우회가
패치로 고쳐져 왔다(3.11 변경 기록: 3.11.13의 gh-135034 · 2026-09-30 조회 때 미출시 절의
gh-157190). 신뢰 못 하는 과제는 최신 패치 인터프리터로 푼다.
"""

import tarfile
from pathlib import Path

DESCRIPTION_FILENAME = "description.txt"
REPO_ARCHIVE_NAME = "repo-vul.tar.gz"

# 과제 디렉터리에 있어도 되는 **이름의 허용 목록**이다(모듈 docstring). 이 둘 밖의 항목은 종류를
# 가리지 않고 거부한다 — 일반 파일 · 디렉터리(`binaries/`) · 점 파일(`.DS_Store`·`.gitkeep`) ·
# 심링크 · 빈 디렉터리 전부. 예외를 두지 않는 이유: ① 점 파일·하위 디렉터리는 정답 자료를 숨기기
# 좋은 자리라, 이름으로 예외를 열면 그 이름이 구멍이다 ② 비대칭 — 거부는 지우면 끝나는 즉시·명시적
# 실패이고, 놓친 누출은 run 전체 무효가 소리 없이 온다 ③ 과제 디렉터리는 `eval/datasets/<과제>/`에
# 두고 git이 안 추적하므로(`.gitignore`의 `eval/datasets/*`) `.gitkeep`이 있을 이유도 없다.
# ⚠ **이름만 본다** — 허용된 이름 아래의 내용(심링크가 가리키는 곳 포함)은 안 본다. 이 가드는
# 데이터셋 glob이 정답 파일을 옆에 떨어뜨린 사고를 막지, 허용 이름 안에 정답을 넣어 둔 과제를
# 막지 못한다.
TASK_FILES = frozenset({DESCRIPTION_FILENAME, REPO_ARCHIVE_NAME})

# 안전 상한 — 실측이 아니라 신뢰 못 하는 입력의 자원 상한이다(PovSpec 8 MiB 상한과 같은 성격).
# `data` 필터는 해제 크기를 안 묶으므로, 작은 gz가 GB로 풀려 디스크를 채우는 것을 여기서 막는다.
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_MEMBERS = 100_000


class UnsafeArchiveError(Exception):
    """아카이브 멤버가 안전 필터에 걸렸거나(경로 이탈·dest 밖 링크·특수 파일) 크기·개수 상한을
    넘었다. 신뢰 못 하는 과제를 풀 때만 나며, 이 예외가 나면 해제를 중단한다."""


class UnexpectedTaskFilesError(Exception):
    """과제 디렉터리에 허용 목록(`TASK_FILES`) 밖 항목이 있다 — 정답 자료일 수 있어 그 과제로는
    run을 시작하지 않는다. 메시지가 걸린 이름을 전부 든다."""


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
    # 추출 필터가 없는 인터프리터(3.11.4 전)에서는 풀기 **전에**(dest도 만들기 전에) 멈춘다 —
    # 모듈 docstring. 가용성은 버전이 아니라 `data_filter` 유무로 본다(tarfile 문서가 권하는 판별).
    if not hasattr(tarfile, "data_filter"):
        raise RuntimeError(
            "safe_extract는 tarfile 추출 필터(PEP 706)가 필요하다 — Python 3.11.4 이상에서만 돈다"
        )
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


def check_task_dir(task_dir: Path) -> None:
    """`task_dir` 바로 아래에 `TASK_FILES` 밖 항목이 하나라도 있으면 `UnexpectedTaskFilesError`.

    정답 자료(`repo-fix.tar.gz`·`patch.diff`·`error.txt` — 모듈 docstring)가 과제 옆에 떨어진 채
    시작하는 run을 막는다. 걸린 이름을 **전부** 메시지에 든다. 하위 디렉터리 안은 보지 않는다 —
    디렉터리 자체가 허용 목록 밖이라 그 이름 하나로 이미 거부된다.
    """
    entries = Path(task_dir).iterdir()
    extras = sorted(entry.name for entry in entries if entry.name not in TASK_FILES)
    if extras:
        raise UnexpectedTaskFilesError(
            f"과제 디렉터리에 허용 목록 밖 항목이 있다: {', '.join(map(repr, extras))} — "
            f"허용되는 이름: {', '.join(map(repr, sorted(TASK_FILES)))}. 정답 자료가 섞였을 수 "
            f"있어 이 과제로는 시작하지 않는다(지우거나, 받을 때 허용된 파일만 받는다): {task_dir}"
        )


def load_task(task_dir: Path, work_dir: Path) -> tuple[Path, str | None]:
    """Level 1 과제 디렉터리를 `(source_root, description)`으로 편다.

    `repo-vul.tar.gz`를 `work_dir` 아래로 안전 해제한 것이 `source_root`이고, 이를
    `preprocess.run(source_root=...)`에 그대로 넘긴다. `description`은 캡슐의
    `vuln_description` 섹션이 된다(PoV 배선). 저장소 아카이브가 없으면 조용히
    빈 소스로 넘어가지 않고 `FileNotFoundError`로 멈춘다.

    디렉터리에 허용 목록 밖 항목이 있으면 **풀기 전에** `UnexpectedTaskFilesError`로 멈춘다
    (`check_task_dir`) — 거부한 과제는 `work_dir`에도 아무것도 남기지 않는다.
    """
    task_dir = Path(task_dir)
    archive = task_dir / REPO_ARCHIVE_NAME
    if not archive.is_file():
        raise FileNotFoundError(f"과제에 {REPO_ARCHIVE_NAME}가 없다: {task_dir}")
    # 존재 확인 **뒤**다 — 없는 경로·파일 경로가 받던 위의 `FileNotFoundError` 문구를 그대로 둔다.
    check_task_dir(task_dir)
    source_root = safe_extract(archive, Path(work_dir))
    return source_root, read_description(task_dir)
