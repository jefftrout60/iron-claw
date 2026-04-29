# Implementation Plan: Apple Health Multi-Source Health Data Import
Branch: main | Date: 2026-04-29 | Depth: comprehensive

---

## Overview

Consolidate 4 personal health data sources into `health.db` (SQLite) to enable cross-pillar LLM correlation queries via iMessage. Target correlations: HRV vs. workout intensity, BP vs. sleep quality, body fat% trend vs. BP trend, sauna tag vs. recovery metrics.

**3-session delivery (Option A — source-by-source):**
- **Session A**: DB migration framework + Withings API sync → `body-metrics` iMessage query
- **Session B**: Apple Health XML import (all categories) → `activity` + `workouts` iMessage queries
- **Session C**: Evernote workout detail + Oura tags → `workout-detail` query + SKILL/AGENTS wiring for all new types

Each session ships end-to-end value independently.

---

## Current State

**Schema** (`health_db.py:54-269`):
- Tables: `blood_pressure`, `oura_daily`, `oura_sleep_sessions`, `oura_heartrate`, `sync_state`, `lab_markers`, `lab_results`, `health_knowledge`, `health_knowledge_fts`
- No body composition, activity, workout, or tags data
- No PRAGMA user_version migration framework (arch review flagged as required before next schema change)
- PRAGMAs: WAL, synchronous=NORMAL, foreign_keys=ON, cache_size=-32768, busy_timeout=5000

**Query layer** (`health_query.py:286-330`):
- Subcommands: `lab-trend`, `oura-window`, `blood-pressure`, `bp-log`, `search`
- All output is JSON; outer try/except always produces valid JSON

**Sync infrastructure** (`oura-sync.py`):
- Pattern: load token from env → paginate API → upsert SQLite → update sync_state → launchd cron
- `sync_state` table already exists; resource keys are plain strings

**Enforcement layer** (`AGENTS.md:68-120`, `SKILL.md`):
- Rule 6b: forbidden phrases + mandatory exec blocks per data type
- Rule 6c: two-turn BP entry flow
- Intent Classification table + per-intent STEP blocks in SKILL.md

---

## Desired End State

### New Tables in health.db

```sql
-- body_metrics: one row per Withings weigh-in or Apple Health body measurement
body_metrics (id, date, time, weight_lbs, fat_ratio_pct, fat_mass_lbs, lean_mass_lbs, muscle_mass_lbs, source, fetched_at)
UNIQUE(date, time)

-- activity_daily: one aggregated row per day
activity_daily (id, date, steps, daylight_minutes, source, fetched_at)
UNIQUE(date)

-- workouts: one row per Apple Watch workout session
workouts (id, date, start_time, end_time, workout_type, duration_min, calories, avg_hr, max_hr, effort_rating, source, notes, fetched_at)
UNIQUE(date, start_time, workout_type)

-- workout_exercises: sets/reps detail from Evernote, linked to workouts
workout_exercises (id, workout_id [FK workouts], workout_date, exercise_name, set_number, reps, weight_lbs, notes)
INDEX(workout_date)

-- oura_tags: enhanced tags from Oura API
oura_tags (id [TEXT PK, Oura-assigned], day, tag_type, start_time, end_time, comment, fetched_at)
INDEX(day)
```

### New Scripts
- `scripts/withings-sync.py` — OAuth2 cron, body composition → body_metrics
- `scripts/import-apple-health.py` — XML streaming parser, all categories → body_metrics + activity_daily + workouts
- `scripts/import-evernote-workouts.py` — ENEX parser, exercise detail → workout_exercises

### Modified Files
- `health_db.py` — PRAGMA user_version framework + 5 new tables
- `health_query.py` — 4 new subcommands: `body-metrics`, `activity`, `workouts`, `workout-exercises`
- `oura-sync.py` — add enhanced_tag sync function
- `AGENTS.md` — Rule 6b extensions for each new query type
- `SKILL.md` — new intents for each new data type

