"""4단계 Scout / Triage — 후보를 만들고 승격 여부를 정책에 묻는다.

전이 표 C-1(생성) → C-2/C-3(승격·보류). 임계값(`scout_threshold`)은 노드가 아니라
정책이 본다. 탈락한 후보는 폐기하지 않고 `queued`로 남는다.

`previous`는 `queued`에서 재평가로 돌아온 후보다(표 C-8). 그 경우에만 C-4(파싱 실패)와
C-7(예산 소진)이 성립한다 — 첫 호출에서는 아직 후보 객체가 없어 전이시킬 대상이
없으므로 예외가 그대로 올라가고 러너가 run을 정리한다.

## 세션 분할 (ADR 0012 결정 2 — need_evidence 되묻기)

v3 프롬프트는 판정에 더해 `triage_need_evidence` 신호를 낼 수 있다(`scout_response.signal_of`).
그 신호를 받으면 좌석 집계 루프(러너)가 같은 세션에서 되물어야 하므로, "모델을 부르고
바로 후보를 만드는" 한 함수(`run`)를 셋으로 쪼갠다.

- `open_session` — hop0 호출. **후보를 만들지 않는다** — candidate_id만 발급한다.
- `continue_session` — hop k(k≥1). 되묻기 응답(`ResolveQuery`/`Resolution`은
  `src/schemas/need_evidence.py`)을 다음 user 턴으로 보낸다.
- `finish` — 세션의 마지막 판정으로 후보를 만들고(C-1) 트리아지를 적용한다(C-2/C-3).

`run() = open_session() + finish()`다 — 신호가 없으면(v1·v2, 세 신호 밖 접두) 지금 점수
경로 그대로다. 루프 자체(정지 판정·resolver 호출·`SCOUT_HOP` 기록)는 여기 없다 — 노드는
세션 하나를 다루고, 홉을 몇 번 돌지·언제 멈출지는 좌석 집계가 정한다(runner.py).
"""

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from src.interfaces.model_client import ChatTurn, Prompt, Usage
from src.orchestrator.budget import BudgetExhausted
from src.orchestrator.context import RunContext
from src.orchestrator.entry_scope import enclosing_scope
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.prompt_loader import PromptError, build_facts, load_prompt
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind
from src.schemas.artifact import Artifact
from src.schemas.candidate import Candidate, SliceRef, scout_member_of
from src.schemas.capsule import ContextCapsule
from src.schemas.common import Location
from src.schemas.enums import FindingState, ReasonCode, Role, ScoutSignal
from src.schemas.evidence import Evidence
from src.schemas.scout_response import AnalysisSummary, RequestedContext, ScoutResponse, signal_of

ROLE = Role.SCOUT
# 이 역할이 낼 수 있는 구조화 출력 스키마. **버전별로 갈린다** — v1·v2 프롬프트는
# `Candidate`를 바로 내고, v3는 `ScoutResponse`(근거 래퍼 `{candidate, analysis_summary}`)를
# 낸다. 근거를 써야 Scout 판정이 정확해진다는 실측이 v3의 근거다.
#
# 어느 것을 요청할지는 노드가 박지 않고 **프롬프트 meta가 선언한 것**을 따른다
# (`_ask_model` → `_resolve_schema`). 그래야 같은 역할에 v2(Candidate)와 v3(ScoutResponse)가
# 공존한다 — dev·테스트·실서빙 단일 scout은 v1/v2를 그대로 쓰고, 풀 v3만 래퍼다.
#
# 그래도 **아무 스키마나 받지는 않는다.** meta가 이 집합 밖을 선언하면 `_resolve_schema`가
# 실패한다. 프롬프트와 grammar가 어긋나면 `additionalProperties: false` 마스크가 추론을
# 덮으므로(`tests/test_config.py`의 output_schema 검사), 이 집합이 그 가드레일의 폭이다.
ALLOWED_OUTPUT_SCHEMAS: tuple[type[Candidate] | type[ScoutResponse], ...] = (
    Candidate,
    ScoutResponse,
)
_SCHEMA_BY_NAME = {schema.__name__: schema for schema in ALLOWED_OUTPUT_SCHEMAS}

# 이 노드(`run`)가 도는 4단계 자리 전부 — 단일 `scout`과 풀 세 멤버. 전부 같은 `_ask_model`을
# 타므로 버전별 스키마 해석도 같다. bench가 같은 자리를 잴 때 이 집합으로 scout 경로를
# 가른다(`bench_report`).
SCOUT_ROLES: frozenset[Role] = frozenset(
    {Role.SCOUT, Role.SCOUT_MEMORY, Role.SCOUT_BOUNDARY, Role.SCOUT_INJECTION}
)


def resolve_output_schema(name: str) -> type[Candidate] | type[ScoutResponse]:
    """프롬프트 meta의 `output_schema` 이름을 이 역할이 허용하는 스키마로 푼다.

    집합 밖 이름은 조용히 넘기지 않는다 — grammar가 프롬프트와 싸우는 상태를
    런타임 첫 호출(=GPU 시간)까지 미루지 않고 여기서 사유와 함께 세운다.
    """
    try:
        return _SCHEMA_BY_NAME[name]
    except KeyError:
        raise PromptError(
            f"scout meta의 output_schema `{name}`은 이 역할이 낼 수 없다 — "
            f"허용: {sorted(_SCHEMA_BY_NAME)}"
        ) from None


