# 0010. 하네스 구조와 모델 로스터 확정 — Scout 풀 3역할 · Auditor Qwen3.6-35B-A3B · Critic gpt-oss-20b · 단계 단위 실행

- 상태: **제안** — 2026-09-06 @danielcho02 초안. **@sumin0218의 최종 리뷰 뒤 팀이 확정한 구조로 간다** — 그 리뷰가 이 PR의 머지이고, 머지 전까지 아래 「결정」은 전부 제안이다. Scout·인덱서·Auditor·Critic 어느 자리도 아직 닫힌 것이 아니다
- 일자: 2026-09-06
- 관련: [docs/research/2026-09-04-models.md](../research/2026-09-04-models.md) §2·§3.4·§3.5·§5, [docs/research/2026-09-04-quantization.md](../research/2026-09-04-quantization.md), [docs/research/2026-09-04-swap.md](../research/2026-09-04-swap.md) §6·§7, `ADR 0009`, [build-plan.md](../build-plan.md) §2·§4·§7·§8, [model-serving.md](../model-serving.md) §1·§2, [evaluation.md](../evaluation.md) §2.1, [D9](../open-questions/D/D9.md)·[D59](../open-questions/D/D59.md)·[D63](../open-questions/D/D63.md)·[D64](../open-questions/D/D64.md), [R2](../open-questions/R/R2.md)·[R8](../open-questions/R/R8.md), [2026-09-04 다섯 상주 보고서](../experiments/harness/2026-09-04-five-model-residency-atom.md), 중간발표 v3 슬라이드 10·18·23, 하네스 리스트럭처 계획(2026-09-04, @sumin0218·@danielcho02 — 로컬 노트 `.claude/restructure-2026-09-04.md`, 저장소 밖)

## 배경

**2026-08-30 5차 회의가 모델 선정을 보류했다.** scout·auditor·critic 모델과 오케스트레이션(큰 모델 하나 대 소형 앙상블)이 정해지기 전에는 실측 축이 통째로 달라지므로 D2·D3·D4·D24·D28·D29·D51·D54 여덟을 보류했다([open-questions.md](../open-questions.md) §2 머리). **그 여덟은 이 문서가 아니라 #151이 이미 처리했다** — 인덱스 §2 머리와 각 D 본문의 2026-09-08 절이 여섯을 `열림`으로 바꿨고(2026-09-09 조회 기준 `origin/main`), 그 머리가 근거로 든 「로스터 제안」이 이 문서와 [09-08 조사](../research/2026-09-08-roster.md)다. **남은 둘을 이 문서가 풀지는 않는다** — 사유는 「결과」에 적었다.

그 사이 세 입력이 쌓였다.

1. **중간발표 v3(슬라이드 23)의 기준안** — Scout `Phi-4`·`Mistral-Small-24B`·`QwQ-32B`, Auditor `DeepSeek-Coder-V2-Lite`, Critic `DeepSeek-R1-Distill-Llama-70B`. "가장 큰 조합에서 시작해 Top-Down으로 줄인다"는 전략과 함께다. 슬라이드 18이 Scout를 **CWE 계열별 세 에이전트**(Memory 415/416 · Boundary 119/125/787/190 · Injection 78/22/134)로, 슬라이드 10이 전체 구조(Code Slicing → Candidate Context Pool → Scout Orchestration → Auditor → Evidence Gate → Critic → Validation)로 그렸다.
2. **2026-09-04 외부 문헌 조사 셋**([research/](../research/README.md)). 요지 셋 — *문헌으로는 후보를 못 고른다*(취약점 탐지 실측이 후보 전부 0~1건 · models.md §3.3.1), *"4비트"가 한 물건이 아니다*(quantization.md), *단계 단위 실행이 교대의 선행 조건이다*(swap.md §6.2 — 후보 단위면 교대 512회, 단계 단위면 3~5회 · 계산값).
3. **리스트럭처 계획(2026-09-04, @sumin0218·@danielcho02)** — 70B dense 폐기 → 30B급 MoE 둘(조합 B), Scout 오케스트레이터 팬아웃, 리랭커 키움, 단계 단위 + 교대 스키마, "낡은 결함 수정 중단·리스트럭처 먼저".

