"""교대 명령(`tools/serve/swap.sh`)·감시자(`tools/serve/guard.sh`)와 config의 대조.

스크립트는 장비(ATOM·vast)에서만 실제 vLLM을 띄운다 — 에이전트와 CI는 장비에 접속하지 않는다.
⏵ 2026-10-08 — ATOM은 2026-09-29 사용 종료(#340 · D81). 지금 띄우는 장비는 vast다.
여기서 보는 것은 넷이다.

1. 문법(`bash -n`).
2. 교대 마감(`settle_timeout_s`)이 두 장비 파일의 모든 교대를 덮는가.
   ⏵ 2026-10-08 — 지금은 세 장비 파일이다(아래 `DEVICES` — `vastai-remote`가 #417로 더해졌다).
3. 계약의 뼈대가 스크립트에 있는가 — ⚠ **철자 검사다**(CLAUDE.md §2.5). `flock`이라는 글자가 있다는
   것이지 잠금이 교대 전체를 감싼다는 것이 아니다.
4. 성질은 **Linux에서 스크립트를 실제로 돌려** 본다 — 가짜 `nvidia-smi`와 가짜 `vllm`(HTTP 서버)을
   PATH에 둔다. ⚠ Windows에서는 건너뛴다(setsid·flock·/proc). 가짜 서버라 vLLM의 기동 시간·메모리
   거동은 재지 않는다 — 그것은 장비 검증(PR 본문의 GPU 검증 블록)의 몫이다.
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest

from src.bench_metrics import _TIMEOUT_S as _SERVED_WINDOW_S
from src.config import load_config, load_serving

ROOT = Path(__file__).resolve().parent.parent
# `bash`에 **상대 경로**로 넘긴다 — Windows에서 PATH의 `bash`가 WSL 런처여도 cwd로 풀린다.
SWAP = Path("tools/serve/swap.sh")
GUARD = Path("tools/serve/guard.sh")
DEVICES = ("atom", "vastai", "vastai-remote")

# 교대 마감 식에 들어가는 스크립트 상수 — `NAME=숫자` 한 줄.
_CONST = re.compile(
    r"^(ATTEMPTS|TERM_WAIT_S|KILL_WAIT_S|MEM_WAIT_S|POLL_S|PROBE_MAX_S)=(\d+)$", re.M
)


def _constants() -> dict[str, int]:
    return {name: int(value) for name, value in _CONST.findall((ROOT / SWAP).read_text("utf-8"))}


# --- 1. 문법 ----------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash가 없다")
@pytest.mark.parametrize("script", [SWAP, GUARD], ids=lambda p: p.name)
def test_the_serve_scripts_parse(script: Path) -> None:
    completed = subprocess.run(
        ["bash", "-n", script.as_posix()], cwd=ROOT, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr


# --- 2. 교대 마감 -------------------------------------------------------------------


def test_the_constant_pattern_reads_the_real_script() -> None:
    """검출기 핀 — 양성: `tools/serve/swap.sh`의 상수 여섯. 음성: 기본값을 든 환경 변수 줄은 상수가
    아니다(같은 파일의 `MIN_AVAIL_GIB="${MIN_AVAIL_GIB:-12}"` 꼴 — 장비마다 조정하는 손잡이다)."""
    assert set(_constants()) == {
        "ATTEMPTS",
        "TERM_WAIT_S",
        "KILL_WAIT_S",
        "MEM_WAIT_S",
        "POLL_S",
        "PROBE_MAX_S",
    }
    assert _CONST.search('MIN_AVAIL_GIB="${MIN_AVAIL_GIB:-12}"') is None


# 교대 순서 — 4 Scout → 5 Auditor → 7 Critic → 8 PoV(ADR 0009 「2026-09-29 제안」 절 3 · PoV 배선).
_STAGE_ROLES = (
    ("scout_memory", "scout_boundary", "scout_injection"),
    ("auditor",),
    ("critic",),
    ("pov",),
)


@pytest.mark.parametrize("device", DEVICES)
def test_the_swap_deadline_covers_every_swap_on_every_device(device: str) -> None:
    """`settle_timeout_s`(교대 한 번의 마감)가 그 장비의 가장 무거운 교대를 덮는다.

    교대 한 번의 상한 = 앞 단계 서버 내림 + 키마다 시도 × 시도 하나의 상한. 시도 하나 = 메모리
    대기(`MEM_WAIT_S` + 마지막 폴링) + 준비 상한 + 준비 루프의 마지막 폴링 + 마지막 채팅 요청의
    `curl -m` 상한(준비 상한을 넘겨서도 그만큼 붙잡는다) + 성공 뒤 `/v1/models` 확인
    (`bench_metrics._TIMEOUT_S`) + 실패 기동 내림(TERM + KILL). 성공한 시도에는 내림이, 실패한
    시도에는 확인이 없어 식이 실제 최악보다 조금 크다. 식 밖: 셸·파이썬 기동 같은 초 단위 비용 ·
    `/v1/models` 확인의 60 s는 소켓 연산마다의 상한이라(urllib) 연결과 읽기가 각각 그만큼 걸리는
    경우.
    마감이 모자라면 느린 기동이 도중에 끊겨 run이 `model_swap_failed`로 선다 — 준비 상한을 올리고
    마감을 안 올리는 실수를 잡는다. 한계: 준비 상한이 실제 기동 시간을 덮는지는 이 검사 밖이다
    (장비 파일 주석의 계산 — 미실측).
    """
    config = load_config("harness_pool")
    serving = load_serving(device)
    const = _constants()
    stop = const["TERM_WAIT_S"] + const["KILL_WAIT_S"]
    per_attempt = (
        const["MEM_WAIT_S"] + 2 * const["POLL_S"] + const["PROBE_MAX_S"] + _SERVED_WINDOW_S + stop
    )
    served = config.served_models()
    assert config.swap is not None

    previous: set[str] = set()
    for roles in _STAGE_ROLES:
        keys = {served[role] for role in roles}
        worst = len(previous) * stop + sum(
            const["ATTEMPTS"] * (serving.servers[key].ready_timeout_s + per_attempt) for key in keys
        )
        assert config.swap.settle_timeout_s >= worst, (device, sorted(keys), worst)
        previous = keys


def test_the_readiness_request_waits_at_most_the_constant_the_deadline_reads() -> None:
    """⚠ 철자 검사다(CLAUDE.md §2.5). 위 마감 식은 `PROBE_MAX_S`를 읽는다 — 준비 확인 요청의
    `curl -m`이 그 상수로 자른 값(`$m`)을 써야 식이 실제 대기와 맞는다. `probe_chat`이 리터럴로
    자르면(예: `m=300`) 식은 120을 읽은 채 초록이다. 그 결합만 문다 — 실제로 몇 초 붙잡는지는 안
    잰다."""
    text = (ROOT / SWAP).read_text("utf-8")
    probe = re.search(r"^probe_chat\(\) \{.*?^\}$", text, re.M | re.S)
    assert probe, "probe_chat 정의를 못 찾았다"
    assert 'if [ "$m" -gt "$PROBE_MAX_S" ]; then m=$PROBE_MAX_S; fi' in probe[0]
    assert 'curl -sf -m "$m" ' in probe[0]


# --- 3. 계약의 뼈대(철자) -------------------------------------------------------------


def test_the_swap_script_carries_the_contract_skeleton() -> None:
    """⚠ 철자 검사다 — 모듈 docstring 3. 성질은 아래 Linux 실행 테스트가 문다."""
    text = (ROOT / SWAP).read_text("utf-8")

    assert "flock -w" in text  # 교대끼리 겹치지 않는다
    assert "trap emit EXIT" in text  # 실패해도 stdout 마지막 줄이 JSON 보고다
    assert "MemAvailable" in text and "memory.total" in text  # 두 메모리 판정
    assert '"$D/logs/$key.$RUN_ID.' in text  # 기동마다 run_id가 붙은 새 로그


# --- 4. Linux에서 실제로 돌린다 --------------------------------------------------------

_LINUX = sys.platform.startswith("linux") and all(
    shutil.which(tool) for tool in ("bash", "setsid", "flock", "curl")
)
needs_linux = pytest.mark.skipif(
    not _LINUX, reason="교대 스크립트는 Linux(setsid·flock·curl·/proc)에서만 돈다"
)
RUN_ID = "run_x"
# vastai 행 `qwen3-14b`의 포트(registry `endpoint`) — 가짜 서버가 여기 뜬다.
# ⏵ 2026-10-14 — `seccoderx-8b`가 로스터에서 내려오며 8000은 `qwen3-14b`가 받았다.
_PORT = 8000

# 가짜 vLLM — 받은 argv를 첫 줄에 찍고(교대 명령이 로그 파일로 돌린다) `/v1/models`·채팅에 답한다.
_FAKE_VLLM = """#!{python}
import json, os, signal, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

