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

import ast
import difflib
import functools
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
import yaml

from tests.conftest import tracked_files

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

# `YYYY-MM-DD-<주제>-<장비>.md`의 **모양**만 본다 — 날짜 접두사 · 주제-장비 두 자리(하이픈으로
# 갈린 둘 이상) · `.md`. `--device` 행이 없는 보고서도 이 모양은 지켜야 한다. 주제는 검사하지 않는다
# (아래 주석 참조). **장비는 여기서 안 뽑는다**: 장비 이름이 하이픈을 들 수 있어(`vastai-remote`)
# 파일명만으로는 주제와 장비의 경계를 못 긋는다 — 끝 토큰만 장비로 읽던 판은 어떤 파일명도
# `vastai-remote`와 같아지게 할 수 없었다. 대조는 본문 `--device`를 알고 나서
# `_name_carries_device`가 한다.
_REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+-.+)\.md$")

# 보고서 머리 메타 표의 `--device` 행. TEMPLATE.md가 정한 서식이다.
_DEVICE_ROW = re.compile(r"^\|\s*`--device`\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)


# 이 수집기는 `tests/conftest.py`의 `tracked_files`로 옮겼다 — 전수 검사 셋이 `rglob`로 걸어서
# `.claude/worktrees/<이름>/`의 **남의 낡은 문서**를 이 저장소의 결함으로 보고한 2026-10-12 사고
# 뒤, 「무엇을 훑는가」의 단일 출처가 필요해졌다. 이름은 그대로 둔다 — 이 파일이 열두 곳에서
# 부르고, 아래 docstring들이 `_tracked()`를 이름으로 든다.
_tracked = tracked_files


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


# 소수 10자리 이상 — 파이썬이 float를 그대로 문자열로 낼 때만 나오는 자릿수다(배정밀도의
# 유효숫자가 그쯤에서 끝난다). **임계가 10인 것은 의도다.** 6~9자리는 도구가 그렇게 찍은
# 값을 그대로 옮긴 것이라 §0상 정당하다 — `2026-08-25-serving-nvfp4-atom.md`의 `45.698446`
# 이 그렇다. 그 둘을 한 규칙으로 묶으면 **실측 전사를 노이즈로 몰아** 보고서를 깎게 된다.
_RAW_FLOAT = re.compile(r"\d+\.\d{10,}")


def test_the_raw_float_regex_actually_matches_a_raw_float() -> None:
    """정규식이 안 물면 아래 검사가 **빈 채로 초록이 된다** — 이 파일이 이미 겪은 실패다."""
    assert _RAW_FLOAT.search("| `362` / `6241.860184431076 s` |")
    assert not _RAW_FLOAT.search("Joern 4.0.605")  # 버전은 3자리다
    assert not _RAW_FLOAT.search("`120.0036`초")  # 제한 도달 값은 4자리라 통과한다
    assert not _RAW_FLOAT.search("45.698446")  # 도구가 찍은 6자리 전사는 결함이 아니다


def test_reports_do_not_carry_raw_float_repr() -> None:
    """보고서에 **측정하지 않은 정밀도**가 실리지 않는다.

    초 단위 경과를 17자리로 실으면 읽는 사람에게 펨토초를 쟀다고 말하는 셈이다. §0이
    *"수치를 쓸 때는 어디서 나왔는지를 같은 문장에 적는다"* 라 한 자리에서, 출처가 아니라
    **자릿수**가 거짓말을 한다.

    **왜 사람이 아니라 기계가 세는가.** `8cf5cfb`가 이 정리를 하면서 커밋 메시지에
    *"그 패턴이 나오는 자리 전부에서 없앤다"* 라고 적었는데 **같은 표의 바로 윗 네 행(값
    여섯 개)이 그대로 남아 있었다.** 눈으로 훑어 「전부」를 주장한 것이 틀린 자리다(§2.2).

    자릿수만 본다 — 반올림이 맞는지, 값이 옳은지는 이 검사의 범위가 아니다.
    """
    noisy = [
        f"{_rel(path)}:{lineno}: {hit} — 소수 10자리 이상은 float repr이지 측정 정밀도가 아니다"
        for path in _reports()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for hit in _RAW_FLOAT.findall(line)
    ]
    assert noisy == [], (
        f"측정하지 않은 정밀도 {len(noisy)}건:\n" + "\n".join(noisy)
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


# `config/*.yaml` 주석이 가리키는 저장소 문서 — `docs/`로 시작하는 `.md`. 앞 글자가 경로 글자면
# 다른 경로(외부 URL 포함)의 가운데라 저장소 루트의 `docs/`가 아니다.
_CONFIG_DOC_PATH = re.compile(r"(?<![\w/.-])(docs/[\w./-]+\.md)")


def test_config_comments_point_at_a_real_doc() -> None:
    """`config/*.yaml` 주석이 든 `docs/…md` 경로는 추적되는 파일이어야 한다.

    **사고 —** PR #262(base main)가 `config/models.yaml` 머리 문단에서 ATOM 스모크 보고서를
    가리켰는데 그 보고서는 짝 PR #263만 들여왔다. 머지 순서를 강제하는 장치가 없어 config가 없는
    파일을 가리킨 채 main에 들어갈 수 있었다(@sumin0218 리뷰 MEDIUM, 2026-09-24). 위 링크 검사는
    마크다운만 읽어 YAML 주석을 못 본다. 추적 목록(`git ls-files`)과 대조하므로 긴 워크트리 경로의
    `Path.exists()` 오판에는 안 걸린다.
    """
    # 양성 핀 — 실형태: `config/models.yaml`의 gpt-oss 기동 블록 2026-09-20 문단 원천 줄.
    assert _CONFIG_DOC_PATH.search(
        "#   원천은 `docs/experiments/harness/2026-09-11-gpt-oss-structured-vocab-flag-atom.md`의"
    )
    # 음성 핀 — 외부 문서 URL의 `/docs/` 경로 조각. config 주석이 외부 URL을
    # 인용하는 꼴은 실제로 온다 — 같은 스택의 #266이 `config/models.yaml`
    # `qwen3-14b` 항목에 `GET https://huggingface.co/api/…`를 들인다.
    assert not _CONFIG_DOC_PATH.search(
        "    # https://github.com/vllm-project/vllm/blob/main/docs/usage/faq.md"
    )
    tracked = {p.relative_to(REPO_ROOT).as_posix() for p in _tracked()}
    missing = [
        f"{_rel(path)}:{lineno}: `{target}` 가 가리키는 파일이 없다"
        for path in _tracked("config/*.yaml")
        for lineno, line in enumerate(_read(path).splitlines(), 1)
        for target in _CONFIG_DOC_PATH.findall(line)
        if target not in tracked
    ]
    assert missing == [], "\n".join(missing)


def _name_carries_device(name: str, device: str) -> bool:
    """파일명이 `YYYY-MM-DD-<주제>-<장비>.md`이고 그 장비가 `device`인가.

    **장비는 접미사로 묻는다.** 장비 이름이 하이픈을 들 수 있어(`vastai-remote`) 파일명만 보고
    장비를 뽑을 수 없다 — 본문 `--device`를 알고 나서 「주제 다음에 `-<장비>`로 끝나는가」를
    본다. 주제가 비면(`2026-10-04-vastai-remote.md`) 거짓이다.

    ponytail: 장비 `remote`인 보고서가 `…-vastai-remote.md`로도 통과한다(접미사 대조의 한계).
    장비 이름이 서로의 하이픈 접미사가 되는 일이 생기면 `config/serving/*.yaml`의 stem 집합과
    대조하도록 올린다 — 그 집합은 두 번째 진실 원천이라 지금은 안 짠다(위 docstring 경고).
    """
    match = _REPORT_NAME.match(name)
    return match is not None and match.group(2).endswith(f"-{device}")


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
        if _REPORT_NAME.match(report.name) is None:
            offenders.append(f"{_rel(report)}: 파일명이 `YYYY-MM-DD-<주제>-<장비>.md`가 아니다")
            continue

        declared = _DEVICE_ROW.search(_read(report))
        if declared is None:
            # 메타 표에 `--device` 행이 없는 것은 별개 문제다(TEMPLATE.md 미준수).
            # 여기서 단언하면 두 실패가 한 메시지에 섞인다.
            continue
        if not _name_carries_device(report.name, declared.group(1)):
            offenders.append(
                f"{_rel(report)}: 파일명이 `-{declared.group(1)}.md`로 끝나지 않는다 — "
                f"본문 `--device`는 `{declared.group(1)}`이다"
            )

    assert offenders == [], "\n".join(offenders)


def test_the_report_name_check_reads_a_hyphenated_device() -> None:
    """하이픈이 든 장비(`vastai-remote`)도 파일명으로 대조된다 — 끝 토큰만 읽던 판은 못 읽었다.

    **사고 —** 2026-10-04 `vastai-remote`로 돈 run의 보고서가 이 검사에 걸렸는데 파일명을
    아무리 바꿔도 안 풀렸다. 장비 그룹이 `[a-z0-9]+`라 어디서 끊어도 `remote`가 나와 **어떤
    파일명도 `vastai-remote`와 같아질 수 없었다**(본문 `--device`를 바꾸는 길뿐). 이 장비
    이름은 `config/serving/vastai-remote.yaml`의 stem이고 `--device`가 그것을 고른다.

    핀의 출처 — 양성: 이 보고서의 파일명, 단일 토큰 장비는 기존 보고서의 것(`harness/`).
    음성: 첫 판의 파일명(`…-win11-vast.md` — 끝 토큰이 `vast`라 `vastai-remote`가 아니다)과,
    그 문형이 실제로 나올 수 있는 둘 — 날짜 없는 이름, 주제 없이 장비만 있는 이름.
    """
    hyphenated = "2026-10-04-cybergym-operational-run-vastai-remote.md"
    assert _REPORT_NAME.match(hyphenated)
    assert _REPORT_NAME.match("2026-08-18-serving-atom.md")
    assert not _REPORT_NAME.match("cybergym-operational-run-vastai-remote.md")  # 날짜 없음
    assert not _REPORT_NAME.match("2026-10-05-foo.md")  # 주제-장비 두 자리가 아니다

    assert _name_carries_device(hyphenated, "vastai-remote")
    assert _name_carries_device("2026-08-18-serving-atom.md", "atom")
    first_edition = "2026-10-04-cybergym-operational-run-win11-vast.md"  # 끝 토큰 `vast`
    assert not _name_carries_device(first_edition, "vastai-remote")
    assert not _name_carries_device(hyphenated, "atom")
    assert not _name_carries_device("2026-10-04-vastai-remote.md", "vastai-remote")  # 주제가 비었다


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

    **2026-09-12 — D46 (c)의 자리가 열렸다. 이 검사는 아직 안 바꾼다.**
    `thresholds.output_max_by_role`이 생겼지만 **어느 역할도 선언하지 않았으므로** 모든
    역할의 실효 상한이 `thresholds.output_max`와 같고, 이 검사가 비교하는 것이 곧 그
    값이다. 지금 바꾸면 **아무것도 안 지키는 검사**가 된다.

    ⚠ **값을 처음 넣는 PR이 여기를 함께 고친다.** 그때 비교 대상은 전역값이 아니라
    `thresholds.output_max_for(<그 예시 블록의 role>)`이다 — 문서의 `agent:` 예시는
    `role:`을 같은 블록에 들고 있으므로 역할과 숫자를 **짝으로** 뽑아야 한다. 지금처럼
    전역값 하나와 대조하면, critic에 8,192를 주고 문서에 4,096을 적어도 통과한다.

    **`harness_pool`을 쓰는 것은 아무 프로필이나 된다는 뜻이다**(2026-09-24 전에는 지운
    `b1_single`이었다). `config/budgets.yaml`이
    "thresholds·sampling·seed는 프로필과 무관하게 공통"이라고 못박아 뒀다. 그 전제가
    깨지면 이 테스트는 한 프로필만 보게 되므로, 그때는 여기도 같이 고쳐야 한다.

    **2026-09-26 — 첫 역할값이 들어와 역할 짝 비교로 고쳤다.** Auditor가 사고를 켜며
    `output_max_by_role.auditor`가 32,768이 됐다(`config/budgets.yaml`). 위 ⚠ 문단대로 비교
    대상이 전역값이 아니라 `thresholds.output_max_for(<그 예시 블록의 role>)`이 됐고, 그 날을
    기다리던 가드(`test_the_contract_guard_goes_red_the_day_a_role_cap_is_declared`)는 할 일을
    마쳐 지웠다 — 짝 비교가 선 것은 바로 아래 검사가 잠근다.
    """
    from src.config import load_config

    # 핀 — 역할과 짝을 짓는가(양성), 역할 없는 블록을 역할 없음으로 두는가(음성).
    assert _contract_caps("```yaml\nagent:\n  role: critic\n    output_max: 8192\n```") == [
        ("critic", 8192)
    ]
    assert _contract_caps("```yaml\ntoken_budget:\n  output_max: 4096\n```") == [(None, 4096)]

    caps = _contract_caps(_read(REPO_ROOT / "docs" / "agent-contracts.md"))
    assert caps, "docs/agent-contracts.md에 output_max 선언이 없다"

    thresholds = load_config("harness_pool").thresholds
    offenders = []
    for role, cap in caps:
        actual = thresholds.output_max_for(role) if role else thresholds.output_max
        if cap != actual:
            offenders.append(f"role={role}: 문서 {cap} · config {actual}")
    assert offenders == [], (
        "docs/agent-contracts.md의 output_max 선언이 그 블록 역할의 config 값과 다르다 — "
        "둘을 함께 고친다\n" + "\n".join(offenders)
    )


def _contract_caps(text: str) -> list[tuple[str | None, int]]:
    """계약 문서의 yaml 예시 블록마다 (그 블록의 `role:`, `output_max:`) 짝. 역할이 없으면 None."""
    caps: list[tuple[str | None, int]] = []
    for block in re.findall(r"```yaml\n(.*?)```", text, re.DOTALL):
        role = re.search(r"^\s*role:\s*([a-z_]+)", block, re.MULTILINE)
        caps += [
            (role.group(1) if role else None, int(cap.group(1)))
            for cap in re.finditer(r"^\s*output_max:\s*(\d+)", block, re.MULTILINE)
        ]
    return caps


def test_the_contract_check_pairs_the_cap_with_the_blocks_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """위 검사는 **그 블록 역할의 값**과 대조한다 — 전역값만 흔들면 통과, 역할값을 흔들면 죽는다.

    전역값 하나와 대조하던 동안에는 critic에 8,192를 주고 문서에 4,096을 적어도 통과했다(D42가
    나온 드리프트가 한 겹 아래에서 재현되는 모양). 그 날을 기다리던 옛 가드는 2026-09-26 첫
    역할값(Auditor)과 함께 지웠고, 이 검사가 짝 비교의 결과를 잠근다.
    """
    from src import config as config_module

    loaded = config_module.load_config("harness_pool")
    moved_global = loaded.transition(thresholds=loaded.thresholds.transition(output_max=2048))
    monkeypatch.setattr(config_module, "load_config", lambda *args, **kwargs: moved_global)
    test_the_contract_example_output_max_matches_the_config()  # 전역값은 비교 대상이 아니다

    moved_role = loaded.transition(
        thresholds=loaded.thresholds.transition(
            output_max_by_role={**loaded.thresholds.output_max_by_role, "auditor": 16384}
        )
    )
    monkeypatch.setattr(config_module, "load_config", lambda *args, **kwargs: moved_role)
    with pytest.raises(AssertionError, match="role=auditor"):
        test_the_contract_example_output_max_matches_the_config()


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
# D63(리랭커 순위를 우리 입력 모양에서 재지 않았다)은 2026-09-20에 더했다. 구축안이 그것을
# **이 저장소의 D63**으로 인용한다 — §1 정정 16, §5 단계별의 3단계 검색 블록, §8 행 2.2. 전부
# 리랭커 자리를 서술하며 그 항목의 상태(`열림`)를 든다. 파인튜닝 라벨이 아니다.
# ⚠ **개수는 적지 않는다** — 세는 자리는 문서이고([CLAUDE.md](../CLAUDE.md) §7), 이 주석에 수를
# 박으면 인용이 늘 때 조용히 틀린다. 실제로 첫 판이 절 번호를 틀리게 적었다.
_BUILD_PLAN_D_CITATIONS = frozenset({"D9", "D13", "D25", "D30", "D51", "D63"})

# 파인튜닝 단계 라벨. 앞뒤 경계를 보는 이유는 `_D_REF`와 같다.
_FT_REF = re.compile(r"(?<![A-Za-z0-9])FT(\d+)(?![0-9A-Za-z])")

# 취소선. **죽은 것을 지우지 않고 취소선으로 남기는** 것이 이 저장소의 관례다
# (ADR 0010 §2의 죽은 행·`docs/open-questions.md`의 해소된 D 제목). 번호를 재사용하지
# 않는 것과 같은 이유 — 회의록·PR이 그 라벨로 참조한다.
#
# **`DOTALL`이다** — 목록 항목의 취소선은 줄을 넘어간다(`- ~~첫 줄…\n  둘째 줄~~`).
# 줄 단위로 보면 어느 쪽도 짝이 안 맞아 **멀쩡히 죽인 것을 살아 있다고 잡는다.**
_STRUCK = re.compile(r"~~(.+?)~~", re.DOTALL)

# 날짜가 박힌 줄. 이 저장소의 정정은 전부 `2026-09-13 —` 꼴을 달고 온다(ADR·D 본문·
# PROJECT-CONTEXT.md). **죽은 라벨을 이름으로 부르며 "사라졌다"고 적는 정정문은 통과해야 한다** —
# 그것을 못 쓰게 하면 왜 죽었는지가 문서에서 사라진다. 날짜 없는 현재형 주장은 그대로 걸린다.
_DATED_LINE = re.compile(r"20\d{2}-\d{2}-\d{2}")

# 아래 FT 검사에서 빼는 절. **날짜가 박힌 정정 기록**이라 고치면 그날의 사실이 지워진다 —
# 그 표는 "원문이 파인튜닝을 D 계열로 불렀고 FT1·FT2·FT3으로 바꿨다"는 **이름 이력**이지
# "FT1이 지금 산다"는 주장이 아니다. `_BUILD_PLAN_D_CITATIONS`와 같은 논법의 좁은 예외이고,
# 넓힐 때는 그 절이 정말 기록인지 문서에서 확인하고 넣는다.
_BUILD_PLAN_FT_EXEMPT_SECTION = "## 1. 합의 원문에서 고친 것"

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


# 모델 자리가 없는 단계. PROJECT-CONTEXT.md §2가 슬라이싱을 **무모델 결정론**으로 확정해
# 0·1단계에서 모델 자리가 사라졌다(2026-09-13). 자리가 되살아나면 **이 집합을 고치는 것이**
# 그 결정을 기록하는 자리다 — 검사를 지우는 것이 아니라.
_STAGES_WITHOUT_A_MODEL = ("1",)

# 메모리 표의 상주 행. 첫 셀이 `1 · 쿼리 생성` 처럼 「번호 · 이름」이고 둘째 셀이 상주 모델이다.
_MEMORY_ROW = re.compile(r"^\|\s*(\d+)\s*·[^|]*\|([^|]*)\|")

# 메모리 표를 여는 줄. 두 문서가 서로 다른 머리말을 쓴다.
_MEMORY_HEADER = re.compile(r"^\|\s*단계\s*\|\s*상주")


def test_no_memory_table_lists_a_resident_model_for_a_stage_that_has_no_model_slot() -> None:
    """모델 자리를 지운 단계가 **메모리 표에 상주 모델을 들고** 있으면 안 된다.

    **왜 이 검사가 있나 — 실제로 놓쳤다.** PR #196이 1단계 모델 자리(쿼리 생성기)를
    걷어내며 ADR 0010 §2 로스터 표(95행)와 「리뷰에서 정할 것」 표(218행)를 **둘 다**
    고쳤는데, 그 사이에 낀 §결과의 메모리 표(187행)만 빠졌다:

        | 1 · 쿼리 생성 | `qwen3-8b` | 6.38 | 실측 |

    취소선도 날짜도 없는 **현재형**이라, 같은 파일이 *"1단계에 모델 자리 없음"*과
    *"1단계에 `qwen3-8b`가 6.38 GiB로 상주한다"*를 동시에 주장했다.

    **놓친 이유가 정확히 특정된다.** 두 차례의 자기 적대적 리뷰가 `쿼리 생성기`로 grep했는데
    이 행은 `쿼리 생성`(기 없음)이라 그 패턴을 피해갔다. CLAUDE.md §2.3이 사람에게 맡긴
    「전수」가 **문자열 패턴에 의존해서** 실패한 자리다 — 사람이 고른 패턴이 아니라 **표의
    구조**로 세면 피해갈 수 없다.

    형제 검사(`..._fine_tuning_stage_whose_model_slot_it_removed`)와 같은 사고의 다른 얼굴이다.
    그쪽은 §2 자리를 지우고 §6·§8을 안 고친 것, 이쪽은 §2를 고치고 §결과를 안 고친 것이다.
    """
    stale = []
    for path in (BUILD_PLAN, DECISIONS / "0010-harness-structure-and-model-roster.md"):
        text = _read(path)
        # 취소선은 「죽었다」는 표시다 — 줄·열을 보존하며 공백으로 덮어 살아 있는 것만 남긴다.
        masked = _STRUCK.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
        inside = False
        for lineno, line in enumerate(masked.splitlines(), 1):
            if _MEMORY_HEADER.match(line):
                inside = True
                continue
            if inside and not line.startswith("|"):
                inside = False
            if not inside:
                continue
            m = _MEMORY_ROW.match(line)
            if m and m.group(1) in _STAGES_WITHOUT_A_MODEL and m.group(2).strip():
                stale.append(
                    f"{_rel(path)}:{lineno}: {m.group(1)}단계에 모델 자리가 없는데 "
                    f"메모리 표가 상주 모델 `{m.group(2).strip()}` 를 현재형으로 든다 — "
                    "취소선(~~...~~)으로 죽이고 사유를 적는다"
                )
    assert stale == [], "\n".join(stale)


def test_the_build_plan_does_not_keep_a_fine_tuning_stage_whose_model_slot_it_removed() -> None:
    """§2 표에서 모델 자리를 지운 파인튜닝 단계가 문서 다른 곳에 살아 있으면 안 된다.

    **왜 이 검사가 있나.** §2 표의 한 행이 **모델 자리와 그 자리의 파인튜닝을 같은 줄에**
    든다. 자리가 사라지면 그 파인튜닝도 대상을 잃는데, **그 셀 하나만 고치고 §6 표와 §8
    로드맵을 안 고쳐도 아무것도 실패하지 않는다.**

    2026-09-13에 실제로 그랬다. 슬라이싱이 무모델로 확정되며 자리 ①이 사라졌고 §2 표의
    `FT1`을 취소선으로 죽였는데, §6이 `| FT1 | 인덱서 (단계 1) | …`를 그대로 들고 §8
    로드맵이 *"인덱서 best-of-N **쿼리**를 켠다 · FT1 데이터가 쌓이기 시작"*을 들고 있었다 —
    **걷어낸 설계(모델이 쓰는 쿼리)가 계획에 현재형으로 살아 있었다.** 적대적 리뷰가 같은
    표의 다른 자리(⑤)는 잡고 이것은 놓쳤다. CLAUDE.md §2.3이 사람에게 맡긴 자리이고,
    사람이 한 번 놓쳤으므로 기계에 넘긴다.

    **통과 조건은 「지우기」가 아니라 「취소선」이다** — 번호를 재사용하지 않는 것과 같은
    이유다(`docs/open-questions.md` 관리 방식). 지우면 회의록·PR이 그 라벨로 참조할 때
    무엇이었는지 사라진다.
    """
    text = _read(BUILD_PLAN)
    lines = text.splitlines()
    # 취소선을 **문서 전체에서** 공백으로 덮는다. 줄 수와 열 위치를 보존해야 아래에서
    # 줄 번호를 그대로 쓸 수 있다 — 그래서 개행만 남기고 나머지를 공백으로 바꾼다.
    masked = _STRUCK.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text).splitlines()

    section_of: list[str] = []
    current = ""
    for line in lines:
        if line.startswith("## "):
            current = line.strip()
        section_of.append(current)

    summary_lines = {
        i
        for i, line in enumerate(lines)
        if section_of[i].startswith("## 2.") and line.startswith("|")
    }
    assert summary_lines, (
        "docs/build-plan.md §2에서 모델 자리 표를 찾지 못했다 — 표 서식이나 절 제목이 "
        "바뀌었다면 이 검사도 함께 고친다"
    )

    declared: set[str] = set()
    live: set[str] = set()
    for i in summary_lines:
        declared.update(f"FT{n}" for n in _FT_REF.findall(lines[i]))
        live.update(f"FT{n}" for n in _FT_REF.findall(masked[i]))
    # 취소선 안에만 있던 라벨이 죽은 것. 한 행에서 죽고 다른 행에서 살아 있으면 산 쪽이 이긴다.
    dead = declared - live

    assert declared, (
        "docs/build-plan.md §2 표가 파인튜닝 라벨을 하나도 들지 않는다 — 표가 바뀐 것이다"
    )

    offenders: list[str] = []
    for lineno, line in enumerate(masked):
        if (
            lineno in summary_lines
            or section_of[lineno] == _BUILD_PLAN_FT_EXEMPT_SECTION
            or _DATED_LINE.search(line)
        ):
            continue
        for number in _FT_REF.findall(line):
            if f"FT{number}" in dead:
                offenders.append(
                    f"docs/build-plan.md:{lineno + 1}: FT{number} — §2 표가 그 모델 자리를 "
                    "지웠는데 여기서는 현재형으로 살아 있다. 죽었다고 적으려면 취소선을 "
                    "씌우거나 그 줄에 정정 날짜를 단다"
                )

    assert offenders == [], (
        "구축안이 **자리가 사라진** 파인튜닝 단계를 계획에 그대로 들고 있다 "
        "(CLAUDE.md §2.3 — 지운 것을 살아 있는 소스가 가리키지 않는다).\n"
        + "\n".join(offenders)
    )


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


# --- 정본 선언(PROJECT-CONTEXT.md) ↔ 모델 registry ------------------------------------
#
# PROJECT-CONTEXT.md는 스스로를 로스터의 **정본**이라고 선언한다. 선언만으로는 아무것도 강제되지
# 않아서 아래 둘이 그 선언을 기계가 읽는 불변식으로 바꾼다 — 두 파일이 조용히 갈라지는 것,
# 그러니까 PROJECT-CONTEXT.md가 존재하는 이유 그 자체를 막는다.
#
# **판정은 표 구조로만 한다. 낱말로 하지 않는다.** `미등재`·`신규`는 이 저장소에서 다른
# 뜻으로도 쓰인다(`docs/build-plan.md` §9의 R 행, 실험 보고서의 "신규 결함"). 낱말을 키로
# 삼으면 상관없는 문서가 그 낱말을 옮길 때마다 여기가 깨지고, 오탐을 내는 검사는 꺼진다.

CONTEXT = REPO_ROOT / "PROJECT-CONTEXT.md"

# 표를 여는 절 제목. **번호가 아니라 제목으로 찾는다** — 앞에 절이 하나 끼는 날 번호로
# 찾으면 조용히 다른 표를 읽는다.
_ROSTER_HEADING = re.compile(r"^##\s+\d+\.\s+모델 로스터")
_KEPT_HEADING = re.compile(r"^###\s+로스터에 없지만 registry에 남는 것")

_ROSTER_TABLE = "PROJECT-CONTEXT.md 로스터 표(§1)"
_KEPT_TABLE = "PROJECT-CONTEXT.md 「로스터에 없지만 registry에 남는 것」 표"

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
    이름을 여러 번 인용한다(`qwen3-14b` 블록이 지운 `deepseek-r1-distill-qwen-32b`를
    부르는 식) — 줄 단위로 긁으면 **주석에 이름이 나온 것만으로 「등재됐다」가 된다.**
    """
    from src.config import CONFIG_DIR

    raw = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))
    models = raw.get("models") if isinstance(raw, dict) else None
    assert isinstance(models, dict) and models, "config/models.yaml에서 models 매핑을 읽지 못했다"
    return set(models)


