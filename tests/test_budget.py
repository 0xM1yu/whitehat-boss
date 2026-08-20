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


# --- GPU 시간 회계 (2026-08-17 실측 기반) -------------------------------------

# `artifacts/run_20260813_0001/trace.jsonl`의 `tool_call.wall_ms` 11건을 그대로 옮긴 값.
# **합계 8,492 ms인데 그 run의 장부에는 3초가 남았다** — 청구 지점이 초로 내림해서
# 342~906 ms짜리 8건이 통째로 0으로 청구됐기 때문이다. 같은 도구를 같은 순서로 돈 세
# run이 3/6/8초를 남겼는데 실제 차이는 1.6배였다(장부는 2.7배).
MEASURED_TOOL_WALL_MS = (493, 1053, 342, 348, 906, 356, 367, 345, 1979, 342, 1961)

# 같은 11건을 ATOM(aarch64/podman)에서 돌린 값. **이쪽이 더 강한 단언이다** — 전부 1초
# 미만이라 예전 내림에서는 11/11이 0으로 청구됐다. 즉 그 run의 도구 시간은 장부에
# 통째로 없었다. 아키텍처가 다르면 같은 도구도 다른 시간을 쓴다는 사실도 함께 남는다.
ATOM_TOOL_WALL_MS = (299, 929, 321, 272, 932, 258, 248, 281, 335, 258, 358)


def _ledger(gpu_seconds: int = 0) -> BudgetLedger:
    budget = Budget(input_tokens=10**9, output_tokens=10**9, gpu_seconds=gpu_seconds)
    return BudgetLedger(budget, TraceWriter(RUN_ID, clock=lambda: NOW))


def test_the_ledger_keeps_every_millisecond_of_the_measured_run() -> None:
    """이 테스트가 이 결함의 재발을 막는 유일한 장치다.

    단언은 **8,492 ms**다. 3초가 아니다 — 초로 내림하는 코드가 돌아오면 여기서 걸린다.

    **`BudgetLedger.charge()`를 직접 부른다.** `RunContext.call_tool`을 거치면 Fake 런타임
    프로필에서 11건이 전부 `None`으로 청구되고, 그러면 이 테스트는 아무것도 지키지 않은 채
    통과한다. 아래 두 단언이 그 공허함을 막는다 —
    `test_pipeline_e2e.test_trace_actually_carries_the_volatile_fields`와 같은 방어다.
    """
    ledger = _ledger()
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    for wall_ms in MEASURED_TOOL_WALL_MS:
        ledger.charge(zero_usage, stage=2, gpu_ms=wall_ms)

    assert ledger.gpu_ms == sum(MEASURED_TOOL_WALL_MS) == 8492
    assert ledger.gpu_seconds == 8  # 파생값. 예전 장부의 3이 아니다
    # 공허하지 않다는 증거: 청구가 실제로 일어났고(0도 None도 아니다) 미측정으로 샌 것이
    # 하나도 없다. 이 둘이 없으면 청구 경로가 통째로 죽어도 위 단언이 통과할 수 있다.
    assert ledger.gpu_ms > 0
    assert ledger.unmeasured_gpu_calls == 0


def test_the_ledger_keeps_a_run_where_every_call_was_under_a_second() -> None:
    """ATOM(aarch64/podman) 실측 11건. **11건 전부 1초 미만이다.**

    예전 내림에서는 이 run의 도구 시간이 **통째로 0**이었다 — 8/11이 아니라 11/11이다.
    x86 케이스보다 강한 단언이라 둘 다 남긴다: 하나가 통과해도 다른 하나가 걸리는
    조합이 있어야 그물이 촘촘해진다.
    """
    ledger = _ledger()
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    for wall_ms in ATOM_TOOL_WALL_MS:
        ledger.charge(zero_usage, stage=2, gpu_ms=wall_ms)

    assert ledger.gpu_ms == sum(ATOM_TOOL_WALL_MS) == 4491
    assert ledger.gpu_seconds == 4  # 예전 장부는 0이었다
    # 공허 방지: 11건 전부 1초 미만이라는 전제 자체를 고정한다. 이 전제가 깨지면 이
    # 테스트가 "1초 미만도 청구된다"를 더는 증명하지 못한다.
    assert all(ms < 1000 for ms in ATOM_TOOL_WALL_MS)
    assert ledger.unmeasured_gpu_calls == 0


def test_a_sub_second_call_is_not_charged_as_zero() -> None:
    """342 ms 한 건. 예전 코드는 `342 // 1000 == 0`으로 통째로 버렸다."""
    ledger = _ledger()

    ledger.charge(Usage(input_tokens=0, output_tokens=0, cached_tokens=0), stage=2, gpu_ms=342)

    assert ledger.gpu_ms == 342


def test_an_unmeasured_call_is_counted_not_added_as_zero() -> None:
    """`None`은 "GPU를 안 썼다"가 아니라 "안 쟀다"다.

    0으로 더하면 두 사실이 합쳐져 `incomplete_fields()`가 판정할 근거가 사라진다 —
    `repo_dirty`·`device`가 `None`을 쓰는 것과 같은 이유다.
    """
    ledger = _ledger()
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    ledger.charge(zero_usage, stage=4, gpu_ms=None)
    ledger.charge(zero_usage, stage=4, gpu_ms=120)

    assert ledger.gpu_ms == 120
    assert ledger.unmeasured_gpu_calls == 1


