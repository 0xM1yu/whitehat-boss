"""`OpenAICompatModelClient`의 재시도 회계.

**이 클래스에는 그전까지 테스트가 한 줄도 없었다.** 그래서 "GPU 시간은 재시도분까지
더하는데 토큰은 마지막 시도만 센다"는 어긋남이 주석 바로 옆에서 살아남았다 — 그 주석이
편향의 논증을 이미 다 적어 두고 있었는데도 그랬다.

HTTP를 띄우지 않고 `_post`만 갈아 끼운다. 여기서 재는 것은 서버와의 통신이 아니라
**여러 시도의 비용을 어떻게 합치는가**이고, 그것은 `complete()` 안에 있다.
"""

import io
import json
import re
import time
import urllib.error
import urllib.request

import pytest
from pydantic import BaseModel, ValidationError

from src.adapters.openai_compat import RETRY_ERROR_MAX_CHARS, OpenAICompatModelClient
from src.interfaces.model_client import (
    MAX_ERROR_DETAIL,
    ModelGatewayError,
    Prompt,
    SchemaParseError,
    Usage,
)
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


class Many(BaseModel):
    """원소마다 오류가 나는 스키마.

    `Tiny`는 오류를 **하나**만 낸다. 재시도 본문이 커지는 두 번째 경로 — 응답 원문이
    아니라 **오류 개수** — 는 그 스키마로는 만들 수가 없다.
    """

    items: list[Tiny]


