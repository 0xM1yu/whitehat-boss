"""Artifact 스키마 (파이프라인 0단계 산출물).

제안: architecture.md §3의 0단계는 출력으로 `Artifact[]`를 명시하지만
agent-contracts.md §3에는 이 구조체가 없다. 0단계가 "펌웨어 추출 → 파일 타입 분류 →
바이너리·소스·설정 식별 → artifact hash 부여"를 수행한다는 서술에서 필드를 도출했다.
"""

from src.schemas.common import HarnessModel, Identifier, NonEmptyStr, Sha256Digest
from src.schemas.enums import ArtifactKind


class Artifact(HarnessModel):
    """분석 대상 파일 하나.

    `path`는 run artifact 디렉터리 기준 상대 경로다. 절대 경로를 넣으면 재현 실행에서
    호스트마다 달라져 결과 비교가 깨진다(engineering-guide.md §5).
    """

    artifact_id: Identifier
    run_id: Identifier
    kind: ArtifactKind
    path: NonEmptyStr
    digest: Sha256Digest
    # `file(1)` 출력 등 분류 근거. 분류에 실패해도 kind=unknown으로 진행하므로 선택 필드다.
    media_type: str | None = None
    # 펌웨어에서 추출된 파일이면 원본 이미지의 artifact_id. binwalk 경로 추적용.
    parent_id: Identifier | None = None
