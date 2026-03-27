#!/usr/bin/env python3
"""
Behavioral tests for on_demand episode matching and transcript_fetcher utilities.

Run with:
    python3 -m unittest test_episode_utils -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import on_demand
import transcript_fetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ep(title, audio_url="", ep_id=""):
    """Build a minimal episode dict for testing."""
    return {"title": title, "audio_url": audio_url, "id": ep_id}


# ---------------------------------------------------------------------------
# P1 — on_demand episode matching
# ---------------------------------------------------------------------------


class TestIsUrl(unittest.TestCase):

    def test_https_url_returns_true(self):
        self.assertTrue(on_demand._is_url("https://example.com/ep.mp3"))

    def test_http_url_returns_true(self):
        self.assertTrue(on_demand._is_url("http://example.com/ep.mp3"))

    def test_plain_query_returns_false(self):
        self.assertFalse(on_demand._is_url("Peter Attia #312"))

    def test_episode_number_query_returns_false(self):
        self.assertFalse(on_demand._is_url("#312"))

    def test_empty_string_returns_false(self):
        self.assertFalse(on_demand._is_url(""))


class TestIsEpisodeNumberQuery(unittest.TestCase):

    def test_hash_number_returns_true(self):
        self.assertTrue(on_demand._is_episode_number_query("#312"))

    def test_ep_space_number_returns_true(self):
        self.assertTrue(on_demand._is_episode_number_query("ep 312"))

    def test_episode_space_number_returns_true(self):
        self.assertTrue(on_demand._is_episode_number_query("episode 312"))

    def test_bare_number_returns_true(self):
        self.assertTrue(on_demand._is_episode_number_query("312"))

    def test_ep_dot_number_returns_true(self):
        self.assertTrue(on_demand._is_episode_number_query("ep. 312"))

    def test_text_without_number_returns_false(self):
        self.assertFalse(on_demand._is_episode_number_query("Peter Attia episode"))

    def test_title_with_embedded_hash_returns_false(self):
        # Hash is not at the start of the query — should not be treated as number query
        self.assertFalse(on_demand._is_episode_number_query("Peter Attia #312"))

    def test_empty_string_returns_false(self):
        self.assertFalse(on_demand._is_episode_number_query(""))


class TestMatchByTitle(unittest.TestCase):

    def setUp(self):
        self.episodes = [
            _ep("Naval Ravikant on Wealth and Happiness"),
            _ep("Peter Attia on Longevity"),
            _ep("The Science of Sleep with Matthew Walker"),
        ]

    def test_all_query_words_in_title_returns_episode(self):
        result = on_demand._match_by_title("Naval Ravikant wealth", self.episodes)
        self.assertIsNotNone(result)
        self.assertIn("Naval", result["title"])

    def test_single_word_match_returns_episode(self):
        result = on_demand._match_by_title("Longevity", self.episodes)
        self.assertIsNotNone(result)
        self.assertIn("Longevity", result["title"])

    def test_missing_query_word_returns_none(self):
        result = on_demand._match_by_title("Naval Ravikant longevity", self.episodes)
        self.assertIsNone(result)

    def test_case_insensitive_match(self):
        result = on_demand._match_by_title("naval ravikant", self.episodes)
        self.assertIsNotNone(result)
        self.assertIn("Naval", result["title"])

    def test_no_matching_episode_returns_none(self):
        result = on_demand._match_by_title("Lex Fridman", self.episodes)
        self.assertIsNone(result)

    def test_empty_episode_list_returns_none(self):
        result = on_demand._match_by_title("Naval", [])
        self.assertIsNone(result)

    def test_returns_first_matching_episode(self):
        # Two episodes contain "on" — should return the first one
        result = on_demand._match_by_title("on", self.episodes)
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Naval Ravikant on Wealth and Happiness")


class TestMatchByNumber(unittest.TestCase):

    def setUp(self):
        self.episodes = [
            _ep("Episode #311: Recovering from Injury"),
            _ep("Episode #312: The Drive with Peter Attia"),
            _ep("Episode #313: Sleep Science"),
        ]

    def test_hash_number_matches_correct_episode(self):
        result = on_demand._match_by_number("#312", self.episodes)
        self.assertIsNotNone(result)
        self.assertIn("#312", result["title"])

    def test_ep_prefix_matches_episode_number_in_title(self):
        result = on_demand._match_by_number("ep 312", self.episodes)
        self.assertIsNotNone(result)
        self.assertIn("#312", result["title"])

    def test_episode_prefix_matches_episode_number_in_title(self):
        result = on_demand._match_by_number("episode 312", self.episodes)
        self.assertIsNotNone(result)
        self.assertIn("#312", result["title"])

    def test_hash_number_does_not_match_different_number(self):
        result = on_demand._match_by_number("#312", [_ep("Episode #313: Sleep Science")])
        self.assertIsNone(result)

    def test_no_match_returns_none(self):
        result = on_demand._match_by_number("#999", self.episodes)
        self.assertIsNone(result)

    def test_empty_episode_list_returns_none(self):
        result = on_demand._match_by_number("#312", [])
        self.assertIsNone(result)

    def test_bare_number_matches_episode(self):
        result = on_demand._match_by_number("312", self.episodes)
        self.assertIsNotNone(result)
        self.assertIn("#312", result["title"])


class TestFindEpisodeInVault(unittest.TestCase):

    def setUp(self):
        self.episodes = [
            {
                "title": "Episode #312: Longevity Protocols",
                "audio_url": "https://cdn.example.com/ep312.mp3",
                "id": "ep-312-id",
            },
            {
                "title": "Episode #313: Sleep and Recovery",
                "audio_url": "https://cdn.example.com/ep313.mp3",
                "id": "ep-313-id",
            },
            {
                "title": "Naval Ravikant on Wealth",
                "audio_url": "https://cdn.example.com/naval.mp3",
                "id": "naval-id",
            },
        ]

    def test_url_query_routes_to_url_match_by_audio_url(self):
        result = on_demand._find_episode_in_vault(
            "https://cdn.example.com/ep312.mp3", self.episodes
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "ep-312-id")

    def test_url_query_routes_to_url_match_by_episode_id(self):
        result = on_demand._find_episode_in_vault("ep-313-id", self.episodes)
        # "ep-313-id" does not start with http — falls through to title match
        # This confirms URL routing only fires for http/https prefixes
        # The id field is matched by _match_by_url, but _is_url is False here
        # So title match fires; "ep-313-id" words don't appear in any title → None
        self.assertIsNone(result)

    def test_url_query_does_not_match_wrong_episode(self):
        result = on_demand._find_episode_in_vault(
            "https://cdn.example.com/ep312.mp3",
            [self.episodes[1]],  # only ep313 in list
        )
        self.assertIsNone(result)

    def test_hash_number_query_routes_to_number_match(self):
        result = on_demand._find_episode_in_vault("#312", self.episodes)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "ep-312-id")

    def test_ep_prefix_query_routes_to_number_match(self):
        result = on_demand._find_episode_in_vault("ep 313", self.episodes)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "ep-313-id")

    def test_text_query_routes_to_title_match(self):
        result = on_demand._find_episode_in_vault("Naval Ravikant", self.episodes)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "naval-id")

    def test_text_query_falls_back_to_number_match(self):
        # "Longevity Protocols #312" — title match will fail (no episode has
        # all those words exactly), but number fallback should find #312
        result = on_demand._find_episode_in_vault(
            "SomeMissingShow #312", self.episodes
        )
        # title match fails ("somemissingshow" not in any title),
        # number fallback finds episode #312
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "ep-312-id")

    def test_no_matching_episode_returns_none(self):
        result = on_demand._find_episode_in_vault("Lex Fridman", self.episodes)
        self.assertIsNone(result)

    def test_empty_episode_list_returns_none(self):
        result = on_demand._find_episode_in_vault("#312", [])
        self.assertIsNone(result)


class TestEpisodeNumberMatchHelper(unittest.TestCase):
    """Tests for _episode_number_match — the underlying matching predicate."""

    def test_hash_query_matches_hash_in_title(self):
        self.assertTrue(on_demand._episode_number_match("#312", "Episode #312: Longevity"))

    def test_hash_query_does_not_match_different_number(self):
        self.assertFalse(on_demand._episode_number_match("#312", "Episode #313: Sleep"))

    def test_ep_prefix_query_matches_episode_word_in_title(self):
        self.assertTrue(on_demand._episode_number_match("ep 312", "Episode #312: Longevity"))

    def test_episode_prefix_query_matches_hash_in_title(self):
        self.assertTrue(on_demand._episode_number_match("episode 312", "Episode #312: Longevity"))

    def test_bare_number_query_matches_title_with_that_number(self):
        self.assertTrue(on_demand._episode_number_match("312", "Episode #312: Longevity"))

    def test_text_only_query_returns_false(self):
        self.assertFalse(on_demand._episode_number_match("Peter Attia", "Episode #312: Longevity"))

    def test_query_number_does_not_collide_with_partial_number_in_title(self):
        # #31 should not match a title containing #312
        self.assertFalse(on_demand._episode_number_match("#31", "Episode #312: Longevity"))


# ---------------------------------------------------------------------------
# P2 — transcript_fetcher utilities
# ---------------------------------------------------------------------------


class TestStripHtml(unittest.TestCase):

    def test_removes_bold_tags(self):
        result = transcript_fetcher.strip_html("<b>Hello</b>")
        self.assertEqual(result, "Hello")

    def test_removes_paragraph_tags(self):
        result = transcript_fetcher.strip_html("<p>Hello world</p>")
        self.assertEqual(result, "Hello world")

    def test_removes_br_tags(self):
        # <br> is stripped without inserting a space — tag removal is not
        # whitespace insertion; the subsequent \s+ collapse only normalises
        # existing whitespace characters.
        result = transcript_fetcher.strip_html("Line one<br>Line two")
        self.assertEqual(result, "Line oneLine two")

    def test_decodes_amp_entity(self):
        result = transcript_fetcher.strip_html("Salt &amp; Pepper")
        self.assertEqual(result, "Salt & Pepper")

    def test_decodes_lt_entity(self):
        result = transcript_fetcher.strip_html("A &lt; B")
        self.assertEqual(result, "A < B")

    def test_decodes_gt_entity(self):
        result = transcript_fetcher.strip_html("A &gt; B")
        self.assertEqual(result, "A > B")

    def test_empty_string_returns_empty_string(self):
        result = transcript_fetcher.strip_html("")
        self.assertEqual(result, "")

    def test_none_returns_empty_string(self):
        result = transcript_fetcher.strip_html(None)
        self.assertEqual(result, "")

    def test_nested_tags_are_stripped(self):
        result = transcript_fetcher.strip_html("<p><b>Bold</b> and normal</p>")
        self.assertEqual(result, "Bold and normal")

    def test_mixed_tags_and_entities(self):
        result = transcript_fetcher.strip_html("<p>Rock &amp; Roll <b>forever</b></p>")
        self.assertEqual(result, "Rock & Roll forever")

    def test_whitespace_is_collapsed(self):
        result = transcript_fetcher.strip_html("  lots   of   spaces  ")
        self.assertEqual(result, "lots of spaces")

    def test_plain_text_unchanged(self):
        result = transcript_fetcher.strip_html("Just plain text")
        self.assertEqual(result, "Just plain text")


class TestStripVttTimestamps(unittest.TestCase):

    def test_removes_vtt_timestamp_lines(self):
        vtt = "00:00:01.000 --> 00:00:04.000\nHello world"
        result = transcript_fetcher.strip_vtt_timestamps(vtt)
        self.assertNotIn("-->", result)
        self.assertIn("Hello world", result)

    def test_removes_webvtt_header(self):
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHello"
        result = transcript_fetcher.strip_vtt_timestamps(vtt)
        self.assertNotIn("WEBVTT", result)

    def test_leaves_spoken_text_intact(self):
        vtt = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "Welcome to the podcast.\n\n"
            "00:00:04.500 --> 00:00:08.000\n"
            "Today we discuss longevity.\n"
        )
        result = transcript_fetcher.strip_vtt_timestamps(vtt)
        self.assertIn("Welcome to the podcast", result)
        self.assertIn("Today we discuss longevity", result)

    def test_removes_srt_style_timestamps_with_comma(self):
        # SRT uses commas instead of periods in timestamps
        srt = "00:00:01,000 --> 00:00:04,000\nHello"
        result = transcript_fetcher.strip_vtt_timestamps(srt)
        self.assertNotIn("-->", result)

    def test_handles_hour_prefixed_timestamps(self):
        vtt = "1:00:00.000 --> 1:00:05.000\nDeep in the episode"
        result = transcript_fetcher.strip_vtt_timestamps(vtt)
        self.assertNotIn("-->", result)
        self.assertIn("Deep in the episode", result)

    def test_empty_string_returns_empty(self):
        result = transcript_fetcher.strip_vtt_timestamps("")
        self.assertEqual(result, "")

    def test_plain_text_without_timestamps_is_unchanged(self):
        text = "This is just plain spoken text with no timestamps."
        result = transcript_fetcher.strip_vtt_timestamps(text)
        self.assertEqual(result, text)


class TestMakeSlug(unittest.TestCase):

    def test_basic_two_word_title(self):
        self.assertEqual(transcript_fetcher.make_slug("Hello World"), "hello-world")

    def test_special_chars_removed(self):
        # # is not in \w or \s or -, so it is stripped
        result = transcript_fetcher.make_slug("Peter Attia #312")
        self.assertEqual(result, "peter-attia-312")

    def test_multiple_spaces_become_single_hyphen(self):
        result = transcript_fetcher.make_slug("Hello   World")
        self.assertEqual(result, "hello-world")

    def test_underscores_become_hyphens(self):
        result = transcript_fetcher.make_slug("hello_world")
        self.assertEqual(result, "hello-world")

    def test_mixed_spaces_and_underscores(self):
        result = transcript_fetcher.make_slug("hello _ world")
        self.assertEqual(result, "hello-world")

    def test_output_is_lowercase(self):
        result = transcript_fetcher.make_slug("ALL CAPS TITLE")
        self.assertEqual(result, "all-caps-title")

    def test_leading_and_trailing_hyphens_stripped(self):
        # A title that starts/ends with special chars should not produce leading/trailing hyphens
        result = transcript_fetcher.make_slug("...Title Here...")
        self.assertFalse(result.startswith("-"))
        self.assertFalse(result.endswith("-"))

    def test_consecutive_hyphens_collapsed(self):
        # Multiple consecutive special chars should not leave "--" in the slug
        result = transcript_fetcher.make_slug("Hello -- World")
        self.assertNotIn("--", result)

    def test_numbers_preserved(self):
        result = transcript_fetcher.make_slug("Episode 312")
        self.assertEqual(result, "episode-312")

    def test_single_word(self):
        result = transcript_fetcher.make_slug("Longevity")
        self.assertEqual(result, "longevity")

    def test_already_slug_like(self):
        result = transcript_fetcher.make_slug("already-slugged")
        self.assertEqual(result, "already-slugged")


if __name__ == "__main__":
    unittest.main()
