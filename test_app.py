"""Tests for clean_config, which is the only place render input is validated.

The renderers assume a clean config and fail unhelpfully otherwise, so anything the
interface can send has to be caught here. Run with: python -m unittest
"""

import time
import unittest
from unittest import mock

import app
import data


def cfg(**over):
    raw = {"chart": "line", "tickers": ["AAPL"], "start": "2024-01-01"}
    raw.update(over)
    return app.clean_config(raw)


class IntervalValidationTests(unittest.TestCase):
    def test_defaults_to_daily(self):
        self.assertEqual(cfg()["interval"], "1d")

    def test_unknown_interval_is_refused(self):
        with self.assertRaises(ValueError):
            cfg(interval="7s")

    def test_daily_start_is_left_alone(self):
        self.assertEqual(cfg(interval="1d", start="2019-05-02")["start"], "2019-05-02")

    def test_intraday_start_is_pulled_forward_to_what_yahoo_keeps(self):
        # The start date defaults to a year no intraday interval can reach, so without
        # this every first switch to 5m would be an error instead of a chart.
        got = cfg(interval="5m", start="2024-01-01")["start"]
        floor = time.strftime("%Y-%m-%d", time.localtime(time.time() - 60 * 86400))
        self.assertEqual(got, floor)

    def test_a_start_inside_the_window_is_kept(self):
        recent = time.strftime("%Y-%m-%d", time.localtime(time.time() - 3 * 86400))
        self.assertEqual(cfg(interval="5m", start=recent)["start"], recent)

    def test_a_junk_start_is_refused_rather_than_guessed_at(self):
        # The date fields can only send an ISO date; the API can send anything. Saying so
        # beats clamping an unreadable date to the interval floor and rendering a window
        # nobody asked for.
        with self.assertRaises(ValueError) as caught:
            cfg(interval="1m", start="not-a-date")
        self.assertIn("Start date", str(caught.exception))

    def test_a_start_older_than_the_interval_reaches_is_pulled_forward(self):
        got = cfg(interval="1m", start="2020-01-01")["start"]
        floor = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
        self.assertEqual(got, floor)

    def test_intraday_is_refused_where_yfinance_is_absent(self):
        # Stooq is daily-only, so without yfinance there is nothing to serve an intraday
        # render. Better a clear refusal than a render that fails at the end.
        with mock.patch.object(data, "intraday_available", return_value=False):
            with self.assertRaises(ValueError) as caught:
                cfg(interval="5m")
            self.assertIn("yfinance", str(caught.exception))

    def test_daily_still_works_without_yfinance(self):
        with mock.patch.object(data, "intraday_available", return_value=False):
            self.assertEqual(cfg(interval="1d")["interval"], "1d")


if __name__ == "__main__":
    unittest.main()
