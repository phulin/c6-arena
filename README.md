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
```

`run` pits your script in seat 0 against `--reference` (default: `random.lua`)
in seat 1. Both NPC seats explicitly use `random.lua`. `compare` runs each seed
twice with major seats swapped. `schedule --seeds 1 2 3` previews those pairings.
Use `--harness /path/to/c6-native-arena` to select a different checkout.
The native harness requires its compiled inputs to be committed before launch.
The runner uses the harness's compiler cache when `ccache` is available.

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
| `unit` | `name` (`skip`, `fortify`, `found_city`, `move`), `actor`, `value` (operation hash), optional origin and target coordinates |
| `production` | `name` (`UNIT_SCOUT`, `UNIT_WARRIOR`, etc.), `value` (definition index), city `x`, `y` |
| `research`, `civic`, `commemoration` | `name` (original definition type, e.g. `TECH_WRITING`), `value` (native definition index) |

All options also have `index`. Coordinates are flat `x`, `y`, `origin_x`,
`origin_y` fields, omitted when unknown. Each seat has its own Lua VM. Basic
Lua, table, string, and math functions are available; file/process/package/debug
APIs are unavailable. Each call has a 1,000,000-instruction budget and each VM a
16 MiB allocation limit. Invalid choices and script errors stop the run.
These limits catch ordinary script mistakes; this is not isolation for hostile
code. Scripts are text, not Lua bytecode.

The native harness enumerates legal choices and executes the selected native
operation. Its existing hooks suppress supported built-in policy selection.
The Lua bridge receives copied scalar choices, never raw engine pointers. The
stable ABI is `native/policy_api.h`, mirrored by the harness's
`native/external_policy.h` (native indices are zero based). Without a plugin,
the harness retains its original random policy.

Scripts can also return a second value describing the decision:
`return index, "defend the launch city"`. It appears as `reason` in native traces
and replay results. Older scripts returning just an index still work.

## Science strategy

`policies/science.lua` is a complete decision policy targeting a science victory.
It establishes cities and scouts, limits surplus settlers, develops science and
production, responds to war and economic trouble, then prioritizes spaceports,
satellite/moon/Mars/exoplanet projects and expedition acceleration. It includes
combat, healing and retreat, builder improvements, trade routes, diplomacy,
government/policy/governor choices, great people, Congress, purchases, citizen
assignments and a modest religion policy. Movement remembers recent positions
to avoid loops. State is private per seat; edits go directly in the Lua script.

The strategy works with the current native choices and named definitions. Its
later-game handlers have **scenario tests, not full-game validation**: the native
adapter still lacks most later-game action enumeration and observations. The
replay command exercises supplied legal-choice contexts using the same Lua VM;
it does not run a game or establish that those options are natively available.

## Rich decision contexts

The optional `native/context_api.h` interface copies nested objects and arrays
into Lua. The original native choice ABI remains supported. `c6_arena.policy.LuaPolicy`
uses the richer interface for decision tests and ordered replay, retaining the
script's memory between calls. Contexts have `kind`, `owner`, `options`, and
`random_index`, plus an optional `observation`. Arrays are one based in Lua;
JSON null values become nil. Limit contexts to depth 32 and 50,000 values.

The science script accepts these observation fields when an adapter can supply
them from information available to that seat:

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

Every run keeps copies of its Lua files, the bridge, script hashes, native
outputs, and `decisions.jsonl`. The trace records each offered choice and the
selection. `inspect` summarizes decisions by seat and highlights cases where
FoundCity was available but delayed. Those are prompts for inspection, not
automatic declarations of mistakes.

`reflect` produces a compact JSON packet containing the script and summary for
an LLM to edit. Read the referenced trace for more context, save a modified
script, commit it, then run `compare` against its parent. This initial version
does not call a paid model service or automatically promote candidates.

## Current scope

The native harness supports bounded early-game play, not complete Civ VI games.
This runner caps games at 100 rounds and stops on unsupported decisions. A
successful horizon is reported as `truncated`; incomplete execution is
`coverage_failure`. Neither is recorded as a policy win or loss.

For an explicit boundary-discovery run, `run --explore --rounds 500` permits a
longer requested horizon (up to 1000). This does not add decision coverage or
victory detection; unsupported branches still stop and no winner is inferred.

The native API exposes the existing legal-choice subset, not a complete map or
player observation. It covers unit orders, production, research, civics and era
choices. World Congress and other unsupported branches still stop explicitly.
City/unit totals currently combine both major seats, so comparisons retain
diagnostics and traces without inventing a competitive score. Full observations,
per-seat outcomes, checkpoint branching and automatic LLM mutation are future
extensions to this shared framework.

Native build caches and outputs stay ignored. The tests use real Lua against
synthetic requests and do not need proprietary assets or sibling repositories.
