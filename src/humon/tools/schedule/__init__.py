"""``schedule`` tool (FR-4.4).

Create, list, and delete scheduled/recurring tasks. Creating or deleting a task
declares ``schedule.write`` (approval-gated by default) — approving a task at
creation is what pre-authorizes it to run later (OQ#2). Listing is ``schedule.read``.
Storage + execution live in the in-process scheduler, reached via ``ctx.tasks``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...core.interfaces import ToolContext, ToolResult
from .._util import err, ok


class ScheduleTool:
    name = "schedule"
    description = (
        "Create, list, or delete scheduled tasks. Schedule specs: 'once', "
        "'once:<iso8601>', 'every:<seconds>', or 'daily@HH:MM'. Example: run "
        "\"ping the NAS and report\" with schedule 'daily@08:00'."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "delete"]},
            "description": {"type": "string", "description": "What the task should do."},
            "schedule": {
                "type": "string",
                "description": "once | once:<iso> | every:<seconds> | daily@HH:MM",
            },
            "id": {"type": "integer", "description": "Task id to delete."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    permissions = ["schedule.read"]

    def permissions_for(self, args: dict[str, Any]) -> list[str]:
        action = str(args.get("action", "")).lower()
        return ["schedule.write"] if action in {"create", "delete"} else ["schedule.read"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.tasks is None:
            return err("Scheduling is not available (enable the schedule tool in config).")
        action = str(args.get("action", "")).lower()

        if action == "create":
            description = str(args.get("description", "")).strip()
            schedule = str(args.get("schedule", "")).strip()
            if not description or not schedule:
                return err("Provide both 'description' and 'schedule'.")
            task_id, next_run = await ctx.tasks.add_task(
                description, schedule, session_id=ctx.session_id
            )
            when = _fmt(next_run)
            return ok(f"Scheduled task {task_id} ({schedule}); next run {when}.")

        if action == "list":
            rows = await ctx.tasks.list_tasks()
            if not rows:
                return ok("No scheduled tasks.")
            return ok(
                "\n".join(
                    f"[{tid}] {schedule} — {desc} (next: {_fmt(nxt)})"
                    for tid, desc, schedule, nxt in rows
                )
            )

        if action == "delete":
            task_id = args.get("id")
            if not isinstance(task_id, int):
                return err("Provide an integer 'id' to delete.")
            done = await ctx.tasks.delete_task(task_id)
            return ok(f"Deleted task {task_id}." if done else f"No task with id {task_id}.")

        return err(f"Unknown action: {action!r}")


def _fmt(epoch: float | None) -> str:
    if epoch is None:
        return "never (disabled/one-shot done)"
    return datetime.fromtimestamp(epoch).isoformat(timespec="minutes")
