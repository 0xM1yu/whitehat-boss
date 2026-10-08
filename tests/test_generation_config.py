"""모델 `generation_config.json` → `RunRecord.model_sampling` 리더 (D29).

**재는 것은 "모델이 선언한 샘플링을 충실히·지어내지 않고 읽는가"다.** 서버가 그 파일을
읽어 `top_k` 등을 적용하는데 하네스는 그 값을 안 보내고 응답도 안 돌려주므로, 이 리더가
없으면 그 축이 기록 어디에도 안 남아 모델 비교가 조용히 오염된다(evaluation.md §2.3).

⏵ 2026-10-08 — 「하네스는 그 값을 안 보내고」(2026-08-29)는 `top_k`에 대해 낡았다. registry
`sampling`을 선언한 모델은 `temperature`·`top_p`·`top_k`·`min_p`·`presence_penalty`를 요청에
싣는다(`RequestSampling` -> `extra_params`, #261 / `top_k` 선언은 2026-09-26부터).
`repetition_penalty`는 여전히 안 보낸다. 응답 쪽 서술은 재확인 안 함.
"""

import json
from pathlib import Path

from src.adapters.generation_config import (
    declared_sampling_by_role,
    read_declared_sampling,
)
from src.adapters.tokenizer import tokenizer_dir_from_source
from src.config import load_config
from src.schemas.run import ModelSampling, RunRecord

_SOURCE = "hf://org/model@abc123"
ZERO_DIGEST = "sha256:" + "0" * 64


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


def test_a_model_that_declared_nothing_is_told_apart_from_one_we_could_not_read(
    tmp_path: Path,
) -> None:
    """"선언이 없다"와 "못 읽었다"가 갈려야 한다 (이슈 #99 ③).

    granite·Foundation-Sec가 샘플링을 선언하지 않는 것은 **관측된 실험 사실**이다.
    반면 HF 캐시 스냅샷을 못 찾은 것은 **우리 쪽 기록 구멍**이다. 둘이 같은 모양으로
    나오면 그 run의 샘플링 축은 사후에 검증할 수 없다 — **HF 캐시는 저장소 밖이라
    그때 그 상태였는지 나중에 알 방법이 없다.** 곧 돌 모델 비교가 정확히 이 축을
    비교하므로 지금 안 가르면 그 값은 영구히 사라진다.

    빈 `ModelSampling()`은 "파일을 읽었고 스키마에 담을 선언이 없었다"는 **관측**이고,
    `None`은 "읽지 못했다"는 **부재**다. 지어내는 값이 아니므로 CLAUDE.md §0에 맞는다.
    """
    # bos/eos만 든 모델 — 파일은 읽혔고 샘플링 선언이 0개다
    _write_generation_config(tmp_path, _SOURCE, {"bos_token_id": 1, "eos_token_id": 2})

    declared = read_declared_sampling(_SOURCE, hub_cache=tmp_path)
    unreadable = read_declared_sampling("hf://org/absent@deadbee", hub_cache=tmp_path)

    assert declared is not None, "파일을 읽었다 — 그 사실이 기록에서 사라지면 안 된다"
    assert declared.model_dump() == {
        "temperature": None,
        "top_p": None,
        "top_k": None,
        "repetition_penalty": None,
    }
    assert unreadable is None, "그 경로에 파일이 없으면 읽지 못한 쪽이다"


def test_a_file_we_cannot_decode_is_unread_not_an_exception(tmp_path: Path) -> None:
    """비-UTF-8 바이트는 `None`이다 — 예외가 run을 죽이면 안 된다 (리뷰 지적 L1).

    docstring이 "파일이 깨졌거나 매핑이 아니다 → `None`"이라 적는데
    `UnicodeDecodeError`는 `ValueError` 하위지 `OSError`도 `JSONDecodeError`도 아니라
    except를 통과했다. 그 예외는 `declared_sampling_by_role` → `factory`(run 조립 경로)까지
    전파해 **run 자체를 죽인다.**
    """
    snapshot = tokenizer_dir_from_source(_SOURCE, tmp_path)
    assert snapshot is not None
    snapshot.mkdir(parents=True, exist_ok=True)
    # 유효하지 않은 UTF-8 바이트열. 이스케이프 없이 만들어 소스가 ASCII로 남게 한다.
    (snapshot / "generation_config.json").write_bytes(
        b'{"temperature": "' + bytes([0xFF, 0xFE]) + b'"}'
    )

    assert read_declared_sampling(_SOURCE, hub_cache=tmp_path) is None


