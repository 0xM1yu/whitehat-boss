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
import yaml

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


def _split_cells(line: str) -> list[str]:
    """마크다운 표 한 행의 셀.

    앞뒤 파이프는 셀이 아니라 표의 테두리이므로 한 개씩 뗀다. 남은 파이프가 셀 경계다.
    """
    body = _LEADING_PIPE.sub("", line.strip())
    body = _TRAILING_PIPE.sub("", body)
    return [cell.strip() for cell in _PIPE.split(body)]


def _cells(line: str) -> int:
    """마크다운 표 한 행의 셀 **개수**.

    쪼개는 규칙은 `_split_cells` 하나뿐이다 — 세는 쪽과 읽는 쪽이 규칙을 따로 들면
    "무엇이 한 칸인가"의 답이 두 개가 된다.
    """
    return len(_split_cells(line))


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


# 보고서는 `harness/` 아래에 있다. `experiments/` 바로 밑에는 색인(README)과 양식(TEMPLATE)만
# 남는다. **튜플인 것은 의도다** — 2026-09-04까지는 `2x2/`(팀 실험)가 두 번째 원소였고,
# 보고서 폴더가 다시 갈릴 때 여기에 더하면 아래 전부가 따라온다.
REPORT_DIRS = (EXPERIMENTS / "harness",)


def _reports() -> list[Path]:
    return sorted(
        p
        for directory in REPORT_DIRS
        if directory.is_dir()
        for p in directory.glob("*.md")
        if p.name not in _NOT_A_REPORT
    )


