"""명령줄 진입점 — `doctor` / `estimate` / `run` / `bench` / `gc`.

**어댑터와 오케스트레이터를 잇는 유일한 파일이다.** `src/orchestrator/**`는
`src/adapters/`를 import 하지 않으므로(engineering-guide.md §3), factory가 만든 `Deps`를
풀어 러너에 넘기고 `read_source`·`on_cache` 같은 콜백을 배선하는 일이 여기서 일어난다.

`doctor`는 **엔드포인트에 접속하지 않는다.** 설정 파일과 저장소 상태만 본다 — GPU가 없는
팀원도 돌릴 수 있어야 하고, 네트워크 실패와 설정 오류가 같은 오류로 보이면 안 된다.

**`bench`만 접속한다.** 배포 전 검증 체크리스트(model-serving.md §3)는 실제 서버를 재야
답이 나오므로 그 경계를 여기서 한 번 연다. 나머지 커맨드는 여전히 소켓을 열지 않는다.
"""

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TextIO, get_args

from src.adapters.factory import Deps, build_deps, describe_wiring
from src.adapters.fake_retriever import read_symbol_source
from src.bench import (
    DEFAULT_CONCURRENCY,
    DEFAULT_CONTEXTS,
    DEFAULT_SAMPLES,
    BenchApplyError,
)
from src.bench_report import apply_benchmarks, pending_updates, run_bench
from src.config import (
    CONFIG_DIR,
    PROMPTED_ROLES,
    AdapterSelection,
    ConfigError,
    HarnessConfig,
    load_config,
)
from src.console import force_utf8
from src.ids import MAX_SEQUENCE, SEQ_WIDTH, new_run_id
from src.orchestrator.prompt_loader import PromptError, load_prompt
from src.orchestrator.runner import (
    BLOCKING_WHEN_SERVING,
    DEFAULT_CONDITION,
    DEFAULT_KB_SNAPSHOT,
    LateCacheHook,
    LateIndexHook,
    LateTruncateHook,
    LateUsageHook,
    RunResult,
    RunSpec,
    malformed_condition,
    missing_run_labels,
    run_pipeline,
)
from src.orchestrator.stages import MAX_STAGE
from src.orchestrator.trace import TraceWriter
from src.schemas.common import PLACEHOLDER_DIGEST
from src.schemas.enums import Role
from src.schemas.finding import Finding
from src.tokens import using_counter

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARTIFACTS_ROOT = REPO_ROOT / "artifacts"

# run 디렉터리 접두사. `gc`는 이 접두사를 가진 디렉터리만 지운다.
RUN_DIR_PREFIX = "run_"

# 실측 전 초기값인 임계값 (이슈 #91·#92). 조건 간 비교 전에 확정해야 한다.
PROVISIONAL_THRESHOLDS = ("scout_threshold", "capsule_expansion_max", "requeue_max")

# `--tool-runner`가 받을 수 있는 값. 목록을 여기 적지 않고 설정 스키마에서 뽑는다 —
# 배선이 늘었는데 CLI만 모르는 상태(그 반대도)가 생기지 않는다.
TOOL_RUNNERS: tuple[str, ...] = get_args(AdapterSelection.model_fields["tool_runner"].annotation)

# 4바이트 ≈ 1토큰. 실제 토크나이저가 아니라 추정용 근사다(어댑터와 같은 상수).
_CHARS_PER_TOKEN = 4


class Severity(StrEnum):
    OK = "OK"
    WARN = "WARN"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Check:
    severity: Severity
    message: str

    def render(self) -> str:
        return f"[{self.severity.value:5}] {self.message}"


# --- doctor -----------------------------------------------------------------


def load_profile(
    profile: str,
    *,
    seed: int | None = None,
    tool_runner: str | None = None,
    config_dir: Path = CONFIG_DIR,
) -> HarnessConfig:
    """프로필을 읽고 CLI 오버라이드를 적용한다. **오버라이드가 적용되는 유일한 곳이다.**

    `doctor`·`estimate`·`run`·`bench`가 전부 이 함수를 지난다. 갈래마다 따로 적용하면
    `doctor`가 프로필에 적힌 docker를 검사해 놓고 실제 run은 singularity로 죽는다 —
    "있다고 했는데 없다"는 진단이 가장 비싼 종류의 거짓말이다.

    `seed`와 `tool_runner`가 같은 경로를 쓰는 것은 두 값의 성격이 같기 때문이다: 둘 다
    프로필 바깥에서 들어와 **실제로 쓰인 값**이 되고, 그래서 둘 다 `RunRecord`에 박힌다
    (`seed`, `tool_runner`).
    """
    config = load_config(profile, config_dir=config_dir)
    if seed is not None:
        # config를 갈아 끼운다 — 어댑터도 같은 seed를 봐야 하고 `RunRecord.seed`는
        # "실제로 쓰인 값"이어야 한다.
        config = config.transition(seed=seed)
    if tool_runner is not None:
        # `AdapterSelection`은 frozen이라 새 인스턴스를 만들어 갈아 끼운다. `transition()`은
        # 재검증을 타므로 Literal 밖의 값은 여기서 죽는다(argparse choices와 이중 방어).
        adapters = config.profile.adapters.transition(tool_runner=tool_runner)
        config = config.transition(profile=config.profile.transition(adapters=adapters))
    return config


