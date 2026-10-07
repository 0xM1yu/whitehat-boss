"""`preflight` — 실모델·실도구 run의 선결조건을 **접속해서** 한 번에 점검한다 (이슈 #534).

`doctor`는 엔드포인트에 접속하지 않는 것이 계약이라(`src/cli.py` 모듈 docstring) 이 점검은
거기에 못 둔다 — 여기는 **일부러** ssh·TCP·`docker run`을 쓴다. 선결조건 하나가 비면 run이
0~3단계(실 Docker 도구)를 태운 뒤 4단계 진입에서야 멈추고(대여 비용이 버려진다) 종료 코드는
0이다. 그래서 run 전에 빠진 것을 **전부** 모아 보인다: 한 항목이 실패해도 나머지를 다 돌고
ERROR가 하나라도 있으면 종료 코드 2다.

외부 접촉은 전부 `Probes`로 주입한다(기본값이 실제 구현, 테스트는 가짜). subprocess는 argv
리스트·`shell=False`·timeout이다. `CYBERGYM_API_KEY`는 존재·길이·문자 종류만 말하고 값은
안 찍는다. `cli`·`model_swap`·`container_tools`의 비공개 이름을 일부러 가져다 쓴다 — 같은
판정을 복제하면 갈린다.

⚠ **OK가 보증하지 않는 것.** ① 터널 포트 OK는 TCP 연결뿐이다 — ssh 포워드는 원격 서버가
없어도 로컬 포트를 먼저 연다. ② 원격 커밋이 같아도 로컬 미커밋 변경은 못 가른다. ③ 채점기
TCP는 그 서버가 CyberGym인지·키가 맞는지 모른다. ④ 카나리 `boot_id`가 같다는 것은 재기동이
없었다는 뜻이지 지금 `vm.mmap_rnd_bits`를 재는 것이 아니다. ⑤ `run`의 거부를 전부 되풀이하지
않는다(`--mask-map`·체크섬·번들 주소 대조 등) — 통과해도 run이 거부할 수 있다. ⑥ 이 PC에서
실제 ssh·docker·채점기로 끝까지 돌려 본 적은 없다(테스트는 가짜뿐) — 미실측.
"""

import argparse
import os
import re
import shlex
import socket
import subprocess
import urllib.parse
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from src.adapters.canary_report import (
    CanaryReport,
    CanaryReportError,
    canary_refusal,
    read_canary_report,
)
from src.adapters.container_tools import _INDEXER, DEFAULT_IMAGE
from src.adapters.cybergym_oracle import unsupported_task_id
from src.adapters.model_swap import _resolved
from src.cli import (
    _CANARY_COMMAND,
    _ORACLE_KEY_ENV,
    _SWAP_NEEDS_DEVICE,
    REPO_ROOT,
    Check,
    Severity,
    _check_serving,
    _key_has_bad_chars,
    _oracle_server_problem,
    load_profile,
)
from src.config import CONFIG_DIR, ConfigError, HarnessConfig, ServingConfig, load_serving
from src.interfaces.model_client import ModelSwapError
from src.orchestrator.task_loader import (
    REPO_ARCHIVE_NAME,
    SUBMIT_SCRIPT_NAME,
    UnexpectedTaskFilesError,
    check_task_dir,
    read_submit_metadata,
)

# `run`·`bench`의 거부 코드와 같다. `doctor`의 ERROR(1)와 다른 값이다 — 파이썬이 잡히지 않은
# 예외로 죽을 때도 1이라, 「어긋남을 찾았다」와 「점검이 죽었다」를 자동화가 가를 수 있다.
EXIT_ERRORS = 2

# 장비 파일의 `command`가 이 래퍼를 부를 때만 환경변수·터널·원격 커밋이 걸린다.
REMOTE_WRAPPER = "tools/serve/swap-remote.sh"
# 그 스크립트의 `need` 호출이 요구하는 변수 — 사람이 세우는 셋과 하네스가 run마다 주는 하나
# (`model_swap.swap_runner`가 env에 넣는다). 스크립트의 `need` 줄과 같은지 테스트가 잠근다.
SWAP_ENV_OPERATOR = ("SWAP_SSH_HOST", "SWAP_REMOTE_ROOT", "SWAP_REMOTE_PYTHON")
SWAP_ENV_HARNESS = ("HARNESS_RUN_ID",)

