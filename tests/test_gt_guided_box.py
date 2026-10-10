"""`tools/gt_guided/`의 상자 쪽 — `gt_worker.py`·`gt_slice.py`·`build_auditor.py`. PR #416 리뷰.

막으려는 명제:
1. **한 표본은 한 번에 하나만 돈다**(claim이 sid 단위로 배타적이다) — 두 번째 run의
   `rmtree`가 살아 있는 run의 디렉터리를 지우던 결함.
2. **`DONE`은 성공일 때만 쓴다** — 실패는 `FAILED`, 일시 실패는 `RETRY`로 가른다. 코디네이터가
   실패를 완료로 세던 결함의 상자 쪽 절반이다.
3. **작업자 스레드는 어떤 예외에도 안 죽는다** — 죽으면 main이 non-zero로 끝나 알린다.
4. **GT 필터의 라벨은 실제로 쓴 창을 말한다.** 행의 `gt_guided`는 슬라이스가 GT로 걸러졌으면
   job이 뭐라 하든 true다.

실제 Joern·git·ssh는 한 번도 안 불린다 — `sh`·`Popen`·`subprocess.run`을 가짜로 바꾸고, 안 바꾼
호출은 `hermetic` 픽스처가 막는다. 🔴 **실 Joern·실 인덱서·실 슬라이서로는 이 파일이 아무것도
재지 않는다**(미실측). 가짜는 호출의 모양으로만 갈린다.
"""

import gzip
import json
import os
import runpy
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import gt_guided_support as support
from tests.gt_guided_support import TOOL_DIR, load
from tests.test_pipeline_e2e import _FIXTURE_INDEX, TARGET
from tests.test_slice_targets import _CASE_A, _CASE_B, _slice, _slices_file

hermetic = support.hermetic  # autouse: pytest finds a fixture bound in the test module
cp949_default = support.cp949_default
REPO_ROOT = Path(__file__).resolve().parent.parent


