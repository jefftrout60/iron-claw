#!/usr/bin/env python3
"""
Behavioral tests for summarizer.py.

Run with:
    python3 -m unittest test_summarizer -v
from the scripts/ directory.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the scripts/ directory is on the path so `import summarizer` works
# regardless of the working directory the test runner uses.
sys.path.insert(0, str(Path(__file__).parent))

import summarizer
from summarizer import _build_prompt, classify_show_style


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prompt(style, **kwargs):
    """Call _build_prompt with sensible defaults; override via kwargs."""
    defaults = dict(
        summary_style=style,
        show="Test Show",
        title="Test Episode",
        transcript="Sample transcript text.",
        depth="standard",
        source_quality="",
        summary_paragraphs=0,
        show_notes="",
    )
    defaults.update(kwargs)
    return _build_prompt(**defaults)


# ---------------------------------------------------------------------------
# P1 — _build_prompt() style coverage
# ---------------------------------------------------------------------------

class TestBuildPromptStyleCoverage(unittest.TestCase):

    def _assert_non_empty_tuple(self, result, style_name):
        self.assertIsInstance(result, tuple, f"{style_name}: result should be a tuple")
        self.assertEqual(len(result), 2, f"{style_name}: tuple should have exactly 2 elements")
        system, user = result
        self.assertIsInstance(system, str, f"{style_name}: system prompt should be a str")
        self.assertIsInstance(user, str, f"{style_name}: user prompt should be a str")
        self.assertTrue(len(system) > 0, f"{style_name}: system prompt should not be empty")
        self.assertTrue(len(user) > 0, f"{style_name}: user prompt should not be empty")

    def test_build_prompt_deep_science_returns_tuple(self):
        result = _make_prompt("deep_science")
        self._assert_non_empty_tuple(result, "deep_science")

    def test_build_prompt_long_form_interview_returns_tuple(self):
        result = _make_prompt("long_form_interview")
        self._assert_non_empty_tuple(result, "long_form_interview")

    def test_build_prompt_commentary_returns_tuple(self):
        result = _make_prompt("commentary")
        self._assert_non_empty_tuple(result, "commentary")

    def test_build_prompt_hunting_outdoor_returns_tuple(self):
        result = _make_prompt("hunting_outdoor")
        self._assert_non_empty_tuple(result, "hunting_outdoor")

    def test_build_prompt_meateater_returns_tuple(self):
        result = _make_prompt("meateater")
        self._assert_non_empty_tuple(result, "meateater")

    def test_build_prompt_orvis_fly_fishing_returns_tuple(self):
        result = _make_prompt("orvis_fly_fishing")
        self._assert_non_empty_tuple(result, "orvis_fly_fishing")

    def test_build_prompt_devotional_returns_tuple(self):
        result = _make_prompt("devotional")
        self._assert_non_empty_tuple(result, "devotional")


# ---------------------------------------------------------------------------
# P1 — extended depth injects extended_suffix into user prompt
# ---------------------------------------------------------------------------

class TestBuildPromptExtendedDepth(unittest.TestCase):

    EXTENDED_MARKER = "Provide a more detailed summary than usual."

    def test_extended_depth_injects_extended_suffix_into_user_prompt(self):
        _, user = _make_prompt("deep_science", depth="extended")
        self.assertIn(self.EXTENDED_MARKER, user)

    def test_standard_depth_does_not_inject_extended_suffix(self):
        _, user = _make_prompt("deep_science", depth="standard")
        self.assertNotIn(self.EXTENDED_MARKER, user)

    def test_extended_depth_works_across_all_styles(self):
        """Spot-check a few other styles to confirm extended_suffix is not style-gated."""
        for style in ("commentary", "meateater", "devotional"):
            with self.subTest(style=style):
                _, user = _make_prompt(style, depth="extended")
                self.assertIn(self.EXTENDED_MARKER, user)


# ---------------------------------------------------------------------------
# P1 — Q&A in title injects Q&A structure instruction into user prompt
# ---------------------------------------------------------------------------

class TestBuildPromptQATitle(unittest.TestCase):

    QA_MARKER = "Structure the summary as a Q&A"

    def test_qa_in_title_injects_qa_instruction(self):
        _, user = _make_prompt("deep_science", title="Episode Q&A with Dr. Smith")
        self.assertIn(self.QA_MARKER, user)

    def test_qa_lowercase_in_title_injects_qa_instruction(self):
        _, user = _make_prompt("long_form_interview", title="listener q&a december")
        self.assertIn(self.QA_MARKER, user)

    def test_title_without_qa_does_not_inject_qa_instruction(self):
        _, user = _make_prompt("deep_science", title="Regular Episode About Sleep")
        self.assertNotIn(self.QA_MARKER, user)


# ---------------------------------------------------------------------------
# P1 — show_notes > 200 chars injects topic map section
# ---------------------------------------------------------------------------

class TestBuildPromptShowNotes(unittest.TestCase):

    TOPIC_MAP_MARKER = "Episode description / show notes"

    def _long_show_notes(self):
        """Return a show notes string that is definitely > 200 stripped chars."""
        return "A" * 201

    def _short_show_notes(self):
        """Return a show notes string that is <= 200 stripped chars."""
        return "A" * 200

    def test_long_show_notes_injects_topic_map(self):
        _, user = _make_prompt("commentary", show_notes=self._long_show_notes())
        self.assertIn(self.TOPIC_MAP_MARKER, user)

    def test_short_show_notes_does_not_inject_topic_map(self):
        _, user = _make_prompt("commentary", show_notes=self._short_show_notes())
        self.assertNotIn(self.TOPIC_MAP_MARKER, user)

    def test_empty_show_notes_does_not_inject_topic_map(self):
        _, user = _make_prompt("commentary", show_notes="")
        self.assertNotIn(self.TOPIC_MAP_MARKER, user)

    def test_boundary_exactly_200_chars_does_not_inject_topic_map(self):
        """200 stripped chars is NOT > 200, so no topic map."""
        _, user = _make_prompt("commentary", show_notes="B" * 200)
        self.assertNotIn(self.TOPIC_MAP_MARKER, user)

    def test_boundary_201_chars_injects_topic_map(self):
        _, user = _make_prompt("commentary", show_notes="C" * 201)
        self.assertIn(self.TOPIC_MAP_MARKER, user)


# ---------------------------------------------------------------------------
# P1 — per-show extra instructions
# ---------------------------------------------------------------------------

class TestBuildPromptShowExtraInstructions(unittest.TestCase):

    def test_rokcast_show_produces_gear_enumeration_instruction(self):
        GEAR_MARKER = "list every specific item"
        _, user = _make_prompt("hunting_outdoor", show="RokCast Podcast")
        self.assertIn(GEAR_MARKER, user)

    def test_rokcast_case_insensitive_match(self):
        """Show matching is on lowercase — mixed case should still trigger."""
        GEAR_MARKER = "list every specific item"
        _, user = _make_prompt("hunting_outdoor", show="ROKCAST Episode")
        self.assertIn(GEAR_MARKER, user)

    def test_triggernometry_produces_unasked_question_instruction(self):
        UNASKED_MARKER = "The Unasked Question:"
        _, user = _make_prompt("commentary", show="Triggernometry")
        self.assertIn(UNASKED_MARKER, user)

    def test_triggernometry_case_insensitive_match(self):
        UNASKED_MARKER = "The Unasked Question:"
        _, user = _make_prompt("commentary", show="TRIGGERNOMETRY PODCAST")
        self.assertIn(UNASKED_MARKER, user)

    def test_unrelated_show_does_not_inject_gear_instruction(self):
        GEAR_MARKER = "list every specific item"
        _, user = _make_prompt("hunting_outdoor", show="Some Other Podcast")
        self.assertNotIn(GEAR_MARKER, user)

    def test_unrelated_show_does_not_inject_unasked_question(self):
        UNASKED_MARKER = "The Unasked Question:"
        _, user = _make_prompt("commentary", show="Random News Show")
        self.assertNotIn(UNASKED_MARKER, user)


# ---------------------------------------------------------------------------
# P2 — CLI --style choices (regression for the fix)
# ---------------------------------------------------------------------------

class TestParseArgsStyleChoices(unittest.TestCase):
    """
    _parse_args() reads sys.argv internally via argparse.  We patch sys.argv
    to simulate each invocation scenario.
    """

    def _parse(self, argv):
        """Run _parse_args() with the given argv list (excluding script name)."""
        with patch("sys.argv", ["summarizer.py"] + argv):
            return summarizer._parse_args()

    def test_subcommand_style_meateater_does_not_raise(self):
        try:
            self._parse(["summarize", "--style", "meateater", "--title", "Test"])
        except SystemExit as e:
            self.fail(f"_parse_args raised SystemExit({e.code}) for meateater style")

    def test_subcommand_style_orvis_fly_fishing_does_not_raise(self):
        try:
            self._parse(["summarize", "--style", "orvis_fly_fishing", "--title", "Test"])
        except SystemExit as e:
            self.fail(f"_parse_args raised SystemExit({e.code}) for orvis_fly_fishing style")

    def test_legacy_flat_args_meateater_does_not_raise(self):
        """Legacy invocation without a subcommand should still be accepted."""
        try:
            self._parse(["--style", "meateater", "--title", "Test"])
        except SystemExit as e:
            self.fail(f"_parse_args raised SystemExit({e.code}) for legacy meateater flat args")

    def test_invalid_style_raises_system_exit(self):
        with self.assertRaises(SystemExit):
            self._parse(["summarize", "--style", "invalid_style", "--title", "Test"])


# ---------------------------------------------------------------------------
# P1 — classify_show_style prompt contains all 7 style names
# ---------------------------------------------------------------------------

class TestClassifyShowStylePromptContents(unittest.TestCase):

    ALL_STYLES = [
        "deep_science",
        "long_form_interview",
        "commentary",
        "hunting_outdoor",
        "meateater",
        "orvis_fly_fishing",
        "devotional",
    ]

    def _capture_user_prompt(self):
        """
        Monkeypatch call_openai so we can inspect the user prompt that
        classify_show_style passes to it.  Returns (captured_prompt, result).
        """
        captured = {}

        def fake_call_openai(prompt, system_prompt, api_key, model=None, **kwargs):
            captured["user_prompt"] = prompt
            # Return a valid style so the function does not raise or fall back
            return "long_form_interview"

        with patch("summarizer.call_openai", side_effect=fake_call_openai):
            result = classify_show_style("Test Show", "Test description", api_key="fake")

        return captured.get("user_prompt", ""), result

    def test_classify_prompt_contains_meateater(self):
        user_prompt, _ = self._capture_user_prompt()
        self.assertIn("meateater", user_prompt)

    def test_classify_prompt_contains_orvis_fly_fishing(self):
        user_prompt, _ = self._capture_user_prompt()
        self.assertIn("orvis_fly_fishing", user_prompt)

    def test_classify_prompt_contains_all_seven_styles(self):
        user_prompt, _ = self._capture_user_prompt()
        for style in self.ALL_STYLES:
            with self.subTest(style=style):
                self.assertIn(style, user_prompt, f"Style '{style}' missing from classify prompt")

    def test_classify_returns_valid_style_when_llm_responds_correctly(self):
        """Sanity-check: returned value is one of the seven valid styles."""
        _, result = self._capture_user_prompt()
        self.assertIn(result, self.ALL_STYLES)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
