"""Interrupt a recorded slicer run right after the first flow_saved commit.

Resume equality is only meaningful if the interrupt lands after a durable stage
commit; raising inside save_stage would abort before SQLite committed. The patch
therefore calls the original first and raises on its return.
"""

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import slicer  # noqa: E402


def main() -> int:
    original = slicer.Slicer.save_stage
    fired = []

    def save_stage(self, candidate, state, phase):
        original(self, candidate, state, phase)
        if phase == "flow_saved" and not fired:
            fired.append(candidate["candidate_key"])
            raise KeyboardInterrupt("injected after the first flow_saved commit")

    slicer.Slicer.save_stage = save_stage
    try:
        runpy.run_path(
            str(Path(__file__).resolve().parent / "run_slicer_recorded.py"), run_name="__main__"
        )
    except SystemExit as exit_code:
        if not fired:
            print("No flow_saved stage was reached; nothing was interrupted", file=sys.stderr)
            return 1
        return int(exit_code.code or 0)
    finally:
        slicer.Slicer.save_stage = original
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
