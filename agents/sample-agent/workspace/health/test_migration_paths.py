#!/usr/bin/env python3
"""
Tests for health_db schema migration paths.

Verifies that initialize_schema correctly upgrades a DB from any prior version
to the current SCHEMA_VERSION (6), and that all column patches are idempotent.

Strategy for incremental-path tests:
  1. Open an in-memory DB and call initialize_schema → reaches v6.
  2. Stamp the DB back to version N with PRAGMA user_version = N.
  3. Drop tables that were added in versions > N.
  4. Call initialize_schema again — it must apply only the missing migrations.
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import health_db


# ---------------------------------------------------------------------------
# Helper: tables introduced in each version (excluding v1 base tables)
# ---------------------------------------------------------------------------

# Tables added per version (used by _conn_at_version to decide what to drop)
_TABLES_ADDED_BY_VERSION = {
    5: ["state_of_mind"],
    4: ["workout_exercises", "oura_tags"],
    3: ["activity_daily", "workouts"],
    2: ["body_metrics"],
}

# All tables expected in a fully-migrated v6 DB
_ALL_V6_TABLES = {
    # v1 base tables
    "health_knowledge",
    "lab_markers",
    "lab_results",
    "oura_daily",
    "oura_sleep_sessions",
    "oura_heartrate",
    "sync_state",
    "blood_pressure",
    # v2
    "body_metrics",
    # v3
    "activity_daily",
    "workouts",
    # v4
    "workout_exercises",
    "oura_tags",
    # v5
    "state_of_mind",
    # v6: no new tables — only new columns + FTS rebuild
}


def _list_tables(conn: sqlite3.Connection) -> set:
    """Return set of user-created table names in the connection."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {row[0] for row in rows}


