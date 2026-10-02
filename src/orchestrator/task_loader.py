"""CyberGym Level 1 과제 로더 (PoV 배선 — Phase 4).

Level 1 과제는 `repo-vul.tar.gz`(취약 저장소) + `description.txt`(취약점 설명) +
`README.md` + `submit.sh`로 온다. 이 모듈은 그중 저장소를 **안전하게** 풀어
`preprocess.run`이 받을 `source_root`를 만들고, 설명 텍스트를 읽어 돌려준다.

**해제 전에 정답 자료를 거른다**(`check_no_ground_truth`). 데이터셋 원본 폴더는 고친 소스·
패치·크래시 로그를 `repo-vul.tar.gz` 옆에 담는데, Level 1은 그것을 안 준다 — 그게 해제·인덱싱
경로에 닿으면 모델이 정답을 보고 run이 무효가 된다(evaluation.md §4). 누출 스캐너
(`eval/leakage`)는 프롬프트·run 산출물만 보고 과제 입력 폴더는 안 보므로, 받는 이 자리에서 막는다.

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

# Level 1이 주지 않는 정답 자료. 과제 디렉터리에 이 중 하나라도 있으면 run을 거부한다
# (`check_no_ground_truth`). Level 1 입력은 취약 저장소 + 설명뿐인데, 데이터셋 원본 폴더는
# 고친 소스(`repo-fix.tar.gz`)·패치(`patch.diff`)·크래시 로그(`error.txt`)를 같이 담는다. 그것이
# 해제·인덱싱 경로에 닿으면 모델이 정답을 보고, 그 run의 결과는 무효다(evaluation.md §4). 누출
# 스캐너(`eval/leakage`)는 프롬프트·run 산출물만 봐서 **과제 입력 폴더는 안 본다** — 그래서 입력을
# 받는 이 자리에서 막는다. `repo-fix`로 시작하는 이름(압축 전 트리 포함)도 막는다.
_GROUND_TRUTH_NAMES = frozenset({"patch.diff", "error.txt"})
_GROUND_TRUTH_PREFIX = "repo-fix"

# 안전 상한 — 실측이 아니라 신뢰 못 하는 입력의 자원 상한이다(PovSpec 8 MiB 상한과 같은 성격).
# `data` 필터는 해제 크기를 안 묶으므로, 작은 gz가 GB로 풀려 디스크를 채우는 것을 여기서 막는다.
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_MEMBERS = 100_000


class GroundTruthLeakError(Exception):
    """과제 디렉터리에 Level 1이 주지 않는 정답 자료(패치·크래시 로그·고친 소스)가 있다. 그것이
    모델에 닿으면 run이 무효라(evaluation.md §4), 해제·인덱싱 **전에** 멈춘다."""


def check_no_ground_truth(task_dir: Path) -> None:
    """과제 디렉터리 최상위에 정답 자료가 섞여 있으면 `GroundTruthLeakError`로 거부한다.

    **최상위만 본다** — 정답 자료는 CyberGym 데이터셋 폴더의 형제 파일이고(`repo-vul.tar.gz`
    옆의 `patch.diff`·`error.txt`·`repo-fix.tar.gz`), 취약 저장소 **안**은 정상 입력이라 안 본다.
    `load_task`가 해제 전에 부른다 — 한 번 풀리면 인덱서·슬라이서가 그 트리를 본다. 이름 비교는
    대소문자를 구분하지 않는다(파일시스템차를 타지 않게).

    ⚠ **아는 정답 자료를 막는 차단 목록이다 — 「Level 1 입력 외 전부」를 막는 허용 목록이 아니다**
    (#397 리뷰 MEDIUM-2). 저자 생성기 `gen_task`의 네 파일(`repo-vul.tar.gz`·`description.txt`·
    `README.md`·`submit.sh`)은 의도적으로 통과한다. 그래서 이름이 다른 정답 자료(`crash.txt` 등)·
    하위 디렉터리 안의 정답 자료(`extra/patch.diff`)는 못 잡는다 — 목록 근거는 2026-10-02에 과제
    2건(arvo:1065·oss-fuzz:368076871)만 실측한 값이고, 1,507건 전체의 이름 분포는 미측정이다."""
    offenders = sorted(
        entry.name
        for entry in Path(task_dir).iterdir()
        if entry.name.lower() in _GROUND_TRUTH_NAMES
        or entry.name.lower().startswith(_GROUND_TRUTH_PREFIX)
    )
    if offenders:
        raise GroundTruthLeakError(
            f"과제에 정답 자료가 있다(Level 1은 주지 않는다): {', '.join(offenders)} — {task_dir}"
        )


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
    # 정답 자료 가드 — 존재 확인 **뒤**다(없는 경로·파일 경로가 받던 위의 `FileNotFoundError`
    # 문구를 OS 예외로 바꾸지 않게, #397 리뷰 MEDIUM-1) · 해제 **앞**이다(풀리면 인덱서·슬라이서가
    # 그 트리를 본다).
    check_no_ground_truth(task_dir)
    source_root = safe_extract(archive, Path(work_dir))
    return source_root, read_description(task_dir)
