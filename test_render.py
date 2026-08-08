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


if __name__ == "__main__":
    unittest.main()
