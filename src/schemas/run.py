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
    # 하네스 저장소에 미커밋 변경이 있었는가. `repo_commit`은 HEAD만 읽고 `source_hash`는
    # *분석 대상*만 잡으므로, 그 사이에 "하네스 자신이 dirty했다"는 사실이 빠져 있었다.
    # 설정을 고친 채로 돈 run이 manifest만 보면 재현 가능해 보이던 것이 이 필드의 계기다.
    #
    # `bool`이 아니라 `bool | None`인 것은 의도다. git이 없거나 저장소가 아닐 수 있는데
    # 그 경우를 `False`로 적으면 "깨끗하다"는 적극적인 거짓말이 된다. `None`이 "확인 못 함"이다.
    # 기본값을 두지 않는 것도 의도다 — 이 모듈 docstring대로 재현성 항목은 전부 필수 필드다.
    repo_dirty: bool | None
    # 분석 대상 소스 트리의 해시. repo_commit만으로는 미커밋 변경을 잡지 못한다.
    source_hash: Sha256Digest
    # 역할 → model·tokenizer·quantization checksum
    model_checksums: dict[str, Sha256Digest]
    # 역할 → prompt_version
    prompt_versions: dict[str, NonEmptyStr]
    # 도구 이름 → 컨테이너 이미지 digest
    tool_images: dict[str, Sha256Digest]
    # 그 run이 **실제로 쓴** 도구 런타임 (`fake` | `docker` | `singularity` | `podman`).
    #
    # 닫힌 집합은 `config.AdapterSelection.tool_runner`가 들고 있다. 여기서 `Literal`을
    # 다시 적지 않는 것은 `src/schemas/`가 `src/config.py`를 import 할 수 없기 때문이고
    # (config가 이 모듈을 import 한다), 같은 열거를 두 곳에 두면 갈라진다.
    #
    # 필수 필드인 이유는 두 가지다.
    #
    # 1. `--tool-runner` 오버라이드가 프로필에 적힌 값과 실제 값을 갈라놓을 수 있다
    #    (ADR 0005). evaluation.md §2.3 규칙 2("조건 간 차이는 프로필과 단계 활성화만")에
    #    예외를 여는 것이므로, 기록에 남지 않으면 나중에 비교표에서 이 run들을 구분할 수
    #    없다.
    # 2. **오버라이드가 없어도 필요하다.** `tool_images`의 digest는 런타임 사이에서
    #    비교할 수 없다 — Docker는 이미지 config의 sha256을, Singularity는 SIF 파일의
    #    sha256을 적는다(STATUS.md §10. 같은 이미지가 다른 값으로 잡힌 실측이 있다).
    #    런타임이 함께 기록되면 최소한 digest를 런타임별로 묶어서 볼 수는 있다. §10의
    #    해법은 아니지만 그 전제 조건이다.
    #
    # `incomplete_fields()`에는 넣지 않는다. 값이 config에서 항상 유도되어 빌 수가 없고,
    # `fake`로 돈 run은 이미 `tool_images`가 비어 경고에 걸린다 — 같은 사실을 두 줄로
    # 내면 진짜 문제가 묻힌다(runner.py의 같은 판단 참조).
    tool_runner: NonEmptyStr
    # 이 run이 **어느 장비에서** 돌았는가. `--device`가 준 라벨이고, 없으면 `None`이다.
    #
    # 두 장비가 모두 지원 대상이므로(docs/portability.md) 같은 프로필·같은 조건으로 돈 두 run이
    # 다른 하드웨어의 결과일 수 있다. 그런데 기록에는 그것을 가릴 것이 하나도 없었다 —
    # `model_checksums`는 설계상 장비 불변이고 endpoint는 애초에 남지 않는다. 대학
    # 학생이 Slurm에서 낸 결과와 팀의 ATOM 결과가 비교표에서 섞이는 것을 막는 필드다.
    #
    # **호스트명으로 자동으로 채우지 않는다.** 그러면 한 필드에 "사람이 붙인 장비 라벨"과
    # "하네스 프로세스가 돈 호스트명"이라는 **서로 다른 두 사실**이 들어가고, 기록만 보면
    # 어느 쪽인지 구분할 수 없다. 게다가 그 둘은 자주 어긋난다 — 로그인 노드에서 하네스를
    # 돌리면 GPU 작업이 한 톨도 일어나지 않은 기계 이름이 적힌다. `None`은 `repo_dirty`의
    # `None`과 같은 뜻이다: **확인 못 함.** 값과 섞지 않는다.
    #
    # `tool_runner`와 달리 `incomplete_fields()`에 들어간다. 그쪽은 config에서 항상
    # 유도되어 빌 수가 없지만, 이 값은 사람이 안 주면 실제로 빈다.
    device: str | None
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
