"""``shell`` tool (FR-4.1): allowlist, metachar rejection, truncation, timeout."""

from __future__ import annotations

import logging

import pytest

from humon.core.interfaces import ToolContext
from humon.tools.shell import ShellTool


def ctx(config: dict) -> ToolContext:
    async def _approve(_summary: str) -> bool:
        return True

    return ToolContext(
        session_id="t",
        config=config,
        jail_paths=[],
        logger=logging.getLogger("test"),
        request_approval=_approve,
    )


BASE = {"allowed_binaries": ["echo", "true", "false"], "allow_shell_metachars": False}


@pytest.mark.asyncio
async def test_allowlisted_binary_runs():
    r = await ShellTool().execute({"command": "echo hello"}, ctx(BASE))
    assert r["ok"]
    assert "hello" in r["content"]


@pytest.mark.asyncio
async def test_non_allowlisted_binary_blocked():
    r = await ShellTool().execute({"command": "rm -rf /"}, ctx(BASE))
    assert not r["ok"]
    assert "allowlist" in (r["error"] or "")


@pytest.mark.asyncio
async def test_metacharacters_rejected():
    r = await ShellTool().execute({"command": "echo hi; rm x"}, ctx(BASE))
    assert not r["ok"]
    assert "metacharacter" in (r["error"] or "")


@pytest.mark.asyncio
async def test_pipe_injection_rejected():
    r = await ShellTool().execute({"command": "echo hi | tee /etc/x"}, ctx(BASE))
    assert not r["ok"]


@pytest.mark.asyncio
async def test_nonzero_exit_reported():
    r = await ShellTool().execute({"command": "false"}, ctx(BASE))
    assert r["ok"]
    assert "exit code" in r["content"]


@pytest.mark.asyncio
async def test_output_truncation():
    cfg = {"allowed_binaries": ["echo"], "max_output_bytes": 5}
    r = await ShellTool().execute({"command": "echo abcdefghij"}, ctx(cfg))
    assert "truncated" in r["content"]


@pytest.mark.asyncio
async def test_empty_command():
    r = await ShellTool().execute({"command": "  "}, ctx(BASE))
    assert not r["ok"]
