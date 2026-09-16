"""heartbeat 파일 계약 — 러너가 살아 있는지를 밖에서 읽게 한다 (이슈 #208 ②).

여기서 고정하는 핵심은 **trace가 침묵해도 heartbeat는 틱한다**는 것이다 — 그래야
외부 관찰자가 "프로세스가 죽었나 / 느린 호출 중인가"를 가른다. 나머지는 파일이
기대한 필드를 담고, 원자적으로 쓰이며(반쯤 쓰인 파일 없음), 종료가 멱등인지다.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from src.orchestrator.heartbeat import Heartbeat

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


def _read(path: Path) -> dict:
    # Windows에선 스레드의 `os.replace`와 이 읽기가 겹치면 일시적으로 PermissionError가
    # 난다(원자 교체의 반대편). 실서비스 폴러(`tools/monitor.py`)도 같은 것을 견뎌야 하고,
    # 여기서는 잠깐 재시도로 그 레이스를 넘긴다 — 테스트가 보는 것은 틱 동작이지 이 레이스가
    # 아니다.
    for _ in range(50):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            time.sleep(0.005)
    return json.loads(path.read_text(encoding="utf-8"))


def test_start_writes_the_file_immediately(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    hb = Heartbeat(path, seq_of=lambda: 7, stage_of=lambda: 3, pid=1234, clock=lambda: NOW)
    hb.start()
    try:
        payload = _read(path)  # 첫 주기를 기다리지 않고 이미 있어야 한다
    finally:
        hb.stop()
    assert payload["pid"] == 1234
    assert payload["last_event_seq"] == 7
    assert payload["current_stage"] == 3
    assert payload["interval_s"] > 0
    assert payload["ts"].startswith("2026-09-15")


def test_it_ticks_while_the_trace_stays_silent(tmp_path: Path) -> None:
    """모델 호출이 멈춘 상황: seq(trace 이벤트 수)는 굳어 있고 단계만 바뀐다.
    heartbeat 스레드가 메인의 emit과 무관하게 돌아 파일을 갱신하는지 본다."""
    path = tmp_path / "heartbeat.json"
    stage = {"v": 1}
    # seq_of는 상수 5로 고정 — trace가 한 이벤트도 더 안 낸 '침묵' 상태를 흉내낸다.
    hb = Heartbeat(path, seq_of=lambda: 5, stage_of=lambda: stage["v"], interval_s=0.01)
    hb.start()
    try:
        stage["v"] = 6  # 메인은 emit 없이 단계만 진행
        deadline = time.time() + 3.0
        while time.time() < deadline and _read(path)["current_stage"] != 6:
            time.sleep(0.01)
        payload = _read(path)
    finally:
        hb.stop()
    assert payload["current_stage"] == 6, "heartbeat가 trace 침묵 중에도 틱하지 않았다"
    assert payload["last_event_seq"] == 5  # trace는 여전히 침묵


def test_write_is_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    hb = Heartbeat(path, seq_of=lambda: 0, stage_of=lambda: 0, interval_s=0.01)
    hb.start()
    time.sleep(0.05)
    hb.stop()
    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []  # 원자 교체라 잔여 tmp가 없다


def test_start_survives_a_transient_first_write_error(tmp_path: Path) -> None:
    """첫 쓰기가 일시적 OSError에 걸려도 `start()`는 raise하지 않고, 다음 틱이 파일을 만든다.
    이게 안 되면 heartbeat 하나가 run 전체 산출물을 없앤다 (#210 리뷰 H1)."""
    path = tmp_path / "heartbeat.json"
    hb = Heartbeat(path, seq_of=lambda: 1, stage_of=lambda: 2, interval_s=0.01)
    original = hb._write
    calls = {"n": 0}

    def flaky() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "일시 잠금(테스트)")
        original()

    hb._write = flaky  # type: ignore[method-assign]
    hb.start()  # 첫 _write가 터져도 여기서 raise되면 안 된다
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline and not path.exists():
            time.sleep(0.01)
        assert path.exists()  # 후속 틱이 회복해 파일을 만든다
    finally:
        hb.stop()


def test_stop_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    hb = Heartbeat(path, seq_of=lambda: 0, stage_of=lambda: 0, clock=lambda: NOW)
    hb.start()
    hb.stop()
    hb.stop()  # 두 번째 stop이 터지지 않는다
    assert path.exists()


def test_context_manager_stops_on_exit(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat.json"
    with Heartbeat(path, seq_of=lambda: 1, stage_of=lambda: 2, clock=lambda: NOW) as hb:
        assert path.exists()
        assert hb._thread is not None and hb._thread.is_alive()
    assert hb._thread is None  # __exit__이 스레드를 세웠다
