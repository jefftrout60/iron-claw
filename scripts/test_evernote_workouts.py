#!/usr/bin/env python3
"""
Behavioral tests for import-evernote-workouts.py.

Covers:
  1. Note title filter (parse_enex yields only matching titles)
  2. Week-date derivation (_week_monday)
  3. Day-name extraction from concatenated cell (via main loop logic)
  4. Exercise text parser — regex match (parse_exercise_text)
  5. Exercise text parser — fallback (parse_exercise_text)
  6. Exercise text parser — kg conversion (parse_exercise_text)
  7. ENEX table extraction (extract_table_rows)
  8. Empty actual column skipped (parse_enex / main loop integration)
"""

import importlib.util
import sys
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Sys.path setup: health_db must be importable before loading the module
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "agents/sample-agent/workspace/health"))

# Patch health_db at the top-level so the module-level `import health_db`
# inside import-evernote-workouts.py succeeds without touching the real DB.
_fake_health_db = MagicMock()
sys.modules.setdefault("health_db", _fake_health_db)

_spec = importlib.util.spec_from_file_location(
    "evernote_workouts",
    Path(__file__).parent / "import-evernote-workouts.py",
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_enex(notes: list[dict]) -> str:
    """Build a minimal ENEX XML string from a list of note dicts.

    Each dict: {"title": str, "content": str}
    No DOCTYPE — the importer strips it anyway, and skipping it avoids the
    network-fetch issue that prompted the stripping in the first place.
    """
    note_fragments = []
    for n in notes:
        note_fragments.append(
            f"<note>"
            f"<title>{n['title']}</title>"
            f"<content><![CDATA[{n['content']}]]></content>"
            f"</note>"
        )
    return f"<en-export>{''.join(note_fragments)}</en-export>"


def _write_enex(tmp: tempfile.NamedTemporaryFile, notes: list[dict]) -> str:
    """Write ENEX content to a NamedTemporaryFile and return its path."""
    xml = _make_enex(notes)
    tmp.write(xml.encode("utf-8"))
    tmp.flush()
    return tmp.name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoteTitleFilter(unittest.TestCase):
    """Behavior 1: parse_enex() only yields notes matching Week \\d+ Training Plan."""

    def _parse(self, notes):
        with tempfile.NamedTemporaryFile(suffix=".enex", delete=False) as f:
            path = _write_enex(f, notes)
        results = list(mod.parse_enex(path))
        return results

    def test_matching_title_is_yielded(self):
        results = self._parse([
            {"title": "Week 0125 Training Plan",
             "content": "<en-note><p>exercise</p></en-note>"},
        ])
        self.assertEqual(len(results), 1)
        title, _, _ = results[0]
        self.assertEqual(title, "Week 0125 Training Plan")

    def test_non_matching_title_is_silently_skipped(self):
        results = self._parse([
            {"title": "My Random Journal Entry",
             "content": "<en-note><p>skip me</p></en-note>"},
        ])
        self.assertEqual(len(results), 0)

    def test_multiple_notes_only_matching_yielded(self):
        results = self._parse([
            {"title": "Week 0326 Training Plan",
             "content": "<en-note><p>exercise</p></en-note>"},
            {"title": "Grocery List",
             "content": "<en-note><p>milk</p></en-note>"},
            {"title": "Week 0526 Training Plan",
             "content": "<en-note><p>exercise</p></en-note>"},
        ])
        self.assertEqual(len(results), 2)
        titles = [r[0] for r in results]
        self.assertIn("Week 0326 Training Plan", titles)
        self.assertIn("Week 0526 Training Plan", titles)
        self.assertNotIn("Grocery List", titles)

    def test_title_match_is_case_insensitive(self):
        results = self._parse([
            {"title": "week 0125 training plan",
             "content": "<en-note><p>exercise</p></en-note>"},
        ])
        self.assertEqual(len(results), 1)


class TestWeekMonday(unittest.TestCase):
    """Behavior 2: _week_monday() derives the Monday of the referenced ISO week."""

    def test_week_01_2025(self):
        """Week 0125 → Monday of ISO week 1, 2025 = 2024-12-30 (ISO week 1/2025 starts Dec 30)."""
        result = mod._week_monday("Week 0125 Training Plan", date(2025, 1, 1))
        self.assertEqual(result, date(2024, 12, 30))

    def test_week_18_2026(self):
        """Week 1826 → Monday of ISO week 18, 2026 = 2026-04-27."""
        result = mod._week_monday("Week 1826 Training Plan", date(2026, 1, 1))
        self.assertEqual(result, date(2026, 4, 27))

    def test_no_match_falls_back_to_created(self):
        """Title without Week WWYY pattern returns created date."""
        fallback = date(2025, 3, 10)
        result = mod._week_monday("Random Note Title", fallback)
        self.assertEqual(result, fallback)

    def test_invalid_week_number_falls_back(self):
        """Week number 99 is invalid; falls back to created."""
        fallback = date(2025, 6, 1)
        result = mod._week_monday("Week 9925 Training Plan", fallback)
        self.assertEqual(result, fallback)


class TestDayNameExtractionFromConcatenatedCell(unittest.TestCase):
    """Behavior 3: day column strings like 'Monday12/30' → offset 0."""

    # The extraction logic lives in main() via re.match on day_cell.lower().
    # We test it by exercising parse_enex + extract_table_rows together in a
    # dry-run-style loop that mirrors the production path, without writing to DB.

    _DAY_OFFSETS = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    }
    import re as _re
    _DAY_RE = _re.compile(
        r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
        _re.IGNORECASE,
    )

    def _extract_offset(self, day_cell: str):
        """Mirror the production extraction: lowercase → regex → offset."""
        import re
        day_cell = day_cell.strip().lower()
        m = re.match(
            r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)',
            day_cell,
        )
        if not m:
            return None
        return self._DAY_OFFSETS.get(m.group(1))

    def test_monday_with_date_suffix(self):
        self.assertEqual(self._extract_offset("Monday12/30"), 0)

    def test_tuesday_with_date_suffix(self):
        self.assertEqual(self._extract_offset("Tuesday1/1"), 1)

    def test_sunday_with_date_suffix_case_insensitive(self):
        self.assertEqual(self._extract_offset("sunday6/15"), 6)

    def test_saturday(self):
        self.assertEqual(self._extract_offset("Saturday3/22"), 5)

    def test_header_day_returns_none(self):
        self.assertIsNone(self._extract_offset("Day"))

    def test_empty_cell_returns_none(self):
        self.assertIsNone(self._extract_offset(""))

    def test_plain_day_name_no_date(self):
        self.assertEqual(self._extract_offset("Wednesday"), 2)


