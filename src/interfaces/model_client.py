"""ModelClient 인터페이스 (engineering-guide.md §3).

오케스트레이터 노드는 vLLM·LiteLLM을 직접 import 하지 않는다. 어댑터가 이 Protocol을
구현하고, 노드는 이 시그니처에만 의존한다.
"""

from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, Field, model_validator

from src.schemas.common import HarnessModel, NonEmptyStr
from src.schemas.enums import Role

# 역할별 출력 스키마(Candidate/Finding/Decision …). 계약의 `output_schema`에 해당한다.
SchemaT = TypeVar("SchemaT", bound=BaseModel)


class Prompt(HarnessModel):
    """프롬프트를 prefix와 가변부로 나눈 형태.

    agent-contracts.md §4.2: 공통 system prompt + weakness checklist + output schema는
    **prefix로 고정**한다(vLLM automatic prefix caching 적중 목적). 이 분리를 문자열
    하나로 뭉개면 캐시 적중률을 측정할 수도, 지킬 수도 없다.
    """

    prefix: NonEmptyStr
    body: NonEmptyStr
    # 결과에 함께 기록된다(agent-contracts.md §4.1). 버전이 바뀌면 이전 결과와
    # 직접 비교하지 않는다.
    prompt_version: NonEmptyStr


class Usage(HarnessModel):
    """토큰 사용량.

    evaluation.md §5.2: input/output/cached는 **반드시 분리 기록**한다. 합계만 남기면
    prefix cache의 효과(핵심 비용 주장)를 검증할 수 없다.
    """

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    # input 중 캐시에서 온 몫이므로 input_tokens를 넘을 수 없다.
    cached_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def _cached_within_input(self) -> "Usage":
        if self.cached_tokens > self.input_tokens:
            raise ValueError(
                f"cached_tokens({self.cached_tokens})가 "
                f"input_tokens({self.input_tokens})를 초과한다"
            )
        return self


class ModelResult(HarnessModel, Generic[SchemaT]):
    """구조화 출력 + 그 호출의 비용."""

    value: SchemaT
    usage: Usage
    # 실제로 응답한 모델. fallback이 동작하면 요청한 모델과 다를 수 있고,
    # `ProducedBy.model`에 기록되는 값이므로 결과에서 되돌려받아야 한다.
    model: NonEmptyStr


class ModelClient(Protocol):
    """역할 → 모델 라우팅과 구조화 출력 강제 (model-serving.md §6).

    스키마 파싱 실패 시 재요청(계약의 `retries`)은 구현체 책임이다. 한도를 넘기면
    예외를 던지고, 오케스트레이터가 `ReasonCode.SCHEMA_PARSE_FAILED`로 기록한다.
    """

    def complete(
        self, *, role: Role, prompt: Prompt, schema: type[SchemaT]
    ) -> ModelResult[SchemaT]: ...
