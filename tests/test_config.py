"""Config loader + secret-rejection heuristic (FR-8, FR-6.5)."""

from __future__ import annotations

import pytest

from humon.config import parse_config, reject_secrets
from humon.core.errors import ConfigError


def _valid() -> dict:
    return {
        "provider": {"name": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
        "models": {"default": "claude"},
    }


def test_valid_config_parses():
    cfg = parse_config(_valid())
    assert cfg.provider.name == "anthropic"
    assert cfg.policy.default_decision.value == "deny"  # secure by default


def test_rejects_inline_slack_token():
    raw = _valid()
    raw["channels"] = {"slack": {"bot_token": "xoxb-123-real-token"}}
    with pytest.raises(ConfigError):
        reject_secrets(raw)


def test_rejects_secretish_key_with_inline_value():
    raw = _valid()
    raw["provider"]["password"] = "hunter2secret"
    with pytest.raises(ConfigError):
        reject_secrets(raw)


def test_allows_env_reference_keys():
    raw = _valid()
    raw["channels"] = {"slack": {"bot_token_env": "SLACK_BOT_TOKEN"}}
    reject_secrets(raw)  # must not raise


def test_env_key_must_name_a_variable_not_a_value():
    raw = _valid()
    raw["channels"] = {"slack": {"bot_token_env": "xoxb-actually-a-secret"}}
    with pytest.raises(ConfigError):
        reject_secrets(raw)


def test_rejects_sk_prefixed_value_anywhere():
    raw = _valid()
    raw["misc"] = "sk-abcdef123456"
    with pytest.raises(ConfigError):
        reject_secrets(raw)


def test_enabled_tools_only_lists_enabled():
    raw = _valid()
    raw["tools"] = {"shell": {"enabled": True}, "files": {"enabled": False}}
    cfg = parse_config(raw)
    assert cfg.enabled_tools() == ["shell"]


def test_unknown_top_level_key_rejected():
    raw = _valid()
    raw["bogus"] = 1
    with pytest.raises(ConfigError):
        parse_config(raw)


def test_enabled_capabilities_only_lists_enabled():
    raw = _valid()
    raw["capabilities"] = {"vault": {"enabled": True}, "other": {"enabled": False}}
    cfg = parse_config(raw)
    assert cfg.enabled_capabilities() == ["vault"]


def test_capability_extra_keys_preserved():
    raw = _valid()
    raw["capabilities"] = {"vault": {"enabled": True, "vault_path": "/srv/notes"}}
    cfg = parse_config(raw)
    assert cfg.capabilities["vault"].model_dump()["vault_path"] == "/srv/notes"


def test_enabled_channels_includes_slack_and_third_party():
    raw = _valid()
    raw["channels"] = {
        "slack": {"enabled": True},
        "matrix": {"enabled": True, "homeserver": "https://m.example"},
        "off": {"enabled": False},
    }
    cfg = parse_config(raw)
    enabled = cfg.enabled_channels()
    assert set(enabled) == {"slack", "matrix"}
    assert enabled["matrix"]["homeserver"] == "https://m.example"


def test_provider_extra_keys_preserved():
    raw = _valid()
    raw["provider"]["organization"] = "org-123"
    cfg = parse_config(raw)
    assert cfg.provider.model_dump()["organization"] == "org-123"


def test_state_data_dir_defaults():
    cfg = parse_config(_valid())
    assert cfg.state.data_dir.endswith("/data")
