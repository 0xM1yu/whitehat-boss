"""Auditor의 「코드로 확인 불가」 요청은 어떤 ID로 썼든 E2를 막는다 (이슈 #279).

`missing_evidence`가 비지 않았다는 것은 모델이 확인 불가를 요청했다는 뜻이다(agent-contracts.md
§2.2 「"확인 불가"가 유효한 출력이다」). 발급 도장이 발급 `missing_id`와의 교집합만 남기면
발급 안 된 ID만 쓴 요청이 MissingEvidence 0건이 되고, 게이트 `unblocked`가 참이 되어 E2
blocking 축이 열린다.

노드(`auditor.run`) → 게이트(`gate_node.run`)를 이어서 돌린다. 대상은 경로 양 끝(SOURCE·SINK)과
GRAPH 근거·STATIC 신호가 있는 Finding(`VULN_TARGET`)이라, `missing_evidence` 하나만 달라지면
E2 여부가 갈린다. 그 대상에서 Fake는 `missing_evidence`를 비워 내므로 발급 ID는 출력이 아니라
프롬프트의 사실 블록에서 읽는다(`_RewritingModel`).
"""

import dataclasses

import pytest

from src.adapters.fake_model import _parse_context
from src.orchestrator import gate
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor, gate_node
from src.schemas import ProducedBy, ReproSpec, Role, Verdict
from src.schemas.decision import Decision
from src.schemas.enums import EvidenceGrade
from tests import test_nodes
from tests.test_nodes import VULN_TARGET, _triage

# 같은 픽스처를 이 모듈 이름으로 등록한다 — `import ctx`는 인자 이름과 겹쳐 ruff F811이다.
ctx = test_nodes.ctx

# 발급 안 된 서술형 식별자 — 모델이 `ids.missing_id` 대신 쓸 법한 값.
FOREIGN = "missing_caller_link"
FOREIGN_2 = "missing_sanitizer_check"
_REPRO = ReproSpec(steps=["./build.sh", "./poc"], expected_signal="AddressSanitizer: overflow")


class _RewritingModel:
    """Fake 출력의 `missing_evidence`를 `written(발급 ID)`로 바꾸고 `repro_spec`을 채워 돌려준다.

    `repro_spec`은 E3 문턱(`p3`)까지 보려고 채운다 — 등급 E2 여부에는 안 걸린다.
    """

    def __init__(self, inner: object, written) -> None:  # noqa: ANN001
        self._inner = inner
        self._written = written

    def complete(self, *, role, prompt, schema, history=()):  # noqa: ANN001, ANN201
        result = self._inner.complete(role=role, prompt=prompt, schema=schema, history=history)
        issued = _parse_context(prompt.body).ids["missing_id"]
        value = result.value.transition(missing_evidence=self._written(issued), repro_spec=_REPRO)
        return result.model_copy(update={"value": value})


def _to_gate(ctx: RunContext, written):  # noqa: ANN001, ANN202
    """0~4단계 → 5단계(발급 ID를 모델이 어떻게 쓰든) → 6단계 게이트. 결과를 한 묶음으로 준다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    stub_ctx = dataclasses.replace(ctx, models=_RewritingModel(ctx.models, written))
    finding, missings = auditor.run(
        stub_ctx, candidate=candidate, capsule=capsule, evidences=evidences
    )
    graded, result = gate_node.run(
        stub_ctx,
        finding=finding,
        evidences=evidences,
        missings=tuple(missings),
        candidate_location=candidate.location,
    )
    return stub_ctx, candidate, evidences, finding, missings, graded, result


CASES = {
    "issued": lambda issued: [issued],
    "issued_plus_foreign": lambda issued: [issued, FOREIGN],
    "foreign_only": lambda _: [FOREIGN],
    "two_foreign": lambda _: [FOREIGN, FOREIGN_2],
}


def test_precondition_an_empty_missing_evidence_reaches_e2(ctx: RunContext) -> None:
    """전제 — `missing_evidence`를 비운 같은 Finding은 E2다. 아래 차이가 그 필드 하나 탓이다.

    정규화가 빈 요청을 발급 ID로 채우지 않는다는 것도 이것이 문다 — 「누락 없음」은 blocking이
    아니다.
    """
    *_, finding, missings, graded, result = _to_gate(ctx, lambda _: [])

    assert finding.missing_evidence == [] and missings == []
    assert result.predicates["path_ok"] and result.predicates["graph_ok"]
    assert result.predicates["unblocked"] and result.grade is EvidenceGrade.E2


@pytest.mark.parametrize("case", CASES)
def test_a_nonempty_missing_evidence_blocks_e2(ctx: RunContext, case: str) -> None:
    """「코드로 확인 불가」를 요청한 Finding은 어떤 ID를 썼든 E2를 못 넘는다.

    요점은 발급 ID를 안 쓴 둘(`foreign_only`·`two_foreign`)이다 — 교집합만 남기면 그 둘이
    MissingEvidence 0건 → `unblocked` → E2가 된다.
    """
    *_, finding, missings, graded, result = _to_gate(ctx, CASES[case])

    assert any(m.blocking for m in missings), (
        f"{case}: 모델이 확인 불가를 요청했는데 blocking MissingEvidence가 0건이다 "
        f"(finding.missing_evidence={finding.missing_evidence})"
    )
    assert not result.predicates["unblocked"]
    assert result.grade is EvidenceGrade.E1, f"{case}: E2 blocking 축을 통과했다 → {result.grade}"


def test_a_cannot_confirm_request_cannot_reach_e3(ctx: RunContext) -> None:
    """「확인 불가」를 낸 Finding은 반박 실패 + 재현 명세가 있어도 E3(→ `validated`)에 못 닿는다.

    `p3 = p2 and upheld and repro_spec` — `p2`의 `unblocked`가 열리면 이 둘만으로 E3가 된다.
    """
    stub_ctx, candidate, evidences, finding, missings, *_ = _to_gate(ctx, CASES["foreign_only"])
    upheld = Decision(
        decision_id="dec_0001",
        run_id=stub_ctx.run_id,
        finding_id=finding.finding_id,
        verdict=Verdict.UPHOLD,
        rationale="반박 실패",
        evidence_refs=[],
        produced_by=ProducedBy(role=Role.CRITIC, model="stub", prompt_version="v1"),
    )

    result = gate.evaluate(
        finding,
        gate.evidences_for(finding, evidences),
        [upheld],
        missings,
        candidate_location=candidate.location,
    )

    assert result.grade is not EvidenceGrade.E3, "확인 불가를 요청한 Finding이 E3에 올랐다"


def test_the_original_request_is_kept_in_the_trace(ctx: RunContext) -> None:
    """모델이 낸 원래 ID는 `issued_override`가 싣는다 — 판정 입력(MissingEvidence)은 안 읽는다."""
    stub_ctx, *_ = _to_gate(ctx, CASES["foreign_only"])

    (override,) = [e for e in stub_ctx.trace.events if e.kind == "issued_override"]
    assert override.data["fields"] == "missing_evidence"
    assert override.data["written.missing_evidence"] == FOREIGN
