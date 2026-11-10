from pathlib import Path
import tempfile
import unittest

from c6_arena.native import ROOT
from c6_arena.policy import LuaPolicy


class ScienceStrategyTests(unittest.TestCase):
    def setUp(self):
        self.session = LuaPolicy(ROOT / "policies/science.lua")
        self.policy = self.session.__enter__()
        self.addCleanup(self.session.__exit__, None, None, None)

    def choose(self, kind, names=None, *, options=None, observation=None, **context):
        options = options or [{"name": name} for name in names]
        request = {"kind": kind, "options": options, **context}
        if observation is not None:
            request["observation"] = observation
        return self.policy.choose(request)

    def test_found_capital_and_explain_choice(self):
        choice = self.choose("unit", ["skip", "move", "found_city"])
        self.assertEqual(choice["option"]["name"], "found_city")
        self.assertIn("science/expand", choice["reason"])

    def test_choose_settler_before_other_units_then_choose_its_order(self):
        choice = self.choose("unit_selection", options=[
            {"name": "UNIT_WARRIOR", "actor": 1}, {"name": "UNIT_SETTLER", "actor": 2}])
        self.assertEqual(choice["option"]["actor"], 2)
        choice = self.choose("unit", options=[
            {"name": "move", "actor": 2, "threat": 1, "goody_hut": True},
            {"name": "move", "actor": 2, "threat": 0}])
        self.assertEqual(choice["selected"], 2)

    def test_growth_policy_changes_when_threatened(self):
        names = ["UNIT_SETTLER", "BUILDING_GRANARY", "UNIT_ARCHER"]
        peaceful = self.choose("production", names, observation={"player": {"city_count": 2},
            "units": [{"type": "UNIT_ARCHER"}, {"type": "UNIT_ARCHER"}]})
        threatened = self.choose("production", names, observation={"player": {
            "city_count": 2, "at_war": True, "enemy_strength": 200, "military_strength": 50}})
        self.assertEqual(peaceful["option"]["name"], "UNIT_SETTLER")
        self.assertEqual(threatened["option"]["name"], "UNIT_ARCHER")

    def test_does_not_keep_training_settlers_without_expansion_sites(self):
        choice = self.choose("production", ["UNIT_SETTLER", "BUILDING_LIBRARY"],
            observation={"expansion_sites": 0, "player": {"city_count": 8}})
        self.assertEqual(choice["option"]["name"], "BUILDING_LIBRARY")

    def test_housing_and_cash_crises_change_build_order(self):
        request = {"player": {"city_count": 4}, "cities": [{"id": 1, "population": 5, "housing": 5}]}
        choices = [{"name": "BUILDING_GRANARY", "city_id": 1}, {"name": "BUILDING_MONUMENT", "city_id": 1}]
        self.assertEqual(self.choose("production", options=choices, observation=request)["selected"], 1)
        self.assertEqual(self.choose("production", ["DISTRICT_CAMPUS", "BUILDING_MARKET"],
            observation={"player": {"gold_per_turn": -10}})["option"]["name"], "BUILDING_MARKET")

    def test_science_research_and_emergency_military_research(self):
        self.assertEqual(self.choose("research", ["TECH_ASTROLOGY", "TECH_WRITING"])["selected"], 2)
        names = ["TECH_EDUCATION", "TECH_MACHINERY"]
        self.assertEqual(self.choose("research", names)["selected"], 1)
        self.assertEqual(self.choose("research", names, observation={"player": {"threat": 1}})["selected"], 2)

    def test_select_relevant_civic_and_dedication(self):
        self.assertEqual(self.choose("civic", ["CIVIC_DRAMA_POETRY", "CIVIC_RECORDED_HISTORY"])["selected"], 2)
        self.assertEqual(self.choose("commemoration", ["COMMEMORATION_RELIGIOUS", "COMMEMORATION_SCIENTIFIC"])["selected"], 2)

    def test_late_research_prioritizes_victory_path(self):
        choice = self.choose("research", ["TECH_MINING", "TECH_OFFWORLD_MISSION"],
            observation={"known_technologies": {"TECH_ROCKETRY": True}, "turn": 210})
        self.assertEqual(choice["selected"], 2)
        self.assertIn("science/space", choice["reason"])

    def test_all_launch_stages_beat_routine_builds(self):
        for project in ("PROJECT_LAUNCH_EARTH_SATELLITE", "PROJECT_LAUNCH_MOON_LANDING",
                        "PROJECT_LAUNCH_MARS_BASE", "PROJECT_LAUNCH_EXOPLANET_EXPEDITION"):
            with self.subTest(project=project):
                self.assertEqual(self.choose("production", ["UNIT_BUILDER", project],
                    observation={"known_technologies": {"TECH_ROCKETRY": True}})["selected"], 2)

    def test_accelerate_expedition_and_protect_launch_city(self):
        choice = self.choose("production", ["BUILDING_MONUMENT", "PROJECT_TERRESTRIAL_LASER"],
            observation={"player": {"exoplanet_launched": True}})
        self.assertEqual(choice["selected"], 2)
        self.assertIn("science/accelerate", choice["reason"])
        choice = self.choose("production", options=[
            {"name": "PROJECT_LAUNCH_EXOPLANET_EXPEDITION", "city_id": 1},
            {"name": "UNIT_ARCHER", "city_id": 1}], observation={
                "player": {"at_war": True, "threat": 1}, "cities": [{"id": 1, "under_siege": True}]})
        self.assertEqual(choice["selected"], 2)

    def test_builder_improves_worked_production(self):
        choice = self.choose("unit", options=[{"name": "skip", "actor": 2},
            {"name": "build_improvement", "actor": 2, "production_gain": 2, "connects_resource": True}],
            observation={"units": [{"id": 2, "role": "builder"}]})
        self.assertEqual(choice["selected"], 2)

    def test_injured_unit_retreats_instead_of_suicide_attack(self):
        choice = self.choose("unit", options=[
            {"name": "attack", "actor": 3, "damage_dealt": 10, "damage_taken": 40},
            {"name": "move", "actor": 3, "retreats": True, "x": 1, "y": 2}],
            observation={"units": [{"id": 3, "health": 15, "max_health": 100}]})
        self.assertEqual(choice["selected"], 2)

    def test_civilian_avoids_visible_danger(self):
        choice = self.choose("unit", options=[
            {"name": "move", "actor": 4, "x": 1, "y": 0, "threat": 1, "goody_hut": True},
            {"name": "move", "actor": 4, "x": 0, "y": 1, "threat": 0}],
            observation={"units": [{"id": 4, "role": "settler"}]})
        self.assertEqual(choice["selected"], 2)

    def test_exploration_avoids_a_two_tile_loop(self):
        self.choose("unit", options=[{"name": "move", "actor": 5, "x": 1, "y": 0,
                                     "origin_x": 0, "origin_y": 0}])
        choice = self.choose("unit", options=[
            {"name": "move", "actor": 5, "x": 0, "y": 0},
            {"name": "move", "actor": 5, "x": 2, "y": 0}])
        self.assertEqual(choice["selected"], 2)

    def test_diplomacy_preserves_peace_and_victory_budget(self):
        self.assertEqual(self.choose("diplomacy", ["DECLARE_WAR", "RESEARCH_ALLIANCE"])["selected"], 2)
        self.assertEqual(self.choose("trade", options=[{"name": "TRADE", "gold_cost": 800, "new_luxury": True},
            {"name": "DECLINE"}], observation={"player": {"treasury": 850}})["selected"], 2)

    def test_government_policy_governor_and_great_person(self):
        cases = [("government", ["GOVERNMENT_CHIEFDOM", "GOVERNMENT_DEMOCRACY"]),
                 ("policy", ["POLICY_DISCIPLINE", "POLICY_INTERNATIONAL_SPACE_AGENCY"]),
                 ("governor", ["GOVERNOR_THE_DEFENDER", "GOVERNOR_THE_EDUCATOR"])]
        for kind, options in cases:
            self.assertEqual(self.choose(kind, options)["selected"], 2)
        self.assertEqual(self.choose("great_person", options=[{"name": "PASS"},
            {"name": "RECRUIT", "person_class": "scientist"}])["selected"], 2)

    def test_congress_avoids_blocking_own_victory(self):
        self.assertEqual(self.choose("congress", options=[
            {"name": "VOTE", "blocks_own_victory": True},
            {"name": "VOTE", "science_bonus": 1}])["selected"], 2)

    def test_native_full_adapter_choices_preserve_science_plan(self):
        self.assertEqual(self.choose("production_control", ["KEEP_PRODUCTION", "UNIT_SCOUT"])["selected"], 1)
        self.assertEqual(self.choose("production_control", ["UNIT_SCOUT", "DISTRICT_CAMPUS"])["selected"], 2)
        self.assertEqual(self.choose("city_economy", options=[{"name": "BUILDING_LIBRARY", "metadata": {"currency": "gold"}},
            {"name": "PASS"}], observation={"player": {"treasury": 50}})["selected"], 2)
        self.assertEqual(self.choose("player_action", ["PASS", "RECRUIT_GREAT_PERSON"])["selected"], 2)
        self.assertEqual(self.choose("great_work", ["MOVE_GREAT_WORK", "KEEP_GREAT_WORKS"])["selected"], 2)
        self.assertEqual(self.choose("unit", ["skip", "REPAIR"])["selected"], 2)
        self.assertEqual(self.choose("unit", ["skip", "UNITOPERATION_BUILD_IMPROVEMENT"])["selected"], 2)
        self.assertEqual(self.choose("unit", ["skip", "UNITOPERATION_MAKE_TRADE_ROUTE"])["selected"], 2)

    def test_native_congress_metadata_avoids_incidental_diplomatic_victory(self):
        options = [{"name": "ResolutionPlayerTarget", "value": 1,
                    "metadata": {"resolution_name": "WC_RES_DIPLOVICTORY", "target_hash": 0}},
                   {"name": "ResolutionPlayerTarget", "value": 2,
                    "metadata": {"resolution_name": "WC_RES_DIPLOVICTORY", "target_hash": 0}}]
        self.assertEqual(self.choose("congress", options=options, owner=0)["selected"], 2)
        self.assertEqual(self.choose("congress", options=[{"name": "votes", "value": 8},
            {"name": "votes", "value": 1}])["selected"], 2)

    def test_purchase_menu_returns_to_pass_when_nothing_is_useful(self):
        menu = [{"name": "PASS", "actor": 9}, {"name": "OPEN_PURCHASES", "actor": 9}]
        observation = {"turn": 100, "player": {"treasury": 1500, "city_count": 8}}
        self.assertEqual(self.choose("city_economy", options=menu, observation=observation)["selected"], 2)
        self.assertEqual(self.choose("city_purchases", options=[{"name": "BACK", "actor": 9},
            {"name": "UNIT_SCOUT", "actor": 9, "metadata": {"currency": "gold", "gold_cost": 50}}],
            observation=observation)["selected"], 1)
        self.assertEqual(self.choose("city_economy", options=menu, observation=observation)["selected"], 1)
        observation["turn"] += 1
        self.assertEqual(self.choose("city_economy", options=menu, observation=observation)["selected"], 2)

    def test_governor_promotions_follow_science_then_space_plan(self):
        self.assertEqual(self.choose("governor", ["GOVERNOR_THE_RESOURCE_MANAGER",
            "GOVERNOR_PROMOTION_EDUCATOR_RESEARCHER"])["selected"], 2)
        self.assertEqual(self.choose("governor", ["GOVERNOR_PROMOTION_RESOURCE_MANAGER_INDUSTRIALIST",
            "GOVERNOR_PROMOTION_EDUCATOR_SPACE_INITIATIVE"], observation={
                "known_technologies": {"TECH_ROCKETRY": True}})["selected"], 2)

    def test_assign_governor_using_remembered_capital_location(self):
        self.choose("unit", options=[{"name": "found_city", "origin_x": 4, "origin_y": 5}])
        self.choose("governor", options=[{"name": "GOVERNOR_THE_EDUCATOR", "actor": 3}])
        self.assertEqual(self.choose("governor", options=[
            {"name": "ASSIGN_GOVERNOR", "actor": 3, "value": 2, "x": 8, "y": 9},
            {"name": "ASSIGN_GOVERNOR", "actor": 3, "value": 1, "x": 4, "y": 5}])["selected"], 2)

    def test_city_citizens_do_not_starve_for_production(self):
        self.assertEqual(self.choose("city_citizens", options=[
            {"name": "ASSIGN", "production": 50, "starvation": True},
            {"name": "ASSIGN", "production": 10, "food_surplus": 2}])["selected"], 2)

    def test_purchase_can_decline_and_religion_remains_supported(self):
        self.assertEqual(self.choose("purchase", options=[
            {"name": "BUILDING_LIBRARY", "gold_cost": 500}, {"name": "PASS"}],
            observation={"player": {"treasury": 520}})["selected"], 2)
        self.assertEqual(self.choose("religion", ["BELIEF_FERTILITY_RITES", "BELIEF_DIVINE_SPARK"])["selected"], 2)

    def test_unknown_decision_stops_explicitly(self):
        with self.assertRaises(RuntimeError):
            self.choose("not_implemented", ["PASS"])

    def test_seeded_fallback_for_unnamed_native_definitions(self):
        self.assertEqual(self.choose("research", options=[{"value": 1}, {"value": 2}],
                                     random_index=2)["selected"], 2)

    def test_large_unit_choice_stays_within_runtime_budget(self):
        options = [{"name": "move", "actor": 5, "x": x, "y": 0} for x in range(4096)]
        choice = self.choose("unit", options=options, random_index=4096)
        self.assertEqual(choice["selected"], 4096)

    def test_current_observations_override_old_founding_memory(self):
        self.choose("unit", ["found_city"])
        choice = self.choose("unit", options=[
            {"name": "found_city", "threat": 1}, {"name": "move", "goody_hut": True}],
            observation={"player": {"city_count": 0}})
        self.assertEqual(choice["option"]["name"], "found_city")


class ContextTests(unittest.TestCase):
    def test_nested_data_booleans_unicode_and_optional_values(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "inspect.lua"
            script.write_text('''return function(ctx)
                assert(ctx.observation.text == "雪\\n\\\"")
                assert(ctx.observation.active == true and ctx.observation.missing == nil)
                assert(ctx.observation.nested[2].amount == 1.25)
                return ctx.random_index, "nested data received"
            end''')
            with LuaPolicy(script) as policy:
                choice = policy.choose({"kind": "unit", "options": [{"name": "skip"}],
                    "observation": {"text": '雪\n"', "active": True, "missing": None,
                                    "nested": [False, {"amount": 1.25}]}})
            self.assertEqual(choice["reason"], "nested data received")

    def test_invalid_context_is_rejected_without_calling_policy(self):
        with LuaPolicy(ROOT / "policies/science.lua") as policy:
            for context in ({"kind": "unit", "options": []},
                            {"kind": "unit", "owner": -1, "options": [{}]},
                            {"kind": "unit", "options": [{}], "random_index": 0},
                            {"kind": "unit", "options": [{}], "observation": {"x": float("nan")}}):
                with self.assertRaises(ValueError):
                    policy.choose(context)


if __name__ == "__main__":
    unittest.main()
