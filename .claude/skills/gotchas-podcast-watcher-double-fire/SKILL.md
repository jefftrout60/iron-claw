---
name: gotchas-podcast-watcher-double-fire
description: Use when working on podcast-watcher.py or debugging duplicate emails from on-demand podcast requests
user-invocable: false
---

# Podcast Watcher Double-Fire on Intent 3b Messages

**Trigger**: podcast-watcher, double email, duplicate summary, two emails, typing indicator, Intent 3b, style override, depth override
**Confidence**: high
**Created**: 2026-03-28
**Updated**: 2026-04-03
**Version**: 2

## Symptom

User sends a message like "Re-run Beyond the Kill #607 with deep science style, extended depth, save to health" and receives **two identical emails** before the real Whisper-transcribed summary arrives. The agent shows "typing" for several minutes while Whisper runs.

## Root Cause

Two independent systems both react to the same Telegram message:

1. **podcast-watcher.py** (host-side) — detects any message with podcast intent keywords and fires `on_demand.py` with just `--query`. Finds the existing cached summary and emails it immediately.
2. **Agent (Intent 3b)** — also fires `on_demand.py` with the full flags (`--style`, `--depth`, `--strategy`, `--save-to-health`). Waits for Whisper to complete before responding.

Result: 2 duplicate cached emails + 1 correct deep_science email = 3 total. The episode lock prevents them running simultaneously, but both find the cached summary before Whisper completes.

## Fix (Implemented — commit 9c2135f)

`podcast-watcher.py` now skips messages containing Intent 3b modifier keywords. These are handled by the agent directly.

**Location:** `scan_sessions()` in `scripts/podcast-watcher.py` — skip check runs before `is_podcast_request()`.

```python
INTENT_3B_PHRASES = (
    "deep science", "deep_science", "science format",
    "extended depth", "save to health", "health-related",
    "re-run", "re-transcribe", "force whisper",
    "hunting format", "interview format", "long form",
)
if any(phrase in lower_text for phrase in INTENT_3B_PHRASES):
    continue
```

## Prevention

Any time new Intent 3b trigger phrases are added to SKILL.md, add the corresponding phrases to `INTENT_3B_PHRASES` in podcast-watcher.py to keep them in sync.

---

## Container Has No ffmpeg — Always Pass --strategy to docker exec

**Trigger**: show_notes fallback, container on_demand, whisper not running, on-demand returns show_notes
**Confidence**: high
**Created**: 2026-04-03
**Updated**: 2026-04-03
**Version**: 1

### Symptom

On-demand Telegram requests always return show_notes summaries even though Whisper is configured. The agent replies and fires `on_demand.py` but no Whisper transcription happens.

### Root Cause

The agent container (`sample-agent_secure`) has **no ffmpeg installed**. `on_demand.py` uses local whisper chunking by default, which requires ffmpeg to split audio. Without it, the strategy silently falls back to `show_notes`.

This affects ALL on-demand requests fired via `podcast-watcher.py`'s `docker exec` path, not just style-override requests.

### Fix (Implemented — commit 9c2135f)

`fire_on_demand()` in `podcast-watcher.py` now always passes `--strategy fetch_openai_whisper show_notes` to every `docker exec` call:

```python
cmd = [
    "docker", "exec", container,
    "python3", on_demand,
    "--query", query,
    "--agent", agent_name,
    "--strategy", "fetch_openai_whisper", "show_notes",
]
```

This forces cloud Whisper first, then falls back to show_notes. The container cannot use local whisper regardless of `WHISPER_BRIDGE_URL` because ffmpeg is absent.

### Prevention

- Never assume the container has ffmpeg — it doesn't, and the Docker image does not install it
- Any new code that calls `on_demand.py` via `docker exec` MUST pass `--strategy fetch_openai_whisper show_notes`
- The WHISPER_BRIDGE_URL fix (127.0.0.1 → host.docker.internal inside container) is handled in `whisper_client.py` — you don't need to set a different URL for container vs host; the client remaps automatically
