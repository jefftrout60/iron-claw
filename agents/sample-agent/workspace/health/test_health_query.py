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
# body_metrics_query tests
# ---------------------------------------------------------------------------

class TestBodyMetrics(unittest.TestCase):
    """Tests for health_query.body_metrics_query()."""

    def setUp(self):
        self.conn = _make_conn()
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()

    def _insert_metric(self, date_str, time_str, weight_lbs, fat_ratio_pct=None):
        self.conn.execute(
            """INSERT INTO body_metrics (date, time, weight_lbs, fat_ratio_pct, source)
               VALUES (?, ?, ?, ?, 'withings_api')""",
            (date_str, time_str, weight_lbs, fat_ratio_pct),
        )
        self.conn.commit()

    def test_returns_correct_top_level_keys(self):
        self._insert_metric("2026-01-15", "08:00", 185.0, 22.5)
        result = health_query.body_metrics_query(90, "2026-01-01", "2026-12-31")
        for key in ("days_requested", "readings", "data", "summary"):
            self.assertIn(key, result, f"Missing top-level key: {key}")

    def test_data_row_has_expected_fields(self):
        self._insert_metric("2026-01-15", "08:00", 185.0, 22.5)
        result = health_query.body_metrics_query(90, "2026-01-01", "2026-12-31")
        row = result["data"][0]
        for field in ("date", "time", "weight_lbs", "fat_ratio_pct",
                      "fat_mass_lbs", "lean_mass_lbs", "muscle_mass_lbs", "source"):
            self.assertIn(field, row, f"Missing field: {field}")

    def test_summary_has_expected_keys(self):
        self._insert_metric("2026-01-15", "08:00", 185.0, 22.5)
        self._insert_metric("2026-01-20", "08:00", 184.0, 22.0)
        result = health_query.body_metrics_query(90, "2026-01-01", "2026-12-31")
        for key in ("avg_weight_lbs", "min_weight_lbs", "max_weight_lbs",
                    "avg_fat_ratio_pct", "latest"):
            self.assertIn(key, result["summary"], f"Missing summary key: {key}")

    def test_readings_count_matches_rows(self):
        self._insert_metric("2026-01-15", "08:00", 185.0)
        self._insert_metric("2026-01-20", "08:00", 184.0)
        self._insert_metric("2026-01-25", "08:00", 183.5)
        result = health_query.body_metrics_query(90, "2026-01-01", "2026-12-31")
        self.assertEqual(result["readings"], 3)
        self.assertEqual(len(result["data"]), 3)

    def test_days_window_filters_correctly(self):
        # Insert one old row (outside 30-day window) and one recent row
        self._insert_metric("2025-01-01", "08:00", 190.0)
        self._insert_metric("2026-01-15", "08:00", 185.0)
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 1, 20)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = health_query.body_metrics_query(30, None, None)
        # Only the 2026-01-15 row falls within 30 days of 2026-01-20
        self.assertEqual(result["readings"], 1)
        self.assertEqual(result["data"][0]["date"], "2026-01-15")

    def test_start_end_overrides_days(self):
        self._insert_metric("2026-01-01", "08:00", 186.0)
        self._insert_metric("2026-02-01", "08:00", 185.0)
        self._insert_metric("2026-03-01", "08:00", 184.0)
        # --start/--end narrows to Jan only
        result = health_query.body_metrics_query(90, "2026-01-01", "2026-01-31")
        self.assertEqual(result["readings"], 1)
        self.assertEqual(result["data"][0]["date"], "2026-01-01")

    def test_summary_averages_correct(self):
        self._insert_metric("2026-01-15", "08:00", 185.0, 22.0)
        self._insert_metric("2026-01-20", "08:00", 183.0, 21.0)
        result = health_query.body_metrics_query(90, "2026-01-01", "2026-12-31")
        self.assertAlmostEqual(result["summary"]["avg_weight_lbs"], 184.0, places=1)
        self.assertAlmostEqual(result["summary"]["avg_fat_ratio_pct"], 21.5, places=1)
        self.assertEqual(result["summary"]["min_weight_lbs"], 183.0)
        self.assertEqual(result["summary"]["max_weight_lbs"], 185.0)

    def test_latest_is_most_recent_row(self):
        self._insert_metric("2026-01-15", "08:00", 185.0, 22.0)
        self._insert_metric("2026-02-01", "08:00", 183.0, 21.0)
        result = health_query.body_metrics_query(90, "2026-01-01", "2026-12-31")
        self.assertEqual(result["summary"]["latest"]["date"], "2026-02-01")
        self.assertAlmostEqual(result["summary"]["latest"]["weight_lbs"], 183.0, places=1)

    def test_no_data_raises_system_exit(self):
        with self.assertRaises(SystemExit):
            health_query.body_metrics_query(90, None, None)

    def test_empty_window_raises_system_exit(self):
        self._insert_metric("2025-01-01", "08:00", 185.0)
        with self.assertRaises(SystemExit):
            health_query.body_metrics_query(90, "2026-06-01", "2026-06-30")


# ---------------------------------------------------------------------------
# activity_query tests
# ---------------------------------------------------------------------------

