"""Scheduler (FR-4.4): spec parsing, tick execution, and restart survival."""

from __future__ import annotations

import logging
from datetime import datetime

import pytest

from humon.core.scheduler import Scheduler, compute_next_run
from humon.state.repositories import TaskRepo


def test_every_spec():
    nxt = compute_next_run("every:60", now=1000.0)
    assert nxt == 1060.0


def test_once_spec_disables_after():
    assert compute_next_run("once", now=1000.0) == 1000.0
    assert compute_next_run("once", now=1000.0, after=True) is None


def test_daily_spec_next_occurrence():
    now = datetime(2026, 1, 1, 9, 0, 0).timestamp()  # 9am
    nxt = compute_next_run("daily@08:00", now=now)  # 8am already passed today
    assert nxt is not None
    got = datetime.fromtimestamp(nxt)
    assert (got.hour, got.minute) == (8, 0)
    assert got.day == 2  # tomorrow


def test_invalid_spec_disables():
    assert compute_next_run("nonsense", now=1000.0) is None


@pytest.mark.asyncio
async def test_add_list_delete(db):
    ran: list[dict] = []

    async def on_due(row):
        ran.append(row)

    sched = Scheduler(TaskRepo(db), on_due, logging.getLogger("t"), now_fn=lambda: 1000.0)
    task_id, next_run = await sched.add_task("ping the NAS", "every:60", session_id="s1")
    assert next_run == 1060.0

    listed = await sched.list_tasks()
    assert any(t[0] == task_id and "NAS" in t[1] for t in listed)

    assert await sched.delete_task(task_id)
    assert await sched.list_tasks() == []


@pytest.mark.asyncio
async def test_tick_runs_due_task_and_reschedules(db):
    ran: list[dict] = []

    async def on_due(row):
        ran.append(row)

    sched = Scheduler(TaskRepo(db), on_due, logging.getLogger("t"), now_fn=lambda: 1000.0)
    await sched.add_task("check NAS", "every:60", session_id="s1")

    # Not due yet at t=1000 (next_run=1060).
    assert await sched.tick(now=1000.0) == 0
    # Due at t=1100.
    assert await sched.tick(now=1100.0) == 1
    assert ran and ran[0]["description"] == "check NAS"


@pytest.mark.asyncio
async def test_task_survives_restart(db):
    """A task created before a 'restart' is picked up by a fresh Scheduler."""

    async def noop(_row):
        return None

    sched1 = Scheduler(TaskRepo(db), noop, logging.getLogger("t"), now_fn=lambda: 1000.0)
    await sched1.add_task("morning NAS check", "every:30", session_id="s1")

    # Simulate a process restart: brand-new Scheduler over the SAME database.
    ran: list[dict] = []

    async def on_due(row):
        ran.append(row)

    sched2 = Scheduler(TaskRepo(db), on_due, logging.getLogger("t"), now_fn=lambda: 2000.0)
    assert await sched2.tick(now=2000.0) == 1
    assert ran[0]["description"] == "morning NAS check"
