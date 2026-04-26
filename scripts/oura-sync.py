#!/usr/bin/env python3
"""
Oura Ring v2 API sync script — imports all data into health.db.

Usage:
  python3.13 scripts/oura-sync.py                    # incremental (use sync_state)
  python3.13 scripts/oura-sync.py --historical       # full import from 2015-01-01
  python3.13 scripts/oura-sync.py --since 2025-01-01 # manual start date

Requires OURA_PERSONAL_ACCESS_TOKEN in agents/sample-agent/.env or environment.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

# health_db lives in the podcast-summary skill scripts directory
_REPO_ROOT = Path(__file__).parent.parent
_SKILL_SCRIPTS = _REPO_ROOT / "agents/sample-agent/workspace/skills/podcast-summary/scripts"
sys.path.insert(0, str(_SKILL_SCRIPTS))
import health_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("oura-sync")

OURA_BASE = "https://api.ouraring.com/v2/usercollection"
HISTORICAL_START = "2015-01-01"
DEFAULT_START = "2024-01-01"


# ---------------------------------------------------------------------------
# .env / token loading
# ---------------------------------------------------------------------------

def load_token() -> str:
    """Load OURA_PERSONAL_ACCESS_TOKEN from environment or .env file."""
    token = os.environ.get("OURA_PERSONAL_ACCESS_TOKEN", "")
    if token:
        return token

    # Walk up from repo root looking for agents/sample-agent/.env
    env_path = _REPO_ROOT / "agents/sample-agent/.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                if key.strip() == "OURA_PERSONAL_ACCESS_TOKEN":
                    return val.strip().strip('"').strip("'")

    print("Error: OURA_PERSONAL_ACCESS_TOKEN not found in environment or agents/sample-agent/.env",
          file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch_all(resource: str, start_date: str, end_date: str, headers: dict) -> list[dict]:
    """Fetch all pages for a resource/date-range, following next_token cursor."""
    url = f"{OURA_BASE}/{resource}"
    params: dict = {"start_date": start_date, "end_date": end_date}
    results = []

    while True:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        except requests.RequestException as e:
            log.warning("Network error fetching %s: %s", resource, e)
            break

        if resp.status_code == 404:
            log.warning("Endpoint %s returned 404 — skipping (Gen3-only or unavailable)", resource)
            return []
        if resp.status_code == 429:
            log.warning("Rate limited on %s — sleeping 60s", resource)
            time.sleep(60)
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
            except requests.RequestException as e:
                log.warning("Retry failed for %s: %s", resource, e)
                break
        if resp.status_code != 200:
            log.warning("HTTP %s for %s — skipping chunk", resp.status_code, resource)
            break

        body = resp.json()
        batch = body.get("data", [])
        results.extend(batch)

        token = body.get("next_token")
        if not token:
            break
        params = {"next_token": token}

    return results


def date_chunks(start: str, end: str, days: int = 90) -> list[tuple[str, str]]:
    """Split a date range into chunks of `days` days."""
    chunks = []
    cur = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while cur <= end_d:
        chunk_end = min(cur + timedelta(days=days - 1), end_d)
        chunks.append((cur.isoformat(), chunk_end.isoformat()))
        cur = chunk_end + timedelta(days=1)
    return chunks


# ---------------------------------------------------------------------------
# sync_state helpers
# ---------------------------------------------------------------------------

def get_last_synced(conn, resource: str, default: str) -> str:
    row = conn.execute(
        "SELECT last_synced FROM sync_state WHERE resource = ?", (resource,)
    ).fetchone()
    return row[0] if row else default


def set_last_synced(conn, resource: str, last_synced: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sync_state (resource, last_synced) VALUES (?, ?)",
        (resource, last_synced),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Per-endpoint sync functions
# ---------------------------------------------------------------------------

def sync_daily_summaries(conn, headers: dict, start: str, end: str) -> None:
    """
    Merge daily_sleep, daily_readiness, daily_activity, daily_hrv, spo2,
    daily_stress, daily_resilience into oura_daily rows keyed by day.
    """
    log.info("Syncing daily summaries %s → %s", start, end)

    # Accumulate per-day data from multiple endpoints
    daily: dict[str, dict] = {}

    def merge(day: str, **kwargs) -> None:
        if day not in daily:
            daily[day] = {"id": f"daily-{day}", "day": day}
        daily[day].update({k: v for k, v in kwargs.items() if v is not None})

    for chunk_start, chunk_end in date_chunks(start, end, days=90):
        # daily_sleep
        for rec in fetch_all("daily_sleep", chunk_start, chunk_end, headers):
            day = rec.get("day", "")
            if not day:
                continue
            score = rec.get("score")
            contribs = rec.get("contributors", {})
            merge(day, sleep_score=score,
                  contributors_json=json.dumps({"sleep": contribs}))

        # daily_readiness
        for rec in fetch_all("daily_readiness", chunk_start, chunk_end, headers):
            day = rec.get("day", "")
            merge(day,
                  readiness_score=rec.get("score"),
                  temp_deviation=rec.get("temperature_deviation"),
                  resting_heart_rate=rec.get("contributors", {}).get("resting_heart_rate"))

        # daily_activity
        for rec in fetch_all("daily_activity", chunk_start, chunk_end, headers):
            day = rec.get("day", "")
            merge(day,
                  activity_score=rec.get("score"),
                  steps=rec.get("steps"),
                  active_calories=rec.get("active_calories"),
                  total_calories=rec.get("total_calories"))

        # daily_hrv
        for rec in fetch_all("daily_hrv", chunk_start, chunk_end, headers):
            day = rec.get("day", "")
            merge(day, avg_hrv_rmssd=rec.get("night_average"))

        # spo2
        for rec in fetch_all("spo2", chunk_start, chunk_end, headers):
            day = rec.get("day", "")
            spo2 = rec.get("spo2_percentage", {})
            merge(day,
                  spo2_avg=spo2.get("average"),
                  spo2_min=spo2.get("min"))

        # daily_stress (Gen3 — 404 handled in fetch_all)
        for rec in fetch_all("daily_stress", chunk_start, chunk_end, headers):
            day = rec.get("day", "")
            merge(day,
                  stress_high_seconds=rec.get("stress_high"),
                  recovery_high_seconds=rec.get("recovery_high"),
                  stress_day_summary=rec.get("day_summary"))

        # daily_resilience (Gen3 — 404 handled in fetch_all)
        for rec in fetch_all("daily_resilience", chunk_start, chunk_end, headers):
            day = rec.get("day", "")
            merge(day, resilience_level=rec.get("level"))

    # Upsert all accumulated days
    for row in daily.values():
        conn.execute(
            """INSERT OR REPLACE INTO oura_daily
                 (id, day, sleep_score, readiness_score, activity_score,
                  steps, active_calories, total_calories, avg_hrv_rmssd,
                  resting_heart_rate, temp_deviation, spo2_avg, spo2_min,
                  stress_high_seconds, recovery_high_seconds, stress_day_summary,
                  resilience_level, contributors_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.get("id"), row.get("day"),
                row.get("sleep_score"), row.get("readiness_score"), row.get("activity_score"),
                row.get("steps"), row.get("active_calories"), row.get("total_calories"),
                row.get("avg_hrv_rmssd"), row.get("resting_heart_rate"), row.get("temp_deviation"),
                row.get("spo2_avg"), row.get("spo2_min"),
                row.get("stress_high_seconds"), row.get("recovery_high_seconds"),
                row.get("stress_day_summary"), row.get("resilience_level"),
                row.get("contributors_json"),
            ),
        )
    conn.commit()
    log.info("Upserted %d daily summary rows", len(daily))
    set_last_synced(conn, "daily_summaries", end)


