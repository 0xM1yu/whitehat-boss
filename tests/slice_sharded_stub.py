"""Stand-in for Joern server, slicer.py and merge_slicer_shards.py in tests/test_slice_sharded.py.

Not a Joern protocol fake: it fakes what tools/slice_sharded.py reads -- a listening port, the
prepared checkpoint's candidate list, the CPG file, exit codes, the merge counters, --out.
Behaviour by env: STUB_SERVER ok|die, STUB_SERVER_DELAY seconds, STUB_PREPARE ok|fail,
STUB_SHARD ok|die|die0|hang, STUB_CANDIDATES (default 40).
"""

import json
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path


def option(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def serve(port: int) -> int:
    if os.environ.get("STUB_SERVER") == "die":
        print("stub server: died at boot")
        return 3
    time.sleep(float(os.environ.get("STUB_SERVER_DELAY", "0")))  # a slow JVM boot
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen()
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
    index, count = map(int, (option(argv, "--shard") or "0/1").split("/"))
    mode = os.environ.get("STUB_SHARD", "ok")
    if mode == "hang":
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
    if not args[0].startswith("--"):
        raise SystemExit(merge(Path(args[0]), [Path(p) for p in args[1:]]))
    raise SystemExit(slicer(args))
