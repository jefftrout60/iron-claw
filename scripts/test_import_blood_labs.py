#!/usr/bin/env python3
"""
Behavioral tests for parse_reference_range() in import-blood-labs.py
and date_chunks() in oura-sync.py.

Run with:
    python3 -m unittest test_import_blood_labs -v
from the scripts/ directory.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

# Scripts dir on the path so oura-sync can resolve health_db
_SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Import modules with hyphens in their filenames via importlib
# ---------------------------------------------------------------------------

def _load_hyphenated_module(filename: str, module_name: str):
    """Load a .py file whose filename contains hyphens (not valid Python identifiers)."""
    spec = importlib.util.spec_from_file_location(
        module_name, _SCRIPTS_DIR / filename
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


blood_labs = _load_hyphenated_module("import-blood-labs.py", "import_blood_labs")
oura_sync = _load_hyphenated_module("oura-sync.py", "oura_sync")

parse_reference_range = blood_labs.parse_reference_range
date_chunks = oura_sync.date_chunks


# ---------------------------------------------------------------------------
# P2 — parse_reference_range()
# ---------------------------------------------------------------------------

class TestParseReferenceRange(unittest.TestCase):

    # --- Standard ranges ---

    def test_standard_range_returns_low_and_high(self):
        self.assertEqual(parse_reference_range("3.5-5.0"), (3.5, 5.0))

    def test_range_with_spaces_returns_low_and_high(self):
        self.assertEqual(parse_reference_range("3.5 - 5.0"), (3.5, 5.0))

    def test_en_dash_range_returns_low_and_high(self):
        # U+2013 en-dash — supported by the regex via [-–]
        self.assertEqual(parse_reference_range("3.5–5.0"), (3.5, 5.0))

    def test_integer_range_returns_floats(self):
        low, high = parse_reference_range("70-100")
        self.assertEqual(low, 70.0)
        self.assertEqual(high, 100.0)

    # --- Less-than ---

    def test_less_than_returns_none_low(self):
        low, high = parse_reference_range("<5.0")
        self.assertIsNone(low)
        self.assertEqual(high, 5.0)

    def test_less_than_with_space_returns_none_low(self):
        low, high = parse_reference_range("< 5.0")
        self.assertIsNone(low)
        self.assertEqual(high, 5.0)

    # --- Greater-than ---

    def test_greater_than_returns_none_high(self):
        low, high = parse_reference_range(">2.0")
        self.assertEqual(low, 2.0)
        self.assertIsNone(high)

    def test_greater_than_with_space_returns_none_high(self):
        low, high = parse_reference_range("> 2.0")
        self.assertEqual(low, 2.0)
        self.assertIsNone(high)

    # --- Empty / None / unrecognizable ---

    def test_empty_string_returns_none_none(self):
        self.assertEqual(parse_reference_range(""), (None, None))

    def test_none_input_returns_none_none(self):
        self.assertEqual(parse_reference_range(None), (None, None))

    def test_unrecognizable_text_returns_none_none(self):
        self.assertEqual(parse_reference_range("normal"), (None, None))

    def test_whitespace_only_returns_none_none(self):
        self.assertEqual(parse_reference_range("   "), (None, None))

    def test_word_range_returns_none_none(self):
        # e.g. "Negative" or "Positive" — not parseable as a numeric range
        self.assertEqual(parse_reference_range("Negative"), (None, None))

    # --- Return types ---

    def test_result_is_tuple_of_two(self):
        result = parse_reference_range("3.5-5.0")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_parsed_values_are_floats(self):
        low, high = parse_reference_range("3.5-5.0")
        self.assertIsInstance(low, float)
        self.assertIsInstance(high, float)


# ---------------------------------------------------------------------------
# P2 — date_chunks()
# ---------------------------------------------------------------------------

class TestDateChunks(unittest.TestCase):

    def test_range_within_chunk_size_returns_single_chunk(self):
        # 90-day window: 2026-01-01 to 2026-03-31 is exactly 89 days (< 90)
        chunks = date_chunks("2026-01-01", "2026-03-31", 90)
        self.assertEqual(len(chunks), 1)

    def test_single_chunk_has_correct_start(self):
        chunks = date_chunks("2026-01-01", "2026-03-31", 90)
        self.assertEqual(chunks[0][0], "2026-01-01")

    def test_single_chunk_has_correct_end(self):
        chunks = date_chunks("2026-01-01", "2026-03-31", 90)
        self.assertEqual(chunks[0][1], "2026-03-31")

    def test_range_spanning_two_chunks_returns_two_chunks(self):
        # Jan 1 to Jun 29 = 180 days = exactly 2 × 90-day chunks
        # (Jun 30 would be 181 days, producing a third 1-day chunk)
        chunks = date_chunks("2026-01-01", "2026-06-29", 90)
        self.assertEqual(len(chunks), 2)

    def test_two_chunks_first_start_is_range_start(self):
        chunks = date_chunks("2026-01-01", "2026-06-29", 90)
        self.assertEqual(chunks[0][0], "2026-01-01")

    def test_two_chunks_second_end_is_range_end(self):
        chunks = date_chunks("2026-01-01", "2026-06-29", 90)
        self.assertEqual(chunks[1][1], "2026-06-29")

    def test_two_chunks_are_contiguous_with_no_gaps(self):
        from datetime import date, timedelta
        chunks = date_chunks("2026-01-01", "2026-06-29", 90)
        first_end = date.fromisoformat(chunks[0][1])
        second_start = date.fromisoformat(chunks[1][0])
        self.assertEqual(second_start, first_end + timedelta(days=1))

    def test_same_start_and_end_returns_one_chunk(self):
        chunks = date_chunks("2026-01-01", "2026-01-01", 90)
        self.assertEqual(len(chunks), 1)

    def test_same_start_and_end_chunk_boundaries_match(self):
        chunks = date_chunks("2026-01-01", "2026-01-01", 90)
        self.assertEqual(chunks[0][0], "2026-01-01")
        self.assertEqual(chunks[0][1], "2026-01-01")

    def test_all_chunks_are_string_tuples(self):
        chunks = date_chunks("2026-01-01", "2026-06-30", 90)
        for start, end in chunks:
            with self.subTest(chunk=(start, end)):
                self.assertIsInstance(start, str)
                self.assertIsInstance(end, str)

    def test_chunk_end_never_exceeds_range_end(self):
        chunks = date_chunks("2026-01-01", "2026-06-30", 90)
        range_end = "2026-06-30"
        for start, end in chunks:
            with self.subTest(chunk=(start, end)):
                self.assertLessEqual(end, range_end)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