@dataclass(frozen=True)
class ScoutSession:
    """좌석 하나의 홉 기록 — **홉마다 새로 만든다**(불변, ADR 0012 결정 2).

    `candidate`·`summary`·`signal`·`raw_text`·`usage`·`latency_s`는 이 세션이 든 **마지막
    홉**의 것이다. `history`는 다음 홉으로 보낼 문답 전부 — hop0이면
    `[user hop0 body, assistant raw0]`, hop k면 그 뒤로 되묻기·응답이 두 턴씩 더 붙는다.

    `prefix`·`prompt_version`은 hop0에서 고정된다 — 시스템 prefix는 홉마다 다시 붙는
    메시지라(`interfaces/model_client.py` `ChatTurn` 주석) `continue_session`이 새로
    렌더하지 않고 그대로 재사용한다.
    """

    candidate_id: str
    role: Role
    slice: SliceRef | None
    hop: int
    candidate: Candidate
    summary: AnalysisSummary | None
    signal: ScoutSignal | None
    requested_context: tuple[RequestedContext, ...]
    raw_text: str | None
    usage: Usage
    latency_s: float
    prefix: str
    prompt_version: str
    history: tuple[ChatTurn, ...]
    # hop0에서 슬라이스가 트림됐으면 모델이 본 (file, line) 집합(#375 M-2). 되묻기 홉은 SOURCE를
    # 다시 안 싣고 hop0 본문을 재사용하므로 이 집합은 hop0에서 고정돼 세션마다 그대로 이어진다.
    # `None`은 안 잘렸다(또는 슬라이스 없음) — resolver가 `SliceRef.lines` 전체를 `visible`로 쓴다.
    shown_lines: tuple[tuple[str, int], ...] | None = None
    # hop0 프롬프트의 `FACTS.target`(= `capsule.target`) — 되묻기 홉이 후보의 `location.symbol`을
    # 채울지 가르려고 이어 쥔다(`_stamp_target_symbol`의 네 조건 중 파일·이름 둘이 이것을 본다.
    # 찍는 값은 이 심볼이 아니라 후보 줄을 품는 함수다). `None`이면 안 채운다(손으로 짠 세션).
    target: Location | None = None


@dataclass(frozen=True)
class _HopResponse:
    """모델 호출 하나(홉 하나)의 원시 결과. `ScoutSession`을 만드는 재료다."""

    candidate: Candidate
    summary: AnalysisSummary | None
    raw_text: str | None
    usage: Usage
    latency_s: float
    body: str
    prefix: str
    prompt_version: str
    # 트림됐으면 **모델에 간 (file, line) 집합**, 아니면 `None`(#375 M-2 — resolver `visible`).
    shown_lines: tuple[tuple[str, int], ...] | None = None


def run(
    ctx: RunContext,
    *,
    artifact: Artifact,
    capsule: ContextCapsule,
    evidences: tuple[Evidence, ...] = (),
    previous: Candidate | None = None,
    role: Role = ROLE,
    slice: SliceRef | None = None,
) -> Candidate:
    """후보 하나를 만들고 트리아지 결과까지 적용해 돌려준다.

    `role`은 Scout 자리를 고른다 — 풀 없는 단일 `scout`(기본)이거나 풀 멤버
    (`scout_memory`·`scout_boundary`·`scout_injection`). **어느 쪽이든 단계는 4단계
    (`Stage.SCOUT`)이고 임계값·전이 경로는 같다** — 바뀌는 것은 어느 프롬프트·모델을
    부르는가뿐이다. `Candidate.scout_member` stamp는 이 `role`에서 만든다 — v1·v2는
    `RunContext.call_model`이, v3는 `_call_hop`이 찍는다(ADR 0010 §3, `candidate.py` 필드 주석).
    발급 ID·`run_id`·`artifact_id`·초기 `state`는 `_call_hop`이 `RunContext.stamp_issued`로 덮는다.

    `slice`는 슬라이스 타깃이면 그 슬라이스다(ADR 0012 결정 1) — 프롬프트가 `input: slice`를
    선언하면 `SOURCE`가 그 렌더가 되고 FACTS에 `slice_criterion`이 붙으며, `input: source`면
    entry를 품는 함수 본문이다(#529, `_ask_model`). `None`이면 심볼 본문이다.

    **`open_session()` + `finish()`다.** v3가 `triage_need_evidence`를 내도 여기서는
    되묻지 않는다 — 그 홉을 계속 돌리는 것은 좌석 집계 루프(러너)의 일이다. 이 함수를
    직접 부르는 호출자(단위 테스트·재평가 경로)는 하나의 hop0으로 끝나는 것이 지금까지의
    동작이므로 그대로 유지한다.
    """
    with ctx.stage(Stage.SCOUT):
        session = open_session(
            ctx,
            artifact=artifact,
            capsule=capsule,
            evidences=evidences,
            previous=previous,
            role=role,
            slice=slice,
        )
        return finish(ctx, session)


