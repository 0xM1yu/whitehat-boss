"""Auditor 역할의 홉 봉투 — 매 홉 하나. 도구를 쥐어 준 좌석의 제어 단위다.
**봉투는 둘이다**(⏵ 2026-10-06): v8·v9가 `AuditorAction`, v10이 `AuditorProcedureAction`을 낸다.
⏵ 2026-10-09 — **셋이다**: v11이 `AuditorProcedureNullableCweAction`을 낸다(이슈 #525).
⏵ 2026-10-10 — **넷이다**: v12가 `AuditorProcedureAuditAlwaysAction`을 낸다(이슈 #578 — 이 파일 맨
아래).
⏵ 2026-10-13 — **다섯이다**: v15가 `AuditorProcedureProbeInputAction`을 낸다(v12 봉투의
서브클래스 — `probe`의 `spec` 설명만 다시 쓴다, 이 파일 맨 아래). 아래 본문의 「Auditor 경로는
그 바이트를 도구에 안 싣는다」는 v8~v14 봉투의 서술이다.
⏵ 2026-10-16 — **여섯이다**: v18이 `AuditorProcedureProbeInputDiscriminatedAction`을 낸다(다섯째의
서브클래스 — 검증은 그대로고 디코더에 보내는 JSON 스키마만 `action`·`trigger_status`별 판별 합이다,
이슈 #602 · 이 파일 맨 아래).
액션 셋과 조사 필드는 공통 베이스가 들고 최종 답의 필드만 갈린다(`finding` ↔ `procedure`).

한 방 경로(`nodes/auditor.py`의 `OUTPUT_SCHEMA = Finding`)는 그대로 남는다. 이 봉투는
`thresholds.auditor_tool_hops`가 양수이고 배선된 프롬프트 판이 도구를 서술할 때만 요청된다 —
꺼진 run의 요청 바이트는 종전과 같다.

`PovAction`과 같은 규율이다.

- **액션별로 쓰는 필드를 고정한다** — `extra="forbid"`는 없는 이름만 막지, 선언된 필드를
  엉뚱한 액션에 채운 것은 못 잡는다.
- **도구 이름은 Literal로 닫는다** — 자유 문자열이면 화이트리스트 밖 이름이 조용히
  `tool_policy` 위반 Evidence로 떨어지고, 모델은 그것을 「도구가 없다」로 읽는다.
- **바이트는 코드가 아니라 조립 명세로 낸다** — `PovSpec`을 재쓴다. 모델이 쓴 스크립트를
  샌드박스에서 돌리는 길은 열지 않는다(결정론 + 임의 코드 실행 회피).
  ⚠ 다만 **Auditor 경로는 그 바이트를
  도구에 안 싣는다** — `spec`은 기록으로만 남고(아래 `spec` 주석), 모델에게도 그렇게 적는다.
  ⏵ 2026-10-09 (ADR 0014) — **이유가 바뀌었다.** 컨테이너 `poc`는 이제 입력을 **먹일 수
  있지만**(과제 `-vul` 이미지의 `/tmp/poc`) `nodes/auditor._probe`가 `ToolArgs`에 `input_bytes`를
  **안 싣는다**. 그래서 `probe(poc)`는 입력 없는 호출이라 실패 Evidence다 — 싣게 하는 것은
  이슈 #419이고 이 ADR의 범위가 아니다.
  ⏵ **2026-10-13(#419) — 위 두 문장(⚠ 다만 …·⏵ 2026-10-09)은 이제 그 이전의 기록이다. 이제 싣는다.**
  `_probe`가 `spec`을 `assemble_poc`으로 조립해 `ToolArgs.input_bytes`로 보낸다(`pov.run_tool`과
  같은 계약). `probe(poc)`가 입력 없는 호출이라 실패 Evidence라는 말도 그 이전의 서술이다.
  컨테이너에서 읽는 도구는 `poc` 하나다.
  ⚠ **동결된 문구가 낡았다** — 아래 `spec`의 필드 설명과 `AuditorAction`·`AuditorProcedureAction`
  (·형제)의 `probe` 줄(*「이 경로는 입력 바이트를 안 싣는다」*)은 v8~v14의 요청 payload라 고칠 수
  없다. 고침은 다음 프롬프트 판(auditor v15)과 같이 새 필드·새 봉투로 간다.
- **코드 열람은 이름으로 묻는다** — 경로·정규식을 받는 `grep`·`cat`을 열지 않는다. 모델
  문자열이 도구 인자로 들어가지 않고, 같은 사실의 두 번째 데이터원이 생기지 않는다.

v10 판은 최종 답으로 `Finding`이 아니라 `AuditProcedure`를 싣는 형제 봉투
(`AuditorProcedureAction`)를 쓴다(⏵ 2026-10-09 v11은 셋째 `AuditorProcedureNullableCweAction`).
봉투의
공통 필드와 액션 규율은 베이스(`_AuditorEnvelope`)가
들고, `answer`가 싣는 필드 하나(`finding` ↔ `procedure`)와 `rationale`만 갈린다.
"""

from typing import Any, ClassVar, Literal, Self, get_args

from pydantic import Field, model_validator

from src.schemas.common import Cwe, HarnessModel, Identifier, NonEmptyStr
from src.schemas.finding import (
    NO_SIGNAL_ONLY_AUDIT_VALUES,
    AuditBlock,
    AuditBlockWithNoSignal,
    Finding,
    ReproSpec,
    no_signal_only_values_in,
)
from src.schemas.pov import PovSpec
from src.schemas.scout_response import RequestedContext