결정을 묶는 실측 제약은 아래다. **전부 저장소 안의 값이고 라벨을 붙였다**(CLAUDE.md §0).

| 사실 | 값 | 출처 |
| --- | --- | --- |
| ATOM 물리 통합메모리 · 대역폭 | 119.7 GiB · 273 GB/s | [model-serving.md](../model-serving.md) §1 |
| Scout `qwen3-8b`(NVFP4) 처리량 | 단일 35.78 · batch32 **388.46** tok/s(적중률 0.9981, 캐시 ON) · 가중치+non-torch 6.38 GiB | `config/models.yaml` 2026-09-04 · [보고서](../experiments/harness/2026-09-04-five-model-residency-atom.md) §3·§9 [실측] |
| Auditor `qwen3-coder-30b-a3b`(NVFP4) 처리량 | 단일 53.4 · batch32 66.42(적중률 0.5673) · 캐시 OFF 39.64 · 18.28 GiB | 같은 곳 · [2026-09-02](../experiments/harness/2026-09-02-bench-concurrency-atom.md) [실측] |
| 두 모델의 배칭 성질 | Scout 1→32에서 **10.9배**, Auditor **1.13배** | [보고서](../experiments/harness/2026-09-04-five-model-residency-atom.md) §9 해석 ① [실측] |
| `devstral-small-2-24b` | **fp8**(nvfp4가 아니었다) · 멀티모달 · 가중치+non-torch 25.48~26.8 GiB · 콜드 약 330초 · sm_121 기동 확인 | 같은 보고서 §7 [실측] |
| 네 서버 동시 상주 | 가중치 합 27.66 GiB · KV 64.35 GiB · `nvidia-smi` PID 합 96.07 GiB — **제약은 상주 개수가 아니라 KV 예산 배분** | 같은 보고서 §3 [실측] |
| 32768로 다섯 | 4/5에서 CUDA OOM(페이지 캐시) | 같은 보고서 §8 [실측] |
| 70B dense bf16 | 140 GB ≈ 130.4 GiB — 119.7 GiB에 안 들어간다 | [D64](../open-questions/D/D64.md) ③ [계산값] |
| 조건 정의 | B3/B3′의 앙상블 축은 **"제작사가 다른 모델"** | [evaluation.md](../evaluation.md) §2.1 |

## 2026-09-08 갱신 — 이 초안 뒤에 도착한 것

**이 초안은 2026-09-06자다.** 그 뒤 두 가지가 들어왔고 **아래 「결정」을 그대로 두지 않는다.**
본문은 안 고쳤다 — 로스터를 바꾸는 것은 설계 판단이고 이 문서의 리뷰가 정할 일이다.

### ① 이 초안이 전제한 PR 여섯이 전부 머지됐다 (2026-09-08)

`#142`·`#139`·`#138`·`#140`·`#141`·`#143`. 그래서 §4가 과거형으로 적은
*"`foundation-sec-8b`가 registry에서 빠져(#139)"*가 **이제 참이다** — 초안 시점엔 거짓이었다.
registry는 **10개에서 7개**가 됐고 프로필은 **12개에서 9개**다.

**`ADR 0009`(단계 단위)는 번호로 참조한다.** [experiments/README.md](../experiments/README.md)가
적어 둔 대로 **ADR은 서로를 번호로 참조하고 그 번호가 식별자다** — 번호는 그 문서가 저장소에
들어왔는지와 무관하게 같은 것을 가리킨다. **0009를 들고 오는 PR은 #147이다**(2026-09-09 조회
기준 `origin/main`에 없다). 들어온 뒤에는 `docs/decisions/`에서 번호로 찾는다.

### ② 2026-09-08 조사 다섯이 이 로스터의 세 자리를 흔든다

[research/2026-09-08-roster.md](../research/2026-09-08-roster.md)와 그 근거 넷이다.
**아래 §2 표와 갈리는 자리만 적는다.**

