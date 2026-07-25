"""The capability registry — concrete backing for the ``Capabilities`` protocol.

Host services (memory, tasks, embeddings) and plugin-provided services (a vault
index, an activity log, later a code sandbox) are registered here under a name
and looked up by tools/plugins through ``ToolContext.services``. This is the
single seam that lets a new capability be added without editing ``ToolContext``.

Kept deliberately tiny and dependency-free: a name→service map with a strict
``require``. It lives in ``core`` (not ``interfaces``) because it is an
implementation; plugins depend only on the ``Capabilities`` protocol.
"""

from __future__ import annotations

from .errors import HumonError


class ServiceRegistry:
    """A name → service map implementing the ``Capabilities`` protocol."""

    def __init__(self) -> None:
        self._services: dict[str, object] = {}

    def register(self, name: str, service: object | None) -> None:
        """Register ``service`` under ``name``. A ``None`` service is ignored so
        callers can register optional host services unconditionally."""

        if service is None:
            return
        self._services[name] = service

    def get(self, name: str) -> object | None:
        return self._services.get(name)

    def require(self, name: str) -> object:
        try:
            return self._services[name]
        except KeyError:
            raise HumonError(
                f"Required capability '{name}' is not available. "
                f"Enable it in config (available: {', '.join(self.names()) or 'none'})."
            ) from None

    def names(self) -> list[str]:
        return sorted(self._services)
