"""Generated prices for the test suite. Nothing the app runs imports this module.

The suite is built around not touching the network — see CLAUDE.md — which means something
has to stand in for a price feed. This is that something, and it lives here rather than in
`data.py` on purpose: a generated-price mode reachable from the app is a chart of invented
numbers that looks exactly like a real one by the time it is three steps into a video
editor. Keeping the generator on the far side of an import the app never makes is what
makes that impossible rather than merely discouraged.

Two ways in, for the two kinds of test:

- `patch_fetch(case)` for anything running in this process. It replaces `data.fetch`, which
  is the seam every renderer goes through, and undoes itself on teardown.
- `seed_cache(...)` for the tests that spawn a real render subprocess. There is no flag to
  hand a child, by design, so instead its disk cache is filled in advance and the ordinary
  cache hit in `data.fetch` does the rest. That exercises a real code path rather than a
  test-only one, which is the nicer property of the two.
"""

import hashlib
import os
from unittest import mock

import numpy as np
import pandas as pd

import data

# Regular US trading hours. Only this generator needs them — a real feed decides its own
# session — but they have to be right or an offline intraday test isn't exercising the
# axis handling it thinks it is.
SESSION_OPEN = (9, 30)
SESSION_CLOSE = (16, 0)


def session_index(start, end, step):
    """Cash-hours timestamps at `step` spacing.

    The overnight holes are the point: generated intraday has to have the same shape as the
    real thing, or it won't exercise the axis handling whose whole job is closing them.
    """
    open_at = pd.Timedelta(hours=SESSION_OPEN[0], minutes=SESSION_OPEN[1])
    close_at = pd.Timedelta(hours=SESSION_CLOSE[0], minutes=SESSION_CLOSE[1])
    # Bars are labelled by the time they open, so the last one starts before the close.
    parts = [pd.date_range(d + open_at, d + close_at, freq=step, inclusive="left")
             for d in pd.bdate_range(start, end)]
    return parts[0].append(parts[1:]) if parts else pd.DatetimeIndex([])


def synthetic(ticker, start, end, interval=data.DEFAULT_INTERVAL):
    """Deterministic fake OHLCV, seeded off the ticker so a test can rely on the shape."""
    seed = int(hashlib.md5(ticker.upper().encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    step = data._spec(interval)["step"]
    idx = session_index(start, end, step) if step else pd.bdate_range(start, end)
    n = len(idx)
    if n < 2:
        raise ValueError("Date range is too short.")

    drift = rng.normal(0.0007, 0.0007)
    vol = rng.uniform(0.011, 0.028)
    if step:
        # These are per-day figures. A bar covering a fraction of a session moves by a
        # fraction of that, or 390 five-minute bars compound into nonsense.
        per_session = max(int(pd.Timedelta("6h30min") / pd.Timedelta(step)), 1)
        drift /= per_session
        vol /= np.sqrt(per_session)
    close = (20 + seed % 400) * np.exp(np.cumsum(rng.normal(drift, vol, n)))

    prev = np.concatenate([[close[0]], close[:-1]])
    open_ = prev * (1 + rng.normal(0, vol * 0.3, n))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol * 0.5, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol * 0.5, n)))
    vol_shares = rng.lognormal(15.5, 0.4, n)

    return pd.DataFrame(
        {"Open": open_, "High": hi, "Low": lo, "Close": close, "Volume": vol_shares},
        index=idx,
    )


def synthetic_fetch(ticker, start, end=None, interval=data.DEFAULT_INTERVAL,
                    sessions=None):
    """Stand in for `data.fetch`, down to the session trim and the source record.

    The recorded source matters: `attribution()` reads it, and a test that draws a frame
    and then checks the footer is checking real behaviour only if this leaves the same
    trace a real fetch would. It records "yahoo" because that is the source the footer
    stays silent about, so a chart drawn here carries no stamp it wouldn't carry live.
    """
    ticker = ticker.strip().upper()
    end = end or pd.Timestamp.today().normalize()
    frame = data._usable(synthetic(ticker, start, end, interval), sessions)
    data._SOURCES[ticker] = "yahoo"
    return frame


def patch_fetch(case):
    """Point `data.fetch` at generated prices for the length of one test case."""
    patcher = mock.patch.object(data, "fetch", synthetic_fetch)
    patcher.start()
    case.addCleanup(patcher.stop)
    case.addCleanup(data.reset_sources)


def seed_cache(cache_dir, ticker, start, end=None, interval=data.DEFAULT_INTERVAL,
               source="yahoo"):
    """Write generated prices into a cache a render subprocess will read.

    The child inherits ROLLTAPE_CACHE_DIR and nothing else, so this is how it renders
    without a network: `data.fetch` finds a cache hit and never reaches a fetcher. Returns
    the path written, mostly so a failing test can say which file it expected to be used.
    """
    os.makedirs(cache_dir, exist_ok=True)
    frame = synthetic(ticker.upper(), start, end or pd.Timestamp.today().normalize(),
                      interval)
    with mock.patch.object(data, "CACHE_DIR", cache_dir):
        path = data._cache_path(ticker, start, end, source, interval)
    frame.to_csv(path)
    return path
