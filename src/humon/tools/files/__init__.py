"""``files`` tool (FR-4.2).

Read/write/list/delete strictly within configured jail paths. Every path is
canonicalized with symlinks resolved (``Path.resolve``) and then checked to be
inside a jail — so a symlink *inside* the jail that points outside is rejected,
not followed. Writes and deletes declare ``fs.write`` / ``fs.delete`` so the
policy engine gates them behind approval by default; reads/lists need only
``fs.read``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.interfaces import ToolContext, ToolResult
from .._util import err, ok, truncate


class FilesTool:
    name = "files"
    description = (
        "Read, write, list, or delete files within Humon's configured jail paths. "
        "Writes and deletes require operator approval."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["read", "write", "list", "delete"]},
            "path": {"type": "string", "description": "Absolute path inside a jail."},
            "content": {"type": "string", "description": "Content to write (write only)."},
        },
        "required": ["operation", "path"],
        "additionalProperties": False,
    }
    permissions = ["fs.read"]

    def permissions_for(self, args: dict[str, Any]) -> list[str]:
        op = str(args.get("operation", "")).lower()
        if op == "write":
            return ["fs.write"]
        if op == "delete":
            return ["fs.delete"]
        return ["fs.read"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        op = str(args.get("operation", "")).lower()
        raw_path = str(args.get("path", ""))
        jails = ctx.jail_paths
        if not jails:
            return err("No jail paths are configured for the files tool; nothing is accessible.")
        if op not in {"read", "write", "list", "delete"}:
            return err(f"Unknown operation: {op!r}")

        try:
            target = _resolve_in_jail(raw_path, jails)
        except _JailEscape as exc:
            ctx.logger.warning(
                "files.jail_escape", session=ctx.session_id, path=raw_path, reason=str(exc)
            )
            return err(f"Path is outside the allowed jail: {exc}")

        max_read = int(ctx.config.get("max_read_bytes", 1024 * 1024))
        try:
            if op == "read":
                return self._read(target, max_read)
            if op == "list":
                return self._list(target)
            if op == "write":
                return self._write(target, str(args.get("content", "")))
            if op == "delete":
                return self._delete(target)
        except OSError as exc:
            return err(f"Filesystem error: {exc}")
        return err("unreachable")

    # ── operations ────────────────────────────────────────────────────────────
    def _read(self, target: Path, max_read: int) -> ToolResult:
        if not target.is_file():
            return err(f"Not a file: {target}")
        data = target.read_bytes()[: max_read + 1]
        text, truncated = truncate(data.decode("utf-8", "replace"), max_read)
        if truncated:
            text += "\n… [truncated]"
        return ok(text)

    def _list(self, target: Path) -> ToolResult:
        if not target.is_dir():
            return err(f"Not a directory: {target}")
        entries = sorted(f"{p.name}{'/' if p.is_dir() else ''}" for p in target.iterdir())
        return ok("\n".join(entries) if entries else "(empty)")

    def _write(self, target: Path, content: str) -> ToolResult:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ok(f"Wrote {len(content)} bytes to {target}")

    def _delete(self, target: Path) -> ToolResult:
        if target.is_dir():
            return err("Refusing to delete a directory; delete files individually.")
        if not target.exists():
            return err(f"No such file: {target}")
        target.unlink()
        return ok(f"Deleted {target}")


class _JailEscape(Exception):
    pass


def _resolve_in_jail(raw_path: str, jails: list[str]) -> Path:
    """Resolve ``raw_path`` (following symlinks) and require it inside a jail."""

    resolved_jails = [Path(j).resolve() for j in jails]
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = resolved_jails[0] / candidate
    resolved = candidate.resolve()  # follows symlinks in existing components
    for jail in resolved_jails:
        if resolved == jail or jail in resolved.parents:
            return resolved
    raise _JailEscape(f"{resolved} is not within any of {[str(j) for j in resolved_jails]}")
