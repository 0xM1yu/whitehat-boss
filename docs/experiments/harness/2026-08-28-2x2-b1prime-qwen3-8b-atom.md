# 2×2 B1′ 첫 완주 — `b1_single_qwen3_8b` · ATOM

> **PR #36(`feat/registry-foundation-granite`) 검증 run이다.** 모든 수치는 2026-08-28
> ATOM에서 산출물을 직접 읽어 확정했다(서버 미기동, 읽기 전용).
>
> **이 run은 2×2 매트릭스 run이 아니다.** `experiments/2x2/config/run_matrix.yaml`이 정한
> `primary_seed: 20260901`·`artifacts_root: experiments/2x2/artifacts`를 둘 다 쓰지 않았다
> (§3). PR #36의 프로필이 track_a 대상에서 도는지 보는 **개통 확인**이고, 조건 비교의
> 입력이 아니다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실행 구간 | `2026-08-27T18:33:44Z` → `18:43:47Z` = **10분 03초** (KST 8/28 03:33→03:43) |
| 실제 서빙 모델 | `hf://Qwen/Qwen3-8B@b968826d9c46dd6066d109eabc6255188de91218` — 별칭 `qwen3-8b-bf16`. **원본 bf16이지 `qwen3-8b`(NVFP4)가 아니다** |
| `repo_commit` | `eb0e30c8704caaa29236b48b28b81c9b9e2f7ab3` · `repo_dirty: true` |
| `run_id` | `run_20260827_0005` — **UTC 기준이다.** KST 8/28 03:33 = UTC 8/27 18:33 |
| `source_hash` | `sha256:d347359a3f00186daa65d1a0d77c7bd1dbf4f25712383e11eba612cbc4eb05d6` |
| run 산출물 | `/srv/tenants/user/artifacts/run_20260827_0005` — **보존됨** |
| `tool_runner` | 선언 `docker` / 실제 `docker` |
| `--device` | `atom` |
| `tool_images` | `harness-tools:v1` = `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17` (docker 이미지 config sha256) |
| `--gpu-memory-utilization` | **0.25** (8B 단독). 분모는 물리 통합메모리 119.7 GiB |
| 동시에 떠 있던 다른 서버 | **없음** |
| `seed` | `20260828` |
| `sampling` | `temperature 0.2` · `top_p 0.95` |
| `kb_snapshot` | `kb-none` (자리표시자) |
| 서빙 서버 stdout | `/srv/tenants/user/artifacts/logs/20260828/serve_8b_032810.log` (성공분, 42,457 B) · `/srv/tenants/user/artifacts/logs/serve_qwen3-8b_20260828_024813.log` (실패분, 21,644 B) |
| run stdout | `/srv/tenants/user/artifacts/logs/20260828/run_b1p_033343.log` (687 B) |

## 1. 요약

**경로 성립 판정이다. 성능 주장도 인과 주장도 하지 않는다** — 표본 1건이고, 어제 30B run과
**모델 말고도 다섯 항목이 함께 바뀌었다**(§5).

| 항목 | 결과 |
| --- | --- |
| PR #36의 registry 3종 해석 | **성립.** 세 모델이 각 포트 클라이언트로 조립된다 |
| PR #36의 프로필 넷 | **성립. 4/4 ERROR 0건** (§3) |
| PR #36의 `output_max` 배선 | **성립.** `RunRecord.thresholds`에 `output_max: 8192.0`이 실렸다. **폭주 차단 효과는 이 run으로 판정되지 않는다**(§5) |
| B1′ 조건 완주 | **완주.** 후보 19건 / Finding 13건 / 10분 03초 |
| 이 run의 비교표 등재 | **제외.** `model_checksums` 전부 0 · `repo_dirty: true`. **이 판정은 바뀌지 않는다** — 뒤에 checksum이 채워져도 이 run의 기록은 0이다(§10) |
| PWN-003 정답 대조 | **불합격** (§6) |

## 2. 실험 환경

- vLLM 0.27.1 · Qwen3-8B bf16 (체크포인트 15.26 GiB, safetensors 5샤드)
- 기동 인자 원문은 §11 부록.

## 3. 실험 0 — 프로필 넷이 전부 해석된다 (서버 없이)

### 목적

PR #36이 추가한 프로필 넷의 참조가 정합한가. **GPU도 서버도 필요 없다** — 이 세션의 첫
`doctor`는 8001이 죽어 있을 때(`8001 -> 000`) 실행됐고 같은 `[OK]`를 냈다. `doctor`는
config를 파싱해 어댑터를 조립할 뿐 엔드포인트를 두드리지 않는다.

