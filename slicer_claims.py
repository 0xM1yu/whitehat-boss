"""Shared claim queue for sharded runs (#369).

With `--shard I/N` alone a shard slices the candidates at positions `p % N == I`, fixed before any
of them runs, so a few slow candidates (a query past its deadline, a backward trace grown to its
limits) can hold one shard long after the others are idle. With `--claim-db`, every shard instead
claims the next candidate nobody has taken from one SQLite file all shards share, so an idle
shard keeps pulling work.

A claim is a row keyed by candidate_key; the first INSERT wins. A shard may re-take a candidate it
claimed itself (its own `--resume`). A candidate claimed by a shard that then died stays claimed and
unfinished: the merged `--export-only` gives it the degraded slice (#364). The queue picks WHICH
candidates a shard slices, not how, so it is not part of the run contract (same as `--shard`).
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class ClaimQueue:
    def __init__(self, path: Path, owner: str):
        self.owner = owner
        # One connection per process; the busy timeout covers the brief write lock another
        # shard holds while inserting its own claim.
        self.conn = sqlite3.connect(str(path), timeout=60, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS claims ("
            "candidate_key TEXT PRIMARY KEY, owner TEXT NOT NULL, claimed_at TEXT NOT NULL)"
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
            row = self.conn.execute(
                "SELECT owner FROM claims WHERE candidate_key=?", (candidate_key,)
            ).fetchone()
            return row is not None and row[0] == self.owner

    def close(self) -> None:
        self.conn.close()
