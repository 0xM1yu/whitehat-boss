# run A — 하네스 검증: D44·D45 성립과 `output_max 8192`의 피해 — ATOM

> **이 보고서는 하네스 검증 run이다. [evaluation.md](../../evaluation.md) §2.3의 조건 비교표에
> 넣지 않는다.**
>
> 판정하는 것은 D44·D45가 코드에서 실제로 작동하는가와 `output_max 8192`가 무엇을 했는가이지,
> 조건 간 성능이 아니다. 비교표에 못 들어가는 이유는 셋이다 — ① `model_checksums`의
> `embedding`·`reranker`가 자리표시자(전부 0)라 어느 모델로 돈 run인지 기록이 지시하지 못한다
> ② `kb_snapshot`이 `kb-none`이다 ③ 애초에 `stopped_after: 7`인 부분 결과다.
>
> **성능 수치를 이 보고서에서 인용하지 않는다.** 생성 속도 63 tok/s는 실패 시간을 계산하기
> 위한 값이지 처리량 측정이 아니다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-08-28 21:53 KST` (`12:53 UTC`) |
| 실제 서빙 모델 | `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` · 별칭 `qwen3-coder-30b-a3b` |
| `repo_commit` | `44392a6fd72c7933e9b42858a233ef1a82fe9401` (dirty: `false`) |
| `run_id` | `run_20260828_0002` |
| run 산출물 경로·보존 여부 | ATOM `/srv/tenants/user/artifacts/run_20260828_0002` · **보존**. 사본 `~/runA-20260828.tgz`(무효 run `run_20260828_0001` 동봉) |
| `tool_runner` | 프로필 선언 `docker` / 실제 `docker` — **오버라이드가 아니었다.** `--tool-runner docker`를 명시했을 뿐인데 CLI가 값을 비교하지 않고 경고를 낸다(아래 §7 D50) |
| `--device` | `atom` |
| `tool_images` digest | `harness-tools:v1` = `sha256:b5d7048bc0a5dac3b4307ca3c9d7b204d9617b462d3389000bf5335b56f4ae17` (docker = 이미지 config sha256) |
| `--gpu-memory-utilization` | `0.28` · 분모 물리 통합메모리 **119.7 GiB** |
| 동시에 떠 있던 다른 서버 | **미기재.** 확인한 것은 기동 전 `curl 127.0.0.1:8002/v1/models`가 "서버 내려감"이었다는 것뿐이고, 다른 포트(8001·8003·8007)는 조회하지 않았다. 계정을 공유하므로 이 빈칸이 "왜 이번엔 throttle이 안 걸렸나"를 사후에 못 가르게 한다 |
| 원문 산출물 보존 위치 | CLI stdout `~/runA1-pwn003-retry.log` · run 디렉터리 전체 |
| 서빙 서버 stdout | `run_20260828_0002/serve_30b_runA_20260828_213343.log` · **보존** (`setsid nohup … > "$LOG" 2>&1 &`) |

`model_checksums`의 `scout`·`auditor`·`critic`은 셋 다
`sha256:69c4c69eb5fe52d59f2c4fe015986d76495f2f2674aabf7c087e0f6307e00e44`로 같다 — B1 조건이
전 역할을 같은 단일 모델로 돌린다는 프로필 정의대로다.

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| 대상 | `track_a_evaluation_release_v1/blind/PWN-003/source` |
| 프로필 | `b1_single` · 서버 A(`disable_any_whitespace` **없음**) |
| 소요 | 14 m 29 s |
| 결과 | 후보 19 / Finding 3 / `stopped_after: 7` (부분 결과) |
| **D45** | **성립.** `target_planned` 19건에 심볼 이름이 남았고 gold 세 함수가 전부 후보에 있었다 |
| **D44** | **성립.** `critic_failed` 발동 후 run이 계속 갔다. 다만 같은 단계의 **다른** 실패가 run을 죽였다 |
| **`output_max 8192`** | **피해 셋.** 폭주를 못 막았고, gold 함수 하나의 분석을 막았고, 실패를 빠르게 만들지도 못했다 |
| 신규 결함 | 4건 (D47~D50) |

**정답 대조는 이 run의 목적이 아니고, 이 run으로 합격하지도 않는다.** 정답지의 gold 위치 넷 중
함수 셋이 전부 후보에 있었고 그중 둘에 Finding이 났지만, **인과 근원(`router_define`)은 400으로
떨어졌고 낸 둘은 정답지가 부수 효과로 분류한 자리이며 CWE도 안 맞는다.** 자세한 대조는
§5 해석 ④에 있다. 좁혀진 것은 원인이다 — "어디를 봤는지조차 몰랐다"에서 "gold 위치까지는
갔고 결함의 성격을 잘못 짚었다"로 바뀌었다.

## 2. 실험 환경

기동:

```bash
setsid nohup env VLLM_USE_DEEP_GEMM=0 VLLM_HOST_IP=127.0.0.1 \
  NCCL_SOCKET_IFNAME=lo GLOO_SOCKET_IFNAME=lo \
  "$(which vllm)" serve "$SNAP" \
    --served-model-name qwen3-coder-30b-a3b \
    --host 127.0.0.1 --port 8002 \
    --gpu-memory-utilization 0.28 \
    --max-model-len 32768 \
    --enable-prefix-caching \
  > "$SERVE_LOG" 2>&1 &
