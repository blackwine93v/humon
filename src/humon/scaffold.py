"""Plugin scaffolding (FR-7.4).

Generates a minimal, installable plugin — a package with the right protocol
implementation, an entry-point registration, and a starter test — the same shape
a third-party contributor (or a future ``humon-vault`` / ``humon-code`` package)
would ship. Four kinds are supported, one per extension seam:

    tool        humon.tools         a Tool the model can call
    capability  humon.capabilities  a named host service reached via ctx.services
    channel     humon.channels      a conversation surface
    provider    humon.providers     an LLM backend

Templates use ``%%TOKEN%%`` placeholders (not ``str.format``) so the Python code
inside them — full of ``{}`` — needs no brace escaping.
"""

from __future__ import annotations

from pathlib import Path

from .core.errors import HumonError

# ── Tool ─────────────────────────────────────────────────────────────────────
_TOOL_PY = '''\
"""The %%NAME%% tool."""

from __future__ import annotations

from typing import Any

from humon.core.interfaces import ToolContext, ToolResult


class %%CLS%%:
    name = "%%NAME%%"
    description = "TODO: describe what %%NAME%% does (shown to the model)."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "example": {"type": "string", "description": "TODO"},
        },
        "required": ["example"],
        "additionalProperties": False,
    }
    # Declare the least-privileged permissions you need; the policy engine decides.
    permissions = ["%%NAME%%.read"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        example = str(args.get("example", ""))
        # A tool reaches a host service by name, e.g.:
        #   kb = ctx.services.require("knowledge_base") if ctx.services else None
        # TODO: do the real work here.
        return {"ok": True, "content": f"%%NAME%% received: {example}", "error": None}
'''

_TOOL_TEST = """\
import pytest

from %%PKG%%.tool import %%CLS%%


@pytest.mark.asyncio
async def test_%%NAME%%_smoke():
    tool = %%CLS%%()
    result = await tool.execute({"example": "hi"}, ctx=_ctx())
    assert result["ok"]


def _ctx():
    import logging

    from humon.core.interfaces import ToolContext

    async def _approve(_summary: str) -> bool:
        return True

    return ToolContext(
        session_id="test", config={}, jail_paths=[],
        logger=logging.getLogger("test"), request_approval=_approve,
    )
"""

# ── Capability ───────────────────────────────────────────────────────────────
_CAP_PY = '''\
"""The %%NAME%% capability — provides a named host service to tools.

A capability is discovered via the ``humon.capabilities`` entry point and set up
only when config enables it. ``setup`` returns the service object registered
under the name "%%NAME%%"; tools reach it with ``ctx.services.require("%%NAME%%")``.
"""

from __future__ import annotations

from typing import Any

from humon.core.interfaces import CapabilityContext


class %%TITLE%%Service:
    """The object registered under the name "%%NAME%%". Give it whatever methods
    your tools need; keep it importable only from this package (its protocol is
    your vocabulary, not humon core's)."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = data_dir

    def status(self) -> str:
        return "%%NAME%% ready"


class %%CLS%%:
    name = "%%NAME%%"

    def __init__(self) -> None:
        self._service: %%TITLE%%Service | None = None

    async def setup(self, ctx: CapabilityContext) -> Any:
        # Build the service from ctx.config; persist under ctx.data_dir (a private
        # per-capability dir); compose on host services via ctx.services (e.g.
        # ctx.services.get("embeddings")). Return the object tools look up by name.
        self._service = %%TITLE%%Service(ctx.data_dir)
        return self._service

    async def aclose(self) -> None:
        # Release resources (close files/connections) at shutdown.
        self._service = None
'''

_CAP_TEST = """\
import pytest

from %%PKG%%.capability import %%CLS%%


@pytest.mark.asyncio
async def test_%%NAME%%_setup_and_close(tmp_path):
    provider = %%CLS%%()
    service = await provider.setup(_ctx(str(tmp_path)))
    assert service is not None
    await provider.aclose()


def _ctx(data_dir):
    import logging

    from humon.core.capabilities import ServiceRegistry
    from humon.core.interfaces import CapabilityContext

    return CapabilityContext(
        name="%%NAME%%", config={}, logger=logging.getLogger("test"),
        data_dir=data_dir, services=ServiceRegistry(),
    )
"""

