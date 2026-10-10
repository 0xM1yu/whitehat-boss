"""EMERGENCY GT-guided FT data coordinator (runs on the user's PC).

- Keeps each box's inbox/ filled (>= 2 x its worker count) from one global job list, biggest
  repos first, so no box idles (dynamic: a fast box simply drains more).
- Every loop, notes the samples a box finished (out/<sid>/DONE = ok, FAILED = gave up, RETRY =
  transient failure, run once more) and, once PULL_MIN are waiting, pulls them (per-box tar of just
  those dirs, sha256-checked) and stages one <sid>.tgz per sample. Only AFTER the pull is saved to
  state.json is the box copy deleted -- a crash in between must not strand data. Batches are packed
  by SIZE, not count: samples go into a batch while it stays under PART_MB, the one that would
  overflow starts the next batch. A full batch becomes Downloads/ft_gt_auditor_batchNNN.tar (one
  <sid>.tgz per sample inside; an existing file is never replaced) and goes to a Discord webhook
  with the file attached; a lone sample over the limit is split by notify().
  The webhook URL comes from DISCORD_WEBHOOK_URL or the first line of ~/.ft_gt_discord or
  ~/Documents/.ft_gt_discord (written by the user, outside the repository) and is never logged.
  A failed post is logged and never stops the run.
- A job assigned longer than STALE_S ago (queue wait included), or on a box unreachable for
  UNREACH_S, goes back to the queue.
- When every job is pulled (the set in jobs.json, not a count) and a box holds nothing that was not
  pulled, destroys it with `vastai destroy instance` and saves at once. A box that cannot be looked
  at (ssh down, no worker ever started) or still holds something never pulled is NEVER destroyed
  automatically: after INSPECT_TRIES such passes in a row it is given up on (`gave_up`), the log
  says `destroy by hand: vastai destroy instance <id>` (look at it first) and the exit code is 1.
  The same happens after DESTROY_TRIES failed `vastai` calls. The run ends once every box is
  destroyed or given up.
State: state.json next to this file. It belongs to ONE jobs.json (its sha256 is stored): another
jobs.json refuses to start until state.json is moved away.
Usage: py -3.11 coord.py <jobs.json> <boxes.json>
       py -3.11 coord.py --test-notify [file]   (one message, optional attachment, then exit)
  boxes.json = [{"id": 123, "host": "1.2.3.4", "port": 22, "workers": 16}, ...]
"""

import hashlib
import json
import os
import shlex
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


HERE = Path(__file__).resolve().parent
STATE = HERE / "state.json"
DL = Path(os.path.expanduser("~")) / "Downloads"
# BATCH_PREFIX names this run's batches and temp dir, so a second run never mixes with the first
PREFIX = os.environ.get("BATCH_PREFIX", "ft_gt_auditor")
STAGE = DL / f"{PREFIX}_tmp"  # one <sid>.tgz per pulled sample, waiting to be packed into a batch
PULL_MIN, STALE_S, UNREACH_S, LOOP_S = 20, 3 * 3600, 1800, 45
# a transient failure (RETRY) runs MAX_TRIES times in all; vastai gets DESTROY_TRIES tries per box
MAX_TRIES, DESTROY_TRIES = 2, 3
# a box that cannot be looked at, or still holds something never pulled, is refused this many passes
# in a row (about 15 minutes at LOOP_S) before the coordinator stops waiting and tells the user
INSPECT_TRIES = 20
FAIL_LIMIT = 5  # the same loop step failing this many passes in a row stops the run
MARKERS = ("DONE", "FAILED", "RETRY")
POLL = (
    "cd /root/gt && ls inbox | wc -l"
    " && find out -mindepth 2 -maxdepth 2 \\( -name DONE -o -name FAILED -o -name RETRY \\)"
)
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

JOBS: list = []
BOXES: list = []
st: dict = {}
by_id: dict = {}
gave_up: list = []  # boxes vastai would not destroy -- the user does it by hand
destroy_fails: dict = {}
refused: dict = {}
failing: dict = {}


