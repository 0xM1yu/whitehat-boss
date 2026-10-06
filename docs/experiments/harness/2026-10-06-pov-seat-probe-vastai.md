# PoV 좌석 단독 프로브 — `feyospace-v1-small`이 처음 돌았다 (2026-10-06)

| | |
| --- | --- |
| 목적 | **PoV 좌석이 유효한 PoC를 내는가** — 이 좌석은 이 저장소에서 한 번도 불린 적이 없었다 |
| run ID | `run_20261005_0001` (trace sha256 `2491fed0d2ac81d3b6dd7d15ad0970583821e4c2c76f4877b63a527b9c701bed`) |
| 하네스 커밋 | `569f7a9` (= 그때의 `origin/main`) |
| 프로필 | **`s6b_pov` — 커밋하지 않은 임시 프로필**(`dev_fake`에서 pov 좌석만 실모델). §5 |
| 장비 | vast 인스턴스 54351342 · RTX PRO 6000 Blackwell Server Edition · VRAM 97,887 MiB · 드라이버 595.84 · 이미지 `vllm/vllm-openai:v0.27.1` |
| 과제 | `arvo:1065`(번들 2파일) · 오라클은 로컬 `127.0.0.1:8666` · 카나리 `canary-arvo-1065.json`(`vm.mmap_rnd_bits` 28) |
| 산출물 | `_workspace/s6b-2026-10-06/`(gitignore — 로컬 회수) · 박스 로그 포함 |
| 비교표 | **넣지 않는다** — Scout·Auditor·Critic이 Fake이고 `tool_runner: fake`다 |

---

## 0. 결론 — 좌석은 돌았고, `submit`은 못 봤다

**관측된 것(전부 run 산출물에 있다).**

1. **좌석이 뜬다.** 준비까지 **319초**(가중치 캐시가 찬 상태). 가중치 로드 51.1 GiB · KV 캐시 31.89 GiB = **516,532토큰** · 창 262,144 기준 최대 동시성 1.97배.
2. **스키마를 통과하는 액션을 낸다.** 홉 넷 전부 `PovAction`으로 파싱됐다 — `inspect` 1건 · `gen` 3건. 파싱 실패 0건.
3. **도구를 부른다.** hop0이 `build`를, 나머지가 `poc`를 요청했고 하네스가 실행했다(`tool_ok: true` 전부).
4. **PoC 바이트가 조립된다.** 세 `gen`이 각각 **20 → 400 → 8,192바이트**를 냈다. 0바이트 0건.
5. **오라클 되먹임이 돈다.** 중간 `gen` 둘이 `vul` 다리에 제출됐고(실패 0) 판정이 다음 홉 프롬프트에 `oracle_vul exit_code=0`으로 실렸다. **PoV v3의 되먹임 경로가 끝까지 돈 것은 이번이 처음이다.**
6. **마지막 PoC가 파일로 남는다.** `pov_poc/0001.poc` 8,192바이트, digest가 trace의 `poc_digest`와 일치(`f8ca02c6…`).

**관측되지 **않은** 것.**

- 🔴 **`submit` 액션 0건.** find_0001은 홉 넷을 다 써 `stop_reason: max_hop`으로 끝났다(`pov_skipped`). 그래서 **최종 지정 후보가 0건**이고 이 run은 점수를 내지 않았다.
- **`success`가 참인 PoC 0건.** 제출한 둘 다 `response_exit_code: 0`이다 — 크래시가 아니다.
- **모델이 도구 출력을 읽고 고치는지**는 이 run이 못 가린다. `tool_runner: fake`라 `build`·`poc` 관측이 합성값이다. 확인된 것은 「도구를 올바른 모양으로 **요청**한다」까지다.
- **홉이 더 있으면 `submit`이 나오는지** — 미실측(§3 결함 1).

---

## 1. 실측 — 이 좌석의 첫 수치

