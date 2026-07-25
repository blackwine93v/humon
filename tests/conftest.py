"""Shared test fixtures. Everything runs offline — no network, no real provider."""

from __future__ import annotations

from typing import Any

import pytest

from humon.config import Config, parse_config
from humon.logging import StructuredLogger, get_logger
from humon.state.db import Database
from humon.state.repositories import AuditRepo, MemoryRepo, SessionRepo, TaskRepo


def make_logger() -> StructuredLogger:
    """A production-shaped structured logger (accepts kwargs) for tool tests."""

    return get_logger("humon.test")


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


async def build_memory(db: Database, provider: Any) -> Any:
    """Construct a wired MemoryManager for tests (vector path if sqlite-vec present)."""

    from humon.config import MemoryConfig
    from humon.core.memory import MemoryManager
    from humon.state.repositories import MemoryRepo, SessionRepo
    from humon.state.vectors import VectorIndex

    vectors = VectorIndex(db)
    await vectors.setup()
    return MemoryManager(
        memory_repo=MemoryRepo(db),
        session_repo=SessionRepo(db),
        vectors=vectors,
        provider=provider,
        config=MemoryConfig(),
    )


@pytest.fixture
def repos(db: Database) -> dict[str, Any]:
    return {
        "sessions": SessionRepo(db),
        "audit": AuditRepo(db),
        "memory": MemoryRepo(db),
        "tasks": TaskRepo(db),
    }
