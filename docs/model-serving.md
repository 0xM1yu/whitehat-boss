# 모델 서빙과 Model Registry

## 1. 하드웨어: GIGABYTE AI TOP ATOM (NVIDIA GB10)

| 항목 | 값 |
| --- | --- |
| 메모리 | **PL이 34 → 64 GiB로 상향(2026-08-25 공식 확인)했고, 2026-08-31에 다시 80 GiB로 확장**됐다 — 공식 할당 = cgroup `memory.max` = **85,899,345,920 = 80 GiB**(2026-08-31 실측, 2026-09-01T04:39:13Z 읽기 전용 재확인에서 같은 값). `memory.high`는 **이번 확장과 별개로 8/25~8/26 사이에 58 GiB에서 `max`로 풀려 소프트 스로틀이 없다** — 8/25 두 보고서는 58을 실측했고 8/26 재측정·8/28 track_a가 `max`를 두 번 확인했다(정확히 언제 풀렸는지는 기록에 없다) — 감속 완충 없이 상한에서 바로 OOM이므로 **방어선을 직접 잡는다**(예: `max`의 0.9 ≈ 72 GiB). 통합 LPDDR5X, CPU·GPU 공유 — 장비 전체는 128GB지만 우리 슬라이스(`user-1004`)에 걸린 하드 한도가 **80**이다(이전 64는 2026-08-19·08-20 실측). 통합 메모리라 모델 가중치가 이 한도에 포함되고, 공용 계정이라 팀원과 **합산**된다. **하드 한도는 바뀔 수 있으니 실행 시점에 `memory.max`를 읽는다**(8/17엔 34였다가 상향). `free`·`nvidia-smi`·vLLM은 cgroup을 몰라 물리값(119.7 GiB)을 보고, `gpu_memory_utilization`의 분모도 그 물리값이다(다른 축). 이전 "가용 112Gi"는 장비 스펙에서 나온 **추정이었고 틀렸다**. 관측 기록은 [portability.md](portability.md) §6 |
| 메모리 대역폭 | 273 GB/s |
| 연산 | 1 PFLOP (sparse FP4), 48 SM / 6,144 CUDA 코어 |
| CPU / 아키텍처 | 20코어 Arm (Cortex-X925 ×10 + A725 ×10) / Blackwell sm_121, CUDA 13.0 |
| 확장 | ConnectX-7 — 2대 연결 시 FP4 기준 최대 405B급 |

**핵심 특성: 용량은 넉넉하지만 대역폭이 좁다.** 토큰마다 읽어야 하는 바이트가 곧 병목이다. 따라서

- 한 사용자가 대형 모델의 긴 응답을 기다리는 용도에는 불리하다.
- 여러 요청을 동시에 처리하는 멀티 에이전트 구성에는 유리하다.
- MoE(활성 파라미터가 작은 모델)가 같은 총 파라미터의 dense 모델보다 유리하다.

이 특성이 아키텍처를 규정한다. Scout·Embedding은 크게 배칭하고, Auditor·Critic은 후보가 좁혀진 뒤에만 호출한다.

## 2. 역할별 서빙 성질 — 초기 모델 구성 (2026-08 기록)

**어느 모델이 어느 자리에 앉는지의 정본은 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이다.** 아래 표는
**2026-08 초기안의 기록**이고, 로스터는 그 뒤 2026-09-09에 확정됐다
([ADR 0010](decisions/0010-harness-structure-and-model-roster.md) §2). **이 표를 「지금 무엇이
뜨는가」로 읽지 않는다** — 여기 남는 값은 그 시점의 선정 이유와, 그 뒤에 붙은 실측이다.

| 역할 | 모델 (2026-08 초기안) | 선정 이유 | 운영 방식 | 적재(추정) |
| --- | --- | --- | --- | --- |
| Embedding | Qwen3-Embedding-0.6B | Apache 2.0, 32K context, code retrieval 지원 | 상시 상주 | 예산 영향 미미 |
| Reranker | Qwen3-Reranker-0.6B | 상동 | 상시 상주 | 예산 영향 미미 |
| Scout / Triage | Qwen3-8B | Apache 2.0, 128K context. 구조화 분류·요약용 | 짧은 출력, thinking 최소, 높은 병렬성 | FP4 약 4GB (추정) |
| Primary Auditor | Qwen3-Coder-30B-A3B-Instruct | 30.5B total / 3.3B active MoE, 256K native context. 좁은 대역폭에서 총 파라미터 대비 유리 | 선별 후보에만 호출 | NVFP4 약 15GB (추정) |
| Independent Critic | Devstral Small 2 24B | Primary와 **다른 계열**, Apache 2.0, 256K context, tool-use 지원. **멀티모달(vision)이다** — config.json이 `Mistral3ForConditionalGeneration`이고 vLLM이 `PixtralForConditionalGeneration`으로 해석한다(2026-09-04 ATOM 실측, [보고서](experiments/harness/2026-09-04-five-model-residency-atom.md) §7) | 상위 후보만 batch 검증 | dense 24B — 대역폭 영향 큼. **이 칸에 추정이 아닌 값이 하나 생겼다**: 우리가 실제로 띄운 체크포인트는 `nvfp4`가 아니라 **`fp8`**이고 가중치+non-torch가 **25.48 GiB**(util 0.30)~**26.8 GiB**(util 0.45)다 — 2026-09-04 ATOM, vLLM 기동 로그의 자기 회계. 나머지 칸은 여전히 추정이다 |

Critic이 다른 계열이어야 하는 이유: 같은 모델의 self-consistency는 같은 실패를 반복한다. 교차 검증의 가치는 오류 상관관계가 낮을 때만 나온다. **이 요구는 어느 모델이 앉든 남는다** — [ADR 0010](decisions/0010-harness-structure-and-model-roster.md) §4가 auditor와 critic의 `family`(가중치 출처 조직)를 `load_config`가 검사하도록 정했다. **2026-09-09에 그것이 코드가 됐다** — `src/config.py`의 `_check_family_split`이 `load_config`에서 그 두 자리를 대조하고, **registry의 모든 항목이 `family`를 적는다**(§5). **개수는 여기서 세지 않는다** — 등재가 늘 때마다 조용히 낡는다([CLAUDE.md](../CLAUDE.md) §7). 세려면 `config/models.yaml`에서 센다. 그리고 [research/README.md](research/README.md)가 이 전제를 겨누는 남의 실측을 모아 뒀다 — 제작사를 다양화하면 오류 상관이 낮아진다는 보장이 그 데이터에는 없다.

