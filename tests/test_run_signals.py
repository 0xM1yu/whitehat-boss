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
import threading
from pathlib import Path

import pytest

from src import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
# 이 플랫폼에 있는 종료 신호 — Windows에는 SIGHUP이 없다.
SIGNALS = [name for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)]
# (첫 신호, 내림 도중에 오는 신호…) — 이 플랫폼에 있는 것만.
SEQUENCES = [
    sequence
    for sequence in [("SIGTERM",), ("SIGTERM", "SIGTERM"), ("SIGHUP",), ("SIGHUP", "SIGHUP")]
    if all(hasattr(signal, name) for name in sequence)
]

# 자식 프로세스: `cli.execute`를 러너의 `try: … finally: run 끝 내림` 모양으로 바꿔 끼우고
# `cli.main(["run", …])`을 돈다. 서버가 떠 있는 동안 첫 신호가 오고, 내림 도중에 나머지가
# 온다(감독자의 재촉). 내림이 끝까지 돌면 표지 파일을 남긴다. 신호가 run을 끝내지 않으면
# 0으로 끝난다. `ignored`면 `cli.main` 전에 첫 신호를 무시로 건다 — `nohup`(HUP)·
# `trap '' TERM`으로 띄운 run과 파이썬에게는 같은 상태다(`getsignal`이 `SIG_IGN`을 돌려준다).
_CHILD = textwrap.dedent(
    """
    import signal, sys
    from pathlib import Path
    from src import cli

    marker, mode = Path(sys.argv[1]), sys.argv[2]
    first, *later = (getattr(signal, name) for name in sys.argv[3].split(","))
    if mode == "ignored":
        signal.signal(first, signal.SIG_IGN)

    def execute(*_args, **_kwargs):
        try:
            signal.raise_signal(first)
        finally:
            for signum in later:
                signal.raise_signal(signum)
            marker.write_text("run 끝 내림이 끝까지 돌았다", encoding="utf-8")
        raise SystemExit(0)

    cli.execute = execute
    raise SystemExit(cli.main(sys.argv[4:]))
    """
)


def _run_child(
    tmp_path: Path, sequence: tuple[str, ...], *, ignored: bool = False
) -> subprocess.CompletedProcess[str]:
    argv = [
        "run", "--profile", "dev_fake", "--target", str(TARGET), "--condition", "B1@pipeline",
        "--kb-snapshot", "kb-20260901", "--seed", "20260901",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ]  # fmt: skip
    mode = "ignored" if ignored else "handled"
    return subprocess.run(
        [sys.executable, "-c", _CHILD, str(tmp_path / "teardown"), mode, ",".join(sequence), *argv],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False,
    )  # fmt: skip


@pytest.mark.parametrize("sequence", SEQUENCES, ids="-".join)
def test_a_run_ended_by_a_signal_still_runs_its_teardown(
    tmp_path: Path, sequence: tuple[str, ...]
) -> None:
    """TERM·HUP이 run을 끝내면 run 끝 내림(러너의 `finally`)이 돌고 종료 코드는 128 + 첫 신호
    번호다. 내림 도중에 신호가 또 와도 내림이 끊기지 않는다 — 끊는 것은 KILL이고 그것은 감독자
    몫이다.

    **자식 프로세스로 돈다** — 핸들러가 없으면 신호가 테스트 러너를 죽인다. `raise_signal`은
    Windows에서도 파이썬 핸들러를 동기로 부른다.
    """
    done = _run_child(tmp_path, sequence)

    assert (tmp_path / "teardown").exists(), (
        f"{sequence}에 run 끝 내림이 끝까지 안 돌았다 (rc={done.returncode})"
    )
    assert done.returncode == 128 + getattr(signal, sequence[0]), done.stderr[-400:]


@pytest.mark.parametrize("name", SIGNALS)
def test_a_signal_ignored_at_start_stays_ignored(tmp_path: Path, name: str) -> None:
    """무시한 채 띄운 신호는 무시한 채로 둔다 — `nohup`으로 띄운 run이 터미널이 닫혀(HUP) 죽지
    않는다. 운영자가 무시하라고 띄운 것을 하네스가 종료로 되살리면 안 된다."""
    done = _run_child(tmp_path, (name,), ignored=True)

    assert done.returncode == 0, (
        f"{name}을 무시한 채 띄운 run이 그 신호에 끝났다 (rc={done.returncode})"
    )


def test_the_guard_puts_back_the_handlers_it_replaced() -> None:
    """블록 안에서만 신호를 바꾼다 — 같은 프로세스에서 `cli.main`을 부르는 쪽(테스트 등)에 새지
    않는다."""
    before = signal.getsignal(signal.SIGTERM)

    with cli._terminate_as_exit():
        inside = signal.getsignal(signal.SIGTERM)

    assert inside is not before
    assert signal.getsignal(signal.SIGTERM) is before


def test_the_guard_does_nothing_off_the_main_thread() -> None:
    """`signal.signal`은 메인 스레드에서만 된다 — 다른 스레드에서 `run`을 불러도 죽지 않고 신호를
    안 바꾼다(그 스레드의 run은 신호에 내림을 못 돌린다)."""
    before = signal.getsignal(signal.SIGTERM)
    seen: list[object] = []

    def body() -> None:
        try:
            with cli._terminate_as_exit():
                seen.append(signal.getsignal(signal.SIGTERM))
        except ValueError as exc:  # 메인 스레드 밖의 `signal.signal`
            seen.append(exc)

    thread = threading.Thread(target=body)
    thread.start()
    thread.join()

    assert seen == [before]