def test_the_context_roster_matches_the_model_registry() -> None:
    """PROJECT-CONTEXT.md §1의 `registry 등재` 칸은 `config/models.yaml`의 사실과 같아야 한다.

    이 칸이 로스터 표에서 유일하게 **검증 가능한** 열이다. 나머지 두 열(자리·키)은 팀의
    결정이라 기계가 옳고 그름을 말할 수 없지만, "그 키가 registry에 있나"는 파일을 열면
    답이 나온다. 그래서 여기만 잠근다.

    **어긋나면 무슨 일이 나나.** 이 표는 「로스터 확정」과 「그 모델이 선다」를 가르려고
    존재한다(PROJECT-CONTEXT.md §5). 등재를 마친 키가 여기서 계속 미등재로 보이면 읽는 사람이
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
            offenders.append(f"PROJECT-CONTEXT.md:{lineno}: 셀이 {len(row)}개뿐이라 읽을 수 없다")
            continue

        match = _LEADING_KEY.match(row[key_at])
        if match is None:
            offenders.append(
                f"PROJECT-CONTEXT.md:{lineno}: `{_KEY_COLUMN}` 칸이 백틱 키로 시작하지 않는다 "
                f"— {row[key_at][:60]!r}"
            )
            continue

        key, mark = match.group(1), row[mark_at]
        has_yes, has_no = _YES in mark, _NO in mark
        if has_yes == has_no:
            offenders.append(
                f"PROJECT-CONTEXT.md:{lineno}: `{key}` — `{_MARK_COLUMN}` 칸을 판독할 수 없다"
                f"({mark[:40]!r}). {_YES}나 {_NO} 중 하나만 쓴다"
            )
        elif has_yes and key not in registry:
            offenders.append(
                f"PROJECT-CONTEXT.md:{lineno}: `{key}`를 {_YES}(등재)로 적었는데 "
                f"config/models.yaml에 그 키가 없다. 있는 것: {sorted(registry)}"
            )
        elif has_no and key in registry:
            offenders.append(
                f"PROJECT-CONTEXT.md:{lineno}: `{key}`를 {_NO}(미등재)로 적었는데 "
                f"config/models.yaml에 이미 있다 — 등재했으면 이 칸을 {_YES}로 바꾼다"
            )

    assert offenders == [], (
        "PROJECT-CONTEXT.md §1 로스터 표가 config/models.yaml과 갈렸다.\n"
        "로스터를 바꿀 때 손대는 곳은 PROJECT-CONTEXT.md §3이 넷으로 못박아 뒀다 — "
        "이 표·models.yaml·profiles.yaml·ADR 0010.\n" + "\n".join(offenders)
    )


def test_every_registry_model_is_named_in_context() -> None:
    """registry의 모든 키는 PROJECT-CONTEXT.md의 두 표 중 **한 곳에는** 이름이 있어야 한다.

    §1이 자리를 준 키와 「로스터에 없지만 registry에 남는 것」이 사유를 준 키, 그 둘의
    합집합이 `config/models.yaml`을 덮는다. 덮지 못하는 키는 **아무 문서도 설명하지 않는
    모델**이고, 그것을 프로필에 배선한 사람과 나중에 읽는 사람이 서로 다른 것을 안다.

    2026-09-24까지 이 저장소는 registry 키를 지우지 않았다(ADR 0010 「되돌리는 비용」). 그날
    로스터 밖 키를 지우도록 규칙을 고쳤다(PROJECT-CONTEXT.md §1 끝) — 그래도 **로스터에 없이
    남는 키(인프라 `fake`·`fake_v2`, 그리고 선정됐으나 배선 전인 키)의 사유를 적는 자리는 그 두 번째
    표다.** 표가 비면 남긴 것과 잊힌 것을 못 가른다.

    **양방향으로 본다.** 두 번째 표가 "registry에 남는다"고 적은 키가 실제로는 지워졌다면
    그 표가 거짓말이다 — 키를 지우는 PR은 그 행도 함께 지운다. (옛 표는 `qwen3-reranker-0.6b`를
    「지우지 않는 대조 기준」으로 들고 있었고, 2026-09-24 정리가 키와 행을 함께 지웠다.)
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
        f"config/models.yaml에 있는데 PROJECT-CONTEXT.md가 이름을 대지 않는 키: {missing} — "
        "자리를 줬으면 §1 표에, 안 줬으면 「로스터에 없지만 registry에 남는 것」에 "
        "사유와 함께 넣는다"
    )
    assert not orphan, (
        f"PROJECT-CONTEXT.md가 「registry에 남는다」고 적었는데 "
        f"config/models.yaml에 없는 키: {orphan} — "
        "registry 키는 지우지 않는다(ADR 0010). 정말 지웠다면 그 행도 같이 지운다"
    )


