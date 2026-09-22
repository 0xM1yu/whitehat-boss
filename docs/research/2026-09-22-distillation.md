# Fine-tuning을 위한 distillation 조사

조사일 **2026-09-22** · 목적 **FT 이해를 위한 개념 정리 + 우리 파이프라인 적용 지점**

이 문서는 **개념·문헌 정리다. 실측이 아니다** (README·CLAUDE.md §0). 여기 기법의 수치·효과는
전부 「출처의 주장」이고 우리 장비·우리 과제에서 재현된다는 보장이 없다.

## 0. 근거 등급

| 표시 | 뜻 |
| --- | --- |
| **[정의]** | 용어의 통용 정의 |
| **[문헌]** | 논문·벤더 문서가 하는 주장. 우리가 재지 않았다 |
| **[우리계획]** | 우리 파이프라인에 어떻게 붙일지의 구상. 아직 안 지음 |
| **[미확인]** | 확정 못 함 |

---

## 1. 용어부터 가른다 — 셋이 서로 다르다

`distillation`이 한 물건이 아니다. 세 개가 섞여 불린다.

| 용어 | 무엇을 압축하나 | 산출물 |
| --- | --- | --- |
| **knowledge distillation (KD)** [정의] | 큰 모델의 **행동**을 작은 모델로 | 학습된 student 가중치 |
| **dataset distillation (DD)** [정의] | 큰 **데이터셋**을 소수 합성 샘플로 | 작은 합성 데이터셋 |
| **distillation-via-data / distillation for FT** [정의] | 큰 모델의 행동을 **데이터를 매개로** 작은 모델로 | teacher가 만든 학습셋 + student 가중치 |

- **teacher/student로 주고받는다**고 말할 때 뜻하는 것은 대개 **KD** 또는 **distillation-for-FT**다. 이 문서는 그 둘을 다룬다.
- **dataset distillation(DD)**은 별개다 -> "MNIST 6만 장을 10장으로 줄여도 그 10장으로 학습하면 성능이 나온다" 류의 **데이터 압축** 연구다. teacher/student 프레임이 아니다. 혼동 주의.
- 실무 LLM에서 "distill 했다"는 보통 **teacher가 출력을 대량 생성 -> 그 출력으로 student를 SFT** 라는 뜻이다(distillation-for-FT). `deepseek-r1-distill-*`·`gpt-oss` 계열이 그 산출물의 예다 [문헌].

---

## 2. teacher ↔ student가 주고받는 정보

student가 teacher의 **무엇을** 흉내내느냐로 갈린다. 위로 갈수록 신호가 진하고, 아래로 갈수록 배선이 쉽다.

| 층위 | teacher가 주는 것 | student의 loss | 특징 |
| --- | --- | --- | --- |
| **soft label (logit-level)** | 토큰별 확률분포(soft target), temperature T로 부드럽게 | KL(student‖teacher) | 「정답 토큰」뿐 아니라 **오답들의 상대 순위**까지 전달 -> 신호 가장 진함. 단 teacher logit 접근 필요(온프렘/오픈웨이트라야) [문헌] |
| **hard label / sequence-level** | 생성한 **텍스트 출력**(정답 시퀀스) | cross-entropy(표준 SFT) | teacher를 API로만 써도 됨. 지금 LLM distill의 주류. 신호는 soft보다 성김 [문헌] |
| **rationale / CoT** | 최종답 + **추론 흔적**(왜 그 답인지) | CE(추론+답) | 작은 student가 추론 능력을 얻는 핵심 -> "reasoning distillation". 답만 주는 것보다 작은 모델에서 이득 큼 [문헌] |
| **preference / ranking** | 후보 여럿의 **선호 순서**(A>B) | DPO·보상모델 | teacher를 판정자로 씀. RLHF 라벨 비용을 teacher로 대체 [문헌] |

핵심: **soft label은 "분포"를, hard label은 "샘플"을 준다.** soft가 진하지만 teacher 내부(logit)를 열 수 있어야 하고, hard는 어떤 teacher든 되지만 정보가 준다.

