"""OpenAICompatModelClient — vLLM의 OpenAI-compatible 서버에 붙는 ModelClient.

model-serving.md §6: 도메인 로직은 `ModelClient` 인터페이스에만 의존한다. vLLM·LiteLLM
지식은 이 파일 밖으로 새지 않는다.

의존성은 stdlib `urllib.request`뿐이다. HTTP 클라이언트 하나 때문에 requirements를
늘리지 않는다 — 요청 형태가 JSON POST 하나뿐이라 얻는 게 없다.

**스키마 파싱 실패는 지표다.** `retries` 한도까지 재요청하고(agent-contracts.md §1)
초과하면 `SchemaParseError`를 던진다. 오케스트레이터가 이를
`ReasonCode.SCHEMA_PARSE_FAILED`로 기록한다. 여기서 기본값을 반환해 삼키면
"모델별 구조화 출력 실패율"(evaluation.md §5.1)이 영원히 0이 된다.
"""

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from src.bench_metrics import generation_tokens
from src.interfaces.model_client import (
    MAX_ERROR_DETAIL,
    AttemptRecord,
    ChatTurn,
    ModelGatewayError,
    ModelResult,
    Prompt,
    ReasoningField,
    SchemaParseError,
    Usage,
    truncate_detail,
)
from src.schemas.enums import Role

_TIMEOUT_S = 300

# 연결 단계에서 끊긴 요청을 다시 보내기 전의 대기(초). 터널(`tools/serve/vast-tunnel.sh --loop`)이
# ssh 단절 5초 뒤 다시 붙는 것과 ssh 재접속을 덮는 값이다. 합 185초는 계산·추정이고 실측이 아니다.
_RECONNECT_WAITS_S = (5, 10, 20, 30, 60, 60)
# 이것들만 재시도한다 — 응답이 없었으니 서버가 만든 토큰이 없다. `HTTPError`(상태 있음)·
# `TimeoutError`(`generating` 판정)·디코드 오류·그 밖 `OSError`는 아니다.
_CONNECTION_DROPS = (
    ConnectionRefusedError,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    http.client.RemoteDisconnected,
)


def _is_connection_drop(exc: ModelGatewayError) -> bool:
    """`_post`가 올린 오류의 원인이 연결 단절인가 — 직접이든 `URLError.reason`이든."""
    cause = exc.__cause__
    if isinstance(cause, urllib.error.HTTPError):
        return False
    if isinstance(cause, urllib.error.URLError):
        cause = cause.reason
    return isinstance(cause, _CONNECTION_DROPS)


@dataclass(frozen=True)
class PostedResponse:
    """`_post` 한 번이 서버에서 받은 것 — 위치로 푸는 튜플이 아니라 **이름으로 읽는** 기록 (#550).

    `_post`가 `(content, usage)` 튜플일 때 `finish_reason`을 더하려고 셋으로 늘리면, 그것을
    `raw, usage = client._post(payload)`로 푸는 프로브 둘(`tools/probe/`)이 전 호출에서
    `ValueError`를 맞는다. 둘 다 `except Exception` 안이라 GPU가 이미 쓴 생성이
    `{"error": …}`로 버려진다 — #550 작업 중 테스트로 재현(`tests/test_probe.py`), 실서빙 관측
    아님. 필드를 이름으로 읽으면 하나가 더 늘어도 그 자리가 안 깨진다.

    `pydantic` 모델이 아니라 dataclass인 것은 **검증이 없어야 해서다.** 서버가
    `message.content`를 `null`로 줄 수 있고(OpenAI 호환 응답이 허용하며 vLLM 이슈에 그 모양의
    보고가 있다 — `docs/research/2026-09-08-large-moe.md`의 #37030, **[외부 보고 — 이 저장소의
    실서빙 관측으로는 확인하지 못했다]**), 그 `None`이 `complete()`의 `model_validate_json`까지
    흘러 `ValidationError` → 재시도로 이어지는 것이 기존 동작이다. 필드 검증을 달면 그 응답이
    재시도 대신 `_post` 밖으로 생 예외가 된다(`test_a_null_content_is_retried_not_raised`).
    """

    # **`None`일 수 있다** — 위 단락의 `null` 응답이다. `complete()`는 그것을 재시도로 흘린다.
    # 프로브가 이 값을 `str`로 믿고 `.rstrip()`·`len()`을 부르면 죽는다 — `tools/probe/`는
    # `None`이면 `error` 행으로 남긴다.
    content: str | None
    usage: Usage
    # `choices[0].finish_reason` 그대로. 못 읽으면 `None` — `_finish_reason_from`.
    finish_reason: str | None
    # 서버가 `usage.completion_tokens`를 **실었는가.** `usage.output_tokens`는 안 실렸을 때도
    # `0`이다(`_usage_from`이 0을 채우고 `Usage`는 `None`을 못 든다). 「안 줬다」와 「0이라
    # 보고했다」를 가르는 것은 이 값뿐이고, `complete()`가 `AttemptRecord.output_tokens`를
    # `None`으로 두는 근거다.
    output_tokens_reported: bool
    # `message.reasoning`/`message.reasoning_content` — `_reasoning_from`. 키가 없거나 문자열이
    # 아니면 `None`(`""`와 다르다), 실어 온 키 이름은 `reasoning_field`.
    reasoning: str | None = None
    reasoning_field: ReasoningField | None = None