class BoxEnv:
    """상자 한 대의 `/root/gt`와 그 위의 외부 프로그램(git·인덱서·Joern·슬라이서·빌더) 가짜."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.gtw = load("gt_worker")
        self.root = tmp_path / "gt"
        for d in ("inbox", "running", "out", "work"):
            (self.root / d).mkdir(parents=True)
        monkeypatch.setattr(self.gtw, "R", self.root)
        self.sh_calls: list[tuple[list[str], dict | None]] = []
        self.popen_calls: list[list[str]] = []
        self.fetch_rc = 0
        self.index_writes = True
        self.joern_rc: int | None = None  # Popen.poll(): None = 떠 있다
        self.joern_log = "listening"
        self.port_up = True
        self.slicer_doc: dict = {"status": "ok", "slices": [{"id": "slice_0001"}]}
        self.slicer_exc: Exception | None = None
        self.build_rc = 0
        self.build_out = '{"sample_id": "s1", "slices": 1, "gt_relevant": 1, "gt_in_source": 1}'
        self.build_err = ""
        self.build_timeout = False
        self.job_json_seen: dict = {}
        self.build_argv: list[str] = []
        # the commit tree for prune_dirs: path -> text (`ls-tree` lists it, `cat-file` reads it)
        self.tree: dict[str, str] = {}
        monkeypatch.setattr(self.gtw, "sh", self.sh)
        monkeypatch.setattr(self.gtw, "free_port", lambda port: None)
        monkeypatch.setattr(self.gtw, "port_open", lambda port: self.port_up)
        monkeypatch.setattr(subprocess, "Popen", self.popen)
        monkeypatch.setattr(subprocess, "run", self.run_build)
        monkeypatch.setattr(os, "killpg", lambda pid, sig: None, raising=False)

    def submit(self, sid: str = "s1", where: str = "inbox", **fields) -> Path:  # noqa: ANN003
        job = {
            "sample_id": sid,
            "repo_url": "https://example.invalid/r.git",
            "commit": "0" * 40,
            "gt_points": [{"role": "sink", "file": "f.c", "line": 3, "line_end": 3}],
            "description": "plain",
            "project": "p",
            **fields,
        }
        path = self.root / where / f"{sid}.json"
        path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        return path

    def out(self, sid: str = "s1") -> Path:
        return self.root / "out" / sid

    def markers(self, sid: str = "s1") -> list[str]:
        return sorted(m for m in ("DONE", "FAILED", "RETRY") if (self.out(sid) / m).exists())

    # ---- 가짜 프로그램들 ----
    def sh(self, cmd, cwd=None, timeout=None, logf=None, env=None):  # noqa: ANN001, ANN201
        self.sh_calls.append((list(cmd), env))
        if cmd[:2] == ["git", "fetch"]:
            return self.fetch_rc
        if cmd[0] != "sh" and len(cmd) > 1 and cmd[1] == "-c":  # 인덱서
            if self.index_writes:
                (self.root / "work" / logf.parent.name / "index.json").write_text("{}")
            return 0
        if any(str(c).endswith("gt_slice.py") for c in cmd):
            if self.slicer_exc:
                raise self.slicer_exc
            out = Path(cmd[cmd.index("--out") + 1])
            out.write_text(json.dumps(self.slicer_doc), encoding="utf-8")
            return 0
        return 0

    def popen(self, argv, **kwargs):  # noqa: ANN001, ANN003, ANN202
        self.popen_calls.append(list(argv))
        kwargs["stdout"].write(self.joern_log)
        kwargs["stdout"].flush()
        return SimpleNamespace(pid=4242, poll=lambda: self.joern_rc)

    def run_build(self, argv, **kwargs):  # noqa: ANN001, ANN003, ANN202
        if argv[0] == "git":  # prune_dirs: `ls-tree` lists `self.tree`, `cat-file` reads one file
            if argv[1] == "ls-tree":
                return subprocess.CompletedProcess(argv, 0, "\n".join(self.tree), "")
            text = self.tree.get(argv[-1].removeprefix("FETCH_HEAD:"), "")
            return subprocess.CompletedProcess(argv, 0, text, "")
        self.build_argv = list(argv)
        self.job_json_seen = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        target = Path(argv[-1])
        if self.build_timeout:
            target.write_bytes(b"\x1f\x8b\x08")  # 시간 초과로 죽기 전에 반쯤 쓴 gzip
            raise subprocess.TimeoutExpired(argv, 3600)
        if self.build_rc == 0:
            with gzip.open(target, "wt", encoding="utf-8") as fh:
                fh.write("{}\n")
        else:
            target.write_bytes(b"\x1f\x8b\x08")  # 반쯤 쓰다 죽은 gzip
        return subprocess.CompletedProcess(argv, self.build_rc, self.build_out, self.build_err)

    def slicer_calls(self) -> int:
        return sum(
            1 for cmd, _ in self.sh_calls if any(str(c).endswith("gt_slice.py") for c in cmd)
        )


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BoxEnv:
    return BoxEnv(tmp_path, monkeypatch)


def test_the_real_subprocess_and_network_are_blocked(tmp_path: Path) -> None:
    """픽스처 바인딩이 autouse로 걸렸는지 — 안 걸렸으면 아래 테스트가 진짜를 부른다."""
    assert subprocess.run.__name__ == "refuse"


# ---------------------------------------------------------------- claim


def test_a_claim_never_replaces_a_running_claim(
    box: BoxEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """리눅스에서 `rename`은 `running/<sid>.json`이 있으면 조용히 덮어쓴다 — 같은 sid로 `gt_slice`가
    둘 돌고 두 번째의 `rmtree`가 살아 있는 run을 지운다. 이 호스트의 rename도 같은 의미로 만든다."""
    monkeypatch.setattr(Path, "rename", lambda self, target: os.replace(self, target))
    running = box.submit("s1", where="running", description="the live run")
    box.submit("s1", where="inbox", description="a duplicate")

    assert box.gtw.claim() is None

    assert json.loads(running.read_text(encoding="utf-8"))["description"] == "the live run"
    assert (box.root / "inbox" / "s1.json").exists()


def test_a_job_whose_sample_already_has_a_result_is_dropped_not_rerun(box: BoxEnv) -> None:
    (box.out() / "DONE").parent.mkdir(parents=True)
    (box.out() / "DONE").write_text("1")
    box.submit("s1")

    assert box.gtw.claim() is None

    assert not (box.root / "inbox" / "s1.json").exists()
    assert (box.out() / "DONE").exists(), "끝난 결과가 지워졌다"


def test_each_job_is_claimed_by_exactly_one_of_many_threads(box: BoxEnv) -> None:
    for i in range(60):
        box.submit(f"s{i:02d}")
    got: list[str] = []
    guard = threading.Lock()

    def take() -> None:
        while (path := box.gtw.claim()) is not None:
            with guard:
                got.append(path.stem)

    threads = [threading.Thread(target=take) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(got) == [f"s{i:02d}" for i in range(60)]


def test_a_claim_left_by_a_dead_worker_goes_back_to_the_inbox(box: BoxEnv) -> None:
    box.submit("s1", where="running")

    box.gtw.requeue_running()

    assert (box.root / "inbox" / "s1.json").exists()
    assert not (box.root / "running" / "s1.json").exists()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="flock은 POSIX 전용 — 상자는 리눅스다")
def test_a_second_worker_on_the_same_box_is_refused(box: BoxEnv) -> None:
    first = box.gtw.single_instance()
    try:
        with pytest.raises(SystemExit):
            box.gtw.single_instance()
    finally:
        first.close()


# ---------------------------------------------------------------- 스레드가 안 죽는다


def test_log_never_raises(box: BoxEnv) -> None:
    """RUN.log 쓰기가 실패해도(ENOSPC 등) 작업자 스레드를 죽이지 않는다."""
    (box.root / "RUN.log").mkdir()

    box.gtw.log("anything")


def test_a_failure_after_the_marker_does_not_end_the_worker_loop(
    box: BoxEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """루프 몸통의 `process()` 바깥 — 표지·정리·`unlink`·`log` — 이 전부 보호 밖이라 스레드가 조용히
    영구히 죽었다(리뷰 직접 측정: W=2에서 두 스레드가 한 작업씩 하고 죽었다)."""
    box.submit("s1")
    real_unlink = Path.unlink

    def refuses_the_running_file(self: Path, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if self.parent.name == "running":
            raise PermissionError("denied")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuses_the_running_file)

    box.gtw.step(0)  # 죽지 않는다

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert box.markers() == ["DONE"]
    assert not list((box.root / "running").glob("*.json")), "running/에 갇혔다"
    box.gtw.step(0)  # 이미 결과가 있는 작업 — 다시 돌리지 않고 버린다
    assert not list((box.root / "inbox").glob("*.json"))


def test_main_takes_the_box_lock_requeues_old_claims_then_supervises(
    box: BoxEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`main()`의 배선 — 각 함수는 따로 테스트되지만 호출이 빠지면(잠금 없는 두 번째 작업자,
    안 돌아오는 죽은 claim) 아무 데서도 안 걸린다."""
    calls: list[str] = []
    gtw = box.gtw
    monkeypatch.setattr(gtw, "single_instance", lambda: calls.append("lock") or "held")
    monkeypatch.setattr(gtw, "requeue_running", lambda: calls.append("requeue"))
    monkeypatch.setattr(gtw, "supervise", lambda threads: calls.append(f"supervise {len(threads)}"))

    class Idle:
        def __init__(self, target, args, daemon):  # noqa: ANN001
            self.args = args

        def start(self) -> None:
            calls.append(f"start {self.args[0]}")

    monkeypatch.setattr(gtw.threading, "Thread", Idle)

    gtw.main(["gt_worker.py", "2"])

    assert calls == ["lock", "requeue", "start 0", "start 1", "supervise 2"]


