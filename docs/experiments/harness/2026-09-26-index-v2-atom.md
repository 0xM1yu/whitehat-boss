# index-v2 심볼 인덱스 — 표본 셋의 모델 입력 불변 확인과 Scout 풀 실서빙 — ATOM

> **왜 돌렸나.** Phase 4(#63 Refs)가 인덱서를 index-v2로 올렸다 — v1 필드는 그대로 두고 NE resolver가 쓸 조회 표
> 여덟을 더했고, `calls`를 callee로 묶었고, 헤더(`.h`)도 인덱서에 넘긴다. 확인할 것은 둘이다. ① **모델이 받는 입력이
> 바뀌는가** — 조회 표는 캡슐로 가면 안 되고(Scout 프롬프트가 서빙 창을 넘는 결함이 열려 있다), 헤더를 넣어
> v1 심볼·간선이 움직이면 캡슐이 바뀐다. ② **v2 이미지로 하네스가 실서빙까지 도는가.** 탐지 정확도를 잰 것이 아니다.
>
> **비교표 밖이다** — 실서빙 키의 `checksum`이 0이다([CLAUDE.md](../../../CLAUDE.md) §0). run은 머지하지 않는 스모크 브랜치
> 둘에서 돌았다 — `smoke/index-v2-atom`(feat/index-v2 `9bd1f65` + 스모크 설정)과 `smoke/index-v2-atom-main`(main
> `f15840c` + 같은 스모크 설정). 설정 커밋은 둘에 똑같이 얹었다(`0c2f7f2`·`edcf4ef`, diff 동일).

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-26 04:02 KST`(마지막 run `gpu-pr/run_20260925_0002` 종료). 서버 정리 04:03 |
| 실제 서빙 모델 | `hf://SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model@25d7f38146bdf0fad1eac09c586b1993ef3e6ee4`(별칭 `seccoderx-8b`) · `hf://Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18`(별칭 `qwen3-14b`). 확인 1은 모델 전부 fake |
| `repo_commit` | 확인 1 main 쪽 `edcf4efca25bff94f802f86743872bed77c66d75` · PR 쪽 `0c2f7f2e58f0a63060afb7bb3c1402eba9bd547d` / 확인 2 `0c2f7f2e58f0a63060afb7bb3c1402eba9bd547d` — 전부 `repo_dirty: false` |
| `run_id` | 확인 1 `~/index-v2/artifacts/fake-main/run_20260925_000{1,2,3}` · `fake-pr/run_20260925_000{1,2,3}` / 확인 2 `gpu-pr/run_20260925_000{1,2}` (run_id의 날짜는 UTC다) |
| run 산출물 경로·보존 여부 | ATOM `~/index-v2/artifacts/{fake-main,fake-pr,gpu-pr}` — 지우지 않았다. 저장소에는 없다 |
| `tool_runner` | 프로필 선언 `docker` / 실제 `docker`(오버라이드 없음) |
| `--device` | `atom` |
| `tool_images` digest | main 쪽 `harness-tools:v1` = `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17` · PR 쪽 `harness-tools:v2` = `sha256:09e5a5bf54d9c864b4a6f2502b0db9501bfbafc3df6920e7a392222899b1db47` — 종류: docker image config sha256(rootless docker, 로컬 빌드) |
| `--gpu-memory-utilization` | `seccoderx-8b` 0.20 · `qwen3-14b` 0.30 (분모 119.7 GiB) |
| 동시에 떠 있던 다른 서버 | 8000 `seccoderx-8b` + 8001 `qwen3-14b` 둘뿐. 기동 전 GPU 연산 프로세스·vllm·joern·다른 harness run이 전부 0이었다 |
| 원문 산출물 보존 위치 | 위 run 경로 + 대조·집계 스크립트 `~/index-v2/compare_fake.py`·`summarize_gpu.py` |
| 서빙 서버 stdout | ATOM `~/index-v2/logs/seccoderx-8b.20260926_030210.log` · `qwen3-14b.20260926_030400.log`(`--enable-log-requests --enable-log-outputs`, 파일명에 시각) |

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| **모델이 받는 입력이 바뀌는가** | **표본 셋에서는 안 바뀐다.** 슬라이스 241개를 main(index-v1)과 이 PR(index-v2)로 태운 fake run의 프롬프트 **412/412개가 바이트 동일**하다(`run_id`만 정규화 — Scout·Auditor·Critic 전부). ⚠ 표본 헤더에 함수 정의가 0개라서다 — 헤더에 정의가 있는 저장소에서는 그 정의가 심볼·간선이 되어 캡슐이 움직일 수 있고, 그런 표본은 안 쟀다(§5). (⏵ 2026-09-26 저녁 — 쟀다. 헤더 정의가 있는 평가 표본 sample_24에서는 **바뀐다**: Scout 프롬프트 207개 중 53개, Auditor 94개 중 23개 — §7) |
| v1 필드 | `symbols`·간선이 표본 셋 모두 **같다**(98·83·466 심볼 / 750·498·3,015 간선). `auditor_source` 이벤트도 같다 |
| 헤더 | 인덱서가 헤더 1·2·15개를 더 받는다. **표본 셋 헤더에는 함수 정의가 0개**라 심볼·간선이 안 움직였다. 파싱 오류는 그 헤더만큼 는다(+1·+1·+9) |
| 조회 표가 헤더에서 오는 몫 | sample_03 `macros` 707행 중 **489행(69%)**, `types` 100행 중 67행, `fields` 283행 중 144행이 헤더에서 왔다 — 헤더를 안 넘겼으면 빠졌을 행이다 |
| `index.json` 크기 | 2.2~2.6배 — 100,919 → 229,629 · 76,564 → 169,302 · 474,946 → 1,230,183 바이트 |
| 인덱서 시간 | `index` 도구 `wall_ms` 305 → 330 · 297 → 347 · 333 → 489 (run 하나씩 — n=1) |
| `tool_images` | run 기록이 이미지를 따라간다 — main 쪽 `harness-tools:v1` digest, PR 쪽 `harness-tools:v2` digest |
| **실서빙** | 이 PR 하네스(v2 이미지)로 Scout 풀 **33호출** — `run_20260925_0001`(슬라이스 8) **24/24 성공 · 거절 0**(서버 `input_tokens` 최대 18,003) · `run_20260925_0002`(슬라이스 3) **3/9** — 슬라이스 둘이 **40,960 창도 넘는다**(렌더 60,181·184,248자, index-v2와 무관 — §4) |
| 감시자 | **개입 0회** |

## 2. 실험 환경

- **장비** — ATOM `atom-host`. GB10 · 통합메모리 119.7 GiB · 사용자 cgroup `memory.max` 80 GiB · aarch64 20코어.
  vLLM 0.27.1(`~/venv-probe`, Python 3.12). docker는 rootless다(`~/.local/share/docker`) — 도구 컨테이너도 사용자 cgroup에 든다.
- **이미지** — `harness-tools:v2`를 `~/index-v2/wt`(feat/index-v2 + 스모크 설정)의 `tools/harness-tools/`에서 빌드했다
  (`--build-arg SEMGREP_RULES_COMMIT=40b8c63f75dc7c22c8a77482d73bfb864b146f7e` — v1 이미지의 룰셋 커밋. 기본값이 `develop`이라
  그냥 빌드하면 정적 신호까지 움직인다). 빌드 1분 45초, Dockerfile의 인덱서 연막 검사 통과. **두 이미지의 도구 판이 같다** —
  clang 14.0.0-1ubuntu1.1 · semgrep 1.86.0 · 룰셋 `40b8c63f` · tree-sitter 0.26.0 · tree-sitter-c 0.24.2 · tree-sitter-cpp 0.23.4.
  다른 것은 `/opt/indexer.py` 하나다. v1 이미지는 그대로 두었다.
- **노드 이름 확인은 이미지에서도 같다** — 테스트 픽스처(`tests/test_indexer.py`의 v2 절 셋)를 호스트(Windows x86_64 ·
  Python 3.14.5)와 v2 이미지(aarch64 · Python 3.10) 인덱서로 각각 돌려 **파싱한 JSON이 동일**했다(호스트 파일은 CRLF라
  바이트는 다르다 — Windows `write_text`의 줄바꿈이고 컨테이너는 LF다).
- **입력** — 표본 셋 v2 슬라이스 241개(`~/slice-smoke/inputs/` — 2026-09-25 스모크와 같은 파일, 읽기만) ·
  표본 저장소 `slicer-smoke-20260914-512ab693/inputs/sample_0{1,2,3}/repo`(읽기만).
- **안전장치** — 감시자(`~/index-v2/guard.sh`, 2026-09-25 판을 디렉터리 이름만 바꿔 복사)가 5초마다 장비 `MemAvailable` < 12 GiB ·
  cgroup anon > 72 GiB · 마감 6시간을 보고, 걸리면 이 스모크가 띄운 프로세스 그룹만 내린다. 서버 둘과 harness run 셋의
  프로세스 그룹을 모두 등록했다.

## 3. 확인 1 — main vs 이 PR, fake 모델, 슬라이스 241개

프로필 `atom_index_v2_fake`(모델 전부 fake · 도구 docker). 표본마다 main 쪽(`harness-tools:v1`)과 PR 쪽(`harness-tools:v2`)을
같은 슬라이스로 돌려 **산출물을 대조했다**(`compare_fake.py` — run을 `slicer.run_signature`로 짝짓는다).

| 표본 | 슬라이스 | 프롬프트 동일 | 심볼 | 간선 | 파일 | 파싱 오류 | 후보 / Finding |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sample_01 | 68 | **144/144** | 98 = 98 | 750 = 750 | 5 → 6 (+`src/uftpd.h`) | 0 → 1 | 68/68 = 68/68 |
| sample_02 | 30 | **68/68** | 83 = 83 | 498 = 498 | 4 → 6 (+`cJSON.h` 등 2) | 0 → 1 | 30/30 = 30/30 |
| sample_03 | 143 | **200/200** | 466 = 466 | 3,015 = 3,015 | 140 → 155 (+ 헤더 15) | 87 → 96 | 143/49 = 143/49 |

- 프롬프트는 `prompts/*.md` 파일을 `run_id`만 바꿔 바이트로 비교했다. fake 모델의 프롬프트 판은 v1이다 — 실 Scout(v3)가 받는
  프롬프트도 같은 캡슐·같은 `SOURCE`로 만든다(렌더 함수가 같다). **그래서 v3 프롬프트가 같다는 것은 이 대조에서 나온 추론이다**
  (v3를 main 쪽으로 실서빙해 재지는 않았다 — §5).
- 간선 수는 중복을 뺀 (caller, callee, file, line) 집합이다. `graph_index` 이벤트의 `calls`(중복 포함)도 753 · 527 · 3,038로 같다.
- 더해진 파일은 전부 `.h`이고 빠진 파일은 0이다. 늘어난 파싱 오류도 전부 그 헤더다.

**v2 조회 표 크기**(PR 쪽 `index.json`, 키 수 / 행 수)

| 표 | sample_01 | sample_02 | sample_03 |
| --- | --- | --- | --- |
| `functions` | 97 / 98 | 82 / 83 | 431 / 466 |
| `macros` | 21 / 21 | 19 / 19 | 530 / 707 |
| `types` | 3 / 3 | 4 / 6 | 96 / 100 |
| `file_vars` | 20 / 32 | 4 / 4 | 60 / 93 |
| `fields` | 35 / 35 | 21 / 21 | 168 / 283 |
| `field_assigns` | 25 / 76 | 11 / 97 | 153 / 709 |
| `calls` | 181 / 753 | 93 / 527 | 511 / 3,038 |
| `locals`(함수 / 이름) | 97 / 398 | 82 / 336 | 463 / 2,152 |
| `assigns`(함수 / 이름) | 51 / 113 | 46 / 142 | 281 / 814 |

헤더에서 온 행 — `macros` 19 · 19 · 489, `types` 2 · 4 · 67, `fields` 33 · 10 · 144, `file_vars` 11 · 0 · 13, `functions` 0 · 0 · 0.

⚠ 이 run은 PR의 `fields` 수정(`68d4de5` — 이름 있는 필드의 익명 struct 타입은 바깥 struct가 주인이 아니다) **전** 인덱서로
돌았다. 수정 후 인덱서(`68d4de5`의 `indexer.py`)를 같은 v2 이미지에 얹어 같은 파일 집합으로 다시 뽑으니 **`fields` 밖의 키는 전부
같고**, `fields`는 sample_03에서 283행 중 2행만 바뀌었다(sample_01·02는 0행).

## 4. 확인 2 — Scout 풀 실서빙(이 PR)

프로필 `atom_index_v2_scout`(Scout 풀만 실서빙 · Auditor·Critic fake). 서빙 창은 **운영 값** `--max-model-len 40960` ·
`--max-num-seqs 4` · `--reasoning-parser deepseek_r1`이다. 슬라이스는 2026-09-25 스모크 run `0005`·`0006`이 쓴 부분집합 그대로다
(`s1_sample_02.json` 8개 · `s2_sample_03.json` 3개 — 그날은 16,384 창이라 12건이 거절됐다).

**서버**(서버 로그) — `seccoderx-8b` 가중치 15.27 GiB · 로드 83.7 s · KV **57,904**토큰(40,960 기준 동시성 1.41배) /
`qwen3-14b` 27.52 GiB · 172.3 s · KV **48,368**토큰(1.18배). 둘 다 KV가 40,960보다 크다. 두 서버가 떠 있을 때 장비
`MemAvailable` 최저 49.3 GiB(감시자 로그).

| run | 슬라이스 | Scout 호출 | 성공 | 거절(400) | 후보 | Finding |
| --- | --- | --- | --- | --- | --- | --- |
| `gpu-pr/run_20260925_0001` | 8 | 24 | 24 | **0** | 24 | 11 |
| `gpu-pr/run_20260925_0002` | 3 | 9 | 3 | **6** | 3 | 1 |

**좌석별**(`0001`, 서버가 센 `input_tokens`)

| 좌석 | 모델 | `input_tokens` min · p50 · max | `output_tokens` p50 · max | wall p50 · max | 첫 시도 통과 |
| --- | --- | --- | --- | --- | --- |
| `scout_boundary` | `seccoderx-8b` | 4,234 · 5,461 · 15,403 | 1,086 · 1,893 | 93.4 · 148.8 s | 8/8 |
| `scout_injection` | `qwen3-14b` | 6,766 · 8,271 · 17,935 | 784 · 5,008 | 106.9 · 642.8 s | 7/8 |
| `scout_memory` | `qwen3-14b` | 6,834 · 8,061 · 18,003 | 803 · 1,515 | 106.2 · 192.4 s | 8/8 |

- **스키마** — 성공한 27호출이 전부 `Candidate`로 통과했고 첫 시도에 26건이다. 두 번 시도한 1건(`scout_injection`)은 wall
  642.8 s · `output_tokens` 5,008(두 시도 합)이다. 첫 시도의 실패 사유는 trace에 없다. 시도별 시간이 없어 기본 요청 타임아웃
  300 s에 걸렸을지는 모른다 — 이 스모크는 1,200 s를 선언했다(#261).
- **16,384에서 거절되던 프롬프트가 들어간다** — `0001`의 최대 `input_tokens` 18,003 · 17,935 · 15,403은 그날 입력 몫 12,288을
  넘는 값이다. `0002`의 `slice_0038`(73줄)은 2026-09-25 보고서가 절별로 센 바로 그 거절 프롬프트(50,949자)이고, 이번에는
  `input_tokens` 15,044로 들어갔다.
- **판정**(`reason_codes[0]`의 `:` 앞 · 점수, `0001` 24건) — `scout_boundary` review 6(그중 2건은 형식을 벗어난 `triage_review@file`·
  `triage_review@main@L169`) · reject 2(**점수 1.0** — reject인데 1.0) / `scout_injection` need_evidence 2 · reject 6 /
  `scout_memory` review 3 · need_evidence 3 · reject 2. 임계 0.5로 승격 11 · 대기 13.
- 첫 슬라이스의 좌석 셋 `input_tokens` 8,339 · 5,739 · 8,271은 2026-09-25 보고서 §4의 예시와 같은 값이다(그 run은
  `41f802a` · `harness-tools:v1`).

### 40,960 창도 넘는 슬라이스 — index-v2와 무관

`0002`의 거절 6건은 **슬라이스 둘**(좌석 셋씩)이다. 서버 문구는 6건 모두 같다.

> `This model's maximum context length is 40960 tokens. However, you requested 4096 output tokens and your prompt
> contains at least 36865 input tokens, for a total of at least 40961`

| 슬라이스 | 줄 | 프롬프트(`scout_memory`) | `## Source`(슬라이스 렌더) | `## FACTS` |
| --- | --- | --- | --- | --- |
| `slice_0038` (통과) | 73 | 50,949자 · 15,044토큰 | 2,212자 | 13,081자 |
| `slice_0293` | 1,932 | 151,091자 | 60,181자 | 63,828자 |
| `slice_0341` | 5,626 | 404,350자 | 184,248자 | 193,012자 |

- 커지는 것은 **슬라이스 렌더와, 같은 원문을 다시 싣는 `FACTS`**다. 두 거절 프롬프트에서 Capsule 절은 1,500자 미만이었다(집계 필터 기준).
- **index-v2가 만든 것이 아니다** — 세 슬라이스 모두 §3 대조 집합(sample_03의 143개)에 들고, 그 집합에서 main과 이 PR의 프롬프트가
  바이트 동일했다(가장 큰 fake Scout 프롬프트 379,238바이트 포함).
- 운영 창 40,960에서도 **줄 수가 수천인 슬라이스는 넘는다** — 「운영 창으로 띄우면 대부분 사라진다」는 이 부분집합에서
  `s1`(8/8)과 `slice_0038`까지 맞았고 큰 슬라이스 둘에서는 안 맞았다. 이 결함의 등재·처리는 [D87](../../open-questions/D/D87.md)이다(PR #293에서 등재).

## 5. 해석 — 이 run이 말하는 것과 말하지 않는 것

**말하는 것**

1. **index-v2는 표본 셋에서 모델 입력을 바꾸지 않는다** — 프롬프트 412/412 바이트 동일. 조회 표는 캡슐로 가지 않았고,
   헤더를 넣어도 v1 심볼·간선이 그대로다(헤더에 함수 정의가 0개).
2. **헤더가 조회 표의 큰 몫이다** — sample_03 매크로의 69%가 헤더에서 온다. 헤더를 안 넘겼으면 다음 단계 resolver가 그만큼 못 찾는다.
3. **v2 이미지로 하네스가 실서빙까지 돈다**(§4) — Scout 풀 24/24(`0001`) · 스키마 첫 시도 26/27.
4. **운영 창 40,960에서도 줄 수가 수천인 슬라이스는 Scout 입력 몫을 넘는다**(§4 끝) — 슬라이스 렌더와 `FACTS`의 원문 중복이 원인이고 index-v2와 무관하다.

**말하지 않는 것**

- **헤더에 함수 정의가 있는 저장소는 재지 않았다.** 표본 셋 헤더는 정의 0개라, 헤더 `static inline` 헬퍼가 캡슐 callers를
  채우거나 Auditor 앵커가 되는 경로는 이 run에서 안 탔다. 그 경우 캡슐이 바뀔 수 있고 섹션 구성(항목 수·섹션 유무)으로 봐야 한다.
  (⏵ 2026-09-26 저녁 — §7이 헤더 정의가 있는 sample_24로 쟀다.)
- **C++ 헤더를 C 문법으로 파싱한 품질은 재지 않았다**(표본에 C++ 0개).
- main 쪽 v3 프롬프트를 실서빙하지 않았다 — 프롬프트가 바이트 동일하므로 서버가 세는 `input_tokens`도 같다는 것은 **추론**이다.
- 탐지 품질·NE resolver 동작은 안 쟀다(resolver는 다음 단계다).
- 실서빙 키의 `checksum`이 0이라 비교표에 들어가지 않는다.

## 6. 따라온 것

- `fields` 주인 결함을 PR 안에서 고쳤다(`5661585` RED → `68d4de5` GREEN) — §3 끝.
- 운영 창에서도 넘는 큰 슬라이스(§4 끝) — Scout 프롬프트 초과 결함 [D87](../../open-questions/D/D87.md)(PR #293에서 등재)의 관측으로 넘긴다.
- 대상 트리 밖 헤더(`build_context/generated_headers`)는 여전히 인덱서에 안 온다 — 표본 셋에는 없지만 평가 표본에는 있다(슬라이서는 `-I`로 본다).
- **헤더에 함수 정의가 있는 표본으로 다시 잰다**(§5 「말하지 않는 것」 첫 항목) — Phase 5가 이 인덱스에 기대기 전에 한다(PR #295 리뷰 LOW-2). 볼 것은 캡슐 섹션 구성(callers·callees 항목 수)과 Auditor 앵커다. (⏵ 2026-09-26 — (a) 메커니즘은 PR #301의 테스트 다섯 개가 핀했다: Auditor 앵커가 헤더 `static inline` 함수의 정확한 범위를 잡는 것(`tests/test_slice_targets.py`), 캡슐 `callers`·`callees`가 헤더에 정의된 함수를 담는 것(`tests/test_index_graph.py`), 비슬라이스 run에서 함수 정의가 있는 `.h`가 `_Target`을 더 만드는 것(`tests/test_pipeline_e2e.py`), 실 인덱서가 헤더 함수를 v1 `symbols`·v2 `functions` 표에 `file: util.h`로 내는 것(`tests/test_indexer.py`). (b) 헤더 함수 정의가 있는 ATOM 실표본 측정은 §7이다.)
- **동시에 돈 run 둘이 컨테이너 이름을 겹쳐 도구가 조용히 실패한다** — §7 끝. §7의 첫 시도에서 드러났고 이슈 #303으로 넘겼다.

## 7. 추가 측정 — 헤더에 함수 정의가 있는 표본 (sample_24, 2026-09-26 저녁)

> **왜 쟀나.** §5 「말하지 않는 것」 첫 항목과 §6 — 표본 셋은 헤더 함수 정의가 0개라 헤더 `static inline` 함수가 캡슐 `callers`·`callees`와
> Auditor 앵커에 들어오는 경로를 안 탔다. Phase 5(NE resolver)가 이 인덱스에 기대기 전에 재기로 했다. 확인하는 것은 §3과 같다 —
> **모델이 받는 입력이 바뀌는가**다. 탐지 정확도를 잰 것이 아니다(모델은 전부 fake).

### 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-26 19:51 KST`(v2 쪽 재실행 종료) |
| 장비 | ATOM `atom-host` — 착수 전 GPU 연산 프로세스·docker 컨테이너·다른 harness run 0, `MemAvailable` 115 GiB |
| 모델 | 전부 fake — 프로필 `atom_index_v2_fake`(두 스모크 브랜치의 설정 커밋, §9) |
| 두 트리 | v1 = `smoke/index-v2-atom-main` `edcf4ef`(→ `harness-tools:v1`) · v2 = `smoke/index-v2-atom` `0c2f7f2`(→ `harness-tools:v2`). 두 run 모두 `repo_dirty: false` |
| `tool_images` digest | v1 `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17` · v2 `sha256:09e5a5bf54d9c864b4a6f2502b0db9501bfbafc3df6920e7a392222899b1db47`(§메타와 같다) |
| 표본 | 평가 표본 `sample_24` — ATOM `/srv/tenants/user/release-eval-slices-20260918/samples/sample_24/repo`(읽기만). `.h` 54개, `.c` 86개 |
| 슬라이스 | 같은 디렉터리의 `runs/sample_24/export/slices.json`(2026-09-18 슬라이서 산출, 읽기만) — 207개, `run_signature` `b6f3e0e39a09…`. entry가 `.h`에 있는 것 23개, `.h` 줄을 지나는 것 50개 |
| `run_id` | v1 `~/index-v2/artifacts/h-main/run_20260926_0002` · v2 `~/index-v2/artifacts/h-pr/run_20260926_0002`(날짜는 UTC). **버린 run 둘** — 같은 경로의 `run_20260926_0001` 둘(아래 「첫 시도」) |
| 대조 스크립트 | `~/index-v2/compare_fake.py`(§9) · `~/index-v2/compare_caps.py`(이번에 새로 — **슬라이스 id로 짝짓는다**, 아래) |
| 감시자 | `guard.sh`(§2와 같은 판) — 개입 0회 |

**표본을 고른 방법.** 평가 표본 40개의 저장소를 `static inline … {` 꼴 정규식으로 훑으면 **26개**가 헤더에 그런 정의를 가진다
[정규식 휴리스틱 — 선언·매크로를 섞을 수 있다]. 그중 슬라이서 산출(2026-09-18)이 비어 있지 않고 크기가 작은 것을 골랐다
(sample_13·08은 슬라이스 0개였다).

### 결과

| 항목 | v1 | v2 |
| --- | --- | --- |
| 인덱스 파일 | 87(건너뜀 8 — 전부 `.inc`) | 149(건너뜀 0) — `.h` 54 + `.inc` 8 더 |
| 심볼 · 간선 | 1,202 · 7,473 | 1,386 · 8,124 |
| 파싱 오류 | 25 | 41(+11 `.h` · +5 `.inc`) |
| v2 `functions` 행 중 헤더에서 온 것 | — | **130** |
| 후보 · Finding(fake) | 207 · 94 | 207 · 94 |
| 타깃 순서 · 정적 신호 파일 | 같다(207/207 같은 자리) · 19 | 같다 · 19 |

**모델 입력** — 같은 슬라이스의 프롬프트끼리 짝지어, `run_id`·`cand_`·`ev_`·`find_` id를 정규화한 뒤 비교했다.

| 역할 | 짝 | 같다 | 다르다 | 무엇이 다른가 |
| --- | --- | --- | --- | --- |
| Scout | 207 | 154 | **53** | 캡슐 `callers` 항목 **756 → 1,698**(44개 프롬프트) · `callees` **274 → 182**(41개) |
| Auditor | 94 | 71 | **23** | FACTS 대상이 바뀐 5개 — 전부 헤더 entry다. v1은 50줄 창(심볼 없음, 예 `cutest.h` 292-392), v2는 헤더 함수의 정확한 범위(`test_error__` 342-361) · 걸친 함수에 헤더 `static inline`이 들어온다(예 `bitops.h`의 `rotl32`) |
| Critic | 7 | 5 | 2 | `dec_` id 번호뿐(정규화 대상 밖) — 1건씩은 한쪽만 불렸다 |

`auditor_source` — 94개 중 **23개**가 바뀐다. 걸친 함수 수가 늘고 `uncovered_lines`가 0으로 준다.
- 예 `slice_0054`: 함수 2 → 4, 덮지 못한 줄 11 → 0.
- 가장 큰 변화 `slice_0058`: 함수 1 → 9, 덮지 못한 줄 399 → 115, SOURCE 3,093 → 13,385자. 상한 15,000자 안이다.

### 해석

- **헤더에 함수 정의가 있으면 index-v2는 모델 입력을 바꾼다.** 표본 셋의 「412/412 동일」(§3)은 헤더 정의가 0개인 표본의 성질이었다.
- **`callers`가 느는 것** — 헤더 함수가 인덱스의 정의가 되어, 그 함수로 들어오는 호출이 잡힌다. 예 `slice_0052`는 0 → 44건.
- **`callees`가 주는 것** — 슬라이스가 걸친 헤더 줄 안에 정의된 callee는 이제 「본문 안에 정의」라 다시 싣지 않는다(#288의 슬라이스 본문 규칙). v1에서는 그 정의를 몰라 바깥 callee로 적었다.
  - 예 `slice_0054`: `read32_le` 8건이 빠진다. 24 → 8이고, 빠진 것은 전부 `bitops.h`에 정의된 함수다.
  - 설계대로다 — 이미 SOURCE에 보이는 정의를 중복으로 싣지 않는다.
- **Auditor** — 헤더 entry가 50줄 창 대신 정확한 함수 범위를 받고, 슬라이스가 지나는 헤더 헬퍼가 SOURCE에 들어온다. PR #301의 테스트가 핀한 메커니즘이 실표본에서 그대로 돈다.

### 말하지 않는 것

- 표본 **하나**다. 26개 중 나머지의 크기는 안 쟀다.
- 탐지 품질 — 모델이 fake라 Finding 94 = 94는 품질이 아니다.
- 입력이 커진 만큼 실서빙 창(Scout 입력 몫 36,864토큰)에 미치는 영향은 안 쟀다. `callers` 1,698건은 [D87](../../open-questions/D/D87.md)(Scout 프롬프트 초과)과 같은 쪽으로 민다 **[추정]**.
- `.inc` 8개를 C 문법으로 파싱한 품질(파싱 오류 +5)은 안 봤다.

### 첫 시도 — 버렸다: 동시에 돈 run 둘이 컨테이너 이름을 겹쳤다

- **무엇을 했나** — 처음에 두 트리의 run을 **동시에** 띄웠다.
- **무엇이 겹쳤나** — 두 run은 artifacts 루트가 달라도 `run_id`가 둘 다 `run_20260926_0001`이다. 도구 컨테이너 이름은 `harness-{run_id}-{evidence_id}-{index}`(`src/adapters/container_tools.py:828`)라 두 run이 **같은 이름**을 만든다.
- **결과** — 도구 호출 **12건씩**이 `docker run` exit 125로 실패했다(`h-main`·`h-pr`의 `run_20260926_0001`). v1은 인덱서가 실패해 인덱스가 아예 없었고(`graph_index: missing`), v2는 semgrep 일부가 실패했다(예 `boot.c` — `signal_artifacts` 19 → 16).
- **그래도 두 run 다 「완주」였다.** Finding은 v1 84 · v2 60으로, 인덱스와 무관하게 벌어져 있었다.
- **어떻게 했나** — 둘 다 **하나씩 따로** 다시 돌렸다(`run_20260926_0002`, exit 125 0건). 위 결과는 그 둘이다.
- **하네스 결함이다** — 같은 장비에서 run 둘을 나란히 돌리면(Phase 7의 프로세스 병렬이 그 꼴이다) 도구 실패가 조용히 Evidence 결손이 된다. 이슈 #303으로 넘겼다.

### 재현

```bash
MAX_HOURS=3 setsid nohup bash ~/index-v2/guard.sh > /dev/null 2>&1 < /dev/null &
bash ~/index-v2/run_h.sh h-main ~/index-v2/wt-main ~/index-v2/artifacts/h-main atom_index_v2_fake /srv/tenants/user/release-eval-slices-20260918/samples/sample_24/repo /srv/tenants/user/release-eval-slices-20260918/runs/sample_24/export/slices.json
bash ~/index-v2/run_h.sh h-pr ~/index-v2/wt ~/index-v2/artifacts/h-pr atom_index_v2_fake /srv/tenants/user/release-eval-slices-20260918/samples/sample_24/repo /srv/tenants/user/release-eval-slices-20260918/runs/sample_24/export/slices.json
python3 ~/index-v2/compare_fake.py ~/index-v2/artifacts/h-main ~/index-v2/artifacts/h-pr
python3 ~/index-v2/compare_caps.py ~/index-v2/artifacts/h-main ~/index-v2/artifacts/h-pr
touch ~/index-v2/STOP_GUARD
```

- `run_h.sh`는 `run_chain.sh`의 변형이다 — 표본 저장소와 슬라이스 경로를 직접 받는다.
- **두 run은 반드시 차례로 돈다**(위 「첫 시도」).
- `compare_fake.py`의 프롬프트 줄은 파일 이름으로 짝지으므로, 타깃 순서가 다르면 틀린 짝을 비교한다. 슬라이스 단위 대조는 `compare_caps.py`로 한다.

## 9. 부록 — 재현 절차

**설정** — `git diff f15840c smoke/index-v2-atom-main -- config/`(3파일, 두 스모크 브랜치 동일). 스모크 프로필 둘
(`atom_index_v2_fake`·`atom_index_v2_scout`) · 예산 `atom_index_v2_smoke` · 실서빙 키 둘의 `request_timeout_s` 1200.
**머지하지 않는다.** 이 두 브랜치에서 `test_config`·`test_monitor`의 선언 knob 테스트 2개가 빨갛다(knob을 선언해서).

**실제로 친 명령**(ATOM, `~/index-v2`)

```bash
MAX_HOURS=6 setsid nohup bash ~/index-v2/guard.sh > /dev/null 2>&1 < /dev/null &
docker build -t harness-tools:v2 --build-arg SEMGREP_RULES_COMMIT=40b8c63f75dc7c22c8a77482d73bfb864b146f7e ~/index-v2/wt/tools/harness-tools/

# 확인 1 — run_chain.sh는 run을 차례로 돌리고 끝나면 DONE_<이름> 표시 파일을 쓴다
bash ~/index-v2/run_chain.sh fake-main ~/index-v2/wt-main ~/index-v2/artifacts/fake-main atom_index_v2_fake sample_01:sample_01.json sample_02:sample_02.json sample_03:sample_03_resume.json
bash ~/index-v2/run_chain.sh fake-pr ~/index-v2/wt ~/index-v2/artifacts/fake-pr atom_index_v2_fake sample_01:sample_01.json sample_02:sample_02.json sample_03:sample_03_resume.json
python3 ~/index-v2/compare_fake.py ~/index-v2/artifacts/fake-main ~/index-v2/artifacts/fake-pr

# 확인 2
bash ~/index-v2/launch.sh seccoderx-8b 8000 0.20 40960 4 SecCoderX/SecCoderX_Reasoning_Vulnerability_Detection_Reward_Model --revision 25d7f38146bdf0fad1eac09c586b1993ef3e6ee4 --reasoning-parser deepseek_r1
bash ~/index-v2/launch.sh qwen3-14b 8001 0.30 40960 4 Qwen/Qwen3-14B --revision 40c069824f4251a91eefaf281ebe4c544efd3e18 --reasoning-parser deepseek_r1
bash ~/index-v2/run_chain.sh gpu-pr ~/index-v2/wt ~/index-v2/artifacts/gpu-pr atom_index_v2_scout sample_02:s1_sample_02.json sample_03:s2_sample_03.json
python3 ~/index-v2/summarize_gpu.py ~/index-v2/artifacts/gpu-pr/run_*
bash ~/index-v2/stop.sh
touch ~/index-v2/STOP_GUARD
```

`run_chain.sh`의 run 한 번은 `python -m src.cli run --profile <프로필> --target <표본>/repo --slices <슬라이스> --artifacts-root <루트>
--device atom --condition B3prime@pipeline`이다. 완료는 `pgrep`이 아니라 표시 파일로 기다렸다 — `pgrep -f`는 그 명령줄을 담은 셸
자신을 잡아 영원히 기다린다(2026-09-25 스모크가 밟았다).
