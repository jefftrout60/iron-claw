#!/usr/bin/env python3
"""
Behavioral tests for health_store_cmd.py.

Run with:
    python3 -m unittest test_health_store_cmd -v
from the scripts/ directory.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import health_store_cmd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_episode(**kwargs):
    """Return a minimal episode dict with sensible defaults; override via kwargs."""
    defaults = {
        "id": "ep-001",
        "title": "Test Episode Title",
        "summary": "This is a plain summary.",
        "summary_extended": "",
        "show_title": "Test Show",
        "episode_number": "1",
        "pub_date": "2026-01-15T12:00:00Z",
        "source_quality": "transcript",
    }
    defaults.update(kwargs)
    return defaults


def _run_main(argv):
    """Patch sys.argv and call health_store_cmd.main()."""
    with patch("sys.argv", ["health_store_cmd.py"] + argv):
        health_store_cmd.main()


# ---------------------------------------------------------------------------
# P1 — episode not found exits with code 1
# ---------------------------------------------------------------------------

class TestEpisodeNotFound(unittest.TestCase):

    def test_missing_episode_id_exits_1(self):
        episodes_data = {"episodes": [_make_episode(id="ep-001")]}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"):
            with self.assertRaises(SystemExit) as ctx:
                _run_main(["--episode-id", "ep-does-not-exist"])

        self.assertEqual(ctx.exception.code, 1)

    def test_empty_episodes_list_exits_1(self):
        episodes_data = {"episodes": []}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"):
            with self.assertRaises(SystemExit) as ctx:
                _run_main(["--episode-id", "ep-001"])

        self.assertEqual(ctx.exception.code, 1)

    def test_missing_episode_prints_to_stderr(self):
        episodes_data = {"episodes": [_make_episode(id="ep-001")]}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"), \
             patch("sys.stderr") as mock_stderr:
            with self.assertRaises(SystemExit):
                _run_main(["--episode-id", "ep-missing"])

        # stderr.write was called — the print() with file=sys.stderr fired
        self.assertTrue(mock_stderr.write.called)


# ---------------------------------------------------------------------------
# P1 — episode exists but has no summary exits with code 1
# ---------------------------------------------------------------------------

class TestNoSummary(unittest.TestCase):

    def test_no_summary_fields_exits_1(self):
        episode = _make_episode(id="ep-001")
        del episode["summary"]
        del episode["summary_extended"]
        episodes_data = {"episodes": [episode]}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"):
            with self.assertRaises(SystemExit) as ctx:
                _run_main(["--episode-id", "ep-001"])

        self.assertEqual(ctx.exception.code, 1)

    def test_empty_summary_and_empty_extended_exits_1(self):
        episode = _make_episode(id="ep-001", summary="", summary_extended="")
        episodes_data = {"episodes": [episode]}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"):
            with self.assertRaises(SystemExit) as ctx:
                _run_main(["--episode-id", "ep-001"])

        self.assertEqual(ctx.exception.code, 1)

    def test_none_summary_fields_exits_1(self):
        episode = _make_episode(id="ep-001", summary=None, summary_extended=None)
        episodes_data = {"episodes": [episode]}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"):
            with self.assertRaises(SystemExit) as ctx:
                _run_main(["--episode-id", "ep-001"])

        self.assertEqual(ctx.exception.code, 1)

    def test_no_summary_prints_to_stderr(self):
        episode = _make_episode(id="ep-001", summary="", summary_extended="")
        episodes_data = {"episodes": [episode]}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"), \
             patch("sys.stderr") as mock_stderr:
            with self.assertRaises(SystemExit):
                _run_main(["--episode-id", "ep-001"])

        self.assertTrue(mock_stderr.write.called)


# ---------------------------------------------------------------------------
# P1 — duplicate: append_entry returns None → prints "Already in health store"
# ---------------------------------------------------------------------------

class TestDuplicate(unittest.TestCase):

    def test_duplicate_prints_already_in_health_store(self):
        episode = _make_episode(id="ep-001")
        episodes_data = {"episodes": [episode]}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"), \
             patch("health_store_cmd.health_store.append_entry", return_value=None), \
             patch("builtins.print") as mock_print:
            _run_main(["--episode-id", "ep-001"])

        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Already in health store", printed)

    def test_duplicate_exits_0(self):
        episode = _make_episode(id="ep-001")
        episodes_data = {"episodes": [episode]}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"), \
             patch("health_store_cmd.health_store.append_entry", return_value=None):
            # main() should return normally (no SystemExit) for the duplicate path
            try:
                _run_main(["--episode-id", "ep-001"])
            except SystemExit as e:
                self.fail(f"main() raised SystemExit({e.code}) for duplicate — expected exit 0 / no exit")


# ---------------------------------------------------------------------------
# P1 — success: append_entry called with correct entry_data
# ---------------------------------------------------------------------------

class TestSuccess(unittest.TestCase):

    def _run_success(self, episode_overrides=None, argv_extra=None):
        """
        Run main() with a well-formed episode and capture the call made to
        health_store.append_entry.  Returns the captured call args tuple.
        """
        episode = _make_episode(**(episode_overrides or {}))
        episodes_data = {"episodes": [episode]}
        fake_entry = {"id": "fake-id"}

        captured = {}

        def fake_append(entry_data, api_key="", model="gpt-4o-mini"):
            captured["entry_data"] = entry_data
            captured["api_key"] = api_key
            captured["model"] = model
            return fake_entry

        argv = ["--episode-id", episode["id"]] + (argv_extra or [])

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"), \
             patch("health_store_cmd.health_store.append_entry", side_effect=fake_append):
            _run_main(argv)

        return captured

    def test_append_entry_is_called(self):
        captured = self._run_success()
        self.assertIn("entry_data", captured)

    def test_append_entry_receives_correct_show(self):
        captured = self._run_success({"show_title": "My Great Podcast"})
        self.assertEqual(captured["entry_data"]["show"], "My Great Podcast")

    def test_append_entry_receives_correct_episode_title(self):
        captured = self._run_success({"title": "Great Episode"})
        self.assertEqual(captured["entry_data"]["episode_title"], "Great Episode")

    def test_append_entry_receives_correct_summary(self):
        captured = self._run_success({"summary": "Plain summary text."})
        self.assertEqual(captured["entry_data"]["summary"], "Plain summary text.")

    def test_append_entry_prefers_summary_extended_over_summary(self):
        captured = self._run_success({
            "summary": "Plain summary.",
            "summary_extended": "Extended and richer summary.",
        })
        self.assertEqual(captured["entry_data"]["summary"], "Extended and richer summary.")

    def test_append_entry_falls_back_to_summary_when_no_extended(self):
        captured = self._run_success({"summary": "Only summary.", "summary_extended": ""})
        self.assertEqual(captured["entry_data"]["summary"], "Only summary.")

    def test_append_entry_receives_date_truncated_to_10_chars(self):
        captured = self._run_success({"pub_date": "2026-03-22T09:30:00Z"})
        self.assertEqual(captured["entry_data"]["date"], "2026-03-22")

    def test_append_entry_tagged_by_defaults_to_user(self):
        captured = self._run_success()
        self.assertEqual(captured["entry_data"]["tagged_by"], "user")

    def test_append_entry_tagged_by_honoured_from_argv(self):
        captured = self._run_success(argv_extra=["--tagged-by", "admin"])
        self.assertEqual(captured["entry_data"]["tagged_by"], "admin")

    def test_append_entry_source_is_podcast(self):
        captured = self._run_success()
        self.assertEqual(captured["entry_data"]["source"], "podcast")

    def test_append_entry_show_falls_back_to_show_key(self):
        episode = _make_episode(id="ep-001")
        del episode["show_title"]
        episode["show"] = "Fallback Show Name"
        episodes_data = {"episodes": [episode]}

        captured = {}

        def fake_append(entry_data, api_key="", model="gpt-4o-mini"):
            captured["entry_data"] = entry_data
            return {"id": "fake"}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"), \
             patch("health_store_cmd.health_store.append_entry", side_effect=fake_append):
            _run_main(["--episode-id", "ep-001"])

        self.assertEqual(captured["entry_data"]["show"], "Fallback Show Name")

    def test_success_prints_added_message(self):
        episode = _make_episode(id="ep-001", title="My Episode")
        episodes_data = {"episodes": [episode]}

        with patch("health_store_cmd.load_vault", return_value=episodes_data), \
             patch("health_store_cmd.get_vault_path", return_value="/fake/episodes.json"), \
             patch("health_store_cmd.health_store.append_entry", return_value={"id": "ok"}), \
             patch("builtins.print") as mock_print:
            _run_main(["--episode-id", "ep-001"])

        printed = " ".join(str(c) for c in mock_print.call_args_list)
        self.assertIn("Added to health store", printed)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