def test_the_supervisor_exits_nonzero_when_a_worker_thread_died(box: BoxEnv) -> None:
    alive = SimpleNamespace(is_alive=lambda: True)
    dead = SimpleNamespace(is_alive=lambda: False)

    with pytest.raises(SystemExit) as stop:
        box.gtw.supervise([alive, dead])

    assert stop.value.code == 1


# ---------------------------------------------------------------- 표지: DONE은 성공이다


@pytest.mark.usefixtures("cp949_default")
def test_a_clean_sample_ends_with_done_and_utf8_files(box: BoxEnv) -> None:
    """build.json·job.json이 em dash를 든다 — 상자 기본 코덱이 utf-8이 아니면 쓰기가 죽었다."""
    box.submit("s1", project="p — x", description="overflow — “q”")

    box.gtw.step(0)

    assert box.markers() == ["DONE"]
    build = json.loads((box.out() / "build.json").read_text(encoding="utf-8"))
    assert build["project"] == "p — x"
    assert box.job_json_seen["description"] == "overflow — “q”"
    with gzip.open(box.out() / "slices.json.gz", "rt", encoding="utf-8") as fh:
        assert json.load(fh)["status"] == "ok", "slices.json.gz가 진짜 gzip이 아니다"


def _clone_fails(box: BoxEnv) -> None:
    box.fetch_rc = 1


