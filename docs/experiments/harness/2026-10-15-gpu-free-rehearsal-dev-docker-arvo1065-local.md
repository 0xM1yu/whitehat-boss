# GPU 없는 리허설 — arvo:1065 실번들 · `dev_docker` · 이 PC

> ## 🔴 배관·정렬 리허설 · 역량 미측정
>
> 판정하는 것은 둘이다 — **(1)** 실제 Level 1 번들(arvo:1065)로 GPU 없이 0~9단계가 완주하고 채점기 제출·최종 지정
> 배관이 서는가. **(2)** 후보 컷의 설명 일치 예약(`thresholds.candidate_desc_reserve`)이 설명과 맞는 후보를
> 컷에 들이는가. **모델은 전부 Fake다** — 이 run의 어떤 수치도 탐지 역량·점수가 아니다.
>
> 🔴 **이 기록이 보이지 않는 것** — 모델 품질·점수(Fake). 실서빙 슬라이스 경로(`--slices`)의 컷: 이 리허설은 **심볼 색인 경로**다.
> 같은 정렬 함수(`runner._order_and_cut`)를 쓰지만 슬라이스 수·신호 분포는 **미실측**이다. 예약 효과가 점수로 이어지는지.
>
> ⚠ **산출물을 보존하지 않았다.** 아래 값은 세션 중 trace·색인에서 읽어 옮긴 것이고 원문 파일은 남아 있지 않다(세션 임시 폴더).
> 그래서 값 옆에 어느 이벤트에서 나왔는지를 적었다. 다시 재려면 같은 명령을 다시 돌린다(§5).

## 메타

