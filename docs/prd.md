# PRD: 소형 Open-Weight LLM 앙상블 기반 취약점 연구 Multi-Agent Orchestration

- 팀명: 화햇대장 (WHITEHAT SCHOOL 4th)
- 문서 버전: v1.0 (초안)
- 작성일: 2026-08-05

## 1. 개요

### 1.1 배경

대형 상용 LLM(Frontier model)을 이용한 취약점 탐지는 비용과 프라이버시 문제를 동반한다. 권한 관련 취약점 검사 24건 실행에만 297~2,970달러, 전체 213건 실행에는 2,636~26,359달러가 소요되며, 상용 API를 사용하면 코드가 외부 서버로 전송되어 기업 기밀 유출 및 보안·규정 위반 소지가 생긴다. 2026년 7월 CyberGym 평가 규칙이 성공률뿐 아니라 실행 비용도 함께 보고하도록 바뀌면서, 평가 기준 자체가 "성능 중심"에서 "성능 + 비용 효율성"으로 이동하고 있다. IRIS·BACFuzz·Tencent 등의 선행 사례는 소형 오픈소스 LLM만으로도 취약점 탐지가 가능함을 보여준다(규칙 기반으로 후보 132개를 7개까지 축소한 뒤 LLM이 최종 4건을 정확히 탐지).

이 프로젝트는 이러한 배경 위에서, 다수의 소형 오픈소스 LLM을 온프레미스에서 협력시켜 소프트웨어 취약점을 탐지하는 자동화 시스템을 구축한다.

### 1.2 이 프로젝트가 검증하려는 질문

작은 오픈 모델을 여러 개 묶었을 때 Frontier 모델의 취약점 탐지 성능에 어디까지 근접할 수 있으며, 그 대가로 비용과 프라이버시에서 얼마나 큰 이점을 얻을 수 있는가.

### 1.3 범위

시스템은 세 가지 구성 요소로 이루어진다.

1. **소형 오픈 모델 앙상블**: 로컬 환경에서 실행되는 다수의 소형 오픈소스 LLM
2. **Orchestration 계층**: 각 모델에 적합한 작업을 분배하며 전체 탐지 과정을 조율
3. **검증 엔진**: 모델이 제안한 취약점 후보를 정적 분석·빌드·sanitizer·fuzzing 등 결정론적 도구로 검증

초기 목표는 "많은 에이전트"가 아니라 **적은 수의 역할을 계층적으로 실행하여, 제한된 연산 예산 안에서 검증 가능한 finding을 최대화하는 것**이다. 보고서 생성과 dedupe는 가능한 한 템플릿·규칙 기반으로 처리하고 별도 LLM 에이전트를 두지 않는다.

### 1.4 참고한 공개 시스템과 차이점

| 참고 시스템 | 참고한 점 | 이 프로젝트와의 차이 |
| --- | --- | --- |
| Microsoft MDASH | Prepare → Scan → Validate/Debate → Dedupe → Prove 단계 분리, 교차 검증, evidence 중심 confidence 상승 | 100개 이상 에이전트·frontier model 중심 규모 대신 소수 역할·소형 모델로 축소 |
| Theori RoboDuck | 역할별 모델 매핑, 정적 분석·퍼징·LLM 분석 병렬 결합, crash triage → PoV → patch 흐름 | AIxCC 결선급 대규모 API 예산 대신 온프레미스 고정 예산 |
| Buttercup / AIxCC CRS | Orchestrator·seed generation·fuzzing·program model·patcher 분리, LLM을 증폭기로 사용 | 초기 단계부터 Kubernetes·분산 서비스로 가지 않고 단일 노드로 시작 |
| Atlantis / OSS-CRS | 표준 CLI, Docker 재현, 자원·LLM budget 설정, 모듈 교체 계약 | patch 생성까지 포함하는 전체 CRS 범위 대신 탐지·근거·리포트까지로 한정 |

## 2. 목표 및 성공 기준

### 2.1 목표

최고 성능이 아니라 **정확도와 비용을 함께 고려한 최적의 Multi-Agent 구조를 설계하는 것**이 목표다. 계산량을 고정한 뒤 다음 4개 조건을 비교한다.

> ⚠ **2026-09-24 — 아래 2×2 축은 2026-09-20에 폐기됐다**([ADR 0011](decisions/0011-retire-the-2x2-condition-axis.md), PM 결정). 조건 정의는 [evaluation.md](evaluation.md) §2.2 하나이고(`RunRecord.condition`은 `@pipeline`만 받는다) 방향은 Scout 오케스트레이터다. 아래 표와 두 효과 정의는 2026-08 초안의 기록이다.

