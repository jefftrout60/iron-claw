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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
