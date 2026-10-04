#!/usr/bin/env python3
"""EMERGENCY GT-guided FT data worker (box side). W worker threads; each owns one Joern server port.
A worker claims the next job file from inbox/ (a hard link into running/, so a sample that is
already running is never claimed twice), then:
  clone (depth 1) -> harness indexer -> gt_slice.py (one Joern server, GT-near candidates only)
  -> build_auditor.py (AUDITOR_DEFINITIONS=1) -> out/<sid>/{auditor_inputs.jsonl.gz, slices.json.gz,
  build.json} and finally ONE marker file in out/<sid>/: DONE (ok) | FAILED (the sample itself
  failed: no slices, build failed) | RETRY (may not repeat: clone, a Joern that did not boot, an
  unexpected exception -- the coordinator runs it once more). The coordinator on the user's PC keeps
  inbox/ filled and pulls finished out/<sid>/ in batches.
One gt_worker.py per box (flock). Usage: gt_worker.py <W>"""

import gzip
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

R = Path("/root/gt")
PY = str(R / "venv/bin/python")
HARNESS = R / "harness"
TOOL = HARNESS / "tools" / "gt_guided"
JOERN = str(R / "joern/joern-cli/joern")
HEAP = os.environ.get("HEAP", "20g")
HARD = int(os.environ.get("HARD_SECONDS", "2400"))
MARKERS = ("DONE", "FAILED", "RETRY")
TRANSIENT = ("clone failed", "joern not up", "exception")
SLICER_ARGS = [
    "--threshold",
    "0.95",
    "--max-flows-per-candidate",
    "10",
    "--backward-depth",
    "30",
    "--backward-nodes",
    "5000",
    "--query-timeout-seconds",
    "600",
    "--candidate-timeout-seconds",
    "600",
]
lock = threading.Lock()  # log lines
claim_lock = threading.Lock()  # threads of this process; os.link covers other processes


def log(msg):
    """Never raises: a full disk or a closed stdout must not end a worker thread."""
    with lock:
        line = time.strftime("%F %T ") + msg
        try:
            print(line, flush=True)
            with open(R / "RUN.log", "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except (OSError, ValueError):
            pass


def sh(cmd, cwd=None, timeout=None, logf=None, env=None):
    with open(logf, "a") if logf else open(os.devnull, "w") as out:
        try:
            return subprocess.run(
                cmd, cwd=cwd, timeout=timeout, stdout=out, stderr=subprocess.STDOUT, env=env
            ).returncode
        except subprocess.TimeoutExpired:
            return -9


def port_open(port):
    try:
        socket.create_connection(("127.0.0.1", port), 1).close()
        return True
    except OSError:
        return False


def free_port(port):
    subprocess.run(["pkill", "-9", "-f", f"server-port {port}( |$)"], capture_output=True)
    time.sleep(1)


def tail(path, n=500):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-n:]
    except OSError:
        return ""


def claim():
    """Next job from inbox/, or None. Exclusive per sample: os.link fails when running/<sid>.json
    exists, where rename(2) would silently replace it (two runs of one sample rmtree each other's
    dirs). A job whose sample already has a result is dropped, not rerun over it."""
    with claim_lock:
        for f in sorted((R / "inbox").glob("*.json")):
            if any((R / "out" / f.stem / m).exists() for m in MARKERS):
                f.unlink(missing_ok=True)
                log(f"{f.stem}: already has a result, duplicate job dropped")
                continue
            dest = R / "running" / f.name
            try:
                os.link(f, dest)
            except (FileExistsError, FileNotFoundError):
                continue  # already running, or taken by another process
            f.unlink(missing_ok=True)
            return dest
    return None


def give_back(jobfile):
    try:
        os.replace(jobfile, R / "inbox" / jobfile.name)
    except OSError:
        pass


def requeue_running():
    """Claims left in running/ by a worker that died go back to inbox/ (holding the box lock)."""
    for f in (R / "running").glob("*.json"):
        give_back(f)
        log(f"{f.stem}: claim left by a dead worker -> back to inbox")


