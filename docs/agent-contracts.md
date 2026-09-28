# 에이전트 실행 계약과 데이터 스키마

에이전트는 프로세스가 아니라 **실행 계약**이다. 같은 역할을 서로 다른 모델로 A/B 평가할 때 바뀌는 것은 `model_profile` 하나뿐이어야 한다.

## 1. 실행 계약의 5요소

```yaml
agent:
  role: auditor              # scout | auditor | critic | judge
  model_profile: qwen3.8-27b       # model-serving.md의 registry key
  tool_policy:               # 이 역할이 호출 가능한 도구 화이트리스트
    - retriever.expand_capsule
    - index.callers
    - index.callees
  token_budget:
    input_max: 18000         # config의 thresholds.capsule_budget_tokens.expanded와 같은 값
    output_max: 32768        # config의 thresholds.output_max_for(auditor)와 같은 값. 요청에 max_tokens로 실린다
    retries: 1
  output_schema: Finding     # 아래 §3
```

> **`output_max`는 2026-08-28부터 강제된다 (D42, PR #36).** `config/budgets.yaml`의
> `thresholds.output_max`가 값을 들고 `adapters/openai_compat._request_payload`가 요청에
> `max_tokens`로 싣는다. 그전까지 이 줄은 선언만이었고 — `grep -rn output_max src/`가 0건이었다 —
> 서버가 자기 기본값까지 생성했다. `run_20260827_0001`의 auditor 호출 하나가 그렇게 5분을 먹고
> 클라이언트 타임아웃으로 run을 끊었다.
>
> **값이 2,000에서 8,192로 바뀐 것도 그때다.** 2,000은 강제된 적이 없어 검증된 적도 없는
> 숫자였는데, `run_20260827_0002`의 auditor 호출 하나가 `ok: true`로 **2,424 토큰**을 냈다 —
> 2,000이었으면 그 성공이 잘려 파싱 실패가 됐다. 근거와 남은 한계는 `config/budgets.yaml`의
> `output_max` 주석에 있다.
>
> **2026-08-28에 8,192에서 4,096으로 내렸다.** 근거는 헤드룸이 아니라 **폭주가 먹는 시간**이다 —
> `run_20260828_0002`는 869초 중 **792.6초(91.2%)를 실패한 `model_call` 5건**에 썼고, 63 tok/s
> 실측에서 상한까지 폭주하면 1회에 130초, `retries: 1`이라 실패 하나가 2회를 태운다. run 시간의
> 지배 항이 폭주이고 그 항이 상한에 정비례한다. **대가는 관측 최대 2,424 대비 여유가 3.4배에서
> 1.7배로 주는 것**이고, 그보다 큰 정상 응답이 나오면 역할별 상한(D46 (c))이 다음 수다.
> **2026-09-12에 「관측 최대 2,424」가 깨져 그 자리가 열렸다** — gpt-oss-20b Critic의
> `DecisionDraft` 18시행에서 성공 출력 최댓값이 **2,863**이라 여유가 1.7배에서 1.43배로
> 준다(절단은 0/18이므로 **닿았다는 뜻은 아니다**).
> **2,863의 원천이 이 저장소에 들어왔다** — 그 수를 실은
> [DecisionDraft 출력 예산](experiments/harness/2026-09-12-critic-decisiondraft-budget-atom.md)이
> PR #182와 함께 머지됐다. 뒤집힌 2,424는 `run_20260827_0002`로 추적된다
> ([v2 스모크](experiments/harness/2026-08-29-prompt-v2-smoke-atom.md)). 같은 인용이
> `config/budgets.yaml`의 `output_max` 주석에도 있다 — 두 자리가 이제 실재하는 보고서를
> 가리킨다. 자리는
> `thresholds.output_max_by_role`이고 선언이 없는 역할은 위 값을 그대로 쓴다
> (`Thresholds.output_max_for`). **값은 아직 어느 역할도 선언하지 않았다** — 실측과
> 한계는 `config/budgets.yaml`의 `output_max` 주석에 있다.
> ⏵ **2026-09-26 — 처음으로 한 역할이 선언했다.** Auditor(`qwen3.8-27b`)가 추론(사고)을 켜며
> `output_max_by_role.auditor`가 32,768이 됐다 — 전역 4,096은 그대로다. 값은 실측이 아니라 사고를 켜서
> 들어갔다(근거·한계는 `config/budgets.yaml`의 그 주석). 위 예시 블록이 그 값을 들고,
> `tests/test_docs_format.py`가 블록의 `role:`과 짝으로 대조한다.
>
> **2026-09-12 — 값을 안 정하기로 했고 그것이 실측의 결론이다.** 실제 Critic 프롬프트로
> 재니 `prompt_tokens`가 **646~654**이고(캡슐도 원문도 안 실린다) `gpt-oss-20b`의 출력은
> 추론과 본문으로 갈려 **본문이 76~126토큰**이다 — 위 2,863 같은 수는 그 둘의 합이지
> 답변 길이가 아니다. 하네스 실제 설정 16콜에서 `finish=length`가 0건이라
> **전역 4,096이 이미 충분하다.** 근거는
> [2026-09-12 보고서](experiments/harness/2026-09-12-critic-gptoss-token-accounting-atom.md)
> §3.3·§3.4이고, 같은 정정이 `config/budgets.yaml`의 `output_max` 주석에도 들어갔다.
> 타임아웃(`_TIMEOUT_S = 300`)은 **시도 하나당**이라 `wall_ms`(재시도 포함)와 직접 비교하면
> 안 된다 — 자세한 계산은 `config/budgets.yaml`의 `output_max` 주석에 있다.
>
> ⏵ **2026-09-24 — 두 가지가 움직였다**([스모크 보고서](experiments/harness/2026-09-24-roster-smoke-atom.md) · 비교표 밖).
> ① 비교표 밖 스모크(`checksum` 0)에서 한 번에 성공한 출력으로 **2,878**이 관측됐다(`seccoderx-8b` `scout_boundary`,
> 사고 포함). 위 2,863도 추론과 본문의 합이라 같은 축이고, 그 축으로 세면 위 「2,863 · 1.43배」가 「2,878 · 1.42배」가
> 된다(계산값). ② **느린 좌석에서는 상한보다 300초 요청 타임아웃이 먼저 온다.**
> `seccoderx-8b` 13.3 · Qwen3-14B 8.1 tok/s면 300초 안에 낼 수 있는 출력이 ≈3,990 · ≈2,430토큰이다(계산값). 그래서 위
> 「그 항이 상한에 정비례한다」(63 tok/s 기준)는 이 둘에서 성립하지 않는다. 같은 정정은 짝 설정 PR #262가
> `config/budgets.yaml`의 `output_max` 주석에 넣는다. 추적은 이슈 #261이다.
>
> **`input_max`는 아직 강제되지 않는다 (2026-08-25).** 이 값은 `expanded` 캡슐 예산을 비추는
> 선언이고, `grep -rn input_max src/`는 0건이다 — 아래 표의 "`input_max` 초과 → Retrieval
> 단계로 되돌려 capsule 축소"를 수행하는 코드가 없다. 더구나 **캡슐 예산은 프롬프트 크기의
> 상한이 아니다**: 대상 원문이 `ctx.read_source()`로(슬라이스 타깃의 Scout는 `ctx.read_slice()`로) 따로 실려 예산 밖에서 프롬프트를 키운다
> (`config/budgets.yaml`의 주석, [open-questions.md](open-questions.md) D21·D17). 그래서 이
> 숫자를 "프롬프트가 이 안에 들어온다"로 읽으면 안 된다.

계약 위반 시 동작:

| 위반 | 처리 |
| --- | --- |
| `output_schema` 파싱 실패 | `retries` 한도 내 재요청(스키마 오류 메시지만 첨부). 초과 시 후보를 `queued`로 두고 실패 사유 기록 |
| `input_max` 초과 | Retrieval 단계로 되돌려 capsule 축소. 축소 불가 시 후보 분할 |
| `tool_policy` 밖 도구 호출 | 거부 후 오류를 관측치로 기록(모델 비교 지표에 사용) |
| `token_budget` 누적이 run budget 초과 | Budget Ledger가 해당 단계 차단, 부분 결과로 진행 |

## 2. 역할 정의

### 2.1 Scout / Triage

- 목적: 많은 후보를 저비용으로 거른다. **최종 취약점 판정을 하지 않는다.**
- 입력: 경량 Context Capsule(함수 시그니처, 위험 함수 호출, diff 요약, 정적분석 신호 헤드라인)과 대상 코드.
  ⚠ **2026-09-24 — [ADR 0012](decisions/0012-harness-canon-v2.md) 결정 1: 대상 코드가 Joern
  슬라이스가 된다** — 슬라이스 1개가 후보 1개이고, v3 프롬프트가 요구하는 모양(줄번호 gutter ·
  `/* ===== path ===== */` 배너 · `FACTS.slice_criterion` — `prompts/scout_*/v3/prefix.md`)으로 간다.
  **지금 코드는 아니다** — `nodes/scout.py`가 `ctx.read_source()`로 대상 심볼 본문(줄번호·배너 없음,
  `read_symbol_source()`)을 `{{SOURCE}}`에 싣는다. 풀 프로필의 v3 프롬프트는 이미 슬라이스를 전제하므로 그
  둘이 지금 어긋난다([D82](open-questions/D/D82.md)). 구현은 Phase 3이다.
  ⏵ 2026-09-25 — `run --slices`로 돈 run은 이제 그 모양이다(Phase 3a PR #283 · 3b PR #284 · 3c): `{{SOURCE}}`가
  슬라이스 렌더이고 FACTS에 `slice_criterion`(`given_end`와, 슬라이서 entry의 file·line·symbol인 `given`)이
  붙는다. `--slices` 없는 run은 앞 문장 그대로라, 풀 프로필을 슬라이스 없이 돌리면 그 어긋남도 그대로다.
- 출력: `Candidate[]`. v3는 판정(`candidate`)과 근거(`analysis_summary`)를 한 호출에서 내는 `ScoutResponse`
  (`src/schemas/scout_response.py`)이고 하네스는 `.candidate`만 흘린다. ⚠ **ADR 0012 결정 2 —
  `reason_codes[0]`이 `triage_need_evidence`이면 하네스가 `requested_context`의 target을 심볼 인덱스에서
  찾아 같은 세션 후속 턴으로 준다(최대 3홉 — 다른 좌석이 review를 냈으면 돌지 않는다, 결정 2 「좌석 집계」).**
  지금은 그 루프가 0건이라 `triage_need_evidence`(0.30)도
  `triage_reject`(0.10)처럼 `scout_threshold` 미만으로 `queued`된다. 구현은 Phase 5다.
  ⏵ 2026-09-26 — 이 변경이 배선했다: `triage_need_evidence`는 더 이상 점수 임계로 떨어지지 않는다 —
  `policy._scout_triage`가 신호를 먼저 보고, NE 좌석은 같은 세션 후속 턴(최대 `max_hops`)을 거쳐
  정지 사유로 `queued`되거나(`NeStopReason`) 신호가 review·reject로 바뀌면 그 신호로 선다
  (`src/orchestrator/runner.py`의 `_run_ne_hops`·`_step_scout_settle`).
- 프롬프트 규약: thinking 최소화, 출력은 항목당 3줄 이내, 자유 서술 금지
- 실패 모드: 위험 함수 존재만으로 높은 점수를 주는 편향 → benign 샘플로 반드시 교정 측정([evaluation.md](evaluation.md))

### 2.2 Primary Auditor

- 목적: 선택된 후보에 대해 취약점 가설과 근거 경로를 세운다.
- 입력: 확장 Context Capsule + `Candidate` + 대상 원본 소스(`ctx.read_source()`). 표현은 원본 소스다 —
  [ADR 0012](decisions/0012-harness-canon-v2.md) 결정 1-b(2026-09-25 팀 합의, 넓은 (i))이고 Auditor 프롬프트가
  그것을 선언한다(`prompts/auditor/v3/body.md`의 `representation: original source code`).
  슬라이스 타깃이면 `SOURCE`는 슬라이스가 걸친 함수들의 원본이다 — entry 줄을 감싸는 함수(품는 함수를 모르면 —
  인덱스가 없거나 그 줄이 어느 정의에도 안 들면(프로토타입·전역·매크로) — entry 앞뒤 고정 창(`_AUDITOR_WINDOW_LINES`))가 먼저이고,
  나머지는 문자 수 상한(`_AUDITOR_SOURCE_CHARS`) 안에서 함수째 붙는다(`runner._auditor_source`). 상한에 빠진
  함수 수와 어느 함수에도 안 든 줄 수는 trace `auditor_source`가 든다.
  Evidence는 FACTS의 `evidence`로 id·kind·ok가 가고, content-aware 프롬프트 버전(`nodes/auditor.py`의
  `_CONTENT_AWARE_VERSIONS` — 지금 `v3`)에서는 근거 본문의 유계 요약(`evidence_summaries`)까지 싣는다.
  ⏵ 2026-09-29 — registry의 Auditor(`qwen3.8-27b`)가 v3라 그 본문이 실린다. v3 prefix는 `body` 필드를
  이름으로 부르지 않는다 — 모델이 그 본문을 쓰는지는 미측정이다.
- 출력: `Finding`(draft) + `MissingEvidence[]`
- 분석 축: source→sink 경로, memory lifetime/ownership, boundary check, 정수 연산, config·환경 조건 영향
- 규칙: 코드에서 확인 불가한 사실은 주장하지 말고 `MissingEvidence`로 요청한다. "확인 불가"가 유효한 출력이다.

### 2.3 Independent Critic

- 목적: 상위 후보만 **다른 모델 계열**로 반박한다. Auditor 설명의 재진술은 실패로 본다.
- 입력: `Finding` + Evidence 목록. **Auditor의 원문 추론은 전달하지 않는다**(주장·경로·근거 ID를 전달하고, content-aware 프롬프트 버전에서는 근거 본문의 유계 요약까지 싣는다 — #237/#247). ⚠ **2026-09-24 현재 그 본문 싣기는 꺼져 있다** — ~~11~~ 7 프로필(같은 날 로스터 밖 정리 뒤)의 critic이 전부 `v1`이고 v3를 켜는 것은 확인 run(#237 HIGH-1) 뒤다([PROJECT-CONTEXT.md](../PROJECT-CONTEXT.md) §5 「Critic v3 · 근거 본문」).
- 출력: `Decision`
- **요청은 `verdict`를 자유 문자열로 받고(`DecisionDraft`), `Verdict.from_text`로 표준값에 매핑해 `Decision`을 만든다.** `Decision.verdict`(열거형)를 구조화 출력 스키마에 `enum`으로 강제하면 grammar 마스크가 모델의 맞는 추론을 틀린 토큰으로 덮는다 — gpt-oss-20b 실측(2026-09-12): `enum` 강제 verdict 2/5, `enum` 제거 5/5, rationale은 둘 다 정답. ⚠ **원천은 PR #182 본문이고 그 run의 원문은 저장소에 없다** — 같은 날 커밋된 [`DecisionDraft` 예산 보고서](experiments/harness/2026-09-12-critic-decisiondraft-budget-atom.md)는 **다른 실험**이다(§5가 그 사실의 자리다).
- **매핑은 추측하지 않는다.** 표준값(`uphold`/`rebut`/`need_evidence`)이 **맨 앞 단어일 때**(`uphold: no guard…`, `**uphold**`, 이름표를 첫 구분자까지 한 번 걷어 — `verdict: uphold`·`final verdict = uphold`·`verdict:uphold`)와 짧은 명백한 단독 동의어만 읽는다. 표준값이 둘 이상 나오거나(`uphold or rebut`, 머리 단어가 표준값이어도 — `rebut: …not an uphold case`) 문장 속에 묻히면(`upholding is not warranted…`) 읽지 않는다 — **단어 경계 없는 접두어 매칭은 거부를 지지로 읽었다**(#182 리뷰 H1). 못 읽은 것은 **`need_evidence`(보류)** 로 보내며 원문을 `critic_verdict_unmapped` trace에 남긴다 — 미해석을 `uphold`(지지)로 소비하지 않는다(그러면 임의 문자열이 경로·근거 있는 Finding을 `validated`로 통과시킨다).
- **표준값 층과 동의어 층은 같은 정규화를 본다** — 단어 양끝의 장식(`**`·따옴표·백틱·목록 기호·번호 매김·비ASCII 구두점)을 뗀 뒤 양쪽을 대조한다. 장식 제거가 표준값 층에만 걸려 있던 판(#182 재리뷰 H-A)은 `Yes`를 UPHOLD로 읽고 `**Yes**`·`Yes.`를 `None`으로 읽어 **마침표 하나로 판정이 갈렸고**, 그 `None`이 `need_evidence`를 거쳐 게이트 `p3`(`upheld` 요구)에 걸려 **진짜 취약을 `validated`에서 막았다.** ⚠ **동의어에 CWE 이름을 넣지 않는다** — `use-after-free` 한 계열만 매핑되면 Critic 제거율이 대상 CWE에 따라 다른 규칙으로 만들어진다([evaluation.md](evaluation.md) §2.3).
- **ID·`produced_by`는 모델 스키마(`DecisionDraft`)에서 빼고 하네스가 주입한다** — 모델에 provenance를 맡기면 `produced_by.model`이 실제 모델이 아닌 `"ChatGPT"` 등으로 오염된다(실측).
- 반박 체크리스트: guard 존재 여부 / reachability(호출 가능 경로) / aliasing / lifetime / 컴파일 타임 조건·플래그 / 입력 도달성 제약
- 규칙: 반박은 근거 ID 또는 코드 위치를 동반해야 한다. 근거 없는 반박은 `uphold`로 처리하되 지표에 기록한다.

### 2.4 Evidence Judge (Evidence Gate)

- 기본은 **결정론적 규칙**이다. [architecture.md](architecture.md) §4의 **성립 조건 표**가 구현 명세다 — 같은 절의 승격 표는 게이트가 아니라 노드·정책의 명세이고, 두 표를 하나로 읽으면 6·7단계 순서가 거꾸로로 보인다.
- 모델 호출은 등급 경계가 애매한 경우(E1↔E2)로 한정한다. **v1에서는 그것도 열지 않았다** — `gate_node.py`가 "애매한 경우에도 모델을 부르지 않는다"이고 E1↔E2 경계는 `gate_ambiguous`로 기록만 하고 E1으로 확정된다. 판정 경로에 모델이 끼면 결정론이 깨져 제거율이 조건마다 달라지기 때문이다.
- 출력: 상태 전이 + 사유 코드. 사유 코드는 열거형으로 고정한다(자유 텍스트 금지).

## 3. 데이터 스키마

에이전트 간에는 아래 구조체만 오간다. 전체 conversation은 전달하지 않는다. 아래 값은 모두 합성 예시다.

> 구현 대응: `src/schemas/`. 이 절의 예시와 코드가 어긋나면 여러 곳이 서로 다른 것을 만들게 되므로, 예시는 `tests/test_schemas.py`가 실제로 파싱해 본다. 이번 갱신에서 **제안**한 변경은 §3.1에 이유와 함께 모아 두었다.

### Artifact

```json
{
  "artifact_id": "art_a1b2c3",
  "run_id": "run_20260901_0001",
  "kind": "source",
  "path": "src/parser/http.c",
  "digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "media_type": "text/x-c",
  "parent_id": null
}
```

`kind` 열거형: `source` | `binary` | `firmware` | `config` | `unknown`

`path`는 run artifact 디렉터리 기준 상대 경로다. `parent_id`는 펌웨어 이미지에서 추출된 파일의 원본을 가리킨다(binwalk 경로 추적).

### Candidate

```json
{
  "candidate_id": "cand_0001",
  "run_id": "run_20260901_0001",
  "artifact_id": "art_a1b2c3",
  "location": {"file": "src/parser/http.c", "symbol": "parse_header", "line_start": 120, "line_end": 188},
  "suspected_cwe": ["CWE-787", "CWE-190"],
  "score": 0.72,
  "reason_codes": ["memcpy_unbounded", "changed_in_diff"],
  "evidence_refs": ["ev_static_0007"],
  "state": "triaged"
}
```

### Evidence

```json
{
  "evidence_id": "ev_san_0031",
  "run_id": "run_20260901_0001",
  "kind": "sanitizer",
  "source": "asan",
  "artifact_id": "art_a1b2c3",
  "location": {"file": "src/parser/http.c", "line_start": 174},
  "payload_ref": "artifacts/run_20260901_0001/asan/0031.log",
  "digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "observed_at": "2026-09-01T10:22:31Z",
  "ok": true,
  "exit_code": 1,
  "error": null
}
```

`kind` 열거형: `static` | `sanitizer` | `crash` | `fuzz_coverage` | `build` | `poc_run` | `diff` | `graph`

`ok`는 **Evidence가 유효한가**가 아니라 **그 도구 실행이 성공했는가**다. ASAN이 crash를 잡은 것은 도구로서는 성공이므로 `ok: true`다. 도구가 실패하면 `ok: false` + `error` 필수로 기록한다 — 실패 사실 자체가 모델 비교 지표이므로 삼키지 않는다([engineering-guide.md](engineering-guide.md) §8.3).

### Finding

```json
{
  "finding_id": "find_0007",
  "run_id": "run_20260901_0001",
  "candidate_id": "cand_0001",
  "state": "hypothesized",
  "path": [
    {"step": "propagate", "symbol": "parse_header", "note": "len이 검증 없이 전달"},
    {"step": "sink", "symbol": "memcpy", "note": "고정 크기 스택 버퍼"}
  ],
  "trigger_status": "reachability_unknown",
  "confidence": 0.6,
  "rca_draft": "…",
  "cwe": "CWE-787",
  "title": "parse_header에서 길이 검증 없는 memcpy",
  "evidence_refs": ["ev_static_0007", "ev_san_0031"],
  "missing_evidence": ["mev_0002"],
  "repro_spec": null,
  "produced_by": {"role": "auditor", "model": "qwen3.8-27b", "prompt_version": "v3"}
}
```

`state` 열거형: `hypothesized` | `queued` | `validated` | `proven` | `reported`

**위 키 순서가 계약이다 (D51).** `Finding`의 선언 순서가 그대로 `model_json_schema()`의
property 순서가 되고 그 스키마가 `response_format`으로 서버에 실린다. **범주값**(`state`·
`trigger_status`)을 **숫자값**(`confidence`)과 **산문**(`rca_draft`)보다 먼저 두는 것이
요점이고, `produced_by`는 모델이 채우는 값이 아니라 하네스가 stamp하므로 맨 뒤다.
**그 순서가 생성까지 강제되는지는 아직 안 쟀다** — xgrammar에 달렸고 v2 프롬프트로 도는
첫 run이 답한다.

`trigger_status` 열거값: `triggerable` | `guarded` | `no_reach` | `reachability_unknown` | `no_signal`.
**스키마 타입은 `str | None`이고 열거형이 아니다** — 실제 emit 분포를 보기 전에 좁히면 못 본 값이
finding을 떨구므로, 지금은 뭐가 나오든 기록만 한다([finding.py](../src/schemas/finding.py) 주석).

> **출처 주의 — 위 다섯 중 넷만 이 저장소에서 확인된다 (D51·D52).**
> [single/v2 prefix](../prompts/single/v2/prefix.md) 규칙 4가 `triggerable`·`guarded`·`no_reach`·
> `reachability_unknown`을 정의하지만 그것은 **`rca_draft` 첫 문장의 분류**이지 이 필드가 아니고,
> **`no_signal`은 저장소 어디에도 없다.** 그리고 `prompts/` 어느 파일도 `trigger_status`를 내라고
> 시키지 않는데 하네스는 그 디렉터리에서만 프롬프트를 읽는다
> ([prompt_loader.py](../src/orchestrator/prompt_loader.py)). **그래서 이 필드는 해당 프롬프트가
> 저장소에 들어오기 전까지 어느 run에서든 `null`이다** — 채움률 0을 "모델이 안 냈다"로 읽으면 안 된다.
>
> ⏵ **2026-09-29 — 이 블록은 쓰인 지 14분 뒤에 틀려졌다.** 블록은 `93b06e6`(2026-08-29 15:16)이고,
> `52d9e82`(15:30)가 `prompts/auditor/v2`를 들이며 `trigger_status` 다섯 값(`no_signal` 포함)을 정의했다.
> `prompts/auditor/v3`(#237, 2026-09-22)도 같다. 그래도 registry가 v1만 골라 run에서는 `null`이었다 —
> **2026-09-29부터 registry의 Auditor(`qwen3.8-27b`)가 v3를 고른다.** 그 판으로 돈 run은 아직 없어
> 채움률·분포는 미측정이다([D51](open-questions/D/D51.md)). 위 Finding 예시도 그날 v3 계약에 맞췄다.

`repro_spec`은 `hypothesized`·`queued` 초안 단계에서는 `null`이다. `validated` 이상으로 올라가려면 아래 형태로 채워져야 한다.

```json
{
  "steps": ["./build.sh --sanitize=address", "./poc < seeds/0031.bin"],
  "expected_signal": "AddressSanitizer: heap-buffer-overflow at parse_header"
}
```

### MissingEvidence

```json
{
  "missing_id": "mev_0002",
  "run_id": "run_20260901_0001",
  "finding_id": "find_0007",
  "need": "recv_request 호출부에서 len 상한이 강제되는지",
  "how_to_obtain": "index.callers(recv_request) 확장 후 guard 검색",
  "blocking": true
}
```

### Decision

```json
{
  "decision_id": "dec_0004",
  "run_id": "run_20260901_0001",
  "finding_id": "find_0007",
  "verdict": "uphold",
  "rebuttal_axis": ["guard", "reachability"],
  "rationale": "상위 호출부에 길이 상한이 없음(src/net/server.c:88 확인)",
  "evidence_refs": ["ev_graph_0012"],
  "produced_by": {"role": "critic", "model": "gpt-oss-20b", "prompt_version": "v1"}
}
```

`verdict` 열거형: `uphold` | `rebut` | `need_evidence`

### RunRecord (재현성)

```json
{
  "run_id": "run_20260901_0001",
  "condition": "B3prime@pipeline",
  "profile": "harness_pool",
  "repo_commit": "0000000000000000000000000000000000000000",
  "repo_dirty": false,
  "source_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "model_checksums": {
    "auditor": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    "critic": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  },
  "prompt_versions": {"scout_memory": "v3", "scout_boundary": "v3", "scout_injection": "v3", "auditor": "v3", "critic": "v1"},
  "tool_images": {
    "semgrep": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  },
  "tool_runner": "docker",
  "device": "atom",
  "budget": {"input_tokens": 4000000, "output_tokens": 400000, "gpu_seconds": 7200},
  "thresholds": {"scout_threshold": 0.5},
  "seed": 20260901,
  "sampling": {"temperature": 0.2, "top_p": 0.95},
  "model_sampling": {
    "auditor": {"temperature": 0.7, "top_k": 20, "top_p": 0.8, "repetition_penalty": 1.05}
  },
  "compile_flags": {
    "std": {"c": "-std=gnu17", "c++": "-std=gnu++17"},
    "fixed_include_dirs": ["/src"]
  },
  "kb_snapshot": "kb-2026-08-30",
  "started_at": "2026-09-01T09:00:00Z",
  "finished_at": null,
  "stopped_after": null
}
```

`condition`은 `^(B1|B1prime|B3|B3prime)@pipeline$` 형식이다. `'`는 파일명·URL·쉘에서 깨지므로 `prime`으로 적는다.

> **2026-09-20 — `@2x2`를 뺐다.** 예전에는 `@(2x2|pipeline)`이었다 — [evaluation.md](evaluation.md) §2.1의 2×2 설계와 §2.2의 파이프라인 조건이 같은 라벨을 다른 축으로 써서, 어느 정의인지 라벨에 넣지 않으면 비교표가 섞였기 때문이다. **2×2가 폐기되고 Scout 오케스트레이터로 확정되면서 축이 하나만 남았고**, 그 충돌을 등재했던 [D1](open-questions/D/D1.md)도 닫혔다. ⚠ **`@pipeline` 접미사 자체는 남긴다** — 기존 값의 형식을 지키고 축이 다시 늘 때 자리가 된다.

`finished_at`이 비어 있거나 `stopped_after`가 채워진 run(예산 소진 등으로 중단)은 부분 결과이므로 비교표에 넣지 않는다([evaluation.md](evaluation.md) §6). `stopped_after`는 [architecture.md](architecture.md) §3의 단계 번호(0~9)다.

날짜·시각은 모두 UTC ISO-8601(`YYYY-MM-DDTHH:MM:SSZ`)로 기록한다.

### 3.1 제안: Harness v1 스캐폴드에서 발견한 불일치

아래는 **확정이 아니라 제안**이다. `src/schemas/`는 여러 모듈이 함께 건드리는 결합점이므로 팀 검토 후에 확정한다.

| # | 제안 | 근거 |
| --- | --- | --- |
| P1 | 모든 구조체에 `run_id` 추가 | 위 예시에서 `run_id`를 가진 것은 Candidate뿐이었다. Evidence Store가 run 단위 수명이고([knowledge-base.md](knowledge-base.md) §1) 같은 조건을 seed만 바꿔 3회 반복하므로([evaluation.md](evaluation.md) §2.3), run 소속이 없으면 반복 실행 결과가 한 저장소에서 섞인다 |
| P2 | `Artifact` 스키마 신설 | [architecture.md](architecture.md) §3의 0단계 출력이 `Artifact[]`인데 이 절에 스키마가 없었다. ~~펌웨어가 1급 입력이므로(D9)~~ → **1차는 C/C++ 소스이고 바이너리·펌웨어는 2차다**([ADR 0010](decisions/0010-harness-structure-and-model-roster.md) §5 · D9 닫힘). 추출 원본을 가리키는 `parent_id`는 그 2차 경로용으로 **남긴다** — 지금 세팅하는 코드는 없다(`git grep parent_id -- src`가 `src/schemas/artifact.py`의 선언과 `nodes/preprocess.py`의 주석뿐이다 — 2026-09-24. [STATUS.md](../STATUS.md)는 그날부터 보관본이다 — ADR 0012 결정 5) |
| P3 | `Evidence`에 `ok`·`exit_code`·`error` 추가 | [engineering-guide.md](engineering-guide.md) §8.3의 "도구 실패도 Evidence로 기록"과 §6 trace 필수 항목("도구 실행 명령·종료 코드")을 담을 자리가 없었다 |
| P4 | `Finding`에 `repro_spec` 추가 | [architecture.md](architecture.md) §4의 E3 조건이 "재현 명세 존재"이고 §3 8단계 입력이 "`Finding` + 재현 명세"인데 그 구조체가 없었다. 없으면 E3→E4 판정이 사람 판단이 된다 |
| P5 | `RunRecord`에 `source_hash`·`thresholds`·`sampling`·`kb_snapshot`·`finished_at`·`stopped_after` 추가 | 앞 다섯은 [engineering-guide.md](engineering-guide.md) §5 재현성 필수 항목인데 예시에 없었다. 뒤 둘은 [evaluation.md](evaluation.md) §6의 "기록이 불완전한 run은 비교표에 넣지 않는다"를 판정할 근거다 |
| P6 | `condition`에 정의 축을 포함(`B3prime@pipeline`) | [evaluation.md](evaluation.md) §2.2가 이미 지적한 라벨 충돌. ⚠ 2026-09-20에 2×2 축이 폐기돼([ADR 0011](decisions/0011-retire-the-2x2-condition-axis.md)) 구분할 두 축이 없어졌다 — `@pipeline` 하나다 |
| P7 | 상태 열거형은 하나, 객체별 허용 값은 `Literal`로 제약 | [architecture.md](architecture.md) §4의 상태 머신은 하나의 체인인데 이 절은 Candidate와 Finding에 각각 `state`를 둔다. 열거형을 쪼개면 전이 추적이 끊기므로, 열거형은 하나로 두고 Candidate는 `{candidate, triaged, queued}`, Finding은 `{hypothesized, queued, validated, proven, reported}`로 좁혔다 |
| P8 | `under_review` 같은 중간 상태를 두지 **않음** | [disclosure-policy.md](disclosure-policy.md)의 2인 검증은 상태 전이가 아니라 사람 승인 게이트다. 자동 전이가 없는 상태를 상태 머신에 넣으면 단계별 제거율 지표가 오염된다 |
| P9 | 프롬프트 버전은 `v1`부터 시작 | 실제 프롬프트는 아직 없으므로 v1에서 시작한다([engineering-guide.md](engineering-guide.md) §2의 디렉터리 예시도 전부 `v1`이다) |

검토가 필요한 미해결 항목:

1. **근거 없는 반박의 처리 위치.** §2.3은 "근거 없는 반박은 `uphold`로 처리하되 지표에 기록한다"고 정한다. 현재 구현은 `Decision` 스키마에서 이를 막지 않고 `is_grounded`만 제공한 뒤 정규화·기록을 오케스트레이터에 맡긴다 — 스키마에서 거부하면 Critic의 구조화 출력이 파싱 단계에서 실패해 "근거 없는 반박"과 "스키마 파싱 실패"가 한 지표로 뭉개지기 때문이다. 반대로 이 선택은 근거 0건짜리 `rebut`이 저장소에 남는 것을 허용한다. 둘 중 어느 비용을 받을지 결정 필요.
2. **`rebuttal_axis`·`CapsuleSection.kind`의 열거형 승격.** §2.3의 반박 체크리스트 6종과 [knowledge-base.md](knowledge-base.md) §4의 섹션 종류는 사실상 닫힌 집합이다. 지금은 문자열로 두었으나 값이 안정되면 열거형으로 닫는 편이 낫다.
3. **`Candidate.reason_codes`의 축.** 위 예시의 `memcpy_unbounded`·`changed_in_diff`는 §2.4가 열거형 고정을 요구한 "상태 전이 사유"가 아니라 트리아지 신호다. 현재는 두 축을 분리해 전자만 열거형(`ReasonCode`)으로 두었다. 이 해석이 맞는지 확인 필요.

## 4. 프롬프트 관리 규약

1. 모든 프롬프트는 파일로 버전 관리하고 `prompt_version`을 결과에 기록한다. 버전이 바뀌면 이전 실행 결과와 직접 비교하지 않는다.

> ### ⚠ 이 규칙이 발화하지 못한 건이 하나 있다 — `prompts/critic/v1`·`v2` (2026-09-12, PR #182)
>
> **본문이 바뀌었는데 `version`이 안 바뀌었다.** 그래서 `prompt_version: "v1"`이 **서로 다른
> 두 텍스트**를 가리킨다. 위 규칙 1도 [evaluation.md](evaluation.md) §2.3 규칙 4("프롬프트
> 버전이 바뀌면 재측정한다")도 **버전 문자열이 바뀌는 것을 발화 조건으로 삼으므로 자동으로는
> 안 걸린다.** 이 문단이 그 자리를 대신한다.
>
> **왜 범프하지 않았나.** `prompt_template_version`이 `ModelEntry`별이라 커밋된 9개 프로필 중
> **7개에서 critic 엔트리가 scout·auditor와 공유된다** — `fake`(dev 5개 + `single_gpu_12g`)와
> `qwen3-coder-30b-a3b`(`b1_single`). critic만 v3로 올리면 그 둘이 없는 `prompts/scout/v3`·
> `prompts/auditor/v3`를 찾는다. 역할별 `prompt_version`([D67](open-questions/D/D67.md) ·
> 이슈 #172의 선택지 (c))이 들어오면 그때 v3로 가른다. 되돌리지 않은 이유는
> `prompts/critic/v1/meta.yaml` 머리 주석에 있다.
>
> ⚠ **2026-09-24 — 위 「9개 중 7개」는 그때의 수다.** 지금 커밋된 프로필은 7개이고 critic 엔트리를
> 다른 역할과 공유하는 것은 `dev_*` 다섯(`fake`)뿐이다 — `single_gpu_12g`·`b1_single`은 지웠고
> `harness_pool`·`_hybrid`의 critic `gpt-oss-20b`는 scout·auditor와 안 겹친다.
>
> **영향 범위 — 커밋된 run 기준 [관측].** `prompt_versions`에 `critic v1`이 박힌 run은 **둘**
> 이고([2026-08-25](experiments/harness/2026-08-25-pipeline-zlib542-atom.md) `run_20260825_0001` ·
> [2026-08-26](experiments/harness/2026-08-26-pipeline-zlib542-remeasure-atom.md)
> `run_20260826_0001`, 각각 critic `model_call` 8건) **둘 다 프로필이 `single_gpu_12g`라
> critic이 Fake다.** Fake는 프롬프트를 읽지 않으므로 **그 8건의 판정값은 이 텍스트의
> 함수가 아니다** — 바뀐 것은 라벨의 모호성이지 그 run의 수치가 아니다.
>
> ⚠ **말할 수 있는 것은 거기까지다.** 두 run의 `prompt_versions.critic`·`prompt_sha256`은
> 그대로 남아 있고, 그 라벨을 "그 날의 v1 본문"으로 되짚는 것은 이제 커밋 해시를 거쳐야 한다.
>
> **실서빙 critic이 `critic/v1`로 돈 커밋된 run은 확인되지 않았다.**
> [2026-08-29 v2 스모크](experiments/harness/2026-08-29-prompt-v2-smoke-atom.md)의 v1 팔은
> **critic 호출 0건**(Finding 6건이 전부 6단계에서 멈췄다)이고 v2 팔이 critic `v2` ×3이며,
> [2026-09-04 실서빙 run](experiments/harness/2026-09-04-pipeline-zlib-serving-atom.md)은
> **7단계에서 죽었고 그 보고서 자신이 "비교표에 못 들어간다"고 적는다.** 확인 방법은
> `git grep -n "prompt_version" -- docs/` + 각 보고서의 조건·프로필 절이다.

> ⏵ **2026-09-29 — Auditor가 v1 → v3로 바뀌었다**(`harness_pool`·`_hybrid`의 `qwen3.8-27b`, 근거 본문 배선과
> 같은 변경). 이 규칙대로 **그 뒤 run의 Auditor 출력은 그 전 run과 직접 비교하지 않는다** — run의
> `prompt_versions.auditor`가 가른다. ⚠ 규칙 5(고정 표본 회귀 측정 뒤 반영)와 [evaluation.md](evaluation.md) §2.3
> 규칙 4(재측정)는 이 변경 전에 돌지 않았다 — 측정 없이 켰다. Critic은 v1 그대로다.
2. 공통 system prompt + weakness checklist + output schema는 **prefix로 고정**한다(prefix cache 적중 목적). 후보별 가변부는 항상 prefix 뒤에 붙인다.
3. 출력은 JSON 스키마 강제(structured output). 파싱 실패는 지표로 집계한다(모델 비교 항목).
4. 프롬프트에 평가 정답 단서(CVE ID, 문제명, patch diff)를 넣지 않는다. → [evaluation.md](evaluation.md) 누출 방지
5. 프롬프트 변경은 [evaluation.md](evaluation.md)의 고정 표본에서 회귀 측정 후 반영한다.

> 2026-08-08 Harness v1 스캐폴드 구현 중 발견된 불일치와 제안 사항. 팀 검토 필요.
