"""Planner (FR-3.2): step parsing and plan generation via FakeProvider."""

from __future__ import annotations

import pytest

from humon.core.planner import LLMPlanner, parse_steps
from humon.providers.fake import FakeProvider, text_response


def test_parse_numbered_steps():
    steps = parse_steps("1. Check disk\n2. Report result\n3. Notify user")
    assert steps == ["Check disk", "Report result", "Notify user"]


def test_parse_bulleted_steps():
    assert parse_steps("- do a\n* do b") == ["do a", "do b"]


def test_parse_single_line_returns_one_step():
    assert parse_steps("Just answer the question") == ["Just answer the question"]


def test_parse_empty():
    assert parse_steps("   ") == []


@pytest.mark.asyncio
async def test_planner_generates_plan():
    provider = FakeProvider([text_response("1. probe host\n2. summarize\n3. reply")])
    planner = LLMPlanner(provider, "strong-model")
    steps = await planner.plan("check the NAS and tell me", ["lan", "shell"])
    assert len(steps) == 3
    # planner used the strong model
    assert provider.requests[0].model == "strong-model"


@pytest.mark.asyncio
async def test_planner_replan():
    provider = FakeProvider([text_response("1. retry with sudo\n2. verify")])
    planner = LLMPlanner(provider, "strong-model")
    steps = await planner.replan("do X", "step 1", "permission denied", ["shell"])
    assert steps == ["retry with sudo", "verify"]
