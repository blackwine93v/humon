"""Schema migrations.

A tiny forward-only migration runner: each entry in ``MIGRATIONS`` is applied
once, tracked by ``schema_migrations``. Keep migrations append-only so existing
deployments upgrade cleanly on restart.
"""

from __future__ import annotations

import sqlite3

# Each migration is (version, SQL). Never edit a shipped migration — add a new one.
MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id           TEXT PRIMARY KEY,       -- session_ref (e.g. slack thread)
            channel      TEXT NOT NULL,
            user         TEXT,
            summary      TEXT,                   -- compacted history (FR-3.5)
            plan         TEXT,                   -- current plan JSON (FR-3.2)
            status       TEXT NOT NULL DEFAULT 'idle',
            created_at   REAL NOT NULL,
            updated_at   REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role         TEXT NOT NULL,
            content      TEXT NOT NULL DEFAULT '',
            tool_calls   TEXT,                   -- JSON
            tool_call_id TEXT,
            created_at   REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

        CREATE TABLE IF NOT EXISTS audit_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id    TEXT,
            tool          TEXT NOT NULL,
            args          TEXT,                  -- JSON of requested args
            permission    TEXT,
            decision      TEXT NOT NULL,         -- allow | deny | require_approval
            approver      TEXT,                  -- identity if human-approved
            exit_status   TEXT,                  -- ok | error | denied
            duration_ms   INTEGER,
            output_hash   TEXT,                  -- sha256 of (truncated) output
            created_at    REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

        CREATE TABLE IF NOT EXISTS memory_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            kind        TEXT NOT NULL DEFAULT 'note',  -- note | fact | episodic
            text        TEXT NOT NULL,
            meta        TEXT,                    -- JSON
            created_at  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory_notes(kind);

        CREATE TABLE IF NOT EXISTS tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT,
            description  TEXT NOT NULL,
            schedule     TEXT NOT NULL,          -- cron-ish or 'once'
            next_run     REAL,
            scope        TEXT,                   -- pre-approved scope JSON (OQ#2)
            enabled      INTEGER NOT NULL DEFAULT 1,
            last_run     REAL,
            last_status  TEXT,
            created_at   REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_next ON tasks(enabled, next_run);
        """,
    ),
]


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL DEFAULT (unixepoch()))"
    )
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()
    current = int(row["v"] if isinstance(row, sqlite3.Row) else row[0])
    for version, sql in MIGRATIONS:
        if version > current:
            conn.executescript(sql)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
    conn.commit()
