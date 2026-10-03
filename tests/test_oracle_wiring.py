"""채점기(oracle) 배선 — 후보마다 vul, 후보가 하나일 때만 지정해 그 하나에만 fix (#386).

러너(`_run_pov`·`_finalize_oracle`)를 `dev_fake` 문맥에서 직접 돌려 **후보 N개가 vul 제출 N건이 되고
fix 다리는 후보가 0개면 0번 · 1개면 정확히 한 번 · 2개 이상이면 0번 도는지**(셋 다 건다),
지정·실패·부재가 서로 다른 기록이 되는지, 판정이 trace의 재현성 기준을 안 깨는지를 본다. CLI 조립
가드·파서·`submit.sh` 신원은 `tests/test_oracle_cli.py`가 본다.

⚠ **후보가 둘 이상일 때 고르는 규칙은 정해지지 않았다**(`runner._designate_final`) - 이 파일에는
어느 후보를 고르는 것이 맞는지를 단언하는 검사가 없다. 후보가 둘 이상이면 **아무것도 지정하지
않는다**는 것만 건다.

⚠ **이 파일은 `run_pipeline`·CLI를 돌리지 않는다.** 운영 배선(`cli.run` → `execute` → `build_deps` →
`Deps.oracle` → `RunContext.oracle`)이 끊기지 않았는지는 `tests/test_pipeline_e2e.py`가 파이프라인과
CLI 전체를 돌려 본다 — 여기서 `ctx.oracle`을 직접 꽂는 검사는 그 배선을 못 지킨다.
"""

import hashlib
from pathlib import Path

import pytest

from src.orchestrator import runner
from src.orchestrator.oracle_log import OracleLedger, OracleLog
from src.orchestrator.trace import EventKind
from src.schemas.enums import PovStopReason
from tests.oracle_fakes import TASK_ID, FakeOracle
from tests.test_nodes import _pov_state, ctx  # noqa: F401
from tests.test_pov_loop import _gen, _scripted

_SUBMITTED = PovStopReason.SUBMITTED
_POCS = [b"\x00first\xff", b"\x01second poc\xfe", b"\x02third\xfd\x00"]  # 회문도 대칭도 아니다


def _events(ctx, kind):  # noqa: ANN001, ANN202, F811
    return [e for e in ctx.trace.events if e.kind is kind]


def _findings(ctx, count):  # noqa: ANN001, ANN202, F811
    """한 state에 finding `count`개와 그 캡슐을 담는다 - 8단계가 도는 목록이다."""
    state, first = _pov_state(ctx)
    findings = [first]
    for _ in range(count - 1):
        more_state, more = _pov_state(ctx)
        state.update(more)
        state.finding_capsules.update(more_state.finding_capsules)
        findings.append(more)
    return state, findings


def _stub_loop(monkeypatch, results):  # noqa: ANN001, ANN202
    """`_run_pov_hops`를 부른 순서대로 정해진 `(바이트, 정지 사유)`를 내는 스텁으로 바꾼다."""
    queue = iter(results)
    monkeypatch.setattr(runner, "_run_pov_hops", lambda *args, **kwargs: next(queue))


def _drive(ctx, state, findings, oracle):  # noqa: ANN001, ANN202, F811
    """`_analyze`의 8단계 꼬리 그대로 - finding마다 `_run_pov`, 끝에서 한 번 `_finalize_oracle`."""
    ctx.oracle = oracle
    for finding in findings:
        runner._run_pov(ctx, state, finding)
    runner._finalize_oracle(ctx, state)


# --- 후보마다 vul · 지정은 후보가 하나일 때만 · fix는 지정한 하나에만 ---------------------------