_REWIRE_MARKER = "`config/profiles.yaml` 재배선"
_POOL_ROLES = ("scout_memory", "scout_boundary", "scout_injection")


def test_context_declares_that_profiles_are_not_rewired_yet() -> None:
    """`config/profiles.yaml`이 §1 배정을 안 따를 때, PROJECT-CONTEXT.md §5가 그 사실을 들어야 한다.

    **왜 이 검사가 있나.** PROJECT-CONTEXT.md §3이 `config/profiles.yaml`을 「로스터를 바꿀 때
    손대는
    넷」에 넣는다. 그러니 정본만 읽은 사람은 그 파일이 §1과 같다고 읽는다 — **이 검사를 세울
    때는 달랐다.** 프로필 전부가 단일 `scout`이고 `critic`은 `devstral-small-2-24b`였다. 그 어긋남을
    정본이 안 들면, 정본을 먼저 읽으라는 `CLAUDE.md` §7의 지시가 오히려 오독을 만든다.
    (2026-09-24 옛 로스터 프로필을 지워 지금은 실모델 프로필이 §1과 일치한다 — 아래 양방향 중
    「재배선이 끝나면」 쪽이 도는 상태다.)

    **이 검사는 양방향으로 돈다.**

    - 재배선이 아직이면 → §5에 그 줄이 있어야 한다. 없으면 실패한다.
    - 재배선이 끝나면 → 그 줄이 **거짓이 되므로** 실패해서 지우게 만든다.

    후자가 이 검사의 값이다. 「아직 안 된 것」을 적은 문장은 되는 순간 거짓이 되는데,
    그것을 사람이 기억해서 지우기를 기대하지 않는다 — 이 저장소가 §2.3으로 반복해서
    밟은 자리다.

    ⚠ **이 검사는 「자리 ↔ 모델」 짝을 잠그지 않는다.** 잠그는 것은 「풀 역할이 프로필에
    쓰였나」 하나다. 로스터가 옛 배정으로 되돌아가는 것 자체는 여전히 사람이 본다 —
    §1 표의 세 열 중 기계가 답할 수 있는 것은 `registry 등재`뿐이기 때문이다
    (`test_the_context_roster_matches_the_model_registry`의 docstring). (⏵ 2026-09-25 — 그 짝이
    `profiles.yaml`과 같은지는 `test_the_canonical_profiles_seat_the_context_roster`가 잠근다. §1 표
    자체가 옳은지는 여전히 사람이 본다.)
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
            f"프로필 {rewired}이(가) Scout 풀로 재배선됐는데 PROJECT-CONTEXT.md §5가 아직 "
            f"「{_REWIRE_MARKER} — 안 됐다」를 들고 있다. 그 줄은 이제 거짓이다. 지워라."
        )
    if not rewired and not declared:
        raise AssertionError(
            f"프로필 어느 것도 Scout 풀을 안 쓰는데 "
            f"PROJECT-CONTEXT.md에 「{_REWIRE_MARKER}」 줄이 없다. "
            "정본만 읽은 사람은 profiles.yaml이 §1과 같다고 읽는다 — §5에 그 어긋남을 적어라."
        )


# §1 표의 `자리` 열. 백틱 역할 이름이 없는 칸의 낱말 → 프로필 `roles`의 키.
_SEAT_COLUMN = "자리"
_SEAT_LABELS = {
    "임베딩": "embedding", "리랭커": "reranker", "Auditor": "auditor", "Critic": "critic",
}
# §1 로스터를 통째로 배선한 프로필. 검색 경로만 다른 쌍이다(`test_config.py`의 짝 검사).
_CANONICAL_PROFILES = ("harness_pool", "harness_pool_hybrid")


def _seat_role(cell: str) -> str:
    """§1 `자리` 칸 → 프로필 역할 키. 백틱 역할 이름이 있으면 그것, 없으면 `_SEAT_LABELS`의 낱말."""
    named = re.search(r"`([a-z_]+)`", cell)
    return named.group(1) if named else _SEAT_LABELS[cell.split("·", 1)[1].strip()]


def test_the_canonical_profiles_seat_the_context_roster() -> None:
    """§1 표의 자리 ↔ 키가 canonical 프로필(`harness_pool`·`harness_pool_hybrid`)의 `roles`와 같다.

    §1 표가 옳은지는 팀의 결정이라 기계가 말할 수 없다(위 두 검사의 docstring). 그러나
    **`profiles.yaml`이 그 표를 따르는지**는 파일 둘을 열면 답이 나온다. 로스터를 바꿀 때 손대는
    넷(PROJECT-CONTEXT.md §3) 중 이 둘이 갈리면 정본은 새 로스터를 말하는데 run은 옛 로스터로
    돈다. 2026-09-25 `scout_memory` 교체(`seccoderx-8b` → `qwen3-14b`)에서 세웠다.

    모르는 `자리` 낱말이 표에 생기면 `KeyError`로 죽는다 — 새 자리를 조용히 건너뛰지 않는다.
    """
    # 핀 — 양성은 §1 표의 실제 칸 모양이다(4단계 풀 행 · 5단계 행).
    assert _seat_role("4단계 · `scout_injection` (CWE-77·78)") == "scout_injection"
    assert _seat_role("5단계 · Auditor") == "auditor"
    from src.config import CONFIG_DIR

    header, rows = _first_table(_read(CONTEXT), _ROSTER_HEADING, _ROSTER_TABLE)
    seat_at = _column(header, _SEAT_COLUMN, _ROSTER_TABLE)
    key_at = _column(header, _KEY_COLUMN, _ROSTER_TABLE)
    declared = {}
    for lineno, row in rows:
        key = _LEADING_KEY.match(row[key_at])
        assert key is not None, f"PROJECT-CONTEXT.md:{lineno}: 키 칸이 백틱 키로 시작하지 않는다"
        declared[_seat_role(row[seat_at])] = key.group(1)

    raw = yaml.safe_load((CONFIG_DIR / "profiles.yaml").read_text(encoding="utf-8"))
    profiles = raw["profiles"]
    for name in _CANONICAL_PROFILES:
        roles = profiles[name]["roles"]
        assert {role: roles.get(role) for role in declared} == declared, (
            f"config/profiles.yaml `{name}`의 roles가 PROJECT-CONTEXT.md §1 표와 갈렸다 — "
            "로스터를 바꿀 때는 넷(§3)을 함께 고친다"
        )


# §1 키 칸이 적는 추론 등급 — `gpt-oss-20b` (`reasoning_effort: high`)의 괄호.
_KEY_EFFORT = re.compile(r"`reasoning_effort:\s*([a-z]+)`")


def test_the_context_roster_states_the_registry_reasoning_effort() -> None:
    """§1 키 칸의 `reasoning_effort`가 registry의 그 키 선언과 같다 — 양방향.

    §1이 추론 등급을 적는 자리는 키 칸의 괄호다. registry만 바꾸고 표를 놓치면 정본이 옛 등급을
    말하고, 표만 바꾸면 run은 옛 등급으로 돈다. 선언 안 한 키는 표에도 괄호가 없어야 한다 — 「안
    적음」이 「서버 기본값으로 돈다」와 같은 뜻이 되게. 2026-09-26 Auditor를 `qwen3.8-27b`로 바꾸며
    사고를 켤 때(`xhigh`) 세웠다 — 그전에는 Critic 한 칸뿐이라 사람이 봤다.
    """
    # 핀 — 양성은 PROJECT-CONTEXT.md §1 Critic 행의 실제 칸, 음성은 같은 표 memory 행의 실제 칸이다.
    assert _KEY_EFFORT.search("`gpt-oss-20b` (`reasoning_effort: high`)").group(1) == "high"
    assert _KEY_EFFORT.search("`qwen3-14b`") is None
    from src.config import CONFIG_DIR

    header, rows = _first_table(_read(CONTEXT), _ROSTER_HEADING, _ROSTER_TABLE)
    key_at = _column(header, _KEY_COLUMN, _ROSTER_TABLE)
    models = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))["models"]
    offenders: list[str] = []
    for lineno, row in rows:
        key = _LEADING_KEY.match(row[key_at])
        if key is None:
            continue  # 키 칸 모양은 `test_the_context_roster_matches_the_model_registry`가 잡는다
        stated = _KEY_EFFORT.search(row[key_at])
        declared = (models.get(key.group(1)) or {}).get("reasoning_effort")
        if (stated.group(1) if stated else None) != declared:
            offenders.append(
                f"PROJECT-CONTEXT.md:{lineno}: `{key.group(1)}` — 표 "
                f"{stated.group(1) if stated else None!r} · config/models.yaml {declared!r}"
            )
    assert offenders == [], (
        "PROJECT-CONTEXT.md §1 키 칸의 `reasoning_effort`가 registry와 갈렸다 — 등급을 바꿀 때는 "
        "표와 config/models.yaml을 함께 고친다.\n" + "\n".join(offenders)
    )


_MODEL_SERVING = REPO_ROOT / "docs" / "model-serving.md"
_UNAVOIDABLE = "회피 불가"
# `docs/model-serving.md` 포트 표의 행 — `> | **8004** | \`qwen3-embedding-0.6b\` ← …`.
_PORT_ROW = re.compile(r"^> \| \*\*(\d{4})\*\* \| `([^`]+)`", re.M)


def test_the_model_serving_port_table_matches_the_registry() -> None:
    """포트 표는 **지금 상태를 현재형으로 적는 자리**라 registry와 같아야 한다(CLAUDE.md §2.3).

    2026-09-24 포트를 띄우는 순서대로 다시 매겼다(`tests/test_config.py`의
    `test_served_ports_count_up_from_8000_in_launch_order`). registry만 고치고 이 표를 놓치면
    표가 옛 번호로 서빙 절차를 안내한다 — 그 불일치를 사람이 기억해서 잡기를 기대하지 않는다.
    """
    # 핀 — 양성은 저장소의 실제 행이다(`docs/model-serving.md` 포트 표의 8004 행 — 검색 모델이라
    # 재배치에서 번호가 안 바뀐다). 음성은 같은 표의 머리 줄 모양이다 — 행이 아니다.
    assert _PORT_ROW.search("> | **8004** | `qwen3-embedding-0.6b` ← `harness_pool_hybrid` |")
    assert not _PORT_ROW.search("> | 포트 | 어느 프로필이 어느 이름을 보내나 |")
    from src.config import CONFIG_DIR

    rows = {key: int(port) for port, key in _PORT_ROW.findall(_read(_MODEL_SERVING))}
    models = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))["models"]
    served = {
        key: urlparse(str(entry["endpoint"])).port
        for key, entry in models.items()
        if not str(entry["endpoint"]).startswith("fake://")
    }

    assert rows == served, f"포트 표 {rows} ≠ registry {served}"


def _within_profile_port_clashes() -> dict[str, dict[str, list[str]]]:
    """프로필마다 「같은 포트로 서로 다른 registry 키를 보내는가」를 센다.

    **프로필 *사이*의 충돌과 다르다.** 8001·8003은 어느 프로필로 도느냐에 따라 그 포트에
    한 이름만 가므로 *"그날 돌릴 프로필이 무엇인지 보고 맞춘다"*가 듣는다. 한 프로필
    **안에서** 두 이름이 같은 포트로 가면 **고를 프로필이 없다** — 그 회피가 성립하지 않는다.

    **이름은 registry 키다** — `src/adapters/openai_compat.py`가 `"model": self.model_name`으로
    보내고 그 값이 `factory._model_client()`에서 model key 그대로 온다.
    """
    from src.config import CONFIG_DIR

    models = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))["models"]
    raw = yaml.safe_load((CONFIG_DIR / "profiles.yaml").read_text(encoding="utf-8"))
    profiles = raw["profiles"]

    def port_of(key: str) -> str | None:
        endpoint = str((models.get(key) or {}).get("endpoint", ""))
        found = re.search(r"//127\.0\.0\.1:(\d+)/", endpoint)
        return found.group(1) if found is not None else None

    clashes: dict[str, dict[str, list[str]]] = {}
    for name, entry in profiles.items():
        if not isinstance(entry, dict):
            continue
        by_port: dict[str, set[str]] = {}
        for key in (entry.get("roles") or {}).values():
            port = port_of(str(key))
            if port is not None:
                by_port.setdefault(port, set()).add(str(key))
        shared = {port: sorted(keys) for port, keys in by_port.items() if len(keys) > 1}
        if shared:
            clashes[name] = shared
    return clashes


def test_model_serving_calls_the_within_profile_port_clash_unavoidable() -> None:
    """한 프로필이 같은 포트로 두 이름을 보내면 `model-serving.md`가 **회피 불가**라 적어야 한다.

    **왜 이 검사가 있나.** 그 문서는 포트 공유를 적으면서 처방으로 *"그날 돌릴 프로필이
    무엇인지 보고 맞춘다"*를 든다. 그 처방은 **프로필 사이**의 충돌(8001·8003)에만 듣는다 —
    한 프로필 안에서 두 이름이 오면 고를 프로필이 없다. 문서가 전제 둘(*"8002는 한 프로필
    안에서 두 이름이 온다"* · *"멀티네임은 미검증이다"*)을 다 적고도 **그 둘을 잇지 않아서**,
    읽는 사람이 풀 프로필도 「프로필 골라 피하면 된다」로 읽는다.

    **이 검사는 양방향으로 돈다.**

    - 프로필 안 충돌이 있으면 → 그 포트와 「회피 불가」가 문서에 있어야 한다.
    - 충돌이 없어지면 → 그 문장이 **거짓이 되므로** 실패해서 지우게 만든다.

    후자가 곧 온다. 확정 로스터에서 Auditor가 바뀌면 `auditor`와 `scout_memory`가 다른
    모델이 되어 8002 충돌이 사라진다 — 그때 「회피 불가」가 남아 있으면 없는 블로커를
    주장하는 문장이 된다. 사람이 기억해서 지우기를 기대하지 않는다(CLAUDE.md 2.3).

    이 검사는 멀티네임이 **되는지**를 묻지 않는다. 그것은 실측이 없어 문서가 0장으로
    미검증이라 못박은 것이고, 여기서 잠그는 것은 **문서가 자기 전제에서 나오는 결론을
    적었는가** 하나다.
    """
    clashes = _within_profile_port_clashes()
    text = _read(_MODEL_SERVING)
    declared = _UNAVOIDABLE in text

    if clashes and not declared:
        detail = "; ".join(
            f"{name}: " + ", ".join(f"{port}<-{keys}" for port, keys in sorted(ports.items()))
            for name, ports in sorted(clashes.items())
        )
        raise AssertionError(
            f"프로필 안에서 포트를 공유하는 자리가 있는데 docs/model-serving.md가 "
            f"「{_UNAVOIDABLE}」를 안 적는다 — {detail}. "
            "그 자리는 프로필을 골라 피할 수 없으므로 「주의」가 아니라 블로커다."
        )
    if not clashes and declared:
        raise AssertionError(
            f"프로필 안에서 포트를 공유하는 자리가 하나도 없는데 "
            f"docs/model-serving.md가 아직 「{_UNAVOIDABLE}」를 들고 있다. 그 줄은 이제 거짓이다."
        )
    for ports in clashes.values():
        for port in ports:
            assert port in text, (
                f"포트 {port}이 프로필 안에서 두 이름을 받는데 docs/model-serving.md가 "
                f"그 번호를 대지 않는다 — 읽는 사람이 어느 자리인지 못 찾는다"
            )


# 같은 규약을 두 파일이 든다. CLAUDE는 Claude가, AGENTS는 다른 에이전트(Codex 등)가 읽는다.
_RULEBOOKS = ("CLAUDE.md", "AGENTS.md")


def test_the_two_rulebooks_say_the_same_thing() -> None:
    """`AGENTS.md`는 `CLAUDE.md`와 **첫 줄만** 달라야 한다.

    **왜 사본을 두는가.** 에이전트마다 읽는 파일 이름이 다르다. 규약을 한쪽에만 두면
    다른 에이전트는 그것을 아예 안 읽는다.

    **왜 검사가 필요한가.** 사본은 조용히 갈린다 — 사람은 자기가 읽는 쪽만 고친다.
    그리고 **갈린 사본이 실제로 있었다**: 2026-09-11에 `AGENTS.md`가 추적 대상이 되기 전
    상태를 열어 보니 §3이 *"이슈가 결함의 원천이고 상태의 원천이다"*였는데, 같은 시점
    `CLAUDE.md`는 **ADR 0008 이후라 정반대**였다(*"문서가 원천이고 GitHub Issue는 추적
    미러다"*). `PROJECT-CONTEXT.md`를 가리키는 줄과 2026-09-09 4연속 사고 기록도 통째로 없었다.

    **그 상태로 두면 이 저장소가 기록한 사고를 그대로 재생한다** — 다른 에이전트가
    **낡은 문서를 살아 있는 지시로 읽는다**(CLAUDE.md §7의 사고). 그래서 문서로
    부탁하지 않고 코드가 막는다. `PROJECT-CONTEXT.md` 검사들과 같은 판단이다.

    고치는 법: 규약을 `CLAUDE.md`에서 고치고 첫 줄만 바꿔 복사한다.
    """
    bodies = {}
    for name in _RULEBOOKS:
        path = REPO_ROOT / name
        assert path.is_file(), f"{name}이 없다 — 둘 다 추적 대상이다"
        first, _, rest = _read(path).partition("\n")
        assert first == f"# {name} — 하네스 팀 규약", (
            f"{name}의 첫 줄이 `# {name} — 하네스 팀 규약`이 아니다: {first!r}. "
            "이 검사는 첫 줄만 다른 것을 허용하므로 그 줄의 형식이 고정이어야 한다"
        )
        bodies[name] = rest

    base, other = _RULEBOOKS
    if bodies[base] != bodies[other]:
        diff = "\n".join(
            difflib.unified_diff(
                bodies[base].splitlines(),
                bodies[other].splitlines(),
                fromfile=base,
                tofile=other,
                lineterm="",
                n=1,
            )
        )
        raise AssertionError(
            f"{base}와 {other}가 첫 줄 말고도 다르다 — 두 에이전트가 다른 규약을 읽는다.\n"
            f"{base}에서 고치고 첫 줄만 바꿔 복사한다.\n{diff[:4000]}"
        )


# registry 부재를 주장하는 문구. `미등재`는 §1 표가 정당하게 쓰므로 함께 본다.
_ABSENT_CLAIM = re.compile(r"registry에 없|레지스트리에 없|미등재")
# 그 주장 **바로 앞 절**에서만 키를 줍는다. 줄 전체를 보면 같은 줄의 다른 절이 걸린다 —
# PROJECT-CONTEXT.md §5의 재배선 행은 한 줄 안에서 "Critic은 등재됐다"와 "리랭커는 없다"를
# 함께 말하므로, 줄 단위로 보면 등재된 Critic까지 위반으로 잡힌다.
#
# **문장 경계에서 자른다.** 글자 수로만 자르면 앞 문장의 키가 딸려 온다 — 이 검사를 처음
# 돌렸을 때 `build-plan.md`가 그랬다: checksum 자리표시자를 말하는 `fake`가 100자 뒤의
# 다른 문장("새 로스터에는 registry 미등재 자리가 있다")에 붙어 오탐이 됐다.
_CLAUSE_END = re.compile(r"다[.)]|\.\s|—")
_CLAIM_LOOKBACK = 200
# 개수로 registry 상태를 주장하는 문구. 등재가 하나 늘 때마다 조용히 거짓이 된다.
#
# ⚠ **`미등재`는 이 저장소에서 두 뜻이다.** registry 미등재와 **D·R 인덱스 미등재**
# (`build-plan.md`의 「미결 질문」 표가 그쪽이다). 그래서 registry 문맥인 줄에서만 본다 —
# 안 좁히면 R 계열 등재를 말하는 표가 "registry 상태를 개수로 적었다"는 **틀린 사유로**
# 걸린다. 지금 그 표가 안 걸리는 것은 어순 덕분일 뿐이라 설계로 막는다.
_COUNT_CLAIM = re.compile(r"(둘|셋|넷|전부|모두)\s*다?\s*미등재")
_REGISTRY_CONTEXT = re.compile(r"models\.yaml|registry", re.IGNORECASE)


def test_no_document_says_a_registered_model_is_missing_from_the_registry() -> None:
    """등재된 키를 「registry에 없다」고 적은 문장이 없어야 한다.

    **왜 필요한가.** `test_the_context_roster_matches_the_model_registry`는 §1 표의
    `registry 등재` **칸**만 본다. 같은 파일 §5의 **산문**은 안 본다 — 그래서 등재 PR이
    §1을 ✅로 바꾸면서 §5에 남은 「아직 registry에 없어서」를 못 잡고, **정본 파일이 자기
    §1과 §5에서 다른 말을 하게 된다.**

    `PROJECT-CONTEXT.md`가 존재하는 이유가 **낡은 로스터 서술을 살아 있는 지시로 읽는 사고**인데
    (그 파일 머리말 — 2026-09-09 하루에 네 번), 그 파일이 스스로 그 사고의 원천이 되면
    장치가 뒤집힌다. 그래서 사람이 아니라 코드가 막는다.

    **주장 바로 앞만 본다.** 줄 단위로 보면 한 문장이 여러 자리를 말할 때
    (「Critic은 등재됐고 리랭커는 없다」) 등재된 쪽까지 위반으로 잡힌다.
    """
    registry = _registry_keys()
    offenders: list[str] = []

    for path in _tracked("*.md"):
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            for claim in _ABSENT_CLAIM.finditer(line):
                window = line[max(0, claim.start() - _CLAIM_LOOKBACK) : claim.start()]
                # 마지막 문장 경계 뒤만 남긴다 — 그 절이 이 주장의 주어를 들고 있다.
                breaks = list(_CLAUSE_END.finditer(window))
                if breaks:
                    window = window[breaks[-1].end() :]
                for key in re.findall(r"`([^`]+)`", window):
                    if key in registry:
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: "
                            f"`{key}`를 「{claim.group(0)}」로 적었는데 "
                            f"config/models.yaml에 **있다**"
                        )

    assert offenders == [], (
        "등재된 모델을 미등재로 서술한 곳이 있다 — 등재 PR이 §1만 고치고 산문을 안 고친 "
        "자리다(CLAUDE.md §2.3).\n" + "\n".join(offenders)
    )


def test_no_document_states_the_registry_state_as_a_count() -> None:
    """「셋 다 미등재」처럼 **개수**로 registry 상태를 적지 않는다.

    개수는 등재가 하나 늘 때마다 조용히 거짓이 되는데, 키 이름이 없어서 위 검사도 못
    잡는다. `PROJECT-CONTEXT.md` §3이 그 규칙을 이미 적는다 — *"그 밖의 문서는 모델을 현재형으로
    이름 대지 않는다. 이름을 대야 하면 이 파일을 링크한다."*

    **실제로 낡았다**: `R8.md`가 *"`config/models.yaml`(셋 다 미등재)"*로 적은 채
    2026-09-11에 둘이 등재됐다. 세는 대신 §1 표를 가리키면 그 표는 CI가 잠근다.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: 「{match.group(0)}」"
        for path in _tracked("*.md")
        for lineno, line in enumerate(_read(path).splitlines(), start=1)
        if _REGISTRY_CONTEXT.search(line)
        for match in [_COUNT_CLAIM.search(line)]
        if match
    ]
    assert offenders == [], (
        "registry 상태를 개수로 적은 곳이 있다. 개수는 등재마다 거짓이 되고 키 이름이 "
        "없어 다른 검사도 못 잡는다 — PROJECT-CONTEXT.md §1 표를 링크한다.\n" + "\n".join(offenders)
    )


def test_the_roster_registration_cell_only_says_whether_it_is_registered() -> None:
    """§1 `registry 등재` 칸은 **등재 여부만** 말한다 — 다른 항목 번호(R·D)를 안 든다.

    그 열 이름이 「registry 등재」 하나이고, 서빙·검증 상태의 자리는 **§5**다. 한 칸이 두
    축을 들면 다음 모델에서 「어디까지 이 칸에 적나」가 매번 다시 열리고, 그 칸을 잠그는
    `test_the_context_roster_matches_the_model_registry`는 registry 사실만 검증하므로
    **곁들인 서술은 아무도 안 본다.** §4의 등급 표를 두 축으로 가른 것과 같은 판단이다.
    """
    header, rows = _first_table(_read(CONTEXT), _ROSTER_HEADING, _ROSTER_TABLE)
    mark_at = _column(header, _MARK_COLUMN, _ROSTER_TABLE)
    item_ref = re.compile(r"\b[RD]\d+\b")

    offenders = [
        f"PROJECT-CONTEXT.md:{lineno}: `{_MARK_COLUMN}` 칸이 {found}를 든다 — {row[mark_at][:60]!r}"
        for lineno, row in rows
        if mark_at < len(row)
        for found in [item_ref.findall(row[mark_at])]
        if found
    ]
    assert offenders == [], (
        f"§1의 `{_MARK_COLUMN}` 칸은 등재 여부만 말한다. 서빙·검증 상태는 §5에 적는다.\n"
        + "\n".join(offenders)
    )


# R8은 **관문이 하나가 아니다** — 닫히는 조건이 셋이고 서로를 대신하지 못한다.
# 그래서 단수 소유격 「R8의 관문」 자체가 결함이다. `R8.md`의 「온보딩 관문」·
# 「셋 각각에 다른 관문」·표 머리 `| 관문 |`은 다른 구문이라 안 걸린다.
_R8_SOLE_GATE_MARKER = "R8의 관문"

# 이 파일만 뺀다 — **금지어를 금지하려면 그것을 적어야 한다.** 다른 테스트
# 파일은 검사 대상이다(docstring도 주장을 싣는다).
_MARKER_DECLARING_FILE = Path(__file__).resolve()


def test_no_tracked_file_calls_r8_a_single_gate() -> None:
    """어떤 추적 파일도 R8의 관문을 **하나**라고 적지 않는다.

    **왜 기계가 보나.** 이 문장은 PR #180이 지우려고 연 바로 그 결함인데,
    같은 문장이 **살아 있는 소스 셋**에 흩어져 있었다 — `config/models.yaml`,
    `tools/README.md`, `tools/probe/gpt_oss_structured.py`. 산문의 참·거짓을
    보는 검사가 저장소에 없어서(주석을 **보존**하는 검사는 있다 —
    `test_bench_apply.py`) 하나를 고치고 나머지를 놓쳐도 CI가 초록이다.
    **실제로 그렇게 됐다** — #180이 앞 둘을 고치고 프로브 파일을 놓쳤고,
    이 검사가 그것을 잡았다.

    **그리고 이 자리는 머지에서 되살아난다.** PR #181이 같은 주석 블록을
    건드리는데 그쪽 판본은 이 문장을 **그대로 들고 있다.** 충돌을
    `--theirs`로 풀면 #180이 존재하는 이유가 조용히 되돌아오고, 이 검사가
    없으면 그때도 CI가 초록이다. CLAUDE.md §2.3의 전수 grep이 서는 자리다.

    **고치는 방향은 「닫히는 조건 *n*」이다.** 관문이 하나라고 적으면
    `valid_ratio` 0.8도 조건을 만족하는 것처럼 읽힌다 — 조건 1의 통과선은
    **1.0**이고(`src/bench_report.py`의 `_judge_structured`), 조건 3(반박
    정확도)은 `cli bench`에 **아예 없다**.
    """
    offenders = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for path in _tracked("*.md", "*.py", "*.yaml", "*.yml")
        if path.resolve() != _MARKER_DECLARING_FILE
        for number, line in enumerate(_read(path).splitlines(), start=1)
        if _R8_SOLE_GATE_MARKER in line
    ]
    assert offenders == [], (
        "R8의 관문을 **하나**라고 적은 자리가 남아 있다 — R8은 닫히는 조건이 "
        "**셋**이고 서로를 대신하지 못한다. 「닫히는 조건 *n*」으로 고쳐라.\n"
        + "\n".join(offenders)
    )


