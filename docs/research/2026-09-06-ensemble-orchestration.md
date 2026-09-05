# 역할마다 모델 둘 — Scout 풀 · Auditor 2 · Critic 2를 어떻게 묶나 (문헌 조사)

조사일 **2026-09-06** · 대상 `origin/main` `ba3c928` · 대상 장비 NVIDIA GB10 (sm_121)

> **출처 표기 규약.** **[실측]** = 우리가 ATOM에서 잰 것. **[출처주장]** = 논문·블로그·모델 카드가 자기 조건에서
> 잰 것 — 우리 과제·우리 모델에서 재현된다는 보장이 없다. **[계산]** = 산술 유도. **[미확인]** = 못 찾았다.
> 표시 없는 문장은 저장소를 열어 확인한 사실이다. **어떤 문헌도 우리 조건(오픈 MoE 둘 · 단일 GB10 ·
> C/C++ 취약점)을 그대로 재지 않았다** — §6이 그 구멍을 센다.

질문은 셋이다. 팀은 Auditor와 Critic에 **각각 모델 둘**을 쓰기로 했다([2026-09-04-models.md](2026-09-04-models.md)는
역할당 하나씩 조합 넷을 추렸다). ① 둘을 **어떻게** 묶나 — 합집합·투표·연쇄(cascade)·토론 중 무엇인가.
② 둘이 실제로 **다르게 틀리나** — 제작사가 다르면 오류 상관이 낮다는 전제가 얼마나 성립하나.
③ **셋째는** 필요한가. 모델 크기는 이 문서의 변수가 아니다 — 결합 규칙은 크기와 무관하고, 크기·학습은
[2026-09-06-harness-architecture.md](2026-09-06-harness-architecture.md) §4·§6이 다룬다.

---

## 0. 한 장 요약 — 문헌이 지지하는 만큼만

| # | 결론 | 근거 강도 |
| --- | --- | --- |
| F1 | **Auditor 둘은 합집합(union)으로 묶고 투표하지 않는다.** 합집합은 재현율을 올리고 정밀도를 무너뜨리며(§2.1), 다수결은 반대로 재현율을 깎는다(§2.2). 정밀도는 뒤(게이트·Critic·도구)가 되찾는다 — 우리 구조가 이미 그렇다 | 셋 이상의 독립 출처가 같은 방향 |
| F2 | **제작사가 다르면 오류 상관이 낮아지지만, 폭이 크지 않다.** 같은 모델 재샘플 0.68 > 같은 계열 0.52 > 다른 계열 0.47(§2.3). 취약점 과제에서는 **다섯 모델이 같은 19건을 함께 놓쳤다**(Semgrep) | 방향은 일관, 크기는 과제 의존 |
| F3 | **둘이 무릎이다.** 판정 패널은 9명이 유효 2.2표이고 5명이 90%를 채운다(§2.3). 셋째 Auditor·Critic보다 **입력 표현을 바꾸는 것**(슬라이서·도구 근거)이 남은 이득이다 | 패널 연구 하나 + 취약점 실측 하나 |
| F4 | **일치는 정답이 아니다.** 만장일치 항목 정확도 90.9%, 일치–정답 상관 0.20~0.59, 리뷰어 열 명이 만장일치로 승인한 **존재하지 않는** 취약점을 실험 하나가 죽였다(§3.1). Critic 둘의 합의는 **8단계 우선순위**이지 승격 근거가 아니다 | 셋 이상 |
| F5 | **토론(multi-round debate)은 넣지 않는다.** 동종 토론은 고립 자기수정보다 못하고 토큰을 2.1~3.4배 쓴다. 취약점 과제에서도 2~3라운드가 1라운드보다 나빴다(§3.3). 한 라운드 · 독립 · 주장만 전달 | 넷 이상 |
| F6 | **Critic은 반증 임무(kill mandate)와 문맥 비대칭**으로 돈다. 다른 계열 Critic이 같은 계열 리뷰가 놓친 결함 3건을 잡고, 승인된 수정 19건 중 3건의 오류를 찾았다(§3.2). 우리 agent-contracts §2.3 규칙(Auditor 추론 원문 비전달)과 같은 방향 | 단일 운영자 사례 연구 — 재현 없음 |
| F7 | **연쇄(A만 돌리고 불확실할 때 B)는 비용 절감이 크지만 신호 품질에 달렸다.** 우리 Scout 점수는 고유값 9개·71%가 0.0이라(D28) 신호가 없다. **첫 실측은 둘 다 전량**, 연쇄는 공동오답 행렬을 본 뒤 | 이론·다른 도메인 |
| F8 | **Scout 전문가 분업은 라우터와 오탐 필터 없이는 역효과다.** CWE 전문가는 자기 분포에서 +0.03~0.06 F1인데 혼합 데이터에서 F1 0.12~0.19로 붕괴한다(§4). 라우터 top-k + 격리 탐지기가 LLMxCPG 대비 macro-F1 +41.5% | 둘 |

