"""상태 전이의 단일 통로 (architecture.md §4).

**모든 상태 변경은 `apply()`/`create()`/`finish_run()`만 통과한다.** 이 세 함수가
`HarnessModel.transition()` 호출 + StateStore 저장 + trace 기록을 함께 하므로, 노드가
직접 상태를 바꾸는 경로를 만들지 않는 한 전이 로그 누락이 구조적으로 불가능하다.
`model_copy(update=...)`는 검증을 우회하므로(common.py `HarnessModel` 주의) 쓰지 않는다.

아래 표가 **전이의 정의**다. 표에 없는 `(kind, from, to, reason)` 조합은
`IllegalTransition`으로 터진다. 이 프로젝트에서 나중에 바꾸기 가장 비싼 결정이므로
설계 문서가 아니라 코드와 같은 파일에 둔다.

`from == to`인 행(C-9·F-3·F-4·F-5·F-9·F-13)도 **반드시 trace에 남긴다** — 없으면
6·7단계 통과율의 분모가 사라진다.

## Candidate

| # | 트리거 | 단계 | from | to | reason_code |
| --- | --- | --- | --- | --- | --- |
| C-1 | Scout 출력 생성 | 4 | (신규) | `candidate` | — |
| C-2 | `score >= scout_threshold` | 4 | `candidate` | `triaged` | `scout_promoted` |
| C-3 | `score < scout_threshold` | 4 | `candidate` | `queued` | `below_scout_threshold` |
| C-4 | 구조화 출력 재시도 한도 초과 | 4 | `candidate` | `queued` | `schema_parse_failed` |
| C-5 | 캡슐 확장 상한 초과 | 3·5 | `triaged` | `queued` | `capsule_expansion_exhausted` |
| C-6 | Auditor 구조화 출력 실패 | 5 | `triaged` | `queued` | `schema_parse_failed` |
| C-7 | 예산 소진으로 단계 차단 | 4·5 | `candidate`/`triaged` | `queued` | `budget_exhausted` |
| C-8 | 새 Evidence 유입 → 재평가 | — | `queued` | `candidate` | `new_evidence_requeue` |
| C-9 | 재평가 횟수 상한 초과 | — | `queued` | `queued` | `requeue_limit_reached` |

## Finding

| # | 트리거 | 단계 | from | to | reason_code |
| --- | --- | --- | --- | --- | --- |
| F-1 | Auditor 가설 생성 | 5 | (신규) | `hypothesized` | `auditor_hypothesized` |
| F-2 | Gate E0 | 6 | `hypothesized` | `queued` | `insufficient_evidence` |
| F-3 | Gate E1 (무전이) | 6 | `hypothesized` | `hypothesized` | `evidence_weak` |
| F-4 | Gate E2 → Critic (무전이) | 6 | `hypothesized` | `hypothesized` | `promoted_to_critic` |
| F-5 | 정규화 포함 `uphold` 기록 | 7 | `hypothesized` | `hypothesized` | `critic_upheld` |
| F-6 | `rebut` **이면서** `is_grounded` | 7 | `hypothesized` | `queued` | `critic_rebutted` |
| F-7 | Critic `need_evidence` | 7 | `hypothesized` | `queued` | `critic_need_evidence` |
| F-8 | Gate E3 (반박 실패 + 명세) | 7 | `hypothesized` | `validated` | `critic_upheld` |
| F-9 | 반박 실패, `repro_spec` 부재 | 7 | `hypothesized` | `hypothesized` | `repro_spec_missing` |
| F-10 | Gate E4 (재현 로그 `ok=True`) | 8 | `validated` | `proven` | `repro_confirmed` |
| F-11 | 재현 실패 | 8 | `validated` | `queued` | `repro_failed` |
| F-12 | tool_policy 위반 | 8 | `validated` | `queued` | `tool_policy_violation` |
| F-13 | 예산 소진 | 6·7·8 | 임의 | 변경 없음 | `budget_exhausted` |
| F-14 | 새 Evidence 유입 | — | `queued` | `hypothesized` | `new_evidence_requeue` |
| F-15 | 2인 검증 승인 (**자동 전이 없음**) | — | `proven` | `reported` | `human_verified` |

F-5의 `uphold`에는 **근거 없는 `rebut`을 정규화한 것**도 포함된다(gate.normalize,
agent-contracts.md §2.3). 정규화가 일어난 사건 자체는 `critic_normalized`로 따로 남는다.

F-5와 F-8은 `(from, reason)`이 같고 `to`만 다르다. 7단계에서 두 사건이 순서대로
일어나기 때문이다 — 먼저 Critic 판정을 무전이로 기록하고, 그다음 게이트가 E3이면
승격한다. 그래서 이 표의 키는 `(kind, from, to, reason)` 네 값 전부다.

F-15는 사람이 승인해야만 일어난다. 파이프라인이 자동으로 호출하는 경로를 만들지 않는다
(disclosure-policy.md).

## RunRecord

| # | 트리거 | 결과 |
| --- | --- | --- |
| R-1 | 9단계 완주 | `finished_at` 채움 |
| R-2 | `BudgetExhausted` | `finished_at` + `stopped_after=N` |
"""

