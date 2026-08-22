"""Tests for clean_config, which is the only place render input is validated, and for the
one route that serves content rather than JSON.

The renderers assume a clean config and fail unhelpfully otherwise, so anything the
interface can send has to be caught here. Run with: python -m unittest
"""

import os
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

import app
import config
import data
import fundamentals
import presets
import renderers
import storage

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
class WaterfallConfigTests(unittest.TestCase):
    """The waterfall's three settings, which are fiscal rather than calendar.

    They are checked here rather than in the renderer for the reason every other setting
    is: the renderer is handed concrete values and a bad one has to fail as a 400 before
    the job is queued, not as a KeyError two minutes into a render.
    """

    def wf(self, **over):
        return cfg(chart="waterfall", **over)

    def test_the_bridge_defaults_to_the_income_statement(self):
        self.assertEqual(self.wf()["bridge"], "income")

    def test_an_unknown_bridge_is_refused(self):
        with self.assertRaises(ValueError):
            self.wf(bridge="segments")

    def test_an_unknown_statement_period_is_refused(self):
        with self.assertRaises(ValueError):
            self.wf(statement="monthly")

    def test_quarterly_statements_are_accepted(self):
        self.assertEqual(self.wf(statement="quarterly")["statement"], "quarterly")

    def test_the_period_count_is_bounded_rather_than_refused(self):
        # A number typed into a spinner is not the same class of input as a name off a
        # fixed list — clamping keeps a fat-fingered 900 from queueing a fetch nobody
        # meant, without failing a render over it.
        self.assertEqual(self.wf(periods=900)["periods"], fundamentals.MAX_PERIODS)
        self.assertEqual(self.wf(periods=1)["periods"], 2)
        self.assertEqual(self.wf(periods="lots")["periods"], 5)

    def test_typed_rows_stand_in_for_a_ticker(self):
        got = app.clean_config({"chart": "waterfall", "tickers": [],
                                "rows": [{"label": "Opening", "value": 10}]})
        self.assertEqual(got["tickers"], [])

    def test_without_rows_it_still_needs_one(self):
        with self.assertRaises(ValueError):
            app.clean_config({"chart": "waterfall", "tickers": []})


class MetaTests(unittest.TestCase):
    """What /api/meta publishes, which is where the interface builds its controls from."""

    def setUp(self):
        self.meta = app.app.test_client().get("/api/meta").get_json()

    def test_the_waterfall_is_offered_as_a_chart(self):
        self.assertIn("waterfall", [c["id"] for c in self.meta["charts"]])

    def test_the_bridges_and_statement_periods_come_from_the_registries(self):
        self.assertEqual([b["id"] for b in self.meta["bridges"]],
                         list(fundamentals.BRIDGES))
        self.assertEqual([s["id"] for s in self.meta["statements"]],
                         list(fundamentals.PERIODS))


class ClipTrimConfigTests(unittest.TestCase):
    """The in and out points, which decide how much of a take comes out as a file.

    A trim is a window on `duration + hold` rather than a re-timing of it, so the two
    numbers it is bounded against are the ones already in the config. Nothing here draws
    anything — see test_render.ClipTrimTests for the property that makes the window a real
    slice of the same take.
    """

    def test_a_config_that_never_mentions_a_clip_is_untrimmed(self):
        # The whole point of the defaults: an API caller written before there was a trim
        # posts exactly this, and has to keep getting the file it always got.
        c = cfg(duration=6, hold=1.5)
        self.assertEqual((c["clip_in"], c["clip_out"]), (0.0, None))

    def test_both_points_come_back_as_seconds(self):
        c = cfg(duration=6, hold=1.5, clip_in=2, clip_out=5)
        self.assertEqual((c["clip_in"], c["clip_out"]), (2.0, 5.0))

    def test_an_in_point_alone_runs_to_the_end_of_the_clip(self):
        c = cfg(duration=6, hold=1.5, clip_in=2)
        self.assertEqual((c["clip_in"], c["clip_out"]), (2.0, 7.5))

    def test_an_out_point_past_the_end_is_clamped_rather_than_refused(self):
        # Same reasoning as _clamp_start: the reveal is a field of its own, so trimming to
        # 8s and then shortening the reveal is an ordinary thing to do and should render
        # the clip that exists.
        self.assertEqual(cfg(duration=6, hold=1.5, clip_out=99)["clip_out"], 7.5)

    def test_an_out_point_on_the_end_leaves_the_clip_untrimmed(self):
        c = cfg(duration=6, hold=1.5, clip_out=7.5)
        self.assertEqual(c["clip_out"], 7.5)

    def test_an_in_point_past_the_end_is_refused(self):
        # Unlike the out point this cannot be clamped: it names no frames at all, and
        # there is nothing sensible to draw instead.
        with self.assertRaises(ValueError):
            cfg(duration=6, hold=1.5, clip_in=9)

    def test_a_clip_shorter_than_the_floor_is_refused(self):
        with self.assertRaises(ValueError):
            cfg(duration=6, hold=1.5, clip_in=2, clip_out=2 + renderers.MIN_CLIP / 2)

    def test_a_negative_or_unreadable_point_is_refused(self):
        for bad in ({"clip_in": -1}, {"clip_out": -0.5}, {"clip_out": "soon"},
                    {"clip_in": "2s"}):
            with self.subTest(**bad):
                with self.assertRaises(ValueError):
                    cfg(duration=6, hold=1.5, **bad)

    def test_the_trim_does_not_move_the_reveal_or_the_hold(self):
        # If it did, a trim would just be a shorter render — which the two fields above it
        # already are, and which is the thing this is not.
        c = cfg(duration=6, hold=1.5, clip_in=2, clip_out=5)
        self.assertEqual((c["duration"], c["hold"]), (6.0, 1.5))