INDEXER_SOURCE = Path("tools/harness-tools/indexer.py")
_INDEX_VERSION = re.compile(r'^INDEX_VERSION\s*=\s*"([^"]+)"', re.M)
_BOOT_ID_FILE = "/proc/sys/kernel/random/boot_id"
_BOOT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MSYS_DRIVE = re.compile(r"[A-Za-z]:[\\/]")

# 정한 값이다(실측 아님) — 응답 없는 대상에 매달리지 않게 하는 상한. docker는 카나리 도구의
# 호스트 읽기(`_HOST_TIMEOUT_S`)와 같다.
_TCP_TIMEOUT_S = 3.0
_GIT_TIMEOUT_S = 10.0
_SSH_TIMEOUT_S = 30.0
_SSH_CONNECT_TIMEOUT_S = 10
_DOCKER_TIMEOUT_S = 120.0

# --- 주입하는 바깥 세계 -------------------------------------------------------------------

Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]
TcpProbe = Callable[[str, int], str | None]  # 닿으면 None, 아니면 사유
Resolver = Callable[[tuple[str, ...]], tuple[str, ...]]


def _run(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


def _tcp(host: str, port: int) -> str | None:
    try:
        with socket.create_connection((host, port), timeout=_TCP_TIMEOUT_S):
            return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


@dataclass(frozen=True)
class Probes:
    """점검이 바깥 세계에 닿는 통로. 기본값이 실제 구현이고 테스트가 가짜를 넣는다."""

    run: Runner = _run
    tcp: TcpProbe = _tcp
    resolve: Resolver = _resolved  # 교대 때 `model_swap`이 쓰는 bash 해소 그대로다
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)


Done = subprocess.CompletedProcess[str] | None


def _exec(probes: Probes, argv: Sequence[str], timeout: float) -> tuple[Done, str]:
    """`(결과, "")` 또는 `(None, 사유)` — 못 띄웠거나 시간이 넘은 것도 사유로 돌려 이어 돈다."""
    try:
        return probes.run(argv, timeout), ""
    except subprocess.TimeoutExpired:
        return None, f"{timeout:g}초 안에 안 끝났다"
    except OSError as exc:
        return None, f"{argv[0]} 실행 실패: {exc}"


def _failure(done: Done, why: str) -> str:
    """못 읽은 사유 한 줄 — 못 띄웠으면 그 사유, 아니면 exit 코드와 stderr 끝 두 줄.

    끝을 보는 것은 ssh의 경고(`Permanently added …`)가 앞에 오고 진짜 오류가 뒤에 오기 때문이다.
    """
    if done is None:
        return why
    tail = [line.strip() for line in done.stderr.splitlines() if line.strip()][-2:]
    return (
        f"exit {done.returncode}: {' / '.join(tail)[:200]}" if tail else f"exit {done.returncode}"
    )


def _skipped(label: str, flag: str, note: str = "") -> Check:
    # 인자가 없어 안 본 것은 OK가 아니다 — WARN이라 종료 코드는 안 바꾸지만 보고에 남는다.
    return Check(Severity.WARN, f"{label} 건너뜀 — {flag}가 없다{note}")


def _guard(label: str, fn: Callable[[], Check | list[Check]]) -> list[Check]:
    """점검 하나가 예외로 죽어도 나머지가 돈다 — 그 죽음은 삼키지 않고 ERROR로 남긴다."""
    try:
        out = fn()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        message = f"{label} 점검이 예외로 끝났다({detail}) — preflight의 결함이다"
        return [Check(Severity.ERROR, message)]
    return out if isinstance(out, list) else [out]


# --- 1~4. 원격 교대 -----------------------------------------------------------------------


