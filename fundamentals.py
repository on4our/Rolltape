"""Income statements: the licensed feed first, Yahoo behind it, cached to disk.

A second module rather than a fifth entry in `data.SOURCES`, because a statement is not a
price series. That order is a preference over fetchers sharing one signature —
`(ticker, start, end, interval)` returning OHLCV bars — and a statement has none of those
arguments: it arrives per fiscal period, in line items, from a different endpoint. Adding
it there would mean widening the contract for four price sources to carry parameters none
of them use.

What is shared is the reasoning, and this borrows it from data.py rather than restating it:
the same key check, the same `LICENSED_ONLY` narrowing, the same plan horizon, and the same
record of which source answered so one footer names every feed a render actually used. A
source that cannot serve the request is dropped, never asked to approximate it.

Two sources rather than four. Stooq publishes no fundamentals at all, and Twelve Data's sit
on a plan above the $29 one data.py is wired for — so the order is FMP, then Yahoo, and
`LICENSED_ONLY` leaves FMP on its own. Yahoo is reached with plain urllib rather than
yfinance, for the reason the symbol search is: it has to keep working on an install where
yfinance was never installed.

**Deltas are differences between reported subtotals, never sums of components.** A bridge
built by adding up cost lines is a bridge that stops landing on the net income it claims
the moment one line is missing or a filer classifies something unusually — and a waterfall
that misses its own total is the exact "wrong but looks right" failure the source rules
exist to prevent. `bridge()` below only ever subtracts one reported figure from another, so
the arithmetic closes by construction.

Numbers arrive in the currency the company reports in and are never rescaled here. The
renderer formats them; a module that divided by a billion on the way in would leave
"$60.9B" impossible to check against the filing.
"""

import hashlib
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

import config
import data as datasrc

CACHE_DIR = config.CACHE_DIR

# The clock data.py works from, so the plan-horizon check here and the one in front of a
# price fetch agree with each other rather than drifting by a day across midnight.
_today = datasrc._today

# Preference order, narrowed by `_sources_for` the same three ways data.py narrows its own.
SOURCES = ("fmp", "yahoo")

# The one whose terms cover showing the numbers to someone other than the person who
# fetched them. Same list as data.LICENSED minus Twelve Data, which has fundamentals but
# not on the plan this app is wired for.
LICENSED = ("fmp",)

PERIODS = {
    "annual": {"label": "Annual", "fmp": "annual", "yahoo": "annual", "months": 12},
    "quarterly": {"label": "Quarterly", "fmp": "quarter", "yahoo": "quarterly",
                  "months": 3},
}
DEFAULT_PERIOD = "annual"

# The shared column contract, the way data.COLUMNS is for bars. Revenue and NetIncome are
# the two a bridge cannot be drawn without; everything else refines it, and a stage whose
# inputs are missing is skipped rather than guessed at.
REQUIRED = ("Revenue", "NetIncome")
LINES = ("Revenue", "CostOfRevenue", "GrossProfit", "ResearchDevelopment",
         "SellingGeneralAdministrative", "OperatingExpenses", "OperatingIncome",
         "NetIncome")

# Two more columns ride alongside the numbers: what the filer called the period, and what
# currency it reported in. Both are strings and both survive the CSV cache round trip.
META_COLUMNS = ("Label", "Currency")

# How many periods a bridge may ask for. One is enough for an income bridge; the growth
# bridge wants a run of them, and past a dozen the bars are too thin to label.
MAX_PERIODS = 12

# Currency symbols worth printing rather than spelling out. Anything else prints its ISO
# code, which is better than a dollar sign on a company that doesn't report in dollars.
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥",
                    "CNY": "¥", "CHF": "CHF ", "CAD": "C$", "AUD": "A$",
                    "INR": "₹", "KRW": "₩", "TWD": "NT$", "HKD": "HK$"}


def currency_symbol(code):
    """What to put in front of a figure reported in `code`."""
    code = str(code or "USD").strip().upper()
    return CURRENCY_SYMBOLS.get(code) or f"{code} "


def _period(name):
    return PERIODS.get(name) or PERIODS[DEFAULT_PERIOD]


def _reach(period, periods):
    """The earliest date this request needs, for the plan horizon check.

    A statement request is expressed in periods rather than dates, so the horizon has to be
    translated into one: ten annual statements reach back about ten years, and a plan that
    only holds five would answer with five of them under a ten-year label. That is the same
    silent truncation `data.covers` exists for, so it gets the same treatment — the source
    is dropped and a deeper one answers instead.
    """
    # One more period than asked for, matching what the fetchers request: the growth bridge
    # opens on the earliest statement rather than bridging into it.
    months = _period(period)["months"] * (max(int(periods), 1) + 1)
    return _today() - pd.DateOffset(months=months)


