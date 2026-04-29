# Scope: Apple Health Multi-Source Health Data Import
Date: 2026-04-29
Status: Scoped

---

## The Problem

Health data is fragmented across four systems that don't talk to each other:
- **Withings scale** — weight, body fat%, lean mass (app-only; doesn't push composition to Apple Health)
- **Apple Health / Apple Watch** — BP (Omron sync unreliable), steps, daylight, workout summaries
- **Evernote** — actual workout detail: exercises, sets, reps, weights (Planned vs Actual weekly table)
- **Oura** — already synced; but tags (sauna, alcohol, etc.) are not yet captured

The result: multi-pillar correlations that an LLM could surface — "do hard workout days predict next-day HRV drops?", "does BP track with body fat% over time?", "are my sleep scores better on sauna days?" — are impossible because the data lives in silos.

The existing health.db has BP readings and Oura metrics. This scope closes the gap.

---

## Target User

Jeff — personal health intelligence via iMessage. Not clinical. The value is pattern recognition across pillars that would require manual spreadsheet work to find otherwise.

---

## Success Criteria

- Can text "what's my avg weight this month?" and get an answer
- Can text "show me my workouts this week" and see type + duration + key metrics
- Doctor report can include weight trend alongside BP trend
- An LLM query can correlate HRV with prior-day workout intensity
- An LLM query can correlate BP with sleep quality
- An LLM query can show body fat% trend against BP trend over months
- Oura tag days (sauna, etc.) are queryable against any other metric

---

## User Experience

The user doesn't change how they track data — scale, Watch, and Evernote stay as-is. The system pulls from those sources. iMessage queries against the new data types work the same as existing BP and Oura queries.

**Import flows**:
- Withings body composition: cron job hitting Withings API (like Oura sync), automatic ongoing
- Apple Health (steps, daylight, BP backfill, workout summaries): manual XML export → one-time backfill, then iOS Shortcut → iCloud Drive → Mac cron for ongoing
- Evernote workout detail: manual ENEX/HTML export → one-time parser, Jan 1 2025 forward; manual re-export periodically
- Oura tags: extend existing oura-sync.py with tags endpoint

**Dedup behavior**: all imports are idempotent — re-running never creates duplicates.

---

## Scope Boundaries

### ✅ IN — This Pass

**Data: Withings API**
- Weight (lbs/kg)
- Body fat percentage
- Fat mass
- Lean body mass
- Source tag: `withings_api`
- Ongoing: daily cron job

**Data: Apple Health XML**
- Blood pressure historical backfill (dedup against existing Omron/iMessage rows by datetime)
- Steps (daily totals)
- Time in daylight (daily minutes)
- Workout summaries: type, date, duration, calories, avg HR, max HR
- Source tag: `apple_health`
- Initial: manual XML export → parser; Ongoing: iOS Shortcut → iCloud Drive → cron

**Data: Evernote workout detail**
- Actual exercises performed (name, sets, reps, weight)
- Linked to corresponding Apple Watch workout (same date + type)
- Scope: Jan 1 2025 forward
- Access: manual ENEX/HTML export → one-time parser
- Ongoing: manual re-export (Evernote API no longer available)

**Data: Oura tags**
- Enhanced tags: sauna, alcohol, late meal, stress, etc.
- Extend existing oura-sync.py
- Ongoing: same daily cron as existing Oura sync

**Schema additions** (new tables in health.db):
- `body_metrics` (date, weight_lbs, body_fat_pct, fat_mass_lbs, lean_mass_lbs, bmi, source)
- `activity_daily` (date, steps, daylight_minutes, source)
- `workouts` (date, workout_type, duration_min, calories, avg_hr, max_hr, effort_rating, source)
- `workout_exercises` (workout_id, exercise_name, set_number, reps, weight_lbs, notes)
- `oura_tags` (date, tag_name, tag_value, source)

**iMessage query support** for new data types (new health_query.py subcommands):
- `body-metrics` — weight, BF%, lean mass trend
- `activity` — steps, daylight
- `workouts` — list, search by type or date range
- New AGENTS.md Rule 6b extensions for each

### ❌ OUT — This Pass

- All-time Evernote historical (pre-2025) — future scope
- Evernote image parsing (Watch screenshots embedded in notes) — get workout summary from Health XML instead
- iMessage manual weight logging — replaced by Withings API automation
- Oura main metrics changes — already handled
- Height
- DEXA scan data
- Complex correlation skill (query across pillars in one iMessage turn) — next session after data is in
- BP Omron sync reliability fix — separate investigation; backfill from Apple Health covers the gap

### ⚠️ Maybe/Future

- All-time Evernote historical import
- Weekly summary email including weight and workout trends
- Doctor report extension (weight + BF% alongside BP)
- Automated correlation suggestions ("notable patterns this week")
- iMessage tags for workouts ("log sauna 20min") → oura_tags or new table
- Apple Watch standalone workout data via HealthKit continuous sync (instead of manual XML)

---

## Constraints

- Python 3 only, stdlib + sqlite3, no new pip dependencies beyond what's needed for Withings OAuth
- All scripts run on host (Mac), not inside agent container
- health.db path: `agents/sample-agent/workspace/health/health.db`
- busy_timeout=5000 already set — safe for concurrent writes
- Evernote: no API access — manual export only
- Apple Health XML can be large (100MB+) — parser must stream, not load all into memory
- Withings API requires OAuth2 — one-time setup, then token refresh

---

## Integration Points

**Touches**:
- `health_db.py` — new table schema, migration via PRAGMA user_version
- `oura-sync.py` — add tags endpoint
- `health_query.py` — new subcommands for each data type
- `agents/sample-agent/workspace/AGENTS.md` — Rule 6b extensions
- `agents/sample-agent/workspace/skills/health-query/SKILL.md` — new intents

**New scripts**:
- `scripts/withings-sync.py` — Withings API cron (modeled on oura-sync.py)
- `scripts/import-apple-health.py` — Apple Health XML parser (BP, steps, daylight, workouts)
- `scripts/import-evernote-workouts.py` — ENEX/HTML parser for workout tables

**Avoids**:
- `health_store.py` public API (append_entry, load_all, find_by_show) — do not change
- Oura sync schedule or existing endpoints

**Dependencies**:
- Withings API credentials (developer account, OAuth2 client ID + secret) → `.env`
- iOS Shortcut setup (user action, not code)
- Evernote manual export (user action)

---

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| Withings API over Apple Health XML for body composition | Health only receives weight from Withings, not fat/lean — must go direct |
| Apple Health XML for workout summaries | Reliable structured data; avoids OCR of Watch screenshots in Evernote |
| Evernote: manual export only | API access discontinued; ENEX format is parseable |
| Evernote scope: Jan 1 2025 forward | Pragmatic; all-time is a future pass |
| Oura tags via API extension | Already have the sync infrastructure; tags endpoint exists in v2 |
| New health.db tables, not expanding existing | Clean schema boundaries per pillar |
| DB migration via PRAGMA user_version | Arch review requirement before next schema change |

---

## Risks

- **Withings OAuth setup**: first-time complexity; may need developer account approval
- **Apple Health XML size**: exports can be 200MB+; streaming parser required
- **Evernote table format variation**: parser will need testing across note variations (cells with images, empty rows, format drift over years)
- **Apple Health BP gaps**: Omron sync has been unreliable; backfill may still be incomplete; no fix for this in scope
- **Workout dedup between sources**: matching Evernote rows to Apple Watch workouts (same date + type) may have edge cases (two workouts same day, type mismatch)
- **Scope creep**: correlation queries are very tempting to add here — deliberately out of scope; data must be in before queries are built

---

## Complexity

**L (Large)** — 4 data sources, 5 new DB tables, 3 new import scripts, 1 new sync cron, query layer extensions, and schema migration framework. Recommended: break into 2-3 sessions:
1. **Session A**: Schema migration framework + Withings API sync + body_metrics table (highest value, cleanest integration)
2. **Session B**: Apple Health XML import (BP backfill, steps, daylight, workout summaries)
3. **Session C**: Evernote parser + workout_exercises table + Oura tags extension + query layer (health_query.py + AGENTS.md + SKILL.md)

---

## Next Steps

Run `/spectre:plan` against this scope document to generate the implementation plan and task breakdown.
