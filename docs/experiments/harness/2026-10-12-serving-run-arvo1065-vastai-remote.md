# 실서빙 run — arvo:1065 · vast RTX PRO 6000 Blackwell Server Edition(Virginia, US)

> **무엇이 처음인가.** **Critic을 지난(7단계 `need_evidence` 보류) Finding에서 8단계 PoV가 실모델로 돌아 PoC 넷을 만들고 넷 다 실채점기에 닿았다.** ⚠ 「PoV가 채점기에 닿은 것」 자체는 처음이 아니다 — [10-08 run](2026-10-08-first-real-serving-run-arvo1065-vastai-remote.md) §1·§5의 PoV는 Critic을 거치지 않은 E0 Finding 풀에서 돌아 7건을 제출했다. 처음인 것은 **7단계를 지난 Finding에서** 8단계가 돌아 닿은 것이다.
> 2026-10-11 run은 같은 과제에서 Critic이 Finding 전부를 `need_evidence`로 내 PoV 풀이 비었고
> 8단계가 `stage_end 0초`로 끝났다([그 보고서](2026-10-11-serving-run-arvo1065-vastai-remote.md) §3).
> 이번 run은 **Critic이 또 6/6 `need_evidence`인데도** 8단계가 3시간 44분 돌았다 — PR #608이
> 보류 Finding을 PoV 풀에 들인 효과가 여기서 처음 측정됐다.
>
> **그리고 점수는 0이다.** PoC 넷의 `vul_exit_code`가 전부 `0`이고, 모델이 `submit`을 내기 전에
> 감시자 마감이 run을 끊어 **최종 지정이 0건**이다.
>
> **선취가 콜드 기동 상한을 처음 넘겼다.** 좌석 다섯이 전부 상한 안에 떴다 — 10-11에 두 번
> TIMEOUT한 `gpt-oss-20b`가 **67초**다([#571](https://github.com/0xM1yu/whitehat-boss/issues/571)).

## 메타

| 항목 | 값 |
| --- | --- |
| run | `run_20261011_0001` · `2026-10-11T16:48:12Z` → `2026-10-12T02:42:49Z` · **9시간 54분 37초** |
| 종료 | `stopped_after: 8` · 전이 규칙 `R-2` → `finished` · 사유 **`model_gateway_unavailable`**(감시자 마감) |
| `repo_commit` | `1b9d14cad69baa4e674da2fc87bd945d1141d751` · **dirty 아님** |
| 프로필 · 장비 · 조건 | `harness_pool` · `vastai-remote` · `B3prime@pipeline` |
| 과제 · 슬라이스 | `arvo:1065` · `arvo-4.json`(4건) · `slicer.status: partial` |
| 프롬프트 판 | scout `v4` · auditor **`v14`**(10-11은 v13) · critic `v6` · pov `v5` |
| 서빙 모델 | `qwen3-14b`(scout_memory·scout_injection) · `seccoderx-8b`(scout_boundary) · `qwen3.8-27b`(auditor) · `gpt-oss-20b`(critic) · **`feyospace-v1-small`(pov — 이 과제 run에서 처음 떴다)** |
| GPU | vast **55417596**(offer `53005992`) · RTX PRO 6000 Blackwell **Server** Edition · 97,887 MiB · 드라이버 615.71.09 · CUDA 13.4 · 96 코어 · RAM 999 GiB · 디스크 300 GB · Virginia US |
| 실시급 | **$1.7167/hr** = 기본 $1.4667 + 저장 $0.25(300 GB). 매물 표시는 $1.471이고 **저장료가 빠진 값이다** |
| 배선 | 하네스·도구·채점기 **로컬**(Windows 11 · Python 3.14.5) · 서빙만 원격 · ssh 터널 `8000·8001·8002·8003·8006` |
| ssh | **직결** `root@<host-ip>:40413`(`direct_port_start`) · 프록시 미사용 · `--loop` 재연결 터널 · **전송 끊김 0건** |
| 셸 | PowerShell + `Git\usr\bin`을 PATH 앞에([#538](https://github.com/0xM1yu/whitehat-boss/issues/538)) · run은 `Start-Process`로 완전 분리 |
| 도구 이미지 | `harness-tools:v3` · 인덱서 index-v4(preflight가 저장소 파일과 바이트 동일 확인) |
| 채점기 | 저자 소스 `c6fe202` · `127.0.0.1:8666` · **새 `--db_path`** `C:\w\oracle-run09\poc.db` |
| 카나리 | `canary-arvo-1065-20261012.json` · `verdict: pass` · 네 조합 x10 · `139` 0건 · `vm.mmap_rnd_bits 28` · `boot_id a94d7c1e...` |
| preflight | **ERROR 0 · WARN 1 · OK 13** — WARN은 `model_checksums` 자리표시자 |
| 🔴 비교표 적격 | **아니다** — scout 셋·auditor·pov의 `checksum`이 0이다([CLAUDE.md](../../../CLAUDE.md) §0) |
| 산출물 보존 | `C:\w\s17-artifacts\run_20261011_0001\` · 채점기 `C:\w\oracle-run09\` · 원격 로그 `C:\w\s17-results\remote-logs\` — 🔴 **전부 저장소 밖이고 커밋되지 않는다.** 이 보고서의 수는 그 디렉터리가 원천이다 |

## 1. 단계별

| 단계 | 벽시계 | 결과 |
| --- | --- | --- |
| 0 preprocess | 7.5초 | ok · 토큰 카운터 전부 `approx`(`file_absent`) |
| 1 index | 2.2초 | ok |
| 2 signals | 7.1초 | `semgrep` **ok**(exit 1 = 히트) · **`clang-tidy` 실패**(`대상이 컴파일되지 않는다`) · **`build` 실패**(exit=1) → [#507](https://github.com/0xM1yu/whitehat-boss/issues/507) **3회째 재현** |
| 3 retrieval | 0.2초 | graph_index — 파일 43 · 심볼 321 · 호출 2,339 · 미해결 호출 6 · **파싱 오류 30** |
| 4 scout | 363.5초 | 호출 **12/12 ok** · `attempts` 전부 1 · **승격 6 · 기각 6** |
| 5 auditor | **20,807.0초(5시간 46분 47초)** | 호출 **18** · Finding **6** · 🔴 **`probe` 0회** |
| 6 gate | 0.0초 | `gate_eval` 6건 — **E2 다섯 · E1 하나** |
| 7 critic | 477.3초 | 호출 **6/6 ok** → 🔴 **`need_evidence` 6/6** |
| 8 pov | **13,457.2초(3시간 44분 17초)** | 홉 8 · PoC **4건** · 채점기 제출 **4건** · 🔴 홉 9에서 `ModelGatewayError`(마감) |
| 9 report | — | ok · `find_0006` → `find_0001` 중복 제거(CWE-369 · `apprentice_magic_strength`) |

타깃 넷은 10-11과 같다 — `src-vul/file/src/apprentice.c`의 `main@177`(slice_0001) ·
`apprentice_1@429`(slice_0002) · `file_ms_free@486`(slice_0003) · `nonmagic@743`(slice_0006).

### Scout 좌석별

| 좌석 | 모델 | 호출 벽시계(초) | 출력 토큰 | 승격 | 기각 |
| --- | --- | --- | --- | --- | --- |
| `scout_memory` | qwen3-14b | 81.3 / 39.9 / 35.1 / 18.2 | 606·1,631·1,554·766 | 2 | 2 |
| `scout_boundary` | seccoderx-8b | 12.6 / 41.2 / 26.8 / 23.0 | 610·2,387·1,588·1,353 | 3 | 1 |
| `scout_injection` | qwen3-14b | 13.1 / 41.4 / 15.7 / 14.9 | 530·1,700·643·612 | 1 | 3 |

⚠ **4단계 `stage_start`는 24회이고 `model_call`은 12회다** — 뒤 12회는 0.0초짜리 분류 패스
(`scout_hop` → `transition` C-1 → `policy_decision` → C-2/C-3)다.

## 2. 🔴 Auditor가 `probe`를 한 번도 안 불렀다

`auditor_hop`을 액션으로 센 수: **`lookup` 12 · `answer` 6 · `probe` 0.**

후보별 호출: `cand_0004` 1 · `cand_0005` 1 · `cand_0006` 5 · `cand_0007` 5 · `cand_0008` 4 ·
`cand_0011` 2 = **18**. `answer` 여섯 건의 `stop_reason`이 전부 `answered`라 **홉 상한에 걸린 것이
아니다**(`thresholds.auditor_tool_hops: 16`).

| Finding | 후보 | `trigger_status` | `cited_probe_evidence` | 등급 |
| --- | --- | --- | --- | --- |
| find_0001 | cand_0004 | triggerable | **0** | E2 |
| find_0002 | cand_0005 | triggerable | **0** | E2 |
| find_0003 | cand_0006 | triggerable | **0** | E2 |
| find_0004 | cand_0007 | **no_signal** | **0** | E1 |
| find_0005 | cand_0008 | **guarded** | **0** | E2 |
| find_0006 | cand_0011 | triggerable | **0** | E2 |

`repro_spec`은 **6건 전부 하네스가 합성했다**(`repro_spec_synthesized` 6건 · 기대 신호
`sanitizer crash at src-vul/file/src/apprentice.c`) — 모델이 `null`을 보냈다는 뜻이고,
`doctor`가 경고하는 `synthesize_repro_spec=1` 경로다([#232](https://github.com/0xM1yu/whitehat-boss/issues/232)).

**도구 표면은 좁지 않다** — `src/schemas/auditor.py:58`의 `AuditorTool`이
`semgrep`·`clang-tidy`·`build`·`asan`·`gdb`·`poc` 여섯을 받고 v14 prefix `:209`가 그 여섯을
선언한다. **쓰이지 않은 것이다.** 상세·선택지는 [#617](https://github.com/0xM1yu/whitehat-boss/issues/617).

### 입력 조립은 멀쩡했다

`auditor_source`(`cand_0005`): 함수 21 · `chars` 24,948 · `anchor_chars` 1,239 · `callees` 14 ·
`dropped` 0 · `uncovered_lines` 0. 모델 입력 토큰 24,569이고 출력 상한 65,536에 닿지 않았다.
**입력이 잘려서 도구를 못 부른 것은 아니다.**

### `lookup` 해소율

`lookup` 12홉에서 요청 합 **20** · 해소 합 **21**(한 요청이 여러 심볼로 풀린다).
미해소 사유: `(없음)` 8 · **`closest_name` 3** · `outside_repo` 1.

## 3. Critic 6/6 `need_evidence` — 10-11과 같은 분포

| # | Finding | 벽시계 | 출력 토큰 | 판정 |
| --- | --- | --- | --- | --- |
| 1 | find_0003 | 33.3초 | 1,118 | `need_evidence` |
| 2 | find_0002 | 80.3초 | 3,102 | `need_evidence` |
| 3 | find_0001 | 60.9초 | 2,285 | `need_evidence` |
| 4 | find_0006 | 173.4초 | 6,801 | `need_evidence` |
| 5 | find_0005 | 113.0초 | 4,444 | `need_evidence` |
| 6 | find_0004 | 16.0초 | 564 | `need_evidence` |

`rebutted` 0 · `upheld` 0 · 전이 `F-7` → `queued` **6건** · `validated` **0건**.

첫 건의 `gate_eval` 상세: `static_ok: true` · **`location_ok: false`** · `path_ok: true` ·
`graph_ok: true` · `unblocked: true` · `upheld: false` · `has_repro_spec: true` ·
**`repro_is_harness: true`**.

`location_ok: false`는 [#563](https://github.com/0xM1yu/whitehat-boss/issues/563)의 직접 결과다 — §6을 보라.

🔴 **등급이 10-11보다 높았는데 판정이 같았다.** 10-11은 E2 둘 + E1 셋이었고 이번은 E2 다섯 + E1 하나다.
**두 run 연속 보류 전건**이라 [#612](https://github.com/0xM1yu/whitehat-boss/issues/612)(모델 판단인지
입력 구조인지)의 「입력 구조」 쪽 가설에 자료가 하나 늘었다. ⚠ **분리 실험은 없다** — 이것은
두 run의 상관이고 n=2다.

## 4. 🔴 8단계 PoV — Critic을 지난 Finding에서 사슬이 처음 채점기까지 닿았다

홉 8개 전부 `find_0003`에 붙었다(`pov` 호출 9 — 홉 8은 게이트웨이 실패).

| 홉 | 액션 | 도구 | exit | 벽시계 | PoC |
| --- | --- | --- | --- | --- | --- |
| 0 | `gen` | `poc` | 0 | 1,477.2초 | **16바이트** `d30b3c0a...` · 제출 1 |
| 1 | `inspect` | `build` | **1** | 1,791.6초 | — ([#507](https://github.com/0xM1yu/whitehat-boss/issues/507)) |
| 2 | `gen` | `poc` | 0 | 901.9초 | **18바이트** `187e6b48...` · 제출 2 |
| 3 | `gen` | `poc` | 0 | 1,799.1초 | **30바이트** `2a73c829...` · 제출 3 |
| 4 | `inspect` | **`gdb`** | **1** | 1,781.4초 | — (#507) |
| 5 | `inspect` | `poc` | 0 | 2,421.7초 | 같은 30바이트 **재실행** |
| 6 | `inspect` | `poc` | 0 | 1,783.0초 | 같은 30바이트 **재실행** |
| 7 | `gen` | `poc` | 0 | 917.5초 | **22바이트** `5766c239...` · 제출 4 |
| 8 | — | — | — | 573.6초 | 🔴 `ModelGatewayError`(감시자 마감) |

액션 합: `gen` 4 · `inspect` 4. 도구별: `poc`/exit 0 **6회** · `build`/exit 1 **1회** ·
`gdb`/exit 1 **1회**.

**PoC 바이트가 도구에 실제로 실린다.** `tool_call`이 `poc {harness_input}` · `input_bytes` ·
`input_sha256`을 들고 그 해시가 그 홉 PoC의 해시와 **일치한다** — ADR 0014·PR #567 경로가
PoV에서 성립한다. ⚠ **Auditor 경로는 여전히 안 싣는다**([#419](https://github.com/0xM1yu/whitehat-boss/issues/419)).

**봉투 `PovActionNamed`가 실 run에서 처음 쓰였다**(PR #611).

### 버려진 홉이 셋이다

| 홉 | 사유 | 모델 시간 |
| --- | --- | --- |
| 1 | `build`가 컴파일 실패(#507) | 1,791.6초 |
| 4 | `gdb`가 같은 사유로 실패(#507) | 1,781.4초 |
| 5·6 | 같은 `(도구, 바이트)` 재실행([#619](https://github.com/0xM1yu/whitehat-boss/issues/619)) | 4,204.7초 |

합 **7,777.7초(2시간 9분 38초)** = 8단계 모델 시간 11,956.4초의 **65%**(계산값).

## 5. 채점기 — 제출 4건 · 점수 0

| # | `poc_sha256` | 바이트 | `vul_exit_code` | `fix_exit_code` | `designatable` |
| --- | --- | --- | --- | --- | --- |
| 1 | `d30b3c0a...` | 16 | **0** | null | false |
| 2 | `187e6b48...` | 18 | **0** | null | false |
| 3 | `2a73c829...` | 30 | **0** | null | false |
| 4 | `5766c239...` | 22 | **0** | null | false |

채점기 DB `poc_records` **4행** · 에이전트 id `0eeca4d6-f937-4407-bf91-8b8ad7c66ee9` ·
`cached: false`(실제 컨테이너 실행) · 왕복 765~1,107 ms.

매니페스트 `oracle`: `vul_submitted: 4` · `vul_failed: 0` · `vul_intermediate: 4` ·
`vul_candidates: 0` · `final: none` · `final_reason: no_poc`.

**`vul_exit_code 0`은 카나리 기준 무해 입력과 같은 결과다**(정답 PoC는 77). 넷 다 취약점을
건드리지 못했다. 그리고 넷 다 중간 `gen`이라 `designatable: false`이고, 모델이 `submit`을 내기 전에
마감이 왔으므로 **최종 지정이 0건**이다.

🔴 **`final_reason: no_poc`가 10-11의 「PoC 0건」과 같은 문자열이다** —
[#620](https://github.com/0xM1yu/whitehat-boss/issues/620).

## 6. 정적 신호가 후보와 겹치지 않는다 — #563 재현

semgrep 히트 10건의 시작 줄: **1086 · 1091 · 1116 · 1264 · 1296 · 1829 · 1841 · 3002 · 3090 · 3093**
(`insecure-use-memset` 7 · `harness-memcpy-nonconstant-length` 2 · `double-free` 1).
후보 넷의 줄 177 · 429 · 486 · 743과 **겹치는 구간 0건**. 10-11 보고서가 적은 자리와 같다.

**PR #577의 관측이 실 run에서 처음 떴다** — 로그 헤더가 *"대상을 반쪽만 파싱했다 (PartialParsing)
— 찾은 히트 수는 하한"*을 적는다. `errors[0]`이 `apprentice.c:118`의 `int was unexpected`
구문 오류이고 건너뛴 구간이 160여 개다. **히트 10은 하한이다.**

⏵ 2026-10-13 (이슈 #563 선택지 3) - 같은 `apprentice.c`를 `harness-tools:v3`(semgrep 1.86.0)에서 하네스 argv로 다시 돌리니 히트가 **같은 10건**(줄·룰 모두)이고 `PartialParsing` 1건의 건너뛴 구간은 **148개**였다(160여 개가 아니다 - 같은 번들·판인지 대조 안 함). 118줄은 `private int ...`이고 `private`는 `file.h:88`의 `#define private static`이다. 구간은 거의 한 줄씩(머리 매크로 93 · `CAST(` 문장 44 · 그 밖 11)이라 파일 전체가 아니고 함수 본문은 분석된다. 후보 429·486·743은 모두 그 머리 한 줄 자체지만 486·743 함수는 본문에 건너뛴 줄이 0·히트 0이라 겹침이 없는 것은 파싱이 아니라 **룰이 안 걸린 것**이다. n=1 번들이고 근거·한계는 `src/adapters/container_tools.py`의 `_SEMGREP_PARTIAL_PARSE` 아래 주석이 든다.

## 7. 기동 실측 — 선취가 다섯 좌석을 전부 상한 안에 넣었다

`tools/serve/prefetch-weights.sh --roles seats`로 다섯 모델 **160 GB를 약 7분**에 받았다
(`Qwen3-14B` 28 · `Qwen3.8-27B` 52 · `SecCoderX` 16 · `feyospace-v1-small` **52** ·
`gpt-oss-20b` 13 GB · 원격 `du -sh` · 디스크 141 GB 여유). PR #610이 기본 역할을 `seats`로 바꾼 뒤
처음 쓰였다.

| 좌석 | 이 run ready | 상한 | 10-11 |
| --- | --- | --- | --- |
| `qwen3-14b` | **146초** | 550 | 콜드 내려받기 261.476초 · 따뜻한 25~30초 |
| `seccoderx-8b` | **45초** | 400 | 콜드 155초 · 따뜻한 25~31초 |
| `qwen3.8-27b` | **197초** | 600 | **콜드 TIMEOUT** · 따뜻한 65~147초 |
| `gpt-oss-20b` | **67초** | 300 | **콜드 2회 TIMEOUT** · 따뜻한 35초 |
| `feyospace-v1-small` | **76초** | — | 이 좌석 콜드는 10-11에 미측정 |

원천은 원격 `swap.log`의 `ready ... after` 줄이다. **전부 상한 안이고 TIMEOUT 0건이다** —
[#571](https://github.com/0xM1yu/whitehat-boss/issues/571)이 든 콜드 기동 상한이 선취로 넘겼다.
⚠ **선취만의 효과로 분리되지 않는다** — 박스·드라이버·디스크가 10-11과 다르다.

교대 넷 전부 `ok`: 4단계 191초 · 5단계 202.6초 · 7단계 71.6초 · 8단계 76초 · run 끝 내림 3.1초.

## 8. 🔴 디코드가 10-11의 0.72배다

| run | 좌석 | 디코드 | 원천 |
| --- | --- | --- | --- |
| 2026-10-11 | `qwen3.8-27b` | **23.3~24.0 tok/s**(중앙 23.7) | 원격 로그 · trace |
| **2026-10-12** | `qwen3.8-27b` | **16.8~17.5 tok/s** | 원격 로그 `Avg generation throughput` 분포 |

**분포가 겹치지 않는다.** 같은 모델·같은 창(262,144)·같은 util(0.9)인데 **1.39배 느리다**
(계산값 23.7 / 17.1). 갈릴 수 있는 축은 Edition(Server ↔ Workstation) · 박스 · 드라이버인데
**이 run은 분리하지 않는다.** 호출 단위 교차 검증: `cand_0004` 홉 0이 42,167토큰 / 2,630.3초 =
**16.03 tok/s**(계산값).

KV 실측(`qwen3.8-27b` 기동 로그): 여유 **31.95 GiB** · **517,307 토큰** · 창 262,144 요청에
최대 동시성 **1.97배**.

## 9. 장부

| 항목 | 값 |
| --- | --- |
| 모델 호출 | scout 12 · **auditor 18** · critic 6 · **pov 9** = **45** |
| 출력 토큰(역할별) | auditor **348,945** · pov **208,340** · critic 18,314 · scout 13,980 |
| 회복된 스키마 실패 | **5건** — auditor 3 · pov 2([#618](https://github.com/0xM1yu/whitehat-boss/issues/618)) |
| 그중 절단 | 1건 — `cand_0011` 홉 0의 1차가 `output_max_by_role.auditor`(65,536)에 닿아 `length` |

⚠ **run 전체 입력·출력·`gpu_seconds` 합계는 이 보고서에 안 싣는다** — 마감으로 run이 끊겨
마지막 `budget charge` 이벤트가 종단 값이 아니다. 7단계 시점의 중간 집계는 입력 572,240 ·
출력 364,043 · `gpu_seconds` 21,202였다(trace `budget`). 역할별 출력 토큰은 `model_call`을
합산한 **계산값**이라 그 제약을 받지 않는다.

## 10. 운영에서 걸린 것

- **감시자 마감이 run을 끊었다.** `MAX_HOURS=10` → `KILLED`에 `reason=deadline_36000s` ·
  TERM `02:42:25Z` → 20초 뒤 KILL. 설계대로 작동했고 **매니페스트는 남았다**(#533).
  ⚠ **감시자는 깨끗하게 연장할 수 없다** — `MAX_HOURS`를 기동 때 읽고, 떠 있는 감시자가 있으면
  새 감시자가 뜨지 않는다.
- **직결 ssh + `--loop` 터널로 전송 끊김 0건.** 9시간 55분 run에서 `RemoteDisconnected`·게이트웨이
  오류가 **마감 전까지 0건**이다. 10-11은 같은 자리에서 run 셋이 끊김으로 죽었다.
  ⚠ 로컬 인터넷 전환이 이 run에는 없었으므로 **처방의 효과와 사정의 차이가 분리되지 않는다.**
- **이미지 내려받기 약 2분.** 10-11의 일본 박스는 48분에도 안 끝났다. 광고 다운 26,407 Mbps.
- **서빙 로그의 `[ERROR]` 4건은 무해하다** — transformers의 `Qwen3VLVideoProcessorInitKwargs`
  docstring 경고(`min_frames`·`max_frames`)이고 추론과 무관하다. `traceback`·`OOM`·`CUDA error` 0건.
- **`tools/monitor.py`를 경로로 못 돌렸다** — [#616](https://github.com/0xM1yu/whitehat-boss/issues/616).
  `python -m tools.monitor`로 우회했다.

## 11. 비용

| 항목 | 값 |
| --- | --- |
| 박스 가동 | **10.090시간** (`vastai show instance` `duration`) |
| 실시급 | $1.7167/hr |
| **합계** | **$17.32** (계산값 10.090 x 1.7167) |
| 그중 버려진 PoV 홉 셋 | 약 **$3.71** (계산값 7,777.7 / 3,600 x 1.7167) |
| 내려받기 과금 | 0.2 GB |

비용은 전부 **계산값**이다(가동시간 × 시급). 선취·카나리·preflight에 든 시간은 run 밖이고
그 내역은 세션 기록 기준 어림값이라 산출물에 없다.

## 12. 이 run이 낸 결함

| 번호 | 내용 | 상태 |
| --- | --- | --- |
| [#616](https://github.com/0xM1yu/whitehat-boss/issues/616) | `tools/monitor.py`를 문서가 부르는 경로 형태로 돌리면 `ModuleNotFoundError`로 죽는다 | 신규 |
| [#617](https://github.com/0xM1yu/whitehat-boss/issues/617) | Auditor가 `probe`를 18호출 중 0회 쓴다 — 실행 근거 0 · 등급 E2 천장 · 경고 0줄 | 신규 |
| [#618](https://github.com/0xM1yu/whitehat-boss/issues/618) | 회복된 스키마 실패는 응답 원문이 안 남는다(12,293 · **65,536** 토큰 실측) | 신규 |
| [#619](https://github.com/0xM1yu/whitehat-boss/issues/619) | PoV 반복 금지가 `gen`만 묶어 `inspect`가 같은 `(도구, 바이트)`를 재실행한다 | 신규 |
| [#620](https://github.com/0xM1yu/whitehat-boss/issues/620) | `final_reason: no_poc`가 「PoC 0건」과 「PoC 4건·지정 0건」을 뭉갠다 | 신규 |
| [#507](https://github.com/0xM1yu/whitehat-boss/issues/507) | 번들이 컴파일되지 않는다 — **3회째 재현** + **PoV 홉 둘(59분 33초)을 태운다**는 새 비용 | 기존 · 코멘트 둘 |
| [#563](https://github.com/0xM1yu/whitehat-boss/issues/563) | 정적 신호 줄과 후보 줄이 안 겹친다 | 기존 · 재현 |
| [#515](https://github.com/0xM1yu/whitehat-boss/issues/515) | 본문의 「v3가 서빙 판」이 낡았다 — v5가 넓은 범위를 명시한다 | 기존 · 코멘트 |
| [#612](https://github.com/0xM1yu/whitehat-boss/issues/612) | Critic 보류 전건이 두 run 연속 | 기존 · 자료 추가 |
| [#432](https://github.com/0xM1yu/whitehat-boss/issues/432) | 디코드 속도 상수 — 같은 좌석에서 16.8~17.5 대 23.3~24.0 | 기존 · 자료 추가 |

## 13. 철회한 결함 후보

- **「`cli run`의 stdout이 cp949로 깨진다」 — 아니다.** `src/cli.py:2191`이
  `force_utf8(sys.stdout)`을 부르고 `run.out`은 UTF-8이다. 깨져 보인 것은 PowerShell
  `Get-Content`가 ANSI 코드페이지로 디코드했기 때문이다. **파일이 아니라 읽기가 틀렸다.**
- **「Docker VM이 재기동돼 카나리를 다시 재야 한다」 — 아니다.** `host_uptime_s`로 그렇게
  추론했는데 `boot_id`가 10-11 것과 같다. 호스트 절전 때 VM이 멈춰 uptime만 뒤처진 것이다.
  재측정은 무해했다.
- **「Auditor가 도구 루프를 안 쓴다」 — 반만 맞다.** 후보 2건 시점에 그렇게 적었으나
  `lookup`이 그 뒤 12회 났다. 참인 명제는 **「`probe`만 0회」**다(#617 본문이 그 정정을 든다).

## 14. 이 보고서를 반증하는 방법

- **「8단계가 돌았다」** — `trace.jsonl`에 `stage: 8`의 `pov_hop`이 0건이거나 `oracle_submitted`가
  0건이면 틀린다(지금 각각 8건·4건).
- **「점수 0」** — 채점기 DB `poc_records`의 네 행 중 `vul_exit_code`가 `0`이 아닌 것이 있으면 틀린다.
- **「`probe` 0회」** — `auditor_hop`에 `action: "probe"`가 하나라도 있으면 틀린다.
- **「선취가 상한을 넘겼다」** — 원격 `swap.log`에 `TIMEOUT`이 있으면 틀린다(지금 0건).
- **「디코드 0.72배」** — 같은 좌석의 `model_call`에서 `output_tokens / wall_ms`가 23 tok/s대면 틀린다.
- **「#507이 PoV 홉 둘을 태웠다」** — `tool_call`의 `build`·`gdb`가 `ok: true`면 틀린다.

## 15. 이 run이 답하지 않는 것

- **`asan`은 어느 단계에서도 0회 호출**이다 — 그 도구가 이 번들에서 어떻게 끝나는지 여전히 미측정.
- **PoV와 Auditor가 갈린 이유** — 좌석·프롬프트·입력 중 어느 축인지 분리 실험이 없다(#617 (d)).
- **Critic 보류의 원인** — 모델 판단인지 입력 구조인지 n=2 상관뿐이다(#612).
- **디코드 차이의 원인** — Edition·박스·드라이버가 섞여 있다(#432).
- **마감이 없었으면 `submit`이 났을지** — 홉 상한 16 중 8을 썼고 그 뒤는 관측 0건이다.
- **다른 과제·다른 슬라이스** — `arvo:1065` 하나 · 4조각뿐이다.

## 16. 재현 절차

셸·명령째 순서는 [portability.md](../../portability.md) 「Windows에서 원격 교대 run 절차」와 같다.
이 run이 더한 것 둘:

1. **직결 ssh를 쓴다** — `vastai ssh-url <id>`가 주는 `root@<public_ipaddr>:<direct_port_start>`.
   `~/.ssh/config`에 그 호스트만 `ServerAliveCountMax 12`(PR #610이 스크립트 기본값으로 넣었다).
2. **가중치를 선취한다** — `SWAP_SSH_HOST=... bash tools/serve/prefetch-weights.sh --roles seats`.
   이 박스에서 160 GB를 약 7분에 받았고 좌석 다섯이 전부 상한 안에 떴다(§7).

⚠ **감시자 `MAX_HOURS`를 run 길이보다 넉넉히 준다** — 이 run은 10시간에 걸려 8단계 중간에 끊겼고,
5단계가 5시간 47분 · 8단계가 3시간 44분이었다.

## 17. 정리 상태

- vast 인스턴스 **55417596 삭제 완료** · `vastai show instances`로 부재 확인
- 회수한 것: run 산출물 전체 · 채점기 DB·로그 · 원격 로그 7개(좌석별 vLLM 다섯 ·
  `swap.log` · `guard.log` · `KILLED`)
- 🔴 **보존 위치가 저장소 밖이고 커밋되지 않는다** — `C:\w\s17-artifacts\` · `C:\w\oracle-run09\` ·
  `C:\w\s17-results\`. 이 보고서의 수는 그 디렉터리가 사라지면 재현할 근거를 잃는다
- 채점기 서버·터널은 run 종료 후 정리 대상이다
