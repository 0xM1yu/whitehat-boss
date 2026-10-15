"""채점기 제출·최종 단계의 일시 실패 복구 - 모델이 낸 PoC를 전송 실패로 잃을 가능성을 줄인다.

배경(감사 프로브 P1): 중간 gen A가 `vul` 139를 받아 서버에 행이 있는데, 모델이 A를 `submit`한 뒤
지정 가능 재제출이 429·5xx·타임아웃으로 실패하면 후보가 0이 되어 `oracle_final`이
`none`/`all_vul_submissions_failed`였다. 고친 것은 둘 - 재제출을 한 번 다시 하고, 그래도 안 되면
**같은 finding이 앞서 성공시킨 같은 sha256의 중간 제출**을 그 PoC의 후보로 쓴다. 모델이
`submit`하지 않은 PoC를 올리는 것이 아니다(ADR 0013 결정 1 불변). 지정은 여전히 `vul` 응답 코드만
읽는다(결정 2) - 이 파일은 그 둘을 안 건드리고 **전송 실패의 복구**만 문다.
"""

import hashlib

import pytest

from src.adapters.cybergym_oracle import CyberGymOracleError
from src.interfaces.oracle import OracleOutcome
from src.orchestrator import oracle_log, runner
from src.orchestrator.trace import EventKind
from src.schemas.pov import PovActionNamed
from tests.oracle_fakes import FakeOracle
from tests.test_nodes import _pov_state, ctx  # noqa: F401
from tests.test_pov_loop import _gen, _scripted, _with_pov_version
from tests.test_pov_oracle_feedback import ReadingOracle

SHA_A = hashlib.sha256(b"A").hexdigest()
# 재시도한 기록만 싣는 첫 실패 키 셋 - 한 번에 된 run과 키 집합이 같아야 해서다.
FIRST_KEYS = {"first_error", "first_error_status", "retry_wait_s"}


def _http(status: int) -> CyberGymOracleError:
    return CyberGymOracleError(f"HTTP {status}", status=status)


def _conn() -> CyberGymOracleError:
    """연결·타임아웃 계열 - 어댑터가 `transient=True`로 올린다(`status`는 `None`)."""
    return CyberGymOracleError("connection reset", transient=True)


def _shape() -> CyberGymOracleError:
    """응답 형 어긋남·`poc_id` 불일치 - 어댑터가 `status=None`이되 `transient`는 안 단다."""
    return CyberGymOracleError("poc_id mismatch", detail="poc_id mismatch")


def _run(ctx, oracle, sleeps=None):  # noqa: ANN001, ANN202, F811
    """gen A → gen B → A를 이름으로 `submit`. 호출 0·1은 중간 gen, 호출 2가 지정 가능 재제출이다."""
    state, finding = _pov_state(ctx)
    state.oracle_ledger = oracle_log.OracleLedger(wired=True)
    submit = PovActionNamed(action="submit", rationale="named", poc_sha256=SHA_A)
    scripted = _scripted(_with_pov_version(ctx, "v7"), [_gen(count=1), _gen(count=9), submit])
    scripted.oracle = oracle
    if sleeps is not None:
        scripted.sleep = sleeps.append
    runner._run_pov(scripted, state, finding)
    runner._finalize_oracle(scripted, state)
    return scripted, state


def _final_data(scripted):  # noqa: ANN001, ANN202
    return next(e.data for e in scripted.trace.events if e.kind is EventKind.ORACLE_FINAL)


