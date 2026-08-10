# 시스템 아키텍처

## 1. 설계 원칙

1. **Tool-first, Model-second**: 결정론적 도구가 잘하는 탐색(diff, 정적분석, sanitizer, fuzz)은 도구가 한다. LLM은 도구가 남긴 신호를 해석하는 데만 쓴다.
2. **역할은 서비스가 아니라 실행 계약**: 에이전트마다 프로세스를 띄우지 않는다. 하나의 프로세스 안에서 `role + model_profile + tool_policy + token_budget + output_schema` 조합으로 실행된다. → [agent-contracts.md](agent-contracts.md)
3. **비싼 단계일수록 뒤에**: 후보는 단계마다 줄어들고, 모델은 뒤로 갈수록 커진다. 각 단계는 통과율(제거율)을 기록한다.
4. **에이전트 간에는 대화가 아니라 구조체만 전달**: 다른 에이전트의 장문 추론을 프롬프트에 넣지 않는다.
5. **처음부터 분산 시스템을 만들지 않는다**: 단일 Python 서비스 + SQLite/DuckDB + 로컬 artifact 디렉터리 + Docker sandbox + bounded in-process queue.

## 2. 파이프라인

```mermaid
graph LR
    A["Repository / Diff / Crash"] --> B["Deterministic Preprocessor"]
    B --> C["Code Index + Graph + Evidence Store"]
    C --> D["Retriever<br>Embedding + Reranker"]
    D --> E["Scout / Triage<br>Small Model"]
    E -->|low score| X["Discard or Queue"]
    E -->|candidate| F["Primary Auditor<br>Code Model"]
    F --> G["Evidence Gate"]
    G -->|insufficient| X
    G -->|high-value only| H["Independent Critic<br>Different Model Family"]
    H --> I["Tool Validation<br>Static / Build / Sanitizer / Fuzz"]
    I --> J["Deterministic Dedupe + Report"]

    K["Stateful Orchestrator"] --> B
    K --> E
    K --> F
    K --> H
    K --> I
    L["Model Gateway + Budget Ledger"] --> E
    L --> F
    L --> H
    M["Trace / Metrics"] --> K
    M --> L
```

## 3. 단계별 책임과 통과 조건

| # | 단계 | 입력 | 처리 | 출력 | 다음 단계 통과 조건 |
| --- | --- | --- | --- | --- | --- |
| 0 | Preprocessor | repo / firmware image / crash bundle | 펌웨어 추출(binwalk), 파일 타입 분류(file), 바이너리·소스·설정 식별, artifact hash 부여 | `Artifact[]` | 파일 타입 분류 성공 |
| 1 | Index & Graph | `Artifact[]` | 소스: tree-sitter로 함수·symbol·AST 분해 / 바이너리·펌웨어: strings, readelf, objdump, Ghidra Headless. 공통으로 symbol table, xref, function boundary, call graph 추출 | Code Index, Graph | 대상 함수 심볼 해석 성공 |
| 2 | Signal Collection | Artifact, Index | Semgrep · clang-tidy · grep/custom rule 정적분석, sanitizer log·stack trace 파싱, AFL++/libFuzzer coverage, diff 분석 | `Evidence[]` | 실패해도 진행(신호 부재로 기록) |
| 3 | Retrieval | 후보 seed | symbol·graph·keyword 광역 수집 → embedding + reranker 재선별 | `ContextCapsule` | capsule 토큰 상한 이내 |
| 4 | Scout / Triage | `ContextCapsule`(경량) | 후보 우선순위 분류, 위험 카테고리 부여 | `Candidate[]` (score, CWE 추정) | `score >= scout_threshold` |
| 5 | Primary Auditor | `ContextCapsule`(확장) + `Candidate` | source-to-sink, memory lifetime, boundary·guard 분석, RCA 초안 | `Finding`(draft) + `MissingEvidence[]` | Evidence Gate 판정 |
| 6 | Evidence Gate | `Finding` + `Evidence[]` | 결정론적 규칙으로 근거 충족 여부 판정. 애매한 경우에만 모델 호출 | 상태 승격/보류/폐기 | 근거 등급 §4 참조 |
| 7 | Independent Critic | 상위 `Finding`만 | 다른 계열 모델로 guard·reachability·aliasing·lifetime·환경 조건 반박 | `Decision`(rebut/uphold) + 근거 | 반박이 기각될 때 |
| 8 | Tool Validation | `Finding` + 재현 명세 | build, sanitizer, fuzz, PoC 실행 (Docker sandbox) | 실행 로그 `Evidence` | 재현 성공 시 `proven` |
| 9 | Dedupe & Report | `Finding[]` | 규칙 기반 중복 제거, 템플릿 리포트 생성 | 리포트 | — |

