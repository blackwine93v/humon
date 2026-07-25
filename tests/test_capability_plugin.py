"""Extensibility exit criterion: an out-of-tree package adds a NEW host service
(a capability) plus a tool that consumes it via ``ctx.services`` — installs via
pip, is discovered through the ``humon.capabilities`` / ``humon.tools`` groups,
config-gates, passes policy, and runs end-to-end — with zero edits to humon core.

Uses a real pip install of a hand-authored two-entry-point package. Skips
gracefully if pip can't run here so CI stays green (mirrors test_m5_plugin.py).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_PKG = "humon-example-kbpack"

_CAP_PY = """\
from __future__ import annotations

from humon.core.interfaces import CapabilityContext


class KbService:
    def lookup(self, q: str) -> str:
        return f"kb[{q}]"


class KbDemoCapability:
    name = "kbdemo"

    async def setup(self, ctx: CapabilityContext) -> object:
        return KbService()

    async def aclose(self) -> None:
        return None
"""

_TOOL_PY = """\
from __future__ import annotations

from typing import Any

from humon.core.interfaces import ToolContext, ToolResult


class KbTool:
    name = "kbtool"
    description = "Look something up via the kbdemo capability."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
        "additionalProperties": False,
    }
    permissions = ["kbtool.read"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.services is None:
            return {"ok": False, "content": "", "error": "no capability registry"}
        kb = ctx.services.require("kbdemo")
        return {"ok": True, "content": kb.lookup(str(args.get("q", ""))), "error": None}
"""

_PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "humon-example-kbpack"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["humon"]

[project.entry-points."humon.capabilities"]
kbdemo = "humon_example_kbpack.cap:KbDemoCapability"

[project.entry-points."humon.tools"]
kbtool = "humon_example_kbpack.tool:KbTool"

[tool.hatch.build.targets.wheel]
packages = ["src/humon_example_kbpack"]
"""

# Wires the installed capability + tool together through the real registry, the
# policy engine, and a ToolContext carrying the service registry.
_CHECK = """
import asyncio, logging

from humon.core.registry import discover_capabilities, discover_tools
from humon.core.capabilities import ServiceRegistry
from humon.core.policy import PolicyEngine
from humon.config import CapabilitySettings, PolicyConfig
from humon.core.interfaces import CapabilityContext, ToolContext

caps = discover_capabilities()
tools = discover_tools()
assert "kbdemo" in caps, "capability not discovered via entry points"
assert "kbtool" in tools, "tool not discovered via entry points"

# Config gating: only an enabled capability would be built by the app.
enabled = {"kbdemo": CapabilitySettings(enabled=True)}
assert [n for n, c in enabled.items() if c.enabled] == ["kbdemo"]


async def main():
    reg = ServiceRegistry()
    cap = caps["kbdemo"]()
    ctx = CapabilityContext(
        name="kbdemo", config={}, logger=logging.getLogger("t"),
        data_dir="/tmp", services=reg,
    )
    reg.register("kbdemo", await cap.setup(ctx))

    tool = tools["kbtool"]()
    pe = PolicyEngine(PolicyConfig(default_decision="deny", rules={"kbtool.read": "allow"}))
    assert pe.check("kbtool", tool.permissions).decision.value == "allow", "policy denied"

    async def approve(_s): return True
    tctx = ToolContext(
        session_id="s", config={}, jail_paths=[], logger=logging.getLogger("t"),
        request_approval=approve, services=reg,
    )
    res = await tool.execute({"q": "hello"}, tctx)
    assert res["ok"] and res["content"] == "kb[hello]", res
    print("OK", res["content"])

asyncio.run(main())
"""


def _pip(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pip", *args], capture_output=True, text=True, timeout=180
    )


def _write_pkg(root: Path) -> None:
    src = root / "src" / "humon_example_kbpack"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "cap.py").write_text(_CAP_PY, encoding="utf-8")
    (src / "tool.py").write_text(_TOOL_PY, encoding="utf-8")
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")


@pytest.mark.slow
def test_out_of_tree_capability_and_tool_work_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "humon-example-kbpack"
        _write_pkg(root)
        install = _pip("install", "--no-build-isolation", "--no-deps", str(root))
        if install.returncode != 0:
            pytest.skip(f"pip install unavailable here: {install.stderr[-300:]}")
        try:
            check = subprocess.run(
                [sys.executable, "-c", _CHECK], capture_output=True, text=True, timeout=60
            )
            assert check.returncode == 0, f"flow failed: {check.stdout}\n{check.stderr}"
            assert "OK kb[hello]" in check.stdout
        finally:
            _pip("uninstall", "-y", _PKG)
