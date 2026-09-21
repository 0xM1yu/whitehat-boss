# Critic `DecisionDraft` — 출력 예산과 JSON 계약 — ATOM

> **결론 둘.** ① **`DecisionDraft`(필드 넷)에서 `thresholds.output_max` 4,096이 안 터진다** —
> 18시행 전부 `finish_reason=length`가 **0건**이고 최장이 2,863토큰(여유 1,233).
> ② **JSON 계약이 18/18 선다** — `xgrammar` 구조화 출력이 이 스키마·이 serve 설정에서 깨지지
> 않는다. [2026-09-09 온보딩](2026-09-09-gpt-oss-20b-onboarding-atom.md)이 *"이 스키마·이
> serve 설정에서 structured output 실패"*(5/5 오답)라고 적은 그 모델이다 — **스키마와 serve
> 설정이 둘 다 다르므로 그 판정을 뒤집지 않는다.**
>
> ⚠ **이것은 판정 정확도 실측이 **아니다**.** verdict 정답은 12/18이지만 그 수는 비교에
> 쓸 수 없다 — 아래 §4가 그 이유를 적는다. **케이스 3개를 손으로 만들었고, 케이스당 n=6,
> 모델 하나, seed 미고정, `run_id` 없음(비교표 밖)이다.**
>
> ⚠ **PR #182 본문이 드는 `enum` 강제 2/5 대 `enum` 제거 5/5는 이 실험이 아니다.** 그 run의
> 원문은 저장소에 없다 — §5 참조.

## 메타

| 항목 | 값 |
| --- | --- |
| 실험 종료 시각 | **미기재** — 프로브 종료 시각을 따로 안 찍었다 |
| 실제 서빙 모델 | `gpt-oss-20b` MXFP4 · 포트 `8006` |
| `repo_commit` | **미기재** — 프로브가 하네스를 안 거치므로 기록하는 코드가 지나지 않는다 |
| `run_id` | **없다.** `cli run`을 안 돌렸다. **비교표 밖이다**(evaluation.md §2.3) |
| run 산출물 경로·보존 여부 | ATOM `~/probe_results/critic_budget_20260912.jsonl` — **ATOM 홈에만 있고 저장소에 없다** |
| 프로브 스크립트 | ATOM `/srv/tenants/user/critic_budget.py` — **저장소에 없다** |
| 입력 프롬프트 | **손으로 만든 케이스 3종.** 저장소의 `prompts/critic/*`을 렌더한 것이 아니다 |
| 구조화 출력 스키마 | `DecisionDraft` — `verdict`·`rebuttal_axis`·`rationale`·`evidence_refs` 넷 |
| `tool_runner` | 해당 없음 (도구를 안 돌렸다) |
| `--device` | `atom` |
| `tool_images` digest | 해당 없음 |
| `--gpu-memory-utilization` | `0.30` |
| 동시에 떠 있던 다른 서버 | **미기재** |
| 원문 산출물 보존 위치 | 위 JSONL 하나. **이 보고서를 쓴 세션은 ATOM에 접속하지 않았다** — §5 |
| 서빙 서버 stdout | **미기재** |
| vLLM · 환경 | `0.27.1` · `--max-model-len 32768` · `--enforce-eager` · 백엔드 `TRITON_ATTN` · `VLLM_USE_DEEP_GEMM=0` · `VLLM_USE_FLASHINFER_SAMPLER=0` · 구조화 출력 `xgrammar` |
| 샘플링 | `temperature 0.2` · `max_tokens 4096` · `reasoning_effort high` · **`seed` 미지정** |

**⚠ 이 보고서의 수치는 조작자가 ATOM에서 돌려 전달한 것이고, 작성 세션은 위 JSONL을 열지
않았다.** 원문 경로를 명시하는 것이 여기서 할 수 있는 전부다(CLAUDE.md §0).

## 1. 요약

| 항목 | 결과 |
| --- | --- |
| 시행 수 | **18** (케이스 3종 × 6) |
| `finish_reason=length` | **0 / 18** — 상한에 닿은 응답이 없다 |
| JSON 유효 | **18 / 18** |
| `completion_tokens` | min **549** · median **1,051** · max **2,863** |
| 상한 대비 여유 | 4,096 − 2,863 = **1,233** |
| verdict 정답 | 12 / 18 — ⚠ **정확도 실측이 아니다**(§4) |

## 2. 실험 — 케이스 3종 × 6시행