class OutputNamingTests(unittest.TestCase):
    """slug(), which decides what a finished render is called.

    Cutting several clips out of one take is the ordinary way to use the trim, so the two
    things that matter here are that the files can be told apart and that they cannot
    overwrite each other.
    """

    def test_two_renders_queued_in_the_same_second_get_different_files(self):
        # The stamp is only good to the second, so this was one file written twice — the
        # second render replacing the first with no sign it had happened.
        c = cfg(duration=6, hold=1.5)
        self.assertNotEqual(app.slug(c, "aaaaaaaaaa"), app.slug(c, "bbbbbbbbbb"))

    def test_a_trimmed_render_names_its_window(self):
        name = app.slug(cfg(duration=6, hold=1.5, clip_in=2, clip_out=5), "abcdef1234")
        self.assertIn("2-5s", name)

    def test_an_untrimmed_render_names_no_window(self):
        # Nothing was cut, so there is no window to disambiguate and the name stays the
        # shape it has always been.
        self.assertNotIn("s_", app.slug(cfg(duration=6, hold=1.5), "abcdef1234")
                         .replace("_0821", ""))

    def test_the_container_still_follows_the_alpha_setting(self):
        self.assertTrue(app.slug(cfg(transparent=False), "a1b2c3").endswith(".mp4"))
        self.assertTrue(app.slug(cfg(transparent=True), "a1b2c3").endswith(".mov"))

    def test_a_queued_job_reports_the_frames_it_will_actually_write(self):
        # Queuing really does start the worker on these, so the render itself is stubbed
        # out: this module draws nothing and reaches no network, and the numbers being
        # checked are the ones set at creation anyway. Without the stub the child would go
        # looking for prices.
        body = {"chart": "line", "tickers": ["AAPL"], "start": "2024-01-01",
                "duration": 6, "hold": 1.5, "fps": 60, "quality": "final"}
        with mock.patch.object(app.render_job, "run"):
            client = app.app.test_client()
            full = client.post("/api/render", json=body).get_json()["id"]
            cut = client.post("/api/render",
                              json={**body, "clip_in": 2, "clip_out": 5}).get_json()["id"]
            rows = {j["id"]: j for j in client.get("/api/jobs").get_json()}
        # 7.5s at 60fps, against the 3s the trim asked for.
        self.assertEqual(rows[full]["total"], 450)
        self.assertEqual(rows[cut]["total"], 180)
        # And the two are not about to land on the same file.
        self.assertNotEqual(rows[full]["file"], rows[cut]["file"])


