#!/usr/bin/env python3
"""
Apple Health XML streaming importer — loads all categories into health.db.

Handles export files from 200MB to 2GB by using iterparse with elem.clear()
to keep memory usage flat regardless of file size.

Categories imported:
  blood_pressure   — HKQuantityTypeIdentifierBloodPressureSystolic/Diastolic
  body_metrics     — HKQuantityTypeIdentifierBodyMass, BodyFatPercentage
  activity_daily   — HKQuantityTypeIdentifierStepCount, TimeInDaylight
  workouts         — <Workout> elements with embedded WorkoutStatistics

Source priority:
  apple_health rows never overwrite withings_api rows in body_metrics
  apple_health rows never overwrite omron_csv or imessage rows in blood_pressure

Usage:
  python3 scripts/import-apple-health.py --dry-run
  python3 scripts/import-apple-health.py
  python3 scripts/import-apple-health.py --file ~/Desktop/export.xml
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# health_db lives in workspace/health/
_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
sys.path.insert(0, str(_HEALTH_DIR))
import health_db

DEFAULT_EXPORT_PATH = Path.home() / "Downloads/apple_health_export/export.xml"

# Apple Health record types we care about
_RECORD_TYPES = {
    "HKQuantityTypeIdentifierBloodPressureSystolic",
    "HKQuantityTypeIdentifierBloodPressureDiastolic",
    "HKQuantityTypeIdentifierBodyMass",
    "HKQuantityTypeIdentifierBodyFatPercentage",
    "HKQuantityTypeIdentifierStepCount",
    "HKQuantityTypeIdentifierTimeInDaylight",
}

_TS_FMT = "%Y-%m-%d %H:%M:%S %z"


def _parse_ts(value: str) -> datetime:
    """Parse Apple Health timestamp string to aware datetime."""
    return datetime.strptime(value, _TS_FMT)


def parse_export(filepath: Path):
    """
    Stream-parse the Apple Health export XML.

    Returns (bp_pairs, body_records, steps_by_date, daylight_by_date, workouts):
      bp_pairs        — dict keyed by startDate string: {"systolic": N, "diastolic": N}
      body_records    — list of dicts: {ts, type, value}
      steps_by_date   — dict keyed by YYYY-MM-DD: total step count
      daylight_by_date — dict keyed by YYYY-MM-DD: total daylight minutes
      workouts        — list of dicts with workout details
    """
    # BP: buffer systolic and diastolic separately; only flush complete pairs
    bp_systolic = {}   # startDate str → float
    bp_diastolic = {}  # startDate str → float

    body_records = []
    steps_by_date = {}
    daylight_by_date = {}
    workouts = []

    for _event, elem in ET.iterparse(filepath, events=("end",)):
        if elem.tag == "Record":
            rtype = elem.get("type")
            if rtype in _RECORD_TYPES:
                ts_str = elem.get("startDate", "")
                raw_value = elem.get("value", "")

                if not ts_str or not raw_value:
                    elem.clear()
                    continue

                try:
                    value = float(raw_value)
                except ValueError:
                    elem.clear()
                    continue

                if rtype == "HKQuantityTypeIdentifierBloodPressureSystolic":
                    bp_systolic[ts_str] = value
                elif rtype == "HKQuantityTypeIdentifierBloodPressureDiastolic":
                    bp_diastolic[ts_str] = value
                elif rtype == "HKQuantityTypeIdentifierBodyMass":
                    body_records.append({"ts": ts_str, "type": "weight_kg", "value": value})
                elif rtype == "HKQuantityTypeIdentifierBodyFatPercentage":
                    body_records.append({"ts": ts_str, "type": "fat_ratio", "value": value})
                elif rtype == "HKQuantityTypeIdentifierStepCount":
                    try:
                        date_str = _parse_ts(ts_str).strftime("%Y-%m-%d")
                    except ValueError:
                        elem.clear()
                        continue
                    steps_by_date[date_str] = steps_by_date.get(date_str, 0) + value
                elif rtype == "HKQuantityTypeIdentifierTimeInDaylight":
                    try:
                        date_str = _parse_ts(ts_str).strftime("%Y-%m-%d")
                    except ValueError:
                        elem.clear()
                        continue
                    daylight_by_date[date_str] = daylight_by_date.get(date_str, 0.0) + value

            elem.clear()

        elif elem.tag == "Workout":
            workout = _parse_workout(elem)
            if workout:
                workouts.append(workout)
            elem.clear()

    # Flush complete BP pairs only
    bp_pairs = {}
    for ts_str in bp_systolic:
        if ts_str in bp_diastolic:
            bp_pairs[ts_str] = {
                "systolic": bp_systolic[ts_str],
                "diastolic": bp_diastolic[ts_str],
            }

    return bp_pairs, body_records, steps_by_date, daylight_by_date, workouts


def _parse_workout(elem) -> dict | None:
    """Extract workout fields from a <Workout> element."""
    workout_type_raw = elem.get("workoutActivityType", "")
    start_date_str = elem.get("startDate", "")
    end_date_str = elem.get("endDate", "")
    duration_str = elem.get("duration", "")
    duration_unit = elem.get("durationUnit", "min")
    calories_str = elem.get("totalEnergyBurned", "")

    if not workout_type_raw or not start_date_str:
        return None

    # Strip the HKWorkoutActivityType prefix
    workout_type = workout_type_raw.replace("HKWorkoutActivityType", "")

    try:
        start_dt = _parse_ts(start_date_str)
    except ValueError:
        return None

    end_dt = None
    if end_date_str:
        try:
            end_dt = _parse_ts(end_date_str)
        except ValueError:
            pass

    duration_min = None
    if duration_str:
        try:
            duration_min = float(duration_str)
            # The spec says durationUnit is already "min", but guard against seconds
            if duration_unit == "s":
                duration_min = duration_min / 60.0
        except ValueError:
            pass

    calories = None
    if calories_str:
        try:
            calories = float(calories_str)
        except ValueError:
            pass

    # Extract heart rate from WorkoutStatistics children
    avg_hr = None
    max_hr = None
    for stat in elem.findall("WorkoutStatistics"):
        if stat.get("type") == "HKQuantityTypeIdentifierHeartRate":
            avg_str = stat.get("average", "")
            max_str = stat.get("maximum", "")
            if avg_str:
                try:
                    avg_hr = int(float(avg_str))
                except ValueError:
                    pass
            if max_str:
                try:
                    max_hr = int(float(max_str))
                except ValueError:
                    pass
            break

    return {
        "workout_type": workout_type,
        "date": start_dt.strftime("%Y-%m-%d"),
        "start_time": start_dt.strftime("%H:%M"),
        "end_time": end_dt.strftime("%H:%M") if end_dt else None,
        "duration_min": duration_min,
        "calories": calories,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
    }


def import_bp(conn, bp_pairs: dict) -> None:
    """
    Upsert Apple Health BP pairs into blood_pressure table.

    Only overwrites existing rows that are also apple_health — never clobbers
    omron_csv or imessage rows at the same (date, time).
    """
    inserted = 0
    skipped = 0

    for ts_str, pair in bp_pairs.items():
        try:
            dt = _parse_ts(ts_str)
        except ValueError:
            skipped += 1
            continue

        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")

        cursor = conn.execute(
            """
            INSERT INTO blood_pressure (date, time, systolic, diastolic, source)
            VALUES (?, ?, ?, ?, 'apple_health')
            ON CONFLICT(date, time) DO UPDATE SET
                systolic  = excluded.systolic,
                diastolic = excluded.diastolic,
                source    = excluded.source
            WHERE blood_pressure.source IS 'apple_health'
            """,
            (date_str, time_str, int(pair["systolic"]), int(pair["diastolic"])),
        )
        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    print(f"BP: imported {inserted}, skipped {skipped} (already present from other source)")


def import_body_metrics(conn, body_records: list) -> None:
    """
    Upsert Apple Health body composition records into body_metrics table.

    Weight (kg) is converted to lbs. Body fat percentage decimal (0.155) is
    converted to percent (15.5). Each record is its own row keyed by timestamp.

    Withings API rows take precedence — apple_health rows never overwrite
    withings_api rows at the same (date, time).
    """
    inserted = 0
    skipped = 0

    for rec in body_records:
        try:
            dt = _parse_ts(rec["ts"])
        except ValueError:
            skipped += 1
            continue

        date_str = dt.strftime("%Y-%m-%d")
        time_str = dt.strftime("%H:%M")

        if rec["type"] == "weight_kg":
            weight_lbs = rec["value"] * 2.20462
            cursor = conn.execute(
                """
                INSERT INTO body_metrics (date, time, weight_lbs, source)
                VALUES (?, ?, ?, 'apple_health')
                ON CONFLICT(date, time) DO UPDATE SET
                    weight_lbs = excluded.weight_lbs,
                    source     = excluded.source
                WHERE body_metrics.source IS 'apple_health'
                """,
                (date_str, time_str, round(weight_lbs, 2)),
            )
        elif rec["type"] == "fat_ratio":
            fat_pct = rec["value"] * 100.0
            cursor = conn.execute(
                """
                INSERT INTO body_metrics (date, time, fat_ratio_pct, source)
                VALUES (?, ?, ?, 'apple_health')
                ON CONFLICT(date, time) DO UPDATE SET
                    fat_ratio_pct = excluded.fat_ratio_pct,
                    source        = excluded.source
                WHERE body_metrics.source IS 'apple_health'
                """,
                (date_str, time_str, round(fat_pct, 2)),
            )
        else:
            skipped += 1
            continue

        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    print(f"Body metrics: imported {inserted}, skipped {skipped} (already present from withings_api)")


def import_activity(conn, steps_by_date: dict, daylight_by_date: dict) -> None:
    """
    Upsert daily step and daylight totals into activity_daily table.

    Apple Health can retroactively correct step counts, so re-import overwrites
    the aggregated total for each date.
    """
    all_dates = set(steps_by_date) | set(daylight_by_date)

    for date_str in sorted(all_dates):
        steps = int(steps_by_date[date_str]) if date_str in steps_by_date else None
        daylight = daylight_by_date.get(date_str)

        conn.execute(
            """
            INSERT INTO activity_daily (date, steps, daylight_minutes, source)
            VALUES (?, ?, ?, 'apple_health')
            ON CONFLICT(date) DO UPDATE SET
                steps            = excluded.steps,
                daylight_minutes = excluded.daylight_minutes
            """,
            (date_str, steps, daylight),
        )

    conn.commit()
    print(f"Activity: imported {len(all_dates)} days")


def import_workouts(conn, workouts: list) -> None:
    """
    Upsert Apple Watch workout sessions into workouts table.

    Conflict key is (date, start_time, workout_type) — re-import refreshes
    duration, calories, and heart rate.
    """
    inserted = 0
    skipped = 0

    for w in workouts:
        cursor = conn.execute(
            """
            INSERT INTO workouts
                (date, start_time, end_time, workout_type, duration_min,
                 calories, avg_hr, max_hr, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'apple_health')
            ON CONFLICT(date, start_time, workout_type) DO UPDATE SET
                end_time     = excluded.end_time,
                duration_min = excluded.duration_min,
                calories     = excluded.calories,
                avg_hr       = excluded.avg_hr,
                max_hr       = excluded.max_hr
            """,
            (
                w["date"], w["start_time"], w["end_time"], w["workout_type"],
                w["duration_min"], w["calories"], w["avg_hr"], w["max_hr"],
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    print(f"Workouts: imported {inserted}, skipped {skipped} (already present and unchanged)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Apple Health export XML into health.db"
    )
    parser.add_argument(
        "--file",
        help=f"Path to export.xml (default: {DEFAULT_EXPORT_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print counts without writing to DB",
    )
    args = parser.parse_args()

    filepath = Path(args.file) if args.file else DEFAULT_EXPORT_PATH
    if not filepath.exists():
        print(f"Error: {filepath} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {filepath} ...")
    bp_pairs, body_records, steps_by_date, daylight_by_date, workouts = parse_export(filepath)

    print(
        f"Parsed: {len(bp_pairs)} BP pairs, {len(body_records)} body records "
        f"({sum(1 for r in body_records if r['type'] == 'weight_kg')} weight + "
        f"{sum(1 for r in body_records if r['type'] == 'fat_ratio')} fat%), "
        f"{len(steps_by_date)} step days, {len(daylight_by_date)} daylight days, "
        f"{len(workouts)} workouts"
    )

    if args.dry_run:
        print("Dry run — no DB writes.")
        return

    conn = health_db.get_connection()
    import_bp(conn, bp_pairs)
    import_body_metrics(conn, body_records)
    import_activity(conn, steps_by_date, daylight_by_date)
    import_workouts(conn, workouts)
    conn.close()
    print("Import complete.")


if __name__ == "__main__":
    main()
