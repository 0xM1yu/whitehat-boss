"""교대 명령(`tools/serve/swap.sh`)·감시자(`tools/serve/guard.sh`)와 config의 대조.

스크립트는 장비(ATOM·vast)에서만 실제 vLLM을 띄운다 — 에이전트와 CI는 장비에 접속하지 않는다.
여기서 보는 것은 넷이다.

1. 문법(`bash -n`).
2. 교대 마감(`settle_timeout_s`)이 두 장비 파일의 모든 교대를 덮는가.
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
import socket
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

from src.config import load_config, load_serving

ROOT = Path(__file__).resolve().parent.parent
# `bash`에 **상대 경로**로 넘긴다 — Windows에서 PATH의 `bash`가 WSL 런처여도 cwd로 풀린다.
SWAP = Path("tools/serve/swap.sh")
GUARD = Path("tools/serve/guard.sh")
DEVICES = ("atom", "vastai")

# 교대 마감 식에 들어가는 스크립트 상수 — `NAME=숫자` 한 줄.
_CONST = re.compile(r"^(ATTEMPTS|TERM_WAIT_S|KILL_WAIT_S|MEM_WAIT_S)=(\d+)$", re.M)


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
    """검출기 핀 — 양성: `tools/serve/swap.sh`의 상수 넷. 음성: 기본값을 든 환경 변수 줄은 상수가
    아니다(같은 파일의 `MIN_AVAIL_GIB="${MIN_AVAIL_GIB:-12}"` 꼴 — 장비마다 조정하는 손잡이다)."""
    assert set(_constants()) == {"ATTEMPTS", "TERM_WAIT_S", "KILL_WAIT_S", "MEM_WAIT_S"}
    assert _CONST.search('MIN_AVAIL_GIB="${MIN_AVAIL_GIB:-12}"') is None


# 교대 순서 — 4 Scout → 5 Auditor → 7 Critic(ADR 0009 「2026-09-29 제안」 절 3).
_STAGE_ROLES = (("scout_memory", "scout_boundary", "scout_injection"), ("auditor",), ("critic",))


@pytest.mark.parametrize("device", DEVICES)
def test_the_swap_deadline_covers_every_swap_on_every_device(device: str) -> None:
    """`settle_timeout_s`(교대 한 번의 마감)가 그 장비의 가장 무거운 교대를 덮는다.

    교대 한 번의 최악 = 앞 단계 서버 내림 + 키마다 시도 × (준비 상한 + 메모리 대기 +
    실패 기동 내림).
    마감이 모자라면 느린 기동이 도중에 끊겨 run이 `model_swap_failed`로 선다 — 준비 상한을 올리고
    마감을 안 올리는 실수를 잡는다. 한계: 준비 상한이 실제 기동 시간을 덮는지는 이 검사 밖이다
    (장비 파일 주석의 계산 — 미실측).
    """
    config = load_config("harness_pool")
    serving = load_serving(device)
    const = _constants()
    stop = const["TERM_WAIT_S"] + const["KILL_WAIT_S"]
    served = config.served_models()
    assert config.swap is not None

    previous: set[str] = set()
    for roles in _STAGE_ROLES:
        keys = {served[role] for role in roles}
        per_key = const["MEM_WAIT_S"] + stop
        worst = len(previous) * stop + sum(
            const["ATTEMPTS"] * (serving.servers[key].ready_timeout_s + per_key) for key in keys
        )
        assert config.swap.settle_timeout_s >= worst, (device, sorted(keys), worst)
        previous = keys


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
# vastai 행 `seccoderx-8b`의 포트(registry `endpoint`) — 가짜 서버가 여기 뜬다.
_PORT = 8000

# 가짜 vLLM — 받은 argv를 첫 줄에 찍고(교대 명령이 로그 파일로 돌린다) `/v1/models`·채팅에 답한다.
_FAKE_VLLM = """#!{python}
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

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
    # 감시자 대역 — 교대 명령은 감시자가 살아 있어야 띄운다. pid만 살아 있으면 된다.
    (serve / "guard.pid").write_text(str(os.getpid()), encoding="utf-8")
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

        up = rig.run("seccoderx-8b")
        assert up.code == 0, up.stderr
        assert (up.report["ok"], up.report["memory"], up.report["run_id"]) == (True, "vram", RUN_ID)
        server = up.report["server"]["seccoderx-8b"]
        assert (server["action"], server["attempts"], server["max_model_len"]) == (
            "launch", 1, 40960
        )
        log = Path(server["log"])
        assert re.fullmatch(r"seccoderx-8b\.run_x\.\d{8}T\d{6}Z\.log", log.name)
        argv = log.read_text(encoding="utf-8")
        assert "--max-model-len 40960" in argv
        assert "--revision 25d7f38146bdf0fad1eac09c586b1993ef3e6ee4" in argv
        ours = [line.split() for line in rig.servers.read_text().splitlines() if RUN_ID in line]
        assert [(key, run) for key, _, run in ours] == [("seccoderx-8b", RUN_ID)]

        down = rig.run()
        assert down.code == 0, down.stderr
        assert down.report["server"]["seccoderx-8b"]["action"] == "stop"
        assert not _alive_group(int(ours[0][1]))
        assert other.poll() is None, "남의 run 서버를 죽였다"
        assert f"gpt-oss-20b {other.pid} run_other" in rig.servers.read_text()
    finally:
        other.kill()


@needs_linux
def test_the_swap_script_refuses_to_launch_beside_a_foreign_gpu_process(rig: Rig) -> None:
    """이 run 밖의 GPU 프로세스가 있으면 띄우지 않고 실패로 보고한다 — 죽이지도 않는다."""
    result = rig.run("seccoderx-8b", STUB_GPU_PIDS="4194301")

    assert result.code != 0
    assert result.report["ok"] is False and "GPU 프로세스" in result.report["error"]
    assert not list((rig.dir / "logs").glob("seccoderx-8b.*"))
    assert RUN_ID not in rig.servers.read_text()


@needs_linux
def test_the_swap_script_judges_unified_memory_by_mem_available(rig: Rig) -> None:
    """`nvidia-smi` 메모리가 `[N/A]`면(GB10 — ATOM) 통합메모리 판정이다. 하한을 크게 줘
    거절을 본다."""
    result = rig.run(
        "seccoderx-8b",
        STUB_GPU_MEM="[N/A], [N/A]",
        HARNESS_DEVICE="atom",
        MIN_AVAIL_GIB="100000",
        SWAP_DEADLINE_S="3",
    )

    assert result.code != 0
    assert result.report["memory"] == "unified"
    assert "MemAvailable" in result.report["error"]
    assert not list((rig.dir / "logs").glob("seccoderx-8b.*"))


@needs_linux
def test_the_swap_script_gives_up_on_a_held_lock_at_the_deadline(rig: Rig) -> None:
    """다른 교대가 잠금을 쥐고 있으면 마감까지 기다리다 실패로 보고한다 — 겹쳐 띄우지 않는다."""
    import fcntl  # Linux 전용

    with open(rig.dir / "swap.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = rig.run("seccoderx-8b", SWAP_DEADLINE_S="2")

    assert result.code != 0
    assert "잠금" in result.report["error"]
