#!/usr/bin/env python3
"""
Tests for health_db schema migration paths.

Verifies that initialize_schema correctly upgrades a DB from any prior version
to the current SCHEMA_VERSION (7), and that all column patches are idempotent.

Strategy for incremental-path tests:
  1. Open an in-memory DB and call initialize_schema → reaches v7.
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

# All tables expected in a fully-migrated v7 DB
_ALL_V7_TABLES = {
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
    # v7: no new tables — only new columns on workouts
}


def _list_tables(conn: sqlite3.Connection) -> set:
    """Return set of user-created table names in the connection."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {row[0] for row in rows}


def _col_names(conn: sqlite3.Connection, table: str) -> set:
    """Return set of column names for a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _conn_at_version(n: int) -> sqlite3.Connection:
    """
    Return an in-memory DB that looks like it was last migrated to version N.

    Steps:
      1. Create in-memory DB, run initialize_schema → reaches v7.
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

    # Drop columns added in v7 when simulating pre-v7 state
    if n < 7:
        for col in ("min_hr", "intensity_met"):
            try:
                conn.execute(f"ALTER TABLE workouts DROP COLUMN {col}")
            except Exception:
                pass

    conn.commit()

    # Re-enable FK enforcement for the tests
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFreshDBReachesV7(unittest.TestCase):
    """An empty in-memory DB must reach version 7 with all tables present."""

    def test_fresh_db_reaches_v7(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, health_db.SCHEMA_VERSION)
        self.assertEqual(version, 7)

        tables = _list_tables(conn)
        for table in _ALL_V7_TABLES:
            self.assertIn(table, tables, f"Missing table after fresh init: {table}")


class TestV2ToV7(unittest.TestCase):
    """A v2 DB (has body_metrics, missing v3-v7 tables) must upgrade to v7."""

    def test_v2_to_v7(self):
        conn = _conn_at_version(2)

        tables_before = _list_tables(conn)
        self.assertIn("body_metrics", tables_before)
        for table in ["activity_daily", "workouts", "workout_exercises",
                      "oura_tags", "state_of_mind"]:
            self.assertNotIn(table, tables_before,
                             f"Setup error: {table} should not exist at v2")

        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 7)

        tables_after = _list_tables(conn)
        for table in ["workout_exercises", "oura_tags", "state_of_mind"]:
            self.assertIn(table, tables_after,
                          f"Missing table after v2→v7 migration: {table}")


class TestV3ToV7(unittest.TestCase):
    """A v3 DB (has activity_daily/workouts, missing v4-v7 tables) must upgrade to v7."""

    def test_v3_to_v7(self):
        conn = _conn_at_version(3)

        tables_before = _list_tables(conn)
        self.assertIn("activity_daily", tables_before)
        self.assertIn("workouts", tables_before)
        for table in ["workout_exercises", "oura_tags", "state_of_mind"]:
            self.assertNotIn(table, tables_before,
                             f"Setup error: {table} should not exist at v3")

        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 7)

        tables_after = _list_tables(conn)
        for table in ["workout_exercises", "oura_tags", "state_of_mind"]:
            self.assertIn(table, tables_after,
                          f"Missing table after v3→v7 migration: {table}")


