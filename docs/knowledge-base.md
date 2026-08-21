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
| 자연어 설명, 유사 취약 패턴 | Embedding (Qwen3-Embedding-0.6B) |
| 호출 관계, 데이터 흐름 | Graph traversal |

### 2단계 구조

1. **광역 수집**: symbol · call graph · keyword 기반으로 후보 문맥을 넓게 모은다.
2. **재선별**: Qwen3-Reranker-0.6B로 상위 N개만 남긴다.

생성 모델의 긴 context는 "쓸 수 있으니 채우는" 대상이 아니다. **retrieval이 실패했을 때만** 확장한다.

## 4. Context Capsule

모델에 저장소 전체를 넣지 않는다. 후보 하나당 캡슐 하나를 만든다.

구성 요소:

- target function 본문
- 직접 caller·callee 중 관련도 상위 항목
- 사용되는 struct·class·macro·ownership helper
- 입력 경계(source)와 sink
- 관련 diff·trace·정적분석 신호
- 이미 확인된 사실과 아직 필요한 evidence

```json
{
  "capsule_id": "cap_0001",
  "run_id": "run_20260901_0001",
  "target": {"file": "src/parser/http.c", "symbol": "parse_header"},
  "level": "expanded",
  "budget_tokens": 24000,
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

> **위 예시는 목표 구성이고 `types`는 아직 코드가 내지 않는다 (2026-08-20).**
> `callers`·`callees`는 1단계 인덱서 산출물(`index-v1`)에서 실측으로 채워지지만, 그 형식에
> **타입 정보가 없다** — `indexer.build_index()`가 내는 것은 `symbols`와 `calls`뿐이고
> `struct`·`class`·`typedef`를 뽑는 코드가 없다. 없는 데이터로 섹션을 발행하면 토큰 회계가
> 거짓말을 하므로 두 Retriever의 `LEVEL_SECTIONS`에서 `types`를 **뺐다.** 채우려면
> 인덱서에 타입 수집을 넣고 `INDEX_VERSION`을 올려야 한다(open-questions D13).
>
> 인덱서가 돌지 않은 프로필(`dev_fake`·`dev_hybrid`)에서는 `callers`·`callees`도 **섹션
> 자체가 없다.** 빈 섹션을 내면 "그래프가 비었다"와 "인덱서가 안 돌았다"가 구별되지 않는다.

`run_id`는 Evidence Store가 run 단위 수명이기 때문에 필요하다(§1). 같은 조건을 seed만 바꿔
여러 번 돌리므로, run 소속이 없으면 반복 실행의 캡슐이 한 저장소에서 섞인다.

`expansion_count`는 Progressive Context Expansion(§5) 횟수다. **상한은 이 구조체가 아니라
config에서 온다** — `capsule_expansion_max`가 정책값이고 초과 판정은 오케스트레이터가 한다
(초과 시 후보를 폐기하지 않고 `queued`로 둔다).

> 이 예시는 `tests/test_doc_examples.py`가 `ContextCapsule`로 실제 파싱해 본다.
> 스키마와 어긋나면 테스트가 깨진다.

## 5. Progressive Context Expansion

최소 context로 시작하고, 모델이 `MissingEvidence`를 반환할 때만 확장한다.

```
light capsule ──부족──> callers/callees 1-hop 추가
              ──부족──> type/macro 정의 추가
              ──부족──> 관련 evidence 원문 추가
              ──부족──> 확장 중단, 후보를 queued로 (무한 확장 금지)
```

확장 횟수 상한은 계약의 `retries`와 별개로 캡슐 정책에 둔다(초기값 3, 실측 후 조정).

## 6. 캐시 정책

| 캐시 | 키 | 무효화 조건 |
| --- | --- | --- |
| Capsule cache | artifact digest + symbol id + capsule 정책 버전 | 파일 내용 변경, 정책 버전 변경 |
| Summary cache | 함수 digest + prompt version | 함수 변경, 프롬프트 변경 |
| Finding cache | candidate 지문(위치 + CWE + 경로 해시) | 새 evidence 유입 |
| Prefix cache (vLLM) | 공통 system prompt + schema | 프롬프트 버전 변경 |

동일 artifact에 대한 summary를 반복 생성하지 않는다. cache hit ratio는 필수 지표다([evaluation.md](evaluation.md)).

## 7. KB 위생 규칙

1. KB에 넣기 전 [evaluation.md](evaluation.md)의 누출 검사 스크립트를 통과해야 한다(CVE ID 마스킹, patch diff 배제, time split).
2. 평가셋과 동일한 코드·challenge·firmware 버전은 KB에 넣지 않는다.
3. 외부 write-up을 넣을 때는 출처 URL과 수집 시각을 L0에 함께 기록한다.
4. KB 스냅샷은 버전 태그를 붙이고, 각 run의 `RunRecord`에 사용한 KB 버전을 남긴다.
