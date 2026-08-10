"""Tests for the export path — background handling, date labelling and the still export.

No network and no ffmpeg: everything here draws stills from generated prices supplied by
testsupport.py, which exercises the same figure scaffolding a video render uses without
paying for an encode.
Run with: python -m unittest
"""

import io
import unittest

import numpy as np
import pandas as pd

import app as appmod
import data
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
    """Base for the drawing tests: prices come from testsupport, never from a network."""

    def setUp(self):
        testsupport.patch_fetch(self)

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


if __name__ == "__main__":
    unittest.main()
