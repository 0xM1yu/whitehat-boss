"""tools/slice_sharded.py (#370): the shard-count arithmetic, the host readings it rests on, and
main() end to end with tests/slice_sharded_stub.py standing in for Joern, slicer.py and the merge.
The stub fakes what the tool reads (ports, candidate list, exit codes, merge counters), not Joern's
query protocol -- the real prepare -> shards -> merge -> export run is checked on a real box."""

import importlib.util
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "slice_sharded", Path(__file__).resolve().parents[1] / "tools" / "slice_sharded.py"
)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)
STUB = Path(__file__).resolve().parent / "slice_sharded_stub.py"


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


def test_auto_shards_rounds_down():
    """A fraction of a shard's cores or memory is not a shard."""
    assert shards(123, 2000, 5000) == 30          # 30.75
    assert shards(122, 215, 5000) == 8            # 8.96
    assert shards(122, 2000, 119) == 5            # 5.95


def test_auto_shards_never_goes_below_one():
    assert shards(2, 8, 3) == 1


def test_the_deciding_term_is_the_tightest():
    limits = tool.shard_limits(122, 200, 5000, cores_per_shard=4, memory_per_shard_gb=24,
                               min_candidates=20, max_shards=32)
    assert limits == {"cores": 30, "memory": 8, "candidates": 250, "max_shards": 32}
    assert tool.deciding_term(limits) == "memory"


@pytest.mark.parametrize(("text", "cores"), [
    ("12287999 100000\n", 122.88), ("max 100000\n", None), ("abc 100000", None),
    ("100000 0", None), ("100000", None), ("", None), ("²00000 100000", None),
])
def test_cgroup_v2_cpu_max(text, cores):
    assert tool.parse_cpu_max(text) == (pytest.approx(cores, abs=0.01) if cores else None)


@pytest.mark.parametrize(("quota", "period", "cores"), [
    ("800000\n", "100000\n", 8.0), ("-1\n", "100000\n", None), ("abc", "100000", None),
    ("100000", "0", None),
])
def test_cgroup_v1_cfs_quota(quota, period, cores):
    assert tool.parse_cfs_quota(quota, period) == cores


@pytest.mark.parametrize(("text", "limit"), [
    ("68719476736\n", 68719476736), ("max\n", None), ("9223372036854771712\n", None),
    ("abc", None), ("", None),
])
def test_cgroup_memory_limit(text, limit):
    assert tool.parse_limit_bytes(text) == limit


