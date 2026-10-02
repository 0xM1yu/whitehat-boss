"""Record Joern HTTP payloads/timings; resume uses a fresh trace directory."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "slicer"))
import slicer  # noqa: E402


def main() -> int:
    trace = Path(sys.argv.pop(1))
    trace.mkdir(parents=True, exist_ok=False)
    original = slicer.JoernClient

    class RecordedClient(original):
        def query(self, stage, query):
            started = time.perf_counter()
            record = {"stage": stage, "started_epoch": time.time(), "query": query}
            try:
                value, logs = super().query(stage, query)
                record.update(payload=value, logs=logs)
                return value, logs
            except Exception as exc:
                record.update(error=str(exc), logs=getattr(exc, "logs", ""))
                raise
            finally:
                record["elapsed_seconds"] = time.perf_counter() - started
                with (trace / "responses.jsonl").open(
                    "a", encoding="utf-8", newline="\n"
                ) as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    slicer.JoernClient = RecordedClient
    try:
        code = slicer.main()
        (trace / "exit-code.txt").write_text(str(code) + "\n", encoding="utf-8")
        return code
    finally:
        slicer.JoernClient = original


if __name__ == "__main__":
    raise SystemExit(main())
