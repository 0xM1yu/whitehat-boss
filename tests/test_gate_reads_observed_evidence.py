"""6단계 등급이 Auditor의 **자기 신고**(`evidence_refs`)에 안 달렸다 (D106 (b) · 이슈 #552).

**명제 — 같은 Evidence를 관측했으면 모델이 인용했든 안 했든 등급이 같다.**
철자가 아니라 성질을 문다: `evidence_refs`를 비운 Finding과 전부 채운 Finding을 **같은 풀**로
돌려 등급이 같음을 단언한다(`tests/test_gate.py`의 조합 전수와 같은 모양).
⚠ 「둘 다 E0」로도 등식은 성립하므로 **E1 이상임을 같이 단언한다** — 안 그러면 수정 전에도 초록이다.

⚠ **Fake로는 이 결함을 만들 수 없다.** `src/adapters/fake_model.py`의 Auditor 세 자리가 전부
`evidence_refs=[ref.evidence_id for ref in context.evidence]`라 **항상 전부 인용한다.** 그래서
Fake 기반 테스트 어디에도 「Evidence는 있는데 인용이 빈」 경우가 없었고, 이 결함이 실서빙까지
살았다(CLAUDE.md §5 「Fake는 항등식을 만든다」). 여기서는 `Finding`을 손으로 만든다.

**관측** — `run_20261007_0001`(2026-10-08 오프라인 재등급, 실제 `gate.evaluate`로 판정):
Finding 5건이 전부 `evidence_refs=[]`라 6단계에서 E0 → `queued`였고 7단계 이벤트가 0건이었다.
같은 타깃 Evidence를 그대로 두고 다시 매기면 **5건 전부 E1**이다(`static_ok` — semgrep `ok=True`).
"""

from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest

from src.adapters.factory import build_deps
from src.adapters.fake_retriever import read_symbol_source
from src.config import load_config
from src.orchestrator.budget import BudgetLedger
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import critic, gate_node
from src.orchestrator.policy import FixedThresholdPolicy
from src.orchestrator.trace import TraceWriter
from src.schemas import (
    Evidence,
    EvidenceGrade,
    EvidenceKind,
    Finding,
    FindingState,
    Location,
    PathStep,
    PathStepKind,
    ProducedBy,
    Role,
)

RUN_ID = "run_20260901_0001"
NOW = datetime(2026, 9, 1, tzinfo=UTC)
OBSERVED_AT = NOW
AUDITOR = ProducedBy(role=Role.AUDITOR, model="fake", prompt_version="v1")

# 후보 위치와 **겹치지 않는** 정적 신호 위치. 실제 run이 그랬다 — semgrep은 `apprentice.c:1086`,
# 후보는 429·486·743이라 `location_ok`가 거짓이고 `static_ok`만 p1을 세웠다. 겹치게 두면
# 두 술어가 한꺼번에 참이 되어 **어느 쪽이 등급을 올렸는지 검사가 못 가른다.**
CANDIDATE_LOCATION = Location(file="parser.c", symbol="parse_header", line_start=3, line_end=6)
SIGNAL_LOCATION = Location(file="parser.c", symbol="other", line_start=90, line_end=92)

SOURCE_C = """#include <string.h>
void parse_header(const char *in, char *out) {
    memcpy(out, in, 64);
}
"""


class RecordingModelClient:
    """실제 클라이언트에 위임하면서 프롬프트를 남긴다 (`tests/test_nodes.py`에서 복제)."""

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
    """dev_fake 프로필로 배선된 실행 문맥.

    `tests/test_nodes.py`의 같은 이름 픽스처를 복제했다 — `tests/test_auditor_loop.py`가
    같은 이유로 복제한 것과 같다(픽스처는 파일을 넘어 공유되지 않는다)."""
    root = tmp_path / "target"
    root.mkdir()
    (root / "parser.c").write_text(SOURCE_C, encoding="utf-8")

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


def _evidence(
    evidence_id: str,
    kind: EvidenceKind,
    *,
    source: str,
    ok: bool = True,
    location: Location | None = None,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        run_id=RUN_ID,
        kind=kind,
        source=source,
        artifact_id="art_0001",
        location=location,
        payload_ref=f"logs/{source}.json",
        digest="sha256:" + "0" * 64,
        observed_at=OBSERVED_AT,
        ok=ok,
        exit_code=None,
        error=None if ok else "도구 실패",
    )


