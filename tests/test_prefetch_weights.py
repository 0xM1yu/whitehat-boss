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
from typing import Any

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
_HFV = "hf version"
_DF = (
    'd=${HF_HOME:-$HOME/.cache/huggingface}; mkdir -p "$d"'
    ' && df -B1 --output=avail "$d" | tail -n 1'
)
_DOWNLOAD = re.compile(
    r"HF_HUB_ENABLE_HF_TRANSFER=1 HF_XET_HIGH_PERFORMANCE=1"
    r" hf download (\S+) --revision ([0-9a-f]{40})"
    r" --exclude 'original/\*' --exclude 'metal/\*'"
)
_SEATS = "scout_memory,scout_boundary,scout_injection,auditor,critic,pov"
_HEX_A = "a" * 40
_HEX_B = "b" * 40
needs_bash = pytest.mark.skipif(_BASH is None, reason="bash가 없다")

# 진짜 ssh처럼 -n이 없으면 stdin을 먹는다 - 스크립트는 here-string으로 반복을 돌려
# -n이 없으면 첫 호출이 나머지를 삼킨다. df 호출에는 FREE_BYTES로 답한다
_STUB_SSH = """#!/bin/sh
n=$(cat "$STUB_DIR/calls" 2> /dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$STUB_DIR/calls"
printf '%s\\n' "$@" > "$STUB_DIR/argv.$n"
case " $* " in *" -n "*) ;; *) cat > /dev/null ;; esac
case "$*" in *"df -B1"*) echo "${FREE_BYTES:-999999999999999}" ;; esac
[ "$n" = "${FAIL_AT:-0}" ] && exit 1
exit 0
"""

# 진짜 파이썬을 감싸되 `--total-bytes`(HF API)만 가짜로 답한다 - 호출 인자는 pyargs에 남긴다
_STUB_PY = """#!/bin/sh
echo "$*" >> "$STUB_DIR/pyargs"
for a in "$@"; do
  [ "$a" = --total-bytes ] && { echo "${NEED_BYTES:-1000}"; exit 0; }
done
exec "$REAL_PY" "$@"
"""

# Windows 파이썬의 `print`처럼 줄을 `\r\n`으로 낸다 - 어느 OS에서나 같은 입력을 만든다
_CRLF_PY = f"""#!/bin/sh
case "$*" in *--total-bytes*) echo 1000; exit 0 ;; esac
printf 'org/one {_HEX_A}\\r\\norg/two {_HEX_B}\\r\\n'
"""


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def _run_script(
    *args: str, tmp_path: Path | None = None, text: bool = True, **env: str
) -> subprocess.CompletedProcess[Any]:
    """`text=False`는 바이트 그대로다 - `text=True`는 `\\r\\n`을 지운다."""
    path = str(Path(_BASH).parent)
    python = sys.executable
    if tmp_path is not None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _executable(bin_dir / "ssh", _STUB_SSH)
        python = _executable(bin_dir / "py", _STUB_PY).as_posix()
        path = f"{bin_dir}{os.pathsep}{path}"
        env["STUB_DIR"] = str(tmp_path)
        env["REAL_PY"] = Path(sys.executable).as_posix()
    text_kwargs = {"text": True, "encoding": "utf-8", "errors": "replace"} if text else {}
    return subprocess.run(
        [_BASH, SCRIPT.as_posix(), *args],
        cwd=ROOT,
        env={"PATH": path, "PREFETCH_PYTHON": python, **env},
        capture_output=True,
        timeout=120,
        **text_kwargs,
    )


def _lines(raw: bytes) -> list[str]:
    """`\\n`으로만 자른다 - `str.splitlines()`는 `\\r`도 줄 끝으로 읽어 CR을 숨긴다."""
    assert b"\r" not in raw, raw
    return raw.decode("utf-8").split("\n")[:-1]


def _argv(tmp_path: Path, n: int) -> list[str]:
    return _lines((tmp_path / f"argv.{n}").read_bytes())


