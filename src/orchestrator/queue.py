"""bounded in-process 작업 큐와 재평가 카운터 (engineering-guide.md §1, architecture.md §3).

Queue 규칙: 4·6단계에서 탈락한 후보는 버리지 않고 `queued`로 남기고, 새 Evidence가
들어오면 그 후보만 재평가한다. 무한 재평가를 막는 상한(`requeue_max`)은 config에서 온다.

**재평가 횟수는 스키마에 필드를 추가하지 않고 여기서 run 범위 카운터로 들고 있는다.**
`src/schemas/` 변경은 8개 트랙 전원 공지 대상이고(roadmap.md §3), 이 값은 한 run
안에서만 의미가 있어 저장 객체에 남길 이유가 없다. 대신 카운터는 프로세스가 죽으면
사라진다 — 이어 달리기(resume)를 구현할 때 가장 먼저 다시 봐야 할 지점이다.

**v1 러너는 순차다.** 이 큐는 `deque` 하나이고 락이 없다. 스레드로 돌리려면 여기만
바꿔서는 안 되고 S1이 남긴 전제 셋을 함께 풀어야 한다(계획 §5.1):

1. `StateStore` 두 구현에 락이 없고 `sqlite3.connect(check_same_thread=True)` 기본값이다
2. `FakeModelClient._seen_prefixes`가 보호되지 않아 캐시 적중률이 실행마다 달라진다
3. `TraceWriter.seq`·`BudgetLedger` 누산에 락이 없어 순번과 비용이 뒤섞인다

셋을 함께 풀지 않으면 조용히 깨지는 대신 즉시 실패하도록 지금 상태를 유지한다.
"""

from collections import deque
from collections.abc import Iterator
from typing import Generic, TypeVar

ItemT = TypeVar("ItemT")


class QueueFull(Exception):
    """`queue_maxsize` 초과. 조용히 버리면 후보가 사라진 이유를 추적할 수 없다."""


class WorkQueue(Generic[ItemT]):
    """FIFO 작업 큐 + 객체별 재평가 횟수.

    두 관심사를 한 객체에 둔 것은 재평가가 곧 "큐에 다시 넣는 행위"이기 때문이다.
    카운터를 따로 두면 넣는 곳과 세는 곳이 갈라져 상한이 새기 쉽다.
    """

    def __init__(self, *, maxsize: int) -> None:
        if maxsize <= 0:
            raise ValueError(f"maxsize는 양수여야 한다: {maxsize}")
        self._maxsize = maxsize
        self._items: deque[ItemT] = deque()
        self._requeues: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[ItemT]:
        """비우지 않고 훑는다. 소비는 `pop()`으로만 한다."""
        return iter(self._items)

    def push(self, item: ItemT) -> None:
        if len(self._items) >= self._maxsize:
            raise QueueFull(f"큐가 가득 찼다(maxsize={self._maxsize}). queue_maxsize를 올린다")
        self._items.append(item)

    def pop(self) -> ItemT:
        """가장 오래된 항목. 비었으면 `IndexError` — 호출자가 `len()`으로 먼저 본다."""
        return self._items.popleft()

    def requeue_count(self, object_id: str) -> int:
        """지금까지의 재평가 횟수. 정책(`DecisionPoint.REQUEUE`)에 넘기는 신호다."""
        return self._requeues.get(object_id, 0)

    def mark_requeued(self, object_id: str) -> int:
        """재평가 1회를 기록하고 누적값을 돌려준다.

        상한 판정은 여기서 하지 않는다 — 임계값은 config에서 오고 판정은 정책 몫이다
        (engineering-guide.md §8.5).
        """
        count = self._requeues.get(object_id, 0) + 1
        self._requeues[object_id] = count
        return count

    def requeue_counts(self) -> dict[str, int]:
        """객체별 누적 재평가 횟수. 리포트에서 큐 회전을 설명하는 데 쓴다."""
        return dict(self._requeues)
