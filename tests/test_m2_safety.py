"""M2 exit criterion: a file write triggers approval; deny blocks it; the audit
log shows both the approved and the denied attempt.
"""

from __future__ import annotations

import pytest

from conftest import make_config, make_logger
from humon.core.agent import Agent
from humon.core.policy import PolicyEngine
from humon.providers.fake import FakeProvider, text_response, tool_response
from humon.state.repositories import AuditRepo, SessionRepo
from humon.tools.files import FilesTool


def agent_for(db, jail):
    config = make_config(
        tools={"files": {"enabled": True, "jail_paths": [str(jail)]}},
        policy={
            "default_decision": "deny",
            "approval": {"timeout_s": 1},
            "rules": {"fs.read": "allow", "fs.write": "require_approval"},
        },
    )
    return Agent(
        provider=None,  # set per-test
        tools={"files": FilesTool()},
        policy=PolicyEngine(config.policy),
        config=config,
        session_repo=SessionRepo(db),
        audit=AuditRepo(db),
        logger=make_logger(),
    )


@pytest.mark.asyncio
async def test_write_approved_then_denied(db, tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    target = jail / "report.txt"

    agent = agent_for(db, jail)

    # Run 1 — approve the write.
    agent.provider = FakeProvider(
        [
            tool_response("files", {"operation": "write", "path": str(target), "content": "v1"}),
            text_response("Saved."),
        ]
    )
    await SessionRepo(db).ensure("s1", "fake", "u")

    async def approve(_s: str) -> bool:
        return True

    out1 = await agent.run_task(session_id="s1", user_text="save v1", request_approval=approve)
    assert out1.text
    assert target.read_text() == "v1"

    # Run 2 — deny the write.
    agent.provider = FakeProvider(
        [
            tool_response("files", {"operation": "write", "path": str(target), "content": "v2"}),
            text_response("Understood, leaving it."),
        ]
    )
    await SessionRepo(db).ensure("s2", "fake", "u")
    prompts: list[str] = []

    async def deny(summary: str) -> bool:
        prompts.append(summary)
        return False

    out2 = await agent.run_task(session_id="s2", user_text="save v2", request_approval=deny)
    assert out2.text
    assert prompts, "operator should have been prompted"
    assert target.read_text() == "v1", "denied write must not change the file"

    # Audit shows BOTH: an approved write (ok) and a denied write.
    rows = await AuditRepo(db).recent()
    writes = [r for r in rows if r["tool"] == "files"]
    assert any(r["approver"] == "human" and r["exit_status"] == "ok" for r in writes)
    assert any(r["exit_status"] == "denied" for r in writes)
