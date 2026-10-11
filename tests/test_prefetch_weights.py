"""`tools/serve/prefetch-weights.sh` — 원격에 미리 받을 (repo, rev) 쌍이 registry와 어긋나지 않는가.

선취는 `ready_timeout_s`의 전제다(#571). 대상 목록을 registry에서 읽으므로 모델이 늘거나
rev가 바뀌어도 스크립트는 안 고친다 — 여기서는 registry의 `hf://` 소스가 하나도 빠지지
않고 40자 rev로 나오는지, `file://`은 건너뛰는지, `--roles`가 좌석을 고르는지 본다.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.config import CONFIG_DIR, load_config

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "serve"))
import prefetch_weights  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = Path("tools/serve/prefetch-weights.sh")
PROFILE = "harness_pool"
_BASH = shutil.which("bash")
_HF = re.compile(r"^hf://(?P<repo>[^@]+)@(?P<rev>.+)$")


def _registry_hf_pairs(config_dir: Path = CONFIG_DIR) -> set[tuple[str, str]]:
    """프로필이 쓰는 모델 중 `hf://` 소스 전부 — 검사 대상의 정의를 이 테스트가 따로 든다."""
    config = load_config(PROFILE, config_dir)
    pairs = set()
    for key in set(config.profile.roles.values()):
        source = config.models[key].source
        found = _HF.match(source or "")
        if found:
            pairs.add((found["repo"], found["rev"]))
    return pairs


def test_every_hf_model_the_profile_uses_comes_out_as_a_repo_rev_pair() -> None:
    expected = _registry_hf_pairs()
    assert len(expected) >= 7  # 이 레지스트리의 실물 — 비어 있으면 아래 단언이 허공이다
    got = prefetch_weights.targets(PROFILE, "all")
    assert set(got) == expected
    assert len(got) == len(set(got)), "같은 (repo, rev)를 두 번 낸다"
    for repo, rev in got:
        assert re.fullmatch(r"[0-9a-f]{40}", rev), (repo, rev)


def test_roles_pick_the_seat_and_pov_is_the_feyospace_repo() -> None:
    pov = prefetch_weights.targets(PROFILE, "pov")
    assert [repo for repo, _ in pov] == ["feyospace/feyospace-v1-small"]
    both = prefetch_weights.targets(PROFILE, "auditor,critic")
    assert len(both) == 2 and set(both) < set(prefetch_weights.targets(PROFILE, "all"))


def test_file_sources_and_models_without_a_source_are_skipped(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config_dir)
    models = config_dir / "models.yaml"
    text = models.read_text(encoding="utf-8")
    original = (
        'source: "hf://feyospace/feyospace-v1-small@fd02711ea1eb8ea15772d0b7fbded18ecfe8c9d1"'
    )
    assert original in text
    models.write_text(
        text.replace(original, 'source: "file:///srv/weights/feyospace-v1-small"'),
        encoding="utf-8",
        newline="\n",
    )
    assert prefetch_weights.targets(PROFILE, "pov", config_dir) == []
    assert all(
        repo != "feyospace/feyospace-v1-small"
        for repo, _ in prefetch_weights.targets(PROFILE, "all", config_dir)
    )
    # 소스가 없는 fake 모델 좌석(dev_fake)도 에러 없이 빈 목록이다
    assert prefetch_weights.targets("dev_fake", "all") == []


def test_an_unknown_role_is_refused() -> None:
    with pytest.raises(ValueError, match="nope"):
        prefetch_weights.targets(PROFILE, "nope")


_PIP = "pip install -q hf_transfer"
_DOWNLOAD = re.compile(r"HF_HUB_ENABLE_HF_TRANSFER=1 hf download (\S+) --revision ([0-9a-f]{40})")
needs_bash = pytest.mark.skipif(_BASH is None, reason="bash가 없다")

# 진짜 ssh처럼 -n이 없으면 stdin을 먹는다 - 스크립트는 here-string으로 반복을 돌려
# -n이 없으면 첫 호출이 나머지를 삼킨다
_STUB_SSH = """#!/bin/sh
n=$(cat "$STUB_DIR/calls" 2> /dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$STUB_DIR/calls"
printf '%s\\n' "$@" > "$STUB_DIR/argv.$n"
case " $* " in *" -n "*) ;; *) cat > /dev/null ;; esac
"""


def _run_script(
    *args: str, tmp_path: Path | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    path = str(Path(_BASH).parent)
    if tmp_path is not None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        ssh = bin_dir / "ssh"
        ssh.write_text(_STUB_SSH, encoding="utf-8", newline="\n")
        ssh.chmod(0o755)
        path = f"{bin_dir}{os.pathsep}{path}"
        env["STUB_DIR"] = str(tmp_path)
    return subprocess.run(
        [_BASH, SCRIPT.as_posix(), *args],
        cwd=ROOT,
        env={"PATH": path, "PREFETCH_PYTHON": sys.executable, **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


@needs_bash
def test_dry_run_prints_pip_once_then_one_download_per_pair_without_ssh() -> None:
    done = _run_script("--dry-run", "--profile", PROFILE, "--roles", "all")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[0] == _PIP
    printed = set()
    for line in lines[1:]:
        found = _DOWNLOAD.fullmatch(line)
        assert found, line
        printed.add((found[1], found[2]))
    assert printed == _registry_hf_pairs()
    assert len(lines) == 1 + len(printed)


@needs_bash
def test_a_real_run_calls_ssh_once_for_pip_and_once_per_model_with_the_keepalive_options(
    tmp_path: Path,
) -> None:
    done = _run_script(
        "--roles", "all", tmp_path=tmp_path, SWAP_SSH_HOST="root@203.0.113.9", SWAP_SSH_PORT="40123"
    )
    assert done.returncode == 0, done.stderr
    expected = _registry_hf_pairs()
    calls = int((tmp_path / "calls").read_text())
    assert calls == 1 + len(expected)  # -n이 빠지면 첫 ssh가 반복의 입력을 먹어 여기서 모자란다
    argvs = [
        (tmp_path / f"argv.{n}").read_text(encoding="utf-8").splitlines()
        for n in range(1, calls + 1)
    ]
    assert [a[-1] for a in argvs].count(_PIP) == 1
    seen = set()
    for argv in argvs:
        before = argv[: argv.index("--")]
        assert argv[argv.index("--") + 1] == "root@203.0.113.9"
        assert before[before.index("-p") + 1] == "40123"
        assert "-n" in before
        options = dict(
            v.split("=", 1) for f, v in zip(before, before[1:], strict=False) if f == "-o"
        )
        assert int(options["ServerAliveInterval"]) * int(options["ServerAliveCountMax"]) > 30
        assert options["TCPKeepAlive"] == "yes"
        found = _DOWNLOAD.fullmatch(argv[-1])
        if found:
            seen.add((found[1], found[2]))
    assert seen == expected


@needs_bash
def test_without_dry_run_a_missing_host_stops_before_anything_runs() -> None:
    done = _run_script()
    assert done.returncode == 2
    assert "SWAP_SSH_HOST" in done.stderr


@pytest.mark.parametrize("rev", ["main", "abc123", "a" * 41, "A" * 40])
def test_a_source_whose_rev_is_not_a_full_commit_hash_is_refused(tmp_path: Path, rev: str) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config_dir)
    models = config_dir / "models.yaml"
    text = models.read_text(encoding="utf-8")
    original = "hf://feyospace/feyospace-v1-small@fd02711ea1eb8ea15772d0b7fbded18ecfe8c9d1"
    assert original in text
    models.write_text(
        text.replace(original, f"hf://feyospace/feyospace-v1-small@{rev}"),
        encoding="utf-8",
        newline="\n",
    )
    assert prefetch_weights.main(["--roles", "pov", "--config-dir", str(config_dir)]) == 2


@pytest.mark.parametrize("roles", ["", ",", " "])
def test_an_empty_roles_list_is_refused_not_a_silent_no_op(roles: str) -> None:
    assert prefetch_weights.main(["--roles", roles]) == 2
