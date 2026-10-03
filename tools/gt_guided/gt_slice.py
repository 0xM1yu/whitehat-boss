#!/usr/bin/env python3
"""EMERGENCY / FT-data-only: GT-guided slicing (not the harness design -- do not merge anywhere).

Runs the harness slicer unchanged, except that right after candidate discovery it keeps only the
candidates near the ground truth, so a sample slices a handful of candidates instead of thousands.
Slices themselves are produced by the same Joern queries and come out in the same slices.json shape.

Kept: a candidate whose entry or any anchor lies inside a GT function range (the index function
containing a GT point), widened by MARGIN lines. If none match, the K candidates in a GT file
whose entry/anchor is closest to a GT line. The filter result goes to result["gt_filter"].

Usage: gt_slice.py <harness> <gt.json> <index.json> -- <slicer.py args...>
  gt.json = {"gt_points": [{"role","file","line","line_end"}...]}
"""

import json
import sys
from pathlib import Path

HARNESS, GT, INDEX = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
SLICER_ARGS = sys.argv[sys.argv.index("--") + 1 :]
sys.path.insert(0, str(HARNESS / "tools" / "slicer"))
import slicer  # noqa: E402
import slicer_pipeline  # noqa: E402

MARGIN, K = 5, 8
gt = json.load(open(GT, encoding="utf-8"))["gt_points"]
index = json.load(open(INDEX, encoding="utf-8"))
ranges = []  # (file, lo, hi) for each GT point: its enclosing function, else +-30 lines
for p in gt:
    line, end = p["line"], p.get("line_end") or p["line"]
    best = None
    for s in index.get("symbols", []):
        if s.get("file") == p["file"] and s["line_start"] <= line <= s["line_end"]:
            if best is None or (s["line_end"] - s["line_start"]) < (
                best["line_end"] - best["line_start"]
            ):
                best = s
    if best:
        ranges.append((p["file"], best["line_start"] - MARGIN, best["line_end"] + MARGIN))
    else:
        ranges.append((p["file"], line - 30, end + 30))
gt_files = {p["file"] for p in gt}


def points(c):
    return [(c["entry"]["file"], c["entry"]["line"])] + [
        (a["file"], a["line"]) for a in c["anchors"]
    ]


def near(c):
    return any(f == rf and lo <= ln <= hi for f, ln in points(c) for rf, lo, hi in ranges)


def distance(c):
    d = [abs(ln - p["line"]) for f, ln in points(c) for p in gt if f == p["file"]]
    return min(d) if d else None


original = slicer_pipeline.SlicingStages.collect_candidates


def collect_gt(self):
    original(self)
    every = self.candidates
    kept = [c for c in every if near(c)]
    mode = "gt_function"
    if not kept:
        scored = sorted((d, i) for i, c in enumerate(every) if (d := distance(c)) is not None)
        kept = [every[i] for _, i in scored[:K]]
        mode = "nearest_in_gt_file" if kept else "none"
    self.candidates = kept
    self.result["candidate_manifest"] = {c["candidate_key"]: c for c in kept}
    self.result["gt_filter"] = {
        "mode": mode,
        "discovered": len(every),
        "kept": len(kept),
        "gt_ranges": [list(r) for r in ranges],
        "gt_guided": True,
    }


slicer_pipeline.SlicingStages.collect_candidates = collect_gt
raise SystemExit(slicer.main(SLICER_ARGS))