class TestActivityQuery(unittest.TestCase):
    """Tests for health_query.activity_query()."""

    def setUp(self):
        self.conn = _make_conn()
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()

    def _insert_activity(self, date_str, steps=None, daylight_minutes=None):
        self.conn.execute(
            """INSERT INTO activity_daily (date, steps, daylight_minutes, source)
               VALUES (?, ?, ?, 'apple_health')""",
            (date_str, steps, daylight_minutes),
        )
        self.conn.commit()

    def test_returns_correct_top_level_keys(self):
        self._insert_activity("2099-01-15", steps=8000, daylight_minutes=45)
        result = health_query.activity_query(90, "2099-01-01", "2099-12-31")
        for key in ("days_requested", "days_available", "data", "summary"):
            self.assertIn(key, result, f"Missing top-level key: {key}")

    def test_days_window_filters_outside_rows(self):
        # Row outside window should not appear
        self._insert_activity("2099-01-01", steps=5000)
        # Row inside window
        self._insert_activity("2099-03-20", steps=9000)
        result = health_query.activity_query(90, "2099-03-01", "2099-03-31")
        self.assertEqual(result["days_available"], 1)
        self.assertEqual(result["data"][0]["date"], "2099-03-20")

    def test_no_data_raises_system_exit(self):
        with self.assertRaises(SystemExit):
            health_query.activity_query(14, None, None)

    def test_null_daylight_omitted_from_row(self):
        # Insert row with steps only — no daylight_minutes
        self._insert_activity("2099-02-10", steps=7500, daylight_minutes=None)
        result = health_query.activity_query(90, "2099-01-01", "2099-12-31")
        row = result["data"][0]
        self.assertIn("steps", row)
        self.assertNotIn("daylight_minutes", row)

    def test_summary_avg_steps_correct(self):
        self._insert_activity("2099-01-10", steps=6000)
        self._insert_activity("2099-01-11", steps=10000)
        result = health_query.activity_query(90, "2099-01-01", "2099-12-31")
        self.assertAlmostEqual(result["summary"]["avg_steps"], 8000.0, places=1)


# ---------------------------------------------------------------------------
# workouts_query tests
# ---------------------------------------------------------------------------

class TestWorkoutsQuery(unittest.TestCase):
    """Tests for health_query.workouts_query()."""

    def setUp(self):
        self.conn = _make_conn()
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()

    def _insert_workout(self, date_str, start_time, workout_type,
                        duration_min=None, calories=None, avg_hr=None, max_hr=None):
        self.conn.execute(
            """INSERT INTO workouts
                   (date, start_time, workout_type, duration_min, calories,
                    avg_hr, max_hr, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'apple_health')""",
            (date_str, start_time, workout_type, duration_min, calories, avg_hr, max_hr),
        )
        self.conn.commit()

    def test_returns_correct_top_level_keys(self):
        self._insert_workout("2099-01-15", "07:00", "Running", duration_min=30)
        result = health_query.workouts_query(30, "2099-01-01", "2099-12-31", None)
        for key in ("days_requested", "total_workouts", "data", "summary"):
            self.assertIn(key, result, f"Missing top-level key: {key}")

    def test_type_filter_case_insensitive(self):
        # Insert "FunctionalStrengthTraining"; query with "strength"
        self._insert_workout("2099-02-05", "06:00", "FunctionalStrengthTraining",
                             duration_min=45)
        self._insert_workout("2099-02-06", "07:00", "Running", duration_min=30)
        result = health_query.workouts_query(90, "2099-01-01", "2099-12-31", "strength")
        self.assertEqual(result["total_workouts"], 1)
        self.assertEqual(result["data"][0]["workout_type"], "FunctionalStrengthTraining")

    def test_no_data_raises_system_exit(self):
        with self.assertRaises(SystemExit):
            health_query.workouts_query(30, None, None, None)

    def test_summary_by_type_groups_correctly(self):
        self._insert_workout("2099-03-01", "06:00", "Running", duration_min=30)
        self._insert_workout("2099-03-02", "06:00", "Running", duration_min=35)
        self._insert_workout("2099-03-03", "06:00", "FunctionalStrengthTraining",
                             duration_min=45)
        result = health_query.workouts_query(90, "2099-01-01", "2099-12-31", None)
        by_type = result["summary"]["by_type"]
        self.assertEqual(by_type["Running"], 2)
        self.assertEqual(by_type["FunctionalStrengthTraining"], 1)


# ---------------------------------------------------------------------------
# workout_exercises_query tests
# ---------------------------------------------------------------------------