def _clean_absolute(root: str) -> bool:
    """`swap-remote.sh`의 `case … in /*[!/])` — 절대경로이고 `/`로 끝나지 않는다."""
    return root.startswith("/") and len(root) > 1 and not root.endswith("/")


def _check_swap_env(env: Mapping[str, str]) -> list[Check]:
    label = "교대 환경변수:"
    missing = [name for name in SWAP_ENV_OPERATOR if not env.get(name)]
    checks = []
    if missing:
        checks.append(
            Check(
                Severity.ERROR,
                f"{label} {', '.join(missing)}이(가) 비었다 — swap-remote.sh가 ssh 전에 exit 2로 "
                "멈추고 하네스는 그것을 교대를 시도할 때(0~3단계 뒤) 받는다",
            )
        )
    root = env.get("SWAP_REMOTE_ROOT", "")
    if root and not _clean_absolute(root):
        hint = ""
        if _MSYS_DRIVE.match(root):
            hint = (
                " — Git Bash의 export가 `/`로 시작하는 값을 Windows 경로로 바꾼 것이다: "
                "PowerShell `$env:`이나 파이썬 런처로 세운다"
            )
        checks.append(
            Check(
                Severity.ERROR,
                f"{label} SWAP_REMOTE_ROOT가 절대경로가 아니거나 /로 끝난다({root}) — "
                f"/root/harness처럼 적는다{hint}",
            )
        )
    if not checks:
        checks.append(
            Check(
                Severity.OK,
                f"{label} {', '.join(SWAP_ENV_OPERATOR)} 설정됨 — "
                f"{', '.join(SWAP_ENV_HARNESS)}는 하네스가 run마다 준다(점검 대상이 아니다)",
            )
        )
    return checks


def _check_bash(command: tuple[str, ...], probes: Probes) -> Check:
    label, head = "bash 해소:", command[0]
    try:
        resolved = probes.resolve((head,))[0]
    except ModelSwapError as exc:  # System32의 WSL 런처 — 문구가 고치는 법까지 든다
        return Check(Severity.ERROR, f"{label} {exc}")
    if resolved != head:
        return Check(Severity.OK, f"{label} {head} → {resolved}")
    if os.sep in head or (os.altsep and os.altsep in head):
        return Check(Severity.OK, f"{label} 교대 명령이 경로로 적혀 있다({head}) — PATH로 안 푼다")
    return Check(
        Severity.WARN,
        f"{label} {head}를 PATH에서 못 찾았다 — 이름만으로는 못 막는다: Windows "
        "`CreateProcess`는 PATH에 System32가 없어도 먼저 뒤져 WSL 런처를 고를 수 있다",
    )


def tunnel_endpoints(config: HarnessConfig, serving: ServingConfig) -> tuple[tuple[str, int], ...]:
    """장비 파일의 서버가 registry에서 받는 (호스트, 포트) — 터널이 이어 줄 곳이다.

    `tools/serve/vast-tunnel.sh`의 `PORTS`가 같은 집합이어야 한다(테스트가 잠근다).
    """
    found: set[tuple[str, int]] = set()
    for key in serving.servers:
        parts = urllib.parse.urlsplit(config.models[key].endpoint)
        if parts.hostname and parts.port:
            found.add((parts.hostname, parts.port))
    return tuple(sorted(found, key=lambda endpoint: (endpoint[1], endpoint[0])))


def _check_tunnel(config: HarnessConfig, serving: ServingConfig, probes: Probes) -> Check:
    label = "터널 포트:"
    endpoints = tunnel_endpoints(config, serving)
    closed = {f"{h}:{p}": why for h, p in endpoints if (why := probes.tcp(h, p))}
    if closed:
        shown = ", ".join(f"{addr}({why})" for addr, why in closed.items())
        return Check(
            Severity.ERROR,
            f"{label} 닫혀 있다: {shown} — 별도 터미널에서 tools/serve/vast-tunnel.sh를 띄워 "
            "둔다(교대 뒤 준비 2차 확인과 모델 호출이 이 포트를 지난다)",
        )
    ports = "·".join(str(p) for _, p in endpoints)
    return Check(
        Severity.OK,
        f"{label} 127.0.0.1:{ports}에 TCP로 닿는다 — 터널이 떠 있다는 뜻일 뿐 원격 서버가 "
        "떴는지는 교대 뒤 `/v1/models`가 본다",
    )