def open_session(
    ctx: RunContext,
    *,
    artifact: Artifact,
    capsule: ContextCapsule,
    evidences: tuple[Evidence, ...] = (),
    previous: Candidate | None = None,
    role: Role = ROLE,
    slice: SliceRef | None = None,
) -> ScoutSession:
    """hop0 호출. **후보를 만들지 않는다** — candidate_id만 여기서 1회 발급한다.

    실패 처리는 예전 `run()`과 같다 — 첫 호출은 아직 후보가 없으므로 재평가 중이던
    `previous`만 큐로 돌리고(표 C-4·C-7) 예외는 그대로 올린다.
    """
    candidate_id = ctx.ids.next("candidate")
    hop_response = _ask_model(
        ctx,
        candidate_id=candidate_id,
        artifact=artifact,
        capsule=capsule,
        evidences=evidences,
        previous=previous,
        role=role,
        slice=slice,
    )
    return _session_from_hop(
        candidate_id=candidate_id,
        role=role,
        slice=slice,
        hop=0,
        hop_response=hop_response,
        history=(),
        shown_lines=hop_response.shown_lines,
        target=capsule.target,
    )


def continue_session(ctx: RunContext, session: ScoutSession, followup_text: str) -> ScoutSession:
    """hop k(= `session.hop + 1`). 되묻기 응답을 다음 user 턴으로 보낸다.

    **오직 v3에서만 불린다** — `session.signal`이 `triage_need_evidence`일 때만 좌석 집계가
    이 함수를 부르므로, 이 홉의 스키마도 항상 `ScoutResponse`다. 실패는 여기서 처리하지
    않는다 — `previous` 큐잉이 없다(재평가 개념이 아니다). 흡수할지·정지할지는 그 예외를
    본 호출자(러너의 need_evidence 루프)가 정한다(ADR 0012 결정 2).
    """
    hop = session.hop + 1
    prompt = Prompt(
        prefix=session.prefix, body=followup_text, prompt_version=session.prompt_version
    )
    hop_response = _call_hop(
        ctx,
        candidate_id=session.candidate_id,
        # hop0이 이미 발급값으로 덮은 값이다 — 사실 블록은 hop0 본문(`history[0]`)에 한 번 실리고
        # 되묻기 홉은 그것을 다시 싣지 않으므로, 이 세션의 발급 `artifact_id`는 이것뿐이다.
        artifact_id=session.candidate.artifact_id,
        role=session.role,
        prompt=prompt,
        schema=ScoutResponse,
        history=session.history,
        hop=hop,
        target=session.target,
    )
    return _session_from_hop(
        candidate_id=session.candidate_id,
        role=session.role,
        slice=session.slice,
        hop=hop,
        hop_response=hop_response,
        history=session.history,
        # 되묻기 홉은 SOURCE를 다시 안 싣는다 — hop0에서 고정된 집합을 그대로 이어 간다(#375 M-2).
        shown_lines=session.shown_lines,
        target=session.target,
    )


def finish(ctx: RunContext, session: ScoutSession) -> Candidate:
    """세션의 마지막 판정으로 후보를 만들고(C-1) 트리아지를 적용한다(C-2/C-3).

    `session.signal`이 있으면(v3) 점수 대신 그 신호로 판정한다(ADR 0012 결정 2 —
    "판정은 최종 신호") — `policy._scout_triage`가 `signals["signal"]`을 본다. 없으면
    (v1·v2, 또는 세 신호 밖 접두) 지금 점수 경로 그대로다.
    """
    candidate = session.candidate.model_copy(
        # **`slice_id`는 하네스가 덮는다** — `scout_member`와 같은 논법이다(`Candidate.slice_id`
        # 주석). 스키마에 필드가 있어 구조화 출력이 채울 수 있으므로, 비어 오기를 기대하지 않고
        # 저장 **전에** 매번 덮는다. `None`(슬라이스 없는 run)도 덮는다 — 모델이 지어낸 id가
        # 살아남지 않게. `Candidate`에는 `model_validator`가 없어 `model_copy`가 안전하다.
        update={"slice_id": session.slice.slice_id if session.slice is not None else None}
    )
    ctx.create(candidate, reason=None, stage=Stage.SCOUT)
    signals: dict[str, float | str] = {"score": candidate.score}
    if session.signal is not None:
        signals["signal"] = session.signal.value
    action = ctx.decide(
        RoutingRequest(
            point=DecisionPoint.SCOUT_TRIAGE,
            stage=Stage.SCOUT,
            subject_kind="candidate",
            subject_id=candidate.candidate_id,
            signals=signals,
        )
    )
    return ctx.apply_action(candidate, action, stage=Stage.SCOUT)


def _session_from_hop(
    *,
    candidate_id: str,
    role: Role,
    slice: SliceRef | None,
    hop: int,
    hop_response: _HopResponse,
    history: tuple[ChatTurn, ...],
    shown_lines: tuple[tuple[str, int], ...] | None,
    target: Location | None,
) -> ScoutSession:
    requested = (
        tuple(hop_response.summary.requested_context) if hop_response.summary is not None else ()
    )
    new_history = (
        *history,
        ChatTurn(speaker="user", content=hop_response.body),
        ChatTurn(speaker="assistant", content=hop_response.raw_text or ""),
    )
    return ScoutSession(
        candidate_id=candidate_id,
        role=role,
        slice=slice,
        hop=hop,
        candidate=hop_response.candidate,
        summary=hop_response.summary,
        signal=signal_of(hop_response.candidate.reason_codes),
        requested_context=requested,
        raw_text=hop_response.raw_text,
        usage=hop_response.usage,
        latency_s=hop_response.latency_s,
        prefix=hop_response.prefix,
        prompt_version=hop_response.prompt_version,
        history=new_history,
        shown_lines=shown_lines,
        target=target,
    )


