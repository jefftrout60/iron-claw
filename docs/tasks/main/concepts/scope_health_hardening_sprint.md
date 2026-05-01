# Scope: Health DB Hardening Sprint

*Created: 2026-05-01*

---

## The Problem

The health.db system works, but has data integrity, reliability, and observability gaps that create real risk before building higher-value features on top. Specific failure modes:

- **Lab data corruption**: `INSERT OR REPLACE` destroys imported_at audit trail on re-import; no unit normalization means a lab provider switch silently corrupts values (5.4 mmol/L stored as 5.4 mg/dL); no reference-range flags means you can't ask "what's wrong with me right now?"
- **Search quality**: health_knowledge topics are stored as JSON blobs, not indexed by FTS5 — health questions miss episodes whose summary doesn't repeat the tag word. Topic extraction failures are invisible (no enrichment_status tracking).
- **Sync reliability**: oura-sync advances last_synced even on partial fetch failures (OVERLAP_DAYS=1 is a bandaid, not a fix); no flock guard means overlapping launchd runs can corrupt the DB; oura_heartrate grows ~17K rows/year with no cleanup.
- **Agent behavior**: Rule 6c drops a reading if the user doesn't answer "is this now or a past date?" — should default to now.
- **Observability**: no DATA_CARD.md means the agent must introspect the schema every session to know what data exists and what date ranges are covered.
- **Ops hygiene**: container secrets unrotated; Subspace not installed; State of Mind XML key names unconfirmed (blocks behavioral tests).

---

## Target Users

**Primary — Jeff**: Wants to query health.db via iMessage with confidence that the data is correct and complete. Needs this sprint done before building daily_brief and correlation queries.

**Secondary — The agent**: Reads DATA_CARD.md at session start to answer "what data do you have?" without DB introspection.

---

## Success Criteria

- Lab rows survive re-import with imported_at intact (ON CONFLICT fix)
- Provider unit switch doesn't silently corrupt lab values (units registry wired into lab importers)
- `SELECT * FROM lab_results WHERE flag='out'` returns correctly flagged rows (reference-range flagging)
- oura-sync advances last_synced only to the last confirmed chunk (full fetch_all refactor)
- Overlapping oura-sync launchd runs are blocked (flock guard)
- oura_heartrate cleanup runs automatically before table hits 1M rows
- health_knowledge topic queries find episodes whose summary doesn't repeat the tag word (FTS topics)
- Failed enrichment episodes are discoverable: `SELECT * FROM health_knowledge WHERE enrichment_status='failed'`
- Agent reads DATA_CARD.md for pillar/date-range context without querying the DB
- State of Mind XML key names confirmed; behavioral fixture tests written
- Container secrets rotated; Subspace installed on Freedom
- "185.2" → body_metrics entry without agent asking "now or past?" (Rule 6c default-to-now)

---

## User Experience

The primary UX improvement is the iMessage agent becoming more reliable and accurate:

- **Fewer dropped readings**: Rule 6c defaults to "now" so bare-number entries don't get silently dropped waiting for a clarification response.
- **Better health knowledge answers**: FTS on topics finds relevant episodes the current query would miss.
- **Agent knows what it has**: DATA_CARD.md gives the agent instant context on pillars, row counts, date ranges, and last-sync times — no more "let me check the schema."
- **Lab answers you can trust**: Reference-range flags make "what's out of range?" a simple SELECT instead of requiring the agent to know normal ranges.

**DATA_CARD.md flow**: script runs on each sync → output written to `agents/sample-agent/workspace/health/DATA_CARD.md` → mounted in container workspace → agent reads it at session start.

---

## Scope Boundaries

### IN

**Ops (no code)**
- Rotate container secrets — run `scripts/rotate-container-secrets.sh`, update OpenAI/Telegram/Gmail credentials
- Install Subspace on Freedom — macOS multi-agent GUI

**State of Mind verification + tests** (sequential: export → confirm → write tests)
- Apple Health XML export → run `import-apple-health.py --debug` → confirm HKStateOfMindSample MetadataEntry key names
- Write behavioral fixture tests once key names confirmed

**DB integrity** (one migration block: v5 → v6)
- `lab_results ON CONFLICT DO UPDATE` — replace destructive `INSERT OR REPLACE`; preserve imported_at on re-import, only update value when it changes
- Units registry — `lab_markers.canonical_unit` column + `markers_canonical.json` alias map + alias resolution wired into lab importers
- Reference-range flagging at write time — compute `in`/`out`/`borderline` flag on lab insert; store in `lab_results.flag` column
- `enrichment_status` column on `health_knowledge` — add `ok`/`pending`/`failed` tracking; backfill existing rows as `ok`

**Sync reliability**
- True partial failure tracking in oura-sync.py — full refactor of `fetch_all` return semantics; only advance `last_synced` to the last fully confirmed chunk; OVERLAP_DAYS=1 stays as a safety net but is no longer the primary protection
- flock guard for oura-sync.py — single-instance guard via `flock` so overlapping launchd runs block rather than corrupt
- oura_heartrate retention policy — `cleanup_old_heartrate()` helper that deletes rows beyond a configurable retention window; wired into sync run

**Search/retrieval quality**
- FTS on health_knowledge.topics — concatenate topics JSON into FTS5 indexed text column at insert time; existing rows backfilled

**Observability**
- DATA_CARD.md auto-generation — `scripts/generate-data-card.py` queries health.db for table row counts, date ranges, last-sync times; outputs `agents/sample-agent/workspace/health/DATA_CARD.md`; run on each sync and on-demand

