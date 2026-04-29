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
_ENV_PATH = _REPO_ROOT / "agents/sample-agent/.env"
sys.path.insert(0, str(_HEALTH_DIR))
import health_db

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
# .env helpers
# ---------------------------------------------------------------------------

def _read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not _ENV_PATH.exists():
        return env
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _write_env_keys(updates: dict[str, str]) -> None:
    """Overwrite specific keys in .env without touching other lines."""
    lines = _ENV_PATH.read_text().splitlines() if _ENV_PATH.exists() else []
    written: set[str] = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            new_lines.append(line)
    for key, val in updates.items():
        if key not in written:
            new_lines.append(f"{key}={val}")
    _ENV_PATH.write_text("\n".join(new_lines) + "\n")


# ---------------------------------------------------------------------------
# Auth / token management
# ---------------------------------------------------------------------------

def load_credentials() -> dict[str, str]:
    """Load all Withings credentials from environment or .env file."""
    env = _read_env()
    required = ["WITHINGS_CLIENT_ID", "WITHINGS_CLIENT_SECRET",
                "WITHINGS_ACCESS_TOKEN", "WITHINGS_REFRESH_TOKEN",
                "WITHINGS_TOKEN_EXPIRY"]
    creds: dict[str, str] = {}
    for key in required:
        val = os.environ.get(key) or env.get(key, "")
        if not val:
            print(f"Error: {key} not found. Run scripts/withings-auth.py first.",
                  file=sys.stderr)
            sys.exit(1)
        creds[key] = val
    return creds


def refresh_if_needed(creds: dict[str, str]) -> dict[str, str]:
    """Refresh access token if it expires within the next 5 minutes."""
    expiry = int(creds.get("WITHINGS_TOKEN_EXPIRY", "0"))
    if time.time() < expiry - 300:
        return creds  # still fresh

    log.info("Access token expiring soon — refreshing...")
    try:
        resp = requests.post(TOKEN_URL, data={
            "action": "requesttoken",
            "grant_type": "refresh_token",
            "client_id": creds["WITHINGS_CLIENT_ID"],
            "client_secret": creds["WITHINGS_CLIENT_SECRET"],
            "refresh_token": creds["WITHINGS_REFRESH_TOKEN"],
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
    updates = {
        "WITHINGS_ACCESS_TOKEN": body["access_token"],
        "WITHINGS_REFRESH_TOKEN": body["refresh_token"],
        "WITHINGS_TOKEN_EXPIRY": str(new_expiry),
    }
    _write_env_keys(updates)
    creds.update(updates)
    log.info("Token refreshed; new expiry in %dh", int(body.get("expires_in", 10800)) // 3600)
    return creds


# ---------------------------------------------------------------------------
# sync_state helpers (mirrors oura-sync.py exactly)
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
                    muscle_mass_lbs = excluded.muscle_mass_lbs
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
    set_last_synced(conn, "withings_body", end)


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
    access_token = creds["WITHINGS_ACCESS_TOKEN"]
    today = date.today().isoformat()

    conn = health_db.get_connection()

    if args.historical:
        start = HISTORICAL_START
        log.info("Historical import from %s", start)
    elif args.since:
        start = args.since
        log.info("Import from %s (--since)", start)
    else:
        start = get_last_synced(conn, "withings_body", DEFAULT_START)
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
