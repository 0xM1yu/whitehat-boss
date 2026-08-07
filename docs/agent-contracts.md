# 에이전트 실행 계약과 데이터 스키마

에이전트는 프로세스가 아니라 **실행 계약**이다. 같은 역할을 서로 다른 모델로 A/B 평가할 때 바뀌는 것은 `model_profile` 하나뿐이어야 한다.

## 1. 실행 계약의 5요소

```yaml
agent:
  role: auditor              # scout | auditor | critic | judge
  model_profile: qwen3-coder-30b-a3b   # model-serving.md의 registry key
  tool_policy:               # 이 역할이 호출 가능한 도구 화이트리스트
    - retriever.expand_capsule
    - index.callers
    - index.callees
  token_budget:
    input_max: 24000
    output_max: 2000
    retries: 1
  output_schema: Finding     # 아래 §3
```

계약 위반 시 동작:

| 위반 | 처리 |
| --- | --- |
| `output_schema` 파싱 실패 | `retries` 한도 내 재요청(스키마 오류 메시지만 첨부). 초과 시 후보를 `queued`로 두고 실패 사유 기록 |
| `input_max` 초과 | Retrieval 단계로 되돌려 capsule 축소. 축소 불가 시 후보 분할 |
| `tool_policy` 밖 도구 호출 | 거부 후 오류를 관측치로 기록(모델 비교 지표에 사용) |
| `token_budget` 누적이 run budget 초과 | Budget Ledger가 해당 단계 차단, 부분 결과로 진행 |

## 2. 역할 정의

### 2.1 Scout / Triage

- 목적: 많은 후보를 저비용으로 거른다. **최종 취약점 판정을 하지 않는다.**
- 입력: 경량 Context Capsule(함수 시그니처, 위험 함수 호출, diff 요약, 정적분석 신호 헤드라인)
- 출력: `Candidate[]`
- 프롬프트 규약: thinking 최소화, 출력은 항목당 3줄 이내, 자유 서술 금지
- 실패 모드: 위험 함수 존재만으로 높은 점수를 주는 편향 → benign 샘플로 반드시 교정 측정([evaluation.md](evaluation.md))

### 2.2 Primary Auditor

- 목적: 선택된 후보에 대해 취약점 가설과 근거 경로를 세운다.
- 입력: 확장 Context Capsule + `Candidate`
- 출력: `Finding`(draft) + `MissingEvidence[]`
- 분석 축: source→sink 경로, memory lifetime/ownership, boundary check, 정수 연산, config·환경 조건 영향
- 규칙: 코드에서 확인 불가한 사실은 주장하지 말고 `MissingEvidence`로 요청한다. "확인 불가"가 유효한 출력이다.

### 2.3 Independent Critic

- 목적: 상위 후보만 **다른 모델 계열**로 반박한다. Auditor 설명의 재진술은 실패로 본다.
- 입력: `Finding` + Evidence 목록. **Auditor의 원문 추론은 전달하지 않는다**(주장·경로·근거 ID만 전달).
- 출력: `Decision`
- 반박 체크리스트: guard 존재 여부 / reachability(호출 가능 경로) / aliasing / lifetime / 컴파일 타임 조건·플래그 / 입력 도달성 제약
- 규칙: 반박은 근거 ID 또는 코드 위치를 동반해야 한다. 근거 없는 반박은 `uphold`로 처리하되 지표에 기록한다.

### 2.4 Evidence Judge (Evidence Gate)

- 기본은 **결정론적 규칙**이다. [architecture.md](architecture.md) §4의 E0~E4 등급 표가 구현 명세다.
- 모델 호출은 등급 경계가 애매한 경우(E1↔E2)로 한정한다.
- 출력: 상태 전이 + 사유 코드. 사유 코드는 열거형으로 고정한다(자유 텍스트 금지).

## 3. 데이터 스키마

에이전트 간에는 아래 구조체만 오간다. 전체 conversation은 전달하지 않는다. 아래 값은 모두 합성 예시다.

### Candidate