def doctor(
    profile: str,
    *,
    artifacts_root: Path,
    tool_runner: str | None = None,
    device: str | None = None,
    config_dir: Path = CONFIG_DIR,
) -> list[Check]:
    """설정과 저장소 상태를 점검한다. 접속은 하지 않는다."""
    try:
        config = load_profile(profile, tool_runner=tool_runner, config_dir=config_dir)
    except ConfigError as exc:
        return [Check(Severity.ERROR, f"설정 로드 실패: {exc}")]

    checks = [
        Check(Severity.OK, f"config 3종 파싱 성공 (프로필 {profile!r})"),
        Check(
            Severity.OK,
            f"역할 {len(config.profile.roles)}개가 모두 registry 키로 해석됐다: "
            f"{sorted(config.profile.roles)}",
        ),
    ]
    checks.extend(_check_wiring(config))
    if tool_runner is not None and _tool_runner_overrides(
        profile, tool_runner, config_dir=config_dir
    ):
        checks.append(_override_note(tool_runner))
    checks.append(_check_tool_runtime(config))
    checks.extend(_check_device(config, device))
    checks.extend(_check_benchmarks(config))
    checks.extend(_check_checksums(config))
    checks.extend(_check_prompts(config))
    checks.append(_check_thresholds())
    checks.append(_check_state_path(config, artifacts_root))
    return checks


def _check_wiring(config: HarnessConfig) -> list[Check]:
    """설정이 실제로 무엇을 물리는지 보여준다.

    프로필이 선언한 값이 아니라 **결정된 구현체**를 출력한다. 이게 없으면
    `dev_fake`와 `dev_hybrid`의 진단이 한 글자도 다르지 않아, 설정이 무시되고 있어도
    알아챌 방법이 없다 — `adapters.retriever`가 실제로 그 상태였다.
    """
    endpoints = {role: config.model_for(role).endpoint for role in config.profile.roles}
    return [
        Check(Severity.OK, f"{name:12} → {impl}{_endpoint_note(endpoints.get(name))}")
        for name, impl in describe_wiring(config).items()
    ]


def _tool_runner_overrides(
    profile: str, tool_runner: str | None, *, config_dir: Path = CONFIG_DIR
) -> bool:
    """`--tool-runner` 값이 프로필 **선언값과 실제로 다른가** (PR #55).

    예외 신고의 조건은 "플래그를 넘겼는가"가 아니라 "값이 바뀌었는가"다 — evaluation.md
    §2.3 규칙 2("조건 간 차이는 프로필과 단계 활성화만")에 낸 예외이므로(ADR 0005) 실제로
    예외인 run에만 붙어야 한다. 선언값과 **같은** 값을 넘긴 run은 오버라이드가 아니다:
    `run_20260828_0002`가 `b1_single`(선언 docker)에 `--tool-runner docker`로 돌고도
    "프로필과 다른 런타임" 경고를 달아 보고서 메타를 쓰는 사람을 오도했다. `RunRecord`는
    정확했고 틀린 것은 경고 문구뿐이라, 선언값 비교 한 줄이 문구를 사실로 만든다.

    선언값은 **오버라이드 적용 전** 값이라 `load_profile`을 지난 config로는 못 본다(그쪽은
    이미 갈아 끼운 뒤다). pristine `load_config`에서 읽는다.
    """
    if tool_runner is None:
        return False
    declared = load_config(profile, config_dir=config_dir).profile.adapters.tool_runner
    return tool_runner != declared


def _override_note(runtime: str) -> Check:
    """오버라이드가 걸렸다는 사실 자체를 진단에 남긴다.

    남기지 않으면 `doctor` 출력이 프로필 그대로 돈 것과 한 글자도 다르지 않다. 이건
    evaluation.md §2.3 규칙 2("조건 간 차이는 프로필과 단계 활성화만")에 낸 예외이므로
    (ADR 0005) 눈에 띄어야 한다. `run`은 같은 사실을 `RunRecord.tool_runner`에 박는다.
    """
    return Check(
        Severity.WARN,
        f"--tool-runner {runtime} 오버라이드가 걸렸다 — 프로필에 적힌 런타임 대신 이 값을 "
        "검사한다. 공정성 규칙의 예외이며 run에서는 RunRecord.tool_runner에 남는다 (ADR 0005)",
    )


