---
name: decisions-health-knowledge-architecture
description: Use when touching health_knowledge.json, planning health intelligence features, or deciding whether to prune/migrate health knowledge data
user-invocable: false
---

# Health Intelligence: Knowledge Base Architecture

**Trigger**: health_knowledge, health intelligence, Oura, blood labs, DEXA, biometric, trusted sources, Attia, Huberman, Patrick, SQLite migration
**Confidence**: high
**Created**: 2026-03-28
**Updated**: 2026-03-28
**Version**: 1

## Context

`health_knowledge.json` accumulates curated podcast knowledge from trusted sources (Attia, Huberman, Rhonda Patrick). Unlike `episodes.json`, it is **NOT pruned** — growth is intentional. This is the foundation of a personal health intelligence system. At 850+ episodes the flat JSON will eventually become unwieldy.

## Decision

Keep flat JSON until volume warrants migration. Planned evolution:

1. **Now**: flat JSON, append-only, no pruning
2. **Mid-term**: SQLite (when JSON queries get slow or file crosses ~50MB)
3. **Long-term**: semantic/embedding search layer for conceptually-related queries

## Rationale

This is a **personal health intelligence system**. The knowledge base exists to be cross-referenced against Jeff's own biometric data:

- Oura Ring (sleep, HRV, readiness)
- Blood labs
- DEXA scans
- Workout logs
- Blood pressure
- MRIs

Trusted-source podcast knowledge (Attia, Huberman, Patrick) is the **authoritative signal**. General web search is secondary. The whole point is to eventually answer questions like: "Given my last DEXA and blood panel, what does Attia say about this marker?"

## Transcript Caching (Deferred)

`on_demand.py` always re-downloads and re-transcribes audio when generating a new summary depth or style, even if Whisper was already run. There is no transcript cache — the raw transcript text is not stored in `episodes.json`.

**Impact:** Re-running an episode with `--style deep_science` or `--depth extended` costs a full Whisper transcription (~2-5 min, API cost) every time.

**Fix:** Store the raw transcript text in `episodes.json` under a `transcript` key after the first Whisper run. On subsequent runs, skip the fetch/transcribe step if `transcript` is already present and non-empty.

**Where to implement:** `on_demand.py` `run()` function, between the transcript fetch and summarize steps. Also `engine.py` nightly run should store it.

## Per-Episode Health Detection (Deferred)

Some shows are primarily hunting/outdoor but occasionally publish health-relevant episodes (e.g. biomechanics, physiology, nutrition, injury prevention). These are handled as one-offs for now using the `--save-to-health` and `--style deep_science` flags on `on_demand.py`.

**Shows to add detection logic for (future):**
- `beyond-the-kill` — currently `health_tier: never`; ep #607 foot/ankle biomechanics was one-off
- `the-hunt-backcountry-podcast` — currently `health_tier: never`; ep #573 Dr. Andy Galpin everyday habits was one-off

**Future implementation:** Add per-episode health topic detection (keyword or LLM-based) that promotes individual episodes to `sometimes` tier without changing the feed's default. This should live in `engine.py` at the point where `health_tier` is checked.

**One-off command:**
```bash
docker exec sample-agent_secure python3 \
  /home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/on_demand.py \
  --query "Show Name #NNN" --depth extended \
  --strategy fetch_openai_whisper show_notes \
  --style deep_science --save-to-health
```

## Migration Trigger

**Don't migrate health_knowledge.json in isolation.** The right time is when building the health database to import the decade of blood test results from Excel. Do both in the same effort — one SQLite database, one schema, everything queryable together from day one.

Planned unified schema:
- Blood test results (decade of history, imported from Excel)
- health_knowledge.json (trusted-source podcast knowledge)
- Future: Oura Ring data, DEXA scans, workout logs, blood pressure

## Consequences

- **DO NOT add prune logic to health_knowledge.json** — it would destroy the point of the system
- Migrate health_knowledge to SQLite at the same time as the blood test import, not before
- Embedding search sits on top of SQLite; schema should anticipate vector fields
- Future data integrations (Oura API, lab import) feed the personal side; podcast knowledge feeds the trusted-source side
