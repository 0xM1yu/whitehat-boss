"""채점기(oracle) 배선 — 후보마다 vul, 후보가 하나일 때만 지정해 그 하나에만 fix (#386).

러너(`_run_pov`·`_finalize_oracle`)를 `dev_fake` 문맥에서 직접 돌려 **후보 N개가 vul 제출 N건이 되고
fix 다리는 후보가 0개면 0번 · 1개면 정확히 한 번 · 2개 이상이면 0번 도는지**(셋 다 건다),
지정·실패·부재가 서로 다른 기록이 되는지, 판정이 trace의 재현성 기준을 안 깨는지를 본다. CLI 조립
가드·파서·`submit.sh` 신원은 `tests/test_oracle_cli.py`가 본다. 끝의 두 절은 마스킹 서버 몫이다 -
제출은 masked id, 조회는 실 id(`TaskOracle.query_task_id`)로 가는지와 그 실 id를 `--mask-map`에서
만드는 `cli._oracle_for`의 이음(여기만 `_oracle_for`를 직접 부른다 - `cli.run`은 안 돈다).

⚠ **후보가 둘 이상일 때 고르는 규칙은 정해지지 않았다**(`runner._designate_final`) - 이 파일에는
어느 후보를 고르는 것이 맞는지를 단언하는 검사가 없다. 후보가 둘 이상이면 **아무것도 지정하지
않는다**는 것만 건다.

⚠ **이 파일은 `run_pipeline`·CLI를 돌리지 않는다.** 운영 배선(`cli.run` → `execute` → `build_deps` →
`Deps.oracle` → `RunContext.oracle`)이 끊기지 않았는지는 `tests/test_pipeline_e2e.py`가 파이프라인과
CLI 전체를 돌려 본다 — 여기서 `ctx.oracle`을 직접 꽂는 검사는 그 배선을 못 지킨다.
"""

import argparse
import hashlib
from pathlib import Path

import pytest

from src import cli
from src.orchestrator import runner
from src.orchestrator.oracle_log import OracleLedger, OracleLog
from src.orchestrator.trace import EventKind
from src.schemas.enums import PovStopReason
from tests.oracle_fakes import TASK_ID, FakeOracle, submit_script
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


# --- 마스킹 서버 - 제출은 masked id, 조회는 실 id (`TaskOracle.query_task_id`) ----------------
# 마스킹 서버는 제출을 masked task_id로 받지만 DB에는 실 id를 적고 `/query-poc`는 마스크를
# 풀지 않는다(저자 소스 판독이고 실측하지 않았다, `cybergym_oracle.TaskOracle`). 어댑터의 서버
# 호출을 task_id만 적는 스텁으로 바꿔 **어느 호출이 어느 id로 갔는지**를 호출 순서째 본다.

_MASKED_ID = "9c73e92e52b7"  # `unsupported_task_id` docstring이 드는 마스킹 id 표본이다


def _record_server_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """어댑터 모듈의 서버 호출 넷을 `(호출 이름, 받은 task_id)`를 적는 스텁으로 바꾼다."""
    from src.adapters import cybergym_oracle as oc

    calls: list[tuple[str, str]] = []

    def submit(name: str):  # noqa: ANN202
        def stub(server: str, task_id: str, *a: object, **k: object) -> object:
            calls.append((name, task_id))
            return oc.SubmitResult(task_id=task_id, response_exit_code=0, output="", poc_id="p1")

        return stub

    def peek(server: str, task_id: str, *a: object, **k: object) -> None:
        calls.append(("peek_verdict", task_id))

    def query(server: str, task_id: str, *a: object, **k: object) -> object:
        calls.append(("query_verdict", task_id))
        return oc.OracleVerdict(
            task_id=task_id,
            agent_id="a",
            poc_id="p1",
            poc_hash="h",
            vul_exit_code=0,
            fix_exit_code=0,
            created_at=None,
            updated_at=None,
        )

    monkeypatch.setattr(oc, "submit_vul", submit("submit_vul"))
    monkeypatch.setattr(oc, "submit_fix", submit("submit_fix"))
    monkeypatch.setattr(oc, "peek_verdict", peek)
    monkeypatch.setattr(oc, "query_verdict", query)
    return calls