def _check_tool_runtime(config: HarnessConfig) -> Check:
    """도구 런타임이 이 기계에 있는지 본다.

    `describe_wiring()`은 docker·singularity·podman을 모두 `ContainerToolRunner`로 출력한다 —
    같은 클래스이기 때문이다. 그래서 **어느 런타임인지는 여기서만 드러난다.**

    `shutil.which`는 PATH를 볼 뿐 접속하지 않으므로 "doctor는 엔드포인트에 접속하지
    않는다"(모듈 docstring)와 충돌하지 않는다. 런타임이 없으면 첫 도구 실행이 아니라
    `build_deps()`에서 `SandboxUnavailable`로 끊기는데, 그 사실을 5분 뒤가 아니라
    지금 아는 편이 낫다.
    """
    runtime = config.profile.adapters.tool_runner
    if runtime == "fake":
        return Check(Severity.OK, "tool_runner가 Fake라 컨테이너 런타임이 필요 없다")
    found = shutil.which(runtime)
    if found is None:
        return Check(
            Severity.WARN,
            f"{runtime} 실행 파일이 PATH에 없다 — 이 프로필로 run하면 SandboxUnavailable로 "
            "즉시 끊긴다 (준비 절차는 tools/README.md)",
        )
    return Check(Severity.OK, f"도구 런타임 {runtime} 확인: {found}")


def _endpoint_note(endpoint: str | None) -> str:
    return f"   ({endpoint})" if endpoint else ""


def _check_device(config: HarnessConfig, device: str | None) -> list[Check]:
    """registry의 수치가 **지금 이 장비 기준인가**를 본다.

    `--device`가 없으면 아무 말도 하지 않는다. 라벨이 없으면 비교할 대상이 없고, 없는
    비교를 근거로 경고하면 헛경고가 된다 — 사람이 경고를 안 읽게 만드는 가장 빠른 길이다.
    장비를 안 준 것이 실제로 문제가 되는 시점은 그 값을 registry에 **쓸 때**이므로, 그
    경고는 `bench --apply`가 낸다.

    `_check_wiring`이 "선언된 값이 아니라 결정된 구현체를 출력한다"는 것과 같은 정신으로,
    지금 어느 장비 라벨로 도는지를 진단에 드러낸다.
    """
    if device is None:
        return []
    elsewhere = config.models_measured_elsewhere(device)
    checks = [Check(Severity.OK, f"장비 라벨 {device!r} — run 기록과 --apply에 이 값이 남는다")]
    if elsewhere:
        pairs = ", ".join(f"{key}({on})" for key, on in elsewhere.items())
        checks.append(
            Check(
                Severity.WARN,
                f"registry의 benchmark가 다른 장비 기준이다: {pairs} — 현재 장비는 "
                f"{device!r}다. 이 수치로 이 장비를 예측하지 않는다 (model-serving.md §5)",
            )
        )
    return checks


def _check_benchmarks(config: HarnessConfig) -> list[Check]:
    """**전부 측정 / 부분 측정 / 미측정** 셋을 가른다.

    `Benchmark.is_measured`는 셋 중 하나만 0이 아니어도 참이므로(그 속성 주석), 그것만
    보면 **절반만 채운 모델이 production 게이트를 그냥 통과한다.** ATOM에서
    `mem_gb_ctx8k`를 nvidia-smi로 얻을 수 없다는 것이 확정됐으니(2026-08-17) 그 상태는
    가정이 아니라 곧 실제로 생긴다.

    부분 측정을 production에서 ERROR로 두는 것은 model-serving.md §5의 취지 그대로다 —
    그 게이트가 막으려는 것이 "실측 없이 올리기"이고 절반은 실측이 아니다. 어떤 항목을
    이 장비에서 영영 못 재는 것이 해법을 요구한다면 그 해법은 **대체 소스를 정하는 것**
    이지 게이트를 조용히 통과시키는 것이 아니다.
    """
    unmeasured = config.unmeasured_models()
    partial = config.partially_measured_models()
    if not unmeasured and not partial:
        return [Check(Severity.OK, "프로필의 모든 모델에 benchmark 실측치가 있다")]

    checks: list[Check] = []
    # dev 프로필에서 미측정은 정상 상태이므로 경고로만 남긴다.
    blocking = Severity.ERROR if config.profile.production else Severity.WARN
    suffix = "" if config.profile.production else " (dev 프로필이라 허용)"
    if unmeasured:
        state = "을 production에 올렸다" if config.profile.production else ": "
        checks.append(Check(blocking, f"benchmark 미측정 모델{state}{unmeasured}{suffix}"))
    if partial:
        detail = ", ".join(
            f"{key}({', '.join(missing)} 미측정)" for key, missing in partial.items()
        )
        checks.append(
            Check(
                blocking,
                f"benchmark가 일부만 채워진 모델: {detail} — 이 상태로는 그 항목을 "
                f"뺀 비교만 성립한다{suffix}",
            )
        )
    return checks


def _check_checksums(config: HarnessConfig) -> list[Check]:
    """실서빙 역할의 `checksum`이 자리표시자(전부 0)인가.

    `run`이 끝난 뒤 `incomplete_fields()`가 같은 판정을 하지만, **돌리기 전에 알 수 있는
    사실이다.** 5분(실서빙이면 그보다 훨씬 길다)을 쓰고 나서 "이 run은 비교표에 못 넣는다"를
    듣는 것보다 지금 듣는 편이 싸다 — `_check_prompts`가 같은 이유로 여기 있다.

    **세지 않는 역할이 둘이다.** Fake 역할의 0은 미기록이 아니라 정확한 값이고,
    `retriever: fake`인 프로필의 embedding·reranker는 한 번도 뜨지 않는다
    (`HarnessConfig.served_models` 참조 — 이슈 #108 (b)). `unmeasured_models()`가 fake를 세는 것과
    다른데, 저쪽은 "아직 안 쟀다"를 묻고 이쪽은 "잴 대상이 있는데 비어 있다"를 묻는다.

    ERROR가 아니라 WARN인 것도 의도다 — 막지 않고 기록한다는 이 저장소의 논법을 따른다
    (`Benchmark.measured_on` 주석, ADR 0005·0006).
    """
    served = config.served_models()
    placeholder = sorted(
        # `source`가 있으면 함께 찍는다 — 채우려면 **어느 리포의 어느 리비전인지**를
        # 알아야 하고, 그것이 없다는 사실 자체도 알아야 할 정보다.
        f"{role}({key}, source={config.models[key].source or '미상'})"
        for role, key in served.items()
        if config.models[key].checksum == PLACEHOLDER_DIGEST
    )
    if not served:
        return [
            Check(
                Severity.OK,
                "판정 대상 역할이 없다 (전 역할 Fake이거나 이 배선에서 안 뜬다)"
                " — checksum 검사 대상 없음",
            )
        ]
    if not placeholder:
        return [Check(Severity.OK, "실서빙 역할의 model checksum이 모두 채워져 있다")]
    return [
        Check(
            Severity.WARN,
            f"model checksum이 자리표시자(전부 0)다: {', '.join(placeholder)} — 이대로 run하면 "
            "어느 모델로 돈 기록인지 지시하지 못해 모델 비교표에서 제외된다 "
            "(산출 절차는 model-serving.md §5)",
        )
    ]


def _check_prompts(config: HarnessConfig) -> list[Check]:
    """설정이 가리키는 프롬프트 파일이 실제로 있는지 본다.

    run 도중에 없는 것을 알면 그때까지 쓴 시간을 버린다(config.py의 판단과 같다).
    """
    checks: list[Check] = []
    for role in PROMPTED_ROLES:
        if role not in config.profile.roles:
            continue  # 이 프로필이 안 쓰는 역할(예: single-pass 전용 single)은 건너뛴다
        version = config.model_for(role).prompt_template_version
        try:
            load_prompt(Role(role), version)
        except PromptError as exc:
            checks.append(Check(Severity.ERROR, f"{role} 프롬프트 로드 실패: {exc}"))
        else:
            checks.append(Check(Severity.OK, f"{role} 프롬프트 {version} 확인"))
    return checks


def _check_thresholds() -> Check:
    return Check(
        Severity.WARN,
        f"실측 전 초기값 임계값: {list(PROVISIONAL_THRESHOLDS)} "
        "— 조건 간 비교 전에 확정한다 (이슈 #91·#92)",
    )


def _check_state_path(config: HarnessConfig, artifacts_root: Path) -> Check:
    if config.profile.adapters.state_store != "sqlite":
        return Check(Severity.OK, "state_store가 메모리 구현이라 디스크에 남지 않는다")
    resolved = artifacts_root.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        return Check(
            Severity.WARN,
            f"state DB가 저장소 안에 생긴다: {resolved} — 실행 산출물을 커밋하지 않도록 "
            "--artifacts-root를 저장소 밖에 두거나 .gitignore를 확인한다",
        )
    return Check(Severity.OK, f"state DB 경로가 저장소 밖이다: {resolved}")


# --- 공통 배선 ---------------------------------------------------------------


def execute(
    config: HarnessConfig,
    spec: RunSpec,
    *,
    run_id: str,
    run_dir: Path,
    trace_stream: TextIO | None = None,
) -> tuple[RunResult, Deps]:
    """factory가 만든 어댑터를 러너에 넘긴다. 이 접합부가 이 파일의 존재 이유다.

    `Deps`를 함께 돌려주는 것은 `estimate`가 StateStore에서 캡슐을 세야 하기 때문이다.

    **`run_id`를 만들지 않고 받는다.** 예전에는 여기와 `run()`이 각각 `new_run_id`를
    불렀다. 입력이 같아 우연히 일치했을 뿐이라, 순번이 들어오는 순간 디렉터리 이름과
    `RunRecord.run_id`가 갈라질 자리였다 — 지금 고치는 결함보다 나쁜 상태다.
    """
    hook = LateCacheHook()
    # **이 훅이 빠져 있던 것이 W7이다.** `build_deps()`도 `HybridRetriever`도 받을 준비가
    # 되어 있었는데 여기서 만들어 넘기지 않아, `MAX_CHUNKS` 절단이 아무 데도 안 남았다.
    truncate_hook = LateTruncateHook()
    usage_hook = LateUsageHook()
    index_hook = LateIndexHook()
    trace = TraceWriter(run_id, stream=trace_stream)
    deps = build_deps(
        config,
        run_id=run_id,
        source_root=spec.target,
        artifacts_root=run_dir.parent,
        on_cache=hook,
        on_truncate=truncate_hook,
        on_usage=usage_hook,
        on_index=index_hook,
    )
    result = run_pipeline(
        spec,
        config=config,
        deps=deps,
        # Retriever와 **같은 함수**를 쓴다. 두 곳이 다르게 읽으면 캡슐의 토큰 수와
        # 실제 프롬프트가 어긋난다.
        read_source=lambda target: read_symbol_source(spec.target, target),
        trace=trace,
        run_dir=run_dir,
        cache_hook=hook,
        truncate_hook=truncate_hook,
        usage_hook=usage_hook,
        index_hook=index_hook,
    )
    return result, deps


