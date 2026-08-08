"""Evidence 스키마 (agent-contracts.md §3, 파이프라인 2·8단계 산출물)."""

from datetime import datetime

from pydantic import model_validator

from src.schemas.common import (
    Identifier,
    Location,
    NonEmptyStr,
    Sha256Digest,
    UtcModel,
)
from src.schemas.enums import EvidenceKind


class Evidence(UtcModel):
    """관찰된 사실 하나. Evidence Store에 append-only로 쌓인다.

    `ok`/`exit_code`/`error`는 문서 예시에는 없다. engineering-guide.md §8.3이
    "에러는 삼키지 않는다. 도구 실패도 `Evidence`(실패 사유 포함)로 기록한다"를 요구하고,
    §6이 trace에 "도구 실행 명령·종료 코드·소요 시간"을 남기라고 하므로 신설했다.
    도구가 실패해도 그 사실 자체가 신호다(architecture.md §3 2단계 "실패해도 진행").
    """

    evidence_id: Identifier
    run_id: Identifier
    kind: EvidenceKind
    # 신호를 만든 도구. "asan", "semgrep", "clang-tidy" 등.
    source: NonEmptyStr
    artifact_id: Identifier
    # 정적분석 신호가 파일 단위로만 오는 경우가 있어 위치는 선택 필드다.
    location: Location | None = None
    # 원문 로그 경로(artifacts/ 기준 상대). 로그 본문은 스키마에 싣지 않는다.
    payload_ref: NonEmptyStr
    digest: Sha256Digest
    observed_at: datetime
    # 도구 실행 결과. 정적 신호처럼 실행 개념이 없는 Evidence는 기본값(성공)을 쓴다.
    ok: bool = True
    exit_code: int | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _error_matches_ok(self) -> "Evidence":
        """실패에는 사유가, 성공에는 사유가 없어야 한다.

        사유 없는 실패 기록은 "에러를 삼킨" 것과 구분되지 않고, 사유가 붙은 성공 기록은
        지표 집계에서 실패로 오분류된다.
        """
        if self.ok and self.error is not None:
            raise ValueError("ok=True인 Evidence에 error를 넣을 수 없다")
        if not self.ok and not self.error:
            raise ValueError("ok=False인 Evidence는 error가 필요하다 (engineering-guide §8.3)")
        return self
