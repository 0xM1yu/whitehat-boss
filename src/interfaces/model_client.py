"""ModelClient 인터페이스 (engineering-guide.md §3).

오케스트레이터 노드는 vLLM·LiteLLM을 직접 import 하지 않는다. 어댑터가 이 Protocol을
구현하고, 노드는 이 시그니처에만 의존한다.

## 실패 두 종이 여기 있는 이유

`ModelGatewayError`·`SchemaParseError`는 원래 `adapters/openai_compat.py`에 있었다.
그런데 **러너가 그 둘을 구별해야 한다** — 후보 하나가 컨텍스트를 넘긴 것과 서버가 죽은
것은 대응이 다르고(전자는 그 후보만 버리고 계속, 후자는 즉시 중단), 러너는
`src/adapters/`를 import 하지 않는다(`runner.py` docstring, `tests/test_repo_hygiene.py`
가 강제). 둘 다 이 Protocol이 정의하는 실패다 — 아래 `ModelClient` docstring이
`SchemaParseError`의 계약을 이미 적고 있었고 예외만 다른 층에 있었다.

**`adapters/openai_compat.py`에 재수출 별칭을 두지 않는다.** 같은 이름이 두 곳에서
보이면 잡는 쪽이 어느 것을 import 해야 하는지가 갈리고, 갈리면 `except`가 조용히
안 걸린다.
"""

from collections.abc import Sequence
from typing import Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field, model_validator

from src.schemas.common import HarnessModel, NonEmptyStr
from src.schemas.enums import Role

# 역할별 출력 스키마(Candidate/Finding/DecisionDraft …). 계약의 `output_schema`에 해당한다.
# Critic이 보내는 것은 `Decision`이 아니라 `DecisionDraft`다 — ID·`produced_by`는 모델이
# 아니라 하네스가 넣는다(`nodes/critic.py`의 `OUTPUT_SCHEMA`).
SchemaT = TypeVar("SchemaT", bound=BaseModel)

# GPU 시간을 **무엇으로 쟀는가.** 검색 결과(`interfaces/retrieval.py`)도 같은 값을 쓴다.
#
#   request_wall_clock — 요청 왕복 시간. 순차 실행 전제에서만 점유의 근사다
#   vllm_metrics       — 서버가 보고한 점유. **아직 구현하지 않았다**
#
# `vllm_metrics`를 미리 열어 두는 것은 자리만 잡아 두는 것이지 있는 척하는 것이 아니다 —
# 서버가 `/metrics`로 무엇을 주는지는 ATOM에서 확인한 뒤에 정한다. 없는 값을 지어내지
# 않는다(model-serving.md §3, bench.py 모듈 docstring).
# ⏵ 2026-10-08 — ATOM `/metrics` 프로브는 2026-08-18에 돌았다
# (`docs/experiments/harness/2026-08-18-serving-atom.md` §3.3). 그 원문은
# `tests/fixtures/metrics/`에 안 들어왔고 ATOM은 2026-09-29 사용 종료(#340)다.
# `vllm_metrics`는 여전히 미구현이다(위 표).
GpuMsSource = Literal["request_wall_clock", "vllm_metrics"]


# `ModelGatewayError.detail`과 `model_call` 실패 이벤트에 남길 최대 문자 수.
#
# **잠정치다.** 살리려는 것은 vLLM의 컨텍스트 초과 한 문장인데 **그 실측 길이를 우리는
# 갖고 있지 않다** — zlib run에서 그 본문이 버려졌고 그것이 이 상수가 생긴 이유다.
# 512는 그 형태의 한 문장에 여유를 둔 값이고, 넘치면 표시가 붙어 그 사실이 드러난다.
# 첫 실대상 run이 이 값의 근거를 만든다(`budgets.tool_seconds`가 #65에 대해 쓰는 논법).
MAX_ERROR_DETAIL = 512


