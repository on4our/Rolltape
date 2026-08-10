"""Tests for the income statement fetch and the waterfall bridges built on it.

No network: both endpoints are mocked with recorded response samples, and the API key is
patched in per test rather than read from the environment so the suite answers the same on
a machine that has one configured and on one that doesn't. The cache directory is a
temporary one per test, because a statement request is always open-ended and would
otherwise write into the developer's `.cache/`.

The bridge tests are the ones that matter. A waterfall's entire claim is that its bars land
on the total it names, so what is checked here is arithmetic — that every delta is a
difference between two reported subtotals, that a missing line drops a stage instead of
being guessed at, and that the split expense bars add to exactly the step the subtotals
demand. What none of these check is whether the numbers are the ones a filing contains;
that is the feed's business, and the fixtures are shaped after real responses rather than
fetched from one.
Run with: python -m unittest
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import pandas as pd

import config
import data
import fundamentals
import testsupport

# A trimmed but faithful sample of FMP's /stable/income-statement, newest first — which is
# the order they arrive in and the opposite of the one the bridges read.
FMP_INCOME = json.dumps([
    {"date": "2025-01-26", "symbol": "NVDA", "reportedCurrency": "USD",
     "fiscalYear": 2025, "period": "FY", "revenue": 130497000000,
     "costOfRevenue": 32639000000, "grossProfit": 97858000000,
     "researchAndDevelopmentExpenses": 12914000000,
     "sellingGeneralAndAdministrativeExpenses": 3491000000,
     "operatingExpenses": 16405000000, "operatingIncome": 81453000000,
     "netIncome": 72880000000},
    {"date": "2024-01-28", "symbol": "NVDA", "reportedCurrency": "USD",
     "fiscalYear": 2024, "period": "FY", "revenue": 60922000000,
     "costOfRevenue": 16621000000, "grossProfit": 44301000000,
     "researchAndDevelopmentExpenses": 8675000000,
     "sellingGeneralAndAdministrativeExpenses": 2654000000,
     "operatingExpenses": 11329000000, "operatingIncome": 32972000000,
     "netIncome": 29760000000},
])


def _yahoo_group(prefix, name, rows):
    """One type group in a fundamentals-timeseries response."""
    key = prefix + name
    return {"meta": {"symbol": "NVDA", "type": [key]},
            "timestamp": [0] * len(rows),
            key: [{"dataId": 1, "asOfDate": date, "periodType": "12M",
                   "currencyCode": "USD",
                   "reportedValue": {"raw": value, "fmt": str(value)}}
                  for date, value in rows]}


# The same two years off Yahoo's endpoint. Note there is no fiscal label anywhere in it —
# which is the whole reason `_yahoo_label` has to derive one from the period end date.
YAHOO_INCOME = json.dumps({"timeseries": {"result": [
    _yahoo_group("annual", "TotalRevenue",
                 [("2024-01-28", 60922000000), ("2025-01-26", 130497000000)]),
    _yahoo_group("annual", "CostOfRevenue",
                 [("2024-01-28", 16621000000), ("2025-01-26", 32639000000)]),
    _yahoo_group("annual", "GrossProfit",
                 [("2024-01-28", 44301000000), ("2025-01-26", 97858000000)]),
    _yahoo_group("annual", "ResearchAndDevelopment",
                 [("2024-01-28", 8675000000), ("2025-01-26", 12914000000)]),
    _yahoo_group("annual", "SellingGeneralAndAdministration",
                 [("2024-01-28", 2654000000), ("2025-01-26", 3491000000)]),
    _yahoo_group("annual", "OperatingIncome",
                 [("2024-01-28", 32972000000), ("2025-01-26", 81453000000)]),
    _yahoo_group("annual", "NetIncome",
                 [("2024-01-28", 29760000000), ("2025-01-26", 72880000000)]),
], "error": None}})


def _urlopen_returning(body):
    """Stand in for urllib.request.urlopen, which is used as a context manager."""
    resp = mock.MagicMock()
    resp.read.return_value = body.encode()
    resp.__enter__.return_value = resp
    return mock.Mock(return_value=resp)


class CacheCase(unittest.TestCase):
    """Gives each test its own cache directory, since every request here is open-ended."""

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cache, ignore_errors=True)
        patcher = mock.patch.object(fundamentals, "CACHE_DIR", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(data.reset_sources)


def _with_fmp(key="test-key", years=30):
    return mock.patch.multiple(config, FMP_KEY=key, FMP_HISTORY_YEARS=years)


class SourceOrderTests(unittest.TestCase):
    """The same three ways to be dropped that data.py uses, applied to statements."""

    def test_fmp_leads_when_it_has_a_key(self):
        with _with_fmp():
            self.assertEqual(fundamentals._sources_for("annual", 1)[0], "fmp")

    def test_without_a_key_only_yahoo_is_left(self):
        with mock.patch.object(config, "FMP_KEY", ""):
            self.assertEqual(fundamentals._sources_for("annual", 1), ("yahoo",))

    def test_licensed_only_drops_the_scraped_source(self):
        with _with_fmp(), mock.patch.object(config, "LICENSED_ONLY", True):
            self.assertEqual(fundamentals._sources_for("annual", 1), ("fmp",))

    def test_licensed_only_without_a_key_leaves_nothing(self):
        with mock.patch.multiple(config, FMP_KEY="", LICENSED_ONLY=True):
            self.assertEqual(fundamentals._sources_for("annual", 1), ())

    def test_a_run_past_the_plan_horizon_drops_the_licensed_feed(self):
        # Ten annual statements reach back eleven years, which a five-year plan answers
        # with five of them under a ten-year label — the silent truncation data.py drops
        # sources for, arriving here through the same check.
        with _with_fmp(years=5):
            self.assertIn("fmp", fundamentals._sources_for("annual", 2))
            self.assertNotIn("fmp", fundamentals._sources_for("annual", 10))

    def test_quarterly_reaches_back_a_quarter_of_as_far(self):
        # Ten quarters is two and a half years, well inside a plan that ten years is not.
        with _with_fmp(years=5):
            self.assertIn("fmp", fundamentals._sources_for("quarterly", 10))

    def test_nothing_eligible_says_which_problem_it_was(self):
        with mock.patch.multiple(config, FMP_KEY="k", LICENSED_ONLY=True,
                                 FMP_HISTORY_YEARS=5):
            message = fundamentals._nothing_eligible("NVDA", "annual", 10)
        self.assertIn("reaches back 5 years", message)
        self.assertIn("ROLLTAPE_FMP_HISTORY_YEARS", message)


class FMPParsingTests(CacheCase):
    def test_parses_to_the_shared_column_contract(self):
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_INCOME)):
            df = fundamentals.fetch("NVDA", "annual", 1)
        for line in fundamentals.LINES:
            self.assertIn(line, df.columns)
        self.assertEqual(df["Revenue"].iloc[-1], 130497000000)
        self.assertEqual(df["Currency"].iloc[-1], "USD")

    def test_statements_come_back_oldest_first(self):
        # They arrive newest first and every bridge reads them the other way round.
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_INCOME)):
            df = fundamentals.fetch("NVDA", "annual", 1)
        self.assertTrue(df.index.is_monotonic_increasing)

    def test_the_filers_own_period_label_is_kept(self):
        # A January year end is exactly the case no arithmetic on the end date reproduces,
        # so the reported fiscal year is preferred wherever there is one.
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_INCOME)):
            df = fundamentals.fetch("NVDA", "annual", 1)
        self.assertEqual(df["Label"].iloc[-1], "FY2025")

    def test_it_asks_the_income_statement_endpoint_for_one_more_than_wanted(self):
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_INCOME)) as urlopen:
            fundamentals.fetch("NVDA", "annual", 5)
        url = urlopen.call_args[0][0]
        self.assertIn("/income-statement?", url)
        self.assertIn("period=annual", url)
        self.assertIn("limit=6", url)  # the growth bridge opens on the earliest statement

    def test_no_key_never_reaches_the_endpoint(self):
        with mock.patch.multiple(config, FMP_KEY="", LICENSED_ONLY=True), \
             mock.patch("urllib.request.urlopen",
                        _urlopen_returning(FMP_INCOME)) as urlopen:
            with self.assertRaises(ValueError):
                fundamentals.fetch("NVDA", "annual", 1)
        urlopen.assert_not_called()

    def test_an_error_body_arrives_with_a_200(self):
        body = json.dumps({"Error Message": "Invalid API KEY."})
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(body)), \
             mock.patch.object(config, "LICENSED_ONLY", True):
            with self.assertRaises(ValueError) as caught:
                fundamentals.fetch("NVDA", "annual", 1)
        self.assertIn("Invalid API KEY", str(caught.exception))


class YahooParsingTests(CacheCase):
    def setUp(self):
        super().setUp()
        # No key, so Yahoo is the only source and nothing has to force FMP to fail.
        patcher = mock.patch.multiple(config, FMP_KEY="", LICENSED_ONLY=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parses_to_the_shared_column_contract(self):
        with mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_INCOME)):
            df = fundamentals.fetch("NVDA", "annual", 1)
        self.assertEqual(df["Revenue"].iloc[-1], 130497000000)
        self.assertEqual(df["NetIncome"].iloc[-1], 72880000000)
        self.assertEqual(len(df), 2)

    def test_groups_are_assembled_by_period_end(self):
        # Each line item arrives in its own group, so a period is only whole once they have
        # been merged on the date — a parser reading them row by row would produce two
        # statements per year with half the lines missing from each.
        with mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_INCOME)):
            df = fundamentals.fetch("NVDA", "annual", 1)
        latest = df.iloc[-1]
        self.assertEqual(latest["GrossProfit"], 97858000000)
        self.assertEqual(latest["ResearchDevelopment"], 12914000000)

    def test_the_period_label_is_derived_from_the_end_date(self):
        with mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_INCOME)):
            df = fundamentals.fetch("NVDA", "annual", 1)
        self.assertEqual(df["Label"].iloc[-1], "FY2025")

    def test_it_asks_for_the_period_prefixed_types(self):
        with mock.patch("urllib.request.urlopen",
                        _urlopen_returning(YAHOO_INCOME)) as urlopen:
            fundamentals.fetch("NVDA", "annual", 1)
        url = urlopen.call_args[0][0].full_url
        self.assertIn("annualTotalRevenue", url)
        self.assertIn("annualNetIncome", url)

    def test_an_empty_response_is_an_error_rather_than_an_empty_chart(self):
        body = json.dumps({"timeseries": {"result": [], "error": None}})
        with mock.patch("urllib.request.urlopen", _urlopen_returning(body)):
            with self.assertRaises(ValueError):
                fundamentals.fetch("NVDA", "annual", 1)


class FallbackTests(CacheCase):
    def test_yahoo_answers_when_the_licensed_feed_fails(self):
        def urlopen(req, *a, **kw):
            url = req if isinstance(req, str) else req.full_url
            if "financialmodelingprep" in url:
                raise OSError("down")
            resp = mock.MagicMock()
            resp.read.return_value = YAHOO_INCOME.encode()
            resp.__enter__.return_value = resp
            return resp

        with _with_fmp(), mock.patch("urllib.request.urlopen", urlopen):
            df = fundamentals.fetch("NVDA", "annual", 1)
        self.assertEqual(df["Revenue"].iloc[-1], 130497000000)
        self.assertEqual(data.source_for("NVDA"), "yahoo")

    def test_the_footer_names_the_licensed_feed_that_answered(self):
        # fundamentals.py records through the same one writer data.fetch uses, so a
        # waterfall drawn off FMP credits it without the footer learning a second path.
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_INCOME)):
            fundamentals.fetch("NVDA", "annual", 1)
        self.assertEqual(data.attribution(), "Data: Financial Modeling Prep")

    def test_yahoo_stays_silent_in_the_footer(self):
        with mock.patch.object(config, "FMP_KEY", ""), \
             mock.patch("urllib.request.urlopen", _urlopen_returning(YAHOO_INCOME)):
            fundamentals.fetch("NVDA", "annual", 1)
        self.assertIsNone(data.attribution())


class CacheTests(CacheCase):
    def test_a_second_fetch_is_served_from_disk(self):
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_INCOME)) as urlopen:
            first = fundamentals.fetch("NVDA", "annual", 1)
            second = fundamentals.fetch("NVDA", "annual", 1)
        self.assertEqual(urlopen.call_count, 1)
        pd.testing.assert_frame_equal(first, second)

    def test_the_cache_entry_is_stamped_with_the_day(self):
        # A statement request is inherently open-ended — it asks for the most recent N
        # periods, and which periods those are changes the morning a company reports.
        with _with_fmp(), mock.patch("urllib.request.urlopen",
                                     _urlopen_returning(FMP_INCOME)):
            fundamentals.fetch("NVDA", "annual", 1)
        written = os.listdir(self.cache)
        self.assertEqual(len(written), 1)
        self.assertIn(str(pd.Timestamp.now().normalize().date()), written[0])


class FrameTests(unittest.TestCase):
    def test_a_statement_with_no_revenue_is_refused(self):
        with self.assertRaises(ValueError):
            fundamentals._frame([{"End": pd.Timestamp("2025-01-26"),
                                  "NetIncome": 1.0}])

    def test_missing_lines_arrive_as_columns_of_nan(self):
        df = fundamentals._frame([{"End": pd.Timestamp("2025-01-26"),
                                   "Revenue": 10.0, "NetIncome": 2.0}])
        self.assertIn("GrossProfit", df.columns)
        self.assertTrue(pd.isna(df["GrossProfit"].iloc[0]))


class IncomeBridgeTests(unittest.TestCase):
    """The arithmetic. Every one of these is about the bridge closing on its own total."""

    def frame(self, **over):
        row = {"End": pd.Timestamp("2025-01-26"), "Label": "FY2025", "Currency": "USD",
               "Revenue": 130497.0, "CostOfRevenue": 32639.0, "GrossProfit": 97858.0,
               "ResearchDevelopment": 12914.0, "SellingGeneralAdministrative": 3491.0,
               "OperatingExpenses": 16405.0, "OperatingIncome": 81453.0,
               "NetIncome": 72880.0}
        row.update(over)
        return fundamentals._frame([row])

    def levels(self, rows):
        """Replays the bridge the way the renderer plans it."""
        level, tops = 0.0, []
        for row in rows:
            level = level + row["value"] if row["kind"] == "delta" else row["value"]
            tops.append(level)
        return tops

    def test_the_last_bar_lands_on_net_income(self):
        rows, _ = fundamentals.income_bridge(self.frame())
        self.assertAlmostEqual(self.levels(rows)[-1], 72880.0, places=6)

    def test_every_subtotal_bar_lands_on_the_reported_figure(self):
        rows, _ = fundamentals.income_bridge(self.frame())
        landed = dict(zip([r["label"] for r in rows], self.levels(rows)))
        self.assertAlmostEqual(landed["Gross profit"], 97858.0, places=6)
        self.assertAlmostEqual(landed["Operating income"], 81453.0, places=6)
        self.assertAlmostEqual(landed["Net income"], 72880.0, places=6)

    def test_the_expense_bars_add_to_the_step_the_subtotals_demand(self):
        # Gross profit minus operating income, exactly — including whatever R&D and SG&A
        # don't account for, which is the residual bar's whole job.
        rows, _ = fundamentals.income_bridge(self.frame())
        names = {"R&D", "SG&A", "Other opex", "Operating expenses"}
        spend = sum(r["value"] for r in rows if r["label"] in names)
        self.assertAlmostEqual(spend, 81453.0 - 97858.0, places=6)

    def test_an_unexplained_remainder_gets_its_own_bar(self):
        # R&D and SG&A here account for less than the step, and the difference has to be
        # visible as a bar rather than absorbed into the two beside it.
        rows, _ = fundamentals.income_bridge(self.frame(OperatingIncome=70000.0))
        labels = [r["label"] for r in rows]
        self.assertIn("Other opex", labels)
        self.assertAlmostEqual(self.levels(rows)[-1], 72880.0, places=6)

    def test_rounding_alone_does_not_earn_a_bar(self):
        rows, _ = fundamentals.income_bridge(self.frame())
        self.assertNotIn("Other opex", [r["label"] for r in rows])

    def test_a_split_larger_than_the_step_falls_back_to_one_bar(self):
        # A filer who books something unusual can report R&D and SG&A adding to more than
        # gross profit minus operating income. Drawing both would overshoot and land the
        # error on the tax bar, so the step stays one honest bar instead.
        rows, _ = fundamentals.income_bridge(self.frame(ResearchDevelopment=90000.0))
        labels = [r["label"] for r in rows]
        self.assertIn("Operating expenses", labels)
        self.assertNotIn("R&D", labels)
        self.assertAlmostEqual(self.levels(rows)[-1], 72880.0, places=6)

    def test_a_missing_gross_profit_drops_the_stage_and_still_closes(self):
        rows, _ = fundamentals.income_bridge(
            self.frame(GrossProfit=float("nan"), CostOfRevenue=float("nan")))
        labels = [r["label"] for r in rows]
        self.assertNotIn("Gross profit", labels)
        self.assertIn("Costs & expenses", labels)
        self.assertAlmostEqual(self.levels(rows)[-1], 72880.0, places=6)

    def test_gross_profit_is_derived_from_cost_when_only_cost_was_filed(self):
        rows, _ = fundamentals.income_bridge(self.frame(GrossProfit=float("nan")))
        landed = dict(zip([r["label"] for r in rows], self.levels(rows)))
        self.assertAlmostEqual(landed["Gross profit"], 130497.0 - 32639.0, places=6)

    def test_a_statement_with_only_revenue_and_net_income_still_bridges(self):
        rows, _ = fundamentals.income_bridge(self.frame(
            GrossProfit=float("nan"), CostOfRevenue=float("nan"),
            OperatingIncome=float("nan")))
        self.assertEqual([r["label"] for r in rows],
                         ["Revenue", "Tax, interest & other", "Net income"])
        self.assertAlmostEqual(self.levels(rows)[-1], 72880.0, places=6)

    def test_a_loss_making_period_bridges_below_zero(self):
        rows, _ = fundamentals.income_bridge(self.frame(NetIncome=-5000.0))
        self.assertAlmostEqual(self.levels(rows)[-1], -5000.0, places=6)

    def test_the_reporting_currency_is_carried_rather_than_assumed(self):
        _, meta = fundamentals.income_bridge(self.frame(Currency="EUR"))
        self.assertEqual(meta["currency"], "€")

    def test_an_unknown_currency_prints_its_code(self):
        _, meta = fundamentals.income_bridge(self.frame(Currency="SEK"))
        self.assertEqual(meta["currency"], "SEK ")


class GrowthBridgeTests(unittest.TestCase):
    def frame(self, revenues):
        rows = [{"End": pd.Timestamp(f"{2020 + i}-12-31"), "Label": f"FY{2020 + i}",
                 "Currency": "USD", "Revenue": float(v), "NetIncome": float(v) * 0.2}
                for i, v in enumerate(revenues)]
        return fundamentals._frame(rows)

    def levels(self, rows):
        level, tops = 0.0, []
        for row in rows:
            level = level + row["value"] if row["kind"] == "delta" else row["value"]
            tops.append(level)
        return tops

    def test_the_last_bar_lands_on_the_latest_revenue(self):
        rows, _ = fundamentals.growth_bridge(self.frame([10.0, 17.0, 27.0, 61.0, 130.0]))
        self.assertAlmostEqual(self.levels(rows)[-1], 130.0, places=6)

    def test_the_changes_sum_to_the_whole_move(self):
        rows, _ = fundamentals.growth_bridge(self.frame([10.0, 17.0, 27.0, 61.0, 130.0]))
        moved = sum(r["value"] for r in rows if r["kind"] == "delta")
        self.assertAlmostEqual(moved, 120.0, places=6)

    def test_a_fall_is_carried_as_a_negative_change(self):
        rows, _ = fundamentals.growth_bridge(self.frame([27.0, 17.0]))
        self.assertLess([r for r in rows if r["kind"] == "delta"][0]["value"], 0)

    def test_the_pillars_are_named_apart_from_the_changes(self):
        # Without this the closing pillar and the change beside it both read "FY2024" and
        # mean different things.
        rows, _ = fundamentals.growth_bridge(self.frame([10.0, 17.0]))
        self.assertEqual(rows[0]["label"], "FY2020 revenue")
        self.assertEqual(rows[-1]["label"], "FY2021 revenue")
        self.assertEqual(rows[1]["label"], "FY2021")

    def test_one_period_is_not_a_bridge(self):
        with self.assertRaises(ValueError):
            fundamentals.growth_bridge(self.frame([10.0]))


class BridgeDispatchTests(unittest.TestCase):
    def setUp(self):
        testsupport.patch_income(self)

    def test_an_income_bridge_only_fetches_one_period(self):
        seen = {}

        def spy(ticker, period, periods):
            seen["periods"] = periods
            return testsupport.synthetic_income(ticker, period, periods)

        with mock.patch.object(fundamentals, "fetch", spy):
            fundamentals.bridge("NVDA", "income", "annual", 10)
        self.assertEqual(seen["periods"], 1)

    def test_a_growth_bridge_fetches_the_run_it_was_asked_for(self):
        seen = {}

        def spy(ticker, period, periods):
            seen["periods"] = periods
            return testsupport.synthetic_income(ticker, period, periods)

        with mock.patch.object(fundamentals, "fetch", spy):
            fundamentals.bridge("NVDA", "growth", "annual", 7)
        self.assertEqual(seen["periods"], 7)

    def test_an_unknown_bridge_is_refused(self):
        with self.assertRaises(ValueError):
            fundamentals.bridge("NVDA", "segments")


if __name__ == "__main__":
    unittest.main()
