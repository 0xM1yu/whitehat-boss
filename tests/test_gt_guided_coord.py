"""`tools/gt_guided/coord.py` — 사용자 PC의 코디네이터. PR #416 리뷰(2026-10-03)가 재현한 결함.

막으려는 명제 둘:
1. **돈 — 이 코디네이터는 「아직 안 받은 것이 있는 상자」를 지우지 않는다.** 삭제 문은
   `state.json`의 개수만이 아니라 jobs.json의 표본 집합 전체와 상자의 실제 보유 목록을 본다.
2. **데이터 — 받은 결과는 어떤 순서·재시작에서도 사라지지 않는다.** 상자 사본은 「받았다」가
   디스크에 적힌 *뒤에* 지우고, 배치 파일은 있던 것을 덮지 않는다.

가짜 상자(`tests/gt_guided_support.py`)가 `subprocess.run`을 대신한다 — 실제 ssh·scp·vastai는
한 번도 안 불린다. 🔴 **진짜 vast 상자와 진짜 ssh로는 이 파일이 아무것도 재지 않는다**(미실측).
"""

import json
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import gt_guided_support as support
from tests.gt_guided_support import (
    SIDS,
    Crash,
    batch_members,
    drive,
    log_text,
    make_campaign,
    plant_staged,
    run_main,
)

hermetic = support.hermetic  # autouse: pytest finds a fixture bound in the test module
cp949_default = support.cp949_default


# ---------------------------------------------------------------- 정상 경로(회귀 핀)


def test_the_real_subprocess_and_network_are_blocked() -> None:
    """픽스처 바인딩이 autouse로 걸렸는지 — 안 걸렸으면 아래 테스트가 진짜를 부른다."""
    assert subprocess.run.__name__ == "refuse"
    assert urllib.request.urlopen.__name__ == "refuse"


def test_a_clean_campaign_pulls_everything_batches_it_and_destroys_every_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch, box_ids=(101, 102))

    assert drive(c) is True

    assert batch_members(c) == [f"{s}.tgz" for s in SIDS]
    assert sorted(a[3] for a in c.fleet.vastai) == ["101", "102"]
    assert set(c.coord.st["pulled"]) == set(SIDS)


def test_resuming_with_the_same_jobs_file_keeps_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)
    c.coord.tick()
    assigned = dict(c.coord.st["assigned"])

    c.coord.init(c.jobs, c.boxes)  # 프로세스 재시작

    assert c.coord.st["assigned"] == assigned


# ---------------------------------------------------------------- CRITICAL-1: 삭제 문


def test_a_state_file_left_by_another_campaign_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """끝난 캠페인의 state.json(pending 빔, pulled == finished) + 새 jobs.json → 1회차에 전 상자를
    지우던 결함. 2026-10-03 e2e가 정확히 이 파일을 체크아웃 안에 남긴다."""
    c = make_campaign(tmp_path, monkeypatch)
    assert drive(c) is True
    c.fleet.vastai.clear()
    other = tmp_path / "other_jobs.json"
    other.write_text(
        json.dumps([{"sample_id": f"x{i}", "repo_url": "u", "commit": "c"} for i in range(12)]),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        c.coord.init(other, c.boxes)

    assert c.fleet.vastai == []


def test_no_box_is_destroyed_while_a_job_is_missing_from_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """개수만 맞고(3 == 3) 집합이 어긋난 상태 — 작업 하나가 어느 장부에도 없다."""
    c = make_campaign(tmp_path, monkeypatch)
    st = c.coord.st
    st.update(pending=[], assigned={}, finished={s: "101" for s in "abc"}, pulled=list("abc"))
    c.coord.save()
    c.coord.init(c.jobs, c.boxes)

    for _ in range(3):
        assert c.coord.tick() is False

    assert c.fleet.vastai == []


def test_a_box_that_still_holds_unpulled_output_is_not_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """장부는 다 받았다는데 상자 `out/`에 안 받은 표본이 있다 — 그 상자만 남기고 나머지는 지운다."""
    c = make_campaign(tmp_path, monkeypatch, box_ids=(101, 102))
    c.fleet.box(101).finish("ghost")

    done = drive(c, ticks=12)

    assert done is False
    assert [a[3] for a in c.fleet.vastai] == ["102"]
    assert "ghost" in log_text(c)


def test_a_box_that_cannot_be_inspected_is_not_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)
    box = c.fleet.box(101)
    real_ssh = box.ssh

    def refuses_inspection(cmd: str):  # noqa: ANN202
        if "echo '#inbox'" in cmd:
            return 255, ""
        return real_ssh(cmd)

    box.ssh = refuses_inspection

    assert drive(c, ticks=12) is False
    assert c.fleet.vastai == []


