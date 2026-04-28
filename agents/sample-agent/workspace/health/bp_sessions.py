"""
bp_sessions.py — Shared session-grouping logic for blood pressure readings.

Used by:
  - health_query.py (blood-pressure subcommand, container-side)
  - scripts/bp-report.py (HTML report generator, host-side)
"""

from __future__ import annotations

from datetime import datetime


def group_sessions(rows: list, gap_minutes: int = 30) -> list:
    """
    Group blood pressure readings into sessions separated by gaps > gap_minutes.

    Args:
        rows: list of dicts or sqlite3.Row objects with keys:
              date (YYYY-MM-DD), time (HH:MM), systolic, diastolic, pulse
        gap_minutes: readings within this many minutes are the same session

    Returns:
        list of session dicts, each with date, time_range, readings,
        avg_systolic, avg_diastolic, avg_pulse
    """
    if not rows:
        return []

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
