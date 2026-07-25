"""OpenAI-compatible provider (works with OpenAI and API-compatible servers).

Declares tools, streaming, and embeddings. The ``openai`` SDK is imported lazily.
``base_url`` lets you point at any OpenAI-compatible endpoint.
"""

from __future__ import annotations

import asyncio
import json
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


def _to_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]
            out.append(msg)
        else:
            out.append({"role": "user", "content": m.content})
    return out


class OpenAIProvider:
    name = "openai"
    capabilities = {"tools", "streaming", "embeddings"}

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._embedding_model = (config.get("models") or {}).get(
            "embedding"
        ) or "text-embedding-3-small"
        api_key = os.environ.get(config.get("api_key_env") or "OPENAI_API_KEY")
        if not api_key:
            raise ProviderError("OpenAI API key not found in the configured environment variable")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(
                "openai package not installed. Install with: pip install 'humon[openai]'"
            ) from exc
        kwargs: dict[str, Any] = {"api_key": api_key}
        if config.get("base_url"):
            kwargs["base_url"] = config["base_url"]
        self._client = AsyncOpenAI(**kwargs)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": _to_messages(req.system, req.messages),
            "max_tokens": req.max_tokens,
        }
        if req.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in req.tools
            ]
        if req.temperature is not None:
            payload["temperature"] = req.temperature

        resp = await self._call_with_retries(payload)
        choice = resp.choices[0]
        tool_calls: list[ToolCall] = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        usage = getattr(resp, "usage", None)
        return CompletionResponse(
            text=choice.message.content or "",
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            usage=Usage(
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
            ),
            raw=resp,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.embeddings.create(model=self._embedding_model, input=texts)
        return [d.embedding for d in resp.data]

    async def _call_with_retries(self, payload: dict[str, Any]) -> Any:
        last: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return await self._client.chat.completions.create(**payload)
            except Exception as exc:
                last = exc
                if attempt == _MAX_RETRIES - 1:
                    break
                await asyncio.sleep((2**attempt) + random.uniform(0, 1))  # noqa: S311
        raise ProviderError(f"OpenAI completion failed after retries: {last}") from last