def _client(retries: int = 2, max_tokens: int = 8192) -> OpenAICompatModelClient:
    return OpenAICompatModelClient(
        endpoint="http://127.0.0.1:9/v1",
        model_name="test-model",
        temperature=0.0,
        top_p=1.0,
        max_tokens=max_tokens,
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
        time.sleep(0.02)  # 각 시도가 wall-clock을 태운다 — gpu_ms 단언에 판별력을 준다
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


# --- 재시도 프롬프트 (D47) -----------------------------------------------------
#
# 재시도 본문은 `complete()` 안에서만 만들어지고 산출물에 안 남는다(D49). **여기가 그것을
# 볼 수 있는 유일한 자리다.** 위 테스트들과 같이 `_post`를 갈아 끼우되 payload를 보관한다 —
# 재는 것이 회계가 아니라 **서버에 실제로 간 문자열**이기 때문이다.

_SENTINEL = "SENTINEL_DO_NOT_ECHO"

# 공백 폭주 응답. 여는 중괄호 뒤로 공백과 탭만 이어지다 끝난다 — 2026-08-28 ATOM
# `run_20260828_0002`의 실패가 전부 이 모양이었다(`EOF while parsing an object at line
# 16139`). 길이를 15,000자 근처에 둔 것은 그 run에서 8,192 토큰 응답이 escape된 뒤의
# 크기와 자릿수를 맞추기 위해서다.
_RUNAWAY = '{\n  "note": "' + _SENTINEL + '",' + "\t \n" * 5000


def _capture(monkeypatch: pytest.MonkeyPatch, raw: str) -> list[dict]:
    """항상 `raw`를 돌려주는 서버. 보낸 payload를 순서대로 모아 돌려준다."""
    payloads: list[dict] = []

    def fake_post(self, payload: dict) -> tuple[str, Usage]:  # noqa: ANN001, ARG001
        payloads.append(payload)
        return raw, Usage(input_tokens=1, output_tokens=1, cached_tokens=0)

    monkeypatch.setattr(OpenAICompatModelClient, "_post", fake_post)
    return payloads


def _user_content(payload: dict) -> str:
    return payload["messages"][1]["content"]


def test_the_retry_does_not_echo_the_failed_response_into_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재시도 본문에 실패한 응답 원문이 실리면 안 된다 (D47).

    실리면 출력 상한이 그 자리에서 **입력** 초과로 되돌아온다. `run_20260828_0002`에서
    8,192 토큰짜리 폭주가 재시도 프롬프트에 실려 입력이 24,576을 넘었고, 게이트웨이가
    400으로 거절해 target 3건이 떨어졌다 — 그중 하나가 그 대상의 정답 함수였다.

    **`include_input=False`를 되돌리면 아래 단언 둘이 다 깨진다**: 덧붙는 문자열이
    200자대가 아니라 25,000자대가 되고 `_SENTINEL`이 그 안에 그대로 실린다.
    """
    payloads = _capture(monkeypatch, _RUNAWAY)

    with pytest.raises(SchemaParseError):
        _client(retries=1).complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    assert len(payloads) == 2
    first, second = (_user_content(p) for p in payloads)
    # 응답 안의 표지가 프롬프트로 건너오지 않는다.
    assert _SENTINEL not in second
    # 덧붙은 것은 오류 메시지뿐이다. 원문이 실리면 escape되며 오히려 더 커진다.
    assert len(second) - len(first) < 1_000


# 원소 500개가 전부 타입 위반인 응답. 원문보다 오류 문자열이 훨씬 크다 —
# `include_input=False`가 막는 축(원문)과 직교하는 축이라 그 인자로는 안 줄어든다.
# **크기는 아래 상수가 들고 `test_the_error_storm_is_the_size_the_bound_is_justified_by`가
# 잰다.** 주석에 적으면 아무도 다시 안 잰다.
_ERROR_STORM = json.dumps({"items": [{"ok": "nope"} for _ in range(500)]})

# --- 상한 2,000을 정당화하는 실측 (pydantic 2.13.4) ----------------------------
#
# **이 수를 주석에만 두지 않는 이유.** `RETRY_ERROR_MAX_CHARS`를 정당화하는 수가 세 자리
# (`openai_compat.py`의 상수 주석·`complete()` 주석·이 파일)에 서로 베껴 적혀 있었는데
# **셋 다 재현 불가능한 값**이었다 — `git grep`으로 열 수 있는 스크립트도 run ID도 없었다.
# 여기 두면 `python -m pytest tests/test_openai_compat.py -k error_storm`이 곧 재현이다.
_STORM_RAW_CHARS = 8_011
# 코드가 실제로 쓰는 플래그 조합 `(include_url=False, include_input=False)`의 값이다.
# 나머지 셋은 70,390 / 90,890 / 99,390 — **어느 조합으로도 이전에 적혀 있던 116,367이
# 안 나온다.** 그 수는 출처가 없었다.
_STORM_ERROR_CHARS = 61_890
_RUNAWAY_ERROR_CHARS = 178
_SINGLE_VIOLATION_ERROR_CHARS = 122


def _error_text(schema: type[BaseModel], raw: str) -> str:
    """어댑터가 재시도에 싣는 **바로 그 문자열**. 플래그 조합까지 같아야 의미가 있다."""
    try:
        schema.model_validate_json(raw)
    except ValidationError as exc:
        return f"{exc.errors(include_url=False, include_input=False)}"
    raise AssertionError("이 픽스처는 파싱에 실패해야 한다")


def test_the_error_storm_is_the_size_the_bound_is_justified_by() -> None:
    """상한을 정당화하는 넷을 **여기서 다시 잰다** (§0: 실측과 추정을 섞지 않는다).

    묶는 것은 값이 아니라 **두 부등식**이다 — 폭주는 상한에 걸리고(그래서 상한이 일을
    한다), 관측된 경로는 안 걸린다(그래서 상한이 지금 도는 것을 안 건드린다). 정확한 수를
    함께 박아 두는 것은 pydantic 판올림이나 픽스처 수정으로 그 부등식의 여유가 조용히
    바뀌는 것을 드러내기 위해서다.
    """
    storm_errors = _error_text(Many, _ERROR_STORM)
    runaway_errors = _error_text(Tiny, _RUNAWAY)
    single_errors = _error_text(Many, json.dumps({"items": [{"ok": "nope"}]}))

    assert len(_ERROR_STORM) == _STORM_RAW_CHARS
    assert len(storm_errors) == _STORM_ERROR_CHARS
    assert len(runaway_errors) == _RUNAWAY_ERROR_CHARS
    assert len(single_errors) == _SINGLE_VIOLATION_ERROR_CHARS

    # 폭주는 걸린다.
    assert len(storm_errors) > RETRY_ERROR_MAX_CHARS
    # 관측된 경로는 안 걸린다 — `_RUNAWAY`가 #79가 실제로 만난 모양이다.
    assert len(runaway_errors) <= RETRY_ERROR_MAX_CHARS
    assert len(single_errors) <= RETRY_ERROR_MAX_CHARS


def test_a_truncated_response_is_a_single_error_so_it_never_reaches_the_fold() -> None:
    """최상위 JSON이 깨지면 `json_invalid`가 **유일한** 오류다.

    `_bounded_error_text`가 머리를 꼬리보다 크게 잡는 사유로 *"`json_invalid`가 첫
    항목이라"*를 적고 있었는데, 그 경로는 **접기까지 가지도 않는다** — 오류가 하나뿐이라
    상한 아래에서 조기 반환한다. 결론(머리 > 꼬리)은 그대로 두되 사유를 여기서 고정한다.
    """
    try:
        Tiny.model_validate_json(_RUNAWAY)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)

    assert len(errors) == 1, "절단 응답인데 오류가 여럿이다 — 머리 편중의 사유가 바뀐다"
    assert errors[0]["type"] == "json_invalid"
    assert len(f"{errors}") <= RETRY_ERROR_MAX_CHARS

# 오류 문자열 앞에 고정으로 붙는 꼬리표. 상한이 묶는 것은 오류 문자열이고 이 꼬리표는
# 그 밖이라, 본문 증가분을 잴 때 함께 세야 한다.
_ERROR_TAG = "\n\n[스키마 오류] "


def test_the_retry_body_stays_bounded_when_the_error_count_explodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재시도 본문이 커지는 축은 **둘**이고, PR #49는 그중 하나만 닫았다.

    위 `test_the_retry_does_not_echo_the_failed_response_into_the_prompt`가 막는 것은
    **응답 원문**이 실리는 축이다(`include_input=False`). 남은 축은 **오류 개수**다 —
    `exc.errors()`는 리스트라 원소마다 한 항목이 붙고, 그 길이에 상한이 없었다.

    크기는 `test_the_error_storm_is_the_size_the_bound_is_justified_by`가 잰다 — 요약하면
    원문 8,011자에 오류 문자열 61,890자로 **원문의 7.7배**이고, D47이 400을 맞은 크기
    (`run_20260828_0002`가 게이트웨이 상한 밖으로 밀어낸 25,223자)의 **2.5배**다.
    **상한 값을 어떻게 잡든 이 축이 열려 있으면 같은 일이 난다.**

    실측된 절단 응답(`_RUNAWAY`)의 오류 문자열은 178자라 이 상한에 닿지 않는다 —
    관측된 경로는 그대로 지나가고 상한은 폭주에만 걸린다.
    """
    payloads = _capture(monkeypatch, _ERROR_STORM)

    with pytest.raises(SchemaParseError):
        _client(retries=1).complete(role=Role.SCOUT, prompt=PROMPT, schema=Many)

    first, second = (_user_content(p) for p in payloads)
    assert len(second) - len(first) <= RETRY_ERROR_MAX_CHARS + len(_ERROR_TAG)


def test_the_bounded_body_keeps_both_ends_and_says_what_it_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """자른 것과 원래 짧은 것을 섞지 않는다 — `truncate_detail`과 같은 규약이다.

    **꼬리를 남기는 이유**: 오류 리스트의 머리만 보면 재시도하는 모델도, 나중에
    `retry_bodies`를 읽는 사람도 "여기까지가 전부"로 읽는다. 표시가 있어야 버려진 것이
    있다는 사실이 남는다.
    """
    payloads = _capture(monkeypatch, _ERROR_STORM)

    with pytest.raises(SchemaParseError):
        _client(retries=1).complete(role=Role.SCOUT, prompt=PROMPT, schema=Many)

    first, second = (_user_content(p) for p in payloads)
    assert "…(중략" in second
    # 머리: 첫 오류의 위치가 살아 있다. 꼬리: 마지막 오류의 위치가 살아 있다.
    assert "'items', 0, 'ok'" in second
    assert "'items', 499, 'ok'" in second

    # **표시의 N이 실제로 버린 수인지**까지 본다. 위 셋은 N을 999로 바꿔도 다 통과한다 —
    # 그러면 "얼마나 잃었나"를 읽는 사람이 틀린 수를 읽고, 상한을 다시 정할 때 쓸 근거가
    # 조용히 오염된다. 남은 것 + 버린 것 = 원래 것이 그 불변식이다.
    marker = re.search(r"…\(중략 (\d+)자\)…", second)
    assert marker is not None
    carried = len(second) - len(first) - len(_ERROR_TAG)  # 실제로 덧붙은 오류 문자열
    kept = carried - len(marker.group(0))  # 그중 머리+꼬리
    assert kept + int(marker.group(1)) == _STORM_ERROR_CHARS, (
        "표시의 N이 실제로 버린 글자 수와 다르다"
    )


# --- 재시도 본문을 실어 올린다 (D49 · #80) -------------------------------------
#
# 어댑터는 산출물 디렉터리를 모르므로 파일을 못 쓴다(계층). 대신 실제로 보낸 2~N차 body를
# `retry_bodies`로 실어 올리고, 상위(`context.call_model`)가 재시도 파일로 남긴다. 여기서
# 고정하는 것은 그 본문이 **서버에 실제로 간 것과 같다**는 것뿐이다 — `_capture`가 payload를
# 들고 있다.


def _capture_until(monkeypatch: pytest.MonkeyPatch, succeed_on: int) -> list[dict]:
    """`succeed_on` 회차에 성공하는 서버. 보낸 payload를 순서대로 모아 돌려준다."""
    payloads: list[dict] = []

    def fake_post(self, payload: dict) -> tuple[str, Usage]:  # noqa: ANN001, ARG001
        payloads.append(payload)
        raw = '{"ok": true}' if len(payloads) == succeed_on else '{"nope": 1}'
        return raw, Usage(input_tokens=1, output_tokens=1, cached_tokens=0)

    monkeypatch.setattr(OpenAICompatModelClient, "_post", fake_post)
    return payloads


def test_successful_retry_carries_the_sent_retry_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재시도로 살아난 성공이 `ModelResult.retry_bodies`에 **실제로 간** 2차 body를 싣는다."""
    payloads = _capture_until(monkeypatch, succeed_on=2)

    result = _client(retries=2).complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    sent = [_user_content(p) for p in payloads]
    assert len(sent) == 2  # 성공까지 2회
    assert result.retry_bodies == tuple(sent[1:])  # 1차 제외한 실제 요청 = 2차 하나


def test_exhausted_retry_bodies_equal_the_sent_requests_without_a_phantom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """소진돼도 `retry_bodies`는 **실제로 간 요청**과 정확히 일치한다 (phantom 없음).

    보내는 시점이 아니라 스키마 오류 핸들러에서 담으면, 마지막 회차가 만든(한 번도 안 보낸)
    body가 끝에 붙어 payload보다 하나 많아진다. 그 회귀를 여기서 막는다.
    """
    payloads = _capture_until(monkeypatch, succeed_on=99)  # 끝까지 실패

    with pytest.raises(SchemaParseError) as excinfo:
        _client(retries=2).complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    sent = [_user_content(p) for p in payloads]
    assert len(sent) == 3  # 1 + 재시도 2
    assert excinfo.value.retry_bodies == tuple(sent[1:])  # 2·3차, unsent phantom 없음


def test_the_retry_still_carries_why_the_parse_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """원문을 뺐다고 사유까지 빼면 안 된다 — `include_context`는 켜 둔다.

    "EOF while parsing an object"가 스키마 위반과 출력 절단을 가르는 유일한 단서다(D46).
    없으면 재시도하는 모델이 무엇을 고쳐야 하는지 모르고, trace의 `error_detail`도 두
    원인을 못 가른다.
    """
    payloads = _capture(monkeypatch, _RUNAWAY)

    with pytest.raises(SchemaParseError):
        _client(retries=1).complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    second = _user_content(payloads[1])
    assert "[스키마 오류]" in second
    assert "EOF" in second


# --- HTTP 오류 응답 본문 (4-1) -------------------------------------------------
#
# `HTTPError`는 `URLError`의 하위 클래스라 400도 예전 `except`로 들어왔는데 `exc.read()`를
# 부르지 않았다. vLLM은 컨텍스트 초과를 **응답 본문에만** 담으므로, zlib run에서 우리가
# 가장 알고 싶었던 "얼마나 넘쳤는가"가 그 자리에서 버려졌다.
#
# 여기서는 `urlopen`을 갈아 끼운다 — 위 테스트들이 `_post`를 갈아 끼우는 것과 달리,
# 재는 것이 바로 그 `_post` 안의 예외 처리이기 때문이다.

_OVERFLOW = (
    b"{\"error\": {\"message\": \"This model's maximum context length is 32768 tokens. "
    b'However, you requested 43200 tokens."}}'
)


def _raise_http(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:9/v1/chat/completions", code, "Bad Request", {}, io.BytesIO(body)
    )


def test_the_http_error_body_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """**이 문자열이 W2 이후 실대상 run의 첫 실측이 된다.**"""
    _raise_http(monkeypatch, _http_error(400, _OVERFLOW))

    with pytest.raises(ModelGatewayError) as caught:
        _client().complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    assert caught.value.status == 400
    assert "43200 tokens" in caught.value.detail


def test_the_status_code_separates_a_bad_request_from_a_dead_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """러너가 "후보만 버린다"와 "즉시 멈춘다"를 가르는 입력이 이 필드다.

    연결 실패에는 상태 코드가 없다 — `None`을 0이나 500으로 채우면 없는 사실을 지어내는
    것이고, 러너가 그것을 보고 잘못된 쪽으로 간다.
    """
    _raise_http(monkeypatch, urllib.error.URLError("Connection refused"))

    with pytest.raises(ModelGatewayError) as caught:
        _client().complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    assert caught.value.status is None
    assert "Connection refused" in caught.value.detail


def test_a_long_error_body_is_cut_and_says_that_it_was_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """자른 것과 원래 짧은 것을 섞지 않는다 — 표시가 없으면 전부인지 알 수 없다."""
    _raise_http(monkeypatch, _http_error(400, b"x" * 4000))

    with pytest.raises(ModelGatewayError) as caught:
        _client().complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    assert len(caught.value.detail) == MAX_ERROR_DETAIL + len("…(잘림)")
    assert caught.value.detail.endswith("…(잘림)")


def test_an_unreadable_error_body_does_not_lose_the_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """본문을 못 읽어도 우리가 잃는 것은 상세뿐이다. 거기서 예외를 올리면 상태까지 잃는다."""

    class Closed(io.BytesIO):
        def read(self, *_args: object) -> bytes:
            raise OSError("stream closed")

    _raise_http(monkeypatch, _http_error(413, b""))
    monkeypatch.setattr(urllib.error.HTTPError, "read", Closed.read, raising=False)

    with pytest.raises(ModelGatewayError) as caught:
        _client().complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    assert caught.value.status == 413
    assert "읽지 못했다" in caught.value.detail



def test_the_output_cap_is_actually_sent_and_comes_from_the_caller() -> None:
    """`max_tokens`가 payload에 실린다.

    **이것이 없으면 상한도 없다.** 이 필드가 payload에 없으면 서버는 `--max-model-len`까지
    생성하고, 2026-08-27 ATOM에서 30B가 그렇게 폭주해 클라이언트 타임아웃이 났다.

    상수가 아니라 **생성자가 받은 값**이 실리는지를 본다 — 값을 하드코딩하면 config를
    바꿔도 payload가 안 따라오고, 그러면 `RunRecord.thresholds`에 적힌 상한과 실제로 쓰인
    상한이 갈린다.
    """
    payload = _client(max_tokens=1234)._request_payload("prefix", "body", Tiny)

    assert payload["max_tokens"] == 1234
    # 기존 요청 파라미터가 밀려나지 않았다 — 같은 dict에 나란히 있어야 한다.
    assert payload["temperature"] == 0.0
    assert payload["top_p"] == 1.0
    assert payload["seed"] == 7


# --- 시도 수와 실패분 회계 (D55·D53) ------------------------------------------
#
# `complete()`가 재시도를 자기 안에서 끝내고 성공 한 건을 내므로, 1차 실패 후 살아난
# 호출이 성공 한 건과 구별되지 않는다(D55). 그리고 통째로 실패하면 이미 쓴 토큰·GPU가
# 예외와 함께 사라져 상위가 장부에 청구할 것이 없다(D53). 시도 수를 결과·예외에 싣는다.


def test_the_result_carries_how_many_attempts_it_took(monkeypatch: pytest.MonkeyPatch) -> None:
    """2회 실패 후 성공하면 `attempts == 3`이다 (D55).

    이 값이 `model_call`에 실려야 evaluation.md §5.1의 "구조화 출력 실패율" 분자가
    '회복 못 한 실패'만이 아니라 '일어난 실패'를 센다. 지금은 성공 한 건과 구별 불가라
    run B의 실패율이 실제 1/26인데 기록으로는 0%다.
    """
    _stub(monkeypatch, succeed_on=3)

    result = _client().complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    assert result.attempts == 3


def test_a_first_try_success_reports_one_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """재시도가 없으면 `attempts == 1`이다. 성공 경로를 부풀리면 안 된다."""
    _stub(monkeypatch, succeed_on=1)

    result = _client().complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    assert result.attempts == 1


def test_exhaustion_carries_the_spend_it_already_made(monkeypatch: pytest.MonkeyPatch) -> None:
    """소진 예외가 이미 쓴 토큰·GPU·시도 수를 들고 나온다 (D53·D55).

    실패한 응답도 서버가 만들어 냈고 그 토큰·GPU는 실제로 쓰였다. 예외가 빈손으로 나오면
    상위(`context.call_model`)가 장부에 청구할 것이 없어, 폭주가 잦은 조건일수록 싸 보인다 —
    §5.2 비용표의 분자가 조건 비교에서 정확히 거꾸로 간다.
    """
    _stub(monkeypatch, succeed_on=99)

    with pytest.raises(SchemaParseError) as caught:
        _client(retries=1).complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    exc = caught.value
    assert exc.attempts == 2  # retries=1 → 2회 시도
    # 2회분(100+200 / 10+20 / 40+20) 합. 마지막 것만이 아니다.
    assert exc.usage.input_tokens == 100 + 200
    assert exc.usage.output_tokens == 10 + 20
    assert exc.usage.cached_tokens == 40 + 20
    # 2회 시도가 각자 20ms를 태웠으므로 0이 아니다 — `gpu_ms`를 리터럴 0으로 상수화하는
    # 변이를 잡는다(#82 리뷰 F9). `>= 0`은 int면 무조건 통과라 판별력이 없었다.
    assert exc.gpu_ms >= 30
    assert exc.gpu_ms_source == "request_wall_clock"


def test_gateway_failure_carries_the_spend_and_its_own_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트웨이 거절도 이미 쓴 토큰·시도 수를 싣고, **자기 시도의 wall-clock까지** 라벨과
    함께 실어 올린다 (#82 리뷰 F1·F9).

    F1 이전에는 `gpu_ms +=`가 except 블록 밖이라 게이트웨이 실패가 재보지도 않은 0에
    `request_wall_clock` 라벨을 붙였다 — 0은 "안 썼다"는 적극적 진술이라 §0 위반이었다.
    """
    calls: list[int] = []

    def fake_post(self, payload):  # noqa: ANN001, ANN202, ARG001
        calls.append(1)
        time.sleep(0.02)
        if len(calls) == 1:  # 1차는 파싱 실패 → 재시도
            return '{"nope": 1}', Usage(input_tokens=100, output_tokens=10, cached_tokens=40)
        raise ModelGatewayError("400", status=400, detail="maximum context length")

    monkeypatch.setattr(OpenAICompatModelClient, "_post", fake_post)

    with pytest.raises(ModelGatewayError) as caught:
        _client(retries=1).complete(role=Role.SCOUT, prompt=PROMPT, schema=Tiny)

    exc = caught.value
    assert exc.attempts == 2  # 1차 파싱 실패 + 2차 게이트웨이
    assert exc.usage.input_tokens == 100  # 1차분이 실린다
    assert exc.gpu_ms_source == "request_wall_clock"
    # 두 시도가 각자 20ms를 태웠다 — 게이트웨이 시도의 시간도 포함된다(F1). 한 시도(≈20ms)만
    # 세면 30 미만이라, 게이트웨이 시도가 안 잡히는 회귀를 이 임계가 잡는다.
    assert exc.gpu_ms >= 30
