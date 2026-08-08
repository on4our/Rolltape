"""Price data: Yahoo first, Stooq when Yahoo breaks, cached to disk, plus a demo mode."""

import glob
import hashlib
import io
import os
import urllib.request

import numpy as np
import pandas as pd

import config

CACHE_DIR = config.CACHE_DIR
_DEMO = False

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

SOURCES = ("yahoo", "stooq")

# Yahoo keeps intraday history for a while and then drops it, by a different amount per
# interval — minute bars for a week, five-minute for two months. A chart asking for more
# gets silence rather than an error, so the ceiling is enforced before the request.
DEFAULT_INTERVAL = "1d"

INTERVALS = {
    "1d": {"label": "Daily", "days": None, "step": None},
    "1m": {"label": "1 minute", "days": 7, "step": "1min"},
    "5m": {"label": "5 minutes", "days": 60, "step": "5min"},
    "15m": {"label": "15 minutes", "days": 60, "step": "15min"},
    "30m": {"label": "30 minutes", "days": 60, "step": "30min"},
    "1h": {"label": "1 hour", "days": 730, "step": "1h"},
}


def _spec(interval):
    return INTERVALS.get(interval) or INTERVALS[DEFAULT_INTERVAL]


def is_intraday(interval) -> bool:
    return _spec(interval)["step"] is not None


def max_lookback_days(interval):
    """How far back this interval can reach, or None when there's no limit."""
    return _spec(interval)["days"]


def intraday_available() -> bool:
    """Intraday needs Yahoo, and Yahoo needs yfinance to be installed.

    Stooq covers a daily chart when yfinance is missing, so the app still runs without it
    and only this one feature goes. Better to tell the interface up front than to let
    someone build a 5-minute chart and hit a failed render at the end of it.
    """
    import importlib.util

    try:
        return importlib.util.find_spec("yfinance") is not None
    except (ImportError, ValueError):  # a half-installed package shouldn't 500 /api/meta
        return False


def periods_per_year(interval) -> float:
    """Bars in a trading year, for annualising a volatility figure.

    252 sessions, times however many bars fill one. Annualising intraday returns with the
    daily 252 understates volatility by the square root of the bars per session.
    """
    step = _spec(interval)["step"]
    if step is None:
        return 252.0
    return 252.0 * max(pd.Timedelta("6h30min") / pd.Timedelta(step), 1.0)


def _sources_for(interval):
    """Stooq serves daily bars and coarser, so intraday is Yahoo or nothing.

    Falling through would answer a five-minute chart with daily bars and label it
    five-minute. A failed render is recoverable; a wrong one that looks right is not.
    """
    return SOURCES if not is_intraday(interval) else ("yahoo",)


# Which source answered for each ticker in the current render. One module-level record is
# safe without locking: a render has this module to itself in its own process, and the
# previews left in the server are serialised by app.py's DRAW_LOCK.
_SOURCES = {}

SOURCE_LABELS = {"stooq": "Data: Stooq", "demo": "Demo data"}

# Regular US trading hours. Only the demo generator needs these — a real feed decides its
# own session — but they have to be right or an offline intraday preview looks wrong.
SESSION_OPEN = (9, 30)
SESSION_CLOSE = (16, 0)

# Quick-pick windows for the date selector, resolved against today. `short` is what fits on
# a button, `label` is what it means; the UI reads both from /api/meta so adding a preset
# here is the whole change. `days` counts back from today, and an entry that names an
# `interval` other than "1d" is asking for intraday bars.
RANGES = {
    "1d": {"short": "1D", "label": "Intraday", "days": 5,
           "interval": "5m", "sessions": 1},
    "1w": {"short": "1W", "label": "Last week", "days": 7},
    "1m": {"short": "1M", "label": "Last month", "days": 31},
    "3m": {"short": "3M", "label": "Last 3 months", "days": 92},
    "6m": {"short": "6M", "label": "Last 6 months", "days": 183},
    "ytd": {"short": "YTD", "label": "Year to date", "ytd": True},
    "1y": {"short": "1Y", "label": "Last year", "days": 365},
    "3y": {"short": "3Y", "label": "Last 3 years", "days": 1095},
    "5y": {"short": "5Y", "label": "Last 5 years", "days": 1826},
    "10y": {"short": "10Y", "label": "Last 10 years", "days": 3653},
    "max": {"short": "MAX", "label": "Everything the source has", "from": "1970-01-01"},
}