def test_a_failed_destroy_is_reported_and_given_up_after_three_tries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)
    c.fleet.vastai_rc = 1

    assert drive(c) is True

    assert len(c.fleet.vastai) == 3
    assert "destroy by hand" in log_text(c)
    assert "101" in log_text(c)


def test_a_missing_vastai_binary_does_not_crash_the_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)
    c.fleet.vastai_missing = True

    assert drive(c) is True
    assert "destroy by hand" in log_text(c)


def test_main_exits_nonzero_when_a_box_could_not_be_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)
    c.fleet.vastai_rc = 1

    assert run_main(c) == 1


# ---------------------------------------------------------------- CRITICAL-2: 배치 덮어쓰기


def test_a_batch_file_already_on_disk_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """카운터가 untracked state.json에만 있어 `git clean`·새 worktree·두 번째 코디네이터면 1로
    돌아가 batch001을 덮었다 — 그 시점에 상자 사본은 이미 없다."""
    c = make_campaign(tmp_path, monkeypatch)
    precious = c.coord.DL / "ft_gt_auditor_batch001.tar"
    precious.write_bytes(b"PRECIOUS")

    assert drive(c) is True

    assert precious.read_bytes() == b"PRECIOUS"
    assert batch_members(c, skip=(precious,)) == [f"{s}.tgz" for s in SIDS]


# ---------------------------------------------------------------- HIGH: 순서·재시작


def test_the_box_copy_is_deleted_only_after_the_pull_is_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)

    assert drive(c) is True

    deletions = c.fleet.box(101).deleted
    assert deletions, "상자 사본을 지우는 호출이 없다 — 이 테스트가 아무것도 안 잰다"
    for sids, saved in deletions:
        assert set(sids) <= saved, f"{sids}를 지울 때 디스크의 pulled는 {saved}였다"


def test_a_crash_between_the_pull_and_the_save_does_not_strand_the_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """리뷰 HIGH-1 — 받은 뒤 저장 전에 죽으면 재시작 때 같은 sid가 다시 unpulled인데 상자 사본은
    이미 없어 `tar`가 실패하고, 박스가 영영 안 지워져 계속 과금된다."""
    c = make_campaign(tmp_path, monkeypatch)
    real_save, crashed = c.coord.save, []

    def dies_after_the_pull() -> None:
        if c.coord.st["pulled"] and not crashed:
            crashed.append(True)
            raise Crash
        real_save()

    c.coord.save = dies_after_the_pull
    with pytest.raises(Crash):
        drive(c)
    c.coord.save = real_save

    c.coord.init(c.jobs, c.boxes)  # 재시작

    assert drive(c, ticks=20) is True
    assert batch_members(c) == [f"{s}.tgz" for s in SIDS]


def test_staged_samples_left_by_a_dead_run_are_counted_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상자 사본이 이미 없고(옛 순서가 남긴 모양) STAGE에만 남은 표본 — 읽어 들이는 코드가 없어 영영
    unpulled였다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b"])
    st = c.coord.st
    st.update(pending=[], assigned={}, finished={"a": "101", "b": "101"}, pulled=[])
    c.coord.save()
    plant_staged(c, "a")
    plant_staged(c, "b")
    c.coord.init(c.jobs, c.boxes)

    assert drive(c, ticks=12) is True
    assert batch_members(c) == ["a.tgz", "b.tgz"]


