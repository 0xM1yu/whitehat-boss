"""단계 경계 모델 교대 — 교대 명령을 부르고, 뜬 것을 다시 본다 (ADR 0012 Phase 6.5 — ADR 0009).

**교대는 콜드 재기동이다.** 교대 명령(장비 파일 `config/serving/<device>.yaml`의 `command`)이 앞
단계 서버를 끄고 `keys`만 새로 띄운다. vLLM `/sleep`·`/wake_up`은 쓰지 않는다. 이 모듈은
명령을 로컬 subprocess로 부르고 ssh를 모른다 — 서빙이 원격이면 **명령 쪽이** 감싼다
(`tools/serve/swap-remote.sh`, 장비 파일 `vastai-remote.yaml`). 그때도 아래 계약은 원격의
명령이 지키고 `_verify`의 `/v1/models` 조회는 로컬에서 터널을 지난다. 키 없이 부르면 명령이
자기 run의 서버를 전부 내린다(run 끝 내림).

**하네스가 아는 것은 언제·어느 모델이다.** 어떻게 띄우는지(util·창·인자)는 명령이 장비 파일에서
읽는다. 러너는 `src/adapters/`를 import 하지 않으므로 여기서 만든 콜러블이 `Deps.swap_to`로 경계를
넘는다(`symbols_for`·`counter_for_role`과 같은 자리).

## 명령과의 계약 — 교대 명령이 지킬 것

하네스 쪽은 이 모듈이 전부다. 교대 명령은 장비 무관 `tools/serve/swap.sh` 하나이고(장비 차이는 장비
파일이 든다), 다른 명령을 쓰는 장비도 아래를 지킨다.

- **argv = `command + keys`.** 끝나면 **정확히 `keys`만** 떠 있어야 한다 — `keys`에 없는 자기 서버는
  내리고, 없는 것은 띄운다. 이미 떠 있는 키(상주 역할)를 다시 띄울지는 명령이 정한다.
- **인자 없음 = 자기 run의 서버를 전부 내린다.** 러너가 run 끝에 성공·실패와 무관하게 부른다 —
  TERM·HUP로 끝나는 run도 부른다(`cli.run`이 `SystemExit`으로 바꾼다). KILL·OOM으로 죽은 run은 못
  부른다 — 그 뒤처리는 명령 쪽(감시자 등)이 맡는다. 떠 있는 것이 없으면 아무것도 안 하고 0으로
  끝난다. ⚠ 신호로 끝난 run의 이 호출은 TERM·HUP를 **무시한 채** 시작한다 — 하네스가 첫 신호 뒤
  두 신호를 `SIG_IGN`으로 두고(내림을 끊지 않게) exec는 무시를 물려준다. 명령이 띄우는 자식도
  물려받아 그 자식에 보낸 TERM·HUP가 안 먹을 수 있다(WSL 실측: `sleep`은 `kill -TERM`을
  무시했고, GNU `timeout` 9.4는 제 자식을 TERM으로 끝냈다).
- **자기 run의 서버만 건드린다.** 소유는 `HARNESS_RUN_ID`로 가린다 — 남의 프로세스는 내리지 않는다.
- **환경** — `HARNESS_PYTHON`(하네스 인터프리터 — 장비 파일 `config/serving/$HARNESS_DEVICE.yaml`과
  registry를 같은 파이썬으로 읽는다) · `SWAP_DEADLINE_S`(= `SwapPolicy.settle_timeout_s`, 교대 한
  번의 마감) · `HARNESS_RUN_ID` · `HARNESS_DEVICE`. 작업 디렉터리는 저장소 루트다.
- **준비 확인은 명령이 먼저 한다.** 0으로 끝내기 전에 띄운 서버마다 요청 하나(채팅이면 1토큰)가
  돌아오는 것을 본다. 하네스의 `/v1/models` 재확인(아래)은 **2차 확인**이다 — 재시도가 없다.
- **종료코드** — 0은 「`keys`만 떠 있고 요청을 받는다」다. 0이 아니면 이유를 가리지 않고
  `ModelSwapError`다 — 교대면 run이 멈추고(`model_swap_failed`), run 끝 내림이면 경고로 남는다
  (어느 쪽이든 러너 `_swap`이 `model_swap ok=false` 이벤트를 먼저 남긴다).
  명령을 띄우기도 전의 거부(`command[0]`이 PATH로 WSL 런처로 풀림 — `_resolved`)도 같은
  `ModelSwapError`이고 **교대를 시도할 때** 난다(만들 때가 아니다). 교대하지 않는
  run(`--dry-run`·낮은 `--stop-after`)은 만들기만 하므로 영향이 없다 — 단 run 끝 내림은 부르므로
  막힌 환경에서는 경고 한 줄과 `model_swap ok=false` 이벤트 한 줄이 남고 run은 정상으로 끝난다.
  실패해도 띄운 것을 남길 수 있다 — run 끝 내림이 다시 부른다.
- **마감** — `SWAP_DEADLINE_S` 안에 끝낸다. 넘으면 부분 보고를 내고 0이 아닌 코드로 끝낸다. 하네스는
  마감 + `_REPORT_GRACE_S` 뒤에 명령을 죽이고 인자 없이 한 번 부른다.
- **stdout 마지막 비지 않은 줄이 JSON 객체 보고다.** 실패해도 낼 수 있으면 낸다. 중첩은
  `server.<key>.<field>`로 펴서 `model_swap` 이벤트의 `report.*`에 싣는다(목록은 JSON 문자열).
  JSON이 아니거나 못 읽으면 보고가 없는 것으로 읽는다. 그 앞 줄은 사람용 로그로 자유다 — 실패하면
  하네스가 보고의 `error`(한 줄 사유) -> stderr 꼬리 -> stdout 꼬리 순으로 오류 상세에 싣는다(앞에서
  잘리므로 사유가 맨 앞이다 — `_failure_detail`). 실패하면 `error`를 꼭 채운다.
  권하는 필드: `ok`·`models`·서버별
  `action`·`util`·`max_num_seqs`·`max_model_len`·`args`·`revision`·`load_s`·`ready_s`·`attempts` ·
  `memavail_before`·`memavail_after`·`vllm_version`·`error`. 서버의 기동 조건은 이 보고로만 기록에
  남는다 — 올린 키의 `server.<key>.*`가 하나도 없으면 교대가 성공해도 러너가 run 경고로 그 키를
  부른다.
- ⚠ **띄운 vLLM의 stdout·stderr는 로그 파일로 돌린다**(기동마다 새 파일). 서버가 명령의 파이프를
  물려받으면 `subprocess.run`이 EOF를 못 받아 **서버가 죽을 때까지 안 돌아온다.**

## 명령이 0으로 끝났다고 믿지 않는다

끝나면 올린 키마다 `/v1/models`를 읽어 그 이름으로 떠 있는지와 `max_model_len`이 registry
`context_limit`과 **같은지** 본다. 2026-09-25 ATOM에서 `context_limit` 40,960인 Scout를 16,384로
띄운 채 돌렸고 하네스는 아무 말도 안 했다 — 프롬프트 12건이 400으로 거절됐는데, 400은 러너가
**그 후보만 버리는** 크기 거절이라 run은 「완주」로 끝났다(`run_20260925_0005`·`0006`, #331).
"""

