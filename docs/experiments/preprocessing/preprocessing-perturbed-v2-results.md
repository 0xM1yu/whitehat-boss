## 전처리 실험 결과 - perturbed_v2 버전 (source, ast+pdg, ast+cfg few shot)

perturbed 실험에서 정확도가 확 낮아지는 문제의 원인을 찾기 위해 clean 버전에서 변수명과 함수명만 변경한 버전이다. <br>
원인이 데드 코드라고 생각해서 데드 코드를 제외하고 진행하였다.
- 변수명: v1, v2, ... 형식으로 변경
- 함수명: func_a, func_b, ... 형식으로 변경

### 0. 목차
| 번호 | 내용 |
| --- | --- |
| 1 | 최종 결과 |
| 2 | Qwen3-8B 실험 결과 (source, ast+pdg, ast+cfg) |
| 3 | Foundation-Sec-1.1-8B-Instruct 실험 결과 (TODO) |
| 4 | Qwen3-4B 실험 결과 (TODO) |

---

perturbed_v2 버전은 편리한 비교를 위해 우선 preprocessing-few-shot-results.md에 함께 저장해두었다. <br>
이후 위의 모든 모델에 대한 실험이 완료되면 추가로 작성할 예정이다.