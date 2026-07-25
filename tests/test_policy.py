"""Policy engine — the highest-coverage bar (T-1).

Covers every decision type, the most-restrictive combination rule, the global
default, and unknown-permission fallthrough.
"""

from __future__ import annotations

import pytest

from humon.config import PolicyConfig
from humon.core.interfaces import PolicyDecision
from humon.core.policy import PolicyEngine


def engine(default="deny", rules=None) -> PolicyEngine:
    return PolicyEngine(PolicyConfig(default_decision=default, rules=rules or {}))


def test_allow_permission():
    e = engine(rules={"shell.exec": "allow"})
    r = e.check("shell", ["shell.exec"])
    assert r.decision is PolicyDecision.ALLOW


def test_deny_permission():
    e = engine(rules={"fs.delete": "deny"})
    r = e.check("files", ["fs.delete"])
    assert r.decision is PolicyDecision.DENY


def test_require_approval_permission():
    e = engine(rules={"fs.write": "require_approval"})
    r = e.check("files", ["fs.write"])
    assert r.decision is PolicyDecision.REQUIRE_APPROVAL
    assert r.permission == "fs.write"


def test_unknown_permission_falls_to_default_deny():
    e = engine(default="deny")
    r = e.check("mystery", ["mystery.act"])
    assert r.decision is PolicyDecision.DENY


def test_most_restrictive_wins_deny_over_approval_over_allow():
    e = engine(rules={"a": "allow", "b": "require_approval", "c": "deny"})
    assert e.check("t", ["a", "b"]).decision is PolicyDecision.REQUIRE_APPROVAL
    assert e.check("t", ["a", "b", "c"]).decision is PolicyDecision.DENY
    assert e.check("t", ["a"]).decision is PolicyDecision.ALLOW


def test_adding_a_permission_can_only_tighten():
    e = engine(rules={"read": "allow", "write": "require_approval"})
    base = e.check("t", ["read"]).decision
    more = e.check("t", ["read", "write"]).decision
    assert base is PolicyDecision.ALLOW
    assert more is PolicyDecision.REQUIRE_APPROVAL


def test_no_permissions_uses_default():
    assert engine(default="deny").check("t", []).decision is PolicyDecision.DENY
    assert engine(default="allow").check("t", []).decision is PolicyDecision.ALLOW


@pytest.mark.parametrize("decision", ["allow", "deny", "require_approval"])
def test_reason_is_populated(decision):
    e = engine(rules={"p": decision})
    assert e.check("t", ["p"]).reason
