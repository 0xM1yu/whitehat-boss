"""모델 `generation_config.json` → `RunRecord.model_sampling` 리더 (D29).

**재는 것은 "모델이 선언한 샘플링을 충실히·지어내지 않고 읽는가"다.** 서버가 그 파일을
읽어 `top_k` 등을 적용하는데 하네스는 그 값을 안 보내고 응답도 안 돌려주므로, 이 리더가
없으면 그 축이 기록 어디에도 안 남아 모델 비교가 조용히 오염된다(evaluation.md §2.3).
"""

import json
from pathlib import Path

from src.adapters.generation_config import (
    declared_sampling_by_role,
    read_declared_sampling,
)
from src.adapters.tokenizer import tokenizer_dir_from_source
from src.config import load_config

_SOURCE = "hf://org/model@abc123"


def _write_generation_config(hub: Path, source: str, payload: dict) -> None:
    """`source`가 가리키는 HF 캐시 스냅샷에 `generation_config.json`을 쓴다."""
    snapshot = tokenizer_dir_from_source(source, hub)
    assert snapshot is not None
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "generation_config.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_reads_the_declared_sampling_from_the_model_file(tmp_path: Path) -> None:
    """서버가 읽는 그 파일을 하네스도 읽어 선언값을 그대로 담는다."""
    _write_generation_config(
        tmp_path,
        _SOURCE,
        {"temperature": 0.7, "top_k": 20, "top_p": 0.8, "repetition_penalty": 1.05},
    )

    sampling = read_declared_sampling(_SOURCE, hub_cache=tmp_path)

    assert sampling is not None
    assert sampling.temperature == 0.7
    assert sampling.top_k == 20
    assert sampling.top_p == 0.8
    assert sampling.repetition_penalty == 1.05


def test_a_key_the_model_did_not_declare_stays_none_not_zero(tmp_path: Path) -> None:
    """선언하지 않은 키는 `None`이다 — 0으로 채우면 "서버가 vLLM 기본값을 쓴다"가
    "top_k=0으로 돌았다"라는 거짓이 된다. Qwen3-8B가 `top_k`만 든 모양이 이것이다."""
    _write_generation_config(tmp_path, _SOURCE, {"top_k": 20})

    sampling = read_declared_sampling(_SOURCE, hub_cache=tmp_path)

    assert sampling is not None
    assert sampling.top_k == 20
    assert sampling.temperature is None
    assert sampling.top_p is None
    assert sampling.repetition_penalty is None


def test_a_file_with_no_sampling_keys_is_none(tmp_path: Path) -> None:
    """bos/eos만 든 모델(Foundation-Sec·granite)은 선언이 없다 → `None`으로 빠진다.

    서버가 vLLM 기본값을 쓰는데 그 기본값은 이 파일로 알 수 없으므로 지어내지 않는다.
    이 `None`이 맵에서 그 역할을 빼는 근거다.
    """
    _write_generation_config(
        tmp_path, _SOURCE, {"bos_token_id": 1, "eos_token_id": 2, "pad_token_id": 0}
    )

    assert read_declared_sampling(_SOURCE, hub_cache=tmp_path) is None


def test_missing_file_is_none(tmp_path: Path) -> None:
    """스냅샷은 있는데 `generation_config.json`이 없으면 `None`."""
    snapshot = tokenizer_dir_from_source(_SOURCE, tmp_path)
    assert snapshot is not None
    snapshot.mkdir(parents=True)  # 파일은 안 쓴다

    assert read_declared_sampling(_SOURCE, hub_cache=tmp_path) is None


def test_unresolvable_sources_are_none(tmp_path: Path) -> None:
    """읽을 대상을 특정하지 못하면 `None` — 지어내지 않는다."""
    assert read_declared_sampling(None, hub_cache=tmp_path) is None
    assert read_declared_sampling("", hub_cache=tmp_path) is None
    assert read_declared_sampling("sha256:deadbeef", hub_cache=tmp_path) is None
    # 리비전 없는 hf://는 어느 스냅샷인지 특정 못 한다(tokenizer 리졸버와 같은 판정).
    assert read_declared_sampling("hf://org/model", hub_cache=tmp_path) is None


