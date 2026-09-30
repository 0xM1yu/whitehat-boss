# need_evidence 루프 실서빙 스모크 — 정상 표본 — ATOM

> **왜 돌렸나.** [GT 스모크](2026-09-27-ne-loop-smoke-atom.md)는 입력이 전부 실제 취약 경로였다. 정상 코드를
> 받은 Scout가 **need_evidence를 내는지**, 내지 않으면 어떻게 판정하는지를 보려고 정상 표본 다섯 개를
> ATOM에서 **직접 슬라이싱**해 Scout 풀에 올렸다. **탐지 정확도를 잰 것이 아니다** — 표본 셋(좌석 24)이다.
>
> **비교표 밖이다** — 실서빙 키의 `checksum`이 0이다([CLAUDE.md](../../../CLAUDE.md) §0). run은 머지하지 않는 브랜치
> `smoke/ne-loop-atom-benign`에서 돌았다(§7). 조건은 GT 스모크의 **B**(동료 확인 끔 —
> `HARNESS_SMOKE_NO_PEER_REVIEW=1`)이고 되묻기 강제(C)는 켜지 않았다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-28 00:18 KST`(마지막 run `benign/run_20260927_0003` 종료 · 매니페스트 `15:18:09Z`). 서버 정리 00:19 |
| 실제 서빙 모델 | `hf://SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model@25d7f38146bdf0fad1eac09c586b1993ef3e6ee4`(별칭 `seccoderx-8b`) · `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18`(별칭 `qwen3-14b`) — `--max-model-len 40960` |
| `repo_commit` | `7e612ce`(세 run 모두 `repo_dirty: false`) = origin/main `18c98fc` + 스모크 커밋 넷(§6) |
| `run_id` | `benign/run_20260927_0001`(tf_requant) · `_0002`(linux_xdr) · `_0003`(qemu_vmxnet3). 날짜는 UTC |
| run 산출물 경로·보존 여부 | ATOM `~/ne-loop/artifacts/benign/` — 지우지 않았다. 저장소에는 없다 |
| 프로필 | `atom_ne_loop_scout`(스모크 브랜치 전용) — Scout 셋만 실서빙, auditor·critic `fake` |
| `tool_runner` · `--device` | `docker` · `atom` |
| `tool_images` digest | `harness-tools:v2` = `sha256:9f7a989d622b6b817ba133e57329e725368c60a562a813e540b0361c0459186e`(GT 스모크와 같은 이미지) |
| `--gpu-memory-utilization` | `seccoderx-8b` 0.20 · `qwen3-14b` 0.30 (분모 119.7 GiB) |
| 동시에 떠 있던 다른 서버 | 8000 `seccoderx-8b` + 8001 `qwen3-14b` 둘뿐. 첫 run 중에는 슬라이서(Joern) 둘이 CPU에서 같이 돌았다 |
| 서빙 서버 stdout | ATOM `~/ne-loop/logs/seccoderx-8b.20260927_205115.log` · `qwen3-14b.20260927_205312.log`(tf_requant) · `seccoderx-8b.20260927_232548.log` · `qwen3-14b.20260927_232758.log`(나머지 둘) |
| 원문 산출물 보존 위치 | 입력 필터 `~/ne-loop/pick_benign.py` · 슬라이싱 `~/ne-loop/slice_benign.sh` · 집계 `~/ne-loop/sum_benign.py` · 슬라이서 산출 `~/ne-loop/benign/<표본>/work/` |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| **need_evidence 0/24 (Scout-only run)** | 세 표본 24좌석(파싱 성공 23)에서 hop0 need_evidence가 **하나도 없었다** — 이 run에서는 루프가 한 번도 안 돌았다. ⏵ 같은 입력의 재실행(`harness_pool` 0006, §5)에서는 tf `Compute` boundary가 need_evidence를 내 hop 1을 받고 review로 섰다 — 두 실행의 판정은 합쳐 세지 않는다 |
| **라벨 함수 판정 8개 중 review 3개** | 라벨 함수가 직접 타깃인 세 행은 9좌석 중 파싱 성공 8이고, review는 **3개**였다 — xdr `xdr_set_page_base`의 boundary와 vmxnet3 `vmxnet3_put_rxq_descr`의 memory·boundary. tf `Compute`는 reject 2 + 파싱 실패 1이다. **나머지 승격 6개는 정상 라벨이 붙지 않은 xdr의 다른 함수 5개**에서 나왔다 |
| 전체 타깃별 좌석 | 여덟 타깃의 파싱 성공 23좌석 중 triage_review 8 + 신호 밖 1 = 9가 승격하고 reject 14였다. injection **8/8 reject** · memory 6 reject / 2 review · boundary **0 reject**(review 6 · 신호 밖 1 · 파싱 실패 1). 이는 파일 하나에서 고른 여덟 타깃의 좌석 수이지 정상 라벨 9건이 아니다 |
| **신청서는 review에 붙어 나온다** | review 7좌석이 `requests`를 **13개** 달았다(변수 5 · 함수 4 · 타입 2 · 매크로 1 · caller 1). 설계상 루프는 need_evidence에서만 돌므로 **아무것도 조회되지 않았다** |
| `scout_boundary` 신호 이탈·파싱 실패 | 8좌석 중 **1**이 세 신호 밖 코드(`source_@net/sunrpc/xdr.c:L859`)를 첫 reason code로 내 점수 경로(0.8)로 올라갔다 — GT 스모크의 4/28과 같은 모양. **1**(tf_requant)은 두 시도 모두 출력 상한 8,192토큰에 닿아 `schema_parse_failed`로 건너뛰었다(650 s) |
| 슬라이싱 | 5표본 중 **라벨 함수에 걸치는 슬라이스가 나온 것은 셋** — tf_requant 1/6 · linux_xdr 6/68 · qemu_vmxnet3 1/94. linux_keyboard **0/102**, bpf_verifier는 **슬라이싱 2시간 상한 예상으로 제외**(§2). ⚠ 모델에 올린 세 표본의 슬라이서 산출은 **전부 `partial`**이고 linux_xdr는 7,200 s 상한(rc 124) 뒤 체크포인트를 `--export-only`로 내보낸 것이다 — 완주 산출이 아니라, 판정은 부분 입력 위의 것이다 |
| 비용 | Scout 호출 wall **83~200 s**(파싱 실패 1건 650 s 제외) · 입력 5,413~15,091토큰(창 40,960 안). 창 초과·400·타임아웃 **0**. run wall: tf_requant 14분 · linux_xdr 41분 · qemu_vmxnet3 6분 |
| 감시자 | **개입 0회.** `MemAvailable` 최저 **28.5 GiB**(슬라이서 둘과 겹친 구간) · cgroup anon 최대 26.9 GiB · 표본 476 |
| Auditor·Critic까지(재실행 §5) | 라벨 함수 셋에서 E2 이상은 0. xdr·vmxnet3 boundary의 E1 가설은 `hypothesized`로 남았다 — xdr는 GPU 예산 7,353/7,200 s 소진으로 Critic 미호출, vmxnet3는 Critic upheld이나 repro spec 없음. 모델 호출 33건 전부 성공 |

