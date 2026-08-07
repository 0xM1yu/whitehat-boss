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
  "cache_key": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
}
```

`level` 열거형: `light`(Scout용) | `expanded`(Auditor용) | `focused`(Critic용, 반박 축 관련 섹션만)

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
</content>
