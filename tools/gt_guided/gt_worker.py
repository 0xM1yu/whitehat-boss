#!/usr/bin/env python3
"""EMERGENCY GT-guided FT data worker (box side). W worker threads; each owns one Joern server port.
A worker claims the next job file from inbox/ (atomic rename into running/), then:
  clone (depth 1) -> harness indexer -> gt_slice.py (one Joern server, GT-near candidates only)
  -> build_auditor.py (AUDITOR_DEFINITIONS=1) -> out/<sid>/{auditor_inputs.jsonl.gz, slices.json.gz,
  build.json} and finally out/<sid>/DONE. The coordinator on the user's PC keeps inbox/ filled and
pulls finished out/<sid>/ in batches. With GT_DIRECT=1 there is no Joern step: clone -> indexer ->
build_auditor around the GT lines (no slices.json.gz). Usage: gt_worker.py <W>"""

import json
import os
import re
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
W = int(sys.argv[1])
HEAP = os.environ.get("HEAP", "20g")
HARD = int(os.environ.get("HARD_SECONDS", "3600"))
# CPG import runs under the query timeout; 600 s timed out mid-size repos (ImageMagick, binutils)
QUERY_TIMEOUT = os.environ.get("QUERY_TIMEOUT", "1800")
# repos with more C/C++ files than this keep only the GT directories (sparse checkout)
PRUNE_FILES = int(os.environ.get("PRUNE_FILES", "8000"))
INCLUDE_MAX = int(os.environ.get("INCLUDE_MAX", "3000"))
# headers the GT files include are kept too, followed this many levels (at most this many per level)
INCLUDE_ROUNDS = int(os.environ.get("INCLUDE_ROUNDS", "2"))
INCLUDE_FILES_MAX = int(os.environ.get("INCLUDE_FILES_MAX", "200"))
# GT_DIRECT=1: skip Joern; build_auditor assembles around the GT lines (its "-" slices mode).
# AUDITOR_ARMS / AUDITOR_OPTIONS in the environment reach build_auditor unchanged.
GT_DIRECT = os.environ.get("GT_DIRECT") == "1"
C_SUFFIXES = (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".inc")
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
    QUERY_TIMEOUT,
    "--candidate-timeout-seconds",
    "600",
]
for d in ("inbox", "running", "out", "work"):
    (R / d).mkdir(exist_ok=True)
lock = threading.Lock()


def log(msg):
    with lock:
        line = time.strftime("%F %T ") + msg
        print(line, flush=True)
        with open(R / "RUN.log", "a") as fh:
            fh.write(line + "\n")


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


def claim():
    with lock:
        for f in sorted((R / "inbox").glob("*.json")):
            dest = R / "running" / f.name
            try:
                f.rename(dest)
                return dest
            except OSError:
                continue
    return None


_INCLUDE = re.compile(r'^[ 	]*#[ 	]*include[ 	]*[<"]([^>"]+)[>"]', re.M)
_HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx", ".inc")


def _included_dirs(repo, start_files, files, rounds=INCLUDE_ROUNDS):
    """Directories of the headers `start_files` include, followed `rounds` levels deep. An include
    path is matched by suffix against the commit's file list (`<linux/usb.h>` ->
    `include/linux/usb.h`) and resolved to ONE header (`_pick`): the including file's own
    directory first, then the shortest path, x86/generic preferred on a tie -- matching every
    suffix kept all 25 arch trees plus `tools/` copies on a Linux sample (10,281 of 46,794 C files,
    143 s). Headers outside the repo (libc) match nothing. Blobs are read lazily from the partial
    clone."""
    headers = [f for f in files if f.endswith(_HEADER_SUFFIXES)]
    by_name: dict[str, list[str]] = {}
    for h in headers:
        by_name.setdefault(h.rsplit("/", 1)[-1], []).append(h)
    seen, frontier, dirs = set(start_files), list(start_files), set()
    for _ in range(rounds):
        nxt = []
        for f in frontier:
            text = subprocess.run(
                ["git", "cat-file", "-p", f"FETCH_HEAD:{f}"],
                cwd=repo,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=120,
            ).stdout
            here = f.rsplit("/", 1)[0] if "/" in f else ""
            for inc in _INCLUDE.findall(text):
                inc = inc.strip().lstrip("./")
                matches = [
                    h
                    for h in by_name.get(inc.rsplit("/", 1)[-1], ())
                    if h == inc or h.endswith("/" + inc)
                ]
                h = _pick(matches, here, inc)
                if h is not None and h not in seen:
                    seen.add(h)
                    nxt.append(h)
                    if "/" in h:
                        dirs.add(h.rsplit("/", 1)[0])
        frontier = nxt[:INCLUDE_FILES_MAX]
        if not frontier:
            break
    return dirs


def _pick(matches, here, inc):
    """One header for an include path: the including file's directory, else the shortest path
    (x86 or generic first on a tie). None when nothing in the repo matches."""
    if not matches:
        return None
    local = f"{here}/{inc}" if here else inc
    if local in matches:
        return local
    return min(
        matches,
        key=lambda h: (h.count("/"), not ("x86" in h or "generic" in h), h),
    )