def _slicer_raises(box: BoxEnv) -> None:
    box.slicer_exc = OSError(28, "No space left on device")


def _no_slices(box: BoxEnv) -> None:
    box.slicer_doc = {"status": "ok", "slices": []}


def _build_fails(box: BoxEnv) -> None:
    box.build_rc, box.build_err = 1, "Traceback: boom"


def _build_hangs(box: BoxEnv) -> None:
    box.build_timeout = True


def _joern_dies(box: BoxEnv) -> None:
    box.joern_rc, box.joern_log = 137, "java.lang.OutOfMemoryError"


@pytest.mark.parametrize(
    ("setup", "marker", "words"),
    [
        pytest.param(_clone_fails, "RETRY", "clone failed", id="clone"),
        pytest.param(_slicer_raises, "RETRY", "exception", id="unexpected-exception"),
        pytest.param(_joern_dies, "RETRY", "OutOfMemoryError", id="joern-died-at-boot"),
        pytest.param(_no_slices, "FAILED", "status=ok", id="no-slices"),
        pytest.param(_build_fails, "FAILED", "boom", id="build-failed"),
        pytest.param(_build_hangs, "FAILED", "timed out", id="build-timeout"),
    ],
)
def test_a_failure_is_marked_by_what_it_was_never_as_done(
    box: BoxEnv,
    setup,
    marker: str,
    words: str,  # noqa: ANN001
) -> None:
    box.submit("s1")
    setup(box)

    box.gtw.step(0)

    assert box.markers() == [marker]
    build = json.loads((box.out() / "build.json").read_text(encoding="utf-8"))
    assert words in build["error"]


