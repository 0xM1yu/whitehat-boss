# 모델 서빙과 Model Registry

## 1. 하드웨어: GIGABYTE AI TOP ATOM (NVIDIA GB10)

| 항목 | 값 |
| --- | --- |
| 메모리 | 128GB 통합 LPDDR5X (CPU·GPU 공유, 가용 112Gi) |
| 메모리 대역폭 | 273 GB/s |
| 연산 | 1 PFLOP (sparse FP4), 48 SM / 6,144 CUDA 코어 |
| CPU / 아키텍처 | 20코어 Arm (Cortex-X925 ×10 + A725 ×10) / Blackwell sm_121, CUDA 13.0 |
| 확장 | ConnectX-7 — 2대 연결 시 FP4 기준 최대 405B급 |

**핵심 특성: 용량은 넉넉하지만 대역폭이 좁다.** 토큰마다 읽어야 하는 바이트가 곧 병목이다. 따라서

- 한 사용자가 대형 모델의 긴 응답을 기다리는 용도에는 불리하다.
- 여러 요청을 동시에 처리하는 멀티 에이전트 구성에는 유리하다.
- MoE(활성 파라미터가 작은 모델)가 같은 총 파라미터의 dense 모델보다 유리하다.

이 특성이 아키텍처를 규정한다. Scout·Embedding은 크게 배칭하고, Auditor·Critic은 후보가 좁혀진 뒤에만 호출한다.

## 2. 초기 모델 구성

| 역할 | 모델 | 선정 이유 | 운영 방식 | 적재(추정) |
| --- | --- | --- | --- | --- |
| Embedding | Qwen3-Embedding-0.6B | Apache 2.0, 32K context, code retrieval 지원 | 상시 상주 | 예산 영향 미미 |
| Reranker | Qwen3-Reranker-0.6B | 상동 | 상시 상주 | 예산 영향 미미 |
| Scout / Triage | Qwen3-8B | Apache 2.0, 128K context. 구조화 분류·요약용 | 짧은 출력, thinking 최소, 높은 병렬성 | FP4 약 4GB (추정) |
| Primary Auditor | Qwen3-Coder-30B-A3B-Instruct | 30.5B total / 3.3B active MoE, 256K native context. 좁은 대역폭에서 총 파라미터 대비 유리 | 선별 후보에만 호출 | NVFP4 약 15GB (추정) |
| Independent Critic | Devstral Small 2 24B | Primary와 **다른 계열**, Apache 2.0, 256K context, tool-use 지원 | 상위 후보만 batch 검증 | dense 24B — 대역폭 영향 큼 |

Critic이 다른 계열이어야 하는 이유: 같은 모델의 self-consistency는 같은 실패를 반복한다. 교차 검증의 가치는 오류 상관관계가 낮을 때만 나온다.

실제 메모리 점유는 양자화·KV cache·context 길이·동시 요청 수에 크게 좌우된다. **모델을 확정하기 전에 이 장비에서 8K/16K/32K context 기준으로 직접 측정한다.**

## 3. 배포 전 필수 검증 (블로킹)

AI TOP ATOM은 Blackwell sm_121이다. vLLM의 DGX Spark(GB10) 전용 실행 레시피 공식 지원은 2026년 6월부터이고, NVFP4 KV cache·speculative decoding 같은 최적화는 커뮤니티 fork 수준에 머무는 경우가 있다. **이 장비에서 직접 검증하기 전까지 어떤 성능 수치도 계획에 반영하지 않는다.**

검증 체크리스트 (각 항목 뒤는 `python -m src.cli bench`가 그 항목을 답하는 출력 필드다):

