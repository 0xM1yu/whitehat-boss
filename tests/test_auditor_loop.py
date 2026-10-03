"""Auditor 도구 루프의 계약 — 봉투(`AuditorAction`)와 홉 루프.

`tests/test_pov_loop.py`의 Auditor 판이다. 그쪽이 PoV 봉투에 대해 보는 것(액션별 필드 ·
`tool` Literal ⊆ `TOOL_KINDS` · docstring이 요청 payload에 실린다)을 같은 규율로 본다.

**이 파일이 `tests/test_nodes.py`·`tests/test_config.py`를 건드리지 않는 이유**: Auditor
프롬프트 판과 그 meta는 다른 작업 레인이고, 그 레인이 같은 두 파일을 고친다. 허용집합
단언도 여기 둔다.
"""

import pytest
from pydantic import ValidationError

from src.adapters.fake_tools import TOOL_KINDS
from src.schemas.auditor import AuditorAction


def _finding_payload() -> dict:
    """`answer` 액션이 싣는 Finding의 최소 유효 payload."""
    return {
        "finding_id": "finding_0001",
        "run_id": "run_20260101_0001",
        "candidate_id": "candidate_0001",
        "state": "hypothesized",
        "path": [{"step": "sink", "symbol": "memcpy", "note": "길이가 상수가 아니다"}],
        "confidence": 0.5,
        "cwe": "CWE-787",
        "title": "out-of-bounds write",
        "evidence_refs": [],
        "produced_by": {"role": "auditor", "model": "fake", "prompt_version": "v6"},
    }


def _lookup_request() -> dict:
    """`lookup` 액션이 싣는 요청 하나 — `RequestedContext`와 같은 네 칸."""
    return {
        "kind": "macro",
        "target": "BUF_SIZE",
        "purpose": "버퍼 크기를 알아야 오버플로를 판정할 수 있다",
        "request_code": "L1",
    }


# --- 액션별 필드 ------------------------------------------------------------


def test_probe_needs_a_tool() -> None:
    """`probe`인데 `tool`이 없으면 거절한다 — 하네스가 무엇을 돌릴지 알 수 없다."""
    with pytest.raises(ValidationError):
        AuditorAction(action="probe", rationale="정적 신호를 먼저 본다")


def test_answer_needs_a_finding() -> None:
    """`answer`인데 `finding`이 없으면 거절한다 — 끝났다면서 결과가 없다."""
    with pytest.raises(ValidationError):
        AuditorAction(action="answer", rationale="끝")


def test_lookup_needs_at_least_one_request() -> None:
    """`lookup`인데 요청이 비면 거절한다 — resolver에 넘길 것이 없다.

    Scout NE에서 **빈 요청**이 정지 사유 2번이다. 봉투에서 막으면 그 홉이 아예 안 난다.
    """
    with pytest.raises(ValidationError):
        AuditorAction(action="lookup", requests=[], rationale="더 봐야 한다")


def test_an_action_rejects_another_actions_field() -> None:
    """액션이 쓰지 않는 필드가 실리면 거절한다.

    `extra="forbid"`는 **없는 이름**만 막는다 — 선언된 필드를 엉뚱한 액션에 채운 것은
    못 잡는다. `PovSegment._check_kind`·`PovAction._check_action`과 같은 규율이다.
    """
    with pytest.raises(ValidationError):
        AuditorAction(
            action="probe",
            tool="semgrep",
            finding=_finding_payload(),
            rationale="둘 다 싣는다",
        )


def test_each_action_accepts_its_own_field() -> None:
    """세 액션이 제 필드만으로 선다 — 위 거절 검사의 양성 쌍이다."""
    assert AuditorAction(action="probe", tool="semgrep", rationale="정적 신호").action == "probe"
    assert (
        AuditorAction(action="lookup", requests=[_lookup_request()], rationale="매크로").action
        == "lookup"
    )
    assert (
        AuditorAction(action="answer", finding=_finding_payload(), rationale="판정").action
        == "answer"
    )


# --- 화이트리스트 · 요청 payload --------------------------------------------


def test_tool_literal_stays_inside_the_tool_whitelist() -> None:
    """`AuditorAction.tool` Literal의 모든 값은 ToolRunner 화이트리스트 안이어야 한다.

    밖의 이름을 쓰면 `tool_policy` 위반 Evidence로 떨어지고, 모델은 그것을 「도구가 없다」로
    읽는다. 스키마에서 막는 것이 그 자리다.
    """
    schema = AuditorAction.model_json_schema()
    tool_values = _literal_values(schema, "tool")

    assert tool_values, "tool 필드의 Literal 값을 스키마에서 못 찾았다"
    assert tool_values <= set(TOOL_KINDS), (
        f"화이트리스트 밖 도구: {sorted(tool_values - set(TOOL_KINDS))}"
    )


def test_description_in_the_request_has_no_repo_internal_references() -> None:
    """`AuditorAction`의 docstring은 `response_format.json_schema.schema.description`으로
    **모든 Auditor 루프 요청에 실린다**(`openai_compat`). 커밋 SHA·PR·이슈 번호·소스 파일
    경로가 모델 입력에 들어가면 안 된다 — 그런 이력은 클래스 **밖** `#` 주석에 둔다.
    `PovAction`의 같은 검사가 선례다.
    """
    description = AuditorAction.model_json_schema().get("description", "")

    for fragment in ("#419", "D99", "D84", "container_tools", "symbol_resolver", "1fa2182"):
        assert fragment not in description, f"요청 payload에 내부 참조가 실린다: {fragment!r}"


def _literal_values(schema: dict, field: str) -> set[str]:
    """`model_json_schema()`에서 `field`의 Literal 값 집합. `| None`이면 `anyOf`에 든다."""
    node = schema["properties"][field]
    if "enum" in node:
        return set(node["enum"])
    for branch in node.get("anyOf", []):
        if "enum" in branch:
            return set(branch["enum"])
    return set()
