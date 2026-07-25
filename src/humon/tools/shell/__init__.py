"""``shell`` tool (FR-4.1).

Deny-by-default: a command's binary must be in the configured allowlist. Shell
metacharacters are rejected unless explicitly enabled, and even then the default
path uses ``exec`` (no shell) so ``rm foo; curl evil`` can never smuggle a second
command past the allowlist. Output is size-capped and truncated with a notice.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any

from ...core.interfaces import ToolContext, ToolResult
from .._util import err, ok, truncate

# Characters that let one command become several / reach the network / files.
_METACHARS = set("|&;<>`$(){}[]*?!\n\\\"'")

_DEFAULT_TIMEOUT = 10
_DEFAULT_MAX_OUTPUT = 32 * 1024


class ShellTool:
    name = "shell"
    description = (
        "Run a single allowlisted, read-only shell command on the host and return "
        "its combined stdout/stderr. Only pre-approved binaries are permitted."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command line to run, e.g. 'df -h'.",
            }
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    permissions = ["shell.exec"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = str(args.get("command", "")).strip()
        if not command:
            return err("No command provided.")

        cfg = ctx.config
        allowed: list[str] = cfg.get("allowed_binaries", [])
        allow_meta: bool = bool(cfg.get("allow_shell_metachars", False))
        timeout: int = int(cfg.get("timeout_s", _DEFAULT_TIMEOUT))
        max_output: int = int(cfg.get("max_output_bytes", _DEFAULT_MAX_OUTPUT))

        if not allow_meta and (_METACHARS & set(command)):
            return err(
                "Command contains shell metacharacters, which are disabled. "
                "Run a single simple command."
            )

        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return err(f"Could not parse command: {exc}")
        if not tokens:
            return err("Empty command.")

        binary = os.path.basename(tokens[0])
        if binary not in allowed:
            return err(
                f"Binary '{binary}' is not in the allowlist. "
                f"Allowed: {', '.join(sorted(allowed)) or '(none)'}"
            )

        ctx.logger.info("shell.exec", session=ctx.session_id, binary=binary)
        try:
            output, exit_code = await self._run(tokens, allow_meta, command, timeout)
        except TimeoutError:
            return err(f"Command timed out after {timeout}s.")
        except FileNotFoundError:
            return err(f"Binary '{binary}' not found on PATH.")
        except Exception as exc:
            return err(f"Command failed to start: {exc}")

        body, truncated = truncate(output, max_output)
        if truncated:
            body += f"\n… [output truncated to {max_output} bytes]"
        status = "" if exit_code == 0 else f"\n[exit code {exit_code}]"
        return ok(body + status)

    async def _run(
        self, tokens: list[str], allow_meta: bool, raw: str, timeout_s: int
    ) -> tuple[str, int]:
        if allow_meta:
            proc = await asyncio.create_subprocess_shell(
                raw,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *tokens,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return stdout.decode("utf-8", "replace"), proc.returncode or 0
