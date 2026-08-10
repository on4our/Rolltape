"""Tests for the public page — routing, the showcase stills and email capture.

The landing page is the only part of this app a stranger sees before deciding whether to
try it, so the things worth pinning down are the ones that make it look broken: the app
and the page trading places under a flag, a still that can't be drawn taking the page
down with it, and a signup that silently loses an address.

Run with: python -m unittest
"""

import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

import app
import config
import examples as showcase
import renderers
import signups
import testsupport


class RoutingTests(unittest.TestCase):
    """Which of the two pages "/" serves, and whether both stay reachable either way."""

    def setUp(self):
        self.client = app.app.test_client()

    def is_app(self, resp):
        return b'<div class="layout">' in resp.data

    def is_landing(self, resp):
        return b"Animated charts for investing content" in resp.data

    def test_root_is_the_app_by_default(self):
        # The default has to reproduce the local setup exactly: someone running
        # `python app.py` wants their tool, not a page selling it to them.
        with mock.patch.object(config, "LANDING", False):
            self.assertTrue(self.is_app(self.client.get("/")))

    def test_root_is_the_landing_page_when_the_flag_is_set(self):
        with mock.patch.object(config, "LANDING", True):
            self.assertTrue(self.is_landing(self.client.get("/")))

    def test_the_app_keeps_its_own_url_under_the_flag(self):
        with mock.patch.object(config, "LANDING", True):
            self.assertTrue(self.is_app(self.client.get("/app")))

    def test_the_landing_page_is_reachable_without_the_flag(self):
        # So it can be checked on a laptop before anything is deployed.
        with mock.patch.object(config, "LANDING", False):
            self.assertTrue(self.is_landing(self.client.get("/landing")))


class LandingContentTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.body = self.client.get("/landing").data.decode()

    def test_every_chart_type_is_listed(self):
        # Built from the CHARTS registry for the same reason the app's list is: adding a
        # chart type should not mean remembering to edit a marketing page.
        for spec in renderers.CHARTS.values():
            self.assertIn(spec["label"], self.body)

    def test_every_example_is_on_the_page(self):
        for example_id in showcase.EXAMPLES:
            self.assertIn(f"/examples/{example_id}.png", self.body)

    def test_the_call_to_action_points_at_the_app(self):
        with mock.patch.object(config, "DEMO_URL", "https://demo.example.com"):
            body = self.client.get("/landing").data.decode()
        self.assertIn("https://demo.example.com", body)

    def test_the_page_never_offers_to_show_generated_prices(self):
        # The landing page used to disclose a generated-data instance. There is no such
        # instance any more — the app has no path to invented prices at all — so the
        # disclosure would now be describing something that cannot happen.
        self.assertNotIn("Demo data", self.body)
        self.assertNotIn("generated, not real", self.body)


class ExampleStillTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        self.dir = tempfile.mkdtemp()
        patcher = mock.patch.object(config, "EXAMPLES_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Generated prices, so the tests that really draw need no network. This patches
        # data.fetch itself, which is the seam the renderers go through — anything shallower
        # would leave the showcase stills calling out to a real feed.
        testsupport.patch_fetch(self)

    def test_an_unknown_example_is_a_404(self):
        self.assertEqual(self.client.get("/examples/nope.png").status_code, 404)

    def test_a_still_is_drawn_and_served(self):
        example_id = next(iter(showcase.EXAMPLES))
        resp = self.client.get(f"/examples/{example_id}.png")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/png")
        self.assertTrue(resp.data.startswith(b"\x89PNG"))

    def test_the_second_request_does_not_redraw(self):
        example_id = next(iter(showcase.EXAMPLES))
        self.assertEqual(self.client.get(f"/examples/{example_id}.png").status_code, 200)
        with mock.patch.object(showcase, "write_still") as drew:
            resp = self.client.get(f"/examples/{example_id}.png")
        self.assertEqual(resp.status_code, 200)
        drew.assert_not_called()

    def test_a_failed_draw_is_a_404_rather_than_a_500(self):
        # The likeliest cause is the price source being down, and that is exactly when
        # the rest of the page still needs to load.
        example_id = next(iter(showcase.EXAMPLES))
        with mock.patch.object(showcase, "write_still", side_effect=RuntimeError("boom")):
            resp = self.client.get(f"/examples/{example_id}.png")
        self.assertEqual(resp.status_code, 404)

    def test_a_half_written_still_is_never_cached(self):
        example_id = next(iter(showcase.EXAMPLES))
        path = showcase.path_for(example_id, self.dir)
        with mock.patch.object(renderers, "save_still", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                showcase.write_still(example_id, {}, self.dir)
        self.assertFalse(os.path.exists(path))

    def test_every_example_config_survives_validation(self):
        # clean_config() is where a typo in one of these would surface, and it would
        # surface as a blank showcase rather than an exception anyone would notice.
        for example_id, spec in showcase.EXAMPLES.items():
            with self.subTest(example=example_id):
                cleaned = app.clean_config(dict(spec["cfg"]))
                self.assertEqual(cleaned["chart"], spec["cfg"]["chart"])
                self.assertIn(cleaned["chart"], renderers.CHARTS)

    def test_examples_only_name_themes_that_exist(self):
        # A bad theme renders as Midnight without complaining, so the showcase would
        # quietly lose the variety that is the point of showing three.
        for example_id, spec in showcase.EXAMPLES.items():
            with self.subTest(example=example_id):
                self.assertIn(spec["cfg"]["theme"], renderers.THEMES)


class EmailValidationTests(unittest.TestCase):
    def test_a_plain_address_passes(self):
        self.assertEqual(signups.clean_email("  Sam@example.com "), "Sam@example.com")

    def test_an_empty_address_is_refused(self):
        with self.assertRaises(signups.SignupError):
            signups.clean_email("")

    def test_something_without_an_at_is_refused(self):
        with self.assertRaises(signups.SignupError):
            signups.clean_email("example.com")

    def test_a_bare_domain_is_refused(self):
        with self.assertRaises(signups.SignupError):
            signups.clean_email("sam@localhost")

    def test_whitespace_inside_is_refused(self):
        with self.assertRaises(signups.SignupError):
            signups.clean_email("sam @example.com")

    def test_an_overlong_address_is_refused(self):
        with self.assertRaises(signups.SignupError):
            signups.clean_email("a" * 250 + "@example.com")

    def test_a_plus_tag_is_kept(self):
        # Anything stricter than "one @ with something either side" rejects real
        # addresses, and plus-tagging is the one people notice.
        self.assertEqual(signups.clean_email("sam+rolltape@example.co.uk"),
                         "sam+rolltape@example.co.uk")


class SignupFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "signups.jsonl")
        for name, value in (("SIGNUPS_PATH", self.path), ("SIGNUP_URL", "")):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = app.app.test_client()

    def lines(self):
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_an_address_is_appended(self):
        resp = self.client.post("/api/signup", json={"email": "sam@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.lines()[0]["email"], "sam@example.com")

    def test_addresses_accumulate_rather_than_overwrite(self):
        self.client.post("/api/signup", json={"email": "one@example.com"})
        self.client.post("/api/signup", json={"email": "two@example.com"})
        self.assertEqual([r["email"] for r in self.lines()],
                         ["one@example.com", "two@example.com"])

    def test_a_bad_address_is_a_400_and_writes_nothing(self):
        resp = self.client.post("/api/signup", json={"email": "nope"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())
        self.assertFalse(os.path.exists(self.path))

    def test_a_form_post_redirects_back_to_the_page(self):
        # The page has to work with JS blocked — it is the only conversion on it.
        resp = self.client.post("/api/signup", data={"email": "sam@example.com"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("signup=ok", resp.headers["Location"])
        self.assertEqual(self.lines()[0]["email"], "sam@example.com")

    def test_a_bad_form_post_redirects_with_the_reason(self):
        resp = self.client.post("/api/signup", data={"email": "nope"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("signup=error", resp.headers["Location"])

    def test_an_unwritable_path_says_so_instead_of_thanking_you(self):
        # A read-only filesystem with no provider configured loses the address. Showing a
        # thank-you for that is the one outcome worse than an error.
        with mock.patch.object(config, "SIGNUPS_PATH", "/proc/nope/signups.jsonl"):
            resp = self.client.post("/api/signup", json={"email": "sam@example.com"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("aren't set up", resp.get_json()["error"])


class SignupForwardingTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "signups.jsonl")
        for name, value in (("SIGNUP_URL", "https://list.example.com/subscribe"),
                            ("SIGNUPS_PATH", self.path)):
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = app.app.test_client()

    @staticmethod
    def _response(status=200):
        resp = mock.MagicMock()
        resp.status = status
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        return resp

    def test_the_address_goes_to_the_provider(self):
        with mock.patch.object(signups.urllib.request, "urlopen",
                               return_value=self._response()) as sent:
            resp = self.client.post("/api/signup", json={"email": "sam@example.com"})
        self.assertEqual(resp.status_code, 200)
        request = sent.call_args[0][0]
        self.assertEqual(request.full_url, "https://list.example.com/subscribe")
        self.assertEqual(json.loads(request.data)["email"], "sam@example.com")

    def test_a_provider_never_shares_the_disk_with_the_file(self):
        # Configuring a provider is how a container stops losing signups on restart. If
        # both ran, the container would keep writing a file nobody ever reads.
        with mock.patch.object(signups.urllib.request, "urlopen",
                               return_value=self._response()):
            self.client.post("/api/signup", json={"email": "sam@example.com"})
        self.assertFalse(os.path.exists(self.path))

    def test_already_subscribed_reads_as_success(self):
        # Most list providers spell that 409, and from where the visitor is standing it
        # is not a failure.
        error = urllib.error.HTTPError("u", 409, "Conflict", {}, None)
        with mock.patch.object(signups.urllib.request, "urlopen", side_effect=error):
            resp = self.client.post("/api/signup", json={"email": "sam@example.com"})
        self.assertEqual(resp.status_code, 200)

    def test_a_provider_outage_is_not_leaked_to_the_visitor(self):
        error = urllib.error.HTTPError("u", 500, "Server Error", {}, None)
        with mock.patch.object(signups.urllib.request, "urlopen", side_effect=error):
            resp = self.client.post("/api/signup", json={"email": "sam@example.com"})
        self.assertEqual(resp.status_code, 400)
        message = resp.get_json()["error"]
        self.assertIn("try again", message)
        self.assertNotIn("500", message)

    def test_an_unreachable_provider_is_an_error_not_a_thank_you(self):
        with mock.patch.object(signups.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("no route")):
            resp = self.client.post("/api/signup", json={"email": "sam@example.com"})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
