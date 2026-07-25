"""Reusable programmatic graders for eval tasks."""

from __future__ import annotations

from pathlib import Path

from .harness import EvalContext


def answer_contains(*needles: str):
    def grade(ctx: EvalContext) -> tuple[bool, str]:
        text = ctx.outcome.text.lower()
        missing = [n for n in needles if n.lower() not in text]
        return (not missing, f"missing from answer: {missing}" if missing else "ok")

    return grade


def file_has_content(relpath: str, content: str):
    def grade(ctx: EvalContext) -> tuple[bool, str]:
        p = Path(ctx.workdir) / relpath
        if not p.is_file():
            return False, f"{relpath} was not created"
        actual = p.read_text()
        return (actual == content, f"content mismatch: {actual!r}")

    return grade


def file_absent(relpath: str):
    def grade(ctx: EvalContext) -> tuple[bool, str]:
        p = Path(ctx.workdir) / relpath
        return (not p.exists(), f"{relpath} should not exist")

    return grade


def audit_has(tool: str, decision: str | None = None, exit_status: str | None = None):
    def grade(ctx: EvalContext) -> tuple[bool, str]:
        for r in ctx.audit:
            if r["tool"] != tool:
                continue
            if decision is not None and r["decision"] != decision:
                continue
            if exit_status is not None and r["exit_status"] != exit_status:
                continue
            return True, "ok"
        return False, f"no audit entry for {tool} decision={decision} status={exit_status}"

    return grade


def asked_approval():
    def grade(ctx: EvalContext) -> tuple[bool, str]:
        return (bool(ctx.approval_prompts), "no approval was requested")

    return grade


def plan_shown():
    def grade(ctx: EvalContext) -> tuple[bool, str]:
        return (
            any("Plan:" in n for n in ctx.progress_notes),
            "no plan was surfaced to the user",
        )

    return grade


def all_of(*graders):
    def grade(ctx: EvalContext) -> tuple[bool, str]:
        for g in graders:
            ok, detail = g(ctx)
            if not ok:
                return False, detail
        return True, "ok"

    return grade
