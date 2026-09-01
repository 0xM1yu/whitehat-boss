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
    """`extra="forbid"` 확인. 오타 필드가 조용히 무시되면 8개 트랙이 다른 설정을 본다."""
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


def _copy_config(dest: Path, *, profile_patch: dict) -> None:
    """저장소 config를 tmp_path로 복사하고 dev_fake 프로필만 덮어쓴다."""
    for name in ("models.yaml", "budgets.yaml"):
        (dest / name).write_text((CONFIG_DIR / name).read_text("utf-8"), encoding="utf-8")
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