def init(jobs_file, boxes_file):
    """Load jobs, boxes and the state, then reconcile what a dead run left in STAGE.

    state.json belongs to ONE jobs.json (its sha256 is stored). A state left by another campaign
    must not decide anything here: a finished one (pending empty, pulled == finished) next to a new
    jobs.json made the first loop destroy every box with no job done."""
    global JOBS, BOXES, st, by_id
    raw = Path(jobs_file).read_bytes()
    JOBS = json.loads(raw.decode("utf-8"))
    BOXES = json.loads(Path(boxes_file).read_text(encoding="utf-8"))
    # ids go into `vastai destroy instance`: ASCII digits only (isdigit alone takes other scripts)
    if not all(str(b["id"]).isascii() and str(b["id"]).isdigit() for b in BOXES):
        raise SystemExit("every box id in boxes.json must be a number (the vast instance id)")
    by_id = {j["sample_id"]: j for j in JOBS}
    sha = hashlib.sha256(raw).hexdigest()
    if STATE.exists():
        st = json.loads(STATE.read_text(encoding="utf-8"))
        if st.get("jobs_sha256") != sha:
            raise SystemExit(
                f"{STATE} was made for another jobs.json (or by an older coord.py): move it away "
                "to start this campaign, or run with the jobs.json it belongs to"
            )
    else:
        st = {
            "jobs_sha256": sha,
            "pending": [j["sample_id"] for j in sorted(JOBS, key=lambda j: -j.get("size_kb", 0))],
            "assigned": {},
            "finished": {},
            "pulled": [],
            "batches": 0,
            "last_seen": {},
        }
    for key, empty in (("failed", {}), ("tries", {}), ("destroyed", [])):
        st.setdefault(key, empty)
    gave_up.clear()
    destroy_fails.clear()
    refused.clear()
    failing.clear()
    reabsorb()


def reabsorb():
    """Count the staged samples a dead run left behind.

    A run that died between staging and saving leaves STAGE/<sid>.tgz for a finished sample the
    state does not know as pulled -- count it (the box copy is still there until the save, so
    nothing is lost either way). Half-written files (.part) and raw pulls are dropped: the box
    still holds their data."""
    if not STAGE.exists():
        return
    for p in [*STAGE.glob("*.part"), *(STAGE / "pulling").glob("*")]:
        p.unlink(missing_ok=True)
    known = set(st["pulled"])
    for p in sorted(STAGE.glob("*.tgz")):
        sid = p.name.removesuffix(".tgz")
        if sid in st["finished"] and sid not in known:
            st["pulled"].append(sid)


