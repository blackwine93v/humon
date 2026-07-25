"""Full agent-loop integration against FakeProvider + real tools (T-2).

This is the M1 "walking agent" exit scenario as an automated test, plus the
policy allow/deny/approval paths the loop must honour.
"""

from __future__ import annotations

import logging

import pytest

from conftest import make_config
from humon.core.agent import Agent
from humon.core.policy import PolicyEngine
from humon.providers.fake import FakeProvider, text_response, tool_response
from humon.state.repositories import AuditRepo, SessionRepo
from humon.tools.shell import ShellTool


def build_agent(db, config, provider):
    return Agent(
        provider=provider,
        tools={"shell": ShellTool()},
        policy=PolicyEngine(config.policy),
        config=config,
        session_repo=SessionRepo(db),
        audit=AuditRepo(db),
        logger=logging.getLogger("test"),
    )


async def approve_yes(_summary: str) -> bool:
    return True


async def approve_no(_summary: str) -> bool:
    return False


@pytest.mark.asyncio
async def test_walking_agent_runs_shell_then_answers(db):
    config = make_config()
    provider = FakeProvider(
        [
            tool_response("shell", {"command": "echo 42"}),
            text_response("You have 42 units free."),
        ]
    )
    agent = build_agent(db, config, provider)
    await SessionRepo(db).ensure("s1", "fake", "u1")

    outcome = await agent.run_task(
        session_id="s1", user_text="how much is free?", request_approval=approve_yes
    )

    assert "42 units free" in outcome.text
    assert outcome.tool_calls == 1
    assert outcome.tools_used == ["shell"]

    rows = await AuditRepo(db).recent()
    assert any(r["tool"] == "shell" and r["decision"] == "allow" for r in rows)
    # Output is hashed, never stored raw.
    shell_row = next(r for r in rows if r["tool"] == "shell")
    assert shell_row["output_hash"] and len(shell_row["output_hash"]) == 64


@pytest.mark.asyncio
async def test_denied_tool_is_blocked_and_audited(db):
    config = make_config(
        policy={
            "default_decision": "deny",
            "approval": {"timeout_s": 1},
            "rules": {"shell.exec": "deny"},
        }
    )
    provider = FakeProvider(
        [
            tool_response("shell", {"command": "echo 42"}),
            text_response("I could not run that."),
        ]
    )
    agent = build_agent(db, config, provider)
    await SessionRepo(db).ensure("s2", "fake", "u1")

    outcome = await agent.run_task(
        session_id="s2", user_text="run it", request_approval=approve_yes
    )
    assert "could not" in outcome.text.lower()
    rows = await AuditRepo(db).recent()
    assert any(r["tool"] == "shell" and r["decision"] == "deny" for r in rows)


@pytest.mark.asyncio
async def test_approval_required_and_denied(db):
    config = make_config(
        policy={
            "default_decision": "deny",
            "approval": {"timeout_s": 1},
            "rules": {"shell.exec": "require_approval"},
        }
    )
    provider = FakeProvider(
        [
            tool_response("shell", {"command": "echo 42"}),
            text_response("Understood, not running it."),
        ]
    )
    agent = build_agent(db, config, provider)
    await SessionRepo(db).ensure("s3", "fake", "u1")

    prompts: list[str] = []

    async def record_and_deny(summary: str) -> bool:
        prompts.append(summary)
        return False

    outcome = await agent.run_task(
        session_id="s3", user_text="run it", request_approval=record_and_deny
    )
    assert prompts, "operator should have been asked for approval"
    rows = await AuditRepo(db).recent()
    assert any(r["tool"] == "shell" and r["decision"] == "require_approval" for r in rows)
    assert outcome.text


@pytest.mark.asyncio
async def test_unknown_tool_is_handled(db):
    config = make_config()
    provider = FakeProvider(
        [
            tool_response("nonexistent", {}),
            text_response("Sorry, I lack that capability."),
        ]
    )
    agent = build_agent(db, config, provider)
    await SessionRepo(db).ensure("s4", "fake", "u1")
    outcome = await agent.run_task(
        session_id="s4", user_text="do magic", request_approval=approve_yes
    )
    assert outcome.text
