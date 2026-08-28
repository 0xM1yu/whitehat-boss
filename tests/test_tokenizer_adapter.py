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

import pytest
from pydantic import ValidationError
from tokenizers import Tokenizer, models, pre_tokenizers

from src.adapters.factory import build_deps
from src.adapters.sqlite_store import SqliteStateStore
from src.adapters.tokenizer import (
    clear_counter_cache,
    counter_for_source,
    counter_from_tokenizer,
    counter_reason_for_source,
    tokenizer_dir_from_source,
)
from src.config import load_config
from src.schemas.capsule import CapsuleSection, ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import CapsuleLevel
from src.tokens import estimate_tokens, using_counter


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


# --- 폴백 사유 (D21 (b), `token_counter` 이벤트의 `{role}_reason`) ---
#
# **네 사유를 가르는 것이 요점이다.** 결과는 넷 다 글자수 근사로 같지만 대응이 전부 다르다 —
# `graph_index`가 `missing`과 `unsupported_version`을 가른 것과 같은 이유다.
# `library_absent`는 여기서 검사하지 않는다: 그 상태의 이 파일은 collection error이므로
# (모듈 docstring) 테스트가 도달할 수 없는 분기다.


def test_reason_is_none_when_the_counter_is_real(tmp_path: Path) -> None:
    """실 카운터를 얻었으면 사유는 없다 — `real`은 사유를 갖지 않는다."""
    tokenizer = Tokenizer(models.WordLevel(vocab={"a": 0, "[UNK]": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    snapshot = tmp_path / "models--Org--Name" / "snapshots" / "rev1"
    snapshot.mkdir(parents=True)
    tokenizer.save(str(snapshot / "tokenizer.json"))

    assert counter_reason_for_source("hf://Org/Name@rev1", hub_cache=tmp_path) is None


def test_reason_is_unresolved_source_when_the_revision_is_missing(tmp_path: Path) -> None:
    """리비전이 없으면 경로를 못 만든다 — registry가 고칠 일이지 캐시를 받을 일이 아니다."""
    assert (
        counter_reason_for_source("hf://repo-without-rev", hub_cache=tmp_path)
        == "unresolved_source"
    )


def test_reason_is_file_absent_when_the_snapshot_is_not_downloaded(tmp_path: Path) -> None:
    """경로는 섰는데 파일이 없다 — 캐시를 받으면 풀린다. 위와 다른 사건이다."""
    assert counter_reason_for_source("hf://Org/Name@rev1", hub_cache=tmp_path) == "file_absent"


def test_build_deps_reports_real_when_the_tokenizer_file_is_there(tmp_path: Path) -> None:
    """`build_deps`까지 이어져 `counter_report`가 `real`과 그 `source`를 낸다 (D21 (b)).

    **`tests/test_adapters.py`가 아니라 여기 있는 이유**: 이 검사만 `tokenizers`를 실제로
    필요로 한다. 저쪽에 두면 라이브러리가 없는 환경에서 **어댑터 테스트 전체**가 collection
    error가 되고, 하드 의존의 파급이 이 파일 하나를 넘는다. 나머지 `counter_report` 검사
    (`no_source`·`file_absent`·역할 가드)는 라이브러리가 필요 없어 저쪽에 남아 있다.

    **`source`를 싣고 해석된 경로는 안 싣는다** — trace 재현성 기준이 경로 절댓값을
    금지하고(`orchestrator/trace.py`), `source`에 리비전이 박혀 있어 스냅샷은 그것으로
    특정된다.
    """
    target_root = tmp_path / "target"
    target_root.mkdir()
    (target_root / "parser.c").write_text("void f(void) {}\n", encoding="utf-8")

    model_dir = tmp_path / "model"
    model_dir.mkdir()
    tokenizer = Tokenizer(models.WordLevel(vocab={"a": 0, "[UNK]": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(model_dir / "tokenizer.json"))

    source = f"file://{model_dir.as_posix()}"
    base = load_config("single_gpu_12g")
    key = base.model_key_for("scout")
    entries = dict(base.models)
    entries[key] = entries[key].model_copy(update={"source": source})
    config = base.model_copy(update={"models": entries})

    deps = build_deps(
        config,
        run_id="run_20260901_0001",
        source_root=target_root,
        artifacts_root=tmp_path / "artifacts",
    )

    assert deps.counter_for_role("scout") is not None
    assert deps.counter_report["scout_counter"] == "real"
    assert deps.counter_report["scout_source"] == source
    assert deps.counter_report["scout_reason"] is None
    # 같은 프로필의 auditor는 fake다 — 한 run 안에서 역할별로 갈린다는 것이 요점이다.
    assert deps.counter_report["auditor_counter"] == "approx"
    assert deps.counter_report["auditor_reason"] == "no_source"


def test_cache_can_be_cleared_when_the_file_appears(tmp_path: Path) -> None:
    """캐시를 비우지 않으면 없다가 생긴 파일을 못 본다 — 그 손잡이가 있어야 한다.

    지금 테스트가 안전한 것은 `tmp_path`가 매번 달라 키가 겹치지 않아서이고, 그것은 계약이
    아니라 우연이다. 같은 `source`를 두 번 물어 그 우연에 기대지 않는지 확인한다.
    """
    source = "hf://Org/Name@rev1"
    assert counter_for_source(source, hub_cache=tmp_path) is None  # 아직 없다

    tokenizer = Tokenizer(models.WordLevel(vocab={"a": 0, "[UNK]": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    snapshot = tmp_path / "models--Org--Name" / "snapshots" / "rev1"
    snapshot.mkdir(parents=True)
    tokenizer.save(str(snapshot / "tokenizer.json"))

    assert counter_for_source(source, hub_cache=tmp_path) is None  # 캐시된 옛 판정
    clear_counter_cache()
    assert counter_for_source(source, hub_cache=tmp_path) is not None


def test_counter_and_reason_come_from_one_judgement(tmp_path: Path) -> None:
    """카운터와 기록이 갈리면 안 된다 — 갈린 기록은 없는 것보다 나쁘다.

    `_resolve_counter` 하나를 둘이 나눠 쓴다는 계약을 값으로 못박는다.
    """
    for source in ("hf://Org/Name@rev1", "hf://no-rev", "sha256:0000"):
        counter = counter_for_source(source, hub_cache=tmp_path)
        reason = counter_reason_for_source(source, hub_cache=tmp_path)
        assert (counter is None) == (reason is not None), source


def test_a_stored_capsule_only_reloads_under_the_counter_that_built_it(tmp_path: Path) -> None:
    """저장소 경계의 `model_validate`는 조립과 **같은 카운터** 안에서 돌아야 한다 (D43).

    `SqliteStateStore._load`가 캡슐을 `model_validate`로 되돌리므로
    `CapsuleSection._tokens_match_body`가 **로드 시점에 다시** 돈다. 조립을 실 토크나이저로
    했는데 조회가 `using_counter` 밖이면 기본 카운터(글자수 근사)로 재계산되어 갈린다 —
    `cli.estimate`가 그 자리였고 `ValidationError`로 죽었다.

    **`src/tokens.py`가 이 붕괴를 이미 예고했는데 지목한 경로가 달랐다.** 그 docstring은
    `expand()`가 배선되는 날 터진다고 적었지만 실제로 터진 것은 저장소 로드다. 위험한 자리는
    확장 경로 하나가 아니라 **`using_counter` 밖의 모든 `model_validate`**이고, 이 검사는 그
    사실을 값으로 못박는다.

    본문 줄은 두 카운터가 **실제로 갈리는** 것을 고른다(`copy_name util.c:14` = 기본 4 대
    실 6). 우연히 같은 값을 내는 줄로는 결함이 있어도 이 검사가 통과한다.
    """
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    tokenizer = Tokenizer(models.WordLevel(vocab={"a": 0, "[UNK]": 1}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(model_dir / "tokenizer.json"))
    real = counter_for_source(f"file://{model_dir.as_posix()}")
    assert real is not None

    body = ["copy_name util.c:14"]
    assert sum(estimate_tokens(line) for line in body) != _under(real, body), (
        "두 카운터가 같은 값을 내면 이 검사는 결함이 있어도 통과한다"
    )

    with using_counter(real):
        section = CapsuleSection(
            kind="callees",
            tokens=sum(estimate_tokens(line) for line in body),
            items=len(body),
            body=body,
        )
        capsule = ContextCapsule(
            capsule_id="cap_0001",
            run_id="run_20260901_0001",
            target=Location(file="util.c"),
            level=CapsuleLevel.LIGHT,
            budget_tokens=4000,
            cache_key="sha256:" + "0" * 64,
            sections=[section],
        )
    store = SqliteStateStore(tmp_path / "state.db")
    try:
        store.put(capsule)
        # 밖에서 조회하면 재검증이 기본 카운터로 돌아 갈린다.
        with pytest.raises(ValidationError):
            store.query("capsule")
        # 조립과 같은 카운터 안에서는 되돌아온다.
        with using_counter(real):
            assert len(store.query("capsule")) == 1
    finally:
        store.close()


def _under(counter, lines: list[str]) -> int:
    """`counter`를 켠 상태에서 센 합. 검사의 전제(두 카운터가 갈린다)를 확인하는 데 쓴다."""
    with using_counter(counter):
        return sum(estimate_tokens(line) for line in lines)
