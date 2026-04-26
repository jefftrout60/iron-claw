# Cleanup Summary — health-db (090f7c7^..HEAD)
*Generated 2026-04-26*

## Executive Summary
- **Files analyzed**: 8 (health_db.py, health_store.py, health_store_cmd.py, vault.py, oura-sync.py, import-blood-labs.py, migrate_health_knowledge.py, + on_demand.py via grep)
- **Safe removals**: 5 items across 4 files
- **Manual review required**: 0
- **Estimated dead code removed**: ~20 lines

---

## Safe Removals

### 1. health_db.py — Remove `_check_fts5()` and `import sys`
- **Lines**: 239–253 (function) + 253 (module-level call) + line 16 (`import sys`)
- **Why safe**: Fires on every `import health_db` (every agent exec touching health store), adds noisy stderr. Provides no recovery logic — only warns. The real guard is `initialize_schema()` which will raise `sqlite3.OperationalError` clearly if FTS5 is absent. FTS5 already verified working in both host and container.
- **Risk**: None — pure removal of a side-effectful module-level call.

### 2. health_store.py — Remove `find_by_show()`
- **Lines**: 146–159
- **Why safe**: Grep across all scripts found zero callers. Not referenced in `engine.py`, `on_demand.py`, `health_store_cmd.py`, or SKILL.md. Dead since migration.
- **Risk**: None — no callers.

### 3. on_demand.py — Update stale `--save-to-health` help string
- **Line**: 575
- **Current**: `"Save to health_knowledge.json regardless of the feed's health_tier setting."`
- **Updated**: `"Save to health.db regardless of the feed's health_tier setting."`
- **Why safe**: Help text only, no runtime effect.

### 4. import-blood-labs.py — Remove dead `str(...) != "nan"` guards in DB write
- **Lines**: 184–185
- **Why safe**: `parse_reference_range()` only returns Python `None` or `float` — never `float('nan')`. The `is not None` check already handles the actual condition. The `str(...) != "nan"` suffix is unreachable.
- **Risk**: None — the `is not None` check remains.

### 5. migrate_health_knowledge.py — Remove redundant FTS rebuild
- **Lines**: 93–96 (comment + `INSERT INTO health_knowledge_fts ... 'rebuild'`)
- **Why safe**: FTS triggers (`hk_ai`) in `health_db.py:99-118` already kept the index consistent row-by-row during the bulk `INSERT OR IGNORE`. Rebuild adds wall time for no correctness gain. One-shot script so it doesn't matter for future runs.
- **Risk**: None — triggers maintain correctness.

---

## Excluded Items (Keep)

| Item | Location | Reason |
|---|---|---|
| `prune_episodes()` | vault.py:87 | Called by engine.py:583 — live |
| `load_all()` | health_store.py:133 | Used by `_cli_test()` in `__main__` diagnostic |
| `__main__` block | health_store.py:170 | Useful manual diagnostic (`python3 health_store.py --test`) |
| `pd.isna()` in parse_reference_range | import-blood-labs.py:32 | Borderline — safe guard against pandas NA; left as-is |
| 429 retry in fetch_all | oura-sync.py:92 | Live error handling path |
| `podcast-log.sh:18` stale comment | podcast-log.sh | Outside commit range; minor |
| `archive/run_backlog3.sh` stale ref | archive script | Archive file; not production |

---

## Estimated Impact
- ~20 lines removed
- 1 help string updated
- No functional changes to any live code path
