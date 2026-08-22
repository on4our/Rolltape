"""Tests for the motion styles — the reveal's easing curves and the entrance around them.

Two halves, and they fail for different reasons. The curves are arithmetic and are checked
as arithmetic: what matters is that every one of them leaves 0 at 0 and lands on 1 at 1,
because that is what lets `_plan` treat an eased value as an index into real data and what
lets a spring overshoot a bar without changing where the bar ends up.

The entrance is checked by drawing. Most of it comes down to one property — a still drawn
cold at a late frame has to show the frame fully composed. A stage that accumulated its
alphas frame by frame would still be at nothing there, because `save_still` draws that one
frame and no other, so "the entrance is planned" and "a late still looks like a still with
no entrance at all" are the same assertion. That is deliberately the shape of most of what
follows.

No network, no ffmpeg: prices and statements come from testsupport through the shared
GeneratedDataCase, and every test here draws stills rather than encoding a clip.
Run with: python -m unittest
"""

import unittest

import numpy as np

import app as appmod
import renderers
from test_render import CHART_FIXTURES, GeneratedDataCase, draw

STYLES = tuple(renderers.MOTIONS)
MOVING = tuple(s for s in STYLES if s != renderers.MOTION_NONE)


class EasingCurveTests(unittest.TestCase):
    """That every curve starts at 0 and finishes at 1, exactly.

    Not a tidiness check. `_plan` turns an eased value straight into an index into the
    price series, so a curve finishing at 0.999 would stop the reveal a bar short of the
    last close and a curve finishing above 1 would ask for a price that does not exist.
    The spring is the one that makes this load-bearing: it is allowed to go past 1 in the
    middle precisely because it is pinned to 1 at the end, which is what keeps a settling
    bar landing on the number it is supposed to report.
    """

    def test_every_easing_spans_exactly_zero_to_one(self):
        for name in renderers.EASINGS:
            with self.subTest(easing=name):
                self.assertEqual(float(renderers.ease(name, 0.0)), 0.0)
                self.assertEqual(float(renderers.ease(name, 1.0)), 1.0)

    def test_every_easing_is_monotone(self):
        # The reveal cannot un-draw itself. A curve that dipped would walk the head
        # backwards along the series, which reads as a glitch rather than as a flourish —
        # overshoot belongs to the settle, and the settle is not this function.
        t = np.linspace(0.0, 1.0, 1001)
        for name in renderers.EASINGS:
            with self.subTest(easing=name):
                self.assertTrue(np.all(np.diff(renderers.ease(name, t)) >= 0.0),
                                f"{name} goes backwards somewhere")

    # Derivatives by central difference on a dense sample. np.gradient's one-sided
    # estimate at the array edges is worth about two decimal places here, which is not
    # enough to tell a curve that really starts from rest from one that nearly does.
    @staticmethod
    def _derivatives(name, n=20001):
        t = np.linspace(0.0, 1.0, n)
        y = np.asarray(renderers.ease(name, t), float)
        h = float(t[1] - t[0])
        return (y[2:] - y[:-2]) / (2 * h), (y[2:] - 2 * y[1:-1] + y[:-2]) / h ** 2

    def test_smooth_starts_from_rest_where_the_default_does_not(self):
        # The kick this curve exists to remove: "out" leaves the very first frame at full
        # speed, which is what makes a reveal look like it was already running.
        smooth, _ = self._derivatives("smooth")
        out, _ = self._derivatives("out")
        self.assertGreater(out[0], 2.5, "ease-out should open at speed")
        self.assertLess(smooth[0], out[0] / 100.0, "smooth still opens with a kick")
        self.assertLess(smooth[-1], out[0] / 100.0, "smooth still closes with a snap")

    def test_smooth_has_none_of_the_snap_inout_carries_through_its_join(self):
        # `inout` is two cubics meeting at the halfway point and its acceleration flips
        # sign across that join — a jump in acceleration is exactly what the eye reads as
        # mechanical. Smootherstep is one polynomial and has no join to flip across.
        for name, jumpy in (("inout", True), ("smooth", False)):
            with self.subTest(easing=name):
                _, accel = self._derivatives(name)
                mid = len(accel) // 2
                jump = abs(float(accel[mid + 2] - accel[mid - 2]))
                scale = float(np.abs(accel).max())
                self.assertEqual(jump > scale, jumpy,
                                 f"{name}: acceleration {'should' if jumpy else 'should not'}"
                                 " jump at the midpoint")

    def test_the_spring_overshoots_once_and_lands_on_its_target(self):
        curve = renderers._spring(np.linspace(0.0, 1.0, 2001))
        self.assertEqual(float(renderers._spring(0.0)), 0.0)
        # Exactly, not nearly: a bar that settled onto 0.9999 of its value would print a
        # figure a hair under the one the filing reports.
        self.assertEqual(float(renderers._spring(1.0)), 1.0)
        self.assertGreater(curve.max(), 1.0, "a spring that never passes its mark")
        self.assertLess(curve.max(), 1.25, "the overshoot has become a bounce")
        # One overshoot, not a ring: past the peak it comes back and stays back.
        peak = int(np.argmax(curve))
        self.assertTrue(np.all(np.diff(curve[peak:]) <= 0.0), "the spring rings")

    def test_the_published_peak_is_measured_off_the_curve(self):
        # Charts leave room for the overshoot by asking SPRING_PEAK, so a decay change has
        # to move that number with it rather than silently clipping a bar.
        self.assertAlmostEqual(renderers.SPRING_PEAK,
                               float(renderers._spring(np.linspace(0, 1, 20001)).max()),
                               places=4)


