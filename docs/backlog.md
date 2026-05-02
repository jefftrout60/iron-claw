# IronClaw Project Backlog

*Last updated: 2026-05-01. Update at end of each scope or evaluate session.*

---

## Already Done (captured for reference)

- ✅ Apple Health import pipeline (XML + Health Auto Export JSON + iOS automation)
- ✅ HRV backfill — oura_daily.avg_hrv_rmssd populated (3138 rows)
- ✅ State of Mind pillar — DB, importers, mood subcommand, agent wiring (JSON path confirmed working)
- ✅ sync-status subcommand (verified via iMessage 2026-05-01)
- ✅ Migration framework tests (v0→v6)
- ✅ Source-priority dedup (get/set_last_synced consolidated, source guards)
- ✅ Secrets rotation script (rotate-container-secrets.sh) — built; rotation not needed (credentials not exposed)
- ✅ DB backup (backup-health-db.sh, daily launchd)
- ✅ Daily Withings + Oura sync launchd plist
- ✅ Evernote historical backfill (import-evernote-workouts.py)
- ✅ AGENTS.md Rule 6b consolidated to compact table
- ✅ Withings upsert source column bug fixed
- ✅ oura-sync.py sync_tags dict.get bug fixed
- ✅ Withings + Oura secrets moved to macOS Keychain
- ✅ **Health DB Hardening Sprint (2026-05-01)**
  - v6 migration: lab_results.in_range_flag, health_knowledge.enrichment_status + topics_text, FTS rebuild
  - Units alias map (markers_canonical.json) + alias normalization in lab importer
  - oura-sync: FetchError + per-chunk last_synced + mkdir lock guard + heartrate retention (90 days)
  - DATA_CARD.md auto-generation (wired into daily-health-sync.sh)
  - Rule 6c temporal classifier — default to now for bare-number body metric entries
  - 273 tests passing; code review clean; pushed to origin/main

---

## Outstanding Backlog

### Immediate (do before next scope session)

- [ ] **iMessage manual test checklist** — send all 14 queries from `docs/tasks/main/imessage-test-checklist.md`, verify 7 subcommands route correctly

---

### High-Value Queries (DB is ready, just need building)

- [ ] **`daily_brief(day)` query** — the killer-app query: one call returns Oura sleep/readiness/HRV + latest out-of-range labs + health_knowledge entries matching current lab topics (e.g. ApoB elevated → cite Attia ApoB episodes). Agent can call it forever once built.
- [ ] **Correlations view** — pre-baked SQL joining lab_results to oura_daily windowed by date. "Did my ApoB drop after 8 weeks of Zone 2?" is what this DB exists to answer. Agent shouldn't write this SQL fresh each time.
- [ ] **Trends snapshot job** — daily cron computes and stores 7d/30d/90d deltas per lab marker into a `lab_trends` table. "What changed recently?" becomes one SELECT.

---

### Health Pillars — Near Term

- [ ] **Weight iMessage entry** — Rule 6c pattern: "185.2" → body_metrics table. Same flow as BP entry. ~half-session.
- [ ] **Weekly summary email extension** — add weight trend + workout summary alongside existing BP/Oura section. Intent 5 partially wired.
- [ ] **avg_hrv_rmssd trend query** — add health_query.py subcommand now that column is populated
- [ ] **State of Mind vs Oura readiness correlation** — Oura readiness (AM physiological prediction) vs State of Mind valence (PM subjective result, logged ~9 PM). Query: does high readiness reliably predict a good day? Both pillars live; needs cross-pillar query subcommand or agent skill.
- [ ] **Doctor periodic BP summary email** — ask at next appointment if useful; cadence TBD
- [ ] **Oura context injection** — before answering any health question, pull current readiness/sleep/HRV and inject as live context ("given your readiness is 62 today..."). Now unblocked.
- [ ] **events table** — `events(date, kind, label, notes)` for interventions ("started statin", "DEXA scan"). Enables before/after correlation queries.

---

### Data Ingestion