def _call_hop(
    ctx: RunContext,
    *,
    candidate_id: str,
    artifact_id: str,
    role: Role,
    prompt: Prompt,
    schema: type[Candidate] | type[ScoutResponse],
    history: Sequence[ChatTurn],
    hop: int,
    target: Location | None,
) -> _HopResponse:
    """모델 호출 하나 — hop0·hop k 공통. 예외를 그대로 올린다(호출자가 흡수 여부를 정한다)."""
    started = ctx.monotonic_ms()
    result = ctx.call_model(
        role=role,
        prompt=prompt,
        schema=schema,
        stage=Stage.SCOUT,
        subject_kind="candidate",
        subject_id=candidate_id,
        history=history,
        hop=hop,
    )
    latency_s = (ctx.monotonic_ms() - started) / 1000.0
    value = result.value
    if isinstance(value, ScoutResponse):
        # v3 래퍼: 안쪽 `candidate`를 파이프라인으로 흘린다. `analysis_summary`는 세션에 실어
        # need_evidence 루프가 `requested_context`를 읽게 하고, 근거 서술은 어디에도 안 넘긴다
        # (scout_response.py). 근거는 판정을 좋게 하는 추론 흔적이지 소비 대상이 아니다.
        # **`scout_member`는 여기서 찍는다** — `call_model`은 결과 타입에 그 필드가 있을 때만
        # 찍는데(ADR 0010 §3) `ScoutResponse`에는 없어 건너뛴다. `role`에서 유도한 값이라
        # 「모델이 아니라 하네스가 stamp」라는 그 §3 취지는 그대로다.
        candidate = value.candidate.model_copy(update={"scout_member": scout_member_of(role.value)})
        summary = value.analysis_summary
    else:
        # v1·v2: `Candidate`를 바로 낸다. `scout_member`는 `call_model`이 이미 찍었다 —
        # 그 타입에 필드가 있어 §3의 stamp 경로가 성립한다.
        candidate = value
        summary = None
    # **발급값은 하네스가 도장 찍는다**(이슈 #279) — v1·v2·v3, hop0·hop k가 전부 여기를 지난다.
    # 논거·방식은 `RunContext.stamp_issued` docstring. `finish()`가 이 후보를 `create()`하므로
    # 초기 `state`가 C-1(`candidate`)이 아니면 거기서 `IllegalTransition`이 났다.
    candidate = ctx.stamp_issued(
        candidate,
        {
            "candidate_id": candidate_id,
            "run_id": ctx.run_id,
            "artifact_id": artifact_id,
            "state": FindingState.CANDIDATE,
        },
        stage=Stage.SCOUT,
        subject_kind="candidate",
        subject_id=candidate_id,
    )
    candidate = _stamp_target_symbol(ctx, candidate, target, hop=hop)
    return _HopResponse(
        candidate=candidate,
        summary=summary,
        raw_text=result.raw_text,
        usage=result.usage,
        latency_s=latency_s,
        body=prompt.body,
        prefix=prompt.prefix,
        prompt_version=prompt.prompt_version,
    )


def _stamp_target_symbol(
    ctx: RunContext, candidate: Candidate, target: Location | None, *, hop: int
) -> Candidate:
    """후보의 `location.symbol`이 `null`이면 **그 줄을 품는 함수**의 이름을 찍는다 (이슈 #633).

    v4 prefix는 「`location`을 `FACTS.target`에서 복사하라」고 하는데 두 실모델이 `symbol`을 못
    옮겼다(s17 12/12 null). 값의 원천이 모델이 아니라 하네스가 쥔 대상이라 `stamp_issued`와 같은
    논법으로 덮는다 — 단 **넷 다 맞을 때만**이다:

    - 모델이 `symbol`을 안 썼다(`None`) — 쓴 값은 틀려도 그대로 둔다(`""`도 쓴 값이다).
    - 후보가 **대상 파일**에 있다 — 다른 파일 후보는 안 건드린다. 슬라이스 Scout는 여러 파일을
      보므로 이 경우가 실재한다.
    - 대상이 `symbol`을 든다 — 찍는 값은 이제 대상 이름이 아니라 인덱스가 아는 품는 함수라, 이것은
      그 값의 근거가 아니다. 이름 없는 대상(Case B entry 등)에서는 인덱스가 그 줄의 함수를 알아도
      **안 찍는다** — 보수적 동작이다(미측정: 이 가드가 막는 후보 수는 안 쟀다).
    - 인덱스가 후보 줄을 품는 함수를 안다(`_enclosing_symbol`) — 모르면 **안 찍는다**(CLAUDE.md §0).

    ⚠ **대상 이름을 그대로 찍지 않는다**(⏵ 2026-10-13 — 전 판은 `target.symbol`을 찍었다). 슬라이스
    타깃의 `FACTS.target`은 entry 한 줄이고(`line_end` 없음) `SOURCE`는 여러 함수에 걸치므로, 같은
    파일의 **다른 함수** 후보에 대상 이름이 붙어 `repro_spec`·`validate` argv에 실렸다. 찍는 값은
    `ctx.symbols_for`가 아는 품는 함수라 하네스가 그 줄에 대해 실제로 아는 것이다.

    `file`·`line_*`는 안 건드린다. 건드린 건마다 `scout_symbol_stamped`를 남긴다 — 모델이 쓴
    `null`과 하네스가 채운 값이 `state.db`의 후보에서는 구별되지 않아서다. `stamp_issued`의
    `issued_override`와 섞지 않았다: 그 이벤트는 「부재 = 모델이 발급값을 그대로 썼다」로 읽히고
    테스트가 0건을 단언한다.
    """
    if (
        target is None
        or not target.symbol
        or candidate.location.symbol is not None
        or candidate.location.file != target.file
    ):
        return candidate
    symbol = _enclosing_symbol(ctx, candidate.location)
    if symbol is None:
        return candidate
    ctx.trace.emit(
        EventKind.SCOUT_SYMBOL_STAMPED,
        stage=Stage.SCOUT,
        subject_kind="candidate",
        subject_id=candidate.candidate_id,
        hop=hop,
        symbol=symbol,
    )
    return candidate.model_copy(
        update={"location": candidate.location.model_copy(update={"symbol": symbol})}
    )


