#!/usr/bin/env python3
"""
One-shot blood lab Excel importer: unpivots transposed format into health.db.

Excel layout expected per sheet:
  Col 0: marker name  (some rows may be merged — forward-filled automatically)
  Col 1: reference range (e.g. "3.5-5.0", "<5.0", ">2.0", or empty)
  Col 2+: date columns (header row contains draw dates)

Usage:
  python3.13 scripts/import-blood-labs.py --file /path/to/labs.xlsx --dry-run
  python3.13 scripts/import-blood-labs.py --file /path/to/labs.xlsx
"""

from __future__ import annotations

import argparse
import json as _json
import pathlib as _pathlib
import re
import sys
from pathlib import Path

# health_db lives in workspace/health/
_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
sys.path.insert(0, str(_HEALTH_DIR))
import health_db


# ---------------------------------------------------------------------------
# Task 4.2 — Alias lookup for marker names
# ---------------------------------------------------------------------------

def _load_alias_map():
    path = _pathlib.Path(__file__).parent / "markers_canonical.json"
    try:
        data = _json.load(open(path))
        return {alias: canon for canon, aliases in data["aliases"].items() for alias in aliases}
    except (FileNotFoundError, KeyError):
        print("WARNING: markers_canonical.json not found or malformed; proceeding without alias normalization",
              file=sys.stderr)
        return {}

_ALIAS_TO_CANONICAL = _load_alias_map()


def _normalize_marker(name: str) -> str:
    """Return the canonical marker name, or the original name if no alias matches."""
    return _ALIAS_TO_CANONICAL.get(name, name)


# ---------------------------------------------------------------------------
# Task 3.2 — Reference-range flagging
# ---------------------------------------------------------------------------

_BORDERLINE_PCT = 0.10


def _compute_flag(value, ref_low, ref_high):
    if ref_low is None and ref_high is None:
        return None
    if ref_low is not None and value < ref_low:
        if ref_low == 0:
            return 'out'
        return 'borderline' if (ref_low - value) / ref_low <= _BORDERLINE_PCT else 'out'
    if ref_high is not None and value > ref_high:
        if ref_high == 0:
            return 'out'
        return 'borderline' if (value - ref_high) / ref_high <= _BORDERLINE_PCT else 'out'
    return 'in'