def _check_remote_commit(env: Mapping[str, str], repo_root: Path, probes: Probes) -> Check:
    label = "원격 커밋:"
    host, root = env.get("SWAP_SSH_HOST", ""), env.get("SWAP_REMOTE_ROOT", "")
    if not host or not _clean_absolute(root):
        return Check(
            Severity.WARN, f"{label} 건너뜀 — ssh로 갈 곳을 모른다(위 환경변수 점검이 실패했다)"
        )
    local, why = _exec(probes, ["git", "-C", str(repo_root), "rev-parse", "HEAD"], _GIT_TIMEOUT_S)
    if local is None or local.returncode != 0 or not local.stdout.strip():
        return Check(Severity.ERROR, f"{label} 로컬 커밋을 못 읽었다({_failure(local, why)})")
    want = local.stdout.strip()
    # `swap-remote.sh`와 같은 옵션이다 — 암호를 안 묻고(BatchMode) 호스트는 `--` 뒤라 옵션이 아니다.
    options = ["-n", "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={_SSH_CONNECT_TIMEOUT_S}"]
    if env.get("SWAP_SSH_PORT"):
        options += ["-p", env["SWAP_SSH_PORT"]]
    remote = f"cd {shlex.quote(root)} && cat .harness-commit"
    done, why = _exec(probes, ["ssh", *options, "--", host, remote], _SSH_TIMEOUT_S)
    if done is None or done.returncode != 0:
        return Check(
            Severity.ERROR,
            f"{label} ssh로 원격 .harness-commit을 못 읽었다({_failure(done, why)}) — 호스트 접속·"
            "SWAP_REMOTE_ROOT·원격 사본(tools/serve/vast-bootstrap.sh)을 본다",
        )
    got = done.stdout.strip()
    if got != want:
        return Check(
            Severity.ERROR,
            f"{label} 원격 커밋({got or '없음'})이 로컬({want})과 다르다 — "
            "tools/serve/vast-bootstrap.sh를 다시 돌린다",
        )
    return Check(
        Severity.OK, f"{label} 원격 사본이 로컬 HEAD({want[:12]})와 같다 — 미커밋 변경은 못 가른다"
    )


def _uses_remote_wrapper(command: tuple[str, ...]) -> bool:
    return any(arg.replace("\\", "/") == REMOTE_WRAPPER for arg in command)


def _swap_checks(
    config: HarnessConfig, device: str | None, probes: Probes, config_dir: Path, repo_root: Path
) -> list[Check]:
    label = "교대 점검:"
    if config.swap is None:
        return [Check(Severity.OK, f"{label} 해당 없음 — 프로필이 교대를 선언하지 않았다")]
    if device is None:
        return [Check(Severity.ERROR, f"{label} {_SWAP_NEEDS_DEVICE}")]
    found = _check_serving(config, device, config_dir)
    if any(c.severity is Severity.ERROR for c in found):
        return [*found, Check(Severity.WARN, f"{label} 건너뜀 — 장비 파일을 못 믿는다(위 ERROR)")]
    serving, env = load_serving(device, config_dir=config_dir), probes.environ
    bash = _guard("bash 해소:", lambda: _check_bash(serving.command, probes))
    if not _uses_remote_wrapper(serving.command):
        local_only = Check(
            Severity.OK,
            "교대 환경변수·터널·원격 커밋: 해당 없음 — 이 장비의 교대 명령은 swap-remote.sh가 "
            "아니다(서버는 교대 뒤에 뜬다)",
        )
        return [*found, local_only, *bash]
    return [
        *found,
        *_guard("교대 환경변수:", lambda: _check_swap_env(env)),
        *bash,
        *_guard("터널 포트:", lambda: _check_tunnel(config, serving, probes)),
        *_guard("원격 커밋:", lambda: _check_remote_commit(env, repo_root, probes)),
    ]


