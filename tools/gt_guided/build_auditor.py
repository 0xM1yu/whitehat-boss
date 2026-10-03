#!/usr/bin/env python3
"""Auditor FT inputs for ONE sample (CPU only, no model): every slice -> the harness's own
`runner._auditor_target` + `_auditor_source` with the harness's `config/budgets.yaml` thresholds,
plus the sample's description. All #349 Auditor source options stay at their default (off = the
s500 handoff shape) except `auditor_definitions`, which AUDITOR_DEFINITIONS=1 turns on (#define /
struct / file-variable definitions the carried code uses -- buffer sizes, field layout).
Ground truth is NOT used to build `source`; it only labels the row afterwards.

One JSONL row per slice:
  sample_id, kind, slice_id, case, degraded, entry, anchor, source, description,
  functions/dropped/uncovered_lines/chars/anchor_chars (the auditor_source event),
  lines (slice lines), gt_relevant (s500 handoff rule: a slice line within +-2 of a GT point),
  gt_in_source (a GT point line is among the lines the SOURCE shows), harness_commit,
  slicer_run_signature, auditor_options, definitions/definitions_dropped, gt_guided (from the job).
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
from src.adapters.fake_retriever import read_symbol_source  # noqa: E402
from src.adapters.index_graph import GraphIndex  # noqa: E402
from src.adapters.slices import load_slices, slice_refs  # noqa: E402
from src.config import Thresholds  # noqa: E402
from src.orchestrator import runner  # noqa: E402

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
# the harness's own budgets.yaml thresholds (as `load_config` reads them); no Auditor source
# option is set there, so every #349 option is at its default 0 = off (the s500 handoff shape)
import yaml  # noqa: E402

thresholds = Thresholds.model_validate(
    yaml.safe_load((HARNESS / "config" / "budgets.yaml").read_text(encoding="utf-8"))["thresholds"]
)
# AUDITOR_DEFINITIONS=1: also carry the #define / struct / file-variable definitions the carried
# code uses (#349 (f)) -- buffer sizes and field layout. Everything else stays at the default.
if os.environ.get("AUDITOR_DEFINITIONS") == "1":
    thresholds = thresholds.model_copy(update={"auditor_definitions": 1})
assert set(thresholds.auditor_source_options_on()) <= {"auditor_definitions"}, (
    thresholds.auditor_source_options_on()
)
OPTIONS = list(thresholds.auditor_source_options_on())
BANNER = re.compile(r"^/\* ===== (.+):(\d+)-(\d+) ===== \*/$")
OMIT = re.compile(r"^/\* \.\.\. (\d+) lines omitted \.\.\. \*/$")


def ctx():
    events = []
    c = types.SimpleNamespace(
        symbols_for=graph.symbols_for,
        callees_of=graph.callees_of,
        definitions_of=graph.definitions_of,
        call_sites_of=graph.call_sites_of,
        read_source=lambda loc: read_symbol_source(REPO, loc),
        counter_for_role=lambda role: None,
        config=types.SimpleNamespace(thresholds=thresholds),
        trace=types.SimpleNamespace(emit=lambda *a, **kw: events.append(kw)),
    )
    return c, events


def shown_lines(source, anchor):
    lines = source.split("\n")
    if not lines or not BANNER.match(lines[0]):
        start = anchor.line_start or 1
        return {(anchor.file, start + i) for i in range(len(lines))}
    out, file, cur = set(), None, 0
    for ln in lines:
        m = BANNER.match(ln)
        if m:
            file, cur = m[1], int(m[2])
            continue
        o = OMIT.match(ln)
        if o:
            cur += int(o[1])
            continue
        out.add((file, cur))
        cur += 1
    return out


def near_gt(lines):
    for p in job["gt_points"]:
        lo, hi = p["line"], p.get("line_end") or p["line"]
        if any(f == p["file"] and lo - 2 <= ln <= hi + 2 for f, ln in lines):
            return True
    return False


raw = load_slices(SLICES)  # SliceSet; plain slices.json (the driver gunzips)
signature = raw.run_signature
case_of = {s.id: s.case for s in raw.slices}
refs = slice_refs(raw)
n = rel = shown_gt = 0
with gzip.open(OUT, "wt", encoding="utf-8") as out:
    for ref in refs:
        c, events = ctx()
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
            **{
                k: ev.get(k)
                for k in ("functions", "dropped", "uncovered_lines", "chars", "anchor_chars")
            },
            "lines": [list(x) for x in ref.lines],
            "gt_relevant": g_rel,
            "gt_in_source": g_src,
            "harness_commit": commit,
            "slicer_run_signature": signature,
            "auditor_options": OPTIONS,
            "definitions": ev.get("definitions"),
            "definitions_dropped": ev.get("definitions_dropped"),
            "gt_guided": bool(job.get("gt_guided", False)),
        }
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        n += 1
        rel += g_rel
        shown_gt += g_src
print(
    json.dumps(
        {"sample_id": job["sample_id"], "slices": n, "gt_relevant": rel, "gt_in_source": shown_gt}
    )
)
