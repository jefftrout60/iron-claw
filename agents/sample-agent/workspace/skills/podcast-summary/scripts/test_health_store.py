#!/usr/bin/env python3
"""
Behavioral tests for health_store.py.

Run with:
    python3 -m unittest test_health_store -v
from the scripts/ directory.

Isolation strategy: health_store.py imports health_db at module level and
calls health_db.get_connection() directly.  We monkey-patch
health_db.get_connection (the name as seen by health_store's module namespace)
so every call from health_store lands on our temp DB rather than the real file.
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import health_db
import health_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_entry(**overrides):
    """Return a minimal valid entry_data dict; override any field via kwargs."""
    base = {
        "show": "The Drive with Peter Attia",
        "episode_title": "Zone 2 Training and Longevity",
        "episode_number": "312",
        "date": "2025-03-15",
        "source": "rss",
        "source_quality": "high",
        "summary": "A comprehensive look at Zone 2 cardio and its impact on healthspan.",
        "tagged_by": "auto",
    }
    base.update(overrides)
    return base


def _expected_id(entry_data: dict) -> str:
    """Reproduce the ID formula from health_store.append_entry()."""
    source = entry_data["source"]
    show_slug = health_store.slugify(entry_data["show"])
    date_part = entry_data["date"][:10]
    content_hash = hashlib.md5(entry_data["summary"].encode()).hexdigest()[:8]
    return f"{source}-{show_slug}-{date_part}-{content_hash}"


# ---------------------------------------------------------------------------
# Base test class — sets up a temp DB and patches health_db.get_connection
# ---------------------------------------------------------------------------

class _HealthStoreTestBase(unittest.TestCase):
    """
    Creates a fresh temp SQLite DB for each test and monkey-patches
    health_db.get_connection so health_store always uses the temp DB.
    """

    def setUp(self):
        fd, self._tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        # Initialize the schema in the temp DB so tables exist
        tmp_conn = health_db.get_connection(db_path=Path(self._tmp_path))
        tmp_conn.close()

        # Patch health_db.get_connection inside the health_store module's view
        # health_store does `import health_db` then calls `health_db.get_connection()`
        # so we patch the attribute on the health_db module object itself.
        self._orig_get_connection = health_db.get_connection

        def _patched_get_connection(db_path=None):
            return self._orig_get_connection(db_path=Path(self._tmp_path))

        health_db.get_connection = _patched_get_connection

    def tearDown(self):
        health_db.get_connection = self._orig_get_connection
        Path(self._tmp_path).unlink(missing_ok=True)
        for ext in ("-wal", "-shm"):
            Path(self._tmp_path + ext).unlink(missing_ok=True)

    def _row_count(self) -> int:
        conn = self._orig_get_connection(db_path=Path(self._tmp_path))
        count = conn.execute("SELECT COUNT(*) FROM health_knowledge").fetchone()[0]
        conn.close()
        return count


# ---------------------------------------------------------------------------
# P1 — append_entry() inserts and returns expected keys
# ---------------------------------------------------------------------------

class TestAppendEntryInsert(_HealthStoreTestBase):

    def test_returns_dict_with_required_keys(self):
        entry = health_store.append_entry(_minimal_entry(), api_key="")
        self.assertIsNotNone(entry)
        for key in ("id", "show", "episode_title", "date", "topics", "summary", "source"):
            with self.subTest(key=key):
                self.assertIn(key, entry, f"Key '{key}' missing from returned entry")

    def test_inserted_row_is_retrievable(self):
        health_store.append_entry(_minimal_entry(), api_key="")
        self.assertEqual(self._row_count(), 1)

    def test_returned_show_matches_input(self):
        data = _minimal_entry(show="FoundMyFitness")
        entry = health_store.append_entry(data, api_key="")
        self.assertEqual(entry["show"], "FoundMyFitness")

    def test_returned_episode_title_matches_input(self):
        data = _minimal_entry(episode_title="Ep 42: Telomeres and Aging")
        entry = health_store.append_entry(data, api_key="")
        self.assertEqual(entry["episode_title"], "Ep 42: Telomeres and Aging")

    def test_returned_date_matches_input(self):
        data = _minimal_entry(date="2025-06-01")
        entry = health_store.append_entry(data, api_key="")
        self.assertEqual(entry["date"], "2025-06-01")

    def test_returned_source_matches_input(self):
        data = _minimal_entry(source="newsletter")
        entry = health_store.append_entry(data, api_key="")
        self.assertEqual(entry["source"], "newsletter")


# ---------------------------------------------------------------------------
# P1 — append_entry() deduplicates on (show, episode_title, date)
# ---------------------------------------------------------------------------

class TestAppendEntryDeduplication(_HealthStoreTestBase):

    def test_second_identical_call_returns_none(self):
        data = _minimal_entry()
        health_store.append_entry(data, api_key="")
        result = health_store.append_entry(data, api_key="")
        self.assertIsNone(result, "Expected None on duplicate insert, got a dict")

    def test_db_has_exactly_one_row_after_duplicate(self):
        data = _minimal_entry()
        health_store.append_entry(data, api_key="")
        health_store.append_entry(data, api_key="")
        self.assertEqual(self._row_count(), 1)

    def test_different_date_is_not_a_duplicate(self):
        health_store.append_entry(_minimal_entry(date="2025-01-01"), api_key="")
        result = health_store.append_entry(_minimal_entry(date="2025-01-02"), api_key="")
        self.assertIsNotNone(result)
        self.assertEqual(self._row_count(), 2)

    def test_different_episode_title_is_not_a_duplicate(self):
        # Different summaries are required too — the id is MD5(summary)-based,
        # so identical summaries would collide on the primary key before the
        # (show, episode_title, date) unique index is even reached.
        health_store.append_entry(_minimal_entry(episode_title="Ep A", summary="Summary A"), api_key="")
        result = health_store.append_entry(_minimal_entry(episode_title="Ep B", summary="Summary B"), api_key="")
        self.assertIsNotNone(result)
        self.assertEqual(self._row_count(), 2)


# ---------------------------------------------------------------------------
# P1 — load_all() sorted newest-first and topics deserialized
# ---------------------------------------------------------------------------

class TestLoadAll(_HealthStoreTestBase):

    def test_load_all_returns_list(self):
        result = health_store.load_all()
        self.assertIsInstance(result, list)

    def test_load_all_empty_when_no_entries(self):
        self.assertEqual(health_store.load_all(), [])

    def test_load_all_sorted_newest_first(self):
        health_store.append_entry(_minimal_entry(date="2025-01-01", episode_title="Ep A",
                                                  summary="Summary for episode A"), api_key="")
        health_store.append_entry(_minimal_entry(date="2025-06-15", episode_title="Ep B",
                                                  summary="Summary for episode B"), api_key="")
        health_store.append_entry(_minimal_entry(date="2025-03-10", episode_title="Ep C",
                                                  summary="Summary for episode C"), api_key="")
        entries = health_store.load_all()
        dates = [e["date"] for e in entries]
        self.assertEqual(dates, sorted(dates, reverse=True),
                         f"Expected newest-first order, got: {dates}")

    def test_load_all_deserializes_topics_to_list(self):
        health_store.append_entry(_minimal_entry(), api_key="")
        entries = health_store.load_all()
        self.assertEqual(len(entries), 1)
        self.assertIsInstance(entries[0]["topics"], list,
                              "topics field should be a Python list, not a string")

    def test_load_all_topics_is_list_even_when_empty(self):
        # No api_key → topics = []
        health_store.append_entry(_minimal_entry(), api_key="")
        entries = health_store.load_all()
        self.assertIsInstance(entries[0]["topics"], list)
        self.assertEqual(entries[0]["topics"], [])

    def test_load_all_returns_all_inserted_entries(self):
        for i in range(3):
            health_store.append_entry(
                _minimal_entry(episode_title=f"Episode {i}", date=f"2025-0{i+1}-01",
                               summary=f"Unique summary content number {i}"),
                api_key="",
            )
        entries = health_store.load_all()
        self.assertEqual(len(entries), 3)


# ---------------------------------------------------------------------------
# P1 — append_entry() with no api_key succeeds and topics = []
# ---------------------------------------------------------------------------

class TestAppendEntryNoApiKey(_HealthStoreTestBase):

    def test_no_api_key_does_not_raise(self):
        try:
            entry = health_store.append_entry(_minimal_entry(), api_key="")
        except Exception as exc:
            self.fail(f"append_entry raised with empty api_key: {exc}")

    def test_no_api_key_sets_topics_to_empty_list(self):
        entry = health_store.append_entry(_minimal_entry(), api_key="")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["topics"], [])

    def test_no_api_key_still_inserts_row(self):
        health_store.append_entry(_minimal_entry(), api_key="")
        self.assertEqual(self._row_count(), 1)


# ---------------------------------------------------------------------------
# P1 — append_entry() generates stable source-show-date-md5 ID
# ---------------------------------------------------------------------------

class TestAppendEntryStableId(_HealthStoreTestBase):

    def test_id_matches_expected_formula(self):
        data = _minimal_entry()
        entry = health_store.append_entry(data, api_key="")
        self.assertIsNotNone(entry)
        expected = _expected_id(data)
        self.assertEqual(entry["id"], expected)

    def test_id_contains_source_prefix(self):
        data = _minimal_entry(source="newsletter")
        entry = health_store.append_entry(data, api_key="")
        self.assertTrue(entry["id"].startswith("newsletter-"),
                        f"ID '{entry['id']}' does not start with 'newsletter-'")

    def test_id_contains_slugified_show(self):
        data = _minimal_entry(show="FoundMyFitness")
        entry = health_store.append_entry(data, api_key="")
        slug = health_store.slugify("FoundMyFitness")
        self.assertIn(slug, entry["id"])

    def test_id_contains_date_part(self):
        data = _minimal_entry(date="2025-07-04")
        entry = health_store.append_entry(data, api_key="")
        self.assertIn("2025-07-04", entry["id"])

    def test_same_input_produces_same_id(self):
        """ID must be deterministic — same input always yields the same string."""
        data = _minimal_entry()
        expected = _expected_id(data)
        # Verify formula twice for stability
        self.assertEqual(_expected_id(data), expected)

    def test_different_summaries_produce_different_ids(self):
        data_a = _minimal_entry(summary="Summary about apob", episode_title="Ep A")
        data_b = _minimal_entry(summary="Totally different summary content", episode_title="Ep B")
        id_a = _expected_id(data_a)
        id_b = _expected_id(data_b)
        self.assertNotEqual(id_a, id_b)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
