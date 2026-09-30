"""FakeModelClient — GPU 없이 파이프라인을 완주시키는 결정론적 ModelClient.

팀원 대부분이 GPU 접근이 없다. dev_fake 프로필만으로 0~9단계가 돌지 않으면
팀원들이 서로의 구현을 기다리게 된다.

**해시 난수로 출력을 만들지 않는다.** 프롬프트 본문에 실제로 들어 있는 신호에서 값을
계산한다. 해시만 쓰면 프롬프트를 한 글자 고칠 때마다 파이프라인 완주 여부가 바뀌고,
"제거율"이 입력과 무관한 난수가 되어 측정 자체가 무의미해진다.

프롬프트 본문의 마지막 ```json 펜스가 모델이 읽는 사실이다(`FakeContext`). 실제 모델도
같은 블록을 읽으므로 이 계약은 Fake 전용 우회로가 아니다.

아래 가중치는 **정책 임계값이 아니라 테스트 대역의 휴리스틱**이라 config가 아니라 여기 있다.
실제 판정 임계값(`scout_threshold`)은 config에서 오고 이 어댑터는 그것을 모른다.
"""

import json
import re
from collections.abc import Sequence

from pydantic import BaseModel, Field

from src.interfaces.model_client import ChatTurn, ModelResult, Prompt, Usage
from src.schemas.candidate import Candidate
from src.schemas.common import HarnessModel, Location, ProducedBy
from src.schemas.decision import Decision, DecisionDraft
from src.schemas.enums import EvidenceKind, FindingState, PathStepKind, Role, Verdict
from src.schemas.finding import Finding, PathStep, ReproSpec
from src.schemas.pov import PovAction, PovSegment, PovSpec
from src.schemas.scout_response import AnalysisSummary, ScoutResponse

# 4바이트 ≈ 1토큰. 실제 토크나이저가 아니라 비용 지표 경로를 태우기 위한 근사다.
_CHARS_PER_TOKEN = 4

# Scout 점수 가중치. 합이 1.0을 넘지 않아야 score(0.0~1.0) 제약을 지킨다.
#
# **경계에 걸치지 않게 잡는다.** benign 표본(위험 호출만 있고 정적 신호도 실행 흔적도
# 없는 경우)의 점수는 `_SCORE_BASE + _W_RISKY_CALL = 0.40`이고 config의
# `scout_threshold`는 0.5다. 두 값이 같으면 `>=`냐 `>`냐로 결과가 뒤집히고 부동소수
# 반올림까지 얹힌다. benign이 걸러지는 것이 이 픽스처의 핵심 시나리오이므로 0.1의
# 여유를 둔다. 반대로 정적 신호가 붙으면 0.60이 되어 임계값을 확실히 넘는다.
_SCORE_BASE = 0.30
_W_STATIC_SIGNAL = 0.20
_W_RUNTIME_EVIDENCE = 0.20
_W_RISKY_CALL = 0.10

# 위험 호출 → 추정 CWE. Scout이 "위험 함수 존재"만으로 점수를 주는 편향은
# benign 표본으로 교정 측정한다(agent-contracts.md §2.1 실패 모드).
RISKY_CALLS: dict[str, str] = {
    "gets": "CWE-242",
    "strcpy": "CWE-787",
    "sprintf": "CWE-787",
    "memcpy": "CWE-787",
    "alloca": "CWE-789",
}

# 실행 흔적이 남는 Evidence 종류. 정적 신호보다 강한 근거로 본다.
_RUNTIME_KINDS = {EvidenceKind.SANITIZER, EvidenceKind.CRASH, EvidenceKind.POC_RUN}

_FENCE = re.compile(r"```json\s*(?P<body>\{.*?\})\s*```", re.DOTALL)


