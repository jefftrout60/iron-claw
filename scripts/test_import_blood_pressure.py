#!/usr/bin/env python3
"""
Integration tests for scripts/import-blood-pressure.py.

Runs the script via subprocess to test the full CLI surface.  Tests that
insert real rows use dates >= 2099-01-01 so they are safe to clean up
without touching production data.
"""

import csv
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

# --- project paths ----------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "import-blood-pressure.py"
_HEALTH_DIR = _REPO_ROOT / "agents" / "sample-agent" / "workspace" / "health"
_REAL_CSV = _REPO_ROOT / "Report (January 01, 2026 – April 28, 2026).csv"

sys.path.insert(0, str(_HEALTH_DIR))
import health_db  # noqa: E402  (must be after sys.path manipulation)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run the import script with the given CLI args; capture stdout + stderr."""
    return subprocess.run(
        [sys.executable, str(_SCRIPT)] + args,
        capture_output=True,
        text=True,
    )


def _bp_count(where_clause: str = "", params: tuple = ()) -> int:
    """Return count of blood_pressure rows matching an optional WHERE clause."""
    conn = health_db.get_connection()
    sql = "SELECT COUNT(*) FROM blood_pressure"
    if where_clause:
        sql += f" WHERE {where_clause}"
    n = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return n


def _cleanup_test_rows() -> None:
    """Remove blood_pressure rows inserted by these tests (2099-* dates and 2026 omron_csv rows)."""
    conn = health_db.get_connection()
    conn.execute("DELETE FROM blood_pressure WHERE date >= '2099-01-01'")
    conn.execute(
        "DELETE FROM blood_pressure WHERE date >= '2026-01-01' AND date <= '2026-12-31' AND source = 'omron_csv'"
    )
    conn.commit()
    conn.close()


