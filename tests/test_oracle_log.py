"""채점기 장부와 사이드카 — 지정 결과·요약·경고·파일 (#386).

러너 없이 순수하게 본다. 러너가 이 장부를 어떻게 채우는지는 `tests/test_oracle_wiring.py`가,
파이프라인 끝까지 이어지는지는 `tests/test_pipeline_e2e.py`가 본다.

⚠ 후보가 둘 이상일 때의 규칙(같은 바이트는 접고, 남은 것은 (Critic 판정 →) 등급 →
`confidence` → 작은 바이트 → PoC 해시의 전순서로 — ⏵ 2026-10-04에 「동점이면 거부」가
이 사슬로 바뀌었다)은
`runner._designate_final`의 것이고 **이 파일이 아니다** - 장부는 지정한 근거(`designation`)와
거부 사유(`reason`)를 **기록**할
뿐이다. 규칙 자체를 단언하는 검사는 `tests/test_oracle_wiring.py` 끝 절에 있다.
"""

import hashlib
import json
from pathlib import Path

import pytest

from src.interfaces.oracle import OracleOutcome, VulSubmission
from src.orchestrator import oracle_log
from src.orchestrator.oracle_log import FinalAttempt, OracleLedger, OracleLog

_SUBMISSION = VulSubmission(
    task_id="arvo:1065", poc_id="a" * 32, response_exit_code=77, cached=False
)
_OUTCOME = OracleOutcome(
    task_id="arvo:1065",
    poc_id="a" * 32,
    poc_hash="b" * 64,
    poc_length=12,
    vul_exit_code=77,
    fix_exit_code=0,
    success=True,
    fix_cached=False,
)


def _ok(finding_id: str, poc: bytes = b"poc", submission: VulSubmission = _SUBMISSION):  # noqa: ANN202
    return oracle_log.vul_ok(finding_id, poc, submission, wall_ms=5, designatable=True)


def _failed(finding_id: str, poc: bytes = b"poc"):  # noqa: ANN202
    return oracle_log.vul_failed(
        finding_id, poc, RuntimeError("server refused"), wall_ms=3, designatable=True
    )


def _cached_vul() -> VulSubmission:
    return VulSubmission("arvo:1065", "a" * 32, 77, cached=True)


# --- 지정할 후보 -------------------------------------------------------------------


def test_no_poc_and_all_submissions_failed_are_different_reasons() -> None:
    """「낼 PoC가 없었다」와 「냈는데 전부 실패했다」는 다른 사건이다(§0)."""
    empty = OracleLedger(wired=True)
    broken = OracleLedger(wired=True)
    broken.add_attempt(_failed("find_0001"))

    assert empty.none_reason() == oracle_log.REASON_NO_POC
    assert broken.none_reason() == oracle_log.REASON_ALL_VUL_FAILED


class _RateLimited(RuntimeError):
    """`CyberGymOracleError`와 같은 모양 — `status`를 들고 오는 예외다.

    어댑터를 import하지 않는다 — `src/orchestrator/**`는 `src.adapters`를 import하지
    않고(`test_orchestrator_source_has_no_adapter_import`), 장부도 그래서 duck typing으로 읽는다.
    본문은 저자 서버가 21번째에 돌려주는 것이다(어댑터 모듈 docstring의 2026-10-02 실측).
    """

    def __init__(self) -> None:
        super().__init__("Rate limit exceeded for agent <id>. Max 20 requests per 60s.")
        self.status = 429


def _rate_limited(finding_id: str, poc: bytes = b"poc"):  # noqa: ANN202
    return oracle_log.vul_failed(finding_id, poc, _RateLimited(), wall_ms=3, designatable=True)


def test_the_http_status_of_a_failed_submission_survives_in_the_ledger() -> None:
    """429(레이트리미트)와 그 밖의 실패가 **사유 본문을 파싱하지 않고** 갈려야 한다 (#422)."""
    assert _rate_limited("find_0001").error_status == 429
    assert _failed("find_0002").error_status is None


def test_a_rate_limited_attempt_carries_its_status_into_the_sidecar(tmp_path: Path) -> None:
    """사이드카에도 상태가 들어가야 한다 — 장부를 나중에 읽는 사람이 세어야 한다 (#422)."""
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.add_attempt(_rate_limited("find_0001"))

    row = ledger.record()["candidates"][0]

    assert row["status"] == "failed" and row["error_status"] == 429