| 케이스 | 기대 | 관측 verdict | 정답 |
| --- | --- | --- | --- |
| `need_evidence` | `need_evidence` | `need_evidence` ×1 · `rebut` ×5 | **1 / 6** |
| `true_positive_reachable_uaf` | `uphold` | `uphold` ×5 · `rebut` ×1 | **5 / 6** |
| `false_positive_guarded_path` | `rebut` | `rebut` ×6 | **6 / 6** |

**오답 6건이 전부 `rebut` 방향이다** — 과도 반박. 세 케이스가 서로 다른 기대값을 갖는데
틀리는 방향이 한쪽으로 몰렸다. [관측]

## 3. 이 실험이 말하는 것

**① `DecisionDraft` 4필드에서 출력 상한이 안 터진다.** 18시행 중 `finish_reason=length`가
0건이고 최장이 2,863토큰이다. [D46](../../open-questions/D/D46.md)의 공백 폭주가 이
스키마·이 설정에서는 안 났다.

⚠ **이것이 `src/bench_report.py`의 현재형 서술을 지지한다** — 그쪽은 *"8/26 실측이 후보당
output 167토큰이라 `thresholds.output_max`는 정상 경로에서 닿지 않는다"*고 적는데, 그 근거는
**Scout·Auditor 자리의 값**이었다. 이 실험이 **Critic 자리에서** 같은 방향의 값을 하나 더
준다. ⚠ 다만 **다른 모델·다른 스키마**이므로 그 문장의 근거를 교체하지 않고 더한다.

**② JSON 계약이 18/18 선다.** 구조화 출력이 이 스키마에서 깨지지 않는다.

⚠ **[2026-09-09 온보딩 보고서](2026-09-09-gpt-oss-20b-onboarding-atom.md)의 판정을 뒤집지
않는다.** 그쪽이 *"`xgrammar`는 5/5 오답"*이라 적은 것은 **다른 스키마**(Auditor 계열)와
**다른 serve 설정**에서다. 축이 둘 다 움직였으므로 어느 쪽이 갈랐는지 이 실험은 모른다.

## 4. 이 실험이 말하지 **않는** 것

**판정 정확도가 아니다.** 12/18이라는 수는 다음 넷 때문에 비교에 쓸 수 없다.

1. **케이스를 손으로 만들었다** — 정답지에서 뽑은 것이 아니다. 세 케이스가 모집단을
   대표한다는 근거가 없다.
2. **케이스당 n=6이다.** 1/6과 5/6의 차이를 표본 오차와 가를 수 없다.
3. **모델 하나다** — `gpt-oss-20b` 한 자리. 역할 배정이 확정된 것도 아니다([PROJECT-CONTEXT.md](../../../PROJECT-CONTEXT.md)).
4. **`seed`가 안 박혔다.** [evaluation.md](../../evaluation.md) §2.3 규칙 3(seed 3회)을 안
   따르므로 재현 실행이 같은 값을 낼 보장이 없다.

**`need_evidence` 케이스 1/6도 마찬가지다.** "모델이 보류를 못 낸다"로 읽으면 위 넷을
전부 건너뛴 것이다. 말할 수 있는 것은 **이 케이스·이 설정에서 6시행 중 5건이 `rebut`으로
나왔다**까지다.

**`Verdict.from_text` 매퍼를 재지 않았다.** 프로브는 모델 응답의 `verdict` 문자열을 직접
읽었고 하네스의 매핑 경로를 안 지난다.

## 5. 원천이 저장소에 없는 인용 — PR #182의 `2/5 vs 5/5`

`src/schemas/enums.py`·`src/schemas/decision.py`·`src/orchestrator/nodes/critic.py`·
`docs/agent-contracts.md`·`tests/test_config.py` 다섯 곳이 *"gpt-oss-20b 실측(2026-09-12):
`enum` 강제 verdict 2/5, `enum` 제거 5/5"*를 근거로 든다. **그 run의 원문은 저장소에 없고
이 보고서의 실험도 아니다** — 여기서는 `enum` 강제 팔을 돌리지 않았다.

**다섯 곳에 `원천: PR #182 본문(원문 미커밋)`을 달았다.** 없는 보고서를 가리키게 만들지
않는 것이 CLAUDE.md §0·§2.1이 요구하는 것이고, 이 문단이 그 사실의 자리다.

**해소 조건**: 그 run의 JSONL·스크립트·serve 설정이 커밋되고 보고서가 서면 다섯 인용이
그것을 가리키게 바꾼다.
