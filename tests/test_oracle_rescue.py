"""ADR 0013 결정 1 개정(2026-10-15) - `submit` 없이 끝난 Finding의 크래시 계단 중간 gen을 구제한다.

규칙: `submit` 없이 끝난 Finding(`designatable` 시도가 없다)의 중간 gen 중 **자기 `vul` 제출 응답
코드가 크래시 계단**(`oracle_log.vul_code_tier`)인 것이 지정 풀에 든다
(`OracleLedger.rescued`·`pool`).
새 제출은 없다. 같은 계단에서는 제출된 후보가 늘 앞선다(`submitted_over_rescued`), `fix` 다리는
지정된 하나에만 한 번 돈다.

진짜 `_run_pov_hops`를 스크립트한 모델로 돌리고(중간 gen이 `vul`에 나가는 `v3` 이상 판), 채점기
대역이 **바이트별로** 응답 코드를 정한다(`_CodedOracle`).
"""

import dataclasses

import pytest

from src.interfaces.model_client import SchemaParseError
from src.interfaces.oracle import VulSubmission
from src.orchestrator import oracle_log, runner
from src.orchestrator.oracle_log import OracleLedger
from src.orchestrator.trace import EventKind
from src.schemas.enums import EvidenceGrade
from src.schemas.pov import PovAction
from tests.oracle_fakes import TASK_ID, FakeOracle
from tests.test_nodes import ctx  # noqa: F401
from tests.test_oracle_wiring import _events, _findings, _flat, _ledger_of, _uncritiqued
from tests.test_pov_loop import _gen, _scripted, _with_pov_version

A, AA, AAA = b"A", b"AA", b"AAA"  # `_gen(count=n)`이 만드는 바이트
_SUBMIT = PovAction(action="submit", rationale="the last gen")


class _CodedOracle(FakeOracle):
    """제출한 **바이트**가 응답 코드를 정하는 대역 - 나머지는 `FakeOracle`(호출 기록)이다."""

    def __init__(self, codes: dict[bytes, int]) -> None:
        super().__init__(vul_exit_code=0)
        self._codes = codes

    def _code(self, poc: bytes) -> int:
        return self._codes.get(poc, 0)

    def submit_vul(self, poc: bytes) -> VulSubmission:
        result = dataclasses.replace(super().submit_vul(poc), response_exit_code=self._code(poc))
        self.vul_results[-1] = result
        return result

    def query_vul(self, poc: bytes) -> int | None:
        self.query_calls.append(poc)
        return self._code(poc)


def _run(ctx, actions, codes, *, findings=1):  # noqa: ANN001, ANN202, F811
    """finding마다 진짜 PoV 루프를 돌리고 끝에서 최종 지정 한 번(`_analyze`의 8단계 꼬리)."""
    state, targets = _findings(ctx, findings)
    state.oracle_ledger = OracleLedger(wired=True)
    scripted = _scripted(_with_pov_version(ctx, "v3"), actions)
    scripted.oracle = oracle = _CodedOracle(codes)
    for finding in targets:
        runner._run_pov(scripted, state, finding)
    runner._finalize_oracle(scripted, state)
    return scripted, state, oracle


def _final(scripted):  # noqa: ANN001, ANN202
    (final,) = _events(scripted, EventKind.ORACLE_FINAL)
    return final.data


# --- (a) submit 없이 끝난 Finding의 크래시 중간 gen이 구제된다 ----

_NO_SUBMIT_ENDINGS = {
    "repeat_spec": [_gen(count=1), _gen(count=2), _gen(count=1)],
    "parse_failed": [_gen(count=1), SchemaParseError("bad at hop1")],
    "max_hop": [_gen(count=n) for n in range(1, 40)],
}


@pytest.mark.parametrize("ending", _NO_SUBMIT_ENDINGS)
def test_a_crashing_intermediate_gen_of_a_finding_that_never_submitted_is_rescued(
    ctx, ending  # noqa: ANN001, F811
) -> None:
    """🔴 (a) 현 코드(구제 전)에서는 `submit`이 없으니 후보 0건 - `none`/`no_poc`로 끝나 이 검사가
    빨갛다. 이제는 첫 gen(139)이 지정되고 그 행으로 `fix` 다리가 돌며 `rescued`가 참이다."""
    scripted, state, oracle = _run(ctx, _NO_SUBMIT_ENDINGS[ending], {A: 139})

    ledger = state.oracle_ledger
    assert ledger.candidates == [], "submit이 없었다 - 제출된 후보는 0건이다"
    assert [a.poc for a in ledger.rescued] == [A]
    assert [poc for poc, _ in oracle.final_calls] == [A]
    data = _final(scripted)
    assert (data["status"], data["designation"], data["rescued"]) == (
        "scored", "sole_candidate", True,
    )
    assert data["poc_sha256"] == ledger.rescued[0].poc_sha256
    record = ledger.record()
    assert record["final"]["rescued"] is True
    assert [row.get("rescued") for row in record["candidates"]][0] is True
    assert any("submit하지 않은 중간 gen" in w for w in ledger.warnings())