# --- estimate ---------------------------------------------------------------


def estimate(config: HarnessConfig, target: Path) -> list[str]:
    """0~3단계만 실제로 돌려 예산 대비 추정치를 낸다. 모델을 부르지 않는다."""
    spec = RunSpec(condition=DEFAULT_CONDITION, target=target, dry_run=True)
    with tempfile.TemporaryDirectory() as tmp:
        # 추정은 산출물을 남기지 않는다 — 실행 기록과 섞이면 gc가 무엇을 지울지 흐려진다.
        # 그래서 순번도 **소비하지 않는다.** 임시 디렉터리에는 충돌할 상대가 없으므로
        # `_claim_run_dir`를 거치지 않고 1번을 그대로 쓴다.
        run_dir = Path(tmp) / "estimate"
        result, deps = execute(config, spec, run_id=new_run_id(datetime.now(UTC)), run_dir=run_dir)
        # **조회를 조립과 같은 카운터 안에서 한다 (#84 (a)).**
        #
        # `SqliteStateStore._load`가 `model_validate`로 캡슐을 되돌리므로
        # `CapsuleSection._tokens_match_body`가 **로드 시점에 다시 돈다.** 캡슐은 러너가
        # `using_counter(counter_for_role(scout))` 안에서 조립했는데 이 조회가 그 밖이라,
        # 실 토크나이저 프로필에서는 저장값(실 토크나이저)과 재계산값(글자수 근사)이 갈려
        # `ValidationError`로 죽었다 — `estimate --profile b1_single`이 실제로 그렇게 죽었고
        # 사유는 "callees 섹션의 tokens(30)가 본문에서 계산한 값(18)과 다르다"였다.
        #
        # scout 카운터인 것은 `runner._triage_one`이 LIGHT 캡슐을 그것으로 조립하기
        # 때문이다. 조립과 검증이 같은 카운터를 봐야 한다는 `src/tokens.py`의 불변식을
        # 여기서도 같은 방식으로 지킨다.
        #
        # **⚠ 이것은 임시방편이다.** `src/tokens.py`가 이미 경고한 대로 위험한 자리는
        # 확장 경로 하나가 아니라 **`using_counter` 밖의 모든 `model_validate`**이고, 이
        # 수정은 그중 지금 터진 한 곳을 막을 뿐이다. 정본은 캡슐이 자기 카운터를 싣고
        # 로드가 그것을 복원하는 것이다(#84 (c)) — 그때 이 `with`는 필요 없어진다.
        with using_counter(deps.counter_for_role(Role.SCOUT.value)):
            capsules = deps.store.query("capsule")

    calls = _expected_calls(config, len(capsules))
    tokens = sum(c.used_tokens for c in capsules) + _prefix_tokens(config) * calls
    budget = config.budget.input_tokens
    share = f"{tokens / budget:.1%}" if budget else "예산 0"
    return [
        f"대상 artifact: {len(result.candidates) or len(capsules)}개 / 캡슐 {len(capsules)}개",
        f"예상 모델 호출: 최대 {calls}회 (scout + auditor + critic 상한)",
        f"예상 input 토큰: 약 {tokens:,} / 예산 {budget:,} ({share})",
        _gpu_estimate(config),
    ]


def _expected_calls(config: HarnessConfig, capsules: int) -> int:
    """캡슐 하나당 scout 1회, 승격되면 auditor 1회, 상위만 critic 1회.

    승격률을 모르므로 **상한**을 낸다 — 추정치를 낙관적으로 내면 예산 계획이 무의미해진다.
    """
    return capsules * 2 + min(capsules, config.thresholds.critic_top_n)


def _prefix_tokens(config: HarnessConfig) -> int:
    """호출 1회당 실리는 prefix 토큰의 평균 근사."""
    sizes = [
        len(load_prompt(Role(role), config.model_for(role).prompt_template_version).prefix)
        // _CHARS_PER_TOKEN
        for role in PROMPTED_ROLES
        if role in config.profile.roles
    ]
    return sum(sizes) // len(sizes)


def _gpu_estimate(config: HarnessConfig) -> str:
    """실측치가 없으면 "추정 불가"라고 말한다. 지어낸 숫자는 예산 계획을 망친다."""
    unmeasured = config.unmeasured_models()
    if unmeasured:
        return f"예상 GPU-second: 추정 불가 (benchmark 미측정: {unmeasured})"
    keys = set(config.profile.roles.values())
    rates = [config.models[key].benchmark.tok_s_single for key in keys]
    return f"예상 GPU-second: 최저 처리율 {min(rates):.1f} tok/s 기준으로 산출 가능"


