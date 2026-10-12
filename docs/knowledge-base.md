# Knowledge Base, Retrieval, Context 관리

## 1. KB와 Evidence Store를 분리하는 이유

| 구분 | Knowledge Base | Evidence Store |
| --- | --- | --- |
| 성격 | 실행 간 재사용되는 지식 | 이번 실행에서 관찰된 사실 |
| 예 | CVE 요약, CWE 설명, 과거 finding, 코드 인덱스 | crash log, sanitizer log, 이번 run의 정적분석 출력 |
| 수명 | 장기(버전 관리) | run 단위(artifact 디렉터리) |
| 누출 위험 | **높음** — 평가 대상 정답이 섞이면 실험 무효 | 낮음 |

이 분리는 [evaluation.md](evaluation.md)의 데이터 누출 방지와 직결된다. KB에 평가 대상의 patch diff나 write-up이 들어가는 순간 B1~B3' 비교가 무의미해진다.

## 2. KB 6계층

| 레이어 | 내용 | 주 검색 방식 |
| --- | --- | --- |
| L0 Metadata | 파일 경로, 함수명, symbol, binary offset, firmware path, artifact hash | Exact |
| L1 Summary | CVE 요약, CWE 설명, finding 요약, 패치 요약 | FTS + Embedding |
| L2 Evidence | crash log, sanitizer log, fuzzing result, static analyzer output | Exact + FTS |
| L3 Graph | caller/callee, xref, source-to-sink, config dependency | Graph traversal |
| L4 Detail | 함수 코드, 디컴파일 결과, 패치 hunk, 원본 write-up | Embedding + Exact |
| L5 Decision | 최종 finding, RCA, false positive 사유 | FTS + Embedding |

KB의 데이터 소스는 **공개 지식**이다: CWE/CVE, 패치 정보, CTF write-up, 도구 사용법. 분석 대상에서 실제로 관찰한 결과는 KB가 아니라 Evidence Store로 간다. 취약점 판정은 Evidence를 중심으로 수행하고 KB는 배경 지식만 제공한다.

**L5는 특히 가치가 높다.** false positive 사유를 축적하면 이후 실행에서 같은 패턴을 Scout 단계에서 감점할 수 있다.

## 3. Hybrid Retrieval

단순 chunk RAG는 코드 전역 문맥·심볼 관계 파악과 근거 추적에 약하다. 검색 대상 특성에 따라 조합한다.

| 대상 | 방식 |
| --- | --- |
| symbol, 파일 경로, offset, hash | Exact lookup |
| 로그, 에러 메시지, 문자열 | FTS |
| 자연어 설명, 유사 취약 패턴 | Embedding — 모델은 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이 정본 |
| 호출 관계, 데이터 흐름 | Graph traversal |

### 2단계 구조

1. **광역 수집**: symbol · call graph · keyword 기반으로 후보 문맥을 넓게 모은다.
2. **재선별**: 리랭커로 상위 N개만 남긴다.

**어느 리랭커인지는 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이 정본이다.** 이 자리의 모델은 2026-09-09
로스터에서 바뀌었고, 근거는 **만든 쪽 표에서 0.6B가 코드 검색에서 1차 검색기보다 낮다**는
것이다 — MTEB-Code 75.41(리랭킹 없음) → **73.42** `[출처주장]`, 그 차 **−1.99**는 `[계산]`
([2026-09-08 검색 조사](research/2026-09-08-retrieval.md)). ⚠ **우리가 실제로 보내는 입력
모양에서는 아직 한 번도 재지 않았다** — 그 공백이 [D63](open-questions/D/D63.md)이고(닫힘 — 아래 ⏵), 재측정
대상은 PROJECT-CONTEXT.md §1이 리랭커 자리에 둔 모델이다.