### 결과

| 프로필 | scout / auditor / critic 엔드포인트 | ERROR |
| --- | --- | --- |
| `b1_single_qwen3_8b` | 8001 / 8001 / 8001 | 0 |
| `b1_single_foundation_sec` | 8006 / 8006 / 8006 | 0 |
| `b1_single_granite` | 8003 / 8003 / 8003 | 0 |
| `b3prime_balanced` | **8006 / 8003 / 8001** | 0 |

넷 다 WARN은 같은 둘이다 — `benchmark 미측정` · `model checksum 자리표시자`.

> **checksum WARN은 이후 해소됐다.** 이 표는 `eb0e30c` 시점의 출력이다. PR #36이
> `ee1d93b`로 세 모델의 checksum을 실측해 넣은 뒤 같은 명령이
> `[OK   ] 실서빙 역할의 model checksum이 모두 채워져 있다`를 낸다. `benchmark 미측정`
> WARN은 그대로다 — `measured_at`이 셋 다 아직 `null`이다.

### 근거 로그

```
--- b3prime_balanced ---
[OK   ] config 3종 파싱 성공 (프로필 'b3prime_balanced')
[OK   ] scout        → OpenAICompatModelClient   (http://127.0.0.1:8006/v1)
[OK   ] auditor      → OpenAICompatModelClient   (http://127.0.0.1:8003/v1)
[OK   ] critic       → OpenAICompatModelClient   (http://127.0.0.1:8001/v1)
[OK   ] 도구 런타임 docker 확인: /usr/bin/docker
[WARN ] model checksum이 자리표시자(전부 0)다: auditor(granite-3.3-8b, ...), critic(qwen3-8b-bf16, source=hf://Qwen/Qwen3-8B@b968826d9c46dd6066d109eabc6255188de91218), scout(foundation-sec-8b, ...)
```

### 해석

**`b3prime_balanced`의 세 포트가 갈린다.** scout=`foundation-sec-8b` · auditor=`granite-3.3-8b`
· critic=`qwen3-8b-bf16`으로 제작사가 셋 다 다르다. `run_matrix.yaml` §9가 요구하는
`b3prime_profile: b3prime_balanced`와 `b1prime_profiles` 3종이 **PR #36으로 채워진다.**

**따라 나오지 않는 것: 이 프로필이 돈다는 것.** `doctor`는 포트의 생사를 보지 않고, 이
프로필은 세 서버를 동시에 띄워야 하는 유일한 구성이다. 8B 하나가 KV cache를 13.82 GiB
잡았으므로(§5) 셋이 함께 서는지는 별도 판정이다.

## 4. 실험 1 — 동거 상태의 `0.15`가 KV cache를 못 잡는다

### 조건

8002에 30B(`--gpu-memory-utilization 0.28`, 32,940 MiB 점유)가 살아 있는 상태에서
8001에 8B를 `0.15`로 기동.

### 결과

가중치는 다 읽고 **KV cache 할당 단계에서 죽는다.** 기동 실패까지 약 2분.

### 근거 로그

```
(EngineCore pid=205011) INFO 08-28 02:48:25 [weight_utils.py:867] Checkpoint size: 15.26 GiB. Available RAM: 60.67 GiB.
(EngineCore pid=205011) ERROR 08-28 02:50:23 [core.py:1349] ValueError: No available memory for the cache blocks. Try increasing `gpu_memory_utilization` when initializing the engine
(APIServer pid=204868) RuntimeError: Engine core initialization failed. See root cause above.
```

### 해석

**"8B에는 0.25가 필요하다"는 따라 나오지 않는다.** 실패 뒤 `pkill -f "vllm serve"`가
패턴이 넓어 **30B까지 함께 죽였고**(`8001/8002 free`), 성공한 0.25는 8B **단독**이다.
비율과 동거 여부가 같이 움직였으므로 이 두 점은 한 축의 두 값이 아니다.

**아직 모르는 것 둘**: ① 단독 0.15가 뜨는가 ② 동거 0.25가 뜨는가. `b3prime_balanced`는
세 모델을 동시에 띄워야 하므로 ②가 그 조건의 전제다.

## 5. 실험 2 — 단독 `0.25`로 기동, B1′ 완주

### 조건

8B 단독 · `0.25` · `--seed 20260828` · `--condition B1prime@2x2` · `--device atom` ·
`--artifacts-root /srv/tenants/user/artifacts`.

### 결과

