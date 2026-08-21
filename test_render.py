"""Tests for the export path — background handling, date labelling and the still export.

No network and no ffmpeg: everything here draws stills from generated prices supplied by
testsupport.py, which exercises the same figure scaffolding a video render uses without
paying for an encode.
Run with: python -m unittest
"""

import io
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import app as appmod
import data
import fundamentals
import renderers
import testsupport
# Imported after renderers so the Agg backend is already selected.
import matplotlib.image as mpimg

BASE = {"start": "2024-01-01", "end": "2024-06-01", "duration": 1.0, "hold": 0.2}

# Enough of each chart to draw. Charts that compare need more than one ticker.
CHART_FIXTURES = {
    "line": {"tickers": ["NVDA"]},
    "compare": {"tickers": ["NVDA", "AMD", "MU"]},
    "candles": {"tickers": ["NVDA"]},
    "bars": {"tickers": ["NVDA", "AMD", "MU"], "metric": "return"},
    "timeline": {"tickers": ["NVDA"],
                 "annotations": [{"date": "2024-03-01", "label": "Q1"}]},
    "race": {"tickers": ["NVDA", "AMD", "MU"]},
    # Statements rather than prices, and the fixture says so — several tests below sweep
    # this dict, so a chart missing from it is silently untested rather than failing.
    "waterfall": {"tickers": ["NVDA"], "bridge": "income"},
}


def draw(cfg, at=0.7, quality="draft", dpi=None):
    """Draw one frame and read it back as an RGBA array, 0-255."""
    buf = io.BytesIO()
    renderers.save_still(cfg, buf, at=at, quality=quality, dpi=dpi)
    buf.seek(0)
    return np.rint(mpimg.imread(buf, format="png") * 255).astype(int)


def alpha_of(cfg, quality="draft"):
    return draw(cfg, quality=quality)[:, :, 3]


def size_of(img):
    """(width, height) from an imread array, which is indexed row-first."""
    return img.shape[1], img.shape[0]


class GeneratedDataCase(unittest.TestCase):
    """Base for the drawing tests: prices and statements come from testsupport, never a
    network. Both are patched for every case so the sweeps over CHART_FIXTURES cover the
    waterfall on the same footing as the price charts."""

    def setUp(self):
        testsupport.patch_fetch(self)
        testsupport.patch_income(self)

    def cfg(self, chart="line", **kw):
        return appmod.clean_config({**BASE, **CHART_FIXTURES[chart],
                                    "chart": chart, **kw})


class BackgroundTests(GeneratedDataCase):
    def test_every_chart_type_honours_the_background_setting(self):
        # A renderer that reads theme["bg"] directly instead of ctx.bg would paint a
        # backdrop into a transparent export and quietly ruin the overlay.
        for chart in CHART_FIXTURES:
            with self.subTest(chart=chart):
                solid = alpha_of(self.cfg(chart, transparent=False))
                clear = alpha_of(self.cfg(chart, transparent=True))
                self.assertEqual(solid[3, 3], 255, "solid render has a see-through corner")
                self.assertEqual(clear[3, 3], 0, "transparent render painted the backdrop")
                self.assertEqual(clear.max(), 255, "transparent render drew nothing")

    def test_ctx_bg_follows_the_alpha_setting(self):
        theme = renderers.THEMES["midnight"]
        self.assertEqual(renderers.make_ctx("midnight", "16:9", "final").bg, theme["bg"])
        self.assertEqual(
            renderers.make_ctx("midnight", "16:9", "final", transparent=True).bg, "none")


class ContainerTests(GeneratedDataCase):
    def test_alpha_renders_land_in_a_mov(self):
        # h264 in an .mp4 has no alpha channel, so the container has to follow the codec.
        self.assertEqual(renderers.output_extension(False), ".mp4")
        self.assertEqual(renderers.output_extension(True), ".mov")

    def test_the_filename_matches_the_container(self):
        self.assertTrue(appmod.slug(self.cfg(transparent=False)).endswith(".mp4"))
        self.assertTrue(appmod.slug(self.cfg(transparent=True)).endswith(".mov"))


class StillExportTests(GeneratedDataCase):
    def test_a_still_is_the_real_frame_size(self):
        # This is what makes the export usable as a thumbnail — it has to be the frame
        # the video would have shown, at the size the video will be.
        for quality, expected in (("draft", (1280, 720)), ("final", (1920, 1080))):
            with self.subTest(quality=quality):
                self.assertEqual(size_of(draw(self.cfg(), quality=quality)), expected)
                ctx = renderers.make_ctx("midnight", "16:9", quality)
                self.assertEqual((ctx.w, ctx.h), expected)

    def test_the_preview_dpi_override_shrinks_the_payload(self):
        # The preview is base64'd into a JSON response, so it is drawn smaller on purpose.
        self.assertEqual(size_of(draw(self.cfg(), dpi=appmod.PREVIEW_DPI)), (1152, 648))

    def test_a_still_can_be_drawn_at_any_point_in_the_animation(self):
        for at in (0.05, 0.5, 1.0):
            with self.subTest(at=at):
                buf = io.BytesIO()
                renderers.save_still(self.cfg(), buf, at=at)
                self.assertGreater(len(buf.getvalue()), 0)

    def test_a_transparent_still_keeps_its_alpha(self):
        # The thumbnail path and the video path share one background decision.
        self.assertEqual(alpha_of(self.cfg(transparent=True))[3, 3], 0)
        self.assertEqual(alpha_of(self.cfg(transparent=False))[3, 3], 255)


class WindowTests(GeneratedDataCase):
    """From a posted config to the dates a fetch actually gets."""

    def test_a_preset_resolves_to_dates_before_it_reaches_a_renderer(self):
        # Renderers only ever see plain start and end dates; the preset stops here.
        cfg = self.cfg(range="ytd")
        self.assertTrue(cfg["start"].endswith("-01-01"))
        self.assertIsNone(cfg["end"])
        self.assertEqual(cfg["interval"], "1d")

    def test_a_preset_overrides_dates_left_over_from_custom(self):
        # The interface keeps posting whatever is in the two date fields so switching back
        # to Custom doesn't lose them.
        cfg = self.cfg(range="1y", start="2019-01-01", end="2019-06-01")
        self.assertNotEqual(cfg["start"], "2019-01-01")
        self.assertIsNone(cfg["end"])

    def test_custom_keeps_the_dates_it_was_given(self):
        cfg = self.cfg(range=appmod.CUSTOM_RANGE)
        self.assertEqual((cfg["start"], cfg["end"]), ("2024-01-01", "2024-06-01"))

    def test_a_config_with_no_range_at_all_still_means_its_dates(self):
        # The API predates the selector: a caller that knows only start and end keeps working.
        cfg = appmod.clean_config({"chart": "line", "tickers": ["NVDA"],
                                   "start": "2024-02-01", "end": "2024-03-01"})
        self.assertEqual(cfg["range"], appmod.CUSTOM_RANGE)
        self.assertEqual((cfg["start"], cfg["end"]), ("2024-02-01", "2024-03-01"))

    def test_a_backwards_custom_range_is_refused(self):
        with self.assertRaises(ValueError):
            self.cfg(range=appmod.CUSTOM_RANGE, start="2024-06-01", end="2024-01-01")

    def test_a_date_that_isnt_one_is_refused(self):
        with self.assertRaises(ValueError):
            self.cfg(range=appmod.CUSTOM_RANGE, start="last tuesday")

    def test_an_unknown_preset_is_refused(self):
        with self.assertRaises(ValueError):
            self.cfg(range="1 fortnight")

    def test_the_intraday_preset_asks_for_intraday_bars(self):
        cfg = self.cfg(range="1d")
        self.assertEqual(cfg["interval"], "5m")
        self.assertEqual(cfg["sessions"], 1)

    def test_every_chart_type_draws_an_intraday_window(self):
        # Intraday is a different shape of index — timestamps rather than dates, one
        # session rather than a year — and every renderer has to survive it.
        for chart in CHART_FIXTURES:
            with self.subTest(chart=chart):
                self.assertGreater(len(draw(self.cfg(chart, range="1d")).ravel()), 0)


