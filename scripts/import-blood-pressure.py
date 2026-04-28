#!/usr/bin/env python3
"""
Omron blood pressure CSV importer: loads readings into health.db blood_pressure table.

CSV format expected (Omron Connect export):
  Date,Time,Systolic (mmHg),Diastolic (mmHg),Pulse (bpm),Notes
  "Apr 28, 2026",11:25,143,84,59,

Usage:
  python3.13 scripts/import-blood-pressure.py --file report.csv --dry-run
  python3.13 scripts/import-blood-pressure.py --file report.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

# health_db lives in workspace/health/
_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
sys.path.insert(0, str(_HEALTH_DIR))
import health_db


def parse_rows(filepath: Path) -> list[dict]:
    """Parse Omron CSV and return list of normalized row dicts. Bad rows skipped."""
    rows = []
    with open(filepath, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, start=2):  # start=2 accounts for header line
            try:
                dt = datetime.strptime(
                    f"{row['Date']} {row['Time']}", "%b %d, %Y %H:%M"
                )
            except (KeyError, ValueError) as e:
                print(
                    f"  [warn] Skipping line {i}: cannot parse date/time "
                    f"({row.get('Date', '?')} {row.get('Time', '?')}): {e}",
                    file=sys.stderr,
                )
                continue

            rows.append({
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M"),
                "systolic": int(row["Systolic (mmHg)"]),
                "diastolic": int(row["Diastolic (mmHg)"]),
                "pulse": int(row["Pulse (bpm)"]) if row["Pulse (bpm)"].strip() else None,
                "notes": row["Notes"].strip() or None,
            })
    return rows


def run(filepath: Path, dry_run: bool) -> None:
    rows = parse_rows(filepath)

    if not rows:
        print("No parseable rows found.", file=sys.stderr)
        sys.exit(1)

    dates = sorted(r["date"] for r in rows)
    print(f"Parsed {len(rows)} rows  ({dates[0]} to {dates[-1]})")

    if dry_run:
        print("\n(dry-run) No data written. Remove --dry-run to import.")
        return

    conn = health_db.get_connection()
    inserted = 0
    skipped = 0

    for row in rows:
        cursor = conn.execute(
            """
            INSERT INTO blood_pressure (date, time, systolic, diastolic, pulse, source, notes)
            VALUES (?, ?, ?, ?, ?, 'omron_csv', ?)
            ON CONFLICT(date, time) DO UPDATE SET
                systolic  = excluded.systolic,
                diastolic = excluded.diastolic,
                pulse     = excluded.pulse,
                notes     = excluded.notes
            WHERE blood_pressure.systolic  IS NOT excluded.systolic
               OR blood_pressure.diastolic IS NOT excluded.diastolic
               OR blood_pressure.pulse     IS NOT excluded.pulse
            """,
            (row["date"], row["time"], row["systolic"], row["diastolic"],
             row["pulse"], row["notes"]),
        )
        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()
    print(f"Imported {inserted}, skipped {skipped} (already present or unchanged)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Omron blood pressure CSV data into health.db"
    )
    parser.add_argument("--file", required=True, help="Path to the Omron CSV file")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview import without writing to DB"
    )
    args = parser.parse_args()

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    run(filepath, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
