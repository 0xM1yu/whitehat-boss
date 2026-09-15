"""`tools/monitor.py` — run 밖에서 상태·플래그를 읽는 폴러의 도출 규칙을 고정한다 (이슈 #208).

순수 함수만 본다(파일·시계·상수는 인자로 받는다). 핵심은 임계값이 추측이 아니라 코드
상수에서 오고, "죽음↔느림"과 "에러 반복"이 파일 스냅샷만으로 갈린다는 것이다.
"""

from datetime import UTC, datetime, timedelta

from tools import monitor

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
T = 600.0  # 테스트용 stuck 상한
K = 3


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _hb(age_s: float, *, seq: int = 5, stage: int = 4, interval: float = 5.0) -> dict:
    return {
        "ts": _iso(NOW - timedelta(seconds=age_s)),
        "pid": 1,
        "last_event_seq": seq,
        "current_stage": stage,
        "interval_s": interval,
    }


def _ev(
    kind: str, *, age_s: float, seq: int, reason: str | None = None, subject: str | None = None
) -> dict:
    e = {"seq": seq, "ts": _iso(NOW - timedelta(seconds=age_s)), "kind": kind, "data": {}}
    if reason is not None:
        e["data"]["reason"] = reason
    if subject is not None:
        e["subject_kind"] = subject
    return e


def _derive(**kw):  # noqa: ANN003
    base = {"heartbeat": None, "events": [], "manifest": None, "now": NOW,
            "stuck_after_s": T, "error_storm_k": K}
    base.update(kw)
    return monitor.derive_status(**base)


# --- alive / stuck ----------------------------------------------------------


def test_alive_when_heartbeat_is_fresh() -> None:
    assert _derive(heartbeat=_hb(2))["alive"] is True


def test_dead_when_heartbeat_is_stale_beyond_grace() -> None:
    # interval 5 · grace 3 → 15초까지 살아있다. 40초면 죽음.
    assert _derive(heartbeat=_hb(40))["alive"] is False


def test_stuck_when_alive_but_trace_silent_past_the_ceiling() -> None:
    """heartbeat는 신선(살아있음)한데 마지막 이벤트가 T를 넘겨 침묵 = wedge."""
    status = _derive(heartbeat=_hb(2), events=[_ev("model_call", age_s=T + 1, seq=5)])
    assert status["alive"] is True
    assert status["stuck"] is True


def test_not_stuck_just_below_the_ceiling() -> None:
    status = _derive(heartbeat=_hb(2), events=[_ev("model_call", age_s=T - 1, seq=5)])
    assert status["stuck"] is False


def test_not_stuck_when_dead() -> None:
    """죽었으면(heartbeat stale) stuck이 아니다 — 그건 다른 신호(alive=False)다."""
    status = _derive(heartbeat=_hb(40), events=[_ev("model_call", age_s=T + 1, seq=5)])
    assert status["alive"] is False
    assert status["stuck"] is False


# --- error_storm ------------------------------------------------------------


def test_error_storm_when_last_k_share_a_reason() -> None:
    events = [_ev("critic_failed", age_s=10, seq=i, reason="schema_error") for i in range(K)]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is True


def test_no_storm_below_k() -> None:
    events = [_ev("critic_failed", age_s=10, seq=i, reason="schema_error") for i in range(K - 1)]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is False


def test_no_storm_when_reasons_differ() -> None:
    events = [
        _ev("target_skipped", age_s=10, seq=1, reason="gateway_error"),
        _ev("target_skipped", age_s=9, seq=2, reason="schema_error"),
        _ev("target_skipped", age_s=8, seq=3, reason="schema_error"),
    ]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is False


# --- completion / budget ----------------------------------------------------


def test_incomplete_when_manifest_absent() -> None:
    assert _derive(heartbeat=_hb(2))["incomplete"] is True


def test_complete_when_manifest_finished_and_not_stopped() -> None:
    manifest = {"finished_at": _iso(NOW), "stopped_after": None, "thresholds": {}}
    assert _derive(manifest=manifest)["incomplete"] is False


def test_incomplete_when_stopped_after_is_set() -> None:
    manifest = {"finished_at": _iso(NOW), "stopped_after": 6, "thresholds": {}}
    assert _derive(manifest=manifest)["incomplete"] is True


def test_budget_exhausted_from_run_transition_reason() -> None:
    events = [_ev("transition", age_s=1, seq=9, reason="budget_exhausted", subject="run")]
    assert _derive(heartbeat=_hb(2), events=events)["budget_exhausted"] is True


# --- stuck ceiling from constants ------------------------------------------


def test_ceiling_uses_manifest_retries_when_present() -> None:
    manifest = {"thresholds": {"schema_retries": 2}}
    seconds, basis = monitor.stuck_ceiling_s(manifest, 300.0, default_retries=1)
    assert seconds == 900.0  # (2+1)×300
    assert "manifest" in basis


def test_ceiling_falls_back_to_default_retries() -> None:
    seconds, basis = monitor.stuck_ceiling_s(None, 300.0, default_retries=1)
    assert seconds == 600.0  # (1+1)×300
    assert "기본" in basis


# --- trace reader -----------------------------------------------------------


def test_read_trace_skips_a_half_written_last_line(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text('{"seq":1,"ts":"x","kind":"stage_start"}\n{"seq":2,"ts":"y"', encoding="utf-8")
    events = monitor.read_trace(path)
    assert [e["seq"] for e in events] == [1]  # 잘린 둘째 줄은 버린다
