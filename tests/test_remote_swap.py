"""원격 교대 — `tools/serve/`의 `swap-remote.sh`·`vast-bootstrap.sh`·`vast-tunnel.sh`와
장비 파일 `vastai-remote`.

래퍼는 원격의 `swap.sh`에 **인자·환경을 그대로 옮기고 결과를 그대로 돌려주는 것**이
전부다. 그래서 여기서는 가짜 `ssh`를 PATH 앞에 두고 원격 명령 문자열을 실제 `sh -c`로
돌린다 — 따옴표가 원격 셸을 지나서도 한 인자로 남는지를 문자열 비교가 아니라 파싱 결과로
본다. 가짜 `ssh`는 원격 명령을 `env -i`로 돌린다 — 로컬 환경이 새면 래퍼가 빠뜨린 변수도
원격에 있는 것처럼 보여 테스트가 거짓으로 초록이 된다. 그리고 진짜 ssh처럼 로그인 디렉터리
(HOME)에서 시작한다 — 상대 경로가 어디 기준으로 풀리는지가 결과를 가르는 결함이 있었다.

⚠ 진짜 ssh·vast는 안 탄다. 접속·호스트 키·터널의 거동은 장비 검증(PR 본문)의 몫이다.
⚠ Windows에서는 `posix` 표식 36건을 건너뛰고 나머지 7건이 돈다(2026-10-05 Win11 · Python
3.14.5 — 36 skipped는 `pytest`를 직접 돌려, 7 passed는 독립 재검토가 쟀다). 그 7건 중
`test_the_remote_scripts_parse` 3건은 맨 `bash -n`을 부르므로 Windows에서는 `which`가 고르는
Git Bash가 아니라 System32의 런처로 풀릴 수 있다(`CreateProcess`는 PATH보다 System32를 먼저
뒤진다 — #477과 같은 어긋남). 건너뛰는 사유로 예전에 적던 「`bash`가 WSL 런처라 환경이 안
넘어간다」는 #477에서 정정됐다 — 환경은 멀쩡했고 `bash`가 System32 아래의 런처로 풀린 것이다
(`swap-remote.sh` 머리 주석).
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

from src.adapters.model_swap import swap_runner
from src.config import CONFIG_DIR, load_config, load_serving
from src.interfaces.model_client import ModelSwapError

ROOT = Path(__file__).resolve().parent.parent
REMOTE = Path("tools/serve/swap-remote.sh")
BOOTSTRAP = Path("tools/serve/vast-bootstrap.sh")
TUNNEL = Path("tools/serve/vast-tunnel.sh")

posix = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("git") is None,
    reason="래퍼는 POSIX bash에서 돈다(Windows에서는 이 표식의 36건을 건너뛴다)",
)

_FAKE_SSH = """#!/bin/sh
printf '%s\\n' "$@" > "$SSH_ARGV_LOG"
for last; do :; done
cd "$HOME" || exit 1
exec env -i PATH="$PATH" HOME="$HOME" sh -c "$last"
"""

# 원격의 swap.sh 대역 — 받은 인자와 교대 환경을 JSON 한 줄로 낸다(진짜 swap.sh의 stdout 계약 모양).
_FAKE_SWAP = """#!/usr/bin/env bash
"{py}" -c 'import json, os, sys
names = ["HARNESS_RUN_ID", "HARNESS_DEVICE", "HARNESS_PYTHON", "SWAP_DEADLINE_S", "SWAP_SSH_HOST"]
print(json.dumps({{"argv": sys.argv[1:], "env": {{n: os.environ.get(n) for n in names}}}}))' "$@"
echo "remote-stderr" >&2
exit "$(cat "$(dirname "$0")/../../exit_code" 2> /dev/null || echo 0)"
"""


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.bin = tmp / "bin"
        self.bin.mkdir()
        ssh = self.bin / "ssh"
        ssh.write_text(_FAKE_SSH, encoding="utf-8")
        ssh.chmod(0o755)
        self.argv_log = tmp / "ssh.argv"
        self.remote = tmp / "remote 'root'"  # 공백·따옴표 — 원격 경로 인용도 본다
        serve = self.remote / "tools" / "serve"
        serve.mkdir(parents=True)
        swap = serve / "swap.sh"
        swap.write_text(_FAKE_SWAP.format(py=sys.executable), encoding="utf-8")
        swap.chmod(0o755)
        (self.remote / ".harness-commit").write_text(_head() + "\n", encoding="utf-8")
        self.env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp),
            "SSH_ARGV_LOG": str(self.argv_log),
            "SWAP_SSH_HOST": "vast-box",
            "SWAP_REMOTE_ROOT": str(self.remote),
            "SWAP_REMOTE_PYTHON": "/opt/remote/python3",
            "HARNESS_RUN_ID": "run_20261003_0001",
            "HARNESS_DEVICE": "vastai-remote",
            "HARNESS_PYTHON": "/local/only/python",
            "SWAP_DEADLINE_S": "4200",
        }

    def swap(self, *keys: str, **env: str | None) -> subprocess.CompletedProcess[str]:
        merged = {**self.env, **env}
        merged = {k: v for k, v in merged.items() if v is not None}
        return subprocess.run(
            ["bash", REMOTE.as_posix(), *keys],
            cwd=ROOT,
            env=merged,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def ssh_argv(self) -> list[str]:
        return self.argv_log.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def _report(done: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert len(lines) == 1, done.stdout  # swap.sh 계약 — stdout은 JSON 한 줄
    return json.loads(lines[0])


# --- 문법 ------------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash가 없다")
@pytest.mark.parametrize("script", [REMOTE, BOOTSTRAP, TUNNEL], ids=lambda p: p.name)
def test_the_remote_scripts_parse(script: Path) -> None:
    completed = subprocess.run(
        ["bash", "-n", script.as_posix()], cwd=ROOT, capture_output=True, text=True
    )
    assert completed.returncode == 0, completed.stderr


# --- 제어 바이트 -------------------------------------------------------------------------


def _control_bytes(raw: bytes) -> list[tuple[int, int]]:
    """(위치, 값) — LF·탭 말고 0x20 아래의 바이트. CR도 거른다(이 저장소는 LF만 쓴다)."""
    return [(at, byte) for at, byte in enumerate(raw) if byte < 0x20 and byte not in b"\n\t"]


def test_the_control_byte_scan_flags_the_real_defect_and_passes_the_fixed_line() -> None:
    # 양성 핀 — `1f0ec6a`의 `swap-remote.sh:30`에 실제로 있던 조각이다. 경로 구분자 뒤 `b`가 셸에서
    # 백스페이스로 먹혀 화면에는 `System32ash.exe`로 보였다. 음성 핀 — 같은 자리의 고친 모양(구분자
    # 없이 쓴다). CR도 잡는다.
    defect = b"WSL (`System32\x08ash.exe`)"
    assert _control_bytes(defect) == [(defect.index(b"\x08"), 8)]
    assert _control_bytes("WSL (System32 아래의 `bash.exe`)\n\t".encode()) == []
    assert _control_bytes(b"a\r\nb") == [(1, 13)]


def test_the_wrapper_and_the_serving_files_carry_no_control_bytes() -> None:
    """셸·문자열 안에서 백슬래시 이스케이프가 제어 문자로 먹히면 화면에는 글자 하나가 빠져 보인다
    (경로 System32 아래 bash.exe의 구분자 뒤 b가 0x08이 됐다 — 2026-10-05, `swap-remote.sh`).
    이 PR이 만지는 두 디렉터리의 전수다 — 다른 층(파이썬 문자열)은 `test_model_swap.py`가 본다."""
    files = sorted((ROOT / "tools/serve").glob("*.sh")) + sorted(
        (ROOT / "config/serving").glob("*.yaml")
    )
    assert files  # 글롭이 비면 전수가 아니다

    leaked = {p.name: found[:3] for p in files if (found := _control_bytes(p.read_bytes()))}

    assert leaked == {}


# --- 래퍼 -------------------------------------------------------------------------------


@posix
def test_keys_reach_the_remote_swap_script_one_argument_each(rig: Rig) -> None:
    # 실제 키는 영숫자·점·하이픈이다. 나머지는 인용이 원격 셸을 지나 한 인자로 남는지 보려는 값이다.
    keys = ("qwen3.8-27b", "seccoderx-8b", "a b", "it's", "$HOME", '"q"', "x;rm -rf /")
    report = _report(rig.swap(*keys))
    assert report["argv"] == list(keys)
    # 명령은 ssh를 지났다 — 로컬에서 swap.sh를 부르지 않았다
    assert rig.ssh_argv()[-2] == "vast-box"


@posix
def test_the_swap_environment_reaches_the_remote_and_the_local_python_does_not(rig: Rig) -> None:
    run_id = "run 'odd' $x \"y\""
    report = _report(rig.swap("qwen3-14b", HARNESS_RUN_ID=run_id))
    assert report["env"] == {
        "HARNESS_RUN_ID": run_id,
        "HARNESS_DEVICE": "vastai-remote",
        "HARNESS_PYTHON": "/opt/remote/python3",  # 로컬 인터프리터 경로는 원격에서 뜻이 없다
        "SWAP_DEADLINE_S": "4200",
        "SWAP_SSH_HOST": None,  # 래퍼 설정은 원격으로 안 간다
    }


@posix
def test_no_keys_reaches_the_remote_as_no_keys(rig: Rig) -> None:
    # run 끝 내림 — 키 없이 부르면 원격 swap.sh도 키 없이 돈다(자기 run 서버 전부 내림).
    assert _report(rig.swap())["argv"] == []


@posix
def test_the_remote_exit_code_and_stderr_come_back(rig: Rig) -> None:
    (rig.remote / "exit_code").write_text("7", encoding="utf-8")
    done = rig.swap("gpt-oss-20b")
    assert done.returncode == 7
    assert "remote-stderr" in done.stderr
    assert json.loads(done.stdout)["argv"] == ["gpt-oss-20b"]  # 실패해도 보고는 그대로 온다


@posix
@pytest.mark.parametrize(
    "name", ["SWAP_SSH_HOST", "SWAP_REMOTE_ROOT", "SWAP_REMOTE_PYTHON", "HARNESS_RUN_ID"]
)
def test_a_missing_setting_stops_before_ssh(rig: Rig, name: str) -> None:
    for value in (None, ""):
        done = rig.swap("qwen3-14b", **{name: value})
        assert done.returncode == 2, (name, value, done.stderr)
        assert name in done.stderr
        assert not rig.argv_log.exists(), "설정이 비었는데 ssh를 열었다"


@posix
def test_a_remote_copy_from_another_commit_is_refused(rig: Rig) -> None:
    (rig.remote / ".harness-commit").write_text("0" * 40 + "\n", encoding="utf-8")
    done = rig.swap("qwen3-14b")
    assert done.returncode == 2
    assert done.stdout == ""  # 원격 swap.sh를 부르지 않았다
    assert "vast-bootstrap.sh" in done.stderr
    assert "0" * 40 in done.stderr  # 어느 커밋이 갈렸는지 둘 다 보인다
    assert _head() in done.stderr


@posix
def test_the_local_commit_reaches_the_remote_shell_only_through_the_quoting(rig: Rig) -> None:
    # 로컬 `git rev-parse`는 hex만 주지만 「원격 셸에 가는 값은 전부 인용」에 예외가 없어야 한다.
    # 가짜 git이 적대 값을 주면 불일치 메시지 안의 생 값이 명령 치환으로 읽힌다(고치기 전 결함).
    git = rig.bin / "git"
    git.write_text("#!/bin/sh\nprintf '%s\\n' \"$FAKE_GIT_OUT\"\n", encoding="utf-8")
    git.chmod(0o755)
    hostile = "$(touch pwned)"
    done = rig.swap("qwen3-14b", FAKE_GIT_OUT=hostile)
    assert done.returncode == 2, done.stderr
    assert not (rig.remote / "pwned").exists(), "커밋 값이 원격 셸에서 실행됐다"
    assert hostile in done.stderr  # 값은 글자 그대로 메시지에 나온다


_BAD_ROOTS = ["workspace/harness", "~/harness", "{tmp}/harness/", "/", "//"]


@posix
@pytest.mark.parametrize("root", _BAD_ROOTS)
def test_swap_remote_refuses_a_root_that_is_not_a_clean_absolute_path_before_ssh(
    rig: Rig, tmp_path: Path, root: str
) -> None:
    done = rig.swap("qwen3-14b", SWAP_REMOTE_ROOT=root.replace("{tmp}", str(tmp_path)))
    assert done.returncode == 2, (root, done.stderr)
    assert "SWAP_REMOTE_ROOT" in done.stderr
    assert not rig.argv_log.exists(), "루트가 어긋났는데 ssh를 열었다"


@posix
def test_ssh_never_prompts_and_takes_the_port(rig: Rig) -> None:
    _report(rig.swap("qwen3-14b", SWAP_SSH_PORT="40123"))
    argv = rig.ssh_argv()
    assert "BatchMode=yes" in argv
    assert "-n" in argv
    assert argv[argv.index("-p") + 1] == "40123"
    _report(rig.swap("qwen3-14b", SWAP_SSH_PORT=None))
    assert "-p" not in rig.ssh_argv()


# --- 하네스 어댑터 → 래퍼 -------------------------------------------------------------------


@posix
def test_the_harness_adapter_drives_the_wrapper_named_by_the_device_file(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """하네스가 실제로 부르는 모양 — 장비 파일의 `command`를 `swap_runner`가 cwd·환경을 붙여 부른다.

    위 테스트들은 교대 환경을 손으로 넣는다. 여기서는 그 값을 **어댑터가** 넣는다 — 어댑터가 싣는
    `HARNESS_PYTHON`(로컬 인터프리터)이 원격에서 `SWAP_REMOTE_PYTHON`으로 바뀌는지, 마감이
    `f"{deadline_s:g}"` 그대로 가는지, 2차 확인이 registry 엔드포인트(로컬 — 터널 자리)를 보는지.
    """
    for name in ("PATH", "HOME", "SSH_ARGV_LOG", *(k for k in rig.env if k.startswith("SWAP_"))):
        monkeypatch.setenv(name, rig.env[name])
    for name in ("HARNESS_RUN_ID", "HARNESS_DEVICE", "HARNESS_PYTHON", "SWAP_DEADLINE_S"):
        monkeypatch.delenv(name, raising=False)  # 어댑터가 넣어야 한다 — 바깥 값이 새지 않게
    models = load_config("harness_pool").models
    seen: list[tuple[str, str]] = []

    def probe(endpoint: str, key: str) -> dict[str, object]:
        seen.append((endpoint, key))
        return {"ok": True, "reason": None, "max_model_len": models[key].context_limit}

    swap_to = swap_runner(
        load_serving("vastai-remote").command,
        models=models,
        deadline_s=4200,
        run_id="run_20261003_0009",
        device="vastai-remote",
        probe=probe,
    )

    report = swap_to(("qwen3-14b",))
    assert json.loads(str(report["argv"])) == ["qwen3-14b"]
    assert {k: v for k, v in report.items() if k.startswith("env.")} == {
        "env.HARNESS_RUN_ID": "run_20261003_0009",
        "env.HARNESS_DEVICE": "vastai-remote",
        "env.HARNESS_PYTHON": "/opt/remote/python3",
        "env.SWAP_DEADLINE_S": "4200",
        "env.SWAP_SSH_HOST": None,
    }
    assert seen == [(models["qwen3-14b"].endpoint, "qwen3-14b")]

    assert json.loads(str(swap_to(())["argv"])) == []  # run 끝 내림

    (rig.remote / "exit_code").write_text("3", encoding="utf-8")
    with pytest.raises(ModelSwapError) as failed:
        swap_to(("gpt-oss-20b",))
    assert "exit 3" in str(failed.value)
    assert json.loads(str(failed.value.report["argv"])) == ["gpt-oss-20b"]  # 실패 보고도 온다


# --- bootstrap ---------------------------------------------------------------------------


def _bootstrap(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", BOOTSTRAP.as_posix()],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@posix
def test_bootstrap_unpacks_head_and_stamps_the_commit(rig: Rig, tmp_path: Path) -> None:
    target = tmp_path / "boot 'root' $x"  # 원격 셸 인용을 보는 이름
    env = {**rig.env, "SWAP_REMOTE_ROOT": str(target), "SWAP_REMOTE_PYTHON": sys.executable}
    done = _bootstrap(env)
    assert done.returncode == 0, done.stderr
    assert (target / ".harness-commit").read_text(encoding="utf-8").strip() == _head()
    assert (target / "tools/serve/swap.sh").is_file()
    assert (target / "config/serving/vastai-remote.yaml").is_file()
    assert not (target / "tests").exists()  # 교대 몫만 보낸다
    assert not Path(f"{target}.incoming").exists()  # 임시 사본을 남기지 않는다


@posix
def test_bootstrap_refuses_a_root_it_did_not_make(rig: Rig, tmp_path: Path) -> None:
    target = tmp_path / "someone else"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    env = {**rig.env, "SWAP_REMOTE_ROOT": str(target), "SWAP_REMOTE_PYTHON": sys.executable}
    done = _bootstrap(env)
    assert done.returncode == 2, done.stderr
    assert (target / "keep.txt").read_text(encoding="utf-8") == "x"
    assert not (target / ".harness-commit").exists()


_OLD_MARKER = "a" * 40 + "\n"  # 이 스크립트가 쓴 마커의 모양 — 커밋 하나, 한 줄


@posix
def test_bootstrap_keeps_the_old_copy_when_the_python_cannot_read_the_device_file(
    rig: Rig, tmp_path: Path
) -> None:
    target = tmp_path / "boot"
    target.mkdir()
    (target / ".harness-commit").write_text(_OLD_MARKER, encoding="utf-8")
    env = {**rig.env, "SWAP_REMOTE_ROOT": str(target), "SWAP_REMOTE_PYTHON": "false"}
    done = _bootstrap(env)
    assert done.returncode == 2
    assert "원격 파이썬" in done.stderr  # 가드가 아니라 파이썬 검사가 막았다
    assert (target / ".harness-commit").read_text(encoding="utf-8") == _OLD_MARKER


@posix
@pytest.mark.parametrize(
    "marker",
    [
        "",
        "x\n",
        "a" * 39 + "\n",
        "a" * 41 + "\n",
        "g" * 40 + "\n",
        "A" * 40 + "\n",
        _OLD_MARKER + "b\n",
    ],
    ids=["empty", "word", "short", "long", "not-hex", "upper", "two-lines"],
)
def test_bootstrap_refuses_a_root_whose_marker_is_not_a_commit(
    rig: Rig, tmp_path: Path, marker: str
) -> None:
    # 마커가 「이 스크립트가 만든 곳」의 증거다 — 파일 이름만 맞는 남의 디렉터리는 만든 곳이 아니다
    target = tmp_path / "someone else"
    target.mkdir()
    (target / ".harness-commit").write_text(marker, encoding="utf-8")
    (target / "keep.txt").write_text("x", encoding="utf-8")
    env = {**rig.env, "SWAP_REMOTE_ROOT": str(target), "SWAP_REMOTE_PYTHON": sys.executable}
    done = _bootstrap(env)
    assert done.returncode == 2, done.stderr
    assert (target / "keep.txt").read_text(encoding="utf-8") == "x"
    assert (target / ".harness-commit").read_text(encoding="utf-8") == marker


@posix
def test_bootstrap_reruns_over_its_own_install_and_replaces_the_whole_root(
    rig: Rig, tmp_path: Path
) -> None:
    target = tmp_path / "boot"
    env = {**rig.env, "SWAP_REMOTE_ROOT": str(target), "SWAP_REMOTE_PYTHON": sys.executable}
    assert _bootstrap(env).returncode == 0
    (target / "operator-cache").write_text("x", encoding="utf-8")
    done = _bootstrap(env)
    assert done.returncode == 0, done.stderr
    assert (target / ".harness-commit").read_text(encoding="utf-8").strip() == _head()
    # ⚠ 머리 주석이 적은 한계 — 재실행은 루트를 통째로 바꾼다. 그 아래에 둔 것은 지워진다
    assert not (target / "operator-cache").exists()


@posix
@pytest.mark.parametrize("root", _BAD_ROOTS)
def test_bootstrap_refuses_a_root_that_is_not_a_clean_absolute_path_before_ssh(
    rig: Rig, tmp_path: Path, root: str
) -> None:
    env = {
        **rig.env,
        "SWAP_REMOTE_ROOT": root.replace("{tmp}", str(tmp_path)),
        "SWAP_REMOTE_PYTHON": sys.executable,
    }
    done = _bootstrap(env)
    assert done.returncode == 2, (root, done.stderr)
    assert "SWAP_REMOTE_ROOT" in done.stderr
    assert not rig.argv_log.exists(), "루트가 어긋났는데 ssh를 열었다"


@posix
def test_bootstrap_never_deletes_what_a_relative_root_names_from_the_filesystem_root(
    rig: Rig, tmp_path: Path
) -> None:
    # 고치기 전 결함 — 원격 가드는 로그인 디렉터리 기준으로 보고 삭제는 `cd /` 뒤에 한다. 상대
    # 루트면 둘이 다른 곳을 가리켜서 가드가 한 번도 안 본 디렉터리가 지워졌다. `/` 기준으로
    # victim이 되는 상대 경로를 주면 그 길이 그대로 재현된다(로그인 디렉터리 아래에는 없는 경로).
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious.txt").write_text("x", encoding="utf-8")
    env = {
        **rig.env,
        "SWAP_REMOTE_ROOT": str(victim).lstrip("/"),
        "SWAP_REMOTE_PYTHON": sys.executable,
    }
    done = _bootstrap(env)
    assert (victim / "precious.txt").read_text(encoding="utf-8") == "x", "상대 루트가 지워졌다"
    assert done.returncode == 2, done.stderr


@posix
def test_bootstrap_keeps_an_install_when_the_root_has_a_trailing_slash(
    rig: Rig, tmp_path: Path
) -> None:
    # 고치기 전 결함 — `<root>.incoming`이 `<root>/.incoming`(루트 안)이 되어 삭제가 새 사본까지
    # 치우고 `mv`가 실패했다. 돌아가던 설치본이 사라졌다.
    target = tmp_path / "boot"
    target.mkdir()
    (target / ".harness-commit").write_text(_OLD_MARKER, encoding="utf-8")
    env = {**rig.env, "SWAP_REMOTE_ROOT": f"{target}/", "SWAP_REMOTE_PYTHON": sys.executable}
    done = _bootstrap(env)
    assert done.returncode == 2, done.stderr
    assert (target / ".harness-commit").read_text(encoding="utf-8") == _OLD_MARKER


@posix
def test_every_ssh_call_puts_the_destination_after_an_end_of_options_marker(
    rig: Rig, tmp_path: Path
) -> None:
    # `-`로 시작하는 SWAP_SSH_HOST가 ssh 옵션으로 읽히지 않게 — 세 스크립트 모두
    _report(rig.swap("qwen3-14b"))
    assert rig.ssh_argv()[-3:-1] == ["--", "vast-box"]
    rig.argv_log.unlink()
    root, python = str(tmp_path / "boot"), sys.executable
    assert (
        _bootstrap({**rig.env, "SWAP_REMOTE_ROOT": root, "SWAP_REMOTE_PYTHON": python}).returncode
        == 0
    )
    assert rig.ssh_argv()[-3:-1] == ["--", "vast-box"]
    rig.argv_log.unlink()
    # 가짜 ssh가 호스트를 명령으로 돌려 127로 끝난다 — 여기서 보는 것은 argv뿐이다
    subprocess.run(
        ["bash", TUNNEL.as_posix()],
        cwd=ROOT,
        env=rig.env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert rig.ssh_argv()[-2:] == ["--", "vast-box"]


# --- 장비 파일 · 터널 ----------------------------------------------------------------------


def test_the_remote_device_file_differs_from_vastai_only_in_the_command() -> None:
    local, remote = load_serving("vastai"), load_serving("vastai-remote")
    assert remote.servers == local.servers
    assert local.command == ("bash", "tools/serve/swap.sh")
    assert remote.command == ("bash", REMOTE.as_posix())


def test_the_tunnel_forwards_exactly_the_ports_the_remote_device_serves() -> None:
    text = (ROOT / TUNNEL).read_text(encoding="utf-8")
    found = re.search(r"^PORTS=\(([\d ]+)\)$", text, re.M)
    assert found, "vast-tunnel.sh의 PORTS 줄을 못 찾았다"
    tunneled = {int(p) for p in found.group(1).split()}
    models = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))["models"]
    keys = load_serving("vastai-remote").servers
    served = {urlparse(models[key]["endpoint"]).port for key in keys}
    assert tunneled == served