class DateLabelTests(unittest.TestCase):
    """Tick labels and subtitles have to follow the window or they say nothing."""

    def test_a_session_is_labelled_by_the_clock(self):
        idx = pd.date_range("2024-06-03 09:30", periods=78, freq="5min")
        self.assertEqual(renderers._axis_fmt(idx), "%H:%M")
        self.assertEqual(renderers._range_label(idx, True), "03 Jun 2024, 09:30 – 15:55")
        self.assertEqual(renderers._stamp(idx[0], idx, True), "09:30")

    def test_intraday_across_sessions_names_both_days(self):
        # One date and two clock times only tells the truth within a single session.
        idx = pd.date_range("2024-06-03 09:30", periods=200, freq="5min")
        self.assertEqual(renderers._range_label(idx, True), "03 Jun – 04 Jun 2024")
        self.assertEqual(renderers._stamp(idx[0], idx, True), "03 Jun 09:30")

    def test_a_week_is_labelled_by_the_day(self):
        idx = pd.bdate_range("2024-06-03", "2024-06-07")
        self.assertEqual(renderers._axis_fmt(idx), "%d %b")
        self.assertEqual(renderers._range_label(idx, False), "03 Jun – 07 Jun 2024")

    def test_a_year_keeps_the_month_and_the_year(self):
        idx = pd.bdate_range("2023-06-01", "2024-06-14")  # both ends are weekdays
        self.assertEqual(renderers._axis_fmt(idx), "%b %Y")
        self.assertEqual(renderers._range_label(idx, False), "Jun 2023 – Jun 2024")

    def test_a_decade_drops_to_years(self):
        self.assertEqual(renderers._axis_fmt(pd.bdate_range("2014-06-01", "2024-06-01")),
                         "%Y")

    def test_a_short_window_across_new_year_keeps_both_years(self):
        idx = pd.bdate_range("2023-12-20", "2024-01-10")
        self.assertEqual(renderers._range_label(idx, False), "20 Dec 2023 – 10 Jan 2024")

    def test_an_unknown_window_falls_back_to_what_the_charts_always_did(self):
        self.assertEqual(renderers._axis_fmt(None), "%b %Y")


# Charts with a price y-axis. Bars and races are categorical — a log scale there would
# be meaningless, and negative metrics would make it undrawable.
PRICE_CHARTS = ("line", "compare", "candles", "timeline")


class LogScaleTests(GeneratedDataCase):
    def figure(self, chart, **kw):
        cfg = self.cfg(chart, **kw)
        ctx = renderers.make_ctx("midnight", "16:9", "draft")
        return renderers.CHARTS[chart]["fn"](cfg, ctx, None, still=0.9)

    def test_price_charts_draw_a_usable_log_axis(self):
        for chart in PRICE_CHARTS:
            with self.subTest(chart=chart):
                ax = self.figure(chart, log_scale=True).axes[0]
                lo, hi = ax.get_ylim()
                self.assertEqual(ax.get_yscale(), "log")
                # A log axis cannot draw a non-positive limit at all.
                self.assertGreater(lo, 0)
                labelled = [t for t in ax.get_yticks() if lo <= t <= hi]
                self.assertGreaterEqual(len(labelled), 3,
                                        f"only {len(labelled)} ticks on the whole axis")

    def test_the_axis_stays_linear_by_default(self):
        self.assertEqual(self.figure("line").axes[0].get_yscale(), "linear")

    def test_the_default_subtitle_says_when_the_scale_is_log(self):
        # A log axis flattens a big move and the viewer has no other way to tell.
        self.assertIn("log", renderers._scale_note(True))
        self.assertEqual(renderers._scale_note(False), "")

    def test_log_is_declined_when_the_data_cannot_take_it(self):
        # Falling back to linear beats failing a render over an axis preference.
        self.assertTrue(renderers._log_ok({"log_scale": True}, 12.0))
        self.assertFalse(renderers._log_ok({"log_scale": True}, 0.0))
        self.assertFalse(renderers._log_ok({"log_scale": True}, -3.0))
        self.assertFalse(renderers._log_ok({}, 12.0))

    def test_a_log_camera_never_plans_a_non_positive_floor(self):
        # The camera owns every price-axis limit, and it pads linearly — on a low-priced
        # series that padding crosses zero, which a log axis cannot draw at all. Every
        # move has to come out positive, on every frame, including the hold.
        ctx = renderers.make_ctx("midnight", "16:9", "draft")
        x = np.arange(200, dtype=float)
        y = np.linspace(0.4, 1.2, 200)
        cut = np.linspace(0, 199, 120).astype(int)
        for move in renderers.CAMERAS:
            with self.subTest(move=move):
                cam = renderers.Camera(
                    {"camera": move}, ctx, x=x, lo=y, hi=y,
                    head=renderers.head_track(x, cut, 30),
                    n_frames=120, hold_frames=30,
                    rest_y=(-0.4, 1.6), log=True)   # a pad wide enough to cross zero
                self.assertGreater(cam.y0.min(), 0)
                self.assertTrue((cam.y1 > cam.y0).all())

    def test_log_ticks_cover_the_axis_at_every_span(self):
        # The failure this guards against: a sub-decade range getting ticks at 60-90 and
        # then 100, with the whole 100-192 stretch unlabelled.
        loc = renderers._PriceLogLocator()
        for lo, hi in ((56.5, 192.0), (292.0, 409.0), (20.0, 400.0), (1.0, 1000.0)):
            with self.subTest(span=(lo, hi)):
                inside = [t for t in loc.tick_values(lo, hi) if lo <= t <= hi]
                self.assertGreaterEqual(len(inside), 3,
                                        f"{lo}-{hi} got {len(inside)} ticks")


MA_CHARTS = ("line", "candles", "timeline")


