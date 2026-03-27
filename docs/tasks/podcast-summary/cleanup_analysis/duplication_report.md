# Duplication Report — Podcast Summary Skill

**Date**: 2026-03-27

## Duplicate Code Clusters

### Cluster 1: _find_repo_root() (4 instances)

- `whisper_client.py:70-79`
- `summarizer.py:30-38`
- `on_demand.py:35-43`
- `engine.py:31-44` (public name `find_repo_root`)

**Pattern**: Walk up from `__file__` to find directory containing `CLAUDE.md`. Identical 9-line implementation.

**Recommendation**: Extract to a shared module (e.g. `config.py` or `vault.py`).

**Effort**: medium (touching 4 files, risk of import ordering)

---

### Cluster 2: _load_env() (5 instances)

- `whisper_client.py:82-114`
- `summarizer.py:41-69`
- `on_demand.py:46-73`
- `engine.py:56-74` (public `load_env`, slightly different — takes `env_path: Path` not `agent_name: str`)
- `transcript_fetcher.py:545-567` (named `_load_openai_api_key`, partial — only returns OPENAI_API_KEY)

**Pattern**: Check `os.environ` for known keys, fall back to reading `.env` file from repo root.

**Note**: `engine.py`'s version takes a pre-resolved `Path` instead of `agent_name`, making it the odd one out. `transcript_fetcher.py` only loads `OPENAI_API_KEY`. The others load 5+ keys.

**Recommendation**: Consolidate 4 identical versions into shared `config.py`; leave `engine.py` as-is (different interface) or adapt it.

**Effort**: high (5 files, risk of subtle breakage from environment variable precedence changes)

---

### Cluster 3: _extract_episode_number() (2 instances)

- `engine.py:250-265`
- `on_demand.py:478-486`

**Pattern**: Regex search for `#NNN` or `Ep/Episode NNN` in episode title; return `#NNN` string or `""`.

**Note**: The implementations are slightly different:
- `engine.py`: Returns `f"#{m.group(1)}"` for both patterns, docstring mentions the format
- `on_demand.py`: Same logic, 7-line version without docstring

**Recommendation**: `on_demand.py` could `import engine` and call `engine._extract_episode_number()`, or both could import from a shared utility. Low-risk consolidation.

**Effort**: low

---

## Summary

| Cluster | Instances | Effort | Priority |
|---------|-----------|--------|----------|
| _find_repo_root() | 4 | medium | future |
| _load_env() | 5 | high | future |
| _extract_episode_number() | 2 | low | future |

**Note**: All clusters are refactor candidates, not dead code. They work correctly. Consolidation is recommended but not urgent — the codebase is a single-skill script collection without a shared lib convention yet. Address when adding new modules.
