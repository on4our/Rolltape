"""Price data: a licensed feed first, the scraped sources behind it, cached to disk.

Four sources, in preference order. Financial Modeling Prep leads — it is licensed, its
interval grid matches the one the interface offers, and its intraday history goes back
years rather than months. Twelve Data sits behind it as the other licensed option, and
Yahoo and Stooq behind both so a clone with no key still renders and a provider outage
costs a render rather than the afternoon. Each licensed source is inert without its key,
so which ones are live is a matter of configuration rather than code.

The one thing FMP's entry plan cannot do is reach past five years. That is a plan property
rather than an API one and it fails quietly — a MAX request comes back short rather than
refused — so the ceiling is enforced in `_sources_for` before the request is made.

There is deliberately no generated-data mode. A chart drawn from invented prices looks
exactly like a real one three steps later in a video editor, and no flag is worth that.
The test suite gets its offline prices from `testsupport.py`, which the app never imports.

Also the symbol lookup behind the ticker field — see search() at the bottom. That is a
different service from the price download and fails independently of it, which is why it
keeps its own built-in fallback rather than sharing the source order above.
"""

import glob
import hashlib
import io
import json
import os
import urllib.parse
import urllib.request
from collections import OrderedDict

import pandas as pd

import config

CACHE_DIR = config.CACHE_DIR

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

# Preference order. Each licensed source drops out without its key, Stooq drops out for
# intraday, and a source drops out for a window older than it can serve — `_sources_for` is
# where all three of those happen.
SOURCES = ("fmp", "twelvedata", "yahoo", "stooq")

# The ones whose terms cover showing the data to someone other than the person who fetched
# it. config.LICENSED_ONLY narrows the order to these.
LICENSED = ("fmp", "twelvedata")

# Which config key turns each licensed source on. A source whose key is blank is dropped
# before any request is made, so an unconfigured one never spends a call finding out.
SOURCE_KEYS = {"fmp": "FMP_KEY", "twelvedata": "TWELVEDATA_KEY"}

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


def _yfinance_installed() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("yfinance") is not None
    except (ImportError, ValueError):  # a half-installed package shouldn't 500 /api/meta
        return False


def intraday_available() -> bool:
    """Intraday needs a source that serves it — a licensed feed, or Yahoo via yfinance.

    Stooq covers a daily chart when none of those is there, so the app still runs and only
    this one feature goes. Better to tell the interface up front than to let someone build
    a 5-minute chart and hit a failed render at the end of it.
    """
    if _keyed("fmp") or _keyed("twelvedata"):
        return True
    return _yfinance_installed() and not config.LICENSED_ONLY


def periods_per_year(interval) -> float:
    """Bars in a trading year, for annualising a volatility figure.

    252 sessions, times however many bars fill one. Annualising intraday returns with the
    daily 252 understates volatility by the square root of the bars per session.
    """
    step = _spec(interval)["step"]
    if step is None:
        return 252.0
    return 252.0 * max(pd.Timedelta("6h30min") / pd.Timedelta(step), 1.0)


def _keyed(source) -> bool:
    """True when a licensed source has a key configured. Unlicensed sources need none."""
    attr = SOURCE_KEYS.get(source)
    return True if attr is None else bool(getattr(config, attr, ""))


def _horizon_days(source):
    """How far back this source's plan reaches, or None when it has no ceiling.

    Only FMP has one, and it is a property of the subscription rather than the API — see
    config.FMP_HISTORY_YEARS.
    """
    if source == "fmp":
        years = config.FMP_HISTORY_YEARS
        return years * 365.25 if years and years > 0 else None
    return None


def _covers(source, start) -> bool:
    """Whether `source` can reach back to `start`."""
    days = _horizon_days(source)
    if days is None or start is None:
        return True
    try:
        return (_today() - pd.Timestamp(start)).days <= days
    except (ValueError, TypeError):
        return True  # unparseable; fetch complains about it somewhere more useful