def test_the_report_glob_actually_finds_reports() -> None:
    """`_reports()`가 빈 목록이면 아래 보고서 검사들이 **조용히 통과한다.**

    파라미터화된 검사는 목록이 비면 아무것도 안 돌고 "통과"로 보인다. 실제로 이 함수가
    `experiments/*.md`를 보던 때 보고서를 `harness/`로 옮겼더니 **0건이 되어 두 검사가
    빈 채로 초록이었다.** 그 사고를 여기서 잡는다 — 디렉터리 배치가 또 바뀌면 이 단언이
    먼저 깨진다.
    """
    assert _reports(), (
        f"{[d.name for d in REPORT_DIRS]} 어디에도 보고서가 없다 — "
        "배치를 바꿨다면 REPORT_DIRS를 같이 고친다"
    )


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

    **파일명이 아니라 `experiments/` 기준 상대경로로 대조한다.** 보고서 폴더가 둘 이상으로
    갈리면 이름만 보는 대조는 색인이 **다른 폴더의 같은 이름**을 가리켜도 통과시킨다.
    """
    index = _read(EXPERIMENTS / "README.md")
    linked = {unquote(target.split("#", 1)[0]) for target in _LINK.findall(index)}

    missing = [
        f"docs/experiments/README.md: `{rel}` 가 색인 표에 없다"
        for rel in (r.relative_to(EXPERIMENTS).as_posix() for r in _reports())
        if rel not in linked
    ]

    assert missing == [], "\n".join(missing)


def test_the_contract_example_output_max_matches_the_config() -> None:
    """계약 문서의 `output_max`가 config의 값과 같다.

    그 줄 자신이 `# config의 thresholds.output_max와 같은 값`이라고 적어 뒀는데 지키는
    것이 없었다. **D42가 정확히 이 드리프트에서 나왔다** — 계약 문서가 `output_max: 2000`을
    선언하고 `config/budgets.yaml`의 역산 주석이 그 2,000을 쓰는데 **하네스에는 그 키가
    없어서** 실제 상한은 `--max-model-len`이었다. 선언과 실행이 갈린 것을 아무도 못 봤다.

    **값 자체는 잠그지 않는다** — 실측으로 정하는 값을 테스트가 상수로 박으면 안 된다는
    것이 `tests/test_config.py` 모듈 docstring의 판정이다. 잠그는 것은 **두 자리가 같다**는
    불변식뿐이라, 값을 바꿀 때 두 곳을 함께 바꾸면 통과한다.

    **선언을 전부 잡는다.** 첫 매치만 보면 D46 **(c)**(역할별 `output_max`)가 그 문서에
    예시로 들어오는 날 조용히 첫 줄만 검사하게 된다 — 지금 고치는 드리프트와 같은 모양이
    한 겹 아래에서 다시 생긴다.

    **`b1_single`을 쓰는 것은 아무 프로필이나 된다는 뜻이다.** `config/budgets.yaml`이
    "thresholds·sampling·seed는 프로필과 무관하게 공통"이라고 못박아 뒀다. 그 전제가
    깨지면 이 테스트는 한 프로필만 보게 되므로, 그때는 여기도 같이 고쳐야 한다.
    """
    from src.config import load_config

    contract = _read(REPO_ROOT / "docs" / "agent-contracts.md")
    pattern = re.compile(r"^\s*output_max:\s*(\d+)", re.MULTILINE)
    declared = [int(m.group(1)) for m in pattern.finditer(contract)]
    assert declared, "docs/agent-contracts.md에 output_max 선언이 없다"

    actual = load_config("b1_single").thresholds.output_max
    assert declared == [actual] * len(declared), (
        f"docs/agent-contracts.md의 output_max 선언 {declared}이 "
        f"config의 값({actual})과 다르다 — 둘을 함께 고친다"
    )


# --- 구축안(docs/build-plan.md) 전용 가드 -----------------------------------
#
# 셋 다 **다른 검사가 구조적으로 못 잡는 것**만 본다. 하나라도 기존 검사와 겹치면
# 그 자리는 지우는 편이 낫다.
#
# 허용 목록은 **문서가 실제로 인용하는 것만** 담는다. 안 쓰는 번호를 넣어 두면 목록이
# 조용히 넓어져 오탐 방지가 아니라 검사 무력화가 된다 — 항목을 더할 때 문서에서 그 번호를
# 실제로 찾아보고 넣는다.

BUILD_PLAN = REPO_ROOT / "docs" / "build-plan.md"

# 구축안이 정당하게 인용하는 D 항목. 이 목록 **밖의** D 참조는 파인튜닝 라벨을 D 계열로
# 잘못 쓴 것이다 — 원문이 파인튜닝 단계를 `D1`·`D2`·`D3`으로 불렀는데 이 저장소에서 그
# 셋은 각각 벤치마크 라벨 통합·Scout 임계값·Capsule 확장 상한이다.
_BUILD_PLAN_D_CITATIONS = frozenset({"D9", "D13", "D25", "D30", "D51"})

# `docs/open-questions.md` §1의 R 행. **서식을 `test_open_questions_layout`의 `_ROW`와
# 정확히 맞춘다** — 그쪽이 "인덱스 행 ↔ 본문 파일"을 잇는 기준이라, 여기가 더 느슨하면
# 본문 파일이 없는 번호가 이쪽에서만 "정의됨"으로 새어 나간다.
_R_ROW = re.compile(r"^\| (R\d+) \|", re.MULTILINE)

# 다른 문서가 가리키는 `R<n>`. `_D_REF`와 같은 논법이다 — `\b`만 쓰면 16진 digest가 샌다.
_R_REF = re.compile(r"(?<![A-Za-z0-9])R(\d+)(?![0-9A-Za-z])")


def test_the_build_plan_is_a_tracked_document() -> None:
    """구축안이 `git ls-files`에 있어야 나머지 문서 검사가 그것을 본다.

    **`_tracked()`가 `git ls-files -z`로 수집하고 파라미터화는 수집 시점에 평가된다.**
    그래서 `git add` 되지 않은 새 문서는 표 열 수·내부 링크·D 참조 세 검사에 **존재하지
    않는다** — 문서를 써 놓고 pytest가 초록이어도 아무것도 검사되지 않았다는 뜻이다.

    같은 논거의 선례가 이 파일에 이미 있다(`test_the_report_glob_actually_finds_reports`
    — "파라미터화된 검사는 목록이 비면 아무것도 안 돌고 통과로 보인다").

    **이 검사는 CI가 아니라 푸시 전 로컬에서 발화한다.** CI는 커밋된 트리를 체크아웃하므로
    추적되지 않은 파일이 애초에 없다. 잡으려는 상황이 "손에서 문서를 쓰고 add를 잊은 채
    테스트를 돌렸다"라서 그 자리가 맞다.
    """
    assert BUILD_PLAN.is_file(), "docs/build-plan.md가 없다"
    assert BUILD_PLAN in _tracked("*.md"), (
        "docs/build-plan.md가 추적 목록에 없다 — 표 열 수·내부 링크·D 참조 검사가 "
        "이 문서를 통째로 건너뛴다. `git add docs/build-plan.md` 했는지 확인한다"
    )


def test_the_build_plan_does_not_borrow_the_d_series_namespace() -> None:
    """파인튜닝 단계 라벨은 `FT1`·`FT2`·`FT3`다. D 계열을 빌려 쓰면 안 된다.

    **기존 D 참조 검사가 이것을 못 잡는다.** `test_decision_references_point_at_a_real_row`
    는 번호가 인덱스에 **실재하는지**만 보는데 `D1`·`D2`·`D3`은 전부 실재한다 — 다른 뜻으로.
    그래서 CI가 초록인 채로, 파인튜닝 계획을 읽던 사람이 링크를 따라가 "벤치마크 라벨 통합"
    문서를 연다. CLAUDE.md §2가 말하는 "테스트가 못 잡는 규칙"의 교과서적 사례라 여기에
    **이 문서 한정** 가드를 둔다.

    허용 목록은 처음부터 필수다 — 구축안은 D9(1차 분석 대상의 범위)와 D51(필드 순서)을
    정당하게 인용한다. 인용이 늘면 이 집합에 더한다.
    """
    borrowed: list[str] = []
    for lineno, line in enumerate(_read(BUILD_PLAN).splitlines(), 1):
        for number in _D_REF.findall(line):
            if f"D{number}" not in _BUILD_PLAN_D_CITATIONS:
                borrowed.append(
                    f"docs/build-plan.md:{lineno}: D{number} — 파인튜닝 라벨이면 "
                    f"FT{number}로 쓴다. D{number}는 이 저장소의 다른 항목이고 "
                    "번호가 실재하므로 D 참조 검사가 잡지 못한다"
                )
    assert borrowed == [], "\n".join(borrowed)


def test_the_build_plan_risk_references_point_at_a_real_r_row() -> None:
    """구축안이 가리키는 `R<n>`이 인덱스 §1에 실재해야 한다.

    **R 참조는 이 저장소에서 어떤 검사도 받지 않는다.** `_D_ROW`·`_D_REF` 둘 다 `D`만 보고,
    `[DR]`로 넓혀진 것은 인덱스 행과 본문 파일을 잇는 `test_open_questions_layout` 쪽
    정규식 하나뿐이다 — 즉 "다른 문서 → R 참조"가 통째로 무검사다. 구축안은 R1·R4·R6을
    인용한다(§1 정정 10 · §9 두 행). §9의 「미등재」 넷은 아직 번호가 없다.

    **범위를 이 문서 하나로 좁혔다.** `_D_REF`를 `[DR]`로 넓히는 안은 지금 저장소 전체에서
    이미 초록이라 RED가 되지 않고, 그 확대는 `R1` 같은 토큰이 다른 뜻으로 쓰인 자리를
    전수 확인하는 별건이다.
    """
    defined = set(_R_ROW.findall(_read(OPEN_QUESTIONS)))
    assert defined, "docs/open-questions.md §1에서 R 행을 하나도 찾지 못했다"

    dangling: list[str] = []
    for lineno, line in enumerate(_read(BUILD_PLAN).splitlines(), 1):
        for number in _R_REF.findall(line):
            if f"R{number}" not in defined:
                dangling.append(
                    f"docs/build-plan.md:{lineno}: R{number}이 "
                    "docs/open-questions.md §1에 없다 — 번호가 맞는지 확인한다"
                )
    assert dangling == [], "\n".join(dangling)


# 구축안이 프로필의 실서빙 역할 수를 셀 때 쓰는 표기: `프로필이름`(실서빙 N역할
_SERVED_ROLE_CLAIM = re.compile(r"`([a-z0-9_]+)`\(실서빙 (\d+)역할")


def test_the_build_plan_served_role_counts_match_served_models() -> None:
    """구축안이 「실서빙 N역할」이라 적으면 그 N은 `served_models()`가 내는 수여야 한다.

    #140 리뷰가 `local_balanced`의 「실서빙 3역할」을 **2로 고치라**고 요구했다. 근거는
    셋째 critic(`devstral-small-2-24b`) 서버가 아직 안 떴다는 것이고, 그 사실 자체는
    맞다 — 같은 표의 윗 행이 그렇게 적는다. 그러나 `served_models()`는 서버가 실제로
    떴는지를 보지 않는다. `is_fake`(엔드포인트 스킴)와 `retriever` 축만 본다
    (`src/config.py`, 이슈 #108 (b)). 그래서 코드가 내는 수는 **3이다.**

    2로 고쳤다면 문서가 코드와 어긋난다 — CLAUDE.md §2.1이 금지하는 것이 정확히 그것이고,
    「서버가 안 떴다」는 별개의 사실이라 **개수를 깎는 대신 선행 조건으로 적는 것**이 맞다.

    이 검사는 그 혼동이 다시 들어오는 것을 막는다. 개수는 코드에서 가져온다.
    """
    from src.config import load_config

    offenders = []
    for lineno, line in enumerate(_read(BUILD_PLAN).splitlines(), 1):
        for profile, claimed in _SERVED_ROLE_CLAIM.findall(line):
            actual = len(load_config(profile).served_models())
            if int(claimed) != actual:
                offenders.append(
                    f"docs/build-plan.md:{lineno}: {profile} — 문서는 {claimed}역할, "
                    f"served_models()는 {actual}역할"
                )

    assert offenders == [], (
        "구축안의 실서빙 역할 수가 `served_models()`와 다르다 (CLAUDE.md §2.1).\n"
        "서버가 아직 안 떴다는 사실은 개수를 깎아서가 아니라 선행 조건으로 적는다.\n"
        + "\n".join(offenders)
    )


# `docs/decisions/README.md`의 ADR 목록 표 행. 파일명 앞 네 자리가 번호다.
_ADR_ROW = re.compile(r"^\|\s*\[(\d{4})\]\(", re.MULTILINE)

DECISIONS = REPO_ROOT / "docs" / "decisions"


def test_every_adr_has_a_row_in_the_decisions_readme() -> None:
    """ADR 파일과 목록 표가 갈리면 **CI가 침묵한 채로** 결정이 사라진다.

    이 검사가 없어서 생기는 구체적 사고: `docs/decisions/README.md`를 두 PR이 동시에
    건드리면(각자 자기 ADR 행을 표에 더한다) 머지에서 충돌하는데, `--ours`/`--theirs`로
    풀면 **한쪽 행이 통째로 사라진다.** 파일 자체는 남으므로
    `test_internal_links_point_at_a_real_file`이 안 걸리고, 표를 세는 검사도 없어서
    **초록인 채로 결정 하나가 목록에서 빠진다.**

    표가 유일한 조망면이라 거기 없는 ADR은 사실상 없는 것이다 — 이 저장소가
    `docs/open-questions.md` 인덱스를 지우지 않는 이유(ADR 0008)와 같은 논거다.

    양방향으로 본다. 고아 행(파일 없는 번호)도 같은 종류의 거짓말이다.
    """
    if not DECISIONS.is_dir():
        pytest.skip("docs/decisions/ 없음")

    files = {p.name[:4] for p in DECISIONS.glob("[0-9][0-9][0-9][0-9]-*.md")}
    rows = set(_ADR_ROW.findall(_read(DECISIONS / "README.md")))

    missing = sorted(files - rows)
    orphan = sorted(rows - files)
    assert not missing, (
        f"ADR 파일은 있는데 README 목록에 행이 없다: {missing} — "
        "머지 충돌을 --ours/--theirs로 풀면 이렇게 된다"
    )
    assert not orphan, f"README 목록에 행은 있는데 그 번호의 ADR 파일이 없다: {orphan}"


# --- 정본 선언(CONTEXT.md) ↔ 모델 registry ------------------------------------
#
# CONTEXT.md는 스스로를 로스터의 **정본**이라고 선언한다. 선언만으로는 아무것도 강제되지
# 않아서 아래 둘이 그 선언을 기계가 읽는 불변식으로 바꾼다 — 두 파일이 조용히 갈라지는 것,
# 그러니까 CONTEXT.md가 존재하는 이유 그 자체를 막는다.
#
# **판정은 표 구조로만 한다. 낱말로 하지 않는다.** `미등재`·`신규`는 이 저장소에서 다른
# 뜻으로도 쓰인다(`docs/build-plan.md` §9의 R 행, 실험 보고서의 "신규 결함"). 낱말을 키로
# 삼으면 상관없는 문서가 그 낱말을 옮길 때마다 여기가 깨지고, 오탐을 내는 검사는 꺼진다.

CONTEXT = REPO_ROOT / "CONTEXT.md"

# 표를 여는 절 제목. **번호가 아니라 제목으로 찾는다** — 앞에 절이 하나 끼는 날 번호로
# 찾으면 조용히 다른 표를 읽는다.
_ROSTER_HEADING = re.compile(r"^##\s+\d+\.\s+모델 로스터")
_KEPT_HEADING = re.compile(r"^###\s+로스터에 없지만 registry에 남는 것")

_ROSTER_TABLE = "CONTEXT.md 로스터 표(§1)"
_KEPT_TABLE = "CONTEXT.md 「로스터에 없지만 registry에 남는 것」 표"

# 열은 **이름으로** 찾는다. 순서로 찾으면 열이 하나 끼는 날 옆 칸을 읽는다.
_KEY_COLUMN = "registry 키"
_MARK_COLUMN = "registry 등재"
_KEPT_KEY_COLUMN = "키"

# `registry 등재` 칸의 판정 기호(U+2705 · U+274C).
_YES = "✅"
_NO = "❌"

# 셀 첫머리의 백틱 토큰이 registry 키다. **첫 것만 읽는다** — Critic 행은
# `` `gpt-oss-20b` (`reasoning_effort: high`) ``처럼 백틱 토큰을 하나 더 들고 있고
# 그것은 키가 아니라 샘플링 인자다.
_LEADING_KEY = re.compile(r"^`([^`]+)`")


def _first_table(
    text: str, heading: re.Pattern[str], where: str
) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """`heading`이 여는 절의 **첫 표**를 `(헤더 셀, [(줄 번호, 본문 셀)])`로 읽는다.

    절은 다음 `#` 제목에서 끝나고 표는 첫 것 하나만 본다 — 절 뒤에 다른 표가 생겨도
    읽는 대상이 안 바뀐다. 코드 펜스 안은 건너뛴다(`_column_offenders`와 같은 이유).

    **못 찾으면 빈 것을 돌려주지 않고 여기서 실패한다.** 조용히 비면 절 제목을 다듬는 날
    아래 두 검사가 **아무것도 안 보고 초록**이 된다 —
    `test_the_report_glob_actually_finds_reports`가 세운 것과 같은 논거다.
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    assert start is not None, (
        f"{where}: 절 제목을 찾지 못했다 — 제목을 바꿨다면 이 검사도 같이 고친다"
    )

    header: list[str] | None = None
    rows: list[tuple[int, list[str]]] = []
    in_fence = False

    for index in range(start + 1, len(lines)):
        line = lines[index].strip()
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("#"):
            break
        if not line.startswith("|"):
            if header is not None:
                break  # 표가 끝났다
            continue
        if _SEPARATOR.match(line):
            continue
        if header is None:
            header = _split_cells(line)
        else:
            rows.append((index + 1, _split_cells(line)))

    assert header is not None and rows, f"{where}: 절 안에서 표를 찾지 못했다"
    return header, rows


def _column(header: list[str], name: str, where: str) -> int:
    assert name in header, (
        f"{where}: `{name}` 열이 없다(헤더: {header}) — 열 이름을 바꿨다면 이 검사도 같이 고친다"
    )
    return header.index(name)


def _keys_in(rows: list[tuple[int, list[str]]], column: int) -> set[str]:
    """표 한 열에서 백틱 키만 걷는다.

    백틱으로 시작하지 않는 칸은 여기서 빠지고 **그 사실 자체는 감춰지지 않는다** —
    registry에 있는 키가 안 걷히면 아래 `missing`에 나타나고, 서식 자체는
    `test_the_context_roster_matches_the_model_registry`가 따로 잡는다.
    """
    keys: set[str] = set()
    for _, row in rows:
        if column >= len(row):
            continue
        match = _LEADING_KEY.match(row[column])
        if match is not None:
            keys.add(match.group(1))
    return keys


def _registry_keys() -> set[str]:
    """`config/models.yaml`이 등재한 model key. 로더가 읽는 것과 같은 방식으로 읽는다.

    **정규식으로 긁지 않는다.** 이 파일은 주석이 본문보다 길고 그 주석이 다른 항목의 키
    이름을 여러 번 인용한다(`qwen3-8b` 블록이 `qwen3-4b`를 부르는 식) — 줄 단위로 긁으면
    **주석에 이름이 나온 것만으로 「등재됐다」가 된다.**
    """
    from src.config import CONFIG_DIR

    raw = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))
    models = raw.get("models") if isinstance(raw, dict) else None
    assert isinstance(models, dict) and models, "config/models.yaml에서 models 매핑을 읽지 못했다"
    return set(models)


