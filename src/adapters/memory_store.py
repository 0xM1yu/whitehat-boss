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

    def query(self, kind: ObjectKind, **filters: object) -> list[StoredObject]:
        return [
            obj
            for (row_kind, _), obj in self._rows.items()
            if row_kind == kind and filters_match(obj, filters)
        ]
