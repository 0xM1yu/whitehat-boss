# Case B per-method round-robin budgeting — 전체 51샘플 level-2 후보 포착 실측 — vast

> **왜 돌렸나.** Case B 앵커가 flat `.take(cap)`로 잘려 큰 repo에서 GT 함수가 순회 순서에
> 굶기는 문제(inj_18)를 per-method round-robin budgeting으로 고친 뒤, 전체 평가셋에서 recall과
> 후보 폭증을 확인. #310(Case B 캡 2000→4000)의 폭증 대안이다 — #310 supersede.

| | |
| --- | --- |
| `run_id` | 없음 (즉석 측정 스크립트, 산출물은 vast 박스 `/root/fs/measure/out_permethod3/`) |
| 장비 | vast.ai 인스턴스 51324057 — RTX PRO 6000, 288코어, 1TB RAM |
| commit | `feat/case-b-per-method-budget` (`CASE_B_PER_METHOD = 16`, budget round-robin) |
| baseline | `origin/main` (flat cap 2000, `out_baseline/`) |
| 방식 | **level-2 후보 포착** — build_cpg + collect_candidates 후 GT source/sink 요소별 live/captured 채점(per-candidate 슬라이싱 없음). per-candidate 비용(level-3)은 **미측정** |
| 데이터셋 | 하은 평가셋 main 39 + injection 12 = 51샘플 (GT 요소 77개, dead 제외 후 live) |

## 결과 (PR vs baseline, 같은 51샘플)

| 구분 | PR (k=16) | baseline (flat 2000) |
| --- | --- | --- |
| main | 46/59 → 48/59 회복 후 48/59 (81.4%) | 48/59 (81.4%) |
| inj | 15/18 (83.3%) | 12/18 (66.7%) |
| 전체 | **63/77 (81.8%)** | 60/77 (77.9%) |

- flip: 회수 3 (inj_18 source·sink, inj_13 sink), **회귀 0**.
- inj_18: miss → captured, 후보 11,745 → 12,025 (+280).
- 전체 후보 230,926 → **225,283 (−5,643)** — 폭증 없이 감소.
- main_09: k=4 round-robin에선 회귀했으나 k=16에서 회복 (후보 1,643 → 1,226).

## 주의 (실측 한계)
- **level-2만.** per-candidate 슬라이싱(level-3)의 dataflow 비용·GT-in-slice recall은 안 쟀다.
- **`run_id` 없음.** 산출물은 vast 박스에만 있고 저장소엔 없다 — 재현은 `out_permethod3/`
  스크립트(`run_permethod2.py`류 + `compare_subset.py`)로 하되 박스 재기동 필요.
- `#310`의 inj_18 ~23,000 후보는 **별도 P1+P2+P3+#310 stack run**(`out/`)에서 나온 값이고
  이 vs-main 측정에서 돌린 게 아니다.
