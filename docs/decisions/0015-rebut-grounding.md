# 0015. 근거 없는 `rebut`은 `need_evidence`로 간다 - 근거는 인용 ID 또는 닫힌 어휘의 반박 축이다

- 상태: **제안** - 승인은 @danielcho02
- ⏵ 2026-10-15 - 맨 아래 2026-10-15 개정(결정 2·4)은 @danielcho02가 승인했다. 위 상태 줄은 원 결정(2026-10-13)의 것이라 그대로 둔다.
- 일자: 2026-10-13
- 관련: [PR #588](https://github.com/0xM1yu/whitehat-boss/pull/588)(Critic v7 - 미머지, 디스크만) ·
  이슈 [#604](https://github.com/0xM1yu/whitehat-boss/issues/604)(`need_evidence` 5/5가 PoV 풀을 비움 - 닫힘) ·
  이슈 [#391](https://github.com/0xM1yu/whitehat-boss/issues/391) ·
  [D108](../open-questions/D/D108.md) ·
  [D106](../open-questions/D/D106.md)(같은 종류의 자기 신고) ·
  [agent-contracts.md](../agent-contracts.md) §2.3·§3 ·
  [ADR 0012](0012-harness-canon-v2.md) 결정 5(실험 설계는 실험팀)

## 배경

### 측정된 사슬 - 게이트가 반박 실패를 만든다

`gate.normalize`는 근거 없는 `rebut`을 **`uphold`로** 바꿨고(`is_grounded = bool(evidence_refs)`),
`gate.evaluate`의 `upheld`는 `any(normalize(d) is UPHOLD)`다. 그래서 근거 없는 `rebut` 하나가
`p3`(`p2 and upheld and repro_spec`)을 세워 E3에 올리고 정책 `_critic_verdict`가 `validated`로
보낸다. PR #588의 적대적 리뷰(2026-10-10)가 이를 **실행으로** 확인했다 - v7 모양의 `rebut`
(`evidence_refs=[]`)이 `validated`에 닿고 사유가 `critic_upheld`였다. 이 PR의 테스트
(`test_a_v7_shaped_rebut_no_longer_reaches_validated`)가 같은 사슬을 되만든다.

**Critic이 거른 오탐이 Critic이 거르지 못한 것보다 먼저 승격된다.** 불변식을 한 줄로 쓰면
*"Critic은 반박을 못 해서 Finding을 승격시키지 않는다."*

### v7이 이 사슬을 상시로 만든다

| 판 | `rebut`이 요구하는 것 | `evidence_refs` |
| --- | --- | --- |
| v6(결정 당일의 서빙 판) | 비어 있지 않은 축 + **비어 있지 않은 `evidence_refs`** + 본문 있는 Evidence (`prompts/critic/v6/prefix.md:152-158`, 예시 `:201`) | 채운다 |
| v7(디스크만) | 비어 있지 않은 축 + 축마다 **SOURCE 코드 인용**을 rationale에 (`prompts/critic/v7/prefix.md:207-210`, 예시 `:250`) | **비운다** - Evidence는 `uphold`에만 쓴다(`:87`·`:241`) |

v7을 그대로 배선하면 **모든 v7 `rebut`이 근거 없는 반박**이 된다. 같은 필드에서 180도 방향 전환이다
(이슈 #391의 2026-09-29 교대 스모크는 2/2 반박이 모두 비어 있지 않은 `evidence_refs`로 도착해 `queued`로
갔다 - Critic v1은 metadata만 받았는데도 모델이 받은 ID를 채웠다).

### 저자의 논증 - 그리고 이 ADR이 받는 부분

PR #588 코멘트(2026-10-10)에서 저자는 이렇게 논한다. **안전을 입증하는 Evidence 종류는 없다**
(`static`·`graph`·`crash`는 취약의 증거이고 "취약하지 않다"의 증거가 아니다). 그러므로 반박의 근거는
**SOURCE뿐**이고, `rebut`에 Evidence ID를 요구하는 것은 **충족할 수 없는 조건**이다. 이 ADR은 그
전제를 받는다 - 그래서 `evidence_refs`만 근거로 세는 술어를 유지할 수 없다.

⚠ **이 ADR이 받지 않는 것.** 저자의 설계가 *맞다*는 것과 v7이 *작동한다*는 것은 다른 명제다. CASTLE
수치(specificity 0.693 -> 0.803, Recall 0.873 -> 0.833)는 저자가 인용한 값이고 **이 저장소의 v7 run은 0건이다**
(ADR 0012 결정 5 - 실험은 실험팀 몫이고 여기서는 인용으로 받는다).

### H1 = ⓑ는 이 결함을 해결하지 않는다

Auditor probe Evidence를 Critic에 연결하는 결정(H1 ⓑ - Critic에만, 등급 제외)은 v7의 step 1을 세운다.
그러나 **step 1이 만드는 것은 `uphold`뿐이다** - v7은 *"Evidence ... never to rebut it"*이라 못 박았다.
`rebut`의 `evidence_refs`는 H1을 닫아도 그대로 빈다. 그래서 이 결정은 H1·H2와 **독립이다**.

## 결정

**G3 (3/3)와 G1 (2:1)을 함께 채택한다.** council 2026-10-13(외 목소리 셋 + architect)의 판정이다.

### 1. 폴백 목적지 - `need_evidence` (G3)

```python
# src/orchestrator/gate.py
def normalize(decision: Decision) -> Verdict:
    if decision.verdict is Verdict.REBUT and not decision.is_grounded:
        return Verdict.NEED_EVIDENCE
    return decision.verdict
```

근거 없는 `rebut`은 "Critic이 반박을 시도해 실패했다"가 아니라 "확인하지 못했다"이다. 정규화된
`need_evidence`는 모델이 낸 `need_evidence`와 **같은 값**이라 정책(F-7 `critic_need_evidence`)·게이트(`upheld`
거짓)·러너(PoV 풀)가 둘을 구별하지 않는다. `uphold`와 모델이 낸 `need_evidence`는 건드리지 않는다.

### 2. 근거의 정의 - 인용 ID 또는 닫힌 어휘의 축 (G1)

```python
# src/schemas/decision.py - Decision
is_grounded = bool(evidence_refs) or (
    bool(rebuttal_axis) and all(RebuttalAxis.from_text(a) is not None for a in rebuttal_axis)
)
```

- `RebuttalAxis`(`src/schemas/enums.py`)는 StrEnum **열 값**이다: `guard` · `reachability` · `aliasing` ·
  `lifetime` · `arithmetic` · `compile_flag` · `input_reachability` · `source` · `propagate` · `sink`.
  원천은 프롬프트다 - `prompts/critic/v7/prefix.md:193-202`(판정 규칙의 축 목록)과
  `prompts/critic/v6/prefix.md:141-144`(`Use only these prompt-level axis values:`)가 **글자까지 같은 열 개**를
  든다(`test_the_axis_vocabulary_matches_the_critic_prompts`가 두 파일을 읽어 대조한다).
- `from_text`는 **앞뒤 공백을 뗀 전체가 정확히 한 값일 때만** 읽고 아니면 `None`이다. **`strip()`이 허용하는
  유일한 정규화다** - 앞뒤 공백(`" guard "`, 탭·줄바꿈 포함)은 의미가 아니라 직렬화 잡음이라 어휘 안으로 읽고,
  그 밖의 모든 변형은 어휘 밖이다. 대소문자를 접지
  않고(`GUARD` -> `None`), 부분 문자열·접두어로 추측하지 않으며(`guard-check` -> `None`), 장식도 안 뗀다.
  `Verdict.from_text`의 규율(추측하지 않는다)을 가져오되 훨씬 좁다 - 축은 판정이 아니라 **근거의 자격**이라
  느슨하게 읽어 얻는 것이 없다.
- `DecisionDraft.rebuttal_axis`는 **`list[NonEmptyStr]` 그대로**다. 스키마에서 enum을 강제하면 grammar 마스크가
  모델의 맞는 답을 덮는다 - gpt-oss-20b 실측 2026-09-12 enum 강제 2/5, 제거 5/5(**원천: PR #182 본문,
  원문 미커밋** - `src/schemas/decision.py`의 `DecisionDraft` 주석이 그 사실을 적는다). 검증은 **파싱 뒤**에 한다.
- **`or evidence_refs`를 유지한다.** v6이 (이 결정 시점에) 서빙 중이고 그 `rebut`은 ID를 인용한다. v7은 rebut에 ID를
  **싣지 않는다**(`:241` - `evidence_refs`는 *only for an uphold confirmed by Evidence*) - 두 판의 규약이 반대이므로 술어가 둘 다 받아야 한다. 이 차이를 여기 적는다.
  ⚠ 부작용은 아래 「서빙 v6의 라우팅이 뒤집힌다」 표가 든다 - v6 규약을 어기고 **축만** 든 `rebut`
  (`evidence_refs=[]`)이 다른 길로 간다. v6 프롬프트가 요구한 ID 인용을 하네스가 강제하지는 않는다.
- ⏵ 2026-10-15 - 바로 위 「`or evidence_refs`를 유지한다」 항(그 ⚠ 부작용 포함)은 **v6·v7에만 선다**(v8 이상은 `or evidence_refs`가 없다 - 맨 아래 ⏵ 첫째).

### 3. 지표 - `critic_normalized`가 사유를 싣는다

`critic_normalized` 이벤트의 키: `original_verdict`(`rebut`) · `reason` · `invalid_axes`.
`reason`은 `empty_axis`(ID도 축도 없다) 또는 `invalid_axis`(축이 어휘 밖이다).
⏵ 2026-10-15 - 셋째 `rebut_ids_only`(v8 이상에서 ID만 든 `rebut` - 맨 아래 ⏵).
⚠ (v6·v7. v8 이상은 ID만 든 `rebut`이 `rebut_ids_only`로, ID와 어휘 밖 축을 함께 든 `rebut`이 `invalid_axis`로 선다 - 맨 아래 ⏵.) 사유는 **ID가 없을 때만** 선다 — `evidence_refs`가 비어 있지 않으면 `ungrounded_reason`이 즉시 `None`이라 **어휘 밖 축이 있어도** `invalid_axis`가 안 서고 `critic_normalized`도 `invalid_axes`도 안 남는다. 그래서 **ID와 축을 둘 다 요구하는 서빙 v6에서 규약을 지킨 rebut의 어휘 변형은 이 지표에 0으로 보인다.** `invalid_axes`는 어휘 밖
원문의 JSON 배열 문자열이다(최대 5개, 값마다 40자 - `TraceValue`가 스칼라라서 문자열이다).
`gate.was_normalized`의 뜻은 그대로다 - "반박이 반박으로 세어지지 않았다".

### 4. PoV 풀 - 정규화된 `need_evidence`는 다른 `need_evidence`와 같은 길을 간다

`runner._pov_targets`가 풀에서 빼는 것은 판정 호출이 실패한 Finding(`critic_failed_ids`)과
`gate.normalize` 뒤 판정이 `rebut`인 Finding뿐이다(`_critic_set_aside`).
⏵ 2026-10-15 - **앞 절반은 낡았다**: 판정 호출 실패 Finding은 이제 풀에 든다(맨 아래 ⏵ 둘째). ⏵ 2026-10-18 - **뒤 절반(반박은 뺀다)도 낡았다**: 반박도 풀에 든다(맨 아래 ⏵ 셋째 · 이슈 #706). 이슈 #604(보류 5/5가 풀을 비움)는
보류를 "아니다"가 아니라 "모르겠다"로 읽어 **풀에 들이고 정렬을 uphold < 미경유 < 보류 < 반박으로 낮추는**
방식으로 닫혔다(`_verdict_ranks_by_finding` - 러너 docstring의 출처 표기는 PR #608). 두 함수 모두
`gate.normalize` **뒤** 값을 읽으므로 이 경로가 그 수정을 **그대로 물려받는다**:

| | 전 (`uphold`로 정규화) | 후 (`need_evidence`로 정규화) |
| --- | --- | --- |
| 정책 | F-5 `critic_upheld`, `hypothesized` 유지 (E3면 `validated`) | F-7 `critic_need_evidence`, `queued` |
| PoV 추가 풀 | 든다, 정렬 계단 0(uphold와 동점) | 든다, 정렬 계단 2(보류) |
| 8단계 `validate` | `validated`면 든다 | 안 든다(`queued`) |

### 서빙 v6의 라우팅이 뒤집힌다 - 요청 바이트는 그대로다

`or evidence_refs`를 유지해도 v6에는 한 갈래가 움직인다. v6 규약은 `rebut`에 비어 있지 않은 축 **과**
`evidence_refs`를 요구하는데(`prompts/critic/v6/prefix.md:152-158`), 규약을 어기고 **어휘 안의 축만 들고
`evidence_refs=[]`인** v6 `rebut`은 전과 후에 이렇게 다르다:

| 서빙 v6 `rebut` (유효 축 · `evidence_refs=[]`) | 전 (`bool(evidence_refs)`) | 후 (이 ADR) |
| --- | --- | --- |
| 정규화 | `uphold`로 정규화 | 정규화 안 됨 - 근거 있는 `rebut` |
| 정책 | F-5 `critic_upheld` (E3면 `validated`) | F-6 `critic_rebutted`, `queued` |
| PoV 추가 풀 | 든다, 정렬 계단 0 | **뺀다**(`_critic_set_aside`가 반박을 민다) |
| `critic_normalized` | 남는다 | 안 남는다 |

**같은 프롬프트 판·같은 요청 바이트에서 7단계 라우팅이 갈린다**(`docs/evaluation.md`의 단계별 후보 제거율
행이 이 비교 불가를 적는다 - §2.3 규칙 4가 못 덮는 사유다). 이것이 개선인지는 **미측정**이다: 전 라우팅은
반박을 못 한 Critic이 승격시키는 쪽이었고 후 라우팅은 풀에서 그 Finding을 빼는 쪽이다(⏵ 2026-10-18 - 이제 빼지 않는다, 맨 아래 ⏵) - 어느 쪽이 실제
참양성을 더 잃는지 아무도 재지 않았다.
**지금까지 관측된 v6 영향은 0건이다**: 2026-10-11 run은 7단계 5/5가 `need_evidence`
(`docs/experiments/harness/2026-10-11-serving-run-arvo1065-vastai-remote.md` §3·§5)이고 2026-10-12 run은 6/6이
`need_evidence`(`docs/experiments/harness/2026-10-12-serving-run-arvo1065-vastai-remote.md` §3)라 둘 다 `rebut`이
없었다. 그 전 2026-09-24 스모크(`docs/experiments/harness/2026-09-24-roster-smoke-atom.md`)는 7단계 4건이 전부
반박이었고 그중 1건(`find_0007`)이 `evidence_refs`가 비어 `uphold`로 정규화됐다 - 그 보고서는
`rebuttal_axis`를 적지 않아 **그 축이 어휘 안이었는지 모르고**, 서빙 판이 v6도 아니었으므로(로스터·프롬프트가
다르다) v6의 뒤집힘 사례로 세지 않는다.

즉 "Critic이 못 거른" Finding이 uphold와 같은 자리를 더는 차지하지 못하고, PoV 시도 자체는 막히지
않는다(`pov_attempt_top_n > 0`일 때). 풀이 빈 사유를 세는 `_pov_pool_empty_reason`의 "보류" 칸에는 이제
정규화된 반박이 섞인다 - 칸 이름이 「보류(파서 폴백·정규화된 반박 포함)」로 바뀌었다. 구분은 `critic_normalized`가 한다.

## 고르지 않은 것 - 근거째로 남긴다

| 안 | 기각 근거 |
| --- | --- |
| **G2** - `source_refs` 같은 구조화 인용 필드를 `DecisionDraft`에 신설 | 프롬프트와 스키마가 **둘 다** 바뀌고(v7은 미머지지만 v8이 필요해진다), 인용 필드가 생겨도 그것은 **한 층 깊은 자기 신고**다 - 인용한 코드가 SOURCE에 실재하는지 기계가 안 보는 한(P0b) 문자열 하나가 느는 것뿐이다. 이득(가장 정확하다)에 비해 비용(새 프롬프트 + 새 스키마)이 앞선다 |
| **G3 단독** - `normalize`만 `need_evidence`로 바꾸고 근거 정의는 `bool(evidence_refs)` 그대로 | 사슬은 막지만 **v7 `rebut`이 전부 비활성**이다 - 반박을 해도 결과가 `need_evidence`라 Critic이 반박기로 작동하지 않는다. 그리고 #604와 같은 모양이 생긴다: 풀이 보류로 가득 차 정렬이 의미를 잃는다. 안전하지만 v7을 쓸 이유가 없어진다 |
| **G1 + 폴백 `uphold` 유지** | 불변식 "Critic은 반박을 못 해서 Finding을 승격시키지 않는다"를 깬다. 축이 어휘 밖이면(`guard-check`) 같은 사슬이 다시 열린다 |
| **P0b** - 인용한 코드가 SOURCE에 실재하는지 기계 검증 | **기각이 아니라 보류다.** 자기 신고를 관측으로 바꾸는 유일한 길이지만 이 결정의 선행이 아니다. 별 이슈로 남긴다 |

### 소수 의견 - Skeptic의 이견을 근거째로 남긴다

Skeptic 목소리는 **G3 먼저, 축 유효성은 로깅만, G1은 P0b 뒤에**를 주장했다. 근거: 축 어휘 검사는
**외부 앵커가 없는 자기 신고**다 - 모델이 매번 `["guard"]`를 적으면 어휘를 통과하는 일괄 기각기가 된다.
council은 2:1로 G1을 채택했다(Architect·Pragmatist·Critic). **이 이견은 반증되지 않았다.** 그 위험을 이
ADR은 (a) 축 분포를 첫 v7 run의 지표로 올려(아래) 보이게 하고 (b) `or evidence_refs`를 유지해 v6의 **ID
경로**는 그대로 두며 - ⚠ 다만 **ID 없이 유효 축만 든 v6 `rebut`은 뒤집힌다**(위 「서빙 v6의 라우팅이
뒤집힌다」 표 · `is_grounded`에 판 구분이 없다) - (c) P0b를 별 이슈로 남기는 것으로 받는다. Skeptic의 안이 지금 서면 v7 `rebut`이 전부
`need_evidence`가 되어 G3 단독과 같은 결과가 된다(위 표).

## 결과

**따라오는 것.**

- `Decision.is_grounded`의 정의가 바뀐다(위). `invalid_axes`·`ungrounded_reason` 접근자가 따라온다.
- `critic_normalized`에 `reason`·`invalid_axes` 키가 는다(키 추가 - 기존 소비자는 그대로).
- 정규화 목적지가 `uphold`에서 `need_evidence`로 바뀌어 F-5 -> F-7이다. `docs/agent-contracts.md` §2.3의
  체크리스트(6 -> 10)와 규칙, §3의 제안 두 항목이 갱신된다. Fake Critic의 근거 없는 `rebut`은
  `rebuttal_axis=[]`가 된다(축이 어휘 안이면 근거라 정규화 경로를 못 탄다).
- 이 PR은 **v7을 배선하지 않는다**(`config/models.yaml`은 v6). H1 ⓑ·H2 ⓐ·H3·G2·P0b도 이 PR 밖이다.

**v7을 켜기 전 선행.** 이 ADR(게이트가 근거 없는 반박을 승격시키지 않는다) + H3(`cli run`의 Critic 프롬프트
선행 가드 - 반쪽 배선이 1~6단계 예산을 쓴 뒤에 죽는 것을 막는다). v7 flip PR이 이 둘 위에 선다.

**첫 v7 run이 셀 것.** 이 ADR이 약속하는 지표다 - 전부 `critic_normalized`·`policy_decision`·
`state.db`의 `objects` 표(`kind='decision'` 행)로 센다.

| 지표 | 읽는 법 |
| --- | --- |
| rebut 총수 | `decision.verdict = rebut` |
| 근거 있는 rebut 수 | rebut 총수 - `critic_normalized` 수 |
| 정규화 사유 분포 | `critic_normalized.reason` = `empty_axis` / `invalid_axis` (⏵ 2026-10-15 - v8 이상은 `rebut_ids_only`도) |
| **`critic_upheld`로 `validated`가 된 근거 없는 rebut** | **0이어야 한다** - 구조상 0이고 0이 아니면 이 ADR이 깨진 것이다 |
| 축 분포 | 근거 있는 rebut의 `rebuttal_axis` 값별 수 - 한 값(`guard`)이 압도하면 Skeptic의 우려가 현실이다 |
| **무효 축 비율** | `reason = invalid_axis` / rebut 총수. 높으면 어휘 변형(`guard-check`)이 엄격 `from_text`에 걸려 대량 `need_evidence`가 난 것이다 - `invalid_axes`가 그 변형의 모양을 싣는다 |

**실패 양상 둘(Critic 목소리가 예측했다).** (1) 매번 `["guard"]`면 일괄 기각기가 되어 풀이 반대로 빈다 -
축 분포와 rebut 비율로 첫 run에서 보인다. (2) 어휘 변형으로 대량 `need_evidence` - 무효 축 비율로 보인다.
둘 다 **추측해 읽는 쪽으로 풀지 않는다** - 비율을 먼저 본다(`from_text`를 느슨하게 만들면 이 지표가 가려진다).

## 미결 - 미측정

§0대로 **0으로 두고 사유를 적는다.**

- **v7 run 0건.** 위 지표 전부 미측정이다. v7의 rebut 비율·정밀도·재현율은 CASTLE 수치(저자 인용)뿐이다.
- **`docs/evaluation.md`가 요구하는 프롬프트 판 재측정**이 이 결정에도 걸린다. auditor v4 -> v6·v6 -> v7에서
  연속으로 안 돌았고, 이 PR도 돌리지 않았다 - 이 PR은 판을 바꾸지 않고 게이트를 바꾸므로 재측정은 v7 flip PR의 몫이다.
- **서빙 v6에서 이 변경이 결과를 바꾸는지** 미측정이다. 관측된 뒤집힘은 0건이다(위 「서빙 v6의 라우팅이
  뒤집힌다」 - 10-11 5/5 · 10-12 6/6이 `need_evidence`뿐이었다). v6 `rebut`이 ID 없이 축만 든 경우가 얼마나
  있을지는 모른다(v6의 `critic_normalized` 건수는 이 PR 전에 사유 키가 없었다). 그 뒤집힘이 정밀도·재현율에
  주는 방향은 재지 않았다.
- **`evidence_refs`가 근거인가.** 이 술어의 첫 갈래(`bool(evidence_refs)`)도 모델의 자기 신고다 -
  하네스는 등록된 ID인지·본문이 있는지 보지 않는다(`gate.evidences_for`는 모르는 참조를 조용히 버린다).
  [D106](../open-questions/D/D106.md)이 같은 종류의 문제를 다룬다. 이 ADR은 그 기준을 **낮추지 않고** 같은
  종류의 신호를 SOURCE 경로로 넓힌다. 자기 신고를 관측으로 바꾸는 것은 P0b이고 보류다.
- **`from_text`의 공백 허용**은 `strip()` 하나다(결정 2). 앞뒤 공백이 섞인 응답의 비율은 모른다.

## 되돌리기 비용

**작다.** `gate.normalize`의 반환값 한 줄(`NEED_EVIDENCE` -> `UPHOLD`)과 `Decision.is_grounded`의 정의를 되돌리면
된다. `RebuttalAxis`는 추가형이라 안 쓰면 죽은 코드일 뿐 해롭지 않고, `critic_normalized`의 새 키는 소비자가 없다.

⚠ **되돌릴 수 있다는 것과 되돌려도 된다는 것은 다르다.** `normalize`를 `uphold`로 되돌리면 PR #588의 사슬이
다시 열린다. 되돌리려면 v7 배선이 **없어야** 한다 - 2026-10-13 당시 서빙 중이던 v6은 ID를 인용하므로 `uphold` 폴백이 사슬을
거의 안 연다(v6 `rebut`이 규약을 지키는 한). 그러나 그것은 규약 순응에 기댄 것이라 이 ADR의 불변식보다 약하다.
되돌리면 위 v6 뒤집힘 표도 함께 원래대로 돌아간다.

⏵ 2026-10-14 - **v7 flip 완료.** 위 「v7을 켜기 전 선행」 둘은 충족됐다 - 이 ADR(게이트 정규화)과 H3(`cli run`의 Critic 프롬프트 가드, #651)가 main에 있다. S20 Phase 11이 `config/models.yaml`의 `gpt-oss-20b`를 v6에서 v7로 올렸고 `nodes/critic`의 두 집합에 v7을 더했다. **이 ⏵ 위의 본문 전체는 이 ADR을 넣은 PR 시점(2026-10-13 결정 당일)의 서술이다** — 서빙 판·배선·상태를 현재형으로 말하는 자리는 전부 그 시점 기준으로 읽는다(철자 몇 개를 열거하면 다음 자리를 못 막는다 — CLAUDE.md §2.5). 머리의 상태 「제안」·관련 PR #588 「미머지, 디스크만」도 같다. 승인 여부는 이 PR이 기록하지 않는다 - 상태 줄은 그대로 둔다. 그 PR이 미룬 **재측정은 이 flip으로도 돌지 않았다** - v7 run은 0건이고 위 「첫 v7 run이 셀 것」 지표는 전부 미측정이다.

⏵ 2026-10-14 - **v7의 SOURCE만으로 한 `uphold`는 이 ADR의 불변식 밖이다.** 이 ADR은 근거 없는 *rebut*만 정규화한다. v7 `uphold`는
SOURCE만으로 서고 `evidence_refs=[]`일 수 있다(`prompts/critic/v7/prefix.md:214`·`:258`) - v6은 본문 있는 등록 Evidence ID를 요구했다
(`prompts/critic/v6/prefix.md:166-168`·`:206`). 게이트는 `uphold`의 `evidence_refs`를 안 본다: `gate.py:198` `upheld = any(normalize(d) is UPHOLD)`,
`:204` `p3 = p2 and upheld and repro_spec`. `p2`의 graph 조건(`gate.py:196`)은 run 전역 GRAPH Evidence 하나의 `ok`라 Finding의 경로를 확인하지
않는다(`nodes/index.py` 머리 · `runner._artifact_signals`가 반환의 맨 앞에 싣는다) - `ok`인 GRAPH Evidence가 하나라도 있으면 선다(`usable`
`:185`·`:196`). 6단계가 관측 Evidence를 `evidence_refs`에 붙이므로(`gate_node.py:72` `attach_observed` · 이슈 #552) 모델의 인용과 무관하게 이 조건이
선다. 열린 조건은 `path_ok`·`unblocked`(blocking 누락 없음)·`repro_spec`이다. 그래서 `repro_spec`이 있는 Finding의 v7 SOURCE-only `uphold`는 E3,
`validated`(`policy.py:193` F-8 `critic_upheld`)까지 갈 수 있다 - Auditor의 협조에 달리지 않는다. 이 PR은 게이트를 바꾸지 않는다 - 인용한 코드가 SOURCE에 실재하는지 기계가 안 보는 것은
P0b(보류) 그대로다. **추가 지표(첫 v7 run이 셀 것):** `uphold`이고 그 Decision의 `evidence_refs=[]`(Critic 출력 - `objects` 표 `kind='decision'` 행의 값이고 부착과 무관하다)인데
Finding이 `validated`가 된 수 - `objects` 표의 `kind='decision'` 행과 `policy_decision`으로 센다. step 1이 서빙 경로에서 발동하지 않으므로(`docs/agent-contracts.md` §2.3 ⏵ 2026-10-14) 규약을 지킨 v7 `uphold`는 전부 `evidence_refs=[]`다 - 이 지표는 사실상 `critic_upheld`로 `validated`가 된 수 전수와 같아진다. 그래서 첫 run에서 **사람이** `uphold`의 `rationale`이 인용한 코드가 SOURCE에 실재하는지 표본으로 확인해야 한다(기계 확인은 P0b - 보류). ⚠ Finding의 `evidence_refs`는 6단계 부착 뒤 값이라 모델 인용 수가 아니다
(`gate_node.py:32-41`) - 모델이 인용한 수는 `gate_eval.cited_by_model`(6단계 행)에 따로 남는다. 값은 미측정이다(v7 run 0건).
v7이 서빙 중이므로 `normalize`를 `UPHOLD`로 되돌리는 것은 서빙을 v6으로 되돌린 뒤에만 안전하다 - 지금 되돌리면 v7 `rebut` 전부가 `validated` 사슬을 연다. ⏵ 2026-10-15 - 서빙 Critic은 v8이다(`config/models.yaml` · v8은 v7과 body가 바이트 동일이고 판정 절차·출력 규칙이 같다 - 같은 `rebut` 규약을 든다). 그래서 이 문장의 안전 조건은 「서빙을 **v8과 v7**에서 v6으로 되돌린 뒤」다.

⏵ 2026-10-15 (@danielcho02 승인) - **v8 이상: `rebut`의 근거는 `rebuttal_axis`뿐이다. ID만 든 `rebut`은 `need_evidence`로 정규화된다.** 결정 2의 「`or evidence_refs`를 유지한다」는 **v6·v7에만 선다.** 서빙 Critic v8은 `rebut`에 Evidence ID를 금지하는데(prefix의 `rebut` 규약은 v7과 같다), 술어는 판 구분이 없어 규약을 어기고 ID만 든 v8 `rebut`이 `bool(evidence_refs)`로 근거 있는 반박이 되었다 - 게이트가 못 보는 ID 하나로 `gate.normalize`를 비껴가 F-6 `queued`로 가는 길이다(`nodes/critic._record_unknown_refs`가 사후에 수만 센다). 이제 `Decision.ungrounded_reason`이 `produced_by.prompt_version`(하네스가 넣는 값)이 `schemas/decision.AXIS_ONLY_GROUNDING_VERSIONS`(지금 `{"v8"}` - ⏵ 2026-10-18 `{"v8", "v9"}`)에 들면 ID 갈래를 건너뛴다. ID만 든 `rebut`의 사유는 **`rebut_ids_only`**(`critic_normalized.reason`의 셋째 값)이고, 축이 있는데 어휘 밖이면 ID가 있어도 `invalid_axis`다. 정규화는 종전과 같은 한 자리(`gate.normalize`)에서 일어나 F-7 `queued`로 가고 PoV 풀·정렬은 결정 4의 표(후)를 그대로 따른다. **v6·v7 판정은 한 글자도 안 바뀐다**(위 「서빙 v6의 라우팅이 뒤집힌다」 표도 그대로다). 판 구분은 노드의 `_CONTENT_AWARE_VERSIONS`와 같은 **이름 집합**이다 - meta 선언(`prompt_loader` 머리의 「버전 이름으로 분기하지 않는 이유」)이 정석이나 `prompts/`를 건드려야 해서 안 썼다. 판이 늘 때 집합을 잊으면 새 판에서 ID-only `rebut`이 조용히 근거가 되므로 `tests/test_critic_v8.py`가 디스크의 v8 이상 디렉터리가 집합에 드는지 문다. **지표가 바뀐다:** 사유 분포에 `rebut_ids_only`가 는다 - 2026-10-15 앞뒤 run의 「근거 없는 반박 비율」(`critic_normalized` / Critic `model_call`)은 **한 열에 놓지 않는다**(v8 run의 분자에 규약 위반 `rebut`이 든다). **첫 v8 run이 셀 것:** `reason = rebut_ids_only` 건수 = ID만 든(축이 비어 있는) `rebut`의 수뿐이다 - 규약 위반 전부가 아니다(ID와 어휘 밖 축을 함께 든 `rebut`은 `invalid_axis`로, ID와 유효 축을 함께 든 `rebut`은 축이 근거라 정규화 없이 선다). v8이 `rebut`에 ID를 안 싣는다는 규약을 어긴 **전수**는 `objects` 표의 `kind='decision'` 행 중 `verdict=rebut`이고 `evidence_refs`가 비어 있지 않은 수다. 값은 미측정이다(v8 run 0건). 되돌리기는 집합에서 `"v8"`을 빼면 된다.

⏵ 2026-10-15 (@danielcho02 승인) - **결정 4 개정: 판정 호출이 실패한 Finding도 추가 PoV 풀에 든다.** 스키마 파싱 실패·크기 거절로 판정을 못 받은 Finding(`runner._drop_finding` → `critic_failed_ids`)은 「Critic이 아니라고 했다」가 아니라 「Critic이 못 봤다」이므로 보류와 같은 근거로 뺄 이유가 없다. `_critic_set_aside`는 이제 `gate.normalize` 뒤 판정이 `rebut`인 Finding만 밀어 낸다. 판정이 없어 정렬 계단은 **Critic을 안 거친 Finding과 같은 1**이다(uphold 0 < 미경유·판정 실패 1 < 보류 2 < 반박 3). `critic_failed` trace·경고(`Finding N건이 7단계 critic 호출 실패로 판정을 못 받았다`)는 그대로다. `_pov_pool_empty_reason`의 「판정 실패」 칸은 K>0에서 항상 0이고 K=0(`pov_attempt_top_n=0`)에서만 정보 칸이다. 이 개정이 PoV 시도·최종 후보에 주는 영향은 미측정이다(실서빙에서 판정 호출이 실패한 Finding의 PoC가 채점기에 닿은 run 0건).

⏵ 2026-10-18 (@danielcho02 승인 · 이슈 #706 · council 2026-10-18) - **결정 4 재개정: 근거 있는 `rebut`도 추가 PoV 풀에 든다.** 풀은 이제 `validated` 밖 Finding 전부이고 Critic 판정은 **정렬 계단만** 정한다(uphold 0 < 판정 없음 1 < 보류 2 < 반박 3 - `runner._verdict_ranks_by_finding`). `runner._critic_set_aside`는 지웠다. 그래서 결정 4 첫 문단의 「풀에서 빼는 것은 … `rebut`인 Finding뿐이다」와 「서빙 v6의 라우팅이 뒤집힌다」 표의 「PoV 추가 풀 | **뺀다**」 칸은 이 날 전의 서술이다.
- **왜.** `rebut`은 모델의 자기 신고이고 하네스는 인용한 코드가 SOURCE에 있는지 안 본다(P0b 보류). 2026-10-16 arvo:1065 run(`run_20261016_0001` · Critic v8)의 근거 있는 `rebut` 3건 중 2건(dec_0001 arithmetic · dec_0004 guard)은 rationale로 「취약하다」를 쓰고 `rebut`을 냈다(`docs/experiments/harness/2026-10-17-serving-run-arvo1065-vastai-remote.md` §3 · n=1). 빼면 그런 Finding의 PoV 기회가 영영 없고, 들이면 `pov_attempt_top_n` 안의 자리를 쓴다(아래 대가 - `triggerable` 반박은 1순위도 된다).
- **이슈 #706의 전제는 정정됐다.** 이슈는 v8 `rebut`을 Auditor 결론에 대한 **상대값**으로 읽고 policy가 `(trigger_status, rebuttal_axis)`로 뜻을 정하는 안 (a)를 들었다. council(4/4)은 그 전제를 기각했다 - v8 prefix 5절은 축마다 「약점이 필요로 하는 조건」을 정의하고 `rebut`은 그 조건이 성립 못 한다는 **절대값**이다(`guard` 축 `rebut` = 가드가 모든 트리거 값을 막는다). Critic 입력에 `trigger_status`도 없다(`nodes/critic._describe_claim`). 그래서 dec_0004는 「정의대로 쓴 `rebut`」이 아니라 dec_0001과 같은 판정–근거 불일치이고, (a)는 정당한 `guarded`+`guard` 기각을 보류 쪽으로 새게 만들어 기각했다. 상대값으로 읽힐 수 있는 v8 13행 문구는 Critic v9가 고쳤다(`prompts/critic/v9/meta.yaml`).
- **불변식은 그대로다.** 「Critic은 반박을 못 해서 Finding을 승격시키지 않는다」 - 이 개정은 상태를 안 바꾼다. 정책 F-6(`queued` · `critic_rebutted`)·게이트·`validate` 루프는 그대로이고 PoV는 추가형이다(`_run_pov`).
- **대가 - 반박된 Finding의 PoC도 최종 제출이 될 수 있다.** `_designation_key`에서 판정 계단은 셋째다(자기 `vul` 응답 코드 · 제출 여부 다음). 자기 `vul` 응답 코드 계단은 크래시 < 모름 < 크래시 아님이다. 앞 두 계단이 같은 다른 판정의 후보에게는 반박 PoC가 진다. 반박 PoC가 이기는 것은 그 두 계단에서 앞설 때(자기 응답 코드가 크래시이거나, 상대가 「크래시 아님」인데 자기는 「모름」이거나, 자기는 제출이고 상대는 구제된 중간 gen일 때)와 후보가 전부 반박일 때(그때는 등급 이하가 가른다 - 유일 후보 `sole_candidate` 포함)다. 추가 풀 정렬에서는 Auditor 결론 계단(`_trigger_rank`)이 판정 계단 앞이라 `triggerable` 반박은 `guarded` uphold보다 앞선다.
- **지표가 바뀐다.** 그 날 앞뒤 run은 PoV 시도 수·대상 열을 한 열에 놓지 않는다(`docs/evaluation.md` 단계별 후보 제거율 행). `_pov_pool_empty_reason`의 다섯 칸은 K>0에서 전부 0이고 K=0에서만 정보다.
- **v9도 축만 근거로 센다.** `AXIS_ONLY_GROUNDING_VERSIONS`는 `{"v8", "v9"}`이고 위 「되돌리기」 ⏵의 안전 조건은 「서빙을 **v9·v8·v7**에서 v6으로 되돌린 뒤」다.
- **미측정.** 반박된 Finding발 PoC가 채점기에 닿은 run 0건 · v9 run 0건. 되돌리기는 이 개정을 넣은 PR의 `runner.py`·테스트 변경을 되돌린다 - `_critic_set_aside` 함수와 `_pov_targets`의 필터, 빈 풀 문장, 풀 판정표 테스트다.