def _make_temp_csv(rows: list[dict]) -> Path:
    """
    Write a minimal Omron-format CSV to a temporary file and return its Path.
    Caller is responsible for unlinking it after use.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline=""
    )
    fieldnames = ["Date", "Time", "Systolic (mmHg)", "Diastolic (mmHg)", "Pulse (bpm)", "Notes"]
    writer = csv.DictWriter(tmp, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestImportBloodPressure(unittest.TestCase):

    def setUp(self) -> None:
        # Belt-and-suspenders: remove any leftover test rows before each test.
        _cleanup_test_rows()

    def tearDown(self) -> None:
        # Always clean up so a failing test does not pollute the next one.
        _cleanup_test_rows()

    # -----------------------------------------------------------------------
    # Test 1 — 28 rows from real Omron CSV are imported
    # -----------------------------------------------------------------------

    def test_import_28_rows_from_real_csv(self) -> None:
        """Running against the real CSV inserts 28 rows and exits 0."""
        if not _REAL_CSV.exists():
            self.skipTest(f"Real CSV not found: {_REAL_CSV}")

        # Start from a clean slate for the 2026 omron rows so we can measure
        # exactly how many new rows the script inserts.
        conn = health_db.get_connection()
        conn.execute(
            "DELETE FROM blood_pressure WHERE date >= '2026-01-01' AND date <= '2026-12-31' AND source = 'omron_csv'"
        )
        conn.commit()
        conn.close()

        result = _run(["--file", str(_REAL_CSV)])

        print("\n[test_import_28_rows] stdout:", result.stdout.strip())
        print("[test_import_28_rows] stderr:", result.stderr.strip())

        self.assertEqual(result.returncode, 0, msg=f"Non-zero exit: {result.stderr}")
        self.assertIn("Imported 28", result.stdout)
        self.assertIn("skipped 0", result.stdout)

        # Verify all 28 rows are now in the DB.
        n = _bp_count(
            "date >= '2026-01-01' AND date <= '2026-12-31' AND source = 'omron_csv'"
        )
        self.assertEqual(n, 28, msg=f"Expected 28 omron_csv rows in DB, found {n}")

    # -----------------------------------------------------------------------
    # Test 2 — Second import deduplicates all 28 rows
    # -----------------------------------------------------------------------

    def test_second_import_skips_all_rows(self) -> None:
        """Importing the same CSV twice skips all rows on the second run."""
        if not _REAL_CSV.exists():
            self.skipTest(f"Real CSV not found: {_REAL_CSV}")

        # Ensure 2026 omron rows are absent before the first run so we can
        # reliably observe "Imported 28" on the first pass.
        conn = health_db.get_connection()
        conn.execute(
            "DELETE FROM blood_pressure WHERE date >= '2026-01-01' AND date <= '2026-12-31' AND source = 'omron_csv'"
        )
        conn.commit()
        conn.close()

        # First run — should import all 28
        result1 = _run(["--file", str(_REAL_CSV)])
        print("\n[test_dedup] first run stdout:", result1.stdout.strip())
        self.assertEqual(result1.returncode, 0, msg=f"First run failed: {result1.stderr}")
        self.assertIn("Imported 28", result1.stdout)

        # Second run — same file, all rows already present and unchanged
        result2 = _run(["--file", str(_REAL_CSV)])
        print("[test_dedup] second run stdout:", result2.stdout.strip())

        self.assertEqual(result2.returncode, 0, msg=f"Second run failed: {result2.stderr}")
        self.assertIn("Imported 0", result2.stdout)
        self.assertIn("skipped 28", result2.stdout)

    # -----------------------------------------------------------------------
    # Test 3 — Dry-run prints preview and writes no rows
    # -----------------------------------------------------------------------

    def test_dry_run_writes_no_rows(self) -> None:
        """--dry-run prints row count and date range; DB is unchanged."""
        if not _REAL_CSV.exists():
            self.skipTest(f"Real CSV not found: {_REAL_CSV}")

        before = _bp_count("date >= '2026-01-01' AND date <= '2026-12-31'")
        result = _run(["--file", str(_REAL_CSV), "--dry-run"])

        print("\n[test_dry_run] stdout:", result.stdout.strip())
        print("[test_dry_run] stderr:", result.stderr.strip())

        self.assertEqual(result.returncode, 0, msg=f"Non-zero exit: {result.stderr}")

        # stdout must mention row count
        self.assertIn("28", result.stdout, msg="Expected '28' in dry-run output")
        # stdout must mention the date-range boundary years
        self.assertIn("2026", result.stdout, msg="Expected date range in dry-run output")
        # dry-run message present
        self.assertIn("dry-run", result.stdout.lower(), msg="Expected dry-run notice in output")

        after = _bp_count("date >= '2026-01-01' AND date <= '2026-12-31'")
        self.assertEqual(
            before, after,
            msg=f"Dry-run must not write rows (before={before}, after={after})"
        )

    # -----------------------------------------------------------------------
    # Test 4 — Malformed row is skipped gracefully; valid row is inserted
    # -----------------------------------------------------------------------

    def test_malformed_row_skipped_gracefully(self) -> None:
        """A CSV with one valid row and one bad date skips the bad row and inserts 1."""
        tmp_csv = _make_temp_csv([
            # valid row with a far-future date that won't collide with real data
            {
                "Date": "Jan 15, 2099",
                "Time": "09:00",
                "Systolic (mmHg)": "120",
                "Diastolic (mmHg)": "80",
                "Pulse (bpm)": "60",
                "Notes": "",
            },
            # bad date — parser will skip this row
            {
                "Date": "not-a-date",
                "Time": "09:00",
                "Systolic (mmHg)": "130",
                "Diastolic (mmHg)": "85",
                "Pulse (bpm)": "65",
                "Notes": "",
            },
        ])

        try:
            result = _run(["--file", str(tmp_csv)])
            print("\n[test_malformed] stdout:", result.stdout.strip())
            print("[test_malformed] stderr:", result.stderr.strip())

            self.assertEqual(result.returncode, 0, msg=f"Non-zero exit: {result.stderr}")

            # The bad row must emit a warning to stderr
            self.assertIn("warn", result.stderr.lower(), msg="Expected warning about bad row in stderr")

            # Exactly 1 row inserted (the valid 2099 row)
            n = _bp_count("date = '2099-01-15'")
            print(f"[test_malformed] rows with date=2099-01-15: {n}")
            self.assertEqual(n, 1, msg=f"Expected 1 inserted row for 2099-01-15, got {n}")

        finally:
            tmp_csv.unlink(missing_ok=True)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
