# Implementation Plan: Health DB Hardening Sprint

*Branch: main | Date: 2026-05-01 | Depth: comprehensive*
*Scope: `docs/tasks/main/concepts/scope_health_hardening_sprint.md`*
*Context: `docs/tasks/main/task_context.md`*

---

## Overview

13-item hardening sprint to make health.db trustworthy before building `daily_brief` and correlation queries on top. Covers DB integrity (new columns, FTS rebuild), sync reliability (oura-sync refactor), search quality (topics indexed), observability (DATA_CARD), agent behavior (Rule 6c), and ops hygiene (secrets, Subspace).

**One item struck before planning**: Item #5 (lab_results ON CONFLICT) is already correctly implemented in `import-blood-labs.py` — `ON CONFLICT DO UPDATE` with `imported_at` excluded from SET clause.

**Architectural decisions (approved 2026-05-01)**:
- v6 migration: single block (all column additions + FTS rebuild in one migration)
- oura-sync error signaling: `fetch_all` raises `FetchError`; per-chunk last_synced advance

---

## Current State

| Component | Current State | Problem |
|-----------|--------------|---------|
| `lab_results.in_range_flag` | Column does not exist | Agent computes ranges at query time; "what's out of range?" requires LLM reasoning |
| `lab_markers.canonical_unit` | Column exists; importer populates it | No alias map — provider name variants bypass normalization |
| `health_knowledge.enrichment_status` | Column does not exist | Silent topic extraction failures; failed rows not queryable |
| `health_knowledge.topics_text` | Column does not exist; topics stored as JSON blob | FTS5 index excludes topics; topic-tag queries miss relevant episodes |
| `health_knowledge_fts` | Indexes `episode_title` + `summary` only (`health_db.py:100-106`) | FTS miss on topic tags not repeated in summary text |
| `oura-sync.py fetch_all` | Returns partial results on error; `break` on failure (`oura-sync.py:87-124`) | `set_last_synced` advances even on partial fetch; OVERLAP_DAYS=1 is the only protection |
| `oura-sync.py` instance guard | No guard | Overlapping launchd runs can corrupt DB |
| `oura_heartrate` | No retention policy; ~17K rows/year | Will hit 1M rows in ~59 years; no cleanup path today |
| `DATA_CARD.md` | Does not exist | Agent must introspect schema every session to understand what data exists |
| Rule 6c in `health_query.py` | Asks "now or past?" unconditionally | Bare-number readings dropped if user doesn't respond |
| `SCHEMA_VERSION` | 5 (`health_db.py:54`) | All new columns require v6 migration block |

---

## Desired End State

- `lab_results` has `in_range_flag TEXT` (`'in'`/`'borderline'`/`'out'`/NULL); populated at import time
- `health_knowledge` has `enrichment_status TEXT` and `topics_text TEXT`; existing rows backfilled
- `health_knowledge_fts` indexes `episode_title`, `summary`, `topics_text`; all three triggers updated
- `oura-sync.py`: `fetch_all` raises `FetchError` on failure; per-chunk `last_synced` tracking; `mkdir` lock guard; `cleanup_old_heartrate()` removes rows > 90 days
- `markers_canonical.json` seeded from live DB; alias lookup wired into `import-blood-labs.py`
- `scripts/generate-data-card.py` produces `agents/sample-agent/workspace/health/DATA_CARD.md` on each sync
- `health_query.py` Rule 6c defaults to "now" for bare numbers and unambiguous inputs
- `SCHEMA_VERSION = 6`; all new columns in single v6 migration block
- State of Mind XML key names confirmed; behavioral fixture tests written
- Container secrets rotated; Subspace installed on Freedom

---

## Out of Scope

- New health pillars (weight entry, DEXA, macros, supplements)
- New query subcommands (daily_brief, correlations view, trends snapshot, HRV trend)
- Oura context injection, cross-pillar correlation skill
- Weekly summary email extension, agent access control
- Bulk podcast ingestion, embedding/vector search
- events table, active_energy column, all-time Evernote backfill
- Migration ladder refactor (not needed until v8)

---

## Technical Approach

### Phase 1 — Ops (no code)
Pure operational steps. Do first to clear the immediate list.

1. **Rotate container secrets**: run `scripts/rotate-container-secrets.sh`; update OpenAI, Telegram, Gmail credentials; restart container and verify Telegram responds.
2. **Install Subspace**: macOS multi-agent GUI; install on Freedom machine.

---

### Phase 2 — State of Mind XML Verification
External dependency; do early since it may reveal surprises.

