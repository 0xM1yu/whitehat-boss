"""라우팅 정책 (계획 §4, ADR 0003).

노드는 임계값을 직접 비교하지 않는다. `ctx.decide(request)`로 `Action`을 받고, 그
`Action`을 `state_machine.apply()`에 넘긴다. 임계값이 노드에 흩어지면
engineering-guide.md §8.5("임계값은 config에서")가 코드 리뷰로만 지켜지고, 정책을
바꿔 보려면 노드를 전부 고쳐야 한다.

**`src/interfaces/`가 아니라 여기 있다.** 외부 시스템 경계(ModelClient·StateStore…)와
내부 정책 경계는 성격이 다르고, PR 1에서 정의한 인터페이스 표면을 넓히지 않는다.

모든 결정은 `policy_decision` 이벤트로 trace에 남는다(RunContext.decide). 어떤 정책이
어떤 신호를 보고 어디로 보냈는지가 남아야 나중에 정책 A/B 비교의 데이터가 된다.
v1 구현체는 `FixedThresholdPolicy` 하나이고, 그 기록이 비교의 기준선이다.
"""

from enum import StrEnum
from typing import Protocol

from pydantic import Field

from src.config import Thresholds
from src.interfaces.state_store import ObjectKind
from src.schemas.common import HarnessModel, Identifier, NonEmptyStr
from src.schemas.enums import EvidenceGrade, FindingState, ReasonCode, ScoutSignal, Verdict

# `basis`·`signals`에 담을 수 있는 값. 판정에 실제로 쓴 신호만 넣는다.
Signal = float | int | str | bool | None


class DecisionPoint(StrEnum):
    """정책이 개입하는 지점. 파이프라인 단계와 1:1이 아니다 — 한 단계에 둘 이상 있다."""

    SCOUT_TRIAGE = "scout_triage"
    CAPSULE_EXPANSION = "capsule_expansion"
    GATE_GRADE = "gate_grade"
    CRITIC_VERDICT = "critic_verdict"
    REPRO_RESULT = "repro_result"
    REQUEUE = "requeue"


class Route(StrEnum):
    """정책이 고른 방향. 상태 전이의 적법성은 state_machine이 따로 검증한다."""

    ADVANCE = "advance"  # 다음 단계로
    HOLD = "hold"  # 상태 유지, 기록만
    QUEUE = "queue"  # queued로
    EXPAND = "expand"  # 캡슐 확장 후 재시도
    STOP = "stop"  # run 중단 (예산 소진. 러너가 처리한다 — budget.py)


class RoutingRequest(HarnessModel):
    """정책에 넘기는 질문. 정책은 이 안에 있는 것만 보고 판단한다."""

    point: DecisionPoint
    stage: int = Field(ge=0)
    subject_kind: ObjectKind
    subject_id: Identifier
    signals: dict[str, Signal] = Field(default_factory=dict)


class Action(HarnessModel):
    """정책의 답. `to_state`가 `None`이면 상태를 바꾸지 않는다."""

    route: Route
    to_state: FindingState | None = None
    reason_code: ReasonCode | None = None
    # trace에 남길 정책 이름. 정책을 갈아 끼웠을 때 결과를 구분하는 유일한 열이다.
    policy: NonEmptyStr
    basis: dict[str, Signal] = Field(default_factory=dict)


class RoutingPolicy(Protocol):
    """결정 지점 하나를 판정한다."""

    name: str

    def decide(self, request: RoutingRequest) -> Action: ...


class UnknownDecisionPoint(Exception):
    """정책이 모르는 지점이거나 신호가 빠졌다. 기본값으로 넘기지 않는다 —
    조용한 오라우팅은 지표를 오염시키고 원인을 찾을 수 없게 만든다."""


def _signal(request: RoutingRequest, key: str) -> Signal:
    if key not in request.signals:
        raise UnknownDecisionPoint(
            f"{request.point.value} 판정에 필요한 신호 {key!r}가 없다: {sorted(request.signals)}"
        )
    return request.signals[key]