def test_p1_resubmit_fails_twice_and_the_earlier_ok_submission_of_the_same_bytes_is_the_candidate(
    ctx,  # noqa: F811
) -> None:
    oracle = ReadingOracle(
        codes=(139, 0), vul_exit_code=139, vul_errors={2: _http(500), 3: _http(500)}
    )

    scripted, state = _run(ctx, oracle)

    ledger = state.oracle_ledger
    assert [(a.designatable, a.ok) for a in ledger.attempts] == [
        (False, True), (False, True), (True, False), (True, True),
    ]
    reused = ledger.candidates[0]
    assert reused.reused_intermediate and reused.submit_calls == 2
    # 이 PoC의 앞선 성공 제출 그 자체다 - 새로 낸 것이 아니다.
    assert reused.submission is ledger.attempts[0].submission
    assert reused.poc_sha256 == SHA_A
    assert len(oracle.vul_calls) == 4  # 중간 둘 + 재제출 한 번 + 그 재시도 한 번
    assert [poc for poc, _ in oracle.final_calls] == [b"A"]
    assert ledger.final.status == oracle_log.STATUS_SCORED
    assert ledger.final.designation == oracle_log.DESIGNATION_SOLE_CANDIDATE
    row = ledger.record()["candidates"][3]
    assert row["reused_intermediate"] is True and row["submit_calls"] == 2
    # 실패한 재제출 행은 마지막 실패(`error_status`)와 첫 실패를 따로 든다.
    assert row["first_error"] == "CyberGymOracleError" and row["first_error_status"] == 500
    failed_row = ledger.record()["candidates"][2]
    assert failed_row["first_error_status"] == 500 and failed_row["error_status"] == 500
    submitted = [e for e in scripted.trace.events if e.kind is EventKind.ORACLE_SUBMITTED]
    assert [e.data.get("reused_intermediate") for e in submitted] == [None, None, True]
    failed = [e for e in scripted.trace.events if e.kind is EventKind.ORACLE_FAILED]
    assert [e.data["submit_calls"] for e in failed] == [2]
    assert failed[0].data["first_error_status"] == 500 and failed[0].data["retry_wait_s"] == 0.0
    assert submitted[-1].data["first_error"] == "CyberGymOracleError"


