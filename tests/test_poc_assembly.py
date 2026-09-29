"""PoV 구성 스펙(PovSpec) → 바이트 조립기의 계약.

council verdict(2026-09-30, fork B-iii): 모델은 바이트를 직접 내지 않고 **구성 스펙**을
내며, 순수 함수 `assemble_poc`가 결정론적으로 바이트를 만든다. 조립은 모델 경로 밖이라
같은 스펙은 항상 같은 바이트다(gate.py가 판정을 순수 함수로 둔 것과 같은 규율).
"""

import pytest
from pydantic import ValidationError

from src.orchestrator.poc_assembly import assemble_poc
from src.schemas.pov import PovSegment, PovSpec


def _spec(*segments: PovSegment) -> PovSpec:
    return PovSpec(segments=list(segments), rationale="test")


def test_literal_segment_decodes_hex() -> None:
    out = assemble_poc(_spec(PovSegment(kind="literal", hex="deadbeef")))
    assert out == bytes([0xDE, 0xAD, 0xBE, 0xEF])


def test_repeat_segment_repeats_byte() -> None:
    out = assemble_poc(_spec(PovSegment(kind="repeat", byte=0x41, count=64)))
    assert out == b"A" * 64


def test_int_segment_little_and_big_endian() -> None:
    little = assemble_poc(_spec(PovSegment(kind="int", value=1, size=4, endian="little")))
    big = assemble_poc(_spec(PovSegment(kind="int", value=1, size=4, endian="big")))
    assert little == b"\x01\x00\x00\x00"
    assert big == b"\x00\x00\x00\x01"


def test_segments_concatenate_in_declared_order() -> None:
    out = assemble_poc(
        _spec(
            PovSegment(kind="repeat", byte=0x41, count=8),
            PovSegment(kind="int", value=0xDEADBEEF, size=4, endian="little"),
        )
    )
    assert out == b"A" * 8 + b"\xef\xbe\xad\xde"


def test_assembly_is_deterministic() -> None:
    spec = _spec(
        PovSegment(kind="literal", hex="00ff"),
        PovSegment(kind="repeat", byte=0, count=16),
    )
    assert assemble_poc(spec) == assemble_poc(spec)


def test_spec_requires_at_least_one_segment() -> None:
    with pytest.raises(ValidationError):
        PovSpec(segments=[], rationale="empty")


def test_int_value_must_fit_in_size() -> None:
    with pytest.raises(ValidationError):
        PovSegment(kind="int", value=256, size=1, endian="little")


def test_literal_requires_valid_even_length_hex() -> None:
    with pytest.raises(ValidationError):
        PovSegment(kind="literal", hex="xyz")


def test_repeat_requires_byte_and_count() -> None:
    with pytest.raises(ValidationError):
        PovSegment(kind="repeat", byte=0x41)  # count missing


def test_int_requires_size_and_endian() -> None:
    with pytest.raises(ValidationError):
        PovSegment(kind="int", value=1)  # size·endian missing


def test_literal_requires_hex_present() -> None:
    with pytest.raises(ValidationError):
        PovSegment(kind="literal")  # hex missing


def test_segment_rejects_cross_kind_fields() -> None:
    # kind가 literal인데 repeat 필드(count)가 섞이면 조용히 무시되지 않고 실패한다
    # (적대적 리뷰 2026-09-30: docstring이 약속한 배타성이 강제되지 않았다).
    with pytest.raises(ValidationError):
        PovSegment(kind="literal", hex="dead", count=3)


def test_repeat_count_is_bounded() -> None:
    # 단건 count가 PoC 상한을 넘으면 스키마에서 거절한다 — 모델 출력이 거대 할당을 못 부른다.
    from src.schemas.pov import _MAX_POC_BYTES

    with pytest.raises(ValidationError):
        PovSegment(kind="repeat", byte=0, count=_MAX_POC_BYTES + 1)


def test_total_poc_size_is_bounded() -> None:
    # 단건은 le를 통과해도 세그먼트 합이 상한을 넘으면 PovSpec이 거절한다(바이트는 안 만든다).
    from src.schemas.pov import _MAX_POC_BYTES

    with pytest.raises(ValidationError):
        PovSpec(
            segments=[
                PovSegment(kind="repeat", byte=0, count=_MAX_POC_BYTES),
                PovSegment(kind="repeat", byte=0, count=_MAX_POC_BYTES),
            ],
            rationale="over the total cap",
        )
