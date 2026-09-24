"""config 로더 계약 테스트.

engineering-guide.md §8.5(임계값·예산·모델은 config에서)와 §8.7(비결정적 로직에는
테스트를 남긴다)이 근거다.

**구체 수치는 단언하지 않는다.** `scout_threshold`가 0.5인지, 예산이 얼마인지는 이
프로젝트가 실측으로 정할 값이다(open-questions D2·D3). 테스트가 상수로 박으면 값을
조정할 때마다 테스트가 깨지고, 결국 값이 아니라 테스트를 맞추게 된다.
여기서 고정하는 것은 **참조 정합성과 불변식**뿐이다.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

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
    SCOUT_POOL_ROLES,
    Benchmark,
    ConfigError,
    HarnessConfig,
    ModelEntry,
    ProfileEntry,
    load_config,
)
from src.orchestrator.nodes import auditor as auditor_node
from src.orchestrator.nodes import critic as critic_node
from src.orchestrator.nodes import scout as scout_node
from src.orchestrator.prompt_loader import load_prompt
from src.orchestrator.runner import RunSpec, _new_record
from src.schemas import CapsuleLevel, RunRecord
from src.schemas.common import PLACEHOLDER_DIGEST
from src.schemas.enums import Role
from tests.conftest import ALL_PROFILES

ZERO_DIGEST = PLACEHOLDER_DIGEST

# 역할 → 그 역할로 모델을 부르는 노드가 `ctx.call_model(schema=...)`에 넣는 스키마.
# **scout 네 자리는 여기 없다** — 버전별로 스키마가 갈려(v1·v2=Candidate, v3=ScoutResponse
# 래퍼) 노드가 「하나」를 박지 않고 프롬프트 meta 선언을 따른다(`nodes/scout.py`의
# `resolve_output_schema`). 그래서 아래 검사는 scout을 **허용집합**으로 본다.
# 풀 세 멤버는 별도 노드가 아니라 `nodes/scout.py`의 `run(role=...)`이 돈다(ADR 0010 §3).
# `single`·`judge`는 아직 노드가 없어 여기 없다 — bench 쪽 검사만 선다.
NODE_OUTPUT_SCHEMAS = {
    Role.AUDITOR: auditor_node.OUTPUT_SCHEMA,
    Role.CRITIC: critic_node.OUTPUT_SCHEMA,
}

# scout 자리가 낼 수 있는 스키마 이름. 노드·bench가 프롬프트 선언을 이 집합 안에서 고른다.
SCOUT_ALLOWED_SCHEMA_NAMES = {s.__name__ for s in scout_node.ALLOWED_OUTPUT_SCHEMAS}


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
    들어왔다. (두 프로필은 2026-09-24에 지웠다 — 지금 같은 모양은 `harness_pool`이다:
    `retriever: fake`인데 embedding·reranker에 실모델을 선언한다.)

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
    그 파일이 다시 바뀌면 함께 사라진다. 합성 프로필은 `retriever: fake`에 실서빙 검색 모델을
    선언한 프로필 하나만 있으면 서므로 게이트 자체를 지키는 그물은 이쪽이다. 그 기반이
    `b1_single`이었고 2026-09-24에 그것을 지우며 `harness_pool`로 옮겼다(같은 모양이다).
    """
    base = load_config("harness_pool")
    profile = base.profile.model_copy(
        update={"adapters": base.profile.adapters.model_copy(update={"retriever": "hybrid"})}
    )
    served = base.model_copy(update={"profile": profile}).served_models()

    assert set(RETRIEVAL_ROLES) <= set(served), (
        "hybrid 배선에서는 embedding·reranker가 실제로 뜬다 — 판정 대상에 있어야 한다"
    )


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


POOL_HYBRID_PROFILE = "harness_pool_hybrid"


def test_the_pool_hybrid_profile_differs_from_harness_pool_only_in_the_retriever() -> None:
    """풀 검색 프로필도 대조군(`harness_pool`)과의 차이가 `retriever` **하나**다.

    이 프로필의 쓸모가 검색 경로를 켜는 그 한 줄에 걸려 있고, roles나 budget이 함께
    움직이면 풀 run의 차이가 무엇에서 왔는지 못 가른다. 한쪽만 고치는 것을 사람이 놓치기
    쉬워 기계로 고정한다. (같은 성질을 지키던 옛 `local_balanced` 쌍 검사는 그 쌍을
    2026-09-24에 지우며 함께 뺐다 — 남은 실서빙 hybrid 짝은 이 둘뿐이다.)
    """
    base = load_config("harness_pool").profile
    variant = load_config(POOL_HYBRID_PROFILE).profile

    assert variant.roles == base.roles
    assert variant.budget == base.budget
    # 실행 단위도 같아야 한다 — 한쪽만 `per_stage`면 두 run이 루프 순서까지 갈린다.
    assert variant.execution == base.execution
    assert variant.adapters.retriever == "hybrid"
    assert base.adapters.retriever == "fake"
    assert variant.adapters.model_dump(exclude={"retriever"}) == base.adapters.model_dump(
        exclude={"retriever"}
    )
    # 검색 두 모델(`qwen3-embedding-0.6b`·`qwen3-reranker-4b`)의 benchmark는 아직 0이고
    # 별칭·gpt-oss도 0이라, production으로 올리지 않는다 — `cli._check_benchmarks`가
    # 부분/미측정을 막는다(model-serving.md §5).
    # 리랭커는 4b다(2026-09-20, #152). 0.6b를 쓰던 옛 `local_balanced` 쌍과 그 키는
    # 2026-09-24에 지웠다.
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
        f"{directory}/meta.yaml의 output_schema `{template.output_schema}`가 `src.schemas`에 없다"
    )


