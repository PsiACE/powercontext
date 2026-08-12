"""Command-line entry point for built-in e2e tasks and offline scoring."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger

from .models import load_tasks
from .runner import rescore_replay
from .settings import HarnessSettings
from .tasks import run_tasks, select_tasks


def main() -> None:
    logger.remove()
    logger.add(sys.stderr, backtrace=False, diagnose=False)

    parser = argparse.ArgumentParser(prog="powercontext-e2e")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, default=Path("e2e/bub/tasks"))
    run_parser.add_argument("--id", action="append", default=[])
    run_parser.add_argument("--category", action="append", default=[])
    run_parser.add_argument("--output", type=Path, required=True)

    rescore_parser = subparsers.add_parser("rescore")
    rescore_parser.add_argument("replay", type=Path)
    rescore_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = HarnessSettings()

    if args.command == "rescore":
        passed = asyncio.run(rescore_replay(args.replay, args.output, settings))
    else:
        tasks = load_tasks(args.manifest)
        selected = select_tasks(tasks, ids=tuple(args.id), categories=tuple(args.category))
        passed = asyncio.run(
            run_tasks(
                selected,
                output_dir=args.output,
                settings=settings,
            )
        )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