## 2. 입력 — 정상 표본과 슬라이싱

- **출처** — 사용자가 준 `primevul_normal_5.jsonl`(정상 함수 다섯: `project`·`file_path`·`func_name`·`code`)과
  그 파일 원본 다섯. **저장소는 파일 하나씩이다** — 헤더·다른 번역 단위가 없다. 그래서 resolver가 조회했더라도
  그 파일 밖 정의는 못 찾았을 것이다(이번에는 조회 자체가 0).
- **슬라이싱** — ATOM에서 스모크 트리의 `slicer.py`(Joern 4.0.605, `-Xmx10g`)로 떴다. 플래그
  `--max-flows-per-candidate 10 --backward-depth 30 --backward-nodes 5000 --candidate-timeout-seconds 1200
  --query-timeout-seconds 600`, 표본당 `timeout 7200`.
- **모델 입력은 라벨 함수에 걸치는 슬라이스만**(사용자 결정) — `pick_benign.py`가 jsonl `code`를 원본에서
  찾아 함수 줄 범위를 정하고, 슬라이스 `lines`가 그 범위와 한 줄이라도 겹치면 고른다. 슬라이서 export를
  고치지 않고 `slices` 배열만 줄인다. ⚠ **겹침 기준이라 Scout 타깃 함수가 라벨 함수와 다를 수 있다** —
  linux_xdr의 6개 중 라벨 함수(`xdr_set_page_base`)가 타깃인 것은 하나이고 나머지 다섯은
  `xdr_stream_set_pos` · `xdr_init_decode_pages` · `xdr_copy_to_scratch` · `xdr_inline_decode` ·
  `xdr_stream_subsegment`다.

