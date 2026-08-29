# 2×2 실험 기록

팀 2×2 매트릭스가 **실제로 무엇으로 돌았는지**를 남기는 자리다. 옆의
[`harness/`](../harness/)와 목적이 다르다 — 그쪽은 "하네스가 도는가"를 본 run이고,
여기는 "그 하네스로 돌린 실험"이다.

## 여기 들어오는 것

| 무엇 | 왜 |
| --- | --- |
| 셀별 실행 설정(`run_matrix.yaml` 등) | **하네스 기록만으로는 복원이 안 되는 값이 있다.** `RunRecord.sampling`은 `temperature`·`top_p`만 담아서, 서버나 실행 스크립트에서 고정한 `top_k` 같은 값은 어디에도 안 남는다 |
| 서버 기동 명령 원문 | 모델 파일 선언값과 실효값이 갈리는 지점이 여기다 |
| 셀별 보고서 | [TEMPLATE.md](../TEMPLATE.md)의 메타 12항목을 따른다 |

## 여기 **안** 들어오는 것

**정답지를 넣지 않는다.** `evaluator/**`의 `ground_truth.yaml` 계열은 이 저장소에 들어오면
안 된다 — 하네스가 읽는 대상은 `blind/**`뿐이고, 정답지가 저장소에 있으면
[evaluation.md](../../evaluation.md) §4의 누출 검사 전제가 무너진다.

**자동으로 안 걸린다는 것을 알아 둔다.** `eval/leakage`의 digest 교집합 검사는 매니페스트가
없어 지금 **생략된다**([open-questions.md](../../open-questions.md) R3). 정규식 검사만
도므로 **파일을 넣는 사람이 직접 봐야 한다.**

대상 소스(`blind/**`)와 run 산출물도 넣지 않는다 — 크고, 저장소가 들고 있을 이유가 없다.

## 지금 여기 있는 것

| 파일 | 무엇 |
| --- | --- |
| [`config/run_matrix.yaml`](config/run_matrix.yaml) | 매트릭스 스펙 원문. 조건 다섯(B1·B1'·B3·B3'·B3'-large)의 모델·역할 구성, seed 정책, 샘플링 고정값, 판정 규칙 |
| [`config/models_endpoints.yaml`](config/models_endpoints.yaml) | 조건이 쓰는 엔드포인트와 HF repo. **하네스 `config/models.yaml`과 별개다** |

2026-08-30에 ATOM `~/experiments/2x2/config/`에서 그대로 받았다. **편집하지 않았다** —
원본이 ATOM에서 계속 바뀌므로 이쪽은 그 시점의 사본이다.

## 이 파일들이 말해 주는 것 — 하네스 기록만 봐선 모르는 것

**샘플링 고정값 중 다섯이 `RunRecord`에 안 남는다.** `run_matrix.yaml` §3이 아홉을 고정하고
"모든 조건·모든 역할 공통. 절대 조건별로 바꾸지 말 것"이라 못박았는데, 하네스가 요청에
싣는 것은 `temperature`·`top_p`·`max_tokens` 셋뿐이다.

| 스펙이 고정한 값 | 하네스가 보내나 | 기록에 남나 |
| --- | :-: | :-: |
| `temperature: 0.2` · `top_p: 0.95` | O | `RunRecord.sampling` |
| `max_tokens: 8192` | O | `RunRecord.thresholds.output_max` |
| `top_k: -1` · `min_p: 0.0` · `repetition_penalty: 1.0` · `frequency_penalty: 0.0` · `max_input_tokens: 32768` | **X** | **어디에도 안 남는다** |

**다섯 다 끄는 값이라 실효는 없다** — `-1`·`0.0`·`1.0`은 그 기능을 안 쓴다는 뜻이고,
`temperature 0.2` · `top_p 0.95`면 분포가 이미 좁아 `top_k` 상한이 얹혀도 자를 것이 없다.
그래도 **무엇으로 고정했는지가 이 파일에만 있다**는 사실은 남는다. 그래서 사본을 들였다.

**`max_tokens: 8192`가 스펙 고정값이다.** 하네스 쪽 `config/budgets.yaml`의
`thresholds.output_max`를 바꾸면 하네스로 도는 셀만 스펙을 어긴다 — 매트릭스가 도는 동안은
그 값을 건드리지 않는다.

**v2 프롬프트의 원본이 ATOM의 `~/experiments/2x2/prompts/multi/`다.** 저장소
[`prompts/{scout,auditor,critic}/v2/`](../../../prompts/)는 그 사본이고, **둘이 갈릴 수
있다.** 어느 쪽이 정본인지는 아직 안 정했다.

## 아직 없는 것

- **셀 ↔ `run_id` 매핑.** ATOM에 매핑 파일이 없다(`grep -rl "run_2026"`가 run 산출물
  안쪽만 찾는다). `results/<조건>/<모델>/` 디렉터리 구조와 각 run의 `condition`으로
  역추적해야 한다
- **서버 기동 명령 원문.** `models_endpoints.yaml`은 포트와 repo만 담는다. `--generation-config`
  같은 플래그가 걸렸는지는 여기서 알 수 없고, 그것이 모델 선언값과 실효값을 가른다
- **셀별 보고서.** 매트릭스가 끝난 뒤 [TEMPLATE.md](../TEMPLATE.md)를 따라 쓴다
