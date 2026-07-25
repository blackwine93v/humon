# Humon

**A self-hosted AI agent with hands inside your LAN — lean by design, secure by default.**

Humon is a single, lightweight async Python process you run as a systemd service on
low-resource hardware (reference: Intel NUC, Celeron, 4 GB RAM). The LLM API is the brain;
the host machine is the body. You talk to Humon through chat (Slack via Socket Mode by
default) and it executes local tools — shell commands, file operations, LAN probes,
scheduled tasks — under a strict, centrally-enforced permission model.

- **Zero inbound ports.** All connectivity is outbound: a Slack Socket Mode websocket and
  one LLM API endpoint over HTTPS.
- **Secure by default.** Every tool is disabled until you enable it. Destructive actions are
  gated behind human approval. Every tool call is audited.
- **Hand-rolled agent loop.** Planning, tool use, memory, reflection, and human-in-the-loop
  approval — no LangChain, no agent frameworks.
- **Extensible.** Add tools, channels, and LLM providers as ordinary pip packages discovered
  via entry points.

> Status: early alpha, tracking the Humon PRD v0.1. See [`docs/`](docs/) and
> [`SECURITY.md`](SECURITY.md).

## Quickstart

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[slack,anthropic]'          # pick the extras you need

cp config.example.yaml config.yaml            # edit: provider, models, channels, tools
export ANTHROPIC_API_KEY=sk-...               # secrets live in the environment, never config
export SLACK_BOT_TOKEN=xoxb-...  SLACK_APP_TOKEN=xapp-...

humon doctor -c config.yaml                   # validate config, secrets, DB, connectivity
humon run    -c config.yaml                   # start the service
```

Then, in Slack, mention the bot: **"how much disk is free?"** → Humon runs `df -h` and answers.

See [`docs/quickstart.md`](docs/quickstart.md) for the full walkthrough and
[`deploy/humon.service`](deploy/humon.service) for the hardened systemd unit.

## Architecture

```
channels/  ──►  core/ (agent loop · policy engine · sessions · memory)  ◄──  tools/
                        │
                 providers/            state/  (one SQLite file, WAL)
```

`core/` depends only on the protocols in `core/interfaces.py`; channels, tools, and providers
implement those protocols. The layering is enforced in CI by import-linter.

## Extending Humon

Write a tool, channel, or provider as a small pip package, register it under the
`humon.tools` / `humon.channels` / `humon.providers` entry-point group, and enable it in
config. Installation alone never activates a plugin. Scaffold one with:

```bash
humon new-tool my_tool
```

See [`docs/write-your-first-tool.md`](docs/write-your-first-tool.md) and
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
