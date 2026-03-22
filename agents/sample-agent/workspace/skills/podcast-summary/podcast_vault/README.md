# podcast_vault

Runtime state for the podcast-summary skill. JSON files are gitignored (they contain live data, not source). This README and `.gitignore` are tracked.

---

## feeds.json

Feed registry. One entry per podcast RSS feed.

```json
{
  "version": 1,
  "last_updated": "2026-03-21T11:00:00Z",
  "feeds": [
    {
      "id": "peter-attia-drive",
      "title": "The Peter Attia Drive",
      "rss_url": "https://...",
      "state": "active",
      "summary_style": "deep_science",
      "health_tier": "always",
      "whisper_model": "large-v3",
      "transcript_strategy": ["fetch_happyscribe", "whisper_large"],
      "transcript_strategy_last_tested": "2026-03-21",
      "last_checked": "2026-03-21T11:00:00Z",
      "last_episode_guid": "ep224-guid-hash"
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Slug used as foreign key in episodes.json |
| `title` | string | Human-readable show name |
| `rss_url` | string | RSS/Atom feed URL |
| `state` | string | `"active"` \| `"one-off"` \| `"inactive"` |
| `summary_style` | string\|null | `"deep_science"` \| `"long_form_interview"` \| `"commentary"` \| `"hunting_outdoor"` \| `"devotional"` \| null (auto-classify on first episode) |
| `health_tier` | string\|null | `"always"` \| `"sometimes"` \| `"never"` \| null |
| `whisper_model` | string\|null | Per-show override: `"large-v3"` \| `"small.en"` \| null (use default routing) |
| `transcript_strategy` | array | Ordered list of strategy names to try. e.g. `["fetch_tim_blog", "whisper_small"]` |
| `transcript_strategy_last_tested` | string\|null | ISO date of last strategy test; failed strategies retried after 7 days |
| `last_checked` | string\|null | ISO 8601 timestamp of last RSS poll |
| `last_episode_guid` | string\|null | GUID of last seen episode — prevents reprocessing |

---

## episodes.json

Episode cache. One entry per processed episode.

**Retention:** last 90 days only. engine.py prunes entries older than 90 days on each run.

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
      "summary_extended": null,
      "summary_depth": "standard",
      "health_tagged": true,
      "health_store_id": "attia-ep224-2026-03-20",
      "digest_sent": true,
      "digest_date": "2026-03-21",
      "processed_at": "2026-03-21T02:14:00Z"
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `{show_id}_{guid_hash}` |
| `show_id` | string | Foreign key into feeds.json |
| `title` | string | Episode title from RSS |
| `pub_date` | string | ISO 8601 publication date |
| `audio_url` | string | MP3/audio enclosure URL |
| `duration_seconds` | int\|null | Episode duration |
| `source_quality` | string | `"published_transcript"` \| `"third_party_transcript"` \| `"whisper_large"` \| `"whisper_small"` \| `"show_notes"` |
| `summary` | string\|null | Standard summary text |
| `summary_extended` | string\|null | Extended depth summary (on-demand re-summarize only) |
| `summary_depth` | string | `"standard"` \| `"extended"` |
| `health_tagged` | bool | True if stored in health_knowledge.json |
| `health_store_id` | string\|null | FK into health_knowledge.json if health_tagged |
| `digest_sent` | bool | True if included in an email digest |
| `digest_date` | string\|null | ISO date the digest was sent |
| `processed_at` | string | ISO 8601 timestamp when engine.py processed this episode |

---

## health_knowledge.json

Persistent health knowledge store. Append-only — entries are never deleted.

**Retention:** permanent. Estimated ~500 entries/year.

```json
{
  "version": 1,
  "entries": [
    {
      "id": "attia-ep224-2026-01-15",
      "show": "The Peter Attia Drive",
      "episode_title": "Episode 224: ...",
      "episode_number": "224",
      "date": "2026-01-15",
      "source": "podcast",
      "source_quality": "whisper_large",
      "topics": ["ApoB", "cardiovascular"],
      "summary": "Full summary text...",
      "tagged_by": "auto"
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | `{show-slug}-ep{number}-{date}` |
| `show` | string | Human-readable show name |
| `episode_title` | string | Episode title |
| `episode_number` | string\|null | Episode number if available |
| `date` | string | ISO date (YYYY-MM-DD) |
| `source` | string | `"podcast"` \| `"newsletter"` |
| `source_quality` | string | Same values as episodes.json `source_quality` |
| `topics` | array | 3–8 health/science topic tags extracted by LLM |
| `summary` | string | Full summary text |
| `tagged_by` | string | `"auto"` (engine) \| `"user"` (Telegram override) |

---

## processing_status.json

Written by engine.py at the end of each nightly run. Read by the 6AM OpenClaw cron turn to generate the morning Telegram/iMessage notification.

```json
{
  "version": 1,
  "run_date": "2026-03-21",
  "status": "complete",
  "completed_at": "2026-03-21T03:47:00Z",
  "episodes_processed": 3,
  "shows": ["The Peter Attia Drive", "All-In", "Orvis Fly-Fishing"],
  "newsletters_archived": 1,
  "errors": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `version` | int | Schema version |
| `run_date` | string\|null | ISO date of the run (YYYY-MM-DD) |
| `status` | string | `"never_run"` \| `"in_progress"` \| `"complete"` \| `"failed"` |
| `completed_at` | string\|null | ISO 8601 timestamp when run finished |
| `episodes_processed` | int | Count of episodes successfully processed |
| `shows` | array | Show names processed this run |
| `newsletters_archived` | int | Count of health newsletters stored |
| `errors` | array | List of error strings from the run |

---

## Retention Policy

| File | Retention |
|------|-----------|
| `feeds.json` | Permanent (feed registry — never pruned) |
| `episodes.json` | Last 90 days (engine.py prunes on each run) |
| `health_knowledge.json` | Permanent (append-only knowledge store) |
| `processing_status.json` | Overwritten each run (single current state) |