def _sources_for(period=DEFAULT_PERIOD, periods=1):
    """The sources that can answer this request, in preference order."""
    order = tuple(s for s in SOURCES if datasrc.keyed(s))
    if config.LICENSED_ONLY:
        order = tuple(s for s in order if s in LICENSED)
    start = _reach(period, periods)
    return tuple(s for s in order if datasrc.covers(s, start))


def _nothing_eligible(ticker, period, periods):
    """Explain an empty source list, which is configuration rather than a failed fetch."""
    dropped = [s for s in SOURCES
               if datasrc.keyed(s) and not datasrc.covers(s, _reach(period, periods))]
    if dropped and config.LICENSED_ONLY:
        return (f"No statements for {ticker}: "
                f"{datasrc.SOURCE_NAMES.get(dropped[0], dropped[0])} only reaches back "
                f"{config.FMP_HISTORY_YEARS} years and this chart asks for {periods} "
                f"{period} periods. Ask for fewer, or set ROLLTAPE_FMP_HISTORY_YEARS to "
                "match a plan with deeper history.")
    return (f"No statements for {ticker}: no source is configured. Set ROLLTAPE_FMP_KEY, "
            "or clear ROLLTAPE_LICENSED_ONLY to allow the fallback source.")


# ---------------------------------------------------------------------------
# Financial Modeling Prep
# ---------------------------------------------------------------------------
# Their field names onto the shared contract. `data.fmp_rows` does the transport — the URL,
# the error body that arrives under a 200, and naming the rate limit as itself.
FMP_FIELDS = {
    "revenue": "Revenue",
    "costOfRevenue": "CostOfRevenue",
    "grossProfit": "GrossProfit",
    "researchAndDevelopmentExpenses": "ResearchDevelopment",
    "sellingGeneralAndAdministrativeExpenses": "SellingGeneralAdministrative",
    "operatingExpenses": "OperatingExpenses",
    "operatingIncome": "OperatingIncome",
    "netIncome": "NetIncome",
}


def _fmp_label(row, period):
    """What the filer called this period.

    FMP reports the fiscal year and the quarter it belongs to, which is the whole reason to
    prefer it here: a company whose year ends in January has a fiscal label that no amount
    of arithmetic on the end date reproduces.
    """
    year = row.get("fiscalYear") or row.get("calendarYear") or ""
    quarter = str(row.get("period") or "").strip().upper()
    year = str(year).strip()[:4]
    if not year:
        return ""
    if period == "annual" or quarter in ("", "FY"):
        return f"FY{year}"
    return f"{quarter} FY{year}"


def _fmp(ticker, period, periods):
    if not config.FMP_KEY:
        raise RuntimeError("no API key — set ROLLTAPE_FMP_KEY")

    rows = datasrc.fmp_rows("income-statement", {
        "symbol": ticker, "apikey": config.FMP_KEY,
        "period": _period(period)["fmp"],
        # One more than asked for: the growth bridge opens on the earliest period rather
        # than bridging into it, so N bars of change need N+1 statements behind them.
        "limit": min(int(periods) + 1, MAX_PERIODS + 1),
    })
    if not rows:
        raise ValueError("no statements returned")

    out = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        rec = {"End": pd.Timestamp(row["date"]),
               "Label": _fmp_label(row, period),
               "Currency": str(row.get("reportedCurrency") or "USD").strip().upper()}
        for field, line in FMP_FIELDS.items():
            rec[line] = pd.to_numeric(row.get(field), errors="coerce")
        out.append(rec)
    return _frame(out)


# ---------------------------------------------------------------------------
# Yahoo
# ---------------------------------------------------------------------------
# The endpoint yfinance reads for the same numbers, called directly. Plain urllib for the
# reason data.search is: this has to answer on an install where yfinance is missing, and a
# statement is a filed figure rather than anything the price SDK adds to.
YAHOO_URL = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
             "timeseries/{symbol}")