def truncate_detail(text: str) -> str:
    """`MAX_ERROR_DETAIL`로 자르고, 잘렸으면 표시를 남긴다.

    자른 것과 원래 짧은 것을 섞지 않는다 — 표시가 없으면 서버가 말해 준 것이 전부인지
    우리가 버린 것이 있는지 사후에 알 수 없다.

    줄바꿈을 공백으로 접는 것은 이 문자열이 trace JSONL 한 줄의 `data`에 실리기
    때문이다. 원문 개행은 이스케이프되어 읽기만 나빠진다.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_ERROR_DETAIL:
        return collapsed
    return collapsed[:MAX_ERROR_DETAIL] + "…(잘림)"


def error_detail(exc: BaseException) -> str:
    """실패 이벤트에 실을 짧은 사유. `detail`을 든 예외는 그것을, 아니면 문자열화한다.

    `getattr`로 무는 것은 오케스트레이터가 **모든** 모델 실패에 이것을 쓰기 때문이다 —
    어댑터가 무엇을 던지든(`ValidationError`·`OSError`) 한 자리에서 처리한다.
    """
    return truncate_detail(getattr(exc, "detail", "") or f"{exc}")


class ModelGatewayError(RuntimeError):
    """게이트웨이에 도달하지 못했거나 HTTP 오류. 재시도 대상이 아니다.

    **`status`가 러너의 판정 입력이다.** 요청 하나가 거절된 것(400·413·422 — 컨텍스트
    초과가 여기 온다)과 서버 쪽 사건(연결 실패·타임아웃·계약 밖 200이면 `None`, 서버 오류면
    5xx, 이 밖의 4xx도 같다)은 같은 예외에 뭉쳐 있었고, 그래서 러너가 가릴 수단이 없었다.
    문자열을 파싱해 가르지 않도록 상태 코드를 필드로 든다.

    ⚠ **400 하나는 상태만으로 안 갈린다(#296).** vLLM은 컨텍스트 초과와 요청 모양·서버 설정
    거절에 같은 400을 준다 — 앞은 이 후보에 대한 사실이고 뒤는 다음 요청도 같다. 그래서 러너는
    400일 때만 `detail`의 서버 문구를 한 번 더 본다(`runner._is_size_rejection`).

    ⚠ **타임아웃은 서버가 살아 있어도 난다** — 생성이 느린 모델이 출력을 다 내기 전에 어댑터의
    요청 타임아웃에 닿는 경우다(2026-09-24 ATOM 스모크에서 실제로 났다, 이슈 #261). `status`만
    보면 서버 사망과 같은 모양이라 **`generating`이 그 둘을 가른다** — 어댑터가 요청 전·후
    `/metrics` 생성 카운터를 대조해 채운다. `True`(그동안 토큰이 나왔다 = 느림)면 러너가 그
    요청 하나만 버리고, `False`(멈춤)·`None`(못 쟀다·타임아웃이 아니다)이면 지금처럼 run을 멈춘다.

    `detail`은 응답 본문에서 잘라낸 서버 측 사유다. vLLM은 컨텍스트 초과를 그 본문에만
    담는다(`maximum context length is N, however you requested M`) — 예전에는 `exc.read()`를
    부르지 않아 **가장 알고 싶은 M이 버려졌다**.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str = "",
        usage: "Usage | None" = None,
        gpu_ms: int | None = None,
        gpu_ms_source: "GpuMsSource | None" = None,
        attempts: int = 1,
        retry_bodies: tuple[str, ...] = (),
        generating: bool | None = None,
        attempt_log: "tuple[AttemptRecord, ...]" = (),
        raw_texts: tuple[str | None, ...] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.generating = generating
        # 실패해도 이미 쓴 토큰·GPU는 실재한다 — 상위(`context.call_model`)가 장부에
        # 청구하도록 실어 올린다(#82). 게이트웨이가 요청 전에 거절하면(400 컨텍스트 초과)
        # 이 시도는 토큰을 안 만들지만 앞선 재시도의 소비가 `usage`에 남는다. `attempts`는
        # 여기까지의 시도 수다(#83).
        self.usage = usage
        self.gpu_ms = gpu_ms
        self.gpu_ms_source = gpu_ms_source
        self.attempts = attempts
        # 재시도 회차가 실제로 보낸 body — 400이 재시도에서 났으면 그 요청이 여기 있다.
        # 상위가 재시도 파일로 남긴다(#80, `ModelResult.retry_bodies` 주석).
        self.retry_bodies = retry_bodies
        # 여기까지 시도별 (출력 토큰, 종료 사유) — 실패한 호출이 바로 가르려는 대상이다(#550).
        # 응답을 못 받은 시도(이 예외를 낸 시도)도 `AttemptRecord()`로 칸을 지킨다.
        self.attempt_log = attempt_log
        # 이 시도 앞서 스키마에 실패한 시도들의 응답 원문 — 거절한 시도 자체엔 응답이 없다(#603).
        # 칸이 `None`이면 그 시도는 받은 문자열이 없다(`content: null`) — 빈 응답(`""`)이 아니다.
        # ⚠ 이 칸들이 비었다고 응답을 받은 적이 없다는 뜻은 아니다: 200이었으나 모양이 계약과 달라
        # 이 예외가 된 시도의 원문은 여기 없다(`_post`의 「응답 모양이 계약과 다르다」 분기).
        self.raw_texts = raw_texts


class ModelSwapError(ModelGatewayError):
    """단계 경계 교대가 모델을 못 띄웠다 — 교대 명령 실패·마감 초과·준비 확인의 창 불일치.

    **`ModelGatewayError`의 하위형이다.** 다음 단계의 서버가 없다는 사건이라 러너의 게이트웨이
    갈래가 run을 멈추고 매니페스트까지 간다. `status`·`generating`이 `None`이라
    `runner._is_droppable`이 후보 하나의 실패로 읽지 않는다. 사유 코드는 서버 사망과 갈라
    `ReasonCode.MODEL_SWAP_FAILED`로 적는다 — 러너가 이 타입을 먼저 잡아야 한다.

    `report`는 교대 명령이 stdout 마지막 줄에 낸 JSON 보고를 스칼라로 편 것이다. 실패해도
    파싱되는 보고가 있으면 싣는다 — 무엇을 띄우다 죽었는지가 `model_swap ok=false`에 남는다.
    """

    def __init__(
        self,
        message: str,
        *,
        detail: str = "",
        report: dict[str, str | int | float | bool | None] | None = None,
    ) -> None:
        super().__init__(message, detail=detail)
        self.report = dict(report or {})


class SchemaParseError(RuntimeError):
    """`retries` 한도 안에 구조화 출력을 얻지 못했다.

    후보 단위 실패다 — 한 호출이 한도를 소진한 것이고 다음 후보는 성공할 수 있다.
    러너가 이것을 잡아 그 후보만 버린다. 실패 사실은 `context.call_model`의
    `model_call ok=False`가 이미 남기므로(예외를 다시 올리기 전에 쓴다) 잡아도
    "스키마 파싱 실패율"의 분자를 잃지 않는다(그 분자는 시도 단위 `attempts`이고, 산출식
    정본은 `trace.py`의 「이벤트 → 지표」 표다).

    소진까지 서버가 만든 응답의 토큰·GPU는 이미 쓰였다. `usage`·`gpu_ms`에 실어 올려
    상위가 장부에 청구하게 한다(#82) — 안 실으면 폭주가 잦은 조건일수록 싸 보인다.
    `attempts`는 시도 수다(#83).
    """

    def __init__(
        self,
        message: str,
        *,
        usage: "Usage | None" = None,
        gpu_ms: int | None = None,
        gpu_ms_source: "GpuMsSource | None" = None,
        attempts: int = 1,
        retry_bodies: tuple[str, ...] = (),
        attempt_log: "tuple[AttemptRecord, ...]" = (),
        raw_texts: tuple[str | None, ...] = (),
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.gpu_ms = gpu_ms
        self.gpu_ms_source = gpu_ms_source
        self.attempts = attempts
        # 소진까지 재시도가 실제로 보낸 body들. 상위가 재시도 파일로 남긴다(#80).
        self.retry_bodies = retry_bodies
        # 시도별 (출력 토큰, 종료 사유). 소진한 호출이 절단이었는지를 서버의 말로 직접 가른다
        # (#550) — `usage`는 시도를 합친 값이라 못 한다.
        self.attempt_log = attempt_log
        # 시도별 응답 원문 — 실패 경로만 든다. 상위가 응답 파일로 남긴다(#603).
        # 칸이 `None`이면 그 시도는 받은 문자열이 없다(`content: null`) — 빈 응답(`""`)이 아니다.
        self.raw_texts = raw_texts


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


class ChatTurn(HarnessModel):
    """멀티턴 history의 한 턴 (ADR 0012 결정 2 — need_evidence 되묻기).

    `Prompt`와 같은 자리다 — 시스템 prefix는 여기 없다. 요청에 실릴 때는
    `OpenAICompatModelClient._request_payload`가 system 메시지 뒤·이번 turn(user) 앞에
    `speaker`를 그대로 역할로 써서 순서대로 얹는다.
    """

    speaker: Literal["user", "assistant"]
    content: str


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


class AttemptRecord(HarnessModel):
    """시도 한 번이 서버에서 받은 응답의 두 사실 (#550).

    `Usage`는 시도를 **합친** 값이라 「어느 시도가 얼마를 냈고 어떻게 끝났나」를 못 돌려준다.
    **합이 이미 주는 것과 못 주는 것을 가른다**(CLAUDE.md §2.2). 상한이 32,768일 때 두 시도의
    합 65,536은 산술로 **둘 다 정확히 32,768**이고(각각 상한 이하이므로), 그때 남는 물음은 그 값이
    `length`인지 상한과 같은 값에서의 정상 종료(`stop`)인지뿐이다. 합 27,888은 **`null` 칸이 없을
    때** 두 시도가 다 상한 아래임을 이미 보인다 — 그러면 `max_tokens` 절단은 배제된다. `null` 칸은
    합에 0을 더한 시도라 합이 그 시도의 몫만큼 과소일 수 있고, 그 칸이 있으면 이 산술은 서지 않는다
    (`orchestrator/trace.py` 읽는 규칙 3). 두 수의 출처는 #550 본문의 `run_20261007_0001` 관측이다.
    합이 **못** 주는 것은 ① 상한과 그 2배 사이의 합에서 어느 시도가 상한에 닿았나
    (`a₁ + a₂ = 8,402`이고 두 항이 다 8,192 이하면 나오는 것은 `a₁ ≥ 210`뿐이라던 §2.2의 사례)
    ② `length`·`stop` 밖의 사유(`abort` …) ③ 추론이 아니라 서버가 한 말이다.

    **`None`은 「안 쟀다」다.** 응답을 못 받은 시도(게이트웨이 거절·타임아웃·모양이 깨진 200)는
    `AttemptRecord()`(둘 다 `None`)로 자리를 지킨다 — 칸이 시도 순서와 1:1이라 `attempts`와
    어긋나지 않고, 그 시도의 토큰을 0으로 지어내지 않는다. **응답은 받았어도 서버가
    `usage.completion_tokens`를 안 실었으면 `output_tokens`가 `None`이다** — 합산 `Usage`는 그때
    0을 채우지만(`None`을 못 든다) 이 칸은 안 채운다. 그래서 여기서 `0`은 **서버가 0이라고 보고한
    것**이고 「안 줬다」가 아니다. 합산 쪽에는 이 구분이 없다(`Usage.output_tokens == 0`은 둘 다다).

    `finish_reason`은 서버가 준 문자열 그대로다(`stop`·`length`·`abort` …). 키가 없거나 비었거나
    공백뿐이거나 문자열이 아니면 `None`이고, `""`·`"unknown"`으로 채우지 않는다. 알려진 값 목록으로
    거르지 않는다 — 처음 보는 사유를 버리면 이 필드가 있는 이유가 사라진다. 원문이
    512자(`MAX_ERROR_DETAIL`)를 넘는 값만 `error_detail`과 같은 자르기로 묶는데
    (`openai_compat._finish_reason_from`), 그 자르기는 **공백을 접고 다시 재므로** 접은 결과가
    512자 이하면 잘림 표시 없이 공백만 달라진다.
    """

    output_tokens: int | None = Field(default=None, ge=0)
    finish_reason: str | None = None


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
    # 이 결과를 얻기까지의 시도 수. 재시도가 `complete()` 안에서 끝나 성공 한 건으로
    # 나오므로, 이 값이 없으면 **1차 실패 후 살아난 호출이 성공 한 건과 구별되지 않는다**
    # (#83). trace의 `model_call`에 실려 evaluation.md §5.1 "구조화 출력 실패율"의 분자를
    # 시도 단위로 만든다. 재시도가 없으면 1이다.
    attempts: int = Field(default=1, ge=1)
    # 재시도 회차(2~N차)가 **실제로 서버에 보낸** body. 1차 body는 이미 프롬프트 로그에
    # 있으므로 담지 않는다 — 이것은 그 뒤의 것들이다. 어댑터는 산출물 디렉터리를 모르므로
    # (계층: `src/adapters/`는 오케스트레이터를 import 하지 않는다) 파일을 직접 못 쓰고,
    # 상위(`context.call_model`)가 이 본문을 재시도 파일로 남겨 **실제로 간 요청을 재현**할
    # 수 있게 한다(#80). 재시도가 없으면 빈 튜플이다.
    retry_bodies: tuple[str, ...] = ()
    # 성공한 시도의 응답 원문(`_post`가 읽는 `message.content`. `FakeModelClient`는 낸
    # 값의 JSON). **사고 토큰은 담지 않는다** — 그 자리는 Phase 8이다. 구현체가 안 채우면
    # `None`이고, 이것도 미실측 표시다.
    raw_text: str | None = None
    # 시도별 (출력 토큰, 종료 사유), **시도 순서대로**(#550). `usage`는 이 시도들을 합친 값이고
    # 그대로다 — 이것은 합을 쪼갠 내역이지 대체가 아니다. 구현체가 안 채우면 빈 튜플이고, 그것은
    # 「안 쟀다」이지 「시도가 없었다」가 아니다(`attempts >= 1`이다). 채우는 구현체는 시도마다
    # 칸 하나를 둔다.
    #
    # ⚠ **아래 둘은 어댑터가 짓는 시점의 관례이지 강제되는 불변식이 아니다**: `len(attempt_log) ==
    # attempts`, 그리고 `usage.output_tokens == Σ(칸의 output_tokens, None 제외)`. 이 모델은 둘을
    # 검증하지 않아 어긋난 `ModelResult`도 그대로 만들어지고 trace까지 간다. 일부러 거부하지 않는다
    # — 던지는 검증은 계측의 결함을 8시간짜리 run의 중단으로 바꾼다. 지켜지는 곳은
    # `OpenAICompatModelClient.complete()`와 대본을 준 `FakeModelClient`이고, 그 둘을 못 박는 것은
    # `tests/test_model_call_instrumentation.py`다:
    #   - 합: `test_the_summed_usage_equals_the_sum_of_the_attempts`
    #   - 칸 수: `test_every_attempt_keeps_its_slot_at_three_attempts`
    #   - Fake: `test_the_fake_can_script_a_truncated_multi_attempt_call_through_routing`
    # 어긋남을 읽는 쪽은 같은 `model_call` 이벤트의 `attempts`·`output_tokens`와 대조한다
    # (`orchestrator/trace.py`).
    attempt_log: tuple[AttemptRecord, ...] = ()


class ModelClient(Protocol):
    """역할 → 모델 라우팅과 구조화 출력 강제 (model-serving.md §6).

    스키마 파싱 실패 시 재요청(계약의 `retries`)은 구현체 책임이다. 한도를 넘기면
    예외를 던지고, 오케스트레이터가 `ReasonCode.SCHEMA_PARSE_FAILED`로 기록한다.

    **`history`는 이 저장소 첫 Protocol 키워드 전용 기본값이다** (ADR 0012 결정 2 —
    need_evidence 되묻기의 멀티턴). 구현체 셋(`fake_model.py`·`openai_compat.py`·
    `routing_model.py`) 모두 `history[k].speaker`를 그대로 역할로 써서 system과 이번
    turn(user) 사이에 순서대로 얹는다 — 비어 있으면 지금까지의 단일 턴 동작 그대로다.
    재시도는 이 history를 그대로 두고 이번 turn의 본문만 바꾼다 — 실패한 응답을
    history에 끼우지 않는다.
    """

    def complete(
        self,
        *,
        role: Role,
        prompt: Prompt,
        schema: type[SchemaT],
        history: Sequence[ChatTurn] = (),
    ) -> ModelResult[SchemaT]: ...