def test_a_run_whose_submissions_were_all_rate_limited_says_so() -> None:
    """「한도에 걸려 상실했다」가 「냈는데 전부 실패했다」로 읽히면 안 된다 (#422)."""
    limited = OracleLedger(wired=True)
    limited.add_attempt(_rate_limited("find_0001"))

    assert limited.none_reason() == oracle_log.REASON_RATE_LIMITED


def test_a_mixed_failure_run_is_not_called_rate_limited() -> None:
    """한도 밖 실패가 섞이면 「한도 때문」이라 말할 수 없다 — 더 넓은 사유로 남긴다 (#422)."""
    mixed = OracleLedger(wired=True)
    mixed.add_attempt(_rate_limited("find_0001"))
    mixed.add_attempt(_failed("find_0002"))

    assert mixed.none_reason() == oracle_log.REASON_ALL_VUL_FAILED


def test_the_warning_names_the_rate_limit_even_when_a_candidate_survived(tmp_path: Path) -> None:
    """후보가 남아도 한도로 잃은 건이 있으면 경고가 그것을 말한다 (#422).

    ⚠ 이 항목은 **콘솔 본문의 어느 낱말을** 문다 — 성질을 물 형(形)을 못 찾았다.
    """
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.add_attempt(_ok("find_0001"))
    ledger.add_attempt(_rate_limited("find_0002"))

    assert any("429" in line for line in ledger.warnings())


def test_a_run_lost_to_the_rate_limit_is_not_told_it_had_no_poc(tmp_path: Path) -> None:
    """한도로 후보를 다 잃은 run의 경고가 「낼 PoC가 없었다」로 읽히면 안 된다 (#422).

    사유는 사이드카·매니페스트에 `rate_limited`로 맞게 들어가는데 **콘솔 한 줄만** 옛 두 사유의
    글자를 썼다 — 이 PR이 가르려던 바로 그 오독이다(§2.5 「산문과 코드는 양방향이다」).
    """
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.add_attempt(_rate_limited("find_0001"))
    ledger.set_final(FinalAttempt(status="none", reason=ledger.none_reason()))

    none_line = [line for line in ledger.warnings() if "최종 PoC" in line]

    assert len(none_line) == 1
    assert "submit한 PoC가 없어" not in none_line[0]
    assert "429" in none_line[0]


# --- 매니페스트 요약 -----------------------------------------------------------------


def test_an_unwired_run_has_no_summary_no_warnings_and_no_file(tmp_path: Path) -> None:
    ledger = OracleLedger(wired=False, log=OracleLog(tmp_path))

    assert ledger.summary() is None
    assert ledger.warnings() == []
    assert not (tmp_path / oracle_log.DIR_NAME).exists()


@pytest.mark.parametrize(
    ("final", "expected", "reason"),
    [
        (None, "not_reached", None),
        (FinalAttempt(status="none", reason="no_poc"), "none", "no_poc"),
        (FinalAttempt(status="none", reason="grade_unknown"), "none", "grade_unknown"),
        (FinalAttempt(status="failed", attempt=_ok("find_0001"), error="X"), "failed", None),
        (
            FinalAttempt(status="scored", attempt=_ok("find_0001"), outcome=_OUTCOME),
            "scored",
            None,
        ),
    ],
)
def test_the_summary_reports_counts_the_final_status_and_why_there_is_none(
    final: FinalAttempt | None, expected: str, reason: str | None, tmp_path: Path
) -> None:
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.add_attempt(_ok("find_0001"))
    ledger.add_attempt(_failed("find_0002"))
    ledger.add_attempt(_failed("find_0003"))
    if final is not None:
        ledger.set_final(final)

    assert ledger.summary() == {
        "vul_submitted": 1,
        "vul_failed": 2,
        "final": expected,
        "final_reason": reason,
        "record": "oracle/verdict.json",
    }


def test_the_summary_has_no_record_pointer_without_a_log() -> None:
    summary = OracleLedger(wired=True).summary()

    assert summary is not None and summary["record"] is None


# --- 경고 ---------------------------------------------------------------------------


