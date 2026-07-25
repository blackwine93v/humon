"""Configuration models, loader, and the secret-rejection heuristic.

A single ``config.yaml`` is validated into the :class:`Config` model (FR-8.1).
Secrets never live in config — only the *names* of environment variables do
(FR-6.5). :func:`load_config` scans the raw document and refuses to load if it
finds anything that looks like a real credential, so a misconfigured deployment
fails loudly at ``humon doctor`` time rather than leaking a token into a repo.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .core.errors import ConfigError
from .core.interfaces import PolicyDecision

# ─────────────────────────────────────────────────────────────────────────────
# Secret heuristic (FR-6.5)
# ─────────────────────────────────────────────────────────────────────────────

# Leaf keys whose *name* implies a secret. A key ending in ``_env`` is exempt:
# it holds the NAME of an environment variable, not the secret itself.
_SECRET_KEY_HINTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "api_key",
    "private_key",
    "credential",
)

# Values that begin like a well-known credential are always rejected, regardless
# of the key they sit under.
_SECRET_VALUE_PREFIXES = (
    "xoxb-",
    "xoxp-",
    "xapp-",
    "sk-",
    "sk_live_",
    "ghp_",
    "gho_",
    "github_pat_",
    "AKIA",
    "AIza",
)

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _iter_leaves(node: Any, path: str = "") -> list[tuple[str, str, Any]]:
    """Yield ``(path, leaf_key, value)`` for every scalar leaf in a nested dict."""

    out: list[tuple[str, str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            out.extend(_iter_leaves(value, child_path))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            out.extend(_iter_leaves(item, f"{path}[{i}]"))
    else:
        leaf_key = path.split(".")[-1].split("[")[0]
        out.append((path, leaf_key, node))
    return out


def reject_secrets(raw: dict[str, Any]) -> None:
    """Raise :class:`ConfigError` if the raw config appears to embed a secret."""

    violations: list[str] = []
    for path, leaf_key, value in _iter_leaves(raw):
        if not isinstance(value, str) or not value.strip():
            continue
        if value.startswith(_SECRET_VALUE_PREFIXES):
            violations.append(f"{path}: value looks like a live credential")
            continue
        low = leaf_key.lower()
        if leaf_key.endswith("_env"):
            # This must be an env-var NAME, not a value. Flag inline secrets here.
            if not _ENV_NAME_RE.match(value):
                violations.append(
                    f"{path}: '*_env' keys must name an environment variable "
                    f"(UPPER_SNAKE_CASE), got {value!r}"
                )
            continue
        if any(hint in low for hint in _SECRET_KEY_HINTS):
            violations.append(
                f"{path}: put the secret in an environment variable and reference "
                f"it via a '{leaf_key}_env' key instead of inlining it"
            )
    if violations:
        raise ConfigError(
            "Config appears to contain secrets. Remove them and use environment "
            "variables (see SECURITY.md):\n  - " + "\n  - ".join(violations)
        )


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────


class ProviderConfig(BaseModel):
    # extra="allow" so an out-of-tree provider has a config home: unknown keys
    # are preserved and passed through to the provider's own constructor.
    model_config = ConfigDict(extra="allow")

    name: str
    api_key_env: str | None = None
    base_url: str | None = None


class ModelsConfig(BaseModel):
    default: str
    strong: str | None = None
    cheap: str | None = None
    embedding: str | None = None

    def strong_or_default(self) -> str:
        return self.strong or self.default

    def cheap_or_default(self) -> str:
        return self.cheap or self.default


class SlackConfig(BaseModel):
    enabled: bool = False
    bot_token_env: str = "SLACK_BOT_TOKEN"  # noqa: S105 - env var NAME, not a secret
    app_token_env: str = "SLACK_APP_TOKEN"  # noqa: S105 - env var NAME, not a secret
    allowed_users: list[str] = Field(default_factory=list)
    allowed_channels: list[str] = Field(default_factory=list)
    approve_reaction: str = "white_check_mark"
    deny_reaction: str = "x"


class ChannelsConfig(BaseModel):
    """Channel config. ``slack`` is the one built-in channel with a typed model;
    third-party channels (shipped as plugins) add their own blocks under their
    entry-point name, preserved via ``extra="allow"`` and read by the channel
    from its own config slice (the same pattern tools use)."""

    model_config = ConfigDict(extra="allow")
    slack: SlackConfig = Field(default_factory=SlackConfig)

    def enabled_map(self) -> dict[str, dict[str, Any]]:
        """Name → config slice for every enabled channel (built-in + plugins)."""

        out: dict[str, dict[str, Any]] = {}
        if self.slack.enabled:
            out["slack"] = self.slack.model_dump()
        for name, raw in (self.model_extra or {}).items():
            if isinstance(raw, dict) and raw.get("enabled"):
                out[name] = dict(raw)
        return out


class ToolSettings(BaseModel):
    """Per-tool config. Unknown keys are preserved so each tool reads its own."""

    model_config = ConfigDict(extra="allow")
    enabled: bool = False


class CapabilitySettings(BaseModel):
    """Per-capability config. Like tools, a capability is disabled until listed,
    and unknown keys are preserved so the capability provider reads its own."""

    model_config = ConfigDict(extra="allow")
    enabled: bool = False


class ApprovalConfig(BaseModel):
    timeout_s: int = 600


class PolicyConfig(BaseModel):
    default_decision: PolicyDecision = PolicyDecision.DENY
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    rules: dict[str, PolicyDecision] = Field(default_factory=dict)


class LimitsConfig(BaseModel):
    max_iterations: int = 15
    max_tool_calls: int = 25
    task_timeout_s: int = 600
    token_budget: int = 120000
    max_concurrent_sessions: int = 2


class MemoryConfig(BaseModel):
    enabled: bool = True
    compaction_token_threshold: int = 8000
    long_term_top_k: int = 5
    chunk_size: int = 800


class AgentConfig(BaseModel):
    planning: bool = True
    reflection: bool = True
    reflection_min_tools: int = 3


class StateConfig(BaseModel):
    db_path: str = "/var/lib/humon/humon.sqlite"
    # Root for per-capability private storage. Each enabled capability gets its
    # own ``<data_dir>/<name>/`` subdirectory so a plugin can persist without
    # ever importing ``humon.state``.
    data_dir: str = "/var/lib/humon/data"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"


class PrometheusConfig(BaseModel):
    enabled: bool = False
    bind: str = "127.0.0.1"
    port: int = 9464


class ObservabilityConfig(BaseModel):
    prometheus: PrometheusConfig = Field(default_factory=PrometheusConfig)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderConfig
    models: ModelsConfig
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    tools: dict[str, ToolSettings] = Field(default_factory=dict)
    capabilities: dict[str, CapabilitySettings] = Field(default_factory=dict)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    def enabled_tools(self) -> list[str]:
        """Names of tools explicitly enabled in config (FR-4 / FR-6.3)."""

        return [name for name, cfg in self.tools.items() if cfg.enabled]

    def enabled_capabilities(self) -> list[str]:
        """Names of capabilities explicitly enabled in config (same gate as tools)."""

        return [name for name, cfg in self.capabilities.items() if cfg.enabled]

    def enabled_channels(self) -> dict[str, dict[str, Any]]:
        """Name → config slice for every enabled channel (built-in + plugins)."""

        return self.channels.enabled_map()


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────


def parse_config(raw: dict[str, Any]) -> Config:
    """Validate a raw mapping into a :class:`Config` (after secret scanning)."""

    reject_secrets(raw)
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration:\n{exc}") from exc


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML config file."""

    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"Config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse YAML in {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(raw).__name__}")
    return parse_config(raw)


def resolve_secret(env_var: str | None) -> str | None:
    """Read a secret from the environment (or systemd LoadCredential)."""

    if not env_var:
        return None
    return os.environ.get(env_var)
