"""The slicer lives in tools/slicer/; every tool that reaches it by path must still find it.

pytest's `pythonpath` puts tools/slicer on sys.path inside this process, which hides a wrong
`sys.path.insert` in a tool -- the import still succeeds and the test stays green while the real
script dies with ModuleNotFoundError. So each tool runs the way the docs run it: a fresh
interpreter that ignores PYTHONPATH (`-E`) from a directory that is not the repo root.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def run_by_path(script: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-E", "-X", "utf8", str(script), *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False,
    )


# Runs only a tool's top-level imports (`run_name` is not "__main__", so main() is skipped) with
# sys.path[0] = tools/, which is what `python tools/<script>` gives it, and reports where every
# slicer* module came from. A wrong `sys.path.insert` does NOT raise for `import slicer`: tools/
# holds the tools/slicer/ directory, which imports as an empty namespace package
# (`__file__` is None).
IMPORTS_ONLY = """
import runpy, sys
tools, script = sys.argv[1:3]
sys.path.insert(0, tools)
runpy.run_path(script, run_name="not_main")
for name, module in sorted(sys.modules.items()):
    if name.startswith("slicer"):
        print(name, getattr(module, "__file__", None))
"""


@pytest.mark.parametrize(
    "script", ["merge_slicer_shards.py", "run_slicer_recorded.py", "interrupt_after_flow_saved.py"]
)
def test_a_tool_loads_the_real_slicer_without_help_from_pythonpath(script, tmp_path):
    child = subprocess.run(
        [sys.executable, "-E", "-X", "utf8", "-c", IMPORTS_ONLY, str(TOOLS), str(TOOLS / script)],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert child.returncode == 0, child.stderr
    loaded = dict(line.split(" ", 1) for line in child.stdout.splitlines())
    assert loaded, f"{script} imported no slicer module"
    inside = [Path(file).resolve().is_relative_to(TOOLS / "slicer") for file in loaded.values()]
    assert all(inside), loaded


def test_the_live_probe_child_finds_the_slicer(tmp_path):
    child = run_by_path(
        TOOLS / "probe" / "joern_slicer_live.py", tmp_path, "--inline-library-child", "--help"
    )
    # `--joern-endpoint` is a slicer.py flag (the probe's own --help only has --endpoint), so
    # seeing it means the child really imported slicer and ran its argparse.
    assert child.returncode == 0 and "--joern-endpoint" in child.stdout, child.stderr


def test_slice_sharded_points_at_files_that_exist():
    spec = importlib.util.spec_from_file_location(
        "slice_sharded_paths", TOOLS / "slice_sharded.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.SLICER.is_file(), tool.SLICER
    assert tool.MERGE.is_file(), tool.MERGE
