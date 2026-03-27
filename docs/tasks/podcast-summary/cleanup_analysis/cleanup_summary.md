# Cleanup Summary — Podcast Summary Skill

**Date**: 2026-03-27
**Scope**: Context mode — session-modified files
**Files analyzed**: 18 | **Issues found**: 7 confirmed-safe, 1 latent bug, 6 historical scripts

---

## Executive Summary

18 files analyzed. No production-breaking issues. Two redundant local imports confirmed safe to remove (dead-code slop). Five summarizer inconsistencies (docstrings + CLI choices) are documentation/tooling drift — safe to clean up. One latent bug found: the LLM auto-classification prompt silently omits two production styles (meateater, orvis_fly_fishing) — currently harmless because both feeds have styles pre-assigned, but will bite when a new show of either type is added without setting the style.

Backlog scripts (run_backlog*.sh): all 138/139 vault episodes are summarized; scripts are complete. Recommended for archival.

---

## Safe Removals / Updates

### Task 1 — whisper_client.py: Remove redundant `import os as _os`
- **File**: `agents/sample-agent/workspace/skills/podcast-summary/scripts/whisper_client.py`
- **Line 92**: Delete `import os as _os`
- **Line 98**: Change `_os.environ[k]` → `os.environ[k]` and `k in _os.environ` → `k in os.environ`
- **Why**: `os` is imported at L21. The local alias `_os` was added unnecessarily — same object, no side effects.
- **Impact**: Zero — pure rename

### Task 2 — on_demand.py: Remove redundant `import re as _re`
- **File**: `agents/sample-agent/workspace/skills/podcast-summary/scripts/on_demand.py`
- **Line 557**: Delete `import re as _re`
- **Line 558**: Change `_re.search(` → `re.search(`
- **Why**: `re` is imported at L20. Used extensively throughout the module. The local alias inside `main()` is dead slop.
- **Impact**: Zero — pure rename

### Task 3 — summarizer.py: Fix stale docstrings (classify_show_style + summarize)
- **File**: `agents/sample-agent/workspace/skills/podcast-summary/scripts/summarizer.py`
- **L479**: `"five"` → `"seven"`
- **L482**: append `, meateater, orvis_fly_fishing` to the style list
- **L495**: `"five"` → `"seven"`
- **L409-410**: append `, meateater, orvis_fly_fishing` to summary_style arg docs
- **Why**: Two styles were added after these docs were written. Doc drift.
- **Impact**: Zero (documentation only)

### Task 4 — summarizer.py: Fix CLI `--style` choices (both parsers)
- **File**: `agents/sample-agent/workspace/skills/podcast-summary/scripts/summarizer.py`
- **L572**: Add `"meateater", "orvis_fly_fishing"` to `choices` list
- **L588-589**: Add `"meateater", "orvis_fly_fishing"` to legacy `choices` list
- **Why**: CLI test mode hard-errors on `--style meateater` or `--style orvis_fly_fishing`. Production unaffected (calls `summarize()` directly).
- **Impact**: CLI test mode only — enables manual testing of these two styles

### Task 5 — summarizer.py: Fix LLM classification prompt (latent bug)
- **File**: `agents/sample-agent/workspace/skills/podcast-summary/scripts/summarizer.py`
- **L529** (after `"devotional (Christian/religious teaching). "`): Add before closing quote:
  `"meateater (MeatEater Podcast — hunting, wild food, conservation with Steve Rinella), "` and
  `"orvis_fly_fishing (Orvis fly-fishing podcast with Tom Rosenbauer), "`
- **Why**: LLM auto-classification prompt lists only 5 styles. If a new MeatEater/Orvis-type feed is added without a pre-assigned style, it will be silently misclassified.
- **Current risk**: LOW (both existing feeds have styles pre-assigned). Latent on new feed additions.
- **Impact**: Better auto-classification for future shows

### Task 6 — Archive completed backlog scripts
- **Files**: `run_backlog.sh`, `run_backlog2.sh`, `run_backlog3.sh`, `run_backlog3_cleanup.sh`, `run_backlog4.sh`, `run_backlog4_cleanup.sh`
- **Recommendation**: Move to `scripts/archive/` subdirectory rather than delete — they document the episode query patterns used and serve as templates for future backlog runs
- **Why**: All episodes processed (138/139 have summaries). These scripts are operationally complete.
- **Impact**: Cleaner scripts/ directory; patterns preserved for reference

---

## Manual Review Required

None — all findings have clear dispositions.

---

## Excluded Items (KEEP)

| Item | Reason |
|------|--------|
| `hunting_outdoor` style in summarizer.py | Intentionally preserved — available via `summary_style_override` |
| `_extract_episode_number()` in engine.py + on_demand.py | Duplication, not dead — future refactor |
| `_find_repo_root()` / `_load_env()` across 5 modules | Duplication, not dead — future refactor |
| `--episode` TODO in engine.py | Stub for future feature, documents intent |
| `tempfile.mktemp()` in transcript_fetcher.py | C-3 from architecture review — future fix |
| `import re` inside `_extract_episode_number()` engine.py | Not redundant — `re` not at module level in engine.py |
| 6 deferred imports in on_demand.py (rss_poller, vault, etc.) | Intentional lazy-load of sibling modules |

---

## Estimated Impact

- Lines of dead code removed: ~4 lines (2 import statements + 2 renaming lines)
- Docstring updates: ~6 lines
- CLI choices updates: ~4 lines
- LLM prompt fix: ~2 lines
- Scripts archived: 6 files

Total: minor cleanup — ~10 lines changed, 6 files archived.
