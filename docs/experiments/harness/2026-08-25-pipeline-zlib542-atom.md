# zlib 심볼 542건 전량 완주 — ATOM

> 대상: PR #28(`feat/d21-budget-floor`) + 측정 전용 커밋 1개. `candidate_max`를 올려
> **8/23에 잘렸던 286건을 포함한 전량**을 돌린 run이다. D24의 유일한 입력이고,
> 결과적으로 D26의 원인 서술을 바꿨다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | **2026-08-25 13:33 KST** (04:33:05Z). 시작 12:57 KST (03:57:04Z) · 소요 **36 m 01 s** |
| 실제 서빙 모델 | `hf://Qwen/Qwen3-4B-Instruct-2507-FP8@8591804019c8b22094c3b5b4454e0edc05dffc98` (별칭 `qwen3-4b`, fp8). **근거**: `/v1/models`의 `root`가 그 스냅샷 경로를 가리킨다 — `models_20260825_125430.json`에 보존 |
| `repo_commit` | `393b38e2d647cf830de0db859c6350ceac031d49` · dirty **False** (base `origin/feat/d21-budget-floor` = `f121b65`) |
| `run_id` | `run_20260825_0001` |
| run 산출물 경로·보존 | `/srv/tenants/user/artifacts/run_20260825_0001` — **보존.** 사본 `logs/raw_20260825_125430.tgz` (1,155,687 B) |
| `tool_runner` | 프로필 선언 `fake` / 실제 **`docker`** (`--tool-runner docker` 오버라이드, ADR 0005). **네 번째 사례다** |
| `--device` | `atom` |
| `tool_images` digest | `harness-tools:v1` = `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17` · **종류: docker 29.x `RepoDigests`(= manifest list digest, `.Id`와 같은 값)** — portability.md §5 #4 |
| `--gpu-memory-utilization` | `0.2` · **분모 119.7 GiB → 23.94 GiB**. 기동 로그 원문: `Free memory on device (110.37/119.7 GiB) on startup. Desired GPU memory utilization is (0.2, 23.94 GiB)` |
| 동시에 떠 있던 다른 서버 | **없음.** 기동 전 `ss -ltnp \| grep :800[0-9]` 0건 · `nvidia-smi` "No running processes found" · `ps \| grep vllm` 0건 |
| 원문 산출물 보존 위치 | 위 run 디렉터리 + `logs/raw_20260825_125430.tgz` |
| 서빙 서버 stdout | **`run_20260825_0001/serve_20260825_125430.log` — 보존했다.** 8/18·8/22·8/23 세 번 연속 유실된 값이고, 이번에 처음 남았다 |

## 1. 요약

**무엇을 확인하려 했나**: `candidate_max`가 자르지 않는 조건에서 zlib 심볼 **542건 전량**이
얼마의 토큰·시간·도구 시간을 쓰는가. 그 값이 D24·D15의 유일한 입력이다. 부수적으로
D21의 계상배율 세 번째 점과, PR #28의 예산 축소가 실제로 무언가를 묶는지를 본다.

**성능 주장과 경로 성립 주장을 섞지 않는다.** 8/23 run과 코드가 갈리므로(아래 §2) 후보당
수치를 그 run과 나란히 두는 비교는 하지 않는다. 이 run이 답하는 것은 **"542건이 얼마인가"**
하나다.

