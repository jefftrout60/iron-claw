#!/usr/bin/env python3
"""
Unit tests for health_query.blood_pressure().

All tests use an in-memory SQLite DB — never touches health.db on disk.
health_db.get_connection is monkey-patched on the module reference that
health_query already imported, so the patch takes effect transparently.
"""

import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import health_db
import health_query


# ---------------------------------------------------------------------------
# Shared fixture helpers (same pattern as test_health_query.py)
# ---------------------------------------------------------------------------

def _make_conn():
    """Return an in-memory connection with the full schema initialised."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    health_db.initialize_schema(conn)
    return conn


def _insert_bp(conn, date_str, time_str, systolic, diastolic, pulse=72):
    conn.execute(
        "INSERT INTO blood_pressure (date, time, systolic, diastolic, pulse)"
        " VALUES (?, ?, ?, ?, ?)",
        (date_str, time_str, systolic, diastolic, pulse),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Base test class that patches get_connection before each test
# ---------------------------------------------------------------------------

class BPTestCase(unittest.TestCase):
    """Sets up a fresh in-memory DB and patches health_db.get_connection."""

    def setUp(self):
        self.conn = _make_conn()
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()


# ---------------------------------------------------------------------------
# Happy path + session grouping
# ---------------------------------------------------------------------------

class TestBloodPressureHappyPath(BPTestCase):
    """4 readings across 2 days collapse into 2 sessions."""

    def setUp(self):
        super().setUp()
        # Jan 21: 08:54 and 09:08 — gap = 14 min  → 1 session
        _insert_bp(self.conn, "2026-01-21", "08:54:00", 118, 76, 64)
        _insert_bp(self.conn, "2026-01-21", "09:08:00", 122, 78, 66)
        # Feb 28: 11:36 and 11:52 — gap = 16 min  → 1 session
        _insert_bp(self.conn, "2026-02-28", "11:36:00", 126, 80, 70)
        _insert_bp(self.conn, "2026-02-28", "11:52:00", 130, 82, 72)

    def test_session_count_is_two(self):
        result = health_query.blood_pressure(30, "2026-01-01", "2026-12-31")
        self.assertEqual(result["sessions"], 2)

    def test_session_one_avg_systolic(self):
        result = health_query.blood_pressure(30, "2026-01-01", "2026-12-31")
        jan_session = result["data"][0]
        # (118 + 122) / 2 = 120.0
        expected = round((118 + 122) / 2, 1)
        self.assertAlmostEqual(jan_session["avg_systolic"], expected, places=1)

    def test_summary_keys_present(self):
        result = health_query.blood_pressure(30, "2026-01-01", "2026-12-31")
        for key in ("avg_systolic", "avg_diastolic", "avg_pulse",
                    "min_systolic", "max_systolic"):
            self.assertIn(key, result["summary"], f"Missing summary key: {key}")

    def test_top_level_keys_present(self):
        result = health_query.blood_pressure(30, "2026-01-01", "2026-12-31")
        for key in ("days_requested", "sessions", "data", "summary"):
            self.assertIn(key, result, f"Missing top-level key: {key}")


# ---------------------------------------------------------------------------
# Date filter via --start / --end
# ---------------------------------------------------------------------------

class TestBloodPressureDateFilter(BPTestCase):
    """start/end arguments correctly isolate readings by calendar range."""

    def setUp(self):
        super().setUp()
        _insert_bp(self.conn, "2026-01-21", "08:54:00", 118, 76, 64)
        _insert_bp(self.conn, "2026-01-21", "09:08:00", 122, 78, 66)
        _insert_bp(self.conn, "2026-02-28", "11:36:00", 126, 80, 70)
        _insert_bp(self.conn, "2026-02-28", "11:52:00", 130, 82, 72)

    def test_feb_only_returns_one_session(self):
        result = health_query.blood_pressure(30, "2026-02-01", "2026-02-28")
        self.assertEqual(result["sessions"], 1)

    def test_feb_only_date_is_feb(self):
        result = health_query.blood_pressure(30, "2026-02-01", "2026-02-28")
        self.assertEqual(result["data"][0]["date"], "2026-02-28")

    def test_jan_only_returns_one_session(self):
        result = health_query.blood_pressure(30, "2026-01-01", "2026-01-31")
        self.assertEqual(result["sessions"], 1)

    def test_jan_only_date_is_jan(self):
        result = health_query.blood_pressure(30, "2026-01-01", "2026-01-31")
        self.assertEqual(result["data"][0]["date"], "2026-01-21")


# ---------------------------------------------------------------------------
# Date filter via --days (deterministic today)
# ---------------------------------------------------------------------------

class TestBloodPressureDaysFilter(BPTestCase):
    """--days window is computed relative to a patched date.today()."""

    def setUp(self):
        super().setUp()
        _insert_bp(self.conn, "2026-01-21", "08:54:00", 118, 76, 64)
        _insert_bp(self.conn, "2026-01-21", "09:08:00", 122, 78, 66)
        _insert_bp(self.conn, "2026-02-28", "11:36:00", 126, 80, 70)
        _insert_bp(self.conn, "2026-02-28", "11:52:00", 130, 82, 72)

    def test_7_days_from_feb28_returns_only_feb_readings(self):
        # today = 2026-02-28 → cutoff = 2026-02-21 → only Feb 28 readings qualify
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = health_query.blood_pressure(7, None, None)

        self.assertEqual(result["sessions"], 1)
        self.assertEqual(result["data"][0]["date"], "2026-02-28")

    def test_7_days_from_feb28_excludes_jan_readings(self):
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 28)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = health_query.blood_pressure(7, None, None)

        dates_returned = [s["date"] for s in result["data"]]
        self.assertNotIn("2026-01-21", dates_returned)


# ---------------------------------------------------------------------------
# Session boundary: 30-minute gap threshold
# ---------------------------------------------------------------------------

class TestBloodPressureSessionBoundary(BPTestCase):
    """Readings within 30 min merge; readings over 30 min split."""

    def test_29_min_gap_is_same_session(self):
        _insert_bp(self.conn, "2026-03-01", "09:00:00", 120, 78, 68)
        _insert_bp(self.conn, "2026-03-01", "09:29:00", 122, 80, 70)
        result = health_query.blood_pressure(30, "2026-03-01", "2026-03-01")
        self.assertEqual(result["sessions"], 1)

    def test_31_min_gap_is_separate_sessions(self):
        _insert_bp(self.conn, "2026-03-01", "09:00:00", 120, 78, 68)
        _insert_bp(self.conn, "2026-03-01", "09:31:00", 122, 80, 70)
        result = health_query.blood_pressure(30, "2026-03-01", "2026-03-01")
        self.assertEqual(result["sessions"], 2)

    def test_exactly_30_min_gap_is_same_session(self):
        # gap == 30 → condition is gap <= 30 → still same session
        _insert_bp(self.conn, "2026-03-01", "09:00:00", 120, 78, 68)
        _insert_bp(self.conn, "2026-03-01", "09:30:00", 122, 80, 70)
        result = health_query.blood_pressure(30, "2026-03-01", "2026-03-01")
        self.assertEqual(result["sessions"], 1)


# ---------------------------------------------------------------------------
# Empty table
# ---------------------------------------------------------------------------

class TestBloodPressureEmptyTable(BPTestCase):
    """blood_pressure() should call _err() → SystemExit when no rows found."""

    def test_empty_table_raises_system_exit(self):
        with self.assertRaises(SystemExit):
            health_query.blood_pressure(30, None, None)

    def test_date_range_with_no_data_raises_system_exit(self):
        # Insert a reading outside the requested window
        _insert_bp(self.conn, "2025-01-01", "09:00:00", 118, 76, 64)
        with self.assertRaises(SystemExit):
            health_query.blood_pressure(30, "2026-06-01", "2026-06-30")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
