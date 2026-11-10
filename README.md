# Civ AI arena

An arena for evolving plain Lua AIs against the original Civ VI engine in
`../c6-native-arena`. Python runs experiments; one reusable C/Lua bridge turns
native legal choices into Lua calls. Strategy changes are edits to Lua files.

This is an early implementation. The native harness supports bounded early-game
play, not complete Civ VI games. Unsupported decisions stop the run.
