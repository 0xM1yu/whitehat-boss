"""상태 머신·큐 계약 테스트.

**전이 결과 개수 같은 측정값은 단언하지 않는다.** 여기서 고정하는 것은 전이 표의
무결성, "모든 전이가 trace에 남는다"는 계약, 그리고 표에 없는 조합이 터진다는 것뿐이다.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.adapters.memory_store import InMemoryStateStore
from src.orchestrator import state_machine
from src.orchestrator.queue import QueueFull, WorkQueue
from src.orchestrator.stages import Stage
from src.orchestrator.state_machine import TRANSITIONS, IllegalTransition
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas import (
    Candidate,
    Finding,
    FindingState,
    Location,
    PathStep,
    PathStepKind,
    ProducedBy,
    ReasonCode,
    ReproSpec,
    Role,
)
from src.schemas.run import Budget, RunRecord, Sampling

RUN_ID = "run_20260901_0001"
STARTED_AT = datetime(2026, 9, 1, tzinfo=UTC)
PRODUCED_BY = ProducedBy(role=Role.AUDITOR, model="fake", prompt_version="v1")


def _writer() -> TraceWriter:
    return TraceWriter(RUN_ID, clock=lambda: STARTED_AT)


def _candidate(state: FindingState = FindingState.CANDIDATE) -> Candidate:
    return Candidate(
        candidate_id="cand_0001",
        run_id=RUN_ID,
        artifact_id="art_0001",
        location=Location(file="parser.c", symbol="parse_header"),
        score=0.7,
        state=state,
    )


def _finding(state: FindingState = FindingState.HYPOTHESIZED, *, backed: bool = False) -> Finding:
    extra = {}
    if backed:
        extra = {
            "evidence_refs": ["ev_0001"],
            "path": [
                PathStep(step=PathStepKind.SOURCE, symbol="read_input"),
                PathStep(step=PathStepKind.SINK, symbol="memcpy"),
            ],
            "repro_spec": ReproSpec(steps=["./build.sh"], expected_signal="ASAN"),
        }
    return Finding(
        finding_id="find_0001",
        run_id=RUN_ID,
        candidate_id="cand_0001",
        title="검증 없는 memcpy",
        cwe="CWE-787",
        state=state,
        confidence=0.6,
        produced_by=PRODUCED_BY,
        **extra,
    )


def _run_record() -> RunRecord:
    return RunRecord(
        run_id=RUN_ID,
        condition="B3prime@pipeline",
        repo_commit="0" * 40,
        repo_dirty=False,
        source_hash="sha256:" + "0" * 64,
        model_checksums={},
        prompt_versions={},
        tool_images={},
        tool_runner="fake",
        device="atom",
        budget=Budget(input_tokens=10, output_tokens=10, gpu_seconds=0),
        thresholds={},
        seed=1,
        sampling=Sampling(temperature=0.2, top_p=0.95),
        kb_snapshot="kb-v1",
        started_at=STARTED_AT,
    )


def test_transition_table_has_no_duplicate_keys() -> None:
    """(kind, from, to, reason)가 겹치면 한 행이 조용히 가려진다.

    F-5와 F-8은 `to`만 다르므로 키에서 `to`가 빠지면 여기서 잡힌다.
    """
    keys = {(r.kind, r.from_state, r.to_state, r.reason) for r in TRANSITIONS}
    assert len(keys) == len(TRANSITIONS)


def test_apply_records_transition_and_persists() -> None:
    store, trace = InMemoryStateStore(), _writer()

    updated = state_machine.apply(
        _candidate(),
        to=FindingState.TRIAGED,
        reason=ReasonCode.SCOUT_PROMOTED,
        stage=Stage.SCOUT,
        store=store,
        trace=trace,
    )

    assert updated.state is FindingState.TRIAGED
    assert store.get("candidate", "cand_0001").state is FindingState.TRIAGED
    (event,) = [e for e in trace.events if e.kind is EventKind.TRANSITION]
    assert event.data["rule"] == "C-2"
    assert event.data["from_state"] == "candidate"
    assert event.data["to_state"] == "triaged"
    assert event.stage == Stage.SCOUT


def test_self_transition_is_still_recorded() -> None:
    """`from == to`도 기록한다 — 6·7단계 통과율의 분모가 여기서 나온다."""
    trace = _writer()

    updated = state_machine.apply(
        _finding(),
        to=FindingState.HYPOTHESIZED,
        reason=ReasonCode.EVIDENCE_WEAK,
        stage=Stage.GATE,
        store=InMemoryStateStore(),
        trace=trace,
    )

    assert updated.state is FindingState.HYPOTHESIZED
    assert [e.data["rule"] for e in trace.events if e.kind is EventKind.TRANSITION] == ["F-3"]


def test_unknown_reason_for_state_raises() -> None:
    with pytest.raises(IllegalTransition):
        state_machine.apply(
            _finding(),
            to=FindingState.PROVEN,
            reason=ReasonCode.REPRO_CONFIRMED,  # F-10은 validated에서만 나온다
            stage=Stage.VALIDATE,
            store=InMemoryStateStore(),
            trace=_writer(),
        )


def test_illegal_transition_leaves_no_trace_or_store_write() -> None:
    """반쯤 기록된 전이를 남기면 지표가 실제보다 부풀어 오른다."""
    store, trace = InMemoryStateStore(), _writer()

    with pytest.raises(IllegalTransition):
        state_machine.apply(
            _candidate(),
            to=FindingState.PROVEN,
            reason=ReasonCode.SCOUT_PROMOTED,
            stage=Stage.SCOUT,
            store=store,
            trace=trace,
        )

    assert trace.events == []
    assert store.get("candidate", "cand_0001") is None


def test_promotion_runs_schema_validation_not_model_copy() -> None:
    """표에 있는 전이라도 불변식을 깨면 실패해야 한다.

    `model_copy(update=...)`를 썼다면 근거 없는 `validated`가 조용히 만들어진다.
    """
    with pytest.raises(ValidationError):
        state_machine.apply(
            _finding(),  # repro_spec·evidence_refs·path 없음
            to=FindingState.VALIDATED,
            reason=ReasonCode.CRITIC_UPHELD,
            stage=Stage.CRITIC,
            store=InMemoryStateStore(),
            trace=_writer(),
        )


def test_backed_finding_can_reach_proven() -> None:
    store, trace = InMemoryStateStore(), _writer()
    validated = state_machine.apply(
        _finding(backed=True),
        to=FindingState.VALIDATED,
        reason=ReasonCode.CRITIC_UPHELD,
        stage=Stage.CRITIC,
        store=store,
        trace=trace,
    )

    proven = state_machine.apply(
        validated,
        to=FindingState.PROVEN,
        reason=ReasonCode.REPRO_CONFIRMED,
        stage=Stage.VALIDATE,
        store=store,
        trace=trace,
    )

    assert proven.state is FindingState.PROVEN
    assert [e.data["rule"] for e in trace.events] == ["F-8", "F-10"]


def test_create_records_new_object_with_null_from_state() -> None:
    trace = _writer()

    state_machine.create(
        _candidate(),
        reason=None,
        stage=Stage.SCOUT,
        store=InMemoryStateStore(),
        trace=trace,
    )

    (event,) = trace.events
    assert event.data["rule"] == "C-1"
    assert event.data["from_state"] is None
    assert event.data["to_state"] == "candidate"


def test_finish_run_marks_stopped_stage_on_budget_exhaustion() -> None:
    """R-1은 finished_at만, R-2는 stopped_after까지 채운다."""
    store, trace = InMemoryStateStore(), _writer()
    finished_at = datetime(2026, 9, 1, 1, tzinfo=UTC)

    stopped = state_machine.finish_run(
        _run_record(),
        finished_at=finished_at,
        stopped_after=int(Stage.CRITIC),
        reason=ReasonCode.BUDGET_EXHAUSTED,
        store=store,
        trace=trace,
    )

    assert stopped.finished_at == finished_at
    assert stopped.stopped_after == int(Stage.CRITIC)
    assert not stopped.is_complete  # 부분 결과는 비교표에 넣지 않는다
    assert trace.events[-1].data["rule"] == "R-2"


def test_finish_run_completed_is_comparable() -> None:
    record = state_machine.finish_run(
        _run_record(),
        finished_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
        stopped_after=None,
        reason=None,
        store=InMemoryStateStore(),
        trace=_writer(),
    )

    assert record.is_complete


@pytest.mark.parametrize(
    "reason",
    [ReasonCode.BUDGET_EXHAUSTED, ReasonCode.USER_STOP_REQUESTED, ReasonCode.DRY_RUN],
)
def test_finish_run_records_the_reason_it_was_given(reason: ReasonCode) -> None:
    """중단 사유가 호출자에게서 와야 한다.

    이 세 값이 전부 `budget_exhausted`로 기록되던 것이 이 테스트를 만든 계기다.
    `--stop-after`로 자른 run이 "예산 초과로 중단됨"으로 읽히면 조건 간 비교
    (evaluation.md §5)가 존재하지 않는 결론을 낸다.
    """
    trace = _writer()

    state_machine.finish_run(
        _run_record(),
        finished_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
        stopped_after=int(Stage.CRITIC),
        reason=reason,
        store=InMemoryStateStore(),
        trace=trace,
    )

    assert trace.events[-1].data["reason"] == reason.value


@pytest.mark.parametrize(
    ("stopped_after", "reason"),
    [
        (int(Stage.CRITIC), None),
        (None, ReasonCode.USER_STOP_REQUESTED),
    ],
)
def test_finish_run_rejects_a_half_filled_stop(
    stopped_after: int | None, reason: ReasonCode | None
) -> None:
    """단계와 사유는 함께 채워지거나 함께 비어야 한다.

    이번 결함이 정확히 두 값이 갈라진 것이므로 불변식으로 고정한다 — 한쪽만 넘기는
    호출부가 생기면 여기서 터진다.
    """
    with pytest.raises(ValueError, match="stopped_after"):
        state_machine.finish_run(
            _run_record(),
            finished_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
            stopped_after=stopped_after,
            reason=reason,
            store=InMemoryStateStore(),
            trace=_writer(),
        )


def test_queue_is_fifo_and_bounded() -> None:
    queue: WorkQueue[str] = WorkQueue(maxsize=2)
    queue.push("a")
    queue.push("b")

    with pytest.raises(QueueFull):
        queue.push("c")
    assert queue.pop() == "a"


def test_requeue_counter_is_per_object() -> None:
    """상한 판정은 정책이 한다. 큐는 세기만 한다."""
    queue: WorkQueue[str] = WorkQueue(maxsize=8)

    assert queue.requeue_count("cand_0001") == 0
    assert queue.mark_requeued("cand_0001") == 1
    assert queue.mark_requeued("cand_0001") == 2
    assert queue.requeue_count("cand_0002") == 0