# HTTP 오류 응답 본문에서 읽어 둘 최대 바이트. 서버가 거대한 본문을 주는 경우를 막는다.
# `_MAX_ERROR_DETAIL`이 그것을 다시 문자 단위로 자른다.
_MAX_ERROR_BODY = 8192

# 재시도 프롬프트에 덧붙이는 **스키마 오류 문자열**의 상한(문자).
#
# **막는 것은 원문이 아니라 오류 개수다.** 원문이 실리는 축은 PR #49가
# `include_input=False`로 닫았고(아래 `complete()`의 주석), 이 상한은 그것과 직교하는
# 두 번째 축을 닫는다 — `exc.errors()`는 리스트라 스키마 위반 원소마다 한 항목이 붙고
# 그 길이에 상한이 없었다.
#
# **수는 여기 적지 않는다. 테스트가 잰다.**
# `tests/test_openai_compat.py::test_the_error_storm_is_the_size_the_bound_is_justified_by`가
# 이 상한을 정당화하는 넷을 그 자리에서 다시 재고, 상수로 들고 있다. 주석에만 뒀던 판은
# **재현 불가능한 수를 세 자리가 서로 베껴 적고 있었다**(§0) — 그 판의 `116,367`은
# `errors()`의 어느 플래그 조합으로도 안 나오는 값이었다.
#
# 그 테스트가 고정하는 부등식 둘이 이 값의 근거다. (2026-09-12, pydantic 2.13.4)
#
# 1. **폭주는 걸린다.** 원소 500개가 전부 타입 위반인 응답의 오류 문자열이 61,890자로
#    이 상한의 31배이고 원문(8,011자)의 7.7배다. PR #49가 닫은 경로를 **재현하면** 붙는
#    문자열이 25,223자인데(run A 보고서 §5의 `재현:` 블록 — 그 run에서 **관측된** 것은
#    `input_tokens ≥ 24,577`이고 25,223은 사후 재현 스크립트의 값이다) 그 2.5배다.
#    **원문을 뺐어도 같은 자리로 되돌아온다.**
# 2. **관측된 경로는 안 걸린다.** 절단 응답(`json_invalid`, 이 저장소가 실제로 만난
#    #79의 모양)의 오류 문자열이 178자, 원소 하나짜리 타입 위반이 122자다. 11배 넘는
#    여유를 두고도 1이 성립한다.
#
# **그래서 왜 하필 2,000인가.** 위 둘은 부등식이라 [178, 61,889] 전 구간에서 성립한다 —
# 값을 정하는 것은 그 둘이 아니라 **관측된 경로를 안 건드린다**는 요구다. 관측 최대(178)의
# 10배 위 눈금이 2,000이고, 그 위 눈금(5,000·10,000)은 폭주에 대한 여유를 줄이면서 얻는
# 것이 없다. **정밀한 값이 아니라 안전한 눈금이고, 그 사실을 여기 적어 둔다**(§0).
#
# 규모 감각으로는 약 900토큰인데(`config/budgets.yaml`의 chars/token 2.224), **그 배율은
# 구성 의존값이라 근거로 세지 않는다**(#96) — 근거는 위 178·122다.
#
# `MAX_ERROR_DETAIL`(512)과 값을 공유하지 않는다. 저쪽은 trace JSONL 한 줄에 실리는
# **사람이 읽을 사유**이고 이쪽은 **모델에게 보내는 재시도 입력**이라, 잘라야 하는
# 이유도 적정선도 다르다.
RETRY_ERROR_MAX_CHARS = 2000


def _bounded_error_text(errors: object) -> str:
    """재시도에 실을 오류 문자열. 상한을 넘으면 **머리와 꼬리를 남기고** 가운데를 접는다.

    `truncate_detail`을 안 쓴다. 저쪽은 머리만 남기는데, 오류 리스트의 머리만 보면
    재시도하는 모델도 나중에 `retry_bodies`를 읽는 사람도 "여기까지가 전부"로 읽는다.
    **자른 것과 원래 짧은 것을 섞지 않는다**는 규약은 같고, 남기는 자리가 다르다.

    **머리를 꼬리보다 크게 잡는 사유는 `json_invalid`가 아니다.** 그렇게 적혀 있었는데
    (*"`json_invalid`가 첫 항목이라"*) 그 경로는 **여기까지 오지도 않는다** — 최상위 JSON이
    깨지면 그것이 **유일한** 오류라 문자열이 178자이고 위에서 조기 반환한다
    (`test_a_truncated_response_is_a_single_error_so_it_never_reaches_the_fold`).
    `include_context`를 끄지 않는 것이 그 신호(#79의 "EOF while parsing an object at line
    N")를 지키는 자리이고, 이 함수는 그 자리가 아니다.

    접기까지 오는 것은 **원소마다 한 항목이 붙는 오류 리스트**뿐이다. 거기서 진단이 되는
    것은 **어디서부터 어긋나기 시작했나**(앞쪽 `loc`)이고, 꼬리는 "리스트가 잘렸고 어디까지
    갔나"를 남기는 용도라 그만큼 작아도 된다. pydantic이 순서를 보장한다고 적힌 곳은 없으니
    **특정 항목이 머리에 온다는 가정 위에 서지 않는다** — 서는 것은 위치 편중뿐이다.
    """
    text = f"{errors}"
    if len(text) <= RETRY_ERROR_MAX_CHARS:
        return text
    # 표시도 상한 안에 넣는다 — 그래야 이 상수가 "덧붙는 것의 크기"를 실제로 묶는다.
    # 버린 글자 수는 **정확한 값**을 적어야 하는데 그 자릿수가 표시 길이를 바꾸므로,
    # 자리는 `len(text)`(버린 양의 상한)로 잡아 두고 수는 실제로 버린 값을 넣는다.
    # 자릿수가 상한보다 작으면 결과가 상한보다 짧아질 뿐 넘지 않는다.
    kept = RETRY_ERROR_MAX_CHARS - len(f"…(중략 {len(text)}자)…")
    head = kept * 2 // 3
    tail = kept - head
    return f"{text[:head]}…(중략 {len(text) - kept}자)…{text[-tail:]}"


