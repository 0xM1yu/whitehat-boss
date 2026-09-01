"""config/*.yaml 로더.

engineering-guide.md §8.5: "하드코딩 금지 — 임계값·예산·모델은 `config/`에서 온다."
이 모듈이 그 규칙의 단일 진입점이다. 노드도 어댑터도 YAML을 직접 읽지 않는다.

설정 모델은 `src.schemas`의 `HarnessModel`을 상속한다. `extra="forbid"` 덕분에
YAML의 오타 필드가 조용히 무시되지 않고 로드 시점에 실패한다 — 8개 트랙이 같은 설정
파일을 건드리므로(roadmap.md §3) 조용한 무시가 가장 비싼 실패다.

`Budget`·`Sampling`은 `src.schemas.run`의 것을 그대로 쓴다. 같은 값이 `RunRecord`에
박제되므로(evaluation.md §6) 별도 타입을 두면 두 정의가 갈라진다.
"""

from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal

import yaml
from pydantic import Field, model_validator

from src.schemas.common import HarnessModel, NonEmptyStr, Sha256Digest
from src.schemas.enums import CapsuleLevel
from src.schemas.run import Budget, Sampling

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# 프로필에 반드시 있어야 하는 역할. embedding·reranker는 Retriever 구현에 따라 없을 수 있다.
REQUIRED_ROLES = ("scout", "auditor", "critic")

# `retriever: hybrid`일 때만 추가로 요구되는 역할 (ProfileEntry의 검증기 참조).
RETRIEVAL_ROLES = ("embedding", "reranker")

# 프롬프트를 받고 구조화 출력을 내는 역할. `ModelClient`가 만들어지는 유일한 대상이다.
#
# `Role` 열거형과 다르다 — `Role.JUDGE`는 Evidence Gate가 결정론적이라 모델을 부르지
# 않는다. `RETRIEVAL_ROLES`와도 다르다 — embedding·reranker는 프롬프트도 출력 스키마도
# 없다(ADR 0004). 이 셋을 구분하지 않으면 `RunRecord.prompt_versions`에 존재하지 않는
# 프롬프트의 버전이 적힌다.
#
# `tests/test_config.py::test_prompted_roles_matches_prompt_directories`가 이 상수와
# `prompts/` 하위 디렉터리의 일치를 강제한다.
PROMPTED_ROLES = (
    "scout",
    "auditor",
    "critic",
    "single",
)

# 이 스킴이면 FakeModelClient로 간다. 역할별로 갈리므로 프로필 안에서 혼용이 가능하다.
FAKE_SCHEME = "fake://"


class ConfigError(Exception):
    """설정 파일이 없거나 참조가 깨졌다. 삼키지 않고 기동 시점에 터뜨린다."""


