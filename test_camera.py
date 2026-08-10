"""Tests for the camera — the planned axis limits behind every move.

The camera is planned rather than accumulated, so most of what matters can be checked
without drawing anything: the plan is just four arrays. The drawing tests at the bottom
exist for the things a plan can't tell you, like whether a locked camera still produces
exactly the frame the chart produced before there was a camera at all.
Run with: python -m unittest
"""

import unittest

import numpy as np

import app as appmod
import renderers
from test_render import CHART_FIXTURES, GeneratedDataCase, draw

# The charts that draw on a price/time plane. Everything else composes out of ranked rows,
# so there is nothing for a camera to point at and it never builds one — derived rather
# than listed, so a chart added to the registry lands in one list or the other.
CAMERA_CHARTS = ("line", "compare", "candles", "timeline")
FLAT_CHARTS = tuple(c for c in CHART_FIXTURES if c not in CAMERA_CHARTS)
MOVES = tuple(renderers.CAMERAS)
MOVING = tuple(m for m in MOVES if m != "locked")


def plan(move="pullback", travel="standard", camera_y="track", fps=30,
         n_frames=60, hold=15, y=None):
    """Build a camera over a synthetic reveal.

    The head runs linearly from the first sample to the last and then stops for the hold,
    which is the shape every renderer feeds it — the easing lives in the frame-to-index
    mapping upstream, not in anything the camera does with it. It is placed on elapsed
    reveal rather than on `linspace`, so frame i at 30fps and frame 2i at 60fps describe
    the same instant and the frame-rate test is comparing like with like.
    """
    x = np.linspace(0.0, 100.0, 400)
    if y is None:
        # A ramp with a late spike: something for a tracking camera to have to react to.
        y = np.linspace(10.0, 20.0, 400)
        y[330:360] += np.linspace(0, 14, 30)
        y[360:] += 14
    elapsed = np.arange(n_frames) / float(n_frames)
    head = np.concatenate([x[0] + (x[-1] - x[0]) * elapsed, np.full(hold, x[-1])])
    ctx = renderers.make_ctx("midnight", "16:9", "draft", fps=fps)
    cfg = {"camera": move, "camera_travel": travel, "camera_y": camera_y}
    # Padded the way every renderer pads its resting frame. Handing the camera a window
    # that sits exactly on the data instead would leave it nowhere to put the margin it
    # keeps under a tracked extreme, and none of the resting comparisons would hold.
    pad = (y.max() - y.min()) * 0.12
    cam = renderers.Camera(cfg, ctx, x=x, lo=y, hi=y, head=head,
                           n_frames=n_frames, hold_frames=hold,
                           rest_y=(y.min() - pad, y.max() + pad))
    return cam, x, y, head


class PlanTests(unittest.TestCase):
    def test_a_locked_camera_hands_back_the_resting_limits(self):
        # This is the whole reason locked can stay the default: an existing config has to
        # render exactly as it did before the camera existed.
        x = np.linspace(0.0, 100.0, 400)
        ctx = renderers.make_ctx("midnight", "16:9", "draft", fps=30)
        cam = renderers.Camera({"camera": "locked"}, ctx, x=x, lo=x, hi=x,
                               head=np.full(40, 100.0), n_frames=30, hold_frames=10,
                               extent=(-1.0, 101.0), rest_y=(5.0, 55.0))
        self.assertTrue(np.all(cam.x0 == -1.0))
        self.assertTrue(np.all(cam.x1 == 101.0))
        self.assertTrue(np.all(cam.y0 == 5.0))
        self.assertTrue(np.all(cam.y1 == 55.0))
        self.assertFalse(cam.moving)

    def test_travel_and_vertical_cannot_leak_into_a_locked_camera(self):
        ref, _, _, _ = plan(move="locked")
        for travel in renderers.TRAVELS:
            for mode in renderers.CAMERA_Y:
                with self.subTest(travel=travel, camera_y=mode):
                    cam, _, _, _ = plan(move="locked", travel=travel, camera_y=mode)
                    for a, b in ((cam.x0, ref.x0), (cam.x1, ref.x1),
                                 (cam.y0, ref.y0), (cam.y1, ref.y1)):
                        self.assertTrue(np.array_equal(a, b))

    def test_the_head_is_never_out_of_shot(self):
        # Losing the point the reveal is drawing reads as a bug rather than as a camera,
        # so it holds for every move at every travel.
        for move in MOVING:
            for travel in renderers.TRAVELS:
                with self.subTest(move=move, travel=travel):
                    cam, _, _, head = plan(move=move, travel=travel)
                    self.assertTrue(np.all(cam.x0 <= head),
                                    "camera left the head behind")
                    self.assertTrue(np.all(cam.x1 >= head),
                                    "camera ran ahead of the head")

    def test_no_move_frames_wider_than_the_range_it_was_given(self):
        # Beyond the extent there is nothing drawn, so a window wider than the data is
        # empty margin the viewer paid for in legibility.
        for move in MOVING:
            with self.subTest(move=move):
                cam, x, _, _ = plan(move=move)
                span = x[-1] - x[0]
                self.assertLessEqual((cam.x1 - cam.x0).max(), span * 1.05)

    def test_a_pull_back_opens_tight_and_ends_on_the_whole_range(self):
        cam, x, _, _ = plan(move="pullback", travel="bold")
        span = x[-1] - x[0]
        self.assertLess(cam.width(0), span * 0.2, "pull back did not open tight")
        self.assertAlmostEqual(cam.x0[-1], x[0], places=6)
        self.assertAlmostEqual(cam.x1[-1], x[-1], places=6)

    def test_a_follow_settles_back_to_the_whole_range_on_the_hold(self):
        # A replay that ends on a close-up never shows the shape of what it replayed.
        cam, x, _, _ = plan(move="follow", n_frames=60, hold=30)
        self.assertLess(cam.width(59), (x[-1] - x[0]) * 0.6, "follow was not tight")
        self.assertAlmostEqual(cam.x0[-1], x[0], places=6)
        self.assertAlmostEqual(cam.x1[-1], x[-1], places=6)

    def test_a_follow_with_no_hold_has_nowhere_to_settle(self):
        cam, x, _, _ = plan(move="follow", n_frames=60, hold=0)
        self.assertLess(cam.width(cam.frames - 1), (x[-1] - x[0]) * 0.6)

    def test_a_push_lands_as_the_reveal_ends_and_then_stops(self):
        cam, _, _, _ = plan(move="push", n_frames=60, hold=20)
        self.assertAlmostEqual(cam.width(59), cam.width(cam.frames - 1), places=6)
        self.assertLess(cam.width(59), cam.width(0), "push never moved in")

    def test_no_move_jumps(self):
        # The one fault a still can't show and a viewer can't miss. Everything here moves
        # a couple of percent of the frame per frame at most; a discontinuity — a settle
        # starting from the wrong place, a clamp biting mid-move — lands near 1.0.
        for move in MOVING:
            for mode in renderers.CAMERA_Y:
                with self.subTest(move=move, camera_y=mode):
                    cam, _, _, _ = plan(move=move, camera_y=mode, fps=30,
                                        n_frames=90, hold=30)
                    height = cam.y1 - cam.y0
                    step = np.abs(np.diff(cam.x0)) / (cam.x1 - cam.x0)[:-1]
                    jerk = np.abs(np.diff(cam.y0, 2)) / height[:-2]
                    self.assertLess(step.max(), 0.25, "the frame jumped sideways")
                    self.assertLess(jerk.max(), 0.02, "the frame snapped vertically")

    def test_a_move_that_reaches_the_whole_range_is_framed_like_a_locked_one(self):
        # Pull back ends there and follow settles back to it, and "there" has to mean the
        # composition the chart would have had on its own — right down to the vertical,
        # which is what the frame's own travel is used to fade out.
        rest = plan(move="locked", hold=40)[0]
        for move in ("pullback", "follow"):
            with self.subTest(move=move):
                cam = plan(move=move, camera_y="track", hold=40)[0]
                for arr in ("x0", "x1", "y0", "y1"):
                    self.assertAlmostEqual(getattr(cam, arr)[-1],
                                           getattr(rest, arr)[-1], places=6)

    def test_bolder_travel_gets_closer(self):
        for move in MOVING:
            with self.subTest(move=move):
                widths = [min(plan(move=move, travel=t)[0].x1
                              - plan(move=move, travel=t)[0].x0)
                          for t in ("subtle", "standard", "bold")]
                self.assertGreater(widths[0], widths[1])
                self.assertGreater(widths[1], widths[2])


