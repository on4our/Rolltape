"""Tests for economic series — the FRED source, and what a chart does with one.

No network: the FRED endpoints are mocked with recorded response samples, and the key is
patched in per-test rather than read from the environment so the suite behaves the same on
a machine that has one configured and on a machine that doesn't. The drawing tests take
their observations from testsupport.py and draw a single still rather than encoding a clip.

What this deliberately does not cover: the price sources, which are test_data.py's, and the
suggestion field's ticker half, which is test_tickers.py's. What it does cover is every
place the two kinds of symbol have to behave differently — routing, refusals, units and
labelling.
Run with: python -m unittest
"""

import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from unittest import mock

import pandas as pd

import app as appmod
import config
import data
import renderers
import testsupport

# A trimmed but faithful sample of a FRED /series/observations response. Oldest first, values
# are strings, and the "." is the detail worth keeping: FRED writes a missing observation as
# one rather than leaving the row out, and a parser that forgot would plot it as a hole or
# raise on it.
OBSERVATIONS_JSON = json.dumps({
    "realtime_start": "2024-07-01", "realtime_end": "2024-07-01",
    "observation_start": "2024-01-01", "observation_end": "2024-06-01",
    "units": "lin", "count": 5,
    "observations": [
        {"realtime_start": "2024-07-01", "realtime_end": "2024-07-01",
         "date": "2024-01-01", "value": "3.7"},
        {"realtime_start": "2024-07-01", "realtime_end": "2024-07-01",
         "date": "2024-02-01", "value": "3.9"},
        {"realtime_start": "2024-07-01", "realtime_end": "2024-07-01",
         "date": "2024-03-01", "value": "."},
        {"realtime_start": "2024-07-01", "realtime_end": "2024-07-01",
         "date": "2024-04-01", "value": "3.9"},
        {"realtime_start": "2024-07-01", "realtime_end": "2024-07-01",
         "date": "2024-05-01", "value": "4.0"},
    ],
})

# And of /series, which is where a chart gets its title and its units.
SERIES_JSON = json.dumps({"seriess": [{
    "id": "UNRATE", "title": "Unemployment Rate",
    "observation_start": "1948-01-01", "observation_end": "2024-06-01",
    "frequency": "Monthly", "frequency_short": "M",
    "units": "Percent", "units_short": "%",
    "seasonal_adjustment": "Seasonally Adjusted", "seasonal_adjustment_short": "SA",
}]})

SEARCH_JSON = json.dumps({"seriess": [
    {"id": "CPIAUCSL", "title": "Consumer Price Index for All Urban Consumers"},
    {"id": "CPALTT01USM657N", "title": "Consumer Price Index: Total for United States"},
]})


def _urlopen_returning(body):
    """Stand in for urllib.request.urlopen, which is used as a context manager."""
    resp = mock.MagicMock()
    resp.read.return_value = body.encode()
    resp.__enter__.return_value = resp
    return mock.Mock(return_value=resp)


def _urlopen_raising(code, message):
    """FRED reports a bad key or a missing series as a 4xx with a JSON body."""
    body = json.dumps({"error_code": code, "error_message": message}).encode()
    err = urllib.error.HTTPError("https://api.stlouisfed.org", code, "Bad Request",
                                 {}, io.BytesIO(body))
    return mock.Mock(side_effect=err)


def _with_fred(key="test-key"):
    """Configure a FRED key for the length of a block."""
    return mock.patch.object(config, "FRED_KEY", key)


