# need_evidence 루프(Phase 5 RAG) 실서빙 스모크 — Scout 풀 — ATOM

> **왜 돌렸나.** Phase 5(PR #298·#301·#302·#305·#306·#309)가 need_evidence resolver와 멀티턴 루프를 지었다
> ([ADR 0012](../../decisions/0012-harness-canon-v2.md) 결정 2·6). **실모델로 한 번도 안 돌렸다** — resolver가
> 실제 Scout 신청서를 얼마나 채우는지, 채운 코드를 받은 Scout가 판정을 바꾸는지, 홉 하나가 무엇을 쓰는지가
> 전부 미실측이었다. 그것을 재려고 돌렸다. **탐지 정확도를 잰 것이 아니다.**
>
> **비교표 밖이다** — 실서빙 키의 `checksum`이 0이다([CLAUDE.md](../../../CLAUDE.md) §0). run은 머지하지 않는 브랜치
> `smoke/ne-loop-atom`에서 돌았다(§9). 입력은 **슬라이서 산출이 아니라 팀원이 만든 GT 경로 슬라이스**다(§2) —
> 전부 실제 취약 경로이고 표본당 하나다. 그래서 「손 드는 비율」은 운영 입력의 값이 아니다.
>
> ⚠ **세 조건을 섞지 않는다.** 설계 그대로 돈 run(A), 동료 확인을 끈 run(B), 신청서를 채운 좌석을 한 번 되묻게
> 한 run(C)은 스모크 브랜치 전용 스위치로 갈랐다. C는 설계가 아니다 — 「가져온 코드가 판정을 바꾸나」를
> 재려고 표본을 늘린 조건이다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-27 19:36 KST`(마지막 run `gpu-force/run_20260927_0010` 종료 · 매니페스트 `10:36:08Z`). 서버 정리 19:37 |
| 실제 서빙 모델 | `hf://SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model@25d7f38146bdf0fad1eac09c586b1993ef3e6ee4`(별칭 `seccoderx-8b`) · `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18`(별칭 `qwen3-14b`) |
| `repo_commit` | A `2dbe213` · B `3251e67`(`gpu-np/run_20260927_0007`만 `71f244c`) · C `2bad718` — 전부 `repo_dirty: false`. 스모크 브랜치의 커밋은 §9 |
| `run_id` | A `gpu/run_20260927_0001~0011` · B `gpu-np/run_20260927_0001~0007` · C `gpu-force/run_20260927_0001~0010`(날짜는 UTC). 예행 `fake/run_20260927_0001` · `fakegt/run_20260927_0001` |
| run 산출물 경로·보존 여부 | ATOM `~/ne-loop/artifacts/{gpu,gpu-np,gpu-force,fake,fakegt}` — 지우지 않았다. 저장소에는 없다 |
| `tool_runner` | 프로필 선언 `docker` / 실제 `docker`(오버라이드 없음) |
| `--device` | `atom` |
| `tool_images` digest | `harness-tools:v2` = `sha256:9f7a989d622b6b817ba133e57329e725368c60a562a813e540b0361c0459186e` — 종류: docker image config sha256(rootless docker, 스모크 트리 `tools/harness-tools/`에서 로컬 빌드, `SEMGREP_RULES_COMMIT=40b8c63f`). 이미지 안 `/opt/indexer.py` sha256이 트리 파일과 같다(`0c9968fa…`) |
| `--gpu-memory-utilization` | `seccoderx-8b` 0.20 · `qwen3-14b` 0.30 (분모 119.7 GiB) |
| 동시에 떠 있던 다른 서버 | 8000 `seccoderx-8b` + 8001 `qwen3-14b` 둘뿐. 기동 전 GPU 연산 프로세스·vllm·다른 harness run 0 |
| 원문 산출물 보존 위치 | 위 run 경로 + 입력 변환 `~/ne-loop/convert_gt_slices.py` · 집계 `~/ne-loop/summarize_ne.py` · resolver 직접 평가 `~/ne-loop/offline_resolve.py`(결과 `offline_resolve_all.json`) · 색인 프로브 `~/ne-loop/probe_idx/` |
| 서빙 서버 stdout | ATOM `~/ne-loop/logs/seccoderx-8b.20260927_152231.log` · `qwen3-14b.20260927_152451.log`(`--enable-log-requests --enable-log-outputs`) |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| **resolver는 실제 신청서를 대부분 채운다** | Scout가 hop0에 낸 신청서 **48개** 중 **40개(83%)가 원본 블록을 받았다** — function 14/14 · variable 23/30 · macro 2/3 · type 1/1. 나머지는 비슷한 이름 4 · 정의 없음 3 · 이미 SOURCE에 있음 1. 돌려준 글자 중앙값 825자(최대 14,889 — 상한 15,000) [resolver 직접 호출 · 모델 없음 · §5]. ⚠ 48개 중 **6개(sample_07)는 정식 색인이 아니라 큰 헤더를 뺀 프로브 색인**으로 풀었다 — 그 run에는 색인이 없었다(#314) |
| **설계 그대로(A)는 루프가 한 번도 안 돌았다** | 11표본 33좌석 중 hop0 need_evidence **2** — 둘 다 **동료 확인**(`peer_review`)으로 건너뛰었다. 같은 타깃의 `scout_boundary`가 hop0에 review를 냈다(33좌석 중 review 10) |
| **need_evidence는 드물다** | 세 조건 28 run · 84좌석에서 hop0 need_evidence **5(6%)**. 같은 슬라이스를 다시 돌리면 뒤집힌다(sample_02 memory: A는 NE, B는 review) |
| **가져온 코드로 판정이 바뀐 것은 1/10** | C에서 루프에 든 좌석 **10**(강제 8 · 스스로 NE 2), 추가 호출 **11건 전부 성공**. review 7 → 6 유지 · reject 1 → 유지 · NE 2 → 둘 다 NE로 남아 `repeat_request`. **바뀐 1건(sample_13 memory)은 review → reject** — GT(타입 혼동·범위 밖 읽기)와 반대 방향이다(§6) |
| **need_evidence 좌석은 받은 뒤에도 같은 것을 다시 묻는다** | 2/2가 `repeat_request`로 섰다 — 하나는 함수 포인터 호출 대상(`wsi->a.protocol->callback`, 구문 색인으로 못 푼다), 하나는 변수의 선언만 받고 값을 원했다(`length`) |
| **인덱서가 큰 데이터 헤더에서 죽는다 — [#314](https://github.com/0xM1yu/whitehat-boss/issues/314)** | sample_07에서 샌드박스 300 s 초과(exit 124) → `index.json` 없음 → resolver 빈손. 시간 한도 3,600 s도 넘었다. 128 KiB 넘는 `.h` 10개를 빼면 **19 s** |
| **「색인 없음」이 「못 찾음」으로 기록된다 — [#315](https://github.com/0xM1yu/whitehat-boss/issues/315)** | 그 run의 NE 좌석은 `nothing_resolved`로 섰다 — 조회 실패와 구별되지 않는다 |
| 비용 | 홉 하나 wall **99~438 s**(중앙 145 s) · 입력 6,177~13,841토큰(창 40,960 안). 창 초과·400·타임아웃 **0** |
| `scout_boundary` 신호 이탈 | 28좌석 중 **4**가 세 신호 밖 코드(`source_0001@…` 등)를 첫 reason code로 냈다 — 신호가 없어 점수 경로로 판정됐다 |
| 감시자 | **개입 0회.** `MemAvailable` 최저 48.0 GiB · cgroup anon 최대 8.0 GiB |

## 2. 입력 — 팀원 GT 슬라이스

- **출처** — 팀원이 준 `gt_slices` 아카이브(main 40 · inj 20). 표본마다 `slices.json`(slice-v2 모양) ·
  `slice.c` · `metadata.json` · `ground_truth_path.md`. **어느 슬라이서 커밋으로 떴는지 어디에도 적혀 있지 않다**
  (`미기재`). `analysis_kind: "gt_derived"` · `run_signature: "gt-derived:<set>:<sample>"` — **Joern 슬라이서 산출이
  아니라 GT 인과 경로를 슬라이스 모양으로 싼 것**이다. main 173블록 중 166블록이 `ground_truth_path.md`의 인용 구간이다.
- **그대로는 못 썼다.** `code`에 CVE·CWE·SOURCE·SINK 머리말이 있고 경로가 `repo/`로 시작해 `render_slices`가
  drift로 거부한다. `entry`는 60/60이 정답 sink다.
- **변환**(`convert_gt_slices.py`, main만):
  - `code`를 원본에서 `slices.render()` 규칙대로 다시 조립한다 → 머리말이 사라진다. `repo/`를 뗀다.
  - `id`·`candidate_key`의 `GT` 글자를 중립 이름(`slice_NN` · `cand_NN`)으로 바꾼다.
  - `case`·`entry`·`scopes`는 **팀원 값 그대로**(지어내지 않았다) — `entry`가 정답 sink라는 한계는 남는다.
  - 팀원 도구가 `splitlines()`로 줄을 셌다(폼피드에서도 끊는다 — #259와 같은 원인). sample_23·32의 세 파일을 `"\n"`
    번호로 다시 셌다(조각 0).
  - Java 블록이 든 sample_39는 뺐다 → **39표본**. inj 20은 ATOM에 저장소가 없고 C/C++ 밖 언어가 섞여 안 썼다.
- **검증** — 39/39가 하네스 `load_slices` + `render_slices`(ATOM `samples/sample_XX/repo` 기준)를 통과했다.
  팀원 `code`의 코드 줄(`//` 주석 줄 제외)이 전부 재조립본에 있다. 재조립본 `code`·`id`·`candidate_key`에 `CVE-`·
  `CWE-`·`GT SLICE`·`SINK:`·`SOURCE:`가 0건이다. fake 예행의 Scout 프롬프트에서 걸린 것은 고정 지시문의
  `CWE-<숫자>` 한 줄뿐이다.
- **같은 저장소인가** — main 40개 프로젝트 이름이 ATOM `public/metadata.json`과 40/40 같고, 블록 173개 중 164개가
  줄번호째 바이트가 같다(나머지 9는 위 줄세기 문제).
- **GT 정보는 입력에 안 넣었다** — `metadata.json`·`ground_truth_path.md`는 §5·§6 대조에만 썼다.

## 3. 실험 환경

- **장비** — ATOM `atom-host`. GB10 · 통합메모리 119.7 GiB · 사용자 cgroup `memory.max` 80 GiB.
  vLLM 0.27.1(`~/venv-probe`). docker rootless.
- **서빙**(운영 창) — 둘 다 `--max-model-len 40960` · `--max-num-seqs 4` · `--reasoning-parser deepseek_r1` · `--enforce-eager`.
  `seccoderx-8b` 가중치 15.27 GiB · 로드 112.7 s · KV 54,064토큰(40,960 기준 1.32배) / `qwen3-14b` 27.52 GiB ·
  176.7 s · KV 48,912토큰(1.19배).
- **프로필** `atom_ne_loop_scout` — Scout 풀만 실서빙(`scout_memory`·`scout_injection` = `qwen3-14b`,
  `scout_boundary` = `seccoderx-8b`), Auditor·Critic fake. 루프는 Scout 단계 안에서만 돈다.
  실서빙 키에 `request_timeout_s` 1,200(기본 300 s로는 멈춘다 — #261). `max_hops` 3(명세값).
- **안전장치** — 감시자가 5초마다 `MemAvailable` < 12 GiB · cgroup anon > 72 GiB · 마감 12시간을 보고, 걸리면 이
  스모크가 띄운 프로세스 그룹만 내린다. 서버 기동 스크립트는 감시자·빈 포트·남의 GPU 프로세스 0·여유 메모리를
  확인한 뒤에만 띄운다. **개입 0회.** 끝난 뒤 GPU 프로세스 0 · `MemAvailable` 114.6 GiB.

## 4. 세 조건

| 조건 | 스위치 | 표본 | 좌석 | hop0 NE | 루프에 든 좌석 | 추가 호출 |
| --- | --- | --- | --- | --- | --- | --- |
| A 설계 그대로 | 없음 | 11 (sample_01~11) | 33 | 2 | 0 (둘 다 `peer_review`) | 0 |
| B 동료 확인 끔 | `HARNESS_SMOKE_NO_PEER_REVIEW=1` | 7 (02·07·11~15) | 21 | 1 | 1 (색인 없음 → `nothing_resolved`) | 0 |
| C 강제 되묻기 | B + `HARNESS_SMOKE_FORCE_LOOP=1` | 10 (A에서 신청서가 나온 표본) | 30 | 2 | 10 | 11 |

- A는 계획상 39표본이었으나 11에서 멈췄다 — 되묻기 표본이 안 모여 B로 바꿨다(사용자 결정).
- B는 NE 좌석 8을 목표로 돌렸으나 7표본에서 1이라 C로 바꿨다.
- ⚠ B의 첫 run(`gpu-np/0001`, 07:49Z 시작)이 A의 마지막 run(`gpu/0011`, 07:51Z 종료)과 **2분 50초 겹쳤다** —
  두 run이 같은 서버를 나눠 썼다. run_id가 달라 컨테이너 이름은 안 겹쳤다(#303 조건 아님). 겹친 구간의 호출 시간은
  비교에 쓰지 않는다.
- ⚠ B의 `gpu-np/0006`(sample_14)은 run 도중 워크트리를 `71f244c`로 올렸다 — 매니페스트는 시작 시점 `3251e67`이다.
  바뀐 것은 인덱서 입력 선택뿐이고, 그 run의 인덱서는 시작 직후에 이미 돌았다. 그래서 이 run은 **집계에서 빼지
  않았다** — B의 7표본·21좌석과 §5의 신청서에 들어 있다.

**hop0 신호**(좌석별, 세 조건 합계 84좌석)

| 좌석 | review | reject | need_evidence | 신호 밖 |
| --- | --- | --- | --- | --- |
| `scout_memory` | 12 | 15 | 1 | 0 |
| `scout_boundary` | 20 | 4 | 0 | 4 |
| `scout_injection` | 0 | 24 | 4 | 0 |

## 5. resolver 직접 평가 — 실제 신청서 48개

신청서는 hop0 `SCOUT_HOP`의 `requests`다. **신호와 무관하게 Scout가 채운 것**이고, 48개가 hop0 review 좌석 42 ·
need_evidence 좌석 5 · reject 좌석 1에서 나왔다. 각 신청서를 그 run의 `index.json`·원본 저장소·슬라이스 가시 줄로
`resolve()`에 **하나씩** 넣었다(hop 1 · `max_chars` 15,000). sample_07 run 셋은 색인이 없어(#314) 큰 헤더를 뺀 프로브
색인을 썼다(신청서 6개 — 결과 파일에 표시).

| 결과 | 수 |
| --- | --- |
| 블록을 돌려줌 | **40** |
| 비슷한 이름만 있음(`closest_name`) | 4 |
| 정의 없음(`not_found`) | 3 |
| 이미 SOURCE에 있음(95% 겹침) | 1 |

- 블록 169개 — `assignment to <필드·전역>` **95** · 같은 함수 안 대입 31 · 지역 선언 15 · 함수 본문 12 · 체인 타입 8 ·
  매크로 5. 요청 하나당 블록 중앙 1 · **최대 74**(`p->len` — 필드 이름이 같은 저장소 전체의 대입이 다 왔다).
- 요청 target의 식별자가 `ground_truth_path.md` 본문에 나오는 것 **28/48** — Scout는 GT가 짚는 이름을 묻고 있다.
- 블록이 GT 인용 구간과 겹친 것은 0 — GT 경로는 이미 슬라이스에 있으므로 그 줄은 「이미 SOURCE」로 빠진다.
- `not_found` 3 중 하나는 C++ 멤버 체인(`str.data`, sample_02), `closest_name`의 하나는 빌드 때 생긴 헤더 쪽으로 보이는
  설정 매크로(`ASN1_MAX_NAME_SIZE`) — 인덱서는 대상 트리 밖 `build_context/generated_headers`를 안 본다.
  [D89](../../open-questions/D/D89.md)가 같은 매크로(정의 `build_context/generated_headers/libtasn1.h:194`)를 실례로 들고
  선택지 (a)로 인덱스가 헤더에서 값을 찾는 길을 적는다 — 지금 인덱서로는 이 예가 안 풀린다.

## 6. 루프가 돈 좌석 — C의 10좌석

| 표본 | 좌석 | hop0 | 요청 → 받은 것 | 최종 | 멈춘 이유 |
| --- | --- | --- | --- | --- | --- |
| sample_07 | memory | review(강제) | 함수 2 → 함수 본문 2 | review | `triage_review` |
| sample_07 | injection | NE | `user_callback_handle_rxflow` → 본문 1; hop1 `wsi->a.protocol->callback` → 타입·대입 9 | NE | `repeat_request`(hop 2) |
| sample_11 | boundary | review(강제) | 변수 3 → 선언·대입 7 | review | `triage_review` |
| sample_06 | memory | reject(강제) | 매크로 1 → 매크로 1 | reject | `triage_reject` |
| sample_09 | boundary | review(강제) | 변수 3 → 선언·대입 17 | review | `triage_review` |
| sample_09 | injection | NE | `length` → 지역 선언 1 | NE | `repeat_request` |
| sample_08 | boundary | review(강제) | 필드·타입 2 → 타입·대입 5 | review | `triage_review` |
| sample_10 | memory | review(강제) | 함수 1 → 본문 1 | review | `triage_review` |
| sample_13 | memory | review(강제) | 함수(실은 매크로) 1 → 매크로 1 | **reject** | `triage_reject` |
| sample_13 | boundary | review(강제) | 필드 1 → 타입·대입 74 | review | `triage_review` |

- **판정이 바뀐 1건(sample_13 memory).** hop0은 `p->len` 역참조의 **null 가능성**으로 review(0.8)였다. 매크로
  `JS_VALUE_GET_STRING`(`quickjs.c:243`, 포인터 캐스트)을 받은 hop1은 「`JS_IsString` 검사가 p를 non-null로 만든다」로
  reject(0.1)했다. 그 검사는 실제로 슬라이스 48978행에 있어 null 가설을 버린 것 자체는 코드와 맞다. 그러나 이 표본의
  GT는 **CWE-843(타입 혼동)·CWE-125(범위 밖 읽기)**라 최종 판정은 GT와 반대다. hop0의 의심 근거도 GT의 메커니즘이
  아니었다 — **이 1건으로 「RAG가 판정을 해친다」를 말할 수 없다.**
- **review를 유지한 좌석 6 중 4는 hop1에 hop0과 같은 신청서를 다시 적었다** — 루프는 review에서 서므로 비용은 없다.
- **need_evidence 좌석 둘은 받고도 NE로 남았다.** 하나는 함수 포인터로 부르는 콜백의 **호출 대상**을 원했다 —
  구문 색인은 그것을 모른다(타입과 `.callback` 대입 9개를 줬다). 다른 하나는 `length`의 **값**을 원했는데 resolver는
  같은 함수 안 선언 1줄만 줬다(대입 줄은 없었다).
- 추가 호출 11건 wall 99.4~437.6 s(중앙 145 s), 입력 6,177~13,841토큰. hop2(13,841)가 가장 컸다. 창 초과 0.

## 7. 해석 — 이 run이 말하는 것과 말하지 않는 것

**말하는 것**
- resolver는 실제 Scout 신청서에 대해 대부분 원본을 돌려준다(48개 중 40 — 6개는 프로브 색인, §5). 돌려주는 분량은
  작다(중앙 825자).
- 설계 그대로의 Scout 풀에서는 이 입력으로 루프가 거의 안 돈다 — need_evidence가 드물고(84좌석 중 5), 드문 그것도
  다른 좌석의 review에 가려진다.
- 받은 코드가 Scout 판정을 바꾼 것은 10좌석 중 1이다.

**말하지 않는 것**
- **탐지 정확도.** 입력은 전부 실제 취약 경로(GT 유래)이고 표본당 하나라 음성 대조가 없다. `entry`가 정답 sink다.
- **운영 입력에서의 need_evidence 비율.** 슬라이서 산출은 표본당 수십~수백 슬라이스이고 잘려 나간 것이 다르다.
- **10좌석으로 판정 효과의 크기.** 1/10은 경향이 아니다. C의 8좌석은 설계상 되묻지 않을 좌석이었다(강제).
- **지금 main의 Scout.** 이 스모크의 Scout v3 프롬프트는 main의 것보다 한 판 이전이다(#307의 degraded 표식 설명이
  없다 — §9). 결과가 지금 main 프롬프트에 그대로 옮겨진다고 말하지 않는다.
- 신청서 48개 중 42개가 review 좌석에서 왔다 — **Scout가 「더 보고 싶은 것」을 적는 것과 손을 드는 것은 다르다.**

## 8. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| GT 슬라이스를 그대로 못 넣었다 | `code`에 정답 머리말 · `repo/` 경로 · `splitlines` 줄세기 | 원본에서 재조립하는 변환기(§2) — 스모크 입력 전용 |
| 설계 그대로는 루프 0 | 동료 확인 + 낮은 NE 비율 | 스모크 전용 스위치 둘(§4) |
| sample_07 색인 없음 | 데이터 배열 헤더 → 인덱서 300 s 초과 | 스모크 전용 128 KiB 상한(§9) — 본 결함은 [#314](https://github.com/0xM1yu/whitehat-boss/issues/314) |
| 색인 없는 run이 「못 찾음」으로 기록 | `resolve()`의 빈 결과와 루프 정지 규칙 | 집계에서 `graph_index.status`로 갈랐다 — 본 결함은 [#315](https://github.com/0xM1yu/whitehat-boss/issues/315) |
| 보충 체인이 앞 체인과 겹쳐 시작 | 대기 조건 `pgrep -f`가 상대 경로로 뜬 프로세스를 못 잡았다 | 겹친 2분 50초를 §4에 적었다 |

## 9. 스모크 브랜치 — 무엇을 얹었나

`smoke/ne-loop-atom`은 origin/main `fc8f888` 위에 #298 → #301(README 충돌만 손으로 풀었다) → #309(아래로 #302·#305·
#306을 품는다)를 차례로 합친 트리(`9dce869`)다. 그 뒤 여섯 PR은 main에 머지됐다 — **main에는 그 뒤로 슬라이서 PR
#300·#307·#308·#311도 들어가, 이 스모크의 Scout v3 프롬프트는 지금 main보다 한 판 이전 것이다**(#307의 degraded 표식
설명이 없다).

| 커밋 | 무엇 |
| --- | --- |
| `2dbe213` | 설정 — 프로필 `atom_ne_loop_fake`·`atom_ne_loop_scout`, 예산 `atom_ne_loop_smoke`, 실서빙 키 `request_timeout_s` 1,200 |
| `3251e67` | `HARNESS_SMOKE_NO_PEER_REVIEW=1`이면 동료 확인을 건너뛴다 |
| `71f244c` | 인덱서에 128 KiB 넘는 `.h`를 안 넘긴다 |
| `2bad718` | `HARNESS_SMOKE_FORCE_LOOP=1`이면 신청서를 채운 review·reject 좌석도 hop1을 한 번 받는다 |

스위치 둘은 환경 변수가 없으면 설계 그대로다. 이 브랜치에서 `test_config`의 선언 knob 테스트 1개가 빨갛다(knob을
선언해서) — 머지하지 않는 브랜치라 고치지 않았다. 나머지 2052 passed.

## 10. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| 운영 입력(슬라이서 산출)에서의 NE 비율·루프 동작 | 입력이 GT 경로 슬라이스였다 | 지금 main 슬라이서로 뜬 `export/slices.json`으로 같은 설정 run |
| 탐지 정확도에 대한 RAG의 효과 | 음성 대조 없음 · 좌석 10 · 정답지 비교 설계는 실험팀 몫(ADR 0012 결정 5) | 실험팀 설계 |
| 여유 512토큰 · RETRIEVED 글자/토큰 비 · `max_hops` 3의 근거 | 창 초과 0 · 3홉까지 간 좌석 0이라 상한이 안 물었다 | 더 긴 슬라이스·더 많은 되묻기 |
| Auditor 쪽 RAG | 이 스모크는 Auditor를 fake로 뒀다 | 별도 설계(계약 변경 필요) |
| 사고 토큰 | parser가 사고를 분리해 content만 받았다 — 사고 토큰 수는 안 쟀다 | 서버 로그의 reasoning 토큰 집계 |