class SettleRoomTests(unittest.TestCase):
    """That the overshoot is scaled to the frame rather than the frame opened up for it.

    The alternative was extra headroom under kinetic, and it was the wrong one: it would
    mean a bridge composed differently from its own thumbnail depending on which style drew
    it. Softening the spring instead keeps every chart framed exactly as it always was.
    """

    def test_a_bar_with_room_to_spare_keeps_the_whole_overshoot(self):
        self.assertEqual(renderers.settle_room(0.0, 100.0, [0.0], [50.0]), 1.0)

    def test_a_bar_that_fills_the_frame_gives_the_overshoot_up(self):
        # The waterfall's opening pillar: as tall as the y range by construction, so there
        # is nowhere above it to overshoot into.
        self.assertEqual(renderers.settle_room(0.0, 100.0, [0.0], [100.0]), 0.0)

    def test_the_tightest_bar_decides_for_all_of_them(self):
        room = renderers.settle_room(0.0, 100.0, [0.0, 0.0], [10.0, 100.0])
        self.assertEqual(room, 0.0, "one boxed-in bar has to damp the whole chart")

    def test_a_bar_growing_downward_is_measured_against_the_floor(self):
        self.assertEqual(renderers.settle_room(-100.0, 100.0, [0.0], [-100.0]), 0.0)
        self.assertEqual(renderers.settle_room(-100.0, 100.0, [0.0], [-10.0]), 1.0)

    def test_a_damped_settle_still_starts_and_ends_where_it_should(self):
        # Mixed back towards the plain easing rather than truncated, so both ends are
        # untouched however little room there was.
        cfg = {"motion": "kinetic", "easing": "out"}
        stage = renderers.Stage(cfg, renderers.make_ctx("midnight", "16:9", "draft"),
                                30, 6)
        for room in (0.0, 0.25, 0.6, 1.0):
            with self.subTest(room=room):
                self.assertEqual(stage.settle(0.0, room), 0.0)
                self.assertEqual(stage.settle(1.0, room), 1.0)
        peaks = [max(stage.settle(t, room) for t in np.linspace(0, 1, 401))
                 for room in (0.0, 0.5, 1.0)]
        self.assertEqual(peaks[0], 1.0, "no room should mean no overshoot at all")
        self.assertLess(peaks[1], peaks[2])
        self.assertGreater(peaks[1], 1.0)


