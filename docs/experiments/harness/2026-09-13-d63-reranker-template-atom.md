# D63 후속 — Qwen3-Reranker instruction 양식: 서버는 안 붙인다, 부분 양식이 정식과 ≈ — ATOM

> **결론 둘.**
> ① **`/v1/rerank`(pooling)는 Qwen3-Reranker에 instruction 템플릿을 안 붙인다** — vLLM 0.27.1
> 소스 확인. 그래서 모델은 훈련 때 기대한 양식이 통째로 빠진 맨 텍스트를 받아 왔다(D63 (a)에서
> 식별자 질의가 무너진 이유). **정식 채점법은 `/rerank`가 아니라 생성 + `P(yes)`**다(모델 README).
> ② **`/rerank`로 넣을 수 있는 부분 양식(`<Instruct>/<Query>/<Document>`)이 정식 생성 채점과
> 거의 같았다** — 실경로 질의에서 맨질의 FAIL · 부분양식 PASS ≈ 생성 PASS. **그래서 client를
> 생성 채점으로 재작성할 필요 없이, `/rerank` 호출에 양식만 씌우면 된다**(이 PR이 그 배선).
>
> ⚠ **안 닫는 것.** 표본이 작고(문서 8건·질의 2·압축된 한 줄 코드) 정식 채점(C)도 완벽하지 않다
> (안전한 방해물을 상위에 올리기도). 실조각·32건·리랭커-끈 기준선은 D63 (a)대로 배선 후에 남았다.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험일 | 2026-09-13 (ATOM) |
| 소스 확인 대상 | `vllm==0.27.1` @ `/srv/tenants/user/venv-probe` — `model_executor/models/config.py`·`interfaces.py`·`entrypoints/pooling/scoring/io_processor.py`, 모델 README(`Qwen3-Reranker-4B` 스냅샷 `@22e6836`) |
| 서빙 | A·B: 리랭커 8005(`is_original_qwen3_reranker`) · C: 일반 causal LM 8008(오버라이드 없이, `/v1/completions`) — 둘 다 `Qwen/Qwen3-Reranker-4B` bf16 |
| 채점(C) | 전체 공식 양식으로 생성 `max_tokens=1`·`temperature=0`·`logprobs=20`, `P(yes)=softmax(lp_yes, lp_no)` |
| `run_id` | **없다.** 어댑터 밖 직접 호출 — 비교표 밖 |
| 원문 | `/srv/tenants/user/d63_official_template_20260913.txt` · 프로브 `d63_official_template_test.py`(ATOM 홈) |

## 1. 소스 확인 — 서버는 Qwen3에 양식을 안 붙인다

- **`is_original_qwen3_reranker: true`** (`config.py`의 `Qwen3ForSequenceClassificationConfig`)는 **점수 계산법만** 설정한다 — `method="from_2_way_softmax"` + `classifier_from_token=["no","yes"]`. 입력 포맷과 무관.
- **`/rerank`는 모델이 `get_score_template`을 구현했을 때만 템플릿을 붙인다**(`io_processor.py`의 `supports_score_template` 분기). 그 메서드를 구현한 모델은 **`jina_vl` 하나뿐**이고 Qwen3는 아니다 → `else`(sep 토큰)로 가 **맨 query+document**를 넣는다.
- **정식 사용법은 생성 채점**이다 — 모델 README의 vLLM 예제가 `/rerank`가 아니라 `LLM.generate` + `allowed_token_ids=[yes,no]` + `logprobs`로 `P(yes)`를 낸다. 전체 양식:
  ```
  <|im_start|>system
  Judge whether the Document meets the requirements ... answer can only be "yes" or "no".<|im_end|>
  <|im_start|>user
  <Instruct>: {task}\n<Query>: {q}\n<Document>: {doc}<|im_end|>
  <|im_start|>assistant\n<think>\n\n</think>\n\n
  ```
  `/rerank`(pooling)로는 system prefix·`<think>` suffix를 못 싣는다 — `<Instruct>/<Query>/<Document>` **부분**만 가능.

## 2. 실험 — 부분 양식이 정식 생성 채점과 같나