def _warnings(final: FinalAttempt | None, *, failed_attempts: int = 0) -> list[str]:
    ledger = OracleLedger(wired=True)
    ledger.add_attempt(_ok("find_0001"))
    for index in range(failed_attempts):
        ledger.add_attempt(_failed(f"find_f{index}"))
    if final is not None:
        if final.attempt is not None and final.attempt not in ledger.attempts:
            ledger.add_attempt(final.attempt)  # 지정되는 후보는 장부에 있는 시도다
        ledger.set_final(final)
    return ledger.warnings()


def _scored(attempt=None, outcome: OracleOutcome = _OUTCOME) -> FinalAttempt:  # noqa: ANN001
    return FinalAttempt(status="scored", attempt=attempt or _ok("find_0001"), outcome=outcome)


def test_submission_failures_are_counted_in_a_warning() -> None:
    texts = _warnings(_scored(), failed_attempts=2)

    assert any("vul 제출 2건이 예외로 실패했다" in text for text in texts)


@pytest.mark.parametrize(
    ("final", "needle"),
    [
        (None, "8단계가 끝까지 돌지 않아"),
        (
            FinalAttempt(status="failed", attempt=_ok("find_0001"), error="CyberGymOracleError"),
            "최종 판정이 예외(CyberGymOracleError)로 실패했다",
        ),
        (FinalAttempt(status="none", reason="no_poc"), "PoV가 submit한 PoC가 없어"),
        (
            FinalAttempt(status="none", reason="all_vul_submissions_failed"),
            "vul 제출이 전부 실패해",
        ),
        (
            FinalAttempt(status="none", reason="grade_unknown"),
            "등급 또는 confidence를 모르는 것이 있어",
        ),
        # 사유는 **다섯**이다. 이 표가 넷만 들어서 `rate_limited`의 경고가 옛 사유의 글자로
        # 떨어지는 것을 못 잡았다 - 사유를 더할 때 이 표도 같이 늘린다.
        (
            FinalAttempt(status="none", reason="rate_limited"),
            "레이트리미트(HTTP 429)로 실패해",
        ),
    ],
)
def test_a_run_without_a_score_says_so_and_why(final: FinalAttempt | None, needle: str) -> None:
    texts = _warnings(final)

    assert any(needle in text and "채점 점수가 없다" in text for text in texts), texts


def test_every_none_reason_gets_its_own_sentence() -> None:
    """🔴 **성질을 문다** - 위 표는 철자를 문다(§2.5 「검사가 무는 것을 세게 적지 않는다」).

    `_none_warning` docstring이 「사유마다 다른 말이다(같은 문장이면 사유가 안 가려진다)」라고
    적는데, 사유를 하나 더하고 분기를 안 더하면 그 문장이 거짓이 된다 - 위 표에 행을 안 넣으면
    아무도 안 잡는다. 여기서는 모듈의 `REASON_*` **전부**를 세어 문장이 서로 다른지 본다.

    ⏵ 2026-10-04 - **다섯에서 넷이 됐다.** `REASON_GRADE_TIE`를 지웠다 - 지정 사슬이 전순서라
    어느 run도 그 사유를 못 낸다(`runner._designate_final`). 수를 박아 두는 것이 이 검사의
    요점이다 - 사유를 더하거나 빼면서 분기를 안 맞추면 여기서 걸린다.
    """
    reasons = [
        value
        for name, value in vars(oracle_log).items()
        if name.startswith("REASON_") and isinstance(value, str)
    ]
    sentences = {
        reason: _warnings(FinalAttempt(status="none", reason=reason)) for reason in reasons
    }

    assert len(reasons) == 4, reasons
    assert all(len(lines) == 1 for lines in sentences.values()), sentences
    assert len({lines[0] for lines in sentences.values()}) == len(reasons), sentences


def test_a_scored_run_warns_only_when_a_leg_came_from_the_cache() -> None:
    stale_vul = _scored(_ok("find_0009", b"other", submission=_cached_vul()))
    stale_fix = _scored(
        outcome=OracleOutcome("arvo:1065", "a" * 32, "b" * 64, 12, 77, 0, True, True)
    )

    assert _warnings(_scored()) == []
    assert any("vul 다리는 이전 판정의 캐시다" in text for text in _warnings(stale_vul))
    assert any("fix 다리는 이전 판정의 캐시다" in text for text in _warnings(stale_fix))


