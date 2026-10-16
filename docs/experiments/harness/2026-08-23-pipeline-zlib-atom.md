# 심볼 단위 후보로 zlib 완주 — ATOM

> 대상: PR #25(`feat/symbol-targets`, W2·D20 + 진단 장비 둘). 이 브랜치의 첫 실대상 검증이다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | **미기재** — 원자료에 없다. [TEMPLATE.md](../TEMPLATE.md) 첫 필드가 이것 때문에 생겼는데 또 비었다 |
| 실제 서빙 모델 | 별칭 `qwen3-4b` (fp8). **revision 미기재** — 서버 기동 로그를 보존하지 않았다 |
| `repo_commit` | `28754ec951e5e7a0a2ae15129808d74b4a53d635` · dirty **False** (base `main` = `672434a`) |
| `run_id` | `run_20260823_0001`(픽스처) · `run_20260823_0002`(zlib) |
| run 산출물 경로·보존 | `/srv/tenants/user/artifacts` — **보존 여부 미기재** |
| `tool_runner` | 프로필 선언 `fake` / 실제 `docker` (`--tool-runner docker` 오버라이드) |
| `--device` | `atom` |
| `tool_images` digest | **미기재** |
| `--gpu-memory-utilization` | `0.2` — 분모는 물리 통합메모리 **119.7 GiB**([README.md](../README.md) 확정 #4) |
| 동시에 떠 있던 다른 서버 | **미기재** |
| 원문 산출물 보존 위치 | run 산출물은 위 경로. **서빙 서버 stdout은 보존하지 않았다** — 포그라운드로 띄우고 창을 닫았다 |

## 1. 요약

**무엇을 확인하려 했나**: 후보 단위를 파일에서 심볼로 옮긴 배선(W2)이 실저장소 규모에서
성립하는가. 직전 zlib run이 후보 33건째에서 HTTP 400으로 죽고 산출물을 하나도 안 남긴
것이 이 PR의 계기다.

**성능 주장과 경로 성립 주장을 섞지 않는다.** 이 실험이 재는 것은 "돈다"이고 "빠르다"는
재지 않았다 — 비교 대상인 직전 run은 완주하지 못했으므로 두 시간을 나란히 두면 안 된다.

| 항목 | 결과 |
| --- | --- |
| 심볼 단위 target | **성립** — zlib artifact 79건에서 심볼 542건 |
| 실저장소 완주 | **성립** — 18분 54초, `stopped_after` None, `reason` None |
| 최대 단일 프롬프트 | **9,707 / 32,768 tok** (여유 23,061) |
| 후보 단위 실패 | **0건** — 게이트웨이 0 · 스키마 0 |
| (a′) 정렬 | **성립** — 신호 붙은 파일 19건이 하나도 안 잘렸다 |
| 절단 기록 | **성립** — `target_skipped` 286건, 전부 `over_candidate_max` |
| Scout가 받은 그래프 | **성립** — scout 프롬프트에 그래프 줄이 실렸다 (D20·D19 ③) |
| `location_ok` | **여전히 전부 false** — 55건. PR #25 §5 정정이 실측으로 확인됐다 |
| 8단계 도구 검증 | **0건** — §3.4의 사슬 |
| prefix cache 서버 측 재사용률 | **미기재** — 서빙 stdout 유실 |

**이 run이 태우지 않은 것**: 4-1(HTTP 400 본문 보존)과 4-2(후보 단위 실패 생존)의 실패
경로. 400이 한 건도 안 났고 폐기도 0건이라 **조건이 성립하지 않았다.** 두 장비는
테스트로만 고정돼 있고 실대상 실측은 아직 없다(§5).

## 2. 실험 환경

| 항목 | 값 |
| --- | --- |
| 장비 | GIGABYTE AI TOP ATOM (NVIDIA GB10, sm_121, aarch64) |
| 프로필 | `single_gpu_12g` — scout만 실서빙, auditor·critic·retriever는 Fake |
| 서버 기동 | `--gpu-memory-utilization 0.2 --max-model-len 32768 --enable-prefix-caching` |
| 도구 런타임 | docker (프로필 선언은 `fake`, 오버라이드) |
| 대상 | 실험 1 = `tests/fixtures/target` (parser.c · util.c) / 실험 2 = zlib |

**갈리는 항목을 먼저 적는다**: 이 run은 `--device atom`이고 직전 비교 대상도 atom이다.
갈리는 것은 **코드**(브랜치 `28754ec` vs `main` 계열)와 **대상 규모**뿐이다.

## 3. 실험 1 — 픽스처에서 배선이 성립하는가 (`run_20260823_0001`)

### 목적

**경로 성립 판정이다.** 심볼 단위 target이 실제로 만들어지고 캡슐이 심볼 줄 범위로
좁혀지는지, 그 그래프가 scout 프롬프트까지 가는지를 작은 대상에서 먼저 본다.

### 조건

`single_gpu_12g` · `--tool-runner docker` · 대상 `tests/fixtures/target`(파일 2건).

### 결과

| 항목 | 결과 |
| --- | --- |
| 소요 | 21.195 s · 완주 |
| 후보 / Finding | 4건 / 2건 |
| `target_plan` | analyzable 2 · symbol_targets **4** · fallback **0** · no_symbol **0** · signal 2 · planned 4 · deferred 0 · candidate_max 256 |
| 캡슐 | 6건 — 전부 **심볼 단위** |
| `gate_eval` | E2 2건 · E3 4건 · `location_ok` **6건 전부 false** |
| report.md | queued 2건 |

### 근거 로그

캡슐 6건이 전부 심볼과 줄 범위를 들고 있다. 파일 단위였다면 `symbol`·`line_start`가 비고
`target_function`이 파일 전체였을 자리다.

```
cap_0001 light    parser.c parse_header  19-26
cap_0002 expanded parser.c parse_header  19-26
cap_0003 light    parser.c recv_request  28-31
cap_0004 light    util.c   copy_name     14-24
cap_0005 expanded util.c   copy_name     14-24
cap_0006 light    util.c   name_length   26-29
```

프롬프트에 실린 그래프 줄(`함수명 parser.c:N` 패턴) 개수:

```
0001_scout   6      0002_auditor 6
0003_scout   2      0004_scout   0
0005_auditor 0      0006_scout   0
0007_critic  0      0008_critic  0
```

### 해석

**① D20을 (a)로 정한 결과가 scout 프롬프트에서 보인다.** `0001_scout` 6줄 ·
`0003_scout` 2줄이다. `run_20260821_0007`에서는 같은 자리가 **0개**였고 그래프를 담은
프롬프트가 auditor 것 하나뿐이었다 — 그것이 D20 관측 ③의 실측 근거였다. 이 축은 닫혔다.

**② 0인 셋은 결함이 아니다.** `0004_scout`·`0006_scout`는 `copy_name`·`name_length`가
대상이고 픽스처의 호출 간선이 `recv_request → parse_header`와 `parse_header → memcpy`
둘뿐이라 그 두 심볼에는 실을 간선이 없다. `0005_auditor`도 같은 이유다. **"그래프가
없다"와 "간선이 0건"을 캡슐이 가르는 것**(`index_graph` 모듈 docstring)이 여기서
프롬프트 층까지 이어진다.

**③ `location_ok`가 6건 전부 false다.** 후보 위치가 심볼로 정밀해졌는데도 그렇다 —
PR #25 §5의 정정(원인은 후보 위치가 아니라 `Evidence.location`이 비어 있는 것)이
실측으로 확인됐다. **판정 기준 6번이 요구한 것이 정확히 이 값이다**(D23).

**④ 여기서 나온 E3 4건은 실험 2와 갈린다.** zlib에서는 E3가 0건이다. 사유는 §3.4에 있다.

## 4. 실험 2 — zlib 규모에서 완주하는가 (`run_20260823_0002`)

### 목적

**경로 성립 판정이다.** 직전 run이 죽은 자리를 지나가는지, 후보가 수백 개가 됐을 때
(a′) 정렬과 `candidate_max` 절단이 설계대로 도는지를 본다.

### 조건

실험 1과 같은 프로필·서버. 대상만 zlib.

### 결과

| 항목 | 결과 |
| --- | --- |
| 소요 | 18 m 54.216 s · **완주** (`stopped_after` None · `reason` None) |
| 후보 / Finding | 256건 / 47건 |
| `target_plan` | analyzable **79** · symbol_targets **542** · fallback **0** · no_symbol **31** · signal **19** · planned **256** · deferred **286** · candidate_max 256 |
| `target_skipped` | **286건 — 전부 `over_candidate_max`** (gateway_error 0 · schema 0) |
| `model_call` | 311건 · **`ok=False` 0건** |
| 최대 단일 프롬프트 | **9,707 / 32,768 tok** |
| 실서빙 콜 | 256건 · chars/token **2.702** · 계상배율 **1.480x** |
| 장부 | 실서빙 input **514,960 tok** · `cached_tokens` **0 (0.0%)** |
| 도구 | `tool_call` 238건 · 합계 112,578 ms |
| `gate_eval` | stage6 47 · stage7 8 · stage8 **0** |
| 등급 | E0 1 · E2 54 · **E3 0** · E4 0 |
| `location_ok` | **55건 전부 false** |

### 근거 로그

cli 경고와 report.md 첫머리 — **절단이 trace 밖에서도 보인다**:

```
[WARN ] target 286건을 candidate_max(256)로 잘랐다. trace의 target_skipped가 목록이다
```

```
중복 제거: 43건
분석하지 못한 대상: 예산 초과 286건 · 호출 실패 0건
```

trace 이벤트 12종:

```
stage_start 743 · stage_end 743 · transition 615 · budget 549 ·
policy_decision 311 · model_call 311 · cache 303 · target_skipped 286 ·
tool_call 238 · gate_eval 55 · target_plan 1 · graph_index 1
```

정책 판정:

```
stage4  below_scout_threshold 209 / scout_promoted 47
stage6  promoted_to_critic 46 / insufficient_evidence 1
stage7  repro_spec_missing 8
Finding state  hypothesized 46 · queued 1
repro_spec 있음 14 / 47
confidence 상위 8건: 전부 conf=0.6, 전부 repro_spec 없음
```

입력 토큰 상위 5:

```
9707 · 9650 · 9211 · 8035 · 6523
```

### 해석

#### ① 회계가 서로 맞는다 — 수가 우연히 그럴듯한 것이 아니다

이 run의 수들은 **독립적으로 교차 검증된다.** 아래는 원자료에서 유도한 것이다.

| 항등식 | 값 |
| --- | --- |
| `planned` + `deferred` = `symbol_targets` | 256 + 286 = **542** |
| `target_skipped` = `deferred` | 286 = **286** |
| scout + auditor + critic = `model_call` | 256 + 47 + 8 = **311** |
| 실서빙 + Fake = `model_call` | 256 + 55 = **311** |
| light + expanded 캡슐 = `cache` | 256 + 47 = **303** |
| artifact×도구 + index = `tool_call` | 79×3 + 1 = **238** |
| below + promoted = 후보 | 209 + 47 = **256** |
| stage6 + stage7 = `gate_eval` | 47 + 8 = **55** |

여덟 개가 전부 맞는다. **`target_plan`이 낸 분모(542)와 실제 실행이 어긋나지 않는다** —
W2가 도입한 회계가 자기 자신과 일치한다는 뜻이고, 이것이 이 run에서 가장 강한 관측이다.

#### ② 직전 run이 죽은 자리를 지나갔다

| | 2026-08-21 zlib | 이번 (`run_20260823_0002`) |
| --- | --- | --- |
| 결과 | `cand_0033`에서 HTTP 400 | **완주** |
| 소요 | 2 m 46 s (죽을 때까지) | 18 m 54 s |
| 산출물 | **없음** — `run_manifest.json`이 안 남았다 | 전량 |
| 원인 | `contrib/minizip/unzip.c` **67,662 B**가 통째로 프롬프트에 | 최대 프롬프트 **9,707 tok** |

**두 소요 시간을 성능으로 비교하지 않는다.** 하나는 죽은 run이고 후보 33건까지만 갔다.
비교 가능한 것은 "완주했는가" 하나뿐이다.

여유가 **23,061 tok**이라는 것은 컨텍스트 상한이 이제 병목이 아니라는 뜻이고, 그래서
4-1·4-2가 이번 run에서 안 탔다(§5).

#### ③ (a′)가 설계대로 돌았다 — 신호 있는 파일은 하나도 안 잘렸다

정적 신호를 실제로 낸 artifact가 **19건**(`Evidence kind=static, ok=True, exit_code!=0`)이고,
`planned` 22파일 중 **19파일이 그 신호를 보유한다.** 남은 13자리를 신호 없는 3파일이
경로 순으로 채웠다:

```
adler32.c 5 · compress.c 6 · contrib/blast/blast-test.c 2
```

**"잘렸는데 신호 있는 파일" 0건.** 신호를 게이트가 아니라 순서로 쓴 결정((a′))이
실저장소에서 의도대로 동작한다.

**⚠ 두 수를 섞지 마라.** `Evidence(kind=static, ok=True)`만 세면 **79건**이 나온다.
`ok`는 "도구가 정상 종료했다"이지 "찾았다"가 아니다 — 정적분석기는 아무것도 못 찾아도
정상 종료한다. 발견 여부는 `exit_code`로 가른다(`signals.with_signal()`). **D11이
지적해 온 구별이 실측에서 79 vs 19로 드러났다.** 이 run의 (a′) 판정은 19를 쓴 것이다.

#### ④ 8단계가 0건인 사슬 — 선별 키와 통과 조건이 서로를 모른다

이것이 이 run이 새로 연 가장 큰 항목이다(**D26**).

```
6단계   promoted_to_critic 46건
7단계   critic_top_n = 8  →  gate_eval 8건
        38건은 승격된 채로 아무 기록도 안 남는다
        8건 전부 repro_spec_missing  →  VALIDATED 0건
8단계   gate_eval 0건
```

**코드에서 확인한 것 셋**:

| 자리 | 무엇 |
| --- | --- |
| `critic.select()` | `sorted(findings, key=lambda f: (-f.confidence, f.finding_id))[:critic_top_n]` |
| `runner._analyze()` | 8단계는 `state.in_state(FindingState.VALIDATED)`만 돈다 |
| `validate.run()` | `if spec is None: raise` — `repro_spec`이 통과 조건이다 |

**선별은 `confidence`로 하고 통과는 `repro_spec`으로 가른다. 두 기준이 서로를 모른다.**
실측이 그 결과다 — 상위 8건이 전부 `conf=0.6`·`repro_spec` 없음이고, `repro_spec`을 가진
14건은 선별에서 밀렸다.

**Fake 의존성을 가른다.** "상위 8건이 하필 전부 `repro_spec` 없음"이라는 **상관**은
Fake auditor의 성질일 수 있다 — 이 run의 auditor는 Fake다. 그러나 **"선별 키와 통과
조건이 독립"은 코드의 성질이라 Fake와 무관하다.** 실서빙 auditor에서는 상관이 달라질 수
있어도 구조는 그대로다.

**같은 사슬의 두 번째 마디**: 7단계가 38건을 자르면서 **아무 기록도 남기지 않는다.**
`critic.select()`는 슬라이스일 뿐 이벤트가 없다. 이 PR이 target에 대해 방금 없앤 것과
(`target_skipped` 286건) 정확히 같은 종류의 조용한 절단이 7단계에 남아 있다.
**`critic_top_n`이 실제로 걸린 것이 이번이 처음이라 이제야 보인다.**

**E3가 0건인 것도 같은 사슬이다.** `p3 = upheld and repro_spec is not None`이므로 8건
전부 p3이 거짓이고, 그래서 등급이 E2에서 멈춘다. 실험 1에서 E3 4건이 나온 것과 갈리는
이유가 여기 있다 — 픽스처는 후보가 4건이라 `critic_top_n`에 걸리지 않았다.

#### ⑤ "중복 제거 43건"을 제거율로 읽지 않는다

출처는 `nodes/report.dedupe()`다. `(finding.cwe, _sink(finding))`로 묶어 `finding_id` 순
첫 건만 남기고, `_sink`는 `finding.path[-1].symbol`(경로가 없으면 `title`)이다.

읽지 않는 이유 셋:

1. **auditor가 Fake다.** 뭉치는 키를 만든 `cwe`와 `path`가 Fake의 생성 규칙에서 나온다.
   47건이 4개 sink로 접힌 것은 **Fake가 sink를 몇 종류만 만든다**는 뜻일 수 있고, 이
   run은 그 구별을 못 한다.
2. **dedupe는 상태를 바꾸지 않는다.** `report.run()`이 "상태는 바꾸지 않는다"고 못박고
   있고, evaluation.md가 요구하는 단계별 제거율은 `policy_decision` 전이에서 나온다.
   9단계 렌더링과 상태 축은 다른 것이다.
3. **trace에 없다.** `dedupe` 이벤트가 없어 `Report.duplicates` 필드와 report.md 첫 줄에만
   있다. 지표로 쓰려면 텍스트를 파싱해야 한다.

**지금은 항목을 열지 않는다.** 이 값이 아직 어떤 지표의 입력도 아니기 때문이고, 필요해질
때 이벤트를 만드는 것이 맞다. 대신 "제거율로 읽지 않는다"를 STATUS.md §10에 박제했다.

#### ⑥ 토큰 계상 배율이 두 번째 점을 얻었다 (D21)

| run | 대상 | chars/token | 계상배율 |
| --- | --- | :-: | :-: |
| `run_20260821_0007` | 픽스처 | 2.224 | 1.798x |
| `run_20260823_0002` | zlib | **2.702** | **1.480x** |

**D21의 주장("2.224를 상수로 쓰면 안 된다 — 구성 의존값이다")이 확인됐다.** 그 항목은
한국어 프롬프트 템플릿이 배율을 지배하고 대상 코드가 커지면 ASCII 비중이 올라 4 쪽으로
수렴한다고 적었는데, zlib에서 실제로 2.224 → 2.702로 움직였다. **여전히 4는 아니다** —
프롬프트의 한국어 부분이 사라지지는 않는다.

#### ⑦ prefix cache — 장부 0은 확정, 서버 쪽은 미기재

`cached_tokens` 0 / 514,960 (0.0%). `--enable-prefix-caching`을 주고 띄웠다.

**서버가 실제로 몇 %를 재사용했는지는 이 run으로 정해지지 않는다.** vLLM을 포그라운드로
띄워 stdout에 찍었고 그 창을 닫았다. R6이 근거로 삼는 "서버는 재사용하는데 응답이 안
싣는다"의 대조값(`run_20260821_0004`의 19.9%)에 해당하는 값이 이번에는 없다.
**퍼센트를 추정으로 적지 않는다.**

## 5. 이 run이 닫은 것과 연 것

**갈라서 적는다.** 완주 하나로 전부 닫혔다고 읽히면 다음 사람이 안 열린 것을 안 본다.

### 닫힌 것

| 축 | 근거 |
| --- | --- |
| 심볼 단위 배선이 실저장소에서 성립하는가 | artifact 79 → 심볼 542, fallback 0 |
| 큰 파일이 통째로 실리는가 | 최대 9,707 / 32,768 tok (직전 run은 67,662 B 파일 한 개로 죽었다) |
| (a′)가 신호를 순서로 쓰는가 | 신호 19파일 전부 `planned`, 잘린 것 중 신호 보유 0건 |
| 절단이 기록에 남는가 | `target_skipped` 286 + cli 경고 + report.md 줄 |
| Scout가 그래프를 받는가 (D20 ③) | scout 프롬프트에 그래프 줄 6·2 |
| 실서빙 모델이 그 그래프를 처리하는가 (D19 ③) | 위 프롬프트를 받은 scout이 실서빙이고 256콜 전부 `ok=True` |
| `location_ok`가 W2로 열리지 않는다는 정정 | 55건 전부 false |
| W2 회계가 자기 자신과 맞는가 | §4 해석 ①의 항등식 8개 |

### 열린 것 / 안 닫힌 것

| 축 | 왜 |
| --- | --- |
| **4-1 실측** | 400이 한 건도 안 났다. `error_detail`의 실제 문자열을 아직 못 봤다 |
| **4-2 실측** | 후보 단위 실패 0건. 폐기 경로를 안 탔다 |
| **D26 (신설)** | 7단계 선별이 조용히 자르고, 그 키가 8단계 진입 조건과 무관하다 |
| **deferred 286건의 결과** | 542 전량을 돌린 적이 없다. `candidate_max`의 근거는 아직 없다(D24) |
| **prefix cache 서버 측** | stdout 유실(R6) |
| **실서빙 auditor·critic** | 이 프로필은 scout만 실서빙이다. Finding 품질·오탐 제거율은 이 run으로 말할 수 없다 |
| **D2 임계값** | 승격 18.4%(47/256)라는 이분값만 있고 개별 점수 분포가 원자료에 없다 |

## 6. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| `single_gpu_12g`로 도구가 안 돈다 | 프로필 선언이 `tool_runner: fake` | `--tool-runner docker` 오버라이드. **세 번째 사례다** — [README.md](../README.md) 미확정 #4 |
| prefix cache 서버 측 값 유실 | vLLM을 포그라운드로 띄우고 창을 닫았다 | 다음 기동부터 `tee`로 파일에 받는다 — [README.md](../README.md)에 규약을 넣었다 |

## 7. 미측정 항목과 사유

**못 잰 값을 0으로 채우지 않는다.**

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| prefix cache hit rate (서버 측) | 서빙 stdout을 보존하지 않았다 | 기동을 `tee`로 파일에 받고 종료 후 `Prefix cache hit rate` 줄을 읽는다 |
| 4-1 `error_detail` 실제 문자열 | 400이 안 났다 | `--max-model-len`을 낮추거나 캡슐 예산을 올려 한 후보를 일부러 넘긴다 |
| 4-2 폐기 후 생존 | 후보 단위 실패 0건 | 위와 같은 조건. 그때 `target_skipped(reason=gateway_error)`와 매니페스트 잔존을 함께 본다 |
| deferred 286건의 Finding | `candidate_max`가 잘랐다 | 상한을 542 이상으로 올려 1회 더 돌린다. **그 run이 D24의 입력이다** |
| 후보 1건당 GPU 시간 | run 합계만 있다 | `model_call`의 `gpu_ms`를 후보별로 집계한다 (이번 원자료에 없다) |
| 실서빙 auditor·critic | 프로필이 Fake다 | `local_balanced`·`b1_single` |
| 서빙 모델 revision | 기동 로그 미보존 | `/v1/models` 또는 기동 로그 |
| 실험 종료 시각 (분 단위) | 원자료에 없다 | run 디렉터리 mtime 또는 `RunRecord.finished_at` |
| `tool_images` digest | 원자료에 없다 | `run_manifest.json`의 `tool_images` |
| Scout 점수 분포 | 승격/미승격 이분값만 있다 | `Candidate.score`를 `state.db`에서 뽑는다 |

## 8. 반증 조건

**이 실험이 실패했다면 무엇이 틀린 것이었나.**

- **심볼 단위 배선이 실저장소 규모에서 성립하지 않는다는 뜻이었다.** 완주했으므로 그
  축은 닫혔다.
- **`target_plan`의 분모가 실제 실행과 어긋났다면** W2가 도입한 회계 자체를 못 믿는다.
  항등식 8개가 전부 맞았으므로 그 축도 닫혔다.
- **신호 있는 파일이 하나라도 잘렸다면** (a′)의 정렬 키가 틀린 것이었다. 0건이다.

**성공이 배제하지 못한 것**: 이 run은 실패 경로를 태우지 않았다. 4-1·4-2가 실대상에서
동작한다는 근거는 **이 run에 없다** — 있는 것은 "그 경로가 필요할 만큼 큰 프롬프트가
안 만들어졌다"는 사실뿐이고, 그것은 W2가 의도한 결과이지 장비 둘의 검증이 아니다.

## 9. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 높음 | **D26** — 7단계 선별의 조용한 절단 + 선별 키와 8단계 진입 조건의 독립 | 신설. 선택지 (a)(b)의 성격이 갈린다 — 항목 참조 |
| 높음 | `candidate_max`를 542 이상으로 올린 zlib run 1회 | D24의 유일한 입력 |
| 중간 | 4-1·4-2 실패 경로를 일부러 태우는 run | 컨텍스트를 넘기는 조건을 만들어야 한다 |
| 중간 | 실서빙 auditor 프로필로 1회 | Finding 품질·D26의 상관이 Fake의 성질인지 판정 |
| 낮음 | `Evidence.location` 채우기 | D23. `location_ok`를 여는 유일한 길 |

## 10. 정리 상태

- 서빙 서버: **미기재** — 종료 여부가 원자료에 없다
- 임시 설정·프로필: `--tool-runner docker`는 CLI 오버라이드라 파일 변경 없음
- 저장소 변경: 없음 (이 보고서와 문서 갱신은 PR #25에 포함)

## 11. 부록 — 재현 절차

**원자료에 기동 명령 원문이 없다.** 아래는 원자료가 명시한 인자만 적은 것이고 **완전한
재현 명령이 아니다** — 환경변수([portability.md](../../portability.md) §5의 세 조건)와
모델 경로가 빠져 있다.

```bash
# 서버 (원자료가 명시한 인자만)
--gpu-memory-utilization 0.2 --max-model-len 32768 --enable-prefix-caching

# 하네스
--profile single_gpu_12g --device atom --tool-runner docker \
  --artifacts-root /srv/tenants/user/artifacts
```
