"""Join the checkpoints of `slicer.py --shard I/N` runs, then export the full result.

    python tools/merge_slicer_shards.py merged.db shard0.db shard1.db ...
    python slicer.py --repo ... --out slices.json --checkpoint merged.db --export-only

Refuses shards whose run contract or candidate list differ. Prints how many candidates are
complete; --export-only reports the rest as unfinished.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slicer_checkpoint import merge_shards  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    print(json.dumps(merge_shards(Path(argv[1]), [Path(p) for p in argv[2:]])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