class BreakoutTests(unittest.TestCase):
    """That the reveal head reacts to an event rather than to every bar.

    The naive reading of "new high" fires on every bar of a rising series, which is a
    permanently larger marker rather than a reaction. What reads as a beat is the record
    that had to be won back.
    """

    def test_a_series_that_only_rises_has_no_breakouts(self):
        self.assertEqual(renderers._breakouts(np.linspace(100.0, 200.0, 500)), [])

    def test_a_high_taken_back_after_a_drawdown_is_a_breakout(self):
        v = np.concatenate([np.linspace(100, 140, 50), np.linspace(140, 110, 50),
                            np.linspace(110, 160, 50)])
        hits = renderers._breakouts(v)
        self.assertEqual(len(hits), 1, "one recovery, one beat")
        self.assertAlmostEqual(float(v[hits[0]]), 140.0, delta=2.0,
                               msg="the beat should land where the old high was retaken")

    def test_a_flat_series_never_fires(self):
        self.assertEqual(renderers._breakouts(np.full(200, 42.0)), [])

    def test_a_series_that_crosses_zero_is_measured_on_its_range(self):
        # An economic series can sit at or below zero, where a percentage of the level is
        # not a distance. Nothing here should divide by the level.
        v = np.concatenate([np.linspace(-5, 5, 50), np.linspace(5, -5, 50),
                            np.linspace(-5, 8, 50)])
        self.assertEqual(len(renderers._breakouts(v)), 1)

    def test_the_kick_fades_over_the_same_time_at_any_frame_rate(self):
        # Frame-rate independence, the same rule the camera and the average lag follow: the
        # preview draws at 30fps and the render at 60, and both have to show the same beat.
        v = np.concatenate([np.linspace(100, 140, 50), np.linspace(140, 110, 50),
                            np.linspace(110, 160, 50)])
        levels = {}
        for fps in (30, 60):
            cut = np.clip((np.linspace(0, 1, 4 * fps) * (len(v) - 1)).astype(int),
                          1, len(v) - 1)
            track = renderers.pulse_track(v, cut, 5 * fps, fps)
            start = int(np.argmax(track >= 1.0))
            levels[fps] = round(float(track[start + int(0.3 * fps)]), 6)
        self.assertEqual(levels[30], levels[60],
                         "the kick decays per frame instead of per second")

    def test_the_kick_starts_at_full_strength_and_returns_to_nothing(self):
        v = np.concatenate([np.linspace(100, 140, 50), np.linspace(140, 110, 50),
                            np.linspace(110, 160, 50)])
        cut = np.clip((np.linspace(0, 1, 90) * (len(v) - 1)).astype(int), 1, len(v) - 1)
        track = renderers.pulse_track(v, cut, 150, 30)
        self.assertEqual(float(track.max()), 1.0)
        self.assertLess(float(track[-1]), 0.05, "the head never settles back")

    def test_a_series_with_no_events_leaves_the_head_alone(self):
        v = np.linspace(100.0, 200.0, 500)
        cut = np.clip((np.linspace(0, 1, 90) * (len(v) - 1)).astype(int), 1, len(v) - 1)
        np.testing.assert_array_equal(renderers.pulse_track(v, cut, 150, 30),
                                      np.zeros(150))