class MovingAverageTests(GeneratedDataCase):
    def figure(self, chart, at=0.9, **kw):
        cfg = self.cfg(chart, **kw)
        ctx = renderers.make_ctx("midnight", "16:9", "draft")
        return renderers.CHARTS[chart]["fn"](cfg, ctx, None, still=at)

    def ma_artists(self, ax):
        return [ln for ln in ax.get_lines()
                if (ln.get_label() or "").endswith("-day MA")]

    def ma_head(self, fig):
        """How far along the averages have been drawn."""
        return max(float(ln.get_xdata()[-1])
                   for ln in self.ma_artists(fig.axes[0]))

    def price_head(self, fig):
        """How far along everything that isn't an average has been drawn.

        Line2D covers the price line, its glow and the head marker; the collections cover
        the candles and their volume bars, where the count of paths is the reveal. A lag
        that moved either is holding back the reveal rather than the averages.
        """
        ax = fig.axes[0]
        mas = {id(ln) for ln in self.ma_artists(ax)}
        return ([float(ln.get_xdata()[-1]) for ln in ax.get_lines() if id(ln) not in mas],
                [len(c.get_paths()) for c in fig.axes[0].collections])

    def test_averages_are_drawn_and_keyed(self):
        for chart in MA_CHARTS:
            with self.subTest(chart=chart):
                ax = self.figure(chart, ma="50, 200").axes[0]
                lines = self.ma_artists(ax)
                self.assertEqual(len(lines), 2)
                for ln in lines:
                    self.assertGreater(len(ln.get_xdata()), 0, "line never got data")
                self.assertIsNotNone(ax.get_legend(), "no key drawn")

    def test_no_averages_means_no_key(self):
        ax = self.figure("line").axes[0]
        self.assertEqual(self.ma_artists(ax), [])
        self.assertIsNone(ax.get_legend())

    def test_an_average_is_warm_on_the_very_first_bar(self):
        # Without the run-up fetch a 200-day line would only begin 200 bars in — two
        # thirds of the way across a one-year chart.
        cfg = self.cfg("line", ma="200")
        df, series = renderers._fetch_with_ma("NVDA", cfg, [200])
        first = renderers._align_ma(series, df.index)[200][0]
        self.assertTrue(np.isfinite(first), "the average is still cold where drawing starts")

    def test_the_run_up_is_only_fetched_when_an_average_needs_it(self):
        asked = []
        real = data.fetch

        def spy(tk, start, end=None, interval=data.DEFAULT_INTERVAL, sessions=None):
            asked.append(start)
            return real(tk, start, end, interval, sessions)

        data.fetch = spy
        self.addCleanup(setattr, data, "fetch", real)
        renderers._fetch_with_ma("NVDA", self.cfg("line", ma="200"), [200])
        renderers._fetch_with_ma("NVDA", self.cfg("line"), [])
        with_ma, without = asked
        self.assertLess(with_ma, without)
        self.assertEqual(without, "2024-01-01")

    def test_candle_averages_stay_daily_through_a_rollup(self):
        # A "50-day" line on a chart whose candles are weeks must still mean fifty days.
        cfg = self.cfg("candles", start="2022-01-01", end="2025-01-01", ma="50")
        daily, series = renderers._fetch_with_ma("NVDA", cfg, [50])
        weekly = daily.resample("W").agg({"Close": "last"}).dropna()
        aligned = renderers._align_ma(series, weekly.index, ffill=True)[50]

        # Each bar carries the daily average as of that week...
        at = weekly.index[10]
        self.assertAlmostEqual(aligned[10], series[50].loc[:at].iloc[-1], places=6)
        # ...and not a 50-week average, which is what computing after the rollup gives.
        fifty_weeks = weekly["Close"].rolling(50).mean().iloc[-1]
        self.assertFalse(np.isclose(aligned[-1], fifty_weeks))

    def test_averages_never_reuse_the_price_colour(self):
        # Every theme's series palette overlaps its own up/down colours.
        import matplotlib.pyplot as plt
        for name, theme in renderers.THEMES.items():
            with self.subTest(theme=name):
                fig, ax = plt.subplots()
                self.addCleanup(plt.close, fig)
                ctx = renderers.make_ctx(name, "16:9", "draft")
                vals = np.arange(10.0)
                pairs = renderers._ma_lines(ax, ctx, [(50, vals), (200, vals)],
                                            avoid=(theme["up"], theme["down"]))
                used = {ln.get_color() for ln, _ in pairs}
                self.assertEqual(len(used), 2, "two averages came out the same colour")
                self.assertNotIn(theme["up"], used)
                self.assertNotIn(theme["down"], used)

    def test_a_cold_average_is_dropped_rather_than_drawn_empty(self):
        # An average with no value anywhere in range returns nothing, and _ma_lines
        # skips it — better than a legend entry pointing at an invisible line.
        x = np.arange(5.0)
        self.assertIsNone(renderers._dense_ma(x, np.full(5, np.nan), np.arange(5.0)))

    def test_the_leading_gap_survives_upsampling(self):
        # np.interp would smear the NaNs across the neighbouring interval.
        x = np.arange(5.0)
        ma = np.array([np.nan, np.nan, 3.0, 4.0, 5.0])
        dense = renderers._dense_ma(x, ma, np.linspace(0, 4, 21))
        self.assertTrue(np.isnan(dense[0]))
        self.assertTrue(np.isfinite(dense[-1]))
        self.assertFalse(np.isnan(dense[np.isfinite(dense)]).any())

    def test_the_averages_trail_the_price_line_without_holding_it_back(self):
        for chart in MA_CHARTS:
            with self.subTest(chart=chart):
                plain = self.figure(chart, at=0.5, ma="50")
                lagged = self.figure(chart, at=0.5, ma="50", ma_lag="bold")
                self.assertLess(self.ma_head(lagged), self.ma_head(plain),
                                "the average kept pace with the price line")
                self.assertEqual(self.price_head(lagged), self.price_head(plain),
                                 "the lag held back the reveal, not just the averages")

    def test_the_last_frame_is_the_same_chart_either_way(self):
        # The gap has to be closed by the end: a clip ending on a short average reads as a
        # render that failed to finish, and that frame is the one that becomes a thumbnail.
        for chart in MA_CHARTS:
            with self.subTest(chart=chart):
                base = draw(self.cfg(chart, ma="50"), at=1.0)
                lagged = draw(self.cfg(chart, ma="50", ma_lag="bold"), at=1.0)
                self.assertTrue(np.array_equal(base, lagged),
                                "the averages had not caught up by the final frame")

    def test_a_lag_with_nothing_to_lag_changes_nothing(self):
        # The interface hides the control when the averages field is empty, but an API
        # caller can still send it — inert beats half-applied.
        base = draw(self.cfg("line"))
        self.assertTrue(np.array_equal(base, draw(self.cfg("line", ma_lag="bold"))))


