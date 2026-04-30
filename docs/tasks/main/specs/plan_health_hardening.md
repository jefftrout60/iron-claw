# Plan: Health Hardening + iOS Sync Sprint

**Date**: 2026-04-30 | **Depth**: Standard | **Branch**: main

---

## Overview

Seven hardening themes flagged by architecture/code review. All changes are additive or surgical — no schema migrations, no new external dependencies for the core items. The iOS Shortcut piece is the most novel (new JSON import format + launchd WatchPaths job) but is bounded by a clear existing pattern (oura-sync.py as the model).

---

## Desired End State

1. `scripts/rotate-container-secrets.sh` — interactive credential rotation + container restart in one command
2. `docs/tasks/main/imessage-test-checklist.md` — ~12 queries covering all 5 new subcommands, with expected output shapes for manual verification
3. `oura_daily.avg_hrv_rmssd` non-NULL for every day with a sleep session; new syncs auto-populate it
4. `health_db.get_last_synced` / `health_db.set_last_synced` — one canonical implementation; importers/sync scripts call it; zero copy-paste
5. Apple Health source guard defined once in `import-apple-health.py`
6. Migration path tests: v0→v4, v2→v4, v3→v4 incremental; try/except patches verified idempotent
7. `health_query.py sync-status` — returns last_synced + stale flag per source for all 4 pillars; AGENTS.md + SKILL.md wired
8. Health Auto Export → iCloud Drive → launchd WatchPaths → `import-apple-health-json.py` auto-imports on arrival; iOS Shortcut guide written

---

## Out of Scope

