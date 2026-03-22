# Test Guide: Podcast Summary Skill
*Branch: boostrap-ollama-v2 | Complexity: Complex | Generated: 2026-03-22*

## Testing Overview
**Scope**: Full end-to-end validation of the podcast-summary skill — Whisper bridge, nightly batch engine, transcript pipeline, email digest, Gmail newsletter ingestion, health knowledge store, on-demand summaries, and feed management.

**Environment**: Mac host + Docker container (sample-agent)

**Prerequisites**:
- `OPENAI_API_KEY` set in `agents/sample-agent/.env`
- `SMTP_FROM_EMAIL` and `GMAIL_APP_PASSWORD` set (for email tests)
- `PODCAST_DIGEST_TO_EMAIL` set (recipient email)
- `GMAIL_IMAP_EMAIL` and `GMAIL_IMAP_APP_PASSWORD` set (for newsletter tests)
- Agent running: `./scripts/compose-up.sh sample-agent -d`

**Estimated Time**: 60–90 min (full run); 20 min (dry-run + smoke tests only)

---

## 1. Initial Setup & Whisper Bridge

### 1a. Setup Script
- [ ] Run `bash agents/sample-agent/workspace/skills/podcast-summary/scripts/setup-whisper-bridge.sh --help` — verify `--model-dir`, `--port`, `--whisper-dir` options are printed
- [ ] Run setup with defaults: `bash .../setup-whisper-bridge.sh` — verify it checks prerequisites (cmake, git, curl, ffmpeg) and exits clearly if any are missing
- [ ] On a machine with all prerequisites: run setup and verify it completes with ✓ summary listing what was installed or skipped
- [ ] Verify LaunchAgent is installed: `ls ~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist`
- [ ] Verify whisper-server is running: `curl -s http://localhost:18797/` — should return HTML test UI (not connection refused)
- [ ] Re-run setup script a second time — verify it skips steps already done (idempotent, no errors)

### 1b. Whisper Bridge Health from Container
- [ ] From inside the agent container, test bridge reachability: `docker exec sample-agent_secure curl -s http://host.docker.internal:18797/` — should return same HTML UI
- [ ] Verify LaunchAgent auto-restarts whisper-server: `launchctl stop com.ironclaw.whisper-bridge && sleep 5 && curl -s http://localhost:18797/` — should be reachable again

---

## 2. Vault Structure & OPML Import

### 2a. Vault Files
- [ ] Verify all 4 vault files exist with correct structure:
  - `podcast_vault/feeds.json` — has `{"version": 1, "feeds": [...]}`
  - `podcast_vault/episodes.json` — has `{"version": 1, "episodes": []}`
  - `podcast_vault/health_knowledge.json` — has `{"version": 1, "entries": []}`
  - `podcast_vault/processing_status.json` — has `{"version": 1, "status": "never_run", ...}`
- [ ] Verify `podcast_vault/*.json` is gitignored: `git check-ignore -v agents/sample-agent/workspace/skills/podcast-summary/podcast_vault/feeds.json` — should show it's ignored

### 2b. OPML Importer
- [ ] Run with `--dry-run` on a real OPML file: `python3 importer.py ~/path/to.opml --dry-run` — verify it prints feeds that would be added/skipped without modifying `feeds.json`
- [ ] Run without `--dry-run`: verify feeds are added to `feeds.json`
- [ ] Re-run on same OPML: verify existing entries are not overwritten (state, health_tier, summary_style preserved)
- [ ] Verify 35 pre-seeded feeds are present including: `peter-attia-drive`, `huberman-lab`, `found-my-fitness`
- [ ] Verify health shows have correct metadata: `python3 -c "import json; d=json.load(open('podcast_vault/feeds.json')); [print(f[\"title\"], f[\"health_tier\"], f[\"whisper_model\"]) for f in d[\"feeds\"] if f.get(\"health_tier\")==\"always\"]"`

---

## 3. RSS Poller & Engine Dry-Run

### 3a. RSS Poller
- [ ] Run direct test: `python3 scripts/rss_poller.py --test-url "https://feeds.megaphone.fm/huberman-lab"` — verify it returns parsed episodes with title, pub_date, audio_url, guid
- [ ] Verify it handles Atom feeds: test with a known Atom feed URL and check episodes are returned
- [ ] Verify `transcript_tag_url` is extracted when present (check a podcast that publishes transcripts)

### 3b. Engine Dry-Run
- [ ] Run dry-run: `python3 scripts/engine.py --dry-run --agent sample-agent`
  - Should print `=== Podcast Summary Engine — DRY RUN ===`
  - Should list active feed count (35) and new episodes found (many on first run)
  - Should print `[DRY RUN] No changes written.`
  - Should NOT send any email
  - Should NOT modify feeds.json, episodes.json, or processing_status.json
- [ ] Verify feeds.json `last_checked` timestamps are NOT updated in dry-run mode
- [ ] Run dry-run a second time — verify episode count is still high (last_checked not persisted in dry-run)

