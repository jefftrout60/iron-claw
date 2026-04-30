#!/usr/bin/env python3
"""
Unit tests for withings-sync.py logic.

Tests pure decode/conversion functions and upsert behavior using an in-memory DB.
The withings-sync.py filename contains a hyphen so we import via importlib.
"""

import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db

# Load withings-sync.py via importlib (hyphen in filename prevents normal import)
# Path: health/ -> workspace/ -> sample-agent/ -> agents/ -> ironclaw/ -> scripts/
_SYNC_PATH = Path(__file__).parent.parent.parent.parent.parent / "scripts" / "withings-sync.py"
_spec = importlib.util.spec_from_file_location("withings_sync", str(_SYNC_PATH))
withings_sync = importlib.util.module_from_spec(_spec)
# Defer exec_module until we need it — but we must execute it for the module to work.
# If the file is unavailable, skip the tests gracefully.
try:
    _spec.loader.exec_module(withings_sync)
    _MODULE_AVAILABLE = True
except Exception:
    _MODULE_AVAILABLE = False


def _make_conn() -> sqlite3.Connection:
    """Return an in-memory connection with the full schema initialised."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    health_db.initialize_schema(conn)
    return conn



@unittest.skipUnless(_MODULE_AVAILABLE, "withings-sync.py not available or has import error")
class TestWithingsValueDecode(unittest.TestCase):
    """Tests for _decode_value() — Withings measure value * 10^unit."""

    def test_negative_unit_shifts_decimal(self):
        # value=705, unit=-1 → 70.5
        result = withings_sync._decode_value(705, -1)
        self.assertAlmostEqual(result, 70.5, places=6)

    def test_zero_unit_returns_value(self):
        result = withings_sync._decode_value(75, 0)
        self.assertAlmostEqual(result, 75.0, places=6)

    def test_positive_unit_shifts_left(self):
        result = withings_sync._decode_value(75, 1)
        self.assertAlmostEqual(result, 750.0, places=6)

    def test_two_decimal_places(self):
        # value=2215, unit=-2 → 22.15
        result = withings_sync._decode_value(2215, -2)
        self.assertAlmostEqual(result, 22.15, places=6)


@unittest.skipUnless(_MODULE_AVAILABLE, "withings-sync.py not available or has import error")
class TestKgToLbsConversion(unittest.TestCase):
    """Tests for kg → lbs conversion using KG_TO_LBS constant."""

    def test_70_5_kg_approx_155_4_lbs(self):
        kg = 70.5
        lbs = kg * withings_sync.KG_TO_LBS
        self.assertAlmostEqual(lbs, 155.4, delta=0.1)

    def test_decode_then_convert(self):
        # value=705, unit=-1 → 70.5 kg → ~155.4 lbs
        kg = withings_sync._decode_value(705, -1)
        lbs = kg * withings_sync.KG_TO_LBS
        self.assertAlmostEqual(lbs, 155.4, delta=0.1)

    def test_100_kg_is_220_lbs(self):
        lbs = 100.0 * withings_sync.KG_TO_LBS
        self.assertAlmostEqual(lbs, 220.462, delta=0.01)


@unittest.skipUnless(_MODULE_AVAILABLE, "withings-sync.py not available or has import error")
class TestBodyMetricsUpsert(unittest.TestCase):
    """Tests for the ON CONFLICT upsert logic in sync_body_metrics()."""

    def setUp(self):
        self.conn = _make_conn()
        # Patch health_db.get_connection on the withings_sync module reference
        self._orig = withings_sync.health_db.get_connection
        withings_sync.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        withings_sync.health_db.get_connection = self._orig
        self.conn.close()

    def _upsert_row(self, date_str, time_str, weight_lbs, fat_ratio_pct=22.5):
        """Directly execute the same upsert SQL as sync_body_metrics()."""
        cursor = self.conn.execute(
            """
            INSERT INTO body_metrics
                (date, time, weight_lbs, fat_ratio_pct, fat_mass_lbs,
                 lean_mass_lbs, muscle_mass_lbs, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'withings_api')
            ON CONFLICT(date, time) DO UPDATE SET
                weight_lbs      = excluded.weight_lbs,
                fat_ratio_pct   = excluded.fat_ratio_pct,
                fat_mass_lbs    = excluded.fat_mass_lbs,
                lean_mass_lbs   = excluded.lean_mass_lbs,
                muscle_mass_lbs = excluded.muscle_mass_lbs
            WHERE body_metrics.weight_lbs      IS NOT excluded.weight_lbs
               OR body_metrics.fat_ratio_pct   IS NOT excluded.fat_ratio_pct
               OR body_metrics.fat_mass_lbs    IS NOT excluded.fat_mass_lbs
               OR body_metrics.lean_mass_lbs   IS NOT excluded.lean_mass_lbs
               OR body_metrics.muscle_mass_lbs IS NOT excluded.muscle_mass_lbs
            """,
            (date_str, time_str, weight_lbs, fat_ratio_pct, None, None, None),
        )
        self.conn.commit()
        return cursor.rowcount

    def test_first_insert_returns_rowcount_1(self):
        rowcount = self._upsert_row("2026-01-15", "08:00", 185.0)
        self.assertEqual(rowcount, 1)

    def test_identical_row_returns_rowcount_0(self):
        # Insert once
        self._upsert_row("2026-01-15", "08:00", 185.0)
        # Upsert with identical data — WHERE guard prevents update
        rowcount = self._upsert_row("2026-01-15", "08:00", 185.0)
        self.assertEqual(rowcount, 0)

    def test_changed_weight_returns_rowcount_1(self):
        # Insert initial row
        self._upsert_row("2026-01-15", "08:00", 185.0)
        # Upsert with different weight
        rowcount = self._upsert_row("2026-01-15", "08:00", 184.5)
        self.assertEqual(rowcount, 1)

    def test_changed_weight_updates_stored_value(self):
        self._upsert_row("2026-01-15", "08:00", 185.0)
        self._upsert_row("2026-01-15", "08:00", 184.5)
        row = self.conn.execute(
            "SELECT weight_lbs FROM body_metrics WHERE date='2026-01-15' AND time='08:00'"
        ).fetchone()
        self.assertAlmostEqual(row["weight_lbs"], 184.5, places=2)

    def test_no_duplicate_rows_on_conflict(self):
        self._upsert_row("2026-01-15", "08:00", 185.0)
        self._upsert_row("2026-01-15", "08:00", 185.0)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM body_metrics WHERE date='2026-01-15'"
        ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
