"""명령줄 진입점 — `doctor` / `estimate` / `run` / `bench` / `gc` / `preflight`.

**어댑터와 오케스트레이터를 잇는 유일한 파일이다.** `src/orchestrator/**`는
`src/adapters/`를 import 하지 않으므로(engineering-guide.md §3), factory가 만든 `Deps`를
풀어 러너에 넘기고 `read_source`·`on_cache` 같은 콜백을 배선하는 일이 여기서 일어난다.

`doctor`는 **엔드포인트에 접속하지 않는다.** 설정 파일과 저장소 상태만 본다 — GPU가 없는
팀원도 돌릴 수 있어야 하고, 네트워크 실패와 설정 오류가 같은 오류로 보이면 안 된다.

**`bench`와 `preflight`는 접속 자체가 일인 진단 명령이다.** 배포 전 검증 체크리스트(model-serving.md
§3)는 실제 서버를 재야 답이 나오므로 그 경계를 여기서 한 번 연다. `preflight`(#534)는 run 전에
ssh·TCP·`docker run`으로 선결조건을 보므로 `doctor`에 못 두고 별 명령이다. `run`도 접속한다 —
모델 서버·채점기·교대 명령에 닿는 것이 파이프라인을 도는 일이다. 소켓을 열지 않는 것은 `doctor`·
`estimate`·`gc`다.

종료 코드 — `run`이 **어떻게 멈췄는지**를 stdout 문구가 아니라 이 값으로 가른다(#533).
점수가 났는지·비교표에 서는지는 이 값이 말하지 않는다.

| 코드 | 뜻 |
| --- | --- |
| 0 | 정상. `run`은 완주·`--stop-after`·`--dry-run`·예산 소진이다. 후보·Finding 0건도 0이다 |
| 1 | `doctor` ERROR · `bench --apply` 실패 · 트레이스백으로 죽음(①) |
| 2 | 입력·설정 거부 — 모델을 부르기 전에 멈췄다. `preflight`도 어긋난 선결조건(ERROR)에 2다 |
| 3 | `run`이 장애로 중단됐다 — 교대 실패·게이트웨이 장애·연속 폐기·스키마 파싱 실패 |
| 128+N | TERM(143)·HUP(129)로 끝났다 — 매니페스트는 안 남는다(②) |

- **0은 점수가 났다는 뜻이 아니다.** 채점을 켠 run은 매니페스트 `oracle.final`(`scored`·`failed`·
  `none`·`not_reached`)을 본다. **0은 비교표 적격도 아니다** — 낮은 `--stop-after`·`--dry-run`·예산
  소진은 `stopped_after`가 차서 `RunRecord.is_complete`가 거짓이고, 기록이 불완전한 run도 빠진다
  (`docs/evaluation.md` §6).
- **2에는 프롬프트 배선 어긋남이 든다**(⏵ 2026-10-13, #600 H3). `run`은 `doctor`와 같은 검사
  (`_check_prompts`)의 ERROR — 프롬프트 로드 실패 · 내용 인식 집합 밖의 판 — 에 모델을 부르기 전에
  2로 선다. 그 run이 닿는 단계의 역할만 본다. WARN은 막지 않고, `--dry-run`(3단계에서 섬)은 일부러
  가드 밖이다.
- ① 트레이스백: `run`의 예상 밖 예외(`UNFORESEEN_FAILURE` — 러너가 매니페스트를 남기고 다시
  던진다) · `_claim_run_dir`의 하루 상한 초과(설계된 `SystemExit`) ·
  `estimate`·`bench`의 `ConfigError`.
- ② TERM·HUP는 `_terminate_as_exit`가 `SystemExit(128+N)`으로 바꾼다. **Ctrl-C(SIGINT)는 안 바꾼다**
  — `KeyboardInterrupt`가 올라가고 값은 인터프리터가 정한다(POSIX 130 · Windows 네이티브
  `0xC000013A`, 2026-10-07 실측. 이 저장소의 검사는 그 값을 안 문다).
- **`preflight --strict`의 4는 이 표 밖이다** — ERROR 없이 WARN만 있을 때(`EXIT_UNVERIFIED`:
  건너뛴 점검이 있어 **확인하지 못했다**)이고 그 명령만 쓰는 값이다.

⚠ **3은 프로그램마다 따로다 — 이 표의 3은 다른 도구의 3과 같은 사건이 아니다.** `src.cli`에서 3은
`EXIT_RUN_FAULT` 하나뿐이다. `tools/monitor.py`의 3은 DEAD(매니페스트 없이 heartbeat가 낡음)·
CRASHED(예상 밖 예외 — 이 표에서는 1)이고, 위 장애 넷으로 멈춘 run을 `monitor`는 0으로 읽는다.
`tools/oracle_canary.py`(weak_pass)·`tools/slice_sharded.py`(후보 미완)·`tools/serve/swap.sh`(서버
기동·내림 실패)의 3도 각자의 사건이다. 운영 스크립트가 두 3을 한 사건으로 읽지 않는다.
"""

import argparse
import ipaddress
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import urllib.parse
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TextIO, get_args

from src.adapters.canary_report import (
    CanaryReport,
    CanaryReportError,
    canary_refusal,
    read_canary_report,
)
from src.adapters.container_tools import PocTarget, make_payload_reader, poc_target_for
from src.adapters.cybergym_oracle import (
    TaskOracle,
    checksum,
    refused_family_task_id,
    unsupported_task_id,
)
from src.adapters.factory import Deps, build_deps, describe_wiring
from src.adapters.fake_retriever import read_raw_source, read_symbol_source
from src.adapters.slices import SliceInputError, SliceSet, load_slices, render_slices
from src.adapters.tokenizer import REASON_NO_SOURCE, counter_reason_for_source
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
    RELAYED_SOURCE_OPTIONS,
    SCOUT_POOL_ROLES,
    AdapterSelection,
    ConfigError,
    HarnessConfig,
    check_container_runtime_caps_tool_time,
    check_serving,
    load_config,
    load_serving,
)
from src.console import force_utf8
from src.ids import MAX_SEQUENCE, SEQ_WIDTH, new_run_id
from src.interfaces.oracle import Oracle
from src.orchestrator import prompt_loader
from src.orchestrator.nodes import auditor, critic, pov
from src.orchestrator.nodes.auditor import loop_problem as auditor_loop_problem
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
from src.orchestrator.stages import MAX_STAGE, Stage
from src.orchestrator.task_loader import (
    DESCRIPTION_FILENAME,
    REPO_ARCHIVE_NAME,
    SUBMIT_SCRIPT_NAME,
    UnexpectedTaskFilesError,
    UnsafeArchiveError,
    check_task_dir,
    read_description,
    read_submit_metadata,
    safe_extract,
)
from src.orchestrator.trace import TraceWriter
from src.schemas.common import PLACEHOLDER_DIGEST
from src.schemas.enums import ReasonCode, Role
from src.schemas.finding import Finding
from src.tokens import COUNTED_ROLES, using_counter

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARTIFACTS_ROOT = REPO_ROOT / "artifacts"

# run 디렉터리 접두사. `gc`는 이 접두사를 가진 디렉터리만 지운다.
RUN_DIR_PREFIX = "run_"

# 실측 전 초기값인 임계값 (이슈 #91·#92 — 둘 다 닫혔다, 값은 여전히 안 잰 초기값).
# 조건 간 비교 전에 확정해야 한다. `pov_tool_hops`(#511, 2026-10-12 닫음)는 값이
# 16(사용자 결정 2026-10-06)이나 효과 미실측이라 같은 자리다.
# ⏵ 2026-10-13 - `capsule_expansion_max`를 이 목록에서 뺐다(#78). 그 값을 읽는 정책 결정점은 코드에
# 남아 있으나 러너가 부르는 곳이 없다(ADR 0012 결정 6이 확장 경로를 `max_hops` 루프로 대체) -
# 어느 run도 그 값에 좌우되지 않는데 doctor가 「확정하라」고 말하는 것은 거짓 경고다. 코드 삭제는
# 별 작업이다.
PROVISIONAL_THRESHOLDS = (
    "scout_threshold",
    "requeue_max",
    "max_hops",
    "pov_tool_hops",
)

# `--tool-runner`가 받을 수 있는 값. 목록을 여기 적지 않고 설정 스키마에서 뽑는다 —
# 배선이 늘었는데 CLI만 모르는 상태(그 반대도)가 생기지 않는다.
TOOL_RUNNERS: tuple[str, ...] = get_args(AdapterSelection.model_fields["tool_runner"].annotation)

# 4바이트 ≈ 1토큰. 실제 토크나이저가 아니라 추정용 근사다(어댑터와 같은 상수).
_CHARS_PER_TOKEN = 4

# `run`의 거부와 `doctor`의 경고가 같은 문장이다 — 갈라지면 doctor가 run과 다른 말을 한다.
_SWAP_NEEDS_DEVICE = (
    "교대를 선언한 프로필이라 run은 --device 없이 모델에 닿지 않는다 — 장비 파일 "
    "config/serving/<device>.yaml이 교대 명령과 서버 기동 조건을 든다 (ADR 0012 Phase 6.5)"
)


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
        # 오버라이드는 `load_config`의 가드 **뒤에** 런타임을 바꾼다 - 0 예산 위의 컨테이너를
        # 여기서 다시 막는다(이슈 #436).
        check_container_runtime_caps_tool_time(config)
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
    checks.extend(_check_serving(config, device, config_dir))
    checks.extend(_check_benchmarks(config))
    checks.extend(_check_checksums(config))
    checks.extend(_check_tokenizers(config))
    checks.extend(_check_prompts(config))
    checks.extend(_check_source_options(config))
    checks.extend(_check_auditor_loop(config))
    checks.extend(_check_score_chain(config))
    checks.extend(_check_scout_slice_trim(config))
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


# 개수 가드 에러가 이름을 대는 안 맞은 슬라이스의 최대 수 — 나머지는 개수로만 센다.
_UNMATCHED_SLICES_SHOWN = 5


def _unmatched_slices_error(
    slice_set: SliceSet, rendered: dict[str, str], work_dir: Path
) -> str | None:
    """`render_slices`가 **건너뛴** 슬라이스가 있으면 그 사유, 없으면 `None`.

    `render_slices`는 entry 파일이 트리에 없는 슬라이스를 오류 없이 뺀다 — 러너가 그것을
    `unmatched_slices`로 셀 뿐이라 경로 기준이 어긋난 `slices.json`은 `후보 0건 / 완주`로 끝난다.
    `--task-dir`는 해제 트리가 번들 **최상위**라 슬라이서를 그 안의 `src-vul/`에서 돌린
    슬라이스(접두어 없음)가 그 모양이 된다(`--target`은 사람이 루트를 골라 안 난다)."""
    unmatched = [item.id for item in slice_set.slices if item.id not in rendered]
    if not unmatched:
        return None
    shown = ", ".join(unmatched[:_UNMATCHED_SLICES_SHOWN])
    if len(unmatched) > _UNMATCHED_SLICES_SHOWN:
        shown += f" 외 {len(unmatched) - _UNMATCHED_SLICES_SHOWN}건"
    return (
        f"슬라이스 {len(slice_set.slices)}건 중 {len(unmatched)}건이 풀린 트리에 없다({shown}) — "
        "슬라이스 경로는 작업 디렉터리 기준(번들 최상위 디렉터리 `src-vul/` 포함)이어야 한다. "
        "슬라이서를 그 디렉터리 안에서 돌리지 않았는지, 다른 과제의 slices.json이 아닌지 본다 — "
        f"해제 트리가 {work_dir}에 남았다. 비우고 다시 한다"
    )


def _serving_error(config: HarnessConfig, device: str, config_dir: Path) -> str | None:
    """장비 파일을 읽어 registry·교대 선언과 대조한다. 어긋나면 그 사유다 — `run`·`doctor` 공용."""
    try:
        check_serving(config, load_serving(device, config_dir=config_dir))
    except ConfigError as exc:
        return f"{exc}"
    return None


def _check_serving(config: HarnessConfig, device: str | None, config_dir: Path) -> list[Check]:
    """교대 프로필의 장비 파일 — `run`이 모델에 닿기 전에 거부할 것을 미리 말한다.

    **`--device`가 없으면 WARN이다.** doctor는 run이 모델에 닿는지 모르고(`--dry-run`은 안
    막는다), ERROR면 설정이 멀쩡한 교대 프로필의 doctor가 exit 1이 된다 — `_check_prompts`의
    슬라이스 경고와 같은 판단이다. **장비를 줬으면 ERROR다** — run과 같은 판정이고 그 run은
    시작하지 않는다. 교대 없는 프로필에는 말하지 않는다(전 프로필에 뜨는 줄은 안 읽힌다).
    """
    if config.swap is None:
        return []
    if device is None:
        return [Check(Severity.WARN, _SWAP_NEEDS_DEVICE)]
    problem = _serving_error(config, device, config_dir)
    if problem is not None:
        return [Check(Severity.ERROR, problem)]
    return [
        Check(
            Severity.OK,
            f"장비 파일 config/serving/{device}.yaml — registry·교대 선언과 맞는다",
        )
    ]