class TestWorkoutExercisesQuery(unittest.TestCase):
    """Tests for health_query.workout_exercises_query()."""

    def setUp(self):
        self.conn = _make_conn()
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()

    def _insert_workout(self, date_str, workout_type, duration_min=None):
        self.conn.execute(
            """INSERT INTO workouts (date, workout_type, duration_min, source)
               VALUES (?, ?, ?, 'apple_health')""",
            (date_str, workout_type, duration_min),
        )
        self.conn.commit()
        return self.conn.execute(
            "SELECT id FROM workouts WHERE date = ? AND workout_type = ?",
            (date_str, workout_type),
        ).fetchone()["id"]

    def _insert_exercise(self, workout_id, workout_date, exercise_name,
                         set_number=None, reps=None, weight_lbs=None, notes=None):
        self.conn.execute(
            """INSERT INTO workout_exercises
                   (workout_id, workout_date, exercise_name, set_number, reps, weight_lbs, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (workout_id, workout_date, exercise_name, set_number, reps, weight_lbs, notes),
        )
        self.conn.commit()

    def test_returns_correct_top_level_keys(self):
        wid = self._insert_workout("2099-01-15", "Strength", 45.0)
        self._insert_exercise(wid, "2099-01-15", "Squat", set_number=1, reps=5, weight_lbs=185.0)
        result = health_query.workout_exercises_query(7, "2099-01-15")
        for key in ("period", "total_exercises", "workouts"):
            self.assertIn(key, result, f"Missing top-level key: {key}")

    def test_single_date_sets_period(self):
        wid = self._insert_workout("2099-02-10", "Strength", 60.0)
        self._insert_exercise(wid, "2099-02-10", "Deadlift", set_number=1, reps=3, weight_lbs=225.0)
        result = health_query.workout_exercises_query(7, "2099-02-10")
        self.assertEqual(result["period"], "2099-02-10")

    def test_exercises_grouped_under_workout(self):
        wid = self._insert_workout("2099-03-01", "FunctionalStrengthTraining", 62.0)
        self._insert_exercise(wid, "2099-03-01", "Squat", set_number=1, reps=5, weight_lbs=185.0)
        self._insert_exercise(wid, "2099-03-01", "Squat", set_number=2, reps=5, weight_lbs=185.0)
        result = health_query.workout_exercises_query(7, "2099-03-01")
        self.assertEqual(len(result["workouts"]), 1)
        self.assertEqual(len(result["workouts"][0]["exercises"]), 2)
        self.assertEqual(result["total_exercises"], 2)

    def test_null_values_omitted_from_exercise_dict(self):
        wid = self._insert_workout("2099-04-01", "Cardio", 30.0)
        # Insert exercise with no weight or notes
        self._insert_exercise(wid, "2099-04-01", "Run", set_number=1, reps=None, weight_lbs=None)
        result = health_query.workout_exercises_query(7, "2099-04-01")
        exercise = result["workouts"][0]["exercises"][0]
        self.assertNotIn("reps", exercise)
        self.assertNotIn("weight_lbs", exercise)
        self.assertNotIn("notes", exercise)

    def test_null_duration_omitted_from_workout(self):
        wid = self._insert_workout("2099-05-01", "Yoga", None)
        self._insert_exercise(wid, "2099-05-01", "Warrior Pose", set_number=1)
        result = health_query.workout_exercises_query(7, "2099-05-01")
        self.assertNotIn("duration_min", result["workouts"][0])

    def test_days_window_filters_correctly(self):
        wid1 = self._insert_workout("2099-01-01", "Strength", 40.0)
        self._insert_exercise(wid1, "2099-01-01", "OldExercise", set_number=1)
        wid2 = self._insert_workout("2099-03-20", "Strength", 50.0)
        self._insert_exercise(wid2, "2099-03-20", "Squat", set_number=1)
        # Single-date mode returns only the matching workout
        result = health_query.workout_exercises_query(7, "2099-03-20")
        self.assertEqual(len(result["workouts"]), 1)
        self.assertEqual(result["workouts"][0]["date"], "2099-03-20")

    def test_no_data_raises_system_exit(self):
        with self.assertRaises(SystemExit):
            health_query.workout_exercises_query(7, None)

    def test_no_data_for_date_raises_system_exit(self):
        with self.assertRaises(SystemExit):
            health_query.workout_exercises_query(7, "2099-12-31")


# ---------------------------------------------------------------------------
# tags_query tests
# ---------------------------------------------------------------------------

class TestTagsQuery(unittest.TestCase):
    """Tests for health_query.tags_query()."""

    def setUp(self):
        self.conn = _make_conn()
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()

    def _insert_tag(self, tag_id, day, tag_type, comment=None):
        self.conn.execute(
            "INSERT INTO oura_tags (id, day, tag_type, comment) VALUES (?, ?, ?, ?)",
            (tag_id, day, tag_type, comment),
        )
        self.conn.commit()

    def test_returns_correct_top_level_keys(self):
        self._insert_tag("t1", "2099-01-15", "sauna")
        result = health_query.tags_query(30, "2099-01-01", "2099-12-31", None)
        for key in ("days_requested", "total_tags", "data", "by_type"):
            self.assertIn(key, result, f"Missing top-level key: {key}")

    def test_data_row_has_required_fields(self):
        self._insert_tag("t1", "2099-01-15", "sauna", "30 min session")
        result = health_query.tags_query(30, "2099-01-01", "2099-12-31", None)
        row = result["data"][0]
        for field in ("day", "tag_type", "comment"):
            self.assertIn(field, row, f"Missing field: {field}")

    def test_by_type_groups_correctly(self):
        self._insert_tag("t1", "2099-01-15", "sauna")
        self._insert_tag("t2", "2099-01-16", "sauna")
        self._insert_tag("t3", "2099-01-17", "alcohol")
        result = health_query.tags_query(30, "2099-01-01", "2099-12-31", None)
        self.assertEqual(result["by_type"]["sauna"], 2)
        self.assertEqual(result["by_type"]["alcohol"], 1)

    def test_total_tags_count(self):
        self._insert_tag("t1", "2099-02-01", "sauna")
        self._insert_tag("t2", "2099-02-02", "late_meal")
        result = health_query.tags_query(30, "2099-01-01", "2099-12-31", None)
        self.assertEqual(result["total_tags"], 2)
        self.assertEqual(len(result["data"]), 2)

    def test_type_filter_substring_match(self):
        self._insert_tag("t1", "2099-03-01", "sauna")
        self._insert_tag("t2", "2099-03-02", "alcohol")
        self._insert_tag("t3", "2099-03-03", "late_meal")
        result = health_query.tags_query(30, "2099-01-01", "2099-12-31", "sau")
        self.assertEqual(result["total_tags"], 1)
        self.assertEqual(result["data"][0]["tag_type"], "sauna")

    def test_null_comment_included_in_row(self):
        self._insert_tag("t1", "2099-04-01", "sauna", None)
        result = health_query.tags_query(30, "2099-01-01", "2099-12-31", None)
        self.assertIsNone(result["data"][0]["comment"])

    def test_start_end_overrides_days(self):
        self._insert_tag("t1", "2099-01-01", "sauna")
        self._insert_tag("t2", "2099-03-01", "sauna")
        result = health_query.tags_query(30, "2099-01-01", "2099-01-31", None)
        self.assertEqual(result["total_tags"], 1)
        self.assertEqual(result["data"][0]["day"], "2099-01-01")

    def test_no_data_raises_system_exit(self):
        with self.assertRaises(SystemExit):
            health_query.tags_query(30, None, None, None)

    def test_type_filter_no_match_raises_system_exit(self):
        self._insert_tag("t1", "2099-05-01", "sauna")
        with self.assertRaises(SystemExit):
            health_query.tags_query(30, "2099-01-01", "2099-12-31", "alcohol")


# ---------------------------------------------------------------------------
# mood_query tests
# ---------------------------------------------------------------------------

class TestMoodQuery(unittest.TestCase):
    """Tests for health_query.mood_query()."""

    def setUp(self):
        self.conn = _make_conn()
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()

    def _insert_mood(self, date_str, kind="daily_mood", valence=0.7, arousal=0.5,
                     labels=None, associations=None):
        import json as _json
        self.conn.execute(
            """INSERT INTO state_of_mind
               (date, kind, valence, arousal, labels, associations)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                date_str, kind, valence, arousal,
                _json.dumps(labels) if labels is not None else None,
                _json.dumps(associations) if associations is not None else None,
            ),
        )
        self.conn.commit()

    def test_empty_table_returns_empty_list_not_error(self):
        # Must return [] not raise SystemExit
        result = health_query.mood_query("2026-01-01", "daily_mood")
        self.assertEqual(result, [])

    def test_returns_list(self):
        self._insert_mood("2026-04-20")
        result = health_query.mood_query("2026-04-01", "daily_mood")
        self.assertIsInstance(result, list)

    def test_row_has_required_keys(self):
        self._insert_mood("2026-04-20", labels=["calm"], associations=["nature"])
        result = health_query.mood_query("2026-04-01", "daily_mood")
        row = result[0]
        for key in ("date", "kind", "valence", "arousal", "labels", "associations"):
            self.assertIn(key, row, f"Missing key: {key}")

    def test_labels_parsed_to_list(self):
        self._insert_mood("2026-04-20", labels=["happy", "energized"])
        result = health_query.mood_query("2026-04-01", "daily_mood")
        self.assertIsInstance(result[0]["labels"], list)
        self.assertEqual(result[0]["labels"], ["happy", "energized"])

    def test_associations_parsed_to_list(self):
        self._insert_mood("2026-04-20", associations=["work", "exercise"])
        result = health_query.mood_query("2026-04-01", "daily_mood")
        self.assertIsInstance(result[0]["associations"], list)
        self.assertEqual(result[0]["associations"], ["work", "exercise"])

    def test_null_labels_returns_empty_list(self):
        self._insert_mood("2026-04-20", labels=None, associations=None)
        result = health_query.mood_query("2026-04-01", "daily_mood")
        self.assertEqual(result[0]["labels"], [])
        self.assertEqual(result[0]["associations"], [])

    def test_kind_filter_daily_mood(self):
        self._insert_mood("2026-04-20", kind="daily_mood")
        self._insert_mood("2026-04-20", kind="momentary_emotion")
        result = health_query.mood_query("2026-04-01", "daily_mood")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["kind"], "daily_mood")

    def test_kind_filter_momentary_emotion(self):
        self._insert_mood("2026-04-20", kind="daily_mood")
        self._insert_mood("2026-04-20", kind="momentary_emotion")
        result = health_query.mood_query("2026-04-01", "momentary_emotion")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["kind"], "momentary_emotion")

    def test_since_filters_old_rows(self):
        self._insert_mood("2026-01-01")   # old, should be excluded
        self._insert_mood("2026-04-20")   # recent, should be included
        result = health_query.mood_query("2026-04-01", "daily_mood")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], "2026-04-20")

    def test_ordered_descending_by_date(self):
        self._insert_mood("2026-04-18")
        self._insert_mood("2026-04-20")
        self._insert_mood("2026-04-19")
        result = health_query.mood_query("2026-04-01", "daily_mood")
        dates = [r["date"] for r in result]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_values_correct(self):
        self._insert_mood("2026-04-20", valence=0.8, arousal=0.3,
                          labels=["calm"], associations=["meditation"])
        result = health_query.mood_query("2026-04-01", "daily_mood")
        row = result[0]
        self.assertAlmostEqual(row["valence"], 0.8, places=5)
        self.assertAlmostEqual(row["arousal"], 0.3, places=5)

    def test_since_default_covers_30_days(self):
        # mood_query(None, kind) should use 30 days ago as cutoff
        # Insert one row far in the past — it must be excluded
        self._insert_mood("2000-01-01")
        result = health_query.mood_query(None, "daily_mood")
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# cmd_sync_status tests
# ---------------------------------------------------------------------------