def _enclosing_symbol(ctx: RunContext, location: Location) -> str | None:
    """`location.line_start`를 품는 가장 좁은 심볼의 이름 — 인덱스가 없거나 못 품으면 `None`.

    `runner._narrowest_symbol`과 같은 규칙이다. 그쪽을 import하지 않는 이유는 순환이다 —
    러너가 이 모듈을 import한다. 줄이 없는 후보(`line_start`가 `None`)도 `None`이다: 품는 함수를
    고를 근거가 없고, 대상 이름으로 메우면 그것이 위 ⚠의 결함이다.
    """
    line = location.line_start
    if line is None:
        return None
    spanning = [
        symbol
        for symbol in ctx.symbols_for(location.file) or []
        if symbol.line_start is not None
        and symbol.line_start <= line <= (symbol.line_end or symbol.line_start)
    ]
    narrowest = min(
        spanning,
        key=lambda symbol: (symbol.line_end or symbol.line_start or 0) - (symbol.line_start or 0),
        default=None,
    )
    return None if narrowest is None else narrowest.symbol


def _ask_model(
    ctx: RunContext,
    *,
    candidate_id: str,
    artifact: Artifact,
    capsule: ContextCapsule,
    evidences: tuple[Evidence, ...],
    previous: Candidate | None,
    role: Role,
    slice: SliceRef | None,
) -> _HopResponse:
    """hop0 프롬프트를 렌더해 모델에 묻는다. 실패는 사유 코드로 기록하고 그대로 올린다.

    **`SOURCE`의 모양은 프롬프트 meta의 `input` 선언이 정한다**(#529) — 슬라이스 유무가 아니다.
    `slice`면 슬라이스 렌더 + `slice_criterion`(ADR 0012 결정 1), `source`면 entry를 품는 함수 본문
    (`entry_scope.enclosing_scope` — Auditor `_auditor_target`과 같은 규칙·창 폭)이고 FACTS
    `target`·`shown_lines`도 그 범위다. 슬라이스 없음은 선언과 무관하게 `capsule.target`의
    심볼 본문이다.
    """
    template = load_prompt(role, ctx.config.model_for(role.value).prompt_template_version)
    schema = resolve_output_schema(template.output_schema)
    target = capsule.target
    shown_lines: tuple[tuple[str, int], ...] | None = None
    if slice is None:
        source = ctx.read_source(target)
    elif template.input == "source":
        target = enclosing_scope(ctx.symbols_for(slice.entry.file), slice.entry)
        # 창 폴백은 심볼이 없다 — 슬라이스가 든 심볼(`capsule.target`)의 이름을 쓴다.
        if target.symbol is None:
            target = target.model_copy(update={"symbol": capsule.target.symbol})
        source = ctx.read_source(target)
        first = target.line_start or 1
        shown_lines = tuple((target.file, first + i) for i in range(len(source.splitlines())))
    else:
        source = ctx.read_slice(slice.slice_id)
    slice_input = slice is not None and template.input == "slice"

    def render(source: str) -> Prompt:
        return template.render(
            facts=build_facts(
                run_id=ctx.run_id,
                ids={"candidate_id": candidate_id},
                artifact_id=artifact.artifact_id,
                target=target,
                capsule=capsule,
                source=source,
                evidences=evidences,
                slice_criterion=_slice_criterion(slice) if slice_input else None,
            ),
            target=describe_target(target),
            capsule_sections=describe_sections(capsule),
            source=source,
        )

    prompt = render(source)
    # `source` 선언은 트림이 안 돈다 — 큰 함수는 잘리지 않고 크기 거절된다
    # (`runner._is_size_rejection` -> `_drop`, #529).
    if slice_input and ctx.config.thresholds.scout_slice_trim:
        prompt, shown_lines = _fit_slice(ctx, role, slice, candidate_id, source, render, prompt)
    try:
        hop_response = _call_hop(
            ctx,
            candidate_id=candidate_id,
            artifact_id=artifact.artifact_id,
            role=role,
            prompt=prompt,
            schema=schema,
            history=(),
            hop=0,
            target=capsule.target,
        )
    except BudgetExhausted:
        _queue_previous(ctx, previous, ReasonCode.BUDGET_EXHAUSTED)
        raise
    except Exception:
        _queue_previous(ctx, previous, ReasonCode.SCHEMA_PARSE_FAILED)
        raise
    # 모델이 본 줄 집합을 세션에 싣는다 — resolver의 `visible`이 된다(#375 M-2). 슬라이스 렌더는
    # 잘렸을 때만, `source` 선언은 늘 있다(함수 본문 범위). `None`이면 `SliceRef.lines` 전체다.
    return hop_response if shown_lines is None else replace(hop_response, shown_lines=shown_lines)


