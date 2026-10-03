"""Auditor `SOURCE`의 `STORES` 블록이 인덱스와 주고받는 두 값 (옵션 `auditor_stores`).

`StoreTarget`은 **어느 이름의 쓰기 지점을 찾나**, `StoreSite`는 **찾은 쓰기 지점**이다.
`GraphIndex.stores_of`(어댑터)가 만들고 `orchestrator/source_stores.py`가 읽는다 — 러너는
어댑터를 import 하지 않으므로(계층 규칙) 둘 사이를 건너는 모양이 여기 있다. `Location`이
`symbols_for`·`call_sites_of`의 경계를 건너는 것과 같은 자리다.
"""

from pydantic import Field

from src.schemas.common import HarnessModel, NonEmptyStr


class StoreTarget(HarnessModel):
    """쓰기 지점을 찾을 이름 하나 — 슬라이스 줄에 나온 필드(`x->len`)나 변수."""

    name: NonEmptyStr
    # 참이면 `x->name`·`x.name`의 `name`(필드), 거짓이면 맨 식별자(변수).
    field: bool
    # 이름을 본 파일·줄. 같은 파일의 쓰기를 먼저 보이는 기준이 `file`이고, 변수면 `line`을 품는
    # 함수에서 그 이름이 지역·파라미터인지 가린다. 필드는 `line`을 안 쓴다. entry 줄을 모르면 0이다.
    file: NonEmptyStr
    line: int = Field(ge=0)
    # 필드가 적힌 모양(`ctx->hdr.len`). 인덱스는 이것으로 거르지 않는다 — `field_assigns`에는 객체의
    # 타입이 없어 철자가 같아도 같은 구조체라고 못 한다. 쓰는 쪽(`source_stores`)이 모호한 행끼리
    # 순서를 정할 때 좌변이 같은 철자인 쓰기를 앞세운다.
    lhs: str | None = None


class StoreSite(HarnessModel):
    """인덱스가 아는 쓰기 지점 하나 — 구문상 직접 대입(`=`·`+=` 등)만이다."""

    file: NonEmptyStr
    line: int = Field(ge=1)
    # 쓰기가 있는 함수 이름.
    func: str
    name: NonEmptyStr
    field: bool
    # 필드 쓰기의 좌변 철자(`c->h.len`)와 연산자(`=`·`+=`…). 변수 대입은 `lhs`가 비고 연산자를
    # 모른다(`assigns` 표는 줄만 든다).
    lhs: str = ""
    op: str = "="
    # 같은 줄에 같은 함수의 할당기 호출(`malloc` 등)이 **있다** — 그 이름과 인자(크기 식). 줄 단위
    # 판정이라 쓰기의 우변이 그 호출이라는 뜻은 아니다(`x = f(malloc(n))`) — 가르는 것은 읽는 쪽이
    # 원문 줄로 한다(`source_stores._predicate`).
    alloc: str | None = None
    alloc_args: tuple[str, ...] = ()
    # 이 쓰기가 **같은 변수·필드의 것이라고 확인하지 못했다**(이름만으로 이었다). 필드는 선언한
    # 구조체가 인덱스에서 하나이고 그 파일이 쓰기 파일과 찾는 줄의 파일 둘 다와 같은 디렉터리일
    # 때만 아니다 — 쓰기 행에는 객체의 타입이 없고 include 정보도 인덱스에 없다. 변수는 찾는 줄과
    # 같은 파일의 쓰기만 아니다(인덱스는 저장 클래스를 안 든다). 아닌 행도 **증명이 아니다**.
    ambiguous: bool = False
    # 쓰기가 비제품 경로(`tests/`·`examples/`·`fuzz*` 등 — `is_nonproduct_path`)에 있고 찾는 줄은
    # 제품 경로다. 정의 조회가 비제품 정의를 제품 뒤로 미루는 것과 같은 규칙이다(찾는 줄이
    # 비제품이면 안 미룬다).
    nonproduct: bool = False
