# 0010. 하네스 구조와 모델 로스터 확정 — Scout 풀 3역할 · Auditor A/B · Critic gpt-oss-20b · 단계 단위 실행 · Joern 슬라이서

- 상태: **승인** — 2026-09-06 팀 초안, **2026-09-09 팀 확정**, 같은 날 **팀 리뷰 승인**(PR #148, 커밋 `c347b9e`). 리뷰가 남긴 셋은 아래 「리뷰에서 정할 것」이 disposition을 든다. 아래 「결정」은 이제 정본이고 **저장소가 이 로스터를 바라본다**(→ [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md)). ⚠ **모델은 계속 바뀐다** — 팀이 실험하며 정하는 중이고, 바꾸는 비용을 낮게 유지하는 것이 이 문서의 설계 목표다(「되돌리는 비용」). **바뀔 때 고치는 자리는 PROJECT-CONTEXT.md와 registry이지 문서 전부가 아니다.** 팀 리뷰는 폐기되지 않았다 — 아래 「리뷰에서 정할 것」에 남은 셋이 그 몫이고, 로스터를 막지 않는다
- 일자: 2026-09-06
- 관련: [docs/research/2026-09-04-models.md](../research/2026-09-04-models.md) §2·§3.4·§3.5·§5, [docs/research/2026-09-04-quantization.md](../research/2026-09-04-quantization.md), [docs/research/2026-09-04-swap.md](../research/2026-09-04-swap.md) §6·§7, `ADR 0009`, [build-plan.md](../build-plan.md) §2·§4·§7·§8, [model-serving.md](../model-serving.md) §1·§2, [evaluation.md](../evaluation.md) §2.1, [D9](../open-questions/D/D9.md)·[D59](../open-questions/D/D59.md)·[D63](../open-questions/D/D63.md)·[D64](../open-questions/D/D64.md), [R2](../open-questions/R/R2.md)·[R8](../open-questions/R/R8.md), [2026-09-04 다섯 상주 보고서](../experiments/harness/2026-09-04-five-model-residency-atom.md), 중간발표 v3 슬라이드 10·18·23, 하네스 리스트럭처 계획(2026-09-04, 수민·민규 — 로컬 노트 `.claude/restructure-2026-09-04.md`, 저장소 밖)

> ⚠ **2026-09-24 — [ADR 0012](0012-harness-canon-v2.md)가 이 ADR의
> 네 자리를 다시 정한다. 본문은 안 고쳤다 — 자리마다 같은 날짜의 노트가 범위를 짓는다.**
> ① **슬라이스의 역할** — §2 슬라이서 행의 *"후보 열거를 대신하지 못한다"*와 「리뷰에서 정할 것」 #2 행의
> *"(a) 후보 열거에만"*을 그 ADR 결정 1이 대체한다(슬라이스 = Scout의 코드 입력, 후보 = 슬라이스 1개).
> ② **재평가(requeue) 경로** — 후속 3의 *"도구 루프 재진입 신호 #60"*을 결정 6이 폐기한다(모자란 맥락은
> need_evidence 루프가 채운다 — Phase 5, 미구현). (⏵ 2026-09-26 — 구현됐다: `src/orchestrator/runner.py`의
> `_run_ne_hops`. 상세는 [ADR 0012](0012-harness-canon-v2.md) 결정 2 검증표 · [knowledge-base.md](../knowledge-base.md) §5.)
> ③ **Scout 후보를 5단계로 보내는 기준** — §3 「팬아웃」 불릿의 합집합(방식 ①)이 보내는 *"임계값을 넘은 멤버의
> 후보"*를 결정 2가 「최종 신호가 `triage_review`인 좌석의 후보」로 바꾼다. 고르지도 합치지도 않고 좌석마다
> 각각 보내는 방식 ①은 그대로다.
> ④ **Scout 좌석을 옮기는 근거** — §2 표 아래의 *"첫 run의 공동오답 행렬 … 재배치의 유일한 근거이고, 그 전에는
> 옮기지 않는다"*를 결정 4가 실험팀 실험(결정 기한 2026-09-30)으로 둔다(⏵ 같은 날 팀이 세 좌석을 확정했다 — PROJECT-CONTEXT.md §1). 2026-09-21 교체가 이미 그 규칙을
> 밟지 않았다(그 자리의 노트 · §3 머리 인용 블록의 같은 날 노트).
> 그리고 **2026-09-21 로스터 교체가 남긴 것**에도 같은 날짜로 노트를 달았다 — Auditor A/B 조건절(⑤ · §4 · §7 ·
> 「결과」 · 후속 1·2·4·5 · 「다시 열리는 조건」), 「잃는 것」의 checksum 서술(오늘 거짓인 그 문장에는 취소선도
> 그었다 — [R8](../open-questions/R/R8.md)의 2026-09-21 절과 같다). 메모리 문단의 로스터 언급에는 날짜(2026-09-09)를
> 붙였다. 제목의 「Auditor A/B」는 승인 당시의 이름이라 그대로 둔다.

## 배경

**2026-08-30 5차 회의가 모델 선정을 보류했다.** scout·auditor·critic 모델과 오케스트레이션(큰 모델 하나 대 소형 앙상블)이 정해지기 전에는 실측 축이 통째로 달라지므로 D2·D3·D4·D24·D28·D29·D51·D54 여덟을 보류했다([open-questions.md](../open-questions.md) §2 머리). **그 여덟은 이 문서가 아니라 #151이 이미 처리했다** — 인덱스 §2 머리와 각 D 본문의 2026-09-08 절이 여섯을 `열림`으로 바꿨고(2026-09-09 조회 기준 `origin/main`), 그 머리가 근거로 든 「로스터 제안」이 이 문서와 [09-08 조사](../research/2026-09-08-roster.md)다. **남은 둘을 이 문서가 풀지는 않는다** — 사유는 「결과」에 적었다.

그 사이 세 입력이 쌓였다.

1. **중간발표 v3(슬라이드 23)의 기준안** — Scout `Phi-4`·`Mistral-Small-24B`·`QwQ-32B`, Auditor `DeepSeek-Coder-V2-Lite`, Critic `DeepSeek-R1-Distill-Llama-70B`. "가장 큰 조합에서 시작해 Top-Down으로 줄인다"는 전략과 함께다. 슬라이드 18이 Scout를 **CWE 계열별 세 에이전트**(Memory 415/416 · Boundary 119/125/787/190 · Injection 78/22/134)로, 슬라이드 10이 전체 구조(Code Slicing → Candidate Context Pool → Scout Orchestration → Auditor → Evidence Gate → Critic → Validation)로 그렸다.
2. **2026-09-04 외부 문헌 조사 셋**([research/](../research/README.md)). 요지 셋 — *문헌으로는 후보를 못 고른다*(취약점 탐지 실측이 후보 전부 0~1건 · models.md §3.3.1), *"4비트"가 한 물건이 아니다*(quantization.md), *단계 단위 실행이 교대의 선행 조건이다*(swap.md §6.2 — 후보 단위면 교대 512회, 단계 단위면 3~5회 · 계산값).
3. **리스트럭처 계획(2026-09-04, 수민·민규)** — 70B dense 폐기 → 30B급 MoE 둘(조합 B), Scout 오케스트레이터 팬아웃, 리랭커 키움, 단계 단위 + 교대 스키마, "낡은 결함 수정 중단·리스트럭처 먼저".

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
들어왔는지와 무관하게 같은 것을 가리킨다. **0009는 2026-09-09 02:09에 #147로 들어왔다**(`2dc3901`) —
[docs/decisions/0009-stage-unit-execution-and-model-swap.md](0009-stage-unit-execution-and-model-swap.md).
그 문장은 원래 "아직 `origin/main`에 없다"였고 **한 시간 뒤 거짓이 됐다.** 번호 참조 규칙
자체는 그대로 두되(그것이 이 문단의 요지다) 사실은 갱신한다.

### ② 2026-09-08 조사 다섯이 흔든 세 자리 — **2026-09-09에 조사 쪽으로 확정했다**

[research/2026-09-08-roster.md](../research/2026-09-08-roster.md)와 그 근거 넷이다.
**아래 §2 표와 갈렸던 자리만 적는다. 셋 다 「09-08 조사」 열이 §2에 반영됐다** — 아래 표는 그 판단의 근거 기록이다.

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

> ⚠ **2026-09-19 — 이 유보는 끝났다. 다만 유보 사유와 미구현 상태를 가른다.**
> **(1)** 은 2026-09-09 승인으로 사라졌다. **(2)** 는 두 절인데 갈린다 —
> ① *"`architecture.md` §3 주석이 여전히 D9를 가리키고"* 는 일곱 자리를 §5에 맞춘 PR이 없앴고,
> ② *"`preprocess.py`가 펌웨어 경로를 **미구현**으로 둔다"* 는 **그대로다.** 그 PR이 고친 것은
> 그 사실을 서술하는 **주석의 방향**이지 미구현 상태가 아니다([STATUS.md](../../STATUS.md) §5의
> 빈칸이 그 상태를 든다). **유보의 근거는 ①이었고 그것이 해소됐으므로 닫는다** —
> [D9](../open-questions/D/D9.md)는 **닫힘**이고 그 PR이 `Closes #94`를 들었다.
>
> ⚠ **아래 §5 마지막 문장**(*"architecture.md §3의 0·1단계 서술을 그에 맞춘다"*)**은 표 행이
> 아니라 그 아래 단서 문단으로 이행됐다.** `architecture.md` §3 표의 0·1단계 행은 *"펌웨어
> 추출(binwalk)"*·*"strings, readelf, objdump, Ghidra Headless"*를 **그대로 든다** — 그것은
> 책임 정의이고, 순서가 2차라는 것을 바로 아래 문단이 적는다. 같은 정렬에서 나온 잔여(조건
> `B1'`가 실인덱스에서 후보 0건)는 [D79](../open-questions/D/D79.md)가 든다.

### ⑤ 리뷰에서 이 셋을 함께 본다

**위 ②의 세 자리는 2026-09-09에 팀이 조사 쪽으로 확정했다** — 근거는 ②의 표 그대로다. 조사 자신은 *"이것은 제안이지 결정이 아니다"*라 적었고 A/B만 남겼는데, **그 A/B(Auditor)는 그대로 열어 둔다.** ⚠ **2026-09-24 — 이 A/B는 2026-09-21 로스터 교체로 소멸했다**(§2 Auditor 행).

아래 「리뷰에서 정할 것」 중 **#2(인덱서)도 같은 날 확정됐다**(§2 슬라이서 행). 남은 셋(#1·#3·#4)이 팀 리뷰의 몫이고 **로스터를 막지 않는다.**

## 결정

### 1. 파이프라인은 0~9 그대로, 실행 단위는 **단계 단위**

[architecture.md](../architecture.md) §3의 열 단계와 Evidence Gate E0~E4는 바뀌지 않는다. 바뀌는 것은 **후보 하나가 3~6단계를 관통하던 루프를 모든 후보가 한 단계씩 함께 통과하는 루프로** 바꾸는 것이다 — `ADR 0009`의 `execution.mode: per_stage`를 **승인**한다. Scout 배칭 이득(10.9배 · 실측)과 교대 횟수 3~5회(계산값)는 이 구조에서만 나온다.

**교대(`execution.swap`)는 스키마만 열고 기본은 끈다.** 교대 예산이 아직 실측이 아니기 때문이다 — "왕복 2~6초"는 `/v1/models` 응답까지만 본 값이고(swap.md §1.2), 잠든 서버의 잔류 2,382 MiB가 모델마다 같은지도 미측정이다(swap.md §3.3). 아래 §결과의 메모리 표가 보여 주듯 **단계 단위만으로 단계별 상주가 119.7 GiB에 들어가므로**(계산값) ~~교대 없이 첫 run을 돌릴 수 있다.~~ → ⏵ **2026-09-24 — 지금 로스터에서는 교대 없이 못 돈다.** 단계 단위 러너는 서버를 내리지 않아 네 서버가 동시에 떠 있어야 하고 util 합이 1.44다(`config/profiles.yaml`의 `harness_pool` 주석, 계산값). ⏵ **2026-09-29 — 그 주석의 합은 이제 1.80이다**(0.30 + 0.40 + 0.80 + 0.30 — `config/serving/atom.yaml`의 util, 계획값). 1.44는 2026-09-24 판(Scout 0.20·0.30 · 옛 Auditor `qwen3.6-35b-a3b` 0.64 · Critic 0.30 — 그날 ATOM에서 뜬 util)이다. 결론(한 장비에 동시에 못 올린다)은 그대로다. 그래서 교대를 켠다 — 방식은 단계 단위 콜드 재기동이고(팀 — sleep/wake 아님) 배선은 아직이며 착수는 열린 PR을 모두 머지한 뒤다([PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §5 「모델 배선」 행). ⏵ **2026-09-29 — 교대의 하네스 쪽이 들어왔다** — 러너가 단계 경계에서 교대 콜러블을 부르고 run 끝에 내린다(`runner._swap_for`) · 교대 어댑터(`src/adapters/model_swap.py`) · 장비 파일 스키마(`config/serving/<device>.yaml`) · `model_swap` 기록. 모양은 [ADR 0009](0009-stage-unit-execution-and-model-swap.md) 「2026-09-29 제안 — 단계 단위 콜드 재기동」 절이고 **제안이다(팀 사인오프 필요).** 교대 명령(`tools/serve/swap.sh` — 장비 무관)·장비 파일(`config/serving/atom.yaml`·`vastai.yaml`)·`harness_pool`의 `swap` 선언도 같은 날 들어왔다 — **실장비 종단은 미실측이다**(ATOM 먼저 · 장비 파일 값은 계획값).

### 2. 역할별 모델

| 자리 | 모델 (registry 키) | 제작사 | 양자화 | 지금 상태 |
| --- | --- | --- | --- | --- |
| ~~1단계 · 쿼리 생성기(모델 자리 ①)~~ | ~~`qwen3-8b`~~ | — | — | **2026-09-13 이 자리가 소멸했다.** 슬라이싱 방식이 무모델로 확정되면서 1단계에 모델 자리가 없어졌다(아래 행). ~~`qwen3-8b`는 `scout_injection`으로 남으므로 registry 키는 안 지운다~~ → 2026-09-24 지웠다(로스터 밖 정리 — [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1 끝) |
| 0·1단계 · 슬라이서 | **Joern 기반 — 무모델 결정론.** Case A(source-first) · Case B(sink-first) 합집합 → 포함률 임계 클러스터링 | — | — | **2026-09-09 구조 확정 · 2026-09-13 방식 확정.** ⚠ **방식이 바뀌었다** — 앞 판본은 *"`LLM4FPM`(신호 주도) → `LLMxCPG-Q`(쿼리 생성) 순"*이었고, 실험 결과 **둘 다 안 쓴다**(LLM4FPM의 SAST 시작점도, LLMxCPG-Q의 모델 쿼리 생성도 없다). **tree-sitter 인덱서는 전환 대상이 아니라 짝으로 계속 남는다** — 슬라이스는 그래프가 아니라 그래프의 **투영**이라 E2의 `GRAPH` Evidence와 후보 열거를 대신하지 못한다. 정본은 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §2다. ⚠ **2026-09-24 — 이 칸의 「후보 열거를 대신하지 못한다」는 [ADR 0012](0012-harness-canon-v2.md) 결정 1이 대체한다** — 후보가 슬라이스 1개이고 슬라이스가 Scout의 코드 입력이다. tree-sitter는 짝으로 남고 need_evidence resolver의 심볼 표가 된다 — `GRAPH` Evidence와 `callers`/`callees`는 그 결정이 건드리지 않는다 |
| 3단계 · 임베딩 | `qwen3-embedding-0.6b` | Alibaba | fp16 | checksum 有 · 처리량 미실측 |
| 3단계 · 리랭커 | **`qwen3-reranker-4b`** (신규) | Alibaba | fp16 | 미실측. **D63의 재측정 대상이 이 모델이다** — ~~0.6B는 registry에 남긴다~~(→ 2026-09-24 지웠다 — 로스터 밖 정리). ⚠ **2026-09-20 — 이 행의 두 칸이 낡았다.** ① dtype은 **`bf16`**이다 — registry가 `bf16`으로 등재했고(`config/models.yaml`, 그 항목 주석이 *"양자화가 아니라 native dtype"*이라 적는다), 2026-09-13 **템플릿 보고서**도 bf16으로 적는다(입력 모양 보고서는 dtype을 안 든다). ② **`미실측`이 아니다** — 2026-09-13에 ATOM 8005 기동과 **D63 (a) 첫 실측**이 났다(`run_id` 없어 비교표 밖 · ⏵ 2026-09-24 — 사람이 고른 짧은 함수 8건, 4~10줄에서다. (a)가 정한 40줄 조각 · 32건은 아직이다 — [D63](../open-questions/D/D63.md)의 끝 절). 형제 행들이 그러듯 *"기동 실측 있음 · 점수 정확성 미실측"*으로 읽는다. **점수 정확성이 미실측인 것은 그대로다** |
| 4단계 · `scout_memory` (CWE-401·404·415·416·457·476·562·590·761·762·772·775·824·825) | ~~`qwen3-coder-30b-a3b`~~ → ~~`seccoderx-8b`~~ → **`qwen3-14b`** | ~~Alibaba~~ → ~~SecCoderX~~ → Alibaba (`family: qwen`) | ~~nvfp4~~ → bf16 | **2026-09-21 회의가 이 자리를 교체했다.** 취소선 쪽 근거(*"가장 어려운 계열에 가장 강한 모델"* · Auditor A안과 같은 서버 상주)는 옛 배정의 것이고 **교체판의 근거는 이 ADR에 아직 없다** — 정본은 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1, 오늘 드는 것은 `config/models.yaml` 「확정 로스터」 주석·실험팀 결과뿐이다. ~~`qwen3-coder-30b-a3b`는 B1 기준으로 남으므로 registry 키는 안 지운다~~ → 2026-09-24 이 키도 B1 기준 행도 지웠다(아래 B1 행). ⏵ **2026-09-25 — 다시 `qwen3-14b`로 옮겼다**(실험팀 최종 권고를 팀이 받았다 — 파인튜닝한 Qwen3-14B를 쓸 계획이고 체크포인트 전까지 베이스 키다. 근거는 팀 보고이고 run ID가 없다 — 정본은 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1). |
| 4단계 · `scout_boundary` (CWE-119·120·121·122·124·125·126·127·129·131·170·190·191·193·194·195·197·369·680·681·787·805) | ~~`devstral-small-2-24b`~~ → ~~`seccoderx-8b`~~ → **`qwen3-14b`** | ~~Mistral~~ → ~~SecCoderX~~ → Alibaba (`family: qwen`) | ~~fp8~~ → bf16 | **2026-09-21 교체.** ⚠ **2026-09-25 전까지 memory와 같은 키였다** — `seccoderx-8b` 한 벌이 두 자리를 프롬프트 v3의 역할 폴더로 가른다(`scout_memory/v3`=옛 실험 v14s · `scout_boundary/v3`=옛 v15s). 같은 weights라 별칭 키를 새로 두지 않는다. ⚠ 그래서 Scout 셋의 `family`가 전부 `qwen`이 됐다(§4가 보는 것은 auditor↔critic이라 CI는 안 잡는다). ~~`devstral-small-2-24b`는 실서빙 `critic`으로 남으므로 키는 안 지운다~~ → 2026-09-24 지웠다(로스터 밖 정리) ⏵ 2026-09-25 — memory가 `qwen3-14b`로 옮겨 이 키는 boundary 하나였다. 한 키를 두 자리가 나누는 것은 그때 memory·injection(`qwen3-14b`)이었고, Scout 셋의 `family`는 여전히 전부 `qwen`이다. ⏵ **2026-10-14 — 이 자리도 `qwen3-14b`로 옮겼다**(실험팀 프롬프트 개선 작업에서 정해진 교체 · 원천은 팀 Notion이고 이 저장소는 배선만 든다 — 결정 5. 정본은 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1). **선정을 가른 실측은 이 저장소에 없다** — run ID 없음(CLAUDE.md §0). 그 결과 `seccoderx-8b`가 로스터에서 내려와 registry에서 지웠고(§3 ②) **Scout 세 자리가 한 키**다 — 한 키를 여러 자리가 나누는 것이 셋으로 늘었다. ⚠ **공동오답 행렬(§3)에서 모델 대비가 사라졌다**: 세 축이 재는 것이 전부 프롬프트 차이다. |
| 4단계 · `scout_injection` (CWE-77·78·88·89·94·116·134) | ~~`qwen3-8b`~~ → ~~`deepseek-r1-distill-qwen-32b`~~ → **`qwen3-14b`** | ~~Alibaba~~ → ~~DeepSeek (Qwen2.5 베이스)~~ → Alibaba (`family: qwen`) | ~~nvfp4~~ → bf16 | **2026-09-24 교체** — 2026-09-23 팀 피드백(「모델 크기를 내리자」)으로 `qwen3-14b`로 옮겼다. 근거 표는 ADR 0012(PR #260) 결정 4이고 배선은 2026-09-24 팀 지시다. 그날 ATOM 스모크에서 10/10이 첫 시도에 스키마를 통과했고 단일 요청 생성이 8.1 tok/s였다(`config/models.yaml`의 그 항목). `benchmark`는 여전히 0이다. ~~`deepseek-r1-distill-qwen-32b` 키는 registry에 남는다([PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) 「로스터에 없지만 registry에 남는 것」).~~ → 같은 날 로스터 밖 정리로 지웠다. **2026-09-21 교체.** (⏵ 2026-09-24 — 이 문단은 그날 키 `deepseek-r1-distill-qwen-32b`의 기록이다. 지금 키 `qwen3-14b`는 Alibaba 공식 Qwen3(`Qwen/Qwen3-14B`)라 아래 「계열 … 확정 필요」가 해당하지 않는다.) 취소선 쪽 근거(가장 쉬운 계열 + batch32 **388.46 tok/s** [실측])는 옛 배정의 것이고 **새 키에는 그 실측이 없다**(`benchmark` 여섯 필드가 0·null). ⚠ 계열은 베이스가 Qwen2.5인데 DeepSeek 재학습이라 확정 필요 — §4 「베이스 제작사」로 일단 `qwen`. ~~`qwen3-8b`는 실서빙 단일 `scout`으로 남으므로 키는 안 지운다.~~ (⏵ 2026-09-24 — 두 키 다 지웠다. 그래서 앞의 「계열 … 확정 필요」도 대상이 없다.) `phi-4` 기각은 §7 그대로다 |
| 5단계 · Auditor | ~~**A안 `qwen3-coder-30b-a3b` / B안 `nemotron-3-super-120b-a12b`**~~ → ~~`qwen3.6-35b-a3b`~~ → **`qwen3.8-27b`** | ~~Alibaba / NVIDIA~~ → Alibaba | ~~nvfp4~~ → bf16 | **2026-09-21 A/B가 소멸했다.** *"문헌이 갈려 실측으로 가른다(D24)"*던 갈래가 없어지고 자리가 하나가 됐다 — ⚠ **가르는 실측이 나온 게 아니라 회의가 다른 키를 골랐다**(근거는 이 ADR에 없다 · 정본 §1). B안 `nemotron-3-super-120b-a12b`는 §1에서 내려왔고 registry 미등재라 온보딩 전이었다. ⚠ **`qwen3.6-35b-a3b`의 §7 기각은 2026-09-09의 것**이고 §7이 함께 적은 *"2차 후보로 남긴다"*가 올라온 경로다. dtype `bf16`은 config.json `text_config.dtype: "bfloat16"` **선언값**이고, **비전-언어 모델**이다(`config/models.yaml` 주석 참조) **2026-09-26 — 다시 교체했다**(팀): Qwen/Qwen3.8-27B — dense 27.8B(옛 `qwen3.6-35b-a3b`는 MoE 35B·활성 3B였다) · GDN hybrid · VLM · bf16 native(2026-09-26 Hugging Face 직접 판독). **추론(사고)을 켠다**(`reasoning_effort: xhigh` — PROJECT-CONTEXT.md §1). 고른 근거는 벤더 모델 카드 비교뿐이고(Qwen3.6-27B 대비 — 우리 35B-A3B와 직접 비교 아님) `[출처주장]`, 품질 실측은 0이다(`checksum` 0 · `benchmark` 0). |
| 7단계 · Critic | **`gpt-oss-20b`** + `reasoning_effort: high` (신규) | OpenAI | mxfp4(네이티브) | **기동 실측 있음** — [2026-09-09 온보딩 보고서](../experiments/harness/2026-09-09-gpt-oss-20b-onboarding-atom.md): 기동 통과 · 가중치 14.91~16.06 GiB · **structured output 실패**(아래 「다시 열리는 조건」) |
| B1 기준(최강 단일 멤버) | ~~`qwen3-coder-30b-a3b`~~ | Alibaba | nvfp4 | 실측 있음. ~~**지우지 않는다**~~ — build-plan §8이 요구하는 "항상 최강 단일 멤버와 비교"의 그 멤버였다. **2026-09-24 이 키와 `b1_single`을 지웠다**(로스터 밖 정리 — [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1 끝) |

**Scout 세 멤버의 배정은 09-08 조사가 근거를 댔다(②).** 초안 시점엔 임시였다 — 근거가 될 실측이 없다 — C 결함에서 가장 많은 계열(Boundary)에 가장 큰 모델을, 실측이 있는 `qwen3-8b`를 UAF/DF에, 나머지를 Injection에 뒀을 뿐이다. **첫 run의 공동오답 행렬**(누가 무엇을 함께 놓쳤나 — `Candidate.scout_member`로 만든다)이 재배치의 유일한 근거이고, 그 전에는 옮기지 않는다. ⚠ **2026-09-24 — 이 규칙대로 되지 않았다.** 2026-09-21 교체의 근거는 이 행렬이 아니라 실험팀 결과다(위 표 Scout 세 행). [ADR 0012](0012-harness-canon-v2.md) 결정 4는 Scout 좌석을 「교체 가능」으로 둔다 — 결정 기한 2026-09-30. (⏵ 같은 날 팀이 세 좌석을 확정했다 — 위 표 Scout 세 행 · [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1.)

**"Mistral-Small-24B" 자리를 `devstral-small-2-24b`로 채운 이유.** 발표안이 적은 이름은 Mistral-Small-24B인데, Devstral Small 2는 그 24B 베이스(`base_model: mistralai/Mistral-Small-3.1-24B-Base-2503` — HF 메타데이터, 2026-09-06 조회)의 코드 파생이고 **이미 ATOM에서 기동·checksum이 끝나 있다.** 바닐라 `Mistral-Small-3.2-24B-Instruct-2506`을 고집하면 얻는 것은 이름이고 대가는 온보딩 하나다. 바꾸고 싶으면 registry 키 하나·프로필 한 줄이다.

### 3. Scout 풀은 **역할 셋**이다 — 팬아웃 · 합집합 · 발화 멤버 태깅

> **2026-09-14 — 이 구조가 실측으로 확증됐다.** 실험팀이 CWE 계열 분담을 추론 유형(연역·귀납·귀추) 분담과 실측으로 비교했고, `Qwen3-Coder-30B-FP8`에서 CWE 계열 분담이 `MCC +0.239`·`pair_acc 46/148`로 연역/귀납/귀추의 어느 구성보다 높았다([scout-reasoning §9](../research/2026-09-08-scout-reasoning.md), 실험팀 별도 하네스 · `run_id` 없음). **확증된 것은 계열 세 Agent 「구조」다** — 실험이 쓴 CWE→Agent 세부 목록은 아래 §2 표·프롬프트와 다르고, 어느 목록을 정본으로 할지는 [D75](../open-questions/D/D75.md)로 남긴다(오늘은 아래 표 유지). 어느 모델이 어느 자리인지는 여전히 아래 첫 run 공동오답 행렬이 정한다. ⚠ **2026-09-24 — 앞 문장은 그렇게 되지 않았다** — §2 표 바로 아래 노트.

- 풀 멤버는 `Role`에 **`scout_memory`·`scout_boundary`·`scout_injection`** 셋을 더해 표현한다. 프로필은 `roles:`에 그 셋을 적고, 기존 `scout` 한 역할 프로필도 그대로 유효하다(둘 중 하나가 있어야 한다).
- **팬아웃**: 4단계에서 대상 하나를 세 멤버가 각각 본다(캡슐은 한 번 만든다). **합집합(방식 ①)**: 임계값을 넘은 멤버의 후보를 **고르지도 합치지도 않고 각각** 5단계로 보낸다 — 함수 하나에 Finding이 여럿 생길 수 있고 9단계 dedupe가 같은 함수의 것을 합친다. 판정은 6단계 게이트가 근거로 한다(build-plan §5 "투표가 아니라 분업이다"). 함수당 하나로 합쳐 Auditor를 한 번만 부르는 방식 ②는 **Auditor가 받은 근거가 어느 멤버 것인지 섞여** 기각했다 — 공동오답 행렬이 흐려진다. Auditor 호출은 최대 3배지만 멤버가 CWE 계열로 갈려 있어 실제 겹침은 첫 run이 답한다. **태깅**: `Candidate.scout_member`를 신설해 하네스가 stamp한다 — 모델이 채우는 값이 아니다(`produced_by`와 같은 논법). ⚠ **2026-09-24 — 이 불릿의 「임계값을 넘은」은 [ADR 0012](0012-harness-canon-v2.md) 결정 2가 「최종 신호가 `triage_review`인」으로 바꾼다** — 좌석마다 고르지도 합치지도 않고 각각 보내는 방식 ①과 방식 ② 기각은 그대로다(그 ADR 결정 2의 「좌석 집계」 불릿).
- **왜 "roles를 리스트로" 대신 역할 셋인가.** 계획 D절은 `roles: dict[str, str]`를 리스트 수용으로 넓히자고 적었고 그 파급(config·factory·cli·bench·scout.py)을 스스로 셌다. 역할로 두면 기록·라우팅 층이 **역할 키를 새로 정의하지 않고** 따라온다. 다만 「공짜」의 범위는 아래대로다.

  | 자리 | 풀 셋을 넣을 때 드는 것 | 근거 |
  | --- | --- | --- |
  | `RunRecord.model_checksums` | **0** | `config.model_checksums()`가 `profile.roles.items()`를 그대로 돈다 |
  | `ProducedBy.role` | **0** | 필드 타입이 `Role`이라 열거형이 늘면 따라온다 |
  | `RunRecord.prompt_versions` | `PROMPTED_ROLES`에 **멤버당 한 줄** | `prompt_versions()`가 `for role in PROMPTED_ROLES` |
  | `RoutingModelClient` | 같은 상수의 **같은 줄** | `factory`의 `clients` 컴프리헨션이 같은 상수에 걸린다 |
  | `bench.ROLE_SPEC` | 멤버당 한 줄 | 없으면 `ROLE_SPEC[role_enum]`이 `KeyError` |
  | 역할별 토큰 카운터 | **러너는 첫 멤버만 켠다 · 기록은 안 따라온다** (2026-09-11 정정) | 아래 ⚠ |

  즉 **상수 두 곳에 줄을 더하는 일**이고, 리스트 안이 요구하던 것은 그것이 아니라 위 여섯 전부에
  "리스트의 몇 번째"라는 **새 좌표**였다. 계획의 의도(N멤버 · 태깅)는 같다.

  ⚠ **초안은 이 자리들이 전부 0이라고 적었다. 그 문장이 틀렸다.** 정정도 한 번 틀렸다 —
  「기록 넷이 0」이라고 좁혔는데, 그 넷 중 `prompt_versions`·`RoutingModelClient` 둘이
  `PROMPTED_ROLES`에 걸려 있다(위 표). 0인 것은 **둘**이다.

  ⚠ **2026-09-11 정정 (PR #178) — 아래 문단의 절반이 거짓이 됐다.** 러너는 이제
  `_step_retrieval`에서 `ctx.counter_for_role(item.role.value)`를 부른다. ⚠ **켜지는 것은
  그 target의 첫 멤버 하나다** — 캡슐이 공유돼 나머지 멤버는 그 자리에 도달하지 않는다
  (`{scout_memory, auditor}` `[실측 2026-09-11]`). **여전히 참인 것은 기록 쪽뿐이다** — `counter_report`가 `COUNTED_ROLES` 둘만 돌아
  풀 멤버 카운터가 켜져도 `token_counter` 이벤트에는 안 실린다. 아래는 그 시점의 기록이다.

  ⚠ **역할별 토큰 카운터는 조회는 되지만 켜지지 않는다.** `counter_for_role(role)`은 프로필이
  선언한 역할이면 카운터를 주는데(가르는 것은 `COUNTED_ROLES`가 아니라 `profile.roles`다),
  부르는 쪽인 `runner._triage_one`이 `counter_for_role(Role.SCOUT.value)`로 박혀 있고 기록을 내는
  `counter_report`는 `src.tokens.COUNTED_ROLES = ("scout", "auditor")`만 돌며
  `tests/test_tokens.py`가 그 둘을 등식으로 고정한다. `nodes/scout.py`도 `ROLE = Role.SCOUT` 하나다.
  즉 풀 멤버가 4단계를 도는 날 그 캡슐은 기본 카운터(글자수 근사)로 회계된다. 늘리는 것은 러너를
  옮기는 PR의 몫이다 — 지금 늘리면 러너가 안 켜는 역할이 `token_counter` 이벤트에 실려 기록이
  거짓이 된다.

### 4. 계열 규칙을 코드가 강제한다 (D59 (a))

`ModelEntry`에 **`family`**(베이스 가중치 제작사 — 양자화한 조직이 아니라 가중치의 출처 조직)를 넣고, `load_config`가 **auditor와 critic이 다른 모델 키면 `family`가 달라야 한다**를 검사한다. 같은 키(B1)는 정의상 허용한다. 2026-09-04에 `foundation-sec-8b`(Cisco 배포·Llama 베이스)가 registry에서 빠져(#139) D59가 든 "계열 정의의 애매함" 실례가 없어졌고, 남은 값은 전부 자명하다 — `qwen`(Alibaba)·`mistral`·`openai`·`fake`, 그리고 Auditor B안을 고르면 `nvidia`. ⚠ `microsoft`는 여기 없다 — `phi-4`가 그 유일한 항목이었는데 §7이 기각했다. ⚠ **2026-09-24 — 이 값 목록은 2026-09-09 로스터의 것이다.** 「Auditor B안을 고르면 `nvidia`」는 2026-09-21 교체로 B안이 소멸해 대상이 없고(§2 Auditor 행), `openai`는 registry에 `gpt-oss`로 올라갔다(`config/models.yaml:514`). ~~「전부 자명하다」도 이제 아니다 — `deepseek-r1-distill-qwen-32b`의 `qwen`은 §2 `scout_injection` 행이 「확정 필요」로 적는다.~~ → 그 예였던 `deepseek-r1-distill-qwen-32b`는 로스터 밖 정리로 지웠다(2026-09-24). 이 문단의 줄 번호는 `c301342` 기준이다.

### 5. 1차 분석 대상은 **C/C++ 소스 저장소**다 (D9 닫힘)

킥오프 04-1의 "펌웨어" 문구보다 중간발표 슬라이드 8의 "C/C++ 오픈소스 Zero-Day"가 팀의 현재 목표이고, 이번 구조의 슬라이서가 **소스 기반**(Joern CPG)이라 펌웨어를 1급 입력으로 두면 1단계가 성립하지 않는다. (⚠ **2026-09-13 정정** — 이 문장은 원래 *"슬라이서 둘(LLM4FPM · LLMxCPG-Q)"*이라 적었다. 방식이 무모델 Case A/B로 바뀌어 그 둘은 안 쓰지만 **Joern CPG 기반이라는 근거는 그대로**이므로 결론은 안 바뀐다 — §2 슬라이서 행.) 바이너리·펌웨어는 2차다. [architecture.md](../architecture.md) §3의 0·1단계 서술을 그에 맞춘다.

### 6. 프롬프트 버전

~~새 로스터의 실서빙 모델(`qwen3-8b`·`devstral-small-2-24b`·`qwen3-coder-30b-a3b`와 신규 셋)은 **전부 `v2`**다.~~ **2026-09-21 로스터 교체로 이 문장의 「새 로스터」가 09-09 판이 됐다.** 그때 §1 로스터의 프롬프트 버전은 `seccoderx-8b` `v3`(boundary 하나, 2026-09-25 전에는 memory·boundary 한 키 · ⏵ **2026-10-14 — 그 키가 로스터에서 내려왔다.** boundary가 `qwen3-14b`로 옮겨 Scout 세 자리가 한 엔트리이고, 그 엔트리의 `prompt_template_version` 하나가 풀 전체의 판이다 — 아래 「풀은 한 버전」이 구조로 보장된다)·~~`deepseek-r1-distill-qwen-32b` `v3`~~ → `qwen3-14b` `v3`(2026-09-24 injection으로 등재, 2026-09-25부터 memory도 같은 키 — 역할 폴더가 내용 가름)·~~`qwen3.6-35b-a3b`~~ → `qwen3.8-27b` `v1`(2026-09-26 교체 — 프롬프트는 안 바꿨다, Auditor 프롬프트 미확정이라 디스크의 v1 · ⏵ 2026-09-29 — `v3`로 올렸다, 근거 본문 배선과 같은 변경 · 같은 날 `v4`로 다시 올렸다 · ⏵ **2026-10-03 — `v6`로 올렸다**(#418·#421 · `v5`는 건너뛰었다) · ⏵ 2026-10-08 — 그 뒤 `v7`·`v8`(2026-10-04) · `v9`(PR #508) · `v10`(PR #521)으로 올랐고 지금 판은 `config/models.yaml` `qwen3.8-27b`의 `prompt_template_version`이 정본이다 — [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §5 Auditor 행)이다 — 풀 세 멤버가 **전부 `v3` 한 버전**이라 아래 「풀은 한 버전」 규칙을 충족한다. D54의 (c)("기본을 v2로 올린다")가 막혀 있던 이유는 도는 2×2 매트릭스를 무효화한다는 것이었는데 그 매트릭스는 2026-09-01에 폐기됐다. 풀 세 멤버가 다른 버전이면 같은 단계에 다른 계약이 섞이므로 **풀은 한 버전이어야 한다.** ~~`qwen3-4b`(`single_gpu_12g` 전용)는 `v1`에 둔다.~~(⏵ 2026-09-24 — 그 키와 프로필은 지웠다.) D54는 이것으로 닫지 않는다 — "어느 조건에 어느 버전을 쓰는가"의 설계 기록이 남는다(부분). ⏵ 2026-10-10 — D54는 닫혔다(그 설계 기록은 실험팀 범위로 옮겼다 — [D54](../open-questions/D/D54.md) 머리 블록).

### 7. 기각

| 안 | 기각 사유 |
| --- | --- |
| Critic `DeepSeek-R1-Distill-Llama-70B` (발표안) | bf16 140 GB로 적재 불가(계산값), 양자화판은 sm_121 미검증, Auditor도 DeepSeek이면 "제작사가 다른 모델" 위반 — [D64](../open-questions/D/D64.md) ①·③. **D64는 이 ADR로 기각·닫힘** |
| Auditor `DeepSeek-Coder-V2-Lite` (발표안) | 2024년 모델 · NVFP4 없음(FP8만) — models.md §2.1·§4.2 |
| Scout `QwQ-32B` (발표안) | thinking 모델이라 답 하나에 수천 토큰 — 후보 전량이 지나는 4단계에서 그 비용이 후보 수만큼 곱해지고, thinking 모델의 구조화 출력 결함군(models.md §3.5.4)이 그 경로다 |
| 조합 A `Nemotron-3.5-Lightning` + `gpt-oss-20b` (연구 권장 1순위) | GB10 검증 명시·NVFP4 상태 SWE-bench 공개(52.8)는 장점이나 코드 성능 보고가 후보 중 낮고 취약점 탐지·critic 실측 0건. 팀이 "보고 성능 최대"(조합 B)를 골랐다. **Nemotron은 Auditor 2차 후보로 남긴다** |
| 조합 C `Gemma-4-26B-A4B` + `Nemotron` | Gemma4가 vLLM structured output 결함(#39130) 당사자 · Critic 근거 0건 |
| 조합 D 현행 `qwen3-coder-30b-a3b` + `Nemotron` (변수 최소) | Coder가 2025-07 모델. **B1 기준으로 남겨 비교 대상이 된다** — 버린 것이 아니다(⏵ 2026-09-24 — 그 키와 `b1_single`은 지웠다 — §2 B1 행) |
| Scout·Injection `phi-4` (이 문서 초안) | **컨텍스트 16,384**라 LIGHT 캡슐 예산 4,000 + 대상 원문이 그 안에 들지 미확인이고, 추론 유형 배정에 논문 근거가 없다(09-08 조사). 대신 `qwen3-8b`가 **388.46 tok/s [실측]**로 그 자리에 맞는다. **2026-09-09 기각 — 온보딩 하나가 줄었다** |
| Auditor `qwen3.6-35b-a3b` (이 문서 초안) | 미실측이고 서빙 근거가 B안보다 약하다(09-08 조사). Auditor는 A/B로 재기로 했고 그 둘이 `qwen3-coder-30b-a3b`(실측 있음)와 Nemotron-120B다. **2026-09-09 기각 — 2차 후보로 남긴다**. ⚠ **2026-09-24 — 2026-09-21 교체가 이 키를 5단계로 올렸다**(§2 Auditor 행) — 이 행은 2026-09-09 판단의 기록이다 (⏵ 2026-09-26 — 그 자리에서도 다시 내려왔다: 지금 Auditor는 `qwen3.8-27b`다 — PROJECT-CONTEXT.md §1.) |
| Auditor·Critic 둘 다 Qwen | 조건 2 정면 위반 — models.md §5.5 |
| Scout: 소형 베이스 하나 + 전문가 프롬프트 셋(→ LoRA 셋) | 온보딩 최소·상주 최소라 초안의 권장이었으나, 팀이 발표안의 모양(다른 제작사 셋)과 멤버 간 오류 상관 가능성을 택했다. **FT2(계열별 LoRA)는 이제 베이스 셋에 각각이다** — 비용이 는다 |
| 후보 단위 실행 유지 | Scout 배칭 못 씀 · 상주 전부 동시 · 교대 512회(swap.md §6.2 계산값) |

## 결과

**얻는 것.** 발표안과 research가 같은 방향을 가리키는 자리(Scout 오케스트레이터·MoE 둘·단계 단위)가 코드 좌표로 내려온다(§1·§3). 계열 규칙이 코드로 지켜진다(§4 — D59 (a)).

**보류는 여기에 넣지 않는다.** 여섯을 `열림`으로 바꾼 것은 #151이고, **이 문서가 새로 푸는 것은 없다.** 남은 둘이 그것을 보여 준다 — **D51**은 `trigger_status`를 내는 것이 Auditor라 A/B 미결이 단독으로 막는 유일한 항목인데(인덱스 §2 머리) **이 문서는 그 A/B를 리뷰의 몫으로 남긴다**(위 ②·⑤). **D3**은 애초에 모델 문제가 아니다 — `expand()`의 호출부가 저장소에 없어 어떤 모델로 돌아도 `capsule_expansion_max`가 안 걸린다(D3 본문 2026-09-08). **이 문서가 보류에 대해 갖는 몫은 「로스터 제안」이라는 근거 자리 하나이고, 리뷰가 로스터를 바꾸면 그 근거도 함께 움직인다.** ⚠ **2026-09-24 — 이 문단의 「A/B 미결」은 2026-09-21 교체로 대상이 사라졌다.** D51을 막는 것은 *"「그 단일 모델의 run이 아직 없다」"*로 바뀌었다 — [D51](../open-questions/D/D51.md)의 2026-09-21 절. 그리고 D3의 확장 상한은 [ADR 0012](0012-harness-canon-v2.md) 결정 6이 need_evidence 루프의 `max_hops`로 대체한다(Phase 5, 미구현 — `c301342`에 `max_hops` 0건). (⏵ 2026-09-26 — 구현됐다 — `max_hops`가 config에 있고 `_run_ne_hops`가 그 값을 강제한다. D3 닫힘.) (⏵ 2026-10-10 — D51도 닫혔다: 서빙 판의 응답 스키마가 `trigger_status` 다섯 값을 강제하고 `Finding` 필드는 `str` 그대로 — [D51](../open-questions/D/D51.md) 머리 블록.)

**잃는 것.** ~~다음 벤치마크 전에 **새 모델 둘**(`gpt-oss-20b`·`qwen3-reranker-4b`) — Auditor B안을 고르면 `nemotron-3-super-120b-a12b`까지 **셋** —~~ **2026-09-21 대상이 바뀌었다**(§2). B안이 소멸해 조건절이 없어졌고, ~~오늘 `config/models.yaml`에서 `checksum`이 placeholder(0)인 것은 확정 로스터의 **다섯 키**(`gpt-oss-20b`·`qwen3-reranker-4b`·`seccoderx-8b`·`deepseek-r1-distill-qwen-32b`·`qwen3.6-35b-a3b`)다([R8](../open-questions/R/R8.md)의 2026-09-21 절이 그 표를 든다).~~ 무엇이든 ATOM에서 받아 checksum과 §3 체크리스트를 통과시켜야 한다 — 공용 장비 시간이 가장 비싼 자원이다. 그리고 취약점 탐지 성능은 **후보 전부 문헌에 없으므로**(models.md §3.3.1) 이 로스터가 낫다는 근거는 우리가 만들어야 한다. ⚠ **2026-09-24 — 이 문단의 「다섯 키」는 틀렸다**(취소선 — [R8](../open-questions/R/R8.md)의 2026-09-21 절도 같은 문장을 그었다). `gpt-oss-20b`(`config/models.yaml:512`)·`qwen3-reranker-4b`(`:635`)의 checksum은 0이 아니고 `config/checksums/`에 파일도 있다 — 2026-09-11 등재 때부터다(`3100061` #176 · `3f84d59` #179). 그 문장(`ce6a4c8`, 2026-09-22 — 가리킨 로스터는 2026-09-21 교체판)은 그 열하루 뒤에 쓰였다. placeholder(0)이고 `source`에 `@rev`도 없는 것은 `seccoderx-8b`(`:664-665`)·`deepseek-r1-distill-qwen-32b`(`:687-688`)·`qwen3.6-35b-a3b`(`:715-716`)다. (⏵ 2026-09-24 — 이 문단의 줄 번호는 `c301342` 기준이다. 그 뒤 injection이 `qwen3-14b`로 확정돼 `deepseek-r1-distill-qwen-32b`는 지웠고, `qwen3-14b`는 `checksum`이 0이지만 `source`에 `@rev`가 있다. 두 칸이 다 빈 것은 지금 `seccoderx-8b`·`qwen3.6-35b-a3b`다. ⏵ 2026-09-26 — `@rev`를 박아(2026-09-24 스모크가 서빙한 리비전) 그 둘도 `source`에 `@rev`가 있고, 빈 칸은 `checksum`뿐이다.)

⏵ 2026-09-26 — `qwen3.6-35b-a3b`는 로스터 밖이 돼 registry에서 지웠다. 그 자리의 새 키 `qwen3.8-27b`는 `source`에 처음부터 `@rev`가 있고(`@1d4bf0f…`) `checksum`만 0이다.

**메모리 — 단계 단위에서 동시에 떠 있는 것만 센다.** 계산값과 실측을 갈랐다.

| 단계 | 상주 | 가중치+non-torch (GiB) | 라벨 |
| --- | --- | --- | --- |
| ~~1 · 쿼리 생성~~ | ~~`qwen3-8b`~~ | ~~6.38~~ | **2026-09-13 소멸** — 1단계가 무모델이 되어 상주가 없다(§2 · [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §2). 실측 6.38은 그 자리가 있던 때의 값이다 |
| 3 · 검색 | `qwen3-embedding-0.6b` + `qwen3-reranker-4b` | 1.62 + 약 9 = **약 10.6** | 실측 + 계산(4B × 2바이트 + non-torch) |
| 4 · Scout 풀 | ~~`qwen3-coder-30b-a3b` + `devstral-small-2-24b` + `qwen3-8b`~~ → `seccoderx-8b`(2026-09-25 전에는 memory·boundary 한 키, 지금은 boundary 하나) + ~~`deepseek-r1-distill-qwen-32b`~~ → `qwen3-14b`(2026-09-24 injection, 2026-09-25부터 memory도 — §1) | ~~18.28 + 26.8 + 6.38 = **51.46**~~ → **미실측(0)** | **2026-09-21 상주가 바뀌었다**(§2). 취소선 합 51.46은 옛 배정의 실측 — [09-08 조사](../research/2026-09-08-roster.md) §1.1. 새 셋은 `benchmark`가 전부 0·null이라 합을 적을 수 없다(CLAUDE.md §0). ⏵ **2026-09-24** — `seccoderx-8b`는 16.05로 쟀다(가중치+non-torch, vLLM 로그 — [스모크 보고서](../experiments/harness/2026-09-24-roster-smoke-atom.md) §2). ~~`deepseek-r1-distill-qwen-32b`는 안 띄웠으므로 **합은 여전히 못 적는다.** 그 자리의 잠정 후보 Qwen3-14B(registry 밖)는 28.27이다~~ → **같은 날 그 자리가 `qwen3-14b`로 확정됐다**(§2). 두 키를 다 쟀으므로 합은 16.05 + 28.27 = **44.32** `[계산]`이다(가중치+non-torch · 서버마다 vLLM 로그 — 위 보고서 §2). ⏵ 2026-09-25 — 자리만 바뀌고 두 모델은 그대로라 이 합도 그대로였다. ⏵ **2026-10-14 — boundary가 `qwen3-14b`로 옮겨 Scout 풀의 상주가 모델 하나다.** 합은 **28.27** GiB다(가중치+non-torch · 그 전 44.32 = 16.05 + 28.27). 16.05는 내려온 키의 값이라 더는 안 더한다 |
| 5 · Auditor | ~~**A** `qwen3-coder-30b-a3b` / **B** `nemotron-3-super-120b-a12b`~~ → ~~`qwen3.6-35b-a3b`~~ → **`qwen3.8-27b`** | ~~A **18.28** / B **79.12**~~ → ~~**미실측(0)**~~ → ~~68.6(2026-09-24)~~ → **미실측(0)**(2026-09-26) | **2026-09-21 A/B가 소멸했다**(§2). 첫 취소선은 옛 후보의 값(A 실측·B 계산)이고, 둘째(~~미실측(0)~~)는 2026-09-24까지의 상태다. 새 키는 `benchmark`가 전부 0·null이다. ⏵ **2026-09-24 — 쟀다**: 가중치 65.53 + non-torch = 68.6 GiB다(vLLM 로그 · util 0.64 · `--enforce-eager` — 위 보고서 §2). `benchmark`는 여전히 0이다(그 칸은 `bench --apply`가 채운다) ⏵ 2026-09-26 — 자리가 다시 바뀌었다. 68.6은 옛 `qwen3.6-35b-a3b`의 2026-09-24 ATOM 실측이라 옮기지 않는다. `qwen3.8-27b`의 가중치는 51.75 GiB(계산값 — HF safetensors 크기 합)이고 적재 크기(가중치+non-torch)는 미실측이다. ⏵ 2026-09-29 vast.ai에서 쟀다 — 가중치+non-torch **51.91 GiB**(vLLM 로그, 장비가 GB10이 아니다 — `config/serving/vastai.yaml`의 그 행). |
| 7 · Critic | `gpt-oss-20b` | ≤16 | 출처주장(OpenAI "16GB 메모리 안에서 동작") — 바이트 미확인 |

~~**피크는 A/B로 갈린다 — A안이면 4단계 51.46 GiB, B안이면 5단계 79.12 GiB(가중치)이고**~~ **2026-09-21 A/B가 소멸해 이 갈림이 없어졌다**(§2 · 위 표). 취소선 두 수는 옛 로스터의 것이고 **새 로스터의 피크는 미실측(0)이다.** (⏵ 2026-09-24 — 5단계는 쟀다: `qwen3.6-35b-a3b` 68.6 GiB(위 표). ~~4단계 정본 풀의 합은 `deepseek-r1-distill-qwen-32b`를 안 띄워 아직 모른다 — 그래서 피크가 어느 단계인지도 아직 모른다.~~ → injection이 `qwen3-14b`로 확정돼 4단계 합도 적는다 — 44.32 `[계산]`(위 표). 가중치+non-torch만 놓고 보면 5단계(68.6)가 크다 `[계산]`. ⏵ 2026-09-26 — 5단계 모델이 바뀌어 위 68.6은 옛 모델의 값이다. 새 모델의 적재 크기가 미실측이라 어느 단계가 피크인지는 다시 모른다(가중치만은 51.75 GiB — 계산값). ⏵ 2026-09-29 — vast.ai에서 잰 5단계 가중치+non-torch 51.91 GiB가 4단계 합 44.32 `[계산]`보다 크다 `[계산]` — 장비가 다른 실측이라 GB10에서 다시 본다.) **KV는 `--gpu-memory-utilization`으로 우리가 정한다** — 9/4 실측이 "제약은 KV 예산 배분"이라고 답한 그대로다. [09-08 조사](../research/2026-09-08-roster.md) §1.1이 단계 예산을 **≈105 GiB**(`119.7 − 약 15(미상 오버헤드)` — 그 15는 [9/4 보고서](../experiments/harness/2026-09-04-five-model-residency-atom.md) §3이 `nvidia-smi` PID 합과 `free` 사용의 차로 **관측만 하고 내역을 안 갈랐다**)로 잡고 **피크인 B안 79.12에서도 KV가 26 GiB 남는다**고 적는다. ⚠ **「전부 동시」 합은 다시 계산하지 않았다** — 초안의 ≈93 GiB는 기각된 `phi-4`(≈14)·`qwen3.6-35b-a3b`(≈21)를 더한 값이라 2026-09-09 확정 로스터의 수가 아니고, **그럴듯한 값으로 갈음하지 않는다**(CLAUDE.md §0). 단계 단위가 선행인 이유는 그 합이 아니라 **단계별 상주만 세면 어느 단계도 예산 안에 든다**는 §1.1이다 — 그 표의 여섯 단계 전부 KV 여유가 양수다. 교대는 그 다음이다.

**되돌리는 비용.** 모델은 registry 키를 지우지 않으므로 프로필 한 줄이다. ⚠ **2026-09-24 개정 — 로스터가 확정돼 로스터 밖 키·프로필은 지운다**(팀, [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1 끝). 되돌리기는 이제 git 이력에서 항목을 되살리는 것이다. Scout 풀 역할 셋은 `Role` 열거형·`prompts/` 디렉터리·`Candidate.scout_member` 필드다 — 지울 수 있으나 그 사이 run의 `RunRecord`가 그 역할 키를 들고 있으므로 옛 manifest 로드를 깨지 않는 방향(필드 유지)으로만 되돌린다. 단계 단위 러너는 `execution.mode` 기본값(`per_candidate`)이 옛 경로를 남기므로 스위치 하나다(`ADR 0009`).
**§5(1차 대상 범위)를 되돌리는 비용은 서술 일곱 자리다** — 목록과 「고치지 않은 자리」는 [D9](../open-questions/D/D9.md)의 2026-09-19 절이 든다. **스키마는 안 지웠으므로**(`ArtifactKind.FIRMWARE`·`Artifact.parent_id`·`runner._ANALYZABLE`) 되돌리는 것은 문서뿐이고, 그 정렬에서 나온 잔여는 [D79](../open-questions/D/D79.md)다.

## 리뷰에서 정할 것 — **2026-09-09 팀 리뷰로 넷 다 닫혔다**

이 문서가 답하지 않은 구조 질문 넷이었고 **2026-09-09에 넷 다 닫혔다** — #2(인덱서)는 §2 슬라이서 행으로, 나머지 셋은 아래 리뷰 disposition으로. 발표 자료와 저장소 코드가 서로 다르게 말하는 자리라 **팀이 정해야 하고**, 각 줄에 이 초안이 기울어 있는 쪽을 적었다.


> **2026-09-09 — 팀이 PR #148을 승인하며 남은 셋을 disposition했다.** 셋 다 「초안의 기울기」
> 열에 동의하는 방향이고, 요지는 *"세 자리 모두 발표 자료와 코드가 갈리던 곳인데 **코드/실측
> 쪽으로 확정**하는 것이 맞다"*이다. 아래 표의 「초안의 기울기」가 그대로 결정이 됐다.
>
> | # | 결정 | 리뷰가 든 사유 |
> | --- | --- | --- |
> | 1 | **넣지 않는다** (코드 쪽 유지) | Scout은 후보 전량 단계라 배칭 이득(10.9배 [실측])이 핵심인데, 후보마다 임베딩을 부르면 그것이 무너지고 [D63](../open-questions/D/D63.md)(리랭커 미검증)이 4단계 입력에 바로 걸린다 |
> | 3 | **예시로 읽는다** | `candidate_max 256`·`critic_top_n 8` 자체가 근거 없음([D24](../open-questions/D/D24.md)). 슬라이드 숫자를 목표로 박지 않는다 |
> | 4 | **A안(데이터셋 정답 PoC 재생) 먼저** | E4(`proven`)에 PoC 소스가 필요하고, LLM PoV 에이전트(B안)는 더 큰 별도 결정이다 |
>
> ⚠ **넷이 닫혔다고 이 절을 지우지 않는다.** 표가 「발표 자료 대 코드」의 대조를 들고 있고,
> 그 대조가 왜 이렇게 갈렸는지가 나중에 다시 필요하다.

| # | 질문 | 발표 자료 | 지금 코드 | 초안의 기울기 |
| --- | --- | --- | --- | --- |
| 1 | **Scout 캡슐에 검색 결과(`related`)가 들어가는가** | 슬라이드 17 — Embedder→Reranker→Retriever가 만든 캡슐을 **Scout에게** 전달 | LIGHT(Scout)는 검색 안 함, EXPANDED(Auditor)에만 `related`(`hybrid_retriever.LEVEL_SECTIONS`) — "후보 전량 단계에서 임베딩을 부르면 배칭 전제가 무너진다" | 코드 쪽 유지. 검색을 Scout에 넣으면 3단계 비용이 후보 수만큼 붙고 [D63](../open-questions/D/D63.md)의 리랭커 미검증이 4단계 입력에 바로 걸린다 |
| ~~2~~ | ~~**인덱서에서 확정된 것**~~ → **2026-09-09 확정, §2 슬라이서 행으로 옮겼다. 2026-09-13에 방식이 바뀌었다** | 슬라이드 13·14 — 둘 다 | tree-sitter 인덱서와 독립 오프라인 [Joern 슬라이서](../slicer-README.md)가 있다. 슬라이스 소비 경로는 2026-09-18에 **(a) 후보 열거에만**으로 확정됐고([D74](../open-questions/D/D74.md) 닫힘 · 캡슐 경로의 선행조건은 [D78](../open-questions/D/D78.md)), **확정이지 배선은 아직 없다.** **실서버 검증은 2026-09-15에 끝났다**([보고서](../experiments/harness/2026-09-15-joern-slicer-v2-atom.md)). ⚠ **2026-09-24 — 이 칸의 「(a) 후보 열거에만」은 [ADR 0012](0012-harness-canon-v2.md) 결정 1이 대체한다** — 슬라이스가 Scout의 코드 입력이고 후보는 슬라이스 1개다. 「배선은 아직 없다」는 그대로다 — `git grep -n -i slice -- src config`가 0건이다(`c301342`). ⏵ 2026-09-25 — 배선이 들어왔다(Phase 3a PR #283 · 3b PR #284 · 3c): `run --slices`면 슬라이스 1개가 타깃 1개이고 Scout는 그 렌더를 받는다. `--slices` 없는 run은 그대로 tree-sitter 심볼이다 | **결정(2026-09-13 현재): Joern 도입 · 무모델 결정론(Case A/B 합집합 → 클러스터링) · tree-sitter 인덱서는 짝으로 존치 · 1단계에 모델 자리 없음.** ⚠ 2026-09-09 판본은 *"`LLM4FPM` → `LLMxCPG-Q` 순 · 슬라이서가 설 때까지 tree-sitter 병행 · 쿼리 생성기 `qwen3-8b`"*였다 — 셋 다 바뀌었다. 발표안 아티팩트의 Code Slicing 박스를 이번 범위에 넣는다 |
| 3 | **후보 수 50 → 30 → 10**(슬라이드 10)이 임계값 목표인가 예시인가 | 숫자가 박혀 있다 | `candidate_max 256`(근거 없는 관례) · `critic_top_n 8` | 예시로 읽는다. 목표라면 `critic_top_n`을 10으로 — 8도 근거가 없다 |
| 4 | **Validation의 Exploit은 누가 만드는가** | 슬라이드 21 — "Exploit을 통해 실제 취약 여부를 검증" | PoC를 만드는 주체가 없어 E4(`proven`)가 실서빙에서 도달 불가(STATUS.md §5) | 데이터셋 정답 PoC 재생(STATUS §5 선택지 A)을 먼저. LLM PoV 에이전트(B)는 별도 결정 |

## 후속

> ⚠ **2026-09-24 — 아래 1·2·4·5의 「Auditor B안」·「A안·B안」 조건절은 2026-09-21 로스터 교체 전 서술이다.**
> 그 교체로 5단계가 한 자리가 되며 A/B가 소멸했다(§2 Auditor 행 · 정본 [PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1).
> 그래서 1의 B안 온보딩은 대상이 없고, 2의 R8 대상 집합은 그 뒤 다시 바뀌었으며(R8 본문의 2026-09-21 절),
> 4·5가 [D24](../open-questions/D/D24.md)에 건 「run 두 번」은 그 항목의 2026-09-21 절대로 **한 번**이 됐다 — 겹칠
> 두 Auditor가 없다. 1이 든 두 모델(`gpt-oss-20b`·`qwen3-reranker-4b`)은 2026-09-11에 등재·checksum을 마쳤다
> (`3100061` #176 · `3f84d59` #179).
> **3의 「도구 루프 재진입 신호 #60」은 [ADR 0012](0012-harness-canon-v2.md) 결정 6이 폐기한다** — 모자란
> 맥락은 재평가(requeue) 대신 need_evidence 루프가 같은 세션 안에서 채운다(Phase 5, 미구현 — ⏵ 2026-09-26
> 구현됐다, `src/orchestrator/runner.py`의 `_run_ne_hops`). 같은 줄의 「`Evidence.location` 채우기 #70」은 #248(`73b1fba`, 2026-09-24)이
> **semgrep 히트에 한해** 채웠다 — clang-tidy는 채우지 않는다(`src/adapters/container_tools.py`의 `first_hit_location`).

1. **온보딩 둘(Auditor B안이면 셋) — 순서와 각각의 관문.** (⚠ 위 2026-09-24 노트) 다운로드 → 스냅샷 → `tools/model_checksum.py` → `checksum`·`source` → §3 체크리스트 7개 → `benchmark`. 채우기 전엔 0이다. 순서는 위험이 큰 것부터:
   - `gpt-oss-20b` — **Harmony × structured output smoke가 첫 관문이다.** vLLM 이슈 #23120·#37359가 not-planned로 닫혀 있고 0.27.0의 PR #45560이 그 경로를 겨냥했는데 우리 0.27.1에서 재현되는지 모른다(models.md §3.5.4). 깨지면 "자유 서술 → 별도 추출" 2단(quantization.md 제안)이 다음 수다.
   - `qwen3-reranker-4b` — **키우기 전에 [09-08 조사](../research/2026-09-08-roster.md) §2의 배제 셋을 먼저 한다** — [D63](../open-questions/D/D63.md)이 온보딩(스냅샷·`checksum`)과 그 배제 셋을 (a) 앞의 관문 둘로 세운다. 그 다음 **D63의 (a)**(40줄 코드 조각 × 식별자 질의)를 이 모델로 잰다(⏵ 2026-09-24 — 2026-09-13 프로브는 짧은 함수 8건이라 아직 이 모양이 아니다). 0.6B의 관측은 이 모델에 이식하지 않는다. ⚠ **2026-09-20 — 이 순서는 지켜지지 않았다. 살아 있는 지시로 읽지 마라.** 온보딩(등재·`checksum`)은 2026-09-11에, 기동과 **(a) 첫 실측**은 2026-09-13에 됐으나 **배제 셋은 ②(템플릿)만 닫혔고 ①(참조 구현 대조)·③(리랭커를 끈 기준선)이 열린 채** 두 풀 프로필(`local_balanced_pool`·`local_balanced_pool_hybrid`)이 이 모델로 배선됐다. **이 문서가 그 결정을 뒤집지 않는다** — 무엇을 알고 넘었는지와 남은 것은 [D63](../open-questions/D/D63.md)의 2026-09-20 블록이 든다. **「0.6B의 관측은 이식하지 않는다」는 그대로 유효하다.**
   - *(Auditor B안을 고른 경우에만)* `nemotron-3-super-120b-a12b` — **FlashInfer 금지 · Triton attention · MTP=1.** 카드의 "1M context"와 config의 `262144`가 달라 **262,144를 상한으로 잡는다.** (⚠ 위 2026-09-24 노트)
2. **R8 등재** — 새 로스터 신규 모델 둘(Auditor B안이면 셋)의 sm_121 기동·구조화 출력·Critic 반박 정확도가 미검증이라는 프로젝트 수준 리스크. **등재 당시 넷이던 대상이 §7의 기각 둘(`phi-4`·`qwen3.6-35b-a3b`)로 줄었다.** R2(Devstral NVFP4 Critic)는 로스터 변경으로 닫힌다. (⚠ 위 2026-09-24 노트)
3. **PR 셋.** A(이 문서 · registry · 문서 · D 상태) → B(스키마: `execution`·`family`·풀 역할·`scout_member` · 검증기 · 테스트) → C(러너 단계 단위 · 팬아웃 · 8단계 dedupe 선행). 모델 무관 선행 둘(계획 D절 — `Evidence.location` 채우기 #70, 도구 루프 재진입 신호 #60)은 **별도 PR**이다. (⚠ 위 2026-09-24 노트)
4. **보류 여덟 — 이 문서가 할 일은 없다.** 상태도 각 항목의 다음 입력도 #151이 인덱스와 D 본문에 이미 적었다(D4를 "고정밀 대비"에서 **반박 정확도 표본 자체**로 재정의한 것도 그 PR이다). ⚠ **그리고 그 다음 입력들은 「새 로스터 첫 run」 하나가 아니다** — 풀린 여섯 중 첫 run이 답하는 것은 **D2·D28**(멤버별 score 분포·고유값 수)뿐이고, **D24**는 A안·B안 **run 두 번**("한 번으로는 못 정한다"), **D54**는 `prompt_template_version`을 v2로 올리는 **PR**, **D29**는 **로스터와 무관한** 정책 판단 둘, **D4**는 표본 크기·합격선을 정하는 설계 판단이다. **첫 run 하나로 여섯이 닫힌다고 읽지 않는다.** (⚠ 위 2026-09-24 노트)
5. **오류 상관을 잰다**(models.md §6.3, 계획 D절 "교차검증 상관 재확인"). Scout 멤버 셋의 공동오답 행렬은 `scout_member`로 첫 run에서 나온다. Auditor 후보 둘(**A안** `qwen3-coder-30b-a3b` 대 **B안** `nemotron-3-super-120b-a12b` — §2)의 오답 겹침은 같은 입력으로 두 번 돌려 센다([D24](../open-questions/D/D24.md) — *"한 번으로는 못 정한다"*). (⚠ 위 2026-09-24 노트)
6. **Critic 계측** — 통과율(100%에 가까우면 합격이 아니라 고장, models.md §3.4.2)과 position bias(A/B 순서 뒤집기, §3.4.5)를 trace에서 뽑는다.
7. **평가 규칙** — 바뀐 하네스는 항상 B1(`qwen3-coder-30b-a3b` 단일)과 비교한다(⏵ 2026-09-24 — 그 키와 `b1_single`은 지웠다 — §2 B1 행). 평균이나 지난 조건과 비교하지 않는다(build-plan §8).
8. **공지** — 계획 착수 순서 1번(타팀 정보전달, 70B 실측 근거 포함)은 팀이 맡는다.

## 이 결정이 다시 열리는 조건

> ⚠ **2026-09-24 — 아래 둘째·셋째 줄은 Auditor A/B를 전제한다.** 2026-09-21 교체로 그 갈래가 소멸해(§2 Auditor 행)
> 두 조건 다 대상이 없다 — ~~5단계와 B1 기준은 지금 다른 키이고([PROJECT-CONTEXT.md](../../PROJECT-CONTEXT.md) §1 표),~~ (⏵ 로스터 밖 정리 뒤 — B1 기준 키는 지웠고 §1 표에서 그 행을 뺐다)
> B안은 온보딩 전에 §1에서 내려왔다(같은 파일 §1).

- 온보딩 관문에서 `gpt-oss-20b`의 구조화 출력이 우리 버전에서 깨지고 2단 추출로도 안 닫히면 → Critic을 Nemotron-3.5-Lightning(조합 A/C의 Critic 자리)으로 바꾼다. 계열 규칙은 그대로 만족한다.
- **Auditor A/B가 A안으로 갈리면** → 5단계는 `qwen3-coder-30b-a3b`이고 **B1 기준과 같은 모델이 된다.** 그때는 B1 대조가 「같은 모델 대 같은 모델」이 되므로 build-plan §8의 「항상 최강 단일 멤버와 비교」를 다른 축(단계 단위 대 후보 단위)으로 읽어야 한다 — **이 문서가 답하지 않는다.** (⚠ 위 2026-09-24 노트)
- **B안(`nemotron-3-super-120b-a12b`)이 sm_121에서 안 뜨면** → A안으로 확정하고 위 줄을 따른다. B안 온보딩은 그 시점에 취소한다. (⚠ 위 2026-09-24 노트)
- 첫 run의 공동오답 행렬에서 멤버 둘이 같은 것을 함께 놓치면 → 그 둘 중 하나를 바꾼다. 어느 쪽인지는 그 행렬이 정한다.
- **바이너리·펌웨어를 1급 입력으로 되돌려야 하면 → §5를 다시 연다.** 근거와 고친 자리 목록은 [D9](../open-questions/D/D9.md)의 2026-09-19 절이, 그 정렬이 남긴 잔여는 [D79](../open-questions/D/D79.md)가 든다. **되돌리는 것은 서술뿐이다** — 위 「되돌리는 비용」.
