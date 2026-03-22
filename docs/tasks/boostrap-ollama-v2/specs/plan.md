# Implementation Plan — Podcast Summary Skill
Date: 2026-03-21 | Branch: boostrap-ollama-v2 | Depth: Comprehensive

---

## Overview

Build a `podcast-summary` skill for the IronClaw/OpenClaw agent that monitors an open-ended list of podcast RSS feeds (seeded from an OPML export, extensible at any time), acquires transcripts (scraped or Whisper-generated), produces adaptive LLM summaries, stores health-relevant content in a persistent knowledge store, and delivers a nightly email digest. Health newsletters (Attia, Rhonda Patrick) are ingested from Gmail, summarized, stored to the health store, and moved to Gmail Trash automatically. On-demand single-episode summaries are available via Telegram/iMessage.

**Architecture decision:** Overnight batch processing runs as a Mac host Python script (system crontab) to avoid agent turn timeouts. The 6AM notification is a lightweight OpenClaw cron turn. On-demand requests use the normal SKILL.md pipeline inside the agent.

---

## Current State

- No podcast monitoring capability exists in IronClaw
- `send-email` skill exists and works (Gmail SMTP via Python)
- `audio_bridge_service.py` pattern exists for host↔container HTTP services
- `productwatcher` vault pattern (JSON files + atomic writes) is established
- `whisper-server` binary does not yet exist on the Mac host
- `config/cron/jobs.json` is empty `{"version": 1, "jobs": []}`
- No health knowledge store exists

---

## Desired End State

1. **Every morning**, Jeff wakes up to an email digest containing summaries of every new podcast episode from the prior day, each labeled with its transcript source quality
2. **6AM Telegram/iMessage nudge**: "Processed N new podcasts overnight — [show names]. Enjoy your digest!"
3. **Health newsletters** (Attia weekly brief, FoundMyFitness) are automatically fetched from Gmail, summarized, stored to the health knowledge store, and moved to Gmail Trash — the 6AM nudge and digest footer both confirm what was archived
4. **On-demand**: "Give me a summary of this episode" or "find a recent Huberman episode on the vagus nerve" via Telegram/iMessage → result emailed
5. **Health knowledge store**: Persistent JSON file of health-relevant summaries ready for future LLM-based query interface

---

## Out of Scope (v1)

- Overcast / Spotify queue automation
- Health DB query interface ("what does Attia say about ApoB?") — store only
- Telegram inline summaries (email is the delivery channel)
- Multi-user support
- Non-English podcasts / video podcasts
- Additional health newsletters beyond Attia + Rhonda Patrick

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ MAC HOST                                                        │
│                                                                 │
│  11PM system crontab                                            │
│       │                                                         │
│       ▼                                                         │
│  engine.py ──────────── rss_poller.py ──► RSS feeds (internet) │
│       │                                                         │
│       │──────────────── transcript_fetcher.py                  │
│       │                    │── web scrape (tim.blog, etc.)      │
│       │                    │── browser (Attia member pages)     │
│       │                    └── whisper_client.py                │
│       │                              │                          │
│       │                    ┌─────────▼──────────┐              │
│       │                    │  whisper-server     │              │
│       │                    │  (whisper.cpp)      │              │
│       │                    │  port 18797         │              │
│       │                    │  Metal GPU          │              │
│       │                    └────────────────────┘              │
│       │                                                         │
│       │──────────────── gmail_fetcher.py ──► Gmail IMAP        │
│       │                                                         │
│       │──────────────── OpenAI API (summarization)             │
│       │                                                         │
│       │──────────────── digest_emailer.py ──► Gmail SMTP       │
│       │                                                         │
│       └── writes ──────► workspace/skills/podcast-summary/     │
│                              podcast_vault/                     │
│                                 feeds.json                      │
│                                 episodes.json                   │
│                                 health_knowledge.json           │
│                                 processing_status.json          │
└─────────────────────────────────────────────────────────────────┘
         ▲ volume mount
