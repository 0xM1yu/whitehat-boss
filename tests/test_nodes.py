"""파이프라인 노드와 프롬프트 계약 테스트.

**측정값을 하드코딩하지 않는다.** 점수·후보 수·제거율은 이 프로젝트가 재려는 값이지
테스트가 고정할 상수가 아니다. 여기서 고정하는 것은 관계("benign은 임계값 미만"),
계약("Critic 프롬프트에 rca_draft가 없다"), 그리고 **산출 가능성**(제거율을 trace에서
계산할 수 있고 0%도 100%도 아니다)뿐이다.
"""

import dataclasses
import hashlib
import json
import re
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.adapters.factory import build_deps
from src.adapters.fake_model import _parse_context
from src.adapters.fake_retriever import read_symbol_source
from src.config import load_config
from src.interfaces.model_client import (
    ChatTurn,
    ModelGatewayError,
    ModelResult,
    SchemaParseError,
    Usage,
)
from src.interfaces.tool_runner import INPUT_PLACEHOLDER, SandboxSpec, ToolArgs, ToolRunner
from src.orchestrator import gate, prompt_loader, runner
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import (
    auditor,
    critic,
    gate_node,
    index,
    pov,
    preprocess,
    report,
    retrieval,
    scout,
    signals,
    validate,
)
from src.orchestrator.poc_assembly import assemble_poc
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.prompt_loader import (
    PROMPTS_DIR,
    PromptError,
    build_facts,
    evidence_summaries,
    load_prompt,
)
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind, TraceWriter
from src.schemas import (
    ArtifactKind,
    CapsuleLevel,
    EvidenceKind,
    FindingState,
    Location,
    ProducedBy,
    ReasonCode,
    ReproOrigin,
    ReproSpec,
    Role,
    Verdict,
)
from src.schemas.candidate import Candidate, SliceRef
from src.schemas.decision import Decision
from src.schemas.enums import ScoutSignal
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, PathStep, PathStepKind
from src.schemas.scout_response import AnalysisSummary, RequestedContext, ScoutResponse
from src.tokens import estimate_tokens
from tests.conftest import declare_scout_input

RUN_ID = "run_20260901_0001"
NOW = datetime(2026, 9, 1, tzinfo=UTC)

VULNERABLE_C = """\
#include <string.h>

void parse_header(const char *src, int len) {
    char buf[64];
    memcpy(buf, src, len);
}
"""

BENIGN_C = """\
#include <string.h>

void safe_copy(char *dst, const char *src) {
    strncpy(dst, src, 63);
    dst[63] = '\\0';
}
"""

VULN_TARGET = Location(file="parser.c", symbol="parse_header", line_start=3, line_end=6)
BENIGN_TARGET = Location(file="util.c", symbol="safe_copy", line_start=3, line_end=6)


class RecordingModelClient:
    """실제 클라이언트에 위임하면서 프롬프트를 남긴다. 프롬프트 계약 검사용이다."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.prompts: list[tuple[Role, object]] = []

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        self.prompts.append((role, prompt))
        return self._inner.complete(role=role, prompt=prompt, schema=schema, history=history)

    def last_for(self, role: Role):  # noqa: ANN201
        return next(prompt for seen, prompt in reversed(self.prompts) if seen is role)


@pytest.fixture
def ctx(tmp_path: Path) -> RunContext:
    """dev_fake 프로필로 배선된 실행 문맥. 대상 트리는 임시 디렉터리에 만든다."""
    root = tmp_path / "target"
    root.mkdir()
    (root / "parser.c").write_text(VULNERABLE_C, encoding="utf-8")
    (root / "util.c").write_text(BENIGN_C, encoding="utf-8")

    config = load_config("dev_fake")
    trace = TraceWriter(RUN_ID, clock=lambda: NOW)
    holder: list[RunContext] = []
    deps = build_deps(
        config,
        run_id=RUN_ID,
        source_root=root,
        artifacts_root=tmp_path / "artifacts",
        on_cache=lambda key, hit: holder[0].record_cache(key, hit),
    )
    context = RunContext(
        run_id=RUN_ID,
        config=config,
        models=RecordingModelClient(deps.models),
        retriever=deps.retriever,
        tools=deps.tools,
        store=deps.store,
        ids=deps.ids,
        trace=trace,
        ledger=BudgetLedger(config.budget, trace),
        policy=FixedThresholdPolicy(config.thresholds),
        read_source=partial(read_symbol_source, root),
        monotonic_ms=lambda: 0,
    )
    holder.append(context)
    return context


def _root_of(ctx: RunContext) -> Path:
    """픽스처가 `partial(read_symbol_source, root)`로 주입한 대상 루트."""
    return Path(ctx.read_source.args[0])


def _triage(ctx: RunContext, target: Location):  # noqa: ANN202
    """0~4단계를 대상 하나에 대해 돌린다. 노드 조립은 C5 러너의 몫이라 손으로 엮는다."""
    artifacts = preprocess.run(ctx, source_root=_root_of(ctx))
    artifact = next(a for a in artifacts if a.path == target.file)
    graph = index.run(ctx, workdir=target.file)
    found = signals.with_signal(signals.run(ctx, workdir=target.file))
    capsule = retrieval.run(ctx, target=target, level=CapsuleLevel.LIGHT)
    candidate = scout.run(ctx, artifact=artifact, capsule=capsule, evidences=(graph, *found))
    return candidate, capsule, (graph, *found)


# --- 0~3단계 ----------------------------------------------------------------


def test_preprocess_classifies_and_stores_relative_paths(ctx: RunContext) -> None:
    artifacts = preprocess.run(ctx, source_root=_root_of(ctx))

    assert [a.path for a in artifacts] == ["parser.c", "util.c"]  # 정렬 순서가 ID 순서다
    assert all(a.kind is ArtifactKind.SOURCE for a in artifacts)
    assert all(not Path(a.path).is_absolute() for a in artifacts)
    assert ctx.store.get("artifact", artifacts[0].artifact_id) is not None


def test_include_fragment_is_classified_as_analyzable_source() -> None:
    """`.inc`가 UNKNOWN이면 `runner._ANALYZABLE`이 걸러 실제 코드가 통째로 빠진다.

    실측 샘플은 `target.c`가 28바이트짜리 `#include` 한 줄이고 코드 17KB가 전부
    `.inc`에 있다. 걸러져도 **에러가 나지 않으므로** "그 파일에 취약점이 없다"와
    구별되지 않는다 — 그래서 분류만이 아니라 필터를 통과하는지까지 본다.
    """
    kind = preprocess.classify(Path("session_core.inc"))

    assert kind is ArtifactKind.SOURCE
    assert kind in runner._ANALYZABLE


def test_static_tool_failure_is_recorded_not_raised(ctx: RunContext) -> None:
    """도구 실패는 예외가 아니라 Evidence로 남는다 (engineering-guide.md §8.3)."""
    evidences = signals.run(ctx, workdir="../outside")

    assert evidences and all(not ev.ok and ev.error for ev in evidences)
    assert signals.with_signal(evidences) == ()


def test_no_hit_static_run_is_recorded_but_is_not_a_signal(ctx: RunContext) -> None:
    """"돌렸는데 없었다"와 "신호가 있다"는 다른 사건이다.

    빌드 성공은 발견 여부와 무관하게 신호다(재현 명세를 쓸 수 있는지가 여기서 갈린다).
    걸러지는 것은 **아무것도 못 찾은 정적분석**뿐이다.
    """
    clean = signals.run(ctx, workdir="util.c")
    hit = signals.run(ctx, workdir="parser.c")

    assert all(ev.ok for ev in clean)  # 신호 부재로 기록된다
    assert not [ev for ev in signals.with_signal(clean) if ev.kind is EvidenceKind.STATIC]
    assert [ev for ev in signals.with_signal(hit) if ev.kind is EvidenceKind.STATIC]


def test_capsule_budget_comes_from_config(ctx: RunContext) -> None:
    capsule = retrieval.run(ctx, target=VULN_TARGET, level=CapsuleLevel.LIGHT)

    assert capsule.budget_tokens == ctx.config.thresholds.budget_for(CapsuleLevel.LIGHT)
    assert capsule.used_tokens <= capsule.budget_tokens
    assert any(e.kind is EventKind.CACHE for e in ctx.trace.events)


def test_capsule_expansion_stops_at_configured_limit(ctx: RunContext) -> None:
    candidate, capsule, _ = _triage(ctx, VULN_TARGET)
    _, missings = auditor.run(ctx, candidate=candidate, capsule=capsule)
    if not missings:
        pytest.skip("이 픽스처에서는 Auditor가 근거를 요청하지 않았다")

    over = capsule.transition(expansion_count=ctx.config.thresholds.capsule_expansion_max)
    _, queued = retrieval.expand(ctx, capsule=over, need=missings[0], candidate=candidate)

    assert queued.state is FindingState.QUEUED


# --- 4단계 Scout ------------------------------------------------------------


def test_benign_target_stays_below_threshold(ctx: RunContext) -> None:
    """benign이 걸러지는 것이 이 픽스처의 핵심 시나리오다. 점수 값은 단언하지 않는다."""
    candidate, _, _ = _triage(ctx, BENIGN_TARGET)

    assert candidate.score < ctx.config.thresholds.scout_threshold
    assert candidate.state is FindingState.QUEUED  # 폐기가 아니라 보류다


def test_vulnerable_target_is_promoted(ctx: RunContext) -> None:
    candidate, _, _ = _triage(ctx, VULN_TARGET)

    assert candidate.score >= ctx.config.thresholds.scout_threshold
    assert candidate.state is FindingState.TRIAGED


def test_scout_records_creation_then_triage(ctx: RunContext) -> None:
    """제거율의 분모(생성)와 분자(승격)가 둘 다 trace에 남아야 한다."""
    _triage(ctx, VULN_TARGET)

    rules = [
        e.data["rule"]
        for e in ctx.trace.events
        if e.kind is EventKind.TRANSITION and e.stage == Stage.SCOUT
    ]
    assert rules == ["C-1", "C-2"]


def test_node_does_not_compare_thresholds_itself(ctx: RunContext) -> None:
    """판정 근거가 정책 이벤트에 남는다 — 노드가 직접 비교했다면 이 기록이 없다."""
    _triage(ctx, VULN_TARGET)

    decisions = [e for e in ctx.trace.events if e.kind is EventKind.POLICY_DECISION]
    triage = next(e for e in decisions if e.data["point"] == "scout_triage")
    assert triage.data["basis.scout_threshold"] == ctx.config.thresholds.scout_threshold
    assert triage.data["policy"] == "fixed_threshold"


def test_open_session_makes_no_candidate_and_finish_completes_it(ctx: RunContext) -> None:
    """`run()` = `open_session()` + `finish()`다(ADR 0012 결정 2). 나눠 불러도 결과가 같아야 한다.

    `open_session`은 hop0 호출만 한다 — 후보를 만들지 않는다. 만드는 것은 `finish()`이고,
    그래서 그 사이에는 이 candidate_id로 저장된 것이 아무것도 없어야 한다.
    """
    artifacts = preprocess.run(ctx, source_root=_root_of(ctx))
    artifact = next(a for a in artifacts if a.path == VULN_TARGET.file)
    graph = index.run(ctx, workdir=VULN_TARGET.file)
    found = signals.with_signal(signals.run(ctx, workdir=VULN_TARGET.file))
    capsule = retrieval.run(ctx, target=VULN_TARGET, level=CapsuleLevel.LIGHT)

    session = scout.open_session(ctx, artifact=artifact, capsule=capsule, evidences=(graph, *found))

    assert session.hop == 0
    assert session.signal is None, "dev_fake는 v1/v2 스키마라 신호가 없어야 한다"
    assert ctx.store.get("candidate", session.candidate_id) is None, (
        "open_session이 후보를 만들면 안 된다 — finish()가 만든다"
    )

    candidate = scout.finish(ctx, session)

    assert candidate.candidate_id == session.candidate_id
    assert candidate.state is FindingState.TRIAGED
    rules = [
        e.data["rule"]
        for e in ctx.trace.events
        if e.kind is EventKind.TRANSITION and e.stage == Stage.SCOUT
    ]
    assert rules == ["C-1", "C-2"], "쪼갠 뒤에도 creation_then_triage 검사와 같은 전이여야 한다"


def _ne_session(
    *,
    candidate_id: str = "cand_ne_0001",
    signal: ScoutSignal = ScoutSignal.NEED_EVIDENCE,
    score: float = 0.8,
    requested: tuple[RequestedContext, ...] = (
        RequestedContext(kind="function", target="doit", purpose="확인", request_code="stl:err"),
    ),
    hop: int = 0,
    history: tuple[ChatTurn, ...] = (
        ChatTurn(speaker="user", content="HOP0 BODY"),
        ChatTurn(speaker="assistant", content="HOP0 RAW"),
    ),
) -> "scout.ScoutSession":
    """손으로 짠 v3 세션 — `continue_session`·`finish`를 렌더 파이프라인 없이 단위로 잰다."""
    candidate = Candidate(
        candidate_id=candidate_id,
        run_id=RUN_ID,
        artifact_id="artifact_0001",
        location=VULN_TARGET,
        score=score,
        reason_codes=[f"{signal.value}:missing_request:function:doit"],
        state=FindingState.CANDIDATE,
    )
    return scout.ScoutSession(
        candidate_id=candidate_id,
        role=Role.SCOUT,
        slice=None,
        hop=hop,
        candidate=candidate,
        summary=AnalysisSummary(claim="fake", requested_context=list(requested)),
        signal=signal,
        requested_context=requested,
        raw_text="HOP0 RAW",
        usage=Usage(input_tokens=100, output_tokens=20, cached_tokens=0),
        latency_s=0.05,
        prefix="SYSTEM PREFIX",
        prompt_version="v3",
        history=history,
    )


def test_continue_session_sends_full_history_and_advances_the_hop(ctx: RunContext) -> None:
    """`continue_session`은 `[user hop0, assistant raw0, user followup]`을 그대로 보낸다.

    `prompt.body`는 되묻기 본문 자체다(전체 프롬프트를 다시 렌더하지 않는다) — 렌더 자체는
    hop0(`open_session`)에서만 하고, hop≥1은 resolver가 만든 후속 문답만 오간다.
    """
    session = _ne_session()
    captured: dict[str, object] = {}

    class _Stub:
        def complete(self, *, role: Role, prompt, schema, history=()):  # noqa: ANN001, ANN202
            captured["role"] = role
            captured["prompt"] = prompt
            captured["schema"] = schema
            captured["history"] = tuple(history)
            value = ScoutResponse(
                candidate=session.candidate.model_copy(
                    update={"reason_codes": ["triage_review"]}
                ),
                analysis_summary=AnalysisSummary(claim="hop1"),
            )
            return ModelResult(
                value=value,
                usage=Usage(input_tokens=110, output_tokens=15, cached_tokens=90),
                model="stub",
                raw_text="HOP1 RAW",
            )

    stub_ctx = dataclasses.replace(ctx, models=_Stub())

    next_session = scout.continue_session(stub_ctx, session, "FOLLOWUP TEXT")

    assert next_session.candidate_id == session.candidate_id, "루프 동안 candidate_id는 하나다"
    assert next_session.hop == 1
    assert captured["history"] == session.history
    assert captured["prompt"].body == "FOLLOWUP TEXT"
    assert captured["prompt"].prefix == session.prefix
    assert captured["schema"] is ScoutResponse
    assert next_session.signal is ScoutSignal.REVIEW
    assert next_session.raw_text == "HOP1 RAW"
    # 다음 홉으로 이어질 history에 이번 문답이 쌓인다.
    assert next_session.history == (
        *session.history,
        ChatTurn(speaker="user", content="FOLLOWUP TEXT"),
        ChatTurn(speaker="assistant", content="HOP1 RAW"),
    )


def test_finish_uses_the_signal_for_the_triage_decision_when_present(ctx: RunContext) -> None:
    """v3 신호가 있으면 `finish()`가 점수 대신 그 신호로 판정한다(ADR 0012 결정 2).

    `reject` + 점수 1.0 — 점수만 보면 승격되지만, 최종 신호가 판정이라 QUEUED다(D82 ⑤).
    """
    session = _ne_session(candidate_id="cand_ne_reject", signal=ScoutSignal.REJECT, score=1.0)

    candidate = scout.finish(ctx, session)

    assert candidate.state is FindingState.QUEUED, "reject 신호인데 점수로 승격됐다"
    decisions = [e for e in ctx.trace.events if e.kind is EventKind.POLICY_DECISION]
    triage = next(e for e in decisions if e.data["point"] == "scout_triage")
    assert triage.data["basis.signal"] == "triage_reject"
    rules = [
        e.data["rule"]
        for e in ctx.trace.events
        if e.kind is EventKind.TRANSITION and e.stage == Stage.SCOUT
    ]
    assert rules == ["C-1", "C-3"]
    transition = next(
        e for e in ctx.trace.events if e.kind is EventKind.TRANSITION and e.data["rule"] == "C-3"
    )
    assert transition.data["reason"] == "scout_reject"


def test_finish_promotes_on_review_signal_regardless_of_score(ctx: RunContext) -> None:
    """review 신호 + 낮은 점수 — v3는 약한 review에 낮은 점수를 허용한다(프롬프트 계약)."""
    session = _ne_session(candidate_id="cand_ne_review", signal=ScoutSignal.REVIEW, score=0.1)

    candidate = scout.finish(ctx, session)

    assert candidate.state is FindingState.TRIAGED
    transition = next(
        e for e in ctx.trace.events if e.kind is EventKind.TRANSITION and e.data["rule"] == "C-2"
    )
    assert transition.data["reason"] == "scout_promoted"


def test_resolve_output_schema_rejects_names_outside_the_allowlist() -> None:
    """허용집합 밖 `output_schema` 이름은 런타임 첫 호출(GPU)까지 미루지 않고 여기서 막는다.

    `run`이 이 가드를 실제로 태우는 것은 `test_scout_pool_v3_...`(e2e)가 보고, 여기서는
    가드 자체의 양·음 분기를 무는다 — 없으면 「집합 밖은 막힌다」는 서술이 검증되지 않는다
    (리뷰 #239 MEDIUM-1).
    """
    assert scout.resolve_output_schema("Candidate").__name__ == "Candidate"
    assert scout.resolve_output_schema("ScoutResponse").__name__ == "ScoutResponse"
    with pytest.raises(PromptError):
        scout.resolve_output_schema("DecisionDraft")


# --- 5~6단계 ----------------------------------------------------------------


def test_auditor_creates_finding_and_materializes_missing_evidence(ctx: RunContext) -> None:
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)

    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule)

    assert finding.state is FindingState.HYPOTHESIZED
    assert [m.missing_id for m in missings] == finding.missing_evidence
    assert all(m.blocking and m.need and m.how_to_obtain for m in missings)
    created = next(
        e for e in ctx.trace.events if e.kind is EventKind.TRANSITION and e.data["rule"] == "F-1"
    )
    assert created.data["reason"] == ReasonCode.AUDITOR_HYPOTHESIZED.value


def _set_synthesize(ctx: RunContext, value: int) -> None:
    thresholds = ctx.config.thresholds.transition(synthesize_repro_spec=value)
    ctx.config = ctx.config.transition(thresholds=thresholds)


def test_synthesize_off_leaves_an_empty_repro_spec(ctx: RunContext) -> None:
    """#S7 끈 경우(0): 모델이 명세를 비워 내면 그대로 비어 F-9로 멈춘다. 합성 이벤트 없음.

    ⏵ 2026-10-05 — `config/budgets.yaml`이 `synthesize_repro_spec: 1`을 선언해 **기본값이 1로
    바뀌었다.** 그래서 이 검사는 커밋된 기본값에 기대지 않고 0을 **주입한다** — 무는 명제는
    「켜짐/꺼짐 중 어느 것이 기본인가」가 아니라 「0이면 비는 채로 멈춘다」다.
    """
    _set_synthesize(ctx, 0)
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)

    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)

    assert finding.repro_spec is None
    assert not [e for e in ctx.trace.events if e.kind is EventKind.REPRO_SPEC_SYNTHESIZED]


def test_synthesize_fills_a_harness_repro_spec(ctx: RunContext) -> None:
    """#S7 켜짐(1): 비워 낸 명세를 후보 위치로 합성한다 — `origin=harness`, 신호에 crash 낱말,
    8단계가 도구를 역선택할 수 있게. 합성 이벤트가 그 finding을 가리킨다."""
    _set_synthesize(ctx, 1)
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)

    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)

    assert finding.repro_spec is not None
    assert finding.repro_spec.origin is ReproOrigin.HARNESS
    assert "crash" in finding.repro_spec.expected_signal
    assert validate.tool_for(finding.repro_spec.expected_signal) is not None
    assert BENIGN_TARGET.symbol in finding.repro_spec.expected_signal
    event = next(e for e in ctx.trace.events if e.kind is EventKind.REPRO_SPEC_SYNTHESIZED)
    assert event.subject_id == finding.finding_id
    assert event.data["steps"] == len(finding.repro_spec.steps)


def test_a_model_claimed_harness_origin_is_stamped_back_to_model(ctx: RunContext) -> None:
    """스키마가 `origin`을 노출하므로 모델이 `harness`를 참칭할 수 있다 — 하네스가 `model`로
    덮는다(발급값 도장 #279와 같은 결). 합성이 꺼져 있어도 덮는다."""
    claimed = Finding(
        finding_id="find_0001",
        run_id=RUN_ID,
        candidate_id="cand_0001",
        title="t",
        cwe="CWE-787",
        state=FindingState.HYPOTHESIZED,
        confidence=0.6,
        path=[],
        evidence_refs=[],
        produced_by=ProducedBy(role=Role.AUDITOR, model="fake", prompt_version="v1"),
        repro_spec=ReproSpec(
            steps=["./x"], expected_signal="sanitizer crash", origin=ReproOrigin.HARNESS
        ),
    )
    candidate, _, _ = _triage(ctx, BENIGN_TARGET)

    stamped = auditor._ensure_repro_origin(ctx, claimed, candidate, finding_id="find_0001")

    assert stamped.repro_spec.origin is ReproOrigin.MODEL
    assert not [e for e in ctx.trace.events if e.kind is EventKind.REPRO_SPEC_SYNTHESIZED]


def test_pov_open_hop_puts_the_finding_in_the_prompt(ctx: RunContext) -> None:
    """PoV 루프의 hop0(`open_hop`)이 Finding을 프롬프트에 실어 PoV 역할로 모델을 부르고
    `PovAction`을 받는다. 노드 계약만 고정한다 — 루프 제어·PoC 바이트는 test_pov_loop이 본다.
    """
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)

    hop = pov.open_hop(ctx, finding=finding, capsule=capsule)

    # hop0은 사실 블록을 실은 첫 호출이다 — history에 (user, assistant) 두 턴이 쌓인다.
    assert hop.hop == 0
    assert [t.speaker for t in hop.history] == ["user", "assistant"]
    # 단순 「불렸나」가 아니라 **그 Finding이 프롬프트에 닿았나**를 본다(auditor 테스트와 같은 형).
    prompt = ctx.models.last_for(Role.POV)
    assert finding.finding_id in prompt.body
    assert "{{" not in prompt.body  # 템플릿 토큰이 다 치환됐다


def test_pov_open_hop_reads_the_source_override(ctx: RunContext) -> None:
    """`source=` 콜러블을 주면 그 텍스트가 hop0 프롬프트에 실린다 — auditor와 같은 규약."""
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)
    marker = "MARKER_SOURCE_pov_override_2a7f"

    pov.open_hop(ctx, finding=finding, capsule=capsule, source=lambda: marker)

    assert marker in ctx.models.last_for(Role.POV).body


def _raise(exc: Exception, *, fence: int = 40):  # noqa: ANN202
    """`pov.open_hop`을 대신할 예외 발생기 — 실패 갈래 단위 테스트용.

    ⏵ 2026-10-11 (#550) 파싱 실패는 루프가 계속 도는 예외라 `hop`이 안 오르면 **끝없이 돈다**.
    `fence`번째 호출은 `exc` 대신 `RuntimeError`를 던져 그 회귀가 멈추지 않고 빨갛게 되게 한다 —
    `pov_tool_hops + 1`번만 부르는 정상 코드는 닿지 않는다(파싱 실패 자리는 `pov_tool_hops + 2`)."""
    calls = 0

    def _fn(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls >= fence:
            raise RuntimeError(f"fence: {fence}번째 호출 — 루프가 안 멈춘다")
        raise exc

    return _fn


def _pov_state(ctx: RunContext) -> tuple[runner._Accumulator, Finding]:
    """pov 라우팅 단위 테스트용 (finding + 그 캡슐을 담은 state)."""
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)
    state = runner._Accumulator()
    state.update(finding)
    state.finding_capsules[finding.finding_id] = capsule
    return state, finding


def test_run_pov_generates_and_records_the_poc_byte_size(ctx: RunContext) -> None:
    """러너 8단계 라우팅(추가형 — PoV 배선): capsule이 있으면 루프를 돌려 PoC를 만들고
    `pov_generated`에 **실제** 바이트 크기·sha256을 남긴다 — 기대값을 조립 결과에서 뽑는다
    (truthy·하드코딩 검사는 회귀를 못 잡는다). 결정론 fake라 hop0 gen의 spec을 그대로 조립하면
    루프가 만든 PoC와 같다."""
    state, finding = _pov_state(ctx)
    capsule = state.finding_capsules[finding.finding_id]
    hop0 = pov.open_hop(ctx, finding=finding, capsule=capsule)  # 결정론 fake → hop0는 gen
    assert hop0.action.action == "gen" and hop0.action.spec is not None
    expected = assemble_poc(hop0.action.spec)

    runner._run_pov(ctx, state, finding)

    gen = [e for e in ctx.trace.events if e.kind is EventKind.POV_GENERATED]
    assert gen, "capsule이 있는데 pov_generated가 안 났다 — 러너가 pov를 안 불렀다"
    assert gen[-1].data["poc_bytes"] == len(expected)
    assert gen[-1].data["poc_sha256"] == hashlib.sha256(expected).hexdigest()
    assert not any(e.kind is EventKind.POV_SKIPPED for e in ctx.trace.events)


def test_run_pov_records_a_skip_when_the_capsule_is_missing(ctx: RunContext) -> None:
    """capsule 미스면 pov는 **건너뛰고 기록만** 한다 — 예외를 내거나 finding을 죽이지 않는다.
    추가형이라 finding의 상태는 `validate.run`이 이미 정했고(#296이 막은 「단계가 finding을
    조용히 삼킨다」와 같은 결), 미스는 `pov_skipped`로 남긴다."""
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)
    state = runner._Accumulator()
    state.update(finding)  # finding_capsules에는 일부러 안 넣는다 — 미스.

    runner._run_pov(ctx, state, finding)  # 예외 없이 돌아와야 한다

    skipped = [e for e in ctx.trace.events if e.kind is EventKind.POV_SKIPPED]
    assert skipped and skipped[-1].data["reason"] == "capsule_missing"
    assert not any(e.kind is EventKind.POV_GENERATED for e in ctx.trace.events)