@pytest.mark.parametrize("directory", _prompt_dirs(), ids=lambda d: f"{d.parent.name}/{d.name}")
def test_the_prompt_output_schema_is_what_the_caller_actually_requests(directory: Path) -> None:
    """`meta.yaml`의 `output_schema`가 **그 역할로 모델을 부르는 코드가 실제로 보내는**
    스키마와 같다.

    바로 위 `test_every_prompt_version_on_disk_loads`는 그 이름이 `src.schemas`에 **있는지**만
    본다 — 있기만 하면 노드가 무엇을 보내든 통과한다. 프롬프트는 모델이 읽는 명령이고
    스키마는 grammar가 강제하는 문법이라, 둘이 어긋나면 **모델이 시킨 대로 쓸 수 없는
    필드를 시키는** 상태가 된다. `HarnessModel`이 `extra="forbid"`라 그 필드는
    `additionalProperties: false`로 마스크되고, 마스크가 추론을 덮는 것이 이 PR이 고치는
    바로 그 실패다(gpt-oss-20b 실측 2026-09-12 — **원천: PR #182 본문, 원문 미커밋**.
    `docs/experiments/harness/2026-09-12-critic-decisiondraft-budget-atom.md` §5).

    **사고 —** `nodes/critic.py`가 `schema=DecisionDraft`로 바뀐 뒤에도 `prompts/critic/v1`
    규칙 4가 *"`ids.decision_id`를 그대로 쓴다"*였고 출력표에 `decision_id`·`run_id`·
    `finding_id`가 남아 있었다. `DecisionDraft`에는 그 셋이 없다. v1은 `config/profiles.yaml`의
    **전 프로필**이 고르는 살아 있는 프롬프트다.

    요청하는 곳이 **둘**이라 둘 다 본다 — 파이프라인 노드와 `bench.ROLE_SPEC`(bench는
    같은 프롬프트 파일을 렌더해 구조화 출력을 잰다). 노드가 없는 역할(`single`)은
    bench 쪽만 선다.
    """
    role, version = Role(directory.parent.name), directory.name

    template = load_prompt(role, version)

    # scout 네 자리는 버전별로 스키마가 갈린다 — 노드도 bench도 프롬프트 meta 선언을
    # 허용집합 안에서 고른다(`nodes/scout.py`, `bench_report.py`). 그래서 「하나」와의
    # 동치가 아니라 **집합 소속**을 본다. 집합 밖 이름(예: critic처럼 DecisionDraft)을
    # scout meta가 선언하면 여기서 걸리고 런타임에서도 `resolve_output_schema`가 막는다.
    if role in scout_node.SCOUT_ROLES:
        assert template.output_schema in SCOUT_ALLOWED_SCHEMA_NAMES, (
            f"{directory}/meta.yaml은 `{template.output_schema}`를 선언하는데 "
            f"scout 자리가 낼 수 있는 것은 {sorted(SCOUT_ALLOWED_SCHEMA_NAMES)}뿐이다"
        )
        return

    node_schema = NODE_OUTPUT_SCHEMAS.get(role)
    if node_schema is not None:
        assert template.output_schema == node_schema.__name__, (
            f"{directory}/meta.yaml은 `{template.output_schema}`를 선언하는데 "
            f"노드는 `{node_schema.__name__}`을 요청한다"
        )
    assert template.output_schema == ROLE_SPEC[role][0].__name__, (
        f"{directory}/meta.yaml은 `{template.output_schema}`를 선언하는데 "
        f"bench.ROLE_SPEC은 `{ROLE_SPEC[role][0].__name__}`을 요청한다"
    )


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_every_profile_can_load_the_prompt_version_it_selects(profile: str) -> None:
    """프로필이 **고른** 프롬프트 버전이 디스크에 실재한다.

    바로 위 `test_every_prompt_version_on_disk_loads`는 반대 방향이다 — 디스크에 올라온
    것이 로더를 통과하는지만 본다. **고르는 쪽은 registry의 `prompt_template_version`**
    이고, 그 값이 없는 디렉터리를 가리켜도 `load_config`는 통과한다: 프롬프트는 해당
    역할의 첫 모델 호출에서 처음 열리므로 **GPU 시간을 쓴 뒤에 죽는다.** `cli doctor`의
    `_check_prompts`가 같은 것을 보지만 `run`이 `doctor`를 부르지 않아 그 앞에 서지 못한다.

    실서빙 프로필은 단일 `scout`이고 registry가 `v1`이라 통과한다. **풀 프로필
    `local_balanced_pool`도 통과하는데, 그것은 이 검사가 잡을 뻔한 간극을 v2 별칭이
    메우기 때문이다** — 풀 세 역할의 프롬프트 디렉터리는 `v2`만 있고(D54), base 셋은 v1이라
    풀을 base로 배선했으면 여기서 걸렸을 것을, PR #190이 `prompt_template_version: v2` 별칭
    셋을 등재해 그 프로필이 v2를 가리키게 했다. 그래서 이 검사는 **별칭이 실제로 v2를
    가리키는지**를 지키는 자리로 선다 — 별칭 하나가 base로 되돌아가면 여기서 걸린다.

    ⚠ **2026-09-24 — 위 문단은 PR #190 때의 서술이다.** 2026-09-21 교체로 그 프로필(지금 이름
    `harness_pool`)의 Scout 세 키가 v3를 가리키고 별칭 셋은 배선이 없다. 지금 이 검사가 풀에서
    지키는 것은 **v3 디렉터리의 실재**다.
    """
    config = load_config(profile)

    for role in PROMPTED_ROLES:
        if role not in config.profile.roles:
            continue
        version = config.model_for(role).prompt_template_version
        assert load_prompt(Role(role), version).version == version


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


def _scout_arrangement(**roles: str) -> dict[str, object]:
    """Scout 자리만 갈아 끼우는 프로필 원본. 나머지 역할·어댑터는 고정한다."""
    return {
        "description": "scout 자리 검증용",
        "roles": {"auditor": "fake", "critic": "fake", **roles},
        "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
        "budget": "dev_fake",
    }


def test_a_profile_declaring_the_scout_pool_loads() -> None:
    """풀 셋만 선언한 프로필이 선다 (ADR 0010 §3).

    *"프로필은 `roles:`에 그 셋을 적고, 기존 `scout` 한 역할 프로필도 그대로
    유효하다(둘 중 하나가 있어야 한다)."* 이 방향이 막혀 있었다 — `REQUIRED_ROLES`가
    `scout`을 무조건 요구해서, 풀 프로필은 **로드 자체가 안 됐다.**
    """
    entry = ProfileEntry.model_validate(
        _scout_arrangement(scout_memory="fake", scout_boundary="fake", scout_injection="fake")
    )

    assert set(SCOUT_POOL_ROLES) <= set(entry.roles)
    assert "scout" not in entry.roles


def test_a_profile_declaring_the_single_scout_still_loads() -> None:
    """기존 단일 `scout` 프로필은 그대로 유효하다 (ADR 0010 §3의 "둘 중 하나").

    `config/profiles.yaml`의 프로필이 **현재 전부** 이쪽이라, 이 방향이 막히면 저장소의
    프로필이 하나도 안 선다. 개수는 적지 않는다 — 프로필이 늘고 주는 값이라
    `test_source_prose_does_not_carry_a_stale_profile_count`가 잡는 자리다.
    """
    entry = ProfileEntry.model_validate(_scout_arrangement(scout="fake"))

    assert "scout" in entry.roles
    assert not set(SCOUT_POOL_ROLES) & set(entry.roles)


def test_a_profile_with_neither_a_single_scout_nor_the_pool_is_rejected() -> None:
    """4단계를 설 사람이 없는 프로필은 로드 시점에 막는다.

    `REQUIRED_ROLES`에서 `scout`을 뺀 것의 대가가 이것이다 — 아무 조치도 안 하면
    Scout 없는 프로필이 조용히 로드되고, 4단계에서 후보가 0건인 run이 "취약점이
    없었다"로 읽힌다.
    """
    with pytest.raises(ValidationError, match="Scout 자리"):
        ProfileEntry.model_validate(_scout_arrangement())


def test_a_profile_declaring_only_part_of_the_pool_is_rejected() -> None:
    """풀은 셋 전부이거나 하나도 없거나다.

    둘만 선언하면 그 프로필은 **한 CWE 계열을 통째로 안 본다**
    ([PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md)
    §1의 계열 배정). 그 사실이 후보 수 감소로만 나타나므로 run 결과만 봐서는 안 보인다.
    """
    with pytest.raises(ValidationError, match="Scout 자리"):
        ProfileEntry.model_validate(
            _scout_arrangement(scout_memory="fake", scout_boundary="fake")
        )