class SourceRoutingTests(unittest.TestCase):
    """The symbol picks the source list, and the two kinds never fall into each other."""

    def setUp(self):
        self.addCleanup(data.reset_sources)

    def test_an_economic_symbol_only_ever_asks_fred(self):
        with _with_fred():
            self.assertEqual(data._sources_for("1d", None, "FRED:UNRATE"), ("fred",))

    def test_a_ticker_never_asks_fred(self):
        with _with_fred():
            self.assertNotIn("fred", data._sources_for("1d", None, "AAPL"))

    def test_a_missing_key_drops_the_source_rather_than_falling_through(self):
        # The failure this prevents: an economic symbol quietly reaching a price feed,
        # which would answer for a *ticker* of that name if one happened to exist.
        with mock.patch.object(config, "FRED_KEY", ""):
            self.assertEqual(data._sources_for("1d", None, "FRED:UNRATE"), ())

    def test_intraday_drops_fred_the_way_it_drops_stooq(self):
        with _with_fred():
            self.assertEqual(data._sources_for("5m", None, "FRED:UNRATE"), ())

    def test_licensed_only_keeps_fred(self):
        # A deployment that takes money still has to be able to draw CPI. FRED is a
        # documented API used with a registered key, not a scrape.
        with _with_fred(), mock.patch.object(config, "LICENSED_ONLY", True):
            self.assertEqual(data._sources_for("1d", None, "FRED:UNRATE"), ("fred",))

    def test_a_missing_key_says_where_to_get_one(self):
        with mock.patch.object(config, "FRED_KEY", ""):
            message = data._nothing_eligible("FRED:UNRATE", "2024-01-01", "1d")
        self.assertIn("ROLLTAPE_FRED_KEY", message)
        self.assertIn("fred.stlouisfed.org", message)

    def test_an_intraday_request_says_why_there_is_nothing_to_draw(self):
        with _with_fred():
            message = data._nothing_eligible("FRED:UNRATE", "2024-01-01", "5m")
        self.assertIn("daily", message)

    def test_the_footer_names_fred(self):
        data._SOURCES["FRED:UNRATE"] = "fred"
        self.assertIn("FRED", data.attribution())