# --- run --------------------------------------------------------------------


def _claim_run_dir(artifacts_root: Path, started_at: datetime) -> tuple[str, Path]:
    """그날의 다음 순번을 **선점하고** (run_id, 디렉터리)를 돌려준다.

    예전에는 `new_run_id()`에 순번을 넘기지 않아 같은 날 모든 run이 `_0001`이었고,
    `mkdir(exist_ok=True)`와 trace의 `"w"` 모드가 겹쳐 두 번째 실행이 앞 run의
    trace·manifest·report를 조용히 덮었다. state.db도 이 디렉터리 안에 있으므로
    (`adapters/factory.py`) 객체 행까지 같이 덮였다.

    번호를 훑어 max+1을 고르는 것만으로는 부족하다 — 훑기와 만들기 사이에 다른 프로세스가
    같은 번호를 가져갈 수 있다. 그래서 `exist_ok=False`로 **원자적으로 선점**하고
    `FileExistsError`면 다음 번호로 재시도한다. 덮어쓰기를 구조적으로 불가능하게 만드는
    것이 요점이지, 번호를 잘 고르는 것이 아니다.

    알려진 한계: `gc`가 지운 번호를 같은 날 새 run이 재사용한다. v1에서는 막지 않는다 —
    위험과 해법 후보는 `docs/open-questions/R/R7.md` 에 있다.
    """
    for sequence in range(_next_sequence(artifacts_root, started_at), MAX_SEQUENCE + 1):
        run_id = new_run_id(started_at, sequence)
        run_dir = artifacts_root / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue  # 훑은 뒤 누가 가져갔다. 다음 번호로.
        return run_id, run_dir
    raise SystemExit(f"하루 상한 {MAX_SEQUENCE}개를 넘겼다: {artifacts_root}")


def _next_sequence(artifacts_root: Path, started_at: datetime) -> int:
    """그날 이미 쓰인 순번의 max+1. 없으면 1.

    1부터 하나씩 시도해도 결과는 같지만, run이 쌓이면 매번 그만큼 mkdir을 시도하게 된다.
    """
    prefix = new_run_id(started_at, 0)[:-SEQ_WIDTH]
    if not artifacts_root.is_dir():
        return 1
    used = [
        int(suffix)
        for path in artifacts_root.iterdir()
        if path.is_dir() and path.name.startswith(prefix)
        for suffix in [path.name[len(prefix) :]]
        if suffix.isdigit()
    ]
    return max(used, default=0) + 1


def run(args: argparse.Namespace) -> int:
    config = load_profile(args.profile, seed=args.seed, tool_runner=args.tool_runner)
    # **`_claim_run_dir`보다 앞이다.** 뒤에 두면 아무것도 안 한 run이 디렉터리와 번호를
    # 남기고, 그 번호는 R7(`gc`가 지운 run_id를 같은 날 새 run이 재사용한다)이 서 있는 자리와
    # 같다. 그리고 이 판정은 비용을 쓰기 **전에** 들려야 값어치가 있다 —
    # `_check_checksums`가 doctor에서 같은 논법을 적어 뒀다.
    #
    # **문장은 `incomplete_fields()`와 같고 접두사만 다르다.** 같아야 하는 이유는 두 곳이
    # 갈라지면 안 되기 때문이고(`missing_run_labels` docstring), 그래서 device·kb_snapshot은
    # 한 실행에서 두 번 뜬다. 시점을 접두사로 갈라 둔다 — 앞의 것은 "이렇게 시작한다"이고
    # 뒤의 것은 "이 run은 제외된다"라, 같은 줄이 두 번 뜨는 것으로 읽히면 안 된다.
    gaps = missing_run_labels(
        device=args.device, kb_snapshot=args.kb_snapshot, condition=args.condition
    )
    for warning in gaps:
        print(f"[WARN ] 시작 전 — {warning}", file=sys.stderr)
    # **실서빙이면 경고로 끝내지 않는다.** 경고 한 줄은 몇 시간짜리 run의 출력에 묻히고,
    # 그러면 이 검사가 고치려던 것("태우고 나서 듣는다")이 그대로 남는다. Fake로 도는
    # 프로필은 막지 않는다 — dev 스모크는 장비 라벨 없이 도는 것이 정상이다.
    # **형식은 프로필과 무관하게 막는다.** 결여는 실서빙에서만 거부하지만(위 주석), 형식에
    # 안 맞는 라벨은 어느 프로필에서든 0단계 뒤 `_new_record`에서 `ValidationError`로 죽어
    # 빈 run 디렉터리를 남긴다 — 정책 선택이 아니라 반드시 죽을 값에 대한 빠른 실패다.
    malformed = malformed_condition(args.condition)
    if malformed is not None:
        print(f"[ERROR] {malformed}", file=sys.stderr)
        return 2
    serving = bool(config.served_models())
    blocking = [gap for gap in gaps if gap in BLOCKING_WHEN_SERVING] if serving else []
    if blocking:
        print(
            f"[ERROR] 실서빙 프로필({args.profile})이라 위 {len(blocking)}건을 주지 않으면 "
            "시작하지 않는다 — 이 run은 끝까지 돌아도 비교표에 넣을 수 없다. "
            "--device 와 --condition 을 명시한다",
            file=sys.stderr,
        )
        return 2
    started_at = datetime.now(UTC)
    run_id, run_dir = _claim_run_dir(args.artifacts_root, started_at)
    spec = RunSpec(
        condition=DEFAULT_CONDITION if args.condition is None else args.condition,
        target=args.target,
        stop_after=args.stop_after,
        dry_run=args.dry_run,
        kb_snapshot=args.kb_snapshot,
        device=args.device,
        started_at=started_at,
    )
    with (run_dir / "trace.jsonl").open("w", encoding="utf-8") as stream:
        result, _ = execute(config, spec, run_id=run_id, run_dir=run_dir, trace_stream=stream)
    report_path = write_report(run_dir, result, args.format)

    print(f"run_id: {result.record.run_id}")
    print(f"산출물: {run_dir}")
    print(f"리포트: {report_path.name}")
    print(f"후보 {len(result.candidates)}건 / Finding {len(result.findings)}건")
    print(f"완주 여부: {'완주' if result.record.is_complete else '부분 결과'}")
    if _tool_runner_overrides(args.profile, args.tool_runner):
        print(
            f"[WARN ] 프로필과 다른 도구 런타임으로 돌았다: {args.tool_runner}. "
            "evaluation.md §2.3 규칙 2의 예외이며 RunRecord.tool_runner에 남는다 (ADR 0005)",
            file=sys.stderr,
        )
    for warning in result.warnings:
        print(f"[WARN ] {warning}", file=sys.stderr)
    return 0