# 산문이 D 항목의 상태를 **현재형으로 단언**하는 자리 — `[D74](…)는 열림` · `**D74**는 닫힘`.
#
# **조사가 D 참조에 직접 붙은 것만 잡는다.** 사이에 허용하는 것은 링크와 마크업뿐이고
# 산문은 안 된다. 느슨하게 두면 *"**D29**(샘플링 등가화 남은 부분)"* 이 걸린다 —
# 「남은」의 `은` + `부분`이 조사+상태어로 파싱되는데 거기서 `부분`은 명사다.
# 그 오탐은 가정이 아니라 이 저장소에서 실제로 나왔다.
# ## 분기 둘 — **명제로 나눈다**
#
# 이 정규식은 네 번 넓혔고 **세 번은 문자열 축으로만 넓혀서 매번 반쪽이었다**
# (조사 `가`·`의` 추가 → 괄호 `,` 추가 → `D`에서 `[DR]`로). 네 번째에 축을 바꿨다 —
# 막으려는 명제는 *"산문이 D·R의 상태를 **단언**한다"*이고, 그 문형은 둘이다.
#
# **(가) 맨형** — `[D77](…) 닫힘` · `**D54** 부분` · `D9(닫힘)`. **조사가 없다.**
#   2026-09-20까지 이 문형이 통째로 사각이었고 **18건이 미탐이었다** — 그중 `PROJECT-CONTEXT.md`와
#   `docs/build-plan.md`의 정본 줄이 있다.
# **(나) 인접형** — `D74는 열림` · `#98(D26, 열림)`. 조사/쉼표 바로 뒤에 상태어가 온다.
# **(다) 말이 끼는 형** — `R2는 **그 파일에서 아직** \`열림\`이고`. 여기서 **백틱을 요구하고**
#   창이 **다른 D·R 토큰과 강한 구두점을 못 넘는다**: 그러지 않으면 「D54의 보류 해제를
#   기록한다」 같은 명사구가 걸리고, 이 저장소의 표준 문형인 다중 D 열거
#   (`D29는 … · D51이 \`보류\`다`)에서 **엉뚱한 D를 신고한다.**
#
# > **사고 — (나)를 안 보던 동안 9일 묵은 모순이 살아 있었다.** `docs/model-serving.md`가
# > *"R2는 그 파일에서 아직 `열림`이고"*라 적는데 `R2.md`는 `닫힘`이다. **R2를 닫은 바로 그
# > 커밋(`184aade`)이 같은 파일에 그 문장을 썼다.** 그리고 2026-09-20에 `[DR]`로 넓히면서
# > **핀에 실형태 대신 「R2는 `닫힘`이다」라는 지어낸 형태를 넣어** 그 구멍을 덮었다 —
# > 실형태를 핀으로 썼으면 그 자리에서 빨개졌다([CLAUDE.md](../CLAUDE.md) §0).
#
# ⚠ **왼쪽 경계가 필요하다** — 없으면 `DeepSeek-R1, 열림 상태다`가 R1 단언으로 읽힌다.
# 그 모델 이름은 `docs/decisions/0010-...md`에 살아 있다.
_D_STATUS_CLAIM = re.compile(
    r"(?<![A-Za-z0-9-])([DR]\d+)(?:\]\([^)]*\))?"
    # (가) 맨형 — 조사가 없다. `[D77](…) 닫힘` · `**D54** 부분` · `D9(닫힘)`
    r"(?:[*`\s(]{1,4}\*{0,2}`?(열림|닫힘|보류|부분)`?"
    # (나) 인접형 — 조사/쉼표 바로 뒤
    r"|[*`\s]{0,4}(?:는|은|이|가|,)\s*\*{0,2}`?(열림|닫힘|보류|부분)`?\*{0,2}"
    # (다) 말이 끼는 형 — 백틱 필수. 창이 **다른 D·R 토큰과 강한 구두점을 못 넘는다**
    r"|[*`\s]{0,4}(?:는|은|이|가|의|,)\s*"
    r"(?:(?![DR]\d)[^.\n|·—()「」?!;:]){0,24}?\*{0,2}`(열림|닫힘|보류|부분)`)"
)

