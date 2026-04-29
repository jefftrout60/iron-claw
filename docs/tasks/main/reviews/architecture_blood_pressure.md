# Architecture Review: Blood Pressure Pillar

**Date**: 2026-04-27
**Scope**: commits `7edd224..e9211fc` — BP table, importer, report generator, query/log subcommands, AGENTS.md Rule 6c, SKILL.md Intents 7 & 8
**Reviewed**:
- `agents/sample-agent/workspace/health/health_db.py` (BP table addition)
- `agents/sample-agent/workspace/health/health_query.py` (`blood-pressure`, `bp-log` subcommands)
- `agents/sample-agent/workspace/health/test_blood_pressure.py` (unit tests)
- `scripts/import-blood-pressure.py` (Omron CSV importer, host-side)
- `scripts/bp-report.py` (HTML report generator, host-side)
- `agents/sample-agent/workspace/AGENTS.md` (Rules 6b/6c)
- `agents/sample-agent/workspace/skills/health-query/SKILL.md` (Intents 7 & 8)

---

## Summary

This is the cleanest pillar landed in `health.db` so far. The schema sits in the right place (the `workspace/health/` module recommended by the prior review), the upsert idempotency is correct on both write paths, the session-grouping algorithm is consistent across both consumers, and Rule 6c gives the agent a concrete, enforceable entry flow. The unit tests are well-targeted — they exercise session boundary conditions, upsert behavior, NULL pulse, and notes — and use the same in-memory pattern as the surrounding suite.

The risks are modest and largely about what's _missing_: there's no `busy_timeout` on the connection, the session-grouping algorithm is duplicated rather than shared, the iMessage entry flow has zero state on the agent side (so a missed Step 1 reply is invisible), and the deferred items from the prior review (backup, migration runner) are now load-bearing because the data is becoming user-entered and irreplaceable.

Overall: ship the pillar. Address Critical Issues 1-3 in the next pass, ideally before any third write path joins host CSV import + container iMessage entry.

---

## Strengths

1. **Schema lives in the right module.** `blood_pressure` is colocated with the other tables in `health_db.py` under `workspace/health/`, exactly per the prior review's Recommendation 1. The `sys.path.insert` dance from the importer and report generator is now pointing at one stable location.
2. **Correct idempotent upsert in both write paths.** `bp-log` (container) and `import-blood-pressure.py` (host) both use `ON CONFLICT(date, time) DO UPDATE` — no `INSERT OR REPLACE` audit-trail destruction (the C1 bug from the lab pillar). The importer also gates the update with `WHERE existing IS NOT excluded` so unchanged re-imports are no-ops.
3. **Unique index on (date, time)** is the right natural key — Omron timestamps to the minute, and the iMessage flow constructs the same minute-precision key. The two write paths can't produce duplicates.
4. **Session grouping is deterministic and well-tested.** `gap <= 30` is consistent in both copies, and `test_blood_pressure.py::TestBloodPressureSessionBoundary` covers 29/30/31-min cases explicitly.
5. **Test coverage matches the surface area.** Happy path, date filter (--start/--end), --days filter (with patched `date.today()`), session boundaries, empty table, upsert behavior, NULL pulse, notes — every public-ish behavior of `blood_pressure()` and `bp_log()` has a test. The patching pattern for `health_db.get_connection` is the same as `test_health_query.py`, so it's immediately familiar.
6. **Rule 6c is unusually well-scoped.** It names the trigger pattern (`NNN/NN` or `NNN/NN NN`), specifies the exact one question, lists "y/yes/now" handling, gives the exec template, and explicitly forbids medical advice and permission-asking. Far less ambiguous than most agent-behavior rules.
7. **`source` column is present from day one.** `'manual'` default, `'imessage'` for `bp-log`, `'omron_csv'` for the importer. Provenance tracking is in place before it gets needed.
8. **HTML report is self-contained and print-friendly.** Inline CSS, print stylesheet with `-webkit-print-color-adjust: exact`, page-break hints, and a Print/Save-as-PDF button. Doctor-deliverable without extra tooling.
9. **`statistics.mean` in `bp-report.py`** vs hand-rolled `sum()/len()` in `health_query.py` — both correct, but the report uses the standard library more idiomatically.
10. **Integration tests for the Omron importer** were added (commit e9211fc) — uncommon for one-shot importers.