class FakeEvidenceRef(HarnessModel):
    evidence_id: str
    kind: EvidenceKind
    ok: bool = True
    # Critic·Auditor v3·v4 경로에서 근거 본문이 실린다(#237 HIGH-1 · Auditor는 2026-09-29부터).
    # fake는 판정에 본문을 쓰지 않지만 FACTS를 파싱할 때 이 키를 거부하면 안 되므로
    # optional로 받는다.
    body: str | None = None
    truncated: bool | None = None


class FakeCapsuleSection(HarnessModel):
    """사실 블록의 `capsule_sections` 한 항목.

    `CapsuleSection`을 그대로 쓰지 않는다 — 이쪽은 **프롬프트에서 파싱해 온 것**이고
    저쪽은 캡슐의 불변식(토큰 합 = 본문 길이)을 강제한다. 모델이 받은 프롬프트가
    스키마 검증을 다시 통과해야 할 이유가 없고, 통과시키면 프롬프트가 조금만 달라져도
    Fake가 `FakeModelError`로 죽는다.
    """

    kind: str
    tokens: int = 0
    items: int | None = None
    # `callers`/`callees`의 실제 줄(`이름 파일:줄`). 본문 없는 섹션은 빈 목록이다.
    lines: list[str] = Field(default_factory=list)


class FakeContext(HarnessModel):
    """노드가 프롬프트 본문에 실어 보내는 사실.

    `ids`는 노드가 미리 발급한 식별자다. 구조화 출력 스키마가 ID를 필수 필드로 갖기
    때문에(예: `Candidate.candidate_id`) 모델은 쓸 ID를 프롬프트에서 받아야 한다.
    실제 모델에게도 같은 방식으로 지시한다.
    """

    run_id: str
    ids: dict[str, str] = Field(default_factory=dict)
    artifact_id: str = ""
    target: Location | None = None
    # 캡슐에 담긴 섹션과 그 본문. 점수 산정에는 쓰지 않는다 — `_has_static_signal()`
    # 주석이 그 이유를 적는다(섹션 존재는 레벨이 정하는 것이라 신호가 아니다).
    capsule_sections: list[FakeCapsuleSection] = Field(default_factory=list)
    # 대상 함수 본문(슬라이스 타깃의 Scout면 슬라이스 렌더). 위험 호출 탐지의 근거다.
    source: str = ""
    evidence: list[FakeEvidenceRef] = Field(default_factory=list)
    # source→sink 경로 구성용. 없으면 target.symbol만 쓴다.
    callers: list[str] = Field(default_factory=list)
    # 슬라이스 타깃의 v3 `slice_criterion`(`prompt_loader.FACT_KEYS`의 opt-in 키). Fake는 판정에
    # 안 쓰지만 사실 블록을 파싱할 때 거부하면 안 되므로 받는다(`FakeEvidenceRef.body`와 같은 처리).
    slice_criterion: dict[str, object] | None = None


class FakeModelError(RuntimeError):
    """Fake가 요청받은 스키마를 만들 수 없다. 조용히 빈 값을 내지 않는다."""