실제 메모리 점유는 양자화·KV cache·context 길이·동시 요청 수에 크게 좌우된다. **모델을 확정하기 전에 이 장비에서 8K/16K/32K context 기준으로 직접 측정한다.**

## 3. 배포 전 필수 검증 (블로킹)

AI TOP ATOM은 Blackwell sm_121이다. vLLM의 DGX Spark(GB10) 전용 실행 레시피 공식 지원은 2026년 6월부터이고, NVFP4 KV cache·speculative decoding 같은 최적화는 커뮤니티 fork 수준에 머무는 경우가 있다. **이 장비에서 직접 검증하기 전까지 어떤 성능 수치도 계획에 반영하지 않는다.**

**양자화 커널은 이 체크리스트의 1번보다 앞에 있다.** 2026-08-17 ATOM 실측: `qwen3-4b`(fp8)가
`VLLM_USE_DEEP_GEMM=0` 없이는 가중치 로딩 후처리에서 죽는다(`Unknown SF transformation`).
**수치가 나빠지는 것이 아니라 서버가 안 뜬다** — 기동 인자를 모르면 1번부터 답할 수 없다.
모델별 기동 조건은 `config/models.yaml`의 해당 항목 주석에 있고(⏵ 2026-09-24 — 위 `qwen3-4b` 항목은 로스터 밖 정리로 지웠다 — 원문은 git 이력), 환경 축으로는
[portability.md](portability.md) §5 #5다.

검증 체크리스트 (각 항목 뒤는 `python -m src.cli bench`가 그 항목을 답하는 출력 필드다):