| 항목 | 결과 |
| --- | --- |
| 542 전량 실행 | **성립** — `planned 542 · deferred 0 · target_skipped 0건` |
| 완주 | **성립** — 36 m 01 s · `stopped_after` None |
| 후보 단위 실패 | **0건** — `model_call` 605건 전부 `ok=True` |
| 최대 단일 프롬프트 | **9,707 / 32,768** (여유 23,061) — **8/23과 같은 값** |
| chars/token | **2.724** · 계상배율 **1.469x** (D21 세 번째 점) |
| PR #28 (c) 예산 축소 | **무동작** — 최대 프롬프트 불변, 이 프로필엔 `related` 섹션이 없다 |
| 도구 시간 | `tool_call` **238건 / 114,655 ms** — 8/23(238건)과 **건수 동일** |
| 8단계 `gate_eval` | **0건** — D26이 후보 수와 무관한 구조임이 확정 |
| `location_ok` | **63건 전부 false** — 그리고 **채워도 살아나지 않는다**(§3 해석 ⑤) |
| Scout 점수 | **이산값 9개 · 71%가 0.0** (D2 첫 실측) |
| prefix cache | 서버 **30.5%** vs 장부 실서빙 **0.00%** — R6 첫 대조값 |

**이 run이 태우지 않은 것**: 4-1(HTTP 400)·4-2(후보 단위 실패). **세 번째로 미실현**이다.

## 2. 실험 환경

| 항목 | 값 |
| --- | --- |
| 장비 | GIGABYTE AI TOP ATOM (NVIDIA GB10, sm_121, aarch64) · DGX OS |
| cgroup 상한 (실행 시점 실측) | `memory.max` **68,719,476,736** (64 GiB) · `memory.high` **62,277,025,792** (58 GiB) |
| vLLM | 0.27.1 · attention backend **FLASH_ATTN** (flashinfer 미선택) |
| KV cache | **17.88 GiB → 130,176 tokens** · 32,768 토큰 요청 기준 **최대 동시 3.97x** |
| 프로필 | `single_gpu_12g` — **scout만 실서빙**, auditor·critic·retriever·embedding·reranker는 Fake |
| 대상 | zlib (`/srv/tenants/user/targets/zlib`) |

**갈리는 항목을 먼저 적는다** — 8/23 `run_20260823_0002`와 대조할 때:

| 축 | 8/23 | 오늘 | 비교 가능한가 |
| --- | --- | --- | --- |
| 장비·프로필·서버 인자 | atom · `single_gpu_12g` · util 0.2 · 32768 | **동일** | ✅ |
| `candidate_max` | 256 | **1024** (의도된 변수) | ✅ |
| 코드 | `28754ec` (PR #25 끝) | `393b38e` (= main `fe02e8b` + PR #28 2커밋 + 측정 커밋) | ⚠ |
| `capsule_budget_tokens.expanded` | 24,000 | **18,000** | ⚠ |

**⚠의 뜻**: 코드가 갈렸으므로 후보당 토큰·승격률의 **차이를 원인에 귀속하지 않는다**.
다만 그 사이에 들어온 PR #26(`related` 섹션 본문)은 **이 run에 걸리지 않았다** —
`related`는 `HybridRetriever`에만 있고(`hybrid_retriever.py:92`) 이 프로필은
`retriever: fake`다(`fake_retriever.py:80`에 `related` 없음). 실측으로 확인했다:
프롬프트 605개 중 `related` 문자열을 포함한 것 **0개**.

## 3. 실험 — zlib 542건 전량 (`run_20260825_0001`)

### 목적

**측정이다.** 경로 성립은 8/23이 이미 닫았다. 이 run이 재는 것은 후보 1건당 비용과
542건 전량의 총량이고, 그것이 `candidate_max`(D24)·`tool_seconds`(D15)의 근거가 된다.

### 조건

| 항목 | 값 |
| --- | --- |
| 명령 | `python3 -m src.cli run --profile single_gpu_12g --device atom --tool-runner docker --artifacts-root /srv/tenants/user/artifacts --target /srv/tenants/user/targets/zlib` |
| `condition` | `B3prime@pipeline` (기본값) |
| `seed` | 20260901 |
| thresholds (박제값) | `scout_threshold 0.5 · critic_top_n 8 · candidate_max 1024 · schema_retries 1 · capsule light 4000 / expanded 18000 / focused 8000` |
| `prompt_versions` | scout v1 · auditor v1 · critic v1 |
| `kb_snapshot` | `kb-none` (자리표시자 — 경고 발생) |

### 결과

| 항목 | 결과 |
| --- | --- |
| 소요 | **36 m 01 s** (2,161 s) · 완주 · `stopped_after` None |
| `target_plan` | analyzable **79** · symbol_targets **542** · fallback 0 · no_symbol **31** · signal **19** · planned **542** · deferred **0** · candidate_max 1024 |
| `target_skipped` | **0건** |
| `model_call` | **605건 · `ok=False` 0건** — scout 542 · auditor 55 · critic 8 |
| 장부 (실서빙 542콜) | input **1,052,676** · output **82,041** · cached **0 (0.00%)** · gpu **2,021.9 s** |
| 장부 (Fake 포함 605콜) | input **1,171,453** · output **90,649** · cached **17,078 (1.46%)** |
| chars/token | **2.724** · bytes/char **1.159** · 계상배율 **1.469x** |
| 프롬프트 크기 | p50 **1,526** · p90 **3,103** · max **9,707** / 32,768 (`prompts/0063_scout.md`) |
| 후보 1건당 | wall **3.99 s** · gpu **3.73 s** (p50 3.56 / max 6.66) · input **2,161 tok** · output **167 tok** |
| `tool_call` | **238건 / 114,655 ms** — index 1 · semgrep 79 · clang-tidy 79 · build 79 |
| 도구 성공 | semgrep **79/79** (exit 1 = 13건) · build **21/79** · clang-tidy **21/79** |
| `graph_index` | loaded · files **48** · symbols **542** · calls **3,045** · unresolved_calls **207** · unresolved_symbols 0 · parse_errors **39** · skipped 0 |
| `retrieval_truncated` | **0건** |
| `gate_eval` | 63건 — stage6 **55** · stage7 **8** · **stage8 0** |
| 등급 | **E2 52 · E0 11** · E1 0 · E3 0 · E4 0 |
| prefix cache (서버) | **30.5%** (run 종료 시점) |

### 근거 로그

**승격 사슬 — `transition` reason 전량**

```
below_scout_threshold  487
scout_promoted          55
auditor_hypothesized    55
promoted_to_critic      44
insufficient_evidence   11
repro_spec_missing       8
```

`542 = 487 + 55` · `55 = 44 + 11` — 회계가 자기 자신과 맞는다.

**게이트 술어 (`gate_eval` data, 평평한 키)**

```
[stage 6] 55건  등급 {E2: 44, E0: 11}
  p1  {True: 44, False: 11}      p2  {True: 44, False: 11}
  p3  {False: 55}                p4  {False: 55}
  location_ok     {False: 55}
  static_ok       {True: 44, False: 11}
  graph_ok        {True: 55}
  path_ok         {True: 55}
  unblocked       {True: 44, False: 11}
  upheld          {False: 55}
  has_repro_spec  {False: 44, True: 11}

[stage 7] 8건  등급 {E2: 8}
  upheld {True: 8}   has_repro_spec {False: 8}   p3 {False: 8}
  location_ok {False: 8}
```

**도구 종료 코드**

```
build        79건 · ok={True: 21, False: 58} · exit={0: 21, 2: 31, 1: 27}
clang-tidy   79건 · ok={True: 21, False: 58} · exit={0: 12, 2: 31, 1: 36}
semgrep      79건 · ok={True: 79}            · exit={0: 66, 1: 13}
index         1건 · ok={True: 1}             · exit={0: 1}
```

**Scout 점수 분포 (`state.db` `objects` 테이블, kind=candidate 542행)**

```
min 0.000  p25 0.000  p50 0.000  p75 0.100  max 0.750
0.5 이상 55건 · 0.5 미만 487건
고유값: {0.0: 386, 0.1: 71, 0.15: 2, 0.3: 2, 0.4: 3, 0.45: 23, 0.65: 35, 0.7: 6, 0.75: 14}
```

**(a′) 정렬 — scout 호출 순서 앞/뒤 분할**

```
앞 256: 승격 44건 (17.2%)
뒤 286: 승격 11건 ( 3.8%)
```

**서빙 서버 stdout (prefix cache, 종료 직전)**

```
INFO 08-25 13:33:05 [loggers.py:310] Engine 000: ... Prefix cache hit rate: 30.5%
INFO 08-25 13:33:25 [loggers.py:310] Engine 000: ... Prefix cache hit rate: 30.5%
```

**기동 로그 (메모리 회계)**

```
Available KV cache memory: 17.88 GiB
GPU KV cache size: 130,176 tokens
Maximum concurrency for 32,768 tokens per request: 3.97x
Free memory on device (110.37/119.7 GiB) on startup. Desired GPU memory utilization is
(0.2, 23.94 GiB). Actual usage is 5.96 GiB for consumed memory (weights + non-torch),
0.1 GiB for peak activation, and 0.82 GiB for CUDAGraph memory.
Using FLASH_ATTN attention backend out of potential backends:
  ['FLASH_ATTN', 'FLASHINFER', 'TRITON_ATTN', 'FLEX_ATTENTION'].
WARNING [model.py:1637] Default vLLM sampling parameters have been overridden by the
model's `generation_config.json`: {'temperature': 0.7, 'top_k': 20, 'top_p': 0.8}.
```

### 해석

#### ① 후보 1건당 비용이 처음 나왔다 — D24의 입력

```
wall 2,161 s = GPU 2,021.9 s (93.6%) + tool 114.7 s (5.3%) + 나머지 24 s (1.1%)
```

**이 파이프라인은 GPU-bound다.** 도구는 5%다.

| 예산 | 상한 | 후보당 | 완주 가능 후보 수 |
| --- | ---: | ---: | ---: |
| `input_tokens` | 4,000,000 | 2,161 | **≈ 1,850** ← 가장 먼저 걸린다 |
| `gpu_seconds` | 7,200 | 3.73 s | ≈ 1,930 |
| `output_tokens` | 400,000 | 167 | ≈ 2,390 |
| `tool_seconds` | 1,800 | **후보와 무관** | §③ |

**`candidate_max: 256`은 폭주 차단용으로도 과하게 낮다.** 예산이 실제로 묶는 선은
약 1,850건이고 256은 그 14%다. zlib 542는 예산의 30%만 썼다.

**⚠ 이 상한은 `single_gpu_12g` 한정이다.** GPU 2,021.9 s는 **전부 scout**이다 —
auditor·critic이 Fake라 `gpu_ms`가 `None`이고 장부에 0으로도 안 들어간다. 실서빙
auditor를 붙이면 후보당 3.73 s가 크게 오른다.

#### ② (a′) 정렬이 실측으로 검증됐고, 8/23의 절단 대가가 확정됐다

앞 256에서 **17.2%**, 뒤 286에서 **3.8%** 승격 — **4.5배**다. 그리고 앞 256의 17.2%는
8/23의 18.4%(47/256)와 사실상 같다. 코드가 갈렸는데도 재현됐다.

**더 정확히**: 앞 256이 낸 44건은 **전부 `static_ok=True`(E2)**, 뒤 286이 낸 11건은
**전부 `static_ok=False`(E0)**다. 즉 **8/23이 286건을 잘라 잃은 것은 E0급 Finding
11건뿐이고 E2는 0건이다.** (a′)가 "정적 신호를 게이트가 아니라 순서로 쓴다"는 설계
그대로 작동했다.

#### ③ `tool_seconds`의 분모는 후보가 아니라 artifact다 (D15)

```
238건 = index 1 + (semgrep + clang-tidy + build) × 79 artifact
114,655 ms / 79 = artifact당 1,451 ms
```

**8/23도 238건 / 112,578 ms(artifact당 1,425 ms)였다.** 후보를 2.1배 늘렸는데 도구
호출이 **한 건도 안 늘었다** — 도구는 2단계에서 artifact마다 돌고 후보 수와 무관하다.
artifact당 값이 **±2%로 재현**됐다.

→ 상한 1,800 s는 **artifact 1,240개**에 해당한다. zlib은 79개다.

**⚠ 다만 이 238건은 전부 2단계다.** 8단계 도구 검증이 0건이므로(아래 ④), 그 축이
열리면 이 값이 늘어난다. **지금 값으로 상한을 확정하면 8단계가 열리는 날 또 틀린다.**

#### ④ D26 — 진짜 벽은 `critic_top_n`이 아니다 (항목 재작성 필요)

```
E2 44건  static_ok T · unblocked T · has_repro_spec F  → promoted_to_critic
E0 11건  static_ok F · unblocked F · has_repro_spec T  → insufficient_evidence
                                    ↑ 겹침 0
```

**repro_spec을 가진 11건이 하나도 7단계에 못 가고, 7단계에 간 44건은 전부 repro_spec이
없다.** 그래서 `critic_top_n`을 8에서 44로 올려도 8단계는 0건이다.

반대편도 성립한다: 7단계 Critic은 **8/8 `upheld`**를 냈다. `p3 = upheld and repro_spec`
이므로, **repro_spec을 가진 11건이 7단계에 갔다면 E3가 나왔다.** 즉 **6단계 게이트가
E3의 재료를 걸러내고 있다.**

**항목의 제목이 바뀐다.** "7단계 선별이 조용히 자른다"(36건 절단은 여전히 사실)가 아니라 —

> **6단계 통과 조건(E2 = `path ∧ graph ∧ unblocked`)과 8단계 진입 조건(`repro_spec`)이
> 서로를 배제한다.**

**배타의 기제**는 두 집합의 어긋남이다.

| 술어 | 출처 | 해당 artifact |
| --- | --- | ---: |
| `has_repro_spec` | `fake_model.py:229` `has_build = any(BUILD and ok)` | build ok **21** / 79 |
| `static_ok` (및 `unblocked`) | `fake_model.py:228` `_has_static_signal` | signal **19** / 79 |

그리고 (a′)가 신호 쪽을 앞으로 몰아 배타를 완성했다.

**⚠ Fake 의존성을 가른다.** 위 두 줄은 `fake_model`의 것이므로 **상관은 Fake의 성질일 수
있다.** 그러나 **게이트 쪽 구조는 코드의 성질이라 Fake와 무관하다** — `E3`의 조건이
`E2`의 조건을 포함하지 않는데 정책은 E2 통과분만 Critic에 올린다. 실서빙 auditor로
바꿔도 그 문은 그대로다.

#### ⑤ D23 정정 — `Evidence.location`을 채워도 `location_ok`는 살아나지 않는다

`location_ok`는 63건 전부 false다(픽스처 6 · zlib 55 · 오늘 63 — 세 번째 확인). 그런데
**채우는 것만으로는 안 된다**는 것이 오늘 처음 드러났다.

```python
p1 = static_ok or location_ok        # ← or 다
```

- **44건**: `static_ok`가 이미 True → `location_ok`가 살아나도 **p1이 안 바뀐다.**
- **11건**: `static_ok`가 False = **STATIC Evidence 자체가 없다.** D23 (a)는 semgrep
  `start.line`·clang-tidy `file:line:col`을 파싱해 **STATIC Evidence에** location을
  넣는 일인데, 넣을 Evidence가 없다.

→ **이 구성에서 `location_ok`는 어느 쪽으로도 등급을 바꾸지 못한다.** `p1`의 `or`를
손대거나 술어 구조를 바꾸지 않으면 D23 (a)의 등급 영향은 0이다. **D23 본문에 그 문장이
없다** — 지금은 "채우면 열린다"로 읽힌다.

**D23의 값은 등급 축이 아니라 후보 정렬 해상도에 남는다.** 신호가 artifact 단위라
5,000줄 파일의 모든 심볼이 똑같이 "신호 있음"이 된다(`runner._plan_targets()`가 artifact
단위인 이유).

#### ⑥ D21 — 세 번째 점, 그리고 (c)가 무동작이라는 직접 증거

| | 픽스처 (8/22) | zlib 256 (8/23) | **zlib 542 (오늘)** |
| --- | :-: | :-: | :-: |
| chars/token | 2.224 | 2.702 | **2.724** |
| 계상배율 | 1.798x | 1.480x | **1.469x** |
| bytes/char | 1.30~1.42 | 미기재 | **1.159** |

**새 사실 둘.**

1. **같은 대상·같은 구성이면 재현된다** (2.702 → 2.724, 0.8% 차). D21은 "구성 의존값이라
   상수로 못 쓴다"고 적었는데, 그 반대편 — **구성을 고정하면 안정적** — 이 처음 나왔다.
   상수화의 조건은 "값을 잘 고르는 것"이 아니라 **"구성을 선언하는 것"**이다.
2. `bytes/char` 1.159 — 픽스처 1.30~1.42에서 내려왔다. **대상 코드가 커지면 ASCII 비중이
   올라 4 쪽으로 수렴한다**는 D21의 예측이 그 방향으로 재확인됐다.

**(c) 판정**: `expanded`를 24,000 → 18,000으로 **6,000 깎았는데 최대 단일 프롬프트가
9,707로 8/23과 같다.** 게다가 이 프로필은 `related` 섹션이 없다(§2). → **PR #28의 (c)는
이 구성에서 무동작이다.** "부분 완화"보다 더 낮춰 적어야 한다.

**4-1은 세 번째로 미실현이다.** p90이 3,103으로 상한의 9.5%다. 컨텍스트 상한은 병목이
아니다 — 심볼 단위 후보가 그 경로를 구조적으로 눌렀다.

#### ⑦ D2 — 임계값이 아니라 **점수 해상도**가 문제다

```
0.0:386(71%)  0.1:71  0.15:2  0.3:2  0.4:3  0.45:23  ┃  0.65:35  0.7:6  0.75:14
                                                     ↑ (0.45, 0.65) 완전 공백
```

**실서빙 Qwen3-4B가 542건에 고유값 9개만 쓴다. 71%가 정확히 0.0이다.**

| 임계값 | 승격 |
| --- | :-: |
| ≥0.75 | 14 |
| **0.46 ~ 0.65** | **55 (구간 전체 동일)** |
| ≥0.45 | 78 |
| ≥0.10 | 156 |

→ `scout_threshold: 0.5`는 **폭 0.19짜리 안정 구간의 한가운데**다. 안전한 값이다.
**단 값을 잘 골라서가 아니라 점수에 해상도가 없어서다.** 0.45로 한 칸 내리면 55 → 78건
(+42%)으로 튄다.

→ **이것은 Fake의 성질이 아니다** — scout은 이 프로필의 유일한 실서빙 역할이다.

#### ⑧ R6 — 첫 대조값, 그리고 장부가 **역전**돼 있다

```
서버 측:     Prefix cache hit rate   30.5%
장부 실서빙:  cached      0 / 1,052,676  =  0.00%
장부 전체:    cached 17,078 / 1,171,453  =  1.46%
```

차집합이 **Fake 콜 63건**(auditor 55 + critic 8)이다. → **`cached_tokens` 열은 실서빙에서
0이고 Fake에서만 값이 있다.** 서버는 30.5%를 실제로 재사용하는데 장부는 못 적고,
아무것도 캐시하지 않는 Fake는 값을 낸다. 그 열을 그대로 읽으면 **"Fake가 캐시를 잘
쓴다"**로 읽힌다. R6보다 한 겹 나쁜 이야기라 별도 항목으로 연다.

#### ⑨ ⚠ 등급 분포를 하네스 성능으로 읽으면 안 된다

```
graph_ok   True 55/55     path_ok  True 55/55     upheld  True 8/8     confidence 0.6 고정
```

**세 술어가 전부 100%다.** Fake auditor가 항상 GRAPH Evidence를 붙이고 SOURCE/PROPAGATE/
SINK 3단계를 만들며, Fake Critic이 전부 uphold한다. **E2 44건은 Fake의 산물이지 파이프라인
성능이 아니다.**

#### ⑩ 기록에 없던 샘플링 파라미터가 하나 있다

기동 로그가 `generation_config.json`이 서버 기본값을 `temperature 0.7 · top_k 20 ·
top_p 0.8`로 덮었다고 적는다. `budgets.yaml`의 `sampling`은 `temperature 0.2 ·
top_p 0.95` 둘뿐이라 요청이 그 둘은 덮지만 **`top_k: 20`은 하네스가 보내지 않아 서버
기본값이 적용되고 `RunRecord.thresholds`에 남지 않는다.** evaluation.md §2.3이 "조건 간
비교에서 고정해야 하므로 RunRecord에 남는다"고 못박은 자리의 구멍이다. 8/22·8/23도 같은
모델·같은 서버라 **그 run들과의 비교는 깨지지 않는다** — 다른 모델로 조건을 바꾸는
순간 깨진다.

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| `candidate_max`에 CLI 오버라이드가 없다 | `config/budgets.yaml`에만 있다 | 측정 전용 커밋 1개(`393b38e`)로 올려 **`repo_dirty: False`를 유지**했다. dirty로 돌면 러너가 "모델 비교표에서 제외한다"고 경고한다 |
| `single_gpu_12g`로 도구가 안 돈다 | 프로필 선언이 `tool_runner: fake` | `--tool-runner docker` 오버라이드. **네 번째 사례다** — README 미확정 #4 |
| 세 번 유실된 서빙 stdout | 포그라운드 기동 + 창 닫음 | `setsid nohup ... > "$SERVE_LOG" 2>&1 &` — 보존 목적에 더해 **창을 닫아도 안 죽는다**. run 종료 후 run 디렉터리로 복사 |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 4-1 `error_detail` 실제 문자열 | 400이 안 났다 (세 번째) | `--max-model-len`을 낮춰 일부러 넘긴다 |
| 4-2 후보 단위 실패 후 생존 | `model_call` 실패 0건 | 위와 같은 조건 |
| 실서빙 auditor·critic 비용 | 프로필이 Fake다 | `local_balanced`·`b1_single` |
| `parse_errors: 39`의 단위 | 인덱서 출력의 그 필드가 파일 수인지 노드 수인지 미확인. `analyzable 79 − graph files 48 = 31 = no_symbol_artifacts 31`은 정확히 맞는다 | `tools/harness-tools/indexer.py`의 해당 카운터 확인 |
| `unresolved_calls 207 / 3,045` (6.8%)의 영향 | 이 값이 E2 판정에 얼마나 들어가는지 분석 안 함 | `graph_ok` 판정과 대조 |
| 8단계 도구 시간 | 8단계 진입 0건 | D26이 열린 뒤 |
| 서버 측 prefix cache의 **콜 단위** 값 | 로그가 엔진 누적치만 준다 | `/metrics`의 `prompt_tokens_cached_delta`(R6) |

## 6. 반증 조건

**이 실험이 실패했다면 무엇이 틀린 것이었나.**

- **542 전량에서 예산이 소진됐다면** — `candidate_max`가 예산 때문에 필요했다는 뜻이었다.
  `stopped_after`가 `None`이고 최대 소비가 예산의 30%였으므로 **그 가설은 배제됐다.**
- **`target_plan`의 항등식이 어긋났다면** W2의 회계를 못 믿는다. `542 = 487 + 55`,
  `55 = 44 + 11`이 맞았으므로 그 축도 닫혔다.
- **뒤 286에서 앞 256과 비슷한 비율로 승격이 나왔다면** (a′)의 정렬 키가 틀린 것이었다.
  17.2% vs 3.8%로 **4.5배** 갈렸다.
- **`candidate_max`를 올려 8단계가 열렸다면** D26은 후보 수 문제였다. **0건 그대로다** —
  D26이 구조 문제임이 확정됐다.

**성공이 배제하지 못한 것**: 이 run은 auditor·critic이 Fake다. **Finding 품질·오탐
제거율·등급 분포는 이 run으로 말할 수 없다**(해석 ⑨).

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 높음 | **D26 전면 재작성** — 원인이 `critic_top_n`이 아니라 6단계 통과 조건과 8단계 진입 조건의 배제 | 이 보고서 §3 해석 ④ |
| 높음 | **D24 확정** — `candidate_max`를 예산 역산값(≈1,850)과 함께 정한다 | 입력 확보됨 |
| 높음 | **D23 정정** — "채우면 열린다"를 지운다. `p1`의 `or` 구조가 함께 와야 한다 | §3 해석 ⑤ |
| 중간 | **신규: 장부 `cached_tokens` 역전** (실서빙 0 / Fake 값 있음) | §3 해석 ⑧ |
| 중간 | **신규: Scout 점수 해상도** (고유값 9개 · 71%가 0.0) | §3 해석 ⑦ |
| 중간 | **신규: `top_k` 미기록** — 서버 `generation_config`가 넣는 값이 RunRecord에 없다 | §3 해석 ⑩ |
| 중간 | **D21 갱신** — 세 번째 점, (c) 무동작 판정 | §3 해석 ⑥ |
| 중간 | **D15 갱신** — 분모가 artifact임을 명시. 8단계 미포함 단서 함께 | §3 해석 ③ |
| 중간 | **D2 갱신** — 0.5는 안정 구간 한가운데. 단 해상도 부재가 원인 | §3 해석 ⑦ |
| 중간 | 실서빙 auditor 프로필로 1회 | 해석 ④·⑨의 Fake 의존성을 가른다 |
| 낮음 | `parse_errors 39` 단위 확인 | §5 |

## 8. 정리 상태

- **서빙 서버**: 포트 8001 vLLM — 산출물 수집 후 `pkill -f "vllm serve"`로 종료
- **임시 설정**: `CPATH`는 셸 환경변수라 세션 종료와 함께 사라진다. `VLLM_USE_DEEP_GEMM=0`은
  임시 조치가 아니라 **기동 조건**이므로 원복 대상이 아니다
- **저장소 변경**: 브랜치 `measure/candidate-max-542`에 측정 전용 커밋 1개(`393b38e`).
  **머지 대상이 아니다** — D24가 정한 값으로 별도 PR을 낸다

## 9. 부록 — 재현 절차

```bash
# --- 서버 ---
source /srv/tenants/user/venv-probe/bin/activate
export CPATH="$HOME/pyhdr/usr/include/python3.12:$HOME/pyhdr/usr/include:${CPATH:-}"

SNAP=/srv/tenants/user/.cache/huggingface/hub/models--Qwen--Qwen3-4B-Instruct-2507-FP8/snapshots/8591804019c8b22094c3b5b4454e0edc05dffc98
SERVE_LOG=/srv/tenants/user/artifacts/logs/serve_20260825_125430.log

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

# `VLLM_ATTENTION_BACKEND`는 주지 않았다 — 이 run은 FLASH_ATTN이 선택돼 필요 없었다.

# --- 코드 ---
cd /srv/tenants/user/whitehat-boss
git checkout -B measure/candidate-max-542 origin/feat/d21-budget-floor
# config/budgets.yaml: thresholds.candidate_max 256 → 1024 (측정 전용)
git commit -am "chore(threshold): 측정 전용 — candidate_max 256 → 1024 (D24 입력)"
# → 393b38e2d647cf830de0db859c6350ceac031d49 · git status --porcelain 비어 있음

# --- run ---
python3 -m src.cli run \
  --profile single_gpu_12g \
  --device atom \
  --tool-runner docker \
  --artifacts-root /srv/tenants/user/artifacts \
  --target /srv/tenants/user/targets/zlib
```
