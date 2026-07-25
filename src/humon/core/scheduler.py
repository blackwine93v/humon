"""In-process async scheduler (FR-4.4).

Owns the SQLite-backed task queue and implements the ``TaskStore`` surface the
``schedule`` tool calls. A single poll loop finds due tasks and hands each to an
``on_due`` callback (the app runs it through the same agent + policy engine).

Because tasks live in SQLite, they survive a restart: on startup the scheduler
simply reads the queue and resumes — that is the M4 "works across a service
restart" property (NFR-3).

Schedule spec grammar (small on purpose):
- ``once``            → run at the next tick, then disable
- ``once:<iso8601>``  → run at that time, then disable
- ``every:<seconds>`` → recurring, every N seconds
- ``daily@HH:MM``     → recurring, once a day at local HH:MM
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from ..state.repositories import TaskRepo

OnDue = Callable[[dict[str, Any]], Awaitable[None]]


class Scheduler:
    def __init__(
        self,
        task_repo: TaskRepo,
        on_due: OnDue,
        logger: Any,
        poll_interval_s: float = 30.0,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.tasks = task_repo
        self.on_due = on_due
        self.logger = logger
        self.poll_interval_s = poll_interval_s
        self._now = now_fn
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None

    # ── TaskStore surface (used by the schedule tool) ─────────────────────────
    async def add_task(
        self, description: str, schedule: str, session_id: str | None = None
    ) -> tuple[int, float | None]:
        next_run = compute_next_run(schedule, self._now())
        task_id = await self.tasks.add(
            description, schedule, next_run, session_id=session_id, scope={"preapproved": True}
        )
        return task_id, next_run

    async def list_tasks(self) -> list[tuple[int, str, str, float | None]]:
        rows = await self.tasks.list()
        return [(r["id"], r["description"], r["schedule"], r["next_run"]) for r in rows]

    async def delete_task(self, task_id: int) -> bool:
        return await self.tasks.delete(task_id)

    # ── run loop ──────────────────────────────────────────────────────────────
    async def tick(self, now: float | None = None) -> int:
        """Run all tasks due at ``now``; return how many ran."""

        now = now if now is not None else self._now()
        due: list[dict[str, Any]] = await self.tasks.due(now)
        for row in due:
            status = "ok"
            try:
                await self.on_due(row)
            except Exception:  # a failing task must not kill the scheduler
                status = "error"
                self.logger.exception("scheduler.task_error", task_id=row["id"])
            next_run = compute_next_run(row["schedule"], now, after=True)
            await self.tasks.mark_run(row["id"], next_run, status)
        return len(due)

    async def start(self) -> None:
        self._loop_task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:  # never let the poll loop die
                self.logger.exception("scheduler.tick_error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._loop_task is not None:
            await self._loop_task


def compute_next_run(spec: str, now: float, after: bool = False) -> float | None:
    """Compute the next run epoch for a schedule spec, or None to disable."""

    spec = spec.strip()
    if spec == "once":
        return None if after else now
    if spec.startswith("once:"):
        if after:
            return None
        try:
            return datetime.fromisoformat(spec[len("once:") :]).timestamp()
        except ValueError:
            return now
    if spec.startswith("every:"):
        try:
            seconds = float(spec[len("every:") :])
        except ValueError:
            return None
        return now + seconds
    if spec.startswith("daily@"):
        try:
            hh, mm = spec[len("daily@") :].split(":")
            hour, minute = int(hh), int(mm)
        except (ValueError, IndexError):
            return None
        base = datetime.fromtimestamp(now)
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.timestamp() <= now:
            candidate = candidate + timedelta(days=1)
        return candidate.timestamp()
    return None
