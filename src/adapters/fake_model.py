"""FakeModelClient — GPU 없이 파이프라인을 완주시키는 결정론적 ModelClient.

팀원 대부분이 GPU 접근이 없다. dev_fake 프로필만으로 0~9단계가 돌지 않으면
팀원들이 서로의 구현을 기다리게 된다.

**해시 난수로 출력을 만들지 않는다.** 프롬프트 본문에 실제로 들어 있는 신호에서 값을
계산한다. 해시만 쓰면 프롬프트를 한 글자 고칠 때마다 파이프라인 완주 여부가 바뀌고,
"제거율"이 입력과 무관한 난수가 되어 측정 자체가 무의미해진다.

프롬프트 본문의 마지막 ```json 펜스가 모델이 읽는 사실이다(`FakeContext`). 실제 모델도
같은 블록을 읽으므로 이 계약은 Fake 전용 우회로가 아니다. ⏵ 2026-10-10 — 단 `facts_source: false` 판
(Auditor v13·v14 · ⏵ 2026-10-13 v15 · 2026-10-14 v16·v17 · 2026-10-16 v18 · 10-17 v19도)은
`source`가 펜스에
없고 Fake가 산문 원문
블록에서 읽는다.

아래 가중치는 **정책 임계값이 아니라 테스트 대역의 휴리스틱**이라 config가 아니라 여기 있다.
실제 판정 임계값(`scout_threshold`)은 config에서 오고 이 어댑터는 그것을 모른다.
"""

import json
import re
from collections.abc import Sequence

from pydantic import BaseModel, Field

from src.interfaces.model_client import AttemptRecord, ChatTurn, ModelResult, Prompt, Usage
from src.schemas.auditor import (
    AuditorAction,
    AuditorProcedureAction,
    AuditorProcedureAuditAlwaysAction,
    AuditorProcedureNullableCweAction,
    AuditorProcedureProbeInputAction,
    AuditorProcedureProbeInputDiscriminatedAction,
    AuditProcedure,
    AuditProcedureAuditAlways,
    AuditProcedureNullableCwe,
)
from src.schemas.candidate import Candidate
from src.schemas.common import HarnessModel, Location, ProducedBy
from src.schemas.decision import Decision, DecisionDraft
from src.schemas.enums import EvidenceKind, FindingState, PathStepKind, Role, Verdict
from src.schemas.finding import (
    AuditBlock,
    AuditDefense,
    AuditOrigin,
    AuditPoint,
    AuditRefutation,
    Finding,
    PathStep,
    ReproSpec,
)
from src.schemas.pov import (
    PovAction,
    PovActionNamed,
    PovDiscriminatedAction,
    PovSegment,
    PovSpec,
)
from src.schemas.scout_response import AnalysisSummary, RequestedContext, ScoutResponse

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
    # Critic·Auditor의 content-aware 버전에서 근거 본문이 실린다(#237 HIGH-1).
    # fake는 판정에 본문을 쓰지 않지만 FACTS를 파싱할 때 이 키를 거부하면 안 되므로
    # optional로 받는다.
    body: str | None = None
    truncated: bool | None = None
    # content-aware 판이 싣는 `Evidence.location`(`prompt_loader.build_facts`의 `evidence_locations`
    # — 판단 보조이고 게이트 등급 근거가 아니다). **느슨하게 받는다**: 위 `FakeCapsuleSection`과
    # 같은 이유로 프롬프트에서 파싱해 온 것에 `Location`의 불변식을 다시 걸지 않는다. Fake는
    # 판정에 위치를 쓰지 않으므로 거부하지 않는 것만이 목적이다.
    location: dict[str, object] | None = None


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
    # 대상 함수 본문(`input: slice` 슬라이스 타깃이면 슬라이스 렌더). 위험 호출 탐지의 근거다.
    source: str = ""
    evidence: list[FakeEvidenceRef] = Field(default_factory=list)
    # source→sink 경로 구성용. 없으면 target.symbol만 쓴다.
    callers: list[str] = Field(default_factory=list)
    # 슬라이스 타깃의 v3 `slice_criterion`(`prompt_loader.FACT_KEYS`의 opt-in 키). Fake는 판정에
    # 안 쓰지만 사실 블록을 파싱할 때 거부하면 안 되므로 받는다(`FakeEvidenceRef.body`와 같은 처리).
    slice_criterion: dict[str, object] | None = None
    # PoV의 퍼즈 진입점(`prompt_loader.FACT_KEYS`의 opt-in 키 — `LLVMFuzzerTestOneInput`과 그 본문).
    # `slice_criterion`과 같은 처리다: Fake는 판정에 안 쓰지만 **받지 않으면 `extra="forbid"`가
    # 사실 블록 파싱을 죽인다**(`prompt_loader._validated_facts`가 "어댑터의 FakeContext와 함께
    # 고친다"고 적는 자리 — 2026-10-02에 이것을 빠뜨려 **132 failed**가 났다).
    fuzz_entrypoint: dict[str, object] | None = None
    # Critic의 홉 관측 수(`prompt_loader.FACT_KEYS`의 opt-in 키 — #600 H2ⓐ). `fuzz_entrypoint`와
    # 같은 처리: Fake는 판정에 안 쓰지만 안 받으면 `extra="forbid"`가 사실 블록 파싱을 죽인다.
    auditor_hops: int | None = None
    # Auditor의 슬라이스 정적 상태(`prompt_loader.FACT_KEYS`의 opt-in 키 — 이슈 #438 (b)).
    # `auditor_hops`와 같은 처리: Fake는 판정에 안 쓰지만 안 받으면 `extra="forbid"`가 사실 블록
    # 파싱을 죽인다.
    slice_state: dict[str, object] | None = None
    # Auditor의 상한에 빠진 걸친 함수 목록(`prompt_loader.FACT_KEYS`의 opt-in 키 — 이슈 #702).
    # `slice_state`와 같은 처리: Fake는 판정에 안 쓰지만 안 받으면 `extra="forbid"`가 사실 블록
    # 파싱을 죽인다.
    source_omitted_functions: dict[str, object] | None = None