from dataclasses import dataclass
from datetime import datetime

from src.interfaces.state_store import ObjectKind, StateStore
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas.candidate import Candidate
from src.schemas.enums import FindingState, ReasonCode
from src.schemas.finding import Finding
from src.schemas.run import RunRecord

# 상태를 갖는 객체. Candidate와 Finding은 하나의 상태 머신을 나눠 쓴다(enums.FindingState).
Subject = Candidate | Finding

_S = FindingState
_R = ReasonCode

# F-13의 "임의 상태". Finding이 가질 수 있는 값 전부다(finding.FindingStateValue와 같은 집합).
_FINDING_STATES = (_S.HYPOTHESIZED, _S.QUEUED, _S.VALIDATED, _S.PROVEN, _S.REPORTED)


class IllegalTransition(Exception):
    """전이 표에 없는 조합. 조용히 통과시키면 상태 머신이 문서와 갈라진다."""


@dataclass(frozen=True)
class TransitionRule:
    """위 표의 한 행. `rule`은 표의 번호이며 trace에 그대로 남는다."""

    rule: str
    kind: ObjectKind
    # `None`이면 신규 생성 행(C-1·F-1)이다.
    from_state: FindingState | None
    to_state: FindingState
    reason: ReasonCode | None


TRANSITIONS: tuple[TransitionRule, ...] = (
    # --- Candidate ---
    TransitionRule("C-1", "candidate", None, _S.CANDIDATE, None),
    TransitionRule("C-2", "candidate", _S.CANDIDATE, _S.TRIAGED, _R.SCOUT_PROMOTED),
    TransitionRule("C-3", "candidate", _S.CANDIDATE, _S.QUEUED, _R.BELOW_SCOUT_THRESHOLD),
    TransitionRule("C-4", "candidate", _S.CANDIDATE, _S.QUEUED, _R.SCHEMA_PARSE_FAILED),
    TransitionRule("C-5", "candidate", _S.TRIAGED, _S.QUEUED, _R.CAPSULE_EXPANSION_EXHAUSTED),
    TransitionRule("C-6", "candidate", _S.TRIAGED, _S.QUEUED, _R.SCHEMA_PARSE_FAILED),
    TransitionRule("C-7", "candidate", _S.CANDIDATE, _S.QUEUED, _R.BUDGET_EXHAUSTED),
    TransitionRule("C-7", "candidate", _S.TRIAGED, _S.QUEUED, _R.BUDGET_EXHAUSTED),
    TransitionRule("C-8", "candidate", _S.QUEUED, _S.CANDIDATE, _R.NEW_EVIDENCE_REQUEUE),
    TransitionRule("C-9", "candidate", _S.QUEUED, _S.QUEUED, _R.REQUEUE_LIMIT_REACHED),
    # --- Finding ---
    TransitionRule("F-1", "finding", None, _S.HYPOTHESIZED, _R.AUDITOR_HYPOTHESIZED),
    TransitionRule("F-2", "finding", _S.HYPOTHESIZED, _S.QUEUED, _R.INSUFFICIENT_EVIDENCE),
    TransitionRule("F-3", "finding", _S.HYPOTHESIZED, _S.HYPOTHESIZED, _R.EVIDENCE_WEAK),
    TransitionRule("F-4", "finding", _S.HYPOTHESIZED, _S.HYPOTHESIZED, _R.PROMOTED_TO_CRITIC),
    TransitionRule("F-5", "finding", _S.HYPOTHESIZED, _S.HYPOTHESIZED, _R.CRITIC_UPHELD),
    TransitionRule("F-6", "finding", _S.HYPOTHESIZED, _S.QUEUED, _R.CRITIC_REBUTTED),
    TransitionRule("F-7", "finding", _S.HYPOTHESIZED, _S.QUEUED, _R.CRITIC_NEED_EVIDENCE),
    TransitionRule("F-8", "finding", _S.HYPOTHESIZED, _S.VALIDATED, _R.CRITIC_UPHELD),
    TransitionRule("F-9", "finding", _S.HYPOTHESIZED, _S.HYPOTHESIZED, _R.REPRO_SPEC_MISSING),
    TransitionRule("F-10", "finding", _S.VALIDATED, _S.PROVEN, _R.REPRO_CONFIRMED),
    TransitionRule("F-11", "finding", _S.VALIDATED, _S.QUEUED, _R.REPRO_FAILED),
    TransitionRule("F-12", "finding", _S.VALIDATED, _S.QUEUED, _R.TOOL_POLICY_VIOLATION),
    # F-13 "임의 → 변경 없음"을 상태별 자기 전이로 편다.
    *(
        TransitionRule("F-13", "finding", state, state, _R.BUDGET_EXHAUSTED)
        for state in _FINDING_STATES
    ),
    TransitionRule("F-14", "finding", _S.QUEUED, _S.HYPOTHESIZED, _R.NEW_EVIDENCE_REQUEUE),
    TransitionRule("F-15", "finding", _S.PROVEN, _S.REPORTED, _R.HUMAN_VERIFIED),
)

