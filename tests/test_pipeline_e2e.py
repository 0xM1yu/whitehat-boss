"""파이프라인 end-to-end 테스트.

이 파일이 PR의 주장을 실증한다. 세 가지를 본다.

1. dev_fake 프로필로 0~9단계가 완주하고 `RunRecord.finished_at`이 채워진다
2. 같은 조건으로 두 번 실행하면 trace JSONL에서 `ts`·`wall_ms`만 뺐을 때 바이트 동일
3. 계획 §6 지표 대응표의 앞 8행이 trace에서 실제로 계산된다

**측정값은 하드코딩하지 않는다.** 제거율·토큰 수는 이 프로젝트가 재려는 값이다.
여기서 단언하는 것은 "산출 가능하다"와, Scout 제거율에 한해 "0%도 100%도 아니다"뿐이다.
"""

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src import cli, console
from src.config import load_config
from src.orchestrator.runner import RunSpec
from src.orchestrator.stages import Stage
from src.orchestrator.trace import VOLATILE_FIELDS
from src.schemas.enums import FindingState, ReasonCode
from src.schemas.run import Budget

TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
STARTED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CONDITION = "B3prime@pipeline"


def _run(tmp_path: Path, name: str = "run", **overrides: object):  # noqa: ANN202
    """dev_fake 프로필로 파이프라인을 한 번 돌리고 (결과, trace 경로)를 돌려준다."""
    config = load_config("dev_fake")
    if "budget" in overrides:
        config = config.transition(budget=overrides.pop("budget"))
    run_dir = tmp_path / name / "run_20260901_0001"
    run_dir.mkdir(parents=True)
    spec = RunSpec(condition=CONDITION, target=TARGET, started_at=STARTED_AT, **overrides)
    trace_path = run_dir / "trace.jsonl"
    with trace_path.open("w", encoding="utf-8") as stream:
        result, _ = cli.execute(config, spec, run_dir=run_dir, trace_stream=stream)
    return result, trace_path


def _events(trace_path: Path) -> list[dict]:
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]


def _canonical(trace_path: Path) -> bytes:
    """재현성 비교 표현: `ts`·`wall_ms`만 제거한 JSONL."""
    lines = []
    for event in _events(trace_path):
        for field in VOLATILE_FIELDS:
            event.pop(field, None)
        lines.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines).encode("utf-8")


def _of(events: list[dict], kind: str, *, stage: int | None = None) -> list[dict]:
    return [e for e in events if e["kind"] == kind and (stage is None or e["stage"] == int(stage))]


# --- 1. 완주 ----------------------------------------------------------------


def test_pipeline_runs_all_stages_end_to_end(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path)

    stages_seen = {e["stage"] for e in _of(_events(trace_path), "stage_start")}
    assert stages_seen == set(range(int(Stage.REPORT) + 1))
    assert result.record.finished_at is not None
    assert result.record.stopped_after is None
    assert result.record.is_complete


def test_pipeline_promotes_one_target_and_filters_the_other(tmp_path: Path) -> None:
    """파이프라인이 실제로 무언가를 통과시키고 무언가를 거른다."""
    result, _ = _run(tmp_path)

    states = {c.location.file: c.state for c in result.candidates}
    assert states["parser.c"] is FindingState.TRIAGED
    assert states["util.c"] is FindingState.QUEUED  # 폐기가 아니라 보류
    assert any(f.state is FindingState.PROVEN for f in result.findings)