class ParsingTests(unittest.TestCase):
    def test_observations_parse_to_the_shared_column_contract(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      _urlopen_returning(OBSERVATIONS_JSON)):
            df = data._fred("FRED:UNRATE", "2024-01-01", "2024-06-01")
        self.assertEqual(list(df.columns), data.COLUMNS)
        self.assertEqual(list(df.index), list(pd.to_datetime(
            ["2024-01-01", "2024-02-01", "2024-04-01", "2024-05-01"])))
        self.assertAlmostEqual(df["Close"].iloc[0], 3.7)

    def test_a_missing_observation_is_dropped_rather_than_plotted(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      _urlopen_returning(OBSERVATIONS_JSON)):
            df = data._fred("FRED:UNRATE", "2024-01-01", "2024-06-01")
        self.assertEqual(len(df), 4)  # five observations, one of them a "."
        self.assertFalse(df["Close"].isna().any())

    def test_open_high_low_and_close_are_the_one_observation(self):
        # Not a shortcut — it is what the data is. The consequence is that a candlestick of
        # it is meaningless, which is why both clean_config and render_candles refuse one.
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      _urlopen_returning(OBSERVATIONS_JSON)):
            df = data._fred("FRED:UNRATE", "2024-01-01")
        for col in ("Open", "High", "Low"):
            self.assertTrue((df[col] == df["Close"]).all())
        self.assertTrue((df["Volume"] == 0).all())

    def test_no_key_fails_before_the_request_is_made(self):
        with mock.patch.object(config, "FRED_KEY", ""), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("called anyway")):
            with self.assertRaises(RuntimeError):
                data._fred("FRED:UNRATE", "2024-01-01")

    def test_a_series_id_that_is_not_one_is_refused_before_the_request(self):
        # Also what keeps a symbol typed into the ticker field out of a file path.
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      side_effect=AssertionError("called anyway")):
            with self.assertRaises(ValueError):
                data._fred("FRED:../../etc/passwd", "2024-01-01")

    def test_intraday_is_refused(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      side_effect=AssertionError("called anyway")):
            with self.assertRaises(ValueError):
                data._fred("FRED:UNRATE", "2024-01-01", interval="5m")

    def test_an_error_body_is_read_off_the_4xx(self):
        # The status line says "Bad Request" for both a dead key and an unknown series, and
        # those need completely different instructions.
        with _with_fred(), mock.patch(
                "urllib.request.urlopen",
                _urlopen_raising(400, "The series does not exist.")):
            with self.assertRaises(ValueError) as caught:
                data._fred("FRED:NOPE", "2024-01-01")
        self.assertIn("does not exist", str(caught.exception))

    def test_the_rate_limit_is_named_as_itself(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      _urlopen_raising(429, "Too many requests.")):
            with self.assertRaises(RuntimeError) as caught:
                data._fred("FRED:UNRATE", "2024-01-01")
        self.assertIn("rate limit", str(caught.exception))

    def test_the_key_and_the_window_are_sent(self):
        with _with_fred("secret"), mock.patch(
                "urllib.request.urlopen",
                _urlopen_returning(OBSERVATIONS_JSON)) as urlopen:
            data._fred("FRED:UNRATE", "2024-01-01", "2024-06-01")
        url = urlopen.call_args[0][0]
        self.assertIn("series_id=UNRATE", url)
        self.assertIn("api_key=secret", url)
        self.assertIn("observation_start=2024-01-01", url)
        self.assertIn("observation_end=2024-06-01", url)


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        patcher = mock.patch.object(data, "CACHE_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        data.clear_economic_meta()
        self.addCleanup(data.clear_economic_meta)

    def test_title_and_units_come_back_from_the_series_endpoint(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      _urlopen_returning(SERIES_JSON)):
            meta = data.economic_meta("FRED:UNRATE")
        self.assertEqual(meta["title"], "Unemployment Rate")
        self.assertEqual(meta["units"], "Percent")
        self.assertEqual(meta["frequency"], "Monthly")
        self.assertEqual(meta["seasonal"], "SA")

    def test_a_failed_lookup_degrades_to_the_id_rather_than_raising(self):
        # A title is worth a request. It is not worth a render.
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      side_effect=OSError("unreachable")):
            meta = data.economic_meta("FRED:UNRATE")
        self.assertEqual(meta["title"], "UNRATE")
        self.assertEqual(meta["units"], "")

    def test_without_a_key_no_request_is_made_at_all(self):
        with mock.patch.object(config, "FRED_KEY", ""), \
             mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("called anyway")):
            self.assertEqual(data.economic_meta("FRED:UNRATE")["title"], "UNRATE")

    def test_metadata_is_cached_to_disk_for_the_render_subprocess(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      _urlopen_returning(SERIES_JSON)) as urlopen:
            data.economic_meta("FRED:UNRATE")
        self.assertEqual(urlopen.call_count, 1)

        # A fresh process has the file but not the memo, which is the case that matters:
        # every render is a new process and would otherwise re-request on every job.
        data.clear_economic_meta()
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      side_effect=AssertionError("refetched")):
            self.assertEqual(data.economic_meta("FRED:UNRATE")["title"],
                             "Unemployment Rate")

    def test_a_read_only_cache_still_answers(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      _urlopen_returning(SERIES_JSON)), \
             mock.patch("builtins.open", side_effect=OSError("read-only")):
            meta = data.economic_meta("FRED:UNRATE")
        self.assertEqual(meta["title"], "Unemployment Rate")