class MaLagPlanTests(unittest.TestCase):
    """The lag is a few lines of arithmetic over the reveal's own frame-to-index map, so
    everything that matters about it can be checked without drawing anything."""

    def track(self, lag, n_frames=60, fps=30, dense=1200, easing="out"):
        """A reveal's frame-to-index map, and the averages' one over the same frames."""
        cut = renderers._plan(n_frames / fps, 0.0, fps, easing, dense)[2]
        return cut, renderers.ma_track({"ma_lag": lag}, cut, n_frames, fps)

    def test_no_lag_is_the_reveal_itself(self):
        # What keeps "none" the default: an existing config draws exactly as it did.
        cut, track = self.track("none")
        self.assertIs(track, cut)
        self.assertIs(renderers.ma_track({}, cut, len(cut), 30), cut)

    def test_the_averages_never_run_ahead_of_the_price_or_backwards(self):
        for lag in renderers.MA_LAGS:
            with self.subTest(lag=lag):
                cut, track = self.track(lag)
                self.assertTrue((track <= cut).all(), "the average overtook the price")
                self.assertTrue((np.diff(track) >= 0).all(), "the average un-drew itself")
                self.assertEqual(track[-1], cut[-1], "it never caught up")

    def test_a_bigger_setting_is_a_bigger_gap(self):
        gaps = []
        for lag in renderers.MA_LAGS:
            cut, track = self.track(lag)
            gaps.append(int(cut[20] - track[20]))
        self.assertEqual(gaps, sorted(gaps))
        self.assertEqual(gaps[0], 0)
        self.assertGreater(gaps[-1], 0)

    def test_the_lag_runs_on_the_clock_not_on_the_frame_rate(self):
        # 60fps is twice the frames, not twice the lag: the same second of the clip has to
        # show the averages the same distance behind the price line.
        dense = 1200
        for lag in ("subtle", "standard", "bold"):
            with self.subTest(lag=lag):
                _, slow = self.track(lag, n_frames=60, fps=30, dense=dense,
                                     easing="linear")
                _, fast = self.track(lag, n_frames=120, fps=60, dense=dense,
                                     easing="linear")
                for i in range(0, 60, 5):
                    self.assertLess(abs(int(slow[i]) - int(fast[i * 2])), dense * 0.02)

    def test_a_short_reveal_caps_the_lag(self):
        # Half a second behind on a one-second reveal stops reading as a trailing average
        # and starts reading as a second, shorter one, so the delay is capped against the
        # reveal as well as set in seconds. On a long one the setting is the whole story.
        short = {"n_frames": 20, "fps": 20}    # one second of reveal
        long_ = {"n_frames": 240, "fps": 20}   # twelve
        self.assertTrue(np.array_equal(self.track("standard", **short)[1],
                                       self.track("bold", **short)[1]),
                        "a one-second reveal let the lag past its cap")
        self.assertFalse(np.array_equal(self.track("standard", **long_)[1],
                                        self.track("bold", **long_)[1]))


class MaLagConfigTests(unittest.TestCase):
    def base(self, **kw):
        return appmod.clean_config({"chart": "line", "tickers": ["NVDA"], **kw})

    def test_the_lag_is_off_unless_it_is_asked_for(self):
        self.assertEqual(self.base()["ma_lag"], "none")

    def test_a_lag_that_does_not_exist_is_rejected_by_name(self):
        with self.assertRaises(ValueError) as caught:
            self.base(ma_lag="enormous")
        self.assertIn("must be one of", str(caught.exception))

    def test_a_lag_survives_the_shape_the_browser_sends_it_in(self):
        self.assertEqual(self.base(ma_lag="  Subtle ")["ma_lag"], "subtle")


