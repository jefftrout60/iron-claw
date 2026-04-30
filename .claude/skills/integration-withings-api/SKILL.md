---
name: integration-withings-api
description: Use when working on withings-sync.py, adding Withings body composition data, debugging OAuth token refresh, or understanding the Withings measure API response format.
user-invocable: false
---

# Withings Health Mate API Integration

**Trigger**: withings, withings-sync, body composition, OAuth2, health mate, withings auth
**Confidence**: high
**Created**: 2026-04-29
**Updated**: 2026-04-29
**Version**: 1

## What it is

Withings Health Mate API provides body composition data (weight, fat mass, lean mass, fat ratio, muscle mass) from the Withings smart scale. We use it as the authoritative source for body composition in health.db — Withings data takes priority over Apple Health data in the body_metrics table.

Sync script: `scripts/withings-sync.py` (models oura-sync.py exactly)

## OAuth2 — Non-Standard Quirks (Critical)

**This is NOT standard OAuth2.** Three gotchas that will break standard OAuth libraries:

1. **Token endpoint**: `https://wbsapi.withings.net/v2/oauth2` — NOT the standard `/token` path
2. **Required body param**: `action=requesttoken` must be in every POST body (authorization code exchange AND refresh)
3. **Credentials in body**: `client_id` and `client_secret` go in the POST body, NOT in Basic Auth header

```python
# Correct token exchange / refresh
resp = requests.post("https://wbsapi.withings.net/v2/oauth2", data={
    "action": "requesttoken",
    "grant_type": "authorization_code",  # or "refresh_token"
    "client_id": client_id,
    "client_secret": client_secret,
    "code": code,             # authorization code exchange
    # "refresh_token": token, # token refresh
    "redirect_uri": REDIRECT_URI,
})
```

**Access token expires in ~3 hours.** Refresh proactively when expiry < now + 5min.

**Developer app registration**: Use `http://localhost:8080/callback` as redirect URI. Withings allows localhost for "integration phase" apps (10-user limit — fine for personal use). Register at `developer.withings.com`.

One-time setup script: `scripts/withings-auth.py` — starts local HTTP server, opens browser for OAuth, exchanges code for tokens, writes to `.env`.

**.env vars written by withings-auth.py:**
```
WITHINGS_CLIENT_ID=...
WITHINGS_CLIENT_SECRET=...
WITHINGS_ACCESS_TOKEN=...
WITHINGS_REFRESH_TOKEN=...
WITHINGS_TOKEN_EXPIRY=...   # Unix timestamp
```

**macOS SSL gotcha**: `urllib.request` fails SSL cert verification on macOS. Use `requests` library instead — it handles macOS system certs correctly.

## Measure API

**Endpoint**: `POST https://wbsapi.withings.net/measure`

**Key params**:
```python
{
    "action": "getmeas",
    "meastype": "1,5,6,8,76",  # comma-separated list
    "category": 1,              # real measurements (not user goals)
    "startdate": unix_timestamp,
    "enddate": unix_timestamp + 86399,  # inclusive end of day
}
```

**Meastype codes for body composition:**
| meastype | Metric | Unit |
|----------|--------|------|
| 1 | Weight | kg |
| 5 | Lean body mass | kg |
| 6 | Fat ratio | % |
| 8 | Fat mass weight | kg |
| 76 | Muscle mass | kg |

**Value decoding** — each measure has `value` and `unit`:
```python
real_value = value * (10 ** unit)  # e.g. value=705, unit=-1 → 70.5 kg
```

**Pagination**: response has `more` (0 or 1) and `offset`. Loop until `more == 0`, passing `offset` on next call.

**Response structure**:
```json
{
  "status": 0,
  "body": {
    "measuregrps": [
      {
        "grpid": 12345,
        "date": 1735689600,
        "measures": [
          {"value": 1045, "type": 1, "unit": -1}
        ]
      }
    ],
    "more": 0,
    "offset": 0
  }
}
```

## Storage

All values converted to lbs on import (`kg * 2.20462`). Fat ratio stored as percentage directly (no conversion needed).

**Source priority in body_metrics table**: `withings_api` rows are never overwritten by `apple_health` rows. Enforced via WHERE guard on ON CONFLICT DO UPDATE:
```sql
ON CONFLICT(date, time) DO UPDATE SET weight_lbs = excluded.weight_lbs, ...
WHERE body_metrics.source IS 'apple_health'  -- only overwrite apple_health rows
```

## Sync State

Resource key: `"withings_body"` in `sync_state` table. Same `get_last_synced()` / `set_last_synced()` pattern as oura-sync.py.

## Key Files

- `scripts/withings-auth.py` — one-time OAuth setup
- `scripts/withings-sync.py` — daily cron (model: oura-sync.py)
- `agents/sample-agent/workspace/health/health_db.py` — body_metrics table schema
- `agents/sample-agent/workspace/health/test_withings_sync.py` — unit tests for value decode, upsert