⚠ **2026-09-20 — 바로 위 「한 번도 재지 않았다」는 2026-09-13에 깨졌다.** 그날 ATOM에서 §1의
`qwen3-reranker-4b`를 우리 모양(맨 식별자 질의 × 코드 조각)으로 쟀다 —
[입력 모양 보고서](experiments/harness/2026-09-13-d63-reranker4b-input-shape-atom.md).
**「안 쟀다」가 아니라 「재서 나빴다」다** — 전체-8 렌즈에서 정답이 8건 중 7위다.
⚠ **그래도 [D63](open-questions/D/D63.md)은 닫혔다(2026-10-12)** — 그 실측이 8건·사람이 고른 온전한 함수라
실경로(32건·기계로 자른 40줄 조각)보다 쉽고, 문서 포맷만 바꿔도 순위가 뒤집혔다. 공백은
**「아무 측정도 없다」에서 「이 렌즈로는 쟀다」로 좁아졌을 뿐이고, 남은 것은 D63이 센다**(닫힘 — 아래 ⏵) —
⚠ **개수를 여기 박지 않는다**(크기 축·32건·실조각·리랭커-끈 기준선·참조 구현 대조 등, 정본은
[D63](open-questions/D/D63.md) 본문이다). 2026-09-20에 두 풀 프로필이 그 상태에서 4b로
배선됐다(D63의 그날 블록).
⏵ 2026-10-12 — D63은 닫혔다: 캡슐 `related` 경로 전제가 소멸했다(canonical `harness_pool`은 `retriever: fake`라 리랭커를 안 부른다). 4b의 실경로(40줄·32건) 순위는 미실측이고, 리랭커를 되살릴 때 [D63](open-questions/D/D63.md) 본문의 ⏵가 출발점이다.

생성 모델의 긴 context는 "쓸 수 있으니 채우는" 대상이 아니다. **retrieval이 실패했을 때만** 확장한다.

## 4. Context Capsule

모델에 저장소 전체를 넣지 않는다. **대상 하나당 캡슐 하나를 만든다.**

⚠ **「후보 하나당」이 아니다.** Scout 풀에서는 대상 하나를 멤버 셋이 각각 보므로 후보가
셋인데 **캡슐은 하나이고 셋이 공유한다**(ADR 0010 §3 · `runner._step_retrieval`).
**후보 수로 캡슐 토큰을 곱하지 않는다** — 풀 run에서 셋 배로 계상된다.

구성 요소:

- target function 본문
- 직접 caller·callee 중 관련도 상위 항목
- 사용되는 struct·class·macro·ownership helper
- 입력 경계(source)와 sink
- 관련 diff·trace·정적분석 신호
- 이미 확인된 사실과 아직 필요한 evidence

⚠ **2026-09-24 — [ADR 0012](decisions/0012-harness-canon-v2.md) 결정 1: Scout가 받는 대상 코드가
바뀐다.** 그 결정은 Joern 슬라이스를 Scout의 코드 입력으로 두고 후보 = 슬라이스 1개로
정한다 — 위 「target function 본문」 자리를 Scout에서는 슬라이스가 잇는다. **지금 코드는 아니다**
(Phase 3) — 대상 원문은 `read_symbol_source()`가 심볼 줄 범위를 잘라 싣는다(아래 예시 밑 문단).
Auditor는 슬라이스가 아니라 원본 소스를 받는다(결정 1-b = 넓은 (i), 2026-09-25 팀 합의).
(⏵ 2026-09-25 — `run --slices`로 돈 run의 Scout는 슬라이스 렌더(줄번호 gutter · 파일 배너)를 받고, LIGHT 캡슐의
`target_function`이 그 렌더를 센다(Phase 3c). Auditor의 `SOURCE`는 슬라이스가 걸친 함수들의 원본이다 — entry를
감싸는 함수(품는 함수를 모르면 entry 앞뒤 고정 창)가 먼저이고 나머지는 문자 수 상한 안에서 함수째 붙는다. EXPANDED
캡슐의 `target_function`은 entry 함수만 센다 — 상한이 뺀 함수와 못 본 줄은 trace `auditor_source`가 든다.
`--slices` 없는 run은 앞 문장 그대로다.)

