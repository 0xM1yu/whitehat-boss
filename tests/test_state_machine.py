"""상태 머신·큐 계약 테스트.

**전이 결과 개수 같은 측정값은 단언하지 않는다.** 여기서 고정하는 것은 전이 표의
무결성, "모든 전이가 trace에 남는다"는 계약, 그리고 표에 없는 조합이 터진다는 것뿐이다.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.adapters.memory_store import InMemoryStateStore
from src.orchestrator import state_machine
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
    # `path`·`evidence_refs`는 기본값이 없다(`schemas/finding.py` — 스키마의 `required`가
    # 모델의 생략을 가른다). 그래서 **빈 값이 이 팩토리의 기본**이고 `backed=True`가 그것을
    # 덮는다. ⚠ 이 둘을 `Finding(...)`에 직접 넘기면서 `**extra`에도 두면 중복 키워드라
    # `TypeError`다 — 한 자리에서만 준다.
    extra: dict = {"path": [], "evidence_refs": []}
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


def _docstring_rows() -> list[tuple[str, ...]]:
    """모듈 docstring의 전이 표에서 Candidate·Finding 행만 뽑는다.

    거르는 것은 **둘**이다 — 셀이 6개인가(RunRecord 표는 4열이라 여기서 빠진다), 그리고
    첫 칸이 `C-`/`F-`로 시작하는가. ⚠ **구분선(`| --- | ... |`)은 정확히 6셀이라 앞엣것에
    안 걸린다** — 그것을 거르는 것은 뒤엣것이다. 처음 쓸 때 이 주석이 *"셀 개수로 자르면
    구분선도 자연히 빠진다"*고 적었는데 자기 코드를 틀리게 서술한 것이었다(§2.1 역방향).

    정규식은 안 쓴다 — **검출기가 조용히 죽는 자리**가 하나 줄어든다(CLAUDE.md §2.5).
    """
    rows: list[tuple[str, ...]] = []
    for line in (state_machine.__doc__ or "").splitlines():
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        if len(cells) == 6 and cells[0][:2] in ("C-", "F-"):
            rows.append(cells)
    return rows


def _cell_states(cell: str) -> list[FindingState | None]:
    """from 칸 하나가 상태 둘을 담을 수 있다(C-7: `candidate`/`triaged`)."""
    if cell == "(신규)":
        return [None]
    return [FindingState(part.strip(" `")) for part in cell.split("/")]


def test_the_docstring_table_is_the_executable_table() -> None:
    """표는 **손으로 두 벌** 유지된다 — docstring 표와 `TRANSITIONS`.

    `_lookup`의 에러 메시지가 "docstring의 표를 먼저 고친다"고 안내하는데, 1-A 적대적
    리뷰의 변이 시험에서 **docstring 행만 지워도 스위트가 `1515 passed`**였다. 둘이
    갈라져도 CI가 못 잡으면 그 안내문이 가리키는 표가 더는 정본이 아니다.
    """
    rows = _docstring_rows()

    # 양성 핀 — 파서가 실제 표를 읽는가. 지어낸 샘플이 아니라 docstring의 F-10b 행을
    # 그대로 옮긴 것이다(줄 번호로 적지 않는다 — 번호는 움직이고 내용은 이 단언이 문다).
    assert (
        "F-10b",
        "재현 성공, E4 미만 (무전이)",
        "8",
        "`validated`",
        "`validated`",
        "`evidence_below_proven`",
    ) in rows
    # 음성 핀 — 같은 6열이지만 행이 **아닌** 것(표 구분선)은 안 들어온다.
    assert "---" not in {row[0] for row in rows}

    assert {row[0] for row in rows} == {rule.rule for rule in TRANSITIONS}

    # F-13의 「임의 → 변경 없음」이 펴지는 상태 집합을 **표에서** 얻는다. 코드의
    # `_FINDING_STATES`를 import하면 그 상수가 줄어들 때 검사도 같이 줄어 변이가 살아난다.
    finding_states = {
        state
        for rule, _t, _s, src, dst, _r in rows
        if rule.startswith("F-") and src != "임의"
        for cell in (src, dst)
        for state in _cell_states(cell)
        if state is not None
    }

    expected: set[tuple[str, FindingState | None, FindingState | None, ReasonCode | None]] = set()
    for rule, _trigger, _stage, src, dst, reason_cell in rows:
        kind = "candidate" if rule.startswith("C-") else "finding"
        reason = None if reason_cell == "—" else ReasonCode(reason_cell.strip(" `"))
        if src == "임의" and dst == "변경 없음":  # F-13은 상태별 자기 전이로 펴서 담긴다
            expected |= {(kind, state, state, reason) for state in finding_states}
            continue
        to_state = _cell_states(dst)[0]
        expected |= {(kind, from_state, to_state, reason) for from_state in _cell_states(src)}

    table = {(r.kind, r.from_state, r.to_state, r.reason) for r in TRANSITIONS}
    # **집합 동치다 — 단방향이 아니다.** 첫 판은 `docstring → TRANSITIONS`만 돌았고,
    # 이름 집합이 같으면 **코드에만 행을 더해도 초록**이었다. 검사 이름이 「IS the
    # executable table」인데 무는 것은 그보다 약했다(CLAUDE.md §2.5 마지막 단락).
    assert expected == table, (
        "docstring 표와 TRANSITIONS가 갈라졌다.\n"
        f"  표에만 있다: {sorted(str(key) for key in expected - table)}\n"
        f"  코드에만 있다: {sorted(str(key) for key in table - expected)}"
    )


def test_the_self_transition_list_in_the_docstring_is_complete() -> None:
    """docstring 머리의 `(C-9·F-3·…)` 목록이 **세 번째 사본**이다.

    이 목록이 세는 것은 "통과율의 분모를 만드는 행"이다. 새 자기 전이가 들어오면서
    여기만 안 고치면 분모가 틀릴 수 있다는 사실이 문서에서 사라진다.
    """
    marker = "`from == to`인 행("
    line = next(t for t in (state_machine.__doc__ or "").splitlines() if marker in t)
    listed = set(line.split(marker, 1)[1].split(")", 1)[0].split("·"))

    assert listed == {r.rule for r in TRANSITIONS if r.from_state == r.to_state}


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


@pytest.mark.parametrize(
    "reason",
    [
        ReasonCode.BELOW_SCOUT_THRESHOLD,
        ReasonCode.SCOUT_REJECT,
        ReasonCode.NEED_EVIDENCE_UNRESOLVED,
    ],
)
def test_c3_accepts_the_v3_signal_reasons_too(reason: ReasonCode) -> None:
    """C-3 — v3 신호 판정이 낸 두 새 사유(`scout_reject`·`need_evidence_unresolved`)도

    `candidate` → `queued`로 간다 — 같은 번호, C-7이 두 `from_state`를 쓰는 것과 같은
    선례로 같은 (from, to)에 사유만 늘렸다(Q7).
    """
    store, trace = InMemoryStateStore(), _writer()

    updated = state_machine.apply(
        _candidate(),
        to=FindingState.QUEUED,
        reason=reason,
        stage=Stage.SCOUT,
        store=store,
        trace=trace,
    )

    assert updated.state is FindingState.QUEUED
    assert trace.events[-1].data["rule"] == "C-3"


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


def test_reproduced_finding_below_e4_holds_at_validated() -> None:
    """F-10b — 재현에 성공해도 게이트가 E4를 안 주면 `proven`에 안 간다 (#68).

    **이 테스트가 없으면 표의 F-10b 행은 아무도 안 문다.** 1-A 적대적 리뷰의 변이
    시험에서 그 행을 통째로 지우고도 스위트가 `1515 passed`였다. 행이 빠진 채 F-10b가
    실제로 도달 가능해지면 `apply()`가 `IllegalTransition`을 던져 run이 예고 없이 멈춘다.
    """
    store, trace = InMemoryStateStore(), _writer()
    validated = state_machine.apply(
        _finding(backed=True),
        to=FindingState.VALIDATED,
        reason=ReasonCode.CRITIC_UPHELD,
        stage=Stage.CRITIC,
        store=store,
        trace=trace,
    )

    held = state_machine.apply(
        validated,
        to=FindingState.VALIDATED,
        reason=ReasonCode.EVIDENCE_BELOW_PROVEN,
        stage=Stage.VALIDATE,
        store=store,
        trace=trace,
    )

    assert held.state is FindingState.VALIDATED
    assert [e.data["rule"] for e in trace.events] == ["F-8", "F-10b"]


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
