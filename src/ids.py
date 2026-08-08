"""결정론적 식별자와 다이제스트.

engineering-guide.md §5 재현성: 같은 seed·조건으로 재실행하면 같은 결과가 나와야 한다.
난수나 시각에서 ID를 뽑으면 두 실행의 trace를 대조할 수 없으므로, ID는 전부
(run_id, 종류, 순번)에서 결정론적으로 만든다.

`src/adapters/`와 `src/orchestrator/` 양쪽이 쓰므로 최상위에 둔다 — 어댑터가
오케스트레이터를 import 하면 교체 가능성이 깨진다(engineering-guide.md §3).
"""

import hashlib
from datetime import datetime
from threading import Lock

from src.schemas.common import ID_PREFIXES

# 순번 자릿수. cand_0001 형태 (agent-contracts.md §3 예시).
_SEQ_WIDTH = 4


def sha256_digest(data: bytes) -> str:
    """`Sha256Digest` 형식(`sha256:` + 64 hex)의 다이제스트."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_digest(text.encode("utf-8"))


def new_run_id(started_at: datetime, sequence: int = 1) -> str:
    """`run_20260901_0001` 형태의 run 식별자 (agent-contracts.md §3 예시).

    시각에서 만들지만 `started_at`을 인자로 받는다 — 호출부가 시각을 주입해야
    재현 실행에서 같은 run_id를 만들 수 있다.
    """
    return f"{ID_PREFIXES['run']}{started_at:%Y%m%d}_{sequence:0{_SEQ_WIDTH}d}"


class IdFactory:
    """run 하나에서 쓰는 종류별 순번 생성기.

    같은 순서로 호출하면 같은 ID가 나온다. 순서가 달라지면 ID도 달라지는데, 그것이
    바로 재현성 테스트가 잡아야 할 신호다 — 안정 해시로 순서 의존성을 감추면
    파이프라인 순서가 흔들려도 눈치채지 못한다.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._counters: dict[str, int] = {}
        self._lock = Lock()

    def next(self, kind: str) -> str:
        """`kind`의 다음 ID. `kind`는 `ID_PREFIXES`의 키여야 한다."""
        prefix = ID_PREFIXES.get(kind)
        if prefix is None:
            raise ValueError(f"알 수 없는 객체 종류: {kind!r}. 있는 것: {sorted(ID_PREFIXES)}")
        with self._lock:
            seq = self._counters.get(kind, 0) + 1
            self._counters[kind] = seq
        return f"{prefix}{seq:0{_SEQ_WIDTH}d}"

    def issued(self) -> dict[str, int]:
        """종류별 발급 수. trace의 단계별 산출물 개수와 대조하는 데 쓴다."""
        return dict(self._counters)