def test_a_joern_that_died_at_boot_is_not_waited_on_nor_sliced(
    box: BoxEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """대기 루프가 `server.poll()`을 안 봐 죽은 JVM을 301초 기다린 뒤 슬라이서를 붙였다."""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    box.submit("s1")
    _joern_dies(box)
    box.port_up = False

    box.gtw.step(0)

    assert box.slicer_calls() == 0
    assert len(sleeps) < 10, f"죽은 Joern을 {len(sleeps)}초 기다렸다"


@pytest.mark.parametrize("setup", [_build_fails, _build_hangs], ids=["failed", "timed-out"])
def test_a_failed_build_leaves_no_half_written_output(box: BoxEnv, setup) -> None:  # noqa: ANN001
    box.submit("s1")
    setup(box)

    box.gtw.step(0)

    assert not (box.out() / "auditor_inputs.jsonl.gz").exists()


def test_git_fetch_never_waits_for_a_credential_prompt(box: BoxEnv) -> None:
    box.submit("s1")

    box.gtw.step(0)

    (env,) = [e for cmd, e in box.sh_calls if cmd[:2] == ["git", "fetch"]]
    assert env is not None and env["GIT_TERMINAL_PROMPT"] == "0"


# ---------------------------------------------------------------- gt_slice.py


def run_gt_slice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gt_points: list[dict],
    symbols: list[dict],
    candidates: list[dict],
    checkpoint: object = None,
) -> dict:
    """진짜 슬라이서 없이 `gt_slice.py`를 돌려 `gt_filter`를 얻는다 — 슬라이서 모듈 둘을 가짜로."""
    gt, index = tmp_path / "gt.json", tmp_path / "index.json"
    gt.write_text(json.dumps({"gt_points": gt_points}), encoding="utf-8")
    index.write_text(json.dumps({"symbols": symbols}), encoding="utf-8")
    stage = SimpleNamespace(candidates=None, result={}, checkpoint=checkpoint)

    class Stages:
        def collect_candidates(self) -> None:
            self.candidates = list(candidates)

    stages = types.ModuleType("slicer_pipeline")
    stages.SlicingStages = Stages
    slicer = types.ModuleType("slicer")

    def main(args: list[str]) -> int:
        Stages.collect_candidates(stage)  # gt_slice.py가 갈아 끼운 것
        return 0

    slicer.main = main
    monkeypatch.setitem(sys.modules, "slicer", slicer)
    monkeypatch.setitem(sys.modules, "slicer_pipeline", stages)
    argv = ["gt_slice.py", str(tmp_path), str(gt), str(index), "--", "--x"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as done:
        runpy.run_path(str(TOOL_DIR / "gt_slice.py"), run_name="__main__")
    assert done.value.code == 0
    return stage.result["gt_filter"]


def cand(key: str, line: int) -> dict:
    return {
        "candidate_key": key,
        "entry": {"file": "f.c", "line": line},
        "anchors": [{"file": "f.c", "line": line + 1}],
    }


GT = [{"role": "sink", "file": "f.c", "line": 100, "line_end": 100}]
FUNC = [{"name": "f", "file": "f.c", "line_start": 90, "line_end": 110}]


def test_the_filter_says_function_when_every_gt_point_sits_in_an_index_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run_gt_slice(tmp_path, monkeypatch, GT, FUNC, [cand("near", 95), cand("far", 500)])

    assert result["mode"] == "gt_function"
    assert result["kept"] == 1 and result["discovered"] == 2
    assert result["gt_guided"] is True


def test_the_filter_says_window_when_a_gt_point_had_no_index_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """함수를 못 주는 인덱서(C++ 헤더)면 GT ±30줄로 떨어진다 — 라벨이 그 사실을 말해야 한다.
    `gt_function` 그대로면 README 「한계」의 근거 인용이 두 창을 못 가른다."""
    result = run_gt_slice(tmp_path, monkeypatch, GT, [], [cand("near", 95), cand("far", 500)])

    assert result["mode"] == "gt_window"
    assert result["kept"] == 1


def test_the_filter_falls_back_to_the_nearest_candidates_in_the_gt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run_gt_slice(tmp_path, monkeypatch, GT, FUNC, [cand("a", 300), cand("b", 400)])

    assert result["mode"] == "nearest_in_gt_file"
    assert result["kept"] == 2


def test_a_checkpoint_run_is_refused_not_silently_unfiltered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """원본이 필터 *전* 후보를 체크포인트에 이미 묶었다 — 복원·샤드 병합이 그것을 읽어 필터가 조용히
    무효가 된다(리뷰 재현: 체크포인트 후보 6개 대 manifest 1개)."""
    with pytest.raises(ValueError, match="checkpoint"):
        run_gt_slice(tmp_path, monkeypatch, GT, FUNC, [cand("near", 95)], checkpoint=object())


# ---------------------------------------------------------------- build_auditor.py


SINK_24 = [{"role": "sink", "file": "parser.c", "line": 24, "line_end": 24}]


def run_build_auditor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    slices: list[tuple] | None = None,
    slices_filter: dict | None = None,
    job_flag: bool | None = None,
    gt_points: list[dict] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[list[dict], dict]:
    """진짜 하네스 코드(`runner._auditor_source` 등)로 행을 만든다 — 모델도 Joern도 안 쓴다.
    `slices=None`이면 GT 직접 모드(슬라이스 인자 `-`)다. 행들과 스크립트의 전역을 돌려준다."""
    if slices is None:
        path: Path | str = "-"
    else:
        path = _slices_file(tmp_path, [_slice(*s) for s in slices])
        if slices_filter is not None:
            document = json.loads(path.read_text(encoding="utf-8"))
            document["gt_filter"] = slices_filter
            path.write_text(json.dumps(document), encoding="utf-8")
    index, job_file, out = tmp_path / "index.json", tmp_path / "job.json", tmp_path / "out.gz"
    index.write_text(json.dumps(_FIXTURE_INDEX), encoding="utf-8")
    job = {
        "sample_id": "s1",
        "kind": "vuln",
        "description": "d",
        "gt_points": gt_points if gt_points is not None else SINK_24,
    }
    if job_flag is not None:
        job["gt_guided"] = job_flag
    job_file.write_text(json.dumps(job), encoding="utf-8")
    git = subprocess.CompletedProcess([], 0, "abc1234\n", "")  # `git rev-parse`를 흉내 낸다
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: git)
    for key in ("AUDITOR_DEFINITIONS", "AUDITOR_OPTIONS", "AUDITOR_ARMS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    argv = ["build_auditor.py", *map(str, (REPO_ROOT, job_file, TARGET, path, index, out))]
    monkeypatch.setattr(sys, "argv", argv)
    scope = runpy.run_path(str(TOOL_DIR / "build_auditor.py"), run_name="__main__")
    capsys.readouterr()
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    return rows, scope


def build_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    slices_filter: dict | None,
    job_flag: bool | None,
) -> dict:
    (row,), _ = run_build_auditor(
        tmp_path, monkeypatch, capsys, [_CASE_A], slices_filter=slices_filter, job_flag=job_flag
    )
    return row


