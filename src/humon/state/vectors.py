"""Semantic vector index over SQLite via sqlite-vec (FR-5.2).

sqlite-vec is a tiny loadable extension — no external vector DB. If it (or
loadable-extension support) is unavailable, :attr:`VectorIndex.enabled` stays
False and the memory layer falls back to keyword search, so the feature degrades
gracefully instead of failing.
"""

from __future__ import annotations

import sqlite3

from .db import Database


class VectorIndex:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.enabled = False
        self._dim: int | None = None  # set lazily from the first embedding

    async def setup(self) -> None:
        def _init(conn: sqlite3.Connection) -> bool:
            try:
                import sqlite_vec
            except ImportError:
                return False
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
                return True
            except (AttributeError, sqlite3.OperationalError):
                # Python built without loadable-extension support, or load failed.
                return False

        self.enabled = await self.db.run(_init)

    async def _ensure_table(self, dim: int) -> None:
        if self._dim is not None:
            return

        def _create(conn: sqlite3.Connection) -> None:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_notes USING vec0(embedding float[{dim}])"
            )
            conn.commit()

        await self.db.run(_create)
        self._dim = dim

    async def add(self, note_id: int, embedding: list[float]) -> None:
        if not self.enabled:
            return
        await self._ensure_table(len(embedding))
        import sqlite_vec

        payload = sqlite_vec.serialize_float32(embedding)

        def _add(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT OR REPLACE INTO vec_notes(rowid, embedding) VALUES (?, ?)",
                (note_id, payload),
            )
            conn.commit()

        await self.db.run(_add)

    async def search(self, embedding: list[float], k: int) -> list[tuple[int, float]]:
        if not self.enabled or self._dim is None:
            return []
        import sqlite_vec

        payload = sqlite_vec.serialize_float32(embedding)

        def _search(conn: sqlite3.Connection) -> list[tuple[int, float]]:
            rows = conn.execute(
                "SELECT rowid, distance FROM vec_notes "
                "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (payload, k),
            ).fetchall()
            return [(int(r[0]), float(r[1])) for r in rows]

        return await self.db.run(_search)