@needs_bash
def test_dry_run_prints_setup_once_then_one_download_per_pair_without_ssh() -> None:
    done = _run_script("--dry-run", "--profile", PROFILE, "--roles", "all", text=False)
    assert done.returncode == 0, done.stderr
    lines = _lines(done.stdout)
    assert lines[:3] == [_PIP, _HFV, _DF]
    printed = set()
    for line in lines[3:]:
        found = _DOWNLOAD.fullmatch(line)
        assert found, line
        printed.add((found[1], found[2]))
    assert printed == _registry_hf_pairs()
    assert len(lines) == 3 + len(printed)


@needs_bash
def test_the_dry_run_exclude_flags_are_the_prefixes_the_size_sum_skips() -> None:
    done = _run_script("--dry-run", "--roles", "pov")
    assert done.returncode == 0, done.stderr
    download = done.stdout.splitlines()[-1]
    assert prefetch_weights.EXCLUDED_PREFIXES == ("original/", "metal/")
    for prefix in prefetch_weights.EXCLUDED_PREFIXES:
        assert f"--exclude '{prefix}*'" in download


@needs_bash
def test_a_real_run_calls_ssh_for_setup_and_once_per_model_with_the_keepalive_options(
    tmp_path: Path,
) -> None:
    done = _run_script(
        "--roles", "all", tmp_path=tmp_path, SWAP_SSH_HOST="root@203.0.113.9", SWAP_SSH_PORT="40123"
    )
    assert done.returncode == 0, done.stderr
    expected = _registry_hf_pairs()
    calls = int((tmp_path / "calls").read_text())
    assert calls == 3 + len(expected)  # -n이 빠지면 첫 ssh가 반복의 입력을 먹어 여기서 모자란다
    argvs = [
        _argv(tmp_path, n) for n in range(1, calls + 1)
    ]  # 바이트로 읽는다 - CR이 있으면 여기서 죽는다
    assert [a[-1] for a in argvs[:3]] == [_PIP, _HFV, _DF]
    seen = set()
    for argv in argvs:
        before = argv[: argv.index("--")]
        assert argv[argv.index("--") + 1] == "root@203.0.113.9"
        assert before[before.index("-p") + 1] == "40123"
        assert "-n" in before
        assert "-T" in before  # 비대화형 — 암호 프롬프트에 매달리는 회귀를 막는다
        options = dict(
            v.split("=", 1) for f, v in zip(before, before[1:], strict=False) if f == "-o"
        )
        assert int(options["ServerAliveInterval"]) * int(options["ServerAliveCountMax"]) > 30
        assert options["TCPKeepAlive"] == "yes"
        assert options["BatchMode"] == "yes"
        found = _DOWNLOAD.fullmatch(argv[-1])
        if found:
            seen.add((found[1], found[2]))
    assert seen == expected


# --- Windows 파이썬의 `\r\n` — `$(...)`는 끝 줄바꿈만 지워 rev에 `\r`이 붙었다 --------------


@needs_bash
def test_dry_run_stdout_has_no_carriage_return_with_the_real_python() -> None:
    done = _run_script("--dry-run", "--roles", "all", text=False)
    assert done.returncode == 0, done.stderr
    assert b"\r" not in done.stdout


@needs_bash
def test_dry_run_has_no_carriage_return_when_python_prints_crlf(tmp_path: Path) -> None:
    py = _executable(tmp_path / "crlf-py", _CRLF_PY)
    done = _run_script("--dry-run", PREFETCH_PYTHON=py.as_posix(), text=False)
    assert done.returncode == 0, done.stderr
    lines = _lines(done.stdout)
    assert [_DOWNLOAD.fullmatch(line)[2] for line in lines[3:]] == [_HEX_A, _HEX_B]


@needs_bash
def test_ssh_argv_has_no_carriage_return_when_python_prints_crlf(tmp_path: Path) -> None:
    py = _executable(tmp_path / "crlf-py", _CRLF_PY)
    done = _run_script(
        tmp_path=tmp_path, SWAP_SSH_HOST="root@203.0.113.9", PREFETCH_PYTHON=py.as_posix()
    )
    assert done.returncode == 0, done.stderr
    assert int((tmp_path / "calls").read_text()) == 5
    revs = [_DOWNLOAD.fullmatch(_argv(tmp_path, n)[-1])[2] for n in (4, 5)]
    assert revs == [_HEX_A, _HEX_B]


