"""Reflector (FR-3.4): critic pass improves/keeps the draft."""

from __future__ import annotations

import pytest

from humon.core.reflector import LLMReflector
from humon.providers.fake import FakeProvider, text_response


@pytest.mark.asyncio
async def test_reflector_returns_reviewed_answer():
    provider = FakeProvider([text_response("Polished final answer.")])
    r = LLMReflector(provider, "strong")
    out = await r.review("do X", "rough draft", [])
    assert out == "Polished final answer."
    assert provider.requests[0].model == "strong"


@pytest.mark.asyncio
async def test_reflector_keeps_draft_on_empty_response():
    provider = FakeProvider([text_response("")])
    r = LLMReflector(provider, "strong")
    out = await r.review("do X", "the draft", [])
    assert out == "the draft"


@pytest.mark.asyncio
async def test_reflector_keeps_draft_on_error():
    class Boom(FakeProvider):
        async def complete(self, req):
            raise RuntimeError("provider down")

    out = await LLMReflector(Boom(), "strong").review("q", "draft", [])
    assert out == "draft"