@pytest.mark.parametrize("count", [2, 3])
def test_several_candidates_all_get_the_vul_leg_and_none_is_designated_so_fix_never_runs(
    ctx, monkeypatch, count  # noqa: ANN001, F811
) -> None:
    """차단 결함: 후보마다 vul·fix·판정을 다 내면 비최종 PoC에도 `-fix` 이미지가 돌고 trace에 판정이
    N건 남는다. 후보는 vul만 내고, 후보가 **둘 이상이면 지정하지 않는다** - 마지막·처음·점수로
    고르는 규칙은 조사 문서 1.3절이 막고 모델에게 묻는 방식은 별도 조각이다. 그래서 fix 다리·판정
    조회는 **0번**이고 기록은 「고르는 규칙이 정해지지 않았다」고 말한다. 바이트는 **내용으로**
    본다."""
    state, findings = _findings(ctx, count)
    state.oracle_ledger = OracleLedger(wired=True)
    _stub_loop(monkeypatch, [(poc, _SUBMITTED) for poc in _POCS[:count]])
    oracle = FakeOracle()

    _drive(ctx, state, findings, oracle)

    assert oracle.vul_calls == _POCS[:count]
    assert oracle.final_calls == [], "후보가 여럿인데 fix 다리가 돌았다"
    assert len(_events(ctx, EventKind.ORACLE_SUBMITTED)) == count
    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.subject_kind is None and final.subject_id is None
    assert final.data == {
        "status": "none",
        "designation": None,
        "reason": "policy_undecided",
        "poc_sha256": None,
        "poc_bytes": None,
        "task_id": None,
        "error": None,
        "record_ref": None,  # 이 문맥에는 사이드카 쓰는 객체가 없다
    }
    summary = state.oracle_ledger.summary()
    assert summary is not None
    assert (summary["final"], summary["final_reason"]) == ("none", "policy_undecided")
    assert any("고르는 규칙이 정해지지 않았다" in w for w in state.oracle_ledger.warnings())


