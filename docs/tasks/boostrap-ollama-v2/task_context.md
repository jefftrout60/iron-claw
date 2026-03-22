# Task Context — Podcast Summary Skill
Generated: 2026-03-21
Branch: boostrap-ollama-v2
Scope: docs/tasks/boostrap-ollama-v2/concepts/scope.md

---

## Architecture Patterns

### Vault / State Management (productwatcher pattern)
- **Reference**: `workspace/skills/productwatcher/scripts/watcher_engine.py`
- Three persistent JSON files in a `podcast_vault/` subdir:
  - `feeds.json` — RSS subscriptions, show metadata, state (active/one-off/inactive), transcript strategy cache per show
  - `episodes.json` — Episode cache: title, pub_date, audio_url, summary, source_quality, health_tagged, processed
  - `health_knowledge.json` — Health-relevant summaries: show, episode, date, topics, full_summary_text
- Atomic writes: write to `.tmp` then `os.replace()` — never corrupt on crash
- All JSON manipulation via `python3 -c "import json..."` — NO jq

### Host HTTP Bridge (audio_bridge_service.py pattern)
- **Reference**: `workspace/audio/audio_bridge_service.py` (port 18796, binds 0.0.0.0)
- **Systemd templates**: `scripts/systemd/audio-bridge.user.service.example`
- Docker reaches host via `host.docker.internal:{PORT}` — already wired in `docker-compose.yml.tmpl`
- Whisper bridge follows same pattern: Python HTTP server on Mac host, port 18797 (configurable)
- whisper.cpp ships its own `whisper-server` binary — use that directly, not a wrapper

### Email Delivery
- **Reference**: `workspace/skills/send-email/scripts/send_email.py`
- Python SMTP via Gmail, already working — reuse directly
- Supports HTML emails — use for rich digest layout

### Skill Pipeline Structure (restaurant-scout pattern)
- SKILL.md defines numbered steps with mandatory exec calls
- `scripts/` subdir for all Python/bash scripts
- Knowledge file (`podcast-knowledge.md`) for show metadata + transcript strategy cache
- Structured logging via a `podcast-log.sh` (clone of `scout-log.sh`)

### Cron Scheduling (productwatcher pattern)
- **Reference**: `workspace/skills/productwatcher/scripts/setup-cron.sh`
- Uses `config/cron/jobs.json` — currently empty `{"version": 1, "jobs": []}`
- Alternatively: system crontab entry pointing to Python script in workspace
- Pattern: `0 23 * * * python3 /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/engine.py`

---

## Dependencies

### Existing IronClaw Infrastructure
| Dependency | Path | Used For |
|-----------|------|----------|
| send_email.py | `workspace/skills/send-email/scripts/send_email.py` | Email digest delivery |
| audio_bridge_service.py | `workspace/audio/audio_bridge_service.py` | Pattern for Whisper host bridge |
| systemd templates | `scripts/systemd/*.user.service.example` | Auto-start Whisper bridge on Mac |
| docker-compose.yml.tmpl | `scripts/docker-compose.yml.tmpl` | host.docker.internal already wired |
| .env | `agents/sample-agent/.env` | SMTP_FROM_EMAIL, GMAIL_APP_PASSWORD |
| openclaw.json | `agents/sample-agent/config/openclaw.json` | skill enable toggle, cron jobs |
| scout-log.sh | `workspace/skills/restaurant-scout/scripts/scout-log.sh` | Clone for podcast-log.sh |
| watcher_engine.py | `workspace/skills/productwatcher/scripts/watcher_engine.py` | Vault pattern reference |

### New External Dependencies
| Dependency | Location | Notes |
|-----------|----------|-------|
| whisper.cpp whisper-server | Mac host | Build from source; Metal auto-detected on macOS |
| whisper model files | Mac host | `models/ggml-base.en.bin` (~150MB) or `large-v3` (~1.4GB) |
| Gmail IMAP | External | imaplib stdlib; App Password already in .env |
| subscriber site credentials | .env | Attia + FoundMyFitness site login |
| ffmpeg | Mac host | For whisper-server `--convert` flag (MP3→WAV auto-conversion) |

### New .env Keys Required
```
WHISPER_BRIDGE_URL=http://host.docker.internal:18797
GMAIL_IMAP_EMAIL=<email>
GMAIL_IMAP_APP_PASSWORD=<app-password>
ATTIA_SITE_EMAIL=<email>
ATTIA_SITE_PASSWORD=<password>
FOUNDMYFITNESS_SITE_EMAIL=<email>
FOUNDMYFITNESS_SITE_PASSWORD=<password>
PODCAST_DIGEST_TO_EMAIL=<destination email>
PODCAST_NOTIFICATION_CHAT_ID=<telegram chat id>
```

