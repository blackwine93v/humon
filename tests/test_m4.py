"""M4 exit criterion: "check every morning at 8 whether the NAS responds and DM me
if not" — the scheduled task is created via the tool, persists across a restart,
then executes and delivers a message.
"""

from __future__ import annotations

import pytest

from conftest import make_config, make_logger
from humon.core.agent import Agent
from humon.core.policy import PolicyEngine
from humon.core.scheduler import Scheduler
from humon.providers.fake import FakeProvider, text_response, tool_response
from humon.state.repositories import AuditRepo, SessionRepo, TaskRepo
from humon.tools.schedule import ScheduleTool


async def approve_yes(_s: str) -> bool:
    return True


@pytest.mark.asyncio
async def test_scheduled_nas_check_survives_restart_and_runs(db):
    config = make_config(
        tools={"schedule": {"enabled": True}},
        policy={
            "default_decision": "deny",
            "approval": {"timeout_s": 1},
            "rules": {"schedule.read": "allow", "schedule.write": "allow"},
        },
    )

    # ── Before restart: user asks to schedule a recurring NAS check ───────────
    async def noop(_row):
        return None

    scheduler = Scheduler(TaskRepo(db), noop, make_logger(), now_fn=lambda: 1000.0)
    create_provider = FakeProvider(
        [
            tool_response(
                "schedule",
                {
                    "action": "create",
                    "description": "ping the NAS and report if it is down",
                    "schedule": "daily@08:00",
                },
            ),
            text_response("Scheduled a daily NAS check at 08:00."),
        ]
    )
    agent = Agent(
        provider=create_provider,
        tools={"schedule": ScheduleTool()},
        policy=PolicyEngine(config.policy),
        config=config,
        session_repo=SessionRepo(db),
        audit=AuditRepo(db),
        logger=make_logger(),
        tasks=scheduler,
    )
    await SessionRepo(db).ensure("s1", "fake", "u1")
    out = await agent.run_task(
        session_id="s1",
        user_text="every morning at 8, check whether the NAS responds and message me",
        request_approval=approve_yes,
    )
    assert out.tools_used == ["schedule"]

    tasks = await TaskRepo(db).list()
    assert len(tasks) == 1
    next_run = tasks[0]["next_run"]
    assert next_run is not None

    # ── Restart: fresh Scheduler + agent over the SAME database ───────────────
    delivered: list[str] = []
    run_provider = FakeProvider([text_response("The NAS is DOWN — no response from 192.168.1.9.")])
    run_agent = Agent(
        provider=run_provider,
        tools={},
        policy=PolicyEngine(config.policy),
        config=config,
        session_repo=SessionRepo(db),
        audit=AuditRepo(db),
        logger=make_logger(),
    )

    async def on_due(row):
        outcome = await run_agent.run_task(
            session_id=row["session_id"],
            user_text=row["description"],
            request_approval=approve_yes,
        )
        delivered.append(outcome.text)

    sched2 = Scheduler(TaskRepo(db), on_due, make_logger(), now_fn=lambda: next_run + 1)
    ran = await sched2.tick(now=next_run + 1)

    assert ran == 1
    assert any("DOWN" in d for d in delivered)

    # And it rescheduled for the next day (still recurring).
    tasks_after = await TaskRepo(db).list()
    assert tasks_after[0]["next_run"] is not None and tasks_after[0]["next_run"] > next_run