class Benchmark(HarnessModel):
    """model-serving.md §5의 실측 기록.

    전 항목이 0이면 아직 이 장비에서 재보지 않았다는 뜻이다. §3 체크리스트가
    블로킹이므로(`docs/open-questions/R/R1.md`) 값이 0인 모델은 production 프로필에 넣지 않는다.
    """

    measured_at: datetime | None = None
    # **어느 장비에서 쟀는가.** `measured_at`(언제)과 대칭이다.
    #
    # 이 값이 없으면 registry의 수치가 어느 하드웨어 기준인지 기록에서 사라진다. 두 장비가
    # 모두 지원 대상인 이상(docs/portability.md) `--apply`를 어디서 치느냐에 따라 같은 필드에
    # 다른 장비의 값이 들어가는데, 그 사실이 남지 않으면 다음 사람이 이 수치로 다른 장비를
    # 예측하게 된다.
    #
    # **막지 않고 기록한다.** `--tool-runner`도 막지 않고 `RunRecord.tool_runner`에 남기고
    # (ADR 0005), `repo_dirty`도 dirty 실행을 막지 않고 기록만 한다. 여기서만 금지로 가면
    # 이 저장소가 지켜 온 논법이 깨진다 — 대신 `doctor`가 현재 장비와 다르면 경고한다.
    #
    # 기본값이 `None`인 것은 `measured_at`과 같은 이유다. 기존 registry 항목에 이 키가
    # 없으면 로드가 통째로 실패하고, `None`은 "장비 미상"이라는 정직한 값이다.
    measured_on: NonEmptyStr | None = None
    tok_s_single: float = Field(default=0.0, ge=0.0)
    tok_s_batch32: float = Field(default=0.0, ge=0.0)
    mem_gb_ctx8k: float = Field(default=0.0, ge=0.0)

    # 실측 대상 세 항목. `--apply`가 쓰는 키(`bench_report.APPLY_KEYS`) 중 수치만이다.
    # `ClassVar`가 아니면 pydantic이 필드로 읽어 registry 스키마가 바뀐다.
    MEASUREMENTS: ClassVar[tuple[str, ...]] = ("tok_s_single", "tok_s_batch32", "mem_gb_ctx8k")

    @property
    def missing_measurements(self) -> tuple[str, ...]:
        """아직 0인 항목. **세 상태를 가르는 단일 출처다.**

        비어 있으면 전부 측정, 셋 다면 미측정, 그 사이면 부분 측정이다.
        """
        return tuple(name for name in self.MEASUREMENTS if not getattr(self, name))

    @property
    def is_measured(self) -> bool:
        """**하나라도 쟀는가.** "전부 쟀는가"가 아니다 — `is_fully_measured`가 그쪽이다.

        이 값이 `any`인 것은 의도다. `--apply`가 못 잰 값을 0으로 남기므로(bench.py
        모듈 docstring) 부분적으로 채워진 상태가 정상 경로에 실재하고, 그때 이 모델을
        "아무것도 모르는 모델"로 취급하면 이미 확보한 수치까지 버리게 된다.

        **대신 이것만으로 production 게이트를 세우면 절반만 채우고도 열린다.** 그래서
        `doctor`가 `missing_measurements`로 부분 측정을 따로 판정한다 — ATOM에서
        `mem_gb_ctx8k`를 nvidia-smi로 얻을 수 없다는 것이 확정됐으므로(2026-08-17) 그
        상태는 가정이 아니라 곧 실제로 발생한다.
        """
        return len(self.missing_measurements) < len(self.MEASUREMENTS)

    @property
    def is_fully_measured(self) -> bool:
        return not self.missing_measurements


class ModelEntry(HarnessModel):
    """Model Registry 한 항목 (model-serving.md §5)."""

    endpoint: NonEmptyStr
    context_limit: int = Field(gt=0)
    quantization: NonEmptyStr
    license: NonEmptyStr
    # model·tokenizer·quantization 묶음의 해시. RunRecord.model_checksums로 들어간다.
    checksum: Sha256Digest
    # **그 checksum이 어디서 나왔는가.** 예: `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@<rev>`
    # 또는 `file:///data/models/qwen3-coder-30b-nvfp4`.
    #
    # checksum만으로는 사람이 역추적할 수 없다. 후보가 전부 커뮤니티·서드파티 산출물이라
    # (2026-08-16 조사: Auditor NVFP4만 세 벌) **같은 이름의 모델이 리포지터리마다 다르고**,
    # 값이 안 맞을 때 "어느 리포의 어느 리비전이었나"를 답할 실마리가 기록에 없었다.
    #
    # **형식을 패턴으로 강제하지 않는다.** checksum 정본이 로컬 스냅샷 해시라
    # (model-serving.md §5) `file://`도 정당한 값이고, `hf://`로 좁히면 그 경로를 적을
    # 수 없다. `--device` 라벨 집합을 열어 둔 것과 같은 판단이다(ADR 0006). 대신
    # `NonEmptyStr`이라 "적었는데 빈 문자열"은 막는다.
    #
    # 기본값이 `None`인 것은 `Benchmark.measured_on`과 같은 이유다 — 기존 registry 항목에
    # 이 키가 없으면 로드가 통째로 실패하고, `None`은 "출처 미상"이라는 정직한 값이다.
    # `--apply`는 이 필드를 건드리지 않는다(`benchmark:` 블록 밖이고 `APPLY_KEYS`에 없다).
    source: NonEmptyStr | None = None
    prompt_template_version: NonEmptyStr
    fallback: str | None = None
    benchmark: Benchmark

    @property
    def is_fake(self) -> bool:
        return self.endpoint.startswith(FAKE_SCHEME)


