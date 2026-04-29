# Task Context: Apple Health Multi-Source Health Data Import
Branch: main | Date: 2026-04-29

## Feature Summary

Add 4 new data import pipelines into health.db to enable cross-pillar LLM correlation queries via iMessage. Data sources: Withings API (body composition), Apple Health XML (BP backfill, steps, daylight, workout summaries), Evernote ENEX (workout exercise detail), Oura API tags extension.

**User value**: Ask "do my lowest HRV scores follow hard workout days?", "is my BP higher after poor sleep?", "as my body fat drops, are BP readings improving?" — correlations across pillars that require unified data.

**Scope doc**: `docs/tasks/main/concepts/scope_apple_health_multi_source.md`

---

## Architecture Patterns

### Host-side sync pattern (model: oura-sync.py)
`/Users/jeff/ironclaw/scripts/oura-sync.py`

- **Module import**: `_REPO_ROOT = Path(__file__).parent.parent`, `_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"`, `sys.path.insert(0, str(_HEALTH_DIR))`, then `import health_db`
- **Token load**: env var first (`OURA_PERSONAL_ACCESS_TOKEN`), fallback to parsing `agents/sample-agent/.env` line-by-line (strip quotes)
- **Paginated fetch**: `fetch_all(resource, start_date, end_date, headers)` with `next_token` cursor loop; 404 → return [], 429 → sleep 60s + retry once, non-200 → break
- **Date chunking**: `date_chunks(start, end, days=N)` — resource-specific chunk sizes (90d for daily, 30d for sleep, 7d for heartrate)
- **sync_state watermark**: `SELECT last_synced FROM sync_state WHERE resource = ?` / `INSERT OR REPLACE INTO sync_state` — per-resource resume point, plain date string
- **Upsert**: `INSERT OR REPLACE` when external ID is PK; `ON CONFLICT(natural_key) DO UPDATE SET ... WHERE col IS NOT excluded.col` for no-op dedup on identical data
- **Modes**: `--historical` (from 2015-01-01), `--since YYYY-MM-DD`, incremental (reads sync_state per resource)
- **Scheduling**: launchd `StartCalendarInterval` plist on host (not in repo); script runs standalone, not as daemon

### Container-side query pattern (model: health_query.py)
`/Users/jeff/ironclaw/agents/sample-agent/workspace/health/health_query.py`

- All output: `print(json.dumps(result_dict))` (exit 0) or `print(json.dumps({"error": msg})); sys.exit(1)`
- argparse with `add_subparsers(dest="command", required=True)`, flat if/elif dispatch
- Every function returns a plain dict; outer `try/except Exception` always produces valid JSON
- sibling import: `sys.path.insert(0, str(Path(__file__).parent))` then `import health_db`

### One-time bulk import pattern (model: import-blood-pressure.py)
`/Users/jeff/ironclaw/scripts/import-blood-pressure.py`

- `--dry-run` flag: parse validates input + prints count/range, no DB write
- Parse phase returns list of normalized dicts; bad rows skipped with stderr warning (no abort)
- Import phase: `get_connection()` → upsert loop → `commit()` → `close()` → print summary
- Dedup: `ON CONFLICT(date, time) DO UPDATE SET ... WHERE col IS NOT excluded.col`; `rowcount == 0` → skipped

### health_db.py schema conventions
`/Users/jeff/ironclaw/agents/sample-agent/workspace/health/health_db.py`

- DB at `Path(__file__).parent / "health.db"` — works identically on host and in container
- PRAGMAs: journal_mode=WAL, synchronous=NORMAL, foreign_keys=ON, cache_size=-32768, busy_timeout=5000
- All tables: `CREATE TABLE IF NOT EXISTS` in `initialize_schema(conn)`
- Dedup indexes: `CREATE UNIQUE INDEX IF NOT EXISTS` on natural keys
- `row_factory = sqlite3.Row` — dict-style access
- No migration runner yet (arch review flagged; must add PRAGMA user_version before next schema change)

### AGENTS.md Rule 6b pattern
`/Users/jeff/ironclaw/agents/sample-agent/workspace/AGENTS.md` (lines 68-96)

- Append a new "For {category} ({trigger phrases}):" block with fenced exec to existing Rule 6b
- Path always: `/home/openclaw/.openclaw/workspace/health/health_query.py`
- FORBIDDEN phrases must be explicit — GPT-5-mini won't exec without them

