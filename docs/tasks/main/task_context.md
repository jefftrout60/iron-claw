# Task Context: Health Hardening + iOS Sync Sprint
Branch: main | Date: 2026-04-30

## Feature Summary

Hardening sprint addressing 7 themes flagged by architecture and code reviews:
1. Container secrets rotation (OpenAI, Telegram, Gmail)
2. iMessage manual test checklist (5 new subcommands)
3. HRV backfill + oura-sync.py fix (avg_hrv_rmssd permanently NULL)
4. Source-priority de-dup (get_last_synced copy-paste; apple_health source guard)
5. Migration path tests (v0→v4, incremental, idempotency)
6. sync-status subcommand + Apple Health/Evernote write to sync_state
7. iOS Shortcut → iCloud Drive → launchd WatchPaths auto-import

**Scope doc**: `docs/tasks/main/concepts/scope.md`

---

## Architecture Patterns

### health_db.py — schema hub
`agents/sample-agent/workspace/health/health_db.py`

- `SCHEMA_VERSION = 4`, `PRAGMA user_version` gates migrations
- Migration blocks: `if _version < N: executescript(...); conn.execute("PRAGMA user_version = N"); conn.commit()`
- Two try/except column-addition patches (outside version gates, applied on every connection)
- `sync_state` table: `resource TEXT PRIMARY KEY, last_synced TEXT, next_token TEXT`
- Written by Withings + Oura; NOT written by Apple Health or Evernote importers
- `get_last_synced` / `set_last_synced` currently copy-pasted in withings-sync.py and oura-sync.py (comment says "mirrors oura-sync.py exactly")

### health_query.py — agent query interface
`agents/sample-agent/workspace/health/health_query.py`

- `argparse` with `add_subparsers(dest="command", required=True)`, flat if/elif dispatch
- All output: `print(json.dumps(result_dict))` or `sys.exit(1)` with error JSON
- Existing subcommands: `lab-trend`, `oura-window`, `search`, `blood-pressure`, `bp-log`, `body-metrics`, `activity`, `workouts`, `workout-exercises`, `tags`
- No `sync-status` subcommand exists; `sync_state` table is never read here

### sync_state pattern (withings-sync.py + oura-sync.py)
Both scripts copy-paste this helper pair:
```python
def get_last_synced(conn, resource):
    row = conn.execute("SELECT last_synced FROM sync_state WHERE resource = ?", (resource,)).fetchone()
    return row[0] if row else None

def set_last_synced(conn, resource, value):
    conn.execute("INSERT OR REPLACE INTO sync_state (resource, last_synced) VALUES (?, ?)", (resource, value))
    conn.commit()
```
These belong in `health_db.py`.

### Source-priority conflict resolution (import-apple-health.py)
Apple Health yields to any other source via WHERE guard:
```sql
ON CONFLICT(date, time) DO UPDATE SET
    weight_lbs = excluded.weight_lbs,
    source = excluded.source
WHERE body_metrics.source IS 'apple_health'
```
This guard is duplicated twice in `import-apple-health.py` (weight branch lines ~284-306, fat_ratio branch lines ~295-308). The two branches are structurally identical. Withings uses `ON CONFLICT DO UPDATE` with no source guard (always wins). Oura and Evernote own their own tables exclusively.

### Migration framework
`health_db.py:280-381` — four migration blocks:
- `< 1`: stamp only (tables created by IF NOT EXISTS above)
- `< 2`: creates body_metrics + indexes
- `< 3`: creates activity_daily, workouts, indexes
- `< 4`: creates workout_exercises, oura_tags, indexes

Two column-addition patches (try/except, outside version gates, applied unconditionally):
```python
conn.execute("ALTER TABLE lab_markers ADD COLUMN canonical_unit TEXT")
conn.execute("ALTER TABLE health_knowledge ADD COLUMN raw_transcript TEXT")
```

