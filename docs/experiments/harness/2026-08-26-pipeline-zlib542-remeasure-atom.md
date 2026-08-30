# 배선 수정 후 재측정 — zlib 542건 · ATOM

> 대상: `origin/main`(21da039)에 PR **#29·#30**(build 배선) · **#31**(실 토크나이저) ·
> **#32**(`critic_skipped` 이벤트) 세 갈래를 합친 브랜치 + 측정 전용 커밋 1개.
> **8단계 `gate_eval`이 세 run 만에 0을 벗어났다.** 원인은 `critic_top_n`이 아니라
> build 도구 배선이었고, D26의 원인 서술이 다시 한 번 바뀐다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 시각 | 시작 **2026-08-26 12:42:43 KST** · 종료 **13:21:00 KST** · 소요 **38 m 17 s** (2,297 s) |
| 실제 서빙 모델 | `hf://Qwen/Qwen3-4B-Instruct-2507-FP8@8591804019c8b22094c3b5b4454e0edc05dffc98` (별칭 `qwen3-4b`, fp8). **근거**: `/v1/models`의 `root`가 그 스냅샷 경로 — `models_20260826_124034.json`에 보존 |
| `repo_commit` | `7acf1b5e74b021d38b1a6773d543903aca554452` · dirty **False** |
| 브랜치 구성 | `measure/remeasure-20260826` = `origin/main`(`21da039`) + `feat/d21-budget-floor`(#29·#30) + `feat/d21-tokenizer`(#31) + `feat/d26b-critic-skipped`(#32) → `721d58c`, **세 병합 모두 충돌 0** · 그 위에 측정 전용 커밋 `7acf1b5` |
| 작업 위치 | `git worktree` `/srv/tenants/user/measure-20260826` — 공용 계정의 더러운 작업 트리(`config/profiles.yaml` 타인 WIP)를 건드리지 않기 위해 분리했다 |
| `run_id` | `run_20260826_0001` |
| run 산출물 경로 | `/srv/tenants/user/artifacts/run_20260826_0001` — **보존** |
| `tool_runner` | 프로필 선언 `fake` / 실제 **`docker`** (`--tool-runner docker` 오버라이드, ADR 0005). **다섯 번째 사례다** |
| `--device` | `atom` |
| `condition` | `B3prime@pipeline` (기본값) |
| `seed` | 20260901 |
| `kb_snapshot` | `kb-none` (자리표시자 — 경고 발생) |
| `source_hash` | `sha256:a9bec04a90454f343cb517794fe1f5570344c8d85d38db11109d27599a555c54` |
| `tool_images` digest | `harness-tools:v1` = `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17` (docker `RepoDigests`) — 8/25와 **동일** |
| `--gpu-memory-utilization` | `0.2` · 분모 119.7 GiB → **23.94 GiB**. 원문: `Free memory on device (112.01/119.7 GiB) on startup. Desired GPU memory utilization is (0.2, 23.94 GiB)` |
| 동시에 떠 있던 다른 서버 | **없음.** 기동 전 `ss -ltnp \| grep :800[0-9]` 0건 · `nvidia-smi` "No running processes found" · `ps \| grep vllm` 0건 |
| 서빙 서버 stdout | **`serve_20260826_124034.log` — 보존.** run 디렉터리로 복사했다 |

## 1. 요약

**무엇을 확인하려 했나**: 배선 결함 세 개를 고친 코드에서 **8단계 `gate_eval`이 0을
벗어나는가**. 이 run은 그 하나를 묻는다.

**답은 예다.** 그리고 원인 귀속이 8/25 보고서의 서술과 다르다.

| 항목 | 결과 |
| --- | --- |
| **8단계 `gate_eval`** | **8건** (8/25·8/23은 0건) — **D26 닫힘** |
| **등급 E3** | **8건** (직전 0건) · E4는 여전히 0건 |
| 원인 | `critic_top_n`이 아니라 **build 도구 배선**(`-I` 누락). 고치자 `has_repro_spec`이 11/55 → **77/78**로 뛰며 `static_ok`와의 배타가 해소됐다 |
| 다음 병목 | **이제는 `critic_top_n`이 맞다.** 59건이 6단계를 통과했는데 8건만 7단계에 갔고 그 8건이 **전부 upheld** → E3 8건은 상한값 그 자체다 |
| build 도구 | 2단계 ok **42/79** (직전 21/79) · 실제 실패 **6건**(exit=1) · 나머지 31건은 `.h`(exit=2, D16이 실패가 아니라고 정리한 항목) |
| `critic_skipped` (#32) | **51건** 전량 `over_critic_top_n` — 이 사실이 리포트에 처음 실렸다 |
| 실 토크나이저 (#31) | 카운터는 붙었다(세 모델 `REAL` 확인). **그러나 이 구성에선 무동작** — 캡슐이 예산 근처에 못 간다(§3 해석 ⑤) |
| `location_ok` | **78건 전부 false** (네 번째 확인) — D23 하향 판정 유지 |
| prefix cache | 서버 **30.0%** vs 장부 실서빙 **0.00%** — R6 두 번째 대조값, 이번엔 단계별로 갈렸다 |
| **신규 결함** | 9단계 `dedupe()`가 **E3 6건을 지운다** · 리포트에 **E 등급이 없다** · manifest에 **`profile`이 없다** (§3 해석 ⑦) |
| **신규 사실** | 캡슐 캐시 **604건 조회 / 적중 0건** — 키에 `line_start`가 들어가 심볼 단위 후보는 원리상 적중하지 않는다. **결함이 아니라 조건이다** (§3 해석 ⑧) |
| E4 | **이 프로필로는 측정 불가** — asan 8/8 `repro_failed`, PoC가 Fake auditor의 자리표시자다 (§3 해석 ⑨) |

**이 run이 태우지 않은 것**: 4-1(HTTP 400)·4-2(후보 단위 실패). **네 번째로 미실현**이다.

## 2. 실험 환경

| 항목 | 값 |
| --- | --- |
| 장비 | GIGABYTE AI TOP ATOM (NVIDIA GB10, sm_121, aarch64) · DGX OS 7.4.0 · 커널 6.11.0-1016-nvidia · 드라이버 580.95.05 / CUDA 13.0 |
| cgroup 상한 (실행 시점 실측) | `memory.max` **68,719,476,736** (64 GiB) · `memory.high` **`max`** ← **8/25의 62,277,025,792(58 GiB)에서 풀렸다** |
| vLLM 버전 | **미기재** — 기동 로그에서 해당 줄을 찾지 못했다 |
| attention backend | **FLASH_ATTN** (`['FLASH_ATTN','FLASHINFER','TRITON_ATTN','FLEX_ATTENTION']` 중 선택) — flashinfer 미선택이라 `VLLM_ATTENTION_BACKEND`는 주지 않았다 |
| KV cache | **17.88 GiB → 130,176 tokens** · 32,768 토큰 요청 기준 최대 동시 **3.97x** |
| 메모리 회계 | weights+non-torch **5.96 GiB** · peak activation **0.10 GiB** · CUDAGraph **0.52 GiB** (8/25는 0.82) |
| 프로필 | `single_gpu_12g` — **scout만 실서빙**, auditor·critic·retriever·embedding·reranker는 Fake |
| 대상 | zlib (`/srv/tenants/user/targets/zlib`) |

**갈리는 항목을 먼저 적는다** — 8/25 `run_20260825_0001`과 대조할 때:

| 축 | 8/25 | 오늘 | 비교 가능한가 |
| --- | --- | --- | --- |
| 장비·프로필·서버 인자·대상 | atom · `single_gpu_12g` · util 0.2 · 32768 | **동일** | ✅ |
| `candidate_max` | 1024 | **1024** | ✅ |
| `capsule_budget_tokens.expanded` | 18,000 | **18,000** | ✅ |
| tool 이미지 digest | `b5d7048b…` | **동일** | ✅ |
| 코드 | `393b38e` (PR #28) | `7acf1b5` (main + #29·#30·#31·#32) | ⚠ **의도된 변수** |
| `memory.high` | 58 GiB | `max` | ⚠ 이 run에선 미도달이라 무영향 |

**⚠의 뜻**: 코드가 갈린 것이 이 실험의 목적이다. 다만 **네 갈래가 한 run에 같이 들어갔으므로
개별 PR로 효과를 쪼개는 귀속은 여기서 하지 않는다.** 쪼갤 수 있는 것만 §3에서 쪼갠다.

## 3. 실험 — zlib 542건 재측정 (`run_20260826_0001`)

### 목적

배선 수정 후 **등급 사슬이 8단계까지 이어지는가**. 부수적으로 실 토크나이저(#31)와
`critic_skipped` 이벤트(#32)가 산출물에 실제로 나타나는가.

### 조건

| 항목 | 값 |
| --- | --- |
| 명령 | `python3 -m src.cli run --profile single_gpu_12g --device atom --tool-runner docker --artifacts-root /srv/tenants/user/artifacts --target /srv/tenants/user/targets/zlib` |
| thresholds (박제값) | `scout_threshold 0.5 · critic_top_n 8 · candidate_max 1024 · schema_retries 1 · queue_maxsize 256 · capsule_expansion_max 3 · requeue_max 2 · capsule light 4000 / expanded 18000 / focused 8000` |
| `budget` | `input_tokens 4,000,000 · output_tokens 400,000 · gpu_seconds 7,200 · tool_seconds 1,800` |
| `sampling` | `temperature 0.2 · top_p 0.95` (`top_k`는 하네스가 안 보낸다 — §3 해석 ⑫) |
| `prompt_versions` | scout v1 · auditor v1 · critic v1 |
| `model_checksums` | scout `sha256:ab78c8e5…` · 나머지 네 역할은 all-zero (Fake) |

### 결과

| 항목 | 결과 |
| --- | --- |
| 소요 | **38 m 17 s** (2,297 s) · **완주** · `stopped_after` null |
| trace 이벤트 | **7,048건** |
| `target_plan` | analyzable **79** · symbol_targets **542** · fallback 0 · no_symbol **31** · **signal 26** · planned **542** · deferred **0** · candidate_max 1024 |
| `graph_index` | loaded · files **48** · symbols **542** · calls **3,045** · unresolved_calls **207** · unresolved_symbols 0 · parse_errors **39** · skipped 0 |
| `model_call` | **612건** — scout 542 · auditor 62 · critic 8 |
| 후보 단위 실패 | **0건** — report.md에 `분석하지 못한 대상` 줄이 없다 = `deferred`·`dropped` 모두 0 (`report.py` render 조건) |
| 장부 (실서빙 542콜) | input **1,070,339** · output **86,224** · cached **0 (0.00%)** · gpu **2,127.3 s** |
| 장부 (Fake 70콜) | input **145,395** · output **11,518** · cached **19,094** |
| 장부 (전체 612콜) | input **1,215,734** · output **97,742** · gpu **2,127,309 ms** · tool **144,005 ms** |
| 후보 1건당 | wall **4.24 s** · gpu **3.92 s** · input **2,243 tok**(전체 기준) / **1,975 tok**(실서빙 기준) · output **180 / 159** |
| chars/token | **2.882** · bytes/char **1.145** · 계상배율 **1.388x** |
| 프롬프트 크기 (문자 기준, 612개) | 합 **3,503,291 chars** · p50 **4,164** · p90 **9,754** · max **41,640** (`prompts/0279_auditor.md`) |
| 프롬프트 크기 (토큰 기준) | **미기재** — 문자 수만 집계했고 8/25는 토큰 기준이라 직접 비교 불가 |
| `tool_call` | **254건 / 144,005 ms** — index 1 · semgrep 79 · clang-tidy 79 · build 87 · asan 8 |
| 캡슐 캐시 | **604건 조회 · 적중 0건** |
| `gate_eval` | 78건 — stage6 **62** · stage7 **8** · **stage8 8** |
| 등급 | **E2 59 · E0 3 · E3 16**(stage7 8 + stage8 8) · E1 0 · E4 0 |
| `critic_skipped` | **51건** (reason 전량 `over_critic_top_n`) |
| 리포트 | 19줄 · 중복 제거 **58건** · 실린 Finding **4건** |
| prefix cache (서버) | **30.0%** |
| Scout 점수 분포 | **미기재** — 집계 스크립트가 `state.db` 조회에서 끊겼다 |
| (a′) 앞256/뒤286 승격률 | **미기재** — 위와 같은 이유 |

### 근거 로그

**승격 사슬 — `policy_decision` 전량 (5단계만 `transition`)**

```
[stage 4] below_scout_threshold  480      scout_promoted        62
[stage 5] auditor_hypothesized    62      ← policy_decision이 아니라 transition에만 있다
[stage 6] insufficient_evidence    3      promoted_to_critic    59
[stage 7] critic_upheld            8
[stage 8] repro_failed             8
```

`542 = 480 + 62` · `62 = 59 + 3` · `59 = 51(critic_skipped) + 8` — 회계가 자기 자신과 맞는다.
**세 번째 항등식이 이번에 처음 검증 가능해졌다** — #32가 `critic_skipped`를 이벤트로 남기기
전에는 좌변을 셀 수 없었다.

**게이트 술어 (`gate_eval` data, 평평한 키 · 78건 합산)**

```
graph_ok        {True: 78}
path_ok         {True: 78}
static_ok       {True: 75, False:  3}
unblocked       {True: 75, False:  3}
has_repro_spec  {True: 77, False:  1}
location_ok     {False: 78}
upheld          {True: 16, False: 62}
p1 {T:75 F:3}   p2 {T:75 F:3}   p3 {T:16 F:62}   p4 {F:78}

[stage 6] 62건  {E2: 59, E0: 3}
[stage 7]  8건  {E3: 8}
[stage 8]  8건  {E3: 8}
E3 8건 = find_0002, find_0003, find_0011, find_0022, find_0024, find_0025, find_0026, find_0027
```

**도구 종료 코드**

```
index         1건 · ok={True:1}              · exit={0:1}               ·    414 ms
semgrep      79건 · ok={True:79}             · exit={0:66, 1:13}        · 68,704 ms
clang-tidy   79건 · ok={True:42, False:37}   · exit={0:19, 2:31, 1:29}  · 52,971 ms
build        87건 · ok={True:50, False:37}   · exit={0:50, 2:31, 1: 6}  · 17,352 ms
asan          8건 · ok={False:8}             · exit={1:8}               ·  4,564 ms
```

**build 실패 37건의 분해**

```
exit=2  31건  "build: 컴파일할 C 소스가 없다"   ← .h 파일. 컨테이너를 띄우지도 않는다 (D16)
exit=1   6건  "build 실패 (exit=1)"            ← 실제 실패
```

**8단계 도구 호출 16건 (Finding별 build → asan)**

```
find_0002  build ok  438 ms → asan fail  644 ms   contrib/iostream3/zfstream.cc
find_0003  build ok  277 ms → asan fail  399 ms   contrib/minizip/miniunz.c
find_0011  build ok  468 ms → asan fail  918 ms   contrib/minizip/zip.c
find_0022  build ok  384 ms → asan fail  502 ms   examples/gun.c
find_0024  build ok  321 ms → asan fail  547 ms   examples/gzlog.c
find_0025  build ok  319 ms → asan fail  514 ms   examples/gzlog.c
find_0026  build ok  305 ms → asan fail  535 ms   examples/gzlog.c
find_0027  build ok  347 ms → asan fail  505 ms   examples/gzlog.c

asan argv:  asan ./build.sh --sanitize=address ./poc <파일>
asan error: "asan: 빌드 단계에서 끊겨 프로그램이 실행되지 않았다"
```

**`critic_skipped` 51건 (#32가 처음 남긴 이벤트)**

```
reason          {over_critic_top_n: 51}
confidence      {0.6: 23, 0.3: 28}
has_repro_spec  {True: 50, False: 1}
표본: {"stage":7,"subject_id":"find_0028","data":{"reason":"over_critic_top_n",
       "confidence":0.6,"has_repro_spec":true}}
```

**리포트 산출물 전문 (19줄)**

```
# 취약점 분석 리포트 — run_20260826_0001
중복 제거: 58건
`critic_top_n`으로 잘린 Finding: 51건 (상한 8 · 목록은 trace의 `critic_skipped`)
## hypothesized (2건)
- inflateBack9에서 검증 없는 unknown (CWE-20, confidence 0.30)   find_0001 · 근거 3건
- gzdopen에서 검증 없는 sprintf   (CWE-787, confidence 0.60)     find_0041 · 근거 3건
## queued (2건)
- gzfilebuf::open_mode에서 검증 없는 strcpy (CWE-787, 0.60)      find_0002 · 근거 5건
- makedir에서 검증 없는 memcpy             (CWE-787, 0.60)      find_0003 · 근거 6건
```

**서빙 서버 stdout**

```
Using FLASH_ATTN attention backend out of potential backends:
  ['FLASH_ATTN', 'FLASHINFER', 'TRITON_ATTN', 'FLEX_ATTENTION'].
Available KV cache memory: 17.88 GiB
GPU KV cache size: 130,176 tokens
Maximum concurrency for 32,768 tokens per request: 3.97x
Free memory on device (112.01/119.7 GiB) on startup. Desired GPU memory utilization is
(0.2, 23.94 GiB). Actual usage is 5.96 GiB for consumed memory (weights + non-torch),
0.1 GiB for peak activation, and 0.52 GiB for CUDAGraph memory.
WARNING [model.py:1637] Default vLLM sampling parameters have been overridden by the
model's `generation_config.json`: {'temperature': 0.7, 'top_k': 20, 'top_p': 0.8}.
... Prefix cache hit rate: 30.0%   (종료 직전 20개 샘플 전부 29.7~30.0%)
```

**토크나이저 부착 확인 (run 직전, 같은 워크트리·같은 venv)**

```
tokenizers 0.22.2
qwen3-4b            tokenizer.json 존재 → counter REAL
qwen3-8b            tokenizer.json 존재 → counter REAL
qwen3-coder-30b-a3b tokenizer.json 존재 → counter REAL
표본 "static int inflate_fast(...)" → 실 카운터 19 tok vs len//4 근사 17 tok
```

`counter_from_tokenizer`가 `None`을 돌려주는 세 경로(스킴/리비전 미해석 · 파일 없음 ·
`tokenizers` 미설치)를 run 전에 전부 배제했다. **폴백은 조용하므로 사후에는 구별할 수 없다** —
이 확인을 run 전에 하지 않으면 "토크나이저 적용 후 측정"이라는 기록 자체가 근거를 잃는다.

### 해석

#### ① D26이 닫혔다 — 그리고 원인은 `critic_top_n`도, 게이트 술어도 아니었다

8/25 보고서는 D26을 이렇게 재작성했다: *"6단계 통과 조건(E2)과 8단계 진입 조건(`repro_spec`)이
서로를 배제한다."* 그 서술은 **현상으로는 맞았고 원인으로는 한 겹 얕았다.**

배제를 만든 것은 게이트 구조가 아니라 **도구 배선**이었다.

| | 8/25 | 오늘 | 변화 |
| --- | ---: | ---: | --- |
| build ok (2단계) | 21 / 79 | **42 / 79** | `-I` 추가 + 언어별 `-std` (PR #30) |
| clang-tidy ok | 21 / 79 | **42 / 79** | 동상 |
| `signal_artifacts` | 19 | **26** | 정적 도구가 더 성공 → 신호 증가 |
| `has_repro_spec` | 11 / 55 | **77 / 78** | `has_build`가 참이 되는 artifact가 늘었다 |
| `static_ok` | 44 / 55 | **75 / 78** | |
| 두 집합의 교집합 | **0** | **거의 전부** | 배제 해소 |
| 8단계 `gate_eval` | 0 | **8** | |

`fake_model.py`의 `_repro_spec` 호출부 — `repro_spec = _repro_spec(...) if has_build else None` —
이 그 연결 고리다. build가 실패하면 `repro_spec`이 안 붙고, `repro_spec`이 없으면 `p3`가
영원히 거짓이다. **`-I` 하나가 등급 사슬 전체를 끊고 있었다.**

trace의 `argv`에 `-I`가 안 보이는 것은 그 문자열이 노드가 만든 자리표시자이고 진짜 명령은
`container_tools.plan()`이 조립하기 때문이다(모듈 docstring). 실제 argv는
`clang -c -g -O1 <std> -I <cwd> -I <mount> <소스>`이고 `_include_args()`가 그 자리를 만든다.

**D26 항목은 세 번째로 고쳐 써야 한다.** "7단계 선별이 자른다"(8/23) → "6·8단계 조건이
배제한다"(8/25) → **"build 도구가 include 경로 없이 호출돼 `repro_spec`의 전제가 무너져
있었다"**(오늘).

#### ② 이제는 `critic_top_n`이 진짜 병목이다

```
6단계 통과 59건 ─┬─ 7단계 진입   8건 → upheld 8/8 → E3 8건
                 └─ 절단        51건 (그중 50건이 has_repro_spec=True)
```

E3 건수가 **정확히 상한값 8**이다. 그리고 잘린 51건 중 50건이 `repro_spec`을 갖고 있다.
7단계 Critic은 이 run에서도 **8/8 upheld**를 냈으므로, 상한을 올리면 E3가 늘어날 개연성이
높다 — **다만 개연성이지 실측이 아니다.** 잘린 51건은 confidence 0.6이 23건 / 0.3이 28건이라
Critic이 전부 upheld할 근거는 없다.

→ **상한만 바꾼 run 1회**가 이 질문을 닫는다. 다른 변수를 같이 넣으면 못 닫는다.

#### ③ E3 8건이 무엇인지 — 성능으로 읽으면 안 된다

8단계에 간 8건은 `contrib/minizip`·`examples/gun.c`·`examples/gzlog.c` 등 4개 파일에
몰려 있고 `gzlog.c` 하나가 4건이다. 이 분포는 **Fake auditor가 artifact 단위 정적 신호를
받아 Finding을 찍어내는 방식**의 결과다. E2 59건이 Fake의 산물이라는 8/25 해석 ⑨는
그대로 유효하고, **E3 8건도 마찬가지다.**

이 run이 말할 수 있는 것은 **"등급 사슬의 배관이 8단계까지 뚫렸다"**이고,
말할 수 없는 것은 **"E3가 진짜 취약점이다"**이다.

#### ④ D24 갱신 — 예산 역산값이 조금 내려왔다

```
wall 2,297 s = GPU 2,127.3 s (92.6%) + tool 144.0 s (6.3%) + 나머지 25.7 s (1.1%)
```

여전히 GPU-bound다. 도구 비중이 5.3% → 6.3%로 올랐다 — build가 실제로 컴파일을 하기
시작했고 8단계 도구 16건이 새로 생겼기 때문이다.

| 예산 | 상한 | 후보당 | 완주 가능 후보 수 |
| --- | ---: | ---: | ---: |
| `input_tokens` | 4,000,000 | 2,243 | **≈ 1,783** ← 가장 먼저 걸린다 |
| `gpu_seconds` | 7,200 | 3.92 s | ≈ 1,836 |
| `output_tokens` | 400,000 | 180 | ≈ 2,222 |
| `tool_seconds` | 1,800 | 후보와 무관 | §⑥ |

8/25의 ≈1,850에서 **≈1,783**으로 내려왔다. 결론은 안 바뀐다 — `candidate_max: 256`은
예산이 묶는 선의 14%다. zlib 542는 예산의 **30.4%**만 썼다.

#### ⑤ D21 (b) 판정 — 카운터는 붙었고, **이 구성에선 무동작이다**

먼저 **부착은 확인됐다**: 세 모델 모두 `tokenizer.json`을 찾았고 실 카운터가 표본에서
근사치와 다른 값(19 vs 17)을 냈다. 폴백 세 경로도 run 전에 배제했다.

그런데 크기가 안 줄었다.

| 기준 | 8/25 | 오늘 |
| --- | ---: | ---: |
| 후보당 input (전체 장부) | 2,161 | **2,243** (+3.8%) |
| 후보당 input (실서빙만) | 1,942 | **1,975** (+1.7%) |
| chars/token | 2.724 | **2.882** |
| 계상배율 | 1.469x | **1.388x** |

**⚠ 여기서 8/25와 오늘 사이에 내가 한 번 틀린 비교를 기록해 둔다** — 8/25의 "후보당 2,161"은
Fake를 포함한 전체 장부 기준이고, 이를 오늘의 실서빙 기준 1,975와 나란히 놓으면 8.7% 감소로
보인다. **기준이 다르다.** 같은 기준으로는 두 축 모두 **올랐다.**

왜 안 줄었나. 실 카운터는 캡슐 조립에서 **예산을 초과하는지 판정할 때만** 쓰인다. 그런데
캡슐이 예산 근처에 가지 않는다 — 8/25 실측 프롬프트 p50이 **1,526 토큰**이고 `light` 예산은
**4,000**이다. **묶을 것이 없으면 카운터를 바꿔도 결과가 안 바뀐다.**

→ **PR #28의 (c)와 같은 구조다.** (c)는 `expanded`를 6,000 깎았는데 최대 프롬프트가
불변이었고, (b)는 카운터를 정확하게 바꿨는데 예산이 안 묶여 불변이다. **두 건 모두
"이 구성에서 무동작"이고, 코드가 틀렸다는 뜻이 아니다.**

늘어난 1.7%는 build 성공으로 늘어난 근거 텍스트에 귀속되는 것이 자연스럽지만,
**네 갈래가 한 run에 같이 들어갔으므로 이 귀속은 확정하지 않는다.**

→ **(b)의 효과를 재려면 예산이 실제로 묶이는 구성이 필요하다** — 실서빙 auditor
프로필이거나, `capsule_budget_tokens`를 p90 근처(약 3,400)로 낮춘 대조 run이다.

#### ⑥ D15 — 8단계 도구 시간이 처음 측정됐다

```
1단계  index        1건 ·     414 ms
2단계  semgrep 79 + clang-tidy 79 + build 79 = 237건 · 136,168 ms → artifact당 1,723 ms
8단계  build 8 + asan 8                      =  16건 ·   7,423 ms → Finding당   928 ms
합                                              254건 · 144,005 ms
```

8/25는 artifact당 **1,451 ms**였고 오늘은 **1,723 ms**(+18.7%)다. build가 예전에는 대부분
즉시 실패했는데 이제 실제로 컴파일하기 때문이다. **8/25 보고서가 "8단계가 열리는 날 이 값이
늘어난다"고 단서를 달아둔 자리가 채워졌다.**

→ 상한 1,800 s를 2단계만으로 역산하면 artifact **1,045개**(8/25엔 1,240개). 8단계가
Finding 수에 비례해 더 붙는다. zlib은 artifact 79개다.

#### ⑦ 신규 결함 — 9단계 `dedupe()`가 E3를 지우고, 리포트에 등급이 없다

`src/orchestrator/nodes/report.py`의 `dedupe()`

```python
key = (finding.cwe, _sink(finding))
for finding in sorted(findings, key=lambda f: f.finding_id):
```

**세 갈래로 갈린다.**

1. **키에 위치가 없다.** CWE와 sink 함수 이름만 본다. zlib 전역에서 `memcpy`를 쓰는 서로
   다른 함수가 전부 한 건으로 합쳐진다. Finding **62건이 4건**이 된 것이 그 결과다
   (`중복 제거: 58건`).
2. **대표 선택이 등급을 안 본다.** `finding_id` 최솟값을 남긴다. E3인 `find_0011`
   `find_0022` `find_0024`~`find_0027` **6건이 리포트에서 사라졌고**, 살아남은
   `find_0001`은 E0/E2다. docstring이 이 정렬의 이유로 **결정론**을 든다 — 타당하지만
   `(등급 내림차순, finding_id)`로도 결정론은 유지된다. **둘은 상충하지 않는다.**
3. **`Report.render()`가 E 등급을 출력하지 않는다.** 실린 4건의 표기는 `state`
   (`hypothesized`/`queued`)뿐이다. E3에 도달한 `find_0002`·`find_0003`이 `queued`로
   찍힌다. **파이프라인 전체가 등급을 만드는데 최종 산출물이 그것을 싣지 않는다.**
4. **`run_manifest.json`에 `profile`이 없다.** 키는 `run_id · condition · repo_commit ·
   repo_dirty · source_hash · model_checksums · prompt_versions · tool_images · tool_runner ·
   device · budget · thresholds · seed · sampling · kb_snapshot · started_at · finished_at ·
   stopped_after`다. evaluation.md §2.3이 조건 간 차이를 **프로필과 단계 활성화**로
   정의하는데 그 프로필 이름이 안 남는다. `condition` 문자열만으로는 복원되지 않는다.

3번이 특히 나쁘다 — 이 run의 유일한 성과(8단계 E3 8건)가 **report.md만 읽는 사람에게는
보이지 않는다.** trace를 파야 나온다.

#### ⑧ 신규 사실 — 캡슐 캐시가 604건 조회에 적중 0건이다

```
cache={'capsule': 604}   hit={False: 604}   key: 고유 604개
604 = scout 542 + auditor 62
```

키 구성을 확인했다 — `sha256(파일내용해시 | 심볼@line_start | level | 정책버전)`
(`fake_retriever.py` · `hybrid_retriever.py` 동일). `line_start`가 들어가므로 심볼 단위
후보는 키가 구조적으로 전부 다르다. 결함이 아니라 의도다 — 같은 파일의 동명 `static` 함수
둘이 키를 공유하는 것을 막으려는 것이고 docstring이 W2로 적어 뒀다. 심볼 단위 후보에서
캡슐 캐시는 원리상 무효이고 남는 것은 해시 계산 604회 비용이다. 이 run은
`fallback_artifacts 0`이라 파일 단위 후보가 없었다 — 그쪽은 이 run으로 말할 수 없다.

서버측 prefix cache가 30.0%인 것과 대비된다. **같은 프롬프트 앞부분을 vLLM은 재사용하는데
하네스 캡슐 캐시는 한 번도 안 맞는다.**

#### ⑨ E4는 이 프로필로 측정 불가다 — asan은 고장난 게 아니다

8단계 asan 8건이 전부 실패했다. 원인은 asan이 아니다.

```
asan ./build.sh --sanitize=address ./poc examples/gzlog.c
        ↑ 자리표시자      ↑ 자리표시자
"asan: 빌드 단계에서 끊겨 프로그램이 실행되지 않았다"
```

`./build.sh`도 `./poc`도 실재하지 않는다. 이 `repro_spec`을 만든 것은 5단계 auditor인데
이 프로필에서 auditor는 **Fake**다. **가짜 재현 명세를 asan에 넘기고 있으므로 빌드 단계에서
끊기는 것이 정상 동작이다.**

→ **`p4`(E4)는 실서빙 auditor가 붙기 전까지 원리상 도달 불가다.** 이것은 배선 결함이 아니라
**조건 명시 사항**이고, 지금까지 "E4 0건"을 결함처럼 읽어온 자리를 정정한다.

→ 다만 **낭비 두 가지**는 남는다. 가짜 `repro_spec`으로 컨테이너를 16번 띄우고(7.4 s),
`examples/gzlog.c` 하나에 대해 build+asan을 **4번 반복**한다(dedupe가 9단계라 8단계에서는
아직 안 걸러진다).

#### ⑩ D23 — 네 번째 확인, 판정 유지

`location_ok`가 78건 전부 false다(픽스처 6 · zlib 55 · 8/25 63 · 오늘 78). 8/25 해석 ⑤가
내린 하향 판정이 그대로 유지된다 — `p1 = static_ok or location_ok`이고 `static_ok`가 75건을
통과시키므로, `location`을 채워도 등급이 안 바뀐다. **오늘은 근거가 더 강해졌다**:
`static_ok` False인 3건은 STATIC Evidence 자체가 없어서 채울 대상이 없다.

#### ⑪ R6 — 두 번째 대조값, 이번엔 단계별로 깨끗이 갈린다

```
서버 측:            Prefix cache hit rate  30.0%   (8/25: 30.5%)
장부 4단계(실서빙):  cached_tokens      0
장부 5단계(Fake):    cached_tokens 17,568
장부 7단계(Fake):    cached_tokens  1,526
```

8/25는 "실서빙 0 / 전체 1.46%"로 차집합을 통해 보였는데, 오늘은 **단계별로 직접 갈렸다.**
실서빙 경로는 0을 적고 아무것도 캐시하지 않는 Fake만 값을 낸다. **열이 역전돼 있다**는
진단이 재현됐고, 서버 값도 30.5% → 30.0%로 안정적이다.

#### ⑫ `top_k` 미기록이 재현됐다

기동 로그가 `generation_config.json`이 서버 기본값을 `temperature 0.7 · top_k 20 ·
top_p 0.8`로 덮었다고 적는다. `budgets.yaml`의 `sampling`은 `temperature 0.2 · top_p 0.95`
둘뿐이라 요청이 그 둘은 덮지만 **`top_k: 20`은 하네스가 안 보내 서버 기본값이 적용되고
`RunRecord`에 안 남는다.** 8/25와 같은 모델·같은 서버라 **두 run의 비교는 깨지지 않는다.**

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 공용 계정 작업 트리가 더러웠다 | `config/profiles.yaml`에 타인 WIP(B1′ 라벨 + 약 61줄) | `git stash` 대신 **`git worktree`**로 분리. 타인 변경을 원 브랜치에 남긴 채 측정 |
| 세 브랜치가 한 번도 같이 돈 적 없다 | #29·#30 / #31 / #32가 각각 열려 있었다 | 측정 브랜치에서 병합 — **충돌 0**, `pytest 616 passed` |
| 토크나이저 폴백이 조용하다 | 파일·라이브러리 부재 시 `None` 반환 → 글자수 근사로 복귀, 로그 없음 | **run 전에** 세 경로를 전부 배제하는 확인을 넣었다 (§근거 로그) |
| `candidate_max`에 CLI 오버라이드가 없다 | `config/budgets.yaml`에만 있다 | 측정 전용 커밋 1개(`7acf1b5`)로 `repo_dirty: False` 유지 |
| `single_gpu_12g`로 도구가 안 돈다 | 프로필 선언이 `tool_runner: fake` | `--tool-runner docker` 오버라이드. **다섯 번째 사례다** |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| Scout 점수 분포 (D2 두 번째 점) | 집계 스크립트가 `state.db` 조회에서 `no such column: id`로 끊겼다 | `objects` 테이블 스키마를 먼저 읽고 재조회. **새 run은 필요 없다** (§9 부록) |
| (a′) 앞 256 / 뒤 286 승격률 | 위와 같은 이유 | 동상 |
| scout 전용 프롬프트 토큰 p50/p90/max | 문자 수만 집계했다. 8/25는 토큰 기준이라 **직접 비교 불가** | `model_call` stage 4의 `input_tokens` 분위수 |
| vLLM 버전 | 기동 로그에서 해당 줄을 못 찾았다 | `pip show vllm` |
| 4-1 `error_detail` 실제 문자열 | 400이 안 났다 (네 번째) | `--max-model-len`을 낮춰 일부러 넘긴다 |
| 4-2 후보 단위 실패 후 생존 | 실패 0건 | 위와 같은 조건 |
| 실서빙 auditor·critic 비용, E4 도달 | 프로필이 Fake다 | `local_balanced`·`b1_single` |
| `critic_top_n`을 올리면 E3가 몇 건인가 | 상한을 안 바꿨다 | 상한만 바꾼 대조 run 1회 |
| D21 (b)의 실제 절감 효과 | 캡슐이 예산 근처에 못 간다 | 예산이 묶이는 구성에서 1회 |
| `parse_errors: 39`의 단위 | 8/25에 이어 미확인 | `tools/harness-tools/indexer.py` |

## 6. 반증 조건

**이 실험이 실패했다면 무엇이 틀린 것이었나.**

- **배선을 고쳤는데도 8단계가 0이었다면** — D26의 원인이 build가 아니었다는 뜻이다.
  **8건이 나왔고**, 그 8건이 전부 `has_repro_spec=True`이며, `has_repro_spec`이 11 → 77로
  뛴 것이 build ok 21 → 42와 같은 방향이다. **원인 귀속이 성립한다.**
- **`static_ok`와 `has_repro_spec`의 교집합이 여전히 0이었다면** 배제가 게이트 구조 자체의
  성질이었다. **거의 전부 겹쳤다** — 구조가 아니라 입력이 문제였다.
- **`critic_skipped`가 안 찍혔다면** #32가 배선되지 않은 것이다. **51건이 찍혔고**
  `51 + 8 = 59` 항등식이 맞는다.
- **토크나이저 카운터가 `None`이었다면** 이 run은 "토크나이저 적용 후"가 아니다.
  **세 모델 전부 REAL**이고 표본에서 근사치와 다른 값을 냈다.
- **후보당 토큰이 크게 줄었다면** (b)가 동작한 것이다. **안 줄었다** — 그래서 (b)는
  이 구성에서 무동작으로 판정한다. 이 항목은 **가설이 기각된 쪽**이다.

**성공이 배제하지 못한 것**: auditor·critic이 Fake다. **Finding 품질·오탐 제거율·E3의
의미는 이 run으로 말할 수 없다**(해석 ③).

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 높음 | **#31 · #32 · #34(W6) 머지** — 셋 다 `origin/main`의 조상이 아니다. 머지 전엔 main에서 이 run을 재현할 수 없다. #29·#30은 #33(`0c3c9e8`)으로 머지됐다 | PM 권한 |
| 높음 | **D26 세 번째 재작성** — 원인은 build 배선(`-I`). 게이트 구조는 결과지 원인이 아니었다 | §3 해석 ① |
| 높음 | **신규: `dedupe()`가 E3를 지운다** — 키에 위치 없음 + 대표 선택이 등급 무시 | §3 해석 ⑦ |
| 높음 | **신규: report.md에 E 등급이 없다** — 이 run의 성과가 리포트에 안 보인다 | §3 해석 ⑦ |
| 높음 | **`critic_top_n` 대조 run** — 상한만 바꿔 E3 증가분을 잰다 | §3 해석 ② |
| 중간 | **D21 (b) 판정** — 부착 확인, 이 구성에서 무동작. (c)와 같은 결론 | §3 해석 ⑤ |
| 중간 | **D24 갱신** — 역산값 ≈1,850 → **≈1,783** | §3 해석 ④ |
| 중간 | **D15 갱신** — 8단계 포함 artifact당 1,723 ms. 8/25의 단서가 채워졌다 | §3 해석 ⑥ |
| 중간 | **E4 조건 명시** — asan 실패는 결함이 아니라 Fake auditor의 귀결 | §3 해석 ⑨ |
| 중간 | **신규: manifest에 `profile`이 없다** | §3 해석 ⑦ |
| 중간 | **신규: 캡슐 캐시 적중 0/604** | §3 해석 ⑧ |
| 중간 | **신규: 8단계가 dedupe 이전이라 같은 파일에 도구를 반복 실행** (`gzlog.c` 4회) | §3 해석 ⑨ |
| 중간 | R6·`top_k` 항목에 두 번째 점 추가 | §3 해석 ⑪·⑫ |
| 중간 | 실서빙 auditor 프로필로 1회 | 해석 ③·⑨의 Fake 의존성을 가른다 |
| 낮음 | D2·(a′) 재집계 | §5 |

## 8. 정리 상태

- **서빙 서버**: 포트 8001 vLLM — 산출물 수집 후 `pkill -f "vllm serve"`로 종료.
  `nvidia-smi` "No running processes found" 확인
- **수집 순서를 지켰다**: 서버를 끄기 전에 `Prefix cache hit rate`·`/v1/models`·serve 로그를
  run 디렉터리로 복사했다. 8/23에 이 순서를 어겨 R6이 그 run으로는 못 움직였다
- **임시 설정**: `CPATH`는 셸 환경변수라 세션 종료와 함께 사라진다.
  `VLLM_USE_DEEP_GEMM=0`은 임시 조치가 아니라 **기동 조건**이므로 원복 대상이 아니다
- **저장소 변경**: 워크트리 `/srv/tenants/user/measure-20260826`, 브랜치
  `measure/remeasure-20260826`에 측정 전용 커밋 1개(`7acf1b5`). **머지 대상이 아니다**
- **타인 WIP 보존**: `config/profiles.yaml`의 커밋되지 않은 변경(B1′ 라벨 교정 + 약 61줄)은
  `measure/build-wiring-542` 쪽에 그대로 남아 있다. **건드리지 않았다**

## 9. 부록 — 재현 절차

```bash
# --- 브랜치 ---
cd /srv/tenants/user/whitehat-boss
git fetch origin
git checkout -b measure/remeasure-20260826 origin/main          # 21da039
git merge --no-ff origin/feat/d21-budget-floor                   # #29·#30
git merge --no-ff origin/feat/d21-tokenizer                      # #31
git merge --no-ff origin/feat/d26b-critic-skipped                # #32
# → 721d58c · 충돌 0
git worktree add /srv/tenants/user/measure-20260826 measure/remeasure-20260826
cd /srv/tenants/user/measure-20260826
# config/budgets.yaml: thresholds.candidate_max 256 → 1024 (측정 전용)
git commit -am "chore(measure): candidate_max 1024 (측정 전용, 머지 금지)"
# → 7acf1b5 · git status --porcelain 비어 있음
python -m pytest -q                                              # 616 passed

# --- 토크나이저 부착 확인 (run 전에 한다) ---
python - <<'PY'
import yaml
from pathlib import Path
from src.adapters.tokenizer import tokenizer_dir_from_source, counter_for_source
cfg = yaml.safe_load(Path("config/models.yaml").read_text(encoding="utf-8"))
for name, m in cfg["models"].items():
    c = counter_for_source(m["source"])
    print(name, "REAL" if c else "NONE -> 글자수 근사 폴백")
PY

# --- 서버 ---
source /srv/tenants/user/venv-probe/bin/activate
export CPATH="$HOME/pyhdr/usr/include/python3.12:$HOME/pyhdr/usr/include:${CPATH:-}"
SNAP=/srv/tenants/user/.cache/huggingface/hub/models--Qwen--Qwen3-4B-Instruct-2507-FP8/snapshots/8591804019c8b22094c3b5b4454e0edc05dffc98
SERVE_LOG=/srv/tenants/user/artifacts/logs/serve_20260826_124034.log

setsid nohup env \
  VLLM_USE_DEEP_GEMM=0 \
  VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo \
  vllm serve "$SNAP" \
    --served-model-name qwen3-4b \
    --host 127.0.0.1 --port 8001 \
    --gpu-memory-utilization 0.2 \
    --max-model-len 32768 \
    --enable-prefix-caching \
  > "$SERVE_LOG" 2>&1 &

# `VLLM_ATTENTION_BACKEND`는 주지 않았다 — FLASH_ATTN이 선택돼 필요 없었다.

# --- run ---
python3 -m src.cli run \
  --profile single_gpu_12g \
  --device atom \
  --tool-runner docker \
  --artifacts-root /srv/tenants/user/artifacts \
  --target /srv/tenants/user/targets/zlib

# --- 수집 (서버를 끄기 전에) ---
RUN_DIR=$(ls -dt /srv/tenants/user/artifacts/run_* | head -1)
grep -E "Prefix cache hit rate" "$SERVE_LOG" | tail -20 > "$RUN_DIR/serve_prefix_cache.txt"
cp "$SERVE_LOG" /srv/tenants/user/artifacts/logs/models_20260826_124034.json "$RUN_DIR/"
pkill -f "vllm serve"
```

**§5의 미측정 두 줄을 채우는 명령** (run 산출물만 있으면 언제든 된다):

```bash
python3 - "$RUN_DIR" <<'PY'
import json, sqlite3, statistics as st, sys
from pathlib import Path
from collections import Counter
rd = Path(sys.argv[1])
ev = [json.loads(l) for l in (rd/"trace.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

sc = [e for e in ev if e.get("kind")=="model_call" and e.get("stage")==4]
tk = sorted(e["data"]["input_tokens"] for e in sc)
print(f"scout 프롬프트 토큰  p50 {st.median(tk):,.0f} · p90 {tk[int(len(tk)*.9)]:,} · max {max(tk):,} / 32,768")

con = sqlite3.connect(str(rd/"state.db"))
print([r[0] for r in con.execute("select name from sqlite_master where type='table'")])
rows = con.execute("select * from objects limit 1").fetchall()
print("objects 표본:", str(rows)[:500])
con.close()

order = {e.get("subject_id"): i for i, e in enumerate(sc)}
prom = {e.get("subject_id") for e in ev
        if e.get("kind")=="policy_decision" and e.get("stage")==4
        and e["data"].get("decision")=="scout_promoted"}
f = sum(1 for c,i in order.items() if i < 256 and c in prom)
b = sum(1 for c,i in order.items() if i >= 256 and c in prom)
print(f"(a') 앞256 승격 {f} ({f/256*100:.1f}%) · 뒤{len(order)-256} 승격 {b} ({b/(len(order)-256)*100:.1f}%)")
PY
```
