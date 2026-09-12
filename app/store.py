"""SQLite storage for glucose readings. One row per Dexcom reading, keyed on timestamp."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    ts          INTEGER PRIMARY KEY,   -- unix epoch seconds (UTC)
    mg_dl       INTEGER NOT NULL,
    trend       INTEGER NOT NULL,      -- Dexcom trend code 0-9
    trend_arrow TEXT NOT NULL,
    trend_desc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts DESC);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def upsert(self, rows: Iterable[dict]) -> int:
        """Insert readings, ignoring ones we already have. Returns number inserted."""
        rows = list(rows)
        if not rows:
            return 0
        with self._lock:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO readings (ts, mg_dl, trend, trend_arrow, trend_desc) "
                "VALUES (:ts, :mg_dl, :trend, :trend_arrow, :trend_desc)",
                rows,
            )
            self._conn.commit()
            return self._conn.total_changes - before

    def latest(self) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def since(self, since: datetime, until: datetime | None = None) -> list[dict]:
        until = until or datetime.now(timezone.utc)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM readings WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
                (int(since.timestamp()), int(until.timestamp())),
            ).fetchall()
        return [dict(r) for r in rows]

    def last_hours(self, hours: float) -> list[dict]:
        return self.since(datetime.now(timezone.utc) - timedelta(hours=hours))

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
