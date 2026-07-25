"""Eval runner CLI.

    python -m evals.runner --provider fake   # offline, scripted (CI default)
    python -m evals.runner --provider real   # env-gated; needs real credentials

Fake mode drives every task through the FakeProvider and grades the behavior.
Real mode is a placeholder gate: it only runs when HUMON_EVAL_REAL=1 is set (so
CI can keep a real-provider job opt-in), otherwise it reports "skipped".
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from .harness import EvalResult, run_task_fake
from .tasks import TASKS


async def _run_fake() -> list[EvalResult]:
    results: list[EvalResult] = []
    for task in TASKS:
        with tempfile.TemporaryDirectory(prefix="humon-eval-") as d:
            results.append(await run_task_fake(task, Path(d)))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.runner")
    parser.add_argument("--provider", choices=["fake", "real"], default="fake")
    args = parser.parse_args(argv)

    if args.provider == "real" and os.environ.get("HUMON_EVAL_REAL") != "1":
        print("Real-provider evals are gated. Set HUMON_EVAL_REAL=1 to run them. Skipped.")
        return 0

    if args.provider == "real":
        print("Real-provider eval runner is not wired to a live provider in this build.")
        return 0

    results = asyncio.run(_run_fake())
    passed = sum(1 for r in results if r.passed)
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        line = f"  [{mark}] {r.id}"
        if not r.passed:
            line += f" — {r.detail}"
        print(line)

    total = len(results)
    rate = passed / total if total else 0.0
    print(f"\n{passed}/{total} eval tasks passed ({rate:.0%}).")
    # PRD success metric: >=95% pass rate on main.
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