┌────────┴────────────────────────────────────────────────────────┐
│ DOCKER CONTAINER (OpenClaw agent)                               │
│                                                                 │
│  6AM OpenClaw cron ──► agent turn                               │
│       │── exec: read processing_status.json                     │
│       └── message: Telegram/iMessage nudge                      │
│                                                                 │
│  On-demand (Telegram/iMessage) ──► agent turn                   │
│       │── SKILL.md pipeline                                     │
│       │── exec: scripts/on_demand.py                            │
│       │── web_fetch: transcript sources                         │
│       │── browser: paywalled pages                              │
│       └── send_email: result via digest_emailer.py              │
└─────────────────────────────────────────────────────────────────┘
```

**Key design decisions:**
- `engine.py` runs on Mac host (not in container) — no turn timeout, direct filesystem access, direct Whisper access
- Summarization: `engine.py` calls OpenAI API directly using `OPENAI_API_KEY` from `.env`
- Email sending: `engine.py` calls `send_email.py` directly (same script the skill uses)
- Volume mount means `agents/sample-agent/workspace/` is the same path on host and in container
- On-demand requests use the normal OpenClaw agent pipeline (SKILL.md + exec scripts)

---

## Component Architecture

### `engine.py` — Nightly Coordinator (host-side)
The main orchestrator. Reads `.env` for credentials. Loads `feeds.json`, finds new episodes, coordinates acquisition + summarization, writes results, sends digest.

```
engine.py
├── load_env()                  # Read agents/sample-agent/.env
├── load_vault()                # Read feeds.json, episodes.json
├── check_new_episodes()        # For each active feed, poll RSS
├── acquire_content(episode)    # Transcript pipeline (see below)
├── summarize(episode, transcript) # OpenAI API call
├── store_health(episode, summary) # Write to health_knowledge.json if health-relevant
├── build_digest()              # Compose HTML email
├── send_digest()               # Gmail SMTP
├── save_vault()                # Atomic writes to all vault files
└── write_status()              # processing_status.json for 6AM notification
```

### `rss_poller.py` — RSS Feed Parser (host-side)
Parses RSS/Atom feeds. Returns list of new episodes not in `episodes.json`.

```python
# Key fields extracted per episode
{
    "id": "{feed_id}_{guid_hash}",
    "show_id": "peter-attia-drive",
    "title": "Episode 224: ...",
    "pub_date": "2026-03-20T08:00:00Z",  # ISO 8601 via email.utils.parsedate_to_datetime
    "audio_url": "https://cdn.example.com/ep224.mp3",
    "duration_seconds": 7245,
    "description": "...",          # Short description
    "full_notes": "...",           # content:encoded if present
    "transcript_tag_url": null,    # podcast:transcript URL if in feed
    "episode_number": "224"
}
```

### `transcript_fetcher.py` — Transcript Acquisition Pipeline (host-side)
Implements the per-show strategy. Returns `(text, source_quality)` tuple.

```
source_quality values:
  "published_transcript"  — full transcript from show's own site
  "third_party_transcript" — e.g. podscript.ai, HappyScribe
  "whisper_large"         — local whisper large-v3
  "whisper_small"         — local whisper small.en
  "show_notes"            — description/content:encoded only (lowest quality)
```

Strategy per show (cached in `feeds.json` as `transcript_strategy`):
```
tim-ferriss-show:    ["fetch_tim_blog", "whisper_small"]
huberman-lab:        ["fetch_podscript_ai", "whisper_large"]
peter-attia-drive:   ["fetch_happyscribe", "whisper_large"]
found-my-fitness:    ["whisper_large"]
default:             ["check_transcript_tag", "show_notes", "whisper_small"]
```

### `whisper_client.py` — Whisper Bridge Client (host-side)
Downloads audio (if not cached), POSTs to `localhost:18797/inference`, returns transcript text.

```python
def transcribe(audio_url: str, model_tier: str) -> str:
    # Download to /tmp/podcast-summary/ep_{id}.mp3
    # POST multipart to http://localhost:18797/inference
    # Form: file=@/tmp/..., response_format=json, language=en
    # Timeout: 1800s (30 min — handles 3hr episodes)
    # Returns: response["text"]
