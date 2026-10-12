"""`model_call`의 시도별 계측 (#550) — `attempt_output_tokens` · `attempt_finish_reasons`.

**무엇이 문제였나.** 실서빙 run `run_20261007_0001`에서 Finding 5건 중 2건이 `SchemaParseError`로
버려졌다. 둘 다 재시도까지 쓰고도 실패했는데, 원인을 가를 수단이 없었다 — `finish_reason`은 어디에도
안 남았고(어댑터가 응답에서 읽지도 않았다) `output_tokens`는 **시도를 합친 값**이었다.
합이 **이미** 준 것과 **못** 준 것을 가른다(CLAUDE.md §2.2). `find_0005`의 65,536은 상한(32,768)의
2배라 산술로 두 시도가 **정확히** 32,768씩이고(각각 상한 이하이므로), 그때 남은 물음은 그 값이
`length`인가 상한과 같은 값에서의 정상 종료(`stop`)인가뿐이었다. `find_0004`의 27,888은
**`null` 칸이 없을 때** 두 시도가 다 상한 아래임을 산술로 보인다(그러면 `max_tokens` 절단은
배제된다). `null` 칸은 합에 0을 더한 시도라 합이 그 몫만큼 과소일 수 있고, 그 칸이 있으면 이
산술은 서지 않는다.
합이 **못** 주는 것은 ① 상한과 그 2배 **사이**의 합(§2.2의 `8,402` 사례) ② `abort` 같은
`length`·`stop` 밖의 사유 ③ 추론이 아닌 서버의 직접 확인이다. 두 수의 출처는 이슈 #550
본문(`run_20261007_0001`의 `trace.jsonl` 판독)이다.

**여기서 고정하는 것.**

1. 응답의 `finish_reason`이 `model_call`까지 간다 — 실제 `_post`를 지난다(`urlopen`만 갈아 끼운다)
2. 시도마다 자기 `output_tokens`·`finish_reason`이 **시도 순서대로** 남는다
3. **합이 같아도 쪼갠 모양은 다르게 남는다** — 합이 못 가르는 사유를 가르는 핀이 이것이다
4. **실패 경로도 싣는다** — 가르려는 Finding이 전부 실패했다
5. 못 읽은 `finish_reason`은 `None`이다(`""`·`0`·`"unknown"`이 아니다) — 응답은 멀쩡히 성공한다
6. 합산 `usage.output_tokens`는 그대로이고 시도별 값의 합과 어긋나지 않는다
7. 어댑터가 안 실은 호출(Fake·프로필에 없는 역할을 부른 `UnknownRoleError`)은 **키는 있고 값이
   `None`**이다 — 지어낸 값이 없다. (`BudgetExhausted`는 예가 아니다: `ledger.ensure`가 `try` 밖이라
   `model_call` 이벤트가 아예 안 난다.)
8. **서버가 `completion_tokens`를 안 실으면 그 칸은 `None`이다**(합에는 0이 들어간다) —
   「안 줬다」와 「0이라 보고했다」가 한 값으로 합쳐지지 않는다
9. **종료 사유는 서버의 말만이다** — 상한과 같은 토큰 수가 `finish_reason` 없는 응답에서 `length`를
   지어내지 않는다
10. 시도가 셋이어도 칸이 시도마다 자기 값을 든다(밀리거나 앞 칸을 물려받지 않는다)
11. 터무니없이 긴 `finish_reason`은 묶이고 정상 길이는 한 글자도 안 바뀐다
12. 잃은 내역은 잃은 채로 읽힌다 — 어댑터가 안 잡는 예외가 `complete()`를 뚫으면 null/null이다
    (`trace.py` 읽는 규칙 2의 (나)). 닿는 길의 측정된 예: 검증기의 `TypeError`, 서버가 보낸 깊게
    중첩된 JSON의 `RecursionError`
13. 서버가 `usage`에 `1e999`·`Infinity`를 실어도 `OverflowError`가 아니라 `ModelGatewayError`다 —
    이미 잰 시도의 내역을 들고 온다
14. 공백뿐인 `finish_reason`은 길이와 무관하게 `None`이고(`''`가 아니다), 원문이 상한을 넘는 값의
    공백 접기는 잘림 표시 없이 일어날 수 있다 — 그 사실 그대로 고정한다

**이 핀들은 상한 값·파싱 실패 처리·응답 원문 보존을 건드리지 않는다**(#550의 (c)·(d)와 D84는
별개다).
"""

import io
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, field_validator

from src.adapters import openai_compat
from src.adapters.fake_model import FakeContext, FakeModelClient
from src.adapters.memory_store import InMemoryStateStore
from src.adapters.openai_compat import OpenAICompatModelClient
from src.adapters.routing_model import RoutingModelClient, UnknownRoleError
from src.config import load_config
from src.ids import IdFactory
from src.interfaces.model_client import (
    MAX_ERROR_DETAIL,
    AttemptRecord,
    ModelGatewayError,
    ModelResult,
    Prompt,
    SchemaParseError,
    Usage,
)
from src.orchestrator.budget import BudgetExhausted, BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.stages import Stage
from src.orchestrator.trace import (
    EventKind,
    TraceWriter,
    schema_failure_max_run_by_role,
    schema_recovered_calls_by_role,
)
from src.schemas import Candidate, Location, Role
from src.schemas.run import Budget

RUN_ID = "run_20261008_0001"
NOW = datetime(2026, 10, 8, tzinfo=UTC)
PROMPT = Prompt(prefix="공통 지시", body="후보 본문", prompt_version="v1")

# `config/budgets.yaml` `output_max_by_role.pov`의 값이다. find_0005가 실서빙에서 남긴 합 65,536은
# 이 값의 2배다.
POV_CAP = 32_768

GOOD = '{"ok": true}'
BAD = '{"nope": 1}'  # `Tiny.ok`가 없어 `ValidationError` -> 재시도
TRUNCATED = '{"ok": tr'  # 절단된 JSON을 닮은 파싱 불가 응답

_MISSING = object()  # finish_reason 키 자체가 없는 응답


class Tiny(BaseModel):
    ok: bool


class _Explodes(BaseModel):
    """검증기가 `ValueError`가 아닌 예외를 던지는 스키마 — pydantic이 안 감싸고 그대로 올린다."""

    ok: bool

    @field_validator("ok")
    @classmethod
    def _boom(cls, value: bool) -> bool:  # noqa: ARG003
        raise TypeError("ValueError가 아닌 예외")


