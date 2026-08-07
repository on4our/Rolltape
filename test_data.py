"""Tests for the Yahoo/Stooq fetch path and footer attribution.

No network: the Stooq endpoint is mocked with a recorded CSV sample, and the Yahoo path is
forced to fail to exercise the fallback. Run with: python -m unittest
"""

import io
import shutil
import tempfile
import unittest
from unittest import mock

import data
import renderers

# A trimmed but faithful sample of what stooq.com/q/d/l/?s=aapl.us&i=d returns.
STOOQ_CSV = """Date,Open,High,Low,Close,Volume
2024-01-02,187.15,188.44,183.89,185.64,82488700
2024-01-03,184.22,185.88,183.43,184.25,58414500
2024-01-04,182.15,183.09,180.88,181.91,71983600
2024-01-05,181.99,182.76,180.17,181.18,62303300
2024-01-08,182.09,185.60,181.50,185.56,59144500
"""


def _urlopen_returning(body):
    """Stand in for urllib.request.urlopen, which is used as a context manager."""
    resp = mock.MagicMock()
    resp.read.return_value = body.encode()
    resp.__enter__.return_value = resp
    return mock.Mock(return_value=resp)


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
        data.set_demo(False)
        data.reset_sources()
        self.addCleanup(data.reset_sources)

    def test_falls_back_to_stooq_when_yahoo_fails(self):
        with mock.patch.object(data, "_yahoo", side_effect=RuntimeError("endpoint moved")), \
             mock.patch("urllib.request.urlopen", _urlopen_returning(STOOQ_CSV)):
            df = data.fetch("AAPL", "2024-01-01")

        self.assertEqual(len(df), 5)
        self.assertEqual(data.sources_used(), {"stooq"})

    def test_yahoo_is_preferred_and_stays_silent(self):
        import pandas as pd
        frame = pd.read_csv(io.StringIO(STOOQ_CSV), parse_dates=["Date"], index_col="Date")

        with mock.patch.object(data, "_yahoo", return_value=frame) as yahoo, \
             mock.patch("urllib.request.urlopen") as urlopen:
            data.fetch("AAPL", "2024-01-01")

        yahoo.assert_called_once()
        urlopen.assert_not_called()  # Stooq is never touched when Yahoo answers
        self.assertEqual(data.sources_used(), {"yahoo"})
        self.assertIsNone(data.attribution())

    def test_stooq_carries_the_render_when_yfinance_is_absent(self):
        # The serverless build ships without yfinance to save ~45MB, so this path is
        # load-bearing there, not just a nicety.
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

    def test_demo_mode_never_reaches_the_network(self):
        data.set_demo(True)
        self.addCleanup(data.set_demo, False)

        with mock.patch.object(data, "_yahoo", side_effect=AssertionError("fetched")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("fetched")):
            df = data.fetch("AAPL", "2024-01-01", "2024-03-01")

        self.assertFalse(df.empty)
        self.assertEqual(data.sources_used(), {"demo"})


