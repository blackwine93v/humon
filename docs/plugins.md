# Extending Humon — the plugin platform

Humon is a small secure core with four extension seams. Everything that talks to the
outside world, does work, or provides a service is a **plugin** discovered through a
Python entry-point group and activated only when config lists it. A plugin can live in
the Humon repo (built-in) or — the intended path for anything substantial — in its **own
pip package** (`humon-vault`, `humon-code`, …) that depends on `humon` and adds capability
without editing core.

This guide is the contract for building one. For the single-tool walkthrough see
[write-your-first-tool.md](write-your-first-tool.md); for the security rules every plugin
must honour see [`../CLAUDE.md`](../CLAUDE.md) and [`../SECURITY.md`](../SECURITY.md).

## The four seams

| Kind | Entry-point group | Protocol (in `humon.core.interfaces`) | Scaffold |
|------|-------------------|----------------------------------------|----------|
| **tool** | `humon.tools` | `Tool` | `humon new-tool <name>` |
| **capability** | `humon.capabilities` | `CapabilityProvider` | `humon new-capability <name>` |
| **channel** | `humon.channels` | `Channel` | `humon new-channel <name>` |
| **provider** | `humon.providers` | `LLMProvider` | `humon new-provider <name>` |

Two rules hold for **all** of them:

1. **Discovery is not activation.** `pip install` makes a plugin *discoverable*; it does
   nothing until a config block enables it. This is what stops an installed package from
   silently gaining hands.
2. **Plugins import only the contract.** A plugin may import `humon.core.interfaces` and
   `humon.core.errors` — never core internals, never `humon.state`. This is enforced in CI
   by import-linter. The contract module is your entire shared vocabulary with the host.

## The capability seam — how a plugin adds a *host service*

A tool gets its own config, a jail, `request_approval`, and two built-in handles
(`ctx.memory`, `ctx.tasks`). Anything else a plugin needs — a vault index, an activity
log, later a code sandbox — is a **capability**: a named service registered once at
startup and reached by name.

```python
kb = ctx.services.require("knowledge_base")   # raises if not enabled
notes = kb.search("quarterly planning")       # kb's own API — your vocabulary
```

You provide one by implementing `CapabilityProvider`:

```python
from humon.core.interfaces import CapabilityContext


class VaultIndexCapability:
    name = "vault_index"

    async def setup(self, ctx: CapabilityContext) -> object:
        # Build the service from ctx.config; persist under ctx.data_dir (a private
        # per-capability directory); compose on host services via ctx.services
        # (e.g. ctx.services.get("embeddings")). Return the object tools look up.
        self._svc = VaultIndex(path=ctx.config["vault_path"], store_dir=ctx.data_dir)
        await self._svc.reindex()
        return self._svc

    async def aclose(self) -> None:
        await self._svc.close()
```

Register and enable it exactly like a tool:

```toml
# pyproject.toml
[project.entry-points."humon.capabilities"]
vault_index = "humon_vault.index:VaultIndexCapability"
```

```yaml
# config.yaml
capabilities:
  vault_index:
    enabled: true
    vault_path: /srv/obsidian
```

Why this matters: **adding a new host service never edits the core `ToolContext`.** The
registry is keyed by name, so a package you install tomorrow can offer a service the core
has never heard of, and any enabled tool can consume it. `get(name)` returns `object`, so
you narrow it to your own protocol — the core stays ignorant of your API.

### Host services you can build on

Registered by the core (when available) and reachable from `CapabilityContext.services`
or `ToolContext.services`:

- `"memory"` — long-term note store (`MemoryStore`).
- `"tasks"` — the scheduler (`TaskStore`).
- `"embeddings"` — the active provider's `embed()` (`Embedder`), present only when the
  provider advertises the `embeddings` capability. Build semantic search on this instead
  of importing an SDK.

### Storage without touching `humon.state`

Each capability gets `ctx.data_dir` = `state.data_dir/capabilities/<name>/`, created for
you. Keep your own SQLite/files there. Plugins must not import `humon.state`; a private
data dir is how you persist while respecting the layering law.

## Config & gating, uniformly

- **Tools** → `tools.<name>.enabled` (+ arbitrary keys your tool reads from `ctx.config`).
- **Capabilities** → `capabilities.<name>.enabled` (+ arbitrary keys).
- **Channels** → `channels.<name>.enabled` (Slack is the typed built-in; third-party
  channels add their own block, read from the config slice passed to the constructor).
- **Providers** → `provider.name` selects one; extra keys under `provider:` pass through.

Secrets never go in config — only the **name** of an environment variable, under a
`*_env` key (e.g. `bot_token_env: SLACK_BOT_TOKEN`). The loader rejects inline
credentials. `humon doctor` reports what is discovered, enabled, and missing across all
four seams.

## Policy: new permission namespaces come for free

Tools declare `permissions` (free-form strings); the policy engine maps them to
allow / deny / require_approval, most-restrictive-wins, with a secure default of **deny**
for anything unlisted. A new pack just picks a namespace and documents rules:

```yaml
policy:
  rules:
    "vault.read": allow
    "vault.write": require_approval     # note writes go through human approval
```

No core change is needed to gate a brand-new capability.

## Worked example — `humon-vault` (the "secretary")

A single out-of-tree package delivering the Obsidian-secretary role, built entirely on the
seams above:

- **`vault_index` capability** — recursive markdown walk + YAML frontmatter + `[[wiki-link]]`
  graph, indexed into its own `data_dir` (semantic search via the `embeddings` host
  service, keyword fallback otherwise). *Organize / knowledge base.*
- **`vault_*` tools** — `vault_search`, `vault_read`, `vault_note` (capture/append, with
  templates), `vault_organize` (move/rename, find orphans & dangling links). Each declares
  `vault.read` / `vault.write`; writes are approval-gated by policy. *Take & check notes.*
- **The built-in scheduler** — "remind me…", "every morning summarize yesterday's notes"
  need no new machinery: the `schedule` tool + `tasks` capability already do time-based
  reminders and digests. *Reminders.*
- **`activity_log` capability** — a structured log the tools append to and the planner
  reads, so "suggest a plan from my recent activity" has real data to draw on. *Plan
  suggestions from logs.*

None of that touches `humon` core — it ships, installs, enables, and is gated like any
plugin.

## North star — `humon-code` (teaching it to code)

The same seams cover a future coding assistant with **zero** core changes: a `code.*`
toolset (read/edit/run) plus a **sandbox capability** that owns the workspace and runs
build/test commands under tight limits. `code.exec` maps to `require_approval` (or `deny`
until you trust it), so the human stays in the loop exactly as the design intends. The
platform doesn't need to know it's "coding" — it's just another capability behind the
policy gate.

## Checklist before you ship

1. Implement the protocol; import only `humon.core.interfaces` / `errors`.
2. Register the entry point; add a config block (`enabled: false` by default).
3. Add policy rules for your permission namespace.
4. Ship tests (the scaffold includes a starter); add security regression tests for any
   guard you enforce.
5. `pip install -e .`, enable in `config.yaml`, `humon doctor`, then it appears in
   `!tools` / `!capabilities`.
