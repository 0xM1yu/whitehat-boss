"""Evidence Gate 술어 테스트.

게이트는 순수 함수이므로 테스트도 순수하다 — store도 trace도 쓰지 않는다.
등급 판정은 이 시스템이 "측정하려는 값"이 아니라 **문서가 정한 규칙**이므로 여기서는
조건별 등급을 그대로 단언한다(architecture.md §4 표).
"""

from datetime import UTC, datetime
from itertools import product

import pytest

from src.adapters.container_tools import Step, judge
from src.orchestrator import gate
from src.schemas import (
    Decision,
    Evidence,
    EvidenceGrade,
    EvidenceKind,
    Finding,
    FindingState,
    Location,
    MissingEvidence,
    PathStep,
    PathStepKind,
    ProducedBy,
    ReproOrigin,
    ReproSpec,
    Role,
    Verdict,
)

RUN_ID = "run_20260901_0001"
OBSERVED_AT = datetime(2026, 9, 1, tzinfo=UTC)
TARGET = Location(file="parser.c", symbol="parse_header", line_start=10, line_end=20)
AUDITOR = ProducedBy(role=Role.AUDITOR, model="fake", prompt_version="v1")
CRITIC = ProducedBy(role=Role.CRITIC, model="fake", prompt_version="v1")

FULL_PATH = [
    PathStep(step=PathStepKind.SOURCE, symbol="read_input"),
    PathStep(step=PathStepKind.PROPAGATE, symbol="parse_header"),
    PathStep(step=PathStepKind.SINK, symbol="memcpy"),
]

BLOCKING = MissingEvidence(
    missing_id="mev_0001",
    run_id=RUN_ID,
    finding_id="find_0001",
    need="빌드 플래그",
    how_to_obtain="빌드 로그 첨부",
    blocking=True,
)

# E4가 받는 종류. 게이트의 `_RUNTIME_KINDS`를 import하지 않고 손으로 다시 적는다 — 그 상수가
# 줄어들 때 검사도 같이 줄어 변이가 살아나는 것을 막는다.
RUNTIME_KINDS = (EvidenceKind.SANITIZER, EvidenceKind.CRASH, EvidenceKind.POC_RUN)

# 0도 None도 아닌 종료 코드의 모양 넷 — asan의 기본 종료 코드(1) · 셸이 신호에 매기는 128+n(139) ·
# gdb가 신호로 멈춘 대상에 낸 값(255, 저장소 녹음 전부) · 신호를 음수로 돌려주는 런타임(-11).
# 값이 아니라 「0도 None도 아님」을 문다.
CRASH_EXIT_CODES = (1, 139, 255, -11)


def _evidence(
    evidence_id: str,
    kind: EvidenceKind,
    *,
    ok: bool = True,
    location: Location | None = None,
    exit_code: int | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        run_id=RUN_ID,
        kind=kind,
        source="semgrep",
        artifact_id="art_0001",
        location=location,
        payload_ref="logs/semgrep.json",
        digest="sha256:" + "0" * 64,
        observed_at=OBSERVED_AT,
        ok=ok,
        exit_code=exit_code,
        error=None if ok else "도구 실패",
    )


def _finding(
    *,
    refs: list[str],
    path: list[PathStep] | None = None,
    repro: bool = False,
) -> Finding:
    return Finding(
        finding_id="find_0001",
        run_id=RUN_ID,
        candidate_id="cand_0001",
        title="검증 없는 memcpy",
        cwe="CWE-787",
        state=FindingState.HYPOTHESIZED,
        confidence=0.6,
        path=path or [],
        evidence_refs=refs,
        produced_by=AUDITOR,
        repro_spec=ReproSpec(steps=["./build.sh"], expected_signal="ASAN") if repro else None,
    )


def _decision(verdict: Verdict, *, grounded: bool) -> Decision:
    return Decision(
        decision_id="dec_0001",
        run_id=RUN_ID,
        finding_id="find_0001",
        verdict=verdict,
        rationale="근거 서술",
        evidence_refs=["ev_graph"] if grounded else [],
        produced_by=CRITIC,
    )


