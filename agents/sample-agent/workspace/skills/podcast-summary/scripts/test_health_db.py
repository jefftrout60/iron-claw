#!/usr/bin/env python3
"""
Behavioral tests for health_db.py.

Run with:
    python3 -m unittest test_health_db -v
from the scripts/ directory.
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import health_db


# ---------------------------------------------------------------------------
# P1 — get_db_path()
# ---------------------------------------------------------------------------

class TestGetDbPath(unittest.TestCase):

    def test_returns_path_ending_in_podcast_vault_health_db(self):
        path = health_db.get_db_path()
        self.assertIsInstance(path, Path)
        self.assertEqual(path.parts[-1], "health.db")
        self.assertEqual(path.parts[-2], "podcast_vault")

    def test_returns_absolute_path(self):
        path = health_db.get_db_path()
        self.assertTrue(path.is_absolute())


# ---------------------------------------------------------------------------
# P1 — get_connection()
# ---------------------------------------------------------------------------

class TestGetConnection(unittest.TestCase):

    def setUp(self):
        fd, self._tmp_path = tempfile.mkstemp(suffix=".db")
        import os
        os.close(fd)
        self._conn = None

    def tearDown(self):
        if self._conn:
            self._conn.close()
        Path(self._tmp_path).unlink(missing_ok=True)
        # Clean up WAL side-files if they were created
        for ext in ("-wal", "-shm"):
            Path(self._tmp_path + ext).unlink(missing_ok=True)

    def _open(self):
        self._conn = health_db.get_connection(db_path=Path(self._tmp_path))
        return self._conn

    def test_returns_sqlite3_connection(self):
        conn = self._open()
        self.assertIsInstance(conn, sqlite3.Connection)

    def test_wal_journal_mode_is_enabled(self):
        conn = self._open()
        row = conn.execute("PRAGMA journal_mode").fetchone()
        # row_factory is sqlite3.Row; access by index or key
        self.assertEqual(row[0], "wal")

    def test_row_factory_is_sqlite_row(self):
        conn = self._open()
        self.assertIs(conn.row_factory, sqlite3.Row)


# ---------------------------------------------------------------------------
# P1 — initialize_schema() idempotency and table existence
# ---------------------------------------------------------------------------

EXPECTED_TABLES = [
    "health_knowledge",
    "lab_markers",
    "lab_results",
    "oura_daily",
    "oura_sleep_sessions",
    "oura_heartrate",
    "sync_state",
]


class TestInitializeSchema(unittest.TestCase):

    def setUp(self):
        fd, self._tmp_path = tempfile.mkstemp(suffix=".db")
        import os
        os.close(fd)
        self._conn = health_db.get_connection(db_path=Path(self._tmp_path))

    def tearDown(self):
        self._conn.close()
        Path(self._tmp_path).unlink(missing_ok=True)
        for ext in ("-wal", "-shm"):
            Path(self._tmp_path + ext).unlink(missing_ok=True)

    def _table_names(self):
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {row[0] for row in rows}

    def test_calling_initialize_schema_twice_does_not_raise(self):
        # First call already happened in get_connection(); call once more.
        try:
            health_db.initialize_schema(self._conn)
        except Exception as exc:
            self.fail(f"initialize_schema raised on second call: {exc}")

    def test_all_seven_expected_tables_exist(self):
        tables = self._table_names()
        for table in EXPECTED_TABLES:
            with self.subTest(table=table):
                self.assertIn(table, tables, f"Table '{table}' missing after initialize_schema()")

    def test_idempotent_tables_still_present_after_second_call(self):
        health_db.initialize_schema(self._conn)
        tables = self._table_names()
        for table in EXPECTED_TABLES:
            with self.subTest(table=table):
                self.assertIn(table, tables)


# ---------------------------------------------------------------------------
# P1 — FTS5 virtual table exists
# ---------------------------------------------------------------------------

class TestFtsVirtualTable(unittest.TestCase):

    def setUp(self):
        fd, self._tmp_path = tempfile.mkstemp(suffix=".db")
        import os
        os.close(fd)
        self._conn = health_db.get_connection(db_path=Path(self._tmp_path))

    def tearDown(self):
        self._conn.close()
        Path(self._tmp_path).unlink(missing_ok=True)
        for ext in ("-wal", "-shm"):
            Path(self._tmp_path + ext).unlink(missing_ok=True)

    def test_health_knowledge_fts_virtual_table_exists(self):
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='health_knowledge_fts'"
        ).fetchall()
        self.assertEqual(len(rows), 1, "health_knowledge_fts virtual table is missing")


# ---------------------------------------------------------------------------
# P1 — INSERT trigger populates FTS index automatically
# ---------------------------------------------------------------------------

class TestFtsTrigger(unittest.TestCase):

    def setUp(self):
        fd, self._tmp_path = tempfile.mkstemp(suffix=".db")
        import os
        os.close(fd)
        self._conn = health_db.get_connection(db_path=Path(self._tmp_path))

    def tearDown(self):
        self._conn.close()
        Path(self._tmp_path).unlink(missing_ok=True)
        for ext in ("-wal", "-shm"):
            Path(self._tmp_path + ext).unlink(missing_ok=True)

    def test_insert_into_health_knowledge_populates_fts(self):
        self._conn.execute(
            """INSERT INTO health_knowledge
               (id, show, episode_title, date, source, summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("test-id-001", "Test Show", "Sleep and Longevity Basics",
             "2025-01-01", "rss", "A deep dive into sleep science and longevity research."),
        )
        self._conn.commit()

        # FTS search should find the inserted row by a word in the summary
        rows = self._conn.execute(
            "SELECT rowid FROM health_knowledge_fts WHERE health_knowledge_fts MATCH 'longevity'"
        ).fetchall()
        self.assertEqual(len(rows), 1, "FTS trigger did not populate health_knowledge_fts on INSERT")

    def test_fts_search_by_episode_title_word(self):
        self._conn.execute(
            """INSERT INTO health_knowledge
               (id, show, episode_title, date, source, summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("test-id-002", "Health Show", "Zone 2 Training Deep Dive",
             "2025-02-01", "rss", "An exploration of aerobic threshold training protocols."),
        )
        self._conn.commit()

        rows = self._conn.execute(
            "SELECT rowid FROM health_knowledge_fts WHERE health_knowledge_fts MATCH 'training'"
        ).fetchall()
        self.assertEqual(len(rows), 1, "FTS search by episode_title word returned no rows")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