def test_task_oracle_uses_query_task_id_for_the_db_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """마스킹 서버에선 제출은 masked, 조회는 unmask된 id로 가야 한다 — DB가 unmask로 저장한다.
    조회는 둘이다: 제출 전 장부 읽기(`peek_verdict`)와 판정 조회(`query_verdict`) 모두
    `/query-poc`라 실 id로 간다 - 장부 읽기가 masked id로 가면(소스 판독) 기록이 없는 것으로
    읽혀 캐시 적중이 「아님」으로 적힌다. 제출 두 다리는 masked id 그대로다."""
    from src.adapters import cybergym_oracle as oc

    calls = _record_server_calls(monkeypatch)
    oracle = oc.TaskOracle(
        server="http://x",
        task_id=_MASKED_ID,
        agent_id="a",
        api_key="k",
        query_task_id=TASK_ID,
    )

    sent = oracle.submit_vul(b"xx")
    oracle.finalize(b"xx", sent)

    assert calls == [
        ("peek_verdict", TASK_ID),
        ("submit_vul", _MASKED_ID),
        ("peek_verdict", TASK_ID),
        ("submit_fix", _MASKED_ID),
        ("query_verdict", TASK_ID),
    ], "제출은 masked id, 조회는 실 id여야 한다(마스킹 서버 - 소스 판독이고 실측하지 않았다)"


def test_task_oracle_falls_back_to_submit_task_id_when_no_query_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`query_task_id`가 None이면 마스크 없이 띄운 서버다 — 모든 호출에 같은 id를 쓴다."""
    from src.adapters import cybergym_oracle as oc

    calls = _record_server_calls(monkeypatch)
    oracle = oc.TaskOracle(server="s", task_id=TASK_ID, agent_id="a", api_key="k")

    oracle.finalize(b"x", oracle.submit_vul(b"x"))

    assert [name for name, _ in calls] == [
        "peek_verdict",
        "submit_vul",
        "peek_verdict",
        "submit_fix",
        "query_verdict",
    ]
    assert {task_id for _, task_id in calls} == {TASK_ID}


def test_a_masked_task_id_is_accepted_only_with_a_query_task_id() -> None:
    """마스킹된 id는 조회에 쓸 실 id가 같이 올 때만 받는다 - 없으면 제출은 가도 판정 행을 못 읽는다.
    실 id가 오면 쓸 수 있는 구성이라 마스킹된 id를 통째로 거절하면 그 구성을 막는다. 빈 문자열은
    실 id가 아니다(`_query_task`가 `task_id`로 되돌린다). 문구는 메커니즘(제출 masked · DB 실 id ·
    `/query-poc`는 마스크를 안 푼다)이 소스 판독이라는 것만 말하고 실서버의 응답은 단언하지
    않는다."""
    from src.adapters import cybergym_oracle as oc

    fields = {"server": "http://x", "task_id": _MASKED_ID, "agent_id": "a", "api_key": "k"}
    accepted = oc.TaskOracle(**fields, query_task_id=TASK_ID)
    assert (accepted.task_id, accepted.query_task_id) == (_MASKED_ID, TASK_ID)

    for missing in (None, ""):
        with pytest.raises(ValueError, match="지원하는 모양이 아니다") as caught:
            oc.TaskOracle(**fields, query_task_id=missing)
        message = str(caught.value)
        assert "query_task_id" in message and "/query-poc" in message
        assert "실측하지 않았다" in message
        assert "404" not in message
        message.encode("cp949")  # 콘솔에 찍힐 수 있는 문장이다


# --- CLI `--mask-map` -> `query_task_id` ------------------------------------------------------

_SERVER = "http://127.0.0.1:8666"


def _bundle(tmp_path: Path, task_id: str) -> Path:
    task = tmp_path / "task"
    task.mkdir()
    (task / "submit.sh").write_text(
        submit_script(server=_SERVER, task_id=task_id), encoding="utf-8"
    )
    return task


def _write_mask_map(tmp_path: Path, content: object) -> Path:
    """저자 `mask_map.json`의 모양 `{실 id: masked id}`로 쓴다. 문자열·바이트는 그대로 쓴다."""
    import json

    path = tmp_path / "mask_map.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        text = content if isinstance(content, str) else json.dumps(content)
        path.write_text(text, encoding="utf-8")
    return path


def _args(task: Path, mask_map: Path | None) -> argparse.Namespace:
    return argparse.Namespace(
        oracle_submit=True, oracle_server=_SERVER, task_dir=task, mask_map=mask_map
    )


def test_the_parser_carries_both_oracle_flags_and_the_mask_map_has_no_default() -> None:
    """`--oracle-server`(조작자가 정한 주소)와 `--mask-map`(실 id를 주는 파일)이 둘 다 있다."""
    parse = cli.build_parser().parse_args
    base = ["run", "--profile", "dev_fake", "--target", "t"]

    off = parse(base)
    on = parse([*base, "--oracle-submit", "--oracle-server", _SERVER, "--mask-map", "m.json"])

    assert off.oracle_server is None and off.mask_map is None
    assert on.oracle_server == _SERVER and on.mask_map == Path("m.json")


def test_mask_map_unmasks_the_task_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--mask-map`이 있으면 CLI가 TaskOracle에 unmask된 query_task_id를 넘긴다. 마스킹된 id의
    번들이 거절되지 않고 만들어진다 - 모양 검사(`cli`)도 가드(`TaskOracle`)도 실 id가 오면
    받는다."""
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")
    args = _args(_bundle(tmp_path, _MASKED_ID), _write_mask_map(tmp_path, {TASK_ID: _MASKED_ID}))

    oracle = cli._oracle_for(args)

    assert oracle is not cli._ORACLE_ERROR and oracle is not None
    assert oracle.task_id == _MASKED_ID
    assert oracle.query_task_id == TASK_ID