def test_no_evidence_refs_is_e0() -> None:
    result = gate.evaluate(_finding(refs=[]), [])

    assert result.grade is EvidenceGrade.E0
    assert not result.ambiguous


def test_static_signal_alone_is_e1() -> None:
    finding = _finding(refs=["ev_static"])
    evidences = [_evidence("ev_static", EvidenceKind.STATIC)]

    assert gate.evaluate(finding, evidences).grade is EvidenceGrade.E1


def test_failed_evidence_does_not_count() -> None:
    """`ok=False`는 실패 기록이지 근거가 아니다. `usable`이 그것을 거른다."""
    finding = _finding(refs=["ev_static"])
    evidences = [_evidence("ev_static", EvidenceKind.STATIC, ok=False)]

    assert gate.evaluate(finding, evidences).grade is EvidenceGrade.E0


def test_location_overlap_alone_is_e1() -> None:
    """정적 신호가 없어도 후보 위치와 겹치면 E1이다."""
    finding = _finding(refs=["ev_build"])
    evidences = [
        _evidence(
            "ev_build",
            EvidenceKind.BUILD,
            location=Location(file="parser.c", line_start=15),
        )
    ]

    result = gate.evaluate(finding, evidences, candidate_location=TARGET)

    assert result.predicates["location_ok"]
    assert result.grade is EvidenceGrade.E1


def test_path_and_graph_without_blocking_is_e2() -> None:
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences)

    assert result.grade is EvidenceGrade.E2
    assert not result.ambiguous


def test_blocking_missing_evidence_holds_at_e1_and_flags_ambiguous() -> None:
    """경로와 그래프는 있는데 blocking이 남았다 — E1↔E2 경계다.

    모델을 부르지 않고 E1으로 확정하되, 그 사실을 남길 수 있어야 한다.
    """
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]
    result = gate.evaluate(finding, evidences, missings=[BLOCKING])

    assert result.grade is EvidenceGrade.E1
    assert result.ambiguous


def test_upheld_with_repro_spec_is_e3() -> None:
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E3


def test_a_harness_synthesized_repro_spec_still_reaches_e3() -> None:
    """#S7: 하네스가 합성한 명세도 E3(`validated`)까지 올라간다 — 등급은 출처로 안 갈린다.
    게이트는 `repro_is_harness`로만 그 사실을 trace에 남긴다."""
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    finding = finding.model_copy(
        update={"repro_spec": finding.repro_spec.model_copy(update={"origin": ReproOrigin.HARNESS})}
    )
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E3
    assert result.predicates["repro_is_harness"] is True


def test_a_model_repro_spec_is_not_flagged_harness() -> None:
    """모델이 낸 명세(기본 `origin=model`)는 `repro_is_harness`가 거짓이고 등급은 그대로 E3다."""
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E3
    assert result.predicates["repro_is_harness"] is False


def test_no_repro_spec_is_not_flagged_harness() -> None:
    """명세가 없으면 `repro_is_harness`는 거짓이다(참칭 방지 — None을 harness로 읽지 않는다)."""
    finding = _finding(refs=["ev_static"], path=FULL_PATH, repro=False)

    result = gate.evaluate(finding, [_evidence("ev_static", EvidenceKind.STATIC)])

    assert result.predicates["repro_is_harness"] is False


def test_upheld_without_repro_spec_stays_e2() -> None:
    """E3의 조건 자체가 "재현 명세 존재"다 (architecture.md §4)."""
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E2
    assert not result.predicates["p3"]


def test_upheld_with_repro_but_no_path_is_not_e3() -> None:
    """경로 없는 upheld+repro는 E3가 아니다 (D65).

    E3은 `validated`로 승격되는데 `validated`는 스키마가 source→sink 경로를 요구한다
    (`finding.py`). 경로 없이 E3를 주면 그 전이가 `ValidationError`로 실서빙 run을 7단계에서
    죽인다 — `run_20260904_0001`이 그렇게 끝났다. `p3`가 `path_ok`를 포함하므로, 경로가 빈
    이 Finding은 E2(경로 없어 실패)를 지나 E1로 떨어진다.
    """
    finding = _finding(refs=["ev_static"], path=[], repro=True)
    evidences = [_evidence("ev_static", EvidenceKind.STATIC)]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E1
    assert not result.predicates["p3"]
    assert not result.predicates["path_ok"]
    assert result.predicates["upheld"] and result.predicates["has_repro_spec"]


