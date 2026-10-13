"""상태 전이의 단일 통로 (architecture.md §4).

**모든 상태 변경은 `apply()`/`create()`/`finish_run()`만 통과한다.** 이 세 함수가
`HarnessModel.transition()` 호출 + StateStore 저장 + trace 기록을 함께 하므로, 노드가
직접 상태를 바꾸는 경로를 만들지 않는 한 전이 로그 누락이 구조적으로 불가능하다.
`model_copy(update=...)`는 검증을 우회하므로(common.py `HarnessModel` 주의) 쓰지 않는다.

아래 표가 **전이의 정의**다. 표에 없는 `(kind, from, to, reason)` 조합은
`IllegalTransition`으로 터진다. 이 프로젝트에서 나중에 바꾸기 가장 비싼 결정이므로
설계 문서가 아니라 코드와 같은 파일에 둔다.

`from == to`인 행(C-9·F-3·F-4·F-5·F-9·F-10b·F-13)도 **반드시 trace에 남긴다** — 없으면
6·7단계 통과율의 분모가 사라진다.

## Candidate

| # | 트리거 | 단계 | from | to | reason_code |
| --- | --- | --- | --- | --- | --- |
| C-1 | Scout 출력 생성 | 4 | (신규) | `candidate` | — |
| C-2 | `score >= scout_threshold` | 4 | `candidate` | `triaged` | `scout_promoted` |
| C-3 | `score < scout_threshold` | 4 | `candidate` | `queued` | `below_scout_threshold` |
| C-3 | v3 신호 `triage_reject`(`signal_of`) | 4 | `candidate` | `queued` | `scout_reject` |
| C-3 | v3 NE 미해결(루프 소진) | 4 | `candidate` | `queued` | `need_evidence_unresolved` |
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
| F-5 | `uphold` 기록 | 7 | `hypothesized` | `hypothesized` | `critic_upheld` |
| F-6 | `rebut` **이면서** `is_grounded` | 7 | `hypothesized` | `queued` | `critic_rebutted` |
| F-7 | Critic `need_evidence` | 7 | `hypothesized` | `queued` | `critic_need_evidence` |
| F-8 | Gate E3 (E2 + 반박 실패 + 명세) | 7 | `hypothesized` | `validated` | `critic_upheld` |
| F-9 | 반박 실패, `repro_spec` 부재 | 7 | `hypothesized` | `hypothesized` | `repro_spec_missing` |
| F-10 | Gate E4 (`ok=True` · 종료 코드 비-0) | 8 | `validated` | `proven` | `repro_confirmed` |
| F-10b | 재현 성공, E4 미만 (무전이) | 8 | `validated` | `validated` | `evidence_below_proven` |
| F-11 | 재현 실패 | 8 | `validated` | `queued` | `repro_failed` |
| F-12 | tool_policy 위반 | 8 | `validated` | `queued` | `tool_policy_violation` |
| F-13 | 예산 소진 | 6·7·8 | 임의 | 변경 없음 | `budget_exhausted` |
| F-14 | 새 Evidence 유입 | — | `queued` | `hypothesized` | `new_evidence_requeue` |
| F-15 | 2인 검증 승인 (**자동 전이 없음**) | — | `proven` | `reported` | `human_verified` |

⏵ 2026-10-13 (이슈 #326) - 8단계에는 이 표에 **규칙 번호가 없는 출구**가 하나 더 있다: 재현을
시도하지 못한 건(`poc` 신호 · 도구 층이 측정을 못 낸 `ok=False`)은 전이도 `policy_decision`도
없이 `validated`에 남고 trace `repro_not_measured`만 낸다 - F-10b와 달리 `from == to` 행이
**없다.**

F-7의 `need_evidence`에는 **근거 없는 `rebut`을 정규화한 것**도 포함된다(gate.normalize,
agent-contracts.md §2.3). 정규화가 일어난 사건 자체는 사유와 함께 `critic_normalized`로 따로 남는다.
⏵ 2026-10-13 (ADR 0015) — 정규화 목적지가 F-5의 `uphold`였다.

F-5와 F-8은 `(from, reason)`이 같고 `to`만 다르다. 7단계에서 두 사건이 순서대로
일어나기 때문이다 — 먼저 Critic 판정을 무전이로 기록하고, 그다음 게이트가 E3이면
승격한다. 그래서 이 표의 키는 `(kind, from, to, reason)` 네 값 전부다.

F-15는 사람이 승인해야만 일어난다. 파이프라인이 자동으로 호출하는 경로를 만들지 않는다
(disclosure-policy.md).

## RunRecord

| # | 트리거 | 결과 | reason_code |
| --- | --- | --- | --- |
| R-1 | 9단계 완주 | `finished_at` 채움 | — |
| R-2 | 9단계 전 종료 | `finished_at` + `stopped_after=N` | 아래 중 하나 |

R-2의 사유는 `finish_run()`이 정하지 않고 **호출자가 넘긴다.** ⏵ **2026-10-02 — 표가 낡아서
고쳤다**(전 판은 *"세 경로"*라 적었는데 그동안 다섯이 늘었다. 그 줄은 2026-08-11 이후 안 고쳤고
**CI가 이 표를 강제하지 않는다** — `run_pipeline`의 except 사슬이 정본이다).

| 사유 | 트리거 |
| --- | --- |
| `budget_exhausted` | `BudgetExhausted`가 올라옴 (architecture.md §5) |
| `user_stop_requested` | 사용자가 `--stop-after N`으로 자름 |
| `dry_run` | `--dry-run`이 3단계에서 자름 (`DRY_RUN_LAST_STAGE`) |
| `model_swap_failed` | `ModelSwapError` — 다음 단계 서버를 **띄우지 못했다** |
| `model_gateway_unavailable` | `ModelGatewayError` — 후보 하나의 답이 아닌 것(아래 문단) |
| `consecutive_target_failures` | `_TooManyDrops` — 서버는 답하는데 후보가 계속 죽는다 |
| `schema_parse_failed` | `SchemaParseError`가 가드 없는 경로에서 올라옴 |
| `unforeseen_failure` | **그 밖 전부** — `_analyze`의 예상 밖 예외(최후 방어선이 다시 던진다) |

`model_gateway_unavailable`은 「서버가 죽었다」보다 넓다. `ModelGatewayError` 중 **후보 하나의 답이
아닌 것 전부**(`runner._is_droppable`이 거짓인 것)다 — 상태 코드 없음(연결 끊김·타임아웃·비-JSON·
비-UTF-8·모양이 다른 200)·5xx·`400·413·422` 밖의 4xx(401·404·429 등). 이들을 **같은 사유 코드로**
적고, 무엇이었는지는 trace의 `model_call.error_detail`이 가른다.

앞의 셋(`budget_exhausted`·`user_stop_requested`·`dry_run`)을 하나로 묶지 않는 이유는
evaluation.md가 중단 사유를 조건 간 비교 지표로 쓰기 때문이다. 묶으면 예산을 0.03%만 쓴 run이
"예산 초과로 중단됨"으로 읽힌다. 그 셋 중 뒤의 둘도
따로 두는데, `--dry-run --stop-after 5`처럼 동시에 올 수 있어 사유가 하나면 어느 쪽이
상한을 결정했는지가 사라지기 때문이다 — 판정은 `RunSpec.stop_at()`이 한다.

`stopped_after`와 `reason`은 함께 채워지거나 함께 비어야 하며, `finish_run()`이 그것을
강제한다. 이 둘이 갈라진 것이 R-2가 무조건 `budget_exhausted`를 적던 결함의 원인이다.
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
    # v3 신호 판정(ADR 0012 결정 2) — 같은 (from, to)에 사유만 늘린다. 전이는 추가하지
    # 않는다 — C-7이 두 `from_state`를 같은 사유로 쓰는 선례를 뒤집어, 같은 번호에
    # 사유를 늘리는 쪽을 썼다.
    TransitionRule("C-3", "candidate", _S.CANDIDATE, _S.QUEUED, _R.SCOUT_REJECT),
    TransitionRule("C-3", "candidate", _S.CANDIDATE, _S.QUEUED, _R.NEED_EVIDENCE_UNRESOLVED),
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
    TransitionRule("F-10b", "finding", _S.VALIDATED, _S.VALIDATED, _R.EVIDENCE_BELOW_PROVEN),
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
    reason: ReasonCode | None,
    store: StateStore,
    trace: TraceWriter,
) -> RunRecord:
    """R-1/R-2. 완주면 둘 다 `None`, 중단이면 단계와 사유를 함께 받는다.

    **사유를 여기서 만들지 않고 호출자에게서 받는다.** 예전에는 `stopped_after`가 차 있으면
    무조건 `budget_exhausted`로 적었는데, 그러면 사용자가 `--stop-after`로 자른 run과
    예산이 실제로 소진된 run이 기록에서 구별되지 않는다. 중단 사유는 evaluation.md가
    조건 간 비교에 쓰는 지표이므로, 틀린 값이 조용히 들어가면 존재하지 않는 결론이 나온다.
    """
    if (stopped_after is None) != (reason is None):
        # 이번 결함이 정확히 두 값이 갈라진 것이다. 한쪽만 채우는 호출부를 여기서 막는다.
        raise ValueError(
            "stopped_after와 reason은 함께 채워지거나 함께 비어야 한다: "
            f"stopped_after={stopped_after}, reason={reason}"
        )
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
        reason=reason.value if reason else None,
    )
    return updated
