#!/usr/bin/env python3
"""
Behavioral tests for scripts/import-apple-health-json.py.

All tests use an in-memory SQLite DB so they never touch health.db on disk.
Functions under test are imported directly from the importer module;
the module's own sys.path manipulation brings health_db in.
"""

import importlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path

# Ensure health/ (where health_db lives) is on sys.path before we import
# anything, mirroring what the importer itself does at startup.
_HEALTH_DIR = Path(__file__).parent
sys.path.insert(0, str(_HEALTH_DIR))

import health_db

# Import the importer module.  It lives in scripts/ two levels up from here.
_SCRIPTS_DIR = _HEALTH_DIR.parent.parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))
import importlib.util as _ilu

_IMPORTER_PATH = _SCRIPTS_DIR / "import-apple-health-json.py"
_spec = _ilu.spec_from_file_location("import_apple_health_json", _IMPORTER_PATH)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Aliases for the public API we test
parse_metrics = _mod.parse_metrics
_parse_state_of_mind_direct = _mod._parse_state_of_mind_direct
_date_str = _mod._date_str
_ts_str = _mod._ts_str
import_activity = _mod.import_activity
import_body_metrics = _mod.import_body_metrics
import_state_of_mind = _mod.import_state_of_mind
_extract_hr = _mod._extract_hr
parse_workouts = _mod.parse_workouts