---

## 1. 산업 시스템이 모델 다양성을 어떻게 쓰나 — 방향 확인

전부 **[출처주장]**이고 모델은 프론티어급이라 우리 크기가 아니다. 그러나 **다양성의 용도**는 크기와 무관하다.

| 시스템 | 다양성의 형태 | 관측 |
| --- | --- | --- |
| Palo Alto Unit 42 **NOVA** | 프론티어 모델 여럿을 같은 대상에 병렬 | *"Every model contributed a large set of findings that no other model found."* 최다 모델 235건 확정(185건 고유) · 최소 모델 139건(93건 고유). 두 달 · 3,915 프로젝트 · 확정 14,090건 · 99.4% 미보고 |
| Microsoft **MDASH** | 패널 = 무거운 추론기 + 증류 모델(고빈도 debater) + **별도 SOTA 하나를 독립 반대 의견**으로 | *"Disagreement between models is itself a signal: when an auditor flags something as suspect and the debater can't refute it, that finding's posterior credibility goes up."* CyberGym 88.45%(1,507건). 6월 후속 글: 미탐 52건 중 **65.4%가 prove 단계**, scan 15.4%, validate 19.2% |
| **Refute-or-Promote** (독립 연구자) | 창의 에이전트 vs 반증 에이전트 + **다른 계열 Cross-Model Critic** | ∼171 후보 중 **∼79% 폐기**, 전향적 30건 중 83%(25건). CMC가 같은 계열이 놓친 3건 발견. **리뷰어 10명 만장일치 승인 → 실험 하나가 기각**(OpenSSL CMS) |
| **Sangfor** (CyberGym 1위) | 단일 모델(GLM-5.3·DeepSeek-V4-Flash) 스웜 — 다양성은 모델이 아니라 **가설 독립성** | *"strict separation between exploration and final acceptance"* · 최종 PoC 하나만 차분 검증 · 1,465/1,507(97.21%) |
| Provos **vuln-discovery** | 프론티어(Opus 4.6·4.7·Sonnet 4.6)와 **오픈웨이트 GLM 5.1** 교차 | GLM 5.1이 18년 된 정수 절단 결함을 찾았다. *"Vulnerability discovery is an orchestration problem, not a frontier-model problem."* 조사 1회 ≈ 10M 토큰 · $30~150 |

**읽는 법.** NOVA의 「모델마다 고유 발견」이 F1(합집합)의 산업 근거이고, MDASH의 「반박 못 하면 신뢰 상승」이
F6(반증 임무)의 근거다. Sangfor는 **모델을 하나만 쓰고도** 1위다 — 다양성의 본체가 모델 수가 아니라
**독립 가설 + 도구 검증 + 탐색/수락 분리**라는 뜻이다. 우리 두 모델 설계는 이 셋 위에 얹는 것이지 대체가 아니다.

---

## 2. Auditor 둘

### 2.1 합집합은 재현율을 사고 정밀도를 판다

| 연구 | 구성 | 재현율 | 정밀도 | 비용 |
| --- | --- | --- | --- | --- |
| **MultiVer** (arXiv 2602.17875) | 에이전트 4 · **전부 Claude Opus 4.5** · union voting | PyVul **82.7%**(보안 에이전트 단독 65.7% → **+17pp**) | **48.8%** · **오탐률 85%** · F1 61.4(파인튜닝 GPT-3.5는 71.6) | 표본당 $0.46 · 55초 |
| **Strategic Heterogeneous 3+1** (arXiv 2604.21282) | 전문가 3(DeepSeek-V3) + 로컬 검증자(Qwen3-8B) · Juliet 262건 | 전 구성 100% | 단일 전문가 F1 **71.4** → 전문가 셋 **68.9**(정밀도 55.5→52.6) → **+검증자 77.2**(정밀도 +10.3pp, p<10⁻⁶) | 표본당 $0.002 · 병렬 68.1s vs 직렬 92.2s |
| **GPTLens** (arXiv 2310.01152) | Auditor n=2 · m=3 · t=0.7 → Critic t=0 순위 · GPT-4 | 적중률 **38.5% → 76.9%**(CVE) · 33.3 → 59.0(trial) | 미계량. GPT-4 단독은 선행 벤치에서 오탐 740건·정밀도 4.15% | — |

