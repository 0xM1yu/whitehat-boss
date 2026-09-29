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

# PoC 총 바이트 상한 — 모델 출력에서 온 스펙이 조립 때 프로세스 메모리를 폭주시키지 못하게
# 하는 **안전 한도**다(실측이 아니라 신뢰 경계 입력 검증). `assemble_poc`는 검증을 통과한
# PovSpec만 받으므로(pydantic가 생성 시 검증) 상한을 여기서 걸면 조립기는 순수·단순한 채로
# 둔다. CyberGym PoC 입력은 보통 바이트~KB라(docs/research/2026-09-29-cybergym-pov-design.md)
# 8 MiB면 넉넉하다. 부족하면 실측으로 올린다 — 그때 config로 옮길 수 있다(`_MAX_INT_SIZE`처럼
# 지금은 지역 상수). **없으면** `count`·`hex`가 상한이 없어 15자짜리 스펙이 테라바이트
# 할당을 부른다(적대적 리뷰 2026-09-30 HIGH).
_MAX_POC_BYTES = 8 * 1024 * 1024
# 세그먼트 개수 상한 — 작은 세그먼트를 수없이 쌓아 파싱·객체 수를 불리는 벡터를 막는다.
_MAX_SEGMENTS = 4096

# `kind`별로 그 kind가 쓰는 필드. `_check_kind`가 다른 kind의 필드가 섞였는지 본다.
_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "literal": ("hex",),
    "repeat": ("byte", "count"),
    "int": ("value", "size", "endian"),
}


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
    hex: str | None = Field(default=None, max_length=2 * _MAX_POC_BYTES)
    byte: int | None = Field(default=None, ge=0, le=255)
    # 한 세그먼트가 총 상한을 혼자 넘지 못하게 `le`로 못 박는다. 총합은 `PovSpec._check_total_size`.
    count: int | None = Field(default=None, ge=0, le=_MAX_POC_BYTES)
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
        # 다른 kind의 필드가 섞이면 거절한다 — docstring이 약속한 것이 이것이다. `extra="forbid"`는
        # **없는 필드 이름**만 막지, 선언된 필드를 엉뚱한 kind에 채운 것은 못 잡는다(적대적 리뷰
        # 2026-09-30: `kind="literal"`인데 byte·count가 실려도 통과했다 — 조용히 무시됐다).
        stray = sorted(
            name
            for other_kind, fields in _FIELDS_BY_KIND.items()
            if other_kind != self.kind
            for name in fields
            if getattr(self, name) is not None
        )
        if stray:
            raise ValueError(
                f"{self.kind} 세그먼트에 다른 kind의 필드가 섞였다: {stray} — "
                f"{self.kind}가 쓰는 필드는 {list(_FIELDS_BY_KIND[self.kind])}뿐이다"
            )
        return self


class PovSpec(HarnessModel):
    """PoV 역할이 내는 PoC 구성 스펙. `assemble_poc`가 바이트로 조립한다.

    `segments`는 **선언 순서대로** 이어 붙는다. `rationale`은 왜 이 입력이 취약 경로를
    친다고 보는지의 한 줄 근거 — 사람 검수·trace용이고 바이트에는 안 들어간다.
    """

    segments: list[PovSegment] = Field(min_length=1, max_length=_MAX_SEGMENTS)
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def _check_total_size(self) -> "PovSpec":
        """세그먼트 바이트 **합**이 `_MAX_POC_BYTES`를 넘지 않는지 — 바이트를 만들지 않고 길이만
        센다. 한 세그먼트의 `count` `le`는 단건을, 이 검사는 여러 세그먼트가 합쳐 넘치는 것을
        막는다. 조립 전(모델 출력 검증 시점)에 걸려야 `call_model`이 재시도로 더 작은 스펙을
        받을 수 있다."""
        total = 0
        for seg in self.segments:
            if seg.kind == "literal":
                total += len(seg.hex or "") // 2  # 검증된 짝수 길이 hex
            elif seg.kind == "repeat":
                total += seg.count or 0
            else:  # int — size는 ge=1이라 항상 있다
                total += seg.size or 0
        if total > _MAX_POC_BYTES:
            raise ValueError(
                f"PoC 총 바이트 {total}가 상한 {_MAX_POC_BYTES}를 넘는다 — 스펙을 줄인다"
            )
        return self