# --- 전송 흉내 -------------------------------------------------------------------------------


class _FakeResponse:
    """`urlopen`의 컨텍스트 매니저 흉내 — `read()`가 주어진 바이트를 낸다."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _completion(
    content: object,
    tokens: int | None,
    finish_reason: object = "stop",
    *,
    usage: bool = True,
) -> bytes:
    """vLLM `/chat/completions` 200 응답 한 건.

    `finish_reason=_MISSING`이면 그 키를 뺀다. `tokens=None`이면 `usage.completion_tokens`를 뺀다
    (서버가 안 실은 모양). `usage=False`면 `usage` 객체째 뺀다."""
    choice: dict[str, object] = {
        "index": 0,
        "message": {"role": "assistant", "content": content},
    }
    if finish_reason is not _MISSING:
        choice["finish_reason"] = finish_reason
    body: dict[str, object] = {"choices": [choice]}
    if usage:
        usage_object: dict[str, int] = {"prompt_tokens": 100}
        if tokens is not None:
            usage_object["completion_tokens"] = tokens
        body["usage"] = usage_object
    return json.dumps(body).encode("utf-8")


def _completion_with_usage_literal(usage_json: str) -> bytes:
    """`usage`를 JSON **원문 그대로** 넣은 200 — `json.dumps`가 못 만드는 `1e999`·`Infinity`."""
    head = '{"choices": [{"index": 0, "message": {"role": "assistant", "content": '
    tail = '}, "finish_reason": "stop"}], "usage": '
    return (head + json.dumps(GOOD) + tail + usage_json + "}").encode("utf-8")


def _deeply_nested_body() -> bytes:
    """서버가 보낸 100,000단 중첩 JSON — `json.loads`가 `RecursionError`를 던지는 길이다.

    `ValueError`가 아니고 `_post`의 어느 `except`에도 안 걸린다(3.14.5에서 쟀다).
    응답 하나로 닿는다."""
    depth = 100_000
    return b"[" * depth + b"]" * depth


def _http_400() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:9/v1/chat/completions",
        400,
        "Bad Request",
        {},
        io.BytesIO(b'{"error": {"message": "maximum context length is 32768 tokens"}}'),
    )


def _serve(monkeypatch: pytest.MonkeyPatch, replies: list[object]) -> None:
    """`urlopen`을 갈아 끼운다 — 회차마다 `replies`의 다음 것을 낸다(바이트면 200, 예외면 던진다).

    `_post`는 갈아 끼우지 않는다. 재는 것이 바로 그 안의 응답 해석이다."""
    queue = list(replies)

    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202, ARG001
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _FakeResponse(reply)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(openai_compat, "generation_tokens", lambda _endpoint: None)


def _client(retries: int = 1) -> OpenAICompatModelClient:
    return OpenAICompatModelClient(
        endpoint="http://127.0.0.1:9/v1",
        model_name="test-model",
        temperature=0.0,
        top_p=1.0,
        max_tokens=POV_CAP,
        seed=7,
        retries=retries,
    )


# --- 호출 맥락 -------------------------------------------------------------------------------


def _ctx(models: object) -> RunContext:
    config = load_config("dev_fake")
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    return RunContext(
        run_id=RUN_ID,
        config=config,
        models=models,
        retriever=None,
        tools=None,
        store=InMemoryStateStore(),
        ids=IdFactory(RUN_ID),
        trace=trace,
        ledger=BudgetLedger(
            Budget(input_tokens=10_000_000, output_tokens=10_000_000, gpu_seconds=0), trace
        ),
        policy=FixedThresholdPolicy(config.thresholds),
        monotonic_ms=lambda: 0,
    )


def _run(
    models: object, *, schema: type[BaseModel] = Tiny, prompt: Prompt = PROMPT
) -> tuple[object, dict]:
    """`call_model`을 돌려 (결과 또는 예외, `model_call` 이벤트 데이터)를 돌려준다."""
    ctx = _ctx(models)
    try:
        outcome: object = ctx.call_model(
            role=Role.SCOUT, prompt=prompt, schema=schema, stage=Stage.SCOUT
        )
    except Exception as exc:  # noqa: BLE001 - 실패 경로의 이벤트가 이 테스트의 대상이다
        outcome = exc
    (event,) = [e for e in ctx.trace.events if e.kind is EventKind.MODEL_CALL]
    return outcome, event.data


def _breakdown(data: dict) -> tuple[list, list]:
    """이벤트의 시도별 값 (출력 토큰들, 종료 사유들) — JSON 배열 문자열을 푼다."""
    return json.loads(data["attempt_output_tokens"]), json.loads(data["attempt_finish_reasons"])


# --- 1·2. 종료 사유가 이벤트까지 가고 시도 순서를 지킨다 -----------------------------------------


def test_finish_reason_length_reaches_the_model_call_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """응답의 `choices[0].finish_reason`이 `_post`를 지나 trace까지 간다."""
    _serve(monkeypatch, [_completion(GOOD, 4096, "length")])

    outcome, data = _run(_client())

    assert outcome.value == Tiny(ok=True)
    assert outcome.attempt_log == (AttemptRecord(output_tokens=4096, finish_reason="length"),)
    assert _breakdown(data) == ([4096], ["length"])


def test_two_attempts_are_listed_in_order_each_with_its_own_tokens_and_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1차는 상한에서 잘렸고 2차는 정상 종료했다 — 두 값이 **서로 달라야** 순서가 읽힌다."""
    _serve(monkeypatch, [_completion(TRUNCATED, 8192, "length"), _completion(GOOD, 12, "stop")])

    outcome, data = _run(_client(retries=1))

    assert outcome.attempts == 2
    assert outcome.attempt_log == (
        AttemptRecord(output_tokens=8192, finish_reason="length"),
        AttemptRecord(output_tokens=12, finish_reason="stop"),
    )
    assert _breakdown(data) == ([8192, 12], ["length", "stop"])  # 뒤집힌 [12, 8192]가 아니다
    assert data["output_tokens"] == 8192 + 12  # 합은 그대로다


# --- 3. 같은 합, 다른 쪼갬 --------------------------------------------------------------------

