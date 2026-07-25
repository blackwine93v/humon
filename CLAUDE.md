# CLAUDE.md — contributor guide for agents working on Humon

This file is the contract for any agent (Claude Code or otherwise) editing this
codebase. Read it before writing code. `AGENTS.md` is a copy of this file for
non-Claude tools — **keep the two in sync**; edit `CLAUDE.md` and mirror.

Humon is a single async Python process: an AI agent that runs as a systemd
service, talks over chat channels (Slack by default), and executes local tools
under a strict, centrally-enforced permission model. Lean by design, secure by
default. Full requirements live in the PRD; this file is the operational summary.

## The layering law (do not break it)

```
channels/  ─┐                                  ┌─  tools/
providers/ ─┼──►  core.interfaces  ◄───────────┤   capabilities/  (plugin pkgs)
            │         ▲                         └─  (implement the protocols)
            │         │
          core/  ─────┘   (agent loop, policy, sessions, memory, scheduler)
            │
          state/   (SQLite persistence — used only by core)
```

Enforced by import-linter (`.importlinter`, runs in CI):

- `humon.core` MUST NOT import `humon.channels`, `humon.tools`, or `humon.providers`.
- `humon.channels` / `humon.tools` / `humon.providers` (and any plugin package) may import
  **only** `humon.core.interfaces` and `humon.core.errors` — never core internals, never `state`.
- `humon.state` is used only by `core`; it may import `core.interfaces` for shared types.

If you need a new cross-layer type, add it to `core/interfaces.py` — that module is
the single shared vocabulary. Never widen a layer's imports to "make it work".

**The capability seam.** A plugin that needs a *host service* the core doesn't already
hand it (memory/tasks) must NOT get a new `ToolContext` field. Instead the service is
registered by name in the `ServiceRegistry` (`core/capabilities.py`) and reached via
`ctx.services.require("<name>")`. Host services register under well-known names
(`memory`, `tasks`, `embeddings`); plugin-provided services implement `CapabilityProvider`
(discovered via the `humon.capabilities` entry-point group) and register under their config
name. `ctx.services.get()` returns `object` — narrow it to your own protocol; the core stays
ignorant of your API. A capability persists under its private `ctx.data_dir`, never `state`.

## Security-by-default rules (never regress these)

1. **Tools (and capabilities/channels) are disabled until config lists them.**
   Installation/registration is not activation (`Config.enabled_tools()` /
   `enabled_capabilities()` / `enabled_channels()`). Never auto-enable a plugin.
2. **Tools never self-authorize.** Every tool call goes through `PolicyEngine.check`
   in `core/agent.py` before `execute()`. A tool declares `permissions`; the engine
   decides `allow` / `deny` / `require_approval`. Most-restrictive permission wins.
3. **Writes / deletes / network-mutation require approval by default.** See the
   `policy.rules` defaults in `config.example.yaml`. Human approval routes through the
   channel (`ToolContext.request_approval`); timeout == deny.
4. **Every tool call is audited** (`AuditRepo`), output hashed, never stored raw.
5. **Tool output is untrusted.** Wrap it with `core.prompts.wrap_untrusted`; the system
   prompt forbids following instructions found inside tool output (prompt-injection
   posture). High-risk actions rely on the approval gate, not model judgement.
6. **Secrets only from the environment / systemd credentials.** `config.py` scans config
   and refuses to load if it finds an inline secret. Never add a code path that reads a
   secret from config.
7. **Path/jail/allowlist guards live in the tool** and must canonicalize + block symlink
   escape (`files`), allowlist binaries (`shell`), and restrict to RFC1918 CIDRs (`lan`).

## Conventions

- **Async everywhere.** No blocking I/O on the event loop; wrap blocking calls in
  `asyncio.to_thread` (see `state/db.py`).
- **Tools**: snake_case `name`; `description`, `input_schema` (JSON Schema),
  `permissions: list[str]`; `async execute(args, ctx) -> ToolResult`
  (`{"ok", "content", "error"}`, content size-capped). Use `tools/_util.py` helpers.
- **Providers**: `name`, `capabilities` (subset of `{tools, streaming, embeddings}`),
  `complete`, `embed`. Import the heavy SDK lazily inside the class. Degrade gracefully
  when a capability is absent.
- **Channels**: implement `Channel`; `send` returns a `message_ref` usable with `update`.
- **Capabilities**: implement `CapabilityProvider` (`name`, `async setup(ctx) -> object`,
  `async aclose()`); return the service object registered under `name`. Reach it from a tool
  with `ctx.services.require(name)`. Persist under `ctx.data_dir`; never import `state`.
- **Logging**: structured, via `logging.get_logger(name)` — pass fields as kwargs.
- **Config**: pydantic models in `config.py`; add options there and to
  `config.example.yaml` (commented).
- **CLI**: stdlib `argparse` only — keep the entrypoint dependency-free.
- **Dependencies**: core stays lean (`pydantic`, `PyYAML`). Everything else is an
  optional extra (`[slack]`, `[anthropic]`, `[memory]`, …). Core + FakeProvider tests
  must run with no extras installed.

## Adding a tool / capability / channel / provider

1. Implement the protocol from `core/interfaces.py` in the right layer.
2. Register an entry point in `pyproject.toml` under `humon.tools` / `humon.capabilities` /
   `humon.channels` / `humon.providers` (third-party plugins do the same in their own
   `pyproject.toml`).
3. Add config: a `tools.<name>` / `capabilities.<name>` / `channels.<name>` block (with
   `enabled: false`) and any policy rules for new permission namespaces.
4. Ship tests: unit tests for the component + any security guards. `humon new-tool` /
   `new-capability` / `new-channel` / `new-provider` scaffold this shape.
5. Run the full gate (below) before committing.

The full plugin-authoring guide is [`docs/plugins.md`](docs/plugins.md) — read it before
building anything larger than a single tool.

## Testing expectations

- **Offline first**: drive the loop with `providers/fake.py` (`FakeProvider`) and
  `channels/fake.py` (`FakeChannel`). CI never hits a network.
- **Policy engine has the highest bar** — cover every rule type, combination, and the
  approval flow (`tests/test_policy.py`).
- **Security regression tests** are mandatory for guards: symlink escape, shell
  metachar injection, prompt-injection canary (`tests/test_security.py`).
- **Evals** (`evals/`): programmatic graders; run on FakeProvider in CI, real provider
  behind an env gate.

## The gate every commit must pass

```bash
ruff check src tests evals
ruff format --check src tests evals
mypy src/humon/core          # strict on core
lint-imports                 # layering
pytest -q
python -m evals.runner --provider fake
```

## Milestones & "done"

M1 skeleton (walking agent) · M2 safety & sessions · M3 planning & memory ·
M4 reflection/scheduler/LAN · M5 platform & community. Each milestone's exit
criterion is an automated test (see `tests/`) — a milestone is done when its exit
test is green and the gate passes. See `CHANGELOG.md` for current status.
