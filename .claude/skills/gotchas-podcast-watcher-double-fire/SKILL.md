---
name: gotchas-podcast-watcher-double-fire
description: Use when working on podcast-watcher.py, debugging duplicate emails from on-demand requests, or debugging duplicate nightly digest emails in Evernote
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

---

## Nightly Digest Sent Twice — Broken Dedup Guard

**Trigger**: duplicate nightly digest, two Evernote notes same night, "Fri May 1" and "Sat May 2", engine runs twice, last_email_sent_at not persisted
**Confidence**: high
**Created**: 2026-05-02
**Updated**: 2026-05-02
**Version**: 1

### Symptom

Two identical Evernote digest notes appear each night from the nightly `engine.py` run. The notes have **different date headers** (e.g. "Fri May 1" and "Sat May 2") because the first sends just before midnight and the second just after. Content is identical — same episodes, same "Whisper (standard) [date]" tags — confirming Whisper only ran once.

Timing is consistent: ~11:20 PM first, ~12:20 AM second (approximately 1 hour apart).

### Root Cause

Two bugs combined:

**Bug 1 — engine.py runs twice.** Something triggers a second invocation of `engine.py` ~1 hour after the 11 PM cron. Root cause of the second trigger was not definitively identified but is systematic (happened every night for multiple days).

**Bug 2 — dedup guard silently failed.** A `last_email_sent_at` timestamp was set on a `status` dict variable that didn't exist yet at that point in the code. Python raised a `NameError`, which was silently caught by the surrounding `except Exception` that wraps the `send_digest()` call. The email sent successfully, but the timestamp was never written. The second run saw `None` for `last_email_sent_at` and sent again.

```python
# BROKEN — status doesn't exist here yet; NameError silently swallowed
try:
    digest_emailer.send_digest(...)
    status["last_email_sent_at"] = datetime.now(timezone.utc).isoformat()  # NameError!
except Exception as exc:
    print(f"WARNING: digest email failed: {exc}")  # catches the NameError, not just SMTP errors

# status dict created LATER — overwrites any partial assignment above
status = {"version": 1, "run_date": ..., ...}
write_status(status)
```

### Fix (engine.py)

Use a local variable and include it in the `status` dict at write time:

```python
_email_sent_at = None
if processed and not args.no_email and not _already_sent:
    try:
        digest_emailer.send_digest(...)
        _email_sent_at = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        print(f"WARNING: digest email failed: {exc}")

# Build status dict — include _email_sent_at here, not before
status = {
    "version": 1,
    "run_date": ...,
    ...
    "last_email_sent_at": _email_sent_at,
}
write_status(status)
```

The dedup check reads `status_before.get("last_email_sent_at")` from the file loaded at the start of the run. Window is 4 hours — enough to block a ~1 hour duplicate without blocking the next night's legitimate run.

### Prevention

- **Never assign to a dict variable inside a `try` block if that variable is created later.** The `except Exception` will swallow the `NameError` silently.
- Any dedup state written to `processing_status.json` must go into the dict passed to `write_status()`, not assigned to a variable before that dict exists.
- When adding dedup guards, verify the guard by checking `processing_status.json` manually after the first run to confirm `last_email_sent_at` is populated.
