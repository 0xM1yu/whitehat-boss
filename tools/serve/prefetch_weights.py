"""가중치 선취 대상 — 프로필 역할 모델의 `source: hf://<repo>@<rev>`를 `<repo> <rev>` 줄로 낸다.

`prefetch-weights.sh`가 부른다. `file://`·`null` 소스는 건너뛴다(내려받을 곳이 없다). LoRA 어댑터
좌석(`lora_base` 있음)은 `source`(file:// 어댑터) 대신 `lora_base`의 base 가중치를 선취한다.
같은 (repo, rev)는 한 번만 낸다. `--total-bytes`는 같은 대상의 받을 크기 합(바이트)을 HF API로 낸다.
"""

import argparse
import re
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import CONFIG_DIR, ConfigError, load_config  # noqa: E402

_HF = re.compile(r"^hf://(?P<repo>[\w.-]+/[\w.-]+)@(?P<rev>[0-9a-f]{40})$")

# `seats`(기본) = 프로필의 역할에서 `embedding`·`reranker`를 뺀 것.
# `vastai-remote`는 그 둘을 서빙하지 않는다.
DEFAULT_ROLES = "seats"
_NOT_SEATS = ("embedding", "reranker")
# 받지 않는 접두어 — `prefetch-weights.sh`의 `--exclude`와 같아야 한다(tests가 맞물려 잠근다).
# 같은 가중치를 다른 형식으로 또 든 폴더다.
EXCLUDED_PREFIXES = ("original/", "metal/")


def targets(
    profile: str, roles: str = DEFAULT_ROLES, config_dir: Path = CONFIG_DIR
) -> list[tuple[str, str]]:
    """`roles`(쉼표 목록 · `seats` · `all`)가 쓰는 모델의 `(repo, rev)`. 순서는 프로필 역할 순서."""
    config = load_config(profile, config_dir)
    mapping = config.profile.roles
    if roles == "all":
        wanted = list(mapping)
    elif roles == "seats":
        wanted = [r for r in mapping if r not in _NOT_SEATS]
    else:
        wanted = [r for r in roles.split(",") if r]
    if not wanted:
        raise ValueError(
            f"--roles가 비었다 - 역할 이름을 쉼표로 주거나 seats·all (있는 것: {sorted(mapping)})"
        )
    unknown = [r for r in wanted if r not in mapping]
    if unknown:
        raise ValueError(f"프로필 {profile!r}에 없는 역할 {unknown} — 있는 것: {sorted(mapping)}")
    models = config.models
    out: list[tuple[str, str]] = []
    for role in wanted:
        entry = models[mapping[role]]
        # LoRA 어댑터 좌석(`lora_base` 있음)은 선취 대상이 base 가중치다(`lora_base`의 hf://repo@rev).
        # 어댑터(304MB)는 로컬에 있으면 쓰고 없으면 기동 때 swap.sh가 `lora_adapter_hf`에서 받는다
        # (`ensure_adapter`) — 선취 목록엔 안 넣는다.
        source = entry.lora_base or entry.source
        if source is None or source.startswith("file://"):
            continue
        found = _HF.match(source)
        if found is None:
            raise ValueError(f"역할 {role!r}의 source {source!r}이 hf://<repo>@<40자 hex>가 아니다")
        pair = (found["repo"], found["rev"])
        if pair not in out:
            out.append(pair)
    return out


def _hf_tree(repo: str, rev: str) -> Iterable[tuple[str, int]]:
    """고정 rev의 파일 `(경로, 바이트)` 전부 — 폴더 항목은 크기가 없어 건너뛴다."""
    from huggingface_hub import HfApi  # 선취가 아닌 경로(목록만)는 네트워크도 이 패키지도 필요 없다

    for entry in HfApi().list_repo_tree(repo, revision=rev, recursive=True):
        size = getattr(entry, "size", None)
        if size is not None:
            yield entry.path, size


def total_bytes(
    pairs: Iterable[tuple[str, str]],
    tree: Callable[[str, str], Iterable[tuple[str, int]]] | None = None,
) -> int:
    """`pairs`를 `EXCLUDED_PREFIXES` 빼고 받을 때의 크기 합. 받아 둔 몫은 안 뺀다(상한)."""
    listing = tree or _hf_tree
    return sum(
        size
        for repo, rev in pairs
        for path, size in listing(repo, rev)
        if not path.startswith(EXCLUDED_PREFIXES)
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="harness_pool")
    parser.add_argument("--roles", default=DEFAULT_ROLES)
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--total-bytes", action="store_true", help="목록 대신 받을 크기 합을 낸다")
    args = parser.parse_args(argv)
    try:
        pairs = targets(args.profile, args.roles, args.config_dir)
        if args.total_bytes:
            print(total_bytes(pairs))
            return 0
    except (ConfigError, ValueError) as err:
        print(f"prefetch-weights: {err}", file=sys.stderr)
        return 2
    except Exception as err:  # noqa: BLE001 - 네트워크·HF 오류의 종류가 많다. 사유를 찍고 멈춘다
        print(f"prefetch-weights: 크기를 못 받았다 - {type(err).__name__}: {err}", file=sys.stderr)
        return 2
    for repo, rev in pairs:
        print(repo, rev)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
