"""FakeProvider — scripted, offline LLM for tests and evals (T-2/T-3).

Feed it a list of :class:`CompletionResponse` objects (or callables that build
one from the request). ``complete`` pops them in order, recording every request
so tests can assert on what the loop sent. Embeddings are deterministic hashes,
so semantic-memory tests are reproducible without a network.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable

from ..core.interfaces import (
    CompletionRequest,
    CompletionResponse,
    ToolCall,
    Usage,
)

Script = list["CompletionResponse | Callable[[CompletionRequest], CompletionResponse]"]


def text_response(text: str, stop_reason: str = "end_turn") -> CompletionResponse:
    return CompletionResponse(text=text, stop_reason=stop_reason, usage=Usage(10, 10))


def tool_response(name: str, arguments: dict, call_id: str = "call_1") -> CompletionResponse:
    return CompletionResponse(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        stop_reason="tool_use",
        usage=Usage(10, 10),
    )


def _hash_embed(text: str, dim: int = 64) -> list[float]:
    """Deterministic unit-norm pseudo-embedding derived from the text bytes."""

    vec = [0.0] * dim
    for token in text.lower().split():
        h = int(hashlib.sha256(token.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeProvider:
    name = "fake"
    capabilities = {"tools", "embeddings"}

    def __init__(self, responses: Script | None = None) -> None:
        self._responses: Script = list(responses or [])
        self.requests: list[CompletionRequest] = []

    def queue(self, response: CompletionResponse) -> None:
        self._responses.append(response)

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.requests.append(req)
        if not self._responses:
            return text_response("(fake provider: no scripted response left)")
        nxt = self._responses.pop(0)
        return nxt(req) if callable(nxt) else nxt

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_embed(t) for t in texts]