3. **Apple Health XML export**: export from Health app → share → Save to Files. Run:
   ```bash
   python3 scripts/import-apple-health.py --debug --file ~/Downloads/export.zip
   ```
   Grep output for `HKStateOfMindSample` to confirm MetadataEntry key names. Update parser if key names differ from current assumptions.

4. **Behavioral fixture tests**: once key names confirmed, write fixture tests in existing test suite. Pattern: copy a real `HKStateOfMindSample` XML node into a test fixture, assert parsed valence/labels/date match expected values.

---

### Phase 3 — v6 Migration Block
All schema changes in a single migration. Test on a copy of the live DB before deploying.

#### 3a. Migration block in `health_db.py`

After line 406 (`_version = 5`), add:
```python
# ---------- v6: in_range_flag, enrichment_status, topics_text + FTS rebuild --
if _version < 6:
    conn.executescript("""
        ALTER TABLE lab_results ADD COLUMN in_range_flag TEXT;
        ALTER TABLE health_knowledge ADD COLUMN enrichment_status TEXT;
        ALTER TABLE health_knowledge ADD COLUMN topics_text TEXT;
    """)
    # Rebuild FTS to include topics_text
    conn.executescript("""
        DROP TABLE IF EXISTS health_knowledge_fts;
        CREATE VIRTUAL TABLE health_knowledge_fts USING fts5(
            episode_title, summary, topics_text,
            content='health_knowledge', content_rowid='rowid',
            tokenize='porter unicode61'
        );
        DROP TRIGGER IF EXISTS hk_ai;
        DROP TRIGGER IF EXISTS hk_ad;
        DROP TRIGGER IF EXISTS hk_au;
    """)
    # Recreate triggers with 3 columns
    conn.execute("""CREATE TRIGGER IF NOT EXISTS hk_ai AFTER INSERT ON health_knowledge BEGIN
        INSERT INTO health_knowledge_fts(rowid, episode_title, summary, topics_text)
        VALUES (new.rowid, new.episode_title, new.summary, COALESCE(new.topics_text, ''));
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS hk_ad AFTER DELETE ON health_knowledge BEGIN
        INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary, topics_text)
        VALUES ('delete', old.rowid, old.episode_title, old.summary, COALESCE(old.topics_text, ''));
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS hk_au AFTER UPDATE ON health_knowledge BEGIN
        INSERT INTO health_knowledge_fts(health_knowledge_fts, rowid, episode_title, summary, topics_text)
        VALUES ('delete', old.rowid, old.episode_title, old.summary, COALESCE(old.topics_text, ''));
        INSERT INTO health_knowledge_fts(rowid, episode_title, summary, topics_text)
        VALUES (new.rowid, new.episode_title, new.summary, COALESCE(new.topics_text, ''));
    END""")
    # Repopulate FTS from base table
    conn.execute("""
        INSERT INTO health_knowledge_fts(rowid, episode_title, summary, topics_text)
        SELECT rowid, episode_title, summary, COALESCE(topics_text, '')
        FROM health_knowledge
    """)
    # Backfill enrichment_status from existing topics data
    conn.execute("""
        UPDATE health_knowledge
        SET enrichment_status = CASE
            WHEN topics IS NOT NULL AND topics != '[]' THEN 'done'
            ELSE 'pending'
        END
        WHERE enrichment_status IS NULL
    """)
    # Backfill topics_text from existing topics JSON
    # Python loop needed; can't call json_each reliably in older SQLite
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    _version = 6
```
Also update `SCHEMA_VERSION = 5` → `6` at line 54.

**topics_text backfill**: `executescript` can't call Python. After the migration block, add a Python backfill loop in `initialize_schema`:
```python
# Backfill topics_text for existing rows that have topics JSON but no topics_text
import json as _json
rows = conn.execute(
    "SELECT rowid, topics FROM health_knowledge WHERE topics_text IS NULL AND topics IS NOT NULL"
).fetchall()
for row in rows:
    try:
        tags = _json.loads(row[1])
        topics_text = ' '.join(tags) if isinstance(tags, list) else ''
    except Exception:
        topics_text = ''
    conn.execute(
        "UPDATE health_knowledge SET topics_text = ? WHERE rowid = ?",
        (topics_text, row[0])
    )
if rows:
    conn.commit()
```

#### 3b. Reference-range flagging in `import-blood-labs.py`

Add at module level:
```python
_BORDERLINE_PCT = 0.10

def _compute_flag(value, ref_low, ref_high):
    if ref_low is None and ref_high is None:
        return None
    if ref_low is not None and value < ref_low:
        return 'borderline' if (ref_low - value) / ref_low <= _BORDERLINE_PCT else 'out'
    if ref_high is not None and value > ref_high:
        return 'borderline' if (value - ref_high) / ref_high <= _BORDERLINE_PCT else 'out'
    return 'in'
```

