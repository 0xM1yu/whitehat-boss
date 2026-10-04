"""Auditor 역할의 홉 봉투 — 매 홉 `AuditorAction`. 도구를 쥐어 준 좌석의 제어 단위다.

한 방 경로(`nodes/auditor.py`의 `OUTPUT_SCHEMA = Finding`)는 그대로 남는다. 이 봉투는
`thresholds.auditor_tool_hops`가 양수이고 배선된 프롬프트 판이 도구를 서술할 때만 요청된다 —
꺼진 run의 요청 바이트는 종전과 같다.

`PovAction`과 같은 규율이다.

- **액션별로 쓰는 필드를 고정한다** — `extra="forbid"`는 없는 이름만 막지, 선언된 필드를
  엉뚱한 액션에 채운 것은 못 잡는다.
- **도구 이름은 Literal로 닫는다** — 자유 문자열이면 화이트리스트 밖 이름이 조용히
  `tool_policy` 위반 Evidence로 떨어지고, 모델은 그것을 「도구가 없다」로 읽는다.
- **바이트는 코드가 아니라 조립 명세로 낸다** — `PovSpec`을 재쓴다. 모델이 쓴 스크립트를
  샌드박스에서 돌리는 길은 열지 않는다(결정론 + 임의 코드 실행 회피). ⚠ 다만 **지금 도구는 그
  바이트를 입력으로 안 받는다** — `spec`은 기록으로만 남고(아래 `spec` 주석), 모델에게도 그렇게
  적는다.
- **코드 열람은 이름으로 묻는다** — 경로·정규식을 받는 `grep`·`cat`을 열지 않는다. 모델
  문자열이 도구 인자로 들어가지 않고, 같은 사실의 두 번째 데이터원이 생기지 않는다.
"""

from typing import Literal, get_args

from pydantic import Field, model_validator

from src.schemas.common import HarnessModel, NonEmptyStr
from src.schemas.finding import Finding
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
# (`src/schemas/pov.py`의 모듈 수준 `_FIELDS_BY_ACTION`과 같은 규율).
#
# `probe`가 `spec`도 쓰는 것은 바이트를 넣어야 관측이 모델 행동의 함수가 되기 때문이다.
# 지금 도구는 입력을 받지 않으므로 `spec`은 **기록으로만** 남는다 — 그 한계는 노드가 적는다.
_FIELDS_BY_ACTION: dict[str, tuple[str, ...]] = {
    "lookup": ("requests",),
    "probe": ("tool", "spec"),
    "answer": ("finding",),
}

# 액션이 반드시 들어야 하는 필드. `spec`은 `probe`의 **선택** 필드라 여기 없다.
_REQUIRED_BY_ACTION: dict[str, str] = {
    "lookup": "requests",
    "probe": "tool",
    "answer": "finding",
}


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
# ⚠ `probe`의 `spec`이 도구 입력으로 들어가려면 PoC 바이트 materialize(#386 ①)가 선행이다.
class AuditorAction(HarnessModel):
    """감사 좌석이 매 홉 내는 제어 액션.

    `action`이 어느 필드를 쓰는지 정한다.

    - `lookup`: `requests`에 필요한 코드를 **이름으로** 적어 조회한다. 각 요청은 종류
      (변수·타입·매크로·함수·호출자 등)와 **이름**이고, 경로나 검색 패턴이 아니다. 찾은 원문이
      다음 홉의 관측으로 들어온다.
    - `probe`: `tool`로 진단을 한 번 돌린다. 도구는 입력 바이트를 받지 않는다 — `spec`을 실어도
      도구에 전달되지 않고 관측이 달라지지 않는다. 같은 도구를 다시 부르면 같은 관측이 온다.
    - `answer`: `finding`에 최종 Finding을 실어 감사를 끝낸다.

    새 근거 ID는 관측을 받은 뒤에만 인용할 수 있다. 홉 상한에 닿으면 마지막 홉은 `answer`여야
    한다 — 조사만 하고 끝나면 그 후보는 Finding 없이 사유와 함께 대기로 남는다.

    그 액션이 쓰지 않는 필드가 실리면 거절한다.
    """

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
    # ⚠ **지금 도구는 `spec`을 입력으로 안 받는다** — 노드가 sha256과 길이만 기록하고 도구에는 안
    # 넘긴다. 그 사실을 모델에게 **정직하게** 적는다(필드 설명은 요청 payload에 실린다): 「입력
    # 바이트를 구성한다」고 적으면 모델이 「내가 만든 입력으로 돌렸더니」라는 거짓 문장을 궤적에
    # 쓴다. PoC 바이트 materialize(#386 ①)가 서면 이 설명과 노드 관측의 그 줄
    # (`_probe_observation`)을 같이 고친다.
    spec: PovSpec | None = Field(
        default=None,
        description=(
            "지금은 쓰지 않는다 — 도구는 이 값을 받지 않고 관측도 달라지지 않는다. 싣지 않는다."
        ),
    )
    finding: Finding | None = None
    rationale: NonEmptyStr

    @model_validator(mode="after")
    def _check_action(self) -> "AuditorAction":
        required = _REQUIRED_BY_ACTION[self.action]
        if not getattr(self, required):
            raise ValueError(f"{self.action} 액션은 비지 않은 {required}가 필요하다")
        allowed = set(_FIELDS_BY_ACTION[self.action])
        stray = sorted(
            name
            for fields in _FIELDS_BY_ACTION.values()
            for name in fields
            if name not in allowed and getattr(self, name)
        )
        if stray:
            raise ValueError(f"{self.action} 액션에 다른 액션의 필드가 실렸다: {stray}")
        return self