class TestSyncStatus(unittest.TestCase):
    """Tests for health_query.cmd_sync_status()."""

    def setUp(self):
        self.conn = _make_conn()
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()

    def _insert_sync(self, resource, last_synced):
        self.conn.execute(
            "INSERT OR REPLACE INTO sync_state (resource, last_synced) VALUES (?, ?)",
            (resource, last_synced),
        )
        self.conn.commit()

    def test_empty_sync_state_returns_empty_dict(self):
        result = health_query.cmd_sync_status(self.conn)
        self.assertEqual(result, {})

    def test_stale_automated_source_when_days_ago_exceeds_2(self):
        # daily_summaries is an automated source; 5 days ago → stale
        self._insert_sync("daily_summaries", "2026-04-25")
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 30)
            mock_date.fromisoformat.side_effect = date.fromisoformat
            result = health_query.cmd_sync_status(self.conn)
        self.assertEqual(result["daily_summaries"]["days_ago"], 5)
        self.assertTrue(result["daily_summaries"]["stale"])

    def test_not_stale_automated_source_within_2_days(self):
        # sleep synced 2 days ago → not stale (boundary: must be > 2)
        self._insert_sync("sleep", "2026-04-28")
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 30)
            mock_date.fromisoformat.side_effect = date.fromisoformat
            result = health_query.cmd_sync_status(self.conn)
        self.assertEqual(result["sleep"]["days_ago"], 2)
        self.assertFalse(result["sleep"]["stale"])

    def test_manual_source_never_stale_even_if_old(self):
        # "workouts" is not in the automated set → stale must always be False
        self._insert_sync("workouts", "2026-01-01")
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 30)
            mock_date.fromisoformat.side_effect = date.fromisoformat
            result = health_query.cmd_sync_status(self.conn)
        self.assertFalse(result["workouts"]["stale"])
        self.assertGreater(result["workouts"]["days_ago"], 2)

    def test_days_ago_calculation_accuracy(self):
        self._insert_sync("heartrate", "2026-04-27")
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 30)
            mock_date.fromisoformat.side_effect = date.fromisoformat
            result = health_query.cmd_sync_status(self.conn)
        self.assertEqual(result["heartrate"]["days_ago"], 3)

    def test_days_ago_1_not_stale(self):
        self._insert_sync("withings", "2026-04-29")
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 30)
            mock_date.fromisoformat.side_effect = date.fromisoformat
            result = health_query.cmd_sync_status(self.conn)
        self.assertEqual(result["withings"]["days_ago"], 1)
        self.assertFalse(result["withings"]["stale"])

    def test_days_ago_3_oura_tags_stale(self):
        self._insert_sync("oura_tags", "2026-04-27")
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 30)
            mock_date.fromisoformat.side_effect = date.fromisoformat
            result = health_query.cmd_sync_status(self.conn)
        self.assertEqual(result["oura_tags"]["days_ago"], 3)
        self.assertTrue(result["oura_tags"]["stale"])

    def test_malformed_date_string_gives_days_ago_none(self):
        # Insert a row with a garbled date — should not crash, days_ago = None
        self.conn.execute(
            "INSERT OR REPLACE INTO sync_state (resource, last_synced) VALUES (?, ?)",
            ("daily_summaries", "not-a-date"),
        )
        self.conn.commit()
        result = health_query.cmd_sync_status(self.conn)
        self.assertIsNone(result["daily_summaries"]["days_ago"])
        self.assertFalse(result["daily_summaries"]["stale"])

    def test_full_iso_datetime_truncated_to_date(self):
        # last_synced stored as ISO datetime (with time component)
        self._insert_sync("sleep", "2026-04-28T14:32:00Z")
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 30)
            mock_date.fromisoformat.side_effect = date.fromisoformat
            result = health_query.cmd_sync_status(self.conn)
        self.assertEqual(result["sleep"]["days_ago"], 2)

    def test_result_preserves_last_synced_string(self):
        ts = "2026-04-28"
        self._insert_sync("heartrate", ts)
        result = health_query.cmd_sync_status(self.conn)
        self.assertEqual(result["heartrate"]["last_synced"], ts)

    def test_multiple_resources_ordered_by_resource(self):
        self._insert_sync("withings", "2026-04-28")
        self._insert_sync("daily_summaries", "2026-04-28")
        self._insert_sync("sleep", "2026-04-28")
        result = health_query.cmd_sync_status(self.conn)
        self.assertEqual(list(result.keys()), sorted(result.keys()))