if os.environ.get("STUB_IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
HANG = os.path.join(os.environ.get("STUB_HANG_DIR", "/nonexistent"), str(os.getpid()))
if os.environ.get("STUB_KILL_GUARD"):
    try:
        with open(os.path.join(os.environ["SERVE_DIR"], "guard.pid")) as guard_pid:
            os.kill(int(guard_pid.read().split()[0]), signal.SIGKILL)
    except (OSError, ValueError):
        pass
args = sys.argv[1:]
print("fake-vllm", " ".join(args), flush=True)
opt = dict(zip(args, args[1:]))
name, window = opt["--served-model-name"], int(opt["--max-model-len"])


class Handler(BaseHTTPRequestHandler):
    def _reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply({{"data": [{{"id": name, "max_model_len": window}}]}})

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if os.path.exists(HANG):
            self.send_error(500)
            return
        self._reply({{"choices": [{{"message": {{"content": "p"}}}}]}})

    def log_message(self, *_):
        pass


HTTPServer(("127.0.0.1", int(opt["--port"])), Handler).serve_forever()
"""

# 가짜 nvidia-smi — 메모리는 `STUB_GPU_MEM`, compute 프로세스는 `STUB_GPU_PIDS`.
_FAKE_NVIDIA_SMI = """#!/usr/bin/env bash
case "$*" in
  *--query-gpu=*) echo "$STUB_GPU_MEM" ;;
  *--query-compute-apps=*) for p in ${STUB_GPU_PIDS:-}; do echo "$p"; done ;;
