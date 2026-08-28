"""파이프라인 노드와 프롬프트 계약 테스트.

**측정값을 하드코딩하지 않는다.** 점수·후보 수·제거율은 이 프로젝트가 재려는 값이지
테스트가 고정할 상수가 아니다. 여기서 고정하는 것은 관계("benign은 임계값 미만"),
계약("Critic 프롬프트에 rca_draft가 없다"), 그리고 **산출 가능성**(제거율을 trace에서
계산할 수 있고 0%도 100%도 아니다)뿐이다.
"""

import json
import re
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest

from src.adapters.factory import build_deps
from src.adapters.fake_retriever import read_symbol_source
from src.config import load_config
from src.orchestrator import prompt_loader, runner
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import (
    auditor,
    critic,
    gate_node,
    index,
    preprocess,
    report,
    retrieval,
    scout,
    signals,
    validate,
)
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.prompt_loader import PROMPTS_DIR, PromptError, build_facts, load_prompt
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
    ReproSpec,
    Role,
    Verdict,
)
from src.schemas.finding import Finding, PathStep, PathStepKind

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

    def complete(self, *, role, prompt, schema):  # noqa: ANN001, ANN201
        self.prompts.append((role, prompt))
        return self._inner.complete(role=role, prompt=prompt, schema=schema)

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


def test_ungrounded_rebuttal_is_normalized_once(ctx: RunContext) -> None:
    """근거 없는 반박은 uphold로 처리하되 지표에 남는다. 정규화 지점은 한 곳이다.

    호출 그래프 근거가 없는 상태를 만든다 — 그래야 Critic이 근거 없이 반박한다.
    """
    candidate, capsule, _ = _triage(ctx, BENIGN_TARGET)
    finding, _ = auditor.run(ctx, candidate=candidate, capsule=capsule)

    after, decision = critic.run(ctx, finding=finding)

    assert decision.verdict is Verdict.REBUT and not decision.is_grounded
    normalized = [e for e in ctx.trace.events if e.kind is EventKind.CRITIC_NORMALIZED]
    assert len(normalized) == 1
    verdict_decision = next(
        e
        for e in ctx.trace.events
        if e.kind is EventKind.POLICY_DECISION and e.data["point"] == "critic_verdict"
    )
    assert verdict_decision.data["basis.verdict"] == Verdict.UPHOLD.value
    assert after.state is not FindingState.QUEUED  # 반박으로 탈락하지 않았다


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
    assert set(facts) == set(prompt_loader.FACT_KEYS)


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
