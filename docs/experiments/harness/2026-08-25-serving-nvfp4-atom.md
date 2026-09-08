# NVFP4 실서빙 검증 (8B 기동 · 30B 기동+추론+bench) — atom

> **정정 (2026-09-03, PR #135).** 본문 네 곳이 *"`nvidia-smi`가 `[N/A]`를 낸다"*를 **쿼리 없이**
> 단정했다. 그 문장이 참인 것은 **`--query-gpu`에 대해서**이고, 같은 장비에서
> `--query-compute-apps`는 프로세스별 값을 준다(`150314, 54716 MiB` — [교대 보고서](2026-09-03-model-swap-sleep-atom.md) §3.4).
> 네 곳에 쿼리를 붙여 좁혔다. **관측은 그대로다** — 그때 실제로 본 것이 `--query-gpu`의 `[N/A]`이고,
> 바뀐 것은 그 관측이 무엇까지 말하는가다(CLAUDE.md §2.2).
>
> **`mem_gb_ctx8k`를 채울 수 있게 됐다는 뜻은 아니다.** 그 측정은 `--max-model-len 32768`이라
> 이 필드의 정의(context 8K)와 다르고, 값이 기동 시 `gpu_memory_utilization` 선점으로 정해져
> 설정값의 되읽기에 가깝다. 미측정 판정은 유지된다.

> **갱신 (2026-09-04, PR #142) — 본문은 그대로 두고 registry 쪽 사실만 덧붙인다.**
> 해당 항목 둘: **§1 요약 표의 `devstral-small-2-24b` 행**과 **해석 ①의 마지막 문장**.
> ①이 *"registry의 `source`는 `null`로 남는다"*를 **현재형으로** 적는데 registry는 더 이상
> 그렇지 않다 — [다섯 상주 보고서](2026-09-04-five-model-residency-atom.md) §7이 그 모델을
> **처음 띄웠고**(포트 8003 — 같은 보고서 §8 표), 지금 `config/models.yaml`의
> `devstral-small-2-24b`에는 `source`
> (`hf://mistralai/Devstral-Small-2-24B-Instruct-2512@55c5b41e98c2dbd21b0c8afffc540dcfc9eb5128`)와
> `checksum`이 둘 다 들어 있다(manifest는 `config/checksums/devstral-small-2-24b.txt`).
> **띄운 체크포인트의 양자화도 `nvfp4`가 아니라 `fp8`이다.** 정정 내용은
> [README](../README.md) 확정 #7의 2026-09-04 갱신 블록이 담는다.
>
> **관측은 그대로다** — 이 실험이 2026-08-25에 그 모델을 띄우지 않은 것은 맞다.
> **다만 「받지도」까지 반증됐다고는 적지 않는다**(CLAUDE.md §2.2). 같은 §7이 적은 것은
> *"HF 캐시에 이미 받아져 있었다(2026-08-25)"*까지이고, 그 관측이 말하는 것은 **2026-09-04에
> 본 캐시의 정본 스냅샷이 8/25 날짜였다**는 것이지 누가 언제(이 실험 전인지 후인지) 받았는지가
> 아니다. 그리고 이 보고서에서 「받지도」가 걸려 있던 대상은
> [portability.md](../../portability.md) §5 #1이 세는 **nvfp4 빌드**다 — §7 미측정 표가
> *"24B nvfp4를 받아 띄우고"*로, §9 후속 과제가 *"nvfp4는 `Firworks/…-nvfp4`로 존재하는 것만
> 확인"*으로 적는다. **그 빌드는 지금도 받지 않았다** — `config/models.yaml`의 같은 항목
> 주석이 그렇게 적는다.

## 메타

| 항목 | 값 | 비고 |
| --- | --- | --- |
| 실험 종료 시각 | **2026-08-25 15:12 KST** (`serve_30b` 로그 마지막 기록). bench `measured_at`은 `2026-08-25T05:59:47Z` = **14:59:47 KST** | |
| 실제 서빙 모델 ① | `hf://nvidia/Qwen3-8B-NVFP4@ccd10a893cbca613259517c3efe08e151ddf2b8e` | 별칭 `qwen3-8b` (`served_model_name=qwen3-8b`) · 6.41 GB |
| 실제 서빙 모델 ② | `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` | 별칭 `qwen3-coder-30b-a3b` |
| `repo_commit` | `393b38e2d647cf830de0db859c6350ceac031d49` | dirty: `config/models.yaml` (bench `--apply`) + `config/checksums/*.txt` 2건 신규 |
| `run_id` | **해당 없음** — `cli bench`는 run 디렉터리를 만들지 않는다 | 같은 날 `run_20260825_0001`은 **다른 실험**이다([542건 보고서](2026-08-25-pipeline-zlib542-atom.md)) |
| run 산출물 경로·보존 여부 | `/srv/tenants/user/artifacts/logs/` — **보존** | **저장소 밖이다.** [open-questions](../../open-questions.md) R3이 경고한 바로 그 배치다 |
| `tool_runner` | **해당 없음** — bench는 도구를 돌리지 않는다 | |
| `--device` | `atom` | |
| `tool_images` digest | **해당 없음** — 컨테이너를 쓰지 않는다 | |
| `--gpu-memory-utilization` | 8B **0.2** / 30B **0.3**. 분모는 물리 통합메모리 **119.7 GiB** | 로그 원문: `Free memory on device (109.32/119.7 GiB)`. cgroup 상한(64 GiB)과 다르다([README](../README.md) 확정 #4) |
| 동시에 떠 있던 다른 서버 | **없음** | 기동 전 `ss`·`nvidia-smi`·`ps` 0건 확인 |
| 원문 산출물 보존 위치 | `logs/bench30b_20260825_142256.log` (11,142 B) · `logs/models30b_20260825_142256.json` | **보존** |
| 서빙 서버 stdout | `logs/serve_8b_20260825_142256.log` (28,230 B) · `logs/serve_30b_20260825_142256.log` (77,599 B) | **보존.** [README](../README.md)의 `setsid nohup` 규약을 지킨 두 번째 사례 |

> **경로 접두사**: 위 `logs/`는 전부 `/srv/tenants/user/artifacts/logs/` 아래다.

## 1. 요약

**성능 주장과 경로 성립 주장을 갈라 적는다.**

| 항목 | 결과 | 종류 |
| --- | --- | --- |
| NVFP4 체크포인트가 sm_121에서 기동하는가 | **예 — 2종 모두.** 추가 환경변수 없이 떴다 | 경로 성립 |
| NVFP4 MoE(30B)가 구조화 출력을 내는가 | **예.** `response_format=json_object`로 5/5, `valid_ratio 1.0` | 경로 성립 |
| 30B 처리량 | 단일 54.83 tok/s · 동시성 32에서 38.93 tok/s | 성능 (표본 5) |
| 30B 배치 이득 | **없다 — 배치가 단일보다 느리다.** 4B와 정반대 | 성능 (단, ④ 참조) |
| prefix cache | 장부 0.0 · `/metrics` 0.9996 → `verdict: metrics_only` | 경로 성립 |
| `mem_gb_ctx8k` | **미측정.** `nvidia-smi --query-gpu`가 `[N/A]`(통합메모리) | 아래 정정 |
| `devstral-small-2-24b` | **미검증.** 받지도 띄우지도 않았다 | — |

## 2. 실험 환경

| 항목 | 값 | 갈리는 항목인가 |
| --- | --- | --- |
| 장비 | `atom` — NVIDIA GB10, Blackwell **sm_121**, aarch64 | ✅ slurm(sm_86, x86_64)와 갈린다 |
| vLLM | **0.27.1** (로그 원문: `Initializing a V1 LLM engine (v0.27.1)`) | ✅ slurm는 0.19.1 상한 |
| dtype | `torch.bfloat16` · `quantization=modelopt_fp4` | ✅ 4B는 `fp8` |
| `kv_cache_dtype` | **`fp8_e4m3` (자동 선택)** | ✅✅ **4B는 `bf16`이다. 해석 ⑤** |
| `max_seq_len` | 32768 | |
| cgroup | `memory.max` 68,719,476,736 (64 GiB) · `memory.high` 62,277,025,792 | |
| prefix caching | `enable_prefix_caching=True` · `enable_chunked_prefill=True` | |

## 3. 실험 1 — Qwen3-8B NVFP4 기동

### 목적

**경로 성립 판정.** NVFP4 체크포인트가 sm_121에서 뜨는가. `qwen3-4b`(fp8)가
`VLLM_USE_DEEP_GEMM=0`을 요구했으므로([portability.md](../../portability.md) §5 #5),
다른 양자화도 같은 종류의 백엔드 문제를 따로 만날 수 있다는 것이 열려 있었다.

### 조건

`--gpu-memory-utilization 0.2` (분모 119.7 GiB → 23.94 GiB). 추론은 하지 않았다.

### 결과

| 항목 | 결과 |
| --- | --- |
| 기동 | **성공.** 추가 환경변수 없음 |
| GEMM 커널 | `FlashInferCutlassNvFp4LinearKernel` |
| attention 백엔드 | `FLASHINFER` (후보 `['FLASHINFER', 'TRITON_ATTN']`) |
| Model loading | 5.98 GiB / 45.70 s |
| KV cache | 15.71 GiB → **228,832 tokens** · 32,768 요청 기준 동시 **6.98x** |

### 근거 로그

```
INFO  [model.py:645] Resolved architecture: Qwen3ForCausalLM
INFO  [cache.py:296] Using fp8_e4m3 data type to store kv cache. It reduces the GPU memory
      footprint and boosts the performance. Meanwhile, it may cause accuracy drop without a
      proper scaling factor
WARNING [modelopt.py:1021] Detected ModelOpt NVFP4 checkpoint (quant_algo=NVFP4). Please note
      that the format is experimental and could change in future.
INFO  [core.py:121] Initializing a V1 LLM engine (v0.27.1) with config:
      model='…/models--nvidia--Qwen3-8B-NVFP4/snapshots/ccd10a893cbca613259517c3efe08e151ddf2b8e',
      … quantization=modelopt_fp4, … kv_cache_dtype=fp8_e4m3, … served_model_name=qwen3-8b,
      enable_prefix_caching=True, enable_chunked_prefill=True
INFO  [__init__.py:1077] Using FlashInferCutlassNvFp4LinearKernel for NVFP4 GEMM
INFO  [cuda.py:482] Using FLASHINFER attention backend out of potential backends:
      ['FLASHINFER', 'TRITON_ATTN'].
WARNING [kv_cache.py:134] Checkpoint does not provide a q scaling factor. Setting it to k_scale.
      This only matters for FP8 Attention backends (flash-attn or flashinfer).
WARNING [kv_cache.py:151] Using KV cache scaling factor 1.0 for fp8_e4m3. If this is unintended,
      verify that k/v_scale scaling factors are properly set in the checkpoint.
INFO  [model_runner.py:329] Model loading took 5.98 GiB and 45.698446 seconds
INFO  [kv_cache_utils.py:2235] GPU KV cache size: 228,832 tokens
INFO  [kv_cache_utils.py:2236] Maximum concurrency for 32,768 tokens per request: 6.98x
INFO  [flashinfer.py:824] FlashInfer resolved query dtypes: prefill=torch.bfloat16,
      decode=torch.bfloat16, decode_backend=flashinfer-native,
      kv_cache_dtype=torch.float8_e4m3fn, arch=sm121
```

### 해석

**기동만 판정한다.** 추론을 하지 않았으므로 이 실험에서 나오는 것은
"NVFP4 가중치를 sm_121에서 읽고 커널을 잡는 데까지 간다"뿐이다. 출력 품질은
이 표본으로 말할 수 없다.

**두 경고는 성격이 다르다.** `quant_algo=NVFP4 … experimental`은 vLLM이 이 형식을
실험적이라고 말하는 것이고, `q scaling factor` 부재는 **체크포인트에 값이 없어
`k_scale`로 대체하고 KV cache scaling을 1.0으로 뒀다**는 뜻이다. 후자는 정확도에
영향을 줄 수 있고 **이 실험은 그것을 재지 않았다**.

## 4. 실험 2 — Qwen3-Coder-30B-A3B NVFP4 기동 + 추론

### 목적

**경로 성립 판정.** MoE + NVFP4 조합이 뜨는가, 그리고 **구조화 출력이 나오는가.**
후자가 하네스에 중요하다 — Scout·Auditor·Critic이 전부 JSON을 요구한다.

### 조건

`--gpu-memory-utilization 0.3` = 35.91 GiB. 아키텍처 `Qwen3MoeForCausalLM`
(30.5B total / 3.3B active).

> ⚠ 이 스냅샷은 **2026-08-21 22:42~23:00에 이미 받아져 있었다.** 누가 받았는지는
> **미기재**다. 이번 실험이 받은 것이 아니다.

### 결과

| 항목 | 결과 |
| --- | --- |
| 기동 | **성공** |
| MoE 백엔드 | `FLASHINFER_CUTLASS` (후보 8종 중 자동 선택) |
| Model loading | 16.92 GiB / **153.3 s** |
| 실사용 | weights+non-torch **18.24 GiB** · peak activation 2.08 · CUDAGraph 2.17 |
| KV cache | 15.58 GiB → **340,416 tokens** · 동시 **10.39x** |
| 추론 | `response_format=json_object` → `{"ok": true, "n": 42}` **정확히 반환** (prompt 24 / completion 13) |

### 근거 로그

```
INFO [model.py:645] Resolved architecture: Qwen3MoeForCausalLM
INFO [__init__.py:1077] Using FlashInferCutlassNvFp4LinearKernel for NVFP4 GEMM
INFO [nvfp4.py:285] Using 'FLASHINFER_CUTLASS' NvFp4 MoE backend out of potential backends:
     ['FLASHINFER_TRTLLM', 'FLASHINFER_CUTEDSL', 'FLASHINFER_CUTEDSL_BATCHED',
      'FLASHINFER_CUTLASS', 'VLLM_CUTLASS', 'MARLIN', 'HUMMING', 'EMULATION'].
INFO [gpu_model_runner.py:5405] Model loading took 16.92 GiB memory and 153.304283 seconds
INFO [gpu_worker.py:578] CUDA graph memory profiling is enabled (default since v0.21.0). The
     current --gpu-memory-utilization=0.3000 is equivalent to --gpu-memory-utilization=0.2869
     without CUDA graph memory profiling. To maintain the same effective KV cache size as
     before, increase --gpu-memory-utilization to 0.3131.
INFO [kv_cache_utils.py:2235] GPU KV cache size: 340,416 tokens
INFO [kv_cache_utils.py:2236] Maximum concurrency for 32,768 tokens per request: 10.39x
INFO [gpu_worker.py:789] Free memory on device (109.32/119.7 GiB) on startup. Desired GPU
     memory utilization is (0.3, 35.91 GiB). Actual usage is 18.24 GiB for consumed memory
     (weights + non-torch), 2.08 GiB for peak activation, and 2.17 GiB for CUDAGraph memory.
     Replace gpu_memory_utilization config with `--kv-cache-memory=14240156877` (13.26 GiB) to
     fit into requested memory, or `--kv-cache-memory=93064468480` (86.67 GiB) to fully utilize
     gpu memory. Current kv cache memory in use is 15.58 GiB.
```

### 해석

**구조화 출력이 이 실험의 핵심이다.** 기동은 실험 1이 이미 보였고, MoE가 더한 것은
`FLASHINFER_CUTLASS` 경로다. 새로 나온 것은 **NVFP4 양자화된 MoE가 JSON 스키마를
지켜서 낸다**는 사실이고, 표본 1건이므로 이것은 **경로 성립**이지 품질 측정이 아니다
(실험 3의 5/5가 표본을 5로 올린다).

**`--gpu-memory-utilization` 비율이 실사용과 크게 어긋난다.** 0.3 = 35.91 GiB를
요청했는데 실사용은 18.24 + 2.08 + 2.17 = 22.49 GiB이고 KV cache가 15.58 GiB다.
vLLM 자신이 바이트 고정을 권한다 — 해석 ⑥.

## 5. 실험 3 — `cli bench --profile b1_single --device atom --roles auditor --apply`

### 목적

**성능 측정 + 체크리스트 판정.** [model-serving.md](../../model-serving.md) §3 체크리스트를
30B에 대해 돌린다.

### 조건

`samples 5` · `concurrency [8, 16, 32]` · `contexts [8192, 16384, 32768]` ·
`body_target_tokens 18000` → `prompt_tokens_approx 18224`.

### 결과

| 항목 | 결과 |
| --- | --- |
| startup | **ok** · `served_ids ["qwen3-coder-30b-a3b"]` |
| structured_output | **5/5** · `valid_ratio 1.0` · `failures []` |
| prefix_cache | `verdict "metrics_only"` — 장부 cached 0 / prompt 110,605 = **0.0** · metrics hits 110,560 / queries 110,605 = **0.9996** |
| throughput 동시성 1 | **54.83 tok/s** · prefix hit 0.9996 |
| throughput 동시성 8 | **73.40 tok/s** · prefix hit 0.6356 |
| throughput 동시성 16 | **21.29 tok/s** · prefix hit 0.5142 · **HTTP 400 발생** |
| throughput 동시성 32 | **38.93 tok/s** · prefix hit 0.3225 · **timeout 발생** |
| memory ctx 8192 | kv_peak 0.0840% (57 표본) |
| memory ctx 16384 | kv_peak 0.0605% (82 표본) |
| memory ctx 32768 | `call_ok False` — **HTTP 400** |
| `mem_gb_ctx8k` | **0.0 · unmeasured** (`nvidia-smi --query-gpu` `[N/A]`, 통합메모리) |
| checklist | pass **5** / unmeasured **2** (`mem_gb_ctx8k` · NVFP4 Critic 반박 정확도) |
| `--apply` | `tok_s_single 54.83` · `tok_s_batch32 38.93` 기입. **`mem_gb_ctx8k`는 skipped** |

### 근거 로그

HTTP 400의 `error_detail` 원문:

```
This model's maximum context length is 32768 tokens. However, you requested 0 output tokens
and your prompt contains at least 32769 input tokens, for a total of at least 32769 tokens.
Please reduce the length of the input prompt or the number of requested output tokens.
(parameter=input_tokens, value=32769)
```

prefix cache 판정:

```
"verdict": "metrics_only"
"external_prefix_cache_hits_delta": 0.0
"prompt_tokens_recomputed_delta": null
"prompt_tokens_recomputed_delta_reason": "vllm:prompt_tokens_recomputed_total가 /metrics에 없다"
```

### 해석

아래 ①~⑥.

---

## ① NVFP4의 sm_121 기동이 실측으로 확인됐다 — 단 §5 #1은 닫히지 않는다

**닫힌 것**: `config/models.yaml`의 `qwen3-4b` 주석이 열어 둔 문장이다 —
"다른 양자화(`nvfp4`)도 같은 종류의 백엔드 문제를 따로 만날 수 있다".
NVFP4 2종이 **추가 환경변수 없이** 떴으므로 `fp8`의 `VLLM_USE_DEEP_GEMM=0`에
대응하는 NVFP4 쪽 벽은 **없다**.

**닫히지 않은 것**: [portability.md](../../portability.md) §5 #1이다. 그 항목의
"못 도는 환경" 칸은 **`Blackwell(sm_100+) 미만`**이고 ATOM은 sm_121 = Blackwell이다.
**Blackwell에서 떴다는 것은 #1을 확인하지 반증하지 않는다.** #1의 상태는
`미해결` 그대로이고, 해법("같은 모델의 다른 양자화를 별도 model key + 별도 프로필로
둔다")도 그대로다.

**`devstral-small-2-24b`는 미검증이다.** §5 #1이 세는 3개 중 2개만 이번에 떴다.
받지도 띄우지도 않았으므로 registry의 `source`는 `null`로 남는다.

## ② D21 4-1이 처음 실현됐다 — 단 실 파이프라인이 아니다

세 번 미실현이던 HTTP 400의 `error_detail` 원문을 확보했다(§근거 로그).
`prompt_tokens_approx 32992`로 잡은 프롬프트가 서버에서 **32,769 토큰**으로 세어졌다 —
**근사가 천장을 224토큰(0.7%) 넘겼다.** D21이 말하는 "근사 오차가 천장 근처에서 400을
만든다"의 실증이다.

**단 이것은 `bench`의 합성 프롬프트이지 실 파이프라인이 아니다.** 4-1이 요구하는
**실대상에서의 실현은 여전히 미측정**이다. 합성 프롬프트는 길이를 목표에 맞춰 채우므로
천장에 붙는 것이 설계된 동작이고, 실 파이프라인의 캡슐이 같은 자리에 도달하는지는
이 실험이 답하지 않는다.

## ③ R6에 대체 경로가 확정됐다

장부 `cached_tokens` **0.0** vs `/metrics` **0.9996**. 같은 110,605 프롬프트 토큰에 대해
두 값이 정반대다.

**어댑터 결함이 아니다.** `/metrics`의 서버 측 카운터는 110,560 hit를 정확히 세고 있고,
**장부만 0**이다. `bench`가 이미 그 판정을 코드로 내리고 있다 — `verdict: metrics_only`.

같은 날 `run_20260825_0001`(zlib 542건)의 서버 측 **`Prefix cache hit rate: 30.5%`**와
함께 놓으면, **서버는 두 경우 모두 재사용을 하고 있고 장부가 그것을 못 잡는다**가 된다.
두 수치가 크게 다른 것(0.9996 vs 0.305)은 워크로드 차이다 — bench는 같은 접두사를
반복하고 zlib run은 심볼마다 캡슐이 다르다.

**남은 구멍**: `vllm:prompt_tokens_recomputed_total`이 이 서버 버전의 `/metrics`에 없어
`prompt_tokens_recomputed_delta`가 `null`이다. 대체 경로가 **하나뿐**이라는 뜻이다.

## ④ 30B는 배치가 단일보다 느리다 — 4B와 정반대

| 모델 | 단일 | 배치32 | 비 |
| --- | --- | --- | --- |
| `qwen3-4b` (fp8) | 39.65 | 127.69 | **3.2배 ↑** |
| `qwen3-coder-30b-a3b` (nvfp4) | 54.83 | 38.93 | **0.71배 ↓** |

동시성 8이 최적(73.40)이고 16·32는 떨어진다.

**⚠ 16·32 수치에는 실패가 섞여 있어 순수 처리량이 아니다.** 동시성 16에서 HTTP 400이,
32에서 timeout이 났다. 실패한 요청이 분모·분자에 어떻게 들어갔는지에 따라 값이
움직이므로 **이 두 값으로 "배치가 느리다"의 크기를 말할 수 없다.** 방향(단일 > 배치32)만
읽는다.

**prefix cache 적중이 동시성에 반비례한다**: 0.9996 → 0.6356 → 0.5142 → 0.3225.
동시 요청이 서로의 블록을 밀어내는 것으로 보이지만 **이 실험은 원인을 재지 않았다.**

## ⑤ `kv_cache_dtype`이 모델마다 다르고 `RunRecord`에 안 남는다

| 모델 | `kv_cache_dtype` | 근거 |
| --- | --- | --- |
| `qwen3-4b` (fp8) | **bf16** | [542건 보고서](2026-08-25-pipeline-zlib542-atom.md) |
| NVFP4 2종 | **fp8_e4m3 (자동 선택)** | 이 보고서 §3·§4 로그 |

**조건 비교의 교란변수다.** KV cache 정밀도가 다르면 같은 컨텍스트에서 메모리도 품질도
갈리는데, 하네스가 그것을 고르지도 기록하지도 않는다 — 서버가 체크포인트를 보고
자동으로 정한다. **`top_k` 미기록(D29)과 같은 종류**이고, 같은 축에 컴파일 플래그(D32)가
있다.

## ⑥ util 비율로는 B3′ 3모델 동시가 안 된다

30B `0.3` = 35.91 GiB + 8B `0.2` = 23.94 GiB = **59.85 GiB**. 여기에 24B를 더하면
cgroup 하드 한도 **64 GiB**를 넘는다.

**비율이 실사용과 어긋나는 것이 원인이다.** 30B의 실사용은 22.49 GiB인데 35.91 GiB를
잡았다. vLLM 자신이 바이트 고정을 권한다:

```
Replace gpu_memory_utilization config with `--kv-cache-memory=14240156877` (13.26 GiB) to
fit into requested memory, or `--kv-cache-memory=93064468480` (86.67 GiB) to fully utilize
gpu memory.
```

[model-serving.md](../../model-serving.md)가 이미 그 방향을 적어 뒀다. **이 실험은 3모델
동시를 시도하지 않았다** — 위는 산술이지 실측이 아니다.

---

## 6. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| ctx 32768에서 `call_ok False` | `prompt_tokens_approx` 근사가 천장을 224토큰 넘김 (D21) | **해결 못 함.** 토크나이저 없이는 근사가 남는다. 해석 ② |
| `mem_gb_ctx8k` 측정 불가 | `nvidia-smi --query-gpu`가 `[N/A]` — GB10은 통합메모리라 이 쿼리에 별도 VRAM 카운터가 없다 | **해결 못 함.** `--apply`가 `skipped`로 두고 registry는 0 유지 |
| `prompt_tokens_recomputed_delta` `null` | `vllm:prompt_tokens_recomputed_total`이 이 버전 `/metrics`에 없다 | **해결 못 함.** 대체 경로가 `prompt_tokens_cached` 하나뿐 |
| 동시성 16·32의 실패 혼입 | HTTP 400 · timeout | **해결 못 함.** 수치 옆에 표시만 했다 |

## 7. 미측정 항목과 사유

**못 잰 값을 0으로 채우지 않는다.** `Benchmark.is_measured`가 0 여부로 판정하므로
추정치를 넣으면 안 잰 모델이 production 프로필에 오르는 길이 열린다.

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| `mem_gb_ctx8k` (30B·8B 둘 다) | `nvidia-smi --query-gpu`가 통합메모리에서 `[N/A]`를 낸다 | cgroup `memory.current` 또는 `torch.cuda.memory_allocated()`를 분모로 쓰는 별도 계측 |
| NVFP4 Critic 반박 정확도 (R2) | Critic 모델(`devstral-small-2-24b`)을 띄우지 않았다 | 24B nvfp4를 받아 띄우고 반박 표본을 돌린다 |
| 8B 처리량·구조화 출력 | **기동만 했다.** 추론 요청을 보내지 않았다 | `cli bench --roles`에 8B를 넣는다 |
| 30B 출력 **품질** | 표본 6건(1 + 5)은 형식 준수만 본다 | 평가 데이터셋 |
| 3모델 동시 기동 | 시도하지 않았다 | `--kv-cache-memory` 바이트 고정 후 실측 |
| `q scaling factor` 부재의 정확도 영향 | 재지 않았다 | 같은 프롬프트를 bf16 KV cache와 대조 |

## 8. 반증 조건

**이 실험이 실패했다면 무엇이 틀린 것이었나.**

- **기동이 실패했다면** — `fp8`의 `VLLM_USE_DEEP_GEMM=0`에 대응하는 NVFP4 쪽 벽이
  따로 있다는 뜻이었다. 성공했으므로 그 벽은 **없고**, `qwen3-4b` 주석이 열어 둔
  "다른 양자화도 같은 종류의 문제를 만날 수 있다"가 이 두 모델에 한해 닫힌다.
  **`devstral-small-2-24b`에 대해서는 여전히 열려 있다.**
- **구조화 출력이 깨졌다면** — NVFP4 양자화가 스키마 준수를 무너뜨린다는 뜻이었고,
  B3′의 전제가 바로 무너졌다. 6/6이므로 그 실패 모드는 **이 표본에서는** 없다.
- **장부와 metrics가 둘 다 0이었다면** — R6은 어댑터 결함이 되고 고칠 대상이
  하네스였다. metrics가 0.9996이므로 **서버는 하고 있고 장부만 못 잡는다**로 좁혀진다.

## 9. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 높음 | `devstral-small-2-24b` nvfp4 기동 — §5 #1의 남은 1개, R2의 전제 | 미착수. 정본은 `mistralai/Devstral-Small-2-24B-Instruct-2512`, nvfp4는 `Firworks/…-nvfp4`로 존재하는 것만 확인 |
| 높음 | `--kv-cache-memory` 바이트 고정 후 3모델 동시 기동 | 해석 ⑥ |
| 중간 | `mem_gb_ctx8k`의 통합메모리 계측 경로 | 체크리스트 unmeasured 2건 중 1건 |
| 중간 | 장부 `cached_tokens`가 0인 원인 (R6) | 대체 경로는 확정. 장부 자체는 미해결 |
| 낮음 | 8B 추론·처리량 | 이번엔 기동만 |

## 10. 정리 상태

- **서빙 서버**: 8B(포트 미기재)·30B(`8002`) 둘 다 **종료 여부 미기재**
- **임시 설정·프로필**: 원복 없음 — 프로필을 고치지 않았다
- **저장소 변경**: **있음** — `config/models.yaml`(bench `--apply`) +
  `config/checksums/qwen3-8b.txt`·`qwen3-coder-30b-a3b.txt` 신규.
  브랜치 `measure/nvfp4-atom`, `repo_commit` `393b38e2…`

## 11. 부록 — 재현 절차

**서버 stdout 보존 규약**([README](../README.md))을 지킨 형태:

```bash
setsid nohup vllm serve <스냅샷 경로> \
  --served-model-name qwen3-8b \
  --gpu-memory-utilization 0.2 \
  > "$LOGDIR/serve_8b_$(date +%Y%m%d_%H%M%S).log" 2>&1 &
```

> ⚠ **위 명령은 재구성본이다.** 로그에서 읽히는 인자(`--served-model-name`,
> `--gpu-memory-utilization`, 스냅샷 경로)만 확실하고 **전체 원문은 미기재**다.
> [TEMPLATE.md](../TEMPLATE.md)가 "편집하거나 줄이지 않는다"고 요구하는 필드이므로
> **지키지 못한 것을 그대로 적는다.**

bench:

```bash
python -m src.cli bench --profile b1_single --device atom --roles auditor --apply
```