def _sources_for(interval, start=None):
    """The sources that can answer this request, in preference order.

    Three ways to be dropped, and the same principle behind all of them: **a source that
    cannot serve the request is removed, never asked to approximate it.** A failed render
    is recoverable; a wrong one that looks right is not.

    - *No key.* A licensed source without one is dropped before any request is made, which
      is the whole of what an unconfigured clone notices. `LICENSED_ONLY` goes the other
      way and removes the scraped sources outright — see config.py for why a paid deploy
      wants that.
    - *Wrong interval.* Stooq serves daily bars and coarser, so intraday never falls
      through to it — answering a five-minute chart with daily bars and labelling it
      five-minute is the exact failure this rule exists for.
    - *Too far back.* A plan with a history horizon answers a longer window with a short
      frame rather than an error, so a MAX chart would come back as five years under a MAX
      label. Dropping the source lets a deeper one below it answer instead; under
      LICENSED_ONLY there is nothing below it and the render fails, which is the honest
      outcome.
    """
    order = tuple(s for s in SOURCES if _keyed(s))
    if config.LICENSED_ONLY:
        order = tuple(s for s in order if s in LICENSED)
    if is_intraday(interval):
        order = tuple(s for s in order if s != "stooq")
    return tuple(s for s in order if _covers(s, start))


# Which source answered for each ticker in the current render. One module-level record is
# safe without locking: a render has this module to itself in its own process, and the
# previews left in the server are serialised by app.py's DRAW_LOCK.
_SOURCES = {}

# Sources that get named in the footer, and what to call them. Two different reasons to be
# on this list: a licensed feed asks to be credited and is what a paying deploy is actually
# running on, and Stooq is named because reaching it means everything above it was down —
# worth knowing when the total return doesn't match what another chart says. Yahoo is on
# neither footing, so it stays silent.
SOURCE_NAMES = {"fmp": "Financial Modeling Prep", "twelvedata": "Twelve Data",
                "stooq": "Stooq"}

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


def primary_source() -> str:
    """Which feed a daily render will try first, named for a human.

    For the startup banner. A key that never reached the process is otherwise invisible
    until it shows up as the wrong name in the footer of a finished render.
    """
    order = _sources_for(DEFAULT_INTERVAL)
    if not order:
        return "no source configured"
    return SOURCE_NAMES.get(order[0], order[0].title())


def reset_sources():
    _SOURCES.clear()


def sources_used():
    """Distinct sources that answered since the last reset."""
    return set(_SOURCES.values())


def source_for(ticker):
    """Which source last answered for one symbol, or None if it hasn't been asked.

    attribution() answers for a whole render; this answers per ticker, which is what a
    readout listing six symbols separately needs. Reading one key rather than resetting
    the record also keeps it usable from a request handler, where a reset would race with
    whatever else the server is drawing.
    """
    return _SOURCES.get(str(ticker).strip().upper())


def attribution():
    """Footer note for the current render, or None when everything came from Yahoo.

    A render that drew from more than one source names them all. One ticker off a different
    feed than the rest is exactly the case where a single label would be a lie, and a
    comparison chart is where that happens.
    """
    used = sources_used()
    named = [name for key, name in SOURCE_NAMES.items() if key in used]
    return "Data: " + ", ".join(named) if named else None


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
    keep = {_cache_path(ticker, start, end, s, interval)
            for s in _sources_for(interval, start)}
    pattern = f"{ticker.upper()}_{_cache_key(ticker, start, end, interval)}.*.csv"
    for path in glob.glob(os.path.join(CACHE_DIR, pattern)):
        if path not in keep:
            try:
                os.remove(path)
            except OSError:  # a concurrent render got there first
                pass


def _find_cached(ticker, start, end, interval=DEFAULT_INTERVAL):
    """Return (path, source) for a cached frame, whichever source wrote it."""
    for source in _sources_for(interval, start):
        path = _cache_path(ticker, start, end, source, interval)
        if os.path.exists(path):
            return path, source
    return None, None


FMP_URL = "https://financialmodelingprep.com/stable"

# Rolltape's interval names onto FMP's. Daily has its own endpoint; the rest hang off
# /historical-chart/<step>, which is why this maps to a path fragment rather than a query
# value the way the Twelve Data table does.
FMP_INTERVALS = {"1m": "1min", "5m": "5min", "15m": "15min",
                 "30m": "30min", "1h": "1hour"}