| 구분 | 같은 질문을 여러 번 | 서로 다른 질문으로 역할 분담 |
| --- | --- | --- |
| 단일 모델 | B1 | B1' |
| 여러 모델(앙상블) | B3 | B3' |

- 역할 분담 효과: B1 → B1' 성능 향상으로 측정
- 모델을 섞은 효과: B1' → B3' 성능 향상으로 측정

### 2.2 최종 산출물과 평가 기준

| 최종 산출물 | 목표 | 평가 기준 |
| --- | --- | --- |
| Multi-Agent 취약점 탐지 파이프라인 | 정해진 계산량 안에서 B1/B1'/B3/B3' 4개 조건 모두 실행 | 입력·출력 토큰 분리 기록, 캐시 재사용률, 단계별 후보 제거율, 실패 원인 |
| Frontier 모델 대비 비교 표 | 비교표의 모든 항목을 실제 측정 결과로 작성 | 공개 기준선(CodeQL 27, GPT-4 55 등), 취약점 1건당 비용, 사람 검토 시간, 외부 전송 데이터량 |
| Zero-Day 취약점 | 기본 목표: 오탐 제거 / 최종 목표: 신규 취약점 제보 | 표본(150건) 기반 오탐률, AI 판정과 사람 판정의 일치도 |

발견된 취약점은 최소 2인 이상의 검증을 거친 후 제보하며 AI가 생성한 내용을 그대로 제출하지 않는다. 공개된 소스코드와 직접 구축한 로컬 환경에서만 연구를 수행하여 관련 규정을 준수한다.

MVP의 목표는 완전한 Zero-day 취약점 자동 탐지가 아니라, **취약점 후보 수집·우선순위화·RCA 초안 작성·근거 정리·오탐 검토를 자동화하여 분석 시간을 단축할 수 있는지 검증하는 것**이다.

## 3. 팀 구성 및 역할 분담

팀 화햇대장은 멘토 1명, PL 1명, 멘티 8명(PM 포함) 총 10명으로 구성된다. **초안의 8-way 개인 트랙 모델은 폐기됐다**(2026-09-01). 트랙 단위 개발 대신 하네스는 두 명이 맡고, 나머지는 데이터셋 확보와 파인튜닝 리서치로 나뉜다.

그 결정으로 「개인별 트랙 배정 · 트랙 간 동기화 · 단계별 역할 분담」은 전제가 사라져 닫혔다([open-questions.md](open-questions.md) §3).

## 4. 시스템 아키텍처

### 4.1 전체 파이프라인

```
Repository / Diff / Crash
  → Deterministic Preprocessor
  → Code Index + Graph + Evidence Store
  → Retriever (Embedding + Reranker)
  → Scout/Triage (Small Model)
      -- low score --> Discard or Queue
      -- candidate --> Primary Auditor (Code Model)
  → Evidence Gate
      -- insufficient --> Discard or Queue
      -- high-value only --> Independent Critic (Different Model Family)
  → Tool Validation (Static / Build / Sanitizer / Fuzz)
  → Deterministic Dedupe + Report

Stateful Orchestrator → (Preprocessor, Scout, Auditor, Critic, Tool Validation)
Model Gateway + Budget Ledger → (Scout, Auditor, Critic)
Trace/Metrics → (Orchestrator, Model Gateway)
```

### 4.2 핵심 구성 요소

| 구성 요소 | 도구 기반 처리 | LLM 활용 |
| --- | --- | --- |
| Preprocessor | 펌웨어 추출, 파일 타입 분류, 바이너리·소스·설정 파일 식별 *(2026-09-19 — **펌웨어 추출은 2차다.** [ADR 0010](decisions/0010-harness-structure-and-model-roster.md) §5가 1차 분석 대상을 **C/C++ 소스 저장소**로 확정했다: 확정된 슬라이서가 소스 기반 CPG라 펌웨어를 1급 입력으로 두면 1단계가 성립하지 않는다. 왼쪽은 그 시점의 요구 기록이다)* | X |
| Code/Binary Index | tree-sitter, symbol table, strings, xref, function boundary, call graph 추출 | 필요한 context 선별 |
| Evidence Store | 정적 분석 결과, crash log, sanitizer log, fuzzing 결과 저장 | 판단 근거 제공 |
| Retriever | symbol, call graph, embedding, reranker 기반 context capsule 생성 | 검색된 context 기반 분석 |
| Scout | 위험 함수, 입력 경로, crash 위치 기반 후보 우선순위 결정 | 후보 취약점 1차 분류(Triage) |
| Auditor | source-to-sink, memory lifetime, boundary check, config 영향 분석 | 취약점 가설 수립 및 RCA 작성 |
| Critic | 다른 모델을 활용한 교차 검증 | 오탐 및 도달 가능성 검토, guard 확인 |
| Evidence Gate | 근거 기준 충족 여부 판단 | 근거 기반 결과 검증 |
| Tool Validation | build, sanitizer, fuzzing, PoC 실행 로그 기반 검증 | 검증 결과 해석 및 보완 |
| Reporter | 중복 제거 및 템플릿 기반 리포트 생성 | 리포트 설명 및 RCA 보강 |