def process(jobfile, port):
    job = json.loads(Path(jobfile).read_text(encoding="utf-8"))
    sid = job["sample_id"]
    od, wd = R / "out" / sid, R / "work" / sid
    shutil.rmtree(wd, ignore_errors=True)
    shutil.rmtree(od, ignore_errors=True)
    od.mkdir(parents=True)
    wd.mkdir(parents=True)
    logf = od / "pipeline.log"
    st = {"sample_id": sid, "project": job.get("project"), "gt_guided": True}
    t0 = time.time()
    repo = wd / "repo"
    repo.mkdir()
    sh(["git", "init", "-q"], cwd=repo, logf=logf)
    sh(["git", "remote", "add", "origin", job["repo_url"]], cwd=repo, logf=logf)
    if (
        sh(
            ["git", "fetch", "-q", "--depth", "1", "origin", job["commit"]],
            cwd=repo,
            timeout=1800,
            logf=logf,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),  # a 401 must fail, not wait for a tty
        )
        != 0
        or sh(["git", "checkout", "-q", "FETCH_HEAD"], cwd=repo, logf=logf) != 0
    ):
        st["error"] = "clone failed"
        return st, od, wd
    st["clone_s"] = round(time.time() - t0)
    t1 = time.time()
    index = wd / "index.json"
    # same file selection as the harness index tool (_INDEX_SUFFIXES over sources()); the
    # indexer runs in-process so a large file list does not hit ARG_MAX
    indexer = HARNESS / "tools/harness-tools/indexer.py"
    code = "\n".join(
        [
            "import runpy, sys",
            f"sys.path.insert(0, {str(HARNESS)!r})",
            "from pathlib import Path",
            "from src.adapters.fake_tools import sources",
            "from src.adapters.container_tools import _INDEX_SUFFIXES",
            f"root = Path({str(repo)!r})",
            "files = [str(p) for p in sources(root) if p.suffix in _INDEX_SUFFIXES]",
            f"sys.argv = ['indexer.py', '--out', {str(index)!r}, '--root', str(root), *files]",
            f"runpy.run_path({str(indexer)!r}, run_name='__main__')",
        ]
    )
    sh([PY, "-c", code], logf=logf, timeout=1800)
    st["index_s"] = round(time.time() - t1)
    if not index.is_file():
        st["error"] = "index failed"
        return st, od, wd
    (wd / "gt.json").write_text(json.dumps({"gt_points": job["gt_points"]}), encoding="utf-8")
    free_port(port)
    t2 = time.time()
    jd = wd / "joern"
    jd.mkdir()
    server = subprocess.Popen(
        [
            JOERN,
            "-J-Xss64m",
            f"-J-Xmx{HEAP}",
            "--server",
            "--server-host",
            "127.0.0.1",
            "--server-port",
            str(port),
        ],
        cwd=jd,
        stdout=open(jd / "joern.log", "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        for _ in range(300):
            if server.poll() is not None or port_open(port):
                break
            time.sleep(1)
        if server.poll() is not None or not port_open(port):
            st["error"] = f"joern not up (rc={server.poll()}): {tail(jd / 'joern.log')}"
            return st, od, wd
        rc = sh(
            [
                PY,
                str(TOOL / "gt_slice.py"),
                str(HARNESS),
                str(wd / "gt.json"),
                str(index),
                "--",
                "--repo",
                str(repo),
                "--out",
                str(wd / "slices.json"),
                "--joern-endpoint",
                f"http://127.0.0.1:{port}",
                "--joern-version-command",
                JOERN,
                *SLICER_ARGS,
            ],
            cwd=HARNESS / "tools" / "slicer",
            timeout=HARD,
            logf=logf,
        )
    finally:
        try:
            os.killpg(server.pid, signal.SIGKILL)
        except Exception:
            pass
        free_port(port)
    st["slice_rc"] = rc
    st["slice_s"] = round(time.time() - t2)
    if not (wd / "slices.json").is_file():
        st["error"] = f"slicer produced no output (rc={rc})"
        return st, od, wd
    out = json.loads((wd / "slices.json").read_text(encoding="utf-8"))
    st["status"] = out.get("status")
    st["gt_filter"] = out.get("gt_filter")
    st["slices"] = len(out.get("slices", []))
    with open(wd / "slices.json", "rb") as src, gzip.open(od / "slices.json.gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    if not st["slices"]:
        st["error"] = f"no slices kept (status={st['status']})"
        return st, od, wd
    job_json = dict(job, kind="vuln", description=job.get("description", ""))
    (wd / "job.json").write_text(json.dumps(job_json, ensure_ascii=False), encoding="utf-8")
    t3 = time.time()
    built_gz = od / "auditor_inputs.jsonl.gz"
    try:
        res = subprocess.run(
            [
                PY,
                str(TOOL / "build_auditor.py"),
                str(HARNESS),
                str(wd / "job.json"),
                str(repo),
                str(wd / "slices.json"),
                str(index),
                str(built_gz),
            ],
            capture_output=True,
            text=True,
            timeout=3600,
            env=dict(os.environ, AUDITOR_DEFINITIONS="1"),
        )
    except subprocess.TimeoutExpired:
        st["error"] = "build failed: timed out"
        built_gz.unlink(missing_ok=True)
        return st, od, wd
    st["build_rc"] = res.returncode
    st["build_s"] = round(time.time() - t3)
    try:
        built = json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        built = None
    if res.returncode or built is None:
        st["error"] = "build failed: " + res.stderr[-500:]
        # the builder writes the .gz in place: a half-written one must not ride along
        built_gz.unlink(missing_ok=True)
    else:
        st.update({k: v for k, v in built.items() if k != "sample_id"})
    return st, od, wd


def marker_for(st):
    err = st.get("error")
    if not err:
        return "DONE"
    return "RETRY" if err.startswith(TRANSIENT) else "FAILED"


def run_one(jobfile, port):
    sid = jobfile.stem
    t = time.time()
    try:
        st, od, wd = process(jobfile, port)
    except Exception as exc:  # noqa: BLE001
        st = {"sample_id": sid, "error": f"exception: {exc!r}"}
        od, wd = R / "out" / sid, R / "work" / sid
        od.mkdir(parents=True, exist_ok=True)
    st["total_s"] = round(time.time() - t)
    (od / "build.json").write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
    marker = marker_for(st)
    (od / marker).write_text("1")  # last: the coordinator pulls a dir only once it has one
    shutil.rmtree(wd, ignore_errors=True)
    jobfile.unlink(missing_ok=True)
    log(
        f"{sid} {st.get('project')} slices={st.get('slices')} gt_src={st.get('gt_in_source')} "
        f"{marker} err={st.get('error', '')[:60]} {st['total_s']}s"
    )


def step(i):
    """One pass of worker i's loop. Nothing in it may end the thread: nobody notices a dead one (the
    coordinator only counts inbox files and markers) and the box keeps billing."""
    jobfile = None
    try:
        jobfile = claim()
        if jobfile is None:
            time.sleep(10)
            return
        run_one(jobfile, 19000 + i * 3)
    except Exception as exc:  # noqa: BLE001
        log(f"worker {i}: {type(exc).__name__}: {exc}")
        if jobfile is not None:
            # not stuck in running/: the next claim finds it, or drops it if it is done
            give_back(jobfile)
        time.sleep(10)


def worker(i):
    while True:
        step(i)


def single_instance():
    """A second gt_worker.py on this box would share inbox/ and the Joern ports (its pkill kills the
    first one's servers). flock, held for the life of the process (keep the returned file open)."""
    import fcntl  # POSIX only: the boxes are Linux

    fh = open(R / "worker.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("another gt_worker.py already runs on this box") from None
    return fh


def supervise(threads):
    """Block while every worker thread lives; when one dies exit non-zero instead of idling on."""
    while all(t.is_alive() for t in threads):
        time.sleep(30)
    log("a worker thread died -> exiting")
    raise SystemExit(1)


def main(argv):
    w = int(argv[1])
    for d in ("inbox", "running", "out", "work"):
        (R / d).mkdir(exist_ok=True)
    box_lock = single_instance()  # noqa: F841 - the open file holds the lock
    requeue_running()
    log(f"start W={w}")
    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(w)]
    for t in threads:
        t.start()
    supervise(threads)


if __name__ == "__main__":
    main(sys.argv)
