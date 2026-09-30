"""라우팅 정책과 계층 경계 테스트.

정책 판정은 **문서가 정한 규칙**이므로 조건별 route를 그대로 단언한다. 임계값 자체는
config에서 오므로 테스트에 숫자를 박지 않고 `config.thresholds`에서 읽어 쓴다 —
`budgets.yaml`을 조정해도 이 테스트는 여전히 옳아야 한다.
"""

import re
from pathlib import Path

import pytest

from src.config import Thresholds, load_config
from src.orchestrator import policy as policy_module
from src.orchestrator.policy import (
    Action,
    DecisionPoint,
    FixedThresholdPolicy,
    Route,
    RoutingRequest,
    UnknownDecisionPoint,
)
from src.orchestrator.stages import Stage
from src.schemas import EvidenceGrade, FindingState, ReasonCode, Verdict

ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent / "src" / "orchestrator"
AGENT_CONTRACTS = Path(__file__).resolve().parent.parent / "docs" / "agent-contracts.md"


@pytest.fixture
def thresholds() -> Thresholds:
    return load_config("dev_fake").thresholds


@pytest.fixture
def policy(thresholds: Thresholds) -> FixedThresholdPolicy:
    return FixedThresholdPolicy(thresholds)


def _request(
    point: DecisionPoint,
    stage: Stage,
    *,
    subject_kind: str = "candidate",
    **signals: object,
) -> RoutingRequest:
    return RoutingRequest(
        point=point,
        stage=stage,
        subject_kind=subject_kind,
        subject_id="cand_0001",
        signals=signals,
    )


def test_policy_handles_every_decision_point(policy: FixedThresholdPolicy) -> None:
    """결정 지점을 새로 만들고 처리기를 빼먹으면 실행 중에야 드러난다.

    `RoutingPolicy` Protocol은 데이터 멤버(`name`)를 가져 `isinstance`로 검사할 수
    없으므로, 구조 대신 **모든 지점이 처리된다**는 실질을 확인한다.
    """
    unhandled = [point for point in DecisionPoint if point not in _HANDLED_POINTS]

    assert unhandled == []
    assert isinstance(policy.name, str) and callable(policy.decide)


# 각 지점을 판정하는 데 필요한 최소 신호. 위 테스트가 이 표를 근거로 전 지점을 돌린다.
_HANDLED_POINTS: dict[DecisionPoint, dict[str, object]] = {
    DecisionPoint.SCOUT_TRIAGE: {"score": 0.9},
    DecisionPoint.CAPSULE_EXPANSION: {"expansion_count": 0},
    DecisionPoint.GATE_GRADE: {"grade": EvidenceGrade.E2.value},
    DecisionPoint.CRITIC_VERDICT: {
        "verdict": Verdict.UPHOLD.value,
        "grade": EvidenceGrade.E3.value,
        "has_repro_spec": True,
    },
    DecisionPoint.REPRO_RESULT: {"ok": True, "tool_allowed": True, "grade": EvidenceGrade.E4.value},
    DecisionPoint.REQUEUE: {"requeue_count": 0},
}


def test_every_decision_point_returns_an_action(policy: FixedThresholdPolicy) -> None:
    for point, signals in _HANDLED_POINTS.items():
        action = policy.decide(_request(point, Stage.GATE, **signals))
        assert action.policy == policy.name, point


def test_score_at_threshold_is_promoted(
    policy: FixedThresholdPolicy, thresholds: Thresholds
) -> None:
    """경계값은 `>=`로 통과한다. 어느 쪽인지가 코드에 한 곳만 있어야 한다."""
    action = policy.decide(
        _request(DecisionPoint.SCOUT_TRIAGE, Stage.SCOUT, score=thresholds.scout_threshold)
    )

    assert action.route is Route.ADVANCE
    assert action.to_state is FindingState.TRIAGED
    assert action.reason_code is ReasonCode.SCOUT_PROMOTED


