"""Stand-in for Joern server, slicer.py and merge_slicer_shards.py in tests/test_slice_sharded.py.

Not a Joern protocol fake: it fakes what tools/slice_sharded.py reads -- a listening port, the
prepared checkpoint's candidate list, the CPG file, exit codes, the merge counters, --out.
Behaviour by env: STUB_SERVER ok|die|die-restart|die-restart0|hang-restart0|deaf|deaf-zombie|
graceful|zombie-leader, STUB_SERVER_DELAY seconds, STUB_RESTART_DELAY seconds (a restarted server's
boot instead), STUB_PREPARE ok|fail, STUB_SHARD ok|die|die0|hang|deadline|deadline0,
STUB_DEADLINES (default 1), STUB_AFTER_RESTART ok|die, STUB_CANDIDATES (default 40).

`deaf` is Joern's shape: the launcher (this process) is an `sh` that runs the server as a child, and
the server ignores SIGTERM like a JVM whose shutdown hook cannot finish while a query runs --
SIGTERM ends the launcher and the server lives on. It writes its pid to `jvm.pid` in its cwd.
`deaf-zombie` is `deaf` whose server has also forked a child that exited at once and is never
wait()ed for: a zombie in the process group, which stays one after the server is killed when the
subreaper that adopts it (a container's PID 1) does not reap.
`graceful` handles SIGTERM with a shutdown that takes a moment and leaves `term.marker` when done.
`zombie-leader`: the main thread exits first, a thread keeps the listening socket.
`die-restart` boots once and dies at boot in a `restartN` directory (a restarted server);
`die-restart0` only in shard 0's; `hang-restart0` boots forever there (never listens).
Every server that listens leaves `listening` (its pid) in its cwd.
`deadline` (every shard) / `deadline0` (shard 0): the shard writes the progress events a query that
hit its deadline leaves, then blocks like a slicer waiting behind the abandoned query -- until it is
killed -- unless its --skip-candidates list already holds STUB_DEADLINES keys: then it completes.
"""

import ctypes
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path