@pytest.mark.parametrize(
    ("slices_filter", "job_flag", "expected"),
    [
        pytest.param({"gt_guided": True}, None, True, id="slices-were-filtered-job-says-nothing"),
        pytest.param({"gt_guided": True}, False, True, id="slices-were-filtered-job-says-no"),
        pytest.param(None, True, True, id="job-says-so"),
        pytest.param(None, None, False, id="neither"),
    ],
)
def test_a_row_built_from_gt_filtered_slices_says_so_whatever_the_job_says(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    slices_filter: dict | None,
    job_flag: bool | None,
    expected: bool,
) -> None:
    row = build_row(tmp_path, monkeypatch, capsys, slices_filter, job_flag)

    assert row["gt_guided"] is expected


def test_a_row_runs_on_the_harness_source_assembly_with_guards_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """main의 GUARDS(`auditor_guards`, #349)가 `ctx.read_raw`를 읽는다 — 빌더의 ctx에 그것이 없어
    main과 합친 트리에서 행 하나 못 만들고 `AttributeError`로 죽었다(2026-10-08 재검토 사실 1)."""
    (row,), _ = run_build_auditor(tmp_path, monkeypatch, capsys, [_CASE_B])

    assert "auditor_guards" in row["auditor_options"]
    assert "GUARDS" in row["source"], "Case B인데 GUARDS 블록이 안 붙었다 — 아래 테스트의 전제"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        pytest.param(26, True, id="last-line-of-the-entry-function"),
        pytest.param(27, False, id="line-after-the-function-where-guards-sit"),
        pytest.param(30, True, id="caller-window-line"),
    ],
)
def test_gt_in_source_counts_only_lines_inside_a_banner_never_the_guards_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    line: int,
    expected: bool,
) -> None:
    """GUARDS 블록은 entry 함수(`parser.c:19-26`) 바로 뒤에 배너 없이 붙는다 — 그 줄을 함수 다음 줄
    (27·28…)로 세어 `gt_in_source`가 거짓 양성이 됐다(2026-10-08 보충 지적 2)."""
    pts = [{"role": "sink", "file": "parser.c", "line": line, "line_end": line}]

    (row,), _ = run_build_auditor(tmp_path, monkeypatch, capsys, [_CASE_B], gt_points=pts)

    assert "GUARDS" in row["source"]
    assert row["gt_in_source"] is expected