### SKILL.md intent pattern
`/Users/jeff/ironclaw/agents/sample-agent/workspace/skills/health-query/SKILL.md`

- Intent Classification table row: `| N — Name | "trigger phrases" | action keywords |`
- Intent section: mandatory-stop cap, STEP 1 exec in fenced block, STEP 2 synthesize, Hard rules block with error fallback

---

## Dependencies

### Files Modified
| File | Change |
|------|--------|
| `agents/sample-agent/workspace/health/health_db.py` | Add 5 new tables + PRAGMA user_version migration framework |
| `agents/sample-agent/workspace/health/health_query.py` | Add 4 new subcommands: body-metrics, activity, workouts, tags |
| `scripts/oura-sync.py` | Add tags endpoint sync |
| `agents/sample-agent/workspace/AGENTS.md` | Rule 6b extensions for each new query type |
| `agents/sample-agent/workspace/skills/health-query/SKILL.md` | New intents for each new data type |

### New Files
| File | Purpose |
|------|---------|
| `scripts/withings-sync.py` | Withings API OAuth2 cron (model: oura-sync.py) |
| `scripts/import-apple-health.py` | Apple Health XML streaming parser (BP, steps, daylight, workouts) |
| `scripts/import-evernote-workouts.py` | Evernote ENEX parser for weekly workout tables |
| `agents/sample-agent/workspace/health/test_withings_sync.py` | Tests for Withings sync |
| `agents/sample-agent/workspace/health/test_apple_health.py` | Tests for Apple Health import |

---

## Implementation Approaches

### Withings API — OAuth2 (non-standard, critical gotchas)

OAuth2 with non-standard endpoint shape:
- **Authorization URL**: `https://account.withings.com/oauth2_user/authorize2`
- **Token exchange/refresh URL**: `https://wbsapi.withings.net/v2/oauth2` (NOT a standard `/token` path)
- **Non-standard**: requires `action=requesttoken` in POST body; `client_id`/`client_secret` in body (not Basic Auth header)
- **Token refresh**: same endpoint, `grant_type=refresh_token`, `action=requesttoken`
- **Access token expiry**: ~3 hours; must proactively refresh or catch 401
- **Token storage**: store `access_token`, `refresh_token`, `client_id`, `client_secret`, `token_expiry` in `.env`

Body composition endpoint: `POST https://wbsapi.withings.net/measure` with `action=getmeas`

Measure types needed:
| meastype | Metric |
|----------|--------|
| 1 | Weight (kg) |
| 5 | Lean body mass (kg) |
| 6 | Fat ratio (%) |
| 8 | Fat mass weight (kg) |
| 76 | Muscle mass (kg) |
| 9 | Systolic BP (bonus: maps directly to health.db blood_pressure) |
| 10 | Diastolic BP |

Response: `measuregrps[]` — each is a weigh-in session; each `measure` has `value` and `unit` where real value = `value * 10^unit`. Pagination via `more` (0/1) and `offset`.

**Recommendation**: Use raw `requests` rather than `withings-api` PyPI package (API has versioned multiple times, library may lag; non-standard OAuth is simpler to implement directly than debug through an abstraction).

**OAuth setup flow** (one-time, user does in browser):
1. Visit authorization URL with `client_id`, `scope`, `redirect_uri`
2. Withings redirects to callback with `code`
3. Exchange `code` for tokens via POST to `/v2/oauth2?action=requesttoken`
4. Store tokens in `.env`

### Apple Health XML — Streaming Parser

Export file: `~/Library/Mobile Documents/com~apple~Health/Documents/export.xml` or from Health app → Export. Size: 200MB-2GB+ for years of data — **streaming parse mandatory**.

Key identifiers:
```
HKQuantityTypeIdentifierBodyMass          → body weight
HKQuantityTypeIdentifierBodyFatPercentage → body fat %
HKQuantityTypeIdentifierBloodPressureSystolic / Diastolic → BP
HKQuantityTypeIdentifierStepCount         → steps (aggregate by day)
HKQuantityTypeIdentifierTimeInDaylight    → daylight minutes
HKWorkoutActivityType*                    → workouts (separate <Workout> elements)
```

