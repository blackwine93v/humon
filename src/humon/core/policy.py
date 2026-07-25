"""Central policy engine (FR-6.1 / FR-6.2).

Every tool call passes through :meth:`PolicyEngine.check` before it runs. Tools
*declare* the permissions they need; they never decide for themselves whether a
call is allowed. Config maps each permission to ``allow`` / ``deny`` /
``require_approval``; anything unmapped falls to the global default (``deny`` by
default — secure by default, FR-6.3).

When a tool needs several permissions, the **most restrictive** decision wins:
``deny`` beats ``require_approval`` beats ``allow``. That ordering is what makes
"add one more permission" always safe — it can only tighten, never loosen.
"""

from __future__ import annotations

from ..config import PolicyConfig
from .interfaces import PolicyDecision, PolicyResult

# Higher number == more restrictive == wins when permissions are combined.
_PRECEDENCE: dict[PolicyDecision, int] = {
    PolicyDecision.ALLOW: 0,
    PolicyDecision.REQUIRE_APPROVAL: 1,
    PolicyDecision.DENY: 2,
}


class PolicyEngine:
    def __init__(self, config: PolicyConfig) -> None:
        self._default = config.default_decision
        self._rules = dict(config.rules)
        self.approval_timeout_s = config.approval.timeout_s

    def decision_for_permission(self, permission: str) -> PolicyDecision:
        """Resolve a single permission to its configured decision."""

        return self._rules.get(permission, self._default)

    def check(self, tool_name: str, permissions: list[str]) -> PolicyResult:
        """Decide whether ``tool_name`` may run given its declared permissions."""

        if not permissions:
            # A tool that declares no permissions cannot self-authorize; it is
            # governed by the global default (deny keeps us safe).
            return PolicyResult(
                decision=self._default,
                reason=f"no declared permissions; global default = {self._default.value}",
                permission=None,
            )

        worst = PolicyDecision.ALLOW
        worst_permission = permissions[0]
        for perm in permissions:
            decision = self.decision_for_permission(perm)
            if _PRECEDENCE[decision] > _PRECEDENCE[worst]:
                worst, worst_permission = decision, perm

        if worst is PolicyDecision.ALLOW:
            reason = f"all permissions allowed ({', '.join(permissions)})"
        else:
            reason = (
                f"permission '{worst_permission}' resolves to {worst.value} for tool '{tool_name}'"
            )
        return PolicyResult(decision=worst, reason=reason, permission=worst_permission)
