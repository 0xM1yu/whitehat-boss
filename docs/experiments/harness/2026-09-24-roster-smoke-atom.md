# 새 로스터 역할별 실서빙 스모크 — Scout 풀 · Auditor — ATOM

> **왜 돌렸나.** [ADR 0012](../../decisions/0012-harness-canon-v2.md) 결정 4의 새 키 `seccoderx-8b`(Scout
> memory·boundary)·`qwen3.6-35b-a3b`(Auditor)는 등재·배선까지 됐지만, **이 저장소에 뜬 기록이 없었다** —
> `checksum`·`benchmark`가 전부 0이다. 그 둘과 injection 잠정 후보 Qwen3-14B(registry 밖)를 역할별로 띄워
> ① sm_121에서 뜨는가 ② 하네스 스키마를 통과하는가 ③ 메모리·속도가 run을 막지 않는가를 봤다.
> 결정 4의 Critic `gpt-oss-20b`는 2026-09-09에 이미 떴고, 이번 스모크의 대상이 아니다.
> **탐지 정확도를 잰 것이 아니다.**
>
> **비교표 밖이다** — 실서빙 키의 `checksum`이 전부 0이다([CLAUDE.md](../../../CLAUDE.md) §0). run은
> 머지하지 않는 브랜치에서 돌았다 — `exp/atom-roster-smoke` @ `fdbc465`이고, main `c301342`에 설정만 얹었다(§9).
>
> **이 폴더에 적은 이유** — ADR 0012 결정 5는 실험 기록의 원천을 Notion으로 옮기지만 아직 제안이다(⏵ 2026-09-25 — 승인됐다. ⏵ 2026-09-26 — 그 결정의 범위가 좁아져 **하네스를 돌린 run 기록은 이 폴더가 맞다** — 그 결정의 ⏵ 표).
> 그래서 현행 규칙(이 폴더 [README](../README.md)의 첫 규약)을 따랐다(2026-09-24 팀 결정).

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-24 11:13 KST`(마지막 run `run_20260924_0003` 종료 · 매니페스트 `02:13:13Z`). 서버 정리 11:15 |
| 실제 서빙 모델 | `hf://SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model@25d7f38146bdf0fad1eac09c586b1993ef3e6ee4`(별칭 `seccoderx-8b`) · `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18`(별칭 `qwen3-14b` — **스모크 브랜치에만 있는 키**) · `hf://Qwen/Qwen3.6-35B-A3B@995ad96eacd98c81ed38be0c5b274b04031597b0`(별칭 `qwen3.6-35b-a3b`) |
| `repo_commit` | `fdbc46512b4536b02cc81e6d48a3a1e1e136644a`(세 run 매니페스트 모두 `repo_dirty: false`) |
| `run_id` | `run_20260924_0001`(Scout 풀) · `run_20260924_0002`(Auditor · parser를 건 판 — Finding 0) · `run_20260924_0003`(Auditor · 기동 인자를 바꾼 판 — 완주) |
| run 산출물 경로·보존 여부 | ATOM `~/smoke-0924/wt/artifacts/run_20260924_000{1,2,3}` — 지우지 않았다. 작성자 장비에 사본을 회수했다. 저장소에는 없다 |
| `tool_runner` | 프로필 선언 `docker` / 실제 `docker`(오버라이드 없음) |
| `--device` | `atom` |
| `tool_images` digest | `harness-tools:v1` = `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17`(docker 이미지 config sha256 — 세 run 같음) |
| `--gpu-memory-utilization` | `seccoderx-8b` 0.20 · Qwen3-14B 0.30 · `qwen3.6-35b-a3b` 0.64. 분모 119.7 GiB라 23.9 · 35.9 · 76.6 GiB다(계산값) |
| 동시에 떠 있던 다른 서버 | `run_0001` 동안: 8009 `seccoderx-8b` + 8010 Qwen3-14B 둘. `run_0002`·`0003` 동안: 8008 `qwen3.6-35b-a3b` 하나. 사전 점검(08:42 KST — 작성자 세션 기록, 회수본에는 없다)에서 GPU 연산 프로세스·vllm·joern이 전부 0이었다 |
| 원문 산출물 보존 위치 | 위 run 산출물 경로 + 작성자 장비 회수본 |
| 서빙 서버 stdout | ATOM `~/smoke-0924/logs/<별칭>.log`(`--enable-log-requests --enable-log-outputs`). ⚠ **`qwen3.6-35b-a3b` 첫 기동(`run_0002`)의 로그는 없다** — 두 번째 기동이 같은 파일을 덮어썼다 |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| sm_121 기동 | **셋 다 떴다** — vLLM 0.27.1 · bf16 · `--enforce-eager`(§2 표) |
| 한 장비에 함께 올리기 | **Scout 풀(`seccoderx-8b` + Qwen3-14B)은 함께 올라간다** — 두 서버가 떠 있는 동안 `MemAvailable` 최저 49.1 GiB. **Auditor를 그 풀과 함께 올릴 수는 없다**(계산값 — §2). Auditor와 Critic(`gpt-oss-20b`)을 함께 올리는 것은 안 쟀다. 정본 injection `deepseek-r1-distill-qwen-32b`도 안 띄웠다 |
| Scout 스키마 | **31/31이 첫 시도에 `ScoutResponse`를 통과했다**(strict json_schema · `finish_reason` 전부 `stop`) |
| Auditor 스키마 | parser를 걸면 **0/7**이다 — 사고 모드에서 사고가 4,096토큰 안에 안 끝났다. parser를 빼고 `xgrammar`·`disable_any_whitespace`를 더하면 **36/36**이다(인자 셋을 한꺼번에 바꿨다). **그 판의 Auditor는 사고 없이 답했다** — 문법이 첫 토큰부터 걸렸다(§3 실험 2·3) |
| 생성 속도(단일 요청) | `seccoderx-8b` **13.3** · Qwen3-14B **8.1** · `qwen3.6-35b-a3b` **26.2** tok/s(정의는 §2). Scout 둘은 출력 토큰의 약 2/3가 사고 구간이다 |
| 타임아웃 | **Scout 호출 하나가 300초에 걸려 run 전체가 멈췄다**(`run_0001`) — 서버는 그동안 생성 중이었다. 이슈 #261 |
| 예산 | Scout 풀이 대상 11개에 `gpu_seconds` 3,453을 청구했다. 기본 예산 7,200이면 **Scout만으로 대상 ≈23개에서 멈춘다**(계산값 — [D24](../../open-questions/D/D24.md)) |
| Scout 계약 | boundary 좌석의 응답에 신호와 점수의 모순 2건(`triage_reject`인데 1.0)과 첫 reason code 형식 이탈 4건이 있었다(boundary 10건 중). 이 여섯이 점수 임계로 승격됐다 · `requested_context`는 객체가 아니라 reason code 표기의 문자열로 왔다 · boundary 좌석이 `location`의 `symbol`·`line_start`를 10/10 비워 냈고 그대로 저장됐다 — [D82](../../open-questions/D/D82.md)·[D83](../../open-questions/D/D83.md) |
| Auditor 채움 | `path`·`evidence_refs` **36/36**. `missing_evidence` **26/36** → 전부 해제 경로 없는 blocking([D65](../../open-questions/D/D65.md)) |
| 등급 | 6단계 **E2 4 · E1 32**. E1 32는 blocking만 9 · GRAPH 근거 없음만 6 · 둘 다 17이다. 7단계에 오른 넷은 **Critic이 전부 반박했다**(하나는 근거 ID가 비어 하네스가 uphold로 정규화했다). **E3 0** |