class SearchTests(unittest.TestCase):
    """The suggestion field. The built-in list is the floor; the prefix reaches past it."""

    def setUp(self):
        data.clear_search_cache()
        self.addCleanup(data.clear_search_cache)
        # Yahoo is not what is under test here, and letting it be asked would put the
        # network in the suite.
        patcher = mock.patch.object(data, "_yahoo_search_cached", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def symbols(self, query, **kw):
        return [hit["symbol"] for hit in data.search(query, **kw)]

    def test_a_series_is_found_by_the_thing_it_measures(self):
        # Nobody remembers CPIAUCSL. Typing what the series is about has to find it, or the
        # built-in list may as well not exist.
        self.assertIn("FRED:CPIAUCSL", self.symbols("inflation"))
        self.assertIn("FRED:UNRATE", self.symbols("unemployment"))
        self.assertIn("FRED:MORTGAGE30US", self.symbols("mortgage"))

    def test_a_series_is_found_by_its_id(self):
        self.assertEqual(self.symbols("UNRATE")[0], "FRED:UNRATE")

    def test_an_exact_id_outranks_a_company_with_those_letters_in_its_name(self):
        # _match_rank strips the prefix before ranking, so an exact series id is an exact
        # match rather than a substring buried under every name that contains it.
        self.assertEqual(self.symbols("GDP")[0], "FRED:GDP")

    def test_economic_hits_are_badged_so_the_prefix_explains_itself(self):
        hit = next(h for h in data.search("unemployment") if h["symbol"].startswith("FRED:"))
        self.assertEqual(hit["exchange"], "FRED")
        self.assertEqual(hit["type"], "economic")

    def test_a_bare_prefix_lists_the_built_in_series(self):
        # A menu rather than an empty dropdown, which is what makes the namespace
        # discoverable by typing it.
        hits = self.symbols("FRED:", limit=50)
        self.assertGreater(len(hits), 10)
        self.assertTrue(all(h.startswith("FRED:") for h in hits))

    def test_a_prefixed_query_searches_fred_rather_than_yahoo(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      _urlopen_returning(SEARCH_JSON)) as urlopen:
            hits = self.symbols("FRED:CPI")
        self.assertIn("series/search", urlopen.call_args[0][0])
        self.assertIn("FRED:CPALTT01USM657N", hits)  # found only by asking FRED
        data._yahoo_search_cached.assert_not_called()

    def test_a_plain_query_does_not_spend_a_request_on_fred(self):
        # Two lookups on every keystroke of every ticker anyone types is the cost this
        # avoids; the built-in list is what keeps the common economic queries answerable.
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      side_effect=AssertionError("asked FRED")):
            self.symbols("NVDA")

    def test_a_failed_fred_lookup_narrows_the_list_rather_than_breaking_the_field(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      side_effect=OSError("unreachable")):
            self.assertIn("FRED:CPIAUCSL", self.symbols("FRED:CPI"))


class ConfigTests(unittest.TestCase):
    """The refusals, which belong here rather than in a renderer — see clean_config."""

    BASE = {"start": "2024-01-01", "end": "2024-06-01", "tickers": ["FRED:UNRATE"]}

    def clean(self, **kw):
        with _with_fred():
            return appmod.clean_config({**self.BASE, **kw})

    def test_a_line_chart_of_an_economic_series_is_fine(self):
        cfg = self.clean(chart="line")
        self.assertEqual(cfg["tickers"], ["FRED:UNRATE"])

    def test_a_candlestick_of_an_economic_series_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.clean(chart="candles")
        self.assertIn("open, high or low", str(caught.exception))

    def test_an_intraday_range_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.clean(chart="line", range="1d")
        self.assertIn("daily", str(caught.exception))

    def test_volatility_is_refused_because_the_annualising_would_be_wrong(self):
        with self.assertRaises(ValueError) as caught:
            self.clean(chart="bars", metric="volatility",
                       tickers=["FRED:UNRATE", "FRED:CPIAUCSL"])
        self.assertIn("annualised", str(caught.exception))

    def test_without_a_key_the_request_is_refused_with_the_way_to_fix_it(self):
        with mock.patch.object(config, "FRED_KEY", ""):
            with self.assertRaises(ValueError) as caught:
                appmod.clean_config({**self.BASE, "chart": "line"})
        self.assertIn("ROLLTAPE_FRED_KEY", str(caught.exception))

    def test_a_ticker_chart_is_untouched_by_any_of_this(self):
        cfg = self.clean(chart="candles", tickers=["NVDA"])
        self.assertEqual(cfg["chart"], "candles")


