"""토큰 수 추정 — 갈아끼울 수 있는 카운터 (D21).

**`src/adapters/index_graph.py`에 있던 것을 여기로 올렸다.** `CapsuleSection`이 "본문이
있으면 `tokens`가 그 본문에서 계산된 값과 같아야 한다"를 검증하려면 스키마가 이 함수를
불러야 하는데, `src/schemas/`가 `src/adapters/`를 import 하면 의존 방향이 뒤집힌다
(engineering-guide.md §3: 스키마는 어댑터를 모른다). 그래서 둘 다 import 할 수 있는
자리로 뺐다 — `src/ids.py`·`src/paths.py`와 같은 층이다.

**기본 카운터는 글자수 근사(`len//4`)이고, 실 토크나이저로 갈아끼울 수 있다(D21 (b)).**
조립과 검증이 **같은 값**을 봐야 한다 — `CapsuleSection._tokens_match_body`가 이 함수를
다시 불러 `tokens == sum(estimate_tokens(줄))`을 강제하기 때문이다. 그래서 "지금 켜진
카운터"는 **모듈 전역 하나(`_counter`)**다: 역할마다 모델(=토크나이저)이 다르므로, 그 역할
캡슐을 조립하기 **직전에** 그 역할 토크나이저로 바꾼다 — 러너가 `using_counter()`로
`retrieval.run()` 한 번을 감싸고(그 안에서 `set_counter()`로 스왑), 끝나면 이전 카운터로
되돌린다(`runner._triage_one`). 카운터를 어디서 만들어 주입하는지는
`src/adapters/tokenizer.py`·`factory`에 있다 — 러너는 `src/adapters`를 모른다.
카운터가 갈리면 같은 index.json에서 두 Retriever가 다른 캡슐을 내 `dev_fake`↔`dev_docker`
비교가 깨진다 — 그래서 **지금 켜진 카운터가 하나여야 한다**는 것이 이 모듈의 존재 이유다.
토크나이저 파일이 없으면(CI·dev) 기본 카운터가 유효하다.

**⚠ `expand()`가 배선되는 날 `using_counter` 밖에서 재검증이 터진다.** `HarnessModel.
transition()`은 `model_copy(update=...)`와 달리 **전체 모델을 재검증**하는데,
`Retriever.expand()`가 `capsule.transition(sections=[...])`을 부르므로 **기존 섹션까지 전부
다시 검증된다.** 실 토크나이저로 만든 섹션을 기본 카운터 아래에서 재검증하면
`CapsuleSection._tokens_match_body`가 안 맞아 `ValueError`가 난다 — 조립과 검증이 같은
카운터를 봐야 한다는 이 모듈의 불변식이 확장 경로에서 깨지는 자리다. **지금은 안 터진다**:
러너의 `retrieval.expand()` 호출 지점이 0개다(D20 관측 ③). 배선하는 사람이 확장도
`using_counter(...)` 안에서 돌려야 한다.

**아직 모으지 않은 것이 셋 있다**: `fake_model._CHARS_PER_TOKEN`·
`vllm_retrieval._CHARS_PER_TOKEN`·`bench.CHARS_PER_TOKEN`. 그 셋은 캡슐 토큰 회계가
아니라 각각 프롬프트 usage·검색 usage·벤치 채움 길이를 재므로 이 불변식과 묶여 있지
않다. 값이 같은 것은 우연이 아니지만, 묶는 것은 별건이다.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

# 기본 카운터: 4글자 ≈ 1토큰. 실 토크나이저 파일이 없으면(CI·dev) 이 값이 유효하다.
CHARS_PER_TOKEN = 4

# **역할 카운터가 실제로 켜지는 역할.** `runner._triage_one`이 `using_counter(...)`로 감싸는
# 자리가 이 둘이다 — scout는 LIGHT 캡슐, auditor는 EXPANDED 캡슐을 조립할 때.
#
# `factory`가 이 목록으로 `Deps.counter_report`를 만들고 러너가 그것을 `token_counter`
# 이벤트로 낸다. **켜지지 않는 역할을 기록에 실으면 거짓 정보다** —
# `config.prompt_versions()`가 프롬프트 없는 역할을 안 담는 것과 같은 이유다.
#
# 문자열인 것은 이 모듈이 `src/schemas/`보다 **아래**에 있기 때문이다(`schemas/capsule.py`가
# 이 모듈을 import 한다). `Role`을 여기서 import 하면 순환이 된다 — 대신
# `tests/test_tokens.py`가 이 집합을 `Role`과 대조해 고정한다.
COUNTED_ROLES = ("scout", "auditor")


def _chars_estimate(text: str) -> int:
    """글자수 근사. 실 토크나이저 전까지의 기본 카운터. 원시 개수만 돌려준다
    (바닥 1은 `estimate_tokens`가 강제한다)."""
    return len(text) // CHARS_PER_TOKEN


# **지금 켜진 카운터.** 조립·검증이 같은 값을 보도록 모듈 전역 하나로 둔다(위 docstring).
# 기본은 글자수 근사이고, 실 토크나이저는 `using_counter()`(→`set_counter`)로 갈아끼운다.
_counter: Callable[[str], int] = _chars_estimate


def estimate_tokens(text: str) -> int:
    """`text`를 프롬프트에 실었을 때의 추정 토큰 수. 빈 문자열도 1이다.

    바닥이 1인 이유: 0을 돌려주면 "본문이 있는데 비용이 0"이 되어 예산 회계에서 공짜
    항목이 생긴다. `index_graph.fit_names()`가 이 값을 누적해 섹션 예산을 재고,
    `CapsuleSection`이 같은 함수로 그 합을 검증한다.

    실제 셈은 **지금 켜진 카운터**(`_counter`)가 한다 — 기본은 글자수 근사, 실 토크나이저를
    set 하면 그쪽. 어느 카운터든 바닥 1은 여기서 강제한다.
    """
    return max(1, _counter(text))


def set_counter(counter: Callable[[str], int]) -> None:
    """지금 켜진 토큰 카운터를 바꾼다.

    **조립~검증이 같은 카운터를 봐야 한다** — 그래서 역할 캡슐을 조립하기 **직전에** 그 역할
    모델의 토크나이저로 바꾸고, 조립이 끝나면 `reset_counter()`로 되돌린다. 바닥 1은
    `estimate_tokens`가 강제하므로 카운터는 원시 개수만 돌려주면 된다(빈 문자열 0도 괜찮다).
    """
    global _counter
    _counter = counter


def reset_counter() -> None:
    """기본 카운터(글자수 근사 `len//4`)로 되돌린다."""
    global _counter
    _counter = _chars_estimate


@contextmanager
def using_counter(counter: Callable[[str], int] | None) -> Iterator[None]:
    """`counter`가 켜진 채로 블록을 돈다.

    **캡슐 조립~검증을 한 호흡으로 감싸는 용도** — 역할 캡슐을 만드는 `retrieval.run()` 한 번을
    이 안에 넣으면 조립과 `CapsuleSection` 검증이 같은 카운터를 본다. 끝나면 **이전 카운터로**
    되돌린다(예외에도, `finally`). `counter`가 `None`이면 아무것도 바꾸지 않는다 — 토크나이저
    파일이 없거나(CI·dev) source가 hf/file이 아니면 기본 카운터(글자수 근사)가 유지된다.
    """
    if counter is None:
        yield
        return
    previous = _counter
    set_counter(counter)  # 스왑·복원은 한 변경 경로(set_counter)로 모은다
    try:
        yield
    finally:
        set_counter(previous)  # 기본이 아니라 **이전** 카운터로 — 역할 중첩에서 바깥이 산다
