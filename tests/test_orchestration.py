from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from c6_arena.compare import compare, paired_schedule
from c6_arena.inspect import inspect_game, reflection_packet
from c6_arena.native import ROOT, policy_assignments, run_game, write_json


def completed_fixture(path):
    (path / "native").mkdir()
    write_json(path / "match.json", {"status": "finished", "returncode": 0, "rounds": 2, "seed": 1})
    write_json(path / "native" / "run.json", dict.fromkeys(
        ("driver_returncode", "native_returncode", "arena_status", "effective_returncode"), 0))
    write_json(path / "native" / "arena.json", {
        "status": 0, "phase": "complete", "requested_rounds": 2,
        "completed_half_turns": 4, "initial_turn": 0, "final_turn": 2, "final_cities": 2})
    write_json(path / "native" / "runtime.json", {"returncode": 0, **dict.fromkeys(
        ("fixture_success", "framework_cleanup_proven", "client_cleanup_proven", "native_cleanup_requested"), True)})
    row = {"sequence": 0, "owner": 0, "kind": "unit", "selected": 1, "error": None,
           "options": [{"name": "skip"}, {"name": "found_city"}]}
    (path / "decisions.jsonl").write_text(json.dumps(row) + "\n")


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        completed_fixture(self.path)

    def test_completed_horizon_is_truncation_not_victory(self):
        result = inspect_game(self.path)
        self.assertEqual(result["outcome"], "truncated")
        self.assertIsNone(result["winner"])
        self.assertEqual(result["delayed_founding_choices"], 1)

    def test_partial_run_is_coverage_failure(self):
        arena = json.loads((self.path / "native" / "arena.json").read_text())
        arena["completed_half_turns"] = 3
        write_json(self.path / "native" / "arena.json", arena)
        self.assertEqual(inspect_game(self.path)["outcome"], "coverage_failure")

    def test_cleanup_failure_is_not_a_policy_loss(self):
        (self.path / "native" / "runtime.json").unlink()
        result = inspect_game(self.path)
        self.assertEqual(result["outcome"], "coverage_failure")
        self.assertIsNone(result["winner"])

    def test_missing_and_broken_trace(self):
        (self.path / "decisions.jsonl").unlink()
        self.assertFalse(inspect_game(self.path)["checks"]["lua_trace"])
        row = {"sequence": 5, "owner": 0, "kind": "unit", "selected": None,
               "options": [], "error": "instruction budget exceeded"}
        (self.path / "decisions.jsonl").write_text(json.dumps(row) + "\n")
        self.assertEqual(len(inspect_game(self.path)["errors"]), 2)

    def test_reflection_includes_plain_script(self):
        (self.path / "policies").mkdir()
        (self.path / "policies" / "seat-0.lua").write_text("return function(ctx) return 1 end")
        self.assertIn("return function", reflection_packet(self.path)["script"])


class SchedulingTests(unittest.TestCase):
    def test_swaps_seats_for_each_seed(self):
        self.assertEqual(paired_schedule([7, 8]), [
            {"seed": 7, "candidate_seat": 0, "reference_seat": 1},
            {"seed": 7, "candidate_seat": 1, "reference_seat": 0},
            {"seed": 8, "candidate_seat": 0, "reference_seat": 1},
            {"seed": 8, "candidate_seat": 1, "reference_seat": 0}])

    def test_invalid_seeds(self):
        for seeds in ([], [1, 1], [-1], [2**64], [True]):
            with self.assertRaises(ValueError):
                paired_schedule(seeds)

    def test_failed_game_stops_comparison_without_promotion(self):
        with tempfile.TemporaryDirectory() as temp, patch(
                "c6_arena.compare.run_game", return_value={"outcome": "coverage_failure"}) as run:
            report = compare(Path("a.lua"), Path("b.lua"), Path(temp) / "comparison", seeds=[1])
            self.assertEqual(run.call_count, 1)
            self.assertFalse(report["valid"])
            self.assertIsNone(report["promotion"])

    def test_runner_rejects_unsupported_horizon_before_building(self):
        for rounds in (0, 101):
            with self.assertRaises(ValueError):
                run_game({}, Path("unused"), rounds=rounds)


if __name__ == "__main__":
    unittest.main()
