"""ScoutResponse — Scout의 래퍼 출력 `{candidate, analysis_summary}`.

Scout 프롬프트(v3)는 판정(`candidate`)과 근거(`analysis_summary`)를 **한 호출에서 함께** 낸다.
근거는 downstream으로 넘어가지 않는다 — 하네스에 `analysis_summary` 소비자가 없다. 그런데도
출력에 남기는 이유는 **모델이 근거를 써 보면 판정이 정확해진다**는 실측 때문이다(실험팀 로스터
선정: memory·injection은 근거 자세한 v14s, boundary는 간소화 v15s가 이겼다). 근거는 모델의
사고 발판이지 파이프라인 데이터가 아니다.

노드(`orchestrator/nodes/scout.py`)가 이 래퍼를 받아 `.candidate`만 취하고 `analysis_summary`는
버린다. 안쪽 `candidate`는 하네스 `Candidate`와 **정확히 같은** 구조라(프롬프트 v3가 "exactly the
nine Candidate keys"로 못박음) 그대로 파이프라인에 흐른다.
"""

from typing import Literal

from pydantic import Field

from src.schemas.candidate import Candidate
from src.schemas.common import HarnessModel, Location, NonEmptyStr


class CodeEvidenceItem(HarnessModel):
    """`analysis_summary.code_evidence`의 한 항목. 위치는 `file`·`line_*`만 쓰고 `symbol`은
    안 쓴다(프롬프트 v3) — `Location`의 `symbol`은 선택 필드라 생략하면 `None`이다."""

    location: Location
    description: NonEmptyStr


class AnalysisCondition(HarnessModel):
    """모델이 적용한 규칙의 필요조건과 그 충족 상태."""

    condition: NonEmptyStr
    status: Literal["confirmed", "disproven", "unknown"]


class AnalysisSummary(HarnessModel):
    """Scout의 근거 발판. 하네스는 이것을 **소비하지 않는다**(버린다) — 스키마로 타입만 잡아
    grammar가 모델 출력을 제약하게 한다. 프롬프트 v3의 「Analysis summary contract」와 같다."""

    claim: NonEmptyStr
    code_evidence: list[CodeEvidenceItem] = Field(default_factory=list)
    conditions: list[AnalysisCondition] = Field(default_factory=list)
    counterevidence: list[NonEmptyStr] = Field(default_factory=list)
    uncertainties: list[NonEmptyStr] = Field(default_factory=list)
    requested_context: list[NonEmptyStr] = Field(default_factory=list)


class ScoutResponse(HarnessModel):
    """Scout 노드가 요청하는 출력 스키마. `.candidate`만 파이프라인으로 흐르고
    `.analysis_summary`는 버려진다(위 모듈 docstring)."""

    candidate: Candidate
    analysis_summary: AnalysisSummary