- [ ] vLLM 버전 × 드라이버/CUDA × GPU 아키텍처 커널 호환성 (기동 성공 여부) → `roles.*.startup.ok`
- [ ] 역할별 모델 로드 성공 및 실제 통합메모리 점유 측정 → `roles.*.memory.mem_gb_ctx8k` (+ `nvidia_smi_mb_peak`)
- [ ] context 8K / 16K / 32K에서 KV cache 점유와 처리량 → `roles.*.memory.by_context.*.kv_cache_usage_perc_peak` (`vllm:kv_cache_usage_perc`)
- [ ] 단일 스트림 tok/s vs 배치(8/16/32 동시 요청) 합산 tok/s → `roles.*.throughput.tok_s_single` / `.by_concurrency.*`
- [ ] automatic prefix caching 동작 확인 및 적중률 → `roles.*.prefix_cache.verdict` / `.metrics_delta` (장부가 비어도 `prompt_tokens_cached_delta`가 토큰 수를 준다 — 구간 합계라 BudgetLedger에는 넣지 않는다)
- [ ] structured output(JSON schema 강제) 동작 확인 → `roles.*.structured_output.valid_ratio`
- [ ] NVFP4 양자화 시 Critic 반박 정확도 저하 여부 → §4 — **bench 범위 밖**(고정 반박 표본 필요). 출력에는 `unmeasured(데이터셋 대기)`로만 뜬다. ⚠ **이 항목이 묻던 질문은 2026-09-09에 성립을 잃었다 — §4 머리와 [D4](open-questions/D/D4.md)를 먼저 읽는다.** ~~문구는 `src/bench_report.py`의 `CHECKLIST[6]`과 같은 것이고 **코드는 아직 안 고쳤으므로 `bench` 출력에도 이 문장이 그대로 뜬다**~~ → ⏵ **2026-09-24 — 코드는 고쳐져 있다.** 이 줄과 같은 커밋 `184aade`(2026-09-11)가 `CHECKLIST[6]`을 「Critic 반박 정확도 표본 (#93 재정의 — 고정밀 대비가 아니다)」로 바꿨고 `bench` 출력도 그 문장이다. 가중치는 bf16 그대로 쓰기로 해(@danielcho02) NVFP4 대비라는 물음은 지금 로스터에 대상이 없다

`bench` 출력의 `checklist` 배열이 위 7개를 통과/실패/미측정으로 요약한다. **못 잰 값은 0으로
남기고 사유를 함께 낸다** — 추정치를 넣으면 `Benchmark.is_measured`가 0 여부로 판정하므로
미측정 모델이 production 프로필에 오르는 길이 열린다.

**structured output이 ATOM에서 깨진다면 첫 번째 용의자 — reasoning 토큰.**

2026-08-14 Slurm 실측 응답(`tests/fixtures/metrics/vllm_0191_chat_completion.json`)은
`response_format` 없이 나간 호출이었고, 모델이 **`<think>` 블록을 먼저 내놓았다**
(`finish_reason: "length"`로 잘렸다). Qwen3 계열은 기본적으로 reasoning 토큰을 낸다.

guided decoding이 그것을 억제하지 못하면 `schema.model_validate_json(raw)`가 **첫 글자에서**
깨진다. 2026-08-11 검증(V1)에서 5회 전부 유효 JSON이 나왔으므로 그 스택에서는 억제되고
있지만, **ATOM은 vLLM 버전도 모델도 다르다.** bench의 B 항목이 ATOM에서 실패하면
`chat_template_kwargs`의 `enable_thinking` 계열부터 확인한다. 재현되지 않은 문제이므로
코드는 지금 고치지 않는다.

⏵ **2026-09-24 — ATOM(vLLM 0.27.1)에서 쟀다**([스모크 보고서](experiments/harness/2026-09-24-roster-smoke-atom.md) §3).
reasoning parser 없이 띄우면 구조화 출력 문법이 첫 토큰부터 걸려 모델이 **사고 없이** 답하고 JSON은 깨지지 않는다
(`qwen3.6-35b-a3b` `Finding` 36/36). parser를 걸면 문법이 사고가 끝난 뒤에야 걸리는데, 그 판에서는 사고가 `output_max`
4,096 안에 안 끝나 7/7이 실패했다(본문 `None` 6 · 잘린 JSON 1 — 그 판은 `xgrammar`·`disable_any_whitespace`도 없어
인자 셋이 함께 달랐다). 그래서 위 「첫 글자에서 깨진다」는 그대로 재현되지 않았고, 이 장비에서 관측된 실패는
**사고 길이** 쪽이다. 하네스는
`chat_template_kwargs`를 안 보낸다 — 모델별 기동 조건과 사고를 끄는 서버 인자는 `config/models.yaml`의 그 항목이,
사고를 켜는 배선 방식(Auditor parser `qwen3` + `thinking_token_budget`)은 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §5
「모델 배선」 행이 든다 — 아직 배선되지 않았다. ⏵ **2026-09-26 — Auditor가 `qwen3.8-27b`로 바뀌며 사고를 켰다** — registry가 `reasoning_effort: xhigh`와 사고 상한을 보내고, 기동 조건에 `--reasoning-parser qwen3`가 들어갔다(`config/models.yaml`의 그 항목 — 계획값). 우리 장비에서 사고 켠 Auditor를 띄운 실측은 0건이다.

**측정 순서가 결과를 바꾼다.** structured output을 먼저 돌리면 그 시점에 prefix cache가
데워지므로, 적중률은 `/metrics` 카운터의 **구간 delta**로 잰다. 실제 순서는 출력의
`measurement_order`에 남는다.

측정 결과는 `docs/decisions/`에 ADR로 남기고(첫 사례: [decisions/0007](decisions/0007-atom-qwen3-4b-first-measurement.md) — ATOM `qwen3-4b`), 수치는 Model Registry의 `benchmark` 항목에
`bench --apply`로 기록한다. `--apply`는 해당 라인만 갈아 끼우며 `models.yaml`의 주석을
보존한다 — 대상 블록을 확실히 찾지 못하면 아무것도 쓰지 않고 실패한다.

## 4. 양자화 품질 검증 (Critic 전용) — **전제가 사라진 절**

> **⚠ 이 절은 Critic이 Devstral Small 2였을 때 쓰였고, 그 전제가 2026-09-09에 사라졌다.**
> 로스터가 확정되며 Critic 자리가 바뀌었다 — **그 자리의 정본은 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1**이고
> 결정 기록은 [ADR 0010](decisions/0010-harness-structure-and-model-roster.md) §2다.
>
> **무엇이 무너졌나.** 아래 본문이 요구하는 것은 *"고정밀도 대비 NVFP4의 `verdict` 일치율"*인데,
> 새 Critic 자리의 모델은 **네이티브 MXFP4**라 공개된 고정밀 체크포인트가 없다 — **비교할
> 분모가 원리상 없다.** 근거는 [D4](open-questions/D/D4.md)의 2026-09-08 절과
> [2026-09-08 Critic 양자화 조사](research/2026-09-08-critic-quantization.md) §B4다.
>
> **무엇이 대신 서는가.** D4가 질문을 **「고정밀 대비 하락폭」에서 「반박 정확도 표본 자체」로
> 재정의**했다 — 진짜 취약점 N건 + 명백한 오탐 N건을 몇 건으로 하고 합격선을 어디에 둘
> 것인가. **표본 크기와 합격선의 정본은 D4다.**
> ⏵ 2026-10-10 — D4는 닫혔다. 표본 크기·합격선은 벤치마크 설계라 실험팀 범위다([ADR 0012](decisions/0012-harness-canon-v2.md) 결정 5) — 이 저장소에 그 정본은 없다.
>
> **그래도 지우지 않는다.** §3 체크리스트 7번이 이 절을 이름으로 가리키고,
> [R2](open-questions/R/R2.md)가 이 질문 위에 서 있다 — R2는 **2026-09-09에 `닫힘`이 됐고**(⚠ 이 줄은 R2를 닫은 바로 그 커밋 `184aade`에서 *"아직 `열림`"*이라 적혔고 아흐레를 그대로 살았다 — 2026-09-20에 고쳤다)
> ADR 0010 「후속」 2가 *"R2는 로스터 변경으로 닫힌다"*고 적는다. **아래는 「지금 하라」가
> 아니라 「무엇을 물었던 절인가」로 읽는다.**

Devstral Small 2는 dense 모델이라 대역폭 병목의 영향을 크게 받아 NVFP4가 매력적이다. 그러나 critic의 가치는 **반박 정확도**에 있고, 정밀도를 낮췄을 때 이 능력이 먼저 무너질 수 있다.

검증 방법: 고정 반박 표본(정답이 알려진 finding — 진짜 취약점 N건 + 명백한 오탐 N건)에 대해 고정밀도 대비 NVFP4의 `verdict` 일치율과 `rebuttal_axis` 정확도를 비교한다. 일치율 하락이 기준치를 넘으면 Critic만 고정밀도로 운용한다.

> **2026-09-04 — 이 비교의 「고정밀도」 쪽이 지금 손에 없다.** ATOM에서 실제로 띄운
> `devstral-small-2-24b` 체크포인트의 양자화는 **`fp8`이고 NVFP4가 아니다**
> ([보고서](experiments/harness/2026-09-04-five-model-residency-atom.md) §7 — `config.json`의
> `quant_method: 'fp8'`, vLLM 기동 로그 `quantization=fp8`, 파일 크기 24.0 GiB).
> registry가 `nvfp4`로 적고 있던 것을 그 실측으로 고쳤다. **그러므로 위 비교를 돌리려면
> 기준선을 무엇으로 둘지부터 정해야 한다** — 지금 있는 것은 저정밀도 축의 한 점이지
> 기준선이 아니다. NVFP4 빌드(`Firworks/…-nvfp4`)는 이름만 확인됐고 받지도 띄우지도 않았다.

표본 크기와 허용 하락폭은 [open-questions.md](open-questions.md)에서 확정한다. **⚠ 재정의
뒤의 정본은 [D4](open-questions/D/D4.md)이고, 거기서 정하는 것은 「허용 하락폭」이 아니라
「표본 크기와 합격선」이다.** ⏵ 2026-10-10 — D4가 닫히며 그 판단은 실험팀 범위로 넘어갔다(위 머리 블록).

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
    family: qwen                 # 베이스 가중치의 제작사. 양자화한 조직이 아니다
    prompt_template_version: v1
    fallback: null
    benchmark:
      measured_at: "2026-08-17T00:00:00Z"
      measured_on: "atom"        # 어느 장비에서 쟀는가. null이면 장비 미상
      tok_s_single: 0
      tok_s_batch32: 0
      mem_gb_ctx8k: 0
      prefix_hit_ratio_batch32: null   # 위 batch32가 어떤 캐시 상태에서 나왔는가
```

**`prefix_hit_ratio_batch32`는 실측 항목이 아니다** — `Benchmark.MEASUREMENTS` 셋(`tok_s_single`·
`tok_s_batch32`·`mem_gb_ctx8k`)에 들어가지 않으므로 이 값이 없다고 「부분 측정」이 되지 않는다.
`tok_s_batch32`가 **몇 번째 판이냐의 함수**라는 것이 2026-09-02 ATOM에서 실측으로 드러나
([보고서](experiments/harness/2026-09-02-bench-concurrency-atom.md)), 그 값 옆에 조건을 적을
칸으로 뒀다. **`null`은 미판독이고 `0.0`은 「캐시를 끄고 쟀다」라는 실측이다 — 둘은 다르다.**

**`family`가 §2의 「Primary와 다른 계열」을 검사 가능하게 만든다** ([ADR 0010](decisions/0010-harness-structure-and-model-roster.md) §4).
그 요구는 여기와 [evaluation.md](evaluation.md) §2.1과 critic 노드에 문장으로만 있었고 강제하는
코드가 없었다 — 이제 `load_config`가 **auditor와 critic이 다른 모델 키면 계열이 달라야 한다**를
보고, 같은 키(B1)는 정의상 허용한다. **값이 비어 있으면 통과가 아니라 실패다** — 판정할 근거가
없는 것이지 다른 것이 아니다.

**아래 예시의 모델 키는 구조를 보이려고 든 것이지 현재 로스터가 아니다** — 로스터의 정본은
[PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1이고, 실제로 배선된 값은 `config/profiles.yaml`이다.

```yaml
# config/profiles.yaml (합성 예시 — 모델 키는 자리표시자다)
profiles:
  local_balanced:
    description: "전 역할 실서빙"
    production: false
    roles:                        # 역할 → models.yaml의 키. 어느 키인지는 PROJECT-CONTEXT.md §1
      scout: <모델 키>
      auditor: <모델 키>
      critic: <모델 키>
      embedding: <모델 키>
      reranker: <모델 키>
    adapters:                     # ModelClient는 여기서 고르지 않는다 (아래 참조)
      retriever: fake
      tool_runner: fake
      state_store: sqlite
    budget: default               # budgets.yaml의 키
```

`benchmark` 항목이 비어 있는(`0`) 모델은 프로덕션 프로필에 넣지 않는다.

**registry의 `benchmark`는 `measured_on` 장비 기준이다.** 두 장비가 모두 지원 대상이므로
([portability.md](portability.md)) 같은 필드에 다른 하드웨어의 값이 들어갈 수 있고, 그 사실이
남지 않으면 다음 사람이 이 수치로 다른 장비를 예측한다. **다른 장비의 수치를 덮어쓰기
전에 그 사실을 인지해야 한다** — `bench --apply`는 막지 않고 기록만 하며(ADR 0005의
"금지가 아니라 기록"과 같은 논법), 현재 장비와 `measured_on`이 다르면 `doctor`가
경고한다. 장비를 지정하지 않고 `--apply`하면 `measured_on`이 `null`로 적힌다 — 추측한
호스트명으로 메우지 않는다.

**`mem_gb_ctx8k`가 뜻하는 것.** "8K 요청 한 건의 점유"가 아니라 **8K context 요청이 떠
있는 동안 관측된 카드 점유**다. vLLM은 기동 시 `gpu_memory_utilization` 비율만큼 메모리를
선점하고 가중치를 뺀 나머지를 KV cache로 잡으므로, 이 값은 8K를 처리하든 32K를 처리하든
거의 같게 나온다 — **8K와 32K의 값이 같다고 측정 실패가 아니다.** context에 따라 실제로
변하는 것은 `vllm:kv_cache_usage_perc`이고, 체크리스트 3번은 그 수치로 답한다. 이 값은 counter가
아니라 **gauge**여서 요청이 끝나면 0.0으로 돌아가므로, bench는 요청이 떠 있는 동안 폴링해
peak을 잡는다(2026-08-14 Slurm 실측 원문에서 사후 스크레이프 값이 0.0이었다).

같은 이유로 이 값은 `gpu_memory_utilization`과 **함께 있어야만 의미가 있다.** 그 비율이
다르면 같은 카드·같은 모델에서도 점유가 통째로 달라져 다른 사람이 재현할 수 없다. 그래서
`bench --apply`는 그 비율을 확인하지 못하면(`vllm:cache_config_info` 라벨 부재 등) 이
필드를 registry에 쓰지 않는다.

**`adapters`에 ModelClient가 없는 것은 의도다.** 역할마다 다른 모델을 쓰고 그중 일부만
실서빙일 수 있으므로(~~12GB 프로필은 scout만 실서빙~~ → ⏵ 2026-09-24 — 그 예였던 `single_gpu_12g`는 지웠다. 그 모양은 지금 `tests/conftest.py`의 합성 config `served_scout_config`가 든다), ModelClient 선택은 프로필이 아니라
각 모델의 `endpoint` 스킴이 역할별로 한다. 프로필 단위로 하나를 고르는 구조였다면 그
구성을 표현할 수 없다.

`prompt_template_version`은 **결과에 그대로 박제된다**(`RunRecord.prompt_versions`).
버전이 바뀌면 이전 실행 결과와 직접 비교하지 않는다(agent-contracts.md §4).

### checksum을 어떻게 산출하는가

`checksum`은 **model·tokenizer·quantization 묶음**의 해시다. 실서빙 역할의 이 값이
자리표시자(전부 0)면 그 run은 비교표에서 제외되고(`incomplete_fields`), `doctor`가 run
전에 미리 경고한다.

**정본은 로컬 스냅샷 전 파일의 sha256을 모은 manifest의 sha256이다.** 산출은
`tools/model_checksum.py`가 한다 — 손으로 맞추면 정렬·개행·제외 규칙 중 하나만 어긋나도
**조용히 다른 값**이 나온다.

```bash
python tools/model_checksum.py <스냅샷 경로> --key qwen3-14b \
    --source hf://Qwen/Qwen3-14B@<revision>
```

출력의 `checksum:`·`source:` 두 줄을 `models.yaml`에 **사람이 붙여넣는다.**
`bench --apply`와 달리 자동으로 쓰지 않는다 — 값을 채우는 것은 다운로드가 끝난 뒤의
판단이고, 그 판단까지 자동화할 이유가 없다.

| 규칙 | 내용 | 왜 |
| --- | --- | --- |
| 대상 | 스냅샷의 **모든 파일**. 제외는 짧은 denylist(`*.md`·`LICENSE*`·`.gitattributes`·이미지·`.cache/`)뿐 | **비대칭.** allowlist가 새 가중치를 놓치면 모델이 바뀌었는데 checksum이 그대로다(조용한 거짓). denylist가 문서 파일을 놓치면 값이 헛되이 바뀔 뿐이다(시끄러운 참). `repo_dirty`가 미추적 파일까지 dirty로 세는 것과 같은 판단 |
| 정렬 | 스냅샷 루트 기준 **POSIX 상대경로** 순 | 구분자를 `/`로 고정해야 Windows에서 만들어도 같은 값이 나온다 |
| 형식 | `<파일 sha256>  <상대경로>`, UTF-8·**LF** | `sha256sum` 호환이라 스냅샷에서 바로 `sha256sum -c`로 검증된다 |
| 심볼릭 링크 | **파일 링크는 따라가고, 디렉터리 링크는 따라가지 않는다**(무시한 사실은 사유로 출력) | HF 캐시의 스냅샷은 `blobs/`로 가는 파일 링크 모음이라 따라가지 않으면 해시할 내용이 없다. 디렉터리 링크는 순환하거나 스냅샷 밖 트리를 끌어들인다 |
| manifest | `config/checksums/<model key>.txt`에 **커밋한다** | 값이 `config/models.yaml`에 있으므로 근거도 `config/` 안에 있어야 갈라지지 않는다. 값 하나만 남기면 달라졌을 때 **어느 파일이** 달라졌는지 알 수 없다 |

**HF Hub 메타데이터는 정본이 아니다.** `siblings[].lfs.sha256`은 **LFS 추적 파일만**
덮는다 — `config.json`·`tokenizer_config.json` 같은 작은 파일은 일반 git blob이라
`blobId`(git SHA-1)만 오는데, 하필 그쪽이 양자화 설정과 tokenizer 설정이다. 즉 "묶음"의
절반이 빠진다. HF 조회는 **받기 전 예비 확인**으로만 쓴다.

2026-08-17 API 확인(`GET /api/models/Qwen/Qwen3-0.6B?blobs=true`): 파일 10개 중 `lfs`가
붙은 것은 `model.safetensors`·`tokenizer.json` **둘뿐**이고, `config.json`·
`generation_config.json`·`tokenizer_config.json`·`merges.txt`·`vocab.json`은 전부
`blobId`+`size`만 온다.

`source`는 그 스냅샷이 어디서 왔는지다(`hf://org/repo@rev` 또는 `file:///경로`). 후보가
전부 커뮤니티·서드파티 산출물이라(2026-08-16 조사: Auditor NVFP4만 세 벌) **같은 이름의
모델이 리포지터리마다 다르다**(⏵ 2026-09-24 — 가중치를 bf16 원본 그대로 쓰기로 해(@danielcho02) 지금 로스터에서 서드파티
산출물은 `seccoderx-8b`(`hf://SecCoderX/…`) 하나이고 나머지는 제작사 리포다 — `config/models.yaml`의 `source`. 리비전을
적어야 하는 이유는 그대로다. ⏵ 2026-10-14 — 그 키가 로스터에서 내려왔다. **지금 제작사 리포가 아닌 것은
`feyospace-v1-small`**(`hf://feyospace/…` — Qwen3.8-27B를 post-train한 것) **하나다** — 나머지 다섯은
`hf://Qwen/…`·`hf://openai/…`다) — checksum이 안 맞을 때 이 필드가 없으면 어느 리포의 어느
리비전이었는지 답할 수 없다. 형식은 강제하지 않는다(ADR 0006이 `--device` 라벨 집합을
열어 둔 것과 같은 판단).

## 6. 게이트웨이 구성

```
Orchestrator → ModelClient 인터페이스 → LiteLLM (routing / fallback / budget)
                                          → vLLM (Scout — 풀이면 역할 셋)
                                          → vLLM (Auditor)
                                          → vLLM (Critic)
                                          → embedding / reranker
```

**위 그림은 역할의 배선이지 상주 서버 수가 아니다.** 같은 모델이 앉은 역할들(Scout 셋 — `qwen3-14b` — 이 그 예다)은 **한 서버를
공유하고**, 어느 자리가 그런지는 [PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §1·§2가 든다.

> ### ⚠ 공유는 공짜가 아니다 — **한 서버가 두 이름에 답해야 한다** (2026-09-20)
>
> 하네스는 **registry 키를 그대로** 요청 본문의 `model`에 싣는다 — `src/adapters/factory.py`의
> `_model_client()`가 `model_name=model_key`로 만들고 `src/adapters/openai_compat.py`가
> `"model": self.model_name`으로 보낸다. **역할 이름이 아니라 registry 키다.**
>
> **그래서 포트 하나가 여러 이름에 답해야 할 수 있다 — 그 이름 집합은 프로필마다 다르다.**
> 아래는 `config/profiles.yaml`·`config/models.yaml`을 파싱해 낸 것이다(개수를 손으로 세지 않는다):
>
> | 포트 | 어느 프로필이 어느 이름을 보내나 |
> | --- | --- |
> | **8000** | `qwen3-14b` ← 풀 둘(scout_memory·scout_boundary·scout_injection) |
> | **8001** | `qwen3.8-27b` ← `harness_pool`·`_hybrid`(auditor) |
> | **8002** | `gpt-oss-20b` ← `harness_pool`·`_hybrid`(critic) |
> | **8003** | `qwen3-embedding-0.6b` ← `harness_pool_hybrid`(embedding — `retriever: hybrid`일 때만 뜬다) |
> | **8004** | `qwen3-reranker-4b` ← `harness_pool_hybrid`(reranker — 같은 조건) |
> | **8005** | `feyospace-v1-small` ← `harness_pool`·`_hybrid`(pov — 8단계 · 서빙 호스트 미확보라 실측 전) |
>
> ⏵ **2026-10-14 — 번호가 한 칸씩 당겨졌다.** `scout_boundary`가 `qwen3-14b`로 옮겨 Scout 단계의 키가
> 둘에서 하나가 되었고(`seccoderx-8b`는 로스터에서 내려와 registry에서 지웠다 — PROJECT-CONTEXT.md §1),
> 아래 「빈틈없이」 규칙이 뒤 단계를 전부 한 칸 당겼다. 그 전 번호는 8000=`seccoderx-8b` …
> 8006=`feyospace-v1-small`이었다 — 이 날짜 앞의 보고서가 드는 번호는 그 판이다.
>
> **번호가 곧 띄우는 순서다 (2026-09-24).** 교대가 canonical run에서 모델을 올리는 순서(Scout 풀 → Auditor →
> Critic)대로 8000부터 매겼고, hybrid에서만 뜨는 검색 둘을 그 뒤에, 8단계 PoV(`feyospace-v1-small`)를 그 뒤에 뒀다. 그 전에는 로스터를 바꿀 때마다
> 빈 번호에 끼워 넣어 8009 → 8010 → 8008 → 8006 순서로 떴다. `tests/test_config.py`의
> `test_served_ports_count_up_from_8000_in_launch_order`가 이 순서를, `tests/test_docs_format.py`의
> `test_the_model_serving_port_table_matches_the_registry`가 이 표와 registry의 일치를 고정한다.
>
> **2026-09-24 로스터 밖 정리 뒤로는 포트마다 이름이 하나다.** 그 전에는 8001(`qwen3-8b`·`qwen3-4b`)·
> 8002(`qwen3-coder-30b-a3b`)처럼 프로필에 따라 다른 이름이 같은 포트로 오는 자리가 있었다(그 키·프로필을
> 지웠다). **한 서버가 동시에 두 이름을 받아야 하는 자리는 지금 없다.**
>
> ⚠ **한 프로필 안에서 한 포트로 두 모델이 오는 자리도 지금 없다 — 있으면 「주의」가 아니라
> 블로커다.** 확정 로스터 배선 때 `seccoderx-8b`(Scout)가 `gpt-oss-20b`(Critic)와 같은 :8006이었는데
> (#234 골격 포트) :8009로 갈랐다(#234 리뷰 HIGH-2 — ⏵ 2026-10-14 그 Scout 키는 로스터에서 내려왔고
> Critic은 지금 위 표의 :8002다). 그 자리가 다시 생기면 — 한 프로필이 한 포트로
> 두 모델을 요구하면 — 프로필을 골라 피할 수 없다. `tests/test_docs_format.py`의
> `test_model_serving_calls_the_within_profile_port_clash_unavoidable`이 `config/`를 파싱해 그
> 자리를 세고, 있으면 그 포트 번호와 선언을 여기서 요구한다 — **지금은 0건이라 이 문단이 선언을
> 안 든다.**
>
> ⚠ **어떻게 한 서버가 여러 이름을 받게 하는지는 이 저장소가 모른다.** 기동 명령이 남은 자리를
> 전수로 훑으면 `--served-model-name`이 **32곳**인데 **이름을 둘 이상 준 곳이 0건**이고,
> vLLM이 그것을 받는지 확인한 기록도 없다. **그러니 여기 문법을 적지 않는다**
> ([CLAUDE.md](../CLAUDE.md) §0). **다음 기동 때 확인해 그 결과를 이 문단에 적는다** —
> 확인 전까지 이것은 **미검증 선행조건**이다. 이름이 어긋났을 때 서버가 정확히 무엇을
> 돌려주는지도 이 저장소에 **실측이 없다**(`src/bench.py`가 그 실패를 단언하지만 잰 기록은 아니다).
>
> ### 그런데 그것을 미리 잡는 검사가 이미 있다
>
> **`cli bench`가 역할별로 `GET /v1/models`를 읽어 registry 키가 그 목록에 있는지 본다** —
> `src/bench.py`의 `check_startup()`이고, 그 docstring이 이 함정을 그대로 적는다:
> *"`factory._model_client()`가 `model_name=model_key`를 그대로 실어 보내므로, 여기가 어긋나면
> 실제 run의 `/chat/completions`도 같은 이유로 실패한다."* 판정이 **멤버십**(`model_key not in served`)
> 이라 한 서버가 이름 여럿을 내도 통과한다. 호출부는 `src/bench_report.py`이고 **역할마다 돈다.**
>
> ⇒ **run 전에 `cli bench`를 그날의 프로필로 한 번 돌리는 것이 이 함정의 프리플라이트다.**
>
> (`cli preflight`(#534)는 이름이 겹치지만 다른 명령이다 — run 전 선결조건(터널·원격 커밋·원격 가중치 캐시·채점기·카나리·도구 이미지)을 보고 모델 서버에 요청은 안 보낸다. 이 함정의 프리플라이트는 여전히 `cli bench`다.)
>
> ⏵ **2026-09-29 — 교대 프로필은 run이 스스로 본다.** 교대마다 올린 키를 `check_startup()`으로 다시 읽어 이름이 없거나 `max_model_len`이 registry `context_limit`과 다르면 run을 멈춘다(`src/adapters/model_swap.py` — [ADR 0009](decisions/0009-stage-unit-execution-and-model-swap.md) 「2026-09-29 제안」 절, 제안). **교대를 선언하지 않은 프로필(서버를 사람이 띄운다)은 그대로 위 프리플라이트가 유일하다** — 교대를 선언한 프로필은 `harness_pool` 하나다(`harness_pool_hybrid`는 검색 역할 때문에 선언할 수 없다).
>
> ⏵ 2026-10-16 (#698 · #602) — **디코더 스키마 게이트도 run 전에 돈다.** 요청 스키마(Auditor v18 판별 합 · `tool` enum 축소 변형)를
> 서빙 vLLM의 xgrammar가 못 옮기면 그 좌석의 요청이 전부 HTTP 400이다. **서빙 박스**(vLLM이 쓰는 그 xgrammar가 깔린 곳)의 저장소
> 사본에서 `python tools/check_decoder_schemas.py <프로필>`이 `FAIL 0`이어야 run한다 — 마지막 줄이 xgrammar 버전을 찍는다.
> 오케스트레이터의 `doctor`도 같은 검사를 하지만 xgrammar가 없으면 WARN으로 건너뛰므로 그것으로 갈음하지 않는다.
> 이 게이트는 「변환된다」까지만 본다 — 모델이 그 문법으로 올바른 JSON을 내는지는 재지 않는다.
>
> ⚠ **`doctor`로는 안 된다** — 접속을 하지 않는다(`src/cli.py`의 `doctor()` docstring).
> 다만 **접속이 없어서가 아니다**: `_check_wiring`이 이미 역할별 endpoint를 조립하므로
> 「한 endpoint에 키가 둘」은 네트워크 없이도 볼 수 있다. **지금 그 검사가 없을 뿐이다.**

### 교대 명령과 장비 파일 (2026-09-29)

`harness_pool`은 단계 경계에서 서버를 끄고 켠다(콜드 재기동 — [ADR 0009](decisions/0009-stage-unit-execution-and-model-swap.md)
「2026-09-29 제안」 절, **제안**). **실장비 종단은 미실측이다** — ATOM에서 먼저 돈다. ⏵ 2026-09-29 — ATOM에서 한 번 돌았다([스모크 보고서](experiments/harness/2026-09-29-stage-swap-smoke-atom.md) — 교대 네 번 전부 `ok=true`, 슬라이스 1개·n=1·비교표 밖). vast는 미실측이다. ⏵ 2026-10-12 — 「vast는 미실측」도 낡았다: vast 교대 종단은 2026-10-03에 처음 성립했고(`config/profiles.yaml`의 ⏵ 2026-10-08), 기동 콜드 ↔ 따뜻한 쌍은 [10-11 보고서](experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md) §2가 든다.

- **교대 명령** — `tools/serve/swap.sh`, 장비 무관. 하네스가 단계마다 `bash tools/serve/swap.sh <키…>`를
  부르고 run 끝에 인자 없이 부른다. 계약(인자 · 환경 · 종료코드 · stdout 마지막 줄 JSON 보고 · 서버 로그)은
  `src/adapters/model_swap.py` 모듈 docstring이 든다. 기동마다 vLLM 출력을 새 파일
  `$SERVE_DIR/logs/<key>.<run_id>.<UTC>.log`로 받는다 — 아래 「서버 stdout은 파일로 받는다」 규약을 명령이 지킨다.
- **장비 파일** — `config/serving/<device>.yaml`이 모델별 util · `max_num_seqs` · 창 · 추가 인자 · 환경 · 준비
  상한을 든다. `run --device <device>`가 고르고, 창은 registry `context_limit` 그대로여야 교대 프로필의 run이
  시작한다(`check_serving`). 교대를 선언할 수 없는 `harness_pool_hybrid`는 이 점검 밖이다. 포트·체크포인트·
  revision은 registry가 든다.

  | 장비 | 메모리 판정 | 값의 성격 |
  | --- | --- | --- |
  | `atom`(GB10) | 통합메모리 — `nvidia-smi` 메모리 `[N/A]` → 모델 파일 페이지 캐시를 떼고 MemAvailable | 전부 계획값(ATOM 미실측) |
  | `vastai`(RTX PRO 6000 · 97,887 MiB) | 전용 VRAM — `total − used` ≥ util × total | Auditor 기동 하나만 실측(util 0.90 · 창 262,144 — 2026-09-29), 나머지 계획값 ⏵ 2026-10-12 — 「하나만」은 낡았다: `run_20261011_0001`이 다섯 좌석의 모델 넷(`qwen3-14b` · `seccoderx-8b` · `qwen3.8-27b` · `gpt-oss-20b` — 원격 로그 네 파일)을 vast에서 기동했다([10-11 보고서](experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md) §2 — util·창은 그 run의 장비 파일 값 그대로이고 메모리 판정값 자체는 안 적었다) |

- **감시자** — `tools/serve/guard.sh`, **run마다 하나 띄운다.** 교대 명령은 감시자가 떠 있고 다른 run 것이
  아닐 때만 띄우고(띄우는 run이 기동 직전마다 감시자를 자기 것으로 적는다), run 끝 내림이 그 run 서버를 다 내리면
  감시자를 멈춘다 — (마감을 켰다면) 끝난 run의 감시자 마감이 다음 run 서버를 죽이지 않게. 「떠 있다」는 pid 파일의 번호와 시작
  시각이 함께 맞는다는 것이다 — 번호만 든 옛 형식은 감시자가 아니다. 마감(`MAX_HOURS`)은 **기본 꺼짐**이고(⏵ 2026-10-15 사용자 결정 - 10시간 마감이 10-12 run을 8단계 중간에 끊었다)
  양의 정수를 주면 지키는 run의 서버만 내린다. **위험: 마감이 없으면 로컬 run이 run 끝 내림 없이 죽었을 때(PC 종료·Ctrl-C 뒤 내림 실패 등) 원격 서버가 GPU를 계속 잡는다 → 사람이 `HARNESS_RUN_ID=<run> bash tools/serve/swap.sh`로 내리고 vast 인스턴스를 지운다.** 메모리 두 층 — 통합메모리의 MemAvailable 하한, 그리고 cgroup 층(상한이 걸린 cgroup의 anon >
  상한의 90% — 옛 스모크 감시자의 「anon > 72 GiB」) — 은 장비 보호라 교대 명령이 띄운 서버를 run과 무관하게 전부
  내린다. cgroup 층의 판정은 장비 무관이지만 **자동 탐색은 cgroup v2만이다** — v1이면 `GUARD_CGROUP_DIR`로 시작
  디렉터리를 줘야 한다(v1은 가짜 트리로만 확인했다). 무엇을 보는지는 감시자 시작 로그의 `cgroup=`이 적는다(자동
  탐색이 상한을 못 찾으면 `없음` — 그 층은 안 돈다. `GUARD_CGROUP_DIR`로 준 곳에서 못 찾으면 감시자가 뜨지 않는다).
  감시자가 뜨지 못한 사유는 `$SERVE_DIR/logs/guard.log`의 「시작 거부」 줄에도 남는다(기동 줄이 stderr를 버린다).
  run 끝 내림이 실패하면(러너는 run 결과를 바꾸지 않고 경고로 남긴다) 감시자도 안 멈추므로, 남은 서버는 (마감을 켰다면) 그
  마감이나 `HARNESS_RUN_ID=<run> bash tools/serve/swap.sh`가 치운다 - 마감이 기본 꺼짐이라 보통은 사람이다. 감시자 확인은 서버를 띄우기 직전마다 한다 —
  한 교대가 두 키를 띄우는 중 감시자가 끝나면 둘째 키는 안 띄운다.
  ⚠ `STOP_GUARD`는 run 끝 내림의 신호이자 사람의 멈춤 스위치다. run 도중 사람이 만들면 감시자가 멈추고 그 run의
  다음 기동이 「STOP_GUARD가 있다」로 거부돼 run이 `model_swap_failed`로 멈춘다 — 그때까지 떠 있던 서버는 그
  단계가 끝나 run 끝 내림이 내릴 때까지 감시 없이 돈다.
- ⚠ vast.ai 컨테이너에는 docker·podman이 없다(2026-09-29) — 도구 단계를 어디서 돌릴지 정하기 전에는 vast로
  끝까지 도는 run이 없다. `vastai.yaml`의 `command`는 로컬 실행이다. ⏵ 2026-10-03 — 원격 경로가 생겼다 ->
  `--device vastai-remote`(하네스·도구는 로컬, 서빙만 vast). 교대는 `tools/serve/swap-remote.sh`가 ssh로 원격
  `swap.sh`를 부르고, 원격 사본은 `vast-bootstrap.sh`, 모델 호출은 `vast-tunnel.sh` 터널을 지난다. 순서 ->
  ① bootstrap ② 원격에 감시자 ③ 터널 ④ WSL·Linux에서 `SWAP_SSH_HOST`·`SWAP_REMOTE_ROOT`·`SWAP_REMOTE_PYTHON`을
  주고 run. 이 경로로 끝까지 돈 run은 아직 없다(가짜 ssh 테스트만 — `tests/test_remote_swap.py`).
  ⏵ 2026-10-04 — 끝까지 돈 run이 생겼다(WSL `run_20261003_0002` · Windows `run_20261003_0003` —
  [보고서](experiments/harness/2026-10-04-cybergym-operational-run-vastai-remote.md)). Windows 쪽 서술의 사유는 #477에서 정정했다(아래).
  ⏵ 2026-10-04 — #477: Windows 네이티브에서 교대가 깨진 원인은 `bash`가 `System32` 아래의 WSL 런처로 풀린 것이었다.
  `model_swap`이 `command[0]`을 `shutil.which`로 먼저 푼다. Windows에서는 세 변수를 Git Bash `export`로 세우지
  않는다 — MSYS가 `/root/…` 값을 Windows 경로로 바꾼다.
  교대가 실패하면 하네스 경고·`model_swap` 이벤트의 상세 맨 앞에 교대 스크립트의 사유(`error`)가 온다(#476) ->
  원격 `swap.log`를 열지 않아도 된다.
  ⏵ 2026-10-05 (#495 리뷰) — `which`는 PATH를 **앞에서부터** 본다. ④는 「WSL·Linux에서」말고도 **`Git\usr\bin`이
  `System32`보다 앞에 있는 PATH**(Git Bash로 띄운 셸)에서 돌 수 있다. 그보다 앞에 없는 PATH(레지스트리 PATH —
  PowerShell·cmd)에서는 which도 같은 런처를 골라, **교대를 시도할 때** 하네스가 띄우지 않고 `ModelSwapError`로
  실패한다 — 교대면 run이 `model_swap_failed`로 멈추고 run 끝 내림이면 경고이며, 교대하지 않는 run(`--dry-run`·낮은
  `--stop-after`)은 영향이 없다(그 run의 run 끝 내림이 경고 한 줄과 `model_swap ok=false` 이벤트 한 줄을 남긴다).
  `SWAP_SSH_HOST`이 비었다 같은 엉뚱한
  사유로 실패하는 대신 원인을 바로 말한다. Git Bash로 띄우거나 PATH 앞에 `Git\usr\bin`을 두거나 장비 파일
  `command[0]`에 bash 절대경로를 쓴다 — 단 장비 파일은 추적 파일이라 고치면 `repo_dirty`로 그 run은 비교표에서
  빠진다(실험 run에서는 앞의 둘을 쓴다). 막는 것은 `System32`의 `bash.exe`뿐이고(`WindowsApps` 별칭은 안 막고,
  PATH에서 `bash`를 못 찾아 `which`가 못 풀면 못 막는다 — `CreateProcess`는 `System32`를 먼저 본다)
  `doctor --device`는 이 판정을 안 한다 — doctor가 OK여도 교대를 시도하면 막힐 수 있다. 이 변경 뒤에 Windows
  네이티브로 원격 교대를 끝까지 돌린 run은 없다 — **미실측**(위 Windows `run_20261003_0003`은 `command`를 Git Bash
  절대경로로 고친 미커밋 우회로 돌았다 — 보고서 3.2).
  ⏵ 2026-10-08 (#538) — 위 ①~④와 풀이는 셸 조건이 반쪽이다: Git Bash `export`는 환경변수, PowerShell은 bash
  해소에서 preflight ERROR(2026-10-07 실 박스). 실 박스에서 통과한 조합은 PowerShell `$env:` + `Git\usr\bin`을 PATH 앞에 하나다.
  Windows 순서·명령의 정본은 [portability.md](portability.md) §1 「Windows에서 원격 교대 run 절차」다.

Scout를 역할 셋으로 쪼갠 근거는 [ADR 0010](decisions/0010-harness-structure-and-model-roster.md)
§3이고, **기존 `scout` 한 역할 프로필도 그대로 유효하다**(둘 중 하나가 있어야 한다).

- vLLM: 온프레미스 inference server, OpenAI-compatible API 제공
- LiteLLM: 역할별 routing·retry·fallback, spend/budget 관리
- 도메인 로직은 `ModelClient` 인터페이스에만 의존한다([engineering-guide.md](engineering-guide.md)).
- **서버 stdout은 `tee`로 파일에 받는다.** 규약과 사유는
  [experiments/README.md](experiments/README.md)에 있다 — 같은 유실을 세 번 겪었고,
  그 창에만 있는 값(`Prefix cache hit rate`)이 장부의 `cached_tokens`를 해석하는
  유일한 대조값이다. 여기 두는 것은 포인터뿐이고 규약의 정본은 그쪽이다.

## 7. 확장 정책

1. **모델 추가는 역할별 benchmark에서 실제 이득이 확인될 때만** 허용한다. 에이전트를 무작정 늘리지 않고 repository shard와 candidate queue만 병렬화한다.
2. 2대 연결(ConnectX-7)은 단일 노드 benchmark에서 병목이 확인된 뒤에 검토한다. 확장이 열리면 Primary Auditor를 더 큰 코드 모델로 교체하거나 Critic을 별도 노드로 분리하는 경로가 생긴다.
3. 단일 노드 성능 한계는 확장(2대 연결)을 검토하기 전에 파악한다 — 그 판단의 전제다.
