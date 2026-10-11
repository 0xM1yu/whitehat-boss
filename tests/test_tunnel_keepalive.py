"""`tools/serve/`의 ssh 호출 — keepalive 옵션과 `vast-tunnel.sh --loop`.

2026-10-11 run에서 `ServerAliveCountMax` 기본 3 × `ServerAliveInterval` 10 s = 30초 무응답에 터널이
스스로 끊겼다(#571). 두 스크립트의 ssh 줄이 그 기본을 넘기는 값을 들고 있는지, 그리고 `--loop`가
없을 때 ssh 인자가 종전과 같은지(옵션 둘만 더해진 채) 가짜 `ssh`로 본다.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TUNNEL = Path("tools/serve/vast-tunnel.sh")
SWAP_REMOTE = Path("tools/serve/swap-remote.sh")

# ssh 기본 ServerAliveCountMax 3 × 이 스크립트의 ServerAliveInterval 10 s
DEFAULT_SILENCE_S = 3 * 10

_BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(_BASH is None, reason="bash가 없다")


def _ssh_options(script: Path) -> dict[str, str]:
    """ssh를 부르는 줄(`ServerAliveInterval`을 든 줄)의 `-o K=V` 쌍."""
    lines = [
        line
        for line in (ROOT / script).read_text(encoding="utf-8").splitlines()
        if "ServerAliveInterval" in line and not line.lstrip().startswith("#")
    ]
    assert len(lines) == 1, lines
    return dict(re.findall(r"-o\s+(\w+)=(\S+)", lines[0]))


@pytest.mark.parametrize("script", [TUNNEL, SWAP_REMOTE], ids=lambda p: p.name)
def test_the_ssh_line_survives_longer_silence_than_the_default_and_keeps_tcp_alive(
    script: Path,
) -> None:
    options = _ssh_options(script)
    silence_s = int(options["ServerAliveInterval"]) * int(options["ServerAliveCountMax"])
    assert silence_s > DEFAULT_SILENCE_S, options
    assert options["TCPKeepAlive"] == "yes", options


@needs_bash
@pytest.mark.parametrize("script", [TUNNEL, SWAP_REMOTE], ids=lambda p: p.name)
def test_the_scripts_parse(script: Path) -> None:
    done = subprocess.run(
        [_BASH, "-n", script.as_posix()],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert done.returncode == 0, done.stderr


# --- vast-tunnel.sh 실행: 가짜 ssh가 인자를 한 줄씩 적는다 ----------------------------------

_STUB_SSH = """#!/bin/sh
n=$(cat "$STUB_DIR/calls" 2> /dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$STUB_DIR/calls"
printf '%s\\n' "$@" > "$STUB_DIR/argv.$n"
[ "$n" -ge "${STUB_STOP_AT:-0}" ] && [ "${STUB_STOP_AT:-0}" -gt 0 ] && : > "$SERVE_DIR/STOP_GUARD"
exit 255
"""


def _run_tunnel(tmp_path: Path, *args: str, stop_at: int = 0, preset_stop: bool = False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(_STUB_SSH, encoding="utf-8", newline="\n")
    ssh.chmod(0o755)
    serve = tmp_path / "serve"
    serve.mkdir()
    if preset_stop:
        (serve / "STOP_GUARD").write_text("", encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}{';' if sys.platform == 'win32' else ':'}{Path(_BASH).parent}",
        "HOME": str(tmp_path),
        "STUB_DIR": str(tmp_path),
        "SERVE_DIR": str(serve),
        "STUB_STOP_AT": str(stop_at),
        "SWAP_SSH_HOST": "root@203.0.113.9",
        "SWAP_SSH_PORT": "40123",
    }
    return subprocess.run(
        [_BASH, TUNNEL.as_posix(), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _argv(tmp_path: Path, n: int) -> list[str]:
    return (tmp_path / f"argv.{n}").read_text(encoding="utf-8").splitlines()


@needs_bash
def test_without_loop_the_ssh_argv_is_the_old_one_plus_the_two_keepalive_options(
    tmp_path: Path,
) -> None:
    done = _run_tunnel(tmp_path)
    assert done.returncode == 255, done.stderr  # exec — ssh의 종료 코드가 그대로 나온다
    ports = (8000, 8001, 8002, 8003, 8006)
    forwards = [x for p in ports for x in ("-L", f"127.0.0.1:{p}:127.0.0.1:{p}")]
    old = [
        "-N",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=10",
        *forwards,
        "-p", "40123",
        "--", "root@203.0.113.9",
    ]  # fmt: skip
    new = old[:5] + ["-o", "ServerAliveCountMax=12", "-o", "TCPKeepAlive=yes"] + old[5:]
    assert _argv(tmp_path, 1) == new
    assert (tmp_path / "calls").read_text().strip() == "1"  # 다시 잇지 않는다


@needs_bash
def test_loop_reconnects_after_ssh_exits_with_the_same_argv_and_stops_on_the_marker(
    tmp_path: Path,
) -> None:
    done = _run_tunnel(tmp_path, "--loop", stop_at=2)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "calls").read_text().strip() == "2"
    assert _argv(tmp_path, 1) == _argv(tmp_path, 2)
    assert "-o" in _argv(tmp_path, 1) and "ServerAliveCountMax=12" in _argv(tmp_path, 1)


@needs_bash
def test_loop_does_not_start_ssh_when_the_marker_is_already_there(tmp_path: Path) -> None:
    done = _run_tunnel(tmp_path, "--loop", preset_stop=True)
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "calls").exists()


@needs_bash
def test_an_unknown_argument_is_refused_before_ssh(tmp_path: Path) -> None:
    done = _run_tunnel(tmp_path, "--forever")
    assert done.returncode == 2
    assert not (tmp_path / "calls").exists()
