"""1단계 색인이 실패하면 실서빙 모델을 부르기 전에 멈춘다 (S20 Phase 15 · @danielcho02 승인
2026-10-15).

전엔 `graph` Evidence가 `ok=False`여도 러너가 그대로 4단계로 갔다 - 심볼 인덱스 없이 파일 단위
폴백으로 돌고 전 타깃이 GRAPH 근거를 잃어 E2 위가 불가능한데 GPU 시간은 그대로 썼다. 이제:

1. 실서빙 모델에 닿는 run(`stop_at() >= SCOUT` · `served_models()` 비지 않음)은 색인 실패에서
   `INDEX_UNAVAILABLE`로 선다 - 교대·모델 호출 0건, `cli.run` 종료 코드 3.
2. `--allow-missing-index`가 풀면 경고를 남기고 가며 trace `index_stop_lifted`가 찍힌다
   (`index_missing_allowed=true`). 저장 형태(`RunRecord`·`state.db` 판)는 건드리지 않는다.
3. Fake뿐인 프로필·Scout 전에 서는 run은 영향이 없다.

색인 실패는 실제 1단계 호출(`index.run`)의 결과를 `ok=False`·exit 124로 고쳐서 만든다 -
컨테이너 어댑터가 내는 모양(시간 초과)이다.
"""

from pathlib import Path

import pytest

from src import cli
from src.config import load_config
from src.orchestrator import runner
from src.orchestrator.nodes import index
from src.schemas.enums import ReasonCode
from tests import test_stage_swap as swap_rig

_REAL_INDEX_RUN = index.run


@pytest.fixture
def failing_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """1단계 색인을 시간 초과(124)로 실패시킨다 - 호출 자체는 실제로 돌려 trace가 남는다."""

    def run(ctx, *, workdir="."):  # noqa: ANN001, ANN202
        evidence = _REAL_INDEX_RUN(ctx, workdir=workdir)
        return evidence.model_copy(
            update={"ok": False, "exit_code": 124, "error": "시간 초과 (indexer timeout)"}
        )

    monkeypatch.setattr(runner.index, "run", run)


def _lifted(events: list[dict]) -> list[dict]:
    return [e for e in events if e["kind"] == "index_stop_lifted"]


def test_a_served_run_stops_before_any_model_when_the_index_fails(
    tmp_path: Path, failing_index: None
) -> None:
    config = swap_rig._config()
    assert config.served_models(), "전제 - 실서빙 모델에 닿는 프로필이다"

    result, events, rig = swap_rig._run(tmp_path, config)

    assert result.stop_reason is ReasonCode.INDEX_UNAVAILABLE
    assert result.record.stopped_after == 1
    assert swap_rig._stop_reason(events) == "index_unavailable"
    assert rig.calls == [], "모델을 한 번도 안 불렀다"
    assert [e for e in events if e["kind"] == "model_call"] == []
    assert [e["models"] for e in swap_rig._swap_events(events)] == [""], (
        "교대는 run 끝 내림 하나뿐이다 - 실서빙 모델을 띄우지 않았다"
    )
    (warning,) = [w for w in result.warnings if "색인이 실패했다" in w]
    assert "exit_code=124" in warning and "시간 초과" in warning
    assert "--allow-missing-index" in warning and "멈췄다" in warning
    assert _lifted(events) == [], "정지를 풀지 않았다"
    assert cli._exit_for(result.stop_reason) == 3


def test_the_override_flag_goes_on_with_a_warning_and_the_trace_says_so(
    tmp_path: Path, failing_index: None
) -> None:
    config = swap_rig._config()

    result, events, rig = swap_rig._run(tmp_path, config, allow_missing_index=True)

    assert result.stop_reason is None, "멈추지 않았다"
    assert rig.calls, "모델 단계로 갔다"
    assert rig.misses == []
    (warning,) = [w for w in result.warnings if "--allow-missing-index라" in w]
    assert "exit_code=124" in warning and "GRAPH 근거" in warning
    (lifted,) = _lifted(events)
    assert lifted["index_missing_allowed"] is True
    assert lifted["exit_code"] == 124 and "시간 초과" in lifted["error"]
    assert lifted["stage"] == 1
    assert not hasattr(result.record, "index_missing_allowed"), "저장 형태를 안 건드렸다"
    assert cli._exit_for(result.stop_reason) == 0


def test_a_healthy_index_needs_no_flag_and_leaves_the_record_alone(tmp_path: Path) -> None:
    config = swap_rig._config()

    for allow in (False, True):
        result, events, rig = swap_rig._run(
            tmp_path / str(allow), config, allow_missing_index=allow
        )

        assert result.stop_reason is None and rig.calls
        assert _lifted(events) == [], "풀 것이 없었다"
        assert not [w for w in result.warnings if "색인" in w]


def test_a_fake_only_profile_is_not_stopped_by_a_failed_index(
    tmp_path: Path, failing_index: None
) -> None:
    """`dev_fake`는 `served_models()`가 비었다 - 모델 서버를 안 쓰므로 지킬 것이 없다."""
    config = load_config("dev_fake")
    assert not config.served_models()

    result, events, _rig = swap_rig._run(tmp_path, config, wire=False, allow_missing_index=True)

    assert result.stop_reason is None
    assert _lifted(events) == [], "플래그를 줬어도 풀 것이 없었다"
    assert not [w for w in result.warnings if "색인이 실패했다" in w or "allow-missing" in w]


def test_a_run_that_stops_before_scout_is_not_stopped_by_a_failed_index(
    tmp_path: Path, failing_index: None
) -> None:
    """Scout 전에 서는 run(`--stop-after 3` · `--dry-run`)은 모델을 안 부른다."""
    config = swap_rig._config()

    result, events, rig = swap_rig._run(
        tmp_path, config, stop_after=3, allow_missing_index=True
    )

    assert result.stop_reason is ReasonCode.USER_STOP_REQUESTED
    assert rig.calls == [] and _lifted(events) == []
    assert not [w for w in result.warnings if "색인이 실패했다" in w]


def test_the_cli_flag_is_opt_in_and_reaches_the_run_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    parse = cli.build_parser().parse_args
    base = ["run", "--profile", "dev_fake", "--target", str(swap_rig.TARGET)]
    assert parse(base).allow_missing_index is False
    assert parse([*base, "--allow-missing-index"]).allow_missing_index is True

    seen: list[runner.RunSpec] = []
    real = cli.execute

    def spy(config, spec, **kwargs):  # noqa: ANN001, ANN003, ANN202
        seen.append(spec)
        return real(config, spec, **kwargs)

    monkeypatch.setattr(cli, "execute", spy)
    argv = [*base, "--condition", "B3prime@pipeline", "--seed", "1", "--artifacts-root"]

    assert cli.main([*argv, str(tmp_path / "off")]) == 0, capsys.readouterr().err
    assert cli.main([*argv, str(tmp_path / "on"), "--allow-missing-index"]) == 0
    assert [spec.allow_missing_index for spec in seen] == [False, True]
