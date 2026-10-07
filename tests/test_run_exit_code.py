"""`run`의 종료 코드는 정지 사유가 정한다 (#533) — 장애로 멈춘 run은 3, 정상 종료는 0.

**사유는 `runner._analyze`가 예외를 내게 해서 주입한다.** 러너의 `except` 갈래가 사유를 정하고
`RunResult`가 그것을 `cli.run`까지 나르는 길 전체가 돈다. 사유 없음(`None`)에 기대면 「0이
나온다」만 보고 비-0 갈래를 못 문다 — 정상 종료 쪽도 0을 **사유 없음이 아니라 사유별로** 문다.
"""

import dataclasses
from pathlib import Path

import pytest

from src import cli
from src.interfaces.model_client import ModelGatewayError, ModelSwapError, SchemaParseError
from src.orchestrator import runner
from src.orchestrator.budget import BudgetExhausted
from src.schemas.enums import ReasonCode

TARGET = Path(__file__).resolve().parent / "fixtures" / "target"


def _argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "run", "--profile", "dev_fake", "--target", str(TARGET), "--condition", "B3prime@pipeline",
        "--seed", "1", "--artifacts-root", str(tmp_path / "arts"), *extra,
    ]  # fmt: skip


def _analyze_raises(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(runner, "_analyze", boom)


# 장애로 멈춘 넷 — 러너 `except` 갈래(`MODEL_SWAP_FAILED` 등)가 각각 이 예외를 받는다.
FAULTS = [
    (ModelSwapError("교대 실패"), ReasonCode.MODEL_SWAP_FAILED),
    (ModelGatewayError("연결 실패"), ReasonCode.MODEL_GATEWAY_UNAVAILABLE),
    (runner._TooManyDrops("연속 폐기"), ReasonCode.CONSECUTIVE_TARGET_FAILURES),
    (SchemaParseError("파싱 실패"), ReasonCode.SCHEMA_PARSE_FAILED),
]


@pytest.mark.parametrize(("exc", "reason"), FAULTS, ids=[r.value for _, r in FAULTS])
def test_a_run_stopped_by_a_fault_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    exc: Exception,
    reason: ReasonCode,
) -> None:
    _analyze_raises(monkeypatch, exc)

    code = cli.main(_argv(tmp_path))

    out, err = capsys.readouterr()
    assert code == 3
    assert f"[ERROR] 장애로 중단했다: {reason.value}" in err
    assert "[WARN ]" in err, "사유 문장이 든 기존 경고 줄은 그대로 남는다"
    assert "완주 여부: 부분 결과" in out
    (run_dir,) = (tmp_path / "arts").iterdir()
    assert (run_dir / "run_manifest.json").is_file(), "비-0이어도 산출물은 남는다"


# 정상 종료 — 사용자가 정한 상한 둘(`--stop-after`·`--dry-run`)과 예산 소진(러너 docstring
# 「예산 소진」: 실패가 아니라 정상적인 종료 경로), 그리고 완주.
NORMAL = [
    ("complete", [], None),
    ("stop_after", ["--stop-after", "4"], None),
    ("dry_run", ["--dry-run"], None),
    ("budget", [], BudgetExhausted(4, "input_tokens", 10, 5)),
]


@pytest.mark.parametrize(("extra", "exc"), [c[1:] for c in NORMAL], ids=[c[0] for c in NORMAL])
def test_a_run_that_ends_normally_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    extra: list[str],
    exc: Exception | None,
) -> None:
    if exc is not None:
        _analyze_raises(monkeypatch, exc)

    code = cli.main(_argv(tmp_path, *extra))

    assert code == 0
    assert "[ERROR]" not in capsys.readouterr().err


@pytest.mark.parametrize(("exc", "reason"), FAULTS, ids=[r.value for _, r in FAULTS])
def test_the_error_line_points_at_the_warn_line_that_holds_the_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    exc: Exception,
    reason: ReasonCode,
) -> None:
    """`[ERROR]` 줄이 「원인 문구는 위 `[WARN ]` 줄에 있다」고 말하므로 그것이 참이어야 한다."""
    _analyze_raises(monkeypatch, exc)

    cli.main(_argv(tmp_path))

    lines = capsys.readouterr().err.splitlines()
    error_at = next(i for i, line in enumerate(lines) if line.startswith("[ERROR]"))
    assert any(line.startswith("[WARN ]") and str(exc) in line for line in lines[:error_at]), (
        f"원인 문구 {str(exc)!r}가 [ERROR] 위의 [WARN ] 줄에 없다"
    )


# 정상(0)으로 읽는 사유 — **일부러 코드에서 안 가져오고 여기 다시 적는다.** 허용목록을 늘리면 이
# 검사가 빨개져 분류를 의식적으로 바꾸게 한다. 목록에 없는 사유는 이름이 무엇이든 3이다.
ZERO_REASONS = {ReasonCode.USER_STOP_REQUESTED, ReasonCode.DRY_RUN, ReasonCode.BUDGET_EXHAUSTED}


def test_a_run_with_no_stop_reason_exits_zero() -> None:
    assert cli._exit_for(None) == 0


@pytest.mark.parametrize("reason", list(ReasonCode), ids=lambda reason: reason.value)
def test_every_reason_code_exits_zero_only_if_allowlisted_else_three(reason: ReasonCode) -> None:
    """`ReasonCode` **전체**를 돈다 — 장애를 나열하는 거부목록이면 나열에 없는 사유가 0이 된다.

    새 사유가 생기면 이 검사가 그것도 돈다: 허용목록에 넣지 않는 한 3이어야 한다(#533).
    """
    assert cli._exit_for(reason) == (0 if reason in ZERO_REASONS else 3)


def test_run_result_stop_reason_has_no_default() -> None:
    """기본값 `None`은 「끝까지 갔다」로 읽힌다 — 새 생성처가 빠뜨리면 조용한 0이 된다(#533)."""
    field = next(f for f in dataclasses.fields(runner.RunResult) if f.name == "stop_reason")
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING
