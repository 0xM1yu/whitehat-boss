"""예산 원장과 RunContext 래퍼 테스트.

**토큰 수를 단언하지 않는다.** 여기서 고정하는 것은 "모델 호출 한 번이 usage 기록 +
예산 차감 + trace를 한꺼번에 만든다"는 계약과, 소진 시 예외가 나온다는 것뿐이다.
"""

from datetime import UTC, datetime

import pytest

from src.adapters.memory_store import InMemoryStateStore
from src.config import load_config
from src.ids import IdFactory
from src.interfaces.model_client import ModelResult, Prompt, Usage
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.stages import Stage
from src.orchestrator.trace import VOLATILE_FIELDS, EventKind, TraceWriter
from src.schemas import Candidate, FindingState, Location, Role
from src.schemas.run import Budget

RUN_ID = "run_20260901_0001"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
PROMPT = Prompt(prefix="공통 prefix", body="본문", prompt_version="v1")


class StubModelClient:
    """usage만 정해 놓은 ModelClient. 어댑터 동작이 아니라 래퍼 계약을 본다."""

    def __init__(self, *, usage: Usage, fail: bool = False) -> None:
        self._usage = usage
        self._fail = fail
        self.calls = 0

    def complete(self, *, role, prompt, schema):  # noqa: ANN001, ANN201
        self.calls += 1
        if self._fail:
            raise RuntimeError("구조화 출력 파싱 실패")
        value = Candidate(
            candidate_id="cand_0001",
            run_id=RUN_ID,
            artifact_id="art_0001",
            location=Location(file="parser.c"),
            score=0.7,
            state=FindingState.CANDIDATE,
        )
        return ModelResult(value=value, usage=self._usage, model="fake")


def _usage(input_tokens: int = 40, cached: int = 30) -> Usage:
    return Usage(input_tokens=input_tokens, output_tokens=8, cached_tokens=cached)


def _budget(input_tokens: int = 1000) -> Budget:
    return Budget(input_tokens=input_tokens, output_tokens=1000, gpu_seconds=0)


def _context(
    *, budget: Budget, usage: Usage, fail: bool = False
) -> tuple[RunContext, StubModelClient]:
    config = load_config("dev_fake")
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    models = StubModelClient(usage=usage, fail=fail)
    ctx = RunContext(
        run_id=RUN_ID,
        config=config,
        models=models,
        retriever=None,
        tools=None,
        store=InMemoryStateStore(),
        ids=IdFactory(RUN_ID),
        trace=trace,
        ledger=BudgetLedger(budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
        monotonic_ms=lambda: 0,
    )
    return ctx, models


def test_charge_accumulates_and_leaves_room() -> None:
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    ledger = BudgetLedger(_budget(), trace)

    ledger.charge(_usage(input_tokens=10, cached=4), stage=Stage.SCOUT)

    assert ledger.remaining()["input_tokens"] < _budget().input_tokens
    assert not ledger.is_exhausted
    assert trace.events[-1].data["event"] == "charge"


def test_zero_limit_is_not_treated_as_exhausted() -> None:
    """`dev_fake`의 `gpu_seconds: 0`은 "GPU를 안 쓴다"는 뜻이지 "0초만 허용"이 아니다."""
    ledger = BudgetLedger(_budget(), TraceWriter(RUN_ID, clock=lambda: NOW))

    ledger.ensure(Stage.SCOUT)  # 예외 없음


def test_ensure_raises_after_limit_reached() -> None:
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    ledger = BudgetLedger(_budget(input_tokens=10), trace)
    ledger.charge(_usage(input_tokens=10, cached=0), stage=Stage.SCOUT)

    with pytest.raises(BudgetExhausted) as exc:
        ledger.ensure(Stage.AUDITOR)

    assert exc.value.item == "input_tokens"
    assert exc.value.stage == Stage.AUDITOR
    assert [e.data["event"] for e in trace.events] == ["exhausted", "blocked"]


def test_call_model_records_usage_and_charges_budget() -> None:
    ctx, models = _context(budget=_budget(), usage=_usage())

    result = ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    (call,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    assert call.data["ok"] is True
    assert call.data["cached_tokens"] == result.usage.cached_tokens
    assert ctx.ledger.input_tokens == result.usage.input_tokens
    assert models.calls == 1


def test_call_model_records_failure_and_reraises() -> None:
    """스키마 파싱 실패율의 분자가 여기서 나온다. 삼키면 지표가 0이 된다."""
    ctx, _ = _context(budget=_budget(), usage=_usage(), fail=True)

    with pytest.raises(RuntimeError):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    (call,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    assert call.data["ok"] is False
    assert call.data["error"] == "RuntimeError"


def test_call_model_blocks_before_spending_when_exhausted() -> None:
    ctx, models = _context(budget=_budget(input_tokens=5), usage=_usage(input_tokens=10, cached=0))
    ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    with pytest.raises(BudgetExhausted):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.AUDITOR)

    assert models.calls == 1  # 두 번째는 호출 자체가 없어야 한다


def test_stage_span_pairs_start_and_end() -> None:
    ctx, _ = _context(budget=_budget(), usage=_usage())

    with ctx.stage(Stage.SCOUT):
        pass

    kinds = [e.kind for e in ctx.trace.events]
    assert kinds == [EventKind.STAGE_START, EventKind.STAGE_END]
    assert ctx.trace.events[-1].data["ok"] is True


def test_stage_span_records_failure_without_swallowing() -> None:
    ctx, _ = _context(budget=_budget(), usage=_usage())

    with pytest.raises(ValueError), ctx.stage(Stage.SCOUT):
        raise ValueError("노드 실패")

    end = ctx.trace.events[-1]
    assert end.kind is EventKind.STAGE_END
    assert end.data["ok"] is False
    assert end.data["error"] == "ValueError"


def test_canonical_lines_drop_only_volatile_fields() -> None:
    """재현성 기준: `ts`·`wall_ms`만 빼면 두 실행의 JSONL이 바이트 동일해야 한다."""
    ctx, _ = _context(budget=_budget(), usage=_usage())
    with ctx.stage(Stage.SCOUT):
        pass

    lines = ctx.trace.canonical_lines()

    assert lines
    for field in VOLATILE_FIELDS:
        assert all(f'"{field}"' not in line for line in lines)
    assert all('"seq"' in line for line in lines)
