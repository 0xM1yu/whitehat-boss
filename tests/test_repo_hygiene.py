"""저장소 자체의 위생 검사.

여기 있는 것은 코드의 동작이 아니라 **작업 트리의 상태**에 대한 단언이다. 사람 눈으로
세 번 놓친 부류를 테스트로 옮기는 자리이고, 지금은 개행 하나가 들어 있다.

## 왜 개행이 테스트 대상인가

`.gitattributes`가 `* text=auto eol=lf`로 못박으므로 **체크아웃은 항상 LF**다. 그런데
Windows에서 파이썬이 텍스트 모드로 파일을 쓰면(`Path.write_text()`, `open(..., "w")`)
`\n`이 `\r\n`으로 번역된다. 그 결과가 이렇다:

- 작업 트리는 CRLF, index blob은 LF
- `git`이 비교할 때 정규화하므로 **`git status`는 clean**, `git diff`도 비어 있다
- 다음에 그 파일을 건드리는 사람에게 유령 `modified`가 뜬다

즉 조용히 어긋난 상태로 잠복한다. `core.autocrlf`는 이 저장소에서 `.gitattributes`에
밀려 무력하므로 원인이 아니다 — **파일을 쓴 쪽이 원인이다.**

## 이 검사가 실패하면

작업 트리를 git 체크아웃 형태로 되돌린다. 커밋은 생기지 않는다(내용이 이미 같다):

    rm -f <파일들> && git checkout -- <파일들>

`git checkout-index -f -a`는 stat 캐시가 최신이면 덮어쓰지 않아 이 상황에서는 듣지
않는다. **LF로 "고치려고" 파일을 새로 쓰면 같은 자리로 돌아온다** — 쓰는 쪽에서
`newline="\n"`을 명시하는 것이 유일한 예방이다.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# `git ls-files --eol`의 작업 트리 열. `w/none`은 바이너리라 개행 개념이 없다.
_DIRTY_WORKTREE_EOL = ("w/crlf", "w/mixed", "w/crlf-mixed")


def _eol_rows() -> list[tuple[str, str, str]]:
    """`(index 개행, 작업 트리 개행, 경로)`. git이 없으면 건너뛴다."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--eol"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # pragma: no cover - git 없는 환경
        pytest.skip("git을 실행할 수 없다")
    if result.returncode != 0:  # pragma: no cover - 저장소 밖
        pytest.skip("저장소가 아니다")

    rows = []
    for line in result.stdout.splitlines():
        # `i/lf<TAB>w/lf<TAB>attr/text=auto eol=lf<TAB>경로` — attr에 공백이 있으므로
        # 앞의 두 토큰만 떼고 나머지는 그대로 둔다.
        head, _, path = line.rpartition("\t")
        tokens = head.split()
        if len(tokens) < 2:
            continue
        rows.append((tokens[0], tokens[1], f"{' '.join(tokens[2:])} {path}"))
    return rows


def test_tracked_files_have_lf_in_the_working_tree() -> None:
    """`eol=lf`로 선언한 파일이 작업 트리에서 CRLF면 유령 `modified`가 잠복한다.

    `git status`가 clean이라 사람 눈에는 안 보이고, 다음에 그 파일을 만지는 사람이
    "내가 안 바꾼 파일이 바뀌었다"를 만난다. 원인은 git이 아니라 파이썬 텍스트 모드다.
    """
    offenders = [
        info
        for index_eol, worktree_eol, info in _eol_rows()
        if worktree_eol in _DIRTY_WORKTREE_EOL and "eol=lf" in info
    ]

    assert offenders == [], (
        "작업 트리에 CRLF가 섞인 추적 파일이 있다. 모듈 docstring의 복구 절차를 쓴다:\n"
        + "\n".join(offenders)
    )


# `config/models.yaml`의 `  <모델이름>:` 행. 모델 블록의 경계를 이것으로 잡는다.
_MODEL_HEADER = re.compile(r"^  ([A-Za-z0-9._-]+):\s*$")
_ISO_DAY = re.compile(r"20\d\d-\d\d-\d\d")


def _model_comment_blocks() -> dict[str, str]:
    """모델 이름 → 그 블록 안의 **주석 줄만** 이어붙인 문자열.

    값 줄을 섞으면 `measured_at: "2026-09-04T…"` 자신이 날짜로 잡혀 검사가 항상
    통과한다. 그래서 `#`로 시작하는 줄만 모은다.
    """
    raw = (REPO_ROOT / "config" / "models.yaml").read_text(encoding="utf-8")
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        header = _MODEL_HEADER.match(line)
        if header:
            current = header.group(1)
            blocks[current] = []
            continue
        if current is not None and line.lstrip().startswith("#"):
            blocks[current].append(line)
    return {name: "\n".join(lines) for name, lines in blocks.items()}


