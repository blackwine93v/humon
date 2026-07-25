"""Session manager (FR-3.7).

Enforces two invariants on the reference hardware:
- at most ``max_concurrent_sessions`` tasks run at once (a semaphore), and
- at most one in-flight task **per** session (a second message in a busy thread
  is rejected, not queued behind the first).

Also exposes cancellation so ``!cancel`` can stop a runaway task.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from ..state.repositories import SessionRepo

T = TypeVar("T")


class SessionBusy(Exception):
    """Raised when a session already has an in-flight task."""


class SessionManager:
    def __init__(self, repo: SessionRepo, max_concurrent: int) -> None:
        self.repo = repo
        self._sem = asyncio.Semaphore(max_concurrent)
        self._running: dict[str, asyncio.Task[object]] = {}

    def is_running(self, session_id: str) -> bool:
        return session_id in self._running

    def active_ids(self) -> list[str]:
        return list(self._running.keys())

    def cancel(self, session_id: str) -> bool:
        task = self._running.get(session_id)
        if task is None:
            return False
        task.cancel()
        return True

    async def run(self, session_id: str, factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """Run ``factory()`` as this session's single in-flight task."""

        if session_id in self._running:
            raise SessionBusy(session_id)
        async with self._sem:
            task: asyncio.Task[T] = asyncio.create_task(factory())
            self._running[session_id] = task  # type: ignore[assignment]
            try:
                return await task
            finally:
                self._running.pop(session_id, None)