class FakeModelError(RuntimeError):
    """Fake가 요청받은 스키마를 만들 수 없다. 조용히 빈 값을 내지 않는다."""


class FakeModelClient:
    """`ModelClient` 구현. 같은 프롬프트에는 항상 같은 출력을 낸다."""

    def __init__(
        self, *, model_name: str = "fake", scripted_attempts: Sequence[AttemptRecord] = ()
    ) -> None:
        self.model_name = model_name
        # 시도별 (출력 토큰, 종료 사유)를 **대본으로 줄 때만** 결과에 싣는다(#550) — GPU 없이
        # `length`와 여러 시도를 만들어 `model_call`의 쪼갠 값을 재기 위한 손잡이다. 기본(빈 대본)은
        # 기존 그대로 `attempt_log=()`, 곧 「안 쟀다」다: Fake는 종료 사유를 재지 않았으므로
        # `stop`을 지어내지 않는다.
        self._scripted_attempts = tuple(scripted_attempts)
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
        # 노드가 `tool` enum을 좁힌 하위 봉투를 보낼 수 있다(#698 · `signals.without_tools`)
        builder = next((_BUILDERS[cls] for cls in schema.__mro__ if cls in _BUILDERS), None)
        if builder is None:
            known = sorted(model.__name__ for model in _BUILDERS)
            raise FakeModelError(
                f"FakeModelClient가 모르는 출력 스키마: {schema.__name__}. 아는 것: {known}"
            )
        produced_by = ProducedBy(
            role=role, model=self.model_name, prompt_version=prompt.prompt_version
        )
        # `history`도 넘긴다 — PoV·Auditor 툴 루프의 fake는 홉(=history 길이)마다 다른 액션을 낸다
        # (`_build_pov_action`·`_build_auditor_action`). 다른 빌더는 홉을 안 보지만 dict
        # 디스패치라 시그니처가 하나다.
        value = builder(context, produced_by, history)
        usage = self._usage(prompt, value)
        if not self._scripted_attempts:
            return ModelResult(
                value=value, usage=usage, model=self.model_name, raw_text=value.model_dump_json()
            )
        # 대본이 있으면 합산 출력 토큰을 대본에서 낸다 — 합과 쪼갠 값이 어긋난 Fake를 안 만든다.
        log = self._scripted_attempts
        return ModelResult(
            value=value,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=sum(a.output_tokens or 0 for a in log),
                cached_tokens=usage.cached_tokens,
            ),
            model=self.model_name,
            attempts=len(log),
            raw_text=value.model_dump_json(),
            attempt_log=log,
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