**핵심은 3+1의 가운데 행이다.** 전문가를 셋으로 늘리면 **검증자 없이는 단일 전문가보다 F1이 낮다**(68.9 < 71.4).
합집합의 정밀도 비용은 뒤에 있는 판정기가 되찾을 때만 지불할 가치가 있다 — 우리는 그 판정기가
6단계 게이트(결정론)·7단계 Critic·8단계 도구다([architecture.md](../architecture.md) §3).

### 2.2 다수결은 반대로 재현율을 깎는다

**DVDR-LLM** (arXiv 2512.12536) — 오픈 모델 10개(7B~70B, CodeLlama·Llama3/3.1·Gemma2·Mistral·Mixtral)를
합의 임계 T로 묶은 다수결, 리눅스 커널 CVE(VulnLLMEval):

| 과제 | 앙상블(T=70%) | 최고 단일 |
| --- | --- | --- |
| 탐지(SVD1) | 179건 정답 | Llama3-8b **286건** |
| 검증(SVD2) | 136건 | Llama3-70b **210건** |
| 멀티파일(level 3) | 재현율 **+18.05%** · F1 +11.8%(67.24) | — |

저자 서술: *"prioritizing false positive reduction in verification tasks at the cost of increased false negatives
in detection tasks."* **탐지 자리에 다수결을 두면 놓친다.** 다수결이 맞는 자리는 검증이고 그건 우리 7단계다 —
그리고 거기서도 F4(일치 ≠ 정답) 때문에 승격 근거로는 못 쓴다.

### 2.3 다른 계열은 얼마나 다르게 틀리나