def test_a_duplicate_inside_the_same_run_is_not_a_stale_verdict() -> None:
    """finding 둘이 같은 PoC를 내면(`dev_fake`는 늘 그렇다) 둘째의 `cached`는 참이지만 그 행은 이
    run이 만들었다 - 옛 판정이 아니다. 경고가 매번 뜨면 사람이 경고를 안 읽는다."""
    ledger = OracleLedger(wired=True)
    first = oracle_log.vul_ok("find_0001", b"same", _SUBMISSION, wall_ms=1, designatable=True)
    second = oracle_log.vul_ok("find_0002", b"same", _cached_vul(), wall_ms=1, designatable=True)
    ledger.add_attempt(first)
    ledger.add_attempt(second)
    ledger.set_final(_scored(second))

    assert ledger.warnings() == []
    assert ledger.record()["final"]["cached"] == {"vul": False, "fix": False}
    assert ledger.record()["candidates"][1]["cached"] is True  # 후보 자신의 호출은 적중이었다


def test_a_duplicate_of_a_stale_first_submission_is_stale() -> None:
    """앞의 제출이 이 run 전의 행을 돌려받았다면(캐시) 같은 바이트의 뒤 제출도 그 옛 코드다."""
    ledger = OracleLedger(wired=True)
    first = oracle_log.vul_ok("find_0001", b"same", _cached_vul(), wall_ms=1, designatable=True)
    second = oracle_log.vul_ok("find_0002", b"same", _cached_vul(), wall_ms=1, designatable=True)
    ledger.add_attempt(first)
    ledger.add_attempt(second)
    ledger.set_final(_scored(second))

    assert any("vul 다리는 이전 판정의 캐시다" in text for text in ledger.warnings())
    assert ledger.record()["final"]["cached"]["vul"] is True


def test_an_unknown_cache_state_is_not_reported_as_a_cache_hit() -> None:
    """`None`(못 가렸다)은 적중이 아니다 — 경고를 내지 않는다(없는 사실을 단언하지 않는다)."""
    unknown_vul = VulSubmission("arvo:1065", "a" * 32, 77, cached=None)
    unknown_fix = OracleOutcome("arvo:1065", "a" * 32, "b" * 64, 12, 77, 0, True, None)

    assert _warnings(_scored(_ok("find_0001", submission=unknown_vul), unknown_fix)) == []


def test_every_warning_is_printable_on_a_cp949_console() -> None:
    """콘솔에 찍히는 문장이다 - `U+2014`·`U+26A0`이 이 장비의 stdout을 죽인다. cp949여야 한다."""
    stale_fix = OracleOutcome("arvo:1065", "a" * 32, "b" * 64, 12, 77, 0, True, True)
    finals = [
        None,
        FinalAttempt(status="failed", attempt=_ok("f"), error="CyberGymOracleError"),
        FinalAttempt(status="none", reason="no_poc"),
        FinalAttempt(status="none", reason="all_vul_submissions_failed"),
        FinalAttempt(status="none", reason="grade_unknown"),
        _scored(_ok("f", submission=_cached_vul()), stale_fix),
    ]
    texts = [text for final in finals for text in _warnings(final, failed_attempts=1)]

    assert len(texts) >= 9, "경고 갈래가 안 걸렸다 - 이 검사가 공허하다"
    for text in texts:
        text.encode("cp949")


# --- 사이드카 -----------------------------------------------------------------------


def _read(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "oracle" / "verdict.json").read_bytes().decode("utf-8"))


def test_every_attempt_rewrites_the_sidecar_so_a_cut_short_run_keeps_what_was_submitted(
    tmp_path: Path,
) -> None:
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))

    ledger.add_attempt(_ok("find_0001", b"first"))
    record = _read(tmp_path)
    assert [c["finding_id"] for c in record["candidates"]] == ["find_0001"]
    # 8단계가 끝까지 안 갔다 - 「최종 없음」이 아니라 「아직 안 갔다」다.
    assert record["final"] is None

    ledger.add_attempt(_ok("find_0002", b"second"))
    assert [c["finding_id"] for c in _read(tmp_path)["candidates"]] == ["find_0001", "find_0002"]


