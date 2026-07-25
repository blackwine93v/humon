"""M3 exit criterion: a multi-step task shows a plan and executes; a fact stored
in one session is recalled in a later session.
"""

from __future__ import annotations

import pytest

from conftest import build_memory, make_config, make_logger
from humon.core.agent import Agent
from humon.core.planner import LLMPlanner
from humon.core.policy import PolicyEngine
from humon.providers.fake import FakeProvider, text_response, tool_response
from humon.state.repositories import AuditRepo, SessionRepo
from humon.tools.memory import MemoryTool


@pytest.mark.asyncio
async def test_plan_shown_execute_and_cross_session_recall(db):
    config = make_config(
        tools={"memory": {"enabled": True}},
        policy={
            "default_decision": "deny",
            "approval": {"timeout_s": 1},
            "rules": {"memory.read": "allow", "memory.write": "allow"},
        },
    )
    provider = FakeProvider(
        [
            # Session 1: planner returns a 3-step plan…
            text_response("1. Note the fact\n2. Confirm\n3. Reply"),
            # …then the loop stores a fact and answers.
            tool_response("memory", {"action": "remember", "text": "The NAS is at 192.168.1.50"}),
            text_response("Saved that the NAS is at 192.168.1.50."),
            # Session 2: single-step plan, then an answer.
            text_response("Answer directly"),
            text_response("The NAS is at 192.168.1.50."),
        ]
    )
    memory = await build_memory(db, provider)
    planner = LLMPlanner(provider, config.models.strong_or_default())
    agent = Agent(
        provider=provider,
        tools={"memory": MemoryTool()},
        policy=PolicyEngine(config.policy),
        config=config,
        session_repo=SessionRepo(db),
        audit=AuditRepo(db),
        logger=make_logger(),
        planner=planner,
        memory=memory,
    )

    # ── Session 1 ────────────────────────────────────────────────────────────
    notes: list[str] = []

    async def progress(n: str) -> None:
        notes.append(n)

    async def approve(_s: str) -> bool:
        return True

    await SessionRepo(db).ensure("sess-1", "fake", "u1")
    out1 = await agent.run_task(
        session_id="sess-1",
        user_text="remember the NAS address and confirm",
        request_approval=approve,
        progress=progress,
    )
    assert out1.text
    # The multi-step plan was surfaced to the user.
    assert any("Plan:" in n for n in notes), notes
    assert out1.tools_used == ["memory"]

    # ── Session 2 (fresh session) ────────────────────────────────────────────
    recalled = await memory.search("what is the NAS address", k=5)
    assert any("192.168.1.50" in h for h in recalled), recalled

    await SessionRepo(db).ensure("sess-2", "fake", "u1")
    out2 = await agent.run_task(
        session_id="sess-2",
        user_text="what is the NAS address?",
        request_approval=approve,
    )
    assert out2.text
