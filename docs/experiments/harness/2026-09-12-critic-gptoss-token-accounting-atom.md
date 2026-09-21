# Critic 미발화와 `gpt-oss-20b` 출력 토큰 회계 — ATOM

> **결론 셋.**
> ① **`run_20260912_0001`에서 Critic이 0회 불린 것은 게이트 연쇄다** — Finding 120건이 전부
> `evidence_refs=[]`라 E0으로 단락되고, 정책이 `queued`로 전이시키며, 7단계는 `hypothesized`만
> 고른다. 서버 로그에 `chat/completions` **0건**으로 양쪽에서 확인된다.
> ② **`gpt-oss-20b`의 출력은 추론과 본문으로 갈리고, `max_tokens`가 재는 것은 본문이 아니다.**
> 실제 Critic 프롬프트에서 **본문은 어느 조건에서도 76~126토큰**이고 나머지가 전부 추론이다
> (`reasoning_effort: high`에서 추론 중앙 1,252 · 최대 16,381).
> ③ **그러므로 `output_max_by_role.critic` 값은 안 정한다.** 하네스 실제 설정
> (`max_tokens 4096` · `high` · `Decision`(실서빙 `DecisionDraft`의 상한 — §3.3의 ⚠) · 실
> 프롬프트) **16콜에서 실패 0건**이고 비폭주 최대
> `completion_tokens`가 1,508이다. 전역 4,096이 이미 충분하다.
>
> ⚠ **이 보고서가 새로 답하지 않는 것.** D67의 원인 갈래와 D46의 해법은
> [2026-09-11 D67 실험](2026-09-11-d67-required-schema-atom.md)이 **이미 답했다.** 여기 §3.5는
> 그 실험의 **재현**이다 — 다만 거기서 한 가지가 새로 나왔다: **`disable_any_whitespace`가
> 꺼진 서버에서는 `required` 팔이 0/5라 D67 수정을 적용조차 못 한다.** 판정 정확도는 안 쟀다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | `2026-09-12 23:5x KST` (프로브 종료). run 자체는 `2026-09-12 12:49:46Z` = `21:49 KST` |
| 실제 서빙 모델 | Critic `hf://openai/gpt-oss-20b@6cee5e81ee83917806bbde320786a8fb61efebee` · Auditor `hf://NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` · Scout `hf://nvidia/Qwen3-8B-NVFP4@ccd10a893cbca613259517c3efe08e151ddf2b8e`. `--served-model-name`은 전부 별칭 |
| `repo_commit` | `5b0bec90800632fe52a1206fa5e41cc4c9a92f77` + **dirty** — ATOM 워크트리 `/srv/tenants/user/wire-test`에서 `config/profiles.yaml`의 `critic`만 `gpt-oss-20b`로 바꾼 미커밋 상태. run WARN이 `repo_dirty`로 적었고 **모델 비교표에서 제외된다** |
| `run_id` | `run_20260912_0001` — §3.1·§3.2가 그 run의 산출물을 읽는다. **§3.3·§3.4·§3.5의 프로브 호출에는 `run_id`가 없다**(하네스를 안 지났다) |
| run 산출물 경로·보존 여부 | `/srv/tenants/user/artifacts/run_20260912_0001` — 보존. `prompts/` 403건 · `trace.jsonl` · `state.db` |
| 입력 프롬프트 | §3.3·§3.4는 그 run의 `state.db` Finding에서 **재구성**했다 — 그 run은 critic을 안 불러 `prompts/`에 critic 프롬프트가 **0건**이다. §3.5는 `prompts/0003_auditor.md` **원문 재생** |
| `tool_runner` | `docker` (프로필 선언값 그대로) |
| `--device` | `atom` |
| `tool_images` digest | 이 보고서 범위 밖 — 도구는 run에서만 돌았고 프로브는 안 돌렸다 |
| `--gpu-memory-utilization` | 8001 `0.12` · 8002 `0.28` · 8006 `0.22` — **분모는 물리 통합메모리 119.7 GiB** |
| 동시에 떠 있던 다른 서버 | **셋 다 동시 상주.** 8001 scout · 8002 auditor · 8006 critic. §3.3·§3.4의 초 단위 값은 그 경합 아래 값이다 |
| 원문 산출물 보존 위치 | ATOM `/srv/tenants/user/critic_reasoning_split.json` · `critic_harness_settings.json` · `gptoss_probe_now.jsonl` · `d67_probe_20260912.json` |
| 서빙 서버 stdout | ATOM `/srv/tenants/user/vllm_8001.log` · `vllm_8002.log` · `vllm_8006.log` — 보존. **저장소에는 없다** |
| vLLM · 환경 | `0.27.1` · `sm121` · venv `/srv/tenants/user/venv-probe` |

⚠ **세 서버 전부 `StructuredOutputsConfig(... disable_any_whitespace=False ...)`로 떴다.**
[2026-09-11 실험](2026-09-11-d67-required-schema-atom.md)이 그 옵션을 [D46](../../open-questions/D/D46.md)
해법으로 실측해 둔 뒤인데 기동에 안 걸렸다. §3.2가 그 대가를 센다.

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| run 벽시계 | **78분 55초** (`11:30:51Z` → `12:49:46Z`) |
| `model_call` 합계 | 383회 · **76.4분** — scout 256회/24.9분, auditor 127회/51.5분, **critic 0회** |
| Finding 상태 | `queued` **120/120** |
| Finding의 optional 필드 | `path`·`evidence_refs`·`rca_draft`·`trigger_status`·`missing_evidence` **전부 빔 120/120**. `repro_spec`만 있음 |
| auditor 프롬프트의 evidence 배열 | **127/127 차 있음** (ID 426개) — 배선은 정상 |
| auditor 실패 | **7/127** — 전부 공백 폭주로 잘린 JSON |
| 실제 Critic 프롬프트 크기 | **646~654 토큰** (서버가 센 `prompt_tokens`) |
| Critic 본문 출력 | **76~126 토큰** — `reasoning_effort`와 거의 무관 |
| Critic 추론 출력 | `high` 484~**16,381** · `medium` 69~227 · `low` 12~23 토큰 |
| 하네스 실제 설정 16콜 | **JSON 실패 0 · `finish=length` 0** · `completion_tokens` 615~1,508 |
| D67 팔 재현 (`disable_any_whitespace=False`) | `stock` 4/5 성공·채움 **0/4** · `required` **0/5** · `min_items` **0/5** |

## 2. 실험 환경

ATOM(GB10, sm_121, aarch64)에 vLLM 0.27.1로 세 모델을 동시 상주시켰다. 기동 인자는 §메타의
서버 로그가 정본이고, 셋 다 `--enforce-eager --structured-outputs-config.backend xgrammar`이며
8001·8002만 `--attention-config.backend TRITON_ATTN`을 들었다(8006은 그것 없이 기동했다).

## 3. 실험

### 3.1 Critic이 0회 불린 연쇄

**목적.** run이 78분을 돌고 Critic을 한 번도 안 부른 이유를 코드와 산출물 양쪽에서 고정한다.

**결과.**

| 지점 | 관측 |
| --- | --- |
| `state.db` Finding | `evidence_refs=[]` 120/120 |
| [`gate.py`](../../../src/orchestrator/gate.py)의 `evaluate()` 첫 분기 | `if not finding.evidence_refs:` → `E0` 반환, **술어 미평가** |
| [`policy.py`](../../../src/orchestrator/policy.py)의 `_gate_grade` | `E0` → `Route.QUEUE` · `to_state=QUEUED` |
| `state.db` Finding 상태 | **`queued` 120/120** |
| [`runner.py`](../../../src/orchestrator/runner.py)의 7단계 진입 | `state.in_state(FindingState.HYPOTHESIZED)` → 빈 리스트 |
| `vllm_8006.log` | `POST /v1/chat/completions` **0건** (20:29 기동 이래 `/v1/models` GET 둘뿐) |

**해석 — 문턱은 「E2 이상」이 아니라 「E0이 아닐 것」이다.** 같은 `_gate_grade`에서 **E1은
`Route.HOLD`로 `hypothesized`를 유지하므로 Critic에 간다.** 이번 run이 0인 것은 120건이
**전부** E0이었기 때문이지 E2 미만이어서가 아니다. `run_20260904_0001`은 E1이 2건이었으므로
같은 코드에서 Critic이 2회 불렸을 것이다.

**따라 나오는 것:** Auditor가 한 건만 `evidence_refs`를 채워도 Critic은 발화한다.

### 3.2 Auditor는 required만 채웠고, 실패 7건은 공백 폭주다

**목적.** 채움 실패가 프롬프트 부재인지 배선인지 스키마인지 가르고, 실패 7건의 성격을 본다.

**결과 ① — 배선은 정상이다.** `prompts/*_auditor.md` **127건 전부**의 사실 블록에 evidence
배열이 차 있었다 — ID 총 426개, 종류 `graph`·`static`·`build` 포함, 캡슐 4섹션과 대상 원문
1,823자(표본 0)도 함께 실려 갔다. **인용할 것을 받고도 안 썼다.**

**결과 ② — 채움은 required/optional 경계를 정확히 따른다.**

| 구분 | 필드 | 채움 |
| --- | --- | --- |
| required 8 | `finding_id`·`run_id`·`candidate_id`·`state`·`confidence`·`cwe`·`title`·`produced_by` | **120/120** |
| optional 6 | `path`·`evidence_refs`·`rca_draft`·`trigger_status`·`missing_evidence` | **0/120** |
| optional 6 | `repro_spec` | 120/120 |

**결과 ③ — 실패 7건은 전부 같은 모양이다.** `trace.jsonl`의 실패한 `model_call` 7건:

```
attempts: 2   output_tokens: 8192        ← 4,096 × 2회의 **합계**다
Invalid JSON: EOF while parsing an object at line 1336 column 1
input_value='{\n "finding_id": "find...\t\t\t\t\n \t\t\t\t\n '
```

**꼬리가 탭과 개행뿐이다.** [D46](../../open-questions/D/D46.md) 공백 폭주이고, 두 시도 다
상한까지 차서 잘렸다.

**해석.** [2026-08-29 v2 스모크](2026-08-29-prompt-v2-smoke-atom.md) 해석 ⑧이 표본 1건으로 적은
*"모델은 required만 확실히 채우고 optional은 프롬프트가 시켜야 낸다"*가 실대상 120건에서
재현됐다. ⚠ **`repro_spec`이 optional인데 120/120 나온 것은 그 서술로 설명되지 않는다** —
같은 예외가 v2 스모크에도 있었고(거기서는 `path`·`repro_spec` 둘) 왜인지는 **안 갈랐다.**
⚠ **이것이 D67의 원인을 새로 가르지는 않는다** — 갈래 ②는
[2026-09-11 실험](2026-09-11-d67-required-schema-atom.md)이 이미 확정했다.

**결과 ③이 운영에 거는 것:** 그 실험이 `disable_any_whitespace`를 해법으로 실측(5/20 → 5/5)해
뒀는데 **이 run의 서버는 그 옵션 없이 떴고 7/127을 잃었다.** §7 우선순위 1이다.

### 3.3 `gpt-oss-20b`의 출력 토큰 회계 — 추론과 본문을 가른다

**목적.** `output_max_by_role.critic` 값을 정하려면 **무엇이 그 토큰을 먹는지** 알아야 한다.

**조건.** `state.db`에서 `confidence` 상위 8건의 Critic 프롬프트를
[`nodes/critic.py`](../../../src/orchestrator/nodes/critic.py)의 `_ask_model`과 같은 조립으로
재구성. `max_tokens=16384` 고정 · `Decision` 스키마 · `temperature 0.2` · `top_p 0.95` ·
`seed 20260901`. **본문/추론 토큰은 서버의 `/tokenize`로 각각 셌다**(추정이 아니다).

> **⚠ 스키마는 `_ask_model`과 같지 않다 — `Decision`이고, 하네스는 `DecisionDraft`를 보낸다.**
> 이 프로브를 돌린 시점에 `DecisionDraft`는 PR #182 브랜치에만 있었다. 그 PR이 머지되며
> ([`nodes/critic.py`](../../../src/orchestrator/nodes/critic.py) `OUTPUT_SCHEMA = DecisionDraft`)
> §3.3·§3.4가 잰 스키마가 실서빙과 어긋났다. **필드 집합은 `DecisionDraft` ⊂ `Decision`이다**
> — `Decision`이 `decision_id`·`finding_id`·`produced_by`·`run_id` 넷을 더 요구한다(8 대 4).
> 모델이 그 넷을 더 뱉어야 하므로 **여기 수는 `DecisionDraft`에 대한 상한이다**(구조적
> 논증이지 실측이 아니다 — 두 스키마를 같은 조건에서 나란히 재지는 않았다). 결론
> 「4,096이 충분하다」는 상한이 4,096 아래라서 성립하고, 스키마가 줄면 더 성립한다.
> 프로브의 `--schema draft` 팔이 이제 실서빙과 맞는 팔이다.

**결과.**

| `reasoning_effort` | 추론 tok min/중앙/max | 본문 tok min/중앙/max | `completion_tokens` 중앙/max | 초 중앙 | `finish=length` |
| --- | --- | --- | --- | --- | --- |
| `high` | 484 / 1,252 / **16,381** | 0 / 91 / 126 | 1,361 / **16,384** | 30.7 | **1/8** |
| `medium` | 69 / 142 / 227 | 82 / 86 / 98 | 242 / 327 | 5.5 | 0/8 |
| `low` | 12 / 12 / 23 | 76 / 77 / 84 | 108 / 113 | 2.5 | 0/8 |

**근거 로그.** ATOM `/srv/tenants/user/critic_reasoning_split.json`.

**해석 ① — `max_tokens`는 답변이 아니라 추론을 재고 있었다.** 본문은 세 조건 전부에서
76~126토큰이다. 세 자릿수 차이는 전부 추론 쪽에서 난다. `find_0014`는 `high`에서 추론
16,381토큰 · 본문 0자 · **382초**로 상한을 통째로 먹었고, 같은 Finding이 `medium`에서는
6.4초 · 280토큰이다.

**해석 ② — 지금까지의 「출력 꼬리」 서술이 답변 길이가 아니다.** `config/budgets.yaml`의
`output_max` 주석과 그것을 인용한 자리들이 드는 2,863·10,471 같은 수는
**`completion_tokens`이고 그 대부분이 추론이다.** ⚠ **소급 분해는 안 한다** — 그 실험들은
추론/본문을 안 갈랐으므로 이 보고서가 그 수치를 나눌 수 없다. 말할 수 있는 것은 **같은
모델·같은 스키마에서 오늘 잰 본문이 76~126토큰이라는 것**까지다.

**해석 ③ — 상한을 올리는 것은 처방이 아니다.** 같은 폭주가 `max_tokens 4096`에서 93초,
`16384`에서 **382초**다. 상한이 정하는 것은 폭주의 발생이 아니라 **비용**이고 그 비용이
상한에 정비례한다 — `config/budgets.yaml`의 `output_max` 주석이 2026-08-28에 같은 논법으로
8,192를 4,096으로 내렸다. 같은 결론이
[2026-09-11 실험](2026-09-11-d67-required-schema-atom.md)에도 있다(*"정상 응답이
332~522토큰이라 `output_max` 인상은 처방이 아니다"*).

**해석 ④ — 프롬프트는 작다.** 서버가 센 `prompt_tokens`가 **646~654**다. `_ask_model`이
`build_facts`에 `capsule=`·`source=`·`target=`을 **안 넘기므로** Critic 프롬프트에는 캡슐도
대상 원문도 안 실린다. 21,021토큰짜리 프롬프트는 `bench.sized_prompt()`의 filler가 만든
합성이고, 거기서 역산한 천장(32,768 − 21,021 = 11,747)도 그 합성의 수다. **이 경로의 실효
천장은 32,768 − 650 ≈ 32,100이다.**

### 3.4 하네스 실제 설정 재현 — 값 PR이 필요한가

**목적.** §3.3의 결론을 하네스가 실제로 보내는 요청으로 확인한다.

**조건.** `max_tokens=4096`(= 지금의 `thresholds.output_max`) · `reasoning_effort: high`(=
registry 선언값) · `Decision` 스키마(§3.3의 ⚠ — 실서빙 `DecisionDraft`에 대한 상한) · 자리표시자는 `_describe_path`/`_describe_evidence`가
실제로 내는 문자열(`(경로 없음)`·`(근거 없음)`) · 상위 8건 × 2시행.

**결과.** 16콜 · **JSON 실패 0 · `finish=length` 0** · `completion_tokens` 615~1,508
(추론 520~1,401 · 본문 78~114) · 초 min 29.0 / 중앙 45.2 / max 71.8 · 합 797초.

**근거 로그.** ATOM `/srv/tenants/user/critic_harness_settings.json`.

**해석 — `output_max_by_role.critic`을 선언하지 않는다.** 비폭주 최대가 1,508이라 4,096은
2.7배 여유다. 폭주는 확률적이고 드물다 — 실 프롬프트 `high` 전 시행에서 **3/32**(§3.3의 1건 +
첫 탐색 2건), `medium`·`low`에서 0/16. `thresholds.schema_retries: 1`이 두 번째 독립 시행을
준다. **[D46](../../open-questions/D/D46.md) (c)가 연 자리는 열린 채로 둔다** — 값을 넣으면
[D72](../../open-questions/D/D72.md)의 위쪽 한계 부재가 물리는데 넣을 이유가 실측에 없다.

⚠ **판정 정확도는 이 실험이 안 쟀다.** 센 것은 JSON이 유효한가와 몇 토큰을 쓰는가뿐이다.
⚠ **`reasoning_effort`를 내리는 것은 선택지가 아니다** — `gpt-oss-20b`를 Critic에 앉힌 근거가
그 효력에 얹혀 있다([PROJECT-CONTEXT.md](../../../PROJECT-CONTEXT.md) §1).

### 3.5 D67 `required` 팔 재현 — 그리고 **D46 해법이 선행 조건이라는 것**

**재현 목적이었다.** [2026-09-11 실험](2026-09-11-d67-required-schema-atom.md)이
`run_20260904_0001`의 프롬프트로 확정한 것을 **이 run의 프롬프트**로 한 번 더 돌렸다.
입력은 `prompts/0003_auditor.md`(v1 · stage 5 · 사실 블록 evidence 3건
`ev_0001`·`ev_0024`·`ev_0025`), `--trials 5`, 서버는 §메타 그대로
(**`disable_any_whitespace=False`**).

| 팔 | 성공 | `path` 채움 | `evidence_refs` 채움 | 미상 ID |
| --- | --- | --- | --- | --- |
| `stock` | 4/5 | **0/4** | **0/4** | 0 |
| `required` | **0/5** | — | — | — |
| `min_items` | **0/5** | — | — | — |

실패는 전부 같은 모양이다 — `Invalid JSON: EOF while parsing an object at line **14279** /
**7379** / **2513**`. 4,096토큰 출력에서 그 줄 수면 [D46](../../open-questions/D/D46.md)
공백 폭주다.

**해석 ① — `stock` 0/4는 D67 갈래 ②의 재현이다.** 사실 블록에 ID 셋이 실려 갔는데 성공한
4회 전부 두 필드를 비웠다.

**해석 ② — 새로 나온 것: 그 플래그 없이는 D67 수정을 적용조차 못 한다.** `stock`은 4/5가
유효한데 `required`·`min_items`는 **0/5**다. 스키마를 조이면 공백 폭주가 더 자주 난다.
2026-09-11 실험의 `disable_any_whitespace=False` 조건이 `required` **5/20**이었던 것과 같은
계열이고(그 실험은 옵션을 켜서 5/5를 얻었다), 이 표본은 그 25%에서 0을 뽑은 셈이다 —
**n=5라 「0%」가 아니라 「그 조건에서 못 쓴다」까지가 관측이다.**

→ **§7 우선순위 1은 편의가 아니라 선행 조건이다.** D67을 `required`로 닫으려면 D46 (a)가
먼저 서야 한다.

원문: ATOM `/srv/tenants/user/d67_probe_20260912.json`.

## 4. 막힌 것과 해결

| 벽 | 원인 | 해결 |
| --- | --- | --- |
| `tools/probe/d67_required_schema.py`가 첫 호출 전에 `TypeError` | PR #185가 `factory._model_client`의 `role`을 **키워드 필수**로 바꿨는데 그 호출부가 안 따라갔다 | 한 줄 수정. 이 PR에 포함 |
| 추론 길이가 늘 0으로 읽혔다 | **vLLM 0.27.1의 필드 이름은 `reasoning`이고 `reasoning_content`는 없다.** `message` 키는 `annotations`·`audio`·`content`·`function_call`·`reasoning`·`refusal`·`role` | 키를 고쳐 읽었다. §7 항목 2·3 |
| 시스템 `python3`에 pydantic이 없다 | 하네스는 `/srv/tenants/user/venv-probe`에서 돈다 | 그 venv로 실행 |

## 5. 미측정 항목과 사유

| 항목 | 왜 못 쟀는가 | 무엇을 하면 재는가 |
| --- | --- | --- |
| Critic 판정 정확도 | 고정 반박 표본(진짜 N + 오탐 N)이 선행이고 표본 크기·합격선은 D4가 정한다 | 그 표본을 만든 뒤 같은 프롬프트로 반복 |
| `disable_any_whitespace=True`에서 이 run의 재현 | 서버 재기동이 필요하고 승인 대기다 | 8002를 그 옵션으로 재기동 후 `cli run` 반복 |
| 폭주의 재현 조건 | `find_0014`가 같은 seed·같은 프롬프트에서 한 번은 폭주하고 두 번은 안 했다 — **결정적이지 않다** | 같은 요청 N≥30 반복해 비율을 낸다 |
| `repro_spec`만 optional 중 유일하게 채워지는 이유 | 축을 세우지 않았다 | 스키마에서 그 필드만 빼고/넣고 대조 |
| `enable_in_reasoning=True` | 서버 재기동 필요 | 그 값으로 재기동 후 §3.3 반복 |

## 6. 반증 조건

- **§3.1이 틀렸다면**: `evidence_refs`가 빈 Finding에서 `gate.evaluate`가 E0이 아닌 값을 내거나,
  `queued` Finding이 7단계에 들어간다. 둘 다 코드 한 줄씩이라 읽으면 갈린다.
- **§3.3이 틀렸다면**: 다른 프롬프트에서 본문이 1,000토큰을 넘는다. 표본은 상위 8건 × 3효력이다.
- **§3.4가 틀렸다면**: 같은 설정에서 `finish=length`가 나온다. **n=16의 0건은 「실패율 0」이
  아니다** — 10% 근방의 실패율을 못 배제한다.
- **필드명 정정이 틀렸다면**: 다른 vLLM 버전에서 `reasoning_content`가 채워진다. 이 관측은
  **0.27.1 하나**다.

## 7. 후속 과제

| 우선순위 | 항목 | 현황 |
| --- | --- | --- |
| 1 | 8002를 `--structured-outputs-config.disable_any_whitespace true`로 재기동 | **승인 받았고 다음 세션에 한다. 편의가 아니라 선행 조건이다** — 근거 셋: 2026-09-11 실험(`required` 5/20 → 5/5) · §3.2 결과 ③의 실서빙 7/127 · **§3.5의 `required` 0/5**(그 옵션 없이는 D67 수정을 적용조차 못 한다) |
| 2 | [`tools/probe/gpt_oss_structured.py`](../../../tools/probe/gpt_oss_structured.py)의 `reasoning_content` → `reasoning` | 이 PR에 포함. **그 필드로 낸 과거 관측(「reasoning 빔」)은 무효다** |
| 3 | [2026-09-09 보고서](2026-09-09-gpt-oss-20b-onboarding-atom.md)의 「`reasoning_content`가 빈 이유 — 안 갈랐다」 | 이 PR이 정정 블록을 단다 |
| 4 | `output_max_by_role.critic` 값 PR | **안 낸다.** §3.4가 근거 |

## 8. 정리 상태

프로브 종료 후 ATOM의 vLLM 서버 셋(8001·8002·8006)을 내렸다. 산출물 JSON 넷과 서버 로그
셋은 `/srv/tenants/user/`에 남긴다. `run_20260912_0001` 산출물은 보존.

## 9. 부록 — 재현 절차

```bash
# 1. 프롬프트 크기 분포만 (모델 호출 0회)
python tools/probe/critic_output_budget.py \
    --state-db /srv/tenants/user/artifacts/run_20260912_0001/state.db \
    --profile local_balanced --model-key gpt-oss-20b --dry-run \
    --out ~/probe_results/critic_budget_dryrun.json

# 2. 하네스 실제 설정 (§3.4)
python tools/probe/critic_output_budget.py \
    --state-db /srv/tenants/user/artifacts/run_20260912_0001/state.db \
    --profile local_balanced --model-key gpt-oss-20b \
    --schema decision --max-tokens 4096 --trials 2 --top 8 \
    --out ~/probe_results/critic_budget_4096.json
```

§3.3의 효력별 스윕은 registry가 선언한 `reasoning_effort`를 프로브가 그대로 싣기 때문에
**다른 효력을 재려면 registry를 바꿔야 한다.** 오늘은 그 축을 프로브 밖의 직접 호출로 쟀고
원문은 §메타의 JSON이다.
