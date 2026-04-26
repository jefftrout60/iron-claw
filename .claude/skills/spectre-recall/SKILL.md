---
name: spectre-recall
description: Use when user wants to search for existing knowledge, recall a specific learning, or discover what knowledge is available.
---

# Recall Knowledge

Search and load relevant knowledge from the project's spectre learnings into your context.

## Registry

# SPECTRE Knowledge Registry
# Format: skill-name|category|triggers|description

gotchas-podcast-vault-race-condition|gotchas|vault import, episodes.json, missing episodes, episode disappeared, manual import|Use when manually importing episodes into episodes.json or wondering why recently added episodes disappeared from the vault
gotchas-cloud-whisper-413-segments|gotchas|413, segment, whisper, show_notes fallback, fetch_openai_whisper, transcript failed|Use when cloud Whisper transcription fails with 413 errors, segments return no text, or high-bitrate episodes fall back to show_notes unexpectedly
patterns-podcast-summarizer-customization|patterns|summary style, show instructions, qa_suffix, list_suffix, topic_map, _SHOW_EXTRA_INSTRUCTIONS, AMA, Q&A format|Use when adding per-show instructions, changing how episodes are summarized, adding universal prompt rules, or customizing summary format for a show or episode type
decisions-podcast-summary-style-architecture|decisions|summary_style, hunting_outdoor, long_form_interview, deep_science, feed style, extended depth|Use when changing a feed's summary style, wondering why hunting_outdoor is not assigned to any feed, or deciding what style to use for a new podcast
gotchas-summarizer-style-drift|gotchas|adding style, new show style, summarizer drift, classify wrong style, silent misclassification, meateater, orvis_fly_fishing|Use when adding a new summary style or debugging why a show is being summarized with the wrong style
testing-podcast-summary-test-suite|testing|test_summarizer, test_episode_utils, podcast tests, unittest, 105 tests, run tests|Use when running, adding to, or debugging the podcast summary test suite
gotchas-unbounded-file-growth|gotchas|unbounded growth, infinite growth, file size, log rotation, state eviction, prune, disk space, growing file, RotatingFileHandler, episodes.json prune|Use when reviewing new code, auditing existing code, or adding any persistent state — always check for files that grow without bound
decisions-health-knowledge-architecture|decisions|health_knowledge, health_db, health.db, health intelligence, Oura, blood labs, DEXA, biometric, trusted sources, Attia, Huberman, Patrick, SQLite migration, health store|Use when touching health_knowledge.json, health.db, planning health intelligence features, adding new data pillars, or querying the health SQLite database
gotchas-podcast-watcher-double-fire|gotchas|podcast-watcher, double email, duplicate summary, two emails, Intent 3b, style override, show_notes fallback, container on_demand, whisper not running, no ffmpeg|Use when working on podcast-watcher.py, debugging duplicate emails, or debugging why on-demand requests return show_notes instead of Whisper
decisions-whisper-m5max-offload|decisions|whisper, M5 Max, transcription, faster-whisper, WHISPER_BASE_URL, whisper_client, offload, local whisper|Use when working on whisper_client.py, transcript_fetcher.py, or planning Whisper transcription infrastructure
integration-oura-v2-api|integration|Oura, oura API, oura-sync, daily_sleep, heartrate, daily_spo2, personal access token, oura sync, sync_state, daily_hrv|Use when working on oura-sync.py, adding new Oura endpoints, debugging Oura sync failures, or planning Oura data integration
patterns-sqlite-fts5-python|patterns|SQLite, FTS5, sqlite3, health_db, INSERT OR IGNORE, dedup, rowcount, external content table, WAL mode, Python 3.9, union syntax|Use when adding SQLite to a Python project, implementing FTS5 full-text search, writing dedup/upsert logic, or debugging sqlite3 import errors in Python 3.9

## How to Use

1. **Scan registry above** — match triggers/description against your current task
2. **Load matching skills**: `Skill({skill-name})`
3. **Apply knowledge** — use it to guide your approach

## Search Commands

- `/recall {query}` — search registry for matches
- `/recall` — show all available knowledge by category

## Workflow

**Single match** → Load automatically via `Skill({skill-name})`

**Multiple matches** → List options, ask user which to load

**No matches** → Suggest `/learn` to capture new knowledge
