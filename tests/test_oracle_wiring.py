"""채점기(oracle) 배선 — 후보마다 vul, 지정한 하나에만 fix (#386).

러너(`_run_pov`·`_finalize_oracle`)를 `dev_fake` 문맥에서 직접 돌려 **후보 N개가 vul 제출 N건이 되고
fix 다리는 후보가 0개면 0번 · 1개 이상이면 정확히 한 번 도는지**(둘 다 건다),
지정·실패·부재가 서로 다른 기록이 되는지, 판정이 trace의 재현성 기준을 안 깨는지를 본다. CLI 조립
가드·파서·`submit.sh` 신원은 `tests/test_oracle_cli.py`가 본다. 끝의 두 절은 마스킹 서버 몫이다 -
제출은 masked id, 조회는 실 id(`TaskOracle.query_task_id`)로 가는지와 그 실 id를 `--mask-map`에서
만드는 `cli._oracle_for`의 이음(여기만 `_oracle_for`를 직접 부른다 - `cli.run`은 안 돈다).

**후보가 둘 이상일 때의 규칙은 ⏵ 2026-10-04에 사슬로 바뀌었다.** 2026-10-03 동결판은 「내부 근거
등급의 최댓값 · 동점이면 거부」였고 실측 run에서 후보 8건이 **같은 PoC 바이트 1개**라 그 거부가
점수를 0으로 만들었다. 지금은 같은 바이트끼리 **접고**(`sole_poc`) 남은 동점을 **전순서**로
가른다 — ①내부 근거 등급 ②`confidence` ③작은 바이트 ④PoC 해시(⏵ 2026-10-11 — 맨 앞에 Critic
판정 계단이 붙었다, #604. ⏵ 2026-10-14 — 그 앞에 ⓿후보 자기 `vul` 응답 코드 계단이 붙어 Critic은
둘째가 됐다, #637). `designation`에 **실제로 가른
단계**가 찍힌다. 등급·`confidence`·Critic 판정 중 모르는 값이 있는 후보가 있으면
여전히 거부다(`grade_unknown`).
끝 절의 검사들이 그
사슬과 **불변식**(지정 로직이 읽는 채점기 값은 후보 자기 `vul` 응답 종료 코드 하나뿐이다 —
⏵ 2026-10-14 이슈 #637, 앞 판은 하나도 안 읽었다)을 건다.

⚠ **위 `ctx` 문맥의 후보 N개는 근거가 없는 손수 만든 Finding이라 등급이 전부 E0이고 `confidence`도
같다**(도구가 안 돈다) - 그래서 후보가 둘 이상이면 사슬이 ③까지 내려가 **바이트가 작은 쪽**이
지정되고 `fix` 다리는 **정확히 한 번** 돈다(앞 판은 여기서 `grade_tie`로 거부해 0번이었다). 등급이
**갈리는** run(재현이 한쪽만 성공해 8단계 `gate_eval`이 E4 · E3으로 가른다)과 `dev_fake`의
동점(둘 다 E4)은 `tests/test_pipeline_e2e.py`가 파이프라인으로 문다.

⚠ **이 파일은 `run_pipeline`·CLI를 돌리지 않는다.** 운영 배선(`cli.run` → `execute` → `build_deps` →
`Deps.oracle` → `RunContext.oracle`)이 끊기지 않았는지는 `tests/test_pipeline_e2e.py`가 파이프라인과
CLI 전체를 돌려 본다 — 여기서 `ctx.oracle`을 직접 꽂는 검사는 그 배선을 못 지킨다.
"""

import argparse
import hashlib
import itertools
from pathlib import Path

import pytest

from src import cli
from src.interfaces.model_client import ModelGatewayError
from src.interfaces.oracle import OracleOutcome, VulSubmission
from src.orchestrator import gate, runner
from src.orchestrator import oracle_log as oracle_log_module
from src.orchestrator.budget import BudgetExhausted
from src.orchestrator.oracle_log import OracleLedger, OracleLog, VulAttempt, vul_ok
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind
from src.schemas import FindingState
from src.schemas.enums import EvidenceGrade, PovStopReason, ReasonCode, Verdict
from tests.oracle_fakes import TASK_ID, FakeOracle, submit_script
from tests.test_nodes import (  # noqa: F401
    _critic_decision,
    _demoted_at_validate,
    _finding,
    _outside_validated,
    _pov_state,
    _with_top_n,
    ctx,
)
from tests.test_pipeline_e2e import _run, _run_transition, _with_index
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


# --- 후보마다 vul · fix는 지정한 하나에만 -------------------------------------------------------


