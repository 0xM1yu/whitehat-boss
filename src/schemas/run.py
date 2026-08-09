"""RunRecord 스키마 (agent-contracts.md §3, engineering-guide.md §5).

evaluation.md §6: "기록이 불완전한 run의 결과는 비교표에 넣지 않는다."
따라서 재현성 필수 항목은 전부 **필수 필드**다. 선택으로 두면 누락이 조용히 통과하고,
그 run은 나중에 비교표에서 버려진다 — 실행 비용을 쓰고 나서 알게 되는 것이 최악이다.
"""

from datetime import datetime
from typing import Annotated

from pydantic import Field, model_validator

from src.schemas.common import (
    HarnessModel,
    Identifier,
    NonEmptyStr,
    Sha256Digest,
    UtcModel,
)

# evaluation.md §2.2: 2×2 설계(§2.1)와 파이프라인 조건(§2.2)이 같은 라벨을 다른 축으로
# 쓴다. 어느 정의인지 라벨에 박아 두지 않으면 비교표가 섞인다. 라벨 통합은 D1(open-questions).
# `'`는 파일명·URL·쉘에서 깨지므로 `prime`으로 표기한다.
CONDITION_PATTERN = r"^(B1|B1prime|B3|B3prime)@(2x2|pipeline)$"

# architecture.md §3의 파이프라인 단계 번호는 0~9로 고정이다.
_MAX_STAGE = 9


class Budget(HarnessModel):
    """run 단위 계산 예산 (architecture.md §5 Budget Ledger).

    evaluation.md §2.3의 공정성 규칙 1: 모든 조건은 동일 예산에서 실행한다.
    """

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    gpu_seconds: int = Field(ge=0)


class Sampling(HarnessModel):
    """샘플링 설정 (engineering-guide.md §5 "random seed와 sampling 설정").

    seed와 달리 이 값들은 모델 게이트웨이 요청 파라미터다. 조건 간 비교에서 고정해야
    하므로 RunRecord에 남긴다.
    """

    temperature: float = Field(ge=0.0)
    top_p: float = Field(gt=0.0, le=1.0)


class RunRecord(UtcModel):
    """한 번의 실행에 대한 재현성 기록.

    **전이 시 주의**: `finished_at`은 실행이 끝난 뒤 채워지므로 반드시 전이를 탄다.
    `HarnessModel.transition()`을 써야 시간 순서 검증이 실제로 돈다.
    """

    run_id: Identifier
    # 예: "B3prime@pipeline". 어느 정의의 조건인지 라벨에 포함한다.
    condition: Annotated[str, Field(pattern=CONDITION_PATTERN)]
    # git commit SHA-1 (40 hex). sha256 digest와 형식이 다르므로 별도 제약이다.
    repo_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    # 분석 대상 소스 트리의 해시. repo_commit만으로는 미커밋 변경을 잡지 못한다.
    source_hash: Sha256Digest
    # 역할 → model·tokenizer·quantization checksum
    model_checksums: dict[str, Sha256Digest]
    # 역할 → prompt_version
    prompt_versions: dict[str, NonEmptyStr]
    # 도구 이름 → 컨테이너 이미지 digest
    tool_images: dict[str, Sha256Digest]
    budget: Budget
    # 해석된(resolved) 임계값. 예: {"scout_threshold": 0.5}. 값은 config에서 오고
    # 여기에는 그 run에서 실제로 쓰인 값을 박제한다.
    thresholds: dict[str, float]
    seed: int
    sampling: Sampling
    # KB 스냅샷 버전 태그 (knowledge-base.md §7.4).
    kb_snapshot: NonEmptyStr
    started_at: datetime
    # 아래 둘은 실행 중에는 비어 있다. 완주하면 finished_at만,
    # 예산 소진 등으로 중단되면 stopped_after까지 찬다(architecture.md §5 Budget Ledger).
    finished_at: datetime | None = None
    # 중단된 파이프라인 단계 번호 (architecture.md §3의 0~9).
    stopped_after: int | None = Field(default=None, ge=0, le=_MAX_STAGE)

    @model_validator(mode="after")
    def _finished_after_started(self) -> "RunRecord":
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at이 started_at보다 이르다")
        return self

    @property
    def is_complete(self) -> bool:
        """비교표에 넣을 수 있는 run인가 (evaluation.md §6).

        필수 항목은 스키마가 이미 강제하므로, 남는 조건은 "끝났는가" 하나다.
        중단된 run(`stopped_after`가 있는)은 부분 결과이므로 비교 대상이 아니다.
        """
        return self.finished_at is not None and self.stopped_after is None
