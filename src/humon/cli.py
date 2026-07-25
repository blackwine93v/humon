"""``humon`` CLI: run | doctor | audit | new-tool.

Kept dependency-free (stdlib ``argparse``) so the entrypoint stays lean.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .core.errors import HumonError
from .logging import configure_logging

_DEFAULT_CONFIG = "config.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="humon", description="Humon self-hosted AI agent.")
    parser.add_argument("--version", action="version", version=f"humon {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the Humon service.")
    p_run.add_argument("-c", "--config", default=_DEFAULT_CONFIG)

    p_doctor = sub.add_parser("doctor", help="Validate config, secrets, DB, connectivity.")
    p_doctor.add_argument("-c", "--config", default=_DEFAULT_CONFIG)

    p_audit = sub.add_parser("audit", help="Audit log tools.")
    audit_sub = p_audit.add_subparsers(dest="audit_command", required=True)
    p_export = audit_sub.add_parser("export", help="Export the audit log as JSON.")
    p_export.add_argument("-c", "--config", default=_DEFAULT_CONFIG)
    p_export.add_argument("-o", "--out", default="-", help="Output file, or - for stdout.")

    # One scaffold command per extension seam; all share the same generator.
    for kind in ("tool", "capability", "channel", "provider"):
        p = sub.add_parser(f"new-{kind}", help=f"Scaffold a new {kind} plugin.")
        p.add_argument("name", help=f"{kind.capitalize()} name (snake_case).")
        p.add_argument("--path", default=".", help="Directory to create the plugin in.")

    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            return _cmd_run(args.config)
        if args.command == "doctor":
            return _cmd_doctor(args.config)
        if args.command == "audit":
            return _cmd_audit_export(args.config, args.out)
        if args.command and args.command.startswith("new-"):
            return _cmd_new_plugin(args.command.removeprefix("new-"), args.name, args.path)
    except HumonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_run(config_path: str) -> int:
    from .app import App

    config = load_config(config_path)
    configure_logging(config.logging.level, config.logging.format)
    app = App(config)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_doctor(config_path: str) -> int:
    from .doctor import run_doctor

    configure_logging("INFO", "text")
    checks = asyncio.run(run_doctor(config_path))
    all_ok = True
    for c in checks:
        mark = "✓" if c.ok else "✗"
        print(f" {mark} {c.name}: {c.detail}")
        all_ok = all_ok and c.ok
    print("\nAll checks passed." if all_ok else "\nSome checks failed.")
    return 0 if all_ok else 1


def _cmd_audit_export(config_path: str, out: str) -> int:
    from .state.db import Database
    from .state.repositories import AuditRepo

    config = load_config(config_path)

    async def _export() -> list[dict]:
        db = Database(config.state.db_path)
        await db.connect()
        rows = await AuditRepo(db).export()
        await db.close()
        return rows

    rows = asyncio.run(_export())
    payload = json.dumps(rows, indent=2, default=str)
    if out == "-":
        print(payload)
    else:
        Path(out).write_text(payload, encoding="utf-8")
        print(f"Exported {len(rows)} audit entries to {out}")
    return 0


def _cmd_new_plugin(kind: str, name: str, path: str) -> int:
    from .scaffold import scaffold_plugin

    dest = scaffold_plugin(kind, name, path)
    print(f"Created {kind} plugin skeleton at {dest}")
    print(
        f"Next: implement it, add tests, then `pip install -e .` and enable the "
        f"{kind} in config (installation never activates a plugin)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