In the INSERT statement, add `in_range_flag` to the column list and the `DO UPDATE SET` clause:
```python
flag = _compute_flag(row["value"], row.get("reference_low"), row.get("reference_high"))
# INSERT adds: in_range_flag to columns + values
# DO UPDATE SET adds: in_range_flag = excluded.in_range_flag
```

#### 3c. `enrichment_status` + `topics_text` in `health_store.py`

In `append_entry()`, before building the INSERT:
```python
topics = extract_topics(entry["summary"], api_key, model)
enrichment_status = 'done' if topics else 'failed'
topics_text = ' '.join(topics) if topics else ''
```

Add `enrichment_status` and `topics_text` to the INSERT columns and values. The `INSERT OR IGNORE` dedup means existing rows aren't re-enriched — the backfill in the migration handles those.

---

### Phase 4 — Units Alias Map

5. **Seed `markers_canonical.json`**: query live DB for actual marker names:
   ```bash
   python3 -c "
   import sqlite3, json
   conn = sqlite3.connect('path/to/health.db')
   names = [r[0] for r in conn.execute('SELECT DISTINCT name FROM lab_markers ORDER BY name')]
   print(json.dumps(names, indent=2))
   "
   ```
   Build `scripts/markers_canonical.json` with alias groups based on actual names found.

6. **Wire alias lookup in `import-blood-labs.py`**: load the map at startup; before upsert to `lab_markers`, normalize the marker name through the alias lookup:
   ```python
   CANONICAL = json.load(open("scripts/markers_canonical.json"))
   ALIAS_TO_CANONICAL = {alias: canon for canon, aliases in CANONICAL["aliases"].items() for alias in aliases}

   def normalize_marker_name(name):
       return ALIAS_TO_CANONICAL.get(name, name)
   ```

---

### Phase 5 — oura-sync.py Reliability

Three independent changes to `oura-sync.py`. Apply together; test as a unit.

#### 5a. `fetch_all` raises on failure

```python
class FetchError(Exception):
    pass
```

In `fetch_all`, replace every `break` that signals an error:
- Network exception: `raise FetchError(f"network error: {e}") from e`
- Non-200 after retry: `raise FetchError(f"HTTP {resp.status_code} for {resource}")`

