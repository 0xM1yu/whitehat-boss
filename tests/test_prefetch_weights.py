"""`tools/serve/prefetch-weights.sh` — 원격에 미리 받을 (repo, rev) 쌍이 registry와 어긋나지 않는가.

선취는 `ready_timeout_s`의 전제다(#571). 대상 목록을 registry에서 읽으므로 모델이 늘거나
rev가 바뀌어도 스크립트는 안 고친다 — 여기서는 registry의 `hf://` 소스가 하나도 빠지지
않고 40자 rev로 나오는지, `file://`은 건너뛰는지, `--roles`가 좌석을 고르는지 본다.
"""

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


@pytest.mark.skipif(_BASH is None, reason="bash가 없다")
def test_dry_run_prints_the_remote_command_per_pair_without_ssh() -> None:
    done = subprocess.run(
        [_BASH, SCRIPT.as_posix(), "--dry-run", "--profile", PROFILE, "--roles", "all"],
        cwd=ROOT,
        env={"PATH": str(Path(_BASH).parent), "PREFETCH_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    printed = set()
    for line in done.stdout.splitlines():
        found = re.fullmatch(
            r"pip install -q hf_transfer && HF_HUB_ENABLE_HF_TRANSFER=1 "
            r"hf download (\S+) --revision ([0-9a-f]{40})",
            line,
        )
        assert found, line
        printed.add((found[1], found[2]))
    assert printed == _registry_hf_pairs()


@pytest.mark.skipif(_BASH is None, reason="bash가 없다")
def test_without_dry_run_a_missing_host_stops_before_anything_runs() -> None:
    done = subprocess.run(
        [_BASH, SCRIPT.as_posix()],
        cwd=ROOT,
        env={"PATH": str(Path(_BASH).parent), "PREFETCH_PYTHON": sys.executable},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert done.returncode == 2
    assert "SWAP_SSH_HOST" in done.stderr
