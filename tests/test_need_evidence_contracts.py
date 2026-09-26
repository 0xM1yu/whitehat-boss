"""`src/schemas/need_evidence.py` 계약 테스트 — PR-0(P2)의 이름·모양만 고정한다.

resolver 구현(PR-A)·러너 루프(PR-C)는 여기 없다. 여기서 고정하는 것은 세 가지다:
`HarnessModel`의 `frozen=True, extra="forbid"`가 이 모델들에도 걸려 있는가, `hop`이
1 이상으로 강제되는가, 그리고 `Resolution.fixable_count`가 형식 사유(Q5)만 세는가.
"""

import pytest
from pydantic import ValidationError

from src.schemas.enums import FIXABLE_UNFILLED_REASONS, UnfilledReason
from src.schemas.need_evidence import Resolution, ResolveQuery, RetrievedBlock, Unfilled
from src.schemas.scout_response import RequestedContext

_REQUEST = RequestedContext(
    kind="function",
    target="strdup",
    purpose="정의 확인",
    request_code="missing_request:function:strdup:정의",
)


def _resolution(**overrides: object) -> Resolution:
    base: dict[str, object] = {
        "followup_text": "RETRIEVED:\n(요청 없음)",
        "blocks": (),
        "unfilled": (),
        "already": (),
        "truncated": (),
    }
    return Resolution(**(base | overrides))


def test_resolve_query_is_frozen_and_rejects_unknown_fields() -> None:
    query = ResolveQuery(
        requests=(_REQUEST,),
        hop=1,
        target_file="a.c",
        visible={"a.c": frozenset({1, 2})},
        max_chars=15000,
    )
    with pytest.raises(ValidationError):
        query.hop = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ResolveQuery(
            requests=(),
            hop=1,
            target_file="a.c",
            visible={},
            max_chars=0,
            typo_field=1,  # type: ignore[call-arg]
        )


def test_resolve_query_hop_must_be_at_least_one() -> None:
    """hop0은 최초 판정이라 되묻기 질의를 만들지 않는다 — 1홉이 첫 되묻기다."""
    with pytest.raises(ValidationError):
        ResolveQuery(requests=(), hop=0, target_file="a.c", visible={}, max_chars=0)


def test_resolve_query_max_chars_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        ResolveQuery(requests=(), hop=1, target_file="a.c", visible={}, max_chars=-1)


def test_retrieved_block_and_unfilled_are_frozen() -> None:
    block = RetrievedBlock(name="strdup", label="function", file="a.c", lines=(10, 11))
    with pytest.raises(ValidationError):
        block.name = "other"  # type: ignore[misc]


def test_fixable_count_only_counts_format_reasons() -> None:
    """Q5 — 형식 사유(자리표시자·target 없음·식별자 없음·식별자 아님)만 센다.

    조회에 실패한 것(outside_repo·not_found·closest_name·libc)은 안 센다 — 그것은
    "잘 쓴 이름이 조회에 실패했다"이고 형식 문제가 아니다. 이 구별이 `nothing_resolved`
    (형식 사유도 0이고 블록도 0일 때만)와 그냥 "이번 홉은 못 찾았다"를 가른다.
    """
    resolution = _resolution(
        unfilled=(
            Unfilled(
                kind="context", target="", reason=UnfilledReason.NO_TARGET, text="Not resolved: …"
            ),
            Unfilled(
                kind="function",
                target="???",
                reason=UnfilledReason.NOT_IDENTIFIER,
                text="Not resolved: …",
            ),
            Unfilled(
                kind="function",
                target="frobnicate",
                reason=UnfilledReason.NOT_FOUND,
                text="Not resolved: …",
            ),
            Unfilled(
                kind="function", target="memcpy", reason=UnfilledReason.LIBC, text="Not resolved: …"
            ),
        )
    )
    assert resolution.fixable_count == 2


def test_fixable_unfilled_reasons_is_exactly_the_last_four() -> None:
    """명세 §4.3 UNFILLED 사유 8개 중 뒤 넷(자리표시자·target 없음·식별자 없음·식별자 아님)."""
    assert FIXABLE_UNFILLED_REASONS == frozenset(
        {
            UnfilledReason.PLACEHOLDER,
            UnfilledReason.NO_TARGET,
            UnfilledReason.NO_IDENTIFIER,
            UnfilledReason.NOT_IDENTIFIER,
        }
    )


def test_resolution_stores_no_derived_count_fields() -> None:
    """개수는 `blocks`·`unfilled`의 길이에서 나오는 파생값이라 필드로 안 든다(CLAUDE.md §0).

    `resolved_count` 같은 필드를 저장하면 그 값이 원본(`len(blocks)`)과 갈릴 수 있다 —
    `extra="forbid"`가 그런 필드를 애초에 거부하는지를 여기서 고정한다.
    """
    with pytest.raises(ValidationError):
        _resolution(resolved_count=0)  # type: ignore[call-arg]
