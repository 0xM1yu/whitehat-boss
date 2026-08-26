"""문서의 **형식**을 기계로 검사한다 — 사람 눈이 다섯 번 놓친 부류를 옮긴 자리.

두 사람이 같은 문서의 같은 자리를 각자 고치고, 번호가 겹치고, 표가 깨진 채 머지되는
사고가 반복됐다. 자동 머지는 충돌 0건이었는데 합쳐진 문서가 자기모순이었다.

## 이 파일이 잡는 것은 형식뿐이다

**"주장이 실측과 다르다" 같은 의미 수준은 하나도 못 잡는다.** 이 경계를 여기에 적어 두는
이유는, 다음 사람이 "검사기가 있으니 문서는 안전하다"로 읽으면 그 오독 자체가 다음 사고를
만들기 때문이다. 실제로 이 저장소가 겪은 사고 셋 중 **하나만** 여기서 걸린다.

- **걸린다** — PR #14(재현성 비교)와 #17(`.inc`)이 각각 `D14`를 신설했다(커밋 `ec87efa`).
  `test_decision_numbers_are_unique`가 잡는다
- 같은 사고 — PR #18·#19가 그 `D14`를 누가 고칠지 손으로 조율한 것
- **안 걸린다** — #18이 함께 고친 것들. `trace.py` 대응표에 GPU 시간 행이 없었고,
  STATUS.md §2는 Retrieval을 "흉내"로 두는데 §4는 "완주"라 하고, E2 서술이 `gate.py`
  실동작과 어긋나 있었다. **셋 다 의미 수준이다**

`D14` 충돌이 사람 눈에 안 보였던 이유가 이 검사가 필요한 이유 그대로다. main의 D 표가
D12에서 끝나고 **빈 줄**이 있었는데 한쪽 PR은 그 앞에, 다른 쪽은 뒤에 행을 넣어서 git이
겹침으로 보지 않았다. 텍스트는 안 겹치는데 **번호가 겹쳤다.**

## 못 잡는 것 (전량)

1. **주장이 실측과 다른 것.** 이 검사기는 문서를 읽되 무슨 뜻인지는 모른다
2. **두 문서가 같은 사실을 반대로 적은 것**
3. **표 안의 값이 틀린 것** — 열 개수만 세고 셀 내용은 안 본다
4. **문서가 코드와 어긋나는 것** — `test_doc_examples.py`가 JSON 예시에 한해서만,
   그것도 `agent-contracts.md`·`knowledge-base.md` 두 문서에서만 한다
5. **링크의 앵커(`#절-제목`)가 실재하는지** — 파일이 있는지만 보고 `#` 뒤는 버린다
6. **`D` 항목의 내용이 그 번호에 맞는지** — 유일하고 실재하는지만 본다
7. **개행(CRLF).** 그것은 `test_repo_hygiene.py`가 `git ls-files --eol`로 따로 한다.
   여기 없다 — 두 곳에서 같은 것을 검사하면 어느 쪽이 진짜인지 흐려진다

## 검사 대상은 `git ls-files`가 정한다

목록을 손으로 적지 않는다. 그러면 새 문서가 조용히 빠지는 실패 유형이 구조적으로 안
생긴다 — `test_doc_examples.test_every_json_block_is_covered`가 세운 원칙과 같다.
추적되지 않는 것(`.claude/plans/` 등)은 CI에 존재하지 않으므로 대상이 아니다.
"""

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

OPEN_QUESTIONS = REPO_ROOT / "docs" / "open-questions.md"
EXPERIMENTS = REPO_ROOT / "docs" / "experiments"

# 색인·서식이라 보고서가 아니다.
_NOT_A_REPORT = frozenset({"README.md", "TEMPLATE.md"})

# 이스케이프되지 않은 파이프만 셀 경계다. `\|`는 셀 안의 문자다.
_PIPE = re.compile(r"(?<!\\)\|")
_LEADING_PIPE = re.compile(r"^\|")
_TRAILING_PIPE = re.compile(r"(?<!\\)\|\s*$")

# `| --- | --- |` 꼴. 글자가 하나라도 있으면 구분선이 아니다.
_SEPARATOR = re.compile(r"^\|[\s:|-]+$")

_FENCE = re.compile(r"^\s*(```|~~~)")

# `docs/open-questions.md`의 D 표 행. 이것이 D 번호의 **유일한 정의처**다.
_D_ROW = re.compile(r"^\|\s*(D\d+)\s*\|", re.MULTILINE)

# 다른 문서·설정·코드가 가리키는 `D<n>`. `\b`만 쓰면 `AD15`나 16진 digest가 샌다.
_D_REF = re.compile(r"(?<![A-Za-z0-9])D(\d+)(?![0-9A-Za-z])")

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

