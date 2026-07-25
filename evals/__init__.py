"""Humon evaluation harness (PRD §11 / T-3).

A fixed suite of behavioral tasks with programmatic graders. Runs against the
offline FakeProvider in CI (scripted model responses), or a real provider behind
an env gate. The point is to catch behavioral regressions — "refused the
disallowed command", "asked approval before delete", "created file X" — not to
measure model quality.
"""
