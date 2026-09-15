"""닫힌 D·R 항목 위에 붙는 **시점 노트**가 지목하는 문장이 실재하고, 서로를 가리킨다.

**무엇이 시점 노트인가.** 항목이 닫힌 뒤 세상이 움직여 본문의 현재형 서술이 거짓이 되면,
본문을 지우는 대신 상단에 한 줄을 붙여 *"아래 본문은 …시점 서술이다"* 라고 범위를 짓는다.
닫힌 항목의 역사적 결정 기록(*"PR #178은 (b)를 골랐다"* — 그 시점 사실이라 참)을
falsify하지 않으면서 현재형 오독만 막는 방식이다.

그 노트는 **어느 문장이 낡았는지**를 `*"…"*` 인용으로 지목한다. 인용이 지목을 하려면
두 방향이 다 서 있어야 한다.

**왜 이 검사가 있는가 — 실제로 둘 다 깨졌다.**

*정방향* — PR #200의 `D68.md` 노트가 *"지금 상태다"* 를 인용했는데 **그 문구가 D68 본문에
없었다.** D69·D70 노트를 복붙하면서 안 고친 것이다(그쪽엔 `D69.md:62`·`D70.md:73`에 실재).
노트가 없는 문장을 지목하니 읽는 사람이 찾다가 못 찾는다 — CLAUDE.md §2.1이 적은
*"끊어진 참조를 빈 문서로 옮긴 것이라 더 나빴다"* 와 같은 모양이다.

*역방향* — 노트는 파일 최상단(인덱스 줄 앞)에 있고 지목된 문장은 **37~50줄 아래**
「## 선택지」 안에 있었다. `#선택지` 앵커로 그 절만 여는 사람은 상단 노트를 안 지나간다.
그러면 낡은 현재형을 그대로 읽는다 — 노트가 막으려던 바로 그 오독이다.

`test_every_d_has_a_body_file` ↔ `test_every_body_file_has_an_index_row` 와 같은
정방향·역방향 쌍이다. 한쪽만 있으면 반대 방향이 조용히 통과한다.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BODIES = REPO_ROOT / "docs" / "open-questions"

# 시점 노트의 첫 줄. 인용 앞의 「아래 본문은 …시점 서술이다」가 이 노트의 표지다.
_NOTE = re.compile(r"^>\s*⚠\s*\*\*\d{4}-\d{2}-\d{2}\s*—\s*아래 본문은\b.*시점 서술이다")

# 노트가 지목에 쓰는 인용. `*"…"*` 하나가 낡은 문장 하나를 가리킨다.
_QUOTE = re.compile(r'\*"([^"]+)"\*')

# 인용이 문장 가운데를 접을 때 쓰는 말줄임. 접힌 조각은 각각 실재해야 한다.
_ELLIPSIS = "…"

# 낡은 문장 쪽에 붙는 되돌림 표지. 이 문자열이 그 줄에 있으면 앵커로 들어온 사람도 노트를 만난다.
_BACKREF = "위 시점 노트"


def _bodies() -> list[Path]:
    return sorted(BODIES.rglob("*.md"))


def _notes(path: Path) -> list[tuple[int, str]]:
    return [
        (lineno, line)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _NOTE.match(line)
    ]


def _fragments(quote: str) -> list[str]:
    """`(c) … 지금 상태다` 처럼 접힌 인용은 조각마다 따로 찾는다."""
    return [piece.strip() for piece in quote.split(_ELLIPSIS) if piece.strip()]


def test_the_note_regex_actually_finds_the_notes() -> None:
    """`_NOTE`가 한 건도 못 잡으면 아래 둘이 **아무것도 안 보고 통과한다.**

    이 저장소가 이미 한 번 겪은 실패 모양이다 — `test_open_questions_layout` 의 정규식이
    D만 봐서 R의 1,611자 행이 있어도 아무것도 안 잡았다. 노트 서식이 바뀌면 여기서 먼저
    깨져서, 아래 둘이 조용해진 것을 **조용한 통과가 아니라 실패로** 알린다.
    """
    found = {p.name for p in _bodies() if _notes(p)}
    assert found, (
        "시점 노트를 한 건도 못 찾았다 — 노트 서식이 바뀌었거나 정규식이 낡았다. "
        "아래 두 검사가 지금 아무것도 안 보고 있다"
    )


def test_a_supersede_note_quotes_only_sentences_that_exist_in_its_own_file() -> None:
    """정방향 — 노트가 인용한 문구는 **같은 파일 본문에** 있어야 한다.

    없으면 노트가 허공을 지목한다. 읽는 사람은 「어느 문장이 낡았다는 거지」를 찾다가 못 찾고,
    그 파일의 다른 현재형을 임의로 의심하거나 아무것도 안 의심하고 넘어간다.
    """
    dangling = []
    for path in _bodies():
        text = path.read_text(encoding="utf-8")
        for lineno, note in _notes(path):
            # 노트 줄 자신이 인용을 들고 있으므로 본문에서 그 줄은 빼고 찾는다.
            body = text.replace(note, "")
            for quote in _QUOTE.findall(note):
                for fragment in _fragments(quote):
                    if fragment not in body:
                        dangling.append(
                            f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: "
                            f'노트가 인용한 "{fragment}" 가 이 파일 본문에 없다 — '
                            "다른 항목의 노트를 복붙했거나 본문이 그 사이에 바뀌었다"
                        )
    assert dangling == [], f"허공을 지목하는 인용 {len(dangling)}건:\n" + "\n".join(dangling)


def test_a_superseded_sentence_points_back_at_its_note() -> None:
    """역방향 — 노트가 지목한 문장 쪽에도 **노트로 되돌리는 표지**가 있어야 한다.

    노트는 파일 맨 위에 있고 낡은 문장은 수십 줄 아래 절 안에 있다. `#선택지` 같은 앵커로
    그 절만 여는 사람은 위를 안 지나가므로 노트를 못 본다 — 노트를 붙인 이유가 정확히
    그 오독을 막는 것인데 그 사람에게만 안 걸린다.

    표지는 낡은 문장과 **같은 줄**에 있어야 한다. 문단 어딘가에 있으면 그 문장만 인용해
    옮기는 다음 사람이 표지를 안 들고 간다.
    """
    unmarked = []
    for path in _bodies():
        lines = path.read_text(encoding="utf-8").splitlines()
        quoted: list[list[str]] = []
        for _, note in _notes(path):
            quoted.extend(_fragments(q) for q in _QUOTE.findall(note))
        if not quoted:
            continue
        for lineno, line in enumerate(lines, 1):
            if _NOTE.match(line):
                continue
            # `(c) … 지금 상태다` 는 조각이 **둘 다** 있는 줄만 그 문장이다. 한쪽(`(c)`)만
            # 보면 선택지 목록의 아무 줄에나 걸려 표지를 요구하게 된다.
            hit = next((frs for frs in quoted if all(f in line for f in frs)), None)
            if hit is not None and _BACKREF not in line:
                unmarked.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno}: "
                    f'노트가 지목한 "{_ELLIPSIS.join(hit)}" 가 이 줄에 있는데 '
                    f"「{_BACKREF}」 표지가 없다 — "
                    "앵커로 이 절만 여는 사람은 상단 노트를 안 지나간다"
                )
    assert unmarked == [], (
        f"되돌림 표지가 없는 낡은 문장 {len(unmarked)}건:\n" + "\n".join(unmarked)
    )
