#!/usr/bin/env python3
"""
Behavioral tests for scripts/import-apple-health.py.

All tests use in-memory SQLite so they never touch the real health.db.
Synthetic XML is written to NamedTemporaryFile so parse_export() can open
it as a filepath (it uses iterparse, not a file object).

Run with:
    python3 -m unittest scripts/test_apple_health.py -v
from the repo root.
"""

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents" / "sample-agent" / "workspace" / "health"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

sys.path.insert(0, str(_HEALTH_DIR))
import health_db  # noqa: E402

# Load import-apple-health.py via importlib (filename has hyphens)
_spec = importlib.util.spec_from_file_location(
    "apple_health", _SCRIPTS_DIR / "import-apple-health.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["apple_health"] = mod
_spec.loader.exec_module(mod)

parse_export = mod.parse_export
import_bp = mod.import_bp
import_body_metrics = mod.import_body_metrics
import_activity = mod.import_activity
import_workouts = mod.import_workouts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS_ZONE = " -0500"  # arbitrary UTC offset, valid for strptime format


def _ts(date: str, time: str = "08:00:00") -> str:
    """Build a timestamp string matching Apple Health format."""
    return f"{date} {time}{_TS_ZONE}"


def _xml_document(*record_fragments: str) -> str:
    """Wrap record fragments in a minimal HealthData root element."""
    inner = "\n".join(record_fragments)
    return f'<?xml version="1.0"?>\n<HealthData>\n{inner}\n</HealthData>'


def _write_tmp_xml(content: str) -> Path:
    """Write XML content to a NamedTemporaryFile and return its Path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", delete=False, encoding="utf-8"
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def _make_mem_conn() -> sqlite3.Connection:
    """Open an in-memory SQLite DB with the full health schema initialised."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    health_db.initialize_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestBPPairing(unittest.TestCase):
    """parse_export() correctly pairs BP systolic + diastolic records."""

    def _parse(self, xml: str):
        path = _write_tmp_xml(xml)
        try:
            return parse_export(path)
        finally:
            path.unlink(missing_ok=True)

    def test_matching_pair_produces_one_bp_entry(self):
        """Systolic and diastolic with the same startDate → exactly one paired entry."""
        ts = _ts("2026-03-01")
        xml = _xml_document(
            f'<Record type="HKQuantityTypeIdentifierBloodPressureSystolic"'
            f' startDate="{ts}" value="120"/>',
            f'<Record type="HKQuantityTypeIdentifierBloodPressureDiastolic"'
            f' startDate="{ts}" value="80"/>',
        )
        bp_pairs, _, _, _, _ = self._parse(xml)

        self.assertEqual(len(bp_pairs), 1)
        entry = bp_pairs[ts]
        self.assertEqual(entry["systolic"], 120.0)
        self.assertEqual(entry["diastolic"], 80.0)

    def test_orphan_systolic_is_dropped(self):
        """A systolic record with no matching diastolic is silently dropped."""
        ts = _ts("2026-03-02")
        xml = _xml_document(
            f'<Record type="HKQuantityTypeIdentifierBloodPressureSystolic"'
            f' startDate="{ts}" value="115"/>',
        )
        bp_pairs, _, _, _, _ = self._parse(xml)

        self.assertEqual(len(bp_pairs), 0)

    def test_orphan_diastolic_is_dropped(self):
        """A diastolic record with no matching systolic is silently dropped."""
        ts = _ts("2026-03-03")
        xml = _xml_document(
            f'<Record type="HKQuantityTypeIdentifierBloodPressureDiastolic"'
            f' startDate="{ts}" value="75"/>',
        )
        bp_pairs, _, _, _, _ = self._parse(xml)

        self.assertEqual(len(bp_pairs), 0)

    def test_different_timestamps_are_not_paired(self):
        """Systolic and diastolic with different startDates produce zero pairs."""
        ts_sys = _ts("2026-03-04", "08:00:00")
        ts_dia = _ts("2026-03-04", "08:01:00")
        xml = _xml_document(
            f'<Record type="HKQuantityTypeIdentifierBloodPressureSystolic"'
            f' startDate="{ts_sys}" value="125"/>',
            f'<Record type="HKQuantityTypeIdentifierBloodPressureDiastolic"'
            f' startDate="{ts_dia}" value="82"/>',
        )
        bp_pairs, _, _, _, _ = self._parse(xml)

        self.assertEqual(len(bp_pairs), 0)

    def test_two_complete_pairs_produce_two_entries(self):
        """Two independent paired readings produce two bp_pairs entries."""
        ts1 = _ts("2026-03-05", "07:00:00")
        ts2 = _ts("2026-03-05", "19:00:00")
        xml = _xml_document(
            f'<Record type="HKQuantityTypeIdentifierBloodPressureSystolic"'
            f' startDate="{ts1}" value="118"/>',
            f'<Record type="HKQuantityTypeIdentifierBloodPressureDiastolic"'
            f' startDate="{ts1}" value="76"/>',
            f'<Record type="HKQuantityTypeIdentifierBloodPressureSystolic"'
            f' startDate="{ts2}" value="122"/>',
            f'<Record type="HKQuantityTypeIdentifierBloodPressureDiastolic"'
            f' startDate="{ts2}" value="79"/>',
        )
        bp_pairs, _, _, _, _ = self._parse(xml)

        self.assertEqual(len(bp_pairs), 2)


class TestBPSourcePriority(unittest.TestCase):
    """import_bp() never overwrites rows from non-apple_health sources."""

    def _seed_bp(self, conn, date, time, systolic, diastolic, source):
        conn.execute(
            "INSERT INTO blood_pressure (date, time, systolic, diastolic, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (date, time, systolic, diastolic, source),
        )
        conn.commit()

    def test_apple_health_does_not_overwrite_omron_csv_row(self):
        """import_bp skips a (date, time) slot already owned by omron_csv."""
        conn = _make_mem_conn()
        self._seed_bp(conn, "2026-03-10", "08:00", 130, 85, "omron_csv")

        ts = _ts("2026-03-10")
        bp_pairs = {ts: {"systolic": 120.0, "diastolic": 78.0}}
        import_bp(conn, bp_pairs)

        row = conn.execute(
            "SELECT systolic, diastolic, source FROM blood_pressure"
            " WHERE date='2026-03-10' AND time='08:00'"
        ).fetchone()

        self.assertEqual(row["systolic"], 130, "omron_csv systolic must not be overwritten")
        self.assertEqual(row["diastolic"], 85, "omron_csv diastolic must not be overwritten")
        self.assertEqual(row["source"], "omron_csv")

    def test_apple_health_does_not_overwrite_imessage_row(self):
        """import_bp skips a (date, time) slot already owned by imessage."""
        conn = _make_mem_conn()
        self._seed_bp(conn, "2026-03-11", "09:15", 125, 82, "imessage")

        ts = _ts("2026-03-11", "09:15:00")
        bp_pairs = {ts: {"systolic": 118.0, "diastolic": 76.0}}
        import_bp(conn, bp_pairs)

        row = conn.execute(
            "SELECT systolic, source FROM blood_pressure"
            " WHERE date='2026-03-11' AND time='09:15'"
        ).fetchone()

        self.assertEqual(row["systolic"], 125)
        self.assertEqual(row["source"], "imessage")

    def test_apple_health_overwrites_its_own_previous_row(self):
        """import_bp updates a row that already has source='apple_health'."""
        conn = _make_mem_conn()
        self._seed_bp(conn, "2026-03-12", "07:30", 119, 77, "apple_health")

        ts = _ts("2026-03-12", "07:30:00")
        bp_pairs = {ts: {"systolic": 121.0, "diastolic": 79.0}}
        import_bp(conn, bp_pairs)

        row = conn.execute(
            "SELECT systolic, diastolic FROM blood_pressure"
            " WHERE date='2026-03-12' AND time='07:30'"
        ).fetchone()

        self.assertEqual(row["systolic"], 121)
        self.assertEqual(row["diastolic"], 79)

    def test_apple_health_inserts_new_row_when_no_conflict(self):
        """import_bp inserts a new row when there is no existing entry at that (date, time)."""
        conn = _make_mem_conn()

        ts = _ts("2026-03-15", "06:45:00")
        bp_pairs = {ts: {"systolic": 116.0, "diastolic": 74.0}}
        import_bp(conn, bp_pairs)

        row = conn.execute(
            "SELECT systolic, diastolic, source FROM blood_pressure"
            " WHERE date='2026-03-15' AND time='06:45'"
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["systolic"], 116)
        self.assertEqual(row["source"], "apple_health")


class TestBodyMetricsSourcePriority(unittest.TestCase):
    """import_body_metrics() never overwrites rows from withings_api."""

    def _seed_body(self, conn, date, time, weight_lbs, source):
        conn.execute(
            "INSERT INTO body_metrics (date, time, weight_lbs, source)"
            " VALUES (?, ?, ?, ?)",
            (date, time, weight_lbs, source),
        )
        conn.commit()

    def test_apple_health_does_not_overwrite_withings_api_row(self):
        """import_body_metrics leaves withings_api weight untouched."""
        conn = _make_mem_conn()
        self._seed_body(conn, "2026-03-20", "07:00", 185.5, "withings_api")

        # 84 kg * 2.20462 ≈ 185.19 lbs — different from seeded 185.5
        records = [{"ts": _ts("2026-03-20"), "type": "weight_kg", "value": 84.0}]
        import_body_metrics(conn, records)

        row = conn.execute(
            "SELECT weight_lbs, source FROM body_metrics"
            " WHERE date='2026-03-20' AND time='07:00'"
        ).fetchone()

        self.assertAlmostEqual(row["weight_lbs"], 185.5, places=1)
        self.assertEqual(row["source"], "withings_api")

    def test_apple_health_inserts_weight_when_no_conflict(self):
        """import_body_metrics inserts weight_lbs when the slot is free."""
        conn = _make_mem_conn()

        # 80 kg = 176.37 lbs
        records = [{"ts": _ts("2026-03-21", "06:30:00"), "type": "weight_kg", "value": 80.0}]
        import_body_metrics(conn, records)

        row = conn.execute(
            "SELECT weight_lbs, source FROM body_metrics"
            " WHERE date='2026-03-21' AND time='06:30'"
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["weight_lbs"], 80.0 * 2.20462, places=1)
        self.assertEqual(row["source"], "apple_health")

    def test_fat_ratio_converted_from_decimal_to_percent(self):
        """A fat_ratio value of 0.155 is stored as 15.5 (percent)."""
        conn = _make_mem_conn()

        records = [{"ts": _ts("2026-03-22", "08:00:00"), "type": "fat_ratio", "value": 0.155}]
        import_body_metrics(conn, records)

        row = conn.execute(
            "SELECT fat_ratio_pct FROM body_metrics WHERE date='2026-03-22'"
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["fat_ratio_pct"], 15.5, places=2)

    def test_apple_health_overwrites_its_own_body_row(self):
        """import_body_metrics updates a slot already owned by apple_health."""
        conn = _make_mem_conn()
        self._seed_body(conn, "2026-03-23", "08:00", 180.0, "apple_health")

        records = [{"ts": _ts("2026-03-23"), "type": "weight_kg", "value": 82.0}]
        import_body_metrics(conn, records)

        row = conn.execute(
            "SELECT weight_lbs FROM body_metrics WHERE date='2026-03-23' AND time='08:00'"
        ).fetchone()

        self.assertAlmostEqual(row["weight_lbs"], 82.0 * 2.20462, places=1)


class TestStepsAggregation(unittest.TestCase):
    """parse_export() sums StepCount records from different sources on the same date."""

    def _parse(self, xml: str):
        path = _write_tmp_xml(xml)
        try:
            return parse_export(path)
        finally:
            path.unlink(missing_ok=True)

    def test_two_step_records_same_date_are_summed(self):
        """Two StepCount records on the same calendar date are summed into one entry."""
        xml = _xml_document(
            '<Record type="HKQuantityTypeIdentifierStepCount"'
            ' startDate="2026-03-25 08:00:00 -0500" value="3000"/>',
            '<Record type="HKQuantityTypeIdentifierStepCount"'
            ' startDate="2026-03-25 14:00:00 -0500" value="5000"/>',
        )
        _, _, steps_by_date, _, _ = self._parse(xml)

        self.assertIn("2026-03-25", steps_by_date)
        self.assertEqual(steps_by_date["2026-03-25"], 8000.0)

    def test_step_records_on_different_dates_are_separate(self):
        """StepCount records on different dates produce separate aggregated entries."""
        xml = _xml_document(
            '<Record type="HKQuantityTypeIdentifierStepCount"'
            ' startDate="2026-03-26 08:00:00 -0500" value="4000"/>',
            '<Record type="HKQuantityTypeIdentifierStepCount"'
            ' startDate="2026-03-27 09:00:00 -0500" value="6000"/>',
        )
        _, _, steps_by_date, _, _ = self._parse(xml)

        self.assertEqual(steps_by_date.get("2026-03-26"), 4000.0)
        self.assertEqual(steps_by_date.get("2026-03-27"), 6000.0)

    def test_three_step_records_same_date_are_summed(self):
        """Three StepCount records on the same date produce a single summed total."""
        xml = _xml_document(
            '<Record type="HKQuantityTypeIdentifierStepCount"'
            ' startDate="2026-03-28 06:00:00 -0500" value="2000"/>',
            '<Record type="HKQuantityTypeIdentifierStepCount"'
            ' startDate="2026-03-28 12:00:00 -0500" value="3500"/>',
            '<Record type="HKQuantityTypeIdentifierStepCount"'
            ' startDate="2026-03-28 18:00:00 -0500" value="1500"/>',
        )
        _, _, steps_by_date, _, _ = self._parse(xml)

        self.assertEqual(steps_by_date["2026-03-28"], 7000.0)

    def test_import_activity_writes_summed_steps_to_db(self):
        """import_activity() stores the summed step total as a single activity_daily row."""
        conn = _make_mem_conn()
        steps_by_date = {"2026-03-29": 9500.0}
        import_activity(conn, steps_by_date, {})

        row = conn.execute(
            "SELECT steps FROM activity_daily WHERE date='2026-03-29'"
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["steps"], 9500)


class TestWorkoutTypeSStripping(unittest.TestCase):
    """parse_export() strips the HKWorkoutActivityType prefix from workout types."""

    def _parse_workouts(self, xml: str):
        path = _write_tmp_xml(xml)
        try:
            _, _, _, _, workouts = parse_export(path)
            return workouts
        finally:
            path.unlink(missing_ok=True)

    def test_functional_strength_training_prefix_stripped(self):
        """HKWorkoutActivityTypeFunctionalStrengthTraining → FunctionalStrengthTraining."""
        xml = _xml_document(
            '<Workout workoutActivityType="HKWorkoutActivityTypeFunctionalStrengthTraining"'
            ' startDate="2026-03-30 07:00:00 -0500"'
            ' endDate="2026-03-30 07:45:00 -0500"'
            ' duration="45" durationUnit="min"/>'
        )
        workouts = self._parse_workouts(xml)

        self.assertEqual(len(workouts), 1)
        self.assertEqual(workouts[0]["workout_type"], "FunctionalStrengthTraining")

    def test_running_prefix_stripped(self):
        """HKWorkoutActivityTypeRunning → Running."""
        xml = _xml_document(
            '<Workout workoutActivityType="HKWorkoutActivityTypeRunning"'
            ' startDate="2026-03-31 06:00:00 -0500"'
            ' endDate="2026-03-31 06:30:00 -0500"'
            ' duration="30" durationUnit="min"/>'
        )
        workouts = self._parse_workouts(xml)

        self.assertEqual(workouts[0]["workout_type"], "Running")

    def test_workout_without_required_fields_is_dropped(self):
        """A <Workout> element missing workoutActivityType is silently dropped."""
        xml = _xml_document(
            '<Workout startDate="2026-04-01 07:00:00 -0500"'
            ' endDate="2026-04-01 07:30:00 -0500"/>'
        )
        workouts = self._parse_workouts(xml)

        self.assertEqual(len(workouts), 0)


class TestWorkoutHRExtraction(unittest.TestCase):
    """parse_export() extracts avg_hr and max_hr from WorkoutStatistics children."""

    def _parse_workouts(self, xml: str):
        path = _write_tmp_xml(xml)
        try:
            _, _, _, _, workouts = parse_export(path)
            return workouts
        finally:
            path.unlink(missing_ok=True)

    def test_heart_rate_statistics_populates_avg_and_max_hr(self):
        """WorkoutStatistics HR child with average and maximum → avg_hr and max_hr."""
        xml = _xml_document(
            '<Workout workoutActivityType="HKWorkoutActivityTypeRunning"'
            ' startDate="2026-04-02 06:00:00 -0500"'
            ' endDate="2026-04-02 06:30:00 -0500"'
            ' duration="30" durationUnit="min">'
            '  <WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate"'
            '   average="142.5" maximum="168.0"/>'
            '</Workout>'
        )
        workouts = self._parse_workouts(xml)

        self.assertEqual(len(workouts), 1)
        self.assertEqual(workouts[0]["avg_hr"], 142)   # int(float("142.5"))
        self.assertEqual(workouts[0]["max_hr"], 168)

    def test_workout_without_hr_statistics_has_none_avg_and_max(self):
        """A Workout with no HR WorkoutStatistics child has avg_hr=None, max_hr=None."""
        xml = _xml_document(
            '<Workout workoutActivityType="HKWorkoutActivityTypeCycling"'
            ' startDate="2026-04-03 07:00:00 -0500"'
            ' endDate="2026-04-03 08:00:00 -0500"'
            ' duration="60" durationUnit="min"/>'
        )
        workouts = self._parse_workouts(xml)

        self.assertEqual(len(workouts), 1)
        self.assertIsNone(workouts[0]["avg_hr"])
        self.assertIsNone(workouts[0]["max_hr"])

    def test_hr_average_is_truncated_to_int(self):
        """avg_hr is stored as int (floor), not float."""
        xml = _xml_document(
            '<Workout workoutActivityType="HKWorkoutActivityTypeWalking"'
            ' startDate="2026-04-04 08:00:00 -0500"'
            ' endDate="2026-04-04 08:30:00 -0500"'
            ' duration="30" durationUnit="min">'
            '  <WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate"'
            '   average="99.9" maximum="120.0"/>'
            '</Workout>'
        )
        workouts = self._parse_workouts(xml)

        self.assertIsInstance(workouts[0]["avg_hr"], int)
        self.assertEqual(workouts[0]["avg_hr"], 99)


class TestWorkoutCaloriesFromStatistics(unittest.TestCase):
    """parse_export() extracts calories from WorkoutStatistics ActiveEnergyBurned."""

    def _parse_workouts(self, xml: str):
        path = _write_tmp_xml(xml)
        try:
            _, _, _, _, workouts = parse_export(path)
            return workouts
        finally:
            path.unlink(missing_ok=True)

    def test_active_energy_burned_sum_becomes_calories(self):
        """WorkoutStatistics ActiveEnergyBurned sum='263.4' → calories=263."""
        xml = _xml_document(
            '<Workout workoutActivityType="HKWorkoutActivityTypeFunctionalStrengthTraining"'
            ' startDate="2026-04-05 07:00:00 -0500"'
            ' endDate="2026-04-05 08:00:00 -0500"'
            ' duration="60" durationUnit="min">'
            '  <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned"'
            '   sum="263.4"/>'
            '</Workout>'
        )
        workouts = self._parse_workouts(xml)

        self.assertEqual(len(workouts), 1)
        self.assertEqual(workouts[0]["calories"], 263)

    def test_totalenergyburned_attribute_takes_priority_over_statistics(self):
        """When totalEnergyBurned attr is present it takes priority over WorkoutStatistics."""
        xml = _xml_document(
            '<Workout workoutActivityType="HKWorkoutActivityTypeRunning"'
            ' startDate="2026-04-06 06:00:00 -0500"'
            ' endDate="2026-04-06 06:45:00 -0500"'
            ' duration="45" durationUnit="min"'
            ' totalEnergyBurned="300">'
            '  <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned"'
            '   sum="280.0"/>'
            '</Workout>'
        )
        workouts = self._parse_workouts(xml)

        # totalEnergyBurned on the element wins; statistics-based calories is ignored
        self.assertEqual(workouts[0]["calories"], 300.0)

    def test_workout_with_no_calories_source_has_none_calories(self):
        """A Workout with no totalEnergyBurned and no ActiveEnergyBurned stat → calories=None."""
        xml = _xml_document(
            '<Workout workoutActivityType="HKWorkoutActivityTypeYoga"'
            ' startDate="2026-04-07 09:00:00 -0500"'
            ' endDate="2026-04-07 10:00:00 -0500"'
            ' duration="60" durationUnit="min"/>'
        )
        workouts = self._parse_workouts(xml)

        self.assertIsNone(workouts[0]["calories"])

    def test_calories_rounded_to_nearest_int(self):
        """Calories from WorkoutStatistics are rounded (not truncated) to int."""
        xml = _xml_document(
            '<Workout workoutActivityType="HKWorkoutActivityTypeCycling"'
            ' startDate="2026-04-08 07:00:00 -0500"'
            ' endDate="2026-04-08 08:00:00 -0500"'
            ' duration="60" durationUnit="min">'
            '  <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned"'
            '   sum="199.6"/>'
            '</Workout>'
        )
        workouts = self._parse_workouts(xml)

        # round(199.6) = 200
        self.assertEqual(workouts[0]["calories"], 200)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