Streaming parse with stdlib (no extra deps):
```python
import xml.etree.ElementTree as ET
for event, elem in ET.iterparse(filepath, events=("end",)):
    if elem.tag == "Record":
        rtype = elem.get("type")
        # process...
        elem.clear()  # CRITICAL: prevents memory growth
    elif elem.tag == "Workout":
        # extract WorkoutStatistics for HR
        elem.clear()
```

BP join: systolic and diastolic share same `startDate` — join by timestamp to reconstruct pairs.

Steps: aggregate `HKQuantityTypeIdentifierStepCount` by date (multiple records per day from different sources — sum them or take max).

Workout HR: in `<WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" average="..." minimum="..." maximum="..." unit="count/min"/>` child element.

Workout type mapping: `HKWorkoutActivityTypeFunctionalStrengthTraining`, `HKWorkoutActivityTypeCycling`, `HKWorkoutActivityTypeRunning`, etc. — strip the prefix for storage.

### Evernote ENEX — Two-Pass XML Parser

ENEX is XML containing `<note>` elements. `<content>` is CDATA-wrapped ENML (subset of XHTML). Tables are standard HTML inside the CDATA.

Two-pass approach:
1. Parse outer ENEX XML → extract `<content>` CDATA string per note + `<title>` + `<created>`
2. Parse CDATA as HTML → extract `<table>` rows with `html.parser` (stdlib)

Key gotcha: use `lxml.etree.XMLParser(recover=True, resolve_entities=False)` to handle DOCTYPE declaration without network fetch. If lxml unavailable, pre-strip the DOCTYPE line with regex before parsing.

Weekly table format: rows = days, columns = Planned / Actual (+ any Oura screenshot columns to ignore). Parser should:
- Match notes by title pattern (weekly training log or similar naming convention)
- Filter to notes created >= 2025-01-01
- Extract Actual column values: exercise name, sets×reps@weight format (e.g. "Squat 3×5 @ 185")
- Link to workouts table by date match

### Oura Tags Extension

Oura v2 tags endpoints:
- `GET /v2/usercollection/tag` (deprecated but may still work)
- `GET /v2/usercollection/enhanced_tag` (current endpoint)

Enhanced tag fields: `id`, `day`, `tag_type_code` (e.g. "sauna", "alcohol", "late_meal"), `start_time`, `end_time`, `comment`.

Add as new sync function in `oura-sync.py` following exact `sync_daily_summaries` pattern with new resource key `"tags"` in sync_state.

### DB Schema — 5 New Tables

```sql
-- body_metrics: one row per Withings weigh-in
CREATE TABLE IF NOT EXISTS body_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    time TEXT,
    weight_kg REAL,
    fat_ratio REAL,
    fat_mass_kg REAL,
    lean_mass_kg REAL,
    muscle_mass_kg REAL,
    source TEXT DEFAULT 'withings_api',
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_body_metrics_datetime ON body_metrics(date, time);
CREATE INDEX IF NOT EXISTS idx_body_metrics_date ON body_metrics(date);

-- activity_daily: one row per day, aggregated
CREATE TABLE IF NOT EXISTS activity_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    steps INTEGER,
    daylight_minutes REAL,
    source TEXT DEFAULT 'apple_health',
    fetched_at TEXT DEFAULT (datetime('now'))
);

-- workouts: one row per workout session
CREATE TABLE IF NOT EXISTS workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    workout_type TEXT NOT NULL,
    duration_min REAL,
    calories REAL,
    avg_hr INTEGER,
    max_hr INTEGER,
    effort_rating TEXT,
    source TEXT DEFAULT 'apple_health',
    notes TEXT,
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_workouts_datetime ON workouts(date, start_time, workout_type);
CREATE INDEX IF NOT EXISTS idx_workouts_date ON workouts(date);

-- workout_exercises: linked to workouts, from Evernote
CREATE TABLE IF NOT EXISTS workout_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id INTEGER REFERENCES workouts(id) ON DELETE CASCADE,
    workout_date TEXT NOT NULL,  -- denormalized for queries without join
    exercise_name TEXT NOT NULL,
    set_number INTEGER,
    reps INTEGER,
    weight_lbs REAL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_workout_exercises_date ON workout_exercises(workout_date);

-- oura_tags: from Oura enhanced_tag endpoint
CREATE TABLE IF NOT EXISTS oura_tags (
    id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    tag_type TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    comment TEXT,
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_oura_tags_day ON oura_tags(day);
```