# FACTS에 `source`가 없는 판(meta `facts_source: false` — Auditor v13·v14, 이슈 #67 ·
# ⏵ 2026-10-13 v15 · 2026-10-14 v16·v17 · 2026-10-16 v18 · 10-17 v19도 — 서빙 판)의 원문 자리.
# 그 판은 원문을 산문 블록 하나로만 싣는다(`prompts/auditor/v13/body.md`·`v14/body.md`·
# `v15/body.md`·`v16/body.md`·`v17/body.md`~`v19/body.md`의 이 머리와 다음 `### Facts` 사이).
_PROSE_SOURCE = re.compile(
    r"^### Target under analysis — original source code\n\n(?P<source>.*)\n\n### Facts\n",
    re.DOTALL | re.MULTILINE,
)


def _parse_context(body: str) -> FakeContext:
    matches = _FENCE.findall(body)
    if not matches:
        raise FakeModelError("프롬프트 본문에 ```json 사실 블록이 없다")
    facts = json.loads(matches[-1])
    if "source" not in facts:
        # 키가 없으면 원문은 산문 블록에 있다 — 빈 값으로 두면 위험 호출 탐지가 조용히 0이 되어
        # v11과 같은 입력에서 다른 판정을 낸다. 산문 블록도 못 찾으면 지어내지 않고 실패한다.
        prose = _PROSE_SOURCE.search(body)
        if prose is None:
            raise FakeModelError("사실 블록에 `source`가 없는데 산문 원문 블록도 없다")
        facts["source"] = prose.group("source")
    return FakeContext.model_validate(facts)


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


def _fake_path(context: FakeContext, symbol: str, sink: str) -> list[PathStep]:
    """Fake가 내는 source→sink 경로 — `_build_finding`과 `_build_audit_procedure`가 같이 쓴다."""
    source_symbol = context.callers[0] if context.callers else "input"
    return [
        PathStep(step=PathStepKind.SOURCE, symbol=source_symbol),
        PathStep(step=PathStepKind.PROPAGATE, symbol=symbol, note="길이 검증 없이 전달"),
        PathStep(step=PathStepKind.SINK, symbol=sink, note="고정 크기 버퍼"),
    ]


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
    path = _fake_path(context, symbol, sink)
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