class AdapterSelection(HarnessModel):
    """ModelClient를 제외한 3개 인터페이스의 구현 선택.

    ModelClient가 여기 없는 것은 의도다. 역할마다 다른 모델을 쓰고 그중 일부만 실서빙일
    수 있으므로(single_gpu_12g), 어댑터 선택은 `ModelEntry.endpoint` 스킴이 역할별로 한다.

    `Literal`로 닫아 둔 것도 의도다. 아직 없는 구현체 이름을 YAML에 적으면 factory가
    KeyError를 내는 대신 설정 로드에서 실패해야 한다.
    """

    # hybrid는 embedding·reranker를 실제로 호출한다. 그 두 역할이 프로필에 없으면
    # 로드 시점에 실패한다(아래 검증기) — factory에서 KeyError로 터지면 어느 프로필이
    # 잘못됐는지가 스택 아래에 묻힌다.
    retriever: Literal["fake", "hybrid"]
    # docker·singularity·podman은 같은 구현(`ContainerToolRunner`)의 런타임 선택이다.
    # Seraph는 Docker 데몬 접근이 없어 singularity를 쓴다(2026-08-17 실측).
    #
    # **ATOM은 docker와 podman을 둘 다 가진다**(2026-08-21 실측: Docker 29.2.1 · podman
    # 4.9.3). 이 주석은 2026-08-17 실측을 근거로 "ATOM은 데몬 접근이 없고 singularity도
    # 없어 podman을 쓴다"고 적고 있었는데(`32e2fb5`), 4일 뒤 docker가 확인되어 반증됐다.
    # 이후 실행 기록도 그쪽이다 — 8/25·26·27 ATOM run이 전부 docker로 돌았고 podman으로
    # 도구를 돌린 run은 `run_20260817_0001` 하나다. 어느 쪽을 선언하는지는 프로필마다
    # 다르고 그 표가 docs/portability.md §3에 있다.
    #
    # CLI `--tool-runner`가 이 Literal에서 choices를 뽑는다.
    tool_runner: Literal["fake", "docker", "singularity", "podman"]
    state_store: Literal["memory", "sqlite"]


class ProfileEntry(HarnessModel):
    """실행 프로필 하나 (model-serving.md §5)."""

    description: NonEmptyStr
    production: bool = False
    # 역할 이름 → models.yaml의 model key
    roles: dict[str, NonEmptyStr]
    adapters: AdapterSelection
    # budgets.yaml의 budgets 키
    budget: NonEmptyStr

    @model_validator(mode="after")
    def _has_required_roles(self) -> "ProfileEntry":
        missing = [role for role in REQUIRED_ROLES if role not in self.roles]
        if missing:
            raise ValueError(f"프로필에 필수 역할이 없다: {missing}")
        return self

    @model_validator(mode="after")
    def _hybrid_needs_retrieval_roles(self) -> "ProfileEntry":
        """`retriever: hybrid`는 embedding·reranker 역할을 요구한다.

        REQUIRED_ROLES에 넣지 않은 것은 의도다 — `retriever: fake`인 프로필에는 그
        두 역할이 없어도 된다. 요구 조건이 어댑터 선택에 달려 있으므로 여기서 본다.
        """
        if self.adapters.retriever != "hybrid":
            return self
        missing = [role for role in RETRIEVAL_ROLES if role not in self.roles]
        if missing:
            raise ValueError(f"retriever: hybrid 프로필에 검색 역할이 없다: {missing}")
        return self


class CapsuleBudgets(HarnessModel):
    """캡슐 레벨별 토큰 상한. 키는 `CapsuleLevel` 값과 같다 (knowledge-base.md §4)."""

    light: int = Field(gt=0)
    expanded: int = Field(gt=0)
    focused: int = Field(gt=0)


class Thresholds(HarnessModel):
    """정책 임계값. 노드가 직접 비교하지 않고 RoutingPolicy가 읽는다."""

    scout_threshold: float = Field(ge=0.0, le=1.0)
    capsule_expansion_max: int = Field(ge=0)
    requeue_max: int = Field(ge=0)
    critic_top_n: int = Field(gt=0)
    # 한 run이 4단계에 올릴 target 수 상한 (W2). 초과분은 폐기하지 않고 사유와 함께
    # 기록한다 — 근거와 그 한계는 `config/budgets.yaml`에 적었다.
    candidate_max: int = Field(gt=0)
    # agent-contracts.md §1의 token_budget.retries. 이 값이 "스키마 파싱 실패율"의
    # 분모를 바꾸므로 조건 간 비교에서 고정되어야 한다 — 어댑터 기본값으로 두지 않는다.
    schema_retries: int = Field(ge=0)
    # 요청 하나의 출력 토큰 상한 (agent-contracts.md §1의 token_budget.output_max).
    # `openai_compat`이 payload의 `"max_tokens"`로 싣는다. 없으면 서버가 `--max-model-len`
    # 까지 생성하고, 2026-08-27 ATOM에서 30B가 실제로 그렇게 폭주해 클라이언트 타임아웃이
    # 났다. `schema_retries`와 같은 이유로 여기 있다 — 조건 간 비교에서 고정되어야 하고
    # `flatten()`이 `RunRecord.thresholds`로 실어야 한다. 어댑터 기본값으로 두지 않는다.
    output_max: int = Field(gt=0)
    capsule_budget_tokens: CapsuleBudgets

    def budget_for(self, level: CapsuleLevel) -> int:
        return int(getattr(self.capsule_budget_tokens, level.value))

    def flatten(self) -> dict[str, float]:
        """`RunRecord.thresholds`(dict[str, float])에 그대로 넣을 수 있는 평탄한 표현.

        중첩 값은 점 표기로 편다(`capsule_budget_tokens.light`). RunRecord가 평탄한
        float 맵을 요구하는 것은 조건 간 비교에서 임계값을 열 단위로 대조하기 위해서다.
        """
        dumped = self.model_dump()
        nested = dumped.pop("capsule_budget_tokens")
        flat = {key: float(value) for key, value in dumped.items()}
        for level, tokens in nested.items():
            flat[f"capsule_budget_tokens.{level}"] = float(tokens)
        return flat


