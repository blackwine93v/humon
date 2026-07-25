"""Anthropic provider — native tool use + streaming.

The ``anthropic`` SDK is imported lazily so ``humon`` installs without it. Embed
is unsupported here (Anthropic has no embeddings endpoint); memory degrades
gracefully when a provider lacks the ``embeddings`` capability (FR-7.3).
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any

from ...core.errors import ProviderError
from ...core.interfaces import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ToolCall,
    Usage,
)

_MAX_RETRIES = 4


def _to_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue  # sent as a top-level param
        if m.role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id,
                            "content": m.content,
                        }
                    ],
                }
            )
        elif m.role == "assistant":
            content: list[dict[str, Any]] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            out.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
        else:
            out.append({"role": "user", "content": m.content})
    return out


class AnthropicProvider:
    name = "anthropic"
    capabilities = {"tools", "streaming"}

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        api_key = os.environ.get(config.get("api_key_env") or "ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderError(
                "Anthropic API key not found in the configured environment variable"
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - exercised only without extra
            raise ProviderError(
                "anthropic package not installed. Install with: pip install 'humon[anthropic]'"
            ) from exc
        kwargs: dict[str, Any] = {"api_key": api_key}
        if config.get("base_url"):
            kwargs["base_url"] = config["base_url"]
        self._client = AsyncAnthropic(**kwargs)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in req.tools
        ]
        payload: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": _to_messages(req.messages),
        }
        if tools:
            payload["tools"] = tools
        if req.temperature is not None:
            payload["temperature"] = req.temperature

        resp = await self._call_with_retries(payload)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )
        return CompletionResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason or "end_turn",
            usage=Usage(resp.usage.input_tokens, resp.usage.output_tokens),
            raw=resp,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise ProviderError(
            "AnthropicProvider does not support embeddings; configure a provider "
            "with the 'embeddings' capability or memory will use keyword fallback."
        )

    async def _call_with_retries(self, payload: dict[str, Any]) -> Any:
        last: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return await self._client.messages.create(**payload)
            except Exception as exc:
                last = exc
                if attempt == _MAX_RETRIES - 1:
                    break
                backoff = (2**attempt) + random.uniform(0, 1)  # noqa: S311 - jitter, not crypto
                await asyncio.sleep(backoff)
        raise ProviderError(f"Anthropic completion failed after retries: {last}") from last