def test_the_sidecar_carries_every_field_of_a_scored_run(tmp_path: Path) -> None:
    """필드마다 값을 단언한다 - `poc_id=""`·`poc_bytes=0`·종료 코드 맞바꿈 변이가 여기서 죽는다."""
    poc_a, poc_b = b"\x00\x01\x02 first", b"\xff\xfe second poc"
    sub_a = VulSubmission("arvo:1065", "1" * 32, 139, cached=False)
    sub_b = VulSubmission("arvo:1065", "2" * 32, 77, cached=True)
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.add_attempt(oracle_log.vul_ok("find_0001", poc_a, sub_a, wall_ms=11, designatable=True))
    attempt_b = oracle_log.vul_ok("find_0002", poc_b, sub_b, wall_ms=22, designatable=True)
    ledger.add_attempt(attempt_b)
    outcome = OracleOutcome(
        task_id="arvo:1065", poc_id="2" * 32, poc_hash=hashlib.sha256(poc_b).hexdigest(),
        poc_length=len(poc_b), vul_exit_code=77, fix_exit_code=0, success=True, fix_cached=False,
    )  # fmt: skip
    ledger.set_final(
        FinalAttempt(
            status="scored", wall_ms=33, designation="sole_candidate", attempt=attempt_b,
            outcome=outcome,
        )
    )

    sha_a, sha_b = hashlib.sha256(poc_a).hexdigest(), hashlib.sha256(poc_b).hexdigest()
    assert _read(tmp_path) == {
        "candidates": [
            {
                "finding_id": "find_0001", "poc_sha256": sha_a, "poc_bytes": len(poc_a),
                "leg": "vul", "wall_ms": 11, "status": "submitted", "poc_id": "1" * 32,
                "response_exit_code": 139, "cached": False,
            },
            {
                "finding_id": "find_0002", "poc_sha256": sha_b, "poc_bytes": len(poc_b),
                "leg": "vul", "wall_ms": 22, "status": "submitted", "poc_id": "2" * 32,
                "response_exit_code": 77, "cached": True,
            },
        ],
        "final": {
            "status": "scored", "designation": "sole_candidate", "wall_ms": 33,
            "finding_id": "find_0002",
            "poc_sha256": sha_b, "poc_bytes": len(poc_b), "task_id": "arvo:1065",
            "poc_id": "2" * 32, "vul_exit_code": 77, "fix_exit_code": 0, "success": True,
            "cached": {"vul": True, "fix": False},
        },
    }  # fmt: skip


def test_a_mode_that_did_not_run_is_null_in_the_sidecar_not_zero(tmp_path: Path) -> None:
    """§0: `fix`가 안 돌았으면(`None`) 0이 아니라 `null`이다 - `vul`에서 크래시가 안 난 경우다."""
    outcome = OracleOutcome("arvo:1065", "a" * 32, "b" * 64, 3, 0, None, False, None)
    attempt = _ok("find_0001", b"abc")
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.add_attempt(attempt)
    ledger.set_final(FinalAttempt(status="scored", attempt=attempt, outcome=outcome))

    final = _read(tmp_path)["final"]
    assert final["fix_exit_code"] is None
    assert final["vul_exit_code"] == 0, "진짜 0이 null로 바뀌었다"
    assert final["success"] is False
    assert final["cached"] == {"vul": False, "fix": None}, "못 가린 캐시 상태를 거짓으로 적었다"


def test_a_failed_attempt_and_a_failed_final_carry_their_reason(tmp_path: Path) -> None:
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.add_attempt(_failed("find_0001", b"abc"))
    chosen = _ok("find_0002", b"abcd")
    ledger.add_attempt(chosen)
    ledger.set_final(
        FinalAttempt(
            status="failed", wall_ms=9, designation="sole_candidate", attempt=chosen,
            error="TimeoutError", error_detail="timed out",
        )
    )  # fmt: skip

    record = _read(tmp_path)
    assert record["candidates"][0] == {
        "finding_id": "find_0001", "poc_sha256": hashlib.sha256(b"abc").hexdigest(),
        "poc_bytes": 3, "leg": "vul", "wall_ms": 3, "status": "failed",
        "error": "RuntimeError", "error_status": None, "error_detail": "server refused",
    }  # fmt: skip
    assert record["final"] == {
        "status": "failed", "designation": "sole_candidate", "wall_ms": 9,
        "finding_id": "find_0002",
        "poc_sha256": hashlib.sha256(b"abcd").hexdigest(), "poc_bytes": 4,
        "error": "TimeoutError", "error_detail": "timed out",
    }  # fmt: skip


