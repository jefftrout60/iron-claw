---
name: gotchas-unbounded-file-growth
description: Use when reviewing new code, auditing existing code, or adding any persistent state — always check for files that grow without bound
user-invocable: false
---

# Unbounded File Growth

**Trigger**: unbounded growth, infinite growth, file size, log rotation, state eviction, prune, disk space, growing file
**Confidence**: high
**Created**: 2026-03-27
**Updated**: 2026-03-27
**Version**: 1

## The Rule

**Any file that is appended to over time must have a pruning or eviction mechanism.** Without one, it grows without bound until it fills the disk.

This check is easy to miss during code review because the problem only manifests in production over weeks or months. By then the file is huge and the fix requires cleanup work.

## Symptom

- Log file or state file grows to GB+ over time
- Disk fills on a system that seemed fine
- Process reading the file slows down as it grows
- No error — just silent growth

## Root Cause

Files written to repeatedly with no corresponding delete/truncate/rotate logic. Common patterns:

1. **Log files** — `FileHandler` with no rotation → grows forever
2. **State dicts** — dict entries added when things appear, never removed when they disappear
3. **Episode/record lists** — items appended, old items never pruned
4. **Vault/cache files** — accumulated entries with no TTL or max size

## Where to Look (Ironclaw/Podcast)

| File | Risk | Fix Applied |
|------|------|-------------|
| `podcast-watcher.log` | HIGH — new line every 5s | `RotatingFileHandler` (5MB, 3 backups) |
| `podcast-watcher.state` | MEDIUM — entry per session file, sessions accumulate | Evict keys for deleted `.jsonl` files |
| `episodes.json` | HIGH — one entry per processed episode | `prune_episodes()`: 30d summaries, 90d metadata |
| `health_knowledge.json` | INTENTIONAL — curated knowledge base, should grow | No pruning; future: migrate to SQLite |

## Solution Patterns

### Log rotation (Python)
```python
import logging.handlers

logging.basicConfig(handlers=[
    logging.handlers.RotatingFileHandler(
        path,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
    )
])
```

### State dict eviction (Python)
```python
# Evict keys for items that no longer exist
existing = {p.name for p in directory.glob("*.jsonl")}
stale = [k for k in list(state["files"]) if k not in existing]
for k in stale:
    del state["files"][k]
```

### Record list pruning (Python)
```python
# Keep recent summaries, strip old summaries, drop ancient metadata
cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).date().isoformat()
kept = [ep for ep in episodes if ep.get("date", "") >= cutoff]
```

## The Audit Checklist

Run this check on every file-writing component before shipping:

- [ ] **Log files** — Is there a `RotatingFileHandler`, `TimedRotatingFileHandler`, or logrotate config?
- [ ] **State/cache dicts** — Are stale keys evicted when the source item disappears?
- [ ] **Accumulated record lists** (episodes, events, records) — Is there a max-age or max-count prune?
- [ ] **Append-only files** — Is there any mechanism to archive or truncate old entries?
- [ ] **Exception: intentional knowledge bases** — Confirm growth is intentional and has a migration plan for scale (e.g. SQLite when JSON becomes too large)

## Prevention

Add this question to every code review of file-writing code:

> "If this runs for 12 months without intervention, how large is this file?"

If the answer is "I don't know" or "very large", it needs a prune/rotate/evict mechanism.
