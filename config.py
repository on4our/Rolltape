"""Runtime configuration.

Every default here reproduces the local single-user setup exactly. The env vars exist so
the same code can boot somewhere with a read-only filesystem and no persistent disk —
nothing about a local run reads them.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _path(env, default):
    return os.path.abspath(os.environ.get(env) or os.path.join(HERE, default))


def _flag(env):
    return (os.environ.get(env) or "").strip().lower() in ("1", "true", "yes", "on")


OUT_DIR = _path("ROLLTAPE_OUT_DIR", "outputs")
CACHE_DIR = _path("ROLLTAPE_CACHE_DIR", ".cache")

# Backend selection. A local run never needs anything but these two.
STORAGE = (os.environ.get("ROLLTAPE_STORAGE") or "local").strip().lower()
JOBS = (os.environ.get("ROLLTAPE_JOBS") or "memory").strip().lower()

# --demo still works and wins over this; the env var is for hosts with no CLI.
DEMO = _flag("ROLLTAPE_DEMO")