def write_report(run_dir: Path, result: RunResult, fmt: str) -> Path:
    if fmt == "sarif":
        path = run_dir / "report.sarif.json"
        path.write_text(json.dumps(to_sarif(result), ensure_ascii=False, indent=2), "utf-8")
        return path
    path = run_dir / "report.md"
    path.write_text(result.report.render(), encoding="utf-8")
    return path


def to_sarif(result: RunResult) -> dict:
    """SARIF 2.1.0 최소 형태. 외부 도구에 넘길 때 쓴다."""
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {"driver": {"name": "whitehat-boss", "rules": []}},
                "automationDetails": {"id": result.record.run_id},
                "results": [_sarif_result(finding) for finding in result.report.findings],
            }
        ],
    }


def _sarif_result(finding: Finding) -> dict:
    return {
        "ruleId": finding.cwe,
        "level": "warning",
        "message": {"text": f"{finding.title} (state={finding.state.value})"},
        "properties": {
            "findingId": finding.finding_id,
            "confidence": finding.confidence,
            "evidenceRefs": list(finding.evidence_refs),
        },
    }


# --- bench ------------------------------------------------------------------


def bench(args: argparse.Namespace) -> int:
    """§3 체크리스트를 실제 엔드포인트에 대고 잰다. **접속하는 유일한 커맨드다.**

    종료 코드는 "bench 자신이 제대로 돌았는가"만 뜻한다. 측정 결과의 통과·실패는 JSON의
    `checklist`에 있다 — 체크리스트 실패를 종료 코드로 옮기면 서버가 없는 개발 장비에서
    CI가 빨개지고, 그러면 아무도 이 커맨드를 돌리지 않게 된다.
    """
    config = load_profile(args.profile, tool_runner=args.tool_runner)
    report = run_bench(
        config,
        roles=args.roles,
        samples=args.samples,
        concurrency=args.concurrency,
        contexts=args.contexts,
    )
    code = 0
    if args.apply:
        if args.device is None:
            # 여기가 장비를 안 준 것이 실제로 문제가 되는 시점이다. `doctor`는 라벨이
            # 없으면 조용히 넘어가지만(`_check_device`), registry에 **쓰는** 순간에는
            # 그 수치가 영구히 장비 미상으로 남는다. stderr로 낸다 — stdout의 JSON이
            # 파싱 가능해야 한다.
            print(
                "[WARN ] --device 없이 --apply 했다. 이 수치가 어느 장비 것인지 "
                "registry에 기록되지 않는다 (measured_on: null). "
                "두 장비가 모두 지원 대상이므로 다음 사람이 이 값으로 다른 장비를 "
                "예측하게 된다 (docs/portability.md)",
                file=sys.stderr,
            )
        updates, skipped = pending_updates(report)
        outcome: dict[str, object] = {"skipped": skipped}
        try:
            outcome |= apply_benchmarks(
                CONFIG_DIR / "models.yaml",
                updates,
                str(report["measured_at"]),
                measured_on=args.device,
            )
        except BenchApplyError as exc:
            # 부분적으로 망가진 registry가 갱신 실패보다 훨씬 비싸다. 아무것도 쓰지 않았다.
            outcome |= {"error": str(exc), "updated": {}}
            code = 1
        report["apply"] = outcome
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


# --- gc ---------------------------------------------------------------------


