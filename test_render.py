"""Tests for the export path — background handling and the still export.

No network and no ffmpeg: everything here draws stills from demo data, which exercises
the same figure scaffolding a video render uses without paying for an encode.
Run with: python -m unittest
"""

import io
import unittest

import numpy as np

import app as appmod
import data
import renderers
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


class DemoDataCase(unittest.TestCase):
    def setUp(self):
        data.set_demo(True)
        self.addCleanup(data.set_demo, False)
        self.addCleanup(data.reset_sources)

    def cfg(self, chart="line", **kw):
        return appmod.clean_config({**BASE, **CHART_FIXTURES[chart],
                                    "chart": chart, **kw})


class BackgroundTests(DemoDataCase):
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


class ContainerTests(DemoDataCase):
    def test_alpha_renders_land_in_a_mov(self):
        # h264 in an .mp4 has no alpha channel, so the container has to follow the codec.
        self.assertEqual(renderers.output_extension(False), ".mp4")
        self.assertEqual(renderers.output_extension(True), ".mov")

    def test_the_filename_matches_the_container(self):
        self.assertTrue(appmod.slug(self.cfg(transparent=False)).endswith(".mp4"))
        self.assertTrue(appmod.slug(self.cfg(transparent=True)).endswith(".mov"))

    def test_blob_upload_declares_the_right_type(self):
        import storage
        self.assertEqual(storage._blob_mime("a.mp4"), "video/mp4")
        self.assertEqual(storage._blob_mime("a.mov"), "video/quicktime")


class StillExportTests(DemoDataCase):
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


# Charts with a price y-axis. Bars and races are categorical — a log scale there would
# be meaningless, and negative metrics would make it undrawable.
PRICE_CHARTS = ("line", "compare", "candles", "timeline")


class LogScaleTests(DemoDataCase):
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

    def test_log_padding_never_reaches_zero(self):
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        self.addCleanup(plt.close, fig)
        ax.set_yscale("log")
        renderers._ylim(ax, 1.0, 2.0, log=True)
        self.assertGreater(ax.get_ylim()[0], 0)

    def test_log_ticks_cover_the_axis_at_every_span(self):
        # The failure this guards against: a sub-decade range getting ticks at 60-90 and
        # then 100, with the whole 100-192 stretch unlabelled.
        loc = renderers._PriceLogLocator()
        for lo, hi in ((56.5, 192.0), (292.0, 409.0), (20.0, 400.0), (1.0, 1000.0)):
            with self.subTest(span=(lo, hi)):
                inside = [t for t in loc.tick_values(lo, hi) if lo <= t <= hi]
                self.assertGreaterEqual(len(inside), 3,
                                        f"{lo}-{hi} got {len(inside)} ticks")


class MovingAverageTests(DemoDataCase):
    def figure(self, chart, **kw):
        cfg = self.cfg(chart, **kw)
        ctx = renderers.make_ctx("midnight", "16:9", "draft")
        return renderers.CHARTS[chart]["fn"](cfg, ctx, None, still=0.9)

    def ma_artists(self, ax):
        return [ln for ln in ax.get_lines()
                if (ln.get_label() or "").endswith("-day MA")]

    def test_averages_are_drawn_and_keyed(self):
        for chart in ("line", "candles", "timeline"):
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

        def spy(tk, start, end=None):
            asked.append(start)
            return real(tk, start, end)

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
