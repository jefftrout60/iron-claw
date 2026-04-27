#!/usr/bin/env python3
"""
Unit tests for health_query.py.

All tests use an in-memory SQLite DB so they never touch health.db on disk.
health_db.get_connection is monkey-patched on the already-imported module
reference inside health_query so the patch takes effect transparently.
"""

import sys
import unittest
from pathlib import Path

# Make sure the health/ directory is on sys.path before importing either module
sys.path.insert(0, str(Path(__file__).parent))

import health_db
import health_query


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_conn():
    """Return an in-memory connection with the full schema initialised.

    We cannot pass ':memory:' to health_db.get_connection because it tries to
    call Path(':memory:').parent.mkdir(). Instead we open the connection
    directly and call initialize_schema ourselves, exactly as get_connection
    does minus the filesystem bits.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    health_db.initialize_schema(conn)
    return conn


def _populate(conn):
    """Insert standard fixture data into an in-memory connection."""
    # lab_markers
    conn.execute(
        "INSERT INTO lab_markers (name, canonical_unit) VALUES (?, ?)",
        ("Ferritin (ng/mL)", "ng/mL"),
    )
    marker_id = conn.execute(
        "SELECT id FROM lab_markers WHERE name = 'Ferritin (ng/mL)'"
    ).fetchone()["id"]

    # lab_results — two rows, different dates, well within 12 months
    conn.execute(
        """
        INSERT INTO lab_results (marker_id, date, value, reference_low, reference_high)
        VALUES (?, '2025-10-15', 40.0, 12.0, 150.0)
        """,
        (marker_id,),
    )
    conn.execute(
        """
        INSERT INTO lab_results (marker_id, date, value, reference_low, reference_high)
        VALUES (?, '2026-01-20', 55.0, 12.0, 150.0)
        """,
        (marker_id,),
    )

    # oura_daily — 3 rows
    for i, (day, sleep, readiness, steps) in enumerate([
        ("2026-04-25", 82, 78, 9500),
        ("2026-04-26", 75, 80, 11000),
        ("2026-04-27", 88, 85, 8200),
    ]):
        conn.execute(
            """
            INSERT INTO oura_daily (id, day, sleep_score, readiness_score, steps)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f"oura-{i}", day, sleep, readiness, steps),
        )

    # health_knowledge — 1 row whose summary contains "HRV"
    conn.execute(
        """
        INSERT INTO health_knowledge
            (id, show, episode_title, date, source, source_quality, summary)
        VALUES
            ('ep-001', 'Huberman Lab', 'Optimising HRV for Recovery',
             '2026-03-01', 'podcast', 'high',
             'HRV is a key marker of autonomic nervous system health.')
        """,
    )

    # Rebuild FTS index so snippet() works correctly
    conn.execute("INSERT INTO health_knowledge_fts(health_knowledge_fts) VALUES('rebuild')")
    conn.commit()

    return conn


# ---------------------------------------------------------------------------
# Base test class that patches get_connection before each test
# ---------------------------------------------------------------------------

class HealthQueryTestCase(unittest.TestCase):
    """Sets up a fresh in-memory DB and patches health_db.get_connection."""

    def setUp(self):
        self.conn = _make_conn()
        _populate(self.conn)
        # Patch on the module reference that health_query.py already imported
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()


# ---------------------------------------------------------------------------
# lab_trend tests
# ---------------------------------------------------------------------------

class TestLabTrend(HealthQueryTestCase):

    def test_happy_path_returns_expected_keys(self):
        result = health_query.lab_trend("ferritin", 12)
        for key in ("marker", "unit", "reference_low", "reference_high", "count", "data"):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_happy_path_values(self):
        result = health_query.lab_trend("ferritin", 12)
        self.assertEqual(result["marker"], "Ferritin (ng/mL)")
        self.assertEqual(result["unit"], "ng/mL")
        self.assertEqual(result["reference_low"], 12.0)
        self.assertEqual(result["reference_high"], 150.0)
        self.assertEqual(result["count"], 2)

    def test_data_sorted_ascending(self):
        result = health_query.lab_trend("ferritin", 12)
        dates = [row["date"] for row in result["data"]]
        self.assertEqual(dates, sorted(dates))

    def test_data_contains_date_and_value(self):
        result = health_query.lab_trend("ferritin", 12)
        row = result["data"][0]
        self.assertIn("date", row)
        self.assertIn("value", row)

    def test_case_insensitive_lookup(self):
        # LIKE is case-insensitive for ASCII in SQLite
        result = health_query.lab_trend("FERRITIN", 12)
        self.assertEqual(result["marker"], "Ferritin (ng/mL)")

    def test_unknown_marker_exits(self):
        with self.assertRaises(SystemExit):
            health_query.lab_trend("doesnotexist", 12)

    def test_no_data_in_window_exits(self):
        # months=0 means cutoff is today; both results are in the past
        with self.assertRaises(SystemExit):
            health_query.lab_trend("ferritin", 0)

    def test_marker_with_no_results_exits(self):
        # Insert a marker but give it no results
        self.conn.execute(
            "INSERT INTO lab_markers (name, canonical_unit) VALUES ('Vitamin D (ng/mL)', 'ng/mL')"
        )
        self.conn.commit()
        with self.assertRaises(SystemExit):
            health_query.lab_trend("Vitamin D", 12)