def prune_dirs(repo, gt_points):
    """None if the commit has at most PRUNE_FILES C/C++ files; otherwise the directories to keep:
    each GT file's directory (recursive), the directories of the headers the GT files include
    (`_included_dirs`, so struct/macro definitions in `include/...` stay -- without them a pruned
    sample lost 70% of its definitions, 2026-10-04 local 281-sample check), plus a top-level
    include/ of at most INCLUDE_MAX files."""
    res = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "FETCH_HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        errors="replace",
    )
    files = [f for f in res.stdout.splitlines() if f.endswith(C_SUFFIXES)]
    if len(files) <= PRUNE_FILES:
        return None
    gt_files = sorted({p["file"] for p in gt_points})
    keep = {f.rsplit("/", 1)[0] for f in gt_files if "/" in f}
    try:
        keep |= _included_dirs(repo, gt_files, files)
    except (OSError, subprocess.SubprocessError):
        pass  # keep the GT dirs alone rather than fail the sample
    include = sum(f.startswith("include/") for f in files)
    if 0 < include <= INCLUDE_MAX:
        keep.add("include")
    keep = sorted(keep)
    kept = sum(any(f.startswith(k + "/") for k in keep) or "/" not in f for f in files)
    return {"c_files": len(files), "kept_c_files": kept, "keep": keep}


def process(jobfile, port):
    job = json.load(open(jobfile, encoding="utf-8"))
    sid = job["sample_id"]
    od, wd = R / "out" / sid, R / "work" / sid
    shutil.rmtree(wd, ignore_errors=True)
    shutil.rmtree(od, ignore_errors=True)
    od.mkdir(parents=True)
    wd.mkdir(parents=True)
    logf = od / "pipeline.log"
    st = {"sample_id": sid, "project": job.get("project"), "gt_guided": True}
    st["query_timeout"] = int(QUERY_TIMEOUT)
    t0 = time.time()
    repo = wd / "repo"
    repo.mkdir()
    sh(["git", "init", "-q"], cwd=repo, logf=logf)
    sh(["git", "remote", "add", "origin", job["repo_url"]], cwd=repo, logf=logf)
    # trees first (blobs on demand), so a huge repo can be cut down before its files arrive
    fetch = ["git", "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", job["commit"]]
    if sh(fetch, cwd=repo, timeout=1800, logf=logf) != 0:
        st["error"] = "clone failed"
        return st, od, wd
    pruned = prune_dirs(repo, job["gt_points"])
    if pruned is not None:
        # init --cone first: before git 2.37 (jammy = 2.34) `set` alone means non-cone
        sh(["git", "sparse-checkout", "init", "--cone"], cwd=repo, logf=logf)
        sh(["git", "sparse-checkout", "set", *pruned["keep"]], cwd=repo, logf=logf)
        st["pruned"] = pruned
    if sh(["git", "checkout", "-q", "FETCH_HEAD"], cwd=repo, timeout=1800, logf=logf) != 0:
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
    if GT_DIRECT:  # no Joern: the Auditor input is assembled around the GT lines straight away
        st["gt_direct"] = True
        return build(job, st, od, wd, repo, index, "-")
    json.dump({"gt_points": job["gt_points"]}, open(wd / "gt.json", "w"))
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
            if port_open(port):
                break
            time.sleep(1)
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
    out = json.load(open(wd / "slices.json", encoding="utf-8"))
    st["status"] = out.get("status")
    st["gt_filter"] = out.get("gt_filter")
    st["slices"] = len(out.get("slices", []))
    sh(["sh", "-c", f"gzip -c {wd}/slices.json > {od}/slices.json.gz"])
    if not st["slices"]:
        st["error"] = "no slices kept"
        return st, od, wd
    return build(job, st, od, wd, repo, index, wd / "slices.json")


def build(job, st, od, wd, repo, index, slices):
    json.dump(
        dict(job, kind="vuln", description=job.get("description", "")),
        open(wd / "job.json", "w"),
        ensure_ascii=False,
    )
    t3 = time.time()
    res = subprocess.run(
        [
            PY,
            str(TOOL / "build_auditor.py"),
            str(HARNESS),
            str(wd / "job.json"),
            str(repo),
            str(slices),
            str(index),
            str(od / "auditor_inputs.jsonl.gz"),
        ],
        capture_output=True,
        text=True,
        timeout=3600,
        env=dict(os.environ, AUDITOR_DEFINITIONS="1"),
    )
    st["build_rc"] = res.returncode
    st["build_s"] = round(time.time() - t3)
    try:
        st.update(
            {
                k: v
                for k, v in json.loads(res.stdout.strip().splitlines()[-1]).items()
                if k != "sample_id"
            }
        )
    except (ValueError, IndexError):
        st["error"] = "build failed: " + res.stderr[-500:]
    return st, od, wd


def worker(i):
    port = 19000 + i * 3
    while True:
        jobfile = claim()
        if jobfile is None:
            time.sleep(10)
            continue
        sid = jobfile.stem
        t = time.time()
        try:
            st, od, wd = process(jobfile, port)
        except Exception as exc:  # noqa: BLE001
            st, od, wd = {"sample_id": sid, "error": repr(exc)}, R / "out" / sid, R / "work" / sid
            od.mkdir(parents=True, exist_ok=True)
        st["total_s"] = round(time.time() - t)
        json.dump(st, open(od / "build.json", "w"), indent=1, ensure_ascii=False)
        (od / "DONE").write_text("1")
        shutil.rmtree(wd, ignore_errors=True)
        jobfile.unlink(missing_ok=True)
        log(
            f"{sid} {st.get('project')} slices={st.get('slices')} gt_src={st.get('gt_in_source')} "
            f"pruned={bool(st.get('pruned'))} err={st.get('error', '')[:60]} {st['total_s']}s"
        )


log(f"start W={W}")
for i in range(W):
    threading.Thread(target=worker, args=(i,), daemon=True).start()
while True:
    time.sleep(3600)