esac
"""


class Swap(NamedTuple):
    code: int
    report: dict
    stderr: str


class Rig(NamedTuple):
    dir: Path
    env: dict[str, str]

    @property
    def servers(self) -> Path:
        return self.dir / "servers"

    def pgid(self, key: str) -> int:
        """이 run의 SERVERS 행에서 그 키의 프로세스 그룹."""
        for line in self.servers.read_text().splitlines():
            fields = line.split()
            if fields[0] == key and fields[2] == RUN_ID:
                return int(fields[1])
        raise AssertionError(f"{key} 행이 없다: {self.servers.read_text()!r}")

    def guard(self, **env: str) -> subprocess.Popen:
        """감시자를 띄우고 pid 파일이 그 감시자를 가리킬 때까지 기다린다."""
        pid_file = self.dir / "guard.pid"
        if pid_file.exists() and pid_file.read_text().split()[:1] == [str(os.getpid())]:
            pid_file.unlink()  # fixture의 대역(이 테스트 프로세스) — 진짜 감시자가 아니다
        guard = subprocess.Popen(
            ["bash", GUARD.as_posix()],
            cwd=ROOT,
            env={**self.env, "GUARD_POLL_S": "1", **env},
            start_new_session=True,
        )
        for _ in range(100):
            if pid_file.exists() and pid_file.read_text().split()[:1] == [str(guard.pid)]:
                return guard
            time.sleep(0.05)
        guard.kill()
        raise AssertionError("감시자가 pid 파일을 안 적었다")

    def run(self, *keys: str, **env: str) -> Swap:
        done = subprocess.run(
            ["bash", SWAP.as_posix(), *keys],
            cwd=ROOT,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=120,
        )
        lines = done.stdout.strip().splitlines()
        return Swap(done.returncode, json.loads(lines[-1]) if lines else {}, done.stderr)


def _alive_group(pgid: int) -> bool:
    """그 프로세스 그룹에 좀비가 아닌 프로세스가 있는가(`/proc/<pid>/stat` — 교대 명령과 같다)."""
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if fields[0] != "Z" and int(fields[2]) == pgid:
            return True
    return False


@pytest.fixture
def rig(tmp_path: Path):
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", _PORT)) == 0:
            pytest.skip(f"포트 {_PORT}이 이미 쓰인다")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "vllm").write_text(_FAKE_VLLM.format(python=sys.executable), encoding="utf-8")
    (bin_dir / "nvidia-smi").write_text(_FAKE_NVIDIA_SMI, encoding="utf-8")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    serve = tmp_path / "serve"
    serve.mkdir()
    (tmp_path / "hub").mkdir()
    # 감시자 대역 — 교대 명령은 감시자가 살아 있어야 띄운다. 번호·시작 시각이 맞는 산 프로세스다.
    (serve / "guard.pid").write_text(f"{os.getpid()} {_starttime(os.getpid())}\n", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HARNESS_PYTHON": sys.executable,
        "HARNESS_RUN_ID": RUN_ID,
        "HARNESS_DEVICE": "vastai",
        "SWAP_DEADLINE_S": "60",
        "SERVE_DIR": str(serve),
        "VLLM_BIN": str(bin_dir / "vllm"),
        "HF_HUB_CACHE": str(tmp_path / "hub"),
        "STUB_GPU_MEM": "97887, 1024",
        "STUB_GPU_PIDS": "",
    }
    rig = Rig(serve, env)
    yield rig
    rig.run()  # 남은 이 run 서버를 내린다


@needs_linux
def test_the_swap_script_launches_reports_and_tears_down_only_its_own_run(rig: Rig) -> None:
    """띄우고(보고·로그·소유 기록) 내린다 — 남의 run이 적은 서버는 건드리지 않는다.

    첫 호출은 **이 run 몫이 하나도 없을 때의 내림**이다. 2026-09-28 ATOM 스모크의 교대 훅이
    정확히 그
    자리에서 빈 `pgrep` + `pipefail`로 exit 1을 냈다(docs/experiments/harness/
    2026-09-28-ne-loop-smoke-benign-atom.md 「첫 시도는 결과에서 제외」).
    """
    other = subprocess.Popen(["setsid", "sleep", "300"])  # 다른 run이 띄운 서버 대역
    try:
        rig.servers.write_text(f"gpt-oss-20b {other.pid} run_other\n", encoding="utf-8")

        empty = rig.run()
        assert (empty.code, empty.report.get("ok")) == (0, True), empty.stderr
        assert empty.report["models"] == []

        up = rig.run("qwen3-14b")
        assert up.code == 0, up.stderr
        assert (up.report["ok"], up.report["memory"], up.report["run_id"]) == (True, "vram", RUN_ID)
        assert up.report["models"] == ["qwen3-14b"]
        server = up.report["server"]["qwen3-14b"]
        assert (server["action"], server["attempts"], server["max_model_len"]) == (
            "launch", 1, 131072
        )
        log = Path(server["log"])
        assert re.fullmatch(r"qwen3-14b\.run_x\.\d{8}T\d{6}Z\.log", log.name)
        argv = log.read_text(encoding="utf-8")
        assert "--max-model-len 131072" in argv
        assert "--revision 40c069824f4251a91eefaf281ebe4c544efd3e18" in argv
        ours = [line.split() for line in rig.servers.read_text().splitlines() if RUN_ID in line]
        assert [(key, run) for key, _, run, *_ in ours] == [("qwen3-14b", RUN_ID)]

        down = rig.run()
        assert down.code == 0, down.stderr
        assert down.report["server"]["qwen3-14b"]["action"] == "stop"
        assert not _alive_group(int(ours[0][1]))
        assert other.poll() is None, "남의 run 서버를 죽였다"
        assert f"gpt-oss-20b {other.pid} run_other" in rig.servers.read_text()
    finally:
        other.kill()


@needs_linux
def test_the_swap_script_refuses_to_launch_beside_a_foreign_gpu_process(rig: Rig) -> None:
    """이 run 밖의 GPU 프로세스가 있으면 띄우지 않고 실패로 보고한다 — 죽이지도 않는다."""
    result = rig.run("qwen3-14b", STUB_GPU_PIDS="4194301")

    assert result.code != 0
    assert result.report["ok"] is False and "GPU 프로세스" in result.report["error"]
    assert not list((rig.dir / "logs").glob("qwen3-14b.*"))
    assert RUN_ID not in rig.servers.read_text()


@needs_linux
def test_the_swap_script_judges_unified_memory_by_mem_available(rig: Rig) -> None:
    """`nvidia-smi` 메모리가 `[N/A]`면(GB10 — ATOM) 통합메모리 판정이다. 하한을 크게 줘
    거절을 본다."""
    result = rig.run(
        "qwen3-14b",
        STUB_GPU_MEM="[N/A], [N/A]",
        HARNESS_DEVICE="atom",
        MIN_AVAIL_GIB="100000",
        SWAP_DEADLINE_S="3",
    )

    assert result.code != 0
    assert result.report["memory"] == "unified"
    assert "MemAvailable" in result.report["error"]
    assert not list((rig.dir / "logs").glob("qwen3-14b.*"))


@needs_linux
def test_the_swap_script_gives_up_on_a_held_lock_at_the_deadline(rig: Rig) -> None:
    """다른 교대가 잠금을 쥐고 있으면 마감까지 기다리다 실패로 보고한다 — 겹쳐 띄우지 않는다."""
    import fcntl  # Linux 전용

    with open(rig.dir / "swap.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = rig.run("qwen3-14b", SWAP_DEADLINE_S="2")

    assert result.code != 0
    assert "잠금" in result.report["error"]


def _setsid(*command: str) -> subprocess.Popen:
    """다른 run이 띄운 서버 대역 — 자기 프로세스 그룹의 리더."""
    return subprocess.Popen(["setsid", *command])


def _wait_exit(process: subprocess.Popen, seconds: float) -> bool:
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        return False
    return True


def _port_free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


@needs_linux
def test_one_call_stops_the_previous_key_and_launches_the_next(rig: Rig) -> None:
    """실제 교대 — 한 호출이 앞 키를 내리고 새 키를 띄운다. 보고에 둘 다 있고 앞 서버는 없어진다."""
    if not _port_free(8001):
        pytest.skip("포트 8001이 이미 쓰인다")
    assert rig.run("qwen3-14b").code == 0
    before = rig.pgid("qwen3-14b")

    # ⏵ 2026-10-15 — 같은 키를 다시 부르면 내리지 않고 둔다(keep). 교대는 **다른** 키라야 일어난다.
    swapped = rig.run("qwen3.8-27b")

    assert swapped.code == 0, swapped.stderr
    assert swapped.report["server"]["qwen3-14b"]["action"] == "stop"
    assert swapped.report["server"]["qwen3.8-27b"]["action"] == "launch"
    assert not _alive_group(before)
    assert [line.split()[0] for line in rig.servers.read_text().splitlines()] == ["qwen3.8-27b"]


@needs_linux
def test_a_server_that_ignores_term_is_killed(rig: Rig) -> None:
    """TERM을 무시하는 서버는 TERM 대기 뒤 KILL로 내린다 — 내림이 서버를 남기지 않는다."""
    assert rig.run("qwen3-14b", STUB_IGNORE_TERM="1").code == 0
    group = rig.pgid("qwen3-14b")

    started = time.monotonic()
    down = rig.run()

    assert down.code == 0, down.stderr
    assert not _alive_group(group)
    assert time.monotonic() - started >= _constants()["TERM_WAIT_S"]


@needs_linux
def test_a_stale_server_is_stopped_and_launched_again(rig: Rig, tmp_path: Path) -> None:
    """떠 있으나 답하지 않는 서버(stale)는 내리고 새로 띄운다 — 「유지」로 보고하지 않는다."""
    hang = tmp_path / "hang"
    hang.mkdir()
    assert rig.run("qwen3-14b", STUB_HANG_DIR=str(hang)).code == 0
    stale = rig.pgid("qwen3-14b")
    (hang / str(stale)).touch()

    again = rig.run("qwen3-14b", STUB_HANG_DIR=str(hang))

    assert again.code == 0, again.stderr
    assert again.report["server"]["qwen3-14b"]["action"] == "launch"
    assert not _alive_group(stale)
    assert rig.pgid("qwen3-14b") != stale


@needs_linux
def test_a_reused_pgid_is_not_killed(rig: Rig) -> None:
    """SERVERS 행의 PGID를 남의 프로세스가 물려받았으면(시작 시각이 다르면) 죽이지 않고 행만 지운다.

    공용 장비에서 남의 것을 죽이지 않는 것이 원칙이다 — 리더 PID가 살아 있는 한 커널이 그 번호를
    내주지 않으므로, 리더가 있는데 시작 시각이 다르면 우리 그룹이 아니다.
    """
    stranger = _setsid("sleep", "300")
    try:
        time.sleep(0.2)
        rig.servers.write_text(f"qwen3-14b {stranger.pid} {RUN_ID} 1\n", encoding="utf-8")

        down = rig.run()

        assert down.code == 0, down.stderr
        assert stranger.poll() is None, "PGID가 재사용된 남의 프로세스를 죽였다"
        assert RUN_ID not in rig.servers.read_text()
    finally:
        stranger.kill()


@needs_linux
def test_a_guard_keeps_one_run_and_refuses_a_second(rig: Rig) -> None:
    """감시자는 run 하나만 지킨다 — 다른 run이 그 감시자 아래 띄우려 하면 거부한다(안 죽인다)."""
    guard = rig.guard()
    try:
        assert rig.run("qwen3-14b").code == 0

        other = rig.run("gpt-oss-20b", HARNESS_RUN_ID="run_y")

        assert other.code != 0
        assert "감시자" in other.report["error"] and RUN_ID in other.report["error"]
        assert _alive_group(rig.pgid("qwen3-14b"))
    finally:
        rig.run(HARNESS_RUN_ID="run_y")
        guard.kill()


@needs_linux
def test_a_guard_stops_with_its_run_and_never_kills_the_next_run(rig: Rig) -> None:
    """재사용 시나리오 — 감시자 A 아래 run 1이 끝나면 A가 멈추고, 새 감시자 B 아래 run 2는 A의
    마감에 안 죽는다.

    2026-09-29 적대 리뷰(HIGH): 09:00 감시자 A(당시 12시간) → run 1 11:00 끝 → 같은 A 아래 run 2
    11:05 시작 → 21:00 A의 마감이 run 2 서버를 통째로 내린다. 여기서는 A의 마감을 초로 줄였다 —
    A가 run 1과 함께 멈추지 않으면 테스트 안에서 그 마감이 run 2 서버를 내린다. A가 제대로 멈추면
    마감 분기는 안 탄다 — 마감이 자기 run만 내리는지는
    `test_the_guard_deadline_terms_then_kills_only_its_own_run`이 본다.
    """
    started = time.monotonic()
    guard_a = rig.guard(GUARD_MAX_S="15")
    guard_b = None
    try:
        assert rig.run("qwen3-14b").code == 0
        down = rig.run()
        assert down.code == 0, down.stderr
        assert _wait_exit(guard_a, 5), "run이 끝났는데 그 run의 감시자가 안 멈췄다"
        refused = rig.run("qwen3-14b", HARNESS_RUN_ID="run_2")
        assert refused.code != 0 and "감시자" in refused.report["error"]

        guard_b = rig.guard(GUARD_MAX_S="600")
        assert rig.run("qwen3-14b", HARNESS_RUN_ID="run_2").code == 0
        second = int(
            next(
                line.split()[1]
                for line in rig.servers.read_text().splitlines()
                if line.split()[2] == "run_2"
            )
        )
        time.sleep(max(0.0, 18 - (time.monotonic() - started)))

        assert _alive_group(second), "끝난 run의 감시자 마감이 다음 run 서버를 죽였다"
        assert guard_b.poll() is None
    finally:
        rig.run(HARNESS_RUN_ID="run_2")
        guard_a.kill()
        if guard_b is not None:
            guard_b.kill()


@needs_linux
def test_the_guard_deadline_terms_then_kills_only_its_own_run(rig: Rig) -> None:
    """마감이면 자기 run의 서버만 TERM → (대기) → KILL. 다른 run의 행은 건드리지 않는다."""
    stubborn = _setsid("bash", "-c", "trap '' TERM; sleep 300")
    polite = _setsid("sleep", "300")
    stranger = _setsid("sleep", "300")
    guard = None
    try:
        time.sleep(0.2)
        guard = rig.guard(GUARD_MAX_S="2", GUARD_TERM_WAIT_S="2")
        rig.servers.write_text(
            "".join(
                f"{key} {proc.pid} {run} {_starttime(proc.pid)}\n"
                for key, proc, run in (
                    ("qwen3-14b", stubborn, RUN_ID),
                    ("qwen3.8-27b", polite, RUN_ID),
                    ("gpt-oss-20b", stranger, "run_other"),
                )
            ),
            encoding="utf-8",
        )
        (rig.dir / "guard.owner").write_text(f"{guard.pid} {RUN_ID}\n", encoding="utf-8")

        assert _wait_exit(guard, 15), "마감이 지났는데 감시자가 안 끝났다"

        # 순한 서버는 TERM에, TERM을 무시하는 서버는 대기 뒤 KILL에 죽었다 — 순서까지 문다.
        assert polite.wait(timeout=5) == -signal.SIGTERM
        assert stubborn.wait(timeout=5) == -signal.SIGKILL
        assert stranger.poll() is None, "다른 run의 서버를 마감으로 죽였다"
        log = (rig.dir / "logs" / "guard.log").read_text(encoding="utf-8")
        assert log.index(f"TERM qwen3-14b -{stubborn.pid}") < log.index(
            f"KILL qwen3-14b -{stubborn.pid}"
        )
        assert "deadline" in (rig.dir / "KILLED").read_text(encoding="utf-8")
    finally:
        for proc in (stubborn, polite, stranger):
            proc.kill()
        if guard is not None:
            guard.kill()


@needs_linux
def test_stop_guard_ends_the_guard_without_killing(rig: Rig) -> None:
    server = _setsid("sleep", "300")
    try:
        time.sleep(0.2)
        rig.servers.write_text(
            f"qwen3-14b {server.pid} {RUN_ID} {_starttime(server.pid)}\n", encoding="utf-8"
        )
        guard = rig.guard()
        (rig.dir / "STOP_GUARD").touch()

        assert _wait_exit(guard, 5)
        assert server.poll() is None
        assert not (rig.dir / "guard.pid").exists()
        assert not (rig.dir / "KILLED").exists()
    finally:
        server.kill()


def _starttime(pid: int) -> str:
    """`/proc/<pid>/stat` 22번 필드 — 교대 명령이 SERVERS 행에 싣는 값."""
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


@needs_linux
def test_a_batch_rechecks_the_guard_before_every_launch(rig: Rig) -> None:
    """두 키를 한 호출로 띄우는 중 감시자가 끝나면 둘째 키는 안 띄운다 — 감시자 확인은
    배치 시작의 사진이 아니라 기동마다다. 첫 키의 가짜 vLLM이 뜨자마자 감시자를 죽인다(테스트 전용
    훅).

    2026-09-29 감시자 적대 리뷰(HIGH): 첫 키를 띄운 직후 감시자가 통합메모리 하한에 걸려 서버를
    내리고 끝나면, 재확인 없이 둘째 키를 띄웠다(방금 내려가 메모리 대기도 쉽게 지난다).
    """
    if not _port_free(8001):
        pytest.skip("포트 8001이 이미 쓰인다")
    guard = rig.guard()
    try:
        result = rig.run("qwen3-14b", "qwen3.8-27b", STUB_KILL_GUARD="1")

        assert guard.wait(timeout=5) == -signal.SIGKILL
        assert result.code != 0
        assert "감시자" in result.report["error"]
        assert result.report["server"]["qwen3-14b"]["action"] == "launch"
        assert "qwen3.8-27b" not in rig.servers.read_text()
        assert not list((rig.dir / "logs").glob("qwen3.8-27b.*"))
    finally:
        guard.kill()


@needs_linux
def test_the_swap_script_ignores_an_owner_record_from_another_guard(rig: Rig) -> None:
    """옛 감시자가 남긴 소유 기록(지금 감시자와 다른 pid)은 무효다 — 새 감시자 아래서 띄우고
    다시 적는다."""
    (rig.dir / "guard.owner").write_text("4194302 run_old\n", encoding="utf-8")

    up = rig.run("qwen3-14b")

    assert up.code == 0, up.stderr
    assert (rig.dir / "guard.owner").read_text().split() == [str(os.getpid()), RUN_ID]


@needs_linux
def test_the_guard_ignores_an_owner_record_from_another_guard(rig: Rig) -> None:
    """감시자는 자기 pid가 적힌 소유 기록만 믿는다 — 옛 감시자의 기록으로 마감에 그 run 서버를
    내리지 않는다."""
    server = _setsid("sleep", "300")
    guard = None
    try:
        time.sleep(0.2)
        rig.servers.write_text(
            f"qwen3-14b {server.pid} {RUN_ID} {_starttime(server.pid)}\n", encoding="utf-8"
        )
        (rig.dir / "guard.owner").write_text(f"4194302 {RUN_ID}\n", encoding="utf-8")
        guard = rig.guard(GUARD_MAX_S="2", GUARD_TERM_WAIT_S="1")

        assert _wait_exit(guard, 10)
        assert server.poll() is None, "옛 감시자의 소유 기록으로 서버를 내렸다"
        assert "지키던 run이 없어" in (rig.dir / "KILLED").read_text(encoding="utf-8")
    finally:
        server.kill()
        if guard is not None:
            guard.kill()


@needs_linux
def test_a_stopping_guard_is_refused_by_name(rig: Rig) -> None:
    """STOP_GUARD가 있으면(run 끝 내림이나 사람이 감시자를 멈추는 중) 띄우지 않고 그 원인을
    말한다."""
    (rig.dir / "STOP_GUARD").touch()

    result = rig.run("qwen3-14b")

    assert result.code != 0
    assert "STOP_GUARD" in result.report["error"]
    assert RUN_ID not in rig.servers.read_text()


# 감시자가 정리 없이 죽으면(SIGKILL·OOM·하드리셋) pid 파일이 남는다. 그 번호를 무관한
# 프로세스가 물려받은 두 모양 — 번호만(시작 시각이 없다) · 번호와 그 프로세스가 아닌 시작
# 시각. `pid_only`는 번호만 보는 판정을, `stale_starttime`은 시작 시각을 안 보는 판정을 문다.
_STALE_GUARD_PID = {"pid_only": "{pid}\n", "stale_starttime": "{pid} 1\n"}


@needs_linux
@pytest.mark.parametrize("form", sorted(_STALE_GUARD_PID))
def test_a_pid_file_naming_an_unrelated_live_process_is_not_a_guard(rig: Rig, form: str) -> None:
    """pid 파일의 번호가 살아 있어도 시작 시각이 함께 맞지 않으면 감시자가 아니다 — 서버를 감시
    없이 띄우지 않는다. SERVERS 행이 PGID 재사용을 시작 시각으로 막는 것과 같은 대조다."""
    stranger = _setsid("sleep", "300")
    try:
        time.sleep(0.2)
        (rig.dir / "guard.pid").write_text(
            _STALE_GUARD_PID[form].format(pid=stranger.pid), encoding="utf-8"
        )

        result = rig.run("qwen3-14b")

        assert result.code != 0, "무관한 프로세스의 번호를 감시자로 믿고 감시 없이 띄웠다"
        assert "감시자" in result.report["error"]
        assert RUN_ID not in rig.servers.read_text()
    finally:
        stranger.kill()


@needs_linux
@pytest.mark.parametrize("form", sorted(_STALE_GUARD_PID))
def test_a_new_guard_takes_over_a_pid_file_that_names_an_unrelated_process(
    rig: Rig, form: str
) -> None:
    """같은 파일 앞에서 새 감시자가 「이미 떠 있다」(exit 2)로 물러나면 run을 시작할 수 없다 — 그
    번호는 감시자가 아니므로 새 감시자가 넘겨받는다."""
    stranger = _setsid("sleep", "300")
    guard = None
    try:
        time.sleep(0.2)
        (rig.dir / "guard.pid").write_text(
            _STALE_GUARD_PID[form].format(pid=stranger.pid), encoding="utf-8"
        )
        guard = subprocess.Popen(
            ["bash", GUARD.as_posix()],
            cwd=ROOT,
            env={**rig.env, "GUARD_POLL_S": "1"},
            start_new_session=True,
        )

        assert not _wait_exit(guard, 3), "새 감시자가 무관한 번호를 감시자로 믿고 물러났다"
        assert (rig.dir / "guard.pid").read_text().split()[0] == str(guard.pid)
    finally:
        stranger.kill()
        if guard is not None:
            guard.kill()


_GIB = 2**30
# 80 GiB — ATOM `user-1004.slice`의 `memory.max`(docs/portability.md §6 ATOM 행 ·
# docs/experiments/README.md 확정 #4). 기본 임계(90%)는 72 GiB다.
_CG_LIMIT = 80 * _GIB
# v1 `memory.limit_in_bytes`의 「제한 없음」(4 KiB 페이지).
_V1_UNLIMITED = 9223372036854771712


def _eventually(check: Callable[[], bool], seconds: float) -> bool:
    """`check`가 참이 될 때까지 짧게 폴링한다 — 느린 러너에서도 고정 sleep 대신 쓴다."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return check()


