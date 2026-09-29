"""`run`이 신호로 끝날 때 — run 끝 내림(러너의 `finally`)이 도는가.

ADR 0009 「2026-09-29 제안」 절 4의 run 끝 내림이다. 교대가 띄운 서버는 하네스와 다른
세션에서 돈다 — 하네스 프로세스 그룹에 온 TERM으로 같이 죽지 않는다. 파이썬 기본 동작은
TERM·HUP에 `finally`도 `atexit`도 안 거치고 죽으므로, 그대로 두면 `timeout`·`kill`·세션
끊김으로 끝난 run의 서버가 `HARNESS_RUN_ID`째 남는다.
"""

import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
# 이 플랫폼에 있는 종료 신호 — Windows에는 SIGHUP이 없다.
SIGNALS = [name for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)]

# 자식 프로세스: `cli.execute`를 러너의 `try: … finally: run 끝 내림` 모양으로 바꿔 끼우고
# `cli.main(["run", …])`을 돈다. 서버가 떠 있는 동안 신호가 오고, 내림 도중에 같은 신호가
# `repeat - 1`번 더 온다(감독자의 재촉). 내림이 끝까지 돌면 표지 파일을 남긴다.
_CHILD = textwrap.dedent(
    """
    import signal, sys
    from pathlib import Path
    from src import cli

    marker, signum, repeat = Path(sys.argv[1]), getattr(signal, sys.argv[2]), int(sys.argv[3])

    def execute(*_args, **_kwargs):
        try:
            signal.raise_signal(signum)
        finally:
            for _ in range(repeat - 1):
                signal.raise_signal(signum)
            marker.write_text("run 끝 내림이 끝까지 돌았다", encoding="utf-8")

    cli.execute = execute
    raise SystemExit(cli.main(sys.argv[4:]))
    """
)


@pytest.mark.parametrize("repeat", [1, 2], ids=["once", "again-during-teardown"])
@pytest.mark.parametrize("name", SIGNALS)
def test_a_run_ended_by_a_signal_still_runs_its_teardown(
    tmp_path: Path, name: str, repeat: int
) -> None:
    """TERM·HUP이 오면 run 끝 내림(러너의 `finally`)이 돌고 종료 코드는 128 + 신호 번호다. 내림
    도중에 신호가 또 와도 내림이 끊기지 않는다 — 끊는 것은 KILL이고 그것은 감독자 몫이다.

    **자식 프로세스로 돈다** — 핸들러가 없으면 신호가 테스트 러너를 죽인다. `raise_signal`은
    Windows에서도 파이썬 핸들러를 동기로 부른다.
    """
    marker = tmp_path / "teardown"
    argv = [
        "run", "--profile", "dev_fake", "--target", str(TARGET), "--condition", "B1@pipeline",
        "--kb-snapshot", "kb-20260901", "--seed", "20260901",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ]  # fmt: skip

    done = subprocess.run(
        [sys.executable, "-c", _CHILD, str(marker), name, str(repeat), *argv],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False,
    )  # fmt: skip

    assert marker.exists(), f"{name}에 run 끝 내림이 끝까지 안 돌았다 (rc={done.returncode})"
    assert done.returncode == 128 + getattr(signal, name), done.stderr[-400:]
