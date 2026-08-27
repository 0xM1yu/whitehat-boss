"""`src.adapters.tokenizer` — 실 토크나이저 로더 (D21 (b)).

**`tokenizers`를 최상단에서 하드 import 하는 것이 이 파일의 계약이다.** `importorskip`으로
두면 라이브러리가 없는 CI에서 조용히 건너뛰고, 그러면 **초록불인데 실카운팅을 한 줄도
검증하지 않는 상태**가 된다 — `tests/test_indexer.py`가 tree-sitter에 대해 세운 것과 같은
논법이고, `requirements.txt`에 `tokenizers`가 있는 이유가 그것이다. 없으면 skip이 아니라
**collection error**가 나야 한다: 그것이 "CI에서 실제로 돌았다"의 증명이다.

로더 쪽(`src/adapters/tokenizer.py`)이 지연 import·`None` 폴백을 유지하는 것과 어긋나지
않는다 — 그쪽은 **부분 설치 환경의 런타임**을 위한 것이고, 여기는 **CI 검증**을 위한 것이다.
"""

from pathlib import Path

from tokenizers import Tokenizer, models, pre_tokenizers

from src.adapters.tokenizer import (
    counter_for_source,
    counter_from_tokenizer,
    tokenizer_dir_from_source,
)


def test_none_when_no_tokenizer_file(tmp_path: Path) -> None:
    """파일이 없으면 None — 호출자가 기본 카운터(글자수 근사)를 유지하도록."""
    assert counter_from_tokenizer(tmp_path) is None  # 빈 디렉터리
    assert counter_from_tokenizer(tmp_path / "nope.json") is None  # 없는 파일


def test_counts_with_a_real_tokenizer_file(tmp_path: Path) -> None:
    """tokenizer.json이 있으면 그 토크나이저로 센다.

    **이 검사가 이 PR의 값 전부다** — `len//4`였다면 갈리는 입력을 골랐다(아래 두 번째 단언).
    그래서 skip으로 두지 않는다(모듈 docstring).
    """
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


# --- source(hf://·file://) → 경로 해석 (3a) ---


def test_dir_from_hf_source(tmp_path: Path) -> None:
    """`hf://repo@rev` → HF 캐시 관례 경로 (repo의 `/`는 `--`)."""
    got = tokenizer_dir_from_source(
        "hf://Qwen/Qwen3-4B-Instruct-2507-FP8@abc123", hub_cache=tmp_path
    )
    assert got == tmp_path / "models--Qwen--Qwen3-4B-Instruct-2507-FP8" / "snapshots" / "abc123"


def test_dir_from_file_source() -> None:
    """`file://<path>` → 그 경로 자체 (모델 디렉터리)."""
    assert tokenizer_dir_from_source("file:///data/models/x") == Path("/data/models/x")


def test_dir_none_for_unusable_source(tmp_path: Path) -> None:
    """리비전 없음·다른 스킴·빈 값은 None — 호출자가 기본 카운터를 유지한다."""
    assert tokenizer_dir_from_source("hf://repo-without-rev", hub_cache=tmp_path) is None
    assert tokenizer_dir_from_source("sha256:0000") is None
    assert tokenizer_dir_from_source("") is None


def test_counter_for_source_end_to_end(tmp_path: Path) -> None:
    """`source` 하나로 카운터까지 — 해석된 경로에 tokenizer.json이 있으면 실 카운터."""
    tokenizer = Tokenizer(models.WordLevel(vocab={"a": 0, "b": 1, "[UNK]": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    snapshot = tmp_path / "models--Org--Name" / "snapshots" / "rev1"
    snapshot.mkdir(parents=True)
    tokenizer.save(str(snapshot / "tokenizer.json"))

    counter = counter_for_source("hf://Org/Name@rev1", hub_cache=tmp_path)
    assert counter is not None
    assert counter("a b") == 2


def test_counter_for_source_none_when_file_absent(tmp_path: Path) -> None:
    """경로는 만들어져도 그 자리에 tokenizer.json이 없으면 None."""
    assert counter_for_source("hf://Org/Name@rev1", hub_cache=tmp_path) is None
