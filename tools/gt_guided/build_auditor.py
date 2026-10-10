#!/usr/bin/env python3
"""Auditor FT inputs for ONE sample (CPU only, no model): every slice -> the harness's own
`runner._auditor_target` + `_auditor_source` with the harness's `config/budgets.yaml` thresholds,
plus the sample's description. The Auditor SOURCE options are whatever `budgets.yaml` sets (since
2026-10-04 the harness default turns them on), so a row is what a real run would assemble; the
environment can change them for comparisons -- AUDITOR_DEFINITIONS=1 and AUDITOR_OPTIONS=
"name=value,..." over `config.AUDITOR_SOURCE_OPTIONS`. The rows carry the options that were on
(`auditor_options`).
Ground truth is NOT used to build `source` from slices; it only labels the row afterwards. The
one exception is GT direct mode (`<slices.json>` = "-", rows say `gt_direct: true`): there is no
slicer, the slice IS the GT points (entry = the GT sink line), so the GT decides what `source`
carries and `gt_relevant` is true by construction.

One JSONL row per slice:
  sample_id, kind, slice_id, case, degraded, entry, anchor, source, description,
  functions/dropped/uncovered_lines/chars/anchor_chars (the auditor_source event),
  lines (slice lines), gt_relevant (s500 handoff rule: a slice line within +-2 of a GT point),
  gt_in_source (a GT point line is among the lines the SOURCE shows), harness_commit,
  slicer_run_signature, auditor_options, definitions/definitions_dropped, gt_guided (true when the
  slices were GT-filtered -- slices.json's gt_filter -- or the job says so).
Usage: build_auditor.py <harness> <job.json> <repo> <slices.json> <index.json> <out.jsonl.gz>
"""

import gzip
import json
import os
import re
import sys
import types
from pathlib import Path

HARNESS, JOB, REPO, SLICES, INDEX, OUT = map(Path, sys.argv[1:7])
sys.path.insert(0, str(HARNESS))
from src.adapters.fake_retriever import read_raw_source, read_symbol_source  # noqa: E402
from src.adapters.index_graph import GraphIndex  # noqa: E402
from src.adapters.slices import load_slices, slice_refs  # noqa: E402
from src.config import AUDITOR_SOURCE_OPTIONS, Thresholds  # noqa: E402
from src.orchestrator import runner  # noqa: E402
from src.schemas.candidate import SliceRef  # noqa: E402
from src.schemas.common import Location  # noqa: E402

job = json.load(open(JOB, encoding="utf-8"))
# a packed harness carries HARNESS_COMMIT; a git checkout answers rev-parse
_marker = HARNESS / "HARNESS_COMMIT"
if _marker.is_file():
    commit = _marker.read_text().split()[0]