# ---------------------------------------------------------------------------
# classify_temporal tests  (Rule 6c)
# ---------------------------------------------------------------------------

class TestClassifyTemporal(unittest.TestCase):
    """Tests for health_query.classify_temporal().

    The function classifies user text to determine how to handle the date for a
    body metric entry.  There is no DB dependency — no setUp/tearDown needed.
    """

    # --- 'now' cases ---

    def test_bare_number_returns_now(self):
        self.assertEqual(health_query.classify_temporal("185.2"), "now")

    def test_bare_number_with_decimal_returns_now(self):
        self.assertEqual(health_query.classify_temporal("12.5"), "now")

    def test_word_today_returns_now(self):
        self.assertEqual(health_query.classify_temporal("today"), "now")

    def test_this_morning_returns_now(self):
        self.assertEqual(health_query.classify_temporal("this morning"), "now")

    def test_just_now_returns_now(self):
        self.assertEqual(health_query.classify_temporal("just now"), "now")

    def test_right_now_returns_now(self):
        self.assertEqual(health_query.classify_temporal("right now"), "now")

    def test_empty_string_returns_now(self):
        # No temporal words at all — default is 'now'
        self.assertEqual(health_query.classify_temporal(""), "now")

    def test_no_temporal_words_returns_now(self):
        # A plain reading with no date context defaults to now
        self.assertEqual(health_query.classify_temporal("185.2 lbs"), "now")

    # --- 'past_explicit' cases ---

    def test_iso_date_embedded_returns_past_explicit(self):
        self.assertEqual(health_query.classify_temporal("185.2 on 2026-04-15"), "past_explicit")

    def test_bare_iso_date_returns_past_explicit(self):
        self.assertEqual(health_query.classify_temporal("2026-04-15"), "past_explicit")

    def test_named_month_with_ordinal_returns_past_explicit(self):
        self.assertEqual(health_query.classify_temporal("April 15th"), "past_explicit")

    def test_named_month_lowercase_returns_past_explicit(self):
        self.assertEqual(health_query.classify_temporal("april 15"), "past_explicit")

    def test_named_month_march_ordinal_returns_past_explicit(self):
        self.assertEqual(health_query.classify_temporal("march 3rd"), "past_explicit")

    def test_named_month_abbreviated_returns_past_explicit(self):
        # Three-letter abbreviation without ordinal suffix
        self.assertEqual(health_query.classify_temporal("jan 1"), "past_explicit")

    def test_named_month_mixed_case_returns_past_explicit(self):
        self.assertEqual(health_query.classify_temporal("April 15"), "past_explicit")

    # --- 'ambiguous' cases ---

    def test_yesterday_returns_ambiguous(self):
        self.assertEqual(health_query.classify_temporal("yesterday"), "ambiguous")

    def test_last_tuesday_returns_ambiguous(self):
        self.assertEqual(health_query.classify_temporal("last Tuesday"), "ambiguous")

    def test_last_week_returns_ambiguous(self):
        self.assertEqual(health_query.classify_temporal("last week"), "ambiguous")

    def test_the_other_day_returns_ambiguous(self):
        self.assertEqual(health_query.classify_temporal("the other day"), "ambiguous")

    def test_bare_weekday_monday_returns_ambiguous(self):
        self.assertEqual(health_query.classify_temporal("Monday"), "ambiguous")

    def test_bare_weekday_tuesday_returns_ambiguous(self):
        self.assertEqual(health_query.classify_temporal("Tuesday"), "ambiguous")

    def test_bare_weekday_friday_returns_ambiguous(self):
        self.assertEqual(health_query.classify_temporal("Friday"), "ambiguous")

    def test_a_few_days_ago_returns_ambiguous(self):
        self.assertEqual(health_query.classify_temporal("a few days ago"), "ambiguous")

    def test_earlier_this_week_returns_ambiguous(self):
        self.assertEqual(health_query.classify_temporal("earlier this week"), "ambiguous")

    # --- precedence: past_explicit wins over ambiguous ---

    def test_explicit_date_with_weekday_context_returns_past_explicit(self):
        # If the message contains both an ISO date and an ambiguous word the
        # explicit date pattern is checked first and wins.
        self.assertEqual(
            health_query.classify_temporal("I weighed in on 2026-04-15 last Monday"),
            "past_explicit",
        )