def _conn_at_version(n: int) -> sqlite3.Connection:
    """
    Return an in-memory DB that looks like it was last migrated to version N.

    Steps:
      1. Create in-memory DB, run initialize_schema → reaches v6.
      2. Stamp version down to N.
      3. Drop tables introduced in versions > N so the DB truly resembles a v-N DB.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")   # allow dropping tables with FK refs
    conn.row_factory = sqlite3.Row
    health_db.initialize_schema(conn)

    # Stamp down
    conn.execute(f"PRAGMA user_version = {n}")
    conn.commit()

    # Drop tables added in versions > N (highest version first to respect FK order)
    for ver in sorted(_TABLES_ADDED_BY_VERSION.keys(), reverse=True):
        if ver > n:
            for table in _TABLES_ADDED_BY_VERSION[ver]:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()

    # Re-enable FK enforcement for the tests
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFreshDBReachesV6(unittest.TestCase):
    """An empty in-memory DB must reach version 6 with all tables present."""

    def test_fresh_db_reaches_v6(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, health_db.SCHEMA_VERSION)
        self.assertEqual(version, 6)

        tables = _list_tables(conn)
        # Every table including state_of_mind must exist
        for table in _ALL_V6_TABLES:
            self.assertIn(table, tables, f"Missing table after fresh init: {table}")


class TestV2ToV6(unittest.TestCase):
    """A v2 DB (has body_metrics, missing v3-v6 tables) must upgrade to v6."""

    def test_v2_to_v6(self):
        conn = _conn_at_version(2)

        # Confirm the setup looks like v2: body_metrics present, v3+ absent
        tables_before = _list_tables(conn)
        self.assertIn("body_metrics", tables_before)
        for table in ["activity_daily", "workouts", "workout_exercises",
                      "oura_tags", "state_of_mind"]:
            self.assertNotIn(table, tables_before,
                             f"Setup error: {table} should not exist at v2")

        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 6)

        tables_after = _list_tables(conn)
        for table in ["workout_exercises", "oura_tags", "state_of_mind"]:
            self.assertIn(table, tables_after,
                          f"Missing table after v2→v6 migration: {table}")


class TestV3ToV6(unittest.TestCase):
    """A v3 DB (has activity_daily/workouts, missing v4-v6 tables) must upgrade to v6."""

    def test_v3_to_v6(self):
        conn = _conn_at_version(3)

        tables_before = _list_tables(conn)
        self.assertIn("activity_daily", tables_before)
        self.assertIn("workouts", tables_before)
        for table in ["workout_exercises", "oura_tags", "state_of_mind"]:
            self.assertNotIn(table, tables_before,
                             f"Setup error: {table} should not exist at v3")

        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 6)

        tables_after = _list_tables(conn)
        for table in ["workout_exercises", "oura_tags", "state_of_mind"]:
            self.assertIn(table, tables_after,
                          f"Missing table after v3→v6 migration: {table}")


class TestV4ToV6(unittest.TestCase):
    """A v4 DB (has workout_exercises/oura_tags, missing state_of_mind) must upgrade to v6."""

    def test_v4_to_v6(self):
        conn = _conn_at_version(4)

        tables_before = _list_tables(conn)
        self.assertIn("workout_exercises", tables_before)
        self.assertIn("oura_tags", tables_before)
        self.assertNotIn("state_of_mind", tables_before,
                         "Setup error: state_of_mind should not exist at v4")

        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 6)

        tables_after = _list_tables(conn)
        self.assertIn("state_of_mind", tables_after,
                      "Missing state_of_mind after v4→v6 migration")


class TestIdempotentColumnPatches(unittest.TestCase):
    """Calling initialize_schema twice on a v6 DB must not raise any error."""

    def test_idempotent_column_patches(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        health_db.initialize_schema(conn)

        # Second call — all CREATE IF NOT EXISTS and try/except ALTER TABLE
        # patches must be silent
        try:
            health_db.initialize_schema(conn)
        except Exception as exc:
            self.fail(f"initialize_schema raised on second call: {exc}")

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 6)


class TestSyncHelpersAvailable(unittest.TestCase):
    """get_last_synced and set_last_synced must be accessible as module functions."""

    def test_sync_helpers_available(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        health_db.initialize_schema(conn)

        # Initially no record → returns the supplied default
        result = health_db.get_last_synced(conn, "oura_daily", default="never")
        self.assertEqual(result, "never")

        # Write a value and read it back
        health_db.set_last_synced(conn, "oura_daily", "2026-04-30T00:00:00Z")
        result = health_db.get_last_synced(conn, "oura_daily")
        self.assertEqual(result, "2026-04-30T00:00:00Z")


class TestBackfillDailyHRV(unittest.TestCase):
    """
    Behavioral tests for health_db.backfill_daily_hrv.

    Session selection rules:
      1. Prefer type='long_sleep' over any other type.
      2. Among sessions of equal priority, pick the one with the greatest
         total_sleep_sec.
      3. Skip sessions where avg_hrv IS NULL (both in ORDER and the EXISTS guard).
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_conn(self) -> sqlite3.Connection:
        """Return a fresh in-memory DB with the full v5 schema."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        health_db.initialize_schema(conn)
        return conn

    def _insert_daily(self, conn, id_, day, avg_hrv_rmssd=None):
        """Insert a minimal oura_daily row."""
        conn.execute(
            "INSERT INTO oura_daily (id, day, avg_hrv_rmssd) VALUES (?, ?, ?)",
            (id_, day, avg_hrv_rmssd),
        )
        conn.commit()

    def _insert_session(self, conn, id_, day, type_, avg_hrv, total_sleep_sec=28800):
        """Insert a minimal oura_sleep_sessions row (NOT NULL cols filled with stubs)."""
        conn.execute(
            """INSERT INTO oura_sleep_sessions
               (id, day, type, bedtime_start, bedtime_end, avg_hrv, total_sleep_sec)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                id_,
                day,
                type_,
                f"{day}T22:00:00",   # bedtime_start NOT NULL
                f"{day}T06:00:00",   # bedtime_end NOT NULL
                avg_hrv,
                total_sleep_sec,
            ),
        )
        conn.commit()

    def _get_hrv(self, conn, day):
        """Fetch avg_hrv_rmssd for a given day."""
        row = conn.execute(
            "SELECT avg_hrv_rmssd FROM oura_daily WHERE day = ?", (day,)
        ).fetchone()
        if row is None:
            return None
        return row["avg_hrv_rmssd"]

    # ------------------------------------------------------------------
    # Test 1: basic backfill
    # ------------------------------------------------------------------

    def test_basic_backfill_writes_hrv(self):
        """A NULL avg_hrv_rmssd is populated from a matching sleep session."""
        conn = self._make_conn()
        self._insert_daily(conn, "d1", "2026-04-01", avg_hrv_rmssd=None)
        self._insert_session(conn, "s1", "2026-04-01", "long_sleep", avg_hrv=45.0)

        health_db.backfill_daily_hrv(conn)

        self.assertAlmostEqual(self._get_hrv(conn, "2026-04-01"), 45.0)

    # ------------------------------------------------------------------
    # Test 2: long_sleep wins over longer nap
    # ------------------------------------------------------------------

    def test_prefers_long_sleep_over_longer_nap(self):
        """long_sleep session wins even when a nap has more total_sleep_sec."""
        conn = self._make_conn()
        self._insert_daily(conn, "d1", "2026-04-02")
        # nap is longer in duration but should lose to long_sleep
        self._insert_session(conn, "s_nap",  "2026-04-02", "nap",        avg_hrv=30.0, total_sleep_sec=30000)
        self._insert_session(conn, "s_long", "2026-04-02", "long_sleep", avg_hrv=50.0, total_sleep_sec=25000)

        health_db.backfill_daily_hrv(conn)

        self.assertAlmostEqual(self._get_hrv(conn, "2026-04-02"), 50.0,
                               msg="long_sleep HRV should win over longer nap HRV")

    # ------------------------------------------------------------------
    # Test 3: longest session wins when no long_sleep
    # ------------------------------------------------------------------

    def test_falls_back_to_longest_session_when_no_long_sleep(self):
        """When no long_sleep exists, the session with greatest total_sleep_sec wins."""
        conn = self._make_conn()
        self._insert_daily(conn, "d1", "2026-04-03")
        self._insert_session(conn, "s_short", "2026-04-03", "nap", avg_hrv=20.0, total_sleep_sec=3600)
        self._insert_session(conn, "s_long",  "2026-04-03", "nap", avg_hrv=55.0, total_sleep_sec=7200)

        health_db.backfill_daily_hrv(conn)

        self.assertAlmostEqual(self._get_hrv(conn, "2026-04-03"), 55.0,
                               msg="Longer nap's HRV should win when no long_sleep exists")

    # ------------------------------------------------------------------
    # Test 4: NULL avg_hrv in best-candidate session skips to next
    # ------------------------------------------------------------------

    def test_null_hrv_in_best_session_falls_through_to_next(self):
        """long_sleep with NULL avg_hrv is skipped; nap's non-NULL HRV is used."""
        conn = self._make_conn()
        self._insert_daily(conn, "d1", "2026-04-04")
        # long_sleep but no HRV recorded → must be ignored
        self._insert_session(conn, "s_long", "2026-04-04", "long_sleep", avg_hrv=None,  total_sleep_sec=28800)
        self._insert_session(conn, "s_nap",  "2026-04-04", "nap",        avg_hrv=40.0, total_sleep_sec=3600)

        health_db.backfill_daily_hrv(conn)

        self.assertAlmostEqual(self._get_hrv(conn, "2026-04-04"), 40.0,
                               msg="Nap HRV should be used when long_sleep has NULL avg_hrv")

    # ------------------------------------------------------------------
    # Test 5: idempotent — calling twice gives same result
    # ------------------------------------------------------------------

    def test_idempotent(self):
        """Calling backfill_daily_hrv twice produces the same result as once."""
        conn = self._make_conn()
        self._insert_daily(conn, "d1", "2026-04-05")
        self._insert_session(conn, "s1", "2026-04-05", "long_sleep", avg_hrv=62.0)

        health_db.backfill_daily_hrv(conn)
        hrv_first = self._get_hrv(conn, "2026-04-05")

        health_db.backfill_daily_hrv(conn)
        hrv_second = self._get_hrv(conn, "2026-04-05")

        self.assertAlmostEqual(hrv_first, hrv_second,
                               msg="backfill_daily_hrv must be idempotent")

    # ------------------------------------------------------------------
    # Test 6: sleep session with no matching oura_daily row causes no error
    # ------------------------------------------------------------------

    def test_orphan_session_no_daily_row_no_error(self):
        """A sleep session whose day has no oura_daily row must not raise."""
        conn = self._make_conn()
        # No oura_daily row inserted for this day
        self._insert_session(conn, "s_orphan", "2026-04-06", "long_sleep", avg_hrv=55.0)

        try:
            health_db.backfill_daily_hrv(conn)
        except Exception as exc:
            self.fail(f"backfill_daily_hrv raised unexpectedly: {exc}")

        # Confirm no daily row was created as a side-effect
        row = conn.execute(
            "SELECT 1 FROM oura_daily WHERE day = '2026-04-06'"
        ).fetchone()
        self.assertIsNone(row, "No oura_daily row should be created for an orphan session")

    # ------------------------------------------------------------------
    # Test 7: oura_daily row with no sessions stays NULL
    # ------------------------------------------------------------------

    def test_no_matching_session_leaves_hrv_null(self):
        """An oura_daily row with no sleep sessions keeps avg_hrv_rmssd = NULL."""
        conn = self._make_conn()
        self._insert_daily(conn, "d1", "2026-04-07", avg_hrv_rmssd=None)
        # No sessions for this day

        health_db.backfill_daily_hrv(conn)

        self.assertIsNone(self._get_hrv(conn, "2026-04-07"),
                          "avg_hrv_rmssd should remain NULL when no sessions exist for the day")


if __name__ == "__main__":
    unittest.main()