# --- 5~8. 채점기 --------------------------------------------------------------------------


def _check_oracle_server(server: str | None, probes: Probes) -> Check:
    label = "채점기 서버:"
    if server is None:
        return _skipped(label, "--oracle-server")
    problem = _oracle_server_problem(server)
    if problem is not None:
        return Check(Severity.ERROR, f"{label} --oracle-server {server!r}: {problem}")
    parts = urllib.parse.urlsplit(server)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    why = probes.tcp(parts.hostname or "", port)
    if why:
        return Check(Severity.ERROR, f"{label} {server}에 TCP로 못 닿는다({why}) — 서버를 띄운다")
    return Check(
        Severity.OK, f"{label} {server}에 TCP로 닿는다 — CyberGym 채점기인지·키가 맞는지는 모른다"
    )


def _check_api_key(env: Mapping[str, str], oracle_run: bool) -> Check:
    label = f"{_ORACLE_KEY_ENV}:"
    if not oracle_run:
        return Check(
            Severity.WARN,
            f"{label} 건너뜀 — 채점 run이 아니면 필요 없다(--oracle-server·--oracle-canary가 없다)",
        )
    key = env.get(_ORACLE_KEY_ENV, "")
    if not key:
        return Check(
            Severity.ERROR,
            f"{label} 비어 있다 — 채점기의 private 경로(submit-fix·query-poc)에 필요하다",
        )
    if _key_has_bad_chars(key):
        return Check(
            Severity.ERROR,
            f"{label} 공백·비출력 문자가 있다({len(key)}자) — 값은 찍지 않는다. 파일에서 읽었다면 "
            "끝의 개행(CRLF면 CR까지)이 섞인 것이다",
        )
    return Check(Severity.OK, f"{label} 설정됨({len(key)}자) — 값은 찍지 않는다")


def _expected_task(task_dir: Path | None) -> tuple[str | None, str]:
    """카나리 보고서와 견줄 실 과제 id, 못 알면 `(None, 사유)`."""
    if task_dir is None:
        return None, "--task-dir 없음"
    metadata = read_submit_metadata(task_dir)
    if metadata is None:
        return None, "번들에서 과제 id를 못 읽음"
    if unsupported_task_id(metadata.task_id) is not None:
        return None, "번들 id가 마스킹이라 실 id를 모름 — run은 --mask-map이 필요하다"
    return metadata.task_id, ""


def _check_boot_id(report: CanaryReport, probes: Probes) -> Check:
    label = "카나리 boot_id:"
    recorded = report.host_boot_id
    if recorded is None:
        return Check(
            Severity.WARN, f"{label} 확인 못 함 — 보고서에 host_boot_id가 없어 부팅을 못 견준다"
        )
    argv = ["docker", "run", "--rm", "--network=none", "--pull", "never", DEFAULT_IMAGE]
    done, why = _exec(probes, [*argv, "cat", _BOOT_ID_FILE], _DOCKER_TIMEOUT_S)
    current = done.stdout.strip() if done is not None and done.returncode == 0 else ""
    if not _BOOT_ID.fullmatch(current):
        return Check(
            Severity.WARN,
            f"{label} 확인 못 함 — 현재 boot_id를 못 읽었다({_failure(done, why)}): "
            "보고서가 지금 부팅의 것인지 모른다",
        )
    if current != recorded:
        return Check(
            Severity.ERROR,
            f"{label} Docker VM의 boot_id({current[:8]}…)가 보고서의 것({recorded[:8]}…)과 "
            "다르다 — 재기동돼 vm.mmap_rnd_bits가 32로 돌아갔을 수 있다. 카나리를 다시 돌린다: "
            f"python {_CANARY_COMMAND}",
        )
    return Check(Severity.OK, f"{label} 보고서와 같은 부팅이다({current[:8]}…)")