def test_a_profile_declaring_both_the_single_scout_and_the_pool_is_rejected() -> None:
    """단일 `scout`과 풀을 함께 선언하면 4단계 배선이 정해지지 않는다.

    ADR 0010 §3은 *"둘 중 하나가 있어야 한다"*까지만 적고 겹침을 명시적으로 금지하지는
    않는다. 여기서 막는 근거는 그 문장이 아니라 배선이다 — 둘 다 있으면 **4단계를 어느
    클라이언트가 도는지 config만 보고 정할 수 없고**, 그 선택이 코드 어딘가의 우선순위로
    내려가면 프로필은 두 배선을 같은 모양으로 적으면서 다른 run을 낸다.

    ⚠ **"안 돈 역할의 해시가 `model_checksums`에 남는다"는 근거가 아니다.** 이 저장소는
    그 상태를 이미 정당한 것으로 다룬다 — `retriever: fake` 프로필의 embedding·reranker가
    정확히 그것이고, `served_models()`가 *"선언 자체는 지우지 않는다 … 그래서
    기록(`model_checksums`)은 그대로 두고 판정 대상만 좁힌다"*로 그 판단을 적어 놓았다(위
    `test_served_models_skips_roles_this_profile_never_wires`가 그 계약을 고정한다).
    """
    with pytest.raises(ValidationError, match="Scout 자리"):
        ProfileEntry.model_validate(
            _scout_arrangement(
                scout="fake",
                scout_memory="fake",
                scout_boundary="fake",
                scout_injection="fake",
            )
        )


def test_the_scout_pool_shares_one_prompt_version_on_disk() -> None:
    """풀 세 멤버의 프롬프트 버전 집합이 서로 같다 (ADR 0010 §6).

    *"풀 세 멤버가 다른 버전이면 같은 단계에 다른 계약이 섞이므로 풀은 한 버전이어야
    한다."* 한 멤버에만 새 버전을 올리는 것이 그 혼합을 만드는 가장 싼 길이라 여기서
    막는다.

    **어느 버전인지는 단언하지 않는다.** 이 파일 머리의 규약대로 값이 아니라 불변식만
    고정한다 — `v2`를 박으면 버전을 올릴 때 테스트를 맞추게 된다.
    """
    root = CONFIG_DIR.parent / "prompts"
    versions = {
        role: frozenset(d.name for d in (root / role).iterdir() if d.is_dir())
        for role in SCOUT_POOL_ROLES
    }

    assert len(set(versions.values())) == 1, f"풀 멤버의 프롬프트 버전이 갈렸다: {versions}"


# PROJECT-CONTEXT.md §1이 4단계 풀 세 자리와 5·7단계에 앉힌 registry 키. auditor·critic은
# 계열이 갈리는 쌍이라(`qwen` × `gpt-oss`) `_check_family_split`을 지난다. (2026-09-24 전에는
# 옛 로스터 키 셋이었다 — 그 키들은 로스터 밖 정리로 지웠다.)
#
# **`fake`로 대신하지 않는다.** 아래 검사가 보는 것은 「풀 역할이 고른 버전의
# 디렉터리가 있는가」이고, 그 값을 실제로 움직일 PR은 로스터 모델의
# `prompt_template_version`을 올리는 쪽이다. `fake`를 쓰면 그 PR이 이 검사를 안 건드리고
# 지나간다.
_POOL_PROFILE_ROLES = {
    "scout_memory": "seccoderx-8b",
    "scout_boundary": "seccoderx-8b",
    "scout_injection": "qwen3-14b",
    "auditor": "qwen3.6-35b-a3b",
    "critic": "gpt-oss-20b",
}


def _scout_pool_prompt_version() -> str:
    """풀 세 멤버가 **공통으로** 가진 프롬프트 버전 하나.

    값을 박지 않는 것은 이 파일 머리의 규약이다 — `v2`를 적으면 버전을 올릴 때 값이
    아니라 테스트를 맞추게 된다. 공통 버전이 하나뿐인지는 바로 위
    `test_the_scout_pool_shares_one_prompt_version_on_disk`가 따로 본다.
    """
    root = CONFIG_DIR.parent / "prompts"
    shared = set.intersection(
        *({d.name for d in (root / role).iterdir() if d.is_dir()} for role in SCOUT_POOL_ROLES)
    )
    assert shared, "풀 세 멤버가 공통으로 가진 프롬프트 버전이 없다"
    return max(shared)


def test_a_scout_pool_profile_resolves_every_prompt_it_declares(tmp_path: Path) -> None:
    """풀 프로필을 **`load_config`로 세워** 프롬프트까지 실제로 연다.

    풀 검사 다섯이 전부 `ProfileEntry.model_validate`에서 멈춰 있었다 — 스키마가 셋을
    받아들이는지만 보고, 그 프로필이 **고른 버전의 프롬프트가 열리는지**는 아무도 안
    봤다. 4단계가 **풀로 옮겨진 뒤에** 할 일이 `load_config` → `model_for(role)
    .prompt_template_version` → `load_prompt`이고, 그 사슬이 이어지는지는 여기서만 걸린다.

    ⚠ **지금 러너는 이 사슬을 안 탄다.** `nodes/scout.py`가 `ROLE = Role.SCOUT` 하나라
    풀 프로필에서는 `model_for("scout")`가 먼저 `ConfigError`를 낸다 — 인자 평가가
    `load_prompt`보다 앞이다. 그러니 이 검사가 지키는 것은 **러너를 옮긴 뒤의 두 번째
    관문**이고, 첫 관문(러너의 역할 고정)은 아직 아무도 안 지킨다.

    registry가 그 버전을 고르게 하려고 `model_patch`로 풀 모델의
    `prompt_template_version`을 디스크에 있는 공통 버전으로 맞춘다. (예전에는 저장소
    registry가 그 값이 아니었고 그 간극을 따로 고정하는 검사가 있었다 — 2026-09-21 교체로
    풀 키가 v3를 가리키게 되어 간극이 닫혔고, 2026-09-24 옛 키를 지우며 그 검사도 뺐다.)
    """
    version = _scout_pool_prompt_version()
    _copy_config(
        tmp_path,
        profile_patch={"roles": dict(_POOL_PROFILE_ROLES)},
        model_patch={
            key: {"prompt_template_version": version} for key in set(_POOL_PROFILE_ROLES.values())
        },
    )

    config = load_config("dev_fake", config_dir=tmp_path)

    for role in SCOUT_POOL_ROLES:
        template = load_prompt(Role(role), config.model_for(role).prompt_template_version)
        assert template.role is Role(role)
        assert config.prompt_versions()[role] == version


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
    # 실서빙 역할이 있는 프로필이어야 실클라이언트가 나온다
    # (2026-09-24 전에는 `single_gpu_12g`의 scout).
    config = load_config("harness_pool")

    assert config.thresholds.flatten()["output_max"] == float(config.thresholds.output_max)

    client = _model_client(config.profile.roles["scout_memory"], config, role="scout_memory")
    payload = client._request_payload("prefix", "body", RunRecord)  # noqa: SLF001

    assert payload["max_tokens"] == config.thresholds.output_max


