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
from src.interfaces.model_client import ModelGatewayError, ModelSwapError, error_detail
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


# 못 내려가는 깊이의 두 자리. 목록 10만 겹은 **파서**(`json.loads`)가 못 내려가고, 객체 재귀
# 한도 + 50 겹은 C 파서는 읽지만(2026-09-29 CPython 3.14에서 1,500 겹까지 읽었다) **펴기**
# (`_flatten`, 파이썬 재귀)가 못 내려간다. 앞쪽만 막았을 때 뒤쪽이 교대 콜러블 밖으로 샜다.
_DEPTH = sys.getrecursionlimit() + 50
_DEEP_REPORTS = {
    "list-parser": "[" * 100_000 + "]" * 100_000,
    "dict-flatten": '{"a":' * _DEPTH + "1" + "}" * _DEPTH,
}


@pytest.mark.parametrize("shape", sorted(_DEEP_REPORTS))
@pytest.mark.parametrize("failing", [False, True], ids=["exit0", "exit1"])
def test_a_report_too_deep_to_parse_is_an_empty_report_not_a_crash(
    tmp_path: Path, failing: bool, shape: str
) -> None:
    """마지막 줄이 JSON이어도 못 내려가는 깊이면(RecursionError) 보고가 없는 것으로 읽는다 —
    성공 경로의 날것 예외로 run이 죽지 않고, 실패 경로는 `ModelSwapError`로 멈춘다."""
    deep = _DEEP_REPORTS[shape]
    body = f"print({deep!r})" + ("\nsys.exit(1)" if failing else "")
    swap_to = _swap(_command(tmp_path, body), _probe())

    if failing:
        with pytest.raises(ModelSwapError) as caught:
            swap_to(("seccoderx-8b",))
        assert caught.value.report == {}
    else:
        assert swap_to(("seccoderx-8b",)) == {}


class _Unforeseen(Exception):
    """표준 예외 계층 밖의 `Exception` — 「`Exception`을 감싼다」를 철자가 아니라 성질로 문다.

    `RuntimeError`만 두면 감싸기를 `except (RuntimeError, OSError)`로 좁혀도 초록이다(2026-09-29
    적대적 리뷰 실측). 어떤 표준 예외도 상속하지 않아 그 튜플 어디에도 안 걸린다.
    """


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("probe exploded"), ModelSwapError),
        (_Unforeseen("probe went sideways"), ModelSwapError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
    ids=["exception-is-wrapped", "unforeseen-is-wrapped", "base-exception-passes"],
)
def test_an_unexpected_error_inside_the_swap_is_a_swap_error(
    tmp_path: Path, error: BaseException, expected: type[BaseException]
) -> None:
    """교대 콜러블의 실패는 `ModelSwapError` 하나다 — 러너가 그것만 「교대 실패로 run 정지」로
    읽는다. 그 밖의 예외가 새면 사유가 **「예상 밖」으로** 적혀 「교대가 실패했다」가 기록에서
    사라지고, run 끝 내림은 원래 결과를 덮는다. 인터럽트(`BaseException`)는 감싸지 않는다 —
    사람이 멈춘 것을 교대 실패로 적으면 사유가 틀린다.
    ⏵ 2026-10-02 정정: 전 판은 *"매니페스트 없이 run을 끝내고"*라 적었다. 러너에 최후 방어선이
    생겨 **매니페스트는 남는다** — 틀린 사유가 남는 것이 지금의 대가다."""

    def probe(_endpoint: str, _key: str) -> dict[str, object]:
        raise error

    with pytest.raises(expected) as caught:
        _swap(_command(tmp_path), probe)(("seccoderx-8b",))

    if expected is ModelSwapError:
        assert caught.value.__cause__ is error
        assert type(error).__name__ in str(caught.value)


def test_a_failed_command_raises_with_its_partial_report_and_output_tail(tmp_path: Path) -> None:
    # stderr 문구를 ASCII로 둔다 — 가짜 명령(파이썬)의 stderr 인코딩은 로캘을 따라 Windows에서
    # cp949로 나온다. 실 명령(bash)과 무관한 이 테스트 환경의 성질이다.
    body = (
        "sys.stderr.write('out of memory\\n')\n"
        "print(json.dumps({'ok': False, 'error': 'no mem'}))\n"
        "sys.exit(3)"
    )

    with pytest.raises(ModelSwapError, match="exit 3") as caught:
        _swap(_command(tmp_path, body), _probe())(("seccoderx-8b",))

    assert caught.value.report == {"ok": False, "error": "no mem"}
    assert "out of memory" in caught.value.detail


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


# --- #476: 실패 사유가 잘린 상세의 맨 앞에 남는다 ------------------------------------------


def test_the_reason_leads_the_truncated_detail_even_behind_a_long_report(tmp_path: Path) -> None:
    """보고의 `error`가 상세·메시지 맨 앞이다 — 상세는 실패 이벤트에서 앞 512자로 잘린다.

    `run_20261003_0001`에서 보고(서버 기동 인자)가 앞을 다 먹어 사유가 잘렸고 운영자는
    `memavail_before`만 보고 메모리 부족으로 읽었다. 실제 사유는 「이 run 밖의 GPU 프로세스」였다.
    """
    args = "serve Qwen/Qwen3-14B " + "--flag value " * 300  # 실측 보고처럼 긴 기동 인자
    reason = "outside GPU process: 2920 3648"
    report = {
        "ok": False,
        "memavail_before": 8.5,
        "server": {"qwen3-14b": {"args": args}},
        "error": reason,
    }
    body = "\n".join(
        [
            f"sys.stderr.write({'ABORT: ' + reason!r} + chr(10))",
            f"print(json.dumps({report!r}))",
            "sys.exit(2)",
        ]
    )

    with pytest.raises(ModelSwapError) as caught:
        _swap(_command(tmp_path, body), _probe())(("qwen3-14b",))

    shown = error_detail(caught.value)  # 실패 이벤트·경고에 실리는 그 문자열
    assert shown.startswith(f"사유: {reason}")
    assert reason in str(caught.value)
    assert caught.value.report["error"] == reason  # 보고는 그대로 실린다


def test_without_a_report_stderr_leads_the_detail(tmp_path: Path) -> None:
    """명령이 보고를 못 내면 stderr가 맨 앞이다 — `swap-remote.sh`가 ssh 전에 2로 끝나는 경우."""
    body = "sys.stderr.write('swap-remote: SWAP_SSH_HOST is empty' + chr(10))\nsys.exit(2)"

    with pytest.raises(ModelSwapError) as caught:
        _swap(_command(tmp_path, body), _probe())(("qwen3-14b",))

    assert caught.value.report == {}
    assert error_detail(caught.value).startswith("stderr: swap-remote: SWAP_SSH_HOST is empty")


# --- #477: 교대 명령의 실행 파일을 PATH로 먼저 푼다 ----------------------------------------


def test_the_command_head_is_resolved_on_path_before_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`command[0]`이 경로 없는 이름이면 `shutil.which`로 푼 절대경로로 띄운다.

    Windows `CreateProcess`는 `System32`를 PATH보다 먼저 뒤져 `bash`를 WSL 런처로 풀었다 — 그러면
    교대 스크립트가 Windows 환경변수를 못 받는다(`run_20261003_0001`). 여기서는 PATH에 없는 이름
    `harness-test-shell`을 `which`가 이 파이썬으로 풀게 해, 해소한 경로가 실제로 실행되는지 본다.
    """
    seen: list[str] = []

    def which(name: str) -> str | None:
        seen.append(name)
        return sys.executable if name == "harness-test-shell" else None

    monkeypatch.setattr(model_swap.shutil, "which", which)
    script = _command(tmp_path)[1]

    report = _swap(("harness-test-shell", script), _probe())(("seccoderx-8b",))

    assert seen == ["harness-test-shell"]
    assert report == {}  # 기본 가짜 명령은 보고를 안 낸다 — 성공 경로만 본다
    assert _calls(tmp_path)[0]["argv"] == ["seccoderx-8b"]


def test_a_command_head_with_a_path_or_not_on_path_is_left_as_is() -> None:
    assert model_swap._resolved((sys.executable, "x")) == (sys.executable, "x")
    assert model_swap._resolved(("tools/serve/x.sh",)) == ("tools/serve/x.sh",)
    assert model_swap._resolved(("no-such-shell-1f3a",)) == ("no-such-shell-1f3a",)
