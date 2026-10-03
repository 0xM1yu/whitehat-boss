"""Persistent candidate identities use source coordinates, never CPG node IDs."""

from slicer_checkpoint import canonical, digest
from slicer_source import relative_file


def collect(rows_by_case: dict, repo) -> list[dict]:
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
            c["case"],
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

    Order only (this function; `--priority-case-a` acts earlier, inside the Case A discovery query).
    Stable: each tier keeps collect()'s order, and ids stay as collect() numbered
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