T(temperature): soft label을 낼 때 분포를 평평하게 만드는 값. T가 크면 오답들 간 순위 정보가 더 산다. 학습 후 추론은 T=1 [문헌].

---

## 3. 왜 이원화하나 (teacher ≠ student)

한 모델로 안 하고 굳이 둘로 가르는 이유.

1. **능력 -> 크기 압축.** 큰 teacher의 능력을 배포 가능한 작은 student에 옮긴다. student를 **직접** 그 성능까지 학습시키긴 데이터·비용이 크다 [문헌].
2. **배포 제약(우리에 직접).** teacher는 한 번 크게 돌려 데이터만 만들면 끝. **서빙은 작은 student**로 -> 온프렘/저사양에 올라간다. 우리 24GB Ampere 제약이 이 자리다(docs/portability.md·PROJECT-CONTEXT §7).
3. **라벨 비용.** 사람 라벨 대신 teacher가 supervision을 만든다 -> 라벨링을 GPU 시간으로 바꾼다.
4. **latency·비용.** 추론 때 큰 모델을 안 부른다.
5. **capacity gap 관리.** teacher가 너무 크면 student가 못 따라간다("capacity gap") -> 중간 크기 teacher가 더 잘 옮겨지는 경우가 있다. 무조건 큰 teacher가 답이 아니다 [문헌].

**주의: 이원화의 상한은 teacher다.** student는 teacher를 흉내내므로 **teacher가 틀리면 그 틀림까지 배운다.** teacher의 hallucination = student의 학습 노이즈. 그래서 §4의 필터가 온다.

---

## 4. 그 외 알아둘 것