else:
    import subprocess  # noqa: E402

    _rev = subprocess.run(
        ["git", "-C", str(HARNESS), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    )
    commit = _rev.stdout.strip() or "unknown"
graph = GraphIndex(INDEX)
# the harness's own budgets.yaml thresholds (as `load_config` reads them) -- since 2026-10-04 that
# file turns the Auditor SOURCE options on, so the rows match what a real run assembles. The
# environment below can still change them for comparisons.
import yaml  # noqa: E402

thresholds = Thresholds.model_validate(
    yaml.safe_load((HARNESS / "config" / "budgets.yaml").read_text(encoding="utf-8"))["thresholds"]
)
# AUDITOR_DEFINITIONS=1: also carry the #define / struct / file-variable definitions the carried
# code uses (#349 (f)) -- buffer sizes and field layout.
if os.environ.get("AUDITOR_DEFINITIONS") == "1":
    thresholds = thresholds.model_copy(update={"auditor_definitions": 1})
# AUDITOR_OPTIONS="auditor_callee_hops=1,auditor_caller_lines=10": any other #349 option by name.
# Validated through Thresholds (model_copy alone would skip the field bounds, e.g. hops <= 1).
# the harness's own list, so a new option needs no edit here
_OPTION_KEYS = set(AUDITOR_SOURCE_OPTIONS)


def with_options(base, spec):
    """`spec` = "name=value,..." over the #349 option names, validated through Thresholds
    (model_copy alone would skip the field bounds, e.g. hops <= 1)."""
    extra = {}
    for item in filter(None, spec.split(",")):
        key, _, value = item.strip().partition("=")
        if key not in _OPTION_KEYS:
            raise SystemExit(f"unknown Auditor option {key!r}")
        extra[key] = int(value)
    return Thresholds.model_validate({**base.model_dump(), **extra}) if extra else base


thresholds = with_options(thresholds, os.environ.get("AUDITOR_OPTIONS", ""))
# AUDITOR_ARMS="c4:opt=v,...;c4w5:opt=v,...": several option sets over the same slices, one row per
# slice per arm (`arm` names it) -- compares option sets without re-cloning or re-indexing
ARMS = [("", thresholds)]
if os.environ.get("AUDITOR_ARMS"):
    ARMS = []
    for part in filter(None, os.environ["AUDITOR_ARMS"].split(";")):
        name, _, spec = part.partition(":")
        ARMS.append((name.strip(), with_options(thresholds, spec)))
# event fields that name the row rather than count anything -- the row has its own ids
_EVENT_IDENTITY = frozenset({"stage", "subject_kind", "subject_id", "slice_id"})
BANNER = re.compile(r"^/\* ===== (.+):(\d+)-(\d+) ===== \*/$")
OMIT = re.compile(r"^/\* \.\.\. (\d+) lines omitted \.\.\. \*/$")


def ctx(thr):
    events = []
    c = types.SimpleNamespace(
        symbols_for=graph.symbols_for,
        callees_of=graph.callees_of,
        definitions_of=graph.definitions_of,
        call_sites_of=graph.call_sites_of,
        # same lookups `adapters.factory.build_deps` wires into a real run (C4 · C6 · C5(a) need
        # index-v3 tables; an older index answers None and those steps add nothing)
        locals_of=graph.locals_of,
        functions_named=graph.functions_named,
        indirect_calls_of=graph.indirect_calls_of,
        writes_of=graph.writes_of,
        field_writes_of=graph.field_writes_of,
        read_source=lambda loc: read_symbol_source(REPO, loc),
        # raw bytes, as `cli.py` wires `RunContext.read_raw` -- the GUARDS scan (`auditor_guards`)
        # reads them to see line-ending drift
        read_raw=lambda file: read_raw_source(REPO, file),
        counter_for_role=lambda role: None,
        config=types.SimpleNamespace(thresholds=thr),
        trace=types.SimpleNamespace(emit=lambda *a, **kw: events.append(kw)),
    )
    return c, events


def shown_lines(source, anchor):
    """(file, line) pairs the SOURCE shows. With banners a line counts only inside its banner's
    range -- the GUARDS list sits right after the entry function with no banner of its own, and its
    lines are not the source lines that follow the function."""
    lines = source.split("\n")
    if not lines or not BANNER.match(lines[0]):
        start = anchor.line_start or 1
        return {(anchor.file, start + i) for i in range(len(lines))}
    out, file, cur, end = set(), None, 0, -1
    for ln in lines:
        m = BANNER.match(ln)
        if m:
            file, cur, end = m[1], int(m[2]), int(m[3])
            continue
        o = OMIT.match(ln)
        if o:
            cur += int(o[1])
            continue
        if cur <= end:
            out.add((file, cur))
        cur += 1
    return out


def near_gt(lines):
    for p in job["gt_points"]:
        lo, hi = p["line"], p.get("line_end") or p["line"]
        if any(f == p["file"] and lo - 2 <= ln <= hi + 2 for f, ln in lines):
            return True
    return False


def gt_direct_ref():
    """No slicer: one synthetic slice whose entry is the GT sink line (root_cause if no sink) and
    whose lines are the GT points, so the harness assembler carries the GT function first and the
    functions of the other GT points after it. Rows say `gt_direct: true`."""
    pts = job["gt_points"]
    seed = next((p for p in pts if p["role"] == "sink"), None) or pts[0]
    lines = sorted(
        {
            (p["file"], ln)
            for p in pts
            for ln in range(p["line"], (p.get("line_end") or p["line"]) + 1)
        }
    )
    entry = Location(file=seed["file"], line_start=seed["line"])
    return SliceRef(
        slice_id="gt_direct",
        location=entry,
        given_end="sink",
        entry=entry,
        lines=tuple(lines),
    )


GT_DIRECT = str(SLICES) == "-"
if GT_DIRECT:
    signature, case_of, refs = None, {}, (gt_direct_ref(),)
    gt_guided = True
else:
    raw = load_slices(SLICES)  # SliceSet; plain slices.json (the driver gunzips)
    # load_slices keeps four fields and drops gt_filter -- but a row built from GT-filtered slices
    # must say so whatever the job says, or it mixes into data sliced by design
    gt_guided = bool(
        json.loads(SLICES.read_text(encoding="utf-8")).get("gt_filter", {}).get("gt_guided")
    )
    signature = raw.run_signature
    case_of = {s.id: s.case for s in raw.slices}
    refs = slice_refs(raw)
n = rel = shown_gt = 0
with gzip.open(OUT, "wt", encoding="utf-8") as out:
    for ref, (arm, thr) in ((r, a) for r in refs for a in ARMS):
        c, events = ctx(thr)
        target = types.SimpleNamespace(slice=ref, location=ref.location)
        anchor = runner._auditor_target(c, target)
        source = runner._auditor_source(c, ref, anchor, "cand")
        ev = events[-1] if events else {}
        shown = shown_lines(source, anchor)
        g_rel = near_gt(ref.lines)
        g_src = any(
            (p["file"], ln) in shown
            for p in job["gt_points"]
            for ln in range(p["line"], (p.get("line_end") or p["line"]) + 1)
        )
        row = {
            "sample_id": job["sample_id"],
            "kind": job.get("kind", "vuln"),
            "slice_id": ref.slice_id,
            "arm": arm or None,
            "case": case_of.get(ref.slice_id),
            "degraded": bool(ref.degraded),
            "entry": {
                "file": ref.entry.file,
                "line": ref.entry.line_start,
                "symbol": ref.entry.symbol,
            },
            "anchor": {
                "file": anchor.file,
                "line_start": anchor.line_start,
                "line_end": anchor.line_end,
                "symbol": anchor.symbol,
            },
            "source": source,
            "description": job["description"],
            # every count the harness emits on `auditor_source`, not a hand-kept list -- the list
            # missed `definitions_trimmed` (index-v4 enum trimming) in the 2026-10-04 run. A key the
            # event leaves out (option off) stays out of the row, as in the trace.
            **{k: v for k, v in ev.items() if k not in _EVENT_IDENTITY},
            "lines": [list(x) for x in ref.lines],
            "gt_relevant": g_rel,
            "gt_in_source": g_src,
            "harness_commit": commit,
            "slicer_run_signature": signature,
            "auditor_options": list(thr.auditor_source_options_on()),
            "auditor_source_tokens": thr.auditor_source_tokens,
            "definitions": ev.get("definitions"),
            "definitions_dropped": ev.get("definitions_dropped"),
            "gt_guided": gt_guided or bool(job.get("gt_guided", False)),
            "gt_direct": GT_DIRECT,
        }
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        n += 1
        rel += g_rel
        shown_gt += g_src
print(
    json.dumps(
        {
            "sample_id": job["sample_id"],
            "slices": len(refs),
            "rows": n,
            "gt_relevant": rel,
            "gt_in_source": shown_gt,
        }
    )
)