def _check_benchmarks(config: HarnessConfig) -> list[Check]:
    """**전부 측정 / 부분 측정 / 미측정** 셋을 가른다.

    `Benchmark.is_measured`는 셋 중 하나만 0이 아니어도 참이므로(그 속성 주석), 그것만
    보면 **절반만 채운 모델이 production 게이트를 그냥 통과한다.** ATOM에서
    `mem_gb_ctx8k`를 `nvidia-smi --query-gpu`로 얻을 수 없다는 것이 확정됐으니
    (2026-08-17) 그 상태는 가정이 아니라 곧 실제로 생긴다 — 쿼리를 적는 이유는
    `config.Benchmark.is_measured` 주석과 같다.
    ⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340). vast에서 이 필드가 채워지는지는
    미실측이다(`config.Benchmark.is_measured`의 같은 ⏵).

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


def _check_tokenizers(config: HarnessConfig) -> list[Check]:
    """실서빙 역할의 토크나이저를 쓸 수 있는가 — 없으면 토큰 수가 `len//4` 근사다.

    run에서는 같은 판정이 `token_counter` 이벤트(`approx`와 사유)로만 남아, Auditor SOURCE 20,000
    토큰 같은 상한이 실서빙보다 1.8배 과소하게 세어진 채 도는 것을 run 뒤에야 안다(CLAUDE.md §5).
    HF 캐시 파일만 본다 - 접속하지 않는다. **토큰을 세는 역할만 본다** — `COUNTED_ROLES`
    (scout·auditor)와 Scout 풀 멤버. critic·pov는 카운터를 안 켜 토크나이저가 없어도 run이
    달라지지 않고, embedding·reranker는 프롬프트를 안 만든다.

    파일이 있는데 못 읽으면(깨짐) run도 같은 예외로 죽으므로 ERROR다.
    """
    counted = {*COUNTED_ROLES, *SCOUT_POOL_ROLES}
    missing: list[str] = []
    broken: list[str] = []
    for role, key in sorted(config.served_models().items()):
        if role not in counted:
            continue
        source = config.models[key].source
        try:
            reason = counter_reason_for_source(source) if source else REASON_NO_SOURCE
        except Exception as exc:  # noqa: BLE001 - 어떤 로더 예외든 doctor를 죽이지 않고 보고한다
            broken.append(f"{role}({type(exc).__name__}: {exc})")
            continue
        if reason is not None:
            missing.append(f"{role}({reason})")
    checks: list[Check] = []
    if broken:
        checks.append(
            Check(
                Severity.ERROR,
                f"토크나이저 파일을 읽지 못한다: {', '.join(broken)} — run도 같은 예외로 "
                "죽는다. HF 캐시를 다시 받는다",
            )
        )
    if missing:
        checks.append(
            Check(
                Severity.WARN,
                f"실서빙 역할의 토크나이저를 못 쓴다: {', '.join(missing)} — 토큰 상한이 "
                "글자수 근사(len//4)로 센다. 실서빙보다 최대 1.8배 과소라 상한을 넘은 요청이 "
                "서버에서 거절될 수 있다",
            )
        )
    return checks or [
        Check(Severity.OK, "실서빙 역할의 토크나이저를 모두 쓸 수 있다(또는 대상 없음)")
    ]


# 역할이 처음 모델을 부르는 단계 — 적히지 않은 역할(Scout 계열·single)은 `SCOUT`이다.
_PROMPT_FIRST_STAGE = {"auditor": Stage.AUDITOR, "critic": Stage.CRITIC, "pov": Stage.VALIDATE}


def _check_prompts(config: HarnessConfig, *, last_stage: Stage = MAX_STAGE) -> list[Check]:
    """프롬프트 파일의 실재·내용 인식 배선·디스크의 새 판을 본다.

    run 도중에 없는 것을 알면 그때까지 쓴 시간을 버린다(config.py의 판단과 같다).

    **`doctor`와 `run`이 이 함수를 같이 부른다**(⏵ 2026-10-13, #600 H3). `run`은 `last_stage`에
    그 run이 닿는 마지막 단계를 넘겨, 안 부를 역할의 프롬프트는 보지 않는다(`--stop-after 4` run이
    8단계 PoV 프롬프트 때문에 막히지 않는다 — Auditor 루프 가드와 같은 판단). `doctor`는 기본값
    (전 단계)이다. `run`은 이 중 ERROR만 막고 WARN은 막지 않는다.

    **슬라이스 입력 프롬프트는 WARN 한 줄을 더한다** — `run`은 그것을 `--slices` 없이 시작하지
    않는다(ADR 0012 결정 1). ERROR가 아닌 것은 doctor가 `--slices`를 받지 않아 막을 대상이
    없어서다 — ERROR면 설정이 멀쩡한 프로필의 doctor가 exit 1이 된다.
    """
    checks: list[Check] = []
    for role in PROMPTED_ROLES:
        if role not in config.profile.roles:
            continue  # 이 프로필이 안 쓰는 역할(예: single-pass 전용 single)은 건너뛴다
        if _PROMPT_FIRST_STAGE.get(role, Stage.SCOUT) > last_stage:
            continue  # 이 run이 그 단계에 닿지 않는다
        version = config.model_for(role).prompt_template_version
        try:
            load_prompt(Role(role), version)
        except PromptError as exc:
            checks.append(Check(Severity.ERROR, f"{role} 프롬프트 로드 실패: {exc}"))
            continue
        checks.append(Check(Severity.OK, f"{role} 프롬프트 {version} 확인"))
        checks.extend(_check_prompt_version(config, role, version))
    try:
        slice_input = _slice_input_prompts(config)
    except PromptError:
        slice_input = []  # 로드 실패는 위 루프가 이미 ERROR로 댄다(`_check_source_options`와 같다)
    if slice_input:
        checks.append(
            Check(
                Severity.WARN,
                f"슬라이스 입력 프롬프트({', '.join(slice_input)})라 run은 --slices 없이 "
                "시작하지 않는다 (ADR 0012 결정 1)",
            )
        )
    return checks


def _check_prompt_version(config: HarnessConfig, role: str, version: str) -> list[Check]:
    """내용 인식 배선은 실모델 역할에서 ERROR, 디스크의 새 판은 WARN.

    내용 인식 집합의 정본은 각 노드다. Scout·Single에는 그 집합이 없어 대상이 아니다.
    Fake 여부는 `ModelEntry.is_fake`로 가른다 — 공유 엔트리의 한 역할만 범프하면 다른 역할의
    파일이 없어질 수 있다(#172, 2026-10-12 닫음). Fake는 정해진 답을 내므로 낡은 판도
    ERROR로 막지 않는다.
    디스크의 순서는 정수 `vN`만 비교한다. `v4_t1` 같은 실험 이름의 우열은 추측하지 않는다.
    """
    versions = {
        "critic": critic._CONTENT_AWARE_VERSIONS,
        "pov": pov._WIDE_INPUT_VERSIONS,
        "auditor": auditor._CONTENT_AWARE_VERSIONS,
    }.get(role)
    checks = []
    if versions is not None and not config.model_for(role).is_fake and version not in versions:
        checks.append(Check(
            Severity.ERROR,
            f"{role} 프롬프트 {version}은 내용 인식 집합 밖이다 — 본문·위치 또는 넓은 입력을 "
            f"받지 못한다. 해당 노드의 허용 판: {sorted(versions)}",
        ))
    if version.startswith("v") and version[1:].isdecimal():
        newer = sorted(
            int(path.name[1:])
            for path in (prompt_loader.PROMPTS_DIR / role).iterdir()
            if path.is_dir() and path.name.startswith("v") and path.name[1:].isdecimal()
            and int(path.name[1:]) > int(version[1:])
            and all((path / name).is_file() for name in ("prefix.md", "body.md", "meta.yaml"))
        )
        if newer:
            checks.append(Check(
                Severity.WARN,
                f"{role} 프롬프트는 {version}에 배선됐지만 디스크에 새 판 v{newer[-1]}이 있다 — "
                "공유 모델 엔트리와 역할별 계약을 확인한 뒤 배선을 정한다 (근거: 닫힌 이슈 #172)",
            ))
    return checks


def _slice_input_prompts(config: HarnessConfig) -> list[str]:
    """배선된 프롬프트 중 `SOURCE`를 슬라이스로 정의한 것 — meta의 `input: slice`가 정한다.

    `_undescribed_source_options`와 같은 모양이고 **같은 이유로 이름 있는 함수다**: 저장소
    설정이 그 조건을 못 만드는 때에도 테스트가 검출을 갈아끼워 게이트가 **서는 자리**를
    볼 수 있어야 한다. 그리고 `doctor`와 `run`이 같은 판정을 두 벌로 들고 있었다 —
    한쪽만 고치면 미리 말하는 것과 실제로 막는 것이 갈린다.

    ⏵ **2026-10-07 — 저장소의 어느 프로필도 이 목록을 채우지 않는다**(PR #524로 Scout가
    v4 = `input: source`에 배선됐다). 가드는 그대로 선다 — v3가 디스크에 있고 그것을 고르는
    설정이면 다시 찬다. 역할·버전을 여기 박지 않는 이유가 그것이다.

    ⏵ 2026-10-13 — 반대 방향(`input: source`인데 `--slices`를 줌)은 가드가 필요 없다: 노드가
    `SOURCE`의 모양을 이 선언으로 골라(`scout._ask_model`, #529) `--slices` run에서도 함수 본문이
    간다.
    """
    return [
        f"{role}/{version}"
        for role, version in config.prompt_versions().items()
        if load_prompt(Role(role), version).input == "slice"
    ]


def _undescribed_source_options(config: HarnessConfig) -> list[str]:
    """켜진 Auditor `SOURCE` 옵션 중 Auditor 프롬프트 meta의 `source_options`가 안 든 것
    (#351 리뷰 MEDIUM-2). 프로필에 Auditor가 없으면 `[]`다. 프롬프트를 못 읽으면 `PromptError`.

    **Fake Auditor도 `[]`다** — Fake 클라이언트는 프롬프트 문장을 안 읽고 정해진 답을 내므로
    서술이 SOURCE와 어긋나도 결과가 안 바뀐다. thresholds는 프로필 공통이라(`budgets.yaml`) 정본
    프롬프트(⏵ 2026-10-07 v10 — 그 전 v9·v8·v7 · ⏵ 2026-10-09 v11 · ⏵ 2026-10-10 v13 ·
    ⏵ 2026-10-11 v14)가 옵션을 서술하며 켜면, v1을 고르는 dev 프로필이
    여기서 막혀 GPU 없는 스모크가 죽는다(`_check_checksums`가 Fake 역할을 안 세는 것과
    같은 논법)."""
    on = config.thresholds.auditor_source_options_on()
    if not on or Role.AUDITOR.value not in config.profile.roles:
        return []
    if config.model_for(Role.AUDITOR.value).is_fake:
        return []
    version = config.model_for(Role.AUDITOR.value).prompt_template_version
    described = load_prompt(Role.AUDITOR, version).source_options
    return [f"{key}(auditor/{version})" for key in on if key not in described]


def _undescribed_relayed_options(
    config: HarnessConfig, *, last_stage: Stage = MAX_STAGE
) -> list[str]:
    """켜진 `RELAYED_SOURCE_OPTIONS` 중 **Critic·PoV 좌석의 프롬프트 meta `source_options`가 안
    든 것**(PR #676). 항목은 `옵션(역할/판)`이다 — 프로필에 Auditor가 없으면 `[]`다.

    Auditor가 만든 `SOURCE` 문자열은 `runner.finding_sources`로 Critic
    (`_SOURCE_INPUT_VERSIONS`)과 8단계 PoV(`_WIDE_INPUT_VERSIONS`)에도 같은 바이트로 간다.
    `_undescribed_source_options`는 Auditor 판만 보므로 서빙 Auditor가 블록을 서술해도 그
    문자열을 받는 Critic·PoV 판이 모르는 조합이 통과했다.

    - SOURCE를 **받는 판만** 본다 — 받지 않는 판(Critic v1~v5 · PoV v2)은 문자열이 안 가서
      서술이 필요 없다. 선언이 없는 판(v7 이하 · v6 이하)은 「서술하지 않는다」로 읽는다.
    - Fake 좌석은 `[]`다(`_undescribed_source_options`와 같은 논법 - 정해진 답을 낸다).
    - `last_stage`가 그 좌석의 첫 단계(`_PROMPT_FIRST_STAGE`)에 못 닿으면 건너뛴다
      (`_check_prompts`와 같다 - `--stop-after 4` run이 8단계 PoV 판 때문에 막히지 않는다).
    프롬프트를 못 읽으면 `PromptError`다.
    """
    on = [k for k in config.thresholds.auditor_source_options_on() if k in RELAYED_SOURCE_OPTIONS]
    if not on or Role.AUDITOR.value not in config.profile.roles:
        return []
    receivers = {"critic": critic._SOURCE_INPUT_VERSIONS, "pov": pov._WIDE_INPUT_VERSIONS}
    missing: list[str] = []
    for role, versions in receivers.items():
        if role not in config.profile.roles or _PROMPT_FIRST_STAGE[role] > last_stage:
            continue
        entry = config.model_for(role)
        if entry.is_fake:
            continue
        version = entry.prompt_template_version
        if version not in versions:
            continue
        described = load_prompt(Role(role), version).source_options
        missing.extend(f"{key}({role}/{version})" for key in on if key not in described)
    return missing


def _check_source_options(config: HarnessConfig) -> list[Check]:
    """켜진 Auditor `SOURCE` 옵션을 프롬프트가 서술하지 않으면 ERROR — `--slices` 경고와 달리
    설정 자체가 어긋난 것이라(thresholds는 모든 프로필 공통) doctor가 막는다. 같은 `SOURCE`를 받는
    Critic·PoV 판이 서술하지 않는 경우도 따로 ERROR다(`_undescribed_relayed_options`, PR #676)."""
    checks: list[Check] = []
    # 둘은 **따로 시도한다** - 한 try에 묶으면 Critic·PoV 판 로드 실패가 이미 계산한 Auditor ERROR를
    # 버린다. 로드 실패 자체는 `_check_prompts`가 이미 ERROR로 댄다.
    try:
        missing = _undescribed_source_options(config)
    except PromptError:
        missing = []
    try:
        relayed = _undescribed_relayed_options(config)
    except PromptError:
        relayed = []
    if missing:
        checks.append(
            Check(
                Severity.ERROR,
                f"Auditor SOURCE 옵션 {', '.join(missing)}을 프롬프트가 서술하지 않는다 — meta의 "
                "source_options에 넣은 판으로 올리거나 옵션을 끈다 (#349)",
            )
        )
    if relayed:
        checks.append(Check(Severity.ERROR, _relayed_options_message(relayed)))
    return checks


def _relayed_options_message(relayed: list[str]) -> str:
    return (
        f"SOURCE 옵션 {', '.join(relayed)}을 그 좌석의 프롬프트가 서술하지 않는다 — Auditor가 만든 "
        "SOURCE가 Critic·PoV에도 같이 가므로(finding_sources) meta의 source_options에 넣은 판"
        "으로 올리거나 옵션을 끈다 (#676)"
    )


def _auditor_loop_problem(config: HarnessConfig) -> str | None:
    """도구 루프(`thresholds.auditor_tool_hops`)와 배선된 Auditor 프롬프트 판의 meta 선언이
    어긋나면 그 사유 문장을, 맞으면 `None`을 돌려준다(#419). 프로필에 Auditor가 없으면 `None`이다.
    프롬프트를 못 읽거나 `output_schema`가 허용집합 밖이면 `PromptError`를 올린다 — 호출부가
    각자의 방식으로 처리한다(`_undescribed_source_options`와 같은 모양).

    **판정은 노드와 같은 함수다**(`nodes.auditor.loop_problem`). 예전에는 cli가 meta의 `tools`만
    보고 노드가 `output_schema`만 봐서, 한쪽만 통과하는 설정(판은 루프인데 상한 0 · 판은 한 방인데
    상한 양수 · `tools`에 그 봉투의 `tool`이 안 받는 이름)이 `run`·`doctor`를 지나 첫 Auditor
    호출에서야 섰다 — 그 앞 단계(1~4)에 쓴 GPU 시간 뒤다.
    """
    if Role.AUDITOR.value not in config.profile.roles:
        return None
    entry = config.model_for(Role.AUDITOR.value)
    return auditor_loop_problem(
        load_prompt(Role.AUDITOR, entry.prompt_template_version),
        config.thresholds.auditor_tool_hops,
        is_fake=entry.is_fake,
    )


def _check_auditor_loop(config: HarnessConfig) -> list[Check]:
    """도구 루프 상한과 Auditor 프롬프트 선언이 어긋나면 ERROR.

    🔴 **왜 WARN이 아니라 ERROR인가.** 켜 놓고 프롬프트가 도구를 안 알리면 **모델이 도구를 영영
    안 쓰는데 run은 정상 종료한다** — 그 run의 「도구 호출 0건」이 「모델이 안 골랐다」로 오독된다.
    `--slices` 경고와 달리 설정 자체가 어긋난 것이라(thresholds는 모든 프로필 공통) 막는다
    (`_check_source_options`의 같은 판단).
    """
    try:
        problem = _auditor_loop_problem(config)
    except PromptError as exc:
        # (Auditor가 프로필에 없으면 `_auditor_loop_problem`이 `None`이라 여기 안 온다.) 로드 실패는
        # `_check_prompts`가 이미 ERROR로 댄다. 로드는 됐는데 선언이 이 역할의 허용집합
        # 밖이면(`resolve_output_schema`) 그 검사가 이 선언을 안 풀어 여기가 유일한 보고다.
        version = config.model_for(Role.AUDITOR.value).prompt_template_version
        try:
            load_prompt(Role.AUDITOR, version)
        except PromptError:
            return []
        problem = str(exc)
    if problem is None:
        return []
    return [Check(Severity.ERROR, problem)]


def _check_score_chain(config: HarnessConfig) -> list[Check]:
    """PoV 사슬을 막는 설정은 ERROR, 재현 명세 합성의 #232 위험은 WARN.

    `config._check_real_serving_limits_are_positive`와 같은 `served_models()` 기준이다.
    `production: false`여도 실서빙할 수 있고 Fake 전용은 GPU 없는 리허설이라 제외한다.
    좌석이 없으면 러너의 8단계가 안 돌고, 두 스위치가 0이면 재현법을 안 주는 과제에서
    validated가 비어 PoV가 0건이다 — run 완주가 이 구조적 막힘을 숨겨 ERROR로 드러낸다.
    합성 WARN은 `config.Thresholds.synthesize_repro_spec`의 #232 경고를 따른다. 그 주석의
    exit 0 서술은 낡았다: `gate.evaluate`는 이미 0·None을 제외한다. 남은 위험은 비-0 종료와
    기대 신호를 대조하지 않아 크래시 없이도 proven이 날 수 있다는 것이다(게이트 docstring).
    """
    if not config.served_models():
        return []
    checks = []
    if Role.POV.value not in config.profile.roles:
        checks.append(Check(Severity.ERROR, "점수 사슬: pov 좌석이 없어 8단계가 실행되지 않는다"))
    elif not config.thresholds.pov_attempt_top_n and not config.thresholds.synthesize_repro_spec:
        checks.append(Check(
            Severity.ERROR,
            "점수 사슬: pov_attempt_top_n=0 · synthesize_repro_spec=0 — 재현 명세를 안 주는 "
            "과제에서는 validated가 비어 PoV가 0건이다",
        ))
    if config.thresholds.synthesize_repro_spec:
        checks.append(Check(
            Severity.WARN,
            "점수 사슬: synthesize_repro_spec=1 — #232: p4는 exit 0·None을 제외하지만 비-0 종료를 "
            "기대 신호와 대조하지 않아 크래시 없이도 proven이 날 수 있다 "
            "(src/config.py의 합성 경고 중 남은 위험; gate.evaluate 기준)",
        ))
    return checks or [Check(Severity.OK, "점수 사슬: PoV 좌석과 후보 진입 설정 확인")]


def _check_scout_slice_trim(config: HarnessConfig) -> list[Check]:
    """`scout_slice_trim`이 켜지면 WARN — 모델은 **하네스가** 줄을 뺀 것을 모른다(#375 M-1).

    v3 prefix는 빠진 줄을 「슬라이서가 뺐다」고 서술하는데, 하네스가 뺀 줄은 슬라이서가 **남긴**
    줄이다. 잘린 구간의 방어 코드를 모델이 「슬라이서가 안 닿는다고 본 것」으로 오독할 수 있다.
    ERROR가 아닌 것은 `--slices` 경고와 같은 판단이다(막지 않고 기록한다 — 트림은 opt-in이고
    thresholds는 모든 프로필 공통이라 ERROR면 멀쩡한 프로필 doctor가 exit 1이 된다). 가드의 정본은
    하네스 트림을 서술하는 프롬프트 판에만 이 좌석을 돌리는 것(#351 `source_options` 선례)이다.
    """
    if not config.thresholds.scout_slice_trim:
        return []
    # ⏵ 2026-10-13 — 트림은 `input: slice` 판의 렌더에서만 돈다(`scout._ask_model`, #529). 배선된
    # Scout가 전부 `input: source`(서빙 v4)면 줄을 빼는 일이 없어 알릴 것이 없다.
    try:
        wired = _slice_input_prompts(config)
    except PromptError:
        wired = ["scout"]  # 로드 실패는 `_check_prompts`가 ERROR로 댄다 — 여기서는 WARN을 살린다
    if not any(name.startswith("scout") for name in wired):
        return []
    return [
        Check(
            Severity.WARN,
            "scout_slice_trim이 켜져 있다 — 창을 넘는 슬라이스는 하네스가 줄을 뺀다. 모델은 그것을 "
            "슬라이서가 뺀 것으로 읽는다(프롬프트에 하네스 트림 표식이 없다, #375 M-1). trace "
            "scout_slice_trimmed가 사후에 남긴다",
        )
    ]


def _check_thresholds() -> Check:
    return Check(
        Severity.WARN,
        f"실측 전 초기값 임계값: {list(PROVISIONAL_THRESHOLDS)} "
        "— 조건 간 비교 전에 확정한다 (근거: config/budgets.yaml 각 키 주석 — "
        "이슈 #91·#92·#511은 닫혔다 — #91·#92의 값은 안 잰 초기값 그대로고 "
        "`pov_tool_hops`(16)는 사용자 결정이나 효과가 미실측이다)",
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


# 채점기 private 경로(submit-fix·query-poc)에 쓰는 키의 환경변수 이름. 저자 서버의 기본 키가 공개
# placeholder라 저장소에 박지 않고 환경에서 받는다. 근거는 저자 서버가 아니라 이 저장소의 문서다 -
# docs/research/2026-09-29-cybergym-pov-design.md의 「엔드포인트 가시성」 항(기본 키가 공개
# placeholder이고 `CYBERGYM_API_KEY`로 덮는다)과 왕복 보고서 부록 5)의 주석(기본 키는
# `server/types.py`). 어댑터(`cybergym_oracle.py`)에는 그 문장이 없다.
_ORACLE_KEY_ENV = "CYBERGYM_API_KEY"

# 카나리 보고서를 만드는 명령. 🔴 **인자 모양을 그 도구에서 베꼈다** - `task`는 위치 인자이고
# 경로 옵션은 `--out`이다(`tools/oracle_canary.py`의 `_build_parser`). 앞 판은 거절 문구와
# `--help` 둘이 `--task <id> --json <경로>`라 적었는데 그 도구는 그것을 `unrecognized
# arguments`로 거부한다 - 보고서가 필수인 run의 **유일한 복구 안내**가 틀린 명령이었고, 그러면
# 조작자가 보고서를 손으로 쓰게 된다. 한 자리에 둬서 둘이 갈리지 않게 한다
# (`tests/test_oracle_cli.py`가 이 문자열을 도구 파서로 실제로 파싱해 본다).
_CANARY_COMMAND = "tools/oracle_canary.py <task-id> --out <경로>"


class _OracleRefused:
    """`_oracle_for`가 「사람에게 알리고 멈춰라」를 돌려주는 표식의 타입.

    `Oracle | None`(정상)과 `isinstance`로 갈린다 - 반환 표기가 `object`로 뭉개지지 않는다.
    """


# 설정 오류의 표식. `None`은 「제출 안 함」(정상)이라 그것과 섞으면 설정 오류가 조용히 미제출로
# 떨어진다.
_ORACLE_ERROR = _OracleRefused()


def _refuse_oracle(message: str) -> _OracleRefused:
    """`[ERROR]`를 stderr에 찍고 `_ORACLE_ERROR`를 돌려준다 - 호출자가 exit 2로 멈춘다."""
    print(f"[ERROR] {message}", file=sys.stderr)
    return _ORACLE_ERROR


def _oracle_server_problem(server: str) -> str | None:
    """`--oracle-server` 값이 클라이언트가 보낼 수 있는 주소가 아니면 그 사유, 맞으면 `None`.

    http/https 주소여야 하고(어댑터가 요청 때 같은 것을 보지만 run 디렉터리를 잡기 **전에** 막는다),
    호스트가 있어야 하며, 와일드카드 주소(`0.0.0.0`·`::`)와 경로·인증 정보는 안 받는다 -
    와일드카드는 서버가 bind할 때 쓰는 값이지 클라이언트가 접속할 주소가 아니다(저자도 서버를 그렇게
    띄우지 말라고 한다, 어댑터 docstring).
    """
    try:
        parts = urllib.parse.urlsplit(server)
        port_ok = parts.port is None or parts.port > 0
    except ValueError:
        return "주소를 읽을 수 없다"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "http:// 또는 https://로 시작하는 주소여야 한다"
    if not port_ok:
        return "포트가 올바르지 않다"
    try:
        wildcard = ipaddress.ip_address(parts.hostname).is_unspecified
    except ValueError:  # IP가 아닌 호스트 이름
        wildcard = False
    if wildcard:
        return "와일드카드 주소는 접속할 주소가 아니다"
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return "서버 주소에는 경로·질의·인증 정보를 넣지 않는다(스킴://호스트:포트)"
    return None


def _origin(server: str) -> tuple[str, str]:
    """주소의 (스킴, host:port) - 대소문자와 끝의 `/`를 무시하고 견주려는 키다."""
    parts = urllib.parse.urlsplit(server)
    return parts.scheme.lower(), parts.netloc.lower()


def _oracle_unreachable_reason(spec: RunSpec, config: HarnessConfig) -> str | None:
    """이 run이 채점기에 **아무것도 못 낸다**면 그 사유, 낼 수 있으면 `None`.

    제출은 8단계 PoV가 만든 PoC에서 나온다. `--dry-run`·낮은 `--stop-after`는 8단계에 안 닿고, pov
    좌석이 없는 프로필은 PoV가 안 돈다.
    """
    if spec.stop_at()[0] < Stage.VALIDATE:
        return "이 run은 8단계에 닿지 않는다(--dry-run 또는 --stop-after가 8 미만)"
    if Role.POV.value not in config.profile.roles:
        return f"프로필 {config.profile_name}에 pov 좌석이 없어 PoV가 돌지 않는다"
    return None


def _oracle_for(
    args: argparse.Namespace, *, unreachable: str | None = None
) -> Oracle | None | _OracleRefused:
    """`--oracle-submit`이면 채점기를 조립한다 (#386). 설정이 틀리면 `_ORACLE_ERROR`다.

    안 켰으면 `None`(제출 안 함)이다 - 단 받아 놓고 안 쓰는
    `--oracle-server`·`--mask-map`·`--oracle-canary`는 설정 오류다.

    **`--oracle-submit`의 필수 입력은 넷이다**: `--oracle-server`(기본값 없음) ·
    `--oracle-canary`(이 장비가 sanitizer를 제대로 돌리는지 잰 보고서) · `--task-dir`의 번들
    `submit.sh` · 환경변수 `CYBERGYM_API_KEY`. 마스킹 서버의 번들이면 `--mask-map`도 필수다.

    **서버 주소는 조작자가 정한다**(`--oracle-server`, 기본값 없음). 과제 번들은 신뢰 못 하는
    소스이고(`task_loader`) 거기서 긁은 주소로 조작자의 API 키를 보내면 번들을 만든 쪽이 키를
    받는다. 번들의 `submit.sh`에서 읽는 것은 과제 id·에이전트
    id·체크섬이다(하네스가 다시 만들 수 없는 값이다). **번들이 적은 주소가 조작자의 주소와 다르면
    거부한다** - 둘 중 하나를 조용히 고르지 않는다. 번들의 주소는 보내는 곳이 아니라 「이 번들이
    다른 서버용이 아닌가」를 보는 견줌일 뿐이고, 거부는 조작자가 같은 주소를 주거나 번들을 다시
    만들게 한다(`localhost`와 `127.0.0.1`은 다른 주소로 센다).

    **제출 전에 거절하는 것** - 주소 모양, `--mask-map` 파일(못 읽거나 JSON 객체가 아니거나 번들의
    masked id가 맵에 없는 것), task_id 모양(마스킹된 id는 `--mask-map`이 실 id를 줄 때만 받는다,
    `unsupported_task_id`), 번들의 체크섬이 클라이언트가 보내는 기본 salt로 계산한 값과 다른 것(다른
    salt로 만든 번들은 서버가 400으로 거부한다), 서버가 제출을 400으로 거부하는 계열의 실 id
    (`refused_family_task_id`), 키 부재·키 모양(공백·비출력 문자),
    **카나리 보고서**(없거나 이 과제의 것이 아니거나 통과가 아닌 것 - `_canary_gate`).
    어느 하나가 걸리면 컨테이너는커녕 요청도 안 나간다. 제출하려 한 run이 조용히 미제출로 끝나면
    「0건 제출」이 설정 오류와 구별되지 않는다(§0).

    **마스킹 서버**는 제출을 masked task_id로 받지만 DB·`/query-poc`는 실 id를 쓴다(저자 소스
    판독, 실측 없음). `--mask-map`이 있으면 번들의 masked id를 실 id로 바꿔
    `TaskOracle.query_task_id`로 넘기고(조회에만 쓴다 - 제출은 번들의 id 그대로), 없으면 `None`이다.

    `unreachable`은 이 run이 채점기에 **아무것도 못 내는 사유**(`_oracle_unreachable_reason`)다 -
    있으면 경고만 하고 `None`을 돌려준다. 모델에 안 닿는 run은 막지 않는다는 이 파일의 원칙과 같다:
    막으면 통과시키려고 값을 지어내게 되고, 제출할 게 없는 run에 키·번들 검사를 걸 근거가 없다.
    """
    server = getattr(args, "oracle_server", None)
    if not getattr(args, "oracle_submit", False):
        if server is not None:
            return _refuse_oracle("--oracle-server는 --oracle-submit와 함께만 쓴다")
        if getattr(args, "mask_map", None) is not None:
            return _refuse_oracle("--mask-map은 --oracle-submit와 함께만 쓴다")
        if getattr(args, "oracle_canary", None) is not None:
            return _refuse_oracle("--oracle-canary는 --oracle-submit와 함께만 쓴다")
        return None
    if unreachable is not None:
        print(
            f"[WARN ] --oracle-submit이지만 {unreachable} - 아무것도 제출하지 않는다",
            file=sys.stderr,
        )
        return None
    if args.task_dir is None:
        return _refuse_oracle("--oracle-submit은 --task-dir와 함께만 쓴다")
    if server is None:
        return _refuse_oracle(
            "--oracle-submit에는 --oracle-server가 필요하다 - 채점기 주소는 조작자가 정한다. "
            "API 키가 가는 곳이라 과제 번들(submit.sh)이 가리키는 주소로는 보내지 않고, "
            "기본 주소도 없다"
        )
    problem = _oracle_server_problem(server)
    if problem is not None:
        return _refuse_oracle(f"--oracle-server {server!r}: {problem}")
    metadata = read_submit_metadata(args.task_dir)
    if metadata is None:
        return _refuse_oracle(
            f"과제에서 채점기 신원을 못 읽었다 - {args.task_dir}/{SUBMIT_SCRIPT_NAME}에 "
            "서버 주소와 metadata(task_id·agent_id·checksum, 모두 비어 있지 않은 문자열)가 필요하다"
        )
    query_task_id = _query_task_id_from_mask_map(args, metadata.task_id)
    if isinstance(query_task_id, _OracleRefused):
        return query_task_id
    # 서버가 제출을 400으로 거부하는 계열은 run을 다 돈 뒤에야 실패하므로 제출 전에 거절한다(#431) -
    # **조회에 쓰는 실 id**로 가린다. 카나리 검사보다 앞이라 보고서가 다른 과제의 것이어도 사유는
    # 이쪽이다.
    refused = refused_family_task_id(query_task_id or metadata.task_id)
    if refused is not None:
        return _refuse_oracle(f"{refused}. 아무것도 제출하지 않았다")
    problem = unsupported_task_id(metadata.task_id)
    if problem is not None and query_task_id is None:
        return _refuse_oracle(
            f"과제 번들의 task_id를 지원하지 않는다: {problem}. 마스킹 서버의 번들이면 "
            "--mask-map으로 저자 mask_map.json을 같이 준다. 아무것도 제출하지 않았다"
        )
    if checksum(metadata.task_id, metadata.agent_id) != metadata.checksum:
        return _refuse_oracle(
            "과제 번들의 checksum이 기본 salt로 계산한 값과 다르다 - 이 클라이언트는 기본 salt만 "
            "보낸다(다른 salt로 만든 번들은 서버가 400 Invalid checksum으로 거부한다). 아무것도 "
            "제출하지 않았다"
        )
    if _origin(metadata.server) != _origin(server):
        return _refuse_oracle(
            f"과제 번들(submit.sh)의 서버 주소 {metadata.server!r}가 "
            f"--oracle-server {server!r}와 다르다 - 같은 주소를 주거나 그 서버용으로 번들을 다시 "
            "만든다. 번들의 주소로는 보내지 않는다"
        )
    api_key = os.environ.get(_ORACLE_KEY_ENV, "")
    if not api_key:
        return _refuse_oracle(
            f"{_ORACLE_KEY_ENV}가 비어 있다 - "
            "채점기의 private 경로(submit-fix·query-poc)에 필요하다"
        )
    # 🔴 **키 모양을 여기서 거른다 - 그러지 않으면 키가 run 산출물에 평문으로 남는다.** 끝에 CR이
    # 붙은 키(CRLF 파일을 `$(cat key.txt)`로 읽으면 생긴다)는 `http.client`가 헤더를 만들기 전에
    # `ValueError("Invalid header value b'<키>\\r'")`로 거부하는데, 그 문구가 `error_detail`을 거쳐
    # 사이드카 `oracle/verdict.json`에 **그대로** 쓰인다(앞 판의 어댑터 가림막은 자기 예외
    # 클래스만, 그것도 실제 CR이 든 원문으로만 지웠다 - 지금은 `TaskOracle._redacting`이
    # `repr(bytes)` 모양까지 지우지만 그것에만 기대지 않고 입구에서 먼저 거른다). 팀이 복사·공유하는
    # 파일이라 키를 쓰는 사고다. 값은 **문구에 싣지 않는다**.
    if _key_has_bad_chars(api_key):
        return _refuse_oracle(
            f"{_ORACLE_KEY_ENV}에 공백·비출력 문자가 있다({len(api_key)}자) - 값은 찍지 않는다. "
            "파일에서 읽었다면 끝의 개행이 섞인 것이다(CRLF 파일이면 CR까지). HTTP 헤더에 못 "
            "실리는 값이고, 그 오류 문구에 키가 실려 run 산출물에 남는다. 아무것도 제출하지 않았다"
        )
    # 설정 검사를 다 지난 뒤에 장비 상태를 본다 - 카나리를 다시 돌리라고 하기 전에 고칠 수 있는
    # 것(주소·번들·키)을 먼저 말한다.
    # 🔴 견주는 것은 **실 id**다. 카나리 도구는 과제 id로 `-vul`·`-fix` 이미지 이름을 만들어
    # 조작자가 실 id로 돌리므로 보고서의 `task`도 실 id다. 번들의 masked id와 견주면 마스킹 run의
    # **정상 카나리가 전부 거절된다**(README 표준 기동이 그 경로다).
    gate = _canary_gate(args, query_task_id or metadata.task_id)
    if isinstance(gate, _OracleRefused):
        return gate
    target = server.rstrip("/")
    print(
        f"채점기 대상: {target} (과제 id {metadata.task_id}, 에이전트 id {metadata.agent_id}) - "
        f"{_ORACLE_KEY_ENV}가 이 주소의 private 경로로 간다. vul 다리에는 모델이 "
        "submit한 PoC와(pov v3 이상) 중간 gen을 보내고 중간 gen은 판정 조회도 한다. "
        "fix 다리와 최종 판정 조회는 최종으로 지정한 후보 하나에만 보낸다 - 후보가 하나뿐이면 "
        "그 하나고, 둘 이상이면 같은 PoC 바이트끼리 접은 뒤 전순위로 "
        "하나를 고른다(vul 응답 종료 코드 계단 > Critic 판정 > 내부 근거 등급 > confidence > "
        "작은 바이트 > PoC 해시. 계단은 크래시, 모름, 크래시 아님 순이다). "
        "등급·confidence·Critic 판정 중 모르는 값이 있는 후보가 있으면 보내지 않는다"
    )
    return TaskOracle(
        server=target,
        task_id=metadata.task_id,
        agent_id=metadata.agent_id,
        api_key=api_key,
        query_task_id=query_task_id,
        # 어느 보고서에 기댔는지 - 경로는 풀어서, 해시는 **게이트가 검사한 바이트의 것**이다(#487).
        canary_path=gate.path,
        canary_sha256=gate.sha256,
    )


def _key_has_bad_chars(key: str) -> bool:
    """키에 공백·비출력 문자가 있나 - HTTP 헤더에 못 실리는 값이다. `preflight`도 이 판정을 쓴다."""
    return any(ch.isspace() or not ch.isprintable() for ch in key)


def _canary_ref(oracle: object) -> dict[str, str] | None:
    """`RunRecord.oracle`에 실을 카나리 보고서 참조. 카나리에 안 기댄 채점기면 `None`이다 (#487)."""
    path = getattr(oracle, "canary_path", None)
    digest = getattr(oracle, "canary_sha256", None)
    if path is None and digest is None:
        return None
    if path is None or digest is None:
        raise ValueError("canary_path·canary_sha256은 함께 있어야 한다 - 한쪽만이면 기록이 틀리다")
    return {"canary_path": path, "canary_sha256": digest}


def _poc_target_for(oracle: object) -> PocTarget | None:
    """`poc` 도구가 돌릴 과제 `-vul` 이미지 (ADR 0014). 채점기가 없으면 `None`이다.

    🔴 **배선 자리가 오라클과 같은 것이 결정의 일부다.** 채점기가 서려면 카나리 선행
    (`_canary_gate` → `canary_report.canary_refusal`)을 통과해야 하고, 그 게이트가
    `vm.mmap_rnd_bits > 28`과 **미측정**을 이미 거부한다. 32면 무해 입력도 ≈0.28로 성공해 로컬
    판정이 잡음이므로, 그 게이트를 **두 벌 두지 않고 이 값을 그 뒤에 묶는다** — 두 벌 두면
    두 벌이 갈린다.

    **조회에 쓰는 실 id를 쓴다** — 마스킹 서버의 번들은 `task_id`가 마스킹된 값이고 이미지
    이름은 실 id로 만들어야 한다(`_canary_gate`가 같은 값을 쓴다). `_canary_ref`와 같은
    `getattr` 모양이라 `Oracle` Protocol을 넓히지 않는다.

    모르는 계열이면 `None`이다 — 거기서 멈추지 않는다. 채점은 돌 수 있고 `poc` 도구만 그 run에서
    실패 Evidence가 된다(이름도 명령도 지어내지 않는다).
    """
    task_id = getattr(oracle, "query_task_id", None) or getattr(oracle, "task_id", None)
    if not isinstance(task_id, str) or not task_id:
        return None
    try:
        return poc_target_for(task_id)
    except ValueError:
        return None


def _canary_gate(args: argparse.Namespace, task_id: str) -> _OracleRefused | CanaryReport:
    """통과 못 하면 `_ORACLE_ERROR`, 통과면 그 보고서 - 카나리 보고서 게이트 (이슈 #383).

    보고서가 이 과제·이 장비를 통과시키지 않으면 거절이다.

    🔴 **보고서가 없으면 제출을 시작하지 않는다.** 오라클 판정이 비결정적일 수 있고 그 원인은 호스트
    커널의 mmap 엔트로피다 - `vm.mmap_rnd_bits = 32`에서 sanitizer 섀도 매핑이 간헐 실패해 타깃이
    입력을 읽기도 전에 `139`로 죽고 그 코드가 「크래시」로 집계된다. 2026-10-02 `win11wsl`에서
    **정답 PoC ≈0.40 · 무해 입력 ≈0.28**이 「성공」이었다(차가 ≈0.12. ⚠ **계산값이다** - 다리별
    표본 비율의 곱이고 직접 잰 성공률이 아니다). 그 run이 낸 점수는 PoC 품질을 거의 반영하지 않는다.

    **하네스가 카나리를 직접 돌리지는 않는다** - 조작자가 `tools/oracle_canary.py`로 돌려 보고서를
    만들고 그 경로를 `--oracle-canary`로 준다. 돌리는 쪽까지 하네스로 가져오려면 run이 `-fix`
    이미지와 저자 참조 PoC를 손에 쥐어야 하고 N·보고서 위치·`weak_pass` 처리가 전부 정해야 하는
    값이라, 그 결정은 사람 몫으로 남겼다(그 이슈의 선택지 (a)).

    ⚠ **이 게이트가 보는 것은 파일 하나다.** 그래서 못 묶는 것이 둘이다. ① **신선도** - 보고서가
    지금 이 부팅의 것인지 판별하려면 현재 `boot_id`를 읽어야 하고 그것은 컨테이너 한 번이다.
    `measured_at`을 자를 나이도 미실측이라 지어낸 상수가 된다. ② **장비** - 보고서는 도구가
    `docker run`을 돌린 호스트를 재는데, `--oracle-server`가 **다른 장비**(SSH 포워딩된
    `127.0.0.1`)면 이 노트북의 28이 그 장비의 값을 보증하지 않는다. 주소로는 가를 수 없다.
    그래서 둘의 계약은 **조작자가 run마다 경로를 명시로 넘기는 것**이고, 거절·통과 문구가
    `measured_at`·`docker_version`을 들어 조작자가 그것을 본다.
    """
    path = getattr(args, "oracle_canary", None)
    if path is not None and _inside_task_tree(args, Path(path)):
        # 과제 번들은 **신뢰 못 하는 소스**다(`task_loader`). 지금은 다른 검사들이 우연히 막는다 —
        # 번들에 허용 목록 밖 파일이 있으면 `check_task_dir`가, 작업 디렉터리가 비어 있지 않으면
        # 그쪽 검사가 먼저 거절한다. 그 둘에 기대지 않고 명시로 막는다: 「게이트가 읽는 파일을
        # 과제가 깔지 못한다」가 그 검사들의 부수 효과가 아니라 이 함수의 성질이어야 한다.
        return _refuse_oracle(
            f"--oracle-canary가 과제 트리 안이다({path}) - 과제 번들은 신뢰 경계 밖이라 게이트가 "
            "읽는 파일을 그 안에 두지 않는다. 번들 밖 경로로 준다"
        )
    if path is None:
        return _refuse_oracle(
            "--oracle-submit에는 --oracle-canary가 필요하다 - 채점 run 전에 이 장비가 sanitizer를 "
            "제대로 돌리는지 봐야 한다(호스트 vm.mmap_rnd_bits가 28보다 크면 무해한 입력도 "
            f"「크래시」로 집계된다). python {_CANARY_COMMAND}로 만든다"
        )
    try:
        # 🔴 **경로를 한 번만 풀고 그 값으로 읽는다** - 기록하는 경로(`CanaryReport.path`)와 해시한
        # 파일이 같아야 한다. 읽은 뒤 따로 `resolve`하면 그 틈에 링크가 바뀔 수 있다(#487).
        report = read_canary_report(Path(path).resolve())
    except CanaryReportError as exc:
        return _refuse_oracle(f"{exc} - 아무것도 제출하지 않았다")
    except (OSError, RuntimeError) as exc:  # 경로를 못 푼다(심볼릭 링크 순환 등)
        return _refuse_oracle(f"--oracle-canary 경로를 풀 수 없다({path}): {exc!r}")
    problem = canary_refusal(report, task_id=task_id)
    if problem is not None:
        return _refuse_oracle(f"{problem}. 아무것도 제출하지 않았다")
    print(
        f"카나리 통과: {path} ({report.task} · vm.mmap_rnd_bits {report.mmap_rnd_bits} · "
        f"{report.repeat}회 · {report.measured_at} 측정) - 이 보고서가 지금 이 부팅의 것인지는 "
        "하네스가 못 본다"
    )
    return report


def _inside_task_tree(args: argparse.Namespace, path: Path) -> bool:
    """`path`가 `--task-dir`·`--task-work-dir` 아래인가. 경로를 못 풀면 `False`다.

    못 풀 때 `False`인 이유: 이 함수의 거절은 **덧가드**이고, 경로 해석이 안 되는 run은 뒤의
    읽기(`read_canary_report`)가 어차피 `CanaryReportError`로 거부한다. 여기서 못 푼 것을
    거절로 바꾸면 정상 경로를 애매하게 막는다.
    """
    for attr in ("task_dir", "task_work_dir"):
        root = getattr(args, attr, None)
        if root is None:
            continue
        try:
            if path.resolve().is_relative_to(Path(root).resolve()):
                return True
        except OSError:
            continue
    return False


def _query_task_id_from_mask_map(
    args: argparse.Namespace, masked: str
) -> str | None | _OracleRefused:
    """`--mask-map`이 주어지면 masked task_id를 **실(unmask) id**로 바꿔 돌려준다 (#386).

    마스킹 서버는 제출을 masked로 받지만 DB·`/query-poc`는 unmask된 id를 쓴다(저자 소스 판독 —
    `cybergym_oracle.run_round_trip` docstring의 마스킹 절). 호출자는 이 값을
    `TaskOracle.query_task_id`로 넘긴다. `--mask-map`이 없으면 `None`(서버가 마스크를 안 썼다는
    뜻 — `TaskOracle`이 submit의 task_id를 그대로 쓴다).

    파일을 못 읽거나(없음·권한·UTF-8 아님·JSON 아님) JSON 객체가 아니거나 그 masked id가 맵에
    없으면 `_ORACLE_ERROR` — 지어내지 않는다. 저장소 밖 파일이라 기본값을 두지 않고, 호출자가
    명시해야 한다.
    """
    mask_map = getattr(args, "mask_map", None)
    if mask_map is None:
        return None
    try:
        mapping = json.loads(Path(mask_map).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # `JSONDecodeError`·`UnicodeDecodeError`는 `ValueError`다
        return _refuse_oracle(f"--mask-map을 못 읽었다: {mask_map}: {exc}")
    if not isinstance(mapping, dict):
        return _refuse_oracle(
            f"--mask-map이 JSON 객체가 아니다({type(mapping).__name__}): {mask_map} - "
            "실 id를 masked id에 잇는 객체여야 한다"
        )
    # mask_map.json은 {real: masked}. 역변환이 필요하다.
    reverse = {v: k for k, v in mapping.items() if isinstance(v, str)}
    real = reverse.get(masked)
    if real is None:
        return _refuse_oracle(f"--mask-map에 masked task_id가 없다: {masked} - {mask_map}")
    return real


def execute(
    config: HarnessConfig,
    spec: RunSpec,
    *,
    run_id: str,
    run_dir: Path,
    trace_stream: TextIO | None = None,
    oracle: Oracle | None = None,
) -> tuple[RunResult, Deps]:
    """factory가 만든 어댑터를 러너에 넘긴다. 이 접합부가 이 파일의 존재 이유다.

    `Deps`를 함께 돌려주는 것은 `estimate`가 StateStore에서 캡슐을 세야 하기 때문이다.

    **저장소를 닫는 책임(#156).** 정상 반환이면 호출자가 다 쓴 뒤 `deps.store.close()`를
    부른다. 러너가 예외로 끝나면 `deps`를 돌려줄 수 없어 **여기서** 닫고 원 예외를 그대로
    올린다 — 안 닫으면 `estimate`의 임시 디렉터리 정리가 열린 `state.db`에서 죽어 원 예외를
    덮는다(Windows).

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
        # 못 믿는 슬라이스 파일이면 여기서 멈춘다 — `run_pipeline`(첫 모델 호출) 전이다.
        slices_path=spec.slices_path,
        # 교대 프로필의 장비 파일을 고른다(ADR 0012 Phase 6.5). 없으면 교대 콜러블을 안 만든다.
        device=spec.device,
        # 채점기(#386). `None`이면 PoC를 제출하지 않는다 — 서버 주소와 과제 번들을 아는 것은
        # `run()`이라 factory가 만들지 않고 받는다.
        oracle=oracle,
        # 그 채점기가 기댄 카나리 보고서의 경로·sha256(#487). `RunRecord.oracle`에 실린다.
        canary_ref=_canary_ref(oracle),
        # `poc` 도구가 돌릴 과제 `-vul` 이미지 (ADR 0014). 채점기와 **같은 자리**에서 온다 —
        # 카나리 선행이 ASLR 게이트를 이미 들고 있다(`_poc_target_for`).
        poc_target=_poc_target_for(oracle),
    )
    try:
        result = run_pipeline(
            spec,
            config=config,
            deps=deps,
            # Retriever와 **같은 함수**를 쓴다. 두 곳이 다르게 읽으면 캡슐의 토큰 수와
            # 실제 프롬프트가 어긋난다.
            read_source=lambda target: read_symbol_source(spec.target, target),
            # payload 파일은 `{run_dir.parent}/{run_id}/{slot}/{id}.log`에 있다(build_deps에 준
            # artifacts_root와 같은 루트). Critic·Auditor의 content-aware 버전이 근거 본문을 받는
            # 배선(#237 HIGH-1 · Auditor는 2026-09-29부터).
            read_payload=make_payload_reader(run_dir.parent),
            # 원본 바이트 — `read_source`가 접는 `\r`을 GUARDS의 줄 정렬 검사가 본다(`bare_cr`).
            read_raw=lambda file: read_raw_source(spec.target, file),
            trace=trace,
            run_dir=run_dir,
            cache_hook=hook,
            truncate_hook=truncate_hook,
            usage_hook=usage_hook,
            index_hook=index_hook,
        )
    except BaseException:
        deps.store.close()
        raise
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
        # 이 역할인 것은 `runner._step_retrieval`이 LIGHT 캡슐을 그 카운터로 조립하기
        # 때문이다. 조립과 검증이 같은 카운터를 봐야 한다는 `src/tokens.py`의 불변식을
        # 여기서도 같은 방식으로 지킨다.
        #
        # ⚠ **`"scout"`으로 박으면 풀에서 무너진다** — 풀 프로필에는 그 키가 없어
        # (`_declares_one_scout_arrangement`가 단일과 풀의 공존을 금지한다)
        # `counter_for_role`이 `None`을 주고 `using_counter(None)`은 아무것도 안 바꾼다.
        # 즉 위 막음이 통째로 사라진다. `_capsule_counter_role`이 그것을 가른다.
        #
        # **⚠ 이것은 임시방편이다.** `src/tokens.py`가 이미 경고한 대로 위험한 자리는
        # 확장 경로 하나가 아니라 **`using_counter` 밖의 모든 `model_validate`**이고, 이
        # 수정은 그중 지금 터진 한 곳을 막을 뿐이다. 정본은 캡슐이 자기 카운터를 싣고
        # 로드가 그것을 복원하는 것이다(#84 (c)) — 그때 이 `with`는 필요 없어진다.
        try:
            with using_counter(deps.counter_for_role(_capsule_counter_role(config))):
                capsules = deps.store.query("capsule")
        finally:
            # 임시 디렉터리를 지우기 **전에** 닫는다 — 열린 `state.db`가 Windows에서 정리를
            # 막고(파이썬 판에 따라 WinError 32·267), 그 예외가 위 조회의 `ValidationError`를 덮었다
            # (#156 · #84 ②).
            deps.store.close()

    calls = _expected_calls(config, len(capsules))
    places = _scout_places(config)
    # 캡슐은 target당 하나지만 **자리마다 프롬프트에 실린다** — `_expected_calls`와
    # 같은 근거다. 캡슐당 한 번만 더하면 풀에서 캡슐 항이 1/3로 나온다.
    # ⚠ **Auditor 도구 루프가 켜진 run은 과소추정이다**(⏵ 2026-10-04 — 이슈 #419). 이 합은 호출마다
    # **역할 평균 prefix**(`_prefix_tokens` — `harness_pool`에서 5,253토큰, `len//4`의 계산값이고
    # c2bdac1에 PoV v4 prefix(10,214자)를 올린 상태다. c2bdac1 자체는 5,042, 옛 값 4,481은 낡았다.
    # prefix가 바뀌면 움직인다)만 더한다.
    # 루프는 매 홉 prefix와 hop0 user 턴(`SOURCE` 최대 20,000토큰 + FACTS)을 다시 싣고 관측이
    # 홉마다 쌓이는데 둘 다 안 센다 — `SOURCE`는 캡슐 토큰이 아니라 러너가 따로 조립해서 한 방일
    # 때도 안 센다.
    # 계산값은 `config/budgets.yaml`의 `auditor_tool_hops` 주석이 든다 — 상한 쪽 계산값이라 오차는
    # 대체로 위(과대)이지만 엄밀한 천장은 아니다(근거는 그 주석). 고치지 않았다(별 작업).
    tokens = sum(c.used_tokens for c in capsules) * places + _prefix_tokens(config) * calls
    budget = config.budget.input_tokens
    # 상한 0은 「검사 안 함」이다(`BudgetLedger._overrun`) — 「예산이 0」이 아니다.
    # 0을 두 번 되풀이하면 「못 돈다」로 읽힐 수 있다(#435).
    share = f"{tokens / budget:.1%}" if budget else "상한 없음, 0은 검사 안 함"
    return [
        f"대상 artifact: {len(result.candidates) or len(capsules)}개 / 캡슐 {len(capsules)}개",
        f"예상 모델 호출: 최대 {calls}회 (scout + auditor + critic 상한 · pov 제외)",
        f"예상 input 토큰: 약 {tokens:,} / 예산 {budget:,} ({share})",
        _gpu_estimate(config),
    ]


def _capsule_counter_role(config: HarnessConfig) -> str:
    """LIGHT 캡슐을 **조립한** 역할 키. 재검증도 같은 카운터를 봐야 한다.

    풀이면 첫 멤버다 — `runner._scout_roles`가 `SCOUT_POOL_ROLES` 순서를 유지하고
    `_step_retrieval`이 **첫 멤버가 만든 캡슐을 나머지에 물려 준다**(ADR 0010 §3). 단일이면
    `scout`이다. **프로필에 실재하는 키여야 한다** — 없는 키를 주면 `counter_for_role`이
    `None`을 돌려주고 조립·검증이 다른 카운터를 본다(#84).
    """
    roles = config.profile.roles
    return SCOUT_POOL_ROLES[0] if all(m in roles for m in SCOUT_POOL_ROLES) else Role.SCOUT.value


def _scout_places(config: HarnessConfig) -> int:
    """이 프로필의 4단계 Scout 자리 수. 풀이면 셋, 단일이면 하나.

    판정 근거는 `runner._scout_roles`와 **같은 상수**(`SCOUT_POOL_ROLES`)다 — 두 곳이
    각자 "풀인가"를 정하면 한쪽만 고쳐질 때 추정과 실행이 갈린다.
    """
    roles = config.profile.roles
    return len(SCOUT_POOL_ROLES) if all(m in roles for m in SCOUT_POOL_ROLES) else 1


def _expected_calls(config: HarnessConfig, capsules: int) -> int:
    """후보 하나당 scout **`1 + max_hops`**회(need_evidence 상한), 승격되면 auditor 1회(도구 루프를
    켜면 `1 + auditor_tool_hops`회 — 아래), 상위만 critic 1회.

    승격률도 NE 홉 수도 모르므로 **상한**을 낸다 — 추정치를 낙관적으로 내면 예산 계획이
    무의미해진다. Scout 쪽이 특히 그렇다 — v3 좌석이 매번 `max_hops`까지 되물으면 그
    상한까지 GPU 시간이 실제로 든다(ADR 0012 결정 2). v1·v2(신호 없음)와 **v4**(신호 둘 —
    `triage_review`·`triage_reject`, need_evidence 미사용 · 2026-10-07 서빙 판, PR #524)는 늘
    1회뿐이라 이 상한을 안 채우지만, 예산은 **최악을 견뎌야** 하므로 여기서 낙관하지 않는다.
    ⚠ 그래서 지금 서빙 배선에서 Scout 항은 상한의 1/4이고 전체 추정은 약 17% 과대다
    (계산값 — `max_hops` 3 · `auditor_tool_hops` 16 · `critic_top_n` 8에서 후보 100~1,047일 때
    비 1.166~1.167, 이슈 #530의 표).

    **⚠ 캡슐 수와 후보 수는 같지 않다.** 캡슐은 **target당 하나**인데(ADR 0010 §3 —
    풀 멤버 셋이 같은 캡슐을 공유한다) 4단계는 **자리마다 한 번** 돈다. 그래서 후보가
    `캡슐 × 자리 수`이고 4·5단계 호출이 그것을 따른다. 예전에는 「캡슐 하나당 scout 1회」로
    세어 풀 프로필에서 **실제의 1/3**을 냈다 — 그 값으로 예산을 잡으면 run이 도중에
    `BudgetExhausted`로 부분 결과만 남긴다(PR #178 리뷰).

    **Auditor는 도구 루프를 켜면 후보당 `1 + auditor_tool_hops`회**(`thresholds.auditor_tool_hops` —
    0 = 종전의 1회. 필드 기본은 0이지만 ⏵ 2026-10-04 `config/budgets.yaml`이 16을 들어 저장소 설정은
    후보당 17회다)로 센다 — 켠 run의 Auditor 호출은 후보당 홉 상한까지 간다(계산값).

    **⚠ pov 루프 호출은 이 합에 안 든다** — pov 좌석이 있는 프로필에서 8단계가 PoV 대상 finding당
    최대 `1 + pov_tool_hops`회 더 부른다. 대상은 VALIDATED(critic 통과분이라 최대
    `min(candidates, critic_top_n)`)에 `pov_attempt_top_n`이 양수면 그 밖에서 더한 것이라 상한은
    `min(candidates, critic_top_n + pov_attempt_top_n) × (1 + pov_tool_hops)`다(계산값 —
    `runner._pov_targets`가 더하는 수는 `pov_attempt_top_n` 이하이고 finding은 후보당 하나다.
    `pov_attempt_top_n`이 0이면 `min(candidates, critic_top_n) × (1 + pov_tool_hops)` 그대로다).
    이 추정은 scout·auditor·critic 상한만 센다(라벨에 「pov 제외」).
    """
    candidates = capsules * _scout_places(config)
    scout_calls = candidates * (1 + config.thresholds.max_hops)
    # Auditor 도구 루프를 켰으면 후보당 최대 `1 + auditor_tool_hops`회다(hop 0..limit —
    # `nodes/auditor._run_hops`). 끄면 `auditor_tool_hops`가 0이라 종전의 1회 그대로다.
    # `max_hops`를 안 쓰는 것은 일부러다(`config.py`의 그 필드 주석).
    auditor_calls = candidates * (1 + config.thresholds.auditor_tool_hops)
    return scout_calls + auditor_calls + min(candidates, config.thresholds.critic_top_n)


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


# 파이썬 기본 동작이 `finally`를 안 거치고 죽는 종료 신호. Windows에는 SIGHUP이 없다.
_TERMINATING = tuple(
    getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)
)


@contextmanager
def _terminate_as_exit() -> Iterator[None]:
    """TERM·HUP를 `SystemExit(128 + 신호 번호)`로 바꾼다 — 러너의 `finally`(run 끝 내림)가 돌게.

    파이썬 기본 동작은 두 신호에 `finally`도 `atexit`도 안 거치고 죽는다. 교대가 띄운 서버는
    다른 세션이라 같이 안 죽으므로, 그대로 두면 `timeout`·`kill`·세션 끊김으로 끝난 run의 서버가
    `HARNESS_RUN_ID`째 남는다.

    - **무시한 채 띄운 신호는 그대로 둔다** — `nohup`은 HUP을 무시로 걸고 띄운다. 그 무시를 종료로
      바꾸면 터미널이 닫힐 때 run이 죽는다. 파이썬은 물려받은 무시를 `getsignal`로 `SIG_IGN`이라
      돌려준다.
    - **첫 신호 뒤에는 두 신호를 무시한다** — 내림 도중에 온 재촉이 내림을 끊으면 서버가 남는다.
      끊어야 하면 KILL이고 그것은 감독자 몫이다.
    - **못 받는 것** — KILL·OOM·전원에는 내림이 안 돈다(장비 쪽 감시자 몫이다). 내림이 이미 도는
      중에 온 **첫** 신호는 그 내림을 끊는다. 신호로 끝난 run은 `run_manifest.json`을 안 남긴다 —
      매니페스트는 러너의 `finally` 뒤에 쓴다(Ctrl-C도 같다).
    - 메인 스레드에서만 건다(`signal.signal`의 제약) — 다른 스레드에서는 아무것도 안 한다. 끝나면
      이전 핸들러를 되돌린다.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def exit_once(signum: int, _frame: object) -> None:
        for terminating in _TERMINATING:
            signal.signal(terminating, signal.SIG_IGN)
        raise SystemExit(128 + signum)

    previous = {
        signum: signal.signal(signum, exit_once)
        for signum in _TERMINATING
        if signal.getsignal(signum) is not signal.SIG_IGN
    }
    try:
        yield
    finally:
        for signum, handler in previous.items():
            # `None` = 파이썬 밖에서 건 핸들러 — 되돌릴 수 없어 기본으로 둔다.
            signal.signal(signum, signal.SIG_DFL if handler is None else handler)


# `run`이 장애로 중단됐을 때의 종료 코드. 1(트레이스백)·2(입력 거부)와 안 겹치고 `src.cli`에서
# 3은 이것뿐이다. ⚠ **다른 도구의 3과 같은 뜻이 아니다** — 종료 코드는 프로그램마다 따로이고
# 모집단이 서로소다: `tools/monitor.py`의 3은 DEAD·CRASHED(예상 밖 예외 — `run`은 그것을 1로 낸다)
# 이고, `run`이 3으로 내는 장애 넷(교대 실패·게이트웨이 장애·연속 폐기·스키마 파싱 실패)을
# `monitor`는 0으로 읽는다. 모듈 docstring 표 아래 경고가 나머지 도구를 든다.
EXIT_RUN_FAULT = 3
# 정상 종료로 읽는 사유 — **허용목록이다.** 새 사유가 생기면 기본이 비-0이다(조용한 0이 #533이다).
# 예산 소진은 러너 docstring이 정상적인 종료 경로라 적고, 나머지 둘은 사용자가 정한 상한이다.
_NORMAL_STOPS = frozenset(
    {ReasonCode.USER_STOP_REQUESTED, ReasonCode.DRY_RUN, ReasonCode.BUDGET_EXHAUSTED}
)


def _exit_for(reason: ReasonCode | None) -> int:
    """러너가 정한 정지 사유 → `run`의 종료 코드. 사유 없음(완주)·허용목록은 0, 그 밖은 전부 3.

    `UNFORESEEN_FAILURE`도 이 함수에서는 3이지만 실제로는 여기 안 온다 — 러너가 매니페스트를
    쓴 뒤 예외를 다시 던져 `RunResult`가 안 나오고 `run`은 트레이스백(1)으로 끝난다.
    """
    return 0 if reason is None or reason in _NORMAL_STOPS else EXIT_RUN_FAULT


def run(args: argparse.Namespace) -> int:
    try:
        config = load_profile(args.profile, seed=args.seed, tool_runner=args.tool_runner)
    except ConfigError as exc:
        # `doctor`는 같은 입력에 한 줄로 답한다(`:141-144`). `run`만 트레이스백을 내면
        # 같은 오타가 커맨드에 따라 다른 얼굴로 나온다.
        print(f"[ERROR] 설정 로드 실패: {exc}", file=sys.stderr)
        return 2
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
    # CyberGym 과제 번들을 그대로 받는다(#356). 2026-10-02까지 `task_loader`의 `load_task`·
    # `safe_extract`·`read_description`은 프로덕션 호출처가 **0건**이었다 — 과제를 손으로 풀어
    # `--target`·`--description-file`을 따로 주는 절차였다. 이 분기는 `safe_extract`와
    # `read_description`을 **따로** 부른다(이유는 아래 — 설명은 해제보다 먼저 읽는다).
    # ⚠ 그래서 둘을 묶은 `load_task`의 프로덕션 호출처는 **지금도 0건**이다 — `load_task`에만 거는
    # 검사는 이 경로에 닿지 않는다. 새 검사는 이 분기에도 따로 건다 — 정답 자료 가드
    # (`check_task_dir`)가 그렇게 걸려 있다(아래 아카이브 확인 바로 뒤).
    task_description: str | None = None
    #
    # ⚠ **여기서는 검증만 하고 해제는 `_claim_run_dir` 직전에 한다.** 처음 판은 여기서 바로
    # 풀었는데, 그 뒤의 게이트(실서빙 라벨·슬라이스 가드·교대)가 exit 2를 내면 **작업 디렉터리에
    # 트리가 남아** 오타를 고친 재시도가 "비어 있지 않다"로 거부됐다(적대적 리뷰 2026-10-02 M-1).
    # 쓰기를 **되돌릴 수 없는 지점 바로 앞**으로 미루면 그 함정이 사라진다. `--slices`의 두 검사
    # (drift · 안 맞는 슬라이스 개수)만은 풀린 트리가 있어야 돌아 해제 **뒤**에 있고, 거기서
    # 멈추면 트리가 남는다(에러가 그 사실을 말한다).
    #
    # ⚠ **`--task-work-dir`를 요구하는 참인 이유는 「기록」이 아니라 「섞임 방지」다.** 처음 판은
    # *"해제 자리가 run 기록이다"*라 적었는데 **거짓이다** — `RunRecord`·`RunSpec`에 과제 경로·
    # 아카이브 해시 필드가 **없다**(적대적 리뷰 M-1 HIGH-1). 출처를 진짜로 남기는 것은 S12d의
    # 대응표(`repo_archive_sha256`·`description_sha256`) 몫이고 **아직 없다.**
    if args.task_dir is not None:
        if args.description_file is not None:
            # 설명의 출처가 둘이면 캡슐에 실린 것이 어느 쪽인지 기록으로 못 가른다.
            print(
                "[ERROR] --task-dir와 --description-file을 같이 주지 않는다 — "
                "과제 번들의 description.txt가 설명이다",
                file=sys.stderr,
            )
            return 2
        if args.target is not None:
            print(
                "[ERROR] --task-dir와 --target을 같이 주지 않는다 — 해제된 트리가 target이다",
                file=sys.stderr,
            )
            return 2
        if args.task_work_dir is None:
            print(
                "[ERROR] --task-dir를 주면 --task-work-dir도 줘야 한다 — 해제 트리가 섞이지 "
                "않게 빈 디렉터리를 명시한다",
                file=sys.stderr,
            )
            return 2
        # **작업 디렉터리를 재사용하지 않는다.** `safe_extract`는 상한 초과로 중단해도 앞 멤버를
        # dest에 남기므로(그 함수의 경고 주석), 남은 트리 위에 또 풀면 두 과제가 섞인다. 섞인
        # 트리로 돈 run은 무엇을 봤는지 모른다 — 비우는 것은 **사람이** 한다(지우지 않는다).
        if args.task_work_dir.exists():
            if not args.task_work_dir.is_dir():
                # `iterdir()`는 디렉터리가 아닌 것에 `NotADirectoryError`를 던진다.
                print(
                    f"[ERROR] --task-work-dir가 디렉터리가 아니다: {args.task_work_dir}",
                    file=sys.stderr,
                )
                return 2
            if any(args.task_work_dir.iterdir()):
                print(
                    f"[ERROR] --task-work-dir가 비어 있지 않다: {args.task_work_dir} — "
                    "해제 트리가 섞이지 않게 빈 디렉터리를 준다",
                    file=sys.stderr,
                )
                return 2
        if not (args.task_dir / REPO_ARCHIVE_NAME).is_file():
            # 해제 전에 본다 — 아카이브가 없으면 풀 것이 없고, `safe_extract`에 맡기면
            # `dest.mkdir`이 먼저 돌아 빈 작업 디렉터리만 남는다. 그 전에 다른 게이트에 시간을
            # 쓸 이유도 없다.
            print(
                f"[ERROR] 과제에 {REPO_ARCHIVE_NAME}가 없다: {args.task_dir}", file=sys.stderr
            )
            return 2
        # 🔴 **정답 자료 가드는 이 분기에도 따로 건다**(위 ⚠ — 이 분기는 `load_task`를 안 거친다).
        # 아카이브 확인 **뒤**, 설명을 읽거나 무엇을 쓰기 **앞**이다 — 허용 목록(`TASK_FILES`) 밖
        # 항목이 하나라도 있으면(`patch.diff`·`error.txt`·`repo-fix.tar.gz` 같은 정답 자료가 번들
        # 옆에 떨어진 경우) 시작하지 않고 작업 디렉터리도 run 디렉터리도 만들지 않는다. **여기서
        # 직접 잡는다** — 아래 `safe_extract`의 `except`는 이 호출을 감싸지 않고, 그쪽 문구(「앞
        # 멤버가 남았을 수 있다」)는 아직 아무것도 안 풀었는데 틀리다. `OSError`(목록을 못 읽음 —
        # 권한)도 같이 막는다: 검사를 못 했으면 통과가 아니라 거부다. ⚠ `--target`을 직접 주는
        # 경로는 이 검사 밖이다 — 임의의 소스 트리를 받는 경로라 `patch.diff` 같은 이름이 정상
        # 파일일 수 있다(손으로 푼 번들의 정답 자료는 그쪽에서 못 막는다).
        try:
            check_task_dir(args.task_dir)
        except UnexpectedTaskFilesError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            detail = " ".join(f"{type(exc).__name__}: {exc}".split())
            print(
                f"[ERROR] 과제 디렉터리를 읽을 수 없어 정답 자료 검사를 못 했다: {detail}",
                file=sys.stderr,
            )
            return 2
        # 해제 트리가 target이다 — **경로는 지금 알 수 있다**(풀기 전에도).
        args.target = args.task_work_dir
        # 설명은 **지금** 읽는다 — 작은 텍스트 파일이고 해제와 무관하다(`RunSpec`을 만들 때 필요한데
        # 그 시점이 해제보다 앞이다). `load_task`를 통째로 부르지 않고 그 두 조각
        # (`read_description`·`safe_extract`)을 **따로** 쓰는 이유가 이 시점 차이다.
        task_description = read_description(args.task_dir)
        if not task_description:
            # Level 0 과제에는 설명이 없는 것이 정상이다 — 막지 않고 알린다. 조용히 넘어가면
            # "설명을 줬는데 안 실렸다"와 구별되지 않는다.
            print(
                f"[WARN ] 과제에 {DESCRIPTION_FILENAME}가 없거나 비어 있다 — "
                "캡슐에 vuln_description 섹션이 실리지 않는다",
                file=sys.stderr,
            )
    elif args.task_work_dir is not None:
        # 조용히 무시하면 사람은 과제가 풀린 줄 안다.
        print("[ERROR] --task-work-dir는 --task-dir와 함께만 쓴다", file=sys.stderr)
        return 2
    if args.target is None:
        # argparse의 `required=True`를 뗀 대가를 여기서 문다 — 안 들면 `None`이 `RunSpec`까지 간다.
        print("[ERROR] --target 또는 --task-dir 하나는 필요하다", file=sys.stderr)
        return 2
    # 슬라이스 파일도 같은 자리에서 본다 — `build_deps`가 다시 읽지만 그때는 이미 run 디렉터리와
    # 번호가 나간 뒤다. **렌더까지 본다** — 형식이 맞아도 대상 파일이 슬라이서가 본 것과 다르면
    # (drift) gutter가 다른 파일의 줄을 가리킨다. ponytail: 적재·렌더를 두 번 한다(JSON 하나와
    # 슬라이스가 걸친 파일들이라 싸다). 커지면 결과를 `build_deps`로 넘긴다.
    slice_set = None
    if args.slices is not None:
        try:
            slice_set = load_slices(args.slices)
            # `--task-dir`는 트리가 **아직 없다** — 렌더는 풀린 뒤(아래)에 한다. 지금 하면 entry
            # 파일이 없는 슬라이스가 전부 조용히 빠져(`render_slices` docstring) 아무것도 안 본 채
            # 통과한다.
            if args.task_dir is None:
                render_slices(slice_set, args.target)
        except SliceInputError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 2
    # 과제 설명(description.txt)을 읽어 캡슐에 실을 준비를 한다(PoV 배선). 주고서 없는
    # 파일이면 조용히 넘어가지 않고 멈춘다 — 슬라이스 파일과 같은 판단이다.
    description = task_description
    if args.description_file is not None:
        if not args.description_file.is_file():
            print(f"[ERROR] --description-file 없음: {args.description_file}", file=sys.stderr)
            return 2
        description = args.description_file.read_text(encoding="utf-8", errors="replace")
    started_at = datetime.now(UTC)
    spec = RunSpec(
        condition=DEFAULT_CONDITION if args.condition is None else args.condition,
        target=args.target,
        stop_after=args.stop_after,
        dry_run=args.dry_run,
        kb_snapshot=args.kb_snapshot,
        device=args.device,
        slices_path=args.slices,
        vuln_description=description,
        keep_responses=tuple(args.keep_responses),
        started_at=started_at,
    )
    # **모델을 한 번도 안 부르는 run은 막지 않는다.** 게이트의 근거가 "비용을 쓰기 전에"라
    # `--dry-run`·낮은 `--stop-after`처럼 Scout에 닿지 않는 run에는 근거가 없다. 막으면
    # 통과시키려고 장비·조건 라벨을 **지어내게** 되고, 그것이 CLAUDE.md §0이 금지하는
    # "그럴듯한 값으로 채우기"다 — 검사가 그 위반을 유도하면 안 된다.
    reaches_model = spec.stop_at()[0] >= Stage.SCOUT
    serving = bool(config.served_models()) and reaches_model
    blocking = [gap for gap in gaps if gap in BLOCKING_WHEN_SERVING] if serving else []
    if blocking:
        flags = ", ".join(BLOCKING_WHEN_SERVING[gap] for gap in blocking)
        # **실제로 빠진 것만 이름으로 부른다.** 고정 문장이면 이미 준 플래그를 다시 대라고
        # 말하게 되고, 조작자가 그것을 의심하며 시간을 태운다.
        print(
            f"[ERROR] 실서빙 프로필({args.profile})이라 {flags} 없이는 시작하지 않는다 — "
            "이 run은 완주해도 비교표에 넣을 수 없다",
            file=sys.stderr,
        )
        return 2
    # **`doctor`와 같은 프롬프트 검사를 run도 시작 전에 건다**(#600 H3). 내용 인식 집합에 없는 판
    # (반쯤 배선된 Critic·PoV 판)은 1~6단계의 GPU 시간을 쓰고 7·8단계에서 `PromptError`로 죽는다.
    # ERROR만 막는다 — 디스크에 새 판이 있다는 WARN은 `doctor`가 알린다. 모델에 안 닿는 run은 막지
    # 않고, 닿더라도 그 run이 부를 역할만 본다(`_check_prompts`). **`--dry-run`(3단계에서 선다)이
    # 가드 밖인 것은 의도다** — 모델을 안 부르므로 어긋난 프롬프트가 해를 끼칠 일이 없다.
    if reaches_model:
        prompt_errors = [
            check
            for check in _check_prompts(config, last_stage=Stage(spec.stop_at()[0]))
            if check.severity is Severity.ERROR
        ]
        if prompt_errors:
            for check in prompt_errors:
                print(check.render(), file=sys.stderr)
            return 2
    # **슬라이스 입력 프롬프트는 `--slices` 없이 부르지 않는다**(ADR 0012 결정 1). v3 Scout는
    # `SOURCE`를 슬라이스(원본 줄번호 gutter·파일 배너)로 정의하는데, 슬라이스가 없으면 그 자리에
    # 심볼 본문이 가고 **오류 없이** 어긋난다(#278). 실서빙 여부와 무관하다 — 어긋나는 것은
    # 프롬프트 계약이지 비용이 아니다. 모델에 안 닿는 run(`--dry-run`·낮은 `--stop-after`)은
    # 그 프롬프트를 부르지 않으므로 막지 않는다. 어느 프롬프트인지는 meta의 `input`이 정한다 —
    # 역할·버전을 여기 박으면 프롬프트가 바뀔 때 이 가드만 뒤처진다.
    if reaches_model and spec.slices_path is None:
        try:
            slice_input = _slice_input_prompts(config)
        except PromptError as exc:
            print(f"[ERROR] 프롬프트 로드 실패: {exc}", file=sys.stderr)
            return 2
        if slice_input:
            print(
                f"[ERROR] 슬라이스 입력 프롬프트({', '.join(slice_input)})를 부르는 run이라 "
                "--slices 없이는 시작하지 않는다 — 슬라이스가 없으면 그 SOURCE가 계약과 "
                "어긋난다 (ADR 0012 결정 1)",
                file=sys.stderr,
            )
            return 2
    # **켜진 Auditor SOURCE 옵션을 프롬프트가 서술하지 않으면 시작하지 않는다**(#351 리뷰
    # MEDIUM-2). 옵션은 SOURCE 모양을 바꾸는데 v4·v5는 「(file, start line) 순 · 15,000자」를
    # 말한다 — 어긋나도 오류 없이 돈다. v6(#418의 A/B 판)도 그 모양만 서술한다(⏵ 2026-10-04 서빙
    # 판 v7 = v6 + 옵션 넷의 서술 · meta 선언 — ⏵ 같은 날 v8 = v7 + 도구 루프가 그 서술·선언을
    # 물려받았고 ⏵ 2026-10-06 서빙 판은 v9 = v8 + `auditor_guards` 서술이었으며 ⏵ 같은 날부터
    # v10(v7을 전면 재작성 — 같은 여덟 옵션을 서술·선언한다)이다).
    # ⏵ 2026-10-09 — 서빙 판은 v11이다(v10 + `cwe` 서술 네 줄 — 옵션 서술은 v10과 같다, 이슈 #525).
    # ⏵ 2026-10-10 — 서빙 판은 v13이다(v11 + FACTS에서 `source` 사본을 뺀다 — 옵션 서술은 v11과
    # 같다, 이슈 #67).
    # ⏵ 2026-10-11 — 서빙 판은 v14다(v12 + FACTS에서 `source` 사본을 뺀다 — 옵션 서술은 v12와
    # 같다, 이슈 #67).
    # ⏵ 2026-10-13 — 서빙 판은 v15다(v14 + 계약 문장 여섯 — 옵션 서술·body는 v14와 같다, S19).
    # ⏵ 2026-10-14 — 서빙 판은 v16이다(v15 + `auditor_input_context` 서술 · 표지 줄
    # `auditor_write_window_marker` 설명 — 두 옵션의 서술은 v16부터다, S20 Phase 13).
    # ⏵ 2026-10-14 — 서빙 판은 v17이다(v16 + `auditor_slice_state` 서술 — 그 옵션의 서술은
    # v17부터다, S20 Phase 14 · 이슈 #438 (b). 위 두 옵션은 v17도 선언한다).
    # Auditor에 안 닿는 run은 막지 않는다(위 가드와 같은 판단).
    if spec.stop_at()[0] >= Stage.AUDITOR:
        try:
            undescribed = _undescribed_source_options(config)
        except PromptError as exc:
            print(f"[ERROR] 프롬프트 로드 실패: {exc}", file=sys.stderr)
            return 2
        # Auditor 거절을 먼저 낸다 - Critic·PoV 판 로드 실패가 이 메시지를 가리지 않는다.
        if undescribed:
            print(
                f"[ERROR] Auditor SOURCE 옵션 {', '.join(undescribed)}을 프롬프트가 서술하지 "
                "않는다 — meta의 source_options에 넣은 판으로 올리거나 옵션을 끈다 (#349)",
                file=sys.stderr,
            )
            return 2
        # **같은 SOURCE를 받는 Critic·PoV 좌석도 같은 가드다**(PR #676) - 그 run이 닿는 좌석만 본다.
        try:
            relayed = _undescribed_relayed_options(config, last_stage=Stage(spec.stop_at()[0]))
        except PromptError as exc:
            print(f"[ERROR] 프롬프트 로드 실패: {exc}", file=sys.stderr)
            return 2
        if relayed:
            print(f"[ERROR] {_relayed_options_message(relayed)}", file=sys.stderr)
            return 2
    # **도구 루프 상한과 Auditor 프롬프트 선언이 어긋나면 시작하지 않는다**(#419). 위 가드와 같은
    # 모양· 같은 판단이다 — 켜 놓고 프롬프트가 안 알리면 모델이 도구를 영영 안 쓰는데 run은 정상
    # 종료해 「도구 호출 0건」이 「모델이 안 골랐다」로 오독되고, 판은 루프인데 상한이 0이면(또는
    # 반대면) 노드가 첫 Auditor 호출에서 세워 그 앞 단계에 쓴 시간을 버린다. Auditor에 안 닿는 run은
    # 막지 않는다.
    if spec.stop_at()[0] >= Stage.AUDITOR:
        try:
            loop_problem_text = _auditor_loop_problem(config)
        except PromptError as exc:
            print(f"[ERROR] 프롬프트 로드 실패: {exc}", file=sys.stderr)
            return 2
        if loop_problem_text is not None:
            print(f"[ERROR] {loop_problem_text}", file=sys.stderr)
            return 2
    # **교대 프로필은 장비 파일 없이 모델에 닿지 않는다**(ADR 0012 Phase 6.5). 교대 명령과 서버
    # 기동 조건(util·창·인자)은 `config/serving/<device>.yaml`이 든다 — 장비를 모르면 무엇으로
    # 띄울지 모르고, 파일이 registry와 어긋나면(창 ≠ `context_limit`) 크기 거절을 조용히 먹는다.
    # 모델에 안 닿는 run은 교대도 안 하므로 막지 않는다 — 위 두 가드와 같은 판단이다.
    # ponytail: `build_deps`가 파일을 한 번 더 읽는다(`--slices`와 같은 이유 — 작다).
    if reaches_model and config.swap is not None:
        problem = (
            _SWAP_NEEDS_DEVICE
            if args.device is None
            else _serving_error(config, args.device, CONFIG_DIR)
        )
        if problem is not None:
            print(f"[ERROR] {problem}", file=sys.stderr)
            return 2
    # 채점기 설정은 **해제 앞**에서 본다 (#386) — 순수 검사(파일·환경변수 읽기)이고, 뒤에 두면
    # 설정 오류가 작업 디렉터리를 더럽혀 오타를 고친 재시도가 「비어 있지 않다」로 거부된다
    # (#397 리뷰가 「해제 앞인가」로 확인한 것과 같은 배치 이유 — 거부 뒤 작업 디렉터리가 비어
    # 있다). **8단계에 못 닿는 run은 이 검사를 건너뛴다** — 낼 PoC가 없는 run에 키·번들 검사를
    # 걸 근거가 없고, 막으면 통과시키려고 값을 지어내게 된다(위 `reaches_model` 가드와 같은
    # 판단). 건너뛰는 사실은 경고로 알린다.
    oracle = _oracle_for(args, unreachable=_oracle_unreachable_reason(spec, config))
    if isinstance(oracle, _OracleRefused):
        return 2
    # 🔴 **과제 아카이브는 여기서 푼다** — 모든 게이트 **뒤**, 되돌릴 수 없는 첫
    # 쓰기(`_claim_run_dir`) **앞**이다. 앞에서 풀면 뒤의 게이트가 exit 2를 낼 때 작업 디렉터리에
    # 트리가 남아, 오타를 고친 재시도가 "비어 있지 않다"로 거부된다(적대적 리뷰 2026-10-02 M-1).
    if args.task_dir is not None:
        try:
            safe_extract(args.task_dir / REPO_ARCHIVE_NAME, args.task_work_dir)
        except (UnsafeArchiveError, RuntimeError, OSError) as exc:
            # `UnsafeArchiveError` = 안전 위반·상한 초과, 그리고 **못 읽는 아카이브**(비-gzip — LFS
            # 포인터를 그대로 받은 경우 — 와 끊긴 gz; `safe_extract`가 `TarError`·`EOFError`를 이
            # 예외로 감싼다) · `RuntimeError` = `data_filter` 없는 인터프리터 · `OSError` = NTFS
            # 경로 제약(260자·콜론)과 디렉터리 자리에 파일이 있는 경우. 어느 쪽이든 트레이스백이
            # 아니라 **한 줄**로 멈춘다(`tarfile`의 줄바꿈 든 사유는 접는다). 해제가 중간에 멈췄으면
            # 앞 멤버가 남아 같은 인자로 다시 돌면 「비어 있지 않다」로 거부되므로 비우라고 말한다.
            detail = " ".join(f"{type(exc).__name__}: {exc}".split())
            print(
                f"[ERROR] 과제를 풀 수 없다: {detail} — 작업 디렉터리({args.task_work_dir})에 "
                "앞 멤버가 남았을 수 있다. 비우고 다시 한다",
                file=sys.stderr,
            )
            return 2
        if slice_set is not None:
            # 🔴 **`--task-dir`와 `--slices`를 같이 받는 안전은 이 두 검사에 선다 — 그리고 둘이
            # 전부는 아니다.** 슬라이스는 슬라이서가 어딘가에서 푼 트리를 보고 만든 것이고 여기서
            # 푼 트리는 **다른 디렉터리**다 — 같을 필요는 없다.
            #  · `render_slices`는 슬라이스가 가리키는 **경로가 있는데 줄 내용이 다른** 것(drift)을
            #    `SliceInputError`로 막는다 — 하네스 사본과 글자 단위로 대조한다.
            #  · **경로가 아예 없는** 슬라이스(번들 최상위 접두어 `src-vul/` 불일치 · 다른 과제의
            #    slices.json)는 `render_slices`가 오류 없이 **건너뛴다** — 그래서 렌더된 수가
            #    입력 수와 다르면 여기서 멈춘다(`_unmatched_slices_error`).
            #  · **못 잡는 것:** 경로와 줄 내용이 우연히 다 맞는 다른 과제의 슬라이스. 이 번들의
            #    것이라는 보증은 없다 — 슬라이스 파일 쪽에 과제 해시가 없다.
            # ⚠ 기존 `--target`+`--slices` 경로도 entry 파일이 없는 슬라이스를 조용히 건너뛴다
            # (`unmatched_slices`로만 센다) — 사람이 루트를 골라 주는 경로라 이 분기만 막고 그쪽은
            # 바꾸지 않았다.
            try:
                rendered = render_slices(slice_set, args.task_work_dir)
            except SliceInputError as exc:
                print(
                    f"[ERROR] {exc} — 해제 트리가 {args.task_work_dir}에 남았다. "
                    "다른 슬라이스로 다시 하려면 그 디렉터리를 비우고 시작한다",
                    file=sys.stderr,
                )
                return 2
            unmatched = _unmatched_slices_error(slice_set, rendered, args.task_work_dir)
            if unmatched is not None:
                print(f"[ERROR] {unmatched}", file=sys.stderr)
                return 2
    run_id, run_dir = _claim_run_dir(args.artifacts_root, started_at)
    # TERM·HUP에도 run 끝 내림이 돌게 한다(`_terminate_as_exit`).
    with _terminate_as_exit(), (run_dir / "trace.jsonl").open("w", encoding="utf-8") as stream:
        result, deps = execute(
            config,
            spec,
            run_id=run_id,
            run_dir=run_dir,
            trace_stream=stream,
            oracle=oracle,
        )
        # 결과는 `result`에 다 실렸다 — 저장소를 여기서 닫는다(#156). 안 닫으면 이 프로세스가
        # 끝날 때까지 `state.db`가 열려 있다.
        deps.store.close()
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
    reason = result.stop_reason
    code = _exit_for(reason)
    if reason is not None and code != 0:
        # 사유 문구(무엇을 고칠지)는 위 `[WARN ]` 줄에 있다 — 이 줄은 코드가 왜 3인지만 말한다.
        print(
            f"[ERROR] 장애로 중단했다: {reason.value} — 원인 문구는 위 [WARN ] 줄에 있다 · "
            f"종료 코드 {code}",
            file=sys.stderr,
        )
    return code


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


# `cwe`가 `None`(v11의 `no_signal`)인 결과의 `ruleId` — CWE 꼴(`^CWE-\d+$`)이 아닌 센티넬이다.
_SARIF_NO_CWE_RULE_ID = "no-cwe"


def _sarif_result(finding: Finding) -> dict:
    """SARIF 2.1.0 `result` 하나.

    ⏵ 2026-10-09 (이슈 #525 선택지 1) — `cwe`가 `None`(v11의 `no_signal`)이면 `ruleId`를
    **CWE가 아닌 센티넬 `no-cwe`**(`_SARIF_NO_CWE_RULE_ID`)로 둔다.
    `ruleId`는 JSON 스키마상 선택(`result.required`는 `message`뿐)이지만 **규범 본문은 조건부
    SHALL이다** — OASIS SARIF 2.1.0 §3.27.5 *"If `rule.id` is absent, `ruleId` SHALL be present"*.
    이 result는 `rule`을 안 싣으므로 키를 빼면 규범을 어긴다. `"None"`·자리표시 CWE를 넣으면
    소비자가
    그것을 CWE 규칙으로 읽고 묶는다 — 없는 CWE를 지어내는 것과 같다. 그래서 CWE 꼴이 아닌 이름을
    쓴다(no-CWE 결과끼리는 한 규칙으로 묶인다 — 그것은 참이다). CWE가 있으면 종전과 같은
    dict다(키 순서 포함).
    """
    rule_id = finding.cwe if finding.cwe is not None else _SARIF_NO_CWE_RULE_ID
    return {
        "ruleId": rule_id,
        "level": "warning",
        "message": {"text": f"{finding.title} (state={finding.state.value})"},
        "properties": {
            "findingId": finding.finding_id,
            "confidence": finding.confidence,
            # ⚠ 2026-10-08부터 **모델이 인용한 것이 아니라 하네스가 관측해 붙인 것**이 섞인다
            # (이슈 #552 — 6단계 `gate_node.attach_observed`). 실패 기록(`ok=False`)도 센다.
            # 키 이름은 외부 소비자 계약이라 유지한다.
            "evidenceRefs": list(finding.evidence_refs),
        },
    }


# --- bench ------------------------------------------------------------------


def bench(args: argparse.Namespace) -> int:
    """§3 체크리스트를 실제 엔드포인트에 대고 잰다. **진단 중 서버에 요청을 보내는 건 이것뿐이다.**
    (`preflight`(#534)는 ssh·TCP·`docker run`까지만 한다. `run`도 서버에 닿지만 파이프라인이다.)

    종료 코드는 "bench 자신이 제대로 돌았는가"만 뜻한다. 측정 결과의 통과·실패는 JSON의
    `checklist`에 있다 — 체크리스트 실패를 종료 코드로 옮기면 서버가 없는 개발 장비에서
    CI가 빨개지고, 그러면 아무도 이 커맨드를 돌리지 않게 된다.
    """
    config = load_profile(args.profile, tool_runner=args.tool_runner)
    # **registry에 쓸 것이면 장비 라벨을 먼저 요구한다** (#124). `run`은 같은 결여에
    # `exit 2`인데 여기만 경고하고 진행해서 비대칭이 있었다. 결여의 대가가 더 큰 쪽이
    # 여기다 — run의 산출물은 그 run 하나에 갇히고 `incomplete_fields()`가 비교표 제외를
    # 판정하지만, registry는 이후 모든 예산 추정과 `cli estimate`가 읽는 **공용 파일**이고
    # `measured_on: null`은 거기 영구히 남는다(docs/portability.md).
    #
    # **막는 것은 `--apply`뿐이다.** 읽기 전용 `bench`는 라벨 없이도 돈다 — 근거가
    # "공용 파일이 오염된다"이므로 아무것도 안 쓰는 실행에는 그 근거가 없다.
    # **Fake 프로필도 막지 않는다** — `run` 게이트와 같은 판단이고(그쪽 주석 참조),
    # 애초에 쓸 측정값이 안 나온다.
    #
    # **재기 전에 판정한다.** #119가 run에 세운 원칙과 같은 자리다. 재고 나서 거부하면
    # 측정 시간을 통째로 버리고, 그러면 조작자가 라벨을 **지어내서** 다시 돌리게 된다.
    if args.apply and args.device is None and config.served_models():
        print(
            f"[ERROR] 실서빙 프로필({args.profile})이라 --device 없이는 --apply 하지 않는다 — "
            "measured_on: null이 추적되는 config/models.yaml에 영구히 남고, "
            "다음 사람이 그 값으로 다른 장비를 예측하게 된다 (docs/portability.md)",
            file=sys.stderr,
        )
        return 2
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
        "값 집합은 열려 있다(ADR 0006) - 팀이 쓰는 라벨은 docs/portability.md에 있다. "
        "호스트명으로 자동으로 채우지 않는다: 안 주면 '확인 못 함'이다. "
        "run에서 실서빙 프로필이면 거부한다(exit 2). 교대를 선언한 프로필이면 장비 파일 "
        "config/serving/<device>.yaml을 고르고, 없거나 registry와 어긋나면 거부한다(exit 2)",
    )

    subs.add_parser("doctor", parents=[common, device])

    est = subs.add_parser("estimate", parents=[common])
    est.add_argument("--target", type=Path, required=True)

    run_cmd = subs.add_parser("run", parents=[common, device])
    # **`required=True`가 아니다** — `--task-dir`가 target을 정할 수 있다(#356). 둘 다 없으면
    # `run()`이 exit 2로 막는다(그 검사가 argparse 자리를 대신한다). `estimate`는 그대로 필수다.
    run_cmd.add_argument("--target", type=Path, default=None)
    run_cmd.add_argument(
        "--task-dir",
        type=Path,
        default=None,
        help="CyberGym 과제 번들 디렉터리(repo-vul.tar.gz + description.txt. 저자 생성기가 더하는 "
        "README.md·submit.sh도 받는다). 그 밖의 항목이 하나라도 있으면(정답 자료 patch.diff·"
        "error.txt·repo-fix.tar.gz 포함) 풀기 전에 거부한다(exit 2). 주면 아카이브를 "
        "--task-work-dir에 풀어 그 트리를 --target으로 쓰고 description.txt를 캡슐에 싣는다. "
        "--target·--description-file과 같이 주지 않는다(exit 2). --slices와는 같이 쓴다 - "
        "슬라이스 경로는 작업 디렉터리 기준(번들 최상위 디렉터리 `src-vul/` 포함)이어야 하고, "
        "풀린 트리와 줄 내용이 다르거나(drift) 트리에 없는 슬라이스가 하나라도 있으면 exit 2다 "
        "(그때 트리는 작업 디렉터리에 남는다). 이 번들의 슬라이스인지까지는 보증하지 않는다",
    )
    run_cmd.add_argument(
        "--mask-map",
        type=Path,
        default=None,
        help="저자 mask_map.json 경로. 마스킹 서버(README 표준 기동)와 함께 "
        "--oracle-submit을 쓸 때 필요하다(--oracle-submit 없이 주면 거부한다, exit 2). "
        "서버는 제출을 masked task_id로 받지만 "
        "DB·/query-poc는 unmask된 id를 쓰므로, 조회에만 이 맵으로 바꾼 실 id를 쓴다",
    )
    run_cmd.add_argument(
        "--oracle-submit",
        action="store_true",
        help="8단계가 낸 PoC를 채점기(oracle)에 내보낸다(#386). "
        "vul 다리에는 모델이 submit한 PoC와(pov v3 이상) 중간 gen을 내고 중간 gen은 판정 조회도 "
        "한다. fix 다리와 최종 판정 조회는 최종 PoC 하나에만 run당 한 번 돈다 - vul 제출이 "
        "성공한 최종 후보가 "
        "하나뿐이면 그 하나에 돌고, 둘 이상이면 같은 PoC 바이트끼리 접은 뒤 "
        "전순위(vul 응답 종료 코드 계단 > Critic 판정 > 내부 근거 등급 > confidence > "
        "작은 바이트 > PoC 해시. 계단은 크래시, 모름, 크래시 아님 순이다)로 고른 하나에 "
        "돈다 - 등급·confidence·Critic 판정 중 모르는 값이 있는 후보가 있으면 "
        "안 돈다(oracle_final은 status "
        "none, reason grade_unknown). "
        "--task-dir·--oracle-server·--oracle-canary와 함께만 쓴다. 과제 id·에이전트 id는 "
        "번들의 submit.sh에서 읽고 키는 환경변수 CYBERGYM_API_KEY로만 받는다. 제출 값은 "
        "{run_dir}/oracle/verdict.json에 남고 내부 등급(E4)을 올리지 않는다. 8단계에 닿지 않는 run"
        "(--dry-run, 8 미만의 --stop-after, pov 좌석이 없는 프로필)에서는 아무것도 제출하지 않고 "
        "경고만 한다",
    )
    run_cmd.add_argument(
        "--oracle-server",
        default=None,
        help="채점기 서버 주소(스킴://호스트:포트). 기본값이 없다 - --oracle-submit이면 필수다. "
        "조작자가 정한다: API 키가 가는 곳이라 과제 번들(submit.sh)이 가리키는 주소로는 보내지 "
        "않고, 번들의 주소와 다르면 거부한다(exit 2). 지원하는 과제 id는 마스킹을 안 한 서버의 "
        "실제 id(예 arvo:1065)이거나, 마스킹된 id에 --mask-map을 같이 준 것이다",
    )
    run_cmd.add_argument(
        "--oracle-canary",
        type=Path,
        default=None,
        help=f"카나리 보고서 경로({_CANARY_COMMAND}의 --out). --oracle-submit이면 필수다 - "
        "없거나 통과한 보고서가 아니면 제출 전에 거부한다(exit 2). 호스트 "
        "vm.mmap_rnd_bits가 28보다 크면 같은 입력의 판정이 갈린 것을 쟀고(한 과제·MSan·n=10), "
        "그 값은 네임스페이스가 없어 Docker 재기동으로 32로 돌아갈 것으로 보므로(재기동 뒤 "
        "재측정 0건) 보고서를 run마다 명시로 넘긴다. 하네스가 이 도구를 직접 돌리지는 "
        "않는다 - 조작자가 돌려 보고서를 만들어 둔다"
    )
    run_cmd.add_argument(
        "--task-work-dir",
        type=Path,
        default=None,
        help="--task-dir의 아카이브를 풀 빈 디렉터리. 비어 있지 않으면 거부한다(exit 2) - "
        "해제가 중단되면 앞 멤버가 남아 두 과제가 섞인다",
    )
    # **기본값을 argparse에 두지 않는다.** 값은 `run()`이 `DEFAULT_CONDITION`으로 채우되,
    # `None`이 여기까지 살아 와야 "안 줬다"와 "기본값과 같은 라벨을 의도해서 줬다"가
    # 구별된다 — `runner.missing_run_labels`가 그 구분 위에 서 있다.
    run_cmd.add_argument(
        "--condition",
        default=None,
        help=f"조건 라벨. 안 주면 {DEFAULT_CONDITION}으로 기록되고 경고한다 - "
        "실서빙 프로필에서는 거부한다(exit 2)",
    )
    run_cmd.add_argument("--seed", type=int)
    run_cmd.add_argument("--stop-after", type=int, choices=range(MAX_STAGE + 1))
    run_cmd.add_argument(
        "--slices",
        type=Path,
        default=None,
        help="슬라이서 산출물 slices.json(slice-v2). 주면 슬라이스 1개가 타깃 1개다"
        "(ADR 0012 결정 1). 못 믿는 파일이거나 --target과 어긋나면(drift) run 디렉터리를"
        " 만들기 전에 거부한다(exit 2). 슬라이스 입력 프롬프트(meta input: slice - v3 Scout)를"
        " 부르는 run은 이것 없이 거부한다(exit 2)",
    )
    run_cmd.add_argument(
        "--description-file",
        type=Path,
        default=None,
        help="CyberGym 과제 description.txt 경로. 주면 캡슐에 vuln_description 섹션으로 실려 "
        "Scout·Auditor·PoV가 본다(PoV 배선). 번들을 통째로 주려면 --task-dir를 쓴다 "
        "(그러면 아카이브까지 풀고 설명도 거기서 읽는다) - 둘을 같이 주지 않는다",
    )
    run_cmd.add_argument(
        "--keep-responses",
        nargs="+",
        choices=PROMPTED_ROLES,
        default=(),
        metavar="ROLE",
        help="이 역할들의 성공한 호출도 응답 원문을 prompts/에 남긴다(#612). 서버가 추론을 따로 "
        "주면 그 추론도 *_reasoning*.txt로 남는다. 안 주면 스키마 실패 "
        "시도의 원문(과 추론)만 남는다. Auditor 응답은 수십만 자일 수 있다. "
        "--dry-run에서는 prompts/가 없어 조용히 무효다",
    )
    run_cmd.add_argument("--dry-run", action="store_true")
    run_cmd.add_argument("--format", choices=("md", "sarif"), default="md")
    run_cmd.add_argument("--kb-snapshot", default=DEFAULT_KB_SNAPSHOT)

    bench_cmd = subs.add_parser("bench", parents=[common, device])
    bench_cmd.add_argument("--roles", nargs="+", default=None)
    bench_cmd.add_argument("--contexts", nargs="+", type=int, default=list(DEFAULT_CONTEXTS))
    bench_cmd.add_argument("--concurrency", nargs="+", type=int, default=list(DEFAULT_CONCURRENCY))
    bench_cmd.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    bench_cmd.add_argument("--apply", action="store_true")

    # `common`을 안 쓴다 — `--artifacts-root`·`--tool-runner`를 받아 놓고 안 쓰는 플래그가 된다.
    # 인자가 없으면 그 점검은 건너뛴다고 말한다(조용히 OK로 세지 않는다).
    pre = subs.add_parser(
        "preflight",
        description="실모델·실도구 run의 선결조건을 접속해서 한 번에 점검한다(#534). ssh·TCP·"
        "docker를 일부러 쓴다. ERROR가 하나라도 있으면 exit 2다. 건너뛴·못 확인한 점검(WARN)은 "
        "기본으로는 exit 0이다 - 자동화는 --strict로 돌린다(WARN이 있으면 exit 4)",
    )
    pre.add_argument("--profile", required=True)
    pre.add_argument(
        "--device", default=None, help="장비 파일 config/serving/<device>.yaml - 교대 점검의 기준"
    )
    pre.add_argument("--task-dir", type=Path, default=None, help="과제 번들 - 허용 파일·submit.sh")
    pre.add_argument("--oracle-server", default=None, help="채점기 서버 주소 - TCP로 닿는지 본다")
    pre.add_argument("--oracle-canary", type=Path, default=None, help="카나리 보고서 경로")
    pre.add_argument(
        "--strict",
        action="store_true",
        help="WARN(건너뜀·못 확인)이 하나라도 있으면 exit 4로 끝낸다 - 자동화는 이것을 쓴다. "
        "기본은 사람이 읽는 용도라 WARN만 있으면 exit 0이다",
    )

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
    if args.command == "preflight":
        # 호출 시점에 import한다 — `src.preflight`가 이 파일의 `Check`·`load_profile`을 가져다 쓴다.
        from src.preflight import run_preflight

        return run_preflight(args)
    return gc(args)


if __name__ == "__main__":
    raise SystemExit(main())