def test_score_below_threshold_is_queued_not_dropped(
    policy: FixedThresholdPolicy, thresholds: Thresholds
) -> None:
    """탈락 후보는 폐기하지 않고 queued로 둔다 (architecture.md §3 Queue 규칙)."""
    action = policy.decide(
        _request(DecisionPoint.SCOUT_TRIAGE, Stage.SCOUT, score=thresholds.scout_threshold - 0.01)
    )

    assert action.to_state is FindingState.QUEUED
    assert action.reason_code is ReasonCode.BELOW_SCOUT_THRESHOLD


def test_decision_records_its_basis(
    policy: FixedThresholdPolicy, thresholds: Thresholds
) -> None:
    """정책 A/B 비교의 데이터 소스다 — 판정에 쓴 신호가 남아야 한다."""
    action = policy.decide(_request(DecisionPoint.SCOUT_TRIAGE, Stage.SCOUT, score=0.9))

    assert action.policy == "fixed_threshold"
    assert action.basis["scout_threshold"] == thresholds.scout_threshold
    assert action.basis["score"] == 0.9


def test_capsule_expansion_stops_at_configured_limit(
    policy: FixedThresholdPolicy, thresholds: Thresholds
) -> None:
    within = policy.decide(
        _request(DecisionPoint.CAPSULE_EXPANSION, Stage.RETRIEVAL, expansion_count=0)
    )
    over = policy.decide(
        _request(
            DecisionPoint.CAPSULE_EXPANSION,
            Stage.RETRIEVAL,
            expansion_count=thresholds.capsule_expansion_max,
        )
    )

    assert within.route is Route.EXPAND
    assert within.to_state is None  # 확장은 상태를 바꾸지 않는다
    assert over.reason_code is ReasonCode.CAPSULE_EXPANSION_EXHAUSTED


def test_gate_grades_map_to_transitions(policy: FixedThresholdPolicy) -> None:
    def route_for(grade: EvidenceGrade) -> Action:
        return policy.decide(_request(DecisionPoint.GATE_GRADE, Stage.GATE, grade=grade.value))

    assert route_for(EvidenceGrade.E0).reason_code is ReasonCode.INSUFFICIENT_EVIDENCE
    assert route_for(EvidenceGrade.E1).route is Route.HOLD
    assert route_for(EvidenceGrade.E1).reason_code is ReasonCode.EVIDENCE_WEAK
    assert route_for(EvidenceGrade.E2).reason_code is ReasonCode.PROMOTED_TO_CRITIC


def test_critic_uphold_needs_repro_spec_to_advance(policy: FixedThresholdPolicy) -> None:
    """F-8과 F-9를 가르는 것은 재현 명세의 유무다."""

    def decide(has_spec: bool) -> Action:
        return policy.decide(
            _request(
                DecisionPoint.CRITIC_VERDICT,
                Stage.CRITIC,
                verdict=Verdict.UPHOLD.value,
                grade=EvidenceGrade.E3.value,
                has_repro_spec=has_spec,
            )
        )

    assert decide(True).to_state is FindingState.VALIDATED
    assert decide(False).to_state is FindingState.HYPOTHESIZED
    assert decide(False).reason_code is ReasonCode.REPRO_SPEC_MISSING


def test_grounded_rebut_and_need_evidence_go_to_queue(policy: FixedThresholdPolicy) -> None:
    """rebut·need_evidence는 `Route.QUEUE`로 `QUEUED`에 간다(`state_machine.py` F-6·F-7).

    route·to_state·reason_code를 다 묻는다. 프로덕션에서 `Action.route`를 읽는 곳은 추적
    기록(`context.py`)과 retrieval의 `EXPAND` 분기뿐이라 route만 틀리면 실행 경로가 안
    바뀐다 — 그래서 여기서 직접 묻는다.
    """

    def decide(verdict: Verdict) -> Action:
        return policy.decide(
            _request(
                DecisionPoint.CRITIC_VERDICT,
                Stage.CRITIC,
                verdict=verdict.value,
                grade=EvidenceGrade.E2.value,
                has_repro_spec=True,
            )
        )

    rebut = decide(Verdict.REBUT)
    need_evidence = decide(Verdict.NEED_EVIDENCE)
    assert rebut.reason_code is ReasonCode.CRITIC_REBUTTED
    assert need_evidence.reason_code is ReasonCode.CRITIC_NEED_EVIDENCE
    for action in (rebut, need_evidence):
        assert action.route is Route.QUEUE
        assert action.to_state is FindingState.QUEUED


