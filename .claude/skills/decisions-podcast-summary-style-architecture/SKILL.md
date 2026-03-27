---
name: decisions-podcast-summary-style-architecture
description: Use when changing a feed's summary style, wondering why hunting_outdoor is not assigned to any feed, or deciding what style to use for a new podcast
user-invocable: false
---

# Decision: Podcast Summary Style Architecture

**Trigger**: summary_style, hunting_outdoor, long_form_interview, deep_science, feed style, extended depth
**Confidence**: high
**Created**: 2026-03-27
**Updated**: 2026-03-27
**Version**: 1

## Context

The system launched with a `hunting_outdoor` style that used rigid category buckets (Gear & Equipment, Conservation, Trip Planning, etc.) with "1-3 sentences per category." After reviewing summaries in production, this structure didn't fit most hunting/outdoor shows — they're conversational interviews, not structured field reports.

## Decision

1. **Retired `hunting_outdoor`** as an active style — all 10 previously-assigned feeds moved to `long_form_interview`
2. **Set `extended` depth as the global default** — in both `engine.py` and `on_demand.run()` default parameter
3. **Style assignments** (as of 2026-03-27):

| Style | Shows |
|-------|-------|
| `deep_science` | Peter Attia, Huberman Lab, FoundMyFitness, Barbell Shrugged, Better Brain Fitness, Valley to Peak |
| `long_form_interview` | Hunt Backcountry, Hunting Dog, Rokcast, ElkShape, Beyond the Kill, Mindful Hunter, Live Wild, Backcountry Hunting, Western Hunter, Eastmans' Elevated, Modern Day Sniper, American West, Hornady, Shawn Ryan, Tim Ferriss, All-In, VOMRadio |
| `commentary` | TRIGGERnometry, Winston Marshall, Philosophize This, Just Thinking, Invest Like the Best |
| `meateater` | The MeatEater Podcast |
| `orvis_fly_fishing` | The Orvis Fly-Fishing Podcast |
| `devotional` | Grace to You, Ask Ligonier, Renewing Your Mind |

## Rationale

- `hunting_outdoor` forced summaries into categories that didn't match conversational episode content
- `long_form_interview` produces flowing narrative summaries that capture the actual conversation
- `extended` depth costs no extra effort (same API call) but produces significantly more useful summaries — 2 paragraphs was consistently not enough
- Per-show instructions in `_SHOW_EXTRA_INSTRUCTIONS` handle show-specific needs (gear enumeration for Rokcast, Q&A for MM episodes) without needing a new style

## Consequences

- `hunting_outdoor` style still exists in `summarizer.py` — available via `summary_style_override` if ever needed for a specific episode
- Adding a new hunting/outdoor show → default to `long_form_interview`, add per-show instructions if needed
- Adding a new science/health show → use `deep_science`, add to `WHISPER_LARGE_SHOWS` in `whisper_client.py`