### iMessage Queries Enabled
```
"what's my weight this month?"
"show body fat trend last 90 days"
"how many steps this week?"
"workouts last 2 weeks"
"what did I do at the gym Tuesday?"
"show my sauna days this month"
```

---

## Out of Scope

- Pre-2025 Evernote backfill (future session)
- Cross-pillar correlation skill (next session after all data is in)
- Weekly email including new data types
- iMessage manual weight logging (replaced by Withings API automation)
- iOS Shortcut for ongoing Apple Health sync (manual export for now; Shortcut is a future polish item)
- DEXA scan data
- BP Omron sync reliability fix

---

## Technical Approach

### Foundation: PRAGMA user_version (Session A, first task)

Add migration framework to `health_db.py::initialize_schema()` before adding any new tables. This resolves the arch review requirement and creates a safe pattern for all future schema changes.

```python
SCHEMA_VERSION = 2

def initialize_schema(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        # Version 1: all existing tables (blood_pressure, oura_*, lab_*, health_knowledge)
        # Already created via IF NOT EXISTS — just stamp the version
        conn.execute(f"PRAGMA user_version = 1")
        conn.commit()
    if version < 2:
        # Version 2: body_metrics, activity_daily, workouts, workout_exercises, oura_tags
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS body_metrics (...);
            ...
        """)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    # existing IF NOT EXISTS blocks unchanged — idempotent on fresh DBs
```

**Key**: existing DBs safely migrate (version jumps from 0→1→2). Fresh DBs run all blocks and land at version 2. Existing `CREATE TABLE IF NOT EXISTS` blocks remain for safety.

---

### Session A: Withings API → body_metrics

#### Step 0: Developer Account Setup (one-time, manual + documented)

The plan includes a setup checklist for creating the Withings developer app and getting initial tokens. This is a manual browser flow — the script does not automate it. The checklist:

1. Create account at `https://developer.withings.com` → New Application → Health API
2. Set redirect URI to `http://localhost:8080/callback` (for one-time token exchange)
3. Copy `client_id` and `client_secret` → `agents/sample-agent/.env`
4. Run `python3 scripts/withings-auth.py` — small standalone script that opens the auth URL, starts a local callback server, exchanges the code for tokens, and writes `WITHINGS_ACCESS_TOKEN`, `WITHINGS_REFRESH_TOKEN`, `WITHINGS_TOKEN_EXPIRY` to `.env`

`withings-auth.py` is a setup-only script, separate from the main sync. It uses only stdlib (`http.server`, `urllib`, `webbrowser`).

#### withings-sync.py Structure

Mirrors `oura-sync.py` exactly, with Withings-specific auth and API differences:

**Token management** (key difference from Oura):
```python
def load_credentials() -> dict:
    """Load client_id, client_secret, access_token, refresh_token, token_expiry from .env"""

def refresh_if_needed(creds: dict) -> dict:
    """If token_expiry < now + 5min, POST refresh to wbsapi.withings.net/v2/oauth2"""
    # Non-standard: action=requesttoken in body, client_id/secret in body (not Basic Auth)
    # Updates .env with new tokens in-place
```

**API call**:
```python
BASE = "https://wbsapi.withings.net/measure"

def fetch_measures(creds: dict, meastype: str, startdate: int, enddate: int) -> list[dict]:
    """POST action=getmeas; paginate via more+offset; decode value * 10^unit"""
```

Meastype codes to fetch: `1,5,6,8,76` (weight, lean mass, fat ratio, fat mass, muscle mass)

**Upsert** into body_metrics:
```python
conn.execute("""
    INSERT INTO body_metrics (date, time, weight_lbs, fat_ratio_pct, fat_mass_lbs, lean_mass_lbs, muscle_mass_lbs, source)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'withings_api')
    ON CONFLICT(date, time) DO UPDATE SET
        weight_lbs = excluded.weight_lbs,
        fat_ratio_pct = excluded.fat_ratio_pct,
        ...
    WHERE body_metrics.weight_lbs IS NOT excluded.weight_lbs OR ...
""", row)
```

