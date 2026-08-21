"""Evidence Gate 술어 (architecture.md §4, 계획 §4).

**순수 함수다. 상태를 모르고 등급만 판정한다.** 등급을 전이로 매핑하는 것은 노드 몫이고
(state_machine.py 표 F-2~F-4·F-8·F-10), trace 기록도 호출자가 한다 — 그래야 같은 술어를
6·7·8단계에서 다시 돌려도 부작용이 겹치지 않는다.

한 번에 매겨지는 등급이 아니다. E3은 Critic 출력이, E4는 도구 검증 로그가 있어야
성립하므로 `evaluate()`는 세 단계에서 각각 호출된다(`stages.GATE_STAGES`).

**그 문장이 오래 거짓이었다.** 8단계에 호출이 없어 E4는 그것을 성립시킬 수 있는 유일한
단계에서 판정되지 못했고, `GATE_STAGES`는 아무도 참조하지 않는 상수였다(W3, 2026-08-21
해소). 지금은 `RunContext.evaluate_gate()`가 그 상수로 호출 단계를 **강제한다** — 문서로
부탁하는 대신 코드가 막는다.

## 판정 조건

아래에서 `e`는 **`ok=True`인** Evidence, `m`은 `MissingEvidence`, `d`는 `Decision`이다.
실패한 Evidence(`ok=False`)는 근거로 세지 않는다 — 실패 기록이지 관찰된 사실이 아니다.

| 등급 | 코드 조건 |
| --- | --- |
| E0 | `not finding.evidence_refs` |
| E1 | `any(e.kind == STATIC)` **or** `any(e.location.overlaps(cand.location))` |
| E2 | `{SOURCE, SINK} ⊆ path.steps` **and** `any(e.kind == GRAPH)` **and** `not any(m.blocking)` |
| E3 | `any(normalize(d) == UPHOLD)` **and** `repro_spec is not None` |
| E4 | `any(e.kind in {SANITIZER, CRASH, POC_RUN})` |

- 등급 = `E4 if p4 else E3 if p3 else E2 if p2 else E1 if p1 else E0` (단조 하강)
- `normalize()`: `verdict == REBUT and not is_grounded` → `UPHOLD`.
  이 정규화가 일어난 사건은 호출자가 `critic_normalized` trace로 남긴다
  (agent-contracts.md §2.3 "근거 없는 반박은 uphold로 처리하되 지표에 기록한다").
- `need_evidence`는 반박 실패가 **아니다** — 정규화 대상도 아니다.
- `expected_signal` 문자열 매칭은 8단계 노드가 하고 `Evidence.ok`에 반영한다.
  게이트는 `ok`만 본다.
- **ambiguous(E1↔E2)**: `p1 and not p2 and (a and (not b or not c))`.
  경로는 이어졌는데 그래프 근거나 blocking 해소 중 하나가 빈 경우다.
  이때 `gate_ambiguous`를 기록하고 **E1으로 확정한다. 모델을 호출하지 않는다.**
  (architecture.md §3 6단계의 "애매한 경우에만 모델 호출"은 v1에서 열지 않는다 —
  판정 경로에 모델이 끼면 결정론이 깨지고 제거율이 조건마다 달라진다.)
"""

from collections.abc import Iterable, Sequence

from pydantic import Field

from src.schemas.common import HarnessModel, Location
from src.schemas.decision import Decision
from src.schemas.enums import EvidenceGrade, EvidenceKind, PathStepKind, Verdict
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence

# 실행 흔적이 남는 Evidence 종류. E4의 조건이다.
_RUNTIME_KINDS = frozenset({EvidenceKind.SANITIZER, EvidenceKind.CRASH, EvidenceKind.POC_RUN})

# E2가 요구하는 경로 양 끝.
_REQUIRED_PATH_STEPS = frozenset({PathStepKind.SOURCE, PathStepKind.SINK})

# 단조 하강 판정 순서. 앞에서부터 처음 참인 등급이 결과다.
_DESCENDING = (
    (EvidenceGrade.E4, "p4"),
    (EvidenceGrade.E3, "p3"),
    (EvidenceGrade.E2, "p2"),
    (EvidenceGrade.E1, "p1"),
)

