# Changelog

All notable changes to Humon are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/). Pre-1.0: expect breaking changes.

## [Unreleased]

### M1 — Skeleton (walking agent) ✅
- Project scaffold, Apache-2.0 license, `pyproject.toml` with extras and plugin
  entry points.
- Config loader with pydantic models and an inline-secret rejection heuristic.
- Structured JSON-lines logging.
- SQLite state layer (WAL) with migrations and repositories (sessions, messages,
  audit, memory, tasks).
- `core/interfaces.py` protocols (Tool, Channel, LLMProvider) and the policy engine
  (allow/deny/require_approval, most-restrictive-wins).
- Hand-rolled agent loop with loop guards, audit logging, and untrusted-output
  wrapping. FakeProvider + Anthropic provider.
- `shell` tool (binary allowlist, metachar rejection, output cap).
- Slack Socket Mode channel; FakeChannel for tests.
- `humon` CLI (`run` / `doctor` / `audit` / `new-tool`).
- CI: ruff, mypy (strict on core), pytest, import-linter, detect-secrets.
- Exit criterion test: "how much disk is free?" → shell tool → answer.

### M2 — Safety & sessions ✅
- Human-in-the-loop approval flow: policy `require_approval` routes through the
  channel; Slack collects approvals via emoji reactions; timeout == deny.
- `files` tool with jail paths, path canonicalization, and symlink-escape
  prevention; per-operation permissions (read allowed, write/delete approval-gated).
- `sysinfo` tool: read-only CPU/mem/disk/service/journal (stdlib fallback, no extra
  dependency required).
- Per-action permission refinement (`Tool.permissions_for`) so a single tool can
  gate different operations differently — the policy engine still decides.
- `SECURITY.md` threat model + hardening guide; `docs/hardening.md`.
- Security regression tests: symlink escape, shell metachar injection (parametrized),
  untrusted-output wrapping, and a structural prompt-injection canary.
- M2 exit test: file write triggers approval; deny blocks it; audit shows both.

### M3 — Planning & memory ✅
- Planner (`core/planner.py`): turns multi-step requests into an explicit numbered
  plan (shown in-thread) using the strong model; `replan()` hook for step failure.
- Long-term memory (`core/memory.py` + `state/vectors.py`): semantic notes via
  embeddings + sqlite-vec, with keyword-search fallback when no embedding provider
  is available; episodic outcome records injected as hints on similar tasks.
- Context compaction (FR-3.5): older turns summarized into a per-session summary
  when the transcript exceeds the token threshold; the agent runs on a recent
  window + summary.
- `memory` tool (remember/recall/list/forget) reaching storage through a
  `MemoryStore` handle on `ToolContext` (no layering violation).
- `MemoryStore` protocol + `ToolContext.memory`; `Tool.permissions_for` used again
  for per-action gating.
- `!` chat commands (`!status !cancel !sessions !tools !help !audit !memory`).
- M3 exit test: multi-step task shows a plan, executes, and a fact stored in one
  session is recalled in a later session.

### M4 — Reflection, scheduler, LAN ✅
- Reflector (`core/reflector.py`): critic pass over the draft answer for tasks that
  used ≥3 tool calls; best-effort (keeps the draft on error).
- `lan` tool: ping / TCP check / HTTP GET, restricted to RFC1918 (or configured)
  CIDRs — hostnames are resolved and every address must be private, so it can never
  reach the public internet.
- `schedule` tool + in-process `Scheduler` (`core/scheduler.py`): create/list/delete
  recurring or one-shot tasks (`once`, `once:<iso>`, `every:<seconds>`, `daily@HH:MM`),
  reached via `TaskStore` on `ToolContext`. Creating a task is approval-gated
  (pre-authorizes it, OQ#2); it then runs unattended under the same policy engine.
- Session/task resume after restart: tasks live in SQLite, so a fresh Scheduler
  picks them up on startup.
- M4 exit test: a "check the NAS every morning at 8 and DM me" task is created,
  survives a simulated restart, executes, delivers a message, and reschedules.

### M5 — Platform & community ✅
- OpenAI-compatible and Ollama providers (lazy SDK/httpx import; tools + embeddings;
  message normalization mapped to the shared `CompletionResponse`).
- Entry-point plugin loading proven end-to-end: a scaffolded tool installs via pip,
  is discovered through the `humon.tools` group, config-gates, passes policy, and
  would appear in `!tools` (real pip-roundtrip test).
- `humon new-tool` scaffold generates an installable plugin skeleton with a test.
- Eval harness (`evals/`): 22 behavioral tasks with programmatic graders, run on
  FakeProvider in CI and mirrored as parametrized pytest cases; real-provider job is
  env-gated.
- Model routing (FR-3.6): strong model for planning/reflection, cheap model for
  context compaction, default for routine steps.
- Docs: quickstart, write-your-first-tool, hardening.

### Extensibility foundation — plugin platform ✅
- **Capability seam** (`core/capabilities.py` + `Capabilities`/`CapabilityProvider`/
  `CapabilityContext` in `core/interfaces.py`): a name-keyed `ServiceRegistry` threaded
  through `ToolContext.services`. Host services register under well-known names (`memory`,
  `tasks`, `embeddings`); plugin-provided services register via the new `humon.capabilities`
  entry-point group. Adding a host service no longer edits `ToolContext`.
- **Fourth entry-point group** `humon.capabilities` with `discover_capabilities()`; config
  gating via `capabilities.<name>.enabled` + `Config.enabled_capabilities()`, mirroring tools.
- **Config symmetry for channels & providers**: `ChannelsConfig` is `extra="allow"` with
  `enabled_map()` / `Config.enabled_channels()` so third-party channels have a config home
  and are built generically; `ProviderConfig` passes through extra keys for out-of-tree
  providers. New `state.data_dir` gives each capability private storage
  (`<data_dir>/capabilities/<name>/`) without importing `state`.
- **Scaffolding parity**: `humon new-capability` / `new-channel` / `new-provider` alongside
  `new-tool`, each emitting an installable package with the right entry point and a starter test.
- **Docs**: `docs/plugins.md` (four seams, the capability pattern, a worked `humon-vault`
  sketch, and a `humon-code` north star); `CLAUDE.md`/`AGENTS.md` capability-seam rules;
  `config.example.yaml` capabilities/channels/policy examples.
- `!capabilities` chat command and `doctor` now report discovered/enabled capabilities.
- No behaviour change to existing tools/channels/providers; `ctx.memory`/`ctx.tasks` retained.

## Status

All milestones M1–M5 implemented, plus a plugin-platform extensibility foundation
(capability seam + config/scaffold/doc parity across all four extension points). CI gate =
ruff, ruff-format, mypy (strict on core), import-linter, pytest, detect-secrets, evals.
