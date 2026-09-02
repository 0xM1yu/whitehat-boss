# 동시성 16·32 재측정 — 실패는 사라졌고 수치는 재현되지 않는다

`config/models.yaml`의 `qwen3-coder-30b-a3b` 주석이 **"16·32 측정에는 HTTP 400과 timeout
실패가 섞여 있어 순수 처리량이 아니다"**라고 적어 둔 것을 걷어내려 한 측정이다
([build-plan.md](../../build-plan.md) §8 실행 순서 1번).

**실패는 없앴다. 그런데 11분 간격의 두 판이 동시성 16에서 2.2배 갈렸다.**

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | 2026-09-02 14:16 KST (`measured_at` 05:11:50Z = 14:11:50 KST + 측정 시간) |
| 실제 서빙 모델 | `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` (별칭 `qwen3-coder-30b-a3b` — 기동 로그 `non-default args`의 스냅샷 경로로 대조) |
| `repo_commit` | `9078a50dfceeb542a1887d69eac160b4388f621a` (측정 시점 클린 — `--apply`가 그 뒤 `config/models.yaml`을 고쳤다) |
| `run_id` | **해당 없음** — `cli bench`는 run 디렉터리를 만들지 않는다 |
| run 산출물 경로·보존 여부 | `/srv/tenants/ext01/bench_20260902_140046.json`(1판) · `bench_apply_20260902_141150.json`(2판). **둘 다 보존** |
| `tool_runner` | 선언 `podman` / 실제 **사용 안 함** — `bench`는 도구를 실행하지 않는다(`run_bench`가 값을 기록만 한다) |
| `--device` | `atom` |
| `tool_images` digest | **해당 없음** — 위와 같은 이유 |
| `--gpu-memory-utilization` | **0.45**, 분모 물리 통합메모리 **119.7 GiB** → 53.86 GiB (기동 로그 `Desired GPU memory utilization is (0.45, 53.86 GiB)`) |
| 동시에 떠 있던 다른 서버 | **없음** — 8002 단독(`ss -ltn` 확인) |
| 원문 산출물 보존 위치 | 위 JSON 2건 + `bench_stderr.log`·`bench_apply_stderr.log`(**둘 다 0바이트**) |
| 서빙 서버 stdout | `/srv/tenants/ext01/artifacts/logs/serve_30b_bench_20260902_135120.log` (70,618 B, 보존) |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| 실패 | **네 수준 전부 0건** — 재측정의 목적이 이것이었고 달성됐다 |
| `tok_s_single` | **재현된다** — 52.96 / 53.06 (0.19% 차) |
| 배치 수치 | **재현되지 않는다** — 동시성 16에서 58.33 / 128.51 (**2.20배**) |
| 옛 주석 「배치가 단일보다 느리다」 | **반증됐다.** 두 판 모두 batch32 > single |
| 새 주석으로 「배칭 이득이 거의 없다」 | **쓸 수 없다.** 1판만 보면 그렇지만 2판이 반대다 |
| ctx 32768 | 두 판 모두 HTTP 400. **구조적이다** → [#126](https://github.com/Yunse4l/whitehat-boss/issues/126) |
| cgroup | **vLLM이 `user-1004.slice` 밖에 있다** — 아래 §3.3 |
| attention backend | **FLASHINFER가 선택됐고 그대로 돌았다** — 아래 §3.4 |

## 2. 실험 환경

| 항목 | 값 |
| --- | --- |
| 장비 | `aitopatom-d06a` (GB10, compute_cap 12.1 = sm_121) |
| vLLM | 0.27.1 (`/srv/tenants/ext01/venv-probe`) |
| 기동 인자 | `--served-model-name qwen3-coder-30b-a3b --host 127.0.0.1 --port 8002 --gpu-memory-utilization 0.45 --max-model-len 32768 --enable-prefix-caching` |
| 환경변수 | `VLLM_USE_DEEP_GEMM=0 VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo` |
| 명령 | `python3 -u -m src.cli bench --profile b1_single --device atom --roles auditor --concurrency 8 16 32` (2판은 `--apply` 추가) |
| 프롬프트 | `body_target` 18,000 토큰(`thresholds.budget_for(EXPANDED)`) · `prompt_approx` 18,224 · `max_tokens` = `output_max` **4,096** |

기동 로그가 낸 메모리 회계:

```
Free memory on device (111.15/119.7 GiB) on startup.
Desired GPU memory utilization is (0.45, 53.86 GiB).
Actual usage is 18.63 GiB for consumed memory (weights + non-torch),
  2.66 GiB for peak activation, and 2.27 GiB for CUDAGraph memory.
Current kv cache memory in use is 32.57 GiB.
GPU KV cache size: 711,584 tokens
Maximum concurrency for 32,768 tokens per request: 21.72x
```

**`Maximum concurrency 21.72x`는 요청당 32,768 토큰을 가정한 값이다.** 우리 프롬프트는 그
절반 남짓(18,224 추정)이라 동시성 32도 KV cache에 들어간다 — 실제로 두 판 모두 32에서
실패가 없었다. 8/25에 32가 timeout으로 죽은 것이 **메모리 부족이었다면** 이 값으로 설명된다.

## 3. 실험 — `cli bench --roles auditor --concurrency 8 16 32` ×2

### 3.1 두 판

| 동시성 | 1판 tok/s | 1판 wall | 1판 호출당 out | 2판 tok/s | 2판 wall | 2판 호출당 out | 2판/1판 |
| :-: | --: | --: | --: | --: | --: | --: | :-: |
| 1 | 52.96 | 3.909 s | 207 | 53.06 | 3.958 s | 210 | **1.00** |
| 8 | 62.61 | 26.640 s | 208 | 118.33 | 13.183 s | 195 | **1.89** |
| 16 | 58.33 | 58.738 s | 214 | 128.51 | 25.577 s | 205 | **2.20** |
| 32 | 59.45 | 110.114 s | 204 | 68.64 | 154.022 s | **330** | 1.15 |

**실패는 두 판 네 수준 전부 0건이다.**

호출당 출력 토큰은 계산값(`output_tokens ÷ 동시성`)이다. 동시성 32의 2판만 330으로 튄다 —
같은 프롬프트인데 응답이 1.6배 길었다. `tok_s = produced / elapsed`이므로 분자와 분모가
함께 커진 경우라 **속도가 빨라진 것인지 응답이 길어진 것인지 이 수치만으로는 못 가른다.**

### 3.2 무엇이 갈렸는지는 확정하지 못했다

동시성 8·16에서 2판의 벽시계가 **절반**인데(26.64→13.18, 58.74→25.58) 출력 토큰은
비슷하다(1,668→1,560, 3,426→3,287). **같은 일을 절반 시간에 했다.**

후보 둘을 다 배제하지 못했다.

| 후보 | 이 데이터가 말하는 것 |
| --- | --- |
| 1판이 2판의 prefix cache를 데워 줬다 | 두 판의 프롬프트는 seed 고정이라 동일하다. 다만 `prefix_cache` 절의 `metrics_delta`는 **두 판이 정확히 같다**(`queries_delta` 110,605 · `hit_ratio` 0.9996) — 그 절은 매 판 자기 앞의 `structured_output`이 데운 상태를 재므로 판 간 차이를 못 본다. **throughput 구간의 캐시 상태는 이 기록에 없다** |
| 그냥 산포다 | 단일(동시성 1)이 0.19%로 재현된 것과 어긋난다. 산포라면 단일에도 나타나야 할 이유는 없지만, 배치에서만 2배가 나는 산포는 설명이 필요하다 |

**두 판으로는 못 가른다.** 판을 늘리거나(같은 서버에서 3판 이상), 판 사이에 캐시를
비우거나(`--enable-prefix-caching` 없이 한 판), throughput 구간의 캐시 delta를 따로
기록해야 답이 나온다.

### 3.3 vLLM이 `user-1004.slice` 밖에 있다

`--gpu-memory-utilization`을 정할 때 `memory.max`를 읽으라는 지침([README](../README.md)
확정 #4)대로 읽었더니 **말단이 `max`**였다. 계층을 끝까지 올라가 본 결과:

```
vllm(pid 2894437)의 cgroup = /system.slice/ssh.service
  /system.slice/ssh.service     max=max   high=max   current=10,037,895,168 (9.35 GiB)
  /system.slice                 max=max   high=max   current=12,235,497,472 (11.4 GiB)
  (root)                        max=      high=

/sys/fs/cgroup/user.slice/user-1004.slice/memory.max = 85,899,345,920 (80 GiB)  ← 실재한다
/sys/fs/cgroup/user.slice/memory.max                 = max
```

**80 GiB 한도는 그대로 있다. 우리 프로세스가 그 안에 없을 뿐이다.** SSH 세션이
`user.slice`의 session scope가 아니라 `system.slice/ssh.service`에 놓였고 vLLM이 그것을
물려받았다.

**2026-08-28에는 안에 있었다** — 미확정 #1의 해소 블록이
`/proc/78448/cgroup = 0::/user.slice/user-1004.slice/session-18873.scope`를 적고 있다.
**그 사이에 배치가 바뀌었고, 언제·왜인지는 이 측정으로 알 수 없다.**

무엇이 무너지고 무엇이 안 무너지는지:

| 서술 | 오늘 상태 |
| --- | --- |
| 확정 #4 「제한 단위가 사용자(`user-1004`)다. 팀 전체가 하나의 80 GiB를 나눠 쓴다」 | **오늘 우리 프로세스에는 성립하지 않는다.** 호스트 RAM이 그 슬라이스에 계상되지 않는다 |
| 확정 #4 「상한에 닿는 순간 OOM kill」 | 그 상한이 우리 경로에 없다. 물리 119.7 GiB가 실질 한계다 |
| 미확정 #1 해소 「cgroup은 GPU 할당을 못 막는다」 | **그대로 유효하다** — 오늘 `current` 9.35 GiB는 8/28의 12.84 GiB와 같은 크기대이고, vLLM 자기 회계 53.86 GiB와 여전히 어긋난다 |

**그래서 실무 영향은 생각보다 작다** — 큰 할당(GPU)은 8/28 시점에 이미 cgroup 밖이었다.
바뀐 것은 호스트 RAM마저 계상되지 않는다는 것이고, 잃은 것은 **회계**다. 「팀이 80 GiB를
나눠 쓴다」를 근거로 한 사이징이 오늘은 아무것도 세지 않는다.

### 3.4 FLASHINFER가 선택됐는데 아무 일도 없었다

```
Using FLASHINFER attention backend out of potential backends: ['FLASHINFER', 'TRITON_ATTN'].
```

확정 #3의 조건 ②는 **「`VLLM_ATTENTION_BACKEND=TORCH_SDPA` — flashinfer가 선택될 때 필요.
그 커널 JIT에 nvcc가 필요한데 권한 밖이다」**이다. 이번에는 flashinfer가 선택됐고 그 변수를
주지 않았는데 **서버가 뜨고 두 판을 완주했다**(실패 0). 8/25에는 flashinfer가 후보에 있었으나
선택되지 않아 조건이 발동하지 않았던 것과 다르다 — **이번엔 발동 조건을 만족했는데 아무 일도
없었다.**

**왜인지는 모른다.** nvcc가 생겼는지, 0.27.1이 프리빌트 커널을 싣는지, 이 모델·이 dtype
경로가 JIT를 안 타는지 안 갈랐다. 확정되는 것은 **②의 서술("flashinfer가 선택되면 필요")이
보편명제로는 반증됐다**는 것뿐이다.

### 3.5 그 밖에 재확인된 것

| 항목 | 값 | 상태 |
| --- | --- | --- |
| structured output | 5/5 유효, 두 판 동일 | model-serving.md §3의 「ATOM에서 깨지면 첫 용의자는 reasoning 토큰」 우려가 **이 모델·이 스택에서는 해당 없음** |
| prefix cache | 장부 `hit_ratio` 0.0 / metrics 0.9996 | **R6 재현** — 서버가 `prompt_tokens_details`를 안 준다. 어댑터 결함이 아니다 |
| `mem_gb_ctx8k` | 0 유지, `--apply`가 `skipped` | `nvidia-smi`가 `[N/A]` (GB10 통합메모리). 지어내지 않았다 |
| `kv_cache_dtype` | `fp8_e4m3` (기동 로그) | 확정 #8 그대로 — **하네스 기록에는 안 남는다** |
| ctx 8192 / 16384 | `kv_cache_usage_perc_peak` 0.0149 / 0.0287 | 두 판이 **소수점 이하까지 동일** |

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 기동 직후 헬스체크가 "죽었다"를 냈다(8/29 재발) | `setsid nohup … & VLLM_PID=$!`의 `$!`가 즉시 끝나는 `setsid`의 pid | `setsid`를 빼고 tmux 안에서 `nohup`만 쓴다 |
| `log=`가 빈 값으로 찍혔다 | `LOG=… && nohup … &`에서 `&`가 앞 `&&` 사슬 **전체**를 배경으로 보내 대입이 서브셸 안에서만 일어났다 | 대입과 기동을 갈라 놓는다. 리다이렉트 자체는 서브셸 안에서 정상 동작해 로그 파일은 만들어졌다 |
| `bench` stdout에 경고가 섞여 JSON 파싱이 깨질 뻔했다 | stderr를 stdout으로 합친 뒤 파일로 받았다 | `> out.json 2> stderr.log`로 가른다 |
| `memory.max`가 `max`로 보였다 | 말단 cgroup만 읽었다 | 계층을 루트까지 올라간다 → §3.3 |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 배치 수치의 재현 폭 | 두 판뿐이다 | 같은 서버에서 3판 이상, 판 사이 캐시 상태를 기록 |
| §3.2의 두 후보 판별 | throughput 구간의 prefix cache delta가 기록에 없다 | 그 구간의 카운터 delta를 따로 남기거나, `--enable-prefix-caching` 없이 한 판 |
| ctx 32768의 KV 점유 | 프롬프트 + `output_max`가 `--max-model-len`을 넘어 400 | [#126](https://github.com/Yunse4l/whitehat-boss/issues/126) — 또는 `--max-model-len`을 36,864 이상으로 |
| `mem_gb_ctx8k` | `nvidia-smi`가 `[N/A]` | 8/25 보고서 §7의 방법 |
| §3.3의 배치가 언제 바뀌었나 | 오늘 상태만 봤다 | logind 설정과 세션 생성 경로 확인 |
| §3.4에서 flashinfer가 왜 안 깨졌나 | 안 갈랐다 | nvcc 유무 · 0.27.1 프리빌트 여부 확인 |

## 6. 반증 조건

- **「실패 0」이 반증되려면**: 같은 인자로 돌린 판에서 `failures`가 비지 않으면 된다. 두
  판이 전부 0이었다.
- **「배치 수치가 재현되지 않는다」가 반증되려면**: 세 판 이상이 동시성 8·16에서 서로
  10% 안에 들면 된다. 그러면 오늘의 2.20배는 다른 원인(캐시 등)으로 귀속된다.
- **「배칭 이득이 있다/없다」는 어느 쪽도 주장하지 않는다.** 1판은 없다고, 2판은 있다고
  말한다. 이 보고서는 그 둘이 갈렸다는 것까지만 주장한다.

## 7. 후속 과제

1. **판을 늘려 배치 수치의 폭을 정한다.** `tok_s_batch32`가 registry에서 비교 근거로
   쓰이는데 지금 그 값은 두 관측 중 하나다.
2. [#126](https://github.com/Yunse4l/whitehat-boss/issues/126) — context 스윕이 서버
   상한을 모른다.
3. §3.3의 cgroup 배치를 확인하고 확정 #4의 회계 서술을 그에 맞춘다.
4. §3.4로 확정 #3 ②의 서술 범위를 좁힌다.
