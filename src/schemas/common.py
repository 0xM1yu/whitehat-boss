"""여러 스키마가 공유하는 기반 타입.

agent-contracts.md §3의 `location`·`produced_by` 블록이 Candidate/Evidence/Finding/Decision에
반복 등장하므로 한 곳에 둔다.
"""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from src.schemas.enums import Role

# agent-contracts.md §3 예시가 쓰는 ID 접두사. 생성기는 PR 2(orchestrator)에 있다.
ID_PREFIXES = {
    "artifact": "art_",
    "candidate": "cand_",
    "evidence": "ev_",
    "finding": "find_",
    "missing_evidence": "mev_",
    "decision": "dec_",
    "capsule": "cap_",
    "run": "run_",
}

# "sha256:" + 64 hex. agent-contracts.md §3 Evidence.digest, model-serving.md §5 checksum.
Sha256Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

# 전부 0인 digest. **형식은 유효하지만 값은 자리표시자다** — 위 패턴을 그대로 통과하므로
# 스키마는 이것을 막지 못한다. `config/models.yaml`의 checksum이 실측 전까지 이 값이고,
# 그 상태로 돈 run은 기록상 재현 가능해 보이지만 어느 모델이었는지 지시하지 못한다.
# 판정하는 쪽(`runner.incomplete_fields`, `cli`)이 여기서 가져다 쓴다 — 같은 리터럴을
# 여러 곳에 적으면 한 곳만 고쳐도 아무도 모른다.
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64

# 비어 있지 않은 식별자. 접두사까지 강제하지는 않는다 — 접두사 규칙이 문서에 명문화된
# 것은 예시뿐이므로 값 형태만 최소로 제약한다.
Identifier = Annotated[str, Field(min_length=1, max_length=128)]

# CWE 식별자. agent-contracts.md §3의 Candidate.suspected_cwe와 Finding.cwe가 공유한다.
# ⏵ 2026-10-09 — `Finding.cwe`는 `Cwe | None`이다(`no_signal`에서만 `None` — 이슈 #525).
Cwe = Annotated[str, Field(pattern=r"^CWE-\d+$")]

# 비어 있지 않은 자유 텍스트. 필수 서술 필드를 빈 문자열로 채우는 것을 막는다.
NonEmptyStr = Annotated[str, Field(min_length=1)]


class HarnessModel(BaseModel):
    """전 스키마의 기반 클래스.

    - `frozen=True`: 노드는 순수 함수이므로(engineering-guide.md §4) 상태 객체를
      제자리 변경하지 않는다. 전이는 새 객체를 만들어 표현한다.
    - `extra="forbid"`: 여러 모듈이 이 스키마에 붙으므로 오타 필드가
      조용히 통과하면 안 된다.

    **전이 시 주의**: pydantic v2의 `model_copy(update=...)`는 **검증을 수행하지
    않는다.** `Field` 제약도, `Literal`도, `model_validator`도 전부 우회한다. 즉
    `model_copy`로는 `repro_spec` 없는 `proven` Finding도 만들어진다. 조건부 불변식을
    가진 모델(Finding, ContextCapsule, RunRecord, Evidence)의 상태 전이는 반드시
    `transition()`을 쓴다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    def transition(self, **updates: object) -> "HarnessModel":
        """검증을 거쳐 필드를 갱신한 새 객체를 반환한다.

        `model_copy(update=...)`와 달리 전체 모델을 재검증하므로, 상태 전이가
        불변식을 깨면 그 자리에서 실패한다.
        """
        return type(self).model_validate({**self.model_dump(), **updates})


def to_utc_iso(value: datetime) -> str:
    """UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ`로 직렬화 (agent-contracts.md §3 말미)."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class UtcModel(HarnessModel):
    """datetime 필드를 문서 규약대로 직렬화하는 모델.

    문서가 정한 표기는 `Z` 접미사가 붙은 초 단위 UTC다. pydantic 기본 직렬화는
    `+00:00`과 마이크로초를 내보내므로 재현성 비교(diff)에서 어긋난다.
    """

    @field_serializer("*", when_used="json")
    def _serialize_datetimes(self, value: object) -> object:
        if isinstance(value, datetime):
            return to_utc_iso(value)
        return value


class Location(HarnessModel):
    """코드 위치.

    agent-contracts.md §3에서 Candidate는 file·symbol·line_start·line_end를 모두 쓰고
    Evidence는 file·line_start만 쓴다. symbol과 line은 그래서 선택 필드다.
    """

    file: str = Field(min_length=1)
    symbol: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @field_validator("line_end")
    @classmethod
    def _end_after_start(cls, value: int | None, info) -> int | None:  # noqa: ANN001
        start = info.data.get("line_start")
        if value is not None and start is not None and value < start:
            raise ValueError(f"line_end({value}) < line_start({start})")
        return value

    def overlaps(self, other: "Location") -> bool:
        """같은 파일에서 줄 범위가 겹치는가.

        Evidence Gate E1의 "코드 경로가 후보와 일치" 판정에 쓴다(PR 2).
        한쪽이라도 줄 정보가 없으면 파일 일치만으로 겹침으로 본다 — 정적분석 신호가
        파일 단위로만 오는 경우가 있어 줄 부재를 불일치로 처리하면 신호를 잃는다.
        """
        if self.file != other.file:
            return False
        a_start = self.line_start
        b_start = other.line_start
        if a_start is None or b_start is None:
            return True
        a_end = self.line_end or a_start
        b_end = other.line_end or b_start
        return a_start <= b_end and b_start <= a_end


class ProducedBy(HarnessModel):
    """어떤 역할·모델·프롬프트 버전이 만든 산출물인지.

    조건 비교(evaluation.md §2.3)와 재현성(engineering-guide.md §5)의 기반이므로
    세 필드 모두 필수다.
    """

    role: Role
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