@pytest.mark.parametrize(
    ("point", "relevant", "in_source"),
    [
        pytest.param(("parser.c", 24), True, True, id="on-a-slice-line"),
        pytest.param(("parser.c", 30), False, True, id="shown-but-far-from-the-slice"),
        pytest.param(("util.c", 3), False, False, id="another-file"),
    ],
)
def test_gt_relevant_and_gt_in_source_say_what_they_measure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    point: tuple[str, int],
    relevant: bool,
    in_source: bool,
) -> None:
    """`gt_relevant` = 슬라이스 줄이 GT 점 ±2 안 · `gt_in_source` = SOURCE가 그 줄을 보여 준다. 둘을
    단언하는 테스트가 없었다(보충 지적 1)."""
    pts = [{"role": "sink", "file": point[0], "line": point[1], "line_end": point[1]}]

    (row,), _ = run_build_auditor(tmp_path, monkeypatch, capsys, [_CASE_A], gt_points=pts)

    assert (row["gt_relevant"], row["gt_in_source"]) == (relevant, in_source)


def test_gt_direct_mode_builds_one_row_around_the_gt_sink_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """슬라이스 인자 `-` — 슬라이서 없이 GT 점이 슬라이스다. GT가 SOURCE를 정하므로 행이 그것을
    말해야 하고(`gt_direct`·`gt_guided`) `gt_relevant`는 구조상 참이다."""
    pts = [  # sink가 처음도 끝도 아니다 — entry는 순서가 아니라 role로 고른다
        {"role": "root_cause", "file": "parser.c", "line": 30, "line_end": 30},
        {"role": "sink", "file": "parser.c", "line": 24, "line_end": 24},
        {"role": "root_cause", "file": "parser.c", "line": 31, "line_end": 31},
    ]

    (row,), _ = run_build_auditor(tmp_path, monkeypatch, capsys, None, gt_points=pts)

    assert (row["slice_id"], row["gt_direct"], row["gt_guided"]) == ("gt_direct", True, True)
    assert (row["entry"]["file"], row["entry"]["line"]) == ("parser.c", 24)
    assert row["anchor"]["symbol"] == "parse_header"
    assert (row["gt_relevant"], row["gt_in_source"]) == (True, True)