def _build_audit_procedure(
    context: FakeContext, _produced_by: ProducedBy, _history: Sequence[ChatTurn]
) -> AuditProcedure:
    """v10 도구 루프의 최종 답 — `_build_finding`과 **같은 판정**을 `audit` 블록으로 낸다.

    `_build_finding`이 정적 신호 없이 `missing_evidence`를 내던 자리가 `decisive_code_missing`
    (`reachability_unknown`)이고, 신호가 있으면 `defeated`(`triggerable`)다 — 하네스가 그 결과값으로
    발급 `missing_id`를 붙이는 길(`nodes/auditor._finding_from_procedure`)이 Fake run에서도 서게
    한다. ID·`produced_by`는 모델이 못 내는 값이라 여기 없다(`ids`를 읽지 않으므로
    `FakeModelError`도 없다). 경로·CWE·제목·신뢰도·근거는 `_build_finding`과 같은 값이다.
    """
    target = context.target or Location(file="unknown")
    symbol = target.symbol or "unknown"
    risky = risky_calls_in(context.source)
    sink = risky[0] if risky else "unknown"
    has_signal = _has_static_signal(context)
    has_build = any(ref.kind is EvidenceKind.BUILD and ref.ok for ref in context.evidence)

    return AuditProcedure(
        audit=AuditBlock(
            category="spatial_bounds",
            point=AuditPoint(symbol=symbol, expression=f"{sink}(...)"),
            origin=AuditOrigin(kind="input", fact="fake: 입력 길이가 그대로 전달된다"),
            path=_fake_path(context, symbol, sink),
            defense=AuditDefense(
                expression="none on visible path",
                status="absent",
                gap=None,
                fact="fake: 길이 검증이 보이지 않는다",
            ),
            refutation=AuditRefutation(
                result="defeated" if has_signal else "decisive_code_missing",
                witness="fake: 길이가 버퍼보다 크면 넘친다",
                missing_information=[]
                if has_signal
                else ["fake: 정적 분석 신호 또는 호출 그래프 근거"],
            ),
        ),
        trigger_status="triggerable" if has_signal else "reachability_unknown",
        confidence=0.6 if risky else 0.3,
        cwe=RISKY_CALLS.get(sink, "CWE-20"),
        title=f"{symbol}에서 검증 없는 {sink}",
        rca_draft=f"{symbol}이 입력 길이를 확인하지 않고 {sink}로 전달한다.",
        evidence_refs=[ref.evidence_id for ref in context.evidence],
        repro_spec=_repro_spec(target, sink) if has_build else None,
    )


def _build_decision(
    context: FakeContext, produced_by: ProducedBy, _history: Sequence[ChatTurn]
) -> Decision:
    """7단계 Critic 출력.

    call graph 근거가 있으면 근거를 붙여 `uphold`, 없으면 **근거 없는 `rebut`**을 낸다.
    후자는 agent-contracts.md §2.3대로 오케스트레이터가 `need_evidence`로 정규화하고 지표에
    남긴다 — 그 경로가 실제로 실행되지 않으면 구현되어 있는지 알 수 없다.

    ⏵ 2026-10-13 (ADR 0015) — 근거 없는 `rebut`은 `rebuttal_axis=[]`다. 축이 `RebuttalAxis` 안의
    값이면 그것이 근거라 `evidence_refs`가 비어도 근거 있는 반박이 되고 정규화 경로를 못 탄다.
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
        # `guard`·`reachability`는 `RebuttalAxis`의 값이다. 근거 없는 rebut은 축도 비어 있어야
        # `Decision.is_grounded`가 거짓이고 정규화 대상이 된다(ADR 0015).
        rebuttal_axis=["guard", "reachability"] if grounded else [],
        # 근거 없는 rebut은 rationale만 있고 axis·evidence_refs가 비어 정규화 대상이 된다.
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
    """PoV 툴 루프의 fake 액션 (Option C). 홉을 `history` 길이로 가른다.

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


