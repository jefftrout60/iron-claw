# Task Context: Health DB Hardening Sprint
Branch: main | Date: 2026-05-01

## Feature Summary

Hardening sprint: DB integrity, sync reliability, search quality, observability, and agent behavior.
Scope doc: `docs/tasks/main/concepts/scope_health_hardening_sprint.md`

**13 active items** (item #5 lab_results ON CONFLICT struck — already correctly implemented):
1. Rotate container secrets
2. Install Subspace on Freedom
3. State of Mind XML export → confirm key names → behavioral tests
4. Reference-range flagging (`in_range_flag` column on lab_results, computed at import)
5. ~~lab_results ON CONFLICT DO UPDATE~~ — **already done** in import-blood-labs.py
6. Units registry alias map (`markers_canonical.json`) + resolution in lab importer
7. `enrichment_status` column on health_knowledge + backfill
8. FTS on health_knowledge.topics — add `topics_text` to FTS5 index + trigger
9. True partial failure tracking in oura-sync.py — full fetch_all refactor (raises on error)
10. `mkdir` single-instance guard in oura-sync.py (flock not available on macOS)
11. oura_heartrate retention policy — cleanup_old_heartrate() helper
12. DATA_CARD.md auto-generation script
13. Rule 6c state machine — default-to-now in health_query.py + AGENTS.md

---

## Architecture Patterns

### health_db.py — schema hub
`agents/sample-agent/workspace/health/health_db.py`

- `SCHEMA_VERSION = 5` (line 54); `PRAGMA user_version` gates migrations
- Migration block pattern (v1–v5):
  ```python
  if _version < N:
      conn.executescript("""DDL...""")
      conn.execute("PRAGMA user_version = N")
      conn.commit()
      _version = N
  ```
- v6 block goes immediately after line 406 (`_version = 5`)
- Two existing try/except column patches (outside version gates): `canonical_unit` on lab_markers, `raw_transcript` on health_knowledge
- `get_last_synced` / `set_last_synced` helpers at lines 413–427

### FTS5 — existing health_knowledge_fts
`agents/sample-agent/workspace/health/health_db.py:100-126`

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS health_knowledge_fts USING fts5(
    episode_title, summary,
    content='health_knowledge', content_rowid='rowid',
    tokenize='porter unicode61'
)
```
Three triggers: `hk_ai` (INSERT), `hk_ad` (DELETE), `hk_au` (UPDATE — delete+reinsert).
`topics` is intentionally excluded from the index. Fix: add `topics_text TEXT` column to `health_knowledge`, populate at insert with `' '.join(topics_list)`, extend FTS5 table + all three triggers to include `topics_text`.

### FTS query pattern
`agents/sample-agent/workspace/health/health_query.py:640-677`
```python
def _fts_quote(query: str) -> str:
    return '"' + query.replace('"', '""') + '"'

results = conn.execute("""
    SELECT hk.show, hk.episode_title, hk.date,
           snippet(health_knowledge_fts, 1, '[', ']', '...', 20) AS snippet
    FROM health_knowledge_fts
    JOIN health_knowledge hk ON health_knowledge_fts.rowid = hk.rowid
    WHERE health_knowledge_fts MATCH ?
    ORDER BY rank LIMIT ?
""", (_fts_quote(query), limit)).fetchall()
```
Snippet column index 1 = `summary`. When `topics_text` is added as column 2, index 1 stays correct.

### oura-sync.py — fetch_all and last_synced advance
`scripts/oura-sync.py`

- `OVERLAP_DAYS = 1` (line 47)
- `fetch_all(resource, start_date, end_date, headers)` (lines 87-124): pure HTTP pagination, returns flat `list[dict]`. On network error / non-200, `break` (silent partial return). **No success signal.**
- `set_last_synced` called once per resource at the end of each sync function (lines 231, 270, 292, 319), set to `end - OVERLAP_DAYS`. Advances even if some chunks failed mid-pagination.
- **Refactor plan (Option A — raises)**: Convert `break` paths to `raise FetchError(chunk_end)`. Sync function chunk loop: catch `FetchError`, record `last_good = chunk_end - 1 day`, break. Call `set_last_synced(conn, resource, last_good)` only if at least one chunk succeeded. If zero chunks succeeded, don't advance.

### Single-instance guard — mkdir pattern (NOT flock)
`scripts/watch-health-import.sh:9-15`
```bash
LOCKDIR="$HOME/Library/Logs/ironclaw/.health-watch.lock"
mkdir "$LOCKDIR" 2>/dev/null || exit 0
trap 'rmdir "$LOCKDIR"' EXIT
```
`flock` is not available on macOS. The `mkdir` form is atomic on macOS and is the established pattern. Apply the same to `oura-sync.py` (Python equivalent: `os.mkdir(lockdir)` in a try/except OSError).

### lab_results — already correct ON CONFLICT
`scripts/import-blood-labs.py:184-201`
```sql
INSERT INTO lab_results (marker_id, date, value, reference_low, reference_high, source_sheet)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(marker_id, date) DO UPDATE SET
    value = excluded.value, reference_low = excluded.reference_low,
    reference_high = excluded.reference_high, source_sheet = excluded.source_sheet
WHERE lab_results.value IS NOT excluded.value
   OR lab_results.reference_low IS NOT excluded.reference_low
   OR lab_results.reference_high IS NOT excluded.reference_high
```
`imported_at` not in SET clause → preserved on re-import. ✅ No change needed.

### lab_markers.canonical_unit — already exists
`health_db.py:130-141`: column declared in CREATE TABLE and added via try/except patch.
`import-blood-labs.py:163-177`: extracts unit from trailing parentheses in marker name (e.g. "Ferritin (ng/mL)") and upserts into `lab_markers.canonical_unit`.
**Remaining gap**: no `markers_canonical.json` alias map for cross-provider name variants.

### reference-range flagging — new
No existing flag. `health_query.py:88-95` computes ranges at query time. New: `in_range_flag TEXT` column on `lab_results`, populated at import time in `import-blood-labs.py` using value vs reference_low/reference_high. Values: `'in'`, `'borderline'`, `'out'`, NULL (if no reference range available). Borderline = within 10% of a range boundary (to be defined as a constant).

### enrichment_status — new
No existing column. `health_store.extract_topics()` (lines 25-71) is called inline inside `append_entry()`. On API failure: returns `[]` silently, row inserted with `topics='[]'`. New: `enrichment_status TEXT` on `health_knowledge`, values `'done'`/`'failed'`/`'pending'`. Set at insert time: `'done'` if len(topics)>0, `'failed'` if empty. Backfill existing rows: non-empty topics → `'done'`, empty topics → `'pending'`.

### ON CONFLICT DO UPDATE pattern (canonical)
`scripts/import-apple-health-json.py:405-419` (with source guard)
`agents/sample-agent/workspace/health/health_query.py:222-232` (simple form)
Use these as templates for any new upserts. Avoid `INSERT OR REPLACE`.

### launchd plist pattern
`scripts/launchagents/com.ironclaw.health-watch.plist`
- `StartInterval: 300` (5-min poll; WatchPaths is flaky on iCloud Drive)
- `RunAtLoad: false`
- stdout + stderr → same log file

### health_query.py — Rule 6c (body-metrics write)
`agents/sample-agent/workspace/health/health_query.py`
`argparse` with flat if/elif dispatch. Body-metrics write path exists. Rule 6c fix: detect temporal ambiguity in the input string before deciding whether to ask or default to "now". Bare numbers and "today/this morning/just now" → always default to now. Explicit relative temporal references ("the other day", "last week", "Tuesday") → ask once. Clear past date → use it.

### health_store.py — insert path
`agents/sample-agent/workspace/skills/podcast-summary/scripts/health_store.py`
- `extract_topics(summary, api_key, model)` at lines 25-71 — calls OpenAI, retries 3x, returns `[]` on all failures
- `append_entry(conn, entry)` at lines 74-132 — `INSERT OR IGNORE` (dedup on id), `json.dumps(entry["topics"])`
- Called from `on_demand.py:451-470` when `health_tier in ('always', 'sometimes')` or `save_to_health=True`

### DATA_CARD.md — new script
`scripts/generate-data-card.py` (new):
- Connect to health.db
- Query each table: row count, MIN(date), MAX(date)
- Pull last_synced from sync_state per resource
- Output markdown to `agents/sample-agent/workspace/health/DATA_CARD.md`
- Run on each sync and on-demand
- DATA_CARD.md is mounted in container workspace — agent reads it at session start

---

## Dependencies

### Files Modified
| File | Change |
|------|--------|
| `agents/sample-agent/workspace/health/health_db.py` | v6 migration block (in_range_flag, enrichment_status, topics_text columns + FTS trigger updates) |
| `agents/sample-agent/workspace/health/health_query.py` | Rule 6c temporal ambiguity detection; FTS query update for topics_text |
| `agents/sample-agent/workspace/skills/podcast-summary/scripts/health_store.py` | Set enrichment_status at insert time; populate topics_text |
| `scripts/oura-sync.py` | fetch_all raises on error; per-chunk last_synced advance; mkdir lock guard; cleanup_old_heartrate |
| `scripts/import-blood-labs.py` | Add in_range_flag computation; add markers_canonical.json alias lookup |
| `agents/sample-agent/workspace/AGENTS.md` | Rule 6c wording update |

### New Files
| File | Purpose |
|------|---------|
| `scripts/generate-data-card.py` | Auto-generates DATA_CARD.md from live health.db |
| `agents/sample-agent/workspace/health/DATA_CARD.md` | Generated output; LLM-facing DB manifest |
| `scripts/markers_canonical.json` | Units alias map for cross-provider lab marker names |

### Test Files Modified
| File | Change |
|------|--------|
| Existing health test suite | Existing tests must pass; add tests for in_range_flag, enrichment_status, fetch_all error signaling, heartrate retention |
| New: State of Mind XML behavioral tests | Written after confirming HKStateOfMindSample MetadataEntry key names from real export |

---

## Implementation Approaches

### v6 Migration Block (Option A — single block)
Goes immediately after `_version = 5` in `initialize_schema`, before end of function:
```python
# ---------- v6: in_range_flag, enrichment_status, topics_text + FTS update ----
if _version < 6:
    conn.executescript("""
        ALTER TABLE lab_results ADD COLUMN in_range_flag TEXT;
        ALTER TABLE health_knowledge ADD COLUMN enrichment_status TEXT;
        ALTER TABLE health_knowledge ADD COLUMN topics_text TEXT;
    """)
    # Rebuild FTS triggers to include topics_text
    # (drop old triggers, recreate with 3 columns)
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    _version = 6
```
Also update `SCHEMA_VERSION = 5` → `6`.

Note: SQLite `executescript` does implicit COMMIT; this is safe for migration blocks (same pattern as v3/v4/v5).

### oura-sync.py refactor (Option A — raises)
```python
class FetchError(Exception):
    pass

def fetch_all(resource, start_date, end_date, headers):
    ...
    # Replace: except Exception: break
    # With: except Exception as e: raise FetchError(str(e)) from e
    # Replace: non-200 break → raise FetchError(f"{status}")

def sync_daily_summaries(conn, start, end, headers):
    last_good = None
    for chunk_start, chunk_end in date_chunks(start, end):
        try:
            data = fetch_all("daily_activity", chunk_start, chunk_end, headers)
            # write rows...
            last_good = chunk_end
        except FetchError:
            break
    if last_good:
        set_last_synced(conn, "oura_daily", (date.fromisoformat(last_good) - timedelta(days=OVERLAP_DAYS)).isoformat())
```

### in_range_flag computation (import-blood-labs.py)
```python
BORDERLINE_PCT = 0.10  # within 10% of boundary = borderline

def compute_flag(value, ref_low, ref_high):
    if ref_low is None and ref_high is None:
        return None
    if ref_low is not None and value < ref_low:
        gap = ref_low - value
        return 'borderline' if gap / ref_low <= BORDERLINE_PCT else 'out'
    if ref_high is not None and value > ref_high:
        gap = value - ref_high
        return 'borderline' if gap / ref_high <= BORDERLINE_PCT else 'out'
    return 'in'
```
Add `in_range_flag` to the INSERT column list; no change to ON CONFLICT clause (flag is part of the initial insert, and the DO UPDATE SET includes it when value changes).

### enrichment_status in health_store.py
```python
topics = extract_topics(entry["summary"], api_key, model)
enrichment_status = 'done' if topics else 'failed'
# include enrichment_status + topics_text in INSERT columns
topics_text = ' '.join(topics) if topics else ''
```
Backfill after migration:
```sql
UPDATE health_knowledge SET enrichment_status = 'done'
WHERE topics != '[]' AND topics IS NOT NULL AND enrichment_status IS NULL;
UPDATE health_knowledge SET enrichment_status = 'pending'
WHERE (topics = '[]' OR topics IS NULL) AND enrichment_status IS NULL;
```

### FTS topics fix
FTS5 `content` table triggers must be updated to include `topics_text`. Old triggers (`hk_ai`, `hk_ad`, `hk_au`) are dropped and recreated in the v6 migration block to include the third column. FTS table itself is dropped and recreated with the `topics_text` column added:
```sql
DROP TABLE IF EXISTS health_knowledge_fts;
CREATE VIRTUAL TABLE health_knowledge_fts USING fts5(
    episode_title, summary, topics_text,
    content='health_knowledge', content_rowid='rowid',
    tokenize='porter unicode61'
);
-- Repopulate from base table
INSERT INTO health_knowledge_fts(rowid, episode_title, summary, topics_text)
SELECT rowid, episode_title, summary, COALESCE(topics_text, '') FROM health_knowledge;
```

### mkdir lock guard (oura-sync.py Python)
```python
import os

LOCK_DIR = os.path.expanduser("~/Library/Logs/ironclaw/.oura-sync.lock")
try:
    os.mkdir(LOCK_DIR)
except OSError:
    sys.exit(0)  # Another instance is running
try:
    main()
finally:
    os.rmdir(LOCK_DIR)
```

### markers_canonical.json
```json
{
  "aliases": {
    "Apolipoprotein B": ["ApoB", "Apo B", "APO-B"],
    "Glucose": ["Glucose, Serum", "Blood Glucose", "Glucose Fasting"],
    "LDL Cholesterol": ["LDL-C", "LDL Chol", "LDL Cholesterol Calc"]
  }
}
```
Seeded from `SELECT name FROM lab_markers` — verify actual marker names in DB before shipping.

---

## Impact Summary

**High-impact files**: `health_db.py` (v6 migration, FTS rebuild), `oura-sync.py` (core sync refactor), `health_store.py` (enrichment_status, topics_text)

**Struck item**: Item #5 (lab_results ON CONFLICT) — already correctly implemented in import-blood-labs.py. No change needed.

**Risk areas**:
- v6 migration: `executescript` with `ALTER TABLE` inside — SQLite supports this but test on a copy of the live DB first
- FTS rebuild: DROP + repopulate inside migration block; must happen atomically; if interrupted, FTS is gone until next initialize_schema call
- oura-sync refactor: fetch_all error signaling change touches the core sync loop — run with verbose logging after refactor, verify last_synced advances correctly on clean run and stops at right place on simulated error
- markers_canonical.json: must be seeded from real marker names — query `SELECT DISTINCT name FROM lab_markers` before writing the file

**No change needed**: lab_results ON CONFLICT (done), canonical_unit column (exists), SCHEMA_VERSION bump to 6 covers all column additions.

---

## Assessed Complexity: COMPREHENSIVE

| Signal | Value | Score |
|--------|-------|-------|
| Files impacted | 8 (health_db, health_query, health_store, oura-sync, import-blood-labs, AGENTS.md, 2 new) | HIGH |
| Pattern match | All patterns exist (migration, FTS5, ON CONFLICT, mkdir guard, launchd) | LOW |
| Components crossed | health_db, health_query, health_store, oura-sync, lab importer, new script | HIGH |
| Data model changes | Yes — 3 new columns + FTS rebuild | MEDIUM |
| Integration points | Internal only | LOW |
| External complexity | All well-documented SQLite/shell patterns | LOW |

**Hard stops**: db_schema_destructive (3 new columns + FTS index change) → COMPREHENSIVE.

**Architectural decisions**: Option A (single v6 migration block) + Option A (fetch_all raises on error). Approved by Jeff 2026-05-01.
