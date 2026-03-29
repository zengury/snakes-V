"""
EventStore — SQLite-backed event log.

WAL mode + NORMAL sync for safe multi-process writes.
Lifecycle state checkpoint is also stored here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    joint_name   TEXT,
    severity     TEXT NOT NULL,
    value        REAL,
    threshold    REAL,
    context_json TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_joint ON events(joint_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type  ON events(event_type, timestamp);
"""

_CREATE_LIFECYCLE = """
CREATE TABLE IF NOT EXISTS lifecycle_state (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    phase      TEXT NOT NULL,
    active_chain TEXT,
    updated_at TEXT NOT NULL
);
"""


class EventStore:
    """Thread-safe (WAL) SQLite event log and lifecycle state store.

    H2 fix: each thread gets its own sqlite3.Connection via threading.local().
    Sharing a single connection across threads with check_same_thread=False
    can cause corruption even with WAL mode when multiple threads write
    simultaneously.  Per-thread connections are the correct SQLite pattern.
    """

    def __init__(self, db_path: str = "storage/eventlog/events.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._local = threading.local()  # H2: thread-local connection storage
        self._init()

    def _connect(self) -> sqlite3.Connection:
        # Return (or create) the connection for the current thread.
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init(self) -> None:
        conn = self._connect()
        conn.executescript(_CREATE_EVENTS)
        conn.executescript(_CREATE_LIFECYCLE)
        conn.commit()

    # ---------------------------------------------------------------- events

    def append(
        self,
        event_type: str,
        joint_name: Optional[str],
        severity: str,
        value: float = 0.0,
        threshold: float = 0.0,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO events (timestamp, event_type, joint_name, severity, value, threshold, context_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(),
                event_type,
                joint_name,
                severity,
                value,
                threshold,
                json.dumps(context) if context else None,
            ),
        )
        conn.commit()

    def query_recent(
        self,
        joint_name: Optional[str] = None,
        hours: float = 24.0,
        event_type: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        query = "SELECT * FROM events WHERE timestamp > ?"
        params: list = [cutoff]
        if joint_name:
            query += " AND joint_name = ?"
            params.append(joint_name)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += f" ORDER BY timestamp DESC LIMIT {limit}"
        conn = self._connect()
        return [dict(row) for row in conn.execute(query, params)]

    # -------------------------------------------------------- lifecycle state

    def save_lifecycle_state(
        self, phase: str, active_chain: Optional[str] = None
    ) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO lifecycle_state (id, phase, active_chain, updated_at) "
            "VALUES (1, ?, ?, ?)",
            (phase, active_chain, datetime.now().isoformat()),
        )
        conn.commit()

    def load_lifecycle_state(self) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        row = conn.execute("SELECT * FROM lifecycle_state WHERE id = 1").fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        """Close the connection for the current thread."""
        conn: Optional[sqlite3.Connection] = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None


# Module-level singleton.
event_store = EventStore()
