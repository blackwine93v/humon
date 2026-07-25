"""``files`` tool: jail enforcement, symlink escape, per-op permissions."""

from __future__ import annotations

import pytest

from conftest import make_logger
from humon.core.interfaces import ToolContext
from humon.tools.files import FilesTool


def ctx(jail: str, config: dict | None = None) -> ToolContext:
    async def _approve(_s: str) -> bool:
        return True

    return ToolContext(
        session_id="t",
        config=config or {},
        jail_paths=[jail],
        logger=make_logger(),
        request_approval=_approve,
    )


@pytest.mark.asyncio
async def test_write_then_read_inside_jail(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    tool = FilesTool()
    w = await tool.execute(
        {"operation": "write", "path": str(jail / "note.txt"), "content": "hello"}, ctx(str(jail))
    )
    assert w["ok"]
    r = await tool.execute({"operation": "read", "path": str(jail / "note.txt")}, ctx(str(jail)))
    assert r["ok"] and r["content"] == "hello"


@pytest.mark.asyncio
async def test_absolute_path_outside_jail_blocked(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    r = await FilesTool().execute({"operation": "read", "path": "/etc/passwd"}, ctx(str(jail)))
    assert not r["ok"]
    assert "jail" in (r["error"] or "")


@pytest.mark.asyncio
async def test_dotdot_traversal_blocked(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    r = await FilesTool().execute(
        {"operation": "read", "path": str(jail / ".." / "secret.txt")}, ctx(str(jail))
    )
    assert not r["ok"]


@pytest.mark.asyncio
async def test_symlink_escape_blocked(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    link = jail / "escape"
    link.symlink_to(secret)  # symlink INSIDE the jail pointing OUT
    r = await FilesTool().execute({"operation": "read", "path": str(link)}, ctx(str(jail)))
    assert not r["ok"], "symlink escape must be rejected, not followed"


@pytest.mark.asyncio
async def test_no_jail_configured_denies_all(tmp_path):
    async def _a(_s: str) -> bool:
        return True

    no_jail = ToolContext(
        session_id="t",
        config={},
        jail_paths=[],
        logger=make_logger(),
        request_approval=_a,
    )
    r = await FilesTool().execute({"operation": "read", "path": str(tmp_path / "x")}, no_jail)
    assert not r["ok"]


def test_permissions_are_per_operation():
    tool = FilesTool()
    assert tool.permissions_for({"operation": "read"}) == ["fs.read"]
    assert tool.permissions_for({"operation": "list"}) == ["fs.read"]
    assert tool.permissions_for({"operation": "write"}) == ["fs.write"]
    assert tool.permissions_for({"operation": "delete"}) == ["fs.delete"]


@pytest.mark.asyncio
async def test_delete_removes_file(tmp_path):
    jail = tmp_path / "jail"
    jail.mkdir()
    f = jail / "gone.txt"
    f.write_text("bye")
    r = await FilesTool().execute({"operation": "delete", "path": str(f)}, ctx(str(jail)))
    assert r["ok"] and not f.exists()
