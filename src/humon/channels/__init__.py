"""Chat channels. Each implements ``core.interfaces.Channel`` and imports only
``core.interfaces``. Slack is the v1 default; ``FakeChannel`` drives the loop in
tests without a network.
"""
