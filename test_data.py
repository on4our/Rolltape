"""Tests for the Yahoo/Stooq fetch path, the date range presets and footer attribution.

No network: the Stooq endpoint is mocked with a recorded CSV sample, and the Yahoo path is
forced to fail to exercise the fallback. Run with: python -m unittest
"""

import io
import shutil
import tempfile
import unittest
from unittest import mock

import pandas as pd

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
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(data, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.cache, True)
        data.set_demo(False)
        data.reset_sources()
        self.addCleanup(data.reset_sources)
        # Two sessions of 5-minute bars, which is what "intraday" asks a source for.
        self.frame = data._synthetic("AAPL", "2024-01-11", "2024-01-12", "5m")

    def test_a_session_is_cut_into_bars_at_the_interval(self):
        idx = data._bar_index("2024-01-12", "2024-01-12", "5m")
        self.assertEqual(len(idx), 78)  # 09:30 to 16:00, exclusive of the close
        self.assertEqual(str(idx[0].time()), "09:30:00")
        self.assertEqual(str(idx[-1].time()), "15:55:00")

    def test_daily_bars_are_still_one_a_day(self):
        self.assertEqual(len(data._bar_index("2024-01-08", "2024-01-12", "1d")), 5)

    def test_intraday_keeps_only_the_most_recent_session(self):
        # The window asks for several days because which day the last session falls on
        # depends on weekends and holidays; the trim is what makes it one session.
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

    def test_intraday_bars_lose_the_timezone_and_keep_the_wall_clock(self):
        # Yahoo stamps intraday bars in the exchange's timezone. An axis labelled 09:30
        # should read as the opening bell wherever the render runs, and a naive index is
        # also what survives the round trip through the CSV cache.
        stamped = self.frame.tz_localize("America/New_York")
        yfinance = mock.Mock()
        yfinance.download.return_value = stamped

        with mock.patch.dict("sys.modules", {"yfinance": yfinance}):
            df = data._yahoo("AAPL", "2024-01-11", None, "5m")

        self.assertIsNone(df.index.tz)
        self.assertEqual(str(df.index[0].time()), "09:30:00")
        self.assertEqual(yfinance.download.call_args.kwargs["interval"], "5m")

    def test_stooq_refuses_intraday_rather_than_answering_with_daily_bars(self):
        # Stooq publishes daily bars only. Falling back to a different shape of data would
        # put an "intraday" label on a chart of five closes.
        with self.assertRaises(ValueError):
            data._stooq("AAPL", "2024-01-11", None, "5m")

    def test_a_failed_intraday_fetch_says_why_there_was_no_fallback(self):
        with mock.patch.object(data, "_yahoo", side_effect=RuntimeError("endpoint moved")), \
             mock.patch("urllib.request.urlopen", _urlopen_returning(STOOQ_CSV)):
            with self.assertRaises(ValueError) as caught:
                data.fetch("AAPL", "2024-01-11", None, "5m")

        self.assertIn("intraday", str(caught.exception))

    def test_intraday_and_daily_pulls_do_not_share_a_cache_entry(self):
        self.assertNotEqual(
            data._cache_path("AAPL", "2024-01-11", "2024-01-12", "yahoo", "1d"),
            data._cache_path("AAPL", "2024-01-11", "2024-01-12", "yahoo", "5m"))

    def test_an_open_ended_range_is_keyed_by_when_it_was_fetched(self):
        # Without this a year-to-date range cached this morning still ends this morning
        # next week. Intraday goes finer, because its last bar is still moving.
        today = pd.Timestamp.today().strftime("%Y-%m-%d")
        self.assertEqual(data._freshness(None, "1d"), today)
        self.assertTrue(data._freshness(None, "5m").startswith(today))
        self.assertNotEqual(data._freshness(None, "1d"), data._freshness(None, "5m"))
        self.assertEqual(data._freshness("2024-06-01", "1d"), "2024-06-01")

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

    def test_demo_wins_over_stooq(self):
        # Demo data reaching a published video is the worse mistake, so it takes priority.
        data._SOURCES["AAPL"] = "stooq"
        data._SOURCES["MSFT"] = "demo"
        self.assertEqual(renderers._footer_text(None), "Demo data")


if __name__ == "__main__":
    unittest.main()
