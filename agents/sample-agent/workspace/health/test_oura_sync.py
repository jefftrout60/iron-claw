#!/usr/bin/env python3
"""
Tests for oura-sync.py: FetchError signaling, per-chunk last_synced tracking,
and oura_heartrate retention cleanup.

Import strategy: oura-sync.py is a script, not a package, so we load it with
importlib.  The module-level imports (health_db, keychain) succeed because:
  - health_db is a pure SQLite module
  - keychain only calls the OS at function-call time, not at import time
"""

import importlib.util
import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

# ---------------------------------------------------------------------------
# Load oura-sync.py as a module
# ---------------------------------------------------------------------------

_SCRIPT_PATH = str(Path(__file__).parent.parent.parent.parent.parent / "scripts/oura-sync.py")

_spec = importlib.util.spec_from_file_location("oura_sync", _SCRIPT_PATH)
oura_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oura_sync)

# Convenience aliases
FetchError = oura_sync.FetchError
fetch_all = oura_sync.fetch_all
cleanup_old_heartrate = oura_sync.cleanup_old_heartrate
sync_tags = oura_sync.sync_tags


# ---------------------------------------------------------------------------
# Helper: in-memory DB with the full schema
# ---------------------------------------------------------------------------

# health_db is available on sys.path after oura_sync loaded it
import health_db


