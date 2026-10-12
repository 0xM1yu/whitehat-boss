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
| E3 | `p2` **and** `any(normalize(d) == UPHOLD)` **and** `repro_spec is not None` |
| E4 | `any(e.kind in {SANITIZER, CRASH, POC_RUN} and e.exit_code not in {None, 0})` |

- **E0의 조건은 「모델이 인용을 안 했다」가 아니라 「붙일 관측이 없었다」다** (이슈 #552).
  `evidence_refs`는 모델이 쓰지만 **6단계가 판정 전에 그 타깃에서 관측한 Evidence를 붙인다**
  (`nodes/gate_node.attach_observed` — 8단계도 같은 함수를 쓴다). 그래서 이 단락에 걸리는 것은
  관측이 0건인 타깃이다. 붙이기 전에는 심사 대상의 자기 신고가 심사 여부를 정했다 —
  실측(`run_20261007_0001`): Finding 5건이 인용 0건이라 전부 E0였고 7단계 이벤트가 0건이었다.
- 등급 = `E4 if p4 else E3 if p3 else E2 if p2 else E1 if p1 else E0` (단조 하강)
- **E3는 E2를 포함한다 (이슈 #154).** 단조 하강은 「위에서부터 처음 참인 것」이지 포함이
  아니므로 포함은 술어가 직접 든다 — `p3`가 `p2`를 요구한다. 없으면 E2를 못 넘은 Finding이
  7단계 반박 실패 + 재현 명세만으로 E1에서 E3로 건너뛴다: 러너는 등급이 아니라
  상태(`hypothesized`)로 7단계에 올리므로 6단계 E1 보류분도 Critic에 간다. 경로 축은
  실서빙에서 밟혔다 — 경로 없이 E3를 받은 Finding이 `validated` 전이에서 스키마
  (`finding.py` `_promoted_states_need_backing`)에 걸려 run이 7단계에서 `ValidationError`로
  죽었고(`run_20260904_0001`) 2026-09-09에 `path_ok`만 넣었다. 그래프 근거·blocking 축은
  스키마가 안 보므로 죽지 않고 조용히 `validated`로 갈 수 있었다 — 2026-09-23에 마저 넣었다.
  술어 사이의 포함은 **`p3 ⟹ p2` 하나다** — `p2`는 `p1`을 요구하지 않고, `p4`는 경로도
  E2도 안 본다(E4는 이 포함 밖 — 도달 불가의 잠재다. F-10이 `validated`에서만 `proven`을
  허용하고 `validated`는 `path`를 강제한다. 이슈 #154는 2026-10-12 닫았다).
- `normalize()`: `verdict == REBUT and not is_grounded` → `UPHOLD`.
  이 정규화가 일어난 사건은 호출자가 `critic_normalized` trace로 남긴다
  (agent-contracts.md §2.3 "근거 없는 반박은 uphold로 처리하되 지표에 기록한다").
- `need_evidence`는 반박 실패가 **아니다** — 정규화 대상도 아니다.
- 게이트가 Evidence에서 읽는 것은 `ok`·`kind`·`location`, 그리고 **런타임 종류의 `exit_code`**
  (E4 — 0도 `None`도 아니어야 한다)다. ⚠ **`expected_signal`을 산출물과 대조하는 코드는 없다** —
  8단계 노드가 그 문자열로 하는 일은 `tool_for()`가 **도구 이름**을 고르는 것뿐이고, `Evidence.ok`는
  어댑터가 낸다 — 컨테이너는 `judge()`, Fake는 `_run_repro()`가 **대상 존재**로 낸다.
  그래서 `p4`가 참이어도 「기대한 신호가 관측됐다」는 아니다(#232).
- **E4의 종료 코드 조건은 크래시의 대용 지표이고 그 이상이 아니다 (#232).** exit 0과 미측정
  (`None`)을 크래시로 세지 않는다 — 거기까지다. 비-0은 asan·poc에서 **대상 바이너리**의 값이라
  대상이 스스로 비-0을 돌려줘도 같고, gdb에서는 **gdb 프로세스**의 값이다(대상이 스스로 끝났을
  때만 대상의 값 — `--return-child-result`). 「크래시했다」를 표현하는 필드는 `Evidence`에 없다.
  gdb를 8단계에 배선하기 전에 정상 종료인지 신호 정지인지를 `judge()` 밖으로 따로 내야 한다 —
  지금은 8단계가 gdb를 고르지 않는다(`validate.tool_for()`).
- **ambiguous(E1↔E2)**: `p1 and not p2 and (a and (not b or not c))`.
  경로는 이어졌는데 그래프 근거나 blocking 해소 중 하나가 빈 경우다.
  이때 `gate_ambiguous`를 기록하고 **E1으로 확정한다. 모델을 호출하지 않는다.**
  `p3`가 `p2`를 포함하므로 반박 실패 판정이 있어도 이 Finding은 E3가 안 된다 — 그 포함이
  없던 2026-09-23 전에는 이 표시가 E3와 함께 찍힐 수 있었다. 예외는 `p4`(E4)뿐이다.
  (architecture.md §3 6단계의 "애매한 경우에만 모델 호출"은 v1에서 열지 않는다 —
  판정 경로에 모델이 끼면 결정론이 깨지고 제거율이 조건마다 달라진다.)
"""

from collections.abc import Iterable, Sequence

from pydantic import Field

from src.schemas.common import HarnessModel, Location
from src.schemas.decision import Decision
from src.schemas.enums import EvidenceGrade, EvidenceKind, PathStepKind, ReproOrigin, Verdict
from src.schemas.evidence import Evidence
from src.schemas.finding import Finding, MissingEvidence

# 실행 흔적이 남는 Evidence 종류. E4의 조건 **하나**다 — 나머지는 `ok`(`usable`)와 아래 종료 코드다.
_RUNTIME_KINDS = frozenset({EvidenceKind.SANITIZER, EvidenceKind.CRASH, EvidenceKind.POC_RUN})

# 크래시의 흔적이 **못 되는** 종료 코드: `0`은 깨끗한 종료, `None`은 **안 쟀다**다(#232).
# ⚠ `exit_code != 0`으로 쓰지 않는다 — `None != 0`이 참이라 미측정이 크래시로 읽힌다. 안 잰 것을
# 관측으로 읽지 않는 것이 이 저장소의 되돌릴 수 없는 규칙이다(CLAUDE.md §0). `with_signal()`이
# STATIC에 쓰는 것과 같은 관례(종료 코드가 비-0이면 도구가 무언가를 보고했다)를 런타임 종류에
# 옮긴 것이다.
_NO_CRASH_EXIT = (None, 0)

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
    # `location_ok`는 6단계 행만 겹침의 뜻이 있다 - 7·8단계는 `candidate_location`을 안 넘긴다.
    # run마다의 횟수는 `RunRecord.location_overlap`(이슈 #563). 거짓은 「안 겹쳤다」·「위치 있는
    # Evidence가 없다」·「후보 위치가 없다」를 가르지 않는다.
    location_ok = candidate_location is not None and any(
        ev.location is not None and ev.location.overlaps(candidate_location) for ev in usable
    )
    path_ok = _REQUIRED_PATH_STEPS <= path_steps
    graph_ok = any(ev.kind is EvidenceKind.GRAPH for ev in usable)
    unblocked = not any(missing.blocking for missing in missings)
    upheld = any(normalize(decision) is Verdict.UPHOLD for decision in decisions)
    p2 = path_ok and graph_ok and unblocked

    predicates = {
        "p1": static_ok or location_ok,
        "p2": p2,
        "p3": p2 and upheld and finding.repro_spec is not None,
        "p4": any(
            ev.kind in _RUNTIME_KINDS and ev.exit_code not in _NO_CRASH_EXIT for ev in usable
        ),
        "static_ok": static_ok,
        "location_ok": location_ok,
        "path_ok": path_ok,
        "graph_ok": graph_ok,
        "unblocked": unblocked,
        "upheld": upheld,
        "has_repro_spec": finding.repro_spec is not None,
        # 명세가 하네스 합성인가 (#S7). **등급을 가르지 않는다** — `_DESCENDING`에 안 들어간다.
        # `gate_eval` trace에 실려 「validated인데 합성 명세로 올라왔다」를 비교표가 가르게 한다.
        "repro_is_harness": finding.repro_spec is not None
        and finding.repro_spec.origin is ReproOrigin.HARNESS,
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