## 2. 실험 환경

- **장비** — ATOM `atom-host`.
  - GB10 · 통합메모리 119.7 GiB · 사용자 cgroup `memory.max` 80 GiB · `memory.high` max다
    (cgroup 값은 [portability.md](../../portability.md) §6 `atom` 행).
  - vLLM 0.27.1(`~/venv-probe`, Python 3.12).
  - 대상은 `~/targets/zlib`(`e3dc0a8`)이고 매니페스트 `source_hash`는
    `sha256:a9bec04a90454f343cb517794fe1f5570344c8d85d38db11109d27599a555c54`다.
- **스모크 설정**(브랜치 `fdbc465`, §9)
  - 새 키 `qwen3-14b`(8010)를 더했다.
  - 프로필은 둘이다. `atom_smoke_scout_pool`은 Scout 셋 실서빙 · auditor·critic Fake이고,
    `atom_smoke_auditor`는 Scout `fake_v2` · auditor·critic `qwen3.6-35b-a3b`다.
  - 전역 `candidate_max` 256→**12** · `critic_top_n` 8→**4**. `retriever: fake`.
  - ⚠ **critic을 `qwen3.6-35b-a3b`로 둔 것은 스모크 한정이다** — 정본 Critic은 `gpt-oss-20b`다. 스모크 프로필 주석은
    80 GiB cgroup을 전제로 Critic 실모델을 따로 안 띄웠다. 그런데 GPU 할당은 cgroup에 안 잡힌다(아래 「안전장치」).
    그러니 그 전제는 맞지 않고, 함께 올라가는지는 안 쟀다. 같은 키라 계열 검사가 건너뛴다.
- **샘플링** — 하네스가 보낸 값은 temperature 0.2 · top_p 0.95 · seed 20260901 · `max_tokens` 4,096이다(서버 로그의
  `SamplingParams`). `top_k`는 하네스가 안 보내는데, 서버 로그에는 Qwen 두 모델이 20, `seccoderx-8b`는 0으로 찍혔다.
  하네스는 `chat_template_kwargs`도 안 보낸다 — 채팅 템플릿의 사고 모드 기본값이 그대로 산다(§3 실험 2).
- **속도의 정의** — 이 보고서의 tok/s는 서버 로그 `Avg generation throughput` 가운데 `Running: 1 reqs`인 10초 창의
  중앙값이다(창 수 `seccoderx-8b` 237 · Qwen3-14B 106 · `qwen3.6-35b-a3b` 81).
  - 앞의 둘은 프리필이 섞인 창을 빼도 13.3 · 8.1로 같다.
  - `qwen3.6-35b-a3b`는 창의 절반에 프리필이 섞여 있고, 순수 디코드 42창만 보면 26.6이다.
  - 호출마다 `output_tokens ÷ wall`로 다시 재면 중앙값이 memory 13.2 · boundary 13.4 · injection 8.1이다(프리필 포함).
- **안전장치** — 감시자 `guard.sh`가 5초마다 두 층을 보고, 걸리면 이 스모크가 띄운 프로세스 그룹만 TERM→KILL한다.
  - 두 층은 장비 `MemAvailable` < 12 GiB와 cgroup anon > 72 GiB이고, 마감은 5시간이다.
  - vLLM의 GPU 할당은 cgroup이 아니라 장비 `MemAvailable`을 깎는다 — 두 층을 다 본 이유다. 감시자 로그의 cgroup anon은
    최대 6.5 GiB였다.
  - `launch.sh`는 감시자·빈 포트·외부 GPU 프로세스 0·여유 메모리를 확인하고 나서야 띄운다.
  - **감시자 개입 0회.** 원문은 §9.

### 메모리 (서버 로그)

| 모델 | 가중치 | 가중치+non-torch | 로드 | util → KV | KV 토큰 · 동시 | 기동→서버 준비 |
| --- | --- | --- | --- | --- | --- | --- |
| `seccoderx-8b` | 15.27 GiB | 16.05 GiB | 113 s | 0.20 → 7.69 GiB | 55,952 · 16,384 길이에 3.42× | 132 s |
| Qwen3-14B | 27.52 GiB | 28.27 GiB | 160 s | 0.30 → 7.37 GiB | 48,256 · 16,384 길이에 2.95× | 181 s |
| `qwen3.6-35b-a3b`(두 번째 기동) | 65.53 GiB | 68.6 GiB | 234 s | 0.64 → 6.25 GiB | 282,168 · 24,576 길이에 11.48× | 313 s |

「기동→서버 준비」는 `logs/launch.log`의 기동 시각부터 서버 로그의 `Starting vLLM server`까지다. `launch.sh`가 찍은
「healthy after」(140 · 190 · 320 s)는 그보다 7~9 s 크다 — i번째 확인을 약 (i−1)×5 s에 하고 i×5를 찍는다.
`qwen3.6-35b-a3b` 첫 기동(parser 판)은 `launch.sh` 표기로 345 s였다. 그 판은 서버 로그가 덮여 이렇게 보정할 수 없다.

감시자 로그 기준 `MemAvailable`(30초 간격)은 다음과 같다.
- 사전 113.4 GiB.
- Scout 풀 run 중 최저 **49.1 GiB**(09:08).
- `run_0002` 중 최저 36.6 GiB, `run_0003` 중 최저 **36.0 GiB**(10:59).
- 정리 뒤 115.3 GiB.

→ **Scout 풀과 Auditor는 같이 못 올린다** — 49.1 GiB 남은 자리에 util 몫 76.6 GiB가 들어가야 한다(계산값).
⚠ 정본 injection인 `deepseek-r1-distill-qwen-32b`는 **띄우지 않았다** — 그 자리의 잠정 후보 Qwen3-14B를 띄웠다
(사용자 지시 — ADR 0012 결정 4). 그래서 「정본 Scout 풀이 한 장비에 들어가는가」는 이 표로 답하지 않는다.

## 3. 실험 1 — `run_20260924_0001`: Scout 풀

### 목적
Scout 세 좌석을 실모델로 부른다. 확인할 것은 셋이다.
- v3 프롬프트·`ScoutResponse` 스키마를 통과하는가.
- 한 호출이 얼마나 걸리는가.
- 신호가 계약대로 나오는가.

### 조건
- 프로필 `atom_smoke_scout_pool` · 09:02:29~10:02:23 KST(59분 54초).
- 인덱스는 48파일 · 심볼 542 · 호출 3,045(미해결 207 · 파싱 오류 39)다.
- 대상 542 중 12를 올리고 530은 `over_candidate_max`로 넘겼다.
- 좌석은 한 번에 하나씩 불렸다 — 생성이 있던 10초 창 가운데 `Running`이 2 이상인 창은 0개다(서버 로그).

### 결과

| 좌석 · 모델 | 호출 | ok | wall 평균 / 중앙 / 최대 | 입력 토큰 중앙 / 최대 | 출력 토큰 중앙 / 최대(ok) |
| --- | --- | --- | --- | --- | --- |
| `scout_memory` · `seccoderx-8b` | 11 | 11 | 86.5 / 87.8 / 109.6 s | 7,728 / 10,757 | 1,162 / 1,427 |
| `scout_boundary` · `seccoderx-8b` | 11 | **10** | 131.2 / 107.4 / **300.0 s** | 5,181 / 8,157(측정된 10건) | 1,378 / 2,878 |
| `scout_injection` · Qwen3-14B | 10 | 10 | 105.8 / 94.7 / 195.8 s | 7,713 / 10,689 | 763 / 1,577 |

타임아웃 난 boundary 호출은 `input_tokens`가 0으로 남았다(미측정 — 응답이 없어 서버가 센 값을 못 받았다). 그래서
입력 칸에서 뺐다. wall 칸에는 넣었다(벽시계는 쟀다).

- **완주하지 못했다.** 11번째 대상(`test/example.c`)의 boundary 호출 `cand_0032`가 **300,021 ms**에
  `ModelGatewayError`로 끝나 run이 멈췄다. 12번째 대상은 부르지 못했다. 경고는 *"게이트웨이가 응답하지 않아
  중단했다: … timed out"*이고 이슈 #261이 이것을 추적한다.
- **스키마** — 완료된 31건이 전부 첫 시도에 통과했다(`attempts` 1 · `finish_reason` `stop`).
- **장부**
  - 마지막 `budget` 이벤트는 입력 257,728 · 출력 39,027토큰 · `gpu_seconds` 3,453이다. 실모델 호출은 벽시계로 청구된다
    (`model_call`의 `gpu_ms_source`가 32건 모두 `request_wall_clock`).
  - 대상 11개라 대상당 ≈314 s이고, 타임아웃 난 300 s를 포함한다(계산값).
  - `gpu_seconds` 3,453 중 1,058 s(30.6%)는 Qwen3-14B 몫이다(injection 좌석 wall 합, 계산값).
  - 기본 예산에서 먼저 닿는 것은 `gpu_seconds` 7,200이다 — 대상 ≈23개다(계산값).
- **ID·상태** — 31건 모두 `candidate_id`·`run_id`·`artifact_id`가 FACTS의 발급값과 같고 `state`가 `candidate`다.
- **`location`은 달랐다.**
  - boundary 10/10이 `symbol`·`line_start`를 비워 냈다(아래 근거 로그 — 키 자체가 없다). memory·injection 21건은
    FACTS와 같다.
  - 그 값이 `state.db`에 그대로 들어갔다. 노드가 모델의 `candidate`에 `scout_member`만 덮어 돌려주기 때문이다
    (`src/orchestrator/nodes/scout.py`의 `_ask_model`).
  - boundary v3는 *"Copy … the `location` object from `FACTS.target`, preserving null fields"*라고 시킨다
    (`prompts/scout_boundary/v3/prefix.md:138`).
- **신호(`reason_codes[0]`)와 점수** — v3는 첫 신호에 점수를 묶는다(review 0.80 · need_evidence 0.30 · reject 0.10).

| 좌석 | 신호 · 점수 | 건수 | 하네스 결과 |
| --- | --- | --- | --- |
| memory | `triage_review` 0.8 | 6 | 승격 |
| memory | `triage_reject` 0.1 | 4 | `queued` |
| memory | `triage_need_evidence` 0.3 | 1 | `queued` |
| boundary | `triage_review` 0.8 | 4 | 승격 |
| boundary | **`triage_reject` 1.0** | **2** | **승격** |
| boundary | 형식 이탈 — `triage_review@file:L115` · `triage_review@file:L154` | 2 | 승격(0.8) |
| boundary | 형식 이탈 — 신호 없이 `source_…`로 시작 | 2 | 승격(0.8) |
| injection | `triage_reject` 0.1 | 10 | `queued` |

  **하네스는 점수 임계 0.5로만 가른다.** 승격 16은 review 10 + reject 1.0 둘 + 형식 이탈 넷이고, `queued`는 15다.
  계약을 어긴 여섯이 모두 승격됐다. review 12건(형식 이탈 둘 포함)은 전부 0.8이라, 약한 review에 낮은 점수를 준 경우는
  0건이다.
- **`requested_context`** — 15건 모두 **문자열**이었다.
  - 모두 reason code 표기 `missing_request:<kind>:<target>:<purpose>`를 따른다(예 `missing_request:variable:MAXFILENAME:buffer_capacity`).
  - 좌석별로는 boundary 7응답 14건, memory 1응답 1건, injection 0건이다.
  - 스키마(`list[str]`)가 서버 grammar로 강제되므로, v3가 예시로 든 4키 객체는 나올 자리가 없었다. 스키마 거절은 0건이다.
  - 그 8응답 중 `missing_request:` reason code를 같이 낸 것은 1건(memory의 need_evidence 응답)뿐이다. 그 reason code는
    `candidate.reason_codes`에 남아 `state.db`에 저장됐다. `analysis_summary`는 노드가 버린다.
- **`conditions[].description`** — 0/46. boundary v3는 그 키를 허용하지만 스키마가 막는다.
- **줄 인용** — `code_evidence`의 줄 번호 83개가 **전부 대상의 원본 줄 범위 안**이었다.
  - 스니펫 안 위치(1부터)로 보이는 것은 0개다.
  - 83개 중 59개는 같은 프롬프트의 Capsule 호출 목록에 있는 줄 번호와 같다. 예: `cand_0001`의 147·160은 Capsule에
    `malloc …:147`·`free …:160`으로 적혀 있다. SOURCE에는 줄 번호가 없다.
  - **인용한 줄의 내용이 맞는지는 대조하지 않았다.**
- **사고 구간** — 응답 31/31의 출력 토큰에 `<think>`·`</think>`(이 두 모델 어휘의 id 151667·151668)가 있다.
  - `</think>` 위치로 센 사고 길이는 `seccoderx-8b` 중앙값 869(488~2,220)토큰 · Qwen3-14B 444(320~1,279)토큰이다.
  - 출력 합에서 사고가 차지하는 비율은 67% · 64%다.
  - 본문은 JSON뿐이다.

### 근거 로그

```
# seccoderx-8b.log — 타임아웃 난 요청. 끊기 직전까지 생성 중이었고 끊은 것은 클라이언트다
(APIServer pid=2069949) INFO 09-24 09:57:23 [request_logger.py:63] Received request chatcmpl-9c909fecfacc30b6: params: SamplingParams(…
(APIServer pid=2069949) INFO 09-24 10:02:21 [loggers.py:310] Engine 000: Avg prompt throughput: 0.0 tokens/s, Avg generation throughput: 13.2 tokens/s, Running: 1 reqs, Waiting: 0 reqs, GPU KV cache usage: 15.6%, …
(APIServer pid=2069949) INFO 09-24 10:02:23 [async_llm.py:741] Aborted request(s) chatcmpl-9c909fecfacc30b6-9b8fa220.

# seccoderx-8b.log — boundary 응답 원문의 앞부분(cand_0002). location에 symbol·line_start가 없고, reject인데 1.0이다
output: '{\n\n  "candidate": {\n    "candidate_id": "cand_0002",\n    "run_id": "run_20260924_0001",\n    "artifact_id": "art_0161",\n    "location": {\n      "file": "contrib/minizip/miniunz.c",\n      "line_end": 185\n    },\n    "suspected_cwe": [],\n    "score": 1.0,\n    "reason_codes": [\n      "triage_reject"\n    ],\n …

# run_scout_pool.log 끝
run_id: run_20260924_0001
후보 31건 / Finding 16건
완주 여부: 부분 결과
```

