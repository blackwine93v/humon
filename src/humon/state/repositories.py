"""Repositories — the only code that speaks SQL to the state DB.

Each repository maps one table (or a small cluster) to plain dicts/dataclasses.
Core managers use these; nothing else touches SQL.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from ..core.interfaces import Message, ToolCall
from .db import Database


def _now() -> float:
    return time.time()


class SessionRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, session_id: str) -> dict[str, Any] | None:
        return await self.db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))

    async def ensure(self, session_id: str, channel: str, user: str | None) -> dict[str, Any]:
        existing = await self.get(session_id)
        if existing:
            return existing
        now = _now()
        await self.db.execute(
            "INSERT INTO sessions(id, channel, user, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'idle', ?, ?)",
            (session_id, channel, user, now, now),
        )
        got = await self.get(session_id)
        assert got is not None
        return got

    async def set_status(self, session_id: str, status: str) -> None:
        await self.db.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), session_id),
        )

    async def set_summary(self, session_id: str, summary: str) -> None:
        await self.db.execute(
            "UPDATE sessions SET summary = ?, updated_at = ? WHERE id = ?",
            (summary, _now(), session_id),
        )

    async def set_plan(self, session_id: str, plan: dict[str, Any] | None) -> None:
        await self.db.execute(
            "UPDATE sessions SET plan = ?, updated_at = ? WHERE id = ?",
            (json.dumps(plan) if plan is not None else None, _now(), session_id),
        )

    async def list_active(self) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            "SELECT * FROM sessions WHERE status != 'idle' ORDER BY updated_at DESC"
        )

    # ── transcript ──────────────────────────────────────────────────────────
    async def add_message(self, session_id: str, msg: Message) -> None:
        await self.db.execute(
            "INSERT INTO messages(session_id, role, content, tool_calls, tool_call_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                msg.role,
                msg.content,
                json.dumps([tc.__dict__ for tc in msg.tool_calls]) if msg.tool_calls else None,
                msg.tool_call_id,
                _now(),
            ),
        )

    async def history(self, session_id: str, limit: int = 200) -> list[Message]:
        rows = await self.db.fetchall(
            "SELECT * FROM (SELECT * FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (session_id, limit),
        )
        out: list[Message] = []
        for r in rows:
            tcs = [ToolCall(**tc) for tc in json.loads(r["tool_calls"])] if r["tool_calls"] else []
            out.append(
                Message(
                    role=r["role"],
                    content=r["content"],
                    tool_calls=tcs,
                    tool_call_id=r["tool_call_id"],
                )
            )
        return out


@dataclass
class AuditEntry:
    tool: str
    decision: str
    session_id: str | None = None
    args: dict[str, Any] | None = None
    permission: str | None = None
    approver: str | None = None
    exit_status: str | None = None
    duration_ms: int | None = None
    output: str | None = None  # hashed, never stored raw


class AuditRepo:
    """Append-only audit log (FR-6.4). Output is hashed, not stored verbatim."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, entry: AuditEntry) -> None:
        output_hash = (
            hashlib.sha256(entry.output.encode("utf-8", "replace")).hexdigest()
            if entry.output is not None
            else None
        )
        await self.db.execute(
            "INSERT INTO audit_log(session_id, tool, args, permission, decision, approver, "
            "exit_status, duration_ms, output_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                entry.session_id,
                entry.tool,
                json.dumps(entry.args) if entry.args is not None else None,
                entry.permission,
                entry.decision,
                entry.approver,
                entry.exit_status,
                entry.duration_ms,
                output_hash,
                _now(),
            ),
        )

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self.db.fetchall("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

    async def export(self) -> list[dict[str, Any]]:
        return await self.db.fetchall("SELECT * FROM audit_log ORDER BY id ASC")


class MemoryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        text: str,
        kind: str = "note",
        session_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        return await self.db.execute(
            "INSERT INTO memory_notes(session_id, kind, text, meta, created_at) VALUES (?,?,?,?,?)",
            (session_id, kind, text, json.dumps(meta) if meta else None, _now()),
        )

    async def all(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            return await self.db.fetchall(
                "SELECT * FROM memory_notes WHERE kind = ? ORDER BY id DESC", (kind,)
            )
        return await self.db.fetchall("SELECT * FROM memory_notes ORDER BY id DESC")

    async def forget(self, note_id: int) -> bool:
        before = await self.db.fetchone("SELECT id FROM memory_notes WHERE id = ?", (note_id,))
        if not before:
            return False
        await self.db.execute("DELETE FROM memory_notes WHERE id = ?", (note_id,))
        return True


class TaskRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def add(
        self,
        description: str,
        schedule: str,
        next_run: float | None,
        session_id: str | None = None,
        scope: dict[str, Any] | None = None,
    ) -> int:
        return await self.db.execute(
            "INSERT INTO tasks(session_id, description, schedule, next_run, scope, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                session_id,
                description,
                schedule,
                next_run,
                json.dumps(scope) if scope else None,
                _now(),
            ),
        )

    async def list(self) -> list[dict[str, Any]]:
        return await self.db.fetchall("SELECT * FROM tasks ORDER BY id ASC")

    async def due(self, now: float) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            "SELECT * FROM tasks WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ? "
            "ORDER BY next_run ASC",
            (now,),
        )

    async def mark_run(self, task_id: int, next_run: float | None, status: str) -> None:
        await self.db.execute(
            "UPDATE tasks SET last_run = ?, last_status = ?, next_run = ?, "
            "enabled = CASE WHEN ? IS NULL THEN 0 ELSE enabled END WHERE id = ?",
            (_now(), status, next_run, next_run, task_id),
        )

    async def delete(self, task_id: int) -> bool:
        row = await self.db.fetchone("SELECT id FROM tasks WHERE id = ?", (task_id,))
        if not row:
            return False
        await self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return True
