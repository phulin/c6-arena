"""Exercise the same Lua policy runtime with complete decision contexts.

These calls test decisions; they do not simulate Civ turns or create legal actions.
"""
from __future__ import annotations

import ctypes as ct
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import threading

from .native import build_bridge


class Value(ct.Structure):
    pass


Value._fields_ = [("key", ct.c_char_p), ("type", ct.c_uint32), ("number", ct.c_double),
                  ("text", ct.c_char_p), ("count", ct.c_uint32), ("children", ct.POINTER(Value))]
_runtime_lock = threading.Lock()


def encode(value, key=None, keep=None, depth=0):
    keep = keep if keep is not None else []
    if depth > 32:
        raise ValueError("context exceeds maximum depth")
    result = Value()
    if key is not None:
        if not isinstance(key, str) or "\0" in key:
            raise ValueError("context keys must be strings without NUL bytes")
        result.key = key.encode()
    if value is None:
        result.type = 0
    elif isinstance(value, bool):
        result.type, result.number = 1, int(value)
    elif isinstance(value, (float, int)):
        if not math.isfinite(value) or isinstance(value, int) and abs(value) > 2**53:
            raise ValueError("context numbers must be finite and exactly representable")
        result.type, result.number = 2, value
    elif isinstance(value, str):
        if "\0" in value:
            raise ValueError("context strings cannot contain NUL bytes")
        result.type, result.text = 3, value.encode()
    elif isinstance(value, (dict, list)):
        result.type = 5 if isinstance(value, dict) else 4
        items = value.items() if isinstance(value, dict) else ((None, item) for item in value)
        children = [encode(item, name, keep, depth+1) for name, item in items]
        array = (Value * len(children))(*children)
        keep.extend([array, *children])
        result.count, result.children = len(children), array
    else:
        raise ValueError(f"unsupported context value: {type(value).__name__}")
    keep.append(result)
    return result


@contextmanager
def environment(updates):
    previous = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class LuaPolicy:
    """One synchronous policy session per Python interpreter; per-seat Lua memory."""

    def __init__(self, script: Path):
        self.script = script.resolve()
        self.lib = None

    def __enter__(self):
        if not self.script.is_file():
            raise FileNotFoundError(self.script)
        if not _runtime_lock.acquire(blocking=False):
            raise RuntimeError("another Lua policy session is active")
        try:
            self.lib = ct.CDLL(str(build_bridge()))
            self.lib.c6_arena_choose_context_v1.argtypes = [ct.c_int32, ct.POINTER(Value), ct.POINTER(ct.c_uint32)]
            self.lib.c6_arena_choose_context_v1.restype = ct.c_int
            self.lib.c6_arena_last_reason_v1.restype = ct.c_char_p
            self.lib.c6_arena_policy_reset_v1()
            return self
        except BaseException:
            _runtime_lock.release()
            raise

    def __exit__(self, *error):
        self.lib.c6_arena_policy_reset_v1()
        self.lib = None
        _runtime_lock.release()

    def choose(self, context: dict) -> dict:
        if self.lib is None:
            raise RuntimeError("use LuaPolicy as a context manager")
        owner = context.get("owner", 0)
        options = context.get("options", [])
        if type(owner) is not int or not 0 <= owner < 64 or not options or len(options) > 4096:
            raise ValueError("a context needs owner 0..63 and 1..4096 options")
        if not all(isinstance(option, dict) for option in options):
            raise ValueError("options must be objects")
        if not isinstance(context.get("kind"), str) or not context["kind"]:
            raise ValueError("context needs a decision kind")
        random = context.get("random_index", 1)
        if type(random) is not int or not 1 <= random <= len(options):
            raise ValueError("random_index must be a one-based option index")
        request = {**context, "owner": owner, "random_index": random}
        keep = []
        tree = encode(request, keep=keep)
        selected = ct.c_uint32(2**32-1)
        with environment({f"C6_ARENA_LUA_POLICY_{owner}": str(self.script),
                          "C6_ARENA_POLICY_TRACE": None}):
            status = self.lib.c6_arena_choose_context_v1(owner, ct.byref(tree), ct.byref(selected))
        if status:
            raise RuntimeError(f"Lua policy failed with status {status}")
        return {"selected": selected.value+1, "option": options[selected.value],
                "reason": self.lib.c6_arena_last_reason_v1().decode()}


def replay(script: Path, path: Path) -> list[dict]:
    """Replay supplied decision contexts in order, preserving Lua memory."""
    contexts = json.loads(path.read_text())
    if not isinstance(contexts, list):
        contexts = [contexts]
    with LuaPolicy(script) as policy:
        return [policy.choose(context) for context in contexts]