class OutputRetentionTests(unittest.TestCase):
    """The ceiling on the outputs directory.

    Nothing else ever deletes a render, so on a host anybody can reach this is what stands
    between a busy afternoon and a volume with no room to write the next file. Real files in
    a temp directory rather than a mocked filesystem — which ones survive is the whole
    question — but sized in bytes rather than megabytes, because the suite runs on every
    change and writing real gigabytes to prove arithmetic would be a poor trade.
    """

    CAP = 1e-6          # gigabytes, so the ceiling lands on 1000 bytes

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._out, self._cap = config.OUT_DIR, config.OUT_MAX_GB
        config.OUT_DIR = self.dir
        self.addCleanup(shutil.rmtree, self.dir, True)

    def tearDown(self):
        config.OUT_DIR, config.OUT_MAX_GB = self._out, self._cap

    def write(self, name, size, age_s=0):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(b"\0" * size)
        when = time.time() - age_s
        os.utime(path, (when, when))
        return path

    def names(self):
        return sorted(os.listdir(self.dir))

    def test_no_ceiling_deletes_nothing(self):
        # The local default. A render on a laptop is a file its owner asked for, and
        # deleting it to reclaim space would be the surprise.
        config.OUT_MAX_GB = 0
        for i in range(4):
            self.write(f"clip{i}.mp4", 4000, age_s=100 - i)
        self.assertEqual(storage.prune(), [])
        self.assertEqual(len(self.names()), 4)

    def test_the_oldest_go_first(self):
        config.OUT_MAX_GB = self.CAP
        self.write("old.mp4", 600, age_s=300)
        self.write("middle.mp4", 600, age_s=200)
        self.write("newest.mp4", 600, age_s=100)
        self.assertEqual(storage.prune(), ["old.mp4", "middle.mp4"])
        self.assertEqual(self.names(), ["newest.mp4"])

    def test_it_stops_as_soon_as_it_is_under_the_ceiling(self):
        # 1200 bytes against a 1000-byte ceiling: dropping the oldest is enough, and the
        # newer file has to survive.
        config.OUT_MAX_GB = self.CAP
        self.write("old.mp4", 800, age_s=300)
        self.write("new.mp4", 400, age_s=100)
        self.assertEqual(storage.prune(), ["old.mp4"])
        self.assertEqual(self.names(), ["new.mp4"])

    def test_the_render_that_just_finished_is_never_deleted(self):
        # The job is about to report this URL. One that 404s the moment it appears is a
        # worse outcome than a directory briefly over its limit.
        config.OUT_MAX_GB = self.CAP
        self.write("just-rendered.mov", 5000, age_s=0)
        self.assertEqual(storage.prune(keep="just-rendered.mov"), [])
        self.assertEqual(self.names(), ["just-rendered.mov"])

    def test_a_huge_new_render_evicts_everything_else_first(self):
        config.OUT_MAX_GB = self.CAP
        self.write("older.mp4", 500, age_s=300)
        self.write("old.mp4", 500, age_s=200)
        self.write("big.mov", 4000, age_s=0)
        self.assertEqual(storage.prune(keep="big.mov"), ["older.mp4", "old.mp4"])
        self.assertEqual(self.names(), ["big.mov"])

    def test_it_only_ever_touches_renders(self):
        # Anything else on that volume was put there by a person. A disk ceiling is not the
        # place to start guessing about what.
        config.OUT_MAX_GB = self.CAP
        self.write("clip.mp4", 5000, age_s=300)
        for other in ("notes.txt", ".gitkeep", "presets.json"):
            self.write(other, 5000, age_s=400)
        self.assertEqual(storage.prune(), ["clip.mp4"])
        self.assertEqual(self.names(), [".gitkeep", "notes.txt", "presets.json"])

    def test_a_missing_directory_is_not_an_error(self):
        # prune runs after a render lands, and the render is what mattered — see the worker.
        config.OUT_MAX_GB = 1
        config.OUT_DIR = os.path.join(self.dir, "gone")
        self.assertEqual(storage.prune(), [])


class SafeAreaTests(unittest.TestCase):
    """The short-form safe areas: the table, and the two ways a config uses it.

    There are two separate things here and keeping them apart is the point. `fit` is a
    render setting that moves the composition inside the chrome. The *guides* are a preview
    overlay that only shows where the chrome falls, and they still never reach a render.
    """

    def setUp(self):
        self.meta = app.app.test_client().get("/api/meta").get_json()
        with open(os.path.join(HERE, "templates", "index.html")) as fh:
            self.html = fh.read()

    def test_every_profile_is_published_with_its_four_insets(self):
        # Four, not three: "all" is derived and served alongside the measured ones.
        self.assertEqual([s["id"] for s in self.meta["safe_areas"]],
                         [*renderers.SAFE_AREAS, renderers.FIT_ALL])
        for area in self.meta["safe_areas"]:
            with self.subTest(area=area["id"]):
                self.assertTrue(area["label"])
                for edge in ("top", "right", "bottom", "left"):
                    self.assertGreater(area[edge], 0)
                # An inset large enough to swallow the frame would be a measurement error
                # rather than a guide.
                self.assertLess(area["top"] + area["bottom"], 0.6)
                self.assertLess(area["left"] + area["right"], 0.4)

    def test_all_is_the_worst_edge_of_each_app(self):
        # Derived rather than a fourth row to maintain: a clip that clears every app's
        # chrome is one that clears the deepest inset on each side.
        union = renderers.safe_area(renderers.FIT_ALL)
        for edge in ("top", "right", "bottom", "left"):
            with self.subTest(edge=edge):
                self.assertEqual(union[edge],
                                 max(a[edge] for a in renderers.SAFE_AREAS.values()))

    def test_the_whole_frame_is_the_default_and_has_no_insets(self):
        self.assertIsNone(renderers.safe_area(renderers.FIT_NONE))
        self.assertEqual(cfg()["fit"], renderers.FIT_NONE)

    def test_a_fit_needs_a_vertical_frame(self):
        # The insets were measured off a 9:16 screen, so applying them to another shape
        # would inset a composition against numbers describing a different frame.
        for aspect in ("16:9", "1:1"):
            with self.subTest(aspect=aspect):
                with self.assertRaises(ValueError):
                    cfg(aspect=aspect, fit="tiktok")
        self.assertEqual(cfg(aspect="9:16", fit="tiktok")["fit"], "tiktok")

    def test_an_unknown_fit_is_refused(self):
        with self.assertRaises(ValueError):
            cfg(aspect="9:16", fit="snapchat")

    def test_a_kit_can_carry_the_fit(self):
        # Which app a channel posts to is picked once, which is what a brand kit is for.
        self.assertIn("fit", presets.FIELDS)

    def test_the_guides_are_never_part_of_the_posted_config(self):
        # The overlay state is the thing being kept out, not the fit. config() is what both
        # /api/preview and /api/render are sent, so a guide living outside it is what makes
        # "a guide cannot end up in a render" structural rather than remembered.
        posted = re.search(r"function config\(\)\{(.*?)\n\}", self.html, re.S)
        self.assertIsNotNone(posted)
        self.assertNotIn("guides", posted.group(1))
        # And the render setting is in there, because that one does belong.
        self.assertIn("fit", posted.group(1))

    def test_clean_config_ignores_a_posted_guide(self):
        self.assertNotIn("guides", cfg(guides="tiktok"))


