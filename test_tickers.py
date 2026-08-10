"""Tests for the symbol lookup and for /api/series, the two halves of the ticker field.

No network: Yahoo's search endpoint is mocked with a recorded JSON sample, and every test
that fetches prices runs on the demo generator. Nothing here encodes anything — the series
endpoint reads a frame and never draws, which is the whole reason it can answer on a
keystroke. Run with: python -m unittest
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import app
import data

HERE = os.path.dirname(os.path.abspath(__file__))

# A trimmed but faithful sample of query2.finance.yahoo.com/v1/finance/search?q=nvda. The
# last two entries are the ones that matter: search answers with private companies and
# research rows that have no price series behind them, and they have to be dropped.
YAHOO_SEARCH = json.dumps({
    "count": 4,
    "quotes": [
        {"exchange": "NMS", "shortname": "NVIDIA Corporation", "quoteType": "EQUITY",
         "symbol": "NVDA", "typeDisp": "Equity", "longname": "NVIDIA Corporation",
         "exchDisp": "NASDAQ", "isYahooFinance": True},
        {"exchange": "NGM", "shortname": "GraniteShares 2x Long NVDA Daily",
         "quoteType": "ETF", "symbol": "NVDL", "typeDisp": "ETF",
         "exchDisp": "NASDAQ", "isYahooFinance": True},
        {"index": "78ff5b7e", "name": "Nvidia Ltd", "permalink": "nvidia-ltd",
         "isYahooFinance": False},
        {"symbol": "NVDA.PRIVATE", "shortname": "Nvidia Research Note",
         "isYahooFinance": False},
    ],
    "news": [],
})


def _urlopen_returning(body):
    """Stand in for urllib.request.urlopen, which is used as a context manager."""
    resp = mock.MagicMock()
    resp.read.return_value = body.encode()
    resp.__enter__.return_value = resp
    return mock.Mock(return_value=resp)


class LocalSymbolTests(unittest.TestCase):
    """The built-in list, which is what answers offline and on the first keystroke."""

    def setUp(self):
        data.set_demo(True)  # the shortest way to keep Yahoo out of these entirely
        self.addCleanup(data.set_demo, False)

    def test_a_symbol_prefix_matches(self):
        symbols = [h["symbol"] for h in data.search("NVD")]
        self.assertIn("NVDA", symbols)

    def test_a_company_name_matches_case_insensitively(self):
        symbols = [h["symbol"] for h in data.search("micron")]
        self.assertIn("MU", symbols)

    def test_the_exact_symbol_comes_first(self):
        # "V" is also a prefix of VOO and VTI and a substring of NVDA. Someone who typed
        # the whole symbol has already told you which one they meant.
        self.assertEqual(data.search("v")[0]["symbol"], "V")
        self.assertEqual(data.search("t")[0]["symbol"], "T")

    def test_a_symbol_prefix_outranks_a_name_only_match(self):
        # AMD's symbol starts with the query; Lam Research only matches inside its name.
        # The symbol column is the one being typed into, so it wins.
        ranked = [h["symbol"] for h in data.search("AM", limit=20)]
        self.assertLess(ranked.index("AMD"), ranked.index("LRCX"))

    def test_nothing_matching_is_an_empty_list_not_an_error(self):
        self.assertEqual(data.search("ZZQQXX"), [])

    def test_an_empty_query_asks_nothing(self):
        for query in ("", "   ", None):
            with self.subTest(query=query):
                self.assertEqual(data.search(query), [])

    def test_the_limit_is_honoured(self):
        self.assertEqual(len(data.search("A", limit=3)), 3)

    def test_every_row_carries_the_shared_shape(self):
        for hit in data.search("A", limit=5):
            self.assertEqual(set(hit), {"symbol", "name", "type", "exchange"})

    def test_indices_and_crypto_keep_yahoos_own_symbol_shapes(self):
        # ^GSPC and BTC-USD are not tickers anyone guesses at, which is exactly why they
        # belong in a suggestion list — nobody types a caret on purpose.
        self.assertIn("^GSPC", [h["symbol"] for h in data.search("S&P 500", limit=20)])
        self.assertIn("BTC-USD", [h["symbol"] for h in data.search("bitcoin")])

    def test_demo_mode_never_reaches_the_network(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("searched")):
            self.assertTrue(data.search("NVDA"))


class YahooSearchTests(unittest.TestCase):
    """Yahoo finds everything the built-in list doesn't, and is allowed to fail."""

    def setUp(self):
        data.set_demo(False)
        data.clear_search_cache()
        self.addCleanup(data.clear_search_cache)

    def test_parses_to_the_shared_shape(self):
        with mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_SEARCH)):
            hits = data._yahoo_search("nvda", 8)

        self.assertEqual([h["symbol"] for h in hits], ["NVDA", "NVDL"])
        self.assertEqual(hits[0]["name"], "NVIDIA Corporation")
        self.assertEqual(hits[0]["exchange"], "NASDAQ")
        self.assertEqual(hits[1]["type"], "etf")

    def test_rows_with_no_price_series_are_dropped(self):
        # A suggestion that can't be charted is worse than one suggestion fewer.
        with mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_SEARCH)):
            hits = data._yahoo_search("nvda", 8)

        self.assertNotIn("NVDA.PRIVATE", [h["symbol"] for h in hits])

    def test_the_request_carries_a_user_agent(self):
        # Yahoo answers a bare urllib request with a 429, so this is load-bearing rather
        # than politeness.
        urlopen = _urlopen_returning(YAHOO_SEARCH)
        with mock.patch("urllib.request.urlopen", urlopen):
            data._yahoo_search("nvda", 8)

        request = urlopen.call_args[0][0]
        self.assertIn("Mozilla", request.get_header("User-agent"))
        self.assertIn("q=nvda", request.full_url)

    def test_a_symbol_in_both_appears_once_and_gains_the_exchange(self):
        with mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_SEARCH)):
            hits = data.search("NVDA")

        rows = [h for h in hits if h["symbol"] == "NVDA"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["exchange"], "NASDAQ")  # only Yahoo knows this

    def test_yahoo_widens_the_built_in_list_rather_than_replacing_it(self):
        with mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_SEARCH)):
            symbols = [h["symbol"] for h in data.search("NVDA")]

        self.assertEqual(symbols[0], "NVDA")   # the built-in exact match still leads
        self.assertIn("NVDL", symbols)         # and Yahoo's find is carried along

    def test_a_dead_lookup_leaves_the_built_in_results_standing(self):
        # The field is being typed into. A search outage should narrow the suggestions,
        # never raise into the request handler.
        with mock.patch("urllib.request.urlopen", side_effect=OSError("unreachable")):
            symbols = [h["symbol"] for h in data.search("NVDA")]

        self.assertEqual(symbols, ["NVDA"])

    def test_a_repeated_query_is_answered_from_the_cache(self):
        urlopen = _urlopen_returning(YAHOO_SEARCH)
        with mock.patch("urllib.request.urlopen", urlopen):
            data.search("NVDA")
            data.search("nvda")  # same question, backspaced and retyped

        urlopen.assert_called_once()

    def test_a_failed_lookup_is_not_cached(self):
        # Caching the failure would keep the field degraded long after Yahoo came back.
        with mock.patch("urllib.request.urlopen", side_effect=OSError("unreachable")):
            data.search("NVDA")
        with mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_SEARCH)):
            self.assertIn("NVDL", [h["symbol"] for h in data.search("NVDA")])

    def test_the_cache_stays_bounded(self):
        with mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_SEARCH)):
            for n in range(data.SEARCH_CACHE_SIZE + 20):
                data.search(f"Q{n}")

        self.assertLessEqual(len(data._SEARCH_CACHE), data.SEARCH_CACHE_SIZE)

    def test_search_works_without_yfinance(self):
        # It is a plain HTTP call, not an SDK one — which matters because Stooq can still
        # draw a daily chart for whatever the lookup finds.
        with mock.patch.dict("sys.modules", {"yfinance": None}), \
             mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_SEARCH)):
            self.assertIn("NVDL", [h["symbol"] for h in data.search("NVDA")])


