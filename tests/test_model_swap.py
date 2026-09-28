"""단계 경계 모델 교대 — 실패 예외·사유 코드·trace 이벤트, 그리고 교대 어댑터.

교대는 콜드 재기동이다(ADR 0012 Phase 6.5 — ADR 0009). 하네스는 교대 명령(장비 파일의
`command`)을 로컬 subprocess로 부르고, 명령이 끝나면 올린 모델마다 `/v1/models`로 창을 다시 본다.
"""

import json
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.adapters import model_swap
from src.adapters.memory_store import InMemoryStateStore
from src.adapters.model_swap import swap_runner
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


# --- 교대 어댑터 --------------------------------------------------------------------

# 가짜 교대 명령. 부를 때마다 argv·환경을 `calls.jsonl`에 적고, 그 뒤 BODY를 돈다.
_SCRIPT = """
import json, os, sys, time
from pathlib import Path

keys = sys.argv[1:]
names = ("HARNESS_PYTHON", "SWAP_DEADLINE_S", "HARNESS_RUN_ID", "HARNESS_DEVICE")
with (Path(__file__).parent / "calls.jsonl").open("a", encoding="utf-8") as calls:
    calls.write(json.dumps({"argv": keys, "env": {n: os.environ.get(n) for n in names}}) + "\\n")
BODY
"""

MODELS = load_config("harness_pool").models


def _command(tmp_path: Path, body: str = "") -> tuple[str, ...]:
    script = tmp_path / "swap.py"
    script.write_text(_SCRIPT.replace("BODY", body), encoding="utf-8")
    return (sys.executable, str(script))


