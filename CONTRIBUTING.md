# Contributing to Humon

Thanks for helping build Humon. Please read [`CLAUDE.md`](CLAUDE.md) first — it is the
architectural contract (layering law, security-by-default rules, conventions).

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pre-commit install        # optional but recommended
```

## Before you push — the gate

Every change must pass the same checks CI runs:

```bash
ruff check src tests evals
ruff format --check src tests
mypy src/humon/core
lint-imports
pytest -q
python -m evals.runner --provider fake
```

## What we look for

- **Respect the layering.** `core/` must not import channels/tools/providers. If
  import-linter fails, fix the design, not the contract.
- **Security defaults are sacred.** New tools ship disabled, declare least-privilege
  permissions, and never self-authorize. Destructive actions are approval-gated.
  Add security regression tests for any new guard.
- **Stay lean.** New heavy dependencies belong behind an optional extra, not in the core.
- **Tests offline.** Use `FakeProvider`/`FakeChannel`; CI has no network.

## Writing a tool

```bash
humon new-tool my_tool          # scaffolds package + entry point + test
```

Implement `execute()`, declare `permissions`, add tests, then enable it in `config.yaml`.
See [`docs/write-your-first-tool.md`](docs/write-your-first-tool.md).

## Reporting security issues

See [`SECURITY.md`](SECURITY.md) — please do **not** open a public issue for
vulnerabilities.

## License

By contributing you agree your contributions are licensed under Apache-2.0.
