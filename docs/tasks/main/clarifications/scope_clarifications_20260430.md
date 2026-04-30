# Scope Clarifications — Health Hardening + iOS Sync Sprint
**Date**: 2026-04-30
**Confirmed boundaries**: Secrets rotation, iMessage test script, HRV backfill+fix, source-priority de-dup, migration tests, sync-status subcommand, iOS Shortcut → iCloud Drive auto-import. Vector search is OUT/backlog.

---

## Q1 — Secrets rotation
Rotating OpenAI, Telegram, and Gmail app password in `.env` means generating new credentials in the respective dashboards. Should this scope item produce:
- (a) A step-by-step checklist of where to go and what to update (you do the rotating), or
- (b) Something automatable (unlikely given external dashboards)?

<response>
automatic is preferred
</response>

---

## Q2 — HRV: which field and how to handle multiple sleep sessions per day
`oura_sleep_sessions` has an `avg_hrv` column. If a day has multiple sessions (e.g. a nap + overnight), how should `oura_daily.avg_hrv_rmssd` be populated?
- (a) Use the longest session's `avg_hrv`
- (b) Average across all sessions for that day
- (c) Use the session flagged as the primary/main sleep

<response>
use session flagged as main sleep. longest if that is not available
</response>

---

## Q3 — Apple Health + Evernote last-import tracking
To make sync-status show all 4 sources, the importer scripts need to write a timestamp somewhere after each run. Should this go into the existing `sync_state` table (consistent with Withings/Oura), or a separate mechanism?

<response>
your choice. existing table would seem to make most sense
</response>

---

## Q4 — iOS Shortcut: Mac-side iCloud Drive path
The Mac-side launchd watcher needs to know which folder to watch. What iCloud Drive path should the Shortcut drop the file into? E.g. `~/Library/Mobile Documents/com~apple~CloudDocs/Health/` or a custom folder?

Also: should the exported filename be fixed (e.g. `apple_health_export.xml`) or date-stamped?

<response>
your folder is ok. don't care about the filename vs date stamp. whatever makes the code most stable and future proof. 
</response>

---

## Q5 — iOS Shortcut: what I can actually build
I can build everything on the Mac side (launchd watcher, auto-import script, logging). The iOS Shortcut itself must be built by you on your phone — I can produce step-by-step instructions. Is a written guide sufficient, or do you want a Shortcut `.shortcut` file if that's exportable?

<response>
i can build it off instructions
</response>
