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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
