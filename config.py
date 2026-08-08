"""Runtime configuration.

Every default here reproduces the local single-user setup exactly. The env vars exist so
a container host can point the writable directories at a mounted volume — nothing about a
local run reads them.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _path(env, default):
    return os.path.abspath(os.environ.get(env) or os.path.join(HERE, default))


def _flag(env):
    return (os.environ.get(env) or "").strip().lower() in ("1", "true", "yes", "on")


OUT_DIR = _path("ROLLTAPE_OUT_DIR", "outputs")
CACHE_DIR = _path("ROLLTAPE_CACHE_DIR", ".cache")

# --demo still works and wins over this. The env var is for hosts with no CLI, and it is
# also how a render subprocess inherits demo mode from the server that spawned it.
DEMO = _flag("ROLLTAPE_DEMO")
