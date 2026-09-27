# need_evidence 루프 실서빙 스모크 — 정상 표본 — ATOM

> **왜 돌렸나.** [GT 스모크](2026-09-27-ne-loop-smoke-atom.md)는 입력이 전부 실제 취약 경로였다. 정상 코드를
> 받은 Scout가 **need_evidence를 내는지**, 내지 않으면 어떻게 판정하는지를 보려고 정상 표본 다섯 개를
> ATOM에서 **직접 슬라이싱**해 Scout 풀에 올렸다. **탐지 정확도를 잰 것이 아니다** — 표본 셋(좌석 24)이다.
>
> **비교표 밖이다** — 실서빙 키의 `checksum`이 0이다([CLAUDE.md](../../../CLAUDE.md) §0). run은 머지하지 않는 브랜치
> `smoke/ne-loop-atom-benign`에서 돌았다(§6). 조건은 GT 스모크의 **B**(동료 확인 끔 —
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
| **need_evidence 0/24** | 세 표본 24좌석(파싱 성공 23)에서 hop0 need_evidence가 **하나도 없었다** — 루프는 한 번도 안 돌았다 |
| **정상 코드를 9좌석이 올렸다** | 파싱된 23좌석 중 **triage_review 8 + 신호 밖 1 = 9가 승격**(auditor로 감). reject 14. 좌석별로 갈린다 — injection **8/8 reject** · memory 6 reject / 2 review · boundary **0 reject**(review 6 · 신호 밖 1 · 파싱 실패 1) |
| **신청서는 review에 붙어 나온다** | review 7좌석이 `requests`를 **13개** 달았다(변수 5 · 함수 4 · 타입 2 · 매크로 1 · caller 1). 설계상 루프는 need_evidence에서만 돌므로 **아무것도 조회되지 않았다** |
| `scout_boundary` 신호 이탈·파싱 실패 | 8좌석 중 **1**이 세 신호 밖 코드(`source_@net/sunrpc/xdr.c:L859`)를 첫 reason code로 내 점수 경로(0.8)로 올라갔다 — GT 스모크의 4/28과 같은 모양. **1**(tf_requant)은 두 시도 모두 출력 상한 8,192토큰에 닿아 `schema_parse_failed`로 건너뛰었다(650 s) |
| 슬라이싱 | 5표본 중 **라벨 함수에 걸치는 슬라이스가 나온 것은 셋** — tf_requant 1/6 · linux_xdr 6/68 · qemu_vmxnet3 1/94. linux_keyboard **0/102**, bpf_verifier는 **슬라이싱 2시간 상한 예상으로 제외**(§2) |
| 비용 | Scout 호출 wall **83~200 s**(파싱 실패 1건 650 s 제외) · 입력 5,413~15,091토큰(창 40,960 안). 창 초과·400·타임아웃 **0**. run wall: tf_requant 14분 · linux_xdr 41분 · qemu_vmxnet3 6분 |
| 감시자 | **개입 0회.** `MemAvailable` 최저 **28.5 GiB**(슬라이서 둘과 겹친 구간) · cgroup anon 최대 26.9 GiB · 표본 476 |

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

| 표본 · 타깃 | scout_memory | scout_boundary | scout_injection |
| --- | --- | --- | --- |
| tf_requant `Compute` | reject 0.1 | **파싱 실패**(8,192 ×2) | reject 0.1 |
| xdr `xdr_stream_set_pos` | reject | **신호 밖** → 0.8 승격(CWE-190) | reject |
| xdr `xdr_set_page_base` | reject | review(CWE-119) · 신청 2 | reject |
| xdr `xdr_init_decode_pages` | reject | review(CWE-129) · 신청 1 | reject |
| xdr `xdr_copy_to_scratch` | reject | review(CWE-119·120·191) · 신청 2 | reject |
| xdr `xdr_inline_decode` | reject | review(CWE-119) · 신청 3 | reject |
| xdr `xdr_stream_subsegment` | review(CWE-457) | review(CWE-119) · 신청 2 | reject |
| vmxnet3 `vmxnet3_put_rxq_descr` | review(CWE-476) · 신청 1 | review(CWE-125) · 신청 2 | reject |

- 승격된 9개는 auditor(`fake`)·critic(`fake`)을 거쳤다 — 그 뒤 판정은 가짜 모델의 것이라 **적지 않는다**.
- need_evidence 0이라 GT 스모크의 「가져온 코드로 판정이 바뀌나」는 이 입력에서 **잴 대상이 없었다**.

## 4. 해석 — 여기까지만 말할 수 있다

- **정상 코드에서 Scout는 「더 보여 달라」보다 「올린다」를 골랐다.** 올린 9좌석 중 7이 신청서를 달았다 —
  모자란 것을 적으면서도 신호는 review였다. 루프가 need_evidence에만 걸려 있어 그 신청서는 버려진다.
  이것이 설계 결함인지(review+requests도 조회해야 하나)는 **24좌석으로 정할 수 없다** — 사례로만 적는다.
- **좌석 편차가 크다.** boundary(`seccoderx-8b`)는 8좌석 중 reject가 0이고, injection은 8/8 reject다.
  정상 표본만의 수라 오탐률로 읽지 않는다.
- GT 스모크(84좌석 중 NE 5)와 합쳐도 **need_evidence는 108좌석 중 5**다.

## 5. 하지 않은 것

- 조건 A(설계 그대로)·C(되묻기 강제)는 안 돌렸다. 동료 확인을 켠 A라면 승격 좌석이 늘거나 줄 수 있다.
- keyboard·bpf_verifier는 Scout에 안 올렸다(§2). linux_xdr 슬라이싱을 끝까지 다시 돌리지 않았다.
- 라벨 함수를 **직접** 타깃으로 한 입력(슬라이서 없이)은 만들지 않았다 — 슬라이스 모양을 지어내지 않았다.

## 6. 스모크 브랜치

`smoke/ne-loop-atom-benign` @ `7e612ce` = origin/main `18c98fc` 위에 GT 스모크 브랜치의 커밋 넷을
cherry-pick했다 — `71574d4`(`atom_ne_loop_scout` 설정) · `9dfa5ba`(`HARNESS_SMOKE_NO_PEER_REVIEW`) ·
`946a61d`(128 KiB 넘는 `.h`를 색인에서 뺌 — #314 우회) · `7e612ce`(`HARNESS_SMOKE_FORCE_LOOP`, 이번에는 끔).
스위치를 안 켜면 설계와 같다. **머지하지 않는다.**
