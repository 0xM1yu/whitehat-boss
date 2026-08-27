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

import json
import time
import urllib.error
import urllib.request

from pydantic import BaseModel, ValidationError

from src.interfaces.model_client import (
    ModelGatewayError,
    ModelResult,
    Prompt,
    SchemaParseError,
    Usage,
    truncate_detail,
)
from src.schemas.enums import Role

_TIMEOUT_S = 300

# HTTP 오류 응답 본문에서 읽어 둘 최대 바이트. 서버가 거대한 본문을 주는 경우를 막는다.
# `_MAX_ERROR_DETAIL`이 그것을 다시 문자 단위로 자른다.
_MAX_ERROR_BODY = 8192


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
        retries: int = 1,
        api_key: str = "EMPTY",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.temperature = temperature
        self.top_p = top_p
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

    def complete(self, *, role: Role, prompt: Prompt, schema: type[BaseModel]) -> ModelResult:
        last_error: Exception | None = None
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
        for _ in range(self.retries + 1):
            started = time.monotonic()
            raw, usage = self._post(self._request_payload(prompt.prefix, body, schema))
            gpu_ms += int((time.monotonic() - started) * 1000)
            spent = _add(spent, usage)
            try:
                value = schema.model_validate_json(raw)
            except ValidationError as exc:
                last_error = exc
                # 재요청에는 스키마 오류 메시지만 덧붙인다(agent-contracts.md §1).
                # 이전 응답 원문을 다시 넣으면 같은 오류를 반복하게 만든다.
                body = f"{prompt.body}\n\n[스키마 오류] {exc.errors(include_url=False)}"
                continue
            return ModelResult(
                value=value,
                usage=spent,
                model=self.model_name,
                gpu_ms=gpu_ms,
                gpu_ms_source="request_wall_clock",
            )
        raise SchemaParseError(
            f"{role} 역할의 {self.model_name}이 {self.retries + 1}회 안에 "
            f"{schema.__name__}을 만들지 못했다: {last_error}"
        )

    def _request_payload(self, prefix: str, body: str, schema: type[BaseModel]) -> dict:
        """prefix는 system 메시지로 고정한다 — automatic prefix caching의 전제다.

        prefix와 가변부를 한 문자열로 뭉개면 캐시 적중률을 측정할 수도, 지킬 수도 없다
        (agent-contracts.md §4.2).
        """
        return {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": prefix},
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

    def _post(self, payload: dict) -> tuple[str, Usage]:
        request = urllib.request.Request(  # noqa: S310 - 스킴은 config가 정한 http(s)다
            f"{self.endpoint}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            # 상태 코드가 없다 — 연결 자체가 안 됐거나 응답이 JSON이 아니다. 러너는
            # 이것을 "서버가 죽었다"로 읽고 즉시 멈춘다.
            raise ModelGatewayError(f"{self.endpoint} 호출 실패: {exc}", detail=f"{exc}") from exc
        return data["choices"][0]["message"]["content"], _usage_from(data.get("usage", {}))


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """오류 응답 본문을 읽어 자른다. 못 읽어도 원 예외를 잃지 않는다.

    본문을 읽지 못하는 것(스트림이 이미 닫혔거나 소켓이 끊겼다)은 그 자체로는 사건이
    아니다 — 우리가 잃는 것은 상세뿐이고 상태 코드는 이미 있다. 그래서 여기서 예외를
    올리지 않고 사유를 문자열로 남긴다.
    """
    try:
        body = exc.read(_MAX_ERROR_BODY).decode("utf-8", errors="replace").strip()
    except (OSError, ValueError) as read_error:
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