def test_the_budget_event_carries_milliseconds_too() -> None:
    """초만 남기면 사후 집계가 볼 정확값이 사라지고 이 결함이 그대로 돌아온다."""
    ledger = _ledger()

    ledger.charge(Usage(input_tokens=0, output_tokens=0, cached_tokens=0), stage=2, gpu_ms=1500)

    event = ledger._trace.events[-1]
    assert event.data["gpu_ms"] == 1500
    assert event.data["gpu_seconds"] == 1


def test_a_fake_runtime_tool_call_is_not_charged_as_time() -> None:
    """`FakeToolRunner`의 1~2 ms는 GPU도 컨테이너도 아니라 **하네스가 자기를 잰 값**이다.

    장부에 넣으면 무의미한 숫자가 비용 지표에 섞이고, 같은 입력의 두 실행이 다른 기록을
    남긴다. 예전에는 `// 1000`이 그 값을 전부 0으로 만들어 **내림이 재현성을 우연히 지켜
    주고 있었다** — 이 검사는 그 우연을 의도로 고정한다.

    판단 기준은 `doctor`가 이미 쓰는 것과 같다(`cli._check_tool_runtime`의 `== "fake"`).
    """
    ledger = _ledger()
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.ledger = ledger
    assert ctx.config.profile.adapters.tool_runner == "fake", "이 검사의 전제가 무너졌다"

    ledger.charge(
        Usage(input_tokens=0, output_tokens=0, cached_tokens=0),
        stage=2,
        gpu_ms=None if ctx.config.profile.adapters.tool_runner == "fake" else 1500,
    )

    assert ledger.gpu_ms == 0
    assert ledger.unmeasured_gpu_calls == 1


def test_the_exhaustion_report_uses_the_unit_its_name_claims() -> None:
    """밖으로 나가는 숫자는 `item` 이름이 뜻하는 단위여야 한다.

    원장은 ms로 누산하고 ms로 판정하지만, 예외·trace·`remaining()`은 전부 초를 낸다.
    변환을 빼먹으면 `gpu_seconds 4491/4 소진` 같은 메시지가 사람에게 간다 — 이름과
    단위가 어긋난 숫자를 기록에 남기는 것이 이번 결함과 같은 계열이다.
    """
    ledger = _ledger(gpu_seconds=4)
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)
    for wall_ms in ATOM_TOOL_WALL_MS:
        ledger.charge(zero_usage, stage=2, gpu_ms=wall_ms)
    assert ledger.gpu_ms == 4491  # 내부는 ms

    with pytest.raises(BudgetExhausted) as exc:
        ledger.ensure(Stage.AUDITOR)

    assert exc.value.item == "gpu_seconds"
    assert (exc.value.spent, exc.value.limit) == (4, 4)  # ms였다면 4491/4000
    assert ledger.remaining()["gpu_seconds"] == 0
    blocked = ledger._trace.events[-1].data
    assert (blocked["spent"], blocked["limit"]) == (4, 4)


def test_the_tool_budget_actually_stops_a_run() -> None:
    """**지금까지 한 번도 걸린 적 없는 상한이 실제로 걸리는지 본다.**

    도구 시간이 `gpu_seconds`로 청구되던 시절에는 내림 때문에 소비가 거의 오르지 않아
    상한이 사실상 무한이었다. 지표 문제가 아니라 **안전장치가 죽어 있던 것**이다.
    """
    budget = Budget(input_tokens=10**9, output_tokens=10**9, gpu_seconds=0, tool_seconds=8)
    ledger = BudgetLedger(budget, TraceWriter(RUN_ID, clock=lambda: NOW))
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    for wall_ms in MEASURED_TOOL_WALL_MS:  # 합계 8,492 ms > 8초
        ledger.charge(zero_usage, stage=2, tool_ms=wall_ms)

    assert ledger.tool_ms == 8492
    with pytest.raises(BudgetExhausted) as exc:
        ledger.ensure(Stage.AUDITOR)
    assert exc.value.item == "tool_seconds"
    assert (exc.value.spent, exc.value.limit) == (8, 8)


def test_tool_time_does_not_land_in_the_gpu_ledger() -> None:
    """도구 넷(semgrep·clang-tidy·build·asan)은 GPU를 쓰지 않는다.

    2026-08-17까지 그 시간이 `gpu_seconds`로 청구됐고, evaluation.md §5.3의 "finding당
    GPU-hour"가 그 값을 분자로 쓴다 — 섞이면 핵심 비용 지표가 CPU 시간으로 오염된다.
    """
    ledger = _ledger()

    ledger.charge(Usage(input_tokens=0, output_tokens=0, cached_tokens=0), stage=2, tool_ms=5000)

    assert ledger.tool_ms == 5000
    assert ledger.gpu_ms == 0  # GPU 칸은 도구가 건드리지 않는다
