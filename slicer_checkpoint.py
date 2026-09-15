"""Transactional, single-writer checkpoints for the standalone slicer."""

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Checkpoint:
    """A process lock spans queries; a SQLite transaction only spans a stage commit.

    OS locks are released on process death. A stale lock-file's existence is not a
    lock, and deleting it would let a second writer lock a different inode.
    """

    def __init__(self, path: Path, *, resume: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(path) + ".lock", "a+b")
        self.conn = None
        try:
            self._lock()
            if path.exists() != resume:
                raise ValueError(
                    "Checkpoint exists; use --resume"
                    if path.exists()
                    else "Checkpoint does not exist"
                )
            self.conn = sqlite3.connect(path)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stages (
                    candidate_key TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY, started TEXT NOT NULL, ended TEXT,
                    payload TEXT NOT NULL);
            """)
            self.bind("checkpoint_version", "slicer-checkpoint-v2")
        except BaseException:
            self.close()
            raise

    def _lock(self) -> None:
        if os.name == "nt":
            import msvcrt

            self.lock.seek(0)
            if not self.lock.read(1):
                self.lock.write(b"\0")
                self.lock.flush()
            self.lock.seek(0)
            msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def get(self, key: str):
        row = self.conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, value: object) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value",
                (key, canonical(value)),
            )

    def bind(self, key: str, value: object) -> None:
        previous = self.get(key)
        if previous is None:
            self.set(key, value)
        elif canonical(previous) != canonical(value):
            raise ValueError(f"Checkpoint mismatch: {key}; start a new checkpoint")

    def load(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload, sha256 FROM stages WHERE candidate_key=?", (key,)
        ).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        if digest(value) != row[1]:
            raise ValueError(f"Checkpoint payload checksum mismatch: {key}")
        if value.get("phase") not in {"pending", "flow_saved", "context_saved", "complete"}:
            raise ValueError(f"Invalid checkpoint phase: {key}")
        return value

    def save(self, key: str, value: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO stages VALUES (?,?,?) ON CONFLICT(candidate_key) "
                "DO UPDATE SET payload=excluded.payload, sha256=excluded.sha256",
                (key, canonical(value), digest(value)),
            )

    def begin(self, settings: dict) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO attempts(started,payload) VALUES (?,?)",
                (utc_now(), canonical(settings)),
            )
        return cursor.lastrowid

    def end(self, attempt: int, result: dict) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE attempts SET ended=?, payload=? WHERE id=?",
                (utc_now(), canonical(result), attempt),
            )

    def attempts(self) -> list[dict]:
        return [
            {"attempt_id": i, "started": start, "ended": end, **json.loads(payload)}
            for i, start, end, payload in self.conn.execute(
                "SELECT id, started, ended, payload FROM attempts ORDER BY id"
            )
        ]

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