# --- 역할별 출력 상한 (D46 (c)) ------------------------------------------------
#
# **자리만 연다. 값은 이 PR이 안 정한다.** 어느 역할에 얼마를 주는지는 실측으로 정할
# 값이고(이 파일 모듈 docstring), 여기서 고정하는 것은 **선언하면 그 역할만 바뀌고
# 선언 안 하면 한 톨도 안 바뀐다**는 불변식뿐이다.


def test_a_role_without_a_declared_cap_is_untouched_by_the_new_slot() -> None:
    """선언이 없으면 전역값 그대로다 — 그리고 **기록도 안 늘어난다**.

    `ModelEntry.reasoning_effort`가 낸 전례다: 선언 안 한 역할은 요청에도 기록에도
    키가 안 생긴다. `RunRecord.thresholds`에 `output_max_by_role.*`가 빈 채로 생기면
    **기존 manifest와 새 manifest가 다른 모양**이 되는데, 그 차이가 뜻하는 것이 없다.
    """
    thresholds = load_config("harness_pool").thresholds

    assert all(
        thresholds.output_max_for(role) == thresholds.output_max for role in PROMPTED_ROLES
    ), "선언이 없는데 역할마다 다른 값이 나온다"
    assert not [key for key in thresholds.flatten() if key.startswith("output_max_by_role")], (
        "선언이 없는데 기록에 키가 생겼다 — 기존 manifest와 모양이 갈린다"
    )


def test_a_declared_role_cap_reaches_the_gateway_and_the_run_record(tmp_path: Path) -> None:
    """선언하면 **그 역할만** 바뀌고, 요청과 기록 양쪽에 같은 값으로 간다.

    선례 `test_the_output_cap_reaches_the_run_record_and_the_gateway_together`가 전역값에
    대해 거는 것과 같은 이유다 — 한쪽만 가면 최악이고, 기록이 있다는 이유로 아무도 다시
    확인하지 않는다.

    ⚠ **기제는 `reasoning_effort`와 다르다.** 저쪽은 문자열이라 `dict[str, float]`인
    `thresholds`에 못 들어가 `RunRecord`에 **필드를 새로 냈다**. 이쪽은 int라 그 자리에
    그대로 들어가므로 `flatten()`이 실어 주면 끝이다 — 새 필드를 내지 않는다.
    """
    _copy_config(
        tmp_path,
        # critic만 선언한다 — scout이 안 바뀌는 것까지 함께 고정해야 "전 역할에 같은
        # 값이 실렸다"는 반대 방향 결함이 안 지나간다.
        # auditor·critic은 계열이 달라야 한다(`_check_family_split`) — qwen × gpt-oss.
        profile_patch={
            "roles": {
                "scout": "seccoderx-8b",
                "auditor": "qwen3.6-35b-a3b",
                "critic": "gpt-oss-20b",
            }
        },
        thresholds_patch={"output_max_by_role": {"critic": 8192}},
    )
    config = load_config("dev_fake", config_dir=tmp_path)

    assert config.thresholds.output_max_for("critic") == 8192
    assert config.thresholds.output_max_for("scout") == config.thresholds.output_max

    def sent(role: str) -> int:
        client = _model_client(config.profile.roles[role], config, role=role)
        return client._request_payload("prefix", "body", RunRecord)["max_tokens"]  # noqa: SLF001

    assert sent("critic") == 8192, "선언했는데 요청에 안 실렸다"
    assert sent("scout") == config.thresholds.output_max, "선언 안 한 역할이 함께 움직였다"

    record, _ = _new_record(
        RunSpec(condition="B3prime@pipeline", target=tmp_path, device="atom"),
        config,
        "run_20260912_0001",
        datetime(2026, 9, 12, tzinfo=UTC),
        [],
        {},
        {},
        # compile_flags — 이 검사의 축이 아니다. 어댑터를 안 세웠으므로 `None`이고,
        # 그것은 `RunRecord.compile_flags`가 정한 "이 기록은 남기지 않았다"와 같은 뜻이다.
        None,
    )
    assert record.thresholds["output_max_by_role.critic"] == 8192.0, (
        "요청에는 실리는데 기록에 안 남는다 — engineering-guide.md §5가 그 run을 비교표에서 뺀다"
    )


def test_a_cap_declared_for_a_role_that_does_not_exist_fails_at_load(tmp_path: Path) -> None:
    """오타는 **서빙 때가 아니라 로드 때** 터져야 한다.

    `ModelEntry.reasoning_effort`가 `Literal`로 닫힌 사유가 그대로 걸린다 — 자유 키면
    `critick: 8192`가 로드를 통과하고 **조용히 무시된 채** 전역값으로 돈다. 그 run은
    "역할별 상한을 줬다"고 기록되지만 실제로는 안 준 것이다.
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "seccoderx-8b",
                "auditor": "qwen3.6-35b-a3b",
                "critic": "gpt-oss-20b",
            }
        },
        thresholds_patch={"output_max_by_role": {"critick": 8192}},
    )

    with pytest.raises(ValidationError, match="critick"):
        load_config("dev_fake", config_dir=tmp_path)


@pytest.mark.parametrize("cap", [0, -1])
def test_a_non_positive_role_cap_fails_at_load(tmp_path: Path, cap: int) -> None:
    """`0`은 "상한 없음"이 아니라 **오설정**이다.

    `output_max`가 `Field(gt=0)`인 것과 같은 자리인데 역할별 값에는 그 제약이 안 걸린다 —
    dict의 값이라 `Field`가 못 본다. `_role_caps_name_real_roles`가 손으로 막고 있고,
    그 줄에 테스트가 없었다. `0`이 통과하면 그 역할은 요청에 `max_tokens: 0`을 싣는다.
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "seccoderx-8b",
                "auditor": "qwen3.6-35b-a3b",
                "critic": "gpt-oss-20b",
            }
        },
        thresholds_patch={"output_max_by_role": {"critic": cap}},
    )

    with pytest.raises(ValidationError, match="양수"):
        load_config("dev_fake", config_dir=tmp_path)


def test_asking_the_cap_for_a_role_that_is_not_a_role_fails_loudly() -> None:
    """모르는 문자열에 **조용히 전역값을 주지 않는다**.

    `_model_client`가 `role`을 필수 키워드로 둔 사유(*"기본값이 있으면 역할을 빠뜨린
    조립 지점이 조용히 전역값을 실어 보낸다"*)가 그대로 한 겹 안쪽에 걸린다 — 대소문자가
    다르거나(`Role.CRITIC.value`가 아니라 `"CRITIC"`) 오타인 키는 `dict.get`의 기본값으로
    떨어져 **선언한 상한이 무시된 채** 돌고, 그 run은 "역할별 상한을 줬다"고 기록된다.
    `output_max_by_role`의 오타를 로드 때 막는 것과 같은 자리다.
    """
    thresholds = load_config("harness_pool").thresholds

    for unknown in ("CRITIC", "critick", "nonsense", ""):
        with pytest.raises(ConfigError, match="프롬프트 역할"):
            thresholds.output_max_for(unknown)