| 표본 | 라벨 함수 | 슬라이서 | 슬라이스 | 라벨에 걸침 | 하네스 |
| --- | --- | --- | --- | --- | --- |
| tf_requant | `Compute`(47–74) | 13 s · `partial` | 6 | 1 | run 0001 |
| linux_xdr | `xdr_set_page_base`(1223–1256) | **7,200 s 상한 도달(rc 124)** → 체크포인트에서 `--export-only` · `partial` | 68 | 6 | run 0002 |
| qemu_vmxnet3 | `vmxnet3_put_rxq_descr`(2532–2545) | 3,731 s · `partial` | 94 | 1 | run 0003 |
| linux_keyboard | `k_brlcommit`(898–914) | 1,061 s · `partial` | 102 | **0** | 입력 없음 |
| bpf_verifier | `__find_kfunc_desc_btf` | 1,801 s에 내림(rc 143) | — | — | **슬라이싱 2시간 상한 예상으로 제외** |

linux_xdr는 끝까지 못 돈 슬라이서가 체크포인트에 저장한 후보만 내보낸 것이다 — 다 돌았으면 슬라이스가
더 나왔을 수 있다.

## 3. 좌석별 판정 (hop0)

| 표본 · 타깃 | 라벨 함수 | scout_memory | scout_boundary | scout_injection |
| --- | --- | --- | --- | --- |
| tf_requant `Compute` | **예** | reject 0.1 | **파싱 실패**(8,192 ×2) | reject 0.1 |
| xdr `xdr_stream_set_pos` | — | reject | **신호 밖** → 0.8 승격(CWE-190) | reject |
| xdr `xdr_set_page_base` | **예** | reject | review(CWE-119) · 신청 2 | reject |
| xdr `xdr_init_decode_pages` | — | reject | review(CWE-129) · 신청 1 | reject |
| xdr `xdr_copy_to_scratch` | — | reject | review(CWE-119·120·191) · 신청 2 | reject |
| xdr `xdr_inline_decode` | — | reject | review(CWE-119) · 신청 3 | reject |
| xdr `xdr_stream_subsegment` | — | review(CWE-457) | review(CWE-119) · 신청 2 | reject |
| vmxnet3 `vmxnet3_put_rxq_descr` | **예** | review(CWE-476) · 신청 1 | review(CWE-125) · 신청 2 | reject |

- 정상 라벨 함수가 직접 타깃인 행은 `Compute` · `xdr_set_page_base` · `vmxnet3_put_rxq_descr` 셋이다. 이 셋의
  파싱 판정 8개 중 review는 **3개**(xdr boundary · vmxnet3 memory와 boundary)다.
- 나머지 승격 **6개**(review 5 · 신호 밖 1)는 라벨 함수와 같은 슬라이스에 걸렸지만 정상 라벨이 직접 붙지 않은
  xdr의 다른 함수 다섯에서 나왔다. 그러므로 전체 승격 9개를 「정상 코드를 올린 9좌석」으로 세지 않는다.
- 승격된 9개는 auditor(`fake`)·critic(`fake`)을 거쳤다 — 그 뒤 판정은 가짜 모델의 것이라 **적지 않는다**.
- need_evidence 0이라 GT 스모크의 「가져온 코드로 판정이 바뀌나」는 이 run에서 **잴 대상이 없었다**(재실행의 hop 1 한 건은 §5).

## 4. 해석 — 여기까지만 말할 수 있다

- **라벨 함수 판정 8개에서는 need_evidence 없이 review 3개가 나왔다.** 전체 여덟 타깃에서는 승격 9개 중
  6개가 라벨 없는 xdr 함수에서 나왔으므로, 이를 정상 라벨 9건으로 읽지 않는다. 전체 승격 중 review 7좌석이
  신청서를 달았다 — 모자란 것을 적으면서도 신호는 review였다. 루프가 need_evidence에만 걸려 있어 그 신청서는 버려진다.
  이것이 설계 결함인지(review+requests도 조회해야 하나)는 **24좌석으로 정할 수 없다** — 사례로만 적는다.
- **좌석 편차가 크다.** boundary(`seccoderx-8b`)는 8좌석 중 reject가 0이고, injection은 8/8 reject다.
  정상 표본만의 수라 오탐률로 읽지 않는다.
