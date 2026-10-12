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


def test_a_gate_that_ran_but_only_shortcut_is_a_measured_zero() -> None:
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


def test_the_metric_is_defined_where_the_next_reader_looks() -> None:
    root = Path(__file__).resolve().parent.parent
    evaluation = (root / "docs" / "evaluation.md").read_text(encoding="utf-8")
    assert "location_overlap" in evaluation and "gate_eval" in evaluation
    table = (root / "src" / "orchestrator" / "trace.py").read_text(encoding="utf-8")
    assert "RunRecord.location_overlap" in table