def test_a_broken_file_stays_on_the_unread_side(tmp_path: Path) -> None:
    """깨진 JSON은 `None`이다 — 빈 관측이 아니다 (리뷰 지적 M3).

    #99 ③ 이전에는 이 분기가 다른 모든 경로와 똑같이 `None`을 내서 오분류가 무해했다.
    이제는 **이 분기가 "못 읽음"과 "빈 관측"을 가르는 자리**라 하중을 새로 받는다.
    누가 여기를 `ModelSampling()`으로 바꿔도 잡는 테스트가 없었다.
    """
    snapshot = tokenizer_dir_from_source(_SOURCE, tmp_path)
    assert snapshot is not None
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "generation_config.json").write_text("{ not json", encoding="utf-8")

    assert read_declared_sampling(_SOURCE, hub_cache=tmp_path) is None


def test_a_non_mapping_file_stays_on_the_unread_side(tmp_path: Path) -> None:
    """매핑이 아닌 JSON도 `None`이다 (리뷰 지적 M3). 위와 같은 사유로 하중을 받는다."""
    _write_generation_config(tmp_path, _SOURCE, [1, 2])  # type: ignore[arg-type]

    assert read_declared_sampling(_SOURCE, hub_cache=tmp_path) is None


def _without_source(config, role: str):
    """`role`의 모델에서 `source`만 없앤 config를 돌려준다.

    **합성이어야 하는 이유가 2026-09-04에 생겼다.** 이 파일의 두 테스트는 오래
    `local_balanced`의 critic(`devstral-small-2-24b`)을 「읽을 것이 없는 역할」의
    대조군으로 썼다 — 그 모델이 `source: null`인 **유일한 실서빙 모델**이었기 때문이다.
    그날 ATOM에서 처음 띄우고 `tools/model_checksum.py`를 돌리면서 `source`가 채워졌고,
    **registry에 `source` 없는 실서빙 모델이 하나도 남지 않았다.**

    실프로필로 이 방향을 다시 재려면 누군가 `source`를 비워야 하는데 그것은 §0을
    거스른다(받아서 띄운 모델은 적는다). 그래서 대조군을 합성으로 만든다 —
    `tests/test_config.py::test_a_hybrid_profile_keeps_its_retrieval_roles`가 같은
    이유로 쓰는 방식이다. 이러면 registry 내용이 바뀌어도 이 검사가 안 흔들린다.

    ⚠ **2026-09-24 — `local_balanced`와 그 키들을 지웠다**(로스터 밖 정리). 아래 검사는
    canonical `harness_pool`로 옮겼다. 그 프로필에서 `hf://…@rev`로 **읽히는** 프롬프트 역할은
    `scout_memory`·`scout_injection`(같은 `qwen3-14b` — 2026-09-25부터 memory도)·`critic`
    (`gpt-oss-20b`)·`auditor`(`qwen3.8-27b` — 2026-09-26부터)에 `scout_boundary`(`seccoderx-8b` —
    #294로 `@rev`가 생겼다)까지 다섯 전부다(그 전 Auditor `qwen3.6-35b-a3b`도 #294로 `@rev`가
    생겼다가 같은 날 지웠다). 경로가 풀려도 스냅샷에 `generation_config.json`이 없으면
    `file_absent`로 빠진다(리졸브 실패가 아니다). 이 테스트는 그것과 무관하게 `source`를
    `None`으로 비워 no-source 경로를 본다.
    """
    key = config.profile.roles[role]
    entry = config.models[key].model_copy(update={"source": None})
    return config.model_copy(update={"models": {**config.models, key: entry}})


def test_the_role_map_keeps_a_model_that_read_but_declared_nothing(
    tmp_path: Path,
) -> None:
    """맵에서 빠지는 것은 **못 읽은 역할만**이다 (이슈 #99 ③).

    역할이 맵에 없으면 `source` 없음·리졸브 실패·파일 없음·파일 깨짐·선언 0개를
    **모두** 뜻했다. 그중 마지막만 실험 사실이고 나머지는 우리 쪽 구멍인데, 부재
    하나로 뭉개지면 "그 모델은 선언이 없다"로 읽는 것을 막을 방법이 없다.
    """
    config = _without_source(load_config("harness_pool"), "critic")
    served = config.served_models()
    scout_source = config.models[served["scout_injection"]].source
    assert scout_source is not None
    assert config.models[served["critic"]].source is None  # 대조군: 읽을 것이 없다 (합성)
    # scout_injection에만 파일을 깐다 — bos만 들어 샘플링 선언이 0개다
    _write_generation_config(tmp_path, scout_source, {"bos_token_id": 1})

    by_role = declared_sampling_by_role(config, hub_cache=tmp_path)

    assert "scout_injection" in by_role, "읽었으나 선언이 0개인 역할은 맵에 남아야 한다"
    assert by_role["scout_injection"].top_k is None
    assert "critic" not in by_role, "source가 없어 읽지 못한 역할만 빠진다"
    assert "auditor" not in by_role, "파일을 안 깐 역할도 못 읽은 쪽이다"