def test_a_staged_file_is_complete_or_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stage 도중 죽어도 STAGE에는 끝까지 쓴 `<sid>.tgz`만 있다 — 반쯤 쓴 파일을 재시작이 표본으로
    읽어 들이면 안 된다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b"])
    box = c.fleet.box(101)
    box.finish("a")
    box.finish("b")
    raw = tmp_path / "raw.tgz"
    with tarfile.open(raw, "w:gz") as tf:
        for s in ("a", "b"):
            tf.add(box.root / "out" / s, arcname=s)
    c.coord.STAGE.mkdir()
    real_add = tarfile.TarFile.add

    def dies_on_the_second_sample(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if kwargs.get("arcname") == "b":  # 표본 단위 호출만 — 디렉터리 안쪽은 add가 재귀로 부른다
            self.fileobj.write(b"half")
            raise Crash
        return real_add(self, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "add", dies_on_the_second_sample)
    with pytest.raises(Crash):
        c.coord.stage(raw)
    monkeypatch.setattr(tarfile.TarFile, "add", real_add)

    for path in c.coord.STAGE.glob("*.tgz"):
        with tarfile.open(path, "r:gz") as tf:
            assert tf.getnames(), f"{path.name}이 반쯤 쓰인 채 STAGE에 남았다"


def test_a_pull_timeout_or_an_empty_checksum_does_not_kill_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ssh`의 TimeoutExpired와 `out.strip().split()[-1]`의 IndexError가 루프를 죽였다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b"])
    box = c.fleet.box(101)
    real_ssh = box.ssh

    def times_out_on_tar(cmd: str):  # noqa: ANN202
        if "tar czf" in cmd:
            raise subprocess.TimeoutExpired(cmd, 1800)
        return real_ssh(cmd)

    box.ssh = times_out_on_tar
    for _ in range(3):
        box.work()
        assert c.coord.tick() is False  # 죽지 않고 다음 바퀴를 기다린다
    box.ssh = real_ssh
    box.empty_sha = True
    for _ in range(2):
        assert c.coord.tick() is False
    box.empty_sha = False

    assert drive(c, ticks=10) is True
    assert "pull failed" in log_text(c)


def _broken_pull(c: SimpleNamespace) -> dict:
    real = c.coord.pull_chunk
    state = {"broken": True}

    def pull_chunk(box, chunk):  # noqa: ANN001, ANN202
        if state["broken"]:
            raise TypeError("a bug")
        return real(box, chunk)

    c.coord.pull_chunk = pull_chunk
    return state


def test_a_step_that_recovers_starts_its_failure_count_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b"])
    state = _broken_pull(c)
    for _ in range(3):  # 첫 바퀴는 작업을 밀기만 한다 — 받기 시도는 둘째 바퀴부터다
        c.fleet.box(101).work()
        assert c.coord.tick() is False
    assert list(c.coord.failing.values()) == [2]

    state["broken"] = False
    c.coord.tick()

    assert c.coord.failing == {}


def test_a_box_that_keeps_timing_out_is_requeued_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """안 닿는 박스는 실패가 아니라 상태다 — ssh 시간 초과가 한 단계의 연속 실패로 세어져
    코디네이터를 멈추게 하면 안 되고, 30분 뒤 배정이 되돌려져야 한다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b"], box_ids=(101, 102))
    c.coord.tick()
    assert c.coord.st["assigned"]
    for box in c.fleet.boxes.values():
        box.down = "timeout"

    for _ in range(c.coord.FAIL_LIMIT + 3):
        c.clock.now += 400
        assert c.coord.tick() is False

    assert not c.coord.st["assigned"]
    assert sorted(c.coord.st["pending"]) == ["a", "b"]


def test_a_step_that_keeps_failing_stops_the_run_with_its_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 번의 실패는 로그만 남기고 넘어가지만, 같은 단계가 계속 죽으면(버그·깨진 파일) 조용히 영영
    돌지 않는다 — 안 보이는 무한 루프는 박스를 계속 과금한다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b"])
    _broken_pull(c)

    with pytest.raises(TypeError):
        for _ in range(c.coord.FAIL_LIMIT + 1):
            c.fleet.box(101).work()
            c.coord.tick()


def _one_member_tar(name: str, kind: bytes = tarfile.REGTYPE) -> tarfile.TarFile:
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name)
        info.type = kind
        if kind == tarfile.SYMTYPE:
            info.linkname = "/etc/passwd"
            tf.addfile(info)
        else:
            info.size = 1
            tf.addfile(info, io.BytesIO(b"x"))
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r:gz")


def test_a_python_without_tar_extraction_filters_still_pulls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3.11.4 미만(이 팀 PC의 3.11.0)이면 `_extract`가 같은 거부를 손으로 하고 푼다 — 시작을 막으면
    그 PC에서 코디네이터가 아예 안 돈다. `tarfile.data_filter`를 지우는 대신 `TAR_FILTERS`를 끈다 —
    3.14의 `tarfile`은 그 이름을 기본 필터로 스스로 읽어 지우면 `NameError`로 죽었다."""
    c = make_campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(c.coord, "TAR_FILTERS", False)

    assert run_main(c) == 0
    assert batch_members(c) == [f"{s}.tgz" for s in SIDS]


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        pytest.param("../escape", tarfile.REGTYPE, id="dot-dot"),
        pytest.param("/abs", tarfile.REGTYPE, id="absolute"),
        pytest.param("\\abs", tarfile.REGTYPE, id="absolute-backslash"),
        pytest.param("link", tarfile.SYMTYPE, id="symlink"),
    ],
)
def test_the_manual_extract_refuses_every_member_the_data_filter_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, kind: bytes
) -> None:
    """손 경로의 거부 셋(`..` · 절대 경로 · 파일·디렉터리 아닌 것)을 각각 문다 — `..` 하나만 보던
    테스트로는 나머지 둘을 빼도 초록이었다(2026-10-08 보충 리뷰 변이)."""
    c = make_campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(c.coord, "TAR_FILTERS", False)

    with _one_member_tar(name, kind) as tf, pytest.raises(ValueError, match="refusing"):
        c.coord._extract(tf, tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_a_corrupt_download_is_logged_and_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)
    c.fleet.box(101).corrupt_downloads = 1

    assert drive(c) is True

    assert "sha256 mismatch" in log_text(c)
    assert batch_members(c) == [f"{s}.tgz" for s in SIDS]


def test_a_long_pull_does_not_make_a_healthy_box_look_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pull은 한 시간도 블록한다. 그동안 폴링이 없어 `last_seen`이 낡는데, 그 뒤 ssh가 한 번만
    흔들려도 건강한 박스의 배정이 통째로 재큐됐다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b", "c"], workers=1, pull_min=1)
    box = c.fleet.box(101)
    box.slow_download_s = 3600
    c.coord.tick()  # a, b 배정
    box.work()
    c.clock.now += 45
    c.coord.tick()  # a, b를 받는 데 한 시간, 그 사이 c를 배정
    assert "c" in c.coord.st["assigned"]
    box.down = "refuse"
    c.clock.now += 45

    c.coord.tick()

    assert "c" in c.coord.st["assigned"], "한 번의 ssh 실패로 건강한 박스가 unreachable이 됐다"


# ---------------------------------------------------------------- HIGH: 비ASCII


@pytest.mark.usefixtures("cp949_default")
def test_non_ascii_job_fields_reach_the_box_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """em dash·굽은 따옴표·한글이 든 description — cp949 PC에서 쓰기가 죽거나, 쓰여도 상자의 utf-8
    읽기가 죽었다(리뷰 직접 측정)."""
    text = "overflow — “quote” é 한글"
    c = make_campaign(tmp_path, monkeypatch, sids=["a"], descriptions={"a": text})

    c.coord.tick()

    assert c.fleet.box(101).jobs["a"]["description"] == text


# ---------------------------------------------------------------- HIGH: DONE != 성공


def test_a_failed_sample_is_counted_as_failed_not_as_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)

    assert drive(c, marker_of=lambda s: "FAILED" if s == "b" else "DONE") is True

    assert set(c.coord.st["failed"]) == {"b"}
    assert c.fleet.box(101).pushed.count("b") == 1, "영구 실패를 다시 밀었다"
    assert batch_members(c) == [f"{s}.tgz" for s in SIDS], "실패 표본의 build.json도 받아 온다"
    assert "failed 1" in log_text(c)


def test_a_transient_failure_is_retried_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)
    box = c.fleet.box(101)
    seen: list[str] = []

    def retry_first_time(sid: str) -> str:
        seen.append(sid)
        return "RETRY" if sid == "c" and seen.count("c") == 1 else "DONE"

    assert drive(c, marker_of=retry_first_time) is True

    assert box.pushed.count("c") == 2
    assert "c" not in c.coord.st["failed"]
    assert batch_members(c) == [f"{s}.tgz" for s in SIDS]


def test_a_transient_failure_that_keeps_failing_is_given_up_not_retried_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)

    assert drive(c, marker_of=lambda s: "RETRY" if s == "c" else "DONE") is True

    assert c.fleet.box(101).pushed.count("c") == 2
    assert set(c.coord.st["failed"]) == {"c"}


def test_a_done_marker_from_a_box_the_sample_was_not_assigned_to_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """다른 상자에 남은 옛 `out/<sid>/DONE`을 이 표본의 결과로 받아 가던 결함."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a"], box_ids=(101, 102), workers=1)
    c.coord.tick()
    assert c.coord.st["assigned"]["a"]["box"] == "101"
    c.fleet.box(102).finish("a", error="stale leftover")

    c.coord.tick()

    assert "a" in c.coord.st["assigned"]
    assert "a" not in c.coord.st["finished"]


# ---------------------------------------------------------------- 부수


def test_the_coordinator_never_posts_to_the_real_webhook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hermetic 픽스처가 HOME을 비웠으므로 웹훅 주소를 못 읽는다 — 배치 알림은 「건너뜀」이다."""
    c = make_campaign(tmp_path, monkeypatch)

    assert drive(c) is True
    assert "notify skipped" in log_text(c)