에이전트는 서비스나 별도 프로세스가 아니라 다음 5요소로 구성된 **실행 계약**으로 정의한다. 이를 통해 에이전트 수를 늘리지 않고도 동일 역할을 서로 다른 모델로 A/B 평가할 수 있다.

- `role`: scout, auditor, critic 등 수행 목적
- `model_profile`: 사용할 모델과 fallback
- `tool_policy`: 읽을 수 있는 도구와 명령
- `token_budget`: 입력·출력·재시도 한도
- `output_schema`: 다음 단계가 소비할 구조화 결과

### 4.3 계층형 Knowledge Base

단순 chunk 기반 RAG는 코드 전역 문맥·심볼 관계 파악, 근거 추적이 어렵다는 한계가 있다. 이를 보완하기 위해 재사용 지식(Knowledge Base)과 세션 관찰 사실(Evidence Store)을 분리하고, KB는 다음 6개 레이어로 구성한다.

| 레이어 | 내용 |
| --- | --- |
| L0 Metadata | 파일 경로, 함수명, symbol, binary offset, firmware path, artifact hash |
| L1 Summary | CVE 요약, CWE 설명, finding 요약, 패치 요약 |
| L2 Evidence | crash log, sanitizer log, fuzzing result, static analyzer output |
| L3 Graph | caller/callee, xref, source-to-sink, config dependency |
| L4 Detail | 함수 코드, 디컴파일 결과, 패치 hunk, 원본 write-up |
| L5 Detail | 최종 finding, RCA, false positive 사유 |

검색 대상 특성에 따라 Exact Search, FTS, Embedding Search, Graph Retrieval을 조합하는 Hybrid Retrieval로 불필요한 context를 줄인다.

### 4.4 Context Management 전략

1. **Progressive Context Expansion**: 최소 context부터 제공하고 필요 시 관련 Evidence를 단계적으로 확장, 불필요한 token 사용 최소화
2. **Context Capsule**: 후보별 최소 분석 context 구성, target function 중심 정보 제공, Evidence 및 원본 위치 포함
3. **중복 관리와 Cache**: 동일 artifact 분석 결과 재사용, Context Capsule 및 Finding Cache 활용, artifact 변경 시에만 재검증
4. **Context Budget**: context 크기·검색 범위 제한, 필요한 Evidence만 선별 제공, token 비용과 추론 시간 절감

### 4.5 초기 모델 구성 (2026-08 기록)

**정본은 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이다.** 아래는 2026-08 초기안이고 로스터는 2026-09-09에
확정됐다. ~~표의 다섯 중 셋은 **자리가 바뀌었다** — `Qwen3-8B`는 Scout·Injection,
`Qwen3-Coder-30B-A3B`는 Scout·Memory, `Devstral Small 2`는 Scout·Boundary다.~~ → **2026-09-24 — 그
2026-09-09 판도 2026-09-21에 교체됐다**(Scout 세 자리·Auditor가 바뀌고 Auditor A/B가 소멸했다). 지금
어느 모델이 어느 자리인지는 그 파일만 본다 — 아래 표는 2026-08 기록이다.


| 역할 | 초기 추천 모델 | 비고 |
| --- | --- | --- |
| Code retrieval | Qwen3-Embedding-0.6B, Qwen3-Reranker-0.6B | 저비용 후보 문맥 검색·축소, 상시 구동(CPU 또는 소량 GPU) |
| Scout/Triage | Qwen3-8B | 후보 취약점 분류·요약·우선순위 결정, 짧은 출력·thinking 최소화·높은 병렬성 |
| Primary Auditor | Qwen3-Coder-30B-A3B-Instruct | 30.5B(3.3B active) MoE, 256K context, repository-scale 분석에 최적화, 선별 후보에만 호출 |
| Independent Critic | Devstral Small 2 24B | Primary와 다른 모델 계열, codebase 탐색·tool-use 지원, 상위 후보만 검증하여 비용 최적화 |

