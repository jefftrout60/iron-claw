# IronClaw Project Backlog

*Last updated: 2026-04-30. Update at end of each scope or evaluate session.*

---

## Already Done (captured for reference)

- ✅ Apple Health import pipeline (XML + Health Auto Export JSON + iOS automation)
- ✅ HRV backfill — oura_daily.avg_hrv_rmssd populated (3138 rows)
- ✅ State of Mind pillar — DB, importers, mood subcommand, agent wiring
- ✅ sync-status subcommand
- ✅ Migration framework tests (v0→v5)
- ✅ Source-priority dedup (get/set_last_synced consolidated, source guards)
- ✅ Secrets rotation script (rotate-container-secrets.sh) — **script built, actual rotation not yet run**
- ✅ DB backup (backup-health-db.sh, daily launchd)
- ✅ Daily Withings + Oura sync launchd plist
- ✅ Evernote historical backfill (import-evernote-workouts.py)
- ✅ AGENTS.md Rule 6b consolidated to compact table
- ✅ Withings upsert source column bug fixed
- ✅ oura-sync.py sync_tags dict.get bug fixed
- ✅ Withings + Oura secrets moved to macOS Keychain

---

## Outstanding Backlog

### Immediate (do before next scope session)

- [ ] **Rotate container secrets** — run `scripts/rotate-container-secrets.sh`, get new OpenAI/Telegram/Gmail credentials from their dashboards
- [ ] **iMessage manual test checklist** — send all 14 queries from `docs/tasks/main/imessage-test-checklist.md`, verify 7 subcommands route correctly
- [ ] **State of Mind XML key names** — do one Apple Health XML export, run `import-apple-health.py --debug` to confirm HKStateOfMindSample MetadataEntry key names

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

- [ ] **Reference-range flagging at write time** — compute `in/out/borderline` flag when inserting lab results; store it. `SELECT * FROM lab_results WHERE flag='out' ORDER BY date DESC` becomes "what's wrong with me right now."
- [ ] **Units registry** — `lab_markers.canonical_unit` + `markers_canonical.json` alias map. Without this, lab data is one provider-switch away from silent corruption (5.4 mmol/L vs 97 mg/dL for glucose).
- [ ] **lab_results ON CONFLICT DO UPDATE** — replace current destructive `INSERT OR REPLACE` (which resets `imported_at` and breaks audit trail) with `ON CONFLICT DO UPDATE SET ... WHERE value IS NOT excluded.value`.
- [ ] **enrichment_status column** — track topic extraction failures (`'ok'|'pending'|'failed'`) so backfilling missed episodes is a simple SELECT.
- [ ] **FTS on health_knowledge.topics** — topics is JSON in TEXT, not indexed by FTS5. Health questions matching a topic tag miss episodes whose summary doesn't repeat the tag. Concatenate topics into FTS5 indexed text at insert time.

---

### Architecture

- [ ] **True partial failure tracking in oura-sync.py** — OVERLAP_DAYS=1 is a bandaid. Real fix: refactor fetch_all to signal success/failure per chunk so last_synced only advances to confirmed data.
- [ ] **State of Mind XML behavioral tests** — add fixture test once HKStateOfMindSample key names confirmed from real export
- [ ] **Source-priority table-driven helper** — before adding a 4th data source per table; currently encoded per-importer
- [ ] **Rule 6c state machine** — "now or past?" question can silently drop a reading if unanswered. Default to "now", only ask when ambiguous.
- [ ] **oura_heartrate retention policy** — ~17K rows/year; add cleanup_old_heartrate helper before table hits 1M rows
- [ ] **Migration ladder refactor** — before v8, convert to dispatcher pattern. Not urgent at v5.
- [ ] **flock guard for oura-sync.py** — single-instance guard so overlapping launchd runs can't corrupt the DB.
- [ ] **DATA_CARD.md** — LLM-facing markdown listing what's in health.db: tables, units, date ranges, last-sync times. Cheap to write; agent currently has to introspect schema to know what data exists.

---

### Infrastructure

- [ ] **Subspace** — macOS multi-agent GUI, install on Freedom. Quick.
- [ ] **Sean Ryan feed** — `max_duration_seconds` filter for 5–7hr episodes that stay paused/skipped.
