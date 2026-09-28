"""단계 경계 모델 교대 — 교대 명령을 부르고, 뜬 것을 다시 본다 (ADR 0012 Phase 6.5 — ADR 0009).

**교대는 콜드 재기동이다.** 교대 명령(장비 파일 `config/serving/<device>.yaml`의 `command`)이 앞
단계 서버를 끄고 `keys`만 새로 띄운다. vLLM `/sleep`·`/wake_up`은 쓰지 않는다. 하네스와 같은
기계의 로컬 subprocess다 — `ssh`로 감싸지 않는다. 키 없이 부르면 명령이 자기 run의 서버를 전부
내린다(run 끝 내림).

**하네스가 아는 것은 언제·어느 모델이다.** 어떻게 띄우는지(util·창·인자)는 명령이 장비 파일에서
읽는다. 러너는 `src/adapters/`를 import 하지 않으므로 여기서 만든 콜러블이 `Deps.swap_to`로 경계를
넘는다(`symbols_for`·`counter_for_role`과 같은 자리).

## 명령과의 계약 — 장비별 교대 스크립트가 지킬 것

하네스 쪽은 이 모듈이 전부다. 교대 명령(예: ATOM 스크립트)은 장비마다 따로 쓰고 아래를 지킨다.

- **argv = `command + keys`.** 끝나면 **정확히 `keys`만** 떠 있어야 한다 — `keys`에 없는 자기 서버는
  내리고, 없는 것은 띄운다. 이미 떠 있는 키(상주 역할)를 다시 띄울지는 명령이 정한다.
- **인자 없음 = 자기 run의 서버를 전부 내린다.** 러너가 run 끝에 성공·실패와 무관하게 부른다.
  떠 있는 것이 없으면 아무것도 안 하고 0으로 끝난다.
- **자기 run의 서버만 건드린다.** 소유는 `HARNESS_RUN_ID`로 가린다 — 남의 프로세스는 내리지 않는다.
- **환경** — `HARNESS_PYTHON`(하네스 인터프리터 — 장비 파일 `config/serving/$HARNESS_DEVICE.yaml`과
  registry를 같은 파이썬으로 읽는다) · `SWAP_DEADLINE_S`(= `SwapPolicy.settle_timeout_s`, 교대 한
  번의 마감) · `HARNESS_RUN_ID` · `HARNESS_DEVICE`. 작업 디렉터리는 저장소 루트다.
- **준비 확인은 명령이 먼저 한다.** 0으로 끝내기 전에 띄운 서버마다 요청 하나(채팅이면 1토큰)가
  돌아오는 것을 본다. 하네스의 `/v1/models` 재확인(아래)은 **2차 확인**이다 — 재시도가 없다.
- **종료코드** — 0은 「`keys`만 떠 있고 요청을 받는다」다. 0이 아니면 이유를 가리지 않고
  `ModelSwapError`다 — 교대면 run이 멈추고(`model_swap_failed`), run 끝 내림이면 경고로 남는다.
  실패해도 띄운 것을 남길 수 있다 — run 끝 내림이 다시 부른다.
- **마감** — `SWAP_DEADLINE_S` 안에 끝낸다. 넘으면 부분 보고를 내고 0이 아닌 코드로 끝낸다. 하네스는
  마감 + `_REPORT_GRACE_S` 뒤에 명령을 죽이고 인자 없이 한 번 부른다.
- **stdout 마지막 비지 않은 줄이 JSON 객체 보고다.** 실패해도 낼 수 있으면 낸다. 중첩은
  `server.<key>.<field>`로 펴서 `model_swap` 이벤트의 `report.*`에 싣는다(목록은 JSON 문자열).
  JSON이 아니거나 못 읽으면 보고가 없는 것으로 읽는다. 그 앞 줄은 사람용 로그로 자유다 — 실패하면
  하네스가 stdout·stderr 꼬리를 오류 상세에 싣는다. 권하는 필드: `ok`·`models`·서버별
  `action`·`util`·`max_num_seqs`·`max_model_len`·`args`·`revision`·`load_s`·`ready_s`·`attempts` ·
  `memavail_before`·`memavail_after`·`vllm_version`·`error`.
- ⚠ **띄운 vLLM의 stdout·stderr는 로그 파일로 돌린다**(기동마다 새 파일). 서버가 명령의 파이프를
  물려받으면 `subprocess.run`이 EOF를 못 받아 **서버가 죽을 때까지 안 돌아온다.**

## 명령이 0으로 끝났다고 믿지 않는다

끝나면 올린 키마다 `/v1/models`를 읽어 그 이름으로 떠 있는지와 `max_model_len`이 registry
`context_limit`과 **같은지** 본다. 2026-09-25 ATOM에서 `context_limit` 40,960인 Scout를 16,384로
띄운 채 돌렸고 하네스는 아무 말도 안 했다 — 프롬프트 12건이 400으로 거절됐는데, 400은 러너가
**그 후보만 버리는** 크기 거절이라 run은 「완주」로 끝났다(`run_20260925_0005`·`0006`, #331).
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from src.config import CONFIG_DIR, ModelEntry
from src.interfaces.model_client import ModelSwapError

# 교대 명령의 상대 경로(`tools/serve/…`)가 풀리는 곳 — 저장소 루트.
REPO_ROOT = CONFIG_DIR.parent
# 명령이 자기 마감(`SWAP_DEADLINE_S`)에 걸린 뒤 부분 보고를 내고 끝낼 여유. 하네스는 마감 +
# 이만큼 기다린 뒤 명령을 죽인다 — 같은 값이면 둘이 경주한다. **계획값 — 미실측(2026-09-29).**
_REPORT_GRACE_S = 30.0
# 실패 사유로 싣는 명령 출력 꼬리.
_TAIL_CHARS = 4000

# `trace.TraceValue`와 같은 집합이다 — 어댑터는 `src/orchestrator/`를 import 하지 않는다.
Scalar = str | int | float | bool | None
SwapReport = dict[str, Scalar]
SwapTo = Callable[[tuple[str, ...]], SwapReport]
# `/v1/models` 조회: (endpoint, key) → `ok`·`reason`·`max_model_len`. 기본은 `bench.check_startup`.
# 요청 실패·응답 파싱 실패를 예외로 올리지 않고 `ok=False`와 `reason`으로 싣는다(대역도 같다).
Probe = Callable[[str, str], Mapping[str, object]]


def swap_runner(
    command: tuple[str, ...],
    *,
    models: Mapping[str, ModelEntry],
    deadline_s: float,
    run_id: str,
    device: str,
    probe: Probe | None = None,
    cwd: Path = REPO_ROOT,
) -> SwapTo:
    """`keys`만 떠 있게 하는 콜러블. 돌려주는 값은 명령의 보고를 스칼라로 편 것이다.

    `probe`를 안 주면 `bench.check_startup`이다. **부를 때 import한다** — `src.bench`가
    `src.adapters.openai_compat`을 import하면 이 패키지(`__init__` → `factory` → 이 모듈)가 먼저
    초기화되므로, 모듈 머리에서 import하면 `import src.bench`가 첫 import일 때 반쯤 초기화된
    `src.bench`를 만나 죽는다(`tests/test_import_order.py`).
    """
    if probe is None:
        # ponytail: 순환을 호출 시점으로 미룬 것이다. 의존 방향(어댑터 → bench)은 그대로다.
        # 끊으려면 `check_startup`을 표준 라이브러리만 쓰는 `bench_metrics`로 내린다
        # (`openai_compat`이 이미 서버를 거기서 읽는다). 그때는 그 함수의 자리(`src/bench.py`)를
        # 적은 문서를 같이 고친다(`git grep check_startup -- docs`).
        from src.bench import check_startup

        probe = check_startup
    env = {
        **os.environ,
        "HARNESS_PYTHON": sys.executable,
        "SWAP_DEADLINE_S": f"{deadline_s:g}",
        "HARNESS_RUN_ID": run_id,
        "HARNESS_DEVICE": device,
    }

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - argv는 장비 파일이 정한다
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=deadline_s + _REPORT_GRACE_S,
            check=False,
        )

    def swap_to(keys: tuple[str, ...]) -> SwapReport:
        # **실패는 `ModelSwapError` 하나다** — 러너가 그것만 「교대 실패」로 읽는다. 그 밖의 예외가
        # 새면 단계 진입 교대는 run을 매니페스트 없이 끝낸다(러너의 정지 갈래가 못 잡는다). 그래서
        # 이 경계에서 감싼다. `BaseException`(인터럽트)은 감싸지 않는다 — 사람이 멈춘 것이다.
        label = " ".join(keys) or "(전부 내림)"
        try:
            return swap_once(keys, label)
        except ModelSwapError:
            raise
        except Exception as exc:
            raise ModelSwapError(
                f"모델 교대 중 예상 밖 오류({type(exc).__name__}: {exc}): {label}"
            ) from exc

    def swap_once(keys: tuple[str, ...], label: str) -> SwapReport:
        try:
            done = run([*command, *keys])
        except subprocess.TimeoutExpired as exc:
            # 죽인 것은 명령뿐이고 명령이 띄우던 서버는 남는다 — 내림을 한 번 부른다.
            raise ModelSwapError(
                f"모델 교대가 마감 {deadline_s:g}초(+{_REPORT_GRACE_S:g}) 안에 안 끝났다: {label}"
                f" — 내림 명령을 불렀다({_teardown(run, command)})",
                detail=_tail(exc.stdout, exc.stderr),
                report=_report(exc.stdout),
            ) from exc
        except OSError as exc:
            raise ModelSwapError(f"교대 명령을 실행하지 못했다({command[0]}): {exc}") from exc
        report = _report(done.stdout)
        if done.returncode != 0:
            raise ModelSwapError(
                f"모델 교대가 실패했다(exit {done.returncode}): {label}",
                detail=_tail(done.stdout, done.stderr),
                report=report,
            )
        for key in keys:
            _verify(key, models[key], probe, report)
        return report

    return swap_to


def _teardown(
    run: Callable[[list[str]], subprocess.CompletedProcess[str]], command: tuple[str, ...]
) -> str:
    """내림 명령을 한 번 부르고 결과를 한 마디로. 원래 실패를 가리지 않도록 예외를 안 올린다.

    ⚠ **원 호출과 같은 상한**(마감 + `_REPORT_GRACE_S`)이라 마감을 넘긴 교대는 최악 그 두 배를
    기다린다. 내림 전용으로 짧게 자르지 않는 것은 그 값을 잰 적이 없어서다 — 지어낸 상한으로
    내림을 도중에 죽이면 서버가 남고, 그쪽이 기다리는 것보다 나쁘다.
    """
    try:
        return f"exit {run(list(command)).returncode}"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"실패: {type(exc).__name__}"


def _verify(key: str, entry: ModelEntry, probe: Probe, report: SwapReport) -> None:
    """`key`가 그 이름으로 떠 있고 창이 registry `context_limit`과 같은가. fake는 서버가 없다.

    **2차 확인이다** — 명령이 0으로 끝나기 전에 자기 준비 확인을 했다는 계약(모듈 docstring)을
    전제하므로 재시도하지 않고 마감에도 묶지 않는다. 조회 한 번은 `bench.check_startup`의 상한
    (60 초)을 받아, 서버가 응답하지 않으면 키 수만큼 더 기다릴 수 있다.
    """
    if entry.is_fake:
        return
    served = probe(entry.endpoint, key)
    if not served.get("ok"):
        raise ModelSwapError(
            f"교대 뒤 {key} 준비 확인 실패({entry.endpoint}): {served.get('reason')}",
            report=report,
        )
    window = served.get("max_model_len")
    if window != entry.context_limit:
        raise ModelSwapError(
            f"{key}의 서빙 창 max_model_len {window} ≠ registry context_limit "
            f"{entry.context_limit} — 창이 다르면 크기 거절이 후보 단위로 조용히 버려진다. "
            "장비 파일의 max_model_len을 고친다",
            report=report,
        )


def _report(stdout: str | bytes | None) -> SwapReport:
    """stdout 마지막 비지 않은 줄의 JSON 객체를 편다. 없거나 JSON 객체가 아니면 빈 dict다."""
    lines = _text(stdout).strip().splitlines()
    try:
        parsed = json.loads(lines[-1]) if lines else None
        return _flatten(parsed) if isinstance(parsed, dict) else {}
    # 못 내려가는 깊이도 「못 읽었다」다. 파서(C)가 읽는 깊이를 `_flatten`(파이썬 재귀)이 못
    # 내려갈 수 있어 펴기까지 이 try 안에 둔다 — 2026-09-29 CPython 3.14에서 객체 1,500 겹이 그랬다.
    except (ValueError, RecursionError):
        return {}


def _flatten(value: Mapping[str, object], prefix: str = "") -> SwapReport:
    """중첩은 점으로 편다(`server.<key>.<field>`). 스칼라가 아닌 값(목록)은 JSON 문자열로 둔다."""
    flat: SwapReport = {}
    for key, item in value.items():
        name = f"{prefix}{key}"
        if isinstance(item, Mapping):
            flat.update(_flatten(item, f"{name}."))
        elif item is None or isinstance(item, str | int | float | bool):
            flat[name] = item
        else:
            flat[name] = json.dumps(item, ensure_ascii=False)
    return flat


def _text(stream: str | bytes | None) -> str:
    # `TimeoutExpired`의 출력은 `text=True`여도 bytes로 올 수 있다.
    if isinstance(stream, bytes):
        return stream.decode("utf-8", "replace")
    return stream or ""


def _tail(*streams: str | bytes | None) -> str:
    return "".join(_text(stream) for stream in streams)[-_TAIL_CHARS:]