**Test gap**: existing test suite only calls `initialize_schema(conn)` on fresh in-memory DBs (always v0→v4 in one pass). No test starts at an intermediate version.

### HRV gap
`oura_daily.avg_hrv_rmssd` exists as a column but is permanently NULL. `oura_sleep_sessions.avg_hrv` contains the data. Selection logic: prefer the session where `type = 'long_sleep'`; fallback to longest `duration_seconds`. For historical backfill, run a single UPDATE after joining sessions. For new syncs, populate in `oura-sync.py` when writing oura_daily rows.

### launchd pattern (existing: com.ironclaw.health-sync)
`scripts/daily-health-sync.sh` runs backup → oura-sync → withings-sync at 03:00 via `StartCalendarInterval`. The WatchPaths pattern is the same plist format but uses `<key>WatchPaths</key>` instead of `StartCalendarInterval` to fire on filesystem change.

### AGENTS.md Rule 6b pattern
`agents/sample-agent/workspace/AGENTS.md` — compact table format (not separate blocks). `sync-status` needs a new table row + a trigger phrase like "is my data up to date" or "when did I last sync".

### SKILL.md intent pattern
`agents/sample-agent/workspace/skills/health-query/SKILL.md` — Intent Classification table row + Intent section with exec block.

---

## Dependencies

### Files Modified
| File | Change |
|------|--------|
| `agents/sample-agent/workspace/health/health_db.py` | Add `get_last_synced`/`set_last_synced`; no schema change |
| `agents/sample-agent/workspace/health/health_query.py` | Add `sync-status` subcommand |
| `scripts/withings-sync.py` | Remove local `get_last_synced`/`set_last_synced`; call health_db versions |
| `scripts/oura-sync.py` | Same; also populate `avg_hrv_rmssd` when writing oura_daily rows |
| `scripts/import-apple-health.py` | De-dup source guard; write to sync_state after successful import |
| `scripts/import-evernote-workouts.py` | Write to sync_state after successful import |
| `agents/sample-agent/workspace/AGENTS.md` | Add sync-status row to Rule 6b table |
| `agents/sample-agent/workspace/skills/health-query/SKILL.md` | Add sync-status intent |
| `agents/sample-agent/.env` | New credential values (rotated) |

### New Files
| File | Purpose |
|------|---------|
| `scripts/rotate-container-secrets.sh` | Prompts for new values, updates .env, restarts container |
| `scripts/watch-health-import.sh` | Triggered by launchd WatchPaths; validates + imports XML; archives file |
| `com.ironclaw.health-watch.plist` | launchd WatchPaths job for iCloud Drive folder |
| `docs/ios-shortcut-guide.md` | Step-by-step instructions to build iOS Shortcut |
| `agents/sample-agent/workspace/health/test_migration_paths.py` | Migration path tests |
| `docs/tasks/main/imessage-test-checklist.md` | Manual test queries + expected output shapes |

### Test Files Modified
| File | Change |
|------|--------|
| Existing test files | No change — existing 172 tests unaffected |
| New: `test_migration_paths.py` | v0→v4, v2→v4, v3→v4, idempotency tests |

---

## Implementation Approaches

### 1. Secrets rotation script
`scripts/rotate-container-secrets.sh`:
- Read current `.env` to identify which keys exist
- Prompt for new value per key (OpenAI, Telegram, Gmail)
- Use `sed -i` or Python to update values in-place in `.env`
- Stop container, update `.env`, restart via `./scripts/compose-up.sh sample-agent -d`
- Print confirmation with last 4 chars of each new key

### 2. iMessage test checklist
Markdown file with ~12 queries covering:
- `body-metrics`: "what's my current weight", "show my body fat trend"
- `activity`: "how many steps did I take this week", "how much sunlight did I get today"
- `workouts`: "what workouts did I do this week", "show my recent workouts"
- `workout-exercises`: "what did I do at the gym last Tuesday", "show my squat history"
- `tags`: "how many sauna sessions this month", "show my sleep tags this week"

