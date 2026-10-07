"""`preflight` 테스트의 공용 가짜 — 바깥 세계(ssh·git·docker·TCP·PATH 해소·환경변수)를 대신한다.

`World`의 기본값은 **모든 점검이 통과하는** 세계다 — 테스트는 한 칸씩 어긋나게 한다. 호출은 전부
기록된다. `forbid_real_world`가 진짜 프로세스·소켓이 새면 터뜨려 네트워크·Docker·GPU 없이 돈다는
것을 증명한다(`tests/test_preflight*.py`가 각자 픽스처로 감싼다).
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from src import preflight as pf
from src.cli import Check, Severity
from src.config import load_config
from tests.oracle_fakes import TASK_ID, submit_script

ROOT = Path(__file__).resolve().parent.parent
# 이슈 #534의 재현 프로필 — 교대를 선언하고 도구 런타임이 docker다.
# 전제가 어긋나면 `make_world`가 죽는다.
PROFILE = "harness_pool"
DEVICE = "vastai-remote"
SERVER = "http://127.0.0.1:8666"
KEY = "SECRET-key-0123456789"
COMMIT = "a" * 40
BOOT = "11111111-2222-3333-4444-555555555555"
OK, WARN, ERROR = Severity.OK, Severity.WARN, Severity.ERROR
# ssh는 진짜 오류 앞에 경고를 낸다 — 사유는 stderr의 끝에서 읽어야 한다.
_SSH_STDERR = "\n".join(
    [
        "banner noise",
        "Warning: Permanently added 'vast-box' to the list of known hosts.",
        "ssh: connect to host vast-box port 22: Connection refused",
    ]
)
_Probes = pf.Probes  # CLI 테스트가 `pf.Probes`를 갈아 끼우므로 진짜 클래스를 미리 잡아 둔다


# --- 바깥 세계의 가짜 ---------------------------------------------------------------------


def _script_ports() -> set[int]:
    """`vast-tunnel.sh`의 `PORTS` — 코드와 **따로** 읽는 값이다(둘이 같아야 잠금이 선다)."""
    text = (ROOT / "tools/serve/vast-tunnel.sh").read_text(encoding="utf-8")
    found = re.search(r"^PORTS=\(([\d ]+)\)$", text, re.M)
    assert found, "vast-tunnel.sh의 PORTS 줄을 못 찾았다"
    return {int(port) for port in found.group(1).split()}


def _script_needs(text: str) -> set[str]:
    """`swap-remote.sh`의 `need NAME "…"` 호출이 요구하는 변수 이름."""
    return set(re.findall(r"^need ([A-Z_]+) ", text, re.M))


def _local_index_version() -> str:
    text = (ROOT / "tools/harness-tools/indexer.py").read_text(encoding="utf-8")
    found = re.search(r'^INDEX_VERSION = "([^"]+)"', text, re.M)
    assert found, "indexer.py의 INDEX_VERSION 줄을 못 찾았다"
    return found.group(1)


def _canary(path: Path, **overrides: object) -> Path:
    body = {
        "task": TASK_ID,
        "verdict": "pass",
        "ok": True,
        "repeat": 10,
        "below_measured_repeat": False,
        "mmap_rnd_bits": "28",
        "rnd_bits_above_28": False,
        "rnd_bits_unmeasured": False,
        "measured_at": "2026-10-07T01:02:03+00:00",
        "docker_version": "29.5.3",
        "completed": True,
        "segv_count": 0,
        "problems": [],
        "host_boot_id": BOOT,
        **overrides,
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _bundle(tmp_path: Path, **script: object) -> Path:
    task = tmp_path / "task"
    task.mkdir(exist_ok=True)
    (task / "submit.sh").write_text(submit_script(**script), encoding="utf-8")
    (task / "repo-vul.tar.gz").write_bytes(b"not read by preflight")
    return task


class World:
    """주입하는 가짜 — 호출을 전부 기록한다. 기본값은 **모든 점검이 통과하는** 세계다."""

    def __init__(self, tmp_path: Path) -> None:
        self.env = {
            "SWAP_SSH_HOST": "vast-box",
            "SWAP_REMOTE_ROOT": "/root/harness",
            "SWAP_REMOTE_PYTHON": "/opt/py/bin/python",
            "CYBERGYM_API_KEY": KEY,
        }
        self.commit = COMMIT
        self.remote_commit = COMMIT
        self.boot_id = BOOT
        self.image_version = _local_index_version()
        self.listening = {("127.0.0.1", port) for port in _script_ports()}
        self.listening.add(("127.0.0.1", 8666))
        self.ssh_exit = 0
        self.docker_exit = 0
        self.docker_missing = False
        self.calls: list[tuple[list[str], float]] = []
        self.tcp_calls: list[tuple[str, int]] = []
        self.bundle = _bundle(tmp_path)
        self.canary = _canary(tmp_path / "canary.json")

    def run(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), timeout))
        tool = argv[0]
        if tool in ("docker", "podman") and self.docker_missing:
            raise FileNotFoundError(2, "No such file or directory", tool)
        if tool == "git":
            out, code, err = self.commit, 0, ""
        elif tool == "ssh":
            code = self.ssh_exit
            out = self.remote_commit if code == 0 else ""
            err = "" if code == 0 else _SSH_STDERR
        elif tool in ("docker", "podman"):
            code = self.docker_exit
            err = "" if code == 0 else "Unable to find image 'harness-tools:v3' locally"
            if code != 0:
                out = ""
            elif "cat" in argv:
                out = self.boot_id
            else:
                out = f'INDEX_VERSION = "{self.image_version}"'
        else:
            raise AssertionError(f"모르는 호출: {argv}")
        return subprocess.CompletedProcess(argv, code, out + "\n", err)

    def tcp(self, host: str, port: int) -> str | None:
        self.tcp_calls.append((host, port))
        return None if (host, port) in self.listening else "ConnectionRefusedError"

    def resolve(self, command: tuple[str, ...]) -> tuple[str, ...]:
        return (f"/usr/bin/{command[0]}", *command[1:])

    def probes(self) -> pf.Probes:
        return _Probes(run=self.run, tcp=self.tcp, resolve=self.resolve, environ=self.env)

    def check(self, **overrides: object) -> list[Check]:
        args: dict[str, object] = {
            "device": DEVICE,
            "task_dir": self.bundle,
            "oracle_server": SERVER,
            "oracle_canary": self.canary,
            **overrides,
        }
        return pf.preflight(PROFILE, probes=self.probes(), **args)  # type: ignore[arg-type]

    def tools_called(self) -> list[str]:
        return [argv[0] for argv, _ in self.calls]


def forbid_real_world(monkeypatch: pytest.MonkeyPatch) -> None:
    """진짜 프로세스·소켓을 열지 못하게 한다 — 가짜가 못 막은 호출이 새면 그 자리에서 죽는다."""

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"진짜 바깥 세계를 불렀다: {args} {kwargs}")

    monkeypatch.setattr(pf.subprocess, "run", boom)
    monkeypatch.setattr(pf.socket, "create_connection", boom)


def make_world(tmp_path: Path) -> World:
    config = load_config(PROFILE)
    assert config.swap is not None and config.profile.adapters.tool_runner == "docker", (
        f"{PROFILE}의 전제(교대 선언 · docker)가 바뀌었다 — 이 파일의 시나리오를 다시 본다"
    )
    return World(tmp_path)


def one(checks: list[Check], label: str) -> Check:
    found = [c for c in checks if c.message.startswith(label)]
    assert len(found) == 1, (label, [c.render() for c in checks])
    return found[0]


def text_of(checks: list[Check]) -> str:
    return "\n".join(c.render() for c in checks)
