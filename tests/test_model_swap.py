"""단계 경계 모델 교대 — 실패 예외·사유 코드·trace 이벤트, 그리고 교대 어댑터.

교대는 콜드 재기동이다(ADR 0012 Phase 6.5 — ADR 0009). 하네스는 교대 명령(장비 파일의
`command`)을 로컬 subprocess로 부르고, 명령이 끝나면 올린 모델마다 `/v1/models`로 창을 다시 본다.
"""

from datetime import UTC, datetime

from src.adapters.memory_store import InMemoryStateStore
from src.config import load_config
from src.ids import IdFactory
from src.interfaces.model_client import ModelGatewayError, ModelSwapError
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.runner import _is_droppable
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas.enums import ReasonCode
from src.schemas.run import Budget

RUN_ID = "run_20260929_0001"
NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _ctx() -> RunContext:
    config = load_config("dev_fake")
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    budget = Budget(input_tokens=1000, output_tokens=1000, gpu_seconds=0)
    return RunContext(
        run_id=RUN_ID,
        config=config,
        models=None,
        retriever=None,
        tools=None,
        store=InMemoryStateStore(),
        ids=IdFactory(RUN_ID),
        trace=trace,
        ledger=BudgetLedger(budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
        monotonic_ms=lambda: 0,
    )


# --- 예외·사유 코드·이벤트 ----------------------------------------------------------


def test_a_swap_failure_is_a_gateway_error_the_runner_does_not_drop() -> None:
    """교대 실패는 **서버가 없다**는 사건이다 — 후보 하나를 버리고 계속 가면 안 된다.

    `ModelGatewayError`의 하위형이라 러너의 기존 갈래가 run을 멈추고 매니페스트까지 간다.
    `status`·`generating`이 `None`이라 `_is_droppable`이 「요청 하나에 대한 답」으로 읽지 않는다.
    부분 보고(`report`)는 예외에 실려 올라간다 — 실패한 교대도 무엇을 띄우다 죽었는지 남아야 한다.
    """
    exc = ModelSwapError("교대 실패", detail="stderr 꼬리", report={"ok": False, "stage": 5})

    assert isinstance(exc, ModelGatewayError)
    assert (exc.status, exc.generating, exc.detail) == (None, None, "stderr 꼬리")
    assert exc.report == {"ok": False, "stage": 5}
    assert not _is_droppable(exc)
    assert ModelSwapError("보고 없음").report == {}


def test_a_swap_failure_has_its_own_reason_code() -> None:
    """떠 있던 서버가 죽은 것(`model_gateway_unavailable`)과 교대가 못 띄운 것을 기록에서 가른다
    — 앞은 서버를 살리고, 뒤는 장비 파일·교대 명령을 본다."""
    assert ReasonCode.MODEL_SWAP_FAILED.value == "model_swap_failed"
    assert ReasonCode.MODEL_SWAP_FAILED is not ReasonCode.MODEL_GATEWAY_UNAVAILABLE


def test_record_model_swap_emits_one_event_that_keeps_the_script_report_apart() -> None:
    """교대 한 번 = `model_swap` 한 줄. 하네스가 잰 것과 교대 명령이 보고한 것을 섞지 않는다.

    교대 명령의 보고는 `report.` 아래로 간다 — 명령도 `ok`·`stage`·`wall_ms`를 보고하는데
    (조사 문서의 기록 필드), 접두사 없이 펴면 하네스의 `ok`·`stage`·`wall_ms`와 이름이 겹친다.
    """
    ctx = _ctx()

    ctx.record_model_swap(
        ("qwen3-14b", "seccoderx-8b"),
        stage=Stage.SCOUT,
        ok=True,
        wall_ms=1234,
        report={"ok": True, "stage": 4, "wall_ms": 999, "server.qwen3-14b.util": 0.4},
    )
    ctx.record_model_swap((), stage=None, ok=False, wall_ms=5, report={}, error="내림 실패")

    swap, teardown = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_SWAP]
    assert (swap.stage, swap.wall_ms) == (int(Stage.SCOUT), 1234)
    assert swap.data == {
        "models": "qwen3-14b seccoderx-8b",
        "ok": True,
        "error": None,
        "report.ok": True,
        "report.stage": 4,
        "report.wall_ms": 999,
        "report.server.qwen3-14b.util": 0.4,
    }
    assert (teardown.stage, teardown.data["models"], teardown.data["ok"]) == (None, "", False)
    assert teardown.data["error"] == "내림 실패"