def _stat(path: Path, anon_gib: float) -> None:
    """`memory.stat`을 통째로 바꿔 쓴다(감시자가 반쯤 쓴 파일을 읽지 않게). 진짜 키(v2 `anon` ·
    v1 `total_rss`) 앞뒤에 이름이 비슷한 키(`active_anon`·`anon_thp` … / `rss`·`total_rss_huge`
    …)를 상한보다 큰 값으로 둔다 — 실제 파일에도 있는 키들이고, 느슨하게 읽으면 상한을 넘은
    것으로 오독한다."""
    anon, big = int(anon_gib * _GIB), 500 * _GIB
    if (path / "memory.limit_in_bytes").exists():
        keys = [
            f"rss {big}",
            f"total_rss {anon}",
            f"total_rss_huge {big}",
            f"total_active_anon {big}",
        ]
    else:
        keys = [f"active_anon {big}", f"anon {anon}", f"anon_thp {big}", f"inactive_anon {big}"]
    tmp = path / "memory.stat.tmp"
    tmp.write_text("\n".join([*keys, f"file {_GIB}", ""]), encoding="utf-8")
    tmp.replace(path / "memory.stat")


def _cgroup(path: Path, limit: int | str, anon_gib: float, *, v1: bool = False) -> Path:
    """cgroup 디렉터리 대역 하나 — `cgroup.procs`(v1·v2 모두 모든 cgroup에 있다 — 대역 트리 밖의
    부모에는 없다) · 상한(v2 `memory.max` · v1 `memory.limit_in_bytes`) · `memory.stat`."""
    path.mkdir(parents=True)
    (path / "cgroup.procs").write_text("", encoding="utf-8")
    limit_file = "memory.limit_in_bytes" if v1 else "memory.max"
    (path / limit_file).write_text(f"{limit}\n", encoding="utf-8")
    _stat(path, anon_gib)
    return path