---

## 4. Transcript Fetcher Strategies

### 4a. Strategy dispatch (unit test)
- [ ] Test `check_transcript_tag`: find a feed with `transcript_tag_url` in its RSS (e.g. a Buzzsprout-hosted show) — run engine in dry-run and verify transcript_tag_url is populated
- [ ] Test `fetch_tim_blog`: for a recent Tim Ferriss episode, run: `python3 -c "import transcript_fetcher; t,q = transcript_fetcher._dispatch_tim_blog({'title':'...', 'audio_url':'...'}); print(q, len(t))"`
- [ ] Test `fetch_podscript_ai`: for a Huberman episode, run same pattern — verify either text is returned or `failed_403` is written to transcript_strategy_cache
- [ ] Test fallback to `show_notes`: pass an episode with no strategies and verify full_notes/description is returned with source_quality `"show_notes"`

### 4b. Whisper Transcription (requires Whisper bridge running)
- [ ] Test `whisper_client.is_available()`: `python3 -c "import sys; sys.path.insert(0,'scripts'); import whisper_client; print(whisper_client.is_available())"`— should print `True`
- [ ] Test transcription with a short audio URL (< 5 min): verify it downloads, POSTs to `/inference`, returns text, and deletes temp file
- [ ] Verify model switching: after transcribing with `"large"`, verify server returns to `small.en` (check `/tmp/whisper-bridge.log` for load events)

---

## 5. Nightly Batch Run (Full Pipeline)

### 5a. Single-episode test run
- [ ] Add a one-off test feed to feeds.json with a recent episode: `python3 scripts/add_feed.py --title "Test Feed" --url "https://..." --state one-off`
- [ ] Run engine with `--no-email` first: `python3 scripts/engine.py --agent sample-agent --no-email`
  - Verify the test episode is processed (appears in episodes.json)
  - Verify summary is non-empty and correctly formatted
  - Verify source_quality label is set correctly
  - No email is sent
- [ ] Check processing_status.json: `python3 -c "import json; print(json.dumps(json.load(open('podcast_vault/processing_status.json')), indent=2))"` — verify `status: "complete"`, correct episode count

### 5b. Email digest delivery
- [ ] Re-run engine without `--no-email` (ensure at least one episode is unprocessed): verify email arrives in inbox
- [ ] Verify email subject: `🎧 Podcast Digest — N new episodes · {Day Mon DD}`
- [ ] Verify each episode card contains: show name, episode title, source quality label, summary text, "Listen →" link
- [ ] Verify digest sent exactly ONCE (not twice) even if newsletters were also processed

### 5c. Summary style classification
- [ ] Add a new feed with `summary_style: null` to feeds.json
- [ ] Run engine — verify the LLM classifies the show's style and it's written back to feeds.json
- [ ] Verify subsequent runs use the cached style without re-classifying

---

## 6. Health Store & Newsletter Ingestion

### 6a. Health store
- [ ] After processing a health show episode (Peter Attia, Huberman, etc.), verify entry in health_knowledge.json:
  - `python3 -c "import sys; sys.path.insert(0,'scripts'); import health_store; e=health_store.load_all(); print(len(e), e[0]['topics'] if e else 'empty')"`
  - Entry should have: id, show, episode_title, date, source, source_quality, topics (list), summary, tagged_by="auto"
- [ ] Verify `sometimes` tier shows (Tim Ferriss, Shawn Ryan) are tagged only when relevant health content exists
- [ ] Test `find_by_show()`: `python3 -c "import sys; sys.path.insert(0,'scripts'); import health_store; print(len(health_store.find_by_show('huberman')))"`

### 6b. Gmail newsletter ingestion
- [ ] Verify Gmail connection: `python3 scripts/gmail_fetcher.py --check` — should print count of UNSEEN newsletters in `health-newsletters` label (not error)
- [ ] With at least one test newsletter in the `health-newsletters` Gmail label, run engine and verify:
  - Newsletter body is fetched and summarized
  - Entry appears in health_knowledge.json with `source: "newsletter"`
  - Email is moved to Gmail Trash after processing (check Gmail Trash)
  - Newsletter count appears in digest email footer: "Health Archive: N newsletters stored — [names]"
- [ ] Verify teaser-link expansion: for a newsletter with only a short teaser and a link, verify content_type is `"full"` when the linked page was successfully fetched

---

## 7. On-Demand Episode Summaries (Telegram)

### 7a. Specific episode by number
- [ ] In Telegram, send: `summarize Huberman episode #432` (or a real episode number)
  - Agent should run `on_demand.py --query "#432" --agent sample-agent`
  - Verify email arrives with the episode summary
  - Verify agent replies: "Summary sent to your email"
  - Verify agent does NOT echo the summary inline in chat

### 7b. Specific episode by URL
- [ ] In Telegram, send: `summarize this episode: https://[episode-url]`
  - Agent should parse URL and call on_demand.py with `--query https://...`
  - Verify email arrives with correct episode