# ---------------------------------------------------------------------------
# _parse_explicit_date tests  (Rule 6c)
# ---------------------------------------------------------------------------

class TestParseExplicitDate(unittest.TestCase):
    """Tests for health_query._parse_explicit_date().

    The function always returns a YYYY-MM-DD string — never a date object.
    ISO dates are extracted verbatim.  Named month+day dates default to the
    current year but roll back one year when the resulting date would be in
    the future (relative to today).
    """

    # --- ISO extraction ---

    def test_iso_date_embedded_in_text_returns_correct_string(self):
        result = health_query._parse_explicit_date("185.2 on 2026-04-15")
        self.assertEqual(result, "2026-04-15")

    def test_bare_iso_date_returns_correct_string(self):
        result = health_query._parse_explicit_date("2026-04-15")
        self.assertEqual(result, "2026-04-15")

    def test_iso_date_mid_sentence_returns_correct_string(self):
        result = health_query._parse_explicit_date("I measured on 2025-12-31 after the gym")
        self.assertEqual(result, "2025-12-31")

    def test_iso_date_returns_string_not_date_object(self):
        result = health_query._parse_explicit_date("2026-04-15")
        self.assertIsInstance(result, str)
        # Validate it is valid YYYY-MM-DD
        from datetime import date
        parsed = date.fromisoformat(result)
        self.assertEqual(parsed.year, 2026)
        self.assertEqual(parsed.month, 4)
        self.assertEqual(parsed.day, 15)

    # --- Named month+day extraction ---

    def test_april_15th_returns_correct_month_and_day(self):
        from datetime import date
        result = health_query._parse_explicit_date("April 15th")
        parsed = date.fromisoformat(result)
        self.assertEqual(parsed.month, 4)
        self.assertEqual(parsed.day, 15)

    def test_march_3rd_returns_correct_month_and_day(self):
        from datetime import date
        result = health_query._parse_explicit_date("march 3rd")
        parsed = date.fromisoformat(result)
        self.assertEqual(parsed.month, 3)
        self.assertEqual(parsed.day, 3)

    def test_named_month_returns_string_not_date_object(self):
        result = health_query._parse_explicit_date("April 15th")
        self.assertIsInstance(result, str)

    def test_named_month_result_is_valid_iso_format(self):
        from datetime import date
        result = health_query._parse_explicit_date("april 15")
        # Must be parseable without raising
        date.fromisoformat(result)

    def test_future_named_month_rolls_back_to_prior_year(self):
        # A month+day that is in the future relative to today must resolve to
        # the prior year, not the current year.  We use the unittest.mock.patch
        # approach so the test stays deterministic regardless of when it runs.
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            # Fix "today" to 2026-05-01 so "May 15th" is clearly in the future
            mock_date.today.return_value = date(2026, 5, 1)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = health_query._parse_explicit_date("May 15th")
        self.assertEqual(result, "2025-05-15")

    def test_past_named_month_stays_in_current_year(self):
        # A month+day that is not in the future must stay in the current year.
        from unittest.mock import patch
        from datetime import date
        with patch("health_query.date") as mock_date:
            mock_date.today.return_value = date(2026, 5, 1)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = health_query._parse_explicit_date("April 15th")
        self.assertEqual(result, "2026-04-15")

    # --- Fallback to today ---

    def test_no_date_in_text_returns_todays_date_string(self):
        from datetime import date
        result = health_query._parse_explicit_date("no date here at all")
        self.assertEqual(result, date.today().isoformat())

    def test_bare_number_no_date_falls_back_to_today(self):
        from datetime import date
        result = health_query._parse_explicit_date("185.2")
        self.assertEqual(result, date.today().isoformat())

    def test_empty_string_falls_back_to_today(self):
        from datetime import date
        result = health_query._parse_explicit_date("")
        self.assertEqual(result, date.today().isoformat())

    def test_fallback_returns_string_not_date_object(self):
        result = health_query._parse_explicit_date("")
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# hrv_trend tests
# ---------------------------------------------------------------------------