```

**Whisper model routing:**
```python
WHISPER_LARGE_SHOWS = [
    "peter-attia-drive",
    "huberman-lab",
    "found-my-fitness",
    "foundmyfitness-members-feed"
]
# All others → small.en
# Configurable via feeds.json "whisper_model" per show (overrides default)
```

### `gmail_fetcher.py` — Newsletter Ingestion (host-side)
Polls Gmail IMAP for label `health-newsletters`, returns list of emails with body/links.

```python
def fetch_health_newsletters() -> list[dict]:
    # imaplib.IMAP4_SSL("imap.gmail.com", 993)
    # login with GMAIL_IMAP_APP_PASSWORD from .env
    # select '"health-newsletters"'
    # search UNSEEN
    # fetch RFC822 via BODY.PEEK (no auto-read-mark)
    # parse multipart MIME: prefer text/html, fallback text/plain
    # extract href links via re
    # on successful store → move to Gmail Trash (not permanent delete)
    #   mail.copy(msg_id, '"[Gmail]/Trash"')
    #   mail.store(msg_id, '+FLAGS', '\\Deleted')
    #   mail.expunge()
    # return [{subject, body_html, body_text, links, date}]
```

For Attia teaser emails: detect `peterattia.com` links in body → fetch via `requests` with stored session cookie (or use Playwright/browser for JS-heavy pages).

### `health_store.py` — Health Knowledge Store (host-side)
CRUD operations on `health_knowledge.json`.

```python
# Schema per entry
{
    "id": "attia-ep224-2026-01-15",
    "show": "The Peter Attia Drive",
    "episode_title": "...",
    "episode_number": "224",
    "date": "2026-01-15",
    "source": "podcast",        # "podcast" | "newsletter"
    "source_quality": "whisper_large",
    "topics": ["ApoB", "cardiovascular"],   # extracted from summary
    "summary": "Full summary text...",
    "tagged_by": "auto"         # "auto" | "user"
}
```

Health tagging logic (stored in `feeds.json` per show as `health_tier`):
```
"always":    peter-attia-drive, huberman-lab, found-my-fitness,
             valley-to-peak-nutrition, barbell-shrugged, better-brain-fitness
"sometimes": tim-ferriss-show, shawn-ryan-show (detect health topics in summary)
"never":     all hunting/outdoor shows, triggernometry, philosophize-this, etc.
```

User can override per-episode via Telegram: "add that Hunt Backcountry episode to health store"

### `digest_emailer.py` — Email Digest Composer (host-side)
Builds HTML email, sends via Gmail SMTP (reusing send_email.py pattern).

**Digest format:**
```html
Subject: 🎧 Podcast Digest — N new episodes · Mon Mar 21

[Show Name] — Episode Title
Source: Published Transcript | Whisper (large-v3) | Show Notes
March 20, 2026

[Adaptive summary — 1 to 4 paragraphs]

[Listen →](episode_url)
────────────────────────────────────────

[next episode...]

