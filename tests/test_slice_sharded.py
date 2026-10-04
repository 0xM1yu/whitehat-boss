"""tools/slice_sharded.py (#370): the shard-count arithmetic, the host readings it rests on, and
main() end to end with tests/slice_sharded_stub.py standing in for Joern, slicer.py and the merge.
The stub fakes what the tool reads (ports, candidate list, exit codes, merge counters), not Joern's
query protocol -- the real prepare -> shards -> merge -> export run is checked on a real box."""

import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
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
    real_sleep, fired = time.sleep, []

    class Clock:
        time = staticmethod(time.time)

        @staticmethod
        def sleep(seconds: float) -> None:
            # Interrupts the main loop once; the cleanup that follows sleeps here too.
            running = sum("--resume" in p.args for p in launched if isinstance(p.args, list))
            if running == 2 and not fired:
                fired.append(True)
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
    """start(kind, cwd) -> the launcher Popen of a stub server (STUB_SERVER=kind), by default in
    tmp_path."""
    started = []

    def start(kind: str, cwd: Path | None = None) -> object:
        monkeypatch.setenv("STUB_SERVER", kind)
        process = tool.launch([sys.executable, str(STUB), "--serve", str(free_base(1))], None, [],
                              cwd=cwd or tmp_path)
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
    hook cannot finish while a query runs (jstack, text not kept; 2026-10-03: eight JVMs outlived a
    run). stop_all() returns only when the JVM is gone."""
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 1)
    process = stub_server("deaf")
    jvm = int(wait_for(tmp_path / "jvm.pid"))
    assert tool.stop_all([process]) == []
    assert not alive(jvm)
    assert process.returncode is not None               # the launcher is reaped, not left a zombie


@needs_proc
def test_stop_kills_a_jvm_whose_launcher_is_already_reaped(stub_server, tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 1)
    process = stub_server("deaf")
    jvm = int(wait_for(tmp_path / "jvm.pid"))
    process.terminate()
    process.wait()
    assert alive(jvm)                                   # the launcher is gone, the JVM is not
    assert tool.stop_all([process]) == []
    assert not alive(jvm)


@needs_proc
def test_stop_sends_sigterm_first_and_waits_for_a_clean_exit(stub_server, tmp_path):
    process = stub_server("graceful")
    wait_for(tmp_path / "ready")
    started = time.time()
    assert tool.stop_all([process]) == []
    assert (tmp_path / "term.marker").exists()          # the shutdown ran to its end, no SIGKILL
    assert time.time() - started < 10


@needs_proc
def test_a_zombie_leader_with_a_live_thread_is_not_a_stopped_server(stub_server):
    """For a few hundred ms after SIGKILL a JVM is a zombie leader whose threads still hold the
    listening socket (dev box, 2026-10-03: the port kept accepting for 0.15-0.57 s after the kill,
    while the stop that ignored threads had returned 4-115 ms after it). The next run's
    ensure_ports_free (a bind) refuses the port meanwhile, and a restart on the same port (#472,
    not in this branch) would take the dying server for its own in wait_servers, so stop_all() must
    not return before the last thread has gone."""
    process = stub_server("zombie-leader")
    port = int(process.args[-1])
    deadline = time.time() + 20
    while not tool.port_in_use(port):
        assert time.time() < deadline, "the stub server never listened"
        time.sleep(0.05)
    # The leader is gone, a thread is not. Wait for "Z": right after the port opens the exiting
    # leader can still read "D" (CI, 2026-10-04 — a single read failed on ubuntu-latest).
    state = ""
    while state != "Z":
        assert time.time() < deadline, f"the leader never became a zombie (state {state!r})"
        state = Path(f"/proc/{process.pid}/stat").read_text().rpartition(")")[2].split()[0]
        time.sleep(0.02)
    assert tool._live_groups({process.pid}) == {process.pid: [process.pid]}
    assert tool.stop_all([process]) == []
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), 1)


def kill_group(process: subprocess.Popen) -> None:
    """What a failing test leaves behind: nothing."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    process.wait()


def deaf_python(tmp_path: Path, body: str) -> subprocess.Popen:
    """A process of its own group that ignores SIGTERM, runs `body` and then writes `ready`."""
    code = ("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n" + body
            + "\nopen('ready', 'w').write('1')\ntime.sleep(60)\n")
    return tool.launch([sys.executable, "-c", code], None, [], cwd=tmp_path)