def test_a_measured_benchmark_names_its_measurement_date_in_the_comment() -> None:
    """`benchmark`를 채우면서 그 근거 주석을 안 고치면 값과 주석이 정면 모순한다.

    CLAUDE.md §2.3이 사고로 기록한 부류다 — *"`output_max`를 8,192에서 4,096으로
    내리면서 「8,192는 정상 경로에서 닿지 않는다」고 **현재형으로 추론하는** docstring을
    안 고쳤다. 값이 움직였는데 그 근거만 남았다."*

    `models.yaml`에서 같은 일이 나면 더 나쁘다. 이 저장소의 산출물은 판정이고,
    "이 모델은 안 쟀다"고 적힌 주석 밑에 실측값이 앉아 있으면 **읽는 사람이 어느
    쪽을 믿어야 하는지 알 수 없다.** §0이 "수치를 쓸 때는 어디서 나왔는지를 같은
    문장에 적는다"로 요구하는 것이 이것이다.

    그래서 기계로 고정한다: `measured_at`이 있으면 **그 날짜가 그 모델의 주석 안에**
    있어야 한다. 날짜를 적으려면 주석을 읽게 되고, 읽으면 모순이 눈에 띈다.

    이 검사는 문장의 옳음을 검증하지 않는다 — 날짜의 존재만 본다. 그것으로 "값만
    갈아끼우고 지나가는" 경로를 막는 것이 목적이다.
    """
    models = yaml.safe_load(
        (REPO_ROOT / "config" / "models.yaml").read_text(encoding="utf-8")
    )["models"]
    comments = _model_comment_blocks()

    offenders = []
    for name, spec in models.items():
        measured_at = (spec.get("benchmark") or {}).get("measured_at")
        if not measured_at:
            continue  # 미실측 — §0의 0/null 표시다. 검사 대상이 아니다.
        day = str(measured_at)[:10]
        body = comments.get(name, "")
        if day not in body:
            found = sorted(set(_ISO_DAY.findall(body))) or ["(주석에 날짜 없음)"]
            offenders.append(f"  {name}: measured_at={day} 인데 주석의 날짜는 {found}")

    assert offenders == [], (
        "benchmark 값이 움직였는데 그 근거 주석이 따라오지 않았다 (CLAUDE.md §0 · §2.3).\n"
        "해당 모델의 주석에 이번 측정이 언제/어디서 나왔는지를 적는다:\n"
        + "\n".join(offenders)
    )


# 「프로필 N개」와 「N개 프로필」 두 어순이 다 쓰인다. 예시를 자릿수로 적으면
# 이 줄 자신이 검사에 걸리므로 적지 않는다 — 아래 정규식이 곧 명세다.
_PROFILE_COUNT = re.compile(r"프로필 (\d+)개|(\d+)개 프로필")


def test_source_prose_does_not_carry_a_stale_profile_count() -> None:
    """프로필을 추가하면 개수를 세던 주석이 **전부** 거짓이 된다.

    사람이 `git grep`으로 훑는 것이 CLAUDE.md §2.3이 요구하는 절차인데, #138에서
    그 훑기가 새지 않은 자리를 남겼다 — 프로필 하나를 더하고 넷을 고쳤는데
    `runner.py`·`generation_config.py`·`test_pipeline_e2e.py`가 "12개"인 채로 남았다.
    세 곳 다 **현재형**이라 다음 사람이 그대로 믿는다.

    개수는 기계가 셀 수 있다. 그래서 센다.

    **`src/`와 `tests/`만 본다.** `docs/open-questions/D/` 아래는 "2026-08-29 ATOM
    실측, 줄번호는 origin/main a688e0f 기준" 같은 **날짜가 박힌 기록**이라 당시의
    개수를 적은 것이 맞다 — 그것을 지금 값으로 고치면 기록이 거짓이 된다.
    산 코드와 산 테스트의 서술만 현재를 말해야 한다.

    한글 수사(「실서빙 일곱」 등)는 이 검사가 못 잡는다. 자릿수 표기만 본다.
    """
    profile_count = len(
        yaml.safe_load(
            (REPO_ROOT / "config" / "profiles.yaml").read_text(encoding="utf-8")
        )["profiles"]
    )

    offenders = []
    for directory in ("src", "tests"):
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                for match in _PROFILE_COUNT.finditer(line):
                    claimed = int(match.group(1) or match.group(2))
                    if claimed != profile_count:
                        rel = path.relative_to(REPO_ROOT).as_posix()
                        offenders.append(f"  {rel}:{lineno} — {claimed}개라고 적혀 있다")

    assert offenders == [], (
        f"`config/profiles.yaml`의 프로필은 {profile_count}개인데 서술이 따라오지 "
        "않았다 (CLAUDE.md §2.3).\n" + "\n".join(offenders)
    )