def interval_minutes(interval):
    """Bar length in minutes, or None for daily bars.

    Derived from INTERVALS rather than parsed off the name: "1h" doesn't end in "m" and a
    string check quietly called it daily, which is how an hourly request reaches a
    daily-only source.
    """
    step = _spec(interval)["step"]
    return int(pd.Timedelta(step) / pd.Timedelta("1min")) if step else None


def resolve_range(name, today=None):
    """Turn a preset name into the window a fetch needs.

    `end` stays None on purpose: Yahoo treats an explicit end as exclusive, so pinning it to
    today would drop today's bar — the one a "year to date" chart is being made for.
    """
    spec = RANGES.get(name)
    if not spec:
        raise ValueError(f"Unknown date range: {name}")
    today = (pd.Timestamp(today) if today is not None else pd.Timestamp.today()).normalize()

    if spec.get("ytd"):
        start = today.replace(month=1, day=1)
    elif spec.get("from"):
        start = pd.Timestamp(spec["from"])
    else:
        start = today - pd.Timedelta(days=spec["days"])

    return {"start": start.strftime("%Y-%m-%d"), "end": None,
            "interval": spec.get("interval", "1d"), "sessions": spec.get("sessions")}


def window(cfg):
    """Fetch keywords for the window a config asks for.

    Renderers pass this straight through, so a new setting reaches every chart type without
    six call sites having to learn about it.
    """
    return {"start": cfg["start"], "end": cfg.get("end"),
            "interval": cfg.get("interval", "1d"), "sessions": cfg.get("sessions")}


def set_demo(flag: bool):
    global _DEMO
    _DEMO = bool(flag)


def is_demo() -> bool:
    return _DEMO


def reset_sources():
    _SOURCES.clear()


def sources_used():
    """Distinct sources that answered since the last reset."""
    return set(_SOURCES.values())


def attribution():
    """Footer note for the current render, or None when everything came from Yahoo.

    Yahoo is the assumed default, so it stays silent — a note only appears when the data
    isn't what the viewer would assume, which is exactly when it matters.
    """
    for key in ("demo", "stooq"):  # demo wins; it's the more surprising of the two
        if key in sources_used():
            return SOURCE_LABELS[key]
    return None


def _now():
    return pd.Timestamp.now()


def _today():
    return _now().normalize()


def _is_open_ended(end) -> bool:
    """True when the requested range runs up to now, so the data is still being written.

    An end date of today is no different from no end date at all — both mean "through the
    latest bar", and the latest bar changes.
    """
    if not end:
        return True
    try:
        return pd.Timestamp(end).normalize() >= _today()
    except (ValueError, TypeError):
        return False  # unparseable; fetch will complain about it in a more useful place


def _fresh_until(interval):
    """The window during which a fetched frame is still current.

    Daily bars settle once a day, so the date is the whole answer. An intraday bar is
    replaced every `interval`, so the stamp has to move at that cadence — otherwise a
    five-minute chart opened at the bell is still being served at the close.
    """
    if not is_intraday(interval):
        return str(_today().date())
    return _now().floor(_spec(interval)["step"]).strftime("%Y-%m-%dT%H%M")


def _cache_key(ticker, start, end, interval=DEFAULT_INTERVAL):
    # The default is left out of the hash so entries written before intraday existed keep
    # their names — which is also what lets _drop_superseded still find and retire them.
    suffix = "" if interval == DEFAULT_INTERVAL else f"|{interval}"
    return hashlib.md5(f"{ticker}|{start}|{end}{suffix}".encode()).hexdigest()[:16]