import json
import ntpath
import os
import shutil
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

    # 마지막으로 띄우려던 실행 파일 — 푼 경로면 그것이다(실행 실패 메시지가 보여 준다).
    launched = command[0]

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        # 실행 파일은 **부를 때** 푼다(#477) — 만들 때 풀어 던지면 교대 안 하는 run(`--dry-run`·
        # 낮은 `--stop-after`)까지 `build_deps`에서 죽는다. 여기서 난 `ModelSwapError`는 `swap_to`를
        # 그대로 지나 러너가 읽는다.
        nonlocal launched
        argv = [*_resolved((argv[0],)), *argv[1:]]
        launched = argv[0]
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
        # 새면 사유가 **「교대 실패」가 아니라 「예상 밖」으로** 적히고, 그러면 run을 보는 사람이
        # 장비 파일·교대 명령 로그 대신 엉뚱한 곳을 뒤진다. 그래서 이 경계에서 감싼다.
        # `BaseException`(인터럽트)은 감싸지 않는다 — 사람이 멈춘 것이다.
        # ⏵ 2026-10-02 정정: 전 판은 *"run을 매니페스트 없이 끝낸다(러너의 정지 갈래가 못 잡는다)"*
        # 라 적었다. 러너에 최후 방어선이 생겨 **매니페스트는 남는다** — 틀린 사유가 남는 것이
        # 지금의 대가다. 감싸는 이유는 그대로다.
        label = " ".join(keys) or "(전부 내림)"
        try:
            return swap_once(keys, label)
        except ModelSwapError:
            # 그대로 올린다 — 명령을 띄우기 전의 거부(`_resolved`: `bash`가 WSL 런처로 풀림)도
            # `run`에서 나 여기를 지난다. 감싸면 사유가 「예상 밖」으로 적히고 `__cause__`가 달린다.
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
                detail=_failure_detail(partial := _report(exc.stdout), exc.stdout, exc.stderr),
                report=partial,
            ) from exc
        except OSError as exc:
            raise ModelSwapError(f"교대 명령을 실행하지 못했다({launched}): {exc}") from exc
        report = _report(done.stdout)
        if done.returncode != 0:
            reason = _reason(report)
            raise ModelSwapError(
                f"모델 교대가 실패했다(exit {done.returncode}): {label}"
                + (f" — {reason}" if reason else ""),
                detail=_failure_detail(report, done.stdout, done.stderr),
                report=report,
            )
        for key in keys:
            _verify(key, models[key], probe, report)
        return report

    return swap_to