# 취소선 구간은 **철회 표시**다 — 검사 전에 걷어낸다. `~~D57(부분)~~ → 닫혔다` 처럼
# 스스로 정정하는 문장이 저장소의 표준 형태이고, 안 걷으면 그것이 전부 위반으로 잡힌다.
_WITHDRAWN = re.compile(r"~~.*?~~")


def _claimed_status(claim: "re.Match[str]") -> str:
    """세 문형 중 실제로 잡힌 쪽의 상태값."""
    return next(group for group in claim.groups()[1:] if group)

# D 본문 파일 머리의 상태 셀. `| 열림 |` · `| **닫힘** (2026-09-18, 선택지 (a)) |` 둘 다 받는다.
# ## 문형 (라) — 상태어가 **서술어**일 때. 따로 두는 이유가 있다
#
# 6라운드가 실형태 하나를 더 찾았다: `[R1](…) `:11`의 상태는 **부분**이고`
# (`docs/open-questions/D/D64.md:56`). (다)가 못 넘은 것은 새 문자가 아니라 **창 안에 낀
# 코드 스팬**이다 — `:11`의 `:` 이 (다)의 구두점 차단에 걸린다.
#
# ⚠ **여기서 (다)의 백틱 요구를 푸는 것이 첫 수정이었고 그것이 틀렸다.** 풀면
# 「D54의 보류 해제를 기록한다」가 잡힌다 — 아래 음성 핀이 정확히 그 문장이고, 거기서
# 「보류」는 단언이 아니라 **명사구의 수식어**다. 문자 축을 넓히면 미탐 하나를 오탐 한
# 부류와 바꾼다([CLAUDE.md](../CLAUDE.md) §2.5).
#
# 그래서 축을 **문법 자리**로 잡는다: 상태어 뒤에 **계사**(`이다`·`이고`·`이며`·`이었`·`였`)가
# 붙으면 서술어이고 그때만 단언이다. 「보류 해제를」은 뒤에 명사가 오므로 안 걸린다.
#
# **덧셈으로 건다.** `_D_STATUS_CLAIM`은 **원문 줄에 그대로** 돌고 (라)만 정규화된 줄에
# 돈다 — 그러지 않으면 정규화가 (다)의 백틱을 떼어 **기존 적중 둘을 잃는다**(실측).
#
# **실측 —** 추적되는 `*.md` 전수에서 (라)가 잡는 것은 넷이고 그중 기존 검출기 너머는
# **하나**(위 실형태)다. **잃은 것 0 · 새 위반 0 · 음성 핀 6/6 유지.**
_CLAIM_PART = re.compile(r"열림|닫힘|보류|부분|(?<![A-Za-z0-9-])[DR]\d+")
_CODE_SPAN = re.compile(r"`[^`\n]*`")


def _prose_only(line: str) -> str:
    """백틱 스팬을 걷은 줄 — 스팬은 산문이 아니라 코드다.

    단 그 안에 단언의 두 요소(항목 `D<n>`·`R<n>` 또는 상태어)가 있으면 **잡음이 아니라
    단언의 일부**이므로 백틱만 뗀다. 통째로 지우면 `` `D53`(닫힘) ``
    (`docs/open-questions/D/D66.md:19`)에서 **토큰째 사라져 적중을 잃는다**(실측).
    """
    return _CODE_SPAN.sub(
        lambda span: span.group(0)[1:-1] if _CLAIM_PART.search(span.group(0)) else " ",
        line,
    )


_D_STATUS_PREDICATE = re.compile(
    r"(?<![A-Za-z0-9-])([DR]\d+)(?:\]\([^)]*\))?"
    r"[*`\s]{0,4}(?:는|은|이|가|의|,)\s*"
    r"(?:(?![DR]\d)[^.\n|·—()「」?!;:]){0,24}?"
    r"\*{0,2}(열림|닫힘|보류|부분)\*{0,2}(?:이다|이고|이며|이었|였)"
)



_D_STATUS_CELL = re.compile(r"^\|\s*\*{0,2}(열림|닫힘|보류|부분|결정|결함 아님)")


def _d_body_status(did: str) -> str | None:
    """`D<n>.md`·`R<n>.md` 머리 표가 선언한 상태. 없으면 `None`.

    **`R`도 본다.** R은 이슈를 만들지 않아 미러가 없으므로 산문이 유일한 오염원이다.

    ⚠ **「D만 봐서 R 모순이 살아남았다」로 읽지 마라.** 한때 그렇게 적었는데 거짓 인과다 —
    `docs/model-serving.md`의 *"R2는 그 파일에서 아직 `열림`이고"*는 `[DR]` 확장 뒤에도
    **안 잡혔다.** 그것을 놓친 것은 **(다) 문형 미지원**이지 `R` 미지원이 아니다.
    """
    path = REPO_ROOT / "docs" / "open-questions" / did[0] / f"{did}.md"
    if not path.is_file():
        return None
    for line in _read(path).splitlines():
        found = _D_STATUS_CELL.match(line)
        if found:
            return found.group(1)
    return None


def test_the_d_status_claim_regex_actually_matches_a_claim() -> None:
    """정규식이 아무것도 안 잡으면 아래 검사가 조용히 통과한다 — 그것부터 막는다.

    **양성 핀은 전부 저장소에 실재했던 형태다** — 옆에 출처를 적는다. 지어낸 샘플을 쓰면
    잡히는 형태만 고르게 되고, 그것이 정확히 2026-09-20에 `[DR]` 확장의 구멍을 덮은
    방식이다([CLAUDE.md](../CLAUDE.md) §0).

    ⚠ **음성 핀은 다르다 — 넷은 구성문이다.** 「잡히면 안 되는 형태」는 정의상 저장소에
    없거나(막아야 하니까) 다른 뜻으로만 있다. 그래서 **실재 여부가 아니라 「그 문형이
    저장소에 실제로 나타날 수 있는가」로** 고른다: 다중 D 열거·모델 이름·인덱스 표 행은
    전부 이 저장소의 표준 문형이다. 근거를 옆에 적는다.
    """
    # ⚠ **정규식 이름을 직접 부르는 단언이 하나는 있어야 한다.** 아래 루프는 `found` 를
    # 거쳐 가므로 「이 정규식에 핀이 있는가」를 기계로 세는 검사가 못 본다
    # (`test_every_detector_carries_a_contamination_pin`).
    assert _D_STATUS_CLAIM.search("- [D77](docs/open-questions/D/D77.md) 닫힘"), "맨형을 못 잡는다"
    assert _WITHDRAWN.sub("", "~~D57(부분)~~ 뒤") == " 뒤", "취소선 제거가 깨졌다"

    hits = {
        # 맨형 — `PROJECT-CONTEXT.md:146`이 로스터 표 뒤에 적는 형태
        "- [D77](docs/open-questions/D/D77.md) 닫힘": ("D77", "닫힘"),
        # 맨형(굵게) — `D64.md:62`가 2026-10-10 취소선 전까지 적었던 형태
        "(관련: **D54** 부분 — 로스터 모델 중 어느 것도)": ("D54", "부분"),
        # 인접형 — `D74.md`를 (a)로 닫는 PR이 실험 보고서에 남긴 문장
        "- [D74](../../open-questions/D/D74.md)는 열림 상태를 유지한다.": ("D74", "열림"),
        # 인접형(괄호) — `D65.md:56`가 실제로 적었던 형태
        "**#98(D26, 열림)과의 관계**": ("D26", "열림"),
        # 말이 끼는 형 — `docs/model-serving.md:100`이 아흐레 들고 있던 **실형태**
        "R2는 그 파일에서 아직 `열림`이고": ("R2", "열림"),
        # `의` + 백틱 — `docs/open-questions.md:46`가 적었던 형태
        "그 승격이 D54의 `열림`을 다시 바꾸지는 않는다": ("D54", "열림"),
    }
    for sample, (number, status) in hits.items():
        found = _D_STATUS_CLAIM.search(sample)
        assert found is not None, f"상태 단언 정규식이 못 잡는다: {sample}"
        assert (found.group(1), _claimed_status(found)) == (number, status), sample

    misses = [
        # 실재: `open-questions.md`가 「남은 **부분**」으로 쓴다 — 거기서 `부분`은 명사다
        "D29(샘플링 등가화 남은 부분)",
        # 구성문. `의`+명사구는 흔한 형태이고 그대로 두면 「보류를 풀었다」가 「보류다」로 읽힌다
        "D54의 보류 해제를 기록한다",
        # 구성문. `DeepSeek-R1`은 `decisions/0010-...md`에 살아 있는 모델 이름이다
        "Critic 후보는 DeepSeek-R1, 열림 상태다",
        # 구성문. 인덱스 표 행은 산문이 아니라 정본 자체다 — 잡으면 자기 자신을 신고한다
        "| D9 | 인덱서 | 닫힘 | [본문](x.md) |",
        # 구성문. 문장을 넘어가면 단언이 아니다
        "D63은 열려 있다. 그 뒤 `닫힘`이 됐다",
    ]
    for sample in misses:
        assert _D_STATUS_CLAIM.search(sample) is None, f"정규식이 정상 문장을 잡는다: {sample}"

    # **오귀속 핀.** 다중 D 열거는 이 저장소의 표준 문형이고(`open-questions.md:40`),
    # (다)의 창이 다른 D 토큰을 넘으면 **엉뚱한 D를 신고한다.** 잡히되 **옳은 쪽**이어야 한다.
    for sample, owner in (
        ("**D29**는 남은 것이 있고 · **D51**이 `보류`다", "D51"),
        ("D2는 무관하다 — 옆의 D51이 `보류`다", "D51"),
        ("D40은 아직이고(D35가 `부분`)", "D35"),
    ):
        found = _D_STATUS_CLAIM.search(sample)
        assert found is not None and found.group(1) == owner, f"오귀속: {sample}"

    # **(라) 서술어형의 핀.** 양성은 `docs/open-questions/D/D64.md:56`의 실형태다 —
    # 6라운드 손 훑기가 찾았고 그때 검출기는 못 잡았다. 음성 둘은 구성문이되 근거가 있다:
    # 「보류 해제를」은 이 저장소가 실제로 쓰는 명사구이고(아래 `misses`의 같은 문장),
    # 계사 없는 「열림 상태다」는 `decisions/0010-…md`의 모델 이름 문형과 같은 자리다.
    assert _D_STATUS_PREDICATE.search(
        _prose_only("**⑤ R1과의 관계.** [R1](../R/R1.md) `:11`의 상태는 **부분**이고,")
    ), "(라) 서술어형을 못 잡는다"
    assert _D_STATUS_PREDICATE.search("D54의 보류 해제를 기록한다") is None, (
        "(라)가 명사구 수식어를 단언으로 읽는다"
    )

    # **정규화의 핀.** 스팬을 통째로 지우면 토큰째 사라진다(`D66.md:19`가 그 형태다).
    assert _prose_only("`D53`(닫힘)이 닫은") == "D53(닫힘)이 닫은", "단언 요소를 든 스팬을 지웠다"
    assert _prose_only("`:11`의 상태는") == " 의 상태는", "잡음 스팬을 안 걷는다"
    assert _CODE_SPAN.search("`:11`"), "코드 스팬 정규식이 죽었다"
    assert _CODE_SPAN.search(":11") is None, "백틱 없는 토막을 스팬으로 읽는다"
    assert _CLAIM_PART.search("D53"), "단언 요소 정규식이 항목을 못 읽는다"
    assert _CLAIM_PART.search("trigger_status") is None, "단언 요소 정규식이 아무거나 읽는다"

    # 취소선은 철회 표시다 — 걷어내면 안 남는다.
    assert _D_STATUS_CLAIM.search(_WITHDRAWN.sub("", "~~D57(부분)~~ → 닫혔다")) is None