@pytest.mark.parametrize("level", list(CapsuleLevel))
def test_every_capsule_level_has_a_budget(level: CapsuleLevel) -> None:
    """CapsuleLevel에 값을 추가하면 여기서 먼저 깨진다 — config가 따라오지 않은 것이다."""
    assert load_config("dev_fake").thresholds.budget_for(level) > 0


def test_unmeasured_models_are_reported_not_blocked() -> None:
    """실측 전 모델을 판정만 하고 차단하지는 않는다 (dev 프로필에서는 정상 상태)."""
    config = load_config("harness_pool")
    assert isinstance(config, HarnessConfig)
    assert isinstance(config.unmeasured_models(), list)


def test_reasoning_effort_reaches_the_gateway_when_the_registry_declares_it(
    tmp_path: Path,
) -> None:
    """registry가 `reasoning_effort`를 적으면 **그 값이 실제 요청에 실려야 한다.**

    ## 왜 필요한가

    확정 로스터의 Critic 자리는 `gpt-oss-20b` + **`reasoning_effort: high`**다
    (`PROJECT-CONTEXT.md` §1). 그런데 그 값이 들어갈 자리가 코드에 없었다 — `ModelEntry`는
    `extra="forbid"`(`HarnessModel`)라 registry에 적으면 `load_config`가 죽고,
    `_request_payload`는 고정 dict라 실을 경로도 없었다. **registry 등재가 끝나도
    그 행은 배선되지 않는 상태였다.**

    ## 왜 「선언만」으로 끝내면 안 되나

    `ModelEntry`에 필드만 열고 요청에 안 실으면 **registry가 거짓이 된다** — 문서와
    기록에는 `high`가 적히는데 서버는 기본값으로 돈다. 이 저장소가 반복해서 밟은
    자리이고(`config.prompt_versions`의 *"프롬프트가 존재하지 않는 역할의 「버전」이
    실행 기록에 적힌다 — 거짓 정보"*), 선례
    `test_the_output_cap_reaches_the_run_record_and_the_gateway_together`가 같은 이유로
    두 경로를 한 검사에 묶는다.

    ## 안 적은 모델은 키가 아예 없어야 한다

    `reasoning_effort`를 안 주는 모델에 `None`을 실어 보내면 그 서버가 거절할 수 있고,
    성공 경로 payload의 키 집합이 모델마다 달라진다. 그래서 **선언했을 때만** 넣는다.
    """
    _copy_config(
        tmp_path,
        # auditor·critic은 계열이 달라야 한다(`_check_family_split`) — gpt-oss × qwen.
        # registry가 **실제로** `reasoning_effort: high`를 선언한 키가 `gpt-oss-20b` 하나라 그것을
        # auditor 자리에, 선언 안 한 `qwen3.6-35b-a3b`를 critic 자리에 둔다 — 패치 없이 registry
        # 그대로를 잰다(2026-09-24 전에는 옛 키 `qwen3-8b`에 패치로 선언시켰다).
        profile_patch={
            "roles": {
                "scout": "seccoderx-8b",
                "auditor": "gpt-oss-20b",
                "critic": "qwen3.6-35b-a3b",
            }
        },
    )

    config = load_config("dev_fake", config_dir=tmp_path)

    assert config.model_for("critic").reasoning_effort is None, (
        "선언 안 한 모델은 `None`이어야 한다"
    )
    assert config.model_for("auditor").reasoning_effort == "high"

    declared = _model_client(config.profile.roles["auditor"], config, role="auditor")
    payload = declared._request_payload("prefix", "body", RunRecord)  # noqa: SLF001
    assert payload["reasoning_effort"] == "high", (
        "registry가 선언했는데 요청에 안 실렸다 — 기록만 참이고 서버는 기본값으로 돈다"
    )

    silent = _model_client(config.profile.roles["critic"], config, role="critic")
    quiet = silent._request_payload("prefix", "body", RunRecord)  # noqa: SLF001
    assert "reasoning_effort" not in quiet, (
        "선언 안 한 모델의 payload에 키가 생겼다 — 서버가 거절할 수 있고 키 집합이 흔들린다"
    )


def test_reasoning_effort_reaches_the_run_record_and_the_gateway_together(
    tmp_path: Path,
) -> None:
    """`reasoning_effort`가 **기록과 요청 양쪽**에 같은 값으로 간다 (@sumin0218 리뷰 #165 HIGH).

    ## 왜 요청 경로만으로는 부족한가

    `engineering-guide.md` §5가 `RunRecord`에 남길 것으로 *"random seed와 sampling 설정"*을
    들고 **"하나라도 빠지면 그 run의 결과는 비교표에 넣지 않는다"**로 닫는다.
    `reasoning_effort`는 하네스가 요청 본문에 실어 **출력을 바꾸는** 값이고, 확정 로스터의
    Critic 행이 *"크기 +3.71점 대 effort +11~13점"*을 근거로 그것을 고른다 —
    **이 run의 실험 변수 자체다.** 기록에 안 남으면 `high`로 돈 run과 기본값으로 돈 run이
    manifest에서 구별되지 않는다.

    선례 `test_the_output_cap_reaches_the_run_record_and_the_gateway_together`가 같은
    이유로 두 경로를 한 검사에 묶는다. ⚠ **다만 기제는 다르다** — `output_max`는
    `thresholds.flatten()`(`dict[str, float]`)을 타는데 `reasoning_effort`는 문자열이라
    그 자리에 못 들어간다. `model_sampling`(역할 → 값, `| None`)과 같은 관용구를 쓴다.

    ## `_new_record`를 부르는 이유

    접근자(`config.reasoning_efforts()`)만 단언하면 **그것을 `RunRecord`에 안 넣어도
    초록이다.** 이 저장소가 반복해 밟은 자리라(`prompt_versions`의 *"거짓 정보"* 주석),
    실제 생산 경로를 부른다.
    """
    _copy_config(
        tmp_path,
        # auditor만 선언한다 — scout·critic이 빈 것까지 함께 고정해야 "전 역할에
        # 같은 값이 실렸다"는 반대 방향 결함이 안 지나간다.
        # auditor·critic은 계열이 달라야 한다(`_check_family_split`) — gpt-oss × qwen. 선언한
        # 키는 registry 그대로의 `gpt-oss-20b`(high)다(위 검사와 같은 배치).
        profile_patch={
            "roles": {
                "scout": "seccoderx-8b",
                "auditor": "gpt-oss-20b",
                "critic": "qwen3.6-35b-a3b",
            }
        },
    )
    config = load_config("dev_fake", config_dir=tmp_path)

    assert config.reasoning_efforts() == {"auditor": "high"}, (
        "선언한 역할만, 선언한 값 그대로 나와야 한다"
    )

    record, _ = _new_record(
        RunSpec(condition="B3prime@pipeline", target=tmp_path, device="atom"),
        config,
        "run_20260901_0001",
        datetime(2026, 9, 1, tzinfo=UTC),
        [],
        {},
        {},
        # compile_flags — 이 검사의 축이 아니다. 어댑터를 안 세웠으므로 `None`이고,
        # 그것은 `RunRecord.compile_flags`가 정한 "이 기록은 남기지 않았다"와 같은 뜻이다.
        None,
    )
    assert record.model_reasoning_effort == {"auditor": "high"}, (
        "요청에는 실리는데 기록에 안 남는다 — §5가 그 run을 비교표에서 뺀다"
    )

    written = json.loads(record.model_dump_json())
    assert written["model_reasoning_effort"] == {"auditor": "high"}, (
        "직렬화에서 사라지면 manifest를 읽는 쪽은 못 본다"
    )

    auditor = _model_client(config.profile.roles["auditor"], config, role="auditor")
    payload = auditor._request_payload("prefix", "body", RunRecord)  # noqa: SLF001
    assert payload["reasoning_effort"] == record.model_reasoning_effort["auditor"], (
        "기록과 요청이 갈렸다 — 기록이 있다는 이유로 아무도 다시 확인하지 않는다"
    )