# 창에서 프롬프트 몫을 셀 때 남기는 여유. **잰 값이 아니다** — 러너 `_HOP_TOKEN_MARGIN`도 자신을
# *"설계값·미실측"*이라 적는다(`runner.py`). 값은 그 러너 상수와 같은 512지만 **셈 방식은 다르다**:
# 러너는 서버가 센 직전 홉 `usage`에서 이 몫을 빼고(실측 기반), 이 자리는 클라이언트 추정 토큰에서
# 뺀다. 그래서 「같은 방식」이라고 말하지 않는다 — 512가 추정 오차를 덮는다는 근거는 없고
# (#375 HIGH-1), 추정 과소는 아래 `_SLICE_FALLBACK_CHARS_PER_TOKEN`이 하한비로 막는다.
_SLICE_TOKEN_MARGIN = 512
# 토크나이저가 없을 때(approx — `dev_*`·CI·HF 캐시 미스)의 글자/토큰 비. 기본 카운터 `len//4`(4.0)는
# 실서빙보다 1.8배 과소라(CLAUDE.md §5) 추정이 창을 넘겨도 `fit=true`로 남고 서버가 400/413으로
# 거절한다(#375 HIGH-1 — #353이 고치려던 바로 그 실패). Scout 좌석 실측 하한 3.28
# (`docs/experiments/harness/2026-09-26-slice-smoke-atom.md`, 러너도 `_HOP_CHARS_PER_TOKEN`으로 이
# 하한을 쓴다)로 세면 실비 r≥3.28인 한 과대평가라 트림 뒤 토큰이 창을 안 넘는다. 실 토크나이저가
# 있으면(`counter`) 이 상수는 안 쓴다 — 그 자리 모델 토크나이저가 센다.
_SLICE_FALLBACK_CHARS_PER_TOKEN = 3.28
_BANNER = re.compile(r"^/\* ===== (.+) ===== \*/$")
_GUTTER = re.compile(r"^(\d+)\| ")


def _fallback_tokens(text: str) -> int:
    """토크나이저가 없을 때의 **보수적** 추정 — 하한비 3.28로 나눠 과소추정을 막는다(#375 HIGH-1).

    `estimate_tokens`(len//4)가 아니라 이것을 쓴다 — 트림은 추정 토큰을 창에 맞추므로 추정이
    과소면 실토큰이 넘친다. 바닥 1은 `estimate_tokens`와 같은 사유(본문 있는데 비용 0을 막음).
    """
    return max(1, math.ceil(len(text) / _SLICE_FALLBACK_CHARS_PER_TOKEN))


def _provenance_rank(ref: SliceRef) -> dict[tuple[str, int], int]:
    """(file, line) → 출처 순위. 작을수록 먼저 남긴다 — `candidate_anchor`(0) → `data_dependency`(1)
    → `context`(2). provenance에 없는 줄은 그 뒤(3)다.

    slice-v2 `line_provenance`를 어댑터가 `SliceRef.provenance`로 실어 온다(#375 HIGH-2). 비어
    있으면(옛 슬라이스·degraded 빈 범주) 전부 3으로 떨어져 거리순만 남는다.
    """
    rank: dict[tuple[str, int], int] = {}
    for tier, key in enumerate(("candidate_anchor", "data_dependency", "context")):
        for pair in ref.provenance.get(key, ()):
            rank.setdefault((pair[0], pair[1]), tier)
    return rank


