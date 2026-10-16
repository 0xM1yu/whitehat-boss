"""폴러(`tools/monitor.py`)가 **진짜 하네스 run**의 산출물을 읽는가 — end-to-end (이슈 #208).

`tests/test_monitor.py`는 도출 규칙을 합성 dict로 고정하고, 맨 끝 두 건만 `main()`의 종료
갈래(stopped-run→0 · bad-dir→2)를 실경로로 본다. **여기가 메우는 자리는 둘이다** — 실제
`dev_fake` 파이프라인을 돌려 `heartbeat.json`·`trace.jsonl`·`run_manifest.json`을 만들고,
폴러가 ① 완주 run을 그 산출물에서 읽는지 ② 크래시 run에 DEAD(exit 3)를 내는지를 본다.
DEAD 갈래는 그쪽 파일에 실경로 테스트가 없다.

**단언을 고를 때 「변이로 죽는가」를 기준으로 삼았다.** 아래 셋은 실제로 변이를 넣어 확인했다
(`tools/monitor.py`·`src/orchestrator/heartbeat.py`를 고장 내고 이 파일이 red가 되는지):

| 변이 | 이 파일이 잡는 단언 |
| --- | --- |
| `stuck_ceiling_s`가 매니페스트 대신 config 기본을 쓴다 | `stuck_after_s` 수치 · `basis` |
| heartbeat writer가 `ts` 필드를 개명한다 | `alive is True` |
| `main()`의 DEAD 갈래가 `return 3`을 잃는다 | `sleep` 금지 — 없으면 red가 아니라 **CI 행** |
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.adapters.openai_compat import _TIMEOUT_S
from src.orchestrator import runner
from src.orchestrator.runner import _MAX_CONSECUTIVE_DROPS
from tests.test_pipeline_e2e import _run
from tools import monitor


def _real_run(tmp_path: Path) -> Path:
    """dev_fake 파이프라인을 한 번 돌리고 산출물이 든 run_dir을 돌려준다(모델·GPU 없음).

    **`test_pipeline_e2e._run`을 부른다 — 본문을 복사하지 않는다.** 그 헬퍼 docstring이
    복사를 금지한다: *"이 헬퍼 본문을 복사하게 되고, 그러면 `cli.execute` 인자가 늘 때
    **복사한 쪽만 조용히 뒤처진다**(팀 리뷰 #161-3)."* 복사본은 `cli.execute`에 선택
    인자가 느는 날 TypeError 없이 통과하면서 **하네스가 실제로 내는 것과 다른 산출물**을
    검사하게 된다 — 그러면 이 파일이 지키려는 「실경로」가 사라진다.
    """
    _, trace_path = _run(tmp_path)
    return trace_path.parent


def test_poller_reads_a_real_completed_run(tmp_path: Path) -> None:
    run_dir = _real_run(tmp_path)
    # 하네스가 세 산출물을 실제로 냈다
    assert (run_dir / "heartbeat.json").exists()
    assert (run_dir / "trace.jsonl").exists()
    assert (run_dir / "run_manifest.json").exists()

    # **두 분기가 구조적으로 안 겹치게 `default_retries`를 고른다.** config 기본을 그대로
    # 주면(`schema_retries: 1`, 전 프로필 공통 — `monitor.main`의 `--profile` 도움말) 매니페스트
    # 분기와 T가 똑같이 나와 「매니페스트를 읽었는가」를 수치로 못 가른다. 적용값+1이면 어떤
    # config 값에서도 둘이 다르므로 마법수도, 겹침을 막는 가드 단언도 필요 없다.
    #
    # 하드 인덱싱은 의도다 — `.get()`이면 키가 이사한 날 조용히 건너뛴다. `RunRecord.thresholds`는
    # `dict[str, float]`(열린 dict)라 스키마가 이 키를 보장하지 않으므로, **KeyError가 나면
    # 고칠 곳은 이 줄이 아니라 `stuck_ceiling_s`와 여기 둘 다**다.
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    applied = int(manifest["thresholds"]["schema_retries"])

    status, basis = monitor.snapshot(
        run_dir,
        now=datetime.now(UTC),
        timeout_s=_TIMEOUT_S,
        default_retries=applied + 1,
        error_storm_k=_MAX_CONSECUTIVE_DROPS,
    )
    assert status["has_manifest"] is True
    assert status["incomplete"] is False  # 완주 run (finished_at 있고 stopped_after 없음)
    assert status["current_stage"] is not None
    assert (status["last_event_seq"] or 0) > 0

    # **폴러가 러너의 heartbeat를 실제로 읽었다.** 필드명이 드리프트하면 `hb_ts`가 None이 돼
    # `alive`가 False로 떨어진다 — 이 단언이 없으면 개명이 조용히 통과한다.
    assert status["alive"] is True

    # **T를 그 run의 매니페스트에서 계산했다** — 수치로 가른다. config 기본으로 떨어졌다면
    # 한 단계 큰 값이 나온다.
    assert status["stuck_after_s"] == (applied + 1) * _TIMEOUT_S
    assert "manifest" in basis  # 수치와 별개로 근거 문자열도 매니페스트를 가리킨다

    # 정상 완주 run이므로 셋 다 꺼져 있어야 한다. **거짓양성만 잡는다** — dev_fake는 실패·
    # wedge·예산 소진 경로를 안 돌아(`critic_failed`·`target_skipped` 0건) 거짓음성은 여기서
    # 못 잡는다. 그쪽은 `test_monitor.py`의 합성 dict가 본다.
    assert status["stuck"] is False
    assert status["error_storm"] is False
    assert status["budget_exhausted"] is False


def test_poller_reports_dead_on_a_crashed_real_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """매니페스트를 못 남기고 heartbeat가 stale해진 run = 조용히 죽음 → DEAD, exit 3."""
    run_dir = _real_run(tmp_path)
    (run_dir / "run_manifest.json").unlink()  # 크래시: 완주 마커가 없다
    hb = json.loads((run_dir / "heartbeat.json").read_text(encoding="utf-8"))
    # **대입 전에 키의 실재를 확인한다.** 대입은 없는 키도 만들어서, 필드가 개명되면 이 테스트가
    # 신선한 진짜 heartbeat 옆에 죽은 `ts`를 하나 더 놓고도 통과한다.
    assert "ts" in hb, (
        "heartbeat writer와 폴러가 `ts`에서 어긋났다 — `tools/monitor.py`의 `derive_status`가 "
        "그 키를 직접 읽으므로 **개명은 폴러도 깨뜨린다.** 둘 다 고쳐라(이 리터럴만 고치면 "
        "살아 있는 run이 영구히 DEAD로 보고된다)."
    )
    hb["ts"] = "2000-01-01T00:00:00Z"  # heartbeat가 c×N을 한참 넘겨 stale
    (run_dir / "heartbeat.json").write_text(json.dumps(hb), encoding="utf-8")

    # **DEAD 갈래를 잃으면 red가 아니라 행이다.** `main()`은 `--once` 없이 `--interval` 기본
    # 5초로 무한 폴링하고 이 저장소에는 pytest 타임아웃이 없다. sleep에 닿는 것 자체를 실패로
    # 만들어, 회귀가 CI를 멈추는 대신 즉시 red로 나오게 한다.
    def _no_sleep(seconds: float) -> None:
        pytest.fail(f"DEAD 갈래를 안 타고 폴 루프로 빠졌다 (sleep {seconds}s)")

    monkeypatch.setattr(monitor.time, "sleep", _no_sleep)

    assert monitor.main([str(run_dir)]) == 3  # 첫 폴에서 DEAD 경보 후 종료


def test_poller_reports_crashed_on_a_real_run_that_the_last_line_of_defense_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """🔴 예상 밖 예외로 터진 **진짜 run**은 매니페스트를 남기고(S0), 폴러는 CRASHED로 읽는다.

    `has_manifest`로만 끝내면 exit 0 — 터진 run이 정상 종료로 보이고 사유는 아무 플래그에도 안
    뜬다(적대적 리뷰 2026-10-02 MEDIUM 3). 위 DEAD 갈래와 같은 이유로 sleep을 막는다."""

    class _Defect(KeyError):
        pass

    def explode(*_args: object, **_kwargs: object) -> None:
        raise _Defect("예상 밖")

    monkeypatch.setattr(runner.critic, "run", explode)
    with pytest.raises(_Defect):
        _real_run(tmp_path)
    run_dir = tmp_path / "run" / "run_20260901_0001"
    assert (run_dir / "run_manifest.json").exists(), "최후 방어선이 매니페스트를 안 남겼다"

    def _no_sleep(seconds: float) -> None:
        pytest.fail(f"CRASHED 갈래를 안 타고 폴 루프로 빠졌다 (sleep {seconds}s)")

    monkeypatch.setattr(monitor.time, "sleep", _no_sleep)
    capsys.readouterr()  # 앞선 출력을 비운다

    assert monitor.main([str(run_dir)]) == 3
    captured = capsys.readouterr()
    assert "crashed=!" in captured.out
    assert "unforeseen_failure" in captured.err