class TrackingTests(unittest.TestCase):
    def test_a_tracked_frame_never_clips_what_has_been_drawn(self):
        # The y track is smoothed, and smoothing that lags means a new high sitting
        # outside the frame. Every sample on screen has to be inside the window.
        for move in MOVING:
            with self.subTest(move=move):
                cam, x, y, head = plan(move=move, camera_y="track")
                for i in range(cam.frames):
                    shown = y[(x >= cam.x0[i]) & (x <= min(cam.x1[i], head[i]))]
                    if not len(shown):
                        continue
                    self.assertGreaterEqual(shown.min(), cam.y0[i])
                    self.assertLessEqual(shown.max(), cam.y1[i])

    def test_holding_the_scale_keeps_one_window_for_the_whole_clip(self):
        cam, _, y, _ = plan(move="follow", camera_y="hold")
        self.assertEqual(len(set(cam.y0)), 1)
        self.assertEqual(len(set(cam.y1)), 1)
        self.assertLessEqual(cam.y0[0], y.min())
        self.assertGreaterEqual(cam.y1[0], y.max())

    def test_a_tracked_frame_will_not_magnify_a_quiet_stretch_into_noise(self):
        # A flat window blown up to fill the frame is a lie about how much happened.
        flat = np.linspace(10.0, 20.0, 400)
        flat[:120] = 10.0  # dead calm at the open, then a normal ramp
        cam, _, _, _ = plan(move="follow", travel="bold", camera_y="track", y=flat)
        resting = cam.y1.max() - cam.y0.min()
        self.assertGreater(cam.height(4), resting * 0.15)

    def test_a_tracked_frame_still_moves(self):
        cam, _, _, _ = plan(move="follow", camera_y="track")
        self.assertGreater(cam.height(cam.frames - 1) / cam.height(2), 1.2,
                           "tracking produced a window that never changed")

    def test_a_second_axis_tracks_the_same_windows(self):
        # The volume strip under the candles: same windows, but pinned to its baseline.
        cam, _, _, _ = plan(move="follow", camera_y="track")
        vol = np.abs(np.sin(np.linspace(0, 9, 400))) * 1e6
        bot, top, peak = cam.track(np.zeros(400), vol, rest=(0.0, vol.max() * 1.15),
                                   floor=0.0)
        self.assertTrue(np.all(bot == 0.0), "volume left its baseline")
        self.assertTrue(np.all(top >= peak), "a bar grew out of the top of the strip")
        self.assertLess(peak.min(), vol.max(), "the peak never followed the window")

    def test_a_held_second_axis_keeps_the_tick_on_the_real_maximum(self):
        cam, _, _, _ = plan(move="locked")
        vol = np.abs(np.sin(np.linspace(0, 9, 400))) * 1e6
        _, _, peak = cam.track(np.zeros(400), vol, rest=(0.0, vol.max() * 1.15),
                               floor=0.0)
        self.assertTrue(np.all(peak == vol.max()))


class DeterminismTests(unittest.TestCase):
    def test_a_frame_does_not_depend_on_the_frames_before_it(self):
        # This is what lets the still export tell the truth: `save_still` asks for one
        # frame without drawing the ones ahead of it, so frame 40 has to mean the same
        # thing whether it was reached in order or asked for on its own.
        cam, _, _, _ = plan(move="follow")
        order = [40, 3, 71, 40, 0, 71]
        seen = {}
        for i in order:
            got = (cam.x0[i], cam.x1[i], cam.y0[i], cam.y1[i])
            self.assertEqual(seen.setdefault(i, got), got)

    def test_the_move_runs_on_the_clock_not_on_the_frame_rate(self):
        # 60fps is twice the frames, not twice the move: the same second of the clip has
        # to be framed the same way at either rate.
        for move in MOVING:
            with self.subTest(move=move):
                slow, x, _, _ = plan(move=move, fps=30, n_frames=60, hold=15)
                fast, _, _, _ = plan(move=move, fps=60, n_frames=120, hold=30)
                span = x[-1] - x[0]
                for i in range(0, 60, 5):
                    self.assertAlmostEqual(slow.x0[i], fast.x0[i * 2], places=6)
                    self.assertAlmostEqual(slow.x1[i], fast.x1[i * 2], places=6)
                    # The y track is a filter over time rather than a position on a
                    # timeline, so it lands close rather than exactly. A camera counting
                    # frames instead of seconds would settle at half the speed here, which
                    # is nothing like a hundredth of the frame apart.
                    self.assertLess(abs(slow.y1[i] - fast.y1[i * 2]), span * 0.01)