class SearchEndpointTests(unittest.TestCase):
    """/api/search — a typeahead, so it answers 200 to anything including nonsense."""

    def setUp(self):
        self.client = app.app.test_client()
        data.set_demo(True)
        self.addCleanup(data.set_demo, False)

    def test_the_shape_the_field_reads(self):
        body = self.client.get("/api/search?q=NVD").get_json()
        self.assertEqual(body["query"], "NVD")
        self.assertEqual(body["results"][0]["symbol"], "NVDA")

    def test_junk_is_an_empty_list_not_an_error(self):
        # A red field halfway through typing a symbol is worse than no suggestions.
        for query in ("", "   ", "ZZQQXX", "<script>"):
            with self.subTest(query=query):
                resp = self.client.get("/api/search", query_string={"q": query})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.get_json()["results"], [])

    def test_the_limit_is_bounded_rather_than_refused(self):
        for asked, most in ((3, 3), (0, app.MAX_SEARCH_LIMIT),
                            (999, app.MAX_SEARCH_LIMIT), ("junk", app.SEARCH_LIMIT)):
            with self.subTest(limit=asked):
                body = self.client.get(f"/api/search?q=A&limit={asked}").get_json()
                self.assertLessEqual(len(body["results"]), most)

    def test_a_missing_query_is_still_a_200(self):
        resp = self.client.get("/api/search")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["results"], [])