- GT 스모크(84좌석 중 NE 5)와 이 Scout-only run을 합쳐도 **need_evidence는 108좌석 중 5**다(§5 재실행은 따로 센다).

## 5. Auditor·Critic까지 실서빙 (`harness_pool`)

### 첫 시도는 결과에서 제외 — 4단계 교대 훅 실패

2026-09-28 00:49 KST의 `benign-full/run_20260927_0001`~`0003`은 **판정 결과가 아니다**. 세 run 모두
4단계 시작 전에 `stage_swap.sh 4`가 종료 코드 1을 내 2~3초 만에 중단됐고, `run_manifest.json`·`report.md`가
생기지 않았으며 모델 호출도 없었다(`~/ne-loop/logs/run_full-<표본>.20260928_0049*.log:2-29`의 같은
`CalledProcessError`; `sum_benign.py`는 계획 타깃만 각각 6·1·1로 읽음). 이 세 run은 아래 재실행 집계와
섞지 않는다.

- **확정 원인** — 서버가 하나도 없을 때 `stage_swap.sh:17`의
  `running=$(pgrep -u "$(id -u)" -f "vllm serve" | wc -l)`에서 `pgrep`이 1을 반환했다. `wc` 출력은 0이지만
  `set -euo pipefail` 때문에 대입문 전체가 1이 되어 launch 전에 끝났다. 서버가 떠 있던 수동 `bash -x`에서는
  `pgrep`이 0이라 통과했다. 서버가 없는 상태에서 러너와 같은 `sh -c "~/ne-loop/stage_swap.sh 4"`로 재현해
  종료 코드 1을 확인했다.
- **스모크 스크립트 수정** — 빈 검색을 정상으로 다루도록 그 줄의 파이프라인에 `|| true`를 붙였다.
  `launch.sh`의 guard·포트·다른 GPU 프로세스·`MemAvailable` 검사는 바꾸지 않았다. 감시자를 다시 띄운 뒤 같은
  `sh -c` 호출에서 두 Scout 서버 health와 종료 코드 0을 확인하고 체인을 재시작했다. 이 수정은 ATOM의
  `~/ne-loop/stage_swap.sh`에만 있고 머지 대상 코드가 아니다.
- **연쇄 관측** — 실패했는데도 `chains.log`에는 세 번 모두 `rc=0`과 마지막 `full-chain complete`가 남았다.
  `chain_full.sh`가 각 `run_h2.sh` 실패에서 멈추지 않는 것과 함께, 완료 표식만으로 성공을 판정할 수 없음을 보였다.
- **선택지** — (a) 지금처럼 빈 `pgrep`만 정상화하고 「서버 0개 + 러너의 `/bin/sh -c`」를 스모크 보조 스크립트
  회귀 검사로 고정한다. (b) 프로세스 수 계산을 빈 결과에서도 0으로 끝나는 `ps`/`awk` 한 단계로 바꿔
  `pipefail` 예외 자체를 없앤다. (c) (a) 또는 (b)에 더해 `run_h2.sh`가 하네스 종료 코드를 즉시 보존하고
  `chain_full.sh`가 비정상 run에서 멈추게 해 거짓 `rc=0`·`full-chain complete`를 막는다.

### 재실행 — 완료된 `harness_pool` 세 run

재실행은 `benign-full/run_20260927_0004`부터 시작했다. 이 절의 수치는 ATOM의 각
`artifacts/benign-full/run_20260927_0004`~`0006/{trace.jsonl,state.db,run_manifest.json,report.md}`와
`~/ne-loop/logs/chains.log`에서 다시 읽은 값이다. 앞 절의 0001~0003은 여기에 포함하지 않는다.

| 표본 · run ID | KST 시작~끝 | `stopped_after` | 관측된 run 경과 | GPU 예산 최종값 |
| --- | --- | --- | --- | --- |
| linux_xdr · 0004 | 00:59:20~03:10:10 | 5 | 7,850 s (`run_manifest.json` 시각 차) | 7,353 s / 7,200 s (소진) |
| qemu_vmxnet3 · 0005 | 03:10:10~04:00:25 | 없음 | 3,015 s (같은 계산) | 2,074 s / 7,200 s |
| tf_requant · 0006 | 04:00:26~04:51:10 | 없음 | 3,044 s (같은 계산) | 2,111 s / 7,200 s |