**Agent behavior**
- Rule 6c state machine — default to "now" for bare readings; only ask when message contains explicit but ambiguous temporal reference; update health_query.py routing and AGENTS.md Rule 6c wording

**Parallel (Jeff-led, not a sprint deliverable)**
- iMessage manual test checklist (14 queries, `docs/tasks/main/imessage-test-checklist.md`) — Jeff running this manually in parallel while sprint work proceeds; findings may surface bugs that land in the sprint

### OUT

- New health pillars: weight iMessage entry, DEXA scans, macros, supplements
- New query subcommands: daily_brief, correlations view, trends snapshot, HRV trend, State of Mind vs Oura readiness
- Oura context injection
- Bulk podcast ingestion
- Cross-pillar correlation skill
- Weekly summary email extension
- Agent access control
- Embedding/vector search
- events table
- All-time Evernote pre-2025 backfill
- active_energy column in activity_daily
- Source-priority table-driven helper
- Migration ladder refactor (not urgent until v8)

### MAYBE / FUTURE

- Doctor periodic BP summary email — cadence TBD, blocked on Jeff confirming usefulness

---

## Constraints

- health.db is at schema **v5** — all column additions (units registry, enrichment_status, reference-range flag) go in a single **v6 migration block**
- Units registry alias resolution scoped to **lab importers only** — Oura/Withings/Apple Health use standardized units; unit ambiguity is a lab-data problem (Quest vs. Labcorp reporting formats)
- DATA_CARD.md generation script runs **on the host** (not inside container); output path must be inside agent workspace so the container picks it up without a restart
- flock guard must not break existing launchd plist — test after adding
- State of Mind XML behavioral tests **must wait** for real export key name confirmation — no fixture tests against unconfirmed key format
- `markers_canonical.json` alias map must be seeded from **real lab data in the DB** — verify against actual marker names before wiring

---

## Integration

**Files touched:**

| File | Change |
|------|--------|
| `agents/sample-agent/workspace/health/health_query.py` | Rule 6c state machine, FTS topics query |
| `scripts/oura-sync.py` | Partial failure refactor, flock guard, heartrate retention |
| `scripts/import-apple-health.py` | State of Mind key name fix (if needed after export) |
| Lab importer(s) | ON CONFLICT fix, units alias resolution, reference-range flagging |
| `scripts/generate-data-card.py` | New script |
| `agents/sample-agent/workspace/health/DATA_CARD.md` | Generated output |
| `agents/sample-agent/workspace/AGENTS.md` | Rule 6c wording update |
| `agents/sample-agent/workspace/health/migrations.py` (or equivalent) | v6 migration block |
| `markers_canonical.json` | New file — units alias map |

**Does not touch:**
- Oura/Withings API client logic (no unit ambiguity there)
- Podcast pipeline
- Container config / openclaw.json
- Agent channels (Telegram, iMessage routing layer)
- Embedding/vector infrastructure

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Units registry scope | Lab importers only | Oura/Withings/Apple Health use standardized units; corruption risk is specifically lab provider switching |
| DATA_CARD.md | Auto-generated per sync | Manual doc goes stale; auto-gen keeps agent context current without future maintenance |
| Oura partial failure | Full fetch_all refactor | OVERLAP_DAYS=1 is a bandaid; building daily_brief on unreliable last_synced is risky |
| Rule 6c "ambiguous" trigger | Explicit relative temporal references only ("the other day", "last week", "Tuesday") | Bare numbers and "today/this morning" → always default to now; false positives (asking unnecessarily) are worse than wrong timestamps the user can correct |
| State of Mind tests | Write after XML export confirms key names | Fixture tests against unconfirmed key format are worse than no tests |
| All 14 items in scope | No time constraint — work until done | Jeff's call |

---

## Risks

- **Units alias map bootstrap**: `markers_canonical.json` needs to cover the actual marker name formats in the DB. First step should be a query to enumerate distinct marker names and confirm the alias map covers them all.
- **oura-sync refactor regression risk**: fetch_all return semantics change touches the core sync loop. Existing behavioral tests must pass; consider running a sync in dry-run/verbose mode after the refactor before pushing.
- **v6 migration sequencing**: multiple items add columns in the same migration. All column additions must be coordinated into one `MIGRATION_V6` block to avoid double-migration bugs.
- **Rule 6c false negatives**: the "default to now" behavior could timestamp a past reading incorrectly. Risk is low — users can correct — but worth noting in AGENTS.md.
- **DATA_CARD.md staleness window**: generated on each sync, but if a sync fails, the card won't update. Acceptable given the card is informational, not authoritative.

---

## Suggested Execution Order

1. **Ops first** — secrets rotation, Subspace — no code risk, clears the immediate list
2. **State of Mind XML export** — do early since it's a real-world dependency that might reveal surprises
3. **DB integrity (v6 migration)** — ON CONFLICT, units registry, reference-range flag, enrichment_status — one migration, test suite covers it
4. **oura-sync reliability** — partial failure refactor, flock guard, heartrate retention — isolated to one file, high-risk, do with tests
5. **Search quality** — FTS topics — isolated to health_knowledge table and insert path
6. **DATA_CARD.md** — new script, low risk
7. **Rule 6c state machine** — health_query.py + AGENTS.md
8. **State of Mind behavioral tests** — after step 2 confirms key names

---

## Next Steps

Complexity: **M** (14 items; oura-sync refactor is highest risk; DB migration coordinates multiple changes)

→ `/spectre:plan` to break into tasks
→ `/spectre:execute` to start building
