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
| Critic 선별 절단 | `critic_skipped` | `count(*)`. **이벤트 부재 = 안 잘림** |
| 토큰 in/out/cached | `model_call`의 3필드 | 역할·단계별 합계 |
| GPU 시간 | `model_call`·`retrieval_call`의 `gpu_ms` | 합계. 실측/근사는 `gpu_ms_source` |
| 도구 시간 | `tool_call.wall_ms` | **GPU가 아니다** — `Budget.tool_seconds` 소관 |
| 검색 비용 | `retrieval_call` | 부품·단계별 합계. `model_call`과 분모가 다르다 |
| 검색 범위 절단 | `retrieval_truncated` | `collected`−`kept`. **이벤트 부재 = 안 잘림** |
| cache — prefix | `model_call` | `Σ cached_tokens / Σ input_tokens` |
| cache — capsule | `cache`(cache="capsule") | `count(hit=true) / count(*)` |
| 호출 그래프 커버리지 | `graph_index` | `unresolved_*`·`skipped`. run당 **0건 또는 1건** |
| 스키마 파싱 실패율 | `model_call.ok` | `count(ok=false) / count(*)` — 모델·역할별 |
| 근거 없는 반박 비율 | `critic_normalized` | `count(*) / count(critic의 model_call)` |
| wall-clock latency | `stage_end.wall_ms` | 단계별 / run 합계 |
| 예산 소진 지점 | `budget` | `RunRecord.stopped_after`의 근거 |
| 정책 A/B 비교 | `policy_decision` | 정책별 route 분포 (v1은 기준선) |