| 항목 | 값 | 비고 |
| --- | --- | --- |
| 실험 종료 시각 | `2026-10-15 18:35 KST` (`09:35 UTC`) | 세 run 중 마지막(C)의 trace 끝 시각 |
| 실제 서빙 모델 | **없음** — 전 역할 Fake | 프로필 `dev_docker`(모델 전부 Fake · 도구는 실제 Docker 이미지) |
| `repo_commit` | A·B `e4e5269`(약식 — 전체 40자 `미기재`), dirty false · C `b322dc6`(약식 — 전체 40자 `미기재`, dirty `미기재`) | C가 이 PR의 첫 커밋이다. A·B는 그 부모(PR #685 머지) |
| `run_id` | A `run_20261015_0001` · B `미기재` · C `미기재` | |
| run 산출물 경로·보존 | 세션 scratchpad · **보존하지 않는다** | 세션 임시 폴더. `gc`와 무관하게 세션과 함께 사라진다 |
| `trace.jsonl` sha256 | `미기재` | |
| `tool_runner` | 프로필 선언값 `docker` / 실제 `docker` | 오버라이드 없음 |
| `--device` | **미지정** | 이 PC에서 직접 돈 run이라 장비 파일(`config/serving/<device>.yaml`)을 안 쓴다. 비교표에 넣지 않는다 |
| `tool_images` digest | `harness-tools:v3` · 종류 docker 이미지 config sha256 `sha256:fbd453eddf5dc30726e8df17171158cc935c65380f10d9a6dc6ba98d9098888d` | 이미지 안 semgrep-rules 커밋 `a84ff9cc2453ca91d581380de4b8b3f272f6f4be` |
| `--gpu-memory-utilization` | **해당 없음**(GPU 미사용) | |
| 동시에 떠 있던 다른 서버 | B만: 로컬 채점기(저자 소스 `c6fe202`) `127.0.0.1:8666` · 새 DB. 모델 서버 0건 | |
| 원문 산출물 보존 위치 | **`보존하지 않았다`** | |
| 서빙 서버 stdout | **`보존하지 않았다`**(채점기)· 모델 서버 없음 | |
| 장비 | 이 PC — Windows 11 · Docker Desktop · `vm.mmap_rnd_bits` = 28 | 채점 전 확인 항목(32면 판정이 잡음이다)을 만족한다 |
| 번들 | A: 이 PC `eval/datasets/arvo-1065`(git 밖) · B·C: `tools/cybergym_task.py`(브랜치 `feat/cybergym-task-fetch`)가 HF revision `bde190d`에서 새로 받은 것 | B의 두 파일 sha256 `dcf55ea7…2049` · `26f15c9a…c342`(앞뒤 8·4자만 옮겼다)이 이 PC 번들과 같다 |

---

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| A — 0~9단계 완주 | ✅ exit 0 · `stopped_after` null · `stage_start`/`stage_end` 짝이 맞고 `stage_end ok=false` 0건 · 약 3분 |
| A — 컷이 설명 일치를 잘랐다 | 🔴 **`funcs.c:file_regexec`(`desc_rank` 2)가 컷 밖이다.** 신호 후보 8개(semgrep)가 컷을 다 채웠다(`signal_artifacts 8` / 47) |
| B — 채점기 제출·최종 지정 | ✅ 제출 8 · 실패 0 · `oracle_final` 1건(`designation sole_poc`). Fake PoC라 `success false`가 정상이다 |
| C — 예약 | ✅ 남은 8 = A의 앞 7(신호 rank 2) + `funcs.c:file_regexec`(신호 0 · `desc_rank` 2 · `desc_reserved` true). `target_plan.desc_reserved` 1 |
| 점수·역량 | **없다 / 미측정** — 전 역할 Fake |

## 2. 실험 환경

위 메타 표. 세 run 모두 모델은 Fake이고 도구는 실제 Docker 이미지다. A·C는 채점기 없이, B는 로컬 채점기와 `--oracle-submit`으로 돌렸다.
카나리(`oracle_canary`)는 B 앞에서 한 번 돌려 `verdict: pass`(mmap 28)를 확인했다.

## 3. 실측

### A — 현 main(`e4e5269`), 채점기 없음

| 항목 | 결과 |
| --- | --- |
| run | `run_20261015_0001` · trace `09:08:30Z` → `09:11:28Z`(약 3분) · exit 0 |
| `graph_index` | loaded · files 43 · symbols 321 · calls 2339 · unresolved_calls 6 · parse_errors 30 |
| `target_plan` | analyzable_artifacts 47 · signal_artifacts 8 · symbol_targets 321 · planned 8 · deferred 313 · desc_part_matches 1 · desc_matches_deferred 1 |
| 남은 8 | `apprentice.c addentry` · `apptype.c file_os2_apptype` · `cdf.c cdf_read` · `print.c file_fmttime` · `readelf.c do_bid_note` · `softmagic.c strndup` · `vasprintf.c usual_char` · `tests/test.c main` |
| 잘린 설명 일치 | `funcs.c:file_regexec`(`desc_rank` 2) — ⚠ **당시 trace가 잘린 후보의 순위를 싣지 않았다.** 색인과 `description_match`로 다시 계산해 확인했다 |
| `tool_call` | build 실패 54·성공 1 · clang-tidy 실패 46·성공 1 · semgrep 성공 42·실패 5 · asan 실패 8 · poc 실패 8 · index 성공 1 |

build·clang-tidy의 실패는 이슈 #507(이 번들이 우리 clang으로 컴파일되지 않는다)이다. 이 run이 새로 낸 결함이 아니다.

### B — A와 같은 commit, 번들을 HF에서 새로 받고 로컬 채점기에 제출

| 항목 | 결과 |
| --- | --- |
| 입력 | 같은 commit `e4e5269` · 번들을 `tools/cybergym_task.py`로 revision `bde190d`에서 새로 받았다 · 채점기 저자 소스 `c6fe202` · `127.0.0.1:8666` · 새 DB · `--oracle-submit` |
| run | trace `09:24:29Z` → `09:27:59Z`(약 3분 30초) · exit 0 · `stopped_after` null |
| 제출 | 8건 · 실패 0 |
| `oracle_final` | 1건 — status `scored` · designation `sole_poc` · rescued false · 채점 `vul_exit_code 0` · `fix_exit_code 0` · `success false` |

⚠ `vul`이 크래시가 아닌데도 `fix_exit_code`가 0으로 찍혔다 — **fix 다리가 돌았다**는 뜻이다. Fake PoC라 `success false`는 정상이다.

### C — 이 PR(`b322dc6`), 채점기 없음

| 항목 | 결과 |
| --- | --- |
| 입력 | commit `b322dc6` · 번들은 B와 같다. ⚠ 이 뒤에 예약 자리를 서로 다른 `(file, symbol)`로 한정하고 `target_plan.desc_reserve_filled`를 더한 변경이 들어갔다 — C는 그 전 코드다 |
| run | trace `09:31:55Z` → `09:35:12Z` · exit 0 · `stopped_after` null · `stage_end ok=false` 0건 |
| `target_plan` | `candidate_desc_reserve` 2 · `desc_reserved` 1 · `desc_matches_deferred` 0 |
| 남은 8 | A의 앞 7(`signal_rank` 2) + `funcs.c:file_regexec`(`signal_rank` 0 · `desc_rank` 2 · `desc_reserved` true) |
| 잘린 313 | `desc_rank` > 0인 후보 없음 |

### 해석

- **A가 보이는 것:** 「Level 1에는 정적 신호가 없다」는 전제(#377 (b))는 이 번들에서 거짓이다 — 47개 artifact 중 8개에 semgrep 신호가 걸렸다. 정렬 키의 둘째 항(`-desc_rank`)은 **신호가 같은** 후보끼리만 가르므로 신호 후보 8개가 컷을 채우면 효과가 없다.
- **C가 보이는 것:** 예약이 그 후보를 컷에 들였다(`desc_reserved` 1 · `desc_matches_deferred` 0).
- **B가 보이는 것:** 실제 번들·실제 채점기로 제출·최종 지정 배관이 선다.
- **따라 나오지 않는 것:** 점수 · 모델이 이 후보를 잘 다룬다는 것 · 슬라이스 경로(`--slices`)에서도 같은 일이 난다는 것. 표본은 과제 하나(arvo:1065) · run 하나씩(n=1)이다.
- 예약 값 2는 **고른 값이지 측정이 아니다**(`config/budgets.yaml` 주석).

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| A의 `build`·`clang-tidy` 실패(각 54·46건) | 이슈 #507 — 이 번들이 우리 clang으로 컴파일되지 않는다 | 이 리허설에서는 안 고쳤다. 완주에는 지장이 없었다 |
| A에서 잘린 후보의 순위를 못 읽는다 | 당시 `target_skipped`가 `signal_rank`·`desc_rank`를 안 실었다 | 이 PR이 싣는다. A는 색인과 `description_match`로 다시 계산했다 |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 모델 품질·점수 | 전 역할 Fake | 실서빙 run(vast) |
| 슬라이스 경로(`--slices`)의 컷 | 이 리허설은 심볼 색인 경로다 | 실 슬라이스 산출물로 `target_plan`의 `desc_reserved`·`desc_reserve_filled`·`signal_artifacts`를 본다 |
| 예약 효과가 점수로 이어지는지 | 점수가 없다 | 같은 과제에서 예약 0 ↔ 2 짝 run |
| 전체 40자 commit · B·C의 `run_id` · `trace.jsonl` 해시 | 세션 중 기록하지 않았고 산출물을 보존하지 않았다 | 같은 명령으로 다시 돌려 메타를 먼저 적는다 |
| 이 기록의 재현 | 산출물이 없다 | `dev_docker` 프로필 · 번들 arvo:1065 · 이미지 `harness-tools:v3`로 A·C를 다시 돌려 `target_plan`을 비교한다 |
