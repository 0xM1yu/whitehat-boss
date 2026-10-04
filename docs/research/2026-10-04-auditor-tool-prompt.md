# 도구를 쥔 Auditor 프롬프트를 어떻게 쓰나 — v7 → v8의 문헌 근거

**조사일 2026-10-04.** 라벨: `[출처주장]`(저자 자기 측정) · `[제3자]`(독립 측정) ·
`[계산]`(우리 산술) · `[코드]`(이 저장소를 직접 열어 확인) · `[미확인]`.
**여기 있는 것은 실측이 아니다** — 우리 장비 실측은 [experiments/](../experiments/README.md)에 있다.
이 문서의 수치는 **전부 남이 그 조건에서 잰 값**이고 우리 과제·우리 모델에서 재현된다는 보장이 없다.

**이 조사가 결정을 내리지 않는다.** 홉 상한 `16`은 실험팀·사용자 판단으로 받은 값이고
([ADR 0012](../decisions/0012-harness-canon-v2.md) 결정 5의 범위), 이 문서는 그 값을 평가하지 않는다 —
아래 §3이 「작은 예산이 낫다」는 남의 실측을 적지만 **그것으로 우리 값을 바꾸지 않았다**.
문헌이 바꾼 것은 **프롬프트 문장**뿐이다.