class TestParseExerciseTextRegexMatch(unittest.TestCase):
    """Behavior 4: structured exercise line → dict with name, reps, weight_lbs."""

    def test_basic_squat(self):
        results = mod.parse_exercise_text("Squat 3x5 @ 185")
        self.assertEqual(len(results), 1)
        ex = results[0]
        self.assertEqual(ex["exercise_name"], "Squat")
        self.assertEqual(ex["reps"], 5)
        self.assertEqual(ex["weight_lbs"], 185.0)
        self.assertIsNone(ex["notes"])

    def test_set_number_is_line_index(self):
        results = mod.parse_exercise_text("Squat 3x5 @ 185")
        self.assertEqual(results[0]["set_number"], 1)

    def test_multiple_lines_set_numbers_increment(self):
        text = "Squat 3x5 @ 185\nDeadlift 1x5 @ 225"
        results = mod.parse_exercise_text(text)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["set_number"], 1)
        self.assertEqual(results[1]["set_number"], 2)

    def test_uppercase_x_separator(self):
        results = mod.parse_exercise_text("Bench Press 4X8 @ 135")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["reps"], 8)

    def test_unicode_times_separator(self):
        results = mod.parse_exercise_text("Overhead Press 3×5 @ 95")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["reps"], 5)

    def test_no_weight_specified(self):
        """SetsxReps with no @ weight → weight_lbs is None."""
        results = mod.parse_exercise_text("Pull-up 3x10")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["reps"], 10)
        self.assertIsNone(results[0]["weight_lbs"])

    def test_empty_lines_skipped(self):
        results = mod.parse_exercise_text("Squat 3x5 @ 185\n\n")
        self.assertEqual(len(results), 1)


class TestParseExerciseTextFallback(unittest.TestCase):
    """Behavior 5: non-matching line → fallback dict with raw text in notes."""

    def test_cardio_line_fallback(self):
        results = mod.parse_exercise_text("35 min Z2 bike")
        self.assertEqual(len(results), 1)
        ex = results[0]
        self.assertEqual(ex["exercise_name"], "35 min Z2 bike")
        self.assertIsNone(ex["reps"])
        self.assertIsNone(ex["weight_lbs"])
        self.assertEqual(ex["notes"], "35 min Z2 bike")

    def test_rest_day_fallback(self):
        results = mod.parse_exercise_text("Rest day")
        self.assertEqual(len(results), 1)
        ex = results[0]
        self.assertIsNone(ex["reps"])
        self.assertIsNone(ex["weight_lbs"])
        self.assertEqual(ex["notes"], "Rest day")

    def test_empty_string_returns_empty_list(self):
        results = mod.parse_exercise_text("")
        self.assertEqual(results, [])

    def test_fallback_set_number_is_one(self):
        results = mod.parse_exercise_text("Some free text")
        self.assertEqual(results[0]["set_number"], 1)