# `probe`가 부를 수 있는 도구 — **스키마가 닫은 Literal이다.** ToolRunner
# 화이트리스트(`TOOL_KINDS`)에서 `index`만 뺀 여섯이고(`tests/test_auditor_loop.py`가 대조),
# 프롬프트 meta의 `tools`가 광고할 수 있는 이름의 상한이기도 하다 — `cli`·노드가 `AUDITOR_TOOLS`로
# meta 선언을 이 집합과 맞춘다. 별칭으로 뺀 것은 그 대조가 Literal 값을 **스키마를 뒤져서가 아니라
# 이름으로** 읽게 하려는 것이다(JSON 스키마 표현은 별칭을 안 거치므로 요청 payload는 인라인
# Literal과 같다).
AuditorTool = Literal["semgrep", "clang-tidy", "build", "asan", "gdb", "poc"]
AUDITOR_TOOLS: tuple[str, ...] = get_args(AuditorTool)

# 액션별로 그 액션이 쓰는 필드. `_check_action`이 다른 액션의 필드가 섞였는지 본다
# (`src/schemas/pov.py`의 모듈 수준 `_FIELDS_BY_ACTION`과 같은 규율). **`answer`는 여기 없다** —
# 그 액션이 싣는 필드는 봉투마다 다르다(`_AuditorEnvelope._PAYLOAD_FIELD`).
#
# `probe`가 `spec`도 쓰는 것은 바이트를 넣어야 관측이 모델 행동의 함수가 되기 때문이다.
# ⏵ 2026-10-09 — **이 경로가** 그 값을 도구에 안 싣으므로 `spec`은 **기록으로만** 남는다(컨테이너
# `poc`는 ADR 0014로 받을 수 있다 — 안 싣는 쪽이 이슈 #419다). 그 한계는 노드가 적는다.
# ⏵ 2026-10-13(#419) — **이제 싣는다**(`nodes/auditor._probe`). 위 두 줄은 그 이전의 기록이다.
_FIELDS_BY_ACTION: dict[str, tuple[str, ...]] = {
    "lookup": ("requests",),
    "probe": ("tool", "spec"),
}

# 액션이 반드시 들어야 하는 필드(`answer` 제외 — 위와 같다). `spec`은 `probe`의 **선택** 필드라
# 여기 없다.
_REQUIRED_BY_ACTION: dict[str, str] = {
    "lookup": "requests",
    "probe": "tool",
}


