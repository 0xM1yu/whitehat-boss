"""`preflight` 테스트의 공용 가짜 — 바깥 세계(ssh·git·docker·TCP·PATH 해소·환경변수)를 대신한다.

`World`의 기본값은 **모든 점검이 통과하는** 세계다 — 테스트는 한 칸씩 어긋나게 한다. 호출은 전부
기록된다. `forbid_real_world`가 진짜 프로세스·소켓이 새면 터뜨려 네트워크·Docker·GPU 없이 돈다는
것을 증명한다(`tests/test_preflight*.py`가 각자 픽스처로 감싼다). 컨테이너 호출은 전부
`assert_docker_safe`를 지난다 — 안전 인자(네트워크·당김·마운트 없음)가 한 곳에서 걸린다.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from src import preflight as pf
from src import preflight_probes as pp
from src.adapters.container_tools import DEFAULT_IMAGE
from src.cli import Check, Severity
from src.config import load_config
from tests.oracle_fakes import TASK_ID, submit_script

ROOT = Path(__file__).resolve().parent.parent
# 이슈 #534의 재현 프로필 — 교대를 선언하고 도구 런타임이 docker다.
# 전제가 어긋나면 `make_world`가 죽는다.
PROFILE = "harness_pool"
# 교대를 선언하지 않는 실서빙 프로필(서버를 사람이 띄운다) — 리뷰가 `[OK]` exit 0을 재현했다.
UNSWAPPED_PROFILE = "harness_pool_hybrid"
DEVICE = "vastai-remote"
SERVER = "http://127.0.0.1:8666"
# 앞 두 글자(`Zq`)도 출력에 나올 일이 없는 값이다 — 접두를 찍는 변이를 접두 길이마다 가린다.
KEY = "Zq7#unique-long-key-8f3a91c2d8e4b605"
COMMIT = "a" * 40
# 16진 글자가 든 값이다 — 대소문자를 접는 비교가 숫자뿐인 값에서는 안 드러난다
BOOT = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
OK, WARN, ERROR = Severity.OK, Severity.WARN, Severity.ERROR
# ssh는 진짜 오류 앞에 경고를 낸다 — 사유는 stderr의 끝에서 읽어야 한다.
_SSH_STDERR = "\n".join(
    [
        "banner noise",
        "Warning: Permanently added 'vast-box' to the list of known hosts.",
        "ssh: connect to host vast-box port 22: Connection refused",
    ]
)
_Probes = pp.Probes  # CLI 테스트가 `pf.Probes`를 갈아 끼우므로 진짜 클래스를 미리 잡아 둔다
REAL_CHECKSUMS = pf._check_checksums  # `fill_checksums`가 갈아 끼우기 전의 진짜 판정


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


def _local_indexer_sha() -> str:
    return hashlib.sha256((ROOT / "tools/harness-tools/indexer.py").read_bytes()).hexdigest()


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
    with tarfile.open(task / "repo-vul.tar.gz", "w:gz") as tar:  # 열리는 진짜 아카이브다
        body = b"int main(void) { return 0; }\n"
        member = tarfile.TarInfo("src-vul/main.c")
        member.size = len(body)
        tar.addfile(member, io.BytesIO(body))
    return task


def config_with_command(tmp_path: Path, command: str) -> Path:
    """장비 파일의 교대 `command`만 바꾼 config 사본."""
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg, dirs_exist_ok=True)
    path = cfg / "serving" / f"{DEVICE}.yaml"
    text = path.read_text(encoding="utf-8")
    old = "command: [bash, tools/serve/swap-remote.sh]"
    assert old in text
    path.write_bytes(text.replace(old, f"command: {command}").encode("utf-8"))
    return cfg


def config_with_endpoint(tmp_path: Path, old: str, new: str) -> Path:
    """registry 주소 하나만 바꾼 config 사본 — 그 주소의 철자는 호출자가 config에서 읽어 온다."""
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg, dirs_exist_ok=True)
    path = cfg / "models.yaml"
    text = path.read_text(encoding="utf-8")
    assert text.count(f'endpoint: "{old}"') == 1, old
    path.write_bytes(text.replace(f'endpoint: "{old}"', f'endpoint: "{new}"').encode("utf-8"))
    return cfg


def assert_docker_safe(argv: list[str]) -> None:
    """`preflight`의 **모든** 컨테이너 호출이 지켜야 하는 안전 인자 — 한 곳에서 단언한다."""
    assert DEFAULT_IMAGE in argv, argv
    head = argv[: argv.index(DEFAULT_IMAGE)]
    assert head[0] in ("docker", "podman") and head[1] == "run", argv
    assert "--rm" in head and "--network=none" in head, argv
    assert head[head.index("--pull") + 1] == "never", argv
    unsafe = {"-v", "--volume", "--mount", "--privileged", "--network=host", "-e", "--env"}
    assert not unsafe & set(head) and not any(
        a.startswith("--net") and a != "--network=none" for a in head
    ), argv


class World:
    """주입하는 가짜 — 호출을 전부 기록한다. 기본값은 **모든 점검이 통과하는** 세계다."""

    def __init__(self, tmp_path: Path, profile: str = PROFILE) -> None:
        self.profile = profile
        self.env = {
            "SWAP_SSH_HOST": "vast-box",
            "SWAP_REMOTE_ROOT": "/root/harness",
            "SWAP_REMOTE_PYTHON": "/opt/py/bin/python",
            "CYBERGYM_API_KEY": KEY,
        }
        self.commit = COMMIT
        self.remote_commit = COMMIT
        self.boot_id: str | None = BOOT
        self.mmap_rnd_bits: str | None = "28"
        self.image_version = _local_index_version()
        self.image_sha = _local_indexer_sha()
        self.python_missing = False
        self.listening = {("127.0.0.1", port) for port in _script_ports()}
        self.listening |= {("127.0.0.1", 8004), ("127.0.0.1", 8005), ("127.0.0.1", 8666)}
        self.ssh_exit = 0
        self.uncached: set[str] = set()  # 원격 HF 캐시에 없는 `repo@rev` (#571)
        self.weights_ssh_exit = 0
        self.docker_exit = 0
        self.docker_missing = False
        self.calls: list[tuple[list[str], float]] = []
        self.tcp_calls: list[tuple[str, int]] = []
        self.bundle = _bundle(tmp_path)
        self.canary = _canary(tmp_path / "canary.json")

    def _docker(self, argv: list[str]) -> tuple[str, int, str]:
        assert_docker_safe(argv)
        if self.docker_exit != 0:
            return "", self.docker_exit, "Unable to find image 'harness-tools:v3' locally"
        if "cat" in argv:  # 호스트 사실 — 파일 순서대로, 못 읽은 값은 줄이 빠진다
            lines = [v for v in (self.mmap_rnd_bits, self.boot_id) if v is not None]
            return "\n".join(lines), 0, ""
        version = f'INDEX_VERSION = "{self.image_version}"'
        if "python3" in argv:
            if self.python_missing:
                return "", 127, "exec: python3: executable file not found in $PATH"
            return f"sha256={self.image_sha}\n{version}", 0, ""
        return version, 0, ""  # grep

    def run(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), timeout))
        tool = argv[0]
        if tool in ("docker", "podman") and self.docker_missing:
            raise FileNotFoundError(2, "No such file or directory", tool)
        if tool == "git":
            out, code, err = self.commit, 0, ""
        elif tool == "ssh" and "snapshots" in argv[-1]:  # 가중치 캐시 점검(#571) — 읽기뿐이다
            code = self.weights_ssh_exit
            have = re.findall(r'echo "have ([^"]+)"', argv[-1])
            out = "\n".join(f"have {p}" for p in have if p not in self.uncached)
            err = "" if code == 0 else _SSH_STDERR
        elif tool == "ssh":
            code = self.ssh_exit
            out = self.remote_commit if code == 0 else ""
            err = "" if code == 0 else _SSH_STDERR
        elif tool in ("docker", "podman"):
            out, code, err = self._docker(argv)
        else:
            raise AssertionError(f"모르는 호출: {argv}")
        # 끝 개행 하나는 `cat`·`git`이 늘 붙인다 — 없애려면 값에 따로 넣는다
        return subprocess.CompletedProcess(argv, code, out + "\n", err)

    def tcp(self, host: str, port: int) -> str | None:
        self.tcp_calls.append((host, port))
        return None if (host, port) in self.listening else "ConnectionRefusedError"

    def resolve(self, command: tuple[str, ...]) -> tuple[str, ...]:
        # `_resolved`와 같은 갈래다 — 경로가 든 이름은 PATH로 안 풀고 그대로 돌려준다. 늘 풀어
        # 주는 가짜였을 때는 `check_bash`의 경로 갈래에 **한 번도 안 들어가** 없는 경로가 초록이다.
        head = command[0]
        if os.sep in head or (os.altsep and os.altsep in head):
            return command
        return (f"/usr/bin/{head}", *command[1:])

    def probes(self) -> pp.Probes:
        return _Probes(run=self.run, tcp=self.tcp, resolve=self.resolve, environ=self.env)

    def check(self, **overrides: object) -> list[Check]:
        args: dict[str, object] = {
            "device": DEVICE,
            "task_dir": self.bundle,
            "oracle_server": SERVER,
            "oracle_canary": self.canary,
            **overrides,
        }
        return pf.preflight(self.profile, probes=self.probes(), **args)  # type: ignore[arg-type]

    def tools_called(self) -> list[str]:
        return [argv[0] for argv, _ in self.calls]


def forbid_real_world(monkeypatch: pytest.MonkeyPatch) -> None:
    """진짜 프로세스·소켓을 열지 못하게 한다 — 가짜가 못 막은 호출이 새면 그 자리에서 죽는다."""

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"진짜 바깥 세계를 불렀다: {args} {kwargs}")

    monkeypatch.setattr(pp.subprocess, "run", boom)
    monkeypatch.setattr(pp.socket, "create_connection", boom)


def fill_checksums(monkeypatch: pytest.MonkeyPatch) -> None:
    """`harness_pool`의 서빙 모델 checksum은 아직 0이라 그대로면 점검에 WARN이 늘 하나 붙는다.

    「모든 점검이 통과하는 세계」를 만들려고 그 판정만 채운 것으로 갈아 끼운다 — 진짜 판정은
    `REAL_CHECKSUMS`로 따로 시험한다.
    """
    monkeypatch.setattr(
        pf,
        "_check_checksums",
        lambda config: [Check(OK, "실서빙 역할의 model checksum이 모두 채워져 있다")],
    )


def make_world(tmp_path: Path, profile: str = PROFILE) -> World:
    config = load_config(profile)
    if profile == PROFILE:
        assert config.swap is not None and config.profile.adapters.tool_runner == "docker", (
            f"{PROFILE}의 전제(교대 선언 · docker)가 바뀌었다 — 이 파일의 시나리오를 다시 본다"
        )
    if profile == UNSWAPPED_PROFILE:
        assert config.swap is None and config.served_models(), (
            f"{UNSWAPPED_PROFILE}의 전제(교대 없음 · 실서빙 역할)가 바뀌었다"
        )
    return World(tmp_path, profile)


def one(checks: list[Check], label: str) -> Check:
    found = [c for c in checks if c.message.startswith(label)]
    assert len(found) == 1, (label, [c.render() for c in checks])
    return found[0]


def text_of(checks: list[Check]) -> str:
    return "\n".join(c.render() for c in checks)
