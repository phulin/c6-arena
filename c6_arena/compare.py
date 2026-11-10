"""Matched seed/seat schedules and comparisons with no synthetic win signal."""
from __future__ import annotations

from pathlib import Path
from .native import DEFAULT_HARNESS, ROOT, digest, policy_assignments, run_game, write_json


def paired_schedule(seeds: list[int]) -> list[dict]:
    if not seeds or len(seeds) != len(set(seeds)):
        raise ValueError("provide distinct policy seeds")
    if any(type(seed) is not int or not 0 <= seed < 2**64 for seed in seeds):
        raise ValueError("policy seeds must be unsigned 64-bit integers")
    return [{"seed": seed, "candidate_seat": seat, "reference_seat": 1-seat}
            for seed in seeds for seat in (0, 1)]


def compare(candidate: Path, reference: Path, output: Path, *, seeds: list[int],
            rounds: int = 10, timeout: int = 600, harness: Path = DEFAULT_HARNESS,
            adapter: str = "full", acceleration: str | None = None, city_frame: str = "native",
            query_bindings: str | None = None, yield_neighbors: str | None = None,
            city_geometry: str | None = None, amenities_index: str | None = None,
            neighbor_geometry: str | None = None, candidate_yield_reuse: str = "native") -> dict:
    schedule = paired_schedule(seeds)
    if not 1 <= rounds <= 100 or timeout <= 0:
        raise ValueError("rounds must be 1..100 and timeout positive")
    scripts = {"candidate": candidate.read_bytes(), "reference": reference.read_bytes()}
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "policies").mkdir()
    snapshots = {}
    for name, script in scripts.items():
        snapshots[name] = output / "policies" / f"{name}.lua"
        snapshots[name].write_bytes(script)
    games = []
    for index, case in enumerate(schedule):
        left, right = ((snapshots["candidate"], snapshots["reference"]) if case["candidate_seat"] == 0
                       else (snapshots["reference"], snapshots["candidate"]))
        game = run_game(policy_assignments(left, right), output / f"game-{index:03d}",
                        harness=harness, rounds=rounds, seed=case["seed"], timeout=timeout,
                        adapter=adapter, acceleration=acceleration, city_frame=city_frame, amenities_index=amenities_index, city_geometry=city_geometry, query_bindings=query_bindings, yield_neighbors=yield_neighbors, neighbor_geometry=neighbor_geometry, candidate_yield_reuse=candidate_yield_reuse)
        games.append({**case, "summary": game})
        report = {"schema": 1, "candidate": str(candidate.resolve()),
                  "reference": str(reference.resolve()), "schedule": schedule,
                  "policy_hashes": {name: digest(path) for name, path in snapshots.items()},
                  "games": games, "completed": len(games), "requested": len(schedule),
                  "valid": len(games) == len(schedule) and all(
                      g["summary"]["outcome"] in ("truncated", "finished") for g in games),
                  "promotion": None,
                  "limits": "Seat-swapped runs use a shared initial policy seed, not "
                            "guaranteed identical subsequent RNG. No win/loss or promotion "
                            "is inferred from early-game diagnostics."}
        write_json(output / "comparison.json", report)
        if game["outcome"] == "coverage_failure":
            break
    return report