def test_a_file_with_no_sampling_keys_is_an_empty_observation_not_none(
    tmp_path: Path,
) -> None:
    """bos/eos만 든 모델(Foundation-Sec·granite)은 **빈 관측**이지 부재가 아니다 (#99 ③).

    **이 테스트는 계약이 뒤집혀 이름과 단언이 함께 바뀌었다.** 예전에는 `None`이었고
    그 `None`이 맵에서 역할을 빼는 근거였는데, 바로 그것이 #99 ③이 지목한 결함이다 —
    "모델이 선언하지 않았다"(실험 사실)와 "우리가 HF 캐시를 못 찾았다"(기록 구멍)가
    같은 모양이 된다. **HF 캐시는 저장소 밖이라 사후에 어느 쪽이었는지 알 수 없다.**

    서버가 vLLM 기본값을 쓴다는 사실은 여전히 지어내지 않는다 — 모든 필드가 `None`이고
    그 `None`은 `ModelSampling` docstring대로 "이 키를 선언하지 않았다"는 뜻이다.
    """
    _write_generation_config(
        tmp_path, _SOURCE, {"bos_token_id": 1, "eos_token_id": 2, "pad_token_id": 0}
    )

    sampling = read_declared_sampling(_SOURCE, hub_cache=tmp_path)

    assert sampling is not None, "파일을 읽었다는 사실이 기록에서 사라지면 안 된다"
    assert sampling.temperature is None
    assert sampling.top_k is None


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


def test_a_file_whose_keys_are_all_outside_the_schema_is_still_a_read(
    tmp_path: Path,
) -> None:
    """살아남는 키가 없어도 **읽은 것은 읽은 것**이다 (#99 ③).

    **계약이 뒤집혔다.** 예전 근거는 "빈 객체는 '선언이 있었다'고 말하는데 담긴 값이
    없어 미선언과 뜻이 겹친다"였다. 그 걱정은 지금 반대쪽에서 더 크다 — `None`으로
    돌리면 **읽지 못한 것과 겹치고**, 그쪽이 사후 복원이 불가능한 손실이다.

    빈 `ModelSampling()`이 말하는 것은 "선언이 있었다"가 아니라
    **"파일을 읽었고 스키마에 담을 것이 없었다"**이다 — 함수 docstring이 두 뜻을
    명시적으로 가른다. 이 경로는 `top_k`가 `0`·`-1`을 정상 선언으로 받게 넓힌 뒤로는
    사실상 병리적 입력에서만 온다.
    """
    _write_generation_config(tmp_path, _SOURCE, {"top_p": 5.0, "temperature": -1.0})

    sampling = read_declared_sampling(_SOURCE, hub_cache=tmp_path)

    assert sampling is not None, "읽은 사실은 남는다"
    assert sampling.top_p is None, "스키마 밖 값을 지어내 담지 않는다"
    assert sampling.temperature is None