```json
{
  "capsule_id": "cap_0001",
  "run_id": "run_20260901_0001",
  "target": {"file": "src/parser/http.c", "symbol": "parse_header"},
  "level": "expanded",
  "budget_tokens": 18000,
  "sections": [
    {"kind": "target_function", "tokens": 900},
    {"kind": "callers", "tokens": 1400, "items": 3},
    {"kind": "types", "tokens": 600},
    {"kind": "static_signal", "tokens": 300},
    {"kind": "known_facts", "tokens": 200}
  ],
  "evidence_refs": ["ev_static_0007"],
  "cache_key": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "expansion_count": 1
}
```

> **위 예시는 매니페스트 축만 보여 준다 (2026-08-21).** `callers`·`callees` 섹션에는
> 프롬프트에 실릴 줄이 `body`로 함께 담기고, **`tokens`·`items`가 그 본문에서 계산한 값과
> 일치해야 한다** — 어긋나면 스키마 검증기가 캡슐 생성을 거부한다:
>
>     {"kind": "callers", "tokens": 8, "items": 1,
>      "body": ["parse_request src/http/server.c:214"]}
>
> 여기 `8`은 `len("parse_request src/http/server.c:214") // 4`다(`src/tokens.py`).
> 위 예시처럼 `body`가 없는 섹션도 유효하다 — `target_function`의 원문은 캡슐이 담지 않고
> 프롬프트를 만드는 쪽이 따로 읽으며, `static_signal`·`known_facts`는 데이터원이 아직
> 붙지 않았다. **`body`가 비었다는 것은 "본문이 없다"가 아니라 "이 섹션은 본문을 여기
> 담지 않는다"이다.**

`level` 열거형: `light`(Scout용) | `expanded`(Auditor용) | `focused`(Critic용, 반박 축 관련 섹션만)