```json
{
  "candidate_id": "cand_0001",
  "run_id": "run_20260901_0001",
  "artifact_id": "art_a1b2c3",
  "location": {"file": "src/parser/http.c", "symbol": "parse_header", "line_start": 120, "line_end": 188},
  "suspected_cwe": ["CWE-787", "CWE-190"],
  "score": 0.72,
  "reason_codes": ["memcpy_unbounded", "changed_in_diff"],
  "evidence_refs": ["ev_static_0007"],
  "state": "triaged"
}
```

### Evidence

```json
{
  "evidence_id": "ev_san_0031",
  "kind": "sanitizer",
  "source": "asan",
  "artifact_id": "art_a1b2c3",
  "location": {"file": "src/parser/http.c", "line_start": 174},
  "payload_ref": "artifacts/run_20260901_0001/asan/0031.log",
  "digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "observed_at": "2026-09-01T10:22:31Z"
}
```

`kind` 열거형: `static` | `sanitizer` | `crash` | `fuzz_coverage` | `build` | `poc_run` | `diff` | `graph`

### Finding

```json
{
  "finding_id": "find_0007",
  "candidate_id": "cand_0001",
  "title": "parse_header에서 길이 검증 없는 memcpy",
  "cwe": "CWE-787",
  "state": "hypothesized",
  "confidence": 0.6,
  "path": [
    {"step": "source", "symbol": "recv_request", "note": "네트워크 입력"},
    {"step": "propagate", "symbol": "parse_header", "note": "len이 검증 없이 전달"},
    {"step": "sink", "symbol": "memcpy", "note": "고정 크기 스택 버퍼"}
  ],
  "rca_draft": "…",
  "evidence_refs": ["ev_static_0007", "ev_san_0031"],
  "missing_evidence": ["mev_0002"],
  "produced_by": {"role": "auditor", "model": "qwen3-coder-30b-a3b", "prompt_version": "v3"}
}
```

### MissingEvidence

```json
{
  "missing_id": "mev_0002",
  "finding_id": "find_0007",
  "need": "recv_request 호출부에서 len 상한이 강제되는지",
  "how_to_obtain": "index.callers(recv_request) 확장 후 guard 검색",
  "blocking": true
}
```

### Decision

```json
{
  "decision_id": "dec_0004",
  "finding_id": "find_0007",
  "verdict": "uphold",
  "rebuttal_axis": ["guard", "reachability"],
  "rationale": "상위 호출부에 길이 상한이 없음(src/net/server.c:88 확인)",
  "evidence_refs": ["ev_graph_0012"],
  "produced_by": {"role": "critic", "model": "devstral-small-2-24b", "prompt_version": "v2"}
}
```

`verdict` 열거형: `uphold` | `rebut` | `need_evidence`

### RunRecord (재현성)

```json
{
  "run_id": "run_20260901_0001",
  "condition": "B3prime",
  "repo_commit": "0000000000000000000000000000000000000000",
  "model_checksums": {"auditor": "sha256:0000…", "critic": "sha256:0000…"},
  "prompt_versions": {"scout": "v4", "auditor": "v3", "critic": "v2"},
  "tool_images": {"semgrep": "sha256:0000…", "fuzz": "sha256:0000…"},
  "budget": {"input_tokens": 4000000, "output_tokens": 400000, "gpu_seconds": 7200},
  "seed": 20260901,
  "started_at": "2026-09-01T09:00:00Z"
}
```

날짜·시각은 모두 UTC ISO-8601(`YYYY-MM-DDTHH:MM:SSZ`)로 기록한다.

## 4. 프롬프트 관리 규약

1. 모든 프롬프트는 파일로 버전 관리하고 `prompt_version`을 결과에 기록한다. 버전이 바뀌면 이전 실행 결과와 직접 비교하지 않는다.
2. 공통 system prompt + weakness checklist + output schema는 **prefix로 고정**한다(prefix cache 적중 목적). 후보별 가변부는 항상 prefix 뒤에 붙인다.
3. 출력은 JSON 스키마 강제(structured output). 파싱 실패는 지표로 집계한다(모델 비교 항목).
4. 프롬프트에 평가 정답 단서(CVE ID, 문제명, patch diff)를 넣지 않는다. → [evaluation.md](evaluation.md) 누출 방지
5. 프롬프트 변경은 [evaluation.md](evaluation.md)의 고정 표본에서 회귀 측정 후 반영한다.
</content>
