"""FakeModelClient — GPU 없이 파이프라인을 완주시키는 결정론적 ModelClient.

팀원 대부분이 GPU 접근이 없다. dev_fake 프로필만으로 0~9단계가 돌지 않으면 8개 트랙이
서로의 구현을 기다리게 된다(roadmap.md §3).

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

from pydantic import BaseModel, Field

from src.interfaces.model_client import ModelResult, Prompt, Usage
from src.schemas.candidate import Candidate
from src.schemas.common import HarnessModel, Location, ProducedBy
from src.schemas.decision import Decision
from src.schemas.enums import EvidenceKind, FindingState, PathStepKind, Role, Verdict
from src.schemas.finding import Finding, PathStep, ReproSpec

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
    # 캡슐에 담긴 섹션 종류. static_signal 유무가 Scout 점수를 가른다.
    capsule_sections: list[str] = Field(default_factory=list)
    # 대상 함수 본문. 위험 호출 탐지의 근거다.
    source: str = ""
    evidence: list[FakeEvidenceRef] = Field(default_factory=list)
    # source→sink 경로 구성용. 없으면 target.symbol만 쓴다.
    callers: list[str] = Field(default_factory=list)


class FakeModelError(RuntimeError):
    """Fake가 요청받은 스키마를 만들 수 없다. 조용히 빈 값을 내지 않는다."""


class FakeModelClient:
    """`ModelClient` 구현. 같은 프롬프트에는 항상 같은 출력을 낸다."""

    def __init__(self, *, model_name: str = "fake") -> None:
        self.model_name = model_name
        # prefix cache 시뮬레이션. 같은 prefix를 다시 보면 그만큼 cached_tokens로 잡는다
        # (agent-contracts.md §4.2, evaluation.md §5.2 cache hit ratio).
        self._seen_prefixes: set[str] = set()

    def complete(self, *, role: Role, prompt: Prompt, schema: type[BaseModel]) -> ModelResult:
        context = _parse_context(prompt.body)
        builder = _BUILDERS.get(schema)
        if builder is None:
            known = sorted(model.__name__ for model in _BUILDERS)
            raise FakeModelError(
                f"FakeModelClient가 모르는 출력 스키마: {schema.__name__}. 아는 것: {known}"
            )
        produced_by = ProducedBy(
            role=role, model=self.model_name, prompt_version=prompt.prompt_version
        )
        value = builder(context, produced_by)
        return ModelResult(value=value, usage=self._usage(prompt, value), model=self.model_name)

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


def _build_candidate(context: FakeContext, _produced_by: ProducedBy) -> Candidate:
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


def _build_finding(context: FakeContext, produced_by: ProducedBy) -> Finding:
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


def _build_decision(context: FakeContext, produced_by: ProducedBy) -> Decision:
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


_BUILDERS = {
    Candidate: _build_candidate,
    Finding: _build_finding,
    Decision: _build_decision,
}
