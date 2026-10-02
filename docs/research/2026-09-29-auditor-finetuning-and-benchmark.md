# Auditor 파인튜닝 · PoV · 벤치마크 조사 — 계획을 문헌과 코드에 대조한다

**조사일 2026-09-29.** 라벨: `[출처주장]`(저자·벤더 자기 측정) · `[제3자]`(독립 측정) ·
`[계산]`(우리 산술) · `[코드]`(이 저장소를 직접 열어 확인) · `[미확인]`.
**여기 있는 것은 실측이 아니다** — 우리 장비 실측은 [experiments/](../experiments/README.md)에 있다.
(§5의 `[계산]`만 예외로 **입력이 우리 실측**이고, 그 사실을 같은 문장에 적었다.)

**이 조사가 결정을 내리지 않는다**(CLAUDE.md §3). 벤치마크·조건 설계는 실험팀 소관이고([ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 5),
파인튜닝 방법은 팀 FT 가이드가 든다. 여기 남기는 것은 **계획이 밟는 사실**과 **선택지**다.
특히 §7(라이선스)과 §4(하네스 전제)는 **값이 아니라 가부가 걸린 자리**라 먼저 읽는다.

**전제.** 사용자 지시로 **Phase 6·6.5 완료를 가정**했다 — 실모델 end-to-end run과 단계 단위 교대가 선다는 뜻이다.
그 가정은 §5의 물리적 가능성을 열어 주지만 **§4의 세 전제는 풀지 않는다**(Phase 8은 별개 번호이고 Phase 7 뒤다).

**조사 도구.** firecrawl·exa MCP와 WebSearch/WebFetch를 병렬 조사 에이전트 넷으로 돌렸고,
**핵심 대목은 이쪽에서 파일을 다시 열어 대조했다**(§2.1 규칙 — 남의 요약으로 쓰지 않는다).
**열람한 어느 페이지에서도 에이전트를 겨냥한 지시문은 발견되지 않았다.**

> ⏵ **2026-10-02 — 이 문서의 `[코드]` 서술은 `b52f847`(2026-09-29 00:00 KST, `origin/main`) 트리 기준이다. 그 뒤 main이 움직여 아래 여섯 묶음은 낡았다**(PR #376 리뷰 반영, 원문은 지우지 않았다).
> **그 트리에서는 참이었다** — 2026-10-02에 `b52f847`을 다시 셌다: `src/schemas/enums.py`의 `POV` 0줄 · `pov`가 든 추적 파일 0건 · `src/`의 `cybergym` 0줄 · ADR 0012의 `CyberGym` 0줄 · `config/models.yaml:468`의 Auditor `prompt_template_version: "v1"`.
> 아래는 **`d85ea62`(2026-10-02) main에서 직접 센 값**이고, 해당 줄 끝에 「⏵ 위 시점 노트 ①~⑥」를 달았다:
> ① **「PoV 코드 0줄 · `Role`에 `POV` 없음 · `prompts/pov/`·`nodes/pov.py` 없음 · 선택지 A/B/C 전부 미착수」**(§4.3·§6.2·§9.A 3) → `Role.POV`는 `f1dfde6`(2026-09-30 03:32 KST, `src/schemas/enums.py:89`)부터 있다.
> 다중 홉 툴 루프(`runner._run_pov_hops`, `85249d3`)와 `prompts/pov/v2/`(`a36d008`)는 2026-09-30 22:04 KST에 들어왔고 `git ls-files | grep -i pov`는 6건이다(`prompts/pov/v2/` 셋 · `src/orchestrator/nodes/pov.py` · `src/schemas/pov.py` · `tests/test_pov_loop.py`).
> `STATUS.md` §PoV는 지금 B를 「루프 착지(호스트 전)」로 적는다. `container_tools.py`의 「PoC 실행 파일을 만드는 주체가 아직 없다」 주석은 `:473`으로 옮겨 그대로 있고, 그 뜻(PoC 바이트가 도구 입력으로 아직 안 들어간다)은 `STATUS.md` 「남은 것」이 같게 적는다 — **부분만 낡았다.**
> ② **「`git grep -in cybergym -- src/` 0건」**(§6.1) → 8개 파일 10줄이다(`src/cli.py`·`config.py`·`orchestrator/context.py`·`orchestrator/nodes/retrieval.py`·`orchestrator/runner.py`·`orchestrator/task_loader.py`·`schemas/enums.py`·`schemas/pov.py`). 최초는 `de47057`(2026-09-30 04:19 KST)이다.
> ③ **「Phase 7의 정본에 CyberGym은 없다 · 「Phase 7 = CyberGym」은 아직 저장소 정본이 아니다 · 벤치마크 선택은 실험팀 소관」**(§6.1·§9.A 1·§9.B) → `docs/decisions/0012-harness-canon-v2.md:196`이 `5990dff`(2026-09-30 14:32 KST)부터 「검증 벤치마크 = CyberGym Level 1」을 적는다(@danielcho02 결정 2026-09-30 — 원천 링크는 저장소에 없다고 그 줄이 스스로 적는다). 같은 줄이 벤치마크 **설계**(데이터셋·정답지·조건)는 그대로 실험팀 몫이라 적는다.
> ④ **「PR #327(gdb) 미머지」**(§4.4) → `5cf5c20`(2026-10-01 03:29 KST)으로 main에 머지됐다.
> ⑤ **「미커밋 조사 문서」**(§6.1의 `2026-09-29-cybergym-pov-design.md`) → 그 문서는 PR #376이 처음 저장소에 올린다(`0583ddd`, 2026-10-02 01:47 KST) — 그 PR이 머지되기 전에는 여전히 main에 없다.
> ⑥ **「Auditor 프롬프트가 v1(`config/models.yaml:468`)이고 v4 PR #328이 열려 있다」**(§4.1 ①) → PR #328은 2026-09-29T12:52:38Z(21:52 KST)에 머지됐고 `6e005ef`(2026-09-29 22:54 KST, 제목 「rewire the canonical auditor from prompt v3 to v4」)가 정본 Auditor를 v4로 배선했다.
> 지금 `config/models.yaml`의 `qwen3.8-27b` 항목은 `:403`부터이고 `prompt_template_version`은 `:546`의 `v4`다. 이 문서의 `config/models.yaml:` 줄 인용은 전부 `b52f847` 기준이라 줄 번호가 옮겨 갔다.
> 이 노트가 다루는 것은 위 줄들뿐이다 — 나머지 `[코드]` 서술은 이 PR에서 다시 열지 않았다.

---

## 0. 결론 여덟 — 그리고 각각이 무엇을 막는가

| # | 결론 | 근거 | 무엇이 걸리나 |
| --- | --- | --- | --- |
| **1** | **레퍼런스가 탐지 파인튜닝 문서가 아니다** — CyberGym Level 1 **PoC 생성** 에이전트 리포트다 | §1 | 「파인튜닝 방법 확정」의 근거가 Auditor에 안 걸린다. 그 레퍼런스가 실제로 지지하는 것은 **PoV 역할** 파인튜닝이다 |
| **2** | **SFT 방식이 이 도메인 실측에서 진 쪽이다** — 정답을 주고 근거를 쓰게 하는 것(rationalization)보다 **rejection sampling**이 낫다 | §2.1 | 계획 ②단계를 바꾼다. 정답 위치는 **생성 입력**이 아니라 **채택 필터**로 쓴다 |
| **3** | **RL 설계가 구조적으로 어긋난다** — vul/patch는 preference가 아니라 **정답/오답**이다. 같은 논문에서 GRPO > DPO·ORPO | §2.2 | ORPO/DPO/GRPO 선택이 「취향」이 아니다. 그리고 GRPO는 rollout이 필요해 인프라가 다르다 |
| **4** | **요청 필드에 일곱이 빠졌다** — 그중 **출처 데이터셋·라벨링 방법**이 가장 크다(라벨 정확도가 24%~94%로 갈린다) | §3 | 지금 스펙으로 받으면 학습 데이터의 **1/4~3/4가 오답일 수 있고 그것을 사후에 못 가린다** |
| **5** | **계획 1단계가 규모에서 안 선다** — 실측 단가로 대상 1건당 **6.57시간**, 18,377건이면 **13.8년** | §5 | 하네스 전체를 태우는 방식은 **수백 건이 상한**이다. 우회로가 있고 그 대가가 §5.3이다 |
| **6** | **하네스가 아직 학습 데이터를 못 낸다** — ADR 0012 결정 7의 세 전제 중 **①이 흔들리고 ②가 반쪽, ③이 없다** | §4 | Auditor 프롬프트가 **v1**이고 v4 PR이 열려 있다. 사고 토큰이 기록되지 않는데 Auditor는 **사고를 켠다** |
| **7** | **Anthropic 이용약관이 정면으로 걸린다** — AUP가 *"model distillation ... without prior authorization"*을 금지 항목으로 든다 | §7 | **teacher = Sonnet 4.6 계획 전체의 가부**다. 값이 아니라 승인 문제다 |
| **8** | **CyberGym은 탐지를 안 잰다** — PoV 생성 벤치마크다. 우리 `evaluation.md` §5.1은 탐지 지표다 | §6 | Phase 7에 CyberGym이 들어가면 그것은 **PoV 채점기**다. Auditor 파인튜닝 효과는 그것으로 **직접 못 잰다** |

---

## 1. 레퍼런스 정독 — 그 페이지는 다른 것을 가리킨다 `[출처주장]`

`https://alibaba-velldepth.github.io/writeups/model-track.html`을 **실제로 scrape했다**(성공).

**페이지 제목은 "XeKRung Model on CyberGym"이고, 다루는 것은 취약점 탐지가 아니라 취약점 재현/PoC 생성 에이전트다.**

| 항목 | 페이지가 말하는 것 |
| --- | --- |
| 모델 | `XekRung-1.5-27B-Preview` — 베이스 **Qwen3.8-27B** |
| 성적 | CyberGym **Level 1 성공률 88.9%** (베이스 54.51% 대비 **+34.39%p**), 2026-09-13 리더보드 1위 |
| 2~4위 | GPT-5.5-Cyber 85.6% · GLM-5.3 84.5% · DeepSeek-V4-Pro 83.3% |
| SFT | *"Security-trajectory SFT"* — 취약점 이해·코드 위치 파악·입력 설계·**툴 실행·로그 해석·최종 PoC 선택** 단계를 커버 |
| RL | *"Agentic RL"* + *"Verifiable outcome alignment"* — **빌드/실행/크래시/PoC 검증의 실행 피드백을 보상으로** |
| 그 밖 | *"Failure-sample utilization"* — 실패한 PoC·빌드 실패를 「실패 원인 → 교정 행동」 학습쌍으로 재구성 |
| 평가 설정 | 컨텍스트 256K · self-hosted **FP8** 추론 · task당 최대 28,800초 / **3,000턴** · 네트워크 allowlist |
| 오염 주장 | *"Our training data is entirely benchmark-independent and contains no CyberGym tasks, task-specific reference solutions, patches, PoCs, or other sources of benchmark data leakage."* |

**페이지에 없는 것 — 계획이 그 페이지를 근거로 댈 수 없는 것들이다.**
데이터 **규모** · 하이퍼파라미터(lr·epoch) · **CoT/reasoning 언급 자체** · teacher distillation ·
**GRPO/DPO/ORPO 어느 것도** · 데이터 비율. 전부 **페이지에 없음**이다.

> **그래서 무엇이 달라지나.** 계획서는 *"파인튜닝 방법 확정 · Reference: (이 페이지)"*라 적고 **SFT(Teacher Distillation) → RL**을 든다.
> 그런데 그 페이지는 teacher distillation을 말하지 않고, RL을 **실행 피드백 보상**(빌드·크래시 = verifiable)으로 한다.
> **두 축 다 계획과 다르다.** 그리고 대상이 Auditor(코드 → Finding JSON)가 아니라 **PoV 에이전트**(코드 → PoC 바이트, 툴 루프 3,000턴)다.
>
> ⚠ **다만 이 페이지는 다른 자리에서 매우 크게 걸린다.** 베이스가 **Qwen3.8-27B — 우리 Auditor와 같은 모델**이고
> (PROJECT-CONTEXT.md §1 · `config/models.yaml:364-474` `[코드]`), 그 베이스가 **CyberGym Level 1에서 54.51%**라는 값을 든다.
> 그리고 그 위의 파인튜닝이 **PoV 역할**을 88.9%로 올렸다. **이 페이지가 실제로 지지하는 것은 PoV 파인튜닝이다.**

⚠ **88.9%는 {모델 + 그 팀 스캐폴드 + 3,000턴 + 256K}의 값이지 모델 단독이 아니다** —
이 저장소가 반복 확인한 *"스캐폴드가 숫자를 4배 움직인다"*([vulnerability-detection 조사](2026-09-08-vulnerability-detection.md) §3)와 같은 함정이다.
**제3자 재현은 0건이고 리더보드는 자기신고 호스팅이다.**

---

## 2. 계획 3단계를 문헌에 대조한다

**가장 직접적인 근거 하나가 있다.** arXiv **2602.14012** — *"From SFT to RL: Demystifying the Post-Training Pipeline for LLM-based Vulnerability Detection"*(2026).
**우리 과제(취약점 탐지)에서 SFT 방식과 RL 알고리즘을 정면으로 비교한 논문**이고, 계획의 ②③단계 둘 다에 답을 준다. `[출처주장]`

### 2.1 ②단계 — 정답을 teacher에게 주면 안 된다

계획 ②: *"그 입력 + **정답위치**를 teacher(Sonnet 4.6)한테 줘서 → SFT용 output(Finding)을 생성시킨다."*

이 방식에는 이름이 있다 — **rationalization**(정답을 주고 근거를 쓰게 함).
반대편은 **rejection sampling**(정답을 **안 주고** N회 샘플링해 정답과 일치하는 것만 채택)이다.

> arXiv 2602.14012 그대로: *"SFT with rejection-sampled CoT data is preferred over rationalization,
> as rationalization introduces **hallucinated vulnerability information from teacher models, which misleads learning**."* `[출처주장]`

**반대 방향 근거도 있다** — LLMVulExp(arXiv 2406.09701)는 정답 CWE를 준 teacher 설명(All-Positive 90.3%)이
모델 자체 생성(41.6%)보다 **낫다**고 보고한다 `[출처주장]`. ⚠ 그러나 그 논문은 **그로 인한 일반화 저하를 측정하지 않았다** —
즉 "학습 데이터가 더 그럴듯하다"이지 "학습 결과가 더 낫다"가 아니다. 2602.14012는 **학습 결과**를 비교했다.

**같은 논문의 부수 발견 — 계획의 epoch 수에 직접 걸린다.**
*"excessive SFT"*(5 epoch)는 이후 GRPO의 self-exploration을 억제해 **P-Pass@1이 3 epoch 대비 9.1% 하락**한다 `[출처주장]`.
반대로 DPO·ORPO 같은 off-policy preference 최적화는 SFT를 강하게 할수록 이득이 계속 는다.

> **계획의 「최대 3 epoch」는 우연히 맞다** — 단, **그 뒤에 GRPO를 둘 때만** 그렇다. DPO/ORPO를 둘 거면 3은 낮은 쪽이다.

**teacher 출력 검증 선례.** TitanVul/BenchVul(ACM 2026)은 다중 에이전트 교차검증으로
304,726쌍 → 38,548개 검증 취약 함수로 정제하고 수동 감사 **validity rate 94%**를 보고한다 `[출처주장]`.

### 2.2 ③단계 — vul/patch는 preference가 아니다

계획 ③: *"vul-input/teacher-output ↔ patch-input/**"no vuln"** 쌍으로 RL preference pair를 만든다."*

**이것은 preference(더 나음/못함)가 아니라 정답/오답(correct/incorrect)이다.** DPO·ORPO의 수학적 프레임(Bradley-Terry)은
*"사람이 절대 평가엔 서투르고 상대 비교엔 능하다"*는 전제 위에 있고, **정답이 이미 검증 가능한 경우**에 그 프레임을 씌울 이유가 없다.
RLVR 문헌이 그 구분을 명시한다 — *"RLVR focuses on tasks where correctness is binary and objectively verifiable"*(arXiv 2503.06639) `[출처주장]`.

**그리고 이 도메인의 직접 실측이 그 구분을 확인한다.**

> arXiv 2602.14012: *"on-policy RL with **GRPO consistently outperforms** supervised fine-tuning (SFT),
> **off-policy preference optimization methods [DPO, ORPO]**, and specialized VD LLMs."* `[출처주장]`

| 알고리즘 | 필요한 것 | 이 과제에서 |
| --- | --- | --- |
| **DPO** | (prompt, chosen, rejected) 쌍 + **frozen reference model**(메모리 2벌) · β 0.01~0.5(기본 0.1) | 정답/오답을 선호로 우겨넣어야 한다 |
| **ORPO** | SFT NLL + odds-ratio를 **한 단계**로, reference 불필요 | ⚠ *"SFT 없이 단독"은 과장* — 실무 문헌은 *"always start ORPO from an SFT checkpoint"*라고 단서를 단다 `[출처주장]` |
| **GRPO** | **reward function**(rule-based verifiable 또는 judge). reference·critic 불필요. 프롬프트당 그룹 G(전형 8) **rollout** | 정답 위치가 있으므로 reward를 직접 쓴다. **연산비용 DPO 대비 ≈3~5배** `[출처주장, 단일]` |

⚠ **「검증 가능한 보상이면 무조건 GRPO」는 아니다.** 일반 추론(MATH-500)에서는 짧은 GRPO warm-up 후 오프라인 DPO(G2D)가
**4~7배 적은 연산으로 동등 이상**을 낸 사례가 있다(Qwen2.5-7B, 62.4% vs GRPO 51.6%) `[출처주장, 단일]`.
**도메인 특정 실측(2602.14012)이 취약점 탐지에서 GRPO 우위를 보였다**가 정확한 서술이다.

**⚠ 그리고 보상 설계가 붕괴를 부른다 — 우리 데이터 비율에 정확히 걸린다.**
같은 논문: *"coarse binary reward"*(정답/오답만)는 reward hacking을 유발해
모델이 **거의 모든 샘플을 「안전」으로 예측**하는 지름길을 학습한다. root-cause 기반 fine-grained reward가 그것을 막는다 `[출처주장]`.
별도 실측도 같은 방향 — 보상을 `0.5·format + 0.5·correctness`로 단순 합산하면 **한 라벨로만 답하는 붕괴**가 난다 `[미검증, 단일]`.

**⚠ 평가 방식에도 같은 논문이 경고를 단다.** detection-level(이진 정답) 비교는 성능을 크게 과대평가한다 —
정답 138건 중 **88.4%가 CWE 레벨에서는 오답**이었고, 정답 CWE 16건 중 **56.3%는 근거가 정답과 무관**했다(우연히 맞춘 틀린 추론) `[출처주장]`.
**우리 `Finding`은 `cwe`와 `rca_draft`·`path`를 함께 내므로 이 축으로 채점할 수 있다.**

**1 epoch RL이 현실적인가 — 자료 없음.** 이 도메인에서 RL epoch 수를 검증한 논문을 찾지 못했다.
2602.14012는 **SFT epoch**만 다루고(3 vs 5), GRPO 쪽은 step 단위로 표현한다. **추정을 제시하지 않는다.**

### 2.3 데이터 비율 — 46:1은 문헌이 붕괴를 예고하는 구간이다

보유분은 정상 527,342 : 취약 11,371 = **46.4:1** `[계산]`.

| 근거 | 내용 |
| --- | --- |
| **포지티브 비율 통제 실험**(arXiv 2412.18260) | 10/20/30/40/50%로 리샘플링. *"positive ratio 30% 이상에서 F1·recall 뚜렷 개선"*, *"positive ratio가 sample length보다 성능에 훨씬 큰 영향"* `[출처주장]` |
| **붕괴 실측**(같은 논문 Finding 1) | **가장 불균형한 DiverseVul에서 모든 모델 성능 최악**, 일부는 recall·precision·F1이 **전부 0**으로 붕괴 `[출처주장]` |
| **반대편 — negative가 적으면** | DiverseVul 논문: *"3.5% false positive rate ... corresponds to hundreds of thousands of functions"* — 낮아 보이는 FPR도 절대 건수로는 검토 불가 `[출처주장]` |
| **실무 관행** | LLMVulExp: train/validation은 **1:1 다운샘플링**, test는 **원래 불균형 유지**(실배포 상황) `[출처주장]` |
| **우리 저장소 정본** | `docs/evaluation.md:84` — *"취약/정상 비율은 **최소 1:1**로 유지한다."* `[코드]` |

> **우리 `evaluation.md`가 이미 1:1을 요구한다.** 그리고 문헌은 학습에서 positive ≥30%를 든다.
> 46:1을 그대로 쓰는 시나리오는 **어느 쪽 근거도 없다.** 527,342건을 전부 쓸 계획이 아니라는 것은 계획서가 이미 말한다 —
> 문제는 **얼마나 쓰느냐가 아니라 비율을 얼마로 두느냐**이고, 그 축이 계획서에 없다.

**「정상 비율을 단계적으로 늘리는」 커리큘럼 — 자료 없음.** 문헌의 커리큘럼은 **난이도 기반**이다
(SPLVD arXiv 2511.09212 · ReVD/PO4Vul ACL 2025 Findings — CWE 타입별 현재 정확도가 낮은 것을 우선, 정확도 +9.08%·F1 +10.33%) `[출처주장]`.

**비율 조정 대신 손실함수로 푸는 축도 있다** — focal·class-balanced·LDAM·logit-adjustment 등 9종 비교에서
**단일 손실함수가 전 지표를 지배하지 않는다**(LDAM precision 최고, LA recall 최고, CB-Focal/LA 종합 최고) `[출처주장]`.

---

## 3. 요청한 데이터 필드 검토 — 맞는 것과 빠진 것

### 3.1 맞는 것 — 근거가 있다

- **레포 URL + 커밋 SHA(전체 체크아웃 가능)** 요구는 **문헌이 강하게 뒷받침한다.**
  - **VulEval**(4,196 CVE · 232,239 함수): 취약 함수의 **20.64%가 함수 밖 의존성(inter-procedural)을 필요로 한다.**
    레포 레벨이 함수 레벨 대비 **F1 +4.91%p · precision +7.43%p · recall +3.38%p** `[출처주장]`
  - **InterPVD**: 769개 중 **24.3%가 inter-procedural**(평균 2.8단계 호출) `[출처주장]`
  - **PrimeVul 저자들 스스로**: *"We recommend that the problem be reformulated so that the model also has access to a broader context."* `[출처주장]`
- **정답 위치(파일+줄, sink/source)** — 필요하다. 다만 **쓰는 용도가 계획과 달라야 한다**(§2.1 — 생성 입력이 아니라 채택 필터).
- **패치 ↔ 취약 커밋 매핑** — PrimeVul이 **5,480쌍**으로 pair-wise 평가를 하는 근거와 같다.
  이게 있어야 「패치본과 취약본을 구별하는가」를 잴 수 있고, JITVUL이 지적한 실패 모드
  (*"models struggle to distinguish between vulnerable code and its patched, benign version"*)를 측정할 수 있다 `[출처주장]`.

⚠ **단, 「체크아웃 가능 = 전부 컨텍스트로 투입」은 아니다.** 2026년 실측(arXiv 2602.06751):
caller/callee/전역변수를 그냥 이어붙이면(naive concatenation) 강한 파인튜닝 모델의 성능이 **오히려 떨어진다** `[출처주장]`.
**우리 하네스는 이미 그 반대로 되어 있다** — Joern 슬라이서가 고르고 `_auditor_source`가 15,000자로 자른다(§4.2).

### 3.2 빠진 것 일곱 — 각각 무엇이 걸리나

| # | 빠진 필드 | 왜 필요한가 | 근거 |
| --- | --- | --- | --- |
| **(a)** | **출처 데이터셋 이름과 라벨링 방법** | **라벨 정확도가 24%~94%로 갈린다.** 이걸 모르면 학습 데이터의 1/4~3/4가 오답이어도 사후에 못 가린다 | 아래 표 |
| **(b)** | **중복 제거 상태**(dedup 여부·정규화 방식) | *"up to **18.9%** test samples are leaked from the train set"*. BigVul 12.7% · CVEfixes 18.9% 중복. **PrimeVul 샘플의 71.1%가 DiverseVul에도 존재** | `[출처주장]` |
| **(c)** | **언어** | 우리 하네스는 **사실상 C 전용**이다 — semgrep 룰이 `harness-c.yaml` 하나고(`tools/harness-tools/rules/`), gdb 계획은 **C/C++ 혼합을 명시적으로 거부**한다(`container_tools.py:414-438`) | `[코드]` |
| **(d)** | **빌드 가능 여부와 빌드 방법** | E4(도구 검증)·`ReproSpec`이 빌드를 전제한다. **실컨테이너 ASAN E4 도달이 저장소에 0건**이고 그 사유가 링크 실패다 | `[코드]` [D92](../open-questions/D/D92.md) |
| **(e)** | **커밋 날짜(authored/committed)** | `docs/evaluation.md:95` **Time Split** — *"기준 시점 이후 데이터는 KB에서 제외"*. 날짜 없이는 못 건다 | `[코드]` |
| **(f)** | **「정상 527,342」의 정의** | **패치 후 코드인가, 무관한 코드인가.** 패치본이면 vul과 쌍을 이뤄 pair-wise 평가가 되고, 무관한 코드면 negative의 성격이 전혀 다르다. **7,006 ≠ 527,342이므로 둘은 다른 것으로 보인다** | §2.3 |
| **(g)** | **정답 위치의 입도** | 우리 `Finding.path`는 **source→propagate→sink 3단계**이고 각 step의 `symbol`이 **원문에 있는 이름**이어야 한다(`prompts/auditor/v1/prefix.md` 규칙 2). 「sink/source 지점」만으로는 propagate가 안 채워진다 | `[코드]` |

**(a)의 근거 — PrimeVul 논문이 50개씩 수작업 검증한 라벨 정확도** `[출처주장]`:

| 벤치마크 | 라벨링 방식 | 정확도 |
| --- | --- | --- |
| SVEN | 수작업 | **94.0%** |
| PrimeVul-NVDCheck | CVE 설명 대조 | 92.0% |
| PrimeVul-OneFunc | 자동+제약 | 86.0% |
| DiverseVul | 자동(커밋 기반) | 60.0% |
| CVEFixes | 자동(커밋 기반) | 51.7% |
| VulnPatchPairs | CodeXGLUE 파생 | 36.0% |
| **BigVul** | 자동(커밋 기반) | **26.9%** |
| CodeXGLUE/Devign | 수작업 | 24% |

그리고 **「패치 커밋에서 바뀐 함수 = 취약」 휴리스틱의 오류율이 직접 측정돼 있다** — CodeXGLUE 수작업 검증에서
**58%가 보안과 무관한 커밋**에서 비롯했고, VulnPatchPairs는 **40%**가 무관했다 `[출처주장]`.

> **이것이 F1 과대평가의 정체다.** StarCoder2가 **BigVul F1 68.26% → PrimeVul F1 3.09%**로 붕괴했다 —
> **같은 모델에서 데이터셋 품질만 바꿨을 때** `[출처주장]`.
> 우리 11,371건이 어느 계열에서 왔는지가 이 두 수 사이 어디에 설지를 가른다. **그 필드가 지금 요청서에 없다.**

---

## 4. 하네스 현황 — 계획이 전제하는 것과 실제 `[코드]`

**기준점 `origin/main` = `b52f847`.** 아래는 전부 그 트리를 직접 열어 확인했다.

### 4.1 ADR 0012 결정 7이 건 세 전제 — 지금 상태

[ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 7이 *"하네스가 지는 것은 학습 자체가 아니라 셋"*이라 적는다.
그 문단은 2026-09-24에 쓰였고 *"오늘 셋 다 없다"*로 끝난다. **다섯 날 뒤인 오늘 다시 세면 하나가 움직였다**(§2.4 규율).

| 전제 | 2026-09-24 ADR 서술 | **2026-09-29 실제** |
| --- | --- | --- |
| ① **학습 입력 = 서빙 입력 형식** | 없다 | **흔들린다.** Auditor 프롬프트가 **v1**이다(`config/models.yaml:468` `prompt_template_version: "v1"`). v2·v3는 디스크에 있으나 미배선이고(`prompts/auditor/v3/meta.yaml` 머리: *"미배선(disk-only)"*), **v4 PR #328이 열려 있다** ⏵ 낡음 — 위 시점 노트 ⑥ |
| ② **모델 원문 기록** | 없다 | **반쪽 충족.** `ModelResult.raw_text`가 생겼고(`src/interfaces/model_client.py`) 어댑터가 채운다(`openai_compat.py:262`). **그러나 그 필드 주석이 스스로 적는다 — *"사고 토큰은 담지 않는다 — 그 자리는 Phase 8이다."*** `finish_reason`·`reasoning`도 그대로 없다. **[D84](../open-questions/D/D84.md) 열림** |
| ③ **내보내기** | 없다 | **그대로 없다.** CLI 서브커맨드는 다섯이다 — `doctor`·`estimate`·`run`·`bench`·`gc`(`src/cli.py:919-957`) |

> **②가 이 계획에 가장 아프게 걸린다.** Auditor는 **`reasoning_effort: xhigh` · `thinking_token_budget: 28672`**로 돈다
> (`config/models.yaml:458·466`). **사고를 켠 모델인데 그 사고를 하네스가 기록하지 않는다.**
>
> 그리고 그 조합이 문헌에서 측정된 최악의 실패 모드와 만난다 — **teacher가 reasoning trace 없이 답만 주면
> 그대로 SFT했을 때 사고 능력이 붕괴한다**(GSM8K **96.5% → 34.5%**). trace까지 채운 distillation만이 99~100% 유효 reasoning을 보존했다 `[출처주장, 단일]`.
> **Sonnet 4.6이 Finding JSON만 내고 사고를 안 내면, 그것으로 학습한 Auditor는 사고를 켠 채 사고를 못 하게 된다.**

### 4.2 Auditor의 실제 입출력 계약

- **입력** = 확장 Context Capsule + `Candidate` + **원본 소스**(ADR 0012 결정 1-b = 넓은 (i)).
  슬라이스 타깃이면 SOURCE는 **슬라이스가 걸친 함수들의 원본**이고, entry를 감싸는 함수가 먼저,
  나머지는 **15,000자 상한** 안에서 함수째 붙는다(`runner._auditor_source` · `_AUDITOR_SOURCE_CHARS`).
  이 상한은 **실서빙에서 쟀다** — 147호출에서 서버가 센 `input_tokens` 최대 **15,437**, 입력 몫 20,480에 여유 5,043토큰, **초과 0건**
  (`runner.py:1795-1800` · [2026-09-26 슬라이스 스모크](../experiments/harness/2026-09-26-slice-smoke-atom.md) §1).
  ⚠ **Scout는 슬라이스 렌더를, Auditor는 원본 함수를 본다** — 둘이 다른 코드를 본다(ADR 0012 「잃는 것」 4).
- **출력** = `Finding` + `MissingEvidence[]`. 필드 **선언 순서가 계약**이다(D51) —
  `finding_id, run_id, candidate_id, state, path[], trigger_status, confidence, rca_draft, cwe, title, evidence_refs, missing_evidence, repro_spec, produced_by`.
- ⚠ **`evidence_refs`는 그 run의 FACTS 블록에 있는 근거 ID만 쓸 수 있다**(프롬프트 규칙 5).
  **그래서 teacher도 같은 FACTS 블록을 통째로 받아야 한다** — 코드 스니펫만 주면 teacher가 쓴 `evidence_refs`가 우리 스키마에서 무효다.
- ⚠ **`trigger_status`는 지금 항상 `null`이다** — 저장소의 어느 프롬프트도 그 필드를 채우라고 시키지 않는다.
- ⚠ **Auditor에는 도구 루프·RAG가 없다.** need_evidence 홉 루프는 **Scout 전용**이다(`runner._run_ne_hops`).

**그리고 그 루프의 실측이 있다**([2026-09-27 NE 스모크](../experiments/harness/2026-09-27-ne-loop-smoke-atom.md), 비교표 밖 — checksum 0):
resolver는 신청서 48건 중 **40건(83%)**에 원본 블록을 돌려줬으나,
**hop0 need_evidence는 84좌석 중 5건(6%)**뿐이고 강제 되묻기로 들어간 10좌석 중 **판정이 바뀐 것은 1건**(그마저 GT와 반대 방향)이었다.
정상 표본에서는 **need_evidence 0/24**다.

### 4.3 PoV — 코드가 0줄이다 ⏵ 낡음 — 위 시점 노트 ①

- `Role` 열거형에 **POV가 없다**(`src/schemas/enums.py` — SCOUT·SCOUT_MEMORY·SCOUT_BOUNDARY·SCOUT_INJECTION·AUDITOR·CRITIC·JUDGE 일곱). ⏵ 낡음 — 위 시점 노트 ①
- `prompts/pov/`·`src/orchestrator/nodes/pov.py` **디렉터리 자체가 없다**. `git ls-files | grep -i pov` **0건**. ⏵ 낡음 — 위 시점 노트 ①
- `container_tools.py:440` 주석이 그대로 적는다 — *"PoC 실행 파일을 만드는 주체가 아직 없다."* ⏵ 부분만 낡음(주석은 `:473`에 그대로) — 위 시점 노트 ①
- **실컨테이너 ASAN E4 도달이 저장소에 0건**이다([D92](../open-questions/D/D92.md)).
- STATUS.md §PoV가 선택지 A(정답 PoC 재생)·B(`Role.POV` + `prompts/pov/v1/` + `nodes/pov.py` 추가)·C(퍼징) 셋을 들고 **전부 미착수**다. ⏵ 낡음 — 위 시점 노트 ①

### 4.4 gdb — 어댑터는 붙었고 파이프라인은 아직이다 (PR #327, 미머지) ⏵ 낡음 — 위 시점 노트 ④

- ToolRunner 지원 도구가 **일곱**이 됐다 — `semgrep`·`clang-tidy`·`build`·`asan`·**`gdb`**·`poc`·`index`.
  실행류(`_RUNTIME_TOOLS`)가 `{"asan", "gdb", "poc"}`로 늘었다.
- ⚠ **`validate.tool_for()`가 gdb를 고르지 않는다** — 8단계가 실제로 gdb를 부르는 경로는 아직 없다.
  이 브랜치의 TDD 문서가 그 사실을 스스로 적는다.
- **ptrace 실측**(D92, 2026-09-28): Docker·rootless Podman 둘 다 **`--cap-add=SYS_PTRACE` 불필요**.
  Podman은 정상/크래시 × 권한 유무 **4회 전부 동일 결과**. Singularity는 **0회 미실측**이라 D92가 **의도적으로 열림**이다.
- **문헌 쪽 선례도 같은 방향**: Google Big Sleep/Naptime이 ASan + 디버거를 루프에 넣고,
  AIxCC 결승 팀 다수가 디버거 서브에이전트를 쓴다 `[출처주장]`.
  ⚠ 알려진 함정은 **디버거 출력의 컨텍스트 오염**이다(Theori 사례에서 60KB 출력이 잘림) `[출처주장, 단일]`.

---

## 5. 계획 1단계의 규모 — 실측 단가를 대입한다 `[계산]`

계획 ①: *"vul/patch 레포+커밋을 실제 harness run에 태워서 → Auditor가 실제로 받을 입력(trace에서 뽑음)을 만든다."*

### 5.1 단가의 원천 — 우리 실측이다

[2026-09-26 슬라이스 스모크](../experiments/harness/2026-09-26-slice-smoke-atom.md)(ATOM, 실서빙):
표본 **3건** → v2 슬라이스 **241개** → Scout 임계 통과 **147개** → Auditor **147호출**.
**Scout 좌석 wall p50 84.6~103.9초**(좌석 3개) · **Auditor 호출 wall p50 15.7초**(최대 310.9초).
그 보고서가 직접 적는다 — *"좌석 호출이 중앙값 85~104초라 241개 × 3좌석이면 계산값으로 **20시간이 넘는다**."*

### 5.2 대입

대상 1건당 = 슬라이스 **80.3개** · Auditor **49.0호출** · 순차 wall **6.57시간**
(Scout 6.36 + Auditor 0.21 — Scout 좌석 p50을 95초로 잡음) `[계산]`

| 규모 | 순차 wall | |
| --- | --- | --- |
| vul+patch 전량 **18,377** | 120,800시간 | **13.8년** |
| vul 전량 **11,371** | 74,746시간 | **8.5년** |
| 1,000 | 6,573시간 | 274일 |
| 300 | 1,972시간 | 82일 |
| **100** | **657시간** | **27일** |

⚠ **가정을 적는다.** ① 표본 3건이 대표적이다 ② 병렬 없음(Phase 6.5의 단계 안 동시 요청이 이 수를 나눈다 — 배수는 미실측)
③ 슬라이서 시간 미포함(#321·#322 성능 작업이 방금 머지돼 단가가 움직였다 — 미실측) ④ 장비 1대.
**그래도 자릿수가 셋 이상 벌어져 결론은 병렬로 안 뒤집힌다.**

### 5.3 우회로와 그 대가

**Scout가 비용의 97%다**(6.36/6.57). 그런데 **우리는 정답 위치를 이미 알고 있다** — Scout가 찾아 줄 필요가 없다.

| 경로 | 무엇을 하나 | 대상 1건당 | 대가 |
| --- | --- | --- | --- |
| **(A) 전체 run** | 계획 그대로 | 6.57h | 규모에서 안 선다 |
| **(B) Scout 생략** | 정답 위치로 슬라이스를 고르고 → `_auditor_source` 렌더 → FACTS 조립 → Auditor 입력 완성 | ≈0.2h + 슬라이서 `[계산, 미실측]` | **①전제가 부분적으로 깨진다** — 서빙에선 Scout가 후보를 고르고 그 신호가 FACTS에 실린다. 학습 입력이 늘 「완벽한 후보」면 분포가 어긋난다 |

> **이 갈림이 계획의 핵심 설계 결정이다.** (B)는 규모를 3자릿수 열지만 ADR 0012 결정 7 전제 ①과 정면으로 맞선다.
> 절충(예: 표본 일부만 (A)로 돌려 (B)의 분포 차이를 **재는** 것)이 있고, **값은 실측으로 정한다.**

---

## 6. 벤치마크 — CyberGym이 재는 것과 우리가 재려는 것

### 6.1 Phase 7의 현재 정본에 CyberGym은 없다 `[코드]` ⏵ 낡음 — 위 시점 노트 ③

**Phase 번호의 유일한 정의처는 [ADR 0012](../decisions/0012-harness-canon-v2.md)의 목록이고, Phase 7은 한 줄이다:**

> *"Phase 7 — 평가 어댑터·채점기·배치 실행."*

`git grep "Phase 7"` 결과 **그 한 줄이 전부**다. `STATUS.md`·`docs/prd.md`·`docs/evaluation.md`에는 「Phase」라는 단어 자체가 **0건**이다. ⏵ 그 줄은 지금도 한 줄이나 「CyberGym 없음」은 낡음 — 위 시점 노트 ③
CyberGym은 `docs/prd.md:11`(비용 보고 규칙 변경, 배경 설명)과 `docs/evaluation.md:163`(로컬 서빙 비용 산정) **두 곳뿐**이고 둘 다 참가 계획이 아니다.
`git grep -in cybergym -- src/` **0건**, `src/`에 평가·채점 모듈 **0건** ⏵ 낡음 — 위 시점 노트 ②
(`eval/leakage/`가 있으나 그것은 §6.3의 누출 검사이지 채점기가 아니다).

> **즉 「Phase 7 = CyberGym」은 아직 저장소 정본이 아니다.** 그 논의는 미커밋 조사 문서 ⏵ 낡음 — 위 시점 노트 ③⑤
> [2026-09-29-cybergym-pov-design.md](2026-09-29-cybergym-pov-design.md)에만 있고,
> **그 문서 자신이 Level 1 vs E2E를 팀 쟁점으로 남긴다**(§1.8). ⏵ 낡음 — 위 시점 노트 ③

### 6.2 CyberGym은 PoV 생성 벤치마크다 — 탐지가 아니다 `[출처주장]`

- 1,507 취약점 · 188 프로젝트 · OSS-Fuzz→ARVO 경유.
- **Level 1** = *"the pre-patch codebase and the text description, i.e., our primary task setting"* — 산출은 **PoC 바이트 파일**.
- **성공 판정** = 패치 전 sanitizer 크래시 **∧** 패치 후 무크래시. 리더보드는 **final-submission** 지표만 인정.
- 레벨별 성공률(OpenHands+GPT-4o): **level 0에서 3.5%**만 성공 — 정보량이 성공률을 크게 가른다.
- 원 논문 최고: GPT-5(thinking) **22.0%** · OpenHands+Claude-Sonnet-4 17.9%. SWE-bench 특화 모델은 **≤2.0%**.

**우리 `docs/evaluation.md` §5.1이 재려는 것은 전혀 다른 축이다** `[코드]`:
Recall@known · Precision(`proven`+`validated` 중 **사람 검토**로 확인된 비율) · **False Positive Rate** ·
AI-Human 일치도 · 단계별 후보 제거율 · 스키마 파싱 실패율. **pass@k는 우리 지표 정의에 없다.**

> **그래서 CyberGym으로는 Auditor 파인튜닝의 효과를 직접 못 잰다.** CyberGym이 채점하는 것은 「PoC가 크래시를 냈나」이고,
> Auditor가 내는 것은 Finding JSON이다. 둘을 잇는 것은 **PoV 역할**인데 그 코드가 0줄이다(§4.3). ⏵ 낡음 — 위 시점 노트 ①
> 그리고 CyberGym 성공률은 **PoV의 기여와 탐지의 기여를 못 가른다** — 교락이다.

⚠ **그리고 우리 `proven`이 「크래시 재현」을 뜻하지 않는다**(`docs/evaluation.md:114-122`) —
`container_tools.judge()`가 런타임 도구의 exit 0(=크래시 없음)도 `ok=True`로 올리고,
산출물을 `expected_signal`과 대조하는 코드가 **없다**. [D80](../open-questions/D/D80.md) 열림.
**CyberGym 판정과 우리 `proven`은 같은 말이 아니다.**

### 6.3 오염 — 이미 정본이 요구하는데 구현이 2/7이다

`docs/evaluation.md` §4가 **일곱 기준**을 든다 — Train/Test 분리 · CVE ID Masking · Patch Leakage 방지 ·
**중복 데이터 제거(함수 단위 digest)** · **Time Split** · Prompt Logging · Reproducibility.
그리고 *"한 건이라도 실패하면 해당 run을 무효 처리한다"*고 적는다.

**구현된 것은 `eval/leakage/`의 둘**(`hash_intersection.py`·`id_masking.py`)이다 `[코드]`.

> **파인튜닝이 이 표에 새 행을 만든다.** 지금까지 「Train」은 KB 인덱싱 대상이었는데, 파인튜닝이 들어오면
> **모델 가중치 자체가 Train이 된다.** CyberGym이 OSS-Fuzz/ARVO 계열이고 학습 데이터도 공개 CVE 계열이면 겹칠 수 있고,
> **가중치에 들어간 오염은 hash 교집합 검사로 못 잡는다.**
> §3.2 (b)(중복 상태)와 (e)(커밋 날짜)가 그 검사의 입력이다 — **지금 요청서에 없다.**

### 6.4 탐지를 재려면 별도 벤치마크가 필요하다 `[출처주장]`

PrimeVul(pair-wise **5,480쌍**) · VulDetectBench · CASTLE · SecVulEval 등이 탐지 축이다.
PrimeVul은 F1 대신 **VD-Score**(FPR을 임계 이하로 고정한 뒤 FNR 측정)를 제안한다 — 실배포의 비대칭을 F1이 못 담아서다.

> **우리 데이터에 패치본 7,006건이 있다는 것이 여기서 자산이 된다** — 취약/패치 쌍이 곧 pair-wise 평가셋이다.
> 매핑 필드는 요청서에 **이미 있다**. §3.2 (f)만 풀리면 된다.

---

## 7. ⛔ Anthropic 이용약관 — 값이 아니라 가부가 걸린다 `[출처주장, 원문]`

**teacher = Claude Sonnet 4.6으로 학습 데이터를 만드는 계획 전체에 걸린다.**

- **Usage Policy(AUP)**가 금지 항목으로 든다 —
  > *"Utilization of inputs and outputs to train an AI model (e.g., 'model scraping' or 'model distillation') **without prior authorization from Anthropic**"*
- **Commercial Terms**가 별도로 든다 —
  > *"train competing AI models ... **except as expressly approved by Anthropic**"*
- Help Center는 **특화 분류기는 허용, 범용 챗봇/경쟁 모델 학습은 금지**로 구분한다.

> **이 프로젝트(취약점 탐지 특화 도구)가 어디에 해당하는지는 문서 간 긴장이 있다.**
> AUP의 "distillation without prior authorization"은 용도를 안 가리는 문면이고, Help Center의 "특화 분류기 허용"은 가린다.
> **이 조사가 그것을 판정하지 않는다.** 확실한 것은 **AUP가 「사전 승인」이라는 단어를 쓴다**는 것이고,
> 그러면 안전한 경로는 하나다 — **Anthropic에 사전 서면 승인을 요청한다.**
>
> ⚠ **그리고 이것은 이 조사 결과 중 유일하게 「나중에 고칠 수 없는」 항목이다.** 데이터를 먼저 만들고 나서 약관을 보면
> 이미 만든 데이터가 문제가 된다. **§2~§5의 어느 설계보다 먼저 풀어야 한다.**

**대안 teacher.** 라이선스가 학습 사용을 허용하는 오픈 가중치 모델로 teacher를 바꾸면 이 항목이 사라진다
(우리 registry의 `qwen3.8-27b`는 `license: "Apache-2.0"`이다 — `config/models.yaml:455` 위 `[코드]`).
**대가는 teacher 품질이고, 그 차이는 미실측이다.**

---

## 8. 학습 인프라 — 실무 수치 `[출처주장]`

| 항목 | 내용 |
| --- | --- |
| **27B LoRA** | H100/A100 **80GB 1장**으로 충분(BF16 베이스 ~54~64GB + 어댑터). QLoRA는 24~32GB로도 가능 |
| **27B full FT** | 1B당 ≈16GB 룰 → **≈400GB급**. A100 80GB **4~5장** + ZeRO-3/FSDP |
| **GB10 / DGX Spark (128GB)** | **27B 파인튜닝 1차 실측 사례를 못 찾았다** `[미확인]`. 대역폭이 H100의 약 1/12이라 wall-clock이 크게 느릴 것으로 **추정** |
| **프레임워크 · GRPO** | TRL · Axolotl · ms-swift · veRL · OpenRLHF · Unsloth가 지원. **LLaMA-Factory는 GRPO 미지원** |
| **vLLM rollout** | veRL(대규모) · TRL+Unsloth(소규모). ms-swift는 **Qwen3.8 공식 지원** |
| **Sonnet 4.6 가격** | **$3 / $15** per MTok(in/out). **Batch API $1.50 / $7.50** |
| **1만 건 생성 비용** | 표준 **$225~$825**, Batch **$113~$413**(슬라이스 크기 가정에 따라) `[계산]` |
| **SFT 권고** | 2~3 epoch + validation 조기 종료. LoRA lr **2e-4~5e-4**, full **1e-5~2e-5** |
| **GRPO 비용** | rollout 때문에 SFT 대비 VRAM **2~3배** `[미검증, 단일]`. wall-clock 배수는 **자료 없음** |
| **DPO 실패 모드** | **likelihood displacement** — margin이 너무 작거나 클 때 chosen·rejected **둘 다 확률이 떨어진다** |

⚠ **우리 저장소 정본이 이미 학습 장소를 정해 뒀다** — [build-plan.md](../build-plan.md) §6:
*"학습 장소는 **ATOM이 아니다.** aarch64 세금과, 메모리를 서빙이 쓴다는 두 이유다."* `[코드]`

---

## 9. Phase 7에 넘기는 사실 — 그리고 선택지

**이 조사는 Phase 7을 고치지 않는다.** 아래는 고칠 때 쓸 사실과, 갈림길에 선 선택지다.

### 9.A 직접 반영할 사실 (전부 `[코드]` 또는 출처 확정)

1. **Phase 7의 정본 문장에 CyberGym이 없다**(§6.1). 넣으려면 ADR을 고치는 일이고 그 결정권은 팀이다. ⏵ 낡음 — 위 시점 노트 ③
2. **CyberGym 채점기 ≠ 탐지 채점기**(§6.2). Phase 7이 「평가 어댑터·채점기」라면 **둘이 필요하다** — 지금 `src/`에 어느 쪽도 없다.
3. **PoV 역할은 코드 0줄**(§4.3). CyberGym 채점기를 지어도 **먹일 것이 없다**. 선택지 A/B/C가 STATUS.md에 있고 전부 미착수다. ⏵ 낡음 — 위 시점 노트 ①
4. **우리 `proven`은 크래시 재현을 뜻하지 않는다**(§6.2, [D80](../open-questions/D/D80.md) 열림). CyberGym 판정과 같은 말로 쓰면 안 된다.
5. **누출 검사 7개 중 2개만 구현**(§6.3). 파인튜닝이 들어오면 **가중치 오염**이라는 새 행이 생기고 hash 검사로는 안 잡힌다.
6. **Auditor 입력 상한 15,000자는 실측으로 지켜졌다**(147호출 · 초과 0 · 여유 5,043토큰, §4.2). 학습 데이터도 같은 상한을 써야 ①전제가 산다.
7. **CyberGym 제출 프로토콜은 이미 저자 코드로 확정돼 있다** — 최종 후보 단일 지정 · 실행 10초 · 20건/분 · PoC ≤10MB
   ([2026-09-29 PoV 설계 조사](2026-09-29-cybergym-pov-design.md) §1). Phase 7의 어댑터가 그 규격을 따른다.

### 9.B 갈림길 — 값은 실측·팀 판단

| 갈림길 | 선택지 | 무엇이 가르나 |
| --- | --- | --- |
| **teacher** | Sonnet 4.6(승인 필요) ↔ 오픈 가중치 | **§7. 승인 없이는 진행 자체가 위험** |
| **SFT 데이터 생성** | rationalization(계획 현재) ↔ **rejection sampling** | §2.1 — 도메인 직접 실측이 후자 |
| **RL** | DPO/ORPO(계획 현재) ↔ **GRPO + fine-grained reward** | §2.2 — 같은 논문이 GRPO 우위. 단 인프라가 다르다 |
| **입력 생성 경로** | (A) 전체 run ↔ (B) Scout 생략 | §5.3 — 규모 3자릿수 ↔ ①전제 |
| **학습 대상 역할** | Auditor(계획 현재) ↔ **PoV** ↔ scout_memory | §1 — 레퍼런스가 지지하는 것은 PoV. 그리고 PROJECT-CONTEXT §1이 **scout_memory 자리에 파인튜닝 Qwen3-14B를 쓸 계획**이라 적는다 |
| **벤치마크** | CyberGym Level 1 ↔ E2E ↔ 탐지 벤치(PrimeVul 등) ↔ **둘 다** | §6 — ADR 0012 결정 5상 실험팀 소관 ⏵ 선택은 낡음(ADR 0012:196) — 위 시점 노트 ③ |

### 9.C 이 조사가 제안하는 선행 (만드는 것은 이 조사가 아니다)

1. **§7 라이선스 확인** — 다른 무엇보다 먼저. 데이터를 만든 뒤에는 못 되돌린다.
2. **팀에 §3.2의 일곱 필드를 추가 요청** — 특히 (a) 출처 데이터셋·라벨링 방법, (f) 「정상」의 정의.
3. **[ADR 0012](../decisions/0012-harness-canon-v2.md) 열린 항목 7 「Auditor SFT가 배울 것」**(기한 2026-10-04, 결정권 팀)을 닫는다 —
   **이 계획 전체가 그 항목의 답을 전제하는데 그 항목이 아직 열려 있다.**
4. **(B) 경로의 분포 차이 실측** — 표본 일부를 (A)로 돌려 Scout 신호 유무가 Auditor 출력을 얼마나 바꾸는지.

---

## 10. 이 조사가 확인하지 못한 것

- **1 epoch RL의 현실성** — 이 도메인에서 RL epoch을 검증한 논문 없음. **추정을 제시하지 않았다.**
- **「정상 비율을 단계적으로 늘리는」 커리큘럼** — 자료 없음. 문헌의 커리큘럼은 난이도 기반이다.
- **단일 권장 negative:positive 비율** — 합의 없음. positive ≥30%가 이득이라는 것만 확인.
- **GB10/DGX Spark에서 27B 파인튜닝 1차 실측** — 없음. 대역폭 근거의 **추정**만 있다.
- **GRPO vs SFT의 정확한 wall-clock 배수** — 자료 없음(VRAM 2~3배는 단일 출처).
- **슬라이서의 대상 1건당 단가** — #321·#322가 방금 머지돼 값이 움직였다. §5.3 (B) 경로의 실제 비용은 **미실측**.
- **11,371/7,006/527,342의 출처 데이터셋** — 팀에 물어야 한다(§3.2 (a)). 그것이 §3.2 표의 어디에 설지를 가른다.
- **SecureFalcon의 정량 성능** — 검색 스니펫으로 미확인.
- **feyospace-v1-small의 독립 측정** — 0건. 벤더 자기신고뿐이다([기존 조사](2026-09-29-cybergym-pov-design.md) §6과 같다).
- **VellDepth 88.9%의 제3자 재현** — 없음. 리더보드는 자기신고 호스팅이다.
- **열람한 어느 페이지에서도 에이전트를 겨냥한 지시문(prompt injection)은 발견되지 않았다** — 4개 조사 스레드 전부 동일 보고.
