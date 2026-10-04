"""EMERGENCY GT-guided FT data coordinator (runs on the user's PC).

- Keeps each box's inbox/ filled (>= 2 x its worker count) from one global job list, biggest
  repos first, so no box idles (dynamic: a fast box simply drains more).
- Every loop, notes finished samples (out/<sid>/DONE) and, once PULL_MIN are waiting, pulls them
  (per-box tar of just those dirs, sha256-checked), drops the pulled dirs on the box and stages one
  <sid>.tgz per sample. Batches are packed by SIZE, not count: samples go into a batch while it
  stays under PART_MB, the one that would overflow starts the next batch. A full batch becomes
  Downloads/ft_gt_auditor_batchNNN.tar (one <sid>.tgz per sample inside) and goes to a Discord
  webhook with the file attached; a lone sample over the limit is split by notify().
  The webhook URL comes from DISCORD_WEBHOOK_URL or the first line of ~/.ft_gt_discord or
  ~/Documents/.ft_gt_discord (written by the user, outside the repository) and is never logged.
  A failed post is logged and never stops the run.
- A job running longer than STALE_S, or on a box unreachable for UNREACH_S, goes back to the queue.
- When every job is finished and pulled, destroys the boxes (only after the last pull verified).
State: state.json next to this file (resumable).
Usage: py -3.11 coord.py <jobs.json> <boxes.json>
       py -3.11 coord.py --test-notify [file]   (one message, optional attachment, then exit)
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
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# The team server's upload limit is 20 MB (user, 2026-10-03). A larger file goes up in PART_MB
# pieces, one message each, with the command to join them in the first message.
DISCORD_MAX_MB, PART_MB = 20, 19


def _webhook() -> str | None:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    home = Path(os.path.expanduser("~"))
    for f in (home / ".ft_gt_discord", home / "Documents" / ".ft_gt_discord"):
        if not url and f.is_file():
            words = f.read_text(encoding="utf-8").split()
            url = words[0] if words else None
    if url and url.startswith("https://") and "/api/webhooks/" in url:
        return url
    return None


def _form(boundary: str, text: str, name: str | None, blob: bytes | None) -> bytes:
    head = f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
    head += "Content-Type: application/json\r\n\r\n"
    body = head.encode() + json.dumps({"content": text[:1900]}).encode() + b"\r\n"
    if blob is not None:
        part = f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; '
        part += f'filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        body += part.encode() + blob + b"\r\n"
    return body + f"--{boundary}--\r\n".encode()


def _post(url: str, text: str, name: str | None = None, blob: bytes | None = None) -> str:
    boundary = uuid.uuid4().hex
    data = _form(boundary, text, name, blob)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "ft-gt-coord",
    }
    for _ in range(5):
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300):
                return "ok"
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(float(exc.headers.get("Retry-After", "5")))
                continue
            return f"HTTP {exc.code}"
        except OSError as exc:
            return type(exc).__name__
    return "rate limited"


def notify(text: str, attachment: Path | None = None) -> str:
    url = _webhook()
    if url is None:
        return "notify skipped (no Discord webhook URL)"
    if attachment is None:
        return f"notify {_post(url, text)}"
    blob = attachment.read_bytes()
    if len(blob) <= DISCORD_MAX_MB * 1048576:
        return f"notify {_post(url, text, attachment.name, blob)} (with file)"
    step = PART_MB * 1048576
    pieces = [blob[i : i + step] for i in range(0, len(blob), step)]
    names = [f"{attachment.name}.part{i + 1:02d}" for i in range(len(pieces))]
    join = (
        f"\n파일 {len(blob) // 1048576} MB → 조각 {len(pieces)}개. 받은 뒤 합치기:\n"
        f"Windows `copy /b {'+'.join(names)} {attachment.name}`\n"
        f"Linux·Mac `cat {attachment.name}.part* > {attachment.name}`"
    )
    results = [_post(url, text + join)]
    for i, (name, piece) in enumerate(zip(names, pieces, strict=True), 1):
        results.append(_post(url, f"{attachment.name} 조각 {i}/{len(pieces)}", name, piece))
    bad = [r for r in results if r != "ok"]
    return f"notify {len(pieces)} parts" + (f" — failed {bad}" if bad else " ok")


if len(sys.argv) > 1 and sys.argv[1] == "--test-notify":
    test_file = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    print(notify("[FT GT] 테스트 — gt_guided coord.py 디스코드 알림 설정 확인", test_file))
    raise SystemExit(0)

HERE = Path(__file__).resolve().parent
JOBS = json.load(open(sys.argv[1], encoding="utf-8"))
BOXES = json.load(open(sys.argv[2], encoding="utf-8"))
STATE = HERE / "state.json"
DL = Path(os.path.expanduser("~")) / "Downloads"
STAGE = DL / "ft_gt_tmp"  # one <sid>.tgz per pulled sample, waiting to be packed into a batch
PULLS = DL / "ft_gt_pulls"  # raw pulls, apart from STAGE so flush() never packs one
PULL_MIN, STALE_S, UNREACH_S, LOOP_S = 20, 3 * 3600, 1800, 45
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


def _extract(tf, dest):
    """extractall(filter="data") needs Python >= 3.11.4; older ones get the same refusal by hand."""
    try:
        tf.extractall(dest, filter="data")
        return
    except TypeError:
        pass
    for m in tf.getmembers():
        if (
            not (m.isfile() or m.isdir())
            or m.name.startswith(("/", "\\"))
            or ".." in Path(m.name).parts
        ):
            raise ValueError(f"refusing tar member {m.name!r}")
    tf.extractall(dest)


def needs_retry(build_json) -> bool:
    """Failed before candidate discovery (CPG import hit the query timeout) on a worker older than
    the 1800 s timeout, which did not record query_timeout. Retried once, never again."""
    try:
        b = json.loads(Path(build_json).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return b.get("query_timeout") is None and b.get("status") == "failed" and not b.get("gt_filter")


def requeue(sids):
    for s in sids:
        st["finished"].pop(s, None)
        st["assigned"].pop(s, None)
        if s in st["pulled"]:
            st["pulled"].remove(s)
        if s not in st["pending"]:
            st["pending"].insert(0, s)


def stage(pulled_tgz):
    """Split one pulled tar (several samples) into STAGE/<sid>.tgz, one per sample. Returns the
    (staged, retry) sample ids; a retry sample is not staged."""
    STAGE.mkdir(exist_ok=True)
    sids, retry = [], []
    with tempfile.TemporaryDirectory() as t, tarfile.open(pulled_tgz, "r:gz") as tf:
        _extract(tf, t)
        for d in sorted(Path(t).iterdir()):
            if d.is_dir():
                if needs_retry(d / "build.json"):
                    retry.append(d.name)
                    continue
                with tarfile.open(STAGE / f"{d.name}.tgz", "w:gz") as out:
                    out.add(d, arcname=d.name)
                sids.append(d.name)
    pulled_tgz.unlink()
    return sids, retry


def sample_stats(tgzs):
    n = gt = err = 0
    for p in tgzs:
        with tarfile.open(p, "r:gz") as tf:
            for m in tf.getmembers():
                if m.name.endswith("/build.json"):
                    b = json.load(tf.extractfile(m))
                    n += 1
                    gt += (b.get("gt_in_source") or 0) > 0
                    err += bool(b.get("error"))
    return n, gt, err


def flush(final: bool):
    """Pack staged samples into batches no larger than PART_MB: add samples while they fit, the
    one that would overflow starts the next batch. A batch is written only when it is full (the
    next sample does not fit) or at the end; a lone sample over the limit goes out by itself and
    notify() splits it."""
    limit = PART_MB * 1048576
    staged = sorted(STAGE.glob("*.tgz"))
    group, size = [], 0
    for p in staged:
        s = p.stat().st_size + 1024  # tar header + padding
        if group and size + s > limit:
            write_batch(group)
            group, size = [], 0
        group.append(p)
        size += s
    if group and final:
        write_batch(group)


def write_batch(tgzs):
    st["batches"] += 1
    n, gt, err = sample_stats(tgzs)
    out = DL / f"ft_gt_auditor_batch{st['batches']:03d}.tar"
    with tarfile.open(out, "w") as tf:
        for p in tgzs:
            tf.add(p, arcname=p.name)
    for p in tgzs:
        p.unlink()
    st["sent"] = st.get("sent", 0) + n
    mb = out.stat().st_size / 1048576
    log(f"batch {st['batches']:03d} -> {out} ({mb:.1f} MB, {n} samples)")
    note = notify(
        f"**[FT GT] batch {st['batches']:03d}** — 표본 {n}개 ({mb:.1f} MB) · "
        f"Auditor 입력에 GT 보임 {gt}개 · 오류 {err}개 · 누적 {st['sent']:,} / {len(JOBS):,}",
        out,
    )
    log(f"batch {st['batches']:03d}: {note}")
    save()


log(f"start: jobs {len(JOBS)} boxes {len(BOXES)} pending {len(st['pending'])}")
# a pull that landed but was not staged (crash in between): its samples are already gone from the
# box, so stage them now and mark them finished + pulled instead of waiting for them forever
for left in sorted(PULLS.glob("*_pull_*.tgz")) + sorted(STAGE.glob("*_pull_*.tgz")):
    box_id = left.name.split("_", 1)[0]
    staged, retry = stage(left)
    requeue(retry)
    for s in staged:
        st["assigned"].pop(s, None)
        st["finished"][s] = box_id
        if s not in st["pulled"]:
            st["pulled"].append(s)
    log(f"recovered unstaged pull {left.name}")
    save()
# samples staged by an older worker that failed on the CPG timeout go back to the queue
old_fail = []
for p in sorted(STAGE.glob("hs-*.tgz")):
    with tempfile.TemporaryDirectory() as t, tarfile.open(p, "r:gz") as tf:
        m = next((m for m in tf.getmembers() if m.name.endswith("/build.json")), None)
        if m is None:  # pulled without build.json: never finished
            old_fail.append(p)
            continue
        (Path(t) / "b.json").write_bytes(tf.extractfile(m).read())
        if needs_retry(Path(t) / "b.json"):
            old_fail.append(p)
if old_fail:
    requeue([p.name[: -len(".tgz")] for p in old_fail])
    for p in old_fail:
        p.unlink()
    log(f"requeued {len(old_fail)} staged samples that failed on the old CPG timeout")
    save()
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
    all_done = not st["pending"] and not st["assigned"]
    pulled_set = set(st["pulled"])
    unpulled = [s for s in st["finished"] if s not in pulled_set]
    if len(unpulled) >= PULL_MIN or (all_done and unpulled):
        PULLS.mkdir(exist_ok=True)
        for box in BOXES:
            mine = [s for s in unpulled if st["finished"].get(s) == str(box["id"])]
            for i in range(0, len(mine), 200):
                got = pull(box, mine[i : i + 200], PULLS)
                if got is None:
                    log(f"pull failed on box {box['id']} -> retry next loop")
                    continue
                _, retry = stage(got)
                st["pulled"] += mine[i : i + 200]
                requeue(retry)
                if retry:
                    log(f"requeued {len(retry)} samples that failed on the old CPG timeout")
                save()
    everything_pulled = all_done and len(st["pulled"]) == len(st["finished"])
    if STAGE.exists():
        flush(final=everything_pulled)
    log(
        f"pending {len(st['pending'])} assigned {len(st['assigned'])} "
        f"finished {len(st['finished'])}/{len(JOBS)} "
        f"pulled {len(st['pulled'])} sent {st.get('sent', 0)} batches {st['batches']}"
    )
    save()
    if everything_pulled and not (STAGE.exists() and any(STAGE.glob("*.tgz"))):
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
