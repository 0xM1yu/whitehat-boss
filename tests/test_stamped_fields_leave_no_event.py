"""`issued_override`가 다루는 필드는 `stamp_issued`의 `issued` 키뿐이다 (이슈 #279).

`scout_member`(`call_model`·`_call_hop`)·`slice_id`(`scout.finish`)·`produced_by`(`call_model`)는
하네스가 덮지만 **기록 없이** `model_copy`로 덮는다. 그 셋의 이탈은 `issued_override`가 없으니
「모델이 맞게 썼다」가 아니라 「안 쟀다」다 — 계약 문서(agent-contracts.md §2.1·§2.2)가 그렇게
적는 근거를 실행으로 고정한다. 그 셋에도 기록을 붙이면 이 파일과 그 문서를 같이 고친다.
"""

import dataclasses

from src.interfaces.model_client import ModelResult, Usage
from src.orchestrator.context import RunContext
from src.orchestrator.nodes import auditor, scout
from src.schemas import ProducedBy, Role
from src.schemas.scout_response import AnalysisSummary, ScoutResponse
from tests import test_nodes
from tests.test_nodes import (
    VULN_TARGET,
    _ne_session,
    _overrides,
    _RewritingModelClient,
    _triage,
)

# 같은 픽스처를 이 모듈 이름으로 등록한다 — `import ctx`는 인자 이름과 겹쳐 ruff F811이다.
ctx = test_nodes.ctx


def test_scout_member_and_slice_id_are_overwritten_with_no_event(ctx: RunContext) -> None:
    """모델이 낸 `scout_member`·`slice_id`는 덮이는데 `issued_override`는 0건이다."""
    stub = _RewritingModelClient(
        ctx.models, lambda v: v.transition(scout_member="scout_boundary", slice_id="slc_9999")
    )

    candidate, _, _ = _triage(dataclasses.replace(ctx, models=stub), VULN_TARGET)

    assert candidate.scout_member is None and candidate.slice_id is None  # 덮였다
    assert _overrides(ctx) == []  # 기록은 없다


def test_v3_scout_member_is_overwritten_with_no_event(ctx: RunContext) -> None:
    """v3(`ScoutResponse`)에서도 모델이 낸 `scout_member`는 덮이는데 `issued_override`는 0건이다.

    위 테스트는 v1 `Candidate`라 `call_model`의 덮기를 탄다. v3 래퍼에는 그 필드가 없어
    `call_model`이 건너뛰고 `_call_hop`이 덮는다 — 그 자리는 이 테스트만 지난다.
    """
    session = _ne_session()  # `scout` 한 역할 — 하네스가 찍는 값은 `None`이다

    class _Stub:
        def complete(self, *, role: Role, prompt, schema, history=()):  # noqa: ANN001, ANN202
            written = session.candidate.transition(scout_member="scout_boundary")
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
    candidate = scout.finish(ctx, next_session)

    assert candidate.scout_member is None  # 덮였다
    assert _overrides(ctx) == []  # 기록은 없다


def test_the_event_lists_only_the_issued_keys_even_when_the_stamped_ones_differ(
    ctx: RunContext,
) -> None:
    """발급값 이탈과 도장 필드 이탈이 같은 출력에 겹치면 `fields`는 발급 키만 든다."""
    stub = _RewritingModelClient(
        ctx.models,
        lambda v: v.transition(
            candidate_id="cand_9999", scout_member="scout_boundary", slice_id="slc_9999"
        ),
    )

    _triage(dataclasses.replace(ctx, models=stub), VULN_TARGET)

    (override,) = _overrides(ctx)
    assert override.data["fields"] == "candidate_id"
    written = {key for key in override.data if key.startswith("written.")}
    assert written == {"written.candidate_id"}, override.data


def test_produced_by_is_overwritten_with_no_event(ctx: RunContext) -> None:
    """모델이 낸 `produced_by`(`model="ChatGPT"`)는 덮이는데 `issued_override`는 0건이다."""
    candidate, capsule, evidences = _triage(ctx, VULN_TARGET)
    stub = _RewritingModelClient(
        ctx.models,
        lambda v: v.transition(
            produced_by=ProducedBy(role=Role.AUDITOR, model="ChatGPT", prompt_version="v9")
        ),
    )

    finding, _ = auditor.run(
        dataclasses.replace(ctx, models=stub),
        candidate=candidate,
        capsule=capsule,
        evidences=evidences,
    )

    assert finding.produced_by.model != "ChatGPT"  # 덮였다
    assert _overrides(ctx) == []  # 기록은 없다
