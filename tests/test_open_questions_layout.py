"""D 항목 본문은 인덱스가 아니라 **한 항목당 한 파일**에 있다.

**왜 이 검사가 있는가.** 예전에는 `docs/open-questions.md`의 표 한 행이 본문 전체를
들고 있었다(최대 5,500자). 그래서 두 사람이 서로 다른 D를 고쳐도 같은 파일의 같은 표를
건드렸고, 열을 하나 추가하는 PR은 55행 **전부**와 충돌했다(#86 ↔ #87이 그랬다).

`docs/decisions/`가 이미 쓰는 구조로 바꾼다 — 인덱스는 한 줄만 들고, 본문은 각자 파일에.
그러면 서로 다른 D를 고치는 두 PR이 **파일을 공유하지 않는다.**

이 검사는 그 구조가 되돌아가는 것을 막는다.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX = REPO_ROOT / "docs" / "open-questions.md"
BODIES = REPO_ROOT / "docs" / "open-questions"

# 인덱스 행이 이보다 길면 본문이 표 안으로 되돌아온 것이다.
_MAX_ROW = 200

_D_ROW = re.compile(r"^\| (D\d+) \|")


def _d_rows() -> list[tuple[int, str, str]]:
    rows = []
    for lineno, line in enumerate(INDEX.read_text(encoding="utf-8").splitlines(), 1):
        m = _D_ROW.match(line)
        if m:
            rows.append((lineno, m.group(1), line))
    return rows


def test_every_d_has_a_body_file() -> None:
    """인덱스의 모든 D가 `docs/open-questions/`의 파일을 가리킨다."""
    missing = []
    for _, did, line in _d_rows():
        hits = list(BODIES.glob(f"{did}.md"))
        if not hits:
            missing.append(f"{did}: docs/open-questions/{did}.md 가 없다")
        elif f"open-questions/{hits[0].name}" not in line:
            missing.append(f"{did}: 인덱스 행이 {hits[0].name} 을 링크하지 않는다")
    assert missing == [], "\n".join(missing)


def test_index_rows_stay_thin() -> None:
    """인덱스 행에 본문을 넣지 않는다 — 그것이 충돌의 원인이었다."""
    fat = [
        f"docs/open-questions.md:{lineno}: {did} 행이 {len(line)}자다 "
        f"(상한 {_MAX_ROW}) — 본문은 docs/open-questions/{did}.md 로 옮긴다"
        for lineno, did, line in _d_rows()
        if len(line) > _MAX_ROW
    ]
    assert fat == [], f"인덱스 행 {len(fat)}개가 본문을 들고 있다:\n" + "\n".join(fat[:15])
