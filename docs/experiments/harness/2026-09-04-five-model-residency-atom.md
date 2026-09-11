# 검색 역할까지 다섯을 올린다 — 어디까지 올라가는가 · ATOM

> **갱신 (2026-09-09) — 본문은 그대로 두고 §8 「이것이 답이다」 3번의 근거만 덧붙인다.**
> 그 항이 *"`execution.mode`·`swap` 키가 스키마에 없고 `extra="forbid"`다"*를 배선이 없는
> 근거로 들고 2026-09-03 보고서 §7-5를 인용한다. **그 근거는 없어졌다** — 2026-09-09에
> `ProfileEntry.execution`이 들어와 두 키를 적으면 로드는 통과한다
> ([ADR 0009](../../decisions/0009-stage-unit-execution-and-model-swap.md) 결과 1).
>
> **「하네스에 배선이 없다」는 그대로다** — 러너가 아직 단계 단위가 아니고 sleep/wake를 부르는
> 코드도 없다(같은 ADR 결과 2·3). 이 실험의 관측과 「이 배분에서는 아니오」라는 답도 그대로다.
> 바뀐 것은 3번이 든 **이유** 하나다(CLAUDE.md §2.3).

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 시각 | 2026-09-04 13:23~16:0x KST (vLLM 로그 타임스탬프 기준) |
| 장비 | `atom-host` (GB10, 물리 통합메모리 119.7 GiB) |
| vLLM | 0.27.1 (`/srv/tenants/user/venv-probe`) |
| `repo_commit` | `f47af57` → `b613d71` (`feat/local-balanced-hybrid-profile`). 측정 중 `--apply`로 한 커밋 늘었다 |
| 프로필 | `local_balanced_hybrid` (PR #138, 머지 전) |
| 실제 서빙 모델 | scout `hf://nvidia/Qwen3-8B-NVFP4@ccd10a893cbca613259517c3efe08e151ddf2b8e` · auditor `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` · critic `hf://mistralai/Devstral-Small-2-24B-Instruct-2512@55c5b41e98c2dbd21b0c8afffc540dcfc9eb5128` · embedding `hf://Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` · reranker `hf://Qwen/Qwen3-Reranker-0.6B@e61197ed45024b0ed8a2d74b80b4d909f1255473` |
| `--gpu-memory-utilization` 분모 | **119.7 GiB**(물리). cgroup 상한과 **다른 축**이다 — §5 |
| `--device` | `atom` |
| `tool_runner` | 선언 `docker`. **이 보고서의 측정에 도구가 없다** |
| `tool_images` digest | **해당 없음** (도구 미실행) |
| `run_id` | **해당 없음** — `cli bench`는 run 디렉터리를 만들지 않는다 |
| 서빙 서버 stdout | **보존** — `~/vllm_8001.log` ~ `~/vllm_8005.log` |
| 원문 산출물 | **보존** — `~/bench_hybrid_auditor_20260904_133046.json` · `~/bench_hybrid_scout_20260904_135623.json` · `~/bench_apply_*.json` |
| 동시에 떠 있던 다른 서버 | 절마다 명시한다. **이 실험의 주제가 그것이다** |

## 1. 요약

**경로 성립 주장과 성능 주장을 갈라 적는다.**

| 항목 | 결과 |
| --- | --- |
| **검색 둘이 나머지와 함께 뜨는가** | **뜬다.** 8001·8002·8004·8005 네 서버 동시 상주(32768) |
| **다섯이 뜨는가** | **이 배분으로는 아니다.** critic까지 넣은 32768에서 **4/5**까지 갔고 embedding이 CUDA OOM |
| **제약이 무엇인가** | **상주 개수가 아니다.** 넷의 가중치 합은 27.66 GiB뿐이고 나머지 64.35 GiB가 KV cache다 — KV는 `--gpu-memory-utilization`으로 우리가 정한다 |
| **cgroup 80 GiB가 걸리는가** | **안 걸린다.** vLLM이 `user-1004.slice` **밖**(`system.slice/ssh.service`)에 있다 |
| **기동을 실제로 막은 것** | **페이지 캐시.** 모델 파일을 읽은 만큼 캐시가 차고 CUDA 할당이 그 회수를 못 끌어낸다 |
| 그 대응 | **루트 없이 된다** — `posix_fadvise(DONTNEED)`로 캐시 35 → 8 GiB, 여분 81 → 108 GiB |
| **critic(devstral)** | **처음 띄웠다.** registry의 `quantization: nvfp4`가 **틀렸다 — 실제 fp8**이고 `PixtralForConditionalGeneration`(VLM)으로 해석된다 |
| scout 처리량 | **첫 실측** `tok_s_single 35.78` · `tok_s_batch32 388.46`(적중률 0.9981) |
| auditor `tok_s_batch32` | **0에서 풀렸다** — 66.42(적중률 0.5673). 값이 확정된 것이 아니라 **적중률과 함께 적을 수 있게 된 것**이 변화다(#133) |
| `doctor` 자기모순 | **이 프로필에는 없다.** 배선 절이 다섯을 찍고 checksum 경고는 critic 하나만 부른다 |
| 리랭커 순위 | **미검증.** 짧은 조각에서 역전이 3/3 결정론적이고 긴 문장에서는 맞았다. **우리 입력 모양으로는 안 쟀다** |

## 2. 실험 환경

- 환경변수(전 기동 공통): `VLLM_USE_DEEP_GEMM=0 VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo`
- **기동은 한 번에 하나씩** 했다. 사유는 §4.2.
- 모델은 전부 HF 캐시에서 repo id + `--revision`으로 서빙했다. 로컬 경로를 주면 vLLM이 그것을 repo id로 해석해 죽는다(`OSError: Repo id must be in the form ...`).

## 3. 실험 1 — 검색 둘을 포함한 네 서버 동시 상주

### 목적

**경로 성립 판정이다.** `config/profiles.yaml`의 `local_balanced` retriever 주석이 든 근거 중 「ATOM에 모델 5개를 동시에 띄워야 한다」가 실제로 어떤 제약인지 가른다.

### 조건

`--max-model-len 32768`, critic 제외 넷. 큰 것부터 하나씩 기동.

### 결과

| 포트 | 역할 | util (요청 GiB) | 기동 시점 여유 | 가중치+non-torch | act | CUDAGraph | KV | `nvidia-smi` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8002 | auditor (nvfp4) | 0.42 (50.27) | 111.93 | **18.28** | 1.84 | 2.13 | 30.14 | 51,214 MiB |
| 8001 | scout (nvfp4) | 0.18 (21.55) | 56.30 | **6.38** | 1.15 | 1.54 | 14.01 | 22,869 MiB |
| 8004 | embedding (fp16) | 0.10 (11.97) | 31.28 | **1.62** | 0.46 | 0.43 | 9.89 | 11,925 MiB |
| 8005 | reranker (fp16) | 0.10 (11.97) | 16.72 | **1.38** | 0.28 | 0.39 | 10.31 | 12,371 MiB |
| | **합** | 0.80 | — | **27.66** | — | — | **64.35** | **96.07 GiB** |

호스트: `free -g` 총계 119 · 사용 111 · 여분 6.

엔드포인트 넷이 각자 자기 `--served-model-name`을 돌려줬다 — `qwen3-8b` · `qwen3-coder-30b-a3b` · `qwen3-embedding-0.6b` · `qwen3-reranker-0.6b`.

### 해석

**① 가중치는 27.66 GiB뿐이고 KV가 64.35 GiB다.** 「5개를 띄운다」의 비용 대부분이 **우리가 정하는 값**이다. 그 주석이 상주 개수를 제약으로 든 것은 **좁혀야 한다** — 제약은 KV 예산 배분이다.

⚠ **그 주석의 나머지 절반은 이 실험이 건드리지 않는다.** R1(sm_121 커널 호환성)은 커널이 도는가의 문제이고 메모리와 다른 축이다. 검증 지점이 늘어나는 것은 그대로다.

**② `nvidia-smi` PID 합(96.07)과 `free`의 사용(111)이 약 15 GiB 갈린다.** 이 차의 내역은 **가르지 않았다.** 프레임워크·CUDA 컨텍스트로 짐작되지만 확인하지 않았다 — §8에 미측정으로 남긴다.

### 근거 로그

```
8002: Free memory on device (111.93/119.7 GiB) on startup. Desired GPU memory utilization is (0.42, 50.27 GiB).
      Actual usage is 18.28 GiB for consumed memory (weights + non-torch), 1.84 GiB for peak activation,
      and 2.13 GiB for CUDAGraph memory. Current kv cache memory in use is 30.14 GiB.
      GPU KV cache size: 658,512 tokens
      Maximum concurrency for 32,768 tokens per request: 20.10x
8001: Free memory on device (56.3/119.7 GiB) ... Actual usage is 6.38 GiB ... kv cache 14.01 GiB.
8004: Free memory on device (31.28/119.7 GiB) ... Actual usage is 1.62 GiB ... kv cache 9.89 GiB.
8005: Free memory on device (16.72/119.7 GiB) ... Actual usage is 1.38 GiB ... kv cache 10.31 GiB.
```

## 4. 실험 2 — 기동 실패 셋과 그 원인

**셋 다 인자 문제가 아니었다.** `--runner pooling`도 `--hf-overrides`도 정상으로 먹혔다 — 둘 다 KV 할당 단계까지 갔다.

### 4.1 KV 예산을 너무 짜게 줬다

```
8004 @ util 0.06:
ValueError: To serve at least one request with the model's max seq len (32768),
3.5 GiB KV cache is needed, which is larger than the available KV cache memory (2.69 GiB).
Based on the available memory, the estimated maximum model length is 25152.
```

`0.10`으로 올려 해결했다. **에러가 최소치를 알려준다** — 예산은 추정하지 말고 실패시켜 읽는 편이 빠르고 정확하다.

### 4.2 프로파일링 중 여유가 **늘어나면** 죽는다

```
8005 (8004 두 개와 동시 기동 중):
AssertionError: Error in memory profiling. Initial free memory 37.38 GiB,
current free memory 44.99 GiB. This happens when other processes sharing the same
container release GPU memory while vLLM is profiling during initialization.
```

죽어가던 8004가 메모리를 뱉는 동안 8005가 프로파일링을 하고 있었다.

> **이것이 「한 번에 하나씩」의 근거다.** 통합메모리 장비에서 여러 모델을 올리는 절차에 직접 걸린다 — **총량이 남아도 동시에 올리면 죽는다.** 값이 줄어드는 쪽이 아니라 **늘어나는 쪽**을 어설션이 잡는다는 것이 직관과 반대다.

### 4.3 순수 OOM — 페이지 캐시

```
8001 @ util 0.14 (devstral 0.30 + auditor 0.26 이 떠 있는 상태):
gpu_worker.py:395  init_device → MemorySnapshot(device=...)
torch.AcceleratorError: CUDA error: out of memory
```

CUDA 컨텍스트를 만드는 **첫 호출**에서 죽었다. 그 시점 `free -g`: 사용 78 · **여분 7** · 버퍼/캐시 35.

캐시 35 GiB는 devstral 24 GiB + auditor 17 GiB를 읽으면서 리눅스가 잡은 것이고, **CUDA 할당 경로가 그 회수를 끌어내지 못한다.**

## 5. cgroup — 80 GiB는 우리 vLLM을 구속하지 않는다

`docs/model-serving.md` §1이 우리 슬라이스의 하드 한도를 80 GiB로 적는다. 실측:

```
/sys/fs/cgroup/user.slice/user-1004.slice/memory.max     = 85,899,345,920  (80 GiB)   ← 문서와 일치
/sys/fs/cgroup/user.slice/user-1004.slice/memory.current =    541,921,280  (0.5 GiB)
vLLM PID의 cgroup:  0::/system.slice/ssh.service
```

**vLLM이 그 슬라이스 밖에 있다.** SSH로 띄운 프로세스라 `system.slice/ssh.service`로 간다. 그래서 52 GiB를 쓰는 서버가 떠 있는데 `memory.current`가 0.5 GiB다. 2026-09-02 보고서 §3.3의 관측이 그대로 재현됐다.

| 축 | 값 | 누가 보나 |
| --- | --- | --- |
| 물리 통합메모리 | 119.7 GiB | `free` · `nvidia-smi` · vLLM. `--gpu-memory-utilization`의 분모 |
| 슬라이스 cgroup `memory.max` | 80 GiB | 커널. **오늘 우리 vLLM에는 안 걸렸다** |

⚠ **「안 걸린다」는 보장이 아니라 프로세스 배치의 부산물이다.** 그리고 뒤집으면 **cgroup의 보호도 못 받는다** — `memory.high`도 풀려 있어 완충 없이 바로 OOM이고, §4.3이 그 사례다.

## 6. 페이지 캐시를 루트 없이 떨어뜨린다

`drop_caches`는 권한이 없다:

```
$ echo 3 | sudo -n tee /proc/sys/vm/drop_caches
sudo: 암호가 필요합니다
$ echo 3 > /proc/sys/vm/drop_caches
-bash: /proc/sys/vm/drop_caches: 허가 거부
```

**파일 단위 `posix_fadvise(POSIX_FADV_DONTNEED)`는 소유자 권한으로 된다.** 그 캐시는 우리가 읽은 모델 파일에서 온 것이라 전부 우리 소유다.

```python
import os, glob
for f in glob.glob('/srv/tenants/user/.cache/huggingface/hub/**/*.safetensors', recursive=True):
    fd = os.open(f, os.O_RDONLY)
    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    os.close(fd)
```

**결과 (44개 파일):**

| | 사용 | 여분 | 버퍼/캐시 |
| --- | --- | --- | --- |
| 전 | 4 | 81 | 35 |
| 후 | 4 | **108** | **8** |

**기동 사이마다 돌려야 한다.** 모델을 읽을 때마다 캐시가 다시 차기 때문이다.

## 7. 실험 3 — critic(devstral)을 처음 띄웠다

### 목적

registry가 `source: null`·`checksum: 0`으로 두고 주석이 *"받지도 띄우지도 않았다"*고 적어 온 모델이다. **띄우는가**를 판정한다.

### 결과

HF 캐시에 이미 받아져 있었다(2026-08-25). **registry 서술과 어긋난다.** 다만 「받아져 있다」와 「띄웠다」는 다르고, 후자는 오늘이 처음이다.

**포맷이 mistral 네이티브라 전용 로더 인자가 필요하다:**

```
--tokenizer-mode mistral --config-format mistral --load-format mistral
```

스냅샷에 `consolidated-*.safetensors` · `params.json` · `tekken.json`만 있고 HF transformers 포맷 파일이 없다.

| 항목 | 값 |
| --- | --- |
| vLLM 해석 아키텍처 | **`PixtralForConditionalGeneration`** |
| `config.json` `architectures` | `['Mistral3ForConditionalGeneration']`, `model_type: 'mistral3'` |
| 양자화 | **`quant_method: 'fp8'`** · vLLM 로그 `quantization=fp8` |
| dtype / KV | `torch.bfloat16` / `kv_cache_dtype=auto` |
| 파일 크기 | 18,921 MiB + 5,678 MiB = **24.0 GiB** |
| 로딩 | 24.12 GiB · 173.2초 (전체 콜드 ~330초) |
| util 0.45 | 여유 112.76 → 가중치+non-torch **26.8** · act 0.76 · CUDAGraph 1.52 · KV 26.3 · `nvidia-smi` 53,330 MiB |
| util 0.30 | 여유 113.21 → 가중치+non-torch **25.48** · act 0.76 · CUDAGraph 1.46 · KV 9.67 |

### registry·문서와 어긋나는 것 셋

1. **`quantization: "nvfp4"` → 실제 `fp8`.** 파일 크기가 24 GiB로 24B × 1바이트이고(bf16이면 약 48 GiB), `config.json`과 vLLM 로그가 둘 다 fp8이다.
2. **멀티모달이다.** `modules_to_not_convert`에 `model.vision_tower`·`model.multi_modal_projector`가 있고 vLLM이 `Pixtral…`로 해석한다. `model-serving.md` §2는 이 모델을 *"dense 24B, tool-use 지원"*으로만 적고 vision을 말하지 않는다.
3. **`source: null`을 채울 자격이 생겼다.** registry 주석의 조건(*"받아서 띄운 것만 `source`에 적을 자격이 있다"*)이 충족됐다.

⚠ **checksum은 아직 안 만들었다.** `tools/model_checksum.py`가 24 GiB를 읽으면 §6의 페이지 캐시가 다시 차서, 돌고 있는 서버를 §4.3의 방식으로 죽인다. **서버를 내린 뒤에 한다.**

## 8. 실험 4 — 32768 다섯은 이 배분으로 안 들어간다

§6으로 여분을 108 GiB로 만든 뒤, 기동 사이마다 캐시를 떨어뜨리며 큰 것부터 올렸다.

| 순서 | 포트 | util | 결과 | 사용 | 여분 | 캐시 |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | — | — | 시작 | 4 | 108 | 8 |
| 1 | 8003 devstral | 0.30 | **UP** | 43 | 44 | 32 |
| 2 | 8002 auditor | 0.26 | **UP** | 77 | 17 | 25 |
| 3 | 8001 scout | 0.14 | **UP** | **99** | **8** | 14 |
| 4 | 8004 embedding | 0.08 | **CUDA OOM** | 99 | 14 | 8 |
| 5 | 8005 reranker | 0.08 | **UP** | 111 | 7 | 3 |

**4/5다.** 큰 셋의 요청 합은 0.70 = 83.8 GiB인데 그 시점 `free`의 사용이 **99 GiB**다 — §3 해석 ②의 15 GiB 차와 같은 크기의 어긋남이고, **예산을 비율로만 잡으면 그만큼 빗나간다.**

```
8004: AcceleratorError: CUDA error: out of memory
```

### 이것이 답이다

**「32768로 다섯을 올릴 수 있는가」의 답은 이 배분에서는 아니오다.** 남은 길 셋 — 어느 것도 오늘 재지 않았다:

1. **`--max-model-len`을 낮춘다.** auditor 프롬프트가 18,224 토큰이고 `output_max`가 4,096이므로 22,320이면 요청이 들어간다. 32768은 그보다 크다.
2. **`--kv-cache-memory`를 절대값으로 준다.** vLLM이 기동 로그에서 직접 값을 권한다.
3. **교대한다.** 2026-09-03이 확인한 sleep/wake(왕복 2~6초, 약 51 GiB 반환). **하네스에 배선이 없다** — `execution.mode`·`swap` 키가 스키마에 없고 `extra="forbid"`다(그 보고서 §7-5).

## 9. 실험 5 — bench (scout 첫 실측 · auditor `tok_s_batch32` 해제)

### 조건

`cli bench --profile local_balanced_hybrid --device atom --roles scout auditor --concurrency 8 16 32 --apply`. 네 서버 동시 상주 상태. 서버는 `--enable-prefix-caching`으로 떠 있다.

### 결과

**scout (`qwen3-8b`) — registry가 전부 0이었다:**

| 동시성 | tok/s | 적중률 | 실패 |
| --- | --- | --- | --- |
| 1 | 35.57 | 0.9983 | 0 |
| 8 | 180.65 | 0.9983 | 0 |
| 16 | 274.17 | 0.9982 | 0 |
| 32 | **386.45** | 0.9981 | 0 |

`--apply` 판: `tok_s_single 35.78` · `tok_s_batch32 388.46` · `prefix_hit_ratio_batch32 0.9981`.

**auditor (`qwen3-coder-30b-a3b`):**

| 동시성 | tok/s | 적중률 | 실패 |
| --- | --- | --- | --- |
| 1 | 54.35 | 0.9996 | 0 |
| 8 | 54.84 | 0.6758 | 0 |
| 16 | 60.71 | 0.5142 | 0 |
| 32 | 61.66 | 0.5142 | 0 |

`--apply` 판: `tok_s_single 53.4` · `tok_s_batch32 66.42` · `prefix_hit_ratio_batch32 0.5673`.

### 해석

**① 두 모델의 배칭 성질이 완전히 다르다.** scout는 1 → 32에서 **10.9배**이고 32에서도 포화하지 않았다. auditor는 **1.13배**다.

**② 적중률의 거동도 다르다.** scout는 네 수준 내내 0.998로 평평하고, auditor는 0.9996 → 0.5142로 무너진다. **그래서 두 수를 같은 축에 놓으면 안 된다** — scout의 386은 프리필이 거의 다 캐시에 맞은 상태의 디코드 처리량이다.

**③ `tok_s_batch32`가 0이었던 것은 미실측이 아니라 판단이었다.** 2026-09-02에 같은 서버·같은 인자로 돌린 세 판이 59.45 / 68.64 / 107.32로 1.81배 갈렸고 원인이 적중률이라, 어느 값을 적어도 「몇 번째 판이냐」의 함수였다(#128). **#133이 `prefix_hit_ratio_batch32`를 만들면서 값과 캐시 상태를 같은 레코드에 적을 수 있게 됐고, 그래서 이제 적는다.**

⚠ **캐시 ON에서 나온 값이다.** 2026-09-02 캐시 OFF 참값은 39.64다. **두 수를 같은 축에 놓지 마라** — 옆 필드의 적중률이 그 구분의 근거다.

**④ `mem_gb_ctx8k`는 둘 다 0으로 남는다.** `nvidia-smi --query-gpu`가 통합메모리 장비에서 `[N/A]`를 준다. 이 실험은 context를 8192로 고정하지 않았다(32768).

## 10. 실험 6 — 검색 두 역할의 응답과 `doctor` 화면

### embedding

```
POST /v1/embeddings  {"model":"qwen3-embedding-0.6b","input":[2건]}
→ vectors 2, dim 1024, usage {prompt_tokens: 10, total_tokens: 10}
```

`src/adapters/vllm_retrieval.py`의 docstring이 적은 값(*"실제 모델(0.6B는 1024)"*)과 맞는다.

### rerank

`--hf-overrides '{"architectures":["Qwen3ForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}'` 로 띄웠다. 그 설정 없이는 404가 나는 경로다(어댑터 docstring, 2026-08-11 실측).

**200을 준다.** 다만 순위에 관측이 둘이고 서로 다르다 — §11.

### `doctor --profile local_balanced_hybrid --device atom`

```
[OK  ] 역할 5개가 모두 registry 키로 해석됐다: ['auditor','critic','embedding','reranker','scout']
[OK  ] retriever    → HybridRetriever
[OK  ] embedding    → VllmEmbeddingClient   (http://127.0.0.1:8004/v1)
[OK  ] reranker     → VllmRerankerClient    (http://127.0.0.1:8005/v1)
[WARN] model checksum이 자리표시자(전부 0)다: critic(devstral-small-2-24b, source=미상)
```

**#108이 `b1_single`에서 지적한 모순이 이 프로필에는 없다.** 그쪽은 배선 절에 embedding·reranker가 **안 나오는데** checksum 경고가 그 둘을 불렀다. 여기서는 배선 절이 다섯을 다 찍고 경고는 **critic 하나만** 부른다 — 두 절이 일치한다.

⚠ **#108이 닫혔다는 뜻이 아니다.** 이 프로필은 `retriever: hybrid`라 그 이슈의 버그 경로를 타지 않을 뿐이고, `retriever: fake` 프로필에서 세는 방식은 그대로다.

## 11. 리랭커 순위 — 미검증 항목

**관측 1 — 짧은 조각. 순위가 뒤집혔고 3회 반복 전부 동일하다.**

```
query: "buffer overflow"
[1] 0.4772  "the weather is nice"
[0] 0.2904  "strcpy without bounds check"
```

**관측 2 — 긴 서술문 4건. 순위가 맞았다.**

```
query: "buffer overflow vulnerability in C string handling"
0.3015  [0] strcpy copies without checking the destination buffer size, causing a stack overflow
0.1520  [2] memcpy with attacker-controlled length overflows the heap chunk
0.1106  [3] Recipe: preheat the oven to 180 degrees and bake for 25 minutes
0.0671  [1] The cat sat on the mat and purred quietly in the afternoon sun
```

**부수 관측**: 두 호출의 점수 스케일이 다르다(각각 0.4772와 0.3015가 1등). **호출 간 절대 비교가 안 된다.**

### 왜 「리랭커가 틀렸다」로 적지 않는가

우리가 실제로 넣는 것은 둘 중 어느 쪽도 아니다. `src/adapters/hybrid_retriever.py`를 열어 확인한 실경로:

| | 실경로 |
| --- | --- |
| documents | **40줄 단위로 자른 소스 조각**(`CHUNK_LINES=40`), `strip()`된 원문 |
| query | **식별자 한 개.** `_query_text()`는 `심볼 + keywords`지만 러너의 두 호출부(`runner.py:1095`·`1108`)가 `keywords`를 안 넘긴다 |
| 개수 | embedding이 top_k=32로 좁힌 뒤 리랭커가 top_n=5 |

**문서는 관측 2보다 길고 질의는 관측 1보다 짧다.** 어느 관측도 실경로 모양이 아니므로 **재는 것 말고 답이 없다.**

### 왜 지금 물어야 하나

`_fit_related`가 리랭커 순서대로 담다가 **예산 초과 첫 조각에서 `break`** 한다. 순위가 표시 순서가 아니라 **캡슐에 들어가느냐 마느냐**를 정하고, 그 본문이 산문과 사실 블록 양쪽으로 프롬프트에 실린다.

**오늘 이것으로 깨지는 run은 0건이다.** `origin/main`에는 `VllmRerankerClient`를 부르는 프로필이 없다 — hybrid인 둘은 reranker가 fake이고, 실reranker를 선언한 둘은 retriever가 fake라 서로 만나지 않는다. **둘을 처음 만나게 하는 것이 `local_balanced_hybrid`(PR #138)다.**

## 12. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| **`mem_gb_ctx8k`** | context를 8192로 고정하지 않았다(32768) | `--max-model-len 8192`로 다섯을 올리고 다시 잰다 |
| **32768 다섯** | 이 배분에서 4/5까지다 | §8의 셋 중 하나 |
| **리랭커 순위(실경로 모양)** | 40줄 코드 조각 × 식별자 질의로 안 쳤다 | 그 모양으로 6건 이상, 정답이 명확한 구성으로 |
| **`nvidia-smi` 합과 `free` 사용의 15 GiB 차** | 내역을 안 갈랐다 | 프로세스별 RSS와 CUDA 컨텍스트를 따로 센다 |
| **devstral checksum·처리량** | 서버가 떠 있는 동안 24 GiB를 읽으면 §4.3으로 다른 서버가 죽는다 | 서버를 내린 뒤 `tools/model_checksum.py` |
| **캐시 OFF 처리량** | 이번 판은 전부 `--enable-prefix-caching` | `--no-enable-prefix-caching`으로 재기동해 한 판 |
| **다섯 상주에서의 bench** | 다섯이 안 떴다 | §8이 닫힌 뒤 |
| **재현 폭** | 각 구성을 한 번씩 찍었다 | 같은 구성을 3회 이상 반복 |

## 13. 반증 조건

- **「검색 둘이 함께 뜬다」가 반증되려면**: 8004·8005가 나머지와 함께 있을 때 `/v1/models`가 안 뜨면 된다. 넷 다 자기 이름을 돌려줬다.
- **「제약이 KV 예산이다」가 반증되려면**: 가중치 합이 KV 합에 근접하면 된다. 27.66 대 64.35다.
- **「cgroup이 안 걸린다」가 반증되려면**: vLLM PID의 cgroup이 `user-1004.slice`이거나 `memory.current`가 vLLM 사용량을 따라가면 된다. `system.slice/ssh.service`이고 0.5 GiB다.
- **「페이지 캐시가 막았다」가 반증되려면**: `fadvise` 후에도 여분이 안 늘면 된다. 81 → 108이다.
- **「devstral이 fp8이다」가 반증되려면**: 파일 크기가 24B × 2바이트에 가깝거나 `quant_method`가 다르면 된다. 24.0 GiB이고 `fp8`이다.
- **「리랭커 순위가 미검증이다」가 반증되려면**: 실경로 모양(40줄 조각 × 식별자 질의)의 관측이 있으면 된다. **없다.**

## 14. 후속 과제

1. **`config/models.yaml`의 devstral 항목을 고친다** — `quantization: nvfp4` → `fp8`, VLM 표기 추가, `source`·`checksum` 채우기. 서버를 내린 뒤.
2. **`docs/model-serving.md` §2**의 Devstral 행에 vision을 적는다.
3. **`config/profiles.yaml`의 `local_balanced` retriever 주석**을 좁힌다 — 「5개 동시 상주」가 아니라 「KV 예산 배분」이 제약이다. R1 쪽 근거는 그대로 둔다.
4. **`docs/portability.md`에 페이지 캐시 절차를 넣는다** — `posix_fadvise`가 루트 없이 되는 것과, 기동 사이마다 필요하다는 것.
5. **§8의 셋 중 하나를 골라 다섯을 채운다.**
6. **리랭커를 실경로 모양으로 잰다.** run B의 선행 조건이다.
7. **`mem_gb_ctx8k`를 8192 판으로 잰다.** §3 체크리스트 7개 중 계속 0이던 항목이다.
