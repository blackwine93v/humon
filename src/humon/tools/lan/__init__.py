"""``lan`` tool (FR-4.3).

Ping, TCP port check, and HTTP GET — but only against hosts that resolve into the
configured CIDRs (RFC1918 by default). A hostname is resolved first and *every*
resolved address must fall inside an allowed CIDR, so the tool can never be used
to reach the public internet. All actions are read-only (``net.read``).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
import urllib.parse
import urllib.request
from typing import Any

from ...core.interfaces import ToolContext, ToolResult
from .._util import err, ok, truncate

_DEFAULT_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
_MAX_HTTP_BYTES = 64 * 1024


class LanTool:
    name = "lan"
    description = (
        "Probe hosts on the local network: 'ping' a host, 'tcp' check a host:port, "
        "or 'http_get' a URL. Only private (RFC1918/configured) addresses are allowed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["ping", "tcp", "http_get"]},
            "host": {"type": "string", "description": "Host/IP (ping, tcp)."},
            "port": {"type": "integer", "description": "TCP port (tcp)."},
            "url": {"type": "string", "description": "http:// URL (http_get)."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    permissions = ["net.read"]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        action = str(args.get("action", "")).lower()
        cidrs = ctx.config.get("allowed_cidrs", _DEFAULT_CIDRS)
        timeout = float(ctx.config.get("timeout_s", 5))

        if action in {"ping", "tcp"}:
            host = str(args.get("host", ""))
            if not host:
                return err("Provide a 'host'.")
            try:
                ip = _resolve_private(host, cidrs)
            except _NotPrivate as exc:
                return err(str(exc))
            if action == "ping":
                return await _ping(host, ip, timeout)
            port = args.get("port")
            if not isinstance(port, int):
                return err("Provide an integer 'port' for a tcp check.")
            return await _tcp(ip, port, timeout)

        if action == "http_get":
            url = str(args.get("url", ""))
            return await _http_get(url, cidrs, timeout)

        return err(f"Unknown action: {action!r}")


class _NotPrivate(Exception):
    pass


def _resolve_private(host: str, cidrs: list[str]) -> str:
    nets = [ipaddress.ip_network(c) for c in cidrs]
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise _NotPrivate(f"Could not resolve host '{host}': {exc}") from exc
    addrs = {info[4][0] for info in infos}
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if not any(ip in net for net in nets):
            raise _NotPrivate(
                f"Host '{host}' resolves to {addr}, outside the allowed private ranges."
            )
    # Return the first resolved address.
    return next(iter(addrs))


async def _ping(host: str, ip: str, timeout_s: float) -> ToolResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            "1",
            "-W",
            str(int(timeout_s) or 1),
            ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return err("ping is not available on this host.")
    await proc.communicate()
    up = proc.returncode == 0
    return ok(f"{host} ({ip}) is {'UP' if up else 'DOWN'}.")


async def _tcp(ip: str, port: int, timeout_s: float) -> ToolResult:
    try:
        fut = asyncio.open_connection(ip, port)
        _reader, writer = await asyncio.wait_for(fut, timeout=timeout_s)
        writer.close()
        with contextlib.suppress(Exception):  # closing errors don't affect reachability
            await writer.wait_closed()
        return ok(f"{ip}:{port} is OPEN.")
    except (TimeoutError, OSError):
        return ok(f"{ip}:{port} is CLOSED/unreachable.")


async def _http_get(url: str, cidrs: list[str], timeout_s: float) -> ToolResult:
    if not url.lower().startswith(("http://", "https://")):
        return err("URL must start with http:// or https://")
    host = urllib.parse.urlparse(url).hostname or ""
    try:
        _resolve_private(host, cidrs)
    except _NotPrivate as exc:
        return err(str(exc))

    def _fetch() -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "humon-lan/0.1"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            body = resp.read(_MAX_HTTP_BYTES + 1)
            status = resp.status
        text, truncated = truncate(body.decode("utf-8", "replace"), _MAX_HTTP_BYTES)
        suffix = "\n… [truncated]" if truncated else ""
        return f"HTTP {status}\n{text}{suffix}"

    try:
        return ok(await asyncio.to_thread(_fetch))
    except Exception as exc:  # network errors surface to the model as a tool error
        return err(f"HTTP GET failed: {exc}")