# 등급 비교용 순서. `at_least(grade, E3)` 같은 판정에 쓴다.
GRADE_ORDER = (
    EvidenceGrade.E0,
    EvidenceGrade.E1,
    EvidenceGrade.E2,
    EvidenceGrade.E3,
    EvidenceGrade.E4,
)


class GateResult(HarnessModel):
    """판정 결과. `predicates`를 함께 돌려주는 것은 trace에 근거를 남기기 위해서다.

    등급만 남기면 나중에 "왜 E1이었나"를 되짚을 수 없고, 게이트 임계 조정의 근거가
    사라진다(evaluation.md §5.1 제거율 분석).
    """

    grade: EvidenceGrade
    predicates: dict[str, bool] = Field(default_factory=dict)
    ambiguous: bool = False


def at_least(grade: EvidenceGrade, floor: EvidenceGrade) -> bool:
    return GRADE_ORDER.index(grade) >= GRADE_ORDER.index(floor)


def normalize(decision: Decision) -> Verdict:
    """근거 없는 `rebut`을 `uphold`로 정규화한다 (agent-contracts.md §2.3).

    `need_evidence`는 반박이 아니므로 건드리지 않는다.
    """
    if decision.verdict is Verdict.REBUT and not decision.is_grounded:
        return Verdict.UPHOLD
    return decision.verdict


def was_normalized(decision: Decision) -> bool:
    """`critic_normalized` trace를 남겨야 하는 판정인가. 기록은 호출자가 한다."""
    return decision.verdict is Verdict.REBUT and not decision.is_grounded


def evaluate(
    finding: Finding,
    evidences: Sequence[Evidence],
    decisions: Sequence[Decision] = (),
    missings: Sequence[MissingEvidence] = (),
    *,
    candidate_location: Location | None = None,
) -> GateResult:
    """등급을 매긴다. 인자는 이 Finding에 해당하는 것만 걸러서 넘긴다.

    `candidate_location`은 E1의 위치 일치 판정에 쓴다. 없으면 정적 신호 조건만 본다 —
    후보 위치를 모른다고 근거 있는 Finding을 E0으로 떨어뜨리지는 않는다.
    """
    if not finding.evidence_refs:
        return GateResult(grade=EvidenceGrade.E0, predicates={"p0": True})

    usable = [ev for ev in evidences if ev.ok]
    path_steps = {step.step for step in finding.path}

    static_ok = any(ev.kind is EvidenceKind.STATIC for ev in usable)
    location_ok = candidate_location is not None and any(
        ev.location is not None and ev.location.overlaps(candidate_location) for ev in usable
    )
    path_ok = _REQUIRED_PATH_STEPS <= path_steps
    graph_ok = any(ev.kind is EvidenceKind.GRAPH for ev in usable)
    unblocked = not any(missing.blocking for missing in missings)
    upheld = any(normalize(decision) is Verdict.UPHOLD for decision in decisions)

    predicates = {
        "p1": static_ok or location_ok,
        "p2": path_ok and graph_ok and unblocked,
        "p3": upheld and finding.repro_spec is not None,
        "p4": any(ev.kind in _RUNTIME_KINDS for ev in usable),
        "static_ok": static_ok,
        "location_ok": location_ok,
        "path_ok": path_ok,
        "graph_ok": graph_ok,
        "unblocked": unblocked,
        "upheld": upheld,
        "has_repro_spec": finding.repro_spec is not None,
    }
    grade = next((value for value, key in _DESCENDING if predicates[key]), EvidenceGrade.E0)
    ambiguous = (
        predicates["p1"] and not predicates["p2"] and path_ok and not (graph_ok and unblocked)
    )
    return GateResult(grade=grade, predicates=predicates, ambiguous=ambiguous)


def evidences_for(finding: Finding, evidences: Iterable[Evidence]) -> list[Evidence]:
    """Finding이 참조하는 Evidence만 고른다. 순서는 `evidence_refs` 순서를 따른다."""
    by_id = {evidence.evidence_id: evidence for evidence in evidences}
    return [by_id[ref] for ref in finding.evidence_refs if ref in by_id]
