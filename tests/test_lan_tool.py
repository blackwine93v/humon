"""``lan`` tool (FR-4.3): private-CIDR enforcement + tcp/ping/http_get."""

from __future__ import annotations

import asyncio
import logging

import pytest

from humon.core.interfaces import ToolContext
from humon.tools.lan import LanTool


def ctx(cidrs: list[str]) -> ToolContext:
    async def _a(_s: str) -> bool:
        return True

    return ToolContext(
        session_id="t",
        config={"allowed_cidrs": cidrs, "timeout_s": 2},
        jail_paths=[],
        logger=logging.getLogger("t"),
        request_approval=_a,
    )


@pytest.mark.asyncio
async def test_public_ip_rejected():
    r = await LanTool().execute(
        {"action": "tcp", "host": "8.8.8.8", "port": 53},
        ctx(["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]),
    )
    assert not r["ok"]
    assert "outside the allowed" in (r["error"] or "")


@pytest.mark.asyncio
async def test_http_get_public_rejected():
    r = await LanTool().execute(
        {"action": "http_get", "url": "http://8.8.8.8/"}, ctx(["192.168.0.0/16"])
    )
    assert not r["ok"]


@pytest.mark.asyncio
async def test_tcp_open_and_closed_on_loopback():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        r_open = await LanTool().execute(
            {"action": "tcp", "host": "127.0.0.1", "port": port}, ctx(["127.0.0.0/8"])
        )
        assert r_open["ok"] and "OPEN" in r_open["content"]
    finally:
        server.close()
        await server.wait_closed()

    r_closed = await LanTool().execute(
        {"action": "tcp", "host": "127.0.0.1", "port": port}, ctx(["127.0.0.0/8"])
    )
    assert "CLOSED" in r_closed["content"]


@pytest.mark.asyncio
async def test_tcp_requires_port():
    r = await LanTool().execute({"action": "tcp", "host": "127.0.0.1"}, ctx(["127.0.0.0/8"]))
    assert not r["ok"]
