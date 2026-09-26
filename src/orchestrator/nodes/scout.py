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

from collections.abc import Sequence
from dataclasses import dataclass

from src.interfaces.model_client import ChatTurn, Prompt, Usage
from src.orchestrator.budget import BudgetExhausted
from src.orchestrator.context import RunContext
from src.orchestrator.policy import DecisionPoint, RoutingRequest
from src.orchestrator.prompt_loader import PromptError, build_facts, load_prompt
from src.orchestrator.stages import Stage
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
    부르는가뿐이다. `Candidate.scout_member` stamp는 노드가 아니라 `RunContext.call_model`이
    이 `role`에서 만든다(ADR 0010 §3, `candidate.py` 필드 주석).

    `slice`는 슬라이스 타깃이면 그 슬라이스다(ADR 0012 결정 1) — `SOURCE`가 그 렌더가 되고
    FACTS에 `slice_criterion`이 붙는다(`_ask_model`). `None`이면 지금처럼 심볼 본문이다.

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
    )


def continue_session(ctx: RunContext, session: ScoutSession, followup_text: str) -> ScoutSession:
    """hop k(= `session.hop + 1`). 되묻기 응답을 다음 user 턴으로 보낸다.

    **오직 v3에서만 불린다** — `session.signal`이 `triage_need_evidence`일 때만 좌석 집계가
    이 함수를 부르므로, 이 홉의 스키마도 항상 `ScoutResponse`다. 실패는 여기서 처리하지
    않는다 — `previous` 큐잉이 없다(재평가 개념이 아니다). 흡수할지·정지할지는 그 예외를
    본 호출자(러너의 need_evidence 루프)가 정한다(ADR 0012 결정 2 Q6).
    """
    hop = session.hop + 1
    prompt = Prompt(
        prefix=session.prefix, body=followup_text, prompt_version=session.prompt_version
    )
    hop_response = _call_hop(
        ctx,
        candidate_id=session.candidate_id,
        role=session.role,
        prompt=prompt,
        schema=ScoutResponse,
        history=session.history,
        hop=hop,
    )
    return _session_from_hop(
        candidate_id=session.candidate_id,
        role=session.role,
        slice=session.slice,
        hop=hop,
        hop_response=hop_response,
        history=session.history,
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
    )


def _call_hop(
    ctx: RunContext,
    *,
    candidate_id: str,
    role: Role,
    prompt: Prompt,
    schema: type[Candidate] | type[ScoutResponse],
    history: Sequence[ChatTurn],
    hop: int,
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
        # v3 래퍼: 안쪽 `candidate`만 파이프라인으로 흘리고 `analysis_summary`(근거)는
        # 버린다 — 하네스에 근거 소비자가 없다(scout_response.py). 근거는 판정을 좋게 하는
        # 추론 흔적이지 소비 대상이 아니다.
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

    **슬라이스 타깃이면 `SOURCE`가 슬라이스 렌더다**(ADR 0012 결정 1) — `read_source`로 읽으면
    `capsule.target`이 entry(대표 위치, 줄 하나)라 그 한 줄만 간다. 렌더는 run 전에 이미
    만들어져 있고(`build_deps`) 여기서는 id로 찾기만 한다.
    """
    template = load_prompt(role, ctx.config.model_for(role.value).prompt_template_version)
    schema = resolve_output_schema(template.output_schema)
    if slice is None:
        source = ctx.read_source(capsule.target)
    else:
        source = ctx.read_slice(slice.slice_id)
    prompt = template.render(
        facts=build_facts(
            run_id=ctx.run_id,
            ids={"candidate_id": candidate_id},
            artifact_id=artifact.artifact_id,
            target=capsule.target,
            capsule=capsule,
            source=source,
            evidences=evidences,
            slice_criterion=None if slice is None else _slice_criterion(slice),
        ),
        target=describe_target(capsule.target),
        capsule_sections=describe_sections(capsule),
        source=source,
    )
    try:
        return _call_hop(
            ctx,
            candidate_id=candidate_id,
            role=role,
            prompt=prompt,
            schema=schema,
            history=(),
            hop=0,
        )
    except BudgetExhausted:
        _queue_previous(ctx, previous, ReasonCode.BUDGET_EXHAUSTED)
        raise
    except Exception:
        _queue_previous(ctx, previous, ReasonCode.SCHEMA_PARSE_FAILED)
        raise


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
