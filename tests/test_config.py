"""config 로더 계약 테스트.

engineering-guide.md §8.5(임계값·예산·모델은 config에서)와 §8.7(비결정적 로직에는
테스트를 남긴다)이 근거다.

**구체 수치는 단언하지 않는다.** `scout_threshold`가 0.5인지, 예산이 얼마인지는 이
프로젝트가 실측으로 정할 값이다(open-questions D2·D3). 테스트가 상수로 박으면 값을
조정할 때마다 테스트가 깨지고, 결국 값이 아니라 테스트를 맞추게 된다.
여기서 고정하는 것은 **참조 정합성과 불변식**뿐이다.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src import schemas
from src.adapters.factory import _model_client
from src.bench import ROLE_SPEC
from src.config import (
    CONFIG_DIR,
    PROMPTED_ROLES,
    REQUIRED_ROLES,
    RETRIEVAL_ROLES,
    Benchmark,
    ConfigError,
    HarnessConfig,
    ModelEntry,
    ProfileEntry,
    load_config,
)
from src.orchestrator.prompt_loader import load_prompt
from src.schemas import CapsuleLevel, RunRecord
from src.schemas.common import PLACEHOLDER_DIGEST
from src.schemas.enums import Role
from tests.conftest import ALL_PROFILES

ZERO_DIGEST = PLACEHOLDER_DIGEST


# --- 참조 정합성 ------------------------------------------------------------


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_every_profile_loads(profile: str) -> None:
    """모든 프로필이 실제로 해석된다. 깨진 참조는 기동 시점에 잡혀야 한다."""
    config = load_config(profile)
    assert config.profile_name == profile
    for role in REQUIRED_ROLES:
        assert isinstance(config.model_for(role), ModelEntry)


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_run_record_fields_are_derivable(profile: str) -> None:
    """RunRecord의 재현성 필수 항목이 config만으로 채워진다.

    engineering-guide.md §5의 항목이 하나라도 빠지면 그 run은 비교표에서 버려진다
    (evaluation.md §6). 실행해 보고 아는 것이 최악이므로 여기서 고정한다.
    """
    config = load_config(profile)
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
        # 비교의 기준축이다 — evaluation.md §2.3 규칙 2가 "조건 간 차이는 프로필과 단계
        # 활성화만"으로 정의하는데 그 절반이 기록에 없었다 (이슈 #76).
        profile=config.profile_name,
        device="atom",
        budget=config.budget,
        thresholds=config.thresholds.flatten(),
        seed=config.seed,
        sampling=config.sampling,
        kb_snapshot="kb-2026-08-30",
        started_at="2026-09-01T09:00:00Z",  # type: ignore[arg-type]
    )
    assert record.profile == profile
    # checksums는 전 역할을 담는다 — 임베딩 모델이 바뀌면 검색 결과가 바뀌므로
    # 재현성 기록에서 뺄 수 없다.
    assert set(record.model_checksums) == set(config.profile.roles)
    # prompt_versions는 프롬프트가 실제로 있는 역할만 담는다. 없는 프롬프트의 "버전"은
    # 거짓 정보이고, ADR 0004가 Role 확장을 기각한 이유가 그것이다.
    assert set(record.prompt_versions) == set(PROMPTED_ROLES) & set(config.profile.roles)
    for absent in ("embedding", "reranker", "judge"):
        assert absent not in record.prompt_versions
    assert record.thresholds  # 평탄화 결과가 비면 임계값 대조가 불가능하다


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_served_models_skips_roles_this_profile_never_wires(profile: str) -> None:
    """판정 대상에는 **이 배선에서 실제로 뜨는 역할만** 담긴다 (이슈 #108, 선택지 b).

    `served_models()`의 docstring이 스스로 그 계약을 선언한다 — *"저쪽은 기록이라 빠짐이
    없어야 하고, 이쪽은 판정 대상이라 **해시할 대상이 실재하는 역할만** 담아야 한다"*.
    그런데 구현이 `is_fake` 하나로만 걸러 `profile.adapters`를 안 봤다.

    embedding·reranker 클라이언트는 hybrid 빌더 안에서만 만들어지므로
    (`factory.py`의 두 지점이 전부 `adapters.retriever == "hybrid"` 게이트 안이다),
    `retriever: fake`인 프로필에서는 **한 번도 뜨지 않는다.** 그런데 `b1_single`·
    `local_balanced`가 그 두 역할에 실모델을 선언해서 — 그 선언 자체는 의도다,
    hybrid로 올리는 날 쓸 모델을 적어 둔 것이다(`profiles.yaml`의 `local_balanced`
    retriever 주석) — 안 뜨는 모델의 미실측 checksum이 "비교표에서 제외한다" 판정에
    들어왔다.

    **기록은 그대로 둔다.** `model_checksums()`는 계속 전 역할을 담는다 — 프로필이
    선언한 사실이고 빠짐이 없어야 한다. 갈리는 것은 **판정 대상**뿐이다.
    """
    config = load_config(profile)
    served = config.served_models()

    if config.profile.adapters.retriever != "hybrid":
        for role in RETRIEVAL_ROLES:
            assert role not in served, f"{profile}: {role}은 이 배선에서 뜨지 않는다"
    # 뜨는 역할은 **남아야 한다.** 위 단언만으로는 빈 dict를 돌려주는 구현도 통과한다.
    for role, key in config.profile.roles.items():
        if config.models[key].is_fake:
            continue
        if config.profile.adapters.retriever != "hybrid" and role in RETRIEVAL_ROLES:
            continue
        assert role in served, f"{profile}: {role}은 이 배선에서 뜨는데 빠졌다"
    # 기록 쪽은 영향받지 않는다 — 선언한 역할이 전부 남는다.
    assert set(config.model_checksums()) == set(config.profile.roles)


def test_a_hybrid_profile_keeps_its_retrieval_roles() -> None:
    """hybrid면 검색 역할을 **센다.**

    게이트를 통째로 지운 구현(`retriever`를 안 보고 항상 `RETRIEVAL_ROLES`를 빼는 것)이
    스위트를 통과하는 것을 막는다.

    **이 docstring이 오래 "이 방향을 실프로필로는 못 잰다"고 적어 왔고, 그것은
    2026-09-04에 거짓이 됐다** — `local_balanced_hybrid`가 hybrid이면서 embedding·
    reranker를 실서빙한다(아래 `test_a_real_profile_pairs_a_hybrid_retriever_with_
    served_retrieval_models`). 그때까지 hybrid인 두 프로필은 그 역할이 `fake`라
    `is_fake` 축과 `retriever` 축이 같은 답을 냈다.

    **그래도 이 테스트는 남는다.** 실프로필 쪽은 `profiles.yaml`의 내용에 기대므로,
    그 파일이 다시 바뀌면 함께 사라진다. 합성 프로필은 그 내용과 무관하게 `b1_single`
    하나만 있으면 서므로 게이트 자체를 지키는 그물은 이쪽이다.
    """
    base = load_config("b1_single")
    profile = base.profile.model_copy(
        update={"adapters": base.profile.adapters.model_copy(update={"retriever": "hybrid"})}
    )
    served = base.model_copy(update={"profile": profile}).served_models()

    assert set(RETRIEVAL_ROLES) <= set(served), (
        "hybrid 배선에서는 embedding·reranker가 실제로 뜬다 — 판정 대상에 있어야 한다"
    )


# `local_balanced`와 같은 roles·budget에 `retriever: hybrid`만 다른 프로필. 아래 두
# 테스트가 이 이름을 공유한다.
HYBRID_SERVING_PROFILE = "local_balanced_hybrid"


def test_a_real_profile_pairs_a_hybrid_retriever_with_served_retrieval_models() -> None:
    """검색 역할을 **실제로 띄우는** 프로필이 저장소에 실재한다 (build-plan §8 row 2).

    바로 위 테스트가 합성 프로필로 막고 있던 자리다. 그 docstring이 "이 방향을
    실프로필로는 못 잰다"고 적었던 근거는 hybrid인 두 프로필이 **둘 다 전 역할 Fake**라
    `is_fake` 축과 `retriever` 축이 같은 답을 낸다는 것이었다. 이 프로필은 두 축이
    갈리는 첫 실물이다 — hybrid이면서 embedding·reranker가 실서빙이라, 어느 한
    축만 보는 구현은 여기서 갈린다.

    **프로필 이름을 박지 않고 조합으로 찾는다** — `test_some_profile_actually_points_
    at_a_real_endpoint`와 같은 방식이다. 이름이 바뀌어도 그물이 남아야 한다.
    """
    paired = [
        profile
        for profile in ALL_PROFILES
        for config in [load_config(profile)]
        if config.profile.adapters.retriever == "hybrid"
        and all(not config.model_for(role).is_fake for role in RETRIEVAL_ROLES)
    ]

    assert paired, (
        "hybrid 배선에 실서빙 embedding·reranker를 짝지은 프로필이 없다 — "
        "위 테스트의 합성 프로필 말고는 이 방향을 재는 것이 없다"
    )
    for profile in paired:
        served = load_config(profile).served_models()
        assert set(RETRIEVAL_ROLES) <= set(served), f"{profile}: 뜨는데 판정 대상에서 빠졌다"


def test_the_hybrid_serving_profile_differs_from_local_balanced_only_in_the_retriever() -> None:
    """대조군과의 차이가 `retriever` **하나**로 유지된다.

    이 프로필의 쓸모가 그 하나에 걸려 있다. roles나 budget이 함께 움직이면 두 프로필의
    run 차이가 무엇에서 왔는지 못 가른다 — evaluation.md §2.3 규칙 1이 조건 비교에
    요구하는 것과 같은 성질이고, 여기서는 조건 비교표가 아니라 **검색 경로를 켠 효과**를
    보려는 것이라 축이 하나여야 한다는 요구가 더 강하다.

    한쪽만 고치는 것을 사람이 알아채기 어려워 기계로 고정한다.
    """
    base = load_config("local_balanced").profile
    variant = load_config(HYBRID_SERVING_PROFILE).profile

    assert variant.roles == base.roles
    assert variant.budget == base.budget
    assert variant.adapters.retriever == "hybrid"
    assert base.adapters.retriever == "fake"
    assert variant.adapters.model_dump(exclude={"retriever"}) == base.adapters.model_dump(
        exclude={"retriever"}
    )
    # 두 검색 모델의 benchmark는 checksum과 달리 아직 전부 0이다 — config/profiles.yaml의
    # 같은 프로필 주석과 같은 문장이다. scout·auditor는 2026-09-04에 처리량이 채워졌지만
    # `mem_gb_ctx8k`가 0이라 부분 측정이고, `cli._check_benchmarks`가 부분 측정도
    # production에서 막는다 (model-serving.md §5).
    assert variant.production is False


def test_prompted_roles_matches_prompt_directories() -> None:
    """`PROMPTED_ROLES`와 `prompts/` 하위 디렉터리가 일치한다.

    상수가 현실과 갈라지면 doctor가 없는 프롬프트를 확인하려 들거나 있는 프롬프트를
    건너뛴다. `config`는 디스크 배치에 의존하면 안 되므로 상수로 두되, 그 상수가 맞는지는
    여기서 기계로 확인한다.
    """
    on_disk = {
        path.name
        for path in (CONFIG_DIR.parent / "prompts").iterdir()
        if path.is_dir() and not path.name.startswith((".", "_"))
    }
    assert set(PROMPTED_ROLES) == on_disk


def _prompt_dirs() -> list[Path]:
    """디스크에 있는 `prompts/<role>/<version>/` 전부."""
    root = CONFIG_DIR.parent / "prompts"
    return sorted(
        version
        for role in root.iterdir()
        if role.is_dir() and not role.name.startswith((".", "_"))
        for version in role.iterdir()
        if version.is_dir()
    )


@pytest.mark.parametrize("directory", _prompt_dirs(), ids=lambda d: f"{d.parent.name}/{d.name}")
def test_every_prompt_version_on_disk_loads(directory: Path) -> None:
    """`prompts/`에 올려 둔 **모든 버전**이 로더를 통과하고 meta가 디렉터리와 맞는다.

    **버전 단위 검사가 없었다.** `test_prompted_roles_matches_prompt_directories`는 역할
    디렉터리만 보므로, 새 버전에 `{{FACTS}}` 블록이 빠지거나 `output_schema` 이름이
    틀려도 그 버전을 실제로 쓰는 run이 나올 때까지 아무것도 안 깨진다 — 프롬프트는
    모델 입력이라 그때는 이미 GPU 시간을 쓴 뒤다.

    `prompt_template_version`은 모델별 설정이라 **아무도 안 켠 버전이 디스크에 있을 수
    있다.** 그런 버전일수록 여기서 걸러야 한다.
    """
    role, version = Role(directory.parent.name), directory.name

    template = load_prompt(role, version)

    assert template.role is role
    assert template.version == version
    assert getattr(schemas, template.output_schema, None) is not None, (
        f"{directory}/meta.yaml의 output_schema `{template.output_schema}`가 "
        "`src.schemas`에 없다"
    )


def test_every_prompted_role_is_a_real_role() -> None:
    """`Role(role)`이 `PROMPTED_ROLES`의 모든 값에 대해 성립한다.

    **이 검사가 없어서 `"single"`이 상수에만 들어오고 열거형에는 안 들어왔다.**
    `Role("single")`이 `ValueError`였고 `cli._check_prompts`·`cli._prefix_tokens`·
    `bench_report._role_report` 셋이 그 위를 지나간다. 세 곳 다
    `if role in config.profile.roles` 가드가 있어 지금은 안 터지는데, **그 가드가 막는
    것이 정확히 single-pass 프로필을 붙이는 경우**라 등록의 목적이 달성되는 순간 셋이
    함께 죽는다. 위 디렉터리 대조가 상수↔디스크만 보고 상수↔열거형은 안 봤다.

    한쪽 방향만 본다. `Role.JUDGE`는 프롬프트 디렉터리가 없고 `PROMPTED_ROLES`에도
    없는데, 그것은 아직 프롬프트가 없는 역할이지 어긋남이 아니다.
    """
    assert {Role(role) for role in PROMPTED_ROLES} <= set(Role)


def test_every_prompted_role_has_a_bench_spec() -> None:
    """`Role`에만 넣고 `ROLE_SPEC`을 비워 두면 크래시가 자리만 옮긴다.

    `bench_report._role_report()`가 `ROLE_SPEC[role_enum]`으로 바로 색인한다 —
    `Role("single")`의 `ValueError`가 `KeyError`가 될 뿐이고, 터지는 프로필도 같다.
    """
    for role in PROMPTED_ROLES:
        assert Role(role) in ROLE_SPEC, f"{role}에 bench 스펙이 없다"


def test_dev_fake_needs_no_gpu() -> None:
    """제약: dev_fake만으로 파이프라인이 돌아야 한다. 실서빙 엔드포인트가 있으면 안 된다."""
    config = load_config("dev_fake")
    assert all(config.model_for(role).is_fake for role in config.profile.roles)
    assert config.profile.adapters.state_store == "memory"


def test_single_gpu_12g_serves_scout_only() -> None:
    """12GB 카드에는 scout만 올린다(profiles.yaml의 KV cache 계산 근거 참조).

    embedding·reranker를 포함한 나머지가 Fake가 아니게 되면 이 프로필의 전제가 깨진다.
    """
    config = load_config("single_gpu_12g")
    assert not config.model_for("scout").is_fake
    others = set(config.profile.roles) - {"scout"}
    assert all(config.model_for(role).is_fake for role in others)


# --- 실패 경로 --------------------------------------------------------------


def test_unknown_profile_fails_loudly() -> None:
    with pytest.raises(ConfigError, match="프로필"):
        load_config("nope")


def test_missing_config_dir_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="설정 파일이 없다"):
        load_config("dev_fake", config_dir=tmp_path)


def test_dangling_model_reference_fails(tmp_path: Path) -> None:
    """프로필이 없는 모델을 가리키면 로드에서 실패한다. 실행 중간의 KeyError는 늦다.

    `roles`는 통째로 교체되므로 필수 역할을 남긴 채 scout만 깨뜨린다 — 그래야
    "필수 역할 부재"가 아니라 "참조 깨짐"이 실제로 검사된다.
    """
    _copy_config(
        tmp_path,
        profile_patch={"roles": {"scout": "ghost", "auditor": "fake", "critic": "fake"}},
    )
    with pytest.raises(ConfigError, match="ghost"):
        load_config("dev_fake", config_dir=tmp_path)


def test_dangling_budget_reference_fails(tmp_path: Path) -> None:
    _copy_config(tmp_path, profile_patch={"budget": "ghost"})
    with pytest.raises(ConfigError, match="ghost"):
        load_config("dev_fake", config_dir=tmp_path)


def test_typo_field_is_rejected() -> None:
    """`extra="forbid"` 확인. 오타 필드가 조용히 무시되면 여러 곳이 다른 설정을 본다."""
    with pytest.raises(ValidationError):
        ProfileEntry.model_validate(
            {
                "description": "x",
                "roles": {"scout": "fake", "auditor": "fake", "critic": "fake"},
                "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
                "budget": "dev_fake",
                "prodution": True,  # 오타
            }
        )


def test_profile_without_required_role_is_rejected() -> None:
    with pytest.raises(ValidationError, match="필수 역할"):
        ProfileEntry.model_validate(
            {
                "description": "critic 없음",
                "roles": {"scout": "fake", "auditor": "fake"},
                "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
                "budget": "dev_fake",
            }
        )


def test_unknown_adapter_name_is_rejected() -> None:
    """아직 없는 구현체 이름은 factory의 KeyError가 아니라 설정 로드에서 막힌다."""
    with pytest.raises(ValidationError):
        ProfileEntry.model_validate(
            {
                "description": "x",
                "roles": {"scout": "fake", "auditor": "fake", "critic": "fake"},
                "adapters": {
                    "retriever": "hybrid",  # 미구현
                    "tool_runner": "fake",
                    "state_store": "memory",
                },
                "budget": "dev_fake",
            }
        )


# --- 임계값 표현 ------------------------------------------------------------


def test_thresholds_flatten_is_float_map() -> None:
    """RunRecord.thresholds는 dict[str, float]다. 중첩 값이 남으면 파싱이 실패한다."""
    flat = load_config("dev_fake").thresholds.flatten()
    assert all(isinstance(value, float) for value in flat.values())
    assert any(key.startswith("capsule_budget_tokens.") for key in flat)


def test_the_output_cap_reaches_the_run_record_and_the_gateway_together() -> None:
    """`output_max`가 **기록과 요청 양쪽**에 같은 값으로 간다.

    한쪽만 가면 최악이다 — `RunRecord.thresholds`에 8192가 적혔는데 실제 요청은 상한 없이
    돌면, 기록이 있다는 이유로 아무도 다시 확인하지 않는다. 그래서 두 경로를 한 테스트에서
    함께 고정한다.

    `_model_client`를 직접 부르는 것은 `build_deps()`가 tool_runner까지 조립해 docker·podman이
    없는 환경에서 못 도는 반면, 여기서 재려는 것은 모델 클라이언트 하나이기 때문이다.
    """
    config = load_config("single_gpu_12g")  # scout만 실서빙이라 실클라이언트가 하나 나온다

    assert config.thresholds.flatten()["output_max"] == float(config.thresholds.output_max)

    client = _model_client(config.profile.roles["scout"], config)
    payload = client._request_payload("prefix", "body", RunRecord)  # noqa: SLF001

    assert payload["max_tokens"] == config.thresholds.output_max


@pytest.mark.parametrize("level", list(CapsuleLevel))
def test_every_capsule_level_has_a_budget(level: CapsuleLevel) -> None:
    """CapsuleLevel에 값을 추가하면 여기서 먼저 깨진다 — config가 따라오지 않은 것이다."""
    assert load_config("dev_fake").thresholds.budget_for(level) > 0


def test_unmeasured_models_are_reported_not_blocked() -> None:
    """실측 전 모델을 판정만 하고 차단하지는 않는다 (dev 프로필에서는 정상 상태)."""
    config = load_config("local_balanced")
    assert isinstance(config, HarnessConfig)
    assert isinstance(config.unmeasured_models(), list)


# --- 헬퍼 ------------------------------------------------------------------


def _copy_config(dest: Path, *, profile_patch: dict, model_patch: dict | None = None) -> None:
    """저장소 config를 tmp_path로 복사하고 dev_fake 프로필만 덮어쓴다.

    `model_patch`(model key → 덮어쓸 필드)를 주면 registry 항목도 함께 바꾼다. 그때만
    models.yaml이 yaml 왕복을 거치므로 **주석이 사라진다** — 주석이 살아 있어야 하는
    검사는 원문 치환을 쓴다(`test_partially_measured_models_are_named_with_what_is_missing`).
    """
    budgets = (CONFIG_DIR / "budgets.yaml").read_text("utf-8")
    (dest / "budgets.yaml").write_text(budgets, encoding="utf-8")
    models_text = (CONFIG_DIR / "models.yaml").read_text("utf-8")
    if model_patch is None:
        (dest / "models.yaml").write_text(models_text, encoding="utf-8")
    else:
        raw_models = yaml.safe_load(models_text)
        for key, patch in model_patch.items():
            raw_models["models"][key].update(patch)
        (dest / "models.yaml").write_text(
            yaml.safe_dump(raw_models, allow_unicode=True), encoding="utf-8"
        )
    raw = yaml.safe_load((CONFIG_DIR / "profiles.yaml").read_text("utf-8"))
    raw["profiles"]["dev_fake"].update(profile_patch)
    (dest / "profiles.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")


# --- benchmark 세 상태 (2026-08-17) ------------------------------------------


def _bench(**values: float) -> Benchmark:
    return Benchmark(**values)


def test_benchmark_distinguishes_three_states() -> None:
    """전부 측정 / 부분 측정 / 미측정이 구분되어야 한다.

    `is_measured`가 `any`라 그것만으로는 **절반만 채운 모델이 production 게이트를 그냥
    통과한다.** ATOM에서 `mem_gb_ctx8k`를 nvidia-smi로 얻을 수 없다는 것이 확정됐으므로
    (2026-08-17) 부분 측정은 가정이 아니라 곧 실제로 생기는 상태다.
    """
    none = _bench()
    partial = _bench(tok_s_single=30.0, tok_s_batch32=90.0)
    full = _bench(tok_s_single=30.0, tok_s_batch32=90.0, mem_gb_ctx8k=8.25)

    assert (none.is_measured, none.is_fully_measured) == (False, False)
    assert (partial.is_measured, partial.is_fully_measured) == (True, False)
    assert (full.is_measured, full.is_fully_measured) == (True, True)
    assert partial.missing_measurements == ("mem_gb_ctx8k",)
    assert none.missing_measurements == tuple(Benchmark.MEASUREMENTS)
    assert full.missing_measurements == ()


def test_partially_measured_models_are_named_with_what_is_missing(tmp_path: Path) -> None:
    """부분 측정 모델은 **어느 항목이 비었는지**까지 나와야 한다.

    이름만 나오면 "다시 다 재라"가 되고, 이미 확보한 수치를 버리게 된다.
    """
    for name in ("profiles.yaml", "budgets.yaml"):
        (tmp_path / name).write_text((CONFIG_DIR / name).read_text("utf-8"), encoding="utf-8")
    models = (CONFIG_DIR / "models.yaml").read_text(encoding="utf-8")
    # **`qwen3-4b` 블록만** 채운다. registry의 첫 블록은 `fake`이므로 앞에서부터
    # 치환하면 엉뚱한 모델이 부분 측정이 되고, 이 검사가 보려던 것을 못 본다.
    head, marker, tail = models.partition("  qwen3-4b:")
    assert marker, "registry에 qwen3-4b 블록이 없다 — 이 검사의 전제가 무너졌다"
    patched = head + marker + tail.replace(
        "      tok_s_single: 0\n      tok_s_batch32: 0",
        "      tok_s_single: 30.0\n      tok_s_batch32: 90.0",
        1,
    )
    (tmp_path / "models.yaml").write_text(patched, encoding="utf-8")

    config = load_config("single_gpu_12g", config_dir=tmp_path)

    assert config.partially_measured_models() == {"qwen3-4b": ("mem_gb_ctx8k",)}
    # 부분 측정은 "아직 안 쟀다"와 다르다 — 두 목록이 겹치면 안 된다.
    assert "qwen3-4b" not in config.unmeasured_models()


# --- 계열 규칙 (ADR 0010 §4 · 이슈 #121) ---------------------------------------


def test_every_registry_entry_declares_its_family() -> None:
    """registry 전 항목이 `family`를 선언한다.

    이 필드가 비면 아래 교차검사가 **판정을 못 한다**. 그런데 못 하는 것과 통과하는 것이
    같아 보이므로, 항목 하나가 비어 있으면 그 모델을 Critic에 꽂는 프로필이 검사를
    지나가 버린다 — 이 항목(#121)이 지적한 "틀려도 아무것도 안 잡는다"가 그대로 돌아온다.

    새 모델을 registry에 올리는 사람이 이 줄을 빠뜨리면 여기서 먼저 걸린다.
    """
    models = load_config("dev_fake").models

    unnamed = sorted(key for key, entry in models.items() if entry.family is None)
    assert unnamed == [], f"계열 미상 registry 항목: {unnamed}"


def test_a_critic_from_the_same_family_as_the_auditor_is_rejected(tmp_path: Path) -> None:
    """Auditor와 Critic이 같은 계열이면 **로드에서** 막힌다 (ADR 0010 §4).

    evaluation.md §2.1이 B3/B3'의 조건으로 "제작사가 다른 모델"을 못박는데, 그것을
    검사하는 코드가 저장소에 없었다(#121). 어긋나면 조용히 어긋난다 — 프로필 한 줄로
    Critic이 Auditor와 같은 계열이 되어도 그 run은 `B3prime@...` 라벨을 달고 완주하고,
    registry에 계열을 적을 칸이 없어 **사후에도 자동으로 못 가린다.**

    여기서 쓰는 쌍(`qwen3-coder-30b-a3b` × `qwen3-8b`)은 registry에 실재하는 모델
    둘이고 키가 다르다 — 즉 지금 프로필에 그대로 적을 수 있는 조합이다. 그것이 이
    검사가 막아야 하는 실물이다.
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "fake",
                "auditor": "qwen3-coder-30b-a3b",
                "critic": "qwen3-8b",
            }
        },
    )

    with pytest.raises(ConfigError, match="계열"):
        load_config("dev_fake", config_dir=tmp_path)