# --- 헬퍼 ------------------------------------------------------------------


def _copy_config(
    dest: Path,
    *,
    profile_patch: dict,
    model_patch: dict | None = None,
    thresholds_patch: dict | None = None,
) -> None:
    """저장소 config를 tmp_path로 복사하고 dev_fake 프로필만 덮어쓴다.

    `model_patch`(model key → 덮어쓸 필드)를 주면 registry 항목도 함께 바꾼다. 그때만
    models.yaml이 yaml 왕복을 거치므로 **주석이 사라진다** — 주석이 살아 있어야 하는
    검사는 원문 치환을 쓴다(`test_partially_measured_models_are_named_with_what_is_missing`).

    `thresholds_patch`도 같은 규칙이다 — 주면 budgets.yaml이 왕복을 거친다. `output_max`
    처럼 **저장소에는 아직 선언이 없는 값**을 세울 때 쓴다. 저장소 값을 바꿔 재는 검사는
    "값이 실측으로 정해진다"는 이 파일 모듈 docstring의 전제를 깨므로 하지 않는다.
    """
    budgets_text = (CONFIG_DIR / "budgets.yaml").read_text("utf-8")
    if thresholds_patch is None:
        (dest / "budgets.yaml").write_text(budgets_text, encoding="utf-8")
    else:
        raw_budgets = yaml.safe_load(budgets_text)
        raw_budgets["thresholds"].update(thresholds_patch)
        (dest / "budgets.yaml").write_text(
            yaml.safe_dump(raw_budgets, allow_unicode=True), encoding="utf-8"
        )
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
    # **`seccoderx-8b` 블록만** 채운다. registry의 첫 블록은 `fake`이므로 앞에서부터
    # 치환하면 엉뚱한 모델이 부분 측정이 되고, 이 검사가 보려던 것을 못 본다.
    # (2026-09-24 전에는 `qwen3-4b` 블록이었다 — 그 키를 지우며 로스터 키로 옮겼다.)
    head, marker, tail = models.partition("  seccoderx-8b:")
    assert marker, "registry에 seccoderx-8b 블록이 없다 — 이 검사의 전제가 무너졌다"
    patched = (
        head
        + marker
        + tail.replace(
            "      tok_s_single: 0\n      tok_s_batch32: 0",
            "      tok_s_single: 30.0\n      tok_s_batch32: 90.0",
            1,
        )
    )
    (tmp_path / "models.yaml").write_text(patched, encoding="utf-8")

    config = load_config("harness_pool", config_dir=tmp_path)

    assert config.partially_measured_models() == {"seccoderx-8b": ("mem_gb_ctx8k",)}
    # 부분 측정은 "아직 안 쟀다"와 다르다 — 두 목록이 겹치면 안 된다.
    assert "seccoderx-8b" not in config.unmeasured_models()


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

    여기서 쓰는 쌍(`qwen3.6-35b-a3b` × `qwen3-14b`)은 registry에 실재하는 모델
    둘이고 키가 다르다 — 즉 지금 프로필에 그대로 적을 수 있는 조합이다. 그것이 이
    검사가 막아야 하는 실물이다. (2026-09-24 전에는 옛 키 `qwen3-coder-30b-a3b` × `qwen3-8b`였다.)
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "fake",
                "auditor": "qwen3.6-35b-a3b",
                "critic": "qwen3-14b",
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
                "auditor": "qwen3.6-35b-a3b",
                "critic": "gpt-oss-20b",
            }
        },
        model_patch={"gpt-oss-20b": {"family": None}},
    )

    with pytest.raises(ConfigError, match="계열"):
        load_config("dev_fake", config_dir=tmp_path)


@pytest.mark.parametrize("written", ["Qwen", "QWEN", " qwen", "qwen "])
def test_the_family_check_reads_case_and_padding_as_the_same_family(
    tmp_path: Path, written: str
) -> None:
    """`family`는 자유 문자열이라 대소문자·앞뒤 공백이 실제로 갈릴 수 있다.

    `ModelEntry.family`를 `Literal`로 닫지 않은 것은 의도다 — 로스터가 계속 바뀌므로
    registry에 모델을 더할 때마다 코드가 따라오면 안 된다. **그 대가가 표기 흔들림**이고,
    원문 그대로 비교하면 `'Qwen'`과 `'qwen'`이 서로 다른 계열로 통과한다. D59가 막으려던
    "잘못된 짝이 조용히 지나간다"가 바로 그 길로 새어 나간다 — 검사가 있다고 믿는 채로
    아무것도 안 잡는 상태라 원래보다 나쁘다(CLAUDE.md §0).

    **타입은 계속 열어 둔다.** 좁히는 것은 비교뿐이다.
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "fake",
                "auditor": "qwen3.6-35b-a3b",  # family: qwen
                "critic": "gpt-oss-20b",
            }
        },
        model_patch={"gpt-oss-20b": {"family": written}},
    )

    with pytest.raises(ConfigError, match="계열"):
        load_config("dev_fake", config_dir=tmp_path)


def test_b1_puts_one_model_in_every_role_and_still_loads(tmp_path: Path) -> None:
    """B1은 **정의상** auditor와 critic이 같은 모델이다 — 막으면 안 된다.

    build-plan §8이 요구하는 "항상 최강 단일 멤버와 비교"의 그 조건이고, 계열 검사가
    단순 부등이면 이 프로필이 로드에서 죽어 **비교표의 기준선이 통째로 사라진다**
    (ADR 0010 §4 "같은 키(B1)는 정의상 허용한다").

    **합성 프로필로 잰다.** 커밋된 프로필 `b1_single`(옛 로스터 `qwen3-coder-30b-a3b`)은
    2026-09-24 로스터 밖 정리로 지웠다. 이 검사가 지키는 것은 `load_config`의 계열 규칙이라
    프로필과 무관하다.
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "qwen3.6-35b-a3b",
                "auditor": "qwen3.6-35b-a3b",
                "critic": "qwen3.6-35b-a3b",
            }
        },
    )
    config = load_config("dev_fake", config_dir=tmp_path)

    assert config.model_key_for("auditor") == config.model_key_for("critic")
    assert config.model_for("auditor").family == config.model_for("critic").family


# --- 실행 정책 (ADR 0009) ------------------------------------------------------