> **`light`의 「Scout」가 역할 하나를 뜻하지 않는다 (2026-09-09).** 로스터가 Scout를 **풀 세
> 역할**(`scout_memory`·`scout_boundary`·`scout_injection`)로 쪼갰고, 그 셋이 대상 하나를
> 각각 보되 **캡슐은 한 번 만든다** — 팬아웃은 캡슐이 아니라 호출 쪽이다
> ([ADR 0010](decisions/0010-harness-structure-and-model-roster.md) §3). **기존 `scout` 한
> 역할 프로필도 그대로 유효하다.** 어느 모델이 그 역할들에 앉는지는
> [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이 정본이다.

> **위 예시는 목표 구성이고 `types`는 아직 코드가 내지 않는다 (2026-08-20).**
> `callers`·`callees`는 1단계 인덱서 산출물(`index-v1`)에서 실측으로 채워지지만, 그 형식에
> **타입 정보가 없다** — `indexer.build_index()`가 내는 것은 `symbols`와 `calls`뿐이고
> `struct`·`class`·`typedef`를 뽑는 코드가 없다. 없는 데이터로 섹션을 발행하면 토큰 회계가
> 거짓말을 하므로 두 Retriever의 `LEVEL_SECTIONS`에서 `types`를 **뺐다.** 채우려면
> 인덱서에 타입 수집을 넣고 `INDEX_VERSION`을 올려야 한다(open-questions D13).
> ⏵ 2026-09-26 — index-v2가 `types`·`macros` 등 조회 표를 낸다. **캡슐 섹션으로는 싣지 않는다** —
> NE resolver가 모델이 요청했을 때 준다(`index_graph.GRAPH_SECTION_KINDS` 주석).
>
> 인덱서가 돌지 않은 프로필(`dev_fake`·`dev_hybrid`)에서는 `callers`·`callees`도 **섹션
> 자체가 없다.** 빈 섹션을 내면 "그래프가 비었다"와 "인덱서가 안 돌았다"가 구별되지 않는다.

`run_id`는 Evidence Store가 run 단위 수명이기 때문에 필요하다(§1). 같은 조건을 seed만 바꿔
여러 번 돌리므로, run 소속이 없으면 반복 실행의 캡슐이 한 저장소에서 섞인다.

`expansion_count`는 Progressive Context Expansion(§5) 횟수다. **상한은 이 구조체가 아니라
config에서 온다** — `capsule_expansion_max`가 정책값이고 초과 판정은 오케스트레이터가 한다
(초과 시 후보를 폐기하지 않고 `queued`로 둔다). (→ §5 머리 2026-09-24 ⚠ — ADR 0012 결정 6이 이
상한을 `max_hops`로 대체한다)

> 이 예시는 `tests/test_doc_examples.py`가 `ContextCapsule`로 실제 파싱해 본다.
> 스키마와 어긋나면 테스트가 깨진다.

## 5. Progressive Context Expansion

최소 context로 시작하고, 모델이 `MissingEvidence`를 반환할 때만 확장한다.

```
light capsule (callers/callees 1-hop 포함)
              ──부족──> type/macro 정의 추가
              ──부족──> 관련 evidence 원문 추가
              ──부족──> 확장 중단, 후보를 queued로 (무한 확장 금지)
```

확장 횟수 상한은 계약의 `retries`와 별개로 캡슐 정책에 둔다(초기값 3, 실측 후 조정).

> ⚠ **2026-09-24 — [ADR 0012](decisions/0012-harness-canon-v2.md) 결정 2·6: 이 사다리를
> need_evidence 루프가 대체한다.** Scout에서 부족을 알리는 것은 `MissingEvidence`가 아니라
> `triage_need_evidence` 신호와 `requested_context`이고(Auditor의 `MissingEvidence`는 그대로 남는다 —
> Auditor·Critic의 요청은 같은 resolver를 2차로 쓴다, 결정 2), 하네스는 그 target만 믿고 결정론적
> 심볼 resolver로 찾아 **같은 세션 후속 턴**으로 RETRIEVED를 준다 — (슬라이스, 좌석)마다 세션 하나,
> 최대 3홉, 정지 조건 여섯, 그리고 한 좌석이라도 review면 다른 좌석의 루프는 돌지 않는다(좌석 집계). 명세의 원천은 실험팀 Notion 「Scout RAG baseline」이고 ADR 0012가 요약한다. 위 「초기값 3」의 상한
> (`capsule_expansion_max`)은 결정 6이 `max_hops`로 대체한다. **승인됐다(2026-09-25) — 코드는 아직이다**(Phase 5) — 아래
> 「사다리의 첫 칸」 절 끝의 ⚠. (⏵ 2026-09-26 — `Thresholds.max_hops`가 config에 생겼다(PR #302,
> `config/budgets.yaml`의 thresholds 블록). 그 값을 세고 강제하는 루프는 그대로 Phase 5다. ⏵ 이 변경이
> 세고 강제한다 — `src/orchestrator/runner.py`의 `_run_ne_hops`가 `session.hop >= max_hops`에서
> `NeStopReason.MAX_HOP`으로 선다.)
> ⏵ 2026-10-13 - 이 사다리의 확장 경로(`nodes/retrieval.expand()`)는 러너 호출이 0이고(위 「사다리의 첫 칸」 절), 그래서 "예산이 캡슐을 실제로 묶었는지"([D41](open-questions/D/D41.md), 닫힘)는 `search` 시점 절단 하나로 좁아졌다 - 그 절단은 플래그가 없고 영향은 미측정이다(낮을 것으로 본다). `capsule_expansion_max`를 읽는 정책 결정점은 코드에 남아 있으나 어느 run도 부르지 않는다.

### 사다리의 첫 칸을 기본값으로 올렸다 (2026-08-23, D20을 (a)로 확정)

그전 그림은 `light capsule ──부족──> callers/callees 1-hop 추가`였다. **문서를 코드에
맞춘 변경이므로 왜 그렇게 정했는지를 남긴다.**

`run_20260821_0007`에서 이 설계가 실행되지 않는다는 것이 확정됐다. `graph_index
status=loaded`(심볼 4·호출 5)인데 그래프를 담은 프롬프트는 auditor 것 하나뿐이고 scout
프롬프트 둘에는 없었다. 원인은 **확장이 한 번도 돌지 않기 때문**이다 —
`nodes/retrieval.expand()`는 존재하지만 `runner.py`가 부르는 자리가 0개다. 즉 이
사다리의 첫 칸을 오를 경로가 구조적으로 없었고, Scout는 그래프를 얻을 수단이 없었다.

칸을 없애는 대신 **기본값으로 올렸다.** LIGHT를 얇게 둔 근거는 코드 주석에 적혀 있었고
(`hybrid_retriever`: "LIGHT는 검색하지 않는다 — Scout는 후보 전량에 대해 도는 단계라
여기서 임베딩을 부르면 model-serving.md §2의 전제가 무너진다") 그것은 **임베딩 호출
비용**에 관한 진술이다. 그래프 섹션은 임베딩을 부르지 않는다 — 이미 로드된 index.json
에서 읽는다(`index_graph.names_for()`). 실측 비용은 `callees` 10토큰 / LIGHT 예산
4,000 = 0.25%다. **적힌 근거가 이 두 칸을 배제하지 않는다.**

**사다리의 나머지 칸은 그대로 `expand()` 몫이다.** `type/macro`는 index-v1에 타입
데이터가 없어 데이터원부터 없고(D13 — ⏵ 2026-09-26 index-v2가 그 표를 낸다. 주는 길은
`expand()`가 아니라 NE resolver다), `evidence 원문`은 붙일 자리가 있다. 두 칸을
지우지 않은 이유는 `expand()` 배선이 **지금 불가능하기 때문이지 불필요해서가 아니다** —
~~Scout의 구조화 출력이 `Candidate` 하나라 "근거가 부족하다"를 담을 칸이 없고,~~ 그것(부족을
알리는 길)을 만들려면 스키마 변경·러너 루프·판정 규칙이 함께 와야 하는데 `expand()`가 한 번도 돈 적이
없어 그 규칙을 보정할 데이터가 없다.

> ⚠ **2026-09-24 — 바로 위 「Scout의 구조화 출력이 `Candidate` 하나라 "근거가 부족하다"를 담을
> 칸이 없고」는 낡았다**(취소선). 그 문장은 2026-08-23에 쓰였고, 2026-09-22 #239가 v3 Scout의 출력을
> `ScoutResponse`(`candidate` + `analysis_summary`, `src/schemas/scout_response.py`)로 배선했다 —
> `reason_codes[0]`의 `triage_need_evidence`와 `analysis_summary.requested_context`가 바로 그 칸이다.
> **그래도 그 칸을 읽는 루프는 0건이다** — 하네스가 `analysis_summary`를 버리고(`nodes/scout.py`)
> `triage_need_evidence`(0.30)가 `triage_reject`(0.10)와 같이 `below_scout_threshold`로 `queued`된다
> (`policy._scout_triage`, `scout_threshold` 0.5). 그리고 스키마의 `requested_context`는 문자열
> 목록인데 v3 프롬프트는 객체를 요구한다([D82](open-questions/D/D82.md))(D82 닫힘 2026-10-12 — 서빙은 v4). 그 루프가 §5 머리 ⚠의
> need_evidence 루프다(ADR 0012 결정 2 — Phase 5). (⏵ 2026-09-26 — 스키마가 객체 `RequestedContext`를
> 받는다(PR #302). 판정이 그것을 소비하는 루프는 그대로 0건이다.)
> (⏵ 2026-09-26 — 이 변경이 그 루프를 짓는다. `policy._scout_triage`가 `signals["signal"]`을 점수보다
> 먼저 보므로 `triage_need_evidence`는 더 이상 `triage_reject`와 나란히 `below_scout_threshold`로
> 떨어지지 않는다 — NE 신호는 좌석 집계 루프(`runner._run_ne_hops`)를 거쳐 최종 신호(review·reject)나
> `NeStopReason`(정지 사유)으로 선다. `requested_context`도 이제 소비된다 — `_run_ne_hops`가 그것을 읽어
> resolver(`src/adapters/symbol_resolver.py`)에 넘긴다. `analysis_summary` 자체(근거 문자열)는 여전히
> 버려진다 — 판정에 쓰는 것은 신호·요청이지 근거 서술이 아니다. **정지 사유는 명세의 여섯보다
> 많다** — 명세 여섯(신호가 NE 아님·파싱 실패·`max_hops` 소진·요청 반복·요청 없음·resolver
> 전무)에 하네스가 넷을 더 붙였다 — `peer_review`(다른 좌석이 hop0에 review)·`context_overflow`
> (후속 턴이 창에 안 든다 — 홉 글자 상한이 바닥나거나 서버가 크기로 거절)·`budget_exhausted`(예산
> 소진)·`gateway_error`(그 밖 게이트웨이 오류). 명세의 파싱 실패와 뒤 셋은 hop≥1 실패를 흡수해 직전
> 판정을 지키는 자리이고, 크기 아닌 400은 흡수하지 않고 run을 멈춘다(D88 —
> `runner._ne_classify_exception`). 기록은 `src/schemas/enums.py`의 `NeStopReason` 열한 개다 —
> 「신호가 NE 아님」은 최종 신호 이름 `triage_review`·`triage_reject` 둘로 적는다. **홉당 글자 상한**은 계산값이다 —
> `min(15,000자, 남은 토큰 × 3.28)`이고 3.28은 Scout 좌석 전체의 실측 하한이지 RETRIEVED 코드
> 블록만의 비는 안 쟀다. 여유 512토큰은 설계값·미실측이다. **실서빙 루프 실측은 아직 없다**
> (ATOM 스모크 전).)

**즉 이 절은 지금 절반만 구현돼 있다.** 첫 칸은 기본값이 되어 항상 실리고, 나머지
`──부족──>` 화살표는 **아무도 밟지 않는다.** 그 사실을 그림에서 지우지 않는 이유는
설계를 포기한 것이 아니기 때문이고(→ §5 머리 2026-09-24 ⚠ — ADR 0012 결정 2·6이 그 화살표를
need_evidence 루프로 바꾼다), 진행 상태는 open-questions D20 관측 ③에 있다.

## 6. 캐시 정책

| 캐시 | 키 | 무효화 조건 |
| --- | --- | --- |
| Capsule cache | artifact digest + symbol id + capsule 정책 버전 | 파일 내용 변경, 정책 버전 변경 |
| Summary cache | 함수 digest + prompt version | 함수 변경, 프롬프트 변경 |
| Finding cache | candidate 지문(위치 + CWE + 경로 해시) | 새 evidence 유입 |
| Prefix cache (vLLM) | 공통 system prompt + schema | 프롬프트 버전 변경 |

⏵ 2026-09-25 — 슬라이스 타깃(`run --slices`)의 LIGHT 캡슐(Scout용) 키에는 slice id와 슬라이서 `run_signature`가
더 붙는다(두 Retriever의 `cache_key_for()` — ADR 0012 결정 1의 #220 ②). Auditor의 EXPANDED 캡슐은 entry를
감싸는 함수가 대상이라 붙지 않는다. 무효화 조건에 「`run_signature`가
바뀜」이 더해진다 — 슬라이서가 본 소스·설정·Joern 판 등이 바뀌면 바뀌는 값이다(`slicer_pipeline.py`의
`prepare_checkpoint`). 그 변경으로 정책 버전도 올렸다(Fake `v4` · Hybrid `hybrid-v5`). 같은 날 슬라이스 질의의 그래프 섹션 본문이
슬라이스의 줄 집합이 되면서(#288 — D78 ①) 다시 올렸다(`v5` · `hybrid-v6`).

동일 artifact에 대한 summary를 반복 생성하지 않는다. cache hit ratio는 필수 지표다([evaluation.md](evaluation.md)).

## 7. KB 위생 규칙

1. KB에 넣기 전 [evaluation.md](evaluation.md)의 누출 검사 스크립트를 통과해야 한다(CVE ID 마스킹, patch diff 배제, time split).
2. 평가셋과 동일한 코드·challenge·firmware 버전은 KB에 넣지 않는다.
3. 외부 write-up을 넣을 때는 출처 URL과 수집 시각을 L0에 함께 기록한다.
4. KB 스냅샷은 버전 태그를 붙이고, 각 run의 `RunRecord`에 사용한 KB 버전을 남긴다.
