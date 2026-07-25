"""Shared test fixtures. Everything runs offline — no network, no real provider."""

from __future__ import annotations

from typing import Any

import pytest

from humon.config import Config, parse_config
from humon.state.db import Database
from humon.state.repositories import AuditRepo, MemoryRepo, SessionRepo, TaskRepo


def make_config(**overrides: Any) -> Config:
    """Build a valid Config for tests, with shell enabled for a couple of binaries."""

    base: dict[str, Any] = {
        "provider": {"name": "fake"},
        "models": {"default": "fake-model", "cheap": "fake-cheap"},
        "channels": {"slack": {"enabled": False}},
        "tools": {
            "shell": {
                "enabled": True,
                "allowed_binaries": ["echo", "df", "true", "false"],
                "allow_shell_metachars": False,
                "timeout_s": 5,
                "max_output_bytes": 1024,
            }
        },
        "policy": {
            "default_decision": "deny",
            "approval": {"timeout_s": 1},
            "rules": {
                "shell.exec": "allow",
                "fs.read": "allow",
                "fs.write": "require_approval",
                "fs.delete": "require_approval",
                "sys.read": "allow",
                "memory.read": "allow",
                "memory.write": "allow",
            },
        },
        "limits": {"max_iterations": 6, "max_tool_calls": 8, "task_timeout_s": 10},
        "state": {"db_path": ":memory:"},
        "logging": {"level": "WARNING", "format": "text"},
    }
    base.update(overrides)
    return parse_config(base)


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def repos(db: Database) -> dict[str, Any]:
    return {
        "sessions": SessionRepo(db),
        "audit": AuditRepo(db),
        "memory": MemoryRepo(db),
        "tasks": TaskRepo(db),
    }
