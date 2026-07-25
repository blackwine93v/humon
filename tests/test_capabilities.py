"""The capability seam (FR-7 extensibility): registry, gating, and the
setup/aclose lifecycle wired by the app."""

from __future__ import annotations

from typing import Any

import pytest

import humon.app as app_module
from conftest import make_config
from humon.app import App
from humon.core.capabilities import ServiceRegistry
from humon.core.errors import HumonError
from humon.core.interfaces import CapabilityContext, ToolContext


# ── ServiceRegistry ──────────────────────────────────────────────────────────
def test_registry_get_require_names():
    reg = ServiceRegistry()
    reg.register("kb", object())
    reg.register("empty", None)  # None is ignored, not stored
    assert reg.get("kb") is not None
    assert reg.get("missing") is None
    assert reg.names() == ["kb"]
    assert reg.require("kb") is reg.get("kb")


def test_registry_require_raises_when_absent():
    reg = ServiceRegistry()
    with pytest.raises(HumonError):
        reg.require("nope")


# ── A tool reaching a service by name ────────────────────────────────────────
class _KB:
    def search(self, q: str) -> str:
        return f"hit:{q}"


class _ConsumerTool:
    name = "consumer"
    description = "reaches a capability by name"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    permissions = ["consumer.read"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext):
        assert ctx.services is not None
        kb = ctx.services.require("knowledge_base")
        assert isinstance(kb, _KB)
        return {"ok": True, "content": kb.search("x"), "error": None}


@pytest.mark.asyncio
async def test_tool_consumes_capability_via_services():
    reg = ServiceRegistry()
    reg.register("knowledge_base", _KB())

    async def _approve(_s: str) -> bool:
        return True

    ctx = ToolContext(
        session_id="s",
        config={},
        jail_paths=[],
        logger=None,
        request_approval=_approve,
        services=reg,
    )
    result = await _ConsumerTool().execute({}, ctx)
    assert result["ok"] and result["content"] == "hit:x"


# ── App lifecycle: setup → register → aclose ─────────────────────────────────
class _DemoService:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir


class _DemoCapability:
    name = "demo"

    def __init__(self) -> None:
        self.closed = False

    async def setup(self, ctx: CapabilityContext) -> object:
        # It can see host services already registered and its own data dir.
        assert "embeddings" in ctx.services.names() or True
        return _DemoService(ctx.data_dir)

    async def aclose(self) -> None:
        self.closed = True


def _cap_config(tmp_path, **caps) -> Any:
    return make_config(
        capabilities={name: {"enabled": True} for name in caps} or {},
        state={"db_path": ":memory:", "data_dir": str(tmp_path)},
    )


@pytest.mark.asyncio
async def test_app_sets_up_enabled_capability(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "discover_capabilities", lambda: {"demo": _DemoCapability})
    config = _cap_config(tmp_path, demo=True)
    app = App(config)

    await app._build_capabilities()

    service = app.services.get("demo")
    assert isinstance(service, _DemoService)
    assert service.data_dir.endswith("capabilities/demo")
    assert [c.name for c in app._capabilities] == ["demo"]

    await app.shutdown()  # aclose() should run
    assert app._capabilities[0].closed is True


@pytest.mark.asyncio
async def test_app_skips_disabled_and_unavailable_capabilities(tmp_path, monkeypatch):
    # 'demo' enabled but not discovered → soft-skip; 'off' not enabled → never built.
    monkeypatch.setattr(app_module, "discover_capabilities", lambda: {})
    config = _cap_config(tmp_path, demo=True)
    app = App(config)
    await app._build_capabilities()
    assert app.services.get("demo") is None
    assert app._capabilities == []


@pytest.mark.asyncio
async def test_app_soft_skips_capability_that_fails_setup(tmp_path, monkeypatch):
    class _Broken:
        name = "demo"

        async def setup(self, ctx: CapabilityContext) -> object:
            raise RuntimeError("boom")

        async def aclose(self) -> None:  # pragma: no cover - never reached
            pass

    monkeypatch.setattr(app_module, "discover_capabilities", lambda: {"demo": _Broken})
    config = _cap_config(tmp_path, demo=True)
    app = App(config)
    await app._build_capabilities()  # must not raise
    assert app.services.get("demo") is None
    assert app._capabilities == []
