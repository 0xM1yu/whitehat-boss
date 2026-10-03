"""EMERGENCY GT-guided FT data coordinator (runs on the user's PC).

- Keeps each box's inbox/ filled (>= 2 x its worker count) from one global job list, biggest
  repos first, so no box idles (dynamic: a fast box simply drains more).
- Every loop, notes finished samples (out/<sid>/DONE). Whenever >= BATCH new ones are finished
  across boxes, pulls them (per-box tar of just those dirs, sha256-checked), drops the pulled dirs
  on the box, and writes Downloads/ft_gt_auditor_batchNNN.tgz.
- A job running longer than STALE_S, or on a box unreachable for UNREACH_S, goes back to the queue.
- When every job is finished and pulled, destroys the boxes (only after the last pull verified).
State: state.json next to this file (resumable).
Usage: py -3.11 coord.py <jobs.json> <boxes.json>
  boxes.json = [{"id": 123, "host": "1.2.3.4", "port": 22, "workers": 16}, ...]
"""

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOBS = json.load(open(sys.argv[1], encoding="utf-8"))
BOXES = json.load(open(sys.argv[2], encoding="utf-8"))
STATE = HERE / "state.json"
DL = Path(os.path.expanduser("~")) / "Downloads"
BATCH, STALE_S, UNREACH_S, LOOP_S = 100, 3 * 3600, 1800, 45
SSH = [
    "ssh",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=20",
    "-o",
    "ServerAliveInterval=15",
]
SCP = [
    "scp",
    "-q",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=20",
    "-o",
    "ServerAliveInterval=15",
]

st = (
    json.load(open(STATE))
    if STATE.exists()
    else {
        "pending": [j["sample_id"] for j in sorted(JOBS, key=lambda j: -j.get("size_kb", 0))],
        "assigned": {},
        "finished": {},
        "pulled": [],
        "batches": 0,
        "last_seen": {},
    }
)
by_id = {j["sample_id"]: j for j in JOBS}


