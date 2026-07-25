"""Async SQLite wrapper over stdlib ``sqlite3``.

Core stays dependency-free: rather than pull in ``aiosqlite``, we run blocking
sqlite calls in a thread via :func:`asyncio.to_thread`, serialized behind a
lock so the single connection is never touched concurrently. WAL mode keeps
reads non-blocking and gives us crash-safe durability (NFR-3, NFR-6).
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .migrations import apply_migrations


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected; call connect() first")
        return self._conn

    async def connect(self) -> None:
        def _open() -> sqlite3.Connection:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            return conn

        self._conn = await asyncio.to_thread(_open)
        await self.migrate()

    async def migrate(self) -> None:
        async with self._lock:
            await asyncio.to_thread(apply_migrations, self.conn)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Run a write statement, commit, return ``lastrowid``."""

        async with self._lock:

            def _run() -> int:
                cur = self.conn.execute(sql, params)
                self.conn.commit()
                return int(cur.lastrowid or 0)

            return await asyncio.to_thread(_run)

    async def executemany(self, sql: str, seq: list[tuple[Any, ...]]) -> None:
        async with self._lock:

            def _run() -> None:
                self.conn.executemany(sql, seq)
                self.conn.commit()

            await asyncio.to_thread(_run)

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        async with self._lock:

            def _run() -> dict[str, Any] | None:
                row = self.conn.execute(sql, params).fetchone()
                return dict(row) if row is not None else None

            return await asyncio.to_thread(_run)

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with self._lock:

            def _run() -> list[dict[str, Any]]:
                rows = self.conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]

            return await asyncio.to_thread(_run)

    async def run(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """Run an arbitrary callable against the connection, under the lock.

        Used for operations stdlib helpers don't cover (e.g. loading the
        sqlite-vec extension and MATCH queries).
        """

        async with self._lock:
            return await asyncio.to_thread(fn, self.conn)

    async def close(self) -> None:
        if self._conn is not None:
            conn = self._conn
            self._conn = None
            await asyncio.to_thread(conn.close)