@needs_bash
@pytest.mark.parametrize(
    "rev", ["main", "abc123", "A" * 40, "a" * 41, f"{_HEX_A} extra"], ids=lambda r: r[:12]
)
def test_a_rev_that_is_not_a_full_hash_stops_before_any_ssh_call(tmp_path: Path, rev: str) -> None:
    bad = _executable(
        tmp_path / "bad-py",
        f'#!/bin/sh\ncase "$*" in *--total-bytes*) echo 1000; exit 0 ;; esac\n'
        f"printf 'org/one {rev}\\n'\n",
    )
    done = _run_script(
        tmp_path=tmp_path, SWAP_SSH_HOST="root@203.0.113.9", PREFETCH_PYTHON=bad.as_posix()
    )
    assert done.returncode == 2, done.stderr
    assert "40" in done.stderr and "org/one" in done.stderr
    assert not (tmp_path / "calls").exists()


# --- 크기: original/·metal/ 제외, 받기 전 원격 여유와 비교 ----------------------------------


def test_total_bytes_counts_every_file_except_root_original_and_metal() -> None:
    tree = {
        ("org/one", _HEX_A): [
            ("model.safetensors", 10),
            ("config.json", 1),
            ("original/model.safetensors", 100),
            ("metal/model.bin", 1000),
            ("sub/original/x", 5),  # 접두어는 저장소 루트에서만 - `--exclude 'original/*'`와 같다
        ],
        ("org/two", _HEX_B): [("w.safetensors", 7)],
    }
    got = prefetch_weights.total_bytes(list(tree), tree=lambda repo, rev: tree[(repo, rev)])
    assert got == 10 + 1 + 5 + 7


def test_a_failing_size_lookup_exits_2_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(repo: str, rev: str) -> list[tuple[str, int]]:
        raise OSError("network is down")

    monkeypatch.setattr(prefetch_weights, "_hf_tree", boom)
    assert prefetch_weights.main(["--roles", "pov", "--total-bytes"]) == 2
    assert "network is down" in capsys.readouterr().err


@needs_bash
def test_the_size_sum_is_asked_for_the_same_roles_as_the_download_list(tmp_path: Path) -> None:
    done = _run_script("--roles", "pov", tmp_path=tmp_path, SWAP_SSH_HOST="h")
    assert done.returncode == 0, done.stderr
    calls = (tmp_path / "pyargs").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
    sized = [c for c in calls if "--total-bytes" in c]
    assert len(sized) == 1 and "--roles pov" in sized[0] and f"--profile {PROFILE}" in sized[0]
    assert int((tmp_path / "calls").read_text()) == 4  # pip · hf version · df · 내려받기 1


@needs_bash
def test_too_little_free_space_stops_before_any_download(tmp_path: Path) -> None:
    done = _run_script(
        "--roles", "pov", tmp_path=tmp_path, SWAP_SSH_HOST="h", FREE_BYTES="100", NEED_BYTES="1000"
    )
    assert done.returncode == 2
    assert "--skip-df-check" in done.stderr and "100" in done.stderr and "1000" in done.stderr
    assert (tmp_path / "calls").read_text().strip() == "3"  # pip · hf version · df - 내려받기 없다


@needs_bash
def test_a_df_answer_that_is_not_a_number_stops_before_any_download(tmp_path: Path) -> None:
    done = _run_script("--roles", "pov", tmp_path=tmp_path, SWAP_SSH_HOST="h", FREE_BYTES="Avail")
    assert done.returncode == 2
    assert (tmp_path / "calls").read_text().strip() == "3"


@needs_bash
def test_skip_df_check_neither_asks_the_size_nor_calls_df(tmp_path: Path) -> None:
    done = _run_script(
        "--roles",
        "pov",
        "--skip-df-check",
        tmp_path=tmp_path,
        SWAP_SSH_HOST="h",
        FREE_BYTES="1",
        NEED_BYTES="1000",
    )
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "calls").read_text().strip() == "3"  # pip · hf version · 내려받기 1
    assert all(_DF not in _argv(tmp_path, n) for n in (1, 2, 3))
    assert all("--total-bytes" not in c for c in (tmp_path / "pyargs").read_text().splitlines())


