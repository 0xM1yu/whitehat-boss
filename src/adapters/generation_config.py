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
    """모델의 `generation_config.json`이 선언한 샘플링. **읽지 못했으면** `None`.

    **`None`과 빈 `ModelSampling()`은 서로 다른 사실이다 (#99 ③).**

    - `None` = **읽지 못했다.** 우리 쪽 기록 구멍이다:
      - `source`가 비어 있다
      - `source`를 스냅샷 경로로 **풀지 못했다** — `hf://`·`file://`가 아닌 스킴(예 `sha256:`)
        이거나 `@rev`가 없다. 이 리졸버는 **존재 확인을 하지 않는다**
        (`tokenizer.tokenizer_dir_from_source` docstring)
      - 그 경로에 `generation_config.json`이 없다 — **HF 캐시가 안 받아졌을 때가 여기다**
      - 파일이 깨졌거나 매핑이 아니다
    - 빈 `ModelSampling()` = **읽었고 담을 선언이 없었다.** 이것은 **관측**이다:
      - 샘플링 키를 하나도 선언하지 않았다(bos/eos만 있는 모델 — granite·Foundation-Sec)
      - 선언한 키가 **전부** 스키마 밖이었다

    ⚠ **그 두 경우는 서로 구별되지 않는다.** `{"bos_token_id": 1}`(모델이 선언 안 함)과
    `{"top_p": 5.0}`(모델이 선언했는데 **우리 스키마가 거부**)이 같은 빈 객체가 되고,
    매니페스트에서도 글자 그대로 같다. 뒤집기 전 테스트가 든 걱정 — *"빈 객체는 '선언이
    있었다'고 말하는데 담긴 값이 없어 미선언과 뜻이 겹친다"* — 가 **여기서 반쪽 살아 있다.**

    그럼에도 빈 객체를 택한 이유는 **비대칭이다.** `None`으로 되돌리면 그 둘이
    "HF 캐시를 못 찾았다"와 겹치는데, 캐시는 저장소 밖이라 **사후 복원이 불가능하다.**
    반면 "우리가 거부했다"는 `config/models.yaml`의 모델을 다시 받으면 확인된다.
    **복원 가능한 겹침을 남기고 불가능한 겹침을 없앤 것**이지, 겹침이 사라진 것이 아니다.
    (셋을 가르려면 세 번째 표시가 필요하고 그것은 별건이다 — 2026-09-01 리뷰 M1.)

    **왜 갈라야 하나.** 둘이 같은 모양이면 "그 모델은 선언이 없다"(실험 사실)와
    "우리가 캐시를 못 찾았다"(우리 구멍)가 구별되지 않는데, **HF 캐시는 저장소 밖이라
    그때 그 상태였는지 나중에 확인할 방법이 없다** — 그 run의 샘플링 축이 영구히
    검증 불가가 된다. 곧 도는 모델 비교가 정확히 이 축을 비교한다.

    빈 값은 지어낸 값이 아니다. 모든 필드가 `None`이고, `ModelSampling`의 `None`은
    이미 "이 키를 선언하지 않았다"는 뜻이다(그 클래스 docstring) — CLAUDE.md §0의
    "안 잰 값은 사유와 함께 둔다"와 같은 관용구다.

    **스키마 밖 키는 그 키만 버린다.** 전체를 버리면 `top_p: 5.0` 하나가 같은 파일의
    `temperature: 0.7`까지 데려가고, 그 역할이 **"두 개를 선언한 모델"이 "선언 없음"과
    구별되지 않는** 자리로 돌아간다. 필드가 전부 optional이고 교차 검증기가 없어서
    낱개 검증이 성립한다.
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
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        # `UnicodeDecodeError`는 `ValueError` 하위지 나머지 둘의 하위가 아니라 예전에는
        # 이 절을 통과했다 — 비-UTF-8 바이트가 든 파일에서 예외가 run 조립 경로까지
        # 전파해 run 자체를 죽였다. docstring이 "파일이 깨졌거나" 라 적은 자리다.
        return None
    if not isinstance(data, dict):
        return None
    # 여기부터는 **파일을 읽었다**가 확정이다. 아래 두 `return`이 `None`이 아니라
    # 빈 `ModelSampling()`인 것이 #99 ③의 요점이다 — 읽은 사실을 잃지 않는다.
    picked = {key: data[key] for key in _SAMPLING_KEYS if data.get(key) is not None}
    if not picked:
        return ModelSampling()
    kept: dict[str, object] = {}
    for key, value in picked.items():
        try:
            ModelSampling.model_validate({key: value})
        except ValidationError:
            # 이 키만 버린다. 지어내 고치지 않되 **이웃까지 데려가지도 않는다.**
            continue
        kept[key] = value
    if not kept:
        return ModelSampling()
    return ModelSampling.model_validate(kept)


def declared_sampling_by_role(
    config: HarnessConfig, hub_cache: Path | None = None
) -> dict[str, ModelSampling]:
    """`RunRecord.model_sampling`에 넣을 역할 → 선언 샘플링 맵.

    **프롬프트를 쓰는 실서빙 역할만** 담는다 — `PROMPTED_ROLES`로 좁힌다. embedding·reranker는
    생성 모델이 아니라 샘플링과 무관해서다. **이 좁힘은 한때 중복 방어였고, 지금은
    아니다.** `served_models()`가 이슈 #108 (b) 이후 `retriever: fake`인 프로필에서 그
    둘을 이미 빼므로 오래 그 둘을 주는 프로필이 **하나도 없었는데**,
    `local_balanced_hybrid`가 embedding·reranker를 실서빙하면서 그것이 깨졌다
    (2026-09-04 확인). "붙는 날 다시 필요해진다"고 적어 두었던
    그날이 왔다 — 이제 `PROMPTED_ROLES`가 **실제로 거르는** 유일한 방어다. 지우지 마라.

    **맵에서 빠지는 것은 "읽지 못한" 역할뿐이다 (#99 ③).** Fake 역할, `source` 없는 모델,
    스냅샷·파일을 못 찾은 모델이 그것이다. **읽었으나 선언이 0개인 모델은 빈
    `ModelSampling()`으로 맵에 남는다** — granite·Foundation-Sec가 그 모양이고, 그것은
    우리 구멍이 아니라 **관측된 실험 사실**이라 기록에서 사라지면 안 된다.

    ⏵ 2026-10-08 — 「"읽지 못한" 역할뿐」(2026-09-01)은 정확하지 않다. 빠지는 길은 둘이다 ->
    (a) 읽기 시도 없음: `PROMPTED_ROLES` 밖·`served.get(role)`이 `None`(Fake 포함) — 아래 루프가
    `read_declared_sampling`을 안 부르고 `continue` (b) 읽었으나 `None`: `source` 없음·경로
    리졸브 실패·파일 없음·파일 깨짐. Fake는 (b)가 아니라 (a)다
    (`dev_fake`의 `served_models()`가 `{}`).

    그래서 **부재의 뜻이 하나로 좁혀졌다**: "이 역할의 선언 샘플링을 읽지 못했다."
    HF 캐시는 저장소 밖이라 사후 확인이 불가능하므로, 이 구분이 없으면 그 run의 샘플링
    축을 나중에 검증할 수 없다.

    **새 run은 이 함수로 항상 dict를 남긴다(비어도).** `RunRecord.model_sampling`의
    `None`은 이 필드 이전에 쓰인 옛 manifest에서만 나온다.
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