---

## Critical Issues

### C1. No `busy_timeout` on the shared SQLite connection

**Severity: medium-high.** This is the most concrete answer to review question (1).

`health_db.get_connection()` enables WAL but does **not** set `busy_timeout`. WAL allows concurrent readers and a single writer, but if a writer arrives while another writer is in the middle of a transaction, the second one fails immediately with `database is locked` instead of waiting. With BP, the realistic conflict scenarios are:

- **iMessage `bp-log` (container) racing against `import-blood-pressure.py` (host).** Jeff exports an Omron CSV and runs the importer on Freedom while a BP reply text is being processed in-container. The importer has a multi-row transaction; the container's single-row insert can fail with `OperationalError: database is locked` and the agent sees `{"error": "..."}` from health_query.py's outer except.
- **`oura-sync.py` (host, daily 5am) racing against any container query.** Already a risk pre-BP, made more visible now that the system is also doing user-driven writes.
- **Anything during `bp-report.py` runs.** The reporter holds an implicit transaction over its SELECT; it only conflicts with writers if the writer commits during the read. WAL handles that case fine — readers don't block writers — but the reporter also calls `get_connection()` which runs `initialize_schema()` (DDL) on every invocation. DDL takes a write lock.

The schema-on-every-open behavior amplifies this: every consumer takes a write lock briefly, even read-only ones.

**Fix:**
```python
conn.execute("PRAGMA busy_timeout = 5000")  # 5s — covers any realistic write transaction
```
Add to `get_connection()` after `journal_mode = WAL`. This is one line and eliminates the race entirely for any transaction that finishes in <5s (all of them).

**Secondary fix:** consider gating `initialize_schema()` on a flag or `PRAGMA user_version` check so read-only consumers don't take a write lock just to confirm tables exist.

---

### C2. Session grouping algorithm is duplicated, not shared

**Severity: medium.** Direct answer to review question (2).

`_group_sessions` and `_make_session` exist verbatim in two files:
- `agents/sample-agent/workspace/health/health_query.py` (lines 169-216)
- `scripts/bp-report.py` (lines 31-75)

The implementations are identical today (gap_minutes=30, `gap <= gap_minutes`, same dict shape), but they will drift. Concrete drift risks:

1. **Threshold tuning.** If 30 minutes turns out to be wrong (e.g., Jeff takes 3 readings spread over 35 min per AHA guidance), one consumer gets fixed and the other doesn't.
2. **Output-shape additions.** If `_make_session` grows a `note_summary` or `bp_category` field for the doctor report, it needs to land in both places, or the iMessage query goes stale.
3. **Bug fixes.** A timezone or date-rollover edge case (a session that crosses midnight) would need fixing in both. Easy to forget — the report runs ad hoc, the query runs daily.
4. **Test coverage is asymmetric.** All session-boundary tests target `health_query.blood_pressure()`; `bp-report._group_sessions` has integration tests but no equivalent boundary unit tests. A divergence wouldn't be caught.

**Fix:** Move both functions into `workspace/health/health_db.py` (or a new `workspace/health/bp.py`) and import from both sites. The host-side script already does `sys.path.insert(0, str(_HEALTH_DIR))` and imports `health_db`, so importing one more symbol is free:

```python
# workspace/health/bp_sessions.py
def group_sessions(rows, gap_minutes=30): ...
def make_session(rows): ...
```

Both consumers become `from bp_sessions import group_sessions`. Tests stay in `test_blood_pressure.py` and now cover both consumers.

This should be done now, while there are exactly two consumers and they're still identical. The next consumer (a weekly BP cron, a CSV exporter, a chart endpoint) makes it three places.

---

### C3. Rule 6c has no state machine — a missed Step 1 reply is invisible

