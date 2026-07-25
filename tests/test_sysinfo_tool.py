"""``sysinfo`` tool — read-only host inspection works without extra deps."""

from __future__ import annotations

import pytest

from conftest import make_logger
from humon.core.interfaces import ToolContext
from humon.tools.sysinfo import SysinfoTool


def ctx() -> ToolContext:
    async def _a(_s: str) -> bool:
        return True

    return ToolContext(
        session_id="t",
        config={"journal_max_lines": 5},
        jail_paths=[],
        logger=make_logger(),
        request_approval=_a,
    )


@pytest.mark.asyncio
async def test_overview_reports_cpu_mem_disk():
    r = await SysinfoTool().execute({"action": "overview"}, ctx())
    assert r["ok"]
    assert "CPU" in r["content"]
    assert "Memory" in r["content"]
    assert "Disk" in r["content"]


@pytest.mark.asyncio
async def test_disk_action():
    r = await SysinfoTool().execute({"action": "disk"}, ctx())
    assert r["ok"] and "GB" in r["content"]


@pytest.mark.asyncio
async def test_unknown_action():
    r = await SysinfoTool().execute({"action": "bogus"}, ctx())
    assert not r["ok"]


@pytest.mark.asyncio
async def test_service_requires_unit():
    r = await SysinfoTool().execute({"action": "service"}, ctx())
    assert not r["ok"]
