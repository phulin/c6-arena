"""Build the reusable bridge and run the sibling native harness."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HARNESS = ROOT.parent / "c6-native-arena"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def retain_native_output(source: Path, destination: Path) -> None:
    """Preserve the harness's relative cache links when moving its output."""
    source, destination = source.resolve(), destination.resolve()
    for path in source.rglob("*"):
        if path.is_symlink():
            target = path.resolve(strict=True)
            if target.is_relative_to(source):
                target = destination / target.relative_to(source)
            path.unlink()
            path.symlink_to(target)
    shutil.move(str(source), destination)


def build_bridge(output: Path | None = None) -> Path:
    cached = output is None
    source_hash = hashlib.sha256(b"".join((ROOT / "native" / name).read_bytes()
        for name in ("lua_policy.c", "lua51.h", "policy_api.h", "context_api.h"))).hexdigest()[:16]
    output = (output or ROOT / "build" / f"lua_policy-{source_hash}.so").resolve()
    if cached and output.exists():
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    # LuaJIT's stable Lua 5.1 C ABI; a runtime package is enough (no SDK needed).
    subprocess.run([
        "cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIC",
        "-shared", str(ROOT / "native" / "lua_policy.c"),
        "-Wl,-z,defs", "-Wl,-l:libluajit-5.1.so.2", "-o", str(temporary),
    ], check=True)
    temporary.replace(output)
    return output


@contextmanager
def runner_lock(harness: Path):
    """One runner per native repository, including its Git worktrees."""
    common = subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=harness, text=True,
    ).strip()
    with (Path(common) / "lua-arena-runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another Lua arena runner is using this harness") from error
        yield


