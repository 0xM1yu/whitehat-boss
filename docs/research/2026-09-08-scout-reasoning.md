# Scout 세 멤버의 모델 배정 — 연역·귀납·귀추에 근거가 있나

**조사일 2026-09-08.** 라벨: `[출처주장]` · `[제3자]` · `[계산]` · `[미확인]`.
**우리 장비 실측은 [experiments/](../experiments/README.md)에 있다.**

**조사 도구 제약.** firecrawl·exa MCP가 없어 **WebSearch·WebFetch로 대신했고**, 검증에는
**arXiv API 질의**를 직접 썼다.

## 0. 검증한 주장

발표 자료가 Scout 세 멤버를 **추론 유형**으로 나누고 모델을 이렇게 배정한다.
*"논문 기준"*이라는 단서가 붙어 있다.

| 추론 유형 | 배정된 모델 |
| --- | --- |
| 연역 (deductive) | Phi-4 |
| 귀납 (inductive) | Mistral-Small-24B |
| 귀추 (abductive) | QwQ-32B |

---

# 1. 근거가 없다 — 인용이 아니라 보간이다

**세 모델을 같은 평가에서 세 유형으로 나눠 잰 논문이 존재하지 않는다.** 더 나아가
**세 모델을 함께 언급한 논문 자체를 찾지 못했다.**

| 질의 | 결과 |
| --- | --- |
| arXiv `"QwQ-32B" AND "Mistral-Small-24B" AND "Phi-4"` | **0건** |
| arXiv `"QwQ-32B" AND "Phi-4" AND "abductive"` | **0건** |
| arXiv `"Mistral-Small-24B" AND "inductive reasoning"` | **0건** |

`[제3자]`(arXiv API 직접 조회). ⚠ **arXiv API의 `all:`은 제목·초록·코멘트를 훑지 표 본문을
전수하지 않는다.** 모델명은 결과 표에만 있는 경우가 많다. 그래서 이 0건으로 단정하지 않고,
**해당 분야 주요 벤치마크를 하나씩 열어 모델 목록을 직접 확인했다.**