def test_upheld_with_repro_and_path_but_no_graph_is_not_e3() -> None:
    """경로가 있어도 그래프 근거가 없으면 E3가 아니다 — E1에서 E3로 건너뛰지 못한다 (#154).

    6단계에서 E1으로 보류된 Finding도 상태가 `hypothesized`라 7단계에 올라온다(러너는 등급이
    아니라 상태로 고른다). `p3`가 E2의 조건을 안 보면 거기서 반박 실패 하나로 E3가 돼
    `validated`로 간다. 경로는 이어졌으므로 E1↔E2 경계(`ambiguous`)이고 E1으로 남는다.
    """
    finding = _finding(refs=["ev_static"], path=FULL_PATH, repro=True)
    evidences = [_evidence("ev_static", EvidenceKind.STATIC)]

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert result.grade is EvidenceGrade.E1
    assert not result.predicates["p3"]
    assert result.predicates["path_ok"] and not result.predicates["graph_ok"]
    assert result.predicates["upheld"] and result.predicates["has_repro_spec"]
    assert result.ambiguous


def test_upheld_with_repro_but_blocking_missing_is_not_e3() -> None:
    """blocking 근거 요청이 남아 있으면 반박 실패·재현 명세가 있어도 E3가 아니다 (#154).

    `blocking=True`는 "이 근거 없이는 Evidence Gate를 통과시키지 않는다"다(`MissingEvidence`).
    6단계에서 그것이 E2를 막는데 7단계에서 E3를 열어 주면 그 약속이 한 단계 만에 풀린다.
    """
    finding = _finding(refs=["ev_static", "ev_graph"], path=FULL_PATH, repro=True)
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
    ]

    result = gate.evaluate(
        finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)], [BLOCKING]
    )

    assert result.grade is EvidenceGrade.E1
    assert not result.predicates["p3"]
    assert not result.predicates["unblocked"]
    assert result.predicates["upheld"] and result.predicates["has_repro_spec"]
    assert result.ambiguous


def test_e3_requires_every_e2_condition_over_all_combinations() -> None:
    """E3 ⇔ E2의 세 조건 + 반박 실패 + 재현 명세 — 다섯 축의 32개 조합 전부 (#154).

    위 두 테스트는 축을 하나씩 핀으로 박는다. 이것은 성질 자체를 단언한다 — 어느 조합에서도
    E2의 조건 하나라도 빠진 채 E3가 나오지 않고, 다섯이 다 서면 E3다(좁히기만 한 것도 아니다).
    정적 근거는 늘 둬서 `p1`이 서고 런타임 근거는 안 둬서 `p4`는 안 선다 — E4는 이 성질 밖이다.
    """
    static = _evidence("ev_static", EvidenceKind.STATIC)
    graph = _evidence("ev_graph", EvidenceKind.GRAPH)
    for has_path, has_graph, unblocked, upheld, repro in product((False, True), repeat=5):
        evidences = [static, graph] if has_graph else [static]
        finding = _finding(
            refs=[ev.evidence_id for ev in evidences],
            path=FULL_PATH if has_path else None,
            repro=repro,
        )
        verdict = Verdict.UPHOLD if upheld else Verdict.NEED_EVIDENCE

        result = gate.evaluate(
            finding,
            evidences,
            [_decision(verdict, grounded=True)],
            [] if unblocked else [BLOCKING],
        )

        axes = (has_path, has_graph, unblocked, upheld, repro)
        assert (result.grade is EvidenceGrade.E3) == all(axes), axes


def test_ungrounded_rebut_normalizes_to_uphold() -> None:
    """근거 없는 반박은 반박이 아니다 (agent-contracts.md §2.3)."""
    decision = _decision(Verdict.REBUT, grounded=False)

    assert gate.normalize(decision) is Verdict.UPHOLD
    assert gate.was_normalized(decision)