class MaPeriodTests(unittest.TestCase):
    def test_windows_are_parsed_from_free_text(self):
        # The UI field is free text, so junk is dropped rather than failing the render.
        cases = {
            "50, 200": [50, 200],
            "50,50,20": [20, 50],      # deduplicated and sorted
            "9 21 50 100 200": [9, 21, 50],  # capped at three
            "50.0": [50],
            "1": [],                   # too short to mean anything
            "999": [],                 # beyond any sane window
            "abc": [],
            "": [],
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(appmod.ma_periods(raw), want)

    def test_a_list_works_as_well_as_a_string(self):
        self.assertEqual(appmod.ma_periods([50, 200]), [50, 200])
        self.assertEqual(appmod.ma_periods(None), [])


# Three years of quarterly reporting, a dividend a week after each, and the split — which
# is deliberately more than any frame can label. The interesting cases all live at that
# density, because two callouts on a wide chart would pass any layout at all.
def _dense_events():
    rows = []
    for year in (2022, 2023, 2024):
        for month, day in ((2, 21), (5, 22), (8, 23), (11, 20)):
            rows.append({"date": f"{year}-{month:02d}-{day:02d}", "kind": "earnings",
                         "label": "Earnings"})
            rows.append({"date": f"{year}-{month:02d}-{day + 6:02d}", "kind": "dividends",
                         "label": "Dividend $0.04"})
    rows.append({"date": "2024-06-10", "kind": "splits", "label": "10-for-1 split"})
    return rows


class TimelineCalloutTests(GeneratedDataCase):
    """Where the timeline's callouts land, and what happens when too many want one spot.

    The properties rather than the pictures: that no two labels overlap, that a mark is
    never silently dropped to make room, and that a chart which asks for none of this is
    the chart it always was.
    """

    def setUp(self):
        super().setUp()
        testsupport.patch_events(self, _dense_events())

    def cfg(self, **kw):
        return appmod.clean_config({"chart": "timeline", "tickers": ["NVDA"],
                                    "start": "2022-01-01", "end": "2024-12-31",
                                    "duration": 1.0, "hold": 0.2, **kw})

    def measure(self, at=1.0, aspect="16:9", **kw):
        """Everything these tests read off one frame, with the figure closed again.

        Measured in a single pass rather than by handing the figure back: pyplot keeps
        every figure alive until something closes it, and the sweep below draws six.
        """
        cfg = self.cfg(**kw)
        ctx = renderers.make_ctx(cfg["theme"], aspect, "final")
        fig = renderers.CHARTS["timeline"]["fn"](cfg, ctx, None, still=at)
        try:
            r = fig.canvas.get_renderer()
            ax = fig.axes[0]
            # Faded-in callout text, which is what is on screen — not a title or a tick.
            labels = [(t.get_text(), t.get_position()[1], t.get_window_extent(renderer=r))
                      for t in ax.texts if t.get_text() and (t.get_alpha() or 0) > 0.05]
            return {
                "labels": labels,
                "boxes": [box for _, _, box in labels],
                # One dashed leader per callout, and nothing else here is dashed.
                "stems": sum(1 for ln in ax.lines if ln.get_linestyle() == "--"),
                "area": ax.get_window_extent(renderer=r),
                "ylim": ax.get_ylim(),
            }
        finally:
            renderers.plt.close(fig)

    def test_no_two_callout_labels_overlap(self):
        """The one that matters. Two labels on the same pixels is the whole failure mode.

        A row is a lift above each callout's *own* point rather than a shared height, so
        two labels one row apart whose prices differ by that lift land on the same line —
        which is why the check is a rectangle and not a column, and why this sweeps the
        aspects: a vertical frame has about a third of the width to fit them into.
        """
        for aspect in ("16:9", "9:16", "1:1"):
            for at in (0.5, 1.0):
                with self.subTest(aspect=aspect, at=at):
                    boxes = self.measure(
                        at=at, aspect=aspect,
                        auto_annotations=["earnings", "splits", "dividends"])["boxes"]
                    self.assertGreater(len(boxes), 3, "nothing was labelled at all")
                    for i, a in enumerate(boxes):
                        for b in boxes[i + 1:]:
                            self.assertFalse(a.overlaps(b),
                                             f"{a.bounds} overlaps {b.bounds}")

    def test_a_crowded_date_loses_its_label_and_keeps_its_mark(self):
        """Nothing is dropped — only the text thins out.

        A chart showing four of a year's eight earnings dates would read as the complete
        set. Since every one of those labels says the same word, losing some of the text
        loses no information; losing a mark would lose the date itself.
        """
        kinds = ["earnings", "splits", "dividends"]
        cfg = self.cfg(auto_annotations=kinds)
        notes = renderers.timeline_notes(cfg, *self._frame(cfg), False)
        drawn = self.measure(auto_annotations=kinds)
        self.assertEqual(drawn["stems"], len(notes))
        self.assertLess(len(drawn["labels"]), len(notes))

    def _frame(self, cfg):
        """The (df, x, y) a timeline render works from, for calling the planner directly."""
        df, _ = renderers._fetch_with_ma(cfg["tickers"][0], cfg, [])
        x = renderers._x_values(df.index, False)
        return df, x, df["Close"].to_numpy(float)

    def notes_for(self, **kw):
        cfg = self.cfg(**kw)
        return renderers.timeline_notes(cfg, *self._frame(cfg), False)

    def test_a_typed_callout_replaces_the_looked_up_one_on_its_day(self):
        # Two labels on one date is the collision the layout cannot solve, and the typed
        # one is the thing the feed couldn't have known.
        notes = self.notes_for(
            auto_annotations=["splits"],
            annotations=[{"date": "2024-06-10", "label": "Retail piles in"}])
        self.assertEqual([n["label"] for n in notes], ["Retail piles in"])

    def test_a_typed_callout_survives_alongside_the_looked_up_ones(self):
        notes = self.notes_for(
            auto_annotations=["splits"],
            annotations=[{"date": "2023-05-25", "label": "AI narrative begins"}])
        self.assertEqual(sorted(n["label"] for n in notes),
                         ["10-for-1 split", "AI narrative begins"])

    def test_an_event_outside_the_drawn_window_never_reaches_the_axis(self):
        # The lookup is given the chart's window, but a moving average widens the frame
        # behind it — so the drawn index is what decides, not the requested start.
        notes = self.notes_for(start="2024-01-01", auto_annotations=["earnings"])
        first = renderers.mdates.date2num(pd.Timestamp("2024-01-01").to_pydatetime())
        self.assertTrue(notes)
        self.assertTrue(all(n["x"] >= first for n in notes))

    def test_off_by_default(self):
        # Same contract as a locked camera and no average lag: a config written before this
        # existed renders exactly as it always did.
        self.assertEqual(self.cfg()["auto_annotations"], [])
        self.assertEqual(self.notes_for(), [])

    def test_a_timeline_with_no_callouts_keeps_its_old_framing(self):
        # The headroom only appears when there is something to lift into it, so a chart
        # that asks for no callouts is framed exactly as it was before they existed.
        plain = self.measure()["ylim"]
        rng = self._frame(self.cfg())[2]
        pad = (rng.max() - rng.min()) * 0.18
        self.assertAlmostEqual(plain[1], rng.max() + pad, places=4)
        marked = self.measure(auto_annotations=["earnings"])["ylim"]
        self.assertGreater(marked[1], plain[1])

    def test_every_label_stays_inside_the_axes(self):
        # A label anchored centre at the first or last bar hangs off the edge of the frame,
        # which is why the planner picks the anchor rather than always centring.
        for aspect in ("16:9", "9:16"):
            with self.subTest(aspect=aspect):
                drawn = self.measure(
                    aspect=aspect, auto_annotations=["earnings", "splits", "dividends"])
                area = drawn["area"]
                for text, _, box in drawn["labels"]:
                    self.assertGreaterEqual(round(box.x0), round(area.x0) - 1, text)
                    self.assertLessEqual(round(box.x1), round(area.x1) + 1, text)

    def test_callouts_arrive_as_the_reveal_reaches_them_and_never_leave(self):
        """`still=` jumps to a frame without drawing the ones before it.

        So the set on screen has to be decided by the frame index alone: it grows as the
        head crosses each date and never loses one already landed. A layout that settled
        as it went would hand the thumbnail export a different chart than the render, which
        is the same property the camera is planned for.

        Keyed on the callout's own x, because every earnings label says the same word.
        """
        def on_screen(at):
            drawn = self.measure(at=at, auto_annotations=["earnings", "splits"])
            return {round(box.x0 + box.width / 2) for _, _, box in drawn["labels"]}

        early, late = on_screen(0.35), on_screen(1.0)
        self.assertTrue(early)
        self.assertLess(len(early), len(late))
        self.assertEqual(on_screen(0.35), early)  # and the same frame twice agrees

    def test_a_callout_the_head_has_not_reached_is_not_drawn_yet(self):
        # The mark lands when the line arrives at it, so an early frame carries none of
        # the dates still ahead of the reveal.
        self.assertEqual(self.measure(at=0.02,
                                      auto_annotations=["splits"])["labels"], [])


class PlanCalloutTests(unittest.TestCase):
    """The planner on its own, where a case can be built exactly rather than drawn."""

    def plan(self, notes, ylim=(0.0, 100.0), log=False):
        ctx = renderers.make_ctx("midnight", "16:9", "final")
        return renderers.plan_callouts(notes, (0.0, 100.0), ylim, log, ctx, 15.0,
                                       renderers._plot_area(ctx, True))

    def note(self, x, y, label, kind="earnings"):
        return {"x": x, "y": y, "label": label, "kind": kind}

    def test_two_notes_on_the_same_spot_take_different_rows(self):
        notes = self.plan([self.note(50, 50, "Earnings"), self.note(51, 50, "Earnings")])
        self.assertEqual({n["row"] for n in notes}, {0, 1})

    def test_a_note_at_the_top_of_the_data_still_gets_its_label(self):
        # The renderer pads the frame above the highest close precisely so the bottom row
        # fits there. 81 is where that padding puts it in a 0-100 frame, and a callout on
        # the peak of the chart is the one nobody would accept losing.
        self.assertEqual(self.plan([self.note(50, 81, "Earnings")])[0]["row"], 0)

    def test_a_label_with_no_room_at_all_is_dropped_rather_than_pushed_off_frame(self):
        # Rows only go up, so the first that leaves the frame rules out the rest. Text
        # rendered outside the axes is worse than a mark that goes unlabelled.
        self.assertIsNone(self.plan([self.note(50, 99.5, "Earnings")])[0]["row"])

    def test_a_typed_callout_outranks_a_looked_up_one_for_the_space(self):
        # Both want the same spot and only one can have the bottom row. The typed one is
        # the thing that isn't repeated eight times across the chart.
        notes = self.plan([self.note(50, 50, "Earnings", "earnings"),
                           self.note(50.4, 50, "The squeeze begins", "manual")])
        rows = {n["kind"]: n["row"] for n in notes}
        self.assertEqual(rows["manual"], 0)
        self.assertNotEqual(rows["earnings"], 0)

    def test_a_split_outranks_an_earnings_label(self):
        # "Earnings" is on the chart eight times over and says the same thing each time;
        # a split ratio appears once and is the reason the price halved.
        notes = self.plan([self.note(50, 50, "Earnings", "earnings"),
                           self.note(50.4, 50, "10-for-1 split", "splits")])
        self.assertEqual({n["kind"]: n["row"] for n in notes}["splits"], 0)

    def test_labels_at_the_edges_are_anchored_inwards(self):
        notes = self.plan([self.note(0, 50, "First bar"), self.note(100, 50, "Last bar")])
        self.assertEqual([n["ha"] for n in notes], ["left", "right"])
        self.assertEqual(self.plan([self.note(50, 50, "Middle")])[0]["ha"], "center")

    def test_an_unlabelled_mark_keeps_a_short_stem(self):
        # It is a date too crowded to write on, not one the chart left out, so it still
        # reads as a mark rather than as a truncated callout. Four on one spot against
        # three rows, so the fourth is guaranteed to miss out.
        notes = self.plan([self.note(50 + i * 0.4, 50, "Earnings") for i in range(4)])
        placed = [n for n in notes if n["row"] is not None]
        dropped = [n for n in notes if n["row"] is None]
        self.assertEqual(len(placed), renderers.CALLOUT_ROWS)
        self.assertEqual([n["frac"] for n in dropped], [renderers.CALLOUT_TICK])
        self.assertTrue(all(n["frac"] > renderers.CALLOUT_TICK for n in placed))

    def test_a_log_frame_measures_height_multiplicatively(self):
        # A fixed offset in price is a different visual distance at each end of a log axis,
        # so a note near the bottom of a wide log range has plenty of room above it even
        # though its price is a large fraction of the top.
        notes = self.plan([self.note(50, 20, "Earnings")], ylim=(1.0, 1000.0), log=True)
        self.assertEqual(notes[0]["row"], 0)
class WaterfallLevelTests(unittest.TestCase):
    """Where the bars sit, worked out before the first frame rather than per frame.

    This is the same property the camera has and for the same reason: `still=` asks for a
    frame in the middle without drawing the ones before it, so a running total accumulated
    inside `draw` would hand the still export a different chart than the video.
    """

    def rows(self, *specs):
        return [{"label": label, "value": value, "kind": kind, "share": False}
                for label, value, kind in specs]

    def test_a_change_hangs_off_wherever_the_last_bar_ended(self):
        bases, tops = renderers._waterfall_levels(
            self.rows(("Revenue", 100.0, "start"), ("Cost", -30.0, "delta")))
        self.assertEqual(list(bases), [0.0, 100.0])
        self.assertEqual(list(tops), [100.0, 70.0])

    def test_a_level_is_drawn_from_zero(self):
        bases, tops = renderers._waterfall_levels(
            self.rows(("Revenue", 100.0, "start"), ("Cost", -30.0, "delta"),
                      ("Gross", 70.0, "total")))
        self.assertEqual(bases[2], 0.0)
        self.assertEqual(tops[2], 70.0)

    def test_the_bars_after_a_level_carry_on_from_it(self):
        # A subtotal restates the running figure rather than adding to it, so the change
        # after it starts where the pillar stopped and not at zero.
        bases, _ = renderers._waterfall_levels(
            self.rows(("Revenue", 100.0, "start"), ("Cost", -30.0, "delta"),
                      ("Gross", 70.0, "total"), ("Opex", -20.0, "delta")))
        self.assertEqual(bases[3], 70.0)

    def test_a_bridge_can_cross_zero(self):
        _, tops = renderers._waterfall_levels(
            self.rows(("Revenue", 100.0, "start"), ("Costs", -130.0, "delta")))
        self.assertEqual(tops[-1], -30.0)


class WaterfallDrawTests(GeneratedDataCase):
    def cfg(self, **kw):
        return appmod.clean_config({**BASE, "chart": "waterfall",
                                    **CHART_FIXTURES["waterfall"], **kw})

    def test_it_draws_from_typed_rows_with_no_source_at_all(self):
        # The manual path touches neither price feed nor statement endpoint, which is what
        # makes a bridge off a slide possible — and is also why this case patches nothing.
        cfg = appmod.clean_config({**BASE, "chart": "waterfall", "rows": [
            {"label": "Opening", "value": 100.0, "kind": "start"},
            {"label": "Won", "value": 30.0, "kind": "delta"},
            {"label": "Closing", "value": 130.0, "kind": "total"}]})
        with mock.patch.object(fundamentals, "fetch",
                               mock.Mock(side_effect=AssertionError("fetched anyway"))):
            img = draw(cfg, at=1.0)
        self.assertEqual(img.shape[2], 4)

    def test_a_typed_bridge_needs_no_ticker(self):
        cfg = appmod.clean_config({**BASE, "chart": "waterfall", "rows": [
            {"label": "Opening", "value": 100.0},
            {"label": "Won", "value": 30.0}]})
        self.assertEqual(cfg["tickers"], [])

    def test_the_reveal_actually_reveals(self):
        # Early in the reveal only the first bars have grown, so the two frames cannot be
        # the same image — a staggered reveal that drew everything at once would pass every
        # other test here.
        cfg = self.cfg()
        self.assertFalse(np.array_equal(draw(cfg, at=0.15), draw(cfg, at=1.0)))

    def test_the_date_range_changes_nothing(self):
        # A waterfall reads fiscal periods off a filing. If a range preset could move the
        # bars, the interface would be hiding a control that matters.
        self.assertTrue(np.array_equal(draw(self.cfg(range="1y"), at=1.0),
                                       draw(self.cfg(range="5y"), at=1.0)))

    def test_the_growth_bridge_draws_a_different_chart(self):
        self.assertFalse(np.array_equal(draw(self.cfg(bridge="income"), at=1.0),
                                        draw(self.cfg(bridge="growth"), at=1.0)))

    def test_two_bars_are_the_minimum(self):
        cfg = appmod.clean_config({**BASE, "chart": "waterfall",
                                   "rows": [{"label": "Only", "value": 1.0}]})
        with self.assertRaises(ValueError):
            draw(cfg, at=1.0)


class WaterfallColorTests(unittest.TestCase):
    def test_no_theme_paints_a_level_the_colour_of_a_change(self):
        # Two themes open their series palette with the same value they use for a rise, so
        # `series[0]` would make the closing pillar and the bar beside it indistinguishable
        # — the one distinction the chart exists to draw.
        for name, theme in renderers.THEMES.items():
            with self.subTest(theme=name):
                pillar = renderers._pillar_color(theme)
                self.assertNotEqual(pillar, theme["up"])
                self.assertNotEqual(pillar, theme["down"])


class CompactMoneyTests(unittest.TestCase):
    """Line items are read at the scale they are filed at, not the scale a price is."""

    def test_it_scales_to_the_magnitude(self):
        self.assertEqual(renderers._compact(130.497e9), "$130B")
        self.assertEqual(renderers._compact(1.05e9), "$1.05B")
        self.assertEqual(renderers._compact(60.9e6), "$60.9M")
        self.assertEqual(renderers._compact(4.2e12), "$4.20T")
        self.assertEqual(renderers._compact(512.0), "$512")

    def test_a_change_carries_its_sign_outside_the_currency(self):
        self.assertEqual(renderers._signed_compact(-32.6e9), "-$32.6B")
        self.assertEqual(renderers._signed_compact(5.76e9), "+$5.76B")

    def test_the_currency_follows_the_filing(self):
        self.assertEqual(renderers._compact(1.5e9, "€"), "€1.50B")
        self.assertEqual(renderers._compact(1.5e9, "SEK "), "SEK 1.50B")

    def test_every_tick_down_an_axis_shares_one_scale_and_precision(self):
        # _compact picks precision per value, which prints $140B directly above $80.0B and
        # makes a reader stop to work out whether those are the same kind of number.
        fmt = renderers._compact_axis(0.0, 140e9)
        labels = [fmt(v) for v in (0, 20e9, 80e9, 140e9)]
        self.assertEqual(labels, ["$0", "$20B", "$80B", "$140B"])

    def test_zero_carries_no_scale_suffix(self):
        self.assertEqual(renderers._compact_axis(0.0, 5e9)(0), "$0")


class WrapTests(unittest.TestCase):
    def test_it_folds_to_the_line_limit_rather_than_truncating(self):
        # A label folded past the limit keeps its words on the last line. Dropping them
        # would leave a bar captioned with half a phrase.
        self.assertEqual(renderers._wrap("Tax, interest & other", 9, 2),
                         "Tax,\ninterest & other")
        self.assertEqual(renderers._wrap("Tax, interest & other", 9, 3),
                         "Tax,\ninterest\n& other")

    def test_a_short_label_is_left_alone(self):
        self.assertEqual(renderers._wrap("R&D", 14, 2), "R&D")


class ShotPlanTests(unittest.TestCase):
    """plan_shot's arithmetic — which frames of a planned animation get written out.

    No drawing here: this is the frame maths on its own, and ClipTrimTests below is the
    part that proves those frames are the same pictures the untrimmed take would have
    shown at the same moments.
    """

    def shot(self, fps=60, duration=6.0, hold=1.5, **clip):
        cfg = appmod.clean_config({**BASE, "chart": "line", "tickers": ["NVDA"],
                                   "duration": duration, "hold": hold, **clip})
        ctx = renderers.make_ctx("midnight", "16:9", "final", fps=fps)
        return renderers.plan_shot(cfg, ctx.fps, int(duration * fps), int(hold * fps))

    def test_no_trim_writes_the_whole_clip(self):
        s = self.shot()
        self.assertEqual(s.frames, range(0, 450))
        self.assertFalse(s.trimmed)

    def test_the_points_are_seconds_into_the_reveal_and_hold(self):
        s = self.shot(clip_in=2, clip_out=5)
        self.assertEqual(s.frames, range(120, 300))
        self.assertTrue(s.trimmed)

    def test_the_same_seconds_are_cut_whatever_the_frame_rate(self):
        # The preview draws at the draft tier's 30fps and the render at 60. A trim
        # expressed in frames would put them on different moments of the same clip; this
        # is the same rule the average lag and every camera move already follow.
        for fps in (30, 60):
            with self.subTest(fps=fps):
                s = self.shot(fps=fps, clip_in=2, clip_out=5)
                self.assertEqual((s.frames[0] / fps, (s.frames[-1] + 1) / fps), (2.0, 5.0))

    def test_a_clip_is_never_shorter_than_two_frames(self):
        # FuncAnimation over an empty range writes a file with no frames in it, which fails
        # somewhere much less obvious than here. clean_config's own floor already keeps a
        # posted config well clear of this, so the raw dict is the point: a renderer called
        # directly is a path the module supports, and the guard is what makes it safe.
        ctx = renderers.make_ctx("midnight", "16:9", "draft")
        for clip in ({"clip_in": 0.40, "clip_out": 0.41}, {"clip_in": 99},
                     {"clip_in": 0.5, "clip_out": 0.5}):
            with self.subTest(**clip):
                s = renderers.plan_shot(clip, ctx.fps, 15, 0)   # a 0.5s reveal, no hold
                self.assertGreaterEqual(len(s.frames), 2)
                self.assertLessEqual(s.frames[-1], 14)

    def test_an_untrimmed_scrub_lands_exactly_where_it_always_did(self):
        # Every saved scrub position and every thumbnail already points at this frame, so
        # adding a trim has to leave the mapping alone down to the rounding.
        s = self.shot()
        for t in (0.0, 0.05, 0.25, 0.5, 0.75, 1.0):
            with self.subTest(at=t):
                self.assertEqual(s.at(t), int(s.n_frames * t))

    def test_a_trimmed_scrub_spans_the_frames_the_clip_contains(self):
        s = self.shot(clip_in=2, clip_out=5)
        self.assertEqual(s.at(0.0), s.frames[0])
        self.assertEqual(s.at(1.0), s.frames[-1])

    def test_a_clip_trimmed_into_the_hold_scrubs_the_hold(self):
        # The scrub skips the hold while there is a reveal to spend it on, because every
        # hold frame is the same picture on a locked camera. A clip that starts after the
        # reveal has finished has nothing else to offer.
        s = self.shot(clip_in=6.2, clip_out=7.5)
        self.assertGreaterEqual(s.frames[0], s.n_frames)
        self.assertEqual((s.at(0.0), s.at(1.0)), (s.frames[0], s.frames[-1]))

    def test_warm_replays_from_the_first_frame_up_to_the_in_point(self):
        # The race is the one renderer whose rows accumulate: they ease towards each rank
        # rather than being placed at it, so a trimmed clip drawn cold would open with
        # every row in its first-frame position and snap into order on the second.
        seen = []
        self.shot(clip_in=2, clip_out=5).warm(seen.append)
        self.assertEqual(seen, list(range(0, 120)))

    def test_warm_does_nothing_to_an_untrimmed_clip(self):
        seen = []
        self.shot().warm(seen.append)
        self.assertEqual(seen, [])

    def test_frame_count_agrees_with_the_shot_the_render_will_plan(self):
        # app.py puts this on a job the moment it is queued, before the child has reported
        # a frame. Two spellings of the same arithmetic would show one total on a queued
        # job and a different one a second later.
        for clip in ({}, {"clip_in": 2}, {"clip_in": 2, "clip_out": 5},
                     {"clip_out": 3}):
            for fps in (30, 60):
                with self.subTest(fps=fps, **clip):
                    cfg = appmod.clean_config({**BASE, "chart": "line",
                                               "tickers": ["NVDA"], "duration": 6,
                                               "hold": 1.5, "fps": fps, **clip})
                    self.assertEqual(renderers.frame_count(cfg),
                                     len(self.shot(fps=fps, **clip).frames))

    def test_frame_count_follows_the_trim(self):
        cfg = appmod.clean_config({**BASE, "chart": "line", "tickers": ["NVDA"],
                                   "duration": 6, "hold": 1.5, "fps": 60,
                                   "clip_in": 2, "clip_out": 5})
        self.assertEqual(renderers.frame_count(cfg), 180)


class ClipTrimTests(GeneratedDataCase):
    """That a trim is a slice of the take rather than a re-timing of it.

    This is the property the whole feature rests on: frame k of a trimmed render is the
    same picture as frame `in + k` of the untrimmed one. It is what makes the middle of a
    long pull-back available as a short clip — re-planning the window would just be a
    shorter render, which the reveal and hold fields already do.

    Drawn as stills rather than encoded, so it costs two frames per chart and not two
    clips.
    """

    # save_still draws at the draft tier, which is 30fps, and BASE is a 1.0s reveal with a
    # 0.2s hold — so 30 reveal frames and 6 of hold. Trimming to 0.2..0.8s keeps frames
    # 6..23, and frame 15 is inside both.
    FPS, REVEAL, HOLD = 30, 30, 6
    TRIM = {"clip_in": 0.2, "clip_out": 0.8}
    TARGET = 15

    def shot(self, cfg):
        ctx = renderers.make_ctx("midnight", "16:9", "draft")
        self.assertEqual(ctx.fps, self.FPS)
        return renderers.plan_shot(cfg, ctx.fps, self.REVEAL, self.HOLD)

    def scrub_for(self, shot, frame):
        """The 0..1 scrub position that lands on `frame`.

        Searched rather than solved: at() rounds, and a test that reproduced the rounding
        would pass on a mapping that had drifted from the one the preview actually uses.
        """
        for i in range(1001):
            if shot.at(i / 1000) == frame:
                return i / 1000
        self.fail(f"no scrub position reaches frame {frame} of {shot.frames}")

    def frame_at(self, cfg, frame):
        return draw(cfg, at=self.scrub_for(self.shot(cfg), frame))

    def test_every_chart_draws_a_trimmed_frame_exactly_as_the_full_take_did(self):
        for chart in CHART_FIXTURES:
            with self.subTest(chart=chart):
                full = self.cfg(chart)
                cut = self.cfg(chart, **self.TRIM)
                self.assertEqual(self.shot(cut).frames, range(6, 24))
                np.testing.assert_array_equal(
                    self.frame_at(full, self.TARGET), self.frame_at(cut, self.TARGET),
                    f"{chart}: the trim re-planned the take instead of slicing it")

    def test_a_camera_move_keeps_the_speed_it_was_planned_at(self):
        # The move is the reason to trim at all: the camera is planned against the whole
        # reveal, so the slice shows it partway through rather than restarting it inside
        # the shorter window.
        for move in ("pullback", "follow", "push"):
            with self.subTest(camera=move):
                full = self.cfg("line", camera=move)
                cut = self.cfg("line", camera=move, **self.TRIM)
                np.testing.assert_array_equal(
                    self.frame_at(full, self.TARGET), self.frame_at(cut, self.TARGET),
                    f"{move}: the trim re-planned the camera")

    def test_the_trim_moves_the_frame_it_opens_on(self):
        # The guard on the three tests above: if a trimmed render simply drew the same
        # thing as an untrimmed one, they would pass without proving anything.
        full = self.cfg("line")
        cut = self.cfg("line", **self.TRIM)
        self.assertFalse(np.array_equal(draw(full, at=0.0), draw(cut, at=0.0)))
        self.assertEqual(self.shot(cut).at(0.0), 6)


class FitTests(GeneratedDataCase):
    """Fitting the composition inside an app's safe area.

    The claim is narrow and checkable: with a fit set, nothing the viewer needs to read is
    drawn where that app will put its own chrome. So these tests look at the pixels in the
    four bands and assert they are still plain background — the frame is rendered edge to
    edge either way, and what moves is the composition, not the canvas.
    """

    VERTICAL = {"aspect": "9:16"}

    def bg(self, cfg):
        hexes = renderers.THEMES[cfg.get("theme", "midnight")]["bg"].lstrip("#")
        return np.array([int(hexes[i:i + 2], 16) for i in (0, 2, 4)])

    def ink_in_bands(self, cfg, profile):
        """Fraction of each safe-area band holding something other than the background."""
        img = draw(cfg)[:, :, :3]
        area = renderers.safe_area(profile)
        h, w = img.shape[:2]
        top, bottom = round(area["top"] * h), round(area["bottom"] * h)
        left, right = round(area["left"] * w), round(area["right"] * w)
        bands = {"top": img[:top, :], "bottom": img[h - bottom:, :],
                 "left": img[top:h - bottom, :left],
                 "right": img[top:h - bottom, w - right:]}
        bg = self.bg(cfg)
        # A tolerance rather than equality: the encoder is not involved here, but a glow or
        # an antialiased edge lands a couple of levels off the flat colour.
        return {name: float((np.abs(band - bg).sum(axis=2) > 12).mean())
                for name, band in bands.items() if band.size}

    def test_every_chart_keeps_its_composition_out_of_the_chrome(self):
        for chart in CHART_FIXTURES:
            for profile in ("shorts", "tiktok", renderers.FIT_ALL):
                with self.subTest(chart=chart, fit=profile):
                    cfg = self.cfg(chart, **self.VERTICAL, fit=profile)
                    for band, ink in self.ink_in_bands(cfg, profile).items():
                        self.assertEqual(
                            ink, 0.0,
                            f"{chart} draws into {profile}'s {band} band, which that app "
                            f"covers with its own chrome")

    def test_without_a_fit_the_chrome_really_would_cover_something(self):
        # The guard on the test above. If an unfitted 9:16 render happened to leave those
        # bands empty anyway, fitting would be proving nothing.
        cfg = self.cfg("line", **self.VERTICAL)
        bands = self.ink_in_bands(cfg, "tiktok")
        self.assertGreater(bands["bottom"], 0.05, "the date axis should be in the way")
        self.assertGreater(bands["top"], 0.0, "the title should be in the way")

    def test_the_whole_frame_is_still_painted(self):
        # Fitting moves the composition, never the canvas. A render that letterboxed itself
        # into the safe box would read as a mistake on a phone, where the chrome is drawn
        # over the video rather than beside it.
        cfg = self.cfg("line", **self.VERTICAL, fit="tiktok")
        img = draw(cfg)
        self.assertEqual(img[:, :, 3].min(), 255, "a corner went transparent")
        corner = img[2, 2, :3]
        np.testing.assert_array_equal(corner, self.bg(cfg))

    def test_the_default_composes_against_the_whole_frame(self):
        # ctx.box is the identity when nothing asked for a fit, which is what makes every
        # config written before this existed render exactly as it did.
        ctx = renderers.make_ctx("midnight", "9:16", "draft")
        self.assertEqual(ctx.fit, renderers.FIT_NONE)
        self.assertEqual(ctx.box, (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(ctx.at(0.25, 0.75), (0.25, 0.75))
        self.assertEqual(ctx.rect([0.1, 0.2, 0.3, 0.4]), [0.1, 0.2, 0.3, 0.4])

    def test_a_fitted_box_is_the_frame_less_its_insets(self):
        ctx = renderers.make_ctx("midnight", "9:16", "draft", fit="tiktok")
        area = renderers.SAFE_AREAS["tiktok"]
        x0, y0, w, h = ctx.box
        self.assertAlmostEqual(x0, area["left"])
        self.assertAlmostEqual(y0, area["bottom"])
        self.assertAlmostEqual(w, 1 - area["left"] - area["right"])
        self.assertAlmostEqual(h, 1 - area["top"] - area["bottom"])

    def test_an_unknown_fit_composes_against_the_whole_frame(self):
        # make_ctx is reachable without clean_config — a renderer called directly is a path
        # the module supports — so it falls back rather than raising on a bad name.
        self.assertEqual(renderers.make_ctx("midnight", "9:16", "draft", fit="snap").box,
                         (0.0, 0.0, 1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
