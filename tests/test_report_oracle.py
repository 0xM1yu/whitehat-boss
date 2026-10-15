"""report.md의 최종 PoC 줄 - `oracle_final` 이벤트에서 뽑아 리포트에 싣는다 (모델 호출 없음)."""

from src.orchestrator.nodes.report import Report
from src.orchestrator.runner import _oracle_final_summary
from src.orchestrator.trace import EventKind, TraceWriter

_SHA = "ab" * 32


def _final_trace(**data: object) -> TraceWriter:
    trace = TraceWriter("run_20261015_0001")
    # 키 집합은 `_finalize_oracle`이 내는 것과 같다(`status`와 무관하게 같은 키).
    row = {"status": "scored", "designation": "smallest_poc", "reason": None, "poc_sha256": _SHA}
    trace.emit(
        EventKind.ORACLE_FINAL, stage=8, subject_kind="finding", subject_id="find_0002",
        **{**row, **data},
    )
    return trace


def test_summary_reads_the_single_oracle_final_event() -> None:
    assert _oracle_final_summary(_final_trace()) == {
        "status": "scored",
        "designation": "smallest_poc",
        "reason": None,
        "subject": "find_0002",
        "poc_sha256": _SHA,
    }


def test_no_oracle_final_event_means_no_summary() -> None:
    assert _oracle_final_summary(TraceWriter("run_20261015_0001")) is None


def test_report_renders_the_final_poc_and_names_the_dedupe_representative() -> None:
    summary = _oracle_final_summary(_final_trace())
    plain = Report(run_id="r", findings=(), duplicates=0, oracle_final=summary).render()
    assert "### 최종 PoC (채점기)" in plain
    assert "상태 scored" in plain and "지정 근거 smallest_poc" in plain
    assert f"PoC sha256 {_SHA}" in plain and "find_0002" in plain and "합쳐짐" not in plain

    folded = Report(
        run_id="r",
        findings=(),
        duplicates=1,
        merged_into={"find_0002": "find_0001"},
        oracle_final=summary,
    ).render()
    assert "find_0002 (중복 제거로 대표 find_0001에 합쳐짐)" in folded


def test_none_status_shows_the_reason_and_no_oracle_means_no_section() -> None:
    summary = _oracle_final_summary(
        _final_trace(status="none", designation=None, reason="no_poc", poc_sha256=None)
    )
    text = Report(run_id="r", findings=(), duplicates=0, oracle_final=summary).render()
    assert "상태 none" in text and "사유 no_poc" in text and "sha256" not in text
    assert "최종 PoC" not in Report(run_id="r", findings=(), duplicates=0).render()
