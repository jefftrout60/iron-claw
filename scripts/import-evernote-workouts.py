#!/usr/bin/env python3
"""
Evernote ENEX importer for workout training plans.

Parses an ENEX export file (File → Export Notes → ENEX format in Evernote)
and extracts exercise detail from notes titled "Week \d+ Training Plan".

Two-pass parse:
  Pass 1 — outer ENEX XML: extract note title, created date, ENML content.
  Pass 2 — inner ENML: HTMLParser finds tables, extracts Actual exercise rows.

Exercise parsing and DB import are handled in tasks 3.4 and 3.5.

Usage:
  python3 scripts/import-evernote-workouts.py --file workouts.enex --dry-run
  python3 scripts/import-evernote-workouts.py --file workouts.enex
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

# health_db lives in workspace/health/
_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
sys.path.insert(0, str(_HEALTH_DIR))
import health_db

NOTE_TITLE_RE = re.compile(r"Week \d+ Training Plan", re.IGNORECASE)
ENEX_DATE_FORMAT = "%Y%m%dT%H%M%SZ"


# ---------------------------------------------------------------------------
# Pass 1: outer ENEX XML
# ---------------------------------------------------------------------------

def parse_enex(filepath: str | Path):
    """
    Parse ENEX file and yield (title, created_date, content_html) for each
    note whose title matches NOTE_TITLE_RE and was created on or after 2025-01-01.

    DOCTYPE is stripped before parsing to avoid a network fetch of the
    Evernote DTD (xml.etree.ElementTree does not support resolve_entities=False).
    """
    text = Path(filepath).read_text(encoding="utf-8")
    # Strip DOCTYPE declaration — ET will fail trying to resolve it otherwise
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text)

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        print(f"Error: could not parse ENEX file: {e}", file=sys.stderr)
        sys.exit(1)

    for note in root.findall("note"):
        title = (note.findtext("title") or "").strip()
        if not NOTE_TITLE_RE.search(title):
            continue

        created_str = (note.findtext("created") or "").strip()
        try:
            created = datetime.strptime(created_str, ENEX_DATE_FORMAT).date()
        except ValueError:
            continue

        if created < date(2025, 1, 1):
            continue

        content = note.findtext("content") or ""
        if content:
            yield title, created, content


# ---------------------------------------------------------------------------
# Pass 2: inner ENML table extraction
# ---------------------------------------------------------------------------

class TableParser(HTMLParser):
    """Extract rows from the first table in ENML content."""

    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_cell = False
        self.rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "table":
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self._current_row = []
        elif tag in ("td", "th") and self.in_table:
            self.in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.in_cell:
            self._current_row.append("".join(self._current_cell).strip())
            self.in_cell = False
        elif tag == "tr" and self.in_table:
            if self._current_row:
                self.rows.append(self._current_row)
        elif tag == "table":
            self.in_table = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self._current_cell.append(data)


def extract_table_rows(content_html: str) -> list[list[str]]:
    """Extract rows from ENML content. Returns list of row lists."""
    parser = TableParser()
    parser.feed(content_html)
    return parser.rows


# ---------------------------------------------------------------------------
# Task 3.4: Exercise text parser
# ---------------------------------------------------------------------------

_EXERCISE_RE = re.compile(
    r'^(?P<name>.+?)\s+'
    r'(?P<sets>\d+)\s*[×xX]\s*(?P<reps>\d+)'
    r'(?:\s*@?\s*(?P<weight>[\d.]+)\s*(?P<unit>lbs?|kg)?)?',
    re.IGNORECASE
)


def parse_exercise_text(text: str) -> list[dict]:
    """Parse exercise cell text into list of exercise dicts.

    Each non-empty line is treated as one exercise entry.
    Falls back to storing raw text in notes if regex doesn't match.
    """
    results = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        m = _EXERCISE_RE.match(line)
        if m:
            weight = float(m.group("weight")) if m.group("weight") else None
            unit = (m.group("unit") or "lbs").lower().rstrip("s")
            if unit == "kg" and weight is not None:
                weight = round(weight * 2.20462, 2)
            results.append({
                "exercise_name": m.group("name").strip(),
                "set_number": i,
                "reps": int(m.group("reps")),
                "weight_lbs": weight,
                "notes": None,
            })
        else:
            results.append({
                "exercise_name": line,
                "set_number": i,
                "reps": None,
                "weight_lbs": None,
                "notes": line,
            })
    return results


# ---------------------------------------------------------------------------
# Task 3.5: Week date derivation helper
# ---------------------------------------------------------------------------

def _week_monday(title: str, created: date) -> date | None:
    """Derive the Monday of the week referenced in a note title.

    Week numbers > 52 are likely sequential IDs (e.g. "Week 1826"), not ISO
    week numbers — fall back to the Monday of the created_date's own week.
    """
    m = re.search(r'Week (\d+)', title, re.IGNORECASE)
    if not m:
        return None
    week_num = int(m.group(1))
    # Week numbers > 52 are sequential IDs, not ISO weeks
    if week_num > 52:
        return created - timedelta(days=created.weekday())
    try:
        return date.fromisocalendar(created.year, week_num, 1)
    except ValueError:
        return created - timedelta(days=created.weekday())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    arg_parser = argparse.ArgumentParser(
        description="Import Evernote workout training plans into health.db"
    )
    arg_parser.add_argument("--file", required=True, help="Path to .enex export file")
    arg_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print counts without writing to DB",
    )
    args = arg_parser.parse_args()

    enex_path = Path(args.file)
    if not enex_path.exists():
        print(f"Error: file not found: {enex_path}", file=sys.stderr)
        sys.exit(1)

    notes = list(parse_enex(enex_path))
    print(f"Found {len(notes)} matching notes")

    all_rows: list[tuple[str, date, list[list[str]]]] = []
    for title, created, content in notes:
        rows = extract_table_rows(content)
        all_rows.append((title, created, rows))
        if args.dry_run:
            print(f"  {title} ({created}): {len(rows)} rows")

    if args.dry_run:
        print("Dry run — no DB writes")
        return

    # Task 3.5: link exercises to workouts and write to DB
    day_offsets = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    }

    conn = health_db.get_connection()
    total_exercises = 0

    for title, created, rows in all_rows:
        week_start = _week_monday(title, created)
        if not week_start:
            log.warning("Could not derive week date from: %s", title)
            continue

        for row in rows:
            if len(row) < 3:
                continue
            day_raw = row[0].strip().lower()
            actual_text = row[2].strip() if len(row) > 2 else ""
            if not actual_text or not day_raw:
                continue

            offset = day_offsets.get(day_raw)
            if offset is None:
                continue  # header row or unrecognized day name

            workout_date = (week_start + timedelta(days=offset)).isoformat()

            # Find or create a workouts row to link exercises against
            workout_id = None
            row_match = conn.execute(
                """SELECT id FROM workouts
                   WHERE date = ? AND workout_type LIKE '%Strength%'
                   LIMIT 1""",
                (workout_date,),
            ).fetchone()
            if row_match:
                workout_id = row_match[0]
            else:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO workouts (date, workout_type, source)
                       VALUES (?, 'FunctionalStrengthTraining', 'evernote')""",
                    (workout_date,),
                )
                if cursor.lastrowid:
                    workout_id = cursor.lastrowid

            # Delete existing exercises for this date before re-import (idempotent)
            conn.execute(
                "DELETE FROM workout_exercises WHERE workout_date = ?",
                (workout_date,),
            )

            exercises = parse_exercise_text(actual_text)
            for ex in exercises:
                conn.execute(
                    """INSERT INTO workout_exercises
                         (workout_id, workout_date, exercise_name, set_number, reps, weight_lbs, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (workout_id, workout_date, ex["exercise_name"],
                     ex["set_number"], ex["reps"], ex["weight_lbs"], ex["notes"]),
                )
            total_exercises += len(exercises)

        conn.commit()

    conn.close()
    print(f"Imported {total_exercises} exercise records from {len(all_rows)} notes")


if __name__ == "__main__":
    main()