### 3. HRV fix
**Backfill** (one-time, run after deploy):
```sql
UPDATE oura_daily SET avg_hrv_rmssd = (
    SELECT avg_hrv FROM oura_sleep_sessions
    WHERE oura_sleep_sessions.date = oura_daily.date
      AND (type = 'long_sleep' OR (
        SELECT COUNT(*) FROM oura_sleep_sessions s2
        WHERE s2.date = oura_daily.date AND s2.type = 'long_sleep'
      ) = 0)
    ORDER BY CASE WHEN type = 'long_sleep' THEN 0 ELSE 1 END,
             duration_seconds DESC
    LIMIT 1
)
WHERE avg_hrv_rmssd IS NULL;
```
**oura-sync.py fix**: when writing oura_daily row, include avg_hrv_rmssd by selecting from in-memory session data (already fetched in the same sync run).

### 4. Source-priority de-dup
- Add to `health_db.py`:
  ```python
  def get_last_synced(conn, resource): ...
  def set_last_synced(conn, resource, value): ...
  ```
- Update `withings-sync.py` and `oura-sync.py`: remove local helpers, import and call health_db versions
- Extract the apple_health source guard in `import-apple-health.py` to a module-level SQL snippet or helper so it's defined once

### 5. Migration path tests
New `test_migration_paths.py` pattern:
```python
def make_conn_at_version(n):
    """Create in-memory DB stamped at version n with its expected tables."""
    conn = sqlite3.connect(":memory:")
    # apply migrations 1..n manually
    conn.execute(f"PRAGMA user_version = {n}")
    return conn

def test_v2_to_v4():
    conn = make_conn_at_version(2)
    health_db.initialize_schema(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    # verify v3 and v4 tables exist

def test_column_patches_idempotent():
    conn = make_conn_at_version(4)
    health_db.initialize_schema(conn)  # should not raise
```

### 6. sync-status subcommand
```python
# health_query.py
def cmd_sync_status(conn, args):
    rows = conn.execute("SELECT resource, last_synced FROM sync_state ORDER BY resource").fetchall()
    result = {}
    stale_threshold = 2  # days
    for row in rows:
        last = row["last_synced"]
        days_ago = (date.today() - date.fromisoformat(last)).days if last else None
        stale = days_ago is not None and days_ago > stale_threshold and row["resource"] in ("withings", "oura_daily", "oura_sleep", "oura_heartrate", "oura_tags")
        result[row["resource"]] = {"last_synced": last, "days_ago": days_ago, "stale": stale}
    return result
```
Apple Health and Evernote resources added to sync_state by their importers (no stale flag for these).

### 7. iOS Shortcut + launchd watcher
**launchd plist** (`com.ironclaw.health-watch.plist`):
```xml
<key>WatchPaths</key>
<array>
    <string>/Users/jeff/Library/Mobile Documents/com~apple~CloudDocs/Health</string>
</array>
<key>ProgramArguments</key>
<array>
    <string>/Users/jeff/ironclaw/scripts/watch-health-import.sh</string>
</array>
```
**watch-health-import.sh**:
1. Scan iCloud Drive/Health/ for `*.xml` files not yet archived
2. Validate XML (check root element)
3. Run `python3 scripts/import-apple-health.py --file <path>`
4. On success: move to `iCloud Drive/Health/archive/export_YYYYMMDD.xml`
5. Log result to `~/Library/Logs/ironclaw/health-watch.log`

**iOS Shortcut**: *[pending web research — see note below]*
> NOTE: iOS Shortcuts cannot export the Apple Health XML (export.xml) format directly. The export.xml format is only available via the Health app's built-in share flow. Options being evaluated: (a) Health Auto Export third-party app which can export to iCloud Drive on a schedule, (b) a custom Shortcut that exports health data in CSV/JSON format requiring an updated importer. Will update once research returns.

---

