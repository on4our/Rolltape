"""Tests for brand kit persistence and the default-title template.

The presets file is redirected to a temp dir per test, so nothing here touches a real
saved kit. Run with: python -m unittest
"""

import json
import os
import shutil
import tempfile
import unittest

import app
import config
import presets

KIT = {"theme": "carbon", "footer": "@rolltape", "title_format": "{ticker} weekly"}


class PresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = config.PRESETS_PATH
        config.PRESETS_PATH = os.path.join(self.dir, "presets.json")

    def tearDown(self):
        config.PRESETS_PATH = self._saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_saved_kit_survives_a_reload(self):
        presets.save("Channel", KIT)
        # Nothing is cached in memory, so this is a genuine round trip through the file.
        self.assertEqual(presets.all_kits(), {"Channel": KIT})

    def test_saving_the_same_name_updates_rather_than_duplicates(self):
        presets.save("Channel", KIT)
        presets.save("Channel", {**KIT, "footer": "@changed"})
        kits = presets.all_kits()
        self.assertEqual(len(kits), 1)
        self.assertEqual(kits["Channel"]["footer"], "@changed")

    def test_delete_removes_it_and_reports_a_miss(self):
        presets.save("Channel", KIT)
        self.assertTrue(presets.delete("Channel"))
        self.assertEqual(presets.all_kits(), {})
        self.assertFalse(presets.delete("Channel"))

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(presets.all_kits(), {})

    def test_corrupt_file_reads_as_empty_rather_than_raising(self):
        with open(config.PRESETS_PATH, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        self.assertEqual(presets.all_kits(), {})
        # And it stays usable — a corrupt file is written over, not appended to.
        presets.save("Channel", KIT)
        self.assertEqual(presets.all_kits(), {"Channel": KIT})

    def test_fields_are_trimmed_and_unknown_keys_dropped(self):
        _, cleaned = presets.save(" Channel ", {**KIT, "footer": "  @x  ", "junk": "no"})
        self.assertEqual(cleaned["footer"], "@x")
        self.assertNotIn("junk", cleaned)
        self.assertIn("Channel", presets.all_kits())

    def test_a_nameless_kit_is_refused(self):
        for name in ("", "   ", None):
            with self.assertRaises(ValueError):
                presets.save(name, KIT)

    def test_limits_are_enforced(self):
        with self.assertRaises(ValueError):
            presets.save("x" * (presets.MAX_NAME + 1), KIT)
        with self.assertRaises(ValueError):
            presets.save("Channel", {**KIT, "footer": "x" * (presets.MAX_FIELD + 1)})

    def test_the_file_is_valid_json_on_disk(self):
        presets.save("Channel", KIT)
        with open(config.PRESETS_PATH, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"Channel": KIT})


class TitleFormatTests(unittest.TestCase):
    def test_tokens_are_filled_from_the_config(self):
        self.assertEqual(app.format_title("{ticker} weekly", "line", ["NVDA"]),
                         "NVDA weekly")
        self.assertEqual(app.format_title("{tickers}", "compare", ["NVDA", "AMD"]),
                         "NVDA, AMD")
        self.assertEqual(app.format_title("{chart}", "line", ["NVDA"]), "Line reveal")

    def test_unknown_tokens_are_left_alone(self):
        self.assertEqual(app.format_title("{nope} {ticker}", "line", ["NVDA"]),
                         "{nope} NVDA")

    def test_empty_and_missing_inputs_resolve_to_nothing(self):
        self.assertEqual(app.format_title("", "line", ["NVDA"]), "")
        self.assertEqual(app.format_title(None, "line", ["NVDA"]), "")
        # No ticker to substitute leaves nothing behind, not a stray token.
        self.assertEqual(app.format_title("{ticker}", "bars", []), "")


class TitleInConfigTests(unittest.TestCase):
    BASE = {"chart": "line", "tickers": ["NVDA"], "start": "2024-01-01"}

    def test_the_format_fills_a_blank_title(self):
        cfg = app.clean_config({**self.BASE, "title_format": "{ticker} · 5Y"})
        self.assertEqual(cfg["title"], "NVDA · 5Y")

    def test_a_typed_title_wins(self):
        cfg = app.clean_config({**self.BASE, "title": "Hand written",
                                "title_format": "{ticker} · 5Y"})
        self.assertEqual(cfg["title"], "Hand written")

    def test_no_format_leaves_the_chart_default_to_apply(self):
        # None rather than "", so each renderer's own `or <default>` still fires.
        self.assertIsNone(app.clean_config(self.BASE)["title"])
        self.assertIsNone(
            app.clean_config({**self.BASE, "title_format": "   "})["title"])


if __name__ == "__main__":
    unittest.main()
