"""Sum the per-phase query timings a slicer run wrote to its --progress log.

    python tools/slicer_query_timings.py /work/results/progress.jsonl

Per stage: the wall time the client saw (`elapsed_seconds`), the Scala body time (`body_ms`),
and their difference -- REPL compile + HTTP + client validation, which the body cannot see.
Phases come from `mark()` and add up to the body minus `unmarked`; `*_inclusive` phases
overlap the marks and are listed apart. Every figure is a sum over the log, a calculated value.
"""

import json
import sys
from collections import defaultdict


def summarize(lines):
    stages = defaultdict(lambda: {"n": 0, "wall": 0.0, "body": 0.0, "phases": defaultdict(float),
                                  "counts": defaultdict(float)})
    for line in lines:
        event = json.loads(line)
        if event.get("event") != "query_end" or "timings" not in event:
            continue
        s, t = stages[event["stage"]], event["timings"]
        s["n"] += 1
        s["wall"] += event["elapsed_seconds"]
        s["body"] += t["body_ms"] / 1000
        for key, ms in t["timings_ms"].items():
            s["phases"][key] += ms / 1000
        for key, value in t["counts"].items():
            s["counts"][key] += value
    return stages


def main(path):
    with open(path, encoding="utf-8") as stream:
        stages = summarize(stream)
    for stage, s in sorted(stages.items()):
        marked = sum(v for k, v in s["phases"].items() if not k.endswith("_inclusive"))
        rows = [("wall (client)", s["wall"]), ("  outside body", s["wall"] - s["body"]),
                ("  body (Scala)", s["body"])]
        rows += [(f"    {k}", v) for k, v in s["phases"].items() if not k.endswith("_inclusive")]
        rows += [("    unmarked", s["body"] - marked)]
        rows += [(f"  {k}", v) for k, v in s["phases"].items() if k.endswith("_inclusive")]
        print(f"\n## {stage}  (queries: {s['n']})\n\n| part | seconds | share of wall |")
        print("| --- | ---: | ---: |")
        for name, seconds in rows:
            share = seconds / s["wall"] if s["wall"] else 0
            print(f"| {name} | {seconds:.2f} | {share:.1%} |")
        if s["counts"]:
            print("\ncounts: " + ", ".join(f"{k}={v:.0f}" for k, v in s["counts"].items()))


if __name__ == "__main__":
    main(sys.argv[1])