def _cgroup_tree(root: Path, layout: str, anon_gib: float) -> tuple[Path, Path]:
    """(감시자에게 줄 시작 디렉터리 `GUARD_CGROUP_DIR`, 상한이 걸린 디렉터리). anon은 상한이 걸린
    쪽의 값이고, 두 층이면 말단은 anon 1 GiB다.

    - `v2` · `v1` — 시작 디렉터리에 상한(80 GiB)이 있다.
    - `v2_leaf_max` — 상한이 말단이 아니라 부모 slice에 걸린 배치(확정 #4 — 2026-08-28의
      `user-1004.slice/session-*.scope`). 말단은 `max`다.
    - `v2_leaf_zero` — 말단 `memory.max`가 0이다. 상한이 아니다 — 상한으로 치면 첫 폴링에 전부
      내린다.
    - `v1_leaf_unlimited` — 말단이 v1의 「제한 없음」 값이다.
    - `no_limit` — 말단도 부모도 `max`(2026-09-02처럼 상한이 경로에 없다).
    """
    if layout in ("v2", "v1"):
        cg = _cgroup(root / "cg", _CG_LIMIT, anon_gib, v1=layout == "v1")
        return cg, cg
    v1 = layout.startswith("v1")
    leaf_limit = {"v2_leaf_zero": 0, "v1_leaf_unlimited": _V1_UNLIMITED}.get(layout, "max")
    top_limit = "max" if layout == "no_limit" else _CG_LIMIT
    top = _cgroup(root / "user-1004.slice", top_limit, anon_gib, v1=v1)
    return _cgroup(top / "session-3.scope", leaf_limit, 1, v1=v1), top


