"""예산 원장과 RunContext 래퍼 테스트.

**토큰 수를 단언하지 않는다.** 여기서 고정하는 것은 "모델 호출 한 번이 usage 기록 +
예산 차감 + trace를 한꺼번에 만든다"는 계약과, 소진 시 예외가 나온다는 것뿐이다.
"""

from datetime import UTC, datetime

import pytest

from src.adapters.memory_store import InMemoryStateStore
from src.config import load_config
from src.ids import IdFactory
from src.interfaces.model_client import ModelResult, Prompt, SchemaParseError, Usage
from src.interfaces.retrieval import RetrievalResult
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.stages import Stage
from src.orchestrator.trace import VOLATILE_FIELDS, EventKind, TraceWriter
from src.schemas import Candidate, Decision, FindingState, Location, ProducedBy, Role, Verdict
from src.schemas.finding import Finding
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


_BOGUS = ProducedBy(role=Role.SCOUT, model="wrong", prompt_version="v0")


def _bogus_finding() -> Finding:
    return Finding(
        finding_id="find_0001",
        run_id=RUN_ID,
        candidate_id="cand_0001",
        title="t",
        cwe="CWE-787",
        state=FindingState.HYPOTHESIZED,
        confidence=0.5,
        produced_by=_BOGUS,
    )


def _bogus_decision() -> Decision:
    return Decision(
        decision_id="dec_0001",
        run_id=RUN_ID,
        finding_id="find_0001",
        verdict=Verdict.UPHOLD,
        rationale="r",
        produced_by=_BOGUS,
    )


class _BogusProducedByStub:
    """`produced_by`에 **틀린 값**을 넣은 산출물을 낸다 — 실서빙 모델이 자기 role·model을
    모른 채 그 필수 필드를 채우는 상황(@0xM1yu 지적)을 세운다."""

    def __init__(self, value) -> None:  # noqa: ANN001
        self._value = value

    def complete(self, *, role, prompt, schema):  # noqa: ANN001, ANN201
        return ModelResult(value=self._value, usage=_usage(), model="served-30b")


@pytest.mark.parametrize(
    "make_value", [_bogus_finding, _bogus_decision], ids=["finding", "decision"]
)
def test_call_model_stamps_produced_by_over_the_model_value(make_value) -> None:  # noqa: ANN001
    """실서빙 모델이 `produced_by`에 아무 값이나 넣어도 하네스가 진실로 덮는다 (@0xM1yu 요청).

    Fake는 이미 맞게 채우므로, 모델이 **틀린** `produced_by`(role=SCOUT·model=wrong)를 낸
    경우를 세워 override를 본다. `model`은 `ModelResult.model`(실제 응답 모델)을 써야 한다.

    **`produced_by`를 가진 스키마 둘을 다 돈다.** 코드 경로는 하나지만 그 사실이 여기 안
    적혀 있으면 `Decision`이 덮이는지는 아무도 확인하지 않은 것이 된다.
    """
    value = make_value()
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.models = _BogusProducedByStub(value)

    result = ctx.call_model(
        role=Role.AUDITOR, prompt=PROMPT, schema=type(value), stage=Stage.AUDITOR
    )

    pb = result.value.produced_by
    assert pb.role is Role.AUDITOR  # 모델이 쓴 SCOUT이 아니라 호출 역할
    assert pb.model == "served-30b"  # `ModelResult.model` — fallback까지 반영한 실제 응답 모델
    assert pb.prompt_version == PROMPT.prompt_version


