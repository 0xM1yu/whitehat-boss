"""SqliteStateStore — `StateStore`의 파일 기반 구현 (docs/decisions/0001, ADR 0001).

stdlib `sqlite3`만 쓴다. 저장 형태는 종류별 컬럼 스키마가 아니라 **JSON 한 컬럼**이다.
`src/schemas/`는 여러 모듈이 동시에 건드리는 유일한 결합점이라 필드가
늘 때마다 DDL 마이그레이션을 요구하면 서로를 막는다. 분석 쿼리가 실제로 느려지면
그때 컬럼을 승격한다(DuckDB 재검토 조건도 같다).

읽을 때 `model_validate`로 되돌리므로 스키마 검증이 저장소 경계에서도 돈다
(engineering-guide.md §8.4).
"""

import json
import sqlite3
from pathlib import Path

from src.adapters.store_common import filters_match, model_for, object_id, resolve_kind
from src.interfaces.state_store import ObjectKind, StoredObject

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    kind    TEXT NOT NULL,
    obj_id  TEXT NOT NULL,
    run_id  TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (kind, obj_id)
);
CREATE INDEX IF NOT EXISTS objects_by_run ON objects (kind, run_id);
"""


class SqliteStateStore:
    """`StateStore` 구현. `path=":memory:"`면 프로세스 수명 DB."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def put(self, obj: StoredObject) -> str:
        kind = resolve_kind(obj)
        obj_id = object_id(obj, kind)
        self._conn.execute(
            "INSERT INTO objects (kind, obj_id, run_id, payload) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (kind, obj_id) DO UPDATE SET run_id = excluded.run_id, "
            "payload = excluded.payload",
            (kind, obj_id, getattr(obj, "run_id", None), obj.model_dump_json()),
        )
        self._conn.commit()
        return obj_id

    def get(self, kind: ObjectKind, id: str) -> StoredObject | None:  # noqa: A002
        row = self._conn.execute(
            "SELECT payload FROM objects WHERE kind = ? AND obj_id = ?", (kind, id)
        ).fetchone()
        if row is None:
            return None
        return self._load(kind, row[0])

    def query(self, kind: ObjectKind, **filters: object) -> list[StoredObject]:
        """`run_id` 필터만 SQL로 내리고 나머지는 파이썬에서 건다.

        run_id는 인덱스가 있고 결과 집합을 가장 크게 줄인다. 그 외 필드까지 SQL로
        내리려면 JSON 경로를 문자열로 조립해야 하는데, 필드명이 그대로 쿼리에 들어가는
        구조는 나중에 필터가 외부 입력에서 오는 순간 주입 경로가 된다.
        """
        sql = "SELECT payload FROM objects WHERE kind = ?"
        params: list[object] = [kind]
        remaining = dict(filters)
        run_id = remaining.pop("run_id", None)
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY obj_id"
        rows = self._conn.execute(sql, params).fetchall()
        loaded = [self._load(kind, row[0]) for row in rows]
        return [obj for obj in loaded if filters_match(obj, remaining)]

    def close(self) -> None:
        """연결을 닫는다. 두 번 불러도 된다(`sqlite3.Connection.close`가 멱등이다)."""
        self._conn.close()

    def __enter__(self) -> "SqliteStateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _load(kind: ObjectKind, payload: str) -> StoredObject:
        model = model_for(kind)
        return model.model_validate(json.loads(payload))
