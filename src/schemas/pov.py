"""PoV 역할의 출력 스키마 — 매 홉 `PovAction`(v5~v9는 형제
`PovActionNamed`이고 서빙 판은 v9), 그 `gen`이 싣는 PoC 바이트 구성 스펙 `PovSpec`.

council verdict(2026-09-30, fork B-iii): 모델은 PoC 바이트를 직접 내지 않는다. 대신
**어떻게 바이트를 조립할지**를 스펙으로 내고, 순수 함수 `orchestrator.poc_assembly.assemble_poc`가
결정론적으로 바이트를 만든다. 이유:
- **결정론** — 조립을 모델 밖에 두면 같은 스펙은 항상 같은 바이트다(gate.py가 판정을 순수
  함수로 둔 것과 같은 규율).
- **strict-JSON 문법 열화 회피** — 큰 base64를 구조적 출력에 물리면 문법 마스크가 출력을 깎을 수
  있다는 **추정**이다(base64 자체는 안 쟀다 — CLAUDE.md §0). 인접 실측은 **다른 현상**이다:
  verdict를 enum으로 강제하면 gpt-oss-20b가 2/5, 자유 문자열이 5/5였다(`enums.Verdict.from_text`
  주석 · PR #182 본문 · 원문 미커밋). 스펙은 작은 구조라 그 위험을 애초에 피한다.
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
    """`gen` 액션이 싣는 PoC 구성 스펙. `assemble_poc`가 바이트로 조립한다.

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


# 액션별로 그 액션이 쓰는 필드. `_check_action`이 다른 액션의 필드가 섞였는지 본다
# (`_FIELDS_BY_KIND`와 같은 규율 — `extra="forbid"`는 없는 필드 이름만 막지, 선언된 필드를
# 엉뚱한 액션에 채운 것은 못 잡는다).
_FIELDS_BY_ACTION: dict[str, tuple[str, ...]] = {
    "gen": ("spec",),
    "inspect": ("tool",),
    "submit": (),
}


# ⚠ **이 클래스의 docstring은 `response_format.json_schema.schema.description`으로 모델 요청
# payload에 그대로 실린다**(v2 포함 — `src/adapters/openai_compat.py`). 그래서 docstring 본문에는
# 커밋 SHA·PR·이슈 번호·소스 파일 경로 같은 저장소 내부 참조를 넣지 않는다 — 그런 이력은 이 클래스
# **밖의** `#` 주석에 둔다(`tests/test_pov_loop.py::
# test_pov_action_description_in_the_request_has_no_repo_internal_references`가 지킨다).
#
# `poc` 이력: ⏵ 2026-10-09 — 그 도구가 과제 `-vul` 이미지에 PoC를 먹이게 됐다
# (ADR 0014 · 이슈 #513). `asan`·`build`·`gdb` 셋은 그대로 안 받는다 — 셋을 바꾸려면 우리가
# 대상을 빌드해야 하고 그 안(A)은 기각됐다(이슈 #507 — 우리 clang이 번들을 컴파일하지 못한다).
# 🔴 아래 docstring은 **모델 요청에 실리므로** 그 이력을 거기 적지 않는다.
#
# gdb 이력: `TOOL_KINDS`에 `"gdb"`가 들어온 것은 PR #327(`5cf5c20`, 2026-10-01) ·
# `PovAction.tool`에 넣은 근거는 이슈 #360(사용자 결정) · gdb 사용 **규칙**을 프롬프트에 안 두는
# 이유는 이슈 #325(gdb ToolRunner 결정) — ASAN 실행 경로 수리·검증(#326)이 선행 조건.
# ⏵ 2026-10-13 - #326은 선택지 (c) 선언으로 닫혔다(`validate.run`은 서빙 점수 경로 밖 -
# `nodes/validate.py` 모듈 docstring). 자체 링크 경로의 E4 도달성은 이제 #507(e)가 든다.
class PovAction(HarnessModel):
    """PoV 툴 루프가 매 홉 내는 제어 액션.

    `action`이 어느 필드를 쓰는지 정한다:
    - `gen`: `spec`(PovSpec)을 실어 순수 함수가 인프로세스로 바이트를 만든다. **모델이 쓴 코드를
      실행하지 않는다** — 조립은 결정론 순수 함수. raw 임의 바이트가 필요하면 `spec`의 `literal`
      세그먼트에 통째로 넣는다.
    - `inspect`: `tool`(asan/build/gdb/poc) 진단을 돌린다. **`poc`는 직전 `gen`의 PoC를 과제의
      취약 빌드에 먹인다** — 그 관측은 당신 바이트의 결과다. 나머지 셋(`asan`·`build`·`gdb`)은
      대상 소스를 빌드·실행할 뿐이고 그 바이트를 **입력으로 받지 않는다** — 그 셋의 관측은 대상
      자체의 상태다.
    - `submit`: 루프를 끝낸다 — 마지막 gen의 PoC가 final 후보.

    ⚠ **승격 seam(후속):** 모델이 `literal`로 직접 못 쓰는 계산값(체크섬·해시·압축)을 요구하는
    포맷은 이 스펙으로 도달 못 한다 — 그때 `spec` 자리에 generator-script 대안을 더한다(별도
    신뢰면·리뷰). 지금은 열지 않는다(임의 코드 실행 회피).

    `PovSegment._check_kind`와 같은 규율으로, 그 액션이 쓰지 않는 필드가 실리면 거절한다.
    """

    action: Literal["gen", "inspect", "submit"]
    spec: PovSpec | None = None
    # 도구는 `TOOL_KINDS` 화이트리스트 안이어야 한다 — `gdb`는 PR #327(`5cf5c20`, 2026-10-01)로
    # 그 안에 들어왔다. `tests/test_pov_loop.py`의 계약 검사가 이 Literal ⊆ TOOL_KINDS를 강제한다.
    # ⚠ **이 목록을 넓히면 프롬프트도 같이 넓힌다** — 스키마가 허용해도 프롬프트가 광고하지 않으면
    # 모델은 그 도구를 영영 안 쓴다(`prompts/pov/<판>/prefix.md`의 `inspect` 줄).
    # ⚠ **그리고 이 Literal은 스키마 차원이라 프롬프트 버전 게이트(`nodes.pov._WIDE_INPUT_VERSIONS`)
    # 밖이다** — v2에도 `gdb`가 들어간다. v2 prefix가 그것을 안 광고하므로 실제로 고르지는
    # 않지만(위 한계), 이 Literal 자체로 v2 입력이 바뀌는 것은 막지 못한다(적대적 리뷰
    # 2026-10-02 MEDIUM-4가 든 한계).
    tool: Literal["asan", "build", "gdb", "poc"] | None = None
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def _check_action(self) -> "PovAction":
        required = {"gen": "spec", "inspect": "tool"}.get(self.action)
        if required is not None and getattr(self, required) is None:
            raise ValueError(f"{self.action} 액션은 {required}가 필요하다")
        allowed = set(_FIELDS_BY_ACTION[self.action])
        stray = sorted(
            name
            for fields in _FIELDS_BY_ACTION.values()
            for name in fields
            if name not in allowed and getattr(self, name) is not None
        )
        if stray:
            raise ValueError(
                f"{self.action} 액션에 다른 액션의 필드가 섞였다: {stray} — "
                f"{self.action}가 쓰는 필드는 {list(_FIELDS_BY_ACTION[self.action])}뿐이다"
            )
        return self


