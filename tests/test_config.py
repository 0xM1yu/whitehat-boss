"""config 로더 계약 테스트.

engineering-guide.md §8.5(임계값·예산·모델은 config에서)와 §8.7(비결정적 로직에는
테스트를 남긴다)이 근거다.

**구체 수치는 단언하지 않는다.** `scout_threshold`가 0.5인지, 예산이 얼마인지는 이
프로젝트가 실측으로 정할 값이다(open-questions D2·D3). 테스트가 상수로 박으면 값을
조정할 때마다 테스트가 깨지고, 결국 값이 아니라 테스트를 맞추게 된다.
여기서 고정하는 것은 **참조 정합성과 불변식**뿐이다.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml
from pydantic import ValidationError

from src import schemas
from src.adapters.factory import _model_client
from src.bench import ROLE_SPEC
from src.cli import PROVISIONAL_THRESHOLDS, _expected_calls
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
from src.orchestrator.nodes import pov as pov_node
from src.orchestrator.nodes import scout as scout_node
from src.orchestrator.prompt_loader import META_KEYS, PROMPTS_DIR, PromptError, load_prompt
from src.orchestrator.runner import (
    _AUDITOR_ALIAS_ROUNDS,
    _AUDITOR_CALLEE_CHARS,
    _AUDITOR_CALLER_SITES,
    _AUDITOR_DEFINITION_CHARS,
    _AUDITOR_INDIRECT_MAX,
    _AUDITOR_SOURCE_CHARS,
    _AUDITOR_WINDOW_LINES,
    _AUDITOR_WRAPPER_CALLS,
    _AUDITOR_WRAPPER_DEPTH,
    _AUDITOR_WRAPPER_LINES,
    _AUDITOR_WRITE_LINES,
    RunSpec,
    _new_record,
)
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
    Role.POV: pov_node.OUTPUT_SCHEMA,
}

# scout 자리가 낼 수 있는 스키마 이름. 노드·bench가 프롬프트 선언을 이 집합 안에서 고른다.
SCOUT_ALLOWED_SCHEMA_NAMES = {s.__name__ for s in scout_node.ALLOWED_OUTPUT_SCHEMAS}

# auditor도 같다 — 한 방 판은 `Finding`, 도구 루프 판은 봉투다(이슈 #419). ⏵ 2026-10-06 —
# 봉투가 둘이 됐다: v8·v9는 `AuditorAction`, v10은 `AuditorProcedureAction`이다. 그 payload
# 모델 `AuditProcedure`는 허용집합에 **없다** — 집합은 「코드가 보내는 것」만 든다.
AUDITOR_ALLOWED_SCHEMA_NAMES = {s.__name__ for s in auditor_node.ALLOWED_OUTPUT_SCHEMAS}


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


def test_the_pool_hybrid_profile_differs_from_harness_pool_in_the_retriever_and_swap() -> None:
    """풀 검색 프로필과 대조군(`harness_pool`)의 차이는 `retriever`와 교대 선언 유무뿐이다.

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
    # ⚠ **교대 선언은 다르다**(2026-09-29) — hybrid는 검색 역할 때문에 교대를 선언할 수 없다
    # (목록에 넣으면 `SwapPolicy`가, 빼면 `load_config`가 거부 — ADR 0012 결정 3 뒤).
    # 그래서 두 run은 서버를 띄우는 방식이 갈린다
    # (`harness_pool`은 단계마다 교대 · hybrid는 사람이 띄운다). 모델 호출 순서는 같다.
    assert base.execution is not None and variant.execution is not None
    assert variant.execution.mode == base.execution.mode
    assert base.execution.swap is not None
    assert variant.execution.swap is None
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

    # auditor도 버전별로 갈린다(이슈 #419) — `auditor_node.OUTPUT_SCHEMA`는 한 방 경로의
    # 이름이고, 루프 판은 meta 선언을 `resolve_output_schema`로 푼 것을 요청한다. 그래서
    # scout과 같은 이유로 「하나」와의 동치가 아니라 **집합 소속**을 본다. bench가 같은 선언을
    # 푸는지는 `tests/test_auditor_loop.py`가 따로 문다.
    if role is Role.AUDITOR:
        assert template.output_schema in AUDITOR_ALLOWED_SCHEMA_NAMES, (
            f"{directory}/meta.yaml은 `{template.output_schema}`를 선언하는데 "
            f"auditor 자리가 낼 수 있는 것은 {sorted(AUDITOR_ALLOWED_SCHEMA_NAMES)}뿐이다"
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


# `input: slice`를 선언해야 하는 프롬프트 — `SOURCE`를 슬라이스로 정의한 v3 Scout 셋이다
# (ADR 0012 결정 1 · 각 `prefix.md`의 「SOURCE」 정의).
# ⏵ 2026-10-07 — 이 셋은 **디스크에 있고 아무도 안 고른다**(PR #524로 Scout가 v4 = `input: source`에
# 배선됐다). 그래서 아래 단언은 서빙 상태가 아니라 **선언의 집합**을 고정한다 — 가드가 보는 것이
# 그 선언이고, 그것을 고르는 설정이 생기면 가드가 다시 찬다(`cli._slice_input_prompts`).
SLICE_INPUT_PROMPTS = {"scout_memory/v3", "scout_boundary/v3", "scout_injection/v3"}


def test_only_the_v3_scout_prompts_declare_slice_input() -> None:
    """`input: slice`를 선언한 프롬프트는 v3 Scout 셋뿐이고, 나머지는 전부 원본 소스다.

    `cli.run`의 슬라이스 가드가 보는 것이 이 선언 하나다 — `--slices` 없이 슬라이스 입력
    프롬프트를 부르는 run을 시작 전에 거부한다(ADR 0012 결정 1). **선언이 새면 가드가 조용히
    꺼진다**: meta 키를 `inputs:`로 잘못 적으면 로더는 기본값(`source`)으로 읽고, v3 Scout가
    슬라이스 없이 심볼 본문을 받는 run이 오류 없이 다시 돈다. 그래서 있는지가 아니라
    **집합이 같은지**를 본다 — 반대쪽(원본 소스 프롬프트가 슬라이스를 선언해 dev run까지
    막는 것)도 같은 단언이 잡는다.
    """
    declared = {
        f"{d.parent.name}/{d.name}": load_prompt(Role(d.parent.name), d.name).input
        for d in _prompt_dirs()
    }

    assert {key for key, kind in declared.items() if kind == "slice"} == SLICE_INPUT_PROMPTS
    assert {kind for key, kind in declared.items() if key not in SLICE_INPUT_PROMPTS} == {
        "source"
    }


def test_an_unknown_input_kind_is_refused_at_load(tmp_path: Path) -> None:
    """`input`은 닫힌 두 값(`source`·`slice`)이다. 모르는 값은 로드에서 죽는다.

    기본값으로 읽어 넘기면 오타 하나(`slices`)가 슬라이스 가드를 조용히 끈다 — 바로 위
    검사가 키 쪽에서 막는 실패가 값 쪽에서 난다. 복사본 meta에 그 오타를 붙인다.
    """
    copy = tmp_path / "scout" / "v1"
    shutil.copytree(PROMPTS_DIR / "scout" / "v1", copy)
    meta = copy / "meta.yaml"
    meta.write_text(meta.read_text(encoding="utf-8") + "input: slices\n", encoding="utf-8")

    with pytest.raises(PromptError, match="'slices'"):
        load_prompt(Role.SCOUT, "v1", root=tmp_path)


@pytest.mark.parametrize(
    ("read_key", "typo"),
    [
        # 로더가 읽는 키 넷의 오타 — 앞의 셋은 이 변경 **전부터** 있던 구멍이다(키를 못 읽고
        # 기본값으로 가서 값 검사에 닿지도 않았다). 넷째(`hop_header`)는 pov v4가 더했다.
        ("input", "inputs"),
        ("source_options", "source_option"),
        ("tools", "toosl"),
        ("hop_header", "hop_heder"),
    ],
)
def test_a_misspelled_meta_key_is_refused_at_load(
    tmp_path: Path, read_key: str, typo: str
) -> None:
    """🔴 메타 **키**의 오타는 값 검사에 닿지 않는다 — 로더가 그 키를 못 읽고 기본값(`input: source`
    · `tools: []` · `hop_header: false`)으로 가서 가드가 **조용히** 꺼졌고, `cli doctor`는 그래도
    `[OK]`를 찍었다. 위 `test_only_the_v3_scout_prompts_declare_slice_input`이 말하는 `inputs:`가
    그 실물이다. 로드에서 죽어야 한다.

    복사본(pov/v4)에 오타 키를 붙인다. pov/v4가 `hop_header`를 이미 들고 있으므로 그 키는 이름을
    **바꿔** 읽을 키가 사라지는 경우(`hop_header: true` → `hop_heder: true`)를 시험한다."""
    copy = tmp_path / "pov" / "v4"
    shutil.copytree(PROMPTS_DIR / "pov" / "v4", copy)
    meta = copy / "meta.yaml"
    text = meta.read_text(encoding="utf-8")
    if read_key == "hop_header":
        assert "hop_header: true" in text  # 전제 — 안 그러면 아래 치환이 아무것도 안 바꾼다
        text = text.replace("hop_header: true", f"{typo}: true")
    else:
        text += f"{typo}: []\n"
    meta.write_text(text, encoding="utf-8")

    with pytest.raises(PromptError, match=f"모르는 키 .*{typo}"):
        load_prompt(Role.POV, "v4", root=tmp_path)


def _v4_meta_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "pov" / "v4"
    shutil.copytree(PROMPTS_DIR / "pov" / "v4", copy)
    return copy / "meta.yaml"


@pytest.mark.parametrize(
    ("key", "first", "second"),
    [
        # 거절돼야 할 값이 **앞**이고 통과하는 값이 **뒤**다 - PyYAML 기본은 뒤가 앞을 덮는다.
        ("hop_header", "hop_header: true", "hop_header: false"),
        ("tools", "tools: [gdb]", "tools: []"),
    ],
)
def test_a_duplicated_meta_key_is_refused_at_load(
    tmp_path: Path, key: str, first: str, second: str
) -> None:
    """🔴 같은 키를 두 번 쓰면 PyYAML는 **뒤의 값을 조용히 쓴다** - `hop_header: true` 뒤의
    `hop_header: false`가 홉 줄을 끄고(`tools: [gdb]` 뒤의 `tools: []`는 광고를 끈다) 값 검사는
    뒤의 값만 본다. 키 이름 오타를 닫은 `META_KEYS`(위 테스트)가 못 닫는 마지막 자리였다 - 오염
    변형 열여덟 가지(대소문자 · 키릴 유사 문자 · 끝 공백 · `on:` · 정수·null 키 · 빈 문서 · 목록
    문서 …) 중 이것만 로드를 통과했다."""
    meta = _v4_meta_copy(tmp_path)
    text = meta.read_text(encoding="utf-8")
    if key == "hop_header":
        assert "hop_header: true" in text  # 전제 - 안 그러면 치환이 아무것도 안 바꾼다
        text = text.replace("hop_header: true", f"{first}\n{second}", 1)
    else:
        text += f"{first}\n{second}\n"
    meta.write_text(text, encoding="utf-8")

    with pytest.raises(PromptError, match=f"같은 키 '{key}'"):
        load_prompt(Role.POV, "v4", root=tmp_path)


def test_unknown_meta_keys_of_mixed_types_are_a_prompt_error_not_a_type_error(
    tmp_path: Path,
) -> None:
    """정수·실수·null·문자열 키가 섞여도 `PromptError`다 - 목록을 정렬하기 전에 문자열로 바꾼다
    (섞인 형을 그대로 `sorted`에 넣으면 `TypeError`가 나 doctor가 ERROR 줄 없이 죽는다)."""
    meta = _v4_meta_copy(tmp_path)
    mixed = "1: a\n2.5: b\nfoo: c\n? null\n: d\n"  # 정수 · 실수 · 문자열 · null 키
    meta.write_text(meta.read_text(encoding="utf-8") + mixed, encoding="utf-8")

    with pytest.raises(PromptError, match="모르는 키 .*'1'.*'2.5'.*'None'.*'foo'"):
        load_prompt(Role.POV, "v4", root=tmp_path)


def test_every_key_on_disk_is_a_known_meta_key() -> None:
    """디스크의 모든 role 프롬프트 meta가 `META_KEYS` 안의 키만 든다 — 키를 닫은 일이 기존 판을
    깨지 않는다(`test_every_prompt_version_on_disk_loads`가 로드로 같은 것을 보지만 실패 메시지가
    판 하나씩이라, 어느 키가 목록에 빠졌는지는 여기서 한꺼번에 읽힌다)."""
    on_disk = {
        key
        for directory in _prompt_dirs()
        for key in yaml.safe_load((directory / "meta.yaml").read_text(encoding="utf-8"))
    }

    assert on_disk <= set(META_KEYS), sorted(on_disk - set(META_KEYS))
    # 로더가 읽는 키 일곱은 목록의 앞 일곱이다 — 목록에서 읽는 키를 빼면 그 키를 쓰는 판이 죽는다.
    read_by_the_loader = {
        "role", "version", "output_schema", "input", "source_options", "tools", "hop_header",
    }
    assert read_by_the_loader <= set(META_KEYS)


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

    ⚠ **2026-09-25 — 첫 문단의 「첫 모델 호출에서 처음 열린다」는 이제 `--slices`를 준 run에만
    맞다.** 모델에 닿는 run이 `--slices` 없이 시작하면 `cli.run`의 슬라이스 가드가 시작 전에
    프로필의 프롬프트를 전부 열고, 로드 실패도 거기서 exit 2다. 풀 프로필의 run은 `--slices`를
    줘야 서므로 그 run에서는 여전히 첫 호출에서 연다 — 이 검사는 그대로 필요하다.
    """
    config = load_config(profile)

    for role in PROMPTED_ROLES:
        if role not in config.profile.roles:
            continue
        version = config.model_for(role).prompt_template_version
        assert load_prompt(Role(role), version).version == version


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_canonical_auditor_selects_prompt_v14(profile: str) -> None:
    """§1 정본 — `harness_pool`·`_hybrid`의 auditor(`qwen3.8-27b`)가 `v14`를 고른다. 되돌리면
    이 핀이 빨개진다.

    판 이력: v1 → v3(2026-09-29, #256 항목 4) → v4(#328 머지 뒤) → **v6**(2026-10-03 ·
    프롬프트 #418 · 배선 #421). v5는 건너뛴다 — v5 배선 PR(#390)이 대여 세션 측정까지
    주차돼 있었고 v6가 v5에서 출발했다(`2bb374d`). **사용자 결정 2026-10-03이고 측정 결과가
    아니다** — v6는 v5의 범위 제약 둘을 삭제했으므로 v5의 상위집합이 아니다(`config/models.yaml`의
    그 항목이 삭제 목록과 #418이 든 수를 적는다). ⏵ **v7**(2026-10-04 · #349) = v6 + Auditor SOURCE
    옵션 넷의 서술. v6를 그 자리에서 고치지 않은 것은 #418의 수가 v6 문장의 것이라서다
    (agent-contracts §4 규칙 1 — PR #443 리뷰 HIGH). ⏵ **v8**(2026-10-04 · #419) = v7 + 도구
    루프 — 매 홉 `AuditorAction`이고 `SOURCE` 서술·trigger rule은 v7과 바이트 동일이다
    (`tests/test_auditor_prompt_v8.py`). v7을 그 자리에서 고치지 않은 것은 v7이
    `output_schema: Finding`으로 선 판이기 때문이다(같은 규칙 1). ⏵ **v9**(2026-10-06) = v8 +
    `auditor_guards` 서술 한 항목 — 켠 옵션을 meta가 선언해야 run이 서므로 v8을 안 고치고 올렸다.
    ⏵ **v10**(2026-10-06) = v7에서 **전면 재작성**한 판이고 출력 계약이 바뀐다 — 매 홉 봉투가
    `AuditorProcedureAction`이고 결과는 그 `procedure`에 드는 `AuditProcedure`다(새 `audit` 블록
    하나 + 바깥 필드는 `Finding`과 거의 1:1). 발급 ID 셋과 `state`·`missing_evidence`·
    `produced_by`를 **모델에게 묻지 않고** 하네스가 붙인다. v8·v9를 그 자리에서 고치지
    않은 것은 그 둘이
    `output_schema: AuditorAction`으로 선 판이기 때문이다(같은 규칙 1). ⏵ **v11**(2026-10-09 ·
    이슈 #525 선택지 1) = v10 + no_signal의 `cwe`를 null로 내라는 서술 — 봉투는 형제
    `AuditorProcedureNullableCweAction`이다. v10을 그 자리에서 고치지 않은 것은 v10의 요청 바이트가
    `AuditorProcedureAction`의 JSON 스키마라서다(같은 규칙 1). ⏵ **v13**(2026-10-10 · 이슈 #67) =
    v11 + FACTS에서 `source` 사본을 뺀다(meta `facts_source: false`) — 원문은 산문 SOURCE 블록
    하나로 간다. v12는 열린 PR #580이 쓰는 번호라 건너뛰었다. v11을 그 자리에서 고치지 않은 것은
    FACTS가 v11의 요청 바이트라서다(같은 규칙 1). ⏵ **v14**(2026-10-11 · 이슈 #67 · #578) = v12 +
    같은 FACTS 변경 — v13은 봉투가 v11 것이라 v12 계약인 FT 정답과 어긋났다. v13을 그 자리에서
    고치지 않은 것은 같은 규칙 1이다."""
    config = load_config(profile)

    assert config.model_for("auditor").prompt_template_version == "v14"


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_canonical_auditor_version_receives_evidence_bodies(profile: str) -> None:
    """위 핀은 철자다. 막으려는 성질은 **registry가 고른 Auditor 판이 근거 본문을 받는다**이다 —
    `_CONTENT_AWARE_VERSIONS`에 없는 판을 고르면 FACTS에 id·kind·ok만 실려 툴 로그 본문이 조용히
    빠진다(#328 리뷰 MEDIUM-1). 판을 바꾸면서 그 집합을 안 고치면 여기서 빨개진다."""
    config = load_config(profile)

    version = config.model_for("auditor").prompt_template_version
    assert version in auditor_node._CONTENT_AWARE_VERSIONS


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_canonical_auditor_prompt_states_the_runner_source_contract(profile: str) -> None:
    """서빙 판의 입력 계약 철자가 러너 상수와 갈리지 않는다 — **판을 세지 않고 묻는다.**

    `test_slice_targets.py`의 같은 검사와 `test_nodes.py`의 v5 문구 핀은 판 목록이나 경로의
    `"v5"`로 대상을 고른다. 그 축은 **판을 올릴 때마다 목록을 같이 늘려야** 하고, 안 늘리면
    검사가 사라진 것이 아니라 **안 서는 판만 지킨다**.

    **사고 —** 2026-10-03 v4→v6 배선(#418·#421)에서 그것이 났다. 세 핀이 전부 `v4`·`v5`에 걸려 있어
    **서빙 판 v6가 어느 판본 핀에도 안 걸렸고**, 그 사이 v6의 규칙 3 문장이 이미 갈려 있었다
    (`establish it,use` — 공백 하나가 빠져 아래 연속 구가 안 맞았다). CI는 초록이었다.

    여기서 고르는 것은 `prompt_template_version`이라 v7로 올려도 같이 돈다. 묻는 것은 철자다 —
    모델이 그 계약을 따르는지는 고정 표본 회귀의 몫이다(agent-contracts §4 규칙 5).
    """
    config = load_config(profile)
    version = config.model_for("auditor").prompt_template_version
    prefix = (PROMPTS_DIR / "auditor" / version / "prefix.md").read_text(encoding="utf-8")
    flat = " ".join(prefix.split())
    thresholds = config.thresholds

    # 러너 상수 ↔ 계약 수치(`runner._auditor_target`·`_auditor_source`).
    assert f"{_AUDITOR_WINDOW_LINES} lines before and after" in prefix
    assert "`(file, start line)` order" in prefix
    # 상한 -> 토큰 상한이 켜져 있으면 그 값, 아니면 글자 상한. 다른 쪽 철자가 남으면 모델이
    # 두 상한을 같이 읽는다(2026-10-03 옵션 넷을 켬 · 그 서술은 v7 = v6 + 서술, 2026-10-04 · #349).
    if thresholds.auditor_source_tokens:
        assert f"{thresholds.auditor_source_tokens:,}-token cap" in prefix
        assert f"{_AUDITOR_SOURCE_CHARS:,}-character" not in prefix
    else:
        assert f"{_AUDITOR_SOURCE_CHARS:,}-character cap" in prefix
    # 켜진 옵션마다 그 블록의 철자 -- 상한은 배너 줄을 포함한 블록 길이다(`len(block)`, #374 리뷰).
    if thresholds.auditor_definitions:
        assert (
            f"its block, counting its banner line, is at most {_AUDITOR_DEFINITION_CHARS:,} "
            "characters" in flat
        )
        if thresholds.auditor_callee_wrappers:
            assert f"up to {_AUDITOR_ALIAS_ROUNDS} renaming steps" in flat  # 매크로 별칭(C4)
        # 큰 enum은 그 상수 앞뒤만 — 줄 수가 러너 상수와 같아야 한다
        from src.orchestrator.runner import _AUDITOR_ENUM_CONTEXT_LINES

        assert f"the {_AUDITOR_ENUM_CONTEXT_LINES} lines before and after that constant" in flat
    if thresholds.auditor_callee_hops:
        assert (
            f"a callee block, counting its banner line, over {_AUDITOR_CALLEE_CHARS:,} characters"
            " is skipped" in flat
        )
    # 래퍼 너머(C4 · `auditor_callee_wrappers`) — 「one or two」가
    # `_AUDITOR_WRAPPER_CALLS`의 철자다.
    if thresholds.auditor_callee_hops and thresholds.auditor_callee_wrappers:
        assert _AUDITOR_WRAPPER_CALLS == 2
        assert (
            f"a short wrapper (at most {_AUDITOR_WRAPPER_LINES} lines, calling one or two "
            "functions the index can resolve)" in flat
        )
        assert f"up to {_AUDITOR_WRAPPER_DEPTH} further levels" in flat
    if thresholds.auditor_indirect_callees:  # 간접 피호출(C6 · #304)
        assert f"resolve to at most {_AUDITOR_INDIRECT_MAX} candidate functions" in flat
    if thresholds.auditor_write_sites:  # 쓰기 지점 창(C5(a) · #304)
        assert (
            f"the {_AUDITOR_WRITE_LINES} lines before and after the write, cut to the writing "
            f"function's own range, at most {thresholds.auditor_write_sites} windows" in flat
        )
    if thresholds.auditor_caller_lines:
        assert f"the {thresholds.auditor_caller_lines} lines before and after the call" in flat
        assert f"at most {_AUDITOR_CALLER_SITES} call sites are read" in flat
    # 규칙 3의 판정 **방향** — 「otherwise decide」를 「defer」로 바꾸는 한 단어 변이를 막는다.
    # ⏵ 2026-10-06 — v9까지 이 핀은 한 문장이었고, 그 문장이 **모델에게** 발급
    # missing-evidence ID를 쓰라고 시켰다. v10은 `missing_evidence`를 모델 출력에서 빼고
    # 하네스가 붙인다(`result`가 `decisive_code_missing`이거나 `trigger_status`가
    # `reachability_unknown`일 때 —
    # `nodes/auditor`의 변환). 옛 철자를 그대로 묶으면 **거짓이 된 문장을 묶는다.**
    # 그래서 명제를 두 구로 쪼개 묶고, 「모델이 그 ID를 안 낸다」를 셋째 핀으로 더한다.
    # ⚠ 핀은 **배선된 판의 철자**다(이 검사의 설계) — 판을 되돌리면 철자도 같이 본다.
    assert "otherwise decide from the supplied material" in flat
    assert "Truncation alone is not a reason to defer" in flat
    assert (
        "Do not output `finding_id`, `run_id`, `candidate_id`, `state`, `missing_evidence`, "
        "or `produced_by`" in flat
    )
    # 근거 본문·절단 계약 — **`_CONTENT_AWARE_VERSIONS` 자격의 근거가 이 문구들이다**(#328 리뷰
    # MEDIUM-1). 뒤집히면 집합에 든 채로 그 전제가 거짓이 된다. 목록은 `test_nodes.py`의
    # `test_v5_evidence_completeness_wording`과 같고, 그쪽은 경로에 v5를 하드코딩한다.
    assert "factual summary" not in flat
    for text in (
        "not a generated summary",
        "`truncated` is false",
        "line containing only `...`",
        r"`\n...\n`",
        "stop mid-line",
        "not adjacent",
        "without that marker",
        "may also occur in the original log",
        "not a clean result",
    ):
        assert text in flat


# meta.yaml 머리 주석이 「이 판은 어느 registry 엔트리도 안 고른다」를 적는 철자.
# **저장소 실물에서 온 것만 적는다**(양성 핀 — 출처는 같은 자리):
_UNSELECTED_CLAIM_SPELLINGS = (
    "미배선(disk-only)",  # prompts/critic/v4/meta.yaml:1
    "아직 미활성",  # prompts/critic/v3/meta.yaml:1
    "어느 registry 엔트리도 이 버전을 고르지 않는다",  # prompts/auditor/v3/meta.yaml:5-6
)


def _fold(raw: str) -> str:
    """meta.yaml 본문을 한 문자열로 접는다 — 주석 줄바꿈·공백 런은 한 칸으로, 굵게(`**`)와
    백틱은 뺀다. 구절이 주석 줄바꿈으로 갈리는 실물(auditor/v3 meta)이 있고, meta 머리 주석은
    굵게·백틱을 쓰는 문체다(critic/v1 meta:1이 한 줄에 둘 다 든다)."""
    folded = " ".join(line.lstrip("# ").strip() for line in raw.splitlines())
    return " ".join(folded.replace("**", "").replace("`", "").split())


def _meta_text(role: str, version: str) -> str:
    """meta.yaml 본문을 `_fold`로 접은 것."""
    return _fold((PROMPTS_DIR / role / version / "meta.yaml").read_text(encoding="utf-8"))


def _prompts_whose_meta_claims_unselected(config: HarnessConfig) -> list[str]:
    """`config`의 각 역할이 고른 판 중 meta.yaml이 「안 고른다」고 적은 것 — `role/version` 목록."""
    return [
        f"{role}/{version}"
        for role in PROMPTED_ROLES
        if role in config.profile.roles
        for version in [config.model_for(role).prompt_template_version]
        if any(spelling in _meta_text(role, version) for spelling in _UNSELECTED_CLAIM_SPELLINGS)
    ]


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_no_profile_selects_a_prompt_whose_meta_says_disk_only(profile: str) -> None:
    """`meta.yaml` 머리 주석의 「안 고른다」(미배선·미활성 등)는 **어느 registry 엔트리도 이 판을
    안 고른다**는 현재형 단언이다. 판을 배선하면서 그 줄을 안 고치면 산문이 거짓이 된다
    (CLAUDE.md §2.3) — 이 검사가 그 쪽을 잡는다. 반대 방향(안 고르는데 「배선됨」)은 잡지 않는다.
    ⚠ 철자 pin이다 — `_UNSELECTED_CLAIM_SPELLINGS`의 세 철자(저장소 실물: critic/v4·critic/v3·
    auditor/v3 meta)만 문다. 다른 표현으로 쓰면 못 잡는다. 저장소 config만 돌리므로 어느
    프로필도 안 고르는 판의 철자는 여기서 시험되지 않는다 — 판독기가 철자를 읽는지는 아래
    검사들이 고정한다(#341 L-1)."""
    assert _prompts_whose_meta_claims_unselected(load_config(profile)) == []


@pytest.mark.parametrize(
    ("role", "model_key", "version", "expected"),
    [
        # 통제 — prompts/critic/v4/meta.yaml:1. 첫째·셋째 철자를 함께 들어 여기서는 철자를
        # 못 가른다 — 철자별로는 `test_the_known_metas_…`가 본다.
        ("critic", "gpt-oss-20b", "v4", ["critic/v4"]),
        # 실물 형제 문형 — prompts/critic/v3/meta.yaml:1 「아직 미활성: v3 자체는 미배선이다」.
        ("critic", "gpt-oss-20b", "v3", ["critic/v3"]),
        # 실물 — prompts/critic/v7/meta.yaml:1-2가 첫째·셋째 철자를 함께 든다(critic/v4 모양).
        ("critic", "gpt-oss-20b", "v7", ["critic/v7"]),
        # 실물 형제 문형 — prompts/auditor/v3/meta.yaml:5-6 「지금은 어느 registry 엔트리도
        # 이 버전을 고르지 않는다」. 구절이 주석 줄바꿈으로 갈리니 줄바꿈을 접고 읽어야 한다.
        ("auditor", "qwen3.8-27b", "v3", ["auditor/v3"]),
        # 실물 형제 문형 — prompts/auditor/v4/meta.yaml:4-5 「… 어느 registry 엔트리도 이 버전을
        # 고르지 / 않는다」. v3와 같이 구절이 주석 줄바꿈으로 갈린다(`:4` 끝이 「고르지」다).
        # v6 배선(2026-10-03)으로 v4가 미선택이 되며 달았고, 그 전에는 이 행이 음성 통제였다.
        ("auditor", "qwen3.8-27b", "v4", ["auditor/v4"]),
        # 실물 형제 문형 — prompts/auditor/v6/meta.yaml:6-7 「… 어느 / registry 엔트리도 이 버전을
        # 고르지 않는다」. v7 배선(2026-10-04)으로 미선택이 되며 달았고, 그 전에는 이 행이
        # 음성 통제였다.
        ("auditor", "qwen3.8-27b", "v6", ["auditor/v6"]),
        # 실물 형제 문형 — prompts/auditor/v7/meta.yaml 마지막 ⏵ 「… 지금은 어느 / registry
        # 엔트리도 이 버전을 고르지 않는다」. v8 배선(2026-10-04 · PR #491)으로 미선택이 되며
        # 달았고, 그 전에는 이 행이 음성 통제였다. v6처럼 구절이 주석 줄바꿈으로 갈린다.
        ("auditor", "qwen3.8-27b", "v7", ["auditor/v7"]),
        # 실물 형제 문형 — prompts/auditor/v8/meta.yaml 머리의 ⏵ 「… 지금은 어느 registry
        # 엔트리도 이 버전을 고르지 않는다」. v9 배선(2026-10-06)으로 미선택이 되며 달았다.
        ("auditor", "qwen3.8-27b", "v8", ["auditor/v8"]),
        # 실물 형제 문형 — prompts/auditor/v9/meta.yaml 머리의 ⏵ 「… 지금은 어느 registry
        # 엔트리도 이 버전을 / 고르지 않는다」. v10 배선(2026-10-07)으로 미선택이 되며
        # 달았고, 그 전에는 이 행이 음성 통제였다. v8처럼 구절이 주석 줄바꿈으로 갈린다.
        ("auditor", "qwen3.8-27b", "v9", ["auditor/v9"]),
        # 실물 형제 문형 — prompts/auditor/v10/meta.yaml의 ⏵ 「… 지금은 어느 registry 엔트리도
        # 이 버전을 고르지 않는다」. v11 배선(2026-10-09 · 이슈 #525)으로 미선택이 되며 달았고, 그
        # 전에는 이 행이 음성 통제였다. 구절이 한 줄에 든다.
        ("auditor", "qwen3.8-27b", "v10", ["auditor/v10"]),
        # 실물 형제 문형 — prompts/auditor/v11/meta.yaml의 ⏵ 「… 지금은 어느 registry 엔트리도
        # 이 버전을 고르지 않는다」. v13 배선(2026-10-10 · 이슈 #67)으로 미선택이 되며 달았고, 그
        # 전에는 이 행이 음성 통제였다. 구절이 한 줄에 든다.
        ("auditor", "qwen3.8-27b", "v11", ["auditor/v11"]),
        # 실물 형제 문형 — prompts/auditor/v13/meta.yaml 머리의 ⏵ 「… 지금은 어느 registry 엔트리도
        # 이 버전을 고르지 않는다」. v14 배선(2026-10-11 · 이슈 #67)으로 미선택이 되며 달았고, 그
        # 전에는 이 행이 음성 통제였다. 구절이 한 줄에 든다.
        ("auditor", "qwen3.8-27b", "v13", ["auditor/v13"]),
        # 음성 통제 — prompts/auditor/v14/meta.yaml:1은 배선된 판의 문형으로 적는다.
        # **이 문형은 실재한다**(서빙 판의 머리 주석 형식이고 v13도 그날까지 서빙 판이었다) —
        # 판독기가 그것을 「안 고른다」로 잘못 읽으면 서빙 판이 미배선으로 보고돼 여기서 걸린다.
        ("auditor", "qwen3.8-27b", "v14", []),
        # 실물 형제 문형 — prompts/auditor/v12/meta.yaml:3 「지금은 어느 registry 엔트리도 이 버전을
        # 고르지 않는다」. v12(2026-10-10 · 이슈 #578)는 처음부터 미선택 판으로 섰다. 구절이 주석
        # 줄바꿈으로 안 갈린다.
        ("auditor", "qwen3.8-27b", "v12", ["auditor/v12"]),
        # 실물 형제 문형 — prompts/pov/v3/meta.yaml 머리의 ⏵ 「… 지금은 어느 registry 엔트리도 이
        # 버전을 고르지 않는다」. pov v4 배선(2026-10-08)으로 미선택이 되며 달았고, 그 전에는
        # 이 판이 서빙 판이었다. 구절이 한 줄에 든다(주석 줄바꿈이 가르지 않는다).
        ("pov", "feyospace-v1-small", "v3", ["pov/v3"]),
        # 음성 통제 — prompts/pov/v4/meta.yaml:1은 배선된 판의 문형(「…에 배선됐다」)으로 적는다.
        # 같은 이유로 실재하는 문형이다 — v3도 2026-10-08까지 그 문형이었다.
        ("pov", "feyospace-v1-small", "v4", []),
    ],
)
def test_the_unselected_claim_reader_flags_the_known_unselected_metas(
    tmp_path: Path, role: str, model_key: str, version: str, expected: list[str]
) -> None:
    """판을 **실제로 고르게 만든 합성 config**에서 위 검사의 판독기가 그 판의 「안 고른다」를
    읽어 내는가. `test_no_profile_selects_…`는 저장소 config만 돌리므로, 어느 프로필도 안
    고르는 판의 철자를 판독기가 못 읽어도 거기서는 드러나지 않는다(#341 L-1). ⚠ 목록에 든
    meta 여덟만 시험한다(⏵ 2026-10-04 v8 배선으로 v7이 음성 통제에서 양성으로 옮겨가고 v8이
    새 음성 통제가 되어 일곱 · ⏵ 2026-10-07 v10 배선으로 v9가 같은 식으로 옮겨가고 v10이 새
    음성 통제가 되어 여덟 · ⏵ 2026-10-08 pov v4 배선으로 pov/v3가 양성으로 더해지고 pov/v4가 둘째
    음성 통제가 되어 아홉 · ⏵ 2026-10-09 v11 배선으로 v10이 양성으로 옮겨가고 v11이 새 음성 통제가
    되어 열 · ⏵ 2026-10-10 critic/v7(미배선)과 미선택 판 v12가 양성으로 더해져 열둘 ·
    ⏵ 같은 날 v13 배선으로 v11이 양성으로 옮겨가고 v13이 새 음성 통제가 되어 열셋 ·
    ⏵ 2026-10-11 v14 배선으로 v13이 양성으로 옮겨가고 v14가 새 음성 통제가 되어 열넷) —
    새 철자로 적은 meta는 목록에 넣어야 잡힌다."""
    roles = {"scout": "fake", "auditor": "fake", "critic": "fake", role: model_key}
    _copy_config(
        tmp_path,
        profile_patch={"roles": roles},
        model_patch={model_key: {"prompt_template_version": version}},
    )
    config = load_config("dev_fake", config_dir=tmp_path)

    assert config.model_for(role).prompt_template_version == version  # 변이가 실제로 들어갔다
    stale = [s for s in _prompts_whose_meta_claims_unselected(config) if s.startswith(f"{role}/")]
    assert stale == expected


@pytest.mark.parametrize(
    ("role", "version", "expected"),
    [
        # prompts/critic/v4/meta.yaml:1 — 첫째·셋째 철자를 한 줄에 함께 든다.
        ("critic", "v4", {"미배선(disk-only)", "어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/critic/v3/meta.yaml:1
        ("critic", "v3", {"아직 미활성"}),
        # prompts/critic/v7/meta.yaml:1-2 — critic/v4처럼 첫째·셋째 철자를 함께 든다(미배선 판).
        ("critic", "v7", {"미배선(disk-only)", "어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/auditor/v3/meta.yaml:5-6 — 셋째 철자가 주석 줄바꿈으로 갈린다.
        ("auditor", "v3", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/auditor/v4/meta.yaml:4-5 — v6 배선으로 미선택이 되어 v3와 같은 철자를 쓰고,
        # v3처럼 구절이 주석 줄바꿈으로 갈린다.
        ("auditor", "v4", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/auditor/v6/meta.yaml:6-7 — v7 배선으로 미선택이 되어 v4와 같은 철자를 쓰고,
        # 구절이 주석 줄바꿈으로 갈린다(`:6` 끝이 「어느」다).
        ("auditor", "v6", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/auditor/v7/meta.yaml 마지막 ⏵ — v8 배선으로 미선택이 되어 v6와 같은 철자를
        # 쓰고, 구절이 주석 줄바꿈으로 갈린다(그 줄 끝이 「어느」다).
        ("auditor", "v7", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/auditor/v8/meta.yaml 머리의 ⏵ — v9 배선으로 미선택이 되어 v7과 같은 철자를
        # 쓰고, 구절이 주석 줄바꿈으로 갈린다(그 줄 끝이 「어느」다).
        ("auditor", "v8", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/auditor/v9/meta.yaml 머리의 ⏵ — v10 배선으로 미선택이 되어 v8과 같은 철자를
        # 쓰고, 구절이 주석 줄바꿈으로 갈린다(그 줄 끝이 「이 버전을」이다).
        ("auditor", "v9", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/auditor/v10/meta.yaml의 ⏵ — v11 배선(2026-10-09)으로 미선택이 되어 v9와 같은
        # 철자를 쓴다. 구절이 한 줄에 든다.
        ("auditor", "v10", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/auditor/v11/meta.yaml의 ⏵ — v13 배선(2026-10-10)으로 미선택이 되어 v10과 같은
        # 철자를 쓴다. 구절이 한 줄에 든다.
        ("auditor", "v11", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/auditor/v13/meta.yaml 머리의 ⏵ — v14 배선(2026-10-11)으로 미선택이 되어 v11과 같은
        # 철자를 쓴다. 구절이 한 줄에 든다.
        ("auditor", "v13", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # 음성 통제 — prompts/auditor/v14/meta.yaml:1은 배선된 판의 문형으로 적는다(실재하는 문형).
        ("auditor", "v14", set()),
        # prompts/auditor/v12/meta.yaml:3 — 미선택 판으로 섰다(FT Auditor 서빙 때 고를 판).
        ("auditor", "v12", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # prompts/pov/v3/meta.yaml 머리의 ⏵ — pov v4 배선으로 미선택이 되어 셋째 철자를 쓴다.
        ("pov", "v3", {"어느 registry 엔트리도 이 버전을 고르지 않는다"}),
        # 음성 통제 — prompts/pov/v4/meta.yaml:1은 배선된 판의 문형이다(실재하는 문형).
        ("pov", "v4", set()),
    ],
)
def test_the_known_metas_match_exactly_these_claim_spellings(
    role: str, version: str, expected: set[str]
) -> None:
    """각 meta가 **어느 철자에** 걸리는지를 철자 단위로 단언한다. 판독기 결과(걸렸나)만 보면
    한 meta가 철자를 둘 들 때 하나가 깨져도 다른 하나가 대신 잡아 드러나지 않는다 —
    critic/v4 meta가 첫째·셋째 철자를 함께 들어, 첫째 철자는 이 검사 없이는 홀로 고정되지
    않는다(#341 R2). ⚠ 이 열셋 meta의(⏵ 2026-10-04 v8을 더해 일곱 · ⏵ 2026-10-06 v10을 더해
    여덟 · ⏵ 2026-10-08 pov/v3를 더해 아홉 · ⏵ 2026-10-09 v11을 더해 열 ·
    ⏵ 2026-10-10 critic/v7과 v12를 더해 열둘 · ⏵ 같은 날 v13을 더해 열셋 · ⏵ 2026-10-11 v14를
    더해 열넷) 오늘 문구에 묶인다 —
    머리 주석을 고치면 기대값도 고친다."""
    assert {s for s in _UNSELECTED_CLAIM_SPELLINGS if s in _meta_text(role, version)} == expected


@pytest.mark.parametrize(
    "raw",
    [
        "# 어느 registry 엔트리도 이 버전을\n#\n# 고르지 않는다.\n",  # 셋째 철자 · 빈 주석 줄
        "# 어느 registry  엔트리도 이 버전을 고르지 않는다.\n",  # 셋째 · 공백 2칸
        "# 어느 registry 엔트리도 **이 버전을 고르지 않는다**.\n",  # 셋째 · 굵게
        "# **미배선**(disk-only): 이 판은 registry가 안 고른다.\n",  # 첫째 · 굵게
        "# 어느 registry 엔트리도 이 버전을 `고르지 않는다`.\n",  # 셋째 · 백틱
    ],
    ids=["blank-comment-line", "double-space", "bold", "bold-disk-only", "backtick"],
)
def test_the_meta_fold_reads_a_claim_through_comment_markup(raw: str) -> None:
    """접기가 빈 주석 줄·공백 런·굵게(`**`)·백틱을 넘어 철자를 읽어 내는가. meta 머리 주석은
    굵게·백틱을 쓰는 문체다 — 굵게와 백틱은 `prompts/critic/v1/meta.yaml:1`에 함께 있다.
    ⚠ 전에는 백틱 증인으로
    critic/v3의 `:7`을 들었는데 그 줄이 거짓 이력 블록이라 지워졌다 — 지금 critic/v3 머리에는
    백틱이 0개다. 다섯 형은 검토 변이
    B8·B9·B17·B18·B19에서 가져왔다(실물 meta의 형이 아니다) — 접기가 이것들을 못 넘으면 같은
    주장을 그 문체로 적었을 때 판독기가 못 본다(#341 R5)."""
    assert any(spelling in _fold(raw) for spelling in _UNSELECTED_CLAIM_SPELLINGS), _fold(raw)


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
    "scout_memory": "qwen3-14b",
    "scout_boundary": "seccoderx-8b",
    "scout_injection": "qwen3-14b",
    "auditor": "qwen3.8-27b",
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


def test_max_hops_reaches_the_run_record_and_is_provisional() -> None:
    """`max_hops`(ADR 0012 결정 6 — `capsule_expansion_max`를 대체)가 `flatten()`으로

    run 기록에 실리고, `capsule_expansion_max`와 같은 자리(명세값·미실측)로
    `PROVISIONAL_THRESHOLDS`에도 있다.
    """
    thresholds = load_config("dev_fake").thresholds
    assert thresholds.max_hops >= 0
    assert thresholds.flatten()["max_hops"] == float(thresholds.max_hops)
    assert "max_hops" in PROVISIONAL_THRESHOLDS


def test_max_hops_rejects_negative() -> None:
    thresholds = load_config("dev_fake").thresholds
    with pytest.raises(ValidationError):
        thresholds.transition(max_hops=-1)


def test_pov_tool_hops_reaches_the_run_record_and_is_provisional() -> None:
    """`pov_tool_hops`(8단계 PoV 툴 루프의 **전용** 상한 — `max_hops`에서 떼어냈다, 이슈 #511)가
    `flatten()`으로 run 기록에 실리고, 값이 미결정이라 `PROVISIONAL_THRESHOLDS`에도 있다."""
    thresholds = load_config("dev_fake").thresholds
    assert thresholds.pov_tool_hops >= 0
    assert thresholds.flatten()["pov_tool_hops"] == float(thresholds.pov_tool_hops)
    assert "pov_tool_hops" in PROVISIONAL_THRESHOLDS


def test_pov_tool_hops_rejects_negative() -> None:
    thresholds = load_config("dev_fake").thresholds
    with pytest.raises(ValidationError):
        thresholds.transition(pov_tool_hops=-1)


def test_pov_tool_hops_has_no_field_default() -> None:
    """값은 `config/budgets.yaml`만 든다. 필드 기본이 0이면 키가 빠진 설정이 PoV를 hop0(gen) 뒤에서
    조용히 세운다 — submit이 0건이던 증상(#511) 그대로다. `auditor_tool_hops`의 기본 0과 다른
    자리다: 그쪽 0은 「꺼짐 = 종전 경로」이고 여기 0은 「submit에 못 닿는다」다."""
    thresholds = load_config("dev_fake").thresholds
    assert type(thresholds).model_fields["pov_tool_hops"].is_required()


def test_a_budgets_file_without_pov_tool_hops_does_not_load(tmp_path: Path) -> None:
    """키가 빠지면 **로드가 터진다** — 적대적 검토 2026-10-06의 생존 변이.

    위 `is_required()` 핀은 스키마 메타데이터만 단언한다. 키 부재 시 `max_hops`를 복사해 채우는
    before-validator 변이가 **아무 테스트도 빨갛게 하지 않았다**(363 passed). 그러면 PoV 상한이
    Scout 상한을 조용히 따라간다 — 이 PR이 끊은 결합 그대로다. 성질로 무는 자리가 여기다.
    """
    _copy_config(tmp_path, profile_patch={})
    raw = yaml.safe_load((tmp_path / "budgets.yaml").read_text("utf-8"))
    del raw["thresholds"]["pov_tool_hops"]
    (tmp_path / "budgets.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8"
    )
    with pytest.raises((ConfigError, ValidationError)):
        load_config("dev_fake", config_dir=tmp_path)


def test_the_estimate_scout_calls_follow_max_hops_not_pov_tool_hops() -> None:
    """`cli estimate`의 scout 호출 상한은 `1 + max_hops`다 — PoV 상한을 올려도 안 움직인다(#511).

    통제가 헛돌지 않게 `max_hops`는 움직이는지도 본다."""
    config = load_config("dev_fake")
    base = _expected_calls(config, 4)

    def with_thresholds(**changes: int) -> HarnessConfig:
        return config.transition(thresholds=config.thresholds.transition(**changes))

    pov_hops = config.thresholds.pov_tool_hops
    assert _expected_calls(with_thresholds(pov_tool_hops=pov_hops + 7), 4) == base
    assert _expected_calls(with_thresholds(max_hops=config.thresholds.max_hops + 1), 4) > base


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

    ⏵ 2026-09-26 — 저장소가 처음으로 한 역할을 선언했다(Auditor 32,768 — 사고를 켰다). 그 뒤로
    이 검사는 **선언 안 한 역할**만 보고, 기록에는 선언한 역할의 키만 생기는지를 본다.
    """
    thresholds = load_config("harness_pool").thresholds
    declared = set(thresholds.output_max_by_role)
    undeclared = [role for role in PROMPTED_ROLES if role not in declared]
    assert undeclared, "전 역할이 선언해 이 검사가 볼 역할이 없다"

    assert all(thresholds.output_max_for(role) == thresholds.output_max for role in undeclared), (
        "선언이 없는데 역할마다 다른 값이 나온다"
    )
    recorded = {key for key in thresholds.flatten() if key.startswith("output_max_by_role")}
    assert recorded == {f"output_max_by_role.{role}" for role in declared}, (
        "선언 안 한 역할의 키가 기록에 생겼다 — 기존 manifest와 모양이 갈린다"
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
                "auditor": "qwen3.8-27b",
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
                "auditor": "qwen3.8-27b",
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
                "auditor": "qwen3.8-27b",
                "critic": "gpt-oss-20b",
            }
        },
        thresholds_patch={"output_max_by_role": {"critic": cap}},
    )

    with pytest.raises(ValidationError, match="양수"):
        load_config("dev_fake", config_dir=tmp_path)


def _harness_pool_with_role_caps(dest: Path, **caps: int) -> None:
    """`harness_pool`을 그대로 두고 저장소의 `output_max_by_role`에 `caps`만 덮어 쓴다."""
    declared = load_config("harness_pool").thresholds.output_max_by_role
    _copy_config(
        dest,
        profile="harness_pool",
        profile_patch={},
        thresholds_patch={"output_max_by_role": {**declared, **caps}},
    )


# 999,999 · 2**31은 이슈 #214 본문의 관측값이다(`load_config` 실측 — 둘 다 로드를 통과했다).
# 131,072는 `gpt-oss-20b`의 registry `context_limit` 그 자체 — 입력 몫이 0이라 모든 호출이 400이다.
@pytest.mark.parametrize("cap", [131072, 999999, 2**31])
def test_a_role_cap_at_or_above_its_model_window_fails_at_load(tmp_path: Path, cap: int) -> None:
    """출력 상한이 그 역할이 앉은 모델의 창 이상이면 **로드에서** 죽는다 (이슈 #214 (a)).

    vLLM은 `prompt_tokens + max_tokens > max_model_len`이면 400이다. 프롬프트는 최소 1토큰이라
    상한 ≥ 창이면 그 역할은 입력과 무관하게 전량 400이다 — 전엔 그 설정이 로드를 통과했다.
    """
    _harness_pool_with_role_caps(tmp_path, critic=cap)

    with pytest.raises(ConfigError, match=r"critic\(gpt-oss-20b\)"):
        load_config("harness_pool", config_dir=tmp_path)


def test_a_role_cap_one_below_the_window_still_loads(tmp_path: Path) -> None:
    """경계는 「창 이상」뿐이다 — 창보다 1 작으면 통과한다.

    입력 몫을 얼마 남겨야 하는지는 입력 크기에 달려 로드 때 정할 수 없고, 임의 여유를 두면 그
    수가 근거 없는 상수가 된다(이슈 #214 「닫히는 조건」의 ⚠). 이 검사가 더 엄격해지면 여기서 깬다.
    """
    _harness_pool_with_role_caps(tmp_path, critic=131072 - 1)

    config = load_config("harness_pool", config_dir=tmp_path)

    assert config.thresholds.output_max_for("critic") == 131071


def test_the_global_cap_is_checked_for_roles_that_do_not_override_it(tmp_path: Path) -> None:
    """역할별 선언이 없는 역할은 전역 `output_max`가 실효 상한이다 — 그 값도 같은 대조를 받는다.

    `harness_pool`의 Scout 셋은 역할값이 없고 창 40,960 모델(`qwen3-14b`·`seccoderx-8b`)에 앉는다.
    """
    _copy_config(
        tmp_path,
        profile="harness_pool",
        profile_patch={},
        thresholds_patch={"output_max": 40960},
    )

    with pytest.raises(ConfigError, match=r"scout_memory\(qwen3-14b\)"):
        load_config("harness_pool", config_dir=tmp_path)


def test_a_fake_role_is_not_held_to_the_window(tmp_path: Path) -> None:
    """Fake 좌석은 서버로 요청을 안 보내므로 400이 날 자리가 없다 — 대조하지 않는다.

    `dev_fake`의 Fake registry 창은 32,768이고 저장소 상한은 `critic` 32,768(동률)·`auditor`
    65,536(초과)이라, Fake까지 대조하면 저장소 그대로의 `dev_fake`가 로드에서 죽는다.
    """
    _copy_config(
        tmp_path,
        profile_patch={},
        thresholds_patch={"output_max_by_role": {"critic": 2**31}},
    )

    config = load_config("dev_fake", config_dir=tmp_path)

    assert config.served_models() == {}
    assert config.thresholds.output_max_for("critic") == 2**31


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
        # registry가 **실제로** `reasoning_effort: high`를 선언한 키 `gpt-oss-20b`를 auditor
        # 자리에, 선언 안 한 `qwen3-14b`를 critic 자리에 둔다(2026-09-26 전에는
        # `qwen3.6-35b-a3b`였다 — 그 자리의 새 키 `qwen3.8-27b`는 `xhigh`를 선언해 대조군이
        # 못 된다) — 패치 없이 registry
        # 그대로를 잰다(2026-09-24 전에는 옛 키 `qwen3-8b`에 패치로 선언시켰다).
        profile_patch={
            "roles": {
                "scout": "seccoderx-8b",
                "auditor": "gpt-oss-20b",
                "critic": "qwen3-14b",
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
                "critic": "qwen3-14b",
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
    profile: str = "dev_fake",
) -> None:
    """저장소 config를 tmp_path로 복사하고 `profile`(기본 dev_fake) 하나만 덮어쓴다.

    `model_patch`(model key → 덮어쓸 필드)를 주면 registry 항목도 함께 바꾼다. 그때만
    models.yaml이 yaml 왕복을 거치므로 **주석이 사라진다** — 주석이 살아 있어야 하는
    검사는 원문 치환을 쓴다(`test_partially_measured_models_are_named_with_what_is_missing`).

    `thresholds_patch`도 같은 규칙이다 — 주면 budgets.yaml이 왕복을 거친다. `output_max`
    처럼 **저장소에는 아직 선언이 없는 값**을 세울 때 쓴다. 저장소 값을 바꿔 재는 검사는
    "값이 실측으로 정해진다"는 이 파일 모듈 docstring의 전제를 깨므로 하지 않는다.

    **프로필의 예산은 `default`로 둔다.** 패치로 만든 프로필은 실서빙 역할을 쓸 수 있고(이 파일의
    합성 프로필 대부분이 그렇다), 실서빙 프로필은 상한 0을 못 쓴다 — `dev_fake` 예산을 그대로 두면
    로드가 `_check_real_serving_limits_are_positive`에서 죽는다. 예산은 이 검사들의 축이 아니다.
    `profile_patch`가 `budget`을 주면 그것이 이긴다.
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
    raw["profiles"][profile].update({"budget": "default", **profile_patch})
    (dest / "profiles.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")


# --- benchmark 세 상태 (2026-08-17) ------------------------------------------


def _bench(**values: float) -> Benchmark:
    return Benchmark(**values)


def test_benchmark_distinguishes_three_states() -> None:
    """전부 측정 / 부분 측정 / 미측정이 구분되어야 한다.

    `is_measured`가 `any`라 그것만으로는 **절반만 채운 모델이 production 게이트를 그냥
    통과한다.** ATOM에서 `mem_gb_ctx8k`를 nvidia-smi로 얻을 수 없다는 것이 확정됐으므로
    (2026-08-17) 부분 측정은 가정이 아니라 곧 실제로 생기는 상태다.
    ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340). vast에서 이 필드가 채워지는지는
    미실측이다(`src/config.py`의 `Benchmark.is_measured` 같은 ⏵).
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

    여기서 쓰는 쌍(`qwen3.8-27b` × `qwen3-14b`)은 registry에 실재하는 모델
    둘이고 키가 다르다 — 즉 지금 프로필에 그대로 적을 수 있는 조합이다. 그것이 이
    검사가 막아야 하는 실물이다. (2026-09-24 전에는 옛 키 `qwen3-coder-30b-a3b` × `qwen3-8b`였다.)
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "fake",
                "auditor": "qwen3.8-27b",
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
                "auditor": "qwen3.8-27b",
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
                "auditor": "qwen3.8-27b",  # family: qwen
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
                "scout": "qwen3.8-27b",
                "auditor": "qwen3.8-27b",
                "critic": "qwen3.8-27b",
            }
        },
    )
    config = load_config("dev_fake", config_dir=tmp_path)

    assert config.model_key_for("auditor") == config.model_key_for("critic")
    assert config.model_for("auditor").family == config.model_for("critic").family


# --- 실행 정책 (ADR 0009) ------------------------------------------------------


# 단계 단위로 도는 프로필 — 로스터 canonical 쌍(2026-09-24 @danielcho02).
PER_STAGE_PROFILES = frozenset({"harness_pool", "harness_pool_hybrid"})
# 그중 교대를 선언한 프로필(2026-09-29 — 교대 명령·장비 파일과 같이 왔다). hybrid는 검색 역할을
# 어느 단계에 띄울지가 ADR 0012 결정 3 뒤라 선언하지 않는다(`SwapPolicy`가 검색 역할을 거부한다).
SWAP_PROFILES = frozenset({"harness_pool"})


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_only_the_canonical_pool_runs_per_stage_and_only_harness_pool_swaps(profile: str) -> None:
    """실행 단위를 선언한 것은 canonical 풀 쌍뿐이고, **교대는 `harness_pool` 하나만 선언한다.**

    2026-09-24 전에는 `execution`을 선언한 프로필이 0이었다(ADR 0009 결과 1 — 스키마만 열고 값은
    안 박는다). 그날 @danielcho02가 로스터 canonical 배선(`harness_pool`·`_hybrid`)을 단계 단위로 정했다 —
    ADR 0010 §1이 승인한 `per_stage`이고, 네 서버의 util 합이 한 장비를 넘어 단계마다 그 단계의
    모델만 띄우려면 이 루프가 선행이다(`config/profiles.yaml`의 그 주석).

    **교대는 2026-09-29에 `harness_pool`이 선언했다** — 교대 명령(`tools/serve/swap.sh`)과
    장비 파일(`config/serving/`)이 같이 왔다(ADR 0009 「2026-09-29 제안」 절). 교대하는 역할은
    모델을 부르는 여섯 전부이고 상주는 없다 — 단계마다 그 단계 모델만 떠 있어야 한 장비에
    들어간다.
    `settle_timeout_s`는 계산값이다(그 주석 — 미실측).
    나머지 프로필은 기본(`per_candidate`)을 그대로 쓴다 — 지금까지의 run과 같다.
    """
    execution = load_config(profile).profile.execution

    if profile not in PER_STAGE_PROFILES:
        assert execution is None
        return
    assert execution is not None
    assert execution.mode == "per_stage"
    if profile not in SWAP_PROFILES:
        assert execution.swap is None
        return
    assert execution.swap is not None
    assert execution.swap.resident == ()
    assert set(execution.swap.swappable) == {
        "scout_memory", "scout_boundary", "scout_injection", "auditor", "critic", "pov"
    }


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
                        "settle_timeout_s": 30.0,
                    },
                },
            }
        )


def test_swap_naming_a_role_the_profile_does_not_have_is_rejected() -> None:
    """`swap`이 부르는 역할은 그 프로필의 `roles`에 실재해야 한다 (ADR 0009 불변식 2).

    없는 역할을 내리고 띄우는 선언은 **아무 일도 안 하면서 정책이 선 것처럼 보인다** —
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
    assert entry.execution.swap.settle_timeout_s == 30.0


def test_a_swap_that_names_a_sleep_level_is_rejected() -> None:
    """교대는 **콜드 재기동**이다 — vLLM `/sleep?level=`의 단계를 적을 자리가 없다.

    `level`은 sleep/wake 교대의 스키마였고, 채택된 방식(단계 경계에서 서버를 끄고 다음 단계
    모델을 새로 띄운다)에는 그 개념이 없다. 필드를 지운 뒤 옛 YAML이 `level:`을 적어 오면
    **조용히 무시되지 않고** 로드에서 죽어야 한다 — 적은 사람은 그 값이 무언가를 고른다고 믿는다.
    """
    with pytest.raises(ValidationError, match="level"):
        ProfileEntry.model_validate(
            _per_stage_swap(resident=["critic"], swappable=["scout", "auditor"], level=1)
        )


@pytest.mark.parametrize(
    ("resident", "swappable"),
    [([], ["scout", "embedding"]), (["reranker"], ["scout"])],
    ids=["swappable-embedding", "resident-reranker"],
)
def test_a_retrieval_role_in_swap_is_rejected(resident: list[str], swappable: list[str]) -> None:
    """검색 역할(embedding·reranker)은 교대 집합에 못 온다 — **hybrid 교대는 뒤로 미뤘다.**

    검색은 3단계에서 돌고, 5단계 검색을 어떻게 가를지(ADR 0012 결정 3)가 정해지기 전에는 그
    모델을 어느 단계에 띄울지 config가 말할 수 없다. 상주로 두면 모든 단계의 메모리를 먹고,
    교대로 두면 러너가 부를 단계 표가 없다 — 어느 쪽이든 「선 것처럼 보이는 정책」이다.
    `roles`에는 그 역할이 있어도(`_swap_names_roles_this_profile_has`를 통과하는 모양)
    거부해야 한다.
    """
    with pytest.raises(ValidationError, match="ADR 0012 결정 3"):
        ProfileEntry.model_validate(
            {
                "description": "x",
                "roles": {
                    "scout": "fake",
                    "auditor": "fake",
                    "critic": "fake",
                    "embedding": "fake",
                    "reranker": "fake",
                },
                "adapters": {"retriever": "hybrid", "tool_runner": "fake", "state_store": "memory"},
                "budget": "dev_fake",
                "execution": {
                    "mode": "per_stage",
                    "swap": {
                        "resident": resident,
                        "swappable": swappable,
                        "settle_timeout_s": 30.0,
                    },
                },
            }
        )


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
            "swap": {"settle_timeout_s": 30.0, **swap},
        },
    }


def test_a_swap_that_wakes_nobody_is_rejected() -> None:
    """`swappable`이 빈 교대 선언은 막는다 — **ADR 0009의 불변식이 아니다.**

    `SwapPolicy`의 docstring이 세 값에 기본을 안 주는 이유로 *"`swap: {}`이 「교대한다」고
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
    """한 역할이 상주이면서 교대일 수는 없다 — 이것도 ADR의 불변식이 아니다.

    "내리지 않는다"와 "단계 경계에서 내린다"를 같은 역할에 함께 선언한 것이라, 어느 쪽이
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


# 풀 프로필에서 모델을 부르는 실서빙 역할 여섯 — `harness_pool`의 `served_models()`.
_POOL_SERVED = [*SCOUT_POOL_ROLES, "auditor", "critic", "pov"]


def _swap_execution(swappable: list[str]) -> dict[str, object]:
    """상주 없이 `swappable`만 교대하는 `execution` 블록."""
    return {
        "mode": "per_stage",
        "swap": {"resident": [], "swappable": swappable, "settle_timeout_s": 60.0},
    }


@pytest.mark.parametrize(
    ("omitted", "on_the_auditors_key"),
    [("scout_boundary", False), ("auditor", False), ("critic", False), ("critic", True)],
    ids=["scout-member", "auditor", "critic", "critic-on-the-auditors-key"],
)
def test_a_swap_that_leaves_out_a_served_role_is_rejected(
    tmp_path: Path, omitted: str, on_the_auditors_key: bool
) -> None:
    """교대를 선언한 프로필은 **실서빙 역할을 전부** `resident ∪ swappable`에 적는다.

    `⊆ roles`(ADR 0009 불변식 2)는 한 방향뿐이다. 상주가 없는 이 배선에서 목록 밖의 실서빙 역할은
    교대 명령이 그 서버를 띄우지 않고, 그 역할만 부르는 단계의 교대는 키 없이 — 명령 계약상
    「자기 run의 서버 전부 내림」으로 — 불린다. 그 호출은 서버 없이 나가 run이 틀린 사유
    (`model_gateway_unavailable`)로 멈춘다. 다른 역할과 **같은 키**여도 같다 — 교대 대상은 그
    단계의 역할에서 고르므로 앞 단계에 그 키가 떠 있었어도 내려간다. (그 키가 상주로 떠 있어
    당장은 도는 배선도 거부한다 — 규칙이 엄격하다.)
    """
    pool = load_config("harness_pool").profile.roles
    patch: dict[str, object] = {
        "execution": _swap_execution([role for role in _POOL_SERVED if role != omitted])
    }
    if on_the_auditors_key:
        patch["roles"] = {**pool, omitted: pool["auditor"]}
    _copy_config(tmp_path, profile="harness_pool", profile_patch=patch)

    with pytest.raises(ConfigError, match=omitted):
        load_config("harness_pool", config_dir=tmp_path)


def test_a_swap_that_lists_every_served_role_loads(tmp_path: Path) -> None:
    """위 거부의 짝 — 여섯을 다 적으면 로드된다. 거부만 보면 교대 선언을 통째로 거부하는 구현도
    지난다(`test_a_complete_swap_declaration_on_per_stage_is_accepted`와 같은 이유)."""
    patch = {"execution": _swap_execution(_POOL_SERVED)}
    _copy_config(tmp_path, profile="harness_pool", profile_patch=patch)

    swap = load_config("harness_pool", config_dir=tmp_path).swap

    assert swap is not None
    assert set(swap.swappable) == set(_POOL_SERVED)


def test_a_fake_role_may_stay_out_of_the_swap_lists(tmp_path: Path) -> None:
    """세는 것은 `roles`가 아니라 **실서빙** 역할이다 — Fake 역할은 서버가 없어 교대가 다룰 것이
    없다. `dev_fake`는 전 역할이 Fake라 `scout` 하나만 적어도 로드된다."""
    _copy_config(tmp_path, profile_patch={"execution": _swap_execution(["scout"])})

    config = load_config("dev_fake", config_dir=tmp_path)

    assert config.swap is not None
    assert config.served_models() == {}


def test_a_hybrid_profile_cannot_declare_a_swap(tmp_path: Path) -> None:
    """검색 역할이 실서빙인 hybrid 프로필은 교대를 선언할 수 없다 — 목록에 넣으면 `SwapPolicy`가
    거부하고(ADR 0012 결정 3 뒤 — `test_a_retrieval_role_in_swap_is_rejected`), 빼면 위 규칙이
    거부한다. 빼고 통과하면 교대 명령이 검색 서버를 띄우지 않는다 — 교대가 띄우는 것은 목록의
    역할뿐이다."""
    patch = {"execution": _swap_execution(_POOL_SERVED)}
    _copy_config(tmp_path, profile="harness_pool_hybrid", profile_patch=patch)

    with pytest.raises(ConfigError, match="embedding.*reranker"):
        load_config("harness_pool_hybrid", config_dir=tmp_path)


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


def _launch_stages(config: HarnessConfig) -> list[set[str]]:
    """교대가 모델을 띄우는 단계 묶음 — Scout(4) → Auditor(5) → Critic(7) → 검색(hybrid) → PoV(8).

    러너가 단계마다 교대를 부르는 순서와 같다. **한 묶음 안의 모델은 함께 뜬다** — Scout 풀의 두
    모델은 한 장비에 공존해 같은 단계에 떠 있으므로 그 사이에는 띄우는 순서가 없다(2026-09-25
    `scout_memory`가 `qwen3-14b`로 옮겨 풀의 첫 자리 모델이 바뀌었을 때 포트를 안 바꾼 이유다).
    검색 두 모델은 hybrid에서만 뜨므로 canonical run의 묶음 **뒤**에 둔다 — canonical run이
    8000부터 차례로 올라가게 하려는 것이다. 앞 묶음에 이미 나온 키는 다시 세지 않는다.
    """
    roles = config.profile.roles
    stages: list[set[str]] = []
    seen: set[str] = set()
    for group in (SCOUT_POOL_ROLES, ("auditor",), ("critic",), RETRIEVAL_ROLES, ("pov",)):
        keys = {roles[role] for role in group if role in roles} - seen
        if keys:
            stages.append(keys)
            seen |= keys
    return stages


def _port(entry: ModelEntry) -> int | None:
    return urlparse(entry.endpoint).port


def test_served_ports_count_up_from_8000_in_launch_order() -> None:
    """포트는 교대가 띄우는 단계 순서대로 8000부터 빈틈없이 올라간다(한 단계 안의 순서는 없다).

    2026-09-24 전까지는 로스터를 바꿀 때마다 빈 번호에 끼워 넣어, canonical run이
    8009 → 8010 → 8008 → 8006 순서로 떴다(#267 기준 registry) — 번호만 보고는 어느 단계의
    모델인지 알 수 없었다. 이제 번호가 곧 띄우는 순서다.
    """
    config = load_config("harness_pool")
    first = FIRST_PORT
    for keys in _launch_stages(config):
        ports = {key: _port(config.models[key]) for key in keys}
        assert set(ports.values()) == set(range(first, first + len(keys))), (
            f"{sorted(keys)}의 포트 {ports}가 {first}부터 차례로 이어지지 않는다"
        )
        first += len(keys)


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


# --- 모델별 요청 손잡이 (ADR 0012 Phase 6.5 · #261) ------------------------------

_KNOBS = {
    "sampling": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0},
    "chat_template_kwargs": {"enable_thinking": True},
    "thinking_token_budget": 30720,
    "request_timeout_s": 1800,
}


def test_declared_model_knobs_reach_the_gateway_and_the_run_record_together(
    tmp_path: Path,
) -> None:
    """registry가 선언한 손잡이가 **요청과 기록 양쪽에 같은 값으로** 간다.

    `reasoning_effort`의 두 검사(바로 위)를 한 번에 옮긴 것이다 — 요청에만 실리면 기록이
    거짓이고, 기록에만 있으면 서버가 기본값으로 돈다. `sampling`은 프로필 공통
    `temperature`·`top_p`를 **그 모델에서 덮는다** — 그래서 덮은 값이 기록에 남아야 한다.

    `request_timeout_s`는 요청 본문이 아니라 클라이언트 쪽 값이라 payload·기록 맵에 안 간다.

    `scout` 자리는 「선언 안 한 모델」 대조군이다. registry의 `seccoderx-8b`가 2026-09-29부터
    `request_timeout_s`를 선언하므로(#261) 이 복사본에서만 그 필드를 비운다.
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "seccoderx-8b",
                "auditor": "qwen3.8-27b",
                "critic": "gpt-oss-20b",
            }
        },
        model_patch={"qwen3.8-27b": _KNOBS, "seccoderx-8b": {"request_timeout_s": None}},
    )
    config = load_config("dev_fake", config_dir=tmp_path)

    sent = {
        "temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
        "chat_template_kwargs": {"enable_thinking": True},
        "thinking_token_budget": 30720,
    }
    assert config.request_params() == {"auditor": sent}

    record, _ = _new_record(
        RunSpec(condition="B3prime@pipeline", target=tmp_path, device="atom"),
        config, "run_20260925_0001", datetime(2026, 9, 25, tzinfo=UTC), [], {}, {}, None,
    )
    assert json.loads(record.model_dump_json())["model_request_params"] == {"auditor": sent}

    auditor = _model_client(config.profile.roles["auditor"], config, role="auditor")
    payload = auditor._request_payload("prefix", "body", RunRecord)  # noqa: SLF001
    assert {key: payload[key] for key in sent} == sent
    assert "request_timeout_s" not in payload
    assert auditor.timeout_s == 1800

    scout = _model_client(config.profile.roles["scout"], config, role="scout")
    quiet = scout._request_payload("prefix", "body", RunRecord)  # noqa: SLF001
    assert not set(quiet) & {"top_k", "min_p", "chat_template_kwargs", "thinking_token_budget"}
    assert scout.timeout_s is None, "선언 안 한 모델은 어댑터 기본 타임아웃을 쓴다"


@pytest.mark.parametrize(
    "patch",
    [
        None,  # 대조군 — 이것이 죽으면 아래 넷이 손잡이 때문에 죽는지 모른다
        {"sampling": {"top_p": 0.0}},
        {"sampling": {"topk": 20}},
        {"thinking_token_budget": 0},
        {"request_timeout_s": 0},
        # 등급은 닫힌 집합이다 — `xhigh`를 더한 뒤에도(2026-09-26) 모르는 값은 로드에서 죽는다
        {"reasoning_effort": "ultra"},
    ],
)
def test_a_bad_model_knob_fails_at_load(tmp_path: Path, patch: dict | None) -> None:
    """손잡이는 **우리가 적는 설정**이라 오타·범위 밖 값이 곧 결함이다 — 로드에서 죽는다.

    서버가 조용히 무시하거나 400을 내면 그 run은 「선언했다」고 기록되고 실제로는 안 쓴 것이다.
    """
    _copy_config(
        tmp_path,
        profile_patch={
            "roles": {
                "scout": "seccoderx-8b",
                "auditor": "qwen3.8-27b",
                "critic": "gpt-oss-20b",
            }
        },
        model_patch={"seccoderx-8b": patch or {}},
    )
    if patch is None:
        load_config("dev_fake", config_dir=tmp_path)
        return
    with pytest.raises(ValidationError):
        load_config("dev_fake", config_dir=tmp_path)


# 사고 켠 좌석의 디코드 속도(tok/s) — **좌석마다 따로 둔다.** 하나로 묶으면 한 좌석의 실측이
# 다른 좌석의 하한(④ 타임아웃 >= 출력 상한 / 속도)까지 같이 움직인다.
# 2026-10-03 쪼갰다 — 그때까지 둘이 외부 보고 4.5 하나를 공유했다.
#
# **Auditor — vast 실측 16.2.** 2026-09-29 · `config/serving/vastai.yaml`의 `qwen3.8-27b`
# (util 0.90 · `--max-model-len 262144` · KV 29.84 GiB = 483,182토큰 · 정상 상태 생성).
# 운영 장비가 vast다(ATOM은 2026-09-29 사용 종료). 아래 검사가 그 파일과 수를 대조한다.
# ⚠ **긴 입력에서 안 쟀다.** 그 관측은 요청 하나(n=1)의 정상 상태이고, 5단계 도구 루프는 홉마다
#   관측을 history로 되먹여 **입력이 단조 증가**한다 — 홉 후반 속도는 0(미실측)이다(이슈 #432).
# ⚠ **그래서 이 하한이 서는 범위를 수로 적는다 — 손익분기 8.74 tok/s**(= 출력 상한 65,536 /
#   타임아웃 7,500 s, 계산값). 실제 디코드가 그 아래로 떨어지면 상한이 아니라 **시계가** 호출을
#   멈추고, 이 검사는 초록인 채로 그것을 못 잡는다. vast 실측 16.2 대비 여유는 1.85배(계산값).
# ⚠ **ATOM 실측은 4.27이었다** — 외부 보고 4.5보다 느렸다(2026-09-29 교대 스모크
#   `docs/experiments/harness/2026-09-29-stage-swap-smoke-atom.md`). 장비가 달라 vast 값과 같은
#   축에 놓지 않는다(evaluation.md §2.3 규칙 5 · D81). 그 장비로 돌아가면 이 상수도 돌아간다.
_AUDITOR_DECODE_TOK_S = 16.2

# **PoV(`feyospace-v1-small`) — 자기 속도를 안 쟀다.** Auditor와 같은 베이스·같은 파라미터 수지만
# 그것은 **추론이지 측정이 아니다.** 그래서 실측을 빌리지 않고 저장소에서 가장 느린 값(외부 보고
# 4.5 · GB10, NVIDIA 포럼 2026-08-24 · `config/models.yaml`의 `qwen3.8-27b` 항목)을 보수값으로 둔다.
# 그래서 PoV 쪽 검사가 뜻하는 것은 「PoV 타임아웃이 실측으로 맞다」가 아니라 **「같은 관계가
# 보수적으로 선다」**까지다. 그 좌석 속도를 재면 이 값을 그 실측으로 바꾼다(이슈 #432).
_POV_DECODE_TOK_S = 4.5

_DECODE_TOK_S_BY_SEAT = {"auditor": _AUDITOR_DECODE_TOK_S, "pov": _POV_DECODE_TOK_S}


def _assert_thinking_seat_holds_together(role: str) -> None:
    """사고 켠 좌석 하나의 「사고가 끝날 자리 + 시계」 관계 — Auditor·PoV가 같이 쓴다.

    무는 것은 **관계**다: ① `xhigh` ② 템플릿 인자가 사고를 끄지 않는다(`enable_thinking`이
    `false`가 아니다) ③ 사고 상한 < 출력 상한이고, 답 몫(출력 상한 − 사고 상한)이 전역 출력
    상한 이상이다 — `config/budgets.yaml`의 「사고 상한이 답 몫(위 전역 상한)을 남긴다」
    ④ 타임아웃 ≥ 출력 상한 ÷ 속도 ⑤ 요청 payload가 그 값들을 싣는다. 속도는 **좌석별**
    (`_DECODE_TOK_S_BY_SEAT[role]`)이다 — 2026-10-03까지는 상수 하나를 공유했다(위 주석).

    안 묻는 것: `sampling`(값이다) · `chat_template_kwargs`의 `enable_thinking: false` 밖의
    키 · 답 몫이 **충분한지**(PoV라면 `PovSpec` 실제 길이 — 실측 전이다). ③·④는 하한만
    문다 — 경계 바로 위 값도 초록이다.
    """
    config = load_config("harness_pool")
    entry = config.model_for(role)
    cap = config.thresholds.output_max_for(role)

    assert entry.reasoning_effort == "xhigh", (
        f"{role} — 사고를 켜는 가장 높은 등급이다(템플릿: xhigh·medium·low)"
    )
    assert (entry.chat_template_kwargs or {}).get("enable_thinking") is not False, (
        f"{role} — 템플릿 인자 enable_thinking: false가 사고를 끈다 — 사고 좌석의 전제와 모순이다"
    )
    assert entry.thinking_token_budget is not None, (
        f"{role} — 사고 상한 선언이 없다 — 사고가 출력 상한 {cap}까지 가서 답 몫이 없을 수 있다"
    )
    assert entry.thinking_token_budget < cap, (
        f"{role} — 사고 상한이 출력 상한 {cap} 안에 답 몫을 남겨야 한다 — 닿으면 vLLM이 "
        "</think>를 넣고 답을 쓴다"
    )
    assert cap - entry.thinking_token_budget >= config.thresholds.output_max, (
        f"{role} — 답 몫(출력 상한 − 사고 상한)이 전역 출력 상한 "
        f"{config.thresholds.output_max}보다 작다 — 사고가 예산을 다 쓰면 답이 그보다 짧게 잘린다"
    )
    assert entry.request_timeout_s is not None, (
        f"{role} — 어댑터 기본 300 s로는 사고 켠 호출이 못 끝난다"
    )
    assert entry.request_timeout_s >= cap / _DECODE_TOK_S_BY_SEAT[role], (
        f"{role} — 타임아웃이 출력 상한 {cap}보다 먼저 끊는다 — 상한이 아니라 시계가 호출을 "
        "멈춘다(#261)"
    )

    client = _model_client(config.profile.roles[role], config, role=role)
    payload = client._request_payload("prefix", "body", RunRecord)  # noqa: SLF001
    assert payload["reasoning_effort"] == config.reasoning_efforts()[role] == "xhigh"
    assert payload["thinking_token_budget"] == entry.thinking_token_budget
    assert payload["max_tokens"] == cap


def test_the_canonical_auditor_thinks_within_a_budget_that_holds_together() -> None:
    """canonical Auditor는 가장 높은 등급으로 사고하고, 그 사고가 끝날 자리가 예산에 있다.

    2026-09-26 @danielcho02 — Auditor를 `qwen3.8-27b`로 바꾸고 사고를 켠다. **켜기만 하면 셋이 깨진다**
    (같은 날 council): ① 사고가 출력 상한 안에 안 끝나면 본문이 비거나 잘려 스키마 실패다 — 옛
    Auditor에 parser를 걸고 4,096으로 돌린 판이 7/7(본문 `None` 6 · 잘린 JSON 1)이었다 ② 요청
    타임아웃이 상한보다 먼저 오면 호출이 끊기고
    연속 셋이면 run이 선다(#261) ③ 사고 상한이 출력 상한과 같으면 답 몫이 없다. 그래서 **값이 아니라
    관계를** 잠근다 — 값은 ATOM 실측으로 바뀐다.
    ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340). 값을 바꿀 실측 장비는 vast다(D81).
    """
    _assert_thinking_seat_holds_together("auditor")


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_run_gpu_budget_holds_a_full_lap_of_declared_request_timeouts(profile: str) -> None:
    """run `gpu_seconds`는 서빙 역할 한 바퀴의 선언된 요청 타임아웃 합 이상이다(이슈 #389).

    7,200이던 때는 한 바퀴(선언값의 합 — 두 프로필 모두 21,300 s, 2026-10-05 계산)의 3분의 1이라
    호출 하나가 7,500 s 타임아웃으로 한도를 넘을 수 있었고, 정상 run이 중간에 끊긴 채 장부에는
    「결과가 적은 run」으로만 남았다. **값이 아니라 관계를 잠근다** — 입력이 전부 선언된 설정값이라
    측정 없이 핀할 수 있다(타임아웃 선언이 없는 좌석은 어댑터 기본 300 s). 이 검사는 실모델 서빙
    프로필 둘만 본다.
    """
    config = load_config(profile)
    timeouts = {
        role: config.model_for(role).request_timeout_s or 300 for role in config.profile.roles
    }

    assert config.budget.gpu_seconds >= max(timeouts.values()), (
        f"{profile} — gpu_seconds {config.budget.gpu_seconds}가 단일 호출 타임아웃 최대 "
        f"{max(timeouts.values())}보다 작다 — 호출 하나가 run 예산을 넘는다(#389)"
    )
    assert config.budget.gpu_seconds >= sum(timeouts.values()), (
        f"{profile} — gpu_seconds {config.budget.gpu_seconds}가 서빙 역할 한 바퀴의 선언 "
        f"타임아웃 합 {sum(timeouts.values())}보다 작다 — 정상 run이 예산에 끊긴다(#389)"
    )


def test_the_canonical_pov_seat_thinks_within_a_budget_that_holds_together() -> None:
    """8단계 PoV 좌석(`feyospace-v1-small`)도 Auditor와 **같은 관계**를 지킨다.

    그 좌석은 Auditor와 같은 베이스·chat_template이라 손잡이 넷(`reasoning_effort` ·
    `sampling` · `thinking_token_budget` · `request_timeout_s`)과 출력 상한
    (`output_max_by_role.pov`)을 물려받았다(`config/models.yaml`의 그 항목 — 계획값·미실측).
    물려받은 값이 서로 어긋나면 실서빙 때 사고가 답을 밀어내 본문이 비거나 잘려 스키마가 깨지고
    0 PoC로 완주한다 — Auditor가 4,096으로 그 실패를 7/7 냈다. 상한 선언이 빠지면 전역 4,096으로
    돌고, 사고 상한이 출력 상한을 넘으면 답 몫이 없으며, 타임아웃이 짧으면 상한 전에 시계가
    호출을 끊는다.

    ⚠ 무는 것은 **관계**이고, 속도는 feyospace를 안 재서 보수값(`_POV_DECODE_TOK_S` = 외부
    보고 4.5)이다 — 「PoV 타임아웃이 실측으로 맞다」가 아니다. Auditor 쪽은 2026-10-03에 vast
    실측 16.2로 갈라져 나갔고 이 좌석은 따라가지 않았다(그 상수 주석).
    타임아웃과 답 몫은 하한만 문다: 값이 경계 바로 위여도 초록이고, 여유 배수·프리필은 안
    본다. 답 몫이 `PovSpec`에 **충분한지**는 실측 전이라 안 묻는다. `sampling`은 값이라
    안 묻고, `chat_template_kwargs`는 사고를 끄는 `enable_thinking: false` 하나만 문다.
    """
    _assert_thinking_seat_holds_together("pov")


def test_each_thinking_seat_takes_its_decode_speed_from_its_own_device() -> None:
    """속도 상수는 좌석마다 따로 있고, 쓰는 값은 저장소에 실재하는 관측과 같다.

    위 검사 둘이 무는 ④(타임아웃 >= 출력 상한 / 속도)가 **상수 하나를 공유하면** 한 좌석의
    실측이 다른 좌석의 하한을 같이 움직인다. Auditor는 운영 장비(vast) 실측이 있고 PoV
    (`feyospace-v1-small`)는 자기 속도를 안 쟀으므로, 묶어 두면 PoV 검사가 **측정 없이**
    느슨해진다.

    무는 것 셋. (1) 좌석별 상수가 갈려 있다 (2) 미실측 좌석(PoV)이 실측 좌석보다 느린
    보수값을 쓴다 (3) Auditor 상수가 장비 파일(`config/serving/vastai.yaml`)이 적은 실측과
    **같은 수**다 — 상수만 올려 ④를 통과시키는 길을 막는다. PoV 쪽은 대조할 실측이 없어
    (2)까지만 문다.

    안 묻는 것: 그 실측이 **맞는지**(장비 파일이 원천이다) · 긴 입력에서의 속도(미실측 —
    위 상수 주석의 손익분기) · 좌석이 둘보다 늘어나는 경우(늘면 이 검사를 같이 넓힌다).
    """
    assert set(_DECODE_TOK_S_BY_SEAT) == {"auditor", "pov"}, (
        "사고 켠 좌석이 늘거나 줄었다 — 위 상수와 이 검사를 같이 넓힌다"
    )
    assert _DECODE_TOK_S_BY_SEAT["auditor"] != _DECODE_TOK_S_BY_SEAT["pov"], (
        "두 좌석이 상수 하나를 공유한다 — 한쪽 실측이 다른 쪽 하한을 같이 움직인다"
    )
    assert _DECODE_TOK_S_BY_SEAT["pov"] < _DECODE_TOK_S_BY_SEAT["auditor"], (
        "PoV는 자기 속도를 안 쟀다 — 실측이 있는 좌석보다 느린 보수값이어야 한다"
    )

    served = (CONFIG_DIR / "serving" / "vastai.yaml").read_text("utf-8")
    assert f"{_DECODE_TOK_S_BY_SEAT['auditor']} tok/s" in served, (
        f"Auditor 속도 상수 {_DECODE_TOK_S_BY_SEAT['auditor']}가 장비 파일의 실측과 다르다 — "
        "상수만 올리면 「타임아웃 >= 출력 상한 / 속도」가 측정 없이 통과한다"
    )


# 스모크에서 잰 좌석별 단일 요청 생성 속도(tok/s) — 2026-09-24 ATOM `run_20260924_0001`
# (`docs/experiments/harness/2026-09-24-roster-smoke-atom.md` §1·§2 · 이슈 #261 본문도 같은 수),
# vLLM 서버 로그 `Avg generation throughput`의 `Running: 1 reqs` 10초
# 창 중앙값(237·106창). Auditor(그때 `qwen3.6-35b-a3b`, 지금 `qwen3.8-27b`)는 로스터가
# 바뀌었고 `test_the_canonical_auditor_thinks_within_a_budget_that_holds_together`가 이미
# 잠그므로 여기 넣지 않는다.
#
# `gpt-oss-20b`(critic)는 재는 법이 다르다 — 호출 하나의 출력 토큰 ÷ wall(계산값).
# `docs/experiments/harness/2026-09-12-critic-gptoss-token-accounting-atom.md` §3.3의
# `find_0014`가 16,384토큰(`finish=length`)에 382초였다(같은 revision · vLLM 0.27.1 · 세 서버
# 동시 상주 경합 아래). 2026-09-24 스모크의 critic은 이 모델이 아니었다(스모크 한정
# `qwen3.6-35b-a3b` — 그 보고서 §2).
_MEASURED_DECODE_TOK_S = {
    "seccoderx-8b": 13.3,
    "qwen3-14b": 8.1,
    "gpt-oss-20b": 16384 / 382,
}


def test_served_scout_and_critic_roles_declare_a_request_timeout() -> None:
    """실서빙 Scout·Critic 좌석마다 `request_timeout_s`가 있고, 속도를 아는 좌석은 벽시계가
    출력 상한보다 먼저 끊지 않는다(#261).

    ATOM 09-24 실측 단일 요청 속도로는 `seccoderx-8b` 13.3 · `qwen3-14b` 8.1 tok/s라
    전역 `output_max` 4,096에 각각 ≈308 s · ≈506 s가 걸려 어댑터 기본 300 s를 넘는다.
    #274 뒤에는 그 후보만 버리지만, 연속 셋이면 `_MAX_CONSECUTIVE_DROPS`가 run을 세운다.

    Auditor는 `test_the_canonical_auditor_thinks_within_a_budget_that_holds_together`가
    이미 잠갔으므로 여기서 다시 보지 않는다. `_MEASURED_DECODE_TOK_S`에 없는 모델은
    선언 여부만 본다 — **PoV(`feyospace-v1-small`)가 그렇다**: 속도를 안 쟀으니 여기 못 넣고,
    관계(타임아웃 ≥ 출력 상한 ÷ 속도)는
    `test_the_canonical_pov_seat_thinks_within_a_budget_that_holds_together`가 Auditor의 외부 보고
    속도를 빌려 문다. 이 검사가 PoV에 하는 것은 선언 존재까지다.

    ⚠ 무는 것은 **순차 요청의 속도**다. `gpt-oss-20b`는 ≈95 s라 300 s로도 이 관계가 서서,
    그 값을 300으로 내려도 초록이다. 단계 안 동시 요청이 들어오면 스트림당 속도가 떨어지는데
    그것은 이 검사 밖이다.

    ⚠ 그리고 **하한 하나**(`선언값 ≥ 상한 ÷ 속도`)만 문다 — 경계값이어도 초록이다(위 ≈308 s ·
    ≈506 s 바로 위의 값이면 지나간다). 여유 배수·프리필은 안 물므로 초록이 1,200 · 1,800 s에
    여유가 있다는 뜻은 아니다. 선언값의 유도식(`config/models.yaml`이 인용하는 ÷9.1 · ÷6.2 ·
    ÷12 · × 1.3)도 이 검사 밖이다.
    """
    config = load_config("harness_pool")
    served = config.served_models()
    roles_to_check = {role: key for role, key in served.items() if role != "auditor"}
    assert roles_to_check, "harness_pool의 실서빙 역할 목록이 비어 있다 — 프로필이 바뀌었다"

    for role, model_key in roles_to_check.items():
        entry = config.models[model_key]
        cap = config.thresholds.output_max_for(role)
        assert entry.request_timeout_s is not None, (
            f"{model_key}({role}) — 어댑터 기본 300 s로는 느린 좌석이 출력 상한 {cap}을 "
            "다 못 낸다(#261)"
        )
        tok_s = _MEASURED_DECODE_TOK_S.get(model_key)
        if tok_s is None:
            continue
        assert entry.request_timeout_s >= cap / tok_s, (
            f"{model_key}({role}) — 타임아웃이 출력 상한보다 먼저 끊는다(#261)"
        )



def test_auditor_source_options_stay_out_of_flatten_until_turned_on() -> None:
    """#349 옵션은 0이면 `flatten()`에 키가 없다(옛 manifest 모양) — 켜면 생긴다. 키를 늘
    넣어도 통과하던 변이(#351 리뷰)."""
    from src.config import AUDITOR_SOURCE_OPTIONS

    # 2026-10-03부터 `budgets.yaml`이 넷을 켠다 -> 끈 상태를 만들어 옛 모양을 본다.
    thresholds = load_config("dev_fake").thresholds.transition(
        **{key: 0 for key in AUDITOR_SOURCE_OPTIONS}
    )
    assert not set(AUDITOR_SOURCE_OPTIONS) & thresholds.flatten().keys()
    assert thresholds.auditor_source_options_on() == ()
    on = thresholds.transition(**{key: 1 for key in AUDITOR_SOURCE_OPTIONS})
    assert set(AUDITOR_SOURCE_OPTIONS) <= on.flatten().keys()
    assert on.auditor_source_options_on() == AUDITOR_SOURCE_OPTIONS


@pytest.mark.parametrize(
    "key",
    ["auditor_callee_hops", "auditor_definitions", "auditor_entry_trim", "auditor_callee_wrappers"],
)
def test_switch_like_auditor_source_options_reject_two(key: str) -> None:
    with pytest.raises(ValidationError):
        load_config("dev_fake").thresholds.transition(**{key: 2})


@pytest.mark.parametrize("version", ["v4", "v5", "v6"])
def test_the_older_auditor_prompts_describe_no_source_option(version: str) -> None:
    """v4·v5·v6는 결정 1-b의 SOURCE만 서술한다 — 옵션을 켜면 `cli`가 막는 근거(#351 MEDIUM-2).
    v6는 #418이 A/B를 잰 문장 그대로다 — 옵션 서술은 v7이 든다(2026-10-04 · PR #443 리뷰 HIGH)."""
    assert load_prompt(Role.AUDITOR, version).source_options == ()


@pytest.mark.parametrize("profile", ["harness_pool", "harness_pool_hybrid"])
def test_the_canonical_auditor_prompt_declares_exactly_the_options_turned_on(profile: str) -> None:
    """서빙 판이 선언한 옵션 == `budgets.yaml`이 켠 옵션. `cli`는 켰는데 선언 안 한 쪽만 막으므로
    선언했는데 끈 쪽(서술이 거짓)은 여기서만 걸린다(2026-10-03 넷을 켬 · 서술은 v7, #349)."""
    config = load_config(profile)
    version = config.model_for("auditor").prompt_template_version
    described = set(load_prompt(Role.AUDITOR, version).source_options)
    assert described == set(config.thresholds.auditor_source_options_on())


def test_prompt_meta_source_options_take_only_known_keys(tmp_path: Path) -> None:
    """meta의 `source_options`는 아는 키만 — 오타는 가드를 조용히 끄므로 거절한다."""
    root = tmp_path / "prompts"
    shutil.copytree(PROMPTS_DIR / "auditor" / "v5", root / "auditor" / "v5")
    meta = root / "auditor" / "v5" / "meta.yaml"
    base = meta.read_text(encoding="utf-8")

    meta.write_text(base + "source_options: [auditor_definitions]\n", encoding="utf-8")
    assert load_prompt(Role.AUDITOR, "v5", root=root).source_options == ("auditor_definitions",)

    other = tmp_path / "prompts_typo"  # `load_prompt`는 (역할, 판, root)로 캐시한다
    shutil.copytree(PROMPTS_DIR / "auditor" / "v5", other / "auditor" / "v5")
    (other / "auditor" / "v5" / "meta.yaml").write_text(
        base + "source_options: [auditor_definition]\n", encoding="utf-8"
    )
    with pytest.raises(PromptError, match="source_options"):
        load_prompt(Role.AUDITOR, "v5", root=other)