```

**`disable_any_whitespace`를 켜지 않았다.** D46이 그것을 "서버측 우회이지 하네스 계약이
아니다"로 판정했으므로, 이 run은 그 우회 없이 하네스가 무엇을 하는지를 본다.

실행:

```bash
python3 -m src.cli run --profile b1_single --device atom --tool-runner docker \
  --condition "B1prime@pipeline" --artifacts-root /srv/tenants/user/artifacts \
  --target "$TGT"
```

`thresholds`(매니페스트 기록값): `output_max: 8192` · `candidate_max: 256` ·
`critic_top_n: 8` · `schema_retries: 1` · `capsule_budget_tokens.expanded: 18000`.

## 3. 실험 1 — D45: 실행한 대상의 심볼이 기록에 남는가

### 목적

경로 성립 판정. PR #44가 넣은 `target_planned`가 정답 대조에 쓸 수 있는 기록을 내는가.

### 결과 — 성립

`target_planned` 19건 전부에 `file`·`symbol`·`line_start`가 실렸다.

```
2 channel_store.c lease_open 7            2 channel_store.c router_define 45
2 channel_store.c scalar_from_record 20   2 channel_store.c router_store 79
2 channel_store.c router_create 30        2 channel_store.c router_emit 106
2 channel_store.c router_destroy 34       2 channel_store.c router_forward 138
2 record_reader.c load_u16 8              2 router_main.c digest_bytes 12
2 record_reader.c load_u32 12             2 router_main.c consume_define 22
2 record_reader.c record_stream_next 19   2 router_main.c consume_store 29
2 router_main.c consume_emit 36           2 router_main.c consume_forward 53
2 router_main.c router_consume 59         2 router_main.c write_audit_event 93
2 router_main.c main 111
```

**gold 세 함수가 전부 있다** — `router_define`(45) · `router_store`(79) · `router_emit`(106).
`count(target_planned) = 19 = target_plan.planned`로 분모도 맞는다.

### 해석 ①

D45가 열릴 때의 질문("근본 원인 `router_define`이 후보 19건에 있었는가")에 **run 산출물만으로
답할 수 있게 됐다.** 이전에는 프롬프트 본문을 사람이 읽어야 했다.

그리고 그 답이 **"있었다"**이다. **정답을 놓친 원인은 대상 선정이 아니다.** D20(폴백)도
아니다 — 19건 전부 `symbol`이 비어 있지 않으므로 파일 단위 폴백이 한 번도 안 걸렸다.

## 4. 실험 2 — D44: critic의 파싱 실패가 run을 죽이는가

### 목적

경로 성립 판정. PR #41이 넣은 `critic_failed`가 발동하고 run이 계속 가는가.

### 결과 — 성립. 그러나 같은 단계의 다른 실패가 run을 죽였다

7단계 critic 호출 2건이 **둘 다** 실패했는데 사유가 다르다.

```
seq 317  critic  find_0005  wall_ms 288959  SchemaParseError      → critic_failed로 격리, run 계속
seq 321  critic  find_0002  wall_ms 124510  ModelGatewayError 400 → run 중단
```

앞의 것:

```json
{"seq":319,"kind":"critic_failed","stage":7,"subject_id":"find_0005",
 "data":{"reason":"schema_parse_failed","error_detail":"critic 역할의 qwen3-coder-30b-a3b이
 2회 안에 Decision을 만들지 못했다: 1 validation error for Decision Invalid JSON:
 EOF while parsing an object at line 16139 column 0 [type=json_invalid, input_value=…"}}
```

D44가 의도한 대로 Finding 하나만 버리고 계속 갔다. **D46이 말한 공백 폭주가 그대로 재현됐다** —
16,139행에서 객체가 안 닫혔다.

뒤의 것은 격리되지 않았다:

```
[WARN ] 게이트웨이가 응답하지 않아 중단했다: {"error":{"message":"This model's maximum context
length is 32768 tokens. However, you requested 8192 output tokens and your prompt contains at
least 24577 input tokens, for a total of at least 32769 tokens. … code":400}}
```

`stopped_after: 7`. **PR #47이 이 비대칭을 없앤다** — `_triage_all`은 이미 400을 후보 단위로
떨구는 판정을 갖고 있었는데 `_analyze`의 critic 루프에만 없었다.

### 해석 ②

D44는 성립했지만 **7단계에서 판정을 받은 Finding은 0건이다.** `critic_top_n: 8`에 2건이
올라갔고 둘 다 실패했다. D46이 예고한 상태 — "critic이 전량 실패한 run도 `is_complete`가
참이 될 수 있다" — 가 실제로는 한 걸음 못 미쳐서 나타났다(400이 중단시켜 `is_complete`가
거짓이 됐다). #47 이후에는 D46이 예고한 그 모양이 그대로 나온다.

## 5. 실험 3 — `output_max 8192`가 만든 피해 셋

### 목적

D42가 (a)로 닫히며 들어간 값 8192가 무엇을 했는지 판정.

### 피해 ① 폭주를 못 막았다

`critic` seq 317이 **8192를 두 번 다 생성하고** 스키마 실패했다(`wall_ms 288959`). 상한이
폭주 차단 장치인데 상한까지 폭주했다. 16,139행이 그 증거다.

### 피해 ② 입력 여유를 깎아 gold 함수 하나의 분석을 막았다

`--max-model-len 32768`에서 `output_max 8192`를 빼면 입력 상한이 **24,576**이다. 그 상한에
걸려 4단계 target 3건이 400으로 탈락했다.

```
seq  78  target_skipped  channel_store.c  lease_open           gateway_error
seq 139  target_skipped  channel_store.c  router_define        gateway_error   ← gold
seq 235  target_skipped  record_reader.c  record_stream_next   gateway_error
```

**`router_define`은 PWN-003의 gold 함수다.** 후보에는 있었고(§3), 분석은 못 했다.

> **에러 메시지의 `24577`은 프롬프트 크기가 아니라 상한+1이다.** vLLM이
> `input_tokens, value=24577`로 보고하는 것은 "요청이 24,577 이상"이라는 하한 표현이고,
> 실제 프롬프트가 정확히 24,577 토큰이었다는 뜻이 아니다.

### 피해 ③ 실패를 빠르게 만들지도 못했다

서빙 로그의 생성 속도가 **63 tok/s**다.

```
Avg generation throughput: 64.0 tokens/s … 63.5 … 63.0 … 60.2   (Prefix cache hit rate: 25.3%)
```

`8192 ÷ 63 ≈ 130초`. 실패한 호출 5건의 `wall_ms`가 그 값에 맞는다.

| seq | 역할 | wall_ms | 해석 |
| --- | --- | --- | --- |
| 75 | auditor | 125,346 | 1차가 8192를 다 생성 → 스키마 실패 → 재시도가 400 |
| 136 | auditor | 126,799 | 같음 |
| 232 | auditor | 126,942 | 같음 |
| 317 | critic | 288,959 | **8192 × 2회** (시도당 약 144초) |
| 321 | critic | 124,510 | 1차 8192 → 재시도가 400 |
| | | **792,556** | **= run 869초의 91.2%** |

**이 표가 run 시간의 지배 항이다.** 성공한 호출 23건은 전부 합쳐 69.8초다 — 실제 분석에 쓴
시간보다 폭주에 쓴 시간이 **11배**다. 그 항이 상한에 정비례하므로 상한을 절반으로 하면
이 표도 절반이 된다.

> **⚠ 정정 (2026-08-28, 초판 오류).** 이 절의 초판은 seq 317의 288,959 ms가
> `_TIMEOUT_S = 300`에 "12초 못 미쳤다"고 적었다. **범주 오류다.** `_TIMEOUT_S`는
> `openai_compat._post`의 `urlopen` **호출 하나당** 적용되고 `model_call.wall_ms`는
> `context.py`가 `complete()` 전체(재시도 포함)를 감싼 값이다. 288,959 ms는 2회 합계라
> 시도 하나는 약 144초였고 여유는 12초가 아니라 **약 155초**였다.
>
> 바로잡은 계산: 시도 **하나**가 300초를 넘으려면 생성 속도가 8,192에서 **27.3 tok/s**,
> 4,096에서 **13.7 tok/s** 아래로 떨어져야 한다(관측 63 tok/s 대비 여유 2.3배 → 4.6배).
> D42를 연 `run_20260827_0001`의 300,005 ms는 상한이 **아예 없어** 한 시도가
> `--max-model-len`까지 간 경우이고, 8,192가 실린 뒤로는 출력 길이만으로 그 벽에 닿지 않는다.
> **상한을 내리는 근거는 타임아웃이 아니라 위의 91.2%다.**

### 해석 ③ — 400의 원인은 "정상 프롬프트가 컸다"가 아니다

**정상 프롬프트는 24,576에 한참 못 미친다.** 성공한 호출 23건의 `input_tokens`가 전부
1,123 ~ 1,798이고 `capsule_budget_tokens.expanded`가 18,000이라 그 예산조차 안 묶였다.

400은 **재시도 요청에서만** 났다. 기제는 `adapters/openai_compat.py`의 재시도 조립이다.

```python
body = f"{prompt.body}\n\n[스키마 오류] {exc.errors(include_url=False)}"
```

pydantic v2의 `errors()`는 `include_input=True`가 기본값이고, `json_invalid` 오류의 `input`에는
**파싱에 실패한 응답 원문 전체**가 들어간다. 재현:

```
원문 15,000자(공백 폭주)  → 붙는 문자열 25,223자
include_input=False       → 191자
```

즉 8192 토큰짜리 공백 폭주가 escape되어 재시도 프롬프트에 통째로 실렸고, 그것이
`input_tokens ≥ 24,577`의 정체다. **그 두 줄 위 주석은 정반대를 적고 있다.**

> 재요청에는 스키마 오류 메시지만 덧붙인다(agent-contracts.md §1).
> 이전 응답 원문을 다시 넣으면 같은 오류를 반복하게 만든다.

**주석의 판단이 옳았고 코드가 그 판단을 실행하지 못하고 있었다.** 이것을 D47로 연다.

### 해석 ④ — 8192는 gold 위치 둘의 **판정**을 막았다. 정답 대조는 그것과 별개로 불합격이다

> **⚠ 정정 (2026-08-28, 초판 과장).** 이 절의 초판은 "8192가 gold 세 함수 전부의 판정을
> 막았다"·"판정을 못 받은 이유가 전부 출력 상한이다"라고 적었다. **정답지를 읽지 않고 쓴
> 문장이다.** `evaluator/PWN-003/ground_truth.yaml`을 대조한 결과는 아래와 같고, 출력 상한은
> 정답 대조 불합격의 **일부 원인일 뿐**이다.

정답지(`ground_truth.yaml`)가 말하는 것:

| 항목 | 값 |
| --- | --- |
| `finding_count` | **1건** (`PWN-003-F01`) — 함수 셋이 각각 정답인 것이 아니다 |
| 위치 | **4곳** — `router_define`(45–77) · `router_store`(79–104) · `router_emit`(106–136) · `record_format.h`의 레이아웃(36–58) |
| 주 CWE | **CWE-843** (type confusion). 보조 20 · 787 · 125 |
| `ordering` | `CAUSAL_ROOT_THEN_SUBOBJECT_EFFECTS` — **인과 근원과 부수 효과를 명시적으로 가른다** |
| 인과 근원 | `router_define`의 commit-before-validation (거부된 tag가 먼저 커밋된다) |
| `router_store`·`router_emit`의 역할 | `broader consumer and write/read sink` = **부수 효과** |

하네스가 낸 것과 나란히 두면:

| Finding | 위치 | CWE | 정답 대비 |
| --- | --- | --- | --- |
| `find_0002` `router_destroy` | gold 아님 | 415 | **오탐** · critic 400으로 run 중단 |
| `find_0004` `router_store` | gold 효과 지점 | 121 | 위치 O · CWE 불일치(787 아님) · 7단계 못 올라감 |
| `find_0005` `router_emit` | gold 효과 지점 | 121 | 위치 O · CWE 불일치(125 아님) · `critic_failed` |
| — `router_define` | **gold 인과 근원** | — | 4단계에서 400, 분석 자체가 안 됨 |

```
등급 분포: E2 1건 · E1 2건
분석하지 못한 대상: 예산 초과 0건 · 호출 실패 3건
- **[E1] Buffer overflow in router_store due to unchecked memcpy length** (CWE-121, confidence 0.80)
- **[E1] Potential buffer overflow due to unchecked memory copy length in router_emit** (CWE-121, confidence 0.90)
```

**정확한 진술은 이렇다.**

- 하네스는 gold 위치 넷 중 **셋(함수 셋)을 후보로 봤고**, 그중 **둘에 Finding을 냈다**
- 낸 둘은 정답지가 **부수 효과**로 분류한 자리이고, CWE도 정답 집합(843·20·787·125)에 없는
  **121**이다
- **인과 근원 `router_define`은 못 잡았다** — 그것이 400으로 떨어진 대상이다
- 따라서 **출력 상한을 고쳐도 이 run이 곧 정답 대조 합격이 되지는 않는다.** 상한이 막은 것은
  ① 인과 근원의 분석 기회와 ② 효과 지점 둘의 7단계 판정이지, 그 판정이 났다고 CWE와 인과
  서술이 정답과 맞아지는 것은 아니다

그래도 남는 소득은 있다. 지금까지의 "정답 대조 불합격"은 **하네스가 어디를 봤는지조차
몰라서** 원인을 못 갈랐는데(D45), 이제 "gold 위치까지는 갔고 결함의 **성격**을 잘못 짚었다"로
좁혀졌다. 다음에 볼 것은 대상 선정이 아니라 **auditor가 왜 type confusion을 buffer overflow로
읽었는가**다.

> **출처와 주의.** 위 대조는 ATOM의
> `experiments/2x2/dataset/track_a_evaluation_release_v1/evaluator/PWN-003/ground_truth.yaml`을
> 사람이 읽어 만들었다. **정답지는 이 저장소에 들어 있지 않고 하네스도 읽지 않는다** — 대상은
> `blind/PWN-003/source`뿐이다. 이 절이 함수 이름과 CWE를 적는 것은 판정 근거를 남기기
> 위해서이고, `vulnerable_statement`·트리거 시퀀스 같은 정답지 본문은 옮기지 않았다.
> [evaluation.md](../../evaluation.md) §4의 누출 검사는 KB 인덱스와 프롬프트를 보므로 이
> 문서에 걸리지 않지만, **이 문서를 KB에 넣지 않는다**는 것이 그 전제다.

## 6. 실험 4 — `run_20260828_0001` 무효 판정

### 무효 사유

같은 날 09:38의 첫 시도는 **커널 keyring 쿼터 소진**으로 컨테이너가 한 번도 안 떴다.
8주에 걸쳐 `_ses` 키 201개가 쌓여 사용자 `1004`의 `maxkeys` 200을 채웠고, 관리자가 200 → 5000으로
상향해 해소됐다. 상향 후 확인:

```
1004:   376 376/376 376/5000 1902/500000
```

그 run은 2 m 33 s 만에 끝났고 `target_planned`가 4건뿐이다(정상 run은 19건). 인덱서가
죽어 파일 단위로 떨어진 결과다. **비교에도 판정에도 쓰지 않는다.**

### 그런데 이 run을 **끊은** 것은 keyring이 아니다

`stopped_after`가 `run_20260828_0002`와 똑같이 **7**이고, 사유도 같다.

```
stage 4  scout    ok true   wall 3756
stage 5  auditor  ok true   wall 7014
…
stage 7  critic   ok false  wall 124869
  {"error":{"message":"This model's maximum context length is 32768 tokens.
   However, you requested 8192 output t…
```

**D47이 그날 두 run을 끊었다.** keyring은 도구를 죽여 이 run을 무효로 만들었지만, 파이프라인을
멈춘 것은 재시도 프롬프트가 부풀어 받은 400이다 — 도구가 전부 실패한 조건에서도 같은 자리에서
같은 모양으로 났다는 뜻이라, 이 결함이 대상이나 도구 상태에 의존하지 않는다는 것을 보인다.

### 그런데 그 무효 run이 결함 하나를 드러냈다

`tool_call` 13건이 이렇다.

```
tool_call 1 index      False
tool_call 2 semgrep    False     tool_call 2 clang-tidy True    ←
tool_call 2 build      False     tool_call 2 clang-tidy False
tool_call 2 semgrep    False     tool_call 2 clang-tidy True    ←
tool_call 2 build      False     tool_call 2 clang-tidy True    ←
tool_call 2 semgrep    False     tool_call 2 build      False
tool_call 2 semgrep    False
```

`index`·`semgrep`·`build`는 전부 실패인데 **`clang-tidy`만 3건이 `ok: true`로 앉았다.**
같은 런타임이 같은 이유로 죽은 자리에서 한 도구만 성공으로 세어졌다 — **PR #46이 지적한
exit 125 경로**의 실측이다. #46은 이 run이 나오기 전에 열렸고, 이 13줄이 그 PR의 근거가 됐다.

## 7. 남는 것

| 번호 | 무엇 |
| --- | --- |
| **D47** | 스키마 재시도가 실패 응답 원문을 프롬프트에 되싣는다 (§5 해석 ③) |
| **D48** | `target_skipped.stage`가 실제 실패 단계를 안 가리킨다 — 3건 전부 `stage: 4`인데 실패한 `model_call`은 `stage: 5` auditor다 |
| **D49** | 재시도 프롬프트가 산출물에 안 남는다 — `prompts/0026_critic.md`는 2,651 바이트인데 실제로 400을 받은 요청은 24,577 토큰이었다. `prompt_sha256`도 1차 것이다 |
| **D50** | `--tool-runner`를 주면 값이 프로필 선언과 **같아도** "프로필과 다른 도구 런타임으로 돌았다"고 경고한다 (`src/cli.py:558`) |

**D46은 이 run으로 닫히지 않는다.** 공백 폭주가 그대로 재현됐고 서버 우회를 안 켠 조건이라,
D46이 묻는 "구조화 출력이 왜 안 닫히는가"는 여전히 열려 있다. 다만 D46 본문의 "`output_max`를
키우면 더 늦게 잘릴 뿐"에 대칭이 하나 생겼다 — **줄이면 폭주 비용이 준다.**

## 8. 다음 run이 볼 것

`#45`·`#46`·`#47`과 D47·D42 후속이 들어간 뒤 같은 조건으로 한 번 더 돌린다. 그때 판정되는 것:

- 7단계가 실제로 **판정을 내는가** (이 run은 2건 중 0건)
- 8·9단계에 처음 도달하는가 — `stopped_after`가 7이라 재현·dedupe를 못 봤다
- gold 위치의 Finding이 7단계 판정을 받는가 — **그것이 정답 대조 합격은 아니다.** CWE와 인과
  서술이 정답(CWE-843 · commit-before-validation)과 맞는지는 별개 질문이고, 지금 답은 "아니다"다
- `router_define`이 분석되면 auditor가 인과 근원을 짚는가 — 이 run은 그 기회를 못 만들었다

**서버 옵션 `disable_any_whitespace`는 그 run에 섞지 않는다.** 하네스 수정과 서버 우회를 한
run에 넣으면 어느 쪽이 들었는지 못 가른다 — 그것은 별도 대조 run이다.