def test_mask_map_missing_entry_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """맵에 번들의 masked id가 없으면 멈춘다 - 실 id를 지어내지 않는다."""
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")
    mask_map = _write_mask_map(tmp_path, {"arvo:9999": "deadbeef0000"})

    result = cli._oracle_for(_args(_bundle(tmp_path, _MASKED_ID), mask_map))

    err = capsys.readouterr().err
    assert result is cli._ORACLE_ERROR
    assert "[ERROR]" in err and "--mask-map" in err and _MASKED_ID in err
    err.encode("cp949")  # 콘솔에 찍히는 문장이다


@pytest.mark.parametrize(
    ("content", "needle"),
    [
        (None, "못 읽었다"),
        ("{not json", "못 읽었다"),
        (b"\xff\xfe{}", "못 읽었다"),
        ("[]", "JSON 객체가 아니다"),
        ("null", "JSON 객체가 아니다"),
    ],
    ids=["missing-file", "not-json", "not-utf8", "json-list", "json-null"],
)
def test_an_unusable_mask_map_is_an_error_not_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    content: str | bytes | None,
    needle: str,
) -> None:
    """못 읽는 파일(없음·깨진 JSON·UTF-8 아님)과 읽혔지만 `{실 id: masked id}` 객체가 아닌 JSON은
    다른 입력 오류와 같이 `[ERROR]` 한 줄로 멈춘다 - 예외 꼬리표(traceback)가 아니다."""
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")
    path = tmp_path / "absent.json" if content is None else _write_mask_map(tmp_path, content)

    result = cli._oracle_for(_args(_bundle(tmp_path, _MASKED_ID), path))

    err = capsys.readouterr().err
    assert result is cli._ORACLE_ERROR
    assert "[ERROR]" in err and "--mask-map" in err and needle in err
    err.encode("cp949")  # 콘솔에 찍히는 문장이다


def test_mask_map_without_oracle_submit_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`--oracle-server`와 같다 - 받아 놓고 아무 데도 안 쓰는 플래그는 「이 맵으로 조회한다」는
    인상만 준다. 제출을 안 켠 run에 맵을 주면 조용히 무시하지 않고 멈춘다."""
    args = argparse.Namespace(
        oracle_submit=False, oracle_server=None, task_dir=None, mask_map=tmp_path / "m.json"
    )

    result = cli._oracle_for(args)

    err = capsys.readouterr().err
    assert result is cli._ORACLE_ERROR
    assert "[ERROR]" in err and "--mask-map" in err and "--oracle-submit" in err
    err.encode("cp949")  # 콘솔에 찍히는 문장이다


def test_no_mask_map_leaves_query_task_id_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--mask-map`을 안 주면 마스크 없이 띄운 서버라는 뜻 — `query_task_id`가 `None`이다. 번들은
    실 id를 든다(마스크 없는 서버가 그렇게 쓴다) - 마스킹된 id의 번들은 맵이 없으면 거절이라 다음
    검사가 건다."""
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")

    oracle = cli._oracle_for(_args(_bundle(tmp_path, TASK_ID), None))

    assert oracle is not cli._ORACLE_ERROR and oracle is not None
    assert oracle.task_id == TASK_ID and oracle.query_task_id is None


def test_a_masked_task_id_without_a_mask_map_is_refused_before_anything_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """바로 위와 한 쌍이다 - 마스킹된 id에 실 id가 없으면 거절한다(제출은 가도 판정 행을 못 읽는다는
    것은 소스 판독이다). 문구가 말하는 것은 `--mask-map`을 같이 주라는 것이고 실서버에서 무슨 일이
    나는지는 단언하지 않는다. 오라클 객체는 만들어지지 않는다."""
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")
    built: list[object] = []
    monkeypatch.setattr(cli, "TaskOracle", lambda **kwargs: built.append(kwargs))

    result = cli._oracle_for(_args(_bundle(tmp_path, _MASKED_ID), None))

    err = capsys.readouterr().err
    assert result is cli._ORACLE_ERROR
    assert _MASKED_ID in err and "--mask-map" in err and "아무것도 제출하지 않았다" in err
    assert "404" not in err
    err.encode("cp949")  # 콘솔에 찍히는 문장이다
    assert built == []
