"""Price data: Yahoo first, Stooq when Yahoo breaks, cached to disk, plus a demo mode."""

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

# Which source answered for each ticker in the current render. app.py's RENDER_LOCK
# serialises previews and renders, so one module-level record is safe without locking.
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
    """Bar length in minutes, or None for daily bars."""
    return int(interval[:-1]) if interval.endswith("m") else None


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


def _freshness(end, interval):
    """What "up to now" means for a cache entry.

    An open-ended range keeps moving, so the moment it was pulled is part of its identity —
    otherwise a year-to-date range cached this morning still ends this morning next week.
    Daily bars settle once a day; intraday bars are still moving, so they key by the minute
    and leave a small trail of files that "Clear price cache" sweeps up.
    """
    if end:
        return str(end)
    fmt = "%Y-%m-%d %H:%M" if interval_minutes(interval) else "%Y-%m-%d"
    return pd.Timestamp.now().strftime(fmt)


def _cache_path(ticker, start, end, source, interval="1d"):
    stamp = _freshness(end, interval)
    key = hashlib.md5(f"{ticker}|{start}|{stamp}|{interval}".encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{ticker.upper()}_{key}.{source}.csv")


def _find_cached(ticker, start, end, interval="1d"):
    """Return (path, source) for a cached frame, whichever source wrote it."""
    for source in ("yahoo", "stooq"):
        path = _cache_path(ticker, start, end, source, interval)
        if os.path.exists(path):
            return path, source
    return None, None


def _bar_index(start, end, interval):
    """Timestamps a feed would return for this window — sessions only, no overnight gap."""
    days = pd.bdate_range(start, end)
    minutes = interval_minutes(interval)
    if not minutes:
        return days
    parts = [pd.date_range(d + pd.Timedelta(hours=SESSION_OPEN[0], minutes=SESSION_OPEN[1]),
                           d + pd.Timedelta(hours=SESSION_CLOSE[0], minutes=SESSION_CLOSE[1]),
                           freq=f"{minutes}min", inclusive="left")
             for d in days]
    return parts[0].append(parts[1:]) if parts else pd.DatetimeIndex([])


def _synthetic(ticker, start, end, interval="1d"):
    """Deterministic fake OHLCV so the tool is testable without network."""
    seed = int(hashlib.md5(ticker.upper().encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    idx = _bar_index(start, end, interval)
    n = len(idx)
    if n < 2:
        raise ValueError("Date range is too short.")

    # A day's worth of drift and volatility has to be split across that day's bars, or an
    # intraday demo chart swings further in one session than the real thing does in a year.
    per_day = n / max(idx.normalize().nunique(), 1)
    drift = rng.normal(0.0007, 0.0007) / per_day
    vol = rng.uniform(0.011, 0.028) / np.sqrt(per_day)
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


def _yahoo(ticker, start, end, interval="1d"):
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance is not installed. Run: pip install yfinance")

    df = yf.download(ticker, start=start, end=end, interval=interval, progress=False,
                     auto_adjust=True)
    if df is None or df.empty:
        raise ValueError("no rows returned")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # Intraday bars come back stamped in the exchange's timezone. Drop the zone and keep the
    # wall clock: an axis labelled 09:30 should say the opening bell wherever it's rendered,
    # and a naive index survives the round trip through the CSV cache unchanged.
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df[COLUMNS].dropna()


def _stooq(ticker, start, end, interval="1d"):
    """Daily OHLCV from Stooq's CSV endpoint — no key, no account, no SDK.

    Worth knowing when reading a chart sourced here: yfinance is asked for
    dividend-and-split adjusted prices, Stooq adjusts differently, so total return for the
    same window can differ between the two. That's why the footer names the source.
    """
    if interval_minutes(interval):
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
          interval: str = "1d", sessions: int | None = None) -> pd.DataFrame:
    """Return a DataFrame indexed by date with Open/High/Low/Close/Volume.

    Yahoo first, Stooq second. Yahoo breaks whenever it changes its endpoints, and a
    failed render is worse than one drawn from a second-choice source. Intraday is the
    exception: Stooq publishes daily bars only, so those renders stand on Yahoo alone and
    say so rather than quietly falling back to a different shape of data.
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

    problems = []
    for source, fn in (("yahoo", _yahoo), ("stooq", _stooq)):
        try:
            df = fn(ticker, start, end, interval)
            trimmed = _usable(df, sessions)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{source}: {exc}")
            continue
        # Cache what the source gave, not what this render kept — the session trim isn't
        # part of the cache key, so a trimmed frame under that key would be a lie.
        df.to_csv(_cache_path(ticker, start, end, source, interval))
        _SOURCES[ticker] = source
        return trimmed

    raise ValueError(f"No data for {ticker} ({'; '.join(problems)}).")


def fetch_many(tickers, start, end=None, interval="1d", sessions=None) -> dict:
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
