# Validation Gaps: podcast-summary skill
*Generated: 2026-03-22*

## Summary
- **Overall Status**: Needs Work
- **Requirements**: 19 of 26 fully delivered; 7 gaps requiring remediation
- **Gaps Found**: 5 requiring remediation (2 missing files, 1 CLI contract fix, 1 wiring gap, 1 missing feature)
- **Acceptable deviations**: 4 (iMessage path, strategy cache durability, quality gate flag, per-episode override)

---

## Gap Remediation Tasks

### 📦 Phase 1: Critical Gaps — Missing Whisper Bridge Files

#### 📋 [1.1] Create `setup-whisper-bridge.sh` (REQ-010)
**Requirement**: Script that automates whisper.cpp build + model download + LaunchAgent install
**Current State**: OPERATIONS.md fully documents the steps but the script itself does not exist. All paths and commands are documented at OPERATIONS.md:33-66.
**Gap**: `scripts/setup-whisper-bridge.sh` is referenced throughout OPERATIONS.md but the file was never committed.

- [ ] **1.1.1** Create `scripts/setup-whisper-bridge.sh`
  - **Produces**: executable setup script
  - **Consumed by**: operator running `bash setup-whisper-bridge.sh` per OPERATIONS.md
  - [ ] Script checks for prerequisites: `cmake`, `git`, `ffmpeg` — prints clear error if missing
  - [ ] Accepts `--model-dir` and `--port` arguments; defaults documented in `--help`
  - [ ] Clones/pulls whisper.cpp, runs cmake build with Metal support, downloads both model files
  - [ ] Installs LaunchAgent plist to `~/Library/LaunchAgents/` and runs `launchctl load`
  - [ ] Idempotent: safe to re-run (checks if binary/models/plist already exist before overwriting)

#### 📋 [1.2] Create LaunchAgent plist template (REQ-025)
**Requirement**: `com.ironclaw.whisper-bridge.plist` for always-on whisper-server
**Current State**: OPERATIONS.md documents `launchctl load/unload` commands and references `~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist` but the template file is entirely absent from the repo.
**Gap**: Neither the plist template nor the install script exist.

- [ ] **1.2.1** Create `scripts/launchagents/com.ironclaw.whisper-bridge.plist`
  - **Produces**: LaunchAgent plist template with `__WHISPER_DIR__` and `__PORT__` placeholders
  - **Consumed by**: `setup-whisper-bridge.sh` (1.1) — copies to `~/Library/LaunchAgents/` after substituting paths
  - [ ] Starts `whisper-server` on `0.0.0.0:__PORT__` with `--convert` flag and `ggml-small.en.bin` as default model
  - [ ] `KeepAlive=true` so macOS restarts it if it crashes
  - [ ] `StandardOutPath`/`StandardErrorPath` directed to `/tmp/whisper-bridge.log` for debugging

---

### 📦 Phase 2: High Priority — SKILL.md CLI Contract Mismatches

#### 📋 [2.1] Fix SKILL.md exec commands for Intents 1–4 (REQ-014, REQ-015, REQ-016, REQ-003)
**Requirement**: Agent can invoke on_demand.py and add_feed.py via exec
**Current State**: All four SKILL.md intents have exec commands with wrong CLI flags. The Python scripts work correctly at function level but every exec call would fail with "unrecognized arguments".
**Gap**: SKILL.md was written before the final CLI interface was finalized. Four intents need flag corrections.

- [ ] **2.1.1** Fix Intent 1 exec command (specific episode — REQ-014)
  - **Current SKILL.md**: `on_demand.py --show "{show}" --episode "{ep}" --url "{url}"`
  - **Actual CLI**: `on_demand.py --query "{query}" --agent sample-agent`
  - [ ] SKILL.md Intent 1 exec updated to use `--query` with a composed query string
  - [ ] SKILL.md documents query format: `"#NNN"` for episode number, full URL for URL queries, title keywords for fuzzy match

- [ ] **2.1.2** Fix Intent 2 exec command (semantic/topic search — REQ-015)
  - **Current SKILL.md**: `on_demand.py --show "{show}" --topic "{keywords}"`
  - **Actual CLI**: `on_demand.py --query "{keywords from show}" --agent sample-agent`
  - [ ] SKILL.md Intent 2 exec updated to use `--query` with keywords/show name combined
  - [ ] Note in SKILL.md: matching is keyword-based; LLM-assisted best-match not yet implemented

