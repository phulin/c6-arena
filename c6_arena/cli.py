from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from .compare import compare, paired_schedule
from .inspect import inspect_game, reflection_packet
from .native import DEFAULT_HARNESS, ROOT, build_bridge, policy_assignments, run_game
from .policy import replay


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
        command.add_argument("--adapter", choices=("full", "limited"), default="full")
        command.add_argument("--rounds", type=int, default=None if name == "run" else 10)
        command.add_argument("--timeout", type=int, default=None if name == "run" else 600)
        if name == "run":
            command.add_argument("--seed", type=int, default=1)
            command.add_argument("--explore", action="store_true", help="allow up to 1000 requested rounds; unsupported decisions still stop")
            command.add_argument("--until-victory", action="store_true", help="require a native victory result; default safety cap 1000 rounds")
            command.add_argument("--regional-contact", action="store_true", help="use the native initial-contact fixture for adapter checks")
            command.add_argument("--start-era", help="native starting EraType, e.g. ERA_INFORMATION, for integration scenarios")
        else:
            command.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    command = commands.add_parser("schedule", help="preview paired seeds and seats")
    command.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    command = commands.add_parser("replay", help="exercise Lua against supplied decision contexts (no game simulation)")
    command.add_argument("script", type=Path)
    command.add_argument("contexts", type=Path, help="JSON object or ordered list of decision contexts")
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
                              harness=args.harness,
                              rounds=args.rounds if args.rounds is not None else (1000 if args.until_victory else 10),
                              seed=args.seed,
                              timeout=args.timeout if args.timeout is not None else (900 if args.until_victory else 600),
                              exploratory=args.explore,
                              until_victory=args.until_victory, adapter=args.adapter,
                              regional_contact=args.regional_contact, start_era=args.start_era)
        elif args.command == "compare":
            result = compare(args.candidate, args.reference, args.output, seeds=args.seeds,
                             harness=args.harness, rounds=args.rounds, timeout=args.timeout, adapter=args.adapter)
        elif args.command == "schedule":
            result = paired_schedule(args.seeds)
        elif args.command == "replay":
            result = replay(args.script, args.contexts)
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