def test_the_context_roster_matches_the_model_registry() -> None:
    """CONTEXT.md §1의 `registry 등재` 칸은 `config/models.yaml`의 사실과 같아야 한다.

    이 칸이 로스터 표에서 유일하게 **검증 가능한** 열이다. 나머지 두 열(자리·키)은 팀의
    결정이라 기계가 옳고 그름을 말할 수 없지만, "그 키가 registry에 있나"는 파일을 열면
    답이 나온다. 그래서 여기만 잠근다.

    **어긋나면 무슨 일이 나나.** 이 표는 「로스터 확정」과 「그 모델이 선다」를 가르려고
    존재한다(CONTEXT.md §5). 등재를 마친 키가 여기서 계속 미등재로 보이면 읽는 사람이
    쓸 수 있는 모델을 못 쓰고, 반대로 안 받은 모델이 등재로 보이면 **없는 서버를 전제로
    측정 계획을 짠다** — 2026-09-09에 GPU 시간을 잃은 것이 정확히 그 종류의 착오다.

    **`config/models.yaml`을 파싱해서 본다.** 그 파일은 주석이 본문보다 길고 주석이 다른
    항목의 키를 여러 번 부르므로 낱말 검색으로는 등재 여부를 못 가린다(`_registry_keys`).
    """
    header, rows = _first_table(_read(CONTEXT), _ROSTER_HEADING, _ROSTER_TABLE)
    key_at = _column(header, _KEY_COLUMN, _ROSTER_TABLE)
    mark_at = _column(header, _MARK_COLUMN, _ROSTER_TABLE)
    registry = _registry_keys()

    offenders: list[str] = []
    for lineno, row in rows:
        if max(key_at, mark_at) >= len(row):
            offenders.append(f"CONTEXT.md:{lineno}: 셀이 {len(row)}개뿐이라 읽을 수 없다")
            continue

        match = _LEADING_KEY.match(row[key_at])
        if match is None:
            offenders.append(
                f"CONTEXT.md:{lineno}: `{_KEY_COLUMN}` 칸이 백틱 키로 시작하지 않는다 "
                f"— {row[key_at][:60]!r}"
            )
            continue

        key, mark = match.group(1), row[mark_at]
        has_yes, has_no = _YES in mark, _NO in mark
        if has_yes == has_no:
            offenders.append(
                f"CONTEXT.md:{lineno}: `{key}` — `{_MARK_COLUMN}` 칸을 판독할 수 없다"
                f"({mark[:40]!r}). {_YES}나 {_NO} 중 하나만 쓴다"
            )
        elif has_yes and key not in registry:
            offenders.append(
                f"CONTEXT.md:{lineno}: `{key}`를 {_YES}(등재)로 적었는데 "
                f"config/models.yaml에 그 키가 없다. 있는 것: {sorted(registry)}"
            )
        elif has_no and key in registry:
            offenders.append(
                f"CONTEXT.md:{lineno}: `{key}`를 {_NO}(미등재)로 적었는데 "
                f"config/models.yaml에 이미 있다 — 등재했으면 이 칸을 {_YES}로 바꾼다"
            )

    assert offenders == [], (
        "CONTEXT.md §1 로스터 표가 config/models.yaml과 갈렸다.\n"
        "로스터를 바꿀 때 손대는 곳은 CONTEXT.md §3이 넷으로 못박아 뒀다 — "
        "이 표·models.yaml·profiles.yaml·ADR 0010.\n" + "\n".join(offenders)
    )


