"""Small helpers shared across built-in tools."""

from __future__ import annotations

from ..core.interfaces import ToolResult


def truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``max_bytes`` (UTF-8). Returns (text, was_truncated)."""

    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= max_bytes:
        return text, False
    clipped = encoded[:max_bytes].decode("utf-8", "ignore")
    return clipped, True


def ok(content: str) -> ToolResult:
    return {"ok": True, "content": content, "error": None}


def err(message: str) -> ToolResult:
    return {"ok": False, "content": "", "error": message}
