"""Price data: Yahoo first, Stooq when Yahoo breaks, cached to disk, plus a demo mode.

Daily or intraday — see INTERVALS for what each grain costs in history and which source
can serve it.
"""

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

# Bar intervals. Yahoo caps intraday history hard and rejects a request that reaches past
# the cap rather than returning what it has, so `max_days` is a real constraint the UI has
# to respect — see clean_config().
#
# `per_year` is how many bars a year holds, used to annualise the volatility metric. US
# equities trade 6.5 hours a day, 252 days a year: 78 five-minute bars per session, 26
# fifteen-minute, and 7 hourly once the ragged last bar of the session is counted.
#
# `stooq` marks what the fallback can serve. Its CSV endpoint is daily-grain only, so
# every intraday interval is Yahoo-or-nothing — the one place a Yahoo outage still takes
# a render down with it.
# `minutes` is the bar width and cannot be derived from `per_year`: a 6.5-hour session
# holds seven hourly bars only because the last one is cut short at the close.
INTERVALS = {
    "1d": {"label": "Daily", "minutes": None, "max_days": None,
           "per_year": 252, "stooq": True},
    "1h": {"label": "Hourly", "minutes": 60, "max_days": 730,
           "per_year": 252 * 7, "stooq": False},
    "15m": {"label": "15 min", "minutes": 15, "max_days": 60,
            "per_year": 252 * 26, "stooq": False},
    "5m": {"label": "5 min", "minutes": 5, "max_days": 60,
           "per_year": 252 * 78, "stooq": False},
}
DEFAULT_INTERVAL = "1d"


def is_intraday(interval: str) -> bool:
    return interval != DEFAULT_INTERVAL


def bars_per_session(interval: str) -> int:
    return max(INTERVALS[interval]["per_year"] // 252, 1)

# Which source answered for each ticker in the current render. app.py's RENDER_LOCK
# serialises previews and renders, so one module-level record is safe without locking.
_SOURCES = {}

SOURCE_LABELS = {"stooq": "Data: Stooq", "demo": "Demo data"}


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


def _cache_path(ticker, start, end, source, interval):
    # Interval is part of the key, not just the filename: the same ticker over the same
    # dates is a completely different frame at 5m than at 1d, and keying without it would
    # serve daily bars to an intraday render.
    key = hashlib.md5(f"{ticker}|{start}|{end}|{interval}".encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{ticker.upper()}_{interval}_{key}.{source}.csv")


def _find_cached(ticker, start, end, interval):
    """Return (path, source) for a cached frame, whichever source wrote it."""
    for source in ("yahoo", "stooq"):
        path = _cache_path(ticker, start, end, source, interval)
        if os.path.exists(path):
            return path, source
    return None, None


def _session_index(start, end, interval):
    """Bar timestamps for regular US market hours, 9:30 to 16:00.

    Built session by session rather than as one continuous range so demo intraday data has
    the same overnight holes as the real thing. Those holes are what the positional x axis
    in renderers.py exists to close, so generating data without them would let a gap bug
    through --demo untouched.
    """
    minutes = INTERVALS[interval]["minutes"]
    sessions = []
    for day in pd.bdate_range(start, end):
        # Bars are labelled by the time they open, so the session stops short of 16:00.
        sessions.append(pd.date_range(day + pd.Timedelta(hours=9, minutes=30),
                                      day + pd.Timedelta(hours=16),
                                      freq=f"{minutes}min", inclusive="left"))
    if not sessions:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(np.concatenate([s.to_numpy() for s in sessions]))


def _synthetic(ticker, start, end, interval=DEFAULT_INTERVAL):
    """Deterministic fake OHLCV so the tool is testable without network."""
    seed = int(hashlib.md5(ticker.upper().encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    if is_intraday(interval):
        idx = _session_index(start, end, interval)
    else:
        idx = pd.bdate_range(start, end)
    n = len(idx)
    if n < 2:
        raise ValueError("Date range is too short.")

    # The tuned numbers below describe a trading day. A five-minute bar moves far less
    # than a daily one, so scale both down by the square root of the bars in a session and
    # the series keeps a believable shape at any interval.
    per_session = bars_per_session(interval)
    drift = rng.normal(0.0007, 0.0007) / per_session
    vol = rng.uniform(0.011, 0.028) / np.sqrt(per_session)
    close = (20 + seed % 400) * np.exp(np.cumsum(rng.normal(drift, vol, n)))

    prev = np.concatenate([[close[0]], close[:-1]])
    open_ = prev * (1 + rng.normal(0, vol * 0.3, n))
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol * 0.5, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol * 0.5, n)))
    vol_shares = rng.lognormal(15.5 - np.log(per_session), 0.4, n)

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

    df = yf.download(ticker, start=start, end=end, interval=interval, progress=False,
                     auto_adjust=True)
    if df is None or df.empty:
        raise ValueError("no rows returned")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[COLUMNS].dropna()
    # Intraday frames come back tz-aware in exchange time. The renderers put these on a
    # positional axis and only ever format the labels, so carrying a timezone buys nothing
    # and makes them awkward to compare against a naive cache read.
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    return df


def _stooq(ticker, start, end, interval=DEFAULT_INTERVAL):
    """Daily OHLCV from Stooq's CSV endpoint — no key, no account, no SDK.

    Worth knowing when reading a chart sourced here: yfinance is asked for
    dividend-and-split adjusted prices, Stooq adjusts differently, so total return for the
    same window can differ between the two. That's why the footer names the source.
    """
    if is_intraday(interval):
        # Not a temporary gap to paper over: the endpoint has no intraday grain at all.
        # Saying so plainly beats letting fetch() report a parse failure.
        raise ValueError("Stooq serves daily bars only")
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


def fetch(ticker: str, start: str, end: str | None = None,
          interval: str = DEFAULT_INTERVAL) -> pd.DataFrame:
    """Return a DataFrame indexed by date with Open/High/Low/Close/Volume.

    Yahoo first, Stooq second. Yahoo breaks whenever it changes its endpoints, and a
    failed render is worse than one drawn from a second-choice source. At an intraday
    interval there is no second choice — Stooq declines and the error names why.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("Empty ticker.")
    if interval not in INTERVALS:
        raise ValueError(f"Unknown interval: {interval}")

    if _DEMO:
        _SOURCES[ticker] = "demo"
        return _synthetic(ticker, start, end or pd.Timestamp.today().normalize(), interval)

    os.makedirs(CACHE_DIR, exist_ok=True)
    cached, source = _find_cached(ticker, start, end, interval)
    if cached:
        df = pd.read_csv(cached, index_col=0, parse_dates=True)
        if not df.empty:
            _SOURCES[ticker] = source
            return df

    problems = []
    for source, fn in (("yahoo", _yahoo), ("stooq", _stooq)):
        try:
            df = fn(ticker, start, end, interval)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{source}: {exc}")
            continue
        df.to_csv(_cache_path(ticker, start, end, source, interval))
        _SOURCES[ticker] = source
        return df

    raise ValueError(f"No data for {ticker} ({'; '.join(problems)}).")


def fetch_many(tickers, start, end=None, interval=DEFAULT_INTERVAL) -> dict:
    out = {}
    errors = []
    for t in tickers:
        try:
            out[t.strip().upper()] = fetch(t, start, end, interval)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{t}: {exc}")
    if not out:
        raise ValueError("; ".join(errors) or "No tickers resolved.")
    return out


def clear_cache():
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            os.remove(os.path.join(CACHE_DIR, f))
