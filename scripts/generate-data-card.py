#!/usr/bin/env python3
"""Generate DATA_CARD.md from health.db for agent consumption."""
import sqlite3
import os
import sys
from datetime import datetime

DB_PATH = os.path.expanduser("~/ironclaw/agents/sample-agent/workspace/health/health.db")
OUT_PATH = os.path.expanduser("~/ironclaw/agents/sample-agent/workspace/health/DATA_CARD.md")

# Maps table name → sync_state resource name when they differ
_SYNC_KEY_OVERRIDE = {
    "oura_daily":          "daily_summaries",
    "oura_sleep_sessions": "sleep",
    "oura_heartrate":      "heartrate",
}

# (table_name, display_label, date_column)
TABLES = [
    ("lab_results",          "Lab results",                "date"),
    ("oura_daily",           "Oura daily summaries",       "day"),
    ("oura_sleep_sessions",  "Oura sleep sessions",        "day"),
    ("oura_heartrate",       "Oura heart rate",            "timestamp"),
    ("body_metrics",         "Body metrics",               "date"),
    ("blood_pressure",       "Blood pressure",             "date"),
    ("activity_daily",       "Activity (steps/daylight)",  "date"),
    ("workouts",             "Workouts",                   "date"),
    ("state_of_mind",        "State of mind",              "date"),
    ("health_knowledge",     "Health knowledge (podcasts)","date"),
    ("evernote_workouts",    "Evernote workout notes",     "date"),
]


def run():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: health.db not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    # Pull last_synced per resource from sync_state
    try:
        sync = {r[0]: r[1] for r in conn.execute(
            "SELECT resource, last_synced FROM sync_state"
        )}
    except Exception:
        sync = {}

    lines = [
        "# Health DB Data Card",
        f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
        "## Tables",
        "",
        "| Table | Rows | Earliest | Latest | Last Sync |",
        "|-------|------|----------|--------|-----------|",
    ]

    for table, label, date_col in TABLES:
        try:
            row = conn.execute(
                f"SELECT COUNT(*), MIN({date_col}), MAX({date_col}) FROM {table}"
            ).fetchone()
            count, earliest, latest = row
            sync_key = _SYNC_KEY_OVERRIDE.get(table, table)
            last_sync = sync.get(sync_key, "—") or "—"
            lines.append(
                f"| {label} | {count:,} | {earliest or '—'} | {latest or '—'} | {last_sync} |"
            )
        except Exception:
            lines.append(f"| {label} | — | — | — | — |")

    conn.close()

    with open(OUT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"DATA_CARD.md written to {OUT_PATH}")


if __name__ == "__main__":
    run()