# 루프 봉투(`AuditorAction` · `AuditorProcedureAction` · ⏵ 2026-10-09
# `AuditorProcedureNullableCweAction`)의
# 공통 부분. **필드 선언 순서를 지킨다** —
# pydantic은 베이스의 필드를 먼저 놓으므로 여기서 `rationale`을 선언하면 `finding`이 그 **뒤로**
# 밀려 `AuditorAction`의 JSON 스키마(property 순서)가 바뀌고, 그것이 v8·v9 루프의 요청 바이트다
# (evaluation §2.3). 그래서 베이스는 `action`·`requests`·`tool`·`spec`까지만 선언하고 `answer`의
# payload 필드와 `rationale`은 서브클래스가 이 순서로 잇는다.
#
# 이 docstring은 요청 payload에 안 실린다 — 서브클래스가 각자 docstring을 든다(`description`은
# 그 클래스 자신의 것만 나간다).
class _AuditorEnvelope(HarnessModel):
    """루프 봉투가 공유하는 필드와 액션 규율."""

    # `answer`가 싣는 필드의 이름. 서브클래스가 정한다 — 아래 `__pydantic_init_subclass__`가
    # **클래스 정의 시점에** 그 이름이 실재하는 필드인지 본다. 미루면 그 위반이 첫 인스턴스화의
    # `AttributeError`가 되고, 노드의 모델 호출 경로에서는 그것이 `SCHEMA_PARSE_FAILED`로 적혀
    # **코드 결함이 모델 파싱 실패율로 둔갑한다**(적대적 검토 2026-10-06).
    _PAYLOAD_FIELD: ClassVar[str]

    action: Literal["lookup", "probe", "answer"]
    # 조회 요청. 비어 있으면 거절한다 — resolver에 넘길 것이 없는 홉은 아예 안 낸다.
    requests: list[RequestedContext] = Field(default_factory=list)
    # 도구는 ToolRunner 화이트리스트 안이어야 한다(`tests/test_auditor_loop.py`가 대조).
    # ⚠ 화이트리스트에 있는 `index`를 **일부러 뺐다** — 코드 열람은 `lookup`이 담당한다. 둘을
    # 다 열면 같은 사실의 데이터원이 둘이 되고, 모델이 어느 쪽으로 물었는지에 따라 관측이
    # 갈린다. 되돌리려면 `lookup`을 왜 안 쓰는지를 먼저 적어야 한다.
    # ⚠ **이 목록을 넓히면 프롬프트도 같이 넓힌다** — 스키마가 허용해도 프롬프트가 광고하지
    # 않으면 모델은 그 도구를 영영 안 쓴다(PoV v2 ↔ gdb가 그 선례다).
    # ⚠ 실행류(`asan`·`gdb`·`poc`)가 들어 있으므로 이 좌석의 Evidence가 E4 조건을 만족할 수
    # 있다 — 그 경계는 이슈 #419의 결정이다.
    tool: AuditorTool | None = None
    # ⚠ **Auditor 경로는 `spec`을 도구에 안 싣는다** — 노드가 sha256과 길이만 기록하고 도구에는
    # 안 넘긴다. ⏵ 2026-10-09 (ADR 0014) — 컨테이너 `poc`는 먹일 수 **있게** 됐지만 이 경로가
    # 안 싣는다(이슈 #419). 그 사실을 모델에게 **정직하게** 적는다(필드 설명은 요청
    # payload에 실린다): 「입력
    # 바이트를 구성한다」고 적으면 모델이 「내가 만든 입력으로 돌렸더니」라는 거짓 문장을 궤적에
    # 쓴다. 이 경로가 바이트를 싣게 되면(이슈 #419) 이 설명과 노드 관측의 그 줄
    # (`_probe_observation`)을 같이 고친다.
    # ⏵ 2026-10-13(#419) — **노드는 이제 싣는다**(관측의 그 줄은 지웠다). ⚠ **그런데 아래
    # `description`은 못 고친다 — 요청 payload라 v8~v14의 요청 바이트다.** 그 문구
    # (*「지금은 쓰지 않는다 … 싣지 않는다」*)는 이제 **거짓이다**: 모델이 이 문구를 믿으면
    # `spec`을 안 보낸다(`probe poc`가 0회인 한 원인 — 이슈 #617). 고침은 다음 프롬프트 판(auditor
    # v15)에서 이 필드를 새 설명으로 다시 선언하는 것과 같이 간다.
    spec: PovSpec | None = Field(
        default=None,
        description=(
            "지금은 쓰지 않는다 — 이 경로가 도구에 안 싣고 관측도 달라지지 않는다. 싣지 않는다."
        ),
    )

    @property
    def answer(self) -> "Finding | AuditProcedure | None":
        """`answer` 액션이 실은 최종 답 — 조사 액션이면 `None`이다.

        봉투마다 필드 이름이 다르므로(`finding` ↔ `procedure`) 읽는 쪽이 봉투 목록을 모르게
        한다. **비공개 `_PAYLOAD_FIELD`를 다른 모듈이 `getattr`로 읽던 자리를 이것이 대신한다** —
        그쪽은 반환 타입이 `Any`라 아무도 검사하지 않았고, 이름이 엉뚱한 실재 필드를 가리키면
        조용히 그 값을 돌려줬다 — `rationale`을 가리키면 `None` 대신 그 문자열이 나오고, 실패는
        뒤에서 `AttributeError`로 터진다(적대적 검토 2026-10-06의 변이 실험으로 확인했다).
        """
        return getattr(self, self._PAYLOAD_FIELD)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        """서브클래스가 `_PAYLOAD_FIELD`로 **실재하는 필드**를 가리키는지 정의 시점에 본다."""
        super().__pydantic_init_subclass__(**kwargs)
        name = getattr(cls, "_PAYLOAD_FIELD", None)
        if name not in cls.model_fields:
            raise TypeError(
                f"{cls.__name__}._PAYLOAD_FIELD={name!r}가 이 봉투의 필드가 아니다 — "
                f"있는 것: {sorted(cls.model_fields)}"
            )

    @model_validator(mode="after")
    def _check_action(self) -> Self:
        payload = self._PAYLOAD_FIELD
        required = {**_REQUIRED_BY_ACTION, "answer": payload}[self.action]
        if not getattr(self, required):
            raise ValueError(f"{self.action} 액션은 비지 않은 {required}가 필요하다")
        fields_by_action = {**_FIELDS_BY_ACTION, "answer": (payload,)}
        allowed = set(fields_by_action[self.action])
        stray = sorted(
            name
            for fields in fields_by_action.values()
            for name in fields
            if name not in allowed and getattr(self, name)
        )
        if stray:
            raise ValueError(f"{self.action} 액션에 다른 액션의 필드가 실렸다: {stray}")
        return self


# ⚠ **이 클래스의 docstring은 `response_format.json_schema.schema.description`으로 모델 요청
# payload에 그대로 실린다**(`src/adapters/openai_compat.py`). 그래서 docstring 본문에는 커밋
# SHA·PR·이슈 번호·소스 파일 경로 같은 저장소 내부 참조를 넣지 않는다 — 그런 이력은 이 클래스
# **밖의** `#` 주석에 둔다(`tests/test_auditor_loop.py`가 지킨다. `PovAction`의 같은 검사가
# 선례다).
#
# 이력: 도구를 Auditor 좌석에 쥐어 주는 근거는 파인튜닝 궤적이다 — ADR 0012 결정 7이 「Auditor가
# 무엇을 배울지」를 판정 ↔ 질의 생성 사이에 열어 뒀고, 질의 생성을 배우려면 모델이 질의를 내는
# 궤적이 있어야 한다. 등재는 이슈 #419다. ⚠ 궤적을 학습에 쓰려면 사고 보존(#280)과 내보내기가
# 따라와야 한다 — 이 봉투 하나로 FT 데이터가 서지 않는다.
# ⚠ `probe`의 `spec`이 도구 입력으로 들어가려면 **이 노드가 싣는 것**이 선행이다(이슈 #419) —
# 컨테이너 `poc` 쪽 선행은 ADR 0014로 섰다.
# ⏵ 2026-10-13(#419) — 노드 쪽 선행도 섰다. 아래 docstring의 `probe` 줄(*「이 경로는 입력 바이트를
# 안 싣는다」*)은 **이제 낡았다** — v8·v9의 요청 바이트라 못 고치고 다음 판(auditor v15)에서 간다.
class AuditorAction(_AuditorEnvelope):
    """감사 좌석이 매 홉 내는 제어 액션.

    `action`이 어느 필드를 쓰는지 정한다.

    - `lookup`: `requests`에 필요한 코드를 **이름으로** 적어 조회한다. 각 요청은 종류
      (변수·타입·매크로·함수·호출자 등)와 **이름**이고, 경로나 검색 패턴이 아니다. 찾은 원문이
      다음 홉의 관측으로 들어온다.
    - `probe`: `tool`로 진단을 한 번 돌린다. **이 경로는 입력 바이트를 안 싣는다** — `spec`을
      실어도 도구에 전달되지 않고 관측이 달라지지 않는다. 같은 도구를 다시 부르면 같은 관측이 온다.
    - `answer`: `finding`에 최종 Finding을 실어 감사를 끝낸다.

    새 근거 ID는 관측을 받은 뒤에만 인용할 수 있다. 홉 상한에 닿으면 마지막 홉은 `answer`여야
    한다 — 조사만 하고 끝나면 그 후보는 Finding 없이 사유와 함께 대기로 남는다.

    그 액션이 쓰지 않는 필드가 실리면 거절한다.
    """

    _PAYLOAD_FIELD: ClassVar[str] = "finding"

    # 베이스의 `action`·`requests`·`tool`·`spec` 뒤에 이 순서로 잇는다 — 순서가 JSON 스키마다.
    finding: Finding | None = None
    rationale: NonEmptyStr