| 항목 | 값 |
| --- | --- |
| KV cache | 13.82 GiB / **100,592 토큰** |
| 최대 동시성 (32,768 tok/req) | **3.07x** |
| 서버 기동 소요 | 약 2분 (03:28:17 → 03:30:16) |
| run 소요 | **10분 03초** |
| 완주 여부 | **완주** |
| 후보 / Finding | **19 / 13** (hypothesized 3 · queued 9 · 중복 제거 1) |
| `critic_top_n` 절단 | Finding 1건 (상한 8) |
| Prefix cache hit rate | **39.6%** |

### 근거 로그

```
(EngineCore pid=243800) INFO 08-28 03:30:16 [gpu_worker.py:563] Available KV cache memory: 13.82 GiB
(EngineCore pid=243800) INFO 08-28 03:30:16 [kv_cache_utils.py:2235] GPU KV cache size: 100,592 tokens
(EngineCore pid=243800) INFO 08-28 03:30:16 [kv_cache_utils.py:2236] Maximum concurrency for 32,768 tokens per request: 3.07x
(APIServer pid=243589) INFO 08-28 03:43:49 [loggers.py:310] Engine 000: ... Prefix cache hit rate: 39.6%
```

`run_manifest.json` 발췌:

```json
{"condition": "B1prime@2x2", "repo_dirty": true, "seed": 20260828,
 "thresholds": {"output_max": 8192.0, "candidate_max": 256.0, "critic_top_n": 8.0},
 "started_at": "2026-08-27T18:33:44Z", "finished_at": "2026-08-27T18:43:47Z"}
```

### 해석 — `output_max`의 인과는 이 두 run으로 성립하지 않는다

`run_20260827_0004`(30B)의 `thresholds`에는 **`output_max` 키 자체가 없다.** 그런데 그 run은
**1분 43초에 완주했다**(`15:45:31Z` → `15:47:14Z`). 8/27의 출력 폭주는 그 시점에 이미
서버 옵션(`--structured-outputs-config` `disable_any_whitespace`)으로 해소된 상태였다.

→ **따라서 "PR #36의 `output_max`가 폭주를 막았다"는 이 두 run이 지시하지 않는다.**
남는 사실은 배선뿐이다: `output_max: 8192`가 `RunRecord.thresholds`에 실제로 기록됐고,
`src/adapters/factory.py`와 `src/bench_report.py`가 같은 값을 읽는다. 차단 효과를 재려면
`output_max` **없이 서버 옵션도 없는** 조건과의 대조가 있어야 하고, 그 run은 이미
`run_0001`~`0003`으로 존재하지만 모델이 30B라 8B에 대한 판정은 아니다.

### 해석 — 두 run은 모델 비교로 쓸 수 없다

| 항목 | `run_0004` (30B) | `run_0005` (8B) |
| --- | --- | --- |
| `condition` | `B1prime@pipeline` | `B1prime@2x2` |
| `repo_commit` | `1b43d55a…` | `eb0e30c8…` |
| `repo_dirty` | false | **true** |
| `thresholds.output_max` | **없음** | 8192.0 |
| `thresholds.candidate_max` | **64.0** | **256.0** |
| `seed` | 20260901 | **20260828** |
| `model_checksums` | `sha256:69c4c69e…` (실측) | 전부 0 |
| 소요 | 1분 43초 | 10분 03초 |
| `source_hash` | `sha256:d347359a…` | **동일** |
| `tool_images` | `sha256:b5d7048b…` | **동일** |

대상과 도구는 같지만 **모델 외 다섯 항목이 함께 움직였다.** evaluation.md §2.3 규칙 2
("조건 간 차이는 프로필과 단계 활성화만")를 만족하지 않는다.

### 해석 — 후보 19건은 파일 분포까지 같다

| 파일 | `run_0004` | `run_0005` |
| --- | --- | --- |
| `channel_store.c` | 8 | 8 |
| `record_reader.c` | 3 | 3 |
| `router_main.c` | 8 | 8 |
| 합계 | 19 | 19 |

**갈리는 것은 심볼뿐이다.** `run_0004`는 3건에 이름이 붙었고(`scalar_from_record` ·
`load_u16` · `main`) **`run_0005`는 19건 전부 `null`이다.** 후보 생성이 모델과 무관하게
정적 신호로 정해진다는 가설과 어긋나지 않지만, **표본이 2건이라 확정하지 않는다.**
심볼 누락은 이미 열려 있는 항목이다(D45).

## 6. 정답 대조 — 불합격

