# Architecture Review: health-db SQLite Migration

**Date**: 2026-04-26
**Scope**: commits 090f7c7..HEAD — unified SQLite health database
**Reviewed**: `health_db.py`, `health_store.py`, `health_store_cmd.py`, `migrate_health_knowledge.py`, `import-blood-labs.py`, `oura-sync.py`, `com.ironclaw.oura-sync.plist`

---

## Summary

This is a solid first cut of a personal health intelligence layer. The schema is well thought out, FTS5 wiring is correct, WAL is enabled, and the JSON→SQLite migration is idempotent. As a v1 it works.

But the design has a fault line that will start hurting as more pillars (DEXA, BP, workouts, supplements, weight/macros, visits) get added: **the database is owned by the podcast-summary skill, not by an independent "health" module.** Every new pillar will have to either pile more code into `podcast-summary/scripts/` or repeat the `sys.path.insert` dance from a sibling location. There are also several silent-corruption risks worth fixing now while the dataset is small.

Overall assessment: ship the migration, but do the structural rename (Recommendation 1) and the correctness fixes (Critical Issues 1–3) before adding the next pillar.

---

## Strengths

1. **WAL + sane PRAGMAs.** `journal_mode=WAL`, `synchronous=NORMAL`, FK on, 32 MB cache — correct defaults for this workload.
2. **FTS5 with external content + triggers** implemented correctly. `porter unicode61` is the right tokenizer.
3. **Schema is idempotent and self-initializing.** `IF NOT EXISTS` everywhere.
4. **Path resolution via `Path(__file__).parent.parent / "podcast_vault"`** mirrors `vault.py`, works on both host and container.
5. **Stable, content-addressable IDs** plus a unique `(show, episode_title, date)` index is belt-and-suspenders dedup.
6. **`oura_heartrate.day` as a generated stored column** keeps day-bucket queries fast.
7. **Per-resource `sync_state`** — partial failure on one endpoint doesn't reset others.
8. **404 and 429 handling in `fetch_all`** — correct distinction between skip and retry.
9. **`import-blood-labs.py` unpivot logic** — ffill on merged cells, reference-range parsing, en-dash support.
10. **CLI separation** between `health_store.py` (library) and `health_store_cmd.py` (Intent 6 entry point).

---

## Critical Issues

### C1. `lab_results` INSERT OR REPLACE silently destroys audit trail

`INSERT OR REPLACE` deletes and re-inserts the row on conflict, resetting `imported_at` and the auto-increment `id`. Future rows pinned to a `lab_results.id` will silently dangle; a corrected lab value from a re-import leaves no record.

**Fix:**
```sql
INSERT INTO lab_results (...) VALUES (...)
ON CONFLICT(marker_id, date) DO UPDATE SET
  value = excluded.value,
  reference_low = excluded.reference_low,
  reference_high = excluded.reference_high,
  source_sheet = excluded.source_sheet
WHERE lab_results.value IS NOT excluded.value
   OR lab_results.reference_low IS NOT excluded.reference_low
   OR lab_results.reference_high IS NOT excluded.reference_high;
```

### C2. `sync_daily_summaries` overwrites `contributors_json` instead of merging

Only `daily_sleep` writes to `contributors_json`. If readiness or activity contributors are added later, they'll silently overwrite sleep contributors via `dict.update()`.

**Fix:** rename to `sleep_contributors_json`, or merge explicitly:
```python
existing = json.loads(daily[day].get("contributors_json") or "{}")
existing["readiness"] = contribs
merge(day, contributors_json=json.dumps(existing))
```

### C3. Incremental sync advances high-water mark on partial failure

If `fetch_all` fails mid-range, `set_last_synced(conn, resource, end)` still runs — permanently claiming the gap was synced.

**Fix:** add `OVERLAP_DAYS = 3` re-fetch window; only advance high-water mark on full success.

### C4. No unit tracking for blood lab markers

`lab_markers.unit` is never populated. A "Glucose" of 5.4 is normal in mmol/L but concerning in mg/dL. Mixing labs from two providers will produce nonsense trends silently.

**Fix:** ship a `markers_canonical.json` reference file alongside the importer; collapse known aliases and set canonical units at import time.

### C5. `extract_topics` failure permanently produces topic-less entries

Transient OpenAI 500s return `[]`, which is then inserted. The `(show, title, date)` unique index means a retry won't re-insert. Add `enrichment_status` column (`'ok'|'pending'|'failed'`) so backfilling is a SELECT away.

---

## Architectural Concerns

### A1. The DB is owned by the wrong module ⭐ Most Important

`health.db`, `health_db.py`, and `health_store.py` all live under `podcast-summary/scripts/` and `podcast_vault/`. Every new pillar's importer has to do:

