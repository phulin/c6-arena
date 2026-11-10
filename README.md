# Civ AI arena

An arena for evolving plain Lua AIs against the original Civ VI engine in
`../c6-native-arena`. Python runs experiments; one reusable C/Lua bridge turns
native legal choices into Lua calls. Strategy changes are edits to Lua files.

The framework is shared; changing a strategy means editing a Lua file. The bridge
build is cached by its source hash and reused across scripts and games.

## Start

Requires Linux x86-64, Python 3.11+, a C compiler and the LuaJIT 2 runtime
(`libluajit-5.1.so.2`, Ubuntu package `libluajit-5.1-2`). Native games also need
the sibling harness, its pinned sources and local game assets. No game assets
are included here. Run these commands from this checkout:

```sh
python3 -m c6_arena build
python3 -m unittest discover -s tests -v
python3 -m c6_arena run policies/settle.lua --rounds 10 --seed 1 --output runs/settle-1
python3 -m c6_arena inspect runs/settle-1
python3 -m c6_arena reflect runs/settle-1
python3 -m c6_arena compare policies/settle.lua --seeds 1 2 3 --output runs/settle-v-random
python3 -m c6_arena replay policies/science.lua examples/science_decisions.json
python3 -m c6_arena run policies/science.lua --until-victory --output runs/full-game
```

To exercise late-game decisions sooner, add `--start-era ERA_INFORMATION`.
This selects the original game's starting-era configuration, including its
starting technologies and units. Results record the selected era; this is a
different match from an Ancient-era start. `--regional-contact` selects the
contact fixture used for focused diplomacy checks.

`run` pits your script in seat 0 against `--reference` (default: `random.lua`)
in seat 1. Barbarians, city states and Free Cities use the built-in AI, including
its native planning and turn processing. Lua scripts control the two competing
civilizations. `compare` runs each seed
twice with major seats swapped. `schedule --seeds 1 2 3` previews those pairings.
Use `--harness /path/to/c6-native-arena` to select a different checkout.
The native harness requires its compiled inputs to be committed before launch.
The runner uses the harness's compiler cache when `ccache` is available.

Full runs default to `--acceleration fast`, which reuses the harness's getter, call-binding, map and
appeal optimizations. NPC planning, caches and turn processing remain native.
The appeal replacement applies within its supported citizen-query scope and
falls back to the original calculation elsewhere. The run records its selected
profile and native command in `match.json`; `--acceleration native` is the
reference configuration.

## A strategy is a Lua function

```lua
local decisions = 0 -- private memory, retained for this seat during the game
return function(ctx)
    decisions = decisions + 1
    if ctx.kind == "unit" then
        for i, option in ipairs(ctx.options) do
            if option.name == "found_city" then return i end
        end
    end
    return ctx.random_index
end
```

`ctx` contains `owner`, `kind`, `options`, and `random_index`. Return a **one-based
index** into `options`. The fallback index comes from the harness policy RNG;
Lua's unseeded random functions are unavailable.

| Decision kind | Option fields |
| --- | --- |
| `unit_selection` | Full-game mode: ready unit `name=UNIT_*`, `actor`, native type index and known coordinates; choose a unit before its command |
| `unit` | Native civilian, military, religious, spy and cultural orders; actor, parameters, origin and target coordinates |
| `production`, `production_control` | All loaded unit, building, district and project definitions; placement, formation, city ID; KEEP/CHANGE for existing queues |
| `research`, `civic`, `commemoration` | `name` (original definition type, e.g. `TECH_WRITING`), `value` (native definition index) |
| `government`, `policy`, `religion` | Native government, policy, or pantheon belief name and definition index; policy `actor` identifies its slot |
| `governor` | Governor/promotion type name, or `ASSIGN_GOVERNOR` with governor actor, city ID, and city coordinates |
| `congress` | Legal resolution targets, discussion vote directions, vote counts, or results acknowledgement |
| `government_optional`, `policy_optional` | Government changes, policy unlocks, and complete policy slates with confirmation |
| `city_economy`, `city_purchases`, `city_citizens`, `city_yield_focus` | Gold/faith purchases, worker locks, and yield preferences; PASS/BACK navigation |
| `player_action` | Great Person recruitment/patronage/rejection, envoys, and minor diplomacy/levies |
| `diplomacy`, `diplomacy_*` | Sessions, responses and typed deal editing, answered on the recipient's native turn |
| `great_work`, `spy_escape` | Artifact/Great Work choices and native escape routes |

