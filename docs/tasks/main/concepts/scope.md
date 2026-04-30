# Scope: Health Hardening + iOS Sync Sprint

**Date**: 2026-04-30
**Branch**: main
**Status**: Scoped — ready for `/spectre:plan`

---

## The Problem

The health import system shipped fully functional but left seven known gaps: a credential rotation task, a verification gap (5 new subcommands untested end-to-end), a permanently-NULL HRV column, two copy-paste duplication issues, zero migration path test coverage, no way for the agent to report data freshness, and a manual Apple Health export flow that requires monthly action to stay current. None of these are blocking, but they compound: stale HRV data silently breaks correlation queries, the manual export creates drift, and the duplicated sync helpers make future changes error-prone.

---

## Target Users

**Primary**: Jeff (sole user of health.db and iMessage health queries).
**Secondary**: The agent — it needs sync-status to caveat health answers when data is stale.

---

## Success Criteria

1. Container can be restarted with fresh credentials via a single script run
2. All 5 new health_query subcommands respond correctly to natural-language iMessage queries (verified manually via test checklist)
3. `oura_daily.avg_hrv_rmssd` is non-NULL for all days that have a sleep session; new Oura syncs populate it automatically
4. `get_last_synced`/`set_last_synced` exists once (in `health_db.py`); no duplicated source-guard logic
5. Migration tests cover v0→v4 full path and v2→v4, v3→v4 incremental paths; try/except patches verified idempotent
6. `health_query.py sync-status` returns last-sync time and stale flag for all 4 sources; agent uses it to caveat stale answers
7. Apple Health XML appears in iCloud Drive → Mac auto-imports within minutes, no manual steps beyond running the iOS Shortcut

---

## User Experience

**Secrets rotation**: Run `scripts/rotate-container-secrets.sh`, enter new values when prompted, script updates `.env` and restarts the container. Credential generation in external dashboards (OpenAI, BotFather, Google) is manual — the script handles everything local.

**iMessage test checklist**: A markdown file with ~12 sample queries (covering all 5 subcommands) and expected output shapes. Jeff pastes each into iMessage, checks the response, marks pass/fail.

**HRV**: No UX change. Backfill runs once; future syncs auto-populate. Queries that reference HRV stop returning NULL.

**Source-priority / migration tests**: Pure internal — no UX impact. Regressions surface in CI rather than silently in production.

**Sync-status**: Jeff asks "is my health data up to date?" → agent responds with last-sync time per source and calls out any stale sources. Agent also proactively notes stale data when answering health queries if Withings or Oura hasn't synced in >2 days.

**iOS Shortcut flow**:
1. Jeff opens Shortcuts on iPhone, runs "Export Health Data" (built once from written guide)
2. Shortcut exports `export.xml` to `iCloud Drive/Health/`
3. Within minutes, Mac launchd watcher detects the file, runs `import-apple-health.py`, archives the processed file to `iCloud Drive/Health/archive/export_YYYYMMDD.xml`
4. Import result logged; next iMessage health query reflects fresh data

---

## Scope Boundaries

### IN

| # | Item | Notes |
|---|------|-------|
| 1 | `scripts/rotate-container-secrets.sh` | Prompts for OpenAI key, Telegram token, Gmail app password; updates `.env`; restarts container |
| 2 | iMessage test checklist | ~12 queries covering body-metrics, activity, workouts, workout-exercises, tags subcommands |
| 3 | HRV backfill | One-time UPDATE from oura_sleep_sessions WHERE type = 'long_sleep'; fallback to longest session |
| 4 | HRV fix in oura-sync.py | Populate avg_hrv_rmssd on new syncs using same session-selection logic |
| 5 | Move get_last_synced/set_last_synced to health_db.py | Remove copy-paste from withings-sync.py and oura-sync.py |
| 6 | De-dup source guard in import-apple-health.py | Two identical WHERE clauses → one helper |
| 7 | Migration path tests | v0→v4, v2→v4, v3→v4 incremental; try/except patch idempotency |
| 8 | sync-status subcommand in health_query.py | Reads sync_state; stale flag for Withings/Oura if >2 days |
| 9 | Apple Health + Evernote write to sync_state | importers record last-import timestamp in sync_state after successful run |
| 10 | AGENTS.md + SKILL.md updates for sync-status | Rule 6b table entry; Intent added to SKILL.md |
| 11 | Mac-side iCloud Drive watcher + launchd job | Watches `~/Library/Mobile Documents/com~apple~CloudDocs/Health/`; runs importer; archives file |
| 12 | iOS Shortcut written guide | Step-by-step instructions to build the Shortcut on-device |
| 13 | State of Mind pillar | health.db v5 migration; import from Apple Health XML + JSON; `mood` subcommand; AGENTS.md + SKILL.md wired |