class UnitTests(unittest.TestCase):
    """Printing a value in its own units, and saying what a move in it means."""

    PERCENT = {"id": "UNRATE", "title": "Unemployment Rate", "units": "Percent",
               "units_short": "%", "frequency": "Monthly", "seasonal": "SA"}
    DOLLARS = {"id": "GDP", "title": "Gross Domestic Product",
               "units": "Billions of Dollars", "units_short": "Bil. of $",
               "frequency": "Quarterly", "seasonal": "SAAR"}
    INDEX = {"id": "CPIAUCSL", "title": "Consumer Price Index",
             "units": "Index 1982-1984=100", "units_short": "Index 1982-1984=100",
             "frequency": "Monthly", "seasonal": "SA"}

    def meta(self, meta):
        return mock.patch.object(data, "economic_meta", return_value=meta)

    def test_a_rate_is_printed_as_a_rate(self):
        with self.meta(self.PERCENT):
            self.assertEqual(renderers._value_text("FRED:UNRATE", 3.74), "3.7%")

    def test_a_dollar_level_keeps_its_magnitude(self):
        with self.meta(self.DOLLARS):
            self.assertEqual(renderers._value_text("FRED:GDP", 27000), "$27,000B")

    def test_an_unknown_unit_prints_a_plain_number_rather_than_a_dollar_sign(self):
        # The same path a failed metadata lookup lands on: never print a unit nobody said.
        with self.meta(self.INDEX):
            self.assertEqual(renderers._value_text("FRED:CPIAUCSL", 314.2), "314")

    def test_a_ticker_is_still_money(self):
        # The property, not the string: a ticker routes straight to _money, so its readout
        # keeps whatever precision rule _money has rather than gaining a second one here.
        for v in (4.25, 128.5, 1875.0):
            self.assertEqual(renderers._value_text("NVDA", v), renderers._money(v))

    def test_a_rate_reports_points_rather_than_percent(self):
        # 4.0 to 4.5 is half a point. Calling it +12.5% is the kind of number a video gets
        # corrected on in the comments.
        with self.meta(self.PERCENT):
            self.assertEqual(renderers._headline("FRED:UNRATE", 4.0, 4.5), "+0.50 pts")

    def test_an_index_reports_percent_because_that_is_what_it_means(self):
        with self.meta(self.INDEX):
            self.assertEqual(renderers._headline("FRED:CPIAUCSL", 300.0, 309.0), "+3.0%")

    def test_a_ticker_reports_percent(self):
        self.assertEqual(renderers._headline("NVDA", 100.0, 125.0), "+25.0%")

    def test_a_series_starting_at_zero_does_not_divide_by_it(self):
        with self.meta(self.INDEX):
            self.assertEqual(renderers._headline("FRED:X", 0.0, 2.0), "+2.0 pts")

    def test_the_axis_carries_the_unit(self):
        with self.meta(self.PERCENT):
            fmt = renderers._value_axis("FRED:UNRATE", 3.4, 4.2)
        self.assertEqual(fmt(3.5), "3.5%")

    def test_a_shared_unit_is_only_shared_when_the_symbols_agree(self):
        with self.meta(self.PERCENT):
            both = renderers._shared_unit(["FRED:UNRATE", "FRED:CIVPART"])
        self.assertIsNotNone(both)
        # A rate against a share price has no axis that can carry both.
        with mock.patch.object(data, "economic_meta", return_value=self.PERCENT):
            self.assertIsNone(renderers._shared_unit(["FRED:UNRATE", "NVDA"]))

    def test_two_tickers_share_a_unit_exactly_as_they_always_did(self):
        self.assertIsNotNone(renderers._shared_unit(["NVDA", "AMD"]))

    def test_an_observation_period_is_read_from_the_series(self):
        # Longest-key-first matters: "semiannual" ends in "annual", "biweekly" in "weekly".
        self.assertEqual(renderers._econ_period({"frequency": "Monthly"})[0], "month")
        self.assertEqual(renderers._econ_period({"frequency": "Quarterly"})[0], "quarter")
        self.assertEqual(renderers._econ_period({"frequency": "Semiannual"})[0],
                         "half-year")
        self.assertEqual(renderers._econ_period({"frequency": "Biweekly"})[0], "fortnight")

    def test_an_average_is_labelled_in_the_series_own_periods(self):
        with self.meta(self.PERCENT):
            self.assertEqual(renderers._ma_unit("FRED:UNRATE"), "month")
        self.assertEqual(renderers._ma_unit("NVDA"), "day")


