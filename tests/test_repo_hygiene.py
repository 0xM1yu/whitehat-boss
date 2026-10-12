"""저장소 자체의 위생 검사.

여기 있는 것은 코드의 동작이 아니라 **작업 트리의 상태**에 대한 단언이다. 사람 눈으로
세 번 놓친 부류를 테스트로 옮기는 자리다. **개수는 여기서 세지 않는다** — 그 문장이
낡는 것이 이 파일이 막으려는 바로 그 종류의 어긋남이다.

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

import importlib
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.specifiers import SpecifierSet

from tests.conftest import tracked_files

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


def test_no_tracked_text_file_starts_with_a_utf8_bom() -> None:
    """UTF-8 BOM(`EF BB BF`)이 소스 첫머리에 붙으면 **아무 도구도 안 잡는다.**

    ## 무슨 일이 났나

    #195 리뷰(적대적, HIGH-1)가 `factory.py`·`runner.py`·`enums.py`·`trace.py` 넷에
    BOM이 새로 든 것을 짚었다 — Windows에서 PowerShell·일부 편집기가 파일을 쓰며
    앞에 BOM을 붙인다. `git diff`에는 **docstring 첫 줄이 통째로 바뀐 것처럼** 보이는데
    (앞 3바이트만 달라 `-` 줄과 `+` 줄이 같은 텍스트다) 내용은 한 글자도 안 바뀌었다.

    ## 왜 사람도 CI도 못 잡나

    `ruff`는 BOM 파일을 `All checks passed`로 통과시키고, 파이썬 인터프리터도 BOM을
    조용히 먹으므로 **1300개 테스트가 전부 통과한다.** `git status`도 clean이다 —
    CLAUDE.md §5의 *"줄바꿈이 이상한데 → CRLF는 `git status`에 안 보인다"*와 같은
    부류이고, 이번엔 BOM이다. 위 개행 검사와 짝을 이룬다.

    ## 이 검사가 보는 것

    추적되는 텍스트 파일의 **첫 3바이트**만 본다. 바이너리(`git ls-files --eol`의
    `w/none`)는 제외한다 — 개행 개념이 없듯 BOM 개념도 다르다. 복구는 앞 3바이트를
    떼는 것뿐이고 내용은 안 바뀐다(커밋 diff가 그 한 줄만 된다).
    """
    binary = {
        info.rsplit(" ", 1)[-1]
        for index_eol, worktree_eol, info in _eol_rows()
        if worktree_eol == "w/none"
    }
    try:
        listed = subprocess.run(  # noqa: S603
            ["git", "ls-files"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
    except OSError:  # pragma: no cover - git 없는 환경
        pytest.skip("git을 실행할 수 없다")
    if listed.returncode != 0:  # pragma: no cover - 저장소 밖
        pytest.skip("저장소가 아니다")

    offenders = []
    for rel in listed.stdout.split():
        if rel in binary:
            continue
        path = REPO_ROOT / rel
        try:
            head = path.read_bytes()[:3]
        except OSError:  # pragma: no cover - 심링크 등
            continue
        if head == b"\xef\xbb\xbf":
            offenders.append(f"  {rel}")

    assert offenders == [], (
        "추적 텍스트 파일 첫머리에 UTF-8 BOM이 있다 — ruff·pytest 어느 것도 안 잡는다 "
        "(CLAUDE.md §5). 앞 3바이트(EF BB BF)만 떼면 된다:\n" + "\n".join(offenders)
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

    **되도록 개수를 적지 않는 편이 낫다.** #139가 같은 주석들에서 숫자를 걷어내며 적은
    대로 *"프로필이 늘고 줄 때마다 이 주석이 틀린다"*. 실제로 #138과 #139를 §4.1대로
    순차 머지해 보면 프로필이 열셋에서 아홉이 되므로, **지금 맞는 숫자도 그때 틀린다** —
    이 검사가 그 상태를 잡아서 알았다. 그러니 이 검사가 지키는 것은 "적지 마라"가
    아니라 **"그래도 적었으면 맞아야 한다"**이다.

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


def test_pull_request_workflows_do_not_limit_the_base_branch() -> None:
    """`pull_request` 워크플로가 base 브랜치로 발화를 제한하면 **스택 PR이 검증을 못 받는다.**

    ## 무슨 일이 났나

    `ci.yml`이 `on.pull_request.branches: [main]`이었다. base가 main이 아닌 PR —
    이 저장소가 `CLAUDE.md` §4.3에서 정면으로 다루는 **스택 PR** — 은 `check` 잡
    (ruff·compileall·pytest·leakage)이 **한 번도 안 돌았다.**

    크기를 실측했다: 전체 PR 107건 중 base ≠ main이 5건(#29·#30·#42·#43·#161)이고,
    **스택 상태에서 `check`를 받은 PR은 0건**이다. 그중 셋(#29·#30·#42)은 **CI 없이
    머지됐다.** `push` 쪽도 같이 막혀 있어서(`push.branches: [main]`) 브랜치를 밀어도
    안 돈다 — CI 워크플로의 push run 103건이 전부 `main`이었다.

    같은 저장소의 다른 워크플로 둘(`pr-require-assignee`·`pr-auto-assign-author`)은
    `branches` 필터가 없어 스택 PR에서도 정상 발화한다. 그래서 #161에는 그 둘만
    SUCCESS로 찍히고 `check`가 아예 없었다 — **초록불이 아니라 무(無)였다.**

    ## 왜 사람이 못 잡나

    체크가 **실패하는 것이 아니라 존재하지 않는다.** PR 화면에 빨간 줄이 없으므로
    통과한 것과 구별되지 않는다. `CLAUDE.md` §5의 *"테스트가 통과했으니 맞겠지"*와
    같은 부류이고, 그보다 나쁘다 — 돌지도 않았다.

    ## 함정: YAML 1.1이 `on`을 불리언으로 읽는다

    `yaml.safe_load`가 `on:` 키를 문자열 `"on"`이 아니라 **`True`**로 준다. `doc["on"]`만
    보면 트리거를 못 찾아 **모든 워크플로가 조용히 통과한다** — 검사가 아무것도 안 막게
    된다. 그래서 둘 다 본다.
    """
    root = Path(__file__).resolve().parents[1]
    listed = subprocess.run(  # noqa: S603
        ["git", "ls-files", ".github/workflows"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert listed, "git ls-files가 워크플로를 하나도 안 냈다 — 검사가 아무것도 안 본다"

    offenders: list[str] = []
    saw_pull_request = False
    for rel in listed:
        doc = yaml.safe_load((root / rel).read_text(encoding="utf-8"))
        # `on:`은 YAML 1.1에서 `True`다. 둘 다 받는다 — 위 docstring의 함정.
        triggers = doc.get(True, doc.get("on")) if isinstance(doc, dict) else None
        if not isinstance(triggers, dict) or "pull_request" not in triggers:
            continue
        saw_pull_request = True
        spec = triggers["pull_request"]
        if not isinstance(spec, dict):
            continue
        limits = sorted(k for k in ("branches", "branches-ignore") if k in spec)
        if limits:
            offenders.append(f"{rel}: pull_request가 {limits}로 base를 거른다 → {spec[limits[0]]}")

    assert saw_pull_request, "`pull_request` 트리거를 가진 워크플로가 없다 — 검사가 헛돈다"
    assert offenders == [], (
        "base 브랜치를 거르는 pull_request 워크플로가 있다. 스택 PR(base ≠ main)이 그 잡을 "
        "**아예 못 받고**, 체크가 없는 것은 실패와 달리 화면에서 통과와 구별되지 않는다:\n"
        + "\n".join(offenders)
    )


# 평가셋 자리에 추적해도 되는 것. 이 디렉터리가 무엇을 담는지를 드는 문서뿐이다.
_DATASET_TRACKED = frozenset({"eval/datasets/.gitkeep", "eval/datasets/README.md"})


def test_no_evaluation_data_is_tracked() -> None:
    """`eval/datasets/`에는 **실데이터가 안 들어간다.**

    ## 왜 테스트인가

    `eval/datasets/README.md`가 이미 *"실제 데이터는 커밋하지 않는다"*고 적는다. 그런데
    그 문장은 **산문에만 있었고 아무것도 강제하지 않았다** — 다음에 `git add`하는 사람은
    README를 안 읽는다. 이 저장소는 정확히 그 자리에서 두 번 밟았다(`53e977b`가 되돌린
    `_workspace/` 30개 파일, PR #31에 딸려 간 `.claude/` md 4건). 둘 다 무시 규칙이
    **없어서** 났고, 둘 다 되돌리는 커밋이 따로 필요했다.

    ⚠ **`problem_names.txt`는 특히 그렇다** — 그 파일은 누출 검사가 **찾는 문자열 그
    자체**다(`eval/leakage/__main__.py`의 `DEFAULT_PROBLEM_NAMES`). 커밋하면 정답지를
    저장소가 갖게 되고, 그 뒤의 누출 검사 결과는 의미가 없다.

    ## 이 검사가 보는 것 — 둘이다

    ① **지금 추적되는 것**이 허용 목록뿐인가. `.gitignore`는 이미 추적 중인 파일을 못
    막으므로 `git add -f`로 들어온 것은 규칙이 아니라 이 단언이 잡는다.
    ② **새 데이터 파일이 실제로 무시되는가.** 규칙이 지워지거나 부정 패턴이 잘못 넓어지면
    ①은 한동안 초록인 채로 있다 — 그래서 양성·음성을 둘 다 묻는다. `--no-index`라
    **없는 경로도 물을 수 있어** 이 테스트는 파일을 만들지 않는다.
    """
    root = Path(__file__).resolve().parents[1]
    listed = set(
        subprocess.run(  # noqa: S603
            ["git", "ls-files", "eval/datasets"],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout.split()
    )
    assert listed == _DATASET_TRACKED, (
        f"`eval/datasets/` 추적 목록이 허용 목록과 다르다.\n"
        f"  추적됨: {sorted(listed)}\n"
        f"  허용:   {sorted(_DATASET_TRACKED)}\n"
        "실데이터가 들어왔으면 `git rm --cached`로 뺀다. 문서를 새로 추가한 것이라면 "
        "`_DATASET_TRACKED`를 같이 넓힌다 — 넓히는 것이 판단이라는 뜻이다."
    )

    def ignored(rel: str) -> bool:
        return (
            subprocess.run(  # noqa: S603
                ["git", "check-ignore", "--no-index", "-q", rel],
                cwd=root, capture_output=True, text=True, check=False,
            ).returncode
            == 0
        )

    # 양성 핀 — 실데이터가 들어올 실제 이름이다(README가 그 파일명을 든다).
    assert ignored("eval/datasets/problem_names.txt"), (
        "`eval/datasets/` 무시 규칙이 죽었다 — 위 단언은 한동안 초록인 채로 있는다."
    )
    # 음성 핀 — 부정 패턴이 살아 있어야 README가 계속 추적된다. 이것이 무시되면
    # 규칙이 자기 사유를 설명하는 문서까지 같이 가린다.
    assert not ignored("eval/datasets/README.md"), (
        "`!eval/datasets/README.md` 부정 패턴이 안 듣는다 — 규칙의 사유가 사라진다."
    )


def _gitignore_blocks() -> list[tuple[str, list[str]]]:
    """`.gitignore`를 `(주석 본문, 패턴 목록)` 덩어리로 자른다.

    빈 줄이 덩어리를 가르고, 패턴 줄 뒤에 주석이 다시 나오면 거기서도 갈린다.
    """
    raw = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    blocks: list[tuple[str, list[str]]] = []
    comments: list[str] = []
    patterns: list[str] = []

    def flush() -> None:
        nonlocal comments, patterns
        if patterns:
            blocks.append(("\n".join(comments), patterns))
        comments, patterns = [], []

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("#"):
            if patterns:
                flush()
            comments.append(stripped)
        else:
            patterns.append(stripped)
    flush()
    return blocks


def test_the_workspace_ignore_rule_names_itself_in_its_own_comment() -> None:
    """`_workspace/`가 **남의 사유 주석 밑에** 앉으면 왜 무시하는지가 사라진다.

    ## 무슨 일이 났나

    PR #116이 `_workspace/` 한 줄을 넣으면서 `PR4-plan.md`의 블록에 얹었다. 그 블록의
    주석은 *"작업 계획 문서 (PR 산출물이 아니다)"*인데 `_workspace/`는 작업 계획 문서가
    아니라 **스킬 스크래치 산출물**이 날짜별 폴더로 쌓이는 자리다. 그래서 이 파일에는
    `_workspace/`가 왜 있는지가 **한 글자도 없다.**

    #116 본문이 이것을 스스로 「남겨 둔 것」에 적었다 — *"행에 주석이 없다. 이 파일의
    다른 항목은 전부 사유 주석을 달고 있다."* 리뷰 코멘트가 **0건**이라 그대로 머지됐다.

    ## 왜 테스트인가

    무시 **동작**은 맞다 — `git check-ignore -v _workspace/`가 이 줄을 짚는다. 그래서
    아무 검사도 안 깨지고, 사람 눈에도 diff 한 줄은 정상으로 보인다. 비용은 다음에
    이 줄을 지울지 판단하는 사람이 **왜 있는지 모른 채** 정하는 것이고, 그때는 이미
    `53e977b`(*"잘못 커밋된 `_workspace/` 30개 파일을 되돌린다"*)가 반복된 뒤다.

    CLAUDE.md §2.3이 사고로 기록한 「값이 움직였는데 그 근거만 남았다」의 **거울상**이다 —
    여기서는 값만 있고 근거가 없다.

    ## 이 검사가 보는 것

    `_workspace/`가 든 블록의 **주석이 그것을 이름으로 부르는지**만 본다. 문장이 옳은지는
    검증하지 않는다 — 위 `test_a_measured_benchmark_names_its_measurement_date_in_the_comment`와
    같은 처리다. 이름을 적으려면 주석을 쓰게 되고, 쓰면 사유가 남는다.

    **한 항목만 본다.** `.gitignore` 전체에 「패턴마다 주석」을 강제하지는 않는다 —
    `*.db`·`*.db-journal`처럼 한 사유로 묶이는 덩어리가 정상이고 `# Python` 같은 분류
    머리글도 정상이라, 일반 규칙으로 올리면 **거짓 양성이 난다.**
    """
    rule = "_workspace/"
    owning = [
        (comment, patterns)
        for comment, patterns in _gitignore_blocks()
        if rule in patterns
    ]

    assert owning, f"`.gitignore`에서 `{rule}` 규칙을 못 찾았다 — 지웠으면 이 검사도 지운다."

    comment, patterns = owning[0]
    assert "_workspace" in comment, (
        f"`{rule}`가 자기 사유를 안 들고 있다 (CLAUDE.md §2.3 · #116 잔여).\n"
        f"  이 규칙이 든 블록의 주석: {comment or '(주석 없음)'}\n"
        f"  그 블록이 든 패턴: {patterns}\n"
        "무엇이 거기 쌓이는지와 왜 추적하지 않는지를 이 규칙 자신의 주석에 적는다."
    )


def test_the_declared_python_floor_covers_the_tarfile_filter_api() -> None:
    """선언된 Python 하한이 `task_loader`가 부르는 `tarfile` 추출 필터 API(`filter="data"`)가
    처음 생긴 패치(3.11.4)와 같다 — `pyproject.toml`의 `requires-python`과 `docs/portability.md`
    §1 표의 「Python X+」 둘 다.

    명세의 **표기**가 아니라 **뜻**을 묻는다: `packaging`의 `SpecifierSet`으로 「3.11.3(필터 API가
    없는 마지막 3.11)은 배제하고 3.11.4는 포함하는가」만 본다 — `~=3.11.4`·`==3.11.4`·`>3.11.3`·
    `>=3.10,>=3.11.4`처럼 같은 뜻의 다른 표기는 초록이다. 하한이 3.11.4보다 낮으면 로더가 첫
    멤버에서 `TypeError`로 죽는 판(3.11.0~3.11.3)을 지원으로 선언하게 되는데, CI의 `python-version:
    "3.11"`은 최신 3.11.x로 풀려 그 판을 안 돌므로 선언이 낡아도 CI가 못 본다.

    ⚠ 「3.11.4 포함」은 선언이 문서(`task_loader` docstring·이 표)의 3.11.4와 **같은 수**를 말하게
    묶는 몫이다 — 하한을 올리기로 하면(예: 3.11.13) 그 둘과 이 검사를 같이 고친다.
    ⚠ 하한 아래 인터프리터에서 로더가 명확히 거부하는지는 `tests/test_task_loader.py`가 본다 —
    하네스는 체크아웃에서 돌아서(`python -m pytest`·`python -m src.cli`) `requires-python`이 실행을
    막지 못한다(설치 도구가 설치할 때만 읽는다). 표 쪽은 행의 **철자**(`| Python X+ |`)를 읽는다 —
    행 모양을 바꾸면 「못 읽었다」로 빨개진다. 어느 쪽이든 그 판의 필터가 안전한지는 안 묻는다.
    """
    declared = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["requires-python"]
    table = (REPO_ROOT / "docs" / "portability.md").read_text(encoding="utf-8")
    row = re.search(r"^\|\s*Python\s+(\d+(?:\.\d+)*)\+\s*\|", table, re.MULTILINE)
    assert row, "`docs/portability.md` §1 표에서 「| Python X+ |」 행을 못 읽었다"

    for where, spec in (
        ("pyproject.toml의 requires-python", declared),
        ("docs/portability.md의 「Python X+」", f">={row.group(1)}"),
    ):
        allowed = SpecifierSet(spec)
        assert "3.11.3" not in allowed, (
            f"{where}({spec!r})가 3.11.3을 허용한다 — `task_loader`의 "
            "`tar.extract(..., filter=\"data\")`는 3.11.4에서 들어온 API다(PEP 706)."
        )
        assert "3.11.4" in allowed, (
            f"{where}({spec!r})가 3.11.4를 배제한다 — 하한을 올렸으면 `task_loader` docstring·"
            "`docs/portability.md`·이 검사를 같이 고친다."
        )


# --- 전수 수집의 오염 핀: 「체크아웃이 아니라 저장소를 센다」 -----------------------------------
#
# 명제: 전수 검사는 **git이 추적하는 파일**만 읽는다. 체크아웃에 같이 있는 미추적 트리는
# 이 저장소의 소스가 아니다 — `.claude/worktrees/<이름>/`은 다른 세션의 linked worktree고
# 그 안에는 낡은 판의 문서가 그대로 있다.
#
# **사고 —** 2026-10-12, `.claude/worktrees/` 아래 worktree가 다섯 있는 체크아웃에서 전수
# 검사 셋이 깨졌다(#631). offender 목록이 **전부 `.claude/worktrees/...`** 였다 — 이 저장소가
# 추적하는 산문은 한 줄도 틀리지 않았고, 읽는 쪽에는 고칠 파일이 없다. 그 셋이
# `_TRACKED_ONLY_SWEEPS`다.
#
# ⚠ **이 핀이 무는 것을 세게 적지 않는다.** 아래 셋이 단언하는 것은 ① 수집기가 추적 파일만
# 낸다(디스크에서 지워진 추적 파일은 뺀다) ② 살아 있는 수집에 `.claude/`가 없다 ③ 그 셋이
# `tracked_files`를 비우면 **훑은 수 가드로 죽는다** - 수집을 다른 길(`REPO_ROOT.glob("**/…")` ·
# `Path(REPO_ROOT).rglob` · 루트 별칭)로 걷는 판은 비워진 수집기를 안 거치므로 가드가 안 물고
# 빨개진다 - 까지다. **수집기를 거치면서 `scanned`에 안 세는 별도 걷기, 그리고 다른 파일에
# 새로 생기는 전수는 안 본다**(이름을 손으로 적는 목록이라서다).
# ⚠ 이 판의 앞 판은 소스에 `"REPO_ROOT.rglob" not in source`를 단언했다. **핀 철자를 남긴 채
# 결함을 되살리는 변이**(`glob("**/" + s)` 한 줄)가 그것을 통과했다 - 문자열은 철자를 단언하지
# 성질을 단언하지 않는다(CLAUDE.md §2.5).
_TRACKED_ONLY_SWEEPS = (
    ("tests/test_pov_oracle_feedback.py", "test_no_live_source_says_pov_v3_is_unwired"),
    ("tests/test_pov_prompt_v4.py", "test_no_live_source_names_v3_alone_as_the_pov_prompt"),
    (
        "tests/test_pov_prompt_v4.py",
        "test_no_live_source_puts_last_gen_on_the_first_line_of_the_observation",
    ),
)

# 2026-10-12에 실제로 보고된 offender의 경로 꼴. 그날의 실형태다.
_PLANTED_WORKTREE = ".claude/worktrees/stale-demo"
# 그 worktree 사본에 들어 있던 낡은 산문 — `test_no_live_source_says_pov_v3_is_unwired`의
# 감지기가 무는 문형이다(지어낸 문장이 아니라 그날 재현에 쓴 줄이다).
_STALE_LINE = "PoV 프롬프트는 v3가 아직 미배선이다."


def test_the_tracked_sweep_counts_the_repository_not_the_checkout(tmp_path: Path) -> None:
    """오염 핀(CLAUDE.md §2.5). 임시 저장소에 같은 줄을 **둘** 심는다 — 추적되는 `docs/live.md`와
    미추적 `.claude/worktrees/stale-demo/docs/live.md`.

    **양성**: 추적된 쪽은 수집에 든다. **음성**: worktree 사본은 안 든다. 둘을 한 단언에
    넣는다 — 수집이 통째로 비어도 음성만으로는 초록이기 때문이다.
    """
    try:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    except OSError:  # pragma: no cover - git 없는 환경
        pytest.skip("git을 실행할 수 없다")

    tracked = tmp_path / "docs" / "live.md"
    tracked.parent.mkdir(parents=True)
    tracked.write_text(_STALE_LINE, encoding="utf-8")
    subprocess.run(
        ["git", "add", "docs/live.md"], cwd=tmp_path, check=True, capture_output=True
    )

    copy = tmp_path / _PLANTED_WORKTREE / "docs" / "live.md"
    copy.parent.mkdir(parents=True)
    copy.write_text(_STALE_LINE, encoding="utf-8")

    collected = [
        path.relative_to(tmp_path).as_posix()
        for path in tracked_files("*.md", root=tmp_path)
    ]
    assert collected == ["docs/live.md"], collected


def test_the_live_tracked_sweep_pulls_no_untracked_checkout_tree() -> None:
    """살아 있는 저장소에서 같은 명제를 센다. 체크아웃에 worktree가 있든 없든 통과해야 한다 —
    없는 기계에서는 공허하므로 **훑은 수**도 같이 단언한다."""
    collected = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in tracked_files("*.md", "*.py", "*.yaml")
    ]
    assert len(collected) > 100, f"수집이 너무 적다 - 저장소 루트가 틀렸다: {len(collected)}"
    strays = [name for name in collected if name.startswith((".claude/", "_workspace/"))]
    assert strays == [], strays


def test_the_tracked_sweep_skips_a_tracked_file_deleted_from_disk(tmp_path: Path) -> None:
    """`git rm` 없이 지운 추적 파일은 인덱스에 남는다 - 수집이 그걸 내면 세 전수가 열다가
    `FileNotFoundError`로 죽는다(`rglob` 판은 통과했다).

    **양성**: 디스크에 있는 `docs/live.md`는 든다. **음성**: 지워진 `docs/gone.md`는 안 든다.
    한 단언에 넣는다 - 수집이 통째로 비어도 음성만으로는 초록이기 때문이다.
    """
    try:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    except OSError:  # pragma: no cover - git 없는 환경
        pytest.skip("git을 실행할 수 없다")

    (tmp_path / "docs").mkdir()
    for name in ("live.md", "gone.md"):
        (tmp_path / "docs" / name).write_text(_STALE_LINE, encoding="utf-8")
    subprocess.run(
        ["git", "add", "docs/live.md", "docs/gone.md"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "docs" / "gone.md").unlink()

    collected = [
        path.relative_to(tmp_path).as_posix()
        for path in tracked_files("*.md", root=tmp_path)
    ]
    assert collected == ["docs/live.md"], collected


@pytest.mark.parametrize(("posix", "name"), _TRACKED_ONLY_SWEEPS)
def test_the_live_source_sweeps_die_on_an_empty_collection(
    posix: str, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """행동 핀. 그 셋의 수집기 `tracked_files`를 빈 목록으로 바꾸고 **불러서** 본다 - 훑은 수
    가드(`scanned > 100`)가 물어야 한다.

    소스 문자열(`REPO_ROOT.rglob`가 없는가)을 보는 판은 `glob("**/…")`·`Path(REPO_ROOT).rglob`·
    루트 별칭으로 핀 철자를 남긴 채 되살아났다(@sumin0218 리뷰). 수집을 다른 길로 걷는 전수는 비워진
    수집기를 안 거치므로 가드가 안 물고 `DID NOT RAISE`로 빨개진다. 함수가 개명되면
    `getattr`이 터진다 - 테스트가 개명돼 이 핀이 아무것도 안 지키는 일은 없다.
    """
    module = importlib.import_module(posix.removesuffix(".py").replace("/", "."))
    monkeypatch.setattr(module, "tracked_files", lambda *patterns, **kwargs: [])
    with pytest.raises(AssertionError, match="훑은 파일이 너무 적다"):
        getattr(module, name)()
