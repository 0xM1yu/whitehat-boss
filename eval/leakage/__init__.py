"""데이터 누출 검증 (evaluation.md §4).

**이 검사가 없으면 모든 비교 결과가 무효다.** evaluation.md §4는 그 이유를 이렇게 적는다:
"코드 리뷰만으로는 보장할 수 없다. 위 항목은 CI에서 실행되는 자동 검증 스크립트로
구현하고, 한 건이라도 실패하면 해당 run을 무효 처리한다."

평가셋이 KB에 섞여 들어갔거나 프롬프트에 CVE 번호가 남아 있으면, 우리가 재는 것은
모델의 취약점 발견 능력이 아니라 정답을 얼마나 잘 기억하는지다. 그 상태에서 나온
제거율·정확도는 숫자만 그럴듯하고 아무것도 뜻하지 않는다.

v1은 evaluation.md §4 표의 7개 항목 중 **둘**을 실제로 구현한다.

| 항목 | 구현 | 모듈 |
| --- | --- | --- |
| Train/Test 분리 | KB 인덱스 digest ∩ 평가셋 digest | `hash_intersection` |
| CVE ID Masking | CVE·flag·문제명·patch diff 정규식 스캔 | `id_masking` |

나머지 다섯(Patch Leakage 출처 검사, 중복 데이터 제거, Time Split, Prompt Logging,
Reproducibility)은 아직 없다. Reproducibility만 `tests/test_pipeline_e2e.py`가 따로 본다.
open-questions.md R3은 이 구현으로 **해결 표시하지 않는다** — 남은 다섯이 있고 담당과
기한은 팀이 정한다.
"""

from eval.leakage.hash_intersection import Overlap, digests, intersecting
from eval.leakage.id_masking import Leak, load_problem_names, scan_paths, scan_text

__all__ = [
    "Leak",
    "Overlap",
    "digests",
    "intersecting",
    "load_problem_names",
    "scan_paths",
    "scan_text",
]