class HarnessConfig(HarnessModel):
    """한 프로필로 해석(resolve)된 설정 전체.

    노드·어댑터는 이 객체만 본다. YAML 파일 경로도, 프로필 이름도 여기서 끝난다.
    """

    profile_name: NonEmptyStr
    profile: ProfileEntry
    models: dict[str, ModelEntry]
    budget: Budget
    thresholds: Thresholds
    sampling: Sampling
    seed: int

    def model_key_for(self, role: str) -> str:
        try:
            return self.profile.roles[role]
        except KeyError as exc:
            raise ConfigError(f"프로필 {self.profile_name!r}에 역할 {role!r}이 없다") from exc

    def model_for(self, role: str) -> ModelEntry:
        return self.models[self.model_key_for(role)]

    def model_checksums(self) -> dict[str, str]:
        """`RunRecord.model_checksums`에 넣을 역할 → checksum 맵."""
        return {role: self.models[key].checksum for role, key in self.profile.roles.items()}

    def prompt_versions(self) -> dict[str, str]:
        """`RunRecord.prompt_versions`에 넣을 역할 → prompt_template_version 맵.

        **`PROMPTED_ROLES`만 담는다.** 전 역할을 담으면 `embedding`·`reranker`처럼
        프롬프트가 존재하지 않는 역할의 "버전"이 실행 기록에 적힌다 — 거짓 정보이고,
        ADR 0004가 `Role` 확장을 기각한 이유가 정확히 이것이다.

        `model_checksums()`는 반대로 전 역할을 담는다. 그 둘은 실제 모델이고 **임베딩
        모델이 바뀌면 검색 결과가 바뀌므로** 재현성 기록에서 뺄 수 없다. `RunRecord`가
        두 dict를 독립 필드로 선언하고 있어 키 집합이 달라도 문제가 없다.
        """
        return {
            role: self.models[self.profile.roles[role]].prompt_template_version
            for role in PROMPTED_ROLES
            if role in self.profile.roles
        }

    def served_models(self) -> dict[str, str]:
        """이 배선에서 **실제로 뜨는** 역할 → model key.

        `model_checksums()`가 전 역할을 담는 것과 대비된다. 저쪽은 기록이라 빠짐이 없어야
        하고, 이쪽은 판정 대상이라 **해시할 대상이 실재하는 역할만** 담아야 한다 —
        `FakeModelClient`는 프로세스 안에서 도는 코드이므로 가중치도 토크나이저도 없고,
        그 checksum이 0인 것은 미기록이 아니라 정확한 값이다.

        역할 단위인 것은 `single_gpu_12g`처럼 한 프로필에서 Fake와 실서빙이 섞이기
        때문이다(그 프로필은 scout만 실서빙이다).

        **거르는 축이 둘이다** (이슈 #108, 선택지 b).

        1. `is_fake` — 위 사유.
        2. `retriever: fake`일 때의 `RETRIEVAL_ROLES` — embedding·reranker 클라이언트는
           hybrid 빌더 안에서만 만들어지므로(`adapters.factory`의 두 지점이 전부
           `adapters.retriever == "hybrid"` 게이트 안이다) 그 프로필에서는 한 번도 뜨지
           않는다. 조건은 바로 위 `ProfileEntry._hybrid_needs_retrieval_roles`가 쓰는 것과
           같다 — 그쪽은 "hybrid면 요구한다"이고 이쪽은 "hybrid가 아니면 안 센다"로,
           같은 사실의 양면이다.

        **선언 자체는 지우지 않는다.** `b1_single`·`local_balanced`가 `retriever: fake`인
        채로 그 두 역할에 실모델을 적어 둔 것은 실수가 아니라 의도다 — hybrid로 올리는 날
        쓸 모델을 적어 둔 것이고, 지금 fake인 이유는 ATOM에 모델 5개를 동시에 띄우면
        R1(sm_121 커널 호환성) 검증의 실패 지점이 늘기 때문이다(`config/profiles.yaml`의
        `local_balanced` retriever 주석). 그 정보를 프로필에서 지우면 나중에 hybrid로
        올릴 때 어느 모델을 쓸지 다시 정해야 한다. 그래서 **기록(`model_checksums`)은
        그대로 두고 판정 대상만 좁힌다.**
        """
        wired = set(self.profile.roles)
        if self.profile.adapters.retriever != "hybrid":
            wired -= set(RETRIEVAL_ROLES)
        return {
            role: key
            for role, key in self.profile.roles.items()
            if role in wired and not self.models[key].is_fake
        }

    def unmeasured_models(self) -> list[str]:
        """benchmark가 비어 있는(실측 전) 모델 키.

        model-serving.md §5: 이런 모델은 production 프로필에 넣지 않는다.
        판정만 하고 차단은 `cli doctor`가 한다 — dev 프로필에서는 정상 상태이기 때문이다.
        """
        return sorted(
            key
            for key in set(self.profile.roles.values())
            if not self.models[key].benchmark.is_measured
        )

    def partially_measured_models(self) -> dict[str, tuple[str, ...]]:
        """**일부만** 잰 모델 키 → 아직 0인 항목.

        `unmeasured_models()`가 "아직 아무것도 안 쟀다"를 답한다면 이쪽은 "쟀는데 덜
        쟀다"를 답한다. 둘을 가르지 않으면 `is_measured`가 `any`이므로 절반만 채운
        모델이 production 게이트를 그냥 통과한다.
        """
        return {
            key: self.models[key].benchmark.missing_measurements
            for key in sorted(set(self.profile.roles.values()))
            if self.models[key].benchmark.is_measured
            and not self.models[key].benchmark.is_fully_measured
        }

    def models_measured_elsewhere(self, device: str) -> dict[str, str]:
        """benchmark를 **다른 장비에서** 잰 모델 키 → 그 장비.

        `unmeasured_models()`와 같은 계층의 판정이다. 저쪽이 "아직 안 쟀다"를 답한다면
        이쪽은 "쟀는데 여기가 아니다"를 답한다 — 둘 다 그 수치로 이 장비를 예측하면 안
        된다는 뜻이지만 대응이 다르다(저쪽은 재야 하고, 이쪽은 알고 쓰면 된다).

        `measured_on`이 `None`인 모델은 세지 않는다. "장비 미상"은 불일치가 아니라
        모름이고, 그것을 불일치로 보고하면 registry를 채우기 전 상태에서 전 모델이
        경고에 걸려 진짜 불일치가 묻힌다.
        """
        measured = (
            (key, self.models[key].benchmark.measured_on)
            for key in set(self.profile.roles.values())
        )
        return {key: on for key, on in sorted(measured) if on is not None and on != device}


