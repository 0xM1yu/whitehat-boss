# 단계 단위 콜드 재기동 교대 종단 스모크 — ATOM

> **왜 돌렸나.** ADR 0012 Phase 6·6.5의 PR 다섯 개(#332 · #334 · #329 · #333 · #330)가 올라왔다. 이 스모크는 그 합성 트리에서
> `harness_pool`이 **단계마다 필요한 모델만 띄우고 내리면서** Scout → Auditor → Critic을 끝까지 도는지 본다.
> 교대가 실제로 도는지, 서빙 창이 registry `context_limit`과 같은지, 메모리 판정·감시자·run 끝 내림이 도는지를 확인한다.
> **탐지 정확도를 잰 것이 아니다** — 슬라이스 1개 · 좌석 3개다.
>
> **비교표 밖이다** — Scout·Auditor 실서빙 키의 `checksum`이 0이다([CLAUDE.md](../../../CLAUDE.md) §0).
> run은 머지하지 않는 브랜치 [`smoke/phase6-atom`](https://github.com/0xM1yu/whitehat-boss/tree/smoke/phase6-atom)에서 돌았다.
> 이 브랜치는 origin/main `b52f847`에 다섯 PR을 머지 순서대로 합친 트리이고, 충돌은 전부 「두 쪽 다 남긴다」로 풀었다(머지 커밋 `e9d7e23` · `23ab9f2`).
> Auditor 프롬프트가 v3로 바뀐 뒤의 run이라, 이 run의 Auditor 출력을 이전 run과 직접 비교하지 않는다
> (`docs/agent-contracts.md` §4 규칙 1 — main에 있는 규칙이고, PR #330이 그 아래에 Auditor v1 → v3 적용 노트를 단다).

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-29 07:28 KST`(매니페스트 `finished_at` `2026-09-28T22:28:36Z`). 서버 정리·감시자 정지 07:28:37 |
| 실제 서빙 모델 | `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18`(별칭 `qwen3-14b`) · `hf://SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model@25d7f38146bdf0fad1eac09c586b1993ef3e6ee4`(`seccoderx-8b`) · `hf://Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`(`qwen3.8-27b`) · `hf://openai/gpt-oss-20b@6cee5e81ee83917806bbde320786a8fb61efebee`(`gpt-oss-20b`) — revision은 교대 보고 `report.server.<key>.revision`과 vLLM `non-default args`가 같다 |
| `repo_commit` | `23ab9f20bb5afcb62d25f2d26a12d22e94b8a23e`(`repo_dirty: false`) = `smoke/phase6-atom` |
| 돌린 PR 헤드 | #332 `1301ef4` · #334 `e49dac2` · #329 `605a30d` · #333 `c127032` · #330 `54bc4e7` — 다섯 모두 `23ab9f2`의 조상이다(`git merge-base --is-ancestor`, 2026-09-29). 2026-09-29 현재 다섯 PR 모두 열려 있어 이후 커밋이 얹히면 PR 헤드가 이 값과 달라진다 — **이 run이 돈 판은 브랜치 `smoke/phase6-atom`(`23ab9f2`)이 든다. 지우지 않는다** |
| `run_id` | `run_20260928_0001`(날짜는 UTC) |
| run 산출물 경로·보존 여부 | ATOM `~/phase6-smoke/artifacts/run_20260928_0001/` — 지우지 않았다. 저장소에는 없다. `trace.jsonl` sha256 `7667b344…4b73` · `run_manifest.json` sha256 `e9f6a26a…5c9f`(ATOM과 회수본이 같다) |
| 프로필 | `harness_pool`(실서빙 다섯 역할 · 검색은 Fake) · 조건 라벨 `B3prime@pipeline`(2026-09-28 스모크와 같은 라벨) · 동료 확인을 끄는 스모크 스위치는 이 브랜치에 없다(설계 그대로). hop0 need_evidence가 0이라 동료 확인·되묻기 루프는 발동하지 않았다 |
| `tool_runner` · `--device` | `docker` · `atom`(장비 파일 `config/serving/atom.yaml` — PR #334) |
| `tool_images` digest | `harness-tools:v2` = `sha256:9f7a989d622b6b817ba133e57329e725368c60a562a813e540b0361c0459186e`(docker 이미지 config sha256) |
| `--gpu-memory-utilization` | `qwen3-14b` 0.40 · `seccoderx-8b` 0.30 · `qwen3.8-27b` 0.80 · `gpt-oss-20b` 0.30. 분모는 MemTotal 125,513,992 kB = 119.70 GiB |
| 동시에 떠 있던 다른 서버 | **이 run의 서버뿐이다.** 시작 전 GPU 프로세스·vLLM·Joern·java가 모두 0이었다. 감시자 표본 166개의 `up=` 목록에는 이 run의 키만 있다. Scout 단계에서만 두 서버(8001 `qwen3-14b` + 8000 `seccoderx-8b`)가 함께 떴다 |
| 원문 산출물 보존 위치 | ATOM `~/phase6-smoke/` — 실행 스크립트 `run_smoke.sh`, 연쇄 기록 `logs/chain.log`, 하네스 stdout `logs/run.20260929_060551.log` |
| 서빙 서버 stdout | ATOM `~/phase6-smoke/serve/logs/<key>.run_20260928_0001.<UTC>.log` 네 개 · 교대 기록 `swap.log` · 감시자 기록 `guard.log` |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| **교대 종단** | **네 번 모두 `ok=true`** — Scout(4단계) → Auditor(5) → Critic(7) → run 끝 내림. run은 **완주**했다(`stopped_after: null`, 하네스 종료 코드 0). 동시에 떠 있던 서버는 Scout 단계의 둘이 최대다 |
| **서빙 창 = `context_limit`** | 네 키 모두 교대 보고의 `max_model_len`이 registry와 같았다 — 40,960 · 40,960 · **262,144** · 131,072. 교대마다 하네스가 `/v1/models`로 다시 확인했고(D87 (a)①), 창이 어긋나면 run을 멈추는 경로는 이번에 안 탔다 |
| **교대 한 번의 시간** | Scout **320 s** · Auditor **445 s** · Critic **135 s** · 내림 **1 s**(`model_swap.wall_ms`). 합 901 s로 run wall 4,963 s의 18%다(계산값) |
| **기동 시간·적재**(vLLM 로그) | `qwen3-14b` 27.52 GiB · 169 s · 준비 191 s / `seccoderx-8b` 15.27 GiB · 101 s · 126 s / **`qwen3.8-27b` 51.1 GiB · 359 s · 442 s** / `gpt-oss-20b` 13.8 GiB · 97 s · 131 s |
| **Auditor 창 262,144·KV를 ATOM에서 처음 쟀다** | KV 캐시 39.31 GiB = **636,746토큰**, 262,144토큰 요청 기준 동시 2.43배. 생성은 **4.27 tok/s**다(출력 토큰 ÷ 호출 wall, 계산값 — 두 호출 같음). ⚠ Auditor `request_timeout_s` 7,500 s가 전제한 4.5 tok/s(외부 보고)보다 낮다 — 출력이 상한 32,768토큰까지 가면 이 속도로 32,768 ÷ 4.27 ≈ 7,674 s(계산값)라 7,500 s를 넘는다(§3 · §7) |
| ⚠ **Auditor 기동 전 메모리 판정은 0.4 GiB 안팎의 여유로 통과했다** | 게이트(`swap.sh`의 `wait_for_memory`)는 free·need를 둘 다 `%.1f`(0.1 GiB)로 반올림한 뒤 비교한다 — 교대 명령이 잰 `memavail_before` **115.2 GiB** ≥ 필요량 **114.8 GiB**였다(114.8은 로그에 안 남아 같은 식으로 다시 셈한 값이다). 반올림 전으로는 필요량 0.80 × 119.70 + 3 + 12 + 4 = 114.76 GiB, MemAvailable 115.15~115.25 GiB라 여유는 0.39~0.49 GiB다(계산값). 장비 파일이 계산으로 적어 둔 「여유 0.5 GiB」(09-28 빈 장비 MemAvailable 115.3 GiB 기준)와 거의 같았다 — §4 |
| 감시자 | **개입 0회.** MemAvailable 최저 **16.7 GiB**(06:29:24 KST, Auditor 서빙 중) — 하한 12 GiB. run 끝 내림이 감시자를 멈췄다(`guard 1828843 stop requested`) |
| 모델 호출 | **7건 전부 `ok`, 시도 1** — Scout 3(105~121 s) · Auditor 2(**1,467 s · 2,188 s**) · Critic 2(28 s · 32 s). 400 · 타임아웃 0. 일곱 건의 wall은 각자의 요청 타임아웃(Scout 1,200/1,800 s · Critic 900 s는 PR #329, Auditor 7,500 s는 main에 있던 값)의 3~29%다(계산값). ⚠ 출력이 상한까지 가는 Auditor 호출은 위 4.27 tok/s로 셈하면 7,500 s를 넘는다(계산값) |
| Auditor v3 입력 | `input_tokens` **3,782 · 3,766**(창 262,144 − 출력 상한 32,768 = 입력 몫 229,376의 1.6%, 계산값) · `output_tokens` 6,267 · 9,351. ⚠ 슬라이스가 함수 2개 · 719자로 작아서다 — PR #330이 걱정한 「v3 입력이 여유 5,043을 넘는가」는 옛 창(53,248 — 입력 몫 20,480, 최대 입력 15,437 → 여유 5,043: [2026-09-26 스모크](2026-09-26-slice-smoke-atom.md), `config/models.yaml`의 `qwen3.8-27b` 「기동 조건」) 기준이라 이 run에는 해당 없다. 큰 입력에서의 호출 시간·GPU 예산은 **이 표본으로는 답하지 않는다** |
| D83 도장 | `issued_override` **0건** — Scout 3 · Auditor 2 출력이 모두 발급 ID·초기 state를 그대로 옮겼다(이 표본) |
| 판정(참고) | Scout: memory·boundary review → `triaged`, injection reject → `queued`. Auditor: E1 둘. Critic: 둘 다 rebut → 최종 `queued` 2건. **정답 대조 없음 — 탐지 결과로 읽지 않는다** |
| 비용 | GPU 예산 4,058 / 7,200 s(모델 호출 wall 합과 같다) · 입력 30,555 · 출력 21,299토큰 |

## 2. 실험 환경

- **장비** `atom-host`(NVIDIA GB10 · 통합메모리 MemTotal 119.70 GiB). `nvidia-smi --query-gpu=memory.total`이 `[N/A]`를 내서, 교대 명령이 **통합메모리 판정**(MemAvailable)을 탔다(`swap.log`의 `memory=unified`).
- **vLLM 0.27.1**(교대 보고 `vllm_version`) · 하네스 파이썬 `~/venv-probe`(3.12.3 — 같은 날 04:41 KST 읽기 점검의 `python -V`) · 가중치는 HF 캐시(`~/.cache/huggingface/hub`).
- **기동 인자는 장비 파일이 든다.**
  - 네 서버 모두 `--enforce-eager` · `--max-num-seqs 8` · `--enable-prompt-tokens-details`다.
  - reasoning parser는 Scout 둘이 `deepseek_r1`, Auditor가 `qwen3`이다.
  - Auditor·Critic은 구조화 출력 backend가 xgrammar이고, Critic은 attention backend가 triton이다(`non-default args`).
- **시작 전 장비**: MemAvailable 115.4 GiB · GPU 프로세스 0 · vLLM·Joern·java 0 · 도커 컨테이너 0. 두 번 확인했다 — 06:04:52 KST 읽기 점검, 그리고 시작 직전 관문(시작과 같은 명령 안 — 출력 `avail=115GiB gpu_procs=0 vllm_joern_java=0`). 둘 다 이 스모크를 돌린 세션의 ssh 출력이고 회수 자료에는 없다. `chain.log`의 첫 줄(06:05:51 · MemAvailable 120,957,144 kB)이 회수 자료에 남은 시작 시점 값이다.

## 3. 실험 — 교대 종단

### 목적

**경로 성립 판정**이다. 성능이나 탐지 품질을 재지 않는다.
- 러너가 단계 경계에서 교대 명령을 부르는가.
- 교대 명령이 필요한 키만 띄우고 나머지를 내리는가.
- 하네스가 창을 다시 확인하는가.
- run 끝에 전부 내려가고 감시자가 멈추는가.

### 조건

- 입력: 2026-09-28 정상 표본 스모크의 qemu_vmxnet3 입력 그대로다(`~/ne-loop/benign/qemu_vmxnet3/{repo,slices_target.json}` — 파일 하나 `hw/net/vmxnet3.c`, 슬라이스 1개, 슬라이서 `partial`). [그 보고서](2026-09-28-ne-loop-smoke-benign-atom.md) §2가 입력을 적는다.
- 역할: Scout 셋(`scout_memory`·`scout_injection` = `qwen3-14b`, `scout_boundary` = `seccoderx-8b`) · Auditor `qwen3.8-27b`(프롬프트 v3) · Critic `gpt-oss-20b`(v1) — 전부 실서빙이다. 검색 둘은 Fake다.
- 교대 선언(`harness_pool.execution.swap`): 상주 없음 · 교대 대상 다섯 역할 · `settle_timeout_s` 3,800.

### 결과 — 시간선 (UTC, `swap.log` · `trace.jsonl`)

| 시각 | 사건 |
| --- | --- |
| 21:05:53 | run 시작. 0~3단계 3 s |
| 21:05:56 | 교대 → `[qwen3-14b seccoderx-8b]`, memory=unified |
| 21:05:58 → 21:09:09 | `qwen3-14b` 기동(8001 · util 0.4) → 준비 **191 s** |
| 21:09:10 → 21:11:16 | `seccoderx-8b` 기동(8000 · util 0.3) → 준비 **126 s**. 교대 완료(`model_swap` wall 320,152 ms) |
| 21:11:16 → 21:17:00 | Scout 호출 3건(117.5 · 105.3 · 120.5 s) |
| 21:17:00 → 21:17:01 | Scout 두 서버 내림(프로세스 그룹 1829368 · 1833324) |
| 21:17:03 → 21:24:25 | `qwen3.8-27b` 기동(8002 · util 0.8 · 창 262,144) → 준비 **442 s**(적재 359 s). 교대 완료(445,428 ms) |
| 21:24:25 → 22:25:20 | Auditor 호출 2건(1,467.5 · 2,187.5 s). 게이트(6단계) E1 둘 |
| 22:25:20 → 22:25:24 | Auditor 서버 내림(프로세스 그룹 1842192) |
| 22:25:24 → 22:27:35 | `gpt-oss-20b` 기동(8003 · util 0.3 · 창 131,072) → 준비 **131 s**. 교대 완료(134,956 ms) |
| 22:27:35 → 22:28:35 | Critic 호출 2건(28.2 · 31.7 s) |
| 22:28:35 → 22:28:36 | run 끝 내림 `()` — `gpt-oss-20b` 내림, 「감시자 1828843를 멈춘다」. 교대 완료(1,076 ms). 9단계 리포트 |

### 결과 — 서버별 (vLLM 로그 · 교대 보고)

| 키 | util | 창 | 적재 | 적재 시간 | 준비 | KV 캐시 | 창 기준 동시 | 기동 직전 MemAvailable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qwen3-14b` | 0.40 | 40,960 | 27.52 GiB | 169.2 s | 191 s | 19.43 GiB · 127,328토큰 | 3.11배 | 115.1 GiB |
| `seccoderx-8b` | 0.30 | 40,960 | 15.27 GiB | 101.1 s | 126 s | 19.48 GiB · 141,840토큰 | 3.46배 | 64.2 GiB |
| `qwen3.8-27b` | 0.80 | 262,144 | 51.1 GiB | 359.1 s | 442 s | 39.31 GiB · 636,746토큰 | 2.43배 | 115.2 GiB |
| `gpt-oss-20b` | 0.30 | 131,072 | 13.8 GiB | 96.5 s | 131 s | 21.14 GiB · 894,488토큰 | 6.82배 | 115.2 GiB |

- 「준비」는 교대 명령이 잰 기동부터 준비 확인 통과까지다(`ready_s`). 적재 시간은 vLLM의 `Model loading took`이다.
- 준비 상한(`atom.yaml` `ready_timeout_s`)은 표의 준비 시간 대비 `qwen3-14b` 550 s = 2.9배 · `seccoderx-8b` 400 s = 3.2배 · `qwen3.8-27b` 1,720 s = 3.9배 · `gpt-oss-20b` 300 s = 2.3배다(계산값). 장비 파일 주석은 `seccoderx-8b`·`qwen3-14b`·`gpt-oss-20b` 셋을 이전 ATOM 기동 실측 × 3으로, Auditor만 계산값 × 3(vast 기동에서 적재 밖 시간 + ATOM에서 잰 가장 느린 적재 속도로 셈한 적재 시간)으로 적는다. `settle_timeout_s` 3,800 s는 가장 긴 교대(445 s)의 8.5배다(계산값).

### 결과 — 모델 호출 (`trace.jsonl` `model_call`)

| 역할 | 모델 · 프롬프트 | 대상 | 입력 / 출력 토큰 | wall | 결과 |
| --- | --- | --- | --- | --- | --- |
| scout_memory | `qwen3-14b` v3 | `cand_0001` | 8,020 / 880 | 117.5 s | review → `triaged` |
| scout_boundary | `seccoderx-8b` v3 | `cand_0002` | 5,413 / 1,378 | 105.3 s | review → `triaged` |
| scout_injection | `qwen3-14b` v3 | `cand_0003` | 7,952 / 909 | 120.5 s | reject → `queued` |
| auditor | `qwen3.8-27b` v3 | `find_0001` | 3,782 / 6,267 | 1,467.5 s | E1(`static_ok`·`graph_ok`) |
| auditor | `qwen3.8-27b` v3 | `find_0002` | 3,766 / 9,351 | 2,187.5 s | E1 |
| critic | `gpt-oss-20b` v1 | `find_0001` | 797 / 1,159 | 28.2 s | rebut → `queued` |
| critic | `gpt-oss-20b` v1 | `find_0002` | 825 / 1,355 | 31.7 s | rebut → `queued` |

일곱 건 모두 `ok: true` · `attempts: 1`이고 서버 로그의 `POST /v1/chat/completions`도 전부 200이다.

### 근거 로그

교대 보고(Auditor, `trace.jsonl` seq 55 발췌):

```
"kind":"model_swap","stage":5,"wall_ms":445428,"data":{"models":"qwen3.8-27b","ok":true,
"report.memory":"unified","report.memavail_before":25.2,"report.server.qwen3-14b.action":"stop",
"report.server.seccoderx-8b.action":"stop","report.server.qwen3.8-27b.action":"launch",
"report.server.qwen3.8-27b.load_s":359.146593,"report.server.qwen3.8-27b.max_model_len":262144,
"report.server.qwen3.8-27b.memavail_before":115.2,"report.server.qwen3.8-27b.ready_s":442,
"report.server.qwen3.8-27b.util":0.8,"report.server.qwen3.8-27b.vllm_version":"0.27.1", ...}
```

Auditor 서버(`qwen3.8-27b.run_20260928_0001.20260928T211703Z.log`):

```
non-default args: {... 'max_model_len': 262144, 'enforce_eager': True, 'served_model_name': ['qwen3.8-27b'], 'reasoning_parser': 'qwen3', 'gpu_memory_utilization': 0.8, 'max_num_seqs': 8, ...}
Model loading took 51.1 GiB memory and 359.146593 seconds
Available KV cache memory: 39.31 GiB
GPU KV cache size: 636,746 tokens
Maximum concurrency for 262,144 tokens per request: 2.43x
```

run 끝(`swap.log` · `guard.log` · `chain.log`):

```
2026-09-28T22:28:35Z [run_20260928_0001] swap → [전부 내림] memory=unified
2026-09-28T22:28:35Z [run_20260928_0001] stop gpt-oss-20b (pgid 1913492)
2026-09-28T22:28:36Z [run_20260928_0001] 감시자 1828843를 멈춘다 — run run_20260928_0001의 서버를 다 내렸다
2026-09-28T22:28:36Z [run_20260928_0001] swap done → []
2026-09-29T07:28:37+09:00 guard 1828843 stop requested
2026-09-29T07:28:36+09:00 harness rc=0
2026-09-29T07:28:36+09:00 end run=run_20260928_0001 servers_rows=0
```

### 해석 — 여기까지만 말할 수 있다

- **교대 경로가 이 장비에서 끝까지 성립한다.**
  - 단계마다 그 단계 모델만 떠 있었다. 서버 기록에 이 run 밖의 행은 없었다.
  - run 끝에는 기록이 비었고 감시자가 멈췄다.
  - 표본은 run 하나이고 교대는 네 번이다.
- **교대 비용은 이 run에서 wall의 18%다(계산값).** 호출이 짧은 입력이었다면 비중은 더 컸을 것이다. 이 수는 이 run의 몫이지 일반 비율이 아니다.
- **Auditor 교대의 메모리 판정은 기동 전 여유가 거의 없다.** 이번에는 통과했다. 그러나 같은 계정의 다른 프로세스나 도커 도구 컨테이너가 0.5 GiB만 더 쥐어도(여유는 0.4 GiB 안팎이다, 계산값) 거부되어 run이 5단계에서 멈춘다. 그 선택지는 §4에 적는다.
- **생성 속도 4.27 tok/s는 호출 wall로 나눈 값이다.** 사고와 본문을 가르지 않았다. 서버 로그의 10초 창 `Avg generation throughput` 평균도 4.28 tok/s다(363창 — 프리필이 든 두 창은 뺐다, 계산값). 같은 모델을 ATOM에서 util 0.80으로 돌린 [2026-09-28 스모크](2026-09-28-ne-loop-smoke-benign-atom.md) §5의 Auditor 여섯 호출도 출력 토큰 ÷ wall이 4.17~4.29 tok/s(문맥 4.9k~17.7k, 여섯 다 `attempts: 1` — 계산값)라 같은 자리다.
  - **이 속도로 셈하면 Auditor `request_timeout_s` 7,500 s가 출력 상한을 못 덮는다(계산값).** 설정은 `7,500 ≥ 32,768 ÷ 4.5 tok/s ≈ 7,282 s`에서 나왔다 — 32,768은 출력 전체(사고 28,672 + 답 4,096)이고 4.5 tok/s는 외부 보고값이다(`config/models.yaml`의 `qwen3.8-27b` 주석). 같은 식에 이 run의 4.27을 넣으면 32,768 ÷ 4.27 ≈ 7,674 s(계산값)로 7,500 s를 넘는다 — 이 속도라면 출력 32,025토큰(계산값)에서 시계가 먼저 끊는다.
  - 이번 두 호출은 출력 6,267 · 9,351토큰(상한의 19% · 29%, 계산값)이라 안 닿았다. 이 속도라면 사고가 예산 28,672토큰을 다 써도 답이 약 3,353토큰(= 7,500 × 4.27 − 28,672, 계산값)까지는 7,500 s 안이다 — 사고·답 분리는 미측정(§5)이라 이 조건이 얼마나 자주 서는지는 모른다. 사고 예산 28,672토큰만 4.27로 나눈 ≈ 6,700 s(계산값)는 타임아웃이 걸리는 단위(사고와 답을 합친 요청 하나)가 아니다.
  - 4.27은 문맥 ≈13k토큰(입력 3.8k + 출력 9.4k)까지의 값이다. 로그의 창 평균은 KV 사용 1.0% → 2.4% 구간에서 4.30 → 4.26으로 거의 안 변했다(계산값) — 32,768토큰 출력에서 이 속도가 유지되는지는 안 쟀다.
  - 잠금 테스트 `test_the_canonical_auditor_thinks_within_a_budget_that_holds_together`는 속도 상수 4.5(외부 보고)로 성립한다 — 그 상수를 4.27로 바꾸면 `7,500 ≥ 32,768 ÷ 4.27`이 거짓이라 실패한다(2026-09-29 한 줄 바꿔 돌려 확인).
  - **이 보고서는 설정을 바꾸지 않았다.** 선택지는 §7 2행.
- **판정은 읽지 않는다.** E1 둘과 Critic rebut 둘은 정답 대조가 없고 좌석 셋뿐이다.

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| (막히지 않음 — 관측) Auditor 기동 전 메모리 판정 여유 **0.4 GiB 안팎**(게이트 비교값 115.2 ≥ 114.8 — 둘 다 0.1 GiB 반올림 · 반올림 전 여유 0.39~0.49 GiB, 계산값 — §1) | 필요량 식 `util × MemTotal + 3 + 12 + 4 GiB`에서 util 0.80 몫(95.76 GiB)과 고정 여유 19 GiB의 합 114.76 GiB(계산값)가 빈 장비 MemAvailable(115.1~115.4 GiB)의 거의 전부다 | **이 보고서는 설정을 바꾸지 않았다.** 선택지: (a) Auditor util을 낮춘다 — 단 창 262,144 요청 하나를 담을 KV가 남아야 한다. 그 KV는 262,144토큰 × 약 64.7 KiB(KV 39.31 GiB ÷ 636,746토큰) ≈ 16.2 GiB이고 KV 밖 몫은 가중치+non-torch 54.58 · 활성 1.88 GiB(서버 로그)라, util 하한은 (54.58 + 1.88 + 16.2) ÷ 119.70 ≈ 0.61이다(모두 계산값). 그 아래면 창을 못 담아 vLLM 0.27.1이 기동을 거부한다(`get_kv_cache_configs`가 부르는 `_check_enough_kv_cache_memory`, `vllm/v1/core/kv_cache_utils.py` v0.27.1 — 소스 판독, 미실측). util 0.01이 약 1.2 GiB다(계산값). 로그의 KV 사용 최대 2.4%(Auditor 서버 로그 10초 창 `GPU KV cache usage`, 07:23~07:25 KST — 두 번째 호출 끝 무렵)는 입력 3.8k · 출력 9.4k 요청 하나의 값이라 이 하한과 무관하다(창 262,144 요청 하나는 KV 풀 636,746토큰의 41%다, 계산값) (b) 여유값 셋(`SERVER_HOST_GIB`·`MIN_AVAIL_GIB`·`SWAP_MARGIN_GIB`, 환경변수) 중 하나를 줄인다 (c) 그대로 두고 다른 작업과 겹치지 않게 운영으로 막는다. PR #334에서 정한다 |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| Scout·Auditor 실서빙 키 `checksum` | 이 스모크 범위가 아니다(0이라 비교표 밖) | `tools/model_checksum.py`로 네 스냅샷을 잰다(ADR 0012 Phase 6 줄 — 결정 4의 새 키 `@rev`·checksum) |
| Auditor 사고/본문 토큰 분리 | trace가 `output_tokens` 합만 싣는다 — 하네스 어댑터가 응답의 추론 필드를 안 남긴다([D84](../../open-questions/D/D84.md)) | 응답의 `message.reasoning`(vLLM 0.27.1의 필드 이름이다 — `reasoning_content`는 없다, [2026-09-12 보고서](2026-09-12-critic-gptoss-token-accounting-atom.md) §4)과 `message.content`를 따로 꺼내 그 모델 토크나이저로 센다 — 0.27.1의 `usage`에는 추론 토큰 수가 없다(소스 판독) |
| 큰 v3 Auditor 입력에서의 호출 시간·GPU 예산 소진 | 슬라이스가 함수 2개 · 719자로 작다(입력 3.8k — 창의 1.4%, 계산값). 옛 창(53,248 — 입력 몫 20,480) 기준의 「여유 5,043」 우려(여유는 [2026-09-26 스모크](2026-09-26-slice-smoke-atom.md)의 최대 입력 15,437에서 나왔고, 우려는 PR #330이 `config/models.yaml`의 `qwen3.8-27b` 「기동 조건」에 적었다)는 이 run에 해당 없다 — 이 창의 입력 몫은 229,376(계산값)이다 | 큰 슬라이스(2026-09-26 스모크의 147건 같은 입력)로 돌려 입력 토큰별 호출 wall과 GPU 예산(7,200 s) 소진을 잰다 |
| 교대 실패 경로(기동 실패 · 창 불일치 · 감시자 사망)의 실장비 동작 | 이 run에서는 안 탔다 — 테스트(가짜 vLLM)로만 고정됐다 | 일부러 창을 어긋나게 한 장비 파일로 한 번 돈다 |
| vast.ai 종단 | 도구 단계를 어디서 돌릴지 정해지지 않았다(컨테이너에 docker 없음) | 그 결정 뒤 `--device vastai` |

## 6. 반증 조건

- **교대가 실패했다면** 둘 중 하나가 틀린 것이었다.
  - 장비 파일의 기동 인자가 이 장비·vLLM 판과 맞지 않았다(플래그 철자 · parser · 창).
  - 하네스와 교대 명령의 계약(인자 · 환경변수 · stdout 마지막 줄 JSON 보고 · 서버 소유권)이 실장비에서 어긋났다.
  - 네 번 모두 `ok=true`였으므로 이 조합에서는 둘 다 맞았다.
- **서빙 창이 `context_limit`과 달랐다면** D87 (b)(운영 창으로 띄운다)가 장비 파일에서 새고 있었다는 뜻이다. 네 키 모두 같았다.
- **run 끝에 서버 기록이 남았거나 감시자가 살아 있었다면** 소유권(run ID) 판정이나 run 끝 신호가 틀렸다는 뜻이다. 기록 0 · 감시자 정지였다.
- **감시자가 개입했다면** util 합 또는 여유값이 이 장비에서 틀렸다는 뜻이다. 개입 0회였다. 다만 Auditor 기동 전 판정의 여유는 0.4 GiB 안팎이었다(§4).

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 1 | Auditor 기동 전 여유 0.4 GiB 안팎 — §4 선택지 | PR #334에서 결정. 설정 무변경 |
| 2 | 이 run의 4.27 tok/s로 셈하면 Auditor `request_timeout_s` 7,500 s가 출력 상한 32,768토큰을 못 덮는다 — 32,768 ÷ 4.27 ≈ 7,674 s(계산값)이고 설정 근거 4.5 tok/s는 외부 보고값이다. 선택지: (a) 타임아웃을 올린다(하한 ≈ 7,674 s — 2026-09-28 스모크에서 가장 느린 Auditor 호출의 4.17 tok/s면 ≈ 7,853 s, 둘 다 계산값) (b) 사고 예산·출력 상한을 줄인다 — 지금 값은 2026-09-26 팀이 council에 맡겨 정했다(성능이 시간보다 먼저 — `config/models.yaml`의 `qwen3.8-27b` 주석) (c) 근거 문구만 4.27로 갱신한다 — 잠금 테스트 `test_the_canonical_auditor_thinks_within_a_budget_that_holds_together`의 속도 상수(4.5)를 같이 바꾸면 실패하므로 (c)만으로는 서지 않는다. ⚠ 어느 쪽이든 run GPU 예산 7,200 s와 같이 본다 — 이 run의 Auditor 두 호출이 그 예산의 50.8%(3,655 s, 계산값)를 썼고, 타임아웃 7,500 s는 호출 하나로 그 예산보다 길다(같은 주석이 이 예산 문제를 이슈 #97로 든다) | 결정 대기. 설정 무변경. 이슈 미등재 — 결정 뒤 #261에 잔여로 적거나 신규로 뗀다 |
| 3 | `atom.yaml`의 준비 상한·주석을 계획값에서 이 실측(191 · 126 · 442 · 131 s)으로 | PR #334 |
| 4 | 새 키 셋 `checksum`(Scout·Auditor) | 미착수 |
| 5 | 큰 슬라이스로 v3 Auditor 호출 시간·GPU 예산(입력 몫 400 우려는 이 창에서 해당 없음 — §5) | 미착수 |

## 8. 정리 상태

- **서빙 서버: 전부 내렸다.** run 끝 내림이 `gpt-oss-20b`를 내렸고 서버 기록 행은 0이다. 07:43 KST 확인: GPU compute 프로세스 0 · vLLM·감시자·교대 프로세스 0 · MemAvailable 115.4 GiB(`120978444 kB`). 이 확인은 세션의 ssh 읽기 점검 출력이고 회수 자료에는 없다 — 회수 자료의 마지막 값은 `chain.log`의 07:28:36 `MemAvailable: 120975188 kB`다.
- **감시자: 멈췄다**(run 끝 내림의 STOP_GUARD).
- **임시 설정·프로필: 없다.** 장비 파일과 프로필은 브랜치에 커밋된 그대로다. 환경변수는 실행 스크립트 안에서만 줬다.
- **ATOM 파일: 남겨 뒀다.** `~/phase6-smoke/`(워크트리 `wt` · 산출물 · 로그)와 `~/whitehat-boss`의 워크트리 등록 하나다. 다른 사람의 파일과 `~/ne-loop`은 읽기만 했다.
- **저장소 변경:** 이 보고서와 색인 한 행뿐이다.

## 9. 부록 — 재현 절차

사전 관문(시작 직전 같은 명령 안에서 통과해야 시작한다):

```bash
ssh atom 'set -u; avail=$(awk "/MemAvailable/{print int(\$2/1048576)}" /proc/meminfo); gp=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader | grep -c .); vj=$(ps -eo args | grep -E "vllm|joern|java" | grep -v grep | grep -c .); echo "avail=${avail}GiB gpu_procs=$gp vllm_joern_java=$vj"; if [ "$avail" -ge 110 ] && [ "$gp" -eq 0 ] && [ "$vj" -eq 0 ] && [ ! -e ~/phase6-smoke/DONE ]; then setsid nohup bash ~/phase6-smoke/run_smoke.sh > ~/phase6-smoke/logs/outer.log 2>&1 < /dev/null & echo "started pid=$!"; else echo "GATE FAILED — not started"; fi'
```

배치: `~/whitehat-boss`에서 `git fetch origin smoke/phase6-atom` 뒤 `git worktree add --detach ~/phase6-smoke/wt FETCH_HEAD`(`23ab9f2`).

`~/phase6-smoke/run_smoke.sh` 원문:

```bash
#!/usr/bin/env bash
# Phase 6·6.5 교대 스모크 — 2026-09-29. 머지하지 않는 브랜치 smoke/phase6-atom(23ab9f2)에서 돈다.
# 안전장치: 감시자(3 h) · 하네스 timeout 3 h · 내 run의 서버만 내린다(swap.sh 인자 없음 + 그 run ID).
set -u
S="$HOME/phase6-smoke"; WT="$S/wt"; L="$S/logs"
export SERVE_DIR="$S/serve" VLLM_BIN="$HOME/venv-probe/bin/vllm" TIKTOKEN_ENCODINGS_BASE="$HOME/tiktoken_encodings"
T=$(date +%Y%m%d_%H%M%S)
cd "$WT" || exit 2
echo "$(date -Is) start $(git rev-parse --short HEAD)" >> "$L/chain.log"
grep -E "MemTotal|MemAvailable" /proc/meminfo >> "$L/chain.log"
MAX_HOURS=3 setsid nohup bash tools/serve/guard.sh > "$L/guard.$T.out" 2>&1 < /dev/null &
for _ in $(seq 1 30); do [ -s "$SERVE_DIR/guard.pid" ] && break; sleep 1; done
echo "$(date -Is) guard pid $(cat "$SERVE_DIR/guard.pid" 2>/dev/null || echo NONE)" >> "$L/chain.log"
timeout --kill-after=60 10800 "$HOME/venv-probe/bin/python" -m src.cli run --profile harness_pool --device atom \
  --condition B3prime@pipeline \
  --target "$HOME/ne-loop/benign/qemu_vmxnet3/repo" \
  --slices "$HOME/ne-loop/benign/qemu_vmxnet3/slices_target.json" \
  --artifacts-root "$S/artifacts" > "$L/run.$T.log" 2>&1 < /dev/null
rc=$?
echo "$(date -Is) harness rc=$rc" >> "$L/chain.log"
RUN=$(ls -1t "$S/artifacts" 2>/dev/null | grep '^run_' | head -1)
if [ -n "$RUN" ] && [ -f "$SERVE_DIR/servers" ] && awk -v r="$RUN" '$3 == r' "$SERVE_DIR/servers" | grep -q .; then
  echo "$(date -Is) leftover rows for $RUN — teardown" >> "$L/chain.log"
  HARNESS_RUN_ID="$RUN" HARNESS_DEVICE=atom HARNESS_PYTHON="$HOME/venv-probe/bin/python" \
    timeout 300 bash tools/serve/swap.sh >> "$L/teardown.$T.log" 2>&1
  echo "$(date -Is) teardown rc=$?" >> "$L/chain.log"
fi
echo "$(date -Is) end run=$RUN servers_rows=$(wc -l < "$SERVE_DIR/servers" 2>/dev/null || echo 0)" >> "$L/chain.log"
grep -E "MemAvailable" /proc/meminfo >> "$L/chain.log"
echo "$rc" > "$S/DONE"
```