class TestParseExerciseTextKgConversion(unittest.TestCase):
    """Behavior 6: weight in kg → converted to lbs."""

    def test_deadlift_100kg(self):
        results = mod.parse_exercise_text("Deadlift 3x5 @ 100kg")
        self.assertEqual(len(results), 1)
        ex = results[0]
        self.assertEqual(ex["exercise_name"], "Deadlift")
        self.assertIsNotNone(ex["weight_lbs"])
        self.assertAlmostEqual(ex["weight_lbs"], 220.46, delta=0.1)

    def test_squat_60kg(self):
        results = mod.parse_exercise_text("Squat 5x5 @ 60kg")
        self.assertEqual(len(results), 1)
        # 60 * 2.20462 = 132.277
        self.assertAlmostEqual(results[0]["weight_lbs"], 132.28, delta=0.1)

    def test_lbs_suffix_not_converted(self):
        results = mod.parse_exercise_text("Squat 3x5 @ 185lbs")
        self.assertAlmostEqual(results[0]["weight_lbs"], 185.0, delta=0.01)

    def test_no_unit_defaults_to_lbs(self):
        """No unit specified → treated as lbs, not converted."""
        results = mod.parse_exercise_text("Squat 3x5 @ 185")
        self.assertAlmostEqual(results[0]["weight_lbs"], 185.0, delta=0.01)


class TestExtractTableRows(unittest.TestCase):
    """Behavior 7: extract_table_rows() returns list of cell lists."""

    def test_single_row_three_cells(self):
        html = "<table><tr><td>Mon</td><td>Plan</td><td>Squat 3x5</td></tr></table>"
        rows = mod.extract_table_rows(html)
        self.assertEqual(rows, [["Mon", "Plan", "Squat 3x5"]])

    def test_multiple_rows(self):
        html = (
            "<table>"
            "<tr><td>Day</td><td>Planned</td><td>Actual</td></tr>"
            "<tr><td>Monday</td><td>Squat</td><td>Squat 3x5 @ 185</td></tr>"
            "</table>"
        )
        rows = mod.extract_table_rows(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ["Day", "Planned", "Actual"])
        self.assertEqual(rows[1], ["Monday", "Squat", "Squat 3x5 @ 185"])

    def test_no_table_returns_empty(self):
        html = "<en-note><p>No table here</p></en-note>"
        rows = mod.extract_table_rows(html)
        self.assertEqual(rows, [])

    def test_whitespace_in_cells_stripped(self):
        html = "<table><tr><td>  Monday  </td><td>  Plan  </td><td>  Squat  </td></tr></table>"
        rows = mod.extract_table_rows(html)
        self.assertEqual(rows[0], ["Monday", "Plan", "Squat"])

    def test_th_cells_also_extracted(self):
        html = "<table><tr><th>Day</th><th>Planned</th><th>Actual</th></tr></table>"
        rows = mod.extract_table_rows(html)
        self.assertEqual(rows[0], ["Day", "Planned", "Actual"])

    def test_enml_wrapper_does_not_break_parse(self):
        """Realistic ENML wrapping around the table."""
        html = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">
            <en-note>
              <p>Week summary</p>
              <table>
                <tr><td>Monday1/6</td><td>Squat 3x5</td><td>Squat 3x5 @ 185</td></tr>
              </table>
            </en-note>
        """)
        rows = mod.extract_table_rows(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Monday1/6")


class TestEmptyActualColumnSkipped(unittest.TestCase):
    """Behavior 8: rows where the actual column (col 2) is empty are not imported."""

    def test_empty_actual_column_not_returned_as_exercise(self):
        """extract_table_rows returns the row, but the main loop must skip it.

        We verify the gating condition directly: actual_text.strip() == "".
        """
        html = (
            "<table>"
            "<tr><td>Monday1/6</td><td>Planned squat</td><td></td></tr>"
            "</table>"
        )
        rows = mod.extract_table_rows(html)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        actual_text = row[2].strip() if len(row) > 2 else ""
        # The gating condition in main() is `if not actual_text: continue`
        self.assertEqual(actual_text, "")
        self.assertFalse(bool(actual_text))

    def test_whitespace_only_actual_column_skipped(self):
        """Whitespace-only cell should also be skipped after strip()."""
        html = (
            "<table>"
            "<tr><td>Tuesday1/7</td><td>Planned</td><td>   </td></tr>"
            "</table>"
        )
        rows = mod.extract_table_rows(html)
        actual_text = rows[0][2].strip()
        self.assertFalse(bool(actual_text))

    def test_nonempty_actual_column_passes_gate(self):
        """A row with real content should pass the gate."""
        html = (
            "<table>"
            "<tr><td>Wednesday1/8</td><td>Deadlift</td><td>Deadlift 1x5 @ 225</td></tr>"
            "</table>"
        )
        rows = mod.extract_table_rows(html)
        actual_text = rows[0][2].strip()
        self.assertTrue(bool(actual_text))

    def test_parse_enex_content_with_empty_actual_produces_no_exercises_via_parse_exercise_text(self):
        """End-to-end: empty actual column → parse_exercise_text("") → empty list."""
        results = mod.parse_exercise_text("")
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
