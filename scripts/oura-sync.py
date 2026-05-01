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

# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class FetchError(Exception):
    pass


# ---------------------------------------------------------------------------
# Single-instance lock guard (mkdir is atomic on macOS; flock unavailable)
# ---------------------------------------------------------------------------

_LOCK_DIR = os.path.expanduser("~/Library/Logs/ironclaw/.oura-sync.lock")

_HEARTRATE_RETENTION_DAYS = 90


def _acquire_lock():
    os.makedirs(os.path.dirname(_LOCK_DIR), exist_ok=True)
    try:
        os.mkdir(_LOCK_DIR)
    except OSError:
        print("oura-sync: another instance is running, exiting", file=sys.stderr)
        sys.exit(0)


def _release_lock():
    try:
        os.rmdir(_LOCK_DIR)
    except OSError:
        pass


# health_db lives in workspace/health/
_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
sys.path.insert(0, str(_HEALTH_DIR))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import health_db
from keychain import kc_get

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("oura-sync")

OURA_BASE = "https://api.ouraring.com/v2/usercollection"
HISTORICAL_START = "2015-01-01"
DEFAULT_START = "2024-01-01"

# Re-fetch the last N days on every incremental sync so rate-limit gaps
# and retroactive Oura corrections are healed automatically.
OVERLAP_DAYS = 1


# ---------------------------------------------------------------------------
# .env / token loading
# ---------------------------------------------------------------------------

def load_token() -> str:
    """Load Oura PAT from Keychain, falling back to environment/.env."""
    # Keychain (preferred)
    token = kc_get("com.ironclaw.oura", "access_token")
    if token:
        return token

    # Environment variable fallback
    token = os.environ.get("OURA_PERSONAL_ACCESS_TOKEN", "")
    if token:
        return token

    # .env fallback (pre-migration)
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

    print("Error: Oura token not found in Keychain or .env — run scripts/migrate-secrets-to-keychain.py",
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
            raise FetchError(f"network error fetching {resource}: {e}") from e

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
                raise FetchError(f"network error fetching {resource}: {e}") from e
        if resp.status_code != 200:
            log.warning("HTTP %s for %s — skipping chunk", resp.status_code, resource)
            raise FetchError(f"HTTP {resp.status_code} for {resource} chunk {params.get('start_date', '?')}–{params.get('end_date', '?')}")

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
# Heartrate retention cleanup
# ---------------------------------------------------------------------------

def cleanup_old_heartrate(conn):
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=_HEARTRATE_RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM oura_heartrate WHERE timestamp < ?", (cutoff,))
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

    last_good_date = None

    for chunk_start, chunk_end in date_chunks(start, end, days=90):
        # Accumulate per-day data from multiple endpoints for this chunk only
        daily: dict[str, dict] = {}

        def merge(day: str, **kwargs) -> None:
            if day not in daily:
                daily[day] = {"id": f"daily-{day}", "day": day}
            daily[day].update({k: v for k, v in kwargs.items() if v is not None})

        try:
            # daily_sleep
            for rec in fetch_all("daily_sleep", chunk_start, chunk_end, headers):
                day = rec.get("day", "")
                if not day:
                    continue
                score = rec.get("score")
                contribs = rec.get("contributors") or {}
                merge(day, sleep_score=score,
                      contributors_json=json.dumps({"sleep": contribs}))

            # daily_readiness
            for rec in fetch_all("daily_readiness", chunk_start, chunk_end, headers):
                day = rec.get("day", "")
                merge(day,
                      readiness_score=rec.get("score"),
                      temp_deviation=rec.get("temperature_deviation"),
                      resting_heart_rate=(rec.get("contributors") or {}).get("resting_heart_rate"))

            # daily_activity
            for rec in fetch_all("daily_activity", chunk_start, chunk_end, headers):
                day = rec.get("day", "")
                merge(day,
                      activity_score=rec.get("score"),
                      steps=rec.get("steps"),
                      active_calories=rec.get("active_calories"),
                      total_calories=rec.get("total_calories"))

            # daily_spo2 (note: daily_hrv endpoint doesn't exist in v2 API;
            # HRV is already captured via sleep sessions avg_hrv field)
            for rec in fetch_all("daily_spo2", chunk_start, chunk_end, headers):
                day = rec.get("day", "")
                spo2 = rec.get("spo2_percentage") or {}
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

        except FetchError as e:
            log.warning("oura fetch failed at chunk %s–%s: %s", chunk_start, chunk_end, e)
            break

        # Upsert this chunk's accumulated days and mark as confirmed
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
        log.info("Upserted %d daily summary rows for chunk %s–%s", len(daily), chunk_start, chunk_end)
        last_good_date = chunk_end

    if last_good_date:
        safe_date = (date.fromisoformat(last_good_date) - timedelta(days=OVERLAP_DAYS)).isoformat()
        health_db.set_last_synced(conn, "daily_summaries", safe_date)
    # If last_good_date is None: no chunks succeeded; don't advance last_synced


def sync_sleep_sessions(conn, headers: dict, start: str, end: str) -> None:
    """Sync detailed sleep sessions into oura_sleep_sessions."""
    log.info("Syncing sleep sessions %s → %s", start, end)
    total_count = 0
    last_good_date = None

    for chunk_start, chunk_end in date_chunks(start, end, days=30):
        chunk_count = 0
        try:
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
                chunk_count += 1
        except FetchError as e:
            log.warning("oura fetch failed at chunk %s–%s: %s", chunk_start, chunk_end, e)
            break

        conn.commit()
        total_count += chunk_count
        last_good_date = chunk_end

    log.info("Upserted %d sleep session rows", total_count)
    if last_good_date:
        health_db.backfill_daily_hrv(conn)
        log.info("Refreshed avg_hrv_rmssd from sleep sessions")
        safe_date = (date.fromisoformat(last_good_date) - timedelta(days=OVERLAP_DAYS)).isoformat()
        health_db.set_last_synced(conn, "sleep", safe_date)
    # If last_good_date is None: no chunks succeeded; don't advance last_synced


def sync_heartrate(conn, headers: dict, start: str, end: str) -> None:
    """Sync intraday heart rate stream into oura_heartrate (7-day chunks)."""
    log.info("Syncing heart rate stream %s → %s", start, end)
    total_count = 0
    last_good_date = None

    for chunk_start, chunk_end in date_chunks(start, end, days=7):
        chunk_count = 0
        try:
            for rec in fetch_all("heartrate", chunk_start, chunk_end, headers):
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO oura_heartrate (timestamp, bpm, source) VALUES (?, ?, ?)",
                        (rec.get("timestamp"), rec.get("bpm"), rec.get("source")),
                    )
                    chunk_count += 1
                except Exception as e:
                    log.warning("Skipping heartrate row: %s", e)
        except FetchError as e:
            log.warning("oura fetch failed at chunk %s–%s: %s", chunk_start, chunk_end, e)
            break

        conn.commit()
        total_count += chunk_count
        last_good_date = chunk_end

    log.info("Upserted %d heart rate rows", total_count)
    if last_good_date:
        safe_date = (date.fromisoformat(last_good_date) - timedelta(days=OVERLAP_DAYS)).isoformat()
        health_db.set_last_synced(conn, "heartrate", safe_date)
    # If last_good_date is None: no chunks succeeded; don't advance last_synced
    cleanup_old_heartrate(conn)