def test_call_model_leaves_schemas_without_produced_by_alone() -> None:
    """`produced_by`가 없는 스키마(`Candidate`)에는 그 필드를 붙이지 않는다.

    **가드가 없어지면 조용히 틀린다.** pydantic 2.13.4에서 `produced_by` 없는 모델에
    `model_copy(update=...)`를 하면 예외도 경고도 없고, `model_dump()`에도 안 나타난다 —
    그런데 **객체에는 붙는다**. 그래서 덤프를 보는 단언은 회귀를 못 잡고 `hasattr`만 잡는다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())

    result = ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    assert "produced_by" not in Candidate.model_fields  # 전제: 이 스키마에는 그 필드가 없다
    assert not hasattr(result.value, "produced_by")


def test_call_model_records_failure_and_reraises() -> None:
    """스키마 파싱 실패율의 분자가 여기서 나온다. 삼키면 지표가 0이 된다."""
    ctx, _ = _context(budget=_budget(), usage=_usage(), fail=True)

    with pytest.raises(RuntimeError):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    (call,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    assert call.data["ok"] is False
    assert call.data["error"] == "RuntimeError"


class _FailingWithSpendStub:
    """이미 토큰·GPU를 쓴 뒤 `SchemaParseError`로 소진되는 클라이언트 (D53).

    실서빙의 `openai_compat.complete()`가 재시도를 소진하면 그때까지의 usage·gpu_ms를
    예외에 실어 올린다. 이 스텁은 그 계약을 세워 `call_model`이 실패분을 청구하는지 본다.
    """

    def __init__(self, *, usage: Usage, gpu_ms: int, attempts: int) -> None:
        self._usage = usage
        self._gpu_ms = gpu_ms
        self._attempts = attempts
        self.calls = 0

    def complete(self, *, role, prompt, schema):  # noqa: ANN001, ANN201
        self.calls += 1
        raise SchemaParseError(
            "3회 안에 스키마를 만들지 못했다",
            usage=self._usage,
            gpu_ms=self._gpu_ms,
            gpu_ms_source="request_wall_clock",
            attempts=self._attempts,
        )


class _AttemptsStub:
    """재시도 끝에 성공한 호출. `attempts`를 실은 `ModelResult`를 낸다 (D55)."""

    def __init__(self, *, usage: Usage, attempts: int) -> None:
        self._usage = usage
        self._attempts = attempts

    def complete(self, *, role, prompt, schema):  # noqa: ANN001, ANN201
        value = Candidate(
            candidate_id="cand_0001",
            run_id=RUN_ID,
            artifact_id="art_0001",
            location=Location(file="parser.c"),
            score=0.7,
            state=FindingState.CANDIDATE,
        )
        return ModelResult(value=value, usage=self._usage, model="fake", attempts=self._attempts)


def test_a_failed_call_charges_what_it_already_spent() -> None:
    """실패한 호출도 이미 쓴 토큰·GPU를 장부에 청구한다 (D53).

    실패 경로가 청구를 건너뛰면 evaluation.md §5.2의 분자(input/output token·GPU-second)가
    비고, 폭주가 잦은 조건일수록 싸 보인다 — 조건 비교에서 정확히 거꾸로 가는 편향이다.
    v2 스모크에서 `critic_failed` 3건 × 243초(run의 60.7%)가 그렇게 장부 밖으로 샜다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.models = _FailingWithSpendStub(
        usage=Usage(input_tokens=300, output_tokens=30, cached_tokens=100),
        gpu_ms=243_000,
        attempts=2,
    )

    with pytest.raises(SchemaParseError):
        ctx.call_model(role=Role.CRITIC, prompt=PROMPT, schema=Candidate, stage=Stage.CRITIC)

    assert ctx.ledger.input_tokens == 300  # 실패분이 장부에 실렸다
    assert ctx.ledger.gpu_ms == 243_000
    (call,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    assert call.data["ok"] is False
    assert call.data["input_tokens"] == 300  # 실패 이벤트도 소비를 싣는다
    assert call.data["output_tokens"] == 30
    assert call.data["attempts"] == 2
    # 이벤트가 장부와 어긋나면 잡는다 (#82 리뷰 F11) — gpu·source·cached도 균질하게 싣는다.
    # cached(100)는 input(300)과 다른 값이라 캐시 비율 분모 오염(input엔 들어가고 cached엔
    # 안 들어감)도 이 단언이 잡는다.
    assert call.data["gpu_ms"] == 243_000
    assert call.data["gpu_ms_source"] == "request_wall_clock"
    assert call.data["cached_tokens"] == 100


def test_a_failed_calls_charge_can_exhaust_the_budget() -> None:
    """실패분 청구가 상한을 넘기면 다음 단계 진입이 막힌다 — 실패 한 건이 run 종료 시점을
    바꿀 수 있다 (#82 (a)의 의도된 귀결, 리뷰 F12).

    이 PR 전에는 실패한 호출이 장부를 안 건드려 이 전이가 없었다. `gpu_seconds=0`(검사 안 함)이
    아니라 `input_tokens` 상한으로 세워 실패분 청구가 실제로 `ensure()`를 막는지 본다.
    """
    ctx, _ = _context(budget=_budget(input_tokens=100), usage=_usage())
    ctx.models = _FailingWithSpendStub(
        usage=Usage(input_tokens=100, output_tokens=8192, cached_tokens=0),
        gpu_ms=100,
        attempts=2,
    )

    with pytest.raises(SchemaParseError):
        ctx.call_model(role=Role.CRITIC, prompt=PROMPT, schema=Candidate, stage=Stage.CRITIC)

    # 실패분이 input_tokens 100을 청구해 상한(100)에 닿았다 — 다음 ensure가 막는다.
    with pytest.raises(BudgetExhausted):
        ctx.ledger.ensure(Stage.AUDITOR)


def test_the_attempt_count_lands_on_a_successful_model_call() -> None:
    """재시도가 성공으로 끝나도 시도 수가 `model_call`에 남는다 (D55).

    이 값이 없으면 1차 실패 후 살아난 호출이 성공 한 건과 구별되지 않아 §5.1 실패율이 0%로
    읽힌다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())
    ctx.models = _AttemptsStub(usage=_usage(), attempts=3)

    ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Candidate, stage=Stage.SCOUT)

    (call,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    assert call.data["ok"] is True
    assert call.data["attempts"] == 3


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

    0으로 더하면 두 사실이 합쳐져 나중에 구분할 수 없다 — `repo_dirty`·`device`가 `None`을
    쓰는 것과 같은 이유다.

    **`incomplete_fields()`가 이 카운터를 읽지는 않는다.** 오래 그렇게 적혀 있었지만
    거짓이었고 실제 입력은 `RunContext.gpu_unmeasured_roles`다. 여기서 고정하는 것은
    원장이 두 사실을 섞지 않는다는 것뿐이다.
    """
    ledger = _ledger()
    zero_usage = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    ledger.charge(zero_usage, stage=4, gpu_ms=None)
    ledger.charge(zero_usage, stage=4, gpu_ms=120)

    assert ledger.gpu_ms == 120
    assert ledger.unmeasured_gpu_calls == 1


def test_the_unmeasured_counter_mixes_model_and_retrieval_calls() -> None:
    """이 카운터를 그대로 판정에 쓰면 Fake 검색이 "미측정 GPU"로 잡힌다.

    `record_retrieval()`도 `charge()`로 들어오고 Fake embedding·reranker는 `gpu_ms=None`을
    준다. 그래서 이 값은 **모델 호출과 검색 호출을 섞어 센다** — 판정에 쓰면 멀쩡한
    `dev_*` run이 매번 비교표에서 빠진다(checksum 판정에서 Fake 역할을 빼는 것과 같은
    이유).

    이 테스트는 고칠 것을 요구하는 것이 아니라 **함정을 박제한다.** 다음 사람이 카운터를
    게이트에 연결하면 여기서 "모델만 세야 한다"는 전제를 다시 만나게 된다. 갈라야 할
    것이 무엇인지는 `unmeasured_gpu_calls` 필드 주석에 있다.
    """
    ctx, _ = _context(budget=_budget(), usage=_usage())
    zero = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)

    ctx.record_retrieval("embedding", RetrievalResult(usage=zero, gpu_ms=None), candidates=3)

    assert ctx.ledger.unmeasured_gpu_calls == 1
    # 판정에 실제로 쓰이는 쪽은 검색 호출을 세지 않는다 — 모델 호출만 역할별로 담는다.
    assert ctx.gpu_unmeasured_roles == {}


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