def parse_reference_range(ref_str) -> tuple[float | None, float | None]:
    """Parse a reference range string into (low, high) floats."""
    import pandas as pd
    if ref_str is None or (hasattr(pd, "isna") and pd.isna(ref_str)):
        return None, None
    s = str(ref_str).strip()
    if not s:
        return None, None
    # Range: "3.5-5.0" or "3.5 - 5.0" or "3.5–5.0" (en-dash)
    m = re.match(r"([\d.]+)\s*[-–]\s*([\d.]+)", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    # Less than: "<5.0"
    m = re.match(r"[<]\s*([\d.]+)", s)
    if m:
        return None, float(m.group(1))
    # Greater than: ">2.0"
    m = re.match(r"[>]\s*([\d.]+)", s)
    if m:
        return float(m.group(1)), None
    return None, None


def unpivot_sheet(filepath: str, sheet_name: str):
    """
    Read one sheet and return a long-format DataFrame:
      date, marker_name, value, reference_low, reference_high, source_sheet
    Returns None if the sheet cannot be parsed.
    """
    import pandas as pd

    try:
        raw = pd.read_excel(filepath, sheet_name=sheet_name, header=0,
                            engine="openpyxl", dtype=str)
    except Exception as e:
        print(f"  [skip] Cannot read sheet '{sheet_name}': {e}", file=sys.stderr)
        return None

    if raw.shape[1] < 3:
        print(f"  [skip] Sheet '{sheet_name}' has fewer than 3 columns", file=sys.stderr)
        return None

    # Rename first two columns regardless of header text
    cols = list(raw.columns)
    cols[0] = "marker_name"
    cols[1] = "reference_range"
    raw.columns = cols

    # Drop completely empty rows
    raw = raw.dropna(subset=["marker_name"])
    raw["marker_name"] = raw["marker_name"].str.strip()
    raw = raw[raw["marker_name"] != ""]

    # Forward-fill merged marker cells
    raw["marker_name"] = raw["marker_name"].ffill()

    # Parse reference ranges
    parsed = raw["reference_range"].apply(parse_reference_range)
    raw["reference_low"] = parsed.apply(lambda x: x[0])
    raw["reference_high"] = parsed.apply(lambda x: x[1])

    # Date columns are everything after the first two
    id_cols = ["marker_name", "reference_range", "reference_low", "reference_high"]
    date_cols = [c for c in raw.columns if c not in id_cols]

    if not date_cols:
        print(f"  [skip] Sheet '{sheet_name}' has no date columns", file=sys.stderr)
        return None

    # Melt: wide → long
    long = raw.melt(
        id_vars=["marker_name", "reference_low", "reference_high"],
        value_vars=date_cols,
        var_name="date",
        value_name="value",
    )

    # Coerce value and date
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long["date"] = pd.to_datetime(long["date"], errors="coerce")

    # Drop rows where either is missing
    long = long.dropna(subset=["value", "date"])

    if long.empty:
        print(f"  [skip] Sheet '{sheet_name}' produced no parseable rows", file=sys.stderr)
        return None

    long["date"] = long["date"].dt.strftime("%Y-%m-%d")
    long["source_sheet"] = sheet_name

    return long[["date", "marker_name", "value", "reference_low", "reference_high", "source_sheet"]]


def run(filepath: str, dry_run: bool) -> None:
    import pandas as pd

    wb = pd.ExcelFile(filepath, engine="openpyxl")
    all_frames = []

    for sheet in wb.sheet_names:
        df = unpivot_sheet(filepath, sheet)
        if df is not None:
            all_frames.append(df)
            if dry_run:
                markers = sorted(df["marker_name"].unique())
                date_min = df["date"].min()
                date_max = df["date"].max()
                print(f"\nSheet: {sheet}")
                print(f"  Markers: {len(markers)}")
                print(f"  Date range: {date_min} to {date_max}")
                sample = ", ".join(markers[:3])
                if len(markers) > 3:
                    sample += f", ... (+{len(markers)-3} more)"
                print(f"  Sample markers: {sample}")
                print(f"  Rows to import: {len(df)}")

    if not all_frames:
        print("No sheets produced importable data.", file=sys.stderr)
        sys.exit(1)

    combined = pd.concat(all_frames, ignore_index=True)
    total_markers = combined["marker_name"].nunique()
    total_rows = len(combined)

    if dry_run:
        print(f"\nTotal: {len(all_frames)} sheets, {total_markers} unique markers, {total_rows} rows")
        print("\n(dry-run) No data written. Remove --dry-run to import.")
        return

    # Live import
    conn = health_db.get_connection()
    inserted = 0
    skipped = 0

    for _, row in combined.iterrows():
        # Extract unit from marker name if embedded in parentheses at end
        # e.g. "Glucose (mg/dL)" → "mg/dL"; "HbA1c (%)" → "%"
        raw_name = str(row["marker_name"]).strip()
        unit_match = re.search(r'\(([^)]+)\)\s*$', raw_name)
        canonical_unit = unit_match.group(1) if unit_match else None

        # Normalize marker name through alias map (Task 4.2)
        marker_name = _normalize_marker(raw_name)

        # Upsert marker — set canonical_unit if we can parse it
        conn.execute(
            """INSERT INTO lab_markers (name, canonical_unit)
               VALUES (?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 canonical_unit = COALESCE(excluded.canonical_unit, lab_markers.canonical_unit)""",
            (marker_name, canonical_unit),
        )
        marker_row = conn.execute(
            "SELECT id FROM lab_markers WHERE name = ?", (marker_name,)
        ).fetchone()
        marker_id = marker_row[0]

        ref_low = row["reference_low"] if row["reference_low"] is not None else None
        ref_high = row["reference_high"] if row["reference_high"] is not None else None
        value = row["value"]
        flag = _compute_flag(value, ref_low, ref_high)

        # Upsert result — preserve imported_at and id on re-import
        cursor = conn.execute(
            """INSERT INTO lab_results
                 (marker_id, date, value, reference_low, reference_high, source_sheet, in_range_flag)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(marker_id, date) DO UPDATE SET
                 value          = excluded.value,
                 reference_low  = excluded.reference_low,
                 reference_high = excluded.reference_high,
                 source_sheet   = excluded.source_sheet,
                 in_range_flag  = excluded.in_range_flag
               WHERE lab_results.value IS NOT excluded.value
                  OR lab_results.reference_low IS NOT excluded.reference_low
                  OR lab_results.reference_high IS NOT excluded.reference_high""",
            (
                marker_id,
                row["date"],
                value,
                ref_low,
                ref_high,
                row["source_sheet"],
                flag,
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()
    print(f"Import complete: {inserted} rows inserted/replaced, {skipped} skipped")
    print(f"Markers in DB: {total_markers} unique")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import blood lab Excel data into health.db")
    parser.add_argument("--file", required=True, help="Path to the Excel (.xlsx) file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview import without writing to DB")
    args = parser.parse_args()

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    run(str(filepath), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
