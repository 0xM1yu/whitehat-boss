"""6단계 등급이 Auditor의 **자기 신고**(`evidence_refs`)에 안 달렸다 (D106 (b) · 이슈 #552).

**명제 — 같은 Evidence를 관측했으면 모델이 인용했든 안 했든 등급이 같다.**
철자가 아니라 성질을 문다: `evidence_refs`를 비운 Finding과 전부 채운 Finding을 **같은 풀**로
돌려 등급이 같음을 단언한다(`tests/test_gate.py`의 조합 전수와 같은 모양).
⚠ 「둘 다 E0」로도 등식은 성립하므로 **E1 이상임을 같이 단언한다** — 안 그러면 수정 전에도 초록이다.

⚠ **Fake로는 이 결함을 만들 수 없다.** `src/adapters/fake_model.py`의 Auditor 자리 **둘**
(`_build_finding` · `_build_audit_procedure`)이 `evidence_refs=[ref.evidence_id for ref in
context.evidence]`라 **항상 전부 인용한다**(세 번째 같은 꼴은 `_build_candidate` — 4단계
Scout의 `Candidate.evidence_refs`라 이 결함과 무관하다). 그래서
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
from src.orchestrator.stages import Stage
from src.orchestrator.trace import EventKind, TraceWriter
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
    """🔴 **결함 특성화 핀 — 이슈 #576.** 그래프 근거가 없으면 E2로 안 간다(손으로 넘긴 풀 기준).

    부착이 등급을 **건너뛰게** 하지 않는다.

    ⚠ **⏵ 2026-10-10 — 앞 판의 docstring은 거짓이었다**(적대적 리뷰 · §2.1). 그것은
    *"실제 run이 이 모양이었다 — 그래프 Evidence가 타깃 풀 밖(`artifact='.'`)이라 `graph_ok`가
    거짓이었고 E1에서 멈췄다"*고 적었다. **러너 풀은 그렇지 않다** — `runner.py:1190`이 run 전역
    graph를 만들고 `_artifact_signals`가 `(graph, *found)`를 돌려주므로 **모든 타깃 풀에 들어간다.**
    그 run의 **실측은 `5/5 E0`**이고(`evidence_refs`가 비어 `p1`~`p4`를 계산조차 않았다) E1·E2
    분포는 **재구성한 풀의 계산값**이다.

    🔴 **그래서 이 핀은 「현재 동작이 정답」이라는 뜻이 아니다** — 손으로 넘긴 풀에서의 거동을
    고정할 뿐이다. 러너 풀 기준은 아래 `test_the_runner_pool_always_carries_the_run_wide_graph`가
    문다. 이슈 #576이 그 결함을 들고 D106의 「재검토 발동 조건」이 다시 보는 시점을 적는다
    (D106 2026-10-12 닫음 — 발동 조건은 #576 본문에도 있다).
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


def test_only_the_handed_evidence_lands_in_evidence_refs(ctx: RunContext) -> None:
    """`evidence_refs`에 들어가는 것은 **넘겨받은 Evidence뿐**이다.

    등급 쪽은 `gate.evidences_for`가 모르는 참조를 조용히 버려서 지켜지지만, `evidence_refs`
    자체는 아무도 안 본다 — 그 값이 `cli` SARIF·리포트 「근거 N건」·승격 불변식·그리고
    러너의 `_grades_by_finding`(독립 게이트 호출)으로 그대로 흐른다. 그래서 refs 축을 따로 문다.

    (부착이 저장소를 읽게 바꾸는 변이가 등급 핀 전부를 통과한다 — 그 변이를 잡는 자리다.)
    """
    elsewhere = _evidence("ev_other", EvidenceKind.GRAPH, source="index")
    ctx.store.put(elsewhere)
    handed = (_static_signal(),)

    graded, _ = gate_node.run(
        ctx,
        finding=_finding(refs=[]),
        evidences=handed,
        candidate_location=CANDIDATE_LOCATION,
    )

    assert list(graded.evidence_refs) == ["ev_static"]


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


# --- 🔴 러너 풀 기준 — 결함 특성화 핀 (이슈 #576 · council 2026-10-10) -------------
#
# 위 핀들은 **손으로 넘긴 풀**을 본다. 그래서 「러너가 만드는 풀이 어떤 모양인가」를
# 못 잡았고, D106 산문이 「그래프 Evidence는 타깃 풀 밖」이라 적은 것이 네 라운드를 살아남았다.
# 아래 둘은 러너 쪽에서 끌어와 **그 산문이 다시 들어오면 빨개진다.**
#
# 🔴 **이 둘은 「현재 동작이 정답」이라는 뜻이 아니다.** 결함을 고정(characterize)할 뿐이고,
#    그 결함은 이슈 #576이 든다. D106의 「재검토 발동 조건」이 다시 보는 시점을 적는다
#    (D106 2026-10-12 닫음 — 발동 조건은 #576 본문에도 있다).


def test_the_runner_pool_always_carries_the_run_wide_graph() -> None:
    """🔴 **결함 특성화 핀 — 이슈 #576.** 러너 풀에는 run 전역 GRAPH가 **늘** 들어간다.

    `_artifact_signals`가 `(graph, *found)`를 돌려주고 그것이 그대로 `_Target.evidences`가 된다.
    `artifact_id`로 걸러지지 않는다 — D106 앞 판이 *"`artifact_id`가 `.`이라 타깃 풀 밖"*이라
    적은 것이 거짓인 이유다(§2.1).

    그래서 **인덱서가 성공하는 동안 `graph_ok`는 전 타깃에서 참**이고 E2가
    `path_ok ∧ unblocked`로 줄어든다. `path_ok`는 **모델이 쓴 `finding.path`**다.
    """
    import inspect

    from src.orchestrator import runner

    source = inspect.getsource(runner._artifact_signals)

    assert "return (graph, *found)" in source, (
        "러너가 run 전역 graph를 타깃 풀에 넣는 방식이 바뀌었다 — D106과 이슈 #576의 서술을 "
        "같이 고친다"
    )


def test_the_grade_tracks_the_indexer_not_the_evidence_when_the_graph_fails() -> None:
    """🔴 **인덱서 실패는 「부착 범위를 좁힌 쪽」의 미리보기다** (council 2026-10-10 Critic).

    인덱서가 실패하면 그 Evidence 하나가 `ok=False`가 되고 `gate.evaluate`의 `usable`이 그것을
    뺀다 → `graph_ok`가 **전 타깃에서 동시에** 거짓이다. 전원 E1 이하, E3·E4 불가.

    🔴 **그 실패는 타깃별로 독립이 아니라 run 단위로 상관된다.** 그래서 등급이 근거 품질이 아니라
    **인덱서 가동률**을 잰다 — 비교표에 인덱서 상태 열이 없으면 run 간 등급 비교가 무의미하다
    (`docs/evaluation.md` · 이슈 #576).

    ⚠ **인덱서 실패율은 0 관측이다**(§0). 이 핀은 그 비율을 주장하지 않고 **실패했을 때의 등급**만
    고정한다.
    """
    from src.orchestrator.gate import evaluate

    path = [
        PathStep(step=PathStepKind.SOURCE, symbol="read_input"),
        PathStep(step=PathStepKind.SINK, symbol="memcpy"),
    ]
    finding = _finding(refs=["ev_graph", "ev_static"], path=path)
    static = _static_signal()
    graph_up = _evidence("ev_graph", EvidenceKind.GRAPH, source="index", ok=True)
    graph_down = _evidence("ev_graph", EvidenceKind.GRAPH, source="index", ok=False)

    up = evaluate(finding, [graph_up, static], missings=[], decisions=[])
    down = evaluate(finding, [graph_down, static], missings=[], decisions=[])

    assert up.grade is EvidenceGrade.E2, "인덱서가 서면 E2다(구조상 — path_ok 이고 unblocked)"
    assert down.grade is EvidenceGrade.E1, (
        "인덱서가 죽으면 같은 근거·같은 경로인데 등급이 내려간다 — 등급이 인덱서 가동률을 "
        "잰다는 사실이 바뀌었으면 이슈 #576과 D106을 같이 고친다"
    )


# --- 핀 ③ 계측: 부착 전 모델 인용 수가 `gate_eval`에 남는다 (이슈 #554) ---------------------


def _gate_evals(ctx: RunContext, stage: Stage = Stage.GATE) -> list:
    return [
        e for e in ctx.trace.events if e.kind is EventKind.GATE_EVAL and e.stage == int(stage)
    ]


def test_cited_by_model_counts_the_refs_before_the_harness_attaches(ctx: RunContext) -> None:
    """모델 인용 2건 + 하네스 관측 3건 → 저장된 Finding은 5건이고 `gate_eval`은 **2**다.

    부착이 `state.db`의 목록을 덮으므로 저장소로는 이 2를 못 되찾는다 - 그게 이 키의 존재 이유다.
    """
    pool = (
        _evidence("ev_a", EvidenceKind.STATIC, source="semgrep", location=SIGNAL_LOCATION),
        _evidence("ev_b", EvidenceKind.STATIC, source="clang-tidy"),
        _evidence("ev_c", EvidenceKind.STATIC, source="cppcheck"),
        _evidence("ev_d", EvidenceKind.STATIC, source="flawfinder"),
        _evidence("ev_e", EvidenceKind.STATIC, source="infer"),
    )
    finding = _finding(refs=["ev_a", "ev_b"])

    graded, _ = gate_node.run(
        ctx, finding=finding, evidences=pool, candidate_location=CANDIDATE_LOCATION
    )

    (row,) = _gate_evals(ctx)
    assert row.data["cited_by_model"] == 2
    assert len(graded.evidence_refs) == 5
    stored = ctx.store.get("finding", finding.finding_id)
    assert len(stored.evidence_refs) == 5, "전제 - 저장소 행은 덮였다(그래서 이 키가 필요하다)"


def test_cited_by_model_zero_is_a_measured_zero(ctx: RunContext) -> None:
    """인용 0건이면 키가 있고 값이 0이다 - 「안 쟀다」(키 부재)와 갈린다."""
    gate_node.run(
        ctx,
        finding=_finding(refs=[]),
        evidences=(_static_signal(),),
        candidate_location=CANDIDATE_LOCATION,
    )

    (row,) = _gate_evals(ctx)
    assert "cited_by_model" in row.data
    assert row.data["cited_by_model"] == 0


def test_cited_by_model_is_absent_when_the_caller_does_not_measure(ctx: RunContext) -> None:
    """`evaluate_gate`를 인자 없이 부르면 키가 없다 - `None`·`0`으로 채우지 않는다(CLAUDE.md §0)."""
    ctx.evaluate_gate(_finding(refs=["ev_a"]), (_static_signal(),), stage=Stage.GATE)

    (row,) = _gate_evals(ctx)
    assert "cited_by_model" not in row.data
