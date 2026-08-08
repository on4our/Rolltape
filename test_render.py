"""Tests for the export path — background handling, date labelling and the still export.

No network and no ffmpeg: everything here draws stills from demo data, which exercises
the same figure scaffolding a video render uses without paying for an encode.
Run with: python -m unittest
"""

import io
import unittest

import numpy as np
import pandas as pd

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


class WindowTests(DemoDataCase):
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
        self.assertEqual(renderers._range_label(idx), "03 Jun 2024   ·   09:30 – 15:55")
        self.assertEqual(renderers._stamp_fmt(idx), "%d %b %Y  %H:%M")

    def test_a_week_is_labelled_by_the_day(self):
        idx = pd.bdate_range("2024-06-03", "2024-06-07")
        self.assertEqual(renderers._axis_fmt(idx), "%d %b")
        self.assertEqual(renderers._range_label(idx), "03 Jun – 07 Jun 2024")

    def test_a_year_keeps_the_month_and_the_year(self):
        idx = pd.bdate_range("2023-06-01", "2024-06-14")  # both ends are weekdays
        self.assertEqual(renderers._axis_fmt(idx), "%b %Y")
        self.assertEqual(renderers._range_label(idx), "Jun 2023 – Jun 2024")

    def test_a_decade_drops_to_years(self):
        self.assertEqual(renderers._axis_fmt(pd.bdate_range("2014-06-01", "2024-06-01")),
                         "%Y")

    def test_a_short_window_across_new_year_keeps_both_years(self):
        idx = pd.bdate_range("2023-12-20", "2024-01-10")
        self.assertEqual(renderers._range_label(idx), "20 Dec 2023 – 10 Jan 2024")

    def test_an_unknown_window_falls_back_to_what_the_charts_always_did(self):
        self.assertEqual(renderers._axis_fmt(None), "%b %Y")

    def test_volatility_is_annualised_from_the_bar_spacing(self):
        # 252 trading days a year, however many bars each day is cut into. Hardcoding 252
        # understates an intraday volatility by an order of magnitude.
        daily = pd.bdate_range("2024-01-01", "2024-03-01")
        self.assertAlmostEqual(renderers._periods_per_year(daily), 252)
        session = pd.date_range("2024-06-03 09:30", periods=78, freq="5min")
        self.assertAlmostEqual(renderers._periods_per_year(session), 252 * 78)


if __name__ == "__main__":
    unittest.main()
