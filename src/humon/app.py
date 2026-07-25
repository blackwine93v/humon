"""Composition root: wire config → state → providers → tools → policy → channels
→ agent, and run the service.

This is the only module allowed to know about every layer at once. It builds the
object graph, translates inbound channel messages into agent tasks (or ``!``
commands), and manages graceful shutdown (FR-1.3).
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from .config import Config
from .core.agent import Agent
from .core.errors import HumonError
from .core.interfaces import InboundMessage
from .core.policy import PolicyEngine
from .core.registry import discover_channels, discover_providers, discover_tools
from .core.session import SessionBusy, SessionManager
from .logging import get_logger
from .state.db import Database
from .state.repositories import AuditRepo, MemoryRepo, SessionRepo, TaskRepo


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
        self.tools: dict[str, Any] = {}

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
        self.agent = Agent(
            provider=provider,
            tools=self.tools,
            policy=policy,
            config=self.config,
            session_repo=self.session_repo,
            audit=self.audit_repo,
            logger=get_logger("humon.agent"),
        )
        self._channels = self._build_channels()
        self.logger.info(
            "app.built",
            provider=provider.name,
            tools=list(self.tools),
            channels=[c.name for c in self._channels],
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
        slack_cfg = self.config.channels.slack
        if slack_cfg.enabled:
            cls = available.get("slack")
            if cls and not _is_broken(cls):
                built.append(cls(slack_cfg.model_dump()))
        return built

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
        self.logger.info("app.running")
        await self._shutdown.wait()
        await self.shutdown()

    async def shutdown(self) -> None:
        self.logger.info("app.shutdown")
        for channel in self._channels:
            try:
                await channel.stop()
            except Exception:
                self.logger.exception("channel.stop.error", channel=channel.name)
        await self.db.close()

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
                "`!cancel` — stop this thread's task\n"
                "`!audit` — recent tool calls\n"
                "`!memory list` / `!memory forget <id>` — long-term memory"
            )
        if cmd == "!tools":
            names = ", ".join(sorted(self.tools)) or "(none enabled)"
            return f"Enabled tools: {names}"
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