def save():
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def log(msg):
    line = time.strftime("%F %T ") + msg
    print(line, flush=True)
    with open(HERE / "COORD.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def safely(what, fn, *args):
    """Run one step of the loop. A failure is logged and the loop goes on: this runs unattended and
    one hung ssh must not end it while the boxes bill. The same step failing FAIL_LIMIT passes in a
    row is not a hiccup (a bug, a corrupt file): it stops the run with the traceback."""
    try:
        result = fn(*args)
    except Exception as exc:  # noqa: BLE001
        failing[what] = failing.get(what, 0) + 1
        log(f"{what}: {type(exc).__name__}: {exc} ({failing[what]}/{FAIL_LIMIT})")
        if failing[what] >= FAIL_LIMIT:
            alive = [str(b["id"]) for b in BOXES if str(b["id"]) not in st["destroyed"]]
            cmds = ", ".join(f"vastai destroy instance {i}" for i in alive) or "none"
            log(
                f"STOPPING: {what} failed {FAIL_LIMIT} passes in a row; still alive: {cmds}"
                " -- pull their out/ first: a box may still hold outputs not yet pulled"
            )
            raise
        return None
    failing.pop(what, None)
    return result


def run(cmd, timeout, **kw):
    """subprocess.run that never raises for a hung or missing program: (returncode, stdout).
    124 = timed out, 127 = could not start."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""
    return r.returncode, r.stdout


def ssh(box, cmd, timeout=120):
    return run(SSH + ["-p", str(box["port"]), f"root@{box['host']}", cmd], timeout, text=True)


def scp(box, src, dst, timeout):
    return run(SCP + ["-P", str(box["port"]), src, dst], timeout)[0]


def push(box, sids):
    with tempfile.TemporaryDirectory() as t:
        for s in sids:
            job = json.dumps(dict(by_id[s], gt_guided=True), ensure_ascii=False)
            (Path(t) / f"{s}.json").write_text(job, encoding="utf-8")
        tar = Path(t) / "jobs.tar"
        with tarfile.open(tar, "w") as tf:
            for s in sids:
                tf.add(Path(t) / f"{s}.json", arcname=f"{s}.json")
        if scp(box, str(tar), f"root@{box['host']}:/root/gt/jobs_in.tar", 300):
            return False
        rc, _ = ssh(
            box,
            "cd /root/gt && mkdir -p stage && tar -xf jobs_in.tar -C stage"
            " && mv stage/*.json inbox/ && rm -f jobs_in.tar",
        )
        return rc == 0


def pull(box, sids):
    """Copy the samples' out/ dirs to STAGE/pulling (sha256-checked). The box copy stays: release()
    removes it once the pull is saved, so a crash in between strands nothing."""
    raw = STAGE / "pulling"
    raw.mkdir(parents=True, exist_ok=True)
    name = f"pull_{int(time.time())}.tgz"
    lst = " ".join(shlex.quote(s) for s in sids)
    rc, out = ssh(
        box,
        f"cd /root/gt/out && tar czf /root/gt/{name} {lst}"
        f" && sha256sum /root/gt/{name} | cut -d' ' -f1",
        timeout=1800,
    )
    words = out.split()
    want = words[-1] if words else ""
    if rc or len(want) != 64:  # `| cut` swallows a failed sha256sum: rc 0 and no output
        log(f"pull on box {box['id']}: remote tar/sha256 failed (rc {rc})")
        return None
    local = raw / f"{box['id']}_{name}"
    for _ in range(8):
        if scp(box, f"root@{box['host']}:/root/gt/{name}", str(local), 3600) == 0:
            got = hashlib.sha256(local.read_bytes()).hexdigest() if local.is_file() else ""
            if got == want:
                ssh(box, f"rm -f /root/gt/{name}")
                return local
            log(f"pull on box {box['id']}: sha256 mismatch ({got[:12]} != {want[:12]}), retrying")
        time.sleep(5)
    return None


def release(box, sids):
    """Delete pulled samples from the box. Only after the pull is saved. A failure leaves clutter on
    a box that is destroyed anyway (holdings() accepts pulled samples)."""
    lst = " ".join(shlex.quote(s) for s in sids)
    ssh(box, f"cd /root/gt/out && rm -rf {lst}", timeout=600)


# extractall(filter="data") needs Python >= 3.11.4 (tests turn this off to take the manual path)
TAR_FILTERS = hasattr(tarfile, "data_filter")


def _extract(tf, dest):
    """extractall(filter="data") needs Python >= 3.11.4. On an older interpreter (this team's
    Windows PC runs 3.11.0) refuse the same things by hand -- anything but a regular file or a
    directory, absolute paths, `..` -- and extract plainly."""
    if TAR_FILTERS:
        tf.extractall(dest, filter="data")
        return
    for m in tf.getmembers():
        if (
            not (m.isfile() or m.isdir())
            or m.name.startswith(("/", "\\"))
            or ".." in Path(m.name).parts
        ):
            raise ValueError(f"refusing tar member {m.name!r}")
    tf.extractall(dest)


def stage(pulled_tgz):
    """Split one pulled tar (several samples) into STAGE/<sid>.tgz, one per sample. A staged file
    appears whole or not at all (written as .part, then renamed). Returns the sample names found."""
    names = set()
    with tempfile.TemporaryDirectory() as t, tarfile.open(pulled_tgz, "r:gz") as tf:
        _extract(tf, t)
        for d in sorted(Path(t).iterdir()):
            if d.is_dir():
                part = STAGE / f"{d.name}.tgz.part"
                with tarfile.open(part, "w:gz") as out:
                    out.add(d, arcname=d.name)
                os.replace(part, STAGE / f"{d.name}.tgz")
                names.add(d.name)
    return names


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


def open_new_batch():
    """Create the next batch file exclusively. The counter lives in an untracked state.json that a
    clean checkout resets, and by then the box copy is gone: a batch on disk is never replaced."""
    while True:
        st["batches"] += 1
        out = DL / f"{PREFIX}_batch{st['batches']:03d}.tar"
        try:
            return out, tarfile.open(out, "x")
        except FileExistsError:
            log(f"{out.name} already exists -> next number")


def write_batch(tgzs):
    n, gt, err = sample_stats(tgzs)
    out, tf = open_new_batch()
    try:
        with tf:
            for p in tgzs:
                tf.add(p, arcname=p.name)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    for p in tgzs:
        p.unlink()
    st["sent"] = st.get("sent", 0) + n
    save()  # the batch is recorded before the slow upload that may fail
    mb = out.stat().st_size / 1048576
    log(f"batch {st['batches']:03d} -> {out} ({mb:.1f} MB, {n} samples)")
    try:
        note = notify(
            f"**[{PREFIX}] batch {st['batches']:03d}** — 표본 {n}개 ({mb:.1f} MB) · "
            f"Auditor 입력에 GT 보임 {gt}개 · 오류 {err}개 · 누적 {st['sent']:,} / {len(JOBS):,}",
            out,
        )
    except Exception as exc:  # noqa: BLE001
        note = f"notify raised {type(exc).__name__}: {exc}"
    log(f"batch {st['batches']:03d}: {note}")


def parse_poll(out):
    """(inbox count, [(sid, marker)]) from POLL's output, or None if it is not that."""
    lines = out.split("\n")
    try:
        inbox = int(lines[0])
    except (ValueError, IndexError):
        return None
    marks = []
    for line in lines[1:]:
        parts = line.strip().split("/")
        if len(parts) == 3 and parts[0] == "out" and parts[2] in MARKERS:
            marks.append((parts[1], parts[2]))
    return inbox, marks


def settle(box, key, sid, marker):
    """A box reports a finished sample. Only the box it was assigned to counts (another box may
    hold an old copy). DONE = ok, FAILED = gave up, RETRY = transient failure: run once more
    (MAX_TRIES in all), then it is a failure too. A failed sample is still pulled: its build.json
    says why."""
    a = st["assigned"].get(sid)
    if a is None or a["box"] != key:
        return
    if marker == "RETRY" and st["tries"].get(sid, 1) < MAX_TRIES:
        rc, _ = ssh(box, f"cd /root/gt/out && rm -rf {shlex.quote(sid)}")
        if rc == 0:  # the old result must be gone, or the worker drops the new job as a duplicate
            del st["assigned"][sid]
            st["pending"].insert(0, sid)
            log(f"{sid} failed transiently on box {key} -> requeued")
        return
    del st["assigned"][sid]
    st["finished"][sid] = key
    if marker != "DONE":
        st["failed"][sid] = marker
        log(f"{sid} {marker} on box {key}")


def poll(box, now):
    key = str(box["id"])
    rc, out = ssh(box, POLL)
    parsed = parse_poll(out) if rc == 0 else None
    if parsed is None:
        if rc == 0:
            log(f"box {key}: unreadable poll output {out[:80]!r}")
        if now - st["last_seen"].get(key, now) > UNREACH_S:
            back = [s for s, a in st["assigned"].items() if a["box"] == key]
            for s in back:
                del st["assigned"][s]
            st["pending"] = back + st["pending"]
            if back:
                log(f"box {key} unreachable -> requeued {len(back)}")
        return
    inbox, marks = parsed
    st["last_seen"][key] = now
    for sid, marker in marks:
        settle(box, key, sid, marker)
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
                st["tries"][s] = st["tries"].get(s, 0) + 1
        else:
            st["pending"] = take + st["pending"]


def pull_chunk(box, chunk):
    """Pull, stage, record, and only then delete the box copy."""
    key = str(box["id"])
    got = pull(box, chunk)
    if got is None:
        log(f"pull failed on box {key} -> retry next loop")
        return
    try:
        names = stage(got)
    finally:
        got.unlink(missing_ok=True)  # the box still has the data; never leave raw pulls to pile up
    if not set(chunk) <= names:
        log(f"pull on box {key}: {sorted(set(chunk) - names)} not in the tar -> retry next loop")
        return
    st["pulled"] += chunk
    save()
    release(box, chunk)
    # a pull can block for an hour; the box was reachable at its end
    st["last_seen"][key] = time.time()


def holdings(box, pulled):
    """What the box still holds that was never pulled ("inbox/<sid>"...), or None if it cannot be
    looked at."""
    rc, out = ssh(
        box,
        "cd /root/gt && echo '#inbox' && ls inbox && echo '#running' && ls running"
        " && echo '#out' && ls out",
    )
    if rc:
        return None
    where, left, seen = "", [], set()
    for line in (x.strip() for x in out.split("\n")):
        if line.startswith("#"):
            where = line[1:]
            seen.add(where)
        elif line and line.removesuffix(".json") not in pulled:
            left.append(f"{where}/{line.removesuffix('.json')}")
    # rc 0 with a cut-off listing would read as "nothing left"
    return left if seen == {"inbox", "running", "out"} else None


def destroy(box):
    try:
        r = subprocess.run(
            ["vastai", "destroy", "instance", str(box["id"])],
            input="y\n",
            capture_output=True,
            text=True,
            env=dict(os.environ, PYTHONUTF8="1"),
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"destroy {box['id']}: could not run vastai ({type(exc).__name__})")
        return False
    log(f"destroy {box['id']}: rc {r.returncode} {r.stdout.strip()[-80:]} {r.stderr.strip()[-80:]}")
    return r.returncode == 0


def destroy_all():
    """Destroy each box, but only after looking at what it still holds: a box that cannot be looked
    at, or holds anything not pulled, is NOT destroyed -- never automatically. After INSPECT_TRIES
    such passes in a row it is given up on: the user is told to look and destroy it by hand (exit
    code 1), because waiting forever leaves it billing. True when no box is left to try."""
    log("all finished and pulled -> destroying boxes")
    pulled = set(st["pulled"])
    for box in BOXES:
        key = str(box["id"])
        if key in st["destroyed"] or key in gave_up:
            continue
        left = holdings(box, pulled)
        if left is None or left:
            refused[key] = refused.get(key, 0) + 1
            what = (
                "cannot be looked at" if left is None else f"holds {left[:8]} that was never pulled"
            )
            log(f"box {key} NOT destroyed: it {what} ({refused[key]}/{INSPECT_TRIES})")
            if refused[key] >= INSPECT_TRIES:
                gave_up.append(key)
                log(
                    f"box {key} still refused after {INSPECT_TRIES} passes -- it may hold output "
                    f"that was never pulled: look at it, then destroy by hand: "
                    f"vastai destroy instance {key}"
                )
        else:
            refused.pop(key, None)  # in a row: a clean look starts the count over
            if destroy(box):
                st["destroyed"].append(key)
                save()  # at once: a restart must not look at a box that is already gone
                continue
            destroy_fails[key] = destroy_fails.get(key, 0) + 1
            if destroy_fails[key] >= DESTROY_TRIES:
                gave_up.append(key)
                log(
                    f"destroy FAILED {destroy_fails[key]} times for box {key} -- "
                    f"destroy by hand: vastai destroy instance {key}"
                )
    save()
    return all(str(b["id"]) in st["destroyed"] or str(b["id"]) in gave_up for b in BOXES)


def tick() -> bool:
    """One pass over the boxes. True when the run is over (every box destroyed or given up)."""
    now = time.time()
    for box in BOXES:
        if str(box["id"]) not in st["destroyed"]:
            safely(f"box {box['id']}", poll, box, now)
    all_done = not st["pending"] and not st["assigned"]
    pulled_set = set(st["pulled"])
    unpulled = [s for s in st["finished"] if s not in pulled_set]
    if len(unpulled) >= PULL_MIN or (all_done and unpulled):
        STAGE.mkdir(parents=True, exist_ok=True)
        for box in BOXES:
            mine = [s for s in unpulled if st["finished"][s] == str(box["id"])]
            for i in range(0, len(mine), 200):
                safely(f"pull on box {box['id']}", pull_chunk, box, mine[i : i + 200])
    # the SET of jobs, not a count: a state.json that does not cover jobs.json must not pass
    everything_pulled = all_done and set(st["pulled"]) == set(by_id)
    if STAGE.exists():
        safely("batch", flush, everything_pulled)
    log(
        f"pending {len(st['pending'])} assigned {len(st['assigned'])} "
        f"finished {len(st['finished'])}/{len(JOBS)} (failed {len(st['failed'])}) "
        f"pulled {len(st['pulled'])} sent {st.get('sent', 0)} batches {st['batches']}"
    )
    save()
    if everything_pulled and not (STAGE.exists() and any(STAGE.glob("*.tgz"))):
        return bool(safely("destroy", destroy_all))
    return False


def main(argv):
    if len(argv) > 1 and argv[1] == "--test-notify":
        test_file = Path(argv[2]) if len(argv) > 2 else None
        print(notify("[FT GT] 테스트 — gt_guided coord.py 디스코드 알림 설정 확인", test_file))
        return 0
    init(argv[1], argv[2])
    log(f"start: jobs {len(JOBS)} boxes {len(BOXES)} pending {len(st['pending'])}")
    while not tick():
        time.sleep(LOOP_S)
    return 1 if gave_up else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