def _cache_path(ticker, start, end, source, interval=DEFAULT_INTERVAL):
    """Where a frame for this request is cached.

    A closed historical range never changes, so it is cached under the request alone. An
    open-ended one keeps gaining bars, so the window it was fetched in is part of its
    identity — without that, a chart rendered now is served a file written earlier and
    silently ends on a stale bar.
    """
    stamp = f"{_fresh_until(interval)}." if _is_open_ended(end) else ""
    key = _cache_key(ticker, start, end, interval)
    return os.path.join(CACHE_DIR, f"{ticker.upper()}_{key}.{stamp}{source}.csv")


def _drop_superseded(ticker, start, end, interval=DEFAULT_INTERVAL):
    """Delete earlier copies of an open-ended range once the current one is written.

    The key covers the exact request, so everything matching the glob is a copy of this
    same range differing only by fetch window or source. That includes files written before
    stamped names existed, which is what retires them.
    """
    if not _is_open_ended(end):
        return
    keep = {_cache_path(ticker, start, end, s, interval) for s in _sources_for(interval)}
    pattern = f"{ticker.upper()}_{_cache_key(ticker, start, end, interval)}.*.csv"
    for path in glob.glob(os.path.join(CACHE_DIR, pattern)):
        if path not in keep:
            try:
                os.remove(path)
            except OSError:  # a concurrent render got there first
                pass


def _find_cached(ticker, start, end, interval=DEFAULT_INTERVAL):
    """Return (path, source) for a cached frame, whichever source wrote it."""
    for source in _sources_for(interval):
        path = _cache_path(ticker, start, end, source, interval)
        if os.path.exists(path):
            return path, source
    return None, None


def _session_index(start, end, step):
    """Cash-hours timestamps at `step` spacing.

    The overnight holes are the point: demo intraday has to have the same shape as the real
    thing, or it won't exercise the axis handling whose whole job is closing them.
    """
    open_at = pd.Timedelta(hours=SESSION_OPEN[0], minutes=SESSION_OPEN[1])
    close_at = pd.Timedelta(hours=SESSION_CLOSE[0], minutes=SESSION_CLOSE[1])
    # Bars are labelled by the time they open, so the last one starts before the close.
    parts = [pd.date_range(d + open_at, d + close_at, freq=step, inclusive="left")
             for d in pd.bdate_range(start, end)]
    return parts[0].append(parts[1:]) if parts else pd.DatetimeIndex([])