def run_game(
    policies: dict[int, Path], output: Path, *, harness: Path = DEFAULT_HARNESS,
    rounds: int = 10, seed: int = 1, timeout: int = 600,
    exploratory: bool = False,
    until_victory: bool = False,
    adapter: str = "full",
    acceleration: str | None = None,
    city_frame: str = "native",
    amenities_index: str | None = None,
    city_geometry: str | None = None,
    query_bindings: str | None = None,
    yield_neighbors: str | None = None,
    regional_contact: bool = False,
    start_era: str | None = None,
    trace_crash: bool = False,
) -> dict:
    if adapter not in ("full", "limited"):
        raise ValueError("adapter must be full or limited")
    if acceleration is None:
        acceleration = "fast" if adapter == "full" else "native"
    if acceleration not in ("native", "fast") or (acceleration == "fast" and adapter != "full"):
        raise ValueError("acceleration must be native, or fast with the full adapter")
    if city_frame not in ("native", "shadow", "fast") or (city_frame != "native" and adapter != "full"):
        raise ValueError("city_frame must be native, or shadow/fast with the full adapter")
    if amenities_index is None:
        amenities_index = "fast" if acceleration == "fast" and city_frame == "native" else "native"
    if amenities_index not in ("native", "shadow", "fast") or (amenities_index != "native" and adapter != "full"):
        raise ValueError("amenities_index must be native, or shadow/fast with the full adapter")
    if amenities_index != "native" and city_frame != "native":
        raise ValueError("amenities_index and city_frame kernels require separate runs")
    if city_geometry is None:
        city_geometry = "fast" if acceleration == "fast" else "native"
    if city_geometry not in ("native", "shadow", "fast") or (city_geometry != "native" and adapter != "full"):
        raise ValueError("city_geometry must be native, or shadow/fast with the full adapter")
    if query_bindings is None:
        query_bindings = "owned" if acceleration == "fast" else "live"
    if yield_neighbors is None:
        yield_neighbors = "fast" if acceleration == "fast" else "native"
    if query_bindings not in ("live", "owned") or (query_bindings == "owned" and acceleration != "fast"):
        raise ValueError("owned query_bindings require fast acceleration")
    if yield_neighbors not in ("native", "shadow", "fast") or (yield_neighbors != "native" and adapter != "full"):
        raise ValueError("yield_neighbors must be native, or shadow/fast with the full adapter")
    if not 1 <= rounds <= (1000 if exploratory or until_victory else 100):
        raise ValueError("supported horizon is 1..100 rounds; explicit exploration allows 1..1000")
    if not 0 <= seed < 2**64:
        raise ValueError("policy seed must be an unsigned 64-bit integer")
    if not 1 <= timeout <= 3600:
        raise ValueError("native game timeout must be 1..3600 seconds")
    if set(policies) != {0, 1}:
        raise ValueError("assign Lua scripts to major seats 0 and 1; NPCs use native AI")
    harness = harness.resolve()
    if not (harness / "native" / "external_policy.h").is_file():
        raise RuntimeError("native harness needs the external policy hook")
    # Read all inputs before creating a run so missing scripts leave no debris.
    scripts = {seat: path.read_bytes() for seat, path in policies.items()}
    with runner_lock(harness):
        output = output.resolve()
        output.mkdir(parents=True, exist_ok=False)
        (output / "policies").mkdir()
        bridge = output / "lua_policy.so"
        shutil.copy2(build_bridge(), bridge)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("C6_ARENA_", "C6_NATIVE_"))}
        env["C6_ARENA_POLICY_LIBRARY"] = str(bridge)
        env["C6_ARENA_POLICY_TRACE"] = str(output / "decisions.jsonl")
        env["C6_ARENA_POLICY_TIMING"] = "1"
        env["C6_ARENA_POLICY_TIMING_FILE"] = str(output / "policy-timing.jsonl")
        env["C6_ARENA_CHOICE_TIMING"] = "1"
        env["C6_NATIVE_SYMBOL_CACHE_STATS_FILE"] = str(output / "preparation-timing.jsonl")
        env["C6_ARENA_NATIVE_NPC_AI"] = "1"
        if query_bindings == "owned":
            env["C6_ARENA_ENTRY_BINDINGS"] = "owned"
            env["C6_ARENA_OWNER_PREDICATE"] = "inline"
        if adapter == "full":
            env["C6_ARENA_ADAPTER"] = "full"
            env["C6_ARENA_UNIT_SELECTION"] = "per-unit"
        if until_victory:
            env["C6_ARENA_REQUIRE_VICTORY"] = "1"
            env["C6_ARENA_MAX_ACTIONS"] = "4096"
            env["C6_ARENA_UNIT_SELECTION"] = "per-unit"
        copied = {}
        for seat, source in scripts.items():
            path = output / "policies" / f"seat-{seat}.lua"
            path.write_bytes(source)
            env[f"C6_ARENA_LUA_POLICY_{seat}"] = str(path)
            copied[str(seat)] = {"source": str(policies[seat].resolve()), "sha256": digest(path)}
        # The bootstrap restricts its outputs to the harness's private namespace.
        # Move the finished artifacts into this run after the host has exited.
        native_output = harness / "artifacts" / f"lua-{uuid.uuid4().hex}"
        ccache_available = (shutil.which("ccache") is not None or
                           (harness / "target/build-tools/ccache/usr/bin/ccache").is_file())
        command = [
            sys.executable, str(harness / "tools" / "benchmark.py"),
            "--runtime", "native", "--rounds", str(rounds),
            "--policy-seed", str(seed), "--production-pool", "settlers",
            "--profile", "timing", "--timeout", str(timeout),
            "--cpus", "1",
            "--build-cache", "ccache" if ccache_available else "off",
            "--output", str(native_output),
        ]
        if regional_contact:
            command.append("--regional-contact")
        if acceleration == "fast":
            command.extend([
                "--getter-inline", "wide", "--plt-binding", "fast",
                "--appeal-reuse", "skip", "--appeal-scalar-kernel", "lookup-fast",
                "--map-coordinate-kernel", "fast", "--map-xy-kernel", "fast",
                "--city-attributes-kernel", "fast", "--global-definitions-kernel", "fast",
            ])
        command.extend(["--city-player-frame", city_frame, "--yield-neighbor-kernel", yield_neighbors,
                        "--city-hexspace-kernel", city_geometry,
                        "--amenities-index-kernel", amenities_index])
        if start_era:
            command.extend(["--start-era", start_era])
        if trace_crash:
            command.append("--trace-crash")
        fixtures = (["regional-contact"] if regional_contact else [])
        if start_era and start_era != "ERA_ANCIENT":
            fixtures.append(f"start-era:{start_era}")
        metadata = {"schema": 1, "seed": seed, "rounds": rounds,
                    "exploratory": exploratory,
                    "until_victory": until_victory,
                    "adapter": adapter,
                    "acceleration": acceleration,
                    "city_frame": city_frame,
                    "amenities_index": amenities_index,
                    "city_geometry": city_geometry,
                    "query_bindings": query_bindings,
                    "yield_neighbors": yield_neighbors,
                    "npc_control": "native",
                    "fixture": "+".join(fixtures) or None,
                    "start_era": start_era or "ERA_ANCIENT",
                    "unit_selection": "per-unit" if until_victory or adapter == "full" else "pooled",
                    "policies": copied, "command": command,
                    "harness_commit": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=harness, text=True).strip(),
                    "bridge_sha256": digest(bridge), "status": "running"}
        write_json(output / "match.json", metadata)
        with (output / "runner.log").open("w") as log:
            process = subprocess.Popen(command, cwd=harness, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=timeout + 120)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                # Also stop compiler/bootstrap descendants; never leave a game running.
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                metadata.update(status="timeout" if isinstance(error, subprocess.TimeoutExpired)
                                else "interrupted", returncode=process.returncode)
                write_json(output / "match.json", metadata)
                if isinstance(error, KeyboardInterrupt):
                    if native_output.exists():
                        retain_native_output(native_output, output / "native")
                    raise
            else:
                metadata.update(status="finished" if code == 0 else "failed", returncode=code)
                write_json(output / "match.json", metadata)
        if native_output.exists():
            retain_native_output(native_output, output / "native")
        from .inspect import inspect_game
        summary = inspect_game(output)
        write_json(output / "summary.json", summary)
        return summary


def policy_assignments(left: Path, right: Path) -> dict[int, Path]:
    return {0: left, 1: right}