def test_run_pov_records_a_skip_on_a_schema_parse_error(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """모델이 매 홉 스키마를 못 맞추면 pov_skipped(parse_failed) — finding은 안 죽는다.

    파싱 실패는 홉 하나를 쓰고 루프가 계속 돈다(#550 (c)) — 홉을 여는 `pov.open_hop`을 항상 실패하게
    스텁하면 **`pov_tool_hops + 1`번** 불리고(절단 기록이 없는 실패라 마지막 홉에서 종단) 뒤에
    parse_failed로 남는다."""
    state, finding = _pov_state(ctx)
    calls: list[int] = []
    pov_hops = ctx.config.thresholds.pov_tool_hops

    def _always_bad(*_args: object, **kwargs: object) -> object:
        calls.append(int(kwargs.get("hop", 0)))  # type: ignore[call-overload]
        if len(calls) > pov_hops + 1:  # 울타리 — 홉이 안 올라가는 변이가 무한히 돌지 않게 한다
            raise RuntimeError("pov_tool_hops + 1번보다 더 열었다")
        raise SchemaParseError("bad pov json")

    monkeypatch.setattr(runner.pov, "open_hop", _always_bad)

    runner._run_pov(ctx, state, finding)

    skipped = [e for e in ctx.trace.events if e.kind is EventKind.POV_SKIPPED]
    assert skipped and skipped[-1].data["reason"] == "parse_failed"
    assert calls == list(range(pov_hops + 1))  # 실패 홉마다 다음 홉 번호로 다시 연다


def test_run_pov_records_a_skip_on_a_droppable_gateway_error(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """버릴 수 있는 게이트웨이(droppable 422)는 pov_skipped(gateway_error) — 이 finding의 사실."""
    state, finding = _pov_state(ctx)
    monkeypatch.setattr(
        runner.pov, "open_hop", _raise(ModelGatewayError("systematic", status=422))
    )

    runner._run_pov(ctx, state, finding)

    skipped = [e for e in ctx.trace.events if e.kind is EventKind.POV_SKIPPED]
    assert skipped and skipped[-1].data["reason"] == "gateway_error"


def test_run_pov_reraises_a_non_droppable_gateway_error(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """서버 쪽 사건(status 없음·5xx)은 run의 사실이라 올려 보낸다 — 조용히 삼키지 않는다
    (critic 루프와 같은 _is_droppable 판정). 루프가 흡수하지 않고 그대로 올린다."""
    state, finding = _pov_state(ctx)
    monkeypatch.setattr(
        runner.pov, "open_hop", _raise(ModelGatewayError("server down", status=None))
    )

    with pytest.raises(ModelGatewayError):
        runner._run_pov(ctx, state, finding)


# --- CyberGym 과제 설명 → 캡슐 섹션 (PoV 배선) ----------------------------------


def test_retrieval_injects_the_vuln_description_when_set(ctx: RunContext) -> None:
    """ctx.vuln_description가 있으면 캡슐에 vuln_description 섹션이 실린다(PoV 배선 — Scout
    설명기반 트리아지 입력). 기본(None)이면 섹션이 없다 — 지어내지 않는다."""
    plain = retrieval.run(ctx, target=VULN_TARGET, level=CapsuleLevel.LIGHT)
    assert not any(s.kind == retrieval.VULN_DESCRIPTION_KIND for s in plain.sections)

    ctx.vuln_description = "Heap overflow in parse_header when len exceeds 64."
    withdesc = retrieval.run(ctx, target=BENIGN_TARGET, level=CapsuleLevel.LIGHT)

    section = next(s for s in withdesc.sections if s.kind == retrieval.VULN_DESCRIPTION_KIND)
    assert section.body and "parse_header" in " ".join(section.body)
    assert section.items == len(section.body)
    assert withdesc.used_tokens <= withdesc.budget_tokens  # 예산 안에 든다


def test_vuln_description_section_fits_the_budget(ctx: RunContext) -> None:
    """설명 섹션은 예산 안에 드는 줄까지만 담고 tokens를 본문에서 계산해 채운다 — 경계에서
    **정확히** 몇 줄이 드는지 고정한다(고정 자리표시자가 아니라 본문에서 센 값이다 — 이 검사가
    도는 기본 카운터는 `len//4` 근사다)."""
    lines = [f"line {i} of the vulnerability description text here" for i in range(10)]
    text = "\n".join(lines)
    costs = [estimate_tokens(line) for line in lines]

    # 정확히 3줄 값을 예산으로 주면 3줄만 담는다(`used + cost > budget`라 등호는 통과 — 경계).
    exactly_three = retrieval._vuln_description_section(text, sum(costs[:3]))
    assert exactly_three is not None
    assert exactly_three.items == 3
    assert exactly_three.tokens == sum(costs[:3])
    assert exactly_three.tokens == sum(estimate_tokens(line) for line in exactly_three.body)

    # 첫 줄보다 작은 예산이면 한 줄도 못 담아 None(빈 자리표시자를 짓지 않는다).
    assert retrieval._vuln_description_section(text, costs[0] - 1) is None

    full = retrieval._vuln_description_section(text, 100_000)
    assert full is not None and full.items == len(lines)


# 설명 예산 선예약 · 잘림 이벤트 (PoV 배선). ⚠ 아래 검사들이 **못 무는 것**:
# ① `FakeRetriever`(dev_fake)만 돈다 — `HybridRetriever`가 `Query.budget_tokens`를 다르게 쓰는
#    결함은 안 본다. ② 기본 카운터(`len//4`)만 — 실 토크나이저 카운터에서 `CapsuleSection`
#    재검증이 갈리는 경우(`src/tokens.py`가 적은 위험)는 안 본다. ③ dev_fake는 단일 scout라 풀
#    마진(`_POOL_CAPSULE_MARGIN`)이 꺼져 있다 — 그 곱과 `budget // 2`의 상호작용은 안 본다.
# ④ 코드 자리는 토큰 **몫**까지다(설명이 쓴 만큼만 빠진 나머지) — 담긴 조각이 좋은지는 안 본다.
# ⑤ 캡슐 안에서 설명 섹션의 **자리(순서)**는 안 묻는다 — 계약이 아니다. ⑥ 홀수 예산에서 절반
#    상한의 내림·올림은 안 묻는다 — 설정 예산(`config/budgets.yaml`의 레벨별 값과 풀 마진 0.90배)이
#    전부 짝수라 둘이 같은 값을 낸다.


def _squeeze_capsule_budget(ctx: RunContext, tokens: int) -> None:
    """세 레벨의 캡슐 예산을 같은 값으로 줄인다 — 예산 경합을 만드는 픽스처용."""
    budgets = {"light": tokens, "expanded": tokens, "focused": tokens}
    thresholds = ctx.config.thresholds.transition(capsule_budget_tokens=budgets)
    ctx.config = ctx.config.transition(thresholds=thresholds)


def _truncations(ctx: RunContext) -> list:
    return [e for e in ctx.trace.events if e.kind is EventKind.VULN_DESCRIPTION_TRUNCATED]


def test_a_target_bigger_than_the_budget_cannot_starve_the_vuln_description(
    ctx: RunContext,
) -> None:
    """예산을 **꽉 채우는** 타깃에서도 설명 섹션이 실린다. Retriever가 예산을 다 쓰기 **전에**
    설명 자리를 미리 떼어야(`retrieval.run`의 `budget - reserve`) 이 검사가 선다 — 안 떼면
    Retriever가 예산을 채운 뒤 설명이 얹혀 `ContextCapsule`의 예산 검증기(`used > budget`)가
    죽고, 선예약 없이 남은 예산으로 뒤에 붙이던 예전 구현에서는 설명 섹션이 조용히 빠진다.

    떼는 양은 설명이 **쓴 만큼**이다 — 코드 자리(`target_function`)는 예산에서 설명 토큰만 뺀
    나머지를 **정확히** 받는다. 설명이 있다고 절반을 통째로 떼거나 몇 토큰 더 떼면(과예약) 코드
    자리를 굶긴다. 이 등식은 `FakeRetriever`가 LIGHT의 첫 섹션 `target_function`에
    min(원문, 질의 예산)을 준다는 것과 원문이 예산보다 크다는 전제 위에 선다.

    타깃 원문이 예산보다 큰 것을 **전제로 먼저 단언**한다 — 픽스처가 어긋나 예산이 안 차면 이
    검사가 아무것도 안 무는 채 초록이 된다(그때 「초록」은 경합이 없었다는 뜻일 수 있다).
    """
    budget = 60
    _squeeze_capsule_budget(ctx, budget)
    (_root_of(ctx) / "big.c").write_text("int global_counter;\n" * 200, encoding="utf-8")
    big = Location(file="big.c")
    assert estimate_tokens(read_symbol_source(_root_of(ctx), big)) > budget, "픽스처 전제 — 큰 타깃"
    ctx.vuln_description = "Heap overflow in parse_header when len exceeds 64."
    assert estimate_tokens(ctx.vuln_description) <= budget // 2, "픽스처 전제 — 설명이 절반 안"

    capsule = retrieval.run(ctx, target=big, level=CapsuleLevel.LIGHT)

    kinds = [section.kind for section in capsule.sections]
    assert retrieval.VULN_DESCRIPTION_KIND in kinds, "큰 타깃이 설명 자리를 먹었다"
    assert "target_function" in kinds, "설명이 코드 자리를 굶겼다"
    assert capsule.budget_tokens == budget, "예산을 원래대로 되돌리지 않았다"
    assert capsule.used_tokens <= capsule.budget_tokens
    assert not _truncations(ctx), "설명이 다 들어갔는데 잘림 이벤트가 났다"
    desc = next(s for s in capsule.sections if s.kind == retrieval.VULN_DESCRIPTION_KIND)
    code = next(s for s in capsule.sections if s.kind == "target_function")
    assert code.tokens == budget - desc.tokens, "설명이 쓴 만큼보다 더(또는 덜) 뗐다"


@pytest.mark.parametrize(
    "budget_of",
    [
        pytest.param(lambda cost: cost * 4, id="half_is_exactly_two_lines"),
        pytest.param(lambda cost: cost * 5, id="a_two_thirds_cap_would_keep_three"),
        pytest.param(lambda cost: cost * 6 - 2, id="a_half_plus_one_cap_would_keep_three"),
    ],
)
def test_a_description_over_half_the_budget_leaves_exactly_one_truncation_event(
    ctx: RunContext, budget_of
) -> None:
    """예산 절반(`budget // 2`)을 넘는 긴 설명은 그 절반까지만 실리고 `vuln_description_truncated`가
    **정확히 1건** 난다 — `kept`는 실린 줄 수(섹션의 `items`), `total`은 전체 줄 수다.

    「조용히 빠지는 것을 §0이 금한다」의 관측 지점이다. 잘림 분기를 지우면(0건) 이 검사가
    빨개지고, 반대로 항상 내면(설명이 다 들어간 run에도 낸다) 위 검사의 `not _truncations`가
    빨개진다. 숫자는 `estimate_tokens`에서 뽑는다 — 카운터 상수에 안 묶인다(줄 값이 같다는 전제는
    단언한다). 예산은 셋으로 돈다 — 줄 값의 4배(절반이 정확히 2줄: 아래쪽 경계) · 5배(2/3 상한이면
    3줄이 든다) · 6배−2(절반+1 상한이면 3줄이 든다). 한 값만으로는 절반보다 느슨한 상한이 안 보인다.
    """
    lines = [f"{i:02d}:" + "a" * 37 for i in range(10)]
    cost = estimate_tokens(lines[0])
    assert all(estimate_tokens(line) == cost for line in lines), "픽스처 전제 — 줄 값이 같다"
    budget = budget_of(cost)  # 셋 다 절반 안에는 정확히 2줄이 든다
    _squeeze_capsule_budget(ctx, budget)
    ctx.vuln_description = "\n".join(lines)

    capsule = retrieval.run(ctx, target=VULN_TARGET, level=CapsuleLevel.LIGHT)

    section = next(s for s in capsule.sections if s.kind == retrieval.VULN_DESCRIPTION_KIND)
    assert section.items == 2, "예산 절반이 아니라 다른 상한으로 잘렸다"
    truncated = _truncations(ctx)
    assert len(truncated) == 1, f"잘림 이벤트가 {len(truncated)}건이다(정확히 1건이어야 한다)"
    assert truncated[0].data == {"kept": section.items, "total": len(lines)}
    assert truncated[0].subject_kind == "capsule"
    assert truncated[0].subject_id == capsule.capsule_id
    assert truncated[0].stage == int(Stage.RETRIEVAL)


def test_a_description_that_fits_no_line_is_recorded_as_truncated_with_kept_zero(
    ctx: RunContext,
) -> None:
    """한 줄도 못 담으면(첫 줄이 절반보다 크다) 섹션은 없고 `kept=0` 잘림 이벤트가 1건 난다 —
    섹션이 없다는 사실만으로는 「설명이 없었다」와 「설명이 예산에 밀렸다」가 갈리지 않는다."""
    line = "b" * 200
    budget = estimate_tokens(line)  # 절반은 첫 줄 값에 못 미친다
    _squeeze_capsule_budget(ctx, budget)
    ctx.vuln_description = line

    capsule = retrieval.run(ctx, target=VULN_TARGET, level=CapsuleLevel.LIGHT)

    assert not any(s.kind == retrieval.VULN_DESCRIPTION_KIND for s in capsule.sections)
    truncated = _truncations(ctx)
    assert len(truncated) == 1
    assert truncated[0].data == {"kept": 0, "total": 1}


def test_blank_lines_are_neither_kept_nor_counted_as_truncated(ctx: RunContext) -> None:
    """빈 줄·공백만 있는 줄은 담지도 세지도 않는다 — 다 담긴 설명에 빈 줄 때문에 잘림 이벤트가
    나면 「잘렸다」는 기록이 거짓이 되고, 빈 줄을 담으면 매니페스트가 본문을 과장한다."""
    ctx.vuln_description = "first line\n\nsecond line\n   \nthird line\n"
    half = ctx.config.thresholds.budget_for(CapsuleLevel.LIGHT) // 2
    assert estimate_tokens(ctx.vuln_description) <= half, "픽스처 전제 — 설명이 다 든다"

    capsule = retrieval.run(ctx, target=VULN_TARGET, level=CapsuleLevel.LIGHT)

    section = next(s for s in capsule.sections if s.kind == retrieval.VULN_DESCRIPTION_KIND)
    assert list(section.body) == ["first line", "second line", "third line"]
    assert section.items == 3
    assert not _truncations(ctx), "빈 줄 때문에 다 담긴 설명에 잘림 이벤트가 났다"


def test_the_description_is_cut_at_the_first_line_that_does_not_fit() -> None:
    """설명은 **앞에서부터** 담다가 처음 안 드는 줄에서 끊는다(「드는 줄까지만」) — 안 드는 줄을
    건너뛰고 뒤의 짧은 줄을 채우면 가운데가 빠진 설명이 되고, `kept`(줄 수)만으로는 그 구멍이 안
    보인다."""
    first, long_line, last = "first line", "x" * 200, "third line"
    budget = estimate_tokens(first) + estimate_tokens(last)  # 짧은 둘은 들고 긴 줄은 안 든다
    assert estimate_tokens(long_line) > budget, "픽스처 전제 — 긴 줄은 안 든다"

    section = retrieval._vuln_description_section("\n".join([first, long_line, last]), budget)

    assert section is not None and list(section.body) == [first]


# --- 8단계 pov의 대상 (PoV 배선 · 추가형) ----------------------------------------------


def _demoted_at_validate(ctx: RunContext) -> tuple[runner._Accumulator, Finding]:
    """8단계에 `VALIDATED`로 들어와 `validate.run`이 `QUEUED`로 내릴 Finding 하나(캡슐 포함) —
    허용 도구로 안 매핑되는 기대 신호라 `tool_policy_violation`으로 내려간다."""
    _, capsule, _ = _triage(ctx, BENIGN_TARGET)
    demoted = _finding("find_0001", state=FindingState.VALIDATED, signal="로그에 WARN이 찍힌다")
    state = runner._Accumulator()
    state.update(demoted)
    state.finding_capsules[demoted.finding_id] = capsule
    return state, demoted


def test_pov_also_runs_for_a_finding_that_validate_demotes_to_queued(ctx: RunContext) -> None:
    """pov는 **8단계에 들어온 Finding**(진입 시 `VALIDATED`) 전부에 돈다 — `validate.run`이 그중
    하나를 `QUEUED`로 내려도(여기서는 허용 도구로 안 매핑되는 기대 신호 →
    `tool_policy_violation`) 그 Finding의 `pov_generated`가 난다. 대상 목록이 `validate.run`
    **전에** 잡히고(`runner._analyze`) pov는 상태를 안 바꾸는 추가 경로라(`runner._run_pov`
    docstring) 상태는 `QUEUED`로 남는다.

    ⚠ **현재 동작을 고정할 뿐 이 정책이 옳다고 주장하지 않는다.** `validate.run` **뒤** 상태로
    거르는 것은 사람이 정할 일이고(PoV 시도를 줄이는 선택 — 근거는 `runner._analyze`의
    `validated` 위 주석), 이 검사는 그 결정 없이 조용히 바뀌는 것을 막는다. 거르기로 정하면 이
    검사를 의식적으로 고친다. 이벤트의 대상(`subject_id`)을 보므로 러너가 그 사이를 어떻게
    엮는지에는 묶이지 않는다.
    """
    state, demoted = _demoted_at_validate(ctx)

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert state.findings[demoted.finding_id].state is FindingState.QUEUED, (
        "픽스처 전제 — validate.run이 이 Finding을 QUEUED로 내린다"
    )
    generated = [e for e in ctx.trace.events if e.kind is EventKind.POV_GENERATED]
    assert [e.subject_id for e in generated] == [demoted.finding_id]


def test_pov_is_handed_the_finding_as_validate_run_left_it(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pov에 넘어가는 Finding은 `validate.run`이 전이시킨 **최신** 것이다 — 진입 시점 사본이
    아니다(`runner._analyze`의 「validate.run이 전이시킨 최신 finding으로 돈다」). 위 검사가
    **어느** Finding에 pov가 도는지를 보고, 이 검사는 **어떤 상태의 사본**이 넘어가는지를 본다.

    ⚠ 러너 안의 이음매 `runner._run_pov`에 스파이를 건다 — 그 이름·인자가 바뀌면 이 검사도
    고친다(위 검사와 달리 러너 배선에 묶인다).
    """
    state, demoted = _demoted_at_validate(ctx)
    handed: list[FindingState] = []
    real_run_pov = runner._run_pov

    def spy(ctx_: RunContext, state_: runner._Accumulator, finding: Finding) -> None:
        handed.append(finding.state)
        real_run_pov(ctx_, state_, finding)

    monkeypatch.setattr(runner, "_run_pov", spy)

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert handed == [FindingState.QUEUED], f"pov가 validate.run 전 사본을 받았다: {handed}"


# --- 8단계 pov의 대상 ② — `validated` 밖의 상위 N (⏵ 2026-10-04, 이슈 #386 ①의 후속) -----------
#
# 2026-10-03 첫 실모델·실도구 run은 완주했는데 PoV가 **0건**이었다 — Level 1이 재현 명세의 근거를
# 주지 않아 F-9가 Finding을 `hypothesized`에 세우고 8단계 진입 시점의 `validated`가 비어 있었다.
# PoC가 없으면 채점 점수가 없다. `thresholds.pov_attempt_top_n`이 그 밖에서도 상위 몇 건을 시도하게
# 한다 — **0이 종전 동작이고** 미실측 표시가 아니다(`config/budgets.yaml`).


def _outside_validated(
    ctx: RunContext, specs: list[tuple[str, float]]
) -> tuple[runner._Accumulator, list[Finding]]:
    """`VALIDATED`로 들어오지 **않은** Finding들과 그 캡슐 — `pov_attempt_top_n`의 **후보 풀**이다
    (그 풀에서 Critic이 밀어 낸 것을 거른 것이 대상이다 — `runner._pov_targets`). 여기 담는 것은
    판정이 없는 `HYPOTHESIZED`뿐이라 거르개에 안 걸린다.

    `_finding`의 기본 상태가 `HYPOTHESIZED`다(재현 명세가 없어 F-9가 세우는 자리와 같은 상태).
    """
    _, capsule, _ = _triage(ctx, BENIGN_TARGET)
    state = runner._Accumulator()
    findings = []
    for finding_id, confidence in specs:
        finding = _finding(finding_id, confidence=confidence)
        state.update(finding)
        state.finding_capsules[finding.finding_id] = capsule
        findings.append(finding)
    return state, findings


def _with_top_n(ctx: RunContext, limit: int) -> None:
    ctx.config = ctx.config.transition(
        thresholds=ctx.config.thresholds.transition(pov_attempt_top_n=limit)
    )


def _pov_subjects(ctx: RunContext) -> list[str | None]:
    return [e.subject_id for e in ctx.trace.events if e.kind is EventKind.POV_GENERATED]


def test_pov_does_not_run_outside_validated_while_the_threshold_is_zero(ctx: RunContext) -> None:
    """`pov_attempt_top_n: 0`이면 `validated` 밖의 Finding은 PoV를 **안** 돈다.

    ⏵ 2026-10-05 — `config/budgets.yaml`이 8을 선언해 **기본값이 올라갔다.** 전 판은 여기서
    `thresholds.pov_attempt_top_n == 0`을 단언해 기본값 자체를 묶었는데, 그 단언은 「0이면 안
    돈다」를 무는 것이 아니라 「기본이 0이다」를 물었다. 0을 **주입**해 명제를 원래 자리로 되돌린다.
    짝 검사 `test_pov_runs_outside_validated_when_the_threshold_is_set`이 양수 쪽을 문다.
    """
    state, _ = _outside_validated(ctx, [("find_0001", 0.9)])
    _with_top_n(ctx, 0)
    assert ctx.config.thresholds.pov_attempt_top_n == 0, "주입이 안 먹었다"

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert _pov_subjects(ctx) == []


def test_pov_runs_outside_validated_when_the_threshold_is_set(ctx: RunContext) -> None:
    """🔴 임계가 양수면 `validated`가 **0건이어도** PoV가 돈다 — 실측 run이 막힌 자리다.

    `validated`가 비어 있을 때 아예 루프에 안 들어가던 `if validated and ...` 게이트도 같이
    바뀌었다. 그 게이트를 되돌리면 이 검사가 빨개진다.
    """
    state, (finding,) = _outside_validated(ctx, [("find_0001", 0.9)])
    _with_top_n(ctx, 1)

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert _pov_subjects(ctx) == [finding.finding_id]
    # ⏵ 2026-10-13 (ADR 0015) - 이 Finding은 7단계에서 Fake Critic의 **근거 없는 반박**을 받는다.
    # 전엔 그것이
    # `uphold`로 정규화돼 `HYPOTHESIZED`로 남았고, 지금은 `need_evidence`(F-7)라 `QUEUED`다. 이
    # 테스트가 묻는
    # 명제는 그대로다 - PoV(추가형)가 상태를 더 올리지 않는다(`validated`·`proven`이 아니다).
    assert state.findings[finding.finding_id].state is FindingState.QUEUED, (
        "pov는 추가형이다 — 상태를 바꾸지 않는다"
    )


def test_the_extra_pov_targets_are_cut_by_confidence_then_finding_id(ctx: RunContext) -> None:
    """판정이 없는 시나리오다 — 그러면 정렬 키의 첫 성분(Critic 판정 계단)이 전부 같아(미경유 1)
    자르는 순서는 `critic.select`와 **같은 키**다 — `confidence` 내림차순, 동점은 `finding_id`.

    `confidence` 0.9 둘 + 0.3 하나를 넣고 상한을 1로 둔다. 넣는 순서를 **0.3 → 큰 id → 작은
    id**로 해서 ①자리로 고르는 구현(첫째 = 0.3)과 ②동점을 뒤 id로 가르는 구현이 각각 빨개지게
    한다. 정렬이 흔들리면 재현 실행이 갈라진다.
    """
    state, _ = _outside_validated(
        ctx, [("find_0003", 0.3), ("find_0002", 0.9), ("find_0001", 0.9)]
    )
    _with_top_n(ctx, 1)

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert _pov_subjects(ctx) == ["find_0001"]


def test_the_threshold_adds_targets_and_never_removes_a_validated_one(
    ctx: RunContext,
) -> None:
    """**더하기만 한다.** 상한이 1인데 `validated`가 1건 + 밖이 2건이면 시도는 **2건**이다 —
    `validated`는 상한에 들지 않는다. 상한을 전체 대상 수로 읽는 구현은 1건만 시도해 빨개진다.

    그래서 이 상한이 잘라 낸 Finding은 **종전에도 PoV를 안 돌던 것**이고, **상한 자체가** 무언가를
    조용히 잃게 하지는 않는다(`runner._pov_targets` docstring — 추가 대상이 예산을 더 쓰는 것은
    별개이고 `tests/test_oracle_wiring.py`가 문다).
    """
    state, demoted = _demoted_at_validate(ctx)
    outside, _ = _outside_validated(ctx, [("find_0002", 0.9), ("find_0003", 0.8)])
    for finding in outside.findings.values():
        state.update(finding)
    state.finding_capsules.update(outside.finding_capsules)
    _with_top_n(ctx, 1)

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert _pov_subjects(ctx) == [demoted.finding_id, "find_0002"]


def test_the_threshold_does_not_widen_the_validate_run_gate(ctx: RunContext) -> None:
    """🔴 **E4 게이트는 안 넓어진다.** `validate.run`은 유일한 결정론 E4 게이트이고 `from=validated`
    를 요구한다 — 거기에 다른 상태를 넣으면 상태 기계(F-10~F-12)가 깨진다. 넓힌 것은 PoV 쪽뿐이다.

    `validated` 1건 + 밖 2건에 상한 2를 두고, 8단계 `gate_eval`의 대상이 **`validated` 하나**인지
    본다. `_pov_targets`를 `validate.run` 루프에도 꽂는 변이가 여기서 빨개진다.
    """
    state, demoted = _demoted_at_validate(ctx)
    outside, _ = _outside_validated(ctx, [("find_0002", 0.9), ("find_0003", 0.8)])
    for finding in outside.findings.values():
        state.update(finding)
    state.finding_capsules.update(outside.finding_capsules)
    _with_top_n(ctx, 2)

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    gated = [
        e.subject_id
        for e in ctx.trace.events
        if e.kind is EventKind.GATE_EVAL and e.stage == int(Stage.VALIDATE)
    ]
    assert gated == [demoted.finding_id]
    assert len(_pov_subjects(ctx)) == 3, "PoV 쪽은 넓어졌다(전제)"


# --- 추가 대상 풀에서 Critic이 밀어 낸 Finding을 뺀다 (적대적 리뷰 #490 MEDIUM-2) ---------------
#
# 풀에 두면 **하네스 Critic이 「아니다」라고 한 Finding의 PoC가 공식 최종 제출**이 될 수 있다 -
# 다른 후보가 없으면 그것이 최종이다. 정렬 키(판정 계단 → `confidence` → id)는 반박을 맨 뒤(3)에
# 두지만 계단은 순서일 뿐 거르지 못하므로, 아래 검사들은 **상한에 자리가 남는 모양**으로 필터
# 자체를 문다(⏵ 2026-10-12 - 전엔 상한 1에서 `confidence` 순서가 필터를 대신 시험했다).
# 풀은 「validated 밖에서 Critic이 밀어 내지 않은 것」이다 - Critic이
# 반박으로 판정했거나 판정 호출이 실패해 버려진 Finding은 뺀다(⏵ 2026-10-11 - 보류
# (`need_evidence`)는 든다, 이슈 #604). Critic을
# **안 거친** Finding(`critic_top_n`에 잘림 · 6단계 게이트가 E0로 QUEUED한 것)과 재현 명세가
# 없어 멈춘 것(F-9)은 든다.


def _critic_decision(finding_id: str, verdict: Verdict, *, grounded: bool) -> Decision:
    return Decision(
        decision_id="dec_0001",
        run_id=RUN_ID,
        finding_id=finding_id,
        verdict=verdict,
        rationale="근거 서술",
        evidence_refs=["ev_0001"] if grounded else [],
        produced_by=ProducedBy(role=Role.CRITIC, model="fake", prompt_version="v1"),
    )


@pytest.mark.parametrize(
    ("finding_state", "verdict", "grounded", "dropped", "in_pool"),
    [
        pytest.param(
            FindingState.QUEUED, Verdict.REBUT, True, False, False, id="critic_rebutted_F6"
        ),
        pytest.param(
            FindingState.QUEUED, Verdict.NEED_EVIDENCE, False, False, True,
            id="critic_asked_for_evidence_F7",
        ),
        pytest.param(
            FindingState.QUEUED, Verdict.NEED_EVIDENCE, True, False, True,
            id="critic_asked_for_evidence_with_citations_F7",
        ),
        pytest.param(
            FindingState.HYPOTHESIZED, None, False, True, False,
            id="critic_call_failed_and_the_finding_was_dropped",
        ),
        pytest.param(
            FindingState.HYPOTHESIZED, Verdict.UPHOLD, True, False, True,
            id="upheld_but_held_for_a_missing_repro_spec_F9",
        ),
        pytest.param(
            FindingState.QUEUED, Verdict.REBUT, False, False, True,
            id="an_ungrounded_rebuttal_is_normalized_to_need_evidence",
        ),
        pytest.param(
            FindingState.HYPOTHESIZED, None, False, False, True,
            id="cut_by_critic_top_n_the_critic_never_judged_it",
        ),
        pytest.param(
            FindingState.QUEUED, None, False, False, True,
            id="gate_queued_it_as_E0_before_the_critic_F2",
        ),
    ],
)
def test_the_extra_pov_pool_excludes_what_the_critic_set_aside_or_dropped(
    ctx: RunContext,
    finding_state: FindingState,
    verdict: Verdict | None,
    grounded: bool,
    dropped: bool,
    in_pool: bool,
) -> None:
    """풀 판정표 - 상태가 아니라 **Critic이 이 Finding에 무엇을 했나**로 가른다. `QUEUED`는
    F-2(게이트 E0 - Critic을 안 거쳤다)와 F-6(밀어 냄)·F-7(보류 - 든다)이 섞인 상태라 상태만으로는
    못 가른다. 근거 없는 `rebut`은 `gate.normalize`가 `need_evidence`로 바꾸므로 보류(F-7)와 같은
    길로
    풀에 든다(⏵ 2026-10-13 ADR 0015 - 전엔 `uphold`로 바뀌어 `HYPOTHESIZED`로 들었다) - 정규화가
    한 곳
    (`gate.normalize`)에서만 일어난다는 전제를 같이 문다.

    ⚠ 판정표 여덟은 **정책·상태 기계에서 이 상태가 나오는 길**(F-2 · F-6 · F-7 · F-9 · 상한
    절단 · `_drop_finding`)에서 골랐다 - 지어낸 상태가 아니다."""
    finding = _finding("find_0001", state=finding_state, confidence=0.9)
    state = runner._Accumulator()
    state.update(finding)
    if verdict is not None:
        state.decisions[finding.finding_id] = (
            _critic_decision(finding.finding_id, verdict, grounded=grounded),
        )
    if dropped:
        runner._drop_finding(ctx, state, finding, "schema_parse_failed", SchemaParseError("bad"))
    _with_top_n(ctx, 1)

    targets = runner._pov_targets(ctx, state, [])

    assert [f.finding_id for f in targets] == (["find_0001"] if in_pool else [])


def test_a_rebutted_finding_cannot_take_the_slot_of_a_finding_held_for_a_missing_repro_spec(
    ctx: RunContext,
) -> None:
    """리뷰의 시나리오다 - 반박된 Finding은 `confidence`가 더 높아도 풀에 안 든다. ⏵ 2026-10-12 -
    상한을 **2**로 둔다: 자리가 남아도 반박은 안 든다(판정 계단은 반박을 뒤로 보낼 뿐 풀에서 빼지
    않는다 - 상한 1이면 계단만으로도 F-9 Finding이 이겨 필터를 시험하지 못한다)."""
    rebutted = _finding("find_0001", state=FindingState.QUEUED, confidence=0.99)
    held = _finding("find_0002", state=FindingState.HYPOTHESIZED, confidence=0.5)
    state = runner._Accumulator()
    state.update(rebutted)
    state.update(held)
    state.decisions[rebutted.finding_id] = (
        _critic_decision(rebutted.finding_id, Verdict.REBUT, grounded=True),
    )
    state.decisions[held.finding_id] = (
        _critic_decision(held.finding_id, Verdict.UPHOLD, grounded=True),
    )
    _with_top_n(ctx, 2)

    assert [f.finding_id for f in runner._pov_targets(ctx, state, [])] == ["find_0002"]


def test_a_held_finding_cannot_take_the_slot_of_an_upheld_one_but_takes_a_rebutted_ones(
    ctx: RunContext,
) -> None:
    """보류(`need_evidence`)는 풀에 들되 정렬 키 앞의 판정 계단에서 uphold·미경유 **뒤**다 -
    상한 1에 `confidence`가 더 높은 보류가 F-9 uphold의 자리를 못 가져간다. 반박된 Finding은
    풀 밖이라 그 자리는 보류가 받는다."""
    held = _finding("find_0001", state=FindingState.QUEUED, confidence=0.99)
    upheld = _finding("find_0002", state=FindingState.HYPOTHESIZED, confidence=0.5)
    state = runner._Accumulator()
    state.update(held)
    state.update(upheld)
    state.decisions[held.finding_id] = (
        _critic_decision(held.finding_id, Verdict.NEED_EVIDENCE, grounded=False),
    )
    state.decisions[upheld.finding_id] = (
        _critic_decision(upheld.finding_id, Verdict.UPHOLD, grounded=True),
    )
    _with_top_n(ctx, 1)

    assert [f.finding_id for f in runner._pov_targets(ctx, state, [])] == ["find_0002"]

    rebutted = _finding("find_0003", state=FindingState.QUEUED, confidence=0.5)
    state2 = runner._Accumulator()
    state2.update(held)
    state2.update(rebutted)
    state2.decisions[held.finding_id] = state.decisions[held.finding_id]
    state2.decisions[rebutted.finding_id] = (
        _critic_decision(rebutted.finding_id, Verdict.REBUT, grounded=True),
    )

    assert [f.finding_id for f in runner._pov_targets(ctx, state2, [])] == ["find_0001"]


def _ladder_state() -> runner._Accumulator:
    """판정 계단 넷이 한 state에 다 있다 - uphold · 미경유(`decisions` 없음) · 보류 · 반박."""
    state = runner._Accumulator()
    for finding_id, confidence, verdict in (
        ("find_0001", 0.1, Verdict.UPHOLD),
        ("find_0002", 0.3, None),
        ("find_0003", 0.85, Verdict.NEED_EVIDENCE),
        ("find_0004", 0.99, Verdict.REBUT),
    ):
        state.update(_finding(finding_id, state=FindingState.HYPOTHESIZED, confidence=confidence))
        if verdict is not None:
            state.decisions[finding_id] = (_critic_decision(finding_id, verdict, grounded=True),)
    return state


def test_the_verdict_ranks_are_uphold_then_uncritiqued_then_held_then_rebutted() -> None:
    """판정 계단은 이슈 #604 (b)의 순서 그대로 **넷**이다 - `uphold 0 < 미경유 1 < 보류 2 < 반박 3`.
    반박(3)은 `_critic_set_aside`가 풀에서 빼므로 **지금 정렬에는 닿지 않는 값**이다 - 이 검사가
    값 자체를 문다(반박을 보류와 같은 2나 uphold와 같은 0에 두는 변이)."""
    assert runner._verdict_ranks_by_finding(_ladder_state()) == {
        "find_0001": 0,
        "find_0002": 1,
        "find_0003": 2,
        "find_0004": 3,
    }


def test_an_upheld_decision_wins_the_rank_even_beside_a_held_one() -> None:
    """판정이 둘이면(지금은 닿지 않는다 - `state.decisions[...] = (decision,)` 한 자리 대입)
    uphold를 먼저 본다 - 게이트 `gate.evaluate`의 `upheld`가 `any(normalize == UPHOLD)`라 uphold가
    하나라도 있으면 게이트는 uphold로 읽기 때문이다."""
    state = runner._Accumulator()
    state.update(_finding("find_0001", state=FindingState.HYPOTHESIZED))
    state.decisions["find_0001"] = (
        _critic_decision("find_0001", Verdict.NEED_EVIDENCE, grounded=True),
        _critic_decision("find_0001", Verdict.UPHOLD, grounded=True),
    )

    assert runner._verdict_ranks_by_finding(state) == {"find_0001": 0}


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        pytest.param(1, ["find_0001"], id="limit_1_takes_the_upheld_one"),
        pytest.param(2, ["find_0001", "find_0002"], id="limit_2_adds_the_uncritiqued_one"),
        pytest.param(3, ["find_0001", "find_0002", "find_0003"], id="limit_3_ends_on_the_held_one"),
    ],
)
def test_the_extra_pov_pool_orders_upheld_then_uncritiqued_then_held(
    ctx: RunContext, limit: int, expected: list[str]
) -> None:
    """상한이 차는 쪽에서 자르는 순서다 - `confidence`는 거꾸로(0.1 · 0.3 · 0.85)여도 판정 계단이
    앞선다. 반박(`find_0004` · 0.99)은 풀 밖이다. 미경유를 보류와 같은 계단에 두거나(`not
    any(UPHOLD)`) uphold와 같은 계단에 두는 변이는 상한 1·2에서 빨개진다."""
    _with_top_n(ctx, limit)

    targets = runner._pov_targets(ctx, _ladder_state(), [])

    assert [f.finding_id for f in targets] == expected


def test_analyze_runs_pov_on_the_held_finding_and_not_on_the_rebutted_one(ctx: RunContext) -> None:
    """위 둘이 보는 `_pov_targets`가 **`_analyze`에 실제로 꽂혀 있다** - 상한 2에 자리가 남아도
    (반박된 Finding이 더 높은 `confidence`로 있다) PoV는 재현 명세가 없어 멈춘 쪽에만 돈다."""
    state, (held,) = _outside_validated(ctx, [("find_0001", 0.5)])
    rebutted = _finding("find_0002", state=FindingState.QUEUED, confidence=0.99)
    state.update(rebutted)
    state.finding_capsules[rebutted.finding_id] = state.finding_capsules[held.finding_id]
    state.decisions[rebutted.finding_id] = (
        _critic_decision(rebutted.finding_id, Verdict.REBUT, grounded=True),
    )
    _with_top_n(ctx, 2)

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert _pov_subjects(ctx) == [held.finding_id]


def test_a_stage_7_rebuttal_keeps_the_finding_out_of_the_extra_pov_pool(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**실제 7단계가 쓴 `state.decisions`가 풀 필터에 읽힌다** - 위 검사들은 `decisions`를 손으로
    꽂는다. 여기서는 `critic.run`의 모델 호출(`_ask_model`)만 갈아 끼워 한 Finding에 근거 있는
    `rebut`를 내게 하고, 정책이 그것을 `QUEUED`(F-6)로 내린 뒤 PoV가 안 도는지 본다.
    `confidence`는 반박된 쪽이 더 높고 상한 2라 자리가 남는다 - 필터가 안 이어졌으면 반박된 쪽도
    돈다. `_critic_set_aside`가 7단계 루프와 끊기는 변이(결정을 안 읽는다)가 여기서 빨개진다."""
    state, (kept, rebutted) = _outside_validated(ctx, [("find_0001", 0.5), ("find_0002", 0.99)])

    def ask(
        ctx_: RunContext,
        *,
        decision_id: str,
        finding: Finding,
        evidences: list,
        source=None,
        hop_observations=(),
    ) -> Decision:
        verdict = Verdict.REBUT if finding.finding_id == rebutted.finding_id else Verdict.UPHOLD
        return Decision(
            decision_id=decision_id,
            run_id=RUN_ID,
            finding_id=finding.finding_id,
            verdict=verdict,
            rationale="근거 서술",
            evidence_refs=["ev_0001"],
            produced_by=ProducedBy(role=Role.CRITIC, model="fake", prompt_version="v1"),
        )

    monkeypatch.setattr(critic, "_ask_model", ask)
    _with_top_n(ctx, 2)

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert state.findings[rebutted.finding_id].state is FindingState.QUEUED, (
        "전제 - 실제 7단계가 이 Finding을 F-6으로 내려야 한다"
    )
    assert _pov_subjects(ctx) == [kept.finding_id]



# --- 8단계 pov의 빈 풀이 run 경고로 오른다 (이슈 #357 잔여) ------------------------------


def _empty_pool_message(ctx: RunContext, specs: list[tuple[str, Verdict | None]]) -> str | None:
    """`specs`의 Finding들(전부 `validated` 밖)을 `_analyze`에 먹이고 `pov_pool_empty`를 돌려준다.
    판정 `None`은 Critic을 안 거친 것, 문자열 `"dropped"`는 판정 호출이 실패해 버려진 것이다."""
    state = runner._Accumulator()
    for finding_id, verdict in specs:
        finding = _finding(finding_id, state=FindingState.QUEUED, confidence=0.9)
        state.update(finding)
        if isinstance(verdict, Verdict):
            state.decisions[finding_id] = (_critic_decision(finding_id, verdict, grounded=True),)
        elif verdict == "dropped":
            runner._drop_finding(ctx, state, finding, "schema_parse_failed", SchemaParseError("x"))
    runner._analyze(ctx, [], state, int(Stage.VALIDATE))
    return state.pov_pool_empty


def test_an_empty_pov_pool_with_a_pov_seat_lands_in_the_run_warnings_with_its_breakdown(
    ctx: RunContext,
) -> None:
    """🔴 좌석이 있는데 PoV 대상이 0건이면 `state.pov_pool_empty`가 사유째 말한다(#357 잔여) —
    다섯 전부 근거 있는 반박이면 풀이 비고 문장이 그 다섯을 센다."""
    _with_top_n(ctx, 8)
    assert Role.POV.value in ctx.config.profile.roles, "전제 — pov 좌석"

    message = _empty_pool_message(ctx, [(f"find_000{i}", Verdict.REBUT) for i in range(1, 6)])

    assert message is not None
    assert message.startswith(
        "PoV 대상 0건 — validated 0 · Critic 반박 5 · 보류(파서 폴백·정규화된 반박 포함) 0"
        " · 판정 실패 0"
        " · Critic 미경유 0 · pov_attempt_top_n 8."
    ), message
    assert "8단계 PoV는 한 건도 시도되지 않았다" in message


def test_the_empty_pool_sentence_counts_each_way_a_finding_leaves_the_pool(ctx: RunContext) -> None:
    """넷을 **서로 다른 수**로 넣어 하나라도 안 세거나 다른 칸에 세면 문장이 어긋난다. 상한이 0이면
    `validated` 밖은 후보가 아니라 Critic 미경유도 풀에 못 든다 — 문장이 상한 0을 든다."""
    _with_top_n(ctx, 0)
    specs: list[tuple[str, Verdict | str | None]] = [
        *[(f"find_01{i}", Verdict.REBUT) for i in range(3)],
        *[(f"find_02{i}", Verdict.NEED_EVIDENCE) for i in range(2)],
        ("find_0300", "dropped"),
        *[(f"find_04{i}", None) for i in range(4)],
    ]

    message = _empty_pool_message(ctx, specs)

    assert message is not None
    assert message.startswith(
        "PoV 대상 0건 — validated 0 · Critic 반박 3 · 보류(파서 폴백·정규화된 반박 포함) 2"
        " · 판정 실패 1"
        " · Critic 미경유 4 · pov_attempt_top_n 0."
    ), message
    assert "상한 0" in message


def test_an_empty_pov_pool_stays_silent_without_a_pov_seat(ctx: RunContext) -> None:
    """좌석이 없는 프로필은 `_pov_targets`가 0건이어도 PoV가 원래 안 돈다 — 문장이 없다."""
    _with_top_n(ctx, 8)
    seatless = {k: v for k, v in ctx.config.profile.roles.items() if k != Role.POV.value}
    ctx.config = ctx.config.transition(profile=ctx.config.profile.transition(roles=seatless))
    assert Role.POV.value not in ctx.config.profile.roles, "주입이 안 먹었다"
    assert _empty_pool_message(ctx, [("find_0001", Verdict.REBUT)]) is None


def test_the_empty_pool_sentence_reads_the_normalized_verdict_and_the_priority_order(
    ctx: RunContext,
) -> None:
    """칸 우선순위와 정규화를 한 번에 문다 — 상한 0 · validated 0, 여섯 Finding이 각자 한 칸:
    근거 없는 반박(→ `normalize`가 `need_evidence` → 보류) · 근거 있는 업홀드(미승격) · **판정 실패
    목록에도 반박이 있는 것**(→ 판정 실패가 먼저) · 근거 있는 반박 · 보류 · 판정 없음.

    ⏵ 2026-10-13 (ADR 0015) - 근거 없는 반박의 칸이 「업홀드 미승격」에서 「보류」로 옮겼다.
    전에는 그 반박이
    `uphold`로 읽혀 **업홀드 칸에 두 건**(미승격 2)이 쌓였다."""
    _with_top_n(ctx, 0)
    state = runner._Accumulator()
    plan = {
        "find_0001": (Verdict.REBUT, False),
        "find_0002": (Verdict.UPHOLD, True),
        "find_0003": (Verdict.REBUT, True),
        "find_0004": (Verdict.REBUT, True),
        "find_0005": (Verdict.NEED_EVIDENCE, True),
        "find_0006": (None, True),
    }
    for finding_id, (verdict, grounded) in plan.items():
        state.update(_finding(finding_id, state=FindingState.QUEUED, confidence=0.9))
        if verdict is not None:
            state.decisions[finding_id] = (
                _critic_decision(finding_id, verdict, grounded=grounded),
            )
    state.critic_failed_ids.add("find_0003")

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert state.pov_pool_empty is not None
    assert state.pov_pool_empty.startswith(
        "PoV 대상 0건 — validated 0 · Critic 반박 1 · 보류(파서 폴백·정규화된 반박 포함) 2"
        " · 판정 실패 1"
        " · Critic 미경유 1 · 업홀드 미승격 1 · pov_attempt_top_n 0."
    ), state.pov_pool_empty


def test_an_empty_pov_pool_with_no_findings_at_all_still_warns(ctx: RunContext) -> None:
    """Finding이 0건인 run도 경고가 나간다 - 좌석이 있는데 대상이 0건이면 사유가 무엇이든 침묵이
    문제라는 것이 #357 명제이고, Finding 0건은 Scout·Auditor가 아무것도 못 낸 경우라 문장의
    「Critic이 모두 밀어 냈거나 없다」의 「없다」로 읽힌다. 호출 조건에 `and state.findings`를
    붙이는 변이를 이 핀이 문다."""
    _with_top_n(ctx, 8)

    message = _empty_pool_message(ctx, [])

    assert message is not None
    assert message.startswith(
        "PoV 대상 0건 — validated 0 · Critic 반박 0 · 보류(파서 폴백·정규화된 반박 포함) 0"
        " · 판정 실패 0"
        " · Critic 미경유 0 · pov_attempt_top_n 8."
    ), message


def test_an_empty_pov_pool_stays_silent_when_a_target_exists(ctx: RunContext) -> None:
    state, _ = _outside_validated(ctx, [("find_0001", 0.9)])
    _with_top_n(ctx, 1)

    runner._analyze(ctx, [], state, int(Stage.VALIDATE))

    assert _pov_subjects(ctx) == ["find_0001"], "전제 — 대상이 있어 pov가 돌았다"
    assert state.pov_pool_empty is None


# --- 발급값 도장 (D83 · 이슈 #279) ---------------------------------------------


class _RewritingModelClient:
    """픽스처 모델(Fake)의 출력을 `rewrite`로 고쳐 돌려준다 — 사실 블록을 안 따른 모델.

    고친 값은 테스트가 `transition()`으로 만든다 — 스키마 검증을 통과하는 값이어야 실제 파서가
    내보낼 수 있는 출력이다(D83: 파싱은 통과하고 생성·저장에서 터지거나 조용히 덮인다).
    """

    def __init__(self, inner: object, rewrite) -> None:  # noqa: ANN001
        self._inner = inner
        self._rewrite = rewrite
        self.written: list[object] = []

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        result = self._inner.complete(role=role, prompt=prompt, schema=schema, history=history)
        self.written.append(result.value)
        return result.model_copy(update={"value": self._rewrite(result.value)})


def _overrides(ctx: RunContext) -> list:  # noqa: ANN202
    return [e for e in ctx.trace.events if e.kind == "issued_override"]


def test_scout_stamps_the_issued_ids_over_the_ones_the_model_wrote(ctx: RunContext) -> None:
    """D83 ② — Scout가 **남의** `candidate_id`·다른 `run_id`·`artifact_id`를 써도 둘 다 남는다.

    저장소 `put()`이 `(kind, obj_id)` upsert라, 모델 값을 그대로 쓰면 예외 없이 첫 후보의 행을
    덮었다. 도장 뒤에는 둘째 후보가 발급값으로 저장되고, `transition`의 `subject_id`도 발급값이며,
    모델이 쓴 값은 덮기 전에 `issued_override`로 남는다.
    """
    first, _, _ = _triage(ctx, VULN_TARGET)
    foreign_run = "run_19990101_0001"
    stub = _RewritingModelClient(
        ctx.models,
        lambda value: value.transition(
            candidate_id=first.candidate_id, run_id=foreign_run, artifact_id=first.artifact_id
        ),
    )

    second, _, _ = _triage(dataclasses.replace(ctx, models=stub), BENIGN_TARGET)

    assert second.candidate_id != first.candidate_id, "모델이 쓴 남의 candidate_id가 살아남았다"
    assert ctx.store.get("candidate", first.candidate_id) == first, "첫 후보의 행이 덮였다"
    stored = ctx.store.get("candidate", second.candidate_id)
    assert stored is not None and stored.run_id == RUN_ID
    assert ctx.store.get("artifact", stored.artifact_id).path == BENIGN_TARGET.file
    created = [
        e.subject_id
        for e in ctx.trace.events
        if e.kind is EventKind.TRANSITION and e.data["rule"] == "C-1"
    ]
    assert created == [first.candidate_id, second.candidate_id]
    (override,) = _overrides(ctx)
    assert override.subject_id == second.candidate_id
    assert override.data["fields"] == "candidate_id,run_id,artifact_id"
    assert override.data["written.candidate_id"] == first.candidate_id
    assert override.data["written.run_id"] == foreign_run


def test_scout_v3_follow_up_hop_is_stamped_too(ctx: RunContext) -> None:
    """D83 — v3 되묻기 홉(hop k)의 `ScoutResponse.candidate`도 발급값으로 덮는다.

    hop0(`open_session`)만 막으면 되묻기 루프의 마지막 판정이 `finish()`로 그대로 간다 —
    `state: triaged`면 C-1 조회가 `IllegalTransition`을 내고, 남의 ID면 그 행을 덮는다.
    """
    session = _ne_session()

    class _Stub:
        def complete(self, *, role: Role, prompt, schema, history=()):  # noqa: ANN001, ANN202
            written = session.candidate.transition(
                candidate_id="cand_9999",
                run_id="run_19990101_0001",
                artifact_id="art_9999",
                state=FindingState.TRIAGED,
                reason_codes=["triage_review"],
            )
            value = ScoutResponse(candidate=written, analysis_summary=AnalysisSummary(claim="h1"))
            return ModelResult(
                value=value,
                usage=Usage(input_tokens=110, output_tokens=15, cached_tokens=90),
                model="stub",
                raw_text="HOP1 RAW",
            )

    next_session = scout.continue_session(
        dataclasses.replace(ctx, models=_Stub()), session, "FOLLOWUP"
    )

    stamped = next_session.candidate
    assert (stamped.candidate_id, stamped.run_id, stamped.artifact_id, stamped.state) == (
        session.candidate_id,
        RUN_ID,
        session.candidate.artifact_id,
        FindingState.CANDIDATE,
    )
    candidate = scout.finish(ctx, next_session)
    assert candidate.candidate_id == session.candidate_id
    (override,) = _overrides(ctx)
    assert override.data["fields"] == "candidate_id,run_id,artifact_id,state"
    assert override.data["written.state"] == "triaged"


# --- 후보 `location.symbol` 도장 (이슈 #633) ----------------------------------


def _with_symbol(location: Location, symbol: str | None, *, file: str | None = None) -> Location:
    return Location(
        file=file or location.file,
        symbol=symbol,
        line_start=location.line_start,
        line_end=location.line_end,
    )


# 도장이 고르는 심볼 표. `ctx`의 기본 `symbols_for`는 `None`(= 인덱스 없음, `dev_fake`)이라 도장이
# 아예 돌지 않는다 — 실서빙(`harness_pool`)은 인덱서 표를 들고 도장은 그 표에서 **후보 줄을 품는
# 함수**를 고른다. `other_fn`은 `VULNERABLE_C`에 없는 **합성 심볼**이다: 「같은 파일 다른 함수」를
# 세우는 것이 용도이고, 도장은 파일 내용이 아니라 이 표만 읽는다.
_PARSER_SYMBOLS = (
    Location(file="parser.c", symbol="parse_header", line_start=3, line_end=6),
    Location(file="parser.c", symbol="other_fn", line_start=20, line_end=40),
    # ⏵ 2026-10-14 — **다른 파일의 심볼이 표에 있어야** 파일 가드 핀이 자명해지지 않는다.
    # 이 줄이 없으면 `util.c` 후보는 품는 함수를 못 찾아 어차피 안 찍히므로
    # `test_scout_does_not_stamp_a_candidate_in_another_file`이 가드를 지워도 통과했다
    # (적대적 리뷰 실측: 378 passed로 생존).
    Location(file="util.c", symbol="util_fn", line_start=1, line_end=99),
)


def _symbol_table(symbols: tuple[Location, ...]):  # noqa: ANN202
    def _for(file: str) -> list[Location] | None:
        found = [symbol for symbol in symbols if symbol.file == file]
        return found or None

    return _for


def _triage_writing_location(  # noqa: ANN202
    ctx: RunContext,
    target: Location,
    written: Location,
    *,
    symbols: tuple[Location, ...] = _PARSER_SYMBOLS,
):
    """모델이 후보 `location`을 `written`으로 낸 것처럼 Scout를 돌린다(심볼 표를 배선해서)."""
    stub = _RewritingModelClient(ctx.models, lambda value: value.transition(location=written))
    patched = dataclasses.replace(ctx, models=stub, symbols_for=_symbol_table(symbols))
    return _triage(patched, target)


def _stamped_symbols(ctx: RunContext) -> list:  # noqa: ANN202
    return [e for e in ctx.trace.events if e.kind is EventKind.SCOUT_SYMBOL_STAMPED]


def test_scout_stamps_the_enclosing_function_when_the_model_left_it_null(ctx: RunContext) -> None:
    """모델이 `symbol`을 `null`로 낸 후보(s17 12/12)는 그 줄을 품는 함수 이름을 받는다.

    `file`·`line_*`는 모델이 쓴 값 그대로이고(여기선 `line_end`를 일부러 다르게 냈다), 저장된 후보도
    같은 값이다. 채운 홉은 `scout_symbol_stamped`로 남는다.
    """
    written = _with_symbol(VULN_TARGET, None)
    written = Location(file=written.file, line_start=3, line_end=9)

    candidate, _, _ = _triage_writing_location(ctx, VULN_TARGET, written)

    assert candidate.location == Location(
        file="parser.c", symbol="parse_header", line_start=3, line_end=9
    )
    assert ctx.store.get("candidate", candidate.candidate_id).location == candidate.location
    (event,) = _stamped_symbols(ctx)
    assert event.subject_id == candidate.candidate_id
    assert (event.data["hop"], event.data["symbol"]) == (0, "parse_header")


def test_scout_stamps_the_other_function_not_the_target_symbol(ctx: RunContext) -> None:
    """같은 파일 **다른 함수**의 후보에는 대상 이름이 아니라 그 함수 이름이 찍힌다 (리뷰 MEDIUM 1).

    전 판은 조건이 셋(파일 같음 · 모델 `null` · 대상에 이름 있음)뿐이라 대상 이름을 그대로 찍었다.
    슬라이스 타깃의 `FACTS.target`은 entry 한 줄이고 `SOURCE`는 여러 함수에 걸치므로 이 경우가
    실재한다 — `inflate.c:1200`(`inflate_fast` 안) 후보에 `inflate`가 붙어 `repro_spec`·`validate`
    argv에 실렸다.
    """
    written = Location(file="parser.c", line_start=25, line_end=30)

    candidate, _, _ = _triage_writing_location(ctx, VULN_TARGET, written)

    assert candidate.location.symbol == "other_fn"
    (event,) = _stamped_symbols(ctx)
    assert event.data["symbol"] == "other_fn"


@pytest.mark.parametrize(
    ("written", "why"),
    [
        (Location(file="parser.c", line_start=12, line_end=14), "표의 어느 심볼도 안 품는 줄이다"),
        (Location(file="parser.c"), "줄이 없어 품는 함수를 고를 근거가 없다"),
    ],
)
def test_scout_stamps_nothing_when_the_index_cannot_place_the_line(
    ctx: RunContext, written: Location, why: str
) -> None:
    """품는 함수를 못 찾으면 안 찍는다 — 대상 이름으로 메우지 않는다(CLAUDE.md §0)."""
    candidate, _, _ = _triage_writing_location(ctx, VULN_TARGET, written)

    assert candidate.location.symbol is None, why
    assert _stamped_symbols(ctx) == []


def test_scout_stamps_nothing_without_an_index(ctx: RunContext) -> None:
    """인덱스가 없는 프로필(`dev_fake` 기본 `symbols_for` = `None`)에서는 도장이 안 돈다."""
    written = _with_symbol(VULN_TARGET, None)

    candidate, _, _ = _triage_writing_location(ctx, VULN_TARGET, written, symbols=())

    assert candidate.location.symbol is None
    assert _stamped_symbols(ctx) == []


def test_the_stamped_symbol_is_the_one_the_model_saw_in_facts(ctx: RunContext) -> None:
    """찍힌 값이 모델이 hop0 `FACTS.target`에서 본 심볼과 같다 (리뷰 MEDIUM 2).

    대상 함수 **안**의 후보에서는 품는 함수와 `FACTS.target`이 같은 심볼을 가리켜야 한다 —
    `docs/agent-contracts.md`가 「후보 `location`은 모델이 사실 블록의 대상을 옮겨 적는 값」이라
    적으므로, 하네스가 채운 값이 그 블록과 갈리면 그 서술이 거짓이 된다.
    ⚠ **이 등식은 #641(= `fix/529-scout-source-declaration`)이 병합된 뒤에야 실질적으로 문다.**
    이 브랜치 단독에서는 이 테스트의 `VULN_TARGET`이 `capsule.target`(Joern 표기)이자 FACTS
    `target`이고 심볼 표(`_PARSER_SYMBOLS`)가 같은 이름을 들게 짜여 있어 자명하게 통과한다.
    두 표기가 갈리는 입력(C++ 클래스 바깥 정의 `P::parse` 등)은 이 테스트에 없다 — 갈림은
    `test_the_stamp_uses_the_index_spelling_not_the_target_spelling`이 물고 실서빙에서 났는지는
    **미실측**이다. #641은 `input: source` 선언에서 hop0 FACTS `target`을
    `enclosing_scope(ctx.symbols_for(...))`로 바꾸므로 그때 원천이 둘이 된다 — 둘 다 같은 심볼 표를
    읽고 **후보 줄이 그 함수 안인 한** 이 등식이 유지된다 — 후보가 같은 파일 다른 함수에 앉으면
    갈리고, 그것이 이 PR이 의도적으로 만든 동작이다.
    """
    written = Location(file="parser.c", line_start=4, line_end=5)

    candidate, _, _ = _triage_writing_location(ctx, VULN_TARGET, written)

    facts = _facts_of(ctx.models.last_for(Role.SCOUT).body)
    assert candidate.location.symbol == facts["target"]["symbol"]
    (event,) = _stamped_symbols(ctx)
    assert event.data["symbol"] == facts["target"]["symbol"]


def test_the_stamp_uses_the_index_spelling_not_the_target_spelling(ctx: RunContext) -> None:
    """대상(Joern 표기)과 인덱스 표기가 다르면 찍히는 것은 **인덱스 표기**다.

    FACTS `target.symbol`(`capsule.target`)과 찍힌 값이 갈릴 수 있다는 사실의 핀이다 — 인덱서가 C++
    클래스 바깥 정의를 `P::parse`로 내는 것(`tools/harness-tools/indexer.py`)을 본뜬 표다. 갈림이
    실서빙에서 났는지는 **미실측**이다.
    """
    target = Location(file="parser.c", symbol="parse", line_start=3, line_end=6)
    table = (Location(file="parser.c", symbol="P::parse", line_start=3, line_end=6),)
    written = Location(file="parser.c", line_start=4, line_end=5)

    candidate, _, _ = _triage_writing_location(ctx, target, written, symbols=table)

    assert candidate.location.symbol == "P::parse" != target.symbol


def test_the_session_carries_the_hop0_target_for_the_follow_up_hops(ctx: RunContext) -> None:
    """`open_session`이 `capsule.target`을 세션에 싣는 **실제 배선**을 문다.

    아래 되묻기 테스트는 세션을 손으로 짜(`_ne_session` + `dataclasses.replace`) `target`을 직접
    넣으므로 그 배선을 안 본다 — `open_session`의 `target=capsule.target`을 `None`으로 바꿔도
    초록이었다(생존 변이).
    """
    artifacts = preprocess.run(ctx, source_root=_root_of(ctx))
    artifact = next(a for a in artifacts if a.path == VULN_TARGET.file)
    capsule = retrieval.run(ctx, target=VULN_TARGET, level=CapsuleLevel.LIGHT)

    session = scout.open_session(ctx, artifact=artifact, capsule=capsule)

    assert session.target == capsule.target
    assert session.target is not None and session.target.symbol == "parse_header"


def test_scout_keeps_a_symbol_the_model_wrote(ctx: RunContext) -> None:
    """모델이 쓴 `symbol`은 대상과 달라도 그대로다 — 도장은 `null`만 채운다. `""`도 쓴 값이다."""
    for written_symbol in ("other_fn", ""):
        written = _with_symbol(VULN_TARGET, written_symbol)

        candidate, _, _ = _triage_writing_location(ctx, VULN_TARGET, written)

        assert candidate.location.symbol == written_symbol
    assert _stamped_symbols(ctx) == []


def test_scout_does_not_stamp_a_candidate_in_another_file(ctx: RunContext) -> None:
    """대상 파일이 아닌 곳의 후보에 대상 함수 이름을 붙이지 않는다 — 슬라이스는 여러 파일을 본다."""
    written = _with_symbol(VULN_TARGET, None, file="util.c")

    candidate, _, _ = _triage_writing_location(ctx, VULN_TARGET, written)

    assert candidate.location.file == "util.c"
    assert candidate.location.symbol is None
    assert _stamped_symbols(ctx) == []


def test_scout_stamps_nothing_when_the_target_has_no_symbol(ctx: RunContext) -> None:
    """이름 없는 대상(`FACTS.target.symbol`이 `null`)에서는 지어내지 않는다."""
    nameless = _with_symbol(VULN_TARGET, None)

    candidate, _, _ = _triage_writing_location(ctx, nameless, nameless)

    assert candidate.location.symbol is None
    assert _stamped_symbols(ctx) == []


def test_scout_v3_follow_up_hop_stamps_the_symbol_from_the_hop0_target(ctx: RunContext) -> None:
    """되묻기 홉의 후보도 `symbol`이 `null`이면 hop0의 target으로 가려 품는 함수를 채운다.

    `finish()`가 마지막 홉의 후보를 저장하므로 hop0만 막으면 되묻기를 거친 후보는 도로 `null`이다.
    """
    session = dataclasses.replace(_ne_session(), target=VULN_TARGET)

    class _Stub:
        def complete(self, *, role: Role, prompt, schema, history=()):  # noqa: ANN001, ANN202
            written = session.candidate.transition(
                location=_with_symbol(VULN_TARGET, None), reason_codes=["triage_review"]
            )
            value = ScoutResponse(candidate=written, analysis_summary=AnalysisSummary(claim="h1"))
            return ModelResult(
                value=value,
                usage=Usage(input_tokens=110, output_tokens=15, cached_tokens=90),
                model="stub",
                raw_text="HOP1 RAW",
            )

    next_session = scout.continue_session(
        dataclasses.replace(ctx, models=_Stub(), symbols_for=_symbol_table(_PARSER_SYMBOLS)),
        session,
        "FOLLOWUP",
    )

    assert next_session.candidate.location.symbol == "parse_header"
    assert next_session.target == VULN_TARGET, "다음 홉이 또 쓸 수 있게 이어 쥔다"
    (event,) = _stamped_symbols(ctx)
    assert (event.data["hop"], event.data["symbol"]) == (1, "parse_header")


def test_the_stamped_symbol_reaches_the_repro_spec_but_no_model_prompt(ctx: RunContext) -> None:
    """도장이 닿는 곳은 합성 `repro_spec` 문구 하나다 — Auditor·Critic 프롬프트 바이트는 안 변한다.

    Auditor는 후보를 `_describe_candidate`(ID·CWE·신호)로만 렌더하고 FACTS의 `target`은
    `capsule.target`이라 `Candidate.location`을 안 읽는다. Critic은 Finding만 읽는다. 이 핀이
    깨지면(후보 위치를 프롬프트에 싣기 시작하면) 도장이 요청 바이트를 바꾸기 시작한 것이니
    `docs/agent-contracts.md`의 ⏵ 2026-10-13 문단(Scout)을 같이 고친다.
    """
    stamped, capsule, evidences = _triage_writing_location(
        ctx, VULN_TARGET, _with_symbol(VULN_TARGET, None)
    )
    assert stamped.location.symbol == "parse_header", "전제 — 도장이 찍혔다"
    unstamped = stamped.transition(location=_with_symbol(VULN_TARGET, None))

    assert auditor._synthesize_repro_spec(stamped).expected_signal == (
        "sanitizer crash at parse_header"
    )
    assert auditor._synthesize_repro_spec(unstamped).expected_signal == (
        "sanitizer crash at parser.c"
    )
    auditor.run(ctx, candidate=stamped, capsule=capsule, evidences=evidences)
    auditor.run(ctx, candidate=unstamped, capsule=capsule, evidences=evidences)
    prompt_a, prompt_b = [p for role, p in ctx.models.prompts if role is Role.AUDITOR][-2:]
    issued = re.compile(r"(?:find|mev)_\d+")  # 호출마다 새로 발급되는 ID만 가린다
    assert prompt_a.prefix == prompt_b.prefix
    assert issued.sub("<id>", prompt_a.body) == issued.sub("<id>", prompt_b.body)


def test_auditor_stamps_the_issued_ids_over_the_ones_the_model_wrote(ctx: RunContext) -> None:
    """D83 ② — Auditor가 **남의** `finding_id`·`candidate_id`, 다른 `run_id`를 써도 둘 다 남는다."""
    first_candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    first, _ = auditor.run(ctx, candidate=first_candidate, capsule=capsule, evidences=evidences)
    second_candidate, capsule2, evidences2 = _triage(ctx, VULN_TARGET)
    foreign_run = "run_19990101_0001"
    stub = _RewritingModelClient(
        ctx.models,
        lambda value: value.transition(
            finding_id=first.finding_id,
            candidate_id=first_candidate.candidate_id,
            run_id=foreign_run,
        ),
    )

    second, _ = auditor.run(
        dataclasses.replace(ctx, models=stub),
        candidate=second_candidate,
        capsule=capsule2,
        evidences=evidences2,
    )

    assert second.finding_id != first.finding_id, "모델이 쓴 남의 finding_id가 살아남았다"
    assert ctx.store.get("finding", first.finding_id) == first, "첫 Finding의 행이 덮였다"
    stored = ctx.store.get("finding", second.finding_id)
    assert stored is not None
    assert (stored.candidate_id, stored.run_id) == (second_candidate.candidate_id, RUN_ID)
    created = [
        e.subject_id
        for e in ctx.trace.events
        if e.kind is EventKind.TRANSITION and e.data["rule"] == "F-1"
    ]
    assert created == [first.finding_id, second.finding_id]
    (override,) = _overrides(ctx)
    assert override.subject_id == second.finding_id
    assert override.data["fields"] == "finding_id,run_id,candidate_id"
    assert override.data["written.finding_id"] == first.finding_id


@pytest.mark.parametrize("keep_issued", [True, False], ids=["issued_plus_foreign", "foreign_only"])
def test_auditor_materializes_only_the_issued_missing_id(
    ctx: RunContext, keep_issued: bool
) -> None:
    """D83 ③ — `missing_evidence`에 발급되지 않은 ID를 쓰면 그 ID는 저장되지 않는다.

    노드가 발급하는 `missing_id`는 호출당 하나다. 그 밖의 ID마다 `blocking=True`
    MissingEvidence가 만들어지면 해제 경로가 없어 그 Finding은 run 끝까지 E2 미만이다(D65).
    도장은 **비어 있지 않으면 발급 ID 하나로 정규화한다** — 발급 안 된 ID만 써도 「확인 불가」
    요청은 blocking 하나로 남는다(교집합만 남기면 0건이 되어 E2가 열린다 —
    `test_auditor_missing_evidence_gate.py`). 그 blocking을 내리는 경로는 여전히 없다(D65는
    안 닫힌다).
    """
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    stub = _RewritingModelClient(
        ctx.models,
        lambda value: value.transition(
            missing_evidence=[*(value.missing_evidence if keep_issued else []), "mev_9999"]
        ),
    )

    finding, missings = auditor.run(
        dataclasses.replace(ctx, models=stub), candidate=candidate, capsule=capsule
    )

    (issued,) = stub.written[0].missing_evidence  # Fake는 정적 신호 없는 대상에 발급 ID를 쓴다
    assert ctx.store.get("missing_evidence", "mev_9999") is None, "발급 안 된 ID가 저장됐다"
    assert finding.missing_evidence == [issued]
    assert [m.missing_id for m in missings] == [issued]
    assert all(m.blocking for m in missings)
    (override,) = _overrides(ctx)
    assert override.data["fields"] == "missing_evidence"
    written = f"{issued},mev_9999" if keep_issued else "mev_9999"
    assert override.data["written.missing_evidence"] == written


@pytest.mark.parametrize("state", [FindingState.VALIDATED, FindingState.PROVEN])
def test_stamp_issued_revalidates_instead_of_copying(ctx: RunContext, state: FindingState) -> None:
    """도장이 **재검증**으로 덮는다 — `model_copy(update=)`면 이 검사가 빨개진다.

    지금 호출부가 덮는 `state`는 `candidate`·`hypothesized`뿐이라, 거기서는 `model_copy`로 바꿔도
    `Finding._promoted_states_need_backing`(`validated` 이상에서만 발동)이 안 걸려 전 스위트가
    초록이었다(D83 리뷰 변이 ⑤). 그 검증기를 직접 태운다 — 재현 명세 없는 `validated`·`proven`은
    만들어질 수 없어야 한다.
    """
    # ⏵ 2026-10-05 — `synthesize_repro_spec` 기본값이 1이 되어 Auditor가 명세를 **채운다.**
    # 이 검사의 전제는 「명세 없는 Finding」이라 그 스위치를 끄고 만든다(아래 단언이 전제를 묶는다).
    _set_synthesize(ctx, 0)
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)
    assert finding.repro_spec is None, "픽스처 전제 — 재현 명세 없는 Finding이어야 검증기가 문다"

    with pytest.raises(ValidationError, match="repro_spec"):
        ctx.stamp_issued(
            finding,
            {"state": state},
            stage=Stage.AUDITOR,
            subject_kind="finding",
            subject_id=finding.finding_id,
        )


def test_gate_records_grade_and_applies_transition(ctx: RunContext) -> None:
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    graded, result = gate_node.run(
        ctx,
        finding=finding,
        evidences=evidences,
        missings=tuple(missings),
        candidate_location=candidate.location,
    )

    evaluated = next(e for e in ctx.trace.events if e.kind is EventKind.GATE_EVAL)
    assert evaluated.data["grade"] == result.grade.value
    assert graded.state in {FindingState.HYPOTHESIZED, FindingState.QUEUED}


def test_gate_without_evidence_queues_the_finding(ctx: RunContext) -> None:
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)

    graded, result = gate_node.run(ctx, finding=finding)

    assert not finding.evidence_refs
    assert graded.state is FindingState.QUEUED
    assert result.grade.value == "E0"


# --- 7단계 Critic -----------------------------------------------------------


def test_critic_prompt_omits_auditor_reasoning(ctx: RunContext) -> None:
    """Auditor 원문 추론을 넘기면 반박이 아니라 재진술이 돌아온다 (§2.3)."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)
    graded, _ = gate_node.run(ctx, finding=finding, evidences=evidences, missings=tuple(missings))

    critic.run(ctx, finding=graded, evidences=evidences, missings=tuple(missings))

    prompt = ctx.models.last_for(Role.CRITIC)
    assert finding.rca_draft and finding.rca_draft not in prompt.body
    assert finding.title in prompt.body
    assert finding.finding_id in prompt.body


def _critic_says(ctx: RunContext, **update: object) -> None:
    """Fake Critic은 표준 어휘로만 답한다 - 실서빙에서만 나는 응답 모양을 **응답에 끼워 넣는다**
    (`test_unreadable_critic_verdict_is_held_and_recorded`와 같은 방식). `DecisionDraft`의 필드를
    덮어쓴다."""
    inner = ctx.models.complete

    def forced(*, role, prompt, schema, history=()):  # noqa: ANN001, ANN202
        result = inner(role=role, prompt=prompt, schema=schema, history=history)
        if role is not Role.CRITIC:
            return result
        return result.model_copy(update={"value": result.value.model_copy(update=update)})

    ctx.models.complete = forced  # type: ignore[method-assign]


def _normalized_events(ctx: RunContext) -> list:  # noqa: ANN202
    return [e for e in ctx.trace.events if e.kind is EventKind.CRITIC_NORMALIZED]


def _critic_verdict_policy(ctx: RunContext):  # noqa: ANN202
    return next(
        e
        for e in ctx.trace.events
        if e.kind is EventKind.POLICY_DECISION and e.data["point"] == "critic_verdict"
    )


def test_ungrounded_rebuttal_is_normalized_once(ctx: RunContext) -> None:
    """근거 없는 반박은 `need_evidence`로 처리하되 지표에 남는다. 정규화 지점은 한 곳이다.

    호출 그래프 근거가 없는 상태를 만든다 — 그래야 Critic이 근거 없이 반박한다(Fake는 이때 ID도 축도
    비운다). ⏵ 2026-10-13 (ADR 0015) - 목적지가 `uphold`였다. 사유는 `empty_axis`다.
    """
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)

    after, decision = critic.run(ctx, finding=finding)

    assert decision.verdict is Verdict.REBUT and not decision.is_grounded
    normalized = _normalized_events(ctx)
    assert len(normalized) == 1
    assert normalized[0].data["original_verdict"] == "rebut"
    assert normalized[0].data["reason"] == "empty_axis"
    assert normalized[0].data["invalid_axes"] == "[]"
    verdict_decision = _critic_verdict_policy(ctx)
    assert verdict_decision.data["basis.verdict"] == Verdict.NEED_EVIDENCE.value
    assert verdict_decision.data["reason"] == "critic_need_evidence"
    assert after.state is FindingState.QUEUED  # F-7 - 모델이 낸 need_evidence와 같은 길이다


def test_a_rebut_with_an_axis_outside_the_vocabulary_is_recorded_with_the_offending_values(
    ctx: RunContext,
) -> None:
    """`critic_normalized`가 사유와 어휘 밖 원문을 싣는다 - 첫 v7 run의 무효 축 비율이 이것으로
    센다.

    어휘 안의 축(`guard`)이 섞여 있어도 **무효인 것만** 싣는다. 값은 40자·다섯 개로 자른다.
    """
    _critic_says(
        ctx,
        verdict="rebut",
        rebuttal_axis=["guard", "guard-check", "무근거", "x" * 100],
        evidence_refs=[],
    )
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)

    after, _ = critic.run(ctx, finding=finding)

    (event,) = _normalized_events(ctx)
    assert event.data["reason"] == "invalid_axis"
    assert json.loads(event.data["invalid_axes"]) == ["guard-check", "무근거", "x" * 40]
    assert _critic_verdict_policy(ctx).data["basis.verdict"] == "need_evidence"
    assert after.state is FindingState.QUEUED


def test_the_offending_axes_are_bounded(ctx: RunContext) -> None:
    bad = [f"bad{i}" for i in range(9)]
    _critic_says(ctx, verdict="rebut", rebuttal_axis=bad, evidence_refs=[])
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)

    critic.run(ctx, finding=finding)

    (event,) = _normalized_events(ctx)
    assert json.loads(event.data["invalid_axes"]) == [f"bad{i}" for i in range(5)]


def test_a_v7_shaped_rebut_with_vocabulary_axes_is_a_real_rebuttal(ctx: RunContext) -> None:
    """어휘 안의 축을 든 `rebut`(`evidence_refs=[]`)은 정규화 대상이 아니다 - 이벤트도 없고
    F-6이다."""
    _critic_says(ctx, verdict="rebut", rebuttal_axis=["guard", "sink"], evidence_refs=[])
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)

    after, decision = critic.run(ctx, finding=finding)

    assert decision.verdict is Verdict.REBUT and decision.is_grounded
    assert _normalized_events(ctx) == []
    assert _critic_verdict_policy(ctx).data["reason"] == "critic_rebutted"
    assert after.state is FindingState.QUEUED


def test_critic_uphold_does_not_lift_a_finding_held_at_e1(ctx: RunContext) -> None:
    """6단계에서 E1으로 보류된 Finding은 7단계에서 반박 실패가 와도 `validated`가 안 된다 (#154).

    러너는 등급이 아니라 상태(`hypothesized`)로 7단계에 올리므로 E1 보류분도 Critic에 간다 —
    그것은 이 테스트가 안 바꾼다. 막는 것은 거기서 E3로 건너뛰는 것이다. 그래프 근거를 빼서
    E2 조건 하나만 깬다 — 경로·재현 명세·정적 근거는 그대로다.
    """
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)
    no_graph = tuple(ev for ev in evidences if ev.kind is not EvidenceKind.GRAPH)
    held, gated = gate_node.run(
        ctx,
        finding=finding,
        evidences=no_graph,
        missings=tuple(missings),
        candidate_location=candidate.location,
    )
    assert gated.grade.value == "E1" and held.state is FindingState.HYPOTHESIZED
    assert held.path and held.repro_spec is not None
    # ⏵ 2026-10-13 (ADR 0015) - 이 테스트는 Fake Critic의 **근거 없는 반박**이 `uphold`로
    # 정규화되는 것에
    # 기대 반박 실패를 얻었다. 지금은 그것이 `need_evidence`(F-7, `QUEUED`)라 명제가 안 선다. 반박
    # 실패는
    # **진짜 `uphold`로** 준다 - 인용한 정적 근거를 든 근거 있는 지지다.
    static_ids = [ev.evidence_id for ev in no_graph if ev.kind is EvidenceKind.STATIC]
    _critic_says(ctx, verdict="uphold", rebuttal_axis=[], evidence_refs=static_ids)

    after, decision = critic.run(ctx, finding=held, evidences=no_graph, missings=tuple(missings))

    assert decision.verdict is Verdict.UPHOLD and decision.evidence_refs

    assert after.state is FindingState.HYPOTHESIZED
    last = [e for e in ctx.trace.events if e.kind is EventKind.GATE_EVAL][-1]
    assert last.stage == Stage.CRITIC and last.data["grade"] == "E1"


# --- Critic 근거 본문 배선 (#237 HIGH-1) ------------------------------------


def _evidence(evidence_id: str, payload_ref: str = "artifacts/run/asan/e.log") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        run_id=RUN_ID,
        kind=EvidenceKind.SANITIZER,
        source="asan",
        artifact_id="a.c",
        payload_ref=payload_ref,
        digest="sha256:" + "0" * 64,
        observed_at=NOW,
    )


def test_evidence_summaries_bounds_and_skips_missing() -> None:
    evs = [_evidence(f"evidence_000{i}", f"r{i}") for i in (1, 2, 3)]
    payloads = {"r1": "short log", "r2": "x" * 5000}  # r3 has no payload
    out = evidence_summaries(evs, lambda ref: payloads.get(ref))
    assert out["evidence_0001"] == {"body": "short log", "truncated": False}
    assert out["evidence_0002"]["truncated"] is True
    assert "\n...\n" in out["evidence_0002"]["body"]  # deterministic head+tail
    assert "evidence_0003" not in out  # missing payload skipped -> metadata only


def test_evidence_summaries_respects_total_budget() -> None:
    evs = [_evidence(f"evidence_000{i}", f"r{i}") for i in range(1, 4)]
    out = evidence_summaries(evs, lambda _ref: "y" * 3000, per_cap=3000, total_cap=4000)
    assert out["evidence_0001"]["body"]  # first fills
    assert out["evidence_0003"]["body"] == ""  # past total budget -> empty body


def test_evidence_body_never_exceeds_the_cap() -> None:
    """#247 리뷰 LOW-3: 구분자까지 cap 안에 들어야 예산이 안 샌다. 그리고 cap이 구분자보다
    작아도 `text[-0:]`로 원문 전체가 나가면 안 된다."""
    evs = [_evidence("evidence_0001", "r1")]
    for cap in (1, 2, 4, 5, 50, 800):
        out = evidence_summaries(evs, lambda _ref: "z" * 5000, per_cap=cap, total_cap=10_000)
        body = out["evidence_0001"]["body"]
        assert len(body) <= cap, (cap, len(body))
        assert out["evidence_0001"]["truncated"] is True


def test_build_facts_evidence_body_is_opt_in() -> None:
    ev = _evidence("evidence_0001", "r1")
    plain = build_facts(run_id=RUN_ID, ids={}, evidences=[ev])
    assert plain["evidence"] == [
        {"evidence_id": "evidence_0001", "kind": EvidenceKind.SANITIZER.value, "ok": True}
    ]
    with_body = build_facts(
        run_id=RUN_ID,
        ids={},
        evidences=[ev],
        evidence_bodies={"evidence_0001": {"body": "heap-overflow", "truncated": False}},
    )
    assert with_body["evidence"][0]["body"] == "heap-overflow"
    assert with_body["evidence"][0]["truncated"] is False


def test_build_facts_evidence_location_is_opt_in_by_version() -> None:
    """`evidence_locations`를 켠 호출(content-aware **판**)은 Evidence마다 `location`을 싣는다.

    위치는 **판단 보조**다 — 게이트 등급을 이 값이 정하지 않는다(`gate.evaluate`의
    `p1 = static_ok or location_ok`에서 Evidence에 위치를 싣는 곳이 semgrep(STATIC) 하나라
    **인용과 무관하게** `static_ok`가 참이다 — 6단계가 타깃 관측분을 붙인다
    (이슈 #552) · `container_tools.first_hit_location` docstring). 그 위치는
    semgrep `results[0]`(첫 히트) 하나다. 위치를 싣는 것이 모델의 인용을 늘리는지는 미실측이다
    (이슈 #563). ⏵ **2026-10-09 — 「돈 run 0건」은 더 이상 참이 아니다**: `run_20261007_0001`이
    위치를 싣고 돌았고(`5828a0a` `nodes/auditor.py:197` `_CONTENT_AWARE_VERSIONS` ∋ v10 — 위치를
    싣는 판으로 돌았다) **인용은 0/5**였다. 단 그 run의 semgrep 히트
    (`1086`~`1296`)가 후보 위치(`429`·`486`·`743`)와 **안 겹쳐** 「겹치는 위치를 보여 주면
    인용이 느는가」는 여전히 0회다 — 그 축이 #563이다.

    ⏵ 2026-10-13(#563) — 겹침 횟수는 이제 run마다 `RunRecord.location_overlap`이 싣는다.
    #248 뒤 읽힌 값은 `run_20260924_0003` 36/36 참(스모크 · `checksum` 0) · 2026-10-12 보고서
    run 6/6 거짓(trace 재계수 - 저장소 밖)이고 과제가 다르다. `run_20261007_0001`은 `p0`
    지름길이라 이 지표로는 미측정이다(D106의 오프라인 재등급 5/5 거짓은 run 실측이 아니다).
    통제 대조 0건.

    **본문과 따로 정해진다**: payload가 없어 본문이 안 실리는 Evidence에도 같은 판이면 키는 나간다
    (적대적 리뷰 #480 LOW-1) — 같은 판의 Critic 줄(`critic._describe_evidence`)과 같은 값을 든다.
    위치가 없는 Evidence는 `null`로 싣는다 — 바로 위 `target`과 같은 관례이고, 키가 아예 없으면
    「위치가 없는 도구」와 「이 판은 위치를 안 싣는다」가 구별되지 않는다(CLAUDE.md §0).
    끄면(Scout, content-aware가 아닌 판) 키가 없다 — 프롬프트 바이트가 종전과 같다.
    """
    located = _evidence("evidence_0001", "r1").model_copy(
        update={"location": Location(file="a.c", line_start=1086, line_end=1087)}
    )
    bodies = {"evidence_0001": {"body": "semgrep hit", "truncated": False}}
    shown = {"file": "a.c", "symbol": None, "line_start": 1086, "line_end": 1087}

    with_body = build_facts(
        run_id=RUN_ID, ids={}, evidences=[located], evidence_bodies=bodies, evidence_locations=True
    )
    assert with_body["evidence"][0]["location"] == shown

    # payload가 없어 본문이 빠져도(맵이 빔) 같은 판이면 위치는 나간다 — 본문과 별개다.
    no_body = build_facts(
        run_id=RUN_ID, ids={}, evidences=[located], evidence_bodies={}, evidence_locations=True
    )
    assert no_body["evidence"] == [
        {"evidence_id": "evidence_0001", "kind": "sanitizer", "ok": True, "location": shown}
    ]

    # 위치 없는 Evidence도 키는 나간다 — `null`이 "도구가 위치를 안 줬다"는 사실이다.
    plain_ev = _evidence("evidence_0001", "r1")
    no_loc = build_facts(
        run_id=RUN_ID, ids={}, evidences=[plain_ev], evidence_bodies=bodies, evidence_locations=True
    )
    assert no_loc["evidence"][0]["location"] is None

    # 끄면 본문이 있어도 위치는 안 나간다(바이트 동일 — opt-in) · 본문만 종전 모양이다.
    off = build_facts(run_id=RUN_ID, ids={}, evidences=[located], evidence_bodies=bodies)
    assert off["evidence"] == [
        {
            "evidence_id": "evidence_0001",
            "kind": "sanitizer",
            "ok": True,
            "body": "semgrep hit",
            "truncated": False,
        }
    ]
    assert "location" not in build_facts(run_id=RUN_ID, ids={}, evidences=[located])["evidence"][0]


def test_describe_evidence_appends_body_when_present() -> None:
    ev = _evidence("evidence_0001", "r1")
    assert "heap-overflow" not in critic._describe_evidence([ev])
    described = critic._describe_evidence(
        [ev], {"evidence_0001": {"body": "heap-overflow", "truncated": True}}
    )
    assert "heap-overflow" in described and "[truncated]" in described


def test_describe_evidence_location_is_opt_in_by_version() -> None:
    """Critic 근거 줄의 위치 토막은 `with_location`(content-aware **판**)일 때만 붙는다.

    **꺼진 쪽(v1)은 main의 렌더와 바이트 동일해야 한다**(적대적 리뷰 #480 HIGH-1) — 아래 기대
    문자열은 main(`origin/main`)의 `_describe_evidence`가 같은 입력에 낸 실제 출력이다(2026-10-04
    그 함수를 직접 돌려 대조했다). `bodies`가 `{}`든 `None`이든 같다.

    켜면 위치가 줄에 `file:start-end`로 붙는다. 위치 없는 Evidence는 **줄에 아무것도 더하지
    않는다** — 산문 한 줄이라 `null`을 적으면 노이즈다(FACTS JSON 블록은 키 부재가 뜻을 흐려서
    `null`을 싣는다). 위치는 Critic의 판단 보조이고 게이트 등급 근거가 아니다 — 근거는
    `test_build_facts_evidence_location_is_opt_in_by_version` docstring.
    """
    loc = Location(file="a.c", line_start=1086, line_end=1087)
    located = _evidence("evidence_0001", "r1").model_copy(update={"location": loc})
    bodies = {"evidence_0001": {"body": "heap-overflow", "truncated": True}}
    main_line = "- evidence_0001 · sanitizer · ok · asan"

    # v1 — 위치가 있어도 main과 같은 줄이다.
    assert critic._describe_evidence([located]) == main_line
    assert critic._describe_evidence([located], {}) == main_line
    assert (
        critic._describe_evidence([located], bodies)
        == f"{main_line}\n    heap-overflow [truncated]"
    )

    # content-aware 판 — 위치가 붙고, 본문이 있으면 그 앞 줄 끝에 붙는다.
    on = critic._describe_evidence([located], with_location=True)
    assert on == f"{main_line} · a.c:1086-1087"
    on_body = critic._describe_evidence([located], bodies, with_location=True)
    assert on_body == f"{main_line} · a.c:1086-1087\n    heap-overflow [truncated]"

    # 위치 없는 Evidence는 켜도 종전과 같은 한 줄이다.
    plain = _evidence("evidence_0001", "r1")
    assert critic._describe_evidence([plain], with_location=True) == main_line


def test_location_text_never_prints_a_missing_value() -> None:
    """줄 필드는 선택이다 — 없는 값을 `None`이라 적지 않는다(CLAUDE.md §0)."""
    assert critic._location_text(Location(file="a.c")) == "a.c"
    assert critic._location_text(Location(file="a.c", line_start=7)) == "a.c:7"
    assert critic._location_text(Location(file="a.c", line_start=7, line_end=7)) == "a.c:7"
    assert critic._location_text(Location(file="a.c", line_start=7, line_end=9)) == "a.c:7-9"


def test_critic_v1_omits_evidence_body_even_with_payload(ctx: RunContext) -> None:
    """dev_fake critic은 v1이라 payload가 있어도 본문을 안 싣는다 (content-aware 버전만 싣는다)."""
    ctx.read_payload = lambda _ref: "SANITIZER heap-buffer-overflow at parse_header"
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)
    graded, _ = gate_node.run(ctx, finding=finding, evidences=evidences, missings=tuple(missings))

    critic.run(ctx, finding=graded, evidences=evidences, missings=tuple(missings))

    prompt = ctx.models.last_for(Role.CRITIC)
    assert "heap-buffer-overflow" not in prompt.body  # v1 stays metadata-only (byte-identical)


def _critic_prompt_at(ctx: RunContext, version: str, payload: str | None):  # noqa: ANN202
    """dev_fake Critic을 `version`으로 바꿔 한 번 돌리고 (프롬프트, 인용한 Evidence)를 준다."""
    key = ctx.config.model_key_for(Role.CRITIC.value)
    models = {
        **ctx.config.models,
        "fake_critic_probe": ctx.config.models[key].transition(prompt_template_version=version),
    }
    roles = {**ctx.config.profile.roles, Role.CRITIC.value: "fake_critic_probe"}
    ctx.config = ctx.config.transition(
        models=models, profile=ctx.config.profile.transition(roles=roles)
    )
    ctx.read_payload = lambda _ref: payload
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)
    graded, _ = gate_node.run(ctx, finding=finding, evidences=evidences, missings=tuple(missings))

    critic.run(ctx, finding=graded, evidences=evidences, missings=tuple(missings))

    return ctx.models.last_for(Role.CRITIC), gate.evidences_for(graded, evidences)


def _facts_of(body: str) -> dict:  # noqa: ANN202
    return json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)[-1])


def test_critic_v1_prompt_does_not_carry_the_evidence_location(ctx: RunContext) -> None:
    """dev_fake의 v1 Critic은 기존 metadata 렌더를 유지한다 — 위치를 싣지 않는다.

    `_CONTENT_AWARE_VERSIONS`가 v1을 뺀 것은 v1이 바이트 동일이라 재측정(evaluation §2.3)이 안
    걸리게 하려는 것이다(#237 HIGH-1). 위치 토막이 그 문 밖에서 붙으면 판 번호 변화 없이 실서빙
    프롬프트가 바뀐다 — 적대적 리뷰 #480 HIGH-1이 `_describe_evidence([위치 든 Evidence], {})`로
    재현했다(main `… · asan` / PR `… · asan · a.c:1086-1087`).

    핀 샘플은 **노드를 거쳐 실제로 인용된** Evidence다(fake semgrep이 실도구
    `first_hit_location`처럼 위치를 싣는다) — 지어낸 샘플이 아니다. 기대 줄은 main의
    `_describe_evidence`가 내던 꼴이다.
    """
    prompt, own = _critic_prompt_at(ctx, "v1", "SANITIZER heap-buffer-overflow at parse_header")
    located = [ev for ev in own if ev.location is not None]
    assert located, "전제: 위치를 든 Evidence가 인용돼야 이 핀이 의미가 있다"

    for ev in own:
        main_line = (
            f"- {ev.evidence_id} · {ev.kind.value} · {'ok' if ev.ok else 'failed'} · {ev.source}"
        )
        assert f"{main_line}\n" in prompt.body or prompt.body.endswith(main_line)
        assert f"{main_line} · " not in prompt.body  # 위치 토막이 이어 붙지 않는다
    assert all("location" not in item for item in _facts_of(prompt.body)["evidence"])


# 판 목록은 노드의 상수에서 읽는다 — 집합에 판이 들어오면 이 검사가 자동으로 늘어난다(손으로 베낀
# 목록은 상수와 갈라진다). 제외한 판 없음 — 집합의 모든 판이 dev_fake 렌더에서 돈다.
@pytest.mark.parametrize("version", sorted(critic._CONTENT_AWARE_VERSIONS))
@pytest.mark.parametrize("payload", ["SANITIZER heap-buffer-overflow at parse_header", None])
def test_critic_content_aware_prompt_carries_the_evidence_location(
    ctx: RunContext, version: str, payload: str | None
) -> None:
    """content-aware 판은 위치를 EVIDENCE 줄과 FACTS 양쪽에 싣는다 — **payload가 없어도.**

    위치 노출은 판의 성질이지 payload 유무의 성질이 아니다. `read_payload`가 None이면
    `evidence_summaries`가 그 Evidence를 맵에서 뺀다(파일 부재·미배선). 위치를 본문과 같은 조건에
    묶으면 그때 FACTS에서 키가 사라지고 Critic 줄에는 남아 **두 자리가 서로 다른 값**을 든다
    (적대적 리뷰 #480 LOW-1).
    """
    prompt, own = _critic_prompt_at(ctx, version, payload)
    located = [ev for ev in own if ev.location is not None]
    assert located, "전제: 위치를 든 Evidence가 인용돼야 이 핀이 의미가 있다"

    facts_by_id = {item["evidence_id"]: item for item in _facts_of(prompt.body)["evidence"]}
    for ev in located:
        shown = facts_by_id[ev.evidence_id]["location"]
        assert shown == ev.location.model_dump(mode="json")
        assert f"{critic._location_text(ev.location)}" in prompt.body
    for ev in own:
        assert ("body" in facts_by_id[ev.evidence_id]) is (payload is not None)


def test_critic_includes_evidence_body_when_version_is_content_aware(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """content-aware 버전에서는 payload 본문이 실제로 프롬프트에 실린다."""
    monkeypatch.setattr(critic, "_CONTENT_AWARE_VERSIONS", frozenset({"v1"}))  # 게이트를 켠다
    ctx.read_payload = lambda _ref: "SANITIZER heap-buffer-overflow at parse_header"
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)
    graded, _ = gate_node.run(ctx, finding=finding, evidences=evidences, missings=tuple(missings))

    critic.run(ctx, finding=graded, evidences=evidences, missings=tuple(missings))

    prompt = ctx.models.last_for(Role.CRITIC)
    assert "heap-buffer-overflow" in prompt.body  # 본문이 EVIDENCE·FACTS에 닿는다


@pytest.mark.parametrize("version", ["v4", "v5"])
def test_critic_wiring_includes_evidence_body(ctx: RunContext, version: str) -> None:
    """실제 버전 선택이 payload 본문을 EVIDENCE·FACTS에 싣는다."""
    key = ctx.config.model_key_for(Role.CRITIC.value)
    models = {
        **ctx.config.models,
        "fake_critic_content": ctx.config.models[key].transition(prompt_template_version=version),
    }
    roles = {**ctx.config.profile.roles, Role.CRITIC.value: "fake_critic_content"}
    ctx.config = ctx.config.transition(
        models=models,
        profile=ctx.config.profile.transition(roles=roles),
    )
    ctx.read_payload = lambda _ref: "SANITIZER heap-buffer-overflow at parse_header"
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)
    graded, _ = gate_node.run(ctx, finding=finding, evidences=evidences, missings=tuple(missings))

    critic.run(ctx, finding=graded, evidences=evidences, missings=tuple(missings))

    prompt = ctx.models.last_for(Role.CRITIC)
    assert "heap-buffer-overflow" in prompt.body


def test_critic_content_aware_versions_pin_v3_v4_v5_v6_and_v7() -> None:
    """#247 리뷰 HIGH-1: 위 테스트는 게이트를 monkeypatch로 켜므로 **실제 값**을 안 문다.
    `_CONTENT_AWARE_VERSIONS`에서 버전을 빼면 그 버전이 조용히 metadata만 받는데
    (#237 HIGH-1과 같은 상태) 그걸 잡는 것은 이 단언뿐이다.
    """
    assert critic._CONTENT_AWARE_VERSIONS == frozenset({"v3", "v4", "v5", "v6", "v7"})


def test_critic_v3_prompt_is_content_conditional() -> None:
    """#237 HIGH-1: v3 prefix의 '현재 metadata-only' 현재형 단언이 조건부로 바뀌었다."""
    prefix = (PROMPTS_DIR / "critic" / "v3" / "prefix.md").read_text(encoding="utf-8")
    assert "For every current metadata-only call" not in prefix
    assert "In the current metadata-only runtime" not in prefix
    assert "carries a `body`" in prefix


# --- Auditor 근거 본문 배선 (v3 #256 항목 4 · v4 #328 · v5 절단 계약 · v6 #418·#421 · v7 #349) -


def test_auditor_v1_omits_evidence_body_even_with_payload(ctx: RunContext) -> None:
    """dev_fake auditor는 v1이라 payload가 있어도 본문을 안 싣는다 (content-aware 버전만)."""
    ctx.read_payload = lambda _ref: "SANITIZER heap-buffer-overflow at parse_header"
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    prompt = ctx.models.last_for(Role.AUDITOR)
    assert "heap-buffer-overflow" not in prompt.body  # v1 stays metadata-only (byte-identical)


def _auditor_evidence_facts(ctx: RunContext, version: str, payload: str | None):  # noqa: ANN202
    """dev_fake Auditor를 `version`으로 돌리고 (FACTS의 evidence 항목, 넘긴 Evidence)를 준다."""
    fake_entry = ctx.config.models["fake"].transition(prompt_template_version=version)
    ctx.config = ctx.config.transition(
        models={**ctx.config.models, "fake_probe": fake_entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_probe"}
        ),
    )
    ctx.read_payload = lambda _ref: payload
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    # **첫** Auditor 프롬프트(hop0)를 읽는다 — 한 방 판은 하나뿐이라 마지막과 같고, 도구 루프 판은
    # 홉마다 프롬프트를 남겨 마지막이 후속 홉의 도구 관측이라 FACTS가 없다. FACTS를 싣는 것은 hop0
    # 하나다(`auditor._render_hop0`). 마지막을 읽으면 `_CONTENT_AWARE_VERSIONS`에 루프 판이 들어오는
    # 순간(#491의 v8) 이 검사가 IndexError로 죽는다 — 어느 PR도 단독으로는 안 깨져 CI가 못 잡는다.
    hop0 = next(prompt for seen, prompt in ctx.models.prompts if seen is Role.AUDITOR)
    return _facts_of(hop0.body)["evidence"], evidences


def test_auditor_v1_facts_carry_no_evidence_location(ctx: RunContext) -> None:
    """v1 Auditor의 FACTS는 위치를 안 싣는다 — content-aware가 아닌 판은 바이트가 종전과 같다.

    본문·위치 둘 다 판이 정하는 opt-in이다(`_CONTENT_AWARE_VERSIONS`). 핀 샘플은 노드가 실제로
    넘긴 Evidence이고 그중 위치를 든 것(fake semgrep)이 있어야 의미가 있다.
    """
    items, evidences = _auditor_evidence_facts(
        ctx, "v1", "SANITIZER heap-buffer-overflow at parse_header"
    )

    assert any(ev.location is not None for ev in evidences), "전제: 위치 든 Evidence가 있어야 한다"
    assert all(set(item) == {"evidence_id", "kind", "ok"} for item in items)


# 판 목록은 노드의 상수에서 읽는다(위 Critic 검사와 같은 이유). `v4_t1`도 든다 — 손으로 쓴 옛 목록이
# 빠뜨렸던 판이고 dev_fake 렌더에서 돈다. 제외한 판 없음.
@pytest.mark.parametrize("version", sorted(auditor._CONTENT_AWARE_VERSIONS))
@pytest.mark.parametrize("payload", ["SANITIZER heap-buffer-overflow at parse_header", None])
def test_auditor_content_aware_facts_carry_the_evidence_location(
    ctx: RunContext, version: str, payload: str | None
) -> None:
    """content-aware 판은 Evidence마다 `location`을 싣는다 — payload가 없어도(리뷰 #480 LOW-1).

    위치는 판단 보조이고 게이트 등급 근거가 아니다(`build_facts` 주석). 위치 없는 Evidence(그래프)는
    `null`이다. 본문은 payload가 있을 때만 실린다.
    """
    items, evidences = _auditor_evidence_facts(ctx, version, payload)

    assert any(ev.location is not None for ev in evidences), "전제: 위치 든 Evidence가 있어야 한다"
    by_id = {item["evidence_id"]: item for item in items}
    for ev in evidences:
        expected = ev.location.model_dump(mode="json") if ev.location else None
        assert by_id[ev.evidence_id]["location"] == expected
        assert ("body" in by_id[ev.evidence_id]) is (payload is not None)


def test_auditor_includes_evidence_body_when_version_is_content_aware(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """content-aware 버전에서는 payload 본문이 실제로 FACTS에 실린다."""
    monkeypatch.setattr(auditor, "_CONTENT_AWARE_VERSIONS", frozenset({"v1"}))  # 게이트를 켠다
    ctx.read_payload = lambda _ref: "SANITIZER heap-buffer-overflow at parse_header"
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)

    auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    prompt = ctx.models.last_for(Role.AUDITOR)
    assert "heap-buffer-overflow" in prompt.body  # 본문이 FACTS에 닿는다


def test_auditor_content_aware_versions_pin_the_exact_set() -> None:
    """critic 쪽 #247 리뷰 HIGH-1과 같은 이유 — 위 테스트는 monkeypatch로 게이트를 켜므로
    **실제 값**을 안 문다. `_CONTENT_AWARE_VERSIONS`에서 버전을 빼면 그 버전이 조용히
    metadata만 받는다(#328 리뷰 MEDIUM-1 — v4 prefix가 evidence content를 전제로 쓰였다).
    **critic의 상수를 import하지 않는다** — 본문을 읽도록 쓰였는지는 역할별 프롬프트의 성질이다.

    ⚠ **부분집합(`<=`)이 아니라 상등이다.** 부분집합이면 든 판만 묻고 안 든 판은 안 묻는다 —
    2026-10-03 v6를 더할 때 이 단언이 그대로 통과한 이유가 그것이고, 그때 함수 이름도
    `..._pin_v3_v4_and_v5`로 남아 `v4_t1`·`v6` 둘을 빼먹은 채 거짓이 됐다(#418·#421 리뷰).
    판을 더하거나 빼면 여기도 같이 고친다 — 그 강제가 이 핀의 목적이다.

    **서빙 판이 이 집합에 드는가**는 판을 세지 않는 별 핀이 묻는다 — `test_config.py`의
    `test_the_canonical_auditor_version_receives_evidence_bodies`.
    """
    assert auditor._CONTENT_AWARE_VERSIONS == frozenset(
        {"v3", "v4", "v4_t1", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12"}
        | {"v13", "v14", "v15"}
    )
    assert auditor._CONTENT_AWARE_VERSIONS is not critic._CONTENT_AWARE_VERSIONS


@pytest.mark.parametrize("version", ["v3", "v4", "v5", "v6", "v7"])
def test_auditor_content_aware_version_renders_slice_source_and_evidence_body_through_the_node(
    ctx: RunContext, version: str
) -> None:
    """registry가 auditor에 v3·v4·v5·v6·v7을 고르면 **노드를 거친** 렌더가 body를 채운다 —
    슬라이스 렌더가 `SOURCE`로, payload 본문이 FACTS `evidence[].body`로 간다.

    위 셋은 게이트(상수)와 v1만 본다. 이 검사는 **버전 선택 → 본문 읽기 → 렌더**를 한 호출로
    묶는다 — 상수에서 버전을 빼거나 노드가 버전을 안 보면 본문이 빠져 빨개진다. fake 엔트리는
    `test_pipeline_e2e._poolify_v3`처럼 `fake`를 복사해 버전만 바꾼다(커밋된 fake 키는 v1·v2뿐).
    """
    fake_entry = ctx.config.models["fake"].transition(prompt_template_version=version)
    ctx.config = ctx.config.transition(
        models={**ctx.config.models, "fake_content_aware": fake_entry},
        profile=ctx.config.profile.transition(
            roles={**ctx.config.profile.roles, "auditor": "fake_content_aware"}
        ),
    )
    ctx.read_payload = lambda _ref: "SANITIZER heap-buffer-overflow at parse_header"
    render = "/* ===== parser.c ===== */\n4|     char buf[64];\n5|     memcpy(buf, src, len);"
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)

    auditor.run(
        ctx, candidate=candidate, capsule=capsule, evidences=evidences, source=lambda: render
    )

    prompt = ctx.models.last_for(Role.AUDITOR)
    assert prompt.prompt_version == version
    assert "{{" not in prompt.body
    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", prompt.body, re.DOTALL)[-1])
    assert facts["source"] == render
    bodies = [ev["body"] for ev in facts["evidence"] if "body" in ev]
    assert any("heap-buffer-overflow" in body for body in bodies)


@pytest.mark.parametrize("version", ["v4", "v5"])
def test_critic_prompt_is_content_conditional(version: str) -> None:
    """#328 2차 HIGH: v4가 본문을 받으면서 metadata-only라고 단언하지 않는다."""
    prefix = (PROMPTS_DIR / "critic" / version / "prefix.md").read_text(encoding="utf-8")
    assert "current wiring is metadata-only" not in prefix
    assert "does not receive `evidence_summaries`" not in prefix
    assert "contain no body text" in prefix


@pytest.mark.parametrize("role", ["auditor", "critic", "wired_critic"])
def test_v5_evidence_completeness_wording(role: str) -> None:
    """#346 M1/M2: v5를 v4 텍스트로 되돌리면 실패하는 철자 핀이지 모델 행동 검증은 아니다.

    출처: prompts/{auditor,critic}/v5/prefix.md의 절단 계약 문단. 의미가 다른 문장을 덧붙이는
    변이는 못 잡는다. 실제 모델의 근거 사용·과보류는 별도 측정 대상이며
    배선된 v6·v7 활성화 전에 그 측정을 했다는 기록은 없다(agent-contracts §4).
    v7(2026-10-14 서빙)은 v5의 규칙 문장을 다시 써서 절단 규칙·rationale 인용·반박 ID 셋의
    철자가 다르다 - 그 셋만 판별로 가른다.
    """
    version = load_config("harness_pool").model_for("critic").prompt_template_version \
        if role == "wired_critic" else "v5"
    role = "critic" if role == "wired_critic" else role
    is_v7 = role == "critic" and version == "v7"
    prefix = (PROMPTS_DIR / role / version / "prefix.md").read_text(encoding="utf-8")
    normalized = " ".join(prefix.split())
    assert "factual summary" not in normalized
    for text in (
        "not a generated summary",
        "`truncated` is false",
        "line containing only `...`",
        r"`\n...\n`",
        "stop mid-line",
        "not adjacent",
        "without that marker",
        "may also occur in the original log",
        "not a clean result",
    ):
        assert text in normalized
    assert ("Truncation alone is not a reason for `need_evidence`" if is_v7
            else "truncation alone is not a reason") in normalized
    if role == "critic":
        assert "`[truncated]`" in prefix
        assert "For a non-empty body with `truncated: true`" in normalized
        assert "Empty bodies have no snippet or suffix" in normalized
        if is_v7:
            assert "retained body text that shows the fault in `rationale`" in normalized
            assert "Set rebuttal_axis=[] and evidence_refs=[]" in normalized
        else:
            assert "supporting retained text in `rationale`" in normalized
            assert "Set rebuttal_axis=[] and use non-empty evidence_refs" in normalized


@pytest.mark.parametrize("role", ["auditor", "critic", "wired_critic"])
def test_v5_cross_stage_input_boundaries_wording(role: str) -> None:
    """러너·노드의 별도 호출 경계를 명시하는 문구 핀; 실제 모델 행동 검증은 아니다."""
    version = load_config("harness_pool").model_for("critic").prompt_template_version \
        if role == "wired_critic" else "v5"
    role = "critic" if role == "wired_critic" else role
    prefix = (PROMPTS_DIR / role / version / "prefix.md").read_text(encoding="utf-8")
    normalized = " ".join(prefix.split())
    if role == "auditor":
        assert "does not receive Scout's `analysis_summary`" in normalized
        assert "does not receive the slice criterion" in normalized
        assert "no Scout-style retrieval loop" in normalized
        assert "A reproduction plan is not an execution result" in normalized
    else:
        assert "separate review call" in normalized
        assert "one record, not independent corroboration" in normalized
        assert "does not automatically retrieve missing material and retry" in normalized


@pytest.mark.parametrize("role", ["auditor", "critic", "wired_critic"])
def test_v5_partial_body_rules_keep_their_direction_wording(role: str) -> None:
    """#346 M2 잔여: 부분 본문 규칙 2·3과 Critic §1 범위 문단의 판정 **방향** 문장 철자 핀.

    위 v5 핀은 절단 안내 문장만 묻는다 — 규칙 3의 「Otherwise decide」를 「Otherwise defer」로
    바꾸는 한 단어 변이도 그 핀들로는 안 잡힌다. 여기서는 규칙 2·3과 Critic §1 범위 문단을
    각각 **연속 구 하나**로 묶어 전제절(「requires an unshown fact …」)·갈래·문장 순서까지 묻는다.
    v7(2026-10-14 서빙)은 규칙 3과 §1 범위 문단을 다시 써서 규칙 2와 step 5의 `- need_evidence is
    required when …` 불릿(Check A·절단·가정의 guard/caller 문장)만 문다.
    철자만 묻는다: 연속 구 **밖에** 반대 문장을 덧붙이는 변이(예: 규칙 3 뒤에 「a truncated
    body always requires …」)와 구를 통째로 파일의 다른 자리로 옮기는 변이는 못 잡고, 규칙 1은
    안 묻는다. 모델이 그 방향을 따르는지도 묻지 않는다(고정 표본 회귀의 몫 — agent-contracts
    §4 규칙 5).
    """
    version = load_config("harness_pool").model_for("critic").prompt_template_version \
        if role == "wired_critic" else "v5"
    role = "critic" if role == "wired_critic" else role
    is_v7 = role == "critic" and version == "v7"
    prefix = (PROMPTS_DIR / role / version / "prefix.md").read_text(encoding="utf-8")
    normalized = " ".join(prefix.split())
    common = (  # 규칙 2 전체 — auditor/v5/prefix.md:114-115 · critic/v5/prefix.md:70-71
        "What a partial body does not show is unknown, not absent: a missing guard, caller, "
        "connection, or result does not prove it is absent from the full evidence.",
    )
    per_role = {
        "auditor": (  # 규칙 3 전체(전제절·ID 갈래·Otherwise) — auditor/v5/prefix.md:116-118
            "If deciding the candidate requires an unshown fact and SOURCE and FACTS do not "
            "establish it, use the issued missing-evidence ID; use `reachability_unknown` for a "
            "missing reachability fact or path connection. Otherwise decide; truncation alone is "
            "not a reason to defer",
        ),
        "critic": (  # 규칙 3 전체 — critic/v5/prefix.md:72-75 · §1 범위 문단 전체 :96-99
            "If the verdict requires an unshown fact and no other supplied evidence content "
            "establishes it, return `need_evidence` naming that fact. CLAIM and PATH identify the "
            "claim to review, not independent support for it. Otherwise apply the verdict rules "
            "below; truncation alone is not a reason for `need_evidence`",
            "Apply these conditions to the decisive facts required for this review, not every "
            "conceivable guard or caller. Truncation or a merely hypothetical hidden guard alone "
            "does not establish a missing decisive fact. Positive support must still come from "
            "retained evidence content; if a specific unresolved fact could change the verdict, "
            "use `need_evidence`",
        ),
    }
    if is_v7:  # v7은 증거 규칙 3(`prefix.md:54` 근처)을 다시 썼다 -
        # 대신 step 5 판정 규칙 불릿 중 `need_evidence` 불릿
        per_role["critic"] = (
            "need_evidence is required when the path is not reviewable (step 3) or the sink "
            "cannot be confirmed in SOURCE (Check A). Truncation that does not hide the deciding "
            "code, a hypothetical unseen guard, or a hypothetical unseen caller is not enough.",
        )
    for text in (*common, *per_role[role]):
        assert text in normalized, text


@pytest.mark.parametrize(
    "version", ["v5", load_config("harness_pool").model_for("critic").prompt_template_version]
)
def test_critic_v5_need_evidence_conditions_are_decisive_fact_conditional(version: str) -> None:
    """#346 sumin0218 M1: 기존 보류 규칙에 판정을 좌우하는 누락 조건이 붙어 있다.

    출처: critic/v5/prefix.md Review procedure §1·§3. 이 핀은 이 문구의 회귀를 잡을 뿐
    모든 의미 반전이나 실제 모델의 규칙 우선순위 준수를 검증하지 않는다.
    """
    prefix = (PROMPTS_DIR / "critic" / version / "prefix.md").read_text(encoding="utf-8")
    normalized = " ".join(prefix.split())
    if version == "v7":  # v7(2026-10-14 서빙)은 이 조건을 SOURCE 검증 문장으로 다시 썼다
        assert "need_evidence is required when the path is not reviewable (step 3)" in normalized
        assert "a hypothetical unseen guard, or a hypothetical unseen caller is not enough" in (
            normalized
        )
    else:
        assert "cannot be checked from supplied content and the verdict depends on" in normalized
        condition = "unresolved fact that could change the verdict and required material is absent"
        assert condition in normalized
        assert "Mere truncation or a hypothetical unseen guard is not enough" in normalized
    assert "need_evidence is required when a rebuttal is plausible" not in normalized


@pytest.mark.parametrize(
    "version", ["v5", load_config("harness_pool").model_for("critic").prompt_template_version]
)
def test_critic_v5_role_summary_states_the_decisive_fact_condition(version: str) -> None:
    """#346 sumin0218 M1 잔여: 「세 결과」 요약도 보류를 판정을 좌우하는 반박으로 한정한다.

    v4에서 상속된 무한정 문구 「the path or a possible rebuttal cannot be decided」는 잘린
    본문에서 숨은 guard가 늘 「possible」하므로 §1·§3과 규칙 3(「truncation alone is not a
    reason」)의 반대로 읽힌다. 출처: critic/v5/prefix.md 「The three outcomes are distinct」 목록.
    철자 핀이다 — 줄 전체(꼬리 「because required material is absent」까지)를 묻는다. 옛 문구가
    돌아오거나 한정·꼬리가 바뀌면 빨개지고, 이 줄을 남긴 채 무한정 조건을 다른 문장으로
    덧붙이는 변이는 못 잡는다. 모델이 어느 쪽을 따르는지는 안 쟀다.
    """
    prefix = (PROMPTS_DIR / "critic" / version / "prefix.md").read_text(encoding="utf-8")
    normalized = " ".join(prefix.split())
    assert "the path or a possible rebuttal cannot be decided" not in normalized
    summary = "need_evidence: the path, or a specific rebuttal that could change the verdict,"
    assert f"{summary} cannot be decided because required material is absent;" in normalized


@pytest.mark.parametrize(
    ("role", "fields"),
    [
        pytest.param(
            Role.AUDITOR,
            {
                "candidate": "candidate",
                "target": "target",
                "capsule_sections": "sections",
                "source": "source",
            },
            id="auditor",
        ),
        pytest.param(
            Role.CRITIC,
            {"claim": "claim", "path": "path", "evidence": "evidence"},
            id="critic",
        ),
    ],
)
@pytest.mark.parametrize("version", ["v4", "v5"])
def test_fact_blocks_follow_the_fake_parser_contract(
    role: Role, fields: dict[str, str], version: str
) -> None:
    """#328 2차 MEDIUM-2: 렌더된 마지막 json 펜스를 Fake 경로가 실제로 읽는다."""
    facts = build_facts(run_id="run_prompt_contract", ids={})
    rendered = load_prompt(role, version).render(facts=facts, **fields)

    assert _parse_context(rendered.body).run_id == "run_prompt_contract"


def test_unreadable_critic_verdict_is_held_and_recorded(ctx: RunContext) -> None:
    """못 읽은 verdict는 `need_evidence`(보류)로 가고 **원문이 trace에 남는다**.

    **Fake는 이 분기를 못 탄다** — 표준 어휘만 내므로 항등 경로만 돈다(#182 재리뷰 M-5).
    그래서 실서빙에서만 나는 자유 서술 verdict를 여기서 모델 응답에 끼워 넣는다.

    이 이벤트가 없으면 trace.py 지표표의 「제거율 — Critic」 분자(`rebutted ∪
    need_evidence`)가 **진짜 보류와 못 읽은 verdict를 한 값으로 합친다** — 전자는 Critic의
    판정이고 후자는 우리 매퍼의 실패라 같은 수에 들어가면 안 된다.
    """
    raw = "I cannot rule out a use-after-free here"
    inner = ctx.models.complete

    def unreadable(*, role, prompt, schema, history=()):  # noqa: ANN001, ANN202
        result = inner(role=role, prompt=prompt, schema=schema, history=history)
        if role is not Role.CRITIC:
            return result
        return result.model_copy(update={"value": result.value.model_copy(update={"verdict": raw})})

    ctx.models.complete = unreadable  # type: ignore[method-assign]
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    finding, missings = auditor.run(ctx, candidate=candidate, capsule=capsule, evidences=evidences)

    _, decision = critic.run(ctx, finding=finding, evidences=evidences, missings=tuple(missings))

    assert decision.verdict is Verdict.NEED_EVIDENCE  # 지지(uphold)로 소비하지 않는다
    unmapped = [e for e in ctx.trace.events if e.kind is EventKind.CRITIC_VERDICT_UNMAPPED]
    assert len(unmapped) == 1
    assert unmapped[0].data["raw_verdict"] == raw
    assert unmapped[0].data["mapped_verdict"] == Verdict.NEED_EVIDENCE.value
    assert unmapped[0].subject_id == decision.decision_id


def test_critic_selection_is_capped_by_config(ctx: RunContext) -> None:
    findings = [_finding(f"find_{i:04d}", confidence=0.5) for i in range(20)]

    selected = critic.select(ctx, findings)

    assert len(selected) == ctx.config.thresholds.critic_top_n
    assert [f.finding_id for f in selected] == sorted(f.finding_id for f in selected)


def test_critic_truncation_leaves_one_event_per_cut_finding(ctx: RunContext) -> None:
    """`critic_top_n` 절단이 trace에 남는다 (D26 (b)).

    실측 `run_20260825_0001`은 승격 44 · 선별 8 · **절단 36**인데 그 36건이 `transition`
    reason 목록에도 trace 어느 이벤트에도 없었다 — 슬라이스가 이벤트를 안 남겨서다.

    state를 섞는 것은 `has_repro_spec`이 실제로 갈리는지 보기 위해서다. `_finding`이
    `repro_spec`을 state에 묶어 두어 HYPOTHESIZED만으로는 그 필드가 **항상 False**이고,
    그러면 필드가 죽어 있어도 초록불이 난다.
    """
    states = (FindingState.HYPOTHESIZED, FindingState.VALIDATED)
    findings = [_finding(f"find_{i:04d}", confidence=0.5, state=states[i % 2]) for i in range(20)]
    limit = ctx.config.thresholds.critic_top_n

    selected = critic.select(ctx, findings)
    skipped = [e for e in ctx.trace.events if e.kind is EventKind.CRITIC_SKIPPED]

    assert len(skipped) == len(findings) - limit
    # 수만 세면 엉뚱한 건이 잘려도 초록불이다. 잘린 집합이 선별의 여집합이어야 한다.
    assert {e.subject_id for e in skipped} == {f.finding_id for f in findings} - {
        f.finding_id for f in selected
    }
    assert {e.stage for e in skipped} == {int(Stage.CRITIC)}
    assert {e.data["reason"] for e in skipped} == {"over_critic_top_n"}
    # 선별 키와 8단계 진입 조건이 함께 남아야 D26 (a)를 trace만으로 판정할 수 있다.
    by_id = {f.finding_id: f for f in findings}
    assert all(e.data["confidence"] == by_id[e.subject_id].confidence for e in skipped)
    assert {e.data["has_repro_spec"] for e in skipped} == {True, False}


def test_critic_selection_within_the_cap_records_nothing(ctx: RunContext) -> None:
    """이벤트 부재가 곧 "안 잘렸다"이다 — `retrieval_truncated`와 같은 계약."""
    findings = [_finding(f"find_{i:04d}") for i in range(ctx.config.thresholds.critic_top_n)]

    critic.select(ctx, findings)

    assert not [e for e in ctx.trace.events if e.kind is EventKind.CRITIC_SKIPPED]


# --- 8~9단계 ----------------------------------------------------------------


def test_expected_signal_picks_the_tool() -> None:
    assert validate.tool_for("AddressSanitizer: heap-buffer-overflow") == "asan"
    assert validate.tool_for("poc가 종료 코드 139로 죽는다") == "poc"
    assert validate.tool_for("로그에 WARN이 찍힌다") is None


def test_unmappable_signal_is_a_tool_policy_violation(ctx: RunContext) -> None:
    """실행할 수 없는 명세를 억지로 돌려 '재현 실패'로 세지 않는다."""
    finding = _finding("find_0001", state=FindingState.VALIDATED, signal="로그에 WARN이 찍힌다")

    after, evidences = validate.run(ctx, finding=finding)

    assert evidences == []
    assert after.state is FindingState.QUEUED
    assert not [e for e in ctx.trace.events if e.kind is EventKind.TOOL_CALL]
    queued = next(e for e in ctx.trace.events if e.kind is EventKind.TRANSITION)
    assert queued.data["reason"] == ReasonCode.TOOL_POLICY_VIOLATION.value


def test_successful_repro_attaches_evidence_and_proves(ctx: RunContext) -> None:
    finding = _finding("find_0001", state=FindingState.VALIDATED)

    after, evidences = validate.run(ctx, finding=finding, workdir="parser.c")

    assert after.state is FindingState.PROVEN
    assert {ev.evidence_id for ev in evidences} <= set(after.evidence_refs)
    assert any(ev.kind is EvidenceKind.SANITIZER for ev in evidences)


class _CallRecorder:
    """`ToolRunner` 대역 — 불린 요청을 기록하고 실제 러너에 그대로 위임한다."""

    def __init__(self, inner: ToolRunner) -> None:
        self._inner = inner
        self.calls: list[tuple[str, ToolArgs]] = []

    def run(self, tool: str, args: ToolArgs, sandbox: SandboxSpec) -> Evidence:
        self.calls.append((tool, args))
        return self._inner.run(tool, args, sandbox)


def test_a_step_equal_to_the_input_placeholder_is_refused_not_fatal(ctx: RunContext) -> None:
    """🔴 모델이 쓴 `repro_spec.steps`는 argv에 그대로 들어간다(`validate._call`). 원소가 예약
    자리표와 **같으면** `ToolArgs`가 `ValidationError`를 던졌고 그 루프에는 try가 없어 run 전체가
    죽었다(독립 검토가 `stopped_after=8`로 실증). 이제는 도구에 가기 전에 `ok=False` Evidence로
    남긴다.

    ⚠ **현재 동작을 고정한다**: `ok=False`라 `repro_failed`(F-11, `queued`)로 간다 — 실행할 수 없는
    명세를 `tool_policy_violation`(F-12)으로 가르는 갈래(위 `test_unmappable_signal_…`)와 다르다.
    어느 쪽이 옳은지는 이 검사가 정하지 않는다."""
    finding = _finding("find_0001", state=FindingState.VALIDATED)
    assert finding.repro_spec is not None
    finding = finding.transition(
        repro_spec=ReproSpec(
            steps=["./build.sh", INPUT_PLACEHOLDER],
            expected_signal=finding.repro_spec.expected_signal,
        )
    )
    recorder = _CallRecorder(ctx.tools)
    refusing = dataclasses.replace(ctx, tools=recorder)

    after, evidences = validate.run(refusing, finding=finding, workdir="parser.c")

    assert recorder.calls == [], "거절한 요청이 도구까지 갔다"
    assert [(ev.source, ev.kind) for ev in evidences] == [
        ("build", EvidenceKind.BUILD),
        ("asan", EvidenceKind.SANITIZER),
    ]
    for ev in evidences:
        assert (ev.ok, ev.exit_code) == (False, None), "안 돈 것을 돈 것처럼 적었다"
        assert INPUT_PLACEHOLDER in (ev.error or "") and "실행하지 않았다" in (ev.error or "")
        assert refusing.store.get("evidence", ev.evidence_id) == ev
    assert {ev.evidence_id for ev in evidences} <= set(after.evidence_refs)
    calls = [e for e in refusing.trace.events if e.kind is EventKind.TOOL_CALL]
    assert [(e.data["tool"], e.data["ok"]) for e in calls] == [("build", False), ("asan", False)]
    keys = {"tool", "argv", "ok", "exit_code", "error", "finding_id"}
    assert all(set(e.data) == keys for e in calls), "거절의 tool_call이 실행의 키 집합과 다르다"
    assert after.state is FindingState.QUEUED
    transition = next(e for e in refusing.trace.events if e.kind is EventKind.TRANSITION)
    assert transition.data["reason"] == ReasonCode.REPRO_FAILED.value


# --- 8단계가 측정하지 못한 것은 재현 실패가 아니다 (#326) -------------------------------------
#
# 컨테이너 러너의 `asan`은 우리 clang으로 대상을 컴파일·링크하는데 CyberGym 번들은 그것이 안 된다
# (#507). 그 `ok=False`를 F-11(`repro_failed`)로 세면 Critic을 통과한 Finding이 `queued`로 내려가고
# 「제거율 — Tool」의 분자가 하네스 한계로 부푼다.


class _AsanFails:
    """`asan` 요청에 주어진 `ok=False` 결과를 돌려주는 도구 대역 - 다른 도구는 위임한다."""

    def __init__(self, inner: ToolRunner, exit_code: int | None, error: str) -> None:
        self.exit_code = exit_code
        self.error = error
        self._inner = inner

    def run(self, tool: str, args: ToolArgs, sandbox: SandboxSpec) -> Evidence:
        evidence = self._inner.run(tool, args, sandbox)
        if tool == "asan":
            return evidence.model_copy(
                update={"ok": False, "exit_code": self.exit_code, "error": self.error}
            )
        return evidence


def _asan_cannot_build(inner: ToolRunner) -> _AsanFails:
    """`asan` 요청에 **어댑터가 실제로 내는 문구**로 `ok=False`를 돌려준다 -
    `container_tools.judge()`가 링크 단계에서 끊긴 대상에 붙이는 것이다. 문구를 손으로 안 쓴다."""
    from src.adapters.container_tools import Step, judge

    ok, exit_code, error = judge("asan", [Step(1, "", "ld.lld: error: undefined symbol: main")], 2)
    assert not ok and error is not None, "픽스처 전제 - 어댑터가 ok=False를 낸다"
    return _AsanFails(inner, exit_code, error)


def _asan_times_out(inner: ToolRunner) -> _AsanFails:
    """`container_tools._execute`의 시간 초과 Evidence와 같은 모양(`124` + `timeout after …`)."""
    return _AsanFails(inner, validate.TIMEOUT_EXIT, "timeout after 60s")


def _validated_with_capsule(ctx: RunContext, signal: str) -> tuple[runner._Accumulator, Finding]:
    _, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding = _finding("find_0001", state=FindingState.VALIDATED, signal=signal)
    state = runner._Accumulator()
    state.update(finding)
    state.finding_capsules[finding.finding_id] = capsule
    return state, finding


def _not_measured_events(ctx: RunContext) -> list:
    return [e for e in ctx.trace.events if e.kind is EventKind.REPRO_NOT_MEASURED]


def test_a_stage_8_compile_failure_holds_the_finding_and_pov_still_targets_it(
    ctx: RunContext,
) -> None:
    """asan이 컴파일·링크 단계에서 끊기면 Finding은 `validated`에 남고(`queued`가 아니다) 전이도
    `policy_decision`도 없다. PoV는 8단계 진입 시점의 `validated` 목록으로 대상을 잡으므로(상태가
    내려가도 도는 것은 `test_pov_also_runs_for_a_finding_that_validate_demotes_to_queued`가 문다)
    풀 소속은 그대로다 - **이 검사가 바꾸는 것은 상태·지표·보고서다.**
    Evidence는 `ok=False`와 사유로 남고 게이트는 그 근거로 등급을 낸다(p4는 거짓)."""
    state, finding = _validated_with_capsule(ctx, "AddressSanitizer: heap-buffer-overflow")
    failing = _asan_cannot_build(ctx.tools)
    broken = dataclasses.replace(ctx, tools=failing)

    runner._analyze(broken, [], state, int(Stage.VALIDATE))

    after = state.findings[finding.finding_id]
    assert after.state is FindingState.VALIDATED
    stage_8_transitions = [
        e
        for e in broken.trace.events
        if e.kind is EventKind.TRANSITION and e.stage == int(Stage.VALIDATE)
    ]
    assert stage_8_transitions == [], "전이가 났다"
    assert not [
        e
        for e in broken.trace.events
        if e.kind is EventKind.POLICY_DECISION and e.stage == int(Stage.VALIDATE)
    ]
    (event,) = _not_measured_events(broken)
    assert (event.subject_id, event.stage) == (finding.finding_id, int(Stage.VALIDATE))
    assert event.data == {
        "tool": "asan",
        "reason": validate.NOT_MEASURED_TOOL,
        "exit_code": failing.exit_code,
        "error": failing.error,
    }
    stored = [ev for ev in state.evidences[finding.finding_id] if ev.source == "asan"]
    assert [(ev.ok, ev.error) for ev in stored] == [(False, failing.error)]
    assert {ev.evidence_id for ev in stored} <= set(after.evidence_refs)
    (graded,) = [
        e
        for e in broken.trace.events
        if e.kind is EventKind.GATE_EVAL and e.stage == int(Stage.VALIDATE)
    ]
    assert graded.data["p4"] is False
    assert _pov_subjects(broken) == [finding.finding_id], "PoV 풀에서 빠졌다"


def test_a_real_crash_still_routes_and_is_not_reported_as_unmeasured(ctx: RunContext) -> None:
    """음성 쌍 - 대상이 떠서 비-0으로 끝난 건(`ok=True`, 크래시는 신호)은 종전대로 `proven`이다.
    `_not_measured`가 `ok=False`만 보는지 문다: 종료 코드로 갈랐다면 여기서 빨개진다."""
    finding = _finding("find_0001", state=FindingState.VALIDATED)

    after, evidences = validate.run(ctx, finding=finding, workdir="parser.c")

    assert any(ev.ok and ev.exit_code not in (None, 0) for ev in evidences), "픽스처 전제"
    assert after.state is FindingState.PROVEN
    assert _not_measured_events(ctx) == []


def test_a_refused_step_is_still_a_failed_repro_not_an_unmeasured_one(ctx: RunContext) -> None:
    """모델이 쓴 `steps`가 예약 자리표와 같아 우리가 요청을 안 보낸 건(`exit_code=None`)은 명세의
    실패라 F-11 그대로다 - 위 `test_a_step_equal_to_the_input_placeholder_…`가 상태까지 묻고, 여기는
    그 건이 `repro_not_measured`로 새지 않는 것만 문다(종료 코드가 있는 `ok=False`와의 경계)."""
    finding = _finding("find_0001", state=FindingState.VALIDATED)
    assert finding.repro_spec is not None
    finding = finding.transition(
        repro_spec=ReproSpec(
            steps=[INPUT_PLACEHOLDER], expected_signal=finding.repro_spec.expected_signal
        )
    )

    after, _ = validate.run(ctx, finding=finding, workdir="parser.c")

    assert after.state is FindingState.QUEUED
    assert _not_measured_events(ctx) == []


def test_a_poc_signal_is_not_run_here_and_holds_the_finding(ctx: RunContext) -> None:
    """`poc` 신호(ADR 0014 - 서빙 점수 경로는 PoV 루프의 `poc`)는 이 노드가 **돌리지 않는다**: 도구
    호출 0건 - 입력 바이트를 실을 길이 없어 항상 `ok=False`로 거절당하던 갈래다. `queued`가 아니라
    `validated`에 두고 사유를 남긴다."""
    finding = _finding(
        "find_0001", state=FindingState.VALIDATED, signal="poc가 종료 코드 139로 죽는다"
    )
    assert finding.repro_spec is not None
    assert validate.tool_for(finding.repro_spec.expected_signal) == validate.POC_TOOL
    recorder = _CallRecorder(ctx.tools)
    watching = dataclasses.replace(ctx, tools=recorder)

    after, evidences = validate.run(watching, finding=finding, workdir="parser.c")

    assert recorder.calls == [] and evidences == []
    assert after.state is FindingState.VALIDATED
    assert not [e for e in watching.trace.events if e.kind is EventKind.TRANSITION]
    (event,) = _not_measured_events(watching)
    assert event.data == {
        "tool": "poc",
        "reason": validate.NOT_MEASURED_POC,
        "exit_code": None,
        "error": None,
    }


def _hold_with(ctx: RunContext, tools: ToolRunner, number: int, signal: str) -> None:
    validate.run(
        dataclasses.replace(ctx, tools=tools),
        finding=_finding(f"find_{number:04d}", state=FindingState.VALIDATED, signal=signal),
        workdir="parser.c",
    )


def test_the_run_warning_is_absent_without_events_and_counts_by_cause(ctx: RunContext) -> None:
    """`repro_not_measured`는 보고서에 안 보이므로(상태가 그대로다) run 경고로 올린다. 이벤트가
    없으면 `None`. 시간 초과(124)와 컴파일 실패는 **한 숫자로 합치지 않고** 종료 코드별로 센다."""
    assert runner._repro_not_measured_warning(ctx.trace, has_pov_seat=True) is None
    asan = "AddressSanitizer: overflow"
    _hold_with(ctx, _asan_times_out(ctx.tools), 1, asan)
    _hold_with(ctx, _asan_times_out(ctx.tools), 2, asan)
    _hold_with(ctx, _asan_cannot_build(ctx.tools), 3, asan)

    warning = runner._repro_not_measured_warning(ctx.trace, has_pov_seat=True)

    assert warning is not None
    assert "3건" in warning
    assert "124(시간 초과) 2건" in warning
    assert "1 1건" in warning, "컴파일 실패(종료 코드 1)가 시간 초과와 한 숫자로 합쳐졌다"
    assert "poc 신호" not in warning, "poc 신호가 없는 run인데 poc 문장이 있다"


def test_the_poc_sentence_depends_on_whether_the_profile_has_a_pov_seat(ctx: RunContext) -> None:
    """8단계는 `poc`를 실행하지 않는다. 그 뒤는 프로필이 가른다 - pov 좌석이 있으면 PoV 풀 여부는
    PoV 단계 이벤트가 들고 **없으면 어디서도 안 돈다.** 경고가 좌석이 없을 때 「PoV 루프」를 말하면
    거짓이다."""
    _hold_with(ctx, ctx.tools, 1, "poc가 종료 코드 139로 죽는다")

    seated = runner._repro_not_measured_warning(ctx.trace, has_pov_seat=True)
    seatless = runner._repro_not_measured_warning(ctx.trace, has_pov_seat=False)

    assert seated is not None and seatless is not None
    assert "PoV 단계 이벤트" in seated and "pov 좌석이 없어" not in seated
    assert "pov 좌석이 없어 어디서도 실행되지 않았다" in seatless
    assert "PoV 루프" not in seatless


def test_the_timeout_exit_code_mirrors_the_adapter() -> None:
    """`validate.TIMEOUT_EXIT`는 어댑터 상수의 사본이다(노드는 어댑터를 못 import한다) - 경고가 이
    값으로 시간 초과를 가른다."""
    from src.adapters import container_tools

    assert validate.TIMEOUT_EXIT == container_tools._TIMEOUT_EXIT


def test_a_timeout_holds_the_finding_like_a_compile_failure(ctx: RunContext) -> None:
    """시간 초과(`ok=False`, 124)도 측정을 못 낸 건이다 - 강등하지 않고 `validated`에 두며 사유와
    종료 코드를 trace에 남긴다. 옛 동작(F-11 강등)이 돌아오면 여기서 빨개진다."""
    finding = _finding("find_0001", state=FindingState.VALIDATED, signal="AddressSanitizer: x")

    after, _ = validate.run(
        dataclasses.replace(ctx, tools=_asan_times_out(ctx.tools)),
        finding=finding,
        workdir="parser.c",
    )

    assert after.state is FindingState.VALIDATED
    (event,) = _not_measured_events(ctx)
    assert (event.data["reason"], event.data["exit_code"]) == (
        validate.NOT_MEASURED_TOOL,
        validate.TIMEOUT_EXIT,
    )


def test_a_judge_with_no_commands_invents_no_exit_code_so_it_is_not_held(ctx: RunContext) -> None:
    """`judge()`의 빈 명령 갈래는 도달 불가 방어 갈래다(`plan()`은 명령을 늘 하나 이상 낸다) -
    그래도 종료 코드를 지어내면(127) `_not_measured`가 그것을 「대상이 안 떴다」로 읽어 보류한다.
    돌려줄 코드가 없으므로 `None`이어야 하고, 그 건은 F-11이다. 127로 되돌리면 두 단언이 같이
    빨개진다."""
    from src.adapters.container_tools import judge

    ok, exit_code, error = judge("asan", [], 2)
    assert (ok, exit_code) == (False, None) and error is not None
    finding = _finding("find_0001", state=FindingState.VALIDATED, signal="AddressSanitizer: x")

    after, _ = validate.run(
        dataclasses.replace(ctx, tools=_AsanFails(ctx.tools, exit_code, error)),
        finding=finding,
        workdir="parser.c",
    )

    assert after.state is FindingState.QUEUED
    assert _not_measured_events(ctx) == []


def test_the_adapter_still_reports_a_failed_build_as_not_ok_with_an_exit_code() -> None:
    """`_not_measured`는 어댑터 문구가 아니라 **`ok=False` + 종료 코드 있음**을 본다. 어댑터가
    컴파일·링크 실패·대상 미기동에 그 모양을 계속 내는지 실제 `judge()`로 문다 -
    문구가 바뀌어도 안 깨지고,
    `ok=True`로 바뀌면(실패를 신호로 읽으면) 빨개진다. 시간 초과(`_execute`)와 실행 전 거절
    (`_fail`)도 같은 모양이지만 `judge()`를 안 거치므로 여기서는 묻지 않는다."""
    from src.adapters.container_tools import Step, judge

    for tool, steps, total in (
        ("asan", [Step(1, "", "x.c:1:1: error: boom")], 2),  # 빌드 단계에서 끊김
        ("asan", [Step(0, "", ""), Step(127, "", "")], 2),  # 대상이 안 떴다
        ("build", [Step(1, "", "x.c:1:1: error: boom")], 1),
    ):
        ok, code, error = judge(tool, steps, total)
        assert (ok, code is not None, bool(error)) == (False, True, True), (tool, steps)
    ok, code, _ = judge("asan", [Step(0, "", ""), Step(139, "", "ERROR: AddressSanitizer")], 2)
    assert ok and code == 139, "크래시는 신호다 - ok=True"


def test_validate_refusal_kinds_mirror_the_tool_whitelist() -> None:
    """거절 Evidence의 `kind`는 `validate`가 든 사본이다 — 어댑터는 못 import해서 복제했다
    (`prompt_loader`의 도구 이름 사본과 같은 사정). 화이트리스트가 움직이면 여기서 빨개지고, 이
    노드가 부르는 도구(`build` + `_SIGNAL_TOOLS`)가 사본에서 빠지면 `_refuse`가 BUILD로 조용히
    떨어지므로 그것도 문다."""
    from src.adapters.fake_tools import TOOL_KINDS

    # `poc`는 신호 분류일 뿐 이 노드가 부르지 않는다(#326) - 사본에도 없다.
    called = {
        validate.BUILD_TOOL,
        *(tool for _, tool in validate._SIGNAL_TOOLS if tool != validate.POC_TOOL),
    }
    assert called == set(validate.REFUSED_KINDS), "이 노드가 부르는 도구와 사본이 갈라졌다"
    assert validate.REFUSED_KINDS == {tool: TOOL_KINDS[tool] for tool in validate.REFUSED_KINDS}


def test_validate_refusal_stamp_mirrors_both_adapter_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """거절 Evidence의 관측 시각 `_REFUSED_AT`은 어댑터 기본값의 사본이다 — 노드는 어댑터를 못
    import해서 복제했다(위 `kind` 사본과 같은 사정). 정상 run의 Evidence는 전부 그 고정 도장이라
    갈라지면 거절 Evidence만 다른 시각을 단다.

    **어댑터 둘 다 본다.** 컨테이너 러너는 런타임이 PATH에 없으면 `SandboxUnavailable`을 던지므로
    `shutil.which`를 가로채 띄운다(`tests/test_container_tools.py`의 `spy` 선례) — 한쪽만 보면 다른
    쪽 기본값이 갈라져도 초록이다. `observed_at`을 주지 않아 **기본값**이 나오게 한다."""
    from src.adapters import container_tools as ct
    from src.adapters.fake_tools import FakeToolRunner
    from src.ids import IdFactory

    fake = FakeToolRunner(ids=IdFactory("run_stamp"), run_id="run_stamp", root=Path("target"))
    monkeypatch.setattr(ct.shutil, "which", lambda name: f"/usr/bin/{name}")
    container = ct.ContainerToolRunner(
        ids=IdFactory("run_stamp"),
        run_id="run_stamp",
        root=Path("target"),
        artifacts_root=tmp_path,
        runtime="docker",
    )

    assert validate._REFUSED_AT == fake.observed_at
    assert validate._REFUSED_AT == container.observed_at


class _CleanExitTools:
    """`dev_fake`가 낸 런타임 Evidence의 종료 코드만 0으로 바꾼다.

    컨테이너 러너가 낼 수 있는 모양 — 도구가 떴고(`ok=True`) 크래시 없이 끝났다 — 을 호스트 없이
    만든다. Fake는 그 값을 상수 `1`로 채우므로(`fake_tools._run_repro`) 이 래퍼 없이는 노드
    경로에서 그 갈래를 못 탄다. **컨테이너 러너를 거친 실측이 아니다.**
    """

    def __init__(self, inner: ToolRunner) -> None:
        self._inner = inner

    def run(self, tool: str, args: ToolArgs, sandbox: SandboxSpec) -> Evidence:
        evidence = self._inner.run(tool, args, sandbox)
        if evidence.kind in (EvidenceKind.SANITIZER, EvidenceKind.POC_RUN):
            return evidence.model_copy(update={"exit_code": 0})
        return evidence


def test_a_clean_exit_holds_the_finding_at_validated_instead_of_proving_or_queueing(
    ctx: RunContext,
) -> None:
    """exit 0으로 끝난 재현은 `proven`도 `queued`도 아니다 — F-10b로 `validated`에 홀드한다 (#232).

    `proven`이 아닌 것은 크래시 0건이 재현 완료로 기록되는 것을 막는 것이고, `queued`(F-11,
    `repro_failed`)가 아닌 것은 이 변경이 「도구 검증 제거율」의 분자를 키우지 않는다는 것이다 —
    제거율 분자는 `repro_failed`다(`trace.py`). 그래서 이 건은 분모에만 든다
    (`evidence_below_proven`). evaluation.md §5.1이 그 사실을 문장으로 적는다.
    """
    finding = _finding("find_0001", state=FindingState.VALIDATED)
    clean = dataclasses.replace(ctx, tools=_CleanExitTools(ctx.tools))

    after, evidences = validate.run(clean, finding=finding, workdir="parser.c")

    assert any(
        ev.kind is EvidenceKind.SANITIZER and ev.ok and ev.exit_code == 0 for ev in evidences
    ), "픽스처 전제 — 도구는 떴고 깨끗이 끝났다"
    assert after.state is FindingState.VALIDATED
    transition = next(e for e in clean.trace.events if e.kind is EventKind.TRANSITION)
    assert transition.data["rule"] == "F-10b"
    assert transition.data["reason"] == ReasonCode.EVIDENCE_BELOW_PROVEN.value
    graded = [
        e
        for e in clean.trace.events
        if e.kind is EventKind.GATE_EVAL and e.stage == int(Stage.VALIDATE)
    ]
    assert [e.data["p4"] for e in graded] == [False]


def test_validate_evaluates_the_gate_before_deciding(ctx: RunContext) -> None:
    """8단계도 게이트를 부른다 (W3). 안 부르면 E4가 어디에도 안 남는다."""
    finding = _finding("find_0001", state=FindingState.VALIDATED)

    validate.run(ctx, finding=finding, workdir="parser.c")

    graded = [
        e
        for e in ctx.trace.events
        if e.kind is EventKind.GATE_EVAL and e.stage == int(Stage.VALIDATE)
    ]
    assert len(graded) == 1
    assert graded[0].data["p4"] is True
    assert graded[0].data["grade"] == "E4"
    # 8단계 목록에는 6단계 하네스 부착분이 섞이므로 「모델 인용 수」를 싣지 않는다(이슈 #554).
    assert "cited_by_model" not in graded[0].data


def test_gate_refuses_stages_where_its_predicates_cannot_hold(ctx: RunContext) -> None:
    """`GATE_STAGES` 밖에서 부르면 아직 성립할 수 없는 술어가 거짓으로 기록된다.

    그 행이 제거율의 분모에 섞이므로 조용히 넘기지 않는다. 이 검사가 `GATE_STAGES`를
    실제로 쓰이는 상수로 만든다 — 그전에는 src/ 참조가 0건이었다.
    """
    finding = _finding("find_0001", state=FindingState.VALIDATED)

    with pytest.raises(ValueError, match="게이트는"):
        ctx.evaluate_gate(finding, (), stage=Stage.SCOUT)


def test_report_dedupes_and_does_not_promote_to_reported(ctx: RunContext) -> None:
    proven = _finding("find_0001", state=FindingState.PROVEN)
    duplicate = _finding("find_0002", state=FindingState.PROVEN)

    result = report.run(ctx, findings=[proven, duplicate])

    assert result.duplicates == 1
    assert [f.state for f in result.findings] == [FindingState.PROVEN]  # 자동 승격 없음
    assert proven.title in result.render()


def _targeting(finding_id: str, target: str) -> Finding:
    """`fake_model`이 내는 3칸 경로(SOURCE input · PROPAGATE 대상함수 · SINK memcpy)를
    본떠, 대상 함수만 다른 Finding을 만든다 — D40이 든 gz_load/avail/… 모양."""
    return _finding(finding_id).model_copy(
        update={
            "path": [
                PathStep(step=PathStepKind.SOURCE, symbol="input"),
                PathStep(step=PathStepKind.PROPAGATE, symbol=target),
                PathStep(step=PathStepKind.SINK, symbol="memcpy"),
            ]
        }
    )


def test_dedupe_keeps_the_highest_grade_representative() -> None:
    """중복에서 등급 높은 것을 남긴다 (D35 b). 예전엔 finding_id 오름차순이라 E3가 낮은
    등급 대표에 밀려 리포트에서 사라졌다 — 실측 6건. id가 더 커도 등급이 이겨야 한다."""
    low = _finding("find_0001")  # 같은 파일·대상 함수 → 같은 dedupe 키
    high = _finding("find_0009")

    kept, dups = report.dedupe([low, high], {"find_0001": "E0", "find_0009": "E3"})

    assert dups == 1
    assert [f.finding_id for f in kept] == ["find_0009"]  # 등급이 id를 이긴다


def test_dedupe_same_grade_breaks_ties_by_finding_id() -> None:
    """동급이면 `finding_id` 오름차순으로 대표를 정한다 — 재현성 (D35 리뷰 ⑥). 입력 순서가
    흔들려도 같은 것이 남아야 리포트를 실행 간에 대조할 수 있다."""
    kept, _ = report.dedupe(
        [_finding("find_0009"), _finding("find_0001")],  # 같은 키·같은 등급, 입력 역순
        {"find_0009": "E3", "find_0001": "E3"},
    )

    assert [f.finding_id for f in kept] == ["find_0001"]  # 더 작은 id


def test_dedupe_does_not_merge_different_target_functions() -> None:
    """같은 CWE·같은 sink라도 **대상 함수(PROPAGATE)**가 다르면 다른 건이다 (D35 a). 예전
    키는 sink만 봐서 D40의 넷(gz_load·gz_avail·gz_look·gz_write → 전부 memcpy)이 한 건이 됐다.

    source는 `fake_model`에서 호출자가 없으면 `"input"` 상수로 접히므로 source로는 못 가른다 —
    가운데 PROPAGATE 칸이 대상 함수다.
    """
    targets = ["gz_load", "gz_avail", "gz_look", "gz_write"]
    findings = [_targeting(f"find_002{i}", t) for i, t in enumerate(targets, start=4)]

    kept, dups = report.dedupe(findings, {})

    assert dups == 0  # 넷 다 남는다
    assert len(kept) == 4


def test_dedupe_does_not_merge_across_files() -> None:
    """같은 CWE·대상 함수라도 파일이 다르면 다른 건이다 (D35 a). `PathStep`에 파일이 없어
    러너가 `Candidate.location.file`을 `files` 맵으로 넘긴다."""
    a, b = _finding("find_0001"), _finding("find_0002")  # 같은 (CWE, 대상 함수)

    kept, dups = report.dedupe([a, b], {}, {"find_0001": "a.c", "find_0002": "b.c"})

    assert dups == 0
    assert {f.finding_id for f in kept} == {"find_0001", "find_0002"}


def test_report_records_which_representative_each_dropped_finding_merged_into(
    ctx: RunContext,
) -> None:
    """버린 Finding마다 **무엇과 합쳐졌는지**를 남긴다 (이슈 #101 잔여). 예전엔 `중복 제거: N건`이
    전부라 실측 `run_20260827_0004`에서 사라진 `find_0005`(E2·`upheld`)를 추적할 수 없었다.

    대표는 등급 우선이다 — id가 가장 큰 E3가 E0·E2를 흡수한다. 다른 파일의 같은 키는 안 묶이고
    행도 안 남는다. 행 수는 `duplicates`와 같다.
    """
    low, mid, high = _finding("find_0001"), _finding("find_0005"), _finding("find_0009")
    elsewhere = _finding("find_0002")  # 같은 (CWE, 대상 함수)지만 다른 파일
    # 둘째 그룹에도 버려지는 쪽을 둔다 — 없으면 대표를 그 키가 아닌 곳에서 가져오는 변이가 산다.
    second_drop = _finding("find_0012")  # b.c — find_0002(E1)에 흡수
    ungraded = _finding("find_0013")  # a.c — 등급 없음: 흡수되고 `grade`는 `null`
    bare_a, bare_b = _finding("find_0020"), _finding("find_0021")  # c.c — 둘 다 등급 없음
    grades = {
        "find_0001": "E0",
        "find_0005": "E2",
        "find_0009": "E3",
        "find_0002": "E1",
        "find_0012": "E0",
    }
    files = {
        "find_0001": "a.c",
        "find_0005": "a.c",
        "find_0009": "a.c",
        "find_0002": "b.c",
        "find_0012": "b.c",
        "find_0013": "a.c",
        "find_0020": "c.c",
        "find_0021": "c.c",
    }

    result = report.run(
        ctx,
        findings=[mid, high, elsewhere, bare_b, second_drop, ungraded, bare_a, low],
        grades=grades,
        files=files,
    )

    assert result.merged_into == {
        "find_0001": "find_0009",
        "find_0005": "find_0009",
        "find_0012": "find_0002",
        "find_0013": "find_0009",
        "find_0021": "find_0020",
    }
    events = [e for e in ctx.trace.events if e.kind is EventKind.FINDING_DEDUPED]
    assert len(events) == result.duplicates == 5
    assert [e.subject_id for e in events] == [
        "find_0001",
        "find_0005",
        "find_0012",
        "find_0013",
        "find_0021",
    ]
    assert all(e.stage == int(Stage.REPORT) and e.subject_kind == "finding" for e in events)
    assert events[0].data == {
        "representative": "find_0009",
        "grade": "E0",
        "representative_grade": "E3",
        "cwe": "CWE-787",
        "file": "a.c",
        "target": "memcpy",  # PROPAGATE 없음 → sink(`_target_symbol`)
    }
    assert events[1].data["representative"] == "find_0009"
    assert events[1].data["grade"] == "E2"
    assert events[2].data["representative"] == "find_0002"  # 자기 그룹의 대표 — a.c의 것이 아니다
    assert (events[2].data["grade"], events[2].data["representative_grade"]) == ("E0", "E1")
    # 등급 없음은 "E0"이 아니라 `null`이다 — 키는 있고 값이 None.
    assert events[3].data["grade"] is None and events[3].data["representative_grade"] == "E3"
    # 대표도 등급이 없으면 `representative_grade`가 `null`이다.
    assert events[4].data["grade"] is None and events[4].data["representative_grade"] is None
    assert "중복 제거: 5건 (무엇과 합쳤는지는 trace의 `finding_deduped`)" in result.render()


def test_dedupe_with_merges_ties_go_to_the_smallest_finding_id() -> None:
    """동급이면 대표는 가장 작은 `finding_id`이고 버린 쪽이 그것을 가리킨다 — 입력 순서와 무관.
    등급이 없는 것은 등급 있는 것에 흡수된다(`_GRADE_RANK_UNKNOWN`)."""
    kept, merged = report.dedupe_with_merges(
        [_finding("find_0009"), _finding("find_0003"), _finding("find_0001")],
        {"find_0009": "E3", "find_0003": "E3"},  # find_0001은 등급 없음
    )

    assert [f.finding_id for f in kept] == ["find_0003"]
    assert merged == {"find_0009": "find_0003", "find_0001": "find_0003"}


def test_report_without_duplicates_records_nothing(ctx: RunContext) -> None:
    """중복이 없으면 대응을 하나도 안 남긴다 — `report.run`이 돈 run에서 이벤트 부재가
    「안 합쳤다」이고, 리포트 바이트도 종전과 같다(중복 줄 자체가 없다)."""
    a = _finding("find_0001")
    b = _finding("find_0002")

    result = report.run(ctx, findings=[a, b], files={"find_0001": "a.c", "find_0002": "b.c"})

    assert result.duplicates == 0
    assert result.merged_into == {}
    assert not [e for e in ctx.trace.events if e.kind is EventKind.FINDING_DEDUPED]
    rendered = result.render()
    assert "중복 제거" not in rendered
    assert "finding_deduped" not in rendered


def test_render_shows_grade_and_distribution(ctx: RunContext) -> None:
    """리포트가 등급을 싣는다 (D36). 예전엔 state만 찍어 E3에 도달한 Finding도 `queued`로
    보였고, run의 성과(E3 건수)를 보려면 trace를 파야 했다."""
    e3 = _finding("find_0001", state=FindingState.VALIDATED)

    rendered = report.run(ctx, findings=[e3], grades={"find_0001": "E3"}).render()

    assert "[E3]" in rendered  # 각 항목 앞머리에 등급
    assert "등급 분포: E3 1건" in rendered  # 맨 위 요약


def test_render_marks_ungraded_finding_as_unknown_not_e0(ctx: RunContext) -> None:
    """`grades`에 없는 Finding은 `[-]`로 찍고 분포에서 뺀다 — "등급 없음"을 "E0"이라
    주장하지 않는다 (D35 리뷰 ⑤). 게이트가 구조적으로 E0을 줄 수 없는 근거 + repro_spec을
    가진 Finding이 있다."""
    backed = _finding("find_0001", state=FindingState.VALIDATED)

    rendered = report.run(ctx, findings=[backed]).render()  # grades 안 넘김

    assert "[-]" in rendered
    assert "[E0]" not in rendered
    assert "등급 분포" not in rendered  # 아는 등급이 0건이면 분포 줄 자체가 없다


def test_render_lists_only_long_inspect_runs_and_counts_the_rest(ctx: RunContext) -> None:
    """분모 전용 집계 줄(#619 (d)): inspect 연속 최장이 2 이상인 Finding만 나열하고 나머지는
    건수다 - 줄이 Finding 수에 비례해 자라지 않는다. 값이 없는(`None`) 집계는 줄을 빼지 않고
    「미측정」으로 찍는다."""
    rendered = report.run(
        ctx,
        findings=[],
        schema_failure_max_run={"auditor": 3},
        pov_inspect_max_run={"find_0001": 3, "find_0002": 2, "find_0003": 1, "find_0004": 0},
    ).render()

    line = next(ln for ln in rendered.splitlines() if ln.startswith("PoV 연속 inspect"))
    assert "find_0001 3 · find_0002 2" in line
    assert "find_0003" not in line and "find_0004" not in line
    assert "나열하지 않은 Finding 2건" in line
    assert "연속 스키마 파싱 실패 최장(역할별, 분모만): auditor 3" in rendered
    recovered = next(ln for ln in rendered.splitlines() if ln.startswith("스키마 재시도로"))
    assert "미측정" in recovered


def test_render_lists_pov_stop_reasons_with_their_counts(ctx: RunContext) -> None:
    """리포트가 PoV 정지 사유를 **사유별 수**로 싣는다 (#357·#358) — 한 수로 묶으면 "상한에서
    헛돌았다"와 "스키마를 못 맞췄다"가 안 갈린다(`ne_absorbed`와 같은 판단).

    **시도 수는 사유별 합과 구조적으로 같다** — 리포트도 `pov_results`(finding_id → 사유) **하나**를
    받아 거기서 둘을 뽑으므로 두 수가 어긋날 자리가 없다. 수를 따로 받으면 그 자리가 생긴다.
    """
    finding = _finding("find_0001", state=FindingState.VALIDATED)

    rendered = report.run(
        ctx,
        findings=[finding],
        pov_results={
            "find_0001": "max_hop",
            "find_0002": "max_hop",
            "find_0003": "parse_failed",
        },
    ).render()

    assert "PoV 정지 사유 (시도 3건)" in rendered
    assert "max_hop 2건" in rendered
    assert "parse_failed 1건" in rendered


def test_render_says_nothing_about_pov_when_pov_did_not_run(ctx: RunContext) -> None:
    """PoV가 시도되지 않은 run(`pov_results`가 빔)에는 그 줄이 없다 — 0건일 때 줄이 나오면 장식이다
    (`ne_absorbed`·`deferred`가 비면 줄을 안 내는 것과 같은 자리). 빈 맵은 8단계 앞에서 멈췄거나,
    PoV 대상이 0건이거나(VALIDATED가 0건이고 `pov_attempt_top_n`이 0이거나 그 추가 대상 풀이
    비었다), 프로필에 pov 좌석이 없다는 뜻이다."""
    finding = _finding("find_0001", state=FindingState.VALIDATED)

    rendered = report.run(ctx, findings=[finding]).render()  # pov_results 안 넘김

    assert "PoV 정지 사유" not in rendered


def test_render_lists_a_single_pov_attempt(ctx: RunContext) -> None:
    """시도가 **한 건**인 run도 PoV 줄이 난다 — 줄이 나는 조건은 「시도가 있다」(`pov_results`가
    비지 않았다)이지 「둘 이상」이 아니다. 위 검사와 e2e 검사는 전부 시도가 둘 이상인 run이라
    이 경계를 안 건드렸고, 발화 조건을 `len(self.pov_results) > 1`로 바꾸는 변이가 한 건짜리
    run의 줄을 조용히 지웠다."""
    finding = _finding("find_0001", state=FindingState.VALIDATED)

    rendered = report.run(ctx, findings=[finding], pov_results={"find_0001": "max_hop"}).render()

    assert "PoV 정지 사유 (시도 1건): max_hop 1건" in rendered


# --- 프롬프트 계약 ----------------------------------------------------------


def test_prefix_is_identical_across_candidates(ctx: RunContext) -> None:
    """prefix가 흔들리면 prefix cache가 빗나가고 cache hit ratio를 잴 수 없다."""
    _triage(ctx, VULN_TARGET)
    _triage(ctx, BENIGN_TARGET)

    scout_prompts = [prompt for role, prompt in ctx.models.prompts if role is Role.SCOUT]
    assert len({p.prefix for p in scout_prompts}) == 1
    assert len({p.body for p in scout_prompts}) == len(scout_prompts)


def test_facts_block_is_the_last_json_fence(ctx: RunContext) -> None:
    """S1 계약: 마지막 ```json 펜스가 모델이 읽는 사실이고 ID는 노드가 발급한다."""
    candidate, _, _ = _triage(ctx, VULN_TARGET)

    body = ctx.models.last_for(Role.SCOUT).body
    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)[-1])
    assert facts["ids"]["candidate_id"] == candidate.candidate_id
    assert facts["run_id"] == RUN_ID
    # `slice_criterion`은 슬라이스 타깃에만 붙는다(opt-in) — 슬라이스 없는 프롬프트는 그 키가
    # 생기기 전과 글자가 같아야 한다. 이 호출은 슬라이스 타깃이 아니다.
    # `fuzz_entrypoint`도 opt-in이고 **PoV만** 준다(2026-10-02) — Scout은 슬라이스 타깃이어도
    # 안 받는다. `auditor_hops`(2026-10-13)도 opt-in이고 **Critic만** 준다. 등식을 유지하는 것이
    # 핵심이다: 무조건 싣는 변이가 여기서 죽는다.
    assert set(facts) == set(prompt_loader.FACT_KEYS) - {
        "slice_criterion",
        "fuzz_entrypoint",
        "auditor_hops",
    }


def test_a_slice_target_sends_its_render_and_slice_criterion(
    ctx: RunContext, scout_declares_slice: None
) -> None:
    """슬라이스 타깃이면 `SOURCE`가 렌더이고 FACTS에 `slice_criterion`이 붙는다(ADR 0012 결정 1).

    `read_source`(심볼 본문)를 부르면 entry 한 줄만 간다 — 줄번호도 배너도 없이.
    """
    render = "/* ===== parser.c ===== */\n4|     char buf[64];\n5|     memcpy(buf, src, len);"
    ctx.read_slice = {"slice_0001": render}.__getitem__
    ref = SliceRef(
        slice_id="slice_0001",
        location=Location(file="parser.c", line_start=5, symbol="parse_header"),
        given_end="sink",
        entry=Location(file="parser.c", line_start=5, symbol="memcpy"),
        lines=(("parser.c", 4), ("parser.c", 5)),
    )
    artifact = next(
        a for a in preprocess.run(ctx, source_root=_root_of(ctx)) if a.path == "parser.c"
    )
    capsule = retrieval.run(ctx, target=ref.location, level=CapsuleLevel.LIGHT)

    candidate = scout.run(ctx, artifact=artifact, capsule=capsule, slice=ref)

    body = ctx.models.last_for(Role.SCOUT).body
    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)[-1])
    # 슬라이스 타깃이라 `slice_criterion`은 붙지만 `fuzz_entrypoint`는 **PoV 전용**이라 안 붙는다
    # (2026-10-02). 등식이라 어느 쪽으로 새도 죽는다.
    assert set(facts) == set(prompt_loader.FACT_KEYS) - {"fuzz_entrypoint", "auditor_hops"}
    assert facts["source"] == render
    assert facts["slice_criterion"] == {
        "given_end": "sink",
        "given": {"file": "parser.c", "line": 5, "symbol": "memcpy"},
        "degraded": False,
    }
    assert candidate.slice_id == "slice_0001"


@pytest.fixture
def scout_declares_slice(monkeypatch: pytest.MonkeyPatch) -> None:
    """`input: slice`를 선언한 Scout — 슬라이스 렌더를 `SOURCE`로 읽는 쪽(v3)."""
    declare_scout_input(monkeypatch, "slice")


_GUTTER_LINE = re.compile(r"^\s*\d+[|:]", re.MULTILINE)
_PARSE_HEADER = Location(file="parser.c", symbol="parse_header", line_start=3, line_end=6)


def _source_declared_slice_ref(ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> SliceRef:
    """`input: source` 선언 + entry 한 줄(parser.c:5, `parse_header` 3~6 안)짜리 슬라이스 타깃.

    `read_slice`는 부르면 터진다 — 선언이 source면 렌더를 읽지도 않는다는 핀이다."""
    declare_scout_input(monkeypatch, "source")

    def _must_not_read(slice_id: str) -> str:
        raise AssertionError(f"input: source인데 슬라이스 렌더({slice_id})를 읽었다")

    ctx.read_slice = _must_not_read
    ctx.symbols_for = lambda file: [_PARSE_HEADER] if file == "parser.c" else None
    return SliceRef(
        slice_id="slice_0001",
        location=Location(file="parser.c", line_start=5, symbol="parse_header"),
        given_end="sink",
        entry=Location(file="parser.c", line_start=5, symbol="memcpy"),
        lines=(("parser.c", 4), ("parser.c", 5)),
    )


def _slice_scout_session(ctx: RunContext, ref: SliceRef) -> scout.ScoutSession:
    artifact = next(
        a for a in preprocess.run(ctx, source_root=_root_of(ctx)) if a.path == "parser.c"
    )
    capsule = retrieval.run(ctx, target=ref.location, level=CapsuleLevel.LIGHT)
    return scout.open_session(ctx, artifact=artifact, capsule=capsule, slice=ref)


def _last_scout_facts(ctx: RunContext) -> dict:
    body = ctx.models.last_for(Role.SCOUT).body
    return json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)[-1])


def test_a_source_declaration_gets_the_function_body_even_for_a_slice_target(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#529 — 입력 모양을 고르는 기준은 슬라이스 유무가 아니라 **프롬프트 선언**(`meta.input`)이다.

    v4 prefix는 `SOURCE`를 「함수 본문, 줄번호도 파일 배너도 없고 첫 줄이 `line_start`」라 단언한다.
    entry는 한 줄이라 `read_source(entry)`가 아니라 entry를 품는 함수가 가야 한다.
    """
    ref = _source_declared_slice_ref(ctx, monkeypatch)

    session = _slice_scout_session(ctx, ref)

    facts = _last_scout_facts(ctx)
    body_lines = VULNERABLE_C.splitlines()[2:6]
    assert facts["source"].splitlines() == body_lines  # entry 한 줄(5)이 아니라 함수(3~6)
    assert len(facts["source"].splitlines()) > 1
    assert not _GUTTER_LINE.search(facts["source"]) and "=====" not in facts["source"]
    first_line = VULNERABLE_C.splitlines()[facts["target"]["line_start"] - 1]
    assert facts["source"].splitlines()[0] == first_line
    assert facts["target"]["line_start"] == 3  # 첫 줄 = FACTS.target.line_start (prefix의 단언)
    # 본문의 「대상」 표기도 같은 범위다 — `describe_target(capsule.target)`이면 entry 한 줄
    # (「줄 5-5」)이 가서 같은 프롬프트의 FACTS와 어긋난다.
    body = ctx.models.last_for(Role.SCOUT).body
    assert "`parser.c` 의 `parse_header` (줄 3-6)" in body and "(줄 5-5)" not in body
    assert "slice_criterion" not in facts  # v4 산문이 그 키를 서술하지 않는다
    assert session.slice is ref
    # resolver의 `visible` — 모델이 본 줄은 함수 3~6이지 SliceRef.lines가 아니다.
    assert session.shown_lines == tuple(("parser.c", n) for n in range(3, 7))


def test_a_source_declaration_without_an_enclosing_symbol_reads_the_entry_window(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """심볼을 못 찾으면 **Auditor와 같은 창**(`entry_scope.ENTRY_WINDOW_LINES`)이다 — 슬라이스
    렌더로 조용히 되돌아가지도(`read_slice`가 터진다), entry 한 줄로 가지도 않는다."""
    from src.orchestrator import entry_scope

    ref = _source_declared_slice_ref(ctx, monkeypatch)
    ctx.symbols_for = lambda _file: []
    monkeypatch.setattr(entry_scope, "ENTRY_WINDOW_LINES", 1)

    _slice_scout_session(ctx, ref)

    facts = _last_scout_facts(ctx)
    assert facts["source"].splitlines() == VULNERABLE_C.splitlines()[3:6]  # entry 5 ± 1 = 4~6
    assert facts["target"]["line_start"] == 4 and facts["target"]["line_end"] == 6
    # 창 폴백은 심볼이 없다 — 노드가 `capsule.target`(슬라이스가 든 심볼)의 이름을 채운다. 그 복사를
    # 지우면 여기서 죽는다(`_ask_model`의 `if target.symbol is None` 두 줄). FACTS symbol 자체의
    # 출처는 #633이 따로 든다 — 이 핀은 **창 폴백**만 본다.
    assert facts["target"]["symbol"] == "parse_header"


def test_a_slice_declaration_keeps_the_slice_render_and_criterion(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`input: slice`는 지금 그대로 — 렌더가 `SOURCE`이고 `slice_criterion`이 붙는다."""
    declare_scout_input(monkeypatch, "slice")
    render = "/* ===== parser.c ===== */\n4|     char buf[64];\n5|     memcpy(buf, src, len);"
    ctx.read_slice = {"slice_0001": render}.__getitem__
    ctx.symbols_for = lambda _file: [_PARSE_HEADER]  # 심볼이 있어도 선언이 slice면 안 쓴다
    ref = SliceRef(
        slice_id="slice_0001",
        location=Location(file="parser.c", line_start=5, symbol="parse_header"),
        given_end="sink",
        entry=Location(file="parser.c", line_start=5, symbol="memcpy"),
        lines=(("parser.c", 4), ("parser.c", 5)),
    )

    session = _slice_scout_session(ctx, ref)

    facts = _last_scout_facts(ctx)
    assert facts["source"] == render
    assert "slice_criterion" in facts
    assert session.shown_lines is None


def test_without_a_slice_the_declaration_does_not_matter(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`slice is None`이면 선언과 무관하게 그대로다 — 타깃 심볼 본문, `slice_criterion` 없음."""
    declare_scout_input(monkeypatch, "slice")
    ctx.read_slice = lambda slice_id: pytest.fail(f"슬라이스가 없는데 렌더({slice_id})를 읽었다")

    _triage(ctx, VULN_TARGET)

    facts = _last_scout_facts(ctx)
    assert facts["source"] == ctx.read_source(VULN_TARGET)
    assert "slice_criterion" not in facts


def _big_slice(ctx: RunContext, *, trim: int, context_limit: int | None = None) -> SliceRef:
    """entry(parser.c:1000)를 가운데 둔 2,000줄 렌더 + 다른 파일 한 줄 — 글자 근사로 ~50k토큰이라
    dev_fake 창 32,768을 넘는다. `scout_slice_trim`·창을 바꾼 config를 ctx에 끼운다."""
    lines = [("parser.c", n) for n in range(1, 2001)] + [("util.c", 7)]
    render = "\n".join(
        ["/* ===== parser.c ===== */"]
        + [f"{n}|     buf[{n}] = src[{n}] + {'x' * 80};" for n in range(1, 2001)]
        + ["/* ===== util.c ===== */", "7|     return 0;"]
    )
    ctx.read_slice = {"slice_0001": render}.__getitem__
    key = ctx.config.model_key_for(Role.SCOUT.value)
    models = dict(ctx.config.models)
    if context_limit is not None:
        models[key] = models[key].model_copy(update={"context_limit": context_limit})
    ctx.config = ctx.config.model_copy(
        update={
            "models": models,
            "thresholds": ctx.config.thresholds.model_copy(update={"scout_slice_trim": trim}),
        }
    )
    return SliceRef(
        slice_id="slice_0001",
        location=Location(file="parser.c", line_start=1000, symbol="parse_header"),
        given_end="sink",
        entry=Location(file="parser.c", line_start=1000, symbol="memcpy"),
        lines=tuple(lines),
    )


def _scout_on(ctx: RunContext, ref: SliceRef) -> None:
    artifact = next(
        a for a in preprocess.run(ctx, source_root=_root_of(ctx)) if a.path == "parser.c"
    )
    capsule = retrieval.run(ctx, target=ref.location, level=CapsuleLevel.LIGHT)
    scout.run(ctx, artifact=artifact, capsule=capsule, slice=ref)


def _gutter_lines(source: str) -> list[int]:
    return [int(m) for m in re.findall(r"^(\d+)\| ", source, re.MULTILINE)]


def test_an_oversized_slice_is_trimmed_to_the_scout_window_around_the_entry(
    ctx: RunContext,
    scout_declares_slice: None,
) -> None:
    """#353 (b) — 렌더가 창을 넘으면 entry에 가까운 줄부터 남겨 창 안에 든다.

    provenance가 없는 슬라이스라 전부 같은 순위 → entry와의 줄 거리순만 남는다. 파일 우선순위는
    없다(#375 HIGH-2): util.c:7은 거리 993이라 창(entry 양쪽 수백 줄) 밖이라서 빠진다 — 다른
    파일이라서가 아니다. 토크나이저가 없으면 **하한비 3.28**로 보수적으로 센다(#375 HIGH-1)."""
    ref = _big_slice(ctx, trim=1)

    _scout_on(ctx, ref)

    prompt = ctx.models.last_for(Role.SCOUT)
    (event,) = [e for e in ctx.trace.events if e.kind is EventKind.SCOUT_SLICE_TRIMMED]
    budget = event.data["budget"]
    assert estimate_tokens(prompt.prefix) + estimate_tokens(prompt.body) <= budget
    assert event.data["tokens_before"] > budget >= event.data["tokens_after"]
    assert event.data["fit"] is True and event.data["token_counter"] == "estimate"
    # HIGH-1: 추정 경로는 len//4(4.0)가 아니라 하한비 3.28로 센다 → 같은 프롬프트를 4.0으로 센
    # 값보다 크다(과대추정 = 보수적). 4.0으로 되돌리면 이 부등식이 깨진다.
    estimate_now = estimate_tokens(prompt.prefix) + estimate_tokens(prompt.body)
    assert estimate_now < event.data["tokens_after"]
    assert event.data["lines_before"] == 2001 > event.data["lines_kept"]
    assert event.data["lines_kept"] > 300  # 이분 탐색 상한이 300줄로 캡되면 깨진다(#375 M-4)
    assert event.data["entry_kept"] is True  # entry(1000)가 남았다(#375 L-1)
    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", prompt.body, re.DOTALL)[-1])
    kept = _gutter_lines(facts["source"])
    assert len(kept) == event.data["lines_kept"]
    assert 1000 in kept and kept == list(range(kept[0], kept[-1] + 1))
    assert abs((1000 - kept[0]) - (kept[-1] - 1000)) <= 1  # entry 양쪽으로 고르게
    assert "util.c" not in facts["source"]  # 거리 993이라 창 밖(파일 우선순위 때문이 아니다)
    assert facts["source"].startswith("/* ===== parser.c ===== */\n")
    assert facts["source"] in prompt.body.split("```json")[0]  # SOURCE도 같은 렌더


def test_slice_trim_off_sends_the_whole_render(ctx: RunContext, scout_declares_slice: None) -> None:
    """기본(0)이면 지금처럼 통째로 — 넘어도 줄이지 않고 이벤트도 없다(크기 거절은 서빙 몫)."""
    ref = _big_slice(ctx, trim=0)

    _scout_on(ctx, ref)

    assert ctx.read_slice("slice_0001") in ctx.models.last_for(Role.SCOUT).body
    assert not [e for e in ctx.trace.events if e.kind is EventKind.SCOUT_SLICE_TRIMMED]


def test_a_slice_that_fits_is_not_trimmed(ctx: RunContext, scout_declares_slice: None) -> None:
    """창이 넉넉하면 켜져 있어도 그대로 — 이벤트 부재 = 안 잘림."""
    ref = _big_slice(ctx, trim=1, context_limit=400_000)

    _scout_on(ctx, ref)

    assert ctx.read_slice("slice_0001") in ctx.models.last_for(Role.SCOUT).body
    assert not [e for e in ctx.trace.events if e.kind is EventKind.SCOUT_SLICE_TRIMMED]


def test_a_slice_that_cannot_fit_goes_as_the_entry_line_and_says_so(
    ctx: RunContext, scout_declares_slice: None
) -> None:
    """프롬프트 고정분만으로 창을 넘으면 entry 한 줄로 보내고 `fit=false` — 크기 거절은 서빙 몫."""
    output_max = ctx.config.thresholds.output_max_for(Role.SCOUT.value)
    ref = _big_slice(ctx, trim=1, context_limit=output_max + 600)

    _scout_on(ctx, ref)

    (event,) = [e for e in ctx.trace.events if e.kind is EventKind.SCOUT_SLICE_TRIMMED]
    assert event.data["fit"] is False and event.data["lines_kept"] == 1
    body = ctx.models.last_for(Role.SCOUT).body
    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", body, re.DOTALL)[-1])
    assert _gutter_lines(facts["source"]) == [1000]


_MARK = "QZX"  # 코드 줄에 한 번씩만 넣는 표식 — 주입 카운터가 센다(프롬프트·배너·캡슐엔 없다)


def _counter_slice(
    ctx: RunContext,
    *,
    parser_lines: list[int],
    util_lines: list[int],
    provenance: dict[str, tuple[tuple[str, int], ...]],
    entry_line: int,
    keep_k: int,
) -> SliceRef:
    """표식 카운터를 주입해 프롬프트 크기 = 2 × (렌더 코드 줄 수)로 **정확히** 통제한다.

    각 코드 줄에 `QZX`가 하나이고 SOURCE·FACTS.source 두 곳에 실리므로 카운터는 줄당 2를 센다.
    창을 `output_max + 512 + 2*keep_k`로 맞춰 몫(budget)을 정확히 `2*keep_k` 토큰으로 만든다.
    """
    parts = ["/* ===== parser.c ===== */"]
    lines: list[tuple[str, int]] = []
    for n in parser_lines:
        parts.append(f"{n}|     v_{n} = {_MARK}_{n};")
        lines.append(("parser.c", n))
    if util_lines:
        parts.append("/* ===== util.c ===== */")
        for n in util_lines:
            parts.append(f"{n}|     w_{n} = {_MARK}_{n};")
            lines.append(("util.c", n))
    ctx.read_slice = {"slice_0001": "\n".join(parts)}.__getitem__
    out_max = ctx.config.thresholds.output_max_for(Role.SCOUT.value)
    key = ctx.config.model_key_for(Role.SCOUT.value)
    models = dict(ctx.config.models)
    models[key] = models[key].model_copy(update={"context_limit": out_max + 512 + 2 * keep_k})
    ctx.config = ctx.config.model_copy(
        update={
            "models": models,
            "thresholds": ctx.config.thresholds.model_copy(update={"scout_slice_trim": 1}),
        }
    )
    ctx.counter_for_role = lambda _role: (lambda s: s.count(_MARK))
    return SliceRef(
        slice_id="slice_0001",
        location=Location(file="parser.c", line_start=entry_line, symbol="parse_header"),
        given_end="sink",
        entry=Location(file="parser.c", line_start=entry_line, symbol="memcpy"),
        lines=tuple(lines),
        provenance=provenance,
    )


# K=5 핀: candidate_anchor(entry 100 · 교차 파일 util 500) → data_dependency → context 순.
_PROV_K5 = {
    "candidate_anchor": (("parser.c", 100), ("util.c", 500)),
    "data_dependency": (("parser.c", 99), ("parser.c", 101), ("parser.c", 98), ("parser.c", 102)),
    "context": (
        ("parser.c", 97),
        ("parser.c", 103),
        ("parser.c", 96),
        ("parser.c", 104),
        ("parser.c", 95),
        ("parser.c", 105),
    ),
}


def test_trim_keeps_provenance_tiers_and_does_not_drop_cross_file_first(
    ctx: RunContext, scout_declares_slice: None
) -> None:
    """#375 HIGH-2/M-4 — anchor > dependency > context 순으로 남기고, 몫은 정확히 식대로다.

    주입 카운터로 몫을 2*K=10 토큰에 고정한다. 남는 다섯은 우선순위 상위 다섯:
    entry(100)·교차 파일 anchor(util 500)·dependency(99,101,98). **교차 파일 anchor가 거리 400인데도
    같은 파일 context(97 거리 3)보다 먼저 남는다** → 파일 우선순위가 없다는 증거(M12)."""
    ref = _counter_slice(
        ctx,
        parser_lines=list(range(95, 106)),
        util_lines=[500],
        provenance=_PROV_K5,
        entry_line=100,
        keep_k=5,
    )

    _scout_on(ctx, ref)

    prompt = ctx.models.last_for(Role.SCOUT)
    (event,) = [e for e in ctx.trace.events if e.kind is EventKind.SCOUT_SLICE_TRIMMED]
    out_max = ctx.config.thresholds.output_max_for(Role.SCOUT.value)
    key = ctx.config.model_key_for(Role.SCOUT.value)
    assert event.data["budget"] == ctx.config.models[key].context_limit - out_max - 512 == 10
    assert event.data["tokens_before"] == 24  # 2 × 12 코드 줄
    assert event.data["tokens_after"] == 10  # 2 × 5, 몫과 같다(최대성)
    assert event.data["lines_kept"] == 5
    assert event.data["token_counter"] == "model"  # 주입 카운터를 썼다(M13)
    assert event.data["entry_kept"] is True
    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", prompt.body, re.DOTALL)[-1])
    assert set(_gutter_lines(facts["source"])) == {98, 99, 100, 101, 500}
    assert "util.c" in facts["source"]  # 교차 파일 anchor가 남았다 — 먼저 안 버려진다
    assert 97 not in _gutter_lines(facts["source"])  # 가까운 context보다 먼 anchor가 우선


def test_trim_breaks_distance_ties_toward_the_earlier_line(
    ctx: RunContext, scout_declares_slice: None
) -> None:
    """#375 M-4 — 같은 순위·같은 거리면 렌더 순서(앞 줄)를 남긴다. 반대로 깨면 101이 남는다.

    99·101 둘 다 entry 100에서 거리 1, provenance 없음(같은 순위). 몫 2*1=2라 하나만 남는다."""
    ref = _counter_slice(
        ctx, parser_lines=[99, 101], util_lines=[], provenance={}, entry_line=100, keep_k=1
    )

    _scout_on(ctx, ref)

    prompt = ctx.models.last_for(Role.SCOUT)
    (event,) = [e for e in ctx.trace.events if e.kind is EventKind.SCOUT_SLICE_TRIMMED]
    assert event.data["lines_kept"] == 1 and event.data["fit"] is True
    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", prompt.body, re.DOTALL)[-1])
    assert _gutter_lines(facts["source"]) == [99]  # 앞 줄(작은 i)을 남긴다


def test_trim_keeps_a_render_that_exactly_fills_the_budget(
    ctx: RunContext, scout_declares_slice: None
) -> None:
    """#375 M-4 — 전체 크기가 몫과 **같으면** 자르지 않는다(`<=`). `<`로 바꾸면 자른다."""
    ref = _counter_slice(
        ctx, parser_lines=[10, 11], util_lines=[], provenance={}, entry_line=10, keep_k=2
    )

    _scout_on(ctx, ref)

    # before = 2*2 = 4 = budget(2*2) → 그대로. 이벤트가 없어야 한다.
    assert not [e for e in ctx.trace.events if e.kind is EventKind.SCOUT_SLICE_TRIMMED]
    assert ctx.read_slice("slice_0001") in ctx.models.last_for(Role.SCOUT).body


def test_trim_visible_reflects_only_the_kept_lines(
    ctx: RunContext, scout_declares_slice: None
) -> None:
    """#375 M-2 — 잘린 좌석의 세션은 **모델이 본 줄**만 싣고, resolver `visible`이 그것을 쓴다.

    자르기 전 `SliceRef.lines` 전체를 쓰면 모델이 못 본 줄을 "이미 SOURCE에 있다"로 답해 버린다."""
    ref = _counter_slice(
        ctx,
        parser_lines=list(range(95, 106)),
        util_lines=[500],
        provenance=_PROV_K5,
        entry_line=100,
        keep_k=5,
    )
    artifact = next(
        a for a in preprocess.run(ctx, source_root=_root_of(ctx)) if a.path == "parser.c"
    )
    capsule = retrieval.run(ctx, target=ref.location, level=CapsuleLevel.LIGHT)

    session = scout.open_session(ctx, artifact=artifact, capsule=capsule, slice=ref)

    assert session.shown_lines is not None
    assert set(session.shown_lines) == {
        ("parser.c", 98),
        ("parser.c", 99),
        ("parser.c", 100),
        ("parser.c", 101),
        ("util.c", 500),
    }
    visible = runner._ne_visible(ref, session.shown_lines)
    assert visible == {"parser.c": frozenset({98, 99, 100, 101}), "util.c": frozenset({500})}
    # 자르기 전 전체는 더 넓다 — 바로 그 차이가 M-2가 막는 오답 범위다.
    assert runner._ne_visible(ref, None)["parser.c"] == frozenset(range(95, 106))


def test_trim_roundtrips_through_the_real_render_with_noncontiguous_lines(
    ctx: RunContext, scout_declares_slice: None
) -> None:
    """#375 M-4 — 손 f-string이 아니라 `adapters.slices.render()` 출력으로 라운드트립한다.

    듬성한 줄(5·8·20)이라 `_parse_render`가 건너뛴 번호를 그대로 들어야 한다."""
    from src.adapters import slices as slices_adapter

    body = "\n".join(f"line_{i} = {_MARK}_{i};" for i in range(1, 21))
    content = {"parser.c": body.encode("utf-8")}
    picked = [("parser.c", 5), ("parser.c", 8), ("parser.c", 20)]
    picked_lines = [f"line_{n} = {_MARK}_{n};" for _, n in picked]
    code = "\n".join(["/* ===== parser.c ===== */"] + picked_lines)
    item = slices_adapter.Slice(
        id="slice_0001",
        candidate_key="k",
        case="A",
        entry={"file": "parser.c", "line": 5, "symbol": "parse_header"},
        members=("k",),
        lines=tuple(picked),
        code=code,
    )
    rendered = slices_adapter.render(item, lambda f: content[f])
    assert rendered == "\n".join(
        ["/* ===== parser.c ===== */"] + [f"{n}| line_{n} = {_MARK}_{n};" for _, n in picked]
    )
    ctx.read_slice = {"slice_0001": rendered}.__getitem__
    out_max = ctx.config.thresholds.output_max_for(Role.SCOUT.value)
    key = ctx.config.model_key_for(Role.SCOUT.value)
    models = dict(ctx.config.models)
    models[key] = models[key].model_copy(update={"context_limit": out_max + 512 + 4})  # 몫 4 = 2줄
    ctx.config = ctx.config.model_copy(
        update={
            "models": models,
            "thresholds": ctx.config.thresholds.model_copy(update={"scout_slice_trim": 1}),
        }
    )
    ctx.counter_for_role = lambda _role: (lambda s: s.count(_MARK))
    ref = SliceRef(
        slice_id="slice_0001",
        location=Location(file="parser.c", line_start=5, symbol="parse_header"),
        given_end="source",
        entry=Location(file="parser.c", line_start=5, symbol="parse_header"),
        lines=tuple(picked),
    )

    _scout_on(ctx, ref)

    prompt = ctx.models.last_for(Role.SCOUT)
    (event,) = [e for e in ctx.trace.events if e.kind is EventKind.SCOUT_SLICE_TRIMMED]
    assert event.data["lines_kept"] == 2  # 몫 4 / 2부 = 2줄
    facts = json.loads(re.findall(r"```json\s*(\{.*?\})\s*```", prompt.body, re.DOTALL)[-1])
    kept = _gutter_lines(facts["source"])
    assert kept == [5, 8]  # entry 5에 가까운 둘, 듬성한 번호 보존


def test_scout_slice_trim_is_absent_from_flatten_when_off() -> None:
    """#375 M-3/M-4 — 0이면 `flatten()`에 키가 없고 1이면 실린다(#349/#351 선례).

    ⏵ 2026-10-05 — `config/budgets.yaml`이 `scout_slice_trim: 1`을 선언해 **기본값이 1이 됐다.**
    전 판은 커밋된 기본값이 0이라고 단언했고 그 줄에 *"기본값 1로 바꾸면 깨진다(M16)"*라고
    적혀 있었다 — 그날이 왔다. 무는 명제는 **기본값이 아니라 `flatten()`의 모양**이라 두 값을
    직접 만들어 양쪽을 문다. 이름의 `defaults_off`도 떼었다(거짓이 됐다).
    """
    thresholds = load_config("dev_fake").thresholds
    off = thresholds.transition(scout_slice_trim=0)
    on = thresholds.transition(scout_slice_trim=1)

    assert "scout_slice_trim" not in off.flatten()
    assert on.flatten()["scout_slice_trim"] == 1.0  # 켜면 기록에 실린다


def test_render_rejects_missing_and_unknown_placeholders() -> None:
    template = load_prompt(Role.SCOUT, "v1")
    facts = build_facts(run_id=RUN_ID, ids={"candidate_id": "cand_0001"})

    with pytest.raises(PromptError):
        template.render(facts=facts, target="t")  # source·capsule_sections 누락
    with pytest.raises(PromptError):
        template.render(facts=facts, target="t", capsule_sections="s", source="c", nonesuch="x")


def test_facts_reject_keys_outside_the_contract() -> None:
    template = load_prompt(Role.SCOUT, "v1")
    facts = build_facts(run_id=RUN_ID, ids={}) | {"secret": "x"}

    with pytest.raises(PromptError):
        template.render(facts=facts, target="t", capsule_sections="s", source="c")


def test_prompts_contain_no_answer_leakage() -> None:
    """CVE ID·flag·patch diff를 프롬프트에 넣지 않는다 (evaluation.md §4)."""
    banned = re.compile(r"CVE-\d{4}-\d{4,}|flag\{|^\+\+\+ b/|^--- a/", re.MULTILINE)
    offenders = [
        path.relative_to(PROMPTS_DIR).as_posix()
        for path in sorted(PROMPTS_DIR.rglob("*.md"))
        if banned.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


# --- 지표 산출 가능성 -------------------------------------------------------


def test_scout_removal_rate_is_derivable_and_not_degenerate(ctx: RunContext) -> None:
    """제거율을 trace에서 계산할 수 있고, 0%도 100%도 아니다.

    값 자체는 단언하지 않는다 — 그것이 이 프로젝트가 재려는 대상이다.
    """
    _triage(ctx, VULN_TARGET)
    _triage(ctx, BENIGN_TARGET)

    stage4 = [
        e for e in ctx.trace.events if e.kind is EventKind.TRANSITION and e.stage == Stage.SCOUT
    ]
    created = [e for e in stage4 if e.data["rule"] == "C-1"]
    promoted = [e for e in stage4 if e.data["reason"] == ReasonCode.SCOUT_PROMOTED.value]

    removal_rate = 1 - len(promoted) / len(created)
    assert 0.0 < removal_rate < 1.0


def _finding(
    finding_id: str,
    *,
    state: FindingState = FindingState.HYPOTHESIZED,
    confidence: float = 0.6,
    signal: str = "AddressSanitizer: heap-buffer-overflow at parse_header",
) -> Finding:
    backed = state in {FindingState.VALIDATED, FindingState.PROVEN, FindingState.REPORTED}
    return Finding(
        finding_id=finding_id,
        run_id=RUN_ID,
        candidate_id="cand_0001",
        title="parse_header에서 검증 없는 memcpy",
        cwe="CWE-787",
        state=state,
        confidence=confidence,
        path=[
            PathStep(step=PathStepKind.SOURCE, symbol="recv_request"),
            PathStep(step=PathStepKind.SINK, symbol="memcpy"),
        ],
        rca_draft="길이 검증 없이 전달한다.",
        evidence_refs=["ev_0001"] if backed else [],
        produced_by=ProducedBy(role=Role.AUDITOR, model="fake", prompt_version="v1"),
        repro_spec=ReproSpec(steps=["./build.sh"], expected_signal=signal) if backed else None,
    )


def test_a_seat_failing_in_different_stages_does_not_add_up_but_in_one_stage_does(
    ctx: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**자리 연속 폐기 카운터의 키는 `_caller(단계, 자리)`다 (D73 ②, #197).**

    같은 Scout 자리의 시도가 4단계에서 한 번, 5단계에서 한 번 죽은 것은 「그 자리가 체계적으로
    고장 났다」가 아니다 — 죽은 모델이 다르다. 예전 키(`item.role`)는 둘을 한 버킷에 쌓아 상한에
    닿았다. 같은 단계에서 다시 죽으면 쌓인다(D68 — 멤버 하나의 전량 실패는 여전히 선다).

    실패 사이마다 `completed()`를 부르는 것은 **형제 자리의 완주**다 — run 카운터를 지워 자리
    카운터만 남긴다(풀에서 형제가 멀쩡할 때의 모양). 상한 2로 낮춰 세 번 안에 가른다.
    """
    monkeypatch.setattr(runner, "_MAX_CONSECUTIVE_DROPS", 2)
    # `_drop`이 target에서 읽는 것은 위치와 슬라이스 id뿐이다.
    target = runner._Target(
        artifact=None, location=VULN_TARGET, evidences=(), signal_rank=0
    )
    state = runner._Accumulator()
    drops = runner._ConsecutiveDrops()

    def fail(stage: Stage) -> None:
        runner._drop(
            ctx, state, target, "schema_parse_failed", SchemaParseError("bad"), drops,
            stage=stage, role=Role.SCOUT_MEMORY,
        )
        drops.completed()

    fail(Stage.SCOUT)
    fail(Stage.AUDITOR)  # 다른 단계 — 같은 자리여도 4단계 키에 안 쌓인다
    with pytest.raises(runner._TooManyDrops, match="4단계 scout_memory"):
        fail(Stage.SCOUT)  # 같은 단계 두 번째 — 상한


def _drop_one(ctx: RunContext, drops: runner._ConsecutiveDrops, symbol: str, **where) -> None:  # noqa: ANN003
    """`symbol` 대상의 시도 하나를 스키마 실패로 버린다 — 카운터만 보려고 `_drop`을 직접 부른다."""
    target = runner._Target(
        artifact=None,
        location=Location(file="parser.c", symbol=symbol, line_start=3, line_end=6),
        evidences=(),
        signal_rank=0,
    )
    runner._drop(
        ctx, runner._Accumulator(), target, "schema_parse_failed", SchemaParseError("bad"), drops,
        **{"stage": Stage.SCOUT, "role": Role.SCOUT_MEMORY, **where},
    )


_POOL_SEATS = (Role.SCOUT_MEMORY, Role.SCOUT_BOUNDARY, Role.SCOUT_INJECTION)


def test_a_fan_out_dying_inside_one_target_does_not_trip_the_run_counter(
    ctx: RunContext,
) -> None:
    """**한 대상의 세 자리가 죽은 것은 증거 하나다 — 둘째 대상이 죽어야 run 카운터가 선다.**

    PR #584 리뷰 §1 — 자리 수(3) ≥ 상한(3)이면 대상 하나의 팬아웃 전멸이 run을 죽였다. 이 검사는
    상한만큼 쌓인 폐기가 **한 대상**에 있을 때 안 서고, 다른 대상의 다음 폐기에 서는 것을 문다.
    """
    drops = runner._ConsecutiveDrops()
    for seat in _POOL_SEATS:  # 상한 3 = 자리 3
        _drop_one(ctx, drops, "a", role=seat)
    assert drops.run == runner._MAX_CONSECUTIVE_DROPS

    with pytest.raises(runner._TooManyDrops, match="자리 무관"):
        _drop_one(ctx, drops, "b", role=Role.SCOUT_MEMORY)


def test_the_run_counter_alone_stops_failures_spread_over_targets_and_seats(
    ctx: RunContext,
) -> None:
    """**서로 다른 대상 셋이 서로 다른 자리에서 하나씩 죽으면 셋째에 선다 — 자리 카운터 없이.**

    자리 키(`(4단계, 자리)`)가 모두 1이라 자리 카운터는 못 닿는다 — 서는 것은 run 카운터뿐이다.
    run 카운터 판정을 끄면 이 검사가 빨개진다(`DID NOT RAISE`).
    """
    drops = runner._ConsecutiveDrops()
    _drop_one(ctx, drops, "a", role=Role.SCOUT_MEMORY)
    _drop_one(ctx, drops, "b", role=Role.SCOUT_BOUNDARY)
    assert max(drops.by_caller.values()) == 1

    with pytest.raises(runner._TooManyDrops, match="자리 무관, 서로 다른 대상 3개"):
        _drop_one(ctx, drops, "c", role=Role.SCOUT_INJECTION)


def test_a_completed_attempt_clears_the_run_counters_target_streak(ctx: RunContext) -> None:
    """완주가 run 카운터와 그 대상 목록을 **같이** 지운다 — 목록이 남으면 앞 대상이 새 줄에 낀다."""
    drops = runner._ConsecutiveDrops()
    for seat in _POOL_SEATS:
        _drop_one(ctx, drops, "a", role=seat)
    drops.completed()
    assert drops.run == 0
    assert not drops.run_targets

    for seat in _POOL_SEATS:  # 새 줄 — 한 대상이라 다시 안 선다
        _drop_one(ctx, drops, "b", role=seat)


def test_auditor_failures_from_different_scout_seats_share_one_key(ctx: RunContext) -> None:
    """**5단계 키는 Scout 자리로 갈리지 않는다 (D73 ②, #197) — `_caller`를 문다.**

    자리 셋의 Auditor 실패가 한 버킷에 쌓여 셋째에 선다. `_caller`가 5단계에서도 자리를 키로 쓰면
    (`return (stage, seat)`) 각 버킷이 1이라 `DID NOT RAISE`다. 실패 사이의 `completed()`는 run
    카운터를 지우는 형제의 완주다 — 자리 카운터만 남긴다.
    """
    drops = runner._ConsecutiveDrops()
    for seat in _POOL_SEATS[:-1]:
        _drop_one(ctx, drops, "a", stage=Stage.AUDITOR, role=seat)
        drops.completed()

    with pytest.raises(runner._TooManyDrops, match="5단계"):
        _drop_one(ctx, drops, "a", stage=Stage.AUDITOR, role=_POOL_SEATS[-1])


def test_with_fewer_seats_than_the_cap_the_run_counter_still_stops_at_the_cap(
    ctx: RunContext,
) -> None:
    """자리 수(2) < 상한(3)이면 둘째 대상이 죽는 순간 이미 상한번째다 — 「한 건 늦다」는 자리 수가
    상한에 닿을 때(N ≥ 상한)만이다. 서는 시점은 `max(상한, 자리 수 + 1)`번째 시도다."""
    drops = runner._ConsecutiveDrops()
    _drop_one(ctx, drops, "a", role=Role.SCOUT_MEMORY)
    _drop_one(ctx, drops, "a", role=Role.SCOUT_BOUNDARY)

    with pytest.raises(runner._TooManyDrops, match=r"후보 3건이 연속으로 실패했다\(자리 무관"):
        _drop_one(ctx, drops, "b", role=Role.SCOUT_MEMORY)