def _synthetic(ticker, start, end, interval=DEFAULT_INTERVAL):
    """Deterministic fake OHLCV so the tool is testable without network."""
    seed = int(hashlib.md5(ticker.upper().encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    step = _spec(interval)["step"]
    idx = _session_index(start, end, step) if step else pd.bdate_range(start, end)
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


def _stooq_symbol(ticker):
    """Stooq namespaces by market; a bare US symbol needs the .us suffix."""
    return ticker.lower() if "." in ticker else f"{ticker.lower()}.us"


def _yahoo(ticker, start, end, interval=DEFAULT_INTERVAL):
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance is not installed. Run: pip install yfinance")

    df = yf.download(ticker, start=start, end=end, interval=interval,
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError("no rows returned")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if getattr(df.index, "tz", None) is not None:
        # Intraday arrives in exchange time. Keep the wall clock and drop the zone: a tick
        # should read 09:30 at the opening bell wherever the render runs, and a naive index
        # survives the CSV cache round-trip without DST turning it into objects.
        df.index = df.index.tz_localize(None)
    return df[COLUMNS].dropna()


def _stooq(ticker, start, end, interval=DEFAULT_INTERVAL):
    """Daily OHLCV from Stooq's CSV endpoint — no key, no account, no SDK.

    Worth knowing when reading a chart sourced here: yfinance is asked for
    dividend-and-split adjusted prices, Stooq adjusts differently, so total return for the
    same window can differ between the two. That's why the footer names the source.
    """
    if is_intraday(interval):
        raise ValueError("intraday bars are not available from Stooq")

    url = f"https://stooq.com/q/d/l/?s={_stooq_symbol(ticker)}&i=d"
    with urllib.request.urlopen(url, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")

    # Stooq answers a bad symbol with a 200 and the body "No data", not an HTTP error.
    if "No data" in raw[:200] or "Date" not in raw[:200]:
        raise ValueError("no rows returned")

    df = pd.read_csv(io.StringIO(raw), parse_dates=["Date"], index_col="Date")
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"unexpected columns, missing {', '.join(missing)}")

    df = df[COLUMNS].dropna()
    # The endpoint ignores date bounds, so trim locally.
    df = df.loc[df.index >= pd.Timestamp(start)]
    if end:
        df = df.loc[df.index <= pd.Timestamp(end)]
    if df.empty:
        raise ValueError("no rows in the requested date range")
    return df


def _usable(df, sessions):
    """Trim to the last `sessions` trading days, then insist there's something to animate.

    "Intraday" means the most recent session, but which day that is depends on weekends and
    holidays — so the range asks for a few days of bars and keeps the tail rather than
    guessing a date and coming back empty on a Monday morning.
    """
    if sessions:
        days = df.index.normalize().unique()
        df = df.loc[df.index.normalize() >= days[-int(sessions):][0]]
    if len(df) < 2:
        raise ValueError("fewer than two bars in that range")
    return df


def fetch(ticker: str, start: str, end: str | None = None,
          interval: str = DEFAULT_INTERVAL, sessions: int | None = None) -> pd.DataFrame:
    """Return a DataFrame indexed by timestamp with Open/High/Low/Close/Volume.

    For daily bars: Yahoo first, Stooq second. Yahoo breaks whenever it changes its
    endpoints, and a failed render is worse than one drawn from a second-choice source.
    For intraday there is no second choice — see _sources_for.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("Empty ticker.")

    if _DEMO:
        _SOURCES[ticker] = "demo"
        end = end or pd.Timestamp.today().normalize()
        return _usable(_synthetic(ticker, start, end, interval), sessions)

    os.makedirs(CACHE_DIR, exist_ok=True)
    cached, source = _find_cached(ticker, start, end, interval)
    if cached:
        df = pd.read_csv(cached, index_col=0, parse_dates=True)
        if not df.empty:
            _SOURCES[ticker] = source
            return _usable(df, sessions)

    fetchers = {"yahoo": _yahoo, "stooq": _stooq}
    problems = []
    for source in _sources_for(interval):  # preference order, as in _find_cached
        try:
            df = fetchers[source](ticker, start, end, interval)
            trimmed = _usable(df, sessions)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{source}: {exc}")
            continue
        # Cache what the source gave, not what this render kept — the session trim isn't
        # part of the cache key, so a trimmed frame under that key would be a lie.
        df.to_csv(_cache_path(ticker, start, end, source, interval))
        _drop_superseded(ticker, start, end, interval)
        _SOURCES[ticker] = source
        return trimmed

    detail = "; ".join(problems)
    if is_intraday(interval):
        detail += ". Intraday is Yahoo-only — Stooq serves daily bars and coarser"
    raise ValueError(f"No data for {ticker} ({detail}).")


def fetch_many(tickers, start, end=None, interval=DEFAULT_INTERVAL, sessions=None) -> dict:
    out = {}
    errors = []
    for t in tickers:
        try:
            out[t.strip().upper()] = fetch(t, start, end, interval, sessions)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{t}: {exc}")
    if not out:
        raise ValueError("; ".join(errors) or "No tickers resolved.")
    return out


def clear_cache():
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            os.remove(os.path.join(CACHE_DIR, f))