class RailSectionTests(unittest.TestCase):
    """The two rails' collapsible sections, and the things that make splitting them safe.

    A section that is closed still feeds config(), so a setting hidden without a digest
    saying what it is set to is a setting nobody can see is wrong — and that holds
    whichever rail the section ended up in. Structure only — there is no browser here, so
    this pins what the markup promises rather than what it does.
    """

    # The settings you only ever pick once for a channel. They are the reason the rail
    # collapses, and now also the reason there are two of them.
    SET_ONCE = ("gOutput", "gLabels", "gKit")

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "templates", "index.html")) as fh:
            cls.html = fh.read()
        cls.rails = dict(re.findall(
            r'<aside class="rail[^"]*" id="(\w+)">(.*?)</aside>', cls.html, re.S))

    def sections(self, rail=None):
        bodies = [self.rails[rail]] if rail else self.rails.values()
        return [s for body in bodies
                for s in re.findall(r'<details class="group"[^>]*id="(g\w+)"', body)]

    def test_both_rails_are_found_and_hold_sections(self):
        # Everything below reads the rails by name, so a renamed or dropped <aside> has to
        # fail here rather than quietly reducing the rest of this class to no-ops.
        self.assertEqual(sorted(self.rails), ["railLeft", "railRight"])
        for rail in self.rails:
            with self.subTest(rail=rail):
                self.assertTrue(self.sections(rail))

    def test_every_section_carries_a_digest(self):
        digests = set(re.findall(r'<span class="digest" id="(d\w+)"', self.html))
        for sec in self.sections():
            with self.subTest(section=sec):
                self.assertIn("d" + sec[1:], digests)

    def test_every_section_is_listed_in_the_script_that_fills_the_digests(self):
        # digests() walks SECTIONS, not the DOM, so a section missing from that array gets
        # a summary that stays blank forever rather than one that throws.
        listed = re.findall(r'\["(g\w+)",', self.html)
        self.assertEqual(sorted(listed), sorted(self.sections()))

    def test_no_control_sits_outside_a_section(self):
        # Everything in a rail has to be inside a disclosure. A stray control would be the
        # one setting with nowhere to collapse to and no digest to describe it.
        for rail, body in self.rails.items():
            with self.subTest(rail=rail):
                orphaned = re.sub(r"<details.*?</details>", "", body, flags=re.S)
                self.assertNotIn('class="ctrl', orphaned)
                self.assertNotIn("<button", orphaned)

    def test_the_set_once_sections_start_closed(self):
        # This is the whole point of the disclosures: output, labels and the brand kit are
        # a channel's settings rather than a render's, and leaving them open is what made
        # one rail three screens tall. <details> is open when the attribute is present.
        for sec in self.SET_ONCE:
            with self.subTest(section=sec):
                tag = re.search(rf'<details class="group" id="{sec}"[^>]*>', self.html)
                self.assertNotIn(" open", tag.group(0))

    def test_nothing_set_once_is_left_in_the_first_rail(self):
        # The point of the split: the left rail is the chart itself, so everything you
        # pick once and stop looking at belongs on the other side of the preview. A
        # set-once section drifting back is the clutter this removed, returning.
        left = self.sections("railLeft")
        for sec in self.SET_ONCE:
            with self.subTest(section=sec):
                self.assertNotIn(sec, left)


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
