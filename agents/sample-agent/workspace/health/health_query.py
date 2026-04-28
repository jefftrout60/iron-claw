#!/usr/bin/env python3
"""
health_query.py — Read-only query interface for health.db.

Subcommands (all output JSON to stdout):
  lab-trend       --marker NAME [--months N]
  oura-window     [--metric NAME | --all] [--days N]
  search          --query TEXT [--limit N]
  blood-pressure  [--days N] [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Exit 0 on success, exit 1 with {"error": "..."} JSON on failure.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health_db


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

def _group_sessions(rows: list, gap_minutes: int = 30) -> list:
    """Group BP readings into sessions separated by gaps > gap_minutes."""
    if not rows:
        return []

    # Sort by (date, time) ascending — rows may already be sorted but be safe
    sorted_rows = sorted(rows, key=lambda r: (r["date"], r["time"]))

    sessions = []
    current: list = [sorted_rows[0]]

    for row in sorted_rows[1:]:
        prev = current[-1]
        prev_dt = datetime.fromisoformat(f"{prev['date']}T{prev['time']}")
        curr_dt = datetime.fromisoformat(f"{row['date']}T{row['time']}")
        gap = (curr_dt - prev_dt).total_seconds() / 60

        if gap <= gap_minutes:
            current.append(row)
        else:
            sessions.append(_make_session(current))
            current = [row]

    sessions.append(_make_session(current))
    return sessions


def _make_session(rows: list) -> dict:
    first_time = rows[0]["time"]
    last_time = rows[-1]["time"]
    time_range = first_time if len(rows) == 1 else f"{first_time} – {last_time}"

    readings = [
        {"time": r["time"], "systolic": r["systolic"],
         "diastolic": r["diastolic"], "pulse": r["pulse"]}
        for r in rows
    ]

    return {
        "date": rows[0]["date"],
        "time_range": time_range,
        "readings": readings,
        "avg_systolic": round(sum(r["systolic"] for r in rows) / len(rows), 1),
        "avg_diastolic": round(sum(r["diastolic"] for r in rows) / len(rows), 1),
        "avg_pulse": round(sum(r["pulse"] for r in rows if r["pulse"] is not None) /
                           sum(1 for r in rows if r["pulse"] is not None), 1)
                    if any(r["pulse"] is not None for r in rows) else None,
    }


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

    sessions = _group_sessions(rows)

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
    _out(result)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
