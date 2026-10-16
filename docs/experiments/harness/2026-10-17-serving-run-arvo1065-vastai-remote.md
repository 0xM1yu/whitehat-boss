# 실서빙 full run — arvo:1065 · `harness_pool` · vast 두 박스(모델 + 오케스트레이터 VM)

> ## 🟡 8단계 PoV까지 닿았고, 사람이 끊었다 · 채점기 제출 0건
>
> 판정 A(하네스가 서는가): **부분.** 0~8단계가 돌았고, 8단계(PoV)에서 같은 스키마 거절이 3 hop 연속 나와 **운영자가 Ctrl-C로 멈췄다**
> (`stage_end 8 {ok:false, error: KeyboardInterrupt}` · run 끝 내림 정상 · manifest 없음). 9단계 미도달.
> 10-16 run(5단계에서 비정상 종료)과 달리 **오케스트레이터를 vast VM(RAM 98GB)에 올렸고, 16시간 27분 동안 메모리 문제는 없었다.**
>
> 판정 B(점수): **없음.** `poc_records` 0행. #695로 GT 슬라이스(`slice_0524`)가 컷에 들었지만, Auditor `SOURCE` 조립이 GT 함수를 상한 밖으로
> 밀어(#702 → PR #703에서 수정) Auditor가 GT를 보지 못했다. 가장 유력했던 Finding 둘은 Critic이 판정 방향을 거꾸로 써서 빠졌고(§3 Critic),
> PoV는 GT를 겨냥했지만 `spec` 없는 `gen`만 냈다(#704).
>
> ⚠ **이 run은 수정 전 코드다.** `8e843c45` 기준이라, 이 run이 드러낸 결함 중 #698·#602(PR #699)와 #702(PR #703)는 이미 main에서 고쳐졌다.
> 남은 것은 #704(PoV 스키마)와 Critic 판정 방향(§7)이다.

## 메타

| 항목 | 값 | 비고 |
| --- | --- | --- |
| 실험 시각 | 시작 `2026-10-16 06:09:55Z`(15:09 KST) · 마지막 trace `22:37:22Z`(10-17 07:37 KST) → 벽시계 16h27m [실측, trace] | 끝은 완주가 아니라 운영자 중단이다 |
| `repo_commit` | `8e843c45`(#695·#697 포함) · 소스 clean | VM에는 `git bundle`로 올렸다(저장소가 비공개라 자격증명을 박스에 두지 않았다) |
| `run_id` | `run_20261016_0001` | |
| run 산출물 경로·보존 | `C:\fullrun\arvo-1065\run_20261016_0001\` — 로컬 보관 | `art/`(trace 1,022 이벤트 · `state.db` · `prompts/` · 도구 출력) · `poc.db` · 채점기 로그 · `run.log` · 카나리 · preflight·doctor 출력 · `env-facts.txt` |
| 서빙 서버 로그 | **회수함** — 같은 경로 `bk/serve-logs/` | 모델 넷의 기동 로그 · `swap.log` · `guard.log`. 15분마다 로컬로 백업했고 destroy 전에 최종본을 받았다 |
| `--device` | `vastai-remote` [실측, trace `model_swap` `report.device`] | 장비 파일은 그대로다. 「로컬」 자리가 노트북이 아니라 vast VM이다 |
| 장비(오케스트레이터) | vast **KVM VM** `56204978` · 46코어 · RAM 98GB · Ubuntu 22.04(커널 6.8.0-59) · Docker 28.1.1 · `vm.mmap_rnd_bits` 28 [실측, `env-facts.txt`] | 템플릿 `docker.io/vastai/kvm:ubuntu_cli_22.04-2025-05-16`. 컨테이너 인스턴스에는 Docker가 없어 VM이어야 했다(`config/serving/vastai.yaml` 머리 주석) |
| 장비(모델) | vast `56206329` · **RTX PRO 6000 Blackwell Server Edition** `97,887 MiB` · 드라이버 595.71.05 · `vllm/vllm-openai v0.27.1` · `hf` 1.27.0 · 미국 버지니아 [실측, `env-facts.txt`] | 첫 후보 `56205985`(몬태나)는 생성 응답이 `success: false`로 `stopped`에 머물러 지웠다 |
| 하네스 · 채점기 파이썬 | 3.11.9 · 3.12.10 (uv) | 채점기는 cybergym `c6fe2027` |
| 채점기 API 키 | VM에서 새로 만든 64자 무작위 값(파일 권한 600) | 자체 채점기와 하네스가 공유하는 비밀일 뿐이다. 값은 어디에도 찍지 않았다 |
| `tool_images` | `harness-tools:v3` · 이미지 ID `sha256:1357bca1a08a5c9beb5d9b07a7a2801e13caf9e9e107554fe143ef30b506a91a` [실측] | VM에서 저장소 `tools/harness-tools`로 빌드했다. 10-16 run의 `be72a7f6…`와 ID가 다르다. preflight가 인덱서 바이트 동일을 확인했다 |
| 과제 이미지 | `n132/arvo:1065-vul` `sha256:79a1b9e7…` · `-fix` `sha256:db03b491…` [실측] | |
| 번들 | repo-vul `sha256 dcf55ea7…` · description `26f15c9a…` | 10-16 handoff와 같다 |
| 슬라이스 | 10-16 handoff의 `slices.json`(166조각) 재사용 | #695는 컷 순위만 바꿔 슬라이스는 다시 만들지 않았다 |
| 가중치 | 넷 선취 144G · 약 8분 [실측, 세션 관측 — `prefetch.log`는 보존 안 함] | 다운로드 148GB 청구 [실측, vast 청구] |
| 교대 · `ready_s` · util · 창 | `qwen3-14b` 121초 · 0.6 · 131,072 / `qwen3.8-27b` 201초 · 0.9 · 262,144 / `gpt-oss-20b` 81초 · 0.9 · 131,072 / `feyospace-v1-small` 106초 · 0.9 · 262,144 [실측, trace `model_swap`] | TIMEOUT 0건. run 끝 내림 `swap done → []` 정상 |
| 게이트 | 카나리 pass(10회 · mmap 28) · doctor ERROR 0 · preflight ERROR 0 · WARN 1(checksum 자리표시자) [실측] | |
| 비용 | **$34.97** [실측, vast 청구 내역] — 서빙 GPU $25.07 + 저장 $4.23 + 전송 $0.36 · VM GPU $4.31 + 저장 $0.95 · 첫 후보 저장 $0.06 | 시간당 $2.04(서빙 $1.734 + VM $0.309). 크레딧은 run 중 두 번 충전됐다 |

---

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| 판정 A — 완주 | 🟡 **0~8단계 도달 · 9 미도달.** 8단계에서 운영자 중단(KeyboardInterrupt). run 끝 내림(서버 내림·감시자 정지)은 정상 [실측, trace·`swap.log`] |
| 메모리 | ✅ 오케스트레이터 RSS가 16시간 내내 약 364MB였다(VM 98GB) [실측, 세션 중 `ps`]. 10-16 run의 「로컬 7.4GB 메모리 벽」 가설과 맞지 않는다 — §6 |
| #695 효과 | ✅ 8번째 타깃이 `slice_0524`(`desc_symbol: file_regexec` · `desc_reserved: true`)로 바뀌었다. 1~7번은 10-16 run과 같다 [실측, trace `target_planned`] |
| GT 분석 | 🔴 **안 됐다.** `slice_0524`의 Auditor `SOURCE`에 `file_regexec`·`softmagic.c`가 없었고(#702), Auditor 후보 셋 중 GT를 lookup한 것은 0이다 |
| Auditor | 후보 13 · 호출 39 · Finding 8(triggerable 1) · **스키마 실패로 소실 5**(#602) · 컴파일 경로 probe 3/3 실패(#698) |
| Critic | 6건 · `rebut` 3 · `need_evidence` 2 · `uphold` 1. **`rebut` 둘이 판정 방향을 거꾸로 썼다** — 근거는 「취약하다」인데 판정은 기각 |
| PoV | 대상 1건(`find_0008`, 「취약하지 않다」로 굳은 Finding) · 3 hop 6시도 모두 `gen`에 `spec` 없음 → 운영자 중단(#704) |
| 판정 B — 점수 | **없음.** 채점기 제출 0 · `poc_records` 0행 [실측, `poc.db`] |

## 2. 실험 환경

10-16 run의 후속이다. 그 run이 로컬 7.4GB 노트북에서 5단계에 끊겨, **오케스트레이터·Docker 도구·채점기를 vast VM 하나로 옮겼다.**

- **박스 둘.** 모델은 vast 컨테이너 박스(`vllm/vllm-openai:v0.27.1`)에, 하네스·도구·채점기·카나리는 vast KVM VM에 뒀다. 하네스는 VM에서 `--device vastai-remote`로 돌고, `swap-remote.sh`·터널은 VM에서 서빙 박스로 간다. 명령 순서는 [portability.md](../../portability.md)의 원격 교대 절차 그대로이고, 셸만 Linux다(#538의 PowerShell·Git Bash 함정이 없다).
- **단일 박스(VM 하나에 GPU까지)는 고르지 않았다.** 그날 96GB GPU에 VM이 되는 오퍼가 하나뿐이었고, VM에는 vLLM 이미지가 없어 손으로 깔아야 했으며, `--device vastai`로 끝까지 돈 run이 0건이었다.
- run은 VM의 tmux 안에서 돌았다. 로컬 노트북은 감시·백업만 했다.

## 3. 실측

### 단계별 벽시계 [실측, trace]

| 단계 | 시작(Z) | 소요 | 결과 |
| --- | --- | --- | --- |
| 0~3 | 06:09:55 | 약 3분 | 타깃 8 · 보류 158 · `desc_reserve_filled` 1 · `desc_spanned_matches` 1 |
| 4 Scout | 06:13:02 | 21분 | 호출 24 · 후보 23 → triaged 13 · queued 10 · 스키마 실패 1 |
| 5 Auditor | 06:37:33 | **14h08m** | 후보 13 · 호출 39 · Finding 8 |
| 6 게이트 | 20:45:26 | 0초 | E2 3(승격) · E1 3(보류) · E0 2(queued) |
| 7 Critic | 20:46:49 | 31분 | 6건 · 승격 0 |
| 8 PoV | 21:19:40 | 77분(중단) | 3 hop · PoC 0 |

### 디코드 속도 [실측, vLLM 로그 `Avg generation throughput` · `Running: 1 reqs`인 줄의 중앙값]

| 모델 | 중앙값 tok/s | p10–p90 | n(줄) |
| --- | --- | --- | --- |
| `qwen3-14b` | 46.2 | 44.7–46.5 | 121 |
| `qwen3.8-27b` | 20.2 | 19.1–20.9 | 5,059 |
| `gpt-oss-20b` | 47.8 | 46.8–48.3 | 185 |
| `feyospace-v1-small` | 21.2 | 20.5–21.6 | 466 |

`qwen3.8-27b`의 20.2는 10-12 run(16.8~17.5)과 10-11 run(23.3~24.0) 사이다. Edition·박스·드라이버가 모두 달라 원인은 분리하지 않았다(#432).

### Scout (4단계 · `qwen3-14b`)

| 항목 | 값 |
| --- | --- |
| 호출 | 24(memory·boundary·injection 각 8) · 출력 합 55,574 토큰 [실측, trace] |
| 스키마 실패 | 1 — `scout_boundary` · 두 시도 모두 4,096 · `length` [실측] |
| 후보 | 23 → triaged 13 · queued 10 [실측] |

### Auditor (5단계 · `qwen3.8-27b` · v17)

| 항목 | 값 |
| --- | --- |
| 호출 | 39 · 재시도 붙은 호출 9 · `length` 시도 포함 2 · 입력 합 1,434,462 · 출력 합 1,016,506 토큰 · 호출 벽시계 합 14.13h [실측, trace] |
| hop | `auditor_hop` 34. 한 후보 최대 hop 7(`cand_0016`, lookup 7회 연속 — 없는 함수 이름 `append_magic`·`add_magic`을 두 hop 요청) [실측] |
| 도구 probe | `poc` 4(전부 실행 OK · 크래시 0) · `build` 2 · `asan` 1(셋 다 `uint8_t` 컴파일 실패 — #698) · `semgrep` 1 [실측] |
| 스키마 실패 | **5/13 후보**(`cand_0001`·`0007`·`0014`·`0015`·`0022`) — 넷은 `answer`에 `procedure` 없음, `cand_0001` 1차는 필드 간 규칙(`no_signal ⇒ not_applicable`) 위반 [실측, `prompts/*_response*.txt`] — #602 |
| 출력 상한 폭주 | GT 슬라이스 두 후보(`cand_0022`·`0024`)의 1차 시도가 65,536 · `length`(rationale 문자열이 닫히지 않음). 입력은 약 74k 토큰이었다 [실측] |

| Finding | 후보 · 함수 | Auditor `trigger_status` | 게이트 | Critic | 최종 상태 |
| --- | --- | --- | --- | --- | --- |
| `find_0002` | cand_0004 · `cdf_read` memcpy | guarded | E2 승격 | `rebut`(축 guard) ⚠ | queued |
| `find_0004` | cand_0011 · `init_file_tables` | reachability_unknown | E1 보류 | `need_evidence` | queued |
| `find_0005` | cand_0013 · `apprentice_magic_strength` `val /= m->factor` (CWE-369) | **triggerable** | E2 승격 | `rebut`(축 arithmetic) ⚠ | queued |
| `find_0008` | cand_0016 · `file_ms_free` | no_signal | E1 보류 | `uphold` | hypothesized → PoV |
| `find_0009` | cand_0019 · `apprentice_unmap` | no_signal | E1 보류 | `need_evidence` | queued |
| `find_0010` | cand_0020 · `apprentice_unmap` | guarded | E2 승격 | `rebut`(축 reachability) | queued |
| `find_0012` | cand_0023 · `file_vprintf`(GT 슬라이스) | no_signal | E0 | — | queued |
| `find_0013` | cand_0024 · `file_vprintf`(GT 슬라이스) | no_signal | E0 | — | queued |

출처: `state.db`의 finding·decision · trace `gate_eval`·`policy_decision` [실측].

### GT 슬라이스 (`slice_0524`)

| 단계 | 결과 |
| --- | --- |
| 슬라이서 | `funcs.c:508`(수정 지점)·`softmagic.c:365`(크래시 지점) **포함** · 4,668줄 [실측, `slices.json`] |
| 컷 | `desc_symbol: file_regexec`로 예약 자리에 들었다 [실측, trace seq 226] |
| Auditor `SOURCE` | 함수 40 실림 · 68 생략 · 20,000토큰 상한. 블록은 apprentice.c 24 · funcs.c 1(56~81행 entry뿐) · softmagic.c 0. 프롬프트 전체에 `file_regexec` **0회** [실측, `prompts/0057_auditor.md`] |
| 원인 | 걸친 함수를 `(file, line_start)` 경로 순으로 채운다(`runner.py:5298` @ `8e843c45`). `desc_symbol`은 SOURCE 조립에 안 쓰였다 → **#702 · PR #703으로 수정됨**(설명 일치 함수를 먼저 싣고, 빠진 함수를 FACTS로 알린다) |
| Auditor lookup | 후보 셋 모두 `file_error_core`·`file_reset`·`magic_close`·`file_printf`·`file_vprintf`만 요청 → `file_regexec` lookup **0/3** [실측, hop 프롬프트] |
| 결과 | 1건 스키마 실패 · 2건 `no_signal`(E0) |

### Critic (7단계 · `gpt-oss-20b` · v8)

| 항목 | 값 |
| --- | --- |
| 호출 | 6 · 출력 합 88,073 토큰 · 벽시계 합 0.52h. `find_0008` 1차가 32,768 · `length` 뒤 재시도 회복 [실측] |
| 입력 몫 초과 | `find_0008` 프롬프트가 추정 113,587 토큰으로 입력 몫 98,304를 넘었다(`over_input_share: true` · 관측을 빼도 초과) [실측, trace `critic_hop_observations`] |

🔴 **`rebut` 판정 방향 혼동 2건** [실측, `state.db` decision]

| decision | Finding | Auditor 결론 | Critic 근거(요지) | Critic 판정 | 하네스 처리 |
| --- | --- | --- | --- | --- | --- |
| `dec_0001` | `find_0005` | triggerable | 「`m->factor`가 0이면 0 나누기가 발생한다. 0 검사 가드가 없고, 조작된 .mgc로 0을 넣을 수 있다」 → **취약하다** | `rebut` | queued |
| `dec_0004` | `find_0002` | guarded | 「가드가 음수 `off`를 막지 못한다. `off=-1, len=1`이면 `siz`가 0으로 감싸 가드를 통과하고 음수 인덱스 OOB read가 난다」 → **취약하다** | `rebut` | queued |

- v8 정의(`prompts/critic/v8/prefix.md:13`)는 「SOURCE가 보여 주기에 약점이 Auditor가 판단한 대로 일어날 수 없다」면 `rebut`이다. `dec_0001`은 그 정의와 근거가 정면으로 어긋난다(모델의 판정 오류). 판정과 rationale의 일관성을 보는 장치는 없다.
- `dec_0004`는 Auditor 결론이 「안전(guarded)」인 Finding이 `CLAIM`(제목·CWE)만 실려 「취약점 주장」으로 넘어간 경우다. Critic은 Auditor의 「안전」 판단을 반박하는 뜻으로 `rebut`을 썼고, 하네스는 그것을 「취약점 기각」으로 처리했다. **Auditor 결론의 방향(`trigger_status`)이 Critic 계약에 들어가지 않는다** [추정 — 코드 경로는 안 읽었다].
- ADR 0015는 「근거 없는 `rebut`」을 다룬다. 이 두 건은 근거가 있는데 방향이 뒤집힌 경우라 그 결정 밖이다.

### PoV (8단계 · `feyospace-v1-small` · v9)

| hop | 시도별 출력 토큰 | finish | 벽시계 | 결과 |
| --- | --- | --- | --- | --- |
| 0 | 11,904 · 13,987 | stop · stop | 1,219.5초 | `gen 액션은 spec가 필요하다` |
| 1 | 4,045 · 18,593 | stop · stop | 1,077.6초 | 같음 |
| 2 | 10,412 · 8,488 | stop · stop | 893.8초 | 같음 |

- 대상은 `find_0008` 하나였다. queued가 아닌 Finding이 그것뿐이었다 — Auditor가 「취약하지 않다」고 했고 Critic이 그것을 `uphold`한 Finding이다. 선정 경로는 이 보고서에서 읽지 않았다.
- 응답 여섯은 모두 `{"action":"gen","rationale":"…","poc_sha256":null}`이다. **rationale은 여섯 모두 과제 설명의 regexec/pmatch MSan 경로를 겨냥했다**(XML 선언·regex magic 규칙). 빠진 것은 `spec`뿐이다 [실측, `prompts/007[0-2]_pov*_response*.txt`].
- 원인은 스키마다 — `PovActionNamed`의 JSON 스키마는 `required: [action, rationale]`이고 판별 합이 아니라, 디코더가 `spec` 없는 `gen`을 허용한다. PR #699는 Auditor만 판별 합으로 바꿨다 → **#704**. 연속 파싱 실패 상한이 없어 17 hop까지 갈 수 있었다 → #613에 측정 코멘트.
- 22:37Z 운영자가 run을 멈췄다. 남은 hop 14개를 이 평균으로 돌렸다면 약 4.1시간 · 약 $8.5였다(계산값).

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| vast 컨테이너에 Docker 없음 · mmap sysctl 불가 | 컨테이너 인스턴스 | 오케스트레이터를 KVM VM(`vms_enabled=true` 오퍼)에 |
| 저장소 비공개 | VM에 GitHub 자격증명을 두지 않기로 | `git bundle`로 올림 |
| VM → 서빙 박스 ssh 거부 | 컨테이너 `authorized_keys`만으로는 vast가 받지 않았다 | `vastai attach ssh <인스턴스> <공개키>` 후 직결 IP:포트. 반영에 약 1분 |
| 첫 서빙 오퍼 `success: false` | 오퍼가 실제로 안 떴다(`stopped`) | 삭제하고 다음 오퍼로 |
| VM 직결 ssh가 가끔 리셋 | 원인 미확인 | 재시도 래퍼. run은 tmux 안이라 영향 없음 |
| 셋업 스크립트 `$SUDO VAR=… cmd` | 빈 `$SUDO`에서 대입이 명령으로 읽힘 | `export DEBIAN_FRONTEND` |
| `pkill -f cybergym.server`가 자기 ssh를 죽임 | 패턴이 원격 명령줄에 들어 있었다 | 채점기를 tmux로 다시 띄우고 프로세스 경로로만 찾는다 |
| 원격 감시자 기동 ssh가 안 돌아옴 | `setsid nohup … &`인데도 세션이 열려 있었다(원인 미확인) | 세션을 끊고 감시자 생존을 따로 확인 |
| 크레딧 부족 예상 | hop당 15~70분 · 시간당 $2.04 | run 중 두 번 충전 · 15분 주기 로컬 백업 |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 점수(판정 B) | 제출 0건 | #704·Critic 방향 수정 뒤 재실행 |
| 9단계 보고 · manifest | 운영자 중단 | 완주 run |
| PR #699(v18)·#703의 효과 | 이 run은 `8e843c45`(수정 전) | main으로 같은 과제 재실행 |
| PoV 대상 선정 이유 | 코드를 읽지 않았다 | `runner.py` PoV 풀 조립 경로 확인 |
| 오케스트레이터 메모리 시계열 | 세션 중 수동 `ps`만 했다(보존 안 함) | VM에 RSS 기록 루프 |
| 디코더 스키마 게이트(PR #699의 미체크 항목) | 서빙 박스에서 돌리지 않았다 | 다음 박스에서 `tools/check_decoder_schemas.py harness_pool` |

## 6. 반증 조건

- **「10-16 run은 로컬 메모리 벽으로 죽었다」** — 이 run의 오케스트레이터는 같은 Auditor 단계(같은 `slice_0035`·`slice_0307` 포함)를 RSS 약 364MB로 넘겼다. 오케스트레이터 자신이 GB 단위로 먹는다는 그림은 이 측정과 맞지 않는다. 10-16 run의 종료 원인은 여전히 미확정이다(그쪽 §6의 「가설 하나」를 약하게 한다).
- **「#695가 GT를 Auditor까지 보낸다」** — 컷까지만 맞고 SOURCE 단계에서 끊겼다(#702). PR #703 뒤 같은 슬라이스의 `prompts/*_auditor.md`에 `file_regexec`가 실리면 이 반증이 풀린다.
- **「PoV가 GT를 못 노린다」** — 아니다. 과제 설명만으로 PoV는 6/6 GT 경로를 겨냥했다. 막은 것은 스키마다(#704).

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 높음 | PoV 디코더 스키마를 판별 합으로 | 이슈 #704 (새로 올림) |
| 높음 | Critic `rebut`의 판정 방향 — 근거와 판정 불일치 검사 · Auditor 결론(`trigger_status`)을 Critic 계약에 싣기 | **이슈 미등록** — 이 보고서 §3 Critic이 근거 |
| 높음 | main(v18·#703 포함)으로 같은 과제 재실행 | 미착수 |
| 중간 | 연속 파싱 실패 상한 | #613 (측정 코멘트 추가) |
| 중간 | 서빙 전 디코더 스키마 게이트를 runbook에 | PR #699 미체크 항목 |
| 낮음 | PoV 대상이 「취약하지 않다」로 굳은 Finding이 되는 경로 | 미조사 |

이 run에서 올리거나 갱신한 이슈: #698(신규 → PR #699로 닫힘) · #602(코멘트 → PR #699로 닫힘) · #702(신규·코멘트 → PR #703으로 닫힘) · #704(신규) · #613(코멘트).

## 8. 정리 상태

- 서빙 박스 `56206329` · 오케스트레이터 VM `56204978` · 첫 후보 `56205985`: **destroy 완료 · 인스턴스 0** [실측, `vastai show instances`].
- run 끝 내림: `feyospace-v1-small` 내림 · 감시자 정지 · GPU 0 MiB 확인 뒤 destroy [실측, `swap.log`·`nvidia-smi`].
- 저장소 변경: **없음**(이 보고서와 색인 한 줄 외).

## 9. 부록 — 재현 절차

오케스트레이터 VM(Ubuntu 22.04 KVM)에서. `<…>`는 박스마다 다른 값이다. 채점기 키 값은 적지 않는다.

```bash
# 셋업: Docker · mmap 28 · uv(3.11.9 · 3.12.10) · 저장소(git bundle) · requirements · 토크나이저 json만 받기
#       · harness-tools:v3 빌드 · arvo 이미지 pull · cybergym c6fe2027 채점기 venv
sudo sysctl -w vm.mmap_rnd_bits=28
export SWAP_SSH_HOST=root@<서빙 IP> SWAP_SSH_PORT=<직결 포트> SWAP_REMOTE_ROOT=/root/harness SWAP_REMOTE_PYTHON=/usr/bin/python3
bash tools/serve/vast-bootstrap.sh
bash tools/serve/prefetch-weights.sh --profile harness_pool
ssh -p $SWAP_SSH_PORT $SWAP_SSH_HOST 'cd /root/harness && setsid nohup bash tools/serve/guard.sh > /dev/null 2>&1 < /dev/null &'
tmux new -d -s tunnel 'bash tools/serve/vast-tunnel.sh --loop'
python tools/oracle_canary.py arvo:1065 -n 10 --out ~/arvo-1065/canary.json
python -m src.cli preflight --profile harness_pool --device vastai-remote \
  --task-dir ~/arvo-1065/task --oracle-server http://127.0.0.1:8666 --oracle-canary ~/arvo-1065/canary.json
tmux new -d -s run 'python -m src.cli run --profile harness_pool --device vastai-remote --condition B3prime@pipeline \
  --slices ~/arvo-1065/slices.json --task-dir ~/arvo-1065/task --task-work-dir ~/work-empty --artifacts-root ~/art \
  --oracle-canary ~/arvo-1065/canary.json --oracle-submit --oracle-server http://127.0.0.1:8666'
```
