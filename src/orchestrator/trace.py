"""실행 trace (engineering-guide.md §6, evaluation.md §5).

trace는 로그가 아니라 **지표의 원본 데이터**다. 여기 남지 않은 사건은 측정할 수 없고,
이 프로젝트가 주장하려는 값(제거율·캐시 적중률·비용)은 전부 아래 이벤트에서 유도된다.
그래서 이벤트 종류를 열거형으로 닫고, 각 종류가 어느 산출식에 쓰이는지 여기 적어 둔다.
필드를 지우기 전에 이 표에서 무엇이 무너지는지 먼저 본다.

## 이벤트 → 지표 대응표 (계획 §6)

| 지표 | 소스 이벤트 | 산출식 |
| --- | --- | --- |
| 제거율 — Scout | `transition`(stage=4) | `1 − count(scout_promoted) / count(생성)` |
| 제거율 — Gate | `gate_eval`(stage=6) | `count(grade=E0) / count(*)` |
| 제거율 — Critic | `transition`(stage=7) | `count(rebutted ∪ need_evidence) / count(*)` |
| 제거율 — Tool | `transition`(stage=8) | `count(repro_failed) / count(repro_*)` |
| 토큰 in/out/cached | `model_call`의 3필드 | 역할·단계별 합계 |
| cache — prefix | `model_call` | `Σ cached_tokens / Σ input_tokens` |
| cache — capsule | `cache`(cache="capsule") | `count(hit=true) / count(*)` |
| 스키마 파싱 실패율 | `model_call.ok` | `count(ok=false) / count(*)` — 모델·역할별 |
| 근거 없는 반박 비율 | `critic_normalized` | `count(*) / count(critic의 model_call)` |
| wall-clock latency | `stage_end.wall_ms` | 단계별 / run 합계 |
| 예산 소진 지점 | `budget` | `RunRecord.stopped_after`의 근거 |
| 정책 A/B 비교 | `policy_decision` | 정책별 route 분포 (v1은 기준선) |

Scout 제거율의 분모는 4단계에서 **생성된** Candidate 수이므로 `create()`도 `transition`을
남긴다. "근거 없는 반박 비율"의 분모는 critic 역할의 `model_call`이고 "스키마 파싱
실패율"과 **다른 분모**다 — 두 실패를 한 분모로 묶으면 모델 비교에서 뜻이 섞인다.

`transition`은 `from == to`인 행도 반드시 남긴다 — 없으면 6·7단계 통과율의 **분모**가
사라진다(계획 §1). `gate_ambiguous`는 E1↔E2 경계 기록 전용이며 모델 호출을 뜻하지 않는다.

## 재현성 기준

두 실행의 trace JSONL에서 `ts`·`wall_ms`만 제거하면 **바이트 동일**해야 한다
(engineering-guide.md §5). 그래서 `seq`는 시각이 아니라 발생 순번이고, ID는
`src.ids.IdFactory`가 결정론적으로 발급하며, 이벤트 필드 순서는 모델 정의 순서로 고정된다.
새 필드에 시각·난수·경로 절댓값을 넣으면 이 기준이 깨진다.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import TextIO

from pydantic import Field

from src.orchestrator.stages import Stage
from src.schemas.common import HarnessModel, Identifier, to_utc_iso

# 재현성 비교 시 제거하는 필드. 두 실행에서 값이 달라지는 것은 이 둘뿐이어야 한다.
VOLATILE_FIELDS = ("ts", "wall_ms")

# trace `data`에 넣을 수 있는 값. 임의 객체를 허용하면 JSONL 직렬화가 실행 중에 깨진다.
TraceValue = str | int | float | bool | None


class EventKind(StrEnum):
    """trace 이벤트 종류. 위 대응표의 "소스 이벤트" 열과 같은 집합이다."""

    STAGE_START = "stage_start"
    STAGE_END = "stage_end"
    TRANSITION = "transition"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    GATE_EVAL = "gate_eval"
    GATE_AMBIGUOUS = "gate_ambiguous"
    CACHE = "cache"
    CRITIC_NORMALIZED = "critic_normalized"
    BUDGET = "budget"
    POLICY_DECISION = "policy_decision"


class TraceEvent(HarnessModel):
    """한 사건. 필드 순서가 곧 JSONL의 키 순서이므로 함부로 바꾸지 않는다."""

    seq: int = Field(ge=1)
    ts: str
    kind: EventKind
    run_id: Identifier
    stage: int | None = Field(default=None, ge=0)
    subject_kind: str | None = None
    subject_id: str | None = None
    # `stage_end`·`model_call`·`tool_call`의 소요 시간. 재현성 비교에서 제거된다.
    wall_ms: int | None = Field(default=None, ge=0)
    data: dict[str, TraceValue] = Field(default_factory=dict)


def canonical_line(event: TraceEvent) -> str:
    """재현성 비교용 표현. `VOLATILE_FIELDS`를 뺀 JSON 한 줄."""
    payload = event.model_dump(mode="json", exclude=set(VOLATILE_FIELDS))
    return json.dumps(payload, ensure_ascii=False, sort_keys=False)


class TraceWriter:
    """이벤트를 순번대로 쌓고, 스트림이 있으면 JSONL로 흘려보낸다.

    스트림을 선택으로 둔 것은 테스트가 파일 없이 이벤트를 검사하기 위해서다. 파일
    핸들의 수명은 호출자(러너)가 관리한다 — 여기서 열면 누가 닫는지가 흐려진다.

    **v1은 순차 실행 전제다.** `seq` 증가와 리스트 추가에 락이 없다. 큐를 스레드로
    돌리게 되면 여기와 StateStore·FakeModelClient의 전제를 함께 풀어야 한다
    (계획 §5.1 "동시성 없음이 v1 전제다").
    """

    def __init__(
        self,
        run_id: str,
        *,
        stream: TextIO | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.run_id = run_id
        self._stream = stream
        self._clock = clock or (lambda: datetime.now(UTC))
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> list[TraceEvent]:
        """지금까지의 이벤트. 집계는 호출자가 한다(`metrics.py`는 다음 PR)."""
        return list(self._events)

    def emit(
        self,
        kind: EventKind,
        *,
        stage: int | None = None,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        wall_ms: int | None = None,
        **data: TraceValue,
    ) -> TraceEvent:
        event = TraceEvent(
            seq=len(self._events) + 1,
            ts=to_utc_iso(self._clock()),
            kind=kind,
            run_id=self.run_id,
            stage=int(stage) if stage is not None else None,
            subject_kind=subject_kind,
            subject_id=subject_id,
            wall_ms=wall_ms,
            data=data,
        )
        self._events.append(event)
        if self._stream is not None:
            self._stream.write(event.model_dump_json() + "\n")
        return event

    def stage_span(self, stage: Stage, *, monotonic_ms: Callable[[], int]) -> "StageSpan":
        """`with`로 감싸 `stage_start`/`stage_end`를 짝지어 남긴다.

        시계를 인자로 받는 것은 `wall_ms`가 재현성 비교에서 제거되는 값이라도 테스트가
        고정할 수 있어야 하기 때문이다.
        """
        return StageSpan(self, stage, monotonic_ms)

    def canonical_lines(self) -> list[str]:
        """재현성 비교용 줄 목록 (`ts`·`wall_ms` 제거)."""
        return [canonical_line(event) for event in self._events]


class StageSpan:
    """`stage_start` → 본문 → `stage_end` 한 쌍."""

    def __init__(self, trace: TraceWriter, stage: Stage, monotonic_ms: Callable[[], int]) -> None:
        self._trace = trace
        self._stage = stage
        self._monotonic_ms = monotonic_ms
        self._started_ms = 0

    def __enter__(self) -> "StageSpan":
        self._started_ms = self._monotonic_ms()
        self._trace.emit(EventKind.STAGE_START, stage=self._stage, name=self._stage.name.lower())
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: object, tb: object) -> bool:
        """예외를 삼키지 않는다 — `stage_end`에 실패 사실만 남기고 그대로 전파한다."""
        self._trace.emit(
            EventKind.STAGE_END,
            stage=self._stage,
            wall_ms=max(0, self._monotonic_ms() - self._started_ms),
            ok=exc_type is None,
            error=None if exc_type is None else exc_type.__name__,
        )
        return False