def test_the_rescue_reuses_the_ledger_row_and_submits_nothing_new(ctx) -> None:  # noqa: ANN001, F811
    """새 제출이 없다 - 구제된 바이트는 중간 gen 때 한 번 나갔을 뿐이고 `fix` 다리가 그 행의
    `submission`을 그대로 받는다."""
    _, state, oracle = _run(ctx, _NO_SUBMIT_ENDINGS["repeat_spec"], {A: 139})

    assert oracle.vul_calls == [A, AA], "중간 gen 둘이 한 번씩 - 구제가 제출을 더하지 않았다"
    ((poc, vul),) = oracle.final_calls
    assert poc == A and vul is oracle.vul_results[0]
    assert state.oracle_ledger.final.attempt.designatable is False


# --- (b) 크래시 계단이 아니면 구제하지 않는다 ----


@pytest.mark.parametrize("code", [0, 71, 300, 124, 125, 126, 127, 137, 255, 256])
def test_a_non_crash_tier_intermediate_gen_is_not_rescued(ctx, code) -> None:  # noqa: ANN001, F811
    """(b) 0·71(크래시 아님)과 환경 값(124~127·137·255·255 초과 · 300)은 크래시 계단이 아니다."""
    scripted, state, oracle = _run(ctx, _NO_SUBMIT_ENDINGS["repeat_spec"], {A: code, AA: code})

    assert state.oracle_ledger.rescued == []
    assert oracle.final_calls == []
    data = _final(scripted)
    assert (data["status"], data["reason"], data["rescued"]) == ("none", "no_poc", None)


def test_an_intermediate_gen_whose_submission_failed_is_not_rescued(ctx) -> None:  # noqa: ANN001, F811
    """제출이 실패한 행은 응답 코드가 없다 - 구제 대상이 아니다."""
    state, (finding,) = _findings(ctx, 1)
    state.oracle_ledger = OracleLedger(wired=True)
    state.oracle_ledger.add_attempt(
        oracle_log.vul_failed(finding.finding_id, A, RuntimeError("down"), 1, designatable=False)
    )
    assert state.oracle_ledger.rescued == [] and state.oracle_ledger.pool == []


# --- (c) submit한 Finding은 구제가 없다 ----


def test_a_finding_that_submitted_never_has_its_intermediates_rescued(ctx) -> None:  # noqa: ANN001, F811
    """(c) 중간 gen A(139)가 크래시였어도 `submit`한 것은 마지막 gen AA(코드 0)다 - 틀린 submit도
    구제가 없다. 지정은 submit된 AA이고 `rescued`는 거짓이다."""
    scripted, state, oracle = _run(ctx, [_gen(count=1), _gen(count=2), _SUBMIT], {A: 139})

    ledger = state.oracle_ledger
    assert ledger.rescued == []
    assert [a.poc for a in ledger.pool] == [AA]
    assert [poc for poc, _ in oracle.final_calls] == [AA]
    assert _final(scripted)["rescued"] is False
    assert ledger.record()["final"]["rescued"] is False
    assert not any("submit하지 않은 중간 gen" in w for w in ledger.warnings())


def test_a_submit_whose_vul_submission_failed_still_counts_as_having_submitted() -> None:
    """모델이 `submit`했는데 그 제출이 실패했다 - 그 Finding의 크래시 중간 gen은 구제가 없다."""
    ledger = OracleLedger(wired=True)
    sub = VulSubmission(TASK_ID, "a" * 32, 139)
    ledger.add_attempt(oracle_log.vul_ok("f", A, sub, 1, designatable=False))
    ledger.add_attempt(oracle_log.vul_failed("f", AA, RuntimeError("down"), 1, designatable=True))

    assert ledger.rescued == [] and ledger.pool == []


# --- (d)(e) 순위 ----


def test_at_the_same_tier_a_submitted_candidate_beats_a_rescued_one(ctx) -> None:  # noqa: ANN001, F811
    """(d) Finding 1은 submit 없이 끝나 A(139)가 구제되고, Finding 2는 AA(139)를 submit했다. 같은
    크래시 계단이다. 구제된 쪽이 **바이트가 작아** 사슬만 보면 이겼을 자리다 - 제출된 AA가 이기고
    근거는 `submitted_over_rescued`다."""
    scripted, state, oracle = _run(
        ctx,
        [_gen(count=1), _gen(count=1), _gen(count=2), _SUBMIT],
        {A: 139, AA: 139},
        findings=2,
    )

    ledger = state.oracle_ledger
    assert [a.poc for a in ledger.rescued] == [A] and [a.poc for a in ledger.candidates] == [AA]
    data = _final(scripted)
    assert (data["designation"], data["rescued"]) == ("submitted_over_rescued", False)
    assert [poc for poc, _ in oracle.final_calls] == [AA]