def _static_signal() -> Evidence:
    """실제 run의 `ev_0002`와 같은 모양 — semgrep · STATIC · `ok=True` · 위치 있음."""
    return _evidence("ev_static", EvidenceKind.STATIC, source="semgrep", location=SIGNAL_LOCATION)


def _finding(
    *,
    refs: list[str],
    path: list[PathStep] | None = None,
    finding_id: str = "find_0001",
) -> Finding:
    """Auditor 산출 Finding. **`refs`가 모델의 자기 신고다** — 이 검사의 변수는 이것 하나다.

    ⚠ 한 테스트에서 두 번 게이트를 돌릴 때는 `finding_id`를 갈라야 한다 — 전이 표는 **저장소에
    기록된 상태**를 보므로 같은 id를 두 번 밀면 두 번째가 `queued → hypothesized`로 읽혀
    `IllegalTransition`이 난다(F-14가 아닌 조합).
    """
    return Finding(
        finding_id=finding_id,
        run_id=RUN_ID,
        candidate_id="cand_0001",
        title="검증 없는 memcpy",
        cwe="CWE-787",
        state=FindingState.HYPOTHESIZED,
        confidence=0.6,
        path=path or [],
        evidence_refs=refs,
        rca_draft="parse_header가 길이를 확인하지 않고 memcpy로 넘긴다.",
        produced_by=AUDITOR,
    )


# --- 핀 ① 성질: 자기 신고가 등급을 바꾸지 않는다 ---------------------------------


def test_the_self_report_does_not_change_the_grade(ctx: RunContext) -> None:
    """같은 Evidence 풀이면 인용 0건과 전부 인용의 등급이 **같다**.

    ⚠ 등식만 보면 「둘 다 E0」로도 통과한다. 그래서 **E1 이상**을 같이 문다 —
    관측된 정적 신호가 등급을 세웠다는 것까지가 이 핀의 명제다.
    """
    observed = (_static_signal(),)
    kwargs = {"evidences": observed, "candidate_location": CANDIDATE_LOCATION}

    _, silent = gate_node.run(ctx, finding=_finding(refs=[], finding_id="find_silent"), **kwargs)
    _, cited = gate_node.run(
        ctx, finding=_finding(refs=["ev_static"], finding_id="find_cited"), **kwargs
    )

    assert silent.grade is cited.grade
    assert silent.grade is EvidenceGrade.E1
    assert silent.predicates["static_ok"] is True


def test_the_self_report_does_not_change_the_predicates(ctx: RunContext) -> None:
    """등급뿐 아니라 **술어 전체**가 같다 — 등급이 같아도 근거가 다르면 제거율 분석이 갈린다."""
    observed = (_static_signal(),)
    kwargs = {"evidences": observed, "candidate_location": CANDIDATE_LOCATION}

    _, silent = gate_node.run(ctx, finding=_finding(refs=[], finding_id="find_silent"), **kwargs)
    _, cited = gate_node.run(
        ctx, finding=_finding(refs=["ev_static"], finding_id="find_cited"), **kwargs
    )

    assert silent.predicates == cited.predicates


# --- 핀 ② 상태: E1은 7단계에 남는다 (F-3) ---------------------------------------


def test_an_uncited_finding_with_a_static_signal_stays_hypothesized(ctx: RunContext) -> None:
    """인용이 비어도 관측된 정적 신호가 있으면 E1이고 상태는 `hypothesized`다.

    러너는 **상태**로 7단계에 올린다(`gate.py` docstring) — `queued`가 되면 Critic이 못 본다.
    실제 run에서 끊긴 자리가 정확히 이것이다(7단계 이벤트 0건).
    """
    graded, result = gate_node.run(
        ctx,
        finding=_finding(refs=[]),
        evidences=(_static_signal(),),
        candidate_location=CANDIDATE_LOCATION,
    )

    assert result.grade is EvidenceGrade.E1
    assert graded.state is FindingState.HYPOTHESIZED


def test_an_uncited_finding_does_not_jump_past_e1(ctx: RunContext) -> None:
    """경로가 이어져 있어도 그래프 근거가 없으면 E2로 안 간다.

    부착이 등급을 **건너뛰게** 하지 않는다.

    실제 run이 이 모양이었다: `path_ok`는 4/5 참인데 그래프 Evidence가 타깃 풀 밖(`artifact='.'`)
    이라 `graph_ok`가 거짓이었고 등급이 E1에서 멈췄다.
    """
    full_path = [
        PathStep(step=PathStepKind.SOURCE, symbol="read_input"),
        PathStep(step=PathStepKind.SINK, symbol="memcpy"),
    ]

    _, result = gate_node.run(
        ctx,
        finding=_finding(refs=[], path=full_path),
        evidences=(_static_signal(),),
        candidate_location=CANDIDATE_LOCATION,
    )

    assert result.grade is EvidenceGrade.E1
    assert result.predicates["path_ok"] is True
    assert result.predicates["graph_ok"] is False


