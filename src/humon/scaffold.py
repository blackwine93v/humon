"""``humon new-tool`` scaffolding (FR-7.4).

Generates a minimal, installable tool plugin: a package with a ``Tool``
implementation, an entry-point registration, and a starter test — the same shape
a third-party contributor would ship.
"""

from __future__ import annotations

from pathlib import Path

from .core.errors import HumonError

_TOOL_PY = '''\
"""The {name} tool."""

from __future__ import annotations

from typing import Any

from humon.core.interfaces import ToolContext, ToolResult


class {cls}:
    name = "{name}"
    description = "TODO: describe what {name} does (shown to the model)."
    input_schema: dict[str, Any] = {{
        "type": "object",
        "properties": {{
            "example": {{"type": "string", "description": "TODO"}},
        }},
        "required": ["example"],
        "additionalProperties": False,
    }}
    # Declare the least-privileged permissions you need; the policy engine decides.
    permissions = ["{name}.read"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        example = str(args.get("example", ""))
        # TODO: do the real work here.
        return {{"ok": True, "content": f"{name} received: {{example}}", "error": None}}
'''

_TEST_PY = """\
import pytest

from {pkg}.tool import {cls}


@pytest.mark.asyncio
async def test_{name}_smoke():
    tool = {cls}()
    result = await tool.execute({{"example": "hi"}}, ctx=_ctx())
    assert result["ok"]


def _ctx():
    from humon.core.interfaces import ToolContext

    async def _approve(_summary: str) -> bool:
        return True

    import logging

    return ToolContext(
        session_id="test", config={{}}, jail_paths=[],
        logger=logging.getLogger("test"), request_approval=_approve,
    )
"""

_PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "humon-tool-{name}"
version = "0.1.0"
description = "A Humon tool: {name}"
requires-python = ">=3.11"
dependencies = ["humon"]

[project.entry-points."humon.tools"]
{name} = "{pkg}.tool:{cls}"

[tool.hatch.build.targets.wheel]
packages = ["src/{pkg}"]
"""


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) + "Tool"


def scaffold_tool(name: str, path: str) -> Path:
    if not name.isidentifier():
        raise HumonError(f"Tool name must be a valid snake_case identifier, got {name!r}")
    cls = _class_name(name)
    pkg = f"humon_tool_{name}"
    root = Path(path) / f"humon-tool-{name}"
    src = root / "src" / pkg
    tests = root / "tests"
    if root.exists():
        raise HumonError(f"Destination already exists: {root}")
    src.mkdir(parents=True)
    tests.mkdir(parents=True)

    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "tool.py").write_text(_TOOL_PY.format(name=name, cls=cls), encoding="utf-8")
    (tests / f"test_{name}.py").write_text(
        _TEST_PY.format(name=name, cls=cls, pkg=pkg), encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        _PYPROJECT.format(name=name, cls=cls, pkg=pkg), encoding="utf-8"
    )
    return root
