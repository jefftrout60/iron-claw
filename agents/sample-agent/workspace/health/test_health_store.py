#!/usr/bin/env python3
"""
Tests for health_store.append_entry — enrichment_status, topics_text,
and FTS5 indexing of topics_text.

All tests use an in-memory SQLite DB so they never touch health.db on disk.
health_store.health_db.get_connection is monkey-patched to return the
in-memory connection, and health_store.extract_topics is mocked to control
what topic tags are "returned" from the OpenAI call.
"""

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# health/ is this file's own directory — add it for health_db import inside health_store
_HEALTH_DIR = Path(__file__).parent
sys.path.insert(0, str(_HEALTH_DIR))

# health_store lives in skills/podcast-summary/scripts/
_STORE_DIR = _HEALTH_DIR.parent / "skills" / "podcast-summary" / "scripts"
sys.path.insert(0, str(_STORE_DIR))

import health_db
import health_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _UnclosableConnection:
    """
    Thin wrapper around a sqlite3.Connection that silences close() calls.

    append_entry calls conn.close() before returning, which would destroy our
    in-memory DB before the test can query it.  Wrapping the connection makes
    close() a no-op while forwarding every other attribute to the real connection.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self):
        pass  # intentional no-op

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _make_conn() -> sqlite3.Connection:
    """Return an in-memory DB with the full v6 schema initialised."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    health_db.initialize_schema(conn)
    return conn


_MINIMAL_ENTRY = {
    "source": "test",
    "show": "Test Podcast",
    "episode_title": "Episode 42",
    "date": "2026-04-30",
    "summary": "General wellness discussion about diet and exercise.",
}


# ---------------------------------------------------------------------------
# 8.2.1 — enrichment_status and topics_text written by append_entry
# ---------------------------------------------------------------------------

class TestAppendEntryEnrichmentStatus(unittest.TestCase):
    """append_entry must write enrichment_status and topics_text to the DB row."""

    def _run_append(self, conn, mock_topics):
        """
        Call append_entry with extract_topics mocked to return mock_topics
        and get_connection returning our in-memory conn wrapped so close() is
        a no-op (append_entry calls close() before returning, which would
        destroy the in-memory DB before the test can query it).
        """
        # Patch extract_topics in health_store's namespace so the call inside
        # append_entry sees the mock, not the real OpenAI function.
        # Patch get_connection on the health_db module object that health_store
        # already imported, so append_entry's `health_db.get_connection()` call
        # returns our in-memory connection instead of opening health.db.
        unclosable = _UnclosableConnection(conn)
        with patch.object(health_store, "extract_topics", return_value=mock_topics), \
             patch.object(health_store.health_db, "get_connection", return_value=unclosable):
            result = health_store.append_entry(_MINIMAL_ENTRY.copy(), api_key="fake", model="gpt-4o-mini")
        return result

    def test_enrichment_status_done_when_topics_returned(self):
        """extract_topics returns ['apob', 'ldl'] → enrichment_status = 'done'."""
        conn = _make_conn()
        self._run_append(conn, ["apob", "ldl"])

        row = conn.execute(
            "SELECT enrichment_status FROM health_knowledge WHERE episode_title = 'Episode 42'"
        ).fetchone()
        self.assertIsNotNone(row, "Row should exist after append_entry")
        self.assertEqual(row["enrichment_status"], "done")

    def test_topics_text_populated_when_topics_returned(self):
        """extract_topics returns ['apob', 'ldl'] → topics_text = 'apob ldl'."""
        conn = _make_conn()
        self._run_append(conn, ["apob", "ldl"])

        row = conn.execute(
            "SELECT topics_text FROM health_knowledge WHERE episode_title = 'Episode 42'"
        ).fetchone()
        self.assertIsNotNone(row, "Row should exist after append_entry")
        self.assertEqual(row["topics_text"], "apob ldl")

    def test_enrichment_status_failed_when_no_topics(self):
        """extract_topics returns [] → enrichment_status = 'failed'."""
        conn = _make_conn()
        self._run_append(conn, [])

        row = conn.execute(
            "SELECT enrichment_status FROM health_knowledge WHERE episode_title = 'Episode 42'"
        ).fetchone()
        self.assertIsNotNone(row, "Row should exist after append_entry")
        self.assertEqual(row["enrichment_status"], "failed")

    def test_topics_text_empty_when_no_topics(self):
        """extract_topics returns [] → topics_text = ''."""
        conn = _make_conn()
        self._run_append(conn, [])

        row = conn.execute(
            "SELECT topics_text FROM health_knowledge WHERE episode_title = 'Episode 42'"
        ).fetchone()
        self.assertIsNotNone(row, "Row should exist after append_entry")
        self.assertEqual(row["topics_text"], "")


# ---------------------------------------------------------------------------
# 8.2.2 — FTS topics_text indexing via the INSERT trigger
# ---------------------------------------------------------------------------

class TestFtsTopicsText(unittest.TestCase):
    """
    The hk_ai trigger must index topics_text so FTS MATCH queries can find
    terms that appear only in topics_text (not in episode_title or summary).
    """

    def _insert_knowledge_row(self, conn, episode_title, summary, topics_text):
        """Insert a health_knowledge row directly (bypasses health_store)."""
        conn.execute(
            """INSERT INTO health_knowledge
                 (id, show, episode_title, date, source, summary, topics_text)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"test-{episode_title}",
                "Test Show",
                episode_title,
                "2026-04-30",
                "test",
                summary,
                topics_text,
            ),
        )
        conn.commit()

    def test_fts_matches_term_in_topics_text(self):
        """A term present in topics_text is found via FTS MATCH."""
        conn = _make_conn()
        self._insert_knowledge_row(
            conn,
            episode_title="Episode 42",
            summary="General wellness discussion",
            topics_text="apob cardiovascular",
        )

        hits = conn.execute(
            "SELECT rowid FROM health_knowledge_fts WHERE health_knowledge_fts MATCH '\"apob\"'"
        ).fetchall()
        self.assertEqual(len(hits), 1, "FTS should find the row by its topics_text term 'apob'")

    def test_fts_no_match_for_absent_term(self):
        """A term absent from all indexed columns returns no rows."""
        conn = _make_conn()
        self._insert_knowledge_row(
            conn,
            episode_title="Episode 42",
            summary="General wellness discussion",
            topics_text="apob cardiovascular",
        )

        hits = conn.execute(
            "SELECT rowid FROM health_knowledge_fts WHERE health_knowledge_fts MATCH '\"xenomorphology\"'"
        ).fetchall()
        self.assertEqual(len(hits), 0, "FTS should return no rows for a term not in any column")


if __name__ == "__main__":
    unittest.main()