Unit conversion on import: `kg * 2.20462 → lbs` (all stored in lbs).

**Sync state**: resource key `"withings_body"` in `sync_state` table.

**Launchd**: add to existing host launchd setup; daily schedule (body weight changes slowly).

#### Session A health_query.py — body-metrics subcommand

```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py body-metrics --days 90
```

Args: `--days N` (default 90), `--start YYYY-MM-DD`, `--end YYYY-MM-DD`, `--metric [weight|fat|lean|all]`

Output dict:
```json
{
  "days_requested": 90,
  "readings": 45,
  "data": [{"date": "...", "weight_lbs": 185.2, "fat_ratio_pct": 22.1, ...}],
  "summary": {"avg_weight_lbs": 186.4, "min_weight_lbs": 184.1, "latest": {...}}
}
```

---

### Session B: Apple Health XML → body_metrics + activity_daily + workouts

#### Import Script: import-apple-health.py

**File location**: fixed drop folder at `~/Downloads/apple_health_export/export.xml`. Script has this as a constant with a `--file` override for flexibility.

**Streaming parse** (mandatory — files can be 200MB-2GB):
```python
import xml.etree.ElementTree as ET

RECORD_TYPES = {
    "HKQuantityTypeIdentifierBloodPressureSystolic": "bp_systolic",
    "HKQuantityTypeIdentifierBloodPressureDiastolic": "bp_diastolic",
    "HKQuantityTypeIdentifierBodyMass": "weight_kg",
    "HKQuantityTypeIdentifierBodyFatPercentage": "fat_ratio",
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierTimeInDaylight": "daylight_min",
}

for event, elem in ET.iterparse(filepath, events=("end",)):
    if elem.tag == "Record":
        rtype = elem.get("type")
        if rtype in RECORD_TYPES:
            # process and buffer
        elem.clear()  # prevent memory growth
    elif elem.tag == "Workout":
        # extract + elem.clear()
```

**BP pairing**: buffer systolic and diastolic by `startDate`; flush pairs to `blood_pressure` table with `source='apple_health'` using existing `ON CONFLICT(date, time) DO UPDATE` upsert (same table, new source value). Only insert if no existing row for that datetime — apple_health rows don't overwrite manual or omron_csv rows.