def _auditor_investigation(
    context: FakeContext, history: Sequence[ChatTurn]
) -> dict[str, object] | None:
    """Auditor 툴 루프의 fake **조사 홉** (#419) — 루프 봉투 셋(`AuditorAction` ·
    `AuditorProcedureAction` · ⏵ 2026-10-09 `AuditorProcedureNullableCweAction`)이 같은 액션을
    낸다. 홉을 `history` 길이로 가르고, `answer` 홉이면
    `None`이다(그 홉의 최종 답은 봉투마다 다르다 — 호출한 빌더가 싣는다).

    hop0(history 빔)은 `lookup` — 대상 함수를 부르는 쪽(없으면 대상 자신)의 본문을 이름으로
    묻는다. hop1은 `probe` — 읽기 전용 도구(`semgrep`)부터 한 번 돌린다. `spec`은 싣지 않는다:
    ⏵ 2026-10-09 — **Auditor 경로가** 그 값을 도구에 안 싣기 때문이다(`nodes/auditor._probe`의
    `ToolArgs`에 `input_bytes`가 없다. 컨테이너 `poc` 자체는 ADR 0014로 받을 수 **있다**).
    그래서 `spec`은 기록일 뿐이고, 그 경로가 싣게 되기(이슈 #419) 전에 fake가 그 길을 태울 이유가
    없다. hop≥2는 `answer`다.
    ⏵ 2026-10-13(#419) — 노드는 이제 `spec`을 싣는다. fake는 **그래도 `spec`을 안 낸다**: 이 fake가
    내는 홉 열이 dev_fake trace와 그것을 세는 검사들의 기준이라 바꾸지 않았다(`probe`에 입력이 가는
    길은 `tests/test_auditor_loop.py`가 `_ScriptedModel`로 건다).

    홉을 `history`로 가르는 자리는 `_build_pov_action`과 같다 — 홉 번호가 따로 안 넘어온다.
    다만 거기는 「비었나」만 보지만 여기는 **홉 번호**가 필요하다: 매 홉이 user 턴(그 홉 본문)과
    assistant 턴(그 홉 응답) **둘**을 쌓으므로(`nodes/pov.py`·`nodes/scout.py`의 `new_history`)
    길이는 0·2·4가 되고 홉은 `len // 2`다. `len == 1`로 가르면 어느 홉도 안 맞는다.
    결정론이라 dev_fake run이 재현 가능하다.

    실제 모델은 관측을 보고 lookup/probe/answer를 스스로 고르고 몇 번이고 되풀이한다 — fake는
    그 루프 배선(lookup→조회→probe→도구→answer)이 한 번씩 도는지만 고정한다.
    """
    hop = len(history) // 2
    if hop == 0:
        target = context.target
        name = context.callers[0] if context.callers else (target.symbol if target else None)
        return {
            "action": "lookup",
            "requests": [
                RequestedContext(
                    kind="function",
                    target=name or "",
                    purpose="fake: 대상으로 들어오는 경로를 읽는다",
                    request_code="fake_auditor_lookup",
                )
            ],
            "rationale": "fake: 근거를 이름으로 조회한다",
        }
    if hop == 1:
        return {
            "action": "probe",
            "tool": "semgrep",
            "rationale": "fake: 읽기 전용 도구로 확인한다",
        }
    return None


_AUDITOR_ANSWER_RATIONALE = "fake: 관측을 받았으니 감사를 끝낸다"


