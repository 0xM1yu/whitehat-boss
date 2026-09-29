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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from src import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
# 이 플랫폼에 있는 종료 신호 — Windows에는 SIGHUP이 없다.
SIGNALS = [name for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name)]
# (첫 신호, 내림 도중에 오는 신호…) — 이 플랫폼에 있는 것만. 교차(HUP→TERM·TERM→HUP)는 「첫 신호
# 뒤에는 **두** 신호를 무시한다」를 문다 — 받은 신호 하나만 무시하면 다른 신호가 내림을 끊는다.
SEQUENCES = [
    sequence
    for sequence in [
        ("SIGTERM",), ("SIGTERM", "SIGTERM"), ("SIGHUP",), ("SIGHUP", "SIGHUP"),
        ("SIGHUP", "SIGTERM"), ("SIGTERM", "SIGHUP"),
    ]  # fmt: skip
    if all(hasattr(signal, name) for name in sequence)
]

# 자식 프로세스: `cli.execute`를 러너의 `try: … finally: run 끝 내림` 모양으로 바꿔 끼우고
# `cli.main(["run", …])`을 돈다. 서버가 떠 있는 동안 첫 신호가 오고, 내림 도중에 나머지가
# 온다(감독자의 재촉). 내림이 끝까지 돌면 표지 파일을 남긴다. 신호가 run을 끝내지 않으면
# 0으로 끝난다.
# **출발 처분은 자식이 정한다** — TERM·HUP를 기본으로 두고 `ignored`에 든 것만 무시로 건다.
# 시험 러너가 `nohup`·`trap '' TERM`으로 떴어도 그 무시를 물려받지 않는다. 무시는 `nohup`(HUP)·
# `trap '' TERM`으로 띄운 run과 파이썬에게는 같은 상태다(`getsignal`이 `SIG_IGN`을 돌려준다).
_CHILD = textwrap.dedent(
    """
    import signal, sys
    from pathlib import Path
    from src import cli

    marker = Path(sys.argv[1])
    ignored = {getattr(signal, name) for name in sys.argv[2].split(",") if name}
    first, *later = (getattr(signal, name) for name in sys.argv[3].split(","))
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signum = getattr(signal, name)
            signal.signal(signum, signal.SIG_IGN if signum in ignored else signal.SIG_DFL)

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
    tmp_path: Path, sequence: tuple[str, ...], *, ignored: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    argv = [
        "run", "--profile", "dev_fake", "--target", str(TARGET), "--condition", "B1@pipeline",
        "--kb-snapshot", "kb-20260901", "--seed", "20260901",
        "--artifacts-root", str(tmp_path / "artifacts"),
    ]  # fmt: skip
    marker, order = str(tmp_path / "teardown"), ",".join(sequence)
    return subprocess.run(
        [sys.executable, "-c", _CHILD, marker, ",".join(ignored), order, *argv],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False,
    )  # fmt: skip


@contextmanager
def _disposition(signum: int, handler: object) -> Iterator[None]:
    """`signum`을 `handler`로 두고 끝나면 원래 처분으로 되돌린다 — 시험 러너가 어떻게 떴든
    (`nohup pytest`·`trap '' TERM`) 같은 출발점에서 돌고, 프로세스 전역 상태를 다음 테스트로
    새게 두지 않는다."""
    original = signal.signal(signum, handler)
    try:
        yield
    finally:
        if original is not None:  # 파이썬 밖에서 건 핸들러는 되돌릴 수 없다
            signal.signal(signum, original)


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
    done = _run_child(tmp_path, (name,), ignored=(name,))

    assert done.returncode == 0, (
        f"{name}을 무시한 채 띄운 run이 그 신호에 끝났다 (rc={done.returncode})"
    )


# 한 신호만 시작 때 무시하고 다른 신호는 살아 있는 조합 — (무시한 신호, 신호 순서열).
MIXED = [
    ("SIGHUP", ("SIGTERM",)),  # nohup + TERM — 무시 안 한 신호는 여전히 SystemExit이다
    ("SIGHUP", ("SIGTERM", "SIGHUP")),  # 내림 도중 HUP — 무시한 채로 남아 내림을 안 끊는다
    ("SIGTERM", ("SIGHUP",)),
    ("SIGTERM", ("SIGHUP", "SIGTERM")),
]


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="SIGHUP이 없는 플랫폼(Windows)")
@pytest.mark.parametrize(
    ("ignored", "sequence"),
    MIXED,
    ids=["hup-ignored+TERM", "hup-ignored+TERM+HUP", "term-ignored+HUP", "term-ignored+HUP+TERM"],
)
def test_one_signal_ignored_at_start_leaves_the_other_live(
    tmp_path: Path, ignored: str, sequence: tuple[str, ...]
) -> None:
    """한 신호만 무시한 채 띄운 run(`nohup` 등)도 다른 신호에는 내림을 돈다 — 무시한 신호가 있다고
    가드를 통째로 끄면 `nohup` 사용자의 TERM 내림이 사라진다. 무시한 신호는 첫 신호 뒤에도 무시로
    남는다 — 내림 도중에 와도 내림을 안 끊는다."""
    done = _run_child(tmp_path, sequence, ignored=(ignored,))

    assert (tmp_path / "teardown").exists(), (
        f"{ignored} 무시 · {sequence}에 run 끝 내림이 끝까지 안 돌았다 (rc={done.returncode})"
    )
    assert done.returncode == 128 + getattr(signal, sequence[0]), done.stderr[-400:]


@pytest.mark.parametrize("name", SIGNALS)
def test_the_guard_leaves_a_signal_ignored_at_start_ignored(name: str) -> None:
    """무시한 채 들어온 신호는 블록 안에서도, 나간 뒤에도 무시다 — 되돌릴 때 기본으로 덮으면
    운영자가 건 무시가 run 뒤에 사라진다."""
    signum = getattr(signal, name)

    with _disposition(signum, signal.SIG_IGN):
        with cli._terminate_as_exit():
            assert signal.getsignal(signum) is signal.SIG_IGN
        assert signal.getsignal(signum) is signal.SIG_IGN


def test_the_guard_puts_back_the_handlers_it_replaced() -> None:
    """블록 안에서만 신호를 바꾼다 — 같은 프로세스에서 `cli.main`을 부르는 쪽(테스트 등)에 새지
    않는다."""
    with _disposition(signal.SIGTERM, signal.SIG_DFL):
        with cli._terminate_as_exit():
            inside = signal.getsignal(signal.SIGTERM)

        assert inside is not signal.SIG_DFL
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_the_guard_does_nothing_off_the_main_thread() -> None:
    """`signal.signal`은 메인 스레드에서만 된다 — 다른 스레드에서 `run`을 불러도 죽지 않고 신호를
    안 바꾼다(그 스레드의 run은 신호에 내림을 못 돌린다)."""
    seen: list[object] = []

    def body() -> None:
        try:
            with cli._terminate_as_exit():
                seen.append(signal.getsignal(signal.SIGTERM))
        except ValueError as exc:  # 메인 스레드 밖의 `signal.signal`
            seen.append(exc)

    with _disposition(signal.SIGTERM, signal.SIG_DFL):
        thread = threading.Thread(target=body)
        thread.start()
        thread.join()

    assert seen == [signal.SIG_DFL]