def sync_tags(conn, headers: dict, start: str, end: str) -> None:
    """Sync Oura enhanced tags into oura_tags table."""
    log.info("Syncing Oura tags %s → %s", start, end)
    total_count = 0
    last_good_date = None

    for chunk_start, chunk_end in date_chunks(start, end, days=90):
        chunk_count = 0
        try:
            for rec in fetch_all("enhanced_tag", chunk_start, chunk_end, headers):
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO oura_tags
                             (id, day, tag_type, start_time, end_time, comment)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (rec.get("id"), rec.get("start_day") or rec.get("day"),
                         rec.get("tag_type_code") or rec.get("tag_type"),
                         rec.get("start_time"), rec.get("end_time"),
                         rec.get("comment")),
                    )
                    chunk_count += 1
                except Exception as e:
                    log.warning("Skipping tag row: %s", e)
        except FetchError as e:
            log.warning("oura fetch failed at chunk %s–%s: %s", chunk_start, chunk_end, e)
            break

        conn.commit()
        total_count += chunk_count
        last_good_date = chunk_end

    log.info("Upserted %d tag rows", total_count)
    if last_good_date:
        safe_date = (date.fromisoformat(last_good_date) - timedelta(days=OVERLAP_DAYS)).isoformat()
        health_db.set_last_synced(conn, "oura_tags", safe_date)
    # If last_good_date is None: no chunks succeeded; don't advance last_synced


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
        (sync_tags, "tags"),
    ]:
        resource_start = start or health_db.get_last_synced(conn, resource_key, DEFAULT_START)
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
    _acquire_lock()
    try:
        main()
    finally:
        _release_lock()
