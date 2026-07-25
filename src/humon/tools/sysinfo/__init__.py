"""``sysinfo`` tool (FR-4.5) — read-only host inspection.

CPU / memory / disk plus systemd unit status and a line-capped journal tail. Uses
``psutil`` when installed for richer numbers, otherwise falls back to stdlib and
``/proc`` so it works with no extra dependency. Everything here is read-only and
declares ``sys.read``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from ...core.interfaces import ToolContext, ToolResult
from .._util import err, ok, truncate


class SysinfoTool:
    name = "sysinfo"
    description = (
        "Read-only host inspection: CPU load, memory, disk usage, systemd unit "
        "status, and a capped journal tail."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["overview", "cpu", "memory", "disk", "service", "journal"],
            },
            "unit": {"type": "string", "description": "systemd unit (service/journal)."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    permissions = ["sys.read"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        action = str(args.get("action", "overview")).lower()
        if action == "overview":
            return ok("\n".join([_cpu(), _memory(), _disk()]))
        if action == "cpu":
            return ok(_cpu())
        if action == "memory":
            return ok(_memory())
        if action == "disk":
            return ok(_disk())
        if action == "service":
            return await _service(str(args.get("unit", "")))
        if action == "journal":
            lines = int(ctx.config.get("journal_max_lines", 200))
            return await _journal(str(args.get("unit", "")), lines)
        return err(f"Unknown action: {action!r}")


def _cpu() -> str:
    try:
        load1, load5, load15 = os.getloadavg()
        cores = os.cpu_count() or 1
        return f"CPU: load {load1:.2f}/{load5:.2f}/{load15:.2f} over {cores} core(s)"
    except OSError:
        return "CPU: load average unavailable"


def _memory() -> str:
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                info[key] = int(rest.strip().split()[0])  # kB
        total = info.get("MemTotal", 0) // 1024
        avail = info.get("MemAvailable", 0) // 1024
        used = total - avail
        return f"Memory: {used} MB used / {total} MB total ({avail} MB available)"
    except (OSError, ValueError, IndexError):
        return "Memory: unavailable"


def _disk() -> str:
    usage = shutil.disk_usage("/")
    gb = 1024**3
    return (
        f"Disk (/): {usage.used // gb} GB used / {usage.total // gb} GB total "
        f"({usage.free // gb} GB free)"
    )


async def _service(unit: str) -> ToolResult:
    if not unit:
        return err("Provide a 'unit' name.")
    code, out = await _run(["systemctl", "is-active", unit])
    _, status = await _run(["systemctl", "status", "--no-pager", "--lines=0", unit])
    if code < 0:
        return err("systemctl not available on this host.")
    body, _ = truncate(status or out, 4096)
    return ok(f"{unit}: {out.strip() or 'unknown'}\n{body}")


async def _journal(unit: str, lines: int) -> ToolResult:
    cmd = ["journalctl", "--no-pager", f"--lines={lines}"]
    if unit:
        cmd += ["-u", unit]
    code, out = await _run(cmd)
    if code < 0:
        return err("journalctl not available on this host.")
    body, truncated = truncate(out, 16384)
    if truncated:
        body += "\n… [truncated]"
    return ok(body or "(no journal output)")


async def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return -1, ""
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -2, "(timed out)"
    return proc.returncode or 0, stdout.decode("utf-8", "replace")