class TestV5ToV6Migration(unittest.TestCase):
    """
    A v5 DB (has state_of_mind, missing v6 columns) must upgrade to v7 with
    v6 columns applied:
      - in_range_flag column on lab_results
      - enrichment_status column on health_knowledge
      - topics_text column on health_knowledge
      - health_knowledge_fts virtual table with all three text columns
    """

    def _build_v5_db(self) -> sqlite3.Connection:
        """
        Return an in-memory DB stamped at user_version=5 with v6 columns absent.

        Strategy:
          1. Run initialize_schema to reach v7 (all columns present).
          2. Drop the three v6 columns using ALTER TABLE DROP COLUMN (SQLite >= 3.35).
          3. Rebuild FTS without topics_text to restore the v5 FTS state.
          4. Stamp user_version back to 5.
        """
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.row_factory = sqlite3.Row
        health_db.initialize_schema(conn)

        # Remove v6 columns so the DB truly resembles a v5 snapshot
        conn.execute("ALTER TABLE lab_results DROP COLUMN in_range_flag")
        conn.execute("ALTER TABLE health_knowledge DROP COLUMN enrichment_status")
        conn.execute("ALTER TABLE health_knowledge DROP COLUMN topics_text")

        # Restore the v5 FTS (2-column, no topics_text)
        conn.executescript("""
            DROP TABLE IF EXISTS health_knowledge_fts;
            CREATE VIRTUAL TABLE health_knowledge_fts USING fts5(
                episode_title, summary,
                content='health_knowledge', content_rowid='rowid',
                tokenize='porter unicode61'
            );
            DROP TRIGGER IF EXISTS hk_ai;
            DROP TRIGGER IF EXISTS hk_ad;
            DROP TRIGGER IF EXISTS hk_au;
        """)
        conn.execute("""CREATE TRIGGER IF NOT EXISTS hk_ai AFTER INSERT ON health_knowledge BEGIN
            INSERT INTO health_knowledge_fts(rowid, episode_title, summary)
            VALUES (new.rowid, new.episode_title, new.summary);
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS hk_ad AFTER DELETE ON health_knowledge BEGIN
            INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary)
            VALUES ('delete', old.rowid, old.episode_title, old.summary);
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS hk_au AFTER UPDATE ON health_knowledge BEGIN
            INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary)
            VALUES ('delete', old.rowid, old.episode_title, old.summary);
            INSERT INTO health_knowledge_fts(rowid, episode_title, summary)
            VALUES (new.rowid, new.episode_title, new.summary);
        END""")

        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def test_v5_to_v6_migration(self):
        conn = self._build_v5_db()

        # Confirm v5 state: v6 columns must be absent before migration
        lr_cols_before = _col_names(conn, "lab_results")
        hk_cols_before = _col_names(conn, "health_knowledge")
        self.assertNotIn("in_range_flag", lr_cols_before,
                         "Setup error: in_range_flag should not exist at v5")
        self.assertNotIn("enrichment_status", hk_cols_before,
                         "Setup error: enrichment_status should not exist at v5")
        self.assertNotIn("topics_text", hk_cols_before,
                         "Setup error: topics_text should not exist at v5")

        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 7, "user_version must be 7 after migration")

        # in_range_flag must exist on lab_results
        lr_cols = _col_names(conn, "lab_results")
        self.assertIn("in_range_flag", lr_cols,
                      "in_range_flag column must exist on lab_results after v6 migration")

        # enrichment_status must exist on health_knowledge
        hk_cols = _col_names(conn, "health_knowledge")
        self.assertIn("enrichment_status", hk_cols,
                      "enrichment_status column must exist on health_knowledge after v6 migration")

        # topics_text must exist on health_knowledge
        self.assertIn("topics_text", hk_cols,
                      "topics_text column must exist on health_knowledge after v6 migration")

        # health_knowledge_fts virtual table must be queryable
        try:
            conn.execute("SELECT * FROM health_knowledge_fts LIMIT 1").fetchall()
        except sqlite3.OperationalError as exc:
            self.fail(f"health_knowledge_fts is not queryable after v6 migration: {exc}")


class TestV4ToV7(unittest.TestCase):
    """A v4 DB (has workout_exercises/oura_tags, missing state_of_mind) must upgrade to v7."""

    def test_v4_to_v7(self):
        conn = _conn_at_version(4)

        tables_before = _list_tables(conn)
        self.assertIn("workout_exercises", tables_before)
        self.assertIn("oura_tags", tables_before)
        self.assertNotIn("state_of_mind", tables_before,
                         "Setup error: state_of_mind should not exist at v4")

        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 7)

        tables_after = _list_tables(conn)
        self.assertIn("state_of_mind", tables_after,
                      "Missing state_of_mind after v4→v7 migration")


