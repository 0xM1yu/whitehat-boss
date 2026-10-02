"""Join the checkpoints of `slicer.py --shard I/N` runs, then export the full result.

    python tools/merge_slicer_shards.py [--claim-db claims.db] merged.db shard0.db shard1.db ...
    python slicer.py --repo ... --out slices.json --checkpoint merged.db --export-only

Refuses shards whose run contract, EngineConfig, candidate list or prepared CPG (#368)
differ. Prints how many candidates are complete and exits 2 when some are not (a shard is
missing, crashed or still running) -- --export-only would then report them as unfinished. Each
incomplete candidate key is listed on stderr; with --claim-db (the shards' shared queue) also the
shard that claimed it and when. Such a candidate is re-sliced only by `--resume` of that same I/N
shard (no other shard takes a claimed candidate), then merge again.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slicer_checkpoint import Checkpoint, merge_shards  # noqa: E402
from slicer_claims import read_claims  # noqa: E402


def incomplete_keys(merged: Path) -> list[str]:
    with Checkpoint(merged, resume=True) as store:
        complete = {
            key for key, payload, _ in store.conn.execute("SELECT * FROM stages")
            if json.loads(payload).get("phase") == "complete"
        }
        return [
            c["candidate_key"] for c in store.get("candidates")
            if c["candidate_key"] not in complete
        ]


def main(argv: list[str]) -> int:
    claim_db = None
    if len(argv) > 2 and argv[1] == "--claim-db":
        claim_db = Path(argv[2])
        argv = [argv[0], *argv[3:]]
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    merged = Path(argv[1])
    summary = merge_shards(merged, [Path(p) for p in argv[2:]])
    print(json.dumps(summary))
    if summary["complete"] < summary["candidates"]:
        print(
            f"incomplete: {summary['candidates'] - summary['complete']} candidates not complete",
            file=sys.stderr,
        )
        claims = read_claims(claim_db) if claim_db else None
        for key in incomplete_keys(merged):
            if claims is None:
                print(f"  {key}", file=sys.stderr)
            elif key in claims:
                owner, claimed_at = claims[key]
                print(f"  {key}  claimed by {owner} at {claimed_at} -> --resume that shard",
                      file=sys.stderr)
            else:
                print(f"  {key}  unclaimed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