- [ ] vLLM 버전 × CUDA 13.0 × sm_121 커널 호환성 (기동 성공 여부) → `roles.*.startup.ok`
- [ ] 역할별 모델 로드 성공 및 실제 통합메모리 점유 측정 → `roles.*.memory.mem_gb_ctx8k` (+ `nvidia_smi_mb_peak`)
- [ ] context 8K / 16K / 32K에서 KV cache 점유와 처리량 → `roles.*.memory.by_context.*.kv_cache_usage_perc_peak` (`vllm:kv_cache_usage_perc`)
- [ ] 단일 스트림 tok/s vs 배치(8/16/32 동시 요청) 합산 tok/s → `roles.*.throughput.tok_s_single` / `.by_concurrency.*`
- [ ] automatic prefix caching 동작 확인 및 적중률 → `roles.*.prefix_cache.verdict` / `.metrics_delta` (장부가 비어도 `prompt_tokens_cached_delta`가 토큰 수를 준다 — 구간 합계라 BudgetLedger에는 넣지 않는다)
- [ ] structured output(JSON schema 강제) 동작 확인 → `roles.*.structured_output.valid_ratio`
- [ ] NVFP4 양자화 시 Critic 반박 정확도 저하 여부 → §4 — **bench 범위 밖**(고정 반박 표본 필요). 출력에는 `unmeasured(데이터셋 대기)`로만 뜬다

`bench` 출력의 `checklist` 배열이 위 7개를 통과/실패/미측정으로 요약한다. **못 잰 값은 0으로
남기고 사유를 함께 낸다** — 추정치를 넣으면 `Benchmark.is_measured`가 0 여부로 판정하므로
미측정 모델이 production 프로필에 오르는 길이 열린다.

**structured output이 ATOM에서 깨진다면 첫 번째 용의자 — reasoning 토큰.**

2026-08-14 Seraph 실측 응답(`tests/fixtures/metrics/vllm_0191_chat_completion.json`)은
`response_format` 없이 나간 호출이었고, 모델이 **`<think>` 블록을 먼저 내놓았다**
(`finish_reason: "length"`로 잘렸다). Qwen3 계열은 기본적으로 reasoning 토큰을 낸다.

guided decoding이 그것을 억제하지 못하면 `schema.model_validate_json(raw)`가 **첫 글자에서**
깨진다. 2026-08-11 검증(V1)에서 5회 전부 유효 JSON이 나왔으므로 그 스택에서는 억제되고
있지만, **ATOM은 vLLM 버전도 모델도 다르다.** bench의 B 항목이 ATOM에서 실패하면
`chat_template_kwargs`의 `enable_thinking` 계열부터 확인한다. 재현되지 않은 문제이므로
코드는 지금 고치지 않는다.

**측정 순서가 결과를 바꾼다.** structured output을 먼저 돌리면 그 시점에 prefix cache가
데워지므로, 적중률은 `/metrics` 카운터의 **구간 delta**로 잰다. 실제 순서는 출력의
`measurement_order`에 남는다.

측정 결과는 `docs/decisions/`에 ADR로 남기고, 수치는 Model Registry의 `benchmark` 항목에
`bench --apply`로 기록한다. `--apply`는 해당 라인만 갈아 끼우며 `models.yaml`의 주석을
보존한다 — 대상 블록을 확실히 찾지 못하면 아무것도 쓰지 않고 실패한다.

## 4. 양자화 품질 검증 (Critic 전용)

Devstral Small 2는 dense 모델이라 대역폭 병목의 영향을 크게 받아 NVFP4가 매력적이다. 그러나 critic의 가치는 **반박 정확도**에 있고, 정밀도를 낮췄을 때 이 능력이 먼저 무너질 수 있다.

검증 방법: 고정 반박 표본(정답이 알려진 finding — 진짜 취약점 N건 + 명백한 오탐 N건)에 대해 고정밀도 대비 NVFP4의 `verdict` 일치율과 `rebuttal_axis` 정확도를 비교한다. 일치율 하락이 기준치를 넘으면 Critic만 고정밀도로 운용한다.

표본 크기와 허용 하락폭은 [open-questions.md](open-questions.md)에서 확정한다.

## 5. Model Registry

역할과 모델을 코드에서 분리한다. 프로필 교체만으로 B1/B1'/B3/B3' 조건을 구성할 수 있어야 한다.

**registry와 프로필은 다른 파일이다.** 모델 목록은 장비가 바뀔 때, 프로필은 실험 조건이
바뀔 때 손대므로 수명이 다르다. 한 파일에 두면 조건 하나를 추가할 때 registry 전체가
diff에 올라온다.

