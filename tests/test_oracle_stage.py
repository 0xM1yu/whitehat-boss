"""`src/orchestrator/oracle_stage.py` — 최종 PoC 한 개를 결정론으로 지정하는 순수 선택 (#386).

선택이 **순수**해야 하는 이유: 오라클 피드백 없이 한 개를 골라야 왕복이 한 번으로 끝나고,
입력 순서·시각·난수가 안 섞여야 두 run이 같은 PoC를 낸다. 그래서 이 파일에는 서버도
tmp_path도 없다 — 자료형만 넣고 결과를 본다.
"""

from itertools import permutations

import pytest

from src.ids import sha256_digest
from src.orchestrator.oracle_stage import Designation, PovResult, designate
from src.schemas.enums import PovStopReason

_SUBMITTED = PovStopReason.SUBMITTED.value
_POC_A = b"poc A \x00\xff"
_POC_B = b"poc B \x01\xfe"


def _submitted(finding_id: str, poc: bytes = _POC_A) -> PovResult:
    return PovResult(finding_id=finding_id, stop_reason=_SUBMITTED, poc=poc)


def test_designate_returns_none_when_there_is_nothing_to_choose_from() -> None:
    """후보 0 — PoV가 안 돌았거나 아무도 submit까지 못 갔다."""
    assert designate([]) is None


def test_designate_picks_the_only_submitted_result() -> None:
    chosen = designate([_submitted("F-1")])

    assert chosen == Designation(
        finding_id="F-1",
        poc=_POC_A,
        digest=sha256_digest(_POC_A),
        stop_reason=_SUBMITTED,
        candidates=1,
    )


@pytest.mark.parametrize(
    "other",
    [
        PovStopReason.MAX_HOP.value,
        PovStopReason.REPEAT_SPEC.value,
        PovStopReason.PARSE_FAILED.value,
        PovStopReason.CONTEXT_OVERFLOW.value,
        PovStopReason.GATEWAY_ERROR.value,
        "capsule_missing",
        "aborted",
        "submitted_without_poc",
        "submitted_unpersisted",
    ],
)
def test_submitted_wins_over_every_other_stop_reason(other: str) -> None:
    """🔴 `submitted`만 「회수 가능한 **최종** PoC」다.

    다른 사유로 선 루프도 바이트를 들고 있을 수 있다 — `poc_log`가 submit 못 한 부분
    PoC도 사이드카에 남긴다. 그래도 **최종 답이 아니다.** 그것을 오라클에 내면
    「최종 PoC를 냈다」가 거짓이 된다.

    `finding_id`를 일부러 **거꾸로** 둔다(다른 사유가 더 작은 id) — 자격을 안 보고
    id만 보면 여기서 빨개진다.
    """
    results = [
        PovResult(finding_id="F-1", stop_reason=other, poc=_POC_B),
        _submitted("F-9"),
    ]

    chosen = designate(results)

    assert chosen is not None
    assert (chosen.finding_id, chosen.poc, chosen.candidates) == ("F-9", _POC_A, 1)


def test_a_tie_is_broken_by_the_lowest_finding_id() -> None:
    """동률이면 `finding_id` 오름차순 — `report.dedupe`의 `_rank`와 같은 결정론."""
    results = [_submitted("F-9", _POC_B), _submitted("F-10"), _submitted("F-2")]

    chosen = designate(results)

    assert chosen is not None
    # 문자열 오름차순이다 — `F-10` < `F-2` < `F-9`. 수치 정렬이 아니다.
    assert chosen.finding_id == "F-10"
    assert chosen.candidates == 3


def test_shuffling_the_input_does_not_change_the_designation() -> None:
    """입력 순서를 섞어도 같은 결과 — 전수(4! = 24가지)로 본다.

    러너가 finding을 어느 순서로 돌려도 같은 PoC가 나와야 한다. dict 순회 순서나
    정렬 안정성에 기댄 구현은 여기서 걸린다.
    """
    results = [
        _submitted("F-3", _POC_B),
        PovResult("F-1", PovStopReason.MAX_HOP.value, _POC_A),
        _submitted("F-2", _POC_A),
        PovResult("F-0", "submitted_without_poc", None),
    ]

    designations = {designate(order) for order in permutations(results)}

    assert len(designations) == 1
    chosen = designations.pop()
    assert chosen is not None and chosen.finding_id == "F-2"


def test_a_submit_without_retrievable_bytes_is_not_a_candidate() -> None:
    """🔴 `submitted_without_poc`·`submitted_unpersisted`만 있으면 **`None`**이다.

    앞 커밋들이 `submitted`를 그 둘로 쪼갠 이유가 이것이다 — 안 쪼개면 「최종 PoC
    있음」에 섞여 **파일이 없는데 오라클에 낼 것이 있다**고 적는다.
    """
    results = [
        PovResult("F-1", "submitted_without_poc", None),
        PovResult("F-2", "submitted_unpersisted", _POC_A),
    ]

    assert designate(results) is None


def test_a_submitted_result_without_bytes_is_not_a_candidate() -> None:
    """철자가 `submitted`인데 바이트가 `None`인 입력은 후보가 아니다.

    `_pov_result`대로면 그 조합은 안 나온다(`submitted`는 `poc is not None`일 때만
    나온다). 그래도 보는 이유는 이 함수의 입력이 **러너가 조립해 넘기는 레코드**라
    그 불변식이 여기서는 타입으로 강제되지 않는다는 것이다.
    """
    assert designate([PovResult("F-1", _SUBMITTED, None)]) is None


def test_a_zero_byte_poc_is_a_candidate() -> None:
    """🔴 `b""`는 PoC 「있음」이다 — `poc is None`으로 가르고 `not poc`로 가르지 않는다.

    `repeat count=0`이면 조립이 `b""`를 내고, 이 저장소의 입장은 0바이트 PoC도
    파일이고 libFuzzer 입력으로 유효한 답이라는 것이다(`tests/test_poc_log.py`).
    참값 검사(`if r.poc:`)는 그 자리에서 **변이로 살아남은 적이 있다**.
    """
    chosen = designate([_submitted("F-1", b"")])

    assert chosen is not None
    assert chosen.poc == b""
    assert chosen.digest == sha256_digest(b"")


def test_the_digest_is_the_digest_of_the_designated_bytes() -> None:
    """digest를 입력에서 받지 않고 **지정한 바이트에서 센다** — 둘이 어긋날 자리가 없다."""
    chosen = designate([_submitted("F-1", _POC_B)])

    assert chosen is not None
    assert chosen.digest == sha256_digest(chosen.poc)
    assert chosen.digest.startswith("sha256:")


def test_the_designation_is_immutable() -> None:
    """지정은 값이다 — 뒤 단계가 고쳐 쓰면 trace와 오라클에 낸 것이 갈린다."""
    chosen = designate([_submitted("F-1")])

    assert chosen is not None
    with pytest.raises(AttributeError):
        chosen.finding_id = "F-2"  # type: ignore[misc]
