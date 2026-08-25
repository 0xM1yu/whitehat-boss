"""`src.tokens` — 갈아끼울 수 있는 토큰 카운터 골격 (D21).

`_counter`가 모듈 전역이므로 카운터를 바꾸는 테스트는 `finally`로 반드시 되돌린다 —
안 되돌리면 뒤 테스트가 다른 카운터를 물려받아 캡슐 회계가 통째로 이동한다.
"""

from src import tokens
from src.tokens import estimate_tokens


def test_default_counter_is_the_char_estimate() -> None:
    """기본은 `max(1, len//4)` 그대로다 — 이 값이 바뀌면 캡슐 예산·검증기가 전부 이동한다."""
    assert [estimate_tokens(s) for s in ["", "x", "12345678", "a" * 100]] == [1, 1, 2, 25]


def test_set_counter_swaps_and_reset_restores() -> None:
    """조립 직전에 카운터를 바꾸고 끝나면 되돌리는 계약."""
    try:
        assert estimate_tokens("12345678") == 2
        tokens.set_counter(len)  # 글자수 그대로를 토큰 수로 쓰는 가짜 카운터
        assert estimate_tokens("12345678") == 8
    finally:
        tokens.reset_counter()
    assert estimate_tokens("12345678") == 2


def test_floor_of_one_holds_for_any_counter() -> None:
    """어떤 카운터가 0을 내도 바닥 1은 `estimate_tokens`가 강제한다 — 예산에 공짜 항목 금지."""
    try:
        tokens.set_counter(lambda _t: 0)
        assert estimate_tokens("아무 본문") == 1
    finally:
        tokens.reset_counter()