def _resolved(command: tuple[str, ...]) -> tuple[str, ...]:
    """`command[0]`을 PATH로 먼저 풀어 절대경로로 바꾼다(#477). 못 풀거나 경로가 든 이름은 그대로.

    Windows `CreateProcess`는 PATH보다 System32를 먼저 뒤져 `bash`가 그 아래의 `bash.exe`(WSL
    런처)로 풀린다 -> 교대 스크립트가 WSL 안에서 돌고 Windows 환경변수를 하나도 못 받는다
    (`SWAP_SSH_HOST`이 비었다 · 2026-10-03 `run_20261003_0001`). `shutil.which`는 PATH를
    **앞에서부터** 본다 — Git의 `usr/bin`이 System32보다 **앞에** 있는 PATH(Git Bash로 띄운
    셸)에서만 Git Bash를 고르고, `usr/bin`이 그보다 앞에 없는 PATH(레지스트리 PATH —
    PowerShell·cmd)에서는 `which`도 같은 런처를 고른다(2026-10-05 이 PC에서 쟀다: Git `usr/bin`이
    앞에 있는 PATH → Git Bash · 레지스트리 PATH → System32 런처). 그래서 **`which`가 푼 `bash`가
    그 런처면 띄우지 않고 `ModelSwapError`를 낸다**(`_is_wsl_launcher`) — `SWAP_SSH_HOST`이 비었다
    같은 엉뚱한 사유로 실패하는 대신 원인(`bash`가 WSL 런처로 풀림)을 바로 말한다.

    **교대를 시도할 때 난다**(`swap_runner`의 `run`이 부른다) — 만들 때가 아니다. 교대 안 하는
    run(`--dry-run`·낮은 `--stop-after`)도 `build_deps`로 만들기는 하므로 만들 때 던지면 그 조립에서
    죽는다. 풀려면 PATH 앞에 `usr/bin`을 두거나 `command[0]`에 경로를 직접 적는다(경로가 든 이름은
    `which`를 안 거치므로 막지 않는다). Git Bash로 하네스를 띄우면 `bash`는 풀리지만 그 셸의
    `export`가 `SWAP_REMOTE_ROOT`를 Windows 경로로 바꿔 preflight가 막는다(이슈 #538 — 동작하는
    조합은 `docs/portability.md` 「Windows에서 원격 교대 run 절차」). 단 장비 파일은 추적 파일이라
    고치면 `repo_dirty`로 그 run은 비교표에서 빠진다 — 실험 run에서는 앞의 것(PATH)을 쓴다.

    ⚠ 막는 것은 System32의 `bash.exe`뿐이다. `WindowsApps`의 `bash.exe` 별칭은 안 막는다 — 이
    PC에서는 그것도 WSL 앱의 `wsl.exe`를 가리켰지만(별칭의 reparse 데이터를 읽었다, 실행은 안
    했다) 다른 장비·사용자가 둔 별칭은 모른다. ⚠ `which`가 못 풀면(`None` — PATH에서 `bash`를 못
    찾음) 못 막는다 — 맨 이름이 그대로 가고 Windows `CreateProcess`는 PATH에 System32가 없어도
    System32를 먼저 뒤져 런처를 고를 수 있다(#477의 원인. 무해한 `whoami`로 쟀다 — 2026-10-05:
    PATH를 없는 디렉터리로 하면 `which`는 `None`인데 `subprocess.run`은 exit 0). 이름만 보고는
    막지 않는다(Git Bash·WSL 안에서는 `bash`가 정상이다). ⚠ `doctor --device`는 이 판정을 안
    한다(장비 파일 검사만 한다) — doctor가 OK여도 교대를 시도하면 막힐 수 있다. POSIX에서는
    `execvp`가 PATH를 같은 순서로 뒤지므로 고르는 실행 파일이 같다[추론 — 재지 않았다].
    """
    head, *rest = command
    if not head or os.sep in head or (os.altsep and os.altsep in head):
        return command
    found = shutil.which(head)
    if not found:
        return command
    if _is_wsl_launcher(found):
        raise ModelSwapError(
            f"교대 명령의 bash가 WSL 런처로 풀렸다({found}) — WSL은 Windows 환경변수를 못 받아 "
            "교대가 빈 환경으로 돈다. "
            r"`Git\usr\bin`을 PATH 앞에 두거나(PowerShell `$env:PATH`) "
            "장비 파일 `command[0]`에 bash 절대경로를 쓴다 "
            "(추적 파일을 고치면 `repo_dirty`로 그 run은 비교표에서 빠진다 — "
            "실험 run에서는 앞의 것을 쓴다). Git Bash로 띄우면 `SWAP_REMOTE_ROOT`가 "
            "Windows 경로로 바뀐다 — docs/portability.md 「Windows에서 원격 교대 run 절차」"
        )
    return (found, *rest)