### 7c. Semantic/topic search
- [ ] In Telegram, send: `find the Huberman episode about sleep and REM cycles`
  - Agent should call on_demand.py with the show name + keywords as `--query`
  - Verify a matching episode is found and emailed (may not be exact — keyword matching is lexical)

### 7d. Extended depth re-summarization
- [ ] In Telegram, send: `give me a longer summary of that Attia episode on metabolic health`
  - Agent should call `on_demand.py --query "..." --depth extended`
  - Verify emailed summary is significantly longer (4-6 paragraphs vs 3-4)
  - Verify `summary_extended` field is written to episodes.json alongside original `summary`

### 7e. Cache behavior
- [ ] Request the same episode twice — second request should be served from cache (no re-transcription, faster response)
- [ ] Request same episode with `--depth extended` after a standard summary — should re-summarize at extended depth

---

## 8. Feed Management (Telegram)

### 8a. Add a new feed
- [ ] In Telegram, send: `add the "Hidden Brain" podcast` (or provide RSS URL directly)
  - Agent should use web_fetch to find RSS URL if not provided
  - Agent should call `add_feed.py --title "Hidden Brain" --url "https://..."`
  - Verify new feed appears in feeds.json with `state: "active"`
  - Verify agent confirms: "Added Hidden Brain to your podcast feeds"

### 8b. Remove / pause a feed
- [ ] In Telegram, send: `stop monitoring Barbell Shrugged`
  - Agent should call `add_feed.py --list` to find ID, then `add_feed.py --id "barbell-shrugged" --state inactive`
  - Verify feeds.json shows `state: "inactive"` for that feed
  - Verify next engine run skips the feed

### 8c. One-off episode
- [ ] In Telegram, send: `just get one episode from Lex Fridman`
  - Agent should call `add_feed.py --id "lex-fridman-podcast" --state one-off`
  - Verify state is `one-off` in feeds.json

### 8d. List feeds
- [ ] In Telegram, send: `show me my podcast feeds`
  - Agent should call `add_feed.py --list` and relay formatted output

### 8e. Correct summary style
- [ ] In Telegram, send: `use the science format for Rhonda Patrick`
  - Agent should call `add_feed.py --list`, find ID, then `add_feed.py --id "found-my-fitness" --style "deep_science"`
  - Verify feeds.json updated with new `summary_style`
  - Verify next engine run uses the new style for that show

---

## 9. 6AM Morning Notification

- [ ] Verify cron entry in jobs.json: `cat agents/sample-agent/config/cron/jobs.json` — should show `"podcast-morning-nudge"` with `"cron": "0 6 * * *"`
- [ ] Simulate the morning notification: run engine normally the night before, then at 6AM verify Telegram message is received
  - If status is "complete": `"🎧 Podcast Digest ready — N episodes processed from [shows list]. Check your email for summaries."`
  - If no run last night: `"🔇 No new podcasts were processed last night."`
- [ ] Test edge case: manually set `processing_status.json` `status: "in_progress"` and verify notification says `"⏳ Podcast processing still running..."`
- [ ] Test with newsletters: verify message includes newsletter count when present

---

## 10. OpenClaw Integration (Skill is Enabled)

- [ ] Verify skill is enabled: `grep -A2 "podcast-summary" agents/sample-agent/config/openclaw.json` — should show `"enabled": true`
- [ ] Restart agent: `./scripts/compose-up.sh sample-agent -d`
- [ ] Send any Telegram message about podcasts — verify agent engages the podcast-summary skill pipeline (not generic response)
- [ ] Verify agent never mentions internal tool names or script paths in Telegram replies (AGENTS.md rule)

---

## Results Documentation

| Test Area | Status | Notes |
|-----------|--------|-------|
| 1. Whisper bridge setup | | |
| 2. Vault & OPML import | | |
| 3. RSS poller & dry-run | | |
| 4. Transcript strategies | | |
| 5. Nightly batch + email | | |
| 6. Health store & Gmail | | |
| 7. On-demand summaries | | |
| 8. Feed management | | |
| 9. 6AM notification | | |
| 10. OpenClaw integration | | |

**Overall**: ⬜ Pass / ⬜ Fail / ⬜ Partial — Notes: _______________

---

## Known Issues & Limitations

1. **iMessage path**: 6AM notification is Telegram-only. The agent will send to whichever channel is configured.
2. **HappyScribe (Attia)**: May return 403 — transcript_fetcher will fall back to Whisper automatically. Check `transcript_strategy_cache` in feeds.json to confirm failure is recorded.
3. **First run episode count**: engine dry-run will show thousands of "new" episodes on first run (no last_checked baseline yet). First real run may be long — limit with `--one-off` state on most feeds during initial testing.
4. **Whisper model download**: `ggml-large-v3.bin` is ~3GB — download may take 10-20 min on first setup.
5. **Strategy cache durability**: transcript strategy failures are cached in memory during a run. If engine crashes mid-run, the cache is not persisted. Failures will be re-tested on next run.