def _two_runs(rig: Rig) -> tuple[subprocess.Popen, subprocess.Popen]:
    """서버 대역 둘 — 이 run의 것과 다른 run의 것. SERVERS에 둘 다 적는다."""
    ours, other = _setsid("sleep", "300"), _setsid("sleep", "300")
    time.sleep(0.2)
    rig.servers.write_text(
        f"qwen3-14b {ours.pid} {RUN_ID} {_starttime(ours.pid)}\n"
        f"gpt-oss-20b {other.pid} run_other {_starttime(other.pid)}\n",
        encoding="utf-8",
    )
    return ours, other


@needs_linux
@pytest.mark.parametrize(
    ("layout", "anon_gib", "pct"),
    [
        pytest.param("v2", 72.1, None, id="v2-72.1GiB"),  # 기본 임계 72 GiB 바로 위
        pytest.param("v1", 72.1, None, id="v1-72.1GiB"),
        pytest.param("v2_leaf_max", 73, None, id="v2_leaf_max"),
        pytest.param("v1_leaf_unlimited", 73, None, id="v1_leaf_unlimited"),
        pytest.param("v2", 41, "50", id="v2-41GiB-pct50"),  # 임계 40 GiB — 기본 90%면 안 걸린다
    ],
)
def test_the_guard_terms_every_server_once_cgroup_anon_passes_the_threshold(
    rig: Rig, tmp_path: Path, layout: str, anon_gib: float, pct: str | None
) -> None:
    """상한이 걸린 cgroup의 anon이 임계(상한 × `GUARD_CGROUP_PCT` — 기본 90%)를 넘으면 서버를
    내린다 — 상한에 닿으면 예고 없이 OOM kill이라(`memory.high`가 `max` — docs/portability.md
    §6) 그 전에 내린다. 옛 감시자(2026-09-24~28 스모크)의 「anon > 72 GiB(80 GiB × 0.9)」
    층이다. 기본 임계는 여기의 72.1 GiB와 아래 대조의 71.9 GiB가 양쪽에서 문다.

    **run과 무관하게 전부** 내린다 — 상한은 run이 아니라 cgroup 단위라서다(guard.sh 머리 주석
    3). 그래서 이 감시자가 지키는 run(guard.owner)의 서버와 다른 run의 서버가 둘 다 TERM을
    받는다. 소유 기록을 먼저 적고 anon을 올린다 — 반대면 소유 기록 없이 걸려 run 한정 판정과
    구별이 안 된다.

    `v2_leaf_max`는 말단만 읽는 판정을, `v1_leaf_unlimited`는 v1 「제한 없음」을 상한으로 치는
    판정을 문다(둘 다 부모 상한으로 걸린다). 시작 로그가 실제로 보는 cgroup을 적는다. 이 리그는
    전용 VRAM(`STUB_GPU_MEM`이 숫자)이라 통합메모리 하한이 안 돈다.
    """
    ours, other = _two_runs(rig)
    guard = None
    try:
        start, limited = _cgroup_tree(tmp_path, layout, anon_gib=0)
        env = {"GUARD_CGROUP_DIR": str(start), "GUARD_TERM_WAIT_S": "1"}
        guard = rig.guard(**env, **({"GUARD_CGROUP_PCT": pct} if pct else {}))
        (rig.dir / "guard.owner").write_text(f"{guard.pid} {RUN_ID}\n", encoding="utf-8")
        _stat(limited, anon_gib)

        assert _wait_exit(guard, 30), "cgroup anon이 임계를 넘었는데 감시자가 안 끝났다"
        assert ours.wait(timeout=10) == -signal.SIGTERM
        assert other.wait(timeout=10) == -signal.SIGTERM, "cgroup 층이 지키는 run의 서버만 내렸다"
        assert "cgroup" in (rig.dir / "KILLED").read_text(encoding="utf-8")
        log = (rig.dir / "logs" / "guard.log").read_text(encoding="utf-8")
        assert f"cgroup={limited} " in log
    finally:
        for proc in (ours, other):
            proc.kill()
        if guard is not None:
            guard.kill()


