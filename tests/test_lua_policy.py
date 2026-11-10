import ctypes
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from c6_arena.native import ROOT, build_bridge
from c6_arena.policy import Value, encode


class Option(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("actor", ctypes.c_uint32),
                ("value", ctypes.c_int32), ("x", ctypes.c_int32), ("y", ctypes.c_int32),
                ("origin_x", ctypes.c_int32), ("origin_y", ctypes.c_int32),
                ("flags", ctypes.c_uint32)]


class Request(ctypes.Structure):
    _fields_ = [("abi_version", ctypes.c_uint32), ("kind", ctypes.c_char_p),
                ("owner", ctypes.c_int32), ("count", ctypes.c_uint32),
                ("random_index", ctypes.c_uint32), ("options", ctypes.POINTER(Option))]


class LuaPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.lib = ctypes.CDLL(str(build_bridge(Path(cls.build.name) / "policy.so")))
        cls.lib.c6_arena_choose_v1.argtypes = [ctypes.POINTER(Request), ctypes.POINTER(ctypes.c_uint32)]
        cls.lib.c6_arena_choose_v1.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        cls.lib.c6_arena_policy_reset_v1()
        cls.build.cleanup()

    def setUp(self):
        self.lib.c6_arena_policy_reset_v1()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "C6_ARENA_LUA_POLICY": str(self.path / "strategy.lua"),
            "C6_ARENA_POLICY_TRACE": str(self.path / "trace.jsonl"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.lib.c6_arena_policy_reset_v1)

    def choose(self, source=None, *, owner=0, kind=b"unit", random=0):
        if source is not None:
            (self.path / "strategy.lua").write_text(source)
        options = (Option * 2)(Option(b"skip", 17, 100, 0, 0, 2, 3, 2),
                               Option(b"found_city", 17, 101, 4, 5, 2, 3, 3))
        request = Request(1, kind, owner, 2, random, options)
        selected = ctypes.c_uint32(999)
        status = self.lib.c6_arena_choose_v1(ctypes.byref(request), ctypes.byref(selected))
        return status, selected.value

    def test_actual_lua_selects_action_and_trace(self):
        script = (ROOT / "policies" / "settle.lua").read_text()
        self.assertEqual(self.choose(script), (0, 1))
        row = json.loads((self.path / "trace.jsonl").read_text())
        self.assertEqual(row["selected"], 2)
        self.assertEqual(row["options"][1]["name"], "found_city")
        self.assertEqual(row["options"][1]["x"], 4)
        self.assertNotIn("x", row["options"][0])

    def test_random_baseline_preserves_native_selection(self):
        script = (ROOT / "policies" / "random.lua").read_text()
        self.assertEqual(self.choose(script, random=1), (0, 1))
        self.assertEqual(self.choose(random=0), (0, 0))

    def test_native_context_inputs_are_preserved_in_readable_trace(self):
        (self.path / "strategy.lua").write_text("return function(ctx) assert(ctx.observation.turn == 42); return 1 end")
        self.lib.c6_arena_choose_context_v1.argtypes = [ctypes.c_int32, ctypes.POINTER(Value), ctypes.POINTER(ctypes.c_uint32)]
        context = {"owner": 0, "kind": "production", "random_index": 1,
                   "options": [{"name": "PROJECT_LAUNCH_EARTH_SATELLITE", "city_id": 9}],
                   "observation": {"turn": 42, "known_technologies": {"TECH_ROCKETRY": True}}}
        keep = []
        tree = encode(context, keep=keep)
        selected = ctypes.c_uint32(999)
        self.assertEqual(self.lib.c6_arena_choose_context_v1(0, ctypes.byref(tree), ctypes.byref(selected)), 0)
        row = json.loads((self.path / "trace.jsonl").read_text())
        self.assertEqual(row["options"], context["options"])
        self.assertEqual(row["observation"], context["observation"])
        self.assertEqual(row["selected"], 1)

    def test_large_native_map_context_fits_policy_budget(self):
        (self.path / "strategy.lua").write_text("return function(ctx) assert(#ctx.observation.plots == 20000); return 1 end")
        self.lib.c6_arena_choose_context_v1.argtypes = [ctypes.c_int32, ctypes.POINTER(Value), ctypes.POINTER(ctypes.c_uint32)]
        context = {"owner": 0, "kind": "unit", "random_index": 1, "options": [{"name": "skip"}],
                   "observation": {"plots": [{"x": x, "y": 1, "visible": True} for x in range(20000)]}}
        keep = []
        tree = encode(context, keep=keep)
        selected = ctypes.c_uint32(999)
        self.assertEqual(self.lib.c6_arena_choose_context_v1(0, ctypes.byref(tree), ctypes.byref(selected)), 0)

    def test_fields_and_each_decision_kind(self):
        script = '''return function(ctx)
            assert(ctx.owner == 0 and #ctx.options == 2)
            assert(ctx.options[1].origin_x == 2 and ctx.options[1].x == nil)
            assert(ctx.options[2].x == 4 and ctx.options[2].actor == 17)
            assert(ctx.options[2].value == 101 and ctx.options[2].index == 2)
            return 2
        end'''
        for kind in (b"unit", b"production", b"research", b"civic", b"commemoration"):
            self.assertEqual(self.choose(script, kind=kind), (0, 1))

    def test_memory_is_persistent_private_to_each_owner_and_reset(self):
        script = "local n=0; return function(ctx) n=n+1; return n end"
        self.assertEqual(self.choose(script, owner=0), (0, 0))
        self.assertEqual(self.choose(owner=0), (0, 1))
        self.assertEqual(self.choose(owner=1), (0, 0))
        self.lib.c6_arena_policy_reset_v1()
        self.assertEqual(self.choose(owner=0), (0, 0))

    def test_timing_separates_one_load_from_repeated_decisions(self):
        with patch.dict(os.environ, {"C6_ARENA_POLICY_TIMING": "1"}):
            self.assertEqual(self.choose("local n=0; return function(ctx) n=n+1; return n end"), (0, 0))
            self.assertEqual(self.choose(), (0, 1))
            self.lib.c6_arena_policy_timing_json_v1.argtypes = [ctypes.c_int32]
            self.lib.c6_arena_policy_timing_json_v1.restype = ctypes.c_char_p
            timing = json.loads(self.lib.c6_arena_policy_timing_json_v1(0))
            self.assertEqual((timing["loads"], timing["calls"], timing["failures"]), (1, 2, 0))
            for field in ("load_cpu_ns", "marshal_cpu_ns", "lua_cpu_ns", "trace_cpu_ns"):
                self.assertGreater(timing[field], 0)
            self.assertGreaterEqual(timing["bridge_cpu_ns"], sum(timing[field] for field in
                ("load_cpu_ns", "marshal_cpu_ns", "lua_cpu_ns", "trace_cpu_ns")))

    def test_bad_results_stop_without_fallback(self):
        for value in ("0", "3", "1.5", "'1'", "nil", "{}", "0/0", "math.huge"):
            with self.subTest(value=value):
                self.lib.c6_arena_policy_reset_v1()
                self.assertEqual(self.choose(f"return function(ctx) return {value} end"), (197, 999))

    def test_script_error_and_syntax_error_stop(self):
        self.assertEqual(self.choose("return function(ctx) error('bad choice') end")[0], 197)
        self.lib.c6_arena_policy_reset_v1()
        self.assertEqual(self.choose("not Lua!")[0], 197)

    def test_missing_script_stops(self):
        self.assertEqual(self.choose()[0], 197)

    def test_runaway_script_and_initialization_are_bounded(self):
        for script in ("while true do end", "return function(ctx) while true do end end"):
            self.lib.c6_arena_policy_reset_v1()
            self.assertEqual(self.choose(script)[0], 197)

    def test_memory_budget(self):
        self.assertEqual(self.choose("return function(ctx) local s=string.rep('a', 32*1024*1024); return #s end")[0], 197)

    def test_no_engine_files_processes_or_unseeded_random(self):
        script = '''return function(ctx)
            assert(io == nil and os == nil and debug == nil and package == nil)
            assert(require == nil and dofile == nil and loadfile == nil)
            assert(math.random == nil and math.randomseed == nil)
            return 1
        end'''
        self.assertEqual(self.choose(script), (0, 0))

    def test_invalid_request_abi(self):
        self.assertEqual(self.lib.c6_arena_choose_v1(None, None), 197)
        self.assertEqual(self.choose("return function() return 1 end", owner=64)[0], 197)


if __name__ == "__main__":
    unittest.main()