세 run은 모두 `repo_commit` `7b16e688f54d7afd574d47658b602ba26627ddc5`, `repo_dirty: false`,
프로필 `harness_pool`, 조건 `B3prime@pipeline`, `HARNESS_SMOKE_NO_PEER_REVIEW=1`로 기록됐다
(`run_manifest.json`). Scout의 기존 §3 판정은 앞선 Scout-only run의 것이고, 아래 Scout 열은 이 재실행의
`trace.jsonl`이다. 두 실행의 판정을 합쳐 세지 않는다.

| 단계 | 실서빙 모델 · revision | 포트 · GPU util | 교대 |
| --- | --- | --- | --- |
| Scout (4) | secCoderX-8B, Qwen3-14B | 8000 · 0.20, 8001 · 0.30 | 4단계에서 둘을 띄우고 다음 단계 전 종료 |
| Auditor (5) | Qwen3.8-27B `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` | 8002 · 0.80 | Scout 종료 뒤 단독 기동 |
| Critic (7) | gpt-oss-20B `6cee5e81ee83917806bbde320786a8fb61efebee` | 8003 · 0.30 | Auditor 종료 뒤 단독 기동 |

`stage_swap.sh`는 4→5→7 순서로 교대했고 동시 서빙을 하지 않았다(`chains.log`의 `swap`·`ready` 행).
`run_manifest.json`의 serving-role checksum은 Critic만 비영(非零)이고 Scout·Auditor는 0이므로, §0 규약에 따라
이 세 run을 역할 checksum 비교표에는 넣지 않는다. 이는 이번 trace의 호출 성공 여부나 위 revision 기록을 대신하는
측정값은 아니다.

| 표본 · run ID | Scout wall 합 | Auditor wall 합 | Critic wall 합 | 모델 호출 |
| --- | ---: | ---: | ---: | ---: |
| linux_xdr · 0004 | 2,476.241 s | 4,877.396 s | 0 s | 18 + 3 + 0 |
| qemu_vmxnet3 · 0005 | 343.535 s | 1,671.881 s | 58.969 s | 3 + 2 + 2 |
| tf_requant · 0006 | 402.464 s | 1,709.152 s | 0 s | 4 + 1 + 0 |

표의 wall은 각 `trace.jsonl` `model_call.gpu_ms`가 아니라 해당 호출의 `wall_seconds` 합계다. 33개
`model_call` 모두 `ok: true`, `attempts: 1`이다. 따라서 trace에서 관측된 HTTP 400·timeout은 0건이다.

Auditor 호출별 입력·출력 토큰과 wall은 다음과 같다(`trace.jsonl`의 `model_call` 6건). 토큰은 모델이 기록한 값이며,
wall은 각각의 호출 wall이다.

| run · finding | input / output tokens | Auditor wall | 이후 최종 state |
| --- | ---: | ---: | --- |
| 0004 · `find_0001` | 8,321 / 6,670 | 1,598.399 s | `hypothesized` (Critic 미호출) |
| 0004 · `find_0002` | 8,663 / 9,086 | 2,175.814 s | `hypothesized` (Critic 미호출) |
| 0004 · `find_0003` | 3,142 / 4,654 | 1,103.096 s | `hypothesized` (Critic 미호출) |
| 0005 · `find_0001` | 1,978 / 2,918 | 688.681 s | `queued` |
| 0005 · `find_0002` | 1,962 / 4,177 | 983.150 s | `hypothesized` |
| 0006 · `find_0001` | 2,205 / 7,337 | 1,709.132 s | `queued` |

`state.db`의 `objects(kind='finding')` payload와 trace의 policy transition을 함께 읽은 라벨 함수의
좌석→Auditor→Critic 결과는 아래다. `review`는 Scout의 의심 신호이지 취약성 확정이 아니다.

| 라벨 함수 · run | 재실행 Scout 좌석 | Auditor | Critic | 최종 finding state |
| --- | --- | --- | --- | --- |
| `Compute` · 0006 | memory reject; boundary는 `need_evidence` 뒤 hop 1에서 review; injection reject | E0, `CalculateUsedRange` 계약 미확인 | E0 gate라 미호출 | `queued` |
| `xdr_set_page_base` · 0004 | memory reject; boundary review(CWE-119); injection reject | E1 `find_0002` | GPU 예산 소진 뒤 미호출 | `hypothesized` |
| `vmxnet3_put_rxq_descr` · 0005 | memory review(CWE-476); boundary review(CWE-119); injection reject | E1 `find_0001`, `find_0002` | 첫째 rebutted; 둘째 upheld이나 repro spec 없음 | 첫째 `queued`, 둘째 `hypothesized` |