def test_none_is_not_failed_in_the_sidecar(tmp_path: Path) -> None:
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.set_final(FinalAttempt(status="none", reason="no_poc"))

    final = _read(tmp_path)["final"]
    assert final == {"status": "none", "designation": None, "wall_ms": None, "reason": "no_poc"}


def test_several_candidates_are_none_with_no_designation_in_the_sidecar(tmp_path: Path) -> None:
    """후보가 둘 이상인데 **등급 또는 `confidence`를 몰라** 못 가르면 지정하지 않는다 -
    `designation`은 `null`이고 (근거를 지어내지 않는다) 사유가 `grade_unknown`이다.
    「후보가 없다」(`no_poc`)와 다른 사건이다. ⏵ 2026-10-04 - 앞 판은 `grade_tie`를 썼는데 그
    사유가 없어졌다(사슬이 전순서라 동점에서 거부할 자리가 없다 — `runner._designate_final`)."""
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.set_final(FinalAttempt(status="none", reason="grade_unknown"))

    assert _read(tmp_path)["final"] == {
        "status": "none", "designation": None, "wall_ms": None, "reason": "grade_unknown",
    }  # fmt: skip


def test_the_grades_the_designation_read_are_in_the_sidecar_only_when_it_read_them(
    tmp_path: Path,
) -> None:
    """지정이 후보 둘 이상에서 **읽은** 등급(`finding_id` -> 등급)이 `final.candidate_grades`로
    남는다. 안 읽은 run(후보 0·1건)은 키가 없다 - `{}`가 아니라 **없다**(안 읽었다 != 빈 등급)."""
    ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    ledger.set_final(
        FinalAttempt(status="none", reason="grade_unknown"), grades={"f_a": "E4", "f_b": "E4"}
    )
    assert _read(tmp_path)["final"]["candidate_grades"] == {"f_a": "E4", "f_b": "E4"}

    ledger.set_final(FinalAttempt(status="none", reason="no_poc"))
    assert "candidate_grades" not in _read(tmp_path)["final"]


def test_an_error_text_is_clipped_before_it_reaches_the_sidecar() -> None:
    long = RuntimeError("x" * (oracle_log.ERROR_DETAIL_CHARS * 3))

    attempt = oracle_log.vul_failed("find_0001", b"p", long, wall_ms=0, designatable=True)

    assert len(attempt.error_detail) == oracle_log.ERROR_DETAIL_CHARS


def test_the_sidecar_is_written_atomically_and_leaves_no_temp_file(tmp_path: Path) -> None:
    log = OracleLog(tmp_path)

    ref = log.write({"a": 1})
    log.write({"a": 2})

    assert ref == "oracle/verdict.json"
    assert sorted(p.name for p in (tmp_path / "oracle").iterdir()) == ["verdict.json"]
    assert json.loads((tmp_path / "oracle" / "verdict.json").read_bytes()) == {"a": 2}


def test_the_sidecar_is_written_with_lf_newlines(tmp_path: Path) -> None:
    """텍스트 모드는 Windows에서 개행을 CRLF로 번역한다 - 바이트로 쓴다(`poc_log`와 같은 이유)."""
    OracleLog(tmp_path).write({"a": 1})

    assert b"\r" not in (tmp_path / "oracle" / "verdict.json").read_bytes()


class _BrokenLog:
    def write(self, record: object) -> str:
        raise PermissionError("denied")


def test_a_sidecar_write_failure_is_absorbed_and_surfaces_as_a_warning() -> None:
    """채점기는 추가형이라 사이드카가 run을 죽이면 안 된다 - 흡수하고 경고·요약에 남긴다."""
    ledger = OracleLedger(wired=True, log=_BrokenLog())  # type: ignore[arg-type]

    ledger.add_attempt(_ok("find_0001"))  # 예외가 새지 않는다

    assert ledger.write_error == "PermissionError"
    summary = ledger.summary()
    assert summary is not None and summary["record"] is None, "못 쓴 파일을 가리켰다"
    assert any("사이드카" in text and "PermissionError" in text for text in ledger.warnings())