| 자리 | 이 초안 (§2) | 09-08 조사 | 조사가 든 근거 |
| --- | --- | --- | --- |
| Scout·Memory | `qwen3-8b` | **`qwen3-coder-30b-a3b`** | UAF/DF는 **stateful·temporal ordering**이라 가장 어렵다 — *"every neural baseline misses it"* `[출처주장]`. **가장 강한 모델이 가야 한다** |
| Scout·Injection | **`phi-4`** | **`qwen3-8b`** | CWE-78·CWE-23은 **GPT-4가 1.00**을 낸다 `[출처주장]` — 가장 쉬운 계열이다. 그리고 `qwen3-8b`는 batch32 **388.46 tok/s** [실측]로 후보 전량이 지나는 단계에 맞는다 |
| Auditor | `qwen3.6-35b-a3b` | **A안 `qwen3-coder-30b-a3b` / B안 Nemotron-120B — A/B 미결** | 문헌이 정면으로 갈린다. PairVul에서 **236B(0.22)가 4B(0.25)보다 낮고** 우리 현행 30B-A3B가 **0.28로 천장(0.30) 근처**다 `[제3자]`. 반대로 CORRECT는 **컨텍스트가 풍부하면** 스케일링이 관찰된다고 한다 `[제3자]` — **그 화해는 우리 해석이지 근거가 아니다** |

**Boundary(`devstral-small-2-24b`)·Critic(`gpt-oss-20b`)·리랭커(`qwen3-reranker-4b`)는 같다.**

### ③ 초안의 판단 둘이 조사로 뒷받침됐다

- **Scout `QwQ-32B` 기각** — 이제 config로 확인된 근거가 붙는다. QwQ-32B의 **측정된**
  reasoning trace가 **LiveCodeBench에서 4,473 토큰** `[제3자]`으로 `config/budgets.yaml`의
  `output_max: 4096`을 넘는다. **코드에 가장 가까운 벤치마크에서조차 JSON에 남는 토큰이 0이다.**
- **Critic `gpt-oss-20b`** — judge 실측이 있는 유일한 계열이다(Hard2Verify·ProfBench·AgentJudgeBench).
  ⚠ 조사는 **120B로 올리지 말라**고 한다 — 크기 +3.71점 대 **reasoning effort +11~13점**이고,
  #37030(SM121 Harmony 첫 토큰 오류)이 열려 있으며 회피책이 우리 박스에 안 들어간다.

### ④ §5(D9 닫힘)를 `Closes`로 걸지 않는다