All options also have `index`. Coordinates are flat `x`, `y`, `origin_x`,
`origin_y` fields, omitted when unknown. Each seat has its own Lua VM. Basic
Lua, table, string, and math functions are available; file/process/package/debug
APIs are unavailable. Each call has a 1,000,000-instruction budget and each VM a
64 MiB allocation limit. Invalid choices and script errors stop the run.
These limits catch ordinary script mistakes; this is not isolation for hostile
code. Scripts are text, not Lua bytecode.

The native harness enumerates legal choices and executes the selected native
operation. Hooks reserve human-major choices for Lua and retain built-in NPC AI.
Full mode supplies `ctx.observation` and descriptive `option.metadata`, including
purchase costs, Great Person classes, promotion/improvement names, government
comparison/anarchy, Congress targets, and deal amount ranges.
The current bridge fetches observation fields when Lua first reads them and
retains their Lua tables for that seat and world revision. Menu navigation
shares a revision; actions, engine updates and uncertain transitions invalidate
it. Fetched tables can be retained as past data. Reading an unfetched field from
an older revision raises an error.

For targeted queries, use `ctx.world:city(id[, owner])`, `:unit(id[, owner])`,
`:plot(x, y)`, or `:plot_yields(x, y)`. `:plots_basic()` returns revealed plot
records without yield calculations. Repeated queries share cached results in
the same revision. The compatibility field `ctx.observation.plots` still
requests the complete plot view; iterating all of `ctx.observation` with
`pairs` or `next` explicitly fetches every field.
The Lua bridge receives copied scalar choices, never raw engine pointers. The
stable ABI is `native/policy_api.h`, mirrored by the harness's
`native/external_policy.h` (native indices are zero based). Without a plugin,
the harness retains its original random policy.

Scripts can also return a second value describing the decision:
`return index, "defend the launch city"`. It appears as `reason` in native traces
and replay results. Older scripts returning just an index still work.

## Science strategy

`policies/science.lua` is an initial science-oriented policy that can run a full
native game. It expands, selects research and civics, and handles the adapter's
unit and mandatory decisions. Movement remembers recent positions to avoid
loops. State is private per seat; edits go directly in the Lua script.

Native production enumerates all loaded definitions, including Campuses,
science buildings, Spaceports and space projects. Districts and wonders include
legal placement choices, projects retain native restrictions, and units include
corps/army variants. Existing queues can be retained or replaced.

The strategy uses native observations and named definitions. Scenario tests also
exercise individual decisions through the same Lua VM. Replay exercises supplied
contexts; it does not run a game or establish a victory.

## Rich decision contexts

The `native/context_api.h` interface copies nested objects and arrays into Lua
and is used by the full native adapter. The original choice ABI remains supported
for bounded compatibility checks. `c6_arena.policy.LuaPolicy`
uses the richer interface for decision tests and ordered replay, retaining the
script's memory between calls. Contexts have `kind`, `owner`, `options`, and
`random_index`, plus an optional `observation`. Arrays are one based in Lua;
JSON null values become nil. Limit contexts to depth 32 and 250,000 values.

Native snapshots include actual turn, economy, owned cities/queues/buildings,
unit lifecycle and combat statistics, known technologies/civics, science victory
progress, known players and visible foreign actors. Revealed plots include
static terrain; current plot details are supplied on visible or owned plots.
Unknown and hidden information stays omitted. The script also accepts these
optional descriptive fields:

| Field | Meaning |
| --- | --- |
| `turn`, `known_technologies` | Actual turn and a set such as `{"TECH_ROCKETRY": true}` |
| `player` | `city_count`, `treasury`, `gold_per_turn`, `military_strength`, `enemy_strength`, `threat`, `at_war`, `amenities`, `trade_routes_available`, `favor`, `exoplanet_launched` |
| `cities` | Records with `id`, `x`, `y`, `population`, `housing`, `amenities`, `production`, `under_siege`, `border`, `unimproved_worked_tiles`, `power_shortfall`, and a `buildings` set |
| `units` | Records with `id`, `type`, `role`, `health`, `max_health`; roles include `settler`, `builder`, `trader`, `garrison` |
| `expansion_sites` | Number of known viable expansion sites; omitted when unknown |