### 해석
- **세 좌석이 뜨고 스키마를 통과한다** — 배선이 모델에 닿는다는 첫 확인이다.
- **run을 멈춘 것은 러너 정책이다** — 호출 하나의 타임아웃을 서버 사망으로 읽고 run을 끝낸다
  (`src/orchestrator/runner.py`의 `run_pipeline`·`_is_droppable`, 이슈 #261).
  - 끊긴 호출은 그때까지 ≈3,860~4,000토큰을 냈다(계산값). 근거는 서버 로그의 KV 사용률 8.7%→15.6%와 13.4 tok/s ×
    300 s다. 상한 4,096의 약 95%다. 그 응답이 제대로 끝났을지 상한까지 폭주했을지는 모른다.
  - 성공한 31건 가운데 2,878토큰을 넘은 것은 없다. 그러니 이 run에서 벽에 닿은 호출은 1/32다.
  - 벽의 위치는 속도가 정한다. `seccoderx-8b` 13.3 tok/s면 300 s 안에 약 3,990토큰, Qwen3-14B 8.1 tok/s면 약
    2,430토큰까지 낼 수 있다(계산값, 프리필 제외).
  - `config/budgets.yaml`의 `output_max` 주석은 *"출력 길이만으로 그 벽에 닿지 않는다"*고 적는데, 그 전제는 옛 30B의
    63 tok/s였다.
- **Scout 계약 위반은 하네스를 조용히 지나간다.** 신호와 점수의 모순, 첫 코드 형식 이탈, `location` 누락이 모두
  예외 없이 저장됐고, 앞의 둘은 승격까지 됐다. 판정을 점수가 아니라 최종 신호로 하는 것은
  [ADR 0012](../../decisions/0012-harness-canon-v2.md) 결정 2다. 그 결정의 근거는 반대 방향(약한 review가 낮은
  점수로 떨어진다)이었다. 이 run에서는 그 방향이 0건이었고, 다른 방향(reject가 높은 점수로 올라간다)이 2건 나왔다.
- **따라 나오지 않는 것**
  - 탐지 정확도 — 정답 대조가 없다.
  - 좌석 간 비교 — 대상 11개 · 좌석당 10~11호출뿐이다.
  - Qwen3-14B가 10/10 reject를 낸 이유 — 모델의 성질인지 zlib 표본 탓인지는 이 run으로 못 가른다.

## 3. 실험 2 — `run_20260924_0002`: Auditor, `--reasoning-parser deepseek_r1`

### 목적
Auditor `qwen3.6-35b-a3b`를 실모델로 부른다. Scout는 `fake_v2`다.

### 조건
- 프로필 `atom_smoke_auditor` · 10:10~10:49 KST.
- 서버는 Scout와 같은 `--reasoning-parser deepseek_r1`로 띄웠다. **`xgrammar`·`disable_any_whitespace` 인자는 없었다.**
- 이 판의 인자는 작성자 실수다 — Scout 설정을 그대로 옮겼다.

### 결과
- auditor 호출 7건이 모두 `SchemaParseError`로 끝났다 — **Finding 0**이다.
  - 사유는 본문 `None`(*"JSON input should be string, bytes or bytearray … input_value=None"*) 6건과 잘린 JSON
    (*"EOF while parsing a string"*) 1건이다(trace `target_skipped.error_detail`).
  - 시도 단위로 보면 `None` 12/14 · 잘린 JSON 2/14다. 잘린 JSON 하나는 재시도 프롬프트 로그
    `prompts/0012_auditor_retry1.md`에서 셌다.
- 호출마다 2시도이고 `output_tokens`가 8,192(= 2 × 4,096)다. **14시도가 전부 상한까지 생성했다.** wall 중앙값은
  318.1 s다.
- run 경고 *"후보 3건이 연속으로 실패했다"* — 차단기로 멈췄다. 후보 7 / Finding 0 · 부분 결과다.

### 해석
- **이 모델은 사고 모드로 돈다.** 채팅 템플릿은 `enable_thinking`이 false가 아니면 생성 프롬프트 끝에 `<think>`를 연다.
  하네스는 `chat_template_kwargs`를 안 보낸다.
- **parser가 있으면 문법은 사고가 끝난 뒤에만 걸린다.** `deepseek_r1` parser는 `</think>`가 없으면 출력 전부를 사고로
  넘긴다(vLLM 0.27.1 `reasoning/basic_parsers.py`). 그러니 이 판은 이렇게 읽힌다.
  - 12/14시도는 사고가 4,096 안에 안 끝나 본문이 비었다.
  - 2/14는 사고가 끝났지만 그 뒤 JSON이 상한에서 잘렸다.
- ⚠ **이 판의 서버 로그는 회수본에 없다**(메타 표). 당시 작성자가 본 로그 두 건은 출력 텍스트가 `{"finding_id": …`로
  시작하고 `finish_reason: length`였다.
  - vLLM은 parser를 거친 뒤의 본문을 로그에 적는다. 그러니 그 둘이 위 2/14다.
  - 당시 「사고 토큰이 없다」고 본 것은 다른 어휘의 id(151668)를 찾은 착오였다. 이 모델의 `<think>`·`</think>`는
    248068·248069다(`tokenizer_config.json`).

## 3. 실험 3 — `run_20260924_0003`: Auditor, 옛 Auditor의 기동 인자

### 목적
같은 Auditor를 parser 없이, registry가 옛 Auditor(`qwen3-coder-30b-a3b`)에 적은 인자로 띄운다. 그 인자는
`--structured-outputs-config.backend xgrammar`와, 필수로 적힌 `--structured-outputs-config.disable_any_whitespace true`다
(`config/models.yaml`의 그 항목 「기동 조건」, 이슈 #172).

### 조건
프로필 `atom_smoke_auditor` · 10:57~11:13 KST(16분). Scout `fake_v2`가 12대상 × 3좌석 36후보를 전부 승격했다.
**실험 2와 인자 셋이 한꺼번에 다르다** — parser를 뺐고 `xgrammar`·`disable_any_whitespace`를 더했다.

### 결과

| 역할 · 모델 | 호출 | ok | wall 평균 / 중앙 / 최대 | 입력 토큰 중앙 / 최대 | 출력 토큰 중앙 / 최대 | 재시도 |
| --- | --- | --- | --- | --- | --- | --- |
| auditor · `qwen3.6-35b-a3b` | 36 | 36 | 22.2 / 16.2 / 171.0 s | 2,126.5 / 6,389 | 402.5 / 4,478 | 1 |
| critic · `qwen3.6-35b-a3b`(스모크 한정) | 4 | 4 | 4.8 / 4.7 / 5.0 s | 738.5 / 854 | 118 / 126 | 0 |

- **장부** — 마지막 `budget` 이벤트(`seq` 2053 · 7단계)는 입력 200,020 · 출력 23,951토큰 · `gpu_seconds` 817
  (`gpu_ms` 817,456)이다. 실모델 호출의 `gpu_ms` 합과 같다 — auditor 36건 798,455 ms + critic 4건 19,001 ms(둘 다 위
  `qwen3.6-35b-a3b` 서버, trace `model_call`). Scout 셋은 `fake_v2`라 `gpu_ms`가 없어 청구되지 않는다.
- **Auditor가 사고 없이 답했다.** parser가 없으면 vLLM이 구조화 출력 문법을 첫 토큰부터 건다(vLLM 0.27.1
  `entrypoints/openai/chat_completion/serving.py`의 `reasoning_ended = None`). 그래서 템플릿이 연 `<think>` 뒤에서도
  모델이 곧장 JSON을 냈다. 응답 41건(auditor 37시도 · critic 4)에 248068·248069가 0건이고, 첫 글자가 전부 `{`다
  (서버 로그). **사고 없는 Auditor가 판정에 무엇을 하는지는 안 쟀다.**
- **재시도 1건은 숫자 폭주였다.**
  - `find_0007`의 첫 시도가 `repro_spec.steps`의 명령 인자 안에서 `9`를 3,808자 이어 4,096에서 잘렸다
    (`finish_reason: length`).
  - 두 번째 시도는 382토큰이다(서버 로그).
  - 공백이 아니므로 `disable_any_whitespace`가 막는 종류가 아니다. 첫 시도 유효율은 35/36이다.
- **Finding 필드**
  - `path` 36/36 · `evidence_refs` 36/36(비지 않음) · `repro_spec` 30/36.
  - `trigger_status`는 전부 null이다 — v1 프롬프트가 그 필드를 묻지 않는다.
  - `confidence`는 0.85 18 · 0.8 6 · 0.75 1 · 0.7 4 · 0.6 7이다.
  - CWE는 787 ×14 · 190 ×13 · 119 ×3 · 125 ×3 · 20 · 131 · 120이다.
- **`missing_evidence`를 채운 Finding 26/36** — 모두 발급 `missing_id` 하나씩이다. 하네스가 그것을 고정 문구의
  `MissingEvidence`(`blocking=True`)로 만들었다(`src/orchestrator/nodes/auditor.py`의 `_materialize_missing`).
- **ID·상태** — 36/36의 `candidate_id`가 발급값과 같고, `state`는 전부 `hypothesized`다. `missing_evidence` ID 26개도
  전부 발급값이다.
- **6단계 게이트 술어**(`gate_eval`, 36건) — `static_ok`·`location_ok`·`path_ok`는 36/36 참이다.

| 6단계 등급 | `graph_ok` | `unblocked` | 건수 |
| --- | --- | --- | --- |
| E2 | 참 | 참 | 4 |
| E1 | **참** | **거짓** | **9** — blocking 하나만 E2를 막았다 |
| E1 | **거짓** | **참** | **6** — GRAPH 근거 부재 하나만 막았다 |
| E1 | 거짓 | 거짓 | 17 — 둘 다 |

  → blocking만 풀면 E2가 13이 되고, GRAPH 근거만 채우면 10이 된다(계산값). 둘 다 채우면 36이다. **병목은 둘이다.**
- **7단계** — `critic_top_n` 4(스모크 값)가 36건 중 4건을 골랐다. 고르는 기준은 등급이 아니라 `confidence`다.
  - 고른 넷은 0.85 동점 18건 중 넷이다 — `find_0003`(E2) · `find_0004`·`0006`·`0007`(E1).
  - 나머지 E2 셋(`find_0001` 0.8 · `find_0011` 0.6 · `find_0012` 0.75)은 confidence가 0.85보다 낮아 `critic_skipped`로 빠졌다.
  - **Critic은 넷 다 반박했다**(`state.db`의 decision 4건이 전부 `rebut`). 그중 `find_0007`의 반박은 `evidence_refs`가
    비어 근거 없는 반박이 됐다. 그래서 하네스가 그것을 uphold로 정규화했다(`gate.normalize()` — trace
    `critic_normalized`). 정책 결과는 rebutted 3 · upheld 1이다.
  - 그 `find_0007`은 경로·GRAPH 근거·재현 명세·(정규화된) 지지를 다 갖췄는데 blocking 하나 때문에 E1이다.
    `p3 = p2 ∧ upheld ∧ repro_spec`이고, `p2`가 `unblocked`를 요구한다. **E3 0.**
  - 7단계 `gate_eval`의 `location_ok`는 네 건 모두 거짓이다. 그 호출이 `candidate_location`을 안 넘기기 때문이다
    (`runner.py`에서 넘기는 자리는 6단계와 리포트 재등급 둘뿐이다). 등급에는 영향이 없다(`p1 = static_ok or location_ok`).
- **리포트**(dedupe 11건 뒤)
  - E2 4 · E1 21이다. 상태는 `hypothesized` 22 · `queued` 3(rebutted 셋)이다(`report.md`).
  - dedupe 전 `state.db`로는 `hypothesized` 33 · `queued` 3이다.

### 해석
- **이 인자로는 Auditor가 선다 — 사고 없이.** 36/36이 통과했고, 재시도 1건은 숫자 폭주였다. 다른 두 길은 안 쟀다.
  - 사고를 끄는 판 — `enable_thinking: false`로 템플릿이 사고를 닫는다. 서버 인자
    `--default-chat-template-kwargs '{"enable_thinking": false}'`로 하네스 변경 없이 된다(vLLM 0.27.1).
  - 사고를 쓰되 길이를 묶는 판 — parser를 걸고 요청에 `thinking_token_budget`(토큰 수)을 준다. 그 수에 닿으면 vLLM이
    `</think>`를 강제로 넣는다. 하네스는 이 필드를 아직 안 보낸다.
  - **등급(low·medium·high)으로는 못 고른다.** 이 모델의 템플릿이 읽는 사고 변수는 `enable_thinking` 하나다. vLLM의
    `reasoning_effort`는 여기서 켜기/끄기로만 번역된다(`"none"`이면 끄고 나머지는 전부 켠다).
- **E2 병목이 바뀌었다.**
  - 옛 로스터(Auditor `qwen3-coder-30b-a3b`)에서는 빈 `evidence_refs`·`path`가 막았다([D67](../../open-questions/D/D67.md)
    — 150건 중 `path` 150 · `evidence_refs` 148이 빔).
  - 새 Auditor에서는 둘 다 36/36 채워진다. 스키마 `required`와 서버 `disable_any_whitespace`가 다 들어간 상태다.
  - 그 대신 **해제 경로 없는 blocking**(26/36)과 **GRAPH 근거 부재**(23/36)가 막는다. 옛 로스터 실측은 Finding 150건에
    `missing_evidence` 객체 1 · 120건에 0 · 7건에 1이었다.
  - 앞의 것이 [D65](../../open-questions/D/D65.md)의 2026-09-24 절이 든 결함이다.
- **이 모델은 확인 불가를 자주 적는다** — Finding 72%다. 프롬프트 v1은 *"코드에서 확인할 수 없는 사실은 주장하지
  않는다. 대신 `missing_evidence`로 요청한다"*고 시킨다. 그런데 그 요청을 받아 풀 경로가 하네스에 없다.
  사고 없이 답한 판이라, 72%가 그 탓인지는 모른다.
- **따라 나오지 않는 것**
  - Finding이 맞는지 — 정답 대조가 없다.
  - 72%가 zlib 표본 탓인지 모델의 성질인지 사고가 막힌 탓인지.
  - Critic 판정의 질 — 정본 Critic이 아니고, Auditor와 같은 모델이다.

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| 감시자가 Scout run을 못 죽이는 상태였다(09:02~09:05) | run을 `cd … && setsid … &`로 띄워 `pids`에 중간 셸 PID(2076883)가 들어갔다 | 09:05경 실제 PGID(2076884)로 고쳤다(감시자 로그 — 09:04:53까지 2076883, 09:05:23부터 2076884). Auditor run은 `cd …; setsid nohup timeout … &` 단순 명령으로 띄워 PID=PGID=SID |
| Auditor Finding 0(`run_0002`) | 사고 모드에서 사고가 `output_max` 4,096 안에 안 끝났다 — parser가 그것을 전부 사고로 넘겼다 | parser를 빼고 `xgrammar`·`disable_any_whitespace`로 다시 띄웠다(`run_0003`) — 대가는 사고 없는 Auditor다 |
| Scout run이 11번째 대상에서 멈췄다 | 호출 하나가 `_TIMEOUT_S` 300 s에 걸리면 러너가 서버 사망으로 읽는다 | 스모크에서는 안 고쳤다 — 이슈 #261 |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| `deepseek-r1-distill-qwen-32b`(정본 injection) | 사용자 지시로 잠정 후보 Qwen3-14B를 대신 띄웠다 | 같은 `launch.sh`로 8007에 띄운다 |
| `checksum` | 셋 다 다운로드만 했다 — `tools/model_checksum.py`를 안 돌렸다 | 그 도구를 돌려 registry에 채운다. 그 전의 run은 전부 비교표 밖이다 |
| Auditor 사고 모드 대조 | parser 판은 사고가 상한을 넘었고, parser 없는 판은 문법이 사고를 막았다 | `--default-chat-template-kwargs '{"enable_thinking": false}'`로 사고를 끈 판과, parser + `thinking_token_budget`으로 사고 길이를 묶은 판 |
| parser 없는 `seccoderx-8b`·Qwen3-14B | 한 판만 띄웠다 | 같은 대상으로 parser를 빼고 한 번. 사고가 막히면 속도·판정이 둘 다 바뀔 수 있다 |
| `disable_any_whitespace` 없는 `qwen3.6-35b-a3b` | 필수로 적힌 조건을 그대로 따랐다 — parser 판은 셋이 함께 달라 대조가 안 된다 | parser 없이 이 인자만 빼고 한 판 |
| eager를 끈 속도 | 전부 `--enforce-eager`였다 | CUDA graph를 켜고 같은 요청으로 잰다 |
| 탐지 정확도 | 정답이 없는 zlib이고 후보 12다 | 평가셋 run(Phase 9) |
| 정본 Critic(`gpt-oss-20b`)과 Auditor 동시 상주 | 스모크 프로필이 80 GiB cgroup 전제로 뺐다 — 그 전제는 GPU 할당에 맞지 않는다(§2) | Auditor 옆에 util을 낮춰 띄운 판 |
| 좌석 간 겹침·공동오답 | 대상 11개 | 대상 수를 늘린 run |

## 6. 반증 조건

- **Scout 셋이 스키마를 못 넘었다면** — v3 프롬프트와 `ScoutResponse`의 서버 grammar가 충돌한다는 뜻이다. 31/31이 넘었으므로
  배선과 grammar는 선다. 남은 어긋남은 [D82](../../open-questions/D/D82.md)가 드는 것처럼 **조용한 쪽**이다(문자열로 접힌
  요청, 모순된 신호).
- **parser 판에서 사고가 상한 안에 끝났다면** — 본문 `None`이 parser 탓이 아니라 문법이나 모델 탓이다. 14시도가 전부
  4,096까지 갔고 그중 둘만 본문을 가졌으므로, 사고 길이가 원인이다. 다만 parser 판과 parser 없는 판은 인자 셋이 한꺼번에
  달랐다 — `xgrammar`·`disable_any_whitespace`가 각각 무엇을 했는지는 가르지 않았다.
- **E1 32건이 전부 blocking 하나로 설명됐다면** — blocking 규칙만 고치면 E2가 32 늘어난다. 실제로는 9다(계산 — §3 실험 3 표).

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 1 | 호출 하나의 타임아웃이 run을 멈춘다 | 이슈 #261(선택지 다섯) |
| 1 | 해제 경로 없는 blocking이 새 Auditor의 E2 병목 둘 중 하나다 | [D65](../../open-questions/D/D65.md) — 이 보고서 날짜의 실측 절 |
| 1 | Scout 풀이 기본 `gpu_seconds` 예산을 대상 ≈23개에 다 쓴다 | [D24](../../open-questions/D/D24.md) — 이 보고서 날짜의 절. 40샘플 run의 예산을 정하기 전에 본다 |
| 1 | Auditor가 사고 없이 답한다 — 사고를 쓸지 정해야 한다 | 미등재. 길은 셋이다: 지금 그대로(문법이 사고를 막음) · 서버 인자로 `enable_thinking: false`(템플릿이 사고를 닫음, 하네스 변경 없음) · parser + `thinking_token_budget`(사고를 쓰되 길이를 묶음, 하네스가 그 필드를 보내야 함). §5 「Auditor 사고 모드 대조」 |
| 2 | Scout 계약 위반이 조용히 저장·승격된다 | [D82](../../open-questions/D/D82.md)(신호·점수 — 닫는 조건 ⑤ · `requested_context`) · [D83](../../open-questions/D/D83.md)(`location`) (⏵ 2026-09-29 — D83은 `location`을 도장 목록에 안 넣고 닫혔다. 이 누출은 [D82](../../open-questions/D/D82.md)의 닫히는 조건 ⑥으로 옮겼다) |
| 2 | 기동 조건을 registry에 적는다 | `config/models.yaml`의 `seccoderx-8b`·`qwen3.6-35b-a3b` 「기동 조건」과 `config/budgets.yaml`의 `output_max` 주석 — 이 보고서와 짝인 설정 PR |
| 3 | Qwen3-14B 키 편입 | ADR 0012 승인 뒤 — 지금은 스모크 브랜치에만 있다 (⏵ 2026-09-25 — 승인 전인 09-24에 PR #266이 이미 `qwen3-14b` 키로 편입했다) |

## 8. 정리 상태

- 서빙 서버: 8008 · 8009 · 8010 전부 내렸다(11:15 KST, `stop.sh` — TERM 뒤 남은 것 0). 감시자는 `STOP_GUARD`로 끝냈다.
  정리 뒤 vllm·run·감시자 프로세스 0, `MemAvailable` 115.3 GiB, cgroup anon 0.1 GiB.
- 표지 파일 `~/smoke-0924/SMOKE_RUNNING` → `SMOKE_DONE`.
- 임시 설정·프로필: 스모크 브랜치(`exp/atom-roster-smoke`, origin에 있음, PR 없음)에만 있다. main은 안 건드렸다.
- ATOM에 남긴 것: `~/smoke-0924/`(스크립트·로그·워크트리 `wt`·산출물). `~/whitehat-boss`에 detached 워크트리 하나가
  등록돼 있다.

## 9. 부록 — 재현 절차

**설정** — `git diff c301342 fdbc465 -- config/ PROJECT-CONTEXT.md`(4파일 · +90 −2).
- `config/models.yaml`에 `qwen3-14b` 키: `http://127.0.0.1:8010/v1` · `context_limit` 40960 · bf16 · `checksum` 0 ·
  `source` `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18` · family qwen · 프롬프트 v3.
- `config/profiles.yaml`에 위 두 프로필.
- `config/budgets.yaml` 전역 `candidate_max` 12 · `critic_top_n` 4. 예산 스키마가 이 둘을 못 받아 전역값을 바꿨다.

**ATOM 준비** — `~/whitehat-boss`에서 `fdbc465`을 detached 워크트리 `~/smoke-0924/wt`로 뽑았다. venv는 `~/venv-probe`
(vLLM 0.27.1)다.

**감시자·기동·run·정리 — 실제로 친 명령**

```bash
MAX_HOURS=5 setsid nohup bash ~/smoke-0924/guard.sh > /dev/null 2>&1 < /dev/null &

bash ~/smoke-0924/launch.sh seccoderx-8b 8009 0.20 16384 4 SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model --revision 25d7f38146bdf0fad1eac09c586b1993ef3e6ee4 --reasoning-parser deepseek_r1
bash ~/smoke-0924/launch.sh qwen3-14b 8010 0.30 16384 4 Qwen/Qwen3-14B --revision 40c069824f4251a91eefaf281ebe4c544efd3e18 --reasoning-parser deepseek_r1
cd ~/smoke-0924/wt && setsid nohup timeout 5400 ~/venv-probe/bin/python -m src.cli run --profile atom_smoke_scout_pool --target /srv/tenants/user/targets/zlib --device atom --condition B3prime@pipeline > ~/smoke-0924/logs/run_scout_pool.log 2>&1 < /dev/null &
bash ~/smoke-0924/stop.sh

# run_0002 — parser를 건 판(Finding 0)
bash ~/smoke-0924/launch.sh qwen3.6-35b-a3b 8008 0.64 24576 2 Qwen/Qwen3.6-35B-A3B --revision 995ad96eacd98c81ed38be0c5b274b04031597b0 --reasoning-parser deepseek_r1
cd ~/smoke-0924/wt; setsid nohup timeout 5400 ~/venv-probe/bin/python -m src.cli run --profile atom_smoke_auditor --target /srv/tenants/user/targets/zlib --device atom --condition B3prime@pipeline > ~/smoke-0924/logs/run_auditor.log 2>&1 < /dev/null &
bash ~/smoke-0924/stop.sh

# run_0003 — parser 없이 옛 Auditor의 기동 인자
bash ~/smoke-0924/launch.sh qwen3.6-35b-a3b 8008 0.64 24576 2 Qwen/Qwen3.6-35B-A3B --revision 995ad96eacd98c81ed38be0c5b274b04031597b0 --structured-outputs-config.backend xgrammar --structured-outputs-config.disable_any_whitespace true
cd ~/smoke-0924/wt; setsid nohup timeout 5400 ~/venv-probe/bin/python -m src.cli run --profile atom_smoke_auditor --target /srv/tenants/user/targets/zlib --device atom --condition B3prime@pipeline > ~/smoke-0924/logs/run_auditor2.log 2>&1 < /dev/null &
bash ~/smoke-0924/stop.sh
touch ~/smoke-0924/STOP_GUARD
```

첫 run 명령의 `&&`가 §4 첫 행의 사고다 — 따라 할 때는 `;`로 띄운다.

**`launch.sh`**

```bash
#!/usr/bin/env bash
# vLLM 서버 하나를 안전 조건을 확인한 뒤 띄운다 — 감시자(guard.sh)가 떠 있어야 하고, 포트가 비어
# 있어야 하고, GPU를 남이 쓰고 있지 않아야 하고, MemAvailable이 이 서버 몫 + 여유 16 GiB보다 커야 한다.
# (포트·GPU 점유 확인과 /v1/models id 대조는 tools/probe/atom.sh의 관행을 따른다.)
#
# 사용: launch.sh NAME PORT UTIL MAXLEN MAXSEQS HF_ID [vllm 추가 인자...]
#   NAME = registry 키 (하네스가 요청의 model로 보내는 이름 — src/adapters/factory.py)
#   UTIL = --gpu-memory-utilization (장비 전체 119.7 GiB에 대한 비율)
set -euo pipefail
D="$HOME/smoke-0924"
V="$HOME/venv-probe/bin"
TOTAL_GIB=119.7
MARGIN_GIB=16
NAME=$1 PORT=$2 UTIL=$3 MAXLEN=$4 SEQS=$5 MODEL=$6
shift 6

pgrep -f "$D/guard.sh" > /dev/null || { echo "ABORT: guard.sh가 안 떠 있다"; exit 2; }
if ss -ltnH "sport = :$PORT" | grep -q .; then echo "ABORT: 포트 $PORT 사용 중"; exit 2; fi
foreign=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' ' | grep -v -x -F -f <(for pg in $(cat "$D/pids" 2>/dev/null); do pgrep -g "$pg"; done; echo 0) || true)
if [ -n "$foreign" ]; then echo "ABORT: 이 스모크 밖의 GPU 프로세스 있음: $foreign"; exit 2; fi
need_kb=$(awk -v u="$UTIL" -v t="$TOTAL_GIB" -v m="$MARGIN_GIB" 'BEGIN{printf "%d", (u*t+m)*1048576}')
avail_kb=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)
if [ "$avail_kb" -le "$need_kb" ]; then
  awk -v a="$avail_kb" -v n="$need_kb" 'BEGIN{printf "ABORT: MemAvailable %.1f GiB <= 필요 %.1f GiB\n", a/1048576, n/1048576}'
  exit 2
fi

export VLLM_USE_DEEP_GEMM=0 VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_HOST_IP=127.0.0.1 NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo
LOGF="$D/logs/$NAME.log"
echo "$(date -Is) launch $NAME port=$PORT util=$UTIL maxlen=$MAXLEN seqs=$SEQS model=$MODEL extra=[$*]" | tee -a "$D/logs/launch.log"
setsid nohup "$V/vllm" serve "$MODEL" --served-model-name "$NAME" --host 127.0.0.1 --port "$PORT" \
  --gpu-memory-utilization "$UTIL" --max-model-len "$MAXLEN" --max-num-seqs "$SEQS" --enforce-eager \
  --enable-log-requests --enable-log-outputs --max-log-len 6000 "$@" > "$LOGF" 2>&1 < /dev/null &
PID=$!
echo "$PID" >> "$D/pids"
echo "started $NAME pid=pgid=$PID log=$LOGF"

for i in $(seq 1 240); do   # 240 x 5s = 20분
  if curl -s -m 3 "http://127.0.0.1:$PORT/v1/models" | grep -q "\"id\":\"$NAME\""; then
    echo "healthy after $((i * 5))s"
    break
  fi
  if ! kill -0 "$PID" 2> /dev/null; then echo "DIED — log tail:"; tail -25 "$LOGF"; exit 3; fi
  if [ -f "$D/KILLED" ]; then echo "guard killed: $(cat "$D/KILLED")"; exit 4; fi
  sleep 5
done
curl -s -m 3 "http://127.0.0.1:$PORT/v1/models" | grep -q "\"id\":\"$NAME\"" || { echo "TIMEOUT: 20분 안에 안 떴다"; exit 5; }
grep -h -E "Model loading took|Available KV cache|GPU KV cache size|Maximum concurrency|Actual usage" "$LOGF" | cut -c1-260 | tail -5
awk '/^MemAvailable:/{printf "MemAvailable %.1f GiB\n", $2/1048576}' /proc/meminfo
awk '$1=="anon"{printf "cgroup anon %.1f GiB\n", $2/1073741824}' /sys/fs/cgroup/user.slice/user-1004.slice/memory.stat
```

**`guard.sh`**

```bash
#!/usr/bin/env bash
# ATOM 스모크 감시자 — 이 스모크가 띄운 프로세스 그룹(pids 파일)만 죽인다.
# GB10은 GPU·CPU가 한 메모리(119.7 GiB)를 나눠 쓰고, vLLM의 GPU 할당은 user cgroup(80 GiB)이
# 아니라 장비 전체의 MemAvailable을 깎는다 — 그래서 두 층을 다 본다.
#   1) 장비: /proc/meminfo MemAvailable < MIN_AVAIL_GIB  → 종료 (장비 전체가 굶는 것을 막는다)
#   2) cgroup: user-1004.slice memory.stat anon > MAX_ANON_GIB → 종료 (80 GiB 상한의 무경고 OOM kill 전에)
#   3) 마감: 감시자 시작 후 MAX_HOURS 시간 → 종료 (서버가 남아 자원을 붙잡지 않게)
# 멈춤: touch $D/STOP_GUARD (죽이지 않고 감시만 끝낸다)
set -u
D="$HOME/smoke-0924"
PIDS="$D/pids"
LOG="$D/logs/guard.log"
CG=/sys/fs/cgroup/user.slice/user-1004.slice
MIN_AVAIL_GIB="${MIN_AVAIL_GIB:-12}"
MAX_ANON_GIB="${MAX_ANON_GIB:-72}"
MAX_HOURS="${MAX_HOURS:-4}"
DEADLINE=$(( $(date +%s) + MAX_HOURS * 3600 ))
touch "$PIDS"
rm -f "$D/STOP_GUARD"

kill_all() {
  echo "$(date -Is) KILL reason=$1" | tee -a "$LOG" > "$D/KILLED"
  while read -r pg; do
    [ -n "$pg" ] && kill -TERM -- "-$pg" 2>/dev/null && echo "$(date -Is) TERM -$pg" >> "$LOG"
  done < "$PIDS"
  sleep 20
  while read -r pg; do
    [ -n "$pg" ] && kill -KILL -- "-$pg" 2>/dev/null && echo "$(date -Is) KILL -$pg" >> "$LOG"
  done < "$PIDS"
}

echo "$(date -Is) guard start min_avail=${MIN_AVAIL_GIB}GiB max_anon=${MAX_ANON_GIB}GiB deadline=$(date -d @$DEADLINE -Is)" >> "$LOG"
n=0
while :; do
  avail_kb=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)
  anon_b=$(awk '$1=="anon"{print $2}' "$CG/memory.stat")
  cur_b=$(cat "$CG/memory.current")
  if [ $((n % 6)) -eq 0 ]; then
    awk -v t="$(date -Is)" -v a="$avail_kb" -v an="$anon_b" -v c="$cur_b" -v p="$(tr '\n' ' ' < "$PIDS")" \
      'BEGIN{printf "%s avail=%.1fGiB cg_anon=%.1fGiB cg_cur=%.1fGiB pgids=[%s]\n", t, a/1048576, an/1073741824, c/1073741824, p}' >> "$LOG"
  fi
  if [ "$avail_kb" -lt $((MIN_AVAIL_GIB * 1048576)) ]; then kill_all "MemAvailable<${MIN_AVAIL_GIB}GiB"; exit 0; fi
  if [ "$anon_b" -gt $((MAX_ANON_GIB * 1073741824)) ]; then kill_all "cgroup_anon>${MAX_ANON_GIB}GiB"; exit 0; fi
  if [ "$(date +%s)" -gt "$DEADLINE" ]; then kill_all "deadline_${MAX_HOURS}h"; exit 0; fi
  if [ -f "$D/STOP_GUARD" ]; then echo "$(date -Is) guard stop requested" >> "$LOG"; exit 0; fi
  n=$((n + 1))
  sleep 5
done
```

**`stop.sh`**

```bash
#!/usr/bin/env bash
# 이 스모크가 띄운 프로세스 그룹(pids 파일)만 내린다 — TERM, 20초 뒤 남은 것 KILL.
# 감시자는 건드리지 않는다(감시 종료는 touch ~/smoke-0924/STOP_GUARD).
set -u
D="$HOME/smoke-0924"
[ -s "$D/pids" ] || { echo "내릴 것 없음"; exit 0; }
while read -r pg; do [ -n "$pg" ] && kill -TERM -- "-$pg" 2> /dev/null && echo "TERM -$pg"; done < "$D/pids"
sleep 20
while read -r pg; do [ -n "$pg" ] && kill -KILL -- "-$pg" 2> /dev/null && echo "KILL -$pg"; done < "$D/pids"
echo "$(date -Is) stop.sh cleared: $(tr '\n' ' ' < "$D/pids")" >> "$D/logs/launch.log"
: > "$D/pids"
left=$(pgrep -af "vllm serve" || true)
[ -n "$left" ] && echo "남은 vllm: $left" || echo "vllm 없음"
awk '/^MemAvailable:/{printf "MemAvailable %.1f GiB\n", $2/1048576}' /proc/meminfo
```

**수치를 다시 뽑는 법**
- run 산출물의 `trace.jsonl`에서 `model_call`(호출·wall·토큰)·`gate_eval`(6·7단계 술어)·`target_planned`·`critic_skipped`·
  `critic_normalized`·`budget`을 읽는다.
- `state.db`의 `objects`(candidate·finding·missing_evidence·decision)도 쓴다.
- 서버 로그에서는 두 가지를 읽는다.
  - `Generated response … output: …, output_token_ids: […]` — 응답 원문과 사고 토큰. 사고 토큰 id는 모델 어휘마다 다르다.
  - `Avg generation throughput … Running: 1 reqs` — 속도.
- 프롬프트 로그 `prompts/NNNN_<역할>.md`의 FACTS(발급 ID·대상 범위)와 대조한다.