# 단계 단위로 도는 프로필 — 로스터 canonical 쌍(2026-09-24 @danielcho02).
# 교대(`swap`)는 배선 전이라 어디에도 없다.
PER_STAGE_PROFILES = frozenset({"harness_pool", "harness_pool_hybrid"})


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_only_the_canonical_pool_runs_per_stage_and_nothing_declares_swap(profile: str) -> None:
    """실행 단위를 선언한 것은 canonical 풀 쌍뿐이고, **교대 값은 아직 아무 프로필도 안 박는다.**

    2026-09-24 전에는 `execution`을 선언한 프로필이 0이었다(ADR 0009 결과 1 — 스키마만 열고 값은
    안 박는다). 그날 @danielcho02가 로스터 canonical 배선(`harness_pool`·`_hybrid`)을 단계 단위로 정했다 —
    ADR 0010 §1이 승인한 `per_stage`이고, 네 서버의 util 합이 한 장비를 넘어 단계마다 그 단계의
    모델만 띄우려면 이 루프가 선행이다(`config/profiles.yaml`의 그 주석).

    **교대(`swap`)는 여전히 안 적는다** — `level`·`settle_timeout_s`는 교대 예산 실측(`swap.md` §9)
    전에 적으면 근거 없는 수가 config에 굳는 그 자리이고, 배선(ADR 0009 결과 3)도 아직 없다.
    나머지 프로필은 기본(`per_candidate`)을 그대로 쓴다 — 지금까지의 run과 같다.
    """
    execution = load_config(profile).profile.execution

    if profile in PER_STAGE_PROFILES:
        assert execution is not None
        assert execution.mode == "per_stage"
        assert execution.swap is None
    else:
        assert execution is None


def test_execution_defaults_preserve_todays_behaviour() -> None:
    """`execution:`을 적되 비워 두면 지금 동작 그대로다.

    기본값이 없으면 `execution`을 선언한 프로필이 곧바로 단계 단위가 되고, 러너는 아직
    그 루프가 아니다(ADR 0009 「함께 옮겨야 하는 것」 — 별도 PR). 스키마가 러너보다
    앞서 나가면 config는 통과하는데 실행 의미가 없는 선언이 남는다.
    """
    entry = ProfileEntry.model_validate(
        {
            "description": "x",
            "roles": {"scout": "fake", "auditor": "fake", "critic": "fake"},
            "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
            "budget": "dev_fake",
            "execution": {},
        }
    )

    assert entry.execution is not None
    assert entry.execution.mode == "per_candidate"
    assert entry.execution.swap is None


def test_swap_without_per_stage_is_rejected() -> None:
    """교대는 **단계 단위에서만** 선언할 수 있다 (ADR 0009 불변식 1).

    후보 단위 + 교대는 `swap.md` §6.2의 **512회 교대[계산]** 조합이다 — `candidate_max:
    256`에 후보당 왕복 2회를 곱한 값이고, 그 표의 열 이름이 "비용 [계산]"이다. 아무도
    교대를 512번 세어 보지 않았다(CLAUDE.md §0). 후보 하나가 3~6단계를 관통하므로 교대가
    후보 수만큼 곱해지고, 왕복이 아직 미실측인 지금(§1.3 — "2~6초"는 `/v1/models`
    응답까지만 본 값이라 설계 예산으로 못 쓴다) 그 조합이 도는 순간 run 시간의 대부분을
    어디에 썼는지 사후에 못 가른다.
    **스키마가 막는다** — 그 자리에서 실패하지 않으면 GPU 시간을 쓰고 나서 안다.
    """
    with pytest.raises(ValidationError, match="per_stage"):
        ProfileEntry.model_validate(
            {
                "description": "x",
                "roles": {"scout": "fake", "auditor": "fake", "critic": "fake"},
                "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
                "budget": "dev_fake",
                "execution": {
                    "mode": "per_candidate",
                    "swap": {
                        "resident": ["critic"],
                        "swappable": ["scout", "auditor"],
                        "level": 1,
                        "settle_timeout_s": 30.0,
                    },
                },
            }
        )


def test_swap_naming_a_role_the_profile_does_not_have_is_rejected() -> None:
    """`swap`이 부르는 역할은 그 프로필의 `roles`에 실재해야 한다 (ADR 0009 불변식 2).

    없는 역할을 재우고 깨우는 선언은 **아무 일도 안 하면서 정책이 선 것처럼 보인다** —
    오타 하나(`embeding`)면 그 모델만 교대에서 빠지고, 그 run은 메모리가 왜 안 줄었는지
    설명할 수 없는 상태로 끝난다. `roles`가 참조 무결성을 로드 시점에 보는 것과 같은
    자리다(`_check_model_refs`).
    """
    with pytest.raises(ValidationError, match="roles"):
        ProfileEntry.model_validate(
            {
                "description": "x",
                "roles": {"scout": "fake", "auditor": "fake", "critic": "fake"},
                "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
                "budget": "dev_fake",
                "execution": {
                    "mode": "per_stage",
                    "swap": {
                        "resident": ["critic"],
                        "swappable": ["scout", "embeding"],
                        "level": 1,
                        "settle_timeout_s": 30.0,
                    },
                },
            }
        )


def test_a_complete_swap_declaration_on_per_stage_is_accepted() -> None:
    """위 둘을 다 지킨 선언은 **통과해야 한다.**

    거부만 검사하면 `execution`을 통째로 거부하는 구현도 스위트를 지난다. 그러면 ADR
    0009가 연 축이 실제로는 안 열린 채 열렸다고 기록된다.
    """
    entry = ProfileEntry.model_validate(
        {
            "description": "x",
            "roles": {"scout": "fake", "auditor": "fake", "critic": "fake"},
            "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
            "budget": "dev_fake",
            "execution": {
                "mode": "per_stage",
                "swap": {
                    "resident": ["critic"],
                    "swappable": ["scout", "auditor"],
                    "level": 2,
                    "settle_timeout_s": 30.0,
                },
            },
        }
    )

    assert entry.execution is not None
    assert entry.execution.mode == "per_stage"
    assert entry.execution.swap is not None
    assert entry.execution.swap.resident == ("critic",)
    assert entry.execution.swap.swappable == ("scout", "auditor")
    assert entry.execution.swap.level == 2


def test_per_stage_without_swap_is_allowed() -> None:
    """단계 단위인데 교대는 안 하는 배선이 정당하다 (ADR 0010 §1).

    ADR 0010이 `per_stage`를 승인하면서 **교대는 스키마만 열고 기본은 끈다**고 적었다 —
    단계 단위만으로 단계별 상주가 119.7 GiB에 들어가므로(계산값) 교대 없이 첫 run을
    돌릴 수 있다. `swap`을 필수로 만들면 그 배선을 config로 표현할 수 없다.
    """
    entry = ProfileEntry.model_validate(
        {
            "description": "x",
            "roles": {"scout": "fake", "auditor": "fake", "critic": "fake"},
            "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
            "budget": "dev_fake",
            "execution": {"mode": "per_stage"},
        }
    )

    assert entry.execution is not None
    assert entry.execution.swap is None


