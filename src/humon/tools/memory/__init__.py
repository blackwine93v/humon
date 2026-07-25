"""``memory`` tool (FR-4.6) — agent-invocable long-term notes.

Stores and recalls durable facts via the ``MemoryStore`` handed to it on the
``ToolContext`` (so it needs no access to ``core`` internals or ``state``).
Reads use ``memory.read``; writes/forgets use ``memory.write``.
"""

from __future__ import annotations

from typing import Any

from ...core.interfaces import ToolContext, ToolResult
from .._util import err, ok


class MemoryTool:
    name = "memory"
    description = (
        "Store or recall long-term notes and facts. Use 'remember' to save a fact, "
        "'recall' to retrieve relevant facts, 'list' to see all, 'forget' to delete."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["remember", "recall", "list", "forget"]},
            "text": {"type": "string", "description": "Fact to remember, or query to recall."},
            "id": {"type": "integer", "description": "Note id to forget."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    permissions = ["memory.read"]

    def permissions_for(self, args: dict[str, Any]) -> list[str]:
        action = str(args.get("action", "")).lower()
        return ["memory.write"] if action in {"remember", "forget"} else ["memory.read"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.memory is None:
            return err("Memory is not available (enable the memory feature in config).")
        action = str(args.get("action", "")).lower()

        if action == "remember":
            text = str(args.get("text", "")).strip()
            if not text:
                return err("Provide 'text' to remember.")
            note_id = await ctx.memory.store(text, kind="note", session_id=ctx.session_id)
            return ok(f"Remembered as note {note_id}.")

        if action == "recall":
            query = str(args.get("text", "")).strip()
            if not query:
                return err("Provide 'text' as the recall query.")
            hits = await ctx.memory.search(query, k=5)
            if not hits:
                return ok("No relevant memories found.")
            return ok("\n".join(f"- {h}" for h in hits))

        if action == "list":
            notes = await ctx.memory.list_notes()
            if not notes:
                return ok("Memory is empty.")
            return ok("\n".join(f"[{i}] ({kind}) {text}" for i, kind, text in notes[:50]))

        if action == "forget":
            note_id = args.get("id")
            if not isinstance(note_id, int):
                return err("Provide an integer 'id' to forget.")
            done = await ctx.memory.forget(note_id)
            return ok(f"Forgot note {note_id}." if done else f"No note with id {note_id}.")

        return err(f"Unknown action: {action!r}")