def _fmp_rows(path, params):
    """One FMP response as a list of row dicts.

    Two shapes to cope with. The intraday endpoints answer with a bare array; the daily one
    has historically wrapped it in {"symbol": ..., "historical": [...]}, and both spellings
    are in the wild across their v3 and stable paths. Accepting either costs three lines and
    saves a silent empty frame if the account is pointed at the other one.
    """
    url = f"{FMP_URL}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))

    if isinstance(payload, dict):
        # Errors arrive as an object with a message rather than a non-200, same as Stooq
        # answering a bad symbol with a 200 and the word "No data".
        for key in ("Error Message", "error", "message"):
            if payload.get(key):
                message = str(payload[key])
                if "limit" in message.lower():
                    raise RuntimeError(f"rate limit reached — {message}")
                raise ValueError(message)
        payload = payload.get("historical") or payload.get("results") or []
    if not isinstance(payload, list):
        raise ValueError("unexpected response shape")
    return payload


def _fmp(ticker, start, end=None, interval=DEFAULT_INTERVAL):
    """OHLCV from Financial Modeling Prep — the licensed feed.

    One request per call: unlike Twelve Data there is no page cap to work around, because
    the endpoints take `from`/`to` and answer the whole window.

    What this cannot do is notice that the window is older than the plan allows. FMP
    answers a too-long range with a short frame rather than an error, so a five-year
    Starter plan would return five years under a MAX label. That check lives in
    `_sources_for`, before the request — see config.FMP_HISTORY_YEARS.

    Unverified and worth checking with a key in hand: how this feed adjusts for dividends
    and splits. `_yahoo` asks for both, Stooq does its own thing, and the three disagreeing
    changes the total return a chart narrates — which is the whole of roadmap item 7.
    """
    if not config.FMP_KEY:
        raise RuntimeError("no API key — set ROLLTAPE_FMP_KEY")

    params = {"symbol": ticker, "apikey": config.FMP_KEY,
              "from": pd.Timestamp(start).strftime("%Y-%m-%d")}
    if end:
        params["to"] = pd.Timestamp(end).strftime("%Y-%m-%d")

    if is_intraday(interval):
        step = FMP_INTERVALS.get(interval)
        if not step:
            raise ValueError(f"interval {interval} is not on the FMP grid")
        rows = _fmp_rows(f"historical-chart/{step}", params)
    else:
        rows = _fmp_rows("historical-price-eod/full", params)

    if not rows:
        raise ValueError("no rows returned")

    df = pd.DataFrame(rows)
    stamp = "date" if "date" in df else "datetime"
    if stamp not in df:
        raise ValueError("no date column in the response")
    df.index = pd.to_datetime(df.pop(stamp))
    # Volume is absent on some instruments rather than zero, and nothing here divides by
    # it — dropping the row instead would throw away a perfectly good price.
    if "volume" not in df:
        df["volume"] = 0.0
    df = df.rename(columns={c: c.capitalize() for c in df.columns})
    for col in COLUMNS:
        if col not in df:
            raise ValueError(f"missing {col} in the response")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Newest first, and the renderers expect the opposite.
    return df[COLUMNS].dropna().sort_index()


TWELVEDATA_URL = "https://api.twelvedata.com/time_series"

# Rolltape's interval names onto Twelve Data's. The two grids agree on every step the
# interface offers, which is most of why this provider was chosen: intraday stops being a
# feature only one source can serve.
TWELVEDATA_INTERVALS = {"1d": "1day", "1m": "1min", "5m": "5min",
                        "15m": "15min", "30m": "30min", "1h": "1h"}

# The endpoint truncates a response to `outputsize`, capped at 5000. A `max` daily range is
# roughly three times that, so long windows arrive in pages.
TWELVEDATA_PAGE = 5000

# A page that comes back full means there is probably more behind it. The ceiling is a
# guard against a paging bug turning into an unbounded request loop against a metered API,
# not a real limit — 12 pages is 60,000 bars, well past two centuries of daily closes.
TWELVEDATA_MAX_PAGES = 12


def _twelvedata_page(params):
    """One page of bars, newest first. Returns the raw row dicts."""
    url = f"{TWELVEDATA_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))

    # Failures arrive as HTTP 200 with a status field, so the response code proves nothing
    # and the body has to be read either way. The rate limit is worth naming separately:
    # on the free tier it is the error anyone actually hits, and "wait a minute" is a
    # different instruction from "check the symbol".
    if not isinstance(payload, dict):
        raise ValueError("unexpected response shape")
    if payload.get("status") == "error":
        message = payload.get("message") or "request rejected"
        if payload.get("code") == 429:
            raise RuntimeError(f"rate limit reached — {message}")
        raise ValueError(message)
    return payload.get("values") or []


