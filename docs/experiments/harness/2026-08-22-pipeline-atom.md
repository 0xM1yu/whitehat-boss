# ATOM 파이프라인 실서빙 완주 — scout 실모델 · 캡슐/게이트 관측

> **기록 성격**: 2026-08-22 시점의 관측 기록이다.
> **원자료는 터미널 출력에서 옮겼고, run 산출물을 다시 열어 확인한 것이 아니다.**
> 그래서 메타 표의 여러 칸이 `미기재`다 — 채우는 방법은 §5에 적었다.

**작성**: @danielcho02
**실험일**: 2026-08-22 (KST)
**대상 장비**: GIGABYTE AI TOP ATOM (NVIDIA DGX Spark 계열, GB10 Grace Blackwell)

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | **2026-08-22 01:42:23 KST** (`finished_at` = 2026-08-21T16:42:23Z) — 2026-08-22 검증 run(`run_20260821_0007`)이 `run_manifest.json`을 열어 확정했다. 디렉터리 mtime `2026-08-22 01:42:23.886773379 +0900`이 같은 값을 가리킨다. `run_id`가 `run_20260821_0004`인 것이 모순이 아닌 이유도 여기서 확인된다: `run_id`는 `started_at`의 **UTC 날짜**에서 나오고 이 run의 `started_at`이 2026-08-21T16:42:11Z다 |
| 실제 서빙 모델 | `hf://Qwen/Qwen3-4B-Instruct-2507-FP8@8591804019c8b22094c3b5b4454e0edc05dffc98`<br>별칭 `qwen3-4b` — **별칭은 근거가 못 된다.** 스냅샷 경로가 revision과 일치하는 것을 기동 로그의 `model_tag`로 확인했다:<br>`/srv/tenants/user/.cache/huggingface/hub/models--Qwen--Qwen3-4B-Instruct-2507-FP8/snapshots/8591804019c8b22094c3b5b4454e0edc05dffc98` |
| `repo_commit` | **`54c80705fb848f4852dc2163c19b1a2541bf61b5`** · `repo_dirty` **false** — 2026-08-22 검증 run(`run_20260821_0007`)이 확정했다. 기록 당시의 추정(`main@54c8070`)이 맞았고, **확인하지 못했던 dirty 여부도 깨끗한 것으로 확정됐다** |
| `run_id` | `run_20260821_0004` |
| run 산출물 경로·보존 | 경로 `/srv/tenants/user/artifacts/run_20260821_0004` · **보존 확인**(2026-08-22 검증 run(`run_20260821_0007`) 시점). `asan` `build` `clang-tidy` `index` `report.md` `run_manifest.json` `scratch` `semgrep` `state.db`(28,672 B) `trace.jsonl`(18,625 B)이 그대로 있다 — [open-questions.md](../../open-questions.md) **R7은 이 run에서 미실현**이다 |
| `tool_runner` | 프로필 선언 **`fake`** / 실제 **`docker`** — `single_gpu_12g`가 `tool_runner: fake`라 도구를 돌리려면 오버라이드가 **구조적으로 필요했다.** [README.md](../README.md) 미확정 #4의 두 번째 사례다([ADR 0005](../../decisions/0005-tool-runner-override.md)가 상정한 용도가 아니다) |
| `--device` | `atom` |
| `tool_images` digest | **`harness-tools:v1` = `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17`** · 종류 **docker(이미지 config sha256)**([portability.md](../../portability.md) §5 #4). 2026-08-22 검증 run(`run_20260821_0007`)의 digest와 **같다** — 두 run이 같은 이미지로 돌았다 |
| `--gpu-memory-utilization` | **0.2** · 그 시점 분모 **119.7 GiB**(물리 통합메모리) = **23.94 GiB**. 기동 로그가 직접 답했다 — `Free memory on device (109.48/119.7 GiB) on startup` / `Desired GPU memory utilization is (0.2, 23.94 GiB)` |
| 동시에 떠 있던 다른 서버 | **영구 미기재.** 2026-08-22 검증 run(`run_20260821_0007`) 시점(2026-08-21T18:15Z)에는 8001 하나뿐이었으나 **이 run은 16:42Z이고 서버 기동 시각을 찍지 않았다** — 두 시점이 같은 프로세스라는 것을 증명할 수 없으므로 그 관측을 이 칸으로 옮기면 지어낸 근거가 된다. 제한 단위가 세션이 아니라 사용자(`user-1004`)라 이 칸이 비면 throttle 여부를 사후에 못 가른다([README.md](../README.md) 확정 #4) |
| 원문 산출물 보존 | **기록 당시에는 터미널 출력만.** 2026-08-22 검증 run(`run_20260821_0007`)이 `run_manifest.json`을 열었고 `trace.jsonl`·`state.db`도 디스크에 남아 있음을 확인했다 — **다만 그 둘의 내용은 아직 읽지 않았다**(§5) |

---

## 1. 요약

**경로 성립 판정이다. 성능 판정이 아니다.**

| 항목 | 결과 |
| --- | --- |
| fp8 4B 실서빙 + 하네스 파이프라인 0~9단계 | **완주** (후보 2건 / Finding 1건) |
| scout 실모델 판정 | **성립** — 후보 하나를 0.00으로 실제로 걸러냈다 |
| 1단계 인덱서 산출물 로드 | **성립** — `graph_index status=loaded`, 심볼 4 / 호출 5 / unresolved 0 |
| 캡슐 그래프 섹션 본문 적재(W1) | **데이터 경로만 성립** — `expanded` 캡슐에 본문이 실렸다. **모델 판단에 미친 영향은 확인되지 않았다**(§3 해석 ⑤) |
| 8단계 게이트 재판정(W3) | **성립** — stage 8에 `gate_eval`이 남았다 |
| 장부 합산 | **일치** — `model_call` 4건의 합이 최종 `budget`과 같다. **2026-08-22 추가**: `tool_call` 9건의 합도 `tool_ms` 3,804와 정확히 같다(§3 해석 ①') |
| prefix cache | **장부는 0, 서버는 19.9%** — [open-questions.md](../../open-questions.md) R6이 `cli run` 경로에서도 성립 |
| 탐지 품질 | **판정 불가** — auditor·critic이 Fake이고 표본이 2건이다 |

---

## 2. 실험 환경

**갈리는 항목을 먼저 적는다.**

| 축 | 값 |
| --- | --- |
| 역할 구성 | **scout만 실서빙**(`qwen3-4b`), auditor·critic은 **Fake** |
| 프로필 | `single_gpu_12g` (`retriever: fake`, `tool_runner: fake` → docker로 오버라이드) |
| 분석 대상 | `tests/fixtures/target` (`parser.c`, `util.c`) |
| 양자화 | `quantization=fp8`, `dtype=torch.bfloat16`, 커널 `CutlassFp8BlockScaledMMKernel` |
| 기동 조건 | `VLLM_USE_DEEP_GEMM=0` — [README.md](../README.md) 확정 #3 ③. 나머지 두 조건(①`CPATH` ②`VLLM_ATTENTION_BACKEND`)의 지정 여부는 **영구 미기재** — 사유는 §5 |
| KV cache | 18.14 GiB · **132,112 tokens** · `max-model-len 32768` → 최대 동시성 **4.03x** |
| 메모리 실사용 | weights+non-torch 5.7 GiB / peak activation 0.1 GiB / CUDAGraph 0.7 GiB / 모델 로딩 4.27 GiB·23.96 s |
| prefix caching | **on** |

---

## 3. 실험 1 — scout 실모델로 파이프라인을 완주시킨다

### 목적

**경로 성립 판정.** fp8 4B가 하네스의 출력 계약(JSON 스키마)을 지키면서 0~9단계를 끝까지 도는가.
성능·품질은 이 실험의 대상이 아니다 — 뒤 두 역할이 Fake이기 때문이다.

### 조건

`single_gpu_12g` 프로필, scout만 실서빙, `--tool-runner docker` 오버라이드,
대상 `tests/fixtures/target`, `--device atom`, artifacts는 저장소 밖.

### 결과

| 항목 | 결과 |
| --- | --- |
| 완주 | 예 (0~9단계) |
| 후보 | 2건 (`cand_0001`, `cand_0002`) |
| Finding | 1건 |
| 모델 호출 | 4건 (scout 2 · auditor 1 · critic 1) |
| 최종 장부 | input **4,389** / output **555** / `gpu_ms` **8,316** / `tool_ms` **3,804** |
| 게이트 등급 | stage 6 **E2** → stage 7 **E3** → stage 8 **E3** |
| 캡슐 | 3건 (light 2 · expanded 1) |

### 근거 로그

**`model_call`**

```
stage 4 scout   qwen3-4b  in 1459 out 164 cached 0 gpu_ms 4814
stage 5 auditor fake      in  979 out 171 cached 0 gpu_ms None
stage 4 scout   qwen3-4b  in 1516 out 149 cached 0 gpu_ms 3502
stage 7 critic  fake      in  435 out  71 cached 0 gpu_ms None
```

**`policy_decision` (`scout_triage`)**

```
cand_0001 score 0.95 → advance
cand_0002 score 0.00 → queued (below_scout_threshold)
```

**`gate_eval`**

```
stage 6  E2   location_ok: False  p4: False
stage 7  E3   location_ok: False  p4: False
stage 8  E3   location_ok: False  p4: False
```

**`graph_index`**

```
status loaded   symbols 4   calls 5   unresolved 0
```

**`budget` (최종)**

```
input 4389   output 555   gpu_ms 8316   tool_ms 3804
```

**capsule (`state.db`의 `objects` 테이블, `kind='capsule'`)**

```
cap_0001 light    parser.c  target_function(146, body[])  static_signal(64, body[])
cap_0003 light    util.c    target_function(150, body[])  static_signal(64, body[])
cap_0002 expanded parser.c  target_function(146, body[])
                            callers(0,  items 0, body[])
                            callees(10, items 2,
                                    body ["handle_header parser.c:25", "memcpy parser.c:24"])
                            static_signal(64, body[])

세 캡슐 모두 target = {file, symbol: null, line_start: null, line_end: null}
```

**`tool_call` (2026-08-22 검증에서 `trace.jsonl`을 열어 확보)**

`argv`는 **요청 argv**다 — `context.call_model` 옆의 `call_tool`이 `argv=" ".join(args.argv)`로 요청을 싣고, 실제 컨테이너 명령은 `container_tools.commands_for()`가 도구 이름과 소스에서 따로 조립한다. 8단계 두 줄의 `argv`가 같아 보이는 것이 그 때문이다.

```
stage 1  index       238ms  ok=true   exit 0   index --callgraph .
stage 2  semgrep     955ms  ok=true   exit 1   semgrep --quiet parser.c
stage 2  clang-tidy  274ms  ok=true   exit 1   clang-tidy --quiet parser.c
stage 2  build       252ms  ok=true   exit 0   build --quiet parser.c
stage 2  semgrep     931ms  ok=true   exit 1   semgrep --quiet util.c
stage 2  clang-tidy  256ms  ok=true   exit 1   clang-tidy --quiet util.c
stage 2  build       255ms  ok=true   exit 0   build --quiet util.c
stage 8  build       286ms  ok=true   exit 0   build ./build.sh --sanitize=address ./poc parser.c   find_0001
stage 8  asan        357ms  ok=false  exit 1   asan  ./build.sh --sanitize=address ./poc parser.c   find_0001
                            error: "asan: 빌드 단계에서 끊겨 프로그램이 실행되지 않았다"
```

**vLLM 서버 로그**

```
Prefix cache hit rate: 19.9%
```

### 해석

**① 장부가 네 호출을 전부 세었다.**
`1459+979+1516+435 = 4389`, `164+171+149+71 = 555`, `4814+3502 = 8316`.
세 축 모두 `model_call` 합과 최종 `budget`이 정확히 같다. 새는 호출이 없다.

**①' 도구 시간도 같다 — 2026-08-22에 확인했다.**
`238+955+274+252+931+256+255+286+357 = 3804`. `tool_call` 아홉 건의 `wall_ms` 합이 최종 `budget`의 `tool_ms`와 정확히 같다.
**이 축은 기록 당시 합만 있었고 내역이 없었다.** 모델 호출 쪽은 처음부터 대조됐지만 도구 쪽은 대조된 적이 없었는데, 이제 됐다.

**② `gpu_ms`가 실서빙 호출에만 있다.**
Fake 두 호출은 `None`이다 — 세는 것은 "GPU를 안 썼다"가 아니라 **"안 쟀다"**이고, 그것이
모델 비교표 제외 판정의 입력이 된다. **이 run의 실제 제외 판정은 `run_manifest.json`을
열지 않아 미기재다.**

**③ scout이 실제로 후보를 걸렀다.**
`cand_0002`가 0.00으로 `below_scout_threshold`에 걸렸다. 실모델 scout이 임계값 판정에
들어간 **첫 기록**이다. 표본 2건이므로 **임계값에 대해서는 아무것도 따라 나오지 않는다**
([open-questions.md](../../open-questions.md) D2).

**④ scout이 본 캡슐에는 호출 그래프가 없었다.**
`cand_0001`·`cand_0002`가 받은 것은 `cap_0001`·`cap_0003`이고 둘 다 `light`다.
`light`의 섹션 구성은 `("target_function", "static_signal")`이라 `callers`/`callees`가
**구조적으로 들어가지 않는다.** 인덱서가 정상으로 돌아 심볼 4·호출 5를 냈는데도(위
`graph_index`) 그 결과를 scout은 보지 못했다. 이것을 결정 대기 항목
[**D20**](../../open-questions.md)으로 열었다.

**⑤ W1의 데이터 경로는 성립했고, 모델 판단에 미친 영향은 확인되지 않았다.**
`cap_0002`(expanded)의 `callees`에 본문 두 줄이 실렸다 — `tokens=10`, `items=2`가 그
줄들에서 계산된 값과 일치하므로 캡슐 스키마 검증기를 통과한 진짜 값이다.
**그러나 그 캡슐을 받은 auditor는 Fake였고**, 실서빙된 유일한 역할인 scout은 ④ 때문에
그래프를 못 봤다. **실모델이 그래프 줄을 보고 판단을 바꿨는지는 이 run이 답하지 않는다.**
같은 이유로 [open-questions.md](../../open-questions.md) D19(프롬프트 본문 미영속)가
그 확인을 막고 있었다 — 프롬프트가 산출물에 남지 않아 "모델이 실제로 무엇을 봤는가"를
사후에 물을 수 없었다.

**⑥ `callers`가 0건인 것은 그래프가 비어서가 아니다.**
`callers(0, items 0)` 섹션이 **발행됐다**는 것이 요점이다 — index.json이 없으면 섹션
자체가 부재해야 한다([knowledge-base.md](../../knowledge-base.md) §4). 즉 "봤는데 들어오는
호출이 0건"이다. `parser.c` 안의 함수를 부르는 외부 파일이 픽스처에 없으므로 예상된 값이다.

**⑦ `location_ok: False`가 세 게이트 전부에서 나온 것은 결함이 아니다.**
세 캡슐의 `target`이 전부 `symbol: null, line_start: null`이다. 지금 캡슐 대상은 **파일
단위**이므로 `Evidence.location`이 후보 위치와 겹칠 일이 사실상 없다 — Gate E1의
`location_ok`는 **심볼 단위 캡슐이 들어와야 작동을 시작한다**([STATUS.md](../../../STATUS.md)
§10, [open-questions.md](../../open-questions.md) D13의 '심볼 단위 캡슐' 항목). 이 run은 그
예고가 실행에서 그대로 관측된 첫 기록이다.

**⑧ 8단계가 게이트를 불렀다.**
stage 8에 `gate_eval`이 남았다 — W3 배선이 실서빙 run에서 동작한다. 다만 등급을 **소비**하는
쪽은 여전히 그대로다([open-questions.md](../../open-questions.md) D18).

**⑧' 8단계 asan이 실패한 것은 결함이 아니다 — 그러나 넘어갈 일도 아니다.**
같은 단계의 `build`는 `ok=true exit 0`인데 바로 다음 `asan`이 `ok=false`다. **두 도구가 같은 빌드를 도는 것이 아니다**: `commands_for()`가 `build`를 `clang -c -g -O1`(오브젝트까지, 링크 없음)로, `asan`을 `clang -fsanitize=address,undefined … -o {SCRATCH}/asan.bin` + 그 바이너리 실행 **두 명령**으로 조립한다. `-c`는 `main` 없이 통과하고 실행 파일 링크는 통과하지 못한다 — **픽스처 두 파일에 `main`이 없다.** 실패 문구도 거기서 나온다: `_RUNTIME_TOOLS` 분기가 `len(steps) < total`일 때 "빌드 단계에서 끊겨 프로그램이 실행되지 않았다"를 돌려주므로 **끊긴 것은 `asan` 자신의 링크 단계**다. `container_tools`의 모듈 docstring이 이미 같은 말을 적어 뒀다 — "지금 픽스처에는 `main`이 없어 링크되지 않으므로 **E4는 여전히 도달 불가**다".

**모르는 것**: 그 clang이 어떤 링크 에러로 죽었는지는 이 데이터에 없다. trace의 `exit 1`은 판정기가 돌려준 값이고 `_EXEC_FAILED = (125, 126, 127)`에 안 걸렸으므로 "실행 실패가 아니라 빌드 중단"까지만 말할 수 있다. 에러 원문은 Evidence 로그에 있고 열지 않았다.

**게이트에 미친 영향은 확실하다.** `gate.py`가 `usable = [ev for ev in evidences if ev.ok]`로 거르고 `"p4": any(ev.kind in _RUNTIME_KINDS for ev in usable)`이므로, `asan`이 `ok=False`라 `usable`에서 빠지고 **p4가 False**다. **stage 8이 E3에 머문 구체 근거가 이것이다.** [open-questions.md](../../open-questions.md) **D18과는 층이 다르다** — 이쪽은 *등급이 왜 E3인가*(게이트)이고 D18은 *그 E3가 왜 소비되지 않는가*(정책)다.

**여기서 멈추면 안 되는 이유 — p4는 구조적으로 닫혀 있다.** `_RUNTIME_KINDS`(`sanitizer`/`crash`/`poc_run`)를 내는 도구는 `TOOL_KINDS`에서 **`asan`과 `poc` 둘뿐**이고(`crash`는 생산자가 아예 없다), **둘 다 닫혀 있다**:

| 경로 | 왜 닫혀 있나 |
| --- | --- |
| `asan` | `main` 없는 픽스처에서 링크 실패 → `ok=False` → `usable`에서 빠진다 |
| `poc` | `commands_for()`가 `{SCRATCH}/poc` 실행을 내는데 **그 파일을 만드는 주체가 없다**(`container_tools` 주석, [STATUS.md](../../../STATUS.md) §5 PoV) |

→ **E4는 지금까지 어떤 run에서도 성립한 적이 없다.** 결함이 아니라 **측정 타당성의 문제**다. 등급 분포나 단계별 제거율을 논할 때 E4는 *분자가 0*인 것이 아니라 **분자가 될 수 없는 상태**다. 둘을 같은 칸에 넣으면 "재현까지 간 Finding이 없다"가 "재현 경로가 열려 있는데 아무도 못 갔다"로 읽힌다. [STATUS.md](../../../STATUS.md) §10이 추적하는 분모 변화와 같은 계열의 사실이다.

**⑨ prefix cache: 서버는 19.9%, 장부는 0.**
`model_call` 네 건 모두 `cached 0`인데 같은 run의 vLLM 서버는 `Prefix cache hit rate: 19.9%`를
찍었다. R6이 지금까지 `cli bench` 경로에서만 관측됐는데 **`cli run` 경로에서도 같다**는
것이 이 run으로 확인됐다. 서버가 재사용을 하고 있으므로 이것은 "캐시가 안 맞았다"가
아니라 **"응답이 그 사실을 싣지 않는다"**이다.

---

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| `single_gpu_12g`로 도구를 돌릴 수 없다 | 그 프로필의 `tool_runner`가 `fake`다 | `--tool-runner docker` 오버라이드. **문서를 고쳐도 사라지지 않는 벽이다** — [README.md](../README.md) 미확정 #4가 이 항목이고, 이번이 두 번째 사례다 |
| fp8 모델이 뜨지 않는다 | DeepGEMM이 scale-factor 레이아웃을 변환하지 못한다 | `VLLM_USE_DEEP_GEMM=0` — 확정 #3 ③. 새로 막힌 것이 아니라 **기지의 조건을 적용한 것**이다 |

---

## 5. 미측정 항목과 사유

**절반이 채워졌다.** 2026-08-22 검증 run이 이 run의 산출물을 열었기 때문이다. 채워진 것과 못 채운 것을 갈라 적는다 — **둘을 한 표에 두면 "언젠가 재면 된다"와 "이제 잴 수 없다"가 같은 칸에 들어간다.**

### 5-1. 2026-08-22 검증에서 채워진 것

`run_20260821_0007` 검증 때 이 run의 `run_manifest.json`과 디렉터리를 열어 확정했다.

| 항목 | 값 | 무엇으로 |
| --- | --- | --- |
| 실험 종료 시각 (분 단위) | **2026-08-22 01:42:23 KST** (`finished_at` 16:42:23Z) | `run_manifest.json` · 디렉터리 mtime |
| `repo_commit` + dirty | **`54c80705fb848f4852dc2163c19b1a2541bf61b5`** · dirty **false** | `run_manifest.json` |
| run 산출물 보존 여부 | **보존됨** — **R7 미실현** | `ls` |
| `tool_images` digest + 종류 | **`harness-tools:v1` = `sha256:b5d7048b…`** · docker | `run_manifest.json` |
| 모델 비교표 제외 판정 | **1건** — `kb_snapshot`이 자리표시자(`kb-none`)다 | `runner.incomplete_fields()` |
| `tool_call` 개별 항목 | **9건** — §3 근거 로그. 합이 `tool_ms` 3,804와 **일치** | `trace.jsonl` |

**비교표 제외 사유가 하나뿐인 것이 관측이다.** `tool_images`가 차 있고 `repo_dirty`가 false이며 `device`가 `atom`이므로, 남은 것은 KB 미구축 하나다 — 즉 **KB가 생기면 이 run은 비교표에 들어갈 수 있는 상태**다.

### 5-2. 여전히 미측정

**못 잰 값을 0으로 채우지 않는다.** **앞의 두 행은 영구다** — 시간이 지나 관측 기회가 사라졌다. 뒤의 두 행은 원자료를 확보하면 아직 쓸 수 있다.

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| **동시에 떠 있던 다른 서버** | **영구.** 검증 시점(18:15Z)에는 8001 하나뿐이었으나 이 run은 16:42Z이고 **서버 기동 시각을 찍지 않아** 같은 프로세스임을 증명할 수 없다 | 이 run에 대해서는 **없다.** 다음 실험에서 `ss -ltnp`를 그때 적는다 |
| **기동 조건 ①`CPATH` ②`VLLM_ATTENTION_BACKEND` 지정 여부** | **영구.** 위와 같은 이유 — 18:15Z의 `/proc/<pid>/environ` 덤프를 16:42Z의 조건으로 옮길 수 없다 | 이 run에 대해서는 **없다.** [README.md](../README.md) 미확정 #2의 입력은 검증 run 쪽에서 나왔다 |
| **`dev_docker` run 2건 (같은 날)** | **미수록** — 원자료가 이 기록에 없다 | 해당 run 디렉터리의 `trace.jsonl`·`run_manifest.json`을 확보해 별도 §를 추가하거나 별도 보고서로 연다 |
| **`cli bench` 1건 (같은 날)** | **미수록** — stdout JSON을 보존하지 않았다 | 재실행 시 stdout을 파일로 받는다. [README.md](../README.md)가 이미 같은 전례("`bench` stdout을 놓쳐 측정 기록이 재구성본이 됐다")를 적고 있다 |

---

## 6. 반증 조건

**이 run이 실패했다면 무엇이 틀린 것이었나.**

- **기동에서 실패했다면** — `VLLM_USE_DEEP_GEMM=0`만으로는 sm_121에서 fp8 4B가 뜨지 않는다는
  뜻이었을 것이다. 떴으므로 확정 #3 ③은 이 조합에서 충분하다.
- **스키마 파싱에서 실패했다면** — 4B 모델이 `Candidate` 계약을 지킬 수 없다는 뜻이었을 것이다.
  scout 2회 모두 통과했으므로 **모델 크기 때문에 계약이 깨지지는 않는다**가 배제됐다.
- **`graph_index`가 `missing`으로 왔다면** — 인덱서 산출물이 Retriever에 닿지 않는다는 뜻,
  즉 D13의 ①(전달 경로)이 닫혔다는 판정이 틀린 것이었다. `loaded`가 왔으므로 그 경로는
  실장비에서도 선다.

**배제되지 않은 것**: 탐지 품질, 임계값의 적절성, 실모델이 그래프를 활용하는지 여부.
이 run은 **경로가 선다**만 말한다.

---

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| ~~높음~~ | ~~§5의 `run_manifest.json` 파생 항목 5개 확보~~ | **2026-08-22 완료** — 산출물이 살아 있었다(§5-1). `tool_call` 내역도 함께 확보했다 |
| 높음 | **D19 — 프롬프트 본문을 run 산출물에 남긴다** | **이 보고서와 같은 PR에서 구현한다.** 그러면 다음 run부터 §3 해석 ⑤가 답 가능해진다 |
| 높음 | **D20 — scout이 그래프를 못 보는 것이 의도인가** | 이 보고서에서 신설. 결정 전에는 `LEVEL_SECTIONS`를 건드리지 않는다 |
| 중간 | auditor·critic 실서빙 | 품질 판정의 전제. 지금은 Fake라 제거율이 성립하지 않는다 |
| 중간 | R6 — `cached_tokens`가 0인 원인을 ATOM에서 확정 | 서버가 19.9%를 아는데 응답이 싣지 않는다는 것까지 좁혀졌다 |
| 낮음 | 미확정 #4 — `single_gpu_12g`의 `tool_runner: fake` | 두 번째 사례가 쌓였다. `config/` 변경이라 별도 PR |

---

## 8. 정리 상태

- 서빙 서버: **미기재** (8001 종료 여부)
- 임시 설정·프로필: 오버라이드는 명령 인자라 원복 대상 없음. 환경변수 원복 여부 **미기재**
- 저장소 변경: **없음** (이 run은 `main@54c8070` 기준으로 돌았다)

---

## 9. 부록 — 재현 절차

**vLLM 기동 명령 원문은 미보존이다.** `VLLM_USE_DEEP_GEMM=0`이 필요했다는 것과 아래 표의
기동 인자 외에는 원문이 남아 있지 않다(§5).

| 인자 | 값 |
| --- | --- |
| 모델 | `Qwen/Qwen3-4B-Instruct-2507-FP8` @ `8591804019c8b22094c3b5b4454e0edc05dffc98` |
| `--served-model-name` | `qwen3-4b` |
| `--gpu-memory-utilization` | `0.2` |
| `--max-model-len` | `32768` |
| prefix caching | on |
| 환경변수 | `VLLM_USE_DEEP_GEMM=0` — **그 외는 영구 미기재**(§5-2). 2026-08-22 검증 run의 `/proc/<pid>/environ` 덤프는 그 시점의 서버이고 이 run의 것이라는 근거가 없다 |

하네스 실행 명령은 원문이 남아 있다.

```bash
python -m src.cli run --profile single_gpu_12g --tool-runner docker \
  --target tests/fixtures/target --device atom \
  --artifacts-root /srv/tenants/user/artifacts
```
