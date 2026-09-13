# 풀 세 자리 토크나이저가 우리 캡슐 텍스트에서 얼마나 갈리나 — ATOM ([#194](../../open-questions/D/D71.md))

> **결론 하나.** 공유 캡슐을 첫 멤버(qwen) 토크나이저로 재고 다른 자리가 그것을 그대로 받을 때,
> **캡슐 합에서 최대 갈림은 +4.0%, 조각 하나에서 최대 +10.6%다** (2026-09-13 ATOM 실측, 아래 §2).
> 갈리는 자리는 **`scout_boundary`(mistral/devstral) 하나뿐**이다 — 두 qwen 자리
> (`scout_memory`·`scout_injection`)는 모든 조각에서 **토큰 수가 완전히 일치**한다. 방향은 대체로
> mistral이 **더 세는** 쪽(합 +4%)이라, 첫 멤버 기준 예산이 mistral에는 **과소**다 — [D71](../../open-questions/D/D71.md)이
> 경고한 spurious `BudgetExhausted`의 방향이다. 이 실측이 [#194](../../open-questions/D/D71.md) 옵션 (A)의 마진 크기를 정한다:
> 예산을 `0.90`배로 담으면 헤드룸이 `1/0.90 ≈ 1.111`(+11.1%)라 조각 최대 갈림(+10.6%)까지 세 자리 전부 상한 안에 든다.

## 메타

| 항목 | 값 |
| --- | --- |
| 측정 종료 시각 | `2026-09-13 KST` (프로브 종료). 하네스 run이 아니다 — `run_id` 없다 |
| 측정한 것 | **토크나이저 토큰 수뿐.** GPU·서빙 없음 — `AutoTokenizer.from_pretrained`로 세 가중치의 토크나이저만 로드해 `encode()` 길이를 셌다 |
| 토크나이저 원천 (ATOM `/srv/tenants/user/.cache/huggingface/hub/`) | `scout_memory` = `NVFP4/Qwen3-Coder-30B-A3B-Instruct-FP4@3b554c28e968128990ef4ac2d25ca757bf4ce048` · `scout_boundary` = `mistralai/Devstral-Small-2-24B-Instruct-2512@55c5b41e98c2dbd21b0c8afffc540dcfc9eb5128` · `scout_injection` = `Qwen/Qwen3-8B@b968826d9c46dd6066d109eabc6255188de91218` |
| 입력 | 캡슐 본문에 실리는 모양의 코드 조각 6개 (C·C 매크로·파이썬·C++ 템플릿·식별자 밀집·한영 혼합 주석). 프로브 소스에 인라인 — `d71_tokenizer_divergence.py`의 `CHUNKS` |
| 프로브 소스 | ATOM `/srv/tenants/user/d71_tokenizer_divergence.py` (저장소 미커밋 — 이 보고서가 §2에 값과 조각을 전재한다) |
| 원문 출력 보존 | ATOM `/srv/tenants/user/d71_tokenizer_divergence_20260913.txt` — 보존. **저장소에는 없다** |
| transformers · 환경 | `5.15.0` · venv `/srv/tenants/user/venv-probe` |
| `--device` | `atom` (GPU 미사용 — CPU에서 토크나이저만) |

## 1. 왜 쟀나

[D71](../../open-questions/D/D71.md)이 세 가지를 확인했다: ① 캐시 적중 경로가 캡슐을 재검증하지 않고 ②
로스터가 세 자리를 서로 다른 세 모델로 정했으며 ③ `cli.py`가 한 토크나이저의 수를 자리 수만큼
곱한다. **미측정으로 남았던 것은 「갈림의 크기」다** — 세 토크나이저가 우리 캡슐 텍스트에서 실제로
얼마나 벌어지는지. 그 크기를 모르면 [#194](../../open-questions/D/D71.md) 옵션 (A)(예산 마진)의 factor를 실측 없이
정하게 되어 [CLAUDE.md](../../../CLAUDE.md) §0에 걸린다. 그래서 마진을 코드에 넣기 **전에** 이 값을 잰다.

## 2. 실측 — 조각별·합 토큰 수와 max/min 비

세 자리 토크나이저가 같은 조각을 센 토큰 수. `max/min`은 그 조각에서 가장 센 자리와 가장 덜 센
자리의 비다.

| 조각 | `scout_memory` (qwen-coder) | `scout_boundary` (mistral) | `scout_injection` (qwen3-8b) | max/min |
| --- | --- | --- | --- | --- |
| C-parse | 76 | 81 | 76 | 1.066 |
| C-macros | 108 | 112 | 108 | 1.037 |
| py-dense | 69 | 72 | 69 | 1.043 |
| cpp-templates | 82 | 86 | 82 | 1.049 |
| identifiers | 66 | **73** | 66 | **1.106** |
| mixed-comments | 105 | 102 | 105 | 1.029 |
| **합(캡슐 근사)** | **506** | **526** | **506** | **1.040** |

**조각별 max/min: 중앙 1.046 · 최대 1.106.**

읽히는 것 셋.

1. **두 qwen 자리는 완전히 같다.** `scout_memory`와 `scout_injection`은 여섯 조각 전부에서 토큰
   수가 일치한다(합도 506 = 506). 갈림은 오직 mistral 자리에서 난다 — 계열 규칙([D71](../../open-questions/D/D71.md) 옵션 d)이
   풀 안에서 왜 문제되는지가 이 한 줄에 있다.
2. **방향은 대체로 mistral이 더 센다** — 합 +4.0%. 첫 멤버(qwen) 기준 예산은 mistral에 **과소**라,
   qwen 기준으로 상한 이내인 캡슐이 mistral 기준으로는 상한을 넘을 수 있다. 그것이 [D71](../../open-questions/D/D71.md)이
   지목한 spurious `BudgetExhausted`의 방향이다.
3. **한 조각(`mixed-comments`)만 방향이 뒤집힌다** — 한영 혼합 주석에서 mistral이 오히려 **덜**
   센다(102 < 105). 갈림은 조각 단위로는 양방향이지만 **캡슐 합에서는 한 방향(+4%)으로 쏠린다.**

## 3. 마진 크기의 근거

예산을 `factor`배로 담으면 첫 멤버 기준 상한이 실질적으로 `budget × factor`가 되고, 다른 자리가
같은 텍스트를 `budget × factor × (그 자리 비)`로 센다. 그 값이 원래 `budget` 이내이려면
`factor × (비) ≤ 1`, 즉 `factor ≤ 1/(비)`가 필요하다.

- 가장 보수적으로 **조각 최대 비 1.106**을 쓰면 `factor ≤ 1/1.106 = 0.904`.
- `factor = 0.90`은 헤드룸 `1/0.90 = 1.111`(+11.1%)이라 조각 최대 갈림(+10.6%)을 **덮는다.**
- 합 기준(1.040)만 보면 `0.96`으로 충분하지만, 예산은 조각을 **하나씩 담다 끊으므로** 마지막
   조각이 다른 자리에서 상한을 넘겨 잘릴 수 있어 **조각 최대**를 기준으로 잡는다.

그래서 [#194](../../open-questions/D/D71.md) 옵션 (A)는 `_POOL_CAPSULE_MARGIN = 0.90`으로 간다 —
`src/orchestrator/nodes/retrieval.py`의 `run()`이 풀 세 자리가 전부 선언된 프로필에서만 예산에
곱한다. 단일 프로필은 자리가 하나라 안 곱한다(실측 없이 상한을 줄이면 §0에 걸린다).

## 4. 이 실측이 답하지 않는 것

- **캡슐 텍스트의 대표성.** 조각 여섯은 캡슐 본문 **모양**을 흉내낸 것이지 실제 run의 캡슐이
  아니다. 진짜 갈림은 실서빙 풀 run의 캡슐로 재야 확정이고, 그때 이 마진이 충분했는지 되본다.
  그 재검은 풀 프로필이 실서빙으로 도는 시점([build-plan.md](../../build-plan.md) §8의 swap 실측)에 붙는다.
- **판정 정확도.** 토큰 수만 쟀다 — 마진이 캡슐 내용을 줄여 판정이 나빠지는지는 여기서 안 본다.
- **예산 강제의 구조적 회복.** 마진은 갈림을 **덮는** 것이지 자리마다 검증하는 것이 아니다.
  [D71](../../open-questions/D/D71.md) 옵션 (a)·(b)가 그 구조적 해법이고 이 실측은 그것을 대신하지 않는다 — (A)는 그 설계
  판단이 서기 전까지의 **실측 기반 강제**다.