class DrawingTests(unittest.TestCase):
    """One still per chart, from generated observations. No network, no encode."""

    BASE = {"start": "2020-01-01", "end": "2024-01-01", "duration": 1.0, "hold": 0.2}
    META = {"id": "UNRATE", "title": "Unemployment Rate", "units": "Percent",
            "units_short": "%", "frequency": "Monthly", "seasonal": "SA"}

    def setUp(self):
        testsupport.patch_fetch(self)
        patcher = mock.patch.object(data, "economic_meta", return_value=self.META)
        patcher.start()
        self.addCleanup(patcher.stop)
        keyed = _with_fred()
        keyed.start()
        self.addCleanup(keyed.stop)

    def cfg(self, **kw):
        return appmod.clean_config({**self.BASE, "chart": "line",
                                    "tickers": ["FRED:UNRATE"], **kw})

    def figure(self, **kw):
        """Draw a still and hand back the Figure, so its text can be read back."""
        cfg = self.cfg(**kw)
        ctx = renderers.make_ctx(cfg["theme"], cfg["aspect"], "draft")
        fig = renderers.CHARTS[cfg["chart"]]["fn"](cfg, ctx, None, still=0.9)
        self.addCleanup(renderers.plt.close, fig)
        return fig

    def texts(self, fig):
        return [t.get_text() for t in fig.texts]

    def test_a_line_chart_draws_and_is_titled_by_the_series(self):
        # "UNRATE" is not a title anybody reads out loud.
        self.assertIn("Unemployment Rate", self.texts(self.figure()))

    def test_the_subtitle_names_the_unit_and_moves_in_points(self):
        sub = " ".join(self.texts(self.figure()))
        self.assertIn("Percent, SA", sub)
        self.assertIn("pts", sub)
        self.assertNotIn("%   ·", sub)  # a rate's move is points, not percent

    def test_a_typed_title_still_wins(self):
        self.assertIn("Inflation is cooling",
                      self.texts(self.figure(title="Inflation is cooling")))

    def test_the_y_axis_is_in_percent_rather_than_dollars(self):
        fig = self.figure()
        labels = [t.get_text() for t in fig.axes[0].get_yticklabels()]
        self.assertTrue(any(lab.endswith("%") for lab in labels if lab), labels)
        self.assertFalse(any("$" in lab for lab in labels))

    def test_a_timeline_draws_with_a_callout(self):
        fig = self.figure(chart="timeline",
                          annotations=[{"date": "2022-06-01", "label": "Peak"}])
        self.assertIn("Peak", [t.get_text() for t in fig.axes[0].texts])

    def test_a_comparison_of_two_series_draws(self):
        fig = self.figure(chart="compare",
                          tickers=["FRED:UNRATE", "FRED:CIVPART"], normalize=False)
        self.assertTrue(fig.axes)

    def test_a_candlestick_is_refused_by_the_renderer_too(self):
        # clean_config catches it first; this is the same refusal one layer down, for a
        # renderer called directly.
        cfg = self.cfg()
        cfg["chart"] = "candles"
        ctx = renderers.make_ctx(cfg["theme"], cfg["aspect"], "draft")
        with self.assertRaises(ValueError) as caught:
            renderers.render_candles(cfg, ctx, None, still=0.5)
        self.assertIn("flat dashes", str(caught.exception))

    def test_the_footer_credits_fred(self):
        fig = self.figure()
        self.assertTrue(any("FRED" in t for t in self.texts(fig)),
                        self.texts(fig))

    def test_a_moving_average_is_labelled_in_months_not_days(self):
        fig = self.figure(ma=[12])
        labels = [t.get_text() for t in fig.axes[0].get_legend().get_texts()]
        self.assertIn("12-month MA", labels)

    def test_the_run_up_for_an_average_is_measured_in_the_series_periods(self):
        # A 12 on monthly data needs a year of lead, not twelve days of it — otherwise the
        # average only starts most of the way across the chart.
        seen = []
        real = data.fetch

        def spy(ticker, start, *a, **kw):
            seen.append(start)
            return real(ticker, start, *a, **kw)

        with mock.patch.object(data, "fetch", spy):
            self.figure(ma=[12])
        lead = pd.Timestamp(self.BASE["start"]) - pd.Timestamp(seen[0])
        self.assertGreater(lead.days, 300)


