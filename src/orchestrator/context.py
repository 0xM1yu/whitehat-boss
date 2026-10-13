"""RunContext — 노드가 보는 실행 문맥.

노드 시그니처는 `def run(ctx: RunContext, ...) -> ...`이고 LangGraph 타입을 노출하지
않는다(open-questions.md ADR 0002 미확정, ADR 0002). 오케스트레이터를 갈아 끼워도 노드가
그대로 남으려면 노드가 아는 것이 이 객체 하나여야 한다.

**부작용은 전부 여기를 지난다.** 모델 호출·도구 실행·게이트 판정·정책 결정·상태 전이가
각각 대응하는 trace 이벤트와 짝지어져 있어서, 노드가 이 래퍼를 쓰는 한 기록 누락이
일어나지 않는다. 노드가 `ctx.models.complete()`를 직접 부르면 usage가 예산에 잡히지
않고 `model_call`이 비어 비용 지표가 무너진다 — 그 경로를 만들지 않는 것이 설계다.

`src/adapters/`의 구체 구현을 import 하지 않는다. 의존성은 `src/interfaces/`의 Protocol로만
받으며 어댑터 조립은 `src/adapters/factory.py`가 한다(engineering-guide.md §3).
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from time import perf_counter

from src.config import HarnessConfig
from src.ids import IdFactory
from src.interfaces.model_client import (
    AttemptRecord,
    ChatTurn,
    ModelClient,
    ModelResult,
    Prompt,
    SchemaT,
    Usage,
    error_detail,
)
from src.interfaces.oracle import Oracle
from src.interfaces.retrieval import RetrievalResult
from src.interfaces.retriever import Retriever
from src.interfaces.state_store import StateStore
from src.interfaces.tool_runner import SandboxSpec, ToolArgs, ToolRunner
from src.orchestrator import gate, state_machine
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.gate import GateResult
from src.orchestrator.poc_log import PocLog
from src.orchestrator.policy import Action, RoutingPolicy, RoutingRequest
from src.orchestrator.prompt_log import PromptLog
from src.orchestrator.stages import GATE_STAGES, Stage
from src.orchestrator.trace import EventKind, StageSpan, TraceValue, TraceWriter
from src.schemas.candidate import scout_member_of
from src.schemas.common import Location, ProducedBy
from src.schemas.decision import Decision
from src.schemas.enums import FindingState, ReasonCode, Role, Verdict
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence
from src.schemas.need_evidence import Resolution, ResolveQuery
from src.schemas.run import RunRecord

# 도구 실행은 토큰을 쓰지 않는다. 매번 만들지 않도록 상수로 둔다.
_NO_TOKENS = Usage(input_tokens=0, output_tokens=0, cached_tokens=0)


def _default_monotonic_ms() -> int:
    return int(perf_counter() * 1000)


def _trace_scalar(value: object) -> str:
    """발급값 필드를 trace 값(스칼라)으로 — ID 리스트(`missing_evidence`)는 쉼표로 잇는다.

    ⚠ 쉼표가 든 ID는 이어 붙인 값에서 경계가 안 갈린다 — `Identifier`는 길이만 제약한다
    (`src/schemas/common.py`). 필드가 달랐다는 사실(`fields`)은 그래도 정확하다.
    """
    return ",".join(value) if isinstance(value, list) else str(value)


# `finish_reason == "length"`로 끝난 시도의 응답 **꼬리** 몇 글자를 볼지 (#79). 공백 폭주는
# 꼬리가 공백·탭·개행의 연속인 모양이다(2026-09-11 프로브: 꼬리 공백 12,849~13,199자 - 현재 로스터가
# 아닌 `qwen3-coder-30b-a3b`). **정한 값이고 잰 값이 아니다 (CLAUDE.md §0)** - 현재 로스터의 꼬리는
# 이 탐지기 이전에 본 적이 없다. 줄이면 정상 JSON의 들여쓰기 끝을 폭주로 읽는 쪽으로, 키우면 폭주가
# 덜 자란 응답을 놓치는 쪽으로 움직인다.
TAIL_WHITESPACE_CHARS = 64


def _tail_whitespace_only(text: str) -> bool:
    """응답 끝 `TAIL_WHITESPACE_CHARS`자가 전부 공백인가. **창을 못 채우는 짧은 응답은 거짓이다**
    (빈 문자열 포함) - 63자 이하가 전부 공백인 응답(개행 둘)은 폭주가 아니고, 아무것도 안 낸
    응답(추론 파서가 `content`를 비운 모양)도 아니다."""
    return len(text) >= TAIL_WHITESPACE_CHARS and text[-TAIL_WHITESPACE_CHARS:].isspace()


def _tail_whitespace_verdicts(
    log: Sequence[AttemptRecord], texts: Sequence[str | None]
) -> list[bool | None]:
    """시도마다 꼬리 판정 한 칸 - `texts[i]`가 `log[i]`의 응답 원문이다(앞에서부터 정렬된다).

    **`None`은 두 가지다 - 같은 이벤트의 `attempt_finish_reasons`로 가른다.** 그 칸이 `length`가
    아니면 안 본 것이고(해당 없음), `length`인데 원문이 없으면(게이트웨이 거절·`content: null`·
    원문을 안 든 구현체) 못 본 것이다(미측정 - 거짓으로 채우지 않는다).
    """
    return [
        _tail_whitespace_only(texts[i])
        if record.finish_reason == "length" and i < len(texts) and texts[i] is not None
        else None
        for i, record in enumerate(log)
    ]


def _attempt_fields(
    log: Sequence[AttemptRecord], texts: Sequence[str | None] = ()
) -> dict[str, TraceValue]:
    """시도별 출력 토큰·종료 사유·꼬리 판정을 `model_call`의 세 키로 (#550·#79). 정본: `trace.py`.

    **값은 JSON 배열 문자열이다**(`TraceValue`가 스칼라라서). 쉼표로 잇지 않는 것은 미측정 칸
    (`null`)을 빈 칸이나 지어낸 문자열로 쓰지 않으려는 것이다 — `json.loads`로 읽으면 `None`이
    `None`으로 온다.

    **`log`가 비면 두 키 모두 `None`이다** — 어댑터가 안 실은 호출(Fake·소비를 안 든 예외)이거나,
    실었는데 잃은 호출(측정된 뒤 생 예외가 `complete()`를 뚫은 경우)이다. 둘은 여기서 안 갈린다
    (`trace.py` 읽는 규칙 2). `"[]"`이 아니다: 시도가 없었다는 뜻으로 읽히고, 키 집합은 성공·실패
    행에서 같아야 한다.
    """
    if not log:
        return {
            "attempt_output_tokens": None,
            "attempt_finish_reasons": None,
            "attempt_tail_whitespace_only": None,
        }
    tails = _tail_whitespace_verdicts(log, texts)
    return {
        "attempt_output_tokens": json.dumps([a.output_tokens for a in log], separators=(",", ":")),
        "attempt_finish_reasons": json.dumps([a.finish_reason for a in log], separators=(",", ":")),
        "attempt_tail_whitespace_only": json.dumps(tails, separators=(",", ":")),
    }


def _success_attempt_texts(result: ModelResult) -> tuple[str | None, ...]:
    """성공 호출의 시도별 응답 원문 - 버려진 앞 시도들 + 마지막(성공) 시도. 칸 수가 시도 수와 안
    맞으면 정렬을 믿을 수 없어 빈 튜플이다(구현체가 `failed_raw_texts`를 안 채운 경우 - 꼬리 판정은
    `None`)."""
    texts = (*result.failed_raw_texts, result.raw_text)
    return texts if len(texts) == len(result.attempt_log) else ()


def _unwired_read_source(_target: Location) -> str:
    """`read_source`가 배선되지 않은 채 호출됐다. 빈 문자열로 넘기지 않는다.

    조용히 ""를 돌려주면 프롬프트에 원문이 빠진 채로 모델이 호출되고, 그 결과가
    "모델이 못 찾았다"로 집계된다 — 배선 실수가 모델 성능 지표로 둔갑한다.
    """
    raise NotImplementedError(
        "RunContext.read_source가 배선되지 않았다. 파일 읽기 구현(read_symbol_source)은 "
        "어댑터 쪽에 있고 조립부가 주입한다 — 오케스트레이터는 그것을 import 하지 않는다."
    )


def _unwired_read_slice(slice_id: str) -> str:
    """`read_slice`가 배선되지 않은 채 슬라이스 타깃이 왔다. `read_source`와 같은 이유로 터뜨린다 —
    심볼 본문으로 대신 채우면 v3가 슬라이스로 읽을 `SOURCE`에 줄번호도 배너도 없는 한 줄이 간다."""
    raise NotImplementedError(
        f"RunContext.read_slice가 배선되지 않았다(슬라이스 {slice_id}). 렌더는 어댑터 쪽에 있고 "
        "조립부(`build_deps`)가 주입한다."
    )


@dataclass
class RunContext:
    """한 run의 의존성과 기록 장치.

    `frozen=True`가 아닌 것은 `record`(RunRecord)가 실행 중에 갱신되기 때문이다.
    나머지 필드는 생성 후 바뀌지 않는다.
    """

    run_id: str
    config: HarnessConfig
    models: ModelClient
    retriever: Retriever
    tools: ToolRunner
    store: StateStore
    ids: IdFactory
    trace: TraceWriter
    ledger: BudgetLedger
    policy: RoutingPolicy
    record: RunRecord | None = None
    # **역할별 GPU 미측정 횟수.** 원장이 아니라 여기서 센다 — 원장은 상한 관리자이고,
    # 역할 차원을 넣으면 "역할별 상한"이 필요하다는 압력이 생긴다. evaluation.md §5.2가
    # 요구하는 것은 역할별 **지표**이지 역할별 상한이 아니다. 역할을 아는 곳이 여기이고,
    # 이 값은 `runner.unmeasured_gpu_warning()`의 입력이 된다.
    gpu_unmeasured_roles: dict[str, int] = field(default_factory=dict)
    # 2단계(`signals.run`)가 **컴파일러 오류 문구를 들고 실패한** `build`·`clang-tidy` Evidence —
    # 키는 그 호출의 `workdir`(= `artifact.path`)다. `with_signal()`이 `ok=False`를 걸러 타깃의
    # `evidences` 에는 안 남으므로 따로 둔다(이슈 #507). 8단계 PoV·5단계 Auditor 루프의 hop0
    # 노트가 읽고, 없으면 노트도 없다(요청 바이트가 종전과 같다).
    compile_failures: dict[str, tuple[Evidence, ...]] = field(default_factory=dict)
    # 마지막으로 진입한 단계. Retriever 콜백이 자기 단계를 모르므로 여기서 채운다.
    current_stage: Stage = Stage.PREPROCESS
    # 대상 심볼의 원문을 읽는다. `ContextCapsule`은 본문을 담지 않는 매니페스트이므로
    # 프롬프트에 실을 코드는 여기서 온다. 구현은 Retriever와 **같은 함수**여야 한다 —
    # 두 곳이 다르게 읽으면 캡슐의 토큰 수와 실제 프롬프트가 어긋난다.
    read_source: Callable[[Location], str] = field(default=_unwired_read_source)
    # 슬라이스 id → 그 슬라이스의 렌더(gutter·배너 — ADR 0012 결정 1). `input: slice`를 선언한
    # 프롬프트의 슬라이스 타깃 Scout `SOURCE`가 여기서 온다(`input: source`면 함수 본문 — #529).
    # Retriever의 `target_function`과 **같은 함수**다(`build_deps`가 둘에 같은 조회를 넣는다) —
    # `read_source`가 Retriever와 같아야 하는 것과 같은 이유다.
    read_slice: Callable[[str], str] = field(default=_unwired_read_slice)
    # evidence payload(`Evidence.payload_ref`)의 원문 로그를 읽는다. Critic·Auditor의 content-aware
    # 버전(각 노드의 `_CONTENT_AWARE_VERSIONS`)이 근거 본문을 받으려면 이 리더가 필요하다(#237
    # HIGH-1 · Auditor는 2026-09-29부터). **미배선 기본값이 예외가 아니라 `None`**인
    # 것은 의도다 — `symbols_for`와 같은 성격으로, payload 파일이 없는 정상 상태(`dev_fake`는
    # 로그 파일을 안 쓴다)가 실제로 있고, 그때는 근거 본문 없이 metadata만으로 진행한다.
    # 배선은 factory/cli가 artifacts_root로 만든 리더를 넣는다.
    read_payload: Callable[[str], str | None] = field(default=lambda _ref: None)
    # 파일(대상 루트 기준 상대경로) → **원본 바이트**. `read_source`는 텍스트 모드로 읽어 `\r`·
    # `\r\n`을 `\n`으로 접으므로 `\r\r\n`·옛 Mac 줄바꿈은 거기서 안 보이고, 인덱서(`\n`만 센다)와
    # 줄 번호가 어긋난다 — 그 어긋남을 보는 쪽(`auditor_guards`의 줄 정렬 검사)이 쓴다.
    # `read_payload`와 같은 성격이다: 미배선 기본값은 `None`("못 읽음")을 돌려주는 리더이고 그
    # 검사는 텍스트로만 한다. 배선은 cli가 대상 루트로 만든 리더를 넣는다.
    read_raw: Callable[[str], bytes | None] = field(default=lambda _file: None)
    # file → 그 파일에 정의된 심볼 위치. 러너가 후보 목록을 만든다(W2).
    #
    # `read_source`와 달리 **미배선 기본값이 예외가 아니라 `None`("인덱스 없음")이다.**
    # 그쪽은 조용히 ""를 돌려주면 배선 실수가 모델 성능 지표로 둔갑하지만, 이쪽의
    # `None`은 실제로 일어나는 정상 상태이고(`dev_fake`) 러너가 파일 단위 폴백으로
    # 처리하며 그 사실을 `target_plan.fallback_artifacts`에 남긴다.
    symbols_for: Callable[[str], list[Location] | None] = field(
        default=lambda _file: None
    )
    # 아래 셋(`callees_of`·`call_sites_of`·`definitions_of`)은 **`demote_nonproduct` 키워드**를
    # 받는다(기본 꺼짐 — `GraphIndex.definitions_of` docstring). 러너가 `auditor_definitions`를 켠
    # run에서만 켠다.
    #
    # 함수 위치 → 그 본문 안 호출이 부르는 함수의 정의 위치(#349). Auditor `SOURCE`가 피호출
    # 함수 본문을 붙이는 데 쓴다. `symbols_for`와 같은 성격 — 미배선 기본값 `None`("그래프 없음")은
    # 붙일 것이 없다는 뜻이고 `SOURCE`는 옛 모양 그대로다.
    callees_of: Callable[..., list[Location] | None] = field(
        default=lambda _target, **_kw: None
    )
    # 함수 위치 → 그 함수를 부르는 호출 지점(#349 (b)). 미배선 `None`이면 호출자 창을 안 붙인다.
    call_sites_of: Callable[..., list[Location] | None] = field(
        default=lambda _target, **_kw: None
    )
    # (이름들, 가까운 파일) → 함수 정의 위치. 매크로 별칭이 부르는 함수를 찾는다(C4).
    # 미배선 `None`이면 별칭 너머 함수를 안 붙인다.
    functions_named: Callable[..., list[Location] | None] = field(
        default=lambda _names, _file, **_kw: None
    )
    # 함수 위치 → 필드 경유 호출마다 (필드, 갈 수 있는 함수)(index-v3, C6). `demote_nonproduct`를
    # 받는다. 미배선 `None`이면 간접 피호출을 안 붙인다.
    indirect_calls_of: Callable[..., list[tuple[str, list[Location]]] | None] = field(
        default=lambda _target, **_kw: None
    )
    # (이름|필드, 가까운 파일) → 쓰는 지점(index-v3, C5(a)). 미배선 `None`이면 쓰기 지점 창이 없다.
    writes_of: Callable[[str, str], list[Location] | None] = field(
        default=lambda _name, _file: None
    )
    field_writes_of: Callable[[str, str], list[Location] | None] = field(
        default=lambda _field, _file: None
    )
    # (이름들, 가까운 파일) → 매크로·타입·파일 변수·enum 상수의 정의 위치(#349 (f)). 미배선
    # `None`이면 정의를 안 붙인다 — `callees_of`와 같은 성격.
    definitions_of: Callable[..., list[Location] | None] = field(
        default=lambda _names, _file, **_kw: None
    )
    # (파일, 함수 이름) → 그 함수의 지역·파라미터 이름. 정의 조회가 본문 이름에서 이것을 뺀다
    # (`runner._auditor_source`). 미배선 `None`("v2 표 없음")은 뺄 것이 없다는 뜻이다.
    locals_of: Callable[[str, str], frozenset[str] | None] = field(
        default=lambda _file, _func: None
    )
    # 역할 → 그 역할 모델의 실 토크나이저 카운터 (#96 (b)). `None`이면 기본 카운터
    # (글자수 근사 `len//4`)를 유지한다. `symbols_for`와 같은 성격 — 어댑터가 만들고
    # factory가 주입하며, 러너가 역할 캡슐 조립 직전에
    # `using_counter(ctx.counter_for_role(role))`로 켠다. RunContext는 `src/adapters/`를
    # import 하지 않으므로 콜러블로 받는다. **미배선 기본값이 "토크나이저 없음"**인 것은
    # 의도다 — 이 필드 없이 만든 RunContext(단위 테스트)가 옛 동작을 그대로 남긴다.
    counter_for_role: Callable[[str], Callable[[str], int] | None] = field(
        default=lambda _role: None
    )
    # 렌더된 프롬프트를 run 산출물에 남기는 곳 (해소). `read_source`와 마찬가지로 조립부가
    # 주입한다 — run_dir을 아는 것은 러너이고 노드도 이 객체도 그것을 몰라야 한다.
    #
    # **`None`이면 아무것도 쓰지 않고 `model_call`도 그대로다.** 산출물 디렉터리가 없는
    # 호출 경로(단위 테스트, `--dry-run`)에서 빈 참조를 남기면 "안 썼다"와 "쓸 곳이
    # 없었다"가 구별되지 않는다.
    prompts: PromptLog | None = None
    # **성공한** 호출의 최종 응답 원문도 `prompts`에 남길 역할(#612) — 기본은 빈 집합이라 안 남긴다.
    # Auditor 응답은 수십만 자일 수 있어 켜는 역할을 골라야 한다(`run --keep-responses`).
    keep_responses: frozenset[str] = frozenset()
    # PoV 루프의 **최종 후보 PoC(보통 마지막 `gen`)**를 run 산출물에 파일로 남기는 곳 (#355) —
    # 루프당 최대 한 파일이다(`poc_log` 모듈 docstring). **`prompts`와 같은 자리이고 같은 이유다** —
    # run_dir을 러너만 알게 두려고 쓰기 객체를 받는다. `None`이면 아무것도 쓰지 않고 trace 이벤트도
    # 그대로다(사이드카 키를 **아예** 안 늘린다).
    pocs: PocLog | None = None
    # need_evidence resolver hook (ADR 0012 결정 2). `None`이면 이 run에 resolver가 없다
    # (인덱스가 v1이거나 미로드) — `symbols_for`·`counter_for_role`과 같은 자리다. 부르는 곳은
    # 둘이다 — Scout 좌석 집계 루프(러너, #95)와 **Auditor 도구 루프의 `lookup`**
    # (`nodes/auditor._lookup`, 이슈 #419 — 훅만 받고 어댑터는 import하지 않는다). `None`이면
    # 후자는 정지하지 않고 「조회가 없다」는 관측을 모델에게 준다.
    # `Dependencies.resolve_requests`(runner.py)와 같은 자리를 `RunContext`로 옮겨 온 것뿐이다.
    resolve_requests: Callable[[ResolveQuery], Resolution] | None = None
    # 채점기(oracle) — 8단계가 낸 PoC를 저장소 밖 채점 서버에 내보내는 자리 (#386). 중간 gen은
    # `vul` 다리와 원시 종료 코드 조회를 다음 홉 관측에 쓰고, 모델이 submit한 하나만 지정 후보가
    # 된다. 최종으로 지정한 하나에만 `fix` 다리를 낸다(`runner._finalize_oracle`).
    # `None`이면 이 run은 제출하지 않는다(기본) — `resolve_requests`·`swap_to`와 같은 자리다.
    # 구현은 어댑터에 있고 이 경계는 `src/interfaces/oracle.py`의 Protocol이 넘는다.
    oracle: Oracle | None = None
    # 단계 경계 모델 교대 (ADR 0012 Phase 6.5). `Dependencies.swap_to`(runner.py)를 옮겨 온
    # 것이다 — `resolve_requests`와 같은 자리. `None`이면 이 run은 교대하지 않는다. 부르는 곳은
    # 러너의 `_swap_for`·run 끝 내림뿐이고 노드는 이것을 쓰지 않는다.
    swap_to: Callable[[tuple[str, ...]], dict[str, TraceValue]] | None = None
    # 교대가 올렸는데 교대 명령이 그 서버를 하나도 보고하지 않은(`server.<key>.*`가 없는) model key.
    # `gpu_unmeasured_roles`와 같은 자리다 — 러너(`_swap`)가 모으고 `run_pipeline`이 run 경고로
    # 올린다.
    swap_unreported: set[str] = field(default_factory=set)
    # 테스트가 고정할 수 있도록 주입한다. `wall_ms`는 재현성 비교에서 제거되는 값이다.
    monotonic_ms: Callable[[], int] = field(default=_default_monotonic_ms)
    # CyberGym 과제 설명(description.txt). `None`이면 이 run에 과제 설명이 없다(대부분의 run).
    # 있으면 `retrieval.run`이 캡슐에 `vuln_description` 섹션으로 싣는다(PoV 배선 — Scout
    # 설명기반 트리아지 입력). run 단위 사실이라 여기서 온다 — `read_payload`와 같은 자리다.
    vuln_description: str | None = None

    # --- 단계 ---

    def stage(self, stage: Stage) -> StageSpan:
        """`with ctx.stage(Stage.SCOUT):` — `stage_start`/`stage_end`를 짝지어 남긴다.

        진입한 단계를 기억한다. Retriever의 콜백(`on_cache`·`on_usage`)은 자기가 몇
        단계에서 불렸는지 모르는데, 캡슐 캐시는 3·5·7단계에서 모두 발생하고 검색 비용은
        `ledger.charge(stage=)`가 단계를 요구한다. 단계 없이 남기면 "3단계 적중률" 같은
        질문에 답할 수 없다.

        v1 전제: 러너가 순차 실행이라 "마지막에 진입한 단계"로 충분하다(계획 §5.1
        "동시성 없음이 v1 전제다" — `trace.py`도 같은 전제 위에 있다). 단계를 동시에
        돌리게 되면 콜백에 단계를 명시적으로 실어 보내야 한다.
        """
        self.current_stage = stage
        return self.trace.stage_span(stage, monotonic_ms=self.monotonic_ms)

    # --- 모델 ---

    def call_model(
        self,
        *,
        role: Role,
        prompt: Prompt,
        schema: type[SchemaT],
        stage: Stage,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        history: Sequence[ChatTurn] = (),
        hop: int = 0,
    ) -> ModelResult[SchemaT]:
        """모델 호출 + usage 기록 + 예산 차감 + trace. 노드는 이것만 쓴다.

        실패해도 `model_call`을 남기고 예외를 그대로 올린다 — "스키마 파싱 실패율"의
        분자(시도 단위 `attempts`)가 여기서 나오므로 삼키면 지표가 0이 된다. 그 지표의
        산출식 정본은 `trace.py`의 「이벤트 → 지표」 표다(evaluation.md §5.1은 그 서술).
        예산 소진이면 호출 전에 `BudgetExhausted`가 나므로 토큰을 쓰지 않는다.

        **실패 이벤트에 클래스 이름만 싣던 것이 zlib에서 조사를 막았다.** vLLM이 컨텍스트
        초과를 응답 본문에만 담는데(`ModelGatewayError.detail`) 기록에는 `error`
        문자열 하나뿐이라, 무엇이 얼마나 넘쳤는지가 산출물 어디에도 없었다.
        `_error_detail()`이 잘라낸 상세를 함께 싣는다.

        **`history`·`hop`은 need_evidence 되묻기의 후속 턴이다** (ADR 0012 결정 2).
        `hop=0`이 첫 호출이고, 조립(system·history·이번 turn 순서)은 여기가 아니라
        `models.complete()`(어댑터의 `_request_payload`)가 한다 — 여기서는 그대로
        위임하고 `hop`을 `MODEL_CALL`과 프롬프트 로그 파일명에 싣는다.
        """
        self.ledger.ensure(stage)
        # **예산 확인 뒤, 시계 시작 전.** 뒤인 이유는 `ensure`가 막은 호출에는 대응하는
        # `model_call`이 없어서 파일만 남으면 개수가 어긋나기 때문이고, 앞인 이유는 이
        # 파일 쓰기가 모델 지연(`wall_ms`)에 섞이면 안 되기 때문이다.
        prompt_fields = self._record_prompt(
            role=role, stage=stage, prompt=prompt, hop=hop, history=history
        )
        started = self.monotonic_ms()
        try:
            result = self.models.complete(role=role, prompt=prompt, schema=schema, history=history)
        except Exception as exc:
            # **실패해도 이미 쓴 토큰·GPU를 청구하고 이벤트에 싣는다(#82).** 실패 경로가
            # 청구를 건너뛰면 evaluation.md §5.2의 분자가 비어 폭주가 잦은 조건일수록 싸 보인다.
            # 청구는 예외가 소비를 들고 왔을 때만 한다(아래 `if usage is not None`).
            #
            # **이벤트 키는 성공 경로와 균질하게 둔다(#82 리뷰 F10).** 소비를 안 든 예외
            # (`UnknownRoleError` 등)는 토큰·gpu·attempts를 `None`으로 싣는다 — 안 잰 것은 0이
            # 아니라 `None`이고(model_client.py `gpu_ms` 주석), 키 집합이 실패 종류마다 달라지면
            # 사후 집계가 실패 행에서 KeyError나 `.get(...,0)` 0-뭉갬을 낸다. `attempts`도 안
            # 실려 오면 **지어낸 1이 아니라 `None`**이다(#83 리뷰 F3 — 미실측은 미실측으로 둔다).
            usage = getattr(exc, "usage", None)
            gpu_ms = getattr(exc, "gpu_ms", None)
            spend: dict[str, object] = {
                "attempts": getattr(exc, "attempts", None),
                "input_tokens": usage.input_tokens if usage is not None else None,
                "output_tokens": usage.output_tokens if usage is not None else None,
                "cached_tokens": usage.cached_tokens if usage is not None else None,
                "gpu_ms": gpu_ms,
                "gpu_ms_source": getattr(exc, "gpu_ms_source", None),
                # 시도별 내역도 같은 규칙이다 — 안 실려 왔으면 `None`이고 지어낸 빈 목록이 아니다.
                # 가르려는 호출(`SchemaParseError`로 버려진 Finding)이 전부 이 경로로 온다(#550).
                **_attempt_fields(
                    getattr(exc, "attempt_log", ()), getattr(exc, "raw_texts", ())
                ),
            }
            # 실패해도 재시도가 실제로 보낸 요청은 남긴다 — 400을 받은 요청이 무엇이었는지가
            # 이 항목의 요점이다(#80). 소비를 안 든 예외(`BudgetExhausted`)는 `retry_bodies`가
            # 없어 `getattr`가 빈 튜플을 준다.
            # **쓰기 실패가 이 블록을 끊지 않는다(#168 (a)).** 파일 I/O가 여기서 터지면 그 예외가
            # 원 예외를 덮고, 아래 `emit`·`charge`가 안 돌아 이미 쓴 토큰·GPU가 장부 밖으로 샌다 —
            # 실패분 청구(#82)의 우회로다. 재시도 많은 run이 곧 디스크를 채우는 run이라 무작위
            # 실패도 아니다. 실패하면 `sent_sha256` 대신 사유(예외 이름·errno)를 싣고 청구·원
            # 예외는 그대로 간다. ⚠ 같은 파일시스템이 차서 trace 쓰기(`emit`)까지 실패하면 그
            # 예외가 원 예외를 덮는다 — 이 재시도 기록 가드(`OSError`)는 재시도 기록 쓰기 하나만
            # 막는다.
            # 성공 경로는 건드리지 않는다 — 거기서는 쓰기 실패가 호출 자체를 멈춘다.
            try:
                retry_fields = self._record_retries(
                    role=role, stage=stage, prompt=prompt,
                    retry_bodies=getattr(exc, "retry_bodies", ()), hop=hop, history=history,
                )
            except OSError as record_exc:
                # errno까지 싣는다 — 클래스 이름만으로는 ENOSPC·EROFS·EIO가 다 `OSError`다.
                retry_fields = {
                    "retry_record_error": f"{type(record_exc).__name__}(errno={record_exc.errno})"
                }
            # 실패한 시도의 응답 원문도 남긴다(#603) — 별도 `try`의 `except Exception` 가드다
            # (위 재시도 기록 가드는 `OSError`뿐). 성공 호출의 버려진 시도는 아래에서 쓴다(#618).
            try:
                response_fields = self._record_failed_responses(
                    role=role, stage=stage, raw_texts=getattr(exc, "raw_texts", ()), hop=hop
                )
            except Exception as record_exc:  # 원 예외·청구를 가리면 안 된다
                errno = f"(errno={record_exc.errno})" if isinstance(record_exc, OSError) else ""
                response_fields = {"response_record_error": f"{type(record_exc).__name__}{errno}"}
            self.trace.emit(
                EventKind.MODEL_CALL,
                stage=stage,
                subject_kind=subject_kind,
                subject_id=subject_id,
                wall_ms=self.monotonic_ms() - started,
                role=role.value,
                model=self.config.model_key_for(role.value),
                prompt_version=prompt.prompt_version,
                hop=hop,
                **prompt_fields,
                **retry_fields,
                **response_fields,
                schema=schema.__name__,
                ok=False,
                error=type(exc).__name__,
                error_detail=error_detail(exc),
                **spend,
            )
            if usage is not None:
                if gpu_ms is None:
                    # 성공 경로와 같은 규칙 — 미측정(`None`)은 "안 썼다"가 아니라 "안 쟀다"다.
                    # **지금 이 배선에선 도달 불가다(#82 리뷰 F16):** 소비를 실어 올리는 예외
                    # 둘(`openai_compat`의 두 raise)이 모두 `gpu_ms`에 정수를 넣는다. 성공 경로
                    # (`result.gpu_ms is None`, Fake)와 규칙을 맞춘 방어 코드이고, 언젠가
                    # `usage`만 실은 예외가 생기면 이 자리가 비교표 제외 판정의 입력이 된다.
                    name = role.value
                    self.gpu_unmeasured_roles[name] = self.gpu_unmeasured_roles.get(name, 0) + 1
                self.ledger.charge(usage, stage=stage, gpu_ms=gpu_ms)
            raise
        # 재시도로 살아난 성공도 실제로 간 요청을 남긴다(#80). 재시도가 없으면 빈 dict라
        # 이벤트 키가 늘지 않는다.
        retry_fields = self._record_retries(
            role=role,
            stage=stage,
            prompt=prompt,
            retry_bodies=result.retry_bodies,
            hop=hop,
            history=history,
        )
        # 재시도로 살아난 호출의 버려진 시도 원문(#618) + `keep_responses`가 고른 역할의 최종 응답
        # (#612). 칸은 시도 번호와 정렬된다(마지막 칸 = 성공 시도). 실패 경로와 같은 가드다 — 이미
        # 끝난 호출(수십 분)의 진단 기록 쓰기가 결과를 죽이면 안 되고, 쓰기 실패는 사유로 남는다.
        kept = result.failed_raw_texts
        if role.value in self.keep_responses:
            kept = (*kept, result.raw_text)
        try:
            response_fields = self._record_failed_responses(
                role=role, stage=stage, raw_texts=kept, hop=hop
            )
        except Exception as record_exc:
            errno = f"(errno={record_exc.errno})" if isinstance(record_exc, OSError) else ""
            response_fields = {"response_record_error": f"{type(record_exc).__name__}{errno}"}
        self.trace.emit(
            EventKind.MODEL_CALL,
            stage=stage,
            subject_kind=subject_kind,
            subject_id=subject_id,
            wall_ms=self.monotonic_ms() - started,
            role=role.value,
            model=result.model,
            prompt_version=prompt.prompt_version,
            hop=hop,
            **prompt_fields,
            **retry_fields,
            **response_fields,
            schema=schema.__name__,
            ok=True,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_tokens=result.usage.cached_tokens,
            # GPU 시간과 **그 출처**를 함께 남긴다. 라벨이 기록에 없으면 반년 뒤 이 수치가
            # wall-clock 근사였다는 사실을 데이터만 보고 알 수 없다
            # (`ModelResult.gpu_ms_source` 주석 — 순차 실행 전제에 의존한다).
            gpu_ms=result.gpu_ms,
            gpu_ms_source=result.gpu_ms_source,
            attempts=result.attempts,
            # `output_tokens`는 위 시도들의 합이다. 시도별 내역과 종료 사유는 여기(#550).
            **_attempt_fields(result.attempt_log, _success_attempt_texts(result)),
        )
        # 모델 호출은 지금까지 GPU 시간을 **한 번도 청구하지 않았다** — `ModelResult`에 그
        # 값이 없었기 때문이다(2026-08-17 확인). GPU를 가장 많이 쓰는 경로가 장부에 0으로
        # 남았고, 그래서 `gpu_seconds` 상한이 한 번도 걸린 적이 없다.
        if result.gpu_ms is None:
            # 세는 것은 "GPU를 안 썼다"가 아니라 **"안 쟀다"**다. Fake 모델이 여기 들어오고,
            # 러너가 실서빙 역할과 교집합해서 비교표 제외 판정을 낸다.
            name = role.value
            self.gpu_unmeasured_roles[name] = self.gpu_unmeasured_roles.get(name, 0) + 1
        self.ledger.charge(result.usage, stage=stage, gpu_ms=result.gpu_ms)
        # **`produced_by`를 하네스가 stamp한다.** 그것은 어느 역할·모델·프롬프트 버전이
        # 만들었는지(evaluation.md §2.3 조건 비교의 기반)인데, 모델은 자기 model 이름도
        # prompt_version도 모른다. 그런데 `produced_by`가 Finding·Decision의 필수 필드라
        # 실서빙에서는 모델이 아무 값이나 채워 넣었다 — Fake는 하네스가 채우는데(fake_model)
        # 그 비대칭이 결함이었다(@0xM1yu 지적). 여기서 덮어 실서빙도 Fake와 같아진다.
        #
        # `produced_by`가 있는 스키마(Finding·Decision)만 덮는다. `model`은 `result.model`을
        # 쓴다 — fallback이 동작하면 요청 모델과 다를 수 있고 그것이 실제로 응답한 모델이다
        # (`ModelResult.model` 주석). Fake는 `result.model`이 이미 `model_name`이라 무변경이다.
        #
        # **`model_copy(update=)`는 저장소가 두 곳에서 금지한 호출인데(`common.py`
        # `HarnessModel` docstring · `finding.py` `Finding` docstring, 둘 다 Finding을 이름으로
        # 지목한다) 이 자리에서만 안전하다.** 금지의 이유는 그것이 `model_validator`를
        # 우회한다는 것인데, `produced_by`를 읽는 검증기가 없다 —
        # `Finding._promoted_states_need_backing`이 보는 것은 `state`·`repro_spec`·
        # `evidence_refs`·`path`뿐이고 `Decision`에는 `model_validator` 자체가 없다. 들어오는
        # 값도 파싱 시점에 이미 검증됐다. **상태 전이는 여기서도 예외가 아니다** — `state`를
        # 이 방식으로 바꾸면 검증기가 조용히 우회되므로 `transition()`을 써야 한다.
        # `transition()`을 여기 쓰지 않는 것은 반환 타입이 `HarnessModel`이라
        # `ModelResult[SchemaT]`의 `SchemaT`가 죽기 때문이고, 그 비용을 질 이유가 없다.
        if "produced_by" in type(result.value).model_fields:
            stamped = result.value.model_copy(
                update={
                    "produced_by": ProducedBy(
                        role=role, model=result.model, prompt_version=prompt.prompt_version
                    )
                }
            )
            result = result.model_copy(update={"value": stamped})
        # **`scout_member`도 하네스가 stamp한다 — 같은 논법이다** (ADR 0010 §3).
        #
        # 4단계 Scout 풀은 대상 하나를 멤버 셋이 각각 보고, 그 후보들이 합쳐지지 않은 채
        # 5단계로 간다. "어느 멤버가 냈나"가 그래서 후보마다 남아야 하는데, 모델은
        # `produced_by`와 똑같이 자기가 어느 자리에서 불렸는지 모른다. 스키마에 필드가
        # 있으면 구조화 출력이 그것을 채우려 들므로 **비어 오기를 기대하지 않고 매번
        # 덮는다** — 모델이 적은 멤버가 살아남으면 첫 run의 공동오답 행렬이 관측이 아니라
        # 모델의 자기소개가 되고, 그 행렬이 모델을 Scout 자리 사이에서 옮기는 유일한
        # 근거다(docs/research/2026-09-08-roster.md §7).
        #
        # 값은 `scout_member_of`가 만든다 — 풀 멤버 역할이면 그 역할 키, 풀이 없는 `scout`
        # 한 역할이면 `None`이다. 그 `None`은 "안 쟀다"가 아니라 "이 run에 풀이 없었다"는
        # 관측이고, 그래서 여기서도 조건부로 건너뛰지 않는다(`candidate.py` 필드 주석).
        #
        # `model_copy(update=)`가 이 자리에서 안전한 근거는 위 `produced_by` 블록과 같다 —
        # `Candidate`에는 `model_validator`가 없고 `scout_member`를 읽는 검증기도 없다.
        # 넣는 값도 스키마와 **같은 패턴**(`SCOUT_MEMBER_PATTERN`)에서 나오므로 검증 우회가
        # 값을 느슨하게 하지 않는다.
        if "scout_member" in type(result.value).model_fields:
            tagged = result.value.model_copy(
                update={"scout_member": scout_member_of(role.value)}
            )
            result = result.model_copy(update={"value": tagged})
        return result

    def stamp_issued(
        self,
        value: SchemaT,
        issued: Mapping[str, object],
        *,
        stage: Stage,
        subject_kind: str,
        subject_id: str,
    ) -> SchemaT:
        """**하네스가 발급한 값을 모델 출력 위에 도장 찍는다** (이슈 #279).

        논거는 `call_model`의 `produced_by`·`scout_member` 도장과 같다 — 그 값의 원천이 모델이
        아니라 하네스다. ID(`candidate_id`·`finding_id`)는 노드가 `ids.next()`로 발급해 사실
        블록에 싣고, `run_id`·`artifact_id`도 사실 블록의 값이며, 생성 시점 `state`는 전이 표의
        생성 행(C-1 `candidate`·F-1 `hypothesized`)이 정한다. 모델은 그것을 베낄 뿐인데 스키마가
        그 필드를 요구하니 구조화 출력이 채운다. 안 베끼면 두 갈래로 샌다:

        - `state` — 스키마가 받는 값(`triaged`·`queued` 등)이라 파싱은 통과하고, `create()`의
          생성 행 조회에서 `IllegalTransition`이 난다. 그 예외를 **이름으로 잡는 곳이 없어** run이
          거기서 끝난다.
          ⏵ 2026-10-02 정정: 전 판은 *"매니페스트 없이 끝난다"*였다. 러너의 최후 방어선이
          **매니페스트는 남긴다**(사유 `unforeseen_failure`, `report.md`는 없다) — 그래도 run은
          실패이고 이 가드가 필요한 이유는 그대로다(이슈 #296 인접 — 모델이 쓴 `state`·ID 대조).
        - ID·`run_id` — 저장소 `put()`이 `(kind, obj_id)` upsert라 남의 ID면 **예외 없이** 그
          행을 덮는다. `transition`의 `subject_id`도 모델 값을 따라간다.

        그래서 맞아 오기를 기대하지 않고 **저장 전에 매번 덮는다.** 부르는 곳은 둘이다 —
        Scout의 `_call_hop`(v1·v2·v3, hop0·hop k 전부)과 Auditor의 `run`. Critic은 처음부터
        ID를 모델 스키마에서 뺐다(`DecisionDraft`).

        **달랐던 필드는 덮기 전에 `issued_override`로 남긴다** — 모델이 사실 블록을 안 따랐다는
        관측이고, 덮고 나면 그 사실이 산출물 어디에도 없다. 모델이 쓴 원래 값을 `written.<필드>`로
        싣는다 — `TraceValue`가 스칼라라 리스트는 `,`로 잇는다(쉼표가 든 ID는 경계가 안 갈린다 —
        `_trace_scalar`). 같으면 이벤트가 없다.

        **재검증으로 덮는다 — `model_copy(update=)`가 아니다.** `state`를 덮으므로 위 `produced_by`
        블록이 적은 경계에 걸린다 — 그 호출은 `Finding._promoted_states_need_backing`을 우회한다.
        `HarnessModel.transition()`과 같은 식이되 반환 타입(`SchemaT`)을 살리려고 여기서 직접 쓴다.

        **`location`은 덮지 않는다** — 발급값이 아니라 모델이 사실 블록의 대상을 옮겨 적는 값이다.
        ⏵ 2026-10-13 — 단 Scout 후보의 `location.symbol`이 `null`이면
        `nodes/scout._stamp_target_symbol`이 그 줄을 품는 함수 이름으로 채운다(이슈 #633).
        이 메서드를 지난 **뒤**의 별도 도장이고 이벤트도
        `issued_override`가 아니라 `scout_symbol_stamped`다.
        """
        differed = [name for name, want in issued.items() if getattr(value, name) != want]
        if differed:
            self.trace.emit(
                EventKind.ISSUED_OVERRIDE,
                stage=stage,
                subject_kind=subject_kind,
                subject_id=subject_id,
                fields=",".join(differed),
                **{f"written.{name}": _trace_scalar(getattr(value, name)) for name in differed},
            )
        return type(value).model_validate({**value.model_dump(), **issued})

    def _record_prompt(
        self,
        *,
        role: Role,
        stage: Stage,
        prompt: Prompt,
        hop: int = 0,
        history: Sequence[ChatTurn] = (),
    ) -> dict[str, TraceValue]:
        """프롬프트를 산출물로 남기고 `model_call`에 실을 참조를 돌려준다 (해소).

        **`prompts`가 없으면 빈 dict다.** `prompt_ref=None`을 늘 싣지 않는 것은 그러면
        기록 장치가 없던 run과 프롬프트를 못 쓴 run이 trace에서 같은 모양이 되기
        때문이다 — `graph_index`가 "없다"와 "0건"을 가르는 것과 같은 이유다.

        본문은 trace에 싣지 않는다. 그 판단의 근거는 `prompt_log` 모듈 docstring에 있다.
        `hop`은 파일명(`_hop{k}`)에 쓰인다. history는 **내용**째 파일에 실린다 — 서버에 간
        요청이 `[system]+history+[이번 turn]`이라서다(`prompt_log.render`). 사슬은
        `model_call`의 (`subject_id`, `role`, `hop`)이 든다.
        """
        if self.prompts is None:
            return {}
        ref, digest = self.prompts.write(
            role=role.value, stage=int(stage), prompt=prompt, hop=hop, history=history
        )
        # `prompt_sha256`은 **1차 시도**의 것이다. 재시도가 있으면 실제로 간 것과 다르므로,
        # `_record_retries`가 `sent_sha256`(마지막으로 간 것)을 함께 싣는다(#80).
        return {"prompt_ref": ref, "prompt_sha256": digest}

    def _record_retries(
        self,
        *,
        role: Role,
        stage: Stage,
        prompt: Prompt,
        retry_bodies: tuple[str, ...],
        hop: int = 0,
        history: Sequence[ChatTurn] = (),
    ) -> dict[str, TraceValue]:
        """재시도 회차(2~N차)의 프롬프트를 산출물로 남기고 `model_call`에 실을 필드를 돌려준다.

        1차는 `_record_prompt`가 이미 남겼다. 이것은 그 뒤에 **실제로 서버에 간** body들을
        각각 `000N_role_retryK.md`로 남긴다 — 그 파일이 곧 실제 요청이라 재현·누출검증이
        된다(#80). 파일명은 1차 순번을 공유해 `prompt_ref`에서 유추된다.

        `sent_sha256`은 **마지막으로 간 요청**의 해시다 — `prompt_sha256`(1차)이 실제 간
        것과 다를 때 그 오도를 없앤다. 재시도가 없으면 빈 dict라 `prompt_sha256`이 곧 실제
        간 것이고, 성공 경로 이벤트에 없던 키를 억지로 늘리지 않는다.
        """
        if not retry_bodies or self.prompts is None:
            return {}
        last_digest = ""
        for index, body in enumerate(retry_bodies, start=1):
            retry_prompt = Prompt(
                prefix=prompt.prefix, body=body, prompt_version=prompt.prompt_version
            )
            _, last_digest = self.prompts.write_retry(
                role=role.value,
                stage=int(stage),
                prompt=retry_prompt,
                index=index,
                hop=hop,
                history=history,
            )
        return {"sent_sha256": last_digest}

    def _record_failed_responses(
        self, *, role: Role, stage: Stage, raw_texts: tuple[str | None, ...], hop: int = 0
    ) -> dict[str, TraceValue]:
        """시도들의 응답 원문을 `…_response[_retryK].txt`로 남기고 참조·해시를 준다.

        부르는 곳은 둘이다 — 종단 실패(#603)와, 재시도로 살아난 성공의 버려진 시도 +
        `keep_responses`가 고른 역할의 최종 응답(#618·#612). 칸 K는 K번째 시도다.
        성공 응답은 그 둘 밖에선 안 남긴다. 원문이 없으면(게이트웨이 거절 등) 빈 dict라
        이벤트 키가 늘지 않는다. 쓰다가 중간에 실패하면 앞서 쓴 파일은 참조가 안 남은 고아로 남는다.

        **`None` 칸은 파일을 안 쓰고 refs·sha의 그 자리가 JSON `null`이다** — `content: null`은 받은
        문자열이 없는 것이라 0바이트 파일이나 빈 문자열의 해시를 남기면 안 잰 값이 잰 값처럼 보인다
        (`_attempt_fields`와 같은 관례, CLAUDE.md §0). `index`는 칸 위치 그대로라 `_retryK`
        번호는 시도 번호와 정렬된다. 칸이 전부 `None`이어도 비어 있지 않으므로 `[null]`이 나가
        시도 칸과 맞는다.
        """
        if not raw_texts or self.prompts is None:
            return {}
        written = [
            None
            if text is None
            else self.prompts.write_response(
                role=role.value, stage=int(stage), index=index, hop=hop, text=text
            )
            for index, text in enumerate(raw_texts)
        ]
        return {
            "response_refs": json.dumps(
                [w[0] if w else None for w in written], separators=(",", ":")
            ),
            "response_sha256": json.dumps(
                [w[1] if w else None for w in written], separators=(",", ":")
            ),
        }

    # --- 도구 ---

    def call_tool(
        self,
        *,
        tool: str,
        args: ToolArgs,
        sandbox: SandboxSpec,
        stage: Stage,
        subject_id: str | None = None,
    ) -> Evidence:
        """도구 실행 + trace. ToolRunner는 실패해도 `Evidence(ok=False)`를 돌려준다."""
        self.ledger.ensure(stage)
        started = self.monotonic_ms()
        evidence = self.tools.run(tool, args, sandbox)
        elapsed = max(0, self.monotonic_ms() - started)
        self.store.put(evidence)
        # 도구 요청에 실은 입력은 **크기와 digest만** trace에 간다(맨 hex — `pov_hop`의
        # `poc_sha256`과 같은 형식). 원바이트는 안 간다. 키 부재 = 입력 없음.
        sent: dict[str, TraceValue] = {}
        if args.input_bytes is not None:
            sent = {
                "input_bytes": len(args.input_bytes),
                "input_sha256": hashlib.sha256(args.input_bytes).hexdigest(),
            }
        if evidence.harness_note is not None:  # `error`처럼 있을 때만 — 키 부재 = 노트 없음(#573)
            sent["harness_note"] = evidence.harness_note
        # 크래시 관측(#232)은 **잰 도구에만** 키가 실린다 — 키 부재 = 안 쟀다(Fake·정적 도구·대상이
        # 안 뜬 호출). `crash_banner`는 배너 줄을 Evidence에 남겼는가이고 본문은 trace에 안 간다.
        observed: dict[str, TraceValue] = {}
        if evidence.crashed is not None:
            observed = {"crashed": evidence.crashed, "crash_banner": evidence.banner is not None}
        self.trace.emit(
            EventKind.TOOL_CALL,
            stage=stage,
            subject_kind="evidence",
            subject_id=evidence.evidence_id,
            wall_ms=elapsed,
            tool=tool,
            argv=" ".join(args.argv),
            ok=evidence.ok,
            exit_code=evidence.exit_code,
            error=evidence.error,
            finding_id=subject_id,
            **sent,
            **observed,
        )
        # **밀리초를 그대로 넘긴다.** 여기서 `// 1000`을 하던 것이 이 결함의 세 지점 중
        # 하나였다 — 도구 11건 중 8건(342~906 ms)이 0으로 청구됐다(2026-08-17 실측).
        # 단위 변환은 원장이 한다.
        #
        # **단, Fake 런타임의 시간은 청구하지 않는다.** `FakeToolRunner`의 1~2 ms는 GPU도
        # 컨테이너도 아니고 **하네스가 자기 자신을 잰 값**이다. 그것을 장부에 넣으면 무의미한
        # 숫자가 비용 지표에 섞이고, 같은 입력의 두 실행이 다른 기록을 남긴다. 예전에는
        # `// 1000`이 그 값을 전부 0으로 만들어 **내림이 재현성을 우연히 지켜 주고
        # 있었다** — 이 분기가 그 우연을 의도로 바꾼다.
        #
        # 판단 기준은 새로 만들지 않았다. `doctor`가 이미 같은 식으로 갈라
        # "tool_runner가 Fake라 컨테이너 런타임이 필요 없다"를 판정한다(`cli._check_tool_runtime`).
        # 어댑터를 import 하지 않고 config를 읽는 것도 이 파일의 기존 방식 그대로다
        # (`self.config.model_key_for(...)`).
        #
        # **`--tool-runner` 오버라이드와 갈라질 수 없다.** 오버라이드는 `cli.load_profile()`이
        # config를 만들 때 이미 반영하고(`adapters.transition(tool_runner=...)`), 러너는
        # `RunRecord.tool_runner`에 **같은 식**을 박는다. 여기서 읽는 값과 기록에 남는 값이
        # 같은 표현이라 어긋날 수 없다 — 오버라이드가 나중에 적용되는 구조였다면 이 분기가
        # 조용히 틀린 값을 만들었을 것이므로, 그렇지 않은 이유를 여기 남긴다.
        #
        # **반대 선례 하나를 처리해 둔다.** `vllm_retrieval._estimated_usage`는 "Fake라고
        # 0으로 두지 않는다"고 적는다. 구분은 재현성이다 — 토큰은 입력의 결정론적
        # 함수(`len // 4`)라 Fake에서도 같은 입력이면 같은 값이지만, wall-clock은 아니다.
        #
        # **`gpu_ms`가 아니라 `tool_ms`다.** 여기서 도는 네 도구(semgrep·clang-tidy·build·
        # asan)는 GPU를 쓰지 않는다. 2026-08-17까지 이 시간이 `gpu_seconds`로 청구됐고,
        # 그래서 그 필드의 0 아닌 값은 **전부 CPU 시간**이었다 — evaluation.md §5.3의
        # "finding당 GPU-hour"가 그 값을 분자로 쓴다.
        is_fake_runtime = self.config.profile.adapters.tool_runner == "fake"
        self.ledger.charge(_NO_TOKENS, stage=stage, tool_ms=None if is_fake_runtime else elapsed)
        return evidence

    # --- 게이트 ---

    def evaluate_gate(
        self,
        finding: Finding,
        evidences: Sequence[Evidence],
        decisions: Sequence[Decision] = (),
        missings: Sequence[MissingEvidence] = (),
        *,
        stage: Stage,
        candidate_location: Location | None = None,
        cited_by_model: int | None = None,
    ) -> GateResult:
        """순수 판정(gate.evaluate) + `gate_eval`·`gate_ambiguous` 기록.

        `ambiguous`는 기록만 하고 모델을 호출하지 않는다 — 등급은 E1로 확정된다.

        **`stage`는 `GATE_STAGES` 안이어야 한다.** E3은 Critic 출력이, E4는 도구 검증
        로그가 있어야 성립하므로 게이트는 그 세 단계에서만 뜻이 있다. 그 밖에서 부르면
        아직 성립할 수 없는 술어를 거짓으로 기록하게 되고, 그 행이 제거율의 분모에
        섞인다. 여기서 막지 않으면 `GATE_STAGES`는 **아무도 안 보는 상수**가 된다 —
        실제로 그랬고, 그동안 8단계에는 호출이 아예 없었다(W3).

        **`cited_by_model`은 하네스가 부착하기 *전*에 센 모델 자신의 인용 수다 (이슈 #554).**
        `gate_node.attach_observed`가 `Finding.evidence_refs`에 관측 Evidence를 덧붙여 `state.db`
        행을 덮으므로 부착 뒤에는 모델의 원본을 셀 수 없다. 6단계(`gate_node.run`)만 넘긴다 —
        그 시점의 `evidence_refs`는 5단계가 모델 출력에서 옮긴 그대로이기 때문이다. **넘기지
        않으면 `gate_eval`에 키가 없다**(`None`을 쓰지 않는다 - 키 부재 = 안 쟀다, CLAUDE.md §0).
        0은 「쟀더니 0건」이다. 7·8단계는 넘기지 않는다: 그 시점의 목록에는 하네스 부착분이
        섞여 있어 「모델이」라는 이름이 거짓이 된다.
        """
        if stage not in GATE_STAGES:
            raise ValueError(
                f"게이트는 {[int(s) for s in GATE_STAGES]}단계에서만 판정한다: {int(stage)}"
            )
        result = gate.evaluate(
            finding, evidences, decisions, missings, candidate_location=candidate_location
        )
        measured = {} if cited_by_model is None else {"cited_by_model": cited_by_model}
        self.trace.emit(
            EventKind.GATE_EVAL,
            stage=stage,
            subject_kind="finding",
            subject_id=finding.finding_id,
            grade=result.grade.value,
            **dict(result.predicates),
            **measured,
        )
        if result.ambiguous:
            self.trace.emit(
                EventKind.GATE_AMBIGUOUS,
                stage=stage,
                subject_kind="finding",
                subject_id=finding.finding_id,
                grade=result.grade.value,
                boundary="E1-E2",
            )
        return result

    def record_critic_normalized(self, decision: Decision, *, stage: Stage) -> None:
        """근거 없는 `rebut` → `uphold` 정규화를 지표에 남긴다 (agent-contracts.md §2.3).

        분모가 `model_call(role=critic)`이므로 판정과 별개의 이벤트로 남긴다.
        """
        self.trace.emit(
            EventKind.CRITIC_NORMALIZED,
            stage=stage,
            subject_kind="decision",
            subject_id=decision.decision_id,
            finding_id=decision.finding_id,
            original_verdict=decision.verdict.value,
        )

    def record_critic_verdict_unmapped(
        self, *, decision_id: str, finding_id: str, raw_verdict: str, stage: Stage
    ) -> None:
        """Critic verdict를 표준값으로 **못 읽어** `need_evidence`(보류)로 보낸 것을 남긴다.

        자유 verdict(enum grammar 회피)가 표준 어휘도 명백한 동의어도 아니면 매핑이
        `None`이고 `to_decision`이 `need_evidence`로 보낸다. 원문을 남겨 그 판정을 사후에
        감사할 수 있게 한다(§0 — 판정을 만드는 변환은 기록한다).
        """
        self.trace.emit(
            EventKind.CRITIC_VERDICT_UNMAPPED,
            stage=stage,
            subject_kind="decision",
            subject_id=decision_id,
            finding_id=finding_id,
            raw_verdict=raw_verdict,
            mapped_verdict=Verdict.NEED_EVIDENCE.value,
        )

    def record_cache(
        self, key: str, hit: bool, *, cache: str = "capsule", counted_by: str | None = None
    ) -> None:
        """캡슐 캐시 적중 기록 (knowledge-base.md §6). Retriever의 `on_cache` 훅에 연결된다.

        `counted_by`는 **그 공유 캡슐을 어느 역할 카운터로 계산했나**다(#192 조건②) — Scout 풀에서
        2·3번 멤버가 첫 멤버의 캡슐을 물려받을 때, 그 캡슐의 토큰이 무엇으로 세어졌는지를 남긴다.
        풀이 아닌 경로(Retriever `on_cache`·단일 프로필)에서는 `None`이라 이벤트에 안 실린다.
        """
        data: dict[str, object] = {"cache": cache, "key": key, "hit": hit}
        if counted_by is not None:
            data["counted_by"] = counted_by
        self.trace.emit(EventKind.CACHE, stage=self.current_stage, **data)

    def record_graph_index(self, fields: dict[str, TraceValue]) -> None:
        """1단계 인덱서 산출물의 로드 결과. Retriever의 `on_index` 훅에 연결된다.

        **`unresolved_*`를 캡슐이 아니라 여기로 보낸다.** 그 값들은 저장소 전체의
        스칼라라 후보마다 달라지지 않고 토큰 비용도 없다 — 섹션으로 실으면 텍스트가
        아닌 숫자가 토큰 회계에 들어간다. 반면 "그 저장소에서 E2를 얼마나 믿을지"는
        지표 해석의 입력이고, **여기 남지 않은 사건은 측정할 수 없다**(trace.py).

        `status`가 함께 실리는 것이 요점이다. 인덱서가 안 돈 것(`missing`)과 형식이
        어긋난 것(`unsupported_version`)과 실제로 간선이 0건인 것은 대응이 전부 다르다.

        **dataclass가 아니라 dict를 받는다.** 어댑터 타입을 여기서 import 하면 "노드는
        구체 어댑터를 모른다"가 깨진다(engineering-guide.md §3). 필드 이름을 만드는 쪽은
        `index_graph.IndexSummary.as_trace_fields()`이고 테스트가 그 집합을 고정한다.
        """
        self.trace.emit(EventKind.GRAPH_INDEX, stage=self.current_stage, **fields)

    def record_token_counter(self, fields: dict[str, TraceValue]) -> None:
        """이 run이 어느 토큰 카운터로 셌는가 (#96 (b)). **run당 정확히 1건이다.**

        `record_graph_index`와 같은 모양이다 — 어댑터 타입이 아니라 **스칼라 dict**만 경계를
        넘는다(engineering-guide.md §3). 필드를 만드는 쪽은 `factory.counter_report()`이고
        테스트가 그 키 집합을 고정한다.

        **`graph_index`와 다른 점은 발행 시점이다.** 그쪽은 Retriever가 그래프를 처음 조회할
        때 훅으로 실려 0건일 수 있는데, 카운터는 `build_deps` 시점에 이미 정해져 있으므로
        **모델을 부르기 전에 무조건 낸다**(`target_plan`과 같은 계약). 그 뒤 어디서 죽어도
        "무엇으로 쟀는가"는 남는다.

        이 이벤트가 없던 동안에는 폴백이 조용했다 — `tokenizers`가 없거나 `tokenizer.json`이
        없으면 글자수 근사로 돌면서 trace에도 `RunRecord`에도 아무것도 안 남았고, 그래서
        2026-08-26 재측정은 run **전에** 사람이 손으로 세 모델을 확인해야 했다.
        """
        self.trace.emit(EventKind.TOKEN_COUNTER, stage=self.current_stage, **fields)

    def record_model_swap(
        self,
        keys: tuple[str, ...],
        *,
        stage: Stage | None,
        ok: bool,
        wall_ms: int,
        report: Mapping[str, TraceValue],
        error: str | None = None,
    ) -> None:
        """단계 경계 교대 한 번 (ADR 0012 Phase 6.5). **성공·실패 모두** 한 줄이다 — 부르는 곳은
        `runner._swap`이고, 실패는 `ModelSwapError`일 때만 여기 온다(그 밖의 예외는 기록 없이
        올라간다).

        `stage`는 이 교대가 **준비하는** 단계다 — run 끝 내림은 준비하는 단계가 없어 `None`이고
        `keys`가 빈다. `current_stage`를 쓰지 않는 것은 교대가 단계에 들어가기 **전**에 일어나서다.

        `report`는 교대 명령의 보고(어댑터가 스칼라로 편 것)이고 `report.` 아래로 싣는다 — 명령도
        `ok`·`stage`·`wall_ms`를 보고하므로 그대로 펴면 하네스가 잰 값과 이름이 겹친다. 어댑터
        타입이 아니라 스칼라 dict만 받는 것은 `record_token_counter`와 같은 이유다.
        """
        self.trace.emit(
            EventKind.MODEL_SWAP,
            stage=stage,
            wall_ms=wall_ms,
            models=" ".join(keys),
            ok=ok,
            error=error,
            **{f"report.{key}": value for key, value in report.items()},
        )

    def record_retrieval(self, component: str, result: RetrievalResult, candidates: int) -> None:
        """검색 호출의 비용을 trace와 예산에 반영한다. Retriever의 `on_usage` 훅에 연결된다.

        `model_call`이 아니라 `retrieval_call`로 남긴다 — trace.py 대응표의 "스키마 파싱
        실패율" 분모가 `model_call` 전체이고, 스키마가 없는 검색 호출이 섞이면 그 값이
        조용히 희석된다. embedding·reranker는 `Role`도 아니다(ADR 0004).
        """
        stage = self.current_stage
        self.trace.emit(
            EventKind.RETRIEVAL_CALL,
            stage=stage,
            component=component,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cached_tokens=result.usage.cached_tokens,
            gpu_ms=result.gpu_ms,
            gpu_ms_source=result.gpu_ms_source,
            candidates=candidates,
        )
        self.ledger.charge(result.usage, stage=stage, gpu_ms=result.gpu_ms)

    def record_retrieval_truncated(self, collected: int, kept: int) -> None:
        """광역 수집 절단을 trace에 남긴다. Retriever의 `on_truncate` 훅에 연결된다.

        **예산에는 청구하지 않는다.** 잘려 나간 조각은 임베딩에 넘어가지도 않았으므로
        비용이 발생하지 않았고, 여기서 장부를 건드리면 `retrieval_call`이 이미 청구한
        것과 겹쳐 센다. 이 이벤트가 남기는 것은 비용이 아니라 **범위**다.

        "전부 봤다"와 "상한까지만 봤다"는 다른 사건인데, 이 훅이 배선되기 전에는 캡슐도
        trace도 그 둘을 구별하지 못했다(#63, W7).
        """
        self.trace.emit(
            EventKind.RETRIEVAL_TRUNCATED,
            stage=self.current_stage,
            collected=collected,
            kept=kept,
        )

    # --- 정책과 전이 ---

    def decide(self, request: RoutingRequest) -> Action:
        """정책 판정 + `policy_decision` 기록. 노드는 임계값을 직접 비교하지 않는다."""
        action = self.policy.decide(request)
        self.trace.emit(
            EventKind.POLICY_DECISION,
            stage=request.stage,
            subject_kind=request.subject_kind,
            subject_id=request.subject_id,
            point=request.point.value,
            policy=action.policy,
            route=action.route.value,
            to_state=action.to_state.value if action.to_state else None,
            reason=action.reason_code.value if action.reason_code else None,
            **{f"basis.{key}": value for key, value in action.basis.items()},
        )
        return action

    def apply(
        self,
        subject: state_machine.Subject,
        *,
        to: FindingState,
        reason: ReasonCode,
        stage: Stage,
    ) -> state_machine.Subject:
        """상태 전이. `state_machine.apply()`로만 간다."""
        return state_machine.apply(
            subject, to=to, reason=reason, stage=stage, store=self.store, trace=self.trace
        )

    def apply_action(
        self, subject: state_machine.Subject, action: Action, *, stage: Stage
    ) -> state_machine.Subject:
        """정책이 상태를 정한 경우의 지름길. `to_state`가 없으면 그대로 둔다(EXPAND 등)."""
        if action.to_state is None or action.reason_code is None:
            return subject
        return self.apply(subject, to=action.to_state, reason=action.reason_code, stage=stage)

    def create(
        self,
        subject: state_machine.Subject,
        *,
        reason: ReasonCode | None,
        stage: Stage,
    ) -> state_machine.Subject:
        """신규 객체 저장 + 생성 기록 (전이 표 C-1·F-1)."""
        return state_machine.create(
            subject, reason=reason, stage=stage, store=self.store, trace=self.trace
        )