- [ ] **2.1.3** Fix Intent 3 exec command + wire `--depth` through on_demand.py (REQ-016)
  - **Current SKILL.md**: `on_demand.py --episode-id "{id}" --depth extended`
  - **Actual CLI**: `--episode-id` and `--depth` flags do not exist
  - [ ] Add `--depth` CLI flag to `on_demand.py` (`argparse`, default `"standard"`, choices `["standard", "extended"]`)
  - [ ] Pass `depth` from CLI into `run()` function signature and down to `summarizer.summarize(depth=...)`
  - [ ] When depth=extended and episode already cached, re-summarize at extended depth (don't serve cached standard summary)
  - [ ] SKILL.md Intent 3 exec updated to `on_demand.py --query "{query}" --depth extended --agent sample-agent`

- [ ] **2.1.4** Fix Intent 4 exec command (feed management — REQ-003)
  - **Current SKILL.md**: `add_feed.py --name "{name}" --rss-url "{url}"` / `add_feed.py --name "{name}" --state inactive`
  - **Actual CLI**: `add_feed.py --title "{title}" --url "{url}"` / `add_feed.py --id "{id}" --state inactive`
  - [ ] SKILL.md Intent 4 exec commands updated to use `--title`/`--url`/`--id` (matching add_feed.py actual flags)
  - [ ] SKILL.md documents that `--list` can be used first to find the correct feed ID for state changes

---

### 📦 Phase 3: Medium Priority — Missing Style Correction Feature

#### 📋 [3.1] Add summary_style correction path (REQ-024)
**Requirement**: User can correct show classification via Telegram ("that Triggernometry summary was too short")
**Current State**: `summary_style` field exists in feeds.json and auto-classification writes it. But there is no write path from an agent command — no script accepts a style correction, and SKILL.md has no intent for it.
**Gap**: Correction path is entirely missing. `update_feed.py` referenced in tasks.md was never created.

- [ ] **3.1.1** Add `--style` flag to `add_feed.py` for style corrections
  - **Produces**: `set_style(feed_id, new_style)` function + `--style` CLI flag in add_feed.py
  - **Consumed by**: SKILL.md style correction intent → exec → add_feed.py
  - [ ] `--style` accepts one of 5 valid styles: `deep_science`, `long_form_interview`, `commentary`, `hunting_outdoor`, `devotional`
  - [ ] Writes `summary_style` to feeds.json for the matching feed via atomic vault save
  - [ ] Prints confirmation JSON: `{"status": "ok", "feed_id": "...", "summary_style": "..."}`

- [ ] **3.1.2** Add style correction intent to SKILL.md
  - **Produces**: Intent 5 in SKILL.md covering style correction and manual health tagging
  - **Consumed by**: OpenClaw agent when user gives style feedback
  - [ ] Intent triggers: "give me more detail", "that summary was too short", "use the science format for X"
  - [ ] Exec command: `add_feed.py --id "{feed_id}" --style "{style}" --agent sample-agent`
  - [ ] After correction: offer to re-summarize the last episode at the new style depth
  - [ ] Also documents: "Add [episode] to health store" → `health_store.py` append with `tagged_by: "user"`

---

## Acceptable Deviations (Not Remediated)

| REQ | Gap | Rationale |
|-----|-----|-----------|
| REQ-007 | iMessage path not implemented — Telegram only | "Telegram/iMessage" in scope means whichever channel is active. Telegram is the primary channel. |
| REQ-013 | Transcript strategy failure cache not durably persisted after each episode | Edge case: cache is only lost on mid-run crash. Normal operation unaffected. Not worth adding per-episode saves. |
| REQ-018 | Per-episode health override not implemented | The spec says "per-episode override" but the task breakdown only implements feed-level tiers. Minor missing feature for v1. |
| REQ-023 | No runtime quality gate — only manual `--no-email` flag | REQ-023 was a deployment gate ("no emails until Whisper is live"), not a runtime filter. The flag satisfies the intent. |

---

## Validation Coverage
| Area | Status | Key Evidence |
|------|--------|-------------|
| Phase 1: Whisper bridge files | ⚠️ Partial | Scripts missing; client + docs present |
| Phase 1: OPML importer | ✅ | importer.py:65-114 |
| Phase 1: Vault structure | ✅ | vault.py:42-84, all 4 vault files present |
| Phase 1: RSS poller | ✅ | rss_poller.py:69-298, full Atom+RSS2.0 |
| Phase 1: Skill scaffold | ✅ | SKILL.md, OPERATIONS.md, podcast-log.sh |
| Phase 1: Engine dry-run | ✅ | engine.py:486-530 |
| Phase 2: Overnight pipeline | ✅ | engine.py:263-384 |
| Phase 2: Email digest | ✅ | digest_emailer.py:159, engine.py:551 |
| Phase 2: 6AM notification | ✅ | jobs.json:6, cron `0 6 * * *` |
| Phase 2: Adaptive styles | ✅ | summarizer.py:108-199, 5 styles |
| Phase 2: Sourcing labels | ✅ | digest_emailer.py:20-26 |
| Phase 2: Whisper model routing | ✅ | whisper_client.py:48-55, transcript_fetcher.py:437 |
| Phase 2: Transcript pipeline | ✅ | transcript_fetcher.py:534 + 4 strategies |
| Phase 2: openclaw.json enabled | ✅ | openclaw.json:257 |
| Phase 3: Health store | ✅ | health_store.py:71-116 |
| Phase 3: Health tagging | ⚠️ Partial | always/sometimes/never ✅; per-episode override ❌ |
| Phase 3: Gmail IMAP | ✅ | gmail_fetcher.py:212-320 |
| Phase 3: Newsletter content | ✅ | gmail_fetcher.py:291-303 |
| Phase 3: Move to Trash | ✅ | gmail_fetcher.py:196-204 |
| Phase 3: Newsletter health store | ✅ | engine.py:441-454 |
| Phase 4: on_demand.py function | ✅ | on_demand.py:190-308 |
| Phase 4: SKILL.md exec commands | ❌ | Wrong flags in all 4 intents |
| Phase 4: add_feed.py function | ✅ | add_feed.py:65-209 |
| Phase 4: Extended depth wiring | ⚠️ Partial | summarizer supports it; on_demand.py always passes "standard" |
| Phase 4: Style correction | ❌ | No script, no SKILL.md intent |
