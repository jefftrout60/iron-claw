# Initial Findings — Podcast Summary Skill Cleanup

**Date**: 2026-03-27
**Scope**: Context mode — session-modified files
**Files analyzed**: 18 (12 Python, 6 shell scripts)

## Pattern Summary

| Pattern | Count | Files |
|---------|-------|-------|
| Redundant local imports | 2 | whisper_client.py, on_demand.py |
| Duplicate function implementations | 3 | engine.py + on_demand.py (_extract_episode_number), 5 modules (_find_repo_root, _load_env) |
| Stale docstring | 1 | summarizer.py (classify_show_style) |
| CLI choices mismatch | 1 | summarizer.py (--style missing meateater, orvis_fly_fishing) |
| TODO stub (unimplemented flag) | 1 | engine.py (--episode) |
| Historical one-time scripts | 5 | run_backlog*.sh |
| Redundant import inside function | 1 | engine.py (_extract_episode_number imports re locally) |

## Findings by File

### whisper_client.py
- L92: `import os as _os` inside `_load_env()` — `os` already imported at module level (L21).
  All usages are `_os.environ` which could be `os.environ`. **SAFE_TO_REMOVE** (local import + rename refs to `os.environ`)

### on_demand.py
- L557: `import re as _re` inside `main()` — `re` already imported at module level (L17).
  All usages via `_re.search(...)` could use module-level `re`. **SAFE_TO_REMOVE**

### engine.py
- L256: `import re` inside `_extract_episode_number()` — `re` NOT imported at module level. KEEP (required).
- L499/L528: `--episode` TODO comments — stub for unimplemented feature. KEEP (documents intent).
- `_extract_episode_number()` function: identical copy exists in `on_demand.py:478`.
  Duplication, not dead code — NEEDS_VALIDATION (one could import from the other)

### summarizer.py
- `classify_show_style` docstring L479: says "five summary style categories" — now 7 styles exist.
  **SAFE_TO_REMOVE** (update docstring wording)
- CLI `--style` choices (L571-573, L587-590): missing `meateater` and `orvis_fly_fishing`.
  These styles exist in production. **SAFE_TO_REMOVE** (add missing choices — prevents CLI testing of these styles)

### transcript_fetcher.py
- `import shutil` inside `fetch_openai_whisper()` finally block (L753): works but non-idiomatic.
  KEEP — this is a lazy import pattern to avoid pulling shutil into the module-level namespace for a rarely-executed cleanup path.
- `tempfile.mktemp()` at L698: TOCTOU issue (C-3 from architecture review). KEEP — flagged for future fix, not a cleanup item.

### on_demand.py (continued)
- `_find_episode_in_feed()` L161: "Q&A #N" detection for FoundMyFitness Aliquot. KEEP — active feature.
- `_QUERY_CONNECTORS` L231: defined inside function, single-use. KEEP — correct placement, not dead.

### Backlog scripts (historical)
- `run_backlog.sh`: All 14 episodes processed. Historical artifact.
- `run_backlog2.sh`: 3 episodes (Winston Marshall retry). Done.
- `run_backlog3.sh`: 46 episodes. Done.
- `run_backlog3_cleanup.sh`: Cleanup re-runs. Done.
- `run_backlog4.sh`: 12 episodes. Done.
- `run_backlog4_cleanup.sh`: 6 cleanup re-runs. Done.
  All are **NEEDS_VALIDATION** — completed, but serve as templates/reference for future backlog operations.

## Duplication Clusters (not dead code, refactor candidates)

### Cluster 1: _find_repo_root() — 4 copies
- `whisper_client.py:70`
- `summarizer.py:30`
- `on_demand.py:35`
- `engine.py:31` (named `find_repo_root`, public)
**Same logic**. Consolidation target: could be in `vault.py` or a shared `config.py`.

### Cluster 2: _load_env() — 5 copies
- `whisper_client.py:82`
- `summarizer.py:41`
- `on_demand.py:46`
- `engine.py:56` (named `load_env`, public, slightly different interface)
- `transcript_fetcher.py:545` (named `_load_openai_api_key`, partial version)
**Nearly identical logic**. Consolidation target: shared `config.py`.

### Cluster 3: _extract_episode_number() — 2 copies
- `engine.py:250`
- `on_demand.py:478`
**Identical 7-line functions**. One could import from the other.

## Investigation Priorities

1. **HIGH**: Validate `import os as _os` removal in whisper_client.py (affects startup path)
2. **HIGH**: Validate `import re as _re` removal in on_demand.py (affects CLI dedup logic)
3. **MEDIUM**: Validate summarizer.py CLI choices gap (meateater, orvis_fly_fishing missing)
4. **LOW**: Backlog scripts — confirm all episodes are in vault before suggesting deletion
