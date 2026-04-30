#!/usr/bin/env python3
"""
health_query.py — Read-only query interface for health.db.

Subcommands (all output JSON to stdout):
  lab-trend          --marker NAME [--months N]
  oura-window        [--metric NAME | --all] [--days N]
  search             --query TEXT [--limit N]
  blood-pressure     [--days N] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
  body-metrics       [--days N] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
  activity           [--days N] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
  workouts           [--days N] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--type TYPE]
  workout-exercises  [--date YYYY-MM-DD | --days N]
  tags               [--days N] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--type TAG]
  mood               [--since YYYY-MM-DD] [--kind daily_mood|momentary_emotion]

Exit 0 on success, exit 1 with {"error": "..."} JSON on failure.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db
from bp_sessions import group_sessions


def _out(data: dict) -> None:
    print(json.dumps(data))


def _err(msg: str) -> None:
    print(json.dumps({"error": msg}))
    sys.exit(1)


# ---------------------------------------------------------------------------
# lab-trend
# ---------------------------------------------------------------------------

def lab_trend(marker: str, months: int) -> dict:
    conn = health_db.get_connection()

    # Substring LIKE match (case-insensitive for ASCII); escape LIKE wildcards
    # so user input like "Vit_D" or "Ca%" doesn't wildcard-expand unintentionally
    escaped = marker.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = conn.execute(
        "SELECT id, name, canonical_unit FROM lab_markers WHERE name LIKE ? ESCAPE '\\'",
        (f"%{escaped}%",),
    ).fetchall()
    if len(rows) > 1:
        exact = conn.execute(
            "SELECT id, name, canonical_unit FROM lab_markers WHERE name = ?",
            (marker,),
        ).fetchall()
        if exact:
            rows = exact
    if not rows:
        _err(f"marker not found: {marker}")

    marker_id = rows[0]["id"]
    marker_name = rows[0]["name"]
    unit = rows[0]["canonical_unit"] or ""

    cutoff = (date.today() - timedelta(days=months * 30)).isoformat()

    results = conn.execute(
        """
        SELECT date, value, reference_low, reference_high
        FROM lab_results
        WHERE marker_id = ? AND date >= ?
        ORDER BY date ASC
        """,
        (marker_id, cutoff),
    ).fetchall()

    if not results:
        _err(f"no data for marker {marker_name} in last {months} months")

    # Reference ranges — use the most recent non-null values
    ref_low = ref_high = None
    for r in reversed(results):
        if ref_low is None and r["reference_low"] is not None:
            ref_low = r["reference_low"]
        if ref_high is None and r["reference_high"] is not None:
            ref_high = r["reference_high"]
        if ref_low is not None and ref_high is not None:
            break

    return {
        "marker": marker_name,
        "unit": unit,
        "reference_low": ref_low,
        "reference_high": ref_high,
        "count": len(results),
        "data": [{"date": r["date"], "value": r["value"]} for r in results],
    }


# ---------------------------------------------------------------------------
# oura-window
# ---------------------------------------------------------------------------

def oura_window(days: int, metric: str | None, all_cols: bool) -> dict:
    conn = health_db.get_connection()

    # Derive valid column names from schema at runtime
    schema = conn.execute("PRAGMA table_info(oura_daily)").fetchall()
    valid_metrics = [
        row["name"] for row in schema
        if row["name"] not in ("id", "contributors_json", "fetched_at")
    ]
    numeric_metrics = [
        row["name"] for row in schema
        if row["name"] not in ("id", "day", "contributors_json", "fetched_at",
                                "stress_day_summary", "resilience_level")
    ]

    if metric and metric not in valid_metrics:
        _err(f"unknown metric: {metric}. Valid metrics: {valid_metrics}")

    cutoff = (date.today() - timedelta(days=days)).isoformat()

    if metric:
        rows = conn.execute(
            f"SELECT day, {metric} FROM oura_daily WHERE day >= ? ORDER BY day ASC",
            (cutoff,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM oura_daily WHERE day >= ? ORDER BY day ASC",
            (cutoff,),
        ).fetchall()

    if not rows:
        _err(f"no Oura data in last {days} days")

    data = []
    for r in rows:
        row_dict = dict(r)
        row_dict.pop("id", None)
        row_dict.pop("contributors_json", None)
        row_dict.pop("fetched_at", None)
        # Drop null values when all_cols requested (cleaner output)
        if all_cols or not metric:
            row_dict = {k: v for k, v in row_dict.items() if v is not None}
        data.append(row_dict)

    # Compute averages for numeric columns that appear in the data
    averages: dict = {}
    for col in numeric_metrics:
        if metric and col != metric:
            continue
        vals = [r[col] for r in rows if r[col] is not None]
        if vals:
            averages[col] = round(sum(vals) / len(vals), 2)

    return {
        "days_requested": days,
        "days_available": len(rows),
        "data": data,
        "averages": averages,
    }


# ---------------------------------------------------------------------------
# blood-pressure
# ---------------------------------------------------------------------------


def blood_pressure(days: int, start: str | None, end: str | None) -> dict:
    conn = health_db.get_connection()

    end_date = end if end else date.today().isoformat()
    start_date = start if start else (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute(
        "SELECT date, time, systolic, diastolic, pulse FROM blood_pressure"
        " WHERE date >= ? AND date <= ? ORDER BY date ASC, time ASC",
        (start_date, end_date),
    ).fetchall()

    if not rows:
        range_desc = f"{start_date} to {end_date}" if start else f"last {days} days"
        _err(f"no blood pressure data in {range_desc}")

    sessions = group_sessions(rows)

    all_systolic = [r["systolic"] for r in rows]
    all_diastolic = [r["diastolic"] for r in rows]
    all_pulse = [r["pulse"] for r in rows if r["pulse"] is not None]

    summary = {
        "avg_systolic": round(sum(all_systolic) / len(all_systolic), 1),
        "avg_diastolic": round(sum(all_diastolic) / len(all_diastolic), 1),
        "avg_pulse": round(sum(all_pulse) / len(all_pulse), 1) if all_pulse else None,
        "min_systolic": min(all_systolic),
        "max_systolic": max(all_systolic),
    }

    return {
        "days_requested": days,
        "sessions": len(sessions),
        "data": sessions,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# bp-log  (single reading insert for iMessage entry flow)
# ---------------------------------------------------------------------------

def bp_log(systolic: int, diastolic: int, pulse: int | None,
           reading_date: str, reading_time: str, notes: str | None) -> dict:
    conn = health_db.get_connection()
    conn.execute(
        """INSERT INTO blood_pressure
           (date, time, systolic, diastolic, pulse, source, notes)
           VALUES (?, ?, ?, ?, ?, 'imessage', ?)
           ON CONFLICT(date, time) DO UPDATE SET
             systolic  = excluded.systolic,
             diastolic = excluded.diastolic,
             pulse     = excluded.pulse,
             notes     = excluded.notes""",
        (reading_date, reading_time, systolic, diastolic, pulse, notes),
    )
    conn.commit()
    return {
        "logged": True,
        "date": reading_date,
        "time": reading_time,
        "systolic": systolic,
        "diastolic": diastolic,
        "pulse": pulse,
    }


# ---------------------------------------------------------------------------
# body-metrics
# ---------------------------------------------------------------------------

def body_metrics_query(days: int, start: str | None, end: str | None) -> dict:
    conn = health_db.get_connection()

    end_date = end if end else date.today().isoformat()
    start_date = start if start else (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute(
        """SELECT date, time, weight_lbs, fat_ratio_pct, fat_mass_lbs,
                  lean_mass_lbs, muscle_mass_lbs, source
           FROM body_metrics
           WHERE date >= ? AND date <= ?
           ORDER BY date ASC, time ASC""",
        (start_date, end_date),
    ).fetchall()

    if not rows:
        range_desc = f"{start_date} to {end_date}" if start else f"last {days} days"
        _err(f"no body metrics data in {range_desc}")

    data = [
        {
            "date": r["date"],
            "time": r["time"],
            "weight_lbs": r["weight_lbs"],
            "fat_ratio_pct": r["fat_ratio_pct"],
            "fat_mass_lbs": r["fat_mass_lbs"],
            "lean_mass_lbs": r["lean_mass_lbs"],
            "muscle_mass_lbs": r["muscle_mass_lbs"],
            "source": r["source"],
        }
        for r in rows
    ]

    weights = [r["weight_lbs"] for r in rows if r["weight_lbs"] is not None]
    fat_ratios = [r["fat_ratio_pct"] for r in rows if r["fat_ratio_pct"] is not None]

    latest_row = rows[-1]
    latest = {
        "date": latest_row["date"],
        "weight_lbs": latest_row["weight_lbs"],
        "fat_ratio_pct": latest_row["fat_ratio_pct"],
    }

    summary: dict = {"latest": latest}
    if weights:
        summary["avg_weight_lbs"] = round(sum(weights) / len(weights), 2)
        summary["min_weight_lbs"] = min(weights)
        summary["max_weight_lbs"] = max(weights)
    if fat_ratios:
        summary["avg_fat_ratio_pct"] = round(sum(fat_ratios) / len(fat_ratios), 2)

    return {
        "days_requested": days,
        "readings": len(rows),
        "data": data,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# activity
# ---------------------------------------------------------------------------

def activity_query(days: int, start: str | None, end: str | None) -> dict:
    conn = health_db.get_connection()

    end_date = end if end else date.today().isoformat()
    start_date = start if start else (date.today() - timedelta(days=days)).isoformat()

    rows = conn.execute(
        "SELECT date, steps, daylight_minutes FROM activity_daily"
        " WHERE date >= ? AND date <= ? ORDER BY date ASC",
        (start_date, end_date),
    ).fetchall()

    if not rows:
        range_desc = f"{start_date} to {end_date}" if start else f"last {days} days"
        _err(f"no activity data in {range_desc}")

    data = []
    for r in rows:
        row_dict: dict = {"date": r["date"]}
        if r["steps"] is not None:
            row_dict["steps"] = r["steps"]
        if r["daylight_minutes"] is not None:
            row_dict["daylight_minutes"] = r["daylight_minutes"]
        data.append(row_dict)

    steps_vals = [r["steps"] for r in rows if r["steps"] is not None]
    daylight_vals = [r["daylight_minutes"] for r in rows if r["daylight_minutes"] is not None]

    summary: dict = {}
    if steps_vals:
        summary["avg_steps"] = round(sum(steps_vals) / len(steps_vals), 1)
    if daylight_vals:
        summary["avg_daylight_min"] = round(sum(daylight_vals) / len(daylight_vals), 1)
        summary["total_daylight_hours"] = round(sum(daylight_vals) / 60, 1)

    return {
        "days_requested": days,
        "days_available": len(rows),
        "data": data,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# workouts
# ---------------------------------------------------------------------------

def workouts_query(days: int, start: str | None, end: str | None,
                   workout_type: str | None) -> dict:
    conn = health_db.get_connection()

    end_date = end if end else date.today().isoformat()
    start_date = start if start else (date.today() - timedelta(days=days)).isoformat()

    if workout_type:
        # Escape LIKE wildcards in user input before embedding in % pattern
        escaped_type = (
            workout_type.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        rows = conn.execute(
            "SELECT date, workout_type, duration_min, calories, avg_hr, max_hr, source"
            " FROM workouts"
            " WHERE date >= ? AND date <= ? AND workout_type LIKE ? ESCAPE '\\'"
            " ORDER BY date ASC, start_time ASC",
            (start_date, end_date, f"%{escaped_type}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT date, workout_type, duration_min, calories, avg_hr, max_hr, source"
            " FROM workouts"
            " WHERE date >= ? AND date <= ?"
            " ORDER BY date ASC, start_time ASC",
            (start_date, end_date),
        ).fetchall()

    if not rows:
        range_desc = f"{start_date} to {end_date}" if start else f"last {days} days"
        filter_desc = f" (type filter: {workout_type})" if workout_type else ""
        _err(f"no workouts in {range_desc}{filter_desc}")

    data = []
    for r in rows:
        row_dict: dict = {
            "date": r["date"],
            "workout_type": r["workout_type"],
            "source": r["source"],
        }
        if r["duration_min"] is not None:
            row_dict["duration_min"] = r["duration_min"]
        if r["calories"] is not None:
            row_dict["calories"] = r["calories"]
        if r["avg_hr"] is not None:
            row_dict["avg_hr"] = r["avg_hr"]
        if r["max_hr"] is not None:
            row_dict["max_hr"] = r["max_hr"]
        data.append(row_dict)

    by_type: dict = {}
    for r in rows:
        wtype = r["workout_type"]
        by_type[wtype] = by_type.get(wtype, 0) + 1

    duration_vals = [r["duration_min"] for r in rows if r["duration_min"] is not None]
    calorie_vals = [r["calories"] for r in rows if r["calories"] is not None]

    summary: dict = {"by_type": by_type}
    if duration_vals:
        summary["avg_duration_min"] = round(sum(duration_vals) / len(duration_vals), 1)
    if calorie_vals:
        summary["total_calories"] = round(sum(calorie_vals), 0)

    return {
        "days_requested": days,
        "total_workouts": len(rows),
        "data": data,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# workout-exercises
# ---------------------------------------------------------------------------

def workout_exercises_query(days: int, single_date: str | None) -> dict:
    conn = health_db.get_connection()

    if single_date:
        start_date = single_date
        end_date = single_date
        period = single_date
    else:
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=days)).isoformat()
        period = f"{start_date} to {end_date}"

    # Left join so workout rows without exercises still appear (evernote-only days)
    rows = conn.execute(
        """
        SELECT
            we.workout_date,
            w.workout_type,
            w.duration_min,
            we.exercise_name,
            we.set_number,
            we.reps,
            we.weight_lbs,
            we.notes AS exercise_notes
        FROM workout_exercises we
        LEFT JOIN workouts w ON w.date = we.workout_date
        WHERE we.workout_date >= ? AND we.workout_date <= ?
        ORDER BY we.workout_date ASC, w.workout_type ASC, we.set_number ASC
        """,
        (start_date, end_date),
    ).fetchall()

    if not rows:
        range_desc = single_date if single_date else f"last {days} days"
        _err(f"no workout exercises in {range_desc}")

    # Group exercises under their workout session (date + workout_type)
    workouts_map: dict = {}
    for r in rows:
        key = (r["workout_date"], r["workout_type"])
        if key not in workouts_map:
            workouts_map[key] = {
                "date": r["workout_date"],
                "workout_type": r["workout_type"],
                "duration_min": r["duration_min"],
                "exercises": [],
            }
        exercise: dict = {}
        if r["exercise_name"] is not None:
            exercise["exercise_name"] = r["exercise_name"]
        if r["set_number"] is not None:
            exercise["set_number"] = r["set_number"]
        if r["reps"] is not None:
            exercise["reps"] = r["reps"]
        if r["weight_lbs"] is not None:
            exercise["weight_lbs"] = r["weight_lbs"]
        if r["exercise_notes"] is not None:
            exercise["notes"] = r["exercise_notes"]
        workouts_map[key]["exercises"].append(exercise)

    # Remove null duration_min from workout entries
    workout_list = []
    for entry in workouts_map.values():
        if entry["duration_min"] is None:
            del entry["duration_min"]
        workout_list.append(entry)

    return {
        "period": period,
        "total_exercises": len(rows),
        "workouts": workout_list,
    }


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------

def tags_query(days: int, start: str | None, end: str | None,
               tag_type: str | None) -> dict:
    conn = health_db.get_connection()

    end_date = end if end else date.today().isoformat()
    start_date = start if start else (date.today() - timedelta(days=days)).isoformat()

    if tag_type:
        escaped_type = (
            tag_type.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        rows = conn.execute(
            "SELECT day, tag_type, comment FROM oura_tags"
            " WHERE day >= ? AND day <= ? AND tag_type LIKE ? ESCAPE '\\'"
            " ORDER BY day ASC, tag_type ASC",
            (start_date, end_date, f"%{escaped_type}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT day, tag_type, comment FROM oura_tags"
            " WHERE day >= ? AND day <= ?"
            " ORDER BY day ASC, tag_type ASC",
            (start_date, end_date),
        ).fetchall()

    if not rows:
        range_desc = f"{start_date} to {end_date}" if start else f"last {days} days"
        filter_desc = f" (type filter: {tag_type})" if tag_type else ""
        _err(f"no tags in {range_desc}{filter_desc}")

    data = [
        {"day": r["day"], "tag_type": r["tag_type"], "comment": r["comment"]}
        for r in rows
    ]

    by_type: dict = {}
    for r in rows:
        by_type[r["tag_type"]] = by_type.get(r["tag_type"], 0) + 1

    return {
        "days_requested": days,
        "total_tags": len(rows),
        "data": data,
        "by_type": by_type,
    }


# ---------------------------------------------------------------------------
# mood
# ---------------------------------------------------------------------------

def mood_query(since: str | None, kind: str) -> list:
    conn = health_db.get_connection()
    cutoff = since if since else (date.today() - timedelta(days=30)).isoformat()

    rows = conn.execute(
        "SELECT date, kind, valence, arousal, labels, associations"
        " FROM state_of_mind"
        " WHERE date >= ? AND kind = ?"
        " ORDER BY date DESC",
        (cutoff, kind),
    ).fetchall()

    result = []
    for r in rows:
        labels = json.loads(r["labels"]) if r["labels"] else []
        associations = json.loads(r["associations"]) if r["associations"] else []
        result.append({
            "date": r["date"],
            "kind": r["kind"],
            "valence": r["valence"],
            "arousal": r["arousal"],
            "labels": labels,
            "associations": associations,
        })
    return result


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _fts_quote(query: str) -> str:
    # Wrap in double quotes to force literal phrase matching, preventing
    # FTS5 operator interpretation (AND/OR/NOT/NEAR/*) for user-supplied text
    return '"' + query.replace('"', '""') + '"'


def search_knowledge(query: str, limit: int) -> dict:
    conn = health_db.get_connection()

    try:
        results = conn.execute(
            """
            SELECT hk.show, hk.episode_title, hk.date,
                   snippet(health_knowledge_fts, 1, '[', ']', '...', 20) AS snippet
            FROM health_knowledge_fts
            JOIN health_knowledge hk ON health_knowledge_fts.rowid = hk.rowid
            WHERE health_knowledge_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (_fts_quote(query), limit),
        ).fetchall()
    except sqlite3.OperationalError:
        results = []

    return {
        "query": query,
        "count": len(results),
        "results": [
            {
                "show": r["show"],
                "episode_title": r["episode_title"],
                "date": r["date"],
                "snippet": r["snippet"],
            }
            for r in results
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="health.db query interface")
    sub = parser.add_subparsers(dest="command", required=True)

    lt = sub.add_parser("lab-trend", help="Lab result trend for a marker")
    lt.add_argument("--marker", required=True, help="Marker name (case-insensitive)")
    lt.add_argument("--months", type=int, default=12, help="Lookback window in months")

    ow = sub.add_parser("oura-window", help="Oura daily metrics for a time window")
    ow.add_argument("--days", type=int, default=7, help="Lookback window in days")
    ow.add_argument("--metric", help="Return only this column (plus day)")
    ow.add_argument("--all", dest="all_cols", action="store_true",
                    help="Return all non-null columns for each day")

    se = sub.add_parser("search", help="FTS5 search over podcast health knowledge")
    se.add_argument("--query", required=True, help="Full-text search query")
    se.add_argument("--limit", type=int, default=5, help="Max results to return")

    bp = sub.add_parser("blood-pressure", help="Blood pressure session averages")
    bp.add_argument("--days", type=int, default=30)
    bp.add_argument("--start", help="Start date YYYY-MM-DD (overrides --days)")
    bp.add_argument("--end", help="End date YYYY-MM-DD (default: today)")

    lg = sub.add_parser("bp-log", help="Log a single blood pressure reading")
    lg.add_argument("--systolic", type=int, required=True)
    lg.add_argument("--diastolic", type=int, required=True)
    lg.add_argument("--pulse", type=int, default=None)
    lg.add_argument("--date", dest="reading_date", required=True, help="YYYY-MM-DD")
    lg.add_argument("--time", dest="reading_time", required=True, help="HH:MM")
    lg.add_argument("--notes", default=None)

    bm = sub.add_parser("body-metrics", help="Body composition trend (weight, fat, lean mass)")
    bm.add_argument("--days", type=int, default=90)
    bm.add_argument("--start", help="Start date YYYY-MM-DD (overrides --days)")
    bm.add_argument("--end", help="End date YYYY-MM-DD (default: today)")

    ac = sub.add_parser("activity", help="Daily steps and daylight exposure")
    ac.add_argument("--days", type=int, default=14)
    ac.add_argument("--start", help="Start date YYYY-MM-DD (overrides --days)")
    ac.add_argument("--end", help="End date YYYY-MM-DD (default: today)")

    wo = sub.add_parser("workouts", help="Workout sessions from Apple Watch")
    wo.add_argument("--days", type=int, default=30)
    wo.add_argument("--start", help="Start date YYYY-MM-DD (overrides --days)")
    wo.add_argument("--end", help="End date YYYY-MM-DD (default: today)")
    wo.add_argument("--type", dest="workout_type",
                    help="Case-insensitive substring filter on workout_type")

    we = sub.add_parser("workout-exercises",
                        help="Exercises grouped by workout session")
    we.add_argument("--date", dest="single_date",
                    help="Single day YYYY-MM-DD (overrides --days)")
    we.add_argument("--days", type=int, default=7,
                    help="Lookback window in days (default: 7)")

    tg = sub.add_parser("tags", help="Oura lifestyle tags (sauna, alcohol, etc.)")
    tg.add_argument("--days", type=int, default=30)
    tg.add_argument("--start", help="Start date YYYY-MM-DD (overrides --days)")
    tg.add_argument("--end", help="End date YYYY-MM-DD (default: today)")
    tg.add_argument("--type", dest="tag_type",
                    help="Case-insensitive substring filter on tag_type")

    md = sub.add_parser("mood", help="State of Mind entries (daily_mood or momentary_emotion)")
    md.add_argument("--since", help="Start date YYYY-MM-DD (default: 30 days ago)")
    md.add_argument("--kind", default="daily_mood",
                    choices=["daily_mood", "momentary_emotion"],
                    help="Kind of state_of_mind entry (default: daily_mood)")

    args = parser.parse_args()

    if args.command == "lab-trend":
        result = lab_trend(args.marker, args.months)
    elif args.command == "oura-window":
        result = oura_window(args.days, args.metric, args.all_cols)
    elif args.command == "search":
        result = search_knowledge(args.query, args.limit)
    elif args.command == "blood-pressure":
        result = blood_pressure(args.days, args.start, args.end)
    elif args.command == "bp-log":
        result = bp_log(args.systolic, args.diastolic, args.pulse,
                        args.reading_date, args.reading_time, args.notes)
    elif args.command == "body-metrics":
        result = body_metrics_query(args.days, args.start, args.end)
    elif args.command == "activity":
        result = activity_query(args.days, args.start, args.end)
    elif args.command == "workouts":
        result = workouts_query(args.days, args.start, args.end, args.workout_type)
    elif args.command == "workout-exercises":
        result = workout_exercises_query(args.days, args.single_date)
    elif args.command == "tags":
        result = tags_query(args.days, args.start, args.end, args.tag_type)
    elif args.command == "mood":
        result = {"mood": mood_query(args.since, args.kind)}
    _out(result)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
