"""D·R 항목 본문은 인덱스가 아니라 **한 항목당 한 파일**에 있다.

**왜 이 검사가 있는가.** 예전에는 `docs/open-questions.md`의 표 한 행이 본문 전체를
들고 있었다(최대 5,500자). 그래서 두 사람이 서로 다른 D를 고쳐도 같은 파일의 같은 표를
건드렸고, 열을 하나 추가하는 PR은 55행 **전부**와 충돌했다(#86 ↔ #87이 그랬다).

`docs/decisions/`가 이미 쓰는 구조로 바꾼다 — 인덱스는 한 줄만 들고, 본문은 각자 파일에.
그러면 서로 다른 항목을 고치는 두 PR이 **파일을 공유하지 않는다.**

**2026-09-01 — R(블로킹 리스크)도 같은 구조로 들어왔다.** 그때까지 이 검사는 D 계열만
봐서 R은 어떤 검사도 안 받았고, 인덱스에 **1,611자짜리 R6 행**이 살아 있었다. 계열 문자를
`[DR]`로 넓히고 본문을 `D/`·`R/` 하위 디렉터리로 갈랐다.

이 검사는 그 구조가 되돌아가는 것을 막는다.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX = REPO_ROOT / "docs" / "open-questions.md"
BODIES = REPO_ROOT / "docs" / "open-questions"

# 인덱스 행이 이보다 길면 본문이 표 안으로 되돌아온 것이다.
_MAX_ROW = 200

# 계열 문자가 그대로 하위 디렉터리 이름이다 — `D42` → `D/D42.md`, `R6` → `R/R6.md`.
_ROW = re.compile(r"^\| ([DR]\d+) \|")


def _rows() -> list[tuple[int, str, str]]:
    rows = []
    for lineno, line in enumerate(INDEX.read_text(encoding="utf-8").splitlines(), 1):
        m = _ROW.match(line)
        if m:
            rows.append((lineno, m.group(1), line))
    return rows


def test_the_row_regex_actually_finds_both_series() -> None:
    """`_rows()`가 한쪽 계열을 못 잡으면 아래 검사들이 **그쪽에 대해 조용히 통과한다.**

    R이 정확히 그 상태였다 — 정규식이 D만 봐서 1,611자 행이 있어도 아무것도 안 잡았다.
    표 서식이 또 바뀌면(굵게, 앞 공백 등) 여기서 먼저 깨진다.
    """
    found = {did[0] for _, did, _ in _rows()}
    assert found == {"D", "R"}, f"인덱스에서 찾은 계열이 {sorted(found)}다 — 표 서식이 바뀌었나"


def test_every_d_has_a_body_file() -> None:
    """인덱스의 모든 항목이 제 계열 디렉터리의 파일을 가리킨다."""
    missing = []
    for _, did, line in _rows():
        relative = f"{did[0]}/{did}.md"
        if not (BODIES / relative).is_file():
            missing.append(f"{did}: docs/open-questions/{relative} 가 없다")
        elif f"open-questions/{relative}" not in line:
            missing.append(f"{did}: 인덱스 행이 {relative} 을 링크하지 않는다")
    assert missing == [], "\n".join(missing)


def test_every_body_file_has_an_index_row() -> None:
    """인덱스 행이 없는 본문은 **아무 검사도 안 받는다.**

    위 검사는 인덱스 행에서 출발해 파일을 찾으므로 **반대 방향을 못 본다.** 행을 지우고
    파일을 남기면(고아 본문) 그 파일은 조용히 남고, 계열 디렉터리 밖에 만든 파일도
    마찬가지다 — 둘 다 인덱스가 안 가리키는 파일이라는 한 가지 사실의 두 모습이다.

    처음에는 `BODIES.glob("*.md")` 로 계열 밖만 봤는데, `glob` 은 재귀하지 않아
    **`D/`·`R/` 안의 고아는 못 봤다.** 인덱스가 가리키는 집합과 디스크의 집합을
    맞대는 편이 두 경우를 한 번에 덮는다.
    """
    indexed = {f"{did[0]}/{did}.md" for _, did, _ in _rows()}
    on_disk = {p.relative_to(BODIES).as_posix() for p in BODIES.rglob("*.md")}
    orphans = sorted(on_disk - indexed)
    assert orphans == [], (
        f"인덱스 행이 없는 본문 {len(orphans)}개: {orphans} — "
        "행을 지웠으면 파일도 지우고, 계열 디렉터리 밖이면 옮긴다"
    )


def test_index_rows_stay_thin() -> None:
    """인덱스 행에 본문을 넣지 않는다 — 그것이 충돌의 원인이었다."""
    fat = [
        f"docs/open-questions.md:{lineno}: {did} 행이 {len(line)}자다 "
        f"(상한 {_MAX_ROW}) — 본문은 docs/open-questions/{did[0]}/{did}.md 로 옮긴다"
        for lineno, did, line in _rows()
        if len(line) > _MAX_ROW
    ]
    assert fat == [], f"인덱스 행 {len(fat)}개가 본문을 들고 있다:\n" + "\n".join(fat[:15])
