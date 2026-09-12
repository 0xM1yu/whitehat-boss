# 에이전트 실행 계약과 데이터 스키마

에이전트는 프로세스가 아니라 **실행 계약**이다. 같은 역할을 서로 다른 모델로 A/B 평가할 때 바뀌는 것은 `model_profile` 하나뿐이어야 한다.

## 1. 실행 계약의 5요소

```yaml
agent:
  role: auditor              # scout | auditor | critic | judge
  model_profile: qwen3-coder-30b-a3b   # model-serving.md의 registry key
  tool_policy:               # 이 역할이 호출 가능한 도구 화이트리스트
    - retriever.expand_capsule
    - index.callers
    - index.callees
  token_budget:
    input_max: 18000         # config의 thresholds.capsule_budget_tokens.expanded와 같은 값
    output_max: 4096         # config의 thresholds.output_max와 같은 값. 요청에 max_tokens로 실린다
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
> ⚠ **2,863의 원천은 아직 이 저장소에 없다** — `2026-09-12-critic-decisiondraft-budget-atom.md`가
> PR #182와 함께 들어온다. 뒤집히는 쪽인 2,424는 `run_20260827_0002`로 추적된다
> ([v2 스모크](experiments/harness/2026-08-29-prompt-v2-smoke-atom.md)).
> **그 보고서가 머지되면 이 ⚠ 문단을 그 파일 링크로 바꾼다** — 그 순간 "아직 없다"가
> 거짓이 되는데 CI가 그것을 못 잡는다(형식만 본다). 같은 메모가
> `config/budgets.yaml`의 `output_max` 주석에도 있고 **둘 다 고쳐야 한 건이 끝난다.** 자리는
> `thresholds.output_max_by_role`이고 선언이 없는 역할은 위 값을 그대로 쓴다
> (`Thresholds.output_max_for`). **값은 아직 어느 역할도 선언하지 않았다** — 실측과
> 한계는 `config/budgets.yaml`의 `output_max` 주석에 있다.
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
> **`input_max`는 아직 강제되지 않는다 (2026-08-25).** 이 값은 `expanded` 캡슐 예산을 비추는
> 선언이고, `grep -rn input_max src/`는 0건이다 — 아래 표의 "`input_max` 초과 → Retrieval
> 단계로 되돌려 capsule 축소"를 수행하는 코드가 없다. 더구나 **캡슐 예산은 프롬프트 크기의
> 상한이 아니다**: 대상 원문이 `ctx.read_source()`로 따로 실려 예산 밖에서 프롬프트를 키운다
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
- 입력: 경량 Context Capsule(함수 시그니처, 위험 함수 호출, diff 요약, 정적분석 신호 헤드라인)
- 출력: `Candidate[]`
- 프롬프트 규약: thinking 최소화, 출력은 항목당 3줄 이내, 자유 서술 금지
- 실패 모드: 위험 함수 존재만으로 높은 점수를 주는 편향 → benign 샘플로 반드시 교정 측정([evaluation.md](evaluation.md))

### 2.2 Primary Auditor

- 목적: 선택된 후보에 대해 취약점 가설과 근거 경로를 세운다.
- 입력: 확장 Context Capsule + `Candidate`
- 출력: `Finding`(draft) + `MissingEvidence[]`
- 분석 축: source→sink 경로, memory lifetime/ownership, boundary check, 정수 연산, config·환경 조건 영향
- 규칙: 코드에서 확인 불가한 사실은 주장하지 말고 `MissingEvidence`로 요청한다. "확인 불가"가 유효한 출력이다.

### 2.3 Independent Critic

- 목적: 상위 후보만 **다른 모델 계열**로 반박한다. Auditor 설명의 재진술은 실패로 본다.
- 입력: `Finding` + Evidence 목록. **Auditor의 원문 추론은 전달하지 않는다**(주장·경로·근거 ID만 전달).
- 출력: `Decision`
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
    {"step": "source", "symbol": "recv_request", "note": "네트워크 입력"},
    {"step": "propagate", "symbol": "parse_header", "note": "len이 검증 없이 전달"},
    {"step": "sink", "symbol": "memcpy", "note": "고정 크기 스택 버퍼"}
  ],
  "trigger_status": null,
  "confidence": 0.6,
  "rca_draft": "…",
  "cwe": "CWE-787",
  "title": "parse_header에서 길이 검증 없는 memcpy",
  "evidence_refs": ["ev_static_0007", "ev_san_0031"],
  "missing_evidence": ["mev_0002"],
  "repro_spec": null,
  "produced_by": {"role": "auditor", "model": "qwen3-coder-30b-a3b", "prompt_version": "v1"}
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
  "produced_by": {"role": "critic", "model": "devstral-small-2-24b", "prompt_version": "v1"}
}
```