def _twelvedata_frame(rows):
    """Row dicts of strings into the shared OHLCV contract."""
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("datetime"))
    # Volume is absent for some instruments rather than zero. Nothing here divides by it,
    # and dropping the row instead would throw away a perfectly good price.
    if "volume" not in df:
        df["volume"] = 0.0
    df = df.rename(columns={c: c.capitalize() for c in df.columns})
    for col in COLUMNS:
        if col not in df:
            raise ValueError(f"missing {col} in the response")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[COLUMNS]


def _twelvedata(ticker, start, end=None, interval=DEFAULT_INTERVAL):
    """OHLCV from Twelve Data — the licensed feed, and the preferred source.

    Paged newest-first on purpose. With a range that overflows `outputsize` the only
    well-defined reading of a truncated response is "the most recent N"; asking oldest-first
    and keeping what arrives would end a `max` chart two decades short and look fine doing
    it. Each page then asks for everything before the oldest bar the last one returned.

    Unverified and worth checking with a key in hand: how this feed adjusts for dividends
    and splits. `_yahoo` asks for both, Stooq does its own thing, and the three disagreeing
    changes the total return a chart narrates — which is the whole of roadmap item 7. Until
    someone has compared them, the footer naming the source is what covers it.
    """
    if not config.TWELVEDATA_KEY:
        raise RuntimeError("no API key — set ROLLTAPE_TWELVEDATA_KEY")
    grid = TWELVEDATA_INTERVALS.get(interval)
    if not grid:
        raise ValueError(f"interval {interval} is not on the Twelve Data grid")

    start_ts = pd.Timestamp(start)
    cursor = pd.Timestamp(end) if end else None
    pages = []

    for _ in range(TWELVEDATA_MAX_PAGES):
        params = {"symbol": ticker, "interval": grid, "apikey": config.TWELVEDATA_KEY,
                  "start_date": start_ts.strftime("%Y-%m-%d %H:%M:%S"),
                  "outputsize": TWELVEDATA_PAGE, "order": "DESC", "format": "JSON",
                  # Exchange-local wall clock, which is what _yahoo also keeps: a bar
                  # should read 09:30 at the opening bell wherever the render runs.
                  "timezone": "Exchange"}
        if cursor is not None:
            params["end_date"] = cursor.strftime("%Y-%m-%d %H:%M:%S")

        rows = _twelvedata_page(params)
        if not rows:
            break
        page = _twelvedata_frame(rows)
        pages.append(page)
        if len(page) < TWELVEDATA_PAGE:
            break
        # One second earlier, so the oldest bar of this page isn't served again as the
        # newest of the next. `end_date` is inclusive.
        cursor = page.index.min() - pd.Timedelta(seconds=1)
        if cursor <= start_ts:
            break

    if not pages:
        raise ValueError("no rows returned")

    df = pd.concat(pages).dropna()
    # Pages overlap at their seams when a bar lands on the boundary, and they arrive
    # newest-first while every renderer expects the opposite.
    return df[~df.index.duplicated()].sort_index()


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


def _nothing_eligible(ticker, start, interval):
    """Explain an empty source list, which is a configuration problem rather than a fetch.

    Two ways to get here and they need different instructions, so the message says which
    one happened rather than listing everything that could be wrong.
    """
    dropped = [s for s in SOURCES if _keyed(s) and not _covers(s, start)]
    if dropped and config.LICENSED_ONLY:
        return (f"No data for {ticker}: {SOURCE_NAMES.get(dropped[0], dropped[0])} only "
                f"reaches back {config.FMP_HISTORY_YEARS} years and this chart starts at "
                f"{start}. Pick a shorter range, or set ROLLTAPE_FMP_HISTORY_YEARS to "
                "match a plan with deeper history.")
    return (f"No data for {ticker}: no source is configured for {interval} bars. "
            "Set ROLLTAPE_FMP_KEY, or clear ROLLTAPE_LICENSED_ONLY to allow the "
            "fallback sources.")


