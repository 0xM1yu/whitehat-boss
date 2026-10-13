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
| 제거율 — Tool | `transition`(stage=8) | 분모는 8단계 사유 셋 — 산식 아래 (#68) |
| Critic 선별 절단 | `critic_skipped` | `count(*)`. **이벤트 부재 = 안 잘림** |
| 토큰 in/out/cached | `model_call`의 3필드 | 역할·단계별 합계 |
| GPU 시간 | `model_call`·`retrieval_call`의 `gpu_ms` | 합계. 실측/근사는 `gpu_ms_source` |
| 도구 시간 | `tool_call.wall_ms` | **GPU가 아니다** — `Budget.tool_seconds` 소관 |
| 검색 비용 | `retrieval_call` | 부품·단계별 합계. `model_call`과 분모가 다르다 |
| 검색 범위 절단 | `retrieval_truncated` | `collected`−`kept`. **이벤트 부재 = 안 잘림** |
| Auditor 원문 범위 | `auditor_source` | 뺀 함수·못 본 줄·entry 크기. **슬라이스 run만** |
| Auditor 도구 홉 | `auditor_hop` | 성공한 모델 호출당 1건 — action·tool·stop_reason·usage
(⏵ 2026-10-13 크기 거절당한 홉도 1건: `stop_reason=context_overflow` · action 없음, #514). **루프
꺼지면 0건이지만 0건이 곧 꺼짐은 아니다**(첫 호출이 파싱 실패·예산 소진으로 죽어도 0건 -
루프의 hop 0 크기 거절만 `context_overflow` 1건을 낸다) — 꺼짐은 `model_call.schema`가
`Finding`인지로 가른다 |
| Scout 슬라이스 절단 | `scout_slice_trimmed` | 뺀 줄·전후 토큰. **이벤트 부재 = 안 잘림** |
| cache — prefix | `model_call` | `Σ cached_tokens / Σ input_tokens` |
| cache — capsule | `cache`(cache="capsule") | `count(hit=true) / count(*)` |
| 호출 그래프 커버리지 | `graph_index` | `unresolved_*`·`skipped`. run당 **0건 또는 1건** |
| 토큰 계상 방식 | `token_counter` | 역할별 `real`/`approx`와 그 사유. run당 **정확히 1건** |
| 스키마 파싱 실패율 | `model_call`(`attempts`·`ok`·`error`) | 시도 단위 — 산식 아래 (#83) |
| 시도별 출력 토큰·종료 사유 | `model_call`의 `attempt_*` 둘 | 시도 순서 — 아래 (#550) |
| 실패 응답 원문 | `model_call`의 `response_refs`·`response_sha256` | 스키마 소진·게이트웨이
거절로 끝난 호출의 스키마 실패 시도(JSON 배열 문자열, 시도 순서).
**칸이 `null`이면 그 시도는 받은 문자열이 없다(`content: null`) — 빈 응답이 아니고 파일도
없다.** **재시도로 살아난 성공 호출도 버려진 시도의 칸을 싣고(#618),
`run --keep-responses`에 든 역할은 성공 응답을 마지막 칸으로 싣는다(#612) — 칸 K = K번째
시도.**
(회복된 실패를 세려면 키 유무가 아니라 `attempts>1`을 쓴다 - 스위치가 깨끗한 성공에도 키를
붙인다.) **키 부재 = 깨끗한 성공(스위치 밖) · PromptLog 없음 · 원문을 못 실은 호출 —
키 부재만으로 응답 없음을 단정하지 않는다: 200이었으나 모양이 계약과 달라
`ModelGatewayError`가 된 시도의 원문은 안 남는다
(`_post`의 「응답 모양이 계약과 다르다」 분기).** 쓰기 실패는 `response_record_error` (#603) |
| 근거 없는 반박 비율 | `critic_normalized` | `count(*) / count(critic의 model_call)` |
| wall-clock latency | `stage_end.wall_ms` | 단계별 / run 합계 |
| 예산 소진 지점 | `budget` | `RunRecord.stopped_after`의 근거 |
| 채점기 최종 판정 | `oracle_final` | run당 0건 또는 1건. 값은 `oracle/verdict.json`(trace 밖) |
| 정책 A/B 비교 | `policy_decision` | 정책별 route 분포 (v1은 기준선) |
| 발급값 이탈 | `issued_override` | `fields`별 `count(*)`. **이벤트 부재 = 발급값 그대로**(아래) |
| 중복 제거 대응 | `finding_deduped` | `= Report.duplicates`. **부재 = 안 합쳤다**(9단계 도달 run) |

⚠ **「제거율 — Gate」는 2026-10-08 전후가 같은 지표가 아니다 (이슈 #552).** 그전에는 6단계가
모델의 인용 목록을 그대로 썼으므로 그 비율에 **인용 순응률**이 섞여 있었다. 지금은 6단계가
타깃에서 관측한 Evidence를 직접 붙이므로(`gate_node.attach_observed`) E0는 「관측이 0건인
타깃」을 센다. **그 날 앞뒤 run의 이 값을 한 열에 놓지 않는다.** 새 임계는 미실측이다.

「발급값 이탈」의 부재가 말하는 것은 `RunContext.stamp_issued`가 덮는 키(ID·`run_id`·
`artifact_id`·초기 `state`·`missing_evidence`)뿐이다. 하네스가 덮는 `produced_by`·
`scout_member`·`slice_id`는 이 이벤트 없이 덮이므로 그 셋의 부재는 「안 쟀다」다.

「스키마 파싱 실패율」 산식: 분자 `Σ(attempts−1 if ok else attempts) −
count(error="ModelGatewayError")`, 분모 `Σ attempts`. 성공 호출의 실패는 `attempts−1`,
소진(`SchemaParseError`)은 `attempts`, 게이트웨이 거절은 파싱 대상이 없어 뺀다(#83).

「시도별 출력 토큰·종료 사유」(#550): `model_call.output_tokens`는 **재시도를 합친 값**이다. 합이
**이미** 주는 것이 있다 — 상한이 32,768일 때 두 시도의 합 65,536은 산술로 둘 다 정확히 32,768이고,
합 27,888은 **`null` 칸이 없을 때** 두 시도가 다 상한 아래임을 보인다(`null` 칸은 합에 0을 더한
시도라 합이 그 몫만큼 과소일 수 있다 — 아래 규칙 3). 합이 65,536일 때 합이 못 가르는 것은 그
32,768이 `length`인지 상한과 같은 값에서의 정상 종료인지뿐이다. 합이 **못** 주는 것은
① 상한과 그 2배 사이의 합에서 어느 시도가 닿았나(CLAUDE.md §2.2의 `a₁ + a₂ = 8,402` 사례)
② `abort` 같은 `length`·`stop` 밖의 사유 ③ 추론이 아니라 서버의 직접 확인이다. 그래서 같은
이벤트에 두 키를 더 싣는다(성공·실패 행 모두, 키 집합이 같다).

- `attempt_output_tokens` — 시도별 출력 토큰. 예: `"[8192,12]"`
- `attempt_finish_reasons` — 시도별 서버 종료 사유(`stop`·`length`·`abort` … 서버가 준 그대로).
  공백뿐인 값은 `null`이다. 원문이 512자를 넘는 값만 `error_detail`과 같은 자르기로 묶이는데, 그
  자르기는 공백 연속을 한 칸으로 접고 다시 재므로 **접은 결과가 512자 이하면 잘림 표시 없이 공백만
  달라진다**(표시가 붙는 것은 접은 뒤에도 넘칠 때뿐이다). 예: `"[\\"length\\",\\"stop\\"]"`

값은 JSON 배열 **문자열**이다(`json.loads`로 읽는다) — `TraceValue`가 스칼라라서이고, 쉼표 연결은
미측정(`null`)을 빈 칸이나 지어낸 문자열로 써야 해서 안 쓴다. 읽는 규칙 셋:

1. **배열 길이 = `attempts`이고 순서가 시도 순서다.** 원소 `null`은 「안 쟀다」다 — 응답을 못 받은
   시도(게이트웨이 거절·타임아웃·모양이 깨진 200)이거나, 서버가 그 값을 안 실었다
   (`attempt_finish_reasons`는 사유를, `attempt_output_tokens`는 `usage.completion_tokens`를).
   `0`·`""`·`"unknown"`이 아니다 — 토큰 칸의 `0`은 서버가 0이라고 보고한 것이다.
2. **두 키가 모두 `null`(배열 아님)이면 이 호출에 시도별 내역이 없다 — 이유는 둘이고 trace에서 안
   갈린다.** (가) 어댑터가 안 실었다 — Fake, 소비를 안 든 예외(프로필에 없는 역할을 부른
   `UnknownRoleError` 등). (나) 실었는데 **잃었다** — 시도가 측정된 뒤 `complete()`를 어댑터가
   안 잡는 예외가 뚫고 나가면 예외에 `attempt_log`가 안 실려 (가)와 같은 모양이 된다. 어댑터가
   잡는 것은 응답 모양이 깨진 200(`KeyError`·`IndexError`·`TypeError`·`AttributeError`·
   `ValueError`·`OverflowError` → `ModelGatewayError`)과 스키마 검증 실패(`ValidationError`)이고,
   **안 잡혀 뚫는 것의 측정된 예**는 ① 서버가 보낸 100,000단 중첩 JSON이 `json.loads`에서 일으키는
   `RecursionError`(응답 하나로 닿는다) ② 스키마 검증기가 던진 `TypeError`·`KeyError` — pydantic
   2.13.4는 검증기의 `ValueError`·`AssertionError`만 `ValidationError`로 감싼다(`SchemaParseError`가
   `attempt_log`를 든다). 그 밖의 예외 형은 재지 않았다. 어느 쪽이든 「시도가 없었다」가 아니고,
   null/null만으로 「측정된 적 없다」를 단정하지 않는다.
3. **`output_tokens == Σ attempt_output_tokens`(`null` 제외)다.** 합은 그대로이고 이것은 쪼갠
   내역이다. `null` 칸은 합에 0을 더한 시도라(`Usage`가 `None`을 못 들어 어댑터가 0을 채운다)
   이 등식은 `null`이 섞여도 선다. **합산 `output_tokens`가 못 가르는 것**: 그 0이 「서버가
   안 줬다」인지 「0이라고 보고했다」인지 — 가르는 것은 칸의 `null`과 `0`이다. 반대로 `null`
   칸이 있는 호출의 합은 **그만큼 과소**일 수 있다.

⚠ **1·3은 어댑터가 호출을 지을 때의 관례이지 강제되는 불변식이 아니다.** `ModelResult`는
`len(attempt_log) == attempts`도 합 일치도 검증하지 않는다(던지는 검증은 계측 결함을 run 중단으로
바꾸므로 일부러 안 둔다). 지켜지는 곳은 `OpenAICompatModelClient.complete()`와 대본을 준
`FakeModelClient`이고 `tests/test_model_call_instrumentation.py`가 그 둘을 문다. 어긋난 이벤트를
읽는 쪽은 같은 이벤트의 `attempts`·`output_tokens`와 규칙 1·3으로 **직접 대조한다** — 어긋나면
그 호출의 쪼갠 값을 쓰지 않는다.

⚠ `finish_reason="length"`는 서버가 **그 시도가 길이 상한에 닿아 끝났다고 보고한 것**이지 파싱
실패의 원인을 확정하는 값이 아니다. 이 키가 하는 일은 절단과 그 밖을 **가를 수 있게** 하는 것까지다.

⚠ **「제거율 — Critic」의 분자는 두 가지를 담는다.** `need_evidence`에는 Critic이 실제로
보류한 것과 **우리 매퍼가 verdict를 못 읽어** 보류로 보낸 것이 함께 들어간다
(`DecisionDraft.to_decision`). 후자는 모델의 판정이 아니라 `Verdict.from_text`의 실패이므로
제거율로 읽으면 안 된다 — **가르는 값은 `critic_verdict_unmapped` 건수**이고, 그 이벤트를
빼고 세면 Critic이 낸 보류만 남는다. 이벤트 부재 = 전부 읽혔다.

「제거율 — Tool」 산식: 분자 `count(repro_failed)`, 분모
`count(repro_failed ∪ repro_confirmed ∪ evidence_below_proven)`.

⚠ **그 분모를 접두사로 세지 않는다.** 예전 산식은 `count(repro_*)`였는데 그 접두사는
8단계 사유 `evidence_below_proven`(F-10b)을 **안 잡고** 7단계 사유
`repro_spec_missing`(F-9)은 **잡는다** — 양쪽으로 틀리는 것을 stage 필터 하나가 겨우
막고 있었다. 8단계 사유가 늘면 이 산식을 같이 고친다(#68).

검색 호출을 `model_call`에 `role=embedding`으로 끼워 넣지 않는 이유도 같다 — "스키마
파싱 실패율"의 분모가 `model_call`들의 시도 합(`Σ attempts`)인데 스키마가 없는 호출이
섞이면 그 값이 조용히 희석된다. embedding·reranker는 `Role`도 아니다(ADR 0004).

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

**바이트 동일은 Fake 경로 run의 계약이다 (#64, 2026-10-10 결정 · `tests/test_pipeline_e2e.py`가
문다).** 실도구·실서빙 run은 바이트 재현 대상이 아니다 — `data`에 시간 파생값이 들고
(`budget`의 `gpu_ms`·`tool_ms`, `model_call`·`retrieval_call`의 `gpu_ms`, `scout_hop`·`pov_hop`의
`latency_s`, `model_swap`의 `report.*`),
시간 상한에 닿는 시점이 `budget` 이벤트 열 자체를 바꾼다. 그래서 비교에서 빼는 것을 `data` 키로
넓히지 않는다. 위 「새 필드」 규칙은 그래도 모든 run에 선다 — 새 휘발 값은 `data`가 아니라 최상위
`wall_ms`나 사이드카로 낸다(`oracle_log`의 선례).
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import TextIO

from pydantic import Field

from src.orchestrator.stages import Stage
from src.schemas.common import HarnessModel, Identifier, to_utc_iso

# 재현성 비교 시 제거하는 필드. Fake 경로의 두 실행에서 값이 달라지는 것은 이 둘뿐이어야 한다 —
# 실도구·실서빙 run의 `data` 시간 값은 비교 범위 밖이다(위 「재현성 기준」).
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
    # "전부 봤다"로 읽혔다**(#63, W7).
    #
    # `retrieval_call`과 합치지 않는다 — 그쪽은 호출 1건당 1행이고 이쪽은 수집 1회당
    # 1행이라 분모가 다르다. `candidates`라는 이름도 겹치지 않게 `collected`를 쓴다.
    RETRIEVAL_TRUNCATED = "retrieval_truncated"
    # 도구 호출 하나. `input_bytes`·`input_sha256`(맨 hex)은 도구 요청에 **데이터로** 실은 입력이
    # 있을 때만 실린다(PoV의 PoC — `ToolArgs.input_bytes`). 요청에 실었다는 뜻이지 도구가 읽었다는
    # 뜻이 아니다. 원바이트는 안 간다. **키 부재 = 입력 없음.**
    TOOL_CALL = "tool_call"
    GATE_EVAL = "gate_eval"
    GATE_AMBIGUOUS = "gate_ambiguous"
    CACHE = "cache"
    # 1단계 인덱서 산출물의 로드 결과와 커버리지. `unresolved_*`가 크면 그 저장소에서 E2를
    # 얼마나 믿을지가 달라지는데, 이 이벤트가 생기기 전에는 아무도 그 숫자를 집계할 수
    # 없었다(#63).
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
    # `no_symbol_artifacts`(인덱스는 있는데 심볼 0건)를 가른다 — `.py`처럼 인덱서에
    # 넘어가지 않는 파일과 인덱서가 아예 안 돈 run은 다른 사건이다.
    TARGET_PLAN = "target_plan"
    # 4단계에 올리지 못한 target 하나 (W2). `reason`이 사유다.
    #
    # **`target_plan`과 합치지 않는다** — 그쪽은 run당 1건인 분모이고 이쪽은 target당
    # 1건이라 분모가 다르다(`retrieval_truncated`를 `retrieval_call`과 합치지 않은 것과
    # 같은 이유). 합치면 절단 목록이 한 줄에 뭉쳐 세는 것과 읽는 것을 함께 잃는다.
    #
    # **`Candidate`를 만들어 `queued`로 두지 않는 이유**: `Candidate.score`가 필수 필드라
    # 모델을 안 부르고 만들려면 0.0을 지어내야 하고, 그 값이 Scout 점수 분포(#91의 입력)에
    # 섞인다. 지어낸 값을 기록에 넣지 않는다.
    TARGET_SKIPPED = "target_skipped"
    # 7단계에 올리지 못한 Finding 하나 (#98 (b)). `critic.select()`가 `critic_top_n`으로
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
    # ⚠ **그 뜻은 Scout 풀에서 이미 달라졌다 — 그리고 한 단위도 아니다**(PR #178).
    # 이 이벤트는 사유에 따라 **다른 단위**로 난다:
    #
    # - `over_candidate_max` — `_plan_targets`가 **팬아웃 전에** 낸다. **대상 단위.**
    # - `gateway_error`·`schema_error` — `_drop`이 `(target, 자리)` **시도마다** 낸다.
    #
    # 그래서 `count(target_skipped)`는 **두 단위의 혼합**이고 target 수도 시도 수도 아니다.
    # 단일 프로필에서는 자리가 하나라 둘이 같아 이 구분이 안 보인다. 세려면 `reason`으로
    # 먼저 가른다. 미해결로 등재돼 있다(인덱스 참조).
    #
    # **`stage`는 7이다.** 절단이 실행되는 자리는 6단계 뒤이지만 6으로 적으면 "게이트에서
    # 잘렸다"로 읽히는데 6단계는 이 건을 Critic 선별 풀에 **두었다**. 7로 적으면
    # `count(critic_skipped, stage=7) + count(gate_eval, stage=7)`
    # `= count(promoted_to_critic) + count(evidence_weak)`로 7단계가 분할로 닫힌다
    # (`stopped_after >= 7`일 때) — 7단계는 HYPOTHESIZED 전부를 선별 대상으로 삼고
    # (`critic_top_n`에 잘린 몫이 `critic_skipped`다) E1 HOLD도 HYPOTHESIZED를 유지하므로
    # 우변은 E2 승격분만이 아니다(#614). "들어갔다가 죽었다"로 오독될 위험은 `kind`가 막는다 —
    # 이 행은 정의상 **안 들어간** 건이다.
    #
    # `confidence`(선별 키)와 `has_repro_spec`(8단계 진입 조건)을 함께 싣는다. #98 (a)가
    # 묻는 것이 정확히 그 둘의 어긋남이고, 이 두 필드가 없던 동안에는 그 답을 보려고
    # `state.db`를 손으로 열어야 했다.
    # 4단계에 **올린** target 하나. `file`·`symbol`·`line_start`를 싣는다 (해소).
    #
    # **`target_plan`에 목록을 접어 넣지 않는다.** 그쪽은 run당 1건인 분모이고
    # `tests/test_pipeline_e2e.py`가 그 계약을 단언한다. 여기에 배열을 실으면 후보가 많은
    # run에서 이벤트 하나가 통째로 커지고, 분모로 쓰던 행이 목록 행으로 성격이 바뀐다.
    #
    # **`target_skipped`와 대칭이다.** 그 이벤트가 잘린 대상의 이름을 남기는 동안 실행된
    # 대상은 개수만 남았고, 그래서 `run_20260827_0004`의 정답 대조에서 "근본 원인
    # `router_define`이 후보 19건에 있었는가"에 run 산출물로 답할 수 없었다 — `deferred: 0`
    # 이라 `target_skipped`가 0건이었고, `Candidate.location`은 **모델이 쓴 값**이라
    # `symbol`이 전부 `null`이었다. **인덱서는 이름을 알고 있다**(`index_graph.py`의
    # `Location(file, symbol, line_start, line_end)`) — 끊긴 자리가 여기다.
    #
    # `count(target_planned) = target_plan.planned`이고, 그래서 이 이벤트는 분모를 새로
    # 만들지 않는다. 같은 수를 두 곳에서 말하되 한쪽은 개수, 한쪽은 이름이다.
    TARGET_PLANNED = "target_planned"
    CRITIC_SKIPPED = "critic_skipped"
    # 7단계에 **들어갔다가** critic 호출이 실패해 그 Finding만 버렸다 (해소).
    #
    # **`critic_skipped`와 합치지 않는다.** 바로 위 주석이 그 이벤트를 "정의상 **안 들어간**
    # 건"으로 못박았다. 파싱 실패는 반대로 **들어가서 모델을 부르고 죽은** 건이라, 한 이름에
    # 넣으면 `critic_skipped`의 뜻이 조용히 넓어지고 #98 (a)가 그 이벤트로 묻던 질문
    # ("선별 키와 8단계 진입 조건이 어긋나는가")이 실패 건에 오염된다.
    #
    # **7단계 분할은 셋으로 늘어난다**:
    #   `count(critic_skipped) + count(critic_failed) + count(gate_eval, stage=7)`
    #   `= count(promoted_to_critic) + count(evidence_weak)`
    # 이 이벤트가 없던 동안에는 좌변이 우변보다 **작았고**, 그 차이가 어디로 갔는지 기록이
    # 지시하지 못했다 — 애초에 run이 통째로 죽어 trace 말고는 아무것도 안 남았기 때문이다.
    #
    # **`reason`은 두 값을 갖는다**: `schema_parse_failed`(모델이 `Decision`을 못 맞췄다)와
    # `gateway_error`(요청 단위 거절 — 400·413·422). 둘을 한 이벤트에 둔 것은 결과가 같기
    # 때문이다(그 Finding만 빠지고 run은 계속 간다). 가르는 것은 `reason`이고, 서버 쪽
    # 사건(상태 없음·5xx)은 애초에 여기 안 온다 — `_is_droppable`이 올려 보낸다.
    # ⚠ **run이 계속 가지 않는 경우가 둘 있다(#296)** — 크기가 아닌 400은 이 이벤트를 남긴 뒤
    # 멈추고(다음 Finding도 같다), 고른 Finding 전부가 이 이벤트로 끝나면 루프 끝에서 멈춘다.
    #
    # `subject_id`는 `finding_id`이고 `reason`·`error_detail`을 함께 싣는다. 사유를 안 실으면
    # "critic이 왜 못 돌았는가"를 보려고 다시 `state.db`를 열어야 한다.
    CRITIC_FAILED = "critic_failed"
    # 슬라이스 타깃의 Auditor `SOURCE`를 무엇으로 채웠나 — 5단계 호출 1건당 1행(결정 1-b = 넓은
    # (i), `runner._auditor_source`). `functions`(넣은 함수 수, entry 함수 포함)·`dropped`(상한 —
    # 글자, `auditor_source_tokens`가 양수면 토큰 — 에 걸려 뺀 함수 수)·`uncovered_lines`(어느
    # 인덱스 함수에도 안 들어 못 본 슬라이스 줄 수)·
    # `chars`(원문 글자 수)·`anchor_chars`(entry 함수 **혼자**의 글자 수)와 `slice_id`를 싣는다.
    # `subject_id`는 후보다. `auditor_callee_hops`가 1이면(#349 (a)) `callees`(붙인 피호출 함수 수 —
    # `functions`에도 들어간다)·`callees_dropped`(피호출 함수 상한·전체 상한에 걸려 안 붙인 수)가
    # 더 실린다. 0이면 두 필드가 없다 — 이벤트 모양이 결정 1-b 그대로다. 같은 조건에서
    # `wrapper_callees`(`callees` 중 래퍼 너머로 붙인 수)·`alias_callees`(매크로 별칭이 부르는
    # 함수로 붙인 수)도 실린다(C4). `auditor_definitions`가 1이면 `definitions`·
    # `definitions_dropped`(붙인·못 붙인 매크로·타입·파일 변수·enum 상수 정의 수 — `functions`에는
    # 안 든다. 단위가 섞인다: 정의 하나가 못 실리면 하나이고, 이미 잘라 실은 enum 블록에 넓히기에서
    # 못 더한 상수 하나도 센다)와 `alias_definitions`
    # (별칭 라운드가 붙이거나 — 이미 실린 큰 enum 블록에 상수를 더해 — 넓힌 정의 수)가 같은 방식으로
    # 더 실린다. 같은 조건에서
    # `definitions_trimmed`(상한을 넘어 물은 상수 주변만 잘라 **SOURCE에 실은** enum 정의 수 —
    # enum마다 하나이고 `definitions`에 든다. 줄였어도 전체 상한에 걸려 못 실은 enum은
    # `definitions_dropped`만 센다. 이미 실린 그 enum 블록에 낱말로 있는 이름 — typedef 이름·
    # 태그·한 줄 다중 상수의 둘째 — 은 보이는 것으로 보고 어느 쪽에도 안 센다. **한계**: 낱말이
    # 있다는 것이 정의가 있다는 뜻은 아니라 끝줄 값이나 주석의 언급만이어도 같게 세므로, 그 경우
    # 정의가 SOURCE에 없는데도 `definitions_dropped`가 과소 집계될 수 있다 — 수정 전과 같은 침묵)도
    # 실린다.
    # `auditor_source_tokens`가 양수면 `tokens`(SOURCE 토큰 수)·`token_counter`(`model` = Auditor
    # 토크나이저 · `estimate` = 글자 근사)가 더 실린다 — 상한이 토큰일 때 무엇으로 셌는지 남긴다.
    # `auditor_entry_trim`이 1이면 `entry_over_cap`(entry 함수가 상한을 넘었나)·
    # `entry_omitted_lines`(줄이며 뺀 줄 수 — 넘었어도 슬라이스 줄이 함수 전체에 퍼지면 0)가 더
    # 실린다. `anchor_chars`는 줄이기 전 크기다. 피호출 단계가 켜졌고 `auditor_indirect_callees`가
    # 1이면 `indirect_callees`(`callees` 중 필드 경유 호출 후보로 붙인 수)·`indirect_skipped`(아직
    # 안 실린 후보가 너무 많아 건너뛴 필드 경유 호출 수 — 필드 하나가 하나)가,
    # `auditor_write_sites`가 양수면 `write_windows`·`write_windows_dropped`(붙인·상한에 못 붙인
    # 쓰기 지점 창 수 — `functions`에는 안 든다)·`write_sites_found`(창을 붙일 후보 쓰기 지점 수
    # — 같은 줄은 한 번,
    # 쓰는 함수의 같은 이름 지역은 뺀 수. 이미 실린 범위 안의 지점도 센다)가 더 실린다(#304).
    # `auditor_caller_lines`가 양수면
    # `callers`·`callers_dropped`(붙인·상한에 못 붙인 호출자 창 수 — `functions`에는 안 든다)·
    # `call_sites`(찾은 호출 지점 수 — 앞 몇 개만 읽는다)가 더 실린다.
    # `auditor_guards`가 1이면 `guards`·`guards_dropped`(훑었을 때) 아니면 `guards_skipped`(안
    # 훑었을 때)가 더 실린다 — **옵션이 꺼졌을 때만 셋 다 없다.** `guards`는 실은 GUARDS 행
    # 수(0이면 맞춰 본 이름을 적은 한 줄을 실었다)고 `guards_dropped`는 행 상한에 걸려 뺀 행 —
    # `파일:줄 | 조건`을 줄바꿈으로 이은 문자열, 50행까지 싣고 넘치면 끝에 `... +N more`, 안 뺐으면
    # 빈 문자열 — 이다(블록에도 `... +N more rows omitted`가 든다). `guards_skipped`는 안 훑은
    # 사유다: `case_a`(sink를 하네스가 모른다) · `window`(품는 함수를 모르는 창 앵커 — 다른
    # 함수의 조건이 든다) · `formfeed`(`splitlines()`만 끊는 글자가 함수 앞·안에 있어 원문 줄이
    # 어긋날 수 있다) · `bare_cr`(원본 바이트에 `\n`을 안 단 `\r`이 함수 앞·안에 있다 — 텍스트
    # 읽기가 `\r`을 `\n`으로 접어 인덱서와 줄 번호가 어긋난다) · `no_sink`(sink 줄이 원문 밖이거나
    # sink에서 이름을 못 뽑았다). GUARDS는 함수가 아니라 `functions`에 안 든다.
    #
    # **`anchor_chars`가 따로 있는 이유** — 상한은 뒤에 붙는 함수만 거르고 entry 함수는 상한보다
    # 커도 늘 들어간다(`auditor_entry_trim`이 0일 때 — 1이면 줄여서 들어간다). 그래서 `chars`가
    # 상한을 넘을 수 있는데, 함수가 둘 이상이면 그것이 entry 혼자 때문인지 합 때문인지
    # `chars`만으로는 안 갈린다(#289 리뷰 LOW-2).
    #
    # **캡슐이 이것을 못 센다.** EXPANDED 캡슐의 `target_function`은 entry 함수만 세고 그것도
    # 예산으로 잘린 값이라, 이 이벤트가 없으면 상한에 잘린 입력이 온전한 입력으로 읽힌다
    # (`retrieval_truncated`가 막는 것과 같은 오독이다).
    #
    # `auditor_source_gate`가 1이면 `gate_reasons`(`source_gate.reasons` — 사유 코드를 쉼표로 이은
    # 문자열: 슬라이서가 낸 분석 사실의 유무(`ANALYSIS_UNKNOWN`)·절단·경계·싱크 cap, 앵커가
    # 창인가, `uncovered_lines`·`dropped`·`callees_dropped`·`callers_dropped`·
    # `definitions_dropped`에서 낸다)가 더 실린다. **빈 문자열은 「나열한 검사가 하나도 안
    # 걸렸다」이고 필드가 없는 것은 「게이트 꺼짐」이다** — 「SOURCE가 완전하다」는 뜻이 아니다
    # (정의·피호출·호출자 창을 안 켠 run은 그 검사가 안 돈다). 분석 사실이 없는 옛 슬라이스는 빈
    # 문자열이 아니라 `ANALYSIS_UNKNOWN`이다. 관측 전용이라 Auditor 입력은 안 바뀐다.
    #
    # **슬라이스 없는 run은 0건이다** — 그 run의 Auditor는 캡슐 대상 하나를 그대로 읽는다.
    AUDITOR_SOURCE = "auditor_source"
    # Scout 슬라이스 렌더가 창에 안 들어 줄을 뺐다(`scout_slice_trim`, #353 (b) —
    # `scout._fit_slice`).
    # 줄을 뺀 Scout 호출 1건당 1행이고 **이벤트 부재 = 안 뺐다**(옵션이 꺼졌거나 들었다).
    # `slice_id`·`role`(자리)·`budget`(몫 = 창 − 출력 상한 − 여유)·`tokens_before`·`tokens_after`
    # (프롬프트 전체 — `model`이면 그 자리 토크나이저, `estimate`면 하한비 3.28)·`lines_before`·
    # `lines_kept`(**코드 줄 수** — 배너 줄은 안 센다. 렌더 줄 수가 아니다: 렌더는 파일마다 배너
    # 한 줄이 더 붙는다)·`fit`(줄 하나로도 안 들면 false)·`entry_kept`(entry 줄이 남았나 — 렌더
    # 형식이 어긋나 entry가 조용히 빠지면 false, #375 L-1)·`token_counter`(`model` = 그 자리
    # 토크나이저 · `estimate` = 글자 하한비 근사 3.28)를 싣는다. `subject_id`는 후보다.
    #
    # ⚠ **모델은 하네스가 뺐다는 것을 모른다**(#375 M-1). v3 prefix는 빠진 줄을 「슬라이서가
    # 뺐다」고 서술하는데 하네스가 뺀 줄은 슬라이서가 **남긴** 줄이다. 이 이벤트가 사후에 그 사실을
    # 남기는 유일한 자리다 — 프롬프트엔 트림 표식이 없다(가드는 #351 선례, 이 PR 밖).
    SCOUT_SLICE_TRIMMED = "scout_slice_trimmed"
    # 이 run이 **어느 토큰 카운터로 셌는가** (#96 (b)). 역할별로 `real`(그 모델의 실
    # 토크나이저) 또는 `approx`(글자수 근사 `len//4`)와, `approx`면 그 **사유**를 싣는다.
    #
    # **run당 정확히 1건이고, 모델을 한 번 부르기 전에 낸다** — `target_plan`과 같은 계약이다.
    # 카운터는 `build_deps` 시점에 이미 정해져 있으므로 지연 발행할 이유가 없고, 그 뒤
    # 어디서 죽어도 "무엇으로 쟀는가"는 남아야 한다.
    #
    # **이 이벤트가 생기기 전에는 폴백이 조용했다.** `tokenizers`가 없거나 HF 캐시에
    # `tokenizer.json`이 없으면 `counter_for_source`가 `None`을 돌려주고
    # `using_counter(None)`이 no-op이라 글자수 근사로 돌았는데, trace에도 `RunRecord`에도
    # 경고에도 아무것도 안 남았다. 그래서 2026-08-26 재측정은 run **전에** 사람이 세 모델을
    # 손으로 확인하고 그 출력을 보고서에 붙여야 했다 — "폴백은 조용해서 사후에는 구별할 수
    # 없다"가 그 보고서에 적힌 이유다. `graph_index`가 `status=loaded|missing`으로 "없다"와
    # "0건"을 가른 것과 같은 자리이고, 카운터에만 그것이 없었다.
    #
    # **실리는 역할은 `factory._counted_roles(config)`가 정한다** — 러너가 카운터를 켜는
    # 자리(= 프로필에 실재하는 scout 자리 + auditor)이고, 켜지지 않는 역할을 실으면 거짓
    # 정보가 된다(`config.prompt_versions()`가 `embedding`을 안 담는 것과 같은 이유).
    #
    # ⚠ **Scout 풀에서는 「켜지는 첫 멤버」를 싣는다**(PR #178 · #192). 풀 run은 그 target의
    # 첫 멤버(`scout_memory`) 카운터만 켜고(캡슐 공유 — 나머지는 요청조차 안 한다), 이제
    # 이벤트도 그 멤버를 싣고 프로필에 없는 `scout`은 안 싣는다(옛 「없는 역할에 대한
    # 기록」을 없앴다). 단일 프로필은 `scout`을 그대로 싣는다.
    # 멤버 2·3의 프롬프트를 무엇으로 셌나(공유 캡슐이 지운다)는 캐시-적중 이벤트의
    # `counted_by`가 남긴다(#192 ②). ⚠ 예산은 풀에서 마진으로 강제하고(#194), 멤버별
    # 재검증은 캡슐 공유 계약을 건드리는 별도 설계로 남는다(#84 (c) — 2026-10-12 소비자 0으로 닫음).
    #
    # **해석된 경로는 싣지 않는다.** 위 "재현성 기준"이 경로 절댓값을 금지하고 HF 캐시
    # 경로는 `Path.home()`을 지난다. 대신 `{role}_source`(리비전이 박힌 `models.yaml` 값)를
    # 실어 스냅샷까지 특정되게 한다.
    TOKEN_COUNTER = "token_counter"
    CRITIC_NORMALIZED = "critic_normalized"
    # Critic verdict를 표준값으로 **못 읽어** `need_evidence`(보류)로 보낸 사건. 자유 verdict
    # (enum grammar 회피, `DecisionDraft`)가 표준 어휘도 명백한 동의어도 아닐 때 원문을 남긴다.
    CRITIC_VERDICT_UNMAPPED = "critic_verdict_unmapped"
    # Critic 요청에 **Auditor 도구 루프의 홉 관측**을 실었다는 기록과 그 크기의 계산값 (#600 H2ⓐ).
    # 내는 곳은 `nodes/critic._record_hop_observations`이고 홉 관측이 1개 이상인 Critic 호출마다
    # 1건, 모델 호출 **앞**에서다. **이벤트 부재 = 관측을 안 실었다**(홉 0 · content-aware가 아닌
    # 판). `subject_id`는 Finding이다. 싣는 값: `hops`(Critic 사본 수 - 돈 홉 수가 아니다,
    # 가림에 실패한 홉은 빠진다) ·
    # `added_chars`(EVIDENCE에 더한 절의 글자, 구분 줄바꿈 포함 · FACTS의 `auditor_hops` 한 줄은
    # 안 셌다) · `prompt_chars`(prefix+body) · `est_input_tokens`(= `prompt_chars` ÷ 글자/토큰
    # 최악 비 1.22, 올림) · `input_share_tokens`(= 그 좌석 모델 `context_limit` − 역할
    # `output_max`) · `over_input_share`(추정이 몫을 넘음) · `over_without_observations`(관측 절을
    # 뺀 프롬프트도 넘음 — 참이면 원인이 관측이 아니다) · `window_checked`(Fake 좌석은 창이 없어
    # false이고 그때는 추정·몫·판정 키가 **없다**). ⚠ **전부 계산값이다**(서버가 센
    # `input_tokens`가 아니다 — 그 값은 같은 호출의 `model_call`에 있다). 그 비 1.22는 Auditor
    # 좌석의 최악이다. Critic 좌석의 비는 홉 관측 **없는** 요청에서 3.10~4.04로 쟀고
    # (run `run_20261011_0001`의 Critic 호출 여섯 — `nodes/critic._WORST_CHARS_PER_TOKEN` 주석),
    # **홉 관측을 실은 요청에서는 미실측**이다. `over_input_share=true`는 「최악 비에서 넘을 수
    # 있다」이다. **하네스는 자르지 않는다** — 넘어도 절은 그대로 나가고 서버가 크기로 거절하면
    # `critic_failed`가 남는다. `over_input_share`가 참인 건수는 run 경고(`warnings`)로도 오른다.
    # ⏵ 2026-10-13 - **크기 거절 폴백.** 관측을 실은 요청이 서버에서 400(컨텍스트 초과)·413으로
    # 거절되면 `critic.run`이 절을 통째로 빼고 한 번 다시 묻고(홉 0과 같은 요청), 그때 같은
    # 종류의 이벤트를 하나 더 낸다: `hops`(뺀 관측 수) · `observations_dropped: true` ·
    # `rejected_status`(거절 상태 코드). 이 이벤트에는 추정·몫 키가 없다. 부재 = 폴백이 안 났다.
    # 거절은 서버가 잰 사실이다(위 계산값과 다르다). run 경고가 그 건수를 센다.
    CRITIC_HOP_OBSERVATIONS = "critic_hop_observations"
    # Critic이 **게이트 입력에 없는** Evidence ID를 `Decision.evidence_refs`로 인용한 사건.
    # 내는 곳은 `nodes/critic._record_unknown_refs`이고 그런 인용이 1건 이상인 호출마다 1건,
    # 판정을 **바꾸지 않고** 기록만 한다(거르는 것은 설계 판단이라 안 한다). 부재 = 인용이
    # 전부 게이트 입력 안이었다(또는 인용이 없었다).
    # ⚠ **`gate.evidences_for`는 이 인용을 안 본다** — 그 함수는 **Finding**의 `evidence_refs`만
    # 거르고 `Decision.evidence_refs`를 검증하는 자리는 없다. `Decision.is_grounded`는
    # `bool(evidence_refs)`라 게이트가 못 보는 ID 하나로도 `rebut`이 「근거 있는 반박」이 되어
    # `gate.normalize`를 비껴가고 F-6 `queued`로 간다. 그 길이 생긴 것은 홉 관측이 `probe`
    # Evidence의 `evidence_id`를 Critic에 보이게 한 뒤다(#600 H2ⓐ · 이슈 #419의 E4 경계).
    # 싣는 값: `decision_id` · `cited`(인용 수) · `unknown`(그중 게이트 입력에 없는 수) ·
    # `all_unknown`(인용이 전부 그런 것) · `verdict`(`Decision.verdict` — 자유 문자열을 표준값으로
    # 읽은 뒤의 값이고 **`gate.normalize` 전**이다. 이 이벤트를 내는 자리가 그 호출 앞이라서다 —
    # 정규화 뒤 값이 궁금하면 `critic_normalized`를 같이 본다) · `grounded`
    # (`Decision.is_grounded`). `subject_id`는 Finding이다.
    CRITIC_UNKNOWN_EVIDENCE_REFS = "critic_unknown_evidence_refs"
    BUDGET = "budget"
    POLICY_DECISION = "policy_decision"
    # 홉 하나의 Scout 되묻기 기록 (ADR 0012 결정 2 — need_evidence 루프). 내는 곳은
    # `runner._emit_scout_hop`이다 — 신호가 있는 v3 좌석의 홉마다(hop0 포함) 하나. resolver 통계
    # (`resolved`·`already`·`fixable_count`·`unfilled`·`truncated`·`index_available`)는 resolver를
    # 부른 홉에만 실린다 — 훅이 없어 `nothing_resolved`로 선 홉에는 **키가 없다**(안 돌린 것을
    # 값으로 적지 않는다). 그래서 `nothing_resolved`의 원인은 셋이다: 키 없음(훅 없음 —
    # 프로덕션은 factory가 늘 꽂는다) · `index_available=False`(색인 없음/v1) · `True`(조회했는데
    # 못 찾음) (#315).
    SCOUT_HOP = "scout_hop"
    # 홉 하나의 Auditor(5단계) 도구 홉 루프 기록 (이슈 #419 — 모델-주도 루프). 내는 곳은
    # `nodes/auditor.py`의 홉 루프다 — **모델 호출이 성공한 홉마다**(0 포함) 하나(⏵ 2026-10-13
    # 크기 거절당한 홉도 하나 - 아래 `context_overflow`, #514). `hop`이 조인
    # 키다(`model_call`의 (subject_id, role, hop) 사슬 — `context.py`). ⚠ **`subject_id`는
    # 후보(candidate)다** — `probe`/`lookup` 홉에는 Finding이 아직 없다(Finding은 `answer`
    # 홉에서만 나온다).
    # 싣는 것:
    # - 공통 — `hop` · `action`(lookup/probe/answer) · `input_tokens`·`output_tokens`.
    #   **상한 값의 실측 근거가 여기서 나온다** — 홉당 usage를 안 실으면 홉 수를 몇으로 둘지가
    #   추정이 된다. 값은 어댑터가 준 `usage`이고 서버가 usage를 안 주면 어댑터가 0을 채운다 —
    #   `model_call`의 합산 `output_tokens`와 같은 한계다. `model_call`은 시도별 칸의 `null`로 그
    #   0을 가르지만(#550) 이 이벤트에는 그 칸이 없어 이 이벤트만의 미측정 표시는 없다.
    # - `lookup` — `requested`(모델이 낸 요청 수) · `ignored`(resolver 처리 상한 3을 넘어 안
    #   돈 수 — 있을 때만) · `resolved`·`already`·`fixable_count` · `unfilled`(사유 쉼표 나열)
    #   · `truncated`(이름 쉼표 나열) · `index_available`. 뒤 셋은 `SCOUT_HOP`과 같은
    #   이름·형식이다.
    #   **`resolved=0`의 세 원인은 이 값들로 가른다** — `index_available=False`면 인덱스
    #   없음/v1(조회를 안 했다, #315), `unfilled`가 차면 이름을 못 찾음, `truncated`가 차면
    #   크기 상한. 훅이 없거나 resolver가 `OSError`·`ValueError`로 실패한 홉에는 이 조회
    #   결과 키가 **없다**(안 돌린 것을 0이라 적지 않는다) — 실패는 `lookup_error`(예외 클래스
    #   이름)가 든다.
    # - `probe` — `tool` · `tool_ok`·`exit_code`·`evidence_kind` · `evidence_id`(그
    #   `tool_call` 이벤트로의 조인 키) · `spec`을 실었으면 `poc_sha256`·`poc_bytes`(**도구에는
    #   안 간다** — 기록뿐이다).
    # - `answer` — `trigger_status`(⏵ 2026-10-06 — 모순 비율의 **분모**다. v8·v9 `answer` 홉에도
    #   새로 붙는다 — 그 판의 `Finding`도 이 필드를 든다) · 어긴 경우에만
    #   `status_result_mismatch`(`refutation.result`가 가리키는 상태 ↔ `trigger_status`, 값은
    #   `"<상태>/<result>"`)·`path_without_endpoints`(경로가 비면 안 되는 상태인데 양 끝이 없다) ·
    #   ⏵ 2026-10-09 `cwe_present_on_no_signal`(v11 답이 `no_signal`인데 `cwe`가 null이 아니다 —
    #   v11만, 이슈 #525) ·
    #   그리고 `cited_probe_evidence`(최종 답이 `evidence_refs`로 인용한 `probe` Evidence
    #   수). ⚠ 그 Evidence는 러너가 게이트에 넘기는 `state.evidences`에 **없어서** 인용해도
    #   등급을 못 올린다(`gate.evidences_for`가 모르는 참조를 조용히 버린다). 0이 아니면 「모델이
    #   게이트에 안 닿는 근거를 인용했다」는 사실이다. ⏵ 2026-10-08 — 6단계가 타깃 관측분을 직접
    #   붙이므로(이슈 #552) 이 값은 **등급과 무관한 순수 인용 지표**다.
    # - 종단 홉의 `stop_reason`(`AuditorStopReason` — `answered`·`repeat_request`·`max_hop`·
    #   `context_overflow`). ⏵ 2026-10-13(#514) 마지막은 **모델 호출 자체가 크기 거절당한** 홉이라
    #   응답이 없다: `action`·`input_tokens`·`output_tokens`가 없고 `error`·`error_detail`이 든다.
    #   예외는 그대로 올라가 러너가 그 후보만 버린다(`target_skipped`).
    # - 조사가 예외로 못 돈 홉의 `observation_error`(예외 클래스 이름 — 예외는 그대로 올라간다).
    # - ⏵ 2026-10-13 (#600 H2ⓐ) `observation_sanitize_failed` — `lookup` 홉의 Critic용 사본을 못
    #   만들었다(`critic_safe_observation`이 `PromptError` - 렌더 모양이 resolver 구조값과
    #   어긋났다). 값은 그 사유 문장이다. **조사는 정상으로 돈 홉이라 `observation_error`와 다르고
    #   run은 안 멈춘다** - 그 홉의 사본만 Critic에 안 간다(Critic 절의 사본 수 = 홉 수 − 이 키가
    #   든 홉 수). `run(observations=None)`이면 사본을 안 만들어 이 키는 생기지 않는다. run 경고
    #   (`runner._critic_hop_observations_warning`)가 이 키가 든 홉 수를 센다.
    # ⚠ **이 이벤트의 부재가 말하는 것은 「루프를 안 켰다」가 아니다** — 이벤트는 모델 호출이
    # **성공한 뒤** 나가므로(크기 거절만 예외다 - 위 `context_overflow`), 켠 run도 첫 호출이
    # 실패하면(파싱 실패·예산 소진) 0건이다.
    # 꺼짐은 `model_call`의 `schema`(`Finding` ↔ `AuditorAction`)로 가른다.
    # `thresholds.auditor_tool_hops`가 0이면 이 이벤트는 0건이다.
    # ⏵ 2026-10-06 — 루프 봉투가 둘이라 켠 run의 `schema`는 v8·v9가 `AuditorAction`, v10이
    # `AuditorProcedureAction`이다(`Finding`은 한 방 판 그대로). 위 `AuditorAction`은
    # v8·v9의 이름이다.
    # ⏵ 2026-10-09 — v11은 셋째 봉투 `AuditorProcedureNullableCweAction`이다(이슈 #525).
    # ⏵ 2026-10-10 — v12는 넷째 봉투 `AuditorProcedureAuditAlwaysAction`이다(이슈 #578).
    # ⏵ 2026-10-13 (이슈 #507) — **hop 0 이벤트만** `compile_note`(true)·`compile_note_tools`
    # (`build` 실패면 `"build,asan,gdb"`, clang-tidy만이면 `"clang-tidy"` — 쉼표로 이은 문자열)를
    # 싣는다: 2단계 `build`·`clang-tidy`가 컴파일 오류로 실패해 hop0 본문 끝에 하네스 노트를 붙인
    # 호출이었다(`nodes/signals.compile_note`). **키 부재 = 노트 없음**(실패한 컴파일 Evidence가
    # 없었거나 한 방 판). 「노트 뒤 재선택률」은 **hop ≥ 0**의 `probe` 홉 `tool`이
    # `compile_note_tools`에 드는 비율로 잰다(hop 0의 답이 노트 뒤 첫 선택) — 노트가 도구를
    # 거절하지 않아 모델이 골라도 도구는 돈다.
    # ⏵ 2026-10-13 — v15는 다섯째 봉투 `AuditorProcedureProbeInputAction`이다(v12의 서브클래스 —
    # `spec` 설명만 다르다).
    AUDITOR_HOP = "auditor_hop"
    # 홉 하나의 PoV 툴 루프 기록 (Option C — 모델-주도 루프). 내는 곳은
    # `runner._run_pov_hops`다 — 홉마다(0 포함) 하나. `hop`이 조인 키다(`model_call`의
    # (subject_id, role, hop) 사슬 — `context.py`). gen/inspect 홉은 `action`과 도구 결과
    # (`tool`·`tool_ok`·`exit_code`·`evidence_kind`)를, 실패 홉은 `error`·`schema_ok`를, 종단 홉은
    # `stop_reason`(`PovStopReason`)을 싣는다. `SCOUT_HOP` ↔ `NeStopReason`과 같은 짝.
    # ⏵ 2026-10-11 (#550) — 스키마 파싱 실패 홉은 `stop_reason` **없이** 나올 수 있다(루프가 같은
    # 자리를 다시 묻는다). `parse_failed`를 싣고 서는 것은 마지막 홉(`pov_tool_hops`)의 실패와,
    # ⏵ 2026-10-12 — 그 호출의 모든 시도가 `finish_reason == "length"`로 끝난 실패(재질문 없이
    # 선다)다 — 뒤쪽만 `parse_truncated: true`를 단다(모델 호출의 `attempt_finish_reasons`와 같은
    # 사실의 홉 요약 — 서버의 말이고 키가 없으면 절단이 아니거나 못 쟀다).
    # hop 0이 실패했다가 다시 열려 살아나면 `fuzz_*` 키는 첫 성공 홉이 아니라 **실패한 hop-0
    # 이벤트**에 있다.
    # **gen 홉은 그 홉이 조립한 PoC의 `poc_sha256`도 싣는다**(맨 hex — 종단 이벤트의 `poc_sha256`과
    # 같은 형식이라 바로 대조된다). 종단 이벤트를 건너뛰는 중단 경로(예산 소진·서버 쪽 게이트웨이
    # 오류)에서 잃은 PoC를 특정하는 흔적이다 — 그 홉 자신의 도구 호출이 예산에서 끊겨도 루프가 이
    # 이벤트(`error=BudgetExhausted`)를 내고 다시 던진다(#394). 그 밖의 형으로 끊기면 없다(`poc_log`
    # 모듈 docstring의 「한계」).
    # ⏵ 2026-10-11 — **`submit` 홉은 `submit_digest`를 싣는다**(`absent` | `matched` | `unknown`) —
    # 모델이 `poc_sha256`으로 앞선 gen을 지명했는지와 하네스가 그것을 풀었는지다
    # (`runner._resolve_submit_digest`). `unknown`은 지명이 안 풀려 마지막 gen으로 돌아갔다는
    # 뜻이다. 다른 홉에는 이 키가 없다.
    # ⏵ 2026-10-12 (#619) — **이미 돌린 도구를 다시 요청한 `inspect` 홉은 도구를 안 돌린다**:
    # `tool`과 `inspect_repeat_of_hop`(그 결과를 낸 홉)만 싣고 `tool_ok`·`exit_code`·
    # `evidence_kind`는 없다(이 홉에서 잰 값이 아니다). `tool_call` 이벤트도 없다.
    # ⏵ 2026-10-13 (이슈 #507) — **hop 0 이벤트만** `compile_note`(true)·`compile_note_tools`
    # (`build` 실패면 `"build,asan,gdb"`, clang-tidy만이면 `"clang-tidy"`)를 싣는다: 2단계
    # `build`·`clang-tidy`가 컴파일 오류로 실패해 hop0 본문 끝에 하네스 노트를 붙인
    # 호출이었다(코드를 읽는 판 v3 이상만 — `fuzz_*`와 같은 경계). **키 부재 = 노트 없음.** hop0
    # 파싱 실패 뒤 다시 여는 호출(`hop` 1)의 본문에도 같은 노트가 실리지만 키는 실패한 hop 0
    # 이벤트에만 있다(`fuzz_*`와 같다). 「노트 뒤 재선택률」은 **hop ≥ 0**의 `inspect` 홉 `tool`이
    # `compile_note_tools`에 드는 비율이다.
    POV_HOP = "pov_hop"
    # PoV 루프가 **모델이 submit해** 최종 PoC를 낸 기록. 내는 곳은 `runner._pov_finish`이고
    # (`_run_pov`→`_run_pov_hops`), 프로필이 pov 좌석을 선언한 run에서 **PoV 대상** Finding당 최대
    # 1건이다 — 8단계에 들어온(진입 시 `VALIDATED`인) Finding, 그리고 `pov_attempt_top_n`이 양수면
    # 그 밖에서 더한 것(`runner._pov_targets`). `validate.run`이 `validated` 중 하나를 `QUEUED`로
    # 내려도(재현 실패·도구 정책 위반) pov는 돈다 — 그 목록이 `validate.run` **전에** 잡히는
    # 추가형이다.
    # `poc_bytes`가 최종 PoC 바이트 길이, `poc_sha256`이 그 내용 해시(재현성 대조용)다.
    # **`poc_ref`가 그 PoC를 담은 사이드카 파일**(run_dir 상대 — `pov_poc/NNNN.poc`, #355)이고
    # `poc_digest`가 **그 파일**의 `sha256:` 접두 digest다(`poc_sha256`은 접두 없는 맨 hex라 형식이
    # 다르다 — `poc_log` 모듈 docstring). 사이드카를 못 쓴 run은 둘 대신 `poc_write_error`·
    # `poc_write_error_detail`을 싣고(종단 사유·종류는 그대로), `ctx.pocs`가 없는 run(단위 테스트·
    # `--dry-run`)에는 **키 넷 모두 없다**.
    # submit이 아닌 종단(max_hop·repeat·흡수된 예외·gen 없는 submit)은 여기가 아니라 POV_SKIPPED다.
    # ⏵ 2026-10-11 — 코드를 읽는 판(v3 이상)의 submit·repeat_spec·max_hop 종단은
    # `last_gen_excluded_while_earlier_not`(bool)도 싣는다 — 마지막 gen의 `vul` 코드가 제외 코드
    # (0·71·300)이거나 없는데 앞선 gen 중에 제외 코드 밖이 있었는가. 로그이고 지정 입력이 아니다.
    # ⚠ **같은 run의 `submit_digest=matched`와 함께 읽는다** — 모델이 이긴 gen을 지명했으면 이 값이
    # True여도 위반이 아니다. 흡수된 예외 종단과 v2는 이 키가 없다. `repeat_spec` 출구의 「마지막
    # gen」은 새로 만든 gen 중 마지막이다 — 반복된 gen은 코드를 못 받는다.
    POV_GENERATED = "pov_generated"
    # PoV가 최종 PoC 없이 끝난 기록. `reason`이 `capsule_missing`(캡슐 없음)이거나 정지 사유
    # (`PovStopReason`: `parse_failed`·`gateway_error`·`context_overflow`·`repeat_spec`·`max_hop`·
    # gen 없는 `submitted`)다. 부분 PoC가 있으면 `poc_bytes`·`poc_sha256`과 함께 POV_GENERATED와
    # **같은 사이드카 키**(`poc_ref`·`poc_digest` 또는 `poc_write_error*`)도 싣는다 — 종단 사유로
    # 안 가른다(회수가 목적이다). **추가형이라 finding 상태는 `validate.run`이 이미 정했으므로**
    # pov 실패는 finding을 안 죽이고 여기 남긴다. ⏵ 2026-10-11 — repeat_spec·max_hop 종단과 gen 없는
    # `submitted` 종단(값은 False)도 `last_gen_excluded_while_earlier_not`를 싣는다
    # (조건은 POV_GENERATED 주석).
    POV_SKIPPED = "pov_skipped"
    # PoV가 낸 PoC 하나를 채점기(oracle)의 `vul` 다리에 내보낸 기록 (#386). **제출마다
    # 1행**이다 - 중간 gen(`designatable=False`, pov v3 이상의 되먹임)과 모델이 submit한 PoC
    # (`designatable=True`)가 둘 다 낸다(같은 바이트면 같은 해시로 두 번 나온다). `fix` 다리는
    # 안 돈다. `subject_id`는 finding이고 `leg`(`vul`)·`designatable`·`task_id`·
    # `poc_sha256`·`poc_bytes`를 싣는다. 소요 시간은 최상위 `wall_ms`다(도커 왕복이 들어 있다).
    #
    # ⚠ **판정 값이 없다.** 종료 코드·`success`·`poc_id`·`cached`는 서버가 돌려준 값이라 trace가
    # 아니라 사이드카 `{run_dir}/oracle/verdict.json`에 있다 — 그 값들은 장비·서버 상태에 따라
    # 갈려서 trace의 재현성 기준(아래)을 깬다(분류는 `oracle_log` 모듈 docstring). 그래서 이
    # 이벤트들을 `any(success)`로 묶을 값이 없다. **점수는 후보 N건의 any-of가 아니라
    # `oracle_final` 하나다.**
    ORACLE_SUBMITTED = "oracle_submitted"
    # 채점기 호출이 예외로 끝난 기록 (#386) — `vul` 제출 하나가 실패했거나(`leg=vul`)
    # 중간 gen의 판정 조회가 실패했거나(`leg=query_vul`) 0바이트 PoC를 안 냈다
    # (`reason=empty_poc` - 실패가 아니라 건너뜀이다). 추가형이라
    # run을 멈추지 않는다. `poc_sha256`·`poc_bytes`·`error`(예외 클래스 이름)를 싣는다.
    # `error_detail`(URL·OS 로케일 문구)은 사이드카에만 있다. 최종 단계의 실패는 이 이벤트가 아니라
    # `oracle_final`의 `status=failed`다.
    ORACLE_FAILED = "oracle_failed"
    # **이 run의 채점 결과 — 채점기가 배선되고 8단계가 끝까지 돈 run에 정확히 1건이다.** 지정한
    # 후보 하나에 `fix` 다리와 판정 조회를 돌린 결과이고, 저자 FAQ의 「최종 제출 하나」 점수에
    # 대응한다.
    #
    # **`designation`은 일곱이고 전부 위치·채점기 점수로 고른 것이 아니다**(`oracle_log`가 목록을
    # 든다) — 후보(`vul` 제출이 성공한 PoC)가 하나뿐이라 고를 것이 없었거나(`sole_candidate`),
    # 후보는 여럿인데 **PoC 바이트가 전부 같아** 선택이 없었거나(`sole_poc`), 사슬의 어느 단계가
    # 갈랐다(`critic_verdict`·`top_grade`·`top_confidence`·`smallest_poc`·`poc_digest` —
    # 하네스가 만든 값의 전순서다). 등급·`confidence`·Critic 판정 중 모르는 값이 있는 후보가 있으면
    # 지정하지 않는다(`status=none` ·
    # `reason`이 `grade_unknown`, `runner._designate_final`). 이 이벤트가 있다고 판정이 있는 것이
    # 아니다.
    #
    # `status`가 첫째 사실이다: `scored`(판정을 받았다 — 값은 `record_ref`의 사이드카) ·
    # `failed`(지정했는데 `finalize`가 예외로 끝났다 — `error`) · `none`(지정된 후보가 없었다 —
    # `reason`이 `no_poc`·`all_vul_submissions_failed`·`rate_limited`(후보가 없다) ·
    # `grade_unknown`(후보가 둘 이상인데 그중 등급·`confidence`·Critic 판정 셋 중 모르는 것이
    # 있다 - ⏵ 2026-10-11 전엔 판정이 없어 둘이었다)).
    # **`none`은 실패가
    # 아니다** — 「최종 PoC가 없다」와
    # 「최종 PoC가 실패했다」를 섞지 않는다. `subject_id`는 지정된 finding이다(`none`이면 없다).
    # 그 PoC의 `poc_sha256`·`poc_bytes`, `scored`일 때 `task_id`, 사이드카의 고정 경로
    # `record_ref`를 싣는다. 지정이 읽은 후보별 등급은 trace가 아니라 사이드카
    # `final.candidate_grades`에 있다.
    # `scored`일 때의 `task_id`는 판정 행(`/query-poc`)이 든 값(`OracleOutcome.task_id`)이라, 마스킹
    # 서버(`TaskOracle.query_task_id`)에서는 마스킹된 id가 아니라 **실 id가 trace와 사이드카에 실릴
    # 수 있다** — 코드 판독에서 나온 추론이고 마스킹 서버로 확인하지 않았다.
    #
    # ⚠ **이벤트 부재는 「제출 안 함」이 아니다.** 채점기가 배선되지 않았거나, 8단계가 끝까지 돌지
    # 않았다(예산 소진 등 — 그래도 `oracle_submitted`가 있으면 vul 제출은 나갔고 매니페스트의
    # `oracle.final`이 `not_reached`다. 단 예산·서버 쪽 게이트웨이 오류로 끊겨도 이미 낸 최종 후보가
    # 있거나 `pov_attempt_top_n`의 추가 대상에서 끊긴 run은 최종 지정까지 하고 끝나 `oracle_final`이
    # 있다). 그리고 호출 도중에 끊긴 것은 어느 이벤트도
    # 남기지 않는다:
    # `Exception`만 흡수하므로 TERM·HUP(`SystemExit`)·Ctrl-C는 이벤트 없이 올라가고, 서버는 그
    # PoC로 컨테이너를 돌렸을 수 있다.
    # ⚠ **이것은 하네스 내부 등급이 아니다** — Evidence를 만들지 않고 게이트가 안 읽는다(#232).
    ORACLE_FINAL = "oracle_final"
    # 하네스가 `repro_spec`을 합성한 기록 (#S7 — `synthesize_repro_spec`이 켜졌고 모델이 명세를
    # 비워 냈을 때 `auditor._ensure_repro_origin`이 낸다). `subject_id`는 finding이고
    # `expected_signal`(합성 신호)·`steps`(단계 수)를 싣는다. **이벤트 부재 = 합성 안 함**(꺼졌거나
    # 모델이 명세를 냈다). 모델 명세가 `origin=harness`를 참칭해 하네스가 `model`로 덮은 경우는
    # 발급값 도장과 같아 `issued_override`가 아니라 여기도 아니다 — 그 경로는 `origin` 필드로만
    # 드러난다.
    REPRO_SPEC_SYNTHESIZED = "repro_spec_synthesized"
    # 과제 설명이 캡슐 예산에 다 못 들어가 잘렸거나(kept < total) 한 줄도 못 들어간(kept 0) 기록.
    # `retrieval.run`이 낸다 — 설명은 예산을 미리 떼어 담지만(코드 자리를 굶기지 않게 최대 절반)
    # 그 상한을 넘는 긴 설명은 잘린다. **조용히 빠지지 않게** 남긴다(§0) — `kept`·`total`은 줄 수다.
    VULN_DESCRIPTION_TRUNCATED = "vuln_description_truncated"
    # 모델이 쓴 발급값(ID·`run_id`·`artifact_id`·초기 `state`·`missing_evidence`)이 하네스
    # 발급값과 **달라서 덮었다** (이슈 #279). 내는 곳은 `RunContext.stamp_issued` 하나다 —
    # Scout·Auditor 출력 1건당 최대 1행이고 **이벤트 부재 = 모델이 발급값을 그대로 썼다**
    # (`critic_skipped`와 같은 읽기).
    #
    # `fields`가 달랐던 필드 이름(쉼표로 이음)이고 `written.<필드>`가 모델이 쓴 원래 값이다 —
    # 덮은 뒤에는 그 값이 저장소에도 `transition`에도 없어서, 「모델이 사실 블록을 안 따랐다」는
    # 관측은 여기에만 남는다. `subject_id`는 발급값이다(같은 출력의 `model_call`·`transition`과
    # 같은 키).
    ISSUED_OVERRIDE = "issued_override"
    # 단계 경계 모델 교대 한 번 (ADR 0012 Phase 6.5 — 콜드 재기동). 내는 곳은
    # `RunContext.record_model_swap`이다.
    #
    # **교대 한 번당 1건이다** — 교대를 선언한 역할이 있는 단계에 처음 들어갈 때 하나, run 끝
    # 내림 하나(`models`가 빈 문자열, `stage`가 없다). **성공과 `ModelSwapError` 실패에 낸다** —
    # 실패면 `ok=false`를 예외보다 먼저 낸다. 그래야 run이 멈춘 자리가 trace 끝에 남는다. 실
    # 어댑터는 예상 밖 `Exception`을 `ModelSwapError`로 감싼다. 계약 밖 예외(어댑터가 아닌 구현)와
    # 인터럽트는 이 이벤트 없이 올라간다 — run 끝 내림의 계약 밖 예외는 run 경고만 남는다.
    #
    # **이 이벤트가 없으면 「교대하지 않았다」이다**(교대를 선언하지 않은 프로필은 0건이다) — 위의
    # 계약 밖 예외·인터럽트가 없었다면.
    # 「교대가 다 성공했다」로 읽지 않는다.
    #
    # `wall_ms`는 하네스가 잰 것(교대 명령 + 준비 확인)이고, `report.*`는 교대 명령이 보고한
    # 것이다(서버별 `report.server.<key>.<field>` — util·창·기동 초 등). 둘을 섞지 않는다 — 명령도
    # `ok`·`wall_ms`를 보고하므로 접두사 없이 펴면 하네스의 값과 이름이 겹친다.
    MODEL_SWAP = "model_swap"
    # 9단계 dedupe가 버린 Finding 하나 (이슈 #101 잔여). 내는 곳은 `nodes/report.run`이다 —
    # **버린 건당 1행이고 `count(*) = Report.duplicates`**, 중복이 없는 run은 0건이다(9단계에 도달한
    # run에서 이벤트 부재 = 안 합쳤다 — 그 전에 크래시한 run은 0건이어도 모른다).
    # `subject_id`는 버린 finding이고 `representative`가 대신 남은 finding이다.
    # `grade`·`representative_grade`(러너가 넘긴 등급, 없으면 `null` — "E0"이 아니다)와 합친 근거인
    # 키 세 칸 `cwe`·`file`·`target`(`report._dedupe_key` — `cwe`가 `null`이면 `target`은 후보 ID)을
    # 싣는다. 행 순서는 `finding_id` 오름차순이다.
    #
    # 이 이벤트가 생기기 전에는 리포트의 `중복 제거: N건`이 전부였다 — 실측 `run_20260827_0004`에서
    # E2·`upheld`였던 `find_0005`가 사라졌는데 무엇과 합쳐졌는지를 run 산출물로 답할 수 없었다.
    FINDING_DEDUPED = "finding_deduped"


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
            # 이벤트마다 flush한다 — 없으면 스트림이 블록 버퍼링(≈8KB)이라 run 도중
            # 파일에 덩어리로 지연되고(`tail -f` 비실시간), 스트림을 닫지 못한 채
            # 프로세스가 죽으면(SIGKILL·OOM-kill·미처리 예외) 버퍼의 직전 이벤트가
            # 증발한다. flush가 닫는 것은 거기까지다 — OS 페이지 캐시로 넘긴 것이라
            # 전원 차단·하드리셋(더티 페이지 유실)까지 보장하려면 fsync가 필요한데,
            # trace가 핫루프는 아니어도 fsync는 다른 급이라 그 대가를 여기서 안 치른다
            # (이슈 #208).
            self._stream.flush()
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