def save():
    tmp = STATE.with_suffix(".tmp")
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def log(msg):
    line = time.strftime("%F %T ") + msg
    print(line, flush=True)
    with open(HERE / "COORD.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def ssh(box, cmd, timeout=120):
    r = subprocess.run(
        SSH + ["-p", str(box["port"]), f"root@{box['host']}", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode, r.stdout


def push(box, sids):
    with tempfile.TemporaryDirectory() as t:
        for s in sids:
            json.dump(
                dict(by_id[s], gt_guided=True), open(Path(t) / f"{s}.json", "w"), ensure_ascii=False
            )
        tar = Path(t) / "jobs.tar"
        with tarfile.open(tar, "w") as tf:
            for s in sids:
                tf.add(Path(t) / f"{s}.json", arcname=f"{s}.json")
        r = subprocess.run(
            SCP + ["-P", str(box["port"]), str(tar), f"root@{box['host']}:/root/gt/jobs_in.tar"],
            capture_output=True,
            timeout=300,
        )
        if r.returncode:
            return False
        rc, _ = ssh(
            box,
            "cd /root/gt && mkdir -p stage && tar -xf jobs_in.tar -C stage"
            " && mv stage/*.json inbox/ && rm -f jobs_in.tar",
        )
        return rc == 0


def pull(box, sids, dest_dir):
    name = f"pull_{int(time.time())}.tgz"
    lst = " ".join(sids)
    rc, out = ssh(
        box,
        f"cd /root/gt/out && tar czf /root/gt/{name} {lst}"
        f" && sha256sum /root/gt/{name} | cut -d' ' -f1",
        timeout=1800,
    )
    if rc:
        return None
    want = out.strip().split()[-1]
    local = dest_dir / f"{box['id']}_{name}"
    for _ in range(8):
        r = subprocess.run(
            SCP + ["-P", str(box["port"]), f"root@{box['host']}:/root/gt/{name}", str(local)],
            capture_output=True,
            timeout=3600,
        )
        if r.returncode == 0 and hashlib.sha256(local.read_bytes()).hexdigest() == want:
            ssh(box, f"cd /root/gt && rm -f {name} && cd out && rm -rf {lst}", timeout=600)
            return local
        time.sleep(5)
    return None


def write_batch(parts):
    st["batches"] += 1
    out = DL / f"ft_gt_auditor_batch{st['batches']:03d}.tgz"
    with tarfile.open(out, "w") as tf:
        for p in parts:
            tf.add(p, arcname=p.name)
    for p in parts:
        p.unlink()
    log(f"batch {st['batches']:03d} -> {out} ({out.stat().st_size // 1048576} MB)")


log(f"start: jobs {len(JOBS)} boxes {len(BOXES)} pending {len(st['pending'])}")
while True:
    now = time.time()
    newly_done = {}
    for box in BOXES:
        key = str(box["id"])
        try:
            rc, out = ssh(
                box,
                "cd /root/gt && ls inbox | wc -l && ls running | wc -l"
                " && ls out/*/DONE 2>/dev/null | cut -d/ -f2",
            )
        except subprocess.TimeoutExpired:
            rc, out = 1, ""
        if rc:
            if now - st["last_seen"].get(key, now) > UNREACH_S:
                back = [s for s, a in st["assigned"].items() if a["box"] == key]
                for s in back:
                    del st["assigned"][s]
                st["pending"] = back + st["pending"]
                if back:
                    log(f"box {key} unreachable -> requeued {len(back)}")
            continue
        st["last_seen"][key] = now
        lines = out.split("\n")
        inbox, running = int(lines[0]), int(lines[1])
        done = [x for x in lines[2:] if x]
        for s in done:
            if s not in st["finished"] and s in st["assigned"]:
                st["finished"][s] = key
                del st["assigned"][s]
                newly_done.setdefault(key, []).append(s)
        for s, a in list(st["assigned"].items()):
            if a["box"] == key and now - a["t"] > STALE_S:
                del st["assigned"][s]
                st["pending"].insert(0, s)
                log(f"{s} stale on box {key} -> requeued")
        want = 2 * box["workers"] - inbox
        if want > 0 and st["pending"]:
            take, st["pending"] = st["pending"][:want], st["pending"][want:]
            if push(box, take):
                for s in take:
                    st["assigned"][s] = {"box": key, "t": now}
            else:
                st["pending"] = take + st["pending"]
    unpulled = [s for s in st["finished"] if s not in st["pulled"]]
    all_done = not st["pending"] and not st["assigned"]
    if len(unpulled) >= BATCH or (all_done and unpulled):
        parts, ok = [], True
        tmpdir = DL / "ft_gt_tmp"
        tmpdir.mkdir(exist_ok=True)
        for box in BOXES:
            mine = [s for s in unpulled if st["finished"][s] == str(box["id"])]
            for i in range(0, len(mine), 200):
                got = pull(box, mine[i : i + 200], tmpdir)
                if got is None:
                    ok = False
                    log(f"pull failed on box {box['id']}")
                else:
                    parts.append(got)
                    st["pulled"] += mine[i : i + 200]
        if parts:
            write_batch(parts)
        save()
    total = len(JOBS)
    log(
        f"pending {len(st['pending'])} assigned {len(st['assigned'])} "
        f"finished {len(st['finished'])}/{total} "
        f"pulled {len(st['pulled'])} batches {st['batches']}"
    )
    save()
    if all_done and len(st["pulled"]) == len(st["finished"]):
        log("all finished and pulled -> destroying boxes")
        for box in BOXES:
            r = subprocess.run(
                ["vastai", "destroy", "instance", str(box["id"])],
                input="y\n",
                capture_output=True,
                text=True,
                env=dict(os.environ, PYTHONUTF8="1"),
            )
            log(f"destroy {box['id']}: {r.stdout.strip()[-80:]}")
        break
    time.sleep(LOOP_S)
