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

### Planned
- M3 — planning/replanning, context compaction, long-term memory (sqlite-vec),
  `memory` tool, `!` commands.
- M4 — reflection pass, `schedule` + `lan` tools, session resume after restart.
- M5 — OpenAI/Ollama providers, entry-point plugins, `humon new-tool`, eval harness
  in CI, docs, model routing.
