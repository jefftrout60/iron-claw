#!/usr/bin/env python3
"""
Behavioral tests for sync_tags() in oura-sync.py.

Run from repo root:
    python3 -m unittest scripts/test_oura_sync_tags.py
"""

import importlib.util
import sqlite3
from typing import Optional
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — mirrors what oura-sync.py does at import time, but we point
# health_db at an in-memory database by patching get_connection after load.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
sys.path.insert(0, str(_HEALTH_DIR))

import health_db  # noqa: E402  — needed before we load the module

# Load oura_sync module from the hyphenated filename
_OURA_SYNC_PATH = Path(__file__).parent / "oura-sync.py"
spec = importlib.util.spec_from_file_location("oura_sync", _OURA_SYNC_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_in_memory_conn() -> sqlite3.Connection:
    """Return a fully-initialised in-memory health DB connection."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    health_db.initialize_schema(conn)
    return conn


def _make_tag(
    id: str = "abc",
    start_day: str = "2026-04-25",
    tag_type_code: str = "tag_sleep_sauna",
    start_time: str = "19:00:00+00:00",
    end_time=None,
    comment: str = "",
) -> dict:
    """Convenience factory for an enhanced_tag API record."""
    return {
        "id": id,
        "start_day": start_day,
        "tag_type_code": tag_type_code,
        "start_time": start_time,
        "end_time": end_time,
        "comment": comment,
    }


def _run_sync(conn, records: list[dict], *, start: str = "2026-04-01", end: str = "2026-04-30") -> None:
    """
    Call sync_tags() with fetch_all patched to return *records*.
    headers are irrelevant because fetch_all is mocked.
    """
    original_fetch_all = mod.fetch_all
    try:
        mod.fetch_all = lambda resource, chunk_start, chunk_end, headers: records
        mod.sync_tags(conn, headers={}, start=start, end=end)
    finally:
        mod.fetch_all = original_fetch_all


def _get_tag_rows(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM oura_tags ORDER BY id").fetchall()


def _get_sync_state(conn, resource: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sync_state WHERE resource = ?", (resource,)
    ).fetchone()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSyncTagsFieldMapping(unittest.TestCase):
    """P1-1: start_day → day column; P1-2: tag_type_code → tag_type column."""

    def setUp(self):
        self.conn = _make_in_memory_conn()

    def tearDown(self):
        self.conn.close()

    def test_start_day_written_to_day_column(self):
        """start_day from API response must be stored as the day column value."""
        record = _make_tag(id="abc", start_day="2026-04-25", tag_type_code="tag_sleep_sauna")
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        self.assertEqual(len(rows), 1, "Expected exactly 1 row in oura_tags")
        print(f"[DEBUG] day column value: {rows[0]['day']!r}")
        self.assertEqual(rows[0]["day"], "2026-04-25")

    def test_tag_type_code_written_to_tag_type_column(self):
        """tag_type_code from API response must be stored as the tag_type column value."""
        record = _make_tag(id="abc", start_day="2026-04-25", tag_type_code="tag_sleep_sauna")
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        self.assertEqual(len(rows), 1)
        print(f"[DEBUG] tag_type column value: {rows[0]['tag_type']!r}")
        self.assertEqual(rows[0]["tag_type"], "tag_sleep_sauna")

    def test_id_stored_correctly(self):
        """The id field is passed through verbatim."""
        record = _make_tag(id="unique-id-xyz", start_day="2026-04-25")
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        self.assertEqual(rows[0]["id"], "unique-id-xyz")

    def test_start_time_stored(self):
        """start_time is persisted from the API record."""
        record = _make_tag(id="t1", start_day="2026-04-25", start_time="19:00:00+00:00")
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        print(f"[DEBUG] start_time column value: {rows[0]['start_time']!r}")
        self.assertEqual(rows[0]["start_time"], "19:00:00+00:00")


class TestSyncTagsFallbackDayField(unittest.TestCase):
    """Ensure fallback to 'day' key works when start_day is absent."""

    def setUp(self):
        self.conn = _make_in_memory_conn()

    def tearDown(self):
        self.conn.close()

    def test_fallback_to_day_when_start_day_absent(self):
        """If start_day is missing, the fallback rec.get('day') is used."""
        record = {
            "id": "fallback-1",
            "day": "2026-04-20",
            # no start_day key
            "tag_type_code": "tag_meditation",
            "start_time": None,
            "end_time": None,
            "comment": None,
        }
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        self.assertEqual(len(rows), 1)
        print(f"[DEBUG] fallback day value: {rows[0]['day']!r}")
        self.assertEqual(rows[0]["day"], "2026-04-20")


class TestSyncTagsUpsertIdempotency(unittest.TestCase):
    """P1-3: calling sync_tags twice with the same record yields exactly 1 row."""

    def setUp(self):
        self.conn = _make_in_memory_conn()

    def tearDown(self):
        self.conn.close()

    def test_duplicate_insert_produces_one_row(self):
        """INSERT OR REPLACE — identical records must not duplicate."""
        record = _make_tag(id="dup-1", start_day="2026-04-25")
        _run_sync(self.conn, [record])
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        print(f"[DEBUG] row count after 2 syncs with same record: {len(rows)}")
        self.assertEqual(len(rows), 1, "Upsert must produce exactly 1 row, not 2")

    def test_multiple_unique_records_all_stored(self):
        """Distinct ids are stored as separate rows."""
        records = [
            _make_tag(id="r1", start_day="2026-04-21", tag_type_code="tag_meditation"),
            _make_tag(id="r2", start_day="2026-04-22", tag_type_code="tag_sleep_sauna"),
            _make_tag(id="r3", start_day="2026-04-23", tag_type_code="tag_workout"),
        ]
        _run_sync(self.conn, records)

        rows = _get_tag_rows(self.conn)
        print(f"[DEBUG] distinct record count: {len(rows)}")
        self.assertEqual(len(rows), 3)

    def test_upsert_updates_tag_type_on_replace(self):
        """Re-syncing same id with changed tag_type_code overwrites the row."""
        record_v1 = _make_tag(id="upd-1", start_day="2026-04-25", tag_type_code="tag_old")
        _run_sync(self.conn, [record_v1])

        record_v2 = _make_tag(id="upd-1", start_day="2026-04-25", tag_type_code="tag_new")
        _run_sync(self.conn, [record_v2])

        rows = _get_tag_rows(self.conn)
        self.assertEqual(len(rows), 1)
        print(f"[DEBUG] tag_type after upsert update: {rows[0]['tag_type']!r}")
        self.assertEqual(rows[0]["tag_type"], "tag_new")


class TestSyncTagsSyncState(unittest.TestCase):
    """P1-4: sync_state must have resource='tags' and last_synced=end after sync."""

    def setUp(self):
        self.conn = _make_in_memory_conn()

    def tearDown(self):
        self.conn.close()

    def test_sync_state_row_created(self):
        """sync_state must contain a row for resource='tags' after sync."""
        _run_sync(self.conn, [], start="2026-04-01", end="2026-04-30")

        row = _get_sync_state(self.conn, "tags")
        print(f"[DEBUG] sync_state row: {dict(row) if row else None}")
        self.assertIsNotNone(row, "sync_state must have a 'tags' row after sync")

    def test_sync_state_last_synced_equals_end_date(self):
        """last_synced must equal the end_date argument passed to sync_tags."""
        end_date = "2026-04-30"
        _run_sync(self.conn, [], start="2026-04-01", end=end_date)

        row = _get_sync_state(self.conn, "tags")
        print(f"[DEBUG] last_synced: {row['last_synced']!r}, expected: {end_date!r}")
        self.assertEqual(row["last_synced"], end_date)

    def test_sync_state_updated_on_second_run(self):
        """A second sync with a later end_date overwrites last_synced."""
        _run_sync(self.conn, [], start="2026-04-01", end="2026-04-15")
        _run_sync(self.conn, [], start="2026-04-16", end="2026-04-30")

        row = _get_sync_state(self.conn, "tags")
        print(f"[DEBUG] last_synced after second run: {row['last_synced']!r}")
        self.assertEqual(row["last_synced"], "2026-04-30")


class TestSyncTagsNullComment(unittest.TestCase):
    """P1-5: empty string and None comment must not cause errors; row is inserted."""

    def setUp(self):
        self.conn = _make_in_memory_conn()

    def tearDown(self):
        self.conn.close()

    def test_empty_string_comment_inserts_without_error(self):
        """comment='' must not raise and the row must be present."""
        record = _make_tag(id="c1", comment="")
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        self.assertEqual(len(rows), 1)
        print(f"[DEBUG] comment for empty-string record: {rows[0]['comment']!r}")
        # empty string stored as empty string (SQLite passes it through)
        self.assertEqual(rows[0]["comment"], "")

    def test_none_comment_inserts_without_error(self):
        """comment=None must not raise and the row must be present."""
        record = _make_tag(id="c2", comment=None)
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        self.assertEqual(len(rows), 1)
        print(f"[DEBUG] comment for None record: {rows[0]['comment']!r}")
        self.assertIsNone(rows[0]["comment"])

    def test_none_end_time_inserts_without_error(self):
        """end_time=None (common for open-ended tags) must not raise."""
        record = _make_tag(id="c3", end_time=None)
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        self.assertEqual(len(rows), 1)
        print(f"[DEBUG] end_time for None record: {rows[0]['end_time']!r}")
        self.assertIsNone(rows[0]["end_time"])

    def test_mixed_null_fields_full_record_inserted(self):
        """A record matching the exact P1 spec (end_time=None, comment='') is inserted correctly."""
        record = {
            "id": "abc",
            "start_day": "2026-04-25",
            "tag_type_code": "tag_sleep_sauna",
            "start_time": "19:00:00+00:00",
            "end_time": None,
            "comment": "",
        }
        _run_sync(self.conn, [record])

        rows = _get_tag_rows(self.conn)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        print(f"[DEBUG] full P1 spec row — id={r['id']!r} day={r['day']!r} "
              f"tag_type={r['tag_type']!r} start_time={r['start_time']!r} "
              f"end_time={r['end_time']!r} comment={r['comment']!r}")
        self.assertEqual(r["id"], "abc")
        self.assertEqual(r["day"], "2026-04-25")
        self.assertEqual(r["tag_type"], "tag_sleep_sauna")
        self.assertEqual(r["start_time"], "19:00:00+00:00")
        self.assertIsNone(r["end_time"])
        self.assertEqual(r["comment"], "")


class TestSyncTagsEmptyApiResponse(unittest.TestCase):
    """Edge case: empty API response still writes sync_state, no rows inserted."""

    def setUp(self):
        self.conn = _make_in_memory_conn()

    def tearDown(self):
        self.conn.close()

    def test_empty_response_no_rows_inserted(self):
        """No records from API → oura_tags table stays empty."""
        _run_sync(self.conn, [])
        rows = _get_tag_rows(self.conn)
        print(f"[DEBUG] row count for empty API response: {len(rows)}")
        self.assertEqual(len(rows), 0)

    def test_empty_response_still_updates_sync_state(self):
        """Even with no records, sync_state is stamped so we don't re-fetch."""
        _run_sync(self.conn, [], start="2026-04-01", end="2026-04-30")
        row = _get_sync_state(self.conn, "tags")
        self.assertIsNotNone(row)
        self.assertEqual(row["last_synced"], "2026-04-30")


if __name__ == "__main__":
    unittest.main(verbosity=2)