- [ ] **Bulk podcast ingestion** — Whisper-summarize Attia (300+), Huberman, Rhonda Patrick at 5–10/night. Biggest leverage on health knowledge search quality. **Needs its own scope session.**
- [ ] **All-time Evernote pre-2025 backfill** — historical workout notes before the 2025 cutoff
- [ ] **active_energy column in activity_daily** — Health Auto Export already exports it; no DB column yet
- [ ] **Full in-workout HR zone tracking** — Health Auto Export `heartRateData` field only covers post-workout cool-down (2 min), not the session itself. True time-in-zone requires either a different HAE export config that streams the full in-workout HR series, or pulling raw `HKQuantityTypeIdentifierHeartRate` samples from the main Health DB export. Deferred from Weekly Brief sprint (2026-05-02) in favor of simplified avg HR + intensity/METs per session.

---

### Health Pillars — Future

- [ ] **DEXA scans** — `dexa_results` table. Blocked: no export mechanism yet.
- [ ] **Visit summaries / doctor notes** — `visit_notes` table + FTS. Blocked: no structured source.
- [ ] **Supplements** — `supplements` table, manual entry or CSV. Low priority.
- [ ] **Daily macros / food tracking** — Cronometer API or manual entry. Blocked: no export mechanism.
- [ ] **Book knowledge pillar** — `book_knowledge` table + FTS, import-book.py for DRM-free PDFs/EPUBs. Blocked: no digital copies yet.

---

### Agent Features

- [ ] **Cross-pillar correlation skill** — LLM queries across all pillars in one iMessage turn ("do my lowest HRV scores follow hard workout days?")
- [ ] **On-demand pattern detection / weekly pattern digest email** — surface trends without the user having to ask
- [ ] **Agent access control** — per-caller permission levels (read-only health queries for doctors/family vs. full access for Jeff)
- [ ] **FTS on state_of_mind.labels** — "show me days I felt anxious" requires full-text search on the JSON labels field
- [ ] **Embedding/vector search layer** — semantic search over health knowledge. Explicitly deferred; revisit when core pillars are stable.

---

### Lab Data Quality

- ✅ **Reference-range flagging at write time** — `lab_results.in_range_flag` added in v6 migration (hardening sprint 2026-05-01)
- ✅ **Units registry** — `markers_canonical.json` alias map (21 groups) seeded; alias normalization wired into `import-blood-labs.py` (hardening sprint 2026-05-01)
- [ ] **lab_results ON CONFLICT DO UPDATE** — replace current destructive `INSERT OR REPLACE` (which resets `imported_at` and breaks audit trail) with `ON CONFLICT DO UPDATE SET ... WHERE value IS NOT excluded.value`.
- ✅ **enrichment_status column** — `health_knowledge.enrichment_status` + `topics_text` added in v6 migration (hardening sprint 2026-05-01)
- ✅ **FTS on health_knowledge.topics** — FTS5 index rebuilt to include `topics_text`; triggers and backfill done (hardening sprint 2026-05-01)

---

### Architecture

- ✅ **True partial failure tracking in oura-sync.py** — per-chunk `last_synced` advance implemented; only advances to confirmed data (hardening sprint 2026-05-01)
- [ ] **State of Mind XML behavioral tests** — add fixture test once HKStateOfMindSample key names confirmed from real export
- [ ] **Source-priority table-driven helper** — before adding a 4th data source per table; currently encoded per-importer
- ✅ **Rule 6c state machine** — temporal classifier implemented in `health_query.py`; bare numbers default to now, only asks for ambiguous relative dates (hardening sprint 2026-05-01)
- ✅ **oura_heartrate retention policy** — 90-day retention via `cleanup_old_heartrate`; `_HEARTRATE_RETENTION_DAYS` constant in `oura-sync.py` (hardening sprint 2026-05-01)
- [ ] **Migration ladder refactor** — before v8, convert to dispatcher pattern. Not urgent at v6.
- ✅ **flock guard for oura-sync.py** — `mkdir`-based single-instance lock guard added (hardening sprint 2026-05-01)
- ✅ **DATA_CARD.md** — auto-generated by `scripts/generate-data-card.py`, wired into `daily-health-sync.sh` (hardening sprint 2026-05-01)

---

### Infrastructure

- [ ] **Sean Ryan feed** — `max_duration_seconds` filter for 5–7hr episodes that stay paused/skipped.