# --- 기본 roles: 좌석 -----------------------------------------------------------------------


def test_default_roles_are_the_serving_seats_not_embedding_and_reranker() -> None:
    seats = set(prefetch_weights.targets(PROFILE, _SEATS))
    assert set(prefetch_weights.targets(PROFILE, prefetch_weights.DEFAULT_ROLES)) == seats
    everything = set(prefetch_weights.targets(PROFILE, "all"))
    assert seats < everything
    assert everything - seats == set(prefetch_weights.targets(PROFILE, "embedding,reranker"))


@needs_bash
def test_without_roles_the_script_prints_only_the_seat_downloads() -> None:
    done = _run_script("--dry-run")
    assert done.returncode == 0, done.stderr
    printed = {(f[1], f[2]) for f in map(_DOWNLOAD.fullmatch, done.stdout.splitlines()[3:])}
    assert printed == set(prefetch_weights.targets(PROFILE, _SEATS))


@needs_bash
def test_roles_all_still_prints_every_model_when_asked() -> None:
    done = _run_script("--dry-run", "--roles", "all")
    printed = {(f[1], f[2]) for f in map(_DOWNLOAD.fullmatch, done.stdout.splitlines()[3:])}
    assert printed == _registry_hf_pairs()


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


# --- 스크립트 수준: 실패·인자 -------------------------------------------------------------


@needs_bash
def test_a_failing_pip_step_stops_the_script_after_one_ssh_call(tmp_path: Path) -> None:
    done = _run_script(
        "--roles", "all", tmp_path=tmp_path, SWAP_SSH_HOST="root@203.0.113.9", FAIL_AT="1"
    )
    assert done.returncode != 0
    assert (tmp_path / "calls").read_text().strip() == "1"


@needs_bash
def test_a_failing_download_stops_the_script_without_further_ssh_calls(tmp_path: Path) -> None:
    done = _run_script(
        "--roles", "all", tmp_path=tmp_path, SWAP_SSH_HOST="root@203.0.113.9", FAIL_AT="5"
    )
    assert done.returncode != 0
    assert (
        tmp_path / "calls"
    ).read_text().strip() == "5"  # 준비 3 + 내려받기 2(실패) - 더 부르지 않는다


@needs_bash
def test_an_unknown_role_exits_2_through_the_script() -> None:
    done = _run_script("--dry-run", "--roles", "nope")
    assert done.returncode == 2
    assert "nope" in done.stderr
    assert done.stdout == ""


@needs_bash
def test_an_empty_roles_value_exits_2_with_a_message_naming_the_option() -> None:
    done = _run_script("--dry-run", "--roles", "")
    assert done.returncode == 2
    assert "--roles" in done.stderr and "line" not in done.stderr


@needs_bash
def test_a_roles_option_without_a_value_exits_2() -> None:
    done = _run_script("--roles")
    assert done.returncode == 2
    assert "--roles" in done.stderr


@needs_bash
def test_roles_pov_prints_exactly_the_feyospace_pair() -> None:
    done = _run_script("--dry-run", "--roles", "pov")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert lines[:3] == [_PIP, _HFV, _DF]
    assert len(lines) == 4
    found = _DOWNLOAD.fullmatch(lines[3])
    assert found and found[1] == "feyospace/feyospace-v1-small"


@needs_bash
def test_a_profile_without_hf_sources_prints_nothing_and_exits_0() -> None:
    done = _run_script("--dry-run", "--profile", "dev_fake")
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""


@needs_bash
def test_a_python_failure_propagates_and_no_ssh_is_called(tmp_path: Path) -> None:
    bad = tmp_path / "badpython"
    bad.write_text("#!/bin/sh\necho boom >&2\nexit 3\n", encoding="utf-8", newline="\n")
    bad.chmod(0o755)
    done = _run_script(
        "--roles", "all", tmp_path=tmp_path, SWAP_SSH_HOST="h", PREFETCH_PYTHON=bad.as_posix()
    )
    assert done.returncode == 3, done.stderr
    assert not (tmp_path / "calls").exists()