def test_every_registry_model_is_named_in_context() -> None:
    """registry의 모든 키는 CONTEXT.md의 두 표 중 **한 곳에는** 이름이 있어야 한다.

    §1이 자리를 준 키와 「로스터에 없지만 registry에 남는 것」이 사유를 준 키, 그 둘의
    합집합이 `config/models.yaml`을 덮는다. 덮지 못하는 키는 **아무 문서도 설명하지 않는
    모델**이고, 그것을 프로필에 배선한 사람과 나중에 읽는 사람이 서로 다른 것을 안다.

    이 저장소는 registry 키를 지우지 않는다(ADR 0010 「되돌리는 비용」). 그래서 로스터에서
    내려온 키가 registry에 남는 것이 정상이고, **정상인 것과 잊힌 것을 가르는 유일한 자리가
    그 두 번째 표다.** 표가 비면 둘을 못 가른다.

    **양방향으로 본다.** 두 번째 표가 "registry에 남는다"고 적은 키가 실제로는 지워졌다면
    그 표가 거짓말이고, 특히 `qwen3-reranker-0.6b`는 그 표에서 **지우지 않는다**고 명시된
    대조 기준이다 — 조용히 사라지면 4B와의 대조가 근거를 잃는다.
    """
    text = _read(CONTEXT)
    roster_header, roster_rows = _first_table(text, _ROSTER_HEADING, _ROSTER_TABLE)
    kept_header, kept_rows = _first_table(text, _KEPT_HEADING, _KEPT_TABLE)

    named = _keys_in(roster_rows, _column(roster_header, _KEY_COLUMN, _ROSTER_TABLE))
    kept = _keys_in(kept_rows, _column(kept_header, _KEPT_KEY_COLUMN, _KEPT_TABLE))
    registry = _registry_keys()

    missing = sorted(registry - named - kept)
    orphan = sorted(kept - registry)

    assert not missing, (
        f"config/models.yaml에 있는데 CONTEXT.md가 이름을 대지 않는 키: {missing} — "
        "자리를 줬으면 §1 표에, 안 줬으면 「로스터에 없지만 registry에 남는 것」에 "
        "사유와 함께 넣는다"
    )
    assert not orphan, (
        f"CONTEXT.md가 「registry에 남는다」고 적었는데 config/models.yaml에 없는 키: {orphan} — "
        "registry 키는 지우지 않는다(ADR 0010). 정말 지웠다면 그 행도 같이 지운다"
    )


