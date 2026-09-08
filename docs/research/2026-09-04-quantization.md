# 양자화가 Critic의 판단 능력에 미치는 영향 — 웹 리서치

조사일: 2026-09-04 · 대상 장비: NVIDIA GB10 (Blackwell sm_121), 통합메모리 119.7 GiB, 대역폭 273 GB/s
조사 도구: WebSearch / WebFetch. 아래 수치는 **전부 외부 출처의 측정치**이며, 우리 장비·우리 과제로 잰 값은 **하나도 없다**(§8).

## 표기 규약

| 표기 | 뜻 |
| --- | --- |
| 🟩 **제3자** | 벤더가 아닌 곳의 측정 (학계·독립 연구) |
| 🟨 **도구 벤더** | 양자화 툴체인 제공자(Red Hat/llm-compressor 등)의 자체 측정 — NVIDIA는 아니나 중립도 아님 |
| ⚠ **벤더 주장** | NVIDIA 자체 발표 |
| 🧮 **계산값** | 내가 원문 수치에서 계산한 것. 원문에 없는 값 |
| ❓ **자료 없음** | 찾지 못함 |

---

# 1. 핵심 답 — "Critic 역할에 4비트 양자화를 써도 되는가"

## 1.1 근거가 지지하는 만큼의 답

> **"4비트"라는 한 단어로 물으면 답할 수 없다. 근거는 W4A16과 W4A4를 완전히 다른 물건으로 취급한다.**
>
> - **가중치만 4비트(W4A16, AWQ/GPTQ)** — 30B급 이상에서 **거의 무손실**. Critic에 써도 된다는 쪽으로 근거가 기운다.
> - **가중치+활성 4비트(W4A4 — RedHatAI/NVIDIA가 배포하는 NVFP4 체크포인트가 이것이다)** — 32B에서도 **추론 과제에 5~9% 상대 손실**이 실측된다. Critic에 기본값으로 쓰는 것은 근거가 **지지하지 않는다**.
>
> **그리고 Critic의 손실은 Scout의 손실보다 크게 나올 이유가 있다.** 아래 셋이 서로 다른 출처에서 같은 방향을 가리킨다.

**Critic에 특히 불리하다고 근거가 말하는 세 가지:**

