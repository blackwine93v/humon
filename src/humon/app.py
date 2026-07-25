"""Composition root: wire config → state → providers → tools → policy → channels
→ agent, and run the service.

This is the only module allowed to know about every layer at once. It builds the
object graph, translates inbound channel messages into agent tasks (or ``!``
commands), and manages graceful shutdown (FR-1.3).
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

from .config import Config
from .core.agent import Agent
from .core.capabilities import ServiceRegistry
from .core.errors import HumonError
from .core.interfaces import CAP_EMBEDDINGS, CapabilityContext, InboundMessage
from .core.memory import MemoryManager
from .core.planner import LLMPlanner
from .core.policy import PolicyEngine
from .core.reflector import LLMReflector
from .core.registry import (
    discover_capabilities,
    discover_channels,
    discover_providers,
    discover_tools,
)
from .core.scheduler import Scheduler
from .core.session import SessionBusy, SessionManager
from .logging import get_logger
from .state.db import Database
from .state.repositories import AuditRepo, MemoryRepo, SessionRepo, TaskRepo
from .state.vectors import VectorIndex


class App:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.logger = get_logger("humon.app")
        self.db = Database(config.state.db_path)
        self._shutdown = asyncio.Event()
        self._channels: list[Any] = []
        # built in start()
        self.sessions: SessionManager | None = None
        self.agent: Agent | None = None
        self.session_repo: SessionRepo | None = None
        self.audit_repo: AuditRepo | None = None
        self.memory_repo: MemoryRepo | None = None
        self.task_repo: TaskRepo | None = None
        self.memory: MemoryManager | None = None
        self.scheduler: Scheduler | None = None
        self._channel_by_name: dict[str, Any] = {}
        self.tools: dict[str, Any] = {}
        self.services = ServiceRegistry()
        self._capabilities: list[Any] = []  # active CapabilityProvider instances

    async def build(self) -> None:
        await self.db.connect()
        self.session_repo = SessionRepo(self.db)
        self.audit_repo = AuditRepo(self.db)
        self.memory_repo = MemoryRepo(self.db)
        self.task_repo = TaskRepo(self.db)

        provider = self._build_provider()
        self.tools = self._build_tools()
        policy = PolicyEngine(self.config.policy)
        self.sessions = SessionManager(
            self.session_repo, self.config.limits.max_concurrent_sessions
        )

        # Long-term memory (FR-5): sqlite-vec index + manager, if enabled.
        memory: MemoryManager | None = None
        if self.config.memory.enabled:
            vectors = VectorIndex(self.db)
            await vectors.setup()
            memory = MemoryManager(
                memory_repo=self.memory_repo,
                session_repo=self.session_repo,
                vectors=vectors,
                provider=provider,
                config=self.config.memory,
            )
            self.memory = memory

        planner = (
            LLMPlanner(provider, self.config.models.strong_or_default())
            if self.config.agent.planning
            else None
        )
        reflector = (
            LLMReflector(provider, self.config.models.strong_or_default())
            if self.config.agent.reflection
            else None
        )

        # In-process scheduler (FR-4.4): reached by the schedule tool, resumes from
        # SQLite on restart.
        self.scheduler = Scheduler(
            self.task_repo, self._run_scheduled, get_logger("humon.scheduler")
        )

        # Capability seam (FR-7): host services register under well-known names,
        # then config-enabled plugin capabilities are set up and registered too.
        # Tools reach any of them by name through ``ToolContext.services``.
        self.services.register("memory", memory)
        self.services.register("tasks", self.scheduler)
        if CAP_EMBEDDINGS in getattr(provider, "capabilities", set()):
            self.services.register("embeddings", provider)
        await self._build_capabilities()

        self.agent = Agent(
            provider=provider,
            tools=self.tools,
            policy=policy,
            config=self.config,
            session_repo=self.session_repo,
            audit=self.audit_repo,
            logger=get_logger("humon.agent"),
            planner=planner,
            reflector=reflector,
            memory=memory,
            tasks=self.scheduler,
            services=self.services,
            data_dir=self.config.state.data_dir,
        )
        self._channels = self._build_channels()
        self._channel_by_name = {c.name: c for c in self._channels}
        self.logger.info(
            "app.built",
            provider=provider.name,
            tools=list(self.tools),
            capabilities=self.services.names(),
            channels=[c.name for c in self._channels],
            memory=memory is not None,
            vectors=bool(memory and getattr(memory.vectors, "enabled", False)),
        )

    # ── builders ──────────────────────────────────────────────────────────────
    def _build_provider(self) -> Any:
        providers = discover_providers()
        name = self.config.provider.name
        cls = providers.get(name)
        if cls is None or _is_broken(cls):
            raise HumonError(f"Provider '{name}' is not installed/available.")
        return cls(
            {
                "api_key_env": self.config.provider.api_key_env,
                "base_url": self.config.provider.base_url,
                "models": self.config.models.model_dump(),
            }
        )

    def _build_tools(self) -> dict[str, Any]:
        available = discover_tools()
        tools: dict[str, Any] = {}
        for name in self.config.enabled_tools():
            cls = available.get(name)
            if cls is None or _is_broken(cls):
                self.logger.warning("tool.unavailable", tool=name)
                continue
            tools[name] = cls()
        return tools

    def _build_channels(self) -> list[Any]:
        available = discover_channels()
        built: list[Any] = []
        for name, slice_cfg in self.config.enabled_channels().items():
            cls = available.get(name)
            if cls is None or _is_broken(cls):
                self.logger.warning("channel.unavailable", channel=name)
                continue
            built.append(cls(slice_cfg))
        return built

    async def _build_capabilities(self) -> None:
        """Set up each config-enabled capability plugin and register the service
        it returns. A capability gets its own private ``data_dir`` subtree and a
        handle to host services already registered (e.g. ``embeddings``)."""

        available = discover_capabilities()
        data_root = self.config.state.data_dir
        for name in self.config.enabled_capabilities():
            cls = available.get(name)
            if cls is None or _is_broken(cls):
                self.logger.warning("capability.unavailable", capability=name)
                continue
            settings = self.config.capabilities.get(name)
            cap_dir = os.path.join(data_root, "capabilities", name)
            await asyncio.to_thread(os.makedirs, cap_dir, exist_ok=True)
            provider = cls()
            ctx = CapabilityContext(
                name=name,
                config=settings.model_dump() if settings is not None else {},
                logger=get_logger(f"humon.capability.{name}"),
                data_dir=cap_dir,
                services=self.services,
            )
            try:
                service = await provider.setup(ctx)
            except Exception:
                self.logger.exception("capability.setup_failed", capability=name)
                continue
            self._capabilities.append(provider)
            self.services.register(name, service)
            self.logger.info("capability.ready", capability=name)

    # ── run loop ──────────────────────────────────────────────────────────────
    async def run(self) -> None:
        await self.build()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._shutdown.set)
            except NotImplementedError:  # pragma: no cover - non-unix
                pass
        for channel in self._channels:
            await channel.start(self._make_handler(channel))
        if self.scheduler is not None:
            await self.scheduler.start()  # resumes tasks persisted in SQLite
        self.logger.info("app.running")
        await self._shutdown.wait()
        await self.shutdown()

    async def shutdown(self) -> None:
        self.logger.info("app.shutdown")
        if self.scheduler is not None:
            await self.scheduler.stop()
        for channel in self._channels:
            try:
                await channel.stop()
            except Exception:
                self.logger.exception("channel.stop.error", channel=channel.name)
        for capability in self._capabilities:
            try:
                await capability.aclose()
            except Exception:
                self.logger.exception("capability.stop.error", capability=capability.name)
        await self.db.close()

    async def _run_scheduled(self, task_row: dict[str, Any]) -> None:
        """Execute a due scheduled task and deliver the result to its channel.

        Scheduled tasks were pre-approved at creation (their creation was itself
        approval-gated), so they run with an auto-approver.
        """

        assert self.sessions and self.session_repo and self.agent
        session_id = task_row.get("session_id")
        if not session_id:
            self.logger.warning("scheduler.no_session", task_id=task_row.get("id"))
            return
        session = await self.session_repo.get(session_id)
        channel = self._channel_by_name.get(session["channel"]) if session else None
        if channel is None:
            self.logger.warning("scheduler.no_channel", session=session_id)
            return

        async def auto_approve(_summary: str) -> bool:
            return True

        try:
            outcome = await self.sessions.run(
                session_id,
                lambda: self.agent.run_task(  # type: ignore[union-attr]
                    session_id=session_id,
                    user_text=task_row["description"],
                    request_approval=auto_approve,
                ),
            )
            await channel.send(session_id, f":alarm_clock: {outcome.text}")
        except SessionBusy:
            self.logger.info("scheduler.session_busy", session=session_id)

    # ── message handling ──────────────────────────────────────────────────────
    def _make_handler(self, channel: Any) -> Any:
        async def handler(msg: InboundMessage) -> None:
            await self._handle(channel, msg)

        return handler

    async def _handle(self, channel: Any, msg: InboundMessage) -> None:
        assert self.sessions and self.session_repo and self.agent
        text = msg.text.strip()
        await self.session_repo.ensure(msg.session_ref, channel.name, msg.user)

        if text.startswith("!"):
            reply = await self._command(channel, msg, text)
            if reply:
                await channel.send(msg.session_ref, reply)
            return

        if self.sessions.is_running(msg.session_ref):
            await channel.send(
                msg.session_ref,
                "I'm still working on your previous request in this thread. "
                "Send `!cancel` to stop it.",
            )
            return

        working_ref = await channel.send(msg.session_ref, "_working…_")

        async def progress(note: str) -> None:
            await channel.update(working_ref, f"_working… {note}_")

        async def request_approval(summary: str) -> bool:
            return await channel.request_approval(
                msg.session_ref, summary, self.config.policy.approval.timeout_s
            )

        try:
            outcome = await self.sessions.run(
                msg.session_ref,
                lambda: self.agent.run_task(  # type: ignore[union-attr]
                    session_id=msg.session_ref,
                    user_text=text,
                    request_approval=request_approval,
                    progress=progress,
                ),
            )
            await channel.update(working_ref, outcome.text or "(no response)")
        except SessionBusy:
            await channel.send(msg.session_ref, "That thread is already busy.")
        except Exception as exc:
            self.logger.exception("task.error", session=msg.session_ref)
            await channel.update(working_ref, f":x: Something went wrong: {exc}")

    async def _command(self, channel: Any, msg: InboundMessage, text: str) -> str:
        assert self.sessions and self.audit_repo
        parts = text.split()
        cmd = parts[0].lower()
        if cmd == "!help":
            return (
                "*Humon commands*\n"
                "`!status` — uptime, sessions, tools\n"
                "`!sessions` — active sessions\n"
                "`!tools` — enabled tools\n"
                "`!capabilities` — active capabilities\n"
                "`!cancel` — stop this thread's task\n"
                "`!audit` — recent tool calls\n"
                "`!memory list` / `!memory forget <id>` — long-term memory"
            )
        if cmd == "!tools":
            names = ", ".join(sorted(self.tools)) or "(none enabled)"
            return f"Enabled tools: {names}"
        if cmd == "!capabilities":
            names = ", ".join(self.services.names()) or "(none)"
            return f"Active capabilities: {names}"
        if cmd == "!sessions":
            active = self.sessions.active_ids()
            return "Active sessions: " + (", ".join(active) if active else "none")
        if cmd == "!status":
            return (
                f"Humon up. Tools: {len(self.tools)}. "
                f"Active sessions: {len(self.sessions.active_ids())}/"
                f"{self.config.limits.max_concurrent_sessions}."
            )
        if cmd == "!cancel":
            return (
                "Cancelled current task."
                if self.sessions.cancel(msg.session_ref)
                else "No task running in this thread."
            )
        if cmd == "!audit":
            rows = await self.audit_repo.recent(10)
            if not rows:
                return "No audit entries yet."
            lines = [f"• {r['tool']} → {r['decision']}/{r['exit_status'] or '-'}" for r in rows]
            return "*Recent tool calls*\n" + "\n".join(lines)
        if cmd == "!memory":
            return await self._memory_command(parts)
        return f"Unknown command: {cmd}. Try `!help`."

    async def _memory_command(self, parts: list[str]) -> str:
        assert self.memory_repo
        if len(parts) >= 2 and parts[1] == "list":
            rows = await self.memory_repo.all()
            if not rows:
                return "Memory is empty."
            return "*Memory*\n" + "\n".join(
                f"[{r['id']}] ({r['kind']}) {r['text'][:80]}" for r in rows[:20]
            )
        if len(parts) >= 3 and parts[1] == "forget":
            try:
                note_id = int(parts[2])
            except ValueError:
                return "Usage: `!memory forget <id>`"
            ok = await self.memory_repo.forget(note_id)
            return "Forgotten." if ok else f"No memory note with id {note_id}."
        return "Usage: `!memory list` or `!memory forget <id>`"


def _is_broken(obj: Any) -> bool:
    return obj.__class__.__name__ == "_BrokenPlugin"