- Embedding / vector search layer
- Moving container secrets to macOS Keychain (Docker can't access it; rotation only)
- Automated credential generation in external dashboards
- `sync-status` record counts (add later if needed)
- Stale flag for Apple Health / Evernote (manual imports, no meaningful threshold)
- Schema changes to health.db (no new tables, no column additions)

---

## Technical Approach

### Theme 1 — Secrets Rotation Script
**File**: `scripts/rotate-container-secrets.sh`

Three secrets to rotate: `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `GMAIL_APP_PASSWORD` in `agents/sample-agent/.env`.

```bash
# Pattern: for each key, prompt → update .env in-place → confirm
for KEY in OPENAI_API_KEY TELEGRAM_BOT_TOKEN GMAIL_APP_PASSWORD; do
    read -rsp "New value for $KEY (enter to skip): " NEW_VAL
    if [[ -n "$NEW_VAL" ]]; then
        python3 -c "
import re, sys
with open('.env') as f: content = f.read()
content = re.sub(r'^($KEY=).*', r'\g<1>$NEW_VAL', content, flags=re.MULTILINE)
with open('.env', 'w') as f: f.write(content)
"
    fi
done
# Stop → update → restart
docker compose -p sample-agent down
./scripts/compose-up.sh sample-agent -d
```

- Must stop the container before modifying `.env` to avoid partial-state reads
- Print last 4 chars of each new key as confirmation (never print full value)
- Script exits 1 if `.env` not found

---

### Theme 2 — iMessage Test Checklist
**File**: `docs/tasks/main/imessage-test-checklist.md`

~12 queries organized by subcommand with expected output shape (not exact values):

| Subcommand | Sample query | Expected shape |
|---|---|---|
| `body-metrics` | "what's my current weight" | Most recent weight + date |
| `body-metrics` | "show my body fat trend this year" | Series of (date, fat%) readings |
| `activity` | "how many steps this week" | Daily step counts for last 7 days |
| `activity` | "how much sunlight did I get today" | Today's daylight minutes |
| `workouts` | "what workouts did I do this week" | List of workout type + date + duration |
| `workouts` | "show my recent workouts" | Last 5–10 workouts |
| `workout-exercises` | "what did I do at the gym last Tuesday" | Exercises + sets/reps/weight for that date |
| `workout-exercises` | "show my squat history" | All squat sets across all sessions |
| `tags` | "how many sauna sessions this month" | Count of sauna tags this month |
| `tags` | "show my sleep tags this week" | Tagged events for last 7 days |
| `sync-status` | "is my health data up to date" | Last sync per source + any stale flags |
| `sync-status` | "when did I last sync my health data" | Same |

---

### Theme 3 — HRV Fix
**Root cause**: `sync_daily_summaries` writes `row.get("avg_hrv_rmssd")` but `avg_hrv_rmssd` is never set in the `daily` accumulator dict. The data lives in `oura_sleep_sessions.avg_hrv`, populated separately by `sync_sleep_sessions`.

**Fix**: Add a `_backfill_daily_hrv(conn)` helper in `health_db.py` and call it at the end of `sync_sleep_sessions` in `oura-sync.py`.

```python
# health_db.py
def backfill_daily_hrv(conn):
    """Populate oura_daily.avg_hrv_rmssd from the best sleep session per day."""
    conn.execute("""
        UPDATE oura_daily
        SET avg_hrv_rmssd = (
            SELECT avg_hrv FROM oura_sleep_sessions
            WHERE oura_sleep_sessions.day = oura_daily.day
            ORDER BY
                CASE WHEN type = 'long_sleep' THEN 0 ELSE 1 END,
                total_sleep_sec DESC
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM oura_sleep_sessions
            WHERE oura_sleep_sessions.day = oura_daily.day
              AND avg_hrv IS NOT NULL
        )
    """)
    conn.commit()
```

Call site in `oura-sync.py`, end of `sync_sleep_sessions`:
```python
health_db.backfill_daily_hrv(conn)
log.info("Refreshed avg_hrv_rmssd from sleep sessions")
```

**Historical backfill**: call `health_db.backfill_daily_hrv(conn)` once from a one-off script or directly via `python3 -c "import health_db, sqlite3; ..."`. The UPDATE is unconditional (re-derives from sessions on each call) so it's idempotent and safe to run repeatedly.

Note: `oura_sleep_sessions` column is `avg_hrv`; `oura_daily` column is `avg_hrv_rmssd`. The naming difference is intentional — RMSSD is the correct unit label for the daily aggregate.

---

### Theme 4 — Source-Priority De-dup

**4a. Move sync helpers to health_db.py**

`oura-sync.py:139-151` and `withings-sync.py` each define identical `get_last_synced`/`set_last_synced` functions (the withings comment literally says "mirrors oura-sync.py exactly").

Add to `health_db.py` (after the `sync_state` table definition, around line 255):
```python
def get_last_synced(conn, resource: str, default: str = None) -> str | None:
    row = conn.execute(
        "SELECT last_synced FROM sync_state WHERE resource = ?", (resource,)
    ).fetchone()
    return row[0] if row else default

def set_last_synced(conn, resource: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sync_state (resource, last_synced) VALUES (?, ?)",
        (resource, value),
    )
    conn.commit()
```

Remove both local copies. Both scripts already `import health_db`, so the call sites don't change beyond removing the local defs.

**4b. De-dup apple_health source guard**

`import-apple-health.py` has the same WHERE guard in two places:
```sql
WHERE body_metrics.source IS 'apple_health'
```

Extract to a module-level constant:
```python
_APPLE_HEALTH_SOURCE_GUARD = "WHERE body_metrics.source IS 'apple_health'"
```

Use f-string interpolation in both upsert statements. The guard logic is identical in both branches; the SQL structure around it differs, so a full helper function would add more indirection than it removes — a constant is the right level.

---

### Theme 5 — Migration Path Tests
**File**: `agents/sample-agent/workspace/health/test_migration_paths.py`

The challenge: `initialize_schema` applies migrations sequentially based on `PRAGMA user_version`. To test incremental paths, we need to build a DB that's already at version N, then call `initialize_schema` and verify it reaches version 4.

```python
def _conn_at_version(n: int) -> sqlite3.Connection:
    """
    Create an in-memory DB that has the tables for versions 1..n
    and PRAGMA user_version = n, simulating a DB that was migrated to version n
    and has not yet received later migrations.
    """
    conn = sqlite3.connect(":memory:")
    # Apply only the base DDL (version 1 tables) since initialize_schema
    # uses CREATE IF NOT EXISTS — we need the base tables present
    health_db.initialize_schema(conn)          # gets us to v4
    conn.execute(f"PRAGMA user_version = {n}") # rewind stamp
    # Drop tables added in versions > n so initialize_schema has real work to do
    if n < 4:
        conn.execute("DROP TABLE IF EXISTS workout_exercises")
        conn.execute("DROP TABLE IF EXISTS oura_tags")
    if n < 3:
        conn.execute("DROP TABLE IF EXISTS activity_daily")
        conn.execute("DROP TABLE IF EXISTS workouts")
    if n < 2:
        conn.execute("DROP TABLE IF EXISTS body_metrics")
    return conn
```

Tests:
- `test_fresh_db_reaches_v4()` — `initialize_schema` on empty DB → `PRAGMA user_version == 4`, all tables present
- `test_v2_to_v4()` — start at v2, call `initialize_schema`, verify v3+v4 tables created, version stamp = 4
- `test_v3_to_v4()` — start at v3, call `initialize_schema`, verify v4 tables created, version stamp = 4
- `test_column_patches_idempotent()` — call `initialize_schema` twice on same DB, no exception raised

---

### Theme 6 — sync-status Subcommand

**6a. Apple Health + Evernote write to sync_state**

Add to end of successful import in `import-apple-health.py`:
```python
health_db.set_last_synced(conn, "apple_health", date.today().isoformat())
```

Add to end of successful import in `import-evernote-workouts.py`:
```python
health_db.set_last_synced(conn, "evernote_workouts", date.today().isoformat())
```

Both scripts already call `health_db.get_connection()` and use health_db — add the import of `set_last_synced` (or call it as `health_db.set_last_synced`).

**6b. sync-status subcommand in health_query.py**

```python
def cmd_sync_status(conn):
    rows = conn.execute(
        "SELECT resource, last_synced FROM sync_state ORDER BY resource"
    ).fetchall()
    STALE_RESOURCES = {"daily_summaries", "sleep", "heartrate", "oura_tags", "withings"}
    STALE_DAYS = 2
    result = {}
    for row in rows:
        last = row["last_synced"]
        try:
            days_ago = (date.today() - date.fromisoformat(last)).days
        except (TypeError, ValueError):
            days_ago = None
        stale = (
            days_ago is not None
            and days_ago > STALE_DAYS
            and row["resource"] in STALE_RESOURCES
        )
        result[row["resource"]] = {
            "last_synced": last,
            "days_ago": days_ago,
            "stale": stale,
        }
    return result
```

Add argparse subparser for `sync-status` (no additional args needed).

**6c. AGENTS.md Rule 6b table row**

Add to the compact table in Rule 6b:
```
| sync-status | "up to date", "last sync", "data fresh", "when did I sync" | health_query.py sync-status |
```

**6d. SKILL.md intent**

Add Intent 14 (or next available):
```
| 14 — SyncStatus | "is my data up to date", "when did I last sync", "data freshness" | sync-status |
```
With exec block: `python3 /home/openclaw/.openclaw/workspace/health/health_query.py sync-status`

---

### Theme 7 — iOS Shortcut + launchd Watcher + JSON Importer

**7a. Health Auto Export JSON format**

Health Auto Export (Liftcode) exports a JSON file with this known structure:
```json
{
  "data": {
    "metrics": [
      {
        "name": "step_count",
        "units": "count",
        "data": [{"date": "2024-01-15 00:00:00 -0800", "qty": 8432, "source": "..."}]
      },
      {
        "name": "body_mass",
        "units": "lb",
        "data": [{"date": "2024-01-15 00:00:00 -0800", "qty": 182.5, "source": "..."}]
      }
    ],
    "workouts": [
      {
        "name": "Strength Training",
        "start": "2024-01-15 09:00:00 -0800",
        "end": "2024-01-15 10:00:00 -0800",
        "duration": 3600,
        "activeEnergy": {"qty": 450, "units": "kcal"},
        "heartRateData": [{"date": "...", "qty": 145}],
        "heartRateStats": {"avg": {"qty": 142}, "min": {"qty": 98}, "max": {"qty": 178}}
      }
    ]
  }
}
```

⚠️ **Schema verification required**: On first run after installing Health Auto Export, generate a sample export and diff against this structure. The metric name strings (e.g. `"step_count"` vs `"steps"`) and unit strings may differ. Build the importer defensively with a metric-name mapping dict at the top of the file.

**7b. New file: `scripts/import-apple-health-json.py`**

Separate file (not extending the XML importer) — different parse path, same health_db write targets. Structure mirrors `import-apple-health.py`:

```python
METRIC_MAP = {
    "step_count": "steps",
    "body_mass": "weight",
    "body_fat_percentage": "fat_ratio",
    "lean_body_mass": "lean_mass",
    "active_energy_burned": "active_calories",
    "time_in_daylight": "daylight_minutes",
    # ... add as verified from sample export
}

def import_json(filepath: str, conn) -> dict:
    with open(filepath) as f:
        data = json.load(f)
    metrics = {m["name"]: m["data"] for m in data["data"].get("metrics", [])}
    # write to body_metrics, activity_daily, workouts using existing health_db upsert patterns
    ...
    health_db.set_last_synced(conn, "apple_health_json", date.today().isoformat())
    return summary
```

**7c. launchd WatchPaths plist**

`~/Library/LaunchAgents/com.ironclaw.health-watch.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ironclaw.health-watch</string>
    <key>WatchPaths</key>
    <array>
        <string>/Users/jeff/Library/Mobile Documents/com~apple~CloudDocs/Health</string>
    </array>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/jeff/ironclaw/scripts/watch-health-import.sh</string>
    </array>
    <key>StandardOutPath</key>
    <string>/Users/jeff/Library/Logs/ironclaw/health-watch.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/jeff/Library/Logs/ironclaw/health-watch.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

**7d. `scripts/watch-health-import.sh`**

```bash
#!/bin/bash
WATCH_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Health"
ARCHIVE_DIR="$WATCH_DIR/archive"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$HOME/Library/Logs/ironclaw/health-watch.log"

mkdir -p "$ARCHIVE_DIR"
mkdir -p "$(dirname "$LOG")"

for f in "$WATCH_DIR"/*.json; do
    [[ -f "$f" ]] || continue
    echo "[$(date -Iseconds)] Found: $f" >> "$LOG"
    
    # Validate JSON before processing
    python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" 2>/dev/null || {
        echo "[$(date -Iseconds)] SKIP: invalid JSON $f" >> "$LOG"
        continue
    }
    
    python3 "$SCRIPT_DIR/import-apple-health-json.py" --file "$f" >> "$LOG" 2>&1
    if [[ $? -eq 0 ]]; then
        STAMP=$(date +%Y%m%d_%H%M%S)
        mv "$f" "$ARCHIVE_DIR/export_${STAMP}.json"
        echo "[$(date -Iseconds)] Archived to export_${STAMP}.json" >> "$LOG"
    else
        echo "[$(date -Iseconds)] FAILED: $f (left in place for retry)" >> "$LOG"
    fi
done
```

Key guards:
- Iterates only `*.json` in the watch dir (not the archive subdir)
- JSON validation before import (handles iCloud partial-sync artifacts)
- On failure: leaves file in place for manual retry
- WatchPaths also fires when archive subdir changes — the loop guard (`[[ -f "$f" ]]` on `*.json` in the top-level dir only) prevents infinite loops

**7e. iOS Shortcut guide** (`docs/ios-shortcut-guide.md`)

Steps:
1. Install "Health Auto Export" from App Store (by Lybron Sobers, confirmed "Data Not Collected")
2. Open app → grant HealthKit permissions for: Body Measurements, Activity, Workouts, Sleep
3. Configure export: Format = JSON, Destination = iCloud Drive → Health folder, Date range = last 30 days (or custom)
4. In iOS Shortcuts: create a new Shortcut, add "Health Auto Export" action, configure to trigger the export
5. Set up a "Time of Day" Automation in Shortcuts to run daily (e.g. 6am)
6. First run: verify a `.json` file appears in `iCloud Drive/Health/` from the Mac
7. Mac will auto-import within 1–2 minutes of file appearing

**Install launchd job** (one-time setup, document in guide):
```bash
mkdir -p ~/Library/Logs/ironclaw
cp ~/ironclaw/com.ironclaw.health-watch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ironclaw.health-watch.plist
```

---

### Theme 8 — State of Mind Pillar

**What it is**: Apple Health's daily mood logging (iOS 17+). Two variants: *daily_mood* (once per day, which Jeff logs each morning) and *momentary_emotion* (logged on demand). Three dimensions: **valence** (-1.0 → +1.0, negative to positive), **arousal** (-1.0 → +1.0, calm to activated), plus optional **labels** (descriptors: "content", "stressed", "happy", etc.) and **associations** (life areas: "health", "fitness", "work", etc.).

**Why it's worth adding now**: Jeff logs this daily. Valence correlates naturally with Oura readiness score, HRV, sauna frequency, and workout load — "subjective recovery" alongside the physiological signals already in the DB.

**8a. Schema migration — health.db v4 → v5**

Add to `health_db.py` after the v4 block:
```python
if _version < 5:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS state_of_mind (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT NOT NULL,
            logged_at    TEXT,
            kind         TEXT DEFAULT 'daily_mood',
            valence      REAL,
            arousal      REAL,
            labels       TEXT,
            associations TEXT,
            source       TEXT DEFAULT 'apple_health',
            imported_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_state_of_mind
        ON state_of_mind(date, kind, logged_at);
        CREATE INDEX IF NOT EXISTS idx_state_of_mind_date
        ON state_of_mind(date);
    """)
    conn.execute("PRAGMA user_version = 5")
    conn.commit()
    _version = 5
```

Update `SCHEMA_VERSION = 5` at top of file.

**8b. Import from Apple Health XML (`import-apple-health.py`)**

State of Mind records appear as `HKStateOfMindSample` in the XML export (iOS 17+). The exact attribute layout needs verification against a real export, but the known structure:

```xml
<Record type="HKStateOfMindSample"
    sourceName="Health"
    startDate="2024-01-15 07:30:00 -0800"
    endDate="2024-01-15 07:30:00 -0800">
    <!-- valence, arousal, kind, labels/associations in MetadataEntry children -->
</Record>
```

⚠️ **Verify on first import**: the exact MetadataEntry key names for valence, arousal, and labels are not fully documented in Apple's public API. Extract all MetadataEntry children for a few `HKStateOfMindSample` records from a real export.xml and confirm key names before finalizing the parser. Likely candidates: `HKStateOfMindValence`, `HKStateOfMindArousal`, `HKStateOfMindKind`, `HKStateOfMindLabel0`...N.

Add to the iterparse loop in `import_body_metrics` (or a new `import_state_of_mind` function following the same pattern):
```python
elif rtype == "HKStateOfMindSample":
    records.append({
        "date": elem.get("startDate", "")[:10],
        "logged_at": elem.get("startDate"),
        "kind": _som_kind(elem),       # parse from metadata
        "valence": _som_float(elem, "HKStateOfMindValence"),
        "arousal": _som_float(elem, "HKStateOfMindArousal"),
        "labels": json.dumps(_som_list(elem, "label")),
        "associations": json.dumps(_som_list(elem, "association")),
    })
```

Upsert pattern (same source-yield logic as body_metrics — apple_health yields to any future manual corrections):
```sql
INSERT INTO state_of_mind (date, logged_at, kind, valence, arousal, labels, associations, source)
VALUES (?, ?, ?, ?, ?, ?, ?, 'apple_health')
ON CONFLICT(date, kind, logged_at) DO UPDATE SET
    valence=excluded.valence, arousal=excluded.arousal,
    labels=excluded.labels, associations=excluded.associations
WHERE state_of_mind.source IS 'apple_health'
```

**8c. Import from Health Auto Export JSON (`import-apple-health-json.py`)**

Health Auto Export likely exports State of Mind as a metric named `"state_of_mind"` or similar with per-entry valence/label fields. ⚠️ Verify field names from a real export before finalizing. Add to `METRIC_MAP` and write to `state_of_mind` table using same upsert pattern.

**8d. `mood` subcommand in health_query.py**

```python
def cmd_mood(conn, args):
    # default: last 30 days daily_mood entries
    since = args.since or (date.today() - timedelta(days=30)).isoformat()
    rows = conn.execute("""
        SELECT date, kind, valence, arousal, labels, associations
        FROM state_of_mind
        WHERE date >= ? AND kind = 'daily_mood'
        ORDER BY date DESC
    """, (since,)).fetchall()
    return [dict(r) for r in rows]
```

Args: `--since YYYY-MM-DD`, `--kind daily_mood|momentary_emotion`.

**8e. AGENTS.md Rule 6b table row**

```
| mood | "mood", "how am I feeling", "state of mind", "mental", "emotional" | health_query.py mood |
```

**8f. SKILL.md intent**

Intent 15 (or next available):
```
| 15 — Mood | "mood", "how am I feeling", "state of mind", "emotional state" | mood |
```

**Update migration tests (Theme 5)**: add `test_v4_to_v5()` — start at v4, call `initialize_schema`, verify `state_of_mind` table created, version = 5.

---

## Implementation Order

Recommended execution sequence (each step is independently shippable):

1. **Theme 4** (de-dup) — small, no risk, cleans foundation before other changes touch the same files
2. **Theme 8a** (v5 migration) — add `state_of_mind` table to health_db.py; bump SCHEMA_VERSION
3. **Theme 3** (HRV fix) — adds `backfill_daily_hrv` to health_db.py; run backfill immediately after
4. **Theme 6** (sync-status) — adds `set_last_synced` calls to importers; adds subcommand + AGENTS.md/SKILL.md wiring
5. **Theme 8b–f** (State of Mind import + query + wiring) — XML parser, `mood` subcommand, AGENTS.md/SKILL.md
6. **Theme 5** (migration tests) — add v0→v5, v2→v5, v3→v5, v4→v5 incremental; idempotency
7. **Theme 2** (test checklist) — doc only; add mood queries to the list
8. **Theme 1** (secrets rotation script) — standalone new script; no dependencies
9. **Theme 7** (iOS + launchd + JSON importer) — largest piece; verify Health Auto Export JSON schema (including State of Mind fields) before finalizing

---

## Critical Files for Implementation

- [agents/sample-agent/workspace/health/health_db.py](agents/sample-agent/workspace/health/health_db.py) — add `get_last_synced`/`set_last_synced`/`backfill_daily_hrv`; migration framework reference
- [agents/sample-agent/workspace/health/health_query.py](agents/sample-agent/workspace/health/health_query.py) — add `sync-status` subcommand; follow existing subcommand pattern
- [scripts/oura-sync.py](scripts/oura-sync.py) — remove local sync helpers; call `backfill_daily_hrv` at end of `sync_sleep_sessions`; model for new JSON importer
- [scripts/import-apple-health.py](scripts/import-apple-health.py) — de-dup source guard; write to sync_state; model for JSON importer structure
- [scripts/import-evernote-workouts.py](scripts/import-evernote-workouts.py) — write to sync_state after successful import
- [agents/sample-agent/workspace/AGENTS.md](agents/sample-agent/workspace/AGENTS.md) — add sync-status to Rule 6b table (compact table format, not new block)
- [agents/sample-agent/workspace/skills/health-query/SKILL.md](agents/sample-agent/workspace/skills/health-query/SKILL.md) — add Intent 14 for sync-status