`report.md`의 Finding 13건에 **`router_define`이 없고 CWE-843도 없다.** 어제 30B 보고서가
기록한 PWN-003의 근본 원인이 `router_define`·CWE-843이므로 **8B도 같은 지점에서 실패한다.**
(정답 파일을 직접 읽지 않았다 — 근거는 어제 보고서의 대조 기록이다.)

**다만 30B가 못 짚은 심볼에는 닿았다.** `run_0004`의 후보 19건에는 `router_*`가 하나도
없었는데(첫 세션의 `grep` 결과: `router_define 0 / router_store 0 / router_emit 0`),
`run_0005`의 Finding에는 `router_destroy`(CWE-415) · `router_emit`(CWE-120) ·
`router_forward`(CWE-459) · `router_consume`(CWE-823)이 있다.

**Finding 13 > 7이 더 낫다는 뜻은 아니다.** 정답이 하나인데 13건을 올린 것은 오탐 12건이
따라온다는 뜻이기도 하다. 오탐률은 benign 표본으로 재야 하고(evaluation.md §3) 이 run에는
없다.

## 7. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 8B가 KV cache를 못 잡는다 | 30B가 0.28 점유 중 · 8B `0.15` | 30B 종료 후 단독 `0.25` (단, §4 해석 참조) |
| `pkill -f "vllm serve"`가 30B까지 죽였다 | 패턴이 두 프로세스에 모두 걸린다 | 포트나 PID로 지목한다 |
| run이 `repo_dirty`로 비교표 제외 | 서빙·run 로그를 워크트리 안에 썼다. `.gitignore`에 `*.log`가 없다 — **`origin/main`에도 없다** | **해결됨.** 로그를 `artifacts/logs/20260828/`로 옮겼고 워크트리는 `git status --porcelain` 빈 출력이다. 다만 `.gitignore`는 그대로라 다음 run에서 재발한다 |

## 8. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| `model_checksums` | **이 run 시점에 PR #36이 세 모델을 0으로 등록한 상태였다.** 이 칸은 영구히 미측정이다 — 지나간 run의 기록은 뒤에 못 채운다 | **앞으로의 run은 채워진다** — `ee1d93b`가 세 값을 실측해 넣었다(§10) |
| `benchmark` (`tok_s`·`mem_gb_ctx8k`) | 세 모델 다 `measured_at: null` | `bench` 실행 |
| 단독 `0.15` · 동거 `0.25` | 시도하지 않았다 | 각 1회 기동 |
| `b3prime_balanced` 실행 | 세 서버를 동시에 안 띄웠다 | 8006·8003·8001 동시 기동 |
| 오탐률 | benign 표본을 안 돌렸다 | `blind/BENIGN-*` 투입 |
| `output_max`의 차단 효과 | 대조군이 없다 | 8B에서 상한 없이 1회 |

## 9. 반증 조건

- **완주가 실패했다면** — 8B가 30B용 프롬프트를 스키마대로 못 채운다는 뜻이었다.
  완주했으므로 **아니다.**
- **doctor가 ERROR를 냈다면** — registry 3종이나 프로필 넷의 참조가 깨졌다는 뜻이었다.
  4/4 ERROR 0건이므로 **PR #36의 config 변경은 정합하다.**
- **이 run이 증명하지 않는 것** — 8B가 30B보다 낫다는 것, `output_max`가 폭주를 막는다는 것,
  B1′가 B1보다 낫다는 것.