class StagePlanTests(unittest.TestCase):
    """That the entrance is planned in seconds and finishes inside the reveal."""

    def stage(self, style="rise", fps=30, n_frames=120, hold=30, **cfg):
        ctx = renderers.make_ctx("midnight", "16:9", "draft", fps=fps)
        return renderers.Stage({"motion": style, **cfg}, ctx, n_frames, hold)

    def test_off_plans_nothing_and_reads_nothing(self):
        stage = self.stage(renderers.MOTION_NONE)
        self.assertFalse(stage.on)
        self.assertEqual(stage._track, {})
        # Asking an axes for its ticks builds them, which would drag the locator forward to
        # a point where a chart may not have set its limits yet. A stage that will never
        # move must not ask, and this is what pins that.
        self.assertEqual(stage._axes, [])

    def test_an_unknown_style_falls_back_to_off(self):
        # clean_config refuses one, so reaching here means a renderer called directly — and
        # the safe answer is the frame every chart drew before there was a stage.
        self.assertFalse(self.stage("swoosh").on)

    def test_the_cascade_takes_the_same_time_at_any_frame_rate(self):
        # A 6s reveal is long enough that nothing is squeezed, so the entrance should end
        # on the same second at both rates.
        seconds = {fps: self.stage(fps=fps, n_frames=6 * fps, hold=fps)._settled / fps
                   for fps in (30, 60)}
        self.assertAlmostEqual(seconds[30], seconds[60], places=1)

    def test_the_layers_arrive_in_order(self):
        stage = self.stage(n_frames=300, hold=60)
        arrives = {name: int(np.argmax(track[0] > 0.0))
                   for name, track in stage._track.items()}
        self.assertLess(arrives["frame"], arrives["title"])
        self.assertLess(arrives["title"], arrives["subtitle"])
        self.assertLess(arrives["subtitle"], arrives["footer"])

    def test_a_short_reveal_squeezes_the_cascade_rather_than_losing_it(self):
        # An entrance sized for a comfortable clip is most of a two-second one. Squeezed
        # rather than truncated, so the last layer still lands and the order still reads.
        stage = self.stage(n_frames=30, hold=6)
        self.assertLessEqual(stage._settled, 30 * renderers.STAGE_MAX + 1)
        for name, track in stage._track.items():
            with self.subTest(layer=name):
                self.assertEqual(float(track[0][-1]), 1.0, f"{name} never arrives")

    def test_the_entrance_is_over_before_the_reveal_is(self):
        for n_frames in (30, 60, 180, 600):
            with self.subTest(reveal=n_frames):
                self.assertLess(self.stage(n_frames=n_frames, hold=0)._settled, n_frames)

    def test_only_kinetic_reaches_past_the_entrance(self):
        for style in STYLES:
            with self.subTest(style=style):
                self.assertEqual(self.stage(style).reactive, style == "kinetic")

    def test_a_settle_without_kinetic_is_the_reveals_own_easing(self):
        # The three quieter styles must not change a single value on a bar chart — they are
        # an entrance and nothing else.
        for style in (renderers.MOTION_NONE, "fade", "rise"):
            stage = self.stage(style, easing="inout")
            for t in np.linspace(0.0, 1.0, 21):
                with self.subTest(style=style, t=round(float(t), 2)):
                    self.assertEqual(stage.settle(t), float(renderers.ease("inout", t)))


class StageDrawingTests(GeneratedDataCase):
    """What the entrance does to a real frame, drawn rather than reasoned about."""

    def cfgs(self, chart, **kw):
        """The same chart with the entrance off and on, for a before-and-after."""
        return self.cfg(chart, **kw), self.cfg(chart, motion="rise", **kw)

    def test_a_late_still_is_the_frame_the_entrance_never_touched(self):
        """The one that proves the whole thing is planned.

        `save_still` draws exactly one frame and no other, so an entrance that accumulated
        its alphas would still be at nothing here. Landing on the untouched frame means the
        stage worked out where it should be at that index without drawing its way there —
        and it also means a thumbnail is the same picture whichever style rendered it.
        """
        for chart in CHART_FIXTURES:
            for style in ("fade", "rise"):
                with self.subTest(chart=chart, style=style):
                    np.testing.assert_array_equal(
                        draw(self.cfg(chart), at=1.0),
                        draw(self.cfg(chart, motion=style), at=1.0),
                        f"{chart}/{style}: the entrance never let go")

    def test_kinetic_leaves_the_value_charts_exactly_where_it_found_them(self):
        # The springs land on the filed number, and `settle_room` keeps them inside the
        # frame on the way — so the last frame of a kinetic bridge is the still one.
        for chart in ("bars", "waterfall", "compare", "candles", "race"):
            with self.subTest(chart=chart):
                np.testing.assert_array_equal(
                    draw(self.cfg(chart), at=1.0),
                    draw(self.cfg(chart, motion="kinetic"), at=1.0),
                    f"{chart}: kinetic moved the composition")

    def test_the_entrance_is_visible_while_it_is_running(self):
        # The other half of the test above: something has to actually happen, or an
        # entrance that quietly did nothing would pass every assertion here.
        for chart in CHART_FIXTURES:
            for style in MOVING:
                with self.subTest(chart=chart, style=style):
                    plain, moved = draw(self.cfg(chart), at=0.12), \
                        draw(self.cfg(chart, motion=style), at=0.12)
                    self.assertTrue(np.any(plain != moved),
                                    f"{chart}/{style}: nothing came on")

    def test_off_is_the_render_that_was_already_there(self):
        for chart in CHART_FIXTURES:
            with self.subTest(chart=chart):
                np.testing.assert_array_equal(
                    draw(self.cfg(chart), at=0.3),
                    draw(self.cfg(chart, motion=renderers.MOTION_NONE), at=0.3),
                    f"{chart}: the default stopped being the old default")

    def test_the_reactive_head_only_grows(self):
        # It is a reaction on top of the marker, never a replacement for it: whatever the
        # data does, the head is never smaller than the one a still render draws.
        cfg = self.cfg("line", motion="kinetic")
        base = self.cfg("line")
        for at in np.linspace(0.0, 1.0, 21):
            with self.subTest(at=round(float(at), 2)):
                self.assertGreaterEqual(self.head_size(cfg, at),
                                        self.head_size(base, at))

    def head_size(self, cfg, at):
        import matplotlib.pyplot as plt
        ctx = renderers.make_ctx(cfg["theme"], cfg["aspect"], "draft")
        fig = renderers.CHARTS[cfg["chart"]]["fn"](cfg, ctx, None, still=float(at))
        try:
            return [ln for ln in fig.axes[0].lines if ln.get_marker() == "o"][0] \
                .get_markersize()
        finally:
            plt.close(fig)

    def test_an_entrance_paints_no_backdrop_into_a_transparent_export(self):
        # Alpha is the entrance's whole mechanism, so it is exactly the feature that could
        # put an opaque plate behind an overlay without anyone noticing until the clip is
        # on a timeline.
        for style in MOVING:
            with self.subTest(style=style):
                frame = draw(self.cfg("line", motion=style, transparent=True), at=0.1)
                self.assertEqual(frame[3, 3, 3], 0,
                                 f"{style}: a transparent export grew a corner")


