# track_a PWN-003 첫 완주 — b1_single · ATOM

> 대상: `origin/main`(`3851c37`) 위에 측정 전용 커밋 2개. **run이 넷이고 서빙 서버가 둘이다.**
> 완주한 것은 네 번째(`run_20260827_0004`)이고, **앞의 셋이 왜 못 갔는지가 이 보고서의 절반이다.**
>
> **여기 적힌 수치는 run 산출물(`run_manifest.json`·`trace.jsonl`·`state.db`)과 보존된 서버
> 로그에서 다시 읽은 것이다.** 파일로 안 남은 인용에는 `세션 stdout(파일 미보존)` 표시를
> 붙였다. 안 잰 것은 §5에 있다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | **2026-08-28 00:47:14 KST** (= `2026-08-27T15:47:14Z`, `run_20260827_0004`의 `finished_at`). 세션 시작은 2026-08-27 23:45 KST(서버 A 기동) |
| 실제 서빙 모델 | `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` (별칭 `qwen3-coder-30b-a3b`). **근거**: trace `token_counter`의 `scout_source`·`auditor_source`가 그 문자열이고 기동 명령의 스냅샷 경로와 같다 |
| `repo_commit` | `run_0001` = `3f93e1cce0e1785054cb84517459322ae27f7d34` · `run_0004` = `1b43d55a3d6182f3b323ab2714bb512bc6aa675a` (둘 다 dirty **False**). **`run_0002`·`run_0003`은 미기재** — 매니페스트를 남기기 전에 죽었다 |
| `run_id` | `run_20260827_0001` ~ `run_20260827_0004`. **날짜가 UTC다** — `cli.py`가 `new_run_id(datetime.now(UTC))`를 쓰므로 KST 08-28 00:45에 시작한 run이 `20260827`로 찍힌다. 이 파일 이름(종료일 KST)과 하루 어긋나는 것이 정상이며, run_id로 날짜를 읽으면 안 된다 |
| run 산출물 경로·보존 여부 | `/srv/tenants/user/artifacts/run_20260827_000{1,2,3,4}` — **넷 다 보존.** 단 `0002`·`0003`에는 `report.md`도 `run_manifest.json`도 **없다**(생성 전에 죽었다) |
| `tool_runner` | 프로필 선언 **`podman`**(`config/profiles.yaml`의 `b1_single`) / 실제 **`docker`**. `--tool-runner docker` 오버라이드이며 [ADR 0005](../../decisions/0005-tool-runner-override.md) 예외다. **이번 세션 run 넷 전부 해당** |
| `--device` | `atom` |
| `tool_images` digest | `harness-tools:v1` = `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17` (docker `RepoDigests`) — 8/25·8/26과 **동일** |
| `--gpu-memory-utilization` | **0.28** · 분모 물리 통합메모리 **119.7 GiB** → 33.52 GiB. 두 서버 같다 |
| 동시에 떠 있던 다른 서버 | **없다.** 서버 A 기동 전 `pgrep -af "vllm serve"` 0건 · `nvidia-smi --query-compute-apps` 0건(세션 stdout(파일 미보존)). A→B는 `pkill` 후 재기동이라 겹치지 않았다 |
| 원문 산출물 보존 위치 | 위 run 디렉터리 4개 + `/srv/tenants/user/artifacts/logs/` |
| 서빙 서버 stdout | **보존.** 서버 A `logs/serve_30b_20260827_234536.log` · 서버 B `logs/serve_30b_nows_20260828_003841.log`. A는 `run_20260827_0001/`에도 복사돼 있다 |

### run 네 개

| run | 시각 (KST) | 대상 | 서버 | 결과 |
| --- | --- | --- | :-: | --- |
| `run_0001` | 00:01:40 ~ 00:09:12 (7 m 32 s) | zlib (심볼 542 중 64 실행) | A | **중단** — `stopped_after: 5`, 후보 3 / Finding 0 |
| `run_0002` | 00:15:24 ~ 00:31:50 (16 m 26 s) | zlib (같음) | A | **사망** — critic `SchemaParseError`, 산출물 없음 |
| `run_0003` | 00:45:03 (기록 2건) | **미기재** | B | **사망** — `preprocess` 1단계만 남았다 |
| `run_0004` | 00:45:31 ~ 00:47:14 (1 m 43 s) | track_a `blind/PWN-003/source` | B | **완주** — `stopped_after: null` |

### 서빙 서버 두 대 — run이 갈린다

| 항목 | 서버 A | 서버 B |
| --- | --- | --- |
| 로그 | `serve_30b_20260827_234536.log` | `serve_30b_nows_20260828_003841.log` |
| 수명 (KST) | 08-27 23:45:53 ~ 08-28 00:38:36 | 08-28 00:40:56 ~ 00:47:28 (마지막 기록) |
| structured outputs | `backend='auto'` · `disable_any_whitespace=False` | `backend='xgrammar'` · `disable_any_whitespace=True` |
| GPU KV cache | 281,504 tok | 278,432 tok |
| 최대 동시성 (32,768 tok/req) | 8.59x | 8.50x |
| 기동 시 free memory | 112.47 / 119.7 GiB | 107.5 / 119.7 GiB |
| 이 서버로 돈 run | `0001` · `0002` | `0003` · `0004` |

**두 기동은 `--structured-outputs-config` 하나만 다른데 KV가 3,072 토큰 줄었다. 이 보고서는
그 인과를 주장하지 않는다** — 기동 시 free memory가 112.47 vs 107.5 GiB로 다르고, 같은 옵션으로
두 번 띄운 대조가 없다. **옵션이 KV를 줄였다는 근거는 이 세션에 없다.**

## 1. 요약

**무엇을 확인하려 했나**: `b1_single`(scout·auditor·critic 세 역할 전부 `qwen3-coder-30b-a3b`
실서빙)이 track_a 평가 데이터셋 샘플 하나를 끝까지 도는가. **경로 성립 판정이고 성능 측정이
아니다** — 표본 1건이다.