# Yahoo's names onto the shared contract. Each is requested with the period as a prefix —
# annualTotalRevenue, quarterlyTotalRevenue — which is also how they come back.
YAHOO_FIELDS = {
    "TotalRevenue": "Revenue",
    "CostOfRevenue": "CostOfRevenue",
    "GrossProfit": "GrossProfit",
    "ResearchAndDevelopment": "ResearchDevelopment",
    "SellingGeneralAndAdministration": "SellingGeneralAdministrative",
    "OperatingExpense": "OperatingExpenses",
    "OperatingIncome": "OperatingIncome",
    "NetIncome": "NetIncome",
}

YAHOO_TIMEOUT = 20


def _yahoo_label(end, period):
    """A period label worked out from its end date, because Yahoo doesn't report one.

    The calendar year an annual period ends in is its fiscal year for very nearly every
    filer, so `FY2025` is right even for a January year-end. Quarters are the weaker half:
    this names them by the calendar quarter they end in, which a company whose year is
    offset from the calendar will number differently. FMP reports the real label and is
    tried first — this is the degraded answer, not the preferred one.
    """
    if period == "annual":
        return f"FY{end.year}"
    return f"Q{(end.month - 1) // 3 + 1} {end.year}"


def _yahoo(ticker, period, periods):
    prefix = _period(period)["yahoo"]
    types = ",".join(prefix + name for name in YAHOO_FIELDS)
    params = urllib.parse.urlencode({
        "symbol": ticker, "type": types, "merge": "false",
        # Epoch bounds rather than a period count: the endpoint has no "last N" parameter,
        # so the window is opened wide and the trim happens in `fetch`.
        "period1": 0, "period2": int(pd.Timestamp.now("UTC").timestamp()) + 86400,
    })
    url = f"{YAHOO_URL.format(symbol=urllib.parse.quote(ticker))}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": datasrc.SEARCH_UA})
    with urllib.request.urlopen(req, timeout=YAHOO_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))

    if not isinstance(payload, dict):
        raise ValueError("unexpected response shape")
    series = (payload.get("timeseries") or {})
    if series.get("error"):
        raise ValueError(str(series["error"]))

    # One group per requested type, each a list of {asOfDate, reportedValue} — so the frame
    # is assembled by end date across groups rather than read off row by row.
    records = {}
    for group in series.get("result") or []:
        if not isinstance(group, dict):
            continue
        for key, rows in group.items():
            if not key.startswith(prefix):
                continue
            line = YAHOO_FIELDS.get(key[len(prefix):])
            if not line:
                continue
            for row in rows or []:
                if not isinstance(row, dict) or not row.get("asOfDate"):
                    continue
                value = (row.get("reportedValue") or {}).get("raw")
                if value is None:
                    continue
                end = pd.Timestamp(row["asOfDate"])
                rec = records.setdefault(end, {"End": end, "Label": "",
                                               "Currency": "USD"})
                rec[line] = pd.to_numeric(value, errors="coerce")
                if row.get("currencyCode"):
                    rec["Currency"] = str(row["currencyCode"]).strip().upper()

    if not records:
        raise ValueError("no statements returned")
    for end, rec in records.items():
        rec["Label"] = _yahoo_label(end, period)
    return _frame(list(records.values()))


# ---------------------------------------------------------------------------
# The shared frame
# ---------------------------------------------------------------------------
def _frame(records):
    """Row dicts into the shared column contract, oldest period first.

    Every column in `LINES` exists whether or not the source filled it, so a bridge asks
    with `.get`-free indexing and decides on NaN rather than on a missing key. The two
    required lines are what makes a frame usable at all — a statement with no revenue in it
    is a response shape problem, not a company.
    """
    if not records:
        raise ValueError("no statements returned")
    df = pd.DataFrame(records)
    df.index = pd.to_datetime(df.pop("End"))
    for line in LINES:
        if line not in df:
            df[line] = float("nan")
        df[line] = pd.to_numeric(df[line], errors="coerce")
    for col in META_COLUMNS:
        if col not in df:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str)

    df = df[list(LINES) + list(META_COLUMNS)]
    df = df[~df.index.duplicated()].sort_index()
    df = df.dropna(subset=list(REQUIRED))
    if df.empty:
        raise ValueError("no statement had both revenue and net income in it")
    return df


# ---------------------------------------------------------------------------
# Cache and fetch
# ---------------------------------------------------------------------------
def _cache_path(ticker, period, periods, source):
    """Where a statement frame for this request is cached.

    Always date-stamped, unlike the price cache, because a statement request is inherently
    open-ended: it asks for the most recent N periods, and which periods those are changes
    the morning a company reports. A day is far finer than the quarterly cadence underneath
    it and costs one request to be wrong by.
    """
    key = hashlib.md5(f"{ticker}|{period}|{periods}".encode()).hexdigest()[:16]
    stamp = str(_today().date())
    return os.path.join(CACHE_DIR,
                        f"{ticker.upper()}_{key}.{stamp}.{source}.income.csv")


