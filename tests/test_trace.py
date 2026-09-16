"""TraceWriter 스트리밍 계약 — 이벤트가 emit 즉시 디스크에 남는가.

여기서 고정하는 것은 **내구성**이다: `emit`이 flush하므로 스트림을 닫기 전에도
이벤트가 파일에 있다. 이 검사가 없으면 스트림이 블록 버퍼링(≈8KB)이라 run 도중
이벤트가 파일에 덩어리로 지연되고, **스트림을 닫지 못한 채 프로세스가 죽으면** 버퍼의
직전 이벤트가 증발한다(flush가 닫는 모델은 거기까지다 — 전원 차단·하드리셋은 fsync 급이다).
"스트림을 닫기 전 파일 내용을 본다"가 정확히 그 프로세스-죽음 모델을 흉내낸다.
파일을 `open("w")` 기본 버퍼링으로 여는 것은 `src/cli.py`의 `trace.jsonl` open과
같은 조건이라, 그 경로의 회귀를 이 단위 테스트가 대신 잡는다 (이슈 #208).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from src.orchestrator.trace import EventKind, TraceWriter

RUN_ID = "run_20260915_0001"
STARTED_AT = datetime(2026, 9, 15, tzinfo=UTC)


def test_emit_puts_the_event_on_disk_before_the_stream_is_closed(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    # cli.py:668과 같은 기본(블록) 버퍼링. flush가 없으면 이 한 줄은 버퍼에 갇힌다.
    with path.open("w", encoding="utf-8") as stream:
        writer = TraceWriter(RUN_ID, stream=stream, clock=lambda: STARTED_AT)
        writer.emit(EventKind.STAGE_START, stage=1, name="scout")
        on_disk = path.read_text(encoding="utf-8")  # 아직 닫지 않았다

    assert on_disk, "emit 뒤 flush가 없어 이벤트가 버퍼에 갇혔다 — 크래시 시 증발한다 (D76)"
    first = json.loads(on_disk.splitlines()[0])
    assert first["kind"] == EventKind.STAGE_START.value
    assert first["run_id"] == RUN_ID


def test_every_emitted_event_survives_a_close_that_never_happens(tmp_path: Path) -> None:
    """마지막 이벤트까지 남는가 — 크래시는 스트림을 닫아 주지 않는다."""
    path = tmp_path / "trace.jsonl"
    with path.open("w", encoding="utf-8") as stream:
        writer = TraceWriter(RUN_ID, stream=stream, clock=lambda: STARTED_AT)
        writer.emit(EventKind.STAGE_START, stage=1, name="scout")
        writer.emit(EventKind.STAGE_END, stage=1, ok=True)
        lines = path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert [json.loads(line)["kind"] for line in lines] == [
        EventKind.STAGE_START.value,
        EventKind.STAGE_END.value,
    ]


def test_none_stream_still_records_in_memory(tmp_path: Path) -> None:
    """스트림이 없어도(테스트·집계 경로) 메모리 이벤트는 그대로다 — flush가 그 경로를 안 건든다."""
    writer = TraceWriter(RUN_ID, stream=None, clock=lambda: STARTED_AT)
    writer.emit(EventKind.STAGE_START, stage=1, name="scout")
    assert [event.kind for event in writer.events] == [EventKind.STAGE_START]