class TestV6ToV7Migration(unittest.TestCase):
    """A v6 DB must gain min_hr and intensity_met on workouts after upgrade to v7."""


    def test_v6_to_v7_adds_workout_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        health_db.initialize_schema(conn)

        # Roll back to v6, drop the v7 columns
        conn.execute("ALTER TABLE workouts DROP COLUMN min_hr")
        conn.execute("ALTER TABLE workouts DROP COLUMN intensity_met")
        conn.execute("PRAGMA user_version = 6")
        conn.commit()

        cols_before = _col_names(conn, "workouts")
        self.assertNotIn("min_hr", cols_before, "Setup error: min_hr should not exist at v6")
        self.assertNotIn("intensity_met", cols_before, "Setup error: intensity_met should not exist at v6")

        health_db.initialize_schema(conn)

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 7)

        cols_after = _col_names(conn, "workouts")
        self.assertIn("min_hr", cols_after, "min_hr must exist on workouts after v7 migration")
        self.assertIn("intensity_met", cols_after, "intensity_met must exist on workouts after v7 migration")


class TestIdempotentColumnPatches(unittest.TestCase):
    """Calling initialize_schema twice on a v7 DB must not raise any error."""

    def test_idempotent_column_patches(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        health_db.initialize_schema(conn)

        try:
            health_db.initialize_schema(conn)
        except Exception as exc:
            self.fail(f"initialize_schema raised on second call: {exc}")

        version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 7)


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