따라서 라벨 함수 세 건에서 검증된 취약성(E2 이상)는 남지 않았다. 다만 정상 코드인데도 `xdr_set_page_base`와
vmxnet3 boundary의 E1 가설은 `hypothesized`로 남았다. 전자는 예산 소진으로 Critic 판정을 받지 못했고, 후자는
Critic이 upheld했지만 repro spec 부재로 승격하지 못했다. `Compute`은 E0 gate에서 `queued`다. 이 세 사례만으로
정상 코드 일반의 오탐률이나 모델 간 우열은 결론 내리지 않는다.

### 재실행에서 드러난 결함 — 0004 Auditor 단계의 GPU 예산 소진

`run_20260927_0004/trace.jsonl`의 budget event는 5단계 뒤 GPU 사용량 7,353 s가 한도 7,200 s를 넘었다고
기록하고, 이어진 정책 event가 실행을 막는다. `run_manifest.json`도 `stopped_after: 5`다. 그래서 Auditor는
세 번 호출됐지만 Critic은 0회였고, 위 표의 xdr `find_0002`를 포함한 세 finding은 Critic이 없는
`hypothesized` 상태다. 이는 400·timeout·파싱 실패가 아니라 예산 한도에 걸린 관측 사실이다.

원인은 이 trace만으로 단정하지 않는다. 가능성은 (a) 이 입력에서 18개의 Scout 호출 뒤 Auditor 세 호출의 누적
wall/생성량이 GPU 예산을 초과했을 수 있음, (b) 예산 장부가 stage wall이 아니라 내부 GPU 계상값을 쓰므로 두 값의
차이를 별도 검증해야 함, (c) 후보 수·Auditor 출력 길이와 남은 예산을 사전에 연결하는 제어가 부족했을 수 있음이다.
대응 선택지는 (1) `stopped_after` run을 Critic 완료 run과 별도 열로 유지하고, (2) 다음 설계 판단에서 stage 5 진입
전 남은 예산과 예상 호출 수를 기록하며, (3) 출력 상한·후보 처리 순서를 바꿀지 여부는 별도 근거를 모은 뒤 결정하는
것이다. 이 보고서는 설정·예산·util을 변경하지 않았다.

### 감시자 기록

`~/ne-loop/logs/guard.log`를 2026-09-28 00:40 KST 이후로 필터하면 499개 표본에서 `MemAvailable` 최저는
16.2 GiB(03:29:24), cgroup anon 최대는 8.0 GiB(03:13:22)다. 같은 구간에 `KILLED`/`killed` 행은 0이고,
마지막은 04:51:34의 `avail=115.3GiB`, `cg_anon=0.1GiB` 뒤 `guard stop requested`다. 즉 감시자 개입은
관측되지 않았고, 체인이 정상 종료 표식 `STOP_GUARD`를 남겨 감시자가 멈췄다.

## 6. 하지 않은 것

- 조건 A(설계 그대로)·C(되묻기 강제)는 안 돌렸다. 동료 확인을 켠 A라면 승격 좌석이 늘거나 줄 수 있다.
- keyboard·bpf_verifier는 Scout에 안 올렸다(§2). linux_xdr 슬라이싱을 끝까지 다시 돌리지 않았다.
- 라벨 함수를 **직접** 타깃으로 한 입력(슬라이서 없이)은 만들지 않았다 — 슬라이스 모양을 지어내지 않았다.

## 7. 스모크 브랜치

`smoke/ne-loop-atom-benign` @ `7b16e68` = origin/main `18c98fc` 위에 GT 스모크 브랜치의 커밋 넷을
cherry-pick했다 — `71574d4`(`atom_ne_loop_scout` 설정) · `9dfa5ba`(`HARNESS_SMOKE_NO_PEER_REVIEW`) ·
`946a61d`(128 KiB 넘는 `.h`를 색인에서 뺌 — #314 우회) · `7e612ce`(`HARNESS_SMOKE_FORCE_LOOP`, 이번에는 끔).
스위치를 안 켜면 설계와 같다. **머지하지 않는다.**
