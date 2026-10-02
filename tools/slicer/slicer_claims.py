"""Shared claim queue for sharded runs (#369).

With `--shard I/N` alone a shard slices the candidates at positions `p % N == I`, fixed before any
of them runs, so a few slow candidates (a query past its deadline, a backward trace grown to its
limits) can hold one shard long after the others are idle. With `--claim-db`, every shard instead
claims the next candidate nobody has taken from one SQLite file all shards share, so an idle
shard keeps pulling work.

A claim is a row keyed by candidate_key; the first INSERT wins. A shard may re-take a candidate it
claimed itself (its own `--resume` with the same I/N). A candidate claimed by a shard that then
died stays claimed and unfinished until that same I/N is resumed: no other shard takes it (there is
no lease -- an owner slow on one candidate is indistinguishable from a dead one without a
heartbeat, and a second shard re-slicing it would race the first). Unresumed, the merged
`--export-only` gives it the degraded slice (#364); tools/merge_slicer_shards.py --claim-db lists
such candidates with their owner. The queue picks WHICH candidates a shard slices, not how, so it
is not part of the run contract (same as `--shard`).

The file is bound to one run: the shard count on creation, the run signature and candidate list on
the first `bind()`. A leftover file from another run is refused instead of starving every shard.
The file must live on a host-local filesystem: WAL needs shared memory between the processes, which
network/9p mounts do not give.
"""

import random
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from slicer_checkpoint import digest


def _locked(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


class ClaimQueue:
    def __init__(self, path: Path, index: int, count: int, *, timeout: float = 60):
        self.path = path
        self.owner = f"{index}/{count}"
        path.parent.mkdir(parents=True, exist_ok=True)
        # The busy timeout covers the brief write lock another shard holds while inserting its own
        # claim. It does NOT cover the first `PRAGMA journal_mode=WAL` on a fresh file: N shards
        # opening it at once get `database is locked` immediately (#372 review, 8 procs on
        # Windows). So the whole bootstrap retries with backoff, up to the same timeout.
        deadline = time.monotonic() + timeout
        delay = 0.01
        while True:
            conn = sqlite3.connect(str(path), timeout=timeout, isolation_level=None)
            try:
                mode = self._bootstrap(conn, count)
            except sqlite3.OperationalError as exc:
                conn.close()
                if not _locked(exc) or time.monotonic() >= deadline:
                    raise
            except BaseException:
                conn.close()
                raise
            else:
                if mode == "wal":
                    break
                conn.close()
                # SQLite returns the old mode instead of raising when it cannot switch, which a
                # concurrent opener can also cause; past the deadline it is the filesystem.
                if time.monotonic() >= deadline:
                    raise ValueError(
                        f"Claim db {path} cannot use WAL (journal_mode={mode}); put --claim-db "
                        "on a host-local filesystem"
                    )
            time.sleep(delay * (1 + random.random()))
            delay = min(delay * 2, 0.5)
        self.conn = conn
        stored = int(self._meta("shard_count"))
        if stored != count:
            self.close()
            raise ValueError(
                f"Claim db {path} belongs to a run with {stored} shards, not {count}; "
                "give every run a fresh --claim-db file"
            )

    @staticmethod
    def _bootstrap(conn: sqlite3.Connection, count: int) -> str:
        """Create the tables; return the journal mode (anything but "wal" leaves them alone)."""
        mode = str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if mode != "wal":
            return mode
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS claims ("
                "candidate_key TEXT PRIMARY KEY, owner TEXT NOT NULL, claimed_at TEXT NOT NULL)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(
                "INSERT OR IGNORE INTO meta VALUES ('shard_count', ?)", (str(count),)
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return mode

    def _meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def bind(self, run_signature: str, candidates: list) -> None:
        """The first shard records the run; every later one must be slicing the same run."""
        expected = {"run_signature": run_signature, "candidates_sha256": digest(candidates)}
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.executemany(
                "INSERT OR IGNORE INTO meta VALUES (?, ?)", list(expected.items())
            )
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        for key, value in expected.items():
            if self._meta(key) != value:
                raise ValueError(
                    f"Claim db {self.path} was started by another run ({key} differs); "
                    "give every run a fresh --claim-db file"
                )

    def claim(self, candidate_key: str) -> bool:
        """True when this shard may slice the candidate: it just took it, or took it earlier."""
        try:
            self.conn.execute(
                "INSERT INTO claims VALUES (?, ?, ?)",
                (candidate_key, self.owner, datetime.now(UTC).isoformat()),
            )
            return True
        except sqlite3.IntegrityError:
            # Only a taken key means "someone else's". A lock past the busy timeout raises
            # OperationalError and must stop the shard, not skip the candidate silently.
            row = self.conn.execute(
                "SELECT owner FROM claims WHERE candidate_key=?", (candidate_key,)
            ).fetchone()
            return row is not None and row[0] == self.owner

    def close(self) -> None:
        self.conn.close()


def read_claims(path: Path) -> dict[str, tuple[str, str]]:
    """candidate_key -> (owner I/N, claimed_at) of an existing claim db, for the merge report."""
    if not path.is_file():
        raise ValueError(f"Claim db does not exist: {path}")
    conn = sqlite3.connect(str(path), timeout=60)
    try:
        return {
            key: (owner, claimed_at)
            for key, owner, claimed_at in conn.execute("SELECT * FROM claims")
        }
    finally:
        conn.close()