def test_no_document_states_a_d_status_that_its_body_file_contradicts() -> None:
    """산문이 단언한 D 상태가 그 항목 본문 파일의 상태와 같아야 한다.

    **왜 필요한가.** 상태의 원천은 `D<n>.md`이고(ADR 0008 — 결함의 상태 원천),
    `test_the_index_status_column_matches_its_body_file`이 **인덱스 행의 상태 열**을
    그것에 묶는다(⚠ 그 검사는 6라운드에 생겼다 — 그전에는 아무것도 안 묶었다.
    `test_every_body_file_has_an_index_row`는 행의 **존재**만 본다). 그런데 산문은
    아무도 안 본다 — D를 닫는 PR이 본문과 인덱스만 고치고, 그 항목을 **현재형으로 서술하던
    다른 문서**를 놓친다. CLAUDE.md §2.3이 든 사고(*"값이 움직였는데 그 근거만 남았다"*)와
    같은 모양이고, 그때도 CI는 초록이다.

    **실제로 이 검사가 잡은 것 —** D74를 (a)로 닫는 PR이 실험 보고서의
    *"[D74](…)는 열림 상태를 유지한다"* 를 안 고쳤다. 그 PR 본문은 실험 보고서를
    *"날짜 박힌 스냅샷이라 안 건드린다"* 로 분류했는데, 그 한 문장만은 스냅샷이 아니라
    **D74의 현재 상태 단언**이었다.

    **보고서를 통째로 면제하지 않는 이유가 그것이다.** 면제하면 이 부류를 영영 못 잡는다.
    측정 서술은 과거형이라 이 정규식에 안 걸리고, 걸리는 것은 상태를 현재형으로 말한
    문장뿐이다.

    자기 자신은 뺀다 — `D74.md`가 자기 상태를 적는 것은 정본이지 주장이 아니다.
    """
    offenders: list[str] = []

    for path in _tracked("*.md"):
        own = path.stem if path.parent.name in ("D", "R") else None
        for number, line in enumerate(_read(path).splitlines(), start=1):
            stripped = _WITHDRAWN.sub("", line)
            claims = [
                (claim.group(1), _claimed_status(claim))
                for claim in _D_STATUS_CLAIM.finditer(stripped)
            ]
            # (라)는 **정규화된 줄**에 따로 돈다. 원문에 걸면 되고 정규화를 위에 걸면
            # (다)의 백틱이 사라져 기존 적중을 잃는다 — 그래서 합집합이지 대체가 아니다.
            claims += [
                (claim.group(1), claim.group(2))
                for claim in _D_STATUS_PREDICATE.finditer(_prose_only(stripped))
            ]
            for did, claimed in dict.fromkeys(claims):
                if did == own:
                    continue
                actual = _d_body_status(did)
                if actual is None or actual == claimed:
                    continue
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{number}: "
                    f"{did}을 「{claimed}」로 적었는데 "
                    f"docs/open-questions/{did[0]}/{did}.md는 「{actual}」이다"
                )

    assert offenders == [], (
        "D·R의 상태를 본문 파일과 다르게 단언한 산문이 있다 — 그 항목을 닫은 PR이 "
        "본문·인덱스만 고치고 이 문장을 놓친 자리다(CLAUDE.md §2.3).\n" + "\n".join(offenders)
    )


def test_the_index_status_column_matches_its_body_file() -> None:
    """인덱스 표의 상태 열이 본문 파일의 상태와 같아야 한다.

    **이 자리가 비어 있었다.** 상태의 원천은 `D<n>.md`인데(ADR 0008 — 결함의 상태 원천)
    그것을 인덱스에 묶는 검사가 없었다 — `test_every_body_file_has_an_index_row`는 인덱스가
    가리키는 **집합**과 디스크의 집합을 맞대므로 행의 **존재**만 묶고,
    `tests/test_open_questions_layout.py`의 `_ROW`는 번호만 캡처한다. 그리고
    `_D_STATUS_CLAIM`은 산문을 겨냥해 `|`를 문자 클래스에 안 넣으므로 **표 행에 구조적으로
    안 닿는다** — 셋 사이로 표 전체가 빠져나갔다.

    **실측 —** 인덱스의 한 행만 본문과 어긋나게 바꾸고 전수를 돌렸더니 그대로 통과했다.
    그 표에는 그렇게 열려 있던 행이 여든 남짓 있었다.

    본문이 없는 행은 `test_every_d_has_a_body_file`이 잡으므로 여기서는 건너뛴다 —
    한 결함을 두 검사가 이중으로 말하면 어느 쪽을 고쳐야 하는지가 흐려진다.

    ⚠ **이 검사는 산문 검사를 대체하지 않는다.** 표는 정형이라 셀 위치로 읽히고, 산문은
    문형으로 읽힌다. 둘은 같은 명제(*"D·R의 상태를 본문과 다르게 말한다"*)의 서로 다른
    문형이고, 한쪽만 들면 [CLAUDE.md](../CLAUDE.md) §2.5가 든 반쪽 전수가 된다.
    """
    index = REPO_ROOT / "docs" / "open-questions.md"
    offenders: list[str] = []

    for number, line in enumerate(_read(index).splitlines(), start=1):
        cells = _split_cells(line)
        if len(cells) < 3:
            continue
        did = cells[0]
        if not (len(did) > 1 and did[0] in "DR" and did[1:].isdigit()):
            continue
        claimed = cells[2].strip("* `")
        actual = _d_body_status(did)
        if actual is None or actual == claimed:
            continue
        offenders.append(
            f"docs/open-questions.md:{number}: {did} 행이 「{claimed}」인데 "
            f"docs/open-questions/{did[0]}/{did}.md는 「{actual}」이다"
        )

    assert offenders == [], (
        "인덱스 표의 상태가 본문 파일과 어긋난다 — 상태의 원천은 본문이고 표는 그것을 "
        "비춘다(ADR 0008 · CLAUDE.md §3).\n" + "\n".join(offenders)
    )


# D 본문의 상태 셀(첫 칸)이 `닫힘`인데 그 칸이 「제안」 단계 결정을 근거로 드는 꼴.
_CLOSED_ON_A_PROPOSAL = re.compile(r"^\|\s*\*{0,2}닫힘\*{0,2}[^|]*\(제안\)")


def test_a_closed_d_does_not_rest_on_a_proposed_decision() -> None:
    """상태의 원천이 `닫힘`을 단언하려면 그 근거 결정이 승인돼 있어야 한다.

    **사고 —** PR #260 첫 판이 D10을 「**닫힘** (2026-09-24, ADR 0012(제안) 결정 6)」으로
    적었다. 근거 결정이 승인 전인데 상태 원천이 닫힘을 단언했고, 안전은 「승인 전에는
    머지하지 않는다」는 사람 규율 하나에 달려 있었다(@sumin0218 리뷰 M1, 2026-09-24). 닫힘은
    그 결정을 승인하는 커밋이 발화한다.
    ⚠ 상태 칸만 본다 — 근거를 본문에만 적은 닫힘(같은 PR의 D12)은 이 검사가 못 잡는다.
    """
    # 양성 핀 — 실형태: PR #260 첫 판(`e18ac88`)의 `D10.md` 5행 앞부분.
    assert _CLOSED_ON_A_PROPOSAL.match(
        "| **닫힘** (2026-09-24, ADR 0012(제안) 결정 6) | [architecture.md](../architecture.md) |"
    )
    # 음성 핀 — 실형태: 같은 판의 `D12.md` 5행 앞부분(상태 칸에 제안 결정이 없다). 관련 문서 칸의
    # 「ADR 0012(제안)」 링크도 상태 칸 밖이라 안 걸린다.
    assert not _CLOSED_ON_A_PROPOSAL.match(
        "| **닫힘** (2026-09-24, 선택지 (a)) | [ADR 0012(제안)](../../decisions/0012.md) |"
    )
    offenders = []
    for path in sorted((REPO_ROOT / "docs" / "open-questions" / "D").glob("D*.md")):
        cell = next((line for line in _read(path).splitlines() if _D_STATUS_CELL.match(line)), "")
        if _CLOSED_ON_A_PROPOSAL.match(cell):
            offenders.append(f"{_rel(path)}: {cell[:90]}")
    assert offenders == [], (
        "제안 단계 결정에 기대 닫힌 D가 있다 — 승인 커밋이 닫는다:\n" + "\n".join(offenders)
    )


# 폐기됐다는 표시. **`제거`는 뺐다** — `PROJECT-CONTEXT.md` §5가 「enum 제약 **제거**」로 상시 쓰는
# 낱말이라, 폐기와 무관한 이웃 줄 하나가 검사를 통과시켰다(2라운드 리뷰가 주입으로 재현).
_DEPRECATION_MARKER = re.compile(r"폐기|소멸|없앴")
# 취소선은 **같은 줄에서만** 센다. 표에서는 그것이 폐기 표시이지만, 창 안 아무 줄에나 있는
# `~~`를 받으면 같은 우회가 열린다.
_STRIKETHROUGH = "~~"
# 곱셈기호(U+00D7)·ASCII `x`·대문자 `X`·전각 숫자·사이 공백을 전부 받는다.
# 2026-09-20에 `git grep "@2x2"`(ASCII)로만 훑은 PR이 `2×2`를 든 네 줄을 통째로 놓쳤고,
# 그 검사를 처음 쓸 때 이번에는 `2X2`·`2 × 2`를 놓쳤다 — 같은 실수의 두 번째 판이다.
# ⚠ **숫자 경계가 필수다.** 없으면 `8192 × 2회`(실험 보고서의 토큰 상한 표기)와 `512x256`이
# 축으로 읽힌다 — 지금 범위에는 오탐이 0이지만 같은 정규식을 차단 문구 쪽도 쓴다.
_TWO_BY_TWO = re.compile(r"(?<![0-9０-９])[2２]\s*[×xX✕✖╳]\s*[2２](?![0-9０-９])")


def test_the_dead_2x2_axis_is_never_named_without_saying_it_is_dead() -> None:
    """정본과 살아 있는 소스가 2×2를 들면 **그것이 폐기됐다는 표시를 같이** 들어야 한다.

    2×2 실험 축은 2026-09-20 PM 결정으로 폐기됐고 `CONDITION_PATTERN`이 `@pipeline`만
    받는다. 그런데 그 축을 **앞으로 올 사건**처럼 적은 줄이 남으면, 읽는 사람은 오지 않을
    선행을 기다린다 — `config/profiles.yaml`이 모델 자리 확정을 *"2×2 뒤 정식으로 정한다"*로
    걸어 둔 것이 그 모양이었다.

    **기록은 지우지 않는다.** 폐기 사실을 적은 줄은 통과한다 — 관측과 이력은 남기고
    「살아 있는 지시」만 막는 것이 이 검사의 경계다(CLAUDE.md §0의 「관측은 안 지운다」).

    **왜 창(window)으로 보나.** 주석은 여러 줄에 걸쳐 앞줄이 사실을, 뒷줄이 처분을 적는다
    (`src/schemas/run.py`의 `CONDITION_PATTERN` 주석이 그렇다). 줄 하나만 보면 그 형태가
    전부 걸리고, 그러면 다음 사람이 이 검사를 지운다.

    **범위는 「읽으면 그대로 따르는 것」뿐이다** — `PROJECT-CONTEXT.md`·`src/`·`config/`.

    ⚠ **`docs/` 를 안 넣은 것은 의도다.** 2026-09-20에 `docs/open-questions/D/` 까지 넓혀
    돌려 보니 **열둘**이 나왔고(커밋 `32ee331` 트리에서 잰 값) 그중 **열하나가 장부의 이력
    서술**이었다 — `D1:29`(원질문 문장) · `D30:11`·`D79:69`(D1과의 대조) · `D44:11`·`D52:11`
    (당시 기록 인용) · `D57:29` · **`D64`의 다섯 줄**(`:46`·`:60`·`:62`·`:74`·`:78` — 그 파일
    머리의 ⚠ 가 *"등재 시점의 상태다"*로 덮는다). 그것을 고치는 것은 관측을 지우는 일이다
    ([CLAUDE.md](../CLAUDE.md) §0).

    **진짜 위반은 하나였다** — `D54.md:66`의 *"넘기는 시점은 2×2 전량 완료 후다"*. 같은 PR에서
    그 줄에 **폐기 표시를 달았다**(문장은 남기고 ⚠ 를 붙였다 — 관측을 안 지운다). **실험
    보고서도 같은 이유로 밖이다** — 날짜 박힌 스냅샷이라 2×2를 현재형으로 적어도 그때의 사실이다.
    """
    # **오염 핀.** 현재 범위의 매치 다섯이 전부 폐기 표시로 면제되므로 아래 단언은
    # `[] == []`다 — **검출기를 통째로 죽여도 초록이다.** 형제 검사
    # (`test_the_blocking_messages_...`)에는 핀을 다섯 달면서 이쪽에는 0개를 달았고,
    # 4라운드 리뷰가 정규식을 무력화해 그것을 재현했다.
    assert _TWO_BY_TWO.search("2×2 뒤 정식으로 정한다"), "곱셈기호를 놓친다"
    assert _TWO_BY_TWO.search("2x2 뒤"), "ASCII x를 놓친다"
    assert _TWO_BY_TWO.search("2X2 뒤"), "대문자 X를 놓친다"
    assert _TWO_BY_TWO.search("2 × 2 뒤"), "사이 공백을 놓친다"
    assert _TWO_BY_TWO.search("２×２ 뒤"), "전각 숫자를 놓친다"
    assert not _TWO_BY_TWO.search("8192 × 2회"), "앞 숫자 경계가 없다"
    assert not _TWO_BY_TWO.search("해상도 2x25"), "뒤 숫자 경계가 없다"
    assert _STRIKETHROUGH in "~~2x2 스펙 고정값~~", "취소선 표시가 바뀌었다"
    assert _DEPRECATION_MARKER.search("2×2 전량 폐기로 소멸했다"), "폐기 표시를 못 읽는다"
    assert not _DEPRECATION_MARKER.search("enum 제약 제거는 별개다"), "「제거」를 표시로 읽는다"

    window = 3
    offenders: list[str] = []

    for path in _tracked("PROJECT-CONTEXT.md", "src", "config"):
        lines = _read(path).splitlines()
        for index, line in enumerate(lines):
            if not _TWO_BY_TWO.search(line):
                continue
            # 창은 **양쪽**을 본다. 주석은 앞줄에 사실을, 뒷줄에 처분을 적기도 하고 그 반대도 한다.
            nearby = "\n".join(lines[max(0, index - window) : index + 1 + window])
            if _STRIKETHROUGH in line or _DEPRECATION_MARKER.search(nearby):
                continue
            offenders.append(
                f"{path.relative_to(REPO_ROOT).as_posix()}:{index + 1}: {line.strip()}"
            )

    assert offenders == [], (
        "폐기된 2×2 축을 살아 있는 것처럼 드는 줄이 있다 — 읽는 쪽이 오지 않을 선행을 "
        "기다린다.\n" + "\n".join(offenders)
    )