## Impact Summary

**High-impact files**: health_db.py (sync helper move), health_query.py (new subcommand), oura-sync.py (HRV fix + helper removal), AGENTS.md (table update)

**Risk areas**:
- HRV backfill UPDATE: must use `WHERE avg_hrv_rmssd IS NULL` guard to avoid overwriting any manually-set values
- Secrets rotation: script must stop container before modifying `.env` to avoid partial state
- launchd WatchPaths: fires on ANY change in watched directory (including archive subdirectory writes) — watcher script must be idempotent and must not re-process archived files
- Migration tests: need to build partial-schema DBs correctly (applying only N migrations) without triggering the current `initialize_schema` logic

**No risk**: existing 172 tests unaffected; no schema changes; all changes are additive or surgical refactors

---

## External Research

### launchd WatchPaths
- Standard macOS mechanism; plist with `<key>WatchPaths</key>` fires program on any change in listed paths
- Fires on file create, modify, delete — watcher script must guard against partial/in-progress files
- Install: `launchctl load ~/Library/LaunchAgents/com.ironclaw.health-watch.plist`
- iCloud Drive path: `~/Library/Mobile Documents/com~apple~CloudDocs/Health/`
- The WatchPaths trigger fires when iCloud finishes syncing the file (file appears/changes locally), not when upload begins on phone — no race condition from phone side

### iOS Shortcut health export (research findings)
**Key finding: iOS Shortcuts cannot trigger the full Apple Health XML export.** The `export.xml`/ZIP is only available via Health app → Profile → Export All Health Data (manual, user-initiated). Apple has not exposed this as a Shortcuts action.

**What Shortcuts CAN do**: "Find Health Samples" reads individual metrics one at a time and writes to a file — but this produces custom JSON/CSV, not the canonical XML format, and requires one action per metric type.

**Option A: Semi-automated (one-tap Shortcut, existing importer unchanged)**
- Shortcut deep-links to Health app export screen (user confirms one tap)
- iOS share sheet → Save to Files → iCloud Drive/Health/
- Mac WatchPaths detects new ZIP, unzips, runs existing `import-apple-health.py`
- Full XML format — existing importer works without changes
- Con: ~1 manual tap per month; export ZIP can be multi-GB for years of data

**Option B: Health Auto Export app + extend importer (fully automated)**
- "Health Auto Export" (Liftcode, ~$4) exports JSON/CSV to iCloud Drive on a schedule via Shortcuts Automation
- Supports incremental/delta exports (much smaller files than full XML)
- Con: requires app purchase; JSON format is NOT the same as export.xml; requires adding a JSON import path to `import-apple-health.py`
- Fully zero-touch after setup

**Selected: Option B — Health Auto Export app + extend importer**
Privacy label confirmed "Data Not Collected" (developer: Lybron Sobers). Fully automated zero-touch path. Requires adding a JSON import path to `import-apple-health.py` alongside existing XML parser. Health Auto Export exports incremental JSON to iCloud Drive on a Shortcuts-based schedule.

---

## Assessed Complexity: STANDARD

| Signal | Value | Score |
|--------|-------|-------|
| Files impacted | ~10 | HIGH |
| Pattern match | All changes follow clear existing patterns | LOW |
| Components crossed | health_db, health_query, 2 sync scripts, 2 importers, AGENTS.md, SKILL.md, new plist/script | HIGH |
| Data model changes | None (sync_state exists; avg_hrv_rmssd column exists) | LOW |
| Integration points | iCloud Drive (new), launchd WatchPaths (new pattern but well-documented) | MEDIUM |
| External complexity | iOS Shortcut mechanism uncertain; all other items are internal | MEDIUM |

**Rationale for STANDARD**: No schema changes, no hard-stops, all items follow established patterns. The iOS Shortcut piece is the only external unknown but is bounded. 10 files but most changes are surgical (add a function, add a subcommand, remove duplication). No architectural overhaul.
