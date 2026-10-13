"""6단계 위치 겹침 횟수 (이슈 #563 선택지 4) - `RunRecord.location_overlap`과 `report.md` 한 줄.

센 값은 `gate_eval`(stage=6) 행이 원천이다. 7·8단계 행은 후보 위치를 안 넘겨 `location_ok`가 구조상
거짓이라 섞으면 겹침률이 깎인다 - 그 단계 구분이 이 파일의 핵심이다.
"""

from pathlib import Path

from src.orchestrator.nodes.report import Report
from src.orchestrator.runner import _location_overlap
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas.run import RunRecord
from tests.test_pipeline_e2e import _events, _of, _run

_GATED = {"p0": False, "p1": True, "static_ok": True, "location_ok": False}


def _writer(*rows: tuple[int, dict[str, bool]]) -> TraceWriter:
    trace = TraceWriter("run_20261013_0001")
    for stage, predicates in rows:
        trace.emit(EventKind.GATE_EVAL, stage=stage, subject_id="find_0001", **predicates)
    return trace


def test_counts_come_from_stage_6_rows_that_reached_the_predicates() -> None:
    trace = _writer(
        (6, {"p0": True}),  # 관측이 비어 단락 - location_ok 키가 없어 gated에서 빠진다
        (6, dict(_GATED)),  # 신호는 있었는데 안 겹쳤다 - #563의 모양
        (6, {**_GATED, "location_ok": True}),
        (6, {**_GATED, "static_ok": False}),
        (7, {**_GATED, "location_ok": True}),  # 다른 단계 - 세면 안 된다
        (8, dict(_GATED)),
    )

    assert _location_overlap(trace.events) == {"gated": 3, "static_ok": 2, "location_ok": 1}


def test_a_run_whose_gate_never_ran_has_no_block_not_zeros() -> None:
    assert _location_overlap(_writer().events) is None
    # 7·8단계 행만 있어도 6단계가 안 돈 것이다 - 0이 아니라 「안 쟀다」
    assert _location_overlap(_writer((7, dict(_GATED)), (8, dict(_GATED))).events) is None


def test_a_gate_that_ran_but_only_shortcut_has_gated_zero_which_is_unmeasured() -> None:
    """6단계 행은 있으나 전부 `p0` 지름길 - 블록은 있고 `gated`는 0이다.

    그 0은 **잰 0이 아니다**(`docs/evaluation.md` §5.1 - *"`gated`가 0인데 키가 있으면 … 겹침은
    미측정이다 - 잰 0이 아니다"*). 블록 자체가 `None`이 아닌 것은 「6단계가 돌았다」는 사실이
    `None`(안 돌았다)과 다르기 때문이고, 그 구분을 리포트가 아래 핀처럼 문구로 옮긴다.
    """
    assert _location_overlap(_writer((6, {"p0": True})).events) == {
        "gated": 0,
        "static_ok": 0,
        "location_ok": 0,
    }


def test_manifest_and_report_carry_the_counts_the_trace_rows_give(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path)

    rows = [e["data"] for e in _of(_events(trace_path), "gate_eval", stage=6)]
    judged = [r for r in rows if "location_ok" in r]
    assert judged, "전제 - dev_fake가 6단계 술어까지 간 행을 낸다"
    expected = {
        "gated": len(judged),
        "static_ok": sum(1 for r in judged if r["static_ok"]),
        "location_ok": sum(1 for r in judged if r["location_ok"]),
    }
    assert result.record.location_overlap == expected
    # 디스크의 manifest가 같은 값을 든다(직렬화에서 빠지지 않는다)
    manifest = RunRecord.model_validate_json(
        (trace_path.parent / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.location_overlap == expected
    line = next(
        row for row in result.report.render().splitlines() if row.startswith("정적 신호 위치 겹침")
    )
    assert f"{expected['gated']}건 중 후보 줄과 겹침 {expected['location_ok']}건" in line


def test_report_prints_no_overlap_line_when_nothing_was_counted() -> None:
    bare = Report(run_id="run_20261013_0001", findings=(), duplicates=0)
    assert "위치 겹침" not in bare.render()
    shown = Report(
        run_id="run_20261013_0001",
        findings=(),
        duplicates=0,
        location_overlap={"gated": 5, "static_ok": 5, "location_ok": 0},
    )
    assert "5건 중 후보 줄과 겹침 0건 · 정적 신호 있음 5건" in shown.render()


def test_report_calls_a_zero_denominator_unmeasured_not_a_counted_zero() -> None:
    """막는 명제 - **리포트가 미측정을 잰 수로 찍지 않는다**(CLAUDE.md §0).

    양성 핀: `gated` 0인 블록(실형태는 `_location_overlap`이 `p0`만 있는 run에 내는 것 -
    `tests/test_location_overlap.py:52`의 `{"gated": 0, …}`)이 **「0건 중」을 안 찍고** 「미측정」을
    찍는다. `render()`가 `n['gated']`를 안 보고 종전처럼 횟수를 찍으면 죽는다.

    음성 핀: `gated`가 0이 아니고 `location_ok`만 0인 run은 **잰 0**이라 횟수로 찍힌다
    (바로 위 테스트의 5/5/0). 둘을 한 테스트에 두는 것은 「0이면 미측정」을 `location_ok`
    쪽으로 잘못 넓히는 수정이 여기서 죽게 하기 위해서다.

    그리고 `if self.location_overlap and self.location_overlap["gated"]:`로 **줄을 통째로
    빼는** 변이가 3라운드 리뷰에서 생존했다 - 미측정 문구를 단언하므로 그 변이는 여기서 죽는다.
    """
    unmeasured = Report(
        run_id="run_20261013_0001",
        findings=(),
        duplicates=0,
        location_overlap={"gated": 0, "static_ok": 0, "location_ok": 0},
    )
    rendered = unmeasured.render()
    line = next(row for row in rendered.splitlines() if row.startswith("정적 신호 위치 겹침"))
    assert "미측정" in line
    assert "0건 중" not in rendered

    measured = Report(
        run_id="run_20261013_0001",
        findings=(),
        duplicates=0,
        location_overlap={"gated": 3, "static_ok": 3, "location_ok": 0},
    )
    # 줄 단위로 본다 - 분모 전용 집계 절(#613)이 자기 「미측정」 줄을 따로 찍는다(다른 명제).
    measured_line = next(
        row for row in measured.render().splitlines() if row.startswith("정적 신호 위치 겹침")
    )
    assert "3건 중 후보 줄과 겹침 0건" in measured_line
    assert "미측정" not in measured_line


def test_a_manifest_written_before_the_field_loads_with_no_block() -> None:
    """옛 manifest는 그대로 로드되고 `None`(안 쟀다)이다 - 0이 아니다."""
    fixture = Path(__file__).resolve().parent / "fixtures" / "manifests" / "run_20260817_0004.json"
    record = RunRecord.model_validate_json(fixture.read_text(encoding="utf-8"))
    assert record.location_overlap is None