# ## 검출기에는 핀이 따라온다 — 이 파일 자신을 검사한다
#
# **이 검사가 있는 이유는 실측이다.** 2026-09-20에 PR #227이 적대적 리뷰를 다섯 라운드
# 받았고, 그중 **두 라운드의 HIGH가 「검출기에 핀이 없어 공회전」**이었다 — 정규식을
# 통째로 죽여도 CI가 초록이었다. 고치고 나서 이 파일 전체를 세어 보니
# **전수 검사를 굴리는 정규식 25개 중 양·음 핀을 다 든 것이 3개**였다.
# 내가 낸 두 결함은 예외가 아니라 **이 파일의 기본값**이었다.
# ⚠ **그 3은 이 검사를 세우기 직전의 수다** — 세운 커밋이 둘을 더 달아 5가 됐고,
# 6라운드의 재기준선 뒤로는 35개 중 8이다. 재는 시점을 안 적으면 다음 사람이
# 「재현이 안 된다」로 읽는다([CLAUDE.md](../CLAUDE.md) §2.4) — 실제로 그랬다.
#
# 규칙은 **래칫**이다 — 새 검출기는 핀을 들어야 하고, 아래 부채 목록은 **줄기만 한다.**
# 한 번에 스무 개를 채우려다 아무것도 안 하는 것보다, 다음 사람이 하나를 더할 때
# 막는 편이 실제로 줄어든다(「인덱스 표는 지우지 않는다」와 같은 논법).
#
# ## 6라운드 — **「어느 것이 전수인가」를 맞히려던 것이 틀렸다**
#
# 처음에는 검출기 중 **전수를 굴리는 것**만 골라 핀을 요구했고, 고르는 방법이
# *"함수 안에 `offenders` 라는 이름의 변수가 있는가"* 였다. 그 축이 샜다 — 6라운드가
# 세 갈래를 **실제로 통과시켰다**(합성 검출기를 넣고 이 검사를 돌려 확인):
#
# 1. `assert not any(_RX.search(x) for x in ...)` — `offenders` 변수가 없어 함수째 안 보인다.
# 2. `_TABLE = {"k": _RX}` 뒤 `_TABLE["k"].search(...)` — 함수 안에 `_RX` 라는 이름이 안 뜬다.
#    도우미 함수로 한 겹 빼도 같다.
# 3. `assert None is _RX.search(x)` — `_assert_direction` 이 좌변을 안 봐서 `pos` 로 읽혔고,
#    **음성 단언 둘이 양·음 쌍으로 통과**했다.
#
# 셋을 각각 막는 것은 **또 문자열 축**이다([CLAUDE.md](../CLAUDE.md) §2.5). 그래서 고르기를
# 통째로 버렸다 — **모듈 수준 `re.compile` 은 전부 핀을 든다.** 추론이 없으면 샐 곳도 없다.
#
# **재기준선.** 그 결과 검사 대상이 25에서 **35**(이 파일의 전 검출기)로 늘었고 면제 목록도
# 20에서 **27**이 됐다. ⚠ **부채가 늘어난 것이 아니라 안 보이던 부채가 드러난 것이다** —
# 새로 들어온 일곱(`_PIPE`·`_LEADING_PIPE`·`_TRAILING_PIPE`·`_D_ROW`·`_D_STATUS_CELL`·
# `_ADR_ROW`·`_KEPT_HEADING`)은 **한 번도 검사받은 적이 없다.** 이 재기준선 뒤로는 그대로
# 래칫이다.

# ⚠ **이 목록은 늘릴 수 없다.** 줄이는 PR만 이 줄을 건드린다.
_UNPINNED_LEGACY = frozenset({
    "_ABSENT_CLAIM", "_ADR_ROW", "_CLAUSE_END", "_COUNT_CLAIM", "_DATED_LINE", "_DEVICE_ROW",
    "_D_REF", "_D_ROW", "_D_STATUS_CELL", "_FENCE", "_FT_REF", "_KEPT_HEADING", "_LEADING_KEY",
    "_LEADING_PIPE", "_LINK", "_MEMORY_HEADER", "_MEMORY_ROW", "_PIPE", "_REGISTRY_CONTEXT",
    "_ROSTER_HEADING", "_R_REF", "_R_ROW", "_SEPARATOR", "_SERVED_ROLE_CLAIM",
    "_STRUCK", "_TRAILING_PIPE",
})


def _assert_direction(node: ast.Assert) -> str | None:
    """단언의 방향. `None`이면 전수 단언(`offenders == []`)이라 핀이 아니다."""
    test = node.test
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return "neg"
    if isinstance(test, ast.Compare):
        if any(isinstance(op, ast.NotIn) for op in test.ops):
            return "neg"
        # ⚠ `comparators` 는 **우변뿐**이다. `None is X` 는 `None` 이 `left` 에 있어
        # 여기서 빠지고 아래 `pos` 로 떨어졌다 — 음성 단언 둘이 **양·음 쌍으로 통과**했다
        # (6라운드 실측: `_PROBE_FAKE` 가 핀 없이 래칫을 통과했다).
        if any(isinstance(op, ast.Is) for op in test.ops) and any(
            isinstance(c, ast.Constant) and c.value is None
            for c in (test.left, *test.comparators)
        ):
            return "neg"
        if any(isinstance(op, ast.Eq) for op in test.ops) and any(
            isinstance(c, ast.List) and not c.elts for c in test.comparators
        ):
            return None
    return "pos"


def test_the_assert_direction_reads_both_sides_of_an_is_comparison() -> None:
    """`None is X` 도 음성이다 — 우변만 보면 그것을 **양성**으로 읽는다.

    **사고 —** 6라운드까지 좌변을 안 봤다. 그래서 음성 단언 **둘**(`None is X` 와
    `X is None`)을 든 검출기가 **양·음 쌍**으로 래칫을 통과했다. 핀이 붙은 것처럼 보이는데
    그 검출기가 한 번이라도 무언가를 잡는다는 증거는 어디에도 없다 — 아래 검사가 막으려는
    상태 그 자체다. **`_assert_direction` 을 직접 겨눈 검사가 없어서** 전수를 거쳐야만
    드러났고, 그 전수는 이 오분류를 초록으로 통과시킨다.
    """

    def direction(source: str) -> str | None:
        parsed = ast.parse(source).body[0]
        assert isinstance(parsed, ast.Assert)
        return _assert_direction(parsed)

    assert direction("assert _X.search(s) is None") == "neg"
    assert direction("assert None is _X.search(s)") == "neg", "좌변의 None 을 못 본다"
    assert direction("assert _X.search(s) is not None") == "pos"
    assert direction("assert None is not _X.search(s)") == "pos", "부정을 뒤집어 읽는다"
    assert direction("assert not _X.search(s)") == "neg"
    assert direction("assert _X.search(s)") == "pos"
    assert direction("assert offenders == []") is None, "전수 단언은 핀이 아니다"