def test_grounded_rebut_is_not_normalized() -> None:
    decision = _decision(Verdict.REBUT, grounded=True)

    assert gate.normalize(decision) is Verdict.REBUT
    assert not gate.was_normalized(decision)


def test_need_evidence_is_not_a_failed_rebuttal() -> None:
    decision = _decision(Verdict.NEED_EVIDENCE, grounded=False)

    assert gate.normalize(decision) is Verdict.NEED_EVIDENCE
    assert not gate.was_normalized(decision)


@pytest.mark.parametrize("exit_code", CRASH_EXIT_CODES)
@pytest.mark.parametrize("kind", RUNTIME_KINDS)
def test_runtime_evidence_is_e4(kind: EvidenceKind, exit_code: int) -> None:
    """런타임 Evidence가 **0도 미측정도 아닌** 종료 코드를 들면 E4다 (#232).

    양성 핀이다 — 아래 음성 핀 둘과 한 쌍이다. 한쪽만 있으면 `p4`를 항상 거짓으로 만드는
    변이(또는 항상 참인 원래 식)가 초록으로 남는다. 종료 코드가 비-0이라는 것은 **크래시의
    대용 지표**이고 크래시했다는 관측이 아니다 — 그 한계는 `p4`가
    배너 관측(`Evidence.crashed`, 기록뿐이다)을 안 읽고 종료 코드만 읽는다는 것이다.
    """
    finding = _finding(refs=["ev_run"])
    evidences = [_evidence("ev_run", kind, exit_code=exit_code)]

    result = gate.evaluate(finding, evidences)

    assert result.predicates["p4"]
    assert result.grade is EvidenceGrade.E4


def _e3_finding_with(runtime: Evidence) -> tuple[Finding, list[Evidence]]:
    """8단계가 게이트를 다시 돌릴 때의 입력 모양 — E3까지 올라온 Finding과 그 근거 + 런타임 근거."""
    evidences = [
        _evidence("ev_static", EvidenceKind.STATIC),
        _evidence("ev_graph", EvidenceKind.GRAPH),
        runtime,
    ]
    finding = _finding(refs=[ev.evidence_id for ev in evidences], path=FULL_PATH, repro=True)
    return finding, evidences


@pytest.mark.parametrize("kind", RUNTIME_KINDS)
def test_a_clean_exit_does_not_lift_e3_to_e4(kind: EvidenceKind) -> None:
    """도구가 떴고(`ok=True`) 종료 코드가 0이면 E4가 아니다 — E3에 남는다 (#232).

    `ok=True`는 「떴다」이지 「크래시했다」가 아니다(`container_tools.judge()`는 exit 0도
    `ok=True`로 올린다). 그 건이 `proven`이 되면 크래시 0건인 run이 재현 완료로 기록된다. 등급이
    E0이 아니라 E3에 남는 것도 문다 — 깨끗한 종료가 근거를 지우는 것은 아니다.
    """
    finding, evidences = _e3_finding_with(_evidence("ev_run", kind, exit_code=0))

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert not result.predicates["p4"]
    assert result.grade is EvidenceGrade.E3


@pytest.mark.parametrize("kind", RUNTIME_KINDS)
def test_an_unmeasured_exit_code_is_not_a_crash(kind: EvidenceKind) -> None:
    """`exit_code=None`은 **안 쟀다**이고 안 잰 것을 크래시로 읽지 않는다 (#232, CLAUDE.md §0).

    ⚠ **`exit_code != 0`으로 쓰면 이 검사가 빨개진다.** `None != 0`은 참이라 미측정이 E4가
    되는데, 위 두 검사(`test_runtime_evidence_is_e4`·`test_a_clean_exit_does_not_lift_e3_to_e4`)는
    종료 코드를 항상 정수로 넣어 **그 변이에서도 초록이다**(측정 — 이 검사의 세 인스턴스와 아래
    전수 검사만 빨개진다). 프로덕션은 항상 정수를 채우므로(`Evidence(`를 만드는 곳이
    `fake_tools`·`container_tools` 둘이고 둘 다 정수를 넘긴다) 실동작 차이는 `_evidence()`처럼
    코드를 안 넣는 테스트 픽스처에서만 난다.
    """
    finding, evidences = _e3_finding_with(_evidence("ev_run", kind, exit_code=None))

    result = gate.evaluate(finding, evidences, [_decision(Verdict.UPHOLD, grounded=True)])

    assert not result.predicates["p4"]
    assert result.grade is EvidenceGrade.E3


