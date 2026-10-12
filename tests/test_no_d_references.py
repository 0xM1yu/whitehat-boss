"""프로덕션 코드가 `docs/open-questions.md`의 D 행에 의존하지 않는다.

**왜 이 검사가 있는가.** D 표는 폐기 중인 문서다(CLAUDE.md §2). 그런데 `src/`·`config/`가
그 표의 행을 **번호로** 가리키고 있었다 — 208건 / 46파일. 표를 지우면 그 참조가 전부
끊기고, `test_decision_references_point_at_a_real_row`가 그것을 CI 실패로 만든다.

**무엇을 강제하나.** 결함의 원천은 이제 GitHub Issue다. 코드가 미해결 사항을 가리킬 때는
D 번호가 아니라 이슈 번호(`#91`)를 쓴다. 그러면 표가 사라져도 참조가 살아 있다.

**여기서 `tests/`를 보지 않는 이유**: 테스트 docstring의 D 참조는 "이 테스트가 어느 결함을
재현하는가"의 이력이고, 프로덕션 코드가 문서에 의존하는 것과는 성격이 다르다. 별건으로 뺀다.
"""

import re
from pathlib import Path

from tests.conftest import tracked_files

REPO_ROOT = Path(__file__).resolve().parent.parent

# `test_decision_references_point_at_a_real_row`와 같은 패턴이다 — 한쪽만 고치면 갈린다.
_D_REF = re.compile(r"(?<![A-Za-z0-9])D(\d+)(?![0-9A-Za-z])")


def test_production_code_does_not_reference_open_questions_d_rows() -> None:
    """`src/`·`config/`에 `D<숫자>` 참조가 없다.

    미해결 사항을 가리켜야 하면 이슈 번호를 쓴다(`#91`). 해소된 것을 가리켜야 하면
    그것을 닫은 PR·ADR을 쓴다. **D 번호는 폐기되는 표에만 있는 좌표다.**
    """
    offenders: list[str] = []
    for path in tracked_files("src/*", "config/*"):
        if path.suffix not in {".py", ".yaml", ".yml"}:
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in _D_REF.finditer(line):
                offenders.append(f"{rel}:{lineno}: {match.group(0)} — 이슈 번호로 바꾼다")

    assert offenders == [], (
        f"프로덕션 코드가 D 표를 {len(offenders)}곳에서 가리킨다:\n"
        + "\n".join(offenders[:40])
    )
