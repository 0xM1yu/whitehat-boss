"""과제 설명(description.txt)과 후보 위치의 일치 — `candidate_max` 컷의 우선순위 (#377 (b)).

순서에만 쓴다. 설명이 틀리거나 비면 모든 후보가 0이라 종전 순서 그대로다. 토큰 규칙은
`tools/slicer/slicer_description.py`와 같다(`src`는 슬라이서를 import하지 않으므로 두 번 적는다 —
`tests/test_description_priority.py`가 둘이 같은 토큰을 내는지 묻는다).
"""

import re
from pathlib import PurePosixPath

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MIN_TOKEN = 4
# 영어 기능어와 버그 설명이 쓰는 동사. 튜닝한 목록이 아니다.
STOPWORDS = frozenset(
    "this that these those with from when where which while then than there their them they "
    "into onto over also been being have does would could should will were what some such only "
    "after before because cause causes caused return returns returned initialize uses used "
    "using allows leads".split()
)


def description_tokens(text: str | None) -> frozenset[str]:
    """설명의 식별자를 소문자로, 4자 미만과 불용어는 뺀다. 설명이 없으면 빈 집합."""
    found = (match.lower() for match in _IDENTIFIER.findall(text or ""))
    return frozenset(t for t in found if len(t) >= MIN_TOKEN and t not in STOPWORDS)


def description_rank(tokens: frozenset[str], file: str, symbol: str | None) -> int:
    """2 = 심볼 이름(또는 `_`·`::` 조각)이 토큰 · 1 = 파일 이름(확장자 뺀)이 토큰 · 0 = 없음."""
    if not tokens:
        return 0
    if symbol:
        name = symbol.lower()
        # 토큰은 이미 4자 이상이라 조각 길이를 따로 보지 않는다. `::`는 C++ 한정 이름(`Foo::bar`).
        if name in tokens or any(piece in tokens for piece in re.split(r"_|::", name)):
            return 2
    return 1 if PurePosixPath(file.replace("\\", "/")).stem.lower() in tokens else 0
