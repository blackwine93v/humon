"""Eval harness: build a fully-wired agent over a temp DB and run one task."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from humon.config import parse_config
from humon.core.agent import Agent, TaskOutcome
from humon.core.memory import MemoryManager
from humon.core.planner import LLMPlanner
from humon.core.policy import PolicyEngine
from humon.core.reflector import LLMReflector
from humon.core.scheduler import Scheduler
from humon.logging import get_logger
from humon.providers.fake import FakeProvider
from humon.state.db import Database
from humon.state.repositories import AuditRepo, MemoryRepo, SessionRepo, TaskRepo
from humon.state.vectors import VectorIndex

# Grader receives the context and returns (passed, detail).
Grader = Callable[["EvalContext"], "tuple[bool, str]"]


@dataclass
class EvalTask:
    id: str
    prompt: str
    script: list[Any]  # FakeProvider response queue (fake mode)
    grader: Grader
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    policy_rules: dict[str, str] = field(default_factory=dict)
    approvals: list[bool] = field(default_factory=list)
    setup: Callable[[Path], None] | None = None
    planning: bool = False
    reflection: bool = False


@dataclass
class EvalContext:
    outcome: TaskOutcome
    audit: list[dict[str, Any]]
    approval_prompts: list[str]
    progress_notes: list[str]
    workdir: Path
    memory: MemoryManager


@dataclass
class EvalResult:
    id: str
    passed: bool
    detail: str


def _build_tools(names: dict[str, dict[str, Any]]) -> dict[str, Any]:
    from humon.tools.files import FilesTool
    from humon.tools.lan import LanTool
    from humon.tools.memory import MemoryTool
    from humon.tools.schedule import ScheduleTool
    from humon.tools.shell import ShellTool
    from humon.tools.sysinfo import SysinfoTool

    registry = {
        "shell": ShellTool,
        "files": FilesTool,
        "sysinfo": SysinfoTool,
        "lan": LanTool,
        "schedule": ScheduleTool,
        "memory": MemoryTool,
    }
    return {name: registry[name]() for name in names if name in registry}


def _sub_workdir(value: Any, workdir: Path) -> Any:
    """Replace the ``$WORKDIR`` sentinel with the task's temp directory."""

    if isinstance(value, str):
        return value.replace("$WORKDIR", str(workdir))
    if isinstance(value, list):
        return [_sub_workdir(v, workdir) for v in value]
    if isinstance(value, dict):
        return {k: _sub_workdir(v, workdir) for k, v in value.items()}
    return value


async def run_task_fake(task: EvalTask, workdir: Path) -> EvalResult:
    if task.setup:
        task.setup(workdir)

    tools_cfg = {
        name: {"enabled": True, **_sub_workdir(cfg, workdir)} for name, cfg in task.tools.items()
    }
    config = parse_config(
        {
            "provider": {"name": "fake"},
            "models": {"default": "fake", "strong": "fake", "cheap": "fake"},
            "channels": {"slack": {"enabled": False}},
            "tools": tools_cfg,
            "policy": {
                "default_decision": "deny",
                "approval": {"timeout_s": 1},
                "rules": task.policy_rules,
            },
            "limits": {"max_iterations": 8, "max_tool_calls": 6, "task_timeout_s": 15},
            "agent": {"planning": task.planning, "reflection": task.reflection},
            "state": {"db_path": ":memory:"},
            "logging": {"level": "ERROR", "format": "text"},
        }
    )

    db = Database(":memory:")
    await db.connect()
    try:
        # Substitute $WORKDIR inside scripted tool-call arguments too (the model's
        # "output" references the temp jail).
        import copy

        script = []
        for resp in task.script:
            resp2 = copy.deepcopy(resp)
            for tc in getattr(resp2, "tool_calls", None) or []:
                tc.arguments = _sub_workdir(tc.arguments, workdir)
            script.append(resp2)
        provider = FakeProvider(script)
        vectors = VectorIndex(db)
        await vectors.setup()
        memory = MemoryManager(
            memory_repo=MemoryRepo(db),
            session_repo=SessionRepo(db),
            vectors=vectors,
            provider=provider,
            config=config.memory,
        )
        scheduler = Scheduler(TaskRepo(db), _noop_on_due, get_logger("eval.sched"))
        agent = Agent(
            provider=provider,
            tools=_build_tools(task.tools),
            policy=PolicyEngine(config.policy),
            config=config,
            session_repo=SessionRepo(db),
            audit=AuditRepo(db),
            logger=get_logger("eval.agent"),
            planner=LLMPlanner(provider, "fake") if task.planning else None,
            reflector=LLMReflector(provider, "fake") if task.reflection else None,
            memory=memory,
            tasks=scheduler,
        )
        await SessionRepo(db).ensure("eval", "fake", "u")

        approvals = list(task.approvals)
        prompts: list[str] = []
        notes: list[str] = []

        async def approve(summary: str) -> bool:
            prompts.append(summary)
            return approvals.pop(0) if approvals else False

        async def progress(note: str) -> None:
            notes.append(note)

        outcome = await agent.run_task(
            session_id="eval",
            user_text=task.prompt,
            request_approval=approve,
            progress=progress,
        )
        ctx = EvalContext(
            outcome=outcome,
            audit=await AuditRepo(db).recent(50),
            approval_prompts=prompts,
            progress_notes=notes,
            workdir=workdir,
            memory=memory,
        )
        try:
            passed, detail = task.grader(ctx)
        except Exception as exc:
            passed, detail = False, f"grader raised: {exc}"
        return EvalResult(task.id, passed, detail)
    finally:
        await db.close()


async def _noop_on_due(_row: dict[str, Any]) -> None:
    return None
