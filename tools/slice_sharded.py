"""Slice one repo with N Joern servers in one command (#370): prepare -> shards -> merge -> export.

    python tools/slice_sharded.py --repo /work/repo --out /work/slices.json \\
      --work-dir /work/run --joern /opt/joern/joern-cli/joern -- --max-flows-per-candidate 10

1. prepare (one server): `slicer.py --prepare-only` builds the CPG and discovers candidates once,
   saves the graph (#368)
2. shards (N servers, started together): each starts from a copy of the prepared checkpoint,
   loads the saved graph (`--resume --cpg`) and takes candidates from one shared queue
   (`--claim-db`, #369)
3. merge the shard checkpoints, then `--export-only` writes `--out`. Candidates a shard claimed but
   did not finish (crash, `--wall-seconds`) come out degraded (#364) -- every shard started from the
   prepared checkpoint, so one stopped early cannot void the merge (accel-ppp, 2026-10-01).

Everything after `--` goes to the prepare call and every shard call unchanged (analysis settings
must match). The export call does not take them: `--export-only` reads the settings from the merged
checkpoint's run contract. Flags this tool sets itself (`--joern-endpoint`, `--checkpoint`,
`--shard`, `--export-dir`, ...) are refused after `--`.

Shard count `--shards auto` = min(cores // --cores-per-shard, memory // --memory-per-shard-gb,
candidates // --min-candidates-per-shard, --max-shards), at least 1. Cores = CPU affinity capped by
the cgroup CPU quota (v2 `cpu.max`, v1 `cpu.cfs_quota_us`; the process's cgroup and its ancestors --
a container's `nproc` can report the host's cores). Memory = MemAvailable capped by the cgroup limit
(v2 `memory.max`, v1 `memory.limit_in_bytes`); when no cgroup file is readable the summary and
stderr say so. ⚠ Every default here is a set value, not a measured one: `--cores-per-shard 4`,
`--heap 20g`, `--memory-per-shard-gb` (= heap + 4 GB for the JVM's non-heap and the slicer process),
`--min-candidates-per-shard 20`, `--max-shards 32`. No run shows `auto` beats a small fixed count:
the only comparison (PR #373 body) is 30 shards 621 s vs 2 shards 664 s, taken while the same box
ran another 8-shard job, so the 43 s difference (computed) is not attributable to the shard count.

Exit: 0 = every candidate complete; 3 = `--out` written but some candidates incomplete (degraded,
see summary.json `merge`, `dead_shards`, `wall_stopped_shards`); 1 = failed (prepare, a server,
merge or export failed, or every shard died); 2 = usage error; 128 + signal when stopped by
SIGTERM/SIGHUP -- shards and servers are stopped and summary.json is written on every path.
Servers and shards are stopped as whole process groups, all at once: SIGTERM, up to
STOP_GRACE_SECONDS for the groups to empty (one pass over /proc per tick for all of them),
SIGKILL, a check -- pids still alive after that are on stderr and in summary.json
`unstopped_pids`; the exit code does not change. (`joern` is an sh that runs the JVM as a child,
and a JVM busy with a query ignores SIGTERM.)
⚠ SIGKILL of this tool cannot be caught: its servers and shards (own sessions) keep running.
POSIX only (process groups via killpg, start_new_session).

`--restart-on-deadline` (off: nothing here changes): a query that hit the slicer's client deadline
keeps running in the shard's Joern server and every later query waits behind it, so the shard's
slicer and server are killed and the shard is started again (new server, `--resume --cpg`, the
timed-out candidates in `--skip-candidates`) -- at most `--max-restarts-per-shard` times, none with
under RESTART_MIN_WALL_SECONDS of `--wall-seconds` left. Those candidates stay unfinished, degraded
in the output as without the flag. The restarted slicer takes the shard's candidates in the same
order: complete ones come from the checkpoint, any other is queried again -- also one that failed
for a reason other than the deadline (it has no checkpoint stage). A restart waits for its new
servers at most the boot limit and about a second past the wall at most (measured +1 s),
looking at nothing else meanwhile; a shard
whose new server does not come up stays dead (summary.json `restart.errors`) while the others of
that batch go on. `restart.per_shard` and `total` count the restarts whose new slicer ran.
"""

