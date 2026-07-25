"""``humon doctor`` — validate config, secrets, DB schema, and connectivity
before the service starts (FR-1.5).

Each check returns a :class:`Check`. Doctor never raises for a *failed* check —
it collects them all so an operator sees every problem at once.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .config import Config, load_config
from .core.errors import HumonError
from .core.registry import (
    discover_capabilities,
    discover_channels,
    discover_providers,
    discover_tools,
)
from .state.db import Database


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


async def run_doctor(config_path: str) -> list[Check]:
    checks: list[Check] = []

    # 1. Config + secret scan.
    try:
        config = load_config(config_path)
        checks.append(Check("config", True, "valid and free of inline secrets"))
    except HumonError as exc:
        checks.append(Check("config", False, str(exc)))
        return checks  # nothing else is meaningful without a config

    # 2. Provider.
    checks.append(_check_provider(config))

    # 3. Channels.
    checks.extend(_check_channels(config))

    # 4. Tools.
    checks.append(_check_tools(config))

    # 5. Capabilities.
    checks.append(_check_capabilities(config))

    # 6. Database schema.
    checks.append(await _check_db(config))

    return checks


def _check_provider(config: Config) -> Check:
    providers = discover_providers()
    name = config.provider.name
    cls = providers.get(name)
    if cls is None or cls.__class__.__name__ == "_BrokenPlugin":
        return Check("provider", False, f"provider '{name}' not installed")
    key_env = config.provider.api_key_env
    if key_env and not os.environ.get(key_env):
        return Check("provider", False, f"env var {key_env} is not set")
    return Check("provider", True, f"'{name}' available, credentials present")


def _check_channels(config: Config) -> list[Check]:
    out: list[Check] = []
    available = discover_channels()
    enabled = config.enabled_channels()
    slack = config.channels.slack
    if slack.enabled:
        if "slack" not in available:
            out.append(Check("channel:slack", False, "slack-bolt not installed"))
        elif not os.environ.get(slack.bot_token_env) or not os.environ.get(slack.app_token_env):
            out.append(
                Check(
                    "channel:slack",
                    False,
                    f"set {slack.bot_token_env} and {slack.app_token_env}",
                )
            )
        else:
            out.append(Check("channel:slack", True, "tokens present"))
    # Third-party channels: verify they are installed (their own start() validates
    # credentials at runtime).
    for name in enabled:
        if name == "slack":
            continue
        installed = name in available and available[name].__class__.__name__ != "_BrokenPlugin"
        out.append(
            Check(
                f"channel:{name}",
                installed,
                "installed" if installed else "enabled but plugin not installed",
            )
        )
    if not out:
        out.append(Check("channels", False, "no channels enabled"))
    return out


def _check_capabilities(config: Config) -> Check:
    available = discover_capabilities()
    enabled = config.enabled_capabilities()
    missing = [
        c
        for c in enabled
        if c not in available or available[c].__class__.__name__ == "_BrokenPlugin"
    ]
    if missing:
        return Check("capabilities", False, f"enabled but not installed: {', '.join(missing)}")
    return Check("capabilities", True, f"{len(enabled)} enabled: {', '.join(enabled) or '(none)'}")


def _check_tools(config: Config) -> Check:
    available = discover_tools()
    enabled = config.enabled_tools()
    missing = [t for t in enabled if t not in available]
    if missing:
        return Check("tools", False, f"enabled but not installed: {', '.join(missing)}")
    return Check("tools", True, f"{len(enabled)} enabled: {', '.join(enabled) or '(none)'}")


async def _check_db(config: Config) -> Check:
    db = Database(config.state.db_path)
    try:
        await db.connect()
        row = await db.fetchone("SELECT COUNT(*) AS n FROM schema_migrations")
        await db.close()
        return Check("database", True, f"schema ok ({row['n'] if row else 0} migrations)")
    except Exception as exc:
        return Check("database", False, str(exc))