class IntervalTests(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(data, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.cache, True)
        data.set_demo(False)
        data.reset_sources()
        self.addCleanup(data.reset_sources)

    def test_interval_is_part_of_the_cache_key(self):
        # Without this the same ticker and dates at 5m would be served daily bars.
        paths = {iv: data._cache_path("AAPL", "2026-07-01", "2026-08-01", "yahoo", iv)
                 for iv in data.INTERVALS}
        self.assertEqual(len(set(paths.values())), len(paths))

    def test_stooq_declines_intraday_instead_of_failing_to_parse(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("should not be contacted")):
            with self.assertRaises(ValueError) as caught:
                data._stooq("AAPL", "2026-07-01", None, "5m")
        self.assertIn("daily", str(caught.exception))

    def test_intraday_has_no_fallback_and_the_error_says_so(self):
        with mock.patch.object(data, "_yahoo", side_effect=RuntimeError("endpoint moved")):
            with self.assertRaises(ValueError) as caught:
                data.fetch("AAPL", "2026-07-01", None, "5m")
        message = str(caught.exception)
        self.assertIn("endpoint moved", message)
        self.assertIn("daily", message)

    def test_yahoo_is_asked_for_the_requested_interval(self):
        import pandas as pd
        frame = pd.read_csv(io.StringIO(STOOQ_CSV), parse_dates=["Date"], index_col="Date")
        with mock.patch.object(data, "_yahoo", return_value=frame) as yahoo:
            data.fetch("AAPL", "2026-07-01", None, "15m")
        self.assertEqual(yahoo.call_args[0][3], "15m")

    def test_unknown_interval_is_refused(self):
        with self.assertRaises(ValueError):
            data.fetch("AAPL", "2026-07-01", None, "3s")


class SyntheticIntradayTests(unittest.TestCase):
    def test_sessions_hold_the_expected_number_of_bars(self):
        for interval, per_session in (("5m", 78), ("15m", 26), ("1h", 7)):
            idx = data._session_index("2026-08-03", "2026-08-05", interval)
            self.assertEqual(len(idx), per_session * 3, interval)

    def test_bars_stay_inside_regular_market_hours(self):
        idx = data._session_index("2026-08-03", "2026-08-03", "5m")
        self.assertEqual(idx[0].strftime("%H:%M"), "09:30")
        self.assertEqual(idx[-1].strftime("%H:%M"), "15:55")

    def test_overnight_gap_is_real_so_the_axis_fix_gets_exercised(self):
        import pandas as pd
        idx = data._session_index("2026-08-03", "2026-08-04", "5m")
        overnight = idx[78] - idx[77]
        self.assertGreater(overnight, pd.Timedelta(hours=12))

    def test_demo_intraday_needs_no_network(self):
        data.set_demo(True)
        self.addCleanup(data.set_demo, False)
        with mock.patch.object(data, "_yahoo", side_effect=AssertionError("fetched")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("fetched")):
            df = data.fetch("AAPL", "2026-08-03", "2026-08-05", "5m")
        self.assertEqual(len(df), 78 * 3)
        self.assertEqual(list(df.columns), data.COLUMNS)

    def test_intraday_bars_move_less_than_daily_ones(self):
        # A five-minute bar that swings like a daily one would make demo previews
        # useless for judging intraday motion.
        import numpy as np
        daily = data._synthetic("AAPL", "2026-05-01", "2026-08-05", "1d")
        fine = data._synthetic("AAPL", "2026-05-01", "2026-08-05", "5m")
        self.assertLess(np.diff(np.log(fine["Close"])).std(),
                        np.diff(np.log(daily["Close"])).std())


class TimeAxisTests(unittest.TestCase):
    def test_intraday_collapses_the_overnight_gap(self):
        import numpy as np
        idx = data._session_index("2026-08-03", "2026-08-05", "5m")
        axis = renderers._time_axis(idx, "5m")
        self.assertTrue(axis.positional)
        # Evenly spaced positions are what stop 17 closed hours eating the chart width.
        self.assertTrue(np.all(np.diff(axis.x) == 1))

    def test_daily_stays_on_a_real_date_axis(self):
        import pandas as pd
        idx = pd.bdate_range("2024-01-01", "2025-06-01")
        axis = renderers._time_axis(idx, "1d")
        self.assertFalse(axis.positional)
        self.assertEqual(axis.fmt, "%b %Y")

    def test_label_format_follows_the_span(self):
        import pandas as pd
        cases = [
            (pd.bdate_range("2024-01-01", "2025-06-01"), "%b %Y"),
            (pd.bdate_range("2024-01-01", "2024-03-01"), "%d %b"),
            (data._session_index("2026-08-03", "2026-08-05", "5m"), "%d %b %H:%M"),
            (data._session_index("2026-08-03", "2026-08-03", "5m"), "%H:%M"),
        ]
        for index, expected in cases:
            self.assertEqual(renderers._time_format(index), expected)

    def test_a_callout_date_snaps_to_the_nearest_bar(self):
        idx = data._session_index("2026-08-03", "2026-08-05", "5m")
        axis = renderers._time_axis(idx, "5m")
        self.assertEqual(axis.position("2026-08-04"), 78.0)  # first bar of day two
        self.assertEqual(axis.stamp(78.0).strftime("%d %H:%M"), "04 09:30")

    def test_a_callout_outside_the_series_is_dropped(self):
        idx = data._session_index("2026-08-03", "2026-08-05", "5m")
        axis = renderers._time_axis(idx, "5m")
        self.assertIsNone(axis.position("2026-01-01"))
        self.assertIsNone(axis.position("2027-01-01"))


class VolatilityScalingTests(unittest.TestCase):
    def setUp(self):
        data.set_demo(True)
        self.addCleanup(data.set_demo, False)

    def test_annualised_volatility_agrees_across_intervals(self):
        # The metric annualises by bars per year. Held at 252 it would report a 5m
        # series at roughly a ninth of its real volatility.
        cfg = dict(chart="bars", tickers=["AAPL"], start="2026-06-10", end="2026-08-06",
                   metric="volatility", rows=[], unit="", decimals=1)
        by_interval = {}
        for interval in ("1d", "1h", "15m", "5m"):
            (_, value), = renderers._bar_rows(dict(cfg, interval=interval))[0]
            by_interval[interval] = value
        daily = by_interval["1d"]
        for interval, value in by_interval.items():
            self.assertLess(abs(value - daily), daily * 0.5, interval)


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

    def test_demo_wins_over_stooq(self):
        # Demo data reaching a published video is the worse mistake, so it takes priority.
        data._SOURCES["AAPL"] = "stooq"
        data._SOURCES["MSFT"] = "demo"
        self.assertEqual(renderers._footer_text(None), "Demo data")


if __name__ == "__main__":
    unittest.main()