---

## Implementation Approaches

### RSS Parsing
- **Python stdlib only**: `xml.etree.ElementTree` + `urllib.request` — no feedparser needed
- Namespace map required: `itunes`, `podcast`, `content`, `media`
- `podcast:transcript` tag check on every episode — cache result per show
- `email.utils.parsedate_to_datetime()` for RFC 2822 pubDate parsing
- Guard for missing enclosures (some feeds mix blog posts + podcast episodes)
- User-Agent header needed for some CDNs that return 403

### Transcript Acquisition Pipeline (per show)
| Show | Strategy | Notes |
|------|----------|-------|
| Tim Ferriss | web_fetch `tim.blog/*-transcript/` | Free, public, reliable |
| Huberman Lab | web_fetch `podscript.ai/podcasts/huberman-lab-podcast/[slug]` | Free third-party |
| Peter Attia | Try HappyScribe → fall back to Whisper | HappyScribe may have bot protection (403); Whisper is reliable fallback; private RSS gives audio URL |
| Rhonda Patrick | Whisper (private RSS gives audio) | No public transcripts found |
| Hunting/outdoor shows | Check `podcast:transcript` tag → show notes → Whisper if needed | Most will be show notes only |
| All-In, Triggernometry, etc. | Check `podcast:transcript` tag → show notes | Commentary shows; show notes often sufficient |
| Newsletters (Attia, Patrick) | Gmail IMAP → web_fetch member link if teaser | browser tool for JS-heavy pages |

Per-show transcript strategy is cached in `feeds.json` after first discovery — avoids retrying failed strategies every night.

### Whisper Bridge Architecture
- **Host service**: `whisper-server` binary (from whisper.cpp) on Mac host
  - Port: 18797 (configurable via env)
  - Bind: `0.0.0.0` so container can reach via host.docker.internal
  - Flag: `--convert` (requires ffmpeg on Mac host for MP3 → WAV auto-conversion)
  - Model: `base.en` for most shows; `large-v3` configurable per-show for high-priority health shows
  - Requests are serialized (single mutex) — queue episodes sequentially
- **Container side**: `curl` POST to `http://host.docker.internal:18797/inference`
  - Form fields: `file=@/tmp/episode.mp3`, `response_format=json`, `language=en`
  - Timeout: 30min per episode (handles 3hr episodes at ~10min/hr transcription)
- **Auto-start**: macOS LaunchAgent or systemd user service (template exists in repo)

### Gmail IMAP Newsletter Ingestion
- `imaplib.IMAP4_SSL("imap.gmail.com", 993)` — SSL mandatory
- App Password (already have GMAIL_APP_PASSWORD in .env — reuse)
- Select label: `'"health-newsletters"'` (double-quoted for spaces)
- Fetch with `BODY.PEEK[]` to avoid auto-marking read; mark read explicitly after processing
- Handle multipart MIME: prefer `text/html`, fall back to `text/plain`
- Extract `href` links via `re.findall(r'href=["\']([^"\']+)["\']', html_body)`
- For Attia teaser emails: detect member link → use `browser` tool to fetch full content with stored credentials

### Health Knowledge Store Schema
```json
{
  "entries": [
    {
      "id": "attia-ep224-2026-01-15",
      "show": "The Peter Attia Drive",
      "episode_title": "...",
      "episode_number": "224",
      "date": "2026-01-15",
      "source_quality": "whisper|transcript|show_notes",
      "topics": ["ApoB", "cardiovascular", "statins"],
      "summary": "Full summary text...",
      "source": "podcast|newsletter",
      "tagged_by": "auto|user"
    }
  ]
}
```

### Summarization
- Uses the OpenClaw agent's LLM (gpt-5-mini primary) via the skill pipeline — not a direct API call
- SKILL.md instructs the agent to summarize with adaptive depth based on show classification
- Show classification stored in `feeds.json` per show; correctable via Telegram feedback
- Summary depth override: user can request "longer summary of episode X" via Telegram

### Email Digest Format
```
Subject: Podcast Digest — [N] new episodes · [date]

[Show Name] — [Episode Title]
Source: [Published Transcript | Whisper | Show Notes]
[date]

[Adaptive summary body — 1 paragraph to 4 paragraphs depending on show type]

Listen: [episode URL]
---
[repeat per episode]
---
Health Newsletters: 2 items archived to health store (Attia weekly brief, FoundMyFitness)
```

### On-Demand / Semantic Search
- "give me a summary of a recent Huberman episode on vagus nerve"
- Skill searches `episodes.json` titles + descriptions using string matching first
- If no match: fetch last N episodes from RSS, scan descriptions for topic keywords
- Then: fetch transcript / Whisper → summarize → email result
- Semantic match uses agent's own LLM reasoning (no vector DB needed for ~28 shows)