def test_a_rescued_crash_beats_another_findings_submitted_non_crash(ctx) -> None:  # noqa: ANN001, F811
    """(e) 응답 코드 계단이 먼저다 - 구제된 크래시(139)가 다른 Finding이 submit한 코드 0을 이긴다.
    근거는 `vul_crash`다."""
    scripted, state, oracle = _run(
        ctx,
        [_gen(count=1), _gen(count=1), _gen(count=2), _SUBMIT],
        {A: 139, AA: 0},
        findings=2,
    )

    data = _final(scripted)
    assert (data["designation"], data["rescued"]) == ("vul_crash", True)
    assert [poc for poc, _ in oracle.final_calls] == [A]


@pytest.mark.parametrize("order", ["rescued_first", "submitted_first"])
def test_the_rank_does_not_follow_the_ledger_order(order: str) -> None:
    """장부 순서를 뒤집어도 같다 - 제출된 쪽이 앞선다(자리로 고르지 않는다)."""
    sub = VulSubmission(TASK_ID, "a" * 32, 139)
    rescued = oracle_log.vul_ok("f_r", A, sub, 1, designatable=False)
    submitted = oracle_log.vul_ok("f_s", AA, sub, 1, designatable=True)
    attempts = (rescued, submitted) if order == "rescued_first" else (submitted, rescued)

    got = runner._designate_final(
        _ledger_of(*attempts),
        {"f_r": EvidenceGrade.E4, "f_s": EvidenceGrade.E0},  # 사슬은 구제된 쪽을 가리킨다
        _flat("f_r", "f_s"),
        {"f_r": 0, "f_s": 3},  # Critic uphold vs 반박 - 이것도 구제된 쪽에 유리하다
    )

    assert got == (submitted, "submitted_over_rescued", None)


def test_the_same_bytes_submitted_and_rescued_fold_into_the_submitted_one() -> None:
    """같은 바이트가 제출과 구제 양쪽에 있으면 선택이 없다 - 제출된 행이 지정돼 구제로 안 센다."""
    sub = VulSubmission(TASK_ID, "a" * 32, 139)
    rescued = oracle_log.vul_ok("f_r", A, sub, 1, designatable=False)
    submitted = oracle_log.vul_ok("f_s", A, sub, 1, designatable=True)

    picked, designation, _ = runner._designate_final(
        _ledger_of(rescued, submitted),
        dict.fromkeys(("f_r", "f_s"), EvidenceGrade.E0),
        _flat("f_r", "f_s"),
        _uncritiqued("f_r", "f_s"),
    )

    assert picked is submitted and designation == "sole_poc"


# --- (f) fix 다리는 한 번 ----


def test_the_fix_leg_runs_once_even_with_several_rescued_gens(ctx) -> None:  # noqa: ANN001, F811
    """(f) 구제된 크래시 gen이 둘(A·AAA)이어도 `fix` 다리는 지정된 하나에만 정확히 한 번이다 -
    작은 바이트 A가 지정된다."""
    actions = [_gen(count=1), _gen(count=3), _gen(count=1)]
    scripted, state, oracle = _run(ctx, actions, {A: 139, AAA: 139})

    assert sorted(a.poc for a in state.oracle_ledger.rescued) == [A, AAA]
    assert len(oracle.final_calls) == 1 and oracle.final_calls[0][0] == A
    assert _final(scripted)["designation"] == "smallest_poc"
    assert len(_events(scripted, EventKind.ORACLE_FINAL)) == 1


# --- 새 계단의 위치 ----


def test_the_new_chain_name_sits_right_after_the_vul_tier() -> None:
    """키와 이름이 자리마다 맞는다(`zip(strict=True)`)· 새 성분이 응답 코드 계단 바로 뒤다."""
    names = runner._DESIGNATION_CHAIN
    assert names[:2] == (
        oracle_log.DESIGNATION_VUL_CRASH,
        oracle_log.DESIGNATION_SUBMITTED_OVER_RESCUED,
    )
    assert oracle_log.DESIGNATION_SUBMITTED_OVER_RESCUED == "submitted_over_rescued"
    sub = VulSubmission(TASK_ID, "a" * 32, 139)
    attempt = oracle_log.vul_ok("f", A, sub, 1, designatable=False)
    key = runner._designation_key(attempt, {"f": EvidenceGrade.E0}, {"f": 0.5}, {"f": 1})
    assert len(key) == len(names) and key[:2] == (0, 1)  # (크래시 계단, 구제됨)
