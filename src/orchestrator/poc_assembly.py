"""PovSpec → PoC 바이트. 순수·결정론 (council 2026-09-30, fork B-iii).

`gate.evaluate`와 같은 부류다 — 상태·부작용 없이 입력만으로 값을 낸다. 조립을 모델 밖
순수 함수에 두는 것이 결정론의 근거다: 같은 스펙은 언제나 같은 바이트를 낸다. 그래서
PoV 루프(모델이 스펙을 낸다)는 비결정적이어도 **바이트 산출은 재현 가능**하다.

구체 어댑터 모듈을 import하지 않는다(orchestrator 규약, tests/test_policy.py).
"""

from src.schemas.pov import PovSegment, PovSpec


def _segment_bytes(seg: PovSegment) -> bytes:
    """검증된 세그먼트 하나를 바이트로. `kind`별 필드는 `PovSegment._check_kind`가
    보장하므로 여기서는 그 불변식을 신뢰한다(assert로 명문화)."""
    if seg.kind == "literal":
        assert seg.hex is not None  # _check_kind 보장
        return bytes.fromhex(seg.hex)
    if seg.kind == "repeat":
        assert seg.byte is not None and seg.count is not None
        return bytes([seg.byte]) * seg.count
    # kind == "int"
    assert seg.value is not None and seg.size is not None and seg.endian is not None
    return seg.value.to_bytes(seg.size, seg.endian)


def assemble_poc(spec: PovSpec) -> bytes:
    """PovSpec의 세그먼트를 선언 순서로 이어 붙여 PoC 바이트를 만든다."""
    out = bytearray()
    for seg in spec.segments:
        out += _segment_bytes(seg)
    return bytes(out)
