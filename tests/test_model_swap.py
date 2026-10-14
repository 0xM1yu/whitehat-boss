"""단계 경계 모델 교대 — 실패 예외·사유 코드·trace 이벤트, 그리고 교대 어댑터.

교대는 콜드 재기동이다(ADR 0012 Phase 6.5 — ADR 0009). 하네스는 교대 명령(장비 파일의
`command`)을 로컬 subprocess로 부르고, 명령이 끝나면 올린 모델마다 `/v1/models`로 창을 다시 본다.
"""

import ast
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.adapters import factory, model_swap
from src.adapters.memory_store import InMemoryStateStore
from src.adapters.model_swap import swap_runner
from src.config import load_config
from src.ids import IdFactory
from src.interfaces.model_client import ModelGatewayError, ModelSwapError, error_detail
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.runner import _is_droppable
from src.orchestrator.runner import _swap as runner_swap
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
        ("qwen3-14b", "qwen3.8-27b"),
        stage=Stage.SCOUT,
        ok=True,
        wall_ms=1234,
        report={"ok": True, "stage": 4, "wall_ms": 999, "server.qwen3-14b.util": 0.4},
    )
    ctx.record_model_swap((), stage=None, ok=False, wall_ms=5, report={}, error="내림 실패")

    swap, teardown = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_SWAP]
    assert (swap.stage, swap.wall_ms) == (int(Stage.SCOUT), 1234)
    assert swap.data == {
        "models": "qwen3-14b qwen3.8-27b",
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

    _swap(_command(tmp_path), _probe(seen=seen))(("qwen3.8-27b", "qwen3-14b"))

    (call,) = _calls(tmp_path)
    assert call["argv"] == ["qwen3.8-27b", "qwen3-14b"]
    assert call["env"] == {
        "HARNESS_PYTHON": sys.executable,
        "SWAP_DEADLINE_S": "600",
        "HARNESS_RUN_ID": RUN_ID,
        "HARNESS_DEVICE": "rig",
    }
    assert seen == [
        (MODELS["qwen3.8-27b"].endpoint, "qwen3.8-27b"),
        (MODELS["qwen3-14b"].endpoint, "qwen3-14b"),
    ]


def test_the_last_stdout_line_is_flattened_into_scalars(tmp_path: Path) -> None:
    """보고는 stdout **마지막 줄** JSON이다. 중첩은 점으로 펴고, 목록은 JSON 문자열로 남긴다 —
    trace `data`는 스칼라만 받는다."""
    report = {
        "ok": True,
        "stage": 4,
        "models": ["qwen3-14b"],
        "server": {"qwen3-14b": {"action": "launch", "util": 0.3, "args": ["--x"]}},
        "memavail_before": 100.5,
        "error": None,
    }
    body = f"print('기동 로그 한 줄')\nprint({json.dumps(json.dumps(report))})"

    got = _swap(_command(tmp_path, body), _probe())(("qwen3-14b",))

    assert got == {
        "ok": True,
        "stage": 4,
        "models": '["qwen3-14b"]',
        "server.qwen3-14b.action": "launch",
        "server.qwen3-14b.util": 0.3,
        "server.qwen3-14b.args": '["--x"]',
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
            swap_to(("qwen3-14b",))
        assert caught.value.report == {}
    else:
        assert swap_to(("qwen3-14b",)) == {}


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
        _swap(_command(tmp_path), probe)(("qwen3-14b",))

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
        _swap(_command(tmp_path, body), _probe())(("qwen3-14b",))

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
        _swap(_command(tmp_path, body), _probe(), deadline_s=2)(("qwen3-14b",))

    assert [call["argv"] for call in _calls(tmp_path)] == [["qwen3-14b"], []]


def test_a_window_other_than_the_context_limit_stops_the_swap(tmp_path: Path) -> None:
    """명령이 0으로 끝나도 믿지 않는다 — 떠 있는 창이 registry `context_limit`과 다르면 멈춘다.

    2026-09-25 ATOM에서 `context_limit` 40,960인 Scout를 16,384로 띄운 채 돌렸고 하네스는 아무 말도
    안 했다 — 프롬프트 12건이 400으로 거절됐고 한 run은 후보 0으로 끝났다(`run_20260925_0005`·
    `0006`). 400은 러너가 **그 후보만 버리는** 크기 거절이라 run은 「완주」로 끝난다.
    """
    body = "print(json.dumps({'ok': True}))"

    with pytest.raises(ModelSwapError, match=r"qwen3-14b.*16384.*40960") as caught:
        _swap(_command(tmp_path, body), _probe({"qwen3-14b": 16384}))(("qwen3-14b",))

    assert caught.value.report == {"ok": True}


def test_a_window_the_server_does_not_report_also_stops_the_swap(tmp_path: Path) -> None:
    """창을 못 읽으면 대조를 못 한 것이지 맞은 것이 아니다(CLAUDE.md §0)."""
    with pytest.raises(ModelSwapError, match="qwen3-14b"):
        _swap(_command(tmp_path), _probe({"qwen3-14b": None}))(("qwen3-14b",))


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

    report = _swap(("harness-test-shell", script), _probe())(("qwen3-14b",))

    assert seen == ["harness-test-shell"]
    assert report == {}  # 기본 가짜 명령은 보고를 안 낸다 — 성공 경로만 본다
    assert _calls(tmp_path)[0]["argv"] == ["qwen3-14b"]


def test_a_command_head_with_a_path_or_not_on_path_is_left_as_is() -> None:
    assert model_swap._resolved((sys.executable, "x")) == (sys.executable, "x")
    assert model_swap._resolved(("tools/serve/x.sh",)) == ("tools/serve/x.sh",)
    assert model_swap._resolved(("no-such-shell-1f3a",)) == ("no-such-shell-1f3a",)


# --- #477: 해소한 bash가 WSL 런처면 띄우지 않는다 ---------------------------------------------
# 아래 두 문자열은 **지어낸 샘플이 아니다** — 2026-10-05 이 PC(Windows 11 · Python 3.14.5)에서 두
# PATH로 `shutil.which("bash")`를 불러 나온 값 그대로다. 레지스트리 PATH(PowerShell·cmd 기본)에서는
# System32의 런처가, 앞에 Git의 `usr/bin`을 붙인 PATH(Git Bash로 띄운 셸)에서는 Git Bash가 나온다.
# `sys.platform`·`which`를 대역으로 바꿔 Windows가 아닌 장비에서도 돈다.
_WSL_LAUNCHER = r"C:\Windows\system32\bash.EXE"
_GIT_BASH = r"C:\Program Files\Git\usr\bin\bash.EXE"


@pytest.mark.parametrize(
    ("system_root", "launcher"),
    [
        (r"C:\WINDOWS", _WSL_LAUNCHER),
        (None, _WSL_LAUNCHER),
        (r"D:\Winnt", r"D:\Winnt\system32\bash.EXE"),
    ],
    ids=["SystemRoot-in-another-case", "SystemRoot-unset", "relocated-windows-directory"],
)
def test_a_bash_that_resolves_to_the_wsl_launcher_is_refused_when_the_swap_is_attempted(
    monkeypatch: pytest.MonkeyPatch, system_root: str | None, launcher: str
) -> None:
    """`which`가 푼 `bash`가 `%SystemRoot%`의 System32 아래 런처면 **띄우지 않고** `ModelSwapError`.

    **만들 때가 아니라 부를 때** 난다 — 만들 때 던지면 교대 안 하는 run(`--dry-run`·낮은
    `--stop-after`)까지 `build_deps`에서 죽는다(아래 `…never_resolves_the_command_head`). 부를 때
    난 `ModelSwapError`는 `swap_to`를 **감싸이지 않고** 지나야 러너가 교대면 `model_swap_failed`로,
    run 끝 내림이면 경고로 읽는다(`runner._swap`·`run_pipeline`의 `except ModelSwapError`) —
    감싸이면 「예상 밖 오류」로 적힌다.
    `usr/bin`이 System32보다 앞에 없는 PATH(레지스트리 PATH — PowerShell·cmd)에서는 `which`도
    `CreateProcess`와 같은 런처를 고른다. 그대로 띄우면 교대 스크립트가 WSL 안에서 돌아 Windows
    환경변수를 하나도 못 받고 `SWAP_SSH_HOST`이 비었다 같은 엉뚱한 사유로 실패한다
    (`run_20261003_0001`). 판정은 대소문자를 가리지 않고, `SystemRoot`가 없으면 `C:/Windows`이며,
    다른 위치의 Windows 디렉터리도 따른다.
    """
    started: list[object] = []
    with monkeypatch.context() as patched:
        patched.setattr(sys, "platform", "win32")
        patched.setattr(model_swap.shutil, "which", lambda _name: launcher)
        patched.setattr(model_swap.subprocess, "run", lambda *args, **_kw: started.append(args))
        if system_root is None:
            patched.delenv("SystemRoot", raising=False)
        else:
            patched.setenv("SystemRoot", system_root)
        swap_to = swap_runner(
            ("bash", "tools/serve/swap-remote.sh"),
            models=MODELS,
            deadline_s=600,
            run_id=RUN_ID,
            device="rig",
            probe=_probe(),
        )  # 만들기는 성공한다

        for keys in (("qwen3-14b",), ()):  # 교대, 그리고 run 끝 내림(키 없음)
            with pytest.raises(ModelSwapError) as refused:
                swap_to(keys)

            message = str(refused.value)
            assert message.startswith("교대 명령의 bash가 WSL 런처로 풀렸다")  # 안 감싸였다
            assert refused.value.__cause__ is None
            assert launcher in message
            assert "command[0]" in message  # 어떻게 푸는지까지 말한다
            assert "repo_dirty" in message  # 그 해법의 대가까지 — 비교표에서 빠진다

    assert started == []


def test_the_launcher_refusal_is_recorded_by_the_runner_as_a_failed_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """부를 때 난 거부는 러너의 교대 채널을 탄다 — `model_swap ok=false`에 사유가 남고
    `ModelSwapError`가 그대로 올라간다(`run_pipeline`의 `except ModelSwapError`가 그것을
    `model_swap_failed`로 읽는다 — `test_stage_swap.py`의 `test_a_failed_swap_stops_the_run…`).
    만들 때 던지면 이 채널을 못 지나 `build_deps`에서 트레이스백으로 끝난다."""
    ctx = _ctx()
    started: list[object] = []
    with monkeypatch.context() as patched:
        patched.setattr(sys, "platform", "win32")
        patched.setattr(model_swap.shutil, "which", lambda _name: _WSL_LAUNCHER)
        patched.setattr(model_swap.subprocess, "run", lambda *args, **_kw: started.append(args))
        patched.setenv("SystemRoot", r"C:\Windows")
        ctx.swap_to = swap_runner(
            ("bash", "tools/serve/swap-remote.sh"),
            models=MODELS,
            deadline_s=600,
            run_id=RUN_ID,
            device="rig",
            probe=_probe(),
        )

        with pytest.raises(ModelSwapError):
            runner_swap(ctx, ("qwen3-14b",), stage=Stage.SCOUT)

    (event,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_SWAP]
    assert event.data["ok"] is False
    assert str(event.data["error"]).startswith("교대 명령의 bash가 WSL 런처로 풀렸다")
    assert started == []


@pytest.mark.parametrize("device", ["atom", "vastai", "vastai-remote"])
def test_assembling_a_swap_runner_never_resolves_the_command_head(
    monkeypatch: pytest.MonkeyPatch, device: str
) -> None:
    """만들 때는 `which`를 안 부른다 — 부르면(=거부가 만들 때 나면) 교대 안 하는 run이 죽는다.

    `factory._swap_to`의 docstring이 「모델에 안 닿는 run(`--dry-run`·낮은 `--stop-after`)은 교대도
    안 하므로 장비 없이도 조립돼야 한다」고 적는다. 이 조립(`build_deps`)에서 던지면 레지스트리
    PATH의 PowerShell·cmd에서 `run --dry-run --device atom`이 트레이스백으로 끝나고 run 디렉터리에
    `state.db`·`trace.jsonl`만 남았다(거부가 만들 때 날 때 — 2026-10-05 이 PC 실측). 장비 파일
    셋(`atom`·`vastai`·`vastai-remote`)은 전부 맨 `bash`를 명령으로 든다.
    """

    def which(name: str) -> str | None:
        raise AssertionError(f"교대 콜러블을 만들 때 which를 불렀다: {name}")

    monkeypatch.setattr(model_swap.shutil, "which", which)

    swap_to = factory._swap_to(load_config("harness_pool"), run_id=RUN_ID, device=device)

    assert callable(swap_to)


@pytest.mark.parametrize(
    ("name", "found"),
    [
        ("bash", _GIT_BASH),
        ("cmd", r"C:\Windows\system32\cmd.EXE"),
        ("wsl", r"C:\Windows\system32\wsl.EXE"),
    ],
    ids=["git-bash", "other-system32-tool", "wsl-asked-for-by-name"],
)
def test_only_the_system32_bash_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, found: str
) -> None:
    """Git Bash는 절대경로로 띄운다. System32의 다른 도구(`cmd`)와 `wsl`을 이름으로 부른 것은
    사용자가 고른 것이라 막지 않는다 — 막는 것은 **`bash`가 런처로 풀린 경우**뿐이다."""
    with monkeypatch.context() as patched:
        patched.setattr(sys, "platform", "win32")
        patched.setattr(model_swap.shutil, "which", lambda _name: found)
        patched.setenv("SystemRoot", r"C:\Windows")
        resolved = model_swap._resolved((name, "tools/serve/swap-remote.sh"))

    assert resolved == (found, "tools/serve/swap-remote.sh")


@pytest.mark.parametrize(
    "found", ["/usr/bin/bash", _WSL_LAUNCHER], ids=["posix-bash", "launcher-string-off-windows"]
)
def test_the_launcher_guard_never_fires_off_windows(
    monkeypatch: pytest.MonkeyPatch, found: str
) -> None:
    """Windows가 아니면 경로가 무엇이든 발화하지 않는다 — 둘째 행은 경로 판정 앞의 **플랫폼 문**을
    따로 핀한다(Windows 경로 문자열이 리눅스에서 올 일은 없지만 문이 빠지면 여기서 빨개진다)."""
    with monkeypatch.context() as patched:
        patched.setattr(sys, "platform", "linux")
        patched.setattr(model_swap.shutil, "which", lambda _name: found)
        resolved = model_swap._resolved(("bash", "x"))

    assert resolved == (found, "x")


def test_a_command_head_with_a_path_is_neither_looked_up_nor_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`command[0]`에 경로를 직접 적었으면 `which`로 푼 것이 아니다 — 부르지도 막지도 않는다."""

    def which(name: str) -> str | None:
        raise AssertionError(f"경로가 든 이름을 which로 풀었다: {name}")

    with monkeypatch.context() as patched:
        patched.setattr(sys, "platform", "win32")
        patched.setattr(model_swap.shutil, "which", which)
        resolved = model_swap._resolved((sys.executable, "x"))

    assert resolved == (sys.executable, "x")


# --- #476: 타임아웃 분기와 상세 순서 -------------------------------------------------------
# 사유는 `run_20261003_0001`의 것이다 — 원격 교대의 메모리 판정이 낸 한 줄.
_REASON = "outside GPU process: 2920 3648"


def test_a_timeout_keeps_the_partial_report_and_leads_the_detail_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """마감을 넘긴 명령이 stdout에 남긴 JSON 보고의 사유가 상세 맨 앞에 서고, 보고는 예외에 남는다.

    `TimeoutExpired`의 출력 형은 플랫폼 의존이다 — POSIX는 `text=True`여도 bytes(CPython
    `subprocess.run` 소스의 주석으로 읽었다, 안 쟀다), Windows는 str(2026-10-05 이 PC · Python
    3.14.5 실측). 대역은 POSIX 모양(bytes)을 올려 `_text`의 bytes 갈래를 본다. `subprocess.run`
    대역이라 진짜 kill은 아니다 — 그것은
    `test_a_command_past_its_deadline_is_torn_down_before_raising`가 본다.
    사유 뒤에는 stderr, 그 뒤에 stdout 꼬리다(자르면 뒤가 잘린다).
    """
    report = {
        "ok": False,
        "memavail_before": 8.5,
        "server": {"qwen3-14b": {"args": "serve Qwen/Qwen3-14B"}},
        "error": _REASON,
    }
    stdout = ("log line before the report\n" + json.dumps(report)).encode()
    stderr = f"ABORT: {_REASON}\n".encode()
    argvs: list[list[str]] = []

    def fake_run(
        argv: list[str], *, timeout: float, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        argvs.append(list(argv))
        if len(argvs) == 1:
            raise subprocess.TimeoutExpired(argv, timeout, stdout, stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")  # 내림 명령

    monkeypatch.setattr(model_swap.subprocess, "run", fake_run)

    with pytest.raises(ModelSwapError, match="마감") as caught:
        _swap((sys.executable, "swap.py"), _probe())(("qwen3-14b",))

    assert [argv[2:] for argv in argvs] == [["qwen3-14b"], []]  # 교대, 그리고 내림 한 번
    shown = error_detail(caught.value)
    assert shown.startswith(f"사유: {_REASON} | stderr: ABORT: {_REASON}")
    assert caught.value.report == {
        "ok": False,
        "memavail_before": 8.5,
        "server.qwen3-14b.args": "serve Qwen/Qwen3-14B",
        "error": _REASON,
    }


def test_a_teardown_the_resolver_refuses_does_not_hide_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """마감 뒤 내림 호출이 `_resolved`의 거부(`run`이 풀다 낸 `ModelSwapError`)를 만나도 원래 마감
    실패가 남는다 — `_teardown`은 원래 실패를 가리지 않도록 예외를 안 올린다. 실제로는 첫 호출이
    풀린 뒤라 같은 PATH에서 둘째가 거부될 일이 드물다 — 이 갈래는 계약을 지키려고 둔 것이다."""
    resolved_calls: list[tuple[str, ...]] = []
    real_resolved = model_swap._resolved

    def resolved(command: tuple[str, ...]) -> tuple[str, ...]:
        resolved_calls.append(command)
        if len(resolved_calls) > 1:
            raise ModelSwapError("거부")
        return real_resolved(command)

    def fake_run(argv: list[str], *, timeout: float, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(model_swap, "_resolved", resolved)
    monkeypatch.setattr(model_swap.subprocess, "run", fake_run)

    with pytest.raises(ModelSwapError, match="마감") as caught:
        _swap((sys.executable, "swap.py"), _probe())(("qwen3-14b",))

    assert "내림 명령을 불렀다(실패: ModelSwapError)" in str(caught.value)


def test_a_teardown_failure_of_an_unforeseen_type_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_teardown`의 `except`는 **좁다** — 마감·실행 실패·해소 거부(`TimeoutExpired`·`OSError`·
    `ModelSwapError`)만 삼킨다. 그 밖의 예외(여기서는 `RuntimeError`)는 올라가 `swap_to`의 경계가
    「예상 밖 오류」로 감싼다 — 내림이 계약 밖으로 죽었는데 마감 실패로 가려지면 안 된다."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], *, timeout: float, **_kwargs: object) -> object:
        calls.append(list(argv))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, timeout)
        raise RuntimeError("내림 중 계약 밖")

    monkeypatch.setattr(model_swap.subprocess, "run", fake_run)

    with pytest.raises(ModelSwapError) as caught:
        _swap((sys.executable, "swap.py"), _probe())(("qwen3-14b",))

    assert len(calls) == 2  # 교대 · 내림
    assert str(caught.value).startswith("모델 교대 중 예상 밖 오류(RuntimeError: 내림 중 계약 밖)")
    assert isinstance(caught.value.__cause__, RuntimeError)


@pytest.mark.parametrize(
    ("head", "which_result", "shown"),
    [
        ("bash", _GIT_BASH, _GIT_BASH),
        ("no-such-shell-1f3a", None, "no-such-shell-1f3a"),
    ],
    ids=["resolved-path", "name-not-on-path"],
)
def test_a_command_that_cannot_start_names_the_executable_it_tried(
    monkeypatch: pytest.MonkeyPatch, head: str, which_result: str | None, shown: str
) -> None:
    """실행 실패(`OSError`) 메시지는 **띄우려던 실행 파일**을 보여 준다 — `which`가 푼 경로면
    그것을, 못 풀었으면 이름을. 호출 시점 해소로 바꾼 뒤 맨 이름(`bash`)만 보이면 #477의 요점인
    「어느 `bash`가 골라졌나」가 이 경로에서 사라진다. Windows의 `[WinError 2]`는 `exc`에 경로가
    안 든다."""

    def fake_run(argv: list[str], **_kwargs: object) -> object:
        raise OSError(2, "The system cannot find the file specified")

    monkeypatch.setattr(model_swap.shutil, "which", lambda _name: which_result)
    monkeypatch.setattr(model_swap.subprocess, "run", fake_run)

    with pytest.raises(ModelSwapError) as caught:
        _swap((head, "tools/serve/swap.sh"), _probe())(("qwen3-14b",))

    assert str(caught.value) == (
        f"교대 명령을 실행하지 못했다({shown}): [Errno 2] The system cannot find the file specified"
    )


def test_a_failed_command_details_the_reason_then_stderr_then_stdout(tmp_path: Path) -> None:
    """stderr와 stdout이 **둘 다** 있을 때의 순서 — 사유 → stderr → stdout.

    상세는 앞에서부터 512자로 잘린다. stdout 마지막 줄은 긴 JSON 보고라 앞에 서면 사유를 먹는다
    (#476). stderr만 있는 실패 테스트는 stdout이 없어 이 순서를 못 본다.
    """
    body = (
        "print('log line before the report')\n"
        "sys.stderr.write('ABORT: out of memory\\n')\n"
        "print(json.dumps({'ok': False, 'error': 'no mem'}))\n"
        "sys.exit(2)"
    )

    with pytest.raises(ModelSwapError) as caught:
        _swap(_command(tmp_path, body), _probe())(("qwen3-14b",))

    shown = error_detail(caught.value)
    parts = ("사유: no mem", "stderr: ABORT", "stdout: log")
    assert [shown.index(part) for part in parts] == sorted(shown.index(part) for part in parts)


def test_a_blank_error_field_is_not_a_reason(tmp_path: Path) -> None:
    """공백뿐인 `error`는 사유가 아니다 — 메시지에 빈 접미를, 상세 앞에 빈 「사유:」를 안 세운다."""
    body = (
        "sys.stderr.write('boom\\n')\nprint(json.dumps({'ok': False, 'error': '   '}))\nsys.exit(2)"
    )

    with pytest.raises(ModelSwapError) as caught:
        _swap(_command(tmp_path, body), _probe())(("qwen3-14b",))

    assert str(caught.value).endswith("(exit 2): qwen3-14b")
    assert error_detail(caught.value).startswith("stderr: boom")


# --- 문자열에 제어 문자가 먹히지 않았다 ----------------------------------------------------


def test_no_string_in_the_adapter_carries_a_control_character() -> None:
    """비 raw 문자열에 백슬래시 b를 쓰면 백스페이스가 된다 — System32 아래 bash.exe를 그렇게 적은
    `_resolved` docstring이 **소스 바이트는 멀쩡한데 실행 시 문자열에 0x08을 들고** 있었다
    (2026-10-05). 소스 바이트 검사(`test_remote_swap.py`)로는 안 잡히는 층이라 AST로 본다."""
    tree = ast.parse(Path(model_swap.__file__).read_bytes())

    leaked = sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(ord(char) < 32 and char not in "\n\t" for char in node.value)
    )

    assert leaked == []