# 합이 넷 다 65,536이다 — `find_0005`와 같은 합이다. 이 합에서 두 시도가 정확히 상한이라는 것까지는
# 산술로 나오고, 합이 못 가르는 것은 그 값이 `length`인지 `stop`인지다.
_SAME_TOTAL = {
    "both_attempts_hit_the_cap": ((POV_CAP, POV_CAP), ("length", "length")),
    "neither_hit_it": ((POV_CAP, POV_CAP), ("stop", "stop")),
    "only_the_first": ((POV_CAP, POV_CAP), ("length", "stop")),
    "only_the_second": ((POV_CAP, POV_CAP), ("stop", "length")),
}


def test_the_same_total_splits_into_different_breakdowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**합이 같은 네 경우에서 종료 사유만 갈리는지 보는 핀.** 합은 같고 쪼갠 모양은 다른지 본다.

    「둘 다 상한에 닿았다」(`length`,`length`)와 「둘 다 상한과 같은 값에서 정상 종료했다」
    (`stop`,`stop`)는 토큰 합도, 시도별 토큰도 같다. 갈리는 것은 `finish_reason`뿐이다 — 그래서
    이 값을 안 읽으면 토큰이 정확히 상한이어도 절단을 단정할 수 없다. (이 합에서 토큰이 상한이라는
    것 자체는 산술이 이미 준다. 이 핀이 문 것은 사유다.)"""
    seen: dict[str, tuple[int, tuple[list, list]]] = {}
    for name, (tokens, reasons) in _SAME_TOTAL.items():
        _serve(monkeypatch, [_completion(BAD, t, r) for t, r in zip(tokens, reasons, strict=True)])
        outcome, data = _run(_client(retries=1))
        assert isinstance(outcome, SchemaParseError), name
        seen[name] = (data["output_tokens"], _breakdown(data))

    assert {total for total, _ in seen.values()} == {2 * POV_CAP}  # 합은 하나다
    shapes = {json.dumps(shape) for _, shape in seen.values()}
    assert len(shapes) == len(_SAME_TOTAL)  # 모양은 넷이다
    assert seen["both_attempts_hit_the_cap"][1] == ([POV_CAP, POV_CAP], ["length", "length"])
    assert seen["neither_hit_it"][1] == ([POV_CAP, POV_CAP], ["stop", "stop"])


# --- 4. 실패 경로도 싣는다 ---------------------------------------------------------------------


def test_a_schema_parse_error_after_two_attempts_still_reports_the_breakdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`find_0005`의 모양 그대로 — 두 시도가 다 파싱에 실패한다. 예외와 trace가 쪼갠 값을 든다."""
    _serve(monkeypatch, [_completion(BAD, POV_CAP, "length"), _completion(BAD, POV_CAP, "length")])

    outcome, data = _run(_client(retries=1))

    assert isinstance(outcome, SchemaParseError)
    assert outcome.attempt_log == (
        AttemptRecord(output_tokens=POV_CAP, finish_reason="length"),
        AttemptRecord(output_tokens=POV_CAP, finish_reason="length"),
    )
    assert data["ok"] is False
    assert data["error"] == "SchemaParseError"
    assert data["attempts"] == 2
    assert data["output_tokens"] == 2 * POV_CAP
    assert _breakdown(data) == ([POV_CAP, POV_CAP], ["length", "length"])


@pytest.mark.parametrize(
    "first_reply",
    [
        _http_400(),
        urllib.error.URLError("Connection refused"),
        json.dumps({"choices": []}).encode("utf-8"),  # 200인데 모양이 계약과 다르다
        TimeoutError("timed out"),  # `_post`가 따로 잡는 갈래 — `generating` 판정이 붙는 쪽이다
    ],
    ids=["http_400", "connection_refused", "malformed_200", "timeout"],
)
def test_a_gateway_error_on_the_first_attempt_leaves_one_unmeasured_slot(
    monkeypatch: pytest.MonkeyPatch, first_reply: object
) -> None:
    """응답이 없는 시도도 자리를 지킨다 — 칸은 있고 값은 `None`이다(안 쟀다 ≠ 0)."""
    _serve(monkeypatch, [first_reply])

    outcome, data = _run(_client(retries=1))

    assert isinstance(outcome, ModelGatewayError)
    assert outcome.attempt_log == (AttemptRecord(),)
    assert data["ok"] is False
    assert data["attempts"] == 1
    assert _breakdown(data) == ([None], [None])