def test_p4_needs_a_usable_runtime_evidence_with_a_crash_exit_over_all_combinations() -> None:
    """p4 ⇔ 런타임 종류 ∧ `ok` ∧ 종료 코드가 0도 None도 아님 — 8 × 2 × 6 = 96조합 전부 (#232).

    종류 8 × ok 2 × 종료 코드 6(None·0·비-0 넷). 위 핀들은 축을 하나씩 박는다. 이것은 성질
    자체를 단언한다 — 어느 종류에서도, 실패한 기록이든 미측정이든 깨끗한 종료든 셋 중 하나라도
    빠진 채 E4가 나오지 않고, 셋이 다 서면 E4다(좁히기만 한 것도 아니다). 비-런타임 종류
    (`STATIC`·`BUILD`·`GRAPH`·`FUZZ_COVERAGE`·`DIFF`)가 비-0 종료 코드를 들어도 E4가 아닌 것도
    여기서 물린다.
    """
    for kind, ok, exit_code in product(EvidenceKind, (True, False), (None, 0, *CRASH_EXIT_CODES)):
        evidences = [_evidence("ev_run", kind, ok=ok, exit_code=exit_code)]

        result = gate.evaluate(_finding(refs=["ev_run"]), evidences)

        crashed = kind in RUNTIME_KINDS and ok and exit_code is not None and exit_code != 0
        assert result.predicates["p4"] is crashed, (kind, ok, exit_code)
        assert (result.grade is EvidenceGrade.E4) is crashed, (kind, ok, exit_code)


@pytest.mark.parametrize(
    ("last_exit_code", "is_e4"),
    [(1, True), (0, False)],
    ids=["crash-exit", "clean-exit"],
)
def test_what_judge_returns_for_asan_decides_e4_through_the_gate(
    last_exit_code: int, is_e4: bool
) -> None:
    """`judge()`가 **실제로 내는** 튜플을 `Evidence`로 감싸 게이트에 넣는다 (#232).

    `judge()`는 크래시도 깨끗한 종료도 `ok=True`로 올리고, 둘을 가르는 것은 게이트의 종료 코드
    조건이다. 두 끝을 따로 무는 검사는 있어도 이어서 무는 검사는 이것뿐이다 — 한쪽이 바뀌면
    (예: `judge()`가 런타임 도구의 `exit_code`를 `None`으로 접으면) 개별 검사는 초록인 채
    연결만 끊긴다.
    """
    ok, exit_code, error = judge("asan", [Step(0, "", ""), Step(last_exit_code, "", "")], 2)
    assert (ok, error) == (True, None)  # 전제 — 둘 다 「떴다」다
    evidences = [_evidence("ev_asan", EvidenceKind.SANITIZER, ok=ok, exit_code=exit_code)]

    result = gate.evaluate(_finding(refs=["ev_asan"]), evidences)

    assert (result.grade is EvidenceGrade.E4) is is_e4


def test_grade_comparison_is_ordered() -> None:
    assert gate.at_least(EvidenceGrade.E3, EvidenceGrade.E2)
    assert gate.at_least(EvidenceGrade.E2, EvidenceGrade.E2)
    assert not gate.at_least(EvidenceGrade.E1, EvidenceGrade.E2)


def test_evidences_for_follows_ref_order() -> None:
    """참조 순서가 곧 판정 순서다 — 순서가 흔들리면 재현성 비교가 깨진다."""
    finding = _finding(refs=["ev_b", "ev_a"])
    pool = [
        _evidence("ev_a", EvidenceKind.STATIC),
        _evidence("ev_b", EvidenceKind.GRAPH),
        _evidence("ev_other", EvidenceKind.BUILD),
    ]

    assert [e.evidence_id for e in gate.evidences_for(finding, pool)] == ["ev_b", "ev_a"]
