"""Tests for the fetch path, the date range presets and footer attribution.

No network: the Stooq and Twelve Data endpoints are mocked with recorded response samples,
and the source above whichever one is under test is forced to fail so the fallback runs.
The API key is patched in per-test rather than read from the environment, so the suite
behaves the same on a machine that has one configured and on a machine that doesn't.
Run with: python -m unittest
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import pandas as pd

import config
import data
import renderers
import testsupport

# A trimmed but faithful sample of what stooq.com/q/d/l/?s=aapl.us&i=d returns.
STOOQ_CSV = """Date,Open,High,Low,Close,Volume
2024-01-02,187.15,188.44,183.89,185.64,82488700
2024-01-03,184.22,185.88,183.43,184.25,58414500
2024-01-04,182.15,183.09,180.88,181.91,71983600
2024-01-05,181.99,182.76,180.17,181.18,62303300
2024-01-08,182.09,185.60,181.50,185.56,59144500
"""


# A trimmed but faithful sample of a Twelve Data /time_series response. Prices are strings
# in the real thing, which is the detail worth keeping: a parser that forgot would produce
# a frame that plots as a flat line of NaN rather than raising.
TWELVEDATA_JSON = json.dumps({
    "meta": {"symbol": "AAPL", "interval": "1day", "currency": "USD",
             "exchange_timezone": "America/New_York", "exchange": "NASDAQ",
             "type": "Common Stock"},
    "values": [  # newest first, which is the order the fetcher asks for
        {"datetime": "2024-01-08", "open": "182.09", "high": "185.60",
         "low": "181.50", "close": "185.56", "volume": "59144500"},
        {"datetime": "2024-01-05", "open": "181.99", "high": "182.76",
         "low": "180.17", "close": "181.18", "volume": "62303300"},
        {"datetime": "2024-01-04", "open": "182.15", "high": "183.09",
         "low": "180.88", "close": "181.91", "volume": "71983600"},
    ],
    "status": "ok",
})


def _urlopen_returning(body):
    """Stand in for urllib.request.urlopen, which is used as a context manager."""
    resp = mock.MagicMock()
    resp.read.return_value = body.encode()
    resp.__enter__.return_value = resp
    return mock.Mock(return_value=resp)


def _urlopen_returning_each(*bodies):
    """Answer successive calls with successive bodies, for the paging tests."""
    def factory(*_a, **_kw):
        resp = mock.MagicMock()
        resp.read.return_value = bodies[min(factory.calls, len(bodies) - 1)].encode()
        resp.__enter__.return_value = resp
        factory.calls += 1
        return resp
    factory.calls = 0
    return factory


def _with_key(key="test-key"):
    """Configure a licensed key for the length of a block.

    Patched rather than read from the environment so the suite gives the same answer on a
    machine that has a real key configured and on one that doesn't.
    """
    return mock.patch.object(config, "TWELVEDATA_KEY", key)


# A trimmed but faithful sample of an FMP daily response. Newest first, and the daily
# endpoint has historically wrapped its rows in an object while the intraday ones return a
# bare array — both spellings are covered below.
FMP_DAILY_JSON = json.dumps({"symbol": "AAPL", "historical": [
    {"date": "2024-01-08", "open": 182.09, "high": 185.60, "low": 181.50,
     "close": 185.56, "volume": 59144500},
    {"date": "2024-01-05", "open": 181.99, "high": 182.76, "low": 180.17,
     "close": 181.18, "volume": 62303300},
    {"date": "2024-01-04", "open": 182.15, "high": 183.09, "low": 180.88,
     "close": 181.91, "volume": 71983600},
]})

FMP_INTRADAY_JSON = json.dumps([
    {"date": "2024-01-08 09:35:00", "open": 182.4, "low": 182.0, "high": 182.6,
     "close": 182.5, "volume": 120000},
    {"date": "2024-01-08 09:30:00", "open": 182.1, "low": 181.9, "high": 182.5,
     "close": 182.4, "volume": 250000},
])


def _with_fmp(key="test-key", years=5):
    """Configure an FMP key and plan horizon for the length of a block."""
    return mock.patch.multiple(config, FMP_KEY=key, FMP_HISTORY_YEARS=years)


class FMPParsingTests(unittest.TestCase):
    def test_daily_parses_to_the_shared_column_contract(self):
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_DAILY_JSON)):
            df = data._fmp("AAPL", "2024-01-01")

        self.assertEqual(list(df.columns), data.COLUMNS)
        self.assertEqual(len(df), 3)
        # Arrives newest first; every renderer expects the opposite.
        self.assertEqual(str(df.index[0].date()), "2024-01-04")
        self.assertAlmostEqual(df["Close"].iloc[-1], 185.56)

    def test_a_bare_array_parses_the_same_as_a_wrapped_one(self):
        # The intraday endpoints answer with a list rather than {"historical": [...]}.
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_INTRADAY_JSON)):
            df = data._fmp("AAPL", "2024-01-08", interval="5m")

        self.assertEqual(len(df), 2)
        self.assertEqual(str(df.index[0]), "2024-01-08 09:30:00")

    def test_intraday_goes_to_the_per_interval_endpoint(self):
        with _with_fmp("sekrit"), mock.patch(
                "urllib.request.urlopen",
                _urlopen_returning(FMP_INTRADAY_JSON)) as urlopen:
            data._fmp("AAPL", "2024-01-08", "2024-01-09", interval="15m")

        url = urlopen.call_args[0][0]
        self.assertIn("/historical-chart/15min?", url)  # the grid name, not Rolltape's
        self.assertIn("apikey=sekrit", url)
        self.assertIn("from=2024-01-08", url)
        self.assertIn("to=2024-01-09", url)

    def test_daily_goes_to_the_end_of_day_endpoint(self):
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_DAILY_JSON)) as urlopen:
            data._fmp("AAPL", "2024-01-01")
        self.assertIn("/historical-price-eod/full?", urlopen.call_args[0][0])

    def test_an_error_body_arrives_with_a_200(self):
        body = json.dumps({"Error Message": "Invalid API KEY."})
        with _with_fmp(), mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            with self.assertRaises(ValueError) as caught:
                data._fmp("AAPL", "2024-01-01")
        self.assertIn("Invalid API KEY", str(caught.exception))

    def test_the_rate_limit_is_named_as_itself(self):
        body = json.dumps({"Error Message": "Limit Reach. Please upgrade your plan."})
        with _with_fmp(), mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            with self.assertRaises(RuntimeError) as caught:
                data._fmp("AAPL", "2024-01-01")
        self.assertIn("rate limit", str(caught.exception))

    def test_no_key_fails_before_the_request_is_made(self):
        with mock.patch.object(config, "FMP_KEY", ""), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("asked anyway")):
            with self.assertRaises(RuntimeError):
                data._fmp("AAPL", "2024-01-01")

    def test_an_interval_off_the_grid_is_refused(self):
        # Every interval the interface offers is on FMP's grid today, so this guards the
        # next one added to INTERVALS without a matching entry here — which would otherwise
        # fall through to the daily endpoint and label the result 5-minute.
        thinned = {k: v for k, v in data.FMP_INTERVALS.items() if k != "5m"}
        with _with_fmp(), mock.patch.object(data, "FMP_INTERVALS", thinned), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("asked anyway")):
            with self.assertRaises(ValueError):
                data._fmp("AAPL", "2024-01-01", interval="5m")


class PlanHorizonTests(unittest.TestCase):
    """The Starter plan reaches back five years and says nothing when asked for more.

    That is the dangerous shape: a MAX request comes back as a short frame rather than an
    error, which would put five years of history under a MAX label and look entirely
    correct. So the ceiling is enforced before the request rather than after it.
    """

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(data, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.cache, True)
        data.reset_sources()
        self.addCleanup(data.reset_sources)

    def _years_ago(self, n):
        return (pd.Timestamp.today() - pd.Timedelta(days=int(365.25 * n))).strftime(
            "%Y-%m-%d")

    def test_a_window_inside_the_horizon_uses_the_licensed_feed(self):
        with _with_fmp():
            self.assertEqual(data._sources_for("1d", self._years_ago(2))[0], "fmp")

    def test_a_window_past_the_horizon_drops_the_licensed_feed(self):
        with _with_fmp():
            order = data._sources_for("1d", self._years_ago(20))
        self.assertNotIn("fmp", order)
        # Yahoo and Stooq have deeper history, so the deep chart still draws — just not
        # from the plan that cannot serve it.
        self.assertEqual(order[0], "yahoo")

    def test_a_deeper_plan_keeps_the_licensed_feed(self):
        # Upgrading to the 30-year plan is meant to be an env var, not a code change.
        with _with_fmp(years=30):
            self.assertIn("fmp", data._sources_for("1d", self._years_ago(20)))

    def test_a_max_chart_really_falls_through_rather_than_truncating(self):
        frame = pd.read_csv(io.StringIO(STOOQ_CSV), parse_dates=["Date"], index_col="Date")
        with _with_fmp(), \
             mock.patch.object(data, "_fmp", side_effect=AssertionError("asked anyway")), \
             mock.patch.object(data, "_yahoo", return_value=frame):
            data.fetch("AAPL", "1970-01-01")

        self.assertEqual(data.sources_used(), {"yahoo"})

    def test_licensed_only_past_the_horizon_says_what_to_do_about_it(self):
        # Nothing below the licensed feed to fall through to, so this has to fail — and
        # the message has to name the horizon rather than reading as a missing key.
        with _with_fmp(), mock.patch.object(config, "LICENSED_ONLY", True), \
             mock.patch.object(data, "_fmp", side_effect=AssertionError("asked anyway")):
            with self.assertRaises(ValueError) as caught:
                data.fetch("AAPL", "1970-01-01")

        message = str(caught.exception)
        self.assertIn("5 years", message)
        self.assertIn("ROLLTAPE_FMP_HISTORY_YEARS", message)


class TwelveDataParsingTests(unittest.TestCase):
    def test_parses_to_the_shared_column_contract(self):
        with _with_key(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(TWELVEDATA_JSON)):
            df = data._twelvedata("AAPL", "2024-01-01")

        self.assertEqual(list(df.columns), data.COLUMNS)
        self.assertEqual(len(df), 3)
        # Strings in, numbers out — and oldest first, whatever order they arrived in.
        self.assertEqual(str(df.index[0].date()), "2024-01-04")
        self.assertAlmostEqual(df["Close"].iloc[-1], 185.56)
        self.assertEqual(df["Close"].dtype.kind, "f")

    def test_an_error_body_arrives_with_a_200(self):
        # The endpoint reports failure in the payload, so a parser trusting the HTTP status
        # would hand back an empty frame and let the render fail somewhere less useful.
        body = json.dumps({"code": 404, "message": "**symbol** not found",
                           "status": "error"})
        with _with_key(), mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            with self.assertRaises(ValueError) as caught:
                data._twelvedata("NOPE", "2024-01-01")
        self.assertIn("not found", str(caught.exception))

    def test_the_rate_limit_is_named_as_itself(self):
        # The error anyone on the free tier actually hits. "Wait a minute" and "check the
        # symbol" are different instructions, so they get different exception types.
        body = json.dumps({"code": 429, "message": "API credits limit reached",
                           "status": "error"})
        with _with_key(), mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            with self.assertRaises(RuntimeError) as caught:
                data._twelvedata("AAPL", "2024-01-01")
        self.assertIn("rate limit", str(caught.exception))

    def test_no_key_fails_before_the_request_is_made(self):
        with mock.patch.object(config, "TWELVEDATA_KEY", ""), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("asked anyway")):
            with self.assertRaises(RuntimeError):
                data._twelvedata("AAPL", "2024-01-01")

    def test_an_interval_off_the_grid_is_refused(self):
        with _with_key(), mock.patch("urllib.request.urlopen",
                                     side_effect=AssertionError("asked anyway")):
            with self.assertRaises(ValueError):
                data._twelvedata("AAPL", "2024-01-01", interval="3s")

    def test_the_key_is_sent_and_bars_are_asked_for_newest_first(self):
        with _with_key("sekrit"), mock.patch(
                "urllib.request.urlopen", _urlopen_returning(TWELVEDATA_JSON)) as urlopen:
            data._twelvedata("AAPL", "2024-01-01", interval="5m")

        url = urlopen.call_args[0][0]
        self.assertIn("apikey=sekrit", url)
        self.assertIn("interval=5min", url)   # the grid name, not Rolltape's
        self.assertIn("order=DESC", url)      # what makes a truncated page well defined
        self.assertIn("timezone=Exchange", url)

    def test_a_range_longer_than_one_page_is_paged_through(self):
        # A full page means there is more behind it. Stopping there would end a `max` chart
        # two decades short and look entirely normal doing it.
        def page(dates):
            return json.dumps({"status": "ok", "values": [
                {"datetime": d, "open": "1", "high": "2", "low": "0.5",
                 "close": "1.5", "volume": "100"} for d in dates]})

        full = [str(d.date()) for d in
                pd.bdate_range(end="2024-06-01", periods=data.TWELVEDATA_PAGE)[::-1]]
        with _with_key(), mock.patch.object(data, "TWELVEDATA_PAGE", len(full)), \
             mock.patch("urllib.request.urlopen", _urlopen_returning_each(
                 page(full), page(["2000-01-04", "2000-01-03"]))) as urlopen:
            df = data._twelvedata("AAPL", "1999-01-01", "2024-06-01")

        self.assertEqual(urlopen.calls, 2)
        self.assertEqual(len(df), len(full) + 2)
        self.assertEqual(str(df.index[0].date()), "2000-01-03")
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertFalse(df.index.has_duplicates)

    def test_a_short_page_ends_the_paging(self):
        with _with_key(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(TWELVEDATA_JSON)) as urlopen:
            data._twelvedata("AAPL", "1970-01-01", "2024-06-01")
        self.assertEqual(urlopen.call_count, 1)

    def test_an_instrument_without_volume_still_draws(self):
        body = json.dumps({"status": "ok", "values": [
            {"datetime": "2024-01-04", "open": "1", "high": "2", "low": "0.5",
             "close": "1.5"},
            {"datetime": "2024-01-03", "open": "1", "high": "2", "low": "0.5",
             "close": "1.4"}]})
        with _with_key(), mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            df = data._twelvedata("^GSPC", "2024-01-01")

        # Dropping the rows instead would throw away perfectly good prices over a column
        # no chart divides by.
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df["Volume"]), [0.0, 0.0])


class StooqParsingTests(unittest.TestCase):
    def test_parses_to_the_shared_column_contract(self):
        with mock.patch("urllib.request.urlopen", _urlopen_returning(STOOQ_CSV)):
            df = data._stooq("AAPL", "2024-01-01", None)

        self.assertEqual(list(df.columns), data.COLUMNS)
        self.assertEqual(len(df), 5)
        self.assertEqual(str(df.index[0].date()), "2024-01-02")
        self.assertAlmostEqual(df["Close"].iloc[0], 185.64)

    def test_trims_to_the_requested_range(self):
        # Stooq ignores date bounds in the query, so fetch() has to trim locally.
        with mock.patch("urllib.request.urlopen", _urlopen_returning(STOOQ_CSV)):
            df = data._stooq("AAPL", "2024-01-04", "2024-01-05")

        self.assertEqual(len(df), 2)
        self.assertEqual(str(df.index[0].date()), "2024-01-04")
        self.assertEqual(str(df.index[-1].date()), "2024-01-05")

    def test_bad_symbol_answers_200_with_no_data(self):
        with mock.patch("urllib.request.urlopen", _urlopen_returning("No data\n")):
            with self.assertRaises(ValueError):
                data._stooq("NOPE", "2024-01-01", None)

    def test_us_symbols_get_the_market_suffix(self):
        self.assertEqual(data._stooq_symbol("AAPL"), "aapl.us")
        self.assertEqual(data._stooq_symbol("BP.UK"), "bp.uk")


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(data, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.cache, True)
        data.reset_sources()
        self.addCleanup(data.reset_sources)

    def test_falls_back_to_stooq_when_yahoo_fails(self):
        with mock.patch.object(data, "_yahoo", side_effect=RuntimeError("endpoint moved")), \
             mock.patch("urllib.request.urlopen", _urlopen_returning(STOOQ_CSV)):
            df = data.fetch("AAPL", "2024-01-01")

        self.assertEqual(len(df), 5)
        self.assertEqual(data.sources_used(), {"stooq"})

    def test_yahoo_is_preferred_and_stays_silent(self):
        frame = pd.read_csv(io.StringIO(STOOQ_CSV), parse_dates=["Date"], index_col="Date")

        with mock.patch.object(data, "_yahoo", return_value=frame) as yahoo, \
             mock.patch("urllib.request.urlopen") as urlopen:
            data.fetch("AAPL", "2024-01-01")

        yahoo.assert_called_once()
        urlopen.assert_not_called()  # Stooq is never touched when Yahoo answers
        self.assertEqual(data.sources_used(), {"yahoo"})
        self.assertIsNone(data.attribution())

    def test_stooq_carries_the_render_when_yfinance_is_absent(self):
        # Not just an import guard: yfinance is the dependency most likely to be missing
        # or broken on a given machine, and a render should survive that.
        with mock.patch.dict("sys.modules", {"yfinance": None}), \
             mock.patch("urllib.request.urlopen", _urlopen_returning(STOOQ_CSV)):
            df = data.fetch("AAPL", "2024-01-01")

        self.assertEqual(len(df), 5)
        self.assertEqual(data.sources_used(), {"stooq"})

    def test_both_sources_failing_names_both_causes(self):
        with mock.patch.object(data, "_yahoo", side_effect=RuntimeError("endpoint moved")), \
             mock.patch("urllib.request.urlopen", side_effect=OSError("unreachable")):
            with self.assertRaises(ValueError) as caught:
                data.fetch("AAPL", "2024-01-01")

        message = str(caught.exception)
        self.assertIn("yahoo", message)
        self.assertIn("stooq", message)

    def test_cache_remembers_which_source_wrote_it(self):
        with mock.patch.object(data, "_yahoo", side_effect=RuntimeError("endpoint moved")), \
             mock.patch("urllib.request.urlopen", _urlopen_returning(STOOQ_CSV)):
            data.fetch("AAPL", "2024-01-01")

        data.reset_sources()
        # Second call is served from cache; neither source should be contacted.
        with mock.patch.object(data, "_yahoo", side_effect=AssertionError("refetched")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("refetched")):
            df = data.fetch("AAPL", "2024-01-01")

        self.assertEqual(len(df), 5)
        self.assertEqual(data.sources_used(), {"stooq"})

    def test_the_licensed_feed_is_preferred_over_both_scrapers(self):
        frame = pd.read_csv(io.StringIO(STOOQ_CSV), parse_dates=["Date"], index_col="Date")

        with _with_key(), \
             mock.patch.object(data, "_twelvedata", return_value=frame) as licensed, \
             mock.patch.object(data, "_yahoo", side_effect=AssertionError("fell through")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("fell through")):
            data.fetch("AAPL", "2024-01-01")

        licensed.assert_called_once()
        self.assertEqual(data.sources_used(), {"twelvedata"})

    def test_yahoo_catches_a_licensed_feed_that_is_out_of_quota(self):
        # The free tier runs out mid-month and the paid one has a ceiling too. A render
        # that stops working on the 28th of every month is worse than one drawn from the
        # fallback and labelled honestly.
        frame = pd.read_csv(io.StringIO(STOOQ_CSV), parse_dates=["Date"], index_col="Date")

        with _with_key(), \
             mock.patch.object(data, "_twelvedata",
                               side_effect=RuntimeError("rate limit reached")), \
             mock.patch.object(data, "_yahoo", return_value=frame):
            df = data.fetch("AAPL", "2024-01-01")

        self.assertEqual(len(df), 5)
        self.assertEqual(data.sources_used(), {"yahoo"})

    def test_without_a_key_the_licensed_feed_is_never_called(self):
        # A fresh clone has no key. It must not spend a request finding that out, and the
        # error from a keyless call must not end up in a user-facing message.
        with mock.patch.object(config, "TWELVEDATA_KEY", ""), \
             mock.patch.object(config, "FMP_KEY", ""), \
             mock.patch.object(data, "_fmp",
                               side_effect=AssertionError("called without a key")), \
             mock.patch.object(data, "_twelvedata",
                               side_effect=AssertionError("called without a key")), \
             mock.patch.object(data, "_yahoo", side_effect=RuntimeError("endpoint moved")), \
             mock.patch("urllib.request.urlopen", _urlopen_returning(STOOQ_CSV)):
            df = data.fetch("AAPL", "2024-01-01")

        self.assertEqual(data.sources_used(), {"stooq"})
        self.assertEqual(len(df), 5)

    def test_licensed_only_refuses_to_fall_through_to_a_scraper(self):
        # The setting a paying deploy runs on: no licensed answer means no chart, rather
        # than a chart quietly drawn from data that may not be shown to a customer.
        with _with_key(), mock.patch.object(config, "LICENSED_ONLY", True), \
             mock.patch.object(data, "_twelvedata", side_effect=ValueError("upstream down")), \
             mock.patch.object(data, "_yahoo", side_effect=AssertionError("fell through")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("fell through")):
            with self.assertRaises(ValueError) as caught:
                data.fetch("AAPL", "2024-01-01")

        self.assertIn("upstream down", str(caught.exception))

    def test_licensed_only_without_a_key_says_what_to_do_about_it(self):
        with mock.patch.object(config, "TWELVEDATA_KEY", ""), \
             mock.patch.object(config, "LICENSED_ONLY", True), \
             mock.patch.object(data, "_yahoo", side_effect=AssertionError("fell through")):
            with self.assertRaises(ValueError) as caught:
                data.fetch("AAPL", "2024-01-01")

        self.assertIn("ROLLTAPE_FMP_KEY", str(caught.exception))


class CacheFreshnessTests(unittest.TestCase):
    """A range running up to now keeps gaining bars, so its cache entry has to expire.

    The End field is empty by default, so this is the path nearly every render takes. When
    it went stale nothing failed — the chart just quietly ended on an old bar.
    """

    DAY1 = "2026-08-07"
    DAY2 = "2026-08-19"  # eight sessions later

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(data, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.cache, True)
        data.reset_sources()
        self.addCleanup(data.reset_sources)

    def _on(self, day):
        return mock.patch.object(data, "_today", lambda: pd.Timestamp(day))

    def _yahoo_through(self, last):
        idx = pd.bdate_range("2024-01-02", last)
        frame = pd.DataFrame({c: range(len(idx)) for c in data.COLUMNS}, index=idx)
        return mock.patch.object(data, "_yahoo", lambda *a, **k: frame.astype(float))

    def _files(self):
        return sorted(os.listdir(self.cache))

    def test_open_ended_range_refetches_the_next_day(self):
        with self._on(self.DAY1), self._yahoo_through("2024-01-08"):
            first = data.fetch("AAPL", "2024-01-01", None)
        with self._on(self.DAY2), self._yahoo_through("2024-01-19"):
            later = data.fetch("AAPL", "2024-01-01", None)

        self.assertEqual(str(first.index[-1].date()), "2024-01-08")
        self.assertEqual(str(later.index[-1].date()), "2024-01-19")

    def test_open_ended_range_is_cached_within_the_day(self):
        # The preview refires on a debounce as the form changes, so same-day repeats have
        # to stay local — expiring per request would put Yahoo behind every keystroke.
        with self._on(self.DAY1), self._yahoo_through("2024-01-08"):
            data.fetch("AAPL", "2024-01-01", None)

        with self._on(self.DAY1), \
             mock.patch.object(data, "_yahoo", side_effect=AssertionError("refetched")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("refetched")):
            df = data.fetch("AAPL", "2024-01-01", None)

        self.assertEqual(str(df.index[-1].date()), "2024-01-08")

    def test_an_end_date_of_today_counts_as_open_ended(self):
        # "Through today" is the same request as "through now" — today's bar is still open.
        with self._on(self.DAY1), self._yahoo_through("2024-01-08"):
            data.fetch("AAPL", "2024-01-01", self.DAY1)
        with self._on(self.DAY2), self._yahoo_through("2024-01-19"):
            df = data.fetch("AAPL", "2024-01-01", self.DAY1)

        self.assertEqual(str(df.index[-1].date()), "2024-01-19")

    def test_closed_range_is_cached_indefinitely(self):
        # A finished historical window never changes; re-downloading it would be waste.
        with self._on(self.DAY1), self._yahoo_through("2024-01-08"):
            data.fetch("AAPL", "2024-01-01", "2024-01-08")

        with self._on(self.DAY2), \
             mock.patch.object(data, "_yahoo", side_effect=AssertionError("refetched")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("refetched")):
            df = data.fetch("AAPL", "2024-01-01", "2024-01-08")

        self.assertEqual(len(df), 5)

    def test_yesterdays_copy_is_pruned(self):
        with self._on(self.DAY1), self._yahoo_through("2024-01-08"):
            data.fetch("AAPL", "2024-01-01", None)
        self.assertEqual(len(self._files()), 1)

        with self._on(self.DAY2), self._yahoo_through("2024-01-19"):
            data.fetch("AAPL", "2024-01-01", None)

        # One file per ticker per day would otherwise pile up forever.
        self.assertEqual(self._files(), [f"AAPL_{data._cache_key('AAPL', '2024-01-01', None)}"
                                         f".{self.DAY2}.yahoo.csv"])

    def test_undated_entries_from_before_the_fix_are_retired(self):
        key = data._cache_key("AAPL", "2024-01-01", None)
        legacy = os.path.join(self.cache, f"AAPL_{key}.yahoo.csv")
        with self._on(self.DAY1), self._yahoo_through("2024-01-08"):
            data.fetch("AAPL", "2024-01-01", None)
            os.rename(os.path.join(self.cache, f"AAPL_{key}.{self.DAY1}.yahoo.csv"), legacy)

        with self._on(self.DAY2), self._yahoo_through("2024-01-19"):
            df = data.fetch("AAPL", "2024-01-01", None)

        self.assertEqual(str(df.index[-1].date()), "2024-01-19")
        self.assertFalse(os.path.exists(legacy))


class RangeTests(unittest.TestCase):
    """The presets behind the date selector."""

    def test_a_rolling_window_counts_back_from_today(self):
        self.assertEqual(data.resolve_range("1y", today="2026-08-08")["start"], "2025-08-08")
        self.assertEqual(data.resolve_range("1w", today="2026-08-08")["start"], "2026-08-01")

    def test_year_to_date_starts_in_january(self):
        self.assertEqual(data.resolve_range("ytd", today="2026-08-08")["start"],
                         "2026-01-01")

    def test_max_reaches_back_further_than_any_source_goes(self):
        self.assertEqual(data.resolve_range("max", today="2026-08-08")["start"],
                         "1970-01-01")

    def test_presets_stay_open_ended(self):
        # Yahoo treats an explicit end as exclusive, so pinning one to today would drop
        # today's bar — the one a year-to-date chart is being made for.
        for name in data.RANGES:
            with self.subTest(name=name):
                self.assertIsNone(data.resolve_range(name)["end"])

    def test_intraday_is_the_only_preset_asking_for_intraday_bars(self):
        for name in data.RANGES:
            with self.subTest(name=name):
                window = data.resolve_range(name)
                expected = "5m" if name == "1d" else "1d"
                self.assertEqual(window["interval"], expected)

    def test_an_unknown_preset_is_rejected(self):
        with self.assertRaises(ValueError):
            data.resolve_range("last tuesday")

    def test_every_preset_carries_what_the_buttons_need(self):
        # The interface builds itself from this registry, so a preset missing either label
        # ships as a blank button.
        for name, spec in data.RANGES.items():
            with self.subTest(name=name):
                self.assertTrue(spec["short"])
                self.assertTrue(spec["label"])


class IntradayTests(unittest.TestCase):
    """Intraday never falls through to Stooq, and it perishes. Both guarded here."""

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(data, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.cache, True)
        data.reset_sources()
        self.addCleanup(data.reset_sources)

    def _at(self, when):
        return mock.patch.object(data, "_now", lambda: pd.Timestamp(when))

    def _bars(self, n=20):
        idx = pd.date_range("2026-08-07 09:30", periods=n, freq="5min")
        frame = pd.DataFrame({c: range(n) for c in data.COLUMNS}, index=idx).astype(float)
        return mock.patch.object(data, "_yahoo", lambda *a, **k: frame)

    def test_stooq_is_never_asked_for_intraday(self):
        # Stooq would answer with daily bars, which would be labelled 5-minute and look
        # entirely plausible. A failed render is the better outcome.
        with mock.patch.object(data, "_yahoo", side_effect=RuntimeError("endpoint moved")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("stooq asked")):
            with self.assertRaises(ValueError) as caught:
                data.fetch("AAPL", "2026-08-01", None, "5m")

        self.assertIn("Stooq serves daily and coarser", str(caught.exception))

    def test_daily_still_falls_back_to_stooq(self):
        with mock.patch.object(data, "_yahoo", side_effect=RuntimeError("endpoint moved")), \
             mock.patch("urllib.request.urlopen", _urlopen_returning(STOOQ_CSV)):
            data.fetch("AAPL", "2024-01-01", None, "1d")

        self.assertEqual(data.sources_used(), {"stooq"})

    def test_cache_expires_at_the_bar_interval(self):
        with self._at("2026-08-07 10:00"), self._bars():
            data.fetch("AAPL", "2026-08-07", None, "5m")

        # Five minutes later the last bar has been replaced, so the cache must not answer.
        with self._at("2026-08-07 10:05"), \
             mock.patch.object(data, "_yahoo", side_effect=AssertionError("served stale")):
            with self.assertRaises(ValueError):
                data.fetch("AAPL", "2026-08-07", None, "5m")

    def test_cache_holds_within_the_bar_interval(self):
        with self._at("2026-08-07 10:00"), self._bars():
            data.fetch("AAPL", "2026-08-07", None, "5m")

        with self._at("2026-08-07 10:04"), \
             mock.patch.object(data, "_yahoo", side_effect=AssertionError("refetched")):
            df = data.fetch("AAPL", "2026-08-07", None, "5m")

        self.assertEqual(len(df), 20)

    def test_intervals_do_not_share_a_cache_entry(self):
        with self._at("2026-08-07 10:00"), self._bars():
            data.fetch("AAPL", "2026-08-07", None, "5m")

        with self._at("2026-08-07 10:00"), \
             mock.patch.object(data, "_yahoo", side_effect=AssertionError("wrong entry")):
            with self.assertRaises(ValueError):
                data.fetch("AAPL", "2026-08-07", None, "15m")

    def test_daily_cache_names_are_unchanged_by_intervals_existing(self):
        # Daily keys leave the interval out of the hash, so caches written before intraday
        # existed stay valid instead of being silently orphaned.
        self.assertEqual(data._cache_key("AAPL", "2024-01-01", None),
                         data._cache_key("AAPL", "2024-01-01", None, "1d"))
        self.assertNotEqual(data._cache_key("AAPL", "2024-01-01", None, "1d"),
                            data._cache_key("AAPL", "2024-01-01", None, "5m"))

    def test_exchange_time_survives_the_cache_round_trip(self):
        idx = pd.date_range("2026-08-07 09:30", periods=6, freq="5min", tz="America/New_York")
        frame = pd.DataFrame({c: range(6) for c in data.COLUMNS}, index=idx).astype(float)

        with mock.patch.dict("sys.modules", {"yfinance": mock.MagicMock(
                download=mock.Mock(return_value=frame))}):
            got = data._yahoo("AAPL", "2026-08-07", None, "5m")

        self.assertIsNone(got.index.tz)  # naive, so the CSV round-trip can't drift
        self.assertEqual(f"{got.index[0]:%H:%M}", "09:30")  # still the opening bell

    def test_volatility_annualises_per_interval(self):
        # 252 is a count of daily bars. Using it on 5-minute returns understates
        # volatility by the square root of the bars in a session.
        self.assertEqual(data.periods_per_year("1d"), 252.0)
        self.assertAlmostEqual(data.periods_per_year("5m"), 252.0 * 78)
        self.assertAlmostEqual(data.periods_per_year("1h"), 252.0 * 6.5)


class SessionTrimTests(unittest.TestCase):
    """The `sessions` trim behind the 1D preset.

    The preset asks for several days of bars and keeps the tail, because which day the
    last session falls on depends on weekends and holidays.
    """

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(data, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.cache, True)
        data.reset_sources()
        self.addCleanup(data.reset_sources)
        # Two sessions of 5-minute bars, which is what "intraday" asks a source for.
        self.frame = testsupport.synthetic("AAPL", "2024-01-11", "2024-01-12", "5m")

    def test_a_session_is_cut_into_bars_at_the_interval(self):
        idx = testsupport.session_index("2024-01-12", "2024-01-12", "5min")
        self.assertEqual(len(idx), 78)  # 09:30 to 16:00, exclusive of the close
        self.assertEqual(str(idx[0].time()), "09:30:00")
        self.assertEqual(str(idx[-1].time()), "15:55:00")

    def test_intraday_keeps_only_the_most_recent_session(self):
        with mock.patch.object(data, "_yahoo", return_value=self.frame):
            df = data.fetch("AAPL", "2024-01-11", None, "5m", sessions=1)

        self.assertEqual(len(df), 78)
        self.assertEqual(df.index.normalize().nunique(), 1)
        self.assertEqual(str(df.index[-1].date()), "2024-01-12")

    def test_the_cache_keeps_what_the_source_sent_not_what_the_render_kept(self):
        # The session trim isn't part of the cache key, so storing a trimmed frame under
        # that key would hand the next reader a shorter window than it asked for.
        with mock.patch.object(data, "_yahoo", return_value=self.frame):
            data.fetch("AAPL", "2024-01-11", None, "5m", sessions=1)

        path, _ = data._find_cached("AAPL", "2024-01-11", None, "5m")
        self.assertEqual(len(pd.read_csv(path, index_col=0, parse_dates=True)),
                         len(self.frame))

    def test_a_cached_intraday_frame_is_still_cut_to_one_session(self):
        # The trim runs on the way out of the cache too, and it needs the timestamps to
        # have survived the CSV round trip as timestamps rather than strings.
        with mock.patch.object(data, "_yahoo", return_value=self.frame):
            fresh = data.fetch("AAPL", "2024-01-11", None, "5m", sessions=1)

        with mock.patch.object(data, "_yahoo", side_effect=AssertionError("refetched")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("refetched")):
            cached = data.fetch("AAPL", "2024-01-11", None, "5m", sessions=1)

        self.assertIsInstance(cached.index, pd.DatetimeIndex)
        self.assertTrue(cached.index.equals(fresh.index))

    def test_a_window_too_short_to_animate_is_refused(self):
        one_bar = self.frame.iloc[:1]
        with mock.patch.object(data, "_yahoo", return_value=one_bar), \
             mock.patch("urllib.request.urlopen", _urlopen_returning("No data\n")):
            with self.assertRaises(ValueError):
                data.fetch("AAPL", "2024-01-11", None, "5m")


class FooterTests(unittest.TestCase):
    def setUp(self):
        data.reset_sources()
        self.addCleanup(data.reset_sources)

    def test_yahoo_leaves_the_footer_untouched(self):
        data._SOURCES["AAPL"] = "yahoo"
        self.assertEqual(renderers._footer_text("@mychannel"), "@mychannel")
        self.assertIsNone(renderers._footer_text(None))

    def test_stooq_is_appended_to_the_user_footer(self):
        data._SOURCES["AAPL"] = "stooq"
        self.assertEqual(renderers._footer_text("@mychannel"),
                         "@mychannel  ·  Data: Stooq")

    def test_stooq_stands_alone_without_a_user_footer(self):
        data._SOURCES["AAPL"] = "stooq"
        self.assertEqual(renderers._footer_text(None), "Data: Stooq")

    def test_a_render_that_mixed_sources_names_both(self):
        # A comparison chart can take one ticker from the licensed feed and another from
        # the fallback. Naming only one of them would be a false statement about the rest.
        data._SOURCES["AAPL"] = "stooq"
        data._SOURCES["MSFT"] = "twelvedata"
        self.assertEqual(renderers._footer_text(None), "Data: Twelve Data, Stooq")

    def test_the_licensed_feed_is_credited(self):
        data._SOURCES["AAPL"] = "twelvedata"
        self.assertEqual(renderers._footer_text(None), "Data: Twelve Data")

    def test_yahoo_alone_still_says_nothing(self):
        # Nobody to credit and nothing surprising to disclose, so the footer stays clean.
        data._SOURCES["AAPL"] = "yahoo"
        self.assertIsNone(data.attribution())


# A trimmed but faithful sample of each FMP event endpoint. Both splits inside one response
# so the window trim has something to remove, and a dividend from outside it for the same
# reason — these endpoints answer with a count rather than a range.
FMP_SPLITS_JSON = json.dumps([
    {"symbol": "NVDA", "date": "2024-06-10", "numerator": 10, "denominator": 1},
    {"symbol": "NVDA", "date": "2021-07-20", "numerator": 4, "denominator": 1},
])

FMP_DIVIDENDS_JSON = json.dumps([
    {"symbol": "NVDA", "date": "2024-06-11", "dividend": 0.01, "adjDividend": 0.01},
    {"symbol": "NVDA", "date": "2023-03-08", "dividend": 0.04, "adjDividend": 0.004},
])

FMP_EARNINGS_JSON = json.dumps([
    {"symbol": "NVDA", "date": "2024-05-22", "epsActual": 6.12, "epsEstimated": 5.59},
    {"symbol": "NVDA", "date": "2024-02-21", "epsActual": 5.16, "epsEstimated": 4.64},
])

# Twelve Data keys its rows under the endpoint name rather than "values", and states a split
# as the two factors rather than one ratio.
TWELVEDATA_SPLITS_JSON = json.dumps({"splits": [
    {"date": "2024-06-10", "from_factor": 1, "to_factor": 10},
]})

TWELVEDATA_DIVIDENDS_JSON = json.dumps({"dividends": [
    {"ex_date": "2024-06-11", "amount": 0.01},
]})


class CorporateEventTests(unittest.TestCase):
    """The dated events the timeline chart marks by itself.

    Same no-network rule as everything else here: the endpoints answer from recorded
    samples, and yfinance is never installed far enough to be reached. What these are
    really about is the two rules that make an automatic callout trustworthy — a kind
    arrives whole from one source or not at all, and a lookup that fails costs the marks
    rather than the render.
    """

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(data, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.cache, True)

    def test_fmp_splits_carry_their_ratio(self):
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_SPLITS_JSON)):
            rows = data.events("NVDA", "2024-01-01", "2024-12-31", ["splits"])
        self.assertEqual(rows, [{"date": "2024-06-10", "kind": "splits",
                                 "label": "10-for-1 split"}])

    def test_fmp_dividends_carry_their_amount(self):
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_DIVIDENDS_JSON)):
            rows = data.events("NVDA", "2024-01-01", "2024-12-31", ["dividends"])
        self.assertEqual([r["label"] for r in rows], ["Dividend $0.01"])

    def test_an_earnings_row_is_labelled_as_itself(self):
        # Deliberately not "Q2 earnings": the reporting date is in the quarter after the
        # one being reported, and a fiscal year that doesn't follow the calendar makes the
        # arithmetic wrong in a way nobody would check. The date carries the meaning.
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_EARNINGS_JSON)):
            rows = data.events("NVDA", "2024-01-01", "2024-12-31", ["earnings"])
        self.assertEqual([r["label"] for r in rows], ["Earnings", "Earnings"])
        self.assertEqual([r["date"] for r in rows], ["2024-02-21", "2024-05-22"])

    def test_events_outside_the_window_are_dropped(self):
        # FMP answers with a count rather than a range, so the trim is local — and a 2021
        # split drawn onto a 2024 chart would land on the first bar of it.
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_SPLITS_JSON)):
            rows = data.events("NVDA", "2024-01-01", "2024-12-31", ["splits"])
        self.assertEqual(len(rows), 1)

    def test_twelvedata_keys_its_rows_under_the_endpoint_name(self):
        with _with_key(), mock.patch.object(config, "FMP_KEY", ""), \
             mock.patch("urllib.request.urlopen",
                        _urlopen_returning(TWELVEDATA_SPLITS_JSON)):
            rows = data.events("NVDA", "2024-01-01", "2024-12-31", ["splits"])
        self.assertEqual([r["label"] for r in rows], ["10-for-1 split"])

    def test_twelvedata_dividends_read_the_ex_date(self):
        with _with_key(), mock.patch.object(config, "FMP_KEY", ""), \
             mock.patch("urllib.request.urlopen",
                        _urlopen_returning(TWELVEDATA_DIVIDENDS_JSON)):
            rows = data.events("NVDA", "2024-01-01", "2024-12-31", ["dividends"])
        self.assertEqual(rows, [{"date": "2024-06-11", "kind": "dividends",
                                 "label": "Dividend $0.01"}])

    def test_a_kind_is_never_assembled_from_two_sources(self):
        """A source that fails is passed over entirely rather than contributed from.

        Half of FMP's splits plus half of Twelve Data's would be a set that is complete
        from neither and looks authoritative anyway. That is the same failure as labelling
        daily bars five-minute, in a place nobody would think to check.
        """
        with _with_fmp(), _with_key(), \
             mock.patch.object(data, "_fmp_events", side_effect=ValueError("down")), \
             mock.patch.object(data, "_twelvedata_events",
                               return_value=[{"date": "2024-06-10", "kind": "splits",
                                              "label": "10-for-1 split"}]):
            rows = data.events("NVDA", "2024-01-01", "2024-12-31", ["splits"])
        self.assertEqual([r["label"] for r in rows], ["10-for-1 split"])

    def test_an_empty_answer_is_an_answer(self):
        # A company that has never split has no splits. Falling through to a second source
        # would spend another call to be told the same thing.
        with _with_fmp(), _with_key(), \
             mock.patch.object(data, "_fmp_events", return_value=[]), \
             mock.patch.object(data, "_twelvedata_events",
                               side_effect=AssertionError("asked anyway")):
            self.assertEqual(data.events("NVDA", "2024-01-01", "2024-12-31", ["splits"]), [])

    def test_every_source_failing_costs_the_marks_and_not_the_render(self):
        # The opposite of what fetch() does, and deliberate: a chart without its prices is
        # nothing, but a chart whose earnings lookup timed out is the chart that was asked
        # for, missing an overlay.
        with _with_fmp(), \
             mock.patch.object(data, "_fmp_events", side_effect=ValueError("down")), \
             mock.patch.object(data, "_yahoo_events", side_effect=ValueError("down")):
            self.assertEqual(data.events("NVDA", "2024-01-01", "2024-12-31",
                                         ["splits", "earnings"]), [])

    def test_stooq_is_never_asked_for_events(self):
        # It is a price CSV and publishes none of this, so it drops out the same way it
        # drops out for intraday.
        self.assertNotIn("stooq", data._event_sources("2024-01-01"))

    def test_the_plan_horizon_applies_to_events_too(self):
        # A five-year plan is no better at reaching ten years back for an earnings date
        # than it is for a price, and it comes up short the same silent way.
        old = (pd.Timestamp.today() - pd.Timedelta(days=int(365.25 * 20))).strftime(
            "%Y-%m-%d")
        with _with_fmp():
            self.assertNotIn("fmp", data._event_sources(old))
            self.assertIn("fmp", data._event_sources("2024-01-01"))

    def test_asking_for_nothing_makes_no_request(self):
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     side_effect=AssertionError("asked anyway")):
            self.assertEqual(data.events("NVDA", "2024-01-01", "2024-12-31", []), [])

    def test_an_unknown_kind_is_ignored_rather_than_fetched(self):
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     side_effect=AssertionError("asked anyway")):
            self.assertEqual(data.events("NVDA", "2024-01-01", "2024-12-31",
                                         ["buybacks"]), [])

    def test_a_second_call_comes_off_the_cache(self):
        # Every preview redraws the chart, so without this a keystroke would spend a call
        # against a metered API.
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_SPLITS_JSON)):
            first = data.events("NVDA", "2024-01-01", "2024-12-31", ["splits"])
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     side_effect=AssertionError("asked again")):
            self.assertEqual(data.events("NVDA", "2024-01-01", "2024-12-31", ["splits"]),
                             first)

    def test_the_event_cache_does_not_collide_with_the_price_cache(self):
        """`_drop_superseded` globs the price cache and must not sweep these up.

        It deletes every file matching one range's key, so an events file landing inside
        that pattern would be retired as a stale price frame — refetched on every render,
        against a metered API, with nothing to show for it.
        """
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_SPLITS_JSON)):
            data.events("NVDA", "2024-01-01", None, ["splits"])
        before = os.listdir(self.cache)
        data._drop_superseded("NVDA", "2024-01-01", None)
        self.assertEqual(sorted(os.listdir(self.cache)), sorted(before))
        self.assertTrue(any(f.endswith(".events.json") for f in before))

    def test_a_malformed_row_is_skipped_rather_than_drawn(self):
        body = json.dumps([
            {"date": "2024-06-10", "numerator": 10, "denominator": 1},
            {"date": None, "numerator": 2, "denominator": 1},        # no date
            {"date": "2024-07-01"},                                   # no ratio
        ])
        with _with_fmp(), mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            rows = data.events("NVDA", "2024-01-01", "2024-12-31", ["splits"])
        self.assertEqual([r["date"] for r in rows], ["2024-06-10"])


if __name__ == "__main__":
    unittest.main()
