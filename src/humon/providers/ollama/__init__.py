"""Ollama provider — run local models on capable hardware.

Talks to an Ollama server over HTTP (``httpx``). Supports tool use and embeddings
on models that provide them; degrades gracefully otherwise. On the reference
4GB host this is out of scope, but it lets Humon run fully offline on bigger boxes.
"""

from __future__ import annotations

from typing import Any

from ...core.errors import ProviderError
from ...core.interfaces import (
    CompletionRequest,
    CompletionResponse,
    Message,
    ToolCall,
    Usage,
)

_DEFAULT_BASE_URL = "http://localhost:11434"


def _to_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            out.append({"role": "tool", "content": m.content})
        elif m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {"function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in m.tool_calls
                ]
            out.append(msg)
        else:
            out.append({"role": "user", "content": m.content})
    return out


class OllamaProvider:
    name = "ollama"
    capabilities = {"tools", "embeddings"}

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._base_url = (config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        self._embedding_model = (config.get("models") or {}).get("embedding") or "nomic-embed-text"
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise ProviderError(
                "httpx not installed. Install with: pip install 'humon[ollama]'"
            ) from exc
        self._httpx = httpx

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": _to_messages(req.system, req.messages),
            "stream": False,
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
        try:
            async with self._httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{self._base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc

        msg = data.get("message", {})
        tool_calls: list[ToolCall] = []
        for i, tc in enumerate(msg.get("tool_calls", []) or []):
            fn = tc.get("function", {})
            tool_calls.append(
                ToolCall(id=f"call_{i}", name=fn.get("name", ""), arguments=fn.get("arguments", {}))
            )
        return CompletionResponse(
            text=msg.get("content", "") or "",
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            usage=Usage(
                data.get("prompt_eval_count", 0) or 0,
                data.get("eval_count", 0) or 0,
            ),
            raw=data,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with self._httpx.AsyncClient(timeout=120) as client:
            for text in texts:
                resp = await client.post(
                    f"{self._base_url}/api/embeddings",
                    json={"model": self._embedding_model, "prompt": text},
                )
                resp.raise_for_status()
                out.append(resp.json().get("embedding", []))
        return out