| 항목 | 결과 |
| --- | --- |
| **완주** | `run_20260827_0004` · **1 m 43 s** · `stopped_after: null` |
| **track_a 첫 투입** | 하네스가 `blind/PWN-003/source`를 그대로 먹었다 — artifact 5개 중 분석대상 4 · **심볼 후보 19 · 전량 실행**(`deferred: 0`) |
| 모델 호출 | **33건**(scout 19 · auditor 7 · critic 7) · **실패 0건** · wall_ms 1,781 / 2,386 / 5,391 (min/중앙/max) |
| Finding | **7건.** 단 `report.md`에는 **6건**이다 — 9단계 `dedupe()`가 `find_0005`를 지웠다 |
| 게이트 등급 | **이벤트 16건** = `gate_eval` 14 + `gate_ambiguous` 2. 6·7단계에 각각 한 번씩 매기므로 **Finding 단위로는 E2 6건 · E1 1건**이고 **E3·E4는 0건** |
| E3·E4가 0인 직접 원인 | **8단계에 진입하지 않았다.** `repro_spec`이 없어 7단계가 `hold`했다 — 8/26의 D38(Fake auditor)과 **원인이 다르다** |
| 정답 대조 | **불합격.** 근본 원인 `router_define`을 못 짚었고 CWE도 다르다(정답 CWE-843) |
| 폭주 두 건 | 원인은 **하네스가 `max_tokens`를 안 보낸다**는 것. 해법은 **서버 옵션**이고 하네스 수정 0 |
| 신규 결함 넷 | 요청당 출력 상한 없음(D42) · 캡슐 재검증이 카운터 밖(D43) · critic 파싱 실패가 run을 죽임(D44) · **실행한 대상의 심볼이 기록에 없다**(D45) |
| 부수 확정 | **CUDA 통합메모리는 cgroup에 계상되지 않는다** — vLLM이 `user-1004.slice` 안에 있는데 `memory.current`가 12.84 GiB뿐이다([README.md](../README.md) 미확정 #1의 답, §2) |

**이 run이 답하지 않는 것**: 탐지 성능(표본 1) · 다른 샘플 · 공백 폭주가 모델의 성질인지
이 스키마와의 상호작용인지 · 장부 예산 계상의 정확성(§5).

## 2. 실험 환경

| 항목 | 값 |
| --- | --- |
| 장비 | GIGABYTE AI TOP ATOM (NVIDIA GB10, sm_121, aarch64) · DGX OS 7.4.0 · 커널 6.11.0-1016-nvidia |
| 드라이버 · CUDA | **미기재** — 이번 세션에서 읽지 않았다 |
| vLLM · Python | 0.27.1 · 3.12.3 (세션 stdout(파일 미보존)) |
| `kv_cache_dtype` | `fp8_e4m3` (두 서버 동일) |
| attention backend | **FLASHINFER** — 후보 `['FLASHINFER', 'TRITON_ATTN']`. **8/25 NVFP4와 같고 두 번째 확인이다.** 8/26의 4B는 `FLASH_ATTN`이었다([재측정 보고서](2026-08-26-pipeline-zlib542-remeasure-atom.md)) |
| NVFP4 커널 | `FlashInferCutlassNvFp4LinearKernel` · MoE `FLASHINFER_CUTLASS` |
| `VLLM_ATTENTION_BACKEND` | **주지 않았다.** flashinfer가 선택됐는데도 두 서버 다 기동했다 — [README.md](../README.md) 확정 #3 ②는 "flashinfer가 선택될 때 nvcc가 필요하다"인데 **NVFP4 경로에서는 발동하지 않는다.** 8/25도 FLASHINFER를 썼으나 **그 보고서는 기동 명령 전체 원문을 미기재로 남겨 그때 이 변수를 줬는지 알 수 없다** — 그래서 "안 주고도 뜬다"를 명령 원문과 함께 남기는 것은 이 기록이 처음이다 |
| cgroup (`user-1004.slice`) | `memory.current` **13,790,609,408 B = 12.84 GiB** · `memory.high` **`max`**(제한 없음) · `memory.max` **68,719,476,736 = 64 GiB**. 2026-08-28 02:4x KST, 서버 B가 idle로 떠 있는 상태에서 읽었다 |

### 8/25 30B와의 대조 — `util`만 다르다

| 항목 | 8/25 `util 0.3` | 서버 A `util 0.28` | 서버 B `util 0.28` |
| --- | --- | --- | --- |
| GPU KV cache | 340,416 tok | 281,504 tok | 278,432 tok |
| 최대 동시성 | 10.39x | 8.59x | 8.50x |
| desired | 35.91 GiB | 33.52 GiB | 33.52 GiB |
| 실사용 (**KV 제외**) | 18.24 + 2.08 + 2.17 = **22.49 GiB** | 17.99 + 2.64 + 2.16 = **22.79 GiB** | 18.18 + 2.59 + 2.14 = **22.91 GiB** |
| KV 사용 | **미기재** (그 보고서에 없다) | 12.89 GiB | 12.75 GiB |
| 합 (**KV 포함**) | 산출 불가 | **35.68 GiB** | **35.66 GiB** |

8/25 열의 출처는 [NVFP4 보고서](2026-08-25-serving-nvfp4-atom.md) §4의 근거 로그다.

**기준을 밝히지 않으면 두 보고서가 갈린 것처럼 읽힌다.** 8/25가 적은 "실사용 22.49 GiB"는
**KV를 뺀 값**이고 이번 35.7 GiB는 **KV를 포함한 값**이다. 같은 기준으로 자르면 22.49 →
22.79 → 22.91로 **거의 같다** — `util`을 0.3에서 0.28로 내려도 가중치·활성화·CUDA graph는
안 줄고 **KV만 줄었다.**

**KV를 포함한 35.7 GiB는 멘토 안내 34 GiB를 약 1.7 GiB 넘는다**([README.md](../README.md) 확정
\#4 — 34는 팀 약속선이고 시스템 하드 한도는 64 GiB다. 하드 한도까지는 약 28 GiB 남는다).
**그 34 GiB가 cgroup에 잡히는 값인지는 README 미확정 #1이었고, 이번에 읽어서 답이 나왔다 —
아래 소절이 그것이다.**

작성 시점(2026-08-28)에도 서버 B가 떠 있어 `nvidia-smi --query-compute-apps`가
**32,940 MiB(32.2 GiB)**를 보고한다. vLLM 자기 회계의 35.66 GiB와 다르고 **무엇이 두 값을
가르는지는 안 쟀다** — 이 보고서가 쓰는 값은 vLLM 회계 쪽이고, `nvidia-smi` 값은 참고다.

### 추가 측정 — CUDA 통합메모리는 이 cgroup에 계상되지 않는다 (미확정 #1 답)

2026-08-28 02:4x KST, 서버 B가 **떠 있는 채로**(idle) 세 값을 함께 읽었다.

```
$ cat /proc/78448/cgroup
0::/user.slice/user-1004.slice/session-18873.scope

$ cat /sys/fs/cgroup/user.slice/user-1004.slice/{memory.current,memory.high,memory.max}
13790609408        # memory.current = 12.84 GiB
max                # memory.high  = 제한 없음
68719476736        # memory.max   = 64 GiB
```

**vLLM 프로세스는 이 slice 안에 있다** — `/proc/78448/cgroup`이
`user-1004.slice/session-18873.scope`이고 그 scope는 `user-1004.slice`의 자식이라
`memory.current`에 합산된다. **그런데 그 값이 12.84 GiB뿐이다.** 같은 시각 vLLM 자기 회계는
35.66 GiB, `nvidia-smi`는 32.2 GiB를 가리킨다.

**따라서 [README.md](../README.md) 미확정 #1의 답은 "미계상"이다.** 그 항목이 세운 판별식은
"util 0.2로 띄운 상태에서 24 GiB 근처면 계상, 4~5 GiB면 미계상"이고, util 0.28이면 계상 쪽
기대값이 **33 GiB 이상**인데 실측은 그 3분의 1이다. 모델 가중치 17.99 GiB와 미리 잡아 둔
KV 12.75 GiB는 idle 여부와 무관하게 상주하므로, **계상됐다면 보였어야 한다.**

**따라 나오는 것 — 34 GiB 약속선은 이 손잡이로 강제되지 않는다.** `memory.max` 64 GiB도
`memory.high`(지금 `max`)도 GPU 쪽 할당을 못 막는다. `--gpu-memory-utilization`의 분모가
물리 통합메모리 119.7 GiB이고 cgroup과 **다른 축**이라는 확정 #4의 서술이 여기서 한 번 더
확인된다. **약속을 지키는 수단은 util 값 자체뿐이다.**

**따라 나오지 않는 것**: ① 이 12.84 GiB의 구성(어느 프로세스 몫인지)은 안 봤다 ② 활성
상태(추론 중)의 값은 안 쟀다 — 다만 위 논거는 상주 메모리에 관한 것이라 활성 여부에
좌우되지 않는다 ③ `memory.high`가 `max`인 것은 8/26 재측정이 "58 GiB에서 풀렸다"고 적은
것의 **두 번째 확인**이고, 이 값은 팀이 바꾸므로 실행 시점에 다시 읽어야 한다.

### 샘플링 — "스펙대로 돌았다"고 읽으면 안 된다

두 서버 로그 **모두** 같은 줄을 남겼다.

```
(APIServer pid=4145320) WARNING 08-27 23:49:13 [model.py:1637] Default vLLM sampling parameters
have been overridden by the model's `generation_config.json`:
`{'repetition_penalty': 1.05, 'temperature': 0.7, 'top_k': 20, 'top_p': 0.8}`.
If this is not intended, please relaunch vLLM instance with `--generation-config vllm`.
```

하네스가 요청에 싣는 것은 `config/budgets.yaml`의 `sampling` **둘뿐**이다 —
`temperature: 0.2` · `top_p: 0.95`. `run_manifest.json`의 `sampling`도 그 둘이다. 따라서
요청이 덮는 것은 그 둘이고, **`top_k: 20`과 `repetition_penalty: 1.05`는 모델 파일이 정한
값 그대로 적용됐으며 run 기록 어디에도 남지 않는다.** [open-questions.md](../../open-questions.md)
D29가 이 항목이고, 그 항목이 "깨지는 순간은 모델을 바꿀 때"라고 적어 둔 자리에 이번 실측이 닿는다.

**`repetition_penalty`가 이 저장소 문서에 실리는 것은 이번이 처음이다.** 값 자체는 8/25 30B
서버 로그(`logs/serve_30b_20260825_142256.log`)에도 같은 형태로 있었으나 보고서 본문으로
옮겨지지 않았다 — 8/25·8/26 보고서의 인용은 `temperature`·`top_k`·`top_p` 셋뿐이다.

**"팀 2x2 스펙이 `repetition_penalty 1.0`·`top_k −1`로 고정한다"는 근거는 저장소에서 찾지
못했다.** `config/budgets.yaml`의 `sampling` 블록에 그 두 키가 없다. 그러므로 정확한 서술은
"스펙과 다른 값으로 돌았다"가 아니라 **"하네스가 안 보내는 두 값을 모델 파일이 정했고, 그
사실이 run 기록에 안 남는다"**이다. 해법은 로그가 직접 알려준다 — `--generation-config vllm`.

### prefix cache — 한 값이 아니라 세 시점이다

| 시점 (KST) | 값 | 출처 |
| --- | --- | --- |
| `run_0001` 종료 직후 (00:09경) | **4.8%** | `run_20260827_0001/serve_prefix_cache.txt` |
| 서버 A 마지막 기록 (00:38) | **29.3%** | 서버 A 로그 |
| 서버 B 마지막 기록 (00:47) | **39.5%** | 서버 B 로그 |

**4.8%는 서버 A의 최종값이 아니라 그 시점의 누적값이다.** `serve_prefix_cache.txt`를
`run_0001` 직후에 떠 놓았고, 그 뒤 `run_0002`가 16분 더 돌면서 29.3%까지 올라갔다. 8/25의
30.5% · 8/26의 30.0%와 비교할 값은 **최종값 쪽**이고, 그 기준에서 서버 A는 거의 같고(29.3%)
서버 B는 더 높다(39.5%).

**장부(`cached_tokens`) 대조 — R6 세 번째 점.** `run_0004`의 `model_call` 33건 전체에서
`cached_tokens` 합이 **0**이다. 서버 39.5% 대 장부 0.00%로, 8/25(30.5% vs 0.00%)·8/26(30.0%
vs 0.00%)에 이어 **세 번째로 같은 방향의 대조값**이다. 이 run은 그 원인을 새로 밝히지 않는다.

## 3. 실험 1 — `run_0001`: 출력 상한이 없어 5분을 침묵한다

### 목적
경로 성립 판정. `b1_single`의 세 역할 실서빙이 실제 저장소(zlib) 위에서 도는가.

### 조건
프로필 `b1_single`(scout·auditor·critic = `qwen3-coder-30b-a3b` 실서빙 / retriever `fake` /
tool_runner는 `docker` 오버라이드), `--condition B1prime@pipeline`, 측정 전용 커밋으로
`candidate_max` 256 → **64**, 서버 A.

### 결과

| 항목 | 결과 |
| --- | --- |
| 대상 계획 | 분석대상 79 · 심볼 542 · **실행 64 · 보류 478** |
| 모델 호출 | **4건**(scout 3 · auditor 1) · **`ok=False` 1건** |
| wall_ms (min/중앙/max) | 3,086 / 3,976 / **300,005** |
| 종료 | `stopped_after: 5` · 후보 3 / Finding 0 |
| 서버 측 에러 | **0건** |

### 근거 로그

**① trace 집계** (`run_20260827_0001/trace.jsonl`)

```
이벤트: [('target_skipped', 478), ('budget', 241), ('tool_call', 238), ('stage_start', 90),
         ('stage_end', 90), ('transition', 8), ('cache', 4), ('model_call', 4),
         ('policy_decision', 3), ('token_counter', 1), ('graph_index', 1), ('target_plan', 1)]
model_call: 4 | ok=False: 1
wall_ms  min/median/max: 3086 3976 300005
```

**② 같은 시각 서버는 정상이었다** (서버 A 로그, 25줄 중 처음과 끝)

```
INFO 08-28 00:05:23 [loggers.py:310] Engine 000: Avg prompt throughput: 0.0 tokens/s,
  Avg generation throughput: 57.5 tokens/s, Running: 1 reqs, Waiting: 0 reqs,
  GPU KV cache usage: 5.2%, Prefix cache hit rate: 4.8%
...
INFO 08-28 00:09:13 [loggers.py:310] Engine 000: Avg generation throughput: 45.2 tokens/s,
  Running: 0 reqs, Waiting: 0 reqs, GPU KV cache usage: 0.0%, Prefix cache hit rate: 4.8%
```

`grep -iE "error|timeout|abort|400 Bad"`는 무출력이었다(세션 stdout(파일 미보존) — 로그
파일은 보존돼 있으므로 재현 가능하다).

**③ 코드** (`origin/main` 기준. 측정 커밋이 건드리지 않은 자리다)

```
src/adapters/openai_compat.py:32:  _TIMEOUT_S = 300
$ git grep -n "max_tokens" -- src/
(출력 없음 — 0건)
```

### 해석

- **서버는 5분 내내 생성 중이었다.** 50~57 tok/s, `Running: 1 reqs`, `Waiting: 0 reqs`,
  KV 사용률이 5.2% → 9.4%로 단조 증가했다. 서버 쪽 에러는 0건이다.
- **끊은 것은 클라이언트다.** 300,005 ms는 `_TIMEOUT_S = 300`에 수 ms가 붙은 값이고, 그 값을
  낼 만한 다른 상수가 이 경로에 없다.
- **원인은 요청에 출력 상한이 없다는 것**이다. `git grep max_tokens -- src/`가 0건이므로
  하네스는 그 필드를 보내지 않고 서버는 자기 기본값까지 생성한다. → D42
- **따라 나오지 않는 것**: 모델이 *무엇을* 그렇게 오래 만들고 있었는지는 이 run으로 모른다.
  응답이 클라이언트에 도착하지 못해 본문이 없다. 그것은 실험 2가 답한다.

## 3. 실험 2 — `run_0002`: 상한을 넣자 폭주가 읽히게 됐다

### 목적
실험 1의 5분 침묵이 무엇이었는지 본다. **성능 측정이 아니라 관측 가능성을 얻는 것이 목적이다.**

### 조건
`src/adapters/openai_compat.py`의 payload에 `"max_tokens": 2000` **한 줄** 추가(측정 전용 커밋
`1b43d55`). 나머지 조건과 서버(A)는 실험 1과 같다.

### 결과

| 항목 | 결과 |
| --- | --- |
| 모델 호출 | **78건**(scout 64 · auditor 13 · critic 1) · **`ok=False` 9건** |
| wall_ms (min/중앙/max) | 2,403 / 2,777 / **70,514** (실험 1은 300,005) |
| 게이트 | `gate_eval` 5건(E1 3 · E2 2) · `gate_ambiguous` 3건 |
| 종료 | **critic 첫 호출에서 `SchemaParseError` → run 전체 사망** |
| 산출물 | `report.md` **없음** · `run_manifest.json` **없음** (trace와 state.db만 남았다) |

### 근거 로그

`logs/run_b1single_20260828_001523.log` (전문 보존)

```
  File "/srv/tenants/user/measure-20260827/src/orchestrator/runner.py", line 499, in run_pipeline
    _analyze(ctx, artifacts, state, last)
  File "/srv/tenants/user/measure-20260827/src/orchestrator/runner.py", line 599, in _analyze
    after, decision = critic.run(
...
src.interfaces.model_client.SchemaParseError: critic 역할의 qwen3-coder-30b-a3b이 2회 안에
Decision을 만들지 못했다: 1 validation error for Decision
  Invalid JSON: EOF while parsing an object at line 2722 column 0 [type=json_invalid,
  input_value='{\n  "decision_id": "dec...n\n  \n  \n\n  \n  \n\n', input_type=str]
```

### 해석

- **응답은 2,722줄이고 꼬리가 `\n  \n  \n\n`이다.** 잘린 값이 보여 주는 것은 **JSON 문법이
  허용하는 공백을 계속 만들고 있었다**는 것이다. 배열 원소가 늘어난 폭주가 아니다.
- **상한 2,000 토큰에서도 한 호출이 70.5초까지 갔다.** `schema_retries: 1`이라 재시도가 한
  호출 안에 들어가므로 그 값과 어긋나지 않는다.
- **이 실패가 run 전체를 죽였다.** `runner.py:751`의 `except SchemaParseError`는 후보 루프
  안(`_triage_one`)이고, `_analyze`를 감싸는 `run_pipeline`의 except 목록은
  `BudgetExhausted`·`ModelGatewayError`·`_TooManyDrops` 셋뿐이다. **16분 26초가 통째로
  버려졌고 매니페스트도 리포트도 안 남았다.** → D44
- **따라 나오지 않는 것**: 공백 폭주가 이 모델의 성질인지 이 스키마(`Decision`)·프롬프트와의
  상호작용인지 이 run은 가르지 않는다. 다른 스키마에서 재현해 본 적이 없다.

## 3. 실험 3 — 서버 옵션으로 막았다 (하네스 수정 0)

### 목적
공백 폭주를 **하네스를 고치지 않고** 막을 수 있는가.

### 결과

| 시도 | 결과 |
| --- | --- |
| `--structured-outputs-config '{"disable_any_whitespace": true}'` | **거부.** 기본 `backend`가 `auto`라 검증에서 막힌다 |
| `--structured-outputs-config '{"backend": "xgrammar", "disable_any_whitespace": true}'` | **기동.** 이 서버(B)에서 critic 7건 전부 통과 |

### 근거 로그

거부 메시지는 **세션 stdout(파일 미보존)**이다 — 그 시도는 로그 파일에 사용법 한 줄만 남기고
즉시 죽었고, 같은 경로가 다음 기동으로 덮였다.

```
vllm serve: error: argument --structured-outputs-config: 1 validation error for
StructuredOutputsConfig
  Value error, disable_any_whitespace is only supported for xgrammar and guidance backends.
```

성공한 기동은 서버 B 로그에 남아 있다.

```
structured_outputs_config=StructuredOutputsConfig(backend='xgrammar',
disable_any_whitespace=True, disable_additional_properties=False, reasoning_parser='',
reasoning_parser_plugin='', enable_in_reasoning=False)
```

### 해석

- **하네스 수정 0으로 폭주가 멎었다.** 서버 쪽 문법에서 공백을 빼면 모델이 낼 수 있는 토큰
  집합 자체가 줄어든다.
- **이것은 D42를 닫지 않는다.** 요청당 출력 상한은 여전히 없고, 다른 폭주 유형(예: 배열 원소
  반복)은 이 옵션이 못 막는다. **서버 설정에 의존하는 우회이지 하네스의 계약이 아니다.**
- **`backend`가 `auto`면 이 옵션을 못 쓴다**는 것이 이 실험이 확정한 사실이다.

## 3. 실험 4 — `run_0004`: 첫 완주와 정답 대조

### 목적
`b1_single`이 track_a 샘플 하나를 끝까지 도는가. **경로 성립 판정.**

### 조건
서버 B, 커밋 `1b43d55`(candidate_max 64 + max_tokens 2000), 대상
`.../track_a_evaluation_release_v1/blind/PWN-003/source`.

### 결과

| 항목 | 결과 |
| --- | --- |
| 소요 | `2026-08-27T15:45:31Z` ~ `15:47:14Z` = **1 m 43 s** · `stopped_after: null` |
| 대상 계획 | artifact 5(`Makefile`·`channel_store.c`·`record_format.h`·`record_reader.c`·`router_main.c`) 중 분석대상 4 · **심볼 19 · 실행 19 · 보류 0** |
| 모델 호출 | **33건**(scout 19 · auditor 7 · critic 7) · **실패 0건** · wall_ms 1,781 / 2,386 / 5,391 |
| 토큰 (`model_call` usage 합) | input **43,222** · output **6,227** · `cached_tokens` **0** |
| 도구 | 13건 — 1단계 `index` 1 · 2단계 `semgrep` 4 · `clang-tidy` 3 + **실패 1** · `build` 3 + **실패 1** |
| `state.db` | candidate 19 · capsule 26 · evidence 13 · finding 7 · decision 7 · missing_evidence 1 |
| 게이트 | `gate_eval` 14 (E2 12 · E1 2) + `gate_ambiguous` 2 (E1, `boundary: E1-E2`) |
| 진입한 단계 | 0·1·2·3·4·5·6·7·**9** — **8단계가 없다** |
| `report.md` | "중복 제거: 1건" · hypothesized **6건** |

### 근거 로그

**① 등급은 Finding 단위가 아니다** (`trace.jsonl` 집계)

```
gate_eval (subject, stage, grade):
  find_0001 6 E2 / find_0001 7 E2 / find_0002 6 E2 / find_0002 7 E2
  find_0003 6 E1 / find_0003 7 E1 / find_0004 6 E2 / find_0004 7 E2
  find_0005 6 E2 / find_0005 7 E2 / find_0006 6 E2 / find_0006 7 E2
  find_0007 6 E2 / find_0007 7 E2
stage_start 단계별: [(0,1), (1,1), (2,4), (3,26), (4,19), (5,7), (6,7), (7,7), (9,1)]
```

**② 게이트 술어** (`gate_eval` 원문 1건. 14건 모두 `p3`·`p4`가 false다)

```
{"grade": "E2", "p1": true, "p2": true, "p3": false, "p4": false, "static_ok": true,
 "location_ok": false, "path_ok": true, "graph_ok": true, "unblocked": true,
 "upheld": false, "has_repro_spec": false}
```

**③ 7단계가 멈춘 이유** (`find_0005`의 7단계 `policy_decision`)

```
{"point": "critic_verdict", "policy": "fixed_threshold", "route": "hold",
 "to_state": "hypothesized", "reason": "repro_spec_missing",
 "basis.verdict": "uphold", "basis.grade": "E2", "basis.has_repro_spec": false}
```

**④ 리포트에서 사라진 Finding** (`report.md` 대 `state.db`)

```
report.md : 중복 제거: 1건 / hypothesized (6건)
            find_0001 find_0002 find_0003 find_0004 find_0006 find_0007
state.db  : finding 7건 — 위 6건 + find_0005
            "Buffer overflow in router_store due to insufficient boundary check on ..."
```

### 해석

**① 하네스가 track_a 데이터셋을 그대로 먹는다 — 첫 확인이다.** 별도 어댑터 없이
`--target`에 `blind/PWN-003/source`를 준 것으로 preprocess가 artifact 5개를 잡고 심볼 19개를
후보로 냈다. `deferred: 0`이므로 `candidate_max: 64`가 이 대상에서는 아무것도 자르지 않았다.

**② 등급 이벤트 16건은 Finding 7건과 단위가 다르다.** 게이트는 **6단계(auditor 뒤)와
7단계(critic 뒤)에 각각 한 번씩** 매긴다. `gate_eval` 14건 = Finding 7 × 2이고, 여기에
`gate_ambiguous` 2건(6·7단계 각 1건, 등급 E1, 경계 `E1-E2`)이 더해져 16건이 된다.
**그 2건이 어느 Finding의 것인지는 확인하지 않았다** — 유일한 E1 Finding이 `find_0003`이라
그쪽일 가능성이 높지만 `gate_ambiguous` 이벤트의 `subject_id`를 열어보지 않았다.
**Finding 단위의 분포는 E2 6건 · E1 1건**이다. 등급 이벤트 수를 Finding 수처럼 읽으면 같은
run을 두 배로 세게 된다.

**③ E3·E4가 0인 직접 원인은 "8단계에 안 갔다"이다.** `stage_start`에 8이 없다. 사슬은
이렇다 — auditor가 `repro_spec`을 안 냈다 → `has_repro_spec: false` → 게이트 `p3` false →
7단계 정책이 `repro_spec_missing`으로 `hold` → **8단계 재현 도구가 한 번도 안 돌았다.**

**이것은 8/26의 D38과 원인이 다르다.** D38은 *Fake* auditor가 자리표시자 `repro_spec`을
만들어 8단계가 돌고 asan이 전부 실패하는 경우다. 이 run의 auditor는 **실서빙**이고, 결과는
"가짜 명세로 도구가 돈다"가 아니라 **"명세가 아예 없어 도구가 안 돈다"**이다. 즉 **실서빙
auditor에서도 E3·E4는 여전히 안 나오지만 그 이유가 바뀐다.** 실서빙 auditor가 왜
`repro_spec`을 안 냈는지는 **이 run이 답하지 않는다** — 프롬프트와 응답을 열어보지 않았다.

**④ `location_ok`가 14건 전부 false다.** D23의 다섯 번째 확인이고 판정은 그대로다.
`state.db`의 finding 7건 모두 `location`이 비어 있다.

**⑤ `find_0005`가 리포트에서 사라졌다 — D35의 두 번째 사례.**
`find_0005`("router_store의 경계 검사 부족")는 6·7단계 모두 **E2 · `upheld: true`**였는데
9단계 `dedupe()`가 지웠고 `report.md`에는 "중복 제거: 1건"만 남는다. **무엇이 무엇과
중복으로 판정됐는지는 리포트에도 trace에도 없다.** 8/26이 E3 6건에 대해 보고한 동작이
**E2에서도 일어난다**는 것이 이번에 더해진 사실이다.

**⑥ 성능 결론은 없다.** 1 m 43 s · 33 콜은 표본 1이고 대상이 5파일짜리 샘플이다.
zlib(심볼 542)과 나란히 놓을 수 없다.

**⑦ 실행된 대상의 심볼이 기록에 없다 — 건너뛴 대상만 남는다.** `target_plan`은
`symbol_targets: 19`처럼 **개수만** 남기고, 심볼 이름이 남는 것은 `target_skipped`
이벤트뿐이다(`runner.py:806`의 `symbol=target.symbol or ""`). `run_0004`는 `deferred: 0`이라
그 이벤트가 한 건도 없다. 후보 쪽도 못 메운다 — `Candidate`는
`scout._ask_model(..., schema=Candidate)`가 **모델에게 받아 그대로 저장**하는 객체라
`location`이 모델이 쓴 값이고, 이 run에서는 `symbol`이 전부 `null`이다(`cand_0001` =
`channel_store.c` 7~18줄). 인덱서는 이름을 알고 있는데(`index_graph.py:189`의
`Location(file, symbol, line_start, line_end)`) **그 이름이 실행 기록으로 건너오지 않는다.**
**이것이 아래 정답 대조의 마지막 벽이다** — 데이터가 아니라 기록 설계의 문제다. → D45

### 정답 대조 — 불합격

정답지: `evaluator/PWN-003/ground_truth.yaml` (`finding_count: 1`, `package_status: REVIEW_READY`)

| 항목 | 정답지 | `run_0004` |
| --- | --- | --- |
| CWE | **CWE-843** (secondary CWE-20·787·125, `ordering: CAUSAL_ROOT_THEN_SUBOBJECT_EFFECTS`) | **CWE-121 ×4** · CWE-122 · CWE-120 · CWE-415 (`state.db` finding 7건 기준. 리포트에 실린 6건 기준으로는 121 ×3) |
| 근본 원인 | `router_define` — 검증(`kind > KIND_BYTES`) **앞에서** `kind`를 커밋한다 | **없다** |
| 인과 범위 (4곳) | `router_define` · `router_store` · `router_emit` · `record_format.h`의 PacketLease/ForwardPlan 레이아웃 | `router_store`(`find_0005` — **리포트에서 삭제됨**) · `router_emit`(`find_0006`) · `router_destroy`(`find_0003`, 범위 밖) |
| 판정 | `true_positive_requirements` 1항: "Identify the released root cause or equivalent broken invariant" | **불합격** |

대상 무결성은 확인했다 — 우리가 돌린 `blind/PWN-003/source/channel_store.c`의 sha256
`1dbbc286459d6d4dbfa893238b6c58b20d55da81f20318ae2842c57a240ceec8`이 정답지의 `file_sha256`과
같다. **정답지가 가리키는 그 파일을 돌린 것이 맞다.**

**해석**: 하네스는 **sink 두 곳(`router_store`·`router_emit`)에 닿았지만 근본 원인에 닿지
못했다.** 정답지가 요구하는 것은 "type tag와 그 표현이 원자적으로 검증·커밋되어야 한다"는
불변식이고, 하네스가 낸 것은 전부 memcpy 크기 계열(121/122/120)과 UAF(415)다. **CWE-843을 못
낸 것과 `router_define`을 못 짚은 것은 같은 실패다** — 원인을 안 봤으니 타입 혼동으로 분류할
근거도 없다.

**따라 나오지 않는 것**: 이 실패가 (a) 모델의 추론 한계인지 (b) 후보 단위가 심볼이라
`router_define`과 `router_store`를 잇는 캡슐이 없어서인지 **이 run은 가르지 않는다.**
그리고 **가르려고 해도 지금 기록으로는 못 간다** — `router_define`이 후보 19건에
들어 있었는지를 확인하려 했으나 `Candidate.location.symbol`이 전부 `null`이고 하네스가
계획한 심볼 목록이 어디에도 안 남는다(위 해석 ⑦, D45).

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 모델 호출 1건이 300,005 ms를 먹고 run이 중단 | 하네스가 `max_tokens`를 안 보내 서버가 자기 기본값까지 생성한다. 클라이언트 `_TIMEOUT_S = 300`이 끊었다 | 측정 전용 커밋으로 `max_tokens: 2000` 삽입 — **폭주를 없앤 게 아니라 읽을 수 있게 바꿨다.** 정본은 D42 |
| critic 응답이 2,722줄 공백으로 끝나 파싱 실패 | 문법이 허용하는 공백을 계속 생성. `backend='auto'`는 공백을 막지 않는다 | `--structured-outputs-config '{"backend":"xgrammar","disable_any_whitespace":true}'`. **하네스 수정 0** |
| 그 파싱 실패가 run 전체를 죽여 16 m 26 s가 버려짐 | `SchemaParseError` 처리가 후보 루프 안에만 있다(`runner.py:751`). `run_pipeline`의 except에 없다 | **미해결.** D44 |
| `cli estimate`가 `ValidationError`로 죽는다 | 캡슐은 실 토크나이저(`using_counter`) 안에서 만들어 저장되고 `store.query("capsule")`는 그 밖이라 기본 근사로 재검증한다(`callees` 섹션 `tokens=30` vs 재계산 18) | **미해결.** D43. **run 경로는 안전하다** — `store.query("capsule")`는 `cli.py:426` 한 곳뿐이다 |
| `run_0003`이 `preprocess` 직후 죽었다 | **미기재** — 그 실행의 stdout을 파일로 받지 않았다 | 재현 시 `tee`로 받는다 |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| cgroup `memory.current` 12.84 GiB의 **구성** | 어느 프로세스 몫인지 안 갈랐다. 미확정 #1의 판정(미계상)에는 필요 없지만 34 GiB 약속선을 실제로 감시하려면 필요하다 | 하위 scope별 `memory.current`를 훑는다 |
| 추론 **중**의 cgroup 값 | idle 상태에서만 읽었다 | 다음 run이 도는 동안 같은 세 파일을 다시 읽는다 |
| `nvidia-smi` 32.2 GiB와 vLLM 회계 35.66 GiB의 차 | 두 값을 나란히 보기만 했고 무엇이 가르는지 안 따졌다 | 같은 시점에 두 값을 떠서 구성 요소별로 대조한다 |
| `run_0003`의 실패 사유 | stdout을 파일로 안 받았고 trace에 이벤트가 2건뿐이다 | 같은 명령을 `tee`로 다시 돌린다 |
| `run_0002`·`run_0003`의 `repo_commit` | 매니페스트를 남기기 전에 죽었다. 시간순으로 `1b43d55` 이후이지만 **그것은 추정이지 기록이 아니다** | D44가 닫히면 죽은 run도 매니페스트를 남긴다 |
| 장부 예산 계상(`budget` 이벤트) | 이벤트가 누적값인지 증분인지 확인하지 않았다. **단순 합산이 `gpu_seconds` 1,758을 내는데 run은 103초다** — 합산 방식이 틀렸거나 필드 뜻이 다르다 | `budget` 이벤트의 계약을 코드에서 확인하고 합산 규칙을 정한다 |
| **`router_define`이 후보 19건에 있었는가** (= 하네스가 실행한 심볼이 무엇인가) | **대조를 시도했고 데이터가 없어서 못 했다.** candidate 19건의 `location.symbol`이 전부 **`null`**이고 `payload like '%router_define%'`도 세 gold 함수 전부 **0건**이다 — 후보에 없었다는 뜻이 아니라 **이름이 저장되지 않았다**는 뜻이다(해석 ⑦) | D45가 닫히거나, `run_20260827_0004/prompts/`의 scout 19건을 사람이 읽는다 |
| 실서빙 auditor가 `repro_spec`을 왜 안 냈는가 | `run_20260827_0004/prompts/`에 전문이 남아 있으나 열지 않았다 | auditor 7건의 프롬프트·응답을 읽는다 |
| 8/25 30B의 KV 포함 총량 | 그 보고서에 `Current kv cache memory in use` 값이 없다 | 8/25 서버 로그(`logs/serve_30b_20260825_142256.log`, 보존됨)에서 읽어 그 보고서를 서식 정리(A)로 채운다 |

## 6. 반증 조건

**① 완주가 실패했다면 무엇이 틀린 것이었나.** `run_0004`가 죽었다면 실험 3의 서버 옵션이
공백 폭주를 못 막는다는 뜻이었다. 완주했으므로 그 옵션은 **이 스키마·이 모델 조합에서는**
작동한다. 표본은 critic 7콜이다.

**② E3·E4 0건은 성능이 아니다.** 8단계에 진입하지 않았으므로 `p3`(재현)·`p4`(PoC)를 판정할
입력 자체가 없었다. **이 run에서 E3·E4가 0인 것은 판정 결과가 아니라 판정 미실시다.**
D38이 말한 "Fake auditor라 도달 불가"와 구별해야 한다 — 여기서는 auditor가 실서빙인데도
`repro_spec`이 안 나왔다.

**③ `B1prime@pipeline`은 팀 표의 B1′가 아니다.** `--condition`의 정규식은
`src/schemas/run.py:24`의 `^(B1|B1prime|B3|B3prime)@(2x2|pipeline)$`이고 스모크·시험용 라벨을
허용하지 않는다. 그래서 **데이터셋 첫 투입이 정식 조건 라벨로 기록됐다.** 팀 표의 B1′는
Qwen3-8B ×3이고 **이 run은 30B ×3**이다. **이 기록을 조건 비교표에 그대로 넣으면 안 된다.**

**④ 샘플링이 스펙 고정값이 아니다.** `top_k: 20` · `repetition_penalty: 1.05`가 모델 파일에서
적용됐고 기록에 없다(§2). **이 run의 출력은 "요청이 지정한 샘플링"의 산물이 아니다.** 모델을
바꾸면 이 두 값도 함께 바뀌므로, 그 비교는 `--generation-config vllm`을 주기 전까지 성립하지
않는다.

**⑤ 정답 대조가 합격이었다면** 하네스가 근본 원인을 짚었다는 뜻이었다. 불합격이므로 이 run이
확정하는 것은 **"경로가 돈다"뿐이고 "무엇을 찾는다"는 아직 아니다.**

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 높음 | critic 파싱 실패가 run을 죽이는 것 | D44 신설 |
| 높음 | 요청당 출력 상한 | D42 신설. 측정 커밋의 `max_tokens: 2000`은 **머지 대상이 아니다** |
| 높음 | `--generation-config vllm`을 기본 기동에 넣을지 | D29 갱신. 모델 비교 전에 정해야 한다 |
| 높음 | 실행 대상의 심볼이 기록에 안 남는다 | **D45 신설.** 정답 대조를 막은 벽이다 |
| 중간 | `cli estimate`가 못 돈다 | D43 신설 |
| 중간 | 실서빙 auditor의 `repro_spec` 부재 | 프롬프트·응답 열어보기(§5) |
| 중간 | `dedupe()`가 E2도 지운다 | D35에 두 번째 사례 추가 |
| 해소 | README 미확정 #1(통합메모리의 cgroup 계상) | **답이 나왔다 — 미계상**(§2). [README.md](../README.md)의 그 항목에 해소 블록을 달았다 |
| 낮음 | `estimate`에 `--device`가 없다 | `cli.py:698`이 `parents=[common]`이고 `run`만 `[common, device]`다. 값이 기록에 안 남는 명령이라 영향은 작다 |
| 낮음 | `counter_for_source(None)`이 `AttributeError` | `tokenizer.py`는 "빈 값이면 `None`"이라 적었지만 `None` 입력에는 터진다. **프로덕션 경로는 `factory.py:298`의 `counter_for_source(source) if source else None`이 막는다** — 규약과 구현의 어긋남이고 파이프라인 결함은 아니다 |

## 8. 정리 상태

- **서빙 서버: 서버 B가 아직 떠 있다.** 작성 시점(2026-08-28) `pgrep -af "vllm serve"`가
  pid 78448을 내고 `nvidia-smi`가 32,940 MiB를 잡고 있다(포트 8002). **공유 계정이므로 끄지
  않으면 다음 사람의 서빙을 밀어낸다.** 서버 A는 00:38에 `pkill`로 종료됐다.
- **임시 설정·프로필**: 저장소 `config/`는 안 건드렸다. 측정 전용 변경 둘은 워크트리
  `/srv/tenants/user/measure-20260827`의 커밋 `3f93e1c`(candidate_max 64)·
  `1b43d55`(max_tokens 2000)에 있고 **둘 다 머지 대상이 아니다.** 그 워크트리는 clean이다.
- **저장소 변경**: 셋뿐이다 — 이 보고서 · [open-questions.md](../../open-questions.md)(D42~D45 신설, D29·D35 갱신) · [README.md](../README.md)(색인 1행, 미확정 #1 해소 블록).

## 9. 부록 — 재현 절차

```bash
# --- 워크트리 (origin/main 3851c37 기준) ---
git worktree add -b measure/b1single-20260827 \
  /srv/tenants/user/measure-20260827 origin/main
cd /srv/tenants/user/measure-20260827
source /srv/tenants/user/venv-probe/bin/activate
export CPATH="$HOME/pyhdr/usr/include/python3.12:$HOME/pyhdr/usr/include:${CPATH:-}"

# 측정 전용 커밋 1 — 머지 대상 아님
sed -i 's/^  candidate_max: 256$/  candidate_max: 64/' config/budgets.yaml
git commit -am "measure: b1_single 첫 run용 candidate_max 64 — 머지 대상 아님"   # 3f93e1c

# --- 서버 A (structured outputs 기본값) ---
SNAP=/srv/tenants/user/.cache/huggingface/hub/models--NVFP4--Qwen3-Coder-30B-A3B-Instruct-FP4/snapshots/3b554c28e968128990ef4ac2d25ca757bf4ce048
SERVE_LOG=/srv/tenants/user/artifacts/logs/serve_30b_$(date +%Y%m%d_%H%M%S).log
setsid nohup env \
  VLLM_USE_DEEP_GEMM=0 \
  VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo \
  vllm serve "$SNAP" \
    --served-model-name qwen3-coder-30b-a3b \
    --host 127.0.0.1 --port 8002 \
    --gpu-memory-utilization 0.28 \
    --max-model-len 32768 \
    --enable-prefix-caching \
  > "$SERVE_LOG" 2>&1 &
until curl -s http://127.0.0.1:8002/v1/models >/dev/null 2>&1; do sleep 15; printf .; done

# --- run_0001 (zlib) ---
python3 -m src.cli doctor --profile b1_single --device atom --tool-runner docker \
  --artifacts-root /srv/tenants/user/artifacts
time python3 -m src.cli run \
  --profile b1_single --device atom --tool-runner docker \
  --condition "B1prime@pipeline" \
  --artifacts-root /srv/tenants/user/artifacts \
  --target /srv/tenants/user/targets/zlib

# 서버를 끄기 전에 챙긴다
RUN_DIR=/srv/tenants/user/artifacts/run_20260827_0001
grep -E "Prefix cache hit rate" "$SERVE_LOG" | tail -5 | tee "$RUN_DIR/serve_prefix_cache.txt"
cp "$SERVE_LOG" "$RUN_DIR/"

# 측정 전용 커밋 2 — 머지 대상 아님
# src/adapters/openai_compat.py 의 payload에 "max_tokens": 2000 한 줄 추가
git commit -am "measure: max_tokens 2000을 요청에 싣는다 — 하네스가 안 보내던 값 (머지 대상 아님)"  # 1b43d55

# --- run_0002 (zlib · 위와 같은 run 명령) → critic SchemaParseError로 사망 ---

# --- 서버 B (공백 금지) ---
pkill -f "vllm serve"; sleep 5
# 아래는 거부된다 — backend가 auto면 disable_any_whitespace를 못 쓴다
#   --structured-outputs-config '{"disable_any_whitespace": true}'
setsid nohup env \
  VLLM_USE_DEEP_GEMM=0 \
  VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo \
  vllm serve "$SNAP" \
    --served-model-name qwen3-coder-30b-a3b \
    --host 127.0.0.1 --port 8002 \
    --gpu-memory-utilization 0.28 \
    --max-model-len 32768 \
    --enable-prefix-caching \
    --structured-outputs-config '{"backend": "xgrammar", "disable_any_whitespace": true}' \
  > /srv/tenants/user/artifacts/logs/serve_30b_nows_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# --- run_0004 (track_a PWN-003) ---
time python3 -m src.cli run \
  --profile b1_single --device atom --tool-runner docker \
  --condition "B1prime@pipeline" \
  --artifacts-root /srv/tenants/user/artifacts \
  --target /srv/tenants/user/experiments/2x2/dataset/track_a_evaluation_release_v1/blind/PWN-003/source
```

> **`run_0004`의 명령 원문은 세션 stdout(파일 미보존)에서 옮겼다.** `--target` 경로는
> `run_manifest.json`에 없고, `state.db`의 artifact 5건이 그 디렉터리의 파일 이름과 일치하는
> 것으로 확인했다. 나머지 인자는 매니페스트의 `condition`·`device`·`tool_runner`와 일치한다.