def test_every_detector_carries_a_contamination_pin() -> None:
    """전수 검사를 굴리는 정규식은 **양성·음성 핀을 둘 다** 들어야 한다.

    **왜 둘 다인가.** 양성만 있으면 정규식이 넓어져 정상 문장을 막는 것을 못 잡고,
    음성만 있으면 좁아져 **검출기가 아무것도 안 잡는 상태**를 못 잡는다. 실제로 난 사고는
    후자다 — 범위 안의 매치가 전부 면제되면 단언이 `[] == []`가 되어 **정규식을 지워도
    초록**이다.

    ⚠ **단언이 정규식 이름을 직접 불러야 센다.** `found = _X.search(s)` 뒤에
    `assert found is not None` 은 이 검사에 안 보인다 — 그 간접이 정확히 `_D_STATUS_CLAIM`의
    양성 핀을 0으로 만들었다. 직접 호명이 규율이기도 하다.

    **면제는 늘리지 않는다.** `_UNPINNED_LEGACY`는 이 검사를 세운 날의 부채이고
    줄이는 방향으로만 움직인다.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))

    detectors = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        if isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "compile"
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    def named(node: ast.AST) -> set[str]:
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & detectors

    pins: dict[str, set[str]] = {}
    for node in (n for n in ast.walk(tree) if isinstance(n, ast.Assert)):
        direction = _assert_direction(node)
        if direction is None:
            continue
        for name in named(node.test):
            pins.setdefault(name, set()).add(direction)

    unpinned = {d for d in detectors if pins.get(d, set()) != {"pos", "neg"}}

    assert unpinned <= _UNPINNED_LEGACY, (
        "전수 검사를 굴리는 새 정규식에 오염 핀이 없다 — 그 검출기가 고장 나도 CI는 "
        "영영 초록이다. 이름을 직접 부르는 양성·음성 단언을 하나씩 단다:\n"
        + "\n".join(f"  {d}" for d in sorted(unpinned - _UNPINNED_LEGACY))
    )
    assert _UNPINNED_LEGACY <= detectors, "면제 목록이 없는 이름을 든다"
    stale = _UNPINNED_LEGACY - unpinned
    assert not stale, (
        "면제 목록에 **이미 핀이 붙은** 정규식이 남아 있다 — 부채를 갚았으면 목록에서 "
        "지운다. 안 지우면 다음 사람이 그만큼 다시 늘릴 수 있다:\n"
        + "\n".join(f"  {d}" for d in sorted(stale))
    )


# --- #513 / ADR 0014 — 「도구가 PoC를 받지 않는다」를 현재형으로 말하는 자리 ---------------

# **명제:** 살아 있는 소스·설정·정본 문서가 *"어느 도구 명령도 PoC 바이트를 받지 않는다 / 도구
# 관측은 PoC와 무관하다"* 를 **현재형으로 단언한다.** ADR 0014
# (`docs/decisions/0014-poc-tool-runs-the-task-image.md`)가 `poc` 도구를 과제 `-vul` 이미지로
# 바꿔 그 사실을 뒤집었으므로, 그렇게 말하는 자리는 **거짓**이다.
#
# 🔴 **축을 세 번 갈았고 앞의 둘은 샜다. 그 수를 적는다 — 선언이 아니라 수다(CLAUDE.md §2.5).**
#   ① **이슈 번호**(`#513`) → `src`·`config`·`docs`에서 **10건**. 번호를 안 쓰는 자리를 통째로
#      놓쳤다: `docs/agent-contracts.md`의 도구 표 행 · `src/schemas/auditor.py`의
#      `probe` 설명 **둘** ·
#      `src/schemas/pov.py`의 `PovAction` 설명. PR #564에서 **같은 실수를 이미 한 번** 했다.
#   ② **동사 철자**(`인자로 받지 않|읽지 않|PoC와 무관|도달 불가`) → **양방향으로 샜다.**
#      과탐: `도달 불가`가 D54·D65·D80·`cybergym_oracle.py` 등 **무관한 20여 건**을 물었다.
#      미탐: `src/interfaces/tool_runner.py`의 「인자로 **안 넘긴다**」와
#      `src/adapters/fake_tools.py`의
#      「있어도 **읽지 않는다**」를 놓쳤다 — **단언이 한 줄에 안 담긴다**(네 줄 떨어져 있다).
#   ③ **「대상 + 부정 + 도구어」를 400자 창에서 동시 요구** → **과탐 44건.**
#      `input_bytes is not None:`
#      같은 코드 줄까지 물었고, 도구어가 대상 **앞**에 오는 실형태(`도구가 PoC를 읽지 않는 동안`)는
#      전방 탐색이라 **놓쳤다**. 느슨한 동시출현은 이 명제를 못 가른다.
#   ④ 그래서 **실형태를 열거한다.** 아래 각 갈래는 저장소에서 온 것이고 양성 핀이 출처를 든다.
#
# ⚠ **못 무는 것 — 적어 둔다(CLAUDE.md §2.5 마지막 절).** 이 검출기는 **철자**를 단언하지 성질을
# 단언하지 않는다. 같은 명제를 새 낱말로 쓰면 안 걸린다 — 「그 바이너리는 바이트를 참조하지
# 않는다」. 성질을 표현하는 형태를 못 찾았고, 그 한계가 이 주석이다. 다음 사람이 새 형태를
# 보면 **갈래를 늘리고 그 출처를 양성 핀에 적는다.**
_POC_UNREAD = re.compile(
    r"인자로 받지 않"
    r"|인자로 안 넘긴"
    r"|인자가 아니다"
    r"|입력으로 안 받"
    r"|입력으로 받지 않"
    r"|입력 바이트를 받지 않"
    # ⏵ 2026-10-09 — **놓쳐서 더한 갈래다.** `src/schemas/auditor.py`의 `spec` 필드 설명이
    # 「도구는 이 값을 **받지 않고**」라 적었고 위 갈래 어느 것에도 안 걸렸다 — 그 문장은
    # 모델 요청 payload에 **직접** 실리는 자리라 가장 비싼 거짓이었다.
    r"|도구는 이 값을 받지 않"
    r"|도구가 이 값을 받지 않"
    # ⏵ 2026-10-09 (2차) — **적대적 리뷰가 다섯 번째 누출을 보였다.** 아래 셋은 위 갈래 어느
    # 것에도 안 걸렸다: `입력을 받지 않아`·`입력을 안 받으므로`(조사만 다르다) ·
    # 「읽게 하는 것 … 후속」(미래형으로 적어 현재형 검사를 비껴간다) · 「materialize」(영어 낱말로
    # 같은 명제를 쓴다). 🔴 **그리고 울타리가 루트 문서를 빼고 있었다** — `STATUS.md`가
    # `src config docs prompts` 밖이라 두 자리가 통째로 안 보였다.
    r"|입력을 받지 않"
    r"|입력을 안 받"
    r"|읽게 하는 것"
    # `materialize`만으로는 D65·D83·D90 등 **무관한 20여 건**을 물었다 — 대상을 같이 요구한다.
    r"|\./poc[^\n]{0,20}materialize"
    r"|materialize[^\n]{0,20}\./poc"
    r"|PoC 바이트[^\n]{0,60}materialize"
    r"|입력으로도 들어가지 않"
    r"|PoC(?: 바이트)?를 받지 않"
    r"|PoC를 읽지 않"
    # ⚠ 조사가 갈린다 — `PoC`는 「와」, `spec`은 「과」다. 한 묶음으로 쓰면 둘 중 하나가 안 걸리고,
    # 그 오류를 **양성 핀이 잡았다**(`runner.py:2519`의 실형태가 「PoC와 무관」이다).
    r"|PoC와 무관"
    r"|spec과 무관"
    r"|만드는 주체가 아직 없"
    r"|어느 명령도 (?:그 파일을 )?읽지 않"
)

# 이 명제의 **울타리**.
_POC_UNREAD_SCOPE_SUFFIXES = (".py", ".yaml", ".md")

# 울타리 **밖** — 전부 **기록**이라 고치지 않는다(CLAUDE.md §2.4 — 서술은 낡지만
# 기록은 그날의 것이다).
#   `docs/experiments/` — run 보고서. 그날의 사진이다.
#   `docs/research/`    — 그날의 조사. 이미 스스로 「부분만 낡음」을 단다.
#   `docs/testing/`     — TDD 기록.
#   `docs/decisions/`   — ADR. **0014 자신이 옛 단언을 인용해야 한다.**
#   `tests/`            — 핀은 과거 실형태를 들어도 된다. 뒤집는 것은 그 핀 자신의 일이다.
#
# 🔴 **프롬프트는 통째로 빼지 않는다.** 지난 판(v2·v3·v4…)은 불변 기록이라 빼지만 **서빙 판은
# 기록이 아니라 지금 모델에게 가는 글**이다. 첫 판은 `prompts/`를 통째로 뺐고, 그 구멍이 v5
# prefix의 **자기모순을 숨겼다** — 한계 문단만 고치고 그 문단을 가리키는 세 자리(`rationale` 줄 ·
# 루프 규율 · 출력 절)가 옛 명제를 그대로 들고 있었는데 검출기가 못 봤다(적대적 리뷰 2026-10-09 H1).
# 그래서 **서빙 판만 울타리 안**이다.
_POC_UNREAD_RECORD_PREFIXES = (
    "docs/experiments/",
    "docs/research/",
    "docs/testing/",
    "docs/decisions/",
    "tests/",
)


@functools.lru_cache(maxsize=1)
def _frozen_prompt_versions() -> tuple[str, ...]:
    """울타리 밖인 **지난** 프롬프트 판의 경로 조각. 서빙 판은 여기 없다."""
    from src.orchestrator.nodes.pov import _WIDE_INPUT_VERSIONS  # noqa: PLC0415
    from tests.test_pov_loop import _served_pov_version  # noqa: PLC0415

    served = _served_pov_version()
    assert served in _WIDE_INPUT_VERSIONS, (
        f"서빙 pov 판 {served!r}이 `_WIDE_INPUT_VERSIONS`에 없다 — 그러면 되먹임이 조용히 사라진다"
    )
    return tuple(f"prompts/pov/{v}/" for v in sorted(_WIDE_INPUT_VERSIONS | {"v2"}) if v != served)


# 🔴 **셋을 다 이름으로 든 문장은 제외한다 — 이것이 이 검출기의 명제를 정확히 하는 자리다.**
# 막으려는 것은 *"`poc`도 PoC를 받지 않는다"*이고, `asan`·`build`·`gdb` **셋에 한정된** 서술은
# ADR 0014 뒤에도 **참**이다(셋은 우리가 빌드한 대상을 돌린다 — 바꾸려면 기각된 A안이 필요하다).
# 첫 판은 그 구분이 없어 참인 문장까지 금지했고, 그래서 내가 **참인 서술을 다른 낱말로 돌려 쓰는**
# 식으로 피해 갔다 — 그것이 바로 CLAUDE.md §2.5가 적는 「핀 문자열을 남긴 채 결함을 되살리는」
# 모양의 거울상이다. 이제 **범위**로 가른다.
#
# ⚠ **못 가르는 것**: 셋 중 둘만 들고 `poc`도 포함하는 문장은 제외되지 않는다(맞다). 반대로 셋을
# 다 들면서 `poc`까지 묶는 거짓 문장은 **빠져나간다** — 그 축은 리뷰가 본다.


def _scoped_to_the_three(text: str, start: int) -> bool:
    """그 자리가 **셋에 한정된** 서술인가 — **가장 가까운 주어**로 가른다.

    창 안에 세 이름이 다 있는지로 보면 안 된다: 갈라 쓴 문장은 **필연적으로** `poc`와 셋을 다
    언급하므로(「`poc`는 받는다 … 나머지 셋은 안 받는다」) 그 규칙은 참인 서술을 못 통과시킨다.
    그래서 **부정 어구 바로 앞의 주어**를 본다 — 그것이 셋 중 하나면 범위가 셋이고, `poc`면 거짓
    단언이다. 실형태가 「나머지 셋(`asan`·`build`·`gdb`)은 … 입력으로 받지 않는다」라 주어가
    술어 앞 100자 안에 있다.
    """
    window = text[max(0, start - 160) : start]
    positions = {
        name: window.rfind(name) for name in ("asan", "gdb", "build", "poc") if name in window
    }
    if not positions:
        return False
    nearest = max(positions, key=lambda name: positions[name])
    return nearest != "poc"


def _poc_unread_scope() -> list[Path]:
    """이 명제의 울타리 안에 있는 추적 파일."""
    return [
        path
        # 🔴 루트 문서(`STATUS.md`·`PROJECT-CONTEXT.md`·`CLAUDE.md`…)도 **살아 있는 소스**다 —
        # 첫 판이 `src config docs prompts`만 세어 `STATUS.md`의 두 자리를 통째로 놓쳤다.
        for path in _tracked("src", "config", "docs", "prompts", "*.md")
        if path.suffix in _POC_UNREAD_SCOPE_SUFFIXES
        if not _rel(path).startswith(_POC_UNREAD_RECORD_PREFIXES)
        # 지난 프롬프트 판만 뺀다 — 서빙 판은 울타리 **안**이다(위 주석).
        if not _rel(path).startswith(_frozen_prompt_versions())
    ]


def test_the_poc_unread_regex_matches_the_real_forms() -> None:
    """🔴 **실형태를 핀으로 쓴다 — 지어낸 샘플은 잡히는 형태만 고르게 된다**(CLAUDE.md §2.5).

    양성 아홉은 전부 **이 저장소에서 온 줄**이고 출처를 옆에 적었다. 음성 넷은 이 검출기가
    **막으면 안 되는** 문장이다 — 「읽지 않는다」를 쓰지만 대상이 PoC가 아니거나 명제가 다르다.
    """
    # 양성 — `src/adapters/container_tools.py:79`
    assert _POC_UNREAD.search("**어느 명령도 그 파일을 인자로 받지 않는다**(`plan()`이")
    # 양성 — `src/interfaces/tool_runner.py:46` (②가 놓친 갈래)
    assert _POC_UNREAD.search("명령의 인자로 안 넘긴다(#513)")
    # 양성 — `src/adapters/container_tools.py:491`
    assert _POC_UNREAD.search("(`ToolArgs.input_bytes`)은 이 명령의 인자가 아니다(모듈 docstring")
    # 양성 — `docs/agent-contracts.md:558`
    assert _POC_UNREAD.search('"도구 넷 모두 PoC를 입력으로 안 받는다"는 사실만 적는다')
    # 양성 — `src/schemas/auditor.py:166`·`:247`
    assert _POC_UNREAD.search("도구는 입력 바이트를 받지 않는다 — `spec`을 실어도")
    # 양성 — `src/schemas/pov.py:165` (「입력으로 받지 않는다」)
    assert _POC_UNREAD.search("`gen`이 만든 PoC 바이트를 입력으로 받지 않는다")
    # 양성 — `config/budgets.yaml:290` (③이 놓친 갈래 — 도구어가 대상 **앞**에 온다)
    assert _POC_UNREAD.search("도구가 PoC를 읽지 않는 동안(이슈 #513) 관측의")
    # 양성 — `src/orchestrator/runner.py:2322`
    assert _POC_UNREAD.search("도구 관측의 `ok`·`exit`·꼬리는 PoC와 무관하다(호스트")
    # 양성 — `src/adapters/container_tools.py:489`
    assert _POC_UNREAD.search("PoC 실행 파일을 만드는 주체가 아직 없다(STATUS.md §5 PoV)")
    # 음성 — 채점기 값과 지정의 경계(ADR 0013). 같은 「읽지 않는다」지만 **다른 명제**다.
    assert not _POC_UNREAD.search("지정 로직은 채점기가 돌려준 값을 읽지 않는다")
    # 음성 — 전처리의 파일 누락. 대상이 PoC가 아니다.
    assert not _POC_UNREAD.search("그 17KB가 어느 단계에도 들어가지 않는데, 에러가 아니므로")
    # 음성 — ②의 과탐 축. 이 명제와 무관한 「도달 불가」 20여 건이 여기서 빠진다.
    assert not _POC_UNREAD.search("그 조건이 안 서면 E4는 도달 불가다")
    # 음성 — ADR 0014 **뒤**의 참 서술. 이것을 막으면 고칠 길이 없다.
    assert not _POC_UNREAD.search(
        "`poc` 도구는 과제 `-vul` 이미지에 PoC를 `/tmp/poc`로 바인드해 돌린다"
    )


def test_no_live_source_says_the_tools_cannot_receive_the_poc() -> None:
    """🔴 **ADR 0014가 뒤집은 사실을 살아 있는 소스가 계속 말하지 않는다**(CLAUDE.md §2.3).

    **왜 기계가 세는가.** 이 명제의 전수를 손으로 **세 번** 돌렸고 **세 번 다 반쪽이었다** —
    번호 축은 번호 없는 네 자리를, 동사 축은 줄을 넘는 두 자리를, 동시출현 축은 44건을 과탐하고도
    실형태 하나를 놓쳤다(위 `_POC_UNREAD` 주석의 수). 네 번째 축이 맞다는 보증은 없고, 있는
    보증은 **이 검사가 빨개진다**는 것뿐이다.

    **울타리는 `_POC_UNREAD_RECORD_PREFIXES`가 든다** — 기록은 고치지 않는다. 프롬프트 판이 그
    울타리 안인 것이 특히 중요하다: 서빙 prefix의 거짓이 된 문단은 **그 판을 고치는 것이 아니라
    다음 판**에서 고친다(`prompts/pov/v5/`).
    """
    offenders = [
        f"{_rel(path)}:{text.count(chr(10), 0, match.start()) + 1}: {match.group(0)}"
        for path in _poc_unread_scope()
        for text in [path.read_text(encoding="utf-8")]
        for match in _POC_UNREAD.finditer(text)
        if not _scoped_to_the_three(text, match.start())
    ]
    assert offenders == [], (
        f"ADR 0014가 뒤집은 단언이 살아 있는 소스에 {len(offenders)}건 남아 있다 — "
        "「도구는 PoC를 받지 않는다」는 이제 거짓이다:\n" + "\n".join(offenders)
    )


# 🔴 **위 검출기의 짝 — 반대 방향이다.** `_POC_UNREAD`는 「넷 모두 안 받는다」를 **금지**하는데,
# 그것만으로는 **한계 서술이 통째로 지워지는 것**을 못 잡는다. `asan`·`gdb`·`build` 셋은 ADR 0014
# **뒤에도 안 받으므로**(셋을 바꾸려면 우리가 대상을 빌드해야 하고 그것이 기각된 A안이다 —
# 이슈 #507) 그 사실이 살아 있는 소스에 **남아 있어야 한다.** 금지와 요구를 둘 다
# 걸어야 「고쳤다」가
# 「지웠다」로 새지 않는다 — 이 저장소가 이미 겪은 실패다(형제 PR에서 핀 문자열을 전부 남긴 채
# 결함을 되살리는 변이가 일곱 났다 · CLAUDE.md §2.5 마지막 절).
_POC_SPLIT_KEPT = re.compile(r"`asan`[^\n]{0,40}`(?:gdb|build)`")


def test_the_three_tools_still_say_they_do_not_receive_the_poc() -> None:
    """🔴 **`_POC_UNREAD`의 짝이다 — 한계 서술이 지워지지 않았다.**

    ADR 0014는 **한 도구만** 바꿨다. 셋이 입력을 안 받는다는 사실은 그대로이고, 그것을 적는
    자리가 0건이 되면 다음 사람이 「도구가 PoC를 받는다」를 **넷 전부로** 읽는다. 금지 검출기만
    있으면 그 삭제가 초록으로 지나간다.

    **무는 것은 「셋을 한 묶음으로 부르는 자리가 있다」까지다** — 그 문장이 **무엇을 말하는지**는
    안 본다(성질을 표현하는 형태를 못 찾았다). 그 한계는 `_POC_UNREAD` 주석과 같은 결이다.
    """
    carriers = [
        _rel(path)
        for path in _poc_unread_scope()
        if _POC_SPLIT_KEPT.search(path.read_text(encoding="utf-8"))
    ]

    assert "src/adapters/container_tools.py" in carriers, (
        "`poc`를 가른 모듈이 셋을 한 묶음으로 부르는 자리를 잃었다 — ADR 0014는 한 도구만 바꿨다"
    )
    assert len(carriers) >= 3, (
        f"셋의 한계를 적는 자리가 {len(carriers)}곳뿐이다 — "
        "「고쳤다」가 「지웠다」로 샜는지 본다:\n"
        + "\n".join(f"  {c}" for c in carriers)
    )


def test_the_poc_split_regex_matches_the_real_forms() -> None:
    """실형태 핀 — 출처는 `src/adapters/container_tools.py`·`docs/agent-contracts.md`다."""
    assert _POC_SPLIT_KEPT.search("나머지 셋(`asan`·`gdb`·`build`)은 그대로 안 받는다")
    assert _POC_SPLIT_KEPT.search("🔴 **`asan`·`gdb`·`build` 셋은 그대로다**")
    assert not _POC_SPLIT_KEPT.search("`poc` 도구는 과제 이미지를 돌린다")
    # 음성 — 셋 중 하나만 든 문장은 묶음이 아니다.
    assert not _POC_SPLIT_KEPT.search("`asan`은 ASan+UBSan으로 새로 빌드한다")
