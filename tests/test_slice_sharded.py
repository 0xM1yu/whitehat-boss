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
                                  "--export-dir", "--export", "--claim-db", "--out", "--cpg",
                                  "--skip-candidates", "--skip"])
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
    ("--max-restarts-per-shard", "0"), ("--max-restarts-per-shard", "many"),
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
            # Only the main loop's sleep: the threads that stop the servers sleep here too.
            running = sum("--resume" in p.args for p in launched if isinstance(p.args, list))
            if threading.current_thread() is threading.main_thread() and running == 2:
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


# -- stopping a server: the JVM, not only its launcher -----------------------------------------

needs_proc = pytest.mark.skipif(not Path("/proc/self/stat").exists(),
                                reason="process groups and /proc are Linux")


def alive(pid: int) -> bool:
    """Running -- a zombie nobody reaped yet is dead."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat[stat.rindex(")") + 2] not in "ZX"


def wait_for(path: Path, seconds: float = 20) -> str:
    deadline = time.time() + seconds
    while not path.exists() or not path.read_text():
        assert time.time() < deadline, f"{path} never appeared"
        time.sleep(0.05)
    return path.read_text()


@pytest.fixture
def stub_server(tmp_path, monkeypatch):
    """start(kind) -> the launcher Popen of a stub server (STUB_SERVER=kind) running in tmp_path."""
    started = []

    def start(kind: str) -> object:
        monkeypatch.setenv("STUB_SERVER", kind)
        process = tool.launch([sys.executable, str(STUB), "--serve", str(free_base(1))], None, [],
                              cwd=tmp_path)
        started.append(process)
        return process

    yield start
    for process in started:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait()


@needs_proc
def test_stop_ends_a_jvm_that_outlives_its_launcher(stub_server, tmp_path, monkeypatch):
    """`joern` is an sh with the JVM as its child. SIGTERM ends the sh at once; the JVM's shutdown
    hook queues behind a running query and never finishes (2026-10-03: eight JVMs outlived a run).
    stop() returns only when the JVM is gone."""
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 1)
    process = stub_server("deaf")
    jvm = int(wait_for(tmp_path / "jvm.pid"))
    assert tool.stop(process) == []
    assert not alive(jvm) and process.poll() is not None


@needs_proc
def test_stop_kills_a_jvm_whose_launcher_is_already_reaped(stub_server, tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 1)
    process = stub_server("deaf")
    jvm = int(wait_for(tmp_path / "jvm.pid"))
    process.terminate()
    process.wait()
    assert alive(jvm)                                   # the launcher is gone, the JVM is not
    assert tool.stop(process) == []
    assert not alive(jvm)


@needs_proc
def test_stop_sends_sigterm_first_and_waits_for_a_clean_exit(stub_server, tmp_path):
    process = stub_server("graceful")
    wait_for(tmp_path / "ready")
    started = time.time()
    assert tool.stop(process) == []
    assert (tmp_path / "term.marker").exists()          # the shutdown ran to its end, no SIGKILL
    assert time.time() - started < 10


@needs_proc
def test_stop_without_a_grace_kills_at_once(stub_server, tmp_path):
    """A server whose query was abandoned past its deadline is not worth a SIGTERM."""
    process = stub_server("graceful")
    wait_for(tmp_path / "ready")
    assert tool.stop(process, grace=0) == []
    assert not (tmp_path / "term.marker").exists()


@needs_proc
def test_stop_reports_what_it_could_not_kill(monkeypatch):
    """After SIGKILL and a bounded wait a group that is still there is reported, not hidden."""
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(tool, "KILL_VERIFY_SECONDS", 0.2)
    monkeypatch.setattr(tool, "_live_members", lambda group: [4242])
    sent = []
    monkeypatch.setattr(tool.os, "killpg", lambda group, sig: sent.append(sig))
    process = tool.launch([sys.executable, "-c", "pass"], None, [])
    assert tool.stop(process) == [4242]
    assert sent == [signal.SIGTERM, signal.SIGKILL]
    assert tool.stop_all([process]) == [4242]


@needs_proc
def test_the_wall_stop_leaves_no_jvm_behind(stubbed, monkeypatch):
    """main() end to end: every server -- prepare's and the shards' -- is gone when it returns,
    though none of them leaves on SIGTERM."""
    run, _ = stubbed
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 1)
    monkeypatch.setenv("STUB_SERVER", "deaf")
    monkeypatch.setenv("STUB_SHARD", "hang")
    assert run("--shards", "2", "--wall-seconds", "1") == tool.EXIT_INCOMPLETE
    jvms = [int(p.read_text()) for p in run.work.glob("*/jvm.pid")]
    assert len(jvms) == 3                                # prepare + two shards
    assert not any(alive(pid) for pid in jvms)
    assert "unstopped_pids" not in run.summary()


# -- --restart-on-deadline ---------------------------------------------------------------------

DEADLINE = "Query deadline exceeded (600.0 seconds); server execution may continue"
needs_posix = pytest.mark.skipif(not hasattr(os, "killpg"), reason="process groups are POSIX")


def test_progress_tail_returns_only_complete_new_lines(tmp_path):
    log = tmp_path / "progress.jsonl"
    tail = tool.ProgressTail(log)
    assert tail.read() == []                                    # no file yet
    log.write_bytes(b'{"event": "a"}\n{"event": "b"')           # the second line is half written
    assert [e["event"] for e in tail.read()] == ["a"]
    with open(log, "ab") as stream:
        stream.write(b', "x": 1}\nnot json\n[1]\n{"event": "c"}\n')
    assert [e["event"] for e in tail.read()] == ["b", "c"]      # the half line completes, junk goes
    assert tail.read() == []


@pytest.mark.parametrize(("error", "hit"), [
    (DEADLINE, True),
    ("Query deadline exceeded (1.5 seconds); server execution may continue", True),
    ("HTTP request failed: timed out", True),
    ("Candidate deadline exceeded", False),         # nothing was sent: no query is left running
    ("HTTP request failed: <urlopen error [Errno 111] Connection refused>", False),
    ("boom", False), (None, False), (7, False),
])
def test_what_counts_as_a_deadline_hit(error, hit):
    assert tool.is_deadline_error(error) is hit


def test_deadline_keys_pick_the_failed_candidates_only():
    events = [{"event": "candidate_failed", "candidate_key": "k1", "error": DEADLINE},
              {"event": "query_error", "candidate_key": "k2", "error": DEADLINE},
              {"event": "candidate_failed", "candidate_key": "k3", "error": "boom"},
              {"event": "candidate_failed", "candidate_key": None, "error": DEADLINE},
              {"event": "candidate_failed", "candidate_key": "k4", "error": DEADLINE}]
    assert tool.deadline_keys(events) == ["k1", "k4"]


def test_the_trigger_is_what_the_real_slicer_writes_on_a_deadline(monkeypatch, tmp_path):
    """Where the slicer's message and this tool's pattern meet: reword it in JoernClient.query and
    --restart-on-deadline would turn itself off without a test going red."""
    from test_slicer import MockTransport, candidate, payloads

    import slicer

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.c").write_text("int entry(char *p)\n{\n    return 0;\n}\n", encoding="utf-8")
    data = payloads(case_a=[candidate()], flow_count=1)
    answer = data["flows"]
    data["flows"] = lambda query: (time.sleep(1.5), answer)[1]   # answers after the deadline
    monkeypatch.setattr(slicer, "urlopen", MockTransport(data))
    monkeypatch.setattr(slicer, "read_version", lambda *args: ("test-joern", "test fixture"))
    out = tmp_path / "out"
    slicer.main(["--repo", str(repo), "--out", str(out / "slices.json"), "--checkpoint",
                 str(out / "state.db"), "--progress", str(out / "progress.jsonl"),
                 "--query-timeout-seconds", "0.3"])
    keys = tool.deadline_keys(tool.ProgressTail(out / "progress.jsonl").read())
    assert len(keys) == 1 and len(keys[0]) == 64


def argvs(run, index: int) -> list[list[str]]:
    """Every start of shard `index`, from the stub's log."""
    lines = (run.work / f"shard{index}" / "argvs.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


@needs_posix
def test_a_deadline_hit_restarts_the_shard_and_skips_that_candidate(stubbed, monkeypatch):
    run, launched = stubbed
    monkeypatch.setenv("STUB_SHARD", "deadline")
    assert run("--shards", "2", "--restart-on-deadline") == tool.EXIT_OK
    summary = run.summary()
    restart = summary["restart"]
    assert restart["per_shard"] == [1, 1] and restart["total"] == 2
    assert restart["limit_hit"] == [] and restart["errors"] == []
    assert sorted(e["shard"] for e in restart["events"]) == [0, 1]
    for index in (0, 1):
        first, second = argvs(run, index)
        assert "--skip-candidates" not in first
        assert Path(flag(second, "--skip-candidates")).read_text().split() == [f"stub-{index}-0"]
        for name in ("--checkpoint", "--cpg", "--claim-db", "--joern-version-command"):
            assert flag(second, name) == flag(first, name)
        assert "--resume" in second and flag(second, "--shard") == f"{index}/2"
        assert second[-2:] == ["--max-flows-per-candidate", "10"]
        assert (run.work / f"shard{index}" / "slicer.1.log").exists()
        assert (run.work / f"shard{index}" / "restart1" / "joern.log").exists()
        # the killed server's imported graph is gone (a big CPG is copied there); the new one's not
        assert not (run.work / f"shard{index}" / "workspace").exists()
        assert (run.work / f"shard{index}" / "restart1" / "workspace" / "cpg.bin").exists()
    assert summary["shard_exit_codes"] == [2, 2] and summary["dead_shards"] == []
    assert all(p.poll() is not None for p in launched)         # old and new servers are all gone


@needs_posix
def test_without_the_flag_a_deadline_hit_restarts_nothing(stubbed, monkeypatch):
    run, _ = stubbed
    monkeypatch.setenv("STUB_SHARD", "deadline")
    assert run("--shards", "2", "--wall-seconds", "3") == tool.EXIT_INCOMPLETE
    summary = run.summary()
    assert "restart" not in summary and summary["wall_stopped_shards"] == [0, 1]
    assert [len(argvs(run, index)) for index in (0, 1)] == [1, 1]


@needs_posix
def test_one_shard_restarts_without_a_claim_queue(stubbed, monkeypatch):
    run, _ = stubbed
    monkeypatch.setenv("STUB_SHARD", "deadline")
    assert run("--shards", "1", "--restart-on-deadline") == tool.EXIT_OK
    first, second = argvs(run, 0)
    assert "--shard" not in second and "--claim-db" not in second
    assert Path(flag(second, "--skip-candidates")).read_text().split() == ["stub-0-0"]
    assert run.summary()["restart"]["per_shard"] == [1]


@needs_posix
def test_the_skip_list_grows_with_every_restart(stubbed, monkeypatch):
    run, _ = stubbed
    monkeypatch.setenv("STUB_SHARD", "deadline")
    monkeypatch.setenv("STUB_DEADLINES", "2")
    assert run("--shards", "2", "--restart-on-deadline", "--max-restarts-per-shard", "3") == 0
    assert run.summary()["restart"]["per_shard"] == [2, 2]
    for index in (0, 1):
        *_, last = argvs(run, index)
        keys = Path(flag(last, "--skip-candidates")).read_text().split()
        assert keys == [f"stub-{index}-0", f"stub-{index}-1"]


@needs_posix
def test_restarts_are_bounded_per_shard(stubbed, monkeypatch):
    run, _ = stubbed
    monkeypatch.setattr(tool, "RESTART_MIN_WALL_SECONDS", 0)
    monkeypatch.setenv("STUB_SHARD", "deadline")
    monkeypatch.setenv("STUB_DEADLINES", "3")
    code = run("--shards", "2", "--restart-on-deadline", "--max-restarts-per-shard", "1",
               "--wall-seconds", "4")
    assert code == tool.EXIT_INCOMPLETE
    restart = run.summary()["restart"]
    assert restart["per_shard"] == [1, 1] and restart["limit_hit"] == [0, 1]
    assert [len(argvs(run, index)) for index in (0, 1)] == [2, 2]


@needs_posix
def test_restarts_stop_at_the_wall(stubbed, monkeypatch):
    run, _ = stubbed
    monkeypatch.setattr(tool, "RESTART_MIN_WALL_SECONDS", 0)
    monkeypatch.setenv("STUB_SHARD", "deadline")
    monkeypatch.setenv("STUB_DEADLINES", "999")
    started = time.time()
    code = run("--shards", "2", "--restart-on-deadline", "--max-restarts-per-shard", "999",
               "--wall-seconds", "3")
    assert code == tool.EXIT_INCOMPLETE and time.time() - started < 25
    summary = run.summary()
    assert summary["wall_stopped_shards"] == [0, 1]
    assert summary["restart"]["total"] >= 2
    assert all(event["at_seconds"] <= 3 for event in summary["restart"]["events"])


@needs_posix
def test_no_restart_starts_with_too_little_wall_left(stubbed, monkeypatch):
    """A restart costs about 16 s on a small graph; with the default floor (60 s) a 3 s wall
    leaves the deadline-hit shards as they are, and the wall stops them."""
    run, _ = stubbed
    monkeypatch.setenv("STUB_SHARD", "deadline")
    code = run("--shards", "2", "--restart-on-deadline", "--wall-seconds", "3")
    assert code == tool.EXIT_INCOMPLETE
    summary = run.summary()
    assert summary["restart"]["total"] == 0 and summary["wall_stopped_shards"] == [0, 1]
    assert [len(argvs(run, index)) for index in (0, 1)] == [1, 1]


@needs_posix
def test_a_failed_restart_leaves_that_shard_dead_and_says_why(stubbed, monkeypatch):
    run, launched = stubbed
    monkeypatch.setenv("STUB_SHARD", "deadline0")
    monkeypatch.setenv("STUB_SERVER", "die-restart")
    assert run("--shards", "2", "--restart-on-deadline") == tool.EXIT_INCOMPLETE
    summary = run.summary()
    assert summary["dead_shards"] == [0] and summary["restart"]["per_shard"] == [1, 0]
    assert "exited 3 before listening" in summary["restart"]["errors"][0]
    assert summary["dead_shard_logs"][0].endswith("slicer.log")
    assert all(p.poll() is not None for p in launched)