### OUT

- Embedding / vector search layer (backlog)
- Moving container secrets (OpenAI, Telegram, Gmail) to macOS Keychain — Docker can't access Keychain; rotation only
- Automated credential generation in external dashboards (OpenAI, BotFather, Google) — requires human action
- Automated test coverage for the iMessage test checklist (manual verification is the bar for this sprint)

### Maybe / Future

- `sync-status` record counts per source (debugging aid; defer until actually needed)
- Stale flag for Apple Health / Evernote (manual imports, threshold unclear)
- iOS Shortcut on a schedule (watchOS complication or Automation trigger)
- `avg_hrv_rmssd` trend query in health_query.py (now that data is populated)
- State of Mind vs Oura readiness correlation query (enabled once both pillars have data)

---

## Constraints

- `health.db` advancing to schema version 5 for `state_of_mind` table; all other items require no schema changes
- Container secrets stay in `.env` (Docker can't read macOS Keychain); Withings/Oura tokens stay in Keychain
- launchd WatchPaths fires on file creation/modification — no polling daemon needed
- iCloud Drive sync latency on Mac is typically seconds to low minutes; import trigger should tolerate a short sync delay
- iOS Shortcut must be built manually on-device; cannot be generated programmatically

---

## Integration

**Touches**:
- `agents/sample-agent/workspace/health/health_db.py` — adds `get_last_synced`/`set_last_synced`, no schema change
- `agents/sample-agent/workspace/health/health_query.py` — adds `sync-status` subcommand
- `scripts/withings-sync.py`, `scripts/oura-sync.py` — remove duplicated sync helpers, call health_db versions
- `scripts/import-apple-health.py`, `scripts/import-evernote-workouts.py` — write to sync_state after run
- `agents/sample-agent/workspace/AGENTS.md` — Rule 6b table updated
- `agents/sample-agent/workspace/skills/health-query/SKILL.md` — Intent added
- New: `scripts/rotate-container-secrets.sh`
- New: `scripts/watch-health-import.sh` (launchd trigger script)
- New: `com.ironclaw.health-watch.plist` (launchd WatchPaths job)

**Avoids**:
- `agents/sample-agent/.env` contents (only the rotation script touches this)
- Docker image, compose template, gateway config

---

## Decisions

| Decision | Rationale |
|----------|-----------|
| HRV: prefer 'long_sleep' session type, fallback to longest | Matches Oura's own "primary sleep" concept; longest is the best proxy if type isn't set |
| Apple Health + Evernote sync_state entries | Consistency — all 4 sources visible from one table; simpler sync-status query |
| iCloud Drive folder: `~/Library/Mobile Documents/com~apple~CloudDocs/Health/` | Standard iCloud path; no custom folder to manage |
| Fixed filename `export.xml` + archive with datestamp after processing | Prevents re-processing races; watcher triggers on file presence/mtime, not filename pattern |
| Stale flag: Withings + Oura only, threshold 2 days | These are automated; stale = broken. Apple Health / Evernote are intentionally manual |
| Rotation script: prompts for values, updates .env, restarts container | Maximum automation given external dashboards require human action |
| sync-status: timestamps + stale flag only, no record counts | Counts are a debugging tool not yet needed; add later if requested |

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| launchd WatchPaths fires before iCloud sync completes (partial XML) | Medium | Import script validates XML before processing; on parse error, leaves file in place and logs |
| HRV backfill UPDATE touches a row that already has a non-NULL value | Low | Backfill script uses WHERE avg_hrv_rmssd IS NULL guard |
| Rotating secrets breaks container while agent is serving requests | Low | Rotation script stops container before updating .env, restarts after |
| iOS Shortcut exports partial file (interrupted) | Low | Same XML validation guard on import side |
| Apple Health write to sync_state fails silently | Low | Write happens after successful import; existing error handling already surfaces import failures |

---

## Next Steps

**Complexity**: M (7 code changes, 1 new script, 1 new launchd job, 1 guide — all bounded)

Run `/spectre:plan` to generate the implementation plan, or `/spectre:create_tasks` to go straight to task breakdown.
