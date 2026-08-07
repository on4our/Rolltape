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


def _cache_path(ticker, start, end, source):
    key = hashlib.md5(f"{ticker}|{start}|{end}".encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{ticker.upper()}_{key}.{source}.csv")


def _find_cached(ticker, start, end):
    """Return (path, source) for a cached frame, whichever source wrote it."""
    for source in ("yahoo", "stooq"):
        path = _cache_path(ticker, start, end, source)
        if os.path.exists(path):
            return path, source
    return None, None


def _synthetic(ticker, start, end):
    """Deterministic fake OHLCV so the tool is testable without network."""
    seed = int(hashlib.md5(ticker.upper().encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, end)
    n = len(idx)
    if n < 2:
        raise ValueError("Date range is too short.")

    drift = rng.normal(0.0007, 0.0007)
    vol = rng.uniform(0.011, 0.028)
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


def _yahoo(ticker, start, end):
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance is not installed. Run: pip install yfinance")

    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError("no rows returned")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[COLUMNS].dropna()


def _stooq(ticker, start, end):
    """Daily OHLCV from Stooq's CSV endpoint — no key, no account, no SDK.

    Worth knowing when reading a chart sourced here: yfinance is asked for
    dividend-and-split adjusted prices, Stooq adjusts differently, so total return for the
    same window can differ between the two. That's why the footer names the source.
    """
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


def fetch(ticker: str, start: str, end: str | None = None) -> pd.DataFrame:
    """Return a DataFrame indexed by date with Open/High/Low/Close/Volume.

    Yahoo first, Stooq second. Yahoo breaks whenever it changes its endpoints, and a
    failed render is worse than one drawn from a second-choice source.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("Empty ticker.")

    if _DEMO:
        _SOURCES[ticker] = "demo"
        return _synthetic(ticker, start, end or pd.Timestamp.today().normalize())

    os.makedirs(CACHE_DIR, exist_ok=True)
    cached, source = _find_cached(ticker, start, end)
    if cached:
        df = pd.read_csv(cached, index_col=0, parse_dates=True)
        if not df.empty:
            _SOURCES[ticker] = source
            return df

    problems = []
    for source, fn in (("yahoo", _yahoo), ("stooq", _stooq)):
        try:
            df = fn(ticker, start, end)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{source}: {exc}")
            continue
        df.to_csv(_cache_path(ticker, start, end, source))
        _SOURCES[ticker] = source
        return df

    raise ValueError(f"No data for {ticker} ({'; '.join(problems)}).")


def fetch_many(tickers, start, end=None) -> dict:
    out = {}
    errors = []
    for t in tickers:
        try:
            out[t.strip().upper()] = fetch(t, start, end)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{t}: {exc}")
    if not out:
        raise ValueError("; ".join(errors) or "No tickers resolved.")
    return out


def clear_cache():
    if os.path.isdir(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            os.remove(os.path.join(CACHE_DIR, f))