1. **판단 실패는 "답을 몰라서"가 아니라 "답을 안 내놔서" 온다.** 양자화 모델 실패의 **최대 52%**가 중간 추론에서 정답에 도달하고도 최종 답으로 내놓지 못한 경우였다 ([2606.00206](https://arxiv.org/abs/2606.00206), 🟩). 이건 지식 손실이 아니라 **결정(commitment) 손실**이고, Critic이 하는 일이 정확히 결정이다.
2. **환각 탐지·지시 준수가 양자화의 최약점으로 반복 관측된다.** 1B~405B·13개 데이터셋·4개 방법을 돌린 IJCAI-25 서베이가 "양자화 모델은 여전히 **instruction-following(IFEval)과 hallucination detection(TruthfulQA)에서 고전한다**"를 5대 발견 중 하나로 꼽았다 ([2409.11055](https://arxiv.org/abs/2409.11055), 🟩). 반박·재검증은 hallucination detection과 같은 계열의 과제다.
3. **긴 입력에서 4비트 손실이 증폭된다.** 64K 토큰 초과 입력에서 8비트는 평균 −0.8%인데 **4비트는 최대 −59%** ([2505.20276](https://arxiv.org/abs/2505.20276), EMNLP 2025, 🟩). Critic은 코드 + Scout 후보 + Auditor 근거를 한꺼번에 읽는 역할이라 이 구간에 정면으로 들어간다.

## 1.2 실무 판정 (역할별)

| 역할 | 권고 정밀도 | 근거 |
| --- | --- | --- |
| **Scout** (후보 식별, 고재현율) | **NVFP4(W4A4) 허용** | 후보 생성은 오탐을 뒤가 걸러준다. 4비트 손실이 재현율에 주는 타격이 파이프라인 끝단에 남는다는 근거를 못 찾음(❓) — 하지만 구조상 Critic보다 관대하다 |
| **Auditor** (재검증) | **fp8 권장** | 판정 계열 과제. §2·§5 |
| **Critic** (반박) | **fp8 우선. 4비트가 불가피하면 W4A16(AWQ) + 30B급 이상 + KV캐시 bf16** | §1.1의 셋 + §2.2 표 |

**"fp8 우선"의 근거 강도:** IJCAI-25가 1B~405B 전 구간에서 **"FP8이 모든 모델 크기·과제에서 가장 안정적"**이라고 결론했고([2409.11055](https://arxiv.org/abs/2409.11055), 🟩), COLM 2025가 32B에서 W8A8KV8 손실 **−0.05%**를 실측했다([2504.04823](https://arxiv.org/abs/2504.04823), 🟩). 두 독립 출처가 일치한다.

## 1.3 메모리가 4비트를 강제하는가 — 우리 장비 산수

🧮 **계산값** (가중치만·디코드 루프라인 상한. KV캐시·활성 제외이므로 실측은 반드시 이보다 낮다):

| 모델 크기 | bf16 | fp8 | 4비트 |
| --- | --- | --- | --- |
| 30B dense 가중치 | 60 GB → **4.6 tok/s** | 30 GB → **9.1 tok/s** | 15 GB → **18.2 tok/s** |
| 70B dense 가중치 | 140 GB → **적재 불가** (119.7 GiB) | 70 GB → **3.9 tok/s** | 35 GB → **7.8 tok/s** |

실측 참고(같은 장비군): Nemotron-3-Super-120B-A12B-NVFP4 디코드 **22.7~23.7 tok/s** ([vLLM 블로그](https://vllm.ai/blog/2026-06-01-vllm-dgx-spark), 🟨) · Qwen3.5-122B-A10B-NVFP4 디코드 **15.1 tok/s**, MTP 켜고 24.5 tok/s ([커뮤니티](https://github.com/bjk110/SPARK_Qwen3.5-122B-A10B-NVFP4), 🟩 n=1).

**결론:** 30B급이면 **fp8로 119.7 GiB 안에 들어간다.** 4비트는 우리 장비에서 *메모리 때문에* 강제되는 게 아니라 *속도 때문에* 선택하는 것이다. 즉 **Critic만 fp8로 두는 선택지가 실제로 존재한다** — 이게 이 조사의 가장 실용적인 결과다.

---

# 2. 수치표 — 정밀도 × 과제 유형별 손실

## 2.1 같은 계열·같은 레시피에서 dense vs MoE (NVFP4 = W4A4)

RedHatAI가 **동일 레시피(UltraChat 512샘플, lm_head만 제외)**로 만든 체크포인트. 🟨 도구 벤더 자체 측정.

| 과제 | Qwen3-32B **dense** BF16→NVFP4 | Qwen3-30B-A3B **MoE** BF16→NVFP4 | Qwen3-8B **dense** BF16→NVFP4 |
| --- | --- | --- | --- |
| OpenLLM V1 평균 | 74.32→74.20 (**99.83%**) | 73.25→71.92 (**98.19%**) | 71.59→70.43 (**98.38%**) |
| OpenLLM V2 평균 | 55.69→54.45 (**97.79%**) | 52.78→48.99 (**92.81%**) | 48.22→47.42 (98.33%) |
| **BBH** (다단계 추론) | 62.35→60.72 (**97.39%**) | 54.97→45.63 (**83.01%** ⚠) | 47.46→49.33 (103.94%) |
| **MMLU-Pro** | 54.39→51.13 (94.01%) | 47.39→42.56 (89.81%) | 34.64→27.49 (**79.36%** ⚠) |
| **IFEval** (지시 준수) | 88.97→87.29 (98.11%) | 88.61→86.93 (98.10%) | 87.89→87.65 (99.73%) |
| HumanEval pass@2 | 90.14→90.40 (100.29%) | 93.62→91.13 (97.34%) | 86.51→85.32 (98.62%) |
| **AIME24** | 75.86→68.97 (**90.93%**) | — | 75.86→62.07 (**81.82%**) |
| **AIME25** | 72.41→65.52 (**90.52%**) | — | 65.52→62.07 (94.74%) |
| GPQA Diamond | 62.94→64.47 (102.43%) | — | 59.90→54.82 (91.51%) |
| **추론 평균** | 70.40→66.32 (**94.21%**) | — | 67.09→59.65 (**89.36%**) |

출처: [Qwen3-32B-NVFP4](https://huggingface.co/RedHatAI/Qwen3-32B-NVFP4) · [Qwen3-30B-A3B-NVFP4](https://huggingface.co/RedHatAI/Qwen3-30B-A3B-NVFP4) · [Qwen3-8B-NVFP4](https://huggingface.co/RedHatAI/Qwen3-8B-NVFP4)

**읽는 법 주의 (§2.2 규율):**
- 표는 **"일반 지식은 거의 안 떨어지고, 추론·다단계 과제가 떨어진다"**까지만 말한다. OpenLLM V1 99.83% 옆에 추론 평균 94.21%가 있다. **평균 지표 하나로 판정하면 손실을 놓친다.**
- 8B의 BBH가 **올라간(103.94%)** 반면 MMLU-Pro는 **79.36%로 무너졌다.** 소형 모델에서 4비트 결과는 **과제별 분산이 크다** — 방향이 일정하지 않다. 한 벤치마크로 결론내면 안 된다.
- dense 32B와 MoE 30B-A3B의 비교는 **교란이 있다**: MoE는 활성 파라미터가 3B뿐이라 "MoE라서"인지 "실질 3B라서"인지 이 표만으로는 **가를 수 없다.** §4.2 참조.

## 2.2 정밀도별 손실 (추론 과제 중심)

| 정밀도 | 과제 유형 | 손실 | 출처 |
| --- | --- | --- | --- |
| **W8A8KV8 (fp8)** | 추론 5종 평균, 32B | **−0.05%** | [2504.04823](https://arxiv.org/abs/2504.04823) COLM'25 🟩 |
| **W8A8KV8 (fp8)** | 추론 5종 평균, QwQ-32B | **+0.18%** | 동일 🟩 |
| **W8A8KV8 (fp8)** | 추론 5종 평균, 14B | **−1.28%** | 동일 🟩 |
| **W4A16 (AWQ)** | 추론 5종 평균, 32B | **−0.82%** | 동일 🟩 |
| **W4A4KV4 (최고 기법 FlatQuant)** | 추론 5종 평균, 32B | **−2.33%** | 동일 🟩 |
| **W4A4KV4** | 추론, 1.5B·7B | **−10% 초과** | 동일 🟩 |
| **W3G128** | AIME-120, QwQ-32B (RL 학습) | **−14.98%** | 동일 🟩 |
| **W3G128** | AIME-120, Qwen-32B (증류) | −9.98% | 동일 🟩 |
| **W4A4KV4** | AIME(어려움) vs GSM8K(쉬움), 32B | **−4.17% vs −0.00%** (약 4배 차) | 동일 🟩 |
| **8비트 전반** | 긴 입력(>64K) | **평균 −0.8%** | [2505.20276](https://arxiv.org/abs/2505.20276) EMNLP'25 🟩 |
| **4비트 전반** | 긴 입력(>64K) | **최대 −59%** | 동일 🟩 |
| AWQ-int4 / GPTQ-int4 | 긴 입력 평균 | −1~3% | 동일 🟩 |
| BNB-nf4 | 긴 입력 평균 | −6.9% | 동일 🟩 |
| 4비트, 모델 크기 추세 | 긴 입력 | Qwen2.5 7B **−6.8%** / 32B −2% / 72B **+0.7%** | 동일 🟩 |
| 4비트, 모델 크기 추세 | 긴 입력 | Llama3.1 8B **−10%** / 70B −4.5% | 동일 🟩 |
| **GPTQ 4비트** | GSM8K, Llama-3.2-**1B** | **−25.32%** | [2409.11055](https://arxiv.org/abs/2409.11055) IJCAI'25 🟩 |
| **GPTQ 4비트** | IFEval, Llama-3.2-**1B** | **−16.01%** | 동일 🟩 |
| GPTQ 8비트 vs 4비트 | OpenLLM v1/v2, 8B | 8비트가 **+2.51% / +2.11%** 우위 | 동일 🟩 |
| GPTQ 8비트 vs 4비트 | 70B | **4비트가 +1.89% 우위** (역전) | 동일 🟩 |
| **W8A8-INT / W8A8-FP / W4A16** | OpenLLM v1 평균, Llama-3.1 8/70/405B | 전부 **>99% 회복** | [Red Hat 50만 평가](https://developers.redhat.com/articles/2024/10/17/we-ran-over-half-million-evaluations-quantized-llms) 🟨 |
| 8비트 / 4비트 | HumanEval | **99.9% / 98.9%** 회복 | 동일 🟨 |
| **fp8 KV캐시 + fp8 attention** | 추론 4종, Qwen3-30B-A3B-Thinking | 최대 **−1~2점**, 최저 회복 97% (GPQA-D) | [vLLM 블로그 2026-04](https://vllm.ai/blog/2026-04-22-fp8-kvcache) 🟨 |
| fp8 KV캐시 | 롱컨텍스트 AUC@128k, Llama-3.3-70B | **97~98%** 회복 | 동일 🟨 |
| fp8 KV캐시 | 롱컨텍스트 AUC@256k, Qwen3-30B-A3B | **94~98%** 회복 | 동일 🟨 |

---

# 3. NVFP4 구체

## 3.1 형식

16값 마이크로블록(MXFP4는 32) + 블록당 **E4M3 fp8 스케일** + 텐서당 **fp32** 2단 스케일. 값은 E2M1 (0, 0.5, 1, 1.5, 2, 3, 4, 6). Blackwell 5세대 텐서코어가 하드웨어로 처리. ([NVIDIA 블로그](https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/) ⚠)

## 3.2 ⚠ 벤더 주장

NVIDIA 블로그: DeepSeek-R1-0528을 **fp8 → NVFP4**로 낮췄을 때 "핵심 언어 모델링 과제에서 **1% 이하** 저하". MMLU-PRO 85→84, GPQA-D 81→80, HLE 15→14, LiveCodeBench 77→76, SciCode 40→40, Math-500 98→98, **AIME 2024 89→91(+2)**.

**이 주장을 그대로 옮기면 안 되는 이유 셋:**
1. **기준선이 BF16이 아니라 fp8이다.** 블로그에 BF16 대비 수치가 없다.
2. **671B 모델 하나다.** 우리가 GB10에 올릴 크기가 아니다.
3. **NVIDIA 자신의 다른 문서가 이 프레이밍을 반박한다** — §3.3.

## 3.3 ⚠→🟩 **NVIDIA 자체 문서가 인정하는 손실** (이게 이 절의 핵심)

NVIDIA 리서치의 기술 보고서 [*Quantization-Aware Distillation for NVFP4 Inference Accuracy Recovery*](https://research.nvidia.com/labs/nemotron/files/NVFP4-QAD-Report.pdf) (2026-03-05)는 본문에서 **직접** 이렇게 쓴다:

> *"For very large LLMs, NVFP4 with post-training quantization (PTQ) shows decent accuracy on different benchmarks. **However, for small LLMs, the accuracy drop from PTQ is often non-negligible.**"*
>
> *"PTQ works quite well for FP8 on most LLMs. For NVFP4, PTQ also works well for very large models. **However, PTQ often struggles with small models and sensitive tasks.**"*

그리고 자체 측정 수치를 낸다. **이 표가 마케팅 블로그의 "1% 이하"와 정면으로 다르다.**

**Table 11 — Nemotron 3 Nano 30B-A3B (MoE, hybrid Mamba-Transformer), BF16 → NVFP4 PTQ:**

| 벤치마크 | BF16 | NVFP4 PTQ | 차이 | 🧮 상대 |
| --- | --- | --- | --- | --- |
| **AA-LCR** (롱컨텍스트 추론) | 35.9 | 31.3 | **−4.6** | **−12.8%** |
| AIME25 | 89.1 | 85.0 | −4.1 | −4.6% |
| GPQA-D | 73.0 | 71.6 | −1.4 | −1.9% |
| LiveCodeBench-v5 | 72.1 | 68.9 | −3.2 | −4.4% |
| SciCode | 33.0 | 30.5 | −2.5 | −7.6% |

**주의:** 이 수치는 **attention 레이어와 그 앞 Mamba-2 레이어를 BF16으로 남기고 KV캐시는 fp8로** 둔 상태다(보고서 §3). 즉 **이미 보호 조치를 한 뒤의 손실**이다.

**Table 4 — AceReason Nemotron 1.1 7B (dense):** AIME24 73.0→69.4(−3.6), AIME25 63.5→58.7(−4.8), LiveCodeBench-v6 54.3→52.0(−2.3)

**Table 12 — 초대형 모델은 실제로 거의 무손실:**
- Llama Nemotron Ultra V1 (253B): MATH500 96.6→96.2, AIME24 75.0→**76.0**, GPQA-D 75.7→74.8
- DeepSeek R1 (671B, fp8→NVFP4): MATH500 95.4→94.2, AIME24 80.0→80.0, GPQA-D 69.7→69.2, GSM8K 96.3→96.1

> **정리:** NVFP4 PTQ가 "거의 무손실"인 것은 **수백 B급에서 사실이고, 7~30B급에서는 NVIDIA 자신도 아니라고 쓴다.** 우리가 GB10에 올릴 크기는 후자다.

## 3.4 🟩 제3자 검증

**(a) IST-DASLab 외, [*Bridging the Gap Between Promise and Performance for Microscaling FP4 Quantization*](https://arxiv.org/abs/2509.23202)** (Egiazarian, Kuznedelev, Panferov, Kurtic, Ashkboos, Hoefler, Alistarh 외)

초록 원문: *"**We conclude that, while FP4 is not an automatic upgrade over INT4**, format-specialized methods like MR-GPTQ can unlock a new frontier of accuracy-performance trade-offs."*

Llama-3.1-8B-Instruct W4A4 회복률:

| 방법 | MXFP4 | NVFP4 | INT4 |
| --- | --- | --- | --- |
| RTN | 69.32% | **74.73%** | — |
| GPTQ | 70.62% | **75.72%** | — |
| RTN + Hadamard | — | — | 74.75% (FP16 대비 94.71%) |
| MR-GPTQ (그들 기법) | 73.65% (93.31%) | **75.84% (95.92%)** | — |

- 본문: *"both NVFP4 and MXFP4 are **lossy**, with MXFP4 inducing major accuracy drops (∼10% relative)"*
- *"NVFP4 and INT4 quantization provide **similar quality**, with INT4 quantization having higher variance"*
- 실양자화(Table 2): NVFP4 회복 **95.7~99.1%**, MXFP4 **89.3~98.4%**
- **NVFP4의 작은 그룹 크기(16)가 전통적 아웃라이어 완화(회전)를 무력화한다**고 증명. 회전이 MXFP4엔 도움되고 **NVFP4엔 해가 된다**.
- **NVIDIA QAD 보고서가 이 논문을 인용해** 자기 PTQ 한계를 설명한다(Egiazarian et al., 2025).

**(b) HKU / ByteDance Seed, [*INT vs FP*](https://arxiv.org/html/2510.25602v1)** — Qwen3-8B, KL divergence(×10⁶, 낮을수록 좋음):

| | FP | INT |
| --- | --- | --- |
| MXFP4 vs MXINT4 (block 32) | **8,228** | 15,552 |
| NVFP4 vs NVINT4 (block 16) | **3,430** | 5,120 |
| MXFP8 vs MXINT8 | 362 | **168** ← INT 승 |
| Hadamard 적용 시 NVFP4 vs NVINT4 | 5,912 | **4,026** ← INT 승 |

결론: *"FP 형식이 coarse-grained에서 유리하지만, **블록 크기가 작아지면 INT가 매우 경쟁적이 된다**"*. MoE(Qwen3-30B-A3B / 235B-A22B) 포함 12개 모델에서 경향 일치.

**(c) 🟨 Red Hat 자체 측정** ([NVFP4 가속 문서](https://developers.redhat.com/articles/2026/02/04/accelerating-large-language-models-nvfp4-quantization)): 70B~235B **~99% 회복**, ~30B **97~99%**, 7B~14B **~95~98%**. 명시적 단서: *"작은 스케일에서는 과제·보정 전략에 따라 결과 편차가 크며, **일률적 레시피보다 모델별 튜닝이 중요**할 수 있다."*

## 3.5 세 출처가 **일치**하는 것

| 주장 | NVIDIA ⚠ | IST-DASLab 🟩 | Red Hat 🟨 |
| --- | --- | --- | --- |
| NVFP4 > MXFP4 정확도 | 예 | **예** (74.73 vs 69.32) | 예 |
| NVFP4 > INT4 정확도 | 예 | **아니오 — 비슷함** | "수치적 강건성" (수치 없음) |
| 대형(200B+)에서 거의 무손실 | 예 | — | 예 (~99%) |
| **소형~중형에서 손실 유의미** | **예** (자체 보고서) | **예** (lossy) | **예** (~95~98%) |

> **"NVFP4가 int4보다 낫다"는 벤더 주장은 제3자 측정에서 확인되지 않았다.** 유일한 정면 비교(IST-DASLab)는 "비슷하고, INT4는 분산이 크다"고 한다.

---

# 4. MoE의 양자화 민감도

## 4.1 라우터를 양자화하지 않는 관행 — **확립되어 있다** ✅

이건 논쟁이 아니라 **레시피에 박혀 있다.** RedHatAI/Qwen3.6-35B-A3B-NVFP4의 실제 `ignore` 목록:

```
re:.*lm_head              # 출력 헤드
re:.*mlp.gate$            # ← MoE 라우터
re:.*shared_expert_gate$  # ← 공유 전문가 게이트
re:.*embed_tokens$        # 임베딩
re:.*linear_attn.*
re:visual.* / re:model.visual.*
```
+ `moe_calibrate_all_experts=True`, UltraChat 256샘플 / max_seq_len 4096
([모델 카드](https://huggingface.co/RedHatAI/Qwen3.6-35B-A3B-NVFP4))

**이유(메커니즘):** 라우터 로짓의 미세한 섭동이 **top-k 전문가 선택을 바꾸면 계산 경로 자체가 바뀐다.** 재구성 오차가 작아도 "다른 전문가로 갔다"는 이산적 사건은 복구되지 않는다. [EAQuant](https://arxiv.org/abs/2506.13329) (Fu 외, 🟩)가 이를 *"routing instability"*로 명명하고 *"routing consistency alignment to preserve expert selection post-quantization"*을 제안 — 3개 MoE 아키텍처에서 평균 **1.15~13.81%** 정확도 개선.

**우리 레포에 주는 함의:** 라우터를 양자화하는 레시피는 **쓰지 않는다.** 그리고 §2.1에서 본 Qwen3-30B-A3B의 BBH 83.01%는 **lm_head만 제외한 구형 레시피**이고, 라우터까지 제외한 신형 레시피(Qwen3.6-35B-A3B)는 전 항목 **93~100%**다. 이것이 인과라는 **직접 실험은 못 찾았다(❓)** — 두 모델이 다르므로 **상관까지만** 말할 수 있다.

## 4.2 MoE가 dense보다 더 떨어지는가 — **출처가 엇갈린다** ⚠

**"MoE가 더 강건하다" 쪽:**
[MoQE](https://arxiv.org/html/2310.02410) (Microsoft, Kim 외, 🟩) — 다국어 기계번역 BLEU:

| 비트 | MoE | Dense |
| --- | --- | --- |
| 4-bit | **−0.36%** | −1.90% |
| 3-bit | **−1.21%** | −11.24% |
| 2-bit | **−12.34%** | −91.21% |

**단서 3개:** ① 과제가 **기계번역(BLEU)**이다 — 추론·판단이 아니다. ② **전문가 FFN만** 양자화하고 attention·dense FFN은 fp16으로 남겼다. ③ 2023년 논문이라 현대 MoE LLM이 아니다.

**"MoE가 더 어렵다" 쪽 (🟩, 2025~2026 다수):**
- MoE 특유 문제 3종이 반복 보고된다: **(a)** 전문가별로 다른 활성 아웃라이어 분포, **(b)** 라우터 로짓 섭동 민감도, **(c)** **희소 활성화로 인한 보정(calibration) 병목** — 거의 안 켜지는 전문가는 보정 샘플이 부족해 스케일 추정이 부정확해진다 ([EAQuant](https://arxiv.org/abs/2506.13329), [GEMQ](https://arxiv.org/pdf/2605.23078), [routing-consistent quantization](https://arxiv.org/html/2606.05688v1))
- 전문가별 민감도가 달라 **expert-wise mixed precision**이 필요하다는 흐름 (라우터 L2 변화가 작은 전문가 = 드물지만 중요한 특징 담당 = 양자화에 더 민감)

**🧮 우리 표(§2.1)의 직접 비교:** Qwen3-30B-A3B(MoE) BBH 회복 **83.01%** vs Qwen3-32B(dense) **97.39%** — 같은 레시피·같은 계열에서 **14%p 차**.

> **정직한 종합:** MoQE의 "MoE가 강건하다"는 **전문가 FFN만 양자화한 번역 과제**에서 나왔고, 우리가 하려는 **전체 W4A4 + 추론 과제**와 조건이 다르다. 2025~2026 문헌은 반대 방향이다. **이 질문은 아직 갈렸고, 우리 조건(NVFP4 W4A4, 판단 과제)에 딱 맞는 비교는 못 찾았다(❓).** 다만 §2.1의 14%p 차는 MoE 쪽 위험을 **경고 신호로** 볼 만하다 — 활성 파라미터 3B라는 교란을 감안하더라도.

## 4.3 ⚠ 우리 장비 고유 — sm_121에서 MoE FP4 커널이 깨진다

[SPARK_Qwen3.5-122B-A10B-NVFP4](https://github.com/bjk110/SPARK_Qwen3.5-122B-A10B-NVFP4) (🟩 커뮤니티, GB10 실기):

- SM121은 **NVFP4 텐서코어 네이티브 지원**을 갖는다 (CUTLASS 경로, W4A4 15.2 tok/s)
- **그러나 FlashInfer의 MoE FP4 경로 둘 다 SM12x에서 실패한다:**
  - TRT-LLM JIT: *"JIT only compiles for `major=10` (SM100), not SM12x"*
  - SM120 MXFP4_MINIMAL: fp8 활성을 요구하는데 vLLM이 BF16을 넘김
- **우회:** `VLLM_USE_FLASHINFER_MOE_FP4`를 **설정하지 않고** 네이티브 `cutlass_moe_fp4` 경로로 폴백 — 이건 SM12x에서 동작
- 별개 버그: GDN 가중치 양자화 — `in_proj_qkvz`·`in_proj_ba`를 **BF16으로 강제**해야 함
- 상류 stable vLLM은 GB10 미지원. CUDA-13 nightly 또는 NGC `nvcr.io/nvidia/vllm:26.02-py3+` 필요

sm_121 일반 배경: *"software built only for datacenter Blackwell (sm_100) is not automatically a native match for GB10 (sm_121)"* — 라이브러리들이 sm_80/86/89/90/120은 넣고 **sm_121을 빼는 일이 잦고, 우아한 폴백이 아니라 커널 로드 실패로 터진다** ([kubesimplify](https://blog.kubesimplify.com/day-3-the-dgx-spark-unpacked-gb10-unified-memory-sm-121-and-the-one-reason-this-hardware-exists), 🟩). GB10 대역폭 **273 GB/s** = 256-bit 버스 × 8,533 MT/s로 확인됨(우리 스펙과 일치).

---

# 5. 구조화 출력(JSON schema 강제)의 안정성

## 5.1 양자화가 structured output·function calling에 주는 영향

**가장 직접적인 측정** — Qwen3.6-35B-A3B (MoE) BF16 → NVFP4, 🟨 [모델 카드](https://huggingface.co/RedHatAI/Qwen3.6-35B-A3B-NVFP4):

| 과제 | BF16 | NVFP4 | 회복 |
| --- | --- | --- | --- |
| **BFCLv4 Overall** (function calling) | 57.83 | 56.10 | 97.01% |
| BFCLv4 Single Turn | 53.81 | 53.45 | 99.34% |
| **BFCLv4 Multi-Turn** | 62.25 | 58.13 | **93.38%** |
| BFCLv4 Agentic | 49.91 | 49.31 | 98.80% |
| **SWEBench Verified** | 54.8 | 50.2 | **91.61%** |
| IFEval | 93.09 | 92.45 | 99.31% |

> **패턴:** 단발 함수 호출은 거의 안 떨어진다(99.34%). **여러 턴에 걸친 도구 사용(93.38%)과 실제 에이전트 과제(SWEBench 91.61%)가 떨어진다.** 우리 하네스는 후자다.

## 5.2 🟩 커뮤니티 실측 — "조용한 실패"

[Level1Techs 포럼 테스트](https://mer.vin/news/local-llm-quantization-benchmarks-reveal-silent-tool-call-failures/) (RTX PRO 6000 Blackwell, vLLM, Qwen3.6-27B, **실제 10만 토큰 지원 트랜스크립트**):

| 빌드 | 결과 |
| --- | --- |
| INT8 W8A16 (커뮤니티, 무보정) | **최고** — 올바른 tool call, 레퍼런스에 가장 근접 |
| NVIDIA 공식 NVFP4 (208 fp8 / 193 NVFP4 혼합) | **최악** — 88k 컨텍스트에서 **약 50% 토큰 플립**, 명령 문법 붕괴 |
| AWQ W4A16 | **tool-call JSON을 제대로 닫지 못함**, 잘못된 명령 생성 |
| INT4 KV캐시 | 재현되는 tool-call 실패, **복구 못 함** |
| INT8 KV캐시 | 발산했다가 **복구함** |

**"조용한 실패"의 정의:** 출력이 문법적으로 멀쩡하고 유창한데 **구조화된 파라미터가 틀린다.** 예: `GigabitEthernet0/1/4`를 골라야 할 곳에서 `GigabitEthernet0/0/1.201`을 고르고, 그게 후속 명령으로 연쇄된다. *"Nothing in the output looks broken"* — 에러가 안 난다.

저자 결론: *"양자화 모델의 **라벨은 그것이 부하에서 어떻게 행동할지 거의 아무것도 말해주지 않는다** — 그 특정 아티팩트를 직접 테스트해야 한다."*

⚠ **증거 강도 낮음:** GPU 1대, 모델 계열 1개, 워크로드 1개, **제3자 재현 없음**, 텐서 병렬 이상 현상 미해결. 저자 본인이 명시한 한계다. **정설로 쓰면 안 된다.** 다만 §5.1의 BFCL multi-turn 하락과 방향이 같다.

## 5.3 MoE가 dense보다 JSON이 불안정한가 — ❓ **자료 없음**

MoE와 dense를 **동일 조건으로 비교한 structured-output 측정**을 찾지 못했다. 관련 있으나 직접 답이 아닌 것:
- Granite 3.1 **MoE** 3B가 유효 JSON 생성에 90% 이상 실패한다는 보고가 있으나, **1.5B 이하 dense 모델들도 같은 그룹**에 묶여 있다 — MoE 때문인지 크기 때문인지 **가려지지 않는다** ([StructuredRAG](https://arxiv.org/pdf/2408.11061) 인용 맥락)
- vLLM 이슈 [#18819](https://github.com/vllm-project/vllm/issues/18819): Qwen3에서 `enable_thinking=False` + guided JSON일 때 **잘못된 JSON**(여분 괄호·백틱·gibberish)이 나오고, `enable_thinking=True`면 정상. **양자화와 무관한 버그**지만 §5.4와 같은 메커니즘을 시사한다.

## 5.4 🟩 중요한 메커니즘 — 형식 강제가 **추론 용량과 경쟁**한다

[*Capacity, Not Format*](https://arxiv.org/html/2606.09410) (Fan, Tianjin Normal Univ.) — MATH-Hard, CoT 자유 생성 vs JSON 강제:

| 모델 | CoT | JSON | 차이 |
| --- | --- | --- | --- |
| Claude Sonnet 4.6 | 89.3% | 88.7% | −0.6pp |
| **Claude Haiku 4.5** | 88.7% | **52.5%** | **−36.2pp** (49%가 토큰 한도 도달) |
| GPT-4o | 56.2% | 44.0% | −12.2pp |
| GPT-4o-mini | 62.3% | 34.3% | −28.0pp |
| Opus 4.7 (AIME) | 96.2% | 91.0% | −5.3pp |
| 쉬운 과제(GSM8K) | — | — | 전 모델 ±4pp 이내 |

핵심 주장: **"모델이 능력 한계 근처에서 동작할 때 형식 준수와 추론이 같은 생성 예산을 두고 경쟁한다"**(premature serialization). 여유가 있는 모델은 형식 제약을 흡수한다. **해법: "먼저 생각하고, 나중에 형식화"** — 지연 직렬화가 손실의 **80~87%를 회복**했다.

> ⚠ **이 논문은 양자화를 다루지 않는다.** "양자화 → 유효 용량 감소 → 형식-추론 경쟁 심화"는 **내가 잇는 추론이지 측정이 아니다.** 다만 **설계 처방은 양자화와 무관하게 옳다** — Critic이 JSON을 뱉으며 동시에 반박을 구성하게 하지 말고, **자유 서술로 반박한 뒤 2차 패스에서 스키마로 옮겨라.**

---

# 6. 손실을 줄이는 실무 기법

## 6.1 bf16으로 남기는 레이어 — 실제 레시피 기준

| 레이어 | 남기나 | 근거 |
| --- | --- | --- |
| `lm_head` | **항상** | 모든 RedHatAI NVFP4 레시피. llm-compressor: *"출력 품질을 위해"* |
| `embed_tokens` | **예** | Qwen3.6-35B-A3B 레시피 |
| **MoE 라우터** (`mlp.gate`, `shared_expert_gate`) | **예** | 신형 레시피 + §4.1 메커니즘 |
| 초기 dense 레이어 (layers 0~2) | **예** | [llm-compressor MoE 가이드](https://docs.vllm.ai/projects/llm-compressor/en/latest/examples/quantizing_moe/): *"aggressive quantization에 민감할 수 있음"* |
| **처음·마지막 2개 레이어** | **예** | NVIDIA QAD 보고서 §3: *"더 나은 PTQ 기준선 유지를 위해"* |
| **attention 레이어** | **하이브리드 모델에서 예** | NVIDIA QAD: Nemotron 3 Nano의 6개 self-attention + 앞선 Mamba-2 레이어를 BF16 유지 |
| LayerNorm | 예 (사실상 항상) | 일반 관행 |
| `in_proj_qkvz`, `in_proj_ba` (GDN) | **sm_121에서 강제** | §4.3 커뮤니티 패치 |

> **주목:** NVIDIA조차 **"처음·마지막 2개 레이어 + attention을 BF16으로 남긴" 뒤에** §3.3의 −1.4~−4.6점을 보고했다. 그 표는 **보호 조치를 다 한 뒤의 손실**이다.

## 6.2 Calibration

| 항목 | 내용 | 출처 |
| --- | --- | --- |
| 표준 샘플 수 | **512** (UltraChat 200k, max_seq_len 2048) — *"증가시키면 정확도가 개선될 수 있다"* | RedHatAI 카드 / llm-compressor |
| MoE 전용 | **`moe_calibrate_all_experts=True`** 필수 — 안 켜면 드물게 켜지는 전문가가 보정 안 됨 | Qwen3.6-35B-A3B 레시피 |
| MoE 로딩 | `load_context()` — MoE 모듈이 보정 가능한 형태로 초기화되게 함 | llm-compressor 문서 |
| 데이터 품질 | *"크기보다 품질"*. 실제 프롬프트로 보정할 것 | [2311.09755](https://arxiv.org/pdf/2311.09755) 🟩 |
| 소형 모델 | Red Hat이 8B~14B에서 **보정 observer·SmoothQuant를 바꿔가며 시험했고 결과가 엇갈렸다** — 개선된 것도, 안 한 게 더 나은 것도 있었다 | Red Hat 🟨 |
| ⚠ 한계 | IST-DASLab: **NVFP4의 그룹 크기 16이 전통적 아웃라이어 완화를 무력화**하므로 *"일반적 PTQ 알고리즘이 baseline NVFP4를 개선하지 못하는 일이 잦다"* (NVIDIA QAD가 인용) | 🟩 |

**우리 함의:** 보정 데이터는 **취약점 분석 프롬프트**여야 한다. UltraChat 채팅 데이터로 보정한 체크포인트를 코드 감사에 쓰는 것은 분포 불일치다. 다만 **"우리 도메인 보정이 얼마나 이득인지"의 측정치는 못 찾았다(❓).**

## 6.3 KV 캐시 fp8 (e4m3)의 독립 효과

🟨 [vLLM 2026-04-22](https://vllm.ai/blog/2026-04-22-fp8-kvcache) — **무보정(scale=1.0) 최악 조건** 기준:

| 조건 | 결과 |
| --- | --- |
| 추론 과제, Qwen3-30B-A3B-Thinking | 최대 **−1~2점**, 최저 회복 **97%** (GPQA-D) |
| 추론 과제, Qwen3.5-27B | 최대 **−0.7점**, 최저 회복 99% |
| 롱컨텍스트 AUC@128k, Llama-3.3-70B | **97~98%** |
| 롱컨텍스트 AUC@256k, Qwen3-30B-A3B | **94~98%** |
| 롱컨텍스트 AUC@1M, Qwen3.5-27B | **완전 회복** |
| 처리량 (동시성 8, 20k in / 2k out, Llama-3.1-8B) | 출력 처리량 **+14.9%**, 총 실행시간 13.0% 단축 |
| KV 토큰당 메모리 | 최선의 경우 BF16의 **54%** |

**⚠ 함정 둘:**
- **슬라이딩 윈도우 하이브리드 모델**(gpt-oss-20b 등): 작은 윈도우 레이어는 KV 발자국이 이미 작아서 **양자화 비용만 내고 이득이 없다.** `--kv-cache-dtype-skip-layers sliding_window`로 건너뛰는 게 최선
- **head_dim=256 모델**(Gemma-4-E2B): 디코드는 좋아지는데 **프리필 TTFT 2차항이 fp8에서 1.6배 커진다** (two-level accumulation의 레지스터 압박)
- 짧은 컨텍스트(<7k)는 손익분기 미달 — BF16이 오히려 빠를 수 있음

**그런데 우리 장비 문서는 더 보수적이다.** vLLM DGX Spark 가이드(🟨)는 fp8 KV캐시가 *"모델 예측 가능성에 영향을 줄 수 있고 눈에 띄는 성능 비용을 수반"*한다며 **"메모리 압박이 요구하고 품질 체크를 통과하기 전에는 쓰지 말라"**고 한다.

**Critic 권고:** KV캐시는 **bf16**. §1.1의 롱컨텍스트 논거와 §5.2의 INT4 KV 실패가 같은 방향이고, 우리는 KV 메모리가 병목이 아니다(§1.3).

## 6.4 손실 회복 기법 (참고)

- **QAD (quantization-aware distillation)** — BF16 교사 ↔ 양자화 학생, KL divergence. NVIDIA가 **QAT보다 낫다**고 보고: QAT는 검증 손실은 맞추지만 **출력 분포를 바꿔버린다**(KL 0.311 vs QAD 0.004). RL 학습 모델에서 QAT는 **PTQ보다도 성능을 떨어뜨렸다**. QAD는 30B-A3B에서 AA-LCR 31.3→34.3, AIME25 85.0→87.9로 회복. 다만 **0.3B 토큰 규모 학습이 필요**하다 — 우리 범위 밖일 가능성이 높다
- **오버싱킹 로짓 페널티** — "wait"/"but"/"alternatively" 토큰에 페널티. **학습 불필요**, CoT 길이 12~23% 단축하며 정확도 유지, 오버싱킹 오류 최대 58% 감소 ([2606.00206](https://arxiv.org/abs/2606.00206), 🟩). **Critic이 4비트로 가야 한다면 이건 싸고 시도할 만하다**

---

# 7. 우리 장비(sm_121)에 비춘 실용 판정

## 7.1 권고

| # | 판정 | 근거 |
| --- | --- | --- |
| 1 | **Critic은 fp8.** §1.3대로 30B급이면 119.7 GiB에 여유롭게 들어간다. 4비트는 메모리가 아니라 속도 선택이고, Critic은 파이프라인에서 가장 적게 호출되는 역할일 것이므로 속도 예산을 여기 쓰지 않는다 | §1.1, §1.3, §2.2 |
| 2 | **Scout은 NVFP4 허용.** 후보 생성은 뒤가 걸러준다 | §1.2 |
| 3 | **4비트가 불가피하면 W4A16(AWQ)를 W4A4(NVFP4)보다 먼저 본다.** 32B에서 −0.82% vs −2.33%. 디코드가 대역폭 병목이므로 W4A16도 🧮 이론상 4배 메모리 트래픽 감소를 얻는다 (텐서코어 가속은 못 얻음) | §2.2 |
| 4 | **라우터·lm_head·embed_tokens·초기 2레이어·마지막 2레이어는 절대 양자화하지 않는다** | §6.1 |
| 5 | **KV 캐시는 bf16 유지** | §6.3 |
| 6 | **Critic 프롬프트를 2패스로 나눈다** — 자유 서술 반박 → 별도 패스에서 JSON 스키마 직렬화. 지연 직렬화가 손실의 80~87%를 회복한다 | §5.4 |
| 7 | **MoE를 쓴다면 sm_121 FlashInfer MoE FP4 경로를 피하고** `cutlass_moe_fp4` 폴백을 쓴다. `VLLM_USE_FLASHINFER_MOE_FP4`를 설정하지 않는다 | §4.3 |
| 8 | **상류 stable vLLM 대신 CUDA-13 nightly 또는 NGC `nvcr.io/nvidia/vllm:26.02-py3+`** | §4.3 |
| 9 | **평균 지표로 판정하지 않는다.** OpenLLM V1 99.83%와 추론 평균 94.21%가 같은 체크포인트에서 나온다 | §2.1 |
| 10 | **우리 과제로 직접 잰다.** 커뮤니티 결론이 *"라벨은 아무것도 말해주지 않는다"*였고, Red Hat도 *"작은 스케일에선 모델별 튜닝"*이라고 했다 | §5.2, §3.4(c) |

## 7.2 sm_121 특유 리스크 (소프트웨어)

정확도와 별개로, **커널 성숙도가 우리 위험의 큰 부분이다.** NVFP4는 sm_121에서 텐서코어 네이티브로 돌지만(CUTLASS 경로 확인됨), **MoE FP4 경로는 상류에서 깨져 있고 커뮤니티 패치로 우회 중이다.** 라이브러리들이 sm_121을 빼면 **폴백이 아니라 커널 로드 실패**로 터진다. 이건 §0 규칙 문제이기도 하다 — **깨진 커널로 잰 값은 양자화 손실이 아니라 버그다.**

---

# 8. 「자료 없음」 — 찾지 못한 것

| 질문 | 상태 |
| --- | --- |
| **양자화가 "반박·비판적 검증" 과제에 미치는 영향의 직접 측정** | ❓ **없음.** 가장 가까운 것이 hallucination detection(TruthfulQA), IFEval, LLM-as-judge(MT-Bench)다. **반박(refutation) 전용 벤치마크에서 정밀도별 손실을 잰 연구를 못 찾았다** |
| MoE vs dense의 **JSON/structured output 안정성** 동일 조건 비교 | ❓ 없음 (§5.3) |
| MoE vs dense의 **W4A4 + 추론 과제** 통제된 비교 | ❓ 없음. §2.1 비교는 활성 파라미터 교란이 있음 |
| **라우터를 양자화했을 때 vs 안 했을 때**의 통제된 ablation 수치 | ❓ 없음. 관행과 메커니즘 설명은 확립됐으나 "얼마나 나빠지는가"의 숫자는 못 찾음 |
| 도메인 특화 calibration(코드/취약점)이 범용 UltraChat 대비 얼마나 이득인가 | ❓ 없음 |
| **GB10 sm_121에서의 정확도 측정** (처리량 수치는 있음) | ❓ 없음 |
| RewardBench 등 **보상/심판 모델 전용 벤치마크의 양자화 손실** | ❓ 없음 |
| Scout 같은 **고재현율 후보 생성** 과제의 양자화 손실 | ❓ 없음 |

## 8.1 ⚠ 주의해서 읽어야 할 것 하나

IJCAI-25가 **"MT-Bench의 Reasoning 카테고리는 양자화로 약 1점 올랐다"**고 보고했다. **이걸 "양자화가 추론을 개선한다"로 읽으면 안 된다.** 같은 논문이 바로 다음 문단에서 스스로 뒤집는다:

> *"수작업 확인 결과 **간결한 답이 GPT4에게 더 높은 점수를 받는 경향**이 있고, 양자화 모델이 원본보다 더 간결하게 답하는 경우가 많았다."*
> *"Reasoning 과제에서 **GPT4가 틀린 답을 맞다고 잘못 판정해** 양자화 모델의 점수를 올렸다."*

**즉 이 상승은 심판 아티팩트다.** 그리고 이건 우리에게 이중으로 중요하다 — **LLM 심판이 양자화된 모델의 간결함에 속는다면, 우리가 Critic 품질을 LLM 심판으로 평가할 때 같은 함정을 밟는다.**

---

# 9. 출처

**🟩 제3자 (학계·독립)**
- [Quantization Hurts Reasoning? An Empirical Study on Quantized Reasoning Models](https://arxiv.org/abs/2504.04823) — COLM 2025
- [Quantized Reasoning Models Think They Need to Think Longer, but They Do Not](https://arxiv.org/abs/2606.00206) — Lotfi, Kirichenko, Li, Liu
- [Exploring the Trade-Offs: Quantization Methods, Task Difficulty, and Model Size](https://arxiv.org/abs/2409.11055) — IJCAI 2025, Lee 외
- [Does quantization affect models' performance on long-context tasks?](https://arxiv.org/abs/2505.20276) — EMNLP 2025, Mekala 외
- [Bridging the Gap Between Promise and Performance for Microscaling FP4 Quantization](https://arxiv.org/abs/2509.23202) — Egiazarian, Alistarh 외 (IST-DASLab)
- [INT vs FP: A Comprehensive Study of Fine-Grained Low-bit Quantization Formats](https://arxiv.org/html/2510.25602v1) — HKU / ByteDance Seed
- [MoQE: Mixture of Quantized Experts](https://arxiv.org/html/2310.02410) — Microsoft
- [EAQuant: Expert-Aware PTQ for MoE](https://arxiv.org/abs/2506.13329) — Fu 외
- [Capacity, Not Format: Rethinking Structured Reasoning Failures](https://arxiv.org/html/2606.09410) — Fan
- [On the Impact of Calibration Data in Post-training Quantization and Pruning](https://arxiv.org/pdf/2311.09755)
- [DGX Spark 실기 NVFP4 셋업 (GB10/SM121)](https://github.com/bjk110/SPARK_Qwen3.5-122B-A10B-NVFP4) — n=1
- [Local LLM Quantization Benchmarks Reveal Silent Tool-Call Failures](https://mer.vin/news/local-llm-quantization-benchmarks-reveal-silent-tool-call-failures/) — n=1, 재현 없음
- [Day 3: DGX Spark Unpacked — GB10, sm_121, NVFP4](https://blog.kubesimplify.com/day-3-the-dgx-spark-unpacked-gb10-unified-memory-sm-121-and-the-one-reason-this-hardware-exists)

**🟨 도구 벤더 (Red Hat / vLLM — NVIDIA 아님, 중립도 아님)**
- [We ran over half a million evaluations on quantized LLMs](https://developers.redhat.com/articles/2024/10/17/we-ran-over-half-million-evaluations-quantized-llms)
- [Accelerating LLMs with NVFP4 quantization](https://developers.redhat.com/articles/2026/02/04/accelerating-large-language-models-nvfp4-quantization)
- [The State of FP8 KV-Cache and Attention Quantization in vLLM](https://vllm.ai/blog/2026-04-22-fp8-kvcache)
- [vLLM on the DGX Spark](https://vllm.ai/blog/2026-06-01-vllm-dgx-spark)
- [llm-compressor: Quantizing MoE models](https://docs.vllm.ai/projects/llm-compressor/en/latest/examples/quantizing_moe/)
- 모델 카드: [Qwen3-32B-NVFP4](https://huggingface.co/RedHatAI/Qwen3-32B-NVFP4) · [Qwen3-30B-A3B-NVFP4](https://huggingface.co/RedHatAI/Qwen3-30B-A3B-NVFP4) · [Qwen3-8B-NVFP4](https://huggingface.co/RedHatAI/Qwen3-8B-NVFP4) · [Qwen3.6-35B-A3B-NVFP4](https://huggingface.co/RedHatAI/Qwen3.6-35B-A3B-NVFP4)

**⚠ 벤더 (NVIDIA)**
- [Introducing NVFP4 for Efficient and Accurate Low-Precision Inference](https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/) — 마케팅 블로그
- [Quantization-Aware Distillation for NVFP4 Inference Accuracy Recovery](https://research.nvidia.com/labs/nemotron/files/NVFP4-QAD-Report.pdf) — 2026-03-05 기술 보고서. **자체 손실 인정 + 수치 공개**

---

# 10. 이 문서의 지위

이 문서의 수치는 **전부 남의 장비, 남의 모델, 남의 과제**에서 나왔다.
**GB10 sm_121에서 우리 Critic 프롬프트로 잰 값은 `0`이다 — 미실측이지 무손실이 아니다.**

이 문서가 정하는 것은 **무엇을 먼저 재야 하는가**이지 판정이 아니다. 최소 측정 세트 제안:

1. 같은 모델의 **bf16 / fp8 / NVFP4** 3판으로 **동일 Critic 프롬프트** — 반박 정확도(진짜 결함을 기각하지 않는가 + 가짜 결함을 잡아내는가)
2. **롱컨텍스트 구간**(우리 실제 코드 입력 길이)에서 위 3판 — §1.1의 −59% 구간에 우리가 들어가는지
3. **structured output 실패율** — JSON 스키마 준수 + 필드 정확성 분리 측정 (§5.2의 "유효하지만 틀린" 실패를 잡으려면 둘을 갈라야 한다)
4. 평가는 **LLM 심판만으로 하지 않는다** (§8.1)
