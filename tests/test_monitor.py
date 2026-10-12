"""`tools/monitor.py` — run 밖에서 상태·플래그를 읽는 폴러의 도출 규칙을 고정한다 (이슈 #208).

대부분 순수 함수를 본다(파일·시계·상수는 인자로 받는다). 핵심은 임계값이 추측이 아니라
코드 상수에서 오고, "죽음↔느림"과 "에러 반복"이 파일 스냅샷만으로 갈린다는 것이다.
맨 끝 두 건은 `main()`의 종료 갈래(N1·N6)를 `--once` 없이 실경로로 고정한다.
"""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.orchestrator.runner import _REASON_GATEWAY, _REASON_OVER_MAX, _REASON_SCHEMA
from src.schemas.enums import ReasonCode
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
    kind: str,
    *,
    age_s: float,
    seq: int,
    reason: str | None = None,
    subject: str | None = None,
    member: str | None = None,
) -> dict:
    e = {"seq": seq, "ts": _iso(NOW - timedelta(seconds=age_s)), "kind": kind, "data": {}}
    if reason is not None:
        e["data"]["reason"] = reason
    if member is not None:
        e["data"]["scout_member"] = member
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


def test_failure_reasons_match_runner_constants() -> None:
    """폴러가 쓰는 실패 사유가 runner의 상수와 일치하고, over_candidate_max는 실패가 아니다(L5)."""
    assert monitor._FAILURE_REASONS == frozenset({_REASON_GATEWAY, _REASON_SCHEMA})
    assert _REASON_OVER_MAX not in monitor._FAILURE_REASONS


def test_storm_when_k_failures_pile_up() -> None:
    events = [_ev("critic_failed", age_s=10, seq=i, reason=_REASON_SCHEMA) for i in range(K)]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is True


def test_no_storm_below_k() -> None:
    events = [_ev("critic_failed", age_s=10, seq=i, reason=_REASON_SCHEMA) for i in range(K - 1)]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is False


def test_storm_when_reasons_alternate_but_failures_are_consecutive() -> None:
    """H2 회귀: 차단기는 schema↔400 번갈아도 터진다. 폴러도 서야 한다(예전엔 사유 상이로 놓쳤다)."""
    events = [
        _ev("target_skipped", age_s=12, seq=1, reason=_REASON_SCHEMA),
        _ev("target_skipped", age_s=11, seq=2, reason=_REASON_GATEWAY),
        _ev("target_skipped", age_s=10, seq=3, reason=_REASON_SCHEMA),
    ]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is True


def test_progress_event_resets_the_streak() -> None:
    """진행 이벤트(target_planned)가 실패 사이에 끼면 그 앞은 다른 국면 — storm 아니다."""
    events = [
        _ev("critic_failed", age_s=30, seq=1, reason=_REASON_SCHEMA),
        _ev("target_planned", age_s=20, seq=2),  # 리셋
        _ev("critic_failed", age_s=12, seq=3, reason=_REASON_SCHEMA),
        _ev("critic_failed", age_s=10, seq=4, reason=_REASON_SCHEMA),
    ]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is False


def test_over_candidate_max_is_not_a_failure() -> None:
    events = [_ev("target_skipped", age_s=10, seq=i, reason=_REASON_OVER_MAX) for i in range(K + 2)]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is False


def test_breaker_trip_transition_is_a_storm_even_below_k() -> None:
    """차단기가 실제로 터진 사후 신호는 K 미만이어도 확정 storm이다."""
    events = [
        _ev("transition", age_s=1, seq=9, reason="consecutive_target_failures", subject="run")
    ]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is True


def test_the_live_flag_counts_per_member_so_one_target_across_members_is_not_a_storm() -> None:
    """N2: 대상 하나가 풀 멤버 셋에서 각 1회씩 죽는 것은 **라이브 판정이** storm으로 안 본다 —
    자리별로 세면 각 카운터가 1이다(K=3=풀크기라 예전엔 전체 3으로 오탐).

    ⚠ 차단기도 안 터진다는 뜻이 아니다 — 차단기의 run 카운터는 자리를 안 가르고 센다(#197). 그
    미탐은 아래 검사가 박제한다.
    """
    events = [
        _ev("target_skipped", age_s=12, seq=1, reason=_REASON_SCHEMA, member="scout_memory"),
        _ev("target_skipped", age_s=11, seq=2, reason=_REASON_SCHEMA, member="scout_boundary"),
        _ev("target_skipped", age_s=10, seq=3, reason=_REASON_SCHEMA, member="scout_injection"),
    ]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is False