def _calls(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _probe(
    windows: Mapping[str, int | None] | None = None, seen: list[tuple[str, str]] | None = None
) -> Callable[[str, str], dict[str, object]]:
    """`/v1/models` 조회 대역. 기본은 registry 창 그대로 떠 있는 서버다."""

    def probe(endpoint: str, key: str) -> dict[str, object]:
        if seen is not None:
            seen.append((endpoint, key))
        window = (windows or {}).get(key, MODELS[key].context_limit)
        return {"ok": True, "reason": None, "max_model_len": window}

    return probe


def _swap(command: tuple[str, ...], probe: Callable[[str, str], object], deadline_s: float = 600):  # noqa: ANN202
    return swap_runner(
        command, models=MODELS, deadline_s=deadline_s, run_id=RUN_ID, device="rig", probe=probe
    )


def test_swap_to_runs_the_command_with_the_keys_and_the_harness_env(tmp_path: Path) -> None:
    """argv = 명령 + 올릴 키. 환경에 하네스 인터프리터·마감·run·장비를 싣는다 — 명령이 장비 파일을
    하네스와 **같은 파이썬**으로 읽고, 마감 안에 끝내고, 자기 run의 서버만 내리게 한다."""
    seen: list[tuple[str, str]] = []

    _swap(_command(tmp_path), _probe(seen=seen))(("seccoderx-8b", "qwen3-14b"))

    (call,) = _calls(tmp_path)
    assert call["argv"] == ["seccoderx-8b", "qwen3-14b"]
    assert call["env"] == {
        "HARNESS_PYTHON": sys.executable,
        "SWAP_DEADLINE_S": "600",
        "HARNESS_RUN_ID": RUN_ID,
        "HARNESS_DEVICE": "rig",
    }
    assert seen == [
        (MODELS["seccoderx-8b"].endpoint, "seccoderx-8b"),
        (MODELS["qwen3-14b"].endpoint, "qwen3-14b"),
    ]


def test_the_last_stdout_line_is_flattened_into_scalars(tmp_path: Path) -> None:
    """보고는 stdout **마지막 줄** JSON이다. 중첩은 점으로 펴고, 목록은 JSON 문자열로 남긴다 —
    trace `data`는 스칼라만 받는다."""
    report = {
        "ok": True,
        "stage": 4,
        "models": ["seccoderx-8b"],
        "server": {"seccoderx-8b": {"action": "launch", "util": 0.3, "args": ["--x"]}},
        "memavail_before": 100.5,
        "error": None,
    }
    body = f"print('기동 로그 한 줄')\nprint({json.dumps(json.dumps(report))})"

    got = _swap(_command(tmp_path, body), _probe())(("seccoderx-8b",))

    assert got == {
        "ok": True,
        "stage": 4,
        "models": '["seccoderx-8b"]',
        "server.seccoderx-8b.action": "launch",
        "server.seccoderx-8b.util": 0.3,
        "server.seccoderx-8b.args": '["--x"]',
        "memavail_before": 100.5,
        "error": None,
    }


def test_a_failed_command_raises_with_its_partial_report_and_output_tail(tmp_path: Path) -> None:
    body = (
        "sys.stderr.write('메모리 부족\\n')\n"
        "print(json.dumps({'ok': False, 'error': 'no mem'}))\n"
        "sys.exit(3)"
    )

    with pytest.raises(ModelSwapError, match="exit 3") as caught:
        _swap(_command(tmp_path, body), _probe())(("seccoderx-8b",))

    assert caught.value.report == {"ok": False, "error": "no mem"}
    assert "메모리 부족" in caught.value.detail


def test_a_command_past_its_deadline_is_torn_down_before_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """마감을 넘기면 명령을 죽이고 **내림 명령(키 없음)을 한 번 부른 뒤** 멈춘다.

    죽인 것은 명령 프로세스뿐이고 명령이 띄우던 서버는 남는다 — 그대로 두면 다음 run이 남의
    메모리 위에서 뜬다. 내림은 run 끝에도 불리지만, 여기서 멈추면 그 사이에 아무도 안 내린다.
    """
    monkeypatch.setattr(model_swap, "_REPORT_GRACE_S", 0.0)
    body = "if keys:\n    time.sleep(60)"

    with pytest.raises(ModelSwapError, match="마감"):
        _swap(_command(tmp_path, body), _probe(), deadline_s=2)(("seccoderx-8b",))

    assert [call["argv"] for call in _calls(tmp_path)] == [["seccoderx-8b"], []]


def test_a_window_other_than_the_context_limit_stops_the_swap(tmp_path: Path) -> None:
    """명령이 0으로 끝나도 믿지 않는다 — 떠 있는 창이 registry `context_limit`과 다르면 멈춘다.

    2026-09-25 ATOM에서 `context_limit` 40,960인 Scout를 16,384로 띄운 채 돌렸고 하네스는 아무 말도
    안 했다 — 프롬프트 12건이 400으로 거절됐고 한 run은 후보 0으로 끝났다(`run_20260925_0005`·
    `0006`). 400은 러너가 **그 후보만 버리는** 크기 거절이라 run은 「완주」로 끝난다.
    """
    body = "print(json.dumps({'ok': True}))"

    with pytest.raises(ModelSwapError, match=r"seccoderx-8b.*16384.*40960") as caught:
        _swap(_command(tmp_path, body), _probe({"seccoderx-8b": 16384}))(("seccoderx-8b",))

    assert caught.value.report == {"ok": True}


def test_a_window_the_server_does_not_report_also_stops_the_swap(tmp_path: Path) -> None:
    """창을 못 읽으면 대조를 못 한 것이지 맞은 것이 아니다(CLAUDE.md §0)."""
    with pytest.raises(ModelSwapError, match="seccoderx-8b"):
        _swap(_command(tmp_path), _probe({"seccoderx-8b": None}))(("seccoderx-8b",))


def test_a_model_missing_after_the_swap_stops_it(tmp_path: Path) -> None:
    def probe(_endpoint: str, _key: str) -> dict[str, object]:
        reason = "registry key가 서버의 모델 목록에 없다"
        return {"ok": False, "reason": reason, "max_model_len": None}

    with pytest.raises(ModelSwapError, match="모델 목록에 없다"):
        _swap(_command(tmp_path), probe)(("qwen3-14b",))


def test_fake_models_and_the_teardown_probe_nothing(tmp_path: Path) -> None:
    """fake 엔드포인트는 서버가 없어 볼 창이 없다. 키 없는 호출(전부 내림)도 볼 것이 없다."""

    def probe(_endpoint: str, _key: str) -> dict[str, object]:
        raise AssertionError("조회하면 안 된다")

    swap_to = swap_runner(
        _command(tmp_path),
        models=load_config("dev_fake").models,
        deadline_s=600,
        run_id=RUN_ID,
        device="rig",
        probe=probe,
    )

    swap_to(("fake",))
    swap_to(())

    assert [call["argv"] for call in _calls(tmp_path)] == [["fake"], []]