def _read_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        raise ConfigError(f"설정 파일이 없다: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}의 최상위는 매핑이어야 한다")
    return data


def _require(mapping: dict[str, object], key: str, where: str) -> dict:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{where}에 매핑 {key!r} 항목이 없다")
    return value


def _check_model_refs(name: str, profile: ProfileEntry, models: dict[str, ModelEntry]) -> None:
    for role, key in profile.roles.items():
        if key not in models:
            raise ConfigError(f"프로필 {name!r}의 역할 {role!r}이 없는 모델 {key!r}을 참조한다")
        fallback = models[key].fallback
        if fallback is not None and fallback not in models:
            raise ConfigError(f"모델 {key!r}의 fallback {fallback!r}이 registry에 없다")


def load_config(profile: str, config_dir: Path = CONFIG_DIR) -> HarnessConfig:
    """프로필 하나를 해석해 `HarnessConfig`로 돌려준다.

    참조가 깨졌거나 필드가 빠지면 여기서 `ConfigError`로 실패한다. 실행 중간에
    KeyError로 터지면 그때까지 쓴 GPU 시간을 버리게 된다.
    """
    models_raw = _read_yaml(config_dir / "models.yaml")
    profiles_raw = _read_yaml(config_dir / "profiles.yaml")
    budgets_raw = _read_yaml(config_dir / "budgets.yaml")

    models = {
        key: ModelEntry.model_validate(value)
        for key, value in _require(models_raw, "models", "models.yaml").items()
    }
    profiles = _require(profiles_raw, "profiles", "profiles.yaml")
    if profile not in profiles:
        raise ConfigError(f"프로필 {profile!r}이 없다. 있는 것: {sorted(profiles)}")
    entry = ProfileEntry.model_validate(profiles[profile])
    _check_model_refs(profile, entry, models)

    budgets = _require(budgets_raw, "budgets", "budgets.yaml")
    if entry.budget not in budgets:
        raise ConfigError(
            f"프로필 {profile!r}이 없는 예산 {entry.budget!r}을 참조한다. "
            f"있는 것: {sorted(budgets)}"
        )

    seed = budgets_raw.get("seed")
    if not isinstance(seed, int):
        raise ConfigError("budgets.yaml에 정수 seed 항목이 없다")

    return HarnessConfig(
        profile_name=profile,
        profile=entry,
        models=models,
        budget=Budget.model_validate(budgets[entry.budget]),
        thresholds=Thresholds.model_validate(_require(budgets_raw, "thresholds", "budgets.yaml")),
        sampling=Sampling.model_validate(_require(budgets_raw, "sampling", "budgets.yaml")),
        seed=seed,
    )