def _tree(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


def test_cgroup_v2_follows_proc_self_cgroup_and_its_ancestors(tmp_path):
    proc = tmp_path / "cgroup"
    proc.write_text("0::/kubepods/pod1/ctr\n")
    root = tmp_path / "fs"
    _tree(root, {"kubepods/cpu.max": "1600000 100000", "kubepods/pod1/ctr/cpu.max": "max 100000",
                 "kubepods/memory.max": "max", "kubepods/pod1/memory.max": str(32 << 30),
                 "kubepods/pod1/ctr/memory.max": "max"})
    assert tool.cgroup_cores(proc, root) == 16.0               # the ancestor's quota binds
    assert tool.cgroup_memory_bytes(proc, root) == (32 << 30, True)


def test_cgroup_v1_reads_the_controller_mounts(tmp_path):
    proc = tmp_path / "cgroup"
    proc.write_text("5:cpu,cpuacct:/docker/abc\n4:memory:/docker/abc\n")
    root = tmp_path / "fs"
    # Inside the container the mount is the container's own cgroup; the host path is absent.
    _tree(root, {"cpu,cpuacct/cpu.cfs_quota_us": "400000",
                 "cpu,cpuacct/cpu.cfs_period_us": "100000",
                 "memory/memory.limit_in_bytes": str(8 << 30)})
    assert tool.cgroup_cores(proc, root) == 4.0
    assert tool.cgroup_memory_bytes(proc, root) == (8 << 30, True)


def test_memory_is_the_tighter_of_memavailable_and_the_cgroup_limit(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       2097152 kB\nMemAvailable:   104857600 kB\n")  # 100 GB
    proc = tmp_path / "cgroup"
    proc.write_text("0::/\n")
    root = tmp_path / "fs"
    _tree(root, {"memory.max": str(16 << 30)})
    reading = tool.memory_reading(meminfo, proc, root)
    assert reading["memory_gb"] == 16.0 and reading["warning"] is None
    missing = tool.memory_reading(meminfo, proc, tmp_path / "nowhere")
    assert missing["memory_gb"] == pytest.approx(100.0)
    assert "no cgroup memory limit readable" in missing["warning"]


def test_available_memory_reads_memavailable(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       2097152 kB\nMemAvailable:   1048576 kB\n")
    assert tool.available_memory_gb(meminfo) == pytest.approx(1.0)
    assert tool.available_memory_gb(tmp_path / "missing") is None
    meminfo.write_text("MemAvailable:\n")
    assert tool.available_memory_gb(meminfo) is None
    meminfo.write_text("MemAvailable:   abc kB\n")
    assert tool.available_memory_gb(meminfo) is None


def test_every_slicer_call_gets_the_same_analysis_arguments(tmp_path):
    """Analysis settings are the run contract: prepare and every shard must pass the same ones."""
    extra = ["--max-flows-per-candidate", "10", "--backward-nodes", "1000"]
    prepare = tool.slicer_command(tmp_path / "r", tmp_path / "p" / "s.json", 1, Path("j"),
                                  tmp_path / "p.db", extra, "--prepare-only")
    shard = tool.slicer_command(tmp_path / "r", tmp_path / "s" / "s.json", 2, Path("j"),
                                tmp_path / "s.db", extra, "--resume")
    assert prepare[-len(extra):] == extra and shard[-len(extra):] == extra


@pytest.mark.parametrize("flag", ["--checkpoint", "--joern-endpoint=http://x", "--shard", "--check",
                                  "--export-dir", "--export", "--claim-db", "--out", "--cpg"])
def test_tool_owned_flags_are_refused_after_the_separator(tmp_path, flag):
    joern = tmp_path / "joern"
    joern.write_text("")
    with pytest.raises(SystemExit) as stopped:
        tool.main(["--repo", str(tmp_path), "--out", str(tmp_path / "o.json"), "--work-dir",
                   str(tmp_path / "w"), "--joern", str(joern), "--", "--threshold", "0.9", flag,
                   "x"])
    assert stopped.value.code == 2


def test_analysis_flags_pass_through():
    assert tool.owned_flags_in(["--threshold", "0.9", "--max-flows-per-candidate", "10",
                                "--compile-commands", "c.json", "--backward-nodes=5"]) == []


@pytest.mark.parametrize(("option", "bad"), [
    ("--shards", "0"), ("--shards", "-1"), ("--shards", "four"), ("--shards", "²"),
    ("--cores-per-shard", "0"), ("--cores-per-shard", "nan"), ("--memory-per-shard-gb", "-3"),
    ("--min-candidates-per-shard", "0"), ("--max-shards", "0"), ("--wall-seconds", "0"),
    ("--wall-seconds", "inf"), ("--heap", "20"), ("--heap", "0g"), ("--base-port", "70000"),
])
def test_bad_numbers_are_refused_by_the_parser(tmp_path, option, bad):
    joern = tmp_path / "joern"
    joern.write_text("")
    with pytest.raises(SystemExit) as stopped:
        tool.main(["--repo", str(tmp_path), "--out", str(tmp_path / "o.json"),
                   "--work-dir", str(tmp_path / "w"), "--joern", str(joern), option, bad])
    assert stopped.value.code == 2
    assert not (tmp_path / "w").exists()


def test_a_missing_joern_is_refused_up_front(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        tool.main(["--repo", str(tmp_path), "--out", str(tmp_path / "o.json"),
                   "--work-dir", str(tmp_path / "w"), "--joern", "no/such/joern"])
    assert stopped.value.code == 2


def test_memory_per_shard_defaults_to_heap_plus_overhead(tmp_path):
    joern = tmp_path / "joern"
    joern.write_text("")
    args, _ = tool.parse(["--repo", ".", "--out", "o", "--work-dir", "w", "--joern", str(joern),
                          "--heap", "16384m"])
    assert args.memory_per_shard_gb == 16 + tool.JVM_OVERHEAD_GB


def test_dry_run_prints_the_count_and_the_deciding_term(tmp_path, capsys, monkeypatch):
    joern = tmp_path / "joern"
    joern.write_text("")
    monkeypatch.setattr(tool, "available_cores", lambda: 64.0)
    monkeypatch.setattr(tool, "memory_reading", lambda: {"memory_gb": 100.0, "warning": None})
    assert tool.main(["--repo", str(tmp_path), "--out", str(tmp_path / "o"), "--work-dir",
                      str(tmp_path / "w"), "--joern", str(joern), "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["shards"] == 4 and plan["decided_by"] == "memory"   # 100 // (20 + 4)
    assert plan["limits"] == {"cores": 16, "memory": 4, "max_shards": 32}


# -- main() end to end on the stub -------------------------------------------------------------


def free_base(count: int) -> int:
    for _ in range(50):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            base = probe.getsockname()[1]
        if base + count < 65535 and not any(tool.port_in_use(base + i) for i in range(count + 1)):
            return base
    raise RuntimeError("no free port range")


@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    """main() with the stub as Joern, slicer.py and the merge; returns (run, launched processes)."""
    monkeypatch.setattr(tool, "SLICER", STUB)
    monkeypatch.setattr(tool, "MERGE", STUB)
    monkeypatch.setattr(tool, "server_command",
                        lambda joern, heap, port: [sys.executable, str(STUB), "--serve", str(port)])
    monkeypatch.setattr(tool, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(tool, "SERVER_START_SECONDS", 20)
    monkeypatch.setattr(tool, "available_cores", lambda: 64.0)
    monkeypatch.setattr(tool, "memory_reading", lambda: {"memory_gb": None, "warning": None})
    launched = []
    real = tool.launch

    def launch(*args, **kw):  # noqa: ANN002, ANN003, ANN202
        process = real(*args, **kw)
        launched.append(process)
        return process

    monkeypatch.setattr(tool, "launch", launch)
    joern = tmp_path / "joern"
    joern.write_text("")
    work = tmp_path / "w"

    def run(*more: str, base: int | None = None) -> int:
        base = base or free_base(8)
        return tool.main(["--repo", str(tmp_path), "--out", str(tmp_path / "out.json"),
                          "--work-dir", str(work), "--joern", str(joern), "--base-port", str(base),
                          *more, "--", "--max-flows-per-candidate", "10"])

    run.work = work
    run.summary = lambda: json.loads((work / "summary.json").read_text())
    yield run, launched
    for process in launched:  # nothing a failing test leaves behind
        if process.poll() is None:
            process.kill()


def test_main_runs_every_stage_and_exits_0_when_complete(stubbed):
    run, launched = stubbed
    assert run("--shards", "3") == tool.EXIT_OK
    summary = run.summary()
    assert summary["merge"] == {"candidates": 40, "complete": 40}
    assert summary["dead_shards"] == [] and summary["shard_exit_codes"] == [2, 2, 2]
    assert len(set(summary["ports"])) == 3
    argvs = [json.loads((run.work / f"shard{i}" / "argv.json").read_text()) for i in range(3)]
    checkpoints = {a[a.index("--checkpoint") + 1] for a in argvs}
    assert len(checkpoints) == 3                                   # a state.db per shard
    endpoints = {a[a.index("--joern-endpoint") + 1] for a in argvs}
    assert len(endpoints) == 3                                     # a server per shard
    for index, argv in enumerate(argvs):
        assert "--resume" in argv and "--cpg" in argv and "--claim-db" in argv
        assert argv[argv.index("--shard") + 1] == f"{index}/3"
        assert argv[-2:] == ["--max-flows-per-candidate", "10"]
    assert all(p.poll() is not None for p in launched)             # servers stopped


def test_one_shard_is_a_plain_seeded_run(stubbed):
    run, _ = stubbed
    assert run("--shards", "1") == tool.EXIT_OK
    argv = json.loads((run.work / "shard0" / "argv.json").read_text())
    assert "--shard" not in argv and "--claim-db" not in argv and "--cpg" in argv


def test_auto_takes_the_candidates_term(stubbed):
    run, _ = stubbed
    assert run() == tool.EXIT_OK                                   # 40 // 20, cores 64 // 4
    summary = run.summary()
    assert summary["shards"] == 2 and summary["decided_by"] == "candidates"


def test_every_shard_dying_is_a_failure_not_a_result(stubbed, monkeypatch):
    run, _ = stubbed
    monkeypatch.setenv("STUB_SHARD", "die")
    assert run("--shards", "2") == tool.EXIT_FAILED
    summary = run.summary()
    assert summary["dead_shards"] == [0, 1] and summary["merge"]["complete"] == 0
    assert "every shard died" in summary["error"]


def test_a_dead_shard_makes_the_result_incomplete(stubbed, monkeypatch):
    run, _ = stubbed
    monkeypatch.setenv("STUB_SHARD", "die0")
    assert run("--shards", "2") == tool.EXIT_INCOMPLETE
    summary = run.summary()
    assert summary["dead_shards"] == [0] and summary["shard_exit_codes"][0] == 1
    assert summary["merge"] == {"candidates": 40, "complete": 20}
    assert summary["dead_shard_logs"][0].endswith("slicer.log")


def test_the_wall_clock_stops_shards(stubbed, monkeypatch):
    run, launched = stubbed
    monkeypatch.setenv("STUB_SHARD", "hang")
    started = time.time()
    assert run("--shards", "2", "--wall-seconds", "1") == tool.EXIT_INCOMPLETE
    assert time.time() - started < 15            # the stub hangs 20 s
    summary = run.summary()
    assert summary["wall_stopped_shards"] == [0, 1] and summary["dead_shards"] == []
    assert all(p.poll() is not None for p in launched)


def test_the_wall_clock_starts_once_the_servers_are_up(stubbed, monkeypatch):
    """Server boot is not shard time: a slow boot must not eat --wall-seconds."""
    run, _ = stubbed
    monkeypatch.setenv("STUB_SERVER_DELAY", "2")
    assert run("--shards", "2", "--wall-seconds", "1.5") == tool.EXIT_OK
    assert run.summary()["wall_stopped_shards"] == []


def test_a_failed_prepare_stops_before_the_shards(stubbed, monkeypatch):
    run, _ = stubbed
    monkeypatch.setenv("STUB_PREPARE", "fail")                    # CPG written, exit 1
    assert run("--shards", "2") == tool.EXIT_FAILED
    assert "prepare failed" in run.summary()["error"]
    assert not (run.work / "shard0").exists()


def test_a_server_that_dies_at_boot_fails_fast(stubbed, monkeypatch):
    run, _ = stubbed
    monkeypatch.setenv("STUB_SERVER", "die")
    started = time.time()
    assert run("--shards", "2") == tool.EXIT_FAILED
    assert time.time() - started < 30
    error = run.summary()["error"]
    assert "exited 3 before listening" in error and "joern.log" in error


def test_a_port_already_in_use_is_refused(stubbed):
    run, launched = stubbed
    base = free_base(8)
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", base))
        squatter.listen()
        assert run("--shards", "2", base=base) == tool.EXIT_FAILED
    assert "ports already in use" in run.summary()["error"] and launched == []


def test_an_interrupt_stops_shards_and_servers_and_writes_the_summary(stubbed, monkeypatch):
    run, launched = stubbed
    monkeypatch.setenv("STUB_SHARD", "hang")
    real_sleep = time.sleep

    class Clock:
        time = staticmethod(time.time)

        @staticmethod
        def sleep(seconds: float) -> None:
            if sum("--resume" in p.args for p in launched if isinstance(p.args, list)) == 2:
                raise KeyboardInterrupt
            real_sleep(seconds)

    monkeypatch.setattr(tool, "time", Clock)
    with pytest.raises(KeyboardInterrupt):
        run("--shards", "2")
    assert all(p.poll() is not None for p in launched)
    assert "interrupted" in run.summary()["error"]


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM to self kills the process outright on Windows")
def test_sigterm_stops_shards_and_servers(stubbed, monkeypatch):
    run, launched = stubbed
    monkeypatch.setenv("STUB_SHARD", "hang")

    def terminate_when_shards_run() -> None:
        while sum("--resume" in p.args for p in list(launched)) < 2:
            time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=terminate_when_shards_run, daemon=True).start()
    with pytest.raises(SystemExit) as stopped:
        run("--shards", "2")
    assert stopped.value.code == 128 + signal.SIGTERM
    assert all(p.poll() is not None for p in launched)
    assert run.summary()["exit"] == 128 + signal.SIGTERM