@needs_proc
def test_a_member_whose_name_is_cut_mid_character_is_still_seen(tmp_path, monkeypatch):
    """The kernel cuts a process name (`comm`) at 15 bytes, in the middle of a multibyte character
    too, and that /proc/<pid>/stat line is not UTF-8. A scan that skipped what it could not decode
    saw an empty group, ended the stop at once, sent no SIGKILL and said nothing."""
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 0.5)
    # PR_SET_NAME = 15; 5 characters of 3 bytes cut to 14 bytes: the last one is broken
    process = deaf_python(tmp_path, "import ctypes\n"
                          "ctypes.CDLL(None).prctl(15, ('\\uac00' * 5).encode()[:14], 0, 0, 0)")
    try:
        wait_for(tmp_path / "ready")
        with pytest.raises(UnicodeDecodeError):
            Path(f"/proc/{process.pid}/stat").read_bytes().decode()  # the premise holds here
        assert tool._live_groups({process.pid}) == {process.pid: [process.pid]}
        assert tool.stop_all([process]) == []
        assert process.returncode == -signal.SIGKILL         # it ignored SIGTERM: killed, not left
    finally:
        kill_group(process)


@needs_proc
def test_a_launcher_that_only_sigkill_ends_is_reaped(tmp_path, monkeypatch):
    """The scan finds a SIGKILLed launcher dead (a zombie) before the stop looks at its exit status;
    stop_all() reaps it before it returns instead of leaving `returncode` None and a zombie."""
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 0.3)
    process = deaf_python(tmp_path, "")
    try:
        wait_for(tmp_path / "ready")
        assert tool.stop_all([process]) == []
        assert process.returncode == -signal.SIGKILL
    finally:
        kill_group(process)


@needs_proc
def test_sigkill_goes_only_to_a_group_that_is_still_alive(stub_server, tmp_path, monkeypatch):
    """A group that emptied on SIGTERM gets nothing more: a pid is reused once its group is gone,
    and a signal to it would land on somebody else's."""
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 1.5)
    for folder in ("graceful", "deaf"):
        (tmp_path / folder).mkdir()
    graceful = stub_server("graceful", tmp_path / "graceful")
    deaf = stub_server("deaf", tmp_path / "deaf")
    wait_for(tmp_path / "graceful" / "ready")
    wait_for(tmp_path / "deaf" / "jvm.pid")
    sent, real = [], os.killpg
    monkeypatch.setattr(tool.os, "killpg",
                        lambda group, sig: sent.append((group, sig)) or real(group, sig))
    assert tool.stop_all([graceful, deaf]) == []
    assert (tmp_path / "graceful" / "term.marker").exists()   # it left on SIGTERM
    assert sorted(sent) == sorted([(graceful.pid, signal.SIGTERM), (deaf.pid, signal.SIGTERM),
                                   (deaf.pid, signal.SIGKILL)])


def run_in_child(script: str, *args: str, prefix: tuple[str, ...] = ()) -> dict:
    """`script` in a python of its own (after `prefix`, e.g. unshare); its last line is JSON."""
    command = [*prefix, sys.executable, "-c", script, str(Path(tool.__file__)), str(STUB), *args]
    done = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


# One stub server (`kind`: launcher + server, as `joern` and its JVM) stopped by stop_all() in a
# python of its own; it says what it saw. `setup` is what the case wants set first.
STOP_IN_CHILD = """
import importlib.util, json, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("slice_sharded", sys.argv[1])
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
tool.STOP_GRACE_SECONDS, tool.KILL_VERIFY_SECONDS = 1, 2
{setup}
os.environ["STUB_SERVER"] = "{kind}"
folder = Path(sys.argv[3])
process = tool.launch([sys.executable, sys.argv[2], "--serve", sys.argv[4]], None, [], cwd=folder)
while not (folder / "jvm.pid").exists() or not (folder / "jvm.pid").read_text():
    time.sleep(0.05)
jvm = int((folder / "jvm.pid").read_text())
survivors = tool.stop_all([process])
# the server is a child of ours once its launcher is gone (we are a subreaper, or init of the
# namespace): waitpid says whether it is dead -- /proc may be a namespace that does not know its pid
reaped = os.waitpid(jvm, os.WNOHANG)[0] == jvm
print(json.dumps({{"survivors": survivors, "jvm_dead": reaped,
                  "own_proc": os.readlink("/proc/self") == str(os.getpid())}}))
"""


@needs_proc
def test_a_server_that_died_unreaped_is_not_a_survivor(tmp_path):
    """The tool is the subreaper (a container's PID 1 is one): a zombie whose parent died is
    adopted by it and stays a zombie in the group. killpg(group, 0) counts it; the stop must not."""
    result = run_in_child(
        STOP_IN_CHILD.format(setup='import ctypes; ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)',
                             kind="deaf-zombie"), str(tmp_path), str(free_base(1)))
    assert result["survivors"] == []        # PR_SET_CHILD_SUBREAPER = 36 above


