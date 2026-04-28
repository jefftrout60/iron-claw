#!/usr/bin/env python3
"""
Blood pressure HTML report generator for doctor appointments.

Reads from health.db, groups readings into sessions (gap > 30 min = new session),
and produces a single-file HTML report with summary stats, session table, and
individual readings appendix.

Usage:
  python3.13 scripts/bp-report.py --start 2026-01-01 --end 2026-04-28
  python3.13 scripts/bp-report.py --start 2026-01-01 --end 2026-04-28 --output bp_jan_apr.html
"""

import argparse
import html
import statistics
import sys
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
sys.path.insert(0, str(_HEALTH_DIR))
import health_db


# ---------------------------------------------------------------------------
# Session grouping (mirrors health_query.py _group_sessions / _make_session)
# ---------------------------------------------------------------------------

def _group_sessions(rows: list, gap_minutes: int = 30) -> list:
    """Group BP readings into sessions separated by gaps > gap_minutes."""
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
    pulse_vals = [r["pulse"] for r in rows if r["pulse"] is not None]

    return {
        "date": rows[0]["date"],
        "time_range": time_range,
        "readings": readings,
        "avg_systolic": round(sum(r["systolic"] for r in rows) / len(rows), 1),
        "avg_diastolic": round(sum(r["diastolic"] for r in rows) / len(rows), 1),
        "avg_pulse": round(sum(pulse_vals) / len(pulse_vals), 1) if pulse_vals else None,
    }


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; }
body {
    font-family: Arial, sans-serif;
    font-size: 13px;
    color: #222;
    max-width: 960px;
    margin: 32px auto;
    padding: 0 24px;
}
h1 { font-size: 22px; margin-bottom: 4px; }
.subtitle { color: #555; font-size: 13px; margin-bottom: 24px; }

/* Summary box */
.summary-box {
    background: #dbeeff;
    border: 1px solid #9ac4e8;
    border-radius: 6px;
    padding: 16px 24px;
    margin-bottom: 28px;
}
.summary-box h2 { font-size: 14px; margin: 0 0 12px 0; }
.summary-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 12px 32px;
}
.stat { display: flex; flex-direction: column; }
.stat-label { font-size: 11px; color: #555; text-transform: uppercase; letter-spacing: 0.03em; }
.stat-value { font-size: 20px; font-weight: bold; color: #1a4f8a; }

/* Tables */
h2.section { font-size: 15px; margin: 28px 0 10px 0; border-bottom: 2px solid #ccc; padding-bottom: 4px; }
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    margin-bottom: 24px;
}
th {
    background: #4a7cbf;
    color: #fff;
    text-align: left;
    padding: 7px 10px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
td { padding: 6px 10px; border-bottom: 1px solid #e0e0e0; }
tr:nth-child(even) td { background: #f5f8fc; }
tr:last-child td { border-bottom: none; }

/* Print button */
.no-print button {
    background: #4a7cbf;
    color: #fff;
    border: none;
    padding: 10px 20px;
    font-size: 13px;
    border-radius: 4px;
    cursor: pointer;
    margin-bottom: 20px;
}
.no-print button:hover { background: #3a6aad; }

@media print {
    .no-print { display: none; }
    body { font-size: 11pt; margin: 0; padding: 0; max-width: 100%; }
    h1 { font-size: 18pt; }
    .summary-box { background: #edf4ff !important; -webkit-print-color-adjust: exact; }
    th { background: #4a7cbf !important; -webkit-print-color-adjust: exact; }
    tr:nth-child(even) td { background: #f5f8fc !important; -webkit-print-color-adjust: exact; }
    tr { page-break-inside: avoid; }
    table { page-break-inside: auto; }
}
"""


def _fmt_date(iso_date: str) -> str:
    """Format YYYY-MM-DD as 'Month D, YYYY' for display."""
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%B %-d, %Y")
    except ValueError:
        return html.escape(iso_date)


def _pulse_str(val) -> str:
    return str(val) if val is not None else "—"


def build_html(start: str, end: str, rows: list) -> str:
    sessions = _group_sessions(rows)

    all_systolic = [r["systolic"] for r in rows]
    all_diastolic = [r["diastolic"] for r in rows]
    all_pulse = [r["pulse"] for r in rows if r["pulse"] is not None]

    avg_sys = round(statistics.mean(all_systolic), 1)
    avg_dia = round(statistics.mean(all_diastolic), 1)
    avg_pulse = round(statistics.mean(all_pulse), 1) if all_pulse else None
    min_sys = min(all_systolic)
    max_sys = max(all_systolic)

    today_str = datetime.today().strftime("%Y-%m-%d")
    start_display = _fmt_date(start)
    end_display = _fmt_date(end)

    # Build sessions table rows
    session_rows_html = []
    for s in sessions:
        session_rows_html.append(
            f"<tr>"
            f"<td>{html.escape(s['date'])}</td>"
            f"<td>{html.escape(s['time_range'])}</td>"
            f"<td style='text-align:center'>{len(s['readings'])}</td>"
            f"<td style='text-align:center'>{s['avg_systolic']}</td>"
            f"<td style='text-align:center'>{s['avg_diastolic']}</td>"
            f"<td style='text-align:center'>{_pulse_str(s['avg_pulse'])}</td>"
            f"</tr>"
        )

    # Build individual readings table rows
    reading_rows_html = []
    for r in rows:
        reading_rows_html.append(
            f"<tr>"
            f"<td>{html.escape(r['date'])}</td>"
            f"<td>{html.escape(r['time'])}</td>"
            f"<td style='text-align:center'>{r['systolic']}</td>"
            f"<td style='text-align:center'>{r['diastolic']}</td>"
            f"<td style='text-align:center'>{_pulse_str(r['pulse'])}</td>"
            f"</tr>"
        )

    avg_pulse_display = str(avg_pulse) if avg_pulse is not None else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Blood Pressure Report</title>
<style>
{_CSS}
</style>
</head>
<body>

<div class="no-print">
  <button onclick="window.print()">Print / Save as PDF</button>
</div>

<h1>Blood Pressure Report</h1>
<p class="subtitle">{html.escape(start_display)} &ndash; {html.escape(end_display)} &nbsp;&bull;&nbsp; Generated: {html.escape(today_str)}</p>

<div class="summary-box">
  <h2>Summary</h2>
  <div class="summary-grid">
    <div class="stat">
      <span class="stat-label">Avg Systolic</span>
      <span class="stat-value">{avg_sys}</span>
    </div>
    <div class="stat">
      <span class="stat-label">Avg Diastolic</span>
      <span class="stat-value">{avg_dia}</span>
    </div>
    <div class="stat">
      <span class="stat-label">Min Systolic</span>
      <span class="stat-value">{min_sys}</span>
    </div>
    <div class="stat">
      <span class="stat-label">Max Systolic</span>
      <span class="stat-value">{max_sys}</span>
    </div>
    <div class="stat">
      <span class="stat-label">Avg Pulse</span>
      <span class="stat-value">{avg_pulse_display}</span>
    </div>
    <div class="stat">
      <span class="stat-label">Total Readings</span>
      <span class="stat-value">{len(rows)}</span>
    </div>
    <div class="stat">
      <span class="stat-label">Total Sessions</span>
      <span class="stat-value">{len(sessions)}</span>
    </div>
  </div>
</div>

<h2 class="section">Sessions</h2>
<table>
  <thead>
    <tr>
      <th>Date</th>
      <th>Time Range</th>
      <th style="text-align:center"># Readings</th>
      <th style="text-align:center">Avg Sys</th>
      <th style="text-align:center">Avg Dia</th>
      <th style="text-align:center">Avg Pulse</th>
    </tr>
  </thead>
  <tbody>
    {"".join(session_rows_html)}
  </tbody>
</table>

<h2 class="section">All Readings</h2>
<table>
  <thead>
    <tr>
      <th>Date</th>
      <th>Time</th>
      <th style="text-align:center">Systolic</th>
      <th style="text-align:center">Diastolic</th>
      <th style="text-align:center">Pulse</th>
    </tr>
  </thead>
  <tbody>
    {"".join(reading_rows_html)}
  </tbody>
</table>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an HTML blood pressure report from health.db"
    )
    parser.add_argument("--start", required=True, metavar="YYYY-MM-DD",
                        help="Start date (inclusive)")
    parser.add_argument("--end", required=True, metavar="YYYY-MM-DD",
                        help="End date (inclusive)")
    parser.add_argument("--output", metavar="FILENAME",
                        help="Output HTML filename (default: bp_report_{start}_{end}.html)")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else Path(f"bp_report_{args.start}_{args.end}.html")

    conn = health_db.get_connection()
    rows = conn.execute(
        "SELECT date, time, systolic, diastolic, pulse"
        " FROM blood_pressure"
        " WHERE date >= ? AND date <= ?"
        " ORDER BY date ASC, time ASC",
        (args.start, args.end),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"No blood pressure data found between {args.start} and {args.end}.", file=sys.stderr)
        sys.exit(1)

    # Convert sqlite3.Row objects to plain dicts so _group_sessions can subscript freely
    rows = [dict(r) for r in rows]

    html_content = build_html(args.start, args.end, rows)

    output_path.write_text(html_content, encoding="utf-8")
    print(str(output_path.resolve()))


if __name__ == "__main__":
    main()
