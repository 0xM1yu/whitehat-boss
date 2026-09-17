"""폴러(`tools/monitor.py`)가 **진짜 하네스 run**의 산출물을 읽는가 — end-to-end (이슈 #208).

`tests/test_monitor.py`는 합성 dict로 도출 규칙을 고정한다(순수 함수 단위). 여기서는 실제
`dev_fake` 파이프라인을 돌려 `heartbeat.json`·`trace.jsonl`·`run_manifest.json`을 만들고,
폴러가 그것을 읽어 **완주 run은 0으로, 크래시 run은 DEAD(exit 3)로** 판정하는지를 본다.
합성 데이터가 실제 산출물과 어긋나는 순간(필드명·완주 정의 등)을 이 다리가 잡는다.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from src import cli
from src.config import load_config
from src.orchestrator.runner import RunSpec
from tools import monitor

TARGET = Path(__file__).resolve().parent / "fixtures" / "target"
STARTED_AT = datetime(2026, 9, 1, tzinfo=UTC)
CONDITION = "B3prime@pipeline"


def _real_run(tmp_path: Path) -> Path:
    """dev_fake 파이프라인을 한 번 돌리고 산출물이 든 run_dir을 돌려준다(모델·GPU 없음)."""
    run_dir = tmp_path / "run_20260101_0001"
    run_dir.mkdir(parents=True)
    spec = RunSpec(condition=CONDITION, target=TARGET, started_at=STARTED_AT)
    with (run_dir / "trace.jsonl").open("w", encoding="utf-8") as stream:
        cli.execute(
            load_config("dev_fake"),
            spec,
            run_id=run_dir.name,
            run_dir=run_dir,
            trace_stream=stream,
        )
    return run_dir


def test_poller_reads_a_real_completed_run(tmp_path: Path) -> None:
    run_dir = _real_run(tmp_path)
    # 하네스가 세 산출물을 실제로 냈다
    assert (run_dir / "heartbeat.json").exists()
    assert (run_dir / "trace.jsonl").exists()
    assert (run_dir / "run_manifest.json").exists()

    status, basis = monitor.snapshot(
        run_dir, now=datetime.now(UTC), timeout_s=300.0, default_retries=1, error_storm_k=3
    )
    assert status["has_manifest"] is True
    assert status["incomplete"] is False  # 완주 run (finished_at 있고 stopped_after 없음)
    assert status["stuck"] is False
    assert status["error_storm"] is False
    assert status["budget_exhausted"] is False
    assert status["current_stage"] is not None
    assert (status["last_event_seq"] or 0) > 0
    # T를 합성값이 아니라 **그 run의 매니페스트 schema_retries**에서 계산했다(리뷰 M1 실경로)
    assert "manifest" in basis


def test_poller_reports_dead_on_a_crashed_real_run(tmp_path: Path) -> None:
    """매니페스트를 못 남기고 heartbeat가 stale해진 run = 조용히 죽음 → DEAD, exit 3."""
    run_dir = _real_run(tmp_path)
    (run_dir / "run_manifest.json").unlink()  # 크래시: 완주 마커가 없다
    hb = json.loads((run_dir / "heartbeat.json").read_text(encoding="utf-8"))
    hb["ts"] = "2000-01-01T00:00:00Z"  # heartbeat가 c×N을 한참 넘겨 stale
    (run_dir / "heartbeat.json").write_text(json.dumps(hb), encoding="utf-8")

    assert monitor.main([str(run_dir)]) == 3  # 첫 폴에서 DEAD 경보 후 종료(루프 안 돎)
