# 동시성 16·32 재측정 — 실패는 사라졌고, 배치 수치는 캐시 온도의 함수였다

`config/models.yaml`의 `qwen3-coder-30b-a3b` 주석이 **"16·32 측정에는 HTTP 400과 timeout
실패가 섞여 있어 순수 처리량이 아니다"**라고 적어 둔 것을 걷어내려 한 측정이다
([build-plan.md](../../build-plan.md) §8 실행 순서 1번).

**실패는 없앴다. 그런데 세 판의 `tok_s_batch32`가 59.45 / 68.64 / 107.32로 1.81배
갈렸고, 아홉 칸 전부에서 prefix cache 적중률 순서가 tok/s 순서와 같다.
세 판 중 어느 것도 운영 처리량이 아니다 — [#128](https://github.com/Yunse4l/whitehat-boss/issues/128).**

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | 2026-09-02 15:04 KST (3판 종료. `measured_at` 1판 05:00:47Z · 2판 05:11:50Z · 3판 06:00:55Z) |
| 실제 서빙 모델 | `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` (별칭 `qwen3-coder-30b-a3b` — 기동 로그 `non-default args`의 스냅샷 경로로 대조) |
| `repo_commit` | `9078a50dfceeb542a1887d69eac160b4388f621a` (측정 시점 클린 — `--apply`가 그 뒤 `config/models.yaml`을 고쳤다) |
| `run_id` | **해당 없음** — `cli bench`는 run 디렉터리를 만들지 않는다 |
| run 산출물 경로·보존 여부 | `/srv/tenants/ext01/` 아래 `bench_20260902_140046.json`(1판) · `bench_apply_20260902_141150.json`(2판) · `bench_run3.json`(3판). **셋 다 보존** |
| `tool_runner` | 선언 `podman` / 실제 **사용 안 함** — `bench`는 도구를 실행하지 않는다(`run_bench`가 값을 기록만 한다) |
| `--device` | `atom` |
| `tool_images` digest | **해당 없음** — 위와 같은 이유 |
| `--gpu-memory-utilization` | **0.45**, 분모 물리 통합메모리 **119.7 GiB** → 53.86 GiB (기동 로그 `Desired GPU memory utilization is (0.45, 53.86 GiB)`) |
| 동시에 떠 있던 다른 서버 | **없음** — 8002 단독(`ss -ltn` 확인) |
| 원문 산출물 보존 위치 | 위 JSON 3건 + stderr 3건(**전부 0바이트**) |
| 서빙 서버 stdout | `/srv/tenants/ext01/artifacts/logs/serve_30b_bench_20260902_135120.log` (70,618 B, 보존) |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| 실패 | **세 판 × 네 수준 전부 0건** — 재측정의 목적이 이것이었고 달성됐다 |
| `tok_s_single` | **재현된다** — 52.96 / 53.06 / 52.36 (산포 1.34%) |
| 배치 수치 | **캐시 온도의 함수다** — `tok_s_batch32`가 59.45 / 68.64 / 107.32(**1.81배**). 세 수준 각각에서 적중률 순서 = tok/s 순서, **역전 0건** → [#128](https://github.com/Yunse4l/whitehat-boss/issues/128) |
| 옛 주석 「배치가 단일보다 느리다」 | **반증됐다.** 두 판 모두 batch32 > single |
| 새 주석으로 「배칭 이득이 거의 없다」 | **쓸 수 없다.** 1판은 1.18배, 3판은 2.31배다 |
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
| 명령 | `python3 -u -m src.cli bench --profile b1_single --device atom --roles auditor --concurrency 8 16 32` (2판만 `--apply` 추가). 세 판 모두 같은 명령·같은 커밋 |
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

## 3. 실험 — `cli bench --roles auditor --concurrency 8 16 32` ×3

### 3.1 세 판

1·2판은 11분 간격, 3판은 그로부터 49분 뒤다. **서버는 세 판 내내 같은 프로세스**
(pid 2894437)이고 재기동하지 않았다.

| 동시성 | 1판 tok/s | 2판 tok/s | 3판 tok/s | 최대/최소 |
| :-: | --: | --: | --: | :-: |
| 1 | 52.96 | 53.06 | 52.36 | **1.01** |
| 8 | 62.61 | 118.33 | 121.05 | 1.93 |
| 16 | 58.33 | 128.51 | 106.36 | 2.20 |
| 32 | 59.45 | 68.64 | 107.32 | **1.81** |

**실패는 세 판 네 수준 전부 0건이다.**

호출당 출력 토큰(계산값 `output_tokens ÷ 동시성`)은 202~214에 모여 있다 — **한 칸만
예외로 2판 동시성 32가 330이다.** 그 칸은 응답이 1.6배 길었으므로 `tok_s`의 분자와 분모가
함께 커졌고, 속도인지 길이인지 그 수치만으로는 못 가른다. 3판의 같은 칸이 202로 돌아왔다.

### 3.2 산포가 아니다 — prefix cache 적중률이 함께 움직인다

`_one_level`이 **수준마다** `prefix_cache_delta`를 남긴다. 그 값이 답을 들고 있었다.

| 동시성 | 1판 적중률 → tok/s | 2판 적중률 → tok/s | 3판 적중률 → tok/s |
| :-: | --: | --: | --: |
| 1 | 0.9996 → 52.96 | 0.9996 → 53.06 | 0.9996 → 52.36 |
| 8 | **0.6356** → 62.61 | **0.9996** → 118.33 | **0.9996** → 121.05 |
| 16 | **0.5142** → 58.33 | **0.9535** → 128.51 | **0.8842** → 106.36 |
| 32 | **0.5142** → 59.45 | **0.7776** → 68.64 | **0.8577** → 107.32 |

**동시성 8·16·32 각각에서, 세 판을 적중률로 정렬하면 tok/s 정렬과 정확히 같다 — 아홉
칸에 역전이 없다.** 그리고 **적중률이 세 판 모두 같은 유일한 수준(동시성 1)만 tok/s
산포가 1.34%다** — 통제군이 붙은 셈이다.

3판이 이 설명의 예측을 확인했다. 동시성 8에서 2·3판이 둘 다 적중률 0.9996이고 tok/s가
118.33 / 121.05로 **2.2% 안에 든다** — 데워진 상태는 유지되고 그 상태의 처리량은
재현된다. 동시성 16·32는 적중률이 판마다 달랐고 tok/s가 그만큼 따라 움직였다.

**동시성 32의 적중률이 낮은 이유는 용량이다.** 32 동시 × 약 18,224 토큰 ≈ 583K가 동시에
살아 있어야 하는데 KV cache가 711,584 토큰이라(§2) 캐시된 접두사가 밀린다. 그 수준만
세 판이 0.5142 / 0.7776 / 0.8577로 계속 달랐다.

**그래서 세 판 중 어느 것도 운영 처리량이 아니다.** `bench.py`의 `_filler` docstring이
이 함정을 미리 적어 뒀다 — *"본문까지 완전히 같으면 서버가 body 접두사도 캐시해 prefill이
거의 사라지고, 그러면 C(처리량)가 **운영보다 낙관적으로** 나온다. 운영에서는 후보마다
body가 다르므로 공유되는 것은 prefix뿐이어야 한다."* 그 장치는 **한 판 안에서**
`variant`로 본문을 흔든다(`bench.py:432`, `variant in range(level)`). **판을 다시 돌리는
것은 막지 못한다** — seed가 고정이라 다음 판의 프롬프트 집합이 앞 판과 동일하고, 서버가
그것을 그대로 돌려준다. 이슈로 열었다 —
[#128](https://github.com/Yunse4l/whitehat-boss/issues/128).

**따라서 registry의 68.64는 적중률 0.7776짜리 데워진 값이고, 1판의 59.45(0.5142)가
운영에 더 가깝다.** 어느 쪽도 "운영 처리량"이라고 부를 수는 없다 — 1판조차 동시성 8에서
0.6356으로 절반 넘게 맞았다. **그리고 안전한 운용 순서가 나쁜 쪽을 고른다**: "먼저 보고
나서 `--apply`"로 돌리면 `--apply` 판이 두 번째가 되어 데워진 값이 들어간다. 이번이 정확히
그 경우다.

**아직 안 갈린 것**: 1판의 0.51~0.64가 접두사 공유만으로 설명되는가(캐시가 완전히 빈
상태의 하한선을 안 쟀다 — 서버 재기동이 필요하다).

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
| 캐시가 빈 상태의 하한선 | 세 판 다 같은 서버 프로세스라 캐시가 이월됐다. 1판조차 동시성 8에서 0.6356이다 | 서버 재기동 후 한 판, 또는 `--enable-prefix-caching` 없이 한 판 |
| 1판의 0.51~0.64가 무엇의 적중인가 | 접두사 공유인지 앞 절(`structured_output`·`prefix_cache`)이 데운 것인지 안 갈랐다 | 절 순서를 바꿔 한 판 |
| ctx 32768의 KV 점유 | 프롬프트 + `output_max`가 `--max-model-len`을 넘어 400 | [#126](https://github.com/Yunse4l/whitehat-boss/issues/126) — 또는 `--max-model-len`을 36,864 이상으로 |
| `mem_gb_ctx8k` | `nvidia-smi`가 `[N/A]` | 8/25 보고서 §7의 방법 |
| §3.3의 배치가 언제 바뀌었나 | 오늘 상태만 봤다 | logind 설정과 세션 생성 경로 확인 |
| §3.4에서 flashinfer가 왜 안 깨졌나 | 안 갈랐다 | nvcc 유무 · 0.27.1 프리빌트 여부 확인 |

## 6. 반증 조건

- **「실패 0」이 반증되려면**: 같은 인자로 돌린 판에서 `failures`가 비지 않으면 된다. 두
  판이 전부 0이었다.
- **「차이의 원인이 prefix cache다」가 반증되려면**: 어느 동시성 수준에서든 세 판을
  적중률로 정렬한 순서가 tok/s 정렬과 어긋나면 된다. **아홉 칸에 역전이 0건**이었고,
  적중률이 세 판 모두 같은 동시성 1만 tok/s 산포가 1.34%였다.
- **「배칭 이득이 있다/없다」는 어느 쪽도 주장하지 않는다.** 1판은 없다고, 2판은 있다고
  말한다. 이 보고서는 그 둘이 갈렸다는 것까지만 주장한다.

## 7. 후속 과제

1. **[#128](https://github.com/Yunse4l/whitehat-boss/issues/128)** — `bench`가 판마다
   같은 프롬프트를 보내 두 번째 판부터 자기 캐시를 맞는다. 세 판의 실측이 그 이슈의
   근거다. 선택지 (a)(판 구분자를 섞는다)는 **재현성과 맞바꾸는 설계 판단**이라 거기서
   정하지 않았다.
2. [#126](https://github.com/Yunse4l/whitehat-boss/issues/126) — context 스윕이 서버
   상한을 모른다.
3. §3.3의 cgroup 배치를 확인하고 확정 #4의 회계 서술을 그에 맞춘다.
4. §3.4로 확정 #3 ②의 서술 범위를 좁힌다.
