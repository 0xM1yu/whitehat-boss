"""`tools/serve/`의 ssh 호출 — keepalive 옵션과 `vast-tunnel.sh --loop`.

`ServerAliveCountMax` 기본값(3 × `ServerAliveInterval` 10 s)이면 30초 무응답에 터널이 끊긴다
(#605 보고서 §6 — 관측된 끊김의 원인이 아니라 그 예방이다).
두 스크립트를 **실제로 돌려** 가짜 `ssh`(PATH 앞)가 받은 argv에서 `-o K=V`를
읽는다 - 옵션이 `--` 앞에 있어야 ssh의 옵션이다(뒤로 가면 원격 명령의 인자가 된다).
`--loop`가 없을 때 argv는 종전과 같다(옵션 둘만 더해진 채). 가짜 `sleep`이 재시도 간격을 기록한다.
신호(Ctrl-C·SIGTERM) 처리는 테스트하지 않았다.
"""

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


# --- 실행: 가짜 ssh가 인자를 한 줄씩 적고, 가짜 sleep이 인자를 적는다 ---------------------------

_STUB_SSH = """#!/bin/sh
n=$(cat "$STUB_DIR/calls" 2> /dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$STUB_DIR/calls"
printf '%s\\n' "$@" > "$STUB_DIR/argv.$n"
if [ "${STUB_STOP_AT:-0}" -gt 0 ] && [ "$n" -ge "$STUB_STOP_AT" ]; then
  : > "$SERVE_DIR/STOP_GUARD"
fi
exit "${STUB_SSH_EXIT:-255}"
"""
_STUB_SLEEP = """#!/bin/sh
echo "$@" >> "$STUB_DIR/sleeps"
"""


def _run(
    tmp_path: Path,
    script: Path,
    *args: str,
    stop_at: int = 0,
    preset_stop: bool = False,
    env_extra: dict[str, str] | None = None,
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ssh", _STUB_SSH), ("sleep", _STUB_SLEEP)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    serve = tmp_path / "serve"
    serve.mkdir()
    if preset_stop:
        (serve / "STOP_GUARD").write_text("", encoding="utf-8")
    sep = ";" if sys.platform == "win32" else ":"
    path = sep.join([str(bin_dir), str(Path(_BASH).parent), str(Path(shutil.which("git")).parent)])
    env = {
        "PATH": path,
        "HOME": str(tmp_path),
        "STUB_DIR": str(tmp_path),
        "SERVE_DIR": str(serve),
        "STUB_STOP_AT": str(stop_at),
        "SWAP_SSH_HOST": "root@203.0.113.9",
        "SWAP_SSH_PORT": "40123",
        **(env_extra or {}),
    }
    return subprocess.run(
        [_BASH, script.as_posix(), *args],
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


def _ssh_options(argv: list[str]) -> dict[str, str]:
    """`--` 앞의 `-o K=V` 쌍. `--`가 없으면 실패한다."""
    before = argv[: argv.index("--")]
    return dict(
        v.split("=", 1) for flag, v in zip(before, before[1:], strict=False) if flag == "-o"
    )


def _assert_keepalive(argv: list[str]) -> None:
    options = _ssh_options(argv)
    silence_s = int(options["ServerAliveInterval"]) * int(options["ServerAliveCountMax"])
    assert silence_s > DEFAULT_SILENCE_S, options
    assert options["TCPKeepAlive"] == "yes", options


@needs_bash
def test_swap_remote_gives_ssh_the_keepalive_options_before_the_double_dash(
    tmp_path: Path,
) -> None:
    done = _run(
        tmp_path,
        SWAP_REMOTE,
        "qwen3-14b",
        stop_at=0,
        env_extra={
            "SWAP_REMOTE_ROOT": "/root/harness",
            "SWAP_REMOTE_PYTHON": "/usr/bin/python3",
            "HARNESS_RUN_ID": "run_20261011_0001",
            "HARNESS_DEVICE": "vastai-remote",
            "STUB_SSH_EXIT": "0",
        },
    )
    assert done.returncode == 0, done.stderr
    argv = _argv(tmp_path, 1)
    _assert_keepalive(argv)
    assert argv[argv.index("-p") + 1] == "40123"


@needs_bash
def test_tunnel_gives_ssh_the_keepalive_options_before_the_double_dash(tmp_path: Path) -> None:
    _run(tmp_path, TUNNEL)
    _assert_keepalive(_argv(tmp_path, 1))


@needs_bash
def test_without_loop_the_ssh_argv_is_the_old_one_plus_the_two_keepalive_options(
    tmp_path: Path,
) -> None:
    done = _run(tmp_path, TUNNEL)
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
    done = _run(tmp_path, TUNNEL, "--loop", stop_at=2)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "calls").read_text().strip() == "2"
    assert _argv(tmp_path, 1) == _argv(tmp_path, 2)
    _assert_keepalive(_argv(tmp_path, 2))
    # 다시 잇기 전에 5초를 쉰다 - 끊긴 박스에 바로 되붙어 도는 루프가 아니다
    assert (tmp_path / "sleeps").read_text().split() == ["5"]


@needs_bash
def test_loop_does_not_start_ssh_when_the_marker_is_already_there(tmp_path: Path) -> None:
    done = _run(tmp_path, TUNNEL, "--loop", preset_stop=True)
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "calls").exists()
    assert "STOP_GUARD" in done.stderr


@needs_bash
def test_an_unknown_argument_is_refused_before_ssh(tmp_path: Path) -> None:
    done = _run(tmp_path, TUNNEL, "--forever")
    assert done.returncode == 2
    assert not (tmp_path / "calls").exists()


@needs_bash
def test_extra_arguments_after_loop_are_refused_before_ssh(tmp_path: Path) -> None:
    done = _run(tmp_path, TUNNEL, "--loop", "--forever")
    assert done.returncode == 2
    assert not (tmp_path / "calls").exists()