def test_manifest_is_written_with_reproducibility_fields(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path)

    manifest = json.loads((trace_path.parent / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == result.record.run_id
    assert manifest["condition"] == CONDITION
    assert manifest["source_hash"].startswith("sha256:")
    assert manifest["thresholds"]["scout_threshold"] > 0
    assert manifest["finished_at"].endswith("Z")


def test_incomplete_manifest_fields_are_reported_not_faked(tmp_path: Path) -> None:
    """컨테이너 digest가 없으면 지어내지 않고 경고한다 (engineering-guide.md §5)."""
    result, _ = _run(tmp_path)

    assert result.record.tool_images == {}
    assert any("tool_images" in warning for warning in result.warnings)


# --- 2. 재현성 --------------------------------------------------------------


def test_two_runs_are_byte_identical_after_dropping_volatile_fields(tmp_path: Path) -> None:
    """재현성 기준 그대로: ts·wall_ms만 제거하면 두 실행의 trace가 바이트 동일하다."""
    _, first = _run(tmp_path, "a")
    _, second = _run(tmp_path, "b")

    assert _canonical(first) == _canonical(second)
    assert _canonical(first) != b""  # 빈 파일끼리 비교해 통과하는 것을 막는다


def test_trace_actually_carries_the_volatile_fields(tmp_path: Path) -> None:
    """위 비교가 공허하지 않다는 증거 — 제거 대상이 실제로 파일에 있다."""
    _, first = _run(tmp_path, "a")

    assert all("ts" in json.loads(line) for line in first.read_text("utf-8").splitlines())


# --- 3. 지표 산출 가능성 ----------------------------------------------------


def test_stage_removal_rates_are_derivable(tmp_path: Path) -> None:
    """계획 §6 대응표 1~4행. 분모가 0이면 산출 자체가 불가능하므로 그것부터 본다."""
    _, trace_path = _run(tmp_path)
    events = _events(trace_path)

    scout = _of(events, "transition", stage=Stage.SCOUT)
    created = [e for e in scout if e["data"]["rule"] == "C-1"]
    promoted = [e for e in scout if e["data"]["reason"] == ReasonCode.SCOUT_PROMOTED.value]
    assert created, "4단계 생성 기록이 없으면 제거율의 분모가 없다"
    scout_rate = 1 - len(promoted) / len(created)

    graded = _of(events, "gate_eval", stage=Stage.GATE)
    assert graded
    gate_rate = sum(1 for e in graded if e["data"]["grade"] == "E0") / len(graded)

    critic = _of(events, "transition", stage=Stage.CRITIC)
    assert critic
    rejected = {ReasonCode.CRITIC_REBUTTED.value, ReasonCode.CRITIC_NEED_EVIDENCE.value}
    critic_rate = sum(1 for e in critic if e["data"]["reason"] in rejected) / len(critic)

    repro = {ReasonCode.REPRO_FAILED.value, ReasonCode.REPRO_CONFIRMED.value}
    validated = [
        e for e in _of(events, "transition", stage=Stage.VALIDATE) if e["data"]["reason"] in repro
    ]
    assert validated, "8단계까지 간 Finding이 없으면 도구 검증 제거율이 정의되지 않는다"
    tool_rate = sum(
        1 for e in validated if e["data"]["reason"] == ReasonCode.REPRO_FAILED.value
    ) / len(validated)

    for rate in (scout_rate, gate_rate, critic_rate, tool_rate):
        assert 0.0 <= rate <= 1.0


def test_scout_removal_rate_is_not_degenerate(tmp_path: Path) -> None:
    """0%도 100%도 아니어야 한다 — 파이프라인이 실제로 후보를 거르고 있다는 뜻이다."""
    _, trace_path = _run(tmp_path)
    scout = _of(_events(trace_path), "transition", stage=Stage.SCOUT)
    created = [e for e in scout if e["data"]["rule"] == "C-1"]
    promoted = [e for e in scout if e["data"]["reason"] == ReasonCode.SCOUT_PROMOTED.value]

    assert 0.0 < 1 - len(promoted) / len(created) < 1.0


def test_token_and_cache_metrics_are_derivable(tmp_path: Path) -> None:
    """계획 §6 대응표 5~8행."""
    _, trace_path = _run(tmp_path)
    events = _events(trace_path)
    calls = _of(events, "model_call")
    assert calls

    ok_calls = [e for e in calls if e["data"]["ok"]]
    assert all(
        {"input_tokens", "output_tokens", "cached_tokens"} <= set(e["data"]) for e in ok_calls
    )
    inputs = sum(e["data"]["input_tokens"] for e in ok_calls)
    cached = sum(e["data"]["cached_tokens"] for e in ok_calls)
    assert inputs > 0
    # prefix를 재사용했으므로 캐시된 몫이 있어야 한다. 0이면 prefix가 흔들렸다는 뜻이다.
    assert 0 < cached / inputs <= 1

    capsule_cache = _of(events, "cache")
    assert capsule_cache, "cache 이벤트가 없으면 캡슐 적중률을 잴 수 없다 (on_cache 미배선)"
    hit_ratio = sum(1 for e in capsule_cache if e["data"]["hit"]) / len(capsule_cache)
    assert 0.0 <= hit_ratio <= 1.0

    parse_failure_rate = sum(1 for e in calls if not e["data"]["ok"]) / len(calls)
    assert 0.0 <= parse_failure_rate <= 1.0


def test_role_and_stage_are_present_for_per_role_aggregation(tmp_path: Path) -> None:
    """지표는 역할·단계별로 쪼개야 한다. 그 열이 실제로 있는지 본다."""
    _, trace_path = _run(tmp_path)
    calls = _of(_events(trace_path), "model_call")

    assert {e["data"]["role"] for e in calls} == {"scout", "auditor", "critic"}
    assert all(e["stage"] is not None and e["data"]["model"] for e in calls)


# --- 중단 경로 --------------------------------------------------------------


def test_budget_exhaustion_still_reaches_the_report(tmp_path: Path) -> None:
    """예산 소진은 실패가 아니라 종료 경로다 (architecture.md §5)."""
    tiny = Budget(input_tokens=1, output_tokens=1, gpu_seconds=0)
    result, trace_path = _run(tmp_path, budget=tiny)

    assert result.record.stopped_after is not None
    assert result.record.finished_at is not None
    assert not result.record.is_complete  # 부분 결과는 비교표에 넣지 않는다
    assert _of(_events(trace_path), "stage_start", stage=Stage.REPORT)
    assert any("예산 소진" in warning for warning in result.warnings)


def test_stop_after_retrieval_makes_no_model_calls(tmp_path: Path) -> None:
    result, trace_path = _run(tmp_path, stop_after=int(Stage.RETRIEVAL))

    assert _of(_events(trace_path), "model_call") == []
    assert result.record.stopped_after == int(Stage.RETRIEVAL)
    assert result.candidates == ()


# --- CLI --------------------------------------------------------------------


def test_doctor_passes_on_dev_profile(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = cli.main(["doctor", "--profile", "dev_fake", "--artifacts-root", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0  # WARN만으로는 실패하지 않는다
    assert "[WARN " in out and "[ERROR" not in out


def test_console_output_survives_a_non_utf8_terminal() -> None:
    """Windows 기본 콘솔(cp949)에서 `—` 하나로 doctor가 죽던 것을 막는다.

    pytest는 출력을 UTF-8로 잡아 이 문제가 테스트에 안 보였다. 스트림을 직접 만들어 본다.
    """
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp949")

    console.force_utf8(stream)
    stream.write("실측 전 초기값 임계값 — 조건 간 비교 전에 확정한다")  # 예외가 나면 실패

    assert stream.encoding == "utf-8"


def test_doctor_fails_on_unknown_profile(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    code = cli.main(["doctor", "--profile", "nope", "--artifacts-root", str(tmp_path)])

    assert code == 1
    assert "[ERROR" in capsys.readouterr().out


def test_estimate_reports_gpu_seconds_as_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """실측치가 없으면 숫자를 지어내지 않는다."""
    code = cli.main(
        [
            "estimate",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "추정 불가" in out
    assert "예상 모델 호출" in out


def test_cli_run_writes_trace_manifest_and_report(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = cli.main(
        [
            "run",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--condition",
            CONDITION,
            "--seed",
            "20260901",
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    assert code == 0
    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert (run_dir / "trace.jsonl").read_text(encoding="utf-8").strip()
    assert (run_dir / "run_manifest.json").is_file()
    assert (run_dir / "report.md").read_text(encoding="utf-8").startswith("#")
    assert "완주" in capsys.readouterr().out


def test_cli_run_can_emit_sarif(tmp_path: Path) -> None:
    cli.main(
        [
            "run",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--format",
            "sarif",
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    sarif = json.loads((run_dir / "report.sarif.json").read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"]


def test_seed_override_is_recorded_in_the_manifest(tmp_path: Path) -> None:
    """실제로 쓰인 seed가 기록되어야 재현 실행을 재구성할 수 있다."""
    cli.main(
        [
            "run",
            "--profile",
            "dev_fake",
            "--target",
            str(TARGET),
            "--seed",
            "4242",
            "--artifacts-root",
            str(tmp_path),
        ]
    )

    (run_dir,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == 4242


def test_gc_is_dry_run_by_default(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    kept = [tmp_path / f"run_2026090{i}_0001" for i in range(1, 4)]
    for path in kept:
        path.mkdir()
    (tmp_path / "not-a-run").mkdir()

    code = cli.main(["gc", "--keep", "1", "--artifacts-root", str(tmp_path)])

    assert code == 0
    assert all(path.is_dir() for path in kept)  # 기본은 아무것도 지우지 않는다
    assert "삭제 예정" in capsys.readouterr().out


def test_gc_apply_removes_only_old_run_dirs(tmp_path: Path) -> None:
    runs = [tmp_path / f"run_2026090{i}_0001" for i in range(1, 4)]
    for path in runs:
        path.mkdir()
    other = tmp_path / "not-a-run"
    other.mkdir()

    cli.main(["gc", "--keep", "1", "--apply", "--yes", "--artifacts-root", str(tmp_path)])

    assert not runs[0].exists() and not runs[1].exists()
    assert runs[2].is_dir()  # 최신 1개는 남는다
    assert other.is_dir()  # run_ 접두사가 아닌 디렉터리는 건드리지 않는다