`verdict` 열거형: `uphold` | `rebut` | `need_evidence`

### RunRecord (재현성)

```json
{
  "run_id": "run_20260901_0001",
  "condition": "B3prime@pipeline",
  "profile": "local_balanced",
  "repo_commit": "0000000000000000000000000000000000000000",
  "repo_dirty": false,
  "source_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "model_checksums": {
    "auditor": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    "critic": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  },
  "prompt_versions": {"scout": "v1", "auditor": "v1", "critic": "v1"},
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
  "kb_snapshot": "kb-2026-08-30",
  "started_at": "2026-09-01T09:00:00Z",
  "finished_at": null,
  "stopped_after": null
}
```

`condition`은 `^(B1|B1prime|B3|B3prime)@(2x2|pipeline)$` 형식이다. [evaluation.md](evaluation.md) §2가 지적하듯 2×2 설계와 파이프라인 조건이 같은 라벨을 다른 축으로 쓰므로, 어느 정의인지 라벨에 포함하지 않으면 비교표가 섞인다. `'`는 파일명·URL·쉘에서 깨지므로 `prime`으로 적는다. 라벨 통합 여부는 [open-questions.md](open-questions.md) D1.

`finished_at`이 비어 있거나 `stopped_after`가 채워진 run(예산 소진 등으로 중단)은 부분 결과이므로 비교표에 넣지 않는다([evaluation.md](evaluation.md) §6). `stopped_after`는 [architecture.md](architecture.md) §3의 단계 번호(0~9)다.

날짜·시각은 모두 UTC ISO-8601(`YYYY-MM-DDTHH:MM:SSZ`)로 기록한다.

### 3.1 제안: Harness v1 스캐폴드에서 발견한 불일치

아래는 **확정이 아니라 제안**이다. `src/schemas/`는 여러 모듈이 함께 건드리는 결합점이므로 팀 검토 후에 확정한다.