def test_tool_policy_violation_outranks_repro_result(policy: FixedThresholdPolicy) -> None:
    action = policy.decide(
        _request(DecisionPoint.REPRO_RESULT, Stage.VALIDATE, ok=True, tool_allowed=False)
    )

    assert action.reason_code is ReasonCode.TOOL_POLICY_VIOLATION


def test_repro_result_maps_to_proven_or_queued(policy: FixedThresholdPolicy) -> None:
    confirmed = policy.decide(
        _request(
            DecisionPoint.REPRO_RESULT,
            Stage.VALIDATE,
            ok=True,
            tool_allowed=True,
            grade=EvidenceGrade.E4.value,
        )
    )
    failed = policy.decide(
        _request(DecisionPoint.REPRO_RESULT, Stage.VALIDATE, ok=False, tool_allowed=True)
    )

    assert confirmed.to_state is FindingState.PROVEN
    assert failed.to_state is FindingState.QUEUED


def test_repro_result_grade_gates_promotion_to_proven(policy: FixedThresholdPolicy) -> None:
    """F-10과 F-10b를 가르는 것은 게이트 등급이다 — E4 미만이면 재현에 성공해도 proven이 아니다."""

    def decide(grade: EvidenceGrade) -> Action:
        return policy.decide(
            _request(
                DecisionPoint.REPRO_RESULT,
                Stage.VALIDATE,
                ok=True,
                tool_allowed=True,
                grade=grade.value,
            )
        )

    assert decide(EvidenceGrade.E4).to_state is FindingState.PROVEN
    assert decide(EvidenceGrade.E3).to_state is FindingState.VALIDATED
    assert decide(EvidenceGrade.E3).reason_code is ReasonCode.EVIDENCE_BELOW_PROVEN


def test_requeue_limit_keeps_object_queued(
    policy: FixedThresholdPolicy, thresholds: Thresholds
) -> None:
    """상한을 넘겨도 폐기하지 않는다 (계획 D-i)."""
    over = policy.decide(
        _request(DecisionPoint.REQUEUE, Stage.SCOUT, requeue_count=thresholds.requeue_max)
    )

    assert over.route is Route.HOLD
    assert over.to_state is FindingState.QUEUED
    assert over.reason_code is ReasonCode.REQUEUE_LIMIT_REACHED


def test_requeue_target_depends_on_subject_kind(policy: FixedThresholdPolicy) -> None:
    """Candidate는 candidate로, Finding은 hypothesized로 돌아간다 (C-8 / F-14)."""
    candidate = policy.decide(
        _request(DecisionPoint.REQUEUE, Stage.SCOUT, subject_kind="candidate", requeue_count=0)
    )
    finding = policy.decide(
        _request(DecisionPoint.REQUEUE, Stage.GATE, subject_kind="finding", requeue_count=0)
    )

    assert candidate.to_state is FindingState.CANDIDATE
    assert finding.to_state is FindingState.HYPOTHESIZED


def test_missing_signal_raises_instead_of_defaulting(policy: FixedThresholdPolicy) -> None:
    """신호가 없으면 기본값으로 넘기지 않는다 — 조용한 오라우팅이 더 비싸다."""
    with pytest.raises(UnknownDecisionPoint):
        policy.decide(_request(DecisionPoint.SCOUT_TRIAGE, Stage.SCOUT))