def _per_stage_swap(**swap: object) -> dict[str, object]:
    """`swap` 블록만 갈아 끼우는 프로필 원본. 나머지는 통과하는 값으로 고정한다."""
    return {
        "description": "x",
        "roles": {"scout": "fake", "auditor": "fake", "critic": "fake"},
        "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
        "budget": "dev_fake",
        "execution": {
            "mode": "per_stage",
            "swap": {"level": 1, "settle_timeout_s": 30.0, **swap},
        },
    }


def test_a_swap_that_wakes_nobody_is_rejected() -> None:
    """`swappable`이 빈 교대 선언은 막는다 — **ADR 0009의 두 불변식이 아니다.**

    `SwapPolicy`의 docstring이 네 값에 기본을 안 주는 이유로 *"`swap: {}`이 「교대한다」고
    선언하면서 아무 역할도 안 부르는 상태"*를 든다. 그런데 `resident: []` +
    `swappable: []`가 그대로 통과해 **정확히 그 상태**를 만들었다 — 사유를 적고 강제하지
    않으면 다음 사람은 그 사유가 강제되는 것으로 읽는다.

    교대하지 않는 배선의 정본 표현은 `swap`을 **생략하는** 것이고 그쪽은 지금도 정당하다
    (`test_per_stage_without_swap_is_allowed`). 같은 뜻을 적는 두 번째 길이 있는데 그쪽만
    「정책이 선 것처럼」 보이는 것이 이 검사가 막는 것이다.
    """
    with pytest.raises(ValidationError, match="swappable"):
        ProfileEntry.model_validate(_per_stage_swap(resident=[], swappable=[]))
    with pytest.raises(ValidationError, match="swappable"):
        ProfileEntry.model_validate(
            _per_stage_swap(resident=["scout", "auditor", "critic"], swappable=[])
        )


def test_a_role_in_both_swap_lists_is_rejected() -> None:
    """한 역할이 상주이면서 교대일 수는 없다 — 이것도 ADR의 둘이 아니다.

    "재우지 않는다"와 "단계 경계에서 재운다"를 같은 역할에 함께 선언한 것이라, 어느 쪽이
    이기는지 **config만 보고 정할 수 없다.** 배선하는 사람이 아무 쪽이나 고르면 그 run은
    메모리가 왜 그만큼인지 설명할 수 없는 상태로 끝난다.
    """
    with pytest.raises(ValidationError, match="상주"):
        ProfileEntry.model_validate(
            _per_stage_swap(resident=["critic", "scout"], swappable=["scout", "auditor"])
        )


def test_a_repeated_role_in_one_swap_list_is_rejected() -> None:
    """한 목록 안의 중복도 막는다.

    두 목록은 역할 **집합**이고, 같은 이름이 두 번 적힌 것의 유일한 효과는 그 사실을
    눈에서 가리는 것이다(병합 산물이거나 오타다). `resident`가 비는 것은 막지 않는다 —
    전부 교대시키는 정책은 말이 되므로 여기서 판단할 것이 아니다.
    """
    with pytest.raises(ValidationError, match="두 번"):
        ProfileEntry.model_validate(
            _per_stage_swap(resident=["critic"], swappable=["scout", "scout"])
        )


def test_an_empty_resident_list_is_still_accepted() -> None:
    """상주가 없는 정책(전부 교대)은 정당하다 — 위 셋이 여기까지 번지면 안 된다.

    거부만 검사하면 `swap`을 통째로 좁히는 구현도 스위트를 지난다
    (`test_a_complete_swap_declaration_on_per_stage_is_accepted`와 같은 이유).
    """
    entry = ProfileEntry.model_validate(_per_stage_swap(resident=[], swappable=["scout"]))

    assert entry.execution is not None
    assert entry.execution.swap is not None
    assert entry.execution.swap.resident == ()
    assert entry.execution.swap.swappable == ("scout",)


def test_an_unknown_execution_key_is_rejected() -> None:
    """`execution` 안에서도 오타는 조용히 무시되지 않는다.

    새 중첩 모델이 `extra="forbid"` 밖에 서면 `mode`를 `modes`로 적은 프로필이 로드를
    통과하고 기본값으로 돌아간다 — 선언한 사람은 단계 단위라고 믿고 run을 읽는다.
    """
    with pytest.raises(ValidationError):
        ProfileEntry.model_validate(
            {
                "description": "x",
                "roles": {"scout": "fake", "auditor": "fake", "critic": "fake"},
                "adapters": {"retriever": "fake", "tool_runner": "fake", "state_store": "memory"},
                "budget": "dev_fake",
                "execution": {"modes": "per_stage"},
            }
        )


# ---------------------------------------------------------------------------
# 포트 — 교대가 띄우는 순서대로 8000부터 (2026-09-24 @danielcho02)
# ---------------------------------------------------------------------------

# 교대가 첫 모델을 띄우는 포트.
FIRST_PORT = 8000


def _launch_order(config: HarnessConfig) -> list[str]:
    """교대가 모델을 띄우는 순서 — Scout 풀(4단계) → Auditor(5) → Critic(7) → 검색(hybrid 전용).

    러너가 단계마다 교대를 부르는 순서와 같다(Scout 풀 안에서는 `SCOUT_POOL_ROLES` 순서). 검색
    두 모델은 hybrid에서만 뜨므로 canonical run의 넷 **뒤**에 둔다 — canonical run이 8000부터
    차례로 올라가게 하려는 것이다.
    """
    roles = config.profile.roles
    order = (*SCOUT_POOL_ROLES, "auditor", "critic", *RETRIEVAL_ROLES)
    return list(dict.fromkeys(roles[role] for role in order if role in roles))


def _port(entry: ModelEntry) -> int | None:
    return urlparse(entry.endpoint).port


def test_served_ports_count_up_from_8000_in_launch_order() -> None:
    """포트는 교대가 띄우는 순서대로 8000부터 빈틈없이 올라간다.

    2026-09-24 전까지는 로스터를 바꿀 때마다 빈 번호에 끼워 넣어, canonical run이
    8009 → 8010 → 8008 → 8006 순서로 떴다(#267 기준 registry) — 번호만 보고는 어느 단계의
    모델인지 알 수 없었다. 이제 번호가 곧 띄우는 순서다.
    """
    config = load_config("harness_pool")
    keys = _launch_order(config)

    actual = {key: _port(config.models[key]) for key in keys}
    expected = {key: FIRST_PORT + index for index, key in enumerate(keys)}
    assert actual == expected


def test_no_two_served_models_share_a_port() -> None:
    """실서빙 키마다 포트가 따로다.

    한 포트로 두 모델을 부르는 프로필은 프로필을 골라 피할 수도 없다(`docs/model-serving.md`의
    「회피 불가」 문단). 2026-09-24 로스터 밖 정리(#267) 전에는 대안 키끼리 포트를 나눠 썼다
    (8001·8002·8005) — 지금은 그 대안이 없으므로 포트 하나에 키 하나다.
    """
    models = load_config("harness_pool").models
    by_port: dict[int | None, list[str]] = {}
    for key, entry in models.items():
        if entry.endpoint.startswith("fake://"):
            continue
        by_port.setdefault(_port(entry), []).append(key)

    shared = {port: keys for port, keys in by_port.items() if len(keys) > 1}
    assert not shared, f"포트를 같이 쓰는 실서빙 키: {shared}"
