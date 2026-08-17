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

from src.interfaces.model_client import ModelResult, Prompt, Usage
from src.schemas.enums import Role

_TIMEOUT_S = 300


class ModelGatewayError(RuntimeError):
    """게이트웨이에 도달하지 못했거나 HTTP 오류. 재시도 대상이 아니다."""


class SchemaParseError(RuntimeError):
    """`retries` 한도 안에 구조화 출력을 얻지 못했다."""


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
        retries: int = 1,
        api_key: str = "EMPTY",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.temperature = temperature
        self.top_p = top_p
        # seed를 요청에 실어야 같은 조건 재실행이 같은 결과를 낸다(engineering-guide.md §5).
        self.seed = seed
        self.retries = retries
        # 온프레미스 vLLM은 인증을 쓰지 않지만 OpenAI 클라이언트 규약상 헤더가 필요하다.
        # 실제 키가 필요한 게이트웨이라면 환경변수에서 주입한다 — 소스에 두지 않는다.
        self.api_key = api_key

    def complete(self, *, role: Role, prompt: Prompt, schema: type[BaseModel]) -> ModelResult:
        last_error: Exception | None = None
        body = prompt.body
        # **재시도분까지 더한다.** 스키마 파싱에 실패한 응답도 서버는 이미 만들어 냈고
        # 그 GPU 시간은 실제로 쓰였다. 마지막 시도만 세면 재시도가 잦은 모델일수록 비용이
        # 싸 보인다 — 조건 간 비교에서 정확히 거꾸로 가는 편향이다.
        gpu_ms = 0
        for _ in range(self.retries + 1):
            started = time.monotonic()
            raw, usage = self._post(self._request_payload(prompt.prefix, body, schema))
            gpu_ms += int((time.monotonic() - started) * 1000)
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
                usage=usage,
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
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelGatewayError(f"{self.endpoint} 호출 실패: {exc}") from exc
        return data["choices"][0]["message"]["content"], _usage_from(data.get("usage", {}))


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
