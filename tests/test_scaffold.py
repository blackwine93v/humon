"""`humon new-tool` scaffolding (FR-7.4)."""

from __future__ import annotations

import importlib.util

import pytest

from conftest import make_logger
from humon.core.errors import HumonError
from humon.scaffold import scaffold_plugin, scaffold_tool


def test_scaffold_creates_expected_layout(tmp_path):
    root = scaffold_tool("weather", str(tmp_path))
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "humon_tool_weather" / "tool.py").is_file()
    assert (root / "tests" / "test_weather.py").is_file()
    pyproject = (root / "pyproject.toml").read_text()
    assert "humon.tools" in pyproject
    assert "weather = " in pyproject


def test_scaffold_rejects_bad_name(tmp_path):
    with pytest.raises(HumonError):
        scaffold_tool("not a name!", str(tmp_path))


def test_scaffold_rejects_unknown_kind(tmp_path):
    with pytest.raises(HumonError):
        scaffold_plugin("widget", "thing", str(tmp_path))


@pytest.mark.parametrize(
    ("kind", "group", "module", "cls"),
    [
        ("capability", "humon.capabilities", "capability", "MyxCapability"),
        ("channel", "humon.channels", "channel", "MyxChannel"),
        ("provider", "humon.providers", "provider", "MyxProvider"),
    ],
)
def test_scaffold_other_kinds_layout(tmp_path, kind, group, module, cls):
    root = scaffold_plugin(kind, "myx", str(tmp_path))
    pkg = f"humon_{kind}_myx"
    assert (root / "src" / pkg / f"{module}.py").is_file()
    assert (root / "tests" / "test_myx.py").is_file()
    pyproject = (root / "pyproject.toml").read_text()
    assert group in pyproject
    assert "myx = " in pyproject
    impl = (root / "src" / pkg / f"{module}.py").read_text()
    assert f"class {cls}" in impl


def test_scaffold_refuses_existing_dest(tmp_path):
    scaffold_tool("weather", str(tmp_path))
    with pytest.raises(HumonError):
        scaffold_tool("weather", str(tmp_path))


@pytest.mark.asyncio
async def test_generated_tool_imports_and_runs(tmp_path):
    root = scaffold_tool("greeter", str(tmp_path))
    tool_py = root / "src" / "humon_tool_greeter" / "tool.py"
    spec = importlib.util.spec_from_file_location("humon_tool_greeter.tool", tool_py)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tool = module.GreeterTool()
    assert tool.name == "greeter"
    assert tool.permissions == ["greeter.read"]

    from humon.core.interfaces import ToolContext

    async def _a(_s: str) -> bool:
        return True

    ctx = ToolContext(
        session_id="t",
        config={},
        jail_paths=[],
        logger=make_logger(),
        request_approval=_a,
    )
    result = await tool.execute({"example": "world"}, ctx)
    assert result["ok"]
