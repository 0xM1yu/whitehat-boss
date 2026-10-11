"""가중치 선취 대상 — 프로필 역할 모델의 `source: hf://<repo>@<rev>`를 `<repo> <rev>` 줄로 낸다.

`prefetch-weights.sh`가 부른다. `file://`·`null` 소스는 건너뛴다(내려받을 곳이 없다).
같은 (repo, rev)는 한 번만 낸다.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import CONFIG_DIR, ConfigError, load_config  # noqa: E402

_HF = re.compile(r"^hf://(?P<repo>[\w.-]+/[\w.-]+)@(?P<rev>[0-9a-f]{40})$")


def targets(
    profile: str, roles: str = "all", config_dir: Path = CONFIG_DIR
) -> list[tuple[str, str]]:
    """`roles`(쉼표 목록 또는 `all`)가 쓰는 모델의 `(repo, rev)`. 순서는 프로필 역할 순서."""
    config = load_config(profile, config_dir)
    mapping = config.profile.roles
    wanted = list(mapping) if roles == "all" else [r for r in roles.split(",") if r]
    if not wanted:
        raise ValueError(
            f"--roles가 비었다 - 역할 이름을 쉼표로 주거나 all (있는 것: {sorted(mapping)})"
        )
    unknown = [r for r in wanted if r not in mapping]
    if unknown:
        raise ValueError(f"프로필 {profile!r}에 없는 역할 {unknown} — 있는 것: {sorted(mapping)}")
    models = config.models
    out: list[tuple[str, str]] = []
    for role in wanted:
        source = models[mapping[role]].source
        if source is None or source.startswith("file://"):
            continue
        found = _HF.match(source)
        if found is None:
            raise ValueError(f"역할 {role!r}의 source {source!r}이 hf://<repo>@<40자 hex>가 아니다")
        pair = (found["repo"], found["rev"])
        if pair not in out:
            out.append(pair)
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="harness_pool")
    parser.add_argument("--roles", default="all")
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    args = parser.parse_args(argv)
    try:
        pairs = targets(args.profile, args.roles, args.config_dir)
    except (ConfigError, ValueError) as err:
        print(f"prefetch-weights: {err}", file=sys.stderr)
        return 2
    for repo, rev in pairs:
        print(repo, rev)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