```python
sys.path.insert(0, str(_REPO_ROOT / "agents/sample-agent/workspace/skills/podcast-summary/scripts"))
import health_db
```

Already repeated in `oura-sync.py`, `import-blood-labs.py`, and `migrate_health_knowledge.py`.

**Recommended structure:**
```
agents/sample-agent/workspace/health/
  db.py                    # was health_db.py
  stores/knowledge.py      # was health_store.py
  health.db                # lives here
scripts/health/
  import_blood_labs.py
  oura_sync.py
  _bootstrap.py            # single sys.path helper
```

### A2. No migration framework

`CREATE IF NOT EXISTS` only handles additive changes. Renaming a column, adding NOT NULL, splitting a table — any of these need a migration runner. Add `PRAGMA user_version` tracking now:

```python
MIGRATIONS = [
    ("ALTER TABLE lab_markers ADD COLUMN canonical_unit TEXT",),
]
def migrate(conn):
    cur = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, stmts in enumerate(MIGRATIONS, 1):
        if cur >= i: continue
        for s in stmts: conn.execute(s)
        conn.execute(f"PRAGMA user_version = {i}")
    conn.commit()
```

### A3. No backup or integrity strategy

The DB contains personal health history going back to 2002. The JSON source will be gone; the Excel file is the only lab recovery path.

**Cheap fixes:**
- Daily: `sqlite3 health.db ".backup '/path/to/backups/health-$(date +%F).db'"` (keep 30 days)
- Weekly: `PRAGMA integrity_check` in the Oura sync log
- Keep the original Excel file and don't delete it

### A4. Dual-locking risk (host + container writing same WAL file)

WAL mode handles concurrent processes on the same host correctly. But two foot-guns:
1. WAL files on network filesystems (iCloud Drive, Dropbox, SMB) will corrupt. Document the constraint.
2. Container UID 1000 vs host UID 501 — masked by Docker Desktop on Mac, may bite on Linux.

### A5. No query/read API

`health_store.py` has `append_entry` and `load_all`, but the agent has no curated way to read cross-pillar data. The agent will resort to ad-hoc SQL via exec unless a `health_query.py` is built.

Add now:
```python
def lab_trend(marker: str, since: date | None = None) -> list[Row]: ...
def oura_window(start: date, end: date, fields: list[str]) -> list[Row]: ...
def search_health_knowledge(query: str, limit: int = 20) -> list[Row]: ...
def daily_brief(day: date) -> dict: ...
```

---

## Operational Issues

### O1. Oura sync is weekly — data lags up to 7 days

For a system meant to answer "how did I sleep last night", weekly is too infrequent. Switch to daily at 5am and set `RunAtLoad: true` so a reboot doesn't break the schedule.

### O2. `/tmp/oura-sync.log` is purged on reboot

Use `~/Library/Logs/ironclaw/oura-sync.log` instead.

### O3. `.env` parser is fragile

Lines like `KEY="value with space" # comment` will be parsed wrong. Use `python-dotenv` or pass token via launchd `EnvironmentVariables`.

---

## Missing Pieces for Genuine Usefulness

1. **`daily_brief(today)`** — one call combining: Oura sleep/readiness, latest out-of-range labs, health-knowledge entries matching current lab topics. This is the killer query.
2. **`events` table** — `events(id, date, kind, label, notes)` for interventions ("started statin", "DEXA scan", "broke arm"). Without it, correlations to intervention dates are impossible.
3. **Reference-range flag at write time** — compute `in_range` flag when inserting lab results; `SELECT * FROM lab_results WHERE flag='out' ORDER BY date DESC` becomes "what's wrong with me right now."
4. **`health_query.py`** — curated read functions the agent can call without writing SQL.
5. **Unit registry** — static `markers_canonical.json` defining canonical units per marker.
6. **FTS on topics** — concatenate `topics` into the FTS5 indexed text so topic-based queries work.

---

## Recommendations (Prioritized)

**Before adding the next pillar:**
1. Move health module out of `podcast-summary` (A1)
2. Switch `lab_results` to `ON CONFLICT DO UPDATE` (C1)
3. Add unit tracking for lab markers (C4)
4. Fix `contributors_json` overwrite (C2)
5. Add `PRAGMA user_version` migration runner (A2)
6. Add `OVERLAP_DAYS = 3` + fail-safe high-water mark (C3)

**This month:**
7. Daily Oura sync, `RunAtLoad: true` (O1)
8. Daily SQLite backup + weekly integrity check (A3)
9. Add `health_query.py` with `lab_trend`, `oura_window`, `search_health_knowledge`, `daily_brief` (A5)
10. Add `events` table
11. Add `enrichment_status` for topic extraction (C5)

**Eventually:**
12. Move secrets to macOS Keychain (O3)
13. Document WAL filesystem constraint (A4)
14. Add flock-based single-instance guard for oura-sync
