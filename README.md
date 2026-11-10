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
```

`run` pits your script in seat 0 against `--reference` (default: `random.lua`)
in seat 1. Both NPC seats explicitly use `random.lua`. `compare` runs each seed
twice with major seats swapped. `schedule --seeds 1 2 3` previews those pairings.
Use `--harness /path/to/c6-native-arena` to select a different checkout.
The native harness requires its compiled inputs to be committed before launch.

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
| `research`, `civic`, `commemoration` | `value` (native definition index); names are currently empty |

All options also have `index`. Coordinates are flat `x`, `y`, `origin_x`,
`origin_y` fields, omitted when unknown. Each seat has its own Lua VM. Basic
Lua, table, string, and math functions are available; file/process/package/debug
APIs are unavailable. Each call has a 200,000-instruction budget and each VM a
16 MiB allocation limit. Invalid choices and script errors stop the run.
These limits catch ordinary script mistakes; this is not isolation for hostile
code. Scripts are text, not Lua bytecode.

The native harness enumerates legal choices and executes the selected native
operation. Its existing hooks suppress supported built-in policy selection.
The Lua bridge receives copied scalar choices, never raw engine pointers. The
stable ABI is `native/policy_api.h`, mirrored by the harness's
`native/external_policy.h` (native indices are zero based). Without a plugin,
the harness retains its original random policy.

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

The first API exposes the existing legal-choice subset, not a complete map or
player observation. It covers unit orders, production, research, civics and era
choices. World Congress and other unsupported branches still stop explicitly.
City/unit totals currently combine both major seats, so comparisons retain
diagnostics and traces without inventing a competitive score. Full observations,
per-seat outcomes, checkpoint branching and automatic LLM mutation are future
extensions to this shared framework.

Native build caches and outputs stay ignored. The tests use real Lua against
synthetic requests and do not need proprietary assets or sibling repositories.