class ConfigTests(unittest.TestCase):
    def base(self, **kw):
        return appmod.clean_config({"chart": "line", "tickers": ["NVDA"], **kw})

    def test_the_camera_defaults_to_locked(self):
        cfg = self.base()
        self.assertEqual(cfg["camera"], "locked")
        self.assertEqual(cfg["camera_travel"], "standard")
        self.assertEqual(cfg["camera_y"], "track")

    def test_a_move_that_does_not_exist_is_rejected_by_name(self):
        for field, value in (("camera", "whip-pan"), ("camera_travel", "enormous"),
                             ("camera_y", "sideways")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as caught:
                    self.base(**{field: value})
                self.assertIn("must be one of", str(caught.exception))

    def test_a_move_survives_the_shape_the_browser_sends_it_in(self):
        self.assertEqual(self.base(camera="  Follow ")["camera"], "follow")


class DrawTests(GeneratedDataCase):
    """The parts a plan can't answer — what actually lands on the frame."""

    def test_every_camera_chart_draws_with_every_move(self):
        for chart in CAMERA_CHARTS:
            for move in MOVES:
                with self.subTest(chart=chart, move=move):
                    img = draw(self.cfg(chart, camera=move, camera_travel="bold"))
                    self.assertGreater(img[:, :, :3].max(), 0, "drew nothing at all")

    def test_a_locked_render_ignores_the_rest_of_the_camera_settings(self):
        # The pixel-level version of the plan test above: whatever else is set, a locked
        # camera has to produce the frame the chart produced before cameras existed.
        for chart in CAMERA_CHARTS:
            with self.subTest(chart=chart):
                base = draw(self.cfg(chart))
                for travel, mode in (("subtle", "hold"), ("bold", "track")):
                    other = draw(self.cfg(chart, camera="locked",
                                          camera_travel=travel, camera_y=mode))
                    self.assertTrue(np.array_equal(base, other),
                                    f"locked frame moved with {travel}/{mode}")

    def test_a_move_actually_changes_the_frame(self):
        for chart in CAMERA_CHARTS:
            with self.subTest(chart=chart):
                locked = draw(self.cfg(chart), at=0.35)
                moved = draw(self.cfg(chart, camera="pullback", camera_travel="bold"),
                             at=0.35)
                self.assertFalse(np.array_equal(locked, moved))

    def test_a_chart_without_a_plane_is_left_alone_by_a_camera(self):
        # Nothing to move over, so the setting has to be inert rather than half-applied —
        # the UI hides it, but an API caller can still send it.
        for chart in FLAT_CHARTS:
            with self.subTest(chart=chart):
                base = draw(self.cfg(chart))
                moved = draw(self.cfg(chart, camera="follow", camera_travel="bold"))
                self.assertTrue(np.array_equal(base, moved))

    def test_a_moving_camera_still_honours_a_transparent_background(self):
        # Same class of bug as reaching for theme["bg"]: the camera adds per-frame
        # drawing, and anything behind the chart still has to disappear on an overlay.
        for chart in CAMERA_CHARTS:
            with self.subTest(chart=chart):
                img = draw(self.cfg(chart, camera="follow", transparent=True))
                self.assertEqual(img[3, 3, 3], 0, "a moving camera painted a backdrop")
                self.assertEqual(img[:, :, 3].max(), 255, "drew nothing at all")


if __name__ == "__main__":
    unittest.main()
