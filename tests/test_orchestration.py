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

    def test_actual_native_victory_can_finish_before_requested_horizon(self):
        match = json.loads((self.path / "match.json").read_text())
        match["policies"] = {"1": {"source": "/strategies/winner.lua"}}
        write_json(self.path / "match.json", match)
        arena = json.loads((self.path / "native/arena.json").read_text())
        arena.update(game_over=True, winner_team=7, winner_player=1, victory_type=2,
                     victory_name="VICTORY_SCORE", completed_half_turns=3, final_turn=1)
        write_json(self.path / "native/arena.json", arena)
        result = inspect_game(self.path)
        self.assertEqual(result["outcome"], "finished")
        self.assertEqual(result["winner"], 1)
        self.assertEqual(result["winner_team"], 7)
        self.assertEqual(result["victory_name"], "VICTORY_SCORE")
        self.assertEqual(result["winner_policy"], "/strategies/winner.lua")

    def test_partial_victory_record_is_not_a_win(self):
        arena = json.loads((self.path / "native/arena.json").read_text())
        arena.update(game_over=True, winner_player=1)
        write_json(self.path / "native/arena.json", arena)
        result = inspect_game(self.path)
        self.assertEqual(result["outcome"], "coverage_failure")
        self.assertIsNone(result["winner"])

    def test_congress_activations_do_not_reduce_a_game_turn_horizon(self):
        arena = json.loads((self.path / "native/arena.json").read_text())
        arena.update(horizon_mode="game-turns", requested_game_turns=2, completed_game_turns=2,
                     completed_half_turns=6, activation_half_turn_limit=20)
        write_json(self.path / "native/arena.json", arena)
        self.assertEqual(inspect_game(self.path)["outcome"], "truncated")
        arena["completed_game_turns"] = 3
        write_json(self.path / "native/arena.json", arena)
        self.assertFalse(inspect_game(self.path)["checks"]["horizon"])

    def test_bounded_victory_after_extra_congress_activations(self):
        arena = json.loads((self.path / "native/arena.json").read_text())
        arena.update(horizon_mode="game-turns", requested_game_turns=2, completed_game_turns=1,
                     completed_half_turns=5, activation_half_turn_limit=20, final_turn=1,
                     game_over=True, winner_team=0, winner_player=0, victory_type=1,
                     victory_name="VICTORY_SCIENCE")
        write_json(self.path / "native/arena.json", arena)
        result = inspect_game(self.path)
        self.assertEqual(result["outcome"], "finished")
        self.assertEqual(result["winner"], 0)

    def test_full_game_request_cannot_succeed_from_horizon_only(self):
        match = json.loads((self.path / "match.json").read_text())
        match["until_victory"] = True
        write_json(self.path / "match.json", match)
        self.assertEqual(inspect_game(self.path)["outcome"], "coverage_failure")

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
        runtime["stderr"] = ("C6_ARENA unit_progress_refusal reason=actor_busy\n"
                             "C6_AI_PREP unavailable native_score_consumer 0x1fdf2d0\n"
                             "C6_CRASH signal SIGSEGV core_rva 0x1fe5670\n"
                             "C6_WHITELIST private_planner_reads_unavailable 0\n"
                             'C6_CHOICE_PROFILE {"version":1,"categories":{"unit_basic_enumeration":{"cpu_seconds":0.25}}}\n')
        write_json(self.path / "native/runtime.json", runtime)
        result = inspect_game(self.path)
        self.assertIn("actor_busy", result["native_failures"][0])
        self.assertIn("native_score_consumer", result["native_failures"][1])
        self.assertIn("core_rva 0x1fe5670", result["native_failures"][2])
        self.assertEqual(len(result["native_failures"]), 3)
        self.assertEqual(result["choice_profile"]["categories"]["unit_basic_enumeration"]["cpu_seconds"], 0.25)
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
    def test_only_major_seats_receive_lua_policies(self):
        left, right = Path("left.lua"), Path("right.lua")
        self.assertEqual(policy_assignments(left, right), {0: left, 1: right})
        with self.assertRaisesRegex(ValueError, "NPCs use native AI"):
            run_game({0: left, 1: right, 63: right}, Path("unused"))

    def test_inherited_npc_policy_settings_cannot_override_native_control(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness = root / "harness"
            (harness / "native").mkdir(parents=True)
            (harness / "native/external_policy.h").touch()
            script, bridge = root / "strategy.lua", root / "bridge.so"
            script.write_text("return function(ctx) return 1 end")
            bridge.write_bytes(b"test bridge")
            inherited = {"C6_ARENA_LUA_POLICY_62": "city-state.lua",
                         "C6_ARENA_LUA_POLICY_63": "barbarian.lua",
                         "C6_ARENA_NATIVE_NPC_AI": "0",
                         "C6_ARENA_ENTRY_BINDINGS": "untrusted",
                         "C6_ARENA_OWNER_PREDICATE": "untrusted",
                         "C6_NATIVE_CONTROLLER": "external"}
            for adapter, acceleration, frame_mode in (("full", "native", "shadow"), ("full", "fast", "native"),
                                                      ("limited", "native", "native"), ("full", None, "native"),
                                                      ("limited", None, "native"), ("full", "fast", "shadow")):
                with self.subTest(adapter=adapter, acceleration=acceleration, frame=frame_mode), patch.dict("os.environ", inherited), patch(
                        "c6_arena.native.runner_lock", return_value=nullcontext()), patch(
                        "c6_arena.native.build_bridge", return_value=bridge), patch(
                        "c6_arena.native.subprocess.check_output", return_value="commit\n"), patch(
                        "c6_arena.native.subprocess.Popen") as launch, patch(
                        "c6_arena.inspect.inspect_game", return_value={"outcome": "truncated"}):
                    launch.return_value.wait.return_value = 0
                    output = root / f"{adapter}-{acceleration}-{frame_mode}"
                    owned = acceleration == "fast" or (acceleration is None and adapter == "full")
                    binding_mode = None if acceleration is None else ("owned" if owned else "live")
                    neighbor_mode = None if acceleration is None else ("shadow" if adapter == "full" else "native")
                    geometry_mode = "fast" if acceleration == "fast" else "native"
                    candidate_mode = "shadow" if adapter == "full" else "native"
                    amenities_mode = "shadow" if adapter == "full" and acceleration == "fast" and frame_mode == "native" else None
                    run_game({0: script, 1: script}, output, harness=harness, adapter=adapter,
                             acceleration=acceleration, query_bindings=binding_mode,
                             yield_neighbors=neighbor_mode, neighbor_geometry=geometry_mode,
                             candidate_yield_reuse=candidate_mode,
                             city_frame=frame_mode, amenities_index=amenities_mode,
                             city_geometry=None if acceleration is None else ("shadow" if adapter == "full" else "native"))
                    env = launch.call_args.kwargs["env"]
                    self.assertEqual(env["C6_ARENA_NATIVE_NPC_AI"], "1")
                    self.assertNotIn("C6_ARENA_LUA_POLICY_62", env)
                    self.assertNotIn("C6_ARENA_LUA_POLICY_63", env)
                    self.assertNotIn("C6_NATIVE_CONTROLLER", env)
                    self.assertEqual(env["C6_NATIVE_SYMBOL_CACHE_STATS_FILE"],
                                     str(output.resolve() / "preparation-timing.jsonl"))
                    self.assertEqual(env.get("C6_ARENA_ENTRY_BINDINGS"), "owned" if owned else None)
                    self.assertEqual(env.get("C6_ARENA_OWNER_PREDICATE"), "inline" if owned else None)
                    self.assertEqual(json.loads((output / "match.json").read_text())["npc_control"], "native")
                    command = launch.call_args.args[0]
                    fast = acceleration == "fast" or (acceleration is None and adapter == "full")
                    self.assertEqual("--getter-inline" in command, fast)
                    self.assertEqual("--plt-binding" in command, fast)
                    self.assertEqual("--appeal-reuse" in command, fast)
                    self.assertEqual("--appeal-scalar-kernel" in command, fast)
                    self.assertEqual("--map-xy-kernel" in command, fast)
                    self.assertEqual(command[command.index("--city-player-frame") + 1], frame_mode)
                    expected_neighbor = ("fast" if owned else "native") if acceleration is None else (
                        "shadow" if adapter == "full" else "native")
                    self.assertEqual(command[command.index("--yield-neighbor-kernel") + 1], expected_neighbor)
                    self.assertEqual(command[command.index("--yield-neighbor-geometry") + 1], geometry_mode)
                    self.assertEqual(command[command.index("--candidate-yield-reuse") + 1], candidate_mode)
                    expected_geometry = ("fast" if owned else "native") if acceleration is None else (
                        "shadow" if adapter == "full" else "native")
                    self.assertEqual(command[command.index("--city-hexspace-kernel") + 1], expected_geometry)
                    expected_amenities = amenities_mode or ("fast" if fast and frame_mode == "native" else "native")
                    self.assertEqual(command[command.index("--amenities-index-kernel") + 1], expected_amenities)
                    self.assertEqual(json.loads((output / "match.json").read_text())["amenities_index"], expected_amenities)
                    self.assertNotIn("--omit-ai-prep", command)
                    self.assertNotIn("--omit-research-prep", command)

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
