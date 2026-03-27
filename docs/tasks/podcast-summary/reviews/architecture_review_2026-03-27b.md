# Architecture Review: Podcast Summary Skill (2026-03-27b)

**Scope:** Cleanup changes (`/spectre:clean`) and test additions (`/spectre:test`) from session 2026-03-27.

## Executive Summary

The codebase is in good shape after this session. The cleanup work (stale docstrings, missing argparse choices, incomplete LLM classification prompt) addressed real correctness gaps that would have caused silent misbehavior for two of seven styles. The test suite is well-structured, behavioral rather than implementation-coupled, and covers the right surface area. No critical issues found.

## Critical Issues

None.

## Simplification Opportunities

### 1. `_load_env()` is duplicated four times with minor variations

**Where:** `summarizer.py:41`, `whisper_client.py:82`, `on_demand.py:46`, `engine.py:56`; plus a fifth inline variant at `transcript_fetcher.py:545` (`_load_openai_api_key`).

**What:** Each module has its own copy of the repo-root-walk + `.env` parser. They differ in small ways: `on_demand.py` includes `PODCAST_EVERNOTE_EMAIL` in its known keys; `whisper_client.py`'s quote-stripping logic is slightly different (`.strip('"').strip("'")` vs the explicit length-check form); `engine.py` takes a `Path` arg while the others take a string agent name.

**Why it matters now:** It's livable at 5 copies, but adding a new env var (or fixing a parsing edge case) requires touching every copy. This is the single highest-duplication pattern in the skill.

**Suggested fix:** Extract a shared `env_loader.py` with `find_repo_root()` and `load_env(agent_name)`. Each module imports it. This is a 30-minute change that removes ~80 lines of duplication. Not urgent, but it would be the single highest-leverage cleanup remaining.

### 2. `_find_repo_root()` is also duplicated across 4 files

**Where:** `summarizer.py:30`, `whisper_client.py:70`, `on_demand.py:35`, `engine.py:31`.

Same function, same CLAUDE.md sentinel check, same error message format. This would be eliminated by the env_loader extraction above.

### 3. `_extract_episode_number()` is duplicated in `engine.py:250` and `on_demand.py:478`

**Where:** Both are identical -- regex for `#NNN` then `Ep/Episode NNN`.

**Suggested fix:** Put it in a shared utils module or one canonical location that both import. Low urgency since the function is stable, but it's a free win if the env_loader extraction happens.

## Architecture Alignment

### Follows existing patterns well

- **stdlib-only HTTP:** All modules consistently use `urllib.request` with no external dependencies, matching the project constraint (no `requests`, no `openai` package). The tests follow the same pattern -- `unittest` only, no pytest, no external fixtures.
- **Vault access:** `on_demand.py` and `engine.py` both go through `vault.py` for load/save with atomic writes. Consistent.
- **Lazy imports:** Heavy modules (`rss_poller`, `transcript_fetcher`, `whisper_client`) are imported inside functions rather than at module level, keeping CLI startup fast. The tests correctly avoid importing these heavy modules.
- **Strategy pattern in `transcript_fetcher.py`:** The dispatch table + ordered fallback chain is clean and extensible. Adding a new transcript source is one function + one dict entry.

### Intentional improvement: tests test behavior, not implementation

The test suite correctly tests `_build_prompt()` output content (marker strings) rather than mocking internal state. The `classify_show_style` test patches `call_openai` to capture the prompt, which is the right level of abstraction -- it verifies the prompt content without hitting the network. This is a pattern worth preserving.

## Future-Proofing Considerations

### Style list is defined in 4+ places

The seven valid styles appear as:
1. `_build_prompt()` if/elif branches (summarizer.py:230-367)
2. `classify_show_style()` valid_styles set (summarizer.py:498)
3. `_TRANSCRIPT_LIMITS` dict keys (summarizer.py:376)
4. Argparse `choices` lists (summarizer.py:574, 592)
5. Test constants (test_summarizer.py:253)

