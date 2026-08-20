"""ModelClient 인터페이스 (engineering-guide.md §3).

오케스트레이터 노드는 vLLM·LiteLLM을 직접 import 하지 않는다. 어댑터가 이 Protocol을
구현하고, 노드는 이 시그니처에만 의존한다.
"""

from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field, model_validator

from src.schemas.common import HarnessModel, NonEmptyStr
from src.schemas.enums import Role

# 역할별 출력 스키마(Candidate/Finding/Decision …). 계약의 `output_schema`에 해당한다.
SchemaT = TypeVar("SchemaT", bound=BaseModel)

# GPU 시간을 **무엇으로 쟀는가.** 검색 결과(`interfaces/retrieval.py`)도 같은 값을 쓴다.
#
#   request_wall_clock — 요청 왕복 시간. 순차 실행 전제에서만 점유의 근사다
#   vllm_metrics       — 서버가 보고한 점유. **아직 구현하지 않았다**
#
# `vllm_metrics`를 미리 열어 두는 것은 자리만 잡아 두는 것이지 있는 척하는 것이 아니다 —
# 서버가 `/metrics`로 무엇을 주는지는 ATOM에서 확인한 뒤에 정한다. 없는 값을 지어내지
# 않는다(model-serving.md §3, bench.py 모듈 docstring).
GpuMsSource = Literal["request_wall_clock", "vllm_metrics"]


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
    # 이 호출이 GPU를 점유한 시간(밀리초). **안 쟀으면 `None`이고 0이 아니다** —
    # 0은 "GPU를 안 썼다"는 적극적인 진술이라 Fake 호출과 미측정이 한 값으로 합쳐진다
    # (`repo_dirty`·`device`가 `None`을 쓰는 것과 같은 이유).
    #
    # **초가 아니라 밀리초인 것이 요점이다.** 2026-08-17 실측: 도구 11건의 `wall_ms`
    # 합계 8,492 ms가 장부에 3초로 남았다 — 호출 지점마다 초로 내림해서 8건이 통째로
    # 0으로 청구됐다. 단위 변환은 원장이 한 곳에서만 한다(`BudgetLedger`).
    gpu_ms: int | None = None
    # 위 값이 **무엇에서 나왔는가.** 라벨 없이 wall-clock을 GPU 시간이라 부르지 않는다.
    #
    # `request_wall_clock`은 요청 왕복 시간이라 **v1의 순차 실행 전제에서만** 점유의
    # 근사다. vLLM은 continuous batching을 하므로 큐 대기가 섞이고, 동시 요청이면 같은
    # GPU 시간이 두 번 세어진다. 큐를 스레드로 돌리는 순간 이 값의 의미가 무너지는데
    # 그때 남아 있을 경고가 이 라벨이다 — 문서는 사람을 규율하고 기록은 데이터를
    # 규율한다(ADR 0005의 논법). 그래서 trace 이벤트에도 함께 싣는다.
    gpu_ms_source: GpuMsSource | None = None


class ModelClient(Protocol):
    """역할 → 모델 라우팅과 구조화 출력 강제 (model-serving.md §6).

    스키마 파싱 실패 시 재요청(계약의 `retries`)은 구현체 책임이다. 한도를 넘기면
    예외를 던지고, 오케스트레이터가 `ReasonCode.SCHEMA_PARSE_FAILED`로 기록한다.
    """

    def complete(
        self, *, role: Role, prompt: Prompt, schema: type[SchemaT]
    ) -> ModelResult[SchemaT]: ...