class HonestFiguresTests(GeneratedDataCase):
    """That a bar may overshoot its level and a number may never overshoot its value.

    This is the one place the spring could have introduced the failure the source rules in
    data.py exist to prevent: a figure that reads high for a few frames and then corrects
    itself is wrong in the direction nobody checks, on a chart whose entire claim is that
    the number on screen matches the filing.
    """

    # Bar j and the figure printed beside it, per chart. Both renderers create the two in
    # one loop, so the orders line up — the waterfall interleaves a second text per bar for
    # the share line, which is why it steps by two.
    READERS = {
        "bars": (lambda ax, j: ax.patches[j].get_width(),
                 lambda ax, j: ax.texts[j].get_text()),
        "waterfall": (lambda ax, j: ax.patches[j].get_height(),
                      lambda ax, j: ax.texts[2 * j].get_text()),
    }

    def bars_at(self, cfg, at):
        """(reach, label) per bar at one scrub position."""
        import matplotlib.pyplot as plt
        reach, label = self.READERS[cfg["chart"]]
        ctx = renderers.make_ctx(cfg["theme"], cfg["aspect"], "draft")
        fig = renderers.CHARTS[cfg["chart"]]["fn"](cfg, ctx, None, still=float(at))
        try:
            ax = fig.axes[0]
            return [(reach(ax, j), label(ax, j)) for j in range(len(ax.patches))]
        finally:
            plt.close(fig)

    def test_a_springing_bar_prints_the_number_it_will_settle_on(self):
        """Per bar, because the bars are staggered.

        At the frame bar 0 is furthest past its level, bar 2 has barely started — so this
        has to ask each bar about its own peak rather than compare two whole charts.
        """
        for chart in ("bars", "waterfall"):
            with self.subTest(chart=chart):
                cfg = self.cfg(chart, motion="kinetic")
                scrubs = np.linspace(0.02, 1.0, 60)
                frames = [self.bars_at(cfg, a) for a in scrubs]
                final = frames[-1]
                sprang = 0
                for j, (end_reach, end_label) in enumerate(final):
                    peak = max(range(len(frames)), key=lambda i: abs(frames[i][j][0]))
                    if abs(frames[peak][j][0]) > abs(end_reach) * (1 + 1e-9):
                        sprang += 1
                        # Past its level, and already printing the number it comes back to.
                        self.assertEqual(
                            frames[peak][j][1], end_label,
                            f"{chart} bar {j}: the figure overshot along with the bar")
                self.assertGreater(sprang, 0,
                                   f"{chart}: nothing overshot, so nothing was tested")

    def test_a_springing_bar_stays_inside_the_frame(self):
        import matplotlib.pyplot as plt
        for chart in ("bars", "waterfall"):
            with self.subTest(chart=chart):
                cfg = self.cfg(chart, motion="kinetic")
                for at in np.linspace(0.05, 1.0, 40):
                    ctx = renderers.make_ctx(cfg["theme"], cfg["aspect"], "draft")
                    fig = renderers.CHARTS[chart]["fn"](cfg, ctx, None, still=float(at))
                    ax = fig.axes[0]
                    lo, hi = (ax.get_xlim() if chart == "bars" else ax.get_ylim())
                    for p in ax.patches:
                        reach = (p.get_x() + p.get_width() if chart == "bars"
                                 else p.get_y() + p.get_height())
                        self.assertGreaterEqual(reach, lo - abs(hi - lo) * 1e-9)
                        self.assertLessEqual(reach, hi + abs(hi - lo) * 1e-9,
                                             f"{chart}: a bar sprang through the ceiling")
                    plt.close(fig)


