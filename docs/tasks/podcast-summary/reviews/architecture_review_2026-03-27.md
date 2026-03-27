# Architecture Review: Podcast Summary Skill Pipeline
**Date:** 2026-03-27
**Reviewer:** Principal Systems Architect (Claude Code)
**Scope:** `workspace/skills/podcast-summary/scripts/` — all nine pipeline modules
**Context:** Docker container, 512 MB `/tmp` tmpfs, local whisper.cpp on Mac host at `host.docker.internal:18797`, cloud Whisper via OpenAI API, nightly cron via `engine.py`, summaries emailed to Evernote via SMTP.

---

## 1. Executive Summary

The pipeline is well-structured for a single-operator personal tool: clean module boundaries, good fault isolation, and thoughtful fallback chains throughout. Two issues require immediate attention before scale or reliability is increased — an unguarded race condition on `episodes.json` that can corrupt the episode store, and audio downloads that write full files into a 512 MB tmpfs before size is known, creating a realistic OOM/ENOSPC failure path during nightly runs with multiple large episodes. All other findings are minor.

---

## 2. Critical Issues

### C-1 — `episodes.json` Race Condition (no file locking)

**What:** `engine.py` and `on_demand.py` both read, mutate, and write `episodes.json` via `vault.save_vault()`. The cron job and an on-demand user request can execute concurrently. The last writer wins; the earlier write is silently dropped.

**Where:**
- `engine.py:355-356` — `episodes_data["episodes"].append(enriched)` then `v.save_vault(episodes_path, episodes_data)` inside a per-episode loop.
- `on_demand.py:436-442` — full read-modify-write of `episodes_data`.
- `vault.py:67-84` — `save_vault()` uses `os.replace()` (atomic for single writers), but there is no cross-process lock before the read in `load_vault()`.

**Why it matters:** A nightly run processing five episodes writes `episodes.json` five times. If `on_demand.py` reads between two of those writes and then saves, it overwrites the file with a snapshot that is missing one or more episodes just persisted by the engine. The data loss is silent — no error is raised.

**Fix:** Use a filesystem advisory lock (e.g., `fcntl.flock` on a `episodes.json.lock` file) wrapping every `load_vault` → mutate → `save_vault` sequence for `episodes.json`. A context-manager helper in `vault.py` would give all callers the same guard with minimal code change. The `.tmp`+`os.replace()` pattern already present is the right write primitive; the lock just needs to be added around the read-modify-write cycle.

---

### C-2 — Audio Buffered Entirely in RAM Before Size Check (`whisper_client.py`)

**What:** `_download_audio()` reads the entire response body with `resp.read()` into memory before writing to disk (`whisper_client.py:247-250`). The `CHUNK_THRESHOLD_BYTES` guard (20 MB) runs only *after* the file is already written.

**Where:** `whisper_client.py:247`: `data = resp.read()` then `dest.write_bytes(data)` on line 250.

**Why it matters:** A 90-minute podcast episode at 128 kbps is ~80 MB. With a 512 MB tmpfs on `/tmp`, two concurrent large-episode downloads (nightly batch + on-demand) could exhaust the tmpfs. More immediately, the full audio bytes are held in the Python process heap *and* written to tmpfs simultaneously during `write_bytes`, briefly doubling memory pressure. An `MemoryError` or `OSError: [Errno 28] No space left on device` here would fail the episode silently (the exception propagates up, `transcript_fetcher` catches it, and falls back to show notes with no operator alert).

**Fix:** Stream the download directly to disk in chunks (same pattern already used in `transcript_fetcher._download_audio()` at `transcript_fetcher.py:571-580`). `whisper_client._download_audio()` should mirror that loop. The size check can then be done via `audio_path.stat().st_size` after download, exactly as it is today.

---

### C-3 — `tempfile.mktemp()` Race (TOCTOU) in `transcript_fetcher.py`

**What:** `fetch_openai_whisper()` uses the deprecated `tempfile.mktemp()` to generate a filename, then opens that path separately (`transcript_fetcher.py:698`). Between `mktemp()` and the actual `open()`, another process could create the same file.

**Where:** `transcript_fetcher.py:698`: `tmp_audio = tempfile.mktemp(suffix=".mp3", prefix="podcast_dl_")`