def _check_canary(path: Path | None, task_dir: Path | None, probes: Probes) -> list[Check]:
    label = "카나리:"
    if path is None:
        return [_skipped(label, "--oracle-canary", " (--oracle-submit run은 보고서 없이 거부된다)")]
    try:
        report = read_canary_report(Path(path).resolve())
    except CanaryReportError as exc:
        return [Check(Severity.ERROR, f"{label} {exc}")]
    except (OSError, RuntimeError) as exc:  # 경로를 못 푼다(심볼릭 링크 순환 등)
        return [Check(Severity.ERROR, f"{label} 경로를 풀 수 없다({path}): {exc!r}")]
    task_id, why = _expected_task(task_dir)
    problem = canary_refusal(report, task_id=task_id or report.task)
    if problem is not None:
        return [Check(Severity.ERROR, f"{label} {problem}")]
    note = "" if task_id else f" · 과제 대조 안 함({why})"
    line = Check(
        Severity.OK,
        f"{label} 과제 {report.task} · 판정 {report.verdict} · vm.mmap_rnd_bits "
        f"{report.mmap_rnd_bits} · {report.repeat}회 · {report.measured_at} 측정{note}",
    )
    return [line, *_guard("카나리 boot_id:", lambda: _check_boot_id(report, probes))]


def _check_bundle(task_dir: Path | None) -> list[Check]:
    label = "과제 번들:"
    if task_dir is None:
        return [_skipped(label, "--task-dir")]
    problems: list[str] = []
    try:
        check_task_dir(task_dir)  # 정답 자료가 섞였는지 — `run`이 쓰는 허용 목록 가드 그대로다
    except UnexpectedTaskFilesError as exc:
        problems.append(str(exc))
    except OSError as exc:
        detail = f"{type(exc).__name__}: {exc}"
        return [Check(Severity.ERROR, f"{label} 과제 디렉터리를 읽을 수 없다: {detail}")]
    required = (REPO_ARCHIVE_NAME, SUBMIT_SCRIPT_NAME)
    missing = [name for name in required if not (Path(task_dir) / name).is_file()]
    if missing:
        problems.append(f"필수 파일이 없다: {', '.join(missing)}")
    metadata = None
    if SUBMIT_SCRIPT_NAME not in missing:
        metadata = read_submit_metadata(task_dir)
        if metadata is None:
            problems.append(
                f"{SUBMIT_SCRIPT_NAME}에서 채점기 신원을 못 읽었다 — 서버 주소와 metadata"
                "(task_id·agent_id·checksum)가 필요하다"
            )
    if problems:
        return [Check(Severity.ERROR, f"{label} {problem}") for problem in problems]
    task = metadata.task_id if metadata else "?"
    return [
        Check(
            Severity.OK,
            f"{label} 허용 파일만 있고 {', '.join(required)}가 있다(과제 id {task})",
        )
    ]


# --- 9. 도구 이미지의 인덱서 버전 -----------------------------------------------------------


def _local_index_version(repo_root: Path) -> str | None:
    try:
        text = (repo_root / INDEXER_SOURCE).read_text(encoding="utf-8")
    except OSError:
        return None
    found = _INDEX_VERSION.search(text)
    return found.group(1) if found else None