@needs_linux
@pytest.mark.parametrize(
    ("layout", "anon_gib"),
    [
        pytest.param("v2", 71.9, id="v2-71.9GiB"),  # 기본 임계 72 GiB 바로 아래
        pytest.param("v1", 71.9, id="v1-71.9GiB"),
        pytest.param("v2_leaf_zero", 10, id="v2_leaf_zero"),
    ],
)
def test_the_guard_leaves_every_server_while_cgroup_anon_stays_under_the_threshold(
    rig: Rig, tmp_path: Path, layout: str, anon_gib: float
) -> None:
    """임계 아래면 아무것도 안 내린다 — 위 테스트의 72.1 GiB와 함께 기본 임계 90%를 문다(20%·
    50%·91%로 바꾸면 어느 한쪽이 빨개진다). `_stat`의 이름이 비슷한 큰 키는 정확히 `anon`·
    `total_rss`만 읽는지를, `v2_leaf_zero`는 `memory.max` 0을 상한으로 치지 않고 그 위의 진짜
    상한을 보는지를 문다.

    기다림은 감시자 자신의 마감(`GUARD_MAX_S`)이다 — 마감까지 폴링마다 cgroup을 보고, 지키던
    run이 없어 아무것도 안 내린 채 끝난다. 감시자가 끝나는 것을 기다리지 고정 sleep이 아니다.
    """
    ours, other = _two_runs(rig)
    guard = None
    try:
        start, limited = _cgroup_tree(tmp_path, layout, anon_gib)
        guard = rig.guard(GUARD_CGROUP_DIR=str(start), GUARD_MAX_S="3", GUARD_TERM_WAIT_S="1")

        assert _wait_exit(guard, 30), "마감이 지났는데 감시자가 안 끝났다"
        killed = (rig.dir / "KILLED").read_text(encoding="utf-8")
        assert "지키던 run이 없어" in killed, f"임계 아래에서 내렸다: {killed}"
        assert ours.poll() is None and other.poll() is None
        log = (rig.dir / "logs" / "guard.log").read_text(encoding="utf-8")
        assert f"cgroup={limited} " in log
    finally:
        for proc in (ours, other):
            proc.kill()
        if guard is not None:
            guard.kill()