@pytest.mark.parametrize("count", [2, 3])
def test_several_candidates_all_get_the_vul_leg_and_the_fix_leg_runs_once_on_the_designated_one(
    ctx, monkeypatch, count  # noqa: ANN001, F811
) -> None:
    """차단 결함: 후보마다 vul·fix·판정을 다 내면 비최종 PoC에도 `-fix` 이미지가 돌고 trace에 판정이
    N건 남는다. 후보는 vul만 낸다 — `fix` 다리는 **지정한 하나에만 정확히 한 번**이다.

    ⏵ **2026-10-04 — 이 검사가 뒤집힌 자리다.** 앞 판은 이 문맥의 후보가 전부 E0 동점이라 「지정
    안 함 · `fix` 0번」을 물었다. 그 거부를 사슬로 바꿨으니 이제 지정된다 — 등급(E0)과
    `confidence`가 같아 사슬이 ③까지 내려가고 **바이트가 작은 쪽**(`_POCS[0]`, 7바이트)이
    최종이다. `designation`은 `smallest_poc`로, **무엇이 갈랐는지**를 말한다.

    ⚠ **자리로 고르는 구현도 `_POCS[0]`을 낸다**(그것이 첫째다) - 그래서 이 검사만으로는 위치
    규칙을 배제하지 못한다. 배제하는 것은 `test_the_smallest_poc_wins_even_when_it_came_last`와
    끝 절의 직접 호출 검사들이다. 바이트는 **내용으로** 본다."""
    state, findings = _findings(ctx, count)
    state.oracle_ledger = OracleLedger(wired=True)
    _stub_loop(monkeypatch, [(poc, _SUBMITTED) for poc in _POCS[:count]])
    oracle = FakeOracle()

    _drive(ctx, state, findings, oracle)

    assert oracle.vul_calls == _POCS[:count]
    assert len(oracle.final_calls) == 1, "fix 다리는 지정한 하나에만 정확히 한 번이다"
    assert oracle.final_calls[0][0] == _POCS[0], "가장 작은 PoC가 지정됐어야 한다"
    assert len(_events(ctx, EventKind.ORACLE_SUBMITTED)) == count
    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.subject_kind == "finding" and final.subject_id == findings[0].finding_id
    assert final.data == {
        "status": "scored",
        "designation": "smallest_poc",
        "reason": None,
        "poc_sha256": hashlib.sha256(_POCS[0]).hexdigest(),
        "poc_bytes": len(_POCS[0]),
        "rescued": False,  # ⏵ 2026-10-15 - 제출된 후보라 구제가 아니다
        "task_id": TASK_ID,
        "error": None,
        "record_ref": None,  # 이 문맥에는 사이드카 쓰는 객체가 없다
    }
    summary = state.oracle_ledger.summary()
    assert summary is not None
    assert (summary["final"], summary["final_reason"]) == ("scored", None)
    assert not any("동점" in w for w in state.oracle_ledger.warnings())


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
        "rescued": False,  # ⏵ 2026-10-15 - 제출된 후보라 구제가 아니다
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
            "designatable": True,
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
    """실제 루프는 `MAX_HOP`·`REPEAT_SPEC`·`PARSE_FAILED`에서 마지막 `gen`의 부분 PoC를 든다.
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
    pov_hops = ctx.config.thresholds.pov_tool_hops

    scripted, oracle = _real_loop_partial(ctx, [_gen(count=n) for n in range(1, pov_hops + 5)])

    _skipped_with_partial_poc(scripted, PovStopReason.MAX_HOP)
    assert oracle.vul_calls == []


def test_the_real_loop_stopped_by_a_repeated_poc_does_not_send_its_partial_poc(ctx) -> None:  # noqa: ANN001, F811
    scripted, oracle = _real_loop_partial(ctx, [_gen(count=8), _gen(count=8)])

    _skipped_with_partial_poc(scripted, PovStopReason.REPEAT_SPEC)
    assert oracle.vul_calls == []


def test_the_real_loop_stopped_by_parse_failures_to_the_cap_sends_no_partial_poc(ctx) -> None:  # noqa: ANN001, F811
    from src.interfaces.model_client import SchemaParseError

    # ⏵ 2026-10-11 (#550) — 파싱 실패는 홉을 쓰고 루프가 계속 돈다. 스크립트는 마지막 항목을 반복해
    # 매 홉 실패해 **마지막 홉**에서 PARSE_FAILED로 선다(부분 PoC는 그때까지의 마지막 gen).
    scripted, oracle = _real_loop_partial(ctx, [_gen(count=8), SchemaParseError("bad at hop1")])

    _skipped_with_partial_poc(scripted, PovStopReason.PARSE_FAILED)
    assert oracle.vul_calls == []


# --- 실패는 흡수하고 사유를 남긴다 · 지정된 후보가 없는 것은 실패가 아니다 ----------------------


def test_a_failed_vul_submission_is_absorbed_and_recorded_and_the_others_still_go(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """셋째 후보의 vul 제출이 실패해도 run은 간다. 실패는 사유와 함께 남는다 - trace에는 예외 클래스
    이름만, 문구(URL·로케일 errno)는 사이드카용 장부에만. 남은 후보 둘에서 **작은 바이트**가
    지정된다(⏵ 2026-10-04 - 앞 판은 「남은 후보가 둘이라 지정은 안 된다」였다)."""
    state, findings = _findings(ctx, 3)
    _stub_loop(monkeypatch, [(poc, _SUBMITTED) for poc in _POCS])
    oracle = FakeOracle(vul_errors={2: RuntimeError("server refused http://10.0.0.1")})

    _drive(ctx, state, findings, oracle)  # 예외가 새지 않는다

    assert oracle.vul_calls == _POCS
    (failed,) = _events(ctx, EventKind.ORACLE_FAILED)
    assert failed.subject_id == findings[2].finding_id
    assert failed.data == {
        "leg": "vul",
        "designatable": True,
        "poc_sha256": hashlib.sha256(_POCS[2]).hexdigest(),
        "poc_bytes": len(_POCS[2]),
        "error": "RuntimeError",
    }, "문구(URL·로케일 errno)는 trace에 없다"
    assert state.oracle_ledger.attempts[2].error_detail == "server refused http://10.0.0.1"
    # 실패한 셋째는 후보가 아니다 - 남은 둘(7·13바이트) 중 작은 쪽이 최종이다.
    assert [poc for poc, _ in oracle.final_calls] == [_POCS[0]]


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
    """「최종 PoC가 없다」의 사유 둘과 「최종 PoC가 실패했다」, 점수가 난 run 둘이 모두 **서로
    다른** 기록이다 (§0 - 같은 모양이면 없음이 실패로, 실패가 없음으로 읽힌다). 사건마다 run을 돌려
    (status, reason, error, designation)을 모은다.

    ⏵ **2026-10-04 — `several`이 `none`에서 `scored`로 옮겼다.** 후보 둘이 E0 동점이라 앞 판은
    `grade_tie`로 거부했고, 지금은 사슬이 작은 바이트로 가른다(`smallest_poc`). 그래서 이 표의
    「점수 없이 끝나는 길」은 **둘**이고(`no_poc`·`all_vul_submissions_failed`) 나머지 사유
    `grade_unknown`은 현 러너로 닿지 않는 방어 분기다(끝 절의 직접 호출 검사가 문다)."""
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
        "several": ("scored", None, None, "smallest_poc"),
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
    """8단계가 최종 단계 전에 끊기면(`_finalize_oracle`에 못 온다) 그때까지 나간 vul 제출은
    사이드카에 남고 `final`은 `null`이다 - 「최종 없음」과 「아직 안 갔다」가 다르다.
    (`BudgetExhausted`·`ModelGatewayError`로 끊긴 run은 낸 PoC가 있으면 최종 지정까지 하고
    끝나므로 이 말이 안 닿는다 - 아래 `test_a_budget_stop_inside_the_extra_…`·
    `test_a_stop_inside_the_validated_…`.)"""
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


# --- 예산·게이트웨이 오류로 PoV 루프가 끊겨도 이미 낸 PoC의 최종 지정은 간다 --------------------
#
# 추가 대상(`pov_attempt_top_n`)은 `validated` 뒤에 붙어 같은 루프에서 돈다.
# `BudgetExhausted`와 서버 쪽 `ModelGatewayError`는 흡수하지 않고 올려 보내는 계약이라
# (`_run_pov_hops`) 그것이 `_finalize_oracle`을 건너뛰게 하면, **앞 대상이 이미 낸 PoC의 최종
# 지정(`fix` 다리·판정 조회)까지 잃는다** - 상한 0이었으면 점수가 났을 run이다(적대적 리뷰 #490
# MEDIUM-1 · 이슈 #532). 낸 PoC 없이 `validated` 대상에서 끊긴 run은 종전 그대로다.


def _validated_and_one_extra(ctx):  # noqa: ANN001, ANN202, F811
    """`validated` 1건(`find_0001`) + 그 밖 1건(`find_0002`)에 상한 1 - `_analyze`가 둘 다
    `_run_pov`로 부른다. 채점기 장부를 켠 상태다."""
    state, validated = _demoted_at_validate(ctx)
    outside, (extra,) = _outside_validated(ctx, [("find_0002", 0.9)])
    state.update(extra)
    state.finding_capsules.update(outside.finding_capsules)
    _with_top_n(ctx, 1)
    state.oracle_ledger = OracleLedger(wired=True)
    return state, validated, extra


def _pov_loop_that_runs_out_of_budget_at(monkeypatch, stop_at, error=None):  # noqa: ANN001, ANN202
    """`_run_pov_hops`가 `stop_at`번째(0부터) 호출에서 `BudgetExhausted`(또는 `error`)를 낸다 -
    그 앞 호출은 PoC를 내고 제출까지 간다. 반환은 호출된 finding_id 목록이다(어디까지 갔는지
    본다)."""
    reached: list[str] = []

    def loop(ctx_, state_, finding, capsule):  # noqa: ANN001, ANN202
        index = len(reached)
        reached.append(finding.finding_id)
        if index == stop_at:
            raise error or BudgetExhausted(int(Stage.VALIDATE), "input_tokens", 200, 100)
        return _POCS[index], _SUBMITTED

    monkeypatch.setattr(runner, "_run_pov_hops", loop)
    return reached


# 이슈 #532가 말한 모양(5xx·401·429)과 상태 없음 - 넷 다 `_is_droppable`이 거짓이라 PoV 루프가
# 흡수하지 않고 올린다. `status`로 갈라 구하는 변이(상태가 있으면 건너뛴다)는 `None` 하나만으로는
# 안 잡힌다(적대적 리뷰가 그 변이를 넣었더니 483건이 전부 초록이었다).
_GATEWAY_STATUSES = [None, 503, 401, 429]


def _gateway_error(status):  # noqa: ANN001, ANN202
    """이 루프까지 올라오는 서버 쪽 `ModelGatewayError` - 전제(흡수 안 됨)를 여기서 단언한다."""
    error = ModelGatewayError("down", status=status)
    assert runner._pov_classify_exception(error) is None, "흡수되는 오류다 - 이 루프에 안 온다"
    return error


def test_a_budget_stop_inside_the_extra_pov_targets_still_designates_the_final(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """`validated` 대상이 PoC를 내 vul 제출까지 성공한 뒤 **추가 대상**에서 예산이 소진돼도 최종
    지정이 돈다 - `fix` 다리·판정 조회가 정확히 한 번이고 `oracle.final`이 `not_reached`가 아니다.
    `BudgetExhausted`는 그대로 올라간다(`run_pipeline`이 그것으로 `stopped_after`와 사유를
    적는다)."""
    state, validated, extra = _validated_and_one_extra(ctx)
    reached = _pov_loop_that_runs_out_of_budget_at(monkeypatch, 1)
    ctx.oracle = oracle = FakeOracle()

    with pytest.raises(BudgetExhausted):
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [validated.finding_id, extra.finding_id], "추가 대상에 못 닿았다 - 공허하다"
    assert oracle.vul_calls == [_POCS[0]]
    assert oracle.final_calls == [(_POCS[0], oracle.vul_results[0])]
    summary = state.oracle_ledger.summary()
    assert summary is not None
    assert (summary["final"], summary["final_reason"]) == ("scored", None)
    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.subject_id == validated.finding_id
    assert final.data["designation"] == "sole_candidate"


def test_a_budget_stop_inside_the_validated_targets_leaves_the_final_not_reached(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """**낸 PoC 없이 `validated` 대상에서 끊긴 run은 안 바뀌었다** - 첫 대상에서 끊기면 후보가
    0건이고, 최종 단계는 안 불려 매니페스트 요약이 `not_reached`로 남는다. 조건이 후보 유무를 안
    보고 `validated` 대상까지 넓어지는 변이를 이 검사가 문다."""
    state, validated, _extra = _validated_and_one_extra(ctx)
    reached = _pov_loop_that_runs_out_of_budget_at(monkeypatch, 0)
    ctx.oracle = oracle = FakeOracle()

    with pytest.raises(BudgetExhausted):
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [validated.finding_id]
    assert oracle.final_calls == [] and not _events(ctx, EventKind.ORACLE_FINAL)
    summary = state.oracle_ledger.summary()
    assert summary is not None and summary["final"] == "not_reached"


@pytest.mark.parametrize("status", _GATEWAY_STATUSES, ids=str)
def test_a_gateway_error_inside_the_extra_pov_targets_still_designates_the_final(
    ctx, monkeypatch, status  # noqa: ANN001, F811
) -> None:
    """서버 쪽 `ModelGatewayError`(`_run_pov_hops`가 흡수하지 않고 올린다)가 추가 대상에서 나도 앞
    대상이 이미 낸 PoC의 최종 지정이 돈다 - `fix` 다리·판정 조회가 정확히 한 번이고
    `oracle.final`이 `not_reached`가 아니다(이슈 #532 - 앞 판은 이것을 `not_reached`로 고정한
    핀이었다). 예외는 그대로 올라간다(`run_pipeline`의 게이트웨이 갈래가 사유를 적는다).
    `status`는 상태 없음·5xx·401·429 넷이다 - 어느 것이든 같다."""
    state, validated, extra = _validated_and_one_extra(ctx)
    reached = _pov_loop_that_runs_out_of_budget_at(monkeypatch, 1, error=_gateway_error(status))
    ctx.oracle = oracle = FakeOracle()

    with pytest.raises(ModelGatewayError):
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [validated.finding_id, extra.finding_id], "추가 대상에 못 닿았다 - 공허하다"
    assert oracle.vul_calls == [_POCS[0]]
    assert oracle.final_calls == [(_POCS[0], oracle.vul_results[0])]
    summary = state.oracle_ledger.summary()
    assert summary is not None
    assert (summary["final"], summary["final_reason"]) == ("scored", None)
    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.subject_id == validated.finding_id


def _two_validated(ctx):  # noqa: ANN001, ANN202, F811
    """`validated` 2건(`find_0001`·`find_0002`)에 추가 대상 없음(상한 0). 채점기 장부를 켠 것."""
    state, first = _demoted_at_validate(ctx)
    second = _finding("find_0002", state=FindingState.VALIDATED, signal="로그에 WARN이 찍힌다")
    state.update(second)
    state.finding_capsules[second.finding_id] = state.finding_capsules[first.finding_id]
    _with_top_n(ctx, 0)
    state.oracle_ledger = OracleLedger(wired=True)
    return state, first, second


@pytest.mark.parametrize("status", ["budget", *_GATEWAY_STATUSES], ids=str)
def test_a_stop_inside_the_validated_targets_designates_what_an_earlier_target_submitted(
    ctx, monkeypatch, status  # noqa: ANN001, F811
) -> None:
    """**막는 것은 「어느 대상에서 끊겼나」가 아니라 「이미 낸 PoC가 있는데 지정이 안 돈다」다.**
    `validated` 둘째 대상에서 끊겨도 첫째가 이미 vul 제출을 성공했으면 최종 지정이 돈다(이슈
    #532 - 앞 조건은 `validated` 대상이면 앞 제출과 무관하게 건너뛰었다). 첫 대상에서 끊긴
    run(낸 PoC 없음)은 위 `…leaves_the_final_not_reached`가 문다. `status`는 예산과 게이트웨이
    넷(상태 없음·5xx·401·429)이다 - 상태로 갈라 구하는 변이를 이 자리에서도 문다."""
    budget = status == "budget"
    state, first, second = _two_validated(ctx)
    reached = _pov_loop_that_runs_out_of_budget_at(
        monkeypatch, 1, error=None if budget else _gateway_error(status)
    )
    ctx.oracle = oracle = FakeOracle()

    with pytest.raises(BudgetExhausted if budget else ModelGatewayError):
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [first.finding_id, second.finding_id], "둘째 대상에 못 닿았다 - 공허하다"
    assert oracle.vul_calls == [_POCS[0]]
    assert oracle.final_calls == [(_POCS[0], oracle.vul_results[0])]
    summary = state.oracle_ledger.summary()
    assert summary is not None
    assert (summary["final"], summary["final_reason"]) == ("scored", None)


@pytest.mark.parametrize("stop", ["budget", "gateway"])
def test_a_final_designation_that_raises_does_not_hide_the_stop_that_cut_the_loop(
    ctx, monkeypatch, stop  # noqa: ANN001, F811
) -> None:
    """`_finalize_oracle` 자신이 던져도 run을 끊은 **원 예외**가 올라온다 - 그 예외로
    `run_pipeline`이 사유를 적는다. 새 예외는 원 예외의 `__notes__`로 붙고, 그 노트가 run
    경고에 실리는 것은 아래 `test_a_final_designation_failure_reaches_the_run_warnings`가 문다.
    ⚠ 그 함수가 던질 실경로는 못 찾았다(미확인) - 이 검사는 `_finalize_oracle`을 던지게 바꿔
    치워 **그 자리의 처리**만 문다."""
    original = (
        BudgetExhausted(int(Stage.VALIDATE), "input_tokens", 200, 100)
        if stop == "budget"
        else ModelGatewayError("down", status=None)
    )
    state, _validated, extra = _validated_and_one_extra(ctx)
    reached = _pov_loop_that_runs_out_of_budget_at(monkeypatch, 1, error=original)
    ctx.oracle = FakeOracle()

    def broken(ctx_, state_):  # noqa: ANN001, ANN202
        raise RuntimeError("장부 쓰기 실패")

    monkeypatch.setattr(runner, "_finalize_oracle", broken)

    with pytest.raises((BudgetExhausted, ModelGatewayError)) as caught:
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached[-1] == extra.finding_id, "추가 대상에 못 닿았다 - 공허하다"
    assert caught.value is original, f"원 예외가 가려졌다: {caught.value!r}"
    notes = getattr(caught.value, "__notes__", [])
    assert any("RuntimeError: 장부 쓰기 실패" in note for note in notes), notes


@pytest.mark.parametrize(
    ("stop", "reason", "headline"),
    [
        ("budget", ReasonCode.BUDGET_EXHAUSTED, "8단계에서 예산 소진"),
        ("gateway", ReasonCode.MODEL_GATEWAY_UNAVAILABLE, "게이트웨이 실패로 중단했다"),
    ],
)
def test_a_final_designation_failure_reaches_the_run_warnings(
    tmp_path: Path, monkeypatch, stop, reason, headline  # noqa: ANN001
) -> None:
    """**`add_note`로 붙은 사유를 `run_pipeline`이 읽어 `RunResult.warnings`에 싣는다.** 이 갈래는
    원 예외를 삼키므로(`run_pipeline`의 예산·게이트웨이 `except`) 노트를 안 읽으면 최종 지정이 왜
    안 됐는지가 산출물 어디에도 안 남는다 - 노트가 트레이스백에 찍히는 매니페스트 실패와 다르다.
    위 `…does_not_hide…`는 `_analyze`까지만 보므로 그 뒤 갈래를 `cli.execute` 경유로 돌린다.
    ⚠ `_finalize_oracle`이 던질 실경로는 못 찾았다(미확인) - 던지게 바꿔 치워 **읽는 쪽**만 문다."""
    reached: list[str] = []

    def loop(ctx_, state_, finding, capsule):  # noqa: ANN001, ANN202
        reached.append(finding.finding_id)
        if len(reached) == 2:  # 추가 대상에서 끊긴다 - 앞 대상은 PoC를 냈다
            raise (
                BudgetExhausted(int(Stage.VALIDATE), "input_tokens", 200, 100)
                if stop == "budget"
                else ModelGatewayError("down", status=None)
            )
        return _POCS[0], _SUBMITTED

    def broken(ctx_, state_):  # noqa: ANN001, ANN202
        raise RuntimeError("장부 쓰기 실패")

    monkeypatch.setattr(runner, "_run_pov_hops", loop)
    monkeypatch.setattr(runner, "_finalize_oracle", broken)

    result, trace_path = _run(
        tmp_path,
        before=_with_index(),
        critic_top_n=1,
        configure=lambda c: c.transition(thresholds=c.thresholds.transition(pov_attempt_top_n=1)),
        oracle=FakeOracle(),
    )

    assert len(reached) == 2, "추가 대상에 못 닿았다 - 이 검사의 전제가 깨졌다"
    assert _run_transition(trace_path)["data"]["reason"] == reason.value
    assert any(headline in w for w in result.warnings), result.warnings
    assert any(
        "최종 지정을 돌리지 못했다: RuntimeError: 장부 쓰기 실패" in w for w in result.warnings
    ), result.warnings


def test_a_non_target_exception_after_a_submitted_poc_still_runs_the_final_designation(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """**낸 PoC가 있으면 예외 종류를 가리지 않고 최종 지정이 돈다** (2026-10-15 팀 승인 - 전엔
    이 자리가 「그 밖의 예외는 안 덮는다」의 핀이었고 `except Exception`으로 넓히는 변이를 물었다).
    앞 대상이 PoC를 냈고(후보 1건) 추가 대상에서 계약 밖 예외가 난다 - `fix` 다리·판정 조회가
    정확히 한 번 돌고 `oracle.final`이 `scored`이며, **원 예외가 그대로 올라가고** 노트가 붙는다.
    그 뒤 다시 불러도 둘째 지정은 안 돈다."""
    state, validated, extra = _validated_and_one_extra(ctx)
    reached = _pov_loop_that_runs_out_of_budget_at(
        monkeypatch, 1, error=RuntimeError("계약 밖 예외")
    )
    ctx.oracle = oracle = FakeOracle()

    with pytest.raises(RuntimeError, match="계약 밖 예외") as raised:
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [validated.finding_id, extra.finding_id], "추가 대상에 못 닿았다 - 공허하다"
    assert oracle.vul_calls == [_POCS[0]]
    assert oracle.final_calls == [(_POCS[0], oracle.vul_results[0])]
    summary = state.oracle_ledger.summary()
    assert summary is not None
    assert (summary["final"], summary["final_reason"]) == ("scored", None)
    assert type(raised.value) is RuntimeError, "원 예외가 그대로 올라간다"
    assert any("최종 지정을 돌렸다" in note for note in raised.value.__notes__)
    runner._finalize_oracle(ctx, state)  # 둘째 호출 - 표지가 막는다
    assert len(oracle.final_calls) == 1
    assert len(_events(ctx, EventKind.ORACLE_FINAL)) == 1


def test_a_non_target_exception_without_a_submitted_poc_leaves_the_final_not_reached(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """**후보가 0건이면 종전 그대로다** - 첫 `validated` 대상에서 계약 밖 예외가 나면 최종 지정은
    안 불리고 `oracle.final`은 `not_reached`다. 조건이 후보 유무를 안 보고 예외 종류만 보는
    변이(`except Exception`이 무조건 부른다)를 문다."""
    state, validated, _extra = _validated_and_one_extra(ctx)
    reached = _pov_loop_that_runs_out_of_budget_at(
        monkeypatch, 0, error=RuntimeError("계약 밖 예외")
    )
    ctx.oracle = oracle = FakeOracle()

    with pytest.raises(RuntimeError, match="계약 밖 예외"):
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [validated.finding_id]
    assert oracle.final_calls == [] and not _events(ctx, EventKind.ORACLE_FINAL)
    summary = state.oracle_ledger.summary()
    assert summary is not None and summary["final"] == "not_reached"


def test_no_oracle_wired_means_no_final_designation_note_on_a_cut_extra_target(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """채점기가 없으면 `_finalize_oracle`은 조용히 돌아온다 - 그런데도 「최종 지정을 돌렸다」 노트가
    붙으면 안 돈 지정이 돈 것으로 읽힌다(2026-10-15 리뷰). 추가 대상에서 예산이 끊긴다 - 조건은
    참이지만 지정은 안 돈다."""
    state, _validated, extra = _validated_and_one_extra(ctx)
    reached = _pov_loop_that_runs_out_of_budget_at(monkeypatch, 1)
    assert ctx.oracle is None, "전제 - 채점기가 없다"

    with pytest.raises(BudgetExhausted) as raised:
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached[-1] == extra.finding_id, "추가 대상에 못 닿았다 - 공허하다"
    assert not getattr(raised.value, "__notes__", [])


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit], ids=lambda e: e.__name__)
def test_an_interrupt_in_the_pov_loop_never_runs_the_final_designation(
    ctx, monkeypatch, interrupt  # noqa: ANN001, F811
) -> None:
    """**`BaseException`은 최종 지정을 안 부른다** - 후보가 있어도(앞 대상이 PoC를 냈다) 중단
    신호에 채점기 네트워크 호출이 돌면 안 된다. `except Exception`을 `except BaseException`으로
    넓히는 변이를 문다."""
    state, validated, extra = _validated_and_one_extra(ctx)
    reached = _pov_loop_that_runs_out_of_budget_at(monkeypatch, 1, error=interrupt())
    ctx.oracle = oracle = FakeOracle()

    with pytest.raises(interrupt):
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [validated.finding_id, extra.finding_id], "추가 대상에 못 닿았다 - 공허하다"
    assert oracle.vul_calls == [_POCS[0]], "전제 - 후보가 있다"
    assert oracle.final_calls == [] and not _events(ctx, EventKind.ORACLE_FINAL)
    summary = state.oracle_ledger.summary()
    assert summary is not None and summary["final"] == "not_reached"


def test_a_final_designation_that_raises_does_not_replace_the_original_exception(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """최종 지정이 던져도 **원 예외가 올라간다** - 던진 사유는 원 예외의 노트로 붙는다."""
    state, _validated, _extra = _validated_and_one_extra(ctx)
    _pov_loop_that_runs_out_of_budget_at(monkeypatch, 1, error=RuntimeError("계약 밖 예외"))
    ctx.oracle = FakeOracle()
    calls: list[str] = []

    def broken(ctx_, state_):  # noqa: ANN001, ANN202
        calls.append("finalize")
        raise OSError("장부 쓰기 실패")

    monkeypatch.setattr(runner, "_finalize_oracle", broken)

    with pytest.raises(RuntimeError, match="계약 밖 예외") as raised:
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert calls == ["finalize"], "한 번만 시도한다"
    assert any(
        "최종 지정을 돌리지 못했다: OSError: 장부 쓰기 실패" in note
        for note in raised.value.__notes__
    )


def test_the_final_designation_runs_once_per_run(ctx) -> None:  # noqa: ANN001, F811
    """`_finalize_oracle`은 표지를 **네트워크 호출 전에** 세운다 - 두 번째 호출은 아무것도 안 한다.
    PoV 루프의 `except`와 8단계 끝의 정상 호출이 겹쳐도 지정이 둘이 되지 않는 근거다."""
    state, _validated, _extra = _validated_and_one_extra(ctx)
    ctx.oracle = oracle = FakeOracle()

    runner._finalize_oracle(ctx, state)
    assert state.oracle_finalized is True
    runner._finalize_oracle(ctx, state)

    assert len(_events(ctx, EventKind.ORACLE_FINAL)) == 1
    assert oracle.final_calls == []  # 후보 0건 - `status=none` 한 건뿐이다


def test_an_intermediate_gen_submission_alone_does_not_make_a_validated_stop_designate(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """**「낸 PoC」는 모델이 submit하고 vul 제출이 성공한 것(`candidates`)이다 - 제출 전부
    (`submitted`)가 아니다.** `validated` 대상이 중간 gen(`designatable=False`)만 vul에 내고
    끊기면 최종 후보는 0건이라 최종 지정이 안 돈다 - 중간 gen은 모델 입력으로만 쓰이고 정렬 키에
    안 닿는다(`VulAttempt.designatable`). `candidates`를 `submitted`로 바꾸는 변이는 여기서
    `none`/`no_poc`를 만들어 `not_reached`를 가린다. 중간 gen은 실제 제출 경로
    (`_submit_vul_to_oracle`)로 낸다.
    ⏵ 2026-10-15 - 응답 코드가 **크래시 계단이 아닌** 중간 gen이다(코드 0). 크래시 계단이면 구제돼
    풀에 들므로 최종 지정이 돈다(`tests/test_oracle_rescue.py`)."""
    state, first, _second = _two_validated(ctx)
    ctx.oracle = oracle = FakeOracle(vul_exit_code=0)
    reached: list[str] = []

    def loop(ctx_, state_, finding, capsule):  # noqa: ANN001, ANN202
        reached.append(finding.finding_id)
        runner._submit_vul_to_oracle(
            ctx_, state_, finding, _POCS[0], designatable=False, in_validate_span=False
        )
        raise BudgetExhausted(int(Stage.VALIDATE), "input_tokens", 200, 100)

    monkeypatch.setattr(runner, "_run_pov_hops", loop)

    with pytest.raises(BudgetExhausted):
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [first.finding_id]
    assert oracle.vul_calls == [_POCS[0]], "중간 gen이 제출되지 않았다 - 공허하다"
    ledger = state.oracle_ledger
    assert ledger.submitted == 1 and ledger.candidates == []
    assert oracle.final_calls == [] and not _events(ctx, EventKind.ORACLE_FINAL)
    summary = ledger.summary()
    assert summary is not None and summary["final"] == "not_reached"


def test_a_droppable_gateway_error_is_absorbed_by_the_pov_loop_and_never_reaches_the_stop_branch(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """**`except`를 `ModelGatewayError`로 넓힌 것이 `_pov_classify_exception`의 계약을 안 바꿨다는
    핀이다.** 버릴 수 있는 게이트웨이 오류(422 - 요청 하나에 대한 거절)는 PoV 루프가 종전대로
    그 대상 하나만 건너뛰고 흡수한다 - `_analyze`가 올리지 않고 끝까지 돌아 최종 지정이 **정상
    경로의 한 번**만 돈다. 올렸다면 `pytest.raises` 없는 이 호출이 터진다."""
    state, validated, extra = _validated_and_one_extra(ctx)
    real_hops = runner._run_pov_hops
    reached: list[str] = []

    def loop(ctx_, state_, finding, capsule):  # noqa: ANN001, ANN202
        reached.append(finding.finding_id)
        if len(reached) == 1:
            return _POCS[0], _SUBMITTED  # 첫 대상은 PoC를 내고 제출한다
        return real_hops(ctx_, state_, finding, capsule)  # 둘째는 실제 루프 - 홉 호출이 거절된다

    def rejected(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise ModelGatewayError("droppable", status=422)

    monkeypatch.setattr(runner, "_run_pov_hops", loop)
    monkeypatch.setattr(runner.pov, "open_hop", rejected)
    ctx.oracle = oracle = FakeOracle()

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [validated.finding_id, extra.finding_id], "추가 대상에 못 닿았다 - 공허하다"
    assert state.pov_results[extra.finding_id] == PovStopReason.GATEWAY_ERROR.value
    assert oracle.final_calls == [(_POCS[0], oracle.vul_results[0])]
    assert len(_events(ctx, EventKind.ORACLE_FINAL)) == 1


@pytest.mark.parametrize("status", ["budget", None], ids=["budget", "gateway"])
def test_a_stop_before_any_candidate_says_none_no_poc_not_not_reached(
    ctx, monkeypatch, status  # noqa: ANN001, F811
) -> None:
    """후보가 0건인 채 추가 대상에서 끊기면 `not_reached`가 아니라 `none`/`no_poc`다 - 의도한 것이
    아니라 `_finalize_oracle` 호출의 부수 효과다(후보 없음은 `_designate_final`이 `no_poc`로
    싣는다). 「PoV가 끝까지 돌아 submit을 못 냈다」와 「예산·게이트웨이가 PoV를 끊었다」를 오라클
    요약은 못 가르고 후자는 run 수준의 `stopped_after`·경고가 말한다 - 그 사실의 핀이다.
    **예산은 #532 앞에도 이랬고(종전대로) 게이트웨이는 #532에서 새로 이렇게 됐다** - 앞 판은 후보
    0건의 게이트웨이를 `not_reached`로 남겼다. 게이트웨이 행이 없으면 그 쪽 조건(`validated` 밖이면
    후보와 무관하게 돈다)을 어느 검사도 안 문다."""
    budget = status == "budget"
    state, (extra,) = _outside_validated(ctx, [("find_0001", 0.9)])
    _with_top_n(ctx, 1)
    state.oracle_ledger = OracleLedger(wired=True)
    reached = _pov_loop_that_runs_out_of_budget_at(
        monkeypatch, 0, error=None if budget else _gateway_error(status)
    )
    ctx.oracle = oracle = FakeOracle()

    with pytest.raises(BudgetExhausted if budget else ModelGatewayError):
        runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert reached == [extra.finding_id], "추가 대상에 못 닿았다 - 공허하다"
    assert oracle.vul_calls == [] and oracle.final_calls == []
    summary = state.oracle_ledger.summary()
    assert summary is not None
    assert (summary["final"], summary["final_reason"]) == ("none", "no_poc")


def test_a_run_cut_by_the_budget_inside_the_extra_pov_targets_keeps_its_stop_and_still_scores(
    tmp_path: Path, monkeypatch  # noqa: ANN001
) -> None:
    """**`run_pipeline` 갈래를 `cli.execute` 경유로 문다** - `_analyze`의 `except`가 같은 예외를
    다시 올려 `run_pipeline`의 `BudgetExhausted` 갈래가 종전대로 `stopped_after`(8)·R-2
    `budget_exhausted`·경고를 적는지, 그러면서 `oracle.final`이 `scored`인지. 위 `_analyze` 검사들은
    그 뒤 갈래를 안 지난다.

    `before=_with_index()`가 후보를 둘로 만들고 `critic_top_n=1`이 그중 하나를 `validated` 밖에
    남긴다(`dev_fake`의 Fake Critic은 올라간 것을 전부 지지한다)."""
    reached: list[str] = []

    def loop(ctx_, state_, finding, capsule):  # noqa: ANN001, ANN202
        reached.append(finding.finding_id)
        if len(reached) == 2:
            raise BudgetExhausted(int(Stage.VALIDATE), "input_tokens", 200, 100)
        return _POCS[0], _SUBMITTED

    monkeypatch.setattr(runner, "_run_pov_hops", loop)
    oracle = FakeOracle()

    result, trace_path = _run(
        tmp_path,
        before=_with_index(),
        critic_top_n=1,
        configure=lambda c: c.transition(thresholds=c.thresholds.transition(pov_attempt_top_n=1)),
        oracle=oracle,
    )

    assert len(reached) == 2, "추가 대상에 못 닿았다 - 이 검사의 전제가 깨졌다"
    assert result.record.stopped_after == int(Stage.VALIDATE)
    assert _run_transition(trace_path)["data"]["reason"] == "budget_exhausted"
    assert any("8단계에서 예산 소진" in w for w in result.warnings)
    assert result.record.oracle is not None and result.record.oracle["final"] == "scored"
    assert len(oracle.final_calls) == 1


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


def _bundle(tmp_path: Path, task_id: str, canary_task: str | None = None) -> Path:
    task = tmp_path / "task"
    task.mkdir()
    (task / "submit.sh").write_text(
        submit_script(server=_SERVER, task_id=task_id), encoding="utf-8"
    )
    # 채점 run은 카나리 보고서 없이 조립되지 않는다(이슈 #383). 기본은 번들의 task_id로 쓴다.
    # ⚠ **마스킹 번들에서는 그 둘이 다르다** - 카나리 도구는 이미지 이름을 과제 id에서 뽑으므로
    # 조작자가 **실 id**로 돌리고 보고서의 `task`도 실 id다. 그래서 `canary_task`를 따로 받는다.
    # 거절 규칙 자체는 `tests/test_canary_report.py`·`tests/test_oracle_cli.py`가 든다.
    _write_canary(tmp_path, canary_task or task_id)
    return task


def _write_canary(tmp_path: Path, task_id: str) -> Path:
    import json

    path = tmp_path / "canary.json"
    path.write_text(
        json.dumps(
            {
                "task": task_id,
                "verdict": "pass",
                "ok": True,
                "repeat": 10,
                "below_measured_repeat": False,
                "mmap_rnd_bits": "28",
                "rnd_bits_above_28": False,
                "rnd_bits_unmeasured": False,
                "measured_at": "2026-10-03T17:42:07+00:00",
                # 게이트가 요약만 믿지 않게 보는 관측 칸 셋(§2.2) - 규칙은
                # `tests/test_canary_report.py`가 든다.
                "completed": True,
                "segv_count": 0,
                "problems": [],
            }
        ),
        encoding="utf-8",
    )
    return path


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
        oracle_submit=True,
        oracle_server=_SERVER,
        task_dir=task,
        mask_map=mask_map,
        oracle_canary=task.parent / "canary.json",
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
    받는다.

    🔴 카나리 보고서는 **실 id**를 든다 - 도구가 이미지 이름을 과제 id에서 뽑아 조작자가 실 id로
    돌리기 때문이다. 앞 판은 게이트가 번들의 masked id와 견줘 **정상 카나리를 전부 거절했다**.
    """
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")
    bundle = _bundle(tmp_path, _MASKED_ID, canary_task=TASK_ID)
    args = _args(bundle, _write_mask_map(tmp_path, {TASK_ID: _MASKED_ID}))

    oracle = cli._oracle_for(args)

    assert oracle is not cli._ORACLE_ERROR and oracle is not None
    assert oracle.task_id == _MASKED_ID
    assert oracle.query_task_id == TASK_ID


def test_a_canary_labelled_with_the_masked_id_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """마스킹 run의 카나리가 masked id를 들면 거절한다 - 그 보고서는 도구가 만든 것이 아니다.

    도구는 과제 id로 `-vul`·`-fix` 이미지 이름을 만든다. masked id로는 그 이미지가 없으므로
    masked id를 든 보고서는 `--vul-image`·`--fix-image`·`--run-cmd`를 손으로 준 것이거나 손으로
    쓴 것이다. 어느 쪽이든 「이 장비가 이 과제를 제대로 돌린다」의 근거가 아니다.
    """
    monkeypatch.setenv(cli._ORACLE_KEY_ENV, "k")
    bundle = _bundle(tmp_path, _MASKED_ID, canary_task=_MASKED_ID)
    args = _args(bundle, _write_mask_map(tmp_path, {TASK_ID: _MASKED_ID}))

    result = cli._oracle_for(args)

    assert result is cli._ORACLE_ERROR
    assert "카나리가 다른 과제의 것이다" in capsys.readouterr().err


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


# --- 후보가 여럿일 때: 같은 바이트는 접고, 남은 것은 전순서로 (⏵ 2026-10-04) ------------------
#
# 2026-10-03 동결판은 「내부 근거 등급 최댓값 · 동점이면 거부」였다. 아래 검사들은 그 거부가 사슬로
# 바뀐 뒤를 문다 - ①등급 ②`confidence` ③작은 바이트 ④PoC 해시(+ 앞의 ⓿응답 코드 → ⓪Critic 판정,
# 아래 절).
# 위 검사들의 후보는 근거 없는
# Finding이라 **등급·confidence가 전부 같아** ③까지 내려간다. 등급·confidence가 **갈리는** 문맥을
# 이 파일에서 만들려고 아래는 `_designate_final`을 사전째 **직접** 부른다 - 규칙 자체가 대상이고
# 파이프라인이 아니다(파이프라인에서 등급이 갈리는 run은 `test_pipeline_e2e.py`).

_SUB = VulSubmission(TASK_ID, "a" * 32, 1)


def _attempt(finding_id: str, poc: bytes) -> VulAttempt:
    return vul_ok(finding_id, poc, _SUB, wall_ms=1, designatable=True)


def _ledger_of(*attempts: VulAttempt) -> OracleLedger:
    """사이드카 없는 장부(`log=None`) - `add_attempt`가 파일을 안 쓴다(`_flush`가 먼저 돌아선다)."""
    ledger = OracleLedger(wired=True)
    for attempt in attempts:
        ledger.add_attempt(attempt)
    return ledger


def _flat(*finding_ids: str) -> dict[str, float]:
    """`confidence`가 전부 같은 사전 - 사슬이 ①②를 지나 ③④로 내려가게 한다."""
    return dict.fromkeys(finding_ids, 0.5)


def _uncritiqued(*finding_ids: str) -> dict[str, int]:
    """판정 계단이 전부 같은(1 = 미경유) 사전 - 사슬이 ⓪을 지나 ①~④로 내려가게 한다."""
    return dict.fromkeys(finding_ids, 1)


def test_the_unique_top_internal_grade_is_the_final() -> None:
    """등급이 E2·E4·E3이면 **E4 하나**가 최종이고 근거는 `top_grade`다.

    최댓값을 **가운데**에 둔다 - 처음·마지막으로 고르는 규칙이면 이 검사가 빨개진다. 자리로
    고르지 않는 것이 이 저장소의 규칙이다. ⚠ 가장 작은 PoC(`f_low`, 4바이트)를 최댓값이 **아닌**
    쪽에 둬서 ③이 ①을 이기는 구현도 빨개진다.
    """
    low = _attempt("f_low", b"\x01low")
    top = _attempt("f_top", b"\x02top\xfe\xfd")
    mid = _attempt("f_mid", b"\x03mid\xfd")
    grades = {
        "f_low": EvidenceGrade.E2,
        "f_top": EvidenceGrade.E4,
        "f_mid": EvidenceGrade.E3,
    }

    got = runner._designate_final(
        _ledger_of(low, top, mid), grades, _flat(*grades), _uncritiqued(*grades)
    )

    assert got == (top, "top_grade", None)


def test_a_tie_at_the_top_grade_is_broken_by_confidence_not_by_position() -> None:
    """등급 최댓값이 동점이면 **거부가 아니라** `confidence`가 가른다(`top_confidence`).

    ⏵ **2026-10-04 - 이 자리가 뒤집혔다.** 앞 판은 `grade_tie`로 거부했다. E4 둘 + E2 하나를
    넣고 이긴 쪽을 **가운데**에 둔다 - 자리로 가르는 구현은 첫 E4를 내서 빨개진다. 이긴 쪽의
    PoC를 **더 크게** 둬서 ③이 ②를 이기는 구현도 빨개진다.
    """
    first = _attempt("f_a", b"\x01a\xff")
    second = _attempt("f_b", b"\x02b\xfe\xfd\xfc")
    lower = _attempt("f_c", b"\x03c\xfd")
    grades = {"f_a": EvidenceGrade.E4, "f_b": EvidenceGrade.E4, "f_c": EvidenceGrade.E2}
    confidences = {"f_a": 0.4, "f_b": 0.9, "f_c": 0.9}

    got = runner._designate_final(
        _ledger_of(first, second, lower), grades, confidences, _uncritiqued(*grades)
    )

    assert got == (second, "top_confidence", None)


def test_two_pocs_from_one_finding_are_separated_by_the_smaller_bytes() -> None:
    """같은 Finding에서 PoC가 둘 나오면 등급·confidence가 같다(둘 다 Finding 단위다) - 그래서
    사슬이 ③으로 내려가 **작은 쪽**이 최종이다(`smallest_poc`).

    ⏵ **2026-10-04** - 앞 판은 「등급이 같으니 거부」였다. 작은 쪽을 **뒤에** 둔다.
    """
    big = _attempt("f_same", b"\x01second\xfe")
    small = _attempt("f_same", b"\x02one\xff")

    got = runner._designate_final(
        _ledger_of(big, small),
        {"f_same": EvidenceGrade.E4},
        _flat("f_same"),
        _uncritiqued("f_same"),
    )

    assert got == (small, "smallest_poc", None)


# --- 둘째 계단: Critic 판정 — uphold 0 < 미경유 1 < 보류 2 (#604) ----------------------------
#
# 보류 Finding이 8단계 추가 풀에 들게 되면서(`_pov_targets`) 그 PoC가 후보가 된다.
# 등급·`confidence`는 보류를 안 본다 - 하네스 Critic이 「모르겠다」고 한 PoC가 더 높은
# `confidence`로 공식 최종 제출이 되지 않도록 판정이 등급보다 **앞**이다(⏵ 2026-10-14 - 사슬의
# 맨 앞은 이제 ⓿ 후보 자기 `vul` 응답 코드이고 판정은 그 바로 뒤다, 아래 「지정 키의 첫 계단」 절
# · #637).
# 0 = uphold, 1 = 판정 없음(미경유·판정 호출 실패), 2 = 보류
# (이슈 #604 (b)의 순서 - 반박은 3이고 풀 밖이다).


def test_an_upheld_poc_is_designated_over_a_held_poc_with_a_higher_confidence() -> None:
    """보류 쪽이 등급(E4 대 E2)도 `confidence`(0.9 대 0.3)도 높은데 **지정은 uphold 쪽**이고 근거는
    `critic_verdict`다. 보류 쪽 PoC를 **더 작게** 둬서 ③이 ①을 이기는 구현도 빨개진다."""
    held = _attempt("f_held", b"\x01h")
    upheld = _attempt("f_upheld", b"\x02upheld-poc")
    grades = {"f_held": EvidenceGrade.E4, "f_upheld": EvidenceGrade.E2}
    confidences = {"f_held": 0.9, "f_upheld": 0.3}
    verdicts = {"f_held": 2, "f_upheld": 0}

    got = runner._designate_final(_ledger_of(held, upheld), grades, confidences, verdicts)
    back = runner._designate_final(_ledger_of(upheld, held), grades, confidences, verdicts)

    assert got == (upheld, "critic_verdict", None)
    assert back == got


def test_a_held_poc_alone_is_designated_as_the_sole_candidate() -> None:
    """보류 PoC가 유일한 후보면 판정 계단이 그것을 막지 않는다 - 선택이 없다(`sole_candidate`)."""
    held = _attempt("f_held", b"\x01held")

    got = runner._designate_final(
        _ledger_of(held), {"f_held": EvidenceGrade.E1}, {"f_held": 0.2}, {"f_held": 2}
    )

    assert got == (held, "sole_candidate", None)


def test_an_upheld_poc_beats_an_uncritiqued_poc_even_at_a_lower_grade() -> None:
    """uphold는 미경유(`decisions` 없음)보다 판정 계단이 앞서므로(0 대 1) 등급이 낮아도 이긴다 -
    `critic_verdict`가 가른다. ⏵ 2026-10-12 - 앞 판은 둘을 같은 0에 두고 `top_grade`로 내려가
    E3 미경유가 E2 uphold를 이겼다(@sumin0218 리뷰 MEDIUM 1 - 이슈 #604 (b)는 uphold > 미경유 > 보류)."""
    uncritiqued = _attempt("f_none", b"\x01none")
    upheld = _attempt("f_upheld", b"\x02upheld\xfe")
    grades = {"f_none": EvidenceGrade.E3, "f_upheld": EvidenceGrade.E2}
    verdicts = {"f_none": 1, "f_upheld": 0}

    got = runner._designate_final(
        _ledger_of(uncritiqued, upheld), grades, _flat(*grades), verdicts
    )
    back = runner._designate_final(
        _ledger_of(upheld, uncritiqued), grades, _flat(*grades), verdicts
    )

    assert got == (upheld, "critic_verdict", None)
    assert back == got


def test_a_held_poc_loses_to_an_uncritiqued_poc_even_at_a_higher_grade() -> None:
    """보류 대 미경유 - 판정 계단이 등급보다 앞서므로 E4 보류(2)가 E0 미경유(1)에 진다."""
    held = _attempt("f_held", b"\x01h")
    none = _attempt("f_none", b"\x02none-poc")
    grades = {"f_held": EvidenceGrade.E4, "f_none": EvidenceGrade.E0}
    verdicts = {"f_held": 2, "f_none": 1}

    got = runner._designate_final(_ledger_of(held, none), grades, _flat(*grades), verdicts)

    assert got == (none, "critic_verdict", None)


def test_a_candidate_without_a_verdict_rank_refuses_the_designation() -> None:
    """판정 계단을 모르는 후보가 있으면 `grade_unknown`이다 - 모르는 값을 0으로 깔지 않는다."""
    known = _attempt("f_known", b"\x01known")
    other = _attempt("f_other", b"\x02other\xfe")
    grades = {"f_known": EvidenceGrade.E3, "f_other": EvidenceGrade.E3}

    got = runner._designate_final(_ledger_of(known, other), grades, _flat(*grades), {"f_known": 1})

    assert got == (None, "", "grade_unknown")


def test_the_finalize_step_hands_the_critic_verdicts_to_the_designation(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """`_finalize_oracle`이 **`state.decisions`에서 읽은** 판정을 지정에 넘긴다 - 위 검사들은
    판정 사전을 손으로 꽂는다. 보류된 Finding의 `confidence`를 더 높게 두고 등급은 같게 깐다."""
    state, (held, upheld) = _findings(ctx, 2)
    state.update(held.model_copy(update={"confidence": 0.95}))
    state.update(upheld.model_copy(update={"confidence": 0.4}))
    state.decisions[held.finding_id] = (
        _critic_decision(held.finding_id, Verdict.NEED_EVIDENCE, grounded=False),
    )
    state.decisions[upheld.finding_id] = (
        _critic_decision(upheld.finding_id, Verdict.UPHOLD, grounded=True),
    )
    monkeypatch.setattr(
        runner,
        "_grades_by_finding",
        lambda _state: dict.fromkeys((held.finding_id, upheld.finding_id), EvidenceGrade.E3),
    )
    state.oracle_ledger = OracleLedger(wired=True)
    state.oracle_ledger.add_attempt(_attempt(held.finding_id, b"\x01held"))
    state.oracle_ledger.add_attempt(_attempt(upheld.finding_id, b"\x02upheld\xfe"))
    ctx.oracle = _OracleThatReadsNothingItIsGiven()

    runner._finalize_oracle(ctx, state)

    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.subject_id == upheld.finding_id
    assert final.data["designation"] == "critic_verdict"


def _finalize_two(ctx, monkeypatch, *, first, second):  # noqa: ANN001, ANN202, F811
    """`state.decisions`에서 **실제로 판정 계단을 읽는** 두 후보 - `first`·`second`는
    `(판정 또는 None, 등급, confidence)`다. 판정 사전을 손으로 꽂지 않으므로
    `_verdict_ranks_by_finding`이 틀리면 여기서 빨개진다. 돌려주는 것은
    `(first가 이겼나, designation)`."""
    state, (a, b) = _findings(ctx, 2)
    for finding, (verdict, _, confidence) in zip((a, b), (first, second), strict=True):
        state.update(finding.model_copy(update={"confidence": confidence}))
        if verdict is not None:
            state.decisions[finding.finding_id] = (
                _critic_decision(finding.finding_id, verdict, grounded=True),
            )
    monkeypatch.setattr(
        runner,
        "_grades_by_finding",
        lambda _state: {a.finding_id: first[1], b.finding_id: second[1]},
    )
    state.oracle_ledger = OracleLedger(wired=True)
    state.oracle_ledger.add_attempt(_attempt(a.finding_id, b"\x01first"))
    state.oracle_ledger.add_attempt(_attempt(b.finding_id, b"\x02second\xfe"))
    ctx.oracle = _OracleThatReadsNothingItIsGiven()

    runner._finalize_oracle(ctx, state)

    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    return final.subject_id == a.finding_id, final.data["designation"]


def test_the_finalize_step_prefers_an_uncritiqued_poc_over_a_held_one_at_a_lower_grade(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """미경유(`decisions` 없음 · E0 · 0.3) 대 보류(E2 · 0.85) - 계단 1 대 2라 미경유가 이긴다.
    보류와 미경유를 같은 계단에 두는 변이(`not any(UPHOLD)`·미경유 → 2)는 등급이 가른다."""
    first_wins, designation = _finalize_two(
        ctx,
        monkeypatch,
        first=(None, EvidenceGrade.E0, 0.3),
        second=(Verdict.NEED_EVIDENCE, EvidenceGrade.E2, 0.85),
    )

    assert (first_wins, designation) == (True, "critic_verdict")


def test_the_finalize_step_prefers_an_upheld_poc_over_an_uncritiqued_one_at_a_lower_grade(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """미경유(E3 · 0.9) 대 uphold(E2 · 0.5) - 계단 1 대 0이라 uphold가 이긴다. 미경유를 uphold와
    같은 0에 두는 변이는 등급(E3)이 가른다."""
    first_wins, designation = _finalize_two(
        ctx,
        monkeypatch,
        first=(None, EvidenceGrade.E3, 0.9),
        second=(Verdict.UPHOLD, EvidenceGrade.E2, 0.5),
    )

    assert (first_wins, designation) == (False, "critic_verdict")


def test_the_last_rung_is_the_poc_digest_and_the_input_order_does_not_move_it() -> None:
    """①②③이 전부 같으면 **PoC 해시 사전순**이 가른다(`poc_digest`) - 사슬이 전순서인 자리다.

    바이트 수까지 같은 두 PoC를 넣고, **넣는 순서를 뒤집어도 같은 PoC가 지정되는지**를 본다.
    자리(처음·마지막)로 가르는 구현은 두 호출이 서로 다른 답을 내서 빨개진다. 기대값은 해시를
    손으로 적지 않고 **`min`으로 센다** - 리터럴을 적으면 검사가 해시 구현을 따라 베낀다.
    """
    one = _attempt("f_one", b"\xaa\xbb\xcc")
    two = _attempt("f_two", b"\xdd\xee\xff")
    assert one.poc_bytes == two.poc_bytes, "③이 갈라 버리면 ④를 안 본다"
    grades = {"f_one": EvidenceGrade.E3, "f_two": EvidenceGrade.E3}
    expected = min((one, two), key=lambda a: a.poc_sha256)

    ranks = _uncritiqued(*grades)
    forward = runner._designate_final(_ledger_of(one, two), grades, _flat(*grades), ranks)
    backward = runner._designate_final(_ledger_of(two, one), grades, _flat(*grades), ranks)

    assert forward == (expected, "poc_digest", None)
    assert backward[0] is expected and backward[1] == "poc_digest"


def test_candidates_that_are_all_the_same_bytes_are_folded_into_one_choice() -> None:
    """🔴 **실측 run이 점수를 못 낸 자리다.** 후보 8건이 **같은 PoC 바이트 1개**였고(제출 8 = 고유
    PoC 1 = DB 행 1) 등급이 Finding 단위라 8건이 동점이었다. 앞 판은 그것을 `grade_tie`로 거부해
    점수를 0으로 만들었다 - 어느 후보를 지정해도 **제출될 바이트가 같은데** 거부한 것이다.

    지금은 접는다: 남은 고유 PoC가 하나면 `sole_poc`이고, 그것은 「등급이 갈랐다」가 아니라
    **「고를 것이 없었다」**다(`sole_candidate`과 같은 종류의 사실이고 사슬의 단계가 아니다).
    등급을 **갈라 놔도** 결론이 같은지 본다 - 같은 바이트 안의 등급 차는 제출될 PoC를 안 바꾼다.
    """
    same = b"A" * 64 + b"\xef\xbe\xad\xde"  # 실측 run의 68바이트와 같은 모양
    attempts = [_attempt(f"f_{i:04d}", same) for i in range(8)]
    grades = {f"f_{i:04d}": EvidenceGrade.E0 if i else EvidenceGrade.E4 for i in range(8)}
    assert len({a.poc_sha256 for a in attempts}) == 1, "접을 전제(바이트가 같다)가 깨졌다"

    picked, designation, reason = runner._designate_final(
        _ledger_of(*attempts), grades, _flat(*grades), _uncritiqued(*grades)
    )

    assert (designation, reason) == ("sole_poc", None)
    assert picked is not None and picked.poc == same


def test_inside_a_fold_group_the_recorded_finding_does_not_follow_the_input_order() -> None:
    """같은 바이트 묶음 안에서 **어느 `finding_id`로 기록할지**도 입력 순서에 안 묶인다.

    제출될 바이트는 묶음 안에서 같으니 이 선택이 PoC를 바꾸지는 않는다 - 그래도 같은 후보
    묶음을 어떤 순서로 넣어도 같은 기록이 나와야 재현 실행이 갈라지지 않는다. 등급·confidence·
    바이트·해시가 전부 같은 셋을 넣고 순서를 뒤집어 본다. 정렬 꼬리(`finding_id`)를 빼면
    `sorted`의 안정성 때문에 **넣은 순서의 첫째**가 나와 이 검사가 빨개진다.
    """
    same = bytes([1]) + b"same" + bytes([255])
    ids = ["f_0003", "f_0001", "f_0002"]
    attempts = [_attempt(finding_id, same) for finding_id in ids]
    grades = dict.fromkeys(ids, EvidenceGrade.E3)

    ranks = _uncritiqued(*ids)
    forward = runner._designate_final(_ledger_of(*attempts), grades, _flat(*ids), ranks)
    backward = runner._designate_final(
        _ledger_of(*reversed(attempts)), grades, _flat(*ids), ranks
    )

    assert forward[0] is not None and forward[0].finding_id == "f_0001"
    assert backward[0] is not None and backward[0].finding_id == "f_0001"
    assert forward[1] == backward[1] == "sole_poc"


def test_a_grade_split_inside_one_fold_group_does_not_decide_the_poc() -> None:
    """갈랐다고 적는 상대는 **다른 바이트를 든 후보**다 - 같은 바이트끼리의 차이는 제출될 PoC를
    바꾸지 않으므로 그것이 `designation`이 되면 안 된다.

    자리를 이렇게 짠다: 같은 바이트 둘이 `confidence`로 갈리고(`f_hi` 0.9 · `f_mid` 0.5),
    **다른** 바이트 하나는 등급이 낮다(`f_other` E2 · 0.9). 정렬은
    `[f_hi, f_mid, f_other]`이므로 **`ordered[1]`이 같은 묶음**이다 -
    상대를 `ordered[1]`로 잡는 구현은 `confidence`가 갈랐다고 적고(`top_confidence`),
    참인 답은 다른 바이트와의 **등급** 차다(`top_grade`). 그래서 이 검사는 이름으로 그 변이를
    가른다.
    """
    shared = bytes([1]) + b"shared" + bytes([255])
    twin_high = _attempt("f_hi", shared)
    twin_mid = _attempt("f_mid", shared)
    other = _attempt("f_other", bytes([2]) + b"other" + bytes([254]))
    grades = {
        "f_hi": EvidenceGrade.E4,
        "f_mid": EvidenceGrade.E4,
        "f_other": EvidenceGrade.E2,
    }
    confidences = {"f_hi": 0.9, "f_mid": 0.5, "f_other": 0.9}

    picked, designation, reason = runner._designate_final(
        _ledger_of(twin_mid, twin_high, other), grades, confidences, _uncritiqued(*grades)
    )

    assert picked is not None and picked.poc == shared
    assert (designation, reason) == ("top_grade", None)


def test_a_candidate_with_an_unknown_grade_refuses_rather_than_reading_it_as_e0() -> None:
    """등급을 모르는 후보가 있으면 거부한다(`grade_unknown`) - E0로 깔면 **없는 근거로** 고른다.

    모르는 쪽을 E0로 읽는 구현은 E4인 쪽을 최종으로 내서 이 검사가 빨개진다. 안 잰 값은 0이
    아니라 미실측 표시다(CLAUDE.md §0). **이 거부는 2026-10-04에도 그대로다** - 동점과 달리
    「모르는 값」은 사슬의 다음 단계로 넘길 수 있는 것이 아니다.
    """
    known = _attempt("f_known", b"\x01known\xff")
    unknown = _attempt("f_unknown", b"\x02unknown\xfe")

    assert runner._designate_final(
        _ledger_of(known, unknown),
        {"f_known": EvidenceGrade.E4},
        _flat("f_known", "f_unknown"),
        _uncritiqued("f_known", "f_unknown"),
    ) == (None, "", "grade_unknown")


def test_a_candidate_with_an_unknown_confidence_is_refused_the_same_way() -> None:
    """`confidence`를 모르는 후보도 같은 거부다 - 사슬의 ②가 읽는 값이고, 없는 것을 0.0으로 깔면
    **없는 근거로** 나머지를 고른다(위 검사와 같은 §0 논법).

    현 러너에서는 세 사전이 같은 `state.findings`에서 나와 키 집합이 같다
    (`_grades_by_finding`·`_confidences_by_finding`·`_verdict_ranks_by_finding`) - 그래서 이것도
    **방어 분기**다.
    """
    known = _attempt("f_known", b"\x01known\xff")
    other = _attempt("f_other", b"\x02other\xfe")
    grades = {"f_known": EvidenceGrade.E4, "f_other": EvidenceGrade.E2}

    assert runner._designate_final(
        _ledger_of(known, other), grades, {"f_known": 0.5}, _uncritiqued(*grades)
    ) == (None, "", "grade_unknown")


def test_there_is_no_tie_refusal_left_to_fall_back_on() -> None:
    """🔴 **사슬이 전순서라 「못 가렸다」가 없다** - 그 사유 상수를 되살리는 변이를 이 줄이 막는다.

    ⏵ 2026-10-04에 `REASON_GRADE_TIE`를 **지웠다**. 사유 상수만 되돌리면 어느 run도 못 내는 사유가
    문서와 경고에 남고(닿지 않는 코드), 분기까지 되돌리면 위 검사들이 빨개진다.

    같이 무는 것 하나 더 - `_DESIGNATION_CHAIN`의 이름 수와 `_designation_key`의 성분 수가 같다.
    키에 성분을 더하고 이름을 안 붙이면 `zip(..., strict=True)`가 터지는데, 그 폭발이 **사슬이
    갈린 run에서만** 나므로 여기서 길이를 직접 센다.
    """
    assert not [name for name in vars(oracle_log_module) if "GRADE_TIE" in name]
    key = runner._designation_key(
        _attempt("f", b"\x00"), {"f": EvidenceGrade.E0}, {"f": 0.5}, {"f": 1}
    )
    # ⏵ 2026-10-15 - 여섯에서 일곱(`submitted_over_rescued`가 응답 코드 계단 바로 뒤에 붙었다)
    assert len(runner._DESIGNATION_CHAIN) == len(key) == 7
    assert runner._DESIGNATION_CHAIN[:3] == (
        "vul_crash", "submitted_over_rescued", "critic_verdict",
    )


def _trap(name: str):  # noqa: ANN202
    def boom(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202, ARG001
        raise AssertionError(f"지정 로직이 채점기가 돌려준 submission을 읽었다: {name}")

    return boom


class _TrapSubmission:
    """`response_exit_code` **한 필드만** 읽히는 `submission` - 그 밖의 **속성**(`.poc_id`·
    `.task_id`·`.cached`)·`vars`·`__dict__`(`__slots__`라 없다)·`repr`·`str`·`format`·`==`·
    `hash`·`dir`는 전부 `AssertionError`다.

    ⏵ 2026-10-14 - 지정 키의 첫 계단(`vul_crash`)이 그 필드를 읽도록 ADR 0013 결정 2가 개정됐다
    (이슈 #637). 앞 판은 그 필드도 막았다. **열린 것은 그 한 필드뿐이다** - `cached`·`poc_id`로
    고르는 변이는 여전히 빨개진다.

    타입은 `VulSubmission`이 아니지만 그 자리에 꽂힌다 - `VulAttempt`가 frozen dataclass라 런타임
    검증이 없다. ⚠ **못 무는 것:** `submission is not None`(후보 자격 `attempt.ok`)은 접근이
    아니라 허용한다. 그리고 `attempt`의 **다른 필드**(`poc`·`poc_sha256`·`wall_ms`)나 `grades`
    사전에 채점기 값이 섞이는 경로는 이 함정이 안 본다 - 앞의 둘은 하네스가 만든 값이고 뒤의
    것은 `test_the_oracle_leg_leaves_the_graded_evidences_alone`이 러너 쪽에서 문다. 열린 한
    필드를 **어떻게** 읽는지(`None`·환경 코드를 크래시로 세는 변이)는 아래
    `test_an_exit_code_that_is_not_a_crash_never_ranks_as_one`이 문다.
    """

    __slots__ = ("response_exit_code",)
    __repr__ = _trap("repr")
    __str__ = _trap("str")
    __format__ = _trap("format")
    __eq__ = _trap("==")
    __ne__ = _trap("!=")
    __hash__ = _trap("hash")
    __dir__ = _trap("dir")

    def __init__(self, response_exit_code: int | None) -> None:
        self.response_exit_code = response_exit_code

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"지정 로직이 채점기가 돌려준 submission을 읽었다: .{name}")


def _trap_attempt(finding_id: str, code: int | None = 1) -> VulAttempt:
    """함정 `submission`을 든 후보. **해시는 `finding_id`에서 뽑는다** - 앞 판은 전부 같은
    `"0" * 64`라 접기(`poc_sha256`)가 모든 분기를 `sole_poc` 하나로 눌러 분기들이 뜻을
    잃는다.
    바이트 수도 id 길이에서 와서 `smallest_poc` 분기가 선다."""
    body = finding_id.encode()
    return VulAttempt(
        finding_id=finding_id,
        poc_sha256=hashlib.sha256(body).hexdigest(),
        poc_bytes=len(body),
        wall_ms=1,
        poc=body,
        submission=_TrapSubmission(code),  # type: ignore[arg-type]
        designatable=True,
    )


# (후보 finding들, 등급 사전, 이긴 finding, designation, reason) - **사슬의 단계마다 하나씩**이다.
# 금지된 읽기가 들어갈 가장 자연스러운 자리는 **등급이 동점인 분기**다(「크래시 코드가 큰 쪽」으로
# 가른다) - 그래서 유일 최상위 분기만 문 판은 그 변이를 놓쳤다(적대적 리뷰 2026-10-04: 129건 초록).
# ⏵ 2026-10-04 - 그 `tie` 분기가 「거부」에서 「사슬이 가른다」로 바뀌었다. `confidence`를
# `_flat`으로 깔아 ③(작은 바이트)까지 내려가게 한다 - 동점 분기를 살려 두려는 것이다
# (`f_bb`가 2바이트로 최소).
_BRANCHES = [
    pytest.param(
        ("f_low", "f_top", "f_mid"),
        {"f_low": EvidenceGrade.E2, "f_top": EvidenceGrade.E4, "f_mid": EvidenceGrade.E3},
        "f_top", "top_grade", None,
        id="unique-top",
    ),
    pytest.param(
        ("f_aaa", "f_bb", "f_c"),
        {"f_aaa": EvidenceGrade.E4, "f_bb": EvidenceGrade.E4, "f_c": EvidenceGrade.E2},
        "f_bb", "smallest_poc", None,
        id="grade-tie-smallest",
    ),
    pytest.param(
        ("f_known", "f_unknown"),
        {"f_known": EvidenceGrade.E4},
        None, None, "grade_unknown",
        id="unknown-grade",
    ),
    pytest.param(("f_sole",), {}, "f_sole", "sole_candidate", None, id="sole-candidate"),
]  # fmt: skip


@pytest.mark.parametrize(("ids", "grades", "winner", "designation", "reason"), _BRANCHES)
def test_designation_does_not_read_oracle_fields(
    ids, grades, winner, designation, reason  # noqa: ANN001
) -> None:
    """🔴 **불변식** - 지정은 채점기가 돌려준 값 중 **`response_exit_code` 한 필드만** 읽는다
    (⏵ 2026-10-14 ADR 0013 결정 2 개정 · 이슈 #637 - 앞 판은 그 필드도 막았다). 이 저장소가 정한
    규칙이다(저자의 금지 문장이 아니다 - `_designate_final` docstring).

    나머지는 **지정 시점에 손에 있는데도** 안 읽는다 - `VulSubmission.poc_id`·`cached`가 후보마다
    장부 행에 실려 있어서(`oracle_log._attempt_record`) 「없어서 안 읽는다」가 아니다. 그래서 문구가
    아니라 **접근**을 문다: 후보의 `submission`이 `_TrapSubmission`이고(그 한 필드 말고는 속성·
    `vars`·`repr`·`==` 전부 터진다) **분기마다**(유일 최상위 · 등급 동점 → 작은 바이트 ·
    등급·confidence·판정 계단 미상 · 후보 하나) 돌린다. 함정의 코드는 전 후보가 같은
    계단(크래시)이라
    이 표의 기대값은 개정 전과 같다 - 계단이 갈리는 분기는 아래 `test_the_vul_tier_reads_only_…`.

    **오염 테스트다** - 어느 분기에든 `attempt.submission.cached`·`.poc_id`를 보거나 `repr`로
    고르는 한 줄을 넣으면 그 분기가 빨개진다(2026-10-04 스크래치 사본에서 변이 아홉으로 확인 —
    그때는 `response_exit_code`도 그 아홉에 들었다). 읽어도 되는 것은 `attempt.ok`(후보 자격)와
    하네스 내부 등급, 그리고 이제 그 한 필드다. 후보 0건(실패한 제출만)은 읽을 `submission`이 없어
    이 함정이 못 문다 - 그 분기는 위 검사들이 문다.
    """
    attempts = [_trap_attempt(finding_id) for finding_id in ids]

    picked, got_designation, got_reason = runner._designate_final(
        _ledger_of(*attempts), grades, _flat(*ids), _uncritiqued(*ids)
    )

    assert (got_designation or None, got_reason) == (designation, reason)
    if winner is None:
        assert picked is None
    else:
        assert picked is attempts[ids.index(winner)]


def test_the_verdict_step_does_not_read_oracle_fields_either() -> None:
    """판정 계단이 갈라도(`critic_verdict`) 후보의 `submission`을 열지 않는다 - 위 분기표에 없는
    새 계단이라 따로 문다."""
    ids = ("f_held", "f_upheld")
    attempts = [_trap_attempt(finding_id) for finding_id in ids]

    picked, designation, reason = runner._designate_final(
        _ledger_of(*attempts),
        dict.fromkeys(ids, EvidenceGrade.E3),
        _flat(*ids),
        {"f_held": 2, "f_upheld": 0},
    )

    assert (picked, designation, reason) == (attempts[1], "critic_verdict", None)


# --- 지정 키의 첫 계단: 자기 `vul` 제출 응답 코드 (⏵ 2026-10-14 · 이슈 #637 · ADR 0013 결정 2) ---
#
# 크래시(0) < 모름(1) < 크래시 아님(2). 나머지 사슬은 개정 전 그대로다.

# 크래시로 읽는 코드 / 모른다고 읽는 코드(환경 실패·`None`) / 크래시가 아니라고 읽는 코드.
_CRASH_CODES = (1, 2, 77, 134, 139)
_UNKNOWN_CODES = (None, 124, 125, 126, 127, 137, 255, 256, 300, -1)
_NO_CRASH_CODES = (0, 71)


def _coded(finding_id: str, poc: bytes, code: int | None) -> VulAttempt:
    """제출 응답 코드가 `code`인 후보. `None`은 계약(`int`) 밖이지만 「코드가 없다」를 흉내 낸다 -
    프레임이 형을 검사하지 않는다(`_TrapSubmission`과 같은 이유)."""
    sub = VulSubmission(TASK_ID, "a" * 32, code)  # type: ignore[arg-type]
    return vul_ok(finding_id, poc, sub, wall_ms=1, designatable=True)


@pytest.mark.parametrize(
    ("code", "tier"),
    [
        *[(c, oracle_log_module.VUL_TIER_CRASH) for c in _CRASH_CODES],
        *[(c, oracle_log_module.VUL_TIER_UNKNOWN) for c in _UNKNOWN_CODES],
        *[(c, oracle_log_module.VUL_TIER_NO_CRASH) for c in _NO_CRASH_CODES],
    ],
)
def test_the_exit_code_tier_table(code: int | None, tier: int) -> None:
    """계단 표 - 크래시 / 모름 / 크래시 아님. 139(SIGSEGV)·77은 대상이 낼 수 있는 값이라
    크래시 계단이고(호스트 잡음일 수 있다 - ADR 0013 한계)
    124~127·137·255·255 초과·음수·`None`은 **크래시로 안 센다**(모름). 71(OOM)은 채점기의 제외
    코드라 크래시 아님이다. 300은 제외 코드이기도 하고 255 초과이기도 한데 **환경 쪽이 이긴다** -
    타임아웃은 「크래시가 안 났다」가 아니라 「끝까지 못 돌았다」다(§0). ⚠ 다만 서버가 300을
    응답에서 0으로 덮으므로(#384) 이 행은 응답 경로로는 닿지 않는 방어 표다(어댑터 사본과
    구간 전체를 맞추려 둔다)."""
    assert oracle_log_module.vul_code_tier(code) == tier
    assert oracle_log_module.VUL_TIER_CRASH < oracle_log_module.VUL_TIER_UNKNOWN
    assert oracle_log_module.VUL_TIER_UNKNOWN < oracle_log_module.VUL_TIER_NO_CRASH


def test_the_env_failure_codes_are_the_tool_adapters_and_cover_the_pov_node_set() -> None:
    """오케스트레이터는 `src.adapters`를 못 읽어 환경 실패 코드를 `oracle_log`에 따로 든다 -
    어댑터의 `_poc_is_harness_failure`와 **구간 전체에서** 같아야 한다(둘이 어긋나면 한쪽이 환경
    코드를 크래시로 센다). PoV 노드의 `INFRA_FAILURE_EXITS`는 그 부분집합이다."""
    from src.adapters.container_tools import _poc_is_harness_failure
    from src.orchestrator.nodes.pov import INFRA_FAILURE_EXITS

    unknown = oracle_log_module.VUL_TIER_UNKNOWN
    for code in range(-300, 1100):
        assert (oracle_log_module.vul_code_tier(code) == unknown) == _poc_is_harness_failure(
            code
        ), code
    assert INFRA_FAILURE_EXITS <= oracle_log_module.ENV_FAILURE_VUL_EXIT_CODES


def test_a_crashing_poc_beats_a_better_ranked_one_that_did_not_crash() -> None:
    """🔴 이슈 #637. A(보류 · E1 · 0.1 · **크래시 139**) 대 B(uphold · E4 · 0.9 · **코드 0**) -
    개정 전 키는 계단(⓪)이 먼저라 B를 냈다. 이제 첫 계단이 가르므로 A고, 근거는 `vul_crash`다.
    넣는 순서를 뒤집어도 같다."""
    crashed = _coded("f_crashed", b"\x01crashed", 139)
    clean = _coded("f_clean", b"\x02clean\xfe", 0)
    grades = {"f_crashed": EvidenceGrade.E1, "f_clean": EvidenceGrade.E4}
    confidences = {"f_crashed": 0.1, "f_clean": 0.9}
    ranks = {"f_crashed": 2, "f_clean": 0}  # 보류 · uphold

    for order in ((crashed, clean), (clean, crashed)):
        got = runner._designate_final(_ledger_of(*order), grades, confidences, ranks)
        assert got == (crashed, "vul_crash", None)


def test_the_vul_tier_reads_only_the_exit_code_field() -> None:
    """계단이 **갈리는** 분기에서도 `response_exit_code` 말고는 `submission`을 안 연다 -
    위 분기표는 전 후보가 같은 계단이라 이 분기를 못 문다. 다른 속성(`cached`·`poc_id`)에 손대는
    변이는 함정이 터뜨린다."""
    crashed = _trap_attempt("f_crashed", 139)
    clean = _trap_attempt("f_clean", 0)
    ids = ("f_crashed", "f_clean")

    for order in ((crashed, clean), (clean, crashed)):
        got = runner._designate_final(
            _ledger_of(*order),
            {"f_crashed": EvidenceGrade.E0, "f_clean": EvidenceGrade.E4},
            _flat(*ids),
            _uncritiqued(*ids),
        )
        assert got == (crashed, "vul_crash", None)


@pytest.mark.parametrize("code", _UNKNOWN_CODES)
def test_an_exit_code_that_is_not_a_crash_never_ranks_as_one(code: int | None) -> None:
    """모름(환경 실패·`None`)은 크래시도 크래시 아님도 아니다 - **둘 사이**다(§0: 안 잰 값을 0으로
    깔지 않는다).

    ① 크래시 후보(E0 · 보류)가 모름 후보(E3 · 미경유 - 사슬만 보면 이쪽이 앞선다)를 이긴다 -
       모름을 크래시로 세는 변이는 사슬로 내려가 모름 후보를 낸다.
    ② 크래시가 없으면 모름 후보(E1 · 보류)가 크래시 아님 후보(E4 · uphold - 사슬만 보면 이쪽이
       앞선다)를 이긴다 - 모름을 크래시 아님으로 세는 변이는 후자를 낸다.
    """
    crash = _coded("f_crash", b"\x01crash", 139)
    unknown = _coded("f_unknown", b"\x02unknown\xfe", code)
    clean = _coded("f_clean", b"\x03clean\xfd", 0)

    grades = {
        "f_crash": EvidenceGrade.E0,
        "f_unknown": EvidenceGrade.E3,
        "f_clean": EvidenceGrade.E4,
    }
    confidences = {"f_crash": 0.1, "f_unknown": 0.5, "f_clean": 0.9}
    ranks = {"f_crash": 2, "f_unknown": 1, "f_clean": 0}
    picked, designation, _ = runner._designate_final(
        _ledger_of(clean, unknown, crash), grades, confidences, ranks
    )
    assert (picked, designation) == (crash, "vul_crash")

    grades["f_unknown"], ranks["f_unknown"] = EvidenceGrade.E1, 2
    picked, designation, _ = runner._designate_final(
        _ledger_of(clean, unknown), grades, confidences, ranks
    )
    assert (picked, designation) == (unknown, "vul_crash")


@pytest.mark.parametrize("code", (*_CRASH_CODES, *_NO_CRASH_CODES))
def test_a_known_code_ranks_by_its_tier_only_not_by_its_value(code: int) -> None:
    """같은 계단 안에서는 코드 값이 순위에 안 든다 - 139가 77을 이기는 식의 「큰 코드」 규칙이
    아니다(`vul_crash`는 계단만 가른다). 같은 계단 둘(코드가 다르다)이 사슬로 내려가 더 높은
    등급이 이긴다."""
    tier = oracle_log_module.vul_code_tier(code)
    twins = [c for c in (*_CRASH_CODES, *_NO_CRASH_CODES) if c != code and
             oracle_log_module.vul_code_tier(c) == tier]  # fmt: skip
    for twin in twins:
        weak = _coded("f_weak", b"\x01weak", code)
        strong = _coded("f_strong", b"\x02strong\xfe", twin)
        grades = {"f_weak": EvidenceGrade.E2, "f_strong": EvidenceGrade.E4}
        got = runner._designate_final(
            _ledger_of(weak, strong), grades, _flat(*grades), _uncritiqued(*grades)
        )
        assert got == (strong, "top_grade", None), (code, twin)


def _chain_before_637(attempt: VulAttempt, grades, confidences, ranks) -> tuple:  # noqa: ANN001
    """이슈 #637 이전의 `_designation_key` - 손으로 옮긴 사본이다(기준선)."""
    return (
        ranks[attempt.finding_id],
        -gate.GRADE_ORDER.index(grades[attempt.finding_id]),
        -confidences[attempt.finding_id],
        attempt.poc_bytes,
        attempt.poc_sha256,
    )


@pytest.mark.parametrize("code", (*_CRASH_CODES, *_NO_CRASH_CODES, *_UNKNOWN_CODES))
def test_when_every_candidate_shares_a_tier_the_old_chain_decides_exactly(
    code: int | None,
) -> None:
    """한 계단 안에서는 선택이 **개정 전과 같다** - 어느 계단이든(코드가 전부 같다). 기대값은 옛 키
    사본으로 센다(`_chain_before_637`). 지정 근거도 첫 계단 이름(`vul_crash`)이 아니다."""
    specs = [  # (finding_id, 계단, 등급, confidence, 바이트)
        ("f_a", 2, EvidenceGrade.E3, 0.9, b"\x01aaaa"),
        ("f_b", 0, EvidenceGrade.E2, 0.4, b"\x02bb"),
        ("f_c", 0, EvidenceGrade.E4, 0.2, b"\x03cccccc"),
        ("f_d", 1, EvidenceGrade.E4, 0.9, b"\x04d"),
    ]
    attempts = [_coded(fid, poc, code) for fid, _, _, _, poc in specs]
    ranks = {fid: rank for fid, rank, _, _, _ in specs}
    grades = {fid: grade for fid, _, grade, _, _ in specs}
    confidences = {fid: conf for fid, _, _, conf, _ in specs}
    expected = min(attempts, key=lambda a: _chain_before_637(a, grades, confidences, ranks))

    picked, designation, reason = runner._designate_final(
        _ledger_of(*attempts), grades, confidences, ranks
    )

    assert picked is expected and reason is None
    assert designation in {"critic_verdict", "top_grade", "top_confidence", "smallest_poc"}


def test_the_designation_does_not_follow_the_input_order_across_tiers() -> None:
    """계단이 섞인 넷을 **모든 순서로** 넣어도 같은 PoC·같은 근거가 나온다 - 자리(처음·마지막)로
    고르는 구현과 `sorted`의 안정성에 기대는 구현이 여기서 빨개진다."""
    attempts = [
        _coded("f_a", b"\x01a", 0),
        _coded("f_b", b"\x02bb", 137),
        _coded("f_c", b"\x03ccc", 139),
        _coded("f_d", b"\x04dddd", 77),
    ]
    ids = tuple(a.finding_id for a in attempts)
    grades = {"f_a": EvidenceGrade.E4, "f_b": EvidenceGrade.E3,
              "f_c": EvidenceGrade.E1, "f_d": EvidenceGrade.E1}  # fmt: skip
    confidences = {"f_a": 0.9, "f_b": 0.8, "f_c": 0.3, "f_d": 0.6}

    results = {
        runner._designate_final(_ledger_of(*order), grades, confidences, _uncritiqued(*ids))[:2]
        for order in itertools.permutations(attempts)
    }

    # 크래시 둘(f_c·f_d)이 앞서고, 그 안은 `confidence`가 가른다(f_d 0.6 > f_c 0.3) - 근거는
    # 지정한 것과 **차점**을 가른 단계다(`top_confidence`). 첫 계단은 둘이 같아 이름이 안 붙는다.
    assert results == {(attempts[3], "top_confidence")}


class _OracleThatReadsNothingItIsGiven:
    """`finalize`가 받은 `vul`을 열지 않는 대역 - 함정 `submission`이 `_score`를 지나가게 한다."""

    def submit_vul(self, poc: bytes):  # noqa: ANN201
        raise AssertionError("이 검사에서는 제출이 안 나간다 - 장부를 손으로 채운다")

    def finalize(self, poc: bytes, vul):  # noqa: ANN001, ANN201
        return OracleOutcome(
            task_id=TASK_ID, poc_id=None, poc_hash="0" * 64, poc_length=len(poc),
            vul_exit_code=None, fix_exit_code=None, success=False,
        )  # fmt: skip


@pytest.mark.parametrize(("ids", "grades", "winner", "designation", "reason"), _BRANCHES)
def test_finalize_oracle_does_not_read_oracle_fields_either(
    ctx, monkeypatch, ids, grades, winner, designation, reason  # noqa: ANN001, F811
) -> None:
    """`_designate_final`만이 아니라 **`_finalize_oracle`도** 채점기 값을 **응답 코드 한 필드
    말고는** 안 읽는다(⏵ 2026-10-14 · 이슈 #637) - 지정 직전에
    등급을 채점 성공 후보 쪽으로 올리는 변이는 `_designate_final`을 안 건드리고 여기서 난다.
    함정 `submission`이 든 장부로 `_finalize_oracle`을 직접 돌리고(등급은
    `_grades_by_finding`을 바꿔 꽂는다) 결과 기록이 분기대로인지 본다."""
    state, _ = _findings(ctx, 1)
    state.oracle_ledger = OracleLedger(wired=True)
    for finding_id in ids:
        state.oracle_ledger.add_attempt(_trap_attempt(finding_id))
    monkeypatch.setattr(runner, "_grades_by_finding", lambda _state: grades)
    # 세 사전은 현 러너에서 같은 `state.findings`에서 나와 키 집합이 같다 - 여기서는 등급 사전을
    # 손으로 꽂으므로 `confidence`·판정 계단도 같은 키로 깔아 준다(안 깔면 전 분기가
    # `grade_unknown`이다).
    monkeypatch.setattr(runner, "_confidences_by_finding", lambda _state: _flat(*ids))
    monkeypatch.setattr(runner, "_verdict_ranks_by_finding", lambda _state: _uncritiqued(*ids))
    ctx.oracle = _OracleThatReadsNothingItIsGiven()

    runner._finalize_oracle(ctx, state)

    (final,) = _events(ctx, EventKind.ORACLE_FINAL)
    assert final.subject_id == winner
    assert (final.data["designation"], final.data["reason"]) == (designation, reason)
    assert final.data["status"] == ("none" if winner is None else "scored")


def test_the_oracle_leg_leaves_the_graded_evidences_alone(
    ctx, monkeypatch  # noqa: ANN001, F811
) -> None:
    """지정 등급의 입력은 `state.evidences`·`state.findings`이고(`_grades_by_finding`) **채점기
    다리는 그것을 안 건드린다** - 채점기 응답이 Evidence가 되거나 Finding을 바꾸면 그 등급이
    채점기 값을 담고 지정이 채점기 값으로 고르는 것과 같아진다. 8단계 `produced`(하네스가
    도구를 돌려 낸 근거)와 이것은 다른 원천이다.

    **한계:** 이 검사는 두 사전이 **같다**는 것만 본다. 채점기 값을 다른 길(전역·지역 사전)로
    등급에 섞는 변이는 못 잡는다.
    """
    state, findings = _findings(ctx, 2)
    state.oracle_ledger = OracleLedger(wired=True)
    evidences, found = dict(state.evidences), dict(state.findings)
    _stub_loop(monkeypatch, [(poc, _SUBMITTED) for poc in _POCS[:2]])

    _drive(ctx, state, findings, FakeOracle(vul_exit_code=139))

    assert state.evidences == evidences
    assert state.findings == found


_CANARY_REF = {"canary_path": "/ops/canary.json", "canary_sha256": "sha256:" + "ab" * 32}


class _BoundOracle(FakeOracle):
    """카나리 보고서에 묶인 채점기 - `TaskOracle`이 `_oracle_for`에서 받는 두 값을 든다."""

    canary_path = _CANARY_REF["canary_path"]
    canary_sha256 = _CANARY_REF["canary_sha256"]


def test_the_canary_report_a_run_leaned_on_is_recorded_in_the_manifest_summary(
    tmp_path: Path,
) -> None:
    """이슈 #487 - `RunRecord.oracle`에 보고서 경로와 sha256이 남는다. 빼면 못 변호한다."""
    result, _ = _run(tmp_path, oracle=_BoundOracle())

    assert result.record.oracle is not None
    assert {key: result.record.oracle[key] for key in _CANARY_REF} == _CANARY_REF


def test_a_run_without_a_canary_reference_leaves_the_summary_keys_as_they_were(
    tmp_path: Path,
) -> None:
    """이 필드 이전의 manifest와 키 집합이 같아야 한다 - 안 준 run에 빈 칸을 새로 만들지 않는다."""
    result, _ = _run(tmp_path, oracle=FakeOracle())

    assert result.record.oracle is not None
    assert not {"canary_path", "canary_sha256"} & result.record.oracle.keys()
