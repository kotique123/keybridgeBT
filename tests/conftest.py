"""
Pytest configuration for keybridgeBT tests.

Adds both mac-sender and win-receiver to sys.path and provides shared
fixtures used across test modules.
"""

import sys
import os
import types

# Allow importing keybridgebt_mac and keybridgebt_win from their source trees
repo_root = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(repo_root, "mac-sender"))
sys.path.insert(0, os.path.join(repo_root, "win-receiver"))

# ---------------------------------------------------------------------------
# Minimal stubs for platform-only imports so the tests can run on macOS or
# Windows without the opposing platform's native libraries being installed.
# ---------------------------------------------------------------------------

def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return mod


# Stub ctypes.windll so win-receiver injectors can be imported on macOS
if not hasattr(sys.modules.get("ctypes", __import__("ctypes")), "windll"):
    import ctypes
    ctypes.windll = types.SimpleNamespace(
        user32=types.SimpleNamespace(SendInput=lambda *a: len(a[1]))
    )
else:
    # Replace SendInput with an identity stub if windll already exists but
    # we are not on Windows (prevents actual injection during tests)
    import ctypes
    try:
        ctypes.windll.user32.SendInput = lambda n, arr, sz: n
    except AttributeError:
        pass
