# 실서빙 run 다섯 번 — arvo:1065 · vast RTX PRO 6000 Blackwell(홍콩/일본)

> **무엇이 처음인가.** 5~7단계가 실모델로 **처음 끝까지 돌았다** — Auditor 도구 홉이 후보당 최대
> 일곱까지 돌고, Critic(`gpt-oss-20b`)이 **처음으로 판정을 냈다**(5건). 그리고 그 run은
> `R-1 finished`로 **완주했는데 채점기 제출이 0건이다** — Critic이 5건 전부를 `need_evidence`로
> 판정하고 PoV 풀이 그 판정을 설계상 빼기 때문이다
> ([#604](https://github.com/0xM1yu/whitehat-boss/issues/604)).
>
> **이 보고서는 run 다섯 번을 한 묶음으로 든다.** 넷이 중간에 죽었고 그 사인이 서로 다르기 때문이다 —
> 셋은 **로컬 인터넷 끊김**(하네스 결함 아님), 하나는 **콜드 기동 상한**
> ([#571](https://github.com/0xM1yu/whitehat-boss/issues/571)).

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 구간 | `2026-10-11 02:49 KST` 대여 → `15:12 KST` 삭제 · **박스 가동 12.4시간** |
| 채점 대상 run | `run_20261011_0001` · `02:14:41Z` → `06:08:51Z` · **3시간 54분 10초** |
| `repo_commit` | `cf6b28c09088f39b42bc1491accdb470ced65dd6` · **dirty 아님**(다섯 run 전부) |
| 실제 서빙 모델 | `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18`(scout_memory·scout_injection) · `hf://SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model@25d7f38146bdf0fad1eac09c586b1993ef3e6ee4`(scout_boundary) · `hf://Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`(auditor) · `hf://openai/gpt-oss-20b@6cee5e81ee83917806bbde320786a8fb61efebee`(critic) |
| 서빙 안 된 좌석 | **`pov`(`feyospace-v1-small`)** — 8단계가 할 일이 없어 교대가 안 돌았다. 가중치는 선취해 뒀다 |
| 프롬프트 판 | scout `v4` · auditor **`v13`** · critic `v6` · pov `v5`(미사용) |
| 프로필 · 장비 · 조건 | `harness_pool` · `vastai-remote` · `B3prime@pipeline` |
| GPU | vast **55256062**(홍콩) · RTX PRO 6000 Blackwell **Workstation** Edition · 97,887 MiB · 드라이버 590.48.01 · 32 vCPU · RAM 123 GiB · 디스크 300 GB · $1.444/hr |
| 버린 박스 | vast **55249138**(일본 · $1.39/hr) — 이미지 내려받기가 **48분**에도 안 끝나 홍콩 박스와 경주시켜 버렸다(§6) |
| 배선 | 하네스·도구·채점기 **로컬**(Windows 11 · Python 3.14.5) · 서빙만 원격 · ssh 터널 `8000·8001·8002·8003·8006` |
| 🔴 ssh 경로 | **직결** `root@<host-ip>:63027`. vast **프록시**(`ssh3/ssh8.vast.ai`)로는 터널이 두 번 끊겼다(§6) |
| 셸 | PowerShell + `Git\usr\bin`을 PATH 앞에([#538](https://github.com/0xM1yu/whitehat-boss/issues/538)) |
| 도구 이미지 | `harness-tools:v3` · 인덱서 index-v4(preflight가 저장소 파일과 바이트 동일 확인) |
| 채점기 | 저자 소스 `c6fe202` · `127.0.0.1:8666` · **새 `--db_path`** `C:\w\oracle-run08\poc.db` · rate limit 기본 20회/60초 |
| 카나리 | `canary-arvo-1065-20261011.json` — **이 부팅에서 재측정**(`verdict: pass` · 네 조합 ×10 · `139` 0건 · `vm.mmap_rnd_bits 28` · `boot_id a94d7c1e…`) |
| preflight | **ERROR 0 · WARN 1 · OK 13** — WARN은 `model_checksums` 전부 0 |
| 🔴 비교표 적격 | **아니다** — `checksum`이 전부 0이다([CLAUDE.md](../../../CLAUDE.md) §0) |
| 산출물 보존 | `C:\w\s14-results\`(15 MB · run 다섯 + 채점기 DB·로그 + **원격 서빙 로그 14종**) — 🔴 **저장소 밖이고 커밋되지 않는다.** 이 보고서의 수는 그 디렉터리가 원천이다 |

## 1. run 다섯 번 — 어디서 죽었나

| run | 끝난 단계 | 종료 사유 | 원인 |
| --- | --- | --- | --- |
| `run_20261010_0001` | 4 교대 중 | `model_swap_failed` | 로컬 인터넷 전환 — 교대 ssh·터널 동시 `Connection reset by peer` |
| `run_20261010_0002` | 5 Auditor 첫 호출 | `model_swap_failed` | 같음(터널 재차 끊김) |
| `run_20261010_0003` | **7 Critic 교대** | `model_swap_failed` | **콜드 기동 상한** — `gpt-oss-20b`가 `ready_timeout_s: 300`에 2회 다 걸렸다(#571) |
| `run_20261010_0004` | 5 Auditor hop 7 | `model_gateway_unavailable` | 로컬 인터넷 끊김 — `RemoteDisconnected` + 내림 ssh `Connection timed out` |
| **`run_20261011_0001`** | **9 리포트(완주)** | **`R-1 finished`** | — · 🔴 **제출 0건**(§3) |

**셋은 하네스 결함이 아니다.** 사용자가 세션 중 인터넷을 바꿨고 그 순간 수립된 ssh가 전부 끊겼다
(02:57 · 03:33 · 09:29). 원격 vLLM은 그때도 살아 있었다 — `run_20261010_0002`의 Auditor 요청은
끊긴 시점에 **23.8 tok/s로 생성 중**이었고(원격 로그), 하네스가 받은 것은
`RemoteDisconnected: Remote end closed connection without response`다.

⚠ **그 셋에서 하네스 쪽 사실 둘이 드러났다.** ① 모델 호출의 `attempts`가 **1**이라 전송 끊김에
재시도가 없다. ② `cli run`에 재개 손잡이가 없다(`--help`에 `resume` 0건) — `run_20261010_0003`은
7단계에서 죽으면서 **4.02 gpu시간·Finding 6건**을 버렸다. 둘 다 #571의 코멘트에 실측째 적었다.

## 2. 기동 실측 — 콜드 ↔ 따뜻한

같은 박스에서 쌍으로 쟀다. **#571이 이름 든 좌석은 600초짜리 둘뿐이었는데 300초 좌석에서도 났다.**

| 좌석 | 상한 | 콜드(첫 기동) | 따뜻한(캐시 뒤) |
| --- | --- | --- | --- |
| `gpt-oss-20b` | 300 | **2회 다 TIMEOUT** — 로그가 `MARLIN Mxfp4 MoE backend`에서 끊김 | **ready 62 s**(로드 26.4 s) · run에서는 **38 s** |
| `qwen3.8-27b` | 600 | **attempt 1 TIMEOUT** | attempt 2 **147 s** · 다음 run **69~79 s** |
| `qwen3-14b` + `seccoderx-8b` | 550 · 400 | (같은 교대에서 다운로드) | 쌍으로 **57 s** |

**처방이 들었다.** `run_20261011_0001` 전에 `hf_transfer`를 깔고 PoV 가중치(`feyospace-v1-small`
약 55 GB)를 **선취**해 뒀다 — 8단계가 돌지 않아 그 선취의 효과는 **미검증**이지만, Critic 좌석은
같은 선취 상태에서 **38초**에 떴고 그 전 run은 같은 좌석에서 죽었다.

⚠ 따뜻한 값은 각 **n=1**이다. 선취는 이미지에 `hf_transfer`가 없어 수동 단계다(#571 선택지 (a)·(f)).

## 3. 🔴 완주했는데 제출이 0건이다

`run_20261011_0001`의 단계별 결과:

| 단계 | 결과 |
| --- | --- |
| 0~3 | OK — `index`·`semgrep` 성공 · **`clang-tidy`·`build` 실패**(`unknown type name 'uint8_t'` — 기존 [#507](https://github.com/0xM1yu/whitehat-boss/issues/507), 번들에 `config.h`가 없다) |
| 4 Scout | 호출 **12/12 성공** · 승격 6 · 기각 6 |
| 5 Auditor(v13) | 호출 20건 · **실패 1**(스키마) · `auditor_hop` **19** · **Finding 5건**(E2 2 · E1 3) · 후보 1건 `target_skipped` |
| 6 게이트 | `gate_eval` 10 · `promoted_to_critic` **2** · `evidence_weak` **3** |
| 7 Critic(v6) | 호출 **5건 전부 ok** → **`critic_need_evidence` 5건** |
| 8 PoV | **`stage_end` 0초** · `oracle_final {status: none, reason: no_poc}` |
| 9 리포트 | ok · `R-1 finished` · `stopped_after: null` |

`run_manifest.json`의 `oracle`: `{"vul_submitted": 0, "vul_failed": 0, "final": "none", "final_reason": "no_poc"}`.

빠지는 자리는 `src/orchestrator/runner.py`의 `_pov_targets` docstring이 명시한다 — 추가 풀은
*"Critic이 반박(F-6)·보류(`need_evidence`, F-7)로 판정한 것"*을 뺀다. `validated`도 비었으므로
(5건 전부 `queued`) `pov_attempt_top_n: 8`이 양수여도 **풀이 공집합**이다.

**버려진 E2 둘은 내용이 구체적이다**(`report.md`):

| id | 등급 | 제목 | CWE · confidence |
| --- | --- | --- | --- |
| `find_0003` | E2 | uint32 wrap of .mgc header counts defeats check_buffer consistency check, causing OOB read in apprentice_list | CWE-190 · **0.85** |
| `find_0006` | E2 | Division by zero on a FILE_SEARCH magic entry with a zero-length string value | CWE-369 · **0.80** |

⚠ **이 둘이 정답인지는 이 run이 답하지 않는다** — 채점기에 안 보냈으므로 **판정 0건**이다.
`arvo:1065`의 정답 경로(`regexec`·`pmatch`·`file_replace`)와 겹치는지는 안 봤다 — 미실측.

🔴 **그런데 run은 실패가 아니다.** 종료 코드와 상태만 보면 완주이고, 점수가 없는 이유는
`oracle.final_reason`을 열어야 보인다. #604가 추적한다.

## 4. 장부 실측

| 항목 | 값 |
| --- | --- |
| 입력 토큰 | **626,702** |
| 출력 토큰 | **386,996** |
| `gpu_seconds` | **13,868**(3.85시간) |
| `tool_ms` | 7,353 |
| 생성 처리량 | **23.3~23.8 tok/s**(`qwen3.8-27b` · 원격 로그의 `Avg generation throughput`과 trace의 `output_tokens / wall_ms`가 같은 값을 준다) |
| 호출 수 | scout 12 · **auditor 20** · critic 5 |
| 한 홉 최장 | `run_20261010_0003`에서 **2,205초 / 51,383 토큰** |
| 한 후보 최대 | `run_20261010_0004`에서 **홉 7개 · 123,463 토큰 · 83분** |

**Auditor가 run 시간의 대부분이다** — 3시간 54분 중 5단계가 약 3시간 40분이다.

⚠ `gpu_seconds`는 `gpu_ms_source: request_wall_clock`이다 — 요청 벽시계이고 GPU 점유 실측이 아니다.

## 5. Critic이 처음 판정을 냈다

| 호출 | 벽시계 | 출력 토큰 |
| --- | --- | --- |
| 1 | 515 s | 58,156 |
| 2 | 65 s | 7,365 |
| 3 | 6 s | 635 |
| 4 | 6 s | 563 |
| 5 | 6 s | 578 |

🔴 **뒤 셋이 6초에 600토큰대다.** 그 셋이 무엇을 보고 보류를 냈는지는 **응답이 저장되지 않아
모른다**([#603](https://github.com/0xM1yu/whitehat-boss/issues/603)). 5/5가 같은 판정인 것이
모델 판단인지 입력 구조 때문인지 이 run만으로는 **안 갈린다 — 미실측**이고, 비교할 Critic 분포도
없다(앞 run들은 7단계에 닿지 못했다).

## 6. 운영에서 걸린 것

- **vast ssh 프록시로는 터널이 유지되지 않았다.** `ssh3/ssh8.vast.ai` 경유에서 두 번 끊겼고
  직결(`public_ipaddr:direct_port_start`)로 바꾼 뒤 3시간 54분 run이 **끊김 0**으로 돌았다.
  ⚠ 인터넷 전환이 겹쳤으므로 **프록시가 원인이라는 분리 실험은 없다** — n=1의 상관이다.
- **터널 기본값이 30초에 스스로 끊는다.** `tools/serve/vast-tunnel.sh`가 `ServerAliveInterval=10`만
  주고 `ServerAliveCountMax`를 안 줘서 기본 3이 된다. 이 run에서는 `~/.ssh/config`에 그 호스트만
  `ServerAliveCountMax 12`를 줘 덮었다(저장소 밖이라 `repo_dirty` 아님).
- **터널을 자동 재연결 루프로 감쌌다**(세션 스크래치패드) — run 중 한 번 되살아났다.
- **이미지 내려받기가 박스마다 크게 다르다.** 일본 박스(다운 856 Mbps 광고)는 **48분**에도
  `loading`이었고, 홍콩 박스는 생성 **2.5분**에 ssh가 열렸다(이미지 캐시 선재). 둘을 병렬로 빌려
  먼저 뜬 쪽을 쓰고 나머지를 즉시 삭제했다 — 겹친 과금 약 $0.1.
- **작업 디렉터리는 run마다 빈 것을 준다.** 앞 run의 해제 트리가 있으면 `--task-work-dir가 비어
  있지 않다`로 거부된다(exit 2).
- **교대를 손으로 부를 때는 `HARNESS_DEVICE`가 필요하다.** 없으면 `ABORT: HARNESS_DEVICE가 없다`.
  감시자 정지 표지(`STOP_GUARD`)가 남아 있으면 그것도 ABORT 사유다 — 새 감시자를 먼저 띄운다.

## 7. 비용

| 항목 | 값 |
| --- | --- |
| 홍콩 박스 | 12.4시간 × $1.444 ≈ **$17.9** |
| 일본 박스(버림) | 약 0.8시간 × $1.39 ≈ **$1.1** |
| 합계 | ≈ **$19** |
| 그중 쓸모 있는 run | `run_20261011_0001` 3.9시간 — 약 **$5.6** |

**버려진 시간의 사인**: 이미지 내려받기 1.5시간 · 가중치 콜드 다운로드 약 1시간 · 끊김으로 죽은
run 셋 약 2.5시간 · 대기·조사 약 3시간.

## 8. 이 run이 낸 결함

| 번호 | 내용 | 상태 |
| --- | --- | --- |
| [#604](https://github.com/0xM1yu/whitehat-boss/issues/604) | Critic `need_evidence` 5/5가 PoV 풀을 비워 완주하고도 제출 0건 | 신규 |
| [#602](https://github.com/0xM1yu/whitehat-boss/issues/602) | Auditor v13이 `answer`를 빈 `procedure`로 내고 재시도 2회가 같이 실패 — 25,271 토큰·18분 | 신규 |
| [#603](https://github.com/0xM1yu/whitehat-boss/issues/603) | 스키마 실패한 응답이 산출물에 안 남아 #602·#604를 진단할 수 없다 | 신규 |
| [#571](https://github.com/0xM1yu/whitehat-boss/issues/571) | 콜드 기동 상한 — **300초 좌석에서도 났다**는 실측과 「재개 없음」비용을 코멘트로 붙였다 | 기존 |
| [#507](https://github.com/0xM1yu/whitehat-boss/issues/507) | `clang-tidy`·`build`가 번들에서 컴파일되지 않는다 | 기존 · 재현 |

## 9. 재현 절차

셸·명령째 순서는 [portability.md](../../portability.md) 「Windows에서 원격 교대 run 절차」와 같다.
이 run이 **더한 것 넷**:

1. **직결 ssh를 쓴다** — `vastai ssh-url <id>`가 주는 `root@<public_ipaddr>:<direct_port_start>`.
   `~/.ssh/config`에 그 호스트만 `ServerAliveCountMax 12` · `TCPKeepAlive yes`.
2. **가중치를 선취한다** — 좌석마다
   `pip install hf_transfer && HF_HUB_ENABLE_HF_TRANSFER=1 hf download <repo> --revision <rev>`.
   8단계까지 갈 작정이면 `feyospace-v1-small`도 미리 받는다(약 55 GB).
3. **카나리를 이 부팅에서 다시 잰다** — Docker를 재기동했으면 `boot_id`가 달라져 preflight가 ERROR다.
   `python tools/oracle_canary.py arvo:1065 --out <경로>`(컨테이너 40회, 몇 분).
4. **터널을 자동 재연결로 감싼다** — 끊김이 run 전체를 죽이지 않게.

⚠ **그래도 8단계는 안 돈다** — #604가 풀리기 전에는 Critic이 보류를 내는 순간 PoV 풀이 빈다.