def _make_conn() -> sqlite3.Connection:
    """Return an in-memory DB with the full v6 schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    health_db.initialize_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 8.3.1 — fetch_all raises FetchError
# ---------------------------------------------------------------------------

class TestFetchAllErrorSignaling(unittest.TestCase):
    """fetch_all must raise FetchError on HTTP errors and network failures."""

    def test_http_500_raises_fetch_error(self):
        """HTTP 500 response → FetchError raised."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("requests.get", return_value=mock_resp):
            with self.assertRaises(FetchError):
                fetch_all("daily_activity", "2026-01-01", "2026-01-07", {})

    def test_connection_error_raises_fetch_error(self):
        """Network ConnectionError → FetchError raised.

        oura-sync catches requests.RequestException, so we raise the requests
        subclass (not the built-in ConnectionError).
        """
        with patch("requests.get", side_effect=requests.exceptions.ConnectionError("refused")):
            with self.assertRaises(FetchError):
                fetch_all("daily_activity", "2026-01-01", "2026-01-07", {})

    def test_successful_response_returns_list(self):
        """HTTP 200 with data → list returned, no exception."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"date": "2026-01-01"}],
            "next_token": None,
        }

        with patch("requests.get", return_value=mock_resp):
            result = fetch_all("daily_activity", "2026-01-01", "2026-01-07", {})

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], "2026-01-01")


# ---------------------------------------------------------------------------
# 8.3.2 — per-chunk last_synced tracking
# ---------------------------------------------------------------------------

class TestPerChunkLastSynced(unittest.TestCase):
    """
    sync_tags advances last_synced only for successfully fetched chunks.

    Uses sync_tags because it has the simplest per-row INSERT path and
    90-day chunks, making it easy to construct a 2-chunk range.
    """

    def setUp(self):
        self.conn = _make_conn()
        # 2-chunk range: 91+ days forces sync_tags (90-day chunks) to split
        self.start = "2026-01-01"
        self.end = "2026-04-30"

    def _set_last_synced(self, value):
        health_db.set_last_synced(self.conn, "oura_tags", value)

    def _get_last_synced(self):
        return health_db.get_last_synced(self.conn, "oura_tags")

    def test_chunk1_succeeds_chunk2_fails_advances_to_chunk1_end(self):
        """
        Chunk 1 succeeds, chunk 2 raises FetchError.
        last_synced is set to chunk 1's end date minus OVERLAP_DAYS.
        """
        # Compute the chunk 1 end date the same way oura_sync does
        chunks = oura_sync.date_chunks(self.start, self.end, days=90)
        self.assertGreaterEqual(len(chunks), 2, "Need at least 2 chunks for this test")
        chunk1_end = chunks[0][1]
        expected_safe_date = (
            date.fromisoformat(chunk1_end) - timedelta(days=oura_sync.OVERLAP_DAYS)
        ).isoformat()

        call_count = [0]

        def fake_fetch(resource, start, end, headers):
            call_count[0] += 1
            if call_count[0] == 1:
                return []   # chunk 1 succeeds, no rows
            raise FetchError("simulated failure on chunk 2")

        self._set_last_synced("2025-01-01")  # old value — should be overwritten

        with patch.object(oura_sync, "fetch_all", side_effect=fake_fetch):
            sync_tags(self.conn, {}, self.start, self.end)

        result = self._get_last_synced()
        self.assertEqual(result, expected_safe_date,
                         f"Expected last_synced={expected_safe_date!r}, got {result!r}")

    def test_all_chunks_fail_does_not_advance_last_synced(self):
        """
        When every chunk raises FetchError, set_last_synced must not be called.
        last_synced stays at its previous value (or None).
        """
        # No prior value — last_synced should remain absent
        with patch.object(oura_sync, "fetch_all", side_effect=FetchError("always fails")):
            sync_tags(self.conn, {}, self.start, self.end)

        result = self._get_last_synced()
        self.assertIsNone(result,
                          "last_synced must not be written when all chunks fail")


# ---------------------------------------------------------------------------
# 8.3.3 — cleanup_old_heartrate retention
# ---------------------------------------------------------------------------

class TestCleanupOldHeartrate(unittest.TestCase):
    """
    cleanup_old_heartrate deletes rows older than _HEARTRATE_RETENTION_DAYS (90)
    and keeps recent rows.
    """

    def setUp(self):
        self.conn = _make_conn()

    def _insert_hr(self, timestamp: str, bpm: int = 65):
        """Insert a single oura_heartrate row."""
        self.conn.execute(
            "INSERT OR REPLACE INTO oura_heartrate (timestamp, bpm, source) VALUES (?, ?, ?)",
            (timestamp, bpm, "test"),
        )
        self.conn.commit()

    def _count_rows(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM oura_heartrate"
        ).fetchone()[0]

    def _timestamps_present(self) -> set:
        rows = self.conn.execute("SELECT timestamp FROM oura_heartrate").fetchall()
        return {row[0] for row in rows}

    def test_old_rows_deleted_recent_rows_survive(self):
        """Rows > 90 days old are deleted; rows <= 90 days old survive."""
        today = date.today()
        old_ts = (today - timedelta(days=100)).isoformat() + "T00:00:00+00:00"
        recent_ts = (today - timedelta(days=30)).isoformat() + "T00:00:00+00:00"

        self._insert_hr(old_ts, bpm=60)
        self._insert_hr(recent_ts, bpm=70)

        self.assertEqual(self._count_rows(), 2)

        cleanup_old_heartrate(self.conn)

        remaining = self._timestamps_present()
        self.assertNotIn(old_ts, remaining, "100-day-old row should have been deleted")
        self.assertIn(recent_ts, remaining, "30-day-old row should survive")
        self.assertEqual(self._count_rows(), 1)

    def test_row_at_boundary_90_days_survives(self):
        """A row exactly 90 days old is at the cutoff boundary and must survive."""
        today = date.today()
        boundary_ts = (today - timedelta(days=90)).isoformat() + "T12:00:00+00:00"
        self._insert_hr(boundary_ts, bpm=72)

        cleanup_old_heartrate(self.conn)

        self.assertEqual(self._count_rows(), 1,
                         "Row at exactly 90 days should survive (cutoff is strictly <)")

    def test_no_rows_no_error(self):
        """cleanup_old_heartrate on an empty table must not raise."""
        try:
            cleanup_old_heartrate(self.conn)
        except Exception as exc:
            self.fail(f"cleanup_old_heartrate raised on empty table: {exc}")

    def test_all_old_rows_deleted(self):
        """When all rows are older than 90 days, the table is emptied."""
        today = date.today()
        for days_ago in [100, 120, 180]:
            ts = (today - timedelta(days=days_ago)).isoformat() + "T00:00:00+00:00"
            self._insert_hr(ts, bpm=60 + days_ago)

        cleanup_old_heartrate(self.conn)

        self.assertEqual(self._count_rows(), 0, "All old rows should be deleted")


if __name__ == "__main__":
    unittest.main()