class FixedThresholdPolicy:
    """v1 정책. config의 `Thresholds`만 보고 결정한다.

    임계값을 생성자에서 받는 것이 요점이다 — 정책이 config를 다시 읽지 않으므로 한
    run에서 쓰인 값이 `RunRecord.thresholds`에 박제된 값과 반드시 같다.
    """

    name = "fixed_threshold"

    def __init__(self, thresholds: Thresholds) -> None:
        self._t = thresholds

    def decide(self, request: RoutingRequest) -> Action:
        handler = _HANDLERS.get(request.point)
        if handler is None:
            raise UnknownDecisionPoint(f"처리기가 없는 결정 지점: {request.point.value}")
        return handler(self, request)

    # --- 지점별 판정. 각각 state_machine.py 표의 어느 행으로 가는지 주석에 적는다. ---

    def _scout_triage(self, request: RoutingRequest) -> Action:
        """C-2 / C-3.

        **판정은 최종 신호다**(ADR 0012 결정 2) — `signals`에 `"signal"`이 있으면(v3) 점수를
        보지 않고 그 신호로 가른다. review는 약한 것도 승격 대상이다(v3 프롬프트 계약). 신호 경로의
        `basis`는 `{"signal": ...}`뿐이다 - 점수는 판정에 안 쓰이고 `Candidate.score`에만 남는다.
        없으면(v1·v2, 또는 첫 reason code가 세 신호 밖) 지금 점수 경로다.
        """
        signal = request.signals.get("signal")
        if signal is not None:
            basis: dict[str, Signal] = {"signal": signal}
            if signal == ScoutSignal.REVIEW.value:
                return self._act(
                    Route.ADVANCE, FindingState.TRIAGED, ReasonCode.SCOUT_PROMOTED, basis
                )
            if signal == ScoutSignal.REJECT.value:
                return self._act(Route.QUEUE, FindingState.QUEUED, ReasonCode.SCOUT_REJECT, basis)
            # 남은 값은 `triage_need_evidence`뿐이다 — 신호가 있는데 review·reject가 아니면
            # NE 루프가 소진돼 이 판정에 왔다는 뜻이다(좌석 집계가 그 경우에만 signal을 싣는다).
            return self._act(
                Route.QUEUE, FindingState.QUEUED, ReasonCode.NEED_EVIDENCE_UNRESOLVED, basis
            )
        score = float(_signal(request, "score"))
        limit = self._t.scout_threshold
        basis = {"score": score, "scout_threshold": limit}
        if score >= limit:
            return self._act(Route.ADVANCE, FindingState.TRIAGED, ReasonCode.SCOUT_PROMOTED, basis)
        return self._act(Route.QUEUE, FindingState.QUEUED, ReasonCode.BELOW_SCOUT_THRESHOLD, basis)

    def _capsule_expansion(self, request: RoutingRequest) -> Action:
        """C-5. 상한 이내면 확장하고(상태 변경 없음) 넘으면 queued로 둔다."""
        count = int(_signal(request, "expansion_count"))
        limit = self._t.capsule_expansion_max
        basis: dict[str, Signal] = {"expansion_count": count, "capsule_expansion_max": limit}
        if count < limit:
            return self._act(Route.EXPAND, None, None, basis)
        return self._act(
            Route.QUEUE, FindingState.QUEUED, ReasonCode.CAPSULE_EXPANSION_EXHAUSTED, basis
        )

    def _gate_grade(self, request: RoutingRequest) -> Action:
        """F-2 / F-3 / F-4. 6단계에서 등급을 전이로 옮긴다."""
        grade = EvidenceGrade(_signal(request, "grade"))
        basis: dict[str, Signal] = {"grade": grade.value}
        if grade is EvidenceGrade.E0:
            return self._act(
                Route.QUEUE, FindingState.QUEUED, ReasonCode.INSUFFICIENT_EVIDENCE, basis
            )
        if grade is EvidenceGrade.E1:
            return self._act(Route.HOLD, FindingState.HYPOTHESIZED, ReasonCode.EVIDENCE_WEAK, basis)
        return self._act(
            Route.ADVANCE, FindingState.HYPOTHESIZED, ReasonCode.PROMOTED_TO_CRITIC, basis
        )

    def _critic_verdict(self, request: RoutingRequest) -> Action:
        """F-5 / F-6 / F-7 / F-8 / F-9.

        `verdict`는 **정규화된** 값이다(gate.normalize). 근거 없는 rebut을 여기서 다시
        판정하면 정규화 지점이 둘이 되고 `critic_normalized` 지표의 분자가 갈라진다.
        ⏵ 2026-10-13 (ADR 0015) — 근거 없는 rebut은 `need_evidence`로 정규화되므로 여기서
        모델이 낸 `need_evidence`와 같은 길(F-7)을 탄다.
        """
        verdict = Verdict(_signal(request, "verdict"))
        grade = EvidenceGrade(_signal(request, "grade"))
        has_spec = bool(_signal(request, "has_repro_spec"))
        basis: dict[str, Signal] = {
            "verdict": verdict.value,
            "grade": grade.value,
            "has_repro_spec": has_spec,
        }
        if verdict is Verdict.NEED_EVIDENCE:
            return self._act(
                Route.QUEUE, FindingState.QUEUED, ReasonCode.CRITIC_NEED_EVIDENCE, basis
            )
        if verdict is Verdict.REBUT:
            return self._act(Route.QUEUE, FindingState.QUEUED, ReasonCode.CRITIC_REBUTTED, basis)
        if not has_spec:
            return self._act(
                Route.HOLD, FindingState.HYPOTHESIZED, ReasonCode.REPRO_SPEC_MISSING, basis
            )
        if grade in (EvidenceGrade.E3, EvidenceGrade.E4):
            return self._act(Route.ADVANCE, FindingState.VALIDATED, ReasonCode.CRITIC_UPHELD, basis)
        return self._act(Route.HOLD, FindingState.HYPOTHESIZED, ReasonCode.CRITIC_UPHELD, basis)

    def _repro_result(self, request: RoutingRequest) -> Action:
        """F-10 / F-10b / F-11 / F-12."""
        ok = bool(_signal(request, "ok"))
        allowed = bool(_signal(request, "tool_allowed"))
        basis: dict[str, Signal] = {"ok": ok, "tool_allowed": allowed}
        if not allowed:
            return self._act(
                Route.QUEUE, FindingState.QUEUED, ReasonCode.TOOL_POLICY_VIOLATION, basis
            )
        if not ok:
            return self._act(Route.QUEUE, FindingState.QUEUED, ReasonCode.REPRO_FAILED, basis)
        # 재현이 실패하면 등급은 무의미하므로 ok=True일 때만 읽는다 — basis에는 실제로
        # 판정에 쓴 신호만 남아야 한다(`Signal` 정의 위 주석). ⚠ 줄 번호로 인용하지
        # 않는다 — 처음 쓸 때 "모듈 docstring 11-13행"이라 적었는데 그 세 줄은 trace
        # 기록 얘기였다(§2.1).
        grade = EvidenceGrade(_signal(request, "grade"))
        basis["grade"] = grade.value
        if grade is EvidenceGrade.E4:
            return self._act(
                Route.ADVANCE, FindingState.PROVEN, ReasonCode.REPRO_CONFIRMED, basis
            )
        return self._act(
            Route.HOLD, FindingState.VALIDATED, ReasonCode.EVIDENCE_BELOW_PROVEN, basis
        )

    def _requeue(self, request: RoutingRequest) -> Action:
        """C-8 / C-9 / F-14. 상한을 넘겨도 폐기하지 않고 queued를 유지한다(계획 D-i)."""
        count = int(_signal(request, "requeue_count"))
        limit = self._t.requeue_max
        basis: dict[str, Signal] = {"requeue_count": count, "requeue_max": limit}
        if count >= limit:
            return self._act(
                Route.HOLD, FindingState.QUEUED, ReasonCode.REQUEUE_LIMIT_REACHED, basis
            )
        back_to = (
            FindingState.CANDIDATE
            if request.subject_kind == "candidate"
            else FindingState.HYPOTHESIZED
        )
        return self._act(Route.ADVANCE, back_to, ReasonCode.NEW_EVIDENCE_REQUEUE, basis)

    def _act(
        self,
        route: Route,
        to_state: FindingState | None,
        reason: ReasonCode | None,
        basis: dict[str, Signal],
    ) -> Action:
        return Action(
            route=route, to_state=to_state, reason_code=reason, policy=self.name, basis=basis
        )


_HANDLERS = {
    DecisionPoint.SCOUT_TRIAGE: FixedThresholdPolicy._scout_triage,
    DecisionPoint.CAPSULE_EXPANSION: FixedThresholdPolicy._capsule_expansion,
    DecisionPoint.GATE_GRADE: FixedThresholdPolicy._gate_grade,
    DecisionPoint.CRITIC_VERDICT: FixedThresholdPolicy._critic_verdict,
    DecisionPoint.REPRO_RESULT: FixedThresholdPolicy._repro_result,
    DecisionPoint.REQUEUE: FixedThresholdPolicy._requeue,
}
