#!/usr/bin/env python3
"""
Withings Health Mate API sync — imports body composition into health.db.

Usage:
  python3 scripts/withings-sync.py                    # incremental (use sync_state)
  python3 scripts/withings-sync.py --historical       # full import from 2019-01-01
  python3 scripts/withings-sync.py --since 2025-01-01 # manual start date

Requires Withings credentials in agents/sample-agent/.env.
Run scripts/withings-auth.py once to obtain initial tokens.

Withings OAuth2 quirks:
  - Token URL: https://wbsapi.withings.net/v2/oauth2 (not /token)
  - action=requesttoken required in POST body
  - client_id/secret in body (not Basic Auth header)
  - Access tokens expire in ~3h; this script refreshes proactively
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).parent.parent
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_HEALTH_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR))
import health_db
from keychain import kc_get, kc_set, kc_require

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("withings-sync")

MEASURE_URL = "https://wbsapi.withings.net/measure"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
HISTORICAL_START = "2019-01-01"
DEFAULT_START = "2024-01-01"
KG_TO_LBS = 2.20462

# Withings meastype codes
MTYPE_WEIGHT = 1       # kg
MTYPE_LEAN_MASS = 5    # kg
MTYPE_FAT_RATIO = 6    # percent (no conversion needed)
MTYPE_FAT_MASS = 8     # kg
MTYPE_MUSCLE_MASS = 76 # kg

MEASTYPE_LIST = f"{MTYPE_WEIGHT},{MTYPE_LEAN_MASS},{MTYPE_FAT_RATIO},{MTYPE_FAT_MASS},{MTYPE_MUSCLE_MASS}"


# ---------------------------------------------------------------------------
# Auth / token management (Keychain)
# ---------------------------------------------------------------------------

_KC = "com.ironclaw.withings"


def load_credentials() -> dict[str, str]:
    """Load Withings credentials from Keychain."""
    return {
        "client_id":     kc_require(_KC, "client_id",     "run scripts/withings-auth.py"),
        "client_secret": kc_require(_KC, "client_secret",  "run scripts/withings-auth.py"),
        "access_token":  kc_require(_KC, "access_token",   "run scripts/withings-auth.py"),
        "refresh_token": kc_require(_KC, "refresh_token",  "run scripts/withings-auth.py"),
        "token_expiry":  kc_require(_KC, "token_expiry",   "run scripts/withings-auth.py"),
    }


def refresh_if_needed(creds: dict[str, str]) -> dict[str, str]:
    """Refresh access token if it expires within the next 5 minutes."""
    expiry = int(creds.get("token_expiry", "0"))
    if time.time() < expiry - 300:
        return creds  # still fresh

    log.info("Access token expiring soon — refreshing...")
    try:
        resp = requests.post(TOKEN_URL, data={
            "action": "requesttoken",
            "grant_type": "refresh_token",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "refresh_token": creds["refresh_token"],
        }, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        log.error("Token refresh failed: %s", e)
        sys.exit(1)

    if payload.get("status") != 0:
        log.error("Token refresh returned status %s: %s", payload.get("status"), payload)
        sys.exit(1)

    body = payload["body"]
    new_expiry = int(time.time()) + int(body.get("expires_in", 10800))
    kc_set(_KC, "access_token",  body["access_token"])
    kc_set(_KC, "refresh_token", body["refresh_token"])
    kc_set(_KC, "token_expiry",  str(new_expiry))
    creds.update({"access_token": body["access_token"],
                  "refresh_token": body["refresh_token"],
                  "token_expiry": str(new_expiry)})
    log.info("Token refreshed; new expiry in %dh", int(body.get("expires_in", 10800)) // 3600)
    return creds


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
# Withings API
# ---------------------------------------------------------------------------

def _iso_to_unix(iso_date: str) -> int:
    """Convert YYYY-MM-DD to UTC midnight Unix timestamp."""
    dt = datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _decode_value(value: int, unit: int) -> float:
    """Decode Withings measure value: real = value * 10^unit."""
    return value * (10 ** unit)


def fetch_measures(access_token: str, start: str, end: str) -> list[dict]:
    """
    Fetch all body composition measure groups for a date range.
    Returns list of decoded measure-group dicts, one per weigh-in session.
    Withings paginates via more/offset.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "action": "getmeas",
        "meastype": MEASTYPE_LIST,
        "category": 1,  # real measurements (not user objectives)
        "startdate": _iso_to_unix(start),
        "enddate": _iso_to_unix(end) + 86399,  # end of day
    }
    groups = []
    offset = 0

    while True:
        if offset:
            params["offset"] = offset

        try:
            resp = requests.post(MEASURE_URL, headers=headers, data=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("Network error fetching measures: %s", e)
            break

        payload = resp.json()
        if payload.get("status") != 0:
            log.warning("Withings API status %s — skipping chunk", payload.get("status"))
            break

        body = payload["body"]
        for grp in body.get("measuregrps", []):
            decoded: dict = {
                "grpid": grp["grpid"],
                "date": datetime.fromtimestamp(grp["date"], tz=timezone.utc).strftime("%Y-%m-%d"),
                "time": datetime.fromtimestamp(grp["date"], tz=timezone.utc).strftime("%H:%M"),
            }
            for m in grp.get("measures", []):
                raw = _decode_value(m["value"], m["unit"])
                mtype = m["type"]
                if mtype == MTYPE_WEIGHT:
                    decoded["weight_lbs"] = round(raw * KG_TO_LBS, 2)
                elif mtype == MTYPE_LEAN_MASS:
                    decoded["lean_mass_lbs"] = round(raw * KG_TO_LBS, 2)
                elif mtype == MTYPE_FAT_RATIO:
                    decoded["fat_ratio_pct"] = round(raw, 2)
                elif mtype == MTYPE_FAT_MASS:
                    decoded["fat_mass_lbs"] = round(raw * KG_TO_LBS, 2)
                elif mtype == MTYPE_MUSCLE_MASS:
                    decoded["muscle_mass_lbs"] = round(raw * KG_TO_LBS, 2)
            groups.append(decoded)

        if not body.get("more"):
            break
        offset = body.get("offset", 0)

    return groups


# ---------------------------------------------------------------------------
# Sync function
# ---------------------------------------------------------------------------

def sync_body_metrics(conn, access_token: str, start: str, end: str) -> None:
    log.info("Syncing body metrics %s → %s", start, end)
    inserted = skipped = 0

    for chunk_start, chunk_end in date_chunks(start, end, days=90):
        groups = fetch_measures(access_token, chunk_start, chunk_end)
        for g in groups:
            cursor = conn.execute(
                """
                INSERT INTO body_metrics
                    (date, time, weight_lbs, fat_ratio_pct, fat_mass_lbs,
                     lean_mass_lbs, muscle_mass_lbs, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'withings_api')
                ON CONFLICT(date, time) DO UPDATE SET
                    weight_lbs      = excluded.weight_lbs,
                    fat_ratio_pct   = excluded.fat_ratio_pct,
                    fat_mass_lbs    = excluded.fat_mass_lbs,
                    lean_mass_lbs   = excluded.lean_mass_lbs,
                    muscle_mass_lbs = excluded.muscle_mass_lbs,
                    source          = excluded.source
                WHERE body_metrics.weight_lbs      IS NOT excluded.weight_lbs
                   OR body_metrics.fat_ratio_pct   IS NOT excluded.fat_ratio_pct
                   OR body_metrics.fat_mass_lbs    IS NOT excluded.fat_mass_lbs
                   OR body_metrics.lean_mass_lbs   IS NOT excluded.lean_mass_lbs
                   OR body_metrics.muscle_mass_lbs IS NOT excluded.muscle_mass_lbs
                """,
                (
                    g.get("date"), g.get("time"),
                    g.get("weight_lbs"), g.get("fat_ratio_pct"), g.get("fat_mass_lbs"),
                    g.get("lean_mass_lbs"), g.get("muscle_mass_lbs"),
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
            else:
                skipped += 1

        conn.commit()
        log.info("Chunk %s → %s: %d groups fetched", chunk_start, chunk_end, len(groups))

    log.info("Body metrics: %d inserted/updated, %d skipped (unchanged)", inserted, skipped)
    health_db.set_last_synced(conn, "withings_body", end)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Withings body composition to health.db")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--historical", action="store_true",
                       help=f"Full import from {HISTORICAL_START} to today")
    group.add_argument("--since", metavar="YYYY-MM-DD",
                       help="Import from this date to today")
    args = parser.parse_args()

    creds = load_credentials()
    creds = refresh_if_needed(creds)
    access_token = creds["access_token"]
    today = date.today().isoformat()

    conn = health_db.get_connection()

    if args.historical:
        start = HISTORICAL_START
        log.info("Historical import from %s", start)
    elif args.since:
        start = args.since
        log.info("Import from %s (--since)", start)
    else:
        start = health_db.get_last_synced(conn, "withings_body", DEFAULT_START)
        log.info("Incremental sync from %s (sync_state)", start)

    if start >= today:
        log.info("Already up to date (%s)", start)
        conn.close()
        return

    try:
        sync_body_metrics(conn, access_token, start, today)
    except Exception as e:
        log.error("Sync failed: %s", e)
        conn.close()
        sys.exit(1)

    conn.close()
    log.info("Withings sync complete.")


if __name__ == "__main__":
    main()