class MotionTrimTests(GeneratedDataCase):
    """That an entrance is planned against the take, not against the clip it is cut into.

    Same property ClipTrimTests pins for the camera, and it has to hold for the same
    reason: the whole point of trimming is to take the middle of a shot at the speed that
    shot moves. An entrance that restarted inside the trim would put the title fading up in
    the middle of a chart that has been drawing for two seconds.
    """

    TRIM = {"clip_in": 0.2, "clip_out": 0.8}
    TARGET = 15

    def scrub_for(self, cfg, frame):
        shot = renderers.plan_shot(cfg, 30, 30, 6)
        for i in range(1001):
            if shot.at(i / 1000) == frame:
                return i / 1000
        self.fail(f"no scrub position reaches frame {frame}")

    def test_a_trimmed_frame_is_the_frame_the_take_had_there(self):
        for chart in CHART_FIXTURES:
            for style in MOVING:
                with self.subTest(chart=chart, style=style):
                    full = self.cfg(chart, motion=style)
                    cut = self.cfg(chart, motion=style, **self.TRIM)
                    np.testing.assert_array_equal(
                        draw(full, at=self.scrub_for(full, self.TARGET)),
                        draw(cut, at=self.scrub_for(cut, self.TARGET)),
                        f"{chart}/{style}: the trim re-planned the entrance")


class MotionConfigTests(unittest.TestCase):
    """The config seam: what an API caller may send and what the interface is handed."""

    def cfg(self, **kw):
        return appmod.clean_config({"tickers": ["NVDA"], "chart": "line", **kw})

    def test_the_default_is_the_render_that_was_already_there(self):
        self.assertEqual(self.cfg()["motion"], renderers.MOTION_NONE)

    def test_every_published_style_is_accepted(self):
        for style in STYLES:
            with self.subTest(style=style):
                self.assertEqual(self.cfg(motion=style)["motion"], style)

    def test_an_unknown_style_is_refused_rather_than_ignored(self):
        # Unlike easing, which has been falling through to "out" for as long as there has
        # been an API and has callers depending on it. Nothing has ever posted this field,
        # so there is nobody to break by checking it.
        with self.assertRaises(ValueError):
            self.cfg(motion="swoosh")

    def test_an_absent_field_still_means_off(self):
        self.assertEqual(self.cfg(motion=None)["motion"], renderers.MOTION_NONE)
        self.assertEqual(self.cfg(motion="")["motion"], renderers.MOTION_NONE)

    def test_meta_publishes_both_registries_with_their_prose(self):
        # The interface builds its two selects and their hints from these, so a style whose
        # description lived only in the markup would be a sentence nothing enforces.
        meta = appmod.app.test_client().get("/api/meta").get_json()
        for key, registry in (("easings", renderers.EASINGS),
                              ("motions", renderers.MOTIONS)):
            with self.subTest(key=key):
                self.assertEqual([e["id"] for e in meta[key]], list(registry))
                for entry in meta[key]:
                    self.assertTrue(entry["label"] and entry["desc"])


if __name__ == "__main__":
    unittest.main()