def _fit_slice(
    ctx: RunContext,
    role: Role,
    ref: SliceRef,
    candidate_id: str,
    source: str,
    render: Callable[[str], Prompt],
    prompt: Prompt,
) -> tuple[Prompt, tuple[tuple[str, int], ...] | None]:
    """슬라이스 렌더가 Scout 창에 안 들면 줄을 빼서 맞춘다(`scout_slice_trim`, #353 (b)).
    `input: slice` 선언에서만 돈다 — `source` 선언은 함수 본문이라 부르지 않는다(#529).

    돌려주는 둘: 맞춘 프롬프트와, **모델에 실제로 간 (file, line) 집합**(안 잘렸으면 `None`).
    뒤엣것을 러너의 need_evidence resolver가 `visible`로 써서 "이미 SOURCE에 있다"를 **모델이
    본 줄**로만 판단한다(#375 M-2) — 아니면 자르기 전 `SliceRef.lines` 전체로 답해 안 보인 줄을
    중복으로 본다.

    몫 = 이 자리 모델의 `context_limit` − 이 역할의 `output_max` − 여유. 프롬프트 전체(prefix +
    body — body에는 `SOURCE`와 `FACTS.source`로 렌더가 두 번 실린다)를 이 자리 모델 토크나이저로
    센다. **토크나이저가 없으면 글자 하한비 3.28로 보수적으로 센다**(`_fallback_tokens`) —
    `len//4`로 세면 실서빙보다 과소라 트림 뒤에도 서버 창을 넘는다(#375 HIGH-1).

    남길 순서는 줄 출처(`line_provenance`)다 — `candidate_anchor`를 먼저, 다음 `data_dependency`,
    마지막 `context`. **각 단계 안에서는 entry와의 줄 거리순**이고 **파일 우선순위는 두지 않는다**:
    교차 파일 줄을 먼저 버리지 않는다. v3 prefix가 *"the entry point ... is often in one file and
    the operation that breaks is in another"*(`prompts/scout_boundary/v3/prefix.md`)라 적으므로,
    entry 파일을 통째로 앞세우면 깨질 줄을 먼저 버린다. 남길 줄 수는 이분 탐색으로 고른다(최대로).

    ⚠ provenance가 비면(옛 슬라이스) 전부 같은 순위라 거리순으로만 떨어진다 — 그래도 파일 우선순위는
    없다. #353 (b) 원문의 *"entry 주변·데이터 의존 줄 우선"*이 이것이다.

    남긴 렌더는 같은 모양이다(배너 + `<원본 줄>| <코드>`). v3 prefix가 SOURCE를 「슬라이서가 줄을
    골라 남겨 줄 번호가 건너뛴다」고 서술하므로 프롬프트 판을 안 바꾼다 — 뺀 줄은 그 건너뜀으로
    보인다. 뺐다는 사실은 trace `scout_slice_trimmed`가 든다. 줄 하나로도 안 들면 그 줄 하나로
    보내고 `fit=false`로 남긴다 — 서빙이 크기 거절하면 지금처럼 그 후보만 빠진다(`_drop`).

    ⏵ 2026-10-12 — 서빙 판은 v4이고 그 prefix는 SOURCE를 줄번호 없는 함수 본문으로 서술한다(이슈
    #529) — 위 「프롬프트 판을 안 바꾼다」는 v3 기준이다.

    ⚠ **알려진 틈(#375 M-1):** 모델은 **하네스가** 줄을 뺀 것을 모른다 — v3 prefix는 빠진 줄을
    「슬라이서가 뺐다」고 서술한다. 하네스가 뺀 줄은 슬라이서가 **남긴** 줄이라, 잘린 구간의 방어
    코드를 모델이 「슬라이서가 안 닿는다고 본 것」으로 오독할 수 있다. 메우려면 하네스 트림을
    서술하는 프롬프트 판에만 이 좌석을 돌리는 가드(#351의 `source_options` + doctor 선례)가
    필요하다 — 이 PR 밖이다. 지금은 trace `scout_slice_trimmed`가 남기고 doctor가 WARN을 단다.
    """
    counter = ctx.counter_for_role(role.value)
    measure = counter or _fallback_tokens
    model = ctx.config.models[ctx.config.model_key_for(role.value)]
    budget = (
        model.context_limit
        - ctx.config.thresholds.output_max_for(role.value)
        - _SLICE_TOKEN_MARGIN
    )

    def size(p: Prompt) -> int:
        return measure(p.prefix) + measure(p.body)

    before = size(prompt)
    if before <= budget:
        return prompt, None
    rows = _parse_render(source)
    rank = _provenance_rank(ref)
    entry_line = ref.entry.line_start or 0
    order = sorted(
        range(len(rows)),
        key=lambda i: (
            rank.get((rows[i][0], rows[i][1]), 3),
            abs(rows[i][1] - entry_line) if entry_line else 0,
            i,
        ),
    )
    low, high, best = 1, len(rows), None
    while low <= high:
        keep = (low + high) // 2
        candidate = render(_rebuild(rows, sorted(order[:keep])))
        if size(candidate) <= budget:
            low, best = keep + 1, (keep, candidate)
        else:
            high = keep - 1
    fit = best is not None
    kept, trimmed = best if best else (min(1, len(rows)), render(_rebuild(rows, order[:1])))
    kept_idx = sorted(order[:kept])
    shown = tuple((rows[i][0], rows[i][1]) for i in kept_idx)
    entry_kept = (ref.entry.file, entry_line) in shown if entry_line else False
    ctx.trace.emit(
        EventKind.SCOUT_SLICE_TRIMMED,
        stage=Stage.SCOUT,
        subject_kind="candidate",
        subject_id=candidate_id,
        slice_id=ref.slice_id,
        role=role.value,
        budget=budget,
        tokens_before=before,
        tokens_after=size(trimmed),
        lines_before=len(rows),
        lines_kept=kept,
        fit=fit,
        entry_kept=entry_kept,
        token_counter="model" if counter else "estimate",
    )
    return trimmed, shown


def _parse_render(source: str) -> list[tuple[str, int, str]]:
    """렌더(`adapters.slices.render`) → (file, 원본 줄, 렌더된 한 줄)."""
    rows: list[tuple[str, int, str]] = []
    file = ""
    for line in source.split("\n"):
        banner = _BANNER.match(line)
        if banner:
            file = banner[1]
            continue
        gutter = _GUTTER.match(line)
        rows.append((file, int(gutter[1]) if gutter else 0, line))
    return rows


