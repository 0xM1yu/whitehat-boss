"""Persistent candidate identities use source coordinates, never CPG node IDs."""

from slicer_checkpoint import canonical, digest
from slicer_source import relative_file

# --case-order: the case is the first sort key, so this decides whether every Case A is sliced
# before every Case B (`ab`, the default) or the reverse. ids are numbered after the sort, so
# `ba` gives Case B the low numbers.
CASE_RANK = {"ab": {"A": 0, "B": 1}, "ba": {"B": 0, "A": 1}}


def collect(rows_by_case: dict, repo, case_order: str = "ab") -> list[dict]:
    rank = CASE_RANK[case_order]
    combined = {}
    for case, rows in rows_by_case.items():
        for row in rows:
            file = relative_file(row["file"], repo) or row["file"]
            scope = dict(row["scope"])
            selector = row.get("selector")
            if case == "B" and not isinstance(selector, dict):
                raise ValueError("Case B candidate has no exact call selector")
            identity = {
                "case": case,
                "file": file,
                "line": row["line"],
                "symbol": row["symbol"],
                "scope": scope,
                "selector": selector,
            }
            key = digest(identity)
            item = combined.setdefault(
                key,
                {
                    "candidate_key": key,
                    "case": case,
                    "entry": {"file": file, "line": row["line"], "symbol": row["symbol"]},
                    "scopes": [scope],
                    "selector": selector,
                    "anchors": [],
                    "trigger_arguments": row.get("trigger_arguments", []),
                    "sink_arguments": row.get("sink_arguments", []),
                    "reasons": row.get("reasons", ["source_entry"]),
                },
            )
            item["anchors"].extend(
                {"file": relative_file(p["file"], repo) or p["file"], "line": p["line"]}
                for p in row["anchors"]
            )
    ordered = sorted(
        combined.values(),
        key=lambda c: (
            rank[c["case"]],
            c["entry"]["file"],
            c["entry"]["line"],
            c["entry"]["symbol"],
            canonical(c["selector"]),
            c["candidate_key"],
        ),
    )
    for number, item in enumerate(ordered, 1):
        item["id"] = f"slice_{number:04d}"
        for field in ("anchors", "trigger_arguments", "sink_arguments", "reasons"):
            item[field] = sorted({canonical(p): p for p in item[field]}.values(), key=canonical)
    return ordered


def prioritize(
    candidates: list[dict], files: set[str]
) -> tuple[list[dict], int, set[str]]:
    """Candidates whose entry or any anchor is in `files` first (#377 option (b)).

    Order only. Stable: each tier keeps collect()'s order, and ids stay as collect() numbered
    them. The candidate set is unchanged, and a candidate that gets a full slice gets the same
    slice whether or not it was listed. Only which candidates end up DEGRADED can differ -- those
    not reached when the run is cut short (a wall budget, a kill), and those after a poison streak
    (slicer_pipeline POISON_STOP_STREAK degrades the rest, or a claim-mode shard stops claiming).

    Matching is EXACT on the normalized file string -- not substring, basename or glob. `files`
    holds what read_priority_files() produced, spelled as collect() spells candidate entry/anchor
    files. Returns the reordered list, the size of the first tier, and the subset of `files` that
    some candidate has as its entry or an anchor.
    """
    head, tail, matched = [], [], set()
    for c in candidates:
        hits = files & ({c["entry"]["file"]} | {a["file"] for a in c["anchors"]})
        matched |= hits
        (head if hits else tail).append(c)
    return head + tail, len(head), matched
