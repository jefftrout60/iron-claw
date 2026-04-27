---
name: decisions-health-knowledge-architecture
description: Use when touching health_knowledge.json, health.db, planning health intelligence features, adding new data pillars, or querying the health SQLite database
user-invocable: false
---

# Health Intelligence: SQLite Database Architecture

**Trigger**: health_knowledge, health_db, health.db, health intelligence, Oura, blood labs, DEXA, biometric, trusted sources, Attia, Huberman, Patrick, SQLite migration, health store, health_store
**Confidence**: high
**Created**: 2026-03-28
**Updated**: 2026-04-27
**Version**: 3

## Status: Read Layer Complete (2026-04-27)

`health_knowledge.json` has been **retired**. The unified SQLite database `health.db` is live at:
`agents/sample-agent/workspace/health/health.db`

`health_db.py` was moved from `skills/podcast-summary/scripts/` to `workspace/health/` (S4). The new health-query read layer (`health_query.py`, `cost_summary.py`, `SKILL.md`) was added in 2026-04-27.

## What's in health.db

| Table | Contents | Row count |
|---|---|---|
| `health_knowledge` | Podcast/newsletter summaries (Attia, Huberman, Patrick) | 97 |
| `health_knowledge_fts` | FTS5 index on episode_title + summary | 97 |
| `lab_markers` | Canonical blood lab marker names | 117 |
| `lab_results` | Blood draw results (Oct 2002–present) | 1,171 |
| `oura_daily` | Daily sleep/readiness/activity scores (Jun 2016–present) | 3,565 |
| `oura_sleep_sessions` | Per-session sleep detail + 5-min arrays | 4,669 |
| `oura_heartrate` | Intraday HR stream (recent only — Oura API limitation) | ~421 |
| `sync_state` | Last synced date per Oura resource | — |

## Architecture: Option A (Swap-in Place)

`health_db.py` owns schema + connection. `health_store.py` keeps its public API unchanged — callers (`engine.py`, `on_demand.py`) required **zero changes**.

```python
# DB path resolution — health_db.py resolves relative to its own location
_HEALTH_DIR = Path(__file__).parent  # workspace/health/
```

Key files:
| File | Role |
|---|---|
| `workspace/health/health_db.py` | Schema + connection manager (moved from podcast-summary/scripts/ in S4) |
| `workspace/health/health.db` | The database (moved from podcast_vault/ in S4) |
| `workspace/health/health_query.py` | Read layer: `lab-trend`, `oura-window`, `search` subcommands → JSON stdout |
| `workspace/health/cost_summary.py` | Session JSONL cost aggregator: `--week` / `--month` → JSON stdout |
| `workspace/health/test_health_query.py` | 21 unit tests for health_query.py (in-memory SQLite fixture) |
| `workspace/skills/health-query/SKILL.md` | Agent skill: 6 intents for health questions + weekly email |
| `skills/podcast-summary/scripts/health_store.py` | R/W interface (append_entry, load_all) |
| `skills/podcast-summary/scripts/health_store_cmd.py` | CLI for SKILL.md Intent 6 |
| `scripts/import-blood-labs.py` | One-shot Excel importer (--dry-run mode) |
| `scripts/oura-sync.py` | Oura v2 API sync (historical + incremental) |
| `scripts/launchagents/com.ironclaw.oura-sync.plist` | Weekly launchd job (Mon 3am) |

Container exec path for health_query.py: `/home/openclaw/.openclaw/workspace/health/health_query.py`

## Running Imports

```bash
# Oura test run (small range first)
python3.13 scripts/oura-sync.py --since 2026-04-01

# Full Oura history (run once — takes ~9 min)
python3.13 scripts/oura-sync.py --historical

# Blood labs (always dry-run first)
python3.13 scripts/import-blood-labs.py --file ~/Documents/Fitness/BloodTest_Tracker.xlsx --dry-run
python3.13 scripts/import-blood-labs.py --file ~/Documents/Fitness/BloodTest_Tracker.xlsx
```

Oura PAT: `OURA_PERSONAL_ACCESS_TOKEN` in `agents/sample-agent/.env`.
Excel: `~/Documents/Fitness/BloodTest_Tracker.xlsx` — 6 tabs (CBC, Metabolic, Hormones, Lipids, Thyroid, Misc).

## Adding a New Health Episode

```bash
# Via agent exec (SKILL.md Intent 6)
python3 workspace/skills/podcast-summary/scripts/health_store_cmd.py \
  --episode-id "the-peter-attia-drive_abc12345" --tagged-by user

# Via on_demand.py
docker exec sample-agent_secure python3 \
  /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/on_demand.py \
  --query "Show Name #NNN" --depth extended \
  --strategy fetch_openai_whisper show_notes \
  --style deep_science --save-to-health
```

## Future Data Pillars (Deferred)

All follow the same pattern: new table in `health_db.py` + new import script.

| Pillar | Status | Notes |
|---|---|---|
| DEXA scans | Deferred | No reliable export mechanism |
| Workout logs | Deferred | Oura /workout endpoint available |
| Blood pressure | Deferred | No source defined |
| Supplement list | Deferred | — |
| Visit summaries | Deferred | — |
| Weight / macros | Deferred | — |

## Deferred Features

- **Oura context injection** — real-time readiness/HRV in health query answers
- **Embedding/vector search** — semantic FTS (sqlite-vec or Chroma)
- **Topics field population** — `topics: []` is empty in all entries
- **Pattern detection skill** — on-demand or scheduled digest

## Known Architecture Issues (status: 2026-04-27)

1. ~~**DB is owned by podcast-summary**~~ — **RESOLVED**: health_db.py + health.db moved to `workspace/health/` (S4)
2. **No migration framework** — `PRAGMA user_version` runner needed for schema changes
3. **No backup strategy** — add daily `sqlite3 health.db ".backup ..."` cron
4. ~~**No read API**~~ — **RESOLVED**: `health_query.py` built (2026-04-27) with `lab-trend`, `oura-window`, `search` subcommands + `health-query` skill
5. **No `events` table** — needed for intervention correlations ("started statin", "DEXA scan")
6. **Unit tracking missing** for lab markers — risk of cross-provider unit mismatch in trends