# ⏵ 2026-10-11 — `PovAction`의 형제 — v5가 `submit`에 앞선 gen을 지명하는 선택 필드를 더한다(이슈
# #549 ②). **`PovAction`은 한 글자도 안 고친다** — 그 JSON 스키마가 v2~v4의 요청 payload라서다
# (`nodes/pov.py`의 판별은 프롬프트 meta `output_schema` 선언 — Auditor 봉투 넷과 같은 방식).
# 필드는 맨 뒤에 붙는다(pydantic 순서). docstring은 **자기 것**이다 — 요청 스키마의 `description`
# 으로 모델에 실리므로 `submit` 줄이 지명을 말해야 한다(부모의 「마지막 gen」은 v2~v4에는 참이라
# 거기는 안 고친다 — Santa 2라운드 C가 잡았다).
# **형식을 검증하지 않는다** — 환각한 해시(짧음·`0x`·백틱·말줄임)를 거절하면 어댑터 재시도 끝에
# `SchemaParseError`가 되어 그 Finding이 후보를 잃는다(fail-closed가 아니다). 맞는 gen을 못 찾으면
# 러너가 마지막 gen으로 돌아가고 trace에 `submit_digest=unknown`을 남긴다(`_resolve_submit_digest`).
# 길이 상한 80만 둔다(`sha256:`+64 hex = 71자) — **그 상한도 같은 비용을 진다**: 81자 이상은 파싱
# 실패라 그 홉은 후보를 못 만든다(`strict` json_schema라 문법으로 먼저 걸릴 수 있다 — 미실측).
# `submit` 밖에서는 읽히지 않고 무시된다.
class PovActionNamed(PovAction):
    """PoV 툴 루프가 매 홉 내는 제어 액션(v5·v6 — `submit`이 앞선 gen을 지명할 수 있는 판).

    `action`이 어느 필드를 쓰는지 정한다:
    - `gen`: `spec`(PovSpec)을 실어 순수 함수가 인프로세스로 바이트를 만든다. **모델이 쓴 코드를
      실행하지 않는다** — 조립은 결정론 순수 함수. raw 임의 바이트가 필요하면 `spec`의 `literal`
      세그먼트에 통째로 넣는다.
    - `inspect`: `tool`(asan/build/gdb/poc) 진단을 돌린다. **`poc`는 직전 `gen`의 PoC를 과제의
      취약 빌드에 먹인다** — 그 관측은 당신 바이트의 결과다. 나머지 셋(`asan`·`build`·`gdb`)은
      대상 소스를 빌드·실행할 뿐이고 그 바이트를 **입력으로 받지 않는다** — 그 셋의 관측은 대상
      자체의 상태다.
    - `submit`: 루프를 끝낸다 — `poc_sha256`에 이미 낸 gen의 digest(전체 또는 8자 이상 접두,
      `sha256:` 접두 허용)를 적으면 **그 gen의 PoC**가, 안 적으면 마지막 gen의 PoC가 final 후보.

    ⚠ **승격 seam(후속):** 모델이 `literal`로 직접 못 쓰는 계산값(체크섬·해시·압축)을 요구하는
    포맷은 이 스펙으로 도달 못 한다 — 그때 `spec` 자리에 generator-script 대안을 더한다(별도
    신뢰면·리뷰). 지금은 열지 않는다(임의 코드 실행 회피).

    `PovSegment._check_kind`와 같은 규율으로, 그 액션이 쓰지 않는 필드가 실리면 거절한다
    (`poc_sha256`만 예외 — `submit` 밖에서는 거절하지 않고 읽지도 않는다).
    """

    poc_sha256: str | None = Field(
        default=None,
        max_length=80,
        description=(
            "`submit`에서만 읽는다 — 이미 낸 gen의 PoC sha256(전체 또는 8자 이상 접두 · `sha256:` "
            "접두 허용). 없으면 마지막 gen의 PoC가 최종 후보다. 형식은 받는 쪽이 정규화한다."
        ),
    )
