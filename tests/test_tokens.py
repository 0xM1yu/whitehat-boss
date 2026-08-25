"""`src.tokens` — 갈아끼울 수 있는 토큰 카운터 골격 (D21).

`_counter`가 모듈 전역이므로 카운터를 바꾸는 테스트는 `finally`로 반드시 되돌린다 —
안 되돌리면 뒤 테스트가 다른 카운터를 물려받아 캡슐 회계가 통째로 이동한다.
"""

import pytest

from src import tokens
from src.tokens import estimate_tokens, using_counter


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


def test_using_counter_scopes_and_restores() -> None:
    """블록 안에서만 카운터가 바뀌고 나가면 이전 카운터로 돌아온다 — 러너가 역할
    캡슐 하나를 감싸는 계약. 3b는 이걸 `retrieval.run()` 한 번에 두른다."""
    assert estimate_tokens("12345678") == 2
    with using_counter(len):  # 글자수 그대로를 토큰 수로 쓰는 가짜 카운터
        assert estimate_tokens("12345678") == 8
    assert estimate_tokens("12345678") == 2  # 복원


def test_using_counter_restores_on_exception() -> None:
    """블록 안에서 예외가 나도 카운터는 되돌아온다 — 안 되면 뒤 후보가 남의 카운터를 문다."""
    with pytest.raises(RuntimeError):
        with using_counter(len):
            raise RuntimeError("boom")
    assert estimate_tokens("12345678") == 2


def test_using_counter_none_is_a_noop() -> None:
    """`None`이면(토크나이저 없음·fake) 아무것도 바꾸지 않고 기본 카운터를 유지한다."""
    with using_counter(None):
        assert estimate_tokens("12345678") == 2
    assert estimate_tokens("12345678") == 2


def test_using_counter_nests_and_restores_outer() -> None:
    """역할이 겹쳐 감싸여도 각 블록이 자기 이전 값으로 되돌린다 (scout 안에서 auditor 등
    가상의 중첩) — 전역 하나를 스택처럼 다룬다는 계약."""
    with using_counter(len):
        assert estimate_tokens("12345678") == 8
        with using_counter(lambda t: len(t) * 2):
            assert estimate_tokens("12345678") == 16
        assert estimate_tokens("12345678") == 8  # 바깥(len)으로 복원
    assert estimate_tokens("12345678") == 2  # 기본으로 복원