**Why it matters:** In the single-operator cron context the probability of collision is very low, but `mktemp()` is documented as unsafe and is a code-quality red flag. On a tmpfs shared with other processes it could produce unexpected overwrites.

**Fix:** Replace with `tempfile.NamedTemporaryFile(suffix=".mp3", prefix="podcast_dl_", delete=False)` and use its `.name`. This is a one-line fix; the existing `finally` cleanup block already handles deletion correctly.

---

## 3. Simplification Opportunities

### S-1 — `.env` Parsing Duplicated Across Five Modules

`engine.py`, `on_demand.py`, `summarizer.py`, `transcript_fetcher.py`, and `whisper_client.py` each contain an almost-identical `_load_env()` / `load_env()` function (check `os.environ` → fall back to `.env` file walk). The logic is nearly identical but not exactly so (e.g., `engine.py`'s version does not check `os.environ` first; the others do).

A single `load_env(agent_name)` in `vault.py` or a new `config.py` module would eliminate four copies and the subtle behavioural divergence. This also removes the `.env` file path from `transcript_fetcher._load_openai_api_key()` which hard-codes `"sample-agent"` at `transcript_fetcher.py:556`.

### S-2 — `_extract_episode_number()` Duplicated in `engine.py` and `on_demand.py`

`engine.py:250-265` and `on_demand.py:478-486` are identical functions. One should import from the other, or both should import from a shared utility module.

### S-3 — `repo_root` Discovery Duplicated Across Six Modules

`_find_repo_root()` (searching for `CLAUDE.md`) appears verbatim in `engine.py`, `on_demand.py`, `summarizer.py`, `transcript_fetcher.py`, and `whisper_client.py`. Same fix as S-1: move to a single shared location.

### S-4 — Strategy Cache Mutates `feed_dict` In-Place Without Persistence

`transcript_fetcher._cache_strategy_result()` writes failure/success data into the in-memory `feed_dict` dict (`transcript_fetcher.py:489`), but that dict is never saved back to `feeds.json` from within `transcript_fetcher`. The strategy failure cache therefore does not survive across runs; a 403 from podscript.ai is re-attempted every nightly run.

Either (a) have `engine.py` / `on_demand.py` call `save_vault(feeds_path, feeds_data)` after `transcript_fetcher.fetch()` returns, or (b) pass a save callback into `fetch()`. Option (a) is simpler given the existing call sites.

---

## 4. Architecture Alignment

### Overall

The three-phase design (`check_new_episodes` → `process_episodes` → `process_newsletters`) in `engine.py` is clean and follows a sensible separation of concerns. The vault abstraction in `vault.py` is the right pattern: all file I/O centralised, atomic writes, and empty-schema fallbacks on first run.

### Transcript Strategy Pattern

The ordered-fallback strategy list (`transcript_strategy` per feed) is a good fit for a heterogeneous feed portfolio. The dispatch table in `transcript_fetcher._STRATEGY_FUNCS` is explicit and easy to extend. The `show_notes` safety net being appended at `transcript_fetcher.py:791-793` is correct defensive design.

### Email Delivery via Subprocess

`digest_emailer.send_digest()` delegates to `send_email.py` via `subprocess.run()` (`digest_emailer.py:214`). This is acceptable given the shared-skill design, but it means email failures surface as a subprocess exit code rather than a Python exception with stack trace. The existing `RuntimeError` re-raise on non-zero exit (`digest_emailer.py:228`) is the right response; callers in `engine.py` and `on_demand.py` correctly catch and log it as a warning without aborting processing.

### Health Store as a Side-Effect

`health_store.append_entry()` is called as a best-effort side-effect inside the main processing loop (`engine.py:363-383`), with its own try/except that never re-raises. This is the correct pattern for an optional enrichment step that should not gate episode persistence.

### `on_demand.py` Lock File Mechanism

The `FileExistsError`-based dedup lock at `on_demand.py:562-566` correctly prevents duplicate in-flight on-demand runs for the same episode number. However, the lock key is derived from only the first 1-4 digit sequence in the query (`on_demand.py:558-559`). A query like `"Philosophize This #173"` and `"Peter Attia #173"` would collide on lock key `173`. In practice this is unlikely to matter (on-demand runs are user-initiated and sequential), but it is worth noting.

---

## 5. Future-Proofing

### F-1 — `episodes.json` Will Become a Performance Bottleneck

Every episode write does a full JSON serialize + fsync of the entire episodes store. At a few hundred episodes this is imperceptible; at tens of thousands it becomes slow. The current design is appropriate for the current scale. When the episode count reaches a point where load/save latency is noticeable, the natural migration is to SQLite (which `vault.py`'s abstraction boundary would make straightforward to swap in).

### F-2 — Transcript Truncation Is a Hard Cliff

`summarizer.py:453-458` truncates transcript content at a character limit before sending to OpenAI. For `whisper_large` sources the limit is raised to 40,000 chars (`summarizer.py:456`), but the truncation is still a hard cut at a character boundary, not a sentence or paragraph boundary. For very long transcripts (3-hour episodes) meaningful content at the end is silently dropped. This is a known limitation of the token-window approach; a chunked-summarization or map-reduce pattern would address it if summary quality on ultra-long episodes becomes a concern.

### F-3 — `_SHOW_EXTRA_INSTRUCTIONS` and Summary Styles Are Hard-Coded

Show-specific prompt injection (`summarizer.py:124-167`) and summary style prompts are hard-coded in `summarizer.py`. This works well for a small fixed feed list but will require code changes for each new show requiring custom behaviour. A data-driven approach (storing extra instructions in `feeds.json` per feed) would make this maintainable without code deploys.

### F-4 — `WHISPER_LARGE_SHOWS` Hard-Coded in `whisper_client.py`

`whisper_client.py:57-64` hard-codes a set of show IDs that should use the large model. This list is not consulted by `transcript_fetcher.py` — model selection is actually driven by the `transcript_strategy` list in `feeds.json`. The `WHISPER_LARGE_SHOWS` constant appears to be vestigial or intended for future use. If it drives no current logic, it should either be removed or its purpose documented.

---

## 6. Performance Notes

### P-1 — Redundant RSS Polls in `on_demand._find_episode_in_feed()`

When the vault lookup misses, `on_demand._find_episode_in_feed()` polls all ranked active feeds, collecting full episode lists into `polled` before beginning any matching (`on_demand.py:195-202`). For a large feed portfolio this means every RSS feed is fetched even if the first one matches. The current implementation at least deduplicates polls (each feed polled once, results cached in `polled`). Given the small feed count typical of a personal setup this is not a problem today, but ranked-early-exit (stop polling once a match is found) would reduce latency for common queries.

---

## 7. What's Done Well

**Atomic writes throughout.** `vault.save_vault()` uses the write-to-`.tmp`-then-`os.replace()` pattern with `fsync` (`vault.py:78-84`). Every JSON file in the vault is safe against mid-write corruption from a crash.

**Zero external Python dependencies.** All HTTP is done with `urllib.request`, JSON with stdlib `json`, XML with `xml.etree.ElementTree`. This is exactly right for a containerised skill that must not require `pip install` at runtime.

**Graceful degradation at every layer.** A feed poll failure never aborts other feeds (`engine.py:168-184`). An episode transcript failure never aborts other episodes (`engine.py:385-387`). A health store failure never aborts the episode (`engine.py:379-383`). An email failure never aborts the run (`engine.py:569-570`). The fallback chain in `transcript_fetcher.fetch()` always returns something.

**Strategy failure caching.** Marking a transcript strategy as `"failed"` for seven days (`transcript_fetcher.py:59`, `496-516`) prevents repeated 403 hammering of third-party transcript sites on every nightly run. The mechanism is well-designed even though the cache is not currently persisted across runs (see S-4).

**Dry-run mode is genuinely safe.** `engine.py` dry-run prints findings and returns without writing any files (`engine.py:229-243`, `535-536`). This makes iterative testing safe.

**`on_demand.py` extended-summary caching.** Storing extended summaries under a separate `summary_extended` key (`on_demand.py:425-431`) while preserving the standard summary is thoughtful — it avoids re-processing and correctly handles the two-depth model without overwriting cheaper cached results.

**HTML email construction avoids XSS.** `digest_emailer._summary_to_html()` calls `html.escape()` before any string interpolation (`digest_emailer.py:50`). Episode titles and show names are all passed through `html_module.escape()` in `_episode_card_html()`. Correct.

**Consistent source quality labelling.** The `source_quality` string flows from `transcript_fetcher` through `summarizer` (where it adjusts prompt detail level) through to `digest_emailer` (where it surfaces as a human-readable label). The semantics are consistent end-to-end.