**Weight/body comp**: convert kg → lbs; upsert to `body_metrics` with `source='apple_health'`. Withings API rows take precedence (same `ON CONFLICT` WHERE guard — apple_health rows won't overwrite withings_api rows that are identical or newer).

**Steps**: aggregate `HKQuantityTypeIdentifierStepCount` by date (multiple records per day from different sources — sum all values for the day). Upsert to `activity_daily` with single daily row.

**Daylight**: same aggregation pattern as steps.

**Workouts**: parse `<Workout>` elements; extract `<WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate">` for avg/max HR. Strip `HKWorkoutActivityType` prefix from workout type for storage (e.g. `FunctionalStrengthTraining`). Upsert to `workouts` with `source='apple_health'`.

**Dry-run flag**: parse + validate, print counts by category, no DB writes.

**Source priority**: apple_health rows do NOT overwrite withings_api rows in body_metrics, and do NOT overwrite omron_csv or imessage rows in blood_pressure. Source column distinguishes them for future queries.

#### Session B health_query.py subcommands

**activity** subcommand:
```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py activity --days 14
```
Args: `--days N`, `--start`, `--end`, `--metric [steps|daylight|all]`
Output: daily rows + summary (avg steps, total daylight hours)

**workouts** subcommand:
```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py workouts --days 30
```
Args: `--days N`, `--start`, `--end`, `--type [strength|cardio|all]`
Output: list of workout sessions with type, duration, calories, HR; summary counts by type

---

### Session C: Evernote + Oura Tags + Full Query/Skill Wiring

#### Evernote ENEX Parser: import-evernote-workouts.py

**Export**: user tags all workout notes in Evernote → exports to ENEX (File → Export Notes → ENEX format). Result: single `.enex` file containing all tagged notes since Jan 1 2025.

**Note title pattern**: `"Week \d+ Training Plan"` (e.g. "Week 1826 Training Plan"). Parser filters to notes matching this regex. Notes outside this pattern are skipped.

**Two-pass parse**:
1. Outer: `lxml.etree` with `XMLParser(recover=True, resolve_entities=False)` → iterate `<note>` elements → extract `<title>`, `<created>` (format: `20250106T090000Z`), `<content>` CDATA string
2. Inner: `html.parser` (stdlib) → find `<table>` → extract rows where col 0 = day name or date, col 1 = Planned (skip), col 2 = Actual exercises

**Note-to-workout linking**: parse week number and year from note title → derive date range → for each Actual cell, match to `workouts` table by date. If no matching workout row (workout not logged in Apple Watch that day), create a `workouts` row with `source='evernote'` and populate exercise detail.

**Exercise parsing**: parse "Exercise 3×5 @ 185" format. Regex: `(\w[\w\s]+?)\s+(\d+)[×x](\d+)\s*@?\s*(\d+(?:\.\d+)?)?\s*(lbs|kg)?` — graceful fallback if format varies (store raw text in `notes` column of `workout_exercises`).

**Dedup**: `workout_exercises` has no unique index (exercise order within a session can repeat). Clear existing exercises for a workout_date before re-importing from that note, or use `workout_id + set_number + exercise_name` as natural key.

#### Oura Tags Extension: oura-sync.py

Add `sync_tags()` function following exact `sync_daily_summaries` pattern:

```python
def sync_tags(conn, headers, start, end):
    resource_key = "tags"
    for chunk_start, chunk_end in date_chunks(start, end, days=90):
        records = fetch_all("enhanced_tag", chunk_start, chunk_end, headers)
        for r in records:
            conn.execute("""
                INSERT OR REPLACE INTO oura_tags (id, day, tag_type, start_time, end_time, comment)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (r["id"], r["day"], r.get("tag_type_code"), r.get("start_time"), r.get("end_time"), r.get("comment")))
        conn.commit()
    set_last_synced(conn, resource_key, end)
```

Add to main() sync loop after heartrate. Resource key: `"tags"`.

If `enhanced_tag` endpoint returns 404 (not available on all Oura plans), fall back gracefully to `[]` per existing 404 handling pattern.

#### Session C health_query.py — workout-exercises + tags subcommands

**workout-exercises** subcommand:
```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py workout-exercises --date 2026-04-28
```
Args: `--date YYYY-MM-DD`, `--days N` (list all workout exercises in window)

**tags** subcommand:
```
exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py tags --days 30 --type sauna
```
Args: `--days N`, `--type [sauna|alcohol|all]`

#### AGENTS.md Rule 6b Extensions (Session C)

Append to existing Rule 6b block — one "For X" block per new query type:
```
For body composition ("my weight", "body fat", "lean mass", "fat percentage", "weight trend", "how much do I weigh"):
    exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py body-metrics --days 90

For activity ("my steps", "steps this week", "daylight", "time outside", "how active"):
    exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py activity --days 14

For workouts ("my workouts", "did I exercise", "gym this week", "workout summary", "training"):
    exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py workouts --days 30

For workout detail ("what did I do at the gym", "my exercises", "sets and reps", "strength training detail"):
    exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py workout-exercises --days 7

For Oura tags ("my sauna days", "when did I sauna", "alcohol tags", "tag trends"):
    exec: python3 /home/openclaw/.openclaw/workspace/health/health_query.py tags --days 30
```

**Note on AGENTS.md size**: already at ~48K chars with ~17% truncation. Each "For X" block must stay under 4 lines. If overall Rule 6b grows too large, consolidate into a single block with grouped trigger phrases.

#### SKILL.md New Intents (Session C)

Add Intents 9-13 following exact existing intent format:
- Intent 9: body-metrics query
- Intent 10: activity (steps/daylight) query
- Intent 11: workouts query
- Intent 12: workout exercises detail query
- Intent 13: Oura tags query

Each intent: mandatory-stop cap + STEP 1 exec in fenced block + STEP 2 synthesize + Hard rules + error fallback.

---

## System Architecture

```
Host (Mac/Freedom)                          Container (OpenClaw)
─────────────────────────────────────────   ────────────────────────────────
withings-sync.py (daily launchd cron)   ┐   health_query.py body-metrics
oura-sync.py (existing + tags endpoint) ┤ → health.db ← health_query.py activity
import-apple-health.py (one-time)       ┤   (workspace/health/) health_query.py workouts
import-evernote-workouts.py (manual)    ┘   health_query.py workout-exercises
                                            health_query.py tags

Withings API → withings-sync.py → body_metrics (source=withings_api, ongoing)
Apple Health XML → import-apple-health.py → body_metrics + activity_daily + workouts + blood_pressure (source=apple_health, one-time backfill)
Evernote ENEX → import-evernote-workouts.py → workout_exercises (manual re-export)
Oura API → oura-sync.py tags → oura_tags (ongoing, existing cron)
```

---

## Implementation Phases

### Phase 1 (Session A) — Withings + Migration Framework

1. Add PRAGMA user_version migration framework to `health_db.py`
2. Add `body_metrics` table schema (version 2 migration block)
3. Create `scripts/withings-auth.py` (one-time OAuth token exchange)
4. Create `scripts/withings-sync.py` (daily cron, mirrors oura-sync.py)
5. Write unit tests for Withings value decode (`value * 10^unit`) and kg→lbs conversion
6. Write integration test: mock API response → parse → upsert → verify body_metrics row
7. Add `body-metrics` subcommand to `health_query.py`
8. Extend Rule 6b in AGENTS.md (body composition block)
9. Add launchd plist for withings-sync (daily)
10. Run `withings-auth.py` → verify first sync populates body_metrics

### Phase 2 (Session B) — Apple Health XML Import

1. Add `activity_daily` + `workouts` table schemas to `health_db.py` (version 3 migration block)
2. Create `scripts/import-apple-health.py` with streaming iterparse
3. Implement BP dedup logic (apple_health doesn't overwrite omron_csv/imessage rows)
4. Implement body comp import (kg→lbs, apple_health doesn't overwrite withings_api rows)
5. Implement steps aggregation per day
6. Implement daylight aggregation per day
7. Implement workout parsing (type, duration, calories, HR from WorkoutStatistics)
8. Write tests: streaming parse handles large files, dedup logic correct, unit conversions correct
9. Run `--dry-run` against actual Apple Health export → verify counts/ranges
10. Run live import → spot-check data in health.db
11. Add `activity` + `workouts` subcommands to `health_query.py`
12. Extend Rule 6b in AGENTS.md (activity + workouts blocks)

### Phase 3 (Session C) — Evernote + Tags + Full Wiring

1. Add `workout_exercises` + `oura_tags` schemas to `health_db.py` (version 4 migration block)
2. Add `sync_tags()` to `oura-sync.py`; test with actual Oura account
3. Create `scripts/import-evernote-workouts.py` with two-pass ENEX parser
4. Implement exercise text parser (regex + fallback to raw text)
5. Implement note→workout date matching
6. Write tests for exercise parser (various formats), note title regex, date matching
7. Run dry-run against actual ENEX export → verify note count and row extraction
8. Add `workout-exercises` + `tags` subcommands to `health_query.py`
9. Add remaining Rule 6b blocks to AGENTS.md
10. Add Intents 9-13 to SKILL.md
11. Deploy: `docker restart sample-agent_secure` → iMessage end-to-end test for all new queries

---

## Testing Strategy

### Unit Tests (per session)

**Session A tests** (`test_withings_sync.py`):
- Withings value decode: `value=705, unit=-1 → 70.5 kg`
- kg→lbs conversion: `70.5 kg → 155.4 lbs` (within 0.1 tolerance)
- Token refresh detection: expiry < now+5min → triggers refresh
- Upsert: identical row → rowcount=0 (skipped); changed value → rowcount=1 (updated)
- Pagination: mock response with `more=1`/`more=0` → two API calls, all records collected

**Session B tests** (`test_apple_health.py`):
- iterparse: synthetic minimal XML → correct record extraction
- BP pairing: systolic + diastolic at same timestamp → single blood_pressure row
- BP dedup: apple_health row doesn't overwrite existing omron_csv row at same datetime
- Steps aggregation: 3 records same day, different sources → sum
- Workout HR extraction: `<WorkoutStatistics>` children parsed correctly
- Workout type stripping: `HKWorkoutActivityTypeFunctionalStrengthTraining` → `FunctionalStrengthTraining`

**Session C tests** (`test_evernote.py`):
- Note title filter: "Week 1826 Training Plan" matches; "Shopping List" doesn't
- Exercise parser: `"Squat 3×5 @ 185"` → `{exercise: "Squat", sets: 3, reps: 5, weight_lbs: 185}`
- Exercise parser fallback: unrecognized format → `{exercise: raw_text, notes: raw_text}`
- ENEX two-pass: synthetic ENEX with HTML table → correct row extraction
- Tag sync: 404 on enhanced_tag → returns `[]`, no crash

### Integration Tests (each session)

Follow `test_import_blood_pressure.py` pattern:
- Subprocess call to import script with `--dry-run` → exits 0, prints expected counts
- Subprocess call without dry-run against temp DB → verify row counts and spot values

### Pattern: All unit tests use in-memory DB

```python
def _make_conn():
    conn = sqlite3.connect(":memory:")
    health_db.initialize_schema(conn)
    return conn

class TestBodyMetrics(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        withings_sync.health_db.get_connection = lambda *a, **kw: self.conn
```

---

## Data Architecture Notes

### Source Priority (body_metrics table)

When both Apple Health and Withings API have a reading for the same datetime:
- Withings API is authoritative (the source of truth for body composition)
- Apple Health row for the same timestamp is silently skipped (WHERE guard on DO UPDATE)
- Identified by `source` column: `withings_api` > `apple_health`

### Steps Aggregation

Apple Health may emit multiple `HKQuantityTypeIdentifierStepCount` records per day from iPhone, Apple Watch, and third-party apps. Aggregate by summing within a date, then upsert as a single `activity_daily` row. On re-import, the DO UPDATE overwrites the aggregated total (re-aggregation from source is idempotent).

### Workout-Exercise Linking

`workout_exercises.workout_id` is a nullable FK to `workouts.id`. When a workout exists in Apple Health for the matching date and type, link by FK. When the Evernote note references a workout day with no Apple Watch record, create a minimal `workouts` row with `source='evernote'` and link to it.

### PRAGMA user_version Sequence

- Version 0 → 1: existing schema stamped (no structural changes)
- Version 1 → 2: body_metrics (Session A)
- Version 2 → 3: activity_daily + workouts (Session B)
- Version 3 → 4: workout_exercises + oura_tags (Session C)

---

## Critical Files for Implementation

- `agents/sample-agent/workspace/health/health_db.py` — Schema owner; add migration framework + all 5 new tables here
- `scripts/oura-sync.py` — Direct model for withings-sync.py; copy token/pagination/sync_state/upsert patterns exactly
- `scripts/import-blood-pressure.py` — Model for one-time bulk importers; copy parse/dry-run/upsert structure
- `agents/sample-agent/workspace/health/health_query.py` — Add 4-5 new subcommands following existing argparse + dispatch pattern
- `agents/sample-agent/workspace/AGENTS.md` — Rule 6b at lines 68-96; append new "For X" blocks; keep each under 4 lines
- `agents/sample-agent/workspace/skills/health-query/SKILL.md` — Intent Classification table + per-intent blocks; add Intents 9-13
- `agents/sample-agent/workspace/health/test_blood_pressure.py` — Test pattern reference: `_make_conn()` + monkey-patch `get_connection`