Keep the `break` that exits on missing `next_token` (that's normal pagination end, not an error).

#### 5b. Per-chunk last_synced tracking in each sync function

Each of the four sync functions (`sync_daily_summaries`, `sync_sleep_sessions`, `sync_heartrate`, `sync_tags`) follows this pattern:

```python
def sync_daily_summaries(conn, start, end, headers):
    last_good_date = None
    for chunk_start, chunk_end in date_chunks(start, end):
        try:
            data = fetch_all("daily_activity", chunk_start, chunk_end, headers)
            # ... write rows ...
            last_good_date = chunk_end
        except FetchError as e:
            logger.warning(f"oura fetch failed at chunk {chunk_start}–{chunk_end}: {e}")
            break
    if last_good_date:
        safe_date = (date.fromisoformat(last_good_date) - timedelta(days=OVERLAP_DAYS)).isoformat()
        set_last_synced(conn, "oura_daily", safe_date)
    # If last_good_date is None: zero chunks succeeded; don't advance last_synced
```

#### 5c. `mkdir` single-instance guard

At the top of the script's `main()` function (or as a module-level guard before `main()` is called):
```python
import os

_LOCK_DIR = os.path.expanduser("~/Library/Logs/ironclaw/.oura-sync.lock")

def _acquire_lock():
    try:
        os.makedirs(os.path.dirname(_LOCK_DIR), exist_ok=True)
        os.mkdir(_LOCK_DIR)
    except OSError:
        print("oura-sync: another instance is running, exiting", file=sys.stderr)
        sys.exit(0)

def _release_lock():
    try:
        os.rmdir(_LOCK_DIR)
    except OSError:
        pass
```

Wrap `main()` call:
```python
_acquire_lock()
try:
    main()
finally:
    _release_lock()
```

#### 5d. `oura_heartrate` retention

Add to `health_db.py` (or inline in `oura-sync.py`):
```python
_HEARTRATE_RETENTION_DAYS = 90

def cleanup_old_heartrate(conn):
    cutoff = (date.today() - timedelta(days=_HEARTRATE_RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM oura_heartrate WHERE timestamp < ?", (cutoff,))
    conn.commit()
```

Call `cleanup_old_heartrate(conn)` at the end of each successful `sync_heartrate()` run (after `set_last_synced`).

---

### Phase 6 — DATA_CARD.md Auto-Generation

New file: `scripts/generate-data-card.py`

```python
#!/usr/bin/env python3
"""Generate DATA_CARD.md from health.db for agent consumption."""
import sqlite3, os, textwrap
from datetime import datetime

DB_PATH = os.path.expanduser("~/ironclaw/agents/sample-agent/workspace/health/health.db")
OUT_PATH = os.path.expanduser("~/ironclaw/agents/sample-agent/workspace/health/DATA_CARD.md")

TABLES = [
    ("lab_results",       "Lab results",           "date"),
    ("oura_daily",        "Oura daily summaries",  "date"),
    ("oura_sleep_sessions","Oura sleep sessions",  "date"),
    ("oura_heartrate",    "Oura heart rate",       "timestamp"),
    ("body_metrics",      "Body metrics",           "date"),
    ("blood_pressure",    "Blood pressure",         "date"),
    ("activity_daily",    "Activity (steps/daylight)", "date"),
    ("workouts",          "Workouts",               "date"),
    ("state_of_mind",     "State of mind",          "date"),
    ("health_knowledge",  "Health knowledge (podcasts)", "date"),
    ("evernote_workouts", "Evernote workout notes", "date"),
]

def run():
    conn = sqlite3.connect(DB_PATH)
    sync = {r[0]: r[1] for r in conn.execute("SELECT resource, last_synced FROM sync_state")}
    lines = [f"# Health DB Data Card", f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"]
    lines.append("## Tables\n")
    lines.append("| Table | Rows | Earliest | Latest | Last Sync |")
    lines.append("|-------|------|----------|--------|-----------|")
    for table, label, date_col in TABLES:
        try:
            row = conn.execute(
                f"SELECT COUNT(*), MIN({date_col}), MAX({date_col}) FROM {table}"
            ).fetchone()
            count, earliest, latest = row
            last_sync = sync.get(table, sync.get(table.replace("oura_", ""), "—"))
            lines.append(f"| {label} | {count:,} | {earliest or '—'} | {latest or '—'} | {last_sync or '—'} |")
        except Exception:
            lines.append(f"| {label} | — | — | — | — |")
    with open(OUT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"DATA_CARD.md written to {OUT_PATH}")

if __name__ == "__main__":
    run()
```

Wire into `scripts/daily-health-sync.sh` as the final step:
```bash
python3 "$IRONCLAW_DIR/scripts/generate-data-card.py"
```

---

### Phase 7 — Rule 6c State Machine

In `health_query.py`, the body-metrics write command (and any iMessage entry command) currently asks "is this now or a past date?" unconditionally. Refactor to:

```python
import re

_TEMPORAL_AMBIGUOUS = re.compile(
    r'\b(last\s+\w+|the\s+other\s+day|a\s+few\s+days?\s+ago|earlier\s+this\s+week'
    r'|yesterday|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
    re.IGNORECASE
)
_TEMPORAL_CLEAR_PAST = re.compile(
    r'\b(\d{4}-\d{2}-\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d+)\b',
    re.IGNORECASE
)

def classify_temporal(text):
    if _TEMPORAL_CLEAR_PAST.search(text):
        return 'past_explicit'   # extract the date, use it
    if _TEMPORAL_AMBIGUOUS.search(text):
        return 'ambiguous'       # ask once
    return 'now'                 # default: use today
```

Dispatch:
- `'now'`: use `date.today()`, do not ask
- `'past_explicit'`: parse the date from the message, use it, do not ask
- `'ambiguous'`: ask "What date? (or reply 'today' to use today's date)"

Update `AGENTS.md` Rule 6c wording to match: "Bare numbers and 'today/this morning' → always log as now. Only ask for clarification when the message contains an ambiguous relative date ('last week', 'Tuesday', 'the other day')."

---

## Implementation Phases (Execution Order)

| Phase | Items | Risk | Est. Size |
|-------|-------|------|-----------|
| 1 — Ops | Secrets rotation, Subspace install | None | S |
| 2 — State of Mind XML | Export, debug, confirm keys, fixture tests | Low | S |
| 3 — v6 Migration | in_range_flag, enrichment_status, topics_text, FTS rebuild, backfill | Medium | M |
| 4 — Units alias map | markers_canonical.json seed + lab importer wire | Low | S |
| 5 — oura-sync | fetch_all raises, per-chunk last_synced, mkdir guard, heartrate retention | High | M |
| 6 — DATA_CARD | generate-data-card.py + wire into sync | Low | S |
| 7 — Rule 6c | Temporal classifier in health_query.py + AGENTS.md | Low | S |
| 8 — Tests | Tests for all new behaviors | Medium | M |

**Rationale for order**: ops first (no risk), state of mind early (external dependency), schema changes before importers that need the new columns, oura-sync last among code changes (highest risk), tests close out.

---

## Testing Strategy

### Existing suite (must pass, no regressions)
`python3 -m unittest discover -s agents/sample-agent/workspace/health -q`
Currently 195 tests. All must pass after each phase.

### New tests to add

**Phase 3 — DB migration**
- `test_v5_to_v6_migration`: start at v5, call `initialize_schema`, assert `PRAGMA user_version = 6`, assert three new columns exist on correct tables
- `test_in_range_flag_in`: value within range → `'in'`
- `test_in_range_flag_out_low`, `test_in_range_flag_out_high`: value outside → `'out'`
- `test_in_range_flag_borderline`: value within 10% of boundary → `'borderline'`
- `test_in_range_flag_no_reference`: both refs NULL → NULL
- `test_enrichment_status_done`: `append_entry` with successful topic extraction → `enrichment_status='done'`
- `test_enrichment_status_failed`: `append_entry` with empty topics → `enrichment_status='failed'`
- `test_topics_text_populated`: `topics_text` = space-joined topics string
- `test_fts_topics_match`: insert row with topics `['apob', 'ldl']`, search `'apob'`, verify hit

**Phase 5 — oura-sync**
- `test_fetch_all_raises_on_http_error`: mock requests to return 500 → `FetchError` raised
- `test_fetch_all_raises_on_network_error`: mock requests to raise `ConnectionError` → `FetchError` raised
- `test_sync_advances_to_last_good_chunk`: simulate 3 chunks where chunk 2 fails → `last_synced` set to chunk 1 end
- `test_sync_no_advance_on_total_failure`: all chunks fail → `set_last_synced` not called
- `test_cleanup_old_heartrate`: insert rows spanning 180 days → after cleanup, only rows within 90 days remain

**Phase 2 — State of Mind XML (after key names confirmed)**
- `test_parse_hkstateofmind_sample`: fixture XML node → assert valence, labels, date

### Test locations
New tests go in `agents/sample-agent/workspace/health/test_*.py`, discovered by the existing `unittest discover` command.

---

## Data Architecture

### Schema v6 additions

```sql
-- lab_results (existing table)
ALTER TABLE lab_results ADD COLUMN in_range_flag TEXT;
-- values: 'in', 'borderline', 'out', NULL (when reference range unavailable)

-- health_knowledge (existing table)
ALTER TABLE health_knowledge ADD COLUMN enrichment_status TEXT;
-- values: 'done' (topics extracted), 'failed' (extraction failed), 'pending' (needs backfill)
ALTER TABLE health_knowledge ADD COLUMN topics_text TEXT;
-- value: space-joined list of topic tags from topics JSON array
```

### FTS5 index update

Old: `health_knowledge_fts(episode_title, summary)`
New: `health_knowledge_fts(episode_title, summary, topics_text)`

Drop + recreate in v6 migration (content table: must repopulate after recreate).

---

## Assumptions Made (no clarification collected)

| Assumption | Rationale |
|------------|-----------|
| oura_heartrate retention = 90 days | Enough for trend analysis; avoids unbounded growth; easily configurable |
| Borderline threshold = 10% | Standard clinical approximation; a constant makes it easy to tune |
| DATA_CARD.md trigger = daily-health-sync.sh | Already runs on launchd schedule; minimal plumbing |
| markers_canonical.json seeded from live DB | Alias map must reflect actual marker names, not guessed ones |

---

## Critical Files for Implementation

| File | Role |
|------|------|
| `agents/sample-agent/workspace/health/health_db.py` | Add v6 migration block + topics_text backfill loop; update SCHEMA_VERSION |
| `agents/sample-agent/workspace/health/health_query.py` | Rule 6c temporal classifier; FTS query correct (snippet col index stays at 1) |
| `agents/sample-agent/workspace/skills/podcast-summary/scripts/health_store.py` | Add enrichment_status + topics_text to INSERT; set status at write time |
| `scripts/oura-sync.py` | fetch_all raises FetchError; per-chunk last_synced; mkdir guard; heartrate cleanup |
| `scripts/import-blood-labs.py` | Add in_range_flag computation + column; add alias lookup for marker names |
| `scripts/generate-data-card.py` | New file — full implementation above |
| `agents/sample-agent/workspace/AGENTS.md` | Rule 6c wording update |