import argparse
import contextlib
import json
import math
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLICER = ROOT / "tools" / "slicer" / "slicer.py"
MERGE = ROOT / "tools" / "merge_slicer_shards.py"
SERVER_START_SECONDS = 300  # set value: a Joern server that is not listening by then failed
# Stopping servers (stop_all): SIGTERM, wait for the whole process groups, then SIGKILL. Measured
# on the dev box (2026-10-03, heap 2g): a Joern server with no query running left on SIGTERM in
# 0.5-0.7 s (3 runs; a `ps` check that skipped zombies, so a lower bound), one with a query running
# had not left after 30 s (1 run). The two waits are set values.
STOP_GRACE_SECONDS = 10
KILL_VERIFY_SECONDS = 10  # how long a SIGKILLed group may take to vanish
STOP_POLL_SECONDS = 0.1
POLL_SECONDS = 5
JVM_OVERHEAD_GB = 4  # set value, not measured: non-heap JVM + slicer process per shard
DEFAULT_MAX_RESTARTS = 5  # set value, not measured: --max-restarts-per-shard
# A restart took 16 s to the shard's next query on a small graph (libxml2, 8.7 MB CPG, dev box,
# 2026-10-03, one run); a bigger one imports longer. Less wall left: none (a set value).
RESTART_MIN_WALL_SECONDS = 60
# progress `candidate_failed` `error`s for a query the client gave up on while the server goes on
# with it (JoernClient.query: its deadline message, or the socket timeout that can fire first).
DEADLINE_ERRORS = ("Query deadline exceeded", "HTTP request failed: timed out")

EXIT_OK, EXIT_FAILED, EXIT_INCOMPLETE = 0, 1, 3
# Flags slice_sharded.py passes itself; one after `--` would split or redirect the run.
OWNED_FLAGS = (
    "--repo", "--out", "--joern-endpoint", "--joern-version-command", "--checkpoint", "--progress",
    "--resume", "--export-only", "--prepare-only", "--cpg-out", "--cpg", "--shard", "--claim-db",
    "--export-dir", "--skip-candidates",
)
# The slicer's client deadlines: without one no query ever "hits the deadline".
DEADLINE_FLAGS = ("--query-timeout-seconds", "--candidate-timeout-seconds")
PROC_CGROUP = Path("/proc/self/cgroup")
CGROUP_ROOT = Path("/sys/fs/cgroup")
UNLIMITED_BYTES = 1 << 60  # cgroup v1 writes "no limit" as a huge page-aligned number


class PipelineError(RuntimeError):
    pass


def _digits(text: str) -> int | None:
    """Plain ASCII digits only -- `isdigit()` also takes '²' and int() then raises."""
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def parse_cpu_max(text: str) -> float | None:
    """cgroup v2 `cpu.max` ("<quota> <period>" or "max <period>") in cores; malformed -> None."""
    parts = text.split()
    if len(parts) < 2 or parts[0] == "max":
        return None
    quota, period = _digits(parts[0]), _digits(parts[1])
    return quota / period if quota and period else None


def parse_cfs_quota(quota_text: str, period_text: str) -> float | None:
    """cgroup v1 `cpu.cfs_quota_us` / `cpu.cfs_period_us` in cores; -1 (none), malformed -> None."""
    quota, period = _digits(quota_text.strip()), _digits(period_text.strip())
    return quota / period if quota and period else None


def parse_limit_bytes(text: str) -> int | None:
    """cgroup `memory.max` / `memory.limit_in_bytes`; "max", v1's huge no-limit, malformed -> None.
    """
    value = _digits(text.strip())
    return value if value and value < UNLIMITED_BYTES else None


def cgroup_dirs(controller: str, proc: Path = PROC_CGROUP, root: Path = CGROUP_ROOT) -> list[Path]:
    """The cgroup directories that bind this process for `controller`, own cgroup first.

    Lines of /proc/self/cgroup are `id:controllers:path` (v2: empty controllers). Every ancestor
    counts -- a limit set higher up still binds -- and in a container the mount is often the
    container's own cgroup while the path names the host's, so walking up reaches the mount root."""
    try:
        lines = proc.read_text().splitlines()
    except OSError:
        lines = []
    found: list[Path] = []
    for line in lines or ["0::/", f"0:{controller}:/"]:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        controllers, path = parts[1], [p for p in parts[2].strip().split("/") if p]
        if controllers == "":
            bases = [root]
        elif controller in controllers.split(","):
            bases = [root / controllers, root / controller]
        else:
            continue
        for base in bases:
            for depth in range(len(path), -1, -1):
                folder = base.joinpath(*path[:depth])
                if folder.is_dir() and folder not in found:
                    found.append(folder)
    return found


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, UnicodeError):
        return None


def cgroup_cores(proc: Path = PROC_CGROUP, root: Path = CGROUP_ROOT) -> float | None:
    quotas = []
    for folder in cgroup_dirs("cpu", proc, root):
        text = _read(folder / "cpu.max")
        if text is not None:
            quotas.append(parse_cpu_max(text))
        quota, period = _read(folder / "cpu.cfs_quota_us"), _read(folder / "cpu.cfs_period_us")
        if quota is not None and period is not None:
            quotas.append(parse_cfs_quota(quota, period))
    quotas = [q for q in quotas if q]
    return min(quotas) if quotas else None


def cgroup_memory_bytes(proc: Path = PROC_CGROUP,
                        root: Path = CGROUP_ROOT) -> tuple[int | None, bool]:
    """(tightest limit or None when unlimited, whether any cgroup memory file was readable)."""
    limits, readable = [], False
    for folder in cgroup_dirs("memory", proc, root):
        for name in ("memory.max", "memory.limit_in_bytes"):
            text = _read(folder / name)
            if text is not None:
                readable = True
                limits.append(parse_limit_bytes(text))
    limits = [v for v in limits if v]
    return (min(limits) if limits else None), readable


