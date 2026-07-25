"""Plugin discovery via Python entry points (FR-7.1 / FR-7.2).

Built-in and third-party tools, channels, and providers are all discovered the
same way — through the ``humon.tools`` / ``humon.channels`` / ``humon.providers``
entry-point groups. Discovery is not activation: installing a plugin makes it
*discoverable*, but the app only ever instantiates what config explicitly lists
(FR-7.2). This keeps "pip install" from ever silently enabling a capability.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

GROUP_TOOLS = "humon.tools"
GROUP_CHANNELS = "humon.channels"
GROUP_PROVIDERS = "humon.providers"


def _load_group(group: str) -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    for ep in entry_points(group=group):
        try:
            loaded[ep.name] = ep.load()
        except Exception as exc:
            loaded[ep.name] = _BrokenPlugin(ep.name, exc)
    return loaded


class _BrokenPlugin:
    """Placeholder recorded when a plugin fails to import, surfaced by doctor."""

    def __init__(self, name: str, error: Exception) -> None:
        self.name = name
        self.error = error


def discover_tools() -> dict[str, Any]:
    return _load_group(GROUP_TOOLS)


def discover_channels() -> dict[str, Any]:
    return _load_group(GROUP_CHANNELS)


def discover_providers() -> dict[str, Any]:
    return _load_group(GROUP_PROVIDERS)