# --- 핀 ③ 범위: 게이트는 **받은 것만** 본다 -------------------------------------


def test_the_gate_only_sees_the_evidence_it_was_handed(ctx: RunContext) -> None:
    """저장소에 다른 Evidence가 있어도 게이트가 그것을 끌어오지 않는다.

    ⚠ **이 핀은 수정 전에도 초록이다.** 변이 M3(게이트가 run 전체 Evidence를 쓰게 바꾸기)를
    잡는 자리다 — 범위를 넓히면 그래프 근거가 섞여 `p2`가 참이 되고 등급이 한 칸 더 오른다.
    """
    elsewhere = _evidence("ev_graph", EvidenceKind.GRAPH, source="index")
    ctx.store.put(elsewhere)

    _, result = gate_node.run(
        ctx,
        finding=_finding(refs=[]),
        evidences=(),
        candidate_location=CANDIDATE_LOCATION,
    )

    assert result.grade is EvidenceGrade.E0
    assert result.predicates.get("graph_ok", False) is False


def test_a_failed_tool_record_is_attached_even_though_it_does_not_lift_the_grade(
    ctx: RunContext,
) -> None:
    """실패한 Evidence도 `evidence_refs`에 실린다 — 등급에는 안 세지만 Critic 심사 재료다.

    두 규칙이 **다른 층**에 있다는 것이 이 핀의 명제다: 부착은 `ok`를 안 보고,
    등급은 `gate.evaluate`의 `usable`이 `ok=True`만 센다. 실제 run의 `ev_0003`
    (clang-tidy · `ok=False`)이 이 모양이었다.

    (부착에 `ok` 필터를 넣는 변이가 여기서 죽는다 — 그 필터 없이는 이 핀이 유일한 방어다.)
    """
    failed = _evidence("ev_failed", EvidenceKind.STATIC, source="clang-tidy", ok=False)

    graded, result = gate_node.run(
        ctx,
        finding=_finding(refs=[]),
        evidences=(failed,),
        candidate_location=CANDIDATE_LOCATION,
    )

    assert "ev_failed" in graded.evidence_refs
    assert result.grade is EvidenceGrade.E0
    assert result.predicates["static_ok"] is False


# --- 핀 ④ Critic 입력: 인용이 비어도 근거가 실린다 ------------------------------


def test_the_critic_prompt_carries_evidence_the_model_never_cited(ctx: RunContext) -> None:
    """6단계를 지난 Finding은 인용이 비었어도 Critic 프롬프트에 그 Evidence를 싣는다.

    `critic.run`이 `gate.evidences_for(finding, …)`로 입력을 거르므로, 6단계가 부착하지 않으면
    Critic이 **빈손으로** 심사한다 — 그러면 판이 `need_evidence`를 내고 그것은 반박 실패가
    아니라 `upheld=False`다(`gate.normalize`는 `need_evidence`를 안 건드린다).
    """
    observed = (_static_signal(),)
    graded, _ = gate_node.run(
        ctx,
        finding=_finding(refs=[]),
        evidences=observed,
        candidate_location=CANDIDATE_LOCATION,
    )

    critic.run(ctx, finding=graded, evidences=observed)

    prompt = ctx.models.last_for(Role.CRITIC)
    assert "ev_static" in prompt.body


# --- 핀 ⑤ 음성: 관측이 0건이면 E0다 --------------------------------------------


def test_a_target_with_no_observed_evidence_is_still_e0(ctx: RunContext) -> None:
    """관측된 Evidence가 하나도 없으면 (b) 뒤에도 E0 · `queued`다.

    ⚠ **이 핀도 수정 전에 초록이다.** `gate.py`의 조기 반환(`if not finding.evidence_refs`)이
    사는 이유가 이것이다 — 그 분기는 「자기 신고가 비었다」가 아니라 「붙일 것이 없었다」를 민다.
    """
    graded, result = gate_node.run(
        ctx,
        finding=_finding(refs=[]),
        evidences=(),
        candidate_location=CANDIDATE_LOCATION,
    )

    assert result.grade is EvidenceGrade.E0
    assert graded.state is FindingState.QUEUED