문서 8건(취약 V1-V3·안전 S1·무관 I1-I4)에 실경로 질의(`parse_header`)와 자연어 대조군을 세 팔로.
⚠ **문서를 한 줄로 압축했다** — D63 (a) 보고서는 여러 줄 포맷이라 **입력이 다르다**(아래 H1 주석).

**instruction(TASK) 문자열은 이것이다** — 코드 `VllmRerankerClient._RERANK_INSTRUCTION`이 **글자까지 같은 것**을 배포한다:
> `Given a code-security query, retrieve C code snippets that contain the vulnerability the query describes.`

| 팔 | 방식 | Q1(실경로) 전체-8 | Q1 top-5 취약 | Q3(자연어) 전체-8 |
| --- | --- | --- | --- | --- |
| A | `/rerank`, 맨 질의 (오늘 harness) | **FAIL** | **3/3** | FAIL |
| B | `/rerank`, 부분 양식 | **PASS** | **3/3** | **PASS** |
| C | 생성 + 전체 공식 양식 + `P(yes)` | **PASS** | **3/3** | **PASS** |

⚠ **top-5 렌즈로는 셋이 같다(전부 3/3).** 배선 판단의 정본 렌즈는 top-5인데(D63 (a) §6 — 리랭커가
캡슐에 넣는 것이 `top_n=5`) **그 렌즈에서 A·B·C가 안 갈린다.** 그러니 "부분 양식이 A를 이긴다"는
**전체-8 렌즈**에서만 성립한다 — 아래 §3이 그럼에도 배선하는 이유를 따로 적는다.

⚠ **H1 — 이 A 팔이 D63 (a)의 같은 팔과 숫자가 다르다.** (a)의 Q1 raw는 top-5 취약 **2/3**(V1 탈락)이고
Q3 raw는 **PASS**인데, 여기(압축 문서)선 Q1 top-5 **3/3**·Q3 **FAIL**이다. 밝혀진 차이는 문서 압축뿐 —
**순위가 문서 포맷에 불안정하다**(두 보고서가 같은 날 D63.md에 나란히 들어간다). 32건·실조각 실측 전까지
어느 숫자도 확정이 아니다.

## 3. 함의 — 배선

**`VllmRerankerClient`가 `/rerank` 호출에 부분 양식을 씌운다**(이 PR): query → `<Instruct>: {task}\n<Query>: {q}`, 각 document → `<Document>: {doc}`. 생성 채점 재작성은 **불필요**(B≈C). 양식 지식은 `vllm_retrieval.py` 안에 둔다 — 그 파일이 "vLLM 지식은 이 파일 밖으로 안 샌다"는 경계다(모듈 docstring).

**배선 근거는 "순위가 더 낫다"가 아니라 "모델에 양식을 아예 안 주던 결함을 고친다"이다.** top-5 렌즈(배선 정본)에선 A·B·C가 다 3/3이라 이 표본이 **순위 우위를 증명하지 않는다**(§2 ⚠). 증명하는 것은 둘뿐이다 — ① 서버가 양식을 안 붙인다(소스, §1), ② 붙여도 정식 생성 채점만큼은 나온다(B≈C, HTTP로 충분). 즉 **훈련 때 기대한 입력을 주는 정확성 수정**이고, 그것이 순위를 얼마나 올리는지는 32건·실조각 실측의 몫이다.

⚠ **지금 프로필의 리랭커는 `qwen3-reranker-0.6b`이고 이 실측은 `Qwen3-Reranker-4B`다.** 즉 배선(양식 씌우기)은 두 모델 다에 적용되지만 실측 근거는 4B에서 났다. 연구 §1이 0.6b를 코드에서 **−1.99**(리랭킹 없는 기준선보다 낮음)로 적으므로, 4B 실측을 0.6b 배선의 순위 근거로 읽지 않는다 — 이 PR은 순위가 아니라 **양식**을 고친다.

⚠ **이 배선은 순위 품질의 근본 검증이 아니다.** B≈C는 8건·압축 코드의 지시적 결과다. 실조각·32건·리랭커-끈 기준선(D63 (a) 「안 닫는 것」·[로스터](../../research/2026-09-08-roster.md) §2 배제셋 ③)은 배선 **뒤에** 재고, 0.6b→4b 교체는 그 검증을 통과한 뒤다. 이 PR이 고치는 것은 **"모델에 양식을 아예 안 주던" 결함**이다.
