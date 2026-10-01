"""tools/slice_sharded.py (#370): the shard-count arithmetic and the host readings it rests on.
The full prepare -> shards -> merge -> export run needs Joern and is checked on a real box."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "slice_sharded", Path(__file__).resolve().parents[1] / "tools" / "slice_sharded.py"
)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


def shards(cores, memory, candidates, **kw):  # noqa: ANN001, ANN003, ANN201
    settings = dict(cores_per_shard=4, memory_per_shard_gb=24, min_candidates=20, max_shards=32)
    settings.update(kw)
    return tool.auto_shards(cores, memory, candidates, **settings)


def test_auto_shards_takes_the_tightest_limit():
    assert shards(122, 2000, 5000) == 30          # cores: 122 // 4
    assert shards(122, 200, 5000) == 8            # memory: 200 // 24
    assert shards(122, 2000, 100) == 5            # candidates: 100 // 20
    assert shards(512, 9000, 9000) == 32          # cap
    assert shards(122, None, 5000) == 30          # memory unknown -> not a limit


def test_auto_shards_never_goes_below_one():
    assert shards(2, 8, 3) == 1


def test_cgroup_quota_is_read_in_cores(tmp_path):
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("12287999 100000\n")
    assert tool.cgroup_cores(cpu_max) == pytest.approx(122.88, abs=0.01)
    cpu_max.write_text("max 100000\n")
    assert tool.cgroup_cores(cpu_max) is None
    assert tool.cgroup_cores(tmp_path / "missing") is None


def test_available_memory_reads_memavailable(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       2097152 kB\nMemAvailable:   1048576 kB\n")
    assert tool.available_memory_gb(meminfo) == pytest.approx(1.0)
    assert tool.available_memory_gb(tmp_path / "missing") is None


def test_every_slicer_call_gets_the_same_analysis_arguments(tmp_path):
    """Analysis settings are the run contract: prepare and every shard must pass the same ones."""
    extra = ["--max-flows-per-candidate", "10", "--backward-nodes", "1000"]
    prepare = tool.slicer_command(tmp_path / "r", tmp_path / "p" / "s.json", 1, Path("j"),
                                  tmp_path / "p.db", extra, "--prepare-only")
    shard = tool.slicer_command(tmp_path / "r", tmp_path / "s" / "s.json", 2, Path("j"),
                                tmp_path / "s.db", extra, "--resume")
    assert prepare[-len(extra):] == extra and shard[-len(extra):] == extra


@pytest.mark.parametrize("bad", ["0", "-1", "four"])
def test_a_bad_shard_count_is_refused(tmp_path, bad):
    with pytest.raises(SystemExit):
        tool.main(["--repo", str(tmp_path), "--out", str(tmp_path / "o.json"),
                   "--work-dir", str(tmp_path / "w"), "--joern", "j", "--shards", bad])