# `YYYY-MM-DD-<주제>-<장비>.md`. 주제는 검사하지 않는다 — 아래 주석 참조.
_REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)-([a-z0-9]+)\.md$")

# 보고서 머리 메타 표의 `--device` 행. TEMPLATE.md가 정한 서식이다.
_DEVICE_ROW = re.compile(r"^\|\s*`--device`\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)


def _tracked(*patterns: str) -> list[Path]:
    """`git ls-files`가 내놓는 추적 파일. git이 없으면 검사를 건너뛴다.

    **`-z`가 필수다.** 기본 출력은 비ASCII 경로를 큰따옴표로 감싸고 바이트를 8진
    이스케이프로 바꾼다(`.claude/노트.md` → `".claude/\\353\\205\\270...md"`). 그대로
    `Path`에 넣으면 없는 파일을 가리켜 `OSError`가 나고, 검사기가 **문서의 결함처럼 보이는
    실패**를 낸다 — 원인이 문서가 아니라 여기라서 읽는 사람이 엉뚱한 곳을 고친다.
    `-z`는 이스케이프를 끄고 NUL로 가른다.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", *patterns],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:  # pragma: no cover - git 없는 환경
        pytest.skip("git을 실행할 수 없다")
    if result.returncode != 0:  # pragma: no cover - 저장소 밖
        pytest.skip("저장소가 아니다")
    return [REPO_ROOT / name for name in result.stdout.split("\0") if name]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _cells(line: str) -> int:
    """마크다운 표 한 행의 셀 개수.

    앞뒤 파이프는 셀이 아니라 표의 테두리이므로 한 개씩 뗀다. 남은 파이프가 셀 경계다.
    """
    body = _LEADING_PIPE.sub("", line.strip())
    body = _TRAILING_PIPE.sub("", body)
    return len(_PIPE.split(body))


def _column_offenders(text: str) -> list[tuple[int, int, int, str]]:
    """`(줄 번호, 실제 셀 수, 헤더 셀 수, 줄 원문)`.

    표로 인정하는 조건은 "파이프로 시작하는 줄 + 바로 다음 줄이 구분선"이다. 코드 펜스
    안은 보지 않는다 — 셸 파이프라인이 든 블록이 표로 오인된다.
    """
    lines = text.splitlines()
    offenders: list[tuple[int, int, int, str]] = []
    in_fence = False
    expected: int | None = None
    index = 0

    while index < len(lines):
        line = lines[index]

        if _FENCE.match(line):
            in_fence = not in_fence
            expected = None
            index += 1
            continue

        if in_fence or not line.lstrip().startswith("|"):
            expected = None
            index += 1
            continue

        if expected is None:
            if index + 1 < len(lines) and _SEPARATOR.match(lines[index + 1].strip()):
                expected = _cells(line)
                index += 2  # 헤더와 구분선을 지난다
            else:
                index += 1
            continue

        found = _cells(line)
        if found != expected:
            offenders.append((index + 1, found, expected, line))
        index += 1

    return offenders


def _defined_decisions() -> list[str]:
    return _D_ROW.findall(_read(OPEN_QUESTIONS))


def _reports() -> list[Path]:
    return sorted(p for p in EXPERIMENTS.glob("*.md") if p.name not in _NOT_A_REPORT)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_decision_numbers_are_unique() -> None:
    """같은 `D` 번호가 서로 다른 두 항목에 붙으면 이후의 모든 참조가 어느 쪽인지 모른다.

    실제로 일어났다 — PR #14(재현성 비교)와 #17(`.inc`)이 각각 `D14`를 신설했고 자동
    머지는 충돌 0건이었다. 두 행이 빈 줄을 사이에 두고 떨어져 있어서 git이 겹침으로
    보지 않았다.
    """
    seen: dict[str, int] = {}
    for number in _defined_decisions():
        seen[number] = seen.get(number, 0) + 1

    duplicates = [
        f"docs/open-questions.md: {number}이 {count}번 정의됐다 — "
        f"`D` 번호는 고정 ID다. 나중에 들어온 쪽에 새 번호를 준다(뒤 번호를 당기지 않는다)"
        for number, count in sorted(seen.items(), key=lambda kv: int(kv[0][1:]))
        if count > 1
    ]

    assert duplicates == [], "\n".join(duplicates)


def test_decision_references_point_at_a_real_row() -> None:
    """가리키는 `D<n>`이 없으면 읽는 사람이 근거를 못 찾는다.

    문서만 보지 않는다. `config/budgets.yaml`이 `D15`를 인용하는 것처럼 설정과 코드도
    `D` 번호를 근거로 든다(PR #18 본문에 그 사실이 적혀 있다).
    """
    defined = set(_defined_decisions())
    dangling: list[str] = []

    for path in _tracked("*.md", "*.yaml", "*.yml", "*.py"):
        if path == OPEN_QUESTIONS:
            continue
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            for number in _D_REF.findall(line):
                if f"D{number}" not in defined:
                    dangling.append(
                        f"{_rel(path)}:{lineno}: D{number}이 "
                        f"docs/open-questions.md에 없다 — 번호가 맞는지 확인한다"
                    )

    assert dangling == [], "\n".join(dangling)


@pytest.mark.parametrize("path", _tracked("*.md"), ids=_rel)
def test_markdown_tables_have_a_consistent_column_count(path: Path) -> None:
    """셀 수가 헤더와 다르면 그 행은 렌더링에서 어긋나거나 뒤 열이 잘린다.

    거의 언제나 원인은 **셀 안의 이스케이프 안 된 `|`**다. GFM은 백틱 안이라고 파이프를
    보호하지 않으므로 인라인 코드에 든 셸 파이프라인도 셀을 쪼갠다.
    """
    offenders = [
        f"{_rel(path)}:{lineno}: 셀 {found}개 — 이 표의 헤더는 {expected}개다.\n"
        f"  이스케이프 안 된 `|`가 셀을 쪼갠다: {line.strip()[:120]}\n"
        "  고치는 법: 셀 안의 파이프를 `\\|`로 쓴다"
        for lineno, found, expected, line in _column_offenders(_read(path))
    ]

    assert offenders == [], "\n".join(offenders)


@pytest.mark.parametrize("path", _tracked("*.md"), ids=_rel)
def test_internal_links_point_at_a_real_file(path: Path) -> None:
    """저장소 안을 가리키는 링크가 깨져 있으면 근거를 따라갈 수 없다.

    `#` 뒤(앵커)는 버린다 — 절 제목은 자주 바뀌고, 그것까지 고정하면 제목을 다듬을 때마다
    검사가 깨져서 사람이 검사기를 끄게 된다. 파일이 있는지만 본다.
    """
    base = path.parent
    broken: list[str] = []

    for lineno, line in enumerate(_read(path).splitlines(), 1):
        for target in _LINK.findall(line):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            # `%20` 같은 퍼센트 인코딩은 파일 이름의 공백이다.
            relative = unquote(target.split("#", 1)[0])
            if not relative:
                continue
            if not (base / relative).exists():
                broken.append(f"{_rel(path)}:{lineno}: `{target}` 가 가리키는 파일이 없다")

    assert broken == [], "\n".join(broken)


def test_experiment_filenames_carry_the_device_the_run_used() -> None:
    """파일명의 장비가 본문의 `--device`와 다르면 색인이 거짓말한다.

    `docs/experiments/README.md`가 그 근거를 적어 뒀다 — 파일명이 `RunRecord.device`로
    grep되고, `evaluation.md` §2.3 규칙 5("장비가 다른 run은 같은 비교 셀에 넣지 않는다")가
    파일명 수준에서 눈에 보이게 하는 것이 규약의 목적이다.

    **주제 문자열은 검사하지 않는다.** README는 주제를 "경로 이름"이라고만 정하고 허용
    목록이 저장소 어디에도 없다. 검사기가 목록을 새로 만들면 그 목록이 또 하나의 진실
    원천이 되고, 사실이 두 곳에 사는 것이 이 폴더가 존재하는 이유(그 실패)와 같다.
    """
    offenders: list[str] = []

    for report in _reports():
        match = _REPORT_NAME.match(report.name)
        if match is None:
            offenders.append(f"{_rel(report)}: 파일명이 `YYYY-MM-DD-<주제>-<장비>.md`가 아니다")
            continue

        device_in_name = match.group(3)
        declared = _DEVICE_ROW.search(_read(report))
        if declared is None:
            # 메타 표에 `--device` 행이 없는 것은 별개 문제다(TEMPLATE.md 미준수).
            # 여기서 단언하면 두 실패가 한 메시지에 섞인다.
            continue
        if declared.group(1) != device_in_name:
            offenders.append(
                f"{_rel(report)}: 파일명의 장비는 `{device_in_name}`인데 "
                f"본문 `--device`는 `{declared.group(1)}`이다"
            )

    assert offenders == [], "\n".join(offenders)


def test_every_experiment_report_is_in_the_index() -> None:
    """색인에 없는 보고서는 없는 것과 같다.

    이 폴더가 생긴 계기가 "보고서가 메신저 첨부로만 존재해서 대조가 안 됐다"이다. 저장소에
    넣어 놓고 색인에 안 걸면 같은 자리로 돌아간다.
    """
    index = _read(EXPERIMENTS / "README.md")
    linked = {unquote(target.split("#", 1)[0]) for target in _LINK.findall(index)}

    missing = [
        f"docs/experiments/README.md: `{report.name}` 가 색인 표에 없다"
        for report in _reports()
        if report.name not in linked
    ]

    assert missing == [], "\n".join(missing)
