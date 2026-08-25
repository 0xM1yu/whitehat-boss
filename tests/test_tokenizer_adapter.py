"""`src.adapters.tokenizer` — 실 토크나이저 로더 (D21 (b))."""

from pathlib import Path

import pytest

from src.adapters.tokenizer import counter_from_tokenizer


def test_none_when_no_tokenizer_file(tmp_path: Path) -> None:
    """파일이 없으면 None — 호출자가 기본 카운터(글자수 근사)를 유지하도록."""
    assert counter_from_tokenizer(tmp_path) is None  # 빈 디렉터리
    assert counter_from_tokenizer(tmp_path / "nope.json") is None  # 없는 파일


def test_counts_with_a_real_tokenizer_file(tmp_path: Path) -> None:
    """tokenizer.json이 있으면 그 토크나이저로 센다.

    `tokenizers`는 하드 의존이 아니므로 없으면 skip한다(실카운팅 검증은 그쪽이 있을 때만).
    """
    pytest.importorskip("tokenizers")
    from tokenizers import Tokenizer, models, pre_tokenizers

    tokenizer = Tokenizer(
        models.WordLevel(vocab={"a": 0, "b": 1, "c": 2, "[UNK]": 3}, unk_token="[UNK]")
    )
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(tmp_path / "tokenizer.json"))

    counter = counter_from_tokenizer(tmp_path)  # 디렉터리 → 안의 tokenizer.json
    assert counter is not None
    assert counter("a b c") == 3  # 세 단어 → 세 토큰
    # len//4였다면 "a b c a"(7자)는 1이다 — 실 토크나이저라 4가 나온다는 것이 요점.
    assert counter("a b c a") == 4