**Severity: medium.** Direct answer to review question (3).

Rule 6c describes a two-turn flow: agent asks "now or past?", user answers, agent logs. But there is **nothing in the system that tracks "we are between Step 1 and Step 2"**. If any of the following happens, the BP reading is silently lost:

- User sends `133/68 55`, agent asks "now or past?", user **never replies** or replies in a different conversation thread. Reading is gone.
- User sends `133/68 55`, agent asks the question, user replies with **another** reading instead (`also 128/72`). Rule 6c fires again on the new message — the original reading is orphaned.
- Agent **misses the trigger pattern** (e.g., user types `BP was 133 over 68, pulse 55` — Rule 6c trigger is `NNN/NN`, this won't match). No question is asked, no log is made, the message is treated as conversational chat.
- Agent runs a model that cuts the response off after the question, so Step 1 fires but Step 2 trigger never resolves on the next user turn (model doesn't connect "yes" to the prior BP).
- Cross-session: iMessage delivers the question, user replies hours later in what the agent treats as a new session — the BP context has been compacted out.

**There is no audit log of "I asked about a reading and never logged it."** The agent's quality coaching log might catch latency issues but won't catch silent drops of medical data.

**Fixes (any of these, in order of value):**

1. **Log the intent immediately at Step 1.** Insert into a `bp_pending` table (or write a JSONL line) with the parsed reading and a `pending` source. Step 2 confirms by upserting with a real timestamp; an unconfirmed row hangs around with `source='pending_imessage'` and a daily check (or the weekly summary) can flag it.
2. **Allow inline timestamping in the trigger pattern.** Extend the regex to `NNN/NN[ NN][ <date/time>]` so `133/68 55` defaults to "now" with a 5-second confirmation window, and `133/68 55 yesterday 9pm` skips Step 1 entirely. This collapses the two-turn flow to one turn for the common case ("now") and removes the loss surface.
3. **Switch the default.** Step 1 currently must wait. Default to "now" and ask **only** if a date hint appears (`yesterday`, a date, a time) — invert the question to "past or now?" and assume now if no answer in the next message. Rule 6c says "ONE question only", but the question itself is the loss surface; remove it for the common case.
4. **Add a daily reconciliation.** Run a cron that scans `imessage` source rows for the last 24h; if there's an asked-but-unanswered pattern in the chat log (requires log inspection), surface it. Heavier-weight than the others; only useful if the simpler fixes aren't enough.

**Recommendation:** combine (2) and (3) — make "now" the default, parse trailing date/time tokens, eliminate Step 1 except for genuinely ambiguous inputs. The current flow optimizes for safety against a wrong-time log, but an unlogged reading is strictly worse than a 5-minute-off timestamp.

A separate concern: Rule 6c is **not enforced anywhere except by the model's adherence to AGENTS.md**. The "FORBIDDEN: medical advice / seek-care language" list relies entirely on instruction-following. Given the prior session log entries about prompt-injection resistance (`gpt-5-mini` was chosen partly for this), this is acceptable — but a downgrade to a weaker model would silently re-introduce clinical commentary. Worth a one-line `tests/test_bp_rule_6c.py` integration test that runs a sample BP message through the agent and asserts the response doesn't contain "consult", "doctor", "elevated", "concerning", "seek care".

---

## Architectural Concerns

### A1. The `source` column will not survive Apple Health import without a refactor

**Direct answer to review question (4).** `source TEXT DEFAULT 'manual'` is good for today (3 known values: `manual`, `imessage`, `omron_csv`) but brittle for Apple Health and other future inputs. Specifically:

- **Apple Health gives you device-level provenance.** A single export contains BP readings from Omron Connect, an Apple Watch, manual iPhone Health entries, and possibly a third-party app. Collapsing all of these to `source='apple_health'` loses the underlying device, which is what doctors and trend-analysis actually need.
- **Apple Health duplicates already-imported Omron data.** If Omron Connect syncs to Apple Health and you also export the Omron CSV, you'll re-import the same readings under a different `source` value. The unique `(date, time)` index will dedup them — but which `source` wins is whichever came second, silently. The audit trail becomes meaningless.
- **There's no schema for "this reading came from device X, app Y, on date Z".** A flat `source` string can't encode it.
- **No `device_id` or `external_id`.** Apple Health entries have stable UUIDs; if you ever need to re-sync incrementally (Apple Health doesn't dedup itself), you'll need them.

**Fix (cheap, do now):**
```sql
ALTER TABLE blood_pressure ADD COLUMN source_app TEXT;        -- 'omron_connect', 'apple_health', 'manual_imessage'
ALTER TABLE blood_pressure ADD COLUMN source_device TEXT;     -- 'BP786N', 'apple_watch_se', 'iphone'
ALTER TABLE blood_pressure ADD COLUMN external_id TEXT;       -- Apple Health UUID, Omron record ID
CREATE INDEX idx_bp_external_id ON blood_pressure(external_id) WHERE external_id IS NOT NULL;
```
Then **change the upsert priority** when a re-import detects a conflict: prefer the row with the more specific source (Omron > Apple Health > manual) rather than "whoever wrote last." Encode this as a CASE expression or handle it in the importer.

**Fix (cheaper, do today):** rename `source` to `source_label` and document a controlled vocabulary (`omron_csv`, `imessage_manual`, `apple_health_omron`, `apple_health_watch`, `apple_health_manual`). At least the values are predictable.

### A2. No backup of `health.db` — and now it has irreplaceable data

The prior review (A3) flagged this. Pre-BP, the dataset was:
- Lab results: derived from an Excel file you keep
- Oura: re-fetchable from the API (with auth and rate limits)
- Health knowledge: derived from podcast vault

Post-BP, the dataset includes **iMessage-entered readings that exist nowhere else.** A `bp-log` row with `source='imessage'` is the only copy of that data point. No CSV, no API, no recovery path.

**Severity has gone up.** A1's "do this eventually" should now be "do this this week."

**Fixes (any of these are sufficient):**
1. **`launchd` daily backup** at 4 AM (before Oura sync at 5):
   ```bash
   sqlite3 /path/to/health.db ".backup '/path/to/backups/health-$(date +%F).db'"
   find /path/to/backups -mtime +30 -delete
   ```
2. **iCloud Drive bucket for the backup directory** (NOT for the live DB — WAL on iCloud will corrupt). The backup is a static `.db` file; it's safe.
3. **Time Machine includes `~`** but the backup window can be hours; for medical data, an hourly snapshot of `imessage`-source rows to a JSONL file is cheap insurance:
   ```bash
   sqlite3 health.db "SELECT json_object('date',date,'time',time,'sys',systolic,'dia',diastolic,'pulse',pulse) FROM blood_pressure WHERE source='imessage'" >> ~/health-backup/bp-imessage.jsonl
   ```

### A3. Still no migration runner

The prior review (A2) recommended `PRAGMA user_version` tracking. The BP commit added a new table and unique index via `CREATE IF NOT EXISTS`, which is the additive case `IF NOT EXISTS` handles fine. But the BP pillar makes the next non-additive change inevitable — likely candidates:

- Splitting `time` into `time_local` + `tz` once Apple Health import lands (Apple Health stores tz; Omron CSV doesn't)
- Adding NOT NULL to a column once data quality stabilizes
- Renaming `source` to `source_label` (per A1)
- Adding a generated `datetime` column for cleaner ORDER BY

Each of these requires a migration runner. The longer the schema goes without one, the more painful the eventual retrofit.

The prior review's snippet (`PRAGMA user_version` + a list of migrations) is still the right shape. Adding it now also retroactively pins the current schema as `user_version = 1`, giving you a known starting point.

### A4. Schema initialization on every connection is wasteful and adds lock pressure

`get_connection()` calls `initialize_schema()` on every open. This does ~30 `CREATE IF NOT EXISTS` statements and 3 `ALTER TABLE` attempts (each in its own try/except for the idempotency hack). Each one of these takes a brief write lock. Per-call cost is small but:

- It's the cause of the lock pressure noted in C1
- It runs for every `bp-report.py` invocation, every `bp-log` exec, every `oura-sync` cycle
- It coupled with `bp-log` exec runs every time the agent processes a BP message — so every BP iMessage run now does the full schema init

**Fix:** gate it on `PRAGMA user_version`:
```python
def get_connection(...):
    conn = sqlite3.connect(...)
    # ...PRAGMAs...
    cur = conn.execute("PRAGMA user_version").fetchone()[0]
    if cur < CURRENT_SCHEMA_VERSION:
        initialize_schema(conn)
        conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
    conn.row_factory = sqlite3.Row
    return conn
```
This pairs naturally with the migration runner from A3.

### A5. The two `ALTER TABLE` calls in `initialize_schema` are a smell

Lines 126-135 of `health_db.py` do try/except `ALTER TABLE` for `lab_markers.canonical_unit` and `health_knowledge.raw_transcript`. This works but it's exactly the migration-runner-by-other-means pattern. New columns will keep accumulating in this style; eventually some field needs a default value or a backfill step and the pattern breaks.

This is the same root cause as A3, but visible in the code today. Replacing them with a numbered migration list is cosmetically cleaner and removes the `OperationalError`-as-control-flow.

### A6. `bp-report.py` does `_REPO_ROOT = Path(__file__).parent.parent` — fragile

This works only as long as `bp-report.py` lives in `scripts/` and `health/` lives in `agents/sample-agent/workspace/health/`. Move either, and import breaks silently with `ModuleNotFoundError: health_db`.

`import-blood-pressure.py` has the same pattern. The prior review's `_bootstrap.py` recommendation (single sys.path helper) would solve this. Until then, at minimum add a hard assertion:

```python
_HEALTH_DIR = _REPO_ROOT / "agents/sample-agent/workspace/health"
assert _HEALTH_DIR.is_dir(), f"health module not found at {_HEALTH_DIR} — has the layout changed?"
```

### A7. `bp-report.py` uses `%-d` strftime — Linux/macOS only

`datetime.strptime(iso_date, "%Y-%m-%d").strftime("%B %-d, %Y")` — `%-d` is a glibc/BSD non-standard. Fails on Windows (which is moot for this project) but also will fail in some minimal Linux containers. If `bp-report.py` ever needs to run inside the agent container (it doesn't today), this breaks. Use `int(d.day)` or `str(d.day)` for portability.

### A8. `_group_sessions` walks readings by sort order, not by source

If Omron CSV import fills in historical data for a date that already has `source='imessage'` rows at slightly different (date, time) keys, the same physical reading session might show up as two sessions. The `(date, time)` natural key is at minute precision; if the user types a reading from 9:00 and the Omron CSV has the same reading at 09:00:08 with seconds (or vice versa), they're separate rows. Current Omron import truncates to HH:MM, so this is OK today — but it's worth a code comment in `parse_rows` noting the assumption, and a test that imports a CSV with seconds-level timestamps to make sure they're all rounded the same direction.

### A9. The agent has no "list recent BP readings" intent for verification

After `bp-log`, Step 4 confirms `Logged — {sys}/{dia}, pulse {pulse} on {date} at {time}.` But if Jeff wants to verify the last 5 readings landed correctly, the only option is `--days 1` which triggers session grouping output. Consider adding `--recent 5` (or a separate `bp-recent` subcommand) that returns the raw last N rows for quick verification. Low priority but cheap.

---

## Operational Issues

### O1. `bp-report.py` opens but doesn't close the connection if it errors mid-build

If `build_html` raises (e.g., a `KeyError` from a malformed row), `conn.close()` on line 335 is unreachable. Wrap in `try/finally` or use a `with` context. Same applies to `health_query.py`'s subcommand functions — they all open a connection and never close it. Python's GC handles it, but for a long-running process (the agent) this matters more than for a CLI script.

### O2. The HTML report doesn't anonymize anything

If Jeff hands the printed PDF to a doctor, no problem. If he ever emails it, the file has no patient identifier (his name, DOB) — which is **good for privacy** but **bad for the doctor's records**. Consider adding optional `--patient-name` and `--dob` flags so the report can be properly labeled when needed.

### O3. No log of `bp-log` activity beyond the DB row

The container exec call to `bp-log` doesn't write anywhere except the DB. If a series of BP entries went sideways (wrong dates, duplicate keys overwriting), you have no way to reconstruct what happened. The OpenClaw run log captures the exec invocation, but extracting it is painful. Consider a `bp_log_audit` table or a simple JSONL append in `bp-log` itself.

### O4. Importer prints to stdout/stderr but has no exit code on partial success

`import-blood-pressure.py` prints `Imported X, skipped Y` and exits 0. If 90% of rows skipped because of date format errors (warned but not failed), the user might not notice. Consider exit 2 if any row was skipped due to parse failure (vs. dedup skip).

### O5. The `bp-report.py` --output path collision risk

`Path(f"bp_report_{args.start}_{args.end}.html")` is relative to CWD. Running it twice from different directories gives you two reports in different places. Either resolve to absolute against a known directory or warn if the file exists.

---

## Missing Pieces for Genuine Usefulness

1. **`bp-recent N`** (per A9) for quick verification post-log.
2. **A "reading category" classifier** at write time (Normal / Elevated / Stage 1 / Stage 2 per AHA) stored as a column. **Don't surface it to the user via the agent** (Rule 6c forbids medical advice — correctly), but having it pre-computed is useful for the doctor report ("18 of 41 sessions Stage 1") and for filtering.
3. **A `bp_pending` reconciliation** (per C3, fix 1) — captures readings parsed from iMessage that didn't get a Step 1 confirmation.
4. **A weekly BP digest in the existing weekly summary** (Intent 5) is already wired in SKILL.md Step 2 — good. Add a 30/60/90-day moving average so trend changes are visible.
5. **An export-back-to-CSV** subcommand for the doctor's portal (some accept CSV, not PDF). Would also let Jeff diff the DB against a fresh Omron export to verify integrity.
6. **A daily cron** that imports any new Omron CSV from a watched directory (e.g., `~/Downloads/omron-*.csv`), so the agent's iMessage entries and the device-level export stay in sync without manual import calls.

---

## Recommendations (Prioritized)

**This week (before next pillar lands):**
1. Add `PRAGMA busy_timeout = 5000` to `get_connection()` (C1)
2. Daily `sqlite3 .backup` cron + 30-day retention (A2)
3. Move `_group_sessions` / `_make_session` into `workspace/health/bp_sessions.py` and import from both consumers (C2)
4. Add `external_id` and `source_app` columns to `blood_pressure`; document the source vocabulary (A1)

**This month:**
5. Add `PRAGMA user_version` migration runner; pin current schema as version 1 (A3, A4, A5)
6. Convert Rule 6c flow to "now is default; ask only on date hints" + parse trailing date tokens (C3)
7. Add `bp_pending` audit trail OR JSONL log of every `bp-log` invocation (C3, O3)
8. Add `try/finally` around connection open in `bp-report.py` and `health_query.py` subcommand functions (O1)
9. Sketch the Apple Health import path; lock in the source vocabulary before the first import (A1)

**Eventually:**
10. Add `bp-recent N` subcommand for post-log verification (A9)
11. Add reading category classifier at write time, displayed only in the doctor report (Missing Piece 2)
12. Add `tests/test_bp_rule_6c.py` integration test (no clinical commentary, no permission-asking) (C3)
13. Add `--patient-name` / `--dob` flags to `bp-report.py` (O2)

---

## Closing Note

The pillar is solid v1 work. The schema is in the right place, the upsert is correct, the tests are real, and Rule 6c is enforceable. The Critical Issues are all small, mechanical fixes. The Architectural Concerns are mostly about deferring less now that the data is irreplaceable: backup the DB, version the schema, and make Apple Health import the next-shoe-to-drop you're already planning for. None of this blocks shipping. All of it gets harder in a month.
