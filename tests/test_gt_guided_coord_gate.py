"""`tools/gt_guided/coord.py` — 게이트 리뷰(2026-10-04)가 짚은 둘: 삭제 거부의 출구와 살아남은 변이.

막으려는 명제: **조회가 안 되거나 안 받은 것이 든 상자를 코디네이터는 자동으로 지우지 않지만, 그것을
이유로 영영 기다리지도 않는다.** `INSPECT_TRIES`번 연속 거부되면 포기하고 「손으로 지우라」고 알리며
종료 코드 1로 끝난다 — 기다리면 그 상자가 계속 과금된다. 아래 후반은 변이 확인에서 살아남은 방어선을
문다. 부품과 가짜 상자는 `tests/gt_guided_support.py` — 실 ssh·scp·vastai는 한 번도 안 불린다.
"""

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests import gt_guided_support as support
from tests.gt_guided_support import (
    SIDS,
    Crash,
    drive,
    log_text,
    make_campaign,
    plant_staged,
    run_main,
)

hermetic = support.hermetic  # autouse: pytest finds a fixture bound in the test module


def test_the_real_subprocess_and_network_are_blocked() -> None:
    """픽스처 바인딩이 autouse로 걸렸는지 — 안 걸렸으면 아래 테스트가 진짜를 부른다."""
    assert subprocess.run.__name__ == "refuse"


def refuse_inspection(box, passes: int | None = None) -> None:
    """상자의 조회(`holdings`) ssh를 `passes`번 실패시킨다 — None이면 영구히."""
    real_ssh, left = box.ssh, [passes]

    def ssh(cmd: str):  # noqa: ANN202
        if "echo '#inbox'" in cmd and (left[0] is None or left[0] > 0):
            if left[0] is not None:
                left[0] -= 1
            return 255, ""
        return real_ssh(cmd)

    box.ssh = ssh


def test_a_box_that_can_never_be_inspected_is_given_up_not_waited_on_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """표본을 한 건도 안 받은 상자의 ssh가 영구히 끊겼다 — 조회 불가 분기에 상한이 없어 `tick()`이
    영영 True가 아니었고(60틱 뒤에도 로그에 `cannot look` 60번) 사람에게 알리지도 않았다. 지우지는
    않는다(안 받은 산출물이 있을 수 있다) — 포기하고 손으로 지우라고 알린다."""
    c = make_campaign(tmp_path, monkeypatch, box_ids=(101, 102))
    c.fleet.box(102).down = "refuse"

    assert drive(c, ticks=60) is True

    assert [a[3] for a in c.fleet.vastai] == ["101"], "조회가 안 되는 상자를 지웠다"
    assert c.coord.gave_up == ["102"]
    assert "destroy by hand: vastai destroy instance 102" in log_text(c)


def test_a_box_holding_unpulled_output_is_given_up_not_waited_on_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch, box_ids=(101, 102))
    ghost = c.fleet.box(101)
    ghost.finish("ghost")

    assert drive(c, ticks=60) is True

    assert [a[3] for a in c.fleet.vastai] == ["102"]
    assert c.coord.gave_up == ["101"]
    assert (ghost.root / "out" / "ghost").is_dir(), "안 받은 산출물에 손을 댔다"
    assert "destroy by hand: vastai destroy instance 101" in log_text(c)
    assert "ghost" in log_text(c)


def test_a_box_that_recovers_from_a_hiccup_is_destroyed_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)
    refuse_inspection(c.fleet.box(101), passes=3)

    assert drive(c, ticks=30) is True

    assert [a[3] for a in c.fleet.vastai] == ["101"]
    assert c.coord.gave_up == []


@pytest.mark.parametrize("extra", [-1, 0])
def test_the_refusal_limit_is_exactly_inspect_tries_in_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: int
) -> None:
    """한 번 모자라면 회복해 지워지고, 꼭 그만큼이면 포기한다."""
    c = make_campaign(tmp_path, monkeypatch)
    refuse_inspection(c.fleet.box(101), passes=c.coord.INSPECT_TRIES + extra)

    assert drive(c, ticks=c.coord.INSPECT_TRIES + 10) is True

    assert bool(c.fleet.vastai) is (extra < 0)
    assert (c.coord.gave_up == ["101"]) is (extra >= 0)


def flaky_inspection(box, refuse: int) -> None:
    """상자의 조회가 `refuse`번 거부되고 한 번 풀리기를 되풀이한다."""
    real_ssh, looks = box.ssh, []

    def ssh(cmd: str):  # noqa: ANN202
        if "echo '#inbox'" in cmd:
            looks.append(1)
            if (len(looks) - 1) % (refuse + 1) < refuse:
                return 255, ""
        return real_ssh(cmd)

    box.ssh = ssh