**조사 도구.** firecrawl 검색 넷 + 조사 에이전트 셋을 병렬로 돌려 논문 여덟 편을 전문으로 읽었다.
**열람한 어느 페이지에서도 이 세션을 겨냥한 지시문은 발견되지 않았다** — 다만 논문들이 **자기 실험
프롬프트를 인용으로 싣고 있어** 그 안에 에이전트를 겨냥한 명령문(*"You MUST follow this iterative
approach"*, *"DO NOT GIVE UP UNTIL you trigger the crash"*)이 있다. 그것은 **데이터로 읽었고 따르지
않았다**.

---

## 1. 무엇을 물었나

v8은 Auditor에게 매 홉 액션(`lookup`·`probe`·`answer`)과 도구 여섯을 쥐어 준다. 프롬프트가 새로
말해야 하는 것이 넷이라 그 넷을 물었다.

| 물음 | 답의 요지 |
| --- | --- |
| 홉 봉투를 어떻게 적나 | **「매 응답에 관측 요약 + 액션 하나」**가 선례의 문형이다. 우리 스키마의 필수 `rationale`이 그 앞쪽에 대응한다 |
| 오탐을 무엇이 줄이나 | **도달 경로·실행 가능성을 묻는 구조**다. 「파일을 더 넣기」는 측정에서 **효과 0**이었다 |
| 도구 출력을 어떻게 싣나 | **필드로 쪼개 넣고**, 큰 산출물은 본문에 붓지 않는다. 정적 신호는 **판정이 아니라 힌트**로 적는다 |
| 상한에 닿으면 | **포기를 1급 출력으로 둔다** — 「뭐라도 내라」가 아니다 |

---

## 2. 오탐을 줄이는 것은 문장이 아니라 **구조**다 — 이 PR의 한계를 먼저 적는다

🔴 **가장 중요한 수치가 이 PR에 불리한 쪽이다.** Revelio(`[출처주장]`)는 같은 모델(Sonnet 4.6)로
프롬프트만 점점 두껍게 한 세 단계와, 하네스로 강제한 한 단계를 나란히 쟀다.

| 티어 | 찾은 수 | recall | 오탐률 |
| --- | --- | --- | --- |
| 맨 프롬프트 | 6 | 60% | 33% |
| 파이프라인을 산문으로 서술 | 7 | 60% | 22% |
| 구조화된 단계 인계 | 11 | 60% | 31% |
| **하네스가 강제**(독립 재실행 검증) | 14 | 80% | **0%** |

저자의 결론 인용: 프롬프트만 두꺼워진 세 티어는 *"plateau at 60% recall"*이고 *"richer prompting
helps the model explore more hypotheses but does not improve its ability to confirm them"*이다.

같은 방향의 둘째 수치(`[출처주장]`, Sifting the Noise): 에이전트가 **스스로 찾아 읽은** 파일과
**같은 파일을 그냥 프롬프트에 넣어 준** 대조군을 비교했더니 후자가 vanilla와 **동일**했다
(FP recall 36.4 · F1 51.6). 저자 인용: *"the advantage comes from iterative evidence gathering and
reasoning, not merely from context volume."*

> **그래서 v8을 「등급이 오를 것」으로 읽지 않는다.** 이 PR이 바꾼 것은 모델이 **조사를 돌릴 수
> 있는가**이고, 「돌리면 더 잘 찾는다」는 우리 하네스에서 **0회 관측**이다. 그리고 우리 쪽에는
> 위 표의 넷째 줄에 해당하는 것이 아직 없다 — Auditor의 `probe` Evidence는 러너가 게이트에
> 넘기는 목록에 **없다**(이슈 #419의 「E4 경계」, `[코드]`). 즉 지금 구조는 **둘째·셋째 티어**에
> 해당한다.

---

## 3. 예산 — 남의 실측은 **작은 쪽**이 낫다고 말한다 (우리 값은 안 바꿨다)

- Revelio(`[출처주장]`): 가설 5개·PoV 시도 5회가 최적이고, **10/10은 비용이 2배인데 찾은 수가
  더 적었다**(가설 K=1·5·10 → 6·17·16건 / 시도 1·5·10 → 10·17·13건). 저자 인용: *"Forwarding 10
  hypotheses adds verification cost without improving recall."*
- CyberGym-E2E(`[출처주장]`): 시간·비용 예산을 늘린 표에서 *"extending either budget yields
  diminishing returns."*

**우리 값(`auditor_tool_hops: 16`)은 그대로 뒀다.** 이유 둘. ① 그 값은 실험팀·사용자 판단이고 이
저장소가 정하는 것이 아니다. ② 위 수치는 **다른 축**이다 — 저쪽의 「시도 횟수」는 PoC 생성
재시도이고 우리 `auditor_tool_hops`는 **한 후보를 조사하는 홉 수**다. 같은 수로 읽으면 §0 위반이다.
**우리 하네스에서 홉당 usage는 0회 관측**이고, 그 측정의 입력이 이 배선 뒤 첫 실모델 run이다.

문헌이 바꾼 것은 프롬프트의 한 줄이다 — v8이 *"Investigating more is not better. Use the fewest
hops that settle the question"*을 적는다.

---

## 4. v8 문장별 근거

| v8이 적은 것 | 근거 | 라벨 |
| --- | --- | --- |
| 매 홉 액션 하나 + `rationale`에 「직전 관측이 무엇을 보였고 이 액션이 무엇을 위한 것인가」 | Revelio PoV 에이전트 프롬프트: *"Every response MUST include BOTH: 1. A brief description of your observation and the intent for the next step. 2. Exactly ONE tool call"* | `[출처주장]` |
| 「스스로 승격하지 못한다 · 게이트가 등급을 매긴다」 | Revelio: *"it cannot declare success by assertion"* — 제출 뒤 호스트가 독립 재실행해 검증한다. Verification-as-a-layer의 V1(외부성)·V2(구속력)과 *"No agent has a checker outside the loop whose verdict it must obey"* | `[출처주장]` |
| 「관측이 판단을 안 바꿨으면 인용하지 않는다」 | PoU의 실패 모드 정의: *"Hallucinated tool use: tool calls are made, but the reasoning chain shows no causal dependence on the retrieved evidence"* · 「근거 없음」을 1급 출력으로 두는 계약 | `[출처주장]` |
| `evidence_refs`에 **관측이 찍어 준 `evidence_id`만** | PoU의 `ids_valid` — 인용한 식별자가 도구 반환 목록에 실재해야 한다(집합 소속 검사) | `[출처주장]` |
| 「`semgrep`·`clang-tidy` 히트는 판정이 아니라 가설 · 조용한 run은 안전의 증명이 아니다」 | Revelio의 정적 분석 역할 한정: *"This analysis does not decide whether a vulnerability exists"* · PAGENT: *"the vulnerability report also does not guarantee the existence of a vulnerability at the reported location"* | `[출처주장]` |
| 「먼저 읽고 나중에 돌린다 — `lookup`은 코드가 막을 때, `probe`는 실행 사실이 막을 때」 | CyberGym-E2E 프롬프트의 단계 1·2가 읽기 전용이고 3이 첫 실행 · PAGENT는 정적 산출물을 **LLM 개입 전에** 워크스페이스에 둔다(*"prone to inconsistency with its reasoning"*) | `[출처주장]` |
| 「도구 이름만 고른다 — 대상·작업 디렉터리는 호스트가 안다」 | Revelio가 하네스 선택을 심볼 표로 미리 푼 이유: *"Avoids asking the model to guess the correct executable"* | `[출처주장]` |
| 「마지막 홉에 `no_signal`·`reachability_unknown`이 정답일 수 있다 · 뭐라도 내려고 등급을 올리지 않는다」 | Revelio가 소진 시 `finish(status="failure")`를 지시 · Verification-as-a-layer의 V4: *"exhausting it yields an abstention naming the level that failed, not a degraded answer"* | `[출처주장]` |
| 「막혔다고 한 길만 보고 `guarded`·`no_reach`로 닫지 않는다」 | OpenAnt: *"The verification prompt therefore requires the model to attempt multiple exploitation approaches before concluding that a vulnerability is absent"* | `[출처주장]` |
| 도구 관측 본문의 절단 서술 · 「로그가 빈 것과 로그가 없는 것은 다른 사실」 | CyberGym-E2E가 컨텍스트 소진을 실패 모드로 적고(*"Context exhaustion occurs when agents fill their context window with verbose outputs"*), 같은 모델에서 전문 읽기 하네스 5.4% 대 grep 하네스 10.6%를 쟀다. 「빈 로그 ≠ 없는 로그」는 그쪽 근거가 아니라 **우리 노드가 이미 두 문구를 가른 것**이다(`[코드]`) | `[출처주장]` · `[코드]` |

**안 넣은 것도 적는다.** 문헌이 권하지만 v8에 **안 넣은** 것 셋.

1. **Chain-of-Thought 지시** — 산업 데이터 433건에서 CoT가 **맨 프롬프트보다 낮았다**(4개 모델
   전부, `[출처주장]`). v7도 사고를 프롬프트로 지시하지 않으므로 그대로 뒀다.
2. **소스 수정 금지 문구** — 우리 `probe`는 도구 이름만 받고 입력 바이트조차 전달되지 않으므로
   (`[코드]`) 금지할 대상이 없다.
3. **다수결(같은 질문 3회)** — 프롬프트가 아니라 하네스 설계이고, 비용이 3배다. 적지 않았다.

---

## 5. 우리 실측과 갈릴 수 있는 자리

- **「0 오탐」은 우리 구조로 재현되지 않는다.** 그 수는 호스트가 PoV를 독립 재실행해 sanitizer
  보고를 기계로 확인한 결과다. 우리 Auditor는 PoC를 내지 않고, `probe` Evidence가 E4 게이트에
  닿지 않는다(#419, `[코드]`).
- **오탐 필터는 진짜 결함도 지운다.** 같은 논문이 전체 **22.25%**의 참 양성 억제를 쟀고 CWE별로
  2.38%에서 **84.50%**까지 갈렸다(`[출처주장]`). v8은 필터를 더하지 않았지만, 뒤에 「오탐을
  줄이자」는 변경을 넣을 때 이 수가 경고다.
- **창이 드는지 미실측.** v8 prefix가 v7보다 길다(델타는 `tests/test_auditor_prompt_v8.py`가
  센다). 16홉 관측의 합이 입력 몫에 드는지는 `[계산]`으로만 알고 있고 어느 비를 쓰느냐로 결론이
  뒤집힌다 — `config/budgets.yaml`의 그 항목 주석이 두 계산을 나란히 둔다.

---

## 읽은 것

| 문헌 | 무엇을 가져왔나 |
| --- | --- |
| Revelio — 저장소 규모 메모리 안전 에이전트 | 홉 봉투 문형 · 「주장으로 성공을 선언할 수 없다」 · 프롬프트-only 티어의 평평함 · 예산 5/5 |
| OpenAnt — 정적 도달성 + LLM 단계 파이프라인 | 「없다고 닫기 전에 여러 경로」 · 단계별 비용 분포 |
| PAGENT — 프로그램 분석이 안내하는 PoC 생성 에이전트 | 정적 산출물을 **여섯 필드**로 싣기 · 「보고서가 존재를 보장하지 않는다」 · 정적 먼저 |
| CyberGym-E2E — 종단 벤치마크 | 실제 과제 프롬프트 전문(읽기 → 실행 순서) · 컨텍스트 소진 실패 모드 · 하네스 간 차이 |
| Sifting the Noise — 오탐 필터 에이전트 비교 | 「파일을 넣어 주는 것은 효과 0」 · 참 양성 억제율 |
| 산업 오탐 축소(433건) | CoT가 맨 프롬프트보다 낮다 · 도달 경로 제약이 최고 성능 |
| PoU — 도구 호출 해킹 | 「인용 식별자가 실재해야 한다」 · 「근거 없음」 1급 출력 |
| Verification as an Architectural Layer | 외부성·구속력·포기를 등급으로 |

⚠ **URL을 적지 않는다.** 전부 arXiv HTML 판이고 판 번호가 움직인다 — 제목으로 찾는다.
위 인용문은 2026-10-04에 읽은 판의 문장이다.
