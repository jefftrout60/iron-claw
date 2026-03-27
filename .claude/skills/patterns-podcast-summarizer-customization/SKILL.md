---
name: patterns-podcast-summarizer-customization
description: Use when adding per-show instructions, changing how episodes are summarized, adding universal prompt rules, or customizing summary format for a show or episode type
user-invocable: false
---

# Pattern: Podcast Summarizer Customization System

**Trigger**: summary style, show instructions, qa_suffix, list_suffix, topic_map, _SHOW_EXTRA_INSTRUCTIONS, prompt suffix, AMA, Q&A format
**Confidence**: high
**Created**: 2026-03-27
**Updated**: 2026-03-27
**Version**: 1

## Problem

Different shows and episode types need different summary formats. Some need gear enumerated item-by-item. Some AMA episodes need Q&A structure. Some need numbered frameworks spelled out. The solution must be layered: universal rules + per-show rules + per-episode overrides.

## Solution: Two-Tier System in `summarizer.py`

### Tier 1: Universal Suffixes (all episodes, all shows)

Added to every style's user prompt in `_build_prompt()`:

```python
# list_suffix — enumerate any explicit list AND any "N [thing]" named framework
list_suffix = (
    "\n\nIMPORTANT: If the episode presents any numbered or explicit list... "
    "This also applies to any named framework where a specific number is part of the concept "
    "(e.g. 'the three macronutrients of happiness', 'four idols') — list every item explicitly."
)

# qa_suffix — Q&A format when "Q&A" appears in episode title
qa_suffix = ""
if "q&a" in title.lower():
    qa_suffix = "\n\nThis episode has 'Q&A' in the title. Structure the summary as a Q&A..."

# topic_map_section — inject show notes as topic guide when real transcript exists
topic_map_section = ""
if source_quality != "show_notes" and show_notes:
    topic_map_section = f"\n\nTopic map from show notes (ensure your summary covers these):\n{show_notes[:1500]}"
```

### Tier 2: Per-Show Instructions (`_SHOW_EXTRA_INSTRUCTIONS`)

Keyed by lowercase show name substring — matched via `key in show_lower`:

```python
_SHOW_EXTRA_INSTRUCTIONS = {
    "rokcast": "For any gear... list every specific item by name — brand, model, key details...",
    "peter attia": "If this is an AMA episode (AMA in title) — structure as Q&A...",
    "foundmyfitness": "If this is an AMA episode — structure as Q&A...",
    "hunt backcountry": "If this is a Monday Minisode (MM in title) — structure as Q&A...",
    "triggernometry": "...",
    "vomradio": "...",
}
```

**To add a new show rule**: add a key using a distinctive lowercase substring of the show name. The match is `key in show_name.lower()`, so `"peter attia"` matches "The Peter Attia Drive".

### Per-Episode Overrides (`on_demand.run()` parameters)

```python
on_demand.run(
    query,
    strategy_override=['fetch_openai_whisper', 'show_notes'],  # force cloud Whisper
    summary_style_override='deep_science',                       # override feed's style
    save_to_health=True,                                         # force health store write
    depth='extended',                                            # extended summary
)
```

## When to Use Each Tier

| Situation | Solution |
|-----------|----------|
| All episodes need a rule | Universal suffix in `_build_prompt()` |
| One show always needs special treatment | `_SHOW_EXTRA_INSTRUCTIONS` entry |
| One episode type on one show (e.g. MM) | `_SHOW_EXTRA_INSTRUCTIONS` with title check |
| One-off episode needs different style | `summary_style_override` in backlog script |
| Q&A format for "Q&A" titled episodes | Already handled by `qa_suffix` |
| Numbered frameworks (e.g. "3 pillars of X") | Already handled by strengthened `list_suffix` |

## Trade-offs

Per-show instructions add tokens to every prompt for that show. Keep them focused — the model reads all of it but only the relevant part applies.