# v10 판의 최종 답. 필드 순서는 `prompts/auditor/v10/prefix.md`의 「Exact output contract」가 적은
# 바깥 순서 그대로다 — `audit`가 `trigger_status`·`confidence`·`rca_draft`보다 **먼저**다(`Finding`
# 주석의 #102와 같은 근거). 「Every field shown is required」라 기본값이 있는 필드가 없고,
# nullable인 `audit`·`repro_spec`도 required다.
#
# 일관성 규칙은 **하나만 코드가 집행한다** — `audit`가 null인 것과 `no_signal`은 동치(같은 파일
# 「4. Keep fields consistent」 첫 항목). 나머지(`refutation.result` ↔ `trigger_status`,
# `defense.status` ↔ holds, 신뢰도 구간, 상태별 `audit.path`의 비움 여부)는 일부러 싣지 않는다 —
# 산문이 의미 규칙으로 쥔다(「Exact output contract」 마지막 문단). 싣지 않는 이유는 비용이다:
# 검증기로 거절하면 **최종 답을 통째로 재생성한다**.
# ⚠ **그래도 두 쌍은 센다** — `refutation.result` ↔ `trigger_status`와 상태별 빈 경로는
# `answer` 홉 관측에 키로 남는다(`nodes/auditor._answer_observed`). 값을 고치지는 않는다:
# 어긴 비율을 모르면 이 규칙을 조일지 풀지 정할 근거가 없기 때문이다.
#
# ⚠ **`cwe`는 non-null 필수다 — no_signal에서도.** `Finding.cwe`가 nullable이 아니고 그 값을 읽는
# 자리가 여섯이라(SARIF `ruleId` 포함) 이 스키마에서 넓히지 않는다. 산문이 이 스키마에 맞아야 한다 —
# no_signal에 `"cwe": null`을 적는 문장이 남으면 모델은 이 스키마가 거절하는 출력을 낸다.
# ⏵ 2026-10-09 — 위 두 문장은 **v10 계약의 기록**이다. `Finding.cwe`는 이제 `no_signal`에서 `None`을
# 받고(이슈 #525 선택지 1) 읽는 자리도 `None`을 다룬다. 그래도 이 클래스는 안 넓힌다 — JSON
# 스키마가 v10의 요청 바이트라서다. null을 받는 쪽은 v11의 `AuditProcedureNullableCwe`(이 파일
# 아래)다.
# ⚠ `repro_spec`은 `ReproSpec`을 그대로 써서 스키마가 선택 필드 `origin`을 함께 내보인다 — 산문은
# `steps`·`expected_signal`만 적는다. 하네스가 저장 전에 그 값을 덮는다
# (`auditor._ensure_repro_origin`).
#
# ⚠ **이 클래스의 docstring은 요청 payload에 실린다**(`AuditorProcedureAction`의 `$defs`) — 저장소
# 내부 참조를 넣지 않는다.
class AuditProcedure(HarnessModel):
    """감사 좌석의 최종 답. `audit`가 null인 것과 `trigger_status`가 `no_signal`인 것은 같다."""

    audit: AuditBlock | None
    trigger_status: Literal[
        "triggerable", "guarded", "no_reach", "reachability_unknown", "no_signal"
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    cwe: Cwe
    title: NonEmptyStr
    rca_draft: NonEmptyStr
    evidence_refs: list[Identifier]
    repro_spec: ReproSpec | None

    @model_validator(mode="after")
    def _audit_is_null_exactly_when_no_signal(self) -> Self:
        if (self.audit is None) != (self.trigger_status == "no_signal"):
            audit = "null" if self.audit is None else "있음"
            raise ValueError(
                "audit는 trigger_status가 no_signal일 때에만 null이어야 한다 "
                f"(trigger_status={self.trigger_status}, audit={audit})"
            )
        return self


# `AuditorAction`의 형제다 — 최종 답으로 `Finding` 대신 `AuditProcedure`(`procedure`)를 싣는다. 이
# 봉투를 따로 둔 까닭은 `AuditorAction`을 **고치지 않기** 위해서다: 그 JSON 스키마가 v8·v9 루프의
# 요청 payload로 그대로 나가므로 필드를 더하면 그 판들의 요청 바이트가 바뀐다(evaluation §2.3).
# ⚠ docstring이 요청 payload에 실린다 — `AuditorAction`과 같은 규율이다(위 주석).
# ⚠ **`AuditorAction`의 같은 줄은 못 고친다** — 그 docstring이 v8·v9 루프의 요청 바이트라
# 한 글자만 바꿔도 그 판들의 payload가 달라진다(evaluation §2.3). 그래서 「같은 도구를 다시
# 부르면 같은 관측이 온다」는 과장이 v8·v9에는 남는다 — 그 판의 기록이고, 고치려면 그 판을
# 올려야 한다.
# ⏵ 2026-10-13(#514) 아래 docstring의 「직전 홉과 같으면 반복으로 감사가 선다」는 이제 **코드보다
# 좁다** - 반복 판정은 직전 홉이 아니라 앞선 **어느 홉과든** 같은 요청이면 선다(`nodes/auditor`의
# `seen`). 이 문장은 v10~v14의 요청 payload라 한 글자도 못 고친다(요청 바이트가 바뀐다) -
# 고치려면 다음 프롬프트 판과 함께 올린다. 그 전에는 모델이 알린 것보다 넓게 선다.
# ⏵ 2026-10-13(#419) — 같은 이유로 아래 `probe` 줄의 *「이 경로는 입력 바이트를 안 싣는다 — `spec`을
# 실어도 도구에 전달되지 않고」*도 이제 거짓이다(노드가 싣는다). v10~v14의 요청 바이트라 못 고치고
# 다음 판(auditor v15)에서 간다. 형제 봉투(`…NullableCwe…`·`…AuditAlways…`)의 같은 줄도 같다.
class AuditorProcedureAction(_AuditorEnvelope):
    """감사 좌석이 매 홉 내는 제어 액션.

    `action`이 어느 필드를 쓰는지 정한다.

    - `lookup`: `requests`에 필요한 코드를 **이름으로** 적어 조회한다. 각 요청은 종류
      (변수·타입·매크로·함수·호출자 등)와 **이름**이고, 경로나 검색 패턴이 아니다. 찾은 원문이
      다음 홉의 관측으로 들어온다.
    - `probe`: `tool`로 진단을 한 번 돌린다. **이 경로는 입력 바이트를 안 싣는다** — `spec`을
      실어도 도구에 전달되지 않고 관측이 달라지지 않는다. 같은 도구를 다시 부르는 것은 같은 질문을
      다시 하는 것이고, 직전 홉과 같으면 반복으로 감사가 선다. 나중에 다시 돌린 로그가 같을지는
      안 쟀고 `evidence_id`는 호출마다 새로 난다.
    - `answer`: `procedure`에 최종 감사 결과를 실어 감사를 끝낸다.

    새 근거 ID는 관측을 받은 뒤에만 인용할 수 있다. 홉 상한에 닿으면 마지막 홉은 `answer`여야
    한다 — 조사만 하고 끝나면 그 후보는 결과 없이 사유와 함께 대기로 남는다.

    그 액션이 쓰지 않는 필드가 실리면 거절한다.
    """

    _PAYLOAD_FIELD: ClassVar[str] = "procedure"

    procedure: AuditProcedure | None = None
    rationale: NonEmptyStr


# === v11: `cwe`가 `no_signal`에서만 null일 수 있는 최종 답 (이슈 #525 선택지 1) =================
#
# ⏵ 2026-10-09 — `no_signal`은 뿌리 원인을 못 세운 판정이라 참인 CWE가 없는데, v8~v10의 계약은
# 그때도 `CWE-<number>`를 요구해 **모델이 지어낸 값**이 SARIF `ruleId`와 보고서 중복 제거 키로
# 갈 수 있었다(CLAUDE.md §0 — 실제로 지어냈는지는 미측정, 이슈 #525 「안 쟀다」). 사용자
# 결정(2026-10-09)으로 `no_signal`에 한해 null을 허용한다.
#
# **v10의 두 클래스(`AuditProcedure` · `AuditorProcedureAction`)는 고치지 않는다.** 그 JSON
# 스키마가 v10의 요청 payload라(`AuditorProcedureAction`의 `$defs`) `cwe`를 `anyOf`로 넓히면 v10
# 프롬프트가 안 바뀌었는데 요청 바이트가 바뀐다 — 한 판 번호가 두 요청을 가리키게 된다
# (agent-contracts §4 규칙 1, evaluation §2.3). v10이 `AuditorAction`을 안 고치고 형제 봉투를 낸
# 선례를 따라 형제 둘을 더한다. 그래서 「v10 계약의 한계」(`AuditProcedure` 위 ⚠ 주석)는 v10의
# 기록으로 남는다.
#
# `AuditProcedure`의 **서브클래스**로 둔다 — 필드·검증기(`audit` null ⟺ `no_signal`)를 물려받고
# 읽는 쪽(`nodes/auditor`의 `isinstance(..., AuditProcedure)`)이 v10과 v11을 같은 최종 답으로
# 다룬다. pydantic은 재선언한 필드를 부모의 자리에 두므로 property 순서가 v10과 같다
# (`tests/test_nullable_cwe.py`가 문다).
#
# 반대 방향(`no_signal`이면 null이어야 한다)은 **스키마가 안 건다** — 산문이 null을 시키고, 어기면
# 최종 답을 통째로 재생성하는 비용을 안 치른다(`AuditProcedure` 위 주석의 「일부러 싣지 않는다」와
# 같은 근거). 그 위반은 v13·v14 서빙 run에서 0/4였다(n=4 · 이슈 #579).
#
# ⚠ docstring은 요청 payload에 실린다 — 저장소 내부 참조를 넣지 않는다(`AuditProcedure`와 같다).
class AuditProcedureNullableCwe(AuditProcedure):
    """감사 좌석의 최종 답. `audit`가 null인 것과 `trigger_status`가 `no_signal`인 것은 같다.

    `cwe`는 `trigger_status`가 `no_signal`일 때에만 null일 수 있다 — 세운 뿌리 원인이 없으면
    붙일 CWE도 없다. 다른 상태에서는 `CWE-<number>` 문자열이다.
    """

    cwe: Cwe | None

    @model_validator(mode="after")
    def _cwe_is_null_only_for_no_signal(self) -> Self:
        if self.cwe is None and self.trigger_status != "no_signal":
            raise ValueError(
                "cwe는 trigger_status가 no_signal일 때에만 null일 수 있다 "
                f"(trigger_status={self.trigger_status})"
            )
        return self


# `AuditorProcedureAction`의 형제 — 최종 답만 `AuditProcedureNullableCwe`다(위 주석). docstring은
# 같은 글을 그대로 싣는다: 액션 서술은 v10과 바뀐 것이 없고, 바뀐 것은 `procedure`의 `cwe` 하나다.
# pydantic은 서브클래스에 docstring이 없으면 `description`을 안 내므로 명시해서 옮긴다.
class AuditorProcedureNullableCweAction(_AuditorEnvelope):
    __doc__ = AuditorProcedureAction.__doc__

    _PAYLOAD_FIELD: ClassVar[str] = "procedure"

    procedure: AuditProcedureNullableCwe | None = None
    rationale: NonEmptyStr


# === v12: `audit`를 모든 상태에서 채우는 최종 답 (이슈 #578) ======================================
#
# ⏵ 2026-10-10 — FT 데이터(Teacher v4)가 `no_signal`에도 `audit`를 채운다(팀 결정 2026-10-10).
# v10·v11의 계약은 `no_signal`이면 `audit`가 정확히 null이라
# (`_audit_is_null_exactly_when_no_signal`) 그 데이터로 학습한 Auditor를 서빙하면 구조화 출력이
# 학습한 출력을 막는다.
#
# **v10·v11의 클래스는 고치지 않는다** — 그 JSON 스키마가 그 판들의 요청 payload라서다(v11 머리
# 주석과 같은 근거). v11의 **서브클래스**로 둔다: `cwe` 규칙(null은 `no_signal`에서만)을 그대로
# 물려받고, 읽는 쪽(`nodes/auditor`의 `isinstance(..., AuditProcedure)`)이 같은 최종 답으로 다룬다.
# `audit`만 다시 선언한다 — non-null · v12 블록. pydantic은 재선언한 필드를 부모 자리에 두므로
# property 순서가 v10·v11과 같다(`tests/test_auditor_v12.py`가 문다).
#
# 검증기는 Teacher v4의 `validate_teacher_output`과 같은 쪽으로 **세 값만** 건다 — `no_signal`이면
# `category` `none` · `defense.status` `not_applicable` · `refutation.result` `no_candidate` 셋 다,
# 다른 상태면 셋 중 어느 것도 안 된다. 부모의 `_audit_is_null_exactly_when_no_signal`을 **같은
# 이름으로 덮어** 이 규칙으로 바꾼다(pydantic은 같은 이름의 검증기를 서브클래스 것으로 바꾼다) —
# 안 덮으면 「`no_signal`이면 null」이 남아 v12의 `no_signal` 답이 전부 거절된다.
# 나머지 쌍(`refutation.result` ↔ 다른 네 상태)은 v10·v11처럼 **거절하지 않고 센다**
# (`nodes/auditor._answer_observed`의 `status_result_mismatch`) — 같은 재생성 비용 근거다. 빈 경로도
# 같다(`no_signal`·`reachability_unknown`은 빈 경로가 합법).
#
# ⚠ docstring은 요청 payload에 실린다 — 저장소 내부 참조를 넣지 않는다.
class AuditProcedureAuditAlways(AuditProcedureNullableCwe):
    """감사 좌석의 최종 답. `audit`는 어느 `trigger_status`에서도 null이 아니다.

    `no_signal`이면 `audit`는 가장 가까웠던 후보를 다섯 점검으로 걸러낸 기록이고, `category`는
    `none`, `defense.status`는 `not_applicable`, `refutation.result`는 `no_candidate`다. 이 세 값은
    `no_signal`에서만 쓴다. `cwe`는 `trigger_status`가 `no_signal`일 때에만 null일 수 있다.
    """

    audit: AuditBlockWithNoSignal

    @model_validator(mode="after")
    def _audit_is_null_exactly_when_no_signal(self) -> Self:
        used = no_signal_only_values_in(self.audit)
        if self.trigger_status == "no_signal":
            missing = sorted(set(NO_SIGNAL_ONLY_AUDIT_VALUES) - set(used))
            if missing:
                wanted = ", ".join(f"{key}={NO_SIGNAL_ONLY_AUDIT_VALUES[key]}" for key in missing)
                raise ValueError(f"trigger_status가 no_signal이면 audit의 {wanted}여야 한다")
        elif used:
            values = ", ".join(f"{key}={value}" for key, value in sorted(used.items()))
            raise ValueError(
                f"{values}는 trigger_status가 no_signal일 때에만 쓴다 "
                f"(trigger_status={self.trigger_status})"
            )
        return self


# `AuditorProcedureNullableCweAction`의 형제 — 최종 답만 `AuditProcedureAuditAlways`다(위 주석).
# docstring은 v10·v11과 같은 글을 싣는다: 액션 서술은 바뀐 것이 없다.
class AuditorProcedureAuditAlwaysAction(_AuditorEnvelope):
    __doc__ = AuditorProcedureAction.__doc__

    _PAYLOAD_FIELD: ClassVar[str] = "procedure"

    procedure: AuditProcedureAuditAlways | None = None
    rationale: NonEmptyStr


# === v15: `probe`가 입력 바이트를 싣는 봉투 (S19 · 이슈 #419 #572 #602) ==================
#
# ⏵ 2026-10-13 — v8~v14 봉투의 `spec` 설명(「지금은 쓰지 않는다 … 싣지 않는다」)과 클래스
# 설명의 `probe` 줄(「이 경로는 입력 바이트를 안 싣는다」)은 노드가 `spec`을 싣게 된 뒤
# (PR #644) **거짓이 됐다** — 모델이 그 문구를 믿으면 `spec`을 안 보낸다(`probe poc` 0회의
# 한 원인, 이슈 #617). 그러나 그 문구는 v8~v14의 요청 payload라 한 글자도 못 고친다
# (evaluation §2.3). 그래서 v12 봉투의 **서브클래스**를 새로 세워 `spec` 설명과 클래스 설명만
# 다시 쓴다. 최종 답(`AuditProcedureAuditAlways`)·필드 순서(pydantic은 재선언한 필드를 부모
# 자리에 둔다)·검증기는 v12 그대로다 — 읽는 쪽(`isinstance(..., AuditProcedure)`)이 v12와
# 같은 최종 답으로 다룬다. `answer`의 `procedure`가 비면 거절하는 규칙(`_check_action`)도
# 같고, 클래스 설명이 그 사실을 적는다(이슈 #602).
#
# ⚠ docstring과 `spec` 설명은 요청 payload에 실린다 — 저장소 내부 참조를 넣지 않는다.
class AuditorProcedureProbeInputAction(AuditorProcedureAuditAlwaysAction):
    """감사 좌석이 매 홉 내는 제어 액션.

    `action`이 어느 필드를 쓰는지 정한다.

    - `lookup`: `requests`에 필요한 코드를 **이름으로** 적어 조회한다. 각 요청은 종류
      (변수·타입·매크로·함수·호출자 등)와 **이름**이고, 경로나 검색 패턴이 아니다. 찾은 원문이
      다음 홉의 관측으로 들어온다.
    - `probe`: `tool`로 진단을 한 번 돌린다. `spec`을 실으면 하네스가 그것을 입력 바이트로 조립해
      도구에 싣는다 — 그 바이트를 읽는 도구는 `poc` 하나이고 `asan`·`gdb`·`build`는 읽지 않는다.
      같은 도구를 다시 부르는 것(`poc`는 같은 입력 바이트로 다시 부르는 것)은 같은 질문을 다시 하는
      것이고, 이 감사의 앞선 어느 호출과든 같으면(바로 앞 호출만이 아니다) 반복으로 감사가 선다.
      나중에 다시 돌린 로그가 같을지는 안 쟀고 `evidence_id`는 호출마다 새로 난다.
    - `answer`: `procedure`에 최종 감사 결과를 실어 감사를 끝낸다. `procedure`가 없거나 null이면
      거절한다.

    새 근거 ID는 관측을 받은 뒤에만 인용할 수 있다. 홉 상한에 닿으면 마지막 홉은 `answer`여야
    한다 — 조사만 하고 끝나면 그 후보는 결과 없이 사유와 함께 대기로 남는다.

    그 액션이 쓰지 않는 필드가 실리면 거절한다.
    """

    spec: PovSpec | None = Field(
        default=None,
        description=(
            "`probe`에서만 쓴다. 도구에 먹일 입력 바이트를 조각(segments)으로 서술한다 — 하네스가 "
            "조립해 도구에 싣는다. 읽는 도구는 `poc` 하나다. 생략하면 도구는 입력 없이 돈다."
        ),
    )


# === v18: 디코더가 보는 JSON 스키마를 판별 합으로 (이슈 #602 · council 2026-10-16) ================
#
# ⏵ 2026-10-16 — 모든 요청이 `model_json_schema()`를 `response_format: json_schema, strict`로 보내고
# 서버의 제약 디코더가 그것을 문법으로 집행한다. v15~v17 봉투의 스키마는 `required: [action,
# rationale]`에 `procedure: anyOf[…, null]`이고 최상위 판별이 없어서, 문법이 `{"action": "answer",
# "rationale": "…"}`와 `no_signal`인데 `defense.status`가 `absent`인 `procedure`를 **합법으로
# 허용하고** pydantic(`_check_action`·`AuditProcedureAuditAlways`의 검증기)이 그 뒤에 거절해
# 후보가 버려졌다(`run_20261016_0001` 후보 4개 중 2개 — n이 작다).
#
# 그래서 v18 봉투는 **디코더가 받는 스키마만** 바꾼다 — 검증기는 한 줄도 안 건드리고 백스톱으로
# 남는다. v15~v17의 `AuditorProcedureProbeInputAction`은 고치지 않는다(그 스키마가 그 판들의 요청
# payload다, evaluation §2.3). 새 서브클래스가 `model_json_schema`만 덮는다.
#
# 모양: 최상위는 `action`별 세 갈래(`anyOf`)고, `answer` 갈래의 `procedure`는 non-null이며
# `trigger_status`별 두 갈래다 — `no_signal` 갈래는 `category` `none` · `defense.status`
# `not_applicable` · `refutation.result` `no_candidate` · `cwe` null로 고정하고, 나머지 갈래는 그
# 셋을 enum에서 빼고 `cwe`를 non-null로 둔다. 값은 `NO_SIGNAL_ONLY_AUDIT_VALUES`와 생성된 스키마에서
# 읽는다 — 손으로 다시 적지 않는다.
# 쓰는 구성은 `anyOf` · `const` · `enum` · `required` · `properties` · `additionalProperties` ·
# `$defs`/`$ref` · `minItems`/`maxItems` 뿐이다(xgrammar가 아는 것만 — `oneOf`·`if/then`·`not`은
# 안 쓴다). ⚠ **백엔드가 이 스키마를 컴파일하는지는 이 파일이 보장하지 않는다** — 서빙 전 스키마
# 컴파일 게이트가 본다. 판별이 판정 분포를 바꾸는지도 안 쟀다.
#
# **`tool` enum을 좁히는 `signals.without_tools`와 겹쳐 선다**: 좁힌 하위 클래스는 같은 이름의 서브
# 클래스고 이 메서드를 물려받는다. `tool`의 enum은 클래스가 이미 낸 스키마(`super()`)에서 읽으므로
# 좁힌 enum이 `probe` 갈래에 그대로 실린다.
def _ref(prop: dict[str, Any]) -> str:
    return prop["$ref"].rsplit("/", 1)[1]


def _non_null(prop: dict[str, Any]) -> dict[str, Any]:
    """`anyOf[X, null]`에서 X만 — nullable 필드의 non-null 쪽 스키마."""
    return next(b for b in prop["anyOf"] if b.get("type") != "null")


def _const(prop: dict[str, Any], value: str) -> dict[str, Any]:
    return {**{k: v for k, v in prop.items() if k != "enum"}, "const": value}


def _without_value(prop: dict[str, Any], value: str) -> dict[str, Any]:
    return {**prop, "enum": [v for v in prop["enum"] if v != value]}


def _object(title: str, source: dict[str, Any], **props: dict[str, Any]) -> dict[str, Any]:
    return {**source, "title": title, "properties": {**source["properties"], **props}}


def _split_procedure(defs: dict[str, Any], proc_name: str) -> None:
    """`defs`의 `procedure` 정의를 `no_signal` 갈래와 그 밖의 갈래로 쪼갠다 (제자리)."""
    only = NO_SIGNAL_ONLY_AUDIT_VALUES
    proc = defs.pop(proc_name)
    block = defs.pop(_ref(proc["properties"]["audit"]))
    defense = defs.pop(_ref(block["properties"]["defense"]))
    refutation = defs.pop(_ref(block["properties"]["refutation"]))

    for suffix, is_no_signal in (("NoSignalCase", True), ("SignalCase", False)):
        pick = _const if is_no_signal else _without_value
        defense_name = f"AuditDefense{suffix}"
        refutation_name = f"AuditRefutation{suffix}"
        block_name = f"AuditBlock{suffix}"
        status = pick(defense["properties"]["status"], only["defense.status"])
        defs[defense_name] = _object(defense_name, defense, status=status)
        defs[refutation_name] = _object(
            refutation_name,
            refutation,
            result=pick(refutation["properties"]["result"], only["refutation.result"]),
        )
        defs[block_name] = _object(
            block_name,
            block,
            category=pick(block["properties"]["category"], only["category"]),
            defense={"$ref": f"#/$defs/{defense_name}"},
            refutation={"$ref": f"#/$defs/{refutation_name}"},
        )
        cwe = proc["properties"]["cwe"]
        defs[f"{proc_name}{suffix}"] = _object(
            f"{proc_name}{suffix}",
            proc,
            trigger_status=pick(proc["properties"]["trigger_status"], "no_signal"),
            audit={"$ref": f"#/$defs/{block_name}"},
            cwe={"title": cwe["title"], "type": "null"}
            if is_no_signal
            else {"title": cwe["title"], **_non_null(cwe)},
        )


class AuditorProcedureProbeInputDiscriminatedAction(AuditorProcedureProbeInputAction):
    __doc__ = AuditorProcedureProbeInputAction.__doc__

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """디코더에 보내는 스키마 — 위 주석의 판별 합. 검증은 부모의 것 그대로다."""
        schema = super().model_json_schema(*args, **kwargs)
        defs = schema["$defs"]
        props = schema["properties"]
        proc_name = _ref(_non_null(props["procedure"]))
        _split_procedure(defs, proc_name)

        no_extra = {"additionalProperties": False, "type": "object"}
        empty = {
            "requests": {"maxItems": 0, "type": "array"},
            "tool": {"type": "null"},
            "spec": {"type": "null"},
            "procedure": {"type": "null"},
        }
        own: dict[str, dict[str, dict[str, Any]]] = {
            "lookup": {"requests": {**props["requests"], "minItems": 1}},
            "probe": {"tool": _non_null(props["tool"]), "spec": props["spec"]},
            "answer": {
                "procedure": {
                    "anyOf": [
                        {"$ref": f"#/$defs/{proc_name}{suffix}"}
                        for suffix in ("NoSignalCase", "SignalCase")
                    ]
                }
            },
        }
        required = {"lookup": "requests", "probe": "tool", "answer": "procedure"}
        branches = []
        for action, fields in own.items():
            name = f"{action.capitalize()}Hop"
            defs[name] = {
                **no_extra,
                "title": name,
                "properties": {
                    "action": {"const": action, "title": "Action", "type": "string"},
                    **{key: fields.get(key, empty[key]) for key in empty},
                    "rationale": props["rationale"],
                },
                "required": ["action", required[action], "rationale"],
            }
            branches.append({"$ref": f"#/$defs/{name}"})
        return {
            "$defs": defs,
            "anyOf": branches,
            "description": schema.get("description", ""),
            "title": schema["title"],
            "type": "object",
        }
