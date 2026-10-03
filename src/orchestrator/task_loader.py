"""CyberGym Level 1 과제 로더 (PoV 배선 — Phase 4).

Level 1 과제는 데이터셋 기준으로 `repo-vul.tar.gz`(취약 저장소) + `description.txt`(취약점 설명)
**두 파일**이 계약이고, 저자 생성기(`gen_task`)가 내놓는 번들은 거기에 `README.md`·`submit.sh`를
더한 네 파일이다. 이 모듈은 그중 저장소를 **안전하게** 푸는 `safe_extract`와 설명 텍스트를 읽는
`read_description`을 든다. 프로덕션은 `run --task-dir`(`src/cli.py`)이 그 둘을 **따로** 부르고(그
앞에 `check_task_dir`를 건다), 둘을 묶은 `load_task`는 호출처가 테스트뿐이다.

**그 넷 밖의 항목은 전부 거부한다**(`check_task_dir` — 허용 목록 `TASK_FILES`). 저자 데이터셋은 과제
옆에 정답 자료를 같이 둔다 — 패치된 저장소 `repo-fix.tar.gz` · 수정 diff `patch.diff` · 크래시 로그
`error.txt`. HF `sunblaze-ucb/cybergym` 리비전 `bde190d`의 `data/<종류>/<id>/` 목록을 1,507개 폴더
전부에서 읽었다(2026-10-03): 전부 정확히 같은 다섯 파일이고 중첩은 0건이며, `tasks.json`은 Level 1
계약을 `{repo-vul.tar.gz, description.txt}`로 1,507건 중 1,507건에 적는다. `data/<종류>/<id>/*`로
받으면 그 다섯이 다 과제 옆에 떨어지고, 그 디렉터리로 도는 run은 무효다. 차단 목록이 아니라 허용
목록인 이유가 그것이다 — 아는 정답 자료만이 아니라 **모르는 항목도** 막는다. 저자 생성기
`ARVO_FILES`가 선언한 참조 PoC `poc`와 `binaries/`(취약·패치 바이너리)도 같은 이유로 목록 밖이다 —
데이터셋 폴더에는 하나도 없었다.
⚠ **이 숫자는 이름이다** — 폴더 목록과 `tasks.json`만 읽었고 아카이브·`description.txt`의 **내용**은
열지 않았다. 내용에 대한 주장은 이 실측으로 늘지 않았다 — 열어 본 것은 여전히 두 과제(`arvo:1065`·
`oss-fuzz:368076871`)뿐이다.

**왜 넷이고 그 이상이 아닌가.** 저자 생성기의 Level 1 산출은 위 둘에 `README.md`·`submit.sh`를 더한
정확히 네 파일이다(저자 `arvo_task.py`의 `prepare_arvo_files`, 커밋 `c6fe202` — ARVO·oss-fuzz
생성기가 같은 함수를 부른다. 2026-10-03에 그 소스를 다시 읽었다). `submit.sh`는 **채점기 신원의
유일한 출처**다 — 서버 주소 · `task_id` · `agent_id`가 거기 있고, 생성기를 마스크 맵
(`--mask-map`)으로 돌렸으면 `task_id`는 마스킹된 값이라 하네스가 다시 만들 수 없다. 그것을 읽는 쪽이
`--oracle-submit`(#408)의 `read_submit_metadata`이고, 그 함수는 `--task-dir` 디렉터리 안의
`submit.sh`를 연다. 허용 목록이 둘뿐이던 판은 **채점할 수 있는 번들을 전부 거부했다** — 두 PR이 서로
다른 곳에 있어 CI가 못 잡는 결함이다. `README.md`는 생성기가 `submit.sh`와 함께 쓰는 안내문이라
넣는다(읽는 코드는 없다 — 빼면 생성기 산출을 그대로 못 넘길 뿐이다). 정답 자료 이름은 하나도 넣지
않는다: `patch.diff` · `error.txt` · `repo-fix.tar.gz` · `poc` · `binaries/`.
⚠ **이름만 본다** — 허용된 이름 **안**에 정답을 넣어 둔 과제나 심링크가 가리키는 곳은 못 본다. 이
가드는 데이터셋 glob이 정답 파일을 옆에 떨어뜨린 사고를 막지, 허용 이름 안에 정답을 숨긴 과제를 막지
못한다. 넷으로 넓히면서 이름 뒤에 숨길 자리도 둘(`README.md`·`submit.sh`) 늘었다.

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
README_FILENAME = "README.md"
SUBMIT_SCRIPT_NAME = "submit.sh"

# 과제 디렉터리에 있어도 되는 **이름의 허용 목록**이다(모듈 docstring) — 저자 생성기의 Level 1 산출
# 네 파일이고 그 이상은 없다. 이 넷 밖의 항목은 종류를 가리지 않고 거부한다 — 일반 파일 ·
# 디렉터리(`binaries/`) · 점 파일(`.DS_Store`·`.gitkeep`) · 심링크 · 빈 디렉터리 전부. 이름은
# **정확히 같아야** 한다 — 대소문자를 접지 않고 접두·부분 문자열도 아니다(CI는 대소문자를 구분하는
# Ubuntu이고 작성자는 구분하지 않는 Windows라, 접는 비교는 한쪽에서만 참인 가정이 된다). 예외를 두지
# 않는 이유: ① 점 파일·하위 디렉터리는 정답 자료를 숨기기 좋은 자리라, 이름으로 예외를 열면 그
# 이름이 구멍이다 ② 비대칭 — 거부는 지우면 끝나는 즉시·명시적 실패이고, 놓친 누출은 run 전체 무효가
# 소리 없이 온다 ③ 과제 디렉터리는 `eval/datasets/<과제>/`에 두고 git이 안 추적하므로(`.gitignore`의
# `eval/datasets/*`) `.gitkeep`이 있을 이유도 없다.
# ⚠ **이름만 본다** — 허용된 이름 아래의 내용(심링크가 가리키는 곳 포함)은 안 본다. 이 가드는
# 데이터셋 glob이 정답 파일을 옆에 떨어뜨린 사고를 막지, 허용 이름 안에 정답을 넣어 둔 과제를
# 막지 못한다.
TASK_FILES = frozenset(
    {DESCRIPTION_FILENAME, REPO_ARCHIVE_NAME, README_FILENAME, SUBMIT_SCRIPT_NAME}
)

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


def check_task_dir(task_dir: Path) -> None:
    """`task_dir` 바로 아래에 `TASK_FILES` 밖 항목이 하나라도 있으면 `UnexpectedTaskFilesError`.

    정답 자료(`repo-fix.tar.gz`·`patch.diff`·`error.txt` — 모듈 docstring)가 과제 옆에 떨어진 채
    시작하는 run을 막는다. 걸린 이름을 **전부** 메시지에 든다. 하위 디렉터리 안은 보지 않는다 —
    디렉터리 자체가 허용 목록 밖이라 그 이름 하나로 이미 거부된다.

    메시지는 콘솔에 **그대로 찍힌다** — 그래서 ① 파일명은 `repr`로 넣는다(개행·ESC가 든 이름이
    로그 줄을 위조한다) ② 운영자 콘솔(cp949)이 못 찍는 글자를 쓰지 않는다 ③ 파일 **내용**은 읽지도
    되풀이하지도 않는다. 목록을 못 읽으면(권한) `OSError`가 그대로 올라온다 — 검사를 못 한 것을
    통과로 읽지 않는다.
    """
    entries = Path(task_dir).iterdir()
    extras = sorted(entry.name for entry in entries if entry.name not in TASK_FILES)
    if extras:
        raise UnexpectedTaskFilesError(
            f"과제 디렉터리에 허용 목록 밖 항목이 있다: {', '.join(map(repr, extras))}. "
            f"허용되는 이름: {', '.join(map(repr, sorted(TASK_FILES)))}. 정답 자료가 섞였을 수 "
            f"있어 이 과제로는 시작하지 않는다(지우거나, 받을 때 허용된 파일만 받는다): "
            f"{str(task_dir)!r}"
        )


def load_task(task_dir: Path, work_dir: Path) -> tuple[Path, str | None]:
    """Level 1 과제 디렉터리를 `(source_root, description)`으로 편다.

    `repo-vul.tar.gz`를 `work_dir` 아래로 안전 해제한 것이 `source_root`이고, 이를
    `preprocess.run(source_root=...)`에 넘기면 된다. `description`은 캡슐의
    `vuln_description` 섹션이 된다(PoV 배선). 저장소 아카이브가 없으면 조용히
    빈 소스로 넘어가지 않고 `FileNotFoundError`로 멈춘다.

    디렉터리에 허용 목록 밖 항목이 있으면 **풀기 전에** `UnexpectedTaskFilesError`로 멈춘다
    (`check_task_dir`) — 거부한 과제는 `work_dir`에도 아무것도 남기지 않는다.

    ⚠ **프로덕션 경로(`run --task-dir`)는 이 함수를 거치지 않고** `safe_extract`·
    `read_description`을 따로 부른다(설명을 해제보다 먼저 읽어야 해서다 — `src/cli.py`). 여기에만
    거는 검사는 그 경로를 못 막는다 — 그래서 `check_task_dir`도 그 분기에서 따로 부른다.
    """
    task_dir = Path(task_dir)
    archive = task_dir / REPO_ARCHIVE_NAME
    if not archive.is_file():
        raise FileNotFoundError(f"과제에 {REPO_ARCHIVE_NAME}가 없다: {task_dir}")
    # 존재 확인 **뒤**다 — 없는 경로·파일 경로가 받던 위의 `FileNotFoundError` 문구를 그대로 둔다.
    check_task_dir(task_dir)
    source_root = safe_extract(archive, Path(work_dir))
    return source_root, read_description(task_dir)


# `submit.sh`가 드는 두 값을 뽑는 자리. 저자 생성기(`gen_task`)가 쓰는 모양이 고정이라
# (`curl -X POST {server}/submit-vul` + `-F 'metadata={...}'`) 그 두 패턴만 본다. 느슨하게 긁지
# 않는 것은 틀린 신원으로 제출하면 채점기가 400을 내고 그 run의 장부가 어긋나기 때문이다.
_SUBMIT_URL = re.compile(r"(https?://[^\s/]+)/submit-vul")
_SUBMIT_METADATA = re.compile(r"metadata=(\{.*?\})", re.DOTALL)
# 이 셋은 비어 있지 않은 문자열이어야 한다 — 숫자·`null`·빈 문자열은 신원이 아니다.
_METADATA_FIELDS = ("task_id", "agent_id", "checksum")


@dataclass(frozen=True)
class SubmitMetadata:
    """과제 번들이 들고 있는 채점기 신원 (`submit.sh`).

    **지어내지 않는다** — 과제 id·에이전트 id·체크섬은 저자 생성기가 과제마다 발급한 값이고 하네스가
    그것을 다시 만들 방법이 없으므로(마스킹 서버가 낸 id는 맵 없이는 되돌릴 수 없다) 번들에서
    읽는다. `task_id`가 마스킹된 id(예: `9c73e92e52b7`)인지 실제 id(`arvo:1065`)인지는 번들을
    만든 서버 설정이 정한다 — 마스킹된 id는 조작자가 `--mask-map`으로 실 id를 줄 때만 받고 아니면
    제출 전에 거절한다(`cybergym_oracle.unsupported_task_id`·`TaskOracle.query_task_id`).

    **`server`는 어디로 보내는 데 쓰지 않는다.** 이 모듈의 첫 문장이 말하듯 과제 번들은 신뢰 못 하는
    소스이고, 번들이 가리키는 주소로 조작자의 API 키를 보내면 번들을 만든 쪽이 키를 받는다. 채점기
    주소는 조작자가 `--oracle-server`로 주고 이 값은 그것과 견주는 데만 쓴다(`cli._oracle_for`).

    `checksum`은 번들이 적은 값이다 — 클라이언트는 자기 salt로 다시 계산해
    제출하므로(`cybergym_oracle`), 번들이 다른 salt로 만들어졌는지를 제출 전에 가르는 데 쓴다.
    """

    server: str
    task_id: str
    agent_id: str
    checksum: str


def read_submit_metadata(task_dir: Path) -> SubmitMetadata | None:
    """`submit.sh`에서 채점기 신원을 읽는다. 파일이 없거나 패턴·값 형이 안 맞으면 `None`.

    `None`은 「이 번들로는 제출할 수 없다」이고 호출자가 그 사실을 사람에게 알린다 — 지어낸 신원
    으로 제출하지 않는다(§0). 값 형도 본다: `task_id`·`agent_id`·`checksum`이 비어 있지 않은
    문자열이 아니면(숫자·`null`·객체·빈 문자열) 읽은 것이 아니다.
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
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    values = [parsed.get(name) for name in _METADATA_FIELDS]
    if not all(isinstance(value, str) and value for value in values):
        return None
    task_id, agent_id, checksum = values
    return SubmitMetadata(
        server=url.group(1), task_id=task_id, agent_id=agent_id, checksum=checksum
    )