class OpenAICompatModelClient:
    """`ModelClient` 구현. 역할 하나에 모델 하나가 대응한다."""

    def __init__(
        self,
        *,
        endpoint: str,
        model_name: str,
        temperature: float,
        top_p: float,
        seed: int,
        max_tokens: int,
        reasoning_effort: str | None = None,
        extra_params: Mapping[str, object] | None = None,
        timeout_s: float | None = None,
        retries: int = 1,
        api_key: str = "EMPTY",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        # 연결 재시도의 대기. 테스트가 실제로 자지 않게 주입한다(`RunContext.sleep`과 같은 관례).
        self._sleep = sleep
        self.model_name = model_name
        self.temperature = temperature
        self.top_p = top_p
        # 선언한 모델만 요청에 싣는다(`config.ModelEntry.reasoning_effort`). 기본값이
        # `None`인 것은 여기서는 "안 보낸다"이고, 그래야 이 파라미터를 모르는 서버가
        # 거절하지 않는다.
        self.reasoning_effort = reasoning_effort
        # registry가 모델별로 선언한 요청 파라미터(`config.ModelEntry.request_params()` —
        # 샘플링·`chat_template_kwargs`·`thinking_token_budget`). 기본 키 **위에** 얹으므로
        # 같은 키(`temperature`·`top_p`)면 그 모델에서 프로필 공통값을 덮는다. 안 주면 키 집합이
        # 그대로다 — `reasoning_effort`와 같은 규칙.
        self.extra_params = dict(extra_params or {})
        # 요청 하나의 벽시계 상한. `None`이면 `_TIMEOUT_S`. 좌석마다 생성 속도가 달라 값은
        # 모델 것이다(`ModelEntry.request_timeout_s`, #261).
        self.timeout_s = timeout_s
        # seed를 요청에 실어야 같은 조건 재실행이 같은 결과를 낸다(engineering-guide.md §5).
        self.seed = seed
        # **기본값을 두지 않는다.** 이 값이 payload에 없으면 서버가 `--max-model-len`까지
        # 생성한다 — 2026-08-27 ATOM에서 30B가 그렇게 폭주해 `_TIMEOUT_S`에 걸렸다.
        # 기본값을 두면 조립 지점이 조용히 config를 안 읽고 지나갈 수 있고, 그러면 실제로
        # 쓰인 상한과 `RunRecord.thresholds`에 적힌 값이 갈린다. `temperature`·`top_p`·`seed`
        # 가 필수 키워드인 것과 같은 이유다.
        self.max_tokens = max_tokens
        self.retries = retries
        # 온프레미스 vLLM은 인증을 쓰지 않지만 OpenAI 클라이언트 규약상 헤더가 필요하다.
        # 실제 키가 필요한 게이트웨이라면 환경변수에서 주입한다 — 소스에 두지 않는다.
        self.api_key = api_key

    def complete(
        self,
        *,
        role: Role,
        prompt: Prompt,
        schema: type[BaseModel],
        history: Sequence[ChatTurn] = (),
    ) -> ModelResult:
        last_error: ValidationError | None = None
        body = prompt.body
        # **재시도분까지 더한다.** 스키마 파싱에 실패한 응답도 서버는 이미 만들어 냈고
        # 그 GPU 시간과 토큰은 실제로 쓰였다. 마지막 시도만 세면 재시도가 잦은 모델일수록
        # 비용이 싸 보인다 — 조건 간 비교에서 정확히 거꾸로 가는 편향이다.
        #
        # **토큰도 같은 규칙이다.** 예전에는 `gpu_ms`만 누적하고 `usage`는 매 반복
        # 재바인딩돼 마지막 성공분만 반환됐다. 3회 시도(2실패+1성공)면 GPU는 3회 합인데
        # 토큰은 1회분이라, 위 주석이 GPU에서 없앤 편향이 토큰 쪽에 **거꾸로 되살아나
        # 있었다** — 재시도가 잦은 모델의 "토큰당 GPU"가 실제보다 커 보인다.
        #
        # 셋(`input`/`output`/`cached`)을 함께 더한다. `cached_tokens`는 `input_tokens`의
        # 부분집합이므로(`interfaces/model_client.py Usage`) prefix 캐시 비율
        # `sum(cached) / sum(input)`의 분자와 분모가 같은 축에서 커져야 한다 — 하나만
        # 누적하면 그 비율이 조용히 틀어진다. 각 항이 `cached <= input`을 만족하므로
        # 합도 만족한다.
        gpu_ms = 0
        spent = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)
        # 2~N차가 실제로 보낸 body. **보내는 시점에 담는다** — 스키마 오류 핸들러에서
        # 담으면 마지막 시도가 실패했을 때 한 번도 안 보낸 body가 끝에 붙는다(핸들러가
        # 다음 회차용 body를 만들고 루프가 소진되는 경우). 상위가 재시도 파일로 남긴다(#80).
        retry_bodies: list[str] = []
        # 시도별 (출력 토큰, 종료 사유) — 시도 순서대로 칸 하나씩(#550). 위 `spent`는 이 칸들의
        # 출력 토큰을 합친 값과 같다(합을 대체하지 않고 쪼갠 내역을 더할 뿐이다). 합이 못 푸는 것은
        # 상한과 그 2배 사이의 합에서 어느 시도가 닿았나, `length`·`stop` 밖의 사유, 그리고 추론이
        # 아닌 서버의 직접 확인이다(`AttemptRecord`) — 합 65,536처럼 상한의 2배면 두 시도가 정확히
        # 상한이라는 것까지는 산술로 이미 나온다.
        attempt_log: list[AttemptRecord] = []
        # 스키마 검증에 실패한 시도의 응답 원문 — 소진·게이트웨이 거절 때 예외에 실어 올린다(#603).
        raw_texts: list[str | None] = []
        attempt = 0
        for attempt in range(1, self.retries + 2):
            if attempt > 1:
                # 이 body는 직전 회차의 스키마 오류 핸들러가 만든 것이고, 지금 보내진다.
                retry_bodies.append(body)
            started = time.monotonic()
            # 연결 재시도가 잔 시간 — 요청 왕복이 아니라서 `gpu_ms`에서 뺀다.
            waited: list[float] = []
            try:
                posted = self._post_reconnecting(
                    self._request_payload(prompt.prefix, body, schema, history), waited
                )
            except ModelGatewayError as exc:
                # 게이트웨이 거절(컨텍스트 초과 400 등)은 재시도 대상이 아니라 그대로 올린다.
                # **이 시도가 태운 wall-clock을 먼저 누적한다.** 안 그러면 재보지도 않은 0에
                # `request_wall_clock` 라벨을 붙여 장부에 청구하게 된다 — 0은 "안 썼다"는 적극적
                # 진술이라(CLAUDE.md §0, `model_client.py`의 `gpu_ms` 주석) 그 라벨이 거짓이 되고,
                # 가장 비싼 실패(타임아웃, `_TIMEOUT_S`)가 장부에 0으로 잡힌다(#82 리뷰 F1).
                # 거절이라 응답 토큰은 없지만 요청 왕복 시간은 실재한다. 앞선 재시도가 쓴 것도
                # `spent`·`gpu_ms`에 함께 실어 상위(`context.call_model`)가 청구한다.
                gpu_ms += int((time.monotonic() - started - sum(waited)) * 1000)
                # 응답을 못 받은 시도도 칸을 지킨다 — `AttemptRecord()`는 전부 `None`이다.
                # 200이었으나 모양이 계약과 달라 온 예외는 읽을 수 있었던 추론을 든다.
                attempt_log.append(
                    AttemptRecord(reasoning=exc.reasoning, reasoning_field=exc.reasoning_field)
                )
                # 그 200의 원문(또는 추론)도 마지막 칸으로 올린다.
                # ⚠ **칸이 안 생기는 조건은 「`message`를 못 읽었다」가 아니다** — 아래 둘이
                # **같은** `(None, None)`이라 둘 다 칸이 없다: ① 읽을 `message`가 없던 응답
                # (`choices` 비었음 등) ② `message`는 읽혔으나 `content`가 비문자열이고 추론
                # 키도 없던 응답. ②는 스키마 실패 경로로 가면 `None` 칸이 생기므로 **두 경로의
                # 칸 규칙이 다르다**(2026-10-13 실측 - `_chat_body(None, usage="깨짐")`로 돌려
                # `raw_texts == ()`). 읽을 수 있었던 `finish_reason`도 이 갈래에선 `None`으로
                # 남는다(`AttemptRecord()`가 전부 `None`이라서다). 맞추지 않은 이유는 「읽혔다」를
                # 예외에 따로 실어야 해서고, 그 전까지 서술이 이 차이를 숨기지 않게 적어 둔다.
                if exc.raw_text is not None or exc.reasoning is not None:
                    raw_texts.append(exc.raw_text)
                exc.usage = spent
                exc.gpu_ms = gpu_ms
                exc.gpu_ms_source = "request_wall_clock"
                exc.attempts = attempt
                exc.retry_bodies = tuple(retry_bodies)
                exc.attempt_log = tuple(attempt_log)
                # 앞 시도가 스키마 실패였다면 그 원문도 올린다(#603).
                exc.raw_texts = tuple(raw_texts)
                raise
            gpu_ms += int((time.monotonic() - started - sum(waited)) * 1000)
            raw, usage = posted.content, posted.usage
            spent = _add(spent, usage)
            # 서버가 `completion_tokens`를 안 실었으면 합(`spent`)에는 0이 들어가지만 이 칸은
            # `None`이다. 합의 0이 「안 줬다」인지 「0이라 보고했다」인지는 이 칸만 안다
            # (`PostedResponse`).
            attempt_log.append(
                AttemptRecord(
                    output_tokens=usage.output_tokens if posted.output_tokens_reported else None,
                    finish_reason=posted.finish_reason,
                    reasoning=posted.reasoning,
                    reasoning_field=posted.reasoning_field,
                )
            )
            try:
                value = schema.model_validate_json(raw)
            except ValidationError as exc:
                last_error = exc
                # `content`가 `null`(또는 비문자열)이면 그 칸은 `None`이다 — 모델이 **빈 응답을
                # 낸 것이 아니라 받은 문자열이 없는 것**이라 `""`와 다르다(CLAUDE.md §0: 안 잰
                # 값을 잰 값으로 쓰지 않는다). 칸은 지켜 시도 순서와 정렬을 유지한다.
                raw_texts.append(raw if isinstance(raw, str) else None)
                # 재요청에는 스키마 오류 메시지만 덧붙인다(agent-contracts.md §1).
                # 이전 응답 원문을 다시 넣으면 같은 오류를 반복하게 만든다.
                #
                # **`include_input=False`가 그 두 줄을 실행하는 자리다 (PR #49).** pydantic의
                # `errors()`는 그 인자가 기본 `True`라, `json_invalid` 오류의 `input`에
                # 파싱에 실패한 **응답 원문 전체**가 실린다 — 위에서 안 넣겠다고 적은
                # 바로 그것이다. `include_url`만 끄고 이쪽을 안 껐던 것이 원인이라 두
                # 인자를 나란히 둔다.
                #
                # 2026-08-28 ATOM `run_20260828_0002`가 그 대가를 보였다. 8,192 토큰짜리
                # 공백 폭주가 escape되어(15,000자 → 25,223자) 재시도 프롬프트에 통째로
                # 실렸고, 입력이 `--max-model-len 32768 − output_max 8192 = 24,576`을 넘어
                # 게이트웨이가 400으로 거절했다. target 3건이 그렇게 떨어졌고 **그중 하나가
                # 그 대상의 정답 함수(`router_define`)다.** 상한을 실은 PR #36가 이 자리에서
                # 입력 초과로 되돌아온 셈이라, 상한 값을 어떻게 잡든 이 줄이 남아 있으면
                # 같은 일이 난다.
                #
                # **`include_context`는 끄지 않는다.** `ctx.error`가 "EOF while parsing an
                # object at line N"을 들고 있고, 그것이 스키마 위반과 출력 절단을 가르는
                # 유일한 단서다(#79). 재시도하는 모델도, 나중에 trace의 `error_detail`을
                # 읽는 사람도 그 문장으로 폭주를 알아본다.
                # ⏵ 2026-10-08 — 사람 쪽은 이제 유일하지 않다: `model_call.attempt_finish_reasons`가
                # 절단 여부를 서버의 말로 직접 싣는다(#550). 재시도하는 모델은 그것을 못 보므로
                # (재시도 본문에 안 실린다) 모델에게는 이 문장이 여전히 유일하다.
                #
                # **2026-09-12 — `include_input=False`가 닫은 축은 하나뿐이었다.** 그 인자는
                # **원문**이 실리는 축을 닫고, `errors()`가 리스트라는 데서 오는 **오류 개수**
                # 축은 열려 있었다. 원소 500개가 전부 타입 위반인 응답은 오류 문자열이
                # 61,890자다 — 위 문단의 25,223자(run A 보고서 §5의 **재현값**이고,
                # 그 run에서 관측된 것은 `input_tokens ≥ 24,577`이다)의 2.5배이고,
                # **원문을 뺐어도 같은 자리로 되돌아온다.** `_bounded_error_text`가 그 축을
                # 묶는다. 신호(`ctx.error`)는 머리에 남는다. 수의 출처는 그 상수 주석이
                # 가리키는 테스트다 — 여기 적힌 61,890도 거기서 재는 값이다.
                body = (
                    f"{prompt.body}\n\n[스키마 오류] "
                    f"{_bounded_error_text(exc.errors(include_url=False, include_input=False))}"
                )
                continue
            return ModelResult(
                value=value,
                usage=spent,
                model=self.model_name,
                gpu_ms=gpu_ms,
                gpu_ms_source="request_wall_clock",
                attempts=attempt,
                retry_bodies=tuple(retry_bodies),
                raw_text=raw,
                attempt_log=tuple(attempt_log),
                failed_raw_texts=tuple(raw_texts),
            )
        raise SchemaParseError(
            f"{role} 역할의 {self.model_name}이 {self.retries + 1}회 안에 "
            f"{schema.__name__}을 만들지 못했다: {last_error}",
            usage=spent,
            gpu_ms=gpu_ms,
            gpu_ms_source="request_wall_clock",
            attempts=self.retries + 1,
            retry_bodies=tuple(retry_bodies),
            attempt_log=tuple(attempt_log),
            # 마지막 시도의 오류를 `include_input=False`·`include_url=False`로 만든 텍스트 —
            # 모델에게 다시 보여도 되는 유일한 사유다(`str(exc)`는 응답 원문 조각과 URL을 든다).
            model_error_text=_bounded_error_text(
                last_error.errors(include_url=False, include_input=False)
            ),
            raw_texts=tuple(raw_texts),
        )

    def _request_payload(
        self,
        prefix: str,
        body: str,
        schema: type[BaseModel],
        history: Sequence[ChatTurn] = (),
    ) -> dict:
        """prefix는 system 메시지로 고정한다 — automatic prefix caching의 전제다.

        prefix와 가변부를 한 문자열로 뭉개면 캐시 적중률을 측정할 수도, 지킬 수도 없다
        (agent-contracts.md §4.2).

        **history는 system과 이번 turn(user) 사이에 순서대로 얹는다** (ADR 0012 결정 2).
        비어 있으면 메시지는 지금까지처럼 `[system, user]` 둘뿐이다. history가 있으면
        user 본문은 늘 마지막(`messages[-1]`)이다 — 재시도 회차에도 history는 그대로
        두고 이 `body`만 바뀐다(`complete()`가 넘기는 값이 매 시도 같은 `history`다).
        """
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": prefix},
                *({"role": turn.speaker, "content": turn.content} for turn in history),
                {"role": "user", "content": body},
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "seed": self.seed,
            # 출력 상한. 이것이 없으면 서버는 `--max-model-len`까지 생성할 수 있다 —
            # 정상 응답을 자르는 손잡이가 아니라 폭주 차단이다(8/26 실측 후보당 167토큰).
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            },
        }
        # **선언했을 때만 넣는다.** `None`을 실어 보내면 이 파라미터를 모르는 서버가
        # 거절할 수 있고, 성공 경로 payload의 키 집합이 모델마다 달라진다.
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        return {**payload, **self.extra_params}

    def _post_reconnecting(self, payload: dict, waited: list[float]) -> PostedResponse:
        """`_post`에 연결 단절 재시도를 건다 - 터널 순단 한 번에 run을 잃지 않으려는 것이다.

        단절은 응답 전이라 토큰이 안 들고 스키마 재시도(`retries`)도 안 센다. 잔 시간은 `waited`에
        쌓아 `complete()`가 `gpu_ms`에서 뺀다. 다 실패하면 같은 `ModelGatewayError`에 횟수를
        붙여 올린다.
        """
        for tried, wait in enumerate((*_RECONNECT_WAITS_S, None)):
            try:
                return self._post(payload)
            except ModelGatewayError as exc:
                if wait is not None and _is_connection_drop(exc):
                    slept = time.monotonic()
                    self._sleep(wait)
                    waited.append(time.monotonic() - slept)
                    continue
                if tried:
                    note = f" (연결 재시도 {tried}회 뒤에도 실패)"
                    exc.args = (f"{exc.args[0]}{note}",)
                    exc.detail = f"{exc.detail}{note}"
                raise
        raise AssertionError("unreachable")  # pragma: no cover - 마지막 wait가 None이다

    def _post(self, payload: dict) -> PostedResponse:
        """요청 한 번. `content`·`usage`·`finish_reason`을 `PostedResponse`로 돌려준다 (#550).

        `finish_reason`은 `choices[0].finish_reason` 그대로인 문자열이고, 키가 없거나 문자열이
        아니거나 비었으면 `None`이다(`_finish_reason_from`) — 멀쩡한 200을 이것 때문에 오류로
        만들지 않는다.
        """
        request = urllib.request.Request(  # noqa: S310 - 스킴은 config가 정한 http(s)다
            f"{self.endpoint}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        # 타임아웃이 「느림」인지 「멈춤」인지 가를 기준점. **요청 전에** 읽는다 — 타임아웃 뒤에는
        # 우리가 끊어 서버가 그 요청을 abort하므로, 그때 두 번 재면 느린 서버도 멈춘 것으로 보인다.
        # ponytail: 서버 전체 카운터라 같은 서버에 동시 요청이 있으면(ADR 0012 Phase 6.5의 단계 안
        # 동시 요청) 남의 생성도 「느림」으로 센다 — 요청별 판정이 필요해지면 스트리밍으로 첫 토큰
        # 이후 진행을 직접 본다.
        before = generation_tokens(self.endpoint)
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=_TIMEOUT_S if self.timeout_s is None else self.timeout_s
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # **`URLError`보다 먼저 잡는다.** `HTTPError`가 그 하위 클래스라 순서를
            # 바꾸면 400도 아래 절로 가고, 그러면 `exc.read()`를 부르는 자리가 없어
            # 서버가 말해 준 사유가 통째로 사라진다 — zlib에서 실제로 그랬다.
            detail = _error_detail(exc)
            raise ModelGatewayError(
                f"{self.endpoint} 호출 실패: HTTP {exc.code} {detail}",
                status=exc.code,
                detail=detail,
            ) from exc
        except TimeoutError as exc:
            # 상태 코드가 없다. 그 사이 서버가 토큰을 냈으면(`generating=True`) 느린 것이라
            # 러너가 이 요청 하나만 버린다. 못 쟀거나(`None`) 안 냈으면(`False`) 멈춘다(#261).
            after = generation_tokens(self.endpoint)
            generating = None if before is None or after is None else after > before
            raise ModelGatewayError(
                f"{self.endpoint} 호출 실패: {exc} (생성 중: {generating})",
                detail=f"{exc}",
                generating=generating,
            ) from exc
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            # 상태 코드가 없다 — 연결 자체가 안 됐거나 응답이 JSON이 아니다. 러너는
            # 이것을 서버 쪽 사건(`model_gateway_unavailable`)으로 읽고 즉시 멈춘다.
            raise ModelGatewayError(f"{self.endpoint} 호출 실패: {exc}", detail=f"{exc}") from exc
        except (OSError, http.client.HTTPException, UnicodeDecodeError) as exc:
            # ⏵ **2026-10-02 — 끊긴 연결이 여기까지 새고 있었다.** 위 셋만 잡던 동안 아래가
            # **생 예외로** 러너를 뚫고 나갔다:
            #   - `http.client.IncompleteRead`·`BadStatusLine` — `HTTPException`은 **`OSError`가
            #     아니다**(3.14.5에서 `issubclass`로 확인). 그래서 한 번도 안 잡혔다.
            #     **[추정 — 실서빙 관측 0건]** 서버가 응답 중간에 죽으면 이 모양일 것이다.
            #   - `ConnectionResetError`·`BrokenPipeError` 등 생 `OSError` — `URLError`로 감싸지지
            #     않고 올라오는 경로가 있다.
            #   - `UnicodeDecodeError` — 응답 바이트가 UTF-8이 아니면 `.decode`가 던진다
            #     (`ValueError`의 하위지만 `JSONDecodeError`와 **형제**라 그 절에 안 걸린다).
            # 🔴 **순서가 계약이다.** `HTTPError` ⊂ `URLError` ⊂ `OSError`이고 `TimeoutError`도
            # `OSError`의 하위라, 이 절이 **맨 뒤**여야 위 셋이 각자의 사유를 유지한다 — 올리면
            # 400도 여기로 와서 서버가 말해 준 사유가 사라진다(그 사고가 zlib에서 실제로 났다).
            # 형 이름을 문구에 싣는다 — `error_detail`은 형을 안 적어서 로그만 보면 무엇이
            # 끊겼는지 모른다.
            raise ModelGatewayError(
                f"{self.endpoint} 호출 실패: {type(exc).__name__}: {exc}",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            # 추론 채널은 서버가 분리했을 때만 있다(`--reasoning-parser`). 없어도 오류가 아니다.
            reasoning, reasoning_field = _reasoning_from(choice["message"])
            # `_usage_from`도 **같은 try 안이다**(적대적 리뷰 2026-10-02 실측): `usage: null`이면
            # `AttributeError`, `completion_tokens: null`이면 `TypeError`, `"abc"`면 `ValueError`가
            # 샜다. 모양이 깨진 응답은 content든 usage든 **한 사건**이다.
            # **`1e999`·`Infinity`는 `OverflowError`다**(2026-10-08 실측) — `json.loads`가 둘 다
            # `inf`로 읽고 `int(inf)`가 그 예외를 던진다. 서버 응답 하나로 닿고, 아래 except에
            # 없으면 이미 잰 시도의 내역(`attempt_log`)째 `complete()`를 뚫고 나간다(#550).
            usage_data = data.get("usage", {})
            usage = _usage_from(usage_data)
            # `_usage_from`이 위에서 `.get`을 통과했으므로 `usage_data`는 매핑이다 — `in`은 안
            # 던진다.
            output_tokens_reported = "completion_tokens" in usage_data
            # `choice`가 위에서 `["message"]`를 통과했으므로 매핑이다 — `.get`은 안 던진다.
            finish_reason = _finish_reason_from(choice)
        except (KeyError, IndexError, TypeError, AttributeError, ValueError, OverflowError) as exc:
            # 응답이 200인데 **모양이 다르다**(`choices`가 비었거나 키가 없다). 전 판은 이 인덱싱이
            # try 밖이라 `KeyError`가 그대로 새 나갔고, 러너는 그것을 게이트웨이 사건으로 못 읽어
            # `write_manifest` 앞을 뚫었다. 상태 코드는 없다 — 전송은 됐고 **계약이 깨진** 것이다.
            # ⚠ 러너는 상태 코드가 없으면 이것도 `model_gateway_unavailable`로 적는다(끊김·
            # 타임아웃과 같은 사유 코드). **`detail`에 문구를 싣는 이유가 그것이다** — 둘을 가르는
            # 것은 trace의 `model_call.error_detail`뿐이고 그것은 `detail`을 읽는다.
            reason = f"응답 모양이 계약과 다르다: {type(exc).__name__}: {exc}"
            # 그래도 읽을 수 있는 `message`가 있으면 원문·추론은 잃지 않는다(`usage`만 깨진 200이
            # 흔하다) — 진단하려는 것이 바로 이 응답이다.
            raw_text, reasoning, reasoning_field = _salvage_message(data)
            raise ModelGatewayError(
                f"{self.endpoint} {reason}",
                detail=reason,
                raw_text=raw_text,
                reasoning=reasoning,
                reasoning_field=reasoning_field,
            ) from exc
        return PostedResponse(
            content=content,
            usage=usage,
            finish_reason=finish_reason,
            output_tokens_reported=output_tokens_reported,
            reasoning=reasoning,
            reasoning_field=reasoning_field,
        )


def _reasoning_from(message: Mapping) -> tuple[str | None, ReasoningField | None]:
    """`message`에서 추론 텍스트와 그것을 실어 온 키 이름을 읽는다 (#280).

    vLLM은 버전에 따라 `reasoning`(0.27.1 ATOM 관측) 또는 `reasoning_content`로 싣는다 — 둘 다 보고
    **문자열인 첫 키**를 쓴다(둘 다면 `reasoning`). 파서가 추론을 못 냈을 때 흔한 `null`은 문자열이
    아니라 **없는 것**이다. `""`는 있는 것이다 — 빈 추론과 추론 없음을 한 값으로 합치지 않는다
    (CLAUDE.md §0).
    """
    for name in ("reasoning", "reasoning_content"):
        value = message.get(name)
        if isinstance(value, str):
            return value, name
    return None, None


def _salvage_message(data: object) -> tuple[str | None, str | None, ReasoningField | None]:
    """모양이 깨진 200에서 읽히는 만큼의 (원문, 추론, 추론 키)를 건진다. 못 읽으면 전부 `None`."""
    try:
        message = data["choices"][0]["message"]  # type: ignore[index]
        content = message.get("content")
        reasoning, reasoning_field = _reasoning_from(message)
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, None, None
    return (content if isinstance(content, str) else None), reasoning, reasoning_field


def _finish_reason_from(choice: dict) -> str | None:
    """`choices[0].finish_reason`을 문자열로, 못 읽으면 `None`으로 (#550).

    **`""`·`"unknown"`으로 채우지 않는다** — 안 잰 값을 그럴듯한 값으로 메우지 않는다(CLAUDE.md §0).
    **알려진 값(`stop`·`length` …)으로 거르지도 않는다.** 서버가 낸 처음 보는 사유(vLLM의 `abort`
    등)를 버리면 이 필드가 있는 이유 — 절단(`length`)과 그 밖을 가르는 것 — 가 사라진다.

    **공백뿐인 값도 `None`이다** — 읽을 사유가 없다. 길이와 무관하게 같다(600칸 공백이 `''`로
    나가던 것이 이 함수의 「`''`는 안 돌려준다」를 깼다).

    **길이는 묶는다.** 이 문자열은 trace JSONL 한 줄의 셀로 그대로 가는데 서버가 정한 값이라 상한이
    없다(2MB 값이 2MB 셀이 된다). 정상 길이(`MAX_ERROR_DETAIL` 이하)는 **한 글자도 안 바꾼다.**
    **원문이 그것을 넘으면** `error_detail`과 같은 자르기(`truncate_detail`)를 거치는데, 그것은
    **공백 연속을 한 칸으로 접고 앞뒤 공백을 뗀 뒤** 길이를 다시 잰다. 그래서 접은 결과가 아직
    `MAX_ERROR_DETAIL`을 넘을 때만 잘림 표시가 붙는다 — 공백이 대부분이라 접으면 들어가는 값은
    **표시 없이 짧아진다**(`length` 뒤에 공백 507칸이 붙은 값이 `length`가 된다).
    잃는 것은 공백뿐이다.
    """
    value = choice.get("finish_reason")
    if not isinstance(value, str) or not value.strip():
        return None
    return truncate_detail(value) if len(value) > MAX_ERROR_DETAIL else value


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """오류 응답 본문을 읽어 자른다. 못 읽어도 원 예외를 잃지 않는다.

    본문을 읽지 못하는 것(스트림이 이미 닫혔거나 소켓이 끊겼다)은 그 자체로는 사건이
    아니다 — 우리가 잃는 것은 상세뿐이고 상태 코드는 이미 있다. 그래서 여기서 예외를
    올리지 않고 사유를 문자열로 남긴다.
    """
    try:
        body = exc.read(_MAX_ERROR_BODY).decode("utf-8", errors="replace").strip()
    except (OSError, ValueError, http.client.HTTPException) as read_error:
        # `HTTPException`(chunked 본문이 끊기는 `IncompleteRead`)은 `OSError`가 아니다 — 프록시
        # 뒤 서버가 상태 줄 뒤에 죽으면 이 모양이고, 안 잡으면 상태까지 잃은 생 예외가 `_post`를
        # 빠져나간다.
        return f"(응답 본문을 읽지 못했다: {type(read_error).__name__})"
    return truncate_detail(body)


def _usage_from(usage: dict) -> Usage:
    """input/output/cached를 분리 기록한다 (evaluation.md §5.2).

    합계만 남기면 prefix cache의 효과 — 이 프로젝트의 핵심 비용 주장 — 을 검증할 수 없다.
    캐시 필드를 내려주지 않는 게이트웨이는 0으로 잡히고, 그 사실이 cache hit ratio 0으로
    드러난다.
    """
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
    return Usage(
        input_tokens=prompt_tokens,
        output_tokens=int(usage.get("completion_tokens", 0)),
        cached_tokens=min(cached, prompt_tokens),
    )


def _add(left: Usage, right: Usage) -> Usage:
    """두 호출의 토큰을 더한다. 재시도 누적 전용이다.

    `Usage`가 frozen이라 제자리 증가가 안 되고, 필드를 손으로 더하는 코드가 호출 지점에
    흩어지면 셋 중 하나를 빠뜨리는 것이 이 결함의 재발이다 — 더하는 자리를 하나로 둔다.
    """
    return Usage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cached_tokens=left.cached_tokens + right.cached_tokens,
    )