현재 가용 장비는 GIGABYTE AI TOP ATOM(NVIDIA GB10, 통합 메모리 128GB *(공식 할당 80 GiB — 34→64(2026-08-25 PL 공식)→80으로 2026-08-31 확장, cgroup `memory.max`=85,899,345,920=80 GiB 실측 확인. docs/model-serving.md §1)*, 대역폭 273GB/s) 1대이며, 용량은 넉넉하지만 대역폭이 좁아 **대형 모델의 긴 응답을 기다리는 용도보다 여러 요청을 동시 처리하는 멀티 에이전트 구성에 유리**하다. Auditor·Critic은 NVFP4 양자화를 기본값으로 검토하고 8K/16K/32K context 기준으로 실측한다. 2대 연결(ConnectX-7) 확장은 단일 노드 benchmark에서 병목이 확인된 뒤 검토한다.

### 4.6 Cost-Effective 및 토큰 효율 전략

1. Tool-first, Model-second: 기존 분석 도구(diff, static analysis, fuzzing) 우선 활용, LLM은 필요한 코드만 분석
2. Full repository prompt 금지: target function 중심 Context Capsule만 전달
3. 두 단계의 Retrieval: symbol·call graph·keyword로 넓게 탐색 후 embedding+reranker로 상위 context 재선별
4. 구조화된 정보 전달: 에이전트 간 전체 conversation 대신 Candidate/Evidence/Decision/MissingEvidence 구조만 전달
5. Prefix cache와 batch 활용: 공통 prompt를 prefix로 고정, 반복 요청의 prefill 비용 절감
6. 비용 기반 성능 관리: token/GPU 사용량, cache hit ratio, finding당 비용(GPU-hour)을 지속 추적

### 4.7 유지보수 원칙

- **Model Registry**: 역할과 모델을 코드에서 분리(YAML profile), endpoint·context limit·quantization·license·checksum·prompt template version·fallback·benchmark 결과 기록
- **교체 가능한 4개 인터페이스**: `ModelClient`(OpenAI-compatible), `Retriever`(symbol·graph·embedding), `ToolRunner`(정적분석·build·sanitizer·fuzz), `StateStore`(run·candidate·evidence·metric)
- **오케스트레이터 종속성 최소화**: LangGraph v1을 초기 후보로 하되 각 node는 순수 Python 함수와 Pydantic schema로 작성해 향후 이관 가능하게 유지
- **실행 재현성**: repository commit/source hash, model·tokenizer·quantization checksum, prompt·agent spec version, tool/container image digest, budget/threshold, random seed 기록
- **관찰 가능성**: 초기에는 JSONL + OpenTelemetry trace만 필수로 하고, 필요 시 self-hosted Langfuse 추가
- **처음부터 분산 시스템으로 만들지 않기**: 단일 Python service, SQLite/DuckDB state, local artifact directory, Docker sandbox, bounded in-process queue로 시작하고 Redis/Kubernetes/vector DB/다중 worker는 병목 확인 후 추가

## 5. 평가 및 벤치마크 계획

### 5.1 평가 데이터셋

| 평가 데이터 | 설명 |
| --- | --- |
| CTF 시스템 해킹 문제 | BOF, UAF, Command injection 등 명확한 정답을 가진 샘플 |
| Juliet Test Suite | C/C++ 기반 취약 코드와 정상 코드 쌍 |
| 공개 CVE 패치 샘플 | 취약 버전과 패치 버전 |
| 공개 펌웨어 샘플 | 실제 펌웨어 구조 |
| 정상 샘플(Benign) | 알려진 취약점이 없는 benign code/binary/firmware |
| Fuzzing Crash 샘플 | sanitizer 또는 crash log가 존재하는 샘플 |

패치된 버전, 안전한 API 사용 예제, crash가 발생하지 않는 펌웨어 컴포넌트를 함께 포함해 단순히 위험 함수 존재만으로 취약점으로 오판하지 않는지 검증한다.

### 5.2 벤치마크 조건

