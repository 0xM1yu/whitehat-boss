"""PoV 역할의 출력 — PoC 바이트 구성 스펙.

council verdict(2026-09-30, fork B-iii): 모델은 PoC 바이트를 직접 내지 않는다. 대신
**어떻게 바이트를 조립할지**를 스펙으로 내고, 순수 함수 `orchestrator.poc_assembly.assemble_poc`가
결정론적으로 바이트를 만든다. 이유:
- **결정론** — 조립을 모델 밖에 두면 같은 스펙은 항상 같은 바이트다(gate.py가 판정을 순수
  함수로 둔 것과 같은 규율).
- **strict-JSON 문법 열화 회피** — 큰 base64 문자열을 구조적 출력에 물리면 문법 마스크가
  출력을 깎는 실측이 이 저장소에 있다(enums.Verdict.from_text 주석). 스펙은 작은 구조다.
- **임의 코드 실행 회피** — 모델이 쓴 스크립트를 샌드박스에서 돌리는 방식(generator-script)은
  새 신뢰면이라 별도 리뷰로 미뤘다. 스펙+조립기는 그 능력을 안 연다.

⚠ **v1 최소 스펙이다.** 패딩·오버플로·정수 필드 같은 흔한 경우만 담는다. 실포맷(구조적
파서 입력 등)에 스펙 표현력이 부족한지는 실측(호스트 필요)으로 정하고, 부족하면 스펙을
넓히거나 generator-script를 별도로 검토한다.
"""

from typing import Literal

from pydantic import Field, model_validator

from src.schemas.common import HarnessModel, NonEmptyStr

# 정수 세그먼트가 담을 수 있는 최대 바이트 폭. 8바이트(64비트)면 흔한 길이/포인터 필드를
# 덮는다. 더 큰 값이 필요하면 literal 세그먼트로 직접 쓴다.
_MAX_INT_SIZE = 8


class PovSegment(HarnessModel):
    """PoC 바이트의 한 조각. `kind`가 어느 필드를 쓰는지 정한다.

    - `literal`: `hex`(짝수 길이 16진 문자열)를 그대로 바이트로.
    - `repeat`: `byte`(0~255)를 `count`번 반복.
    - `int`: `value`를 `size`바이트 `endian`으로 부호 없이 인코딩.

    한 스키마에 세 종류의 필드를 함께 두되, `_check_kind`가 그 `kind`에 맞는 필드만
    채워졌는지 검증한다. 다른 kind의 필드가 섞이면 조용히 무시되는 대신 실패한다
    (`HarnessModel`이 `extra="forbid"`인 것과 같은 정신 — 오타·혼동을 막는다).
    """

    kind: Literal["literal", "repeat", "int"]
    hex: str | None = None
    byte: int | None = Field(default=None, ge=0, le=255)
    count: int | None = Field(default=None, ge=0)
    value: int | None = Field(default=None, ge=0)
    size: int | None = Field(default=None, ge=1, le=_MAX_INT_SIZE)
    endian: Literal["little", "big"] | None = None

    @model_validator(mode="after")
    def _check_kind(self) -> "PovSegment":
        if self.kind == "literal":
            if not self.hex:
                raise ValueError("literal 세그먼트는 hex가 필요하다")
            try:
                bytes.fromhex(self.hex)
            except ValueError as exc:
                raise ValueError(f"hex가 유효한 짝수 길이 16진이 아니다: {self.hex!r}") from exc
        elif self.kind == "repeat":
            if self.byte is None or self.count is None:
                raise ValueError("repeat 세그먼트는 byte와 count가 필요하다")
        elif self.kind == "int":
            if self.value is None or self.size is None or self.endian is None:
                raise ValueError("int 세그먼트는 value·size·endian이 필요하다")
            if self.value >= 256**self.size:
                raise ValueError(
                    f"value {self.value}는 {self.size}바이트에 담기지 않는다"
                )
        return self


class PovSpec(HarnessModel):
    """PoV 역할이 내는 PoC 구성 스펙. `assemble_poc`가 바이트로 조립한다.

    `segments`는 **선언 순서대로** 이어 붙는다. `rationale`은 왜 이 입력이 취약 경로를
    친다고 보는지의 한 줄 근거 — 사람 검수·trace용이고 바이트에는 안 들어간다.
    """

    segments: list[PovSegment] = Field(min_length=1)
    rationale: NonEmptyStr
