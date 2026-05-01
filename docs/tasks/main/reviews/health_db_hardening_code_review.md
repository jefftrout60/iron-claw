# Code Review: Health DB Hardening Sprint
*Reviewed: 2026-05-01*

## Overall Assessment
Well-executed sprint. Code is clean, follows established patterns, and addresses real problems. The FTS rebuild, per-chunk sync tracking, and lock guard are all correct. Two issues require fixes before the sprint is considered done.

**Scores:**
- Security Posture: 9/10
- Logic Correctness: 8/10 (two fixes needed)
- Code Quality: 9/10
- Production Readiness: 8/10

---

## 🔥 HIGH Priority — Fix Before Done

### 1. cleanup_old_heartrate runs on total sync failure
**File:** `scripts/oura-sync.py` — `sync_heartrate()`

`cleanup_old_heartrate(conn)` is called unconditionally, outside the `if last_good_date:` guard. If the entire heartrate sync fails (all chunks raise FetchError), no new data was written but old rows are still deleted. Over repeated failures, this would silently erode heartrate history.

**Fix:** Move `cleanup_old_heartrate(conn)` inside the `if last_good_date:` block.

### 2. _compute_flag division-by-zero when ref_low or ref_high is 0.0
**File:** `scripts/import-blood-labs.py` — `_compute_flag()`

```python
(ref_low - value) / ref_low  # ZeroDivisionError if ref_low == 0.0
(value - ref_high) / ref_high  # ZeroDivisionError if ref_high == 0.0
```

Unlikely for most labs but possible for markers with a lower bound of 0 (e.g., qualitative ratios). Crashes the entire import.

**Fix:** Guard the division: if ref_low == 0 or ref_high == 0, return 'out' directly.

---

## ⚠️ MEDIUM Priority

### 3. Redundant datetime import in cleanup_old_heartrate
**File:** `scripts/oura-sync.py` ~line 177

`from datetime import date, timedelta` is a local import but both are already imported at module level.

### 4. body_log ON CONFLICT with time=NULL
**File:** `agents/sample-agent/workspace/health/health_query.py` — `body_log()`

ON CONFLICT(date, time) fires when both match, including both being NULL. Two iMessage weight entries on the same day will conflict and the second overwrites the first. Probably acceptable for personal use — just document the behavior.

### 5. oura_daily still uses INSERT OR REPLACE
**File:** `scripts/oura-sync.py`

Inconsistent with the stated convention (ON CONFLICT DO UPDATE). Low impact since `fetched_at` is auto-populated, but worth flagging for future work.

---

## 💡 LOW Priority

### 6. FTS _fts_quote forces phrase matching
"apob ldl" → `"apob ldl"` (exact phrase, not separate terms). Deliberate injection-prevention choice. Fine, but worth documenting as user-experience trade-off.

### 7. health_store closes connections it didn't open
Makes testing harder (hence the _UnclosableConnection wrapper). Acceptable for now.

---

## Confirmations (things the review verified as correct)

- FTS snippet column index 1 = summary in both old and new schema ✅
- mkdir lock guard is correct macOS approach (flock unavailable) ✅
- v6 migration block structure is correct (try/except per ALTER TABLE, FTS drop+recreate+repopulate, version stamp before commit) ✅
- topics_text backfill loop correctly guarded to only run on NULL rows ✅
- fetch_all correctly preserves `break` for normal pagination end (missing next_token) ✅
- Per-chunk last_synced tracking correctly handles zero-chunk total failure ✅
- alias map keys include units, consistent with marker name format in DB ✅