def gc(args: argparse.Namespace) -> int:
    """오래된 run 디렉터리를 지운다. **기본은 dry-run이다.**"""
    root: Path = args.artifacts_root
    if not root.is_dir():
        print(f"artifacts 루트가 없다: {root}")
        return 0
    runs = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith(RUN_DIR_PREFIX))
    doomed = runs[: max(0, len(runs) - args.keep)]
    if not doomed:
        print(f"run {len(runs)}개, 보관 기준 {args.keep}개 — 지울 것이 없다")
        return 0
    for path in doomed:
        print(f"{'삭제' if args.apply else '삭제 예정'}: {path}")
    if not args.apply:
        print("실제로 지우려면 --apply를 붙인다 (기본은 dry-run)")
        return 0
    if not args.yes and not _confirm(len(doomed)):
        print("취소했다")
        return 0
    for path in doomed:
        shutil.rmtree(path)
    print(f"{len(doomed)}개 삭제했다")
    return 0


def _confirm(count: int) -> bool:
    answer = input(f"{count}개 run 디렉터리를 삭제한다. 계속하려면 'yes': ")
    return answer.strip().lower() == "yes"


# --- 진입점 -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    subs = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", required=True)
    common.add_argument("--artifacts-root", type=Path, default=DEFAULT_ARTIFACTS_ROOT)
    # 기본값 None = 프로필 존중. 같은 프로필을 Docker가 있는 장비와 Singularity만 되는
    # 장비에서 함께 돌리기 위한 것이다 (ADR 0005).
    common.add_argument("--tool-runner", choices=TOOL_RUNNERS, default=None)

    # `--device`는 estimate·gc에 달지 않는다. 받아 놓고 아무 데도 안 쓰는 플래그는
    # "이 실행에 장비가 기록됐다"는 인상만 준다 — `--seed`가 run에만 있는 것과 같은 판단이다.
    device = argparse.ArgumentParser(add_help=False)
    device.add_argument(
        "--device",
        default=None,
        help="이 실행이 돌아간 장비의 라벨. run 기록과 --apply에 그대로 남는다. "
        "값 집합은 열려 있다(ADR 0006) — 팀이 쓰는 라벨은 docs/portability.md에 있다. "
        "호스트명으로 자동으로 채우지 않는다: 안 주면 '확인 못 함'이다",
    )

    subs.add_parser("doctor", parents=[common, device])

    est = subs.add_parser("estimate", parents=[common])
    est.add_argument("--target", type=Path, required=True)

    run_cmd = subs.add_parser("run", parents=[common, device])
    run_cmd.add_argument("--target", type=Path, required=True)
    # **기본값을 argparse에 두지 않는다.** 값은 `run()`이 `DEFAULT_CONDITION`으로 채우되,
    # `None`이 여기까지 살아 와야 "안 줬다"와 "기본값과 같은 라벨을 의도해서 줬다"가
    # 구별된다 — `runner.missing_run_labels`가 그 구분 위에 서 있다.
    run_cmd.add_argument(
        "--condition",
        default=None,
        help=f"조건 라벨. 안 주면 {DEFAULT_CONDITION}으로 기록되고 그 사실을 경고한다",
    )
    run_cmd.add_argument("--seed", type=int)
    run_cmd.add_argument("--stop-after", type=int, choices=range(MAX_STAGE + 1))
    run_cmd.add_argument("--dry-run", action="store_true")
    run_cmd.add_argument("--format", choices=("md", "sarif"), default="md")
    run_cmd.add_argument("--kb-snapshot", default=DEFAULT_KB_SNAPSHOT)

    bench_cmd = subs.add_parser("bench", parents=[common, device])
    bench_cmd.add_argument("--roles", nargs="+", default=None)
    bench_cmd.add_argument("--contexts", nargs="+", type=int, default=list(DEFAULT_CONTEXTS))
    bench_cmd.add_argument("--concurrency", nargs="+", type=int, default=list(DEFAULT_CONCURRENCY))
    bench_cmd.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    bench_cmd.add_argument("--apply", action="store_true")

    gc_cmd = subs.add_parser("gc")
    gc_cmd.add_argument("--keep", type=int, default=5)
    gc_cmd.add_argument("--artifacts-root", type=Path, default=DEFAULT_ARTIFACTS_ROOT)
    gc_cmd.add_argument("--apply", action="store_true")
    gc_cmd.add_argument("--yes", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8(sys.stdout)
    force_utf8(sys.stderr)
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        checks = doctor(
            args.profile,
            artifacts_root=args.artifacts_root,
            tool_runner=args.tool_runner,
            device=args.device,
        )
        for check in checks:
            print(check.render())
        return 1 if any(c.severity is Severity.ERROR for c in checks) else 0
    if args.command == "estimate":
        config = load_profile(args.profile, tool_runner=args.tool_runner)
        for line in estimate(config, args.target):
            print(line)
        return 0
    if args.command == "run":
        return run(args)
    if args.command == "bench":
        return bench(args)
    return gc(args)


if __name__ == "__main__":
    raise SystemExit(main())