# Load the XML importer for regression tests.
# The importer uses `dict | None` union syntax (PEP 604) in function annotations,
# which requires Python 3.10+.  Prepend `from __future__ import annotations` so
# that all annotations are treated as strings at runtime, making it safe on 3.9.
import types as _types
_XML_IMPORTER_PATH = Path(__file__).parent.parent.parent.parent.parent / "scripts" / "import-apple-health.py"
_xml_source = "from __future__ import annotations\n" + _XML_IMPORTER_PATH.read_text(encoding="utf-8")
_xml_mod = _types.ModuleType("import_apple_health_xml")
_xml_mod.__file__ = str(_XML_IMPORTER_PATH)
exec(compile(_xml_source, str(_XML_IMPORTER_PATH), "exec"), _xml_mod.__dict__)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_conn():
    """
    Return an in-memory SQLite connection with the full health schema.

    We cannot call health_db.get_connection(db_path=":memory:") because it
    calls Path(":memory:").parent.mkdir() which would fail.  Instead we open
    the connection directly and call initialize_schema ourselves, exactly as
    get_connection does minus the filesystem parts.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    health_db.initialize_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 1. Date extraction helpers
# ---------------------------------------------------------------------------

class TestDateHelpers(unittest.TestCase):

    def test_date_str_extracts_yyyy_mm_dd(self):
        result = _date_str("2026-04-30 00:26:00 -0600")
        self.assertEqual(result, "2026-04-30")

    def test_date_str_different_offset(self):
        result = _date_str("2024-01-15 08:30:00 -0800")
        self.assertEqual(result, "2024-01-15")

    def test_ts_str_returns_hhmm_from_raw(self):
        # _ts_str returns the first 19 chars; callers slice [11:16] for HH:MM
        raw = "2026-04-30 14:27:00 -0600"
        ts19 = _ts_str(raw)
        # full 19-char slice
        self.assertEqual(ts19, "2026-04-30 14:27:00")
        # caller convention: HH:MM portion
        self.assertEqual(ts19[11:16], "14:27")

    def test_ts_str_midnight(self):
        raw = "2026-04-30 00:00:00 -0500"
        self.assertEqual(_ts_str(raw)[11:16], "00:00")


# ---------------------------------------------------------------------------
# 2. METRIC_MAP dispatch — step_count recognised; unknown name silently skipped
# ---------------------------------------------------------------------------

class TestMetricMapDispatch(unittest.TestCase):

    def _steps_data(self, date_str="2026-04-01 00:00:00 -0600", qty=1000):
        return {
            "data": {
                "metrics": [
                    {
                        "name": "step_count",
                        "units": "count",
                        "data": [{"date": date_str, "qty": qty}],
                    }
                ]
            }
        }

    def test_step_count_produces_nonempty_steps_by_date(self):
        raw = self._steps_data()
        data = raw.get("data", raw)
        _, steps_by_date, _, _ = parse_metrics(data)
        self.assertGreater(len(steps_by_date), 0)

    def test_unrecognised_metric_skipped_no_exception(self):
        data = {
            "metrics": [
                {
                    "name": "completely_unknown_metric_xyz",
                    "units": "count",
                    "data": [{"date": "2026-04-01 00:00:00 -0600", "qty": 42}],
                }
            ]
        }
        # Should not raise; all output dicts/lists should be empty
        body_records, steps_by_date, daylight_by_date, som_raw = parse_metrics(data)
        self.assertEqual(body_records, [])
        self.assertEqual(steps_by_date, {})
        self.assertEqual(daylight_by_date, {})
        self.assertEqual(som_raw, [])

    def test_empty_metrics_list_returns_empty_outputs(self):
        body_records, steps_by_date, daylight_by_date, som_raw = parse_metrics({"metrics": []})
        self.assertEqual(body_records, [])
        self.assertEqual(steps_by_date, {})
        self.assertEqual(daylight_by_date, {})
        self.assertEqual(som_raw, [])

    def test_missing_metrics_key_returns_empty_outputs(self):
        body_records, steps_by_date, daylight_by_date, som_raw = parse_metrics({})
        self.assertEqual(body_records, [])
        self.assertEqual(steps_by_date, {})


# ---------------------------------------------------------------------------
# 3. Steps aggregation
# ---------------------------------------------------------------------------

class TestStepsAggregation(unittest.TestCase):

    def _make_step_metric(self, entries):
        return {
            "metrics": [
                {"name": "step_count", "units": "count", "data": entries}
            ]
        }

    def test_multiple_entries_same_date_are_summed(self):
        entries = [
            {"date": "2026-04-01 08:00:00 -0600", "qty": 3000},
            {"date": "2026-04-01 14:00:00 -0600", "qty": 4500},
            {"date": "2026-04-01 20:00:00 -0600", "qty": 2000},
        ]
        data = self._make_step_metric(entries)
        _, steps_by_date, _, _ = parse_metrics(data)
        self.assertAlmostEqual(steps_by_date["2026-04-01"], 9500.0, places=1)

    def test_entries_on_different_dates_kept_separately(self):
        entries = [
            {"date": "2026-04-01 08:00:00 -0600", "qty": 5000},
            {"date": "2026-04-02 08:00:00 -0600", "qty": 7000},
        ]
        data = self._make_step_metric(entries)
        _, steps_by_date, _, _ = parse_metrics(data)
        self.assertAlmostEqual(steps_by_date["2026-04-01"], 5000.0, places=1)
        self.assertAlmostEqual(steps_by_date["2026-04-02"], 7000.0, places=1)
        self.assertEqual(len(steps_by_date), 2)

    def test_entry_without_qty_is_skipped(self):
        entries = [
            {"date": "2026-04-01 08:00:00 -0600"},           # no qty
            {"date": "2026-04-01 10:00:00 -0600", "qty": 1234},
        ]
        data = self._make_step_metric(entries)
        _, steps_by_date, _, _ = parse_metrics(data)
        self.assertAlmostEqual(steps_by_date["2026-04-01"], 1234.0, places=1)

    def test_entry_without_date_is_skipped(self):
        entries = [
            {"qty": 999},  # no date — must not crash
            {"date": "2026-04-01 08:00:00 -0600", "qty": 500},
        ]
        data = self._make_step_metric(entries)
        _, steps_by_date, _, _ = parse_metrics(data)
        self.assertAlmostEqual(steps_by_date["2026-04-01"], 500.0, places=1)


# ---------------------------------------------------------------------------
# 4. Daylight aggregation
# ---------------------------------------------------------------------------

class TestDaylightAggregation(unittest.TestCase):

    def _make_daylight_metric(self, entries):
        return {
            "metrics": [
                {"name": "time_in_daylight", "units": "min", "data": entries}
            ]
        }

    def test_multiple_entries_same_date_are_summed(self):
        entries = [
            {"date": "2026-04-15 09:00:00 -0600", "qty": 20},
            {"date": "2026-04-15 13:00:00 -0600", "qty": 35},
        ]
        data = self._make_daylight_metric(entries)
        _, _, daylight_by_date, _ = parse_metrics(data)
        self.assertAlmostEqual(daylight_by_date["2026-04-15"], 55.0, places=1)

    def test_entries_on_different_dates_kept_separately(self):
        entries = [
            {"date": "2026-04-15 09:00:00 -0600", "qty": 30},
            {"date": "2026-04-16 09:00:00 -0600", "qty": 45},
        ]
        data = self._make_daylight_metric(entries)
        _, _, daylight_by_date, _ = parse_metrics(data)
        self.assertEqual(len(daylight_by_date), 2)
        self.assertAlmostEqual(daylight_by_date["2026-04-15"], 30.0, places=1)
        self.assertAlmostEqual(daylight_by_date["2026-04-16"], 45.0, places=1)


# ---------------------------------------------------------------------------
# 5. Fat ratio detection
# ---------------------------------------------------------------------------

class TestFatRatioDetection(unittest.TestCase):

    def _make_fat_metric(self, qty_value):
        return {
            "metrics": [
                {
                    "name": "body_fat_percentage",
                    "units": "%",
                    "data": [{"date": "2026-04-01 08:00:00 -0600", "qty": qty_value}],
                }
            ]
        }

    def _get_fat_pct(self, qty_value):
        data = self._make_fat_metric(qty_value)
        body_records, _, _, _ = parse_metrics(data)
        fat_records = [r for r in body_records if r["type"] == "fat_ratio_pct"]
        self.assertEqual(len(fat_records), 1, "Expected exactly one fat_ratio_pct record")
        return fat_records[0]["value"]

    def test_decimal_0_185_is_multiplied_to_18_5(self):
        result = self._get_fat_pct(0.185)
        self.assertAlmostEqual(result, 18.5, places=4)

    def test_percent_18_5_is_left_as_18_5(self):
        result = self._get_fat_pct(18.5)
        self.assertAlmostEqual(result, 18.5, places=4)

    def test_value_1_0_treated_as_decimal(self):
        # 1.0% body fat is physiologically impossible; must be treated as decimal
        result = self._get_fat_pct(1.0)
        self.assertAlmostEqual(result, 100.0, places=4)

    def test_value_25_is_left_as_percent(self):
        result = self._get_fat_pct(25.0)
        self.assertAlmostEqual(result, 25.0, places=4)

    def test_value_0_0_treated_as_decimal(self):
        # Edge: 0.0 → 0.0 * 100 = 0.0
        result = self._get_fat_pct(0.0)
        self.assertAlmostEqual(result, 0.0, places=4)


# ---------------------------------------------------------------------------
# 6. _parse_state_of_mind_direct
# ---------------------------------------------------------------------------

class TestParseStateOfMindDirect(unittest.TestCase):

    def _valid_entry(self):
        return {
            "start": "2026-04-30T18:26:49Z",
            "end":   "2026-04-30T18:26:50Z",
            "kind":  "daily_mood",
            "valence": 0.75,
            "labels": ["calm", "focused"],
            "associations": ["nature", "work"],
            "id": "abc-123",
        }

    def test_date_extracted_from_iso8601_utc_start(self):
        results = _parse_state_of_mind_direct([self._valid_entry()])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["date"], "2026-04-30")

    def test_logged_at_is_space_separated_datetime(self):
        results = _parse_state_of_mind_direct([self._valid_entry()])
        self.assertEqual(results[0]["logged_at"], "2026-04-30 18:26:49")

    def test_kind_is_preserved(self):
        results = _parse_state_of_mind_direct([self._valid_entry()])
        self.assertEqual(results[0]["kind"], "daily_mood")

    def test_valence_is_float(self):
        results = _parse_state_of_mind_direct([self._valid_entry()])
        self.assertAlmostEqual(results[0]["valence"], 0.75, places=5)

    def test_labels_stored_as_json_array_string(self):
        results = _parse_state_of_mind_direct([self._valid_entry()])
        labels_raw = results[0]["labels"]
        # Must be a JSON-encoded string, not a Python list
        self.assertIsInstance(labels_raw, str)
        parsed = json.loads(labels_raw)
        self.assertEqual(parsed, ["calm", "focused"])

    def test_associations_stored_as_json_array_string(self):
        results = _parse_state_of_mind_direct([self._valid_entry()])
        assoc_raw = results[0]["associations"]
        self.assertIsInstance(assoc_raw, str)
        parsed = json.loads(assoc_raw)
        self.assertEqual(parsed, ["nature", "work"])

    def test_missing_valence_produces_none(self):
        entry = self._valid_entry()
        del entry["valence"]
        results = _parse_state_of_mind_direct([entry])
        self.assertIsNone(results[0]["valence"])

    def test_missing_labels_produces_empty_json_array(self):
        entry = self._valid_entry()
        del entry["labels"]
        results = _parse_state_of_mind_direct([entry])
        self.assertEqual(results[0]["labels"], "[]")

    def test_missing_associations_produces_empty_json_array(self):
        entry = self._valid_entry()
        del entry["associations"]
        results = _parse_state_of_mind_direct([entry])
        self.assertEqual(results[0]["associations"], "[]")

    def test_entry_without_start_is_skipped(self):
        entry = self._valid_entry()
        del entry["start"]
        results = _parse_state_of_mind_direct([entry])
        self.assertEqual(results, [])

    def test_empty_start_is_skipped(self):
        entry = self._valid_entry()
        entry["start"] = ""
        results = _parse_state_of_mind_direct([entry])
        self.assertEqual(results, [])

    def test_multiple_entries_all_parsed(self):
        entries = [
            {**self._valid_entry(), "start": "2026-04-28T10:00:00Z", "valence": 0.5},
            {**self._valid_entry(), "start": "2026-04-29T10:00:00Z", "valence": 0.6},
        ]
        results = _parse_state_of_mind_direct(entries)
        self.assertEqual(len(results), 2)
        dates = [r["date"] for r in results]
        self.assertIn("2026-04-28", dates)
        self.assertIn("2026-04-29", dates)

    def test_arousal_is_always_none(self):
        # Apple's export does not include arousal; the parser always sets it to None
        results = _parse_state_of_mind_direct([self._valid_entry()])
        self.assertIsNone(results[0]["arousal"])


# ---------------------------------------------------------------------------
# 7. Top-level JSON envelope unwrapping
# ---------------------------------------------------------------------------

class TestJsonEnvelopeUnwrap(unittest.TestCase):
    """
    Tests the `data = raw.get("data", raw)` pattern used in main().
    We call parse_metrics() with both envelope shapes to confirm correct dispatch.
    """

    def _metric_payload(self, step_qty=1234):
        return {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [{"date": "2026-04-10 08:00:00 -0600", "qty": step_qty}],
                }
            ]
        }

    def test_nested_data_envelope_parsed(self):
        raw = {"data": self._metric_payload(5555)}
        data = raw.get("data", raw)
        _, steps_by_date, _, _ = parse_metrics(data)
        self.assertAlmostEqual(steps_by_date.get("2026-04-10", 0), 5555.0, places=1)

    def test_flat_envelope_parsed(self):
        # No "data" wrapper — raw IS the data dict
        raw = self._metric_payload(7777)
        data = raw.get("data", raw)
        _, steps_by_date, _, _ = parse_metrics(data)
        self.assertAlmostEqual(steps_by_date.get("2026-04-10", 0), 7777.0, places=1)

    def test_state_of_mind_file_envelope(self):
        # {"data": {"stateOfMind": [...]}} shape
        som_entry = {
            "start": "2026-04-30T20:00:00Z",
            "kind":  "daily_mood",
            "valence": 0.8,
            "labels": [],
            "associations": [],
        }
        raw = {"data": {"stateOfMind": [som_entry]}}
        data = raw.get("data", raw)
        som_direct = _parse_state_of_mind_direct(data.get("stateOfMind", []))
        self.assertEqual(len(som_direct), 1)
        self.assertEqual(som_direct[0]["date"], "2026-04-30")

    def test_state_of_mind_missing_key_returns_empty(self):
        # No stateOfMind key in data → empty list, no exception
        raw = {"data": {"metrics": []}}
        data = raw.get("data", raw)
        som_direct = _parse_state_of_mind_direct(data.get("stateOfMind", []))
        self.assertEqual(som_direct, [])


# ---------------------------------------------------------------------------
# 8. Dry-run: no DB writes
# ---------------------------------------------------------------------------

class TestDryRunNoDbWrites(unittest.TestCase):
    """
    Verify that with --dry-run logic (i.e. calling only the parse functions
    and not the import_ functions), the DB remains empty.
    """

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_parse_metrics_does_not_write_to_db(self):
        data = {
            "metrics": [
                {
                    "name": "step_count",
                    "units": "count",
                    "data": [{"date": "2026-04-01 08:00:00 -0600", "qty": 5000}],
                }
            ]
        }
        parse_metrics(data)
        row = self.conn.execute("SELECT COUNT(*) FROM activity_daily").fetchone()
        self.assertEqual(row[0], 0, "parse_metrics must not write to the DB")

    def test_import_activity_writes_to_db(self):
        # Confirm the import function DOES write (so we know the dry-run test
        # above is meaningful, not just vacuously true)
        import_activity(self.conn, {"2026-04-01": 5000}, {})
        row = self.conn.execute("SELECT COUNT(*) FROM activity_daily").fetchone()
        self.assertEqual(row[0], 1)

    def test_import_state_of_mind_writes_to_db(self):
        records = _parse_state_of_mind_direct([
            {
                "start": "2026-04-30T18:26:49Z",
                "kind": "daily_mood",
                "valence": 0.7,
                "labels": [],
                "associations": [],
            }
        ])
        import_state_of_mind(self.conn, records)
        row = self.conn.execute("SELECT COUNT(*) FROM state_of_mind").fetchone()
        self.assertEqual(row[0], 1)


# ---------------------------------------------------------------------------
# 9. Activity DB round-trip (steps + daylight upsert)
# ---------------------------------------------------------------------------

class TestActivityDbRoundTrip(unittest.TestCase):

    def setUp(self):
        self.conn = _make_conn()

    def tearDown(self):
        self.conn.close()

    def test_steps_are_written_to_activity_daily(self):
        import_activity(self.conn, {"2026-04-01": 8500}, {})
        row = self.conn.execute(
            "SELECT steps FROM activity_daily WHERE date = '2026-04-01'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["steps"], 8500)

    def test_daylight_written_to_activity_daily(self):
        import_activity(self.conn, {}, {"2026-04-05": 47.5})
        row = self.conn.execute(
            "SELECT daylight_minutes FROM activity_daily WHERE date = '2026-04-05'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["daylight_minutes"], 47.5, places=2)

    def test_reimport_overwrites_steps(self):
        import_activity(self.conn, {"2026-04-01": 5000}, {})
        import_activity(self.conn, {"2026-04-01": 9999}, {})
        row = self.conn.execute(
            "SELECT steps FROM activity_daily WHERE date = '2026-04-01'"
        ).fetchone()
        self.assertEqual(row["steps"], 9999)

    def test_source_is_health_auto_export(self):
        import_activity(self.conn, {"2026-04-01": 1000}, {})
        row = self.conn.execute(
            "SELECT source FROM activity_daily WHERE date = '2026-04-01'"
        ).fetchone()
        self.assertEqual(row["source"], "health_auto_export")


# ---------------------------------------------------------------------------
# 10. _extract_hr helper
# ---------------------------------------------------------------------------

class TestExtractHr(unittest.TestCase):

    def test_dict_qty_truncates_to_int(self):
        # {"qty": 102.9} → 102 (int truncation, not rounding)
        self.assertEqual(_extract_hr({"qty": 102.9}), 102)

    def test_plain_scalar_integer(self):
        self.assertEqual(_extract_hr(111), 111)

    def test_plain_scalar_zero_returns_none(self):
        # 0 is falsy — treated as "no reading"
        self.assertIsNone(_extract_hr(0))

    def test_none_input_returns_none(self):
        self.assertIsNone(_extract_hr(None))

    def test_dict_qty_zero_returns_none(self):
        self.assertIsNone(_extract_hr({"qty": 0}))

    def test_non_numeric_string_returns_none(self):
        self.assertIsNone(_extract_hr("abc"))


# ---------------------------------------------------------------------------
# 11. parse_workouts — new fields (min_hr, intensity_met, top-level HR fallback)
# ---------------------------------------------------------------------------

class TestParseWorkoutsNewFields(unittest.TestCase):

    def _base_workout(self):
        return {
            "name": "Cross Training",
            "start": "2026-05-01 15:28:42 -0600",
            "end": "2026-05-01 16:36:06 -0600",
            "duration": 3846.5,
            "heartRate": {
                "avg": {"qty": 102.9, "units": "bpm"},
                "max": {"qty": 111, "units": "bpm"},
                "min": {"qty": 97, "units": "bpm"},
            },
            "intensity": {"qty": 3.585, "units": "kcal/hr·kg"},
            "activeEnergyBurned": {"qty": 407.8, "units": "kcal"},
        }

    def _parse_single(self, workout_dict):
        result = parse_workouts({"workouts": [workout_dict]})
        self.assertEqual(len(result), 1, "Expected exactly one parsed workout")
        return result[0]

    def test_min_hr_extracted_from_heart_rate_min(self):
        w = self._parse_single(self._base_workout())
        self.assertEqual(w["min_hr"], 97)

    def test_intensity_met_extracted_and_rounded(self):
        w = self._parse_single(self._base_workout())
        self.assertAlmostEqual(w["intensity_met"], 3.585, places=3)

    def test_avg_hr_from_top_level_scalar_when_heart_rate_absent(self):
        workout = self._base_workout()
        del workout["heartRate"]
        workout["avgHeartRate"] = {"qty": 102.9}
        w = self._parse_single(workout)
        self.assertEqual(w["avg_hr"], 102)

    def test_max_hr_from_top_level_scalar_when_heart_rate_absent(self):
        workout = self._base_workout()
        del workout["heartRate"]
        workout["maxHeartRate"] = {"qty": 111}
        w = self._parse_single(workout)
        self.assertEqual(w["max_hr"], 111)

    def test_hr_fields_none_when_no_heart_rate_data(self):
        workout = self._base_workout()
        del workout["heartRate"]
        w = self._parse_single(workout)
        self.assertIsNone(w["avg_hr"])
        self.assertIsNone(w["max_hr"])
        self.assertIsNone(w["min_hr"])

    def test_intensity_met_none_when_intensity_absent(self):
        workout = self._base_workout()
        del workout["intensity"]
        w = self._parse_single(workout)
        self.assertIsNone(w["intensity_met"])


# ---------------------------------------------------------------------------
# 12. XML importer — weight unit fix regression tests
# ---------------------------------------------------------------------------

class TestXmlImportBodyMetrics(unittest.TestCase):
    """
    Regression tests for the weight unit fix in import-apple-health.py.

    Before the fix, weight_lbs records were incorrectly multiplied by 2.20462
    (i.e. treated as kg). The fix: weight_lbs stored at face value; weight_kg
    converted to lbs. Also verifies the source guard: withings_api rows are
    never overwritten by apple_health rows.
    """

    def setUp(self):
        self.conn = _make_conn()
        self.import_body_metrics = _xml_mod.import_body_metrics

    def tearDown(self):
        self.conn.close()

    def test_weight_lbs_stored_at_face_value(self):
        # A record already in lbs must be stored as-is — NOT multiplied by 2.20462
        records = [{"ts": "2026-04-27 08:00:00 -0600", "type": "weight_lbs", "value": 230.96}]
        self.import_body_metrics(self.conn, records)
        row = self.conn.execute(
            "SELECT weight_lbs FROM body_metrics WHERE date = '2026-04-27'"
        ).fetchone()
        self.assertIsNotNone(row, "Expected a body_metrics row for 2026-04-27")
        stored = row[0]
        # Must be close to 230.96, NOT ~509 (which would be 230.96 × 2.20462)
        self.assertAlmostEqual(stored, 230.96, places=1,
                               msg=f"weight_lbs stored as {stored}, expected ~230.96 (not ~509)")

    def test_weight_kg_converted_to_lbs(self):
        # A record in kg must be converted: 104.76 kg × 2.20462 ≈ 230.98 lbs
        records = [{"ts": "2026-04-27 09:00:00 -0600", "type": "weight_kg", "value": 104.76}]
        self.import_body_metrics(self.conn, records)
        row = self.conn.execute(
            "SELECT weight_lbs FROM body_metrics WHERE date = '2026-04-27'"
        ).fetchone()
        self.assertIsNotNone(row, "Expected a body_metrics row for 2026-04-27")
        expected_lbs = round(104.76 * 2.20462, 2)
        self.assertAlmostEqual(row[0], expected_lbs, places=1,
                               msg=f"weight_kg stored as {row[0]}, expected ~{expected_lbs}")

    def test_withings_api_row_not_overwritten_by_apple_health(self):
        # Pre-seed a withings_api row at the same (date, time)
        self.conn.execute(
            """
            INSERT INTO body_metrics (date, time, weight_lbs, source)
            VALUES ('2026-04-27', '08:00', 231.50, 'withings_api')
            """
        )
        self.conn.commit()

        # Attempt to import an apple_health row at the same (date, time)
        records = [{"ts": "2026-04-27 08:00:00 -0600", "type": "weight_lbs", "value": 230.96}]
        self.import_body_metrics(self.conn, records)

        row = self.conn.execute(
            "SELECT weight_lbs, source FROM body_metrics WHERE date = '2026-04-27' AND time = '08:00'"
        ).fetchone()
        self.assertIsNotNone(row)
        # withings_api value must be preserved
        self.assertAlmostEqual(row[0], 231.50, places=2,
                               msg="withings_api row was overwritten by apple_health import")
        self.assertEqual(row[1], "withings_api",
                         msg=f"source changed to {row[1]!r} — withings_api row was overwritten")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