def _is_wsl_launcher(path: str) -> bool:
    """`path`가 `%SystemRoot%`의 System32 아래 `bash.exe`(WSL 런처)인가. Windows가 아니면 거짓.

    `ntpath.normcase`로 접어 비교한다(대소문자·슬래시) — 경로 규칙이 Windows 것이라 `os.path`
    (리눅스에선 대소문자를 구별한다)를 안 쓴다. `SystemRoot`가 없으면 `C:/Windows`다. 못 알아보고
    **막지 않는** 꼴: PATH 항목에 `..`나 겹친 구분자가 든 경로 · `Sysnative`(32비트 파이썬이 PATH에
    그 이름을 둔 경우)·`SysWOW64` 아래 경로 · 확장 경로 접두(물음표를 낀 `//?/` 꼴). 직접 프로브에서
    이 꼴들은 거짓이었다(2026-10-05) — PATH에 그런 항목이 드물어 영향은 미확인이다.
    """
    if sys.platform != "win32":
        return False
    launcher = ntpath.join(os.environ.get("SystemRoot") or "C:/Windows", "System32", "bash.exe")
    return ntpath.normcase(path) == ntpath.normcase(launcher)


def _reason(report: SwapReport) -> str:
    """보고의 `error`(명령이 고른 한 줄 사유 — `swap.sh`의 `fail`). 없거나 빈 값이면 빈 문자열."""
    value = report.get("error")
    return value.strip() if isinstance(value, str) else ""


def _failure_detail(
    report: SwapReport, stdout: str | bytes | None, stderr: str | bytes | None
) -> str:
    """사유 -> stderr -> stdout 순서의 실패 상세(#476).

    상세는 실패 이벤트에 실을 때 **앞에서부터** `MAX_ERROR_DETAIL`(512)자로 잘린다. 예전 순서
    (stdout -> stderr)는 stdout 마지막 줄의 긴 JSON 보고(서버 기동 인자)가 앞을 다 먹어 사유가
    잘렸다 — 원격 교대에서는 그 사유가 다른 기계의 `swap.log`에만 남아 운영자가 `memavail_before`를
    보고 메모리 부족으로 오독했다(`run_20261003_0001`). 사유는 보고의 `error` 필드에서 읽고(문자열
    탐색이 아니다), 명령이 보고를 못 냈으면 stderr가 맨 앞이다 — `swap-remote.sh`의 자체 실패와
    원격 `swap.sh`의 `ABORT:` 로그 줄이 stderr로 온다.
    """
    reason = _reason(report)
    parts = [f"사유: {reason}"] if reason else []
    err, out = _text(stderr).strip(), _text(stdout).strip()
    if err:
        parts.append(f"stderr: {err[-_TAIL_CHARS:]}")
    if out:
        parts.append(f"stdout: {out[-_TAIL_CHARS:]}")
    return " | ".join(parts)


def _teardown(
    run: Callable[[list[str]], subprocess.CompletedProcess[str]], command: tuple[str, ...]
) -> str:
    """내림 명령을 한 번 부르고 결과를 한 마디로. 원래 실패를 가리지 않도록 예외를 안 올린다.

    ⚠ **원 호출과 같은 상한**(마감 + `_REPORT_GRACE_S`)이다. 교대 콜러블 한 번은 최악 그 두 배를
    기다리고, 그 실패로 run이 멈추면 러너의 run 끝 내림(키 없이 한 번 더)이 또 두 배를 쓴다 —
    명령이 부를 때마다 마감을 넘기면 run은 명령을 네 번 부르고(교대 · 그 내림 · run 끝 내림 · 그
    내림) 최악 **네 배**를 기다린다. 계약대로 마감에 스스로 끝내는 명령이면 이 함수까지 오지 않아
    교대와 run 끝 내림 두 번이다. 내림 전용으로 짧게 자르지 않는 것은 그 값을 잰 적이 없어서다 —
    지어낸 상한으로 내림을 도중에 죽이면 서버가 남고, 그쪽이 기다리는 것보다 나쁘다.
    """
    try:
        return f"exit {run(list(command)).returncode}"
    except (subprocess.TimeoutExpired, OSError, ModelSwapError) as exc:  # 뒤는 `_resolved`의 거부
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
