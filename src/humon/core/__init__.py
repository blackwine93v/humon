"""Humon core: the agent loop, policy engine, sessions, and memory.

Import rule (enforced by import-linter): everything in ``core`` may import
``humon.core.interfaces`` and its ``core`` siblings, but MUST NOT import from
``humon.channels``, ``humon.tools``, or ``humon.providers``. Those layers depend
on ``core.interfaces``, never the reverse.
"""
