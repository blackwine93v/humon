# Quickstart

Goal: from zero to Humon answering "how much disk is free?" in Slack in under 15 minutes.

## 1. Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[slack,anthropic]'
```

Pick the extras you need: `slack`, `anthropic`, `openai`, `ollama`, `memory`, `sysinfo`,
or `all`.

## 2. Create the Slack app

Go to <https://api.slack.com/apps> → **Create New App** → **From an app manifest**, and
paste [`deploy/slack-manifest.yaml`](../deploy/slack-manifest.yaml).

- Install to your workspace and copy the **Bot User OAuth Token** (`xoxb-…`).
- Under **Basic Information → App-Level Tokens**, generate a token with the
  `connections:write` scope and copy it (`xapp-…`).

## 3. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`: set your provider/model, add your Slack user ID to
`channels.slack.allowed_users`, and keep only the tools you want (start with `shell`).

Secrets go in the environment, **never** in `config.yaml`:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
```

## 4. Validate

```bash
humon doctor -c config.yaml
```

Fix anything it flags (missing tokens, uninstalled provider, unwritable DB path).

## 5. Run

```bash
humon run -c config.yaml
```

In Slack, mention the bot in a channel it's in (or DM it): **"@humon how much disk is
free?"** Humon posts a "working…" message, runs `df -h` via the `shell` tool, and edits
the message with the answer.

## 6. Run as a service

See [`deploy/humon.service`](../deploy/humon.service) for a hardened systemd unit, and
[`hardening.md`](hardening.md) for production guidance.

## Chat commands

`!help` · `!status` · `!tools` · `!sessions` · `!cancel` · `!audit` ·
`!memory list` / `!memory forget <id>`