class FakeModelClient:
    """`ModelClient` 구현. 같은 프롬프트에는 항상 같은 출력을 낸다."""

    def __init__(self, *, model_name: str = "fake") -> None:
        self.model_name = model_name
        # prefix cache 시뮬레이션. 같은 prefix를 다시 보면 그만큼 cached_tokens로 잡는다
        # (agent-contracts.md §4.2, evaluation.md §5.2 cache hit ratio).
        self._seen_prefixes: set[str] = set()

    def complete(
        self,
        *,
        role: Role,
        prompt: Prompt,
        schema: type[BaseModel],
        history: Sequence[ChatTurn] = (),
    ) -> ModelResult:
        # 사실 블록은 **첫 user 턴**에서 읽는다 — history가 있으면 그것이 hop0 본문이고,
        # 이번 turn(`prompt.body`)은 후속 질문이라 펜스가 없다(ADR 0012 결정 2).
        fact_source = history[0].content if history else prompt.body
        context = _parse_context(fact_source)
        builder = _BUILDERS.get(schema)
        if builder is None:
            known = sorted(model.__name__ for model in _BUILDERS)
            raise FakeModelError(
                f"FakeModelClient가 모르는 출력 스키마: {schema.__name__}. 아는 것: {known}"
            )
        produced_by = ProducedBy(
            role=role, model=self.model_name, prompt_version=prompt.prompt_version
        )
        # `history`도 넘긴다 — PoV 툴 루프의 fake는 홉(=history 길이)마다 다른 액션을 낸다
        # (`_build_pov_action`). 다른 빌더는 홉을 안 보지만 dict 디스패치라 시그니처가 하나다.
        value = builder(context, produced_by, history)
        return ModelResult(
            value=value,
            usage=self._usage(prompt, value),
            model=self.model_name,
            raw_text=value.model_dump_json(),
        )

    def _usage(self, prompt: Prompt, value: BaseModel) -> Usage:
        prefix_tokens = len(prompt.prefix) // _CHARS_PER_TOKEN
        body_tokens = len(prompt.body) // _CHARS_PER_TOKEN
        cached = prefix_tokens if prompt.prefix in self._seen_prefixes else 0
        self._seen_prefixes.add(prompt.prefix)
        return Usage(
            input_tokens=prefix_tokens + body_tokens,
            output_tokens=max(1, len(value.model_dump_json()) // _CHARS_PER_TOKEN),
            cached_tokens=cached,
        )


def _parse_context(body: str) -> FakeContext:
    matches = _FENCE.findall(body)
    if not matches:
        raise FakeModelError("프롬프트 본문에 ```json 사실 블록이 없다")
    return FakeContext.model_validate(json.loads(matches[-1]))


def risky_calls_in(source: str) -> list[str]:
    """소스에서 실제로 호출된 위험 함수. 등장 순서를 유지해 결정론을 지킨다."""
    return [name for name in RISKY_CALLS if re.search(rf"\b{name}\s*\(", source)]


def _has_static_signal(context: FakeContext) -> bool:
    """정적 신호가 **실제로** 있는가.

    캡슐의 `static_signal` **섹션 존재**를 신호로 세지 않는다. Retriever는 그 섹션을
    레벨에 따라 무조건 붙이므로(내용이 비어도 목록에는 있다), 섹션 이름을 신호로 세면
    모든 후보가 같은 가산점을 받아 benign 표본이 임계값에 정확히 걸린다 — 걸러지는지
    보려고 만든 표본이 비교 연산자 하나에 좌우된다. 신호의 근거는 Evidence뿐이다.
    """
    return any(ref.kind is EvidenceKind.STATIC and ref.ok for ref in context.evidence)


def _has_runtime_evidence(context: FakeContext) -> bool:
    return any(ref.kind in _RUNTIME_KINDS and ref.ok for ref in context.evidence)


def _require_id(context: FakeContext, key: str) -> str:
    value = context.ids.get(key)
    if not value:
        raise FakeModelError(f"프롬프트가 발급해 준 {key!r}가 없다")
    return value


def _build_candidate(
    context: FakeContext, _produced_by: ProducedBy, _history: Sequence[ChatTurn]
) -> Candidate:
    """4단계 Scout 출력.

    점수는 세 신호의 가중합이다. 위험 호출만 있고 정적 신호도 실행 흔적도 없는 후보는
    `_SCORE_BASE + _W_RISKY_CALL`(0.40)에 머물러 임계값(0.5) 아래로 떨어진다 —
    benign 표본이 실제로 걸러진다. 경계에 걸치지 않게 잡은 이유는 위 상수 주석 참조.
    """
    risky = risky_calls_in(context.source)
    reasons: list[str] = []
    score = _SCORE_BASE
    if _has_static_signal(context):
        score += _W_STATIC_SIGNAL
        reasons.append("static_signal")
    if _has_runtime_evidence(context):
        score += _W_RUNTIME_EVIDENCE
        reasons.append("runtime_evidence")
    if risky:
        score += _W_RISKY_CALL
        reasons.extend(f"{name}_unbounded" for name in risky)

    return Candidate(
        candidate_id=_require_id(context, "candidate_id"),
        run_id=context.run_id,
        artifact_id=context.artifact_id,
        location=context.target or Location(file="unknown"),
        suspected_cwe=[RISKY_CALLS[name] for name in risky],
        score=round(min(score, 1.0), 4),
        reason_codes=reasons,
        evidence_refs=[ref.evidence_id for ref in context.evidence],
        state=FindingState.CANDIDATE,
    )


def _build_finding(
    context: FakeContext, produced_by: ProducedBy, _history: Sequence[ChatTurn]
) -> Finding:
    """5단계 Auditor 출력.

    `repro_spec`은 build Evidence가 있을 때만 채운다 — 빌드 방법을 모르는 채로 재현
    명세를 지어내면 8단계가 실행할 수 없는 절차를 받는다. 명세가 없으면 Evidence Gate가
    E3으로 못 올리고 `repro_spec_missing`으로 기록한다.
    """
    target = context.target or Location(file="unknown")
    symbol = target.symbol or "unknown"
    risky = risky_calls_in(context.source)
    sink = risky[0] if risky else "unknown"
    source_symbol = context.callers[0] if context.callers else "input"

    path = [
        PathStep(step=PathStepKind.SOURCE, symbol=source_symbol),
        PathStep(step=PathStepKind.PROPAGATE, symbol=symbol, note="길이 검증 없이 전달"),
        PathStep(step=PathStepKind.SINK, symbol=sink, note="고정 크기 버퍼"),
    ]
    missing = [] if _has_static_signal(context) else [_require_id(context, "missing_id")]
    has_build = any(ref.kind is EvidenceKind.BUILD and ref.ok for ref in context.evidence)

    return Finding(
        finding_id=_require_id(context, "finding_id"),
        run_id=context.run_id,
        candidate_id=_require_id(context, "candidate_id"),
        title=f"{symbol}에서 검증 없는 {sink}",
        cwe=RISKY_CALLS.get(sink, "CWE-20"),
        state=FindingState.HYPOTHESIZED,
        confidence=0.6 if risky else 0.3,
        path=path,
        rca_draft=f"{symbol}이 입력 길이를 확인하지 않고 {sink}로 전달한다.",
        evidence_refs=[ref.evidence_id for ref in context.evidence],
        missing_evidence=missing,
        produced_by=produced_by,
        repro_spec=_repro_spec(target, sink) if has_build else None,
    )


def _repro_spec(target: Location, sink: str) -> ReproSpec:
    return ReproSpec(
        steps=["./build.sh --sanitize=address", f"./poc {target.file}"],
        expected_signal=f"AddressSanitizer: heap-buffer-overflow at {target.symbol or sink}",
    )


def _build_decision(
    context: FakeContext, produced_by: ProducedBy, _history: Sequence[ChatTurn]
) -> Decision:
    """7단계 Critic 출력.

    call graph 근거가 있으면 근거를 붙여 `uphold`, 없으면 **근거 없는 `rebut`**을 낸다.
    후자는 agent-contracts.md §2.3대로 오케스트레이터가 `uphold`로 정규화하고 지표에
    남긴다 — 그 경로가 실제로 실행되지 않으면 구현되어 있는지 알 수 없다.
    """
    graph_refs = [
        ref.evidence_id for ref in context.evidence if ref.kind is EvidenceKind.GRAPH and ref.ok
    ]
    grounded = bool(graph_refs)
    return Decision(
        decision_id=_require_id(context, "decision_id"),
        run_id=context.run_id,
        finding_id=_require_id(context, "finding_id"),
        verdict=Verdict.UPHOLD if grounded else Verdict.REBUT,
        rebuttal_axis=["guard", "reachability"],
        # 근거 없는 rebut은 rationale만 있고 evidence_refs가 비어 정규화 대상이 된다.
        rationale=(
            "상위 호출부에 길이 상한이 없음(call graph 확인)" if grounded else "도달 불가능해 보임"
        ),
        evidence_refs=graph_refs,
        produced_by=produced_by,
    )


def _build_decision_draft(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> DecisionDraft:
    """7단계 Critic 출력 — 자유 verdict 판(실서빙 Critic이 이 스키마로 받는다).

    `_build_decision`과 같은 판정을 내되 `verdict`를 문자열로 낸다. Fake는 표준 어휘를
    그대로 써 `Verdict.from_text` 매핑이 항등이다 — 매핑 로직 자체는 단위 테스트가 덮고,
    여기서는 배선(자유 verdict → `to_decision`)이 e2e에서 도는지만 고정한다.
    """
    decision = _build_decision(context, produced_by, history)
    return DecisionDraft(
        verdict=decision.verdict.value,
        rebuttal_axis=decision.rebuttal_axis,
        rationale=decision.rationale,
        evidence_refs=decision.evidence_refs,
    )


def _build_scout_response(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> ScoutResponse:
    """4단계 Scout 출력 래퍼. 판정은 `_build_candidate`와 같고, 근거(`analysis_summary`)는
    최소로 채운다 — 하네스가 버리므로 결정론적 자리표시면 된다(scout_response.py)."""
    return ScoutResponse(
        candidate=_build_candidate(context, produced_by, history),
        analysis_summary=AnalysisSummary(claim="fake scout analysis summary"),
    )


def _build_pov(
    _context: FakeContext, _produced_by: ProducedBy, _history: Sequence[ChatTurn]
) -> PovSpec:
    """Fake PoV 구성 스펙 — 흔한 오버플로 모양(64바이트 패딩 + 4바이트 정수). dev_fake 전용.

    `assemble_poc`가 결정론적으로 바이트를 만드므로 dev_fake run이 재현 가능하다. 툴 루프의 gen
    액션(`_build_pov_action`)이 이 스펙을 실어 낸다.
    """
    return PovSpec(
        segments=[
            PovSegment(kind="repeat", byte=0x41, count=64),
            PovSegment(kind="int", value=0xDEADBEEF, size=4, endian="little"),
        ],
        rationale="fake: 64바이트 패딩 뒤 4바이트 정수",
    )


def _build_pov_action(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> PovAction:
    """PoV 툴 루프의 fake 액션 (Phase 3, Option C). 홉을 `history` 길이로 가른다.

    hop0(history 빔)은 `gen` — `_build_pov`의 스펙을 실어 순수 `assemble_poc`가 바이트를
    만든다. hop≥1(history 있음)은 `submit` — 도구 관측을 되먹임받은 뒤 후보가 버그에
    닿는다고 보고 루프를 끝낸다. 결정론이라 dev_fake run이 재현 가능하다(`_build_pov`와 같다).

    실제 모델은 도구 관측을 보고 gen/inspect/submit을 스스로 고른다 — fake는 그 루프 배선
    (gen→도구→되먹임→submit)이 도는지만 고정한다.
    """
    if history:
        return PovAction(action="submit", rationale="fake: candidate reaches the bug")
    return PovAction(
        action="gen",
        spec=_build_pov(context, produced_by, history),
        rationale="fake: try the overflow spec",
    )


# `PovSpec`은 모델 출력 스키마로 직접 요청되지 않는다 — pov 루프는 `PovAction`을 요청하고, `gen`이
# 실은 `PovSpec`은 `_build_pov_action`이 `_build_pov`로 채운다(단발판 제거로 엔트리는 죽어 뺐다).
_BUILDERS = {
    Candidate: _build_candidate,
    ScoutResponse: _build_scout_response,
    Finding: _build_finding,
    Decision: _build_decision,
    DecisionDraft: _build_decision_draft,
    PovAction: _build_pov_action,
}