> **분석 대상 범위**: 킥오프 발표자료 04-1은 이 하네스의 대상을 "펌웨어의 취약점 분석"으로 명시한다. 소스가 없는 바이너리·펌웨어도 1급 입력이므로 0·1단계는 소스 경로와 바이너리 경로를 **둘 다** 갖춰야 한다. 어느 쪽을 먼저 구현할지는 [open-questions.md](open-questions.md) D9.
>
> **단계 순서 주의**: 위 표는 `Evidence Gate(6) → Critic(7)` 순서다. 킥오프 04-6 표는 `Critic(4차) → Evidence Gate(5차)`로 반대이나, 같은 자료의 05-2 구조도와 하네스 제안서 §3은 위 순서를 따른다. 다수 근거를 채택했고 확정은 [open-questions.md](open-questions.md) D8.

**Queue 규칙**: 4·6단계에서 탈락한 후보는 즉시 버리지 않고 `queued` 상태로 남긴다. 새 Evidence(신규 crash, 신규 정적분석 룰)가 들어오면 해당 후보만 재평가한다.

## 4. Finding 상태 머신

```
candidate ──scout pass──> triaged ──auditor──> hypothesized
                                                  │
                                     evidence gate│
                        ┌──insufficient───────────┤
                        ▼                         ▼ sufficient
                     queued  <──rebutted──  critic review
                        │                         │ upheld + 재현 명세
                        │                         ▼
                        │                     validated
                        │                         │ tool validation 재현 성공
                        │                         ▼
                        │                      proven ──> reported
                        │                                 (2인 검증 후, disclosure-policy.md)
                        │
                        └──new evidence──> candidate | hypothesized   (재평가)
```

**`queued`에서 돌아가는 곳은 `validated`가 아니다.** 새 Evidence가 들어오면 Candidate는
`candidate`로, Finding은 `hypothesized`로 돌아가 게이트를 처음부터 다시 통과해야 한다 —
재평가가 판정 단계를 건너뛰면 근거 등급이 검증되지 않은 채로 승격된다. 재평가 횟수 상한은
config(`requeue_max`)에서 오고, 초과해도 폐기하지 않고 `queued`를 유지한다.

> 전이 표 전체(트리거·단계·사유 코드)는 `src/orchestrator/state_machine.py`의 모듈
> docstring에 있다. 이 그림은 그 표의 요약이며, 둘이 어긋나면 표가 기준이다.

근거 등급(Evidence Gate 판정 기준):

| 등급 | 조건 | 승격 결과 |
| --- | --- | --- |
| E0 | 모델 주장만 존재 | `queued` (폐기 아님) |
| E1 | 정적분석 신호 또는 코드 경로가 후보와 일치 | `hypothesized` 유지 |
| E2 | source→sink 경로가 call graph로 확인되고 guard 부재가 코드로 확인 | Critic으로 승격 |
| E3 | Critic이 반박 실패 + 재현 명세 존재 | `validated` |
| E4 | sanitizer/crash/PoC 재현 로그 존재 | `proven` |

구체적 임계값(`scout_threshold`, 단계별 승격 비율)은 실측 후 확정한다. → [open-questions.md](open-questions.md)

## 5. 횡단 구성 요소

| 구성 요소 | 책임 | 비고 |
| --- | --- | --- |
| Stateful Orchestrator | 단계 실행 순서, checkpoint/resume, human gate | LangGraph v1 후보. 각 node는 순수 함수 + Pydantic schema |
| Model Gateway | 역할→모델 라우팅, fallback, 구조화 출력 강제, prefix cache 활용 | vLLM(OpenAI-compatible) + LiteLLM |
| Budget Ledger | 실행 단위 토큰·GPU-second 한도 집행, 초과 시 단계 차단 | 예산 소진 시 부분 결과로 리포트 |
| Evidence Store | 세션에서 관찰된 사실(로그, 분석 결과) 저장 | KB(재사용 지식)와 분리 → [knowledge-base.md](knowledge-base.md) |
| Trace / Metrics | JSONL + OpenTelemetry trace, 단계별 통과율·비용. 모델 호출(`model_call`)과 검색 호출(`retrieval_call`)을 분리 기록한다 — 후자는 출력 스키마가 없어 같은 분모에 넣으면 지표가 섞인다 | → [evaluation.md](evaluation.md) |
| Tool Sandbox | build/sanitizer/fuzz/PoC 실행 격리 | Docker, 네트워크 차단 기본값 |

## 6. 명시적 비범위 (MVP)

- 패치 자동 생성 (AIxCC CRS의 patcher 범위는 제외)
- Kubernetes·분산 워커·외부 vector DB
- LLM 기반 리포트 생성 에이전트 (템플릿 + 규칙으로 처리)
- LLM 기반 dedupe 에이전트
- 다중 노드(ConnectX-7 2대) 구성 — 단일 노드 병목 확인 후 재검토
</content>