@needs_linux
@pytest.mark.parametrize(
    ("knob", "value"),
    [
        pytest.param("GUARD_CGROUP_DIR", "no_limit", id="dir-no_limit_above"),
        pytest.param("GUARD_CGROUP_DIR", "not_a_cgroup", id="dir-not_a_cgroup"),
        pytest.param("GUARD_CGROUP_PCT", "abc", id="pct-abc"),
        pytest.param("GUARD_CGROUP_PCT", "9.5", id="pct-9.5"),
        pytest.param("GUARD_CGROUP_PCT", "0", id="pct-0"),
        pytest.param("GUARD_CGROUP_PCT", "101", id="pct-101"),
    ],
)
def test_the_guard_refuses_to_start_on_a_cgroup_setting_it_cannot_use(
    rig: Rig, tmp_path: Path, knob: str, value: str
) -> None:
    """cgroup 설정을 못 쓰면 뜨지 않는다 — 이름을 대고 exit 2다.

    - `GUARD_CGROUP_DIR`에서 위로 숫자 상한을 못 찾으면(끝까지 `max` · `cgroup.procs`가 없는
      일반 디렉터리 — memory 파일이 있어도 cgroup이 아니다) — 보라고 한 층을 조용히 건너뛰면 그
      층이 있다고 믿은 채 run이 돈다. 올라가는 탐색이 대역 트리 밖에서 끝나는 것도 여기서 본다.
    - `GUARD_CGROUP_PCT`가 1~100 정수가 아니면 — 소수·문자는 셸 산술에서 감시자가 소리 없이
      죽고(pid 파일만 남는다), 0은 첫 폴링에 전부 내린다.

    거부는 pid 파일·STOP_GUARD를 건드리기 **전**이고, 사유는 stderr와 guard.log 둘 다에 남는다
    — 문서의 기동 줄은 stderr를 버린다(`> /dev/null 2>&1`).
    """
    if value == "no_limit":
        value = str(_cgroup_tree(tmp_path, "no_limit", anon_gib=500)[0])
    elif value == "not_a_cgroup":
        plain = _cgroup(tmp_path / "plain", _CG_LIMIT, 500)
        (plain / "cgroup.procs").unlink()
        value = str(plain)
    # fixture의 감시자 대역을 치운다 — 있으면 「이미 떠 있다」가 먼저 걸린다.
    (rig.dir / "guard.pid").unlink()
    (rig.dir / "STOP_GUARD").touch()  # 거부가 STOP_GUARD를 지우기 전인지 본다
    guard = subprocess.Popen(
        ["bash", GUARD.as_posix()],
        cwd=ROOT,
        env={**rig.env, "GUARD_POLL_S": "1", knob: value},
        start_new_session=True,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert _wait_exit(guard, 15), f"{knob}={value}를 못 쓰는데 감시자가 떴다"
        assert guard.returncode == 2
        assert knob in guard.stderr.read()
        log_path = rig.dir / "logs" / "guard.log"
        log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        assert "시작 거부" in log and knob in log, "거부 사유가 guard.log에 없다"
        assert not (rig.dir / "guard.pid").exists()
        assert (rig.dir / "STOP_GUARD").exists()
    finally:
        guard.kill()
        guard.stderr.close()


@needs_linux
def test_a_second_guard_refuses_while_the_first_is_alive(rig: Rig) -> None:
    """살아 있는 감시자(pid 파일의 번호·시작 시각이 맞다) 앞에서 둘째 감시자는 exit 2로
    물러난다 — 감시자는 run당 하나다. 둘째가 넘겨받으면 첫째는 다음 폴링에 「다른 감시자가
    대신했다」로 끝나 지키던 run의 감시가 소리 없이 바뀐다. 거부 사유는 guard.log에도 남고
    첫째의 pid 파일은 그대로다."""
    first = rig.guard()
    second = None
    try:
        second = subprocess.Popen(
            ["bash", GUARD.as_posix()],
            cwd=ROOT,
            env={**rig.env, "GUARD_POLL_S": "1"},
            start_new_session=True,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert _wait_exit(second, 15), "살아 있는 감시자 앞에서 둘째 감시자가 떴다"
        assert second.returncode == 2
        assert "이미 떠 있다" in second.stderr.read()
        log = (rig.dir / "logs" / "guard.log").read_text(encoding="utf-8")
        assert f"시작 거부 — 감시자 {first.pid}가 이미 떠 있다" in log
        assert (rig.dir / "guard.pid").read_text().split()[0] == str(first.pid)
        assert first.poll() is None
    finally:
        first.kill()
        if second is not None:
            second.kill()
            second.stderr.close()


@needs_linux
def test_a_guard_quits_quietly_once_its_pid_file_names_another_guard(rig: Rig) -> None:
    """pid 파일이 다른 감시자로 바뀌면 감시자는 조용히 끝난다 — 서버를 안 내리고 그 파일도 안
    지운다(머리 주석). 다른 감시자 자리에는 fixture의 감시자 대역(이 테스트 프로세스의 번호·시작
    시각)을 적는다."""
    ours, other = _two_runs(rig)
    guard = None
    try:
        guard = rig.guard()
        replacement = f"{os.getpid()} {_starttime(os.getpid())}\n"
        (rig.dir / "guard.pid").write_text(replacement, encoding="utf-8")

        assert _wait_exit(guard, 30), "pid 파일이 다른 감시자로 바뀌었는데 감시자가 안 끝났다"
        assert guard.returncode == 0
        assert (rig.dir / "guard.pid").read_text() == replacement
        assert ours.poll() is None and other.poll() is None
        assert not (rig.dir / "KILLED").exists()
        log = (rig.dir / "logs" / "guard.log").read_text(encoding="utf-8")
        assert "다른 감시자가 대신했다" in log
    finally:
        for proc in (ours, other):
            proc.kill()
        if guard is not None:
            guard.kill()


@needs_linux
def test_the_guard_logs_cgroup_anon_and_warns_once_when_the_cgroup_goes_unreadable(
    rig: Rig, tmp_path: Path
) -> None:
    """30 s 상태 줄에 cgroup anon과 내리는 임계를 적는다 — 스모크 보고서가 「cgroup anon 최대」를
    여기서 뽑는다(옛 감시자의 `cg_anon`). 시작 뒤 cgroup을 못 읽게 되면 조용히 넘어가지 않고 경고를
    **한 번** 적는다 — 그동안 cgroup 층이 멈춘다는 것이 로그에 남는다. 폴링마다 적으면 로그가 경고로
    찬다. 마감(`GUARD_MAX_S`)까지 여러 번 폴링한 뒤에도 경고가 하나인지 본다."""
    start, _ = _cgroup_tree(tmp_path, "v2", anon_gib=10)
    log_path = rig.dir / "logs" / "guard.log"
    guard = rig.guard(GUARD_CGROUP_DIR=str(start), GUARD_MAX_S="8")
    try:
        # pid 파일이 첫 로그 줄보다 먼저 생긴다 — 로그가 아직 없을 수 있다.
        assert _eventually(
            lambda: log_path.exists() and "cg_anon=10.0GiB" in log_path.read_text("utf-8"), 30
        )
        shutil.rmtree(start)

        assert _wait_exit(guard, 30), "마감이 지났는데 감시자가 안 끝났다"
        log = log_path.read_text(encoding="utf-8")
        assert "cg_kill_at=72.0GiB" in log
        assert log.count("를 못 읽는다") == 1, log
        assert "지키던 run이 없어" in (rig.dir / "KILLED").read_text(encoding="utf-8")
    finally:
        guard.kill()


def _drain_until(fd: int, needle: bytes, seconds: float) -> bool:
    """비차단으로 연 FIFO 읽기 쪽에서 `needle`이 나올 때까지 읽는다."""
    seen = b""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            seen += os.read(fd, 65536)
        except BlockingIOError:
            pass
        if needle in seen:
            return True
        time.sleep(0.05)
    return False


@needs_linux
@pytest.mark.parametrize("exit_path", ["stop_guard", "deadline_without_run", "deadline_kill"])
def test_a_replaced_guard_does_not_delete_the_new_guards_pid_file(rig: Rig, exit_path: str) -> None:
    """감시자는 pid 파일이 자기 것일 때만 지운다 — 지우는 자리 셋 모두(STOP_GUARD · 지키던
    run이 없는 마감 · 내림 끝). 옛 감시자가 끝나는 사이(자기 것인지 확인한 뒤 지우기 전) 새
    감시자가 STOP_GUARD를 지우고 자기 pid를 적으면, 옛 감시자가 지우는 것은 새 감시자의
    파일이다 — 새 감시자는 다음 루프에서 「다른 감시자가 대신했다」로 조용히 끝나고, 교대는
    「감시자가 안 떠 있다」로 거부된다(안전한 쪽이지만 원인을 알기 어렵다).

    로그를 FIFO로 만들어 그 틈을 결정적으로 세운다 — 읽는 쪽이 없으면 옛 감시자의 로그 쓰기가
    열기에서 멈춘다. `stop_guard`는 `stop requested`를, 마감 둘은 KILLED 줄(`… | tee -a
    guard.log > KILLED`)을 쓰다 멈춘다 — `deadline_kill`은 지키던 run을 내리는 `kill_rows`의 첫
    줄이다. 마감 둘은 KILLED가 생기는 것(표준출력 리다이렉트가 FIFO 열기보다 먼저다)을 멈춘
    신호로 삼는다.
    """
    logs = rig.dir / "logs"
    logs.mkdir(exist_ok=True)
    fifo = logs / "guard.log"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)  # 읽는 쪽이 있는 동안 쓰기는 안 멈춘다
    old = new = None
    try:
        if exit_path == "stop_guard":
            old = rig.guard()
        else:
            old = rig.guard(GUARD_MAX_S="5", GUARD_TERM_WAIT_S="1")
        if exit_path == "deadline_kill":
            (rig.dir / "guard.owner").write_text(f"{old.pid} {RUN_ID}\n", encoding="utf-8")
        assert _drain_until(reader, b"up=[", 10), "옛 감시자의 첫 상태 줄이 안 나왔다"
        os.close(reader)  # 읽는 쪽 없음 — 옛 감시자의 다음 로그 쓰기는 열기에서 멈춘다
        reader = -1
        if exit_path == "stop_guard":
            (rig.dir / "STOP_GUARD").touch()
            time.sleep(1.8)
        else:
            assert _eventually((rig.dir / "KILLED").exists, 30), "마감 갈래에 안 들어섰다"
            (rig.dir / "STOP_GUARD").touch()  # 새 감시자가 넘겨받게
        assert old.poll() is None, "옛 감시자가 로그 쓰기에서 멈춰 있어야 한다"

        new = subprocess.Popen(
            ["bash", GUARD.as_posix()],
            cwd=ROOT,
            env={**rig.env, "GUARD_POLL_S": "1"},
            start_new_session=True,
        )
        pid_file = rig.dir / "guard.pid"
        for _ in range(100):
            if pid_file.exists() and pid_file.read_text().split()[:1] == [str(new.pid)]:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("새 감시자가 pid를 안 적었다")

        # 읽는 쪽이 붙으면 둘 다 풀린다 — 옛 감시자는 rm으로, 새 감시자는 루프로.
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        assert _wait_exit(old, 5)
        time.sleep(2.5)
        assert pid_file.exists(), "옛 감시자가 새 감시자의 pid 파일을 지웠다"
        assert new.poll() is None, "새 감시자가 「다른 감시자가 대신했다」로 조용히 끝났다"
    finally:
        if reader >= 0:
            os.close(reader)
        for guard in (old, new):
            if guard is not None:
                guard.kill()
