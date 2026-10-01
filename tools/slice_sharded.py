"""Slice one repo with N Joern servers in one command (#370): prepare -> shards -> merge -> export.

    python tools/slice_sharded.py --repo /work/repo --out /work/slices.json \\
      --work-dir /work/run --joern /opt/joern/joern-cli/joern -- --max-flows-per-candidate 10

1. prepare (one server): `slicer.py --prepare-only` builds the CPG and discovers candidates once,
   saves the graph (#368)
2. shards (N servers): each starts from a copy of the prepared checkpoint, loads the saved graph
   (`--resume --cpg`) and takes candidates from one shared queue (`--claim-db`, #369)
3. merge the shard checkpoints, then `--export-only` writes `--out`. Candidates a shard claimed but
   did not finish (crash, `--wall-seconds`) come out degraded (#364) -- every shard started from the
   prepared checkpoint, so one stopped early cannot void the merge (accel-ppp, 2026-10-01).

Shard count `--shards auto` = min(cores // --cores-per-shard, memory // --memory-per-shard-gb,
candidates // --min-candidates-per-shard, --max-shards), at least 1. Cores come from the cgroup CPU
quota when there is one (a container's `nproc` can report the host's cores), memory from
MemAvailable. ⚠ The per-shard defaults are set values, not measured ones -- docs/slicer-README.md
「한 repo를 샤드로 나누기」 has the only reading (8 shards peaked near 47 cores, memory unmeasured).
Everything after `--` goes to every slicer.py call unchanged (analysis settings must match).
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLICER = ROOT / "slicer.py"
MERGE = ROOT / "tools" / "merge_slicer_shards.py"


def cgroup_cores(cpu_max: Path = Path("/sys/fs/cgroup/cpu.max")) -> float | None:
    """CPU quota in cores from cgroup v2 `cpu.max` ("<quota> <period>" or "max <period>")."""
    try:
        quota, period = cpu_max.read_text().split()[:2]
    except (OSError, ValueError):
        return None
    if quota == "max":
        return None
    return int(quota) / int(period)


def available_cores() -> float:
    affinity = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    quota = cgroup_cores()
    return min(quota, affinity) if quota else float(affinity or 1)


def available_memory_gb(meminfo: Path = Path("/proc/meminfo")) -> float | None:
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        return None
    return None


def auto_shards(
    cores: float, memory_gb: float | None, candidates: int, *, cores_per_shard: float,
    memory_per_shard_gb: float, min_candidates: int, max_shards: int,
) -> int:
    limits = [int(cores // cores_per_shard), candidates // min_candidates, max_shards]
    if memory_gb is not None:
        limits.append(int(memory_gb // memory_per_shard_gb))
    return max(1, min(limits))


def wait_port(port: int, seconds: float = 300) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), 1).close()
            return True
        except OSError:
            time.sleep(1)
    return False


def stop(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(30)
    except Exception:  # noqa: BLE001 -- already gone, or hung: escalate
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def serve(joern: Path, heap: str, port: int, cwd: Path) -> subprocess.Popen:
    cwd.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [str(joern), "-J-Xss64m", f"-J-Xmx{heap}", "--server", "--server-host", "127.0.0.1",
         "--server-port", str(port)],
        cwd=cwd, stdout=open(cwd / "joern.log", "w"), stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    if not wait_port(port):
        stop(server)
        raise RuntimeError(f"Joern server on port {port} did not come up (see {cwd}/joern.log)")
    return server


def slicer_command(repo: Path, out: Path, port: int, joern: Path, checkpoint: Path,
                   extra: list[str], *more: str) -> list[str]:
    return [sys.executable, str(SLICER), "--repo", str(repo), "--out", str(out),
            "--joern-endpoint", f"http://127.0.0.1:{port}", "--joern-version-command", str(joern),
            "--checkpoint", str(checkpoint), "--progress", str(out.parent / "progress.jsonl"),
            *more, *extra]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--repo", required=True, type=Path)
    result.add_argument("--out", required=True, type=Path)
    result.add_argument("--work-dir", required=True, type=Path)
    result.add_argument("--joern", required=True, type=Path, help="joern-cli/joern launcher")
    result.add_argument("--shards", default="auto", help="auto, or a number >= 1")
    result.add_argument("--heap", default="20g", help="Joern -Xmx per server")
    result.add_argument("--base-port", type=int, default=18500)
    result.add_argument("--cores-per-shard", type=float, default=4.0)
    result.add_argument("--memory-per-shard-gb", type=float, default=24.0)
    result.add_argument("--min-candidates-per-shard", type=int, default=20)
    result.add_argument("--max-shards", type=int, default=32)
    result.add_argument("--wall-seconds", type=float, help="stop shards still running after this")
    result.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    args = parser().parse_args(argv[: argv.index("--")] if "--" in argv else argv)
    if args.shards != "auto" and (not args.shards.isdigit() or int(args.shards) < 1):
        parser().error("--shards is auto or a number >= 1")
    work, repo = args.work_dir.resolve(), args.repo.resolve()
    prep = work / "prepare"
    seed, cpg = prep / "state.db", prep / "cpg.bin"
    summary: dict = {"repo": str(repo), "extra": extra}
    if args.dry_run:
        cores, memory = available_cores(), available_memory_gb()
        print(json.dumps({"cores": cores, "memory_gb": memory, "shards": args.shards}))
        return 0
    if work.exists() and any(work.iterdir()):
        parser().error(f"--work-dir {work} is not empty")
    started = time.time()
    server = serve(args.joern, args.heap, args.base_port, prep)
    try:
        code = subprocess.call(slicer_command(
            repo, prep / "slices.json", args.base_port, args.joern, seed, extra,
            "--prepare-only", "--cpg-out", str(cpg)))
    finally:
        stop(server)
    summary["prepare_seconds"] = round(time.time() - started)
    if not cpg.is_file():
        summary["error"] = f"prepare did not save a CPG (slicer exit {code}); see {prep}"
        print(json.dumps(summary), file=sys.stderr)
        return 1
    import sqlite3

    with sqlite3.connect(seed) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key='candidates'").fetchone()
    candidates = len(json.loads(row[0])) if row else 0
    count = int(args.shards) if args.shards != "auto" else auto_shards(
        available_cores(), available_memory_gb(), candidates,
        cores_per_shard=args.cores_per_shard, memory_per_shard_gb=args.memory_per_shard_gb,
        min_candidates=args.min_candidates_per_shard, max_shards=args.max_shards)
    summary.update(candidates=candidates, shards=count)
    claims = work / "claims.db"
    servers, shards = [], []
    shard_started = time.time()
    try:
        for index in range(count):
            folder = work / f"shard{index}"
            folder.mkdir(parents=True)
            shutil.copy(seed, folder / "state.db")
            servers.append(serve(args.joern, args.heap, args.base_port + 1 + index, folder))
        for index in range(count):
            folder = work / f"shard{index}"
            # One shard = a plain seeded run (--shard needs N >= 2); more = the shared queue.
            split = ["--shard", f"{index}/{count}", "--claim-db", str(claims)] if count > 1 else []
            shards.append(subprocess.Popen(
                slicer_command(repo, folder / "slices.json", args.base_port + 1 + index, args.joern,
                               folder / "state.db", extra, "--resume", "--cpg", str(cpg), *split),
                stdout=open(folder / "slicer.log", "w"), stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True))
        deadline = shard_started + args.wall_seconds if args.wall_seconds else None
        while any(p.poll() is None for p in shards):
            if deadline and time.time() > deadline:
                summary["wall_stopped"] = sum(p.poll() is None for p in shards)
                for p in shards:
                    if p.poll() is None:
                        stop(p)
                break
            time.sleep(5)
        summary["shard_exit_codes"] = [p.wait() for p in shards]
    finally:
        for server in servers:
            stop(server)
    summary["shard_seconds"] = round(time.time() - shard_started)
    merged = work / "merged.db"
    summary["merge_exit"] = subprocess.call(
        [sys.executable, str(MERGE), str(merged),
         *[str(work / f"shard{i}" / "state.db") for i in range(count)]])
    summary["export_exit"] = subprocess.call(
        [sys.executable, str(SLICER), "--repo", str(repo), "--out", str(args.out),
         "--checkpoint", str(merged), "--export-only"]) if merged.is_file() else None
    summary["total_seconds"] = round(time.time() - started)
    (work / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))
    return 0 if summary["export_exit"] in (0, 2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