def fetch(ticker: str, start: str, end: str | None = None,
          interval: str = DEFAULT_INTERVAL, sessions: int | None = None) -> pd.DataFrame:
    """Return a DataFrame indexed by timestamp with Open/High/Low/Close/Volume.

    A licensed feed first when a key is configured, then Yahoo, then Stooq. Each source
    below the last exists because the one above it breaks: Yahoo whenever it moves its
    endpoints, a licensed feed whenever a monthly quota runs out. A failed render is worse
    than one drawn from a second-choice source — but see _sources_for for the three cases
    where a source is dropped rather than asked to approximate.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("Empty ticker.")

    os.makedirs(CACHE_DIR, exist_ok=True)
    cached, source = _find_cached(ticker, start, end, interval)
    if cached:
        df = pd.read_csv(cached, index_col=0, parse_dates=True)
        if not df.empty:
            _SOURCES[ticker] = source
            return _usable(df, sessions)

    fetchers = {"fmp": _fmp, "twelvedata": _twelvedata,
                "yahoo": _yahoo, "stooq": _stooq}
    problems = []
    for source in _sources_for(interval, start):  # preference order, as in _find_cached
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

    if not problems:  # nothing was even eligible to try
        raise ValueError(_nothing_eligible(ticker, start, interval))

    detail = "; ".join(problems)
    if is_intraday(interval):
        detail += ". Intraday needs Twelve Data or Yahoo — Stooq serves daily and coarser"
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


# ---------------------------------------------------------------------------
# Symbol search
# ---------------------------------------------------------------------------
# Yahoo's search endpoint, which is a separate service from the price download and needs
# neither yfinance nor a key. Plain urllib on purpose: this has to keep working on an
# install where yfinance is missing, since Stooq can still draw the chart it finds.
SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"

# Yahoo answers a bare urllib request with a 429. It isn't rate limiting — it wants a
# browser-shaped User-Agent — so this is the one header that matters.
SEARCH_UA = "Mozilla/5.0 (compatible; Rolltape/1.0; +https://github.com/on4our/rolltape)"

# A typeahead is not a render. Someone is mid-word and will type another letter before
# this comes back, so a lookup that hangs is worse than one that gives up and lets the
# built-in list answer alone.
SEARCH_TIMEOUT = 6

# The floor under the suggestion field, not a universe. Three reasons it is worth carrying
# rather than leaning on Yahoo for everything: demo mode must not touch the network, the
# search endpoint breaks on the same schedule the download one does, and the first
# keystroke should land before a round trip could have finished. Yahoo is still what finds
# everything past this list — anything here that gets delisted keeps suggesting itself, so
# it stays short and stays to names a chart actually gets pointed at.
LOCAL_SYMBOLS = (
    # US large caps
    ("AAPL", "Apple", "equity"), ("MSFT", "Microsoft", "equity"),
    ("NVDA", "NVIDIA", "equity"), ("GOOGL", "Alphabet Class A", "equity"),
    ("GOOG", "Alphabet Class C", "equity"), ("AMZN", "Amazon", "equity"),
    ("META", "Meta Platforms", "equity"), ("TSLA", "Tesla", "equity"),
    ("AVGO", "Broadcom", "equity"), ("BRK-B", "Berkshire Hathaway Class B", "equity"),
    ("LLY", "Eli Lilly", "equity"), ("JPM", "JPMorgan Chase", "equity"),
    ("V", "Visa", "equity"), ("MA", "Mastercard", "equity"),
    ("UNH", "UnitedHealth", "equity"), ("XOM", "Exxon Mobil", "equity"),
    ("COST", "Costco", "equity"), ("WMT", "Walmart", "equity"),
    ("PG", "Procter & Gamble", "equity"), ("JNJ", "Johnson & Johnson", "equity"),
    ("HD", "Home Depot", "equity"), ("ORCL", "Oracle", "equity"),
    ("ABBV", "AbbVie", "equity"), ("NFLX", "Netflix", "equity"),
    ("BAC", "Bank of America", "equity"), ("KO", "Coca-Cola", "equity"),
    ("CRM", "Salesforce", "equity"), ("CVX", "Chevron", "equity"),
    ("PEP", "PepsiCo", "equity"), ("ADBE", "Adobe", "equity"),
    ("MRK", "Merck", "equity"), ("CSCO", "Cisco", "equity"),
    ("MCD", "McDonald's", "equity"), ("WFC", "Wells Fargo", "equity"),
    ("GE", "GE Aerospace", "equity"), ("IBM", "IBM", "equity"),
    ("NOW", "ServiceNow", "equity"), ("DIS", "Walt Disney", "equity"),
    ("CAT", "Caterpillar", "equity"), ("INTU", "Intuit", "equity"),
    ("VZ", "Verizon", "equity"), ("T", "AT&T", "equity"),
    ("AMGN", "Amgen", "equity"), ("PFE", "Pfizer", "equity"),
    ("GS", "Goldman Sachs", "equity"), ("MS", "Morgan Stanley", "equity"),
    ("BLK", "BlackRock", "equity"), ("SPGI", "S&P Global", "equity"),
    ("BA", "Boeing", "equity"), ("LMT", "Lockheed Martin", "equity"),
    ("NKE", "Nike", "equity"), ("SBUX", "Starbucks", "equity"),
    ("TGT", "Target", "equity"), ("LOW", "Lowe's", "equity"),
    ("UBER", "Uber", "equity"), ("BKNG", "Booking Holdings", "equity"),
    ("ISRG", "Intuitive Surgical", "equity"), ("F", "Ford", "equity"),
    ("GM", "General Motors", "equity"),
    # Semiconductors and hardware — the sector a chart channel returns to most
    ("AMD", "Advanced Micro Devices", "equity"), ("INTC", "Intel", "equity"),
    ("MU", "Micron Technology", "equity"), ("QCOM", "Qualcomm", "equity"),
    ("TXN", "Texas Instruments", "equity"), ("AMAT", "Applied Materials", "equity"),
    ("LRCX", "Lam Research", "equity"), ("KLAC", "KLA Corporation", "equity"),
    ("ADI", "Analog Devices", "equity"), ("MRVL", "Marvell Technology", "equity"),
    ("ARM", "Arm Holdings", "equity"), ("SMCI", "Super Micro Computer", "equity"),
    ("DELL", "Dell Technologies", "equity"), ("TSM", "Taiwan Semiconductor", "equity"),
    ("ASML", "ASML Holding", "equity"),
    # Software, fintech and the retail favourites
    ("PLTR", "Palantir Technologies", "equity"), ("CRWD", "CrowdStrike", "equity"),
    ("PANW", "Palo Alto Networks", "equity"), ("SNOW", "Snowflake", "equity"),
    ("DDOG", "Datadog", "equity"), ("NET", "Cloudflare", "equity"),
    ("MDB", "MongoDB", "equity"), ("TEAM", "Atlassian", "equity"),
    ("WDAY", "Workday", "equity"), ("SHOP", "Shopify", "equity"),
    ("PYPL", "PayPal", "equity"), ("COIN", "Coinbase", "equity"),
    ("HOOD", "Robinhood Markets", "equity"), ("SOFI", "SoFi Technologies", "equity"),
    ("ABNB", "Airbnb", "equity"), ("DASH", "DoorDash", "equity"),
    ("SPOT", "Spotify", "equity"), ("RBLX", "Roblox", "equity"),
    ("SNAP", "Snap", "equity"), ("PINS", "Pinterest", "equity"),
    ("RIVN", "Rivian Automotive", "equity"), ("LCID", "Lucid Group", "equity"),
    ("GME", "GameStop", "equity"), ("BABA", "Alibaba", "equity"),
    ("NVO", "Novo Nordisk", "equity"), ("SAP", "SAP SE", "equity"),
    # Funds
    ("SPY", "SPDR S&P 500 ETF Trust", "etf"), ("VOO", "Vanguard S&P 500 ETF", "etf"),
    ("QQQ", "Invesco QQQ Trust", "etf"), ("IWM", "iShares Russell 2000 ETF", "etf"),
    ("DIA", "SPDR Dow Jones Industrial Average ETF", "etf"),
    ("VTI", "Vanguard Total Stock Market ETF", "etf"),
    ("SCHD", "Schwab US Dividend Equity ETF", "etf"),
    ("ARKK", "ARK Innovation ETF", "etf"), ("SMH", "VanEck Semiconductor ETF", "etf"),
    ("XLK", "Technology Select Sector SPDR", "etf"),
    ("XLE", "Energy Select Sector SPDR", "etf"),
    ("XLF", "Financial Select Sector SPDR", "etf"),
    ("GLD", "SPDR Gold Shares", "etf"), ("SLV", "iShares Silver Trust", "etf"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF", "etf"),
    ("IBIT", "iShares Bitcoin Trust", "etf"),
    # Indices and crypto, which take Yahoo's own symbol shapes
    ("^GSPC", "S&P 500", "index"), ("^DJI", "Dow Jones Industrial Average", "index"),
    ("^IXIC", "Nasdaq Composite", "index"), ("^RUT", "Russell 2000", "index"),
    ("^VIX", "CBOE Volatility Index", "index"),
    ("BTC-USD", "Bitcoin USD", "cryptocurrency"),
    ("ETH-USD", "Ethereum USD", "cryptocurrency"),
    ("SOL-USD", "Solana USD", "cryptocurrency"),
    ("DOGE-USD", "Dogecoin USD", "cryptocurrency"),
)

# Typing is bursty, and backspacing asks a question that was already answered. Bounded by
# hand rather than lru_cache because only a successful answer is worth keeping: caching a
# failed lookup would leave the field degraded long after Yahoo came back.
SEARCH_CACHE_SIZE = 64
_SEARCH_CACHE = OrderedDict()


def clear_search_cache():
    _SEARCH_CACHE.clear()


def _local_search(q):
    return [{"symbol": sym, "name": name, "type": kind, "exchange": ""}
            for sym, name, kind in LOCAL_SYMBOLS
            if q in sym or q in name.upper()]


def _yahoo_search(q, limit):
    params = urllib.parse.urlencode({"q": q, "quotesCount": limit, "newsCount": 0,
                                     "listsCount": 0, "enableFuzzyQuery": "false"})
    req = urllib.request.Request(f"{SEARCH_URL}?{params}",
                                 headers={"User-Agent": SEARCH_UA})
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))

    out = []
    for row in payload.get("quotes") or []:
        symbol = str(row.get("symbol") or "").strip().upper()
        # Search also answers with private companies and research entries that have no
        # price series behind them at all. isYahooFinance is what tells those apart, and a
        # suggestion that can't be charted is worse than one suggestion fewer.
        if not symbol or not row.get("isYahooFinance"):
            continue
        out.append({
            "symbol": symbol,
            "name": str(row.get("shortname") or row.get("longname") or "").strip(),
            "type": str(row.get("quoteType") or "").strip().lower(),
            "exchange": str(row.get("exchDisp") or row.get("exchange") or "").strip(),
        })
    return out


def _yahoo_search_cached(q, limit):
    key = (q, limit)
    if key in _SEARCH_CACHE:
        _SEARCH_CACHE.move_to_end(key)
        return _SEARCH_CACHE[key]
    hits = _yahoo_search(q, limit)  # a failure propagates, and so is never cached
    _SEARCH_CACHE[key] = hits
    while len(_SEARCH_CACHE) > SEARCH_CACHE_SIZE:
        _SEARCH_CACHE.popitem(last=False)
    return hits


def _merge_hits(groups):
    """One row per symbol. First mention sets the order; later ones fill in its blanks.

    A symbol in both the built-in list and Yahoo's answer should appear once, keeping the
    position the built-in list gave it while picking up the exchange only Yahoo knows.
    """
    out = {}
    for group in groups:
        for hit in group:
            row = out.setdefault(hit["symbol"], dict(hit))
            for field, value in hit.items():
                if value and not row.get(field):
                    row[field] = value
    return list(out.values())


def _match_rank(hit, q):
    """Exact symbol, then symbol prefix, then symbol substring, then a name-only match.

    Someone typing MU wants Micron, not every company with "mu" in its name, and the sort
    is stable — so within a rank the built-in list still comes before Yahoo's ordering.
    """
    sym = hit["symbol"]
    if sym == q:
        return 0
    if sym.startswith(q):
        return 1
    return 2 if q in sym else 3


def search(query, limit=8):
    """Symbol suggestions for a partial ticker or company name.

    The built-in list answers first and always: it costs nothing, it works offline, and it
    holds the symbols this tool actually gets pointed at. Yahoo finds everything else and
    is allowed to fail — a dead lookup should quietly narrow the suggestions, never break
    the field someone is in the middle of typing into. Demo mode skips it entirely, the
    same way the price fetch does, so --demo still never reaches the network.
    """
    q = str(query or "").strip().upper()
    if not q:
        return []
    limit = max(int(limit), 1)

    groups = [_local_search(q)]
    if not _DEMO:
        try:
            groups.append(_yahoo_search_cached(q, limit))
        except Exception:  # noqa: BLE001 - deliberate; see the docstring
            pass

    hits = _merge_hits(groups)
    hits.sort(key=lambda hit: _match_rank(hit, q))
    return hits[:limit]
