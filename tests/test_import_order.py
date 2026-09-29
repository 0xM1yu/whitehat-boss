"""모든 `src` 모듈이 새 인터프리터의 **첫 import**로 불러진다 — 순환 import의 성질 검사.

순환은 import 순서에 달려 있어 한 프로세스 안의 테스트로는 안 보인다 — 앞의 무엇이 한쪽을
먼저 불러 두면 초록이다. 2026-09-29 PR #332가 그것을 들였다: `src.bench` →
`src.adapters.openai_compat` → 패키지 `__init__` → `factory` → `model_swap` → `src.bench`가
반쯤 초기화된 채 돌아와, `import src.bench`가 첫 import이면 `ImportError`로 죽었다. cli·probe·
테스트가 전부 어댑터를 먼저 import해서 아무도 못 봤다.

한 모듈만 핀하면 다음 순환을 못 잡는다 — 추적되는 `src/**/*.py` 전부를 **모듈마다 새
인터프리터로** 부른다. 병렬로 돈다 — 73개가 순차 ≈37초, 8병렬 ≈5초였다(2026-09-29 이 저장소,
Windows · CPython 3.14).
"""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _modules() -> list[str]:
    """`git ls-files`가 내놓는 `src` 모듈 이름. 워킹트리의 미추적 파일은 보지 않는다."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "src/*.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:  # pragma: no cover - git 없는 환경
        pytest.skip("git을 실행할 수 없다")
    if listed.returncode != 0:  # pragma: no cover - 저장소 밖
        pytest.skip("저장소가 아니다")
    paths = [path for path in listed.stdout.split("\0") if path]
    return sorted(
        {path.removesuffix(".py").removesuffix("/__init__").replace("/", ".") for path in paths}
    )


def _first_import(module: str) -> str | None:
    """새 인터프리터에서 `module`을 첫 import로 부른다. 실패면 그 사유 한 줄, 성공이면 `None`."""
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    if done.returncode == 0:
        return None
    last = done.stderr.strip().splitlines()[-1:] or [f"exit {done.returncode}"]
    return f"{module}: {last[0]}"


def test_every_src_module_imports_first_in_a_fresh_interpreter() -> None:
    modules = _modules()
    # 목록이 잘못 모이면(빈 목록 · 경로 변환 실수) 검사가 아무것도 안 부르고 초록이 된다.
    assert {"src.bench", "src.adapters.model_swap", "src.cli", "src.orchestrator.runner"} <= set(
        modules
    ), f"모듈 목록이 잘못 모였다: {modules[:5]}…"

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        failures = [failure for failure in pool.map(_first_import, modules) if failure]

    assert failures == [], "첫 import로 못 부르는 모듈(순환 import 의심):\n" + "\n".join(failures)