def _build_auditor_action(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> AuditorAction:
    """Auditor 툴 루프의 fake 액션 (#419) — 최종 답이 `Finding`인 봉투(v8·v9). 조사 홉은
    `_auditor_investigation`이 정하고, hop≥2의 `answer`는 `_build_finding`이 최종 Finding을
    채워 루프가 끝난다.

    ⚠ `answer`가 `_build_finding`을 재사용하므로 그 함수의 전제가 여기도 걸린다 — FACTS 블록이
    발급한 `finding_id`·`candidate_id`(·정적 신호가 없으면 `missing_id`)를 `ids`에서 읽는다.
    사실 블록은 hop0 user 턴(`history[0]`)에서 읽으므로(`FakeModelClient.complete`) 루프가
    그 턴을 끝까지 `history` 맨 앞에 보존해야 한다.
    """
    hop = _auditor_investigation(context, history)
    if hop is not None:
        return AuditorAction(**hop)
    return AuditorAction(
        action="answer",
        finding=_build_finding(context, produced_by, history),
        rationale=_AUDITOR_ANSWER_RATIONALE,
    )


def _build_auditor_procedure_action(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> AuditorProcedureAction:
    """Auditor 툴 루프의 fake 액션 — 최종 답이 `AuditProcedure`인 봉투(v10). 조사 홉은
    `_build_auditor_action`과 같고(`_auditor_investigation`), `answer`는 `_build_audit_procedure`다.
    그 빌더는 `ids`를 안 읽는다 — v10의 모델은 발급 ID를 못 낸다.

    **이 항목이 빌더 표에 없으면** Fake가 v10을 고르는 run이 `FakeModelError`로 죽는다.
    """
    hop = _auditor_investigation(context, history)
    if hop is not None:
        return AuditorProcedureAction(**hop)
    return AuditorProcedureAction(
        action="answer",
        procedure=_build_audit_procedure(context, produced_by, history),
        rationale=_AUDITOR_ANSWER_RATIONALE,
    )


def _build_auditor_procedure_nullable_cwe_action(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> AuditorProcedureNullableCweAction:
    """v11 봉투(⏵ 2026-10-09 — 이슈 #525)의 fake 액션. v10과 **같은 판정**을 낸다 — 최종 답의
    클래스만 `AuditProcedureNullableCwe`다. fake 판정은 `triggerable`·`reachability_unknown`뿐이라
    `cwe`가 null인 길(`no_signal`)은 여기서 안 선다.

    **이 항목이 빌더 표에 없으면** Fake가 v11을 고르는 run이 `FakeModelError`로 죽는다.
    """
    hop = _auditor_investigation(context, history)
    if hop is not None:
        return AuditorProcedureNullableCweAction(**hop)
    procedure = _build_audit_procedure(context, produced_by, history)
    return AuditorProcedureNullableCweAction(
        action="answer",
        procedure=AuditProcedureNullableCwe.model_validate(procedure.model_dump()),
        rationale=_AUDITOR_ANSWER_RATIONALE,
    )


def _build_auditor_procedure_audit_always_action(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> AuditorProcedureAuditAlwaysAction:
    """v12 봉투(⏵ 2026-10-10 — 이슈 #578)의 fake 액션. v10·v11과 **같은 판정**을 낸다 — 최종 답의
    클래스만 `AuditProcedureAuditAlways`다. fake 판정은 `triggerable`·`reachability_unknown`뿐이라
    `no_signal`의 `audit`(`category` `none` 등)는 여기서 안 선다.

    **이 항목이 빌더 표에 없으면** Fake가 v12를 고르는 run이 `FakeModelError`로 죽는다.
    """
    hop = _auditor_investigation(context, history)
    if hop is not None:
        return AuditorProcedureAuditAlwaysAction(**hop)
    procedure = _build_audit_procedure(context, produced_by, history)
    return AuditorProcedureAuditAlwaysAction(
        action="answer",
        procedure=AuditProcedureAuditAlways.model_validate(procedure.model_dump()),
        rationale=_AUDITOR_ANSWER_RATIONALE,
    )


def _build_auditor_procedure_probe_input_action(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> AuditorProcedureProbeInputAction:
    """v15 봉투(⏵ 2026-10-13)의 fake 액션 — v12와 **같은 판정**이다(최종 답 클래스도
    `AuditProcedureAuditAlways`). 달라진 것은 봉투의 `spec` 설명뿐이라 fake의 홉 열은 안 바뀐다 —
    `probe`에 `spec`을 안 싣는 것은 그대로다(`_auditor_investigation`).

    **이 항목이 빌더 표에 없으면** Fake가 v15를 고르는 run이 `FakeModelError`로 죽는다.
    """
    hop = _auditor_investigation(context, history)
    if hop is not None:
        return AuditorProcedureProbeInputAction(**hop)
    procedure = _build_audit_procedure(context, produced_by, history)
    return AuditorProcedureProbeInputAction(
        action="answer",
        procedure=AuditProcedureAuditAlways.model_validate(procedure.model_dump()),
        rationale=_AUDITOR_ANSWER_RATIONALE,
    )


def _build_auditor_procedure_probe_input_discriminated_action(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> AuditorProcedureProbeInputDiscriminatedAction:
    """v18 봉투(⏵ 2026-10-16)의 fake 액션 - v15와 같은 판정이다. 달라진 것은 디코더가 받는 JSON
    스키마뿐이고 검증기는 같다."""
    base = _build_auditor_procedure_probe_input_action(context, produced_by, history)
    return AuditorProcedureProbeInputDiscriminatedAction(**base.model_dump())


def _build_pov_action_named(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> PovActionNamed:
    """형제 봉투(`PovActionNamed` · v5~v10)의 fake — `_build_pov_action`과 같은
    액션(`poc_sha256` 없음)."""
    return PovActionNamed(**_build_pov_action(context, produced_by, history).model_dump())


def _build_pov_discriminated_action(
    context: FakeContext, produced_by: ProducedBy, history: Sequence[ChatTurn]
) -> PovDiscriminatedAction:
    """v11 봉투(`PovDiscriminatedAction` · 서빙 판)의 fake — 같은 액션. 판별 합은 요청 스키마의
    일이라 값은 `PovActionNamed`와 같다(`tests/test_pov_v11.py`가 v11 루프에서 낸 hop을 그 스키마에
    대조한다)."""
    return PovDiscriminatedAction(**_build_pov_action(context, produced_by, history).model_dump())


# `PovSpec`은 모델 출력 스키마로 직접 요청되지 않는다 — pov 루프는 `PovAction`(⏵ 2026-10-11 — v5는
# 형제 `PovActionNamed` · ⏵ 2026-10-13 — v6도 그 형제 · ⏵ 2026-10-14 — v7도 그 형제 ·
# ⏵ 2026-10-15 — v8도 그 형제이고
# v8이 서빙 판이다)을 요청하고, `gen`이
# 실은 `PovSpec`은 `_build_pov_action`이 `_build_pov`로 채운다(단발판 제거로 엔트리는 죽어 뺐다).
# `AuditorAction.spec`(`probe`가 싣는 `PovSpec`)도 같다 — 봉투 안에 들어가고 따로 요청되지 않는다.
# fake의 `probe`는 `spec`을 아예 싣지 않는다(`_auditor_investigation`).
# Auditor 루프 봉투는 **여섯이고 여섯 다 든다**(⏵ 2026-10-09 — v11 봉투가 더해져 셋 · ⏵ 2026-10-10 —
# v12 봉투가 더해져 넷 · ⏵ 2026-10-13 — v15 봉투가 더해져 다섯 · ⏵ 2026-10-16 — v18 봉투가 더해져
# 여섯. 그 전은 둘)
# — 빠지면 그 봉투를 고르는 판의 Fake run이 죽는다
# (`tests/test_auditor_loop.py`가 `nodes/auditor`의 루프 봉투 집합과 대조한다).
_BUILDERS = {
    Candidate: _build_candidate,
    ScoutResponse: _build_scout_response,
    Finding: _build_finding,
    Decision: _build_decision,
    DecisionDraft: _build_decision_draft,
    PovAction: _build_pov_action,
    PovActionNamed: _build_pov_action_named,
    PovDiscriminatedAction: _build_pov_discriminated_action,
    AuditorAction: _build_auditor_action,
    AuditorProcedureAction: _build_auditor_procedure_action,
    AuditorProcedureNullableCweAction: _build_auditor_procedure_nullable_cwe_action,
    AuditorProcedureAuditAlwaysAction: _build_auditor_procedure_audit_always_action,
    AuditorProcedureProbeInputAction: _build_auditor_procedure_probe_input_action,
    AuditorProcedureProbeInputDiscriminatedAction: (
        _build_auditor_procedure_probe_input_discriminated_action
    ),
}