────────────────────────────────────────
Health Archive: 2 newsletters stored (Attia weekly brief, FoundMyFitness digest)
```

### `on_demand.py` — On-Demand Episode Summary (container-side)
Invoked by the OpenClaw agent via exec. Accepts episode reference and returns a summary to be emailed.

Handles:
- Specific episode: `--show "huberman-lab" --episode "272"` or `--url "https://..."`
- Vague/semantic: `--show "huberman-lab" --topic "vagus nerve"` → scans recent episodes, finds best match
- Re-summarize: `--episode-id "huberman-272" --depth extended`

---

## Data Architecture

### `feeds.json` — Feed Registry
```json
{
  "version": 1,
  "last_updated": "2026-03-21T11:00:00Z",
  "feeds": [
    {
      "id": "peter-attia-drive",
      "title": "The Peter Attia Drive",
      "rss_url": "https://peterattia.supercast.tech/feeds/3twcVFdxDNqMQcLLsX3RuazZ",
      "state": "active",           // "active" | "one-off" | "inactive"
      "summary_style": "deep_science",  // auto-classified
      "health_tier": "always",
      "whisper_model": "large-v3",  // per-show override; null = use default routing
      "transcript_strategy": ["fetch_happyscribe", "whisper_large"],
      "transcript_strategy_last_tested": "2026-03-21",
      "last_checked": "2026-03-21T11:00:00Z",
      "last_episode_guid": "ep224-guid-hash"
    }
  ]
}
```

Initial feeds.json pre-seeded from the OPML export (35 feeds, all set to `state: "active"`). The 7-8 backlog-only shows are identified by user and set to `state: "one-off"` after their backlog episodes are summarized.

### `episodes.json` — Episode Cache
```json
{
  "version": 1,
  "episodes": [
    {
      "id": "peter-attia-drive_abc123",
      "show_id": "peter-attia-drive",
      "title": "Episode 224: Cardiovascular disease...",
      "pub_date": "2026-03-20T08:00:00Z",
      "audio_url": "https://...",
      "duration_seconds": 7245,
      "source_quality": "whisper_large",
      "summary": "Full summary text...",
      "summary_depth": "standard",    // "standard" | "extended"
      "health_tagged": true,
      "health_store_id": "attia-ep224-2026-03-20",
      "digest_sent": true,
      "digest_date": "2026-03-21",
      "processed_at": "2026-03-21T02:14:00Z"
    }
  ]
}
```

Retention: keep last 90 days of processed episodes (prevents re-processing on restart).

### `health_knowledge.json` — Health Store
See schema above. Append-only for new entries; atomic file write on each append. Estimated ~500 entries/year at <10 entries/week. JSON context window friendly for future LLM queries.

### `processing_status.json` — 6AM Notification Data
```json
{
  "run_date": "2026-03-21",
  "status": "complete",           // "complete" | "in_progress" | "failed"
  "completed_at": "2026-03-21T03:47:00Z",
  "episodes_processed": 3,
  "shows": ["The Peter Attia Drive", "All-In", "Orvis Fly-Fishing"],
  "newsletters_archived": 1,
  "errors": []
}
```

---

## Transcript Acquisition Strategy (Per Show)

| Show | Primary Strategy | Fallback | Notes |
|------|-----------------|----------|-------|
| Tim Ferriss | Fetch `tim.blog/*-transcript/` | whisper_small | Free, public, reliable |
| Huberman Lab | Fetch `podscript.ai/podcasts/huberman-lab-podcast/[slug]` | whisper_large | Free third-party |
| Peter Attia | Fetch HappyScribe (403 risk) | whisper_large | Private RSS gives audio; test HappyScribe first |
| Rhonda Patrick | whisper_large (direct) | — | Private RSS gives audio; no public transcripts |
| All-In | Check `podcast:transcript` tag | show_notes | Good show notes typically |
| Hunting/outdoor shows | Check `podcast:transcript` tag | show_notes | Most on Libsyn/Megaphone; check tag |
| Triggernometry, commentary | show_notes | — | Show notes sufficient for 1-para summaries |
| Devotional (Ligonier, GTY) | show_notes | — | Short episodes; show notes sufficient |

Transcript strategy failures are recorded in `feeds.json` per show and retried after 7 days.

---

## Whisper Bridge Setup (Host Service)

### Build
```bash
# On Mac host
git clone https://github.com/ggerganov/whisper.cpp
cd whisper.cpp
cmake -G Ninja -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --target whisper-server
# Download models
bash models/download-ggml-model.sh small.en   # default
bash models/download-ggml-model.sh large-v3   # health shows
```

### Run (whisper-server — always-on LaunchAgent)
The server stays running 24/7 so on-demand requests from the container also work.

```bash
# Startup command (in LaunchAgent plist)
/path/to/whisper.cpp/build/bin/whisper-server \
  -m /path/to/whisper.cpp/models/ggml-small.en.bin \
  --host 0.0.0.0 \
  --port 18797 \
  --convert \
  -t 8
```

**Model switching**: The `whisper_client.py` switches models by calling `POST /load` with the new model path before transcription, then restoring default after. This avoids running two server instances.

```python
# Switch to large-v3 for health shows
requests.post("http://localhost:18797/load",
              json={"model": "/path/to/ggml-large-v3.bin"})
# ... transcribe ...
# Restore small.en
requests.post("http://localhost:18797/load",
              json={"model": "/path/to/ggml-small.en.bin"})
```

### LaunchAgent (auto-start at login)
```xml
<!-- ~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist -->
<key>Label</key><string>com.ironclaw.whisper-bridge</string>
<key>ProgramArguments</key><array>
  <string>/path/to/whisper-server</string>
  <string>-m</string><string>/path/to/ggml-small.en.bin</string>
  <string>--host</string><string>0.0.0.0</string>
  <string>--port</string><string>18797</string>
  <string>--convert</string>
  <string>-t</string><string>8</string>
</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
```

---

## Implementation Phases

### Phase 1 — Core Infrastructure (no emails yet)

**Goal:** RSS polling, vault, OPML import, skill scaffold, Whisper bridge. No digest email until Phase 2.

**Deliverables:**
- `workspace/skills/podcast-summary/SKILL.md` — full skill definition with on-demand pipeline
- `workspace/skills/podcast-summary/scripts/importer.py` — OPML import → feeds.json
- `workspace/skills/podcast-summary/scripts/rss_poller.py` — RSS/Atom parsing
- `workspace/skills/podcast-summary/scripts/engine.py` — coordinator (polls + logs, no email yet)
- `workspace/skills/podcast-summary/podcast_vault/feeds.json` — pre-seeded from OPML
- `workspace/skills/podcast-summary/podcast-knowledge.md` — show metadata reference
- `scripts/whisper-bridge-service.py` — whisper-server LaunchAgent setup helper
- `scripts/launchagents/com.ironclaw.whisper-bridge.plist.tmpl` — LaunchAgent template
- Mac system crontab entry: `0 23 * * * python3 .../engine.py`
- New `.env` keys documented (not yet required in Phase 1)
- `OPERATIONS.md` — setup runbook for whisper.cpp build + LaunchAgent

**Acceptance:** `python3 engine.py --dry-run` prints new episodes found without sending anything

---

### Phase 2 — Transcript Pipeline + Email Digest (emails start)

**Goal:** Whisper bridge live, transcript fetching, summarization, digest email working end-to-end.

**Deliverables:**
- `scripts/whisper_client.py` — Whisper HTTP client with model switching
- `scripts/transcript_fetcher.py` — Per-show strategy (tim.blog, podscript.ai, HappyScribe, Whisper)
- `scripts/digest_emailer.py` — HTML email composer + SMTP send
- `engine.py` updated: full pipeline (poll → transcribe → summarize → email)
- `config/openclaw.json` updated: enable `podcast-summary` in `skills.entries`
- `config/cron/jobs.json` updated: 6AM notification cron entry
- New `.env` keys added: `WHISPER_BRIDGE_URL`, `PODCAST_DIGEST_TO_EMAIL`, `PODCAST_NOTIFICATION_CHAT_ID`
- OpenAI API call for summarization (using existing `OPENAI_API_KEY`)

**Acceptance:** Full overnight run → email digest received with correct per-show summary styles and source quality labels

---

### Phase 3 — Health Store + Newsletter Ingestion

**Goal:** Health knowledge persistence and Gmail newsletter pipeline.

**Deliverables:**
- `scripts/health_store.py` — Health knowledge CRUD (JSON append + atomic write)
- `scripts/gmail_fetcher.py` — IMAP polling for `health-newsletters` label
- `engine.py` updated: health tagging + newsletter ingestion in pipeline
- `podcast_vault/health_knowledge.json` — initialized empty
- New `.env` keys: `GMAIL_IMAP_EMAIL`, `GMAIL_IMAP_APP_PASSWORD`, `ATTIA_SITE_EMAIL`, `ATTIA_SITE_PASSWORD`, `FOUNDMYFITNESS_SITE_EMAIL`, `FOUNDMYFITNESS_SITE_PASSWORD`
- Digest updated: newsletter archive one-liner in footer

**Acceptance:** Health newsletter arrives in `health-newsletters` Gmail label → processed overnight → entry appears in `health_knowledge.json`

---

### Phase 4 — On-Demand + Semantic Search

**Goal:** Telegram/iMessage triggered summaries, vague topic search, re-summarize capability.

**Deliverables:**
- `scripts/on_demand.py` — On-demand handler: specific episode, URL, vague topic search, re-summarize
- `SKILL.md` updated: on-demand pipeline steps fully defined
- `AGENTS.md` (sample-agent workspace) updated: add podcast-related intent patterns
- 6AM notification cron: reads `processing_status.json`, formats and sends message

**Acceptance:**
- "Catch me up on the last 3 Peter Attia episodes" → 3 summaries emailed
- "Find a recent Huberman episode on vagus nerve" → best match identified + summarized + emailed
- "Give me a longer version of that Triggernometry episode" → extended summary emailed

---

## Summary Style Reference

| Show Type | Style | Depth |
|-----------|-------|-------|
| `deep_science` | Paragraphs: key claims, protocols, studies, actionable takeaways | 3–4 paragraphs |
| `long_form_interview` | Content-adaptive: assess episode depth, scale accordingly | 1–3 paragraphs |
| `commentary` | Key arguments and positions | 1–2 paragraphs |
| `hunting_outdoor` | Topic breakdown: Elk Strategies · Archery Tips · Gear · Travel · Guest | 1 blurb per topic |
| `devotional` | Core scripture/theme, main point | 2–3 sentences |

Show classification stored in `feeds.json` as `summary_style`. Auto-classified on first episode; correctable via Telegram feedback which updates `feeds.json`.

---

## Testing Strategy

**No formal unit tests** — skill scripts are pipeline-oriented. Validate via dry-run modes and integration checkpoints:

1. **Phase 1**: `python3 engine.py --dry-run` — verify RSS polling finds correct new episodes, no writes
2. **Phase 1**: `python3 importer.py feeds.opml --dry-run` — verify 35 feeds parsed correctly
3. **Phase 2**: `python3 engine.py --episode <id> --no-email` — process single episode, print summary
4. **Phase 2**: `python3 engine.py --episode <id> --email-test` — send to test address only
5. **Phase 3**: `python3 gmail_fetcher.py --dry-run` — verify IMAP fetch + parsing without marking read
6. **Phase 4**: `python3 on_demand.py --show "huberman-lab" --topic "vagus nerve" --dry-run`

Whisper bridge: `curl http://localhost:18797/inference -F file=@test.mp3 -F response_format=json`

---

## Critical Files for Implementation

| File | Role |
|------|------|
| `agents/sample-agent/workspace/audio/audio_bridge_service.py` | **Pattern to follow** — host HTTP bridge service (port, 0.0.0.0 binding, JSON responses) |
| `agents/sample-agent/workspace/skills/productwatcher/scripts/watcher_engine.py` | **Pattern to follow** — vault loading, atomic JSON writes, provider fallback chains, cron logging |
| `agents/sample-agent/workspace/skills/send-email/scripts/send_email.py` | **Reuse directly** — SMTP email sender; call from engine.py |
| `agents/sample-agent/workspace/skills/restaurant-scout/scripts/scout-log.sh` | **Clone for** `podcast-log.sh` — structured JSON logging pattern |
| `agents/sample-agent/config/openclaw.json` | **Modify** — add `podcast-summary` to `skills.entries`, add 6AM notification cron |
| `agents/sample-agent/config/cron/jobs.json` | **Modify** — add 6AM notification cron entry |
| `agents/sample-agent/.env` | **Modify** — add 8 new credential keys (document only; user fills values) |
| `scripts/systemd/audio-bridge.user.service.example` | **Pattern to follow** — for whisper-bridge LaunchAgent/systemd service template |
