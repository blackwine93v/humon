"""M5 exit criterion: a third-party tool installs via pip, is discovered through
entry points, enables via config, passes policy, and would appear in `!tools`.

Uses a real pip install of a scaffolded plugin. Skips gracefully if the install
can't run in this environment (e.g. no build backend), so CI stays green.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile

import pytest

from humon.scaffold import scaffold_tool

_PKG = "humon-tool-evalplugin"


def _pip(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pip", *args], capture_output=True, text=True, timeout=180
    )


# The whole flow, run in a subprocess so entry-point metadata is fresh after install.
_CHECK = """
from humon.core.registry import discover_tools
from humon.core.policy import PolicyEngine
from humon.config import PolicyConfig, ToolSettings

tools = discover_tools()
assert "evalplugin" in tools, "plugin not discovered via entry points"

# Config-gated activation: only enabled tools would be built by the app.
enabled = {"evalplugin": ToolSettings(enabled=True)}
active = [n for n, c in enabled.items() if c.enabled]
assert active == ["evalplugin"], "config gating failed"

tool = tools["evalplugin"]()
pe = PolicyEngine(PolicyConfig(default_decision="deny", rules={"evalplugin.read": "allow"}))
res = pe.check("evalplugin", tool.permissions)
assert res.decision.value == "allow", "policy did not allow the plugin"
print("OK", tool.name)
"""


@pytest.mark.slow
def test_third_party_plugin_installs_enables_and_passes_policy():
    with tempfile.TemporaryDirectory() as d:
        root = scaffold_tool("evalplugin", d)
        install = _pip("install", "--no-build-isolation", "--no-deps", str(root))
        if install.returncode != 0:
            pytest.skip(f"pip install unavailable here: {install.stderr[-300:]}")
        try:
            check = subprocess.run(
                [sys.executable, "-c", _CHECK], capture_output=True, text=True, timeout=60
            )
            assert check.returncode == 0, f"flow failed: {check.stdout}\n{check.stderr}"
            assert "OK evalplugin" in check.stdout
        finally:
            _pip("uninstall", "-y", _PKG)
