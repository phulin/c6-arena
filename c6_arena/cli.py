from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from .compare import compare, paired_schedule
from .inspect import inspect_game, reflection_packet
from .native import DEFAULT_HARNESS, ROOT, build_bridge, policy_assignments, run_game


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run and inspect plain Lua Civ AIs")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="compile the reusable C/Lua bridge")
    for name in ("run", "compare"):
        command = commands.add_parser(name)
        command.add_argument("candidate", type=Path)
        command.add_argument("--reference", type=Path, default=ROOT / "policies" / "random.lua")
        command.add_argument("--harness", type=Path, default=DEFAULT_HARNESS)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--rounds", type=int, default=10)
        command.add_argument("--timeout", type=int, default=600)
        if name == "run":
            command.add_argument("--seed", type=int, default=1)
        else:
            command.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    command = commands.add_parser("schedule", help="preview paired seeds and seats")
    command.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    for name in ("inspect", "reflect"):
        command = commands.add_parser(name)
        command.add_argument("directory", type=Path)
        if name == "reflect":
            command.add_argument("--owner", type=int, choices=(0, 1, 62, 63), default=0)
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "build":
            print(build_bridge())
            return 0
        if args.command == "run":
            result = run_game(policy_assignments(args.candidate, args.reference), args.output,
                              harness=args.harness, rounds=args.rounds, seed=args.seed,
                              timeout=args.timeout)
        elif args.command == "compare":
            result = compare(args.candidate, args.reference, args.output, seeds=args.seeds,
                             harness=args.harness, rounds=args.rounds, timeout=args.timeout)
        elif args.command == "schedule":
            result = paired_schedule(args.seeds)
        elif args.command == "inspect":
            result = inspect_game(args.directory)
        else:
            result = reflection_packet(args.directory, args.owner)
        print(json.dumps(result, indent=2, sort_keys=True))
        if args.command == "run" and result["outcome"] == "coverage_failure":
            return 1
        if args.command == "compare" and not result["valid"]:
            return 1
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"c6-arena: {error}", file=sys.stderr)
        return 1