def _check_tool_image(config: HarnessConfig, repo_root: Path, probes: Probes) -> Check:
    label, image = "도구 이미지:", DEFAULT_IMAGE
    runtime = config.profile.adapters.tool_runner
    if runtime == "fake":
        return Check(Severity.OK, f"{label} 해당 없음 — tool_runner가 Fake라 {image}를 안 쓴다")
    if runtime == "singularity":
        return Check(Severity.WARN, f"{label} 건너뜀 — singularity의 sif는 안 본다")
    ours = _local_index_version(repo_root)
    if ours is None:
        return Check(Severity.ERROR, f"{label} {INDEXER_SOURCE}에서 INDEX_VERSION을 못 읽었다")
    argv = [runtime, "run", "--rm", "--network=none", "--pull", "never", image]
    done, why = _exec(probes, [*argv, "grep", "^INDEX_VERSION", _INDEXER], _DOCKER_TIMEOUT_S)
    if done is None or done.returncode != 0:
        return Check(Severity.ERROR, f"{label} {image}의 인덱서를 못 읽었다({_failure(done, why)})")
    found = _INDEX_VERSION.search(done.stdout)
    if found is None:
        return Check(Severity.ERROR, f"{label} {image}의 {_INDEXER}에서 INDEX_VERSION을 못 읽었다")
    if found.group(1) != ours:
        return Check(
            Severity.ERROR,
            f"{label} {image}의 인덱서는 {found.group(1)}인데 저장소 코드는 {ours}다 — 낡은 "
            "이미지는 새 표(writes·includes 등)를 경고 없이 안 붙인다. 재빌드한다: "
            f"{runtime} build -t {image} tools/harness-tools/",
        )
    return Check(Severity.OK, f"{label} {image}의 인덱서가 저장소 코드와 같다({ours})")


# --- 조립 ---------------------------------------------------------------------------------


def preflight(
    profile: str,
    *,
    device: str | None = None,
    task_dir: Path | None = None,
    oracle_server: str | None = None,
    oracle_canary: Path | None = None,
    probes: Probes | None = None,
    config_dir: Path = CONFIG_DIR,
    repo_root: Path = REPO_ROOT,
) -> list[Check]:
    """항목 순서대로 점검 결과를 모은다. 한 항목의 실패가 뒤 항목을 막지 않는다."""
    probes = Probes() if probes is None else probes
    try:
        config = load_profile(profile, config_dir=config_dir)
    except ConfigError as exc:
        return [Check(Severity.ERROR, f"설정 로드 실패: {exc}")]
    oracle_run = oracle_server is not None or oracle_canary is not None
    runtime = config.profile.adapters.tool_runner
    return [
        Check(
            Severity.OK,
            f"프로필 {profile!r}: 실서빙 역할 {len(config.served_models())}개 · "
            f"교대 {'선언' if config.swap else '없음'} · 도구 런타임 {runtime}",
        ),
        *_guard("교대 점검:", lambda: _swap_checks(config, device, probes, config_dir, repo_root)),
        *_guard("채점기 서버:", lambda: _check_oracle_server(oracle_server, probes)),
        *_guard(f"{_ORACLE_KEY_ENV}:", lambda: _check_api_key(probes.environ, oracle_run)),
        *_guard("카나리:", lambda: _check_canary(oracle_canary, task_dir, probes)),
        *_guard("과제 번들:", lambda: _check_bundle(task_dir)),
        *_guard("도구 이미지:", lambda: _check_tool_image(config, repo_root, probes)),
    ]


def exit_code(checks: Sequence[Check]) -> int:
    return EXIT_ERRORS if any(c.severity is Severity.ERROR for c in checks) else 0


def summary(checks: Sequence[Check]) -> Check:
    """끝 줄. 건너뛰었거나 확인 못 한 것(WARN)이 있으면 OK로 끝내지 않는다."""
    n = Counter(c.severity for c in checks)
    counts = f"ERROR {n[Severity.ERROR]}건 · WARN {n[Severity.WARN]}건 · OK {n[Severity.OK]}건"
    if n[Severity.ERROR]:
        return Check(Severity.ERROR, f"preflight: {counts} — 위 ERROR를 고치고 다시 돌린다")
    if n[Severity.WARN]:
        return Check(
            Severity.WARN,
            f"preflight: {counts} — WARN은 건너뛰었거나 못 확인한 것이다(OK가 아니다)",
        )
    return Check(Severity.OK, f"preflight: {counts}")


def run_preflight(args: argparse.Namespace) -> int:
    checks = preflight(
        args.profile,
        device=args.device,
        task_dir=args.task_dir,
        oracle_server=args.oracle_server,
        oracle_canary=args.oracle_canary,
    )
    for check in [*checks, summary(checks)]:  # `doctor`와 같은 모양으로 stdout에 찍는다
        print(check.render())
    return exit_code(checks)
