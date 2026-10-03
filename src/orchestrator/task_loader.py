"""CyberGym Level 1 과제 로더 (PoV 배선 — Phase 4).

Level 1 과제는 `repo-vul.tar.gz`(취약 저장소) + `description.txt`(취약점 설명) +
`README.md` + `submit.sh`로 온다. 이 모듈은 그중 저장소를 **안전하게** 푸는 `safe_extract`와
설명 텍스트를 읽는 `read_description`을 든다. 프로덕션은 `run --task-dir`(`src/cli.py`)이 그 둘을
**따로** 부르고, 둘을 묶은 `load_task`는 호출처가 테스트뿐이다.

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

import json
import re
import tarfile
from dataclasses import dataclass
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
    `tarfile.FilterError`로 막고, 절대경로는 dest 안으로 눌러 담는다. 이 두 사건과 **못 읽는
    아카이브**(비-gzip·끊긴 gz — `tarfile.TarError`·`EOFError`)를 다 `UnsafeArchiveError`로 감싸
    호출자가 「이 과제를 못 쓴다」를 한 사건으로 다루게 한다(사유는 문구로 가른다).

    ⚠ 상한을 넘기거나 중간에서 읽기가 죽어 중단하면 그 앞 멤버들은 dest에 남는다 — 호출자는
    **비어 있는** `work_dir`을 쓴다(`run --task-dir`이 비어 있는지 보고 거부한다 — `src/cli.py`.
    `load_task`는 보지 않고 받은 `work_dir`을 그대로 쓴다). 같은 dest를 재사용하는 호출자는 남은
    부분 파일을 물려받는다.
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
    except (tarfile.TarError, EOFError) as exc:
        # ⏵ 2026-10-02 — 넓혔다(적대적 리뷰 M-2). `FilterError`만 잡던 동안 **아카이브가 아예 못
        # 읽히는 경우**가 호출자까지 생 예외로 샜다: `tarfile.ReadError`(비-gzip — HuggingFace
        # **LFS 포인터**를 `curl -L` 없이 받으면 정확히 이 모양이다) · `EOFError`(중간에 끊긴 gz).
        # 둘 다 "이 과제를 못 쓴다"라는 **한 사건**이고 호출자가 그것만 알면 되므로 같이 감싼다.
        # `FilterError`는 `TarError`의 하위지만 **위에 두어** 사유 문구를 가른다(안전 위반 ≠ 손상).
        raise UnsafeArchiveError(f"아카이브를 읽을 수 없다({type(exc).__name__}): {exc}") from exc
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
    `preprocess.run(source_root=...)`에 넘기면 된다. `description`은 캡슐의
    `vuln_description` 섹션이 된다(PoV 배선). 저장소 아카이브가 없으면 조용히
    빈 소스로 넘어가지 않고 `FileNotFoundError`로 멈춘다.

    ⚠ **프로덕션 경로(`run --task-dir`)는 이 함수를 거치지 않고** `safe_extract`·
    `read_description`을 따로 부른다(설명을 해제보다 먼저 읽어야 해서다 — `src/cli.py`). 여기에만
    거는 검사는 그 경로를 못 막는다.
    """
    task_dir = Path(task_dir)
    archive = task_dir / REPO_ARCHIVE_NAME
    if not archive.is_file():
        raise FileNotFoundError(f"과제에 {REPO_ARCHIVE_NAME}가 없다: {task_dir}")
    source_root = safe_extract(archive, Path(work_dir))
    return source_root, read_description(task_dir)


SUBMIT_SCRIPT_NAME = "submit.sh"
# `submit.sh`가 드는 두 값을 뽑는 자리. 저자 생성기(`gen_task`)가 쓰는 모양이 고정이라
# (`curl -X POST {server}/submit-vul` + `-F 'metadata={...}'`) 그 두 패턴만 본다. 느슨하게 긁지
# 않는 것은 틀린 신원으로 제출하면 채점기가 400을 내고 그 run의 장부가 어긋나기 때문이다.
_SUBMIT_URL = re.compile(r"(https?://[^\s/]+)/submit-vul")
_SUBMIT_METADATA = re.compile(r"metadata=(\{.*?\})", re.DOTALL)


@dataclass(frozen=True)
class SubmitMetadata:
    """과제 번들이 들고 있는 채점기 신원 (`submit.sh`).

    **지어내지 않는다** — 서버 주소·과제 id·에이전트 id는 저자 생성기가 과제마다 발급한 값이고,
    `task_id`는 마스킹된 id(예: `9c73e92e52b7`)라 `arvo:1065` 같은 원 id와 다르다. 하네스가
    그것을 다시 만들 방법이 없으므로 번들에서 읽는다.
    """

    server: str
    task_id: str
    agent_id: str


def read_submit_metadata(task_dir: Path) -> SubmitMetadata | None:
    """`submit.sh`에서 채점기 신원을 읽는다. 파일이 없거나 두 패턴이 안 맞으면 `None`.

    `None`은 「이 번들로는 제출할 수 없다」이고 호출자가 그 사실을 사람에게 알린다 — 지어낸 신원
    으로 제출하지 않는다(§0).
    """
    path = Path(task_dir) / SUBMIT_SCRIPT_NAME
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    url, metadata = _SUBMIT_URL.search(text), _SUBMIT_METADATA.search(text)
    if url is None or metadata is None:
        return None
    try:
        parsed = json.loads(metadata.group(1))
        return SubmitMetadata(
            server=url.group(1), task_id=parsed["task_id"], agent_id=parsed["agent_id"]
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
