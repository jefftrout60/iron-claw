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
import json
import logging
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

# health_db lives in workspace/health/
_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
sys.path.insert(0, str(_HEALTH_DIR))
import health_db

DEFAULT_EXPORT_PATH = Path.home() / "Downloads/apple_health_export/export.xml"

# Base source guard fragment — combined with a table qualifier in each upsert so that
# apple_health rows never overwrite data from other sources (withings_api, omron_csv, etc.).
_AH_SOURCE = "source IS 'apple_health'"
_AH_BP_SOURCE_GUARD   = f"WHERE blood_pressure.{_AH_SOURCE}"
_AH_BODY_SOURCE_GUARD = f"WHERE body_metrics.{_AH_SOURCE}"
_AH_SOM_SOURCE_GUARD  = f"WHERE state_of_mind.{_AH_SOURCE}"

_LOG = logging.getLogger(__name__)

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

    Returns (bp_pairs, body_records, steps_by_date, daylight_by_date, workouts,
             som_records):
      bp_pairs        — dict keyed by startDate string: {"systolic": N, "diastolic": N}
      body_records    — list of dicts: {ts, type, value}
      steps_by_date   — dict keyed by YYYY-MM-DD: total step count
      daylight_by_date — dict keyed by YYYY-MM-DD: total daylight minutes
      workouts        — list of dicts with workout details
      som_records     — list of dicts parsed from HKStateOfMindSample elements
    """
    # BP: buffer systolic and diastolic separately; only flush complete pairs
    bp_systolic = {}   # startDate str → float
    bp_diastolic = {}  # startDate str → float

    body_records = []
    steps_by_date = {}
    daylight_by_date = {}
    workouts = []
    som_records = []

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
                    unit = elem.get("unit", "kg").lower()
                    rec_type = "weight_lbs" if unit in ("lb", "lbs") else "weight_kg"
                    body_records.append({"ts": ts_str, "type": rec_type, "value": value})
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

        elif elem.tag == "HKStateOfMindSample":
            som = _parse_state_of_mind(elem)
            if som:
                som_records.append(som)
            elem.clear()

    # Flush complete BP pairs only
    bp_pairs = {}
    for ts_str in bp_systolic:
        if ts_str in bp_diastolic:
            bp_pairs[ts_str] = {
                "systolic": bp_systolic[ts_str],
                "diastolic": bp_diastolic[ts_str],
            }

    return bp_pairs, body_records, steps_by_date, daylight_by_date, workouts, som_records


def _parse_workout(elem):
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

    # Extract heart rate and calories from WorkoutStatistics children
    avg_hr = None
    max_hr = None
    for stat in elem.findall("WorkoutStatistics"):
        stype = stat.get("type", "")
        if stype == "HKQuantityTypeIdentifierHeartRate":
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
        elif stype == "HKQuantityTypeIdentifierActiveEnergyBurned" and calories is None:
            sum_str = stat.get("sum", "")
            if sum_str:
                try:
                    calories = round(float(sum_str))
                except ValueError:
                    pass

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


# ---------------------------------------------------------------------------
# State of Mind (HKStateOfMindSample)
# ---------------------------------------------------------------------------

# Known MetadataEntry key names based on HealthKit documentation patterns.
# These are best-effort — real exports may vary; unknown keys are logged and skipped.
_SOM_VALENCE_KEY = "HKStateOfMindValence"
_SOM_AROUSAL_KEY = "HKStateOfMindArousal"
_SOM_KIND_KEY = "HKStateOfMindKind"
# Labels appear as HKStateOfMindLabel0, HKStateOfMindLabel1, ... OR as HKStateOfMindLabels
_SOM_LABEL_PREFIX = "HKStateOfMindLabel"

# Integer values for kind (from HealthKit docs)
_SOM_KIND_MOMENTARY = 1
_SOM_KIND_DAILY = 2

# Known MetadataEntry keys consumed above — anything else is logged at DEBUG
_SOM_KNOWN_KEYS = {_SOM_VALENCE_KEY, _SOM_AROUSAL_KEY, _SOM_KIND_KEY}


def _parse_state_of_mind(elem) -> dict | None:
    """
    Parse a single HKStateOfMindSample XML element into a dict ready for upsert.

    MetadataEntry child elements carry valence, arousal, kind, and labels.
    Any unrecognised keys are logged at DEBUG so future exports can be diagnosed.

    Returns None if startDate is missing or unparseable.
    """
    start_str = elem.get("startDate", "")
    end_str = elem.get("endDate", "")

    if not start_str:
        return None

    try:
        start_dt = _parse_ts(start_str)
    except ValueError:
        return None

    logged_at = start_dt.isoformat()
    date_str = start_dt.strftime("%Y-%m-%d")

    # Collect all MetadataEntry children into a flat key→value dict
    metadata: dict[str, str] = {}
    for meta in elem.findall("MetadataEntry"):
        key = meta.get("key", "")
        value = meta.get("value", "")
        if key:
            metadata[key] = value

    # Log unrecognised keys so we can refine the parser as real exports are seen
    for key in metadata:
        if key not in _SOM_KNOWN_KEYS and not key.startswith(_SOM_LABEL_PREFIX):
            _LOG.debug("HKStateOfMindSample: unrecognised MetadataEntry key %r", key)

    # valence: float in raw Apple Health data (-1.0 to 1.0), mapped from 1-10 scale
    valence = None
    if _SOM_VALENCE_KEY in metadata:
        try:
            valence = float(metadata[_SOM_VALENCE_KEY])
        except ValueError:
            _LOG.debug("HKStateOfMindSample: non-float valence %r", metadata[_SOM_VALENCE_KEY])

    # arousal: float, not always present
    arousal = None
    if _SOM_AROUSAL_KEY in metadata:
        try:
            arousal = float(metadata[_SOM_AROUSAL_KEY])
        except ValueError:
            _LOG.debug("HKStateOfMindSample: non-float arousal %r", metadata[_SOM_AROUSAL_KEY])

    # kind: 1=momentary_emotion, 2=daily_mood; fall back to date comparison
    kind = "daily_mood"
    if _SOM_KIND_KEY in metadata:
        try:
            kind_int = int(metadata[_SOM_KIND_KEY])
            kind = "momentary_emotion" if kind_int == _SOM_KIND_MOMENTARY else "daily_mood"
        except ValueError:
            _LOG.debug("HKStateOfMindSample: non-int kind %r", metadata[_SOM_KIND_KEY])
    elif start_str and end_str and start_str != end_str:
        # If the sample spans a range it's momentary; same timestamp = daily snapshot
        kind = "momentary_emotion"

    # labels: collect HKStateOfMindLabel0, HKStateOfMindLabel1, ...
    # Also accept a plain "HKStateOfMindLabels" key if present (array or comma-separated)
    labels: list[str] = []
    if "HKStateOfMindLabels" in metadata:
        raw = metadata["HKStateOfMindLabels"]
        # May be JSON array or comma-separated string
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                labels = [str(v) for v in parsed]
            else:
                labels = [str(parsed)]
        except (json.JSONDecodeError, ValueError):
            labels = [v.strip() for v in raw.split(",") if v.strip()]
    else:
        idx = 0
        while True:
            key = f"{_SOM_LABEL_PREFIX}{idx}"
            if key in metadata:
                labels.append(metadata[key])
                idx += 1
            else:
                break

    return {
        "date": date_str,
        "logged_at": logged_at,
        "kind": kind,
        "valence": valence,
        "arousal": arousal,
        "labels": json.dumps(labels),
        "associations": json.dumps([]),  # not present in XML exports; stored as empty list
    }


def import_state_of_mind(conn, records: list) -> None:
    """
    Upsert HKStateOfMindSample records into the state_of_mind table.

    Conflict key is (date, kind, logged_at). Only overwrites rows that were
    previously imported from apple_health — manual entries are preserved.
    """
    inserted = 0
    skipped = 0

    for rec in records:
        cursor = conn.execute(
            f"""
            INSERT INTO state_of_mind (date, logged_at, kind, valence, arousal,
                                       labels, associations, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'apple_health')
            ON CONFLICT(date, kind, logged_at) DO UPDATE SET
                valence      = excluded.valence,
                arousal      = excluded.arousal,
                labels       = excluded.labels,
                associations = excluded.associations
            {_AH_SOM_SOURCE_GUARD}
            """,
            (
                rec["date"], rec["logged_at"], rec["kind"],
                rec["valence"], rec["arousal"],
                rec["labels"], rec["associations"],
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    print(f"State of mind: imported {inserted}, skipped {skipped}")


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
            f"""
            INSERT INTO blood_pressure (date, time, systolic, diastolic, source)
            VALUES (?, ?, ?, ?, 'apple_health')
            ON CONFLICT(date, time) DO UPDATE SET
                systolic  = excluded.systolic,
                diastolic = excluded.diastolic,
                source    = excluded.source
            {_AH_BP_SOURCE_GUARD}
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

        if rec["type"] in ("weight_kg", "weight_lbs"):
            weight_lbs = rec["value"] * 2.20462 if rec["type"] == "weight_kg" else rec["value"]
            cursor = conn.execute(
                f"""
                INSERT INTO body_metrics (date, time, weight_lbs, source)
                VALUES (?, ?, ?, 'apple_health')
                ON CONFLICT(date, time) DO UPDATE SET
                    weight_lbs = excluded.weight_lbs,
                    source     = excluded.source
                {_AH_BODY_SOURCE_GUARD}
                """,
                (date_str, time_str, round(weight_lbs, 2)),
            )
        elif rec["type"] == "fat_ratio":
            fat_pct = rec["value"] * 100.0
            cursor = conn.execute(
                f"""
                INSERT INTO body_metrics (date, time, fat_ratio_pct, source)
                VALUES (?, ?, ?, 'apple_health')
                ON CONFLICT(date, time) DO UPDATE SET
                    fat_ratio_pct = excluded.fat_ratio_pct,
                    source        = excluded.source
                {_AH_BODY_SOURCE_GUARD}
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
    bp_pairs, body_records, steps_by_date, daylight_by_date, workouts, som_records = (
        parse_export(filepath)
    )

    print(
        f"Parsed: {len(bp_pairs)} BP pairs, {len(body_records)} body records "
        f"({sum(1 for r in body_records if r['type'] in ('weight_kg', 'weight_lbs'))} weight + "
        f"{sum(1 for r in body_records if r['type'] == 'fat_ratio')} fat%), "
        f"{len(steps_by_date)} step days, {len(daylight_by_date)} daylight days, "
        f"{len(workouts)} workouts, {len(som_records)} state-of-mind samples"
    )

    if args.dry_run:
        print("Dry run — no DB writes.")
        return

    conn = health_db.get_connection()
    import_bp(conn, bp_pairs)
    import_body_metrics(conn, body_records)
    import_activity(conn, steps_by_date, daylight_by_date)
    import_workouts(conn, workouts)
    import_state_of_mind(conn, som_records)
    health_db.set_last_synced(conn, "apple_health", date.today().isoformat())
    conn.close()
    print("Import complete.")


if __name__ == "__main__":
    main()