---

## Impact Summary

### New Files Created
```
workspace/skills/podcast-summary/
├── SKILL.md
├── OPERATIONS.md
├── podcast-knowledge.md          # Show metadata, transcript strategy cache
├── podcast_vault/
│   ├── feeds.json                # RSS subscriptions, show state
│   ├── episodes.json             # Episode cache + summaries
│   └── health_knowledge.json     # Health-relevant summaries
├── scripts/
│   ├── engine.py                 # Main cron coordinator
│   ├── rss_poller.py             # RSS feed parsing (stdlib xml)
│   ├── transcript_fetcher.py     # Transcript acquisition pipeline
│   ├── whisper_client.py         # HTTP client for Whisper bridge
│   ├── gmail_fetcher.py          # Gmail IMAP newsletter ingestion
│   ├── health_store.py           # Health knowledge store CRUD
│   ├── digest_emailer.py         # Email digest composer + sender
│   ├── importer.py               # OPML import
│   └── podcast-log.sh            # Structured logging (clone scout-log.sh)
scripts/
├── whisper-bridge-service.py     # Whisper HTTP bridge (runs on Mac host)
└── systemd/
    └── whisper-bridge.user.service  # Auto-start whisper-server on Mac
```

### Modified Files
```
agents/sample-agent/.env                     # Add new credential keys
agents/sample-agent/config/openclaw.json     # Enable skill in skills.entries
agents/sample-agent/config/cron/jobs.json    # Add 11PM nightly cron entry + 6AM notification
```

### No Changes To
- Existing skills (send-email, productwatcher, restaurant-scout, etc.)
- Docker image / Dockerfile
- scripts/docker-compose.yml.tmpl (host.docker.internal already present)
- openclaw.json schema (use skill files only, never add custom schema keys)

---

## External Research Highlights

### whisper.cpp HTTP Server
- Ships built-in `whisper-server` binary (`examples/server/`)
- macOS Metal auto-detected — no flags needed
- API: `POST /inference` with multipart form: `file=@audio.mp3`, `response_format=json`
- `--convert` flag handles MP3/M4A/AAC → requires ffmpeg on host
- Single mutex: requests serialize — plan for sequential episode processing
- Source: https://github.com/ggml-org/whisper.cpp

### Transcript Availability
- **Tim Ferriss**: Free public transcripts at `tim.blog/*-transcript/` — highly reliable
- **Huberman**: Official behind Supercast paywall; `podscript.ai` has free third-party transcripts
- **Peter Attia**: No public transcripts; HappyScribe may have them but 403 risk; Whisper fallback reliable
- **Rhonda Patrick**: No public transcripts; private RSS gives audio access; Whisper required
- `podcast:transcript` RSS tag: NOT present in any of these 4 major shows' feeds
- Other hunting/outdoor shows: mostly hosted on Libsyn/Megaphone — check tag, expect show notes only

### Python Stdlib Sufficiency
- RSS parsing: `xml.etree.ElementTree` + namespace maps — fully sufficient
- Date parsing: `email.utils.parsedate_to_datetime()` handles RFC 2822
- Gmail IMAP: `imaplib.IMAP4_SSL` + App Password — fully sufficient
- No external Python packages needed beyond Python 3.6+ stdlib

### Common Pitfalls
- ElementTree namespace: must pass `namespaces={}` dict or use Clark notation `{namespace_uri}tag`
- Enclosure guard: `enc = item.find('enclosure'); if enc is not None` — some feeds mix blog+podcast
- Gmail App Password: regular password auth disabled by Google since May 2022
- whisper-server concurrent requests: serialized via mutex — don't send parallel requests
- HappyScribe scraping: 403 risk — always have Whisper as fallback

---

## Architecture Decision: Phased Delivery

Per scope.md recommendation, deliver in 4 phases:

**Phase 1: Core Loop** — RSS polling + show notes summaries + email digest
- Immediately useful; no Whisper setup required
- Validates pipeline end-to-end

**Phase 2: Transcript Pipeline** — whisper-server bridge + transcript scraping
- Adds Whisper host bridge service (new component)
- Per-show transcript strategy (Tim Ferriss → podscript → HappyScribe → Whisper → show notes)
- High-quality summaries for health shows

**Phase 3: Health Store + Newsletter Ingestion**
- Gmail IMAP polling for health-newsletters label
- Health knowledge JSON store
- Auto-tagging always/sometimes/never per show

**Phase 4: On-Demand + Semantic Search**
- Telegram/iMessage triggered summaries
- Vague query handling ("recent Huberman on vagus nerve")
- Re-summarize at different depth on request
- 6AM notification cron