# ── Channel ──────────────────────────────────────────────────────────────────
_CHANNEL_PY = '''\
"""The %%NAME%% channel — a conversation surface driving agent sessions."""

from __future__ import annotations

from typing import Any

from humon.core.interfaces import MessageHandler


class %%CLS%%:
    name = "%%NAME%%"

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._handler: MessageHandler | None = None

    async def start(self, on_message: MessageHandler) -> None:
        self._handler = on_message
        # TODO: connect to your platform (outbound only) and call
        # self._handler(InboundMessage(session_ref=..., user=..., text=...)) per message.

    async def send(self, session_ref: str, text: str) -> str:
        # TODO: deliver `text`; return a message_ref usable with update().
        raise NotImplementedError

    async def update(self, message_ref: str, text: str) -> None:
        raise NotImplementedError

    async def request_approval(self, session_ref: str, summary: str, timeout_s: int) -> bool:
        # Human-in-the-loop gate. Deny is the safe default until you implement it.
        return False

    async def stop(self) -> None:
        self._handler = None
'''

_CHANNEL_TEST = """\
def test_%%NAME%%_construct():
    from %%PKG%%.channel import %%CLS%%

    channel = %%CLS%%({})
    assert channel.name == "%%NAME%%"
"""

# ── Provider ─────────────────────────────────────────────────────────────────
_PROVIDER_PY = '''\
"""The %%NAME%% LLM provider."""

from __future__ import annotations

from typing import Any

from humon.core.interfaces import CompletionRequest, CompletionResponse


class %%CLS%%:
    name = "%%NAME%%"
    # Add "tools" / "streaming" / "embeddings" as you support them.
    capabilities: set[str] = set()

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        # Import your heavy SDK lazily inside the methods below, never at module load.

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        # TODO: call your model and map the reply onto CompletionResponse.
        raise NotImplementedError

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Only needed if "embeddings" is in capabilities.
        raise NotImplementedError
'''

_PROVIDER_TEST = """\
def test_%%NAME%%_construct():
    from %%PKG%%.provider import %%CLS%%

    provider = %%CLS%%({})
    assert provider.name == "%%NAME%%"
"""

_PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "humon-%%KIND%%-%%NAME%%"
version = "0.1.0"
description = "A Humon %%KIND%%: %%NAME%%"
requires-python = ">=3.11"
dependencies = ["humon"]

[project.entry-points."%%GROUP%%"]
%%NAME%% = "%%PKG%%.%%MODULE%%:%%CLS%%"

[tool.hatch.build.targets.wheel]
packages = ["src/%%PKG%%"]
"""


# kind → (entry-point group, module filename, class suffix, impl template, test template)
_KINDS: dict[str, tuple[str, str, str, str, str]] = {
    "tool": ("humon.tools", "tool", "Tool", _TOOL_PY, _TOOL_TEST),
    "capability": ("humon.capabilities", "capability", "Capability", _CAP_PY, _CAP_TEST),
    "channel": ("humon.channels", "channel", "Channel", _CHANNEL_PY, _CHANNEL_TEST),
    "provider": ("humon.providers", "provider", "Provider", _PROVIDER_PY, _PROVIDER_TEST),
}


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _render(template: str, **tokens: str) -> str:
    out = template
    for key, value in tokens.items():
        out = out.replace(f"%%{key}%%", value)
    return out


def scaffold_plugin(kind: str, name: str, path: str) -> Path:
    """Generate an installable plugin skeleton of ``kind`` and return its root."""

    if kind not in _KINDS:
        raise HumonError(f"Unknown plugin kind {kind!r}; choose one of {', '.join(_KINDS)}.")
    if not name.isidentifier():
        raise HumonError(f"Plugin name must be a valid snake_case identifier, got {name!r}")

    group, module, suffix, impl_tmpl, test_tmpl = _KINDS[kind]
    title = _camel(name)
    cls = title + suffix
    pkg = f"humon_{kind}_{name}"
    root = Path(path) / f"humon-{kind}-{name}"
    if root.exists():
        raise HumonError(f"Destination already exists: {root}")

    src = root / "src" / pkg
    tests = root / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)

    tokens = {"NAME": name, "CLS": cls, "PKG": pkg, "TITLE": title}
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / f"{module}.py").write_text(_render(impl_tmpl, **tokens), encoding="utf-8")
    (tests / f"test_{name}.py").write_text(_render(test_tmpl, **tokens), encoding="utf-8")
    (root / "pyproject.toml").write_text(
        _render(_PYPROJECT, KIND=kind, GROUP=group, MODULE=module, **tokens),
        encoding="utf-8",
    )
    return root


def scaffold_tool(name: str, path: str) -> Path:
    """Back-compatible shortcut for ``scaffold_plugin("tool", ...)``."""

    return scaffold_plugin("tool", name, path)