이 문서가 **「제안」**인 채로 머지되면 GitHub이 D9(#94)를 자동으로 닫는다. `origin/main`에서는
`architecture.md` §3 주석이 여전히 D9를 가리키고 `preprocess.py`가 펌웨어 경로를 미구현으로 둔다.
**리뷰가 끝나 §5가 확정되면 그때 닫는다.**

### ⑤ 리뷰에서 이 셋을 함께 본다

아래 「리뷰에서 정할 것」 넷에 더해, **위 ②의 세 자리**를 이 초안대로 갈지 조사 쪽으로 갈지가
리뷰의 몫이다. **조사는 로스터를 정하지 않았다** — 두 안을 나란히 놓고 A/B로 재라고 했을 뿐이다.

## 결정

### 1. 파이프라인은 0~9 그대로, 실행 단위는 **단계 단위**

[architecture.md](../architecture.md) §3의 열 단계와 Evidence Gate E0~E4는 바뀌지 않는다. 바뀌는 것은 **후보 하나가 3~6단계를 관통하던 루프를 모든 후보가 한 단계씩 함께 통과하는 루프로** 바꾸는 것이다 — `ADR 0009`의 `execution.mode: stage`를 **승인**한다. Scout 배칭 이득(10.9배 · 실측)과 교대 횟수 3~5회(계산값)는 이 구조에서만 나온다.

**교대(`execution.swap`)는 스키마만 열고 기본은 끈다.** 교대 예산이 아직 실측이 아니기 때문이다 — "왕복 2~6초"는 `/v1/models` 응답까지만 본 값이고(swap.md §1.2), 잠든 서버의 잔류 2,382 MiB가 모델마다 같은지도 미측정이다(swap.md §3.3). 아래 §결과의 메모리 표가 보여 주듯 **단계 단위만으로 단계별 상주가 119.7 GiB에 들어가므로**(계산값) 교대 없이 첫 run을 돌릴 수 있다.

### 2. 역할별 모델

| 자리 | 모델 (registry 키) | 제작사 | 양자화 | 지금 상태 |
| --- | --- | --- | --- | --- |
| 1단계 · 쿼리 생성기(모델 자리 ①) | `qwen3-8b` — `scout_memory`와 **같은 서버** | Alibaba | nvfp4 | 실측 있음. Joern 기반 슬라이서(LLM4FPM → LLMxCPG-Q 순)는 **별도 워크스트림**이고 그때까지 tree-sitter 인덱서를 유지한다 |
| 3단계 · 임베딩 | `qwen3-embedding-0.6b` | Alibaba | fp16 | checksum 有 · 처리량 미실측 |
| 3단계 · 리랭커 | **`qwen3-reranker-4b`** (신규) | Alibaba | fp16 | 미실측. **D63의 재측정 대상이 이 모델이다** — 0.6B는 registry에 남긴다 |
| 4단계 · `scout_memory` (CWE-415·416) | `qwen3-8b` | Alibaba | nvfp4 | 실측 있음 |
| 4단계 · `scout_boundary` (CWE-119·125·787·190) | `devstral-small-2-24b` | Mistral | fp8 | 기동 확인(9/4) · 처리량 미실측 |
| 4단계 · `scout_injection` (CWE-78·22·134) | **`phi-4`** (신규) | Microsoft | fp8(후보 `RedHatAI/phi-4-FP8-dynamic`) | 미실측 · **컨텍스트 16,384** |
| 5단계 · Auditor | **`qwen3.6-35b-a3b`** (신규) | Alibaba | nvfp4(후보 `nvidia/Qwen3.6-35B-A3B-NVFP4`) | 미실측 |
| 7단계 · Critic | **`gpt-oss-20b`** (신규) | OpenAI | mxfp4(네이티브) | 미실측 |
| B1 기준(최강 단일 멤버) | `qwen3-coder-30b-a3b` | Alibaba | nvfp4 | 실측 있음. **지우지 않는다** — build-plan §8이 요구하는 "항상 최강 단일 멤버와 비교"의 그 멤버다 |

**Scout 세 멤버의 모델 배정은 임시다.** 근거가 될 실측이 없다 — C 결함에서 가장 많은 계열(Boundary)에 가장 큰 모델을, 실측이 있는 `qwen3-8b`를 UAF/DF에, 나머지를 Injection에 뒀을 뿐이다. **첫 run의 공동오답 행렬**(누가 무엇을 함께 놓쳤나 — `Candidate.scout_member`로 만든다)이 재배치의 유일한 근거이고, 그 전에는 옮기지 않는다.

**"Mistral-Small-24B" 자리를 `devstral-small-2-24b`로 채운 이유.** 발표안이 적은 이름은 Mistral-Small-24B인데, Devstral Small 2는 그 24B 베이스(`base_model: mistralai/Mistral-Small-3.1-24B-Base-2503` — HF 메타데이터, 2026-09-06 조회)의 코드 파생이고 **이미 ATOM에서 기동·checksum이 끝나 있다.** 바닐라 `Mistral-Small-3.2-24B-Instruct-2506`을 고집하면 얻는 것은 이름이고 대가는 온보딩 하나다. 바꾸고 싶으면 registry 키 하나·프로필 한 줄이다.

### 3. Scout 풀은 **역할 셋**이다 — 팬아웃 · 합집합 · 발화 멤버 태깅

- 풀 멤버는 `Role`에 **`scout_memory`·`scout_boundary`·`scout_injection`** 셋을 더해 표현한다. 프로필은 `roles:`에 그 셋을 적고, 기존 `scout` 한 역할 프로필도 그대로 유효하다(둘 중 하나가 있어야 한다).
- **팬아웃**: 4단계에서 대상 하나를 세 멤버가 각각 본다(캡슐은 한 번 만든다). **합집합(방식 ①)**: 임계값을 넘은 멤버의 후보를 **고르지도 합치지도 않고 각각** 5단계로 보낸다 — 함수 하나에 Finding이 여럿 생길 수 있고 9단계 dedupe가 같은 함수의 것을 합친다. 판정은 6단계 게이트가 근거로 한다(build-plan §5 "투표가 아니라 분업이다"). 함수당 하나로 합쳐 Auditor를 한 번만 부르는 방식 ②는 **Auditor가 받은 근거가 어느 멤버 것인지 섞여** 기각했다 — 공동오답 행렬이 흐려진다. Auditor 호출은 최대 3배지만 멤버가 CWE 계열로 갈려 있어 실제 겹침은 첫 run이 답한다. **태깅**: `Candidate.scout_member`를 신설해 하네스가 stamp한다 — 모델이 채우는 값이 아니다(`produced_by`와 같은 논법).
- **왜 "roles를 리스트로" 대신 역할 셋인가.** 계획 D절은 `roles: dict[str, str]`를 리스트 수용으로 넓히자고 적었고 그 파급(config·factory·cli·bench·scout.py)을 스스로 셌다. 역할로 두면 그 다섯이 **0**이다 — `RunRecord.model_checksums`·`prompt_versions`·`ProducedBy.role`·`RoutingModelClient`·역할별 토큰 카운터가 전부 역할 키로 이미 동작하고, 멤버별 checksum·프롬프트 버전·발화 기록이 **공짜로** 남는다. 리스트였다면 그 다섯에 "리스트의 몇 번째"라는 새 좌표를 새로 파야 한다. 계획의 의도(N멤버 · 태깅)는 같다.

### 4. 계열 규칙을 코드가 강제한다 (D59 (a))

`ModelEntry`에 **`family`**(베이스 가중치 제작사 — 양자화한 조직이 아니라 가중치의 출처 조직)를 넣고, `load_config`가 **auditor와 critic이 다른 모델 키면 `family`가 달라야 한다**를 검사한다. 같은 키(B1)는 정의상 허용한다. 2026-09-04에 `foundation-sec-8b`(Cisco 배포·Llama 베이스)가 registry에서 빠져(#139) D59가 든 "계열 정의의 애매함" 실례가 없어졌고, 남은 값은 전부 자명하다 — `qwen`(Alibaba)·`mistral`·`microsoft`·`openai`·`fake`.

### 5. 1차 분석 대상은 **C/C++ 소스 저장소**다 (D9 닫힘)

킥오프 04-1의 "펌웨어" 문구보다 중간발표 슬라이드 8의 "C/C++ 오픈소스 Zero-Day"가 팀의 현재 목표이고, 이번 구조의 슬라이서 둘(LLM4FPM · LLMxCPG-Q)이 **전부 소스 기반**(Joern CPG)이라 펌웨어를 1급 입력으로 두면 1단계가 성립하지 않는다. 바이너리·펌웨어는 2차다. [architecture.md](../architecture.md) §3의 0·1단계 서술을 그에 맞춘다.

### 6. 프롬프트 버전

새 로스터의 실서빙 모델(`qwen3-8b`·`devstral-small-2-24b`·`qwen3-coder-30b-a3b`와 신규 셋)은 **전부 `v2`**다. D54의 (c)("기본을 v2로 올린다")가 막혀 있던 이유는 도는 2×2 매트릭스를 무효화한다는 것이었는데 그 매트릭스는 2026-09-01에 폐기됐다. 풀 세 멤버가 다른 버전이면 같은 단계에 다른 계약이 섞이므로 **풀은 한 버전이어야 한다.** `qwen3-4b`(`single_gpu_12g` 전용)는 `v1`에 둔다. D54는 이것으로 닫지 않는다 — "어느 조건에 어느 버전을 쓰는가"의 설계 기록이 남는다(부분).

### 7. 기각

| 안 | 기각 사유 |
| --- | --- |
| Critic `DeepSeek-R1-Distill-Llama-70B` (발표안) | bf16 140 GB로 적재 불가(계산값), 양자화판은 sm_121 미검증, Auditor도 DeepSeek이면 "제작사가 다른 모델" 위반 — [D64](../open-questions/D/D64.md) ①·③. **D64는 이 ADR로 기각·닫힘** |
| Auditor `DeepSeek-Coder-V2-Lite` (발표안) | 2024년 모델 · NVFP4 없음(FP8만) — models.md §2.1·§4.2 |
| Scout `QwQ-32B` (발표안) | thinking 모델이라 답 하나에 수천 토큰 — 후보 전량이 지나는 4단계에서 그 비용이 후보 수만큼 곱해지고, thinking 모델의 구조화 출력 결함군(models.md §3.5.4)이 그 경로다 |
| 조합 A `Nemotron-3.5-Lightning` + `gpt-oss-20b` (연구 권장 1순위) | GB10 검증 명시·NVFP4 상태 SWE-bench 공개(52.8)는 장점이나 코드 성능 보고가 후보 중 낮고 취약점 탐지·critic 실측 0건. 팀이 "보고 성능 최대"(조합 B)를 골랐다. **Nemotron은 Auditor 2차 후보로 남긴다** |
| 조합 C `Gemma-4-26B-A4B` + `Nemotron` | Gemma4가 vLLM structured output 결함(#39130) 당사자 · Critic 근거 0건 |
| 조합 D 현행 `qwen3-coder-30b-a3b` + `Nemotron` (변수 최소) | Coder가 2025-07 모델. **B1 기준으로 남겨 비교 대상이 된다** — 버린 것이 아니다 |
| Auditor·Critic 둘 다 Qwen | 조건 2 정면 위반 — models.md §5.5 |
| Scout: 소형 베이스 하나 + 전문가 프롬프트 셋(→ LoRA 셋) | 온보딩 최소·상주 최소라 초안의 권장이었으나, 팀이 발표안의 모양(다른 제작사 셋)과 멤버 간 오류 상관 가능성을 택했다. **FT2(계열별 LoRA)는 이제 베이스 셋에 각각이다** — 비용이 는다 |
| 후보 단위 실행 유지 | Scout 배칭 못 씀 · 상주 전부 동시 · 교대 512회(swap.md §6.2 계산값) |

## 결과

**얻는 것.** 발표안과 research가 같은 방향을 가리키는 자리(Scout 오케스트레이터·MoE 둘·단계 단위)가 코드 좌표로 내려온다(§1·§3). 계열 규칙이 코드로 지켜진다(§4 — D59 (a)).

**보류는 여기에 넣지 않는다.** 여섯을 `열림`으로 바꾼 것은 #151이고, **이 문서가 새로 푸는 것은 없다.** 남은 둘이 그것을 보여 준다 — **D51**은 `trigger_status`를 내는 것이 Auditor라 A/B 미결이 단독으로 막는 유일한 항목인데(인덱스 §2 머리) **이 문서는 그 A/B를 리뷰의 몫으로 남긴다**(위 ②·⑤). **D3**은 애초에 모델 문제가 아니다 — `expand()`의 호출부가 저장소에 없어 어떤 모델로 돌아도 `capsule_expansion_max`가 안 걸린다(D3 본문 2026-09-08). **이 문서가 보류에 대해 갖는 몫은 「로스터 제안」이라는 근거 자리 하나이고, 리뷰가 로스터를 바꾸면 그 근거도 함께 움직인다.**

**잃는 것.** 9/21 벤치마크 전에 **새 모델 넷**(`gpt-oss-20b`·`qwen3.6-35b-a3b`·`phi-4`·`qwen3-reranker-4b`)을 ATOM에서 받아 checksum과 §3 체크리스트를 통과시켜야 한다 — 공용 장비 시간이 가장 비싼 자원이다. 그리고 취약점 탐지 성능은 **후보 전부 문헌에 없으므로**(models.md §3.3.1) 이 로스터가 낫다는 근거는 우리가 만들어야 한다.

**메모리 — 단계 단위에서 동시에 떠 있는 것만 센다.** 계산값과 실측을 갈랐다.

| 단계 | 상주 | 가중치+non-torch (GiB) | 라벨 |
| --- | --- | --- | --- |
| 1 · 쿼리 생성 | `qwen3-8b` | 6.38 | 실측 |
| 3 · 검색 | `qwen3-embedding-0.6b` + `qwen3-reranker-4b` | 1.62 + ≈8 | 실측 + 계산값(4B × 2바이트) |
| 4 · Scout 풀 | `qwen3-8b` + `devstral-small-2-24b` + `phi-4` | 6.38 + 26.8 + ≈14 ≈ **47** | 실측 + 실측(util 0.45) + 계산값(14B × 1바이트) |
| 5 · Auditor | `qwen3.6-35b-a3b` | ≈21 | 계산값 — 30.5B NVFP4가 18.28이었던 비례 |
| 7 · Critic | `gpt-oss-20b` | ≤16 | 출처주장(OpenAI "16GB 메모리 안에서 동작") — 바이트 미확인 |

**피크는 4단계 ≈47 GiB(가중치)이고 KV는 `--gpu-memory-utilization`으로 우리가 정한다** — 9/4 실측이 "제약은 KV 예산 배분"이라고 답한 그대로다. 전부를 동시에 띄우면 가중치만 ≈93 GiB(계산값)라 KV가 안 남는다 — 그것이 단계 단위가 선행인 이유이고, 교대는 그 다음이다.

**되돌리는 비용.** 모델은 registry 키를 지우지 않으므로 프로필 한 줄이다. Scout 풀 역할 셋은 `Role` 열거형·`prompts/` 디렉터리·`Candidate.scout_member` 필드다 — 지울 수 있으나 그 사이 run의 `RunRecord`가 그 역할 키를 들고 있으므로 옛 manifest 로드를 깨지 않는 방향(필드 유지)으로만 되돌린다. 단계 단위 러너는 `execution.mode` 기본값(`candidate`)이 옛 경로를 남기므로 스위치 하나다(`ADR 0009`).

## 리뷰에서 정할 것 — 미결 (@sumin0218)

이 문서가 답하지 않은 구조 질문 넷이다. 발표 자료와 저장소 코드가 서로 다르게 말하는 자리라 **팀이 정해야 하고**, 각 줄에 이 초안이 기울어 있는 쪽을 적었다.

| # | 질문 | 발표 자료 | 지금 코드 | 초안의 기울기 |
| --- | --- | --- | --- | --- |
| 1 | **Scout 캡슐에 검색 결과(`related`)가 들어가는가** | 슬라이드 17 — Embedder→Reranker→Retriever가 만든 캡슐을 **Scout에게** 전달 | LIGHT(Scout)는 검색 안 함, EXPANDED(Auditor)에만 `related`(`hybrid_retriever.LEVEL_SECTIONS`) — "후보 전량 단계에서 임베딩을 부르면 배칭 전제가 무너진다" | 코드 쪽 유지. 검색을 Scout에 넣으면 3단계 비용이 후보 수만큼 붙고 [D63](../open-questions/D/D63.md)의 리랭커 미검증이 4단계 입력에 바로 걸린다 |
| 2 | **인덱서에서 확정된 것** — Joern 도입 여부, LLM4FPM(신호 주도)·LLMxCPG-Q(쿼리 생성) 중 순서, 그때까지 tree-sitter 유지 여부, 쿼리 생성기 모델을 Scout 모델과 공유하는지 | 슬라이드 13·14 — 둘 다 | tree-sitter 인덱서만 있다 | 둘 다 · LLM4FPM 먼저(결정론·독립) · tree-sitter 유지 · 쿼리 생성기는 `qwen3-8b` 공유 |
| 3 | **후보 수 50 → 30 → 10**(슬라이드 10)이 임계값 목표인가 예시인가 | 숫자가 박혀 있다 | `candidate_max 256`(근거 없는 관례) · `critic_top_n 8` | 예시로 읽는다. 목표라면 `critic_top_n`을 10으로 — 8도 근거가 없다 |
| 4 | **Validation의 Exploit은 누가 만드는가** | 슬라이드 21 — "Exploit을 통해 실제 취약 여부를 검증" | PoC를 만드는 주체가 없어 E4(`proven`)가 실서빙에서 도달 불가(STATUS.md §5) | 데이터셋 정답 PoC 재생(STATUS §5 선택지 A)을 먼저. LLM PoV 에이전트(B)는 별도 결정 |

## 후속

1. **온보딩 넷 — 순서와 각각의 관문.** 다운로드 → 스냅샷 → `tools/model_checksum.py` → `checksum`·`source` → §3 체크리스트 7개 → `benchmark`. 채우기 전엔 0이다. 순서는 위험이 큰 것부터:
   - `gpt-oss-20b` — **Harmony × structured output smoke가 첫 관문이다.** vLLM 이슈 #23120·#37359가 not-planned로 닫혀 있고 0.27.0의 PR #45560이 그 경로를 겨냥했는데 우리 0.27.1에서 재현되는지 모른다(models.md §3.5.4). 깨지면 "자유 서술 → 별도 추출" 2단(quantization.md 제안)이 다음 수다.
   - `qwen3.6-35b-a3b` — 멀티모달(`Qwen3_5MoeForConditionalGeneration`, HF config.json 2026-09-06 조회 · 262,144 컨텍스트 · 256 전문가 중 8 활성)이라 **텍스트 전용 경로**와 **thinking-on 상태의 구조화 출력**(#18819는 thinking-off에서 깨진다)을 확인한다.
   - `phi-4` — 컨텍스트 **16,384**(HF config.json). LIGHT 캡슐 예산 4,000 + 대상 원문이 그 안에 드는지 첫 run에서 `model_call` 400 건수로 확인한다.
   - `qwen3-reranker-4b` — **D63의 (a)**(40줄 코드 조각 × 식별자 질의)를 이 모델로 잰다. 0.6B의 관측은 이 모델에 이식하지 않는다.
2. **R8 등재** — 새 로스터 넷의 sm_121 기동·구조화 출력·Critic 반박 정확도가 전부 미검증이라는 프로젝트 수준 리스크. R2(Devstral NVFP4 Critic)는 로스터 변경으로 닫힌다.
3. **PR 셋.** A(이 문서 · registry · 문서 · D 상태) → B(스키마: `execution`·`family`·풀 역할·`scout_member` · 검증기 · 테스트) → C(러너 단계 단위 · 팬아웃 · 8단계 dedupe 선행). 모델 무관 선행 둘(계획 D절 — `Evidence.location` 채우기 #70, 도구 루프 재진입 신호 #60)은 **별도 PR**이다.
4. **보류 여덟 — 이 문서가 할 일은 없다.** 상태도 각 항목의 다음 입력도 #151이 인덱스와 D 본문에 이미 적었다(D4를 "고정밀 대비"에서 **반박 정확도 표본 자체**로 재정의한 것도 그 PR이다). ⚠ **그리고 그 다음 입력들은 「새 로스터 첫 run」 하나가 아니다** — 풀린 여섯 중 첫 run이 답하는 것은 **D2·D28**(멤버별 score 분포·고유값 수)뿐이고, **D24**는 A안·B안 **run 두 번**("한 번으로는 못 정한다"), **D54**는 `prompt_template_version`을 v2로 올리는 **PR**, **D29**는 **로스터와 무관한** 정책 판단 둘, **D4**는 표본 크기·합격선을 정하는 설계 판단이다. **첫 run 하나로 여섯이 닫힌다고 읽지 않는다.**
5. **오류 상관을 잰다**(models.md §6.3, 계획 D절 "교차검증 상관 재확인"). Scout 멤버 셋의 공동오답 행렬은 `scout_member`로 첫 run에서 나온다. Auditor 후보 둘(`qwen3.6` 대 `qwen3-coder`)의 오답 겹침은 같은 입력으로 두 번 돌려 센다.
6. **Critic 계측** — 통과율(100%에 가까우면 합격이 아니라 고장, models.md §3.4.2)과 position bias(A/B 순서 뒤집기, §3.4.5)를 trace에서 뽑는다.
7. **평가 규칙** — 바뀐 하네스는 항상 B1(`qwen3-coder-30b-a3b` 단일)과 비교한다. 평균이나 지난 조건과 비교하지 않는다(build-plan §8).
8. **공지** — 계획 착수 순서 1번(타팀 정보전달, 70B 실측 근거 포함)은 @sumin0218이 맡는다.

## 이 결정이 다시 열리는 조건

- 온보딩 관문에서 `gpt-oss-20b`의 구조화 출력이 우리 버전에서 깨지고 2단 추출로도 안 닫히면 → Critic을 Nemotron-3.5-Lightning(조합 A/C의 Critic 자리)으로 바꾼다. 계열 규칙은 그대로 만족한다.
- `qwen3.6-35b-a3b`가 sm_121에서 안 뜨거나 텍스트 경로가 없으면 → Auditor를 `qwen3-coder-30b-a3b`(B1 기준)로 되돌리고 Nemotron을 2차 후보로 올린다.
- 첫 run의 공동오답 행렬에서 멤버 둘이 같은 것을 함께 놓치면 → 그 둘 중 하나를 바꾼다. 어느 쪽인지는 그 행렬이 정한다.
