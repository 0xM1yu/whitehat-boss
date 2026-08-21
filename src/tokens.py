"""토큰 수 추정 (실제 토크나이저가 아니라 4바이트≈1토큰 근사).

**`src/adapters/index_graph.py`에 있던 것을 여기로 올렸다.** `CapsuleSection`이 "본문이
있으면 `tokens`가 그 본문에서 계산된 값과 같아야 한다"를 검증하려면 스키마가 이 함수를
불러야 하는데, `src/schemas/`가 `src/adapters/`를 import 하면 의존 방향이 뒤집힌다
(engineering-guide.md §3: 스키마는 어댑터를 모른다). 그래서 둘 다 import 할 수 있는
자리로 뺐다 — `src/ids.py`·`src/paths.py`와 같은 층이다.

값이 갈리면 같은 index.json에서 두 Retriever가 다른 캡슐 크기를 내고 `dev_fake`↔
`dev_docker` 비교가 성립하지 않는다. 그래서 정의가 **하나여야 한다**는 것이 이 모듈의
존재 이유이고, `index_graph`가 "두 retriever가 각자 들고 있던 것을 여기로 모았다"고 적은
것과 같은 논법이다.

**아직 모으지 않은 것이 셋 있다**: `fake_model._CHARS_PER_TOKEN`·
`vllm_retrieval._CHARS_PER_TOKEN`·`bench.CHARS_PER_TOKEN`. 그 셋은 캡슐 토큰 회계가
아니라 각각 프롬프트 usage·검색 usage·벤치 채움 길이를 재므로 이 불변식과 묶여 있지
않다. 값이 같은 것은 우연이 아니지만, 묶는 것은 별건이다.
"""

# 4바이트 ≈ 1토큰.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """`text`를 프롬프트에 실었을 때의 추정 토큰 수. 빈 문자열도 1이다.

    바닥이 1인 이유: 0을 돌려주면 "본문이 있는데 비용이 0"이 되어 예산 회계에서 공짜
    항목이 생긴다. `index_graph.fit_names()`가 이 값을 누적해 섹션 예산을 재고,
    `CapsuleSection`이 같은 함수로 그 합을 검증한다.
    """
    return max(1, len(text) // CHARS_PER_TOKEN)
