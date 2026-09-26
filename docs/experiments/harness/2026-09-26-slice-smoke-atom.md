# 슬라이스 입력 실서빙 스모크 — Auditor · Scout 풀 — ATOM

> **왜 돌렸나.** [ADR 0012](../../decisions/0012-harness-canon-v2.md) 결정 1·1-b가 슬라이스를 Scout의 코드 입력으로,
> 슬라이스가 걸친 함수의 원본을 Auditor 입력으로 정했고 Phase 3이 그것을 배선했다(PR #283~#286·#288·#289).
> **그 경로를 실모델로 한 번도 안 돌렸다** — [2026-09-24 스모크](2026-09-24-roster-smoke-atom.md)는 슬라이스 없이
> zlib 후보 12개로 로스터가 뜨는지만 봤다. 그래서 둘이 미실측이었다: ① 실 Scout이 슬라이스 렌더를 받아 내는 출력
> ② 1-b가 넓힌 Auditor 원문의 **실제 토큰 수**(PR #289가 적은 ≈14,900은 글자수 ÷ 비였다 — 계산값).
> **탐지 정확도를 잰 것이 아니다.**
>
> ⚠ **이 run의 Scout 서빙 값은 운영 값이 아니다** — `--max-model-len 16384`는 2026-09-24와 축을 하나만
> 움직이려고 그대로 쓴 값이고, registry의 `context_limit`은 40,960이다. **그 어긋남을 하네스가 못 봤다**는
> 것이 이 run이 낸 진짜 결함이다([D87](../../open-questions/D/D87.md)). §4의 거절 수는 그 잘못된 창의
> 성질이지 파이프라인의 성질이 아니다.
>
> **비교표 밖이다** — 실서빙 키의 `checksum`이 전부 0이다([CLAUDE.md](../../../CLAUDE.md) §0). run은 머지하지 않는
> 브랜치에서 돌았다 — `exp/atom-slice-smoke` @ `41f802a`이고, main `e54661a`에 PR #289·#290과 스모크 설정을 얹었다(§9).

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-26 00:21 KST`(마지막 run `run_20260925_0006` 종료 · 매니페스트 `2026-09-25T15:21:30Z`). 서버 정리 00:23 |
| 실제 서빙 모델 | `hf://Qwen/Qwen3.6-35B-A3B@995ad96eacd98c81ed38be0c5b274b04031597b0`(별칭 `qwen3.6-35b-a3b`) · `hf://SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model@25d7f38146bdf0fad1eac09c586b1993ef3e6ee4`(별칭 `seccoderx-8b`) · `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18`(별칭 `qwen3-14b`) |
| `repo_commit` | `41f802af25a115a67ae006b20ecd9568e9d89f4f`(여섯 run 매니페스트 모두 `repo_dirty: false`) |
| `run_id` | `run_20260925_0001`(Fake 예행) · `0002`·`0003`·`0004`(Auditor 실서빙) · `0005`·`0006`(Scout 풀 실서빙) |
| run 산출물 경로·보존 여부 | ATOM `~/slice-smoke/artifacts/run_20260925_000{1..6}` — 지우지 않았다. 저장소에는 없다 |
| `tool_runner` | 프로필 선언 `docker` / 실제 `docker`(오버라이드 없음) |
| `tool_images` digest | `harness-tools:v1` = `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17` |
| `--gpu-memory-utilization` | `qwen3.6-35b-a3b` 0.64 · `seccoderx-8b` 0.20 · `qwen3-14b` 0.30 (2026-09-24와 같다) |
| 동시에 떠 있던 다른 서버 | `0002`~`0004` 동안: 8002 `qwen3.6-35b-a3b` 하나. `0005`·`0006` 동안: 8000 `seccoderx-8b` + 8001 `qwen3-14b` 둘. 기동 전 GPU 연산 프로세스·vllm·joern이 전부 0이었다 |
| 서빙 서버 stdout | ATOM `~/slice-smoke/logs/<별칭>.<시각>.log`(`--enable-log-requests --enable-log-outputs`). **덮어쓰지 않는다** — 2026-09-24에 로그 하나를 그렇게 잃어 파일명에 시각을 넣었다 |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| **1-b가 입력 몫에 드는가** | **든다 — 초과 0건.** Auditor 실호출 147건에서 서버가 센 `input_tokens`가 최대 **15,437**이고 입력 몫 20,480에 여유 **5,043토큰**이다 |
| ⚠ **글자 상한은 토큰을 안 묶는다** | 글자/토큰 비가 **1.22~3.32**로 벌어졌고(2026-09-24 zlib의 2.51~2.97보다 넓다), **글자 최대 프롬프트가 토큰 최대가 아니다** — 37,081자가 13,944토큰인데 20,917자가 15,437토큰이다 |
| 1-b가 실제로 넓혔는가 | **넓혔다** — `auditor_source` 147건 중 **103건이 함수 둘 이상**에 걸쳤다(최대 25). 넓히기 전이면 그중 하나만 봤다 |
| 상한이 실제로 무는가 | 문다 — 5개 슬라이스에서 함수 249개가 빠졌다. `SOURCE` 최대는 14,995자(상한 15,000) |
| ⚠ **Scout를 운영 값보다 작게 띄웠다** | **이 run의 설계 결함이다.** Scout `context_limit`은 40,960인데 16,384로 띄웠다(2026-09-24와 조건을 맞춘 값이고 운영 값이 아니다). 그래서 33건 중 **12건이 400으로 거절**됐고 한 run은 **9/9 거절로 후보 0**이었다. **하네스가 그 어긋남을 한 마디도 안 했다** — 그것이 [D87](../../open-questions/D/D87.md)이다 |
| 40,960으로 띄웠으면 | 거절의 대부분이 없어진다 — 265건 중 13~27건에서 **3~5건**으로 [계산값 · 글자 환산]. 창을 올려도 **메모리는 안 는다**(KV는 util이 정한다) |
| 그래도 남는 원인 | 슬라이스 렌더가 아니다 — 거절된 프롬프트 50,949자의 절별이 FACTS 13,081 · Capsule 8,621 · **렌더 2,212** · 고정 접두 ≈27,000이다 |
| Scout 스키마 | **21/21이 첫 시도에 통과했다**(`attempts` 전부 1). 2026-09-24의 31/31에 이어진다 |
| 속도 | Auditor 호출 wall 중앙 15.7 s · 최대 310.9 s. Scout 좌석 wall 중앙 84.6~103.9 s · 최대 223.7 s |
| 타임아웃 | **기본 300 s였으면 Auditor run이 멈췄다** — 실패한 호출 하나가 310.9 s였다. 이 스모크는 1,200/900 s를 선언해 넘겼다(이슈 #261) |
| 메모리 | 2026-09-24와 같다 — Scout 둘 공존 시 `MemAvailable` 최저 **49.4 GiB**, Auditor 단독 **37.0 GiB** |
| 감시자 | **개입 0회** |

## 2. 실험 환경

- **장비** — ATOM `atom-host`. GB10 · 통합메모리 119.7 GiB · 사용자 cgroup `memory.max` 80 GiB.
  vLLM 0.27.1(`~/venv-probe`, Python 3.12).
- **입력** — 표본 셋 v2 슬라이스 **241개**(`sample_01` 68 · `sample_02` 30 · `sample_03` 143).
  2026-09-14~15 슬라이서 산출(`slice_version: slice-v2`, joern 4.0.605).
  - **재료를 대조했다.** 표본 저장소 셋은 ATOM에 있던 것(`slicer-smoke-20260914-512ab693/inputs/`)과
    **바이트가 같다** — 파일별 sha256을 정렬해 집계한 값이 양쪽 일치(36 · 18 · 534 파일).
  - v2 슬라이스 JSON은 ATOM에서 지워져 있어 작성자 장비의 사본을 올렸다. 올린 뒤 양쪽에서 `slices` 배열의
    정규화 JSON 해시를 대조해 일치를 확인했다.
- **서빙 인자는 2026-09-24와 같다**(축을 하나만 움직이려고).
  - Auditor: `--max-model-len 24576` · `--max-num-seqs 2` · parser 없이 `--structured-outputs-config.backend xgrammar`
    `--structured-outputs-config.disable_any_whitespace true`. ⚠ **이 인자의 Auditor는 사고 없이 답한다**
    (`config/models.yaml`의 그 항목). 사고를 켠 구성은 이 스모크도 안 쟀다.
  - Scout: `--max-model-len 16384` · `--max-num-seqs 4` · `--reasoning-parser deepseek_r1`.
- **스모크 설정**(브랜치 `exp/atom-slice-smoke` @ `41f802a`, §9)
  - 프로필 둘 — `atom_slice_auditor`(Auditor만 실서빙, Scout·Critic Fake)와
    `atom_slice_scout_pool`(Scout 풀만 실서빙, Auditor·Critic Fake).
  - 실서빙 키 셋에 `request_timeout_s`(Scout 1,200 · Auditor 900). 기본 300 s로는 멈춘다.
  - 임계값은 안 바꿨다 — `scout_threshold` 0.5 · `candidate_max` 256 그대로다.
- **안전장치** — 감시자가 5초마다 장비 `MemAvailable` < 12 GiB · cgroup anon > 72 GiB · 마감을 보고,
  걸리면 이 스모크가 띄운 프로세스 그룹만 TERM→KILL한다. **개입 0회.** 기동 전에 포트·GPU 점유·여유
  메모리를 확인하고, 끝난 뒤 장비를 비웠다(GPU 프로세스 0 · `MemAvailable` 115.4 GiB).

### 메모리 (서버 로그)

| 모델 | 가중치 | 로드 | util → KV | KV 토큰 | 기동→준비 |
| --- | --- | --- | --- | --- | --- |
| `qwen3.6-35b-a3b` | 65.53 GiB | 230 s | 0.64 → 6.15 GiB | 277,617 | 310 s |
| `seccoderx-8b` | 15.27 GiB | 109 s | 0.20 → 7.49 GiB | 54,544 | 140 s |
| `qwen3-14b` | 27.52 GiB | 163 s | 0.30 → 7.51 GiB | 49,232 | 190 s |

감시자 로그의 `MemAvailable`은 사전 115.4 GiB · Auditor 단독 최저 37.0 GiB · Scout 둘 공존 최저 49.4 GiB ·
정리 뒤 115.4 GiB다. 2026-09-24의 36.0 / 49.1 GiB와 같은 자리다.

## 3. 실험 1 — Auditor(1-b) 실토큰

프로필 `atom_slice_auditor` · `run_20260925_0002`(sample_02 · 17:44~17:58 KST) ·
`0003`(sample_03 · 22:42~23:02) · `0004`(sample_01 · 23:02~23:21).
Scout가 Fake라 그 점수가 `scout_threshold` 0.5를 넘은 **147개**가 5단계에 올랐다(241개 중).

| 항목 | 값 | 비고 |
| --- | --- | --- |
| Auditor 실호출 | 147 (실패 1) | |
| 입력 몫 | 20,480토큰 | `--max-model-len` 24,576 − `output_max` 4,096 |
| `input_tokens` | min 1,128 · p50 3,277 · p90 8,977 · **max 15,437** | 서버가 센 값 |
| **입력 몫 초과** | **0건 · 여유 5,043토큰** | |
| 프롬프트 글자 | min 2,611 · p50 8,131 · max 37,081 | 프롬프트 파일 |
| 글자/토큰 비 | **1.22 ~ 3.32** (p50 2.52) | |
| 호출 wall | p50 15.7 s · p90 23.4 s · max 310.9 s | trace `wall_ms` |
| `output_tokens` | p50 382 · max 8,192 | 8,192 = 2시도 × 상한 |

**`auditor_source` 147건**

| 항목 | 값 |
| --- | --- |
| `SOURCE` 글자 | p50 2,364 · max 14,995 (상한 15,000) |
| 함수 수 | p50 3 · max 25 · **둘 이상 103 / 147** |
| 상한이 문 슬라이스 | 5 · 뺀 함수 합 249 |
| 어느 함수에도 안 든 줄 | 슬라이스 8 · 줄 215 |

### ⚠ 결론은 맞고 논거가 틀렸다

PR #289는 *"가장 낮은 비 2.51로 셈하면 ≈14,900토큰"*이라 적었다. 그 2.51은 zlib 코드에서 잰 값이고
슬라이스 프롬프트에 안 맞았다.

| | 글자 | 토큰 | 비 |
| --- | --- | --- | --- |
| **글자가 가장 큰 호출** | 37,081 | 13,944 | 2.66 |
| **토큰이 가장 큰 호출** | 20,917 | **15,437** | 1.35 |
| 비가 가장 낮은 호출 | 4,219 | 3,447 | 1.22 |

**글자가 가장 큰 프롬프트가 토큰이 가장 큰 프롬프트가 아니다.** 그래서 「15,000자가 몇 토큰인가」로는
못 잡는다. 이 상한이 실제로 하는 일은 **폭주 차단**이다 — 상한이 없으면 `SOURCE`가 최대 412,554자였다
(표본 셋, 2026-09-25 로컬 실측). **든다는 근거는 이 147호출이지 글자에서 토큰으로 가는 환산이 아니다.**
`_AUDITOR_SOURCE_CHARS` 주석과 ADR 0012 결정 1-b 문단을 그렇게 고쳤다(PR #289 `ce0d138`).

### 실패 1건

한 호출이 두 시도를 다 쓰고 `SchemaParseError`로 끝났다 — `output_tokens` 8,192(= 2 × 상한 4,096)이고
JSON이 문자열 한가운데서 잘렸다(`EOF while parsing a string`). **그 호출이 310.9 s였다** — 기본 요청
타임아웃 300 s였으면 run 전체가 멈췄을 자리다(이슈 #261). 후보 30건에 Finding 29건이 된 이유가 이것이다.

## 4. 실험 2 — Scout 풀에 슬라이스

프로필 `atom_slice_scout_pool` · `run_20260925_0005`(sample_02에서 고른 8개 · 23:46~00:21 KST) ·
`0006`(sample_03에서 고른 3개 · 00:21, 9초).

⚠ **먼저: 이 절의 서빙 값이 틀렸다.** Scout `context_limit`은 40,960인데 16,384로 띄웠다. 아래 거절 수는 그 창의 성질이고, 운영 값으로 띄우면 대부분 없어진다(위 §1 표). **이 절이 실제로 증명하는 것은 「하네스가 그 어긋남을 모른다」다.**

**부분집합을 쓴 이유** — 좌석 호출이 중앙값 85~104초라 241개 × 3좌석이면 계산값으로 20시간이 넘는다.
`0005`는 렌더 크기를 고르게 훑도록 8개를, `0006`은 서빙 컨텍스트를 넘을 것 둘과 여유 하나를 골랐다.

| run | 대상 | 호출 | 거절 | 후보 | Finding |
| --- | --- | --- | --- | --- | --- |
| `0005` | 8 | 24 | **3**(한 대상의 좌석 셋 전부) | 21 | 9 |
| `0006` | 3 | 9 | **9 (전부)** | **0** | **0** |

**좌석별**(`0005`)

| 좌석 | 모델 | `input_tokens` max | `output_tokens` p50 | wall p50 | wall max |
| --- | --- | --- | --- | --- | --- |
| `scout_boundary` | `seccoderx-8b` | 8,674 | 1,118 | 84.6 s | 147.2 s |
| `scout_injection` | `qwen3-14b` | 11,206 | 715 | 90.9 s | 120.0 s |
| `scout_memory` | `qwen3-14b` | 11,274 | 787 | 103.9 s | 223.7 s |

- **스키마 21/21이 첫 시도에 통과했다**(`attempts` 전부 1).
- **같은 슬라이스인데 좌석마다 `input_tokens`가 다르다**(예: 5,739 · 8,271 · 8,339) — 프롬프트 접두가
  좌석마다 달라서다. 좌석별 글자/토큰 비는 3.28~4.23이다(Auditor의 1.22~3.32와 다르다 — 토크나이저가 다르다).
- 성공한 호출의 최대 `input_tokens` 11,274는 입력 몫 12,288에 붙어 있다.

### 거절 — [D87](../../open-questions/D/D87.md)

서버 문구는 12건 모두 같다.

> `This model's maximum context length is 16384 tokens. However, you requested 4096 output tokens
> and your prompt contains at least 12289 input tokens, for a total of at least 16385`

→ 입력 몫은 16,384 − `output_max` 4,096 = **12,288토큰**이다.

**원인이 슬라이스 렌더 하나가 아니다.** 거절된 호출 하나(`0006` · `slice_0038` · `scout_memory` · 50,949자)를
절별로 세면 이렇다.

| 절 | 글자 |
| --- | --- |
| `## FACTS` | 13,081 |
| `## Capsule` | 8,621 |
| `## Source`(슬라이스 렌더) | **2,212** |
| 나머지(고정 접두 — 역할·규칙·출력 계약) | ≈27,000 |

같은 슬라이스의 `scout_boundary` 프롬프트는 40,075자다(접두가 더 짧다). **렌더가 2,212자로 작은데도
거절됐다** — 고정 접두만으로 입력 몫의 절반을 넘는다.

### 하네스는 조용히 넘어가지 않는다 — 그래도 run은 「완주」다

거절은 `target_skipped`(`reason: gateway_error` · `scout_member` · `slice_id` · `error_detail`)로 남고
`report.md`가 「완주하지 못한 시도」와 「분석되지 않은 대상」을 센다(D70이 세운 자리다).
⚠ **그런데도 run의 판정은 `완주`다** — `0006`은 후보 0 · Finding 0으로 끝나면서 「보고할 Finding이 없다」를
적는다. 읽는 쪽이 「취약점이 없다」와 「하나도 못 봤다」를 가르려면 본문의 그 줄까지 가야 한다.

## 5. 해석 — 이 run이 말하는 것과 말하지 않는 것

**말하는 것**

1. **1-b는 2026-09-24 서빙 구성에서 입력 몫 안에 든다**(147호출 · 초과 0). 여유 5,043토큰.
2. **글자 상한은 토큰 상한이 아니다.** 비가 내용에 따라 2.7배 벌어진다.
3. **하네스가 서빙 창을 안 본다.** registry가 40,960이라 적어 둔 모델을 16,384로 띄웠는데 run이 끝까지 아무 말도 안 했고, 400을 받고 나서야 대상이 빠졌다. 이것이 이 run의 주 결과다([D87](../../open-questions/D/D87.md)).
4. 슬라이스는 대부분 함수 여럿에 걸친다(103/147) — 1-b가 없으면 Auditor가 그중 하나만 봤다.

**말하지 않는 것**

- **탐지 품질을 안 쟀다.** 정답 대조가 없고 Auditor·Critic이 Fake인 run과 Scout이 Fake인 run으로 갈려 있다.
  `0005`의 게이트 등급(E2 9 · E3 16)은 **Fake Auditor가 낸 것**이라 품질 신호로 읽으면 안 된다.
- **사고를 켠 Auditor를 안 쟀다.** 이 run의 인자는 사고 없이 답한다.
- **241개 중 94개는 Auditor에 안 닿았다** — Fake Scout 점수가 임계 아래였다. 그 94개의 토큰은 미측정이다.
- **실 Scout으로는 11개만 봤다.** 입력 몫을 넘는 비율은 모른다 — D87이 그것을 선행으로 든다.
- 실서빙 키의 `checksum`이 0이라 **비교표에 들어가지 않는다**.

## 6. 따라온 것

- [D87](../../open-questions/D/D87.md) 등재 — 실서빙 Scout 프롬프트가 컨텍스트를 넘어 후보가 통째로 빠진다.
- PR #289의 `_AUDITOR_SOURCE_CHARS` 주석과 ADR 0012 결정 1-b 문단을 계산값에서 이 실측으로 바꿨다.

## 9. 부록 — 재현 절차

**설정** — `git diff e54661a 41f802a -- config/ tests/`(5파일). 스모크 프로필 둘 · 스모크 예산
`atom_slice_smoke` · 실서빙 키 셋의 `request_timeout_s`. 브랜치는 `exp/atom-slice-smoke`이고 **머지하지 않는다**.

**ATOM 준비** — `~/whitehat-boss`에서 `41f802a`를 detached 워크트리 `~/slice-smoke/wt`로 뽑았다.
venv는 `~/venv-probe`(vLLM 0.27.1). 감시자·기동·정리 스크립트는 2026-09-24 판(`~/smoke-0924/`)을
`~/slice-smoke/`로 옮긴 것이고, 바뀐 것은 디렉터리 이름과 **서버 로그 파일명에 시각을 넣은 것** 하나다.

**실제로 친 명령**

```bash
MAX_HOURS=6 setsid nohup bash ~/slice-smoke/guard.sh > /dev/null 2>&1 < /dev/null &

# 실험 1 — Auditor
bash ~/slice-smoke/launch.sh qwen3.6-35b-a3b 8002 0.64 24576 2 Qwen/Qwen3.6-35B-A3B --revision 995ad96eacd98c81ed38be0c5b274b04031597b0 --structured-outputs-config.backend xgrammar --structured-outputs-config.disable_any_whitespace true
cd ~/slice-smoke/wt; setsid nohup timeout 14400 ~/venv-probe/bin/python -m src.cli run --profile atom_slice_auditor --target <표본>/repo --slices ~/slice-smoke/inputs/<슬라이스>.json --artifacts-root ~/slice-smoke/artifacts --device atom --condition B3prime@pipeline > ~/slice-smoke/logs/run_a.log 2>&1 < /dev/null &
bash ~/slice-smoke/stop.sh

# 실험 2 — Scout 풀
bash ~/slice-smoke/launch.sh seccoderx-8b 8000 0.20 16384 4 SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model --revision 25d7f38146bdf0fad1eac09c586b1993ef3e6ee4 --reasoning-parser deepseek_r1
bash ~/slice-smoke/launch.sh qwen3-14b 8001 0.30 16384 4 Qwen/Qwen3-14B --revision 40c069824f4251a91eefaf281ebe4c544efd3e18 --reasoning-parser deepseek_r1
cd ~/slice-smoke/wt; setsid nohup timeout 14400 ~/venv-probe/bin/python -m src.cli run --profile atom_slice_scout_pool --target <표본>/repo --slices ~/slice-smoke/inputs/<부분집합>.json --artifacts-root ~/slice-smoke/artifacts --device atom --condition B3prime@pipeline > ~/slice-smoke/logs/run_s.log 2>&1 < /dev/null &
bash ~/slice-smoke/stop.sh
touch ~/slice-smoke/STOP_GUARD
```

⚠ **따라 할 때 주의** — run 완료를 `pgrep -f "src.cli run ..."`로 기다리면 **그 명령줄 자신이 매칭되어
영원히 기다린다.** 이 회차에 실제로 밟았고, `run_20260925_0002`가 끝난 뒤 다음 run이 4시간 45분 동안
시작되지 않았다(서버는 그동안 떠 있었다). 대기는 완료 표시 파일로 한다.

**집계** — trace의 `model_call`(`input_tokens`·`output_tokens`·`wall_ms`·`prompt_ref`)과 `auditor_source`,
그리고 `prompts/*.md`의 글자 수를 센다. 집계 스크립트는 저장소 밖이다(작성자 세션 scratchpad `smoke/`).