```yaml
# config/models.yaml (합성 예시)
models:
  qwen3-8b:
    endpoint: http://127.0.0.1:8001/v1
    context_limit: 131072
    quantization: fp4
    license: Apache-2.0
    checksum: "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    prompt_template_version: v1
    fallback: null
    benchmark:
      measured_at: "2026-08-17T00:00:00Z"
      tok_s_single: 0
      tok_s_batch32: 0
      mem_gb_ctx8k: 0
```

```yaml
# config/profiles.yaml (합성 예시)
profiles:
  local_balanced:
    description: "전 역할 실서빙"
    production: false
    roles:                        # 역할 → models.yaml의 키
      scout: qwen3-8b
      auditor: qwen3-coder-30b-a3b
      critic: devstral-small-2-24b
      embedding: qwen3-embedding-0.6b
      reranker: qwen3-reranker-0.6b
    adapters:                     # ModelClient는 여기서 고르지 않는다 (아래 참조)
      retriever: fake
      tool_runner: fake
      state_store: sqlite
    budget: default               # budgets.yaml의 키
```

`benchmark` 항목이 비어 있는(`0`) 모델은 프로덕션 프로필에 넣지 않는다.

**`mem_gb_ctx8k`가 뜻하는 것.** "8K 요청 한 건의 점유"가 아니라 **8K context 요청이 떠
있는 동안 관측된 카드 점유**다. vLLM은 기동 시 `gpu_memory_utilization` 비율만큼 메모리를
선점하고 가중치를 뺀 나머지를 KV cache로 잡으므로, 이 값은 8K를 처리하든 32K를 처리하든
거의 같게 나온다 — **8K와 32K의 값이 같다고 측정 실패가 아니다.** context에 따라 실제로
변하는 것은 `vllm:kv_cache_usage_perc`이고, 체크리스트 3번은 그 수치로 답한다. 이 값은 counter가
아니라 **gauge**여서 요청이 끝나면 0.0으로 돌아가므로, bench는 요청이 떠 있는 동안 폴링해
peak을 잡는다(2026-08-14 Seraph 실측 원문에서 사후 스크레이프 값이 0.0이었다).

같은 이유로 이 값은 `gpu_memory_utilization`과 **함께 있어야만 의미가 있다.** 그 비율이
다르면 같은 카드·같은 모델에서도 점유가 통째로 달라져 다른 사람이 재현할 수 없다. 그래서
`bench --apply`는 그 비율을 확인하지 못하면(`vllm:cache_config_info` 라벨 부재 등) 이
필드를 registry에 쓰지 않는다.

**`adapters`에 ModelClient가 없는 것은 의도다.** 역할마다 다른 모델을 쓰고 그중 일부만
실서빙일 수 있으므로(12GB 프로필은 scout만 실서빙), ModelClient 선택은 프로필이 아니라
각 모델의 `endpoint` 스킴이 역할별로 한다. 프로필 단위로 하나를 고르는 구조였다면 그
구성을 표현할 수 없다.

`prompt_template_version`은 **결과에 그대로 박제된다**(`RunRecord.prompt_versions`).
버전이 바뀌면 이전 실행 결과와 직접 비교하지 않는다(agent-contracts.md §4).

## 6. 게이트웨이 구성

```
Orchestrator → ModelClient 인터페이스 → LiteLLM (routing / fallback / budget)
                                          → vLLM #1 (Scout)
                                          → vLLM #2 (Auditor)
                                          → vLLM #3 (Critic)
                                          → embedding / reranker
```

- vLLM: 온프레미스 inference server, OpenAI-compatible API 제공
- LiteLLM: 역할별 routing·retry·fallback, spend/budget 관리
- 도메인 로직은 `ModelClient` 인터페이스에만 의존한다([engineering-guide.md](engineering-guide.md)).

## 7. 확장 정책

1. **모델 추가는 역할별 benchmark에서 실제 이득이 확인될 때만** 허용한다. 에이전트를 무작정 늘리지 않고 repository shard와 candidate queue만 병렬화한다.
2. 2대 연결(ConnectX-7)은 단일 노드 benchmark에서 병목이 확인된 뒤에 검토한다. 확장이 열리면 Primary Auditor를 더 큰 코드 모델로 교체하거나 Critic을 별도 노드로 분리하는 경로가 생긴다.
3. 단일 노드 성능 한계는 중간 발표(2026-09-05) 전에 파악해야 일정에 반영할 수 있다.
</content>
