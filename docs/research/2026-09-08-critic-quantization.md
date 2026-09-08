# Critic 자리를 키우면 — judge 실측과 대형 MoE 양자화

**조사일 2026-09-08.** 라벨: `[출처주장]` · `[제3자]` · `[계산]` · `[미확인]`.
**우리 장비 실측은 [experiments/](../experiments/README.md)에 있다.**

**조사 도구 제약.** firecrawl·exa MCP가 없어 **WebSearch·WebFetch로 대신했고**, 검색 예산
소진 뒤에는 arXiv API와 직접 URL fetch로 진행했다. **PDF 본문 추출이 여러 번 실패했다** —
그 논문들은 abstract까지만 확인했고 §8에 적었다.

---

# A. Critic 모델 — 실측이 있는 후보가 하나뿐이다

## A1. judge 벤치마크에 실제로 있는 수치

[Hard2Verify](https://arxiv.org/abs/2510.13744)(Salesforce AI Research, 2025-10) `[제3자]`가
**후보군을 가로지르며 TPR·TNR을 분리 보고하는 유일한 벤치마크**다. 최근 수학 경시 80문항,
1,860 step, 인간 500시간 이상 주석. step-level Balanced F1:

| 모델 | Balanced F1 | TPR | TNR |
| --- | --- | --- | --- |
| GPT-5 | 85.83 | — | — |
| **gpt-oss-120B** | **74.64** | 94.54 | **61.67** |
| **gpt-oss-20B** | **70.93** | 93.06 | **57.31** |
| Qwen3-235B-A22B | 64.03 | — | — |
| DeepSeek-R1 | 62.30 | — | — |
| Qwen3-30B-A3B | 61.91 | — | — |
| **Qwen3-Next-80B-A3B** | **54.69** | 97.87 | **37.95** |
| Qwen3-32B | 51.77 | — | — |
| **GLM-4.5-Air** | **29.40** | 97.50 | **17.31** |
| Llama-3.3-70B-Instruct | 18.37 | — | — |

[ProfBench](https://arxiv.org/html/2510.18941v2) judge macro-F1 `[제3자, 단 NVIDIA가
gpt-oss-120b를 자사 벤치마크 채점자로 채택한 이해관계가 있다]`: gpt-oss-120b(high) **78.4** ·
**gpt-oss-20b(high) 77.9** · Kimi-K2 77.6 · Qwen3-235B-Instruct 77.3 · DeepSeek-V3.1 75.2 ·
**Nemotron-Ultra-253b 69.6**(후보군 최하위권).

[AgentJudgeBench](https://arxiv.org/html/2608.26623) `[제3자]`, 3,808 인스턴스:
gpt-oss-120B **84.8~96.0%**(정답 있음) · **69.7~84.5%**(정답 없음).

### ★ 커버리지 공백이 이 절의 진짜 답이다

arXiv 전수 질의 결과 `[제3자]`: **MiniMax-M2 · GLM-4.6 · Llama-4-Scout를 judge·critic·verifier로
평가한 논문이 사실상 없다.** 35편 중 이들을 evaluator로 쓴 것이 3편이고 judge 능력 자체를
재는 것은 하나다. 나머지는 전부 generator로 다룬다.

**그리고 Hard2Verify에 있는 GLM은 4.5-Air이지 4.6이 아니다**(벤치마크가 2025-10이다).

> **100B+로 올릴 때 judge 수치를 근거로 고를 수 있는 후보는 `gpt-oss-120b`뿐이다.**
> 나머지는 "재본 적이 없다"이지 "좋다·나쁘다"가 아니다. **미실측을 좋은 값으로 채우지 않는다.**

## A2. 20B → 120B로 키우면 실제로 얼마나 오르나

**독립적인 세 데이터포인트가 같은 방향이고, 셋 다 작다.**

| 출처 | 20B | 120B | 차이 `[계산]` |
| --- | --- | --- | --- |
| Hard2Verify (step F1) | 70.93 | 74.64 | **+3.71** |
| Hard2Verify (TNR) | 57.31 | 61.67 | **+4.36** |
| ProfBench (macro-F1) | 77.9 | 78.4 | **+0.5** |
| AgentJudgeBench (정답 없음·hard) | 69.0~84.0 | 69.7~84.5 | **거의 없음** |

세 출처가 전부 **plateau를 명시한다.**

- ProfBench `[출처주장]`: *"larger models generally perform better … but the improvement
  **plateaus after a certain size**."* 근거 — GPT-4.1-nano→mini +20.8%, mini→4.1 **+0.5%**.
- AgentJudgeBench `[제3자]`: 정답 없는 hard 조건에서 **여섯 judge가 77~82% 좁은 밴드로 수렴**하고,
  judge 등급 격차와 일치도가 **음의 상관(Spearman ρ = −0.825, p<0.01)**이다.

> ### ⚠ **우리가 실제로 서 있는 칸이 정확히 그 "정답 없는 hard"다.**
> Auditor가 낸 finding에는 정답 레이블이 없다.

**반대 방향 둘도 있다** — [Self-Preference Bias](https://arxiv.org/abs/2604.22891) `[제3자, 20개 모델]`:
*"advanced capabilities are often uncorrelated, or even **negatively correlated**, with low SPB."*
[When the Judge Changes](https://arxiv.org/abs/2607.08535) `[제3자]`: judge 업그레이드는
**더 나은 판정이 아니라 다른 판정**을 낸다 — *"verdict changes occur even when the candidate
responses stay fixed, simply because the evaluator has changed."*

## A3. 크기보다 3배 큰 지렛대가 이미 20B에 붙어 있다

Hard2Verify의 reasoning-effort ablation `[제3자]`:

| 모델 | low | high | 차이 `[계산]` |
| --- | --- | --- | --- |
| gpt-oss-120B | 61.46 | 74.64 | **+13.18** |
| gpt-oss-20B | 59.69 | 70.93 | **+11.24** |

> **모델 크기 효과(+3.71)의 약 3배다.** 그리고 **병렬 샘플링(best-of-N)으로는 거의 안 나온다** —
> 순차적 추론이 이긴다. 한 번에 한 단계만 도는 우리 구조에서는 이쪽이 더 싼 손잡이다.

⚠ 다만 과제별로 갈린다. AgentJudgeBench에서 CoT 기여는 **+0.11pp**, ProfBench에서
low→high는 **+1.1**이고 Finance 도메인은 **low가 더 높다**.

**이전 조사(2026-09-04)의 "reasoning이 Critic에 유리한지 단정 불가"를 과제별로 쪼갠다:**

| 과제 | 판정 |
| --- | --- |
| **step-level 오류 국소화** (우리 Critic이 하는 일에 가장 가깝다) | **이득이 크다.** gpt-oss 두 크기에서 +11~13 F1으로 재현됐다 |
| rubric 채점 · agentic 정렬 | 이득 +0.1~+1.1pp. **확립되지 않았다** |

⚠ **20B 이상 규모의 통제된 thinking ON/OFF ablation은 여전히 없다.** Hard2Verify의 low/high는
reasoning *effort*이지 thinking 유무가 아니다. 통제 ablation이 있는
[논문](https://arxiv.org/html/2509.13332v2)은 **Qwen3 0.6B·1.7B·4B만** 쓴다.

## A4. "합격률 100%는 합격이 아니라 고장" — 후보 목록 안에서 실측됐다

**TPR·TNR 분해가 정확히 그 진단 도구다.**

| 모델 | TPR | TNR | 읽는 법 |
| --- | --- | --- | --- |
| **GLM-4.5-Air** | 97.50 | **17.31** | 거의 전부 통과시키고 **오류의 17%만 잡는다** |
| **Qwen3-Next-80B-A3B** | 97.87 | **37.95** | 같은 병리, 정도가 덜하다 |
| gpt-oss-120B | 94.54 | **61.67** | TPR을 조금 내주고 TNR을 크게 산다 |

**반대 방향 고장도 실측돼 있다.** [Beyond the Answer Key](https://arxiv.org/abs/2608.28725)
`[제3자, gpt-oss-20B 포함]`: 정답이지만 **비표준 형태로 변형된** 풀이에 대해 base 모델들의
**false-rejection rate가 75.6~85.3%**다. 저자 표현 — *"strong sensitivity to canonical
solution form."*

> **Critic은 양방향으로 고장난다.** 과승인(TNR 17.31)과 과기각(75.6~85.3%) 둘 다 실측됐고,
> **단일 합격률로는 둘 다 안 보인다.**

[RewardBench 2](https://arxiv.org/html/2506.01937v1) `[제3자]`가 기본 실패 모드를 적는다 —
*"generative models as judges typically lack granularity in their judgments, and tend to
produce the same rating for multiple candidates."*

## A5. Auditor를 키우면 Critic이 무너진다

**[Variation in Verification](https://arxiv.org/html/2509.17995v2)**(ICLR 2026) `[제3자]`,
15개 모델 · 4,444 문제:

> **검증자의 TNR이 Llama-3.1-8B 풀이에 0.68 → Qwen3-32B 풀이에 0.17로 붕괴한다** `[출처주장]`.

기전: 강한 generator는 *"internally consistent reasoning chains where early mistakes propagate
coherently"*를 만든다. 약한 generator는 자기모순 같은 표면 오류를 낸다.

그리고 난이도별로 **hard 구간에서는 검증 정확도가 무작위 이하**로 떨어진다.

> ### ⚠ **Critic을 키우는 것과 Auditor를 키우는 것이 서로를 상쇄한다.**
> Critic 규모만 올리는 결정은 이 효과를 고려하지 않은 것이다.

**[JETTS](https://arxiv.org/abs/2504.15253)** `[제3자, judge 10종 × generator 8종, 코드 벤치 포함]`
— **critique 설정에서 일률적 음수**다: *"No judge yields a beneficial performance on refinement
across task category."* instruction following에서 **최대 10% 저하**.

⚠ **중요한 구분**: JETTS의 음수는 **judge의 자연어 비평으로 generator를 고치게 하는** 설정이다.
**우리 Critic은 게이트(수용·기각 + 축 지명)이지 generator를 유도하지 않는다.**
JETTS의 **reranking** 결과가 우리 쪽에 가깝고, 거기서는 judge가 ORM과 대등하다.

## A6. 검증이 생성보다 쉬운가 — 보안 도메인 증거 하나

[PentestJudge](https://arxiv.org/abs/2508.02921) `[제3자]` — **보안 도메인의 유일한 직접 증거**:
*"weaker and cheaper models can judge the trajectories of pentests performed by stronger and
more expensive models, suggesting **verification may be easier than generation**."* 최고 judge
**F1 0.83**. 단 요구사항 유형별로 층화하면 **총점이 비슷한 모델도 서로 다른 유형에서 무너진다.**

**그러나 벤더 다양화 전제에는 지지 증거가 없다.** arXiv 전수 질의 결과 —
**보안 리뷰 파이프라인에서 벤더 간 오류 상관을 측정한 논문을 찾지 못했다.** 취약점 검증
논문 10편 중 6편이 2차 LLM 검증을 쓰지만 **단일 대 이중을 정면 비교한 것이 하나도 없다.**

간접 증거는 오히려 반대 방향이다 — [Artificial Hivemind](https://arxiv.org/html/2510.22954v1)
`[제3자]`: 서로 다른 벤더 모델 간 응답 **평균 pairwise similarity 0.71~0.82**, 상위 50개
유사 응답이 **25개 모델 중 약 8개에서만** 나온다.

⚠ **§2.2 주의**: 이건 **개방형 생성의 출력 유사도**이지 **검증 과제의 오류 상관이 아니다.**
"다른 벤더 Critic이 오류를 탈상관시키지 못한다"고 말하지 **않는다.** 다만 **그 전제가
자동으로 성립하지는 않는다**는 것까지는 말한다.

---

# B. 양자화 손상 — 대형 MoE 규모

## B1. MoE가 dense보다 민감한가 — 조건 일치 비교가 거의 없다

**유일한 직접 비교는 반대를 말한다.** [MoQE](https://arxiv.org/abs/2310.02410) `[제3자, 2023]`:
*"expert layers in MoE models are **much more robust** to the quantization than conventional
feedforward layers."*

> ⚠ **2023년, 기계번역, encoder-decoder다.** 현대 decoder-only MoE LLM으로 일반화된다는
> 근거를 논문이 제시하지 않는다. **이 결과에 기대면 안 된다.**

**기전 셋 — 그중 하나는 통설과 충돌한다.**

**(a) expert별 캘리브레이션 희소성 — 우리 가설이 맞다.**
[MoEQuant](https://arxiv.org/abs/2505.03804) `[출처주장]`: *"uneven distribution of samples
across experts, which leads to insufficient and biased calibration for less frequently
utilized experts."* 효과 크기가 크다 — **DeepSeekMoE-16B 4비트에서 HumanEval 10점 이상** 회복.
**코드 과제에서 두 자릿수라는 점을 주목한다.**
⚠ **"expert 하나가 캘리브레이션 토큰의 몇 %를 보는가"의 수치는 찾지 못했다** `[미확인]`.

**(b) router logit 민감도 — 실측 둘이 어긋난다.**

| 출처 | 설정 | routing이 손상에서 차지하는 몫 |
| --- | --- | --- |
| [2608.26612](https://arxiv.org/abs/2608.26612) `[제3자]` | **가중치 INT4** | 토큰당 8개 중 0.53개 expert가 바뀌지만 **품질 손실의 2.7%만** 재현 |
| [2608.11212](https://arxiv.org/abs/2608.11212) `[제3자]` | **KV-cache 4비트** | 손상의 **약 1/3이 routing 매개** |

**모순으로 보고한다.** 설정이 달라 양립 가능하지만 **어느 쪽도 서로를 조정하지 않는다.**
실무 함의 — **가중치 4비트에서는 router 재보정이 주된 지렛대가 아닐 수 있다.**

**(c) MLP가 FP4 민감도를 지배한다 → expert가 곧 MLP다.**
[2603.08747](https://arxiv.org/abs/2603.08747) `[제3자, dense Qwen2.5]`: *"MLP up- and
down-projection layers **consistently dominate** in terms of sensitivity."* 그리고
*"early blocks can be highly sensitive, particularly under MXFP4."*
⚠ **MoE 논문이 아니다.** MoE의 routed expert가 up/down projection MLP이므로 **이어진다면**
expert가 정확히 가장 민감한 부위가 되지만, **이건 우리 추론이지 논문 주장이 아니다** `[미확인]`.

## B2. 총 파라미터가 커지면 손상이 커지나

**답이 "무엇을 양자화하느냐"에 따라 갈린다.**

**weight-only(W4A16: AWQ·GPTQ·INT4) — 클수록 안전하다.**
[MoEXBench](https://arxiv.org/html/2608.21693v1) `[제3자, MoE 10종 30B~235B, Q4_K_M]`:
Qwen3-30B-A3B **99.91% 유지**, Qwen3.6-35B-A3B **99.26%**.

**weight+activation(W4A4: NVFP4·MXFP4) — 근거가 없다.**
⚠ *"10B 초과 모델에서 activation 양자화 열화가 더 크다"*는 통설의 **원천을 특정하지 못했다** —
지목된 논문 둘을 열어 보니 **그 주장이 없었다.** `[미확인]` **인용하지 않는다.**

**그리고 pruning을 겸하면 큰 쪽이 확실히 나쁘다.** MoEXBench `[제3자]`: Prune+Quant에서
Qwen3-235B-A22B **PPL +54%** 대 6.71배 작은 Qwen3.6-35B-A3B **+31%**.
저자 인용 — *"Larger expert pools do not necessarily imply greater pruning tolerance."*

> ### ★ 그리고 **평균이 실패를 가린다.**
> 같은 조사에서 Prune+Quant가 `Score_perf` **96.35**를 유지하면서 `Score_robust`는
> **88.91**로 떨어진다. **MMLU와 HumanEval이 가장 크게 빠지고 GSM8K·RULER은 안정적이다 —
> 코드가 먼저 무너진다.**

## B3. NVFP4 · MXFP4 · W4A16 서열

**NVIDIA 자체 주장** `[출처주장]`, DeepSeek-R1-0528, **FP8 → NVFP4**:
MMLU-PRO 85→84 · GPQA Diamond 81→80 · LiveCodeBench 77→76 · MATH-500 98→98 · AIME 2024 89→**91**.

> **두 가지를 반드시 붙여 읽는다.**
> ① **기준선이 FP8이지 BF16이 아니다.** BF16 대비 누적 손실은 이 표에 없다.
> ② **AIME 2024는 30문항이다.** 89% → 91%는 `[계산]` **26.7 → 27.3문제, 한 문제 미만**이다.
> "양자화가 성능을 올렸다"가 아니라 **측정 분해능 아래**다. 나머지 1점 차들도 마찬가지일 수 있다.

**제3자 재현은 NVIDIA 주장과 어긋난다.**
[Egiazarian et al.](https://arxiv.org/abs/2509.23202) `[제3자 — ISTA·ETH Zürich·Red Hat AI,
Alistarh·Hoefler 포함]` abstract 직접 인용:

> *"(1) NVFP4's small group size **provably neutralizes traditional outlier mitigation
> techniques**; (2) MXFP4's power-of-two scale quantization **severely degrades accuracy**."*
> *"**FP4 is not an automatic upgrade over INT4**"*

[Knoop & Holtmann](https://arxiv.org/abs/2601.09527) `[제3자, RTX 5090 등 sm_120,
GPT-OSS-20B 포함, 79개 구성]`: *"NVFP4 … **only 2-4% quality loss**"*(BF16 기준).

> ### ⚠ 모순 — 판정하지 않는다
> **NVIDIA ≤1%(FP8 기준, DeepSeek-R1) 대 Knoop & Holtmann 2~4%(BF16 기준, 8~27B).**
> 기준선과 규모가 다르므로 양립 가능하지만 **두 숫자를 같은 문장에 놓으면 안 된다.**
> **우리 규모(120B MoE)에 대한 제3자 측정은 어느 쪽도 아니다.**

**MXFP4는 소지 상태로 NVFP4보다 약 10% 뒤진다** `[출처주장]`
([2603.08713](https://arxiv.org/abs/2603.08713)이 완화 기법으로 *"about 10% to below 1%"*로
줄인다고 보고). [2601.09555](https://arxiv.org/abs/2601.09555) `[제3자]`:
*"**MXFP4 introduces substantial accuracy degradation and remains challenging**."*

## B4. gpt-oss의 네이티브 MXFP4 — 비교할 기준선이 없다

[모델 카드](https://huggingface.co/openai/gpt-oss-120b) 직접 인용 `[출처주장]`:

> *"The models were **post-trained with MXFP4 quantization** of the MoE weights"*
> *"**All evals were performed with the same MXFP4 quantization**"*

**두 번째 문장이 결정적이다. OpenAI가 발표한 벤치마크 수치가 곧 MXFP4 수치이고,
비교 대상이 될 bf16 체크포인트는 공개되지 않았다.**

⚠ **모호성을 남긴다(§2.2).** *"post-trained with MXFP4 quantization"*은 (a) post-training
단계에서 MXFP4 적용(QAT 성격)과 (b) post-training 이후 양자화(PTQ) 둘로 읽힌다.
**모델 카드만으로 확정할 수 없다.** 이 구분이 "upcast하면 원래 모델이 나오는가"에 직결된다.

> ### ★ **우리에게 이것이 뜻하는 것**
> **gpt-oss-120b를 쓰기로 하면 「양자화가 얼마나 깎았나」는 이 저장소에서 영원히
> `checksum: 0`이다** — 델타를 계산할 분모가 없다. bf16 upcast는 약 240 GB로
> 119.7 GiB에 안 올라간다.
>
> 이것은 **결함이 아니라 그 모델을 고르는 대가**이고, 우리 [model-serving.md](../model-serving.md) §4가
> 요구하는 "고정밀 대비 NVFP4 반박 정확도 비교"가 **그 후보에서는 성립하지 않는다는 뜻**이다.
> §4의 기준을 **반박 정확도 표본 자체**로 재정의해야 한다.

## B5. 그리고 아무도 4비트 Critic을 재지 않았다

arXiv 전수 질의 결과 `[제3자]`:

> **모델을 양자화한 뒤 그 모델의 judging 능력을 측정한 연구를 찾지 못했다.**

가장 가까운 [2608.31108](https://arxiv.org/abs/2608.31108) `[제3자]`이 evaluator 자체를
양자화했고 결론이 경고다 — *"INT8 largely preserves quality"*, **"INT4 causes larger,
model- and context-dependent changes."**

**우리는 INT4가 아니라 그보다 공격적인 W4A4를 검토하고 있다.**

> **§A1의 judge 순위표는 전부 고정밀 서빙 기준이다.** 4비트에서 그 서열이 유지된다는
> 근거가 없다. **우리가 그 첫 측정을 하는 셈이다.**

---

# C. 구조화 출력 — 이름이 바뀌었다

**vLLM 0.27.x 실제 현황** `[출처주장, 공식 문서 직독]`:

1. **`guided_json`과 `guided_decoding_backend`는 v0.12.0에서 제거됐다.** 0.27.1에 없다.
   `guided_json` → `{"structured_outputs": {"json": ...}}`, 서빙 플래그는
   `--structured-outputs-config.backend`.
2. **backend**: xgrammar · guidance(llguidance). 기본값은 **`"auto"`**. outlines는 1급 backend가 아니다.
3. **reasoning 모델**: v0.11.2+ 에서 reasoning content가 별도 파싱되지 않으면 구조화 출력이
   **비활성화될 수 있고**, `--structured-outputs-config.enable_in_reasoning=True`가 필요하다.
4. **미지원 JSON Schema 기능 목록과 fallback 동작은 공식 문서에 없다** `[미확인]`.
5. **gpt-oss·Harmony에 대한 언급이 구조화 출력 문서에 전혀 없다.**

> **§B4와 이어 읽는다.** vLLM #37030은 SM121에서 **Harmony 채널 토큰 자체가 커널 레벨에서
> 잘못 샘플링**되는 문제다. **grammar보다 상류이므로 backend를 바꿔서 해결되는 종류가 아니다.**

**스키마 강제가 추론을 깎는가** — 조건부다. [Capacity, Not Format](https://arxiv.org/abs/2606.09410)
`[제3자]`: Haiku **−36.2pp**, GPT-4o-mini **−28.0pp**, Opus 4.7 **−5.3pp**.
**정보량을 맞춘 산문 대조군**으로 프롬프트 길이 교란을 분리했다.
⚠ **평가 대상이 전부 상용 모델이고 open-weight가 하나도 없다.**

완화책은 여러 출처가 같은 방향이다 — **"먼저 추론하고 나중에 형식화한다."** 지연 구조화가
손실의 **80~87%를 회복**하고, [스키마에 추론 필드를 앞에 두면 +15~24점](https://arxiv.org/abs/2608.08254)
`[출처주장, 단독저자·미심사]`이다.

⚠ 그리고 **문법 강제가 값하는 자리도 분명하다** — [The Calibration Floor](https://arxiv.org/abs/2608.04355)
`[제3자]`: grammar-constrained decoding이 형식 실패 격차의 **중앙값 71%를 닫는다.**

---

# D. sm_121 커널 — 조사 중 가장 실무적인 발견

**질문에 적힌 환경변수 이름 대부분이 0.27.x에 이미 없다.** v0.27.1 소스 직독 `[출처주장]`:

NVFP4 자동 선택 순서는 `FLASHINFER_TRTLLM` → `FLASHINFER_CUTEDSL` → `FLASHINFER_CUTLASS` →
`VLLM_CUTLASS` → `MARLIN` → `HUMMING` → `EMULATION`이고 제어는 **`--moe-backend` 플래그**다.

소스 주석에 sm_121이 명시적으로 박혀 있다:
*"FLASHINFER_B12X is intentionally excluded from auto-selection until the **upstream CUTLASS
SM121 MMA op guard is resolved**."*

### ★ MXFP4는 sm_12x에 CUTLASS 커널이 **아예 없다**

`CMakeLists.txt` 직독 `[출처주장]`: `mxfp4_experts_quant.cu`와 `mxfp4_blockwise_moe_kernel.cu`가
**`FP4_SM100_SRCS`에만** 있고 `FP4_SM120_SRCS`에는 없다.

> **Marlin 폴백은 버그가 아니라 현재 설계의 귀결이다.**

### CUDA 13에서 `12.1`이 조용히 걸러진다

`CMakeLists.txt` `[출처주장]`: CUDA ≥13 분기의 `CUDA_SUPPORTED_ARCHS`에 **`12.1`이 없다.**
`TORCH_CUDA_ARCH_LIST=12.1`을 넘겨도 필터링되고 `12.0f`(family cubin)로 커버한다.

> **어느 CUDA로 빌드했는지가 커널을 바꾼다 — run 기록에 반드시 남긴다.**

### 시간이 지나야 터지는 것들

| 이슈 | 내용 | 상태 |
| --- | --- | --- |
| [#49926](https://github.com/vllm-project/vllm/issues/49926) | GB10 Marlin NVFP4, **1.5~2.5일 연속 서빙 후** 결정론적 null-pointer GPU fault | Open |
| [#52225](https://github.com/vllm-project/vllm/issues/52225) | **v0.27.1 정확히 해당.** sm_120 Xid 13 warp error, **수 시간 부하 후** | Open |
| [#54666](https://github.com/vllm-project/vllm/issues/54666) | sm_121 Marlin 자동선택이 EngineDeadError. `--moe-backend b12x`는 18.5M 토큰 무사고 | Open |
| [#37431](https://github.com/vllm-project/vllm/issues/37431) | SM121 Mamba-2 Triton illegal instruction. 우회 시 처리량 **−37%** | Open |
| [flashinfer#3294](https://github.com/flashinfer-ai/flashinfer/issues/3294) | cubin 12,681개 중 **sm_120/121 대상 0개**. 8×DGX Spark에서 **에러 없이** `"あるいあるいあるい…"` 반복 | Closed |

> ### ⚠ **crash가 아니라 garbage인 사례가 둘이다** — gpt-oss Harmony 첫 토큰 오류와
> cubin 불일치 반복 출력. **그리고 #49926·#52225는 수 시간~수 일 뒤에 터진다.**
> **짧은 스모크 테스트로는 둘 다 안 잡힌다.**

**우리 상황에 직결되는 별건** `[제3자]`: 통합 메모리 시스템에서 **`RAY_memory_monitor_refresh_ms=0`**가
필요하다 — Ray의 memory monitor가 서빙 프로세스를 죽일 수 있다. 그리고
`--gpu-memory-utilization` **0.90 초과 금지** 권고.

---

# E. 모순 목록 — 판정하지 않고 남긴다

| # | 모순 | 양쪽 |
| --- | --- | --- |
| 1 | NVFP4 열화 크기 | NVIDIA ≤1%(FP8 기준) 대 Knoop & Holtmann 2~4%(BF16 기준) |
| 2 | NVFP4가 추론을 깎는가 | [ReSET](https://arxiv.org/abs/2606.13233) *"degrades"* 대 [2609.04098](https://arxiv.org/abs/2609.04098) *"−0.52"*(모델 다름, 후자는 소속 미표기) |
| 3 | sm_121에서 Marlin | #50925·#54666·#49926 "broken" 대 Conselara·NVIDIA 포럼 "working" |
| 4 | `120f` family cubin 충분성 | FlashInfer #3170 "충분" 대 vLLM PR #38484 리뷰 *"functional, not just performance, differences"* |
| 5 | MoE 손상에서 routing의 몫 | 2.7% 대 약 31% (가중치 대 KV-cache) |
| 6 | thinking이 judge를 개선하는가 | Hard2Verify +11~13 F1 대 AgentJudgeBench +0.11pp / ProfBench +1.1 |
| 7 | MoE 대 dense 양자화 민감도 | MoQE "MoE가 강건"(2023 MT) 대 현대 MoE-PTQ 논문들의 암묵 전제 |
| 8 | 스키마 강제 효과 | −5~36pp 대 [2607.07026](https://arxiv.org/abs/2607.07026) +7.6~+46.7pp |

---

# F. 평결 — Critic 자리를 키우면

## 좋아지는 것

1. **오류 탐지율이 오른다 — 그러나 TNR +4.36점, F1 +3.71점이다.** 우리 과제와 가장 가까운
   step-level 오류 국소화에서, 같은 모델 계열로 잰 유일한 값이다.
2. **채점 편향이 낮은 것으로 측정된 유일한 후보다** — ProfBench bias-index 0.3(비교 대상 최저).
3. **reasoning effort를 올릴 여지가 생긴다 — 그런데 이건 20B에도 이미 붙어 있다.**

## 위험해지는 것

1. **후보 대부분이 미측정이고, 측정된 것 중 둘은 고장나 있다** — GLM-4.5-Air TNR **17.31**,
   Qwen3-Next-80B TNR **37.95**. **팀이 우려한 "합격률 100%짜리 고장난 judge"가
   후보 목록 안에서 실측됐다.**
2. **우리가 실제로 서 있는 칸에서 크기 효과가 사라진다** — 정답 없는 hard에서 여섯 judge가
   77~82%로 수렴하고 등급 격차와 일치도가 음의 상관이다.
3. **Auditor를 키우면 Critic의 TNR이 0.68 → 0.17로 무너진다.**
4. **4비트 Critic의 판정 능력은 문헌에 없다.**
5. **커널이 조용히 틀린 답을 내고, 그것이 수 시간~수 일 뒤에 나타난다.**
6. **120B MXFP4에는 비교할 고정밀 기준선이 없다.**

## 그래서 권고는 "키워라·키우지 마라"가 아니다

> **크기를 바꾸기 전에, 지금 구성에서 Auditor가 낸 finding에 대한 Critic의 TPR과 TNR을
> 분리해서 잰다.** Hard2Verify가 GLM-4.5-Air의 고장을 드러낸 방식이 정확히 그것이고,
> 그 측정 없이는 120B가 **개선**인지 **다른 판정**인지 구분할 방법이 없다.
> **합격률 하나로는 두 방향의 고장이 둘 다 안 보인다.**

**이 평결이 걸려 있는 미확인 셋** — ① Hard2Verify는 **수학**이고 코드·보안 도메인에서
TPR/TNR을 분리 보고하는 step-level 벤치마크가 **존재하지 않는다**, ② **4비트 Critic의
판정 능력은 문헌에 없다**, ③ **"다른 벤더면 오류가 탈상관된다"에 지지 증거가 없다.**

# G. 확인하지 못한 것

- expert 하나가 캘리브레이션 토큰의 몇 %를 보는지 **수치**.
- *"10B 초과에서 activation 양자화가 더 나쁘다"*의 **원천** — 지목된 논문 둘에 그 주장이 없었다.
- MoEXBench의 **양자화 단독 델타**(절대 PPL만 확인했다).
- gpt-oss 카드 *"post-trained with MXFP4"*의 **QAT/PTQ 여부**.
- vLLM 0.27.x의 **미지원 JSON Schema 기능 목록**과 fallback 동작.
- **100B+ MoE를 judge로 쓴 양자화 조건의 측정** — **문헌에 존재하지 않는 것으로 보인다.**
- PDF 추출 실패로 본문 표를 대조하지 못한 논문 넷 — abstract 수준까지만 확인했다.
- **에이전트를 겨냥한 지시문은 열람한 페이지에서 발견되지 않았다.**
