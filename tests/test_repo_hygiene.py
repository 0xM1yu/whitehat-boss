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

import subprocess
from pathlib import Path

import pytest

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