def sync_sleep_sessions(conn, headers: dict, start: str, end: str) -> None:
    """Sync detailed sleep sessions into oura_sleep_sessions."""
    log.info("Syncing sleep sessions %s → %s", start, end)
    count = 0

    for chunk_start, chunk_end in date_chunks(start, end, days=30):
        for rec in fetch_all("sleep", chunk_start, chunk_end, headers):
            conn.execute(
                """INSERT OR REPLACE INTO oura_sleep_sessions
                     (id, day, type, bedtime_start, bedtime_end,
                      total_sleep_sec, deep_sleep_sec, light_sleep_sec,
                      rem_sleep_sec, awake_sec, efficiency, latency_sec,
                      avg_hrv, avg_heart_rate, lowest_heart_rate,
                      hr_5min, hrv_5min, sleep_phase_5min)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec.get("id"), rec.get("day"), rec.get("type"),
                    rec.get("bedtime_start"), rec.get("bedtime_end"),
                    rec.get("total_sleep_duration"), rec.get("deep_sleep_duration"),
                    rec.get("light_sleep_duration"), rec.get("rem_sleep_duration"),
                    rec.get("awake_time"), rec.get("efficiency"), rec.get("latency"),
                    rec.get("average_hrv"), rec.get("average_heart_rate"),
                    rec.get("lowest_heart_rate"),
                    json.dumps(rec.get("hr_5_min")) if rec.get("hr_5_min") else None,
                    json.dumps(rec.get("hrv_5_min")) if rec.get("hrv_5_min") else None,
                    rec.get("sleep_phase_5_min"),
                ),
            )
            count += 1

    conn.commit()
    log.info("Upserted %d sleep session rows", count)
    set_last_synced(conn, "sleep", end)


def sync_heartrate(conn, headers: dict, start: str, end: str) -> None:
    """Sync intraday heart rate stream into oura_heartrate (7-day chunks)."""
    log.info("Syncing heart rate stream %s → %s", start, end)
    count = 0

    for chunk_start, chunk_end in date_chunks(start, end, days=7):
        for rec in fetch_all("heartrate", chunk_start, chunk_end, headers):
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO oura_heartrate (timestamp, bpm, source) VALUES (?, ?, ?)",
                    (rec.get("timestamp"), rec.get("bpm"), rec.get("source")),
                )
                count += 1
            except Exception as e:
                log.warning("Skipping heartrate row: %s", e)

    conn.commit()
    log.info("Upserted %d heart rate rows", count)
    set_last_synced(conn, "heartrate", end)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Oura Ring data to health.db")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--historical", action="store_true",
                       help=f"Full import from {HISTORICAL_START} to today")
    group.add_argument("--since", metavar="YYYY-MM-DD",
                       help="Import from this date to today")
    args = parser.parse_args()

    token = load_token()
    headers = {"Authorization": f"Bearer {token}"}
    today = date.today().isoformat()

    conn = health_db.get_connection()

    if args.historical:
        start = HISTORICAL_START
        log.info("Historical import from %s", start)
    elif args.since:
        start = args.since
        log.info("Import from %s (--since)", start)
    else:
        # Incremental: use per-resource sync_state
        start = None
        log.info("Incremental sync using sync_state table")

    # Sync each group
    for sync_fn, resource_key in [
        (sync_daily_summaries, "daily_summaries"),
        (sync_sleep_sessions, "sleep"),
        (sync_heartrate, "heartrate"),
    ]:
        resource_start = start or get_last_synced(conn, resource_key, DEFAULT_START)
        if resource_start >= today:
            log.info("%s already up to date (%s)", resource_key, resource_start)
            continue
        try:
            sync_fn(conn, headers, resource_start, today)
        except Exception as e:
            log.error("Failed syncing %s: %s", resource_key, e)

    conn.close()
    log.info("Oura sync complete.")


if __name__ == "__main__":
    main()