def test_auditor_arms_give_one_row_per_slice_per_option_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    arms = "off:auditor_guards=0;on:auditor_guards=1"

    rows, _ = run_build_auditor(
        tmp_path, monkeypatch, capsys, [_CASE_B], env={"AUDITOR_ARMS": arms}
    )

    by_arm = {r["arm"]: r for r in rows}
    assert sorted(by_arm) == ["off", "on"]
    assert "auditor_guards" not in by_arm["off"]["auditor_options"]
    assert "auditor_guards" in by_arm["on"]["auditor_options"]
    assert "GUARDS" not in by_arm["off"]["source"]
    assert "GUARDS" in by_arm["on"]["source"]


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param("auditor_nope=1", id="unknown-name"),
        pytest.param("auditor_callee_hops=5", id="out-of-bounds"),
    ],
)
def test_auditor_options_outside_the_harness_list_or_bounds_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, spec: str
) -> None:
    with pytest.raises((SystemExit, ValueError)):
        run_build_auditor(tmp_path, monkeypatch, capsys, [_CASE_A], env={"AUDITOR_OPTIONS": spec})


def test_shown_lines_reads_the_omission_marker_and_the_bannerless_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _, scope = run_build_auditor(tmp_path, monkeypatch, capsys, [_CASE_A])
    shown_lines = scope["shown_lines"]
    anchor = SimpleNamespace(file="f.c", line_start=10)

    assert shown_lines("a\nb", anchor) == {("f.c", 10), ("f.c", 11)}
    trimmed = "/* ===== f.c:10-20 ===== */\nx\n/* ... 5 lines omitted ... */\ny"
    assert shown_lines(trimmed, anchor) == {("f.c", 10), ("f.c", 16)}


# ---------------------------------------------------------------- 큰 저장소 자르기 · GT 직접 모드


def test_pick_prefers_the_including_directory_then_the_shortest_path_then_x86() -> None:
    gtw = load("gt_worker")
    local = ["src/a/y.h", "y.h"]
    tie = ["arch/arm/asm/io.h", "arch/x86/asm/io.h"]

    assert gtw._pick(local, "src/a", "y.h") == "src/a/y.h"
    assert gtw._pick(["deep/er/y.h", "src/y.h"], "lib", "y.h") == "src/y.h"
    assert gtw._pick(tie, "drivers", "asm/io.h") == "arch/x86/asm/io.h"
    assert gtw._pick([], "src", "stdio.h") is None


def test_a_big_repo_keeps_the_gt_directory_and_the_headers_it_includes(
    box: BoxEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C/C++ 파일이 `PRUNE_FILES`를 넘으면 sparse checkout — GT 파일의 디렉터리와 그 파일이
    include하는 헤더의 디렉터리(인자 아키텍처 사본은 짧은 경로 하나)와 작은 최상위 `include/`."""
    monkeypatch.setattr(box.gtw, "PRUNE_FILES", 3)
    box.tree = {
        "src/a/x.c": '#include "y.h"\n#include <linux/usb.h>\n',
        "src/a/y.h": "",
        "include/linux/usb.h": '#include "deep/d.h"\n',
        "include/linux/deep/d.h": "",
        "arch/arm/include/linux/usb.h": "",
        "other/z.c": "",
        "other/w.c": "",
    }
    box.submit("s1", gt_points=[{"role": "sink", "file": "src/a/x.c", "line": 3, "line_end": 3}])

    box.gtw.step(0)

    assert box.markers() == ["DONE"]
    pruned = json.loads((box.out() / "build.json").read_text(encoding="utf-8"))["pruned"]
    assert pruned["keep"] == ["include", "include/linux", "include/linux/deep", "src/a"]
    assert pruned["c_files"] == 7
    sparse = [cmd for cmd, _ in box.sh_calls if cmd[:3] == ["git", "sparse-checkout", "set"]]
    assert sparse == [["git", "sparse-checkout", "set", *pruned["keep"]]]


def test_gt_direct_mode_skips_joern_and_builds_from_the_gt_lines(
    box: BoxEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(box.gtw, "GT_DIRECT", True)
    box.submit("s1")

    box.gtw.step(0)

    assert box.markers() == ["DONE"]
    assert box.popen_calls == [], "Joern이 떴다"
    assert box.slicer_calls() == 0
    assert box.build_argv[5] == "-"
    assert json.loads((box.out() / "build.json").read_text(encoding="utf-8"))["gt_direct"] is True
