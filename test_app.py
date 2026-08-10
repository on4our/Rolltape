"""Tests for clean_config, which is the only place render input is validated, and for the
one route that serves content rather than JSON.

The renderers assume a clean config and fail unhelpfully otherwise, so anything the
interface can send has to be caught here. Run with: python -m unittest
"""

import os
import re
import time
import unittest
from unittest import mock

import app
import data

HERE = os.path.dirname(os.path.abspath(__file__))


# Registered at import time because Flask refuses new routes once the app has served a
# request, and it is the only way to prove a real exception reaches the 500 handler rather
# than testing that handler in isolation.
@app.app.route("/boom-for-test")
def _boom():
    raise RuntimeError("boom")


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


class PricingPageTests(unittest.TestCase):
    """docs/pricing.md decides the prices; templates/pricing.html only quotes them.

    Nothing in the code enforces a tier, so a price that drifts from the doc has nothing
    else to bump into — the page would simply advertise the wrong number until someone
    noticed by eye. These tests are that second reader.
    """

    def setUp(self):
        self.client = app.app.test_client()

    def _read(self, name):
        with open(os.path.join(HERE, name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_page_is_served(self):
        # Closed explicitly: the route hands back an open file, and letting the test
        # garbage-collect it turns a passing run into a wall of ResourceWarnings.
        with self.client.get("/pricing") as got:
            self.assertEqual(got.status_code, 200)
            self.assertIn("text/html", got.headers["Content-Type"])

    def test_prices_match_the_pricing_doc(self):
        # The doc's table heads each column "**Hobbyist $9**"; the page carries the same
        # number as the data-monthly its billing toggle multiplies up from.
        doc = dict(re.findall(r"\*\*(\w+) \$(\d+)\*\*", self._read("docs/pricing.md")))
        page = dict(re.findall(
            r'class="tier-name">(\w+)</div>.*?data-monthly="(\d+)"',
            self._read("templates/pricing.html"), re.S))
        self.assertEqual(doc, page)
        self.assertEqual(set(doc), {"Hobbyist", "Creator", "Studio"})

    def test_annual_is_ten_times_monthly(self):
        # The page derives every annual price from this one rule rather than hardcoding
        # three more numbers, so the doc changing its mind about the discount is a code
        # change and should fail here first.
        doc = self._read("docs/pricing.md")
        monthly = [int(p) for _, p in re.findall(r"\*\*(\w+) \$(\d+)\*\*", doc)]
        stated = [int(p) for p in re.findall(
            r"\$(\d+)", re.search(r"Annual is 10x monthly — (.+?)\.", doc).group(1))]
        self.assertEqual(stated, [m * 10 for m in monthly])
        self.assertIn("monthly * 10", self._read("templates/pricing.html"))


class AutoCalloutConfigTests(unittest.TestCase):
    """Which corporate events the timeline is asked to mark.

    Unlike the moving-average field, a typo here raises. These arrive from a fixed row of
    toggles rather than a free-text box, so an unknown name is a caller's mistake — and
    dropping it silently would mean marks that never appear on a chart that renders fine.
    """

    def test_off_by_default(self):
        # The same contract as camera=locked and ma_lag=none: a config written before this
        # existed renders exactly as it always did.
        self.assertEqual(cfg()["auto_annotations"], [])
        self.assertEqual(cfg(auto_annotations=None)["auto_annotations"], [])

    def test_the_kinds_are_kept(self):
        self.assertEqual(cfg(auto_annotations=["earnings", "splits"])["auto_annotations"],
                         ["earnings", "splits"])

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            cfg(auto_annotations=["earnings", "buybacks"])
        self.assertIn("must be one of", str(caught.exception))

    def test_the_shape_the_browser_sends_survives(self):
        self.assertEqual(cfg(auto_annotations=[" Earnings "])["auto_annotations"],
                         ["earnings"])
        self.assertEqual(cfg(auto_annotations="splits,earnings")["auto_annotations"],
                         ["earnings", "splits"])

    def test_duplicates_collapse_and_the_order_is_the_registry_order(self):
        # Two of one kind would be two lookups and two marks on the same date, and the
        # order decides which label gets the space when a chart is crowded.
        self.assertEqual(
            cfg(auto_annotations=["splits", "earnings", "splits"])["auto_annotations"],
            ["earnings", "splits"])

    def test_the_interface_is_told_which_kinds_exist(self):
        # The toggles are built from /api/meta the way the chart list is, so a fourth kind
        # needs no edit to the template.
        meta = app.app.test_client().get("/api/meta").get_json()
        self.assertEqual([k["id"] for k in meta["event_kinds"]], list(data.EVENT_KINDS))
        self.assertTrue(all(k["label"] and k["desc"] for k in meta["event_kinds"]))


class RailSectionTests(unittest.TestCase):
    """The rail's collapsible sections, and the one thing that makes collapsing safe.

    A section that is closed still feeds config(), so a setting hidden without a digest
    saying what it is set to is a setting nobody can see is wrong. Structure only — there
    is no browser here, so this pins what the markup promises rather than what it does.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "templates", "index.html")) as fh:
            cls.html = fh.read()
        cls.rail = re.search(r'<aside class="rail">(.*?)</aside>', cls.html, re.S).group(1)

    def sections(self):
        return re.findall(r'<details class="group"[^>]*id="(g\w+)"', self.rail)

    def test_every_section_carries_a_digest(self):
        digests = set(re.findall(r'<span class="digest" id="(d\w+)"', self.rail))
        self.assertTrue(self.sections())
        for sec in self.sections():
            with self.subTest(section=sec):
                self.assertIn("d" + sec[1:], digests)

    def test_every_section_is_listed_in_the_script_that_fills_the_digests(self):
        # digests() walks SECTIONS, not the DOM, so a section missing from that array gets
        # a summary that stays blank forever rather than one that throws.
        listed = re.findall(r'\["(g\w+)",', self.html)
        self.assertEqual(sorted(listed), sorted(self.sections()))

    def test_no_control_sits_outside_a_section(self):
        # Everything in the rail has to be inside a disclosure. A stray control would be
        # the one setting with nowhere to collapse to and no digest to describe it.
        orphaned = re.sub(r"<details.*?</details>", "", self.rail, flags=re.S)
        self.assertNotIn('class="ctrl', orphaned)
        self.assertNotIn("<button", orphaned)

    def test_the_set_once_sections_start_closed(self):
        # This is the whole point: output, labels and the brand kit are a channel's
        # settings rather than a render's, and leaving them open is what made the rail
        # three screens tall. <details> is open when the attribute is present.
        for sec in ("gOutput", "gLabels", "gKit"):
            with self.subTest(section=sec):
                tag = re.search(rf'<details class="group" id="{sec}"[^>]*>', self.rail)
                self.assertNotIn(" open", tag.group(0))


class ErrorPageTests(unittest.TestCase):
    """The split that matters: pages get HTML, /api/* keeps getting JSON.

    The interface calls .json() on every API response, so an HTML body under /api/ would
    turn a missing route into a parse error on the client rather than a message.
    """

    def setUp(self):
        self.client = app.app.test_client()

    def test_a_missing_page_renders_the_styled_page(self):
        resp = self.client.get("/nope")
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(resp.mimetype.startswith("text/html"))
        self.assertIn(b"Nothing at this address.", resp.data)

    def test_a_missing_render_says_the_file_is_gone(self):
        resp = self.client.get("/outputs/not-a-real-render.mp4")
        self.assertEqual(resp.status_code, 404)
        self.assertIn(b"outputs folder", resp.data)

    def test_the_path_is_shown_and_escaped(self):
        # The address is the useful part of a stale download link, and it is also the one
        # piece of the page that comes from the request.
        resp = self.client.get("/outputs/%3Cimg%20src=x%3E.mp4")
        self.assertIn(b"&lt;img src=x&gt;.mp4", resp.data)
        self.assertNotIn(b"<img src=x>", resp.data)

    def test_a_missing_api_route_stays_json(self):
        resp = self.client.get("/api/nope")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.mimetype, "application/json")
        self.assertIn("error", resp.get_json())

    def test_a_route_that_answers_its_own_404_is_untouched(self):
        # An errorhandler only fires for a raised or aborted response, so the views that
        # already return their own jsonify 404 have to keep doing exactly that.
        for path in ("/api/presets/no-such-kit", "/examples/nope.png"):
            with self.subTest(path=path):
                method = self.client.delete if "presets" in path else self.client.get
                resp = method(path)
                self.assertEqual(resp.status_code, 404)
                self.assertEqual(resp.mimetype, "application/json")

    def test_an_unhandled_exception_renders_the_page_not_a_traceback(self):
        # Werkzeug re-raises to the test client unless the app is asked not to; serving
        # with debug off, as the app does, already behaves this way.
        app.app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            resp = self.client.get("/boom-for-test")
        finally:
            app.app.config.pop("PROPAGATE_EXCEPTIONS", None)
        self.assertEqual(resp.status_code, 500)
        self.assertIn(b"Rolltape hit an error.", resp.data)
        self.assertNotIn(b"RuntimeError", resp.data)


if __name__ == "__main__":
    unittest.main()