def test_a_gateway_error_after_a_truncated_attempt_keeps_the_earlier_breakdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1차는 응답을 냈고(잘림) 2차는 게이트웨이가 거절했다 — 1차의 값이 사라지면 안 된다."""
    _serve(monkeypatch, [_completion(TRUNCATED, 8192, "length"), _http_400()])

    outcome, data = _run(_client(retries=1))

    assert isinstance(outcome, ModelGatewayError)
    assert outcome.attempts == 2
    assert _breakdown(data) == ([8192, None], ["length", None])
    assert data["output_tokens"] == 8192


# --- 5. 못 읽은 종료 사유는 None이다 ----------------------------------------------------------


@pytest.mark.parametrize(
    "finish_reason",
    [_MISSING, None, "", 7],
    ids=["key_absent", "json_null", "empty_string", "not_a_string"],
)
def test_an_unreadable_finish_reason_is_none_and_the_call_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, finish_reason: object
) -> None:
    """키가 없거나 비었거나 문자열이 아니면 `None`이다 — `""`·`0`·`"unknown"`이 아니다.

    그리고 **멀쩡한 200 응답을 오류로 만들지 않는다.** `finish_reason`은 이 호출의 부가 정보이고
    내용(`content`)이 맞으면 성공이다."""
    _serve(monkeypatch, [_completion(GOOD, 5, finish_reason)])

    outcome, data = _run(_client())

    assert outcome.value == Tiny(ok=True)
    (record,) = outcome.attempt_log
    assert record.finish_reason is None
    assert record.output_tokens == 5
    assert data["attempt_finish_reasons"] == "[null]"


def test_an_unfamiliar_finish_reason_is_kept_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """처음 보는 사유(`abort` 등)를 허용 목록으로 걸러 버리면 이 필드의 존재 이유가 사라진다."""
    _serve(monkeypatch, [_completion(GOOD, 5, "abort")])

    outcome, _ = _run(_client())

    assert outcome.attempt_log[0].finish_reason == "abort"


@pytest.mark.parametrize(
    "finish_reason",
    [_MISSING, None, ""],
    ids=["key_absent", "json_null", "empty_string"],
)
def test_a_cap_sized_answer_without_a_finish_reason_is_not_called_length(
    monkeypatch: pytest.MonkeyPatch, finish_reason: object
) -> None:
    """**토큰 수가 상한과 같아도 종료 사유를 지어내지 않는다** (CLAUDE.md §0·§2.2).

    `output_tokens >= max_tokens`로 `length`를 채우는 폴백은 **추정을 서버의 측정으로 꾸민 것**이다.
    이 계측이 막으려고 있는 바로 그 오류다. 위 「못 읽은 종료 사유」 핀은 5토큰짜리라 상한에서
    한참 먼 값이어서 그런 폴백을 못 잡는다(`finish_reason or ("length" if tokens >= cap else
    None)`이 그 핀을 통과했다). 여기는 응답을 **상한 그대로**로 줘서 폴백이 있으면 `length`가
    나오게 한다."""
    _serve(monkeypatch, [_completion(GOOD, POV_CAP, finish_reason)])
    client = _client()
    assert client.max_tokens == POV_CAP  # 「상한과 같다」는 전제가 이 핀의 전부다

    outcome, data = _run(client)

    assert outcome.attempt_log == (AttemptRecord(output_tokens=POV_CAP, finish_reason=None),)
    assert _breakdown(data) == ([POV_CAP], [None])


# --- 5b. 터무니없이 긴 종료 사유는 묶이고 정상 길이는 그대로다 ----------------------------------

_CUT = "…(잘림)"  # `truncate_detail`이 붙이는 표시


@pytest.mark.parametrize(
    "size",
    [MAX_ERROR_DETAIL, MAX_ERROR_DETAIL + 1, 2_000_000],
    ids=["at_the_bound", "one_over", "two_megabytes"],
)
def test_an_oversized_finish_reason_is_bounded_in_the_trace_cell(
    monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    """`finish_reason`은 서버가 정한 값이라 길이 상한이 없었다 — 2MB 값이 2MB 트레이스 셀이 됐다.

    `error_detail`과 같은 자르기(`truncate_detail`)를 쓴다. 경계(`MAX_ERROR_DETAIL`)까지는 한 글자도
    안 바뀌고, 넘으면 잘렸다는 표시가 붙는다 — 자른 것과 원래 짧은 것을 섞지 않는다."""
    reason = "x" * size
    _serve(monkeypatch, [_completion(GOOD, 5, reason)])

    outcome, data = _run(_client())

    kept = outcome.attempt_log[0].finish_reason
    assert kept is not None
    if size <= MAX_ERROR_DETAIL:
        assert kept == reason
    else:
        assert kept.endswith(_CUT)
        assert len(kept) <= MAX_ERROR_DETAIL + len(_CUT)
    assert len(data["attempt_finish_reasons"]) < 2 * MAX_ERROR_DETAIL  # 셀 전체가 묶인다


@pytest.mark.parametrize(
    "reason",
    [" ", "\n\t", " " * 600, " " * (MAX_ERROR_DETAIL + 1), " " * 2_000_000],
    ids=["one_space", "newline_tab", "600_spaces", "one_over_the_bound", "two_megabytes"],
)
def test_a_whitespace_only_finish_reason_is_none_at_any_length(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """공백뿐인 사유는 길이와 무관하게 `None`이다 — `''`가 아니다 (`AttemptRecord` 계약).

    **`'x' * N`만 재던 앞 핀은 이 구멍을 못 봤다.** 원문이 512자를 넘으면 `truncate_detail`이 공백을
    접어 `''`를 돌려줬고, 짧은 공백뿐인 값은 그대로 `'   '`로 남았다 — 같은 값이 길이에 따라 다르게
    나갔다."""
    _serve(monkeypatch, [_completion(GOOD, 5, reason)])

    outcome, data = _run(_client())

    assert outcome.attempt_log[0].finish_reason is None
    assert data["attempt_finish_reasons"] == "[null]"


# (원문, 기대). 원문이 512자를 **넘는데** 공백을 접으면 들어가는 값들 — `truncate_detail`이
# 공백을 접고 **다시 재므로** 잘림 표시 없이 짧아진다. 문서(`trace.py`·`AttemptRecord`·
# `_finish_reason_from`)가 적은 그대로를 고정한다: 잃는 것은 공백뿐이고 내용 문자는 안 잃는다.
_FOLDED_WITHOUT_A_MARK = {
    "trailing_spaces": ("length" + " " * (MAX_ERROR_DETAIL - 5), "length"),  # 513자
    "spaces_between_words": (
        " ".join(["abort"] * 50) + " " * 600,
        " ".join(["abort"] * 50),
    ),
    "newlines_and_tabs": (
        "stop\n\n\t" * 50 + "x" * 200,
        " ".join(["stop"] * 50) + " " + "x" * 200,
    ),
}


@pytest.mark.parametrize(
    ("raw", "expected"),
    list(_FOLDED_WITHOUT_A_MARK.values()),
    ids=list(_FOLDED_WITHOUT_A_MARK),
)
def test_an_over_bound_finish_reason_that_folds_to_fit_is_shortened_without_a_mark(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    """**표시가 붙는 것은 접은 뒤에도 넘칠 때뿐이다** — 「묶이면 표시가 붙는다」는 거짓이었다.

    원문 길이로 묶음 여부를 정하고(512자 초과) 표시는 접은 길이로 정하므로, 그 둘 사이의 값은
    공백만 바뀌고 표시가 없다. 이 핀은 그 동작을 있는 그대로 못 박는다 — 누가 표시를 붙이도록
    고치면 문서 셋(`trace.py`·`AttemptRecord`·`_finish_reason_from`)을 같이 고친다."""
    assert len(raw) > MAX_ERROR_DETAIL  # 이 핀의 전제: 묶음 갈래를 탄다
    assert len(expected) <= MAX_ERROR_DETAIL  # 그리고 접으면 들어간다
    _serve(monkeypatch, [_completion(GOOD, 5, raw)])

    outcome, _ = _run(_client())

    assert outcome.attempt_log[0].finish_reason == expected
    assert not outcome.attempt_log[0].finish_reason.endswith(_CUT)


def test_an_over_bound_finish_reason_that_still_overflows_after_folding_is_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """위 핀의 반대편 — 접어도 넘치면 잘림 표시가 붙고, 길이는 `512 + 표시`다."""
    raw = "x\n" * 400  # 접으면 `x x x …`(799자)
    _serve(monkeypatch, [_completion(GOOD, 5, raw)])

    outcome, _ = _run(_client())

    kept = outcome.attempt_log[0].finish_reason
    assert kept is not None
    assert kept.endswith(_CUT)
    assert len(kept) == MAX_ERROR_DETAIL + len(_CUT)
    assert "\n" not in kept  # 줄바꿈이 접혔다


def test_a_normal_length_finish_reason_is_not_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    """정상 길이는 **한 글자도** 안 바뀐다 — 공백을 접는 `truncate_detail`을 무조건 거치면 서버가 준
    값이 아니게 된다. 자르기는 넘칠 때만 한다."""
    odd = "tool  calls\n"
    _serve(monkeypatch, [_completion(GOOD, 5, odd)])

    outcome, _ = _run(_client())

    assert outcome.attempt_log[0].finish_reason == odd


# --- 6. 합산 usage는 그대로이고 시도별 값과 어긋나지 않는다 -------------------------------------

# (회차별 응답, 재시도 횟수, 합산 `output_tokens`의 기대값)
_SUM_SCENARIOS = {
    "success_after_two_failures": (
        [_completion(BAD, 100, "length"), _completion(BAD, 200, "length"), _completion(GOOD, 300)],
        2,
        600,
    ),
    "schema_exhausted": (
        [_completion(BAD, 100, "stop"), _completion(BAD, 200, "length"), _completion(BAD, 300)],
        2,
        600,
    ),
    "gateway_error_on_the_third": (
        [_completion(BAD, 100, "stop"), _completion(BAD, 200, "length"), _http_400()],
        2,
        300,
    ),
}


@pytest.mark.parametrize(
    ("replies", "retries", "expected_total"),
    list(_SUM_SCENARIOS.values()),
    ids=list(_SUM_SCENARIOS),
)
def test_the_summed_usage_equals_the_sum_of_the_attempts(
    monkeypatch: pytest.MonkeyPatch, replies: list[object], retries: int, expected_total: int
) -> None:
    """합과 쪼갠 값이 **조용히 어긋나지 않는다.** 합은 이 PR 전과 같은 값이다(원장이 그것을 읽는다).

    응답이 없는 칸(`None`)은 합에 안 들어간다 — 서버가 그 시도의 토큰을 주지 않았다."""
    _serve(monkeypatch, replies)

    outcome, data = _run(_client(retries=retries))

    usage: Usage = outcome.usage
    measured = [a.output_tokens for a in outcome.attempt_log if a.output_tokens is not None]
    assert usage.output_tokens == expected_total
    assert usage.output_tokens == sum(measured)
    tokens, _ = _breakdown(data)
    assert data["output_tokens"] == sum(t for t in tokens if t is not None) == expected_total
    assert len(tokens) == data["attempts"]  # 칸은 시도마다 하나다


# --- 6b. 재시도 뒤 성공도 `length`로 끝날 수 있다 -----------------------------------------------


@pytest.mark.parametrize(
    ("replies", "expected"),
    [
        (
            [_completion(BAD, 100, "stop"), _completion(GOOD, 200, "length")],
            ([100, 200], ["stop", "length"]),
        ),
        (
            [_completion(TRUNCATED, POV_CAP, "length"), _completion(GOOD, POV_CAP, "length")],
            ([POV_CAP, POV_CAP], ["length", "length"]),
        ),
    ],
    ids=["stop_then_length", "length_then_length"],
)
def test_a_retry_that_succeeds_may_itself_end_on_length(
    monkeypatch: pytest.MonkeyPatch, replies: list[object], expected: tuple[list, list]
) -> None:
    """유효한 JSON이 `length`로 끝나는 경우 — 재시도 뒤 성공 호출의 마지막 칸이 `length`다.

    지금까지 재시도 뒤 성공 핀은 전부 `stop`으로 끝났고 `length` 핀은 전부 소진 사례였다. 그래서
    「재시도가 있으면 마지막 사유를 `stop`으로 본다」는 변이가 모든 핀을 통과했다. 이 핀은 마지막
    칸이 `length`이고 앞 칸이 다른 값이라 그 변이도 「앞 칸을 물려받는」 변이도 잡는다."""
    _serve(monkeypatch, replies)

    outcome, data = _run(_client(retries=1))

    assert outcome.value == Tiny(ok=True)
    assert outcome.attempts == 2
    assert _breakdown(data) == expected


# --- 6c. 시도가 셋이어도 칸은 시도마다 자기 값을 든다 -------------------------------------------

# (회차별 응답, 재시도 횟수, (시도별 토큰, 시도별 사유)). 값을 칸마다 다르게 줘서 밀림·물려받기·
# 잘림이 서로 다른 모양으로 드러난다. 사유는 회차마다 순열을 바꾼다.
_THREE_ATTEMPTS = {
    "success_on_the_third": (
        [
            _completion(BAD, 111, "length"),
            _completion(BAD, 222, "stop"),
            _completion(GOOD, 333, "abort"),
        ],
        2,
        ([111, 222, 333], ["length", "stop", "abort"]),
    ),
    "exhausted_after_three": (
        [
            _completion(BAD, 111, "abort"),
            _completion(BAD, 222, "length"),
            _completion(BAD, 333, "stop"),
        ],
        2,
        ([111, 222, 333], ["abort", "length", "stop"]),
    ),
    "gateway_error_on_the_third": (
        [_completion(BAD, 111, "length"), _completion(BAD, 222, "stop"), _http_400()],
        2,
        ([111, 222, None], ["length", "stop", None]),
    ),
    "timeout_on_the_second_of_three": (
        [_completion(BAD, 111, "length"), TimeoutError("timed out")],
        2,
        ([111, None], ["length", None]),
    ),
}


@pytest.mark.parametrize(
    ("replies", "retries", "expected"),
    list(_THREE_ATTEMPTS.values()),
    ids=list(_THREE_ATTEMPTS),
)
def test_every_attempt_keeps_its_slot_at_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
    replies: list[object],
    retries: int,
    expected: tuple[list, list],
) -> None:
    """시도 둘까지만 재던 핀은 **합**만 단언해 사유 배열의 잘림·물려받기·앞 칸 누락을 못 잡았다.

    `Thresholds.schema_retries`는 1보다 크게 둘 수 있고 문서는 「길이 = `attempts`」가 일반으로
    선다고 적는다. 여기는 셋(또는 셋째 전의 타임아웃)에서 **배열을 통째로** 단언한다: 길이, 순서,
    응답 없는 칸이 `0`이나 앞 칸의 사유가 아닌 `None`인 것."""
    _serve(monkeypatch, replies)

    outcome, data = _run(_client(retries=retries))

    tokens, reasons = _breakdown(data)
    assert (tokens, reasons) == expected
    assert len(tokens) == len(reasons) == data["attempts"] == outcome.attempts


# --- 6d. 서버가 `completion_tokens`를 안 실은 시도는 `None`이다 ---------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        _completion(GOOD, None, "stop"),
        _completion(GOOD, None, "stop", usage=False),
    ],
    ids=["completion_tokens_absent", "usage_object_absent"],
)
def test_a_server_that_omits_completion_tokens_leaves_the_slot_unmeasured(
    monkeypatch: pytest.MonkeyPatch, reply: bytes
) -> None:
    """「서버가 안 줬다」와 「0이라 보고했다」는 한 값(`0`)으로 합쳐지면 안 된다 (CLAUDE.md §0).

    합산 `Usage`는 음이 아닌 정수라 `None`을 못 들고 어댑터가 0을 채운다 — 원장이 읽는 그 값은
    그대로다. 칸은 지어내지 않는다: `None`이다. `finish_reason`은 따로 실렸으므로 그대로 남는다."""
    _serve(monkeypatch, [reply])

    outcome, data = _run(_client())

    assert outcome.value == Tiny(ok=True)  # 멀쩡한 200은 이것 때문에 오류가 되지 않는다
    assert outcome.attempt_log == (AttemptRecord(output_tokens=None, finish_reason="stop"),)
    assert outcome.usage.output_tokens == 0  # 합산이 채운 0 — 원장 입력은 그대로
    assert _breakdown(data) == ([None], ["stop"])
    assert data["output_tokens"] == 0


def test_a_reported_zero_is_a_measured_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """위의 반대편 — 서버가 `completion_tokens: 0`을 **실으면** 칸은 `None`이 아니라 `0`이다."""
    _serve(monkeypatch, [_completion(GOOD, 0, "stop")])

    outcome, data = _run(_client())

    assert outcome.attempt_log == (AttemptRecord(output_tokens=0, finish_reason="stop"),)
    assert _breakdown(data) == ([0], ["stop"])


def test_the_sum_rule_holds_when_an_attempt_is_unreported(monkeypatch: pytest.MonkeyPatch) -> None:
    """`output_tokens == Σ attempt_output_tokens`(`null` 제외)는 `null`이 섞여도 선다.

    `null` 칸은 합에 0을 더한 시도이기 때문이다(`trace.py` 규칙 3). 문서가 적은 등식이 이 경우에도
    실제로 맞는지 본다 — 안 맞으면 규칙 3이 거짓이다."""
    _serve(monkeypatch, [_completion(BAD, None, "length"), _completion(GOOD, 12, "stop")])

    outcome, data = _run(_client(retries=1))

    tokens, reasons = _breakdown(data)
    assert (tokens, reasons) == ([None, 12], ["length", "stop"])
    assert data["output_tokens"] == sum(t for t in tokens if t is not None) == 12


# --- 6e. 규칙 밖의 응답·예외가 흐름을 안 바꾼다 -------------------------------------------------


def test_a_null_content_is_retried_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """`message.content`가 `null`인 응답은 기존대로 `ValidationError` → 재시도로 흐른다.

    `_post`의 반환 기록(`PostedResponse`)이 필드를 **검증하면** 이 `None`이 `_post` 밖으로 생 예외가
    돼 재시도 없이 호출이 죽는다. 1차 칸이 `length`와 토큰으로 남는지도 함께 본다."""
    _serve(monkeypatch, [_completion(None, POV_CAP, "length"), _completion(GOOD, 12, "stop")])

    outcome, data = _run(_client(retries=1))

    assert outcome.value == Tiny(ok=True)
    assert outcome.attempts == 2
    assert _breakdown(data) == ([POV_CAP, 12], ["length", "stop"])


@pytest.mark.parametrize(
    "usage_json",
    [
        '{"prompt_tokens": 100, "completion_tokens": 1e999}',
        '{"prompt_tokens": 100, "completion_tokens": Infinity}',
        '{"prompt_tokens": 1e999, "completion_tokens": 5}',
        '{"prompt_tokens": 100, "completion_tokens": 5,'
        ' "prompt_tokens_details": {"cached_tokens": 1e999}}',
    ],
    ids=["completion_1e999", "completion_infinity", "prompt_1e999", "cached_1e999"],
)
def test_a_usage_that_overflows_int_is_a_gateway_error_that_keeps_the_earlier_breakdown(
    monkeypatch: pytest.MonkeyPatch, usage_json: str
) -> None:
    """서버가 `usage`에 `1e999`·`Infinity`를 실으면 `int()`가 `OverflowError`를 던진다.

    `json.loads`가 둘 다 `inf`로 읽고 `_usage_from`의 `int(inf)`가 그 예외를 낸다 — 서버 응답
    하나로 닿는다. `ValueError`가 아니라서 `_post`의 모양 오류 `except`에 안 걸리면 생 예외로
    `complete()`를 뚫고, **이미 잰** 1차 시도(`length`, 8,192토큰)의 내역을 `attempt_log`째
    잃는다 — 이 계측이 막으려는 손실 그대로다. 다른 모양 오류와 같이 `ModelGatewayError`로
    와야 하고, 1차의 칸은 살아 있고 2차 칸은 응답을 못 받은 시도라 `None`이다."""
    _serve(
        monkeypatch,
        [_completion(TRUNCATED, 8192, "length"), _completion_with_usage_literal(usage_json)],
    )

    outcome, data = _run(_client(retries=1))

    assert isinstance(outcome, ModelGatewayError)
    assert "OverflowError" in data["error_detail"]
    assert outcome.attempts == 2
    assert outcome.attempt_log == (
        AttemptRecord(output_tokens=8192, finish_reason="length"),
        AttemptRecord(),
    )
    assert data["ok"] is False
    assert _breakdown(data) == ([8192, None], ["length", None])
    assert data["output_tokens"] == 8192


_RAW_ESCAPES = {
    # 스키마 검증기가 던진 `TypeError` — pydantic 2.13.4는 검증기의 `ValueError`·`AssertionError`만
    # `ValidationError`로 감싼다(`TypeError`·`KeyError`·`RuntimeError`·`ZeroDivisionError`·
    # `OverflowError`·`IndexError`는 안 감싸이는 것을 쟀다. 그 밖의 형은 재지 않았다).
    "validator_typeerror": ([_completion(GOOD, 7, "stop")], _Explodes, TypeError),
    # 서버가 보낸 100,000단 중첩 JSON — `json.loads`의 `RecursionError`는 `ValueError`가 아니라
    # `_post`의 어느 `except`에도 안 걸린다. **응답 하나로 닿는다.** 1차는 측정된 `length`다.
    "deeply_nested_response": (
        [_completion(BAD, 7, "length"), _deeply_nested_body()],
        Tiny,
        RecursionError,
    ),
}


@pytest.mark.parametrize(
    ("replies", "schema", "expected_exception"),
    list(_RAW_ESCAPES.values()),
    ids=list(_RAW_ESCAPES),
)
def test_a_raw_exception_after_a_measured_attempt_loses_the_breakdown(
    monkeypatch: pytest.MonkeyPatch,
    replies: list[object],
    schema: type[BaseModel],
    expected_exception: type[Exception],
) -> None:
    """**알려진 손실을 있는 그대로 고정한다** — `trace.py` 읽는 규칙 2의 (나).

    어댑터가 안 잡는 예외가 `complete()`를 뚫고 나가면 그 예외에는 `attempt_log`가 안 실려 있다.
    시도는 **이미 측정됐는데** 이벤트는 null/null이라 「어댑터가 안 실었다」와 똑같이 읽힌다.
    **닿을 길이 없다는 말이 아니다** — 이 파일의 앞 판은 그렇게 적었다가 틀렸다: 검증기가
    `TypeError`를 던지는 스키마(지금 없다)뿐 아니라 **서버 응답만으로** 닿는 길이 있다(깊게 중첩된
    JSON의 `RecursionError`). 처리하는 예외 집합 밖의 형은 이렇게 잃는다 — 예외 처리를 이
    변경에서 안 건드린다. 누가 이 손실을 고치면 이 핀과 `trace.py` 규칙 2를 **같이** 고친다."""
    _serve(monkeypatch, replies)

    outcome, data = _run(_client(retries=1), schema=schema)

    assert isinstance(outcome, expected_exception)
    assert data["ok"] is False
    assert data["attempt_output_tokens"] is None
    assert data["attempt_finish_reasons"] is None


def test_an_assertion_error_from_a_validator_is_wrapped_and_keeps_the_breakdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """위 손실의 경계 한쪽 — 검증기의 `AssertionError`는 pydantic이 감싸 `SchemaParseError`가 된다.

    그래서 「검증기가 던진 `ValueError`가 아닌 예외는 샌다」는 문장은 거짓이었다(`TypeError`는 새고
    `AssertionError`는 안 샌다). 감싸이면 재시도하고 소진해 `attempt_log`를 든다."""

    class _Asserts(BaseModel):
        ok: bool

        @field_validator("ok")
        @classmethod
        def _boom(cls, value: bool) -> bool:  # noqa: ARG003
            raise AssertionError("검증 실패")

    _serve(monkeypatch, [_completion(GOOD, 7, "length"), _completion(GOOD, 9, "stop")])

    outcome, data = _run(_client(retries=1), schema=_Asserts)

    assert isinstance(outcome, SchemaParseError)
    assert outcome.attempt_log == (
        AttemptRecord(output_tokens=7, finish_reason="length"),
        AttemptRecord(output_tokens=9, finish_reason="stop"),
    )
    assert _breakdown(data) == ([7, 9], ["length", "stop"])


# --- 7. 안 실은 호출은 키가 있고 값이 None이다 --------------------------------------------------


class _Raises:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201, ARG002
        raise self._exc


_SPENT = Usage(input_tokens=1, output_tokens=2, cached_tokens=0)


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("소비를 안 든 예외"),
        SchemaParseError("`attempt_log`를 안 싣는 옛 어댑터", usage=_SPENT),
    ],
    ids=["exception_without_spend", "parse_error_without_a_breakdown"],
)
def test_a_failure_that_carries_no_breakdown_has_the_keys_with_none(exc: Exception) -> None:
    """키 집합은 성공·실패 행이 같다(`call_model` 주석 F10) — 빈 목록 `"[]"`도, 0도 아니다."""
    outcome, data = _run(_Raises(exc))

    assert outcome is exc
    assert data["attempt_output_tokens"] is None
    assert data["attempt_finish_reasons"] is None


def test_a_role_without_a_model_is_a_real_failure_that_carries_no_breakdown() -> None:
    """`trace.py` 규칙 2 (가)의 예가 실재한다 — 프로필에 없는 역할을 부르면 `UnknownRoleError`가
    `models.complete()` 안에서 나 `model_call` 실패 행이 생기고, 그 예외는 `attempt_log`가 없다.

    (`BudgetExhausted`는 이 자리의 예가 아니다 — `ledger.ensure`가 `try` 밖이라 이벤트가 안 난다.
    `_run`이 `model_call` 이벤트 **하나**를 단언하므로 그것이 났다면 여기서 안다.)"""
    outcome, data = _run(RoutingModelClient({}))

    assert isinstance(outcome, UnknownRoleError)
    assert data["ok"] is False
    assert data["attempt_output_tokens"] is None
    assert data["attempt_finish_reasons"] is None


def test_a_budget_stop_emits_no_model_call_event_at_all() -> None:
    """위 docstring의 괄호를 문다 — `BudgetExhausted`는 null/null 행의 예가 아니라 행이 없다.

    `trace.py`·이 파일 머리가 한때 그것을 null/null 예로 적었다(`context.py`의 옛 주석을 베낀 것).
    `ledger.ensure(stage)`가 `try` 밖에서 던지므로 `model_call`은 한 건도 안 난다."""
    ctx = _ctx(FakeModelClient())
    ctx.ledger.charge(
        Usage(input_tokens=10_000_000, output_tokens=0, cached_tokens=0), stage=Stage.SCOUT
    )

    with pytest.raises(BudgetExhausted):
        ctx.call_model(role=Role.SCOUT, prompt=PROMPT, schema=Tiny, stage=Stage.SCOUT)

    kinds = {event.kind for event in ctx.trace.events}
    assert EventKind.MODEL_CALL not in kinds
    assert EventKind.BUDGET in kinds  # 막았다는 기록은 `budget` 이벤트에 있다


def _fake_prompt() -> Prompt:
    context = FakeContext(
        run_id=RUN_ID,
        ids={"candidate_id": "cand_0001"},
        artifact_id="art_0001",
        target=Location(file="parser.c"),
    )
    body = "분석하라.\n\n```json\n" + context.model_dump_json() + "\n```"
    return Prompt(prefix="SYSTEM", body=body, prompt_version="v1")


def test_the_fake_reports_no_breakdown_by_default() -> None:
    """Fake는 종료 사유를 재지 않았다 — 지어낸 `stop`이나 추정 토큰을 칸에 채우지 않는다."""
    result = FakeModelClient().complete(role=Role.SCOUT, prompt=_fake_prompt(), schema=Candidate)

    assert result.attempt_log == ()


def test_the_fake_can_script_a_truncated_multi_attempt_call_through_routing() -> None:
    """GPU 없이 `length`와 여러 시도를 만들 수 있다 — 라우팅이 그것을 그대로 통과시킨다."""
    script = (
        AttemptRecord(output_tokens=8192, finish_reason="length"),
        AttemptRecord(output_tokens=12, finish_reason="stop"),
    )
    fake = FakeModelClient(scripted_attempts=script)
    routed = RoutingModelClient({"scout": fake})

    result = routed.complete(role=Role.SCOUT, prompt=_fake_prompt(), schema=Candidate)

    assert result.attempt_log == script
    assert result.attempts == 2
    assert result.usage.output_tokens == 8192 + 12  # 합은 쪼갠 값에서 나온다

    outcome, data = _run(routed, schema=Candidate, prompt=_fake_prompt())  # call_model 경로
    assert outcome.attempt_log == script
    assert _breakdown(data) == ([8192, 12], ["length", "stop"])


# --- 15. run 끝 집계 둘 (#613·#618) — 분모만, 행이 없으면 `None` -------------------------
#
# 아래는 `call_model`이 **실제로 남기는** `model_call` 행에서 센다(필드 이름을 손으로 짓지 않는다).
# 빈 경우의 `None`을 `0`·`{}`로 바꾸면 `is None` 단언들이 빨개진다.


class _Script:
    """호출마다 대본 한 칸 — 예외면 던지고, 정수면 그 시도 수로 성공한다."""

    def __init__(self, *steps: object) -> None:
        self._steps = list(steps)

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201, ARG002
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return ModelResult(
            value=Tiny(ok=True), usage=_SPENT, model="m", attempts=step, gpu_ms=1,
            gpu_ms_source="request_wall_clock",
        )


def _drive_calls(*calls: tuple[Role, str, object]) -> list:
    """(역할, 대상, 대본 칸)을 차례로 `call_model`에 태우고 trace 이벤트를 돌려준다."""
    ctx = _ctx(_Script(*(step for _, _, step in calls)))
    for role, subject, _ in calls:
        try:
            ctx.call_model(
                role=role, prompt=PROMPT, schema=Tiny, stage=Stage.SCOUT,
                subject_kind="finding", subject_id=subject,
            )
        except Exception:  # noqa: BLE001 - 실패 행이 이 테스트의 입력이다
            pass
    return ctx.trace.events


def _bad() -> SchemaParseError:
    return SchemaParseError("스키마 소진", usage=_SPENT)


def _refused() -> ModelGatewayError:
    return ModelGatewayError("거절", status=400, usage=_SPENT)


def test_no_model_call_rows_means_unmeasured_not_zero() -> None:
    assert schema_failure_max_run_by_role([]) is None
    assert schema_recovered_calls_by_role([]) is None
    other = TraceWriter(RUN_ID, clock=lambda: NOW)
    other.emit(EventKind.STAGE_START, stage=Stage.SCOUT)  # model_call이 아닌 행만 있다
    assert schema_failure_max_run_by_role(other.events) is None
    assert schema_recovered_calls_by_role(other.events) is None


def test_the_longest_run_of_schema_failures_is_counted_per_role_and_subject() -> None:
    a, s = Role.AUDITOR, Role.SCOUT
    events = _drive_calls(
        (a, "f1", _bad()), (a, "f1", _bad()), (a, "f1", _bad()), (a, "f1", 1), (a, "f1", _bad()),
        (s, "c1", 1),
    )

    # 3연속이 성공에 끊기고 1이 뒤따른다 -> 최장 3. 실패가 없던 역할은 잰 0이다.
    assert schema_failure_max_run_by_role(events) == {"auditor": 3, "scout": 0}


def test_a_gateway_refusal_or_a_success_breaks_the_run() -> None:
    a = Role.AUDITOR
    events = _drive_calls((a, "f1", _bad()), (a, "f1", _refused()), (a, "f1", _bad()))

    assert schema_failure_max_run_by_role(events) == {"auditor": 1}


def test_failures_of_different_subjects_are_not_chained() -> None:
    a = Role.AUDITOR
    events = _drive_calls((a, "f1", _bad()), (a, "f2", _bad()), (a, "f1", _bad()))

    # f1 두 번(사이에 f2) -> 2, f2는 1. 대상 둘의 실패를 이어 3으로 세지 않는다.
    assert schema_failure_max_run_by_role(events) == {"auditor": 2}
    separate = _drive_calls((a, "f1", _bad()), (a, "f2", _bad()))
    assert schema_failure_max_run_by_role(separate) == {"auditor": 1}


def test_recovered_calls_count_only_successes_that_needed_a_retry() -> None:
    a, c = Role.AUDITOR, Role.CRITIC
    events = _drive_calls(
        (a, "f1", 2), (a, "f2", 1), (a, "f3", 3), (a, "f4", _bad()), (c, "f1", 1)
    )

    # 소진으로 끝난 실패 행은 회복이 아니다. 회복이 없던 역할은 잰 0이다.
    assert schema_recovered_calls_by_role(events) == {"auditor": 2, "critic": 0}
