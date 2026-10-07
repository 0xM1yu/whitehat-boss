# Auditor 파인튜닝 계획이 밟는 하네스 사실 · PoV · 벤치마크 조사

**조사일 2026-09-29.** 라벨: `[출처주장]`(저자·벤더 자기 측정) · `[제3자]`(독립 측정) ·
`[계산]`(우리 산술) · `[코드]`(이 저장소를 직접 열어 확인) · `[미확인]`.
**여기 있는 것은 실측이 아니다** — 우리 장비 실측은 [experiments/](../experiments/README.md)에 있다.
(§5의 `[계산]`만 예외로 **입력이 우리 실측**이고, 그 사실을 같은 문장에 적었다.)

**이 조사가 결정을 내리지 않는다**(CLAUDE.md §3). 벤치마크·조건 설계는 실험팀 소관이고([ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 5),
**파인튜닝 방법·학습 데이터·teacher·학습 인프라는 팀 FT 가이드(Notion)가 든다** — 하네스가 지는 것은 학습 자체가 아니라 셋이다(결정 7).
**그래서 이 문서는 학습 계획을 평가하지 않는다.** 처음 판(`0583ddd`, merge commit 머지 가정)에 있던 SFT/RL 방식 대조(§2)·데이터 요청 필드(§3)·약관(§7)·학습 인프라(§8)는
삭제했다(PR #376 리뷰 MEDIUM 5 — git 이력에 남는다). 남기는 것은 **하네스가 밟는 사실**(§4)·**규모 계산**(§5)·**벤치마크 사실**(§6)이고,
§1은 계획이 근거로 댄 레퍼런스 페이지가 실제로 말하는 것을 적는다. 특히 §4(하네스 전제)는 **값이 아니라 가부가 걸린 자리**라 먼저 읽는다.

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
> ③ **「Phase 7의 정본에 CyberGym은 없다 · 「Phase 7 = CyberGym」은 아직 저장소 정본이 아니다 · 벤치마크 선택은 실험팀 소관」**(§6.1·§9.A 1·§9.B) → `docs/decisions/0012-harness-canon-v2.md:196`이 `5990dff`(2026-09-30 14:32 KST)부터 「검증 벤치마크 = CyberGym Level 1」을 적는다(조민규 결정 2026-09-30 — 원천 링크는 저장소에 없다고 그 줄이 스스로 적는다). 같은 줄이 벤치마크 **설계**(데이터셋·정답지·조건)는 그대로 실험팀 몫이라 적는다.
> ④ **「PR #327(gdb) 미머지」**(§4.4) → `5cf5c20`(2026-10-01 03:29 KST)으로 main에 머지됐다.
> ⑤ **「미커밋 조사 문서」**(§6.1의 `2026-09-29-cybergym-pov-design.md`) → 그 문서는 PR #376이 처음 저장소에 올린다(`0583ddd`, 2026-10-02 01:47 KST) — 그 PR이 머지되기 전에는 여전히 main에 없다.
> ⑥ **「Auditor 프롬프트가 v1(`config/models.yaml:468`)이고 v4 PR #328이 열려 있다」**(§4.1 ①) → PR #328은 2026-09-29T12:52:38Z(21:52 KST)에 머지됐고 `6e005ef`(2026-09-29 22:54 KST, 제목 「rewire the canonical auditor from prompt v3 to v4」)가 정본 Auditor를 v4로 배선했다.
> 지금 `config/models.yaml`의 `qwen3.8-27b` 항목은 `:403`부터이고 `prompt_template_version`은 `:546`의 `v4`다. ⏵ 2026-10-03 — 그 뒤 #418·#421이 v6로 올렸다: 지금은 `qwen3.8-27b` 항목의 `prompt_template_version: "v6"`다(2026-10-03 기준 `:578` — 항목 시작 `:403`은 그대로). ⏵ 2026-10-08 — 그 뒤 v7·v8(2026-10-04) · v9(PR #508, 2026-10-06 머지) · v10(PR #521, 2026-10-07 머지)으로 올랐다 — 「지금은 v6」은 그날까지다. 판은 그 항목의 `prompt_template_version`이 정본이고 줄 번호는 여기 다시 적지 않는다. 이 문서의 `config/models.yaml:` 줄 인용은 전부 `b52f847` 기준이라 줄 번호가 옮겨 갔다.
> 이 노트가 다루는 것은 위 줄들뿐이다 — 나머지 `[코드]` 서술은 이 PR에서 다시 열지 않았다.

---

## 0. 결론 넷 — 그리고 각각이 무엇을 막는가

처음 판의 결론은 여덟이었다 — SFT 방식(§2.1)·RL 설계(§2.2)·요청 필드(§3)·약관(§7)은 위 삭제와 함께 뺐다.

| # | 결론 | 근거 | 무엇이 걸리나 |
| --- | --- | --- | --- |
| **1** | **레퍼런스가 탐지 파인튜닝 문서가 아니다** — CyberGym Level 1 **PoC 생성** 에이전트 리포트다 | §1 | 이 레퍼런스가 다루는 것은 **PoV 에이전트**(코드 → PoC 바이트)이고 Auditor(코드 → Finding JSON)가 아니다 |
| **2** | **계획 1단계가 규모에서 안 선다** — 실측 단가로 대상 1건당 **6.57시간**, 18,377건이면 **13.8년** | §5 | 하네스 전체를 태우는 방식은 **수백 건이 상한**이다. 우회로가 있고 그 대가가 §5.3이다 |
| **3** | **하네스가 아직 학습 데이터를 못 낸다** — ADR 0012 결정 7의 세 전제 중 **①이 흔들리고 ②가 반쪽, ③이 없다** | §4 | Auditor 프롬프트가 **v1**이고 v4 PR이 열려 있다. 사고 토큰이 기록되지 않는데 Auditor는 **사고를 켠다** ⏵ 「v1·v4 PR 열림」은 낡음 — 위 시점 노트 ⑥ |
| **4** | **CyberGym은 탐지를 안 잰다** — PoV 생성 벤치마크다. 우리 `evaluation.md` §5.1은 탐지 지표다 | §6 | Phase 7에 CyberGym이 들어가면 그것은 **PoV 채점기**다. Auditor 파인튜닝 효과는 그것으로 **직접 못 잰다** ⏵ 「들어가면」은 낡음 — 위 시점 노트 ③ |

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

**페이지에 없는 것 — 이 페이지를 학습 방법의 근거로 인용하면 닿지 못하는 한계다.**
데이터 **규모** · 하이퍼파라미터(lr·epoch) · **CoT/reasoning 언급 자체** · teacher distillation ·
**GRPO/DPO/ORPO 어느 것도** · 데이터 비율. 전부 **페이지에 없음**이다.

> **이 페이지가 올린 것은 Auditor(코드 → Finding JSON)가 아니라 PoV 에이전트**(코드 → PoC 바이트, 툴 루프 3,000턴)다. 그 페이지의 RL은 **실행 피드백 보상**(빌드·크래시 = verifiable)이고 teacher distillation은 말하지 않는다.
>
> 하네스 쪽에 닿는 사실 하나: 베이스가 **Qwen3.8-27B — 우리 Auditor와 같은 모델**이고
> (PROJECT-CONTEXT.md §1 · `config/models.yaml`의 `qwen3.8-27b` 항목 `[코드]` — `d85ea62` 기준 `:403`부터, 처음 판의 `:364-474`는 `b52f847` 줄 번호),
> 페이지는 그 베이스의 **CyberGym Level 1 점수를 54.51%**, 그 위의 파인튜닝 결과를 **88.9%**로 든다 `[출처주장]`. 학습 방법의 선택은 팀 FT 가이드 소관이라 이 문서가 평가하지 않는다.

⚠ **88.9%는 {모델 + 그 팀 스캐폴드 + 3,000턴 + 256K}의 값이지 모델 단독이 아니다** —
이 저장소가 반복 확인한 *"스캐폴드가 숫자를 4배 움직인다"*([vulnerability-detection 조사](2026-09-08-vulnerability-detection.md) §3)와 같은 함정이다.
**제3자 재현은 0건이고 리더보드는 자기신고 호스팅이다.**

---

## 2. (삭제) 계획 3단계를 문헌에 대조한다 — 실험팀 소관

> ⏵ **2026-10-02 — 이 절은 삭제했다**(PR #376 리뷰 MEDIUM 5). SFT 데이터 생성 방식(rationalization ↔ rejection sampling) · RL 알고리즘(DPO/ORPO ↔ GRPO) · 학습 데이터의 정상:취약 비율을 문헌과 대조한 절이었다.
> 파인튜닝 방법·학습 데이터·teacher·학습 인프라는 팀 FT 가이드(Notion)가 들고, 하네스가 지는 것은 학습 자체가 아니라 입력 형식·원문 기록·내보내기 셋이다([ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 7).
> 이 폴더에는 **하네스 결정·결함의 근거가 되는 조사만** 남긴다([README](README.md)). 처음 판은 git 이력 `0583ddd`의 이 파일에 있다(merge commit 머지 가정 — squash 머지면 PR 브랜치에서만 열린다).

---

## 3. (삭제) 요청한 데이터 필드 검토 — 실험팀 소관

> ⏵ **2026-10-02 — 이 절은 삭제했다**(PR #376 리뷰 MEDIUM 5). 학습 데이터 요청 필드와 빠진 필드(출처 데이터셋·라벨링 방법, 중복 제거, 언어, 빌드 가능 여부, 커밋 날짜, 「정상」의 정의, 정답 위치 입도)를 다룬 절이었다. 데이터셋·정답지는 실험팀 몫이다(결정 5).
> 파인튜닝 방법·학습 데이터·teacher·학습 인프라는 팀 FT 가이드(Notion)가 들고, 하네스가 지는 것은 학습 자체가 아니라 입력 형식·원문 기록·내보내기 셋이다([ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 7).
> 이 폴더에는 **하네스 결정·결함의 근거가 되는 조사만** 남긴다([README](README.md)). 처음 판은 git 이력 `0583ddd`의 이 파일에 있다(merge commit 머지 가정 — squash 머지면 PR 브랜치에서만 열린다).

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
> (`config/models.yaml`의 `qwen3.8-27b` 항목 — `d85ea62` 기준 `reasoning_effort` `:528`·`thinking_token_budget` `:535`, 처음 판의 `:458·466`은 `b52f847` 줄 번호). **사고를 켠 모델인데 그 사고를 하네스가 기록하지 않는다.**
> (처음 판에 있던 teacher 출력 형식과 사고 능력에 관한 수치 문단은 원천을 끝내 못 찾아 뺐다 — 출처를 못 대는 수치는 CLAUDE.md §0상 싣지 않는다. 처음 판은 이력에 있다.)

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
아래 규모 열의 18,377·11,371은 팀이 보유했다고 한 데이터 건수(취약 11,371 + 패치 7,006)다 — 출처 데이터셋·정의는 이 문서가 다루지 않는다(§3 삭제).

| 규모 | 순차 wall | |
| --- | --- | --- |
| vul+patch 전량 **18,377** | 120,800시간 | **13.8년** |
| vul 전량 **11,371** | 74,746시간 | **8.5년** |
| 1,000 | 6,573시간 | 274일 |
| 300 | 1,972시간 | 82일 |
| **100** | **657시간** | **27일** |

⚠ **가정을 적는다.** ① 표본 3건이 대표적이다 ② 병렬 없음(Phase 6.5의 단계 안 동시 요청이 이 수를 나눈다 — 배수는 미실측)
③ 슬라이서 시간 미포함(#321·#322 성능 작업이 방금 머지돼 단가가 움직였다 — 미실측) ④ 장비 1대
⑤ **Scout 좌석 p50(84.6~103.9초)은 서버 컨텍스트를 운영 값(`context_limit` 40,960)보다 작은 16,384로 띄운 run `0005`(24호출)에서 왔다** — 그 보고서가 스스로 「이 run의 설계 결함」이라 적은 조건이다(`docs/experiments/harness/2026-09-26-slice-smoke-atom.md`의 「Scout를 운영 값보다 작게 띄웠다」 행). 위 6.57시간은 그 조건의 값이고 운영 컨텍스트에서의 단가는 **미실측**이다.
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
- 레벨별 성공률(OpenHands+**GPT-4.1** — 논문 Fig. 6, 2026-10-02 원문 대조; 처음 판의 「GPT-4o」는 오기): **level 0에서 3.5%**만 성공 — 정보량이 성공률을 크게 가른다.
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
> 그 검사의 입력(학습 데이터의 중복 상태·커밋 날짜)은 학습 데이터를 만드는 쪽의 일이라 이 문서가 다루지 않는다(§3 삭제).

### 6.4 탐지를 재려면 별도 벤치마크가 필요하다 `[출처주장]`

PrimeVul(pair-wise **5,480쌍**) · VulDetectBench · CASTLE · SecVulEval 등이 탐지 축이다.
PrimeVul은 F1 대신 **VD-Score**(FPR을 임계 이하로 고정한 뒤 FNR 측정)를 제안한다 — 실배포의 비대칭을 F1이 못 담아서다.

> **취약/패치 쌍이 있는 데이터는 pair-wise 평가셋이 된다.** 어떤 데이터를 평가에 쓰는지는 실험팀 몫이다(데이터셋·정답지, ADR 0012 결정 5).

---

## 7. (삭제) Anthropic 이용약관 — teacher 선택은 학습 계획의 일이다

> ⏵ **2026-10-02 — 이 절은 삭제했다**(PR #376 리뷰 MEDIUM 5). 학습 계획의 teacher 모델에 걸리는 약관·라이선스를 다룬 절이었다 — 하네스 결정에 닿는 사실 한 줄만 아래에 남긴다.
> 파인튜닝 방법·학습 데이터·teacher·학습 인프라는 팀 FT 가이드(Notion)가 들고, 하네스가 지는 것은 학습 자체가 아니라 입력 형식·원문 기록·내보내기 셋이다([ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 7).
> 이 폴더에는 **하네스 결정·결함의 근거가 되는 조사만** 남긴다([README](README.md)). 처음 판은 git 이력 `0583ddd`의 이 파일에 있다(merge commit 머지 가정 — squash 머지면 PR 브랜치에서만 열린다).

> **사실 한 줄** `[출처주장]`: Anthropic Usage Policy(Effective September 15, 2025 — <https://www.anthropic.com/legal/aup>, 2026-10-03 조회)는 입력·출력으로 AI 모델을 학습시키는 것(예: *"model distillation"*)을 *"without prior authorization from Anthropic"* 금지 항목으로 든다 —
> teacher가 Claude이면 해당될 수 있어, [ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 7의 Scout 프론티어 teacher 증류([build-plan.md](../build-plan.md) §6, 어느 모델인지는 미지정)를 정하기 전에 사전 승인 확인이 선행된다. **이 저장소는 법률 판단을 하지 않는다**(해당 여부는 팀이 정한다).

---

## 8. (삭제) 학습 인프라 — 실무 수치

> ⏵ **2026-10-02 — 이 절은 삭제했다**(PR #376 리뷰 MEDIUM 5). 27B 학습에 필요한 장비·프레임워크·비용 수치를 모은 절이었다.
> 파인튜닝 방법·학습 데이터·teacher·학습 인프라는 팀 FT 가이드(Notion)가 들고, 하네스가 지는 것은 학습 자체가 아니라 입력 형식·원문 기록·내보내기 셋이다([ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 7).
> 이 폴더에는 **하네스 결정·결함의 근거가 되는 조사만** 남긴다([README](README.md)). 처음 판은 git 이력 `0583ddd`의 이 파일에 있다(merge commit 머지 가정 — squash 머지면 PR 브랜치에서만 열린다).

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
| **입력 생성 경로** | (A) 전체 run ↔ (B) Scout 생략 | §5.3 — 규모 3자릿수 ↔ ①전제 |
| **벤치마크** | CyberGym Level 1 ↔ E2E ↔ 탐지 벤치(PrimeVul 등) ↔ **둘 다** | §6 — ADR 0012 결정 5상 실험팀 소관 ⏵ 선택은 낡음(ADR 0012:196) — 위 시점 노트 ③ |

### 9.C 이 조사가 제안하는 선행 (만드는 것은 이 조사가 아니다)

1. **[ADR 0012](../decisions/0012-harness-canon-v2.md) 열린 항목 7 「Auditor SFT가 배울 것」**(기한 2026-10-04, 결정권 팀)을 닫는다 —
   **이 계획 전체가 그 항목의 답을 전제하는데 그 항목이 아직 열려 있다.**
2. **(B) 경로의 분포 차이 실측** — 표본 일부를 (A)로 돌려 Scout 신호 유무가 Auditor 출력을 얼마나 바꾸는지.

---

## 10. 이 조사가 확인하지 못한 것

- **슬라이서의 대상 1건당 단가** — #321·#322가 방금 머지돼 값이 움직였다. §5.3 (B) 경로의 실제 비용은 **미실측**.
- **feyospace-v1-small의 독립 측정** — 0건. 벤더 자기신고뿐이다([기존 조사](2026-09-29-cybergym-pov-design.md) §6과 같다).
- **XekRung 88.9%(모델트랙)의 제3자 재현** — 없음. 리더보드는 자기신고 호스팅이다.
- **열람한 어느 페이지에서도 에이전트를 겨냥한 지시문(prompt injection)은 발견되지 않았다** — 4개 조사 스레드 전부 동일 보고.
