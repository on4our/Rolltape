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
# Brand kits are the one bit of state meant to outlive a restart. See presets.py.
PRESETS_PATH = _path("ROLLTAPE_PRESETS", "presets.json")

# --demo still works and wins over this. The env var is for hosts with no CLI, and it is
# also how a render subprocess inherits demo mode from the server that spawned it.
DEMO = _flag("ROLLTAPE_DEMO")

# --- the public face -------------------------------------------------------
# Off by default, so a local run still gets the app on "/" and never sees a marketing
# page in front of its own tool. A public host turns this on and the pair swaps: "/" is
# the landing page and the app moves to "/app".
LANDING = _flag("ROLLTAPE_LANDING")

# Where the landing page's primary call to action points. The default is the app this
# same process is already serving, which is the right answer for the demo instance —
# there, the landing page and the thing it advertises are one container. Point it
# elsewhere once the demo and the marketing page stop sharing a host.
DEMO_URL = (os.environ.get("ROLLTAPE_DEMO_URL") or "/app").strip()

# Signups POST here when set — a Buttondown, ConvertKit or Formspree endpoint. Without
# one they append to a local file, which is fine on a laptop and lossy in a container
# that restarts, so set this before pointing anyone at the page.
SIGNUP_URL = (os.environ.get("ROLLTAPE_SIGNUP_URL") or "").strip()
SIGNUPS_PATH = _path("ROLLTAPE_SIGNUPS", "signups.jsonl")

# Rendered showcase stills. Generated on demand and reused, so this wants to be on the
# same writable volume as the outputs rather than in the image.
EXAMPLES_DIR = _path("ROLLTAPE_EXAMPLES_DIR", ".examples")
