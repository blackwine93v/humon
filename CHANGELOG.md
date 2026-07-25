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

### Planned
- M4 — reflection pass, `schedule` + `lan` tools, session resume after restart.
- M5 — OpenAI/Ollama providers, entry-point plugins, `humon new-tool`, eval harness
  in CI, docs, model routing.