class TestComputeFlag(unittest.TestCase):
    """
    Unit tests for the _compute_flag function in scripts/import-blood-labs.py.

    The function is loaded via importlib since it lives in a script, not a module.
    _compute_flag(value, ref_low, ref_high) → 'in' | 'borderline' | 'out' | None

    Borderline threshold: within 10% of the boundary value (gap / boundary <= 0.10).
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from pathlib import Path

        # health/ → sample-agent/ → workspace/ → agents/ → ironclaw/ → scripts/
        script_path = Path(__file__).parent.parent.parent.parent.parent / "scripts" / "import-blood-labs.py"
        spec = importlib.util.spec_from_file_location("import_blood_labs", script_path)
        mod = importlib.util.module_from_spec(spec)
        # The script calls _load_alias_map() at import time, which tries to open
        # markers_canonical.json.  That file may not exist in CI; the function
        # gracefully returns {} on FileNotFoundError so the import succeeds.
        spec.loader.exec_module(mod)
        cls._compute_flag = staticmethod(mod._compute_flag)

    def test_value_within_range_returns_in(self):
        """5.0 within [4.0, 6.0] → 'in'"""
        self.assertEqual(self._compute_flag(5.0, 4.0, 6.0), 'in')

    def test_value_well_below_low_returns_out(self):
        """3.5 vs low=4.0: gap=0.5, 0.5/4.0=12.5% > 10% → 'out'"""
        self.assertEqual(self._compute_flag(3.5, 4.0, 6.0), 'out')

    def test_value_borderline_below_low_returns_borderline(self):
        """3.9 vs low=4.0: gap=0.1, 0.1/4.0=2.5% ≤ 10% → 'borderline'"""
        self.assertEqual(self._compute_flag(3.9, 4.0, 6.0), 'borderline')

    def test_value_well_above_high_returns_out(self):
        """6.7 vs high=6.0: gap=0.7, 0.7/6.0=11.7% > 10% → 'out'"""
        self.assertEqual(self._compute_flag(6.7, 4.0, 6.0), 'out')

    def test_value_borderline_above_high_returns_borderline(self):
        """6.05 vs high=6.0: gap=0.05, 0.05/6.0=0.8% ≤ 10% → 'borderline'"""
        self.assertEqual(self._compute_flag(6.05, 4.0, 6.0), 'borderline')

    def test_both_refs_none_returns_none(self):
        """No reference range at all → None"""
        self.assertIsNone(self._compute_flag(5.0, None, None))

    def test_only_ref_high_value_below_returns_in(self):
        """ref_low=None, ref_high=6.0, value=5.0 (below high) → 'in'"""
        self.assertEqual(self._compute_flag(5.0, None, 6.0), 'in')

    def test_only_ref_high_value_exceeds_returns_out(self):
        """ref_low=None, ref_high=6.0, value=7.0: gap=1.0/6.0=16.7% > 10% → 'out'"""
        self.assertEqual(self._compute_flag(7.0, None, 6.0), 'out')

    # ------------------------------------------------------------------
    # NaN gap — verifies pd.isna() fix; reverts to 'in' if is not None used
    # ------------------------------------------------------------------

    def test_both_refs_nan_returns_none(self):
        """
        NaN refs must be treated as absent — returns None, not 'in'.

        Mutation-resistant: if _compute_flag reverts to `is not None` checks,
        NaN passes the guard (nan is not None == True) and all comparisons with
        NaN return False, so the function falls through to return 'in'. This
        test catches that regression.
        """
        result = self._compute_flag(5.0, float('nan'), float('nan'))
        self.assertIsNone(result,
            "NaN ref bounds must be treated as absent (None), not as a valid "
            "range boundary. Got 'in' — pd.isna() guard is likely missing.")

    def test_nan_low_real_high_value_within_high_returns_in(self):
        """NaN ref_low with real ref_high=6.0: value=5.0 is below high → 'in'."""
        result = self._compute_flag(5.0, float('nan'), 6.0)
        self.assertEqual(result, 'in',
            "NaN ref_low should be ignored; value below real ref_high → 'in'")

    def test_nan_low_real_high_value_exceeds_returns_out(self):
        """NaN ref_low with real ref_high=6.0: value=7.0 exceeds high → 'out'."""
        result = self._compute_flag(7.0, float('nan'), 6.0)
        self.assertEqual(result, 'out',
            "NaN ref_low should be ignored; value well above real ref_high → 'out'")

    def test_nan_refs_consistent_with_none_refs(self):
        """
        NaN and None refs must produce identical results — both mean 'no reference'.

        This guards against a future implementation that handles the two cases
        differently, which would be a semantic error.
        """
        nan = float('nan')
        self.assertEqual(
            self._compute_flag(5.0, None, None),
            self._compute_flag(5.0, nan, nan),
            "NaN refs must behave identically to None refs"
        )


# ---------------------------------------------------------------------------
# Tests for _normalize_marker
# ---------------------------------------------------------------------------

class TestNormalizeMarker(unittest.TestCase):
    """
    Unit tests for _normalize_marker in scripts/import-blood-labs.py.

    _normalize_marker(name: str) → str
      Returns the canonical marker name from the alias map, or the original
      name unchanged if no alias entry exists.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from pathlib import Path

        script_path = Path(__file__).parent.parent.parent.parent.parent / "scripts" / "import-blood-labs.py"
        spec = importlib.util.spec_from_file_location("import_blood_labs_nm", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls._normalize_marker = staticmethod(mod._normalize_marker)

    def test_known_alias_maps_to_canonical(self):
        """
        'ApoB (mg/dL)' is listed as an alias for 'Apolipoprotein B (mg/dL)'
        in markers_canonical.json — must return the canonical form.
        """
        result = self._normalize_marker("ApoB (mg/dL)")
        self.assertEqual(result, "Apolipoprotein B (mg/dL)",
            "Known alias must be resolved to canonical name")

    def test_second_alias_same_canonical(self):
        """
        'Apo B (mg/dL)' is a second alias for the same canonical; must also resolve.
        """
        result = self._normalize_marker("Apo B (mg/dL)")
        self.assertEqual(result, "Apolipoprotein B (mg/dL)")

    def test_alias_from_different_canonical(self):
        """
        'HDL-C (mg/dL)' → 'HDL Cholesterol (mg/dL)'.
        Exercises a different alias group to confirm the map is fully loaded.
        """
        result = self._normalize_marker("HDL-C (mg/dL)")
        self.assertEqual(result, "HDL Cholesterol (mg/dL)")

    def test_unknown_name_returned_unchanged(self):
        """A name not in any alias list is returned as-is."""
        unknown = "Some Unknown Marker (units)"
        result = self._normalize_marker(unknown)
        self.assertEqual(result, unknown,
            "Unknown marker name must be returned unchanged")

    def test_canonical_name_itself_returned_unchanged(self):
        """
        The canonical name 'Apolipoprotein B (mg/dL)' is a key in the JSON,
        not a value in any alias list — so it is not in _ALIAS_TO_CANONICAL
        and must pass through untouched.
        """
        canonical = "Apolipoprotein B (mg/dL)"
        result = self._normalize_marker(canonical)
        self.assertEqual(result, canonical,
            "Canonical name must pass through unchanged (it is a key, not an alias)")

    def test_empty_string_returned_unchanged(self):
        """Edge case: empty string has no alias entry → returned as-is."""
        result = self._normalize_marker("")
        self.assertEqual(result, "")


# ---------------------------------------------------------------------------
# Tests for _load_alias_map
# ---------------------------------------------------------------------------

class TestLoadAliasMap(unittest.TestCase):
    """
    Unit tests for _load_alias_map in scripts/import-blood-labs.py.

    _load_alias_map() → dict
      Reads markers_canonical.json next to the script and returns a flat
      {alias: canonical} dict. Returns {} gracefully when the file is absent.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from pathlib import Path

        script_path = Path(__file__).parent.parent.parent.parent.parent / "scripts" / "import-blood-labs.py"
        spec = importlib.util.spec_from_file_location("import_blood_labs_lam", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls._load_alias_map = staticmethod(mod._load_alias_map)
        cls._script_path = script_path

    def test_returns_dict(self):
        """_load_alias_map must always return a dict, never None or another type."""
        result = self._load_alias_map()
        self.assertIsInstance(result, dict,
            "_load_alias_map must return a dict")

    def test_returns_non_empty_with_real_file(self):
        """
        With markers_canonical.json present next to the script, the map must
        be non-empty and contain known alias entries.
        """
        result = self._load_alias_map()
        self.assertGreater(len(result), 0,
            "_load_alias_map should return a non-empty dict when markers_canonical.json exists")

    def test_known_alias_present_in_map(self):
        """'ApoB (mg/dL)' must map to 'Apolipoprotein B (mg/dL)' in the loaded map."""
        result = self._load_alias_map()
        self.assertIn("ApoB (mg/dL)", result,
            "'ApoB (mg/dL)' must be a key in the alias map")
        self.assertEqual(result["ApoB (mg/dL)"], "Apolipoprotein B (mg/dL)")

    def test_values_are_strings(self):
        """All values in the alias map must be strings (canonical names)."""
        result = self._load_alias_map()
        for alias, canonical in result.items():
            self.assertIsInstance(canonical, str,
                f"Canonical value for '{alias}' must be a str, got {type(canonical)}")

    def test_missing_file_returns_empty_dict(self):
        """
        When called with a non-existent markers_canonical.json path, the
        function must return {} rather than raising an exception.

        We test this by temporarily monkey-patching the Path inside the
        loaded module so open() sees a path that does not exist.
        """
        import importlib.util
        from pathlib import Path
        from unittest.mock import patch

        # Reload the module under a fresh name so we can safely monkey-patch
        script_path = self._script_path
        spec = importlib.util.spec_from_file_location("import_blood_labs_missing", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Patch open() inside the module's _load_alias_map to raise FileNotFoundError
        with patch("builtins.open", side_effect=FileNotFoundError("mocked missing file")):
            result = mod._load_alias_map()

        self.assertEqual(result, {},
            "_load_alias_map must return {} when markers_canonical.json is missing")
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