| 항목 | 값 | 어떻게 쟀나 |
| --- | --- | --- |
| 준비(ready) | **319 s** | `swap.log` `ready feyospace-v1-small after 319s`. 가중치가 이미 캐시에 있었다 |
| 가중치 로드 | 51.1 GiB / 11.28 s | vLLM 로그 `Model loading took` · `Loading weights took` |
| 엔진 초기화 | 94.27 s | vLLM `init engine (profile, create kv cache, warmup model)` |
| KV 캐시 | 31.89 GiB = 516,532토큰 | vLLM `GPU KV cache size` |
| VRAM 점유 | 87,833 / 97,887 MiB | `nvidia-smi`(서버 기동 후) |
| **디코드 속도** | **17.2~18.1 tok/s**(단일 스트림) | 아래 홉 표 — 출력 토큰 ÷ 홉 벽시계(계산값) |
| 호출당 입력 | 60,446 → 60,535 → 60,718 → 61,018 | trace `model_call`(서버가 센 값) |

### 홉별

| 홉 | 액션 | 도구 | 벽시계 | 출력 토큰 | 속도 | PoC |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | `inspect` | `build` | 1,667 s | 28,734 | 17.2 tok/s | — |
| 1 | `gen` | `poc` | 532 s | 9,621 | 18.1 tok/s | 20 B |
| 2 | `gen` | `poc` | 1,622 s | 28,935 | 17.8 tok/s | 400 B |
| 3 | `gen` | `poc` | 1,414 s | 25,253 | 17.9 tok/s | 8,192 B |

**find_0001 PoV 루프 합계: 5,235 s(87.3분) · 출력 92,543토큰 · 입력 242,717토큰.**

🔴 **출력이 사고 상한에 거의 닿는다.** `thinking_token_budget` 28,672에 대해 홉 셋이 28,734 · 28,935 · 25,253이다. 답 본문은 수십~수백 토큰이고 나머지가 사고다. 그래서 **홉 하나의 벽시계가 사고 길이로 정해진다** — 짧았던 hop1(9,621토큰)만 9분이고 나머지는 23~28분이다.

⚠ 같은 run의 find_0002 hop0은 입력 6,572 · 출력 1,021로 **61초**에 끝났다. 입력이 열 배 작고 사고도 짧았다 — **홉 시간은 Finding마다 크게 다를 수 있다**(n=2).

---

## 2. 무엇이 어떻게 돌았나

```
18:13:43Z  8단계 진입 (Fake 단계에서 후보 47 · Finding 11 · proven 8)
18:41:30Z  hop0  inspect/build         ok exit=0
18:50:22Z  hop1  gen → PoC 20 B        → /submit-vul 성공 → oracle_vul exit_code=0
19:17:24Z  hop2  gen → PoC 400 B       → /submit-vul 성공 → oracle_vul exit_code=0
19:40:58Z  hop3  gen → PoC 8,192 B     → max_hop → pov_skipped (pov_poc/0001.poc)
19:41:59Z  find_0002 hop0 inspect/build (61 s) — 여기서 run이 멈췄다(§4)
```

**바이트가 커지는 방향으로 고쳐졌다.** 20 → 400 → 8,192. hop2 프롬프트에 `oracle_vul exit_code=0`이 실려 있었다(프롬프트 기록 `0069_pov_hop2.md`). ⚠ **인과는 단언하지 않는다** — 되먹임이 없는 대조 run이 없다(n=1).

**오라클 사이드카**(`oracle/verdict.json`): 후보 2건 · 둘 다 `response_exit_code: 0` · `cached: false` · `designatable: false`(중간 gen이라 최종 후보가 아니다) · `final: null`.

---

## 3. 이 run이 남긴 결함