| 연구 | 측정 | 값 |
| --- | --- | --- |
| **LLMs as a Jury** (arXiv 2607.10139) | 쌍별 오류 상관 | 같은 모델 재샘플 **0.68** > 같은 계열 **0.52** > 다른 계열 **0.47**. 4모델 패널이 자기일관성 N=32를 이김(AIME 0.70 대 0.433). 공유 오류 하한: 수학 ≈0 · GPQA 3% · MMLU-Pro 14.3% |
| **Too Consistent to Detect** (EMNLP '25) | 자기일관 오류의 교차 모델 겹침 | Qwen2.5-7B × 14B **28.7%** · × 72B 14.2% · × Llama3.1-70B **5.4%**. 외부 검증 모델 hidden state를 섞은 probe가 AUROC 0.729→0.866(OOD) |
| **Nine Judges, Two Effective Votes** (arXiv 2605.29800) | 유효 독립 표 수 n_eff = k/(1+(k−1)φ̄) | 7개 계열 9명 → **n_eff 2.18**. 5명이 90%. 패널 72.0% 대 최고 단일 71.8%. **가장 상관 높은 짝이 교차 계열**(Claude×Gemini 0.603). 계열당 1명으로 좁히면 n_eff **1.93으로 하락** |
| **Semgrep 감사** | IDOR 27건 · 모델 5개 | 합집합 재현율 **29.6%** vs 최고 단일(Kimi) 26% — **다섯이 같은 19건을 놓쳤다** |
| **Refute-or-Promote** | 다른 계열 CMC | 같은 계열 리뷰가 놓친 **3건** 발견 · 승인된 수정 19건 중 3건(∼16%) 오류 적발 |

**정직한 종합.** 방향은 일관한다 — 계열이 멀수록 덜 겹친다. 크기는 과제가 정한다 — 수학은 거의 0, 지식은 14%,
**취약점 탐지는 Semgrep 하나가 「거의 전부 겹친다」고 말한다.** [2026-09-04-models.md](2026-09-04-models.md) §5.0이
이미 이 전제를 의심해 뒀고, 답은 문헌이 아니라 **우리 공동오답 행렬**([build-plan.md](../build-plan.md) §8 row 4)에서
나온다. Nine Judges의 「계열당 1명이 오히려 n_eff를 낮췄다」는 관측은 **제작사 라벨이 독립성의 좋은 프록시가
아니다**라는 뜻이라, 우리 D59의 「계열」 정의 논쟁([D59](../open-questions/D/D59.md))을 코드로 강제하더라도
**그 강제가 오류 독립성을 보장하지 않는다**는 것을 함께 적어야 한다.

### 2.4 연쇄 — A만 돌리고 불확실할 때 B

| 연구 | 무엇 | 우리에게 |
| --- | --- | --- |
| **Agreement-Based Cascading** (arXiv 2407.02348) | 하위 앙상블이 **불일치**할 때만 상위로 넘긴다. 안전한 조건은 계층 간 비용비 γ ≤ 1/50. 임계값 추정에 검증 표본 ∼100 | 우리 둘은 **비용이 같은 급**이다. ABC의 안전 조건이 성립하지 않는다 — 불일치 신호를 쓰려면 둘을 다 돌려야 하고 그러면 절감이 없다 |
| **Is Escalation Worth It?** (arXiv 2605.06350) | 신뢰도 임계 연쇄. 90% 천장 품질에서 비용 절감 MATH 79.5% · MMLU 73.7% · LiveCodeBench 56.1% · SimpleQA 15.1%. 신호의 benefit-AUROC 0.49~0.76 | 절감 폭이 **신호 품질**의 함수다. 합의 기반 연쇄는 평가하지 않았다 |
| **When LLMs Agree, Are They Right?** (arXiv 2607.08065) | 자기일관성–정답 상관 ρ **0.20~0.59**. gpt-4.1은 GPQA에서 C≥0.8인 답의 **48%가 오답**. 신뢰도 라우팅이 한 설정에서 **역효과** | 「A가 확신하면 B를 건너뛴다」의 위험 — 확신하는 오답이 그대로 통과한다 |

**판정.** 연쇄의 유일한 후보 신호는 Auditor A의 `verdict = need_evidence`·게이트 등급 ≤E1·Scout 멤버 불일치다.
셋 다 **신호 품질을 잰 적이 없다** — Scout 점수는 해상도가 없고([D28](../open-questions/D/D28.md)) 후보당
비용도 미실측이다([D24](../open-questions/D/D24.md)). 그래서 **모드 R(둘 다 전량)로 먼저 돌려 공동오답 행렬과
「A 확신 오답을 B가 잡은 건수」를 세고**, 그 수가 작으면 모드 C(연쇄)로 바꾼다. 순서를 뒤집으면 무엇을 잃었는지 영영 모른다.

---

## 3. Critic 둘

### 3.1 일치는 정답이 아니다 — 승격 근거로 못 쓴다

- Nine Judges: 9명 **만장일치 319건의 정확도 90.9%** — 독립이었다면 오답 만장일치는 ∼0.02%여야 한다.
- When LLMs Agree: gpt-4.1과 Claude-haiku가 **같이 틀린 경우의 67%가 같은 오답 선택지**(순열 귀무 대비 p=.003). 네 모델 전부 틀린 항목 6건.
- Refute-or-Promote: *"ten dedicated reviewers unanimously endorsed a non-existent Bleichenbacher padding oracle in OpenSSL's CMS module; it was killed only by a single empirical test."*
- Hard2Verify([2026-09-04-models.md](2026-09-04-models.md) §3.4.2): 약한 verifier는 **TNR→0·TPR→1**로 붕괴 — 통과율 100%는 고장 신호.

→ Critic 둘이 둘 다 `uphold`여도 상태는 **E3까지**다(재현 명세가 있을 때). `proven`은 8단계만 낸다.
[architecture.md](../architecture.md) §4 등급표가 이미 그렇고, 이 조사는 그 선을 **넘지 말 이유**를 보탠다.

### 3.2 반증 임무 · 문맥 비대칭 · 다른 계열

Refute-or-Promote의 기제 셋을 우리 7단계에 대응시킨다.

| 기제 | 원문 | 우리 자리 |
| --- | --- | --- |
| **Kill mandate** | *"The critic carries a kill mandate rather than an improve/evaluate mandate."* 창의 1 : 반증 2(A단계) → 창의 2 : 반증 3(B단계) | Critic 프롬프트의 임무를 「반박 축 6종으로 죽여라」로 둔다. `verdict=rebut`에는 근거 ID가 붙어야 하고 없으면 `uphold`로 정규화한다 — `gate.normalize()`가 이미 그렇다 |
| **Context asymmetry** | 반증자는 **주장만** 받는다(창의자의 추론 비전달). *"cold-start agents that independently reach a different conclusion...provide higher-value signal"* | [agent-contracts.md](../agent-contracts.md) §2.3 「Auditor의 원문 추론은 전달하지 않는다」와 `nodes/critic.py` 머리 docstring이 같은 규칙이다. **둘째 Critic은 첫째 Critic의 판정도 받지 않는다** — 받으면 위치 편향과 정박이 생긴다 |
| **Cross-Model Critic** | 다른 계열 · **최소 문맥**(요약 + 진입점) | Critic 두 계열은 **Auditor 두 계열과도 다르다** — 네 슬롯 네 제작사([2026-09-06-harness-architecture.md](2026-09-06-harness-architecture.md) §4) |

### 3.3 라운드는 하나 — 토론은 넣지 않는다

| 연구 | 결과 |
| --- | --- |
| **VulTrial** (ICSE '26 · arXiv 2505.10961) | 같은 GPT-4o로 4역할(검사·변호·조정·배심). PrimeVul P-C **81** vs 단일 CoT 40 vs GPTLens 44. **2~3라운드는 1라운드보다 나빴다.** 오탐률 0.52(CoT 0.17). 조정자만 50쌍 튜닝 → 81→96 |
| **The Cost of Consensus** (arXiv 2605.00914) | 7~8B 동종 10명 3라운드: Qwen GSM-Hard 58.8 vs 고립 자기수정 61.0 · Ministral 20.7 vs 48.3. 토큰 **2.1~3.4배**. 다수 추종(sycophancy) 최대 **85.5%** · 정답 생성 후 투표로 버림 최대 32.3pp |
| **Statistical Scouting** (arXiv 2605.09618) | 8B 오픈 모델, 동일 토큰 천장: 토론 vs 3표 투표 ±3pp. *"66% of debate-helpful examples occur when voting is unanimous but wrong"* |
| **Heterogeneous Debate Under Adversarial Peers** (arXiv 2606.19826) | **정직한 이종 동료**는 유해 수정 89%→35%(Llama-70B/MATH-hard). 동료가 적대적이면 이득이 사라진다(90.0%) |
| **LM vs LM** (EMNLP '23) | 다른 모델이 **한 라운드 질문**으로 사실 오류를 잡는 cross-examination이 기준선을 크게 이김 |
| **PoLL** (arXiv 2404.18796) | 서로 다른 계열 소형 3(Command R·Haiku·GPT-3.5) 패널이 GPT-4 단일 심판을 이김(NQ κ 0.763 vs 0.627) · 비용 1/7~1/8 · GPT-4는 자기 변종을 실제보다 높게 평가(self-preference) |

→ **우리 Critic 둘은 서로 말하지 않는다.** 각자 주장만 받아 한 번 반증하고, 하네스가 둘의 `Decision`을 나란히 기록한다.
이종성의 이득(Heterogeneous Debate)은 「두 모델을 **한 판정에** 넣을 때」가 아니라 「**둘의 독립 판정을 하네스가**
합칠 때」 살아남는다 — Cost of Consensus의 sycophancy가 그 반대 경로다.

### 3.4 형식 — 두 패스

[2026-09-04-quantization.md](2026-09-04-quantization.md) §5.4·§7 #6이 이미 적었다: JSON 강제가 추론 용량과
경쟁해 능력 한계 근처 모델에서 −12~−36pp이고, **자유 서술 → 별도 직렬화**가 손실의 80~87%를 회복한다.
Critic은 반증(추론)이므로 **1패스 자유 서술 반박 → 2패스 `Decision` 스키마**로 간다. 2패스는 같은 모델이
자기 서술을 요약하는 것이라 싸다(입력은 1패스 출력, 출력은 스키마 필드). 위치 편향 계측(A/B 순서 뒤집기)은
2패스가 아니라 **두 Finding을 나란히 비교시키는 경우에만** 필요하다 — 우리 Critic은 Finding 하나씩 보므로
기본 경로에는 없고, §5의 계측 항목으로만 둔다.

### 3.5 둘의 판정을 합치는 규칙 (제안)

| C1 | C2 | 처리 | 왜 |
| --- | --- | --- | --- |
| uphold | uphold | 게이트 재판정(E3 조건은 그대로) → 8단계 **우선순위 1** | 일치는 우선순위다. 승격은 게이트가 한다(F4) |
| uphold | rebut(근거 있음) | 8단계 **우선순위 2** · 반박 축을 `MissingEvidence`로 붙여 도구가 그 축을 먼저 친다 | MDASH: 불일치가 신호. 반박이 옳으면 도구가 확인하고, 틀리면 도구가 기각한다 |
| rebut(근거 있음) | rebut(근거 있음) | `queued` + 두 반박 축 기록. **폐기 아님** | architecture §3 Queue 규칙. 둘 다 같은 축이면 그 축이 재평가 트리거다 |
| 어느 쪽이든 근거 없는 rebut | — | `uphold`로 정규화 + `critic_normalized` (지금 코드) | 근거 없는 반박 비율이 Critic 품질 지표다 |
| 어느 쪽이든 `need_evidence` | — | 요구한 근거를 `MissingEvidence`로 추가 · 우선순위 2 | Auditor의 `need_evidence` 처리와 같은 형 |

**계측(반드시).** Critic별 통과율(100%면 고장) · 근거 있는 반박 비율 · 둘의 판정 일치율 · **8단계 결과 대비
각 Critic의 혼동행렬**(이것이 Critic 쪽 공동오답 행렬이다) · 2패스 스키마 재검사 실패율.

---

## 4. Scout 오케스트레이터

[build-plan.md](../build-plan.md) §5 4단계가 이미 정한 것 — **투표가 아니라 분업**, 멤버마다 결함 계열과
`reason_codes` 어휘가 다르고, 합치지 않고 줄만 세우며, 발화 멤버를 남긴다. 문헌이 더 주는 것:

| 연구 | 결과 | 우리에게 |
| --- | --- | --- |
| **From Generalist to Specialist** (arXiv 2408.02329) | DeepSeek-Coder-1.3B 파인튜닝. CWE-125 F1 **0.8867 vs 0.8218**, CWE-787 0.8696 vs 0.8298, CWE-119 동일. **혼합 데이터에서 전문가 F1 0.12~0.19로 붕괴** — 학습 시 정상 표본 다운샘플링 탓 | 전문가 멤버는 **자기 계열 밖에서 오탐을 쏟는다.** 합집합만 하면 오탐이 전문가 수만큼 는다. **라우터 또는 오탐 필터(L1.5)가 짝이다** — build-plan이 「2단계의 오탐 필터가 그 사이에 들어가는 짝」이라 적은 그대로 |
| **MulVul** (arXiv 2601.18847) | Router가 top-k(k=3) 대분류를 고르고 **그 탐지기만** 호출, 탐지기는 격리. PrimeVul 유형 수준 macro-F1 **34.79 vs LLMxCPG 24.58**(+41.5%). 프롬프트는 **교차 모델 진화**(생성 Claude Opus 4.5 · 실행 GPT-4o)로 수동 대비 +51.6%. 표본당 1+k 호출 | 라우터는 **정적 신호의 CWE 클래스**로 결정론적으로 짤 수 있다(모델 호출 0). 프롬프트 진화는 FT2 전에 시도할 수 있는 싼 대안이고, 채점기는 파이프라인 최종 상태(build-plan §6 FT2 라벨)와 같다 |
| **VulAgent** (arXiv 2509.11523) | 다관점 에이전트 + 집계 + Joern 기반 가설 검증. PrimeVul acc 53.4→58.4 · **오탐률 52.6→36.7** · SVEN 95.2→55.4. 표본당 8.83 호출 · 7,037 출력 토큰 | 가설을 「가정 조건 + 트리거 경로」로 구조화해 **그래프로 검증**하는 것이 오탐을 깎는다 — 우리 6단계 E2(경로 ∧ 그래프 ∧ guard 부재)가 그 자리다 |
| **Revelio** (arXiv 2606.22263) | **싼 모델이 파일당 가설 5개**(Haiku 4.5) → **비싼 모델이 PoV 구성 5회 시도**(Sonnet 4.6) → sanitizer **독립 재실행**으로만 확정. CyberGym 100건: 175건 vs Claude Code 55 · 재현율 69% vs 53% · **오탐 0% vs 49/60/28%** · $9.10/건 | Scout(8B, 배치 386 tok/s **[실측 · PR #142]**)가 「싼 가설 생성기」, 큰 모델이 「비싼 확인기」라는 배치가 **우리 장비의 대역폭 특성과 정확히 맞는다** |

**Scout 설계 판정.** 멤버는 **같은 8B 서버의 프롬프트 전문가**다(모델을 늘리면 상주가 늘고 배칭이 깨진다).
라우터는 정적 신호 CWE 클래스 → top-k 멤버(첫 실측은 k=전체로 기준선을 만든 뒤 줄인다). 출력은 후보당
`fired_by` 집합과 멤버별 `reason_codes`. **오탐 필터(L1.5)는 선택이 아니라 필수**다(전문가 붕괴).
슬라이스 판별기(입력 표현이 다른 넷째 채널)는 [2026-09-06-indexer-dual-slicing.md](2026-09-06-indexer-dual-slicing.md)의
`Slice.origin`을 그대로 받는다.

---

## 5. 두 모델을 **동시에** 돌리면 빨라지나 — 아니다 [계산·추론]

GB10은 273 GB/s 대역폭에 디코드가 묶인 장비다([model-serving.md](../model-serving.md) §1). 우리 30B 배칭 곡선은
캐시를 끈 실측에서 **동시성 16에서 포화**(21.96 → 35.23 → 39.77 → 39.64 tok/s, 이득 1.81배) **[실측]**
([2026-09-02 보고서](../experiments/harness/2026-09-02-bench-concurrency-atom.md)). 두 서버를 같은 GPU에 함께 두면
둘이 **같은 273 GB/s를 나눈다** — 총량이 늘 이유가 없다. 외부 실측도 같은 방향이다 [출처주장]: MPS로
메모리 집약 커널 둘을 공동 실행하면 **둘 다 최대 2.5배 느려지고**(MIG는 2배 이하), 원인은 *"shared memory
contention within the MPS service"*(arXiv 2604.22430). 단 그 논문은 LLM 디코드를 직접 재지 않았다.

→ **역할 안의 두 모델은 순차로 돈다** — A가 후보 전량을 동시성 16으로 처리 → 교대 → B가 전량. 교대 1회는
3~9초 **[실측·계산]**([2026-09-04-swap.md](2026-09-04-swap.md) §6.2). 순차의 대가는 종단 지연이고 배치 분석기에는
문제가 아니다([build-plan.md](../build-plan.md) §4). 그리고 순차는 **상주 메모리를 절반으로** 줄여 한 번에 하나만
깨어 있으면 되는 만큼 **더 큰 모델**을 각 슬롯에 넣을 수 있게 한다([2026-09-06-harness-architecture.md](2026-09-06-harness-architecture.md) §4).
두 서버 동시 실행이 정말 총량을 안 늘리는지는 **30분짜리 bench 한 판**으로 확정된다(같은 문서 §7 #1).

---

## 6. 문헌이 답하지 않는 것 — 우리가 재야 하는 것

| 구멍 | 가장 가까운 문헌 | 왜 이식이 안 되나 |
| --- | --- | --- |
| 오픈 MoE **둘**의 취약점 과제 오류 상관 | Jury(수학·지식) · Too Consistent(7B~72B dense, 지식) · Semgrep(프론티어 5개) | 과제·크기·아키텍처가 전부 다르다. 공유 오류 하한이 도메인마다 0~14%로 갈렸다 |
| 우리 크기에서 합집합의 정밀도 비용 | MultiVer(Opus 4.5) · 3+1(DeepSeek-V3 + Juliet) | 프론티어 또는 합성 데이터. 프로젝트 규모 실측(arXiv 2601.19239)은 LLM 도구들의 **평균 오발견율 85.3%** · C/C++ 재현율 21.09%를 보고한다 — 우리 크기는 더 나쁠 수 있다 |
| 연쇄 신호(A의 `need_evidence`·게이트 등급)의 품질 | 신뢰도 연쇄 연구들 | 우리 신호는 아직 분포도 없다(D28) |
| Critic 둘의 일치율과 8단계 결과의 관계 | Refute-or-Promote(단일 운영자·Claude/Codex) | 재현 없음 · 재현율 미측정(저자 명시) |
| 두 서버 공동 실행의 총 처리량 | MPS 공동 실행 연구 | LLM 디코드를 안 쟀다 |
| 30B급 모델이 **제로데이를 실제로 찾는가** | Provos(GLM 5.1) · AgentFlow(Kimi K2.5, **1T/32B 활성**) · Semgrep(GLM-5.2 753B) · 시스템 논문(1.2B 모델 · **260줄 합성 대상**) | 성공한 오픈웨이트는 전부 30B-A3B보다 **활성 파라미터가 10배 이상** 크거나, 대상이 장난감이다. **30B-A3B급의 실제 대상 성공 사례는 찾지 못했다** [미확인] |

마지막 행이 이 문서에서 가장 무거운 문장이다. 문헌은 「하네스가 모델보다 크다」를 여러 번 보여 주지만
(§1), 그 하네스들이 든 모델의 하한은 Kimi K2.5 · GLM 5.1이다. 30B-A3B는 **활성 3B**다. 하네스로 되찾을 수
있는 것(오탐 0 — 실행 증명으로 · 재현율 일부 — 다양성·슬라이스·도구 신호로)과 모델이 정하는 것(깊은 추론 ·
익스플로잇 체이닝)을 갈라 적는 일, 그리고 **모델 크기 상한을 어디까지 올릴 수 있는가**는
[2026-09-06-harness-architecture.md](2026-09-06-harness-architecture.md) §1·§4의 몫이다.

---

## 7. 출처

**취약점 탐지 앙상블·다중 에이전트 [출처주장]**
- MultiVer — [arXiv 2602.17875](https://arxiv.org/html/2602.17875)
- Strategic Heterogeneous Multi-Agent Architecture — [arXiv 2604.21282](https://arxiv.org/html/2604.21282)
- GPTLens — [arXiv 2310.01152](https://arxiv.org/html/2310.01152)
- DVDR-LLM "Diverse LLMs vs. Vulnerabilities" — [arXiv 2512.12536](https://arxiv.org/html/2512.12536)
- VulTrial "Let the Trial Begin" — [arXiv 2505.10961](https://arxiv.org/html/2505.10961)
- VulAgent — [arXiv 2509.11523](https://arxiv.org/html/2509.11523)
- MulVul — [arXiv 2601.18847](https://arxiv.org/html/2601.18847)
- From Generalist to Specialist — [arXiv 2408.02329](https://arxiv.org/html/2408.02329)
- Revelio — [arXiv 2606.22263](https://arxiv.org/html/2606.22263)
- Refute-or-Promote — [arXiv 2604.19049](https://arxiv.org/html/2604.19049)
- LLM-based Vulnerability Detection at Project Scale — [arXiv 2601.19239](https://arxiv.org/html/2601.19239)

**오류 상관·판정 패널·토론 [출처주장]**
- LLMs as a Jury — [arXiv 2607.10139](https://arxiv.org/html/2607.10139)
- Too Consistent to Detect — [arXiv 2505.17656](https://arxiv.org/html/2505.17656) · [EMNLP 2025](https://aclanthology.org/2025.emnlp-main.238.pdf)
- Nine Judges, Two Effective Votes — [arXiv 2605.29800](https://arxiv.org/html/2605.29800)
- When LLMs Agree, Are They Right? — [arXiv 2607.08065](https://arxiv.org/html/2607.08065)
- Replacing Judges with Juries (PoLL) — [arXiv 2404.18796](https://arxiv.org/html/2404.18796v1)
- The Cost of Consensus — [arXiv 2605.00914](https://arxiv.org/html/2605.00914)
- Statistical Scouting — [arXiv 2605.09618](https://arxiv.org/html/2605.09618)
- Heterogeneous LLM Debate Under Adversarial Peers — [arXiv 2606.19826](https://arxiv.org/html/2606.19826)
- LM vs LM — [arXiv 2305.13281](https://arxiv.org/abs/2305.13281)

**연쇄 [출처주장]**
- Agreement-Based Cascading — [arXiv 2407.02348](https://arxiv.org/html/2407.02348v4)
- Is Escalation Worth It? — [arXiv 2605.06350](https://arxiv.org/html/2605.06350)

**산업 시스템 [출처주장]**
- Unit 42 NOVA — [The Frontier AI Vulnerability Burst](https://unit42.paloaltonetworks.com/frontier-ai-vulnerability-burst/)
- MDASH — [2026-05-12](https://www.microsoft.com/en-us/security/blog/2026/05/12/defense-at-ai-speed-microsofts-new-multi-model-agentic-security-system-tops-leading-industry-benchmark/) · [2026-06-17](https://www.microsoft.com/en-us/security/blog/2026/06/17/beyond-the-benchmark-advancing-security-at-ai-speed/)
- Sangfor CyberGym 제출 — [GitHub](https://github.com/Sangfor-AI/cybergym-submission-sangfor-ai-v2)
- Provos, Finding zero-days with any model — [provos.org](https://www.provos.org/p/finding-zero-days-with-any-model/)
- Semgrep — [Grounded or Gamed?](https://semgrep.dev/blog/2026/grounded-or-gamed-we-audited-our-own-cyber-benchmark/) · [GLM 5.2](https://semgrep.dev/blog/2026/we-have-mythos-at-home-glm-52-beats-claude-in-our-cyber-benchmarks/)
- AgentFlow — [arXiv 2604.20801](https://arxiv.org/html/2604.20801v1)
- AI Security Policy Should Assess Systems, Not Only Models — [arXiv 2605.09504](https://arxiv.org/html/2605.09504v1)

**서빙 [출처주장]**
- Spatial co-execution on GPUs (MPS·MIG) — [arXiv 2604.22430](https://arxiv.org/html/2604.22430v1)

**저장소 (`origin/main` `ba3c928`)**
- [architecture.md](../architecture.md) §3·§4 · [agent-contracts.md](../agent-contracts.md) §2.3 · [build-plan.md](../build-plan.md) §4·§5·§8
- [2026-09-02 동시성 보고서](../experiments/harness/2026-09-02-bench-concurrency-atom.md) · [2026-09-03 교대 보고서](../experiments/harness/2026-09-03-model-swap-sleep-atom.md)
- [2026-09-04-models.md](2026-09-04-models.md) · [2026-09-04-quantization.md](2026-09-04-quantization.md) · [2026-09-04-swap.md](2026-09-04-swap.md)
- [D24](../open-questions/D/D24.md) · [D28](../open-questions/D/D28.md) · [D59](../open-questions/D/D59.md)
- `src/orchestrator/nodes/critic.py` · `src/orchestrator/gate.py` (`normalize`) · `config/budgets.yaml` (`critic_top_n: 8`)