| # | 제안 | 근거 |
| --- | --- | --- |
| P1 | 모든 구조체에 `run_id` 추가 | 위 예시에서 `run_id`를 가진 것은 Candidate뿐이었다. Evidence Store가 run 단위 수명이고([knowledge-base.md](knowledge-base.md) §1) 같은 조건을 seed만 바꿔 3회 반복하므로([evaluation.md](evaluation.md) §2.3), run 소속이 없으면 반복 실행 결과가 한 저장소에서 섞인다 |
| P2 | `Artifact` 스키마 신설 | [architecture.md](architecture.md) §3의 0단계 출력이 `Artifact[]`인데 이 절에 스키마가 없었다. 펌웨어가 1급 입력이므로(D9) 추출 원본을 가리키는 `parent_id`가 필요하다 |
| P3 | `Evidence`에 `ok`·`exit_code`·`error` 추가 | [engineering-guide.md](engineering-guide.md) §8.3의 "도구 실패도 Evidence로 기록"과 §6 trace 필수 항목("도구 실행 명령·종료 코드")을 담을 자리가 없었다 |
| P4 | `Finding`에 `repro_spec` 추가 | [architecture.md](architecture.md) §4의 E3 조건이 "재현 명세 존재"이고 §3 8단계 입력이 "`Finding` + 재현 명세"인데 그 구조체가 없었다. 없으면 E3→E4 판정이 사람 판단이 된다 |
| P5 | `RunRecord`에 `source_hash`·`thresholds`·`sampling`·`kb_snapshot`·`finished_at`·`stopped_after` 추가 | 앞 다섯은 [engineering-guide.md](engineering-guide.md) §5 재현성 필수 항목인데 예시에 없었다. 뒤 둘은 [evaluation.md](evaluation.md) §6의 "기록이 불완전한 run은 비교표에 넣지 않는다"를 판정할 근거다 |
| P6 | `condition`에 정의 축을 포함(`B3prime@pipeline`) | [evaluation.md](evaluation.md) §2.2가 이미 지적한 라벨 충돌. D1이 확정될 때까지 두 축을 구분해 기록해 둔다 |
| P7 | 상태 열거형은 하나, 객체별 허용 값은 `Literal`로 제약 | [architecture.md](architecture.md) §4의 상태 머신은 하나의 체인인데 이 절은 Candidate와 Finding에 각각 `state`를 둔다. 열거형을 쪼개면 전이 추적이 끊기므로, 열거형은 하나로 두고 Candidate는 `{candidate, triaged, queued}`, Finding은 `{hypothesized, queued, validated, proven, reported}`로 좁혔다 |
| P8 | `under_review` 같은 중간 상태를 두지 **않음** | [disclosure-policy.md](disclosure-policy.md)의 2인 검증은 상태 전이가 아니라 사람 승인 게이트다. 자동 전이가 없는 상태를 상태 머신에 넣으면 단계별 제거율 지표가 오염된다 |
| P9 | 프롬프트 버전은 `v1`부터 시작 | 실제 프롬프트는 아직 없으므로 v1에서 시작한다([engineering-guide.md](engineering-guide.md) §2의 디렉터리 예시도 전부 `v1`이다) |

검토가 필요한 미해결 항목:

1. **근거 없는 반박의 처리 위치.** §2.3은 "근거 없는 반박은 `uphold`로 처리하되 지표에 기록한다"고 정한다. 현재 구현은 `Decision` 스키마에서 이를 막지 않고 `is_grounded`만 제공한 뒤 정규화·기록을 오케스트레이터에 맡긴다 — 스키마에서 거부하면 Critic의 구조화 출력이 파싱 단계에서 실패해 "근거 없는 반박"과 "스키마 파싱 실패"가 한 지표로 뭉개지기 때문이다. 반대로 이 선택은 근거 0건짜리 `rebut`이 저장소에 남는 것을 허용한다. 둘 중 어느 비용을 받을지 결정 필요.
2. **`rebuttal_axis`·`CapsuleSection.kind`의 열거형 승격.** §2.3의 반박 체크리스트 6종과 [knowledge-base.md](knowledge-base.md) §4의 섹션 종류는 사실상 닫힌 집합이다. 지금은 문자열로 두었으나 값이 안정되면 열거형으로 닫는 편이 낫다.
3. **`Candidate.reason_codes`의 축.** 위 예시의 `memcpy_unbounded`·`changed_in_diff`는 §2.4가 열거형 고정을 요구한 "상태 전이 사유"가 아니라 트리아지 신호다. 현재는 두 축을 분리해 전자만 열거형(`ReasonCode`)으로 두었다. 이 해석이 맞는지 확인 필요.

## 4. 프롬프트 관리 규약

1. 모든 프롬프트는 파일로 버전 관리하고 `prompt_version`을 결과에 기록한다. 버전이 바뀌면 이전 실행 결과와 직접 비교하지 않는다.
2. 공통 system prompt + weakness checklist + output schema는 **prefix로 고정**한다(prefix cache 적중 목적). 후보별 가변부는 항상 prefix 뒤에 붙인다.
3. 출력은 JSON 스키마 강제(structured output). 파싱 실패는 지표로 집계한다(모델 비교 항목).
4. 프롬프트에 평가 정답 단서(CVE ID, 문제명, patch diff)를 넣지 않는다. → [evaluation.md](evaluation.md) 누출 방지
5. 프롬프트 변경은 [evaluation.md](evaluation.md)의 고정 표본에서 회귀 측정 후 반영한다.

> 2026-08-08 Harness v1 스캐폴드 구현 중 발견된 불일치와 제안 사항. 팀 검토 필요.
