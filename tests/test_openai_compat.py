"""`OpenAICompatModelClient`의 재시도 회계.

**이 클래스에는 그전까지 테스트가 한 줄도 없었다.** 그래서 "GPU 시간은 재시도분까지
더하는데 토큰은 마지막 시도만 센다"는 어긋남이 주석 바로 옆에서 살아남았다 — 그 주석이
편향의 논증을 이미 다 적어 두고 있었는데도 그랬다.

HTTP를 띄우지 않고 `_post`만 갈아 끼운다. 여기서 재는 것은 서버와의 통신이 아니라
**여러 시도의 비용을 어떻게 합치는가**이고, 그것은 `complete()` 안에 있다.
"""

import pytest
from pydantic import BaseModel

from src.adapters.openai_compat import OpenAICompatModelClient, SchemaParseError
from src.interfaces.model_client import Prompt, Usage
from src.schemas.enums import Role

PROMPT = Prompt(prefix="공통 지시", body="후보 본문", prompt_version="v1")

# 시도 n회차가 쓰는 (input, output, cached). 회차마다 값이 달라야 "마지막 것만 반환"과
# "전부 합산"이 구별된다.
#
# **`cached/input` 비율도 회차마다 달라야 한다.** 처음에는 셋 다 비율 0.4로 뒀는데,
# 그러면 합계 비율도 0.4라 **결함 있는 코드에서도 캐시 비율 테스트가 통과했다** — 판별력
# 0인 단언이었다. 지금은 0.40 / 0.10 / 1.00이라 합계는 0.6이고 마지막 회차만 세면 1.0이다.
_PER_ATTEMPT = ((100, 10, 40), (200, 20, 20), (300, 30, 300))


class Tiny(BaseModel):
    """최소 구조화 출력. `ok` 없는 응답은 `ValidationError`가 된다."""

    ok: bool


def _client(retries: int = 2) -> OpenAICompatModelClient:
    return OpenAICompatModelClient(
        endpoint="http://127.0.0.1:9/v1",
        model_name="test-model",
        temperature=0.0,
        top_p=1.0,
        seed=7,
        retries=retries,
    )


def _stub(monkeypatch: pytest.MonkeyPatch, succeed_on: int) -> list[int]:
    """`succeed_on` 회차에 처음 성공하는 서버. 호출 회차 목록을 돌려준다."""
    attempts: list[int] = []

    def fake_post(self, payload: dict) -> tuple[str, Usage]:  # noqa: ANN001, ARG001
        attempts.append(len(attempts) + 1)
        n = attempts[-1]
        inp, out, cached = _PER_ATTEMPT[n - 1]
        raw = '{"ok": true}' if n == succeed_on else '{"nope": 1}'
        return raw, Usage(input_tokens=inp, output_tokens=out, cached_tokens=cached)

    monkeypatch.setattr(OpenAICompatModelClient, "_post", fake_post)
    return attempts


def test_retry_sums_tokens_instead_of_keeping_only_the_last_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2회 실패 후 성공하면 토큰은 **3회 합**이다.

    실패한 응답도 서버가 이미 만들어 냈고 그 토큰은 실제로 쓰였다. 마지막 시도만 세면
    재시도가 잦은 모델일수록 토큰이 적게 잡히는데, `gpu_ms`는 3회분이라 "토큰당 GPU"가
    실제보다 커진다 — 조건 간 비교에서 정확히 거꾸로 가는 편향이다.
    """
    attempts = _stub(monkeypatch, succeed_on=3)

    result = _client().complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    assert attempts == [1, 2, 3]
    assert result.usage.input_tokens == 100 + 200 + 300
    assert result.usage.output_tokens == 10 + 20 + 30
    assert result.usage.cached_tokens == 40 + 20 + 300


def test_cache_ratio_numerator_and_denominator_grow_on_the_same_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cached`만 빼놓고 누적하면 prefix 캐시 비율이 조용히 틀어진다.

    픽스처의 회차별 `cached/input`은 0.40 / 0.10 / 1.00이고 **참값은 합계 비율 0.6**이다.
    마지막 회차만 세면 1.0이 나온다 — 캐시가 완벽하게 맞은 것처럼 보인다.
    """
    _stub(monkeypatch, succeed_on=3)

    usage = _client().complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny).usage

    assert usage.cached_tokens / usage.input_tokens == pytest.approx(0.6)
    # 부분집합 불변식은 합계에서도 유지된다 — 각 항이 만족하므로 합도 만족한다.
    assert usage.cached_tokens <= usage.input_tokens


def test_first_try_success_is_not_double_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """재시도가 없으면 1회분 그대로다. 누적이 성공 경로를 부풀리면 안 된다."""
    attempts = _stub(monkeypatch, succeed_on=1)

    usage = _client().complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny).usage

    assert attempts == [1]
    assert (usage.input_tokens, usage.output_tokens, usage.cached_tokens) == _PER_ATTEMPT[0]


def test_exhausted_retries_still_raise_instead_of_returning_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한도 안에 못 만들면 기본값을 삼키지 않고 던진다.

    여기서 삼키면 "모델별 구조화 출력 실패율"(evaluation.md §5.1)이 영원히 0이 된다.
    누적 변경이 이 경로를 건드리지 않았음을 함께 고정한다.
    """
    attempts = _stub(monkeypatch, succeed_on=99)

    with pytest.raises(SchemaParseError):
        _client(retries=1).complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    assert attempts == [1, 2]
