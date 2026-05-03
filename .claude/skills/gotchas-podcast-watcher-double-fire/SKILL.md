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

**Trigger**: duplicate nightly digest, two Evernote notes same night, engine runs twice, last_email_sent_at not persisted, concurrent engine runs, startup lock
**Confidence**: high
**Created**: 2026-05-02
**Updated**: 2026-05-03
**Version**: 2

### Symptom

Two Evernote digest notes appear each night with **slightly different episode summary text** (same episodes, different first-sentence phrasing) and different newsletter counts. The duplicate emails are confirmed via Gmail "All Mail" IMAP — both sent from the Gmail account ~39 minutes apart.

The email bodies are NOT identical — episode summaries differ slightly (LLM non-determinism) and the second email is missing the newsletter archive line (newsletters were already moved to Trash by the first run).

### Root Cause

Two concurrent `engine.py` runs both start before either has written `processing_status.json`. Both read `status_before.get("last_email_sent_at") == None`, so the 4-hour dedup guard is blind to both. Both summarize the episodes (generating slightly different LLM output), send emails, then one of them crashes or exits before writing status (the final `processing_status.json` only reflects one run).

The second run skips Whisper (audio already transcribed + deleted by first run) and re-summarizes from cached transcripts, producing slightly different text.

The trigger for the second concurrent run was not definitively identified despite exhaustive log analysis (no second crontab, no launchd, no container exec, no podcast-watcher active).

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

### Fix Part 1 (engine.py) — dedup guard

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

The dedup check reads `status_before.get("last_email_sent_at")` from the file loaded at the start of the run. Window is 4 hours.

**Limitation:** the dedup guard only works when runs are SEQUENTIAL. Concurrent runs both read `last_email_sent_at == None` before either has written the file. Fix Part 2 prevents concurrent runs.

### Fix Part 2 (engine.py) — startup lock (2026-05-03)

Bail at startup if another instance is already running. Uses `mkdir` atomicity (same pattern as `oura-sync.py`):

```python
def main() -> None:
    args = parser.parse_args()

    _LOCK = Path("/tmp/podcast-engine.lock")
    try:
        _LOCK.mkdir(exist_ok=False)
    except FileExistsError:
        print("[engine] Already running — exiting to prevent duplicate digest.", file=sys.stderr)
        sys.exit(0)
    try:
        _main_body(args)
    finally:
        _LOCK.rmdir()
```

The actual logic moved into `_main_body(args)`. Lock is always cleaned up via `finally`.

### Prevention

- **Never assign to a dict variable inside a `try` block if that variable is created later.** The `except Exception` will swallow the `NameError` silently.
- Any dedup state written to `processing_status.json` must go into the dict passed to `write_status()`.
- The startup lock is the primary defence; the 4-hour email dedup guard is a secondary backstop.
