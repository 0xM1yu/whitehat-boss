"""ScoutResponse — Scout의 래퍼 출력 `{candidate, analysis_summary}`.

Scout 프롬프트(v3)는 판정(`candidate`)과 근거(`analysis_summary`)를 **한 호출에서 함께** 낸다.
근거 서술은 downstream으로 넘어가지 않는다 — 하네스가 `analysis_summary`에서 읽는 것은
need_evidence 루프의 `requested_context`뿐이다(ADR 0012 결정 2). 그런데도
출력에 남기는 이유는 **모델이 근거를 써 보면 판정이 정확해진다**는 실측 때문이다(실험팀 로스터
선정: memory·injection은 근거 자세한 v14s, boundary는 간소화 v15s가 이겼다). 근거는 모델의
사고 발판이지 파이프라인 데이터가 아니다.

노드(`orchestrator/nodes/scout.py`)가 이 래퍼를 받아 `.candidate`를 파이프라인에 흘린다.
`analysis_summary`는 세션이 루프 동안 들고 있다가(`requested_context`) 파이프라인에는 넘기지
않는다. 안쪽 `candidate`는 하네스 `Candidate`와 **정확히 같은** 구조라(프롬프트 v3가 "exactly
the nine Candidate keys"로 못박음) 그대로 파이프라인에 흐른다.
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from src.schemas.candidate import Candidate
from src.schemas.common import HarnessModel, Location, NonEmptyStr
from src.schemas.enums import ScoutSignal


class CodeEvidenceItem(HarnessModel):
    """`analysis_summary.code_evidence`의 한 항목. 위치는 `file`·`line_*`만 쓰고 `symbol`은
    안 쓴다(프롬프트 v3) — `Location`의 `symbol`은 선택 필드라 생략하면 `None`이다."""

    location: Location
    description: NonEmptyStr


class AnalysisCondition(HarnessModel):
    """모델이 적용한 규칙의 필요조건과 그 충족 상태."""

    condition: NonEmptyStr
    status: Literal["confirmed", "disproven", "unknown"]
    # boundary v3만 허용하는 선택 필드다(이슈 #278 ④ — "an optional `description` is allowed").
    # 요구가 아니라 허용이라 memory·injection v3는 안 쓴다. 없으면 `None`이다.
    description: str | None = None


class RequestedContext(HarnessModel):
    """Scout가 되묻는 요청 하나 (ADR 0012 결정 2, v3 프롬프트 네 키).

    `kind`는 `Literal`로 묶지 않는다 — 프롬프트가 여덟 어휘를 예시로 주지만(`variable`·
    `type`·`macro`·`function`·`caller`·`allocation`·`guard`·`context`) 그 밖 값도 모델이
    낼 수 있고, grammar로 묶으면 출력이 강제된다. resolver가 값을 해석한다.
    """

    kind: str
    # 빈 문자열을 허용한다 — 모델이 target 없이 요청하면(`UnfilledReason.NO_TARGET`,
    # "no target was given") resolver가 그 사유에 닿아야 한다.
    target: str
    purpose: str
    request_code: str


class AnalysisSummary(HarnessModel):
    """Scout의 근거 발판. 하네스는 이것을 **소비하지 않는다**(버린다) — 스키마로 타입만 잡아
    grammar가 모델 출력을 제약하게 한다. 프롬프트 v3의 「Analysis summary contract」와 같다."""

    claim: NonEmptyStr
    code_evidence: list[CodeEvidenceItem] = Field(default_factory=list)
    conditions: list[AnalysisCondition] = Field(default_factory=list)
    counterevidence: list[NonEmptyStr] = Field(default_factory=list)
    uncertainties: list[NonEmptyStr] = Field(default_factory=list)
    # 이슈 #278 ③ — v3 프롬프트는 4키 객체를 낸다(`scout_memory/v3/prefix.md:242` ·
    # `scout_boundary/v3/prefix.md:135`). `list[NonEmptyStr]`이던 시절은 그 객체를
    # 거부하고 reason-code 표기 문자열만 통과시켰다.
    requested_context: list[RequestedContext] = Field(default_factory=list)


def signal_of(reason_codes: Sequence[str]) -> ScoutSignal | None:
    """`reason_codes[0]`에서 최종 판정 신호를 뽑는다 (ADR 0012 결정 2 — "판정은 최종 신호").

    v3는 첫 reason code를 세 신호 중 하나로 접두한다 — 명세 §4.2 원문 예는
    `triage_need_evidence:missing_request:function:doit`다. 접두 뒤에 오는 나머지는
    resolver로 가는 요청 상세이고 이 함수는 그것을 보지 않는다.

    **접두 비교는 명세 밖이다** — 명세는 신호가 첫 코드의 접두라는 것만 적을 뿐, 완전
    일치가 아니라 "시작하기만 하면 신호로 읽는다"는 해석은 이 하네스가 정했다.

    `reason_codes`가 비었거나 첫 코드가 세 신호 중 어느 것으로도 시작하지 않으면
    `None`이다 — v1·v2 경로(신호가 없어 점수만으로 판정)와, 세 신호 밖의 형식 이탈이
    여기 해당한다.
    """
    if not reason_codes:
        return None
    head = reason_codes[0]
    for signal in ScoutSignal:
        if head.startswith(signal.value):
            return signal
    return None


class ScoutResponse(HarnessModel):
    """Scout 노드가 요청하는 출력 스키마. `.candidate`만 파이프라인으로 흐르고
    `.analysis_summary`는 버려진다(위 모듈 docstring)."""

    candidate: Candidate
    analysis_summary: AnalysisSummary