def test_a_run_counter_stop_is_invisible_to_the_live_flag_until_the_transition_lands() -> None:
    """**한계 박제 (PR #584 리뷰 §2).** 풀 세 자리가 전부 죽는 장애를 러너는 첫 대상의 세 자리 뒤
    둘째 대상의 첫 실패(자리별 2·1·1)에서 세운다. 라이브 판정은 자리별 K 미만이라 침묵하고,
    종료 전이 사유가 trace에 뜬 뒤에야 참이다.

    이 검사는 **미탐이 있다는 사실**을 고정한다 — 라이브에서 자리를 안 가르는 둘째 카운트를 넣어
    고치면 첫 단언을 뒤집는다(그때 임계값 실측이 선행이다, `_error_storm` docstring).
    """
    failures = [
        _ev("target_skipped", age_s=20 - i, seq=i, reason=_REASON_SCHEMA, member=member)
        for i, member in enumerate(
            ("scout_memory", "scout_boundary", "scout_injection", "scout_memory"), start=1
        )
    ]
    assert _derive(heartbeat=_hb(2), events=failures)["error_storm"] is False

    stopped = [
        *failures,
        _ev("transition", age_s=1, seq=9, reason="consecutive_target_failures", subject="run"),
    ]
    assert _derive(heartbeat=_hb(2), events=stopped)["error_storm"] is True


def test_storm_when_one_member_fails_k_times() -> None:
    """한 자리가 K회 연속 죽으면 storm — 자리별 카운터가 K에 닿는다."""
    events = [
        _ev("target_skipped", age_s=15 - i, seq=i, reason=_REASON_SCHEMA, member="scout_memory")
        for i in range(K)
    ]
    assert _derive(heartbeat=_hb(2), events=events)["error_storm"] is True


# --- completion / budget ----------------------------------------------------


def test_incomplete_when_manifest_absent() -> None:
    assert _derive(heartbeat=_hb(2))["incomplete"] is True


def test_complete_when_manifest_finished_and_not_stopped() -> None:
    manifest = {"finished_at": _iso(NOW), "stopped_after": None, "thresholds": {}}
    assert _derive(manifest=manifest)["incomplete"] is False


def test_incomplete_when_stopped_after_is_set() -> None:
    manifest = {"finished_at": _iso(NOW), "stopped_after": 6, "thresholds": {}}
    assert _derive(manifest=manifest)["incomplete"] is True


def test_stopped_run_is_incomplete_but_has_manifest() -> None:
    """N1: 중단된 run은 incomplete=True지만 매니페스트는 남는다 — 폴러는 has_manifest로
    끝나야 정상 종료한 중단 run을 DEAD로 오판하지 않는다."""
    manifest = {"finished_at": _iso(NOW), "stopped_after": 5, "thresholds": {}}
    status = _derive(manifest=manifest)
    assert status["incomplete"] is True
    assert status["has_manifest"] is True


def test_no_manifest_means_not_finished() -> None:
    assert _derive(heartbeat=_hb(2))["has_manifest"] is False


def test_budget_exhausted_from_run_transition_reason() -> None:
    events = [_ev("transition", age_s=1, seq=9, reason="budget_exhausted", subject="run")]
    assert _derive(heartbeat=_hb(2), events=events)["budget_exhausted"] is True


def test_crashed_from_the_run_transition_reason() -> None:
    """🔴 최후 방어선(S0)이 남긴 run 종료 사유 `unforeseen_failure`가 `crashed`다.

    그 run은 **매니페스트를 남긴다** — `has_manifest`만 보면 정상 종료와 같아, 예전에는 매니페스트가
    없어 DEAD(exit 3)로 잡히던 크래시가 조용히 완료로 읽힌다(적대적 리뷰 2026-10-02 MEDIUM 3)."""
    events = [_ev("transition", age_s=1, seq=9, reason="unforeseen_failure", subject="run")]
    assert _derive(heartbeat=_hb(2), events=events)["crashed"] is True


def test_a_policy_stop_is_not_a_crash() -> None:
    """예산·차단기·게이트웨이·사용자 정지는 **예상한** 중단이다 — `crashed`가 아니다."""
    for reason in (
        "budget_exhausted",
        "consecutive_target_failures",
        "model_gateway_unavailable",
        "model_swap_failed",
        "schema_parse_failed",
        "user_stop_requested",
    ):
        events = [_ev("transition", age_s=1, seq=9, reason=reason, subject="run")]
        assert _derive(heartbeat=_hb(2), events=events)["crashed"] is False, reason


def test_the_crash_reason_is_the_one_the_runner_writes() -> None:
    """폴러의 사유 상수가 러너의 `ReasonCode`와 갈라지면 크래시가 조용히 안 잡힌다."""
    assert monitor._CRASH_REASON == ReasonCode.UNFORESEEN_FAILURE.value


def test_budget_exhausted_from_live_budget_event() -> None:
    """run 도중 budget.py가 내는 BUDGET(event=exhausted)도 잡는다 — 사후 전이만이 아니다(L7)."""
    e = {
        "seq": 5,
        "ts": _iso(NOW - timedelta(seconds=2)),
        "kind": "budget",
        "data": {"event": "exhausted"},
    }
    assert _derive(heartbeat=_hb(2), events=[e])["budget_exhausted"] is True


def test_stuck_is_unknown_before_the_first_trace_event() -> None:
    """이벤트 0건이면 event_age는 heartbeat로 폴백하지 않고 None이라 stuck이 안 선다(L3)."""
    status = _derive(heartbeat=_hb(2), events=[])
    assert status["event_age_s"] is None
    assert status["stuck"] is False


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