| 논문 | 세 모델 포함 | 실제 평가 대상 |
| --- | --- | --- |
| [LogiEval](https://arxiv.org/html/2505.11854v1) — 연역·귀납·가추·유추 분해 | **QwQ-32B만** | DeepSeek-R1, QwQ-32B, Qwen3-235B, Claude3.7-Thinking, Grok3, o4-mini 등 |
| [ARCHE](https://arxiv.org/html/2511.12485) — 가추·연역·귀납 분해 | **셋 다 없음** | Claude-Opus-4, GPT-4o, o3, DeepSeek-R1, Gemini-2.5-Pro, Grok-3/4 |
| [FalsifyBench](https://arxiv.org/html/2606.04751) — 귀납(규칙 발견) | **Mistral-Small-24B만** | GPT-5.2-chat, gpt-oss-120b/20b, DeepSeek-V3.1, MiniMax-M2.5, GLM-5 |
| [Wiring the 'Why'](https://arxiv.org/html/2604.08016v1) — 가추 서베이 | **셋 다 없음** | Qwen2.5/3, Llama3.1/3.3, DeepSeek-V3.2, GPT-4o/5.4 |
| [코드 추론 3분법](https://arxiv.org/html/2502.13170) — 귀납·연역·가추 **코드** 메타벤치 | **셋 다 없음** | GPT-4o(주), Llama-3.1-70B, Qwen-max, Claude 3.5 |
| [Theorem-of-Thought](https://arxiv.org/html/2506.07106) — 가추·연역·귀납 3-에이전트 | **셋 다 없음** | Mistral-7B-v0.3, DeepSeek-7B, **Phi-3.5-mini** |

## 1.1 구조가 가장 닮은 논문 — 그런데 결정적으로 다르다

**Theorem-of-Thought**가 발표안과 모양이 같다(가추·연역·귀납 3-에이전트 병렬). 그런데
`[출처주장]`:

> 세 에이전트는 **같은 모델에 프롬프트 스타일만 바꿔** 돌린다 —
> *"Use the {style} reasoning style to answer the following question."*
> **모델을 나누지 않는다.**

그리고 쓰인 모델이 **Phi-3.5-mini · Mistral-7B-v0.3 · DeepSeek-7B**다. 발표안의 셋과
**버전이 전부 다르다.**

> **누군가 이 논문의 3분할 구조를 가져오면서 모델명을 「같은 계열의 최신판」으로
> 갈아끼운 것으로 보인다.** ⚠ **이건 추론이지 확인된 경위가 아니다** `[계산/추론]`.

## 1.2 그리고 있는 데이터는 배정과 **반대로** 간다

| 배정 | 실제 |
| --- | --- |
| 귀추 → QwQ-32B | [LogiEval](https://arxiv.org/html/2505.11854v1) 가추 부문에서 QwQ-32B는 **7개 중 5위(82.68%)**. 1위 o4-mini 87.67% `[출처주장]` |
| 귀납 → Mistral-Small-24B | [FalsifyBench](https://arxiv.org/html/2606.04751)에서 *"최고 instruction-tuned 모델 DeepSeek-V3.1이 41%"* → instruct인 Mistral-Small-24B는 **41% 이하** `[계산]`. ⚠ 개별 수치가 **그림에만 있고 표에 없다** `[미확인]` |
| 연역 → Phi-4 | [Phi-4 카드](https://huggingface.co/microsoft/phi-4)가 연역·논리 추론에 **구체적 주장을 하지 않는다** `[출처주장]` |

**그리고 발표안이 어느 Phi-4인지 특정하지 않는다.** phi-4(16K, non-reasoning) ·
phi-4-reasoning · phi-4-reasoning-plus는 성격이 완전히 다르다. **배정 이전에 표기 결함이다.**

---

# 2. 추론 유형 축 자체가 흔들린다

**① 유형별 1·2위 격차가 배정 근거가 되기엔 너무 작다.** [LogiEval](https://arxiv.org/html/2505.11854v1) `[출처주장]`:

| 유형 | 1위 | 2위 | 격차 |
| --- | --- | --- | --- |
| 가추 | o4-mini 87.67 | Gemini2.0-Flash-Thinking 86.94 | **0.73pp** |
| 연역 | DeepSeek-R1 78.16 | Gemini2.0-Flash-Thinking 77.62 | **0.54pp** |
| 귀납 | Grok3 85.62 | Gemini2.0-Flash-Thinking 84.03 | **1.59pp** |
| 유추 | Qwen3-235B 90.54 | DeepSeek-R1 89.60 | **0.94pp** |

이 논문이 *"different reasoning LLMs excel on different tasks and reasoning types, without
a winner across the board"*라고 쓴 것은 사실이고 발표안 전제에 가장 가까운 문장이다.
**그러나 그 격차가 0.5~1.6pp다.** 1점 차이를 모델 배정 근거로 쓰지 않는다는 기준을 대면
**네 유형 전부 탈락한다.**

**② 반대 증거 — 강한 모델이 세 유형을 전부 이긴다.** [ARCHE](https://arxiv.org/html/2511.12485)에서
**Grok-3이 가추·연역·귀납 1위를 독식**하고 격차도 10.6~14.6pp로 크다 `[출처주장]`.
전문화가 아니라 **일반 역량**이 결정한다.

**③ 가장 무거운 반증 — 유형 구분이 모델 내부에 없다.**
[Linear Probes Detect Task Format, Not Reasoning Mode](https://arxiv.org/html/2606.02907)
`[출처주장]`: Qwen3-14B 32층에서 선형 프로브가 세 유형을 **100%로 분리**하지만, 포맷 교란
(데이터셋 출처·선택지 수·응답 길이)을 제거하면 **정확도가 33.5%(우연 수준)로 무너진다.**
인과 스티어링도 무작위 섭동과 구분되지 않는다(p=0.286).

저자 결론: *"The entire linear separability of reasoning modes is explained by format features."*

> **프로브가 잡은 것은 추론 양식이 아니라 벤치마크 포맷이었다.**
> 발표안은 **모델 내부에 존재한다는 근거가 없는 축**으로 모델을 나누고 있다.

**④ 귀납 능력 자체가 규칙 유도가 아니다.** [MIRAGE](https://openreview.net/forum?id=tZCqSVncRf)(ICLR 2025)
`[출처주장]`: LLM은 *"poor rule-based reasoners"*이고 실제 패러다임은 **neighbor-based
reasoning**이다.

**⑤ 그리고 위 평가가 전부 자연어다.**

> ### ⚠ LogiEval(시험 문제) · ARCHE(자연어 논증) · FalsifyBench(WordNet 의미 범주) ·
> ### 가추 서베이(αNLI·e-CARE·ProofWriter) — **하나도 코드가 아니다.**

전이도 약하다. [Beyond Output Correctness](https://arxiv.org/html/2604.12379) `[출처주장]`:
기존 추론 평가기들은 *"developed and validated on general NLP tasks"*이고 코딩 과제에서
**거의 무작위 수준(AUCROC 약 0.56~0.57)**으로 떨어진다. 같은 논문 —
*"correct outputs may arise from flawed reasoning, and vice versa."*

---

# 3. 대신 무엇으로 나누나 — CWE 계열에는 근거가 있다

우리 Scout는 이미 CWE 계열로 나뉘어 있다. **그 축에는 문헌 근거가 있다.**

[Neuro-Symbolic Reasoning for Vulnerability Detection](https://arxiv.org/html/2607.03963v1)이
취약점을 **두 축**으로 가른다 `[출처주장]`:

- **stateless obligation** — spatial/dereference 계열. 구조적 페어링으로 처리
- **stateful obligation** — lifecycle 계열. **temporal ordering 추적**이 필요

그리고 UAF에 대해 — 신경망 베이스라인이 **전부 놓쳤다**. *"every neural baseline misses it"*
(DeepSeek-V4-Pro · Codex agent · Claude Code agent 각 3회 독립 실행 전부 SAFE 반환).

**난이도는 발표안의 배분과 정확히 반대 방향이다.**
[VulDetectBench](https://arxiv.org/abs/2406.07595) Task 2(CWE 유형 추론) `[출처주장]`:

| CWE | GPT-4 정확도 |
| --- | --- |
| **CWE-78** (OS command injection) | **1.00** |
| **CWE-23 · CWE-36** (path traversal) | **1.00** |
| CWE-476 (NULL deref) | 0.62 |
| CWE-191 (integer underflow) | 0.50 |

논문 설명: 잘 되는 것은 *"vulnerabilities with more distinctive features, such as CWE-78 and
CWE-23"*이고, 안 되는 것은 *"requiring procedural analysis and possessing more hidden
characteristics"*다.

[LLMxCPG](https://arxiv.org/html/2507.16585v1)의 CWE별 F1 `[출처주장]`: CWE-119 0.970 ·
CWE-415 0.852 · CWE-416 0.848 · CWE-190 0.792 · CWE-125 0.455.
⚠ **이 논문은 차이를 추론 유형이 아니라 학습 데이터 부족으로 설명한다** — 추론 유형 근거로
인용하면 안 된다.

## 3.1 그래서 배분이 뒤집혀 있을 가능성이 크다

**아래 표의 「함의」 열은 우리 추론이다 — 인용이 아니다** `[계산/추론]`.

| Scout | CWE | 문헌이 붙이는 성격 | 함의 |
| --- | --- | --- | --- |
| **Memory** | 415 · 416 | **stateful · lifecycle · temporal ordering** `[출처주장]` | **가장 어렵다.** 가장 강한 모델이 가야 한다 |
| **Boundary** | 119 · 125 · 787 · 190 | **stateless · spatial** `[출처주장]`, 190은 range·constraint | 중간 |
| **Injection** | 78 · 22 · 134 | source→sink taint reachability. **LLM이 가장 잘하는 부류** `[출처주장]` | **가장 쉽다.** 가장 가벼운 모델로 충분하다 |

> **발표안이 「귀추 → Injection」으로 매핑한다면, 가장 무겁고 비싼 추론 모델(QwQ)을
> LLM이 이미 1.00을 내는 가장 쉬운 계열에 배치하는 것이 된다.** 반대로 가장 어려운
> Memory 계열에는 **컨텍스트 16,384짜리 non-reasoning 14B(Phi-4)**가 간다.

## 3.2 탐지와 검증은 다른 일이다 — 그리고 우리 구조가 이미 그렇게 갈려 있다

[VulnSage](https://arxiv.org/html/2503.17885v1) `[출처주장]`:

| 과제 | 결과 |
| --- | --- |
| **탐지** | **추론 모델이 앞서지 않는다** — CodeLlama 7B **63.70%** > DeepSeek-R1 7B 57.19% |
| **패치 검증** | **추론 모델이 강하다** — R1 32B **73.36%** |

> **가장 실행 가능한 시사점이다. Scout(탐지)는 non-reasoning, 추론 모델은 뒤 검증 단계.**
> 발표안은 이 분리를 안 하고 Scout에 추론 모델을 넣는다.

---

# 4. thinking 모드가 우리 출력 예산과 정면 충돌한다

**CLAUDE.md §2.1대로 문서가 아니라 config를 열어 대조했다** — `config/budgets.yaml`의
`output_max: 4096`이고 `src/adapters/factory.py`가 그 값을 `max_tokens`로 실제로 싣는다.
⚠ **2026-09-12 갱신**: #185가 역할별 상한을 열어 그 줄이 `max_tokens=config.thresholds.output_max_for(role)`가 됐다. **선언한 역할이 없는 동안은 값이 같다** — 아래 논증은 그대로 서지만, critic에 값을 넣는 PR이 이 절을 다시 봐야 한다.

QwQ-32B의 **측정된** 응답 길이(Round 1) `[제3자]`
([Think Twice](https://arxiv.org/html/2503.19855v1) Table 2) 대비 `[계산]`:

| 벤치마크 | Round-1 평균 토큰 | ÷ 4,096 |
| --- | --- | --- |
| GPQA-Diamond | 13,860.5 | 3.38배 |
| AIME 2024 | 13,566.1 | 3.31배 |
| MATH-500 | 8,489.9 | 2.07배 |
| **LiveCodeBench** (코드에 가장 근접) | **4,473.0** | **1.09배** |

> **네 벤치마크 전부에서 reasoning trace 평균 길이만으로 이미 출력 예산을 넘는다.
> JSON에 남는 토큰이 0이다.** 코드에 가장 가까운 LiveCodeBench조차 넘는다 —
> **이게 가장 정직한 대입이고, AIME의 3.3배를 Scout 예측치로 쓰면 안 된다.**

**이로써 QwQ-32B를 Scout에서 뺀 판단이 직관이 아니라 config로 확인된 근거를 갖는다.**

같은 실패 모드를 [The Coupling Tax](https://arxiv.org/pdf/2605.07686)가 직접 쟀다 `[출처주장]`:
Qwen3-8B GSM8K b=256에서 **thinking 응답의 98.6%가 잘리고**, MATH-500 b=4096에서 **55.3%가
잘린다.** 그리고 *"non-thinking mode matches or outperforms thinking mode on GSM8K and MATH-500
at every budget up to 2048 tokens."*

**같은 계열 대조** `[제3자]` ([Do LLMs Overthink Basic Math Reasoning?](https://arxiv.org/html/2507.04023)):

| 모델 | 평균 출력 토큰 | 정확도 |
| --- | --- | --- |
| Phi-4 (non-reasoning) | 378.6 | **78.92%** |
| Phi-4-reasoning | 6,066.2 | 72.23% |

**16.0배 토큰을 쓰고 정확도는 6.69pp 내려갔다** `[계산]`.

---

# 5. 구조화 출력 — "해친다"는 단정이 갈린다

⚠ **이 절의 수치 상당수는 표 추출(요약 모델이 렌더된 표를 읽은 것)이다.** 옮기기 전에
원문 표를 여는 것이 CLAUDE.md §2.1이다.

| 방향 | 근거 |
| --- | --- |
| **인과 효과가 없다** | [EACL 2026 Findings](https://aclanthology.org/2026.findings-eacl.91/) — **동료심사된 유일한 정면 연구.** 8개 추론 과제 **48개 시나리오 중 43개에서 인과 효과 없음.** 기존 논쟁의 모순을 *"the failure to design comparable prompts … rendering the comparisons nonequivalent"*로 귀속 `[출처주장]` |
| **해친다** | [The Format Tax](https://arxiv.org/abs/2604.03616) — 손실의 **92%가 포맷 요청 프롬프트만으로** 이미 발생. qwen3-8b 평균 −8.7pp `[출처주장, 미심사]` |
| **모델 여력에 달렸다** | [Capacity, Not Format](https://arxiv.org/abs/2606.09410) — Opus **−5.3pp** 대 Haiku **−36.2pp** `[출처주장, 미심사]` |
| **다양성을 압축한다** | [Parikh](https://arxiv.org/abs/2607.18476), 44개 모델 — JSON **요청만으로** modal answer 41% → 64%. *"the collapse lives in the model's response to the register, **not the decoder**"* `[제3자]` |

> ### ⚠ 첫 둘이 정면 모순이다. **A는 동료심사(EACL Findings), B는 미심사 프리프린트다.**
> 그리고 **같은 Claude Haiku 4.5에 대해 Format Tax는 +1.2pp, Capacity는 −28~−36pp를 낸다.**
> 판정하지 않는다.

## 5.1 그런데 완화책은 여러 출처가 같은 방향을 가리킨다

| 출처 | 조치 | 효과 |
| --- | --- | --- |
| [arXiv 2608.08254](https://arxiv.org/abs/2608.08254) `[출처주장, 단독저자·미심사]` | **라벨 필드 앞에 중간 추론 필드를 스키마에 넣는다** | **+15~24점.** 그리고 *"extended thinking alone did not produce a comparable gain"* |
| [Capacity, Not Format](https://arxiv.org/abs/2606.09410) | 자유 추론 → 별도 단계에서 JSON 재포맷 | 손실의 **80~87% 회복** |
| [The Format Tax](https://arxiv.org/abs/2604.03616) | 2-Turn(자유 생성 → 재포맷) | 72개 비교 중 42개에서 유의 개선, **평균 +6.8pp** |

**세 출처가 독립적으로 "먼저 추론하고 나중에 형식화하라"에 수렴한다.**

⚠ 같은 2608.08254가 반대쪽 경고도 한다 — **스키마 필드 설명이 제2의 지시 채널**이라
프롬프트와 충돌하면 **5~45pp 하락**한다(Claude Haiku 4.5 52.5% → 7%).

## 5.2 도구 호출과 겹칠 때 — 극단값 하나

[Constraint Tax](https://arxiv.org/abs/2606.25605) `[출처주장, 미심사, 표 추출]`:
Tool Calling과 JSON Schema를 **동시에 켜면** 오픈웨이트 모델들이 **도구 호출을 아예 멈춘다**
(TIR 100% → 0%). 기전은 JSON Schema의 문법 토큰 마스크가 tool-call 토큰을 도달 불가로
만드는 것. Two-Pass로 100% 회복.

> ⚠ **100 → 0은 극단값이고 단일 미심사 프리프린트의 표 추출이다.** 우리 8단계가
> 도구를 쓰므로 **알아 둘 값어치는 있으나, 근거로 쓰려면 원문 표를 열어야 한다.**

---

# 6. 모델을 갱신한다면

셋 다 승계됐고, **아키텍처 방향이 우리 요구(MoE)와 어긋난다.**

| 발표안 | 2026-09 현재 |
| --- | --- |
| **Phi-4** (14B dense, ctx **16,384**) | **Phi-5는 찾지 못했다** `[미확인]`. 최신은 Phi-4-reasoning-vision-15B(dense, ctx 16,384). MoE Phi는 Phi-mini-MoE 7.6B가 최대 — **너무 작다.** **→ Phi 계열은 우리 요구와 방향이 어긋난다** |
| **Mistral-Small-24B** (dense) | **Mistral Small 4 (119B-A6B MoE)**, 2026-03, Apache-2.0, 256k, `reasoning_effort` 손잡이, native JSON `[출처주장]`. **Mistral이 small 라인을 dense → MoE로 전환했다** |
| **QwQ-32B** (32.5B dense) | **superseded.** Qwen 자신이 *"Qwen3가 thinking mode에서 이전 QwQ를 넘어선다"*고 명시 `[출처주장]`. QwQ 별도 라인이 없어지고 thinking이 본 모델에 흡수됐다 |

> ### ★ 그리고 이 조사에서 가장 중요한 공백 하나
> **조사한 모든 벤더 모델 카드(Phi · Mistral · Qwen · OpenAI · DeepSeek · GLM · MiniMax · IBM)
> 중 결과를 연역·귀납·귀추로 쪼개 발표한 것이 하나도 없다.** 전부 집계 점수
> (GPQA · AIME · LiveCodeBench · SWE-bench)만 낸다.
>
> **즉 「이 MoE 모델의 연역 추론 성능」은 지금 벤더 자료로 인용할 수 없다.**

**부수 발견 — CRUXEval이 벤더 보고에서 사실상 사라졌다** `[미확인]`. 2025~2026 카드에서
찾지 못했고, 보고가 LiveCodeBench · SWE-bench(Verified·Multilingual·Pro) · Terminal-Bench로
옮겨갔다. **발표 자료에 CRUXEval을 축으로 남기면 비교 대상을 못 찾는다.**

---

# 7. 그래서 무엇을 하나

1. **발표 자료에서 "논문 기준"이라는 표현을 뺀다.** 세 배정 어느 것도 인용으로 방어되지
   않는다. 유지하려면 **"설계 가설"**로 표기를 낮춘다.
2. **추론 유형 축을 CWE 계열 축으로 둔다.** Scout는 이미 그렇게 나뉘어 있고, 문헌이
   지지하는 구분은 **stateful(415·416) / stateless-spatial(119·125·787) /
   taint-reachability(78·22·134)**다. 이건 출처가 있다.
3. **배분을 뒤집는다.** 가장 강한 모델은 **Memory Scout**에, 가장 가벼운 모델은
   **Injection Scout**에.
4. **Scout는 non-reasoning으로, 추론 모델은 검증 단계로.** VulnSage의 탐지≠검증 분리가 근거다.
5. **구조화 출력은 「먼저 추론, 나중에 형식화」로 짠다** — 스키마에 추론 필드를 앞에 두거나
   2단계로 나눈다. 세 출처가 같은 방향이다.
6. **Phi-4를 쓸 거라면 어느 변종인지 적는다.** 그리고 **컨텍스트 16,384**가 우리 캡슐 예산에
   드는지 먼저 확인한다.

# 8. 확인하지 못한 것

- **FalsifyBench의 Mistral-Small-24B 정확한 수치** — 그림에만 있다. "41% 이하"는 `[계산]` 상한이다.
- **Phi-4-reasoning의 개별 추론 과제 점수** — abstract만 확인했다.
  **"Phi-4의 연역 추론 점수"를 지금 인용하면 안 된다.**
- **Mistral Small 4의 절대 벤치마크 수치** — 벤더가 비교 서술만 하고 숫자를 안 냈다.
- **같은 모델 thinking ON/OFF의 스키마 준수율 직접 비교** — 측정이 없다.
- **QwQ-32B 대 동급 instruct의 같은 과제 토큰 비교** — 없다. §4의 16배는 **Phi-4 쌍**이지
  QwQ가 아니다. **섞으면 §2.2 위반이다.**
- **코드 취약점 탐지 과제에서 reasoning 대 instruct의 출력 토큰·스키마 준수율을 잰 논문** —
  찾지 못했다. **우리가 실측할 여지가 그대로 남아 있다.**
- **에이전트를 겨냥한 지시문은 열람한 페이지에서 발견되지 않았다.**

# 9. 2026-09-14 — 실측으로 갈렸다: CWE 계열 분담이 추론 유형 분담을 이긴다

**§8 마지막 항목**(*"코드 취약점 탐지 과제에서 reasoning 대 instruct를 잰 논문을 못 찾았다 — 우리가 실측할 여지가 남아 있다"*)**을 실험팀이 실측으로 채웠다.** §1~§7이 추론 유형(연역·귀납·귀추) 배정에 **문헌 근거가 없다**를 보였고, 이번 실측이 **성능에서도 CWE 계열 분담(memory·boundary·injection)이 낫다**를 보탰다.

⚠ **provenance.** 실험팀의 **별도 실험 하네스**에서 돈 것이다 — 이 저장소 하네스 run이 아니고 **`run_id`가 없다**(비교표 밖, [evaluation.md](../evaluation.md) §6). 데이터셋은 Juliet clean·perturbed(각 N=296)와 sliced(N=106)이고, 모델은 `Qwen3-Coder-30B-FP8`·`Qwen3-8B`·`Qwen3-4B`·`Granite-3.3-8B`·`Hermes4-70B`다. 이 중 Granite·Hermes·Qwen3-4B는 **우리 로스터 자리가 아니다.**

## 무엇이 갈렸나

**`Qwen3-Coder-30B-FP8`**(로스터의 `scout_memory`·Auditor A안 모델) **[실측 · 실험팀 하네스 · `run_id` 없음].**

| 순위 | 구조 | pair_acc | MCC | Accuracy |
| --- | --- | --- | --- | --- |
| 1 | CWE 분담 | 46/148 | +0.239 | 0.619 |
| 2 | CWE 분담 (Agent 특화) | 23/148 | +0.129 | 0.561 |
| 3 | 연역/귀납/귀추 (Agent 특화) | 16/148 | +0.159 | 0.581 |
| 4 | 연역/귀납/귀추 | 12/148 | +0.097 | 0.548 |

**가장 성능이 높은 구성이 CWE 분담이다** — `MCC +0.239`·`pair_acc 46/148`로, 연역/귀납/귀추의 **어느 구성보다도**(최고 `MCC +0.159`·최고 `pair_acc 16/148`) 높다. `pair_acc`는 취약·정상 쌍을 둘 다 맞춘 수이고, 이 지표에서 최고 CWE 분담이 최고 연역/귀납/귀추의 약 3배다.

## 관측 (담아둘 것)

- **debate(연역→귀납→귀추)의 효과가 얕다.** 만장일치가 아니면 `need_evidence`로 빠지는 형식이라, 정확도는 34%→43%로 오르지만 판정 건수가 70→42로 떨어진다. 세 갈래의 다양성이 크지 않아 debate가 뚜렷한 이득을 내지 못했다.
- **CWE 분담 Agent는 자기 범위를 지킨다.** 세 Agent 모두 담당 밖 CWE를 **한 번도 응답하지 않았다.** 담당 CWE에서 Recall이 높고 담당 밖에서 급락한다 — 계열 분담이 의도대로 작동한다.
- ⚠ **source→sink 경로 정확도는 낮다(약 37%).** Juliet은 함수가 1-2개라 「취약 함수 정확」이 높게 나오므로 경로 정확도로 봐야 한다. `boundary` Agent의 오탐이 특히 크다 — 「담당 아님」과 「안전함」을 잘 못 가른다. **구조는 갈렸지만 근거 품질은 별개로 남는다.**

## 결론

**Scout 풀을 CWE 계열 세 Agent(`scout_memory`·`scout_boundary`·`scout_injection`)로 두는 [ADR 0010](../decisions/0010-harness-structure-and-model-roster.md) §3의 구조가 실측으로 확증됐다.** 추론 유형 분담은 문헌 근거도(§1) 성능도(위) 없어 채택하지 않는다.

⚠ **확증된 것은 「구조」다.** 이 실험이 쓴 CWE→Agent 세부 목록은 로스터·프롬프트의 목록과 다르다(`injection`이 CWE-22 대신 CWE-89, `boundary`에 170·191·369 추가, `memory`에 401·476 추가). 어느 목록을 정본으로 할지는 [D74](../open-questions/D/D74.md)로 남긴다 — 오늘은 로스터 목록을 유지한다. 그리고 어느 **모델**이 어느 자리에 앉는지는 이 실험이 정하지 않는다 — 그것은 ADR 0010 §3이 적은 대로 첫 run의 공동오답 행렬이 정한다.