def option(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def deaf_launcher(port: int) -> int:
    return subprocess.Popen([sys.executable, __file__, "--serve-deaf", str(port)]).wait()


def serve_deaf(port: int) -> int:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if os.environ.get("STUB_SERVER") == "deaf-zombie" and os.fork() == 0:
        os._exit(0)  # before jvm.pid: a reader of the file finds the zombie already there
    Path("jvm.pid").write_text(str(os.getpid()))  # after the handler: a pid in the file is deaf
    return listen(port)


def listen_gracefully(port: int) -> int:
    def hook(*_: object) -> None:
        time.sleep(0.5)  # a shutdown that finishes unless something cuts it short
        Path("term.marker").write_text("done")
        os._exit(0)

    signal.signal(signal.SIGTERM, hook)
    Path("ready").write_text("1")
    return listen(port)


def zombie_leader(port: int) -> int:
    """A thread holds the listening socket while the main thread is already gone: /proc shows the
    process as a zombie (state Z) with a live thread -- how a JVM looks in the milliseconds after
    SIGKILL, until its last thread has exited and the kernel has closed its sockets."""
    threading.Thread(target=listen, args=(port,)).start()
    ctypes.CDLL(None).pthread_exit(None)
    return 0


def restarted_in() -> str | None:
    """`shardN` when this server runs in `shardN/restartM` (a restart's server), else None."""
    here = Path.cwd()
    return here.parent.name if here.name.startswith("restart") else None


def serve(port: int) -> int:
    mode = os.environ.get("STUB_SERVER")
    shard = restarted_in()
    if mode == "hang-restart0" and shard == "shard0":
        time.sleep(30)  # a JVM that boots forever: the tool gives up on it and stops it
        return 3
    if (mode == "die" or (mode == "die-restart" and shard)
            or (mode == "die-restart0" and shard == "shard0")):
        print("stub server: died at boot")
        return 3
    if mode == "zombie-leader":
        return zombie_leader(port)
    if mode in ("deaf", "deaf-zombie"):
        return deaf_launcher(port)
    return listen_gracefully(port) if mode == "graceful" else listen(port)


def listen(port: int) -> int:
    boot = os.environ.get("STUB_RESTART_DELAY") if restarted_in() else None
    time.sleep(float(boot or os.environ.get("STUB_SERVER_DELAY", "0")))  # a slow JVM boot
    Path("workspace").mkdir(exist_ok=True)  # where Joern keeps the imported graph, in its cwd
    Path("workspace", "cpg.bin").write_text("graph")
    listener = socket.socket()
    if os.name != "nt":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # a restart rebinds the port
    listener.bind(("127.0.0.1", port))
    listener.listen()
    Path("listening").write_text(str(os.getpid()))  # this server bound its port (not a stale one)
    listener.settimeout(1)
    deadline = time.time() + 120  # a stub orphaned by a dead test does not outlive it for long
    while time.time() < deadline:
        try:
            listener.accept()[0].close()
        except TimeoutError:
            pass
    return 0


def merge(merged: Path, shards: list[Path]) -> int:
    candidates = int(os.environ.get("STUB_CANDIDATES", "40"))
    done = sum(int((p.parent / "done").read_text()) for p in shards if (p.parent / "done").exists())
    merged.write_text("merged")
    print(json.dumps({"candidates": candidates, "complete": done}))
    return 2 if done < candidates else 0


DEADLINE_ERROR = "Query deadline exceeded (600.0 seconds); server execution may continue"


def hit_deadline(progress: str, key: str) -> None:
    """What tools/slicer/slicer.py appends when a query runs into its deadline."""
    events = [{"event": "query_start", "stage": "flows"},
              {"event": "query_error", "stage": "flows", "error": DEADLINE_ERROR},
              {"event": "candidate_failed", "error": DEADLINE_ERROR}]
    with open(progress, "a", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps({"time": time.time(), "candidate_key": key, **event}) + "\n")


def slicer(argv: list[str]) -> int:
    checkpoint = Path(option(argv, "--checkpoint"))
    candidates = int(os.environ.get("STUB_CANDIDATES", "40"))
    if "--export-only" in argv:
        Path(option(argv, "--out")).write_text("{}")
        return 2
    port = int(option(argv, "--joern-endpoint").rsplit(":", 1)[1])
    socket.create_connection(("127.0.0.1", port), 2).close()  # the server must be up already
    if "--prepare-only" in argv:
        with sqlite3.connect(checkpoint) as conn:
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO metadata VALUES ('candidates', ?)",
                         (json.dumps(list(range(candidates))),))
        Path(option(argv, "--cpg-out")).write_text("cpg")
        return 1 if os.environ.get("STUB_PREPARE") == "fail" else 2
    folder = checkpoint.parent
    (folder / "argv.json").write_text(json.dumps(argv))
    with open(folder / "argvs.jsonl", "a", encoding="utf-8") as stream:  # one line per start
        stream.write(json.dumps(argv) + "\n")
    index, count = map(int, (option(argv, "--shard") or "0/1").split("/"))
    mode = os.environ.get("STUB_SHARD", "ok")
    if mode == "deadline" or (mode == "deadline0" and index == 0):
        skipped = option(argv, "--skip-candidates")
        known = len(Path(skipped).read_text().split()) if skipped else 0
        if known < int(os.environ.get("STUB_DEADLINES", "1")):
            hit_deadline(option(argv, "--progress"), f"stub-{index}-{known}")
            time.sleep(20)  # blocked behind the abandoned query until the tool kills it
            return 2
        if os.environ.get("STUB_AFTER_RESTART") == "die":
            return 1  # the restarted slicer itself fails
    elif mode == "hang":
        # Long enough for the tool to stop it, short enough that a tool which never does fails
        # the test instead of hanging it; ends without completing anything.
        time.sleep(20)
        return 2
    if mode == "die" or (mode == "die0" and index == 0):
        return 1
    time.sleep(0.5)  # a tool that does not wait for the shards misses this file
    share = candidates // count + (candidates % count if index == 0 else 0)
    (folder / "done").write_text(str(share))
    return 2


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[0] == "--serve":
        raise SystemExit(serve(int(args[1])))
    if args[0] == "--serve-deaf":
        raise SystemExit(serve_deaf(int(args[1])))
    if not args[0].startswith("--"):
        raise SystemExit(merge(Path(args[0]), [Path(p) for p in args[1:]]))
    raise SystemExit(slicer(args))