Adding an 8th style requires touching all five locations. A `VALID_STYLES` tuple at module level (referenced by the set, the argparse choices, and the tests) would reduce this to two places: the tuple and the `_build_prompt()` branch. Not urgent at 7 styles, but this is where an 8th style addition would produce a subtle bug (forgetting argparse choices or the classification prompt, exactly what this session's cleanup fixed).

### `_SHOW_EXTRA_INSTRUCTIONS` substring matching could collide

**Where:** `summarizer.py:124-167`, matched via `key in show_lower` at line 218.

If a future show title contains "peter attia" as a substring of a longer name, or a show like "VOM Radio International" is added, the first-match `next()` returns the first collision. With 6 entries this is fine; at 20+ entries it becomes fragile. The `next()` + generator pattern also means only the first matching instruction applies -- if a show somehow matched two keys, the second is silently dropped.

**Not actionable now** -- just worth knowing when adding entries.

### Vault race condition under concurrent `on_demand.run()` calls

**Where:** `on_demand.py:436-442` (read-modify-write on episodes.json).

This is already documented in the architecture memory file. The file-lock dedup in `main()` (lines 557-565) only guards against duplicate CLI invocations for the same episode number -- it does not protect against two different episodes being processed simultaneously. The `vault.save_vault` atomic write prevents corruption but not lost updates (last writer wins). At current scale (single nightly run + occasional on-demand) this is fine. If parallel processing is ever added, this needs a proper file lock or move to SQLite.

## Performance Notes

### Transcript truncation is character-based, not token-based

**Where:** `summarizer.py:376-385`, `summarizer.py:453-458`.

The `_TRANSCRIPT_LIMITS` and the `high_quality` 40,000-character expansion work well in practice (4 chars ~= 1 token is a reasonable heuristic). No issue here -- just noting that the comment on line 373 ("4 chars ~ 1 token") is slightly optimistic for dense scientific text with long technical terms, but the margin is generous enough that it doesn't matter.

### `_build_multipart()` in `whisper_client.py` uses MD5 for boundary generation

**Where:** `whisper_client.py:131`.

This is not a security concern (the boundary just needs to be unique within the request body, not cryptographically secure). MD5 is fine here. Just noting it because some linters flag MD5 usage.

## What's Done Well

### Test structure is exemplary for this codebase

- **Behavioral focus:** Tests verify prompt content and matching behavior, not internal data structures. This means refactoring `_build_prompt()` internals won't break tests as long as the output semantics stay the same.
- **Boundary testing:** The show_notes tests explicitly check the 200-char boundary (200 = no injection, 201 = injection). The episode number tests check `#31` vs `#312` non-collision. These are the exact edge cases that cause production bugs.
- **No network calls:** Every test that would hit OpenAI is properly mocked. The tests run in 16ms for 105 tests -- fast enough to run on every change.
- **Test naming:** Every test name reads as a specification: `test_hash_number_does_not_match_different_number`, `test_extended_depth_works_across_all_styles`. This is documentation that runs.

### Cleanup was surgically correct

The three summarizer.py fixes (docstring, argparse choices, classification prompt) all addressed the same root cause: two new styles (`meateater`, `orvis_fly_fishing`) were added to the prompt builder and transcript limits but not propagated to all the places that enumerate styles. The cleanup caught all three manifestations. The regression test (`TestParseArgsStyleChoices`) ensures this class of bug is caught going forward.

### Archive of one-time scripts

Moving 6 completed backlog scripts to `scripts/archive/` is good hygiene. These scripts are preserved for reference but won't confuse anyone looking at the active codebase.

### `show_notes` as infallible fallback

The transcript_fetcher design where `show_notes()` returns `tuple[str, str]` (never None) and is always appended as the last strategy is a robust pattern. The `fetch()` function literally cannot return empty -- it has a safety net after the loop and the loop itself guarantees `show_notes` runs. This defensive layering is worth preserving.

---

*Review by: Claude Opus 4.6, 2026-03-27*
*All 105 tests passing (32 summarizer + 73 episode_utils) in 0.016s*
