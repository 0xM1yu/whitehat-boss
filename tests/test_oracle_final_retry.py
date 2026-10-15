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
    submitted = [e for e in scripted.trace.events if e.kind is EventKind.ORACLE_SUBMITTED]
    assert [e.data.get("reused_intermediate") for e in submitted] == [None, None, True]
    failed = [e for e in scripted.trace.events if e.kind is EventKind.ORACLE_FAILED]
    assert [e.data["submit_calls"] for e in failed] == [2]


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
    sent = [e for e in scripted.trace.events if e.kind is EventKind.ORACLE_SUBMITTED][-1]
    assert sent.data["submit_calls"] == 2 and "reused_intermediate" not in sent.data
    assert ledger.failed == 0  # 최종 결과가 성공이면 실패 시도로 세지 않는다


def test_a_429_waits_out_the_documented_window_before_the_one_retry(ctx) -> None:  # noqa: F811
    oracle = ReadingOracle(codes=(139, 0), vul_exit_code=139, vul_errors={2: _http(429)})
    sleeps: list[float] = []

    _, state = _run(ctx, oracle, sleeps)

    assert sleeps == [oracle_log.RATE_LIMIT_WINDOW_S]
    assert state.oracle_ledger.candidates[0].submit_calls == 2
    assert state.oracle_ledger.final.status == oracle_log.STATUS_SCORED


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
    oracle = ReadingOracle(
        codes=(139, 0), vul_exit_code=139, vul_errors={0: _http(400), 2: _http(400)}
    )

    _, state = _run(ctx, oracle)

    assert state.oracle_ledger.candidates == []
    assert len(oracle.vul_calls) == 3
    assert state.oracle_ledger.final.reason == oracle_log.REASON_ALL_VUL_FAILED


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


def _finalize_only(ctx, oracle):  # noqa: ANN001, ANN202, F811
    state, finding = _pov_state(ctx)
    state.oracle_ledger = oracle_log.OracleLedger(wired=True)
    state.oracle_ledger.add_attempt(
        oracle_log.vul_ok(
            finding.finding_id, b"A", oracle.submit_vul(b"A"), 1, designatable=True
        )
    )
    scripted = _scripted(ctx, [])
    scripted.oracle = oracle
    runner._finalize_oracle(scripted, state)
    return scripted, state


def test_finalize_retries_a_transient_failure_once_and_records_both_calls(ctx) -> None:  # noqa: F811
    oracle = _FlakyFinal([_http(503)])

    scripted, state = _finalize_only(ctx, oracle)

    final = state.oracle_ledger.final
    assert final.status == oracle_log.STATUS_SCORED and final.calls == 2
    assert isinstance(final.outcome, OracleOutcome) and len(oracle.final_calls) == 2
    assert _final_data(scripted)["finalize_calls"] == 2
    assert state.oracle_ledger.record()["final"]["finalize_calls"] == 2


def test_finalize_gives_up_after_the_one_retry(ctx) -> None:  # noqa: F811
    oracle = _FlakyFinal([_conn(), _http(500)])

    scripted, state = _finalize_only(ctx, oracle)

    final = state.oracle_ledger.final
    assert final.status == oracle_log.STATUS_FAILED and final.calls == 2
    assert len(oracle.final_calls) == 2
    assert _final_data(scripted)["finalize_calls"] == 2


@pytest.mark.parametrize("status", [400, 403, 404, 429])
def test_finalize_does_not_retry_a_4xx(ctx, status) -> None:  # noqa: ANN001, F811
    oracle = _FlakyFinal([_http(status)])

    scripted, state = _finalize_only(ctx, oracle)

    final = state.oracle_ledger.final
    assert final.status == oracle_log.STATUS_FAILED and final.calls == 1
    assert len(oracle.final_calls) == 1
    assert _final_data(scripted)["finalize_calls"] == 1
    assert "finalize_calls" not in state.oracle_ledger.record()["final"]


def test_a_final_with_no_candidate_has_no_finalize_call_count(ctx) -> None:  # noqa: F811
    state, _ = _pov_state(ctx)
    state.oracle_ledger = oracle_log.OracleLedger(wired=True)
    scripted = _scripted(ctx, [])
    scripted.oracle = FakeOracle()
    runner._finalize_oracle(scripted, state)

    assert _final_data(scripted)["finalize_calls"] is None


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

    retried, failure, calls = runner._call_submit_vul(scripted, b"A", retry=True)
    assert failure is None and calls == 2
    assert retried is not None and retried.cached is None

    # 재시도 없이 한 번에 된 호출은 어댑터가 가른 값을 그대로 든다.
    once, _, calls = runner._call_submit_vul(scripted, b"A", retry=True)
    assert calls == 1 and once is not None and once.cached is True


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
