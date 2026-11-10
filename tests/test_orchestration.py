from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from c6_arena.compare import compare, paired_schedule
from c6_arena.inspect import inspect_game, reflection_packet
from c6_arena.native import ROOT, policy_assignments, retain_native_output, run_game, write_json


def completed_fixture(path):
    (path / "native").mkdir()
    write_json(path / "match.json", {"status": "finished", "returncode": 0, "rounds": 2, "seed": 1})
    write_json(path / "native" / "run.json", dict.fromkeys(
        ("driver_returncode", "native_returncode", "arena_status", "effective_returncode"), 0))
    write_json(path / "native" / "arena.json", {
        "status": 0, "phase": "complete", "requested_rounds": 2,
        "completed_half_turns": 4, "initial_turn": 0, "final_turn": 2, "final_cities": 2,
        "submitted_orders": 1, "choices": 0})
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

    def test_native_stop_details_survive_in_inspection(self):
        runtime = json.loads((self.path / "native/runtime.json").read_text())
        runtime["stderr"] = "C6_ARENA unit_progress_refusal reason=actor_busy\n"
        write_json(self.path / "native/runtime.json", runtime)
        result = inspect_game(self.path)
        self.assertIn("actor_busy", result["native_failures"][0])
        self.assertEqual(result["final_turn"], 2)

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
        self.assertEqual(reflection_packet(self.path)["decisions"][0]["owner"], 0)
        self.assertEqual(reflection_packet(self.path, owner=1)["decisions"], [])

    def test_ordering_another_unit_is_not_delaying_foundation(self):
        row = {"sequence": 0, "owner": 0, "kind": "unit", "selected": 1, "error": None,
               "options": [{"name": "move", "actor": 1}, {"name": "found_city", "actor": 2}]}
        (self.path / "decisions.jsonl").write_text(json.dumps(row) + "\n")
        self.assertEqual(inspect_game(self.path)["delayed_founding_choices"], 0)

    def test_missing_final_trace_rows_are_detected(self):
        arena = json.loads((self.path / "native" / "arena.json").read_text())
        arena["submitted_orders"] = 2
        write_json(self.path / "native" / "arena.json", arena)
        self.assertFalse(inspect_game(self.path)["checks"]["trace_count"])

    def test_interrupted_trace_retains_readable_decisions(self):
        with (self.path / "decisions.jsonl").open("a") as trace:
            trace.write('{"sequence":1,"owner":')
        result = inspect_game(self.path)
        self.assertEqual(result["outcome"], "coverage_failure")
        self.assertIn("trace line 2", result["errors"][0])
        self.assertEqual(result["choices_by_seat"]["0"]["unit:skip"], 1)


class SchedulingTests(unittest.TestCase):
    def test_native_cache_links_survive_output_relocation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, destination = root / "harness/artifacts/game", root / "run/native"
            source.mkdir(parents=True)
            destination.parent.mkdir()
            cache = root / "harness/captures/rules"
            cache.parent.mkdir(parents=True)
            cache.write_text("rules")
            (source / "rules.sqlite").symlink_to("../../captures/rules")
            (source / "data").write_text("game")
            (source / "data-link").symlink_to("data")
            retain_native_output(source, destination)
            self.assertEqual((destination / "rules.sqlite").read_text(), "rules")
            self.assertEqual((destination / "data-link").read_text(), "game")

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
            report = compare(ROOT / "policies" / "settle.lua", ROOT / "policies" / "random.lua",
                             Path(temp) / "comparison", seeds=[1])
            self.assertEqual(run.call_count, 1)
            self.assertFalse(report["valid"])
            self.assertIsNone(report["promotion"])

    def test_runner_rejects_unsupported_horizon_before_building(self):
        for rounds in (0, 101):
            with self.assertRaises(ValueError):
                run_game({}, Path("unused"), rounds=rounds)
        with self.assertRaises(ValueError):
            run_game({}, Path("unused"), rounds=1001, exploratory=True)


if __name__ == "__main__":
    unittest.main()