검색 호출을 `model_call`에 `role=embedding`으로 끼워 넣지 않는 이유도 같다 — "스키마
파싱 실패율"의 분모가 `model_call` 전체인데 스키마가 없는 호출이 섞이면 그 값이 조용히
희석된다. embedding·reranker는 `Role`도 아니다(ADR 0004).

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
    RETRIEVAL_CALL = "retrieval_call"
    # 광역 수집이 `HybridRetriever.MAX_CHUNKS`(400)에 걸려 잘렸다.
    #
    # **이 이벤트가 없으면 "안 잘렸다"이다.** 잘린 run에서만 남으므로 0건이 정상이고,
    # 그래서 `retrieval_call`처럼 분모로 쓸 수 없다. 이벤트가 생기기 전에는 절단이
    # `on_truncate` 콜백에만 있었고 오케스트레이터가 그 훅을 안 받아, **조용히 잘린 것이
    # "전부 봤다"로 읽혔다**(D13, W7).
    #
    # `retrieval_call`과 합치지 않는다 — 그쪽은 호출 1건당 1행이고 이쪽은 수집 1회당
    # 1행이라 분모가 다르다. `candidates`라는 이름도 겹치지 않게 `collected`를 쓴다.
    RETRIEVAL_TRUNCATED = "retrieval_truncated"
    TOOL_CALL = "tool_call"
    GATE_EVAL = "gate_eval"
    GATE_AMBIGUOUS = "gate_ambiguous"
    CACHE = "cache"
    # 1단계 인덱서 산출물의 로드 결과와 커버리지. `unresolved_*`가 크면 그 저장소에서 E2를
    # 얼마나 믿을지가 달라지는데, 이 이벤트가 생기기 전에는 아무도 그 숫자를 집계할 수
    # 없었다(D13).
    #
    # **run당 최대 1건이고, 0건일 수 있다.** 로더가 지연 로드라 Retriever가 그래프를
    # 처음 조회할 때 실린다 — 그래프 섹션은 EXPANDED·FOCUSED에만 있으므로 **후보가 하나도
    # 4단계를 통과하지 못한 run은 이 이벤트가 아예 없다**(실측: 후보 1건/Finding 0건 run에서
    # 0건). 그래서 "이벤트가 없다"를 "커버리지가 완전하다"로 읽으면 안 되고, 분모로 쓸 때는
    # `stage_end`(stage=5) 유무로 먼저 갈라야 한다.
    #
    # 조회마다 싣지 않는 것은 같은 값이 후보 수만큼 중복돼 재현성 비교가 지저분해지기
    # 때문이다.
    GRAPH_INDEX = "graph_index"
    # 4단계에 올릴 target 목록을 확정한 결과 (W2). **run당 1건이고, 모델을 한 번
    # 부르기 전에 낸다** — 그 뒤 어디서 죽어도 분모는 남는다.
    #
    # 후보 단위가 파일에서 심볼로 바뀌면서 제거율의 분모가 `analyzable_artifacts`에서
    # `symbol_targets`로 옮겨 갔다. 두 값을 함께 싣는 이유는 그 이동 자체를 데이터에서
    # 볼 수 있어야 하기 때문이다(STATUS.md §10 지표 단절).
    #
    # `fallback_artifacts`(인덱스가 없어 파일 단위로 되돌아간 수)와
    # `no_symbol_artifacts`(인덱스는 있는데 심볼 0건)를 가른다 — `.h`처럼 인덱서에
    # 넘어가지 않는 파일과 인덱서가 아예 안 돈 run은 다른 사건이다.
    TARGET_PLAN = "target_plan"
    # 4단계에 올리지 못한 target 하나 (W2). `reason`이 사유다.
    #
    # **`target_plan`과 합치지 않는다** — 그쪽은 run당 1건인 분모이고 이쪽은 target당
    # 1건이라 분모가 다르다(`retrieval_truncated`를 `retrieval_call`과 합치지 않은 것과
    # 같은 이유). 합치면 절단 목록이 한 줄에 뭉쳐 세는 것과 읽는 것을 함께 잃는다.
    #
    # **`Candidate`를 만들어 `queued`로 두지 않는 이유**: `Candidate.score`가 필수 필드라
    # 모델을 안 부르고 만들려면 0.0을 지어내야 하고, 그 값이 Scout 점수 분포(D2의 입력)에
    # 섞인다. 지어낸 값을 기록에 넣지 않는다.
    TARGET_SKIPPED = "target_skipped"
    # 7단계에 올리지 못한 Finding 하나 (D26 (b)). `critic.select()`가 `critic_top_n`으로
    # 자른 뒤 남긴다. **잘린 건당 1행이고, 안 잘린 run은 0건이다.**
    #
    # **`transition`에 넣지 않는다.** 이유가 두 겹이고 뒤쪽이 더 무겁다. (가) `transition`의
    # 모든 행은 `TransitionRule`(F-x)이 뒤에 있는데 절단은 어느 규칙에도 대응하지 않아 F
    # 번호를 지어내야 한다. (나) 위 대응표의 "제거율 — Critic"이 `transition`(stage=7)을
    # 분모로 쓰므로, 모델을 한 번도 안 부른 건이 거기 섞이면 **그 비율이 조용히 부푼다** —
    # 기록만 늘리려던 변경이 지표를 바꾼다.
    #
    # **`target_skipped`와 합치지 않는다.** 그쪽의 `data`는 `file`·`symbol`·`line_start`인데
    # 그건 Candidate가 안 만들어져 **ID가 없기 때문**이다. Finding에는 `finding_id`가 있으므로
    # 여기서는 `subject_id`로 지목한다(`critic_normalized`·`gate_eval`과 같은 처리). 합치면
    # `count(target_skipped)`의 뜻이 "4단계에 못 간 target 수"에서 조용히 달라진다.
    #
    # **`stage`는 7이다.** 절단이 실행되는 자리는 6단계 뒤이지만 6으로 적으면 "게이트에서
    # 잘렸다"로 읽히는데 게이트는 이 건을 **승격시켰다**. 7로 적으면
    # `count(critic_skipped, stage=7) + count(gate_eval, stage=7) = count(promoted_to_critic)`
    # 으로 7단계가 분할로 닫힌다. "들어갔다가 죽었다"로 오독될 위험은 `kind`가 막는다 —
    # 이 행은 정의상 **안 들어간** 건이다.
    #
    # `confidence`(선별 키)와 `has_repro_spec`(8단계 진입 조건)을 함께 싣는다. D26 (a)가
    # 묻는 것이 정확히 그 둘의 어긋남이고, 이 두 필드가 없던 동안에는 그 답을 보려고
    # `state.db`를 손으로 열어야 했다.
    CRITIC_SKIPPED = "critic_skipped"
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
