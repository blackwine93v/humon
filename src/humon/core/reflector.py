"""Reflector (FR-3.4): a critic pass over the draft answer.

Runs (by default) only for tasks that used ≥3 tool calls — the ones most likely
to have drifted. Given the original request and the draft, the critic returns an
improved final answer (or the draft unchanged if it's already good).
"""

from __future__ import annotations

from .interfaces import CompletionRequest, LLMProvider, Message

_REFLECT_SYSTEM = """\
You are a critical reviewer. You are given a user's original request and a draft
answer produced by an agent that used tools. Check that the draft:
- actually answers the request,
- is consistent with what the tools returned (do not invent new facts),
- is concise and free of leftover internal reasoning.
Return the final answer to send to the user. If the draft is already good, return
it unchanged. Output ONLY the final answer text.
"""


class LLMReflector:
    def __init__(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model

    async def review(self, request: str, draft: str, transcript: list[Message]) -> str:
        req = CompletionRequest(
            model=self.model,
            system=_REFLECT_SYSTEM,
            messages=[
                Message(
                    role="user",
                    content=(
                        f"Original request:\n{request}\n\nDraft answer:\n{draft}\n\n"
                        f"Return the improved final answer."
                    ),
                )
            ],
            max_tokens=1024,
        )
        try:
            resp = await self.provider.complete(req)
        except Exception:  # reflection is best-effort; on any error keep the draft
            return draft
        return resp.text.strip() or draft