def fetch(ticker, period=DEFAULT_PERIOD, periods=1) -> pd.DataFrame:
    """The last `periods` income statements for `ticker`, oldest first.

    FMP first when a key is configured, Yahoo behind it — and nothing behind Yahoo, so a
    clone with no key draws a waterfall right up until Yahoo moves the endpoint, at which
    point the render fails rather than inventing a filing. That asymmetry with the price
    path is deliberate: prices have four sources because a chart of them is the product,
    and there is no fourth place to read an income statement from that is worth trusting.
    """
    ticker = str(ticker).strip().upper()
    if not ticker:
        raise ValueError("Empty ticker.")
    if period not in PERIODS:
        raise ValueError(f"Unknown statement period: {period}")
    periods = max(min(int(periods), MAX_PERIODS), 1)

    os.makedirs(CACHE_DIR, exist_ok=True)
    order = _sources_for(period, periods)
    for source in order:
        path = _cache_path(ticker, period, periods, source)
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if not df.empty:
                datasrc.note_source(ticker, source)
                return _trim(_frame(_records(df)), periods)

    fetchers = {"fmp": _fmp, "yahoo": _yahoo}
    problems = []
    for source in order:
        try:
            df = fetchers[source](ticker, period, periods)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{source}: {exc}")
            continue
        # Cache what the source gave rather than the trim, the way the price cache does —
        # the period count isn't in the key, so a trimmed frame under it would be a lie.
        df.to_csv(_cache_path(ticker, period, periods, source))
        datasrc.note_source(ticker, source)
        return _trim(df, periods)

    if not problems:
        raise ValueError(_nothing_eligible(ticker, period, periods))
    raise ValueError(f"No statements for {ticker} ({'; '.join(problems)}).")


def _records(df):
    """A cached frame back into the row dicts `_frame` takes."""
    out = []
    for end, row in df.iterrows():
        rec = {"End": end}
        rec.update(row.to_dict())
        out.append(rec)
    return out


def _trim(df, periods):
    """The most recent `periods` statements, plus the one the growth bridge opens on."""
    return df.iloc[-(int(periods) + 1):]


# ---------------------------------------------------------------------------
# Bridges
# ---------------------------------------------------------------------------
# A row is a label, a number and a kind. "start" and "total" are levels drawn from zero;
# "delta" is a change drawn from wherever the row before it ended. The renderer knows only
# that much, which is what lets a hand-typed bridge and a fetched one share one drawing.
KINDS = ("start", "delta", "total")

BRIDGES = {
    "income": {"label": "Revenue to net income",
               "desc": "One period's revenue stepping down through cost, expenses and "
                       "tax to what was left."},
    "growth": {"label": "Revenue growth",
               "desc": "The earliest period's revenue, then what each period after it "
                       "added or gave back."},
}
DEFAULT_BRIDGE = "income"


