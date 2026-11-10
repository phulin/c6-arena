import ctypes
import json
from pathlib import Path
import tempfile
import unittest

from c6_arena.inspect import iter_decisions
from c6_arena.policy import LuaPolicy, NO_QUERY_ARGUMENT


class LazyObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def session(self, source, trace=None):
        script = self.path / 'policy.lua'
        script.write_text(source)
        session = LuaPolicy(script, trace=trace)
        policy = session.__enter__()
        self.addCleanup(session.__exit__, None, None, None)
        return policy

    def context(self, revision=1, owner=0, observation=None, **extra):
        return dict(owner=owner, kind='unit', options=[{'name': 'skip'}], random_index=1,
                    observation_id=revision,
                    observation_fields=['player', 'turn', 'plots'],
                    observation=observation if observation is not None else {'player': {'science': 10}}, **extra)

    def test_unused_fields_are_not_converted_and_tables_are_retained(self):
        policy = self.session('''local old
            return function(ctx)
                local player=ctx.observation.player
                assert(player.science == 10)
                assert(ctx.world:player() == player)
                if old then assert(old == player and player.private_note == 42) end
                player.private_note=42; old=player
                return 1
            end''')
        observation = {'player': {'science': 10}, 'plots': object()}
        policy.choose(self.context(observation=observation))
        policy.choose(self.context(observation={'player': {'science': 99}, 'plots': object()}))
        self.assertEqual(observation['player'], {'science': 10})

    def test_new_revision_is_fresh_and_fetched_old_values_remain_valid(self):
        policy = self.session('''local old
            return function(ctx)
                if old then
                    assert(old.player.science == 10)
                    assert(not pcall(function() return old.plots end))
                    assert(ctx.observation.player.science == 20)
                end
                old=ctx.observation
                local player=old.player
                return 1
            end''')
        policy.choose(self.context())
        policy.choose(self.context(revision=2, observation={'player': {'science': 20}}))

    def test_same_revision_is_private_to_each_seat(self):
        policy = self.session('''return function(ctx)
            assert(ctx.observation.player.science == ctx.owner + 10)
            return 1 end''')
        policy.choose(self.context(owner=0))
        policy.choose(self.context(owner=1, observation={'player': {'science': 11}}))
        policy.choose(self.context(owner=0, observation={'player': {'science': 99}}))

    def test_target_queries_are_separate_and_cached(self):
        policy = self.session('''return function(ctx)
            local p=ctx.world:plot(4,5)
            assert(p.x==4 and p.y==5 and p.yields==nil)
            assert(ctx.world:plot(4,5)==p)
            assert(ctx.world:plot_yields(4,5).food==2)
            assert(ctx.world:city(12).population==7)
            return 1 end''')
        queries = [dict(query='plot', a=4, b=5, value={'x': 4, 'y': 5}),
                   dict(query='plot_yields', a=4, b=5, value={'food': 2}),
                   dict(query='city', a=12, b=0, value={'population': 7})]
        policy.choose(self.context(observation={}, observation_queries=queries))

    def test_pairs_and_next_explicitly_materialize_fields(self):
        policy = self.session('''return function(ctx)
            local n=0
            for k,v in pairs(ctx.observation) do n=n+1 end
            assert(n==3 and #ctx.observation.plots==2)
            assert(next(ctx.observation)~=nil)
            return 1 end''')
        policy.choose(self.context(observation={'player': {}, 'turn': 4, 'plots': [{'x': 1}, {'x': 2}]}))

    def test_unrecorded_query_fails_instead_of_inventing_data(self):
        policy = self.session('return function(ctx) local p=ctx.world:plot(4,5); return 1 end')
        with self.assertRaisesRegex(RuntimeError, 'was not recorded'):
            policy.choose(self.context(observation={}))

    def test_no_observation_reads_issue_no_queries(self):
        trace = self.path / 'unused.jsonl'
        policy = self.session('return function(ctx) assert(ctx.observation.unknown == nil); return 1 end', trace=trace)
        policy.choose(self.context(observation={'plots': object()}))
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertFalse(any(row.get('record') == 'observation' for row in rows))

    def test_missing_actor_is_cached_without_inventing_an_object(self):
        trace = self.path / 'missing.jsonl'
        policy = self.session('''return function(ctx)
            assert(ctx.world:unit(999) == nil and ctx.world:unit(999) == nil)
            return 1 end''', trace=trace)
        policy.choose(self.context(observation_queries=[dict(query='unit', a=999, b=0, value=None)]))
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(sum(row.get('record') == 'observation' for row in rows), 1)

    def test_compact_trace_reconstructs_inputs_and_replays(self):
        trace = self.path / 'trace.jsonl'
        source = 'return function(ctx) assert(ctx.observation.player.science > 0); return 1 end'
        script = self.path / 'policy.lua'
        script.write_text(source)
        with LuaPolicy(script, trace=trace) as policy:
            policy.choose(self.context(revision=7))
            policy.choose(self.context(revision=7))
            policy.choose(self.context(revision=8, observation={'player': {'science': 20}}))
        records = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(sum(row.get('record') == 'world' for row in records), 2)
        self.assertEqual(sum(row.get('record') == 'observation' for row in records), 2)
        self.assertTrue(all('observation' not in row for row in records if 'sequence' in row))
        errors = []
        decisions = list(iter_decisions(trace, errors))
        self.assertEqual(errors, [])
        self.assertEqual([row['observation']['player']['science'] for row in decisions], [10, 10, 20])
        with LuaPolicy(script) as policy:
            self.assertEqual([policy.choose(row)['selected'] for row in decisions], [1, 1, 1])

    def test_query_records_do_not_retroactively_change_prior_inputs(self):
        trace = self.path / 'trace.jsonl'
        rows = [dict(record='world', owner=0, observation_id=1, fields=['player','turn']),
                dict(record='observation', owner=0, observation_id=1, query='player',
                     a=NO_QUERY_ARGUMENT, b=NO_QUERY_ARGUMENT, value={'science': 10}),
                dict(sequence=0, owner=0, kind='unit', observation_id=1, options=[{'name':'skip'}], selected=1),
                dict(record='observation', owner=0, observation_id=1, query='turn',
                     a=NO_QUERY_ARGUMENT, b=NO_QUERY_ARGUMENT, value=42),
                dict(sequence=1, owner=0, kind='unit', observation_id=1, options=[{'name':'skip'}], selected=1)]
        trace.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        errors=[]; decisions=list(iter_decisions(trace, errors))
        self.assertEqual(errors, [])
        self.assertNotIn('turn', decisions[0]['observation'])
        self.assertEqual(decisions[1]['observation']['turn'], 42)


if __name__ == '__main__':
    unittest.main()
