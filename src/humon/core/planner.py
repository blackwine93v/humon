"""Planner (FR-3.2): turn a request into an explicit numbered plan, and replan
on failure.

Uses the (strong) model to produce a short plan. A single-step plan is returned
as a one-element list; the agent only surfaces a plan to the user when it has more
than one step.
"""

from __future__ import annotations

import re

from .interfaces import CompletionRequest, LLMProvider, Message

_PLAN_SYSTEM = """\
You are the planning stage of an agent. Break the user's request into a short,
concrete numbered plan (1-6 steps) using the available tools. If the request is a
single trivial step, output just one line. Output ONLY the numbered list, nothing
else.
"""

_STEP_RE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*\S)\s*$")


class LLMPlanner:
    def __init__(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model

    async def plan(self, user_text: str, tool_names: list[str]) -> list[str]:
        tools = ", ".join(tool_names) or "(none)"
        req = CompletionRequest(
            model=self.model,
            system=_PLAN_SYSTEM,
            messages=[
                Message(
                    role="user",
                    content=f"Available tools: {tools}\n\nRequest:\n{user_text}",
                )
            ],
            max_tokens=512,
        )
        resp = await self.provider.complete(req)
        return parse_steps(resp.text)

    async def replan(
        self, user_text: str, failed_step: str, error: str, tool_names: list[str]
    ) -> list[str]:
        tools = ", ".join(tool_names) or "(none)"
        req = CompletionRequest(
            model=self.model,
            system=_PLAN_SYSTEM,
            messages=[
                Message(
                    role="user",
                    content=(
                        f"Available tools: {tools}\n\nOriginal request:\n{user_text}\n\n"
                        f"The step '{failed_step}' failed with: {error}\n"
                        f"Produce a revised numbered plan."
                    ),
                )
            ],
            max_tokens=512,
        )
        resp = await self.provider.complete(req)
        return parse_steps(resp.text)


def parse_steps(text: str) -> list[str]:
    steps: list[str] = []
    for line in text.splitlines():
        m = _STEP_RE.match(line)
        if m:
            steps.append(m.group(1))
    if not steps:
        stripped = text.strip()
        return [stripped] if stripped else []
    return steps