def test_a_transient_failure_then_success_is_scored_with_the_retry_recorded(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(codes=(139, 0), vul_exit_code=139, vul_errors={2: _http(503)})

    scripted, state = _run(ctx, oracle)

    ledger = state.oracle_ledger
    candidate = ledger.candidates[0]
    assert candidate.submit_calls == 2 and not candidate.reused_intermediate
    # 재시도가 낸 새 제출이다 - 단 재시도한 호출이라 `cached`는 모른다(`None`).
    assert candidate.submission.poc_id == oracle.vul_results[-1].poc_id
    assert candidate.submission.cached is None
    assert ledger.final.status == oracle_log.STATUS_SCORED
    row = ledger.record()["candidates"][2]
    assert row["submit_calls"] == 2 and "reused_intermediate" not in row
    # 성공한 재시도도 첫 실패의 원인을 남긴다.
    assert row["first_error"] == "CyberGymOracleError"
    assert row["first_error_status"] == 503 and row["retry_wait_s"] == 0.0
    sent = [e for e in scripted.trace.events if e.kind is EventKind.ORACLE_SUBMITTED][-1]
    assert sent.data["submit_calls"] == 2 and "reused_intermediate" not in sent.data
    assert sent.data["first_error_status"] == 503 and sent.data["retry_wait_s"] == 0.0
    assert ledger.failed == 0  # 최종 결과가 성공이면 실패 시도로 세지 않는다


def test_a_single_successful_submit_carries_no_first_error_keys(ctx) -> None:  # noqa: F811
    scripted, state = _run(ctx, ReadingOracle(codes=(139, 0), vul_exit_code=139))

    assert all(FIRST_KEYS.isdisjoint(row) for row in state.oracle_ledger.record()["candidates"])
    assert FIRST_KEYS.isdisjoint(state.oracle_ledger.record()["final"])
    assert all(FIRST_KEYS.isdisjoint(e.data) for e in scripted.trace.events)


def test_a_connection_failure_then_success_records_a_null_status(ctx) -> None:  # noqa: F811
    _, state = _run(ctx, ReadingOracle(codes=(139, 0), vul_exit_code=139, vul_errors={2: _conn()}))

    row = state.oracle_ledger.record()["candidates"][2]
    assert row["first_error"] == "CyberGymOracleError" and row["first_error_status"] is None


def test_a_429_waits_out_the_documented_window_before_the_one_retry(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(codes=(139, 0), vul_exit_code=139, vul_errors={2: _http(429)})
    sleeps: list[float] = []

    _, state = _run(ctx, oracle, sleeps)

    assert sleeps == [oracle_log.RATE_LIMIT_WINDOW_S]
    assert state.oracle_ledger.candidates[0].submit_calls == 2
    assert state.oracle_ledger.final.status == oracle_log.STATUS_SCORED
    row = state.oracle_ledger.record()["candidates"][2]
    assert row["first_error_status"] == 429 and row["retry_wait_s"] == 60.0


def test_a_429_twice_waits_once_then_falls_back_to_the_earlier_submission(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(
        codes=(139, 0), vul_exit_code=139, vul_errors={2: _http(429), 3: _http(429)}
    )
    sleeps: list[float] = []

    _, state = _run(ctx, oracle, sleeps)

    ledger = state.oracle_ledger
    assert sleeps == [60]  # 두 번째 429는 기다리지 않는다
    assert len(oracle.vul_calls) == 4
    candidate = ledger.candidates[0]
    assert candidate.reused_intermediate and candidate.submit_calls == 2
    # 실패한 재제출 행이 마지막 429를 든다 - 경고의 레이트리미트 건수가 이 행을 센다.
    assert ledger.rate_limited == 1
    assert ledger.final.status == oracle_log.STATUS_SCORED


def test_a_429_then_a_4xx_is_one_wait_and_then_reuse(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(
        codes=(139, 0), vul_exit_code=139, vul_errors={2: _http(429), 3: _http(400)}
    )
    sleeps: list[float] = []

    _, state = _run(ctx, oracle, sleeps)

    assert sleeps == [60] and len(oracle.vul_calls) == 4
    failed = state.oracle_ledger.attempts[2]
    assert failed.first_failure.status == 429 and failed.error_status == 400
    assert state.oracle_ledger.candidates[0].reused_intermediate


def test_call_submit_vul_sleeps_exactly_once_for_two_429s(ctx) -> None:  # noqa: F811
    oracle = FakeOracle(vul_errors={0: _http(429), 1: _http(429)})
    sleeps: list[float] = []
    scripted = _scripted(ctx, [])
    scripted.oracle = oracle
    scripted.sleep = sleeps.append

    submission, failure, calls, first = runner._call_submit_vul(scripted, b"A", retry=True)

    assert submission is None and calls == 2 and sleeps == [60]
    assert failure.status == 429 and first.status == 429 and first.wait_s == 60.0


def test_a_5xx_or_connection_failure_retries_at_once_without_waiting(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(
        codes=(139, 0),
        vul_exit_code=139,
        vul_errors={2: _conn(), 3: _http(502)},
    )
    sleeps: list[float] = []

    _, state = _run(ctx, oracle, sleeps)

    assert sleeps == []
    assert len(oracle.vul_calls) == 4  # 한 번만 다시 낸다
    assert state.oracle_ledger.candidates[0].reused_intermediate


def test_a_4xx_is_not_retried_and_still_falls_back_to_the_earlier_submission(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(codes=(139, 0), vul_exit_code=139, vul_errors={2: _http(400)})
    sleeps: list[float] = []

    _, state = _run(ctx, oracle, sleeps)

    assert len(oracle.vul_calls) == 3 and sleeps == []
    candidate = state.oracle_ledger.candidates[0]
    assert candidate.reused_intermediate and candidate.submit_calls == 1


def test_without_an_earlier_submission_a_4xx_ends_in_no_candidate_and_no_retry(ctx) -> None:  # noqa: F811
    # 호출 0(중간 A)도 실패하면 이어받을 앞선 성공 제출이 없다.
    # ⏵ 2026-10-15 (사용자 승인) - 끝내 실패한 `submit`은 제출자가 아니라서, 그 Finding의 성공한
    # 크래시 중간 gen B(호출 1 · 139)가 구제돼 지정된다. 제출된 후보(`candidates`)는 여전히 0건이다.
    oracle = ReadingOracle(
        codes=(139, 0), vul_exit_code=139, vul_errors={0: _http(400), 2: _http(400)}
    )

    _, state = _run(ctx, oracle)

    ledger = state.oracle_ledger
    assert ledger.candidates == []
    assert len(oracle.vul_calls) == 3
    assert [a.poc for a in ledger.rescued] == [b"A" * 9]
    assert ledger.final.status == oracle_log.STATUS_SCORED and ledger.final.reason is None
    assert oracle_log.was_rescued(ledger.final.attempt, ledger.attempts)


def test_without_an_earlier_submission_a_transient_failure_retries_once_then_gives_up(
    ctx,  # noqa: F811
) -> None:
    oracle = ReadingOracle(
        codes=(139, 0),
        vul_exit_code=139,
        vul_errors={0: _http(500), 2: _http(500), 3: _http(500)},
    )

    _, state = _run(ctx, oracle)

    assert state.oracle_ledger.candidates == []
    assert len(oracle.vul_calls) == 4
    assert state.oracle_ledger.attempts[-1].submit_calls == 2


def test_an_intermediate_submission_is_never_retried(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(codes=(139, 0), vul_exit_code=139, vul_errors={0: _http(500)})

    _, state = _run(ctx, oracle)

    first = state.oracle_ledger.attempts[0]
    assert not first.ok and first.submit_calls == 1


def test_a_failure_without_the_status_contract_is_not_retried(ctx) -> None:  # noqa: F811
    # 어댑터는 모든 예외를 `status`가 있는 `CyberGymOracleError`로 바꾼다. 그 계약이 없는 예외는
    # 일시 실패인지 알 수 없다 - 재시도하지 않는다(그래도 앞선 제출로 이어받기는 한다).
    oracle = ReadingOracle(codes=(139, 0), vul_exit_code=139, vul_errors={2: RuntimeError("x")})

    _, state = _run(ctx, oracle)

    assert len(oracle.vul_calls) == 3
    assert state.oracle_ledger.candidates[0].reused_intermediate


# --- finalize -----------------------------------------------------------------


class _FlakyFinal(FakeOracle):
    """`finalize`가 앞의 호출에서 `errors`를 차례로 올리고 그 뒤로는 성공한다."""

    def __init__(self, errors: list[Exception], **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self._errors = list(errors)

    def finalize(self, poc, vul):  # noqa: ANN001, ANN201
        if self._errors:
            self.final_calls.append((poc, vul))
            raise self._errors.pop(0)
        return super().finalize(poc, vul)


def _finalize_only(ctx, oracle, sleeps=None):  # noqa: ANN001, ANN202, F811
    state, finding = _pov_state(ctx)
    state.oracle_ledger = oracle_log.OracleLedger(wired=True)
    state.oracle_ledger.add_attempt(
        oracle_log.vul_ok(
            finding.finding_id, b"A", oracle.submit_vul(b"A"), 1, designatable=True
        )
    )
    scripted = _scripted(ctx, [])
    scripted.oracle = oracle
    if sleeps is not None:
        scripted.sleep = sleeps.append
    runner._finalize_oracle(scripted, state)
    return scripted, state


def test_finalize_retries_a_transient_failure_once_and_records_both_calls(ctx) -> None:  # noqa: F811
    oracle = _FlakyFinal([_http(503)])

    scripted, state = _finalize_only(ctx, oracle)

    final = state.oracle_ledger.final
    assert final.status == oracle_log.STATUS_SCORED and final.calls == 2
    assert isinstance(final.outcome, OracleOutcome) and len(oracle.final_calls) == 2
    assert _final_data(scripted)["finalize_calls"] == 2
    record = state.oracle_ledger.record()["final"]
    assert record["finalize_calls"] == 2
    # 재시도 전 첫 실패의 원인 - 5xx는 기다리지 않는다.
    assert record["first_error"] == "CyberGymOracleError" and record["first_error_status"] == 503
    assert record["retry_wait_s"] == 0.0 and _final_data(scripted)["first_error_status"] == 503


def test_finalize_gives_up_after_the_one_retry(ctx) -> None:  # noqa: F811
    oracle = _FlakyFinal([_conn(), _http(500)])

    scripted, state = _finalize_only(ctx, oracle)

    final = state.oracle_ledger.final
    assert final.status == oracle_log.STATUS_FAILED and final.calls == 2
    assert len(oracle.final_calls) == 2
    assert _final_data(scripted)["finalize_calls"] == 2
    # 마지막 실패는 500이고 첫 실패는 연결 실패다 - 처음 원인이 남는다.
    assert final.first_failure.status is None and "500" in final.error_detail
    assert state.oracle_ledger.record()["final"]["first_error_status"] is None


def test_finalize_waits_out_the_window_on_a_429_and_retries_once(ctx) -> None:  # noqa: F811
    # 어댑터 docstring은 `/submit-fix`에 한도가 없다고 적는다(소스 판독) - 429가 오면 덮는 갈래다.
    oracle = _FlakyFinal([_http(429)])
    sleeps: list[float] = []

    scripted, state = _finalize_only(ctx, oracle, sleeps)

    final = state.oracle_ledger.final
    assert final.status == oracle_log.STATUS_SCORED and final.calls == 2
    assert len(oracle.final_calls) == 2
    assert sleeps == [oracle_log.RATE_LIMIT_WINDOW_S]
    record = state.oracle_ledger.record()["final"]
    assert record["first_error_status"] == 429 and record["retry_wait_s"] == 60.0
    assert _final_data(scripted)["retry_wait_s"] == 60.0 and record["finalize_calls"] == 2


def test_finalize_429_twice_waits_once_and_fails(ctx) -> None:  # noqa: F811
    oracle = _FlakyFinal([_http(429), _http(429)])
    sleeps: list[float] = []

    _, state = _finalize_only(ctx, oracle, sleeps)

    final = state.oracle_ledger.final
    assert final.status == oracle_log.STATUS_FAILED and final.calls == 2
    assert len(oracle.final_calls) == 2 and sleeps == [60]  # 두 번째 429는 기다리지 않는다


def test_finalize_5xx_retries_without_sleeping(ctx) -> None:  # noqa: F811
    sleeps: list[float] = []

    _finalize_only(ctx, _FlakyFinal([_http(503)]), sleeps)

    assert sleeps == []


@pytest.mark.parametrize("status", [400, 403, 404])
def test_finalize_does_not_retry_another_4xx(ctx, status) -> None:  # noqa: ANN001, F811
    oracle = _FlakyFinal([_http(status)])
    sleeps: list[float] = []

    scripted, state = _finalize_only(ctx, oracle, sleeps)

    final = state.oracle_ledger.final
    assert final.status == oracle_log.STATUS_FAILED and final.calls == 1
    assert sleeps == []
    assert len(oracle.final_calls) == 1
    assert "finalize_calls" not in _final_data(scripted)
    assert "finalize_calls" not in state.oracle_ledger.record()["final"]
    assert FIRST_KEYS.isdisjoint(state.oracle_ledger.record()["final"])
    assert FIRST_KEYS.isdisjoint(_final_data(scripted))


def test_a_final_with_no_candidate_has_no_finalize_call_count(ctx) -> None:  # noqa: F811
    state, _ = _pov_state(ctx)
    state.oracle_ledger = oracle_log.OracleLedger(wired=True)
    scripted = _scripted(ctx, [])
    scripted.oracle = FakeOracle()
    runner._finalize_oracle(scripted, state)

    assert "finalize_calls" not in _final_data(scripted)


# --- 일시 실패의 정의 - 결정적 실패는 `status=None`이어도 다시 부르지 않는다 ---------------------


def test_a_deterministic_failure_without_a_status_is_not_retried(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(codes=(139, 0), vul_exit_code=139, vul_errors={2: _shape()})

    _, state = _run(ctx, oracle)

    assert len(oracle.vul_calls) == 3  # 재시도 없음
    assert state.oracle_ledger.candidates[0].submit_calls == 1


def test_finalize_does_not_retry_a_deterministic_failure_without_a_status(ctx) -> None:  # noqa: F811
    oracle = _FlakyFinal([_shape()])

    _, state = _finalize_only(ctx, oracle)

    assert state.oracle_ledger.final.calls == 1 and len(oracle.final_calls) == 1


def test_is_transient_reads_5xx_or_the_transient_mark_only() -> None:
    assert oracle_log.is_transient(_http(500)) and oracle_log.is_transient(_conn())
    assert not oracle_log.is_transient(_shape())
    assert not oracle_log.is_transient(_http(429)) and not oracle_log.is_transient(_http(404))
    assert not oracle_log.is_transient(RuntimeError("x"))


# --- 재시도한 호출의 `cached`는 모른다 - 이 run 자신의 첫 호출이 쓴 행을 옛 캐시로 읽지 않는다 ---


def test_a_retried_vul_call_reports_cached_as_unknown(ctx) -> None:  # noqa: F811
    oracle = FakeOracle(vul_errors={0: _http(503)}, vul_cached=True)
    scripted = _scripted(ctx, [])
    scripted.oracle = oracle

    retried, failure, calls, first = runner._call_submit_vul(scripted, b"A", retry=True)
    assert failure is None and calls == 2 and first.status == 503
    assert retried is not None and retried.cached is None

    # 재시도 없이 한 번에 된 호출은 어댑터가 가른 값을 그대로 든다.
    once, _, calls, first = runner._call_submit_vul(scripted, b"A", retry=True)
    assert calls == 1 and first is None and once is not None and once.cached is True


def _scored_final(ctx, oracle):  # noqa: ANN001, ANN202, F811
    scripted, state = _finalize_only(ctx, oracle)
    return state.oracle_ledger, state.oracle_ledger.final


def test_a_retried_finalize_reports_fix_cached_as_unknown_and_the_warning_does_not_claim_a_cache(
    ctx,  # noqa: F811
) -> None:
    # `_finalize_only`의 첫 `submit_vul`이 vul 다리를 캐시로 만들지 않게 vul_cached=False.
    ledger, final = _scored_final(ctx, _FlakyFinal([_http(503)], fix_cached=True, vul_cached=False))

    assert final.calls == 2 and final.outcome.fix_cached is None
    assert ledger.record()["final"]["cached"]["fix"] is None
    warnings = " ".join(ledger.warnings())
    assert "이전 판정의 캐시다" not in warnings
    assert "fix 다리는 재시도한 호출이라" in warnings  # 모른다고 말한다


def test_a_single_finalize_call_keeps_the_cache_claim(ctx) -> None:  # noqa: F811
    ledger, final = _scored_final(ctx, _FlakyFinal([], fix_cached=True, vul_cached=False))

    assert final.calls == 1 and final.outcome.fix_cached is True
    warnings = " ".join(ledger.warnings())
    assert "fix 다리는 이전 판정의 캐시다" in warnings
    assert "재시도한 호출이라" not in warnings


def test_the_reused_and_rate_limited_warnings_say_what_happened(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(
        codes=(139, 0), vul_exit_code=139, vul_errors={2: _http(429), 3: _http(429)}
    )

    _, state = _run(ctx, oracle, [])

    warnings = " ".join(state.oracle_ledger.warnings())
    assert "그중 1건은 채점기 레이트리미트(HTTP 429)다" in warnings
    assert "모델이 submit한 PoC의 제출은 한 번 기다려 다시 낸 뒤에도 429면 여기 센다" in warnings
    assert "최종 후보 1건은 지정 가능 재제출이 실패해" in warnings
    assert "앞선 중간 제출(같은 바이트)을 이어받았다" in warnings


def test_a_retried_vul_submission_warns_that_its_cache_flag_is_unknown(ctx) -> None:  # noqa: F811
    # 앞선 중간 제출이 없으니 이 바이트의 첫 제출이 재시도한 호출이다 - `cached`를 못 가린다.
    scripted, state, finding = _twin_setup(ctx, FakeOracle(vul_errors={0: _http(503)}))

    _submit(scripted, state, finding, b"A", designatable=True)
    runner._finalize_oracle(scripted, state)

    warnings = " ".join(state.oracle_ledger.warnings())
    assert "vul 다리는 재시도한 호출이라" in warnings


# --- 장부 - 이어받은 행은 서버 호출이 아니다 ----------------------------------------------


def test_a_reused_row_is_not_counted_as_a_server_submission(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(
        codes=(139, 0), vul_exit_code=139, vul_errors={2: _http(500), 3: _http(500)}
    )

    _, state = _run(ctx, oracle)

    ledger = state.oracle_ledger
    # 성공한 서버 호출은 중간 gen 둘뿐이다 - 이어받은 후보는 앞선 제출의 사본이다.
    assert ledger.submitted == 2 and ledger.reused == 1 and len(ledger.candidates) == 1
    summary = ledger.summary()
    assert summary["vul_submitted"] == 2 and summary["vul_reused"] == 1
    assert summary["vul_candidates"] == 1


def test_a_run_without_a_reused_row_has_no_vul_reused_key(ctx) -> None:  # noqa: F811
    _, state = _run(ctx, ReadingOracle(codes=(139, 0), vul_exit_code=139))

    assert "vul_reused" not in state.oracle_ledger.summary()


# --- 이어받기는 같은 finding·같은 바이트의 앞선 성공 제출만 (`OracleLedger.intermediate_twin`) ---


def _submit(scripted, state, finding, poc, *, designatable):  # noqa: ANN001, ANN202
    runner._submit_vul_to_oracle(
        scripted, state, finding, poc, designatable=designatable, in_validate_span=False
    )


def _twin_setup(ctx, oracle):  # noqa: ANN001, ANN202, F811
    state, finding = _pov_state(ctx)
    state.oracle_ledger = oracle_log.OracleLedger(wired=True)
    scripted = _scripted(ctx, [])
    scripted.oracle = oracle
    return scripted, state, finding


def test_another_findings_earlier_submission_of_the_same_bytes_is_not_inherited(ctx) -> None:  # noqa: F811
    # F1의 중간 제출 A는 성공(호출 0). F2가 A를 submit하는데 그 재제출이 두 번 다 실패한다.
    oracle = FakeOracle(vul_errors={1: _http(500), 2: _http(500)})
    scripted, state, f1 = _twin_setup(ctx, oracle)
    f2 = f1.model_copy(update={"finding_id": "F2"})

    _submit(scripted, state, f1, b"A", designatable=False)
    _submit(scripted, state, f2, b"A", designatable=True)

    ledger = state.oracle_ledger
    assert len(oracle.vul_calls) == 3
    assert ledger.candidates == [] and ledger.reused == 0
    assert ledger.none_reason() == oracle_log.REASON_ALL_VUL_FAILED


def test_the_same_finding_with_different_bytes_is_not_inherited(ctx) -> None:  # noqa: F811
    # 같은 finding의 중간 제출은 A, submit은 B - B의 재제출이 실패해도 A로 대신하지 않는다.
    oracle = FakeOracle(vul_errors={1: _http(500), 2: _http(500)})
    scripted, state, f1 = _twin_setup(ctx, oracle)

    _submit(scripted, state, f1, b"A", designatable=False)
    _submit(scripted, state, f1, b"B", designatable=True)

    ledger = state.oracle_ledger
    assert ledger.candidates == [] and ledger.reused == 0
    assert ledger.none_reason() == oracle_log.REASON_ALL_VUL_FAILED


def test_the_same_finding_and_bytes_is_inherited_in_the_same_setup(ctx) -> None:  # noqa: F811
    # 위 둘의 대조군 - 같은 장치에서 조건이 맞으면 이어받는다(두 음성이 장치 탓으로 초록이 아니다).
    oracle = FakeOracle(vul_errors={1: _http(500), 2: _http(500)})
    scripted, state, f1 = _twin_setup(ctx, oracle)

    _submit(scripted, state, f1, b"A", designatable=False)
    _submit(scripted, state, f1, b"A", designatable=True)

    assert [a.reused_intermediate for a in state.oracle_ledger.candidates] == [True]
