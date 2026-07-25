"""The hand-rolled agent loop (FR-3.1) — no frameworks.

One task = receive message → (optionally plan) → iterate [LLM call → parse tool
calls → **policy check** → maybe approval → execute → append result] → (optionally
reflect) → final answer. Loop guards bound iterations, tool calls, wall-clock, and
tokens (FR-3.3). Every tool call is authorized by the policy engine and written to
the audit log; every tool result is wrapped as untrusted data (FR-6.6).

Planner, reflector, and memory are optional collaborators injected by the app so
this module stays testable and framework-free. When absent, the loop is the bare
executor of M1.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Config
from ..state.repositories import AuditEntry, AuditRepo, SessionRepo
from .errors import LoopGuardTripped
from .interfaces import (
    ApprovalFn,
    CompletionRequest,
    LLMProvider,
    MemoryStore,
    Message,
    PolicyDecision,
    TaskStore,
    Tool,
    ToolContext,
    ToolDef,
)
from .policy import PolicyEngine
from .prompts import SYSTEM_PROMPT, wrap_untrusted

ProgressFn = Callable[[str], Awaitable[None]]


class Planner(Protocol):
    async def plan(self, user_text: str, tool_names: list[str]) -> list[str]: ...


class Reflector(Protocol):
    async def review(self, request: str, draft: str, transcript: list[Message]) -> str: ...


class MemoryManager(MemoryStore, Protocol):
    """What the agent needs from memory — a superset of the tool-facing MemoryStore."""

    async def retrieve_hints(self, session_id: str, query: str) -> str: ...
    async def record_episode(
        self, session_id: str, task: str, tools_used: list[str], success: bool, note: str
    ) -> None: ...
    async def maybe_compact(self, session_id: str, provider: LLMProvider, model: str) -> None: ...


@dataclass
class TaskOutcome:
    text: str
    tool_calls: int = 0
    iterations: int = 0
    tools_used: list[str] = field(default_factory=list)
    tokens: int = 0
    stopped_reason: str = "completed"


class Agent:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        tools: dict[str, Tool],
        policy: PolicyEngine,
        config: Config,
        session_repo: SessionRepo,
        audit: AuditRepo,
        logger: Any,
        planner: Planner | None = None,
        reflector: Reflector | None = None,
        memory: MemoryManager | None = None,
        tasks: TaskStore | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.policy = policy
        self.config = config
        self.sessions = session_repo
        self.audit = audit
        self.logger = logger
        self.planner = planner
        self.reflector = reflector
        self.memory = memory
        self.tasks = tasks

    def _tool_defs(self) -> list[ToolDef]:
        return [
            ToolDef(name=t.name, description=t.description, input_schema=t.input_schema)
            for t in self.tools.values()
        ]

    async def run_task(
        self,
        *,
        session_id: str,
        user_text: str,
        request_approval: ApprovalFn,
        progress: ProgressFn | None = None,
    ) -> TaskOutcome:
        limits = self.config.limits
        try:
            async with asyncio.timeout(limits.task_timeout_s):
                return await self._run_inner(session_id, user_text, request_approval, progress)
        except TimeoutError:
            msg = f"Task exceeded the {limits.task_timeout_s}s time budget and was stopped."
            self.logger.warning("task.timeout", session=session_id)
            return TaskOutcome(text=msg, stopped_reason="timeout")

    async def _run_inner(
        self,
        session_id: str,
        user_text: str,
        request_approval: ApprovalFn,
        progress: ProgressFn | None,
    ) -> TaskOutcome:
        limits = self.config.limits
        models = self.config.models

        async def note(text: str) -> None:
            if progress is not None:
                await progress(text)

        await self.sessions.add_message(session_id, Message(role="user", content=user_text))

        system = SYSTEM_PROMPT
        # Compacted history summary (FR-3.5), if the session has one.
        session_row = await self.sessions.get(session_id)
        if session_row and session_row.get("summary"):
            system += f"\n\nSummary of earlier conversation:\n{session_row['summary']}"
        if self.memory is not None:
            hints = await self.memory.retrieve_hints(session_id, user_text)
            if hints:
                system += f"\n\nRelevant memory and past outcomes:\n{hints}"

        # Optional planning pass (FR-3.2).
        plan: list[str] = []
        if self.planner is not None and self.config.agent.planning:
            await note("Planning…")
            plan = await self.planner.plan(user_text, list(self.tools))
            if len(plan) > 1:
                await self.sessions.set_plan(session_id, {"steps": plan})
                pretty = "\n".join(f"{i}. {s}" for i, s in enumerate(plan, 1))
                await note(f"*Plan:*\n{pretty}")

        # Windowed history; older turns live in the compacted summary above.
        messages = await self.sessions.history(session_id, limit=60)
        tool_defs = self._tool_defs()

        outcome = TaskOutcome(text="")
        tokens = 0
        for iteration in range(1, limits.max_iterations + 1):
            outcome.iterations = iteration
            if tokens > limits.token_budget:
                outcome.stopped_reason = "token_budget"
                raise LoopGuardTripped("token budget exceeded")

            await note("Thinking…")
            req = CompletionRequest(
                model=models.default,
                system=system,
                messages=messages,
                tools=tool_defs,
                max_tokens=2048,
            )
            resp = await self.provider.complete(req)
            tokens += resp.usage.input_tokens + resp.usage.output_tokens
            outcome.tokens = tokens

            assistant_msg = Message(role="assistant", content=resp.text, tool_calls=resp.tool_calls)
            await self.sessions.add_message(session_id, assistant_msg)
            messages.append(assistant_msg)

            if not resp.tool_calls:
                outcome.text = resp.text
                break

            for call in resp.tool_calls:
                if outcome.tool_calls >= limits.max_tool_calls:
                    outcome.stopped_reason = "max_tool_calls"
                    raise LoopGuardTripped("max tool calls exceeded")
                outcome.tool_calls += 1
                result_text = await self._dispatch_tool(
                    session_id, call.name, call.arguments, request_approval, note
                )
                if call.name not in outcome.tools_used:
                    outcome.tools_used.append(call.name)
                tool_msg = Message(
                    role="tool",
                    content=wrap_untrusted(call.name, result_text),
                    tool_call_id=call.id,
                )
                await self.sessions.add_message(session_id, tool_msg)
                messages.append(tool_msg)
        else:
            outcome.stopped_reason = "max_iterations"
            outcome.text = (
                outcome.text or "I reached my step limit before finishing. Here is where I got to."
            )

        # Optional reflection pass (FR-3.4).
        if (
            self.reflector is not None
            and self.config.agent.reflection
            and outcome.tool_calls >= self.config.agent.reflection_min_tools
            and outcome.text
        ):
            await note("Reviewing answer…")
            outcome.text = await self.reflector.review(user_text, outcome.text, messages)

        # Episodic memory + compaction (FR-5.3 / FR-3.5).
        if self.memory is not None:
            await self.memory.record_episode(
                session_id,
                user_text,
                outcome.tools_used,
                outcome.stopped_reason == "completed",
                outcome.text[:500],
            )
            await self.memory.maybe_compact(session_id, self.provider, models.cheap_or_default())

        return outcome

    async def _dispatch_tool(
        self,
        session_id: str,
        name: str,
        args: dict[str, Any],
        request_approval: ApprovalFn,
        note: ProgressFn,
    ) -> str:
        """Authorize, (maybe) get approval, execute, and audit one tool call."""

        tool = self.tools.get(name)
        if tool is None:
            await self.audit.record(
                AuditEntry(
                    tool=name,
                    decision="deny",
                    session_id=session_id,
                    exit_status="denied",
                    args=args,
                )
            )
            return f"Error: tool '{name}' is not available."

        # A tool may refine which permissions a *specific* call needs (e.g. files
        # read vs write) via an optional permissions_for(args); it still does not
        # decide allow/deny — the policy engine does.
        refine = getattr(tool, "permissions_for", None)
        perms = refine(args) if callable(refine) else tool.permissions
        result = self.policy.check(name, perms)
        approver: str | None = None

        if result.decision is PolicyDecision.DENY:
            await self.audit.record(
                AuditEntry(
                    tool=name,
                    decision="deny",
                    permission=result.permission,
                    session_id=session_id,
                    args=args,
                    exit_status="denied",
                )
            )
            return f"Denied by policy: {result.reason}"

        if result.decision is PolicyDecision.REQUIRE_APPROVAL:
            summary = self._approval_summary(name, args, result.permission)
            await note(f"Waiting for approval: {name}")
            approved = await request_approval(summary)
            if not approved:
                await self.audit.record(
                    AuditEntry(
                        tool=name,
                        decision="require_approval",
                        permission=result.permission,
                        session_id=session_id,
                        args=args,
                        exit_status="denied",
                    )
                )
                return f"The action '{name}' was not approved by the operator."
            approver = "human"

        await note(f"Running {name}…")
        settings = self.config.tools.get(name)
        ctx = ToolContext(
            session_id=session_id,
            config=settings.model_dump() if settings is not None else {},
            jail_paths=list(self._jail_for(name)),
            logger=self.logger,
            request_approval=request_approval,
            memory=self.memory,
            tasks=self.tasks,
        )
        started = time.monotonic()
        try:
            tool_result = await tool.execute(args, ctx)
        except Exception as exc:
            self.logger.exception("tool.error", tool=name, session=session_id)
            await self.audit.record(
                AuditEntry(
                    tool=name,
                    decision=result.decision.value,
                    permission=result.permission,
                    approver=approver,
                    session_id=session_id,
                    args=args,
                    exit_status="error",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )
            return f"Tool '{name}' failed: {exc}"

        duration_ms = int((time.monotonic() - started) * 1000)
        content = tool_result.get("content") or ""
        await self.audit.record(
            AuditEntry(
                tool=name,
                decision=result.decision.value,
                permission=result.permission,
                approver=approver,
                session_id=session_id,
                args=args,
                exit_status="ok" if tool_result.get("ok") else "error",
                duration_ms=duration_ms,
                output=content,
            )
        )
        if not tool_result.get("ok"):
            return f"Error: {tool_result.get('error') or 'tool reported failure'}"
        return content

    def _approval_summary(self, name: str, args: dict[str, Any], permission: str | None) -> str:
        arg_preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:4])
        return f"Tool `{name}` (permission `{permission}`) with: {arg_preview}"

    def _jail_for(self, name: str) -> list[str]:
        settings = self.config.tools.get(name)
        if settings is None:
            return []
        data = settings.model_dump()
        return list(data.get("jail_paths", []) or [])