def test_orchestrator_source_has_no_adapter_import() -> None:
    """오케스트레이터는 구체 어댑터를 직접 import 하지 않는다 (engineering-guide.md §3).

    이것이 깨지면 "구현체 교체가 설정만으로 가능"이라는 주장이 성립하지 않는다.
    조립은 `src/adapters/factory.py` 한 곳에서만 한다.
    """
    offenders = [
        path.name
        for path in sorted(ORCHESTRATOR_DIR.rglob("*.py"))
        if "src.adapters" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def _policy_names(text: str) -> set[str]:
    """문서가 `policy.<이름>`·`FixedThresholdPolicy.<이름>`으로 부르는 밑줄 이름(뒤 `()`는 뗀다)."""
    names = set()
    for token in re.findall(r"`([^`\n]+)`", text):
        parts = token.removesuffix("()").split(".")
        if parts[0] in {"policy", "FixedThresholdPolicy"} and parts[-1].startswith("_"):
            names.add(parts[-1])
    return names


def _absent_from_policy(names: set[str]) -> list[str]:
    """`policy.py`에 없는 이름 — 클래스 속성(처리기)도 모듈 속성(모듈 수준 함수·표)도 아닌 것."""
    return sorted(
        name
        for name in names
        if not hasattr(FixedThresholdPolicy, name) and not hasattr(policy_module, name)
    )


def test_agent_contracts_names_a_real_policy_handler() -> None:
    """agent-contracts가 부르는 정책 처리기 이름은 `policy.py`에 실재한다(CLAUDE.md §2.1).

    **사고 —** #346의 리뷰 반영 커밋(41924cc)이 없는 이름 `policy._critic`을 적었다(처리기는
    `FixedThresholdPolicy._critic_verdict`). 어느 테스트도 그 이름을 안 봐서 CI가 초록이었다.
    이름이 있는지만 본다. 문서가 말한 행선(`need_evidence` → `QUEUED`)은
    `test_grounded_rebut_and_need_evidence_go_to_queue`가 route·to_state·reason_code로 묻는다.
    안 보는 것: 백틱 밖의 이름, 다른 접두(`runner.` 등)·접두 대소문자, 밑줄 없는 이름, 줄바꿈으로
    끊긴 백틱, 접두 없는 단독 이름(`_requeue`처럼), 다른 문서. 인자를 단 호출형
    (`policy._x(request)`)과 인스턴스 속성은 실재해도 없는 이름으로 읽는다 — 오탐은 빨갛게
    드러나므로 그 꼴을 문서에 쓸 때 고친다.
    """
    named = _policy_names(AGENT_CONTRACTS.read_text(encoding="utf-8"))

    assert named, "문서가 정책 처리기를 부르는 곳이 없다 — 이 검사가 아무것도 안 본다"
    missing = _absent_from_policy(named)
    assert not missing, f"agent-contracts.md가 policy.py에 없는 이름을 부른다: {missing}"


def test_the_policy_name_check_flags_the_incident_and_passes_real_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """위 검사의 오염 핀 — 양성은 실제 사고, 음성은 `policy.py`에 있는 이름의 표기다.

    뒤 `()`를 떼지 않는 판은 호출형 표본을, 모듈 속성을 안 보는 판은 모듈 속성 표본을 없는
    이름으로 읽는다. 철자 핀이라 여기 없는 표기(백틱 밖·다른 접두)는 안 묻는다.
    """
    # 양성 — docs/agent-contracts.md:169 (41924cc)
    assert _absent_from_policy(_policy_names("`policy._critic`에서")) == ["_critic"]
    # 합성 표본(저장소 표기가 아니다): 모듈 속성 인식만 시험하려고 `policy` 모듈에 이름을 잠깐
    # 단다. 프로덕션 비공개 이름에 묶으면 그 이름을 바꾸기만 해도 이 핀이 빨개진다.
    monkeypatch.setattr(policy_module, "_probe_module_attr", object(), raising=False)
    for sample in (
        "`policy._repro_result()`",  # docs/architecture.md:217
        "`FixedThresholdPolicy._repro_result()`",  # docs/open-questions/D/D18.md:5
        "`policy._scout_triage`",  # docs/agent-contracts.md:115
        "`policy._probe_module_attr`",  # 합성 — 바로 위 monkeypatch
    ):
        assert _policy_names(sample), sample
        assert _absent_from_policy(_policy_names(sample)) == [], sample
