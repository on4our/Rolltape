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

# --- the price feed --------------------------------------------------------
# The licensed sources, in the order data.SOURCES tries them. Set a key and that feed
# answers; leave both unset and the app falls back to the scraped sources exactly as it
# always did, which is what keeps a fresh clone useful before anyone has signed up for
# anything.
#
# The render subprocess inherits these from the environment it is spawned into, so there is
# nothing to hand across the process boundary.
FMP_KEY = (os.environ.get("ROLLTAPE_FMP_KEY") or "").strip()
TWELVEDATA_KEY = (os.environ.get("ROLLTAPE_TWELVEDATA_KEY") or "").strip()

# --- economic data ---------------------------------------------------------
# FRED, the St. Louis Fed's series database. Not an alternative to the feeds above and
# never a fallback for one: it answers for CPI and the unemployment rate, which none of
# them carries, and they answer for tickers, which it doesn't. Without this key the
# economic symbols simply aren't offered — every price chart works exactly as before.
# The key is free from fred.stlouisfed.org/docs/api/api_key.html.
FRED_KEY = (os.environ.get("ROLLTAPE_FRED_KEY") or "").strip()


def _int(env, default):
    try:
        return int(os.environ.get(env) or default)
    except ValueError:
        return default


# How far back the FMP plan reaches. Starter is five years; Professional is thirty. This is
# a plan property rather than an API one, so it cannot be discovered at runtime — a request
# past the horizon comes back short rather than refused, which would put a five-year chart
# under a MAX label. data.py drops the source instead, so set this to match the plan you
# are actually paying for.
FMP_HISTORY_YEARS = _int("ROLLTAPE_FMP_HISTORY_YEARS", 5)

# Refuse the scraped sources entirely. yfinance scrapes Yahoo and Stooq's terms are no
# better, so neither can be behind data shown to someone who paid for it — a deployment
# that takes money sets this, and a missing key then fails the render instead of quietly
# reaching for Yahoo and putting the licence question back. Off by default: a laptop
# rendering for its owner is the case the fallbacks exist for.
LICENSED_ONLY = _flag("ROLLTAPE_LICENSED_ONLY")

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