def test_by_role_covers_served_prompted_roles_and_skips_the_rest(tmp_path: Path) -> None:
    """맵은 프롬프트를 쓰는 실서빙 역할만, **읽을 수 있었던 것만** 담는다 (D29 · #99 ③).

    `harness_pool`에서 `@rev`로 읽히는 프롬프트 역할은 scout_memory·scout_injection(같은 키
    `qwen3-14b`)·critic·auditor(2026-09-26부터 `qwen3.8-27b`)이고, auditor는
    `_without_source`가 합성으로 비운다. 두 모델에만 파일을
    깔면 그 모델을 쓰는 세 역할만 맵에 들어오고 나머지는 빠진다 — "이 역할은 선언 샘플링을 못
    읽었다"가 부재로 드러난다. `seccoderx-8b`의 scout_boundary가 그 부재다 — #294로 `@rev`가
    생겨 경로는 풀리지만 이 테스트가 그 스냅샷에 파일을 안 깔아 `file_absent`로 빠진다.
    (2026-09-24 전에는 `local_balanced`의 scout·auditor가 읽히는 쪽, critic이 대조군이었다.
    2026-09-25 scout_memory가 `qwen3-14b`로 옮겨 셋이 됐다.)

    **auditor를 합성으로 비우는 이유는 `_without_source`의 docstring에 있다** — 2026-09-04에
    registry에서 `source` 없는 실서빙 모델이 사라졌다.
    """
    config = _without_source(load_config("harness_pool"), "auditor")
    served = config.served_models()
    scout_source = config.models[served["scout_injection"]].source
    critic_source = config.models[served["critic"]].source
    assert scout_source and critic_source
    assert config.models[served["auditor"]].source is None  # 전제 (합성)

    _write_generation_config(tmp_path, scout_source, {"top_k": 20, "temperature": 0.6})
    _write_generation_config(tmp_path, critic_source, {"top_k": 20, "repetition_penalty": 1.05})

    by_role = declared_sampling_by_role(config, hub_cache=tmp_path)

    assert set(by_role) == {"scout_memory", "scout_injection", "critic"}
    assert by_role["scout_injection"].top_k == 20
    assert by_role["scout_memory"] == by_role["scout_injection"]  # 같은 키 — 같은 선언
    assert "scout_boundary" not in by_role  # seccoderx-8b: 파일 미설치 → file_absent
    assert by_role["critic"].repetition_penalty == 1.05
    assert "auditor" not in by_role  # source 없음
    assert "embedding" not in by_role  # PROMPTED_ROLES 아님


def test_by_role_is_empty_not_none_when_nothing_resolves(tmp_path: Path) -> None:
    """읽을 게 없어도 **빈 dict**를 준다 — `RunRecord.model_sampling`의 `None`은
    옛 manifest 전용이고, 새 run은 비어도 dict를 남긴다(그 구분이 필드 주석의 근거다)."""
    config = load_config("harness_pool")  # 파일을 하나도 안 깐다

    assert declared_sampling_by_role(config, hub_cache=tmp_path) == {}


def test_the_read_but_empty_role_survives_the_manifest_round_trip(tmp_path: Path) -> None:
    """가른 구별이 **저장 경계를 넘어서도 남는가** (이슈 #99 ③, PR #107 리뷰).

    이 PR이 만든 구별은 `run_manifest.json`에 실려야 값이 있다. 직렬화에서 빈
    `ModelSampling()`과 부재가 같은 모양(`null`·`{}`)으로 뭉개지면, 읽는 쪽은 다시
    "선언이 없다"와 "못 읽었다"를 못 가른다 — 리더에서 가른 것이 저장에서 도로 합쳐진다.

    실제로는 두 축이 서로 다른 자리에 실린다: 읽은 역할은 **키가 있고 값이 전부 `null`인
    객체**로, 못 읽은 역할은 **키 자체가 없음**으로 남는다. 그 둘이 재검증을 통과해도
    구별된 채 돌아오는 것을 여기서 못 박는다.
    """
    config = load_config("harness_pool")
    served = config.served_models()
    scout_source = config.models[served["scout_injection"]].source
    assert scout_source is not None
    # scout_injection만 bos/eos로 깐다 — 읽혔고 선언이 0개인 역할이다. auditor는 안 깐다.
    _write_generation_config(tmp_path, scout_source, {"bos_token_id": 1, "eos_token_id": 2})

    by_role = declared_sampling_by_role(config, hub_cache=tmp_path)
    record = RunRecord(
        run_id="run_20260901_0001",
        condition="B3prime@pipeline",
        repo_commit="0" * 40,
        repo_dirty=False,
        source_hash=ZERO_DIGEST,
        model_checksums=config.model_checksums(),
        prompt_versions=config.prompt_versions(),
        tool_images={"semgrep": ZERO_DIGEST},
        tool_runner=config.profile.adapters.tool_runner,
        device="atom",
        budget=config.budget,
        thresholds=config.thresholds.flatten(),
        seed=config.seed,
        sampling=config.sampling,
        model_sampling=by_role,
        kb_snapshot="kb-2026-08-30",
        started_at="2026-09-01T09:00:00Z",  # type: ignore[arg-type]
    )

    written = json.loads(record.model_dump_json())
    assert written["model_sampling"]["scout_injection"] == {
        "temperature": None,
        "top_p": None,
        "top_k": None,
        "repetition_penalty": None,
    }, "읽었으나 선언이 0개인 역할은 키가 남아야 한다"
    assert "auditor" not in written["model_sampling"], "못 읽은 역할은 키가 없다"

    reloaded = RunRecord.model_validate_json(record.model_dump_json())
    assert reloaded.model_sampling is not None
    assert reloaded.model_sampling["scout_injection"] == ModelSampling()
    assert "auditor" not in reloaded.model_sampling
