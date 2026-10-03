"""Opt-in real Joern regression probe; never starts a server or executes C/C++.

Run against a dedicated server with the same absolute source paths. The output
directory must be new, so an earlier measurement cannot be overwritten.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SLICER_DIR = ROOT / "tools" / "slicer"
# Child mode: run slicer.py with the per-request builders behind slicer_library's interface.
INLINE_CHILD = "--inline-library-child"


class InlineLibrary:
    """The self-contained per-request queries the session library is generated from. A run
    through this must give the same slices as a run through the library (#322 review HIGH):
    the unit tests only prove the generated text, not what Joern computes with it."""

    def __init__(self, repo, max_flows, limits, max_depth, max_nodes, max_call_depth):
        import slicer_queries

        self.queries = slicer_queries
        self.repo, self.max_flows, self.limits = repo, max_flows, limits
        self.backward_limits = {"max_depth": max_depth, "max_nodes": max_nodes,
                                "max_call_depth": max_call_depth}
        self.definition = slicer_queries.envelope("library", "ujson.Bool(true)")

    def flows(self, scopes):
        return self.queries.flows_query(scopes, self.max_flows, self.limits)

    def context(self, points):
        return self.queries.context_query(points, self.repo)

    def backward(self, candidate):
        from slicer_backward import backward_b_query

        return backward_b_query(candidate, **self.backward_limits)


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute(args, source: Path, name: str, extra: list[str], inline: bool = False) -> dict:
    output = args.out_dir / f"{name}.json"
    script = str(SLICER_DIR / "slicer.py")
    entry = [str(Path(__file__).resolve()), INLINE_CHILD] if inline else [script]
    command = [
        sys.executable, *entry, "--repo", str(source),
        "--out", str(output), "--joern-endpoint", args.endpoint,
        "--query-timeout-seconds", str(args.timeout), *extra,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    dump(args.out_dir / f"{name}.execution.json", {
        "command": command, "returncode": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr,
    })
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == {"ok": 0, "partial": 2, "failed": 1}[result["status"]]
    assert result["counters"]["queries_aborted"] == 0, result.get("error")
    return result


def validate_full(result: dict) -> None:
    assert result["status"] == "ok", result.get("error")
    assert result["counters"]["locations_unmapped"] == 0
    by_symbol = {item["entry"]["symbol"]: item for item in result["slices"]}
    assert "name_length" not in by_symbol["copy_name"]["code"]
    assert "copy_name" not in by_symbol["name_length"]["code"]
    assert "parse_header" in by_symbol["recv_request"]["code"]
    assert "1, 2, 3" in by_symbol["array_entry"]["code"]
    assert "11, 22, 33" in by_symbol["cpp_entry"]["code"]
    assert "unrelated_cpp" not in by_symbol["cpp_entry"]["code"]
    double = by_symbol["double_release"]
    assert "release_second(0, p)" in double["code"] and "free(p)" in double["code"]
    assert all("/* Analysis input only" not in item["code"] for item in result["slices"])


def validate_candidates(args) -> None:
    # The last CLI run leaves its fresh CPG selected. No other client may use it.
    sys.path.insert(0, str(SLICER_DIR))
    from slicer import JoernClient
    from slicer_queries import candidates_b_query

    # case_b is a file-transport stage: it writes its payload to a file the client reads,
    # so the client needs a writable payload dir (the server runs on this same box).
    payload_dir = args.out_dir / ".slicer-payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    client = JoernClient(args.endpoint, args.timeout, payload_dir=payload_dir)
    candidates, logs = client.query("case_b", candidates_b_query())
    dump(args.out_dir / "case-b.json", {"candidates": candidates, "logs": logs})
    frees = [c for c in candidates if c["symbol"] in ("free", "release_second")]
    assert len(frees) == 2, frees
    assert {c["scope"]["symbol"] for c in frees} == {"double_release"}, frees
    assert {c["symbol"] for c in candidates} >= {"consume", "consume_cpp"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:16240")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    args.out_dir = args.out_dir.resolve()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    source = args.out_dir / "source"
    shutil.copytree(ROOT / "tests/fixtures/target", source)
    shutil.copytree(ROOT / "tests/fixtures/slicer_live", source, dirs_exist_ok=True)
    inputs = [SLICER_DIR / name for name in ("slicer.py", "slicer_queries.py", "slicer_source.py")]
    inputs += [Path(__file__), *sorted(source.iterdir())]
    dump(args.out_dir / "manifest.json", {
        "python": sys.version,
        "sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
    })
    first = execute(args, source, "full-1", [])
    validate_full(first)
    validate_candidates(args)
    second = execute(args, source, "full-2", [])
    validate_full(second)
    assert first["slices"] == second["slices"], "Fresh CPGs produced different slices"
    # Fresh CPGs agree (above), so a difference here is the session library's alone.
    inline = execute(args, source, "inline-1", [], inline=True)
    validate_full(inline)
    assert inline["slices"] == first["slices"], "Session library and per-request queries differ"
    capped = execute(args, source, "cap-1", ["--max-flows-per-candidate", "1"])
    assert capped["status"] == "partial" and capped["counters"]["flows_truncated"] > 0
    assert any(item["truncated"] for item in capped["slices"])
    dump(args.out_dir / "checks.json", {"status": "passed", "checks": [
        "source context and initializers", "wrapper argument and declaration identity",
        "fresh CPG repeat determinism", "session library matches per-request queries",
        "observed flow cap and exit status",
    ]})
    print(f"Live checks passed: {args.out_dir}")


if __name__ == "__main__":
    if sys.argv[1:2] == [INLINE_CHILD]:
        sys.argv.pop(1)
        sys.path.insert(0, str(SLICER_DIR))
        import slicer
        import slicer_pipeline

        slicer_pipeline.Library = InlineLibrary
        raise SystemExit(slicer.main())
    main()
