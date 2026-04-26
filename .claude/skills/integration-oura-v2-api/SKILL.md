---
name: integration-oura-v2-api
description: Use when working on oura-sync.py, adding new Oura endpoints, debugging Oura sync failures, or planning Oura data integration
user-invocable: false
---

# Oura Ring v2 API Integration

**Trigger**: Oura, oura API, oura-sync, daily_sleep, heartrate, daily_spo2, personal access token, oura sync, sync_state
**Confidence**: high
**Created**: 2026-04-26
**Updated**: 2026-04-26
**Version**: 1

## Auth

Personal Access Token — generate at https://cloud.ouraring.com/personal-access-tokens  
Store as `OURA_PERSONAL_ACCESS_TOKEN` in `agents/sample-agent/.env`. Never commit or post in chat.

```python
headers = {"Authorization": f"Bearer {token}"}
```

## Endpoint Name Gotchas (Costly Mistakes)

| Wrong | Correct | Notes |
|---|---|---|
| `spo2` | `daily_spo2` | SpO2 daily summary |
| `daily_hrv` | *doesn't exist* | Returns 404 for ALL accounts — HRV is in `sleep` sessions as `average_hrv` |

`daily_hrv` is not a v2 API endpoint. Use `oura_sleep_sessions.avg_hrv` instead.

## Heartrate API Limitation

**`heartrate` returns only recent data (~2-3 days) regardless of the date range requested.**

A historical import will inflate the INSERT counter (each 7-day chunk "inserts" the same recent rows via `INSERT OR REPLACE`) but the DB will only contain recent data. This is an Oura API limitation, not a code bug.

## Gen3-Only Endpoints

These return **404 on Gen2 rings**. Handle gracefully — do not fail the whole sync:
- `daily_stress`
- `daily_resilience`  
- `vo2_max`

```python
if resp.status_code == 404:
    log.warning("Endpoint %s returned 404 — skipping", resource)
    return []
```

## Pagination Pattern

```python
def fetch_all(resource, start_date, end_date, headers):
    url = f"https://api.ouraring.com/v2/usercollection/{resource}"
    params = {"start_date": start_date, "end_date": end_date}
    results = []
    while True:
        resp = requests.get(url, headers=headers, params=params)
        body = resp.json()
        results.extend(body.get("data", []))
        token = body.get("next_token")
        if not token:
            break
        params = {"next_token": token}  # replaces date params on next request
    return results
```

## Nullable API Fields

`contributors` and `spo2_percentage` can be present in the response but with value `null`. `.get("field", {})` does NOT guard against this:

```python
# WRONG — fails when key exists but value is null
contribs = rec.get("contributors", {}).get("resting_heart_rate")

# CORRECT
contribs = (rec.get("contributors") or {}).get("resting_heart_rate")
spo2 = rec.get("spo2_percentage") or {}
```

## Chunking Strategy

| Endpoint | Chunk size | Reason |
|---|---|---|
| Daily summary endpoints | 90 days | API default window |
| `sleep` sessions | 30 days | More data per day |
| `heartrate` | 7 days | High-volume intraday stream |

## Historical Import

Oura account history only goes back to when the ring was first set up. A gap between app history and API history (e.g., Jan–Jun 2016 missing) is normal — older data from early ring generations may not be exposed through v2.

The full historical import takes ~9 minutes for a 10-year Oura account.

```bash
# Test connectivity first
python3.13 scripts/oura-sync.py --since 2026-04-01

# Full history
python3.13 scripts/oura-sync.py --historical

# Incremental (uses sync_state table — default)
python3.13 scripts/oura-sync.py
```

## Known Issues in Current Implementation

- Oura sync runs weekly (Mon 3am) but daily is more appropriate for real-time health answers — change `StartCalendarInterval` to `StartInterval: 86400`
- Sync advances `last_synced` even on partial failure — add `OVERLAP_DAYS = 3` re-fetch window
- `/tmp/oura-sync.log` is purged on reboot — use `~/Library/Logs/ironclaw/oura-sync.log`
