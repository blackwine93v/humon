"""Provider message normalization and missing-dependency handling."""

from __future__ import annotations

import pytest

from humon.core.errors import ProviderError
from humon.core.interfaces import Message, ToolCall
from humon.providers.fake import FakeProvider, _hash_embed
from humon.providers.ollama import _to_messages as ollama_msgs
from humon.providers.openai import _to_messages as openai_msgs


def test_openai_prepends_system_and_maps_tool_result():
    msgs = openai_msgs(
        "SYS",
        [
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", name="shell", arguments={"command": "df"})],
            ),
            Message(role="tool", content="output", tool_call_id="c1"),
        ],
    )
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1] == {"role": "user", "content": "hi"}
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "shell"
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "output"}


def test_ollama_maps_messages():
    msgs = ollama_msgs("SYS", [Message(role="user", content="hi")])
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "hi"}


def test_openai_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from humon.providers.openai import OpenAIProvider

    with pytest.raises(ProviderError):
        OpenAIProvider({"api_key_env": "OPENAI_API_KEY"})


def test_ollama_requires_httpx():
    # httpx isn't installed in the base test env → constructing raises clearly.
    from humon.providers.ollama import OllamaProvider

    with pytest.raises(ProviderError):
        OllamaProvider({})


@pytest.mark.asyncio
async def test_fake_provider_embeddings_are_deterministic():
    p = FakeProvider()
    a = await p.embed(["the nas is up"])
    b = await p.embed(["the nas is up"])
    assert a == b
    assert len(a[0]) == len(_hash_embed("x"))