def _num(value):
    """A finite float, or None — which is how a bridge decides a stage is unreportable."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out or out in (float("inf"), float("-inf")) else out


def _row(label, value, kind, share=False):
    return {"label": label, "value": float(value), "kind": kind, "share": bool(share)}


def income_bridge(df):
    """Revenue down to net income for the latest period in `df`.

    Every step is one reported subtotal subtracted from another, so the bars land on the
    net income the last one names no matter which lines the filer left out. A stage whose
    inputs are missing is dropped and the bridge closes over it — a company reporting no
    gross profit gets one operating-expenses bar instead of three, rather than a bar
    computed from a number nobody filed.

    R&D and SG&A are drawn separately when both are there, with whatever operating expense
    they don't account for following as one residual bar. That residual is the reason the
    two are read as a share of the reported operating step rather than summed into it: on a
    filer who classifies something unusually, summing would miss operating income and the
    error would land on the tax bar, which is the last place anyone would look for it.
    """
    row = df.iloc[-1]
    revenue = _num(row["Revenue"])
    net = _num(row["NetIncome"])
    if revenue is None or net is None:
        raise ValueError("that statement has no revenue and net income to bridge between")

    gross = _num(row["GrossProfit"])
    if gross is None:
        cost = _num(row["CostOfRevenue"])
        gross = revenue - cost if cost is not None else None
    operating = _num(row["OperatingIncome"])

    rows = [_row("Revenue", revenue, "start")]
    level = revenue
    if gross is not None:
        rows.append(_row("Cost of revenue", gross - level, "delta"))
        rows.append(_row("Gross profit", gross, "total", share=True))
        level = gross
    if operating is not None:
        if gross is None:
            # Without a gross profit to step through, the whole distance from revenue to
            # operating income is one bar — and it is cost of revenue *and* operating
            # expense together, so it says so rather than borrowing either name.
            rows.append(_row("Costs & expenses", operating - level, "delta"))
        else:
            rows.extend(_expense_rows(row, level - operating))
        rows.append(_row("Operating income", operating, "total", share=True))
        level = operating
    # Whatever is left between the last reported subtotal and net income. Named for what it
    # is rather than "Tax": below operating income sit interest, one-offs and tax together,
    # and a bar labelled for only one of them would be wrong on most companies.
    rows.append(_row("Tax, interest & other", net - level, "delta"))
    rows.append(_row("Net income", net, "total", share=True))
    return rows, _meta(df, row)


def _expense_rows(row, total):
    """The operating step, split into R&D and SG&A when the filer reported both.

    `total` is the step the subtotals demand — gross profit minus operating income — and
    the rows always add to it exactly. The named lines are drawn at what they were reported
    as and anything unexplained follows as one residual, so a split that doesn't account
    for the whole step is visible as a bar rather than absorbed into the ones beside it.
    """
    rnd = _num(row["ResearchDevelopment"])
    sga = _num(row["SellingGeneralAdministrative"])
    named = [("R&D", rnd), ("SG&A", sga)]
    have = [(label, value) for label, value in named if value is not None and value > 0]
    # A step that isn't positive means operating income came in above gross profit, which
    # happens on filers who book a gain up there. Nothing about that splits into R&D and
    # SG&A, so it stays one honest bar.
    if total <= 0 or not have or sum(value for _, value in have) > total * 1.001:
        return [_row("Operating expenses", -total, "delta")]

    rows = [_row(label, -value, "delta") for label, value in have]
    rest = total - sum(value for _, value in have)
    # Under a thousandth of the step it is rounding rather than a line item, and an
    # unlabelled sliver of a bar reads as a rendering fault.
    if abs(rest) > abs(total) * 0.001:
        rows.append(_row("Other opex", -rest, "delta"))
    return rows


def growth_bridge(df):
    """The earliest revenue in `df`, then what each period after it added or gave back.

    The opening bar is a level and everything after it is a change, so the last bar lands
    on the latest revenue — the same closure property the income bridge has, and here it
    comes free because consecutive revenues are exactly what the deltas are made of.
    """
    revenue = df["Revenue"].astype(float)
    if len(revenue) < 2:
        raise ValueError("a growth bridge needs at least two periods")

    labels = [str(df["Label"].iloc[i] or df.index[i].year) for i in range(len(revenue))]
    # The pillars carry the word, the change bars carry only their period. Without that the
    # closing pillar and the delta beside it would both read "FY2025" and mean different
    # things — the renderer wraps on the space, so it costs a second line rather than width.
    rows = [_row(f"{labels[0]} revenue", revenue.iloc[0], "start")]
    for i in range(1, len(revenue)):
        rows.append(_row(labels[i], revenue.iloc[i] - revenue.iloc[i - 1], "delta"))
    rows.append(_row(f"{labels[-1]} revenue", revenue.iloc[-1], "total"))
    return rows, _meta(df, df.iloc[-1])


def _meta(df, row):
    """What the renderer needs to caption and format the bridge it was handed."""
    return {"currency": currency_symbol(row["Currency"]),
            "label": str(row["Label"] or ""),
            "first": str(df.iloc[0]["Label"] or ""),
            "end": df.index[-1]}


BUILDERS = {"income": income_bridge, "growth": growth_bridge}


def bridge(ticker, kind=DEFAULT_BRIDGE, period=DEFAULT_PERIOD, periods=5):
    """Fetch statements and turn them into waterfall rows.

    The income bridge reads one period and the growth bridge reads a run of them, so the
    fetch is sized to the bridge rather than to a setting someone has to keep in step with
    it — asking for ten years of statements to draw one year's cost structure would spend
    the plan horizon for nothing.
    """
    if kind not in BUILDERS:
        raise ValueError(f"Unknown bridge: {kind}")
    wanted = 1 if kind == "income" else max(min(int(periods), MAX_PERIODS), 2)
    return BUILDERS[kind](fetch(ticker, period, wanted))