| 번호 | 등급 | 무엇 | 이슈 |
| --- | --- | --- | --- |
| 1 | **HIGH** | `max_hops: 3`이 PoV에 짧다 — 모델이 매 홉 PoC를 키우는 중에 홉 상한으로 끊겼다(`stop_reason: max_hop`). 그 run은 최종 후보가 0건이라 **구조적으로 점수가 안 난다.** ⚠ 이 값은 Scout의 need_evidence 홉과 **공유**되므로 올릴 때 두 단계가 함께 움직인다. ⏵ **2026-10-06 — 그 공유를 끊고 값을 올렸다**(PR #517): PoV 상한은 `pov_tool_hops`이고 **값은 16**(@danielcho02 결정)이다. ⚠ 16으로 돈 run은 0건이고, 배가 추정이 맞으면 창에 닿아 **submit 없이** 끝난다(§8) | #511 |
| 2 | MED | 사고가 홉마다 상한을 거의 다 쓴다(28,672 중 25,253~28,935) — 홉 하나가 23~28분이다. `reasoning_effort: xhigh`는 Auditor에서 물려받은 **계획값**이고 이 좌석에서 재 본 적이 없었다. 낮추면 홉이 빨라지는지 미실측 | 미등록 |
| 3 | MED | 실서빙 프로필은 `--device`·`--condition` 없이 시작하지 않는데(올바른 가드), 그 거절이 **과제 아카이브를 푼 뒤**에 와서 작업 디렉터리가 더러워진다 — 같은 인자로 재시도하면 「비어 있지 않다」로 또 거절된다 | 미등록 |
| 5 | **HIGH** | **PoC 바이트가 도구 입력이 아니다** — `nodes/pov.run_tool`이 `ToolArgs(argv=[tool], workdir=workdir)`만 넘기므로 도구가 모델의 PoC를 보지 않는다(`nodes/pov.py:188`의 ⚠ 그대로). 그래서 되먹임이 **자기 PoC와 무관한 관측**이고, 모델에게 「됐으니 submit」을 판단할 신호가 구조적으로 없다. 이 run에서 처음 관측된 결함은 아니다(적대적 리뷰 2026-09-30) — **이 run이 그 결과를 처음 보여준 것이다** | 미등록 |
| 4 | LOW | 첫 기동이 준비 상한 600 s에 걸렸다 — 가중치 다운로드(23 MB/s)가 그 안에 안 끝난다. `hf_transfer`를 깔면 80~100 MB/s다(이미지에 기본 미포함) | 미등록 |

**1번과 5번이 S7의 선행이다.** 이 프로브의 목적이 「`submit`을 보는가」였다. ⚠ **못 본 원인을 이 run이 못 가른다** — 경합하는 기제가 둘이다. (가) 홉 상한이 끊었다 (나) 5번 때문에 되먹임이 무신호라 submit 조건이 성립하지 않았다. (나)는 코드로 단언되고 (가)는 **추정**이다(홉을 늘려 재프로브하지 않았다). **5번을 고치기 전에 상한만 올리면 무신호 되먹임을 더 많이 읽는 run이 된다** — 자세한 산수는 §8.

---

## 4. 한계 — 이 보고서가 말하지 못하는 것

- 🔴 **run이 완주하지 않았다.** find_0002 hop0 직후 **하네스 프로세스가 세션 백그라운드 시간 한도(90분)에 걸려 종료됐다.** 그래서 `run_manifest.json`이 **없다**(러너가 그 작성 지점에 못 닿았다). 중단은 과제·모델·장비 때문이 아니라 **실행 환경 때문**이다.
- **표본 n=1이다.** PoV 루프를 끝까지 돈 Finding은 find_0001 하나다. proven 8건 중 7건은 안 돌았다.
- **Fake 단계의 등급·`proven`은 측정이 아니다.** `dev_fake`의 Fake Critic이 올라온 것을 전부 지지하고 `_run_repro()`가 상수를 낸다 — proven 8건은 「소스가 있다」에 가깝다(이슈 #232의 살아 있는 폭).
- **`tool_runner: fake`다.** 도구 관측이 합성값이라 모델이 진짜 출력을 읽는지는 이 run 밖이다.
- **임시 프로필을 썼다**(§5) — 커밋되지 않았으므로 제3자가 같은 명령으로 재현할 수 없다. 재현하려면 §5의 프로필을 다시 만든다.
- **디코드 17.2~18.1 tok/s는 이 좌석·이 장비·단일 스트림의 값이다.** 동시 요청·다른 장비는 미실측.

---

## 5. 재현

**임시 프로필**(`config/profiles.yaml`에 추가, 커밋하지 않는다):

```yaml
  s6b_pov:
    description: "S6b 프로브 전용(커밋하지 않는다) - dev_fake에서 PoV 좌석만 실모델 feyospace-v1-small."
    production: false
    roles: { scout: fake, auditor: fake, critic: fake, pov: feyospace-v1-small, embedding: fake, reranker: fake }
    adapters: { retriever: fake, tool_runner: fake, state_store: memory }
    budget: default
```

`config/budgets.yaml`의 `thresholds.pov_attempt_top_n`을 **1**로 두었다(main은 8 — 프로브는 Finding 하나만 보면 된다).

```bash
# 1) 박스: 하네스 몫 설치 → 감시자 → 가중치 선취(이미지에 hf_transfer가 없다) → 서버
SWAP_SSH_HOST=root@<host> SWAP_SSH_PORT=<port> SWAP_REMOTE_ROOT=/root/harness \
  SWAP_REMOTE_PYTHON=python3 bash tools/serve/vast-bootstrap.sh
ssh <box> 'cd /root/harness && MAX_HOURS=4 setsid nohup bash tools/serve/guard.sh >/dev/null 2>&1 &'
ssh <box> 'pip install hf_transfer && HF_HUB_ENABLE_HF_TRANSFER=1 hf download \
  feyospace/feyospace-v1-small --revision fd02711ea1eb8ea15772d0b7fbded18ecfe8c9d1'
ssh <box> 'cd /root/harness && HARNESS_RUN_ID=<id> HARNESS_DEVICE=vastai-remote \
  SWAP_DEADLINE_S=1500 setsid nohup bash tools/serve/swap.sh feyospace-v1-small >/root/swap.out 2>&1 &'

# 2) 로컬: 터널(모델 엔드포인트가 127.0.0.1 고정이다)
ssh -N -L 8006:127.0.0.1:8006 <box>

# 3) 로컬: 오라클(127.0.0.1:8666)과 카나리는 2026-10-03 배관 증명 보고서의 절차 그대로
# 4) 로컬: 프로브 run — 실서빙 프로필이므로 --device·--condition이 필수다
CYBERGYM_API_KEY='<저자 server/types.py의 기본 키>' \
python -m src.cli run --profile s6b_pov --device vastai-remote --condition B3prime@pipeline \
  --task-dir <번들> --task-work-dir <빈 디렉터리> --artifacts-root <밖> \
  --oracle-canary <경로>/canary-arvo-1065.json \
  --oracle-submit --oracle-server http://127.0.0.1:8666
```

⚠ **run이 90분을 넘긴다.** 홉 하나가 23~28분이므로 세션 타임아웃이 없는 자리에서 돌린다(§4의 중단 사유).

---

## 6. 비용

인스턴스 54351342를 약 2.6시간 사용했다($1.444/h ≈ **$3.8**). 그중 프로브 run은 88분이고 나머지는 이미지 pull·가중치 다운로드·첫 기동 실패다. **결과물을 로컬에 회수한 뒤 인스턴스를 삭제했고 `show instances`에서 사라진 것을 확인했다.**

---

## 7. 다음

1. **결함 1(홉 상한)은 이슈 #511로 올렸다** — S7의 선행이다. 선택지 넷을 가르는 입력은 「홉을 늘려 재프로브」다.
2. 홉 상한을 올려 같은 Finding을 다시 프로브할지는 **사용자 결정**이다(추가 과금 · 가중치 캐시가
   삭제됐으므로 재다운로드 10분이 붙는다). ⚠ **손잡이는 `max_hops`가 아니다** — PR #517이 PoV를
   전용 `pov_tool_hops`로 갈랐다. 그리고 **§8이 「상한이 원인」과 경합하는 기제를 하나 더 든다**
   (결함 5 — PoC 바이트가 도구 입력이 아니다. 요청에 싣는 것은 별건 PR이고 읽게 하는 것은 #513이다). 그쪽을 먼저 고치지 않으면 상한을
   올려도 같은 무신호 되먹임을 더 읽는다.
3. S7은 이 보고서의 홉 시간으로 다시 환산한다 — 홉당 23~28분은 플랜의 계산값보다 크다.

## 8. 홉별 입력 곡선 — 창은 PoV의 벽이 아니다

**왜 쟀나.** #511의 선택지를 가르려면 「창에 몇 홉이 드나」가 필요하다. **재프로브 없이** 이 run의
아티팩트로 답한다 — 대여도 과금도 0이다.

**§1이 이미 네 홉의 입력을 든다.** 이 절은 그 수에서 파생된 분석이다 — 새 run이 아니다.

**출처.** `trace.jsonl`의 `model_call`(role=pov) `input_tokens`는 **서버가 센 `usage.prompt_tokens`**다
(`src/adapters/openai_compat.py:426`) — 실측이다. 「프롬프트 바이트」는 사이드카 프롬프트 파일의
**크기**다(내용은 읽지 않았다 — 조립된 PoC 바이트가 들어 있다).

| hop | 입력(토큰) | Δ입력 | 프롬프트(바이트) | Δ바이트 | 출력(토큰) | 벽시계(s) | 디코드(tok/s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 60,446 | — | 165,172 | — | 28,734 | 1,667 | 17.24 |
| 1 | 60,535 | +89 | 165,536 | +364 | 9,621 | 533 | 18.06 |
| 2 | 60,718 | +183 | 166,183 | +647 | 28,935 | 1,620 | 17.86 |
| 3 | 61,018 | +300 | 167,148 | +965 | 25,253 | 1,413 | 17.87 |

`find_0001` 네 홉이다(`find_0002`는 hop0에서 run이 끊겼다 — 입력 6,572, 슬라이스 타깃이 아니라 좁은
프롬프트다). 합: 출력 92,543토큰 · 벽시계 5,233 s = **1.45시간 · finding 하나**.

### 읽히는 것 넷

1. **홉당 입력 증가가 작다** — 3홉 누적 **572토큰**(홉당 평균 190.7). `hop0`가 60,446이고 그 뒤는
   사실상 평평하다. 8,192바이트 PoC가 나온 hop2 다음 홉도 **+300**이다 — `PovSpec`이 선언형이라
   PoC 바이트가 프롬프트로 되돌아오지 않는다.
2. **여유가 많다** — 입력 몫 **229,376**(창 262,144 − 출력 상한 32,768, 계산값) 대비 hop3에서
   **168,358토큰**이 남았다.
3. **홉 벽시계는 출력이 정한다** — 네 홉 다 `출력 ÷ 벽시계`가 **17.24~18.06 tok/s**(실측)이고,
   입력 60,446의 프리필은 그 안에서 작다. 입력을 늘려도 홉 시간은 거의 안 움직이고,
   **사고 길이를 줄이면 비례해 줄어든다.**
4. **바이트/토큰 비 2.733~2.739** — 이 좌석의 첫 실측이다(글자 상한을 토큰으로 환산할 때 쓴다).

### 몇 홉이 드나 — 두 추정이 갈리고, 이 run으로는 못 가른다

| 가정 | 드는 홉 | 성격 |
| --- | --- | --- |
| 증가가 선형(300/홉)으로 이어진다 | ≈ 561홉 | 계산값 |
| 증가가 배가(89→183→300)로 이어진다 | ≈ hop 12 | 계산값 |

🔴 **델타가 셋뿐이라 어느 쪽인지 이 run이 안 가른다.** 성립하는 것은 **둘 다 3보다 훨씬 크다**는
것뿐이다. 가르는 다음 측정은 둘이다 — ① 홉을 더 돌려 델타를 넷 이상 본다 ② 추가되는 턴을
`PovSpec`과 관측으로 분해한다(프롬프트 내용을 읽어야 해서 PoC 바이트 격리가 선행이다).

**실도구면 커진다.** fake 도구는 `read_payload`가 `None`이라 관측 꼬리가 **없었다**. 실도구면 꼬리가
최대 2,000자(`runner._POV_OBSERVATION_TAIL_CHARS`)라 위 비로 **홉당 ≈731토큰**이 붙는다(계산값).
그 값으로도 여유 168,358은 **≈168홉**이다(계산값).

**캐시는 안 갈린다.** 네 홉 다 `cached_tokens: 0`인데 이것을 「캐싱이 없었다」로 읽지 않는다 —
서버가 `prompt_tokens_details`를 안 주면 같은 0이 나온다(R6 · `src/bench.py:377`). 어느 쪽인지
이 run으로는 모른다.

### 그래서 홉 상한은 창 손잡이가 아니라 **GPU 시간 손잡이**다

4홉이 1.45시간이고 `pov_attempt_top_n`이 8이다. 홉 상한을 N으로 두면 run 하나가
**8 × (1+N) × 21.8분**이다(계산값 — 홉 평균 21.8분은 위 네 홉의 실측 평균). N=7이면 ≈23시간이다.

같은 시간에 홉을 늘리는 레버는 상한이 아니라 **사고 길이**다. 출력 상한 28,672가 네 홉 중 셋에서
거의 소진됐고(28,734 · 28,935 · 25,253) 벽시계가 출력에 비례하므로, 사고를 8,192로 두면 홉이
**≈463초**가 된다(계산값). ⚠ **품질 영향은 미실측이다** — 이 좌석에서 사고 길이를 가른 run이 없다.
