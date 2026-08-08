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

    def test_demo_mode_never_reaches_the_network(self):
        data.set_demo(True)
        self.addCleanup(data.set_demo, False)

        with mock.patch.object(data, "_yahoo", side_effect=AssertionError("fetched")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("fetched")):
            df = data.fetch("AAPL", "2024-01-01", "2024-03-01")

        self.assertFalse(df.empty)
        self.assertEqual(data.sources_used(), {"demo"})


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