def test_exactly_one_candidate_is_the_final_and_the_fix_leg_runs_exactly_once(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """후보가 하나뿐이면 그 하나가 최종이다 - 고를 것이 없어서이고 위치 규칙이 아니다. fix 다리·판정
    조회는 **정확히 한 번**이고, 그 PoC와 그 PoC의 vul 제출 결과가 넘어간다. 바이트는 내용으로 본다.
    """
    state, findings = _findings(ctx, 1)
    _stub_loop(monkeypatch, [(_POCS[0], _SUBMITTED)])
    oracle = FakeOracle()

    _drive(ctx, state, findings, oracle)

    assert oracle.vul_calls == [_POCS[0]]
    assert oracle.final_calls == [(_POCS[0], oracle.vul_results[0])]
    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.subject_kind == "finding" and final.subject_id == findings[0].finding_id
    assert final.data == {
        "status": "scored",
        "designation": "sole_candidate",
        "reason": None,
        "poc_sha256": hashlib.sha256(_POCS[0]).hexdigest(),
        "poc_bytes": len(_POCS[0]),
        "task_id": TASK_ID,
        "error": None,
        "record_ref": None,  # 이 문맥에는 사이드카 쓰는 객체가 없다
    }


def test_a_candidate_left_alone_by_failed_submissions_is_the_sole_candidate(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """후보는 vul 제출이 **성공한** PoC다 - 제출이 실패한 PoC는 vul 쪽 결과가 없어 후보가 아니다.
    PoC 셋 중 둘의 제출이 실패하면 남은 하나가 후보 하나라 지정된다(실패는 사유와 함께 기록·경고에
    남는다)."""
    state, findings = _findings(ctx, 3)
    _stub_loop(monkeypatch, [(poc, _SUBMITTED) for poc in _POCS])
    oracle = FakeOracle(vul_errors={0: OSError("down"), 2: OSError("down")})

    _drive(ctx, state, findings, oracle)

    assert oracle.vul_calls == _POCS
    assert oracle.final_calls == [(_POCS[1], oracle.vul_results[0])]
    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.subject_id == findings[1].finding_id
    assert final.data["status"] == "scored" and final.data["designation"] == "sole_candidate"
    assert len(_events(ctx, EventKind.ORACLE_FAILED)) == 2


def test_a_candidate_event_carries_nothing_a_reader_could_count_as_a_score(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """`any(success)`로 묶을 값이 후보 이벤트에 없다 - 점수는 `oracle_final` 하나다. 데이터를 통째로
    견준다 - 키 집합만이 아니라 값(해시·길이)도 고정한다."""
    state, findings = _findings(ctx, 2)
    _stub_loop(monkeypatch, [(poc, _SUBMITTED) for poc in _POCS[:2]])

    _drive(ctx, state, findings, FakeOracle())

    submitted = _events(ctx, EventKind.ORACLE_SUBMITTED)
    assert [e.data for e in submitted] == [
        {
            "leg": "vul",
            "task_id": TASK_ID,
            "poc_sha256": hashlib.sha256(poc).hexdigest(),
            "poc_bytes": len(poc),
        }
        for poc in _POCS[:2]
    ]


# --- 낼 PoC가 없는 루프는 제출하지 않는다 -------------------------------------------------


@pytest.mark.parametrize("reason", [r for r in PovStopReason if r is not _SUBMITTED])
def test_a_loop_that_did_not_submit_sends_nothing_even_with_a_partial_poc(
    ctx, monkeypatch, reason  # noqa: ANN001, F811
) -> None:
    """실제 루프는 `MAX_HOP`·`REPEAT_SPEC`·`PARSE_FAILED`에서 부분 PoC(`last_poc`)를 들고 돌아온다.
    스텁이 `(None, ...)`만 내면 `SUBMITTED` 가드를 지워도 초록이라, 부분 바이트를 실어 건다."""
    state, (finding,) = _findings(ctx, 1)
    _stub_loop(monkeypatch, [(b"partial poc", reason)])
    oracle = FakeOracle()

    _drive(ctx, state, [finding], oracle)

    assert oracle.vul_calls == [] and oracle.final_calls == []
    assert not _events(ctx, EventKind.ORACLE_SUBMITTED)


def test_a_submitted_loop_without_bytes_sends_nothing(ctx, monkeypatch) -> None:  # noqa: ANN001, F811
    """gen 없는 submit은 바이트가 `None`이다 - `None`을 제출하지 않는다."""
    state, (finding,) = _findings(ctx, 1)
    _stub_loop(monkeypatch, [(None, _SUBMITTED)])
    oracle = FakeOracle()

    _drive(ctx, state, [finding], oracle)

    assert oracle.vul_calls == []


def _real_loop_partial(ctx, actions):  # noqa: ANN001, ANN202, F811
    """진짜 `_run_pov_hops`를 스크립트한 모델로 돌려 부분 PoC를 들고 서는 루프를 만든다.

    triage·audit은 원래 문맥(`dev_fake` 모델)으로 하고, 스크립트한 모델은 8단계 루프에만 쓴다 -
    `PovAction`을 다른 역할이 받으면 스키마가 어긋난다.
    """
    state, (finding,) = _findings(ctx, 1)
    oracle = FakeOracle()
    ctx.oracle = oracle
    scripted = _scripted(ctx, actions)
    runner._run_pov(scripted, state, finding)
    return scripted, oracle


def _skipped_with_partial_poc(ctx, reason):  # noqa: ANN001, ANN202, F811
    skipped = [e for e in _events(ctx, EventKind.POV_SKIPPED) if e.data["reason"] == reason.value]
    assert skipped and skipped[-1].data["poc_bytes"] > 0, "루프가 부분 PoC 없이 섰다 - 공허하다"


def test_the_real_loop_stopped_by_max_hop_does_not_send_its_partial_poc(ctx) -> None:  # noqa: ANN001, F811
    max_hops = ctx.config.thresholds.max_hops

    scripted, oracle = _real_loop_partial(ctx, [_gen(count=n) for n in range(1, max_hops + 5)])

    _skipped_with_partial_poc(scripted, PovStopReason.MAX_HOP)
    assert oracle.vul_calls == []


def test_the_real_loop_stopped_by_a_repeated_poc_does_not_send_its_partial_poc(ctx) -> None:  # noqa: ANN001, F811
    scripted, oracle = _real_loop_partial(ctx, [_gen(count=8), _gen(count=8)])

    _skipped_with_partial_poc(scripted, PovStopReason.REPEAT_SPEC)
    assert oracle.vul_calls == []


def test_the_real_loop_stopped_by_a_parse_failure_does_not_send_its_partial_poc(ctx) -> None:  # noqa: ANN001, F811
    from src.interfaces.model_client import SchemaParseError

    scripted, oracle = _real_loop_partial(ctx, [_gen(count=8), SchemaParseError("bad at hop1")])

    _skipped_with_partial_poc(scripted, PovStopReason.PARSE_FAILED)
    assert oracle.vul_calls == []


# --- 실패는 흡수하고 사유를 남긴다 · 지정된 후보가 없는 것은 실패가 아니다 ----------------------


def test_a_failed_vul_submission_is_absorbed_and_recorded_and_the_others_still_go(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """셋째 후보의 vul 제출이 실패해도 run은 간다. 실패는 사유와 함께 남는다 - trace에는 예외 클래스
    이름만, 문구(URL·로케일 errno)는 사이드카용 장부에만. 남은 후보가 둘이라 지정은 안 된다."""
    state, findings = _findings(ctx, 3)
    _stub_loop(monkeypatch, [(poc, _SUBMITTED) for poc in _POCS])
    oracle = FakeOracle(vul_errors={2: RuntimeError("server refused http://10.0.0.1")})

    _drive(ctx, state, findings, oracle)  # 예외가 새지 않는다

    assert oracle.vul_calls == _POCS
    (failed,) = _events(ctx, EventKind.ORACLE_FAILED)
    assert failed.subject_id == findings[2].finding_id
    assert failed.data == {
        "leg": "vul",
        "poc_sha256": hashlib.sha256(_POCS[2]).hexdigest(),
        "poc_bytes": len(_POCS[2]),
        "error": "RuntimeError",
    }, "문구(URL·로케일 errno)는 trace에 없다"
    assert state.oracle_ledger.attempts[2].error_detail == "server refused http://10.0.0.1"
    assert oracle.final_calls == []


def test_a_failed_final_is_failed_not_none(ctx, monkeypatch) -> None:  # noqa: ANN001, F811
    state, findings = _findings(ctx, 1)
    _stub_loop(monkeypatch, [(_POCS[0], _SUBMITTED)])
    oracle = FakeOracle(final_error=TimeoutError("slow"))

    _drive(ctx, state, findings, oracle)

    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.data["status"] == "failed"
    assert final.data["error"] == "TimeoutError"
    assert final.data["designation"] == "sole_candidate"
    assert final.data["reason"] is None and final.data["task_id"] is None
    assert final.subject_id == findings[0].finding_id  # 지정은 됐다 - 판정을 못 읽었을 뿐이다
    assert final.data["poc_sha256"] == hashlib.sha256(_POCS[0]).hexdigest()
    assert not _events(ctx, EventKind.ORACLE_FAILED), "최종 단계의 실패는 oracle_final에 있다"


def test_no_poc_at_all_is_none_with_its_own_reason_not_a_failure(ctx, monkeypatch) -> None:  # noqa: ANN001, F811
    """「최종 PoC가 없다」가 「실패했다」로 읽히면 안 된다(§0). 낼 PoC가 없으니 `finalize`도
    안 부른다."""
    state, findings = _findings(ctx, 2)
    _stub_loop(monkeypatch, [(None, PovStopReason.MAX_HOP), (None, PovStopReason.PARSE_FAILED)])
    oracle = FakeOracle()

    _drive(ctx, state, findings, oracle)

    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.data["status"] == "none" and final.data["reason"] == "no_poc"
    assert final.data["designation"] is None
    assert final.subject_id is None and final.data["poc_sha256"] is None
    assert oracle.final_calls == []
    assert not _events(ctx, EventKind.ORACLE_FAILED)


def test_every_vul_submission_failing_is_none_with_a_different_reason(ctx, monkeypatch) -> None:  # noqa: ANN001, F811
    state, findings = _findings(ctx, 2)
    _stub_loop(monkeypatch, [(poc, _SUBMITTED) for poc in _POCS[:2]])
    oracle = FakeOracle(vul_errors={0: OSError("down"), 1: OSError("down")})

    _drive(ctx, state, findings, oracle)

    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.data["status"] == "none"
    assert final.data["reason"] == "all_vul_submissions_failed"
    assert oracle.final_calls == []
    assert len(_events(ctx, EventKind.ORACLE_FAILED)) == 2


def _final_data(ctx, monkeypatch, *, count, results, oracle):  # noqa: ANN001, ANN202, F811
    """한 시나리오를 돌리고 그 run의 `oracle_final` 데이터를 돌려준다(같은 `ctx`에 이어 붙는다)."""
    state, findings = _findings(ctx, count)
    _stub_loop(monkeypatch, results)
    before = len(_events(ctx, EventKind.ORACLE_FINAL))
    _drive(ctx, state, findings, oracle)
    finals = _events(ctx, EventKind.ORACLE_FINAL)
    assert len(finals) == before + 1
    return finals[-1].data


def test_every_way_of_ending_without_a_score_is_told_apart_from_the_others(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """「최종 PoC가 없다」의 사유 셋과 「최종 PoC가 실패했다」, 점수가 난 run이 모두 **서로 다른**
    기록이다 (§0 - 같은 모양이면 없음이 실패로, 실패가 없음으로 읽힌다). 사건마다 run을 돌려
    (status, reason, error, designation)을 모은다."""
    one = [(_POCS[0], _SUBMITTED)]
    two = [(_POCS[0], _SUBMITTED), (_POCS[1], _SUBMITTED)]
    nothing = [(None, PovStopReason.MAX_HOP), (None, PovStopReason.PARSE_FAILED)]
    both_down = FakeOracle(vul_errors={0: OSError("x"), 1: OSError("x")})
    scenarios = {
        "no_poc": {"count": 2, "results": nothing, "oracle": FakeOracle()},
        "all_vul_failed": {"count": 2, "results": two, "oracle": both_down},
        "several": {"count": 2, "results": two, "oracle": FakeOracle()},
        "final_failed": {
            "count": 1,
            "results": one,
            "oracle": FakeOracle(final_error=TimeoutError("slow")),
        },
        "scored": {"count": 1, "results": one, "oracle": FakeOracle()},
    }

    seen = {}
    for name, scenario in scenarios.items():
        data = _final_data(ctx, monkeypatch, **scenario)
        seen[name] = (data["status"], data["reason"], data["error"], data["designation"])

    assert seen == {
        "no_poc": ("none", "no_poc", None, None),
        "all_vul_failed": ("none", "all_vul_submissions_failed", None, None),
        "several": ("none", "policy_undecided", None, None),
        "final_failed": ("failed", None, "TimeoutError", "sole_candidate"),
        "scored": ("scored", None, None, "sole_candidate"),
    }
    assert len(set(seen.values())) == len(seen), "두 사건이 같은 기록이다"


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_an_interrupt_during_the_call_is_not_absorbed(ctx, monkeypatch, interrupt) -> None:  # noqa: ANN001, F811
    """`Exception`만 흡수한다 - TERM·HUP(`SystemExit`)과 Ctrl-C는 올라가고, 단계 구간은 실패로
    닫힌다. 그때 어느 oracle 이벤트도 안 남는다 - 이벤트 부재가 「제출 안 함」이 아닌
    이유다(`trace.py`)."""
    state, findings = _findings(ctx, 1)
    _stub_loop(monkeypatch, [(_POCS[0], _SUBMITTED)])

    with pytest.raises(interrupt):
        _drive(ctx, state, findings, FakeOracle(vul_errors={0: interrupt()}))

    end = _events(ctx, EventKind.STAGE_END)[-1]
    assert end.data["ok"] is False and end.data["error"] == interrupt.__name__
    assert not _events(ctx, EventKind.ORACLE_SUBMITTED)
    assert not _events(ctx, EventKind.ORACLE_FAILED)


# --- 추가형 · 미배선 · 시간 · trace 분류 ---------------------------------------------------

_KINDS = ("artifact", "candidate", "capsule", "decision", "evidence", "finding", "run")


def _store_ids(ctx):  # noqa: ANN001, ANN202, F811
    ids = {}
    for kind in _KINDS:
        ids[kind] = sorted(
            getattr(obj, f"{kind}_id", None) or obj.run_id for obj in ctx.store.query(kind)
        )
    return ids


def test_the_oracle_phase_changes_no_finding_and_creates_no_evidence_of_any_kind(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """채점 판정은 Evidence가 아니다(#232) - 게이트(`gate.py`)가 읽는 근거를 **어느 종류로도**
    늘리지 않는다(`SANITIZER`·`CRASH`·`POC_RUN` 셋이 E4를 연다). 저장소의 모든 객체 종류가 전후로
    같다. 후보가 하나라 `fix` 다리·판정 조회까지 실제로 돈다."""
    state, findings = _findings(ctx, 1)
    _stub_loop(monkeypatch, [(_POCS[0], _SUBMITTED)])
    before = _store_ids(ctx)
    states_before = {f.finding_id: state.findings[f.finding_id].state for f in findings}
    oracle = FakeOracle()

    _drive(ctx, state, findings, oracle)

    assert len(oracle.final_calls) == 1, "최종 단계가 안 돌았다 - 이 검사가 공허하다"
    assert _store_ids(ctx) == before
    assert {f.finding_id: state.findings[f.finding_id].state for f in findings} == states_before


def test_without_an_oracle_nothing_is_recorded_and_no_stage_span_is_opened(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """기본(미배선)이면 이벤트도 단계 구간도 장부도 없다 - 옛 trace가 바이트 그대로 남는 이유다."""
    state, findings = _findings(ctx, 2)
    _stub_loop(monkeypatch, [(poc, _SUBMITTED) for poc in _POCS[:2]])
    ctx.oracle = None
    before = len(ctx.trace.events)

    for finding in findings:
        runner._run_pov(ctx, state, finding)
    runner._finalize_oracle(ctx, state)

    assert len(ctx.trace.events) == before, "채점기가 없는데 이벤트가 났다(단계 구간 포함)"
    assert state.oracle_ledger.attempts == []
    assert state.oracle_ledger.summary() is None and state.oracle_ledger.warnings() == []


def _open_stage_of_each_oracle_event(ctx):  # noqa: ANN001, ANN202, F811
    open_stage, seen = None, []
    for event in ctx.trace.events:
        if event.kind is EventKind.STAGE_START:
            open_stage = event.stage
        elif event.kind is EventKind.STAGE_END:
            open_stage = None
        elif event.kind.value.startswith("oracle"):
            seen.append((event.kind, open_stage))
    return seen


def test_the_round_trip_is_inside_a_stage_8_interval_and_timed(ctx, monkeypatch) -> None:  # noqa: ANN001, F811
    """도커 왕복이 단계 시간 장부에서 빠지던 결함: 이벤트가 8단계 `stage_end` **뒤**에 났고
    `wall_ms`도 없었다. 시계를 10 ms씩 가게 해 값을 못박는다 - 구간 시작(0) · 호출 직전(10) ·
    직후(20) · 구간 끝(30)이라 이벤트는 10, 구간은 30이다."""
    import itertools

    state, findings = _findings(ctx, 1)
    _stub_loop(monkeypatch, [(_POCS[0], _SUBMITTED)])
    tick = itertools.count(0, 10)
    ctx.monotonic_ms = lambda: next(tick)
    before = len(ctx.trace.events)

    _drive(ctx, state, findings, FakeOracle())

    assert _open_stage_of_each_oracle_event(ctx) == [
        (EventKind.ORACLE_SUBMITTED, 8),
        (EventKind.ORACLE_FINAL, 8),
    ]
    submitted = _events(ctx, EventKind.ORACLE_SUBMITTED)[0]
    final = _events(ctx, EventKind.ORACLE_FINAL)[0]
    assert (submitted.wall_ms, final.wall_ms) == (10, 10)
    ends = [e for e in ctx.trace.events[before:] if e.kind is EventKind.STAGE_END]
    assert [(e.stage, e.wall_ms) for e in ends] == [(8, 30), (8, 30)]


def test_a_failed_call_is_timed_and_inside_the_interval_too(ctx, monkeypatch) -> None:  # noqa: ANN001, F811
    import itertools

    state, findings = _findings(ctx, 1)
    _stub_loop(monkeypatch, [(_POCS[0], _SUBMITTED)])
    tick = itertools.count(0, 10)
    ctx.monotonic_ms = lambda: next(tick)

    _drive(ctx, state, findings, FakeOracle(vul_errors={0: RuntimeError("x")}))

    assert _open_stage_of_each_oracle_event(ctx) == [
        (EventKind.ORACLE_FAILED, 8),
        (EventKind.ORACLE_FINAL, 8),
    ]
    assert _events(ctx, EventKind.ORACLE_FAILED)[0].wall_ms == 10


_VOLATILE = ("poc_id", "cached", "fix_cached", "vul_exit_code", "fix_exit_code", "success")


def test_no_server_reported_value_reaches_the_trace(ctx, monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001, F811
    """분류: 서버가 돌려준 값(휘발·장비 의존·문구)은 사이드카에만 간다. 서버가 낸 `poc_id` 값 자체도
    trace 어디에도 없다 - 키 이름만이 아니라 **값**으로 센다. trace는 사이드카를 고정 경로로
    가리킨다."""
    state, findings = _findings(ctx, 1)
    state.oracle_ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    _stub_loop(monkeypatch, [(_POCS[0], _SUBMITTED)])
    oracle = FakeOracle()

    _drive(ctx, state, findings, oracle)

    assert len(oracle.final_calls) == 1, "판정이 있는 최종 단계가 안 돌았다 - 공허하다"
    events = [e for e in ctx.trace.events if e.kind.value.startswith("oracle")]
    assert len(events) == 2
    for event in events:
        assert not [key for key in _VOLATILE if key in event.data], event.data
    poc_ids = [a.submission.poc_id for a in state.oracle_ledger.attempts if a.submission]
    assert len(poc_ids) == 1 and poc_ids[0]  # 서버가 낸 uuid - 이것이 trace에 새면 두 run이 갈린다
    trace_text = "".join(e.model_dump_json() for e in ctx.trace.events)
    assert not [poc_id for poc_id in poc_ids if poc_id in trace_text]
    (final,) = [e for e in events if e.kind is EventKind.ORACLE_FINAL]
    assert final.data["record_ref"] == "oracle/verdict.json"
    assert (tmp_path / "oracle" / "verdict.json").is_file()


def test_the_runner_leaves_a_sidecar_that_survives_a_run_cut_short_before_the_final(
    ctx, monkeypatch, tmp_path: Path  # noqa: ANN001, F811
) -> None:
    """8단계가 중간에 끊기면(`_finalize_oracle`에 못 온다) 그때까지 나간 vul 제출은 사이드카에 남고
    `final`은 `null`이다 - 「최종 없음」과 「아직 안 갔다」가 다르다."""
    import json

    state, findings = _findings(ctx, 2)
    state.oracle_ledger = OracleLedger(wired=True, log=OracleLog(tmp_path))
    _stub_loop(monkeypatch, [(_POCS[0], _SUBMITTED), (_POCS[1], _SUBMITTED)])
    ctx.oracle = FakeOracle()

    runner._run_pov(ctx, state, findings[0])  # 둘째 finding에서 끊겼다고 치고 최종 단계를 안 부른다

    record = json.loads((tmp_path / "oracle" / "verdict.json").read_bytes())
    assert [c["finding_id"] for c in record["candidates"]] == [findings[0].finding_id]
    assert record["final"] is None
    summary = state.oracle_ledger.summary()
    assert summary is not None and summary["final"] == "not_reached"
