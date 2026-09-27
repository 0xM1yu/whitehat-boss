"""스모크 전용 — `HARNESS_SMOKE_STAGE_HOOK`이 per_stage 경계마다 불리는지(머지하지 않는다)."""

import sys
from pathlib import Path

import pytest

from tests.test_pipeline_e2e import _poolify, _run


def test_stage_hook_runs_before_scout_auditor_and_critic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "stages.log"
    hook = tmp_path / "hook.py"
    hook.write_text(f"import sys\nopen({str(log)!r}, 'a').write(sys.argv[1] + '\\n')\n")
    monkeypatch.setenv("HARNESS_SMOKE_STAGE_HOOK", f'"{sys.executable}" "{hook}"')

    _run(tmp_path / "run", configure=_poolify("per_stage"))

    stages = [int(s) for s in log.read_text().split()]
    assert stages[0] == 3 and 4 in stages and 5 in stages and stages[-1] == 7
    assert stages == sorted(stages), stages
