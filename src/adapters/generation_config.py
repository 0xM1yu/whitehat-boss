"""모델이 `generation_config.json`에 선언한 샘플링을 읽는다 (#99).

vLLM은 기동 시 모델의 `generation_config.json`을 읽어 `top_k`·`repetition_penalty` 같은
값을 기본 샘플링에 적용한다(기동 로그: "Default vLLM sampling parameters have been
overridden by the model's generation_config.json"). 하네스는 요청에 `temperature`·`top_p`만
싣고 응답도 실효 샘플링을 안 돌려주므로, 그 값들은 **어디에도 안 남는다.** 그런데 값이
모델마다 다르므로(Qwen3-8B `top_k 20` 대 Foundation-Sec·granite 미선언) 모델 비교에서
샘플링 차이가 모델 차이로 섞인다(evaluation.md §2.3).

하네스는 vLLM을 직접 띄우지 않아 그 기동 로그를 보지 못하고, vLLM에도 기본 샘플링을 주는
API가 없다. 대신 **서버가 읽는 그 파일을 하네스도 읽는다** — 토크나이저가 이미 같은 HF 캐시
스냅샷에서 모델 파일을 읽으므로(`tokenizer.tokenizer_dir_from_source`) 그 리졸버를 재사용한다.

**한계**: 이것은 모델이 **선언한** 값이다. 서버가 기본 모드로 돌면 곧 실효값이지만, 누가
`--generation-config vllm`으로 띄우면 모델 파일값이 적용되지 않는다 — 그 서빙 플래그는
하네스가 볼 수 없다. 지금 하네스가 할 수 있는 것은 모델 선언값을 충실히 기록하는 것까지다.
"""

import json
from pathlib import Path

from pydantic import ValidationError

from src.adapters.tokenizer import tokenizer_dir_from_source
from src.config import PROMPTED_ROLES, HarnessConfig
from src.schemas.run import ModelSampling

# `generation_config.json`에서 담는 샘플링 키. 그 파일은 bos/eos_token_id 등 샘플링과
# 무관한 키도 많으므로 아는 것만 골라 담는다(`ModelSampling`은 extra를 금지한다).
_SAMPLING_KEYS = ("temperature", "top_p", "top_k", "repetition_penalty")


def read_declared_sampling(
    source: str | None, hub_cache: Path | None = None
) -> ModelSampling | None:
    """모델의 `generation_config.json`이 선언한 샘플링. 읽을 수 없거나 선언이 없으면 `None`.

    `None`을 돌려주는 경우 — 지어내지 않기 위해 전부 `None`이다:
    - `source`가 없다(빈 값·`sha256:` 등 다른 스킴·리비전 없는 `hf://`)
    - 스냅샷 디렉터리에 `generation_config.json`이 없다
    - 파일이 깨졌거나 매핑이 아니다
    - 파일은 있으나 샘플링 키를 **하나도** 선언하지 않았다(bos/eos만 있는 모델 — 서버가
      vLLM 기본값을 쓴다는 뜻이고, 그 기본값은 여기서 알 수 없으므로 기록하지 않는다)
    - 선언한 키가 **전부** 스키마 밖이다

    **스키마 밖 키는 그 키만 버린다.** 전체를 버리면 `top_p: 5.0` 하나가 같은 파일의
    `temperature: 0.7`까지 데려가고, 그 역할이 맵에서 빠져 **"두 개를 선언한 모델"이
    "선언 없음"과 구별되지 않는다** — #99가 막으려던 그 자리로 되돌아간다. 필드가 전부
    optional이고 교차 검증기가 없어서 낱개 검증이 성립한다.
    """
    if not source:
        return None
    snapshot = tokenizer_dir_from_source(source, hub_cache)
    if snapshot is None:
        return None
    path = snapshot / "generation_config.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    picked = {key: data[key] for key in _SAMPLING_KEYS if data.get(key) is not None}
    if not picked:
        return None
    kept: dict[str, object] = {}
    for key, value in picked.items():
        try:
            ModelSampling.model_validate({key: value})
        except ValidationError:
            # 이 키만 버린다. 지어내 고치지 않되 **이웃까지 데려가지도 않는다.**
            continue
        kept[key] = value
    if not kept:
        return None
    return ModelSampling.model_validate(kept)


def declared_sampling_by_role(
    config: HarnessConfig, hub_cache: Path | None = None
) -> dict[str, ModelSampling]:
    """`RunRecord.model_sampling`에 넣을 역할 → 선언 샘플링 맵.

    **프롬프트를 쓰는 실서빙 역할만** 담는다. `served_models()`는 embedding·reranker도
    주지만 그것들은 생성 모델이 아니라 샘플링과 무관하다 — `PROMPTED_ROLES`로 좁힌다.
    Fake 역할, `source` 없는 모델, 선언이 없는 모델은 빠진다(그 부재 자체가 정보다).

    **새 run은 이 함수로 항상 dict를 남긴다(비어도).** `RunRecord.model_sampling`의
    `None`은 이 필드 이전에 쓰인 옛 manifest에서만 나온다.

    **한계 — 부재의 뜻이 하나가 아니다 (#99 ③).** 여기서 빠진 역할은 `source` 없음 ·
    스냅샷 리졸브 실패 · 파일 없음 · 파일 깨짐 · 선언 키 0개 · 검증 실패를 **모두** 뜻한다.
    앞 넷은 **우리 쪽 기록 구멍**이고 다섯째만 **실험 사실**인데 그 둘이 안 갈린다. run
    수준에서는 `None`(옛 manifest)과 `{}`(읽었으나 없음)가 갈리지만 **역할 수준에서는 안
    갈린다** — 그래서 부재를 "그 모델은 선언이 없다"로 읽으면 안 된다.
    """
    served = config.served_models()
    out: dict[str, ModelSampling] = {}
    for role in PROMPTED_ROLES:
        key = served.get(role)
        if key is None:
            continue
        sampling = read_declared_sampling(config.models[key].source, hub_cache)
        if sampling is not None:
            out[role] = sampling
    return out
