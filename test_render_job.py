"""Tests for the out-of-process render path.

The point of render_job.py is that a render no longer holds the lock the preview needs, so
the test that matters most here is the one timing a preview against an in-flight render.
The rest covers the protocol between the two processes and what the user is told when a
child dies without managing to explain itself.

Everything runs on demo data. The end-to-end encode is skipped where there is no ffmpeg.
Run with: python -m unittest
"""

import os
import shutil
import signal
import tempfile
import threading
import time
import unittest
from collections import deque
from unittest import mock

import app as appmod
import config
import data
import render_job

BASE = {"chart": "line", "tickers": ["NVDA"], "start": "2024-01-01", "end": "2024-06-01",
        "duration": 0.5, "hold": 0.0, "quality": "draft"}


def have_ffmpeg():
    if shutil.which("ffmpeg"):
        return True
    try:
        import imageio_ffmpeg  # noqa: F401
    except ImportError:
        return False
    return True


class ChildProcessTests(unittest.TestCase):
    """The parent/child round trip, exercised for real rather than mocked."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def out(self, name="clip.mp4"):
        return os.path.join(self.dir, name)

    @unittest.skipUnless(have_ffmpeg(), "needs ffmpeg to encode")
    def test_a_render_produces_a_file_and_reports_every_frame(self):
        cfg = appmod.clean_config(BASE)
        seen = []
        path = render_job.run(cfg, self.out(), progress=lambda i, n: seen.append((i, n)),
                              demo=True)

        self.assertTrue(os.path.exists(path), "the child reported ok but wrote no file")
        self.assertGreater(os.path.getsize(path), 0)
        self.assertTrue(seen, "no progress was reported")
        # The UI draws its bar straight from these, so every frame has to arrive, once,
        # in order, against a total that doesn't move underneath it.
        totals = {n for _, n in seen}
        self.assertEqual(len(totals), 1, "the frame count changed mid-render")
        self.assertEqual(len(seen), totals.pop(), "a frame went unreported")
        self.assertEqual([i for i, _ in seen], sorted(i for i, _ in seen))

    @unittest.skipUnless(have_ffmpeg(), "needs ffmpeg to encode")
    def test_demo_mode_reaches_the_child(self):
        # The flag lives in the parent's module state, so it has to be handed over
        # explicitly. Without that a --demo run would quietly fetch real prices in the
        # child, behind a UI insisting it is on generated data. The ticker is deliberately
        # not a real symbol: if this ever went to the network it would fail rather than
        # pass by luck.
        cfg = appmod.clean_config({**BASE, "tickers": ["ZZQQ"]})
        path = render_job.run(cfg, self.out(), demo=True)
        self.assertGreater(os.path.getsize(path), 0)

    def test_a_bad_config_comes_back_as_a_readable_error(self):
        cfg = {**appmod.clean_config(BASE), "chart": "no-such-chart"}
        with self.assertRaises(render_job.RenderError) as caught:
            render_job.run(cfg, self.out(), demo=True)
        self.assertIn("no-such-chart", str(caught.exception))

    def test_a_child_that_dies_silently_still_explains_itself(self):
        # No JSON, no ok, non-zero exit — what an OOM kill looks like from up here.
        with mock.patch.object(render_job.subprocess, "Popen", _fake_child(
                lines=["Traceback (most recent call last):", "MemoryError"], code=1)):
            with self.assertRaises(render_job.RenderError) as caught:
                render_job.run({}, self.out(), demo=True)
        self.assertIn("status 1", str(caught.exception))
        self.assertIn("MemoryError", str(caught.exception))

    def test_a_clean_exit_without_a_file_is_not_treated_as_success(self):
        with mock.patch.object(render_job.subprocess, "Popen",
                               _fake_child(lines=[], code=0)):
            with self.assertRaises(render_job.RenderError):
                render_job.run({}, self.out(), demo=True)

    def test_stray_output_does_not_break_the_protocol(self):
        # Anything in the child can print. The progress stream has to survive it.
        seen = []
        with mock.patch.object(render_job.subprocess, "Popen", _fake_child(
                lines=['some library being chatty',
                       '{"progress": 1, "total": 2}',
                       'not json either',
                       '{"progress": 2, "total": 2}',
                       '{"ok": true}'], code=0)):
            render_job.run({}, self.out(), progress=lambda i, n: seen.append((i, n)),
                           demo=True)
        self.assertEqual(seen, [(1, 2), (2, 2)])


class DeathMessageTests(unittest.TestCase):
    def test_sigkill_is_reported_as_a_memory_problem(self):
        msg = render_job._died(-signal.SIGKILL, deque())
        self.assertIn("SIGKILL", msg)
        self.assertIn("ran out of memory", msg)

    def test_an_unknown_signal_still_names_itself(self):
        self.assertIn("SIGTERM", render_job._died(-signal.SIGTERM, deque()))

    def test_the_last_line_of_output_is_carried_into_the_message(self):
        msg = render_job._died(1, deque(["earlier noise", "ValueError: no rows for ZZZZ"]))
        self.assertIn("ValueError: no rows for ZZZZ", msg)


class PreviewStaysLiveTests(unittest.TestCase):
    """The regression this whole change exists to prevent.

    worker() used to hold the same lock as /api/preview for the length of a render, so a
    preview issued mid-render waited the full ~70s at 1080p60 — on a UI that fires one on
    every control change. The render is stubbed here so the test asserts the locking
    behaviour rather than racing a real encode.
    """

    def setUp(self):
        data.set_demo(True)
        self.addCleanup(data.set_demo, False)
        self.addCleanup(data.reset_sources)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        patch = mock.patch.object(config, "OUT_DIR", self.dir)
        patch.start()
        self.addCleanup(patch.stop)
        self.client = appmod.app.test_client()

    def _await_status(self, job_id, status, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for job in self.client.get("/api/jobs").get_json():
                if job["id"] == job_id and job["status"] == status:
                    return job
            time.sleep(0.02)
        self.fail(f"job never reached {status}")

    def test_a_preview_is_answered_while_a_render_is_in_flight(self):
        release = threading.Event()

        def slow_render(cfg, out_path, progress=None, demo=False):
            if progress:
                progress(1, 2)
            release.wait(15)
            with open(out_path, "wb") as fh:
                fh.write(b"not really a video")
            return out_path

        with mock.patch.object(render_job, "run", slow_render):
            started = self.client.post("/api/render", json=BASE).get_json()
            self.assertNotIn("error", started)
            self._await_status(started["id"], "rendering")

            began = time.time()
            r = self.client.post("/api/preview?at=0.7", json=BASE)
            elapsed = time.time() - began

            # Still rendering when the preview came back — otherwise the timing proves
            # nothing, because the lock would have been free either way.
            in_flight = [j for j in self.client.get("/api/jobs").get_json()
                         if j["id"] == started["id"]][0]["status"] == "rendering"
            release.set()

        self.assertEqual(r.status_code, 200)
        self.assertIn("image", r.get_json())
        self.assertTrue(in_flight, "the render finished too early to prove anything")
        self.assertLess(elapsed, 5.0,
                        "the preview waited on the render — DRAW_LOCK is covering it again")
        self._await_status(started["id"], "done")


def _fake_child(lines, code):
    """Stand in for Popen so the parent-side protocol can be tested without a real child."""
    def factory(*_args, **_kwargs):
        child = mock.MagicMock()
        child.stdin = mock.MagicMock()
        child.stdout = iter([line + "\n" for line in lines])
        child.wait.return_value = code
        child.__enter__.return_value = child  # run() drives it as a context manager
        return child
    return factory


if __name__ == "__main__":
    unittest.main()