| 조건 | 구성 | 비교 목적 |
| --- | --- | --- |
| B1 | 단일 LLM 또는 단순 Retrieval 기반 소스코드 분석 | Source-only 접근의 한계 확인 |
| B1' | 바이너리·펌웨어 artifact를 포함한 분석 | 입력 범위 확장 효과 검증 |
| B3 | Multi-Agent Orchestration(Scout, Auditor, Critic) 적용 | 역할 분리 및 모델 협업 효과 검증 |
| B3' | B3 + Evidence Gate + Tool Validation | 오탐 감소 및 근거 품질 향상 검증 |

### 5.3 데이터 누출 방지 기준

| 기준 | 설명 |
| --- | --- |
| Train/Test 분리 | KB와 평가 데이터 간 중복 및 정답 누출 방지 |
| CVE ID Masking | CVE 번호, 문제명, Flag, write-up 제목 등 정답 단서 제거 |
| Patch Leakage 방지 | 평가 대상의 patch diff가 prompt 또는 retrieval context에 포함되지 않도록 제한 |
| 중복 데이터 제거 | 동일 코드·challenge·firmware version의 train/test 중복 방지 |
| Time Split | 특정 시점을 기준으로 KB와 평가 데이터 분리 |
| Prompt Logging | 모델 입력 context를 JSONL/OpenTelemetry trace로 기록 |
| Reproducibility | 동일 artifact·model·retrieval 조건에서 결과 재현 보장 |

## 6. 리스크 및 오픈 이슈

- **하드웨어 검증 이력 부재**: AI TOP ATOM(Blackwell sm_121)에서 vLLM의 GB10 전용 실행 지원은 2026년 6월부터 시작되었고 NVFP4 KV cache·speculative decoding 등은 커뮤니티 fork 수준에 머무는 경우가 있어, 배포 전 이 장비에서 vLLM 버전·커널 호환성을 직접 검증해야 한다. (담당자 추후 협의)
- **Critic 모델의 양자화 품질 저하 가능성** *(2026-09-09 — 전제가 사라졌다. 이 항목의 상태 원천은 [R2](open-questions/R/R2.md)이고, 새 Critic의 미검증 항목은 [R8](open-questions/R/R8.md)이 든다. 아래는 그 시점의 기록이다.)*: Devstral Small 2는 dense 모델이라 대역폭 병목의 영향을 크게 받는다. NVFP4 적용 시 critic 특유의 반박 정확도가 떨어지는지 별도 검증이 필요하다. (담당자 추후 협의)
- **단일 노드 확장 한계**: 2대 연결(ConnectX-7) 확장은 benchmark로 병목이 확인되기 전까지 보류한다. 단일 노드 성능 한계 파악이 그 판단의 전제다. (담당자 추후 협의)
- **평가 데이터 누출 검증 부담**: KB와 평가셋 분리, CVE ID Masking, Patch Leakage 방지를 코드 리뷰만으로 보장하기 어려우므로 자동 검증 스크립트가 필요하다. (담당자 추후 협의)
- **Zero-day 제보 프로세스**: 최소 2인 검증 및 AI 생성물 그대로 제출 금지 원칙을 실제 워크플로에 어떻게 강제할지(체크리스트, 승인 절차)는 아직 정의되지 않았다.

## 참고 자료

- [Microsoft Codename MDASH FAQ](https://learn.microsoft.com/en-us/security-exposure-management/codename-mdash-faq)
- [Microsoft MDASH 발표](https://www.microsoft.com/en-us/security/blog/2026/05/12/defense-at-ai-speed-microsofts-new-multi-model-agentic-security-system-tops-leading-industry-benchmark/)
- [Theori RoboDuck 공개 저장소](https://github.com/theori-io/aixcc-afc-archive)
- [RoboDuck CRS Architecture](https://github.com/theori-io/aixcc-afc-archive/blob/main/docs/crs-architecture.md)
- [Trail of Bits Buttercup](https://github.com/trailofbits/buttercup)
- [Team Atlanta Atlantis](https://github.com/Team-Atlanta/aixcc-afc-atlantis)
- [OpenSSF OSS-CRS 제안](https://github.com/ossf/tac/blob/main/process/project-lifecycle-documents/oss-crs_sandbox_stage.md)
- [Qwen3-Coder-30B-A3B](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct)
- [Qwen3 Embedding and Reranker](https://qwenlm.github.io/blog/qwen3-embedding/)
- [Mistral Devstral 2](https://mistral.ai/news/devstral-2-vibe-cli/)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LiteLLM](https://docs.litellm.ai/)
- [vLLM OpenAI-Compatible Server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
- [Langfuse Token and Cost Tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking)
