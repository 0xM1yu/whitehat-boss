"""report.md의 최종 PoC 줄 - `oracle_final` 이벤트에서 뽑아 리포트에 싣는다 (모델 호출 없음)."""

from src.orchestrator.nodes.report import Report
from src.orchestrator.runner import _oracle_final_summary
from src.orchestrator.trace import EventKind, TraceWriter

_SHA = "ab" * 32


def _final_trace(**data: object) -> TraceWriter:
    trace = TraceWriter("run_20261015_0001")
    # 키 집합은 `_finalize_oracle`이 내는 것과 같다(`status`와 무관하게 같은 키).
    row = {
        "status": "scored", "designation": "smallest_poc", "reason": None, "poc_sha256": _SHA,
        "record_ref": "oracle/verdict.json",
    }
    trace.emit(
        EventKind.ORACLE_FINAL, stage=8, subject_kind="finding", subject_id="find_0002",
        **{**row, **data},
    )
    return trace


def test_summary_reads_the_single_oracle_final_event() -> None:
    assert _oracle_final_summary(_final_trace(), wired=True) == {
        "status": "scored",
        "designation": "smallest_poc",
        "reason": None,
        "error": None,
        "subject": "find_0002",
        "poc_sha256": _SHA,
        "record_ref": "oracle/verdict.json",
    }


def test_no_oracle_final_event_means_no_summary() -> None:
    assert _oracle_final_summary(TraceWriter("run_20261015_0001"), wired=False) is None


def test_a_wired_oracle_without_the_final_event_reads_as_not_reached() -> None:
    """채점기를 붙인 run이 최종 단계 전에 끊기면 이벤트가 없다 - 절을 비우지 않고 미도달을 적는다"""
    empty = TraceWriter("run_20261015_0001")
    summary = _oracle_final_summary(empty, wired=True)
    assert summary == {"status": "not_reached"}
    text = Report(run_id="r", findings=(), duplicates=0, oracle_final=summary).render()
    assert "### 최종 PoC (채점기)" in text and "채점기: 최종 단계 미도달" in text
    assert "상태" not in text and "verdict.json" not in text


def test_report_renders_the_final_poc_and_names_the_dedupe_representative() -> None:
    summary = _oracle_final_summary(_final_trace(), wired=True)
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
        _final_trace(status="none", designation=None, reason="no_poc", poc_sha256=None),
        wired=True,
    )
    text = Report(run_id="r", findings=(), duplicates=0, oracle_final=summary).render()
    assert "상태 none" in text and "사유 no_poc" in text and "sha256" not in text
    assert "verdict.json" not in text  # 판정 파일은 scored일 때만 가리킨다
    assert "최종 PoC" not in Report(run_id="r", findings=(), duplicates=0).render()


def test_failed_status_shows_the_error_class_as_the_reason() -> None:
    # 실형태: `failed`는 reason=None이고 예외 클래스 이름이 `error`에 든다.
    summary = _oracle_final_summary(
        _final_trace(status="failed", designation=None, reason=None, error="ConnectError"),
        wired=True,
    )
    text = Report(run_id="r", findings=(), duplicates=0, oracle_final=summary).render()
    assert "상태 failed" in text and "사유 ConnectError" in text and "verdict.json" not in text
    scored = Report(
        run_id="r",
        findings=(),
        duplicates=0,
        oracle_final=_oracle_final_summary(_final_trace(), wired=True),
    ).render()
    assert "판정 값은 oracle/verdict.json" in scored


def test_scored_without_a_sidecar_says_so_instead_of_pointing_at_one() -> None:
    """사이드카를 못 썼으면(쓰기 실패·run_dir 없음) `record_ref`가 None이다."""
    summary = _oracle_final_summary(_final_trace(record_ref=None), wired=True)
    assert summary is not None and summary["record_ref"] is None
    text = Report(run_id="r", findings=(), duplicates=0, oracle_final=summary).render()
    assert "상태 scored" in text and "(판정 파일 없음)" in text and "verdict.json" not in text