def test_a_family_unknown_model_does_not_pass_the_cross_check(tmp_path: Path) -> None:
    """계열이 **미상**인 모델은 통과가 아니다 (CLAUDE.md §0).

    미상을 "다르다"로 읽으면 이 검사는 `family:`를 안 적는 것만으로 우회된다 — 강제
    장치가 있다고 믿는 채로 아무것도 안 잡는 상태가 되고, 그것이 #121이 지적한 원래
    상태보다 나쁘다. 값이 없다는 것은 판정할 근거가 없다는 뜻이므로 여기서 실패한다.
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "fake",
                "auditor": "qwen3-coder-30b-a3b",
                "critic": "devstral-small-2-24b",
            }
        },
        model_patch={"devstral-small-2-24b": {"family": None}},
    )

    with pytest.raises(ConfigError, match="계열"):
        load_config("dev_fake", config_dir=tmp_path)


def test_b1_puts_one_model_in_every_role_and_still_loads() -> None:
    """B1은 **정의상** auditor와 critic이 같은 모델이다 — 막으면 안 된다.

    build-plan §8이 요구하는 "항상 최강 단일 멤버와 비교"의 그 조건이고, 계열 검사가
    단순 부등이면 이 프로필이 로드에서 죽어 **비교표의 기준선이 통째로 사라진다**
    (ADR 0010 §4 "같은 키(B1)는 정의상 허용한다").
    """
    config = load_config("b1_single")

    assert config.model_key_for("auditor") == config.model_key_for("critic")
    assert config.model_for("auditor").family == config.model_for("critic").family