@pytest.mark.parametrize("mount_proc", [True, False])
@needs_proc
def test_a_proc_that_is_not_our_pid_namespace_is_not_trusted(tmp_path, mount_proc):
    """In a sandbox that unshares the pid namespace without remounting /proc (bubblewrap, `unshare
    -p`), /proc names the HOST's pids: no group of ours is in it, every group looks empty and the
    stop would end with no SIGKILL and no warning. Then it must fall back to killpg."""
    if shutil.which("unshare") is None:
        pytest.skip("no unshare")
    prefix = ("unshare", "--user", "--map-root-user", "--pid", "--fork",
              *(("--mount-proc",) if mount_proc else ()))
    try:
        subprocess.run([*prefix, "true"], capture_output=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("unprivileged user + pid namespaces are not allowed here")
    result = run_in_child(STOP_IN_CHILD.format(setup="", kind="deaf"), str(tmp_path),
                          str(free_base(1)), prefix=prefix)
    assert result["own_proc"] is mount_proc     # the premise: /proc is ours only when remounted
    assert result["jvm_dead"]                    # killed either way: not trusted when not ours


SURVIVOR = 10_000_000  # added to a group id: a pid no live process has, one per stopped process


def immortal(monkeypatch) -> None:  # noqa: ANN001
    """Every group looks alive to the stop's check: what no signal can end."""
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(tool, "KILL_VERIFY_SECONDS", 0.2)
    monkeypatch.setattr(tool, "_live_groups", lambda groups: {g: [g + SURVIVOR] for g in groups})


@needs_proc
def test_stop_reports_what_it_could_not_kill(monkeypatch):
    """After SIGKILL and a bounded wait a group that is still there is reported, not hidden."""
    immortal(monkeypatch)
    sent = []
    monkeypatch.setattr(tool.os, "killpg", lambda group, sig: sent.append(sig))
    process = tool.launch([sys.executable, "-c", "pass"], None, [])
    assert tool.stop_all([process]) == [process.pid + SURVIVOR]
    assert sent == [signal.SIGTERM, signal.SIGKILL]


@needs_proc
def test_stop_all_stops_servers_at_once_with_one_proc_pass_per_tick(stub_server, tmp_path,
                                                                     monkeypatch):
    """N servers that ignore SIGTERM share one grace window and one pass over /proc per tick. A
    thread per process that each rescanned /proc took 12.5-31.1 s for 8-32 servers among
    1,000-3,000 processes (WSL2, 2026-10-04); here the time must stay a grace, not N of them."""
    n, grace = 8, 1.0
    monkeypatch.setattr(tool, "STOP_GRACE_SECONDS", grace)
    servers = []
    for i in range(n):
        (tmp_path / str(i)).mkdir()
        servers.append(stub_server("deaf", tmp_path / str(i)))
    jvms = [int(wait_for(tmp_path / str(i) / "jvm.pid")) for i in range(n)]
    passes, scan = [], tool._live_groups
    monkeypatch.setattr(tool, "_live_groups", lambda groups: passes.append(1) or scan(groups))
    started = time.time()
    assert tool.stop_all(servers) == []
    elapsed = time.time() - started
    assert elapsed < 0.6 * n * grace                  # one grace for all, not one each
    assert len(passes) <= elapsed / 0.1 + 2           # a pass per tick, not a pass per server
    assert not any(alive(pid) for pid in jvms)


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


def unstopped(run, launched, *indexes: int) -> bool:
    """summary.json `unstopped_pids` is exactly the processes at these `launched` indexes (each
    one's group id plus SURVIVOR). launched: the prepare server, prepare's slicer, the shard
    servers, the shard slicers, the merge, the export."""
    return sorted(run.summary().get("unstopped_pids", [])) == sorted(
        launched[i].pid + SURVIVOR for i in indexes)


@needs_proc
def test_unstopped_pids_name_the_prepare_server(stubbed, monkeypatch):
    run, launched = stubbed
    immortal(monkeypatch)
    monkeypatch.setenv("STUB_PREPARE", "fail")              # stops after the prepare server's stop
    assert run("--shards", "2") == tool.EXIT_FAILED
    assert unstopped(run, launched, 0)


@needs_proc
def test_unstopped_pids_name_the_wall_stopped_shards_and_every_server(stubbed, monkeypatch):
    """The three stop sites of a wall-stopped run: prepare's server, the shards' slicers, and the
    shards' servers at the end -- each one's survivors reach summary.json."""
    run, launched = stubbed
    immortal(monkeypatch)
    monkeypatch.setenv("STUB_SHARD", "hang")
    assert run("--shards", "2", "--wall-seconds", "1") == tool.EXIT_INCOMPLETE
    # launched: 0 prepare server, 1 prepare slicer, 2-3 shard servers, 4-5 shard slicers
    assert unstopped(run, launched, 0, 2, 3, 4, 5)


@needs_proc
def test_unstopped_pids_name_what_the_final_cleanup_stops(stubbed, monkeypatch):
    """A failure before the shards leaves what is tracked to the cleanup in main()'s `finally`."""
    run, launched = stubbed
    immortal(monkeypatch)
    monkeypatch.setenv("STUB_SERVER", "die")                # the prepare server dies at boot
    assert run("--shards", "2") == tool.EXIT_FAILED
    assert unstopped(run, launched, 0)