- **teacher 노이즈 -> 검증 필터가 필수.** teacher 출력을 그대로 쓰지 않고 **정답이 확인된 것만** 남겨 학습(rejection sampling / verification-filtered distillation). 우리엔 이게 특히 중요하다(§5) [문헌].
- **on-policy vs off-policy.** off = teacher가 미리 만든 고정셋으로 학습(쉬움, 분포 어긋남 가능). on = student가 뽑은 출력을 teacher가 그 자리에서 채점(분포 정합↑, 배선 복잡). 근래 on-policy가 작은 모델에서 낫다는 보고 [문헌].
- **분포 shift / exposure bias.** student가 배운 분포와 실제 추론 입력이 다르면 성능이 빠진다. 학습 데이터가 **실제로 만나는 입력**을 닮아야 한다.
- **mode collapse / over-distillation.** teacher를 과하게 흉내내면 다양성을 잃고 teacher의 특정 버릇에 붙는다. student가 teacher를 **넘지 못하는** 상한도 여기서 온다.
- **eval leakage.** teacher가 만든 학습셋에 **평가셋 정답이 섞이면** 성능이 부풀려진다. 우리 release GT가 학습에 새면 그 뒤 모든 수치가 오염된다(#219 계열 평가셋, CLAUDE.md §0).
- **teacher 출력 라이선스.** 일부 상용 API는 출력으로 경쟁 모델 학습을 금한다. teacher 선택 시 확인 대상.
- **self-distillation / born-again.** teacher와 student가 같은 크기여도 이득이 나는 경우(정칙화 효과). 이원화의 반례지만 알아둘 것 [문헌].
- **재측정 규칙.** distill로 student가 바뀌면 그것은 **새 모델**이다 -> checksum·성능을 다시 잰다(evaluation.md §2.1 — 재현성 기록 축).

---

## 5. 우리 프로젝트에서 어떻게 적용되나 [우리계획]

우리는 이미 **역할별로 큰/작은 모델을 가르는** 구조다(scout·auditor·critic, PROJECT-CONTEXT §1이 정본 — 모델명은 거기서 본다). distillation은 이 구조에 자연스럽게 붙는다.

### 5.1 무엇이 teacher, 무엇이 student가 되나

- **teacher 후보**: 대형(Vast RTX PRO 6000 96GB의 70B급) 또는 강한 추론 모델. 온프렘엔 못 상주하지만 **데이터 생성용으로 한 번** 돌리는 것은 됨.
- **student 후보**: 온프렘 상주하는 작은 역할 모델(24GB Ampere 제약 -> 8B~30B급). ATOM 종료 후 대형은 Vast, 소형은 Seraph/온프렘(docs/portability.md).
- 이미 로스터의 일부가 **남이 distill한 산출물**이다(`deepseek-r1-distill-*`·`gpt-oss` 계열) -> 우리는 그것을 **쓰는** 쪽이면서, 우리 과제로 **다시 distill하는** 쪽도 될 수 있다.

### 5.2 우리 파이프라인 산출물이 곧 distillation 원천

우리 run이 이미 teacher-supervision 모양의 데이터를 만든다.

| 단계 산출물 | distillation 신호로서 | 층위(§2) |
| --- | --- | --- |
| Scout `Candidate` | 입력 -> 후보 라벨 | hard label |
| Scout `ScoutResponse.analysis_summary` | **왜 그 후보인지(근거)** — 하네스가 지금 **버린다**(`nodes/scout.py`) | **rationale/CoT** |
| Auditor `Finding`(+rca_draft) | 취약 판정 + 근본원인 서술 | hard + rationale |
| Critic `DecisionDraft` | 반박·need_evidence 판정 | preference/판정 |
| 슬라이서 후보 + release GT | 입력(코드 슬라이스) -> 정답(취약 위치) | hard label(검증됨) |

-> **`analysis_summary`가 핵심.** 지금은 소비자가 없어 버리는데(scout.py 주석), **reasoning distillation의 teacher rationale로는 정확히 그 값이 쓰인다.** distill을 할 거면 이걸 버리지 말고 적재하는 것이 선행.

### 5.3 verification-filtered가 우리에겐 공짜에 가깝다

§4의 "검증된 것만 학습"이 우리 구조에 이미 있다 -> **Evidence Gate(E0~E4)·Critic 판정**이 그 필터다. teacher가 낸 후보 중 **게이트를 통과한 것(근거 확인)** 만 student 학습셋에 넣으면 teacher 노이즈를 구조적으로 거른다. 단 지금 Critic이 metadata-only라 판정이 약한 것은 별개 제약(#237 HIGH-1 — critic/v3를 아직 안 켠 이유).

### 5.4 반드시 지킬 것

1. **eval leakage 차단.** release GT는 **평가 전용**이다. distill 학습셋에 그 샘플이 섞이면 그 뒤 모든 비교가 죽는다(CLAUDE.md §0). 학습셋과 평가셋의 분리를 run 기록으로 증명해야 한다.
2. **재현성.** distill로 만든 student는 새 모델 -> `RunRecord`의 `model_checksums`·`prompt_versions`에 그 버전이 박혀야 하고, teacher·데이터 생성 조건도 남긴다(engineering-guide 재현성 축).
3. **teacher를 실측 기준으로 오해 금지.** teacher 출력은 **주장**이지 정답이 아니다. student 성능은 release GT로 **따로** 잰다 -> teacher를 잘 흉내냈다 ≠ 취약을 잘 찾는다(슬라이서 「포착≠연결」과 같은 논법).

---

## 6. 열린 질문 (다음)

- teacher를 무엇으로? 대형 상주 불가 -> **데이터 생성 배치**로만 Vast에서 돌리는 파이프라인이 필요(비용·라이선스 확인).
- 어느 역할부터? Scout(후보 생성)가 rationale이 이미 나오므로 진입점 후보. Auditor/Critic은 판정이라 검증 필터와 궁합.
- soft vs hard? 우리 teacher가 오픈웨이트면 soft(logit) 가능 -> 신호가 진함. API teacher면 hard만.
- `analysis_summary` 적재를 먼저 결정해야 reasoning distillation이 가능(지금은 버림).

---

## 우리 실측과 갈린 자리

없음 — 이 문서는 개념 정리다. 우리가 distill을 아직 안 돌렸다. 돌리면 그 수치는 `experiments/` 아래로 가고 여기 주장과 갈리면 그쪽이 이긴다(README).