Options retain their original names and may add details relevant to that choice:
`city_id`, `adjacency`, `production_turns`, `boosted`, `turns_to_complete`,
`gold_cost`, `threat`, visible yield changes, combat damage estimates, target
health, route yields, or explicit effects such as `protects_spaceport`.
Movement can include `unrevealed_neighbors`, `goody_hut`, `destination_distance`,
`origin_distance`, `retreats`, `defends_city`, and `escorts_settler`. Health and
damage use native hit points; `threat` is a nonnegative relative danger estimate.
Fields describe visible state or labeled estimates, never referee-only or
future game information. Unknown fields stay omitted.

Additional decision kinds currently handled by the science script are
`diplomacy`, `trade`, `government`, `policy`, `governor`, `great_person`,
`congress`, `city_citizens`, `purchase`, and `religion`. Options must be complete,
already-legal choices supplied by the adapter. An unknown decision kind raises
an error. This interface supplies a way to test and extend policies; it does
not bypass any unsupported native decision.

## Inspect and mutate

Every run keeps copies of its major-seat Lua files, the bridge, script hashes, native
outputs, and `decisions.jsonl`. The trace records each offered choice and the
selection. `inspect` summarizes decisions by seat and highlights cases where
FoundCity was available but delayed. Those are prompts for inspection, not
automatic declarations of mistakes.
Demand-driven traces store a world declaration and each fetched query result
once, followed by decisions referencing that revision. `inspect`, `reflect`
and replay resolve those references. Replay reports an error if a changed
script requests data that the original run never fetched.

`policy-timing.jsonl` records cumulative CPU timings per seat while the game
runs: script/VM loading, Lua table construction, protected Lua execution, trace
writing, native demand-query execution, and the complete bridge call. Loading happens once per seat; the returned
Lua function runs for each decision. These counters exclude native observation
construction for the legacy v1 interface. V2 records demand-query CPU separately
within the bridge call; the harness's broader policy timing includes both paths.

`inspect` also exposes `choice_profile`: cumulative CPU and native-check counts
for actor selection, unit/city menus and command execution. Enumeration timings
exclude Lua and observation callbacks. Unit execution can include nested native
callbacks, so its timer should not be added to the enumeration subtotal.

`reflect` produces a compact JSON packet containing the script and summary for
an LLM to edit. Read the referenced trace for more context, save a modified
script, commit it, then run `compare` against its parent. This initial version
does not call a paid model service or automatically promote candidates.

## Current scope

Ordinary bounded runs allow up to 100 rounds. A successful horizon is reported
as `truncated`; incomplete execution is `coverage_failure`. Neither is a win.

`run --until-victory` requires the native victory manager to record a winning
team and victory type, followed by clean native shutdown. The summary then says
`finished` and identifies `winner`, `winner_policy`, `winner_team`, and
`victory_name`. It defaults to a safety budget of 1000 activation pairs, including
interleaved Congress phases. This budget does not change the original game's
turn limit or victory rules. Reaching the budget without a winner is a failure.
Full-game runs use Lua per-unit scheduling to avoid repeated path queries for
unselected units, and default to an 1800-second execution limit. Use `--timeout`
to select a budget up to 3600 seconds for native NPC planning and long games.

For an explicit boundary-discovery run, `run --explore --rounds 500` permits a
longer requested horizon (up to 1000). Unsupported branches still stop and no
winner is inferred from diagnostics or the requested horizon.

The default `full` adapter targets standard XP2 gameplay with the loaded rules.
It covers production, unit commands/operations, government/policies/governors,
religion, culture, diplomacy/deals, purchases/citizens, research and Congress.
Reopened human turns return to Lua for further orders. Native AI controls NPCs.
Scripted scenario/DLC-specific actions and native strategic automation are
outside this catalog; unknown branches stop explicitly. `--adapter limited`
retains the earlier bounded major action pool for debugging. City/unit totals
combine both major seats and never determine the winner. Checkpoint branching
and automatic LLM mutation remain future extensions.

Native build caches and outputs stay ignored. The tests use real Lua against
synthetic requests and do not need proprietary assets or sibling repositories.

Use `--city-frame shadow` to compare the city economic kernel with native results,
or `--city-frame fast` to execute its admitted building, district and entertainment
paths. The default is `native`. Tile calculations retain their native fallback
with the full NPC profile. This setting is recorded in each match.
Science strategy helpers fetch observations on demand; unit orders query their
actor directly, and explanations only include phase/threat when scoring needed them.