def test_top_k_zero_is_a_real_declaration_not_a_schema_violation(tmp_path: Path) -> None:
    """`top_k: 0`은 스키마 밖 값이 아니라 **"비활성"이라는 정상 선언**이다.

    HF는 `0`, vLLM은 `-1`을 그 뜻으로 쓴다. `ModelSampling`이 `ge=1`이던 동안에는 그 선언이
    검증에서 튕겨 **그 모델의 기록이 통째로 사라졌다** — `temperature 0.7`까지 함께 버려져
    "선언 없음"으로 남았고, 그것은 D29가 막으려던 오독을 반대 방향에서 재생산한다.
    `Sampling`(우리가 **보내는** 값)이라면 `ge=1`이 맞지만 이쪽은 **받아 적는** 자리다.
    """
    _write_generation_config(tmp_path, _SOURCE, {"top_k": 0, "temperature": 0.7})

    sampling = read_declared_sampling(_SOURCE, hub_cache=tmp_path)
    assert sampling is not None
    assert sampling.top_k == 0
    assert sampling.temperature == 0.7

    _write_generation_config(tmp_path, _SOURCE, {"top_k": -1})
    vllm_style = read_declared_sampling(_SOURCE, hub_cache=tmp_path)
    assert vllm_style is not None
    assert vllm_style.top_k == -1


def test_a_value_outside_the_schema_drops_only_that_key(tmp_path: Path) -> None:
    """진짜 스키마 밖 값(`top_p: 5.0` — 확률이 1을 넘는다)은 **그 키만** 버린다.

    전체를 버리면 그 하나가 같은 파일의 `temperature 0.7`까지 데려가고, 역할이 맵에서
    빠져 **"두 개를 선언한 모델"이 "선언 없음"과 구별되지 않는다** — D29가 막으려던 그
    자리로 되돌아간다. 지어내 고치지 않는 것과 이웃을 데려가는 것은 다른 문제다.
    """
    _write_generation_config(tmp_path, _SOURCE, {"top_p": 5.0, "temperature": 0.7})

    sampling = read_declared_sampling(_SOURCE, hub_cache=tmp_path)

    assert sampling is not None
    assert sampling.temperature == 0.7
    assert sampling.top_p is None


def test_a_file_whose_keys_are_all_outside_the_schema_is_none(tmp_path: Path) -> None:
    """살아남는 키가 하나도 없으면 `None`이다 — 빈 `ModelSampling`을 남기지 않는다.

    빈 객체는 "선언이 있었다"고 말하는데 실제로 담긴 값이 없어서, 미선언과 뜻이 겹친다.
    """
    _write_generation_config(tmp_path, _SOURCE, {"top_p": 5.0, "temperature": -1.0})

    assert read_declared_sampling(_SOURCE, hub_cache=tmp_path) is None


def test_by_role_covers_served_prompted_roles_and_skips_the_rest(tmp_path: Path) -> None:
    """맵은 프롬프트를 쓰는 실서빙 역할만, 선언이 있는 것만 담는다 (D29).

    `local_balanced`는 scout·auditor가 `hf://` source를, critic(`devstral`)이 `source`
    없음이다. scout·auditor에만 파일을 깔면 맵에 둘만 들어오고 critic은 빠진다 —
    "이 역할은 선언 샘플링을 못 읽었다"가 부재로 드러난다.
    """
    config = load_config("local_balanced")
    served = config.served_models()
    scout_source = config.models[served["scout"]].source
    auditor_source = config.models[served["auditor"]].source
    assert scout_source and auditor_source
    assert config.models[served["critic"]].source is None  # 전제

    _write_generation_config(tmp_path, scout_source, {"top_k": 20, "temperature": 0.6})
    _write_generation_config(tmp_path, auditor_source, {"top_k": 20, "repetition_penalty": 1.05})

    by_role = declared_sampling_by_role(config, hub_cache=tmp_path)

    assert set(by_role) == {"scout", "auditor"}
    assert by_role["scout"].top_k == 20
    assert by_role["auditor"].repetition_penalty == 1.05
    assert "critic" not in by_role  # source 없음
    assert "embedding" not in by_role  # PROMPTED_ROLES 아님


def test_by_role_is_empty_not_none_when_nothing_resolves(tmp_path: Path) -> None:
    """읽을 게 없어도 **빈 dict**를 준다 — `RunRecord.model_sampling`의 `None`은
    옛 manifest 전용이고, 새 run은 비어도 dict를 남긴다(그 구분이 필드 주석의 근거다)."""
    config = load_config("local_balanced")  # 파일을 하나도 안 깐다

    assert declared_sampling_by_role(config, hub_cache=tmp_path) == {}