class TestHrvTrend(unittest.TestCase):
    """Tests for health_query.hrv_trend()."""

    def setUp(self):
        self.conn = _make_conn()
        self._original_get_connection = health_db.get_connection
        health_query.health_db.get_connection = lambda *a, **kw: self.conn

    def tearDown(self):
        health_db.get_connection = self._original_get_connection
        self.conn.close()

    def _insert_oura(self, row_id, day, avg_hrv_rmssd=None, resting_heart_rate=None):
        self.conn.execute(
            """INSERT INTO oura_daily (id, day, avg_hrv_rmssd, resting_heart_rate)
               VALUES (?, ?, ?, ?)""",
            (row_id, day, avg_hrv_rmssd, resting_heart_rate),
        )
        self.conn.commit()

    # --- Test 1: weekly bucketing ---
    def test_two_rows_same_week_produce_one_entry_with_average(self):
        # 2026-01-05 (Mon) and 2026-01-07 (Wed) are both in ISO week 2026-W01
        self._insert_oura("r1", "2026-01-05", avg_hrv_rmssd=40.0)
        self._insert_oura("r2", "2026-01-07", avg_hrv_rmssd=60.0)
        # Use a large weeks window so both rows are definitely included
        result = health_query.hrv_trend(520)
        weeks = result["weeks"]
        print(f"[test_bucketing] weeks={weeks}")
        # Both rows are in the same ISO week — must produce exactly one entry
        self.assertEqual(len(weeks), 1)
        # Average of 40.0 and 60.0 = 50.0
        self.assertAlmostEqual(weeks[0]["avg_hrv_rmssd"], 50.0, places=1)

    # --- Test 2: delta computation ---
    def test_two_consecutive_weeks_produce_correct_deltas(self):
        # Week 2026-W01: 2026-01-05
        self._insert_oura("r1", "2026-01-05", avg_hrv_rmssd=40.0, resting_heart_rate=58)
        # Week 2026-W02: 2026-01-12
        self._insert_oura("r2", "2026-01-12", avg_hrv_rmssd=50.0, resting_heart_rate=55)
        result = health_query.hrv_trend(520)
        weeks = result["weeks"]
        print(f"[test_deltas] weeks={weeks}")
        self.assertEqual(len(weeks), 2)
        # First week: deltas must be None
        self.assertIsNone(weeks[0]["hrv_delta"])
        self.assertIsNone(weeks[0]["rhr_delta"])
        # Second week: hrv_delta = 50.0 - 40.0 = 10.0; rhr_delta = 55 - 58 = -3.0
        self.assertAlmostEqual(weeks[1]["hrv_delta"], 10.0, places=1)
        self.assertAlmostEqual(weeks[1]["rhr_delta"], -3.0, places=1)

    # --- Test 3: partial week included ---
    def test_single_row_in_incomplete_week_still_appears(self):
        # Insert one row in week 2026-W01 and one in week 2026-W02 (only 1 day each)
        self._insert_oura("r1", "2026-01-05", avg_hrv_rmssd=40.0)
        self._insert_oura("r2", "2026-01-12", avg_hrv_rmssd=45.0)
        result = health_query.hrv_trend(520)
        weeks = result["weeks"]
        print(f"[test_partial_week] weeks={weeks}")
        # Both single-day weeks must appear
        self.assertEqual(len(weeks), 2)
        # The partial weeks report days=1 each
        self.assertEqual(weeks[0]["days"], 1)
        self.assertEqual(weeks[1]["days"], 1)

    # --- Test 4: weeks_requested in output ---
    def test_weeks_requested_matches_argument(self):
        # 2026-01-05 is ~17 weeks before today (2026-05-01) — use 520 to safely include it
        self._insert_oura("r1", "2026-01-05", avg_hrv_rmssd=40.0)
        result = health_query.hrv_trend(520)
        print(f"[test_weeks_requested] weeks_requested={result['weeks_requested']}")
        self.assertEqual(result["weeks_requested"], 520)

    def test_weeks_requested_small_value_preserved(self):
        # Insert a row very close to today so it falls within a 2-week window
        from datetime import date, timedelta
        recent_day = (date.today() - timedelta(days=3)).isoformat()
        self._insert_oura("r-recent", recent_day, avg_hrv_rmssd=55.0)
        result = health_query.hrv_trend(2)
        print(f"[test_weeks_requested_small] weeks_requested={result['weeks_requested']}")
        self.assertEqual(result["weeks_requested"], 2)

    # --- Test 5: no data exits with code 1 ---
    def test_empty_table_calls_sys_exit_1(self):
        # oura_daily is empty — hrv_trend must call _err() which calls sys.exit(1)
        with self.assertRaises(SystemExit) as cm:
            health_query.hrv_trend(4)
        print(f"[test_no_data_exit] exit code={cm.exception.code}")
        self.assertEqual(cm.exception.code, 1)

    def test_rows_outside_window_treated_as_no_data(self):
        # Insert a row that is definitely outside any reasonable window
        # (2000-01-03 is > 100 weeks ago relative to 2026-05-01)
        self._insert_oura("r-old", "2000-01-03", avg_hrv_rmssd=40.0)
        with self.assertRaises(SystemExit) as cm:
            health_query.hrv_trend(4)
        print(f"[test_outside_window_exit] exit code={cm.exception.code}")
        self.assertEqual(cm.exception.code, 1)

    # --- Test 6: rhr_delta is None when resting_hr missing for a week ---
    def test_rhr_delta_none_when_prior_week_has_no_resting_hr(self):
        # Week 2026-W01: has HRV but NO resting_heart_rate
        self._insert_oura("r1", "2026-01-05", avg_hrv_rmssd=40.0, resting_heart_rate=None)
        # Week 2026-W02: has both HRV and resting_heart_rate
        self._insert_oura("r2", "2026-01-12", avg_hrv_rmssd=50.0, resting_heart_rate=56)
        result = health_query.hrv_trend(520)
        weeks = result["weeks"]
        print(f"[test_rhr_delta_none] weeks={weeks}")
        self.assertEqual(len(weeks), 2)
        # Week 1: avg_resting_hr must be None (no resting_hr data)
        self.assertIsNone(weeks[0]["avg_resting_hr"])
        # Week 2: rhr_delta must be None because prev week avg_resting_hr is None
        self.assertIsNone(weeks[1]["rhr_delta"])
        # But hrv_delta should still be computed correctly: 50.0 - 40.0 = 10.0
        self.assertAlmostEqual(weeks[1]["hrv_delta"], 10.0, places=1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
