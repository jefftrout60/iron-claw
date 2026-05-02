#!/usr/bin/env python3
"""
Apple Health JSON importer — parses Health Auto Export (Liftcode app) exports
and writes to the same health.db tables as import-apple-health.py.

Categories imported:
  body_metrics     — body_mass (lbs), body_fat_percentage, lean_body_mass
  activity_daily   — step_count, time_in_daylight (aggregate by date)
  workouts         — workouts array
  state_of_mind    — state_of_mind metric

Source value 'health_auto_export' distinguishes these rows from XML importer
rows ('apple_health') and Withings rows ('withings_api').

Source priority:
  health_auto_export rows never overwrite withings_api rows in body_metrics

Usage:
  python3 scripts/import-apple-health-json.py --file ~/Downloads/export.json
  python3 scripts/import-apple-health-json.py --file ~/Downloads/export.json --dry-run
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

# health_db lives in workspace/health/
_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
sys.path.insert(0, str(_HEALTH_DIR))
import health_db

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# METRIC_MAP — maps Health Auto Export metric names to internal category tags.
# Update this dict when real exports reveal different or additional names.
# ---------------------------------------------------------------------------
METRIC_MAP = {
    # Body composition
    "body_mass":              "weight_lbs",       # Health Auto Export exports in lbs (not kg)
    "body_fat_percentage":    "fat_ratio",         # decimal 0-1 OR percent 0-100 (detected at parse)
    "lean_body_mass":         "lean_mass_lbs",     # lbs

    # Activity
    "step_count":             "steps",
    "time_in_daylight":       "daylight_minutes",

    # State of mind is handled separately (different structure)
    "state_of_mind":          "state_of_mind",
}

# Source guard fragments — health_auto_export rows never overwrite withings_api rows
_HAE_SOURCE = "source IS 'health_auto_export'"
_HAE_BODY_SOURCE_GUARD = f"WHERE body_metrics.{_HAE_SOURCE}"
_HAE_SOM_SOURCE_GUARD  = f"WHERE state_of_mind.{_HAE_SOURCE}"

# Health Auto Export date format: "2024-01-15 00:00:00 -0800"
_DATE_PREFIX_LEN = 10  # len("YYYY-MM-DD")


def _date_str(raw: str) -> str:
    """Extract YYYY-MM-DD from a Health Auto Export date string."""
    return raw[:_DATE_PREFIX_LEN]


def _ts_str(raw: str) -> str:
    """
    Convert Health Auto Export date string to an ISO-8601-ish timestamp for
    logged_at. We preserve the full string minus the trailing timezone offset
    since sqlite stores it as text anyway.

    Returns the first 19 characters: "YYYY-MM-DD HH:MM:SS"
    """
    return raw[:19]


def parse_metrics(data: dict):
    """
    Walk data["metrics"] and bucket records by internal category.

    Returns:
      body_records     — list of {date, time, type, value}
      steps_by_date    — dict keyed by YYYY-MM-DD: total step count
      daylight_by_date — dict keyed by YYYY-MM-DD: total daylight minutes
      som_raw          — list of raw state_of_mind entry dicts (parsed later)
    """
    body_records: list[dict] = []
    steps_by_date: dict[str, float] = {}
    daylight_by_date: dict[str, float] = {}
    som_raw: list[dict] = []

    metrics = data.get("metrics", [])
    for metric in metrics:
        name = metric.get("name", "")
        internal = METRIC_MAP.get(name)

        if internal is None:
            _LOG.debug("Unrecognised metric name %r — skipping", name)
            continue

        entries = metric.get("data", [])

        if internal == "state_of_mind":
            som_raw.extend(entries)
            continue

        units = metric.get("units", "")

        for entry in entries:
            raw_date = entry.get("date", "")
            if not raw_date:
                continue

            # qty holds the numeric value; skip entries without it
            qty = entry.get("qty")
            if qty is None:
                continue

            try:
                value = float(qty)
            except (TypeError, ValueError):
                _LOG.debug("Metric %r: non-numeric qty %r — skipping", name, qty)
                continue

            d = _date_str(raw_date)
            t = _ts_str(raw_date)[11:16]  # "HH:MM"

            if internal == "steps":
                steps_by_date[d] = steps_by_date.get(d, 0.0) + value
            elif internal == "daylight_minutes":
                daylight_by_date[d] = daylight_by_date.get(d, 0.0) + value
            elif internal in ("weight_lbs", "lean_mass_lbs"):
                body_records.append({"date": d, "time": t, "type": internal, "value": value})
            elif internal == "fat_ratio":
                # Health Auto Export may give either a 0-1 decimal or 0-100 percent.
                # Values <= 1.0 that aren't clearly percentages are treated as decimals
                # and converted to percent. Values > 1.0 are assumed to already be percent.
                if value <= 1.0:
                    fat_pct = value * 100.0
                else:
                    fat_pct = value
                body_records.append({"date": d, "time": t, "type": "fat_ratio_pct", "value": fat_pct})

    return body_records, steps_by_date, daylight_by_date, som_raw


def parse_workouts(data: dict) -> list[dict]:
    """
    Parse the workouts array from the top-level data object.

    Health Auto Export workout fields (best-effort, may vary by app version):
      name, start, end, duration (seconds), activeEnergy, heartRateStats
    """
    workouts_out: list[dict] = []

    for w in data.get("workouts", []):
        workout_type = w.get("name", "Unknown")

        # start / end timestamps
        start_raw = w.get("start", "")
        end_raw = w.get("end", "")
        if not start_raw:
            _LOG.debug("Workout missing start timestamp — skipping: %r", w.get("name"))
            continue

        date_str = _date_str(start_raw)
        start_time = _ts_str(start_raw)[11:16]
        end_time = _ts_str(end_raw)[11:16] if end_raw else None

        # duration: may be in seconds or minutes depending on app version
        duration_min = None
        raw_duration = w.get("duration")
        if raw_duration is not None:
            try:
                dur = float(raw_duration)
                # Heuristic: values > 300 are almost certainly seconds (5 hours in minutes
                # would be unusual); convert to minutes.
                duration_unit = w.get("durationUnit", "")
                if duration_unit.lower() in ("s", "sec", "seconds"):
                    duration_min = dur / 60.0
                elif duration_unit.lower() in ("min", "minutes", ""):
                    # No unit or explicit minutes — check magnitude
                    duration_min = dur / 60.0 if dur > 300 else dur
            except (TypeError, ValueError):
                pass

        # calories: may be nested as {"qty": N, "units": "kcal"} or plain number
        calories = None
        energy = w.get("activeEnergy") or w.get("totalEnergyBurned")
        if energy is not None:
            if isinstance(energy, dict):
                try:
                    calories = round(float(energy.get("qty", 0)))
                except (TypeError, ValueError):
                    pass
            else:
                try:
                    calories = round(float(energy))
                except (TypeError, ValueError):
                    pass

        # heart rate: may be nested as {"avg": {"qty": N}, "min": ..., "max": ...}
        # Also accept top-level avgHeartRate / maxHeartRate scalar fields.
        avg_hr = None
        max_hr = None
        min_hr = None
        hr_stats = w.get("heartRateStats") or w.get("heartRate")
        if isinstance(hr_stats, dict):
            def _extract_hr(node):
                if isinstance(node, dict):
                    try:
                        return int(float(node.get("qty", 0))) or None
                    except (TypeError, ValueError):
                        return None
                elif node is not None:
                    try:
                        return int(float(node)) or None
                    except (TypeError, ValueError):
                        return None
                return None
            avg_hr = _extract_hr(hr_stats.get("avg") or hr_stats.get("average"))
            max_hr = _extract_hr(hr_stats.get("max") or hr_stats.get("maximum"))
            min_hr = _extract_hr(hr_stats.get("min") or hr_stats.get("minimum"))
        # Fall back to top-level scalar fields (Workout export format)
        if avg_hr is None and w.get("avgHeartRate") is not None:
            try:
                avg_hr = int(float(w["avgHeartRate"].get("qty", w["avgHeartRate"])
                             if isinstance(w["avgHeartRate"], dict) else w["avgHeartRate"])) or None
            except (TypeError, ValueError):
                pass
        if max_hr is None and w.get("maxHeartRate") is not None:
            try:
                max_hr = int(float(w["maxHeartRate"].get("qty", w["maxHeartRate"])
                             if isinstance(w["maxHeartRate"], dict) else w["maxHeartRate"])) or None
            except (TypeError, ValueError):
                pass

        # intensity: kcal/hr·kg == METs — objective effort level
        intensity_met = None
        intensity_node = w.get("intensity")
        if isinstance(intensity_node, dict):
            try:
                intensity_met = round(float(intensity_node.get("qty", 0)), 3) or None
            except (TypeError, ValueError):
                pass
        elif intensity_node is not None:
            try:
                intensity_met = round(float(intensity_node), 3) or None
            except (TypeError, ValueError):
                pass

        workouts_out.append({
            "workout_type": workout_type,
            "date": date_str,
            "start_time": start_time,
            "end_time": end_time,
            "duration_min": duration_min,
            "calories": calories,
            "avg_hr": avg_hr,
            "max_hr": max_hr,
            "min_hr": min_hr,
            "intensity_met": intensity_met,
        })

    return workouts_out


def _parse_som_entry(entry: dict):
    """
    Parse a single state_of_mind entry from Health Auto Export JSON.

    The exact field names are unconfirmed; we handle both snake_case and
    camelCase variants and log unrecognised structures at DEBUG.

    Expected fields (one or more of):
      date / startDate
      valence / Valence
      kind / Kind  (e.g. "daily_mood", "momentary_emotion", or int 1/2)
      labels / Labels  (list or comma-separated string)
      associations / Associations  (list or comma-separated string)
    """
    # date
    raw_date = (
        entry.get("date")
        or entry.get("startDate")
        or entry.get("start_date")
    )
    if not raw_date:
        _LOG.debug("state_of_mind entry missing date — skipping: %r", list(entry.keys()))
        return None

    date_str = _date_str(str(raw_date))
    logged_at = _ts_str(str(raw_date))

    # valence: may be float or missing
    valence = None
    raw_valence = entry.get("valence") or entry.get("Valence")
    if raw_valence is not None:
        try:
            valence = float(raw_valence)
        except (TypeError, ValueError):
            _LOG.debug("state_of_mind: non-float valence %r", raw_valence)

    # arousal: optional
    arousal = None
    raw_arousal = entry.get("arousal") or entry.get("Arousal")
    if raw_arousal is not None:
        try:
            arousal = float(raw_arousal)
        except (TypeError, ValueError):
            _LOG.debug("state_of_mind: non-float arousal %r", raw_arousal)

    # kind: string or int
    kind = "daily_mood"
    raw_kind = entry.get("kind") or entry.get("Kind")
    if raw_kind is not None:
        if isinstance(raw_kind, int):
            kind = "momentary_emotion" if raw_kind == 1 else "daily_mood"
        elif isinstance(raw_kind, str):
            normalised = raw_kind.lower().replace(" ", "_")
            if "momentary" in normalised or normalised == "1":
                kind = "momentary_emotion"
            elif "daily" in normalised or normalised == "2":
                kind = "daily_mood"
            else:
                _LOG.debug("state_of_mind: unrecognised kind value %r — defaulting to daily_mood", raw_kind)
        else:
            _LOG.debug("state_of_mind: unexpected kind type %r — defaulting to daily_mood", raw_kind)

    # labels: list or comma-separated string
    labels: list[str] = []
    raw_labels = entry.get("labels") or entry.get("Labels")
    if raw_labels is not None:
        if isinstance(raw_labels, list):
            labels = [str(v) for v in raw_labels]
        elif isinstance(raw_labels, str):
            try:
                parsed = json.loads(raw_labels)
                labels = [str(v) for v in parsed] if isinstance(parsed, list) else [str(parsed)]
            except (json.JSONDecodeError, ValueError):
                labels = [v.strip() for v in raw_labels.split(",") if v.strip()]
        else:
            _LOG.debug("state_of_mind: unexpected labels type %r", type(raw_labels))

    # associations: list or comma-separated string
    associations: list[str] = []
    raw_assoc = entry.get("associations") or entry.get("Associations")
    if raw_assoc is not None:
        if isinstance(raw_assoc, list):
            associations = [str(v) for v in raw_assoc]
        elif isinstance(raw_assoc, str):
            try:
                parsed = json.loads(raw_assoc)
                associations = [str(v) for v in parsed] if isinstance(parsed, list) else [str(parsed)]
            except (json.JSONDecodeError, ValueError):
                associations = [v.strip() for v in raw_assoc.split(",") if v.strip()]

    # Log any keys we didn't consume so future exports can be diagnosed
    consumed = {"date", "startDate", "start_date", "valence", "Valence",
                "arousal", "Arousal", "kind", "Kind", "labels", "Labels",
                "associations", "Associations"}
    for key in entry:
        if key not in consumed:
            _LOG.debug("state_of_mind: unrecognised entry key %r", key)

    return {
        "date": date_str,
        "logged_at": logged_at,
        "kind": kind,
        "valence": valence,
        "arousal": arousal,
        "labels": json.dumps(labels),
        "associations": json.dumps(associations),
    }


def _parse_state_of_mind_direct(entries: list) -> list:
    """
    Parse data.stateOfMind entries from the State of Mind automation export.

    Actual confirmed field names (from real export 2026-04-30):
      start, end, kind, valence, labels (list), associations (list), id
    No arousal field in Apple's export.
    """
    results = []
    for entry in entries:
        start = entry.get("start", "")
        if not start:
            continue
        date_str = start[:10]
        logged_at = start[:19].replace("T", " ")
        kind = entry.get("kind", "daily_mood")
        valence = entry.get("valence")
        labels = entry.get("labels", [])
        associations = entry.get("associations", [])
        results.append({
            "date": date_str,
            "logged_at": logged_at,
            "kind": kind,
            "valence": float(valence) if valence is not None else None,
            "arousal": None,
            "labels": json.dumps(labels if isinstance(labels, list) else []),
            "associations": json.dumps(associations if isinstance(associations, list) else []),
        })
    return results


# ---------------------------------------------------------------------------
# DB write functions
# ---------------------------------------------------------------------------

def import_body_metrics(conn, body_records: list) -> None:
    """
    Upsert Health Auto Export body composition records into body_metrics.

    health_auto_export rows never overwrite withings_api rows at the same
    (date, time).
    """
    inserted = 0
    skipped = 0

    for rec in body_records:
        field = rec["type"]  # "weight_lbs", "fat_ratio_pct", "lean_mass_lbs"

        cursor = conn.execute(
            f"""
            INSERT INTO body_metrics (date, time, {field}, source)
            VALUES (?, ?, ?, 'health_auto_export')
            ON CONFLICT(date, time) DO UPDATE SET
                {field} = excluded.{field},
                source   = excluded.source
            {_HAE_BODY_SOURCE_GUARD}
            """,
            (rec["date"], rec["time"], round(rec["value"], 2)),
        )
        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    print(f"Body metrics: imported {inserted}, skipped {skipped} (already present from withings_api)")


def import_activity(conn, steps_by_date: dict, daylight_by_date: dict) -> None:
    """
    Upsert daily step and daylight totals into activity_daily.

    Re-import overwrites totals — Health Auto Export may retroactively correct
    step counts just like the XML export.
    """
    all_dates = set(steps_by_date) | set(daylight_by_date)

    for date_str in sorted(all_dates):
        steps = int(steps_by_date[date_str]) if date_str in steps_by_date else None
        daylight = daylight_by_date.get(date_str)

        conn.execute(
            """
            INSERT INTO activity_daily (date, steps, daylight_minutes, source)
            VALUES (?, ?, ?, 'health_auto_export')
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
    Upsert workout sessions into workouts table.

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
                 calories, avg_hr, max_hr, min_hr, intensity_met, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'health_auto_export')
            ON CONFLICT(date, start_time, workout_type) DO UPDATE SET
                end_time      = excluded.end_time,
                duration_min  = excluded.duration_min,
                calories      = excluded.calories,
                avg_hr        = excluded.avg_hr,
                max_hr        = excluded.max_hr,
                min_hr        = excluded.min_hr,
                intensity_met = excluded.intensity_met
            """,
            (
                w["date"], w["start_time"], w["end_time"], w["workout_type"],
                w["duration_min"], w["calories"], w["avg_hr"], w["max_hr"],
                w["min_hr"], w["intensity_met"],
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    print(f"Workouts: imported {inserted}, skipped {skipped} (already present and unchanged)")


def import_state_of_mind(conn, records: list) -> None:
    """
    Upsert pre-parsed state_of_mind records (list of dicts with date, logged_at,
    kind, valence, arousal, labels, associations keys).
    """
    inserted = 0
    skipped = 0
    unparseable = 0

    for rec in records:
        if rec is None:
            unparseable += 1
            continue

        cursor = conn.execute(
            f"""
            INSERT INTO state_of_mind (date, logged_at, kind, valence, arousal,
                                       labels, associations, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'health_auto_export')
            ON CONFLICT(date, kind, logged_at) DO UPDATE SET
                valence      = excluded.valence,
                arousal      = excluded.arousal,
                labels       = excluded.labels,
                associations = excluded.associations
            {_HAE_SOM_SOURCE_GUARD}
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
    msg = f"State of mind: imported {inserted}, skipped {skipped}"
    if unparseable:
        msg += f", {unparseable} unparseable"
    print(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Health Auto Export JSON into health.db"
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the Health Auto Export JSON file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print counts without writing to DB",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG logging to see unrecognised metrics and fields",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"Error: {filepath} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {filepath} ...")
    with open(filepath, encoding="utf-8") as fh:
        raw = json.load(fh)

    # Support both {"data": {"metrics": [...], "workouts": [...]}} and flat {"metrics": [...]}
    data = raw.get("data", raw)

    # State of Mind exports use data.stateOfMind (separate automation, separate file)
    som_direct = _parse_state_of_mind_direct(data.get("stateOfMind", []))

    body_records, steps_by_date, daylight_by_date, som_raw = parse_metrics(data)
    workouts = parse_workouts(data)

    # Merge state-of-mind from both paths
    all_som = som_direct + [r for r in [_parse_som_entry(e) for e in som_raw] if r]

    weight_count = sum(1 for r in body_records if r["type"] == "weight_lbs")
    fat_count    = sum(1 for r in body_records if r["type"] == "fat_ratio_pct")
    lean_count   = sum(1 for r in body_records if r["type"] == "lean_mass_lbs")

    print(
        f"Parsed: {weight_count} weight, {fat_count} fat%, {lean_count} lean-mass records; "
        f"{len(steps_by_date)} step days, {len(daylight_by_date)} daylight days; "
        f"{len(workouts)} workouts; {len(all_som)} state-of-mind entries"
    )

    if args.dry_run:
        print("Dry run — no DB writes.")
        return

    conn = health_db.get_connection()
    import_body_metrics(conn, body_records)
    import_activity(conn, steps_by_date, daylight_by_date)
    import_workouts(conn, workouts)
    import_state_of_mind(conn, all_som)
    health_db.set_last_synced(conn, "apple_health_json", date.today().isoformat())
    conn.close()
    print("Import complete.")


if __name__ == "__main__":
    main()