### Migration Framework (PRAGMA user_version)

Current: no migration runner. All tables use `CREATE TABLE IF NOT EXISTS`.
Required before next schema change (arch review requirement):

```python
CURRENT_VERSION = 2  # increment with each schema change

def initialize_schema(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        # existing tables — already there via IF NOT EXISTS
        conn.execute("PRAGMA user_version = 1")
    if version < 2:
        # new tables for this feature
        conn.executescript("""...""")
        conn.execute("PRAGMA user_version = 2")
    conn.commit()
```

### PRAGMA user_version Approach
- Version 1: existing schema (blood_pressure, oura_daily, lab_results, etc.)
- Version 2: adds body_metrics, activity_daily, workouts, workout_exercises, oura_tags

---

## Impact Summary

**High-impact files**: health_db.py (schema hub), AGENTS.md (enforcement), SKILL.md (routing)
**Risk areas**:
- Withings OAuth first-time setup (non-standard flow, manual browser step required)
- Apple Health XML size — must use iterparse or will OOM
- Evernote table format variation across years of notes (parser needs robustness)
- AGENTS.md size already at ~48K chars (~17% truncated) — new Rule 6b blocks must be concise
- Workout dedup between Apple Health and Evernote (date+type match, not perfect)

**No risk**: existing tables untouched; all new tables use IF NOT EXISTS; busy_timeout already set

---

## External Research

### Withings API
- Non-standard OAuth: `action=requesttoken` param required; endpoint is `/v2/oauth2` not `/token`
- Tokens in body (not Basic Auth header); access token expires ~3 hours
- `measuregrps[]` response; real value = `value * 10^unit`; meastype list in analysis above
- Pagination: `more` flag + `offset` param
- Reference: `https://developer.withings.com/api-reference/`

### Apple Health XML
- `iterparse` with `elem.clear()` is mandatory for files >100MB
- BP: join systolic + diastolic by matching `startDate`
- Steps: aggregate per day (multiple source records)
- Workouts: `<Workout>` elements with `<WorkoutStatistics>` children for HR
- All identifier strings documented above

### Evernote ENEX
- CDATA-wrapped ENML; tables are standard HTML `<table><tr><td>`
- Two-pass parse: outer XML → inner HTML
- `lxml` with `resolve_entities=False` prevents DTD network fetch
- `html.parser` (stdlib) works for inner ENML table extraction

---

## Architecture Decision

**Selected: Option A — Source-by-Source**

- **Session A**: PRAGMA user_version framework + Withings API sync + body_metrics table + body-metrics query via iMessage
- **Session B**: Apple Health XML import — ALL categories present in export: BP, weight, body fat%, lean mass, steps, daylight, workout summaries. Tables: body_metrics (backfill), activity_daily, workouts. Not just BP — the full historical record for every supported type.
- **Session C**: Evernote parser + workout_exercises table + Oura tags + SKILL.md/AGENTS.md wiring for all new types

Note: Apple Health is the historical backfill for everything. Withings API (Session A) is the ongoing automated source for body composition going forward, since it doesn't push fat/lean to Health for ongoing sync. The Apple Health XML import writes to body_metrics with source='apple_health' — Withings writes with source='withings_api'. Both coexist in the same table.

---

## Assessed Complexity: COMPREHENSIVE

**Signals:**
- Files impacted: 9+ → HIGH
- Pattern match: Clear (oura-sync.py → withings-sync.py) → LOW; XML parsing is new → MEDIUM
- Components crossed: 5+ (health_db, health_query, oura-sync, AGENTS, SKILL, 3 new scripts) → HIGH
- Data model changes: 5 new tables + migration framework → HIGH
- Integration points: 4 external sources → HIGH
- External complexity: Withings OAuth non-standard + XML streaming → MEDIUM

**Hard stops triggered:** new_service_or_component (3 new scripts), new_models_schema (5 tables)

**Recommended session split:**
- **Session A**: PRAGMA user_version framework + Withings API sync + body_metrics table + body-metrics query subcommand
- **Session B**: Apple Health XML import (BP dedup, steps, daylight, workout summaries) + activity/workouts query subcommands
- **Session C**: Evernote parser + workout_exercises + Oura tags + SKILL.md/AGENTS.md wiring for all new types