def _rebuild(rows: list[tuple[str, int, str]], keep: Sequence[int]) -> str:
    """남길 행만으로 렌더를 다시 짠다 — 파일마다 배너 하나, 원래 순서 그대로."""
    out: list[str] = []
    file = None
    for i in sorted(keep):
        if rows[i][0] != file:
            file = rows[i][0]
            out.append(f"/* ===== {file} ===== */")
        out.append(rows[i][2])
    return "\n".join(out)


def _slice_criterion(ref: SliceRef) -> dict[str, object]:
    """v3 `FACTS.slice_criterion` — 슬라이서가 경로의 **어느 끝**을 쥐여 줬는가.

    `given_end`는 v3 prefix가 정의한 키다(「Slice criterion」 절 — `source`면 앞으로, `sink`면
    뒤로 자른 것). `given`은 그 쥐여 준 끝의 위치다 — memory·injection v3가 *"You are told where
    a value enters"*라 적는(boundary v3는 *"This identifies where a value enters"*) 그 끝이 FACTS에
    없으면 모델은 어느 줄이 주어진 끝인지 모른다. 이름은
    `prompts/_retrieval/embedder/v1/*/meta.yaml`이 이미 가리키는 `slice_criterion.given.*`을 따른다.

    **슬라이서 산출이 드는 것만 싣는다** — entry의 file·line·symbol(`SliceRef.entry`). 그 meta가
    드는 `why_selected`·`tainted_by`는 slice-v2 `slices[]`에 없어 지어내지 않는다. Case B의
    `symbol`은 sink 호출 이름이라 `FACTS.target.symbol`(감싸는 함수)과 다른 것이 정상이다.
    """
    return {
        "given_end": ref.given_end,
        "given": {
            "file": ref.entry.file,
            "line": ref.entry.line_start,
            "symbol": ref.entry.symbol,
        },
        # anchor-only 대체 슬라이스(#307)면 True — 이 슬라이스엔 dataflow 문맥이 없다. 모델이
        # 절단 슬라이스를 full로 오인하지 않게 FACTS에 싣는다(@danielcho02 #307 리뷰). `degraded`는
        # slice-v2 `slices[]`가 실제로 드는 값이라(지어낸 키가 아니다) 여기 실을 자격이 있다.
        "degraded": ref.degraded,
    }


def _queue_previous(ctx: RunContext, previous: Candidate | None, reason: ReasonCode) -> None:
    """재평가 중이던 후보만 전이시킨다. 첫 호출에는 대상이 없다(표 C-4·C-7)."""
    if previous is None or previous.state is not FindingState.CANDIDATE:
        return
    ctx.apply(previous, to=FindingState.QUEUED, reason=reason, stage=Stage.SCOUT)


def describe_target(target: Location) -> str:
    """프롬프트 본문의 사람이 읽는 대상 표기. 사실 블록에는 구조화된 값이 따로 간다.

    「파일 전체」는 **줄 범위가 없을 때만**이다 — 이름 없이 줄만 든 대상(scopes 없는 슬라이스
    Case B의 entry, Auditor의 entry 앞뒤 창)을 그렇게 부르면 같은 프롬프트의 FACTS와 어긋난다.
    """
    if target.line_start is None:
        return f"`{target.file}` 의 `{target.symbol or '(파일 전체)'}`"
    lines = f" (줄 {target.line_start}-{target.line_end or target.line_start})"
    if not target.symbol:
        return f"`{target.file}`{lines}"
    return f"`{target.file}` 의 `{target.symbol}`{lines}"


def describe_sections(capsule: ContextCapsule) -> str:
    """캡슐 섹션의 **본문까지** 프롬프트에 렌더한다 (W1).

    예전에는 `callers(312 tok)` 같은 **이름표만** 냈다. 그래서 #63이 실제 호출 그래프를
    캡슐에 넣은 뒤에도 모델은 그것을 보지 못했고, "못 봐서 못 찾은 건지 봤는데 못 찾은
    건지"가 구별되지 않았다 — 그 상태에서 잰 성능 수치는 하네스가 아니라 Fake 어댑터를
    잰 값이다.

    **같은 내용이 `build_facts()`의 사실 블록에도 간다.** 중복이 아니라 계약이다:
    body.md가 "아래 JSON이 참조 가능한 사실 전부다"라고 못박으므로, 산문에만 있는 것은
    모델이 무시해도 되는 값이 된다. `{{SOURCE}}`가 이미 같은 이유로 양쪽에 실린다
    (`bench.sized_prompt` docstring이 그 2배 계상을 적어 뒀다).

    **본문이 없는 섹션은 이름표로 남는다.** `target_function`(원문은 `{{SOURCE}}`가
    싣는다)·`static_signal`·`known_facts`(데이터원이 아직 없다)가 그렇다 —
    없는 본문을 지어내지 않는다.
    """
    if not capsule.sections:
        return "(없음)"
    lines: list[str] = []
    for section in capsule.sections:
        counted = "" if section.items is None else f", {section.items}건"
        lines.append(f"- {section.kind} ({section.tokens} tok{counted})")
        # 여러 줄짜리 본문(예: `related`의 코드 블록)은 **모든 줄**을 들여쓴다.
        # `f"    {entry}"`는 첫 줄만 붙어 나머지가 왼쪽에 붙던 자리다(그래프 줄은 한 줄이라
        # 안 드러났다).
        for entry in section.body:
            lines.extend(f"    {line}" for line in entry.splitlines())
    return "\n".join(lines)
