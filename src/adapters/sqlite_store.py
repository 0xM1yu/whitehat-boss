"""SqliteStateStore — `StateStore`의 파일 기반 구현 (docs/decisions/0001, ADR 0001).

stdlib `sqlite3`만 쓴다. 저장 형태는 종류별 컬럼 스키마가 아니라 **JSON 한 컬럼**이다.
`src/schemas/`는 여러 모듈이 동시에 건드리는 유일한 결합점이라 필드가
늘 때마다 DDL 마이그레이션을 요구하면 서로를 막는다. 분석 쿼리가 실제로 느려지면
그때 컬럼을 승격한다(DuckDB 재검토 조건도 같다).

읽을 때 `model_validate`로 되돌리므로 스키마 검증이 저장소 경계에서도 돈다
(engineering-guide.md §8.4).

**저장 형태 버전 게이트 (이슈 #281).** JSON 한 컬럼이라 필드가 바뀌어도 DDL은 그대로이고,
어긋남은 되읽기의 `model_validate`에서 처음 드러난다 — 행 하나가 `query()` 전체를
`ValidationError`로 죽인다. 그래서 파일에 저장 형태 판(`PRAGMA user_version`)을 적고 **여는
자리에서** 대조한다.

- 새 파일(아직 `objects` 표가 없다) -> 표를 만들고 `STATE_DB_SCHEMA_VERSION`을 적는다(한 트랜잭션).
- 판이 같다 -> 연다.
- 판이 0이고 표가 있다 -> 게이트 **전** 코드가 만든 파일이다. 연다(종전 동작 그대로).
  판을 새로 적지 않는다 — 그 안의 행이 어느 모양인지 모르므로 지금 판이라 적으면 거짓이다.
  되읽기가 실패할 수 있는 것도 종전 그대로다.
- 그 밖(더 새 판 · 더 옛 판) -> `StateDbVersionError`로 거부한다. 행을 하나도 안 읽는다.
  옛 판을 옮기는 변환은 없다 — 생기면 그 판에서만 받는다.

**판을 올리는 것은 사람이다.** `store_common.KIND_MAP`의 모델이 **한쪽 방향이라도** 못 읽는
행을 만들게 바뀌면 올린다 — 필드 추가(기본값이 있어도: `extra="forbid"`라 옛 코드가 새 행을 못
읽는다)·삭제·값 집합을 넓히거나 좁히기·뜻만 바뀐 필드. docstring만 바뀐 것은 아니다.
올리지 않고 바꾸면 게이트는 공회전한다 — 그것을 잡는 테스트는 없다(JSON 스키마 지문은 못
문다: `Finding.cwe`는 검증이 `None`을 받는데 JSON 스키마는 종전 그대로다 —
`src/schemas/finding.py`의 `_FindingCwe`).

판 1 = 이 게이트가 들어온 시점의 모양이다(`Finding.cwe`가 `no_signal`에서 `None`일 수 있는 판,
#525).

판 2 = 필드가 더 붙은 모양이다. 기본값이 있어 이 코드는 판 1 행도 읽지만 옛 코드는 새 행을
못 읽는다(`extra="forbid"`). 그래서 판 1 파일은 이제 거부된다. 판 2에 든 것은 아래다.

- `Evidence.harness_note`(#573) — 관측 머리에 붙는 하네스 노트. trace에서도 `harness_note`다.
- `Evidence.crashed`·`banner`(#232) — 크래시 관측. trace에서는 `crashed`·`crash_banner`다.
- `RunRecord.location_overlap`(#563) — 정적 신호 위치 겹침 수.
- `RunRecord.serving_launch_intended`(#99) — 교대 프로필 + 장비일 때 장비 파일이 의도한 기동 인자.
  판 1 코드는 이 필드를 든 행을 못 읽는다(`extra="forbid"`).
- `RunRecord`의 trace 집계 필드 넷(#613·#618·#619) — `schema_failure_max_run_by_role`·
  `schema_recovered_calls_by_role`·`pov_inspect_max_run_by_finding`·`trace_aggregate_failures`.
- `RunRecord.keep_responses`(#618·#612) — main `f0cdba76`가 더하면서 판을 **안 올렸다.**
  판 1이라 적힌 파일에 이 필드가 든 행이 섞여 있을 수 있다(선례). 판 1을 적은 `state.db`에
  그 필드를 든 행이 이미 있으면 판 1 코드로는 못 읽힌다. 그 run은 되읽기를 안 하므로(서빙에
  resume 경로가 없다) 서빙 결함은 아니고, 그 선례를 이 판에서 같이 닫는다.

게이트는 **이 코드부터** 돈다 — 게이트 전 코드는 판을 안 읽으므로, 그 코드로 이 판의
파일을 여는 것은 여전히 막지 못한다.
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

# 저장 형태 판. 올리는 규칙은 모듈 docstring. 0은 「판을 안 적은 파일」(게이트 전)이라 쓰지 않는다.
STATE_DB_SCHEMA_VERSION = 2

# 판을 올린 변경 — 오류 문구가 이 표를 읽어, 옛 파일을 만난 사람이 **어느 체크아웃이 그 판을
# 읽는지** 안다. 판을 올리는 PR은 여기에 한 줄을 더한다. 커밋 해시는 squash로 바뀔 수 있어
# 이슈 번호가 기준이다.
_VERSION_INTRODUCED_BY = {
    2: (
        "이슈 #232 — `Evidence.crashed`·`banner` 추가(커밋 19c2bad). 같은 판에 이슈 #573의 "
        "`Evidence.harness_note`와 이슈 #563의 `RunRecord.location_overlap`, 이슈 #613·#618·#619의 "
        "`RunRecord` trace 집계 필드 넷(`schema_failure_max_run_by_role`·"
        "`schema_recovered_calls_by_role`·`pov_inspect_max_run_by_finding`·"
        "`trace_aggregate_failures`), 이슈 #99의 `RunRecord.serving_launch_intended`, "
        "main `f0cdba76`의 `RunRecord.keep_responses`(#618·#612)도 든다 — 그 커밋은 판을 "
        "안 올렸다"
    ),
}


class StateDbVersionError(RuntimeError):
    """`state.db`의 저장 형태 판이 이 코드가 읽는 판과 달라 열기를 거부했다."""


class SqliteStateStore:
    """`StateStore` 구현. `path=":memory:"`면 프로세스 수명 DB."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        try:
            self._open_gate()
        except BaseException:
            self._conn.close()
            raise

    def _open_gate(self) -> None:
        """판을 대조하고, 새 파일이면 표와 판을 적는다. 거부는 행을 읽기 **전**이다."""
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        has_table = (
            self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'objects'"
            ).fetchone()
            is not None
        )
        if not has_table and version == 0:
            # 표·인덱스·판을 한 트랜잭션으로 — 사이에서 죽어도 (표 있음 · 판 0)이 안 남는다.
            # PRAGMA는 자리표시자를 안 받는다 — 값은 모듈 상수 int다.
            version_pragma = f"PRAGMA user_version = {int(STATE_DB_SCHEMA_VERSION)};"
            self._conn.executescript(f"BEGIN;{_SCHEMA}{version_pragma}COMMIT;")
            return
        if version == 0 or version == STATE_DB_SCHEMA_VERSION:
            # 판 0 = 게이트 전 파일(모듈 docstring). 표가 없으면 만든다 — 종전 동작.
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            return
        newer = version > STATE_DB_SCHEMA_VERSION
        # 옛 파일이면 「이 코드의 다음 판」을 올린 변경을 댄다 — 그 앞 체크아웃이 이 파일을 읽는다.
        introduced = _VERSION_INTRODUCED_BY.get(version + 1)
        hint = (
            f" (판 {version + 1}을 올린 변경: {introduced} — 판 {version} 파일은 그 변경"
            " 이전 체크아웃이 읽는다.)"
            if not newer and introduced is not None
            else ""
        )
        raise StateDbVersionError(
            f"{self.path}: state.db 저장 형태 판 {version}은 이 코드가 읽는 판 "
            f"{STATE_DB_SCHEMA_VERSION}과 다르다 — "
            + (
                "더 새 코드가 쓴 파일이다. 그 판을 읽는 체크아웃으로 연다."
                if newer
                else "더 옛 코드가 쓴 파일이고 옮기는 변환이 없다. 그 판의 체크아웃으로 연다."
            )
            + hint
            + " 행은 하나도 읽지 않았다(이슈 #281)."
        )

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
