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


def prioritize(candidates: list[dict], files: set[str]) -> tuple[list[dict], int]:
    """Candidates whose entry or any anchor is in `files` first (#377 option (b)).

    Stable: each tier keeps collect()'s order, and ids stay as collect() numbered them, so a
    candidate's slice does not depend on the list -- only when it is sliced does. That matters
    only when the run is cut short (a wall budget): what was not reached exports degraded.
    Returns the reordered list and the size of the first tier.
    """

    def first(c: dict) -> bool:
        return c["entry"]["file"] in files or any(a["file"] in files for a in c["anchors"])

    head = [c for c in candidates if first(c)]
    tail = [c for c in candidates if not first(c)]
    return head + tail, len(head)