_REWIRE_MARKER = "`config/profiles.yaml` 재배선"
_POOL_ROLES = ("scout_memory", "scout_boundary", "scout_injection")


def test_context_declares_that_profiles_are_not_rewired_yet() -> None:
    """`config/profiles.yaml`이 §1 배정을 안 따를 때, CONTEXT.md §5가 그 사실을 들어야 한다.

    **왜 이 검사가 있나.** CONTEXT.md §3이 `config/profiles.yaml`을 「로스터를 바꿀 때 손대는
    넷」에 넣는다. 그러니 정본만 읽은 사람은 그 파일이 §1과 같다고 읽는다 — **지금은 다르다.**
    프로필 전부가 아직 단일 `scout`이고 `critic`은 `devstral-small-2-24b`다. 그 어긋남을 정본이
    안 들면, 정본을 먼저 읽으라는 `CLAUDE.md` §7의 지시가 오히려 오독을 만든다.

    **이 검사는 양방향으로 돈다.**

    - 재배선이 아직이면 → §5에 그 줄이 있어야 한다. 없으면 실패한다.
    - 재배선이 끝나면 → 그 줄이 **거짓이 되므로** 실패해서 지우게 만든다.

    후자가 이 검사의 값이다. 「아직 안 된 것」을 적은 문장은 되는 순간 거짓이 되는데,
    그것을 사람이 기억해서 지우기를 기대하지 않는다 — 이 저장소가 §2.3으로 반복해서
    밟은 자리다.

    ⚠ **이 검사는 「자리 ↔ 모델」 짝을 잠그지 않는다.** 잠그는 것은 「풀 역할이 프로필에
    쓰였나」 하나다. 로스터가 옛 배정으로 되돌아가는 것 자체는 여전히 사람이 본다 —
    §1 표의 세 열 중 기계가 답할 수 있는 것은 `registry 등재`뿐이기 때문이다
    (`test_the_context_roster_matches_the_model_registry`의 docstring).
    """
    from src.config import CONFIG_DIR

    raw = yaml.safe_load((CONFIG_DIR / "profiles.yaml").read_text(encoding="utf-8"))
    profiles = raw.get("profiles") if isinstance(raw, dict) else None
    assert isinstance(profiles, dict) and profiles, (
        "config/profiles.yaml에서 profiles 매핑을 읽지 못했다"
    )

    rewired = sorted(
        name
        for name, entry in profiles.items()
        if isinstance(entry, dict)
        and any(role in (entry.get("roles") or {}) for role in _POOL_ROLES)
    )
    declared = _REWIRE_MARKER in _read(CONTEXT)

    if rewired and declared:
        raise AssertionError(
            f"프로필 {rewired}이(가) Scout 풀로 재배선됐는데 CONTEXT.md §5가 아직 "
            f"「{_REWIRE_MARKER} — 안 됐다」를 들고 있다. 그 줄은 이제 거짓이다. 지워라."
        )
    if not rewired and not declared:
        raise AssertionError(
            f"프로필 어느 것도 Scout 풀을 안 쓰는데 CONTEXT.md에 「{_REWIRE_MARKER}」 줄이 없다. "
            "정본만 읽은 사람은 profiles.yaml이 §1과 같다고 읽는다 — §5에 그 어긋남을 적어라."
        )