# --- main() 종료 갈래 (--once 없이 실경로로) --------------------------------


def test_main_returns_0_on_a_stopped_run_even_with_stale_heartbeat(tmp_path: Path) -> None:
    """N1 회귀: 중단된 run(매니페스트 있음·stopped_after)에 heartbeat가 stale해도 폴러는
    매니페스트가 났으니 0으로 끝난다 — 종료 조건이 `not incomplete`면 이 run은 영영 안
    끝나고 DEAD(exit 3)로 오판됐다(그 조건으로 되돌리면 이 테스트가 깨진다)."""
    rd = tmp_path / "run_stopped"
    rd.mkdir()
    now = datetime.now(UTC)
    stale = (now - timedelta(seconds=999)).isoformat()  # heartbeat 확실히 stale
    (rd / "heartbeat.json").write_text(
        json.dumps({"ts": stale, "pid": 1, "last_event_seq": 5, "current_stage": 5,
                    "interval_s": 5.0}),
        encoding="utf-8",
    )
    (rd / "run_manifest.json").write_text(
        json.dumps({"finished_at": now.isoformat(), "stopped_after": 5, "thresholds": {}}),
        encoding="utf-8",
    )
    (rd / "trace.jsonl").write_text("", encoding="utf-8")

    assert monitor.main([str(rd)]) == 0  # DEAD(3)가 아니다


def test_main_returns_3_on_a_crashed_run_even_though_it_left_a_manifest(tmp_path: Path) -> None:
    """S0 이후 터진 run도 매니페스트를 남긴다 — 그래도 **정상 종료(0)가 아니다**. 예전에는
    매니페스트가 없어 heartbeat stale → DEAD(3)였고, 지금은 사유 `unforeseen_failure`가 그 자리를
    잇는다."""
    rd = tmp_path / "run_crashed"
    rd.mkdir()
    now = datetime.now(UTC)
    (rd / "run_manifest.json").write_text(
        json.dumps({"finished_at": now.isoformat(), "stopped_after": 6, "thresholds": {}}),
        encoding="utf-8",
    )
    event = {"seq": 1, "ts": now.isoformat(), "kind": "transition", "subject_kind": "run",
             "data": {"reason": "unforeseen_failure"}}
    (rd / "trace.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    assert monitor.main([str(rd)]) == 3


def test_main_exits_2_when_run_dir_is_missing(tmp_path: Path) -> None:
    """N6: 못 읽는 run_dir은 시작 시 보고하고 끝낸다 — 조용히 영원히 돌지 않는다."""
    assert monitor.main([str(tmp_path / "does_not_exist")]) == 2


def test_the_stuck_ceiling_follows_the_longest_declared_request_timeout() -> None:
    """모델이 `request_timeout_s`를 선언하면 T의 한 호출 몫이 그 값이다(ADR 0012 Phase 6.5 · #261).

    전역 `_TIMEOUT_S`만 쓰면 1,800 s를 선언한 좌석이 300 s 넘게 생성하는 정상 호출을 stuck으로
    경보한다. 프로필이 **부르는** 모델만 본다 — registry의 딴 키가 T를 늘리면 경보가 무뎌진다.
    """
    from src.config import load_config

    loaded = load_config("harness_pool")
    # 대조군은 선언을 걷어 합성한다 — 2026-09-26부터 저장소의 Auditor(`qwen3.8-27b`)가 7,500 s를
    # 선언해, 저장소 값을 대조군으로 쓰면 그 값에 이 검사가 묶인다.
    config = loaded.transition(
        models={
            key: entry.transition(request_timeout_s=None) for key, entry in loaded.models.items()
        }
    )
    auditor_key = config.model_key_for("auditor")
    declared = config.transition(
        models={
            **config.models,
            auditor_key: config.models[auditor_key].transition(request_timeout_s=1800),
            "fake": config.models["fake"].transition(request_timeout_s=99999),
        }
    )

    assert monitor.call_timeout_s(config, default=300) == 300
    assert monitor.call_timeout_s(declared, default=300) == 1800
    real = loaded.model_for("auditor").request_timeout_s
    assert monitor.call_timeout_s(loaded, default=300) == real


def test_monitor_runs_by_path_and_as_module(tmp_path: Path) -> None:
    # cwd=저장소 루트: `python tools/monitor.py`는 sys.path[0]이 tools/라 루트가 없다(#616).
    # `--help`는 늦은 import 앞에서 끝나 못 쓴다 — 빈 run_dir + --once가 가장 싼 경로다.
    root = Path(__file__).resolve().parent.parent
    for args in (["tools/monitor.py"], ["-m", "tools.monitor"]):
        proc = subprocess.run(
            [sys.executable, *args, str(tmp_path), "--once"],
            cwd=root, capture_output=True, text=True, timeout=60, check=False,
        )
        assert proc.returncode == 0, (args, proc.stderr)
        assert "ModuleNotFoundError" not in proc.stderr