class SeriesEndpointTests(unittest.TestCase):
    """/api/series — the numbers a chart would be drawn from, without drawing it."""

    def setUp(self):
        self.client = app.app.test_client()
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(data, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.cache, True)
        data.set_demo(True)
        self.addCleanup(data.set_demo, False)
        data.reset_sources()
        self.addCleanup(data.reset_sources)

    def _post(self, query="", **over):
        body = {"chart": "line", "tickers": ["AAPL"], "range": "1y"}
        body.update(over)
        return self.client.post(f"/api/series{query}", json=body)

    def test_the_window_comes_back_alongside_the_data(self):
        body = self._post().get_json()
        self.assertEqual(body["range"], "1y")
        self.assertEqual(body["interval"], "1d")
        self.assertEqual([s["symbol"] for s in body["series"]], ["AAPL"])

    def test_a_row_carries_the_headline_and_the_source(self):
        row = self._post().get_json()["series"][0]
        self.assertGreater(row["bars"], 200)
        self.assertEqual(row["source"], "demo")
        self.assertAlmostEqual(row["change"], row["last"] - row["first"], places=3)
        self.assertAlmostEqual(row["change_pct"],
                               (row["last"] / row["first"] - 1) * 100, places=1)
        self.assertGreaterEqual(row["high"], row["last"])
        self.assertLessEqual(row["low"], row["last"])

    def test_the_summary_is_computed_before_the_thinning(self):
        # The point cap decides how many closes come back, never what the numbers say —
        # a headline that moved with the sparkline resolution would be a quiet wrong answer.
        full = self._post("?points=2000").get_json()["series"][0]
        thin = self._post("?points=2").get_json()["series"][0]

        self.assertGreater(len(full["points"]), len(thin["points"]))
        for field in ("bars", "first", "last", "change", "change_pct", "high", "low"):
            self.assertEqual(full[field], thin[field], field)

    def test_thinning_stays_inside_the_cap_and_keeps_the_final_bar(self):
        for cap in (2, 10, 50):
            with self.subTest(cap=cap):
                row = self._post(f"?points={cap}").get_json()["series"][0]
                # One over the cap at most: the last bar is kept whatever the stride does,
                # because it is the close the headline quotes.
                self.assertLessEqual(len(row["points"]), cap + 1)
                self.assertEqual(row["points"][0][1], row["first"])
                self.assertEqual(row["points"][-1][1], row["last"])
                self.assertEqual(row["points"][-1][0], row["to"])

    def test_a_bad_point_count_falls_back_rather_than_failing(self):
        row = self._post("?points=nonsense").get_json()["series"][0]
        self.assertLessEqual(len(row["points"]), app.SERIES_POINTS + 1)

    def test_one_bad_ticker_does_not_blank_the_others(self):
        # Six symbols on a comparison chart, one typo. The other five still resolved.
        real = data.fetch

        def flaky(ticker, *a, **kw):
            if ticker == "ZZQQ":
                raise ValueError("No data for ZZQQ.")
            return real(ticker, *a, **kw)

        with mock.patch.object(data, "fetch", flaky):
            rows = self._post(chart="compare",
                              tickers=["AAPL", "ZZQQ", "MSFT"]).get_json()["series"]

        self.assertEqual([r["symbol"] for r in rows], ["AAPL", "ZZQQ", "MSFT"])
        self.assertEqual(rows[1]["error"], "No data for ZZQQ.")
        self.assertNotIn("error", rows[0])
        self.assertNotIn("error", rows[2])

    def test_it_resolves_the_same_window_the_render_will(self):
        # Same body as /api/preview, so a preset can't mean one thing here and another at
        # render time. YTD starts in January whatever the posted start date says.
        body = self._post(range="ytd", start="2019-05-02").get_json()
        self.assertEqual(body["start"][5:], "01-01")
        self.assertEqual(body["series"][0]["from"][:4], body["start"][:4])

    def test_the_ticker_limit_is_the_charts_own(self):
        # Line takes one symbol, so the extras are dropped here exactly as they would be
        # on the way into a render.
        body = self._post(tickers=["AAPL", "MSFT", "NVDA"]).get_json()
        self.assertEqual([s["symbol"] for s in body["series"]], ["AAPL"])

    def test_a_rejected_config_is_a_400_with_a_message(self):
        for bad in ({"tickers": []}, {"chart": "nope"}, {"range": "custom",
                                                         "start": "not-a-date"}):
            with self.subTest(bad=bad):
                resp = self._post(**bad)
                self.assertEqual(resp.status_code, 400)
                self.assertIn("error", resp.get_json())

    def test_it_never_draws(self):
        # The whole reason this can answer on a keystroke is that it reads a frame and
        # stops. A figure here would put it behind DRAW_LOCK and every in-flight render.
        with mock.patch.object(app.renderers, "save_still",
                               side_effect=AssertionError("drew a frame")):
            self.assertEqual(self._post().status_code, 200)


class TickerFieldTests(unittest.TestCase):
    """The markup side of the field, so a rename can't leave one half pointing at nothing."""

    def _index(self):
        with open(os.path.join(HERE, "templates", "index.html"), encoding="utf-8") as fh:
            return fh.read()

    def test_the_field_is_wired_to_the_list_it_fills(self):
        page = self._index()
        self.assertIn('aria-controls="tickerList"', page)
        self.assertIn('id="tickerList"', page)
        self.assertIn('role="combobox"', page)

    def test_both_endpoints_are_the_ones_the_app_serves(self):
        page = self._index()
        served = {str(rule) for rule in app.app.url_map.iter_rules()}
        self.assertIn("/api/search", page)
        self.assertIn("/api/series", page)
        self.assertTrue({"/api/search", "/api/series"} <= served)


if __name__ == "__main__":
    unittest.main()
