"""InMemoryStateStore — `StateStore`의 프로세스 내 구현.

dev_fake 프로필의 기본값이다. 디스크를 만들지 않으므로 테스트가 서로 오염되지 않고
`gc`로 지울 것도 없다. 대신 프로세스가 끝나면 사라지므로 checkpoint/resume은 안 된다 —
그것이 필요한 프로필은 SqliteStateStore를 쓴다(docs/decisions/0001).
"""

from src.adapters.store_common import filters_match, object_id, resolve_kind
from src.interfaces.state_store import ObjectKind, StoredObject


class InMemoryStateStore:
    """`StateStore` 구현. (종류, ID) → 객체 dict 하나."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], StoredObject] = {}

    def put(self, obj: StoredObject) -> str:
        kind = resolve_kind(obj)
        obj_id = object_id(obj, kind)
        self._rows[(kind, obj_id)] = obj
        return obj_id

    def get(self, kind: ObjectKind, id: str) -> StoredObject | None:  # noqa: A002
        return self._rows.get((kind, id))

    def close(self) -> None:
        """놓을 자원이 없다 — `StateStore` 계약을 맞추는 자리다."""

    def query(self, kind: ObjectKind, **filters: object) -> list[StoredObject]:
        """ID 오름차순. SqliteStateStore의 `ORDER BY obj_id`와 같은 순서여야 한다.

        정렬 계약이 두 구현에서 갈리면 노드가 결과를 순회하는 순간 프로필에 따라 실행
        순서가 달라지고, 그대로 "같은 seed·조건이면 같은 결과"(engineering-guide.md §5)가
        깨진다. dict 삽입 순서는 계약이 아니다.
        """
        rows = [
            (row_id, obj)
            for (row_kind, row_id), obj in self._rows.items()
            if row_kind == kind and filters_match(obj, filters)
        ]
        return [obj for _, obj in sorted(rows, key=lambda row: row[0])]
