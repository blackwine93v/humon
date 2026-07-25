"""Humon exception hierarchy. All raised errors derive from ``HumonError``."""

from __future__ import annotations


class HumonError(Exception):
    """Base for every Humon-raised error."""


class ConfigError(HumonError):
    """Configuration is invalid, missing, or contains a secret."""


class PolicyDenied(HumonError):
    """A tool call was denied by the policy engine."""


class ApprovalDenied(HumonError):
    """A human declined (or timed out on) a required approval."""


class ToolError(HumonError):
    """A tool failed in a way that should surface to the model as an error."""


class ProviderError(HumonError):
    """An LLM provider call failed after retries."""


class LoopGuardTripped(HumonError):
    """The agent loop hit an iteration/tool-call/time/token guard."""