def available_memory_gb(meminfo: Path = Path("/proc/meminfo")) -> float | None:
    """MemAvailable in GB; unreadable or malformed -> None."""
    text = _read(meminfo)
    for line in (text or "").splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            value = _digits(parts[1]) if len(parts) > 1 else None
            return value / 1024 / 1024 if value is not None else None
    return None


def memory_reading(meminfo: Path = Path("/proc/meminfo"), proc: Path = PROC_CGROUP,
                   root: Path = CGROUP_ROOT) -> dict:
    available = available_memory_gb(meminfo)
    limit, readable = cgroup_memory_bytes(proc, root)
    limit_gb = limit / 1024 ** 3 if limit else None
    known = [v for v in (available, limit_gb) if v is not None]
    warnings = []
    if available is None:
        warnings.append(f"MemAvailable unreadable ({meminfo})")
    if not readable:
        warnings.append("no cgroup memory limit readable: a container limit would be missed")
    return {"memory_gb": min(known) if known else None, "mem_available_gb": available,
            "cgroup_limit_gb": limit_gb, "warning": "; ".join(warnings) or None}


def available_cores() -> float:
    affinity = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    quota = cgroup_cores()
    return min(quota, affinity or quota) if quota else float(affinity or 1)


def shard_limits(cores: float, memory_gb: float | None, candidates: int | None, *,
                 cores_per_shard: float, memory_per_shard_gb: float, min_candidates: int,
                 max_shards: int) -> dict[str, int]:
    """Each term's shard count; a term whose reading is unknown is left out."""
    limits = {"cores": int(cores // cores_per_shard), "max_shards": max_shards}
    if memory_gb is not None:
        limits["memory"] = int(memory_gb // memory_per_shard_gb)
    if candidates is not None:
        limits["candidates"] = candidates // min_candidates
    return limits


def auto_shards(
    cores: float, memory_gb: float | None, candidates: int, *, cores_per_shard: float,
    memory_per_shard_gb: float, min_candidates: int, max_shards: int,
) -> int:
    limits = shard_limits(cores, memory_gb, candidates, cores_per_shard=cores_per_shard,
                          memory_per_shard_gb=memory_per_shard_gb, min_candidates=min_candidates,
                          max_shards=max_shards)
    return max(1, min(limits.values()))


def deciding_term(limits: dict[str, int]) -> str:
    return min(limits, key=limits.__getitem__)


def port_in_use(port: int) -> bool:
    """A bind on 127.0.0.1 fails over any listener the shards could reach there (127.0.0.1,
    0.0.0.0 or dual-stack ::)."""
    with socket.socket() as probe:
        if os.name != "nt":
            # Lets the bind pass over TIME_WAIT leftovers, never over a listener. (On Windows
            # SO_REUSEADDR would bind over a live listener, so it stays off there.)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def ensure_ports_free(ports: list[int]) -> None:
    taken = [p for p in ports if port_in_use(p)]
    if taken:
        # A leftover server would answer for ours: its shard would slice someone else's graph.
        raise PipelineError(f"ports already in use: {taken} -- stop the old servers or pick "
                            "another --base-port")


def server_command(joern: Path, heap: str, port: int) -> list[str]:
    return [str(joern), "-J-Xss64m", f"-J-Xmx{heap}", "--server", "--server-host", "127.0.0.1",
            "--server-port", str(port)]


def launch(command: list[str], log: Path | None, tracked: list, **kw) -> subprocess.Popen:
    stream = open(log, "w") if log else None  # noqa: SIM115 -- the child owns it
    try:
        process = subprocess.Popen(command, stdout=stream or kw.pop("stdout", None),
                                   stderr=subprocess.STDOUT if stream else None,
                                   stdin=subprocess.DEVNULL, start_new_session=True, **kw)
    finally:
        if stream:
            stream.close()
    tracked.append(process)
    return process


def serve(joern: Path, heap: str, port: int, cwd: Path, tracked: list) -> subprocess.Popen:
    cwd.mkdir(parents=True, exist_ok=True)
    return launch(server_command(joern, heap, port), cwd / "joern.log", tracked, cwd=cwd)


def wait_servers(servers: list[tuple[subprocess.Popen, int, Path]],
                 seconds: float | None = None, partial: bool = False) -> dict[int, str]:
    """Wait until every server listens; fail fast when one exits first. With `partial` a server
    that exits, or is not listening in time, does not fail the others: they are waited for and the
    ones that failed come back as {port: why}."""
    seconds = SERVER_START_SECONDS if seconds is None else seconds
    deadline = time.time() + seconds
    pending, failed = list(servers), {}
    while pending:
        for item in list(pending):
            process, port, folder = item
            code = process.poll()
            if code is not None:
                why = (f"Joern server on port {port} exited {code} before listening "
                       f"(see {folder / 'joern.log'})")
                if not partial:
                    raise PipelineError(why)
                failed[port] = why
                pending.remove(item)
                continue
            try:
                socket.create_connection(("127.0.0.1", port), 1).close()
                pending.remove(item)
            except OSError:
                pass
        if pending and time.time() > deadline:
            if not partial:
                raise PipelineError(f"Joern servers on ports {[p for _, p, _ in pending]} not "
                                    f"listening after {seconds} s "
                                    f"(see {pending[0][2] / 'joern.log'})")
            for _, port, folder in pending:
                failed[port] = (f"Joern server on port {port} not listening after "
                                f"{round(seconds, 1)} s (see {folder / 'joern.log'})")
            break
        if pending:
            time.sleep(0.5)
    return failed


def _own_proc() -> bool:
    """Whether /proc is the pid namespace this process is in: `/proc/self` names our own pid there.
    A sandbox that unshares the pid namespace and keeps the host's /proc (bubblewrap, `unshare -p`)
    shows the host's pids -- no group of ours is in it, and every group would look empty."""
    try:
        return os.readlink("/proc/self") == str(os.getpid())
    except OSError:
        return False


def _live_groups(groups: set[int]) -> dict[int, list[int]]:
    """The live pids of each of these process groups, from ONE pass over /proc however many groups
    there are (a group with none is left out). An unreaped zombie is not live (`killpg(group, 0)`
    counts it) unless other threads of it are still exiting: they hold its sockets -- a killed
    server's port kept accepting for up to 0.57 s after SIGKILL (dev, 2026-10-03, 3 runs). Without
    a /proc of our own `killpg` is all there is (it counts an unreaped zombie), and the leader's pid
    names the group."""
    found: dict[int, list[int]] = {}
    if not _own_proc():
        for group in groups:
            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                continue
            found[group] = [group]
        return found
    for entry in Path("/proc").iterdir():
        pid = _digits(entry.name)
        if pid is None:
            continue
        try:  # bytes: the kernel cuts comm at 15 bytes, mid-character too, and that is not UTF-8
            text = (entry / "stat").read_bytes().decode("utf-8", "replace")
        except OSError:  # gone since the listing
            continue
        # "pid (comm) state ppid pgrp ..." -- comm can hold spaces and parentheses
        fields = text.rpartition(")")[2].split()
        group = _digits(fields[2]) if len(fields) > 2 else None
        if group in groups and (fields[0] not in ("Z", "X", "x") or _threads_left(entry)):
            found.setdefault(group, []).append(pid)
    return found


def _threads_left(process_dir: Path) -> bool:
    try:
        return len(os.listdir(process_dir / "task")) > 1  # the zombie leader itself is one
    except OSError:
        return False


def _signal_group(group: int, sig: int) -> None:
    try:
        os.killpg(group, sig)
    except OSError:  # gone already -- _live_groups is what says whether anything is left
        pass


def _wait_gone(groups: dict[int, subprocess.Popen], seconds: float) -> dict[int, list[int]]:
    """Until none of these process groups has a live member; what is still alive when `seconds` ran
    out. One /proc pass per tick for all of them: a thread per process that each rescanned /proc
    took 12.5-31.1 s to stop 8-32 servers among 1,000-3,000 processes, 10 s of it the grace; one
    scan per tick took 10.0-10.2 s there, the grace plus 0.1-0.2 s (WSL2, 2026-10-04, stub servers,
    one run per point, four points). The launchers are reaped on the way: `poll()` runs after the
    scan, so a launcher the scan found dead (a SIGKILLed one is a zombie by then) is reaped before
    we return."""
    deadline = time.time() + seconds
    while groups:
        alive = _live_groups(set(groups))
        for process in groups.values():
            process.poll()
        if not alive or time.time() >= deadline:
            return alive
        groups = {group: groups[group] for group in alive}
        time.sleep(STOP_POLL_SECONDS)
    return {}


def _terminate(processes: list[subprocess.Popen], grace: float | None = None) -> list[int]:
    """End every process and everything else in its process group, all at once; returns the pids
    that outlived SIGKILL. `joern` is an `sh` that runs the JVM as a child, and a JVM with a query
    running does not finish its shutdown hook until that query ends (jstack, text not kept; eight
    outlived a run on 2026-10-03), so the launcher dying says nothing: SIGTERM to every group,
    `grace` seconds (default STOP_GRACE_SECONDS) for them to empty, SIGKILL for what is left, a
    check. They share one grace (measured at four points, see _wait_gone). `grace=0` skips the
    SIGTERM."""
    if not hasattr(os, "killpg"):  # not POSIX: no process group, best effort on the children
        for process in processes:
            try:
                process.terminate()
            except OSError:
                pass
        for process in processes:
            try:
                process.wait(30)
            except subprocess.TimeoutExpired:
                process.kill()
        return []
    groups = {process.pid: process for process in processes}  # `launch`: each leads its own session
    grace = STOP_GRACE_SECONDS if grace is None else grace
    left = set(groups)
    if grace > 0:
        for group in groups:
            _signal_group(group, signal.SIGTERM)
        left = set(_wait_gone(groups, grace))
    for group in left:
        _signal_group(group, signal.SIGKILL)
    alive = _wait_gone({group: groups[group] for group in left}, KILL_VERIFY_SECONDS)
    return [pid for pids in alive.values() for pid in pids]


def stop_all(processes: list[subprocess.Popen], tracked: list | None = None,
             summary: dict | None = None, grace: float | None = None) -> list[int]:
    """Stop all of them and their process groups at once (see _terminate); drop them from
    `tracked`. The pids that outlived SIGKILL are warned about on stderr, put in
    `summary["unstopped_pids"]` and returned."""
    survivors = _terminate(processes, grace)
    if tracked is not None:
        for process in processes:
            if process in tracked:
                tracked.remove(process)
    if survivors:
        print(f"WARNING: still alive after SIGKILL: pids {survivors}", file=sys.stderr)
        if summary is not None:
            summary.setdefault("unstopped_pids", []).extend(survivors)
    return survivors


def slicer_command(repo: Path, out: Path, port: int, joern: Path, checkpoint: Path,
                   extra: list[str], *more: str) -> list[str]:
    return [sys.executable, str(SLICER), "--repo", str(repo), "--out", str(out),
            "--joern-endpoint", f"http://127.0.0.1:{port}", "--joern-version-command", str(joern),
            "--checkpoint", str(checkpoint), "--progress", str(out.parent / "progress.jsonl"),
            *more, *extra]


def shard_command(repo: Path, folder: Path, port: int, joern: Path, cpg: Path, extra: list[str],
                  index: int, count: int, claims: Path, skip: Path | None = None) -> list[str]:
    """One shard's slicer call, resumed on the prepared graph: one shard = a plain seeded run
    (--shard needs N >= 2), more = the shared queue; `skip` (a restart) lists those to leave."""
    split = ["--shard", f"{index}/{count}", "--claim-db", str(claims)] if count > 1 else []
    leave = ["--skip-candidates", str(skip)] if skip else []
    return slicer_command(repo, folder / "slices.json", port, joern, folder / "state.db", extra,
                          "--resume", "--cpg", str(cpg), *split, *leave)


def deadline_keys(events: list[dict]) -> list[str]:
    """Candidates whose query hit the deadline (`candidate_failed` names them for every stage)."""
    return [e["candidate_key"] for e in events
            if e.get("event") == "candidate_failed" and isinstance(e.get("candidate_key"), str)
            and str(e.get("error")).startswith(DEADLINE_ERRORS)]


class ProgressTail:
    """The complete JSON lines a shard's --progress file gained since the last read."""

    def __init__(self, path: Path) -> None:
        self.path, self.offset = path, 0

    def read(self) -> list[dict]:
        try:
            with open(self.path, "rb") as stream:
                stream.seek(self.offset)
                data = stream.read()
        except OSError:
            return []
        data = data[:data.rfind(b"\n") + 1]  # a line still being written is for the next read
        self.offset += len(data)
        events = []
        for line in data.splitlines():
            with contextlib.suppress(ValueError):  # junk is not ours to complain about
                events.append(json.loads(line))
        return [event for event in events if isinstance(event, dict)]


class DeadlineRestarts:
    """--restart-on-deadline (see the module docstring). `poll` reads what the shards' progress logs
    gained; a shard with a deadline hit has its slicer and server killed and is started again, with
    `--skip-candidates` naming the timed-out candidates: `--resume` alone would slice the first of
    them again (its claim comes back to the same `I/N`) and spend the deadline once more."""

    def __init__(self, args: argparse.Namespace, work: Path, ports: list[int],
                 command: Callable[[int, Path], list[str]], tracked: list, summary: dict,
                 started: float, wall: float | None, logs: list[Path]) -> None:
        self.args, self.work, self.ports, self.command, self.logs = args, work, ports, command, logs
        self.tracked, self.summary, self.started, self.wall = tracked, summary, started, wall
        self.tails = [ProgressTail(work / f"shard{i}" / "progress.jsonl")
                      for i in range(len(ports))]
        self.skips: list[list[str]] = [[] for _ in ports]
        self.report = summary["restart"] = {
            "max_per_shard": args.max_restarts_per_shard, "per_shard": [0] * len(ports),
            "total": 0, "events": [], "limit_hit": [], "errors": []}

    def poll(self, shards: list[subprocess.Popen], servers: list[tuple | None]) -> None:
        """One look at the running shards; restarts those whose query hit the deadline."""
        if self.wall and self.wall - time.time() < RESTART_MIN_WALL_SECONDS:
            return  # too late to pay for one: the wall stops the shards soon
        due = {}
        for index, process in enumerate(shards):
            hits = deadline_keys(self.tails[index].read()) if process.poll() is None else []
            if hits and self.report["per_shard"][index] < self.args.max_restarts_per_shard:
                due[index] = hits
            elif hits and index not in self.report["limit_hit"]:
                self.report["limit_hit"].append(index)
        if due:
            self.restart(due, shards, servers)

    def boot_seconds(self) -> float:
        """How long a restart waits for its servers: the boot limit, but not past the wall --
        nothing else is looked at (the wall, the other shards) while it waits."""
        left = self.wall - time.time() if self.wall else SERVER_START_SECONDS
        return max(0.0, min(SERVER_START_SECONDS, left))

    def restart(self, due: dict[int, list[str]], shards: list[subprocess.Popen],
                servers: list[tuple | None]) -> None:
        """Restart the shards in `due` together. A shard whose new server does not come up stays
        dead (its killed slicer is in `shards`, `servers[index]` is None: nothing left to stop);
        the others go on. Counted in `report` once the new slicer runs, not when tried."""
        began = time.time()
        fresh, added = {}, {}
        for index, hits in due.items():
            old, dead_cwd = [shards[index], servers[index][0]], servers[index][2]
            left = stop_all(old, self.tracked, self.summary, grace=0)
            if left:  # the old server may still hold the port
                self.report["errors"].append(f"shard {index}: pids {left} outlived SIGKILL")
                continue
            shutil.rmtree(dead_cwd / "workspace", ignore_errors=True)  # importCpg copied the CPG
            keys = hits + deadline_keys(self.tails[index].read())  # the kill can cut an event off
            added[index] = [k for k in dict.fromkeys(keys) if k not in self.skips[index]]
            self.skips[index] += added[index]
            folder = self.work / f"shard{index}"
            (folder / "skip_candidates.txt").write_text(
                "".join(f"{key}\n" for key in self.skips[index]), encoding="utf-8")
            cwd = folder / f"restart{self.report['per_shard'][index] + 1}"
            fresh[index] = (serve(self.args.joern, self.args.heap, self.ports[index], cwd,
                                  self.tracked), cwd)
        failed = wait_servers([(p, self.ports[i], cwd) for i, (p, cwd) in fresh.items()],
                              self.boot_seconds(), partial=True)
        down = [index for index in fresh if self.ports[index] in failed]
        for index in down:
            self.report["errors"].append(f"shard {index}: {failed[self.ports[index]]}")
            servers[index] = None
        if down:
            stop_all([fresh[index][0] for index in down], self.tracked, self.summary)
        for index, (server, cwd) in fresh.items():
            if index in down:
                continue
            count = self.report["per_shard"][index] = self.report["per_shard"][index] + 1
            self.report["total"] += 1
            folder = self.work / f"shard{index}"
            log = folder / f"slicer.{count}.log"
            shards[index] = launch(self.command(index, folder / "skip_candidates.txt"), log,
                                   self.tracked)
            servers[index], self.logs[index] = (server, self.ports[index], cwd), log
            self.report["events"].append({
                "shard": index, "at_seconds": round(began - self.started),
                "candidates": added[index], "seconds": round(time.time() - began, 1)})


def flags_in(extra: list[str], flags: tuple[str, ...]) -> list[str]:
    """These flags in the passthrough, also as `--flag=value` or an argparse abbreviation."""
    found = []
    for token in extra:
        name = token.split("=", 1)[0]
        if name.startswith("--") and len(name) > 2 and any(f.startswith(name) for f in flags):
            found.append(token)
    return found


def owned_flags_in(extra: list[str]) -> list[str]:
    """Tool-owned flags in the passthrough."""
    return flags_in(extra, OWNED_FLAGS)


def heap_gb(text: str) -> float:
    match = re.fullmatch(r"([0-9]+)([kKmMgG])", text)
    if not match or int(match[1]) == 0:
        raise argparse.ArgumentTypeError("a JVM size like 20g or 16384m")
    return int(match[1]) / {"k": 1024 ** 2, "m": 1024, "g": 1}[match[2].lower()]


def _heap(text: str) -> str:
    heap_gb(text)
    return text


def _positive_int(text: str) -> int:
    value = _digits(text)
    if not value:
        raise argparse.ArgumentTypeError("a whole number >= 1")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("a number > 0")
    return value


def _shards(text: str) -> str | int:
    return "auto" if text == "auto" else _positive_int(text)


def _port(text: str) -> int:
    value = _positive_int(text)
    if value > 65535:
        raise argparse.ArgumentTypeError("a TCP port")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--repo", required=True, type=Path)
    result.add_argument("--out", required=True, type=Path)
    result.add_argument("--work-dir", required=True, type=Path)
    result.add_argument("--joern", required=True, type=Path, help="joern-cli/joern launcher")
    result.add_argument("--shards", default="auto", type=_shards, help="auto, or a number >= 1")
    result.add_argument("--heap", default="20g", type=_heap,
                        help="Joern -Xmx per server (set value, not measured)")
    result.add_argument("--base-port", type=_port, default=18500)
    result.add_argument("--cores-per-shard", type=_positive_float, default=4.0)
    result.add_argument("--memory-per-shard-gb", type=_positive_float,
                        help=f"default: heap + {JVM_OVERHEAD_GB} GB (set value, not measured)")
    result.add_argument("--min-candidates-per-shard", type=_positive_int, default=20)
    result.add_argument("--max-shards", type=_positive_int, default=32)
    result.add_argument("--wall-seconds", type=_positive_float,
                        help="stop shards still running this long after the shard servers are up "
                        "(omitted: no limit)")
    result.add_argument("--restart-on-deadline", action="store_true",
                        help="restart a shard whose query hit the slicer's client deadline "
                        "(--query-timeout-seconds or --candidate-timeout-seconds after `--`; "
                        "without one this does nothing; see the module docstring)")
    result.add_argument("--max-restarts-per-shard", type=_positive_int,
                        default=DEFAULT_MAX_RESTARTS,
                        help="with --restart-on-deadline (default %(default)s, a set value)")
    result.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    return result


def parse(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    cli = parser()
    split = argv.index("--") if "--" in argv else len(argv)
    args, extra = cli.parse_args(argv[:split]), argv[split + 1:]
    owned = owned_flags_in(extra)
    if owned:
        cli.error(f"{owned} after -- : slice_sharded.py sets these itself")
    if args.restart_on_deadline and not flags_in(extra, DEADLINE_FLAGS):
        print("WARNING --restart-on-deadline: no --query-timeout-seconds or "
              "--candidate-timeout-seconds after --, so the slicer has no client deadline and no "
              "restart will ever trigger", file=sys.stderr)
    args.joern = args.joern.resolve()
    if not args.joern.is_file():
        cli.error(f"--joern {args.joern} is not a file")
    if args.memory_per_shard_gb is None:
        args.memory_per_shard_gb = heap_gb(args.heap) + JVM_OVERHEAD_GB
    return args, extra


def host_reading(args: argparse.Namespace, candidates: int | None) -> dict:
    memory = memory_reading()
    cores = available_cores()
    limits = shard_limits(cores, memory["memory_gb"], candidates,
                          cores_per_shard=args.cores_per_shard,
                          memory_per_shard_gb=args.memory_per_shard_gb,
                          min_candidates=args.min_candidates_per_shard, max_shards=args.max_shards)
    if args.shards == "auto":
        count, term = max(1, min(limits.values())), deciding_term(limits)
    else:
        count, term = args.shards, "--shards"
    if memory["warning"]:
        print(f"WARNING memory: {memory['warning']}", file=sys.stderr)
    return {"cores": cores, "memory": memory, "limits": limits, "shards": count,
            "decided_by": term, "heap": args.heap, "memory_per_shard_gb": args.memory_per_shard_gb,
            "cores_per_shard": args.cores_per_shard}


def candidate_count(seed: Path) -> int:
    with sqlite3.connect(seed) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key='candidates'").fetchone()
    return len(json.loads(row[0])) if row else 0


def run(args: argparse.Namespace, extra: list[str], work: Path, summary: dict,
        tracked: list) -> int:
    repo = args.repo.resolve()
    prep = work / "prepare"
    seed, cpg = prep / "state.db", prep / "cpg.bin"
    started = time.time()
    ensure_ports_free([args.base_port])
    server = serve(args.joern, args.heap, args.base_port, prep, tracked)
    wait_servers([(server, args.base_port, prep)])
    prepare = launch(slicer_command(repo, prep / "slices.json", args.base_port, args.joern, seed,
                                    extra, "--prepare-only", "--cpg-out", str(cpg)),
                     None, tracked)
    code = prepare.wait()
    tracked.remove(prepare)
    stop_all([server], tracked, summary)
    summary.update(prepare_exit=code, prepare_seconds=round(time.time() - started))
    # --prepare-only always ends `partial` (2): it slices nothing by design.
    if code not in (0, 2) or not cpg.is_file():
        raise PipelineError(f"prepare failed (slicer exit {code}, CPG saved: {cpg.is_file()}); "
                            f"see {prep}")
    summary["candidates"] = candidate_count(seed)
    summary.update(host_reading(args, summary["candidates"]))
    count = summary["shards"]
    ports = [args.base_port + 1 + i for i in range(count)]
    summary["ports"] = ports
    if ports[-1] > 65535:
        raise PipelineError(f"--base-port {args.base_port} + {count} shards passes port 65535")
    ensure_ports_free(ports)
    claims = work / "claims.db"
    servers = []
    boot = time.time()
    for index, port in enumerate(ports):
        folder = work / f"shard{index}"
        folder.mkdir(parents=True)
        shutil.copy(seed, folder / "state.db")
        servers.append((serve(args.joern, args.heap, port, folder, tracked), port, folder))
    wait_servers(servers)
    summary["servers_seconds"] = round(time.time() - boot)
    shard_started = time.time()  # the wall clock starts once every server is up

    def command(index: int, skip: Path | None = None) -> list[str]:
        return shard_command(repo, work / f"shard{index}", ports[index], args.joern, cpg, extra,
                             index, count, claims, skip)

    logs = [work / f"shard{index}" / "slicer.log" for index in range(count)]
    shards = [launch(command(index), logs[index], tracked) for index in range(count)]
    deadline = shard_started + args.wall_seconds if args.wall_seconds else None
    restarts = (DeadlineRestarts(args, work, ports, command, tracked, summary, shard_started,
                                 deadline, logs) if args.restart_on_deadline else None)
    wall_stopped: list[int] = []
    while any(p.poll() is None for p in shards):
        if deadline and time.time() > deadline:
            wall_stopped = [i for i, p in enumerate(shards) if p.poll() is None]
            stop_all([shards[i] for i in wall_stopped], summary=summary)
            break
        if restarts:
            restarts.poll(shards, servers)
        time.sleep(POLL_SECONDS)
    codes = [p.wait() for p in shards]
    for process in shards:
        if process in tracked:  # a restart that failed took its killed slicer out already
            tracked.remove(process)
    stop_all([entry[0] for entry in servers if entry], tracked, summary)
    # Normal shard exit is 2: finish() always reports a shard as partial. 1 = the slicer failed,
    # negative = killed by a signal we did not send.
    dead = [i for i, c in enumerate(codes) if c not in (0, 2) and i not in wall_stopped]
    summary.update(shard_exit_codes=codes, wall_stopped_shards=wall_stopped, dead_shards=dead,
                   dead_shard_logs=[str(logs[i]) for i in dead],
                   shard_seconds=round(time.time() - shard_started))
    merged = work / "merged.db"
    merge = launch([sys.executable, str(MERGE), str(merged),
                    *[str(work / f"shard{i}" / "state.db") for i in range(count)]],
                   None, tracked, stdout=subprocess.PIPE, text=True)
    output, _ = merge.communicate()
    tracked.remove(merge)
    summary["merge_exit"] = merge.returncode
    try:
        counters = json.loads(output.strip().splitlines()[-1])
        summary["merge"] = {k: counters[k] for k in ("candidates", "complete")}
    except (IndexError, ValueError, KeyError, TypeError):
        raise PipelineError(f"merge printed no counters (exit {merge.returncode}): "
                            f"{output[-500:]!r}") from None
    if merge.returncode not in (0, 2) or not merged.is_file():
        raise PipelineError(f"merge failed (exit {merge.returncode})")
    export = launch([sys.executable, str(SLICER), "--repo", str(repo), "--out", str(args.out),
                     "--checkpoint", str(merged), "--export-only"], None, tracked)
    summary["export_exit"] = export.wait()
    tracked.remove(export)
    # Export of a merge reports `partial` (2) as well: the shards' notes are in the merged errors.
    if summary["export_exit"] not in (0, 2) or not args.out.is_file():
        raise PipelineError(f"export failed (exit {summary['export_exit']})")
    if len(dead) == count:
        summary["error"] = "every shard died; --out holds only degraded slices"
        return EXIT_FAILED
    missing = summary["merge"]["candidates"] - summary["merge"]["complete"]
    if missing:
        summary["incomplete"] = f"{missing} candidates not complete -> degraded in --out"
        return EXIT_INCOMPLETE
    return EXIT_OK


def _interrupt(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    args, extra = parse(list(sys.argv[1:] if argv is None else argv))
    work = args.work_dir.resolve()
    args.out = args.out.resolve()
    if args.dry_run:
        print(json.dumps({"extra": extra, **host_reading(args, None),
                          "note": "candidates term unknown until prepare"}))
        return EXIT_OK
    if work.exists() and any(work.iterdir()):
        parser().error(f"--work-dir {work} is not empty")
    work.mkdir(parents=True, exist_ok=True)
    summary: dict = {"repo": str(args.repo.resolve()), "extra": extra}
    tracked: list[subprocess.Popen] = []
    caught = [s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if s]
    previous = {s: signal.signal(s, _interrupt) for s in caught}
    started, code = time.time(), EXIT_FAILED
    try:
        code = run(args, extra, work, summary, tracked)
        return code
    except PipelineError as exc:
        summary["error"] = str(exc)
        return code
    except BaseException as exc:  # SystemExit from a signal, KeyboardInterrupt, a bug
        code = exc.code if isinstance(exc, SystemExit) else EXIT_FAILED
        summary["error"] = f"interrupted: {type(exc).__name__} {exc}"
        raise
    finally:
        for s in caught:
            signal.signal(s, signal.SIG_IGN)  # a second TERM must not cut the cleanup short
        stop_all(list(tracked), tracked, summary)
        for s, handler in previous.items():
            signal.signal(s, handler)
        summary.update(exit=code, total_seconds=round(time.time() - started))
        (work / "summary.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps(summary), file=sys.stderr if code else sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