_RuleKey = tuple[ObjectKind, FindingState | None, FindingState, ReasonCode | None]

_RULE_INDEX: dict[_RuleKey, TransitionRule] = {
    (rule.kind, rule.from_state, rule.to_state, rule.reason): rule for rule in TRANSITIONS
}


def kind_of(subject: Subject) -> ObjectKind:
    return "candidate" if isinstance(subject, Candidate) else "finding"


def subject_id(subject: Subject) -> str:
    return subject.candidate_id if isinstance(subject, Candidate) else subject.finding_id


def _lookup(
    kind: ObjectKind,
    from_state: FindingState | None,
    to_state: FindingState,
    reason: ReasonCode | None,
) -> TransitionRule:
    rule = _RULE_INDEX.get((kind, from_state, to_state, reason))
    if rule is None:
        origin = "(신규)" if from_state is None else from_state.value
        raise IllegalTransition(
            f"전이 표에 없는 조합: {kind} {origin} → {to_state.value} "
            f"(reason={reason.value if reason else None}). "
            "state_machine.py 모듈 docstring의 표를 먼저 고친다."
        )
    return rule


def apply(
    subject: Subject,
    *,
    to: FindingState,
    reason: ReasonCode,
    stage: int,
    store: StateStore,
    trace: TraceWriter,
) -> Subject:
    """전이를 검증하고, 저장하고, 기록한다. 상태 변경의 유일한 통로다.

    `to == subject.state`인 무전이도 그대로 통과시키고 trace에 남긴다 — 통과율 지표의
    분모가 여기서 나온다.
    """
    kind = kind_of(subject)
    rule = _lookup(kind, subject.state, to, reason)
    updated = subject if subject.state == to else subject.transition(state=to)
    store.put(updated)
    trace.emit(
        EventKind.TRANSITION,
        stage=stage,
        subject_kind=kind,
        subject_id=subject_id(updated),
        rule=rule.rule,
        from_state=subject.state.value,
        to_state=to.value,
        reason=reason.value,
    )
    return updated


def create(
    subject: Subject,
    *,
    reason: ReasonCode | None,
    stage: int,
    store: StateStore,
    trace: TraceWriter,
) -> Subject:
    """신규 객체(C-1·F-1)를 저장하고 생성 사실을 `transition`으로 남긴다.

    생성도 전이로 기록하는 것은 제거율의 **분모**가 "그 단계에서 만들어진 개수"이기
    때문이다(계획 §6). 생성이 trace에 없으면 분모를 셀 수 없다.
    """
    kind = kind_of(subject)
    rule = _lookup(kind, None, subject.state, reason)
    store.put(subject)
    trace.emit(
        EventKind.TRANSITION,
        stage=stage,
        subject_kind=kind,
        subject_id=subject_id(subject),
        rule=rule.rule,
        from_state=None,
        to_state=subject.state.value,
        reason=reason.value if reason else None,
    )
    return subject


def finish_run(
    record: RunRecord,
    *,
    finished_at: datetime,
    stopped_after: int | None,
    store: StateStore,
    trace: TraceWriter,
) -> RunRecord:
    """R-1/R-2. 완주면 `stopped_after=None`, 예산 소진이면 중단 단계를 함께 채운다."""
    updated = record.transition(finished_at=finished_at, stopped_after=stopped_after)
    store.put(updated)
    trace.emit(
        EventKind.TRANSITION,
        stage=stopped_after,
        subject_kind="run",
        subject_id=updated.run_id,
        rule="R-1" if stopped_after is None else "R-2",
        from_state=None,
        to_state="finished",
        reason=None if stopped_after is None else _R.BUDGET_EXHAUSTED.value,
    )
    return updated