def test_the_refusal_count_starts_over_after_a_clean_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「연속」이다 — 조회가 풀린 틱이 끼면 거부 횟수는 0에서 다시 센다. 풀린 틱마다 vastai가
    실패하게 해 두면 15 + 15번의 거부가 20을 넘지만 연속이 아니라서 포기 사유는 삭제 실패다."""
    c = make_campaign(tmp_path, monkeypatch)
    c.fleet.vastai_rc = 1
    flaky_inspection(c.fleet.box(101), c.coord.INSPECT_TRIES * 3 // 4)

    assert drive(c, ticks=200) is True

    assert "destroy FAILED" in log_text(c)
    assert "still refused" not in log_text(c)


def test_main_exits_nonzero_when_a_box_keeps_refusing_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch, box_ids=(101, 102))
    c.fleet.box(102).down = "refuse"

    assert run_main(c) == 1


def test_a_box_whose_release_failed_is_still_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """원격 삭제(release)가 실패해도 받은 표본의 잔재는 받아들인다 — 안 그러면 영영 안 지워진다."""
    c = make_campaign(tmp_path, monkeypatch)
    box = c.fleet.box(101)
    box.fail_release = True

    assert drive(c) is True

    assert [a[3] for a in c.fleet.vastai] == ["101"]
    assert box.on_box("out") == sorted(SIDS), "release가 안 먹었다 — 이 테스트가 아무것도 안 잰다"


def test_a_staged_file_of_another_campaign_is_not_counted_as_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STAGE(`~/Downloads/ft_gt_tmp`)는 캠페인 사이에 공유된다. 이 캠페인이 끝났다고 센 적 없는
    sid의 옛 tgz를 「받았다」로 세면 그 표본의 상자 사본을 안 받고 지운다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b"])
    plant_staged(c, "a")

    c.coord.init(c.jobs, c.boxes)

    assert c.coord.st["pulled"] == []


def test_a_batch_that_fails_halfway_leaves_no_file_and_keeps_the_staged_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch, sids=["a"])
    staged = plant_staged(c, "a")

    def disk_full(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(tarfile.TarFile, "add", disk_full)
    with pytest.raises(OSError, match="space"):
        c.coord.write_batch([staged])

    assert list(c.coord.DL.glob("ft_gt_auditor_batch*.tar")) == []
    assert staged.exists()


def test_a_tar_that_lacks_a_requested_sample_is_not_recorded_as_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """원격 tar가 rc 0으로 표본을 빼먹어도 「받았다」로 적고 상자 사본을 지우면 안 된다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b"])
    box = c.fleet.box(101)
    box.omit_from_tar = {"b"}
    for _ in range(3):
        box.work()
        c.coord.tick()

    assert c.coord.st["pulled"] == []
    assert box.on_box("out") == ["a", "b"] and box.deleted == []
    assert "not in the tar" in log_text(c)

    box.omit_from_tar = set()
    assert drive(c, ticks=10) is True


def test_the_pull_threshold_holds_until_every_job_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`PULL_MIN`에 못 미치고 남은 작업이 있으면 받지 않는다 — 다 끝나면 문턱과 무관하게 받는다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b", "c"], workers=1, pull_min=3)
    box = c.fleet.box(101)
    box.stuck = {"c"}
    for _ in range(3):
        box.work()
        c.coord.tick()
    assert not any("tar czf" in cmd for cmd in box.ssh_calls)

    box.stuck = set()
    assert drive(c, ticks=10) is True
    assert any("tar czf" in cmd for cmd in box.ssh_calls)


@pytest.mark.parametrize("where", ["inbox", "running", "out"])
def test_every_part_of_the_box_is_inspected_before_it_is_destroyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """상자의 세 구역 어디에 안 받은 것이 있어도 그 상자는 지우지 않는다."""
    c = make_campaign(tmp_path, monkeypatch)
    box = c.fleet.box(101)
    if where == "out":
        box.finish("ghost")
    else:
        box.stuck = {"ghost"}
        (box.root / where / "ghost.json").write_text("{}", encoding="utf-8")

    assert drive(c, ticks=12) is False

    assert c.fleet.vastai == []
    assert f"{where}/ghost" in log_text(c)


# ---------------------------------------------- 게이트 리뷰 2차: 저장 시점 · 못 뜬 박스 · 방어 가지