# ---------------------------------------------------------------------------
# oura_window tests
# ---------------------------------------------------------------------------

class TestOuraWindow(HealthQueryTestCase):

    def test_happy_path_all_cols_keys(self):
        result = health_query.oura_window(30, None, True)
        for key in ("days_requested", "days_available", "data", "averages"):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_happy_path_all_cols_values(self):
        result = health_query.oura_window(30, None, True)
        self.assertEqual(result["days_requested"], 30)
        self.assertEqual(result["days_available"], 3)
        self.assertIsInstance(result["data"], list)
        self.assertEqual(len(result["data"]), 3)
        self.assertIsInstance(result["averages"], dict)

    def test_averages_computed_for_numeric_cols(self):
        result = health_query.oura_window(30, None, True)
        # All 3 rows have sleep_score; average should be (82+75+88)/3
        expected_avg = round((82 + 75 + 88) / 3, 2)
        self.assertAlmostEqual(result["averages"]["sleep_score"], expected_avg, places=2)

    def test_metric_filter_returns_only_requested_columns(self):
        result = health_query.oura_window(30, "sleep_score", False)
        self.assertEqual(result["days_requested"], 30)
        self.assertEqual(result["days_available"], 3)
        for row in result["data"]:
            # Each row must have day and sleep_score; nothing extra
            self.assertIn("day", row)
            self.assertIn("sleep_score", row)
            # id, contributors_json, fetched_at are explicitly stripped
            self.assertNotIn("id", row)

    def test_unknown_metric_exits(self):
        with self.assertRaises(SystemExit):
            health_query.oura_window(7, "nonexistent_col", False)

    def test_empty_table_exits(self):
        # Use a fresh in-memory connection with no oura_daily rows
        empty_conn = _make_conn()  # schema only, no data
        health_query.health_db.get_connection = lambda *a, **kw: empty_conn
        try:
            with self.assertRaises(SystemExit):
                health_query.oura_window(7, None, True)
        finally:
            empty_conn.close()
            # Restore the populated connection for tearDown
            health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def test_days_requested_matches_argument(self):
        result = health_query.oura_window(30, "sleep_score", False)
        self.assertEqual(result["days_requested"], 30)


# ---------------------------------------------------------------------------
# search_knowledge tests
# ---------------------------------------------------------------------------

class TestSearchKnowledge(HealthQueryTestCase):

    def test_happy_path_keys(self):
        result = health_query.search_knowledge("HRV", 5)
        for key in ("query", "count", "results"):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_happy_path_returns_match(self):
        result = health_query.search_knowledge("HRV", 5)
        self.assertEqual(result["query"], "HRV")
        self.assertGreaterEqual(result["count"], 1)
        self.assertGreaterEqual(len(result["results"]), 1)

    def test_result_row_has_required_fields(self):
        result = health_query.search_knowledge("HRV", 5)
        row = result["results"][0]
        for field in ("show", "episode_title", "date", "snippet"):
            self.assertIn(field, row, f"Missing field: {field}")

    def test_result_values_correct(self):
        result = health_query.search_knowledge("HRV", 5)
        row = result["results"][0]
        self.assertEqual(row["show"], "Huberman Lab")
        self.assertEqual(row["episode_title"], "Optimising HRV for Recovery")
        self.assertEqual(row["date"], "2026-03-01")

    def test_zero_results_no_system_exit(self):
        # A query with no matches must return count=0 and empty list, not exit
        result = health_query.search_knowledge("zzznomatches999", 5)
        self.assertEqual(result["query"], "zzznomatches999")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["results"], [])

    def test_limit_respected(self):
        # Insert a second knowledge row matching "HRV"
        self.conn.execute(
            """
            INSERT INTO health_knowledge
                (id, show, episode_title, date, source, source_quality, summary)
            VALUES
                ('ep-002', 'Found My Fitness', 'HRV and Longevity',
                 '2026-03-15', 'podcast', 'high',
                 'HRV predicts long-term cardiovascular resilience.')
            """
        )
        self.conn.execute(
            "INSERT INTO health_knowledge_fts(health_knowledge_fts) VALUES('rebuild')"
        )
        self.conn.commit()

        result = health_query.search_knowledge("HRV", 1)
        self.assertLessEqual(len(result["results"]), 1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
