"""Memory manager + memory tool (FR-5, FR-4.6)."""

from __future__ import annotations

import pytest

from conftest import build_memory, make_logger
from humon.core.interfaces import ToolContext
from humon.providers.fake import FakeProvider
from humon.tools.memory import MemoryTool


def mem_ctx(memory) -> ToolContext:
    async def _a(_s: str) -> bool:
        return True

    return ToolContext(
        session_id="t",
        config={},
        jail_paths=[],
        logger=make_logger(),
        request_approval=_a,
        memory=memory,
    )


@pytest.mark.asyncio
async def test_store_and_search_roundtrip(db):
    memory = await build_memory(db, FakeProvider())
    await memory.store("The NAS lives at 192.168.1.9", kind="note")
    await memory.store("Coffee order is a flat white", kind="note")
    hits = await memory.search("where is the NAS", k=3)
    assert any("192.168.1.9" in h for h in hits)


@pytest.mark.asyncio
async def test_keyword_fallback_without_embeddings(db):
    # A provider that declares no embeddings capability → keyword search path.
    class NoEmbed(FakeProvider):
        capabilities = {"tools"}

    memory = await build_memory(db, NoEmbed())
    await memory.store("backup runs at 3am nightly")
    hits = await memory.search("backup", k=3)
    assert any("backup" in h for h in hits)


@pytest.mark.asyncio
async def test_memory_tool_remember_recall_forget(db):
    memory = await build_memory(db, FakeProvider())
    tool = MemoryTool()
    ctx = mem_ctx(memory)

    r = await tool.execute({"action": "remember", "text": "router password is in vault"}, ctx)
    assert r["ok"]

    r = await tool.execute({"action": "recall", "text": "router"}, ctx)
    assert "router" in r["content"]

    listed = await tool.execute({"action": "list"}, ctx)
    assert "router" in listed["content"]

    forget = await tool.execute({"action": "forget", "id": 1}, ctx)
    assert forget["ok"]


def test_memory_tool_permissions_per_action():
    tool = MemoryTool()
    assert tool.permissions_for({"action": "remember"}) == ["memory.write"]
    assert tool.permissions_for({"action": "forget"}) == ["memory.write"]
    assert tool.permissions_for({"action": "recall"}) == ["memory.read"]
    assert tool.permissions_for({"action": "list"}) == ["memory.read"]


@pytest.mark.asyncio
async def test_memory_tool_without_memory_available(db):
    async def _a(_s: str) -> bool:
        return True

    ctx = ToolContext(
        session_id="t",
        config={},
        jail_paths=[],
        logger=make_logger(),
        request_approval=_a,
        memory=None,
    )
    r = await MemoryTool().execute({"action": "list"}, ctx)
    assert not r["ok"]