def test_a_box_destroyed_before_a_crash_is_not_looked_at_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`destroy` 성공을 루프 끝에서야 저장해, 박스 A를 지운 뒤 B에서 죽으면 재시작이 이미 사라진 A를
    「조회 불가」로 보고 영영 돌았다. 지운 즉시 저장한다."""
    c = make_campaign(tmp_path, monkeypatch, box_ids=(101, 102))
    second = c.fleet.box(102)
    real_ssh, crashed = second.ssh, []

    def dies_once_on_the_second_box(cmd: str):  # noqa: ANN202
        if "echo '#inbox'" in cmd and not crashed:
            crashed.append(1)
            raise Crash
        return real_ssh(cmd)

    second.ssh = dies_once_on_the_second_box
    with pytest.raises(Crash):
        drive(c)
    assert json.loads(c.coord.STATE.read_text(encoding="utf-8"))["destroyed"] == ["101"]

    c.coord.init(c.jobs, c.boxes)  # 재시작

    assert drive(c, ticks=30) is True
    assert [a[3] for a in c.fleet.vastai] == ["101", "102"]
    assert c.coord.gave_up == []


def test_a_box_whose_worker_never_started_is_given_up_like_any_other_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """작업자가 못 뜬 박스는 `inbox/` 등이 없어 `ls`가 실패한다 — 조회 불가와 같은 길이다."""
    c = make_campaign(tmp_path, monkeypatch, box_ids=(101, 102))
    for d in ("inbox", "running", "out"):
        shutil.rmtree(c.fleet.box(102).root / d)

    assert drive(c, ticks=60) is True

    assert [a[3] for a in c.fleet.vastai] == ["101"]
    assert c.coord.gave_up == ["102"]
    assert "destroy by hand: vastai destroy instance 102" in log_text(c)


def test_a_box_given_up_on_is_not_looked_at_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """포기한 상자를 뒤 바퀴에서 다시 조회·재포기하면 로그가 끝없이 불고 `gave_up`이 겹친다."""
    c = make_campaign(tmp_path, monkeypatch, box_ids=(101, 102))
    c.fleet.vastai_rc = 1
    flaky_inspection(c.fleet.box(101), c.coord.INSPECT_TRIES * 3 // 4)  # 101은 더 늦게 끝난다
    c.fleet.box(102).down = "refuse"

    assert drive(c, ticks=200) is True

    assert log_text(c).count("box 102 NOT destroyed") == c.coord.INSPECT_TRIES
    assert c.coord.gave_up.count("102") == 1


def test_a_listing_cut_off_before_every_section_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rc 0인데 출력이 잘려 `#running`·`#out`이 없으면 빈 목록으로 읽혀 지우던 길."""
    c = make_campaign(tmp_path, monkeypatch)
    box = c.fleet.box(101)
    real_ssh = box.ssh

    def cut_off(cmd: str):  # noqa: ANN202
        if "echo '#inbox'" in cmd:
            return 0, "#inbox" + chr(10)
        return real_ssh(cmd)

    box.ssh = cut_off

    assert drive(c, ticks=12) is False
    assert c.fleet.vastai == []


def test_a_box_id_that_is_not_a_number_is_refused_at_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """id는 `vastai destroy instance <id>`에 그대로 들어간다."""
    c = make_campaign(tmp_path, monkeypatch)
    bad = tmp_path / "bad_boxes.json"
    bad.write_text(
        json.dumps([{"id": "101 --all", "host": "10.0.0.1", "port": 22, "workers": 2}]),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="id"):
        c.coord.init(c.jobs, bad)


def test_a_run_that_stops_on_repeated_failures_names_the_boxes_still_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """멈춰도 박스는 그대로 과금된다 — 사람이 바로 이어받게 지울 명령을 로그에 남긴다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["a", "b"])

    def broken(box, chunk):  # noqa: ANN001, ANN202
        raise TypeError("a bug")

    c.coord.pull_chunk = broken

    with pytest.raises(TypeError):
        for _ in range(c.coord.FAIL_LIMIT + 1):
            c.fleet.box(101).work()
            c.coord.tick()

    assert "vastai destroy instance 101" in log_text(c)


def test_a_transient_failure_stays_assigned_while_its_old_result_cannot_be_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """옛 결과를 못 지웠는데 다시 밀면 상자가 새 작업을 「이미 결과가 있다」며 버린다."""
    c = make_campaign(tmp_path, monkeypatch, sids=["c"])
    box = c.fleet.box(101)
    c.coord.tick()
    box.finish("c", marker="RETRY", error="clone failed")
    box.fail_release = True

    c.coord.tick()
    c.coord.tick()

    assert "c" in c.coord.st["assigned"] and box.pushed == ["c"]
    box.fail_release = False
    c.coord.tick()
    assert box.pushed == ["c", "c"]


def test_restart_drops_half_written_and_raw_staging_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch, sids=["a"])
    stage = c.coord.STAGE
    (stage / "pulling").mkdir(parents=True)
    (stage / "x.tgz.part").write_bytes(b"half")
    (stage / "pulling" / "101_pull_1.tgz").write_bytes(b"raw")

    c.coord.init(c.jobs, c.boxes)

    assert not list(stage.glob("*.part")) and not list((stage / "pulling").glob("*"))


def test_no_box_is_destroyed_while_staged_samples_still_wait_for_a_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = make_campaign(tmp_path, monkeypatch)

    def disk_full(group):  # noqa: ANN001, ANN202
        raise OSError(28, "No space left on device")

    c.coord.write_batch = disk_full
    for _ in range(4):
        c.fleet.box(101).work()
        assert c.coord.tick() is False

    assert c.fleet.vastai == []
    assert list(c.coord.STAGE.glob("*.tgz")), "스테이징이 비어 이 테스트가 아무것도 안 잰다"
