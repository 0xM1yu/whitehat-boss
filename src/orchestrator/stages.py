"""파이프라인 단계 번호 (architecture.md §3).

단계 번호는 0~9로 고정이고 `RunRecord.stopped_after`·trace의 `stage` 필드가 같은 값을
쓴다. 정수 리터럴을 코드 여기저기에 흩뿌리면 지표 집계(evaluation.md §5)가 어느 단계를
세는지 읽을 수 없으므로 이름을 붙여 한 곳에 둔다.
"""

from enum import IntEnum


class Stage(IntEnum):
    """architecture.md §3 표의 단계 번호와 이름."""

    PREPROCESS = 0
    INDEX = 1
    SIGNALS = 2
    RETRIEVAL = 3
    SCOUT = 4
    AUDITOR = 5
    GATE = 6
    CRITIC = 7
    VALIDATE = 8
    REPORT = 9


# `RunRecord.stopped_after`의 상한과 같아야 한다 (src/schemas/run.py `_MAX_STAGE`).
MAX_STAGE = Stage.REPORT

# Evidence Gate 호출 지점. E3은 Critic 출력이, E4는 도구 검증 로그가 있어야 성립하므로
# 한 번에 매겨지지 않고 세 단계에서 각각 재판정된다 (계획 §2, enums.EvidenceGrade).
GATE_STAGES = (Stage.GATE, Stage.CRITIC, Stage.VALIDATE)
