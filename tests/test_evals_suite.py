"""Run the eval suite (FakeProvider) as part of pytest, so behavioral graders
gate every change — not just the dedicated CI eval job.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from evals.harness import run_task_fake
from evals.tasks import TASKS


def test_suite_has_at_least_20_tasks():
    assert len(TASKS) >= 20


@pytest.mark.asyncio
@pytest.mark.parametrize("task", TASKS, ids=[t.id for t in TASKS])
async def test_eval_task_passes(task):
    with tempfile.TemporaryDirectory(prefix="humon-eval-") as d:
        result = await run_task_fake(task, Path(d))
    assert result.passed, result.detail
