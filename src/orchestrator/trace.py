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
| cache — prefix | `model_call` | `Σ cached_tokens / Σ input_tokens` |
| cache — capsule | `cache`(cache="capsule") | `count(hit=true) / count(*)` |
| 호출 그래프 커버리지 | `graph_index` | `unresolved_*`·`skipped`. run당 **0건 또는 1건** |
| 토큰 계상 방식 | `token_counter` | 역할별 `real`/`approx`와 그 사유. run당 **정확히 1건** |
| 스키마 파싱 실패율 | `model_call`(`attempts`·`ok`·`error`) | 시도 단위 — 산식 아래 (#83) |
| 근거 없는 반박 비율 | `critic_normalized` | `count(*) / count(critic의 model_call)` |
| wall-clock latency | `stage_end.wall_ms` | 단계별 / run 합계 |
| 예산 소진 지점 | `budget` | `RunRecord.stopped_after`의 근거 |
| 정책 A/B 비교 | `policy_decision` | 정책별 route 분포 (v1은 기준선) |
| 발급값 이탈 | `issued_override` | `fields`별 `count(*)`. **이벤트 부재 = 발급값 그대로**(아래) |

「발급값 이탈」의 부재가 말하는 것은 `RunContext.stamp_issued`가 덮는 키(ID·`run_id`·
`artifact_id`·초기 `state`·`missing_evidence`)뿐이다. 하네스가 덮는 `produced_by`·
`scout_member`·`slice_id`는 이 이벤트 없이 덮이므로 그 셋의 부재는 「안 쟀다」다.

「스키마 파싱 실패율」 산식: 분자 `Σ(attempts−1 if ok else attempts) −
count(error="ModelGatewayError")`, 분모 `Σ attempts`. 성공 호출의 실패는 `attempts−1`,
소진(`SchemaParseError`)은 `attempts`, 게이트웨이 거절은 파싱 대상이 없어 뺀다(#83).

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
    # "전부 봤다"로 읽혔다**(#63, W7).
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
    # 잘렸다"로 읽히는데 게이트는 이 건을 **승격시켰다**. 7로 적으면
    # `count(critic_skipped, stage=7) + count(gate_eval, stage=7) = count(promoted_to_critic)`
    # 으로 7단계가 분할로 닫힌다. "들어갔다가 죽었다"로 오독될 위험은 `kind`가 막는다 —
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
    #   `= count(promoted_to_critic)`
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
    # 더 실린다. 0이면 두 필드가 없다 — 이벤트 모양이 결정 1-b 그대로다. `auditor_definitions`가
    # 1이면 `definitions`·`definitions_dropped`(붙인·못 붙인 매크로·타입·파일 변수 정의 수 —
    # `functions`에는 안 든다)가 같은 방식으로 더 실린다.
    # `auditor_source_tokens`가 양수면 `tokens`(SOURCE 토큰 수)·`token_counter`(`model` = Auditor
    # 토크나이저 · `estimate` = 글자 근사)가 더 실린다 — 상한이 토큰일 때 무엇으로 셌는지 남긴다.
    # `auditor_entry_trim`이 1이면 `entry_over_cap`(entry 함수가 상한을 넘었나)·
    # `entry_omitted_lines`(줄이며 뺀 줄 수 — 넘었어도 슬라이스 줄이 함수 전체에 퍼지면 0)가 더
    # 실린다. `anchor_chars`는 줄이기 전 크기다. `auditor_caller_lines`가 양수면 `callers`·
    # `callers_dropped`(붙인·상한에 못 붙인 호출자 창 수 — `functions`에는 안 든다)·`call_sites`
    # (찾은 호출 지점 수 — 앞 몇 개만 읽는다)가 더 실린다.
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
    # **슬라이스 없는 run은 0건이다** — 그 run의 Auditor는 캡슐 대상 하나를 그대로 읽는다.
    AUDITOR_SOURCE = "auditor_source"
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
    # 재검증은 캡슐 공유 계약을 건드리는 별도 설계로 남는다(#84).
    #
    # **해석된 경로는 싣지 않는다.** 위 "재현성 기준"이 경로 절댓값을 금지하고 HF 캐시
    # 경로는 `Path.home()`을 지난다. 대신 `{role}_source`(리비전이 박힌 `models.yaml` 값)를
    # 실어 스냅샷까지 특정되게 한다.
    TOKEN_COUNTER = "token_counter"
    CRITIC_NORMALIZED = "critic_normalized"
    # Critic verdict를 표준값으로 **못 읽어** `need_evidence`(보류)로 보낸 사건. 자유 verdict
    # (enum grammar 회피, `DecisionDraft`)가 표준 어휘도 명백한 동의어도 아닐 때 원문을 남긴다.
    CRITIC_VERDICT_UNMAPPED = "critic_verdict_unmapped"
    BUDGET = "budget"
    POLICY_DECISION = "policy_decision"
    # 홉 하나의 Scout 되묻기 기록 (ADR 0012 결정 2 — need_evidence 루프). 내는 곳은
    # `runner._emit_scout_hop`이다 — 신호가 있는 v3 좌석의 홉마다(hop0 포함) 하나.
    SCOUT_HOP = "scout_hop"
    # 홉 하나의 PoV 툴 루프 기록 (Option C — 모델-주도 루프). 내는 곳은
    # `runner._run_pov_hops`다 — 홉마다(0 포함) 하나. `hop`이 조인 키다(`model_call`의
    # (subject_id, role, hop) 사슬 — `context.py`). gen/inspect 홉은 `action`과 도구 결과
    # (`tool`·`tool_ok`·`exit_code`·`evidence_kind`)를, 실패 홉은 `error`·`schema_ok`를, 종단 홉은
    # `stop_reason`(`PovStopReason`)을 싣는다. `SCOUT_HOP` ↔ `NeStopReason`과 같은 짝.
    # **gen 홉은 그 홉이 조립한 PoC의 `poc_sha256`도 싣는다**(맨 hex — 종단 이벤트의 `poc_sha256`과
    # 같은 형식이라 바로 대조된다). 종단 이벤트를 건너뛰는 중단 경로(예산 소진·서버 쪽 게이트웨이
    # 오류)에서 잃은 PoC를 특정하는 흔적이다 — 그 홉 자신의 도구 호출에서 끊기면 이벤트가 나가기
    # 전이라 없다(`poc_log` 모듈 docstring의 「한계」).
    POV_HOP = "pov_hop"
    # PoV 루프가 **모델이 submit해** 최종 PoC를 낸 기록. 내는 곳은 `runner._pov_finish`이고
    # (`_run_pov`→`_run_pov_hops`), 프로필이 pov 좌석을 선언한 run에서 **8단계에 들어온**(진입 시
    # `VALIDATED`인) Finding당 최대 1건이다. `validate.run`이 그중 하나를 `QUEUED`로 내려도(재현
    # 실패·도구 정책 위반) pov는 돈다 — 대상 목록이 `validate.run` **전에** 잡히는 추가형이다.
    # `poc_bytes`가 최종 PoC 바이트 길이, `poc_sha256`이 그 내용 해시(재현성 대조용)다.
    # **`poc_ref`가 그 PoC를 담은 사이드카 파일**(run_dir 상대 — `pov_poc/NNNN.poc`, #355)이고
    # `poc_digest`가 **그 파일**의 `sha256:` 접두 digest다(`poc_sha256`은 접두 없는 맨 hex라 형식이
    # 다르다 — `poc_log` 모듈 docstring). 사이드카를 못 쓴 run은 둘 대신 `poc_write_error`·
    # `poc_write_error_detail`을 싣고(종단 사유·종류는 그대로), `ctx.pocs`가 없는 run(단위 테스트·
    # `--dry-run`)에는 **키 넷 모두 없다**.
    # submit이 아닌 종단(max_hop·repeat·흡수된 예외·gen 없는 submit)은 여기가 아니라 POV_SKIPPED다.
    POV_GENERATED = "pov_generated"
    # PoV가 최종 PoC 없이 끝난 기록. `reason`이 `capsule_missing`(캡슐 없음)이거나 정지 사유
    # (`PovStopReason`: `parse_failed`·`gateway_error`·`context_overflow`·`repeat_spec`·`max_hop`·
    # gen 없는 `submitted`)다. 부분 PoC가 있으면 `poc_bytes`·`poc_sha256`과 함께 POV_GENERATED와
    # **같은 사이드카 키**(`poc_ref`·`poc_digest` 또는 `poc_write_error*`)도 싣는다 — 종단 사유로
    # 안 가른다(회수가 목적이다). **추가형이라 finding 상태는 `validate.run`이 이미 정했으므로**
    # pov 실패는 finding을 안 죽이고 여기 남긴다.
    POV_SKIPPED = "pov_skipped"
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