## 10. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| — | **PR #36: 새 모델 3종 checksum이 0** — 이 PR이 개통한 프로필로 돈 run이 비교표 밖에 쌓인다고 적었던 항목 | **닫힘(`ee1d93b`).** `qwen3-8b-bf16` `sha256:2dcba233…` · `foundation-sec-8b` `sha256:2f37c76b…` · `granite-3.3-8b` `sha256:d96a17ee…`가 실측으로 들어갔고 `config/checksums/`에 세 파일이 생겼다. ATOM `doctor --profile b3prime_balanced`가 `[OK] 실서빙 역할의 model checksum이 모두 채워져 있다`를 낸다. **이 run(`run_20260827_0005`)에는 소급되지 않는다** — 기록은 0인 채로 남고 비교표 제외도 그대로다 |
| — | **PR #36 브랜치가 `origin/main`보다 뒤처져 있다** | **닫힘(`ee8919f`).** `git merge-base --is-ancestor origin/main <PR36 HEAD>` 통과 |
| — | **`docs/evaluation.md` §2.3 규칙 2가 PR #36과 어긋난다** | **닫힘(`b1505a3`, PR #36).** 목록을 여섯으로 고치고 근거를 "선언한 런타임이 그 장비에 있다"로 바꿨다. 깨진 절 참조 `§7 위험 #4` → `§5 #4` 3곳도 함께 고쳤다 |
| 높음 | **세 모델의 `benchmark`가 미측정이다**(`measured_at: null`) — checksum이 채워져도 `doctor`의 `benchmark 미측정` WARN은 남고, `production: true`로 올릴 수 없다 | `bench` 미실행 |
| 중간 | `.gitignore`에 `*.log` — `origin/main`에도 없다 | 미착수 |
| 중간 | **`run_matrix.yaml` §9의 `b3prime_large_profile: TODO_local_large`** — B3′-large 프로필 이름 미확정 | 미착수 |
| 중간 | **`served_name` 축 대조** — `run_matrix.yaml`은 `qwen3-8b`의 `served_name`을 `qwen3-8b`로 적는데 PR #36 registry는 `qwen3-8b-bf16`으로 서빙한다. 같은 가중치를 가리키지만 이름이 다르다 | 미확인 |
| 낮음 | 매트릭스 seed(20260901)·`artifacts_root`로 재실행 | 이 run은 매트릭스 밖이다 |

## 11. 정리 상태

- 서빙 서버: **전부 종료됨** (`pgrep -af "vllm serve"` 매치 없음, 2026-08-28 확인)
- 임시 설정·프로필: 원복 불필요 (오버레이 없음)
- 저장소 변경: **없음.** 워크트리 `/srv/tenants/user/exp-2x2-20260828`은
  `git status --porcelain` 빈 출력이고, 로그 2건은 `artifacts/logs/20260828/`로 이동됐다

## 12. 부록 — 재현 절차

```bash
# --- 워크트리 (PR #36 eb0e30c) ---
git worktree add /srv/tenants/user/exp-2x2-20260828 origin/feat/registry-foundation-granite
cd /srv/tenants/user/exp-2x2-20260828
source /srv/tenants/user/venv-probe/bin/activate
export CPATH="$HOME/pyhdr/usr/include/python3.12:$HOME/pyhdr/usr/include:${CPATH:-}"

# --- 서버 (8B 단독, 0.25) ---
SNAP8B=/srv/tenants/user/.cache/huggingface/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218
setsid nohup env VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo \
  vllm serve "$SNAP8B" --served-model-name qwen3-8b-bf16 --host 127.0.0.1 --port 8001 \
  --gpu-memory-utilization 0.25 --max-model-len 32768 --enable-prefix-caching \
  --structured-outputs-config '{"backend":"xgrammar","disable_any_whitespace":true}' \
  --generation-config vllm --default-chat-template-kwargs '{"enable_thinking": false}' \
  > /srv/tenants/user/artifacts/logs/20260828/serve_8b_032810.log 2>&1 &

# --- run ---
DS=/srv/tenants/user/experiments/2x2/dataset/track_a_evaluation_release_v1
python -m src.cli run \
  --profile b1_single_qwen3_8b \
  --target "$DS/blind/PWN-003/source" \
  --condition B1prime@2x2 \
  --device atom \
  --artifacts-root /srv/tenants/user/artifacts \
  --seed 20260828
```

> ⚠ **2026-09-20 — 위 명령은 지금 그대로는 안 돈다.** `--condition B1prime@2x2`가 무효값이
> 됐다. 2×2가 폐기되고 Scout 오케스트레이터로 확정되면서 `CONDITION_PATTERN`이 `@pipeline`만
> 받는다([evaluation.md](../../evaluation.md) §2.1 · [D1](../../open-questions/D/D1.md) 닫힘).
> 프로필 `b1_single_qwen3_8b`도 그 트랙 철거 때 함께 걷혔다.
> **이 보고서의 수치와 판정은 그대로 유효하다** — 그때의 관측 기록이고 값을 고치지 않는다.

**서버 기동 인자 원문**(`non-default args`, 로그 8행):

```
{'model_tag': '…/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218',
 'default_chat_template_kwargs': {'enable_thinking': False}, 'host': '127.0.0.1', 'port': 8001,
 'max_model_len': 32768, 'served_model_name': ['qwen3-8b-bf16'], 'generation_config': 'vllm',
 'gpu_memory_utilization': 0.25, 'enable_prefix_caching': True,
 'structured_outputs_config': StructuredOutputsConfig(backend='xgrammar', disable_any_whitespace=True, …)}
```