class SeriesEndpointTests(unittest.TestCase):
    """/api/series, which is the readout behind the ticker field."""

    def setUp(self):
        testsupport.patch_fetch(self)
        patcher = mock.patch.object(
            data, "economic_meta",
            return_value={"id": "UNRATE", "title": "Unemployment Rate",
                          "units": "Percent", "units_short": "%",
                          "frequency": "Monthly", "seasonal": "SA"})
        patcher.start()
        self.addCleanup(patcher.stop)
        keyed = _with_fred()
        keyed.start()
        self.addCleanup(keyed.stop)
        self.client = appmod.app.test_client()

    def test_an_economic_row_carries_its_title_and_unit(self):
        resp = self.client.post("/api/series", json={
            "chart": "line", "tickers": ["FRED:UNRATE"],
            "start": "2020-01-01", "end": "2024-01-01"})
        row = resp.get_json()["series"][0]
        self.assertEqual(row["name"], "Unemployment Rate")
        self.assertEqual(row["units"], "Percent")
        self.assertEqual(row["source"], "fred")

    def test_a_ticker_row_is_unchanged(self):
        resp = self.client.post("/api/series", json={
            "chart": "line", "tickers": ["NVDA"],
            "start": "2024-01-01", "end": "2024-06-01"})
        row = resp.get_json()["series"][0]
        self.assertNotIn("units", row)


class MetaEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = appmod.app.test_client()

    def test_the_namespace_and_the_built_in_list_are_published(self):
        with _with_fred():
            meta = self.client.get("/api/meta").get_json()["economic"]
        self.assertTrue(meta["enabled"])
        self.assertEqual(meta["prefix"], "FRED:")
        self.assertIn("FRED:CPIAUCSL", [s["symbol"] for s in meta["series"]])

    def test_without_a_key_the_interface_is_told_not_to_offer_it(self):
        with mock.patch.object(config, "FRED_KEY", ""):
            self.assertFalse(self.client.get("/api/meta").get_json()["economic"]["enabled"])


class CacheTests(unittest.TestCase):
    """An economic frame goes through the same disk cache every price frame does."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for target in (data, config):
            patcher = mock.patch.object(target, "CACHE_DIR", self.dir)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(data.reset_sources)

    def test_a_fetched_series_is_cached_under_fred_and_reused(self):
        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      _urlopen_returning(OBSERVATIONS_JSON)):
            data.fetch("FRED:UNRATE", "2024-01-01", "2024-06-01")
        written = os.listdir(self.dir)
        self.assertTrue(any(name.endswith(".fred.csv") for name in written), written)

        with _with_fred(), mock.patch("urllib.request.urlopen",
                                      side_effect=AssertionError("refetched")):
            df = data.fetch("FRED:UNRATE", "2024-01-01", "2024-06-01")
        self.assertEqual(data.source_for("FRED:UNRATE"), "fred")
        self.assertEqual(len(df), 4)


if __name__ == "__main__":
    unittest.main()
