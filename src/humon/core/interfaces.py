"""The contract layer (PRD §8).

This module is the ONLY thing ``humon.core`` may import from within the package
besides its own siblings. Channels, tools, and providers implement the protocols
declared here and import *only* this module from ``core``. The layering is
enforced in CI by import-linter (see ``.importlinter``).

Keeping every cross-layer type in one place is what lets the agent loop stay
ignorant of Slack, of Anthropic, and of subprocess — it speaks only these
protocols.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, TypedDict, runtime_checkable

# ─────────────────────────────────────────────────────────────────────────────
# Messages & completions — the normalized shape every provider maps to/from.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """One turn in a conversation, provider-agnostic.

    ``role`` is one of ``system``/``user``/``assistant``/``tool``. Assistant
    turns may carry ``tool_calls``; ``tool`` turns carry a ``tool_call_id``
    linking the result back to its call.
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class ToolDef:
    """A tool advertised to the model (name + JSON Schema)."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class CompletionRequest:
    model: str
    system: str
    messages: list[Message]
    tools: list[ToolDef] = field(default_factory=list)
    max_tokens: int = 2048
    temperature: float | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class CompletionResponse:
    """Normalized completion: text blocks + tool-call blocks + stop reason."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"  # end_turn | tool_use | max_tokens
    usage: Usage = field(default_factory=Usage)
    raw: Any = None


# ─────────────────────────────────────────────────────────────────────────────
# Policy
# ─────────────────────────────────────────────────────────────────────────────


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class PolicyResult:
    decision: PolicyDecision
    reason: str
    permission: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Tools (PRD §8.1)
# ─────────────────────────────────────────────────────────────────────────────


class ToolResult(TypedDict):
    ok: bool
    content: str  # shown to the model, size-capped by the tool
    error: str | None


# Routes an approval prompt through the active channel; returns True if approved.
ApprovalFn = Callable[[str], Awaitable[bool]]


class MemoryStore(Protocol):
    """Long-term memory surface exposed to the ``memory`` tool.

    Lets the tool store/recall notes without importing ``core`` internals or
    ``state`` (layering). The app injects a concrete implementation.
    """

    async def store(self, text: str, kind: str = "note", session_id: str | None = None) -> int: ...
    async def search(self, query: str, k: int = 5) -> list[str]: ...
    async def list_notes(self) -> list[tuple[int, str, str]]: ...  # (id, kind, text)
    async def forget(self, note_id: int) -> bool: ...


class TaskStore(Protocol):
    """Scheduled-task surface exposed to the ``schedule`` tool (same layering
    reasoning as MemoryStore)."""

    async def add_task(
        self, description: str, schedule: str, session_id: str | None = None
    ) -> tuple[int, float | None]: ...  # (task_id, next_run epoch or None)
    async def list_tasks(self) -> list[tuple[int, str, str, float | None]]: ...
    async def delete_task(self, task_id: int) -> bool: ...


# ─────────────────────────────────────────────────────────────────────────────
# Capabilities — the generic host-service seam (FR-7 extensibility)
# ─────────────────────────────────────────────────────────────────────────────
#
# Rather than adding a new ``ToolContext`` field for every host service a plugin
# might need (which forces a core edit each time), services are registered under
# a *name* and looked up by that name through ``ToolContext.services``. Built-in
# services register under well-known names (``"memory"``, ``"tasks"``,
# ``"embeddings"``); a plugin-provided service (e.g. a vault index) registers
# under whatever name its config uses. This is the one seam that lets an
# out-of-tree package add a whole new capability without touching core.


class Capabilities(Protocol):
    """A name-keyed registry of host- and plugin-provided services.

    ``get`` returns ``object`` (not ``Any``) so the strict-typed core never
    leaks an untyped value: the caller narrows the result to whatever protocol
    it expects (``isinstance``/``cast``) — its *own* vocabulary, declared in its
    own package, not something core has to know about.
    """

    def get(self, name: str) -> object | None: ...
    def require(self, name: str) -> object: ...  # raises HumonError if absent
    def names(self) -> list[str]: ...


class Embedder(Protocol):
    """Host embedding service, registered under the name ``"embeddings"`` when
    the active provider advertises the ``embeddings`` capability. A capability
    such as a semantic vault index can build on this without importing a
    provider SDK or ``humon.state``."""

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class CapabilityContext:
    """What :meth:`CapabilityProvider.setup` receives.

    A provider builds its service from its own ``config`` slice and may persist
    under ``data_dir`` — a private per-capability directory — so it never imports
    ``humon.state``. ``services`` exposes host infrastructure already registered
    (e.g. ``"embeddings"``) so a provider can compose on top of it.
    """

    name: str
    config: dict[str, Any]
    logger: Any
    data_dir: str
    services: Capabilities


@runtime_checkable
class CapabilityProvider(Protocol):
    """A plugin that provides a named host-style service.

    Discovered via the ``humon.capabilities`` entry-point group and activated
    only when config enables it (installation is never activation). ``setup``
    returns the service object registered into the shared registry under
    ``name``; ``aclose`` releases resources at shutdown.
    """

    name: str

    async def setup(self, ctx: CapabilityContext) -> object: ...
    async def aclose(self) -> None: ...


@dataclass
class ToolContext:
    """Everything a tool needs at execution time — and nothing more.

    A tool receives its own config slice and jail paths; it cannot see other
    tools' config or reach outside its jail. ``request_approval`` routes a
    human-in-the-loop prompt through whatever channel started the session.
    ``memory`` and ``tasks`` are kept as convenience handles for the built-in
    tools; any other host service is reached by name through ``services`` (the
    capability seam), and ``data_dir`` is a private directory a tool may persist
    into.
    """

    session_id: str
    config: dict[str, Any]
    jail_paths: list[str]
    logger: Any
    request_approval: ApprovalFn
    memory: MemoryStore | None = None
    tasks: TaskStore | None = None
    services: Capabilities | None = None
    data_dir: str | None = None


@runtime_checkable
class Tool(Protocol):
    name: str  # unique, snake_case
    description: str  # shown to the model
    input_schema: dict[str, Any]  # JSON Schema
    permissions: list[str]  # e.g. ["shell.exec", "fs.write"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...


# ─────────────────────────────────────────────────────────────────────────────
# Channels (PRD §8.2)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class InboundMessage:
    """A message arriving from a channel that should drive an agent session."""

    session_ref: str  # stable per-conversation key (e.g. Slack thread ts)
    user: str
    text: str
    message_ref: str | None = None


# Called by a channel for every inbound message it decides to route.
MessageHandler = Callable[[InboundMessage], Awaitable[None]]


class Channel(Protocol):
    name: str

    async def start(self, on_message: MessageHandler) -> None: ...
    # send returns a message_ref usable with update() (signature evolved from the
    # PRD's -> None so the agent can edit its "working…" message in place).
    async def send(self, session_ref: str, text: str) -> str: ...
    async def update(self, message_ref: str, text: str) -> None: ...
    async def request_approval(self, session_ref: str, summary: str, timeout_s: int) -> bool: ...
    async def stop(self) -> None: ...


# ─────────────────────────────────────────────────────────────────────────────
# LLM providers (PRD §8.3)
# ─────────────────────────────────────────────────────────────────────────────

# Capability tokens a provider may declare.
CAP_TOOLS = "tools"
CAP_STREAMING = "streaming"
CAP_EMBEDDINGS = "embeddings"


class LLMProvider(Protocol):
    name: str
    capabilities: set[str]  # subset of {tools, streaming, embeddings}

    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
