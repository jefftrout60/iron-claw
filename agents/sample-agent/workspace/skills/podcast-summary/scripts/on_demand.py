#!/usr/bin/env python3
"""
on_demand.py — Process a specific podcast episode on demand.

Handles the SKILL.md intent: user asks for a specific episode by name, number,
or URL. Looks up the episode in the vault (and polls the relevant feed if not
found), summarizes it if needed, then emails the result immediately via
digest_emailer — never returns the summary inline.

Usage:
    python3 on_demand.py --query "Peter Attia episode 312"
    python3 on_demand.py --query "#224"
    python3 on_demand.py --query "https://example.com/ep312.mp3"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure scripts/ is on sys.path for sibling imports
scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))


# ---------------------------------------------------------------------------
# Path helpers — same pattern as engine.py
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in [current, *current.parents]:
        if (candidate / "CLAUDE.md").exists():
            return candidate
    raise FileNotFoundError(
        "Cannot locate ironclaw repo root (no CLAUDE.md found walking up from "
        f"{Path(__file__).resolve()})"
    )


def _load_env(agent_name: str = "sample-agent") -> dict:
    # Check os.environ first — works inside Docker container where .env file is not accessible
    _KNOWN_KEYS = (
        "OPENAI_API_KEY", "PODCAST_SUMMARY_MODEL", "DIGEST_TO_EMAIL",
        "PODCAST_DIGEST_TO_EMAIL", "PODCAST_EVERNOTE_EMAIL", "SMTP_FROM_EMAIL", "GMAIL_APP_PASSWORD",
    )
    env_from_environ = {k: os.environ[k] for k in _KNOWN_KEYS if k in os.environ}
    if env_from_environ.get("OPENAI_API_KEY"):
        return env_from_environ

    # Fall back to .env file — works on host
    env_path = _find_repo_root() / "agents" / agent_name / ".env"
    env: dict = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                env[key] = value
    except OSError:
        pass
    return env


# ---------------------------------------------------------------------------
# Episode number matching
# ---------------------------------------------------------------------------

def _episode_number_match(query: str, title: str) -> bool:
    """Return True if the episode number in query matches one in title.

    Handles query patterns: "#312", "ep 312", "episode 312", or bare "312".
    Matches the same number patterns in the title.
    """
    # Extract the number from the query
    query_lower = query.lower().strip()

    m = re.search(r"#(\d+)", query_lower)
    if not m:
        m = re.search(r"\bep(?:isode)?\.?\s+(\d+)", query_lower, re.IGNORECASE)
    if not m:
        # Bare number — must be the whole query or a standalone token
        m = re.match(r"^(\d+)$", query_lower)

    if not m:
        return False

    number = m.group(1)

    # Search for that number in the title using common patterns.
    # Prefer explicit episode-number patterns; only fall back to bare word match
    # when the number appears at the START of the title (e.g. "100: ...") to
    # avoid false matches on numbers embedded in episode content (years, $amounts).
    title_lower = title.lower()
    explicit_patterns = [
        rf"#{number}\b",
        rf"\bep(?:isode)?\.?\s+{number}\b",
    ]
    if any(re.search(p, title_lower, re.IGNORECASE) for p in explicit_patterns):
        return True
    # Bare number only matches if it appears at the very start of the title
    # (e.g. "100: How I Built This" or "100 — Tim's story")
    return bool(re.match(rf"^{number}\b", title_lower))


# ---------------------------------------------------------------------------
# Episode search helpers
# ---------------------------------------------------------------------------

def _is_url(query: str) -> bool:
    return query.startswith("http://") or query.startswith("https://")


def _is_episode_number_query(query: str) -> bool:
    q = query.lower().strip()
    return bool(
        re.match(r"^#\d+", q)
        or re.match(r"^ep(?:isode)?\.?\s+\d+", q, re.IGNORECASE)
        or re.match(r"^\d+$", q)
    )


def _match_by_url(query: str, episodes: list[dict]) -> dict | None:
    for ep in episodes:
        if query in (ep.get("audio_url") or "") or query in (ep.get("id") or ""):
            return ep
    return None


def _match_by_number(query: str, episodes: list[dict]) -> dict | None:
    for ep in episodes:
        if _episode_number_match(query, ep.get("title", "")):
            return ep
    return None


def _match_by_title(query: str, episodes: list[dict]) -> dict | None:
    """Fuzzy title match — all query words must appear in the title."""
    words = query.lower().split()
    for ep in episodes:
        title_lower = ep.get("title", "").lower()
        if all(w in title_lower for w in words):
            return ep
    return None


def _find_episode_in_vault(query: str, episodes: list[dict]) -> dict | None:
    if _is_url(query):
        return _match_by_url(query, episodes)
    if _is_episode_number_query(query):
        return _match_by_number(query, episodes)
    # Title match first; fall back to number match for queries like "Show Name #123"
    return _match_by_title(query, episodes) or _match_by_number(query, episodes)


def _find_episode_in_feed(query: str, feeds: list[dict]) -> tuple[dict | None, dict | None]:
    """Poll each active feed's RSS and return (episode, feed) if found.

    To avoid polling every feed, we only poll feeds whose title is mentioned
    in the query (case-insensitive word overlap).  Falls back to polling all
    active feeds when the query gives no feed hint.
    """
    import rss_poller

    query_lower = query.lower()
    query_words = set(re.sub(r"[^a-z0-9 ]", " ", query_lower).split())

    active_feeds = [f for f in feeds if f.get("state") == "active"]

    # "Q&A #N" queries are specific to FoundMyFitness Aliquot — restrict to that feed
    # so Pass 2 number fallback never collides with unrelated feeds (e.g. Eastmans' ep 78)
    if re.search(r"\bq&a\s+#\d+", query_lower):
        fm_feeds = [
            f for f in active_feeds
            if "foundmyfitness" in f.get("id", "").lower()
            or "foundmyfitness" in f.get("title", "").lower()
            or "aliquot" in f.get("title", "").lower()
        ]
        if fm_feeds:
            active_feeds = fm_feeds

    # Prefer feeds whose title overlaps with query words
    def _feed_relevance(feed: dict) -> int:
        feed_words = set(re.sub(r"[^a-z0-9 ]", " ", feed.get("title", "").lower()).split())
        return len(feed_words & query_words)

    ranked = sorted(active_feeds, key=_feed_relevance, reverse=True)

    # Poll all ranked feeds once and cache results.
    polled: list[tuple[dict, list[dict]]] = []
    for feed in ranked:
        try:
            first_run_feed = {**feed, "last_episode_pub_date": None, "last_episode_guid": None}
            episodes = rss_poller.poll(first_run_feed)
            polled.append((feed, episodes))
        except Exception as exc:
            print(f"[on_demand] WARNING: could not poll {feed.get('title', feed.get('id'))}: {exc}", file=sys.stderr)

    if _is_url(query):
        for feed, episodes in polled:
            match = _match_by_url(query, episodes)
            if match:
                return match, feed
        return None, None

    if _is_episode_number_query(query):
        for feed, episodes in polled:
            match = _match_by_number(query, episodes)
            if match:
                return match, feed
        return None, None

    # Descriptive query (e.g. "Philosophize This #173" or "Aliquot #105"):
    # Pass 1 — title match across all feeds.  This correctly handles queries
    # where the show name appears in episode titles (e.g. "Aliquot #105" →
    # FoundMyFitness titles contain "Aliquot").
    for feed, episodes in polled:
        match = _match_by_title(query, episodes)
        if match:
            return match, feed

    # Pass 1b — title match with feed name words and query-only connectors stripped.
    # Handles queries like "Triggernometry The Climate Crisis is a Scam with Ian Plimer"
    # where the show name helps rank the feed but is absent from the episode title,
    # and connector words like "with/featuring/ft" don't appear in titles.
    _QUERY_CONNECTORS = {"with", "featuring", "ft", "guest", "hosted", "by", "episode", "podcast"}
    for feed, episodes in polled:
        feed_words = set(re.sub(r"[^a-z0-9 ]", " ", feed.get("title", "").lower()).split())
        stripped_words = [
            w for w in query.lower().split()
            if w not in feed_words and w not in _QUERY_CONNECTORS
        ]
        if stripped_words and len(stripped_words) < len(query.split()):
            stripped_query = " ".join(stripped_words)
            match = _match_by_title(stripped_query, episodes)
            if match:
                return match, feed

    # Pass 2 — number fallback for shows whose episode titles don't echo the
    # show name (e.g. "Philosophize This #173" → episode titles are just
    # "Episode #173 ...").  Relevance ranking ensures a feed whose title
    # overlaps the query (e.g. "Philosophize This!") is tried before
    # unrelated feeds, preventing cross-show number collisions.
    for feed, episodes in polled:
        match = _match_by_number(query, episodes)
        if match:
            return match, feed

    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    query: str,
    agent_name: str = "sample-agent",
    depth: str = "extended",
    strategy_override: list[str] | None = None,
    save_to_health: bool = False,
    summary_style_override: str | None = None,
) -> dict:
    """Process a specific episode on demand.

    Returns {"status": "ok", "message": summary_text, ...} or
            {"status": "error", "message": reason}.

    After successfully summarizing, emails the result immediately via
    digest_emailer — per SKILL.md: summaries are NEVER returned inline.

    Args:
        query: Episode search query (title, URL, episode number, etc.)
        agent_name: Agent to load .env from.
        depth: "standard" or "extended".
        strategy_override: If provided, replace the feed's transcript_strategy
            with this list (e.g. ["fetch_openai_whisper", "show_notes"]).
            Useful for forcing cloud Whisper without changing feed configs.
        save_to_health: If True, write to health_store regardless of the
            feed's health_tier setting.
        summary_style_override: If provided, use this summary style instead of
            the feed's configured style (e.g. force "deep_science" for a
            science guest on a hunting podcast).
    """
    # 1. Load environment
    env = _load_env(agent_name)
    api_key = env.get("OPENAI_API_KEY", "")
    if not api_key:
        return {"status": "error", "message": "OPENAI_API_KEY is not set — cannot summarize episode."}

    # 2. Load vault data
    import vault
    import transcript_fetcher
    import summarizer
    import digest_emailer

    feeds_path = vault.get_vault_path("feeds.json")
    episodes_path = vault.get_vault_path("episodes.json")
    feeds_data = vault.load_vault(feeds_path)
    episodes_data = vault.load_vault(episodes_path)

    feeds = feeds_data.get("feeds", [])
    episodes = episodes_data.get("episodes", [])

    feeds_by_id = {f["id"]: f for f in feeds}

    # 3. Search for the episode
    episode = _find_episode_in_vault(query, episodes)
    feed: dict = feeds_by_id.get(episode.get("show_id", ""), {}) if episode else {}

    if not episode:
        # Check feeds' RSS for recent episodes not yet in the vault
        episode, feed = _find_episode_in_feed(query, feeds)

    if not episode:
        return {"status": "error", "message": f"No episode found matching: {query}"}

    show_id = episode.get("show_id", "unknown")
    if not feed:
        feed = feeds_by_id.get(show_id, {})

    # 4. Check if already summarized — extended depth gets its own cached key
    if depth == "extended":
        existing_extended = episode.get("summary_extended", "")
        if existing_extended:
            enriched = {
                **episode,
                "show_name": feed.get("title", show_id),
                "summary": existing_extended,
            }
            _email_result(enriched, env, digest_emailer)
            return {
                "status": "ok",
                "message": existing_extended,
                "source_quality": episode.get("source_quality", ""),
                "episode_title": episode.get("title", ""),
                "show_name": feed.get("title", show_id),
                "cached": True,
            }
        # Fall through to re-summarize at extended depth even if standard summary exists
    else:
        existing_summary = episode.get("summary", "")
        if existing_summary:
            enriched = {
                **episode,
                "show_name": feed.get("title", show_id),
            }
            _email_result(enriched, env, digest_emailer)
            return {
                "status": "ok",
                "message": existing_summary,
                "source_quality": episode.get("source_quality", ""),
                "episode_title": episode.get("title", ""),
                "show_name": feed.get("title", show_id),
                "cached": True,
            }

    # 5. Fetch transcript and summarize
    model = env.get("PODCAST_SUMMARY_MODEL", "gpt-4o-mini")

    # Apply strategy override if requested (e.g. force cloud Whisper for backlog)
    fetch_feed = feed
    if strategy_override:
        fetch_feed = {**feed, "transcript_strategy": strategy_override}

    try:
        transcript, source_quality = transcript_fetcher.fetch(episode, fetch_feed)
    except Exception as exc:
        return {"status": "error", "message": f"Transcript fetch failed: {exc}"}

    # Build show-notes topic map — only when the transcript isn't the show notes itself
    show_notes_text = ""
    if source_quality != "show_notes":
        raw_notes = episode.get("full_notes") or episode.get("description") or ""
        notes_clean = transcript_fetcher.strip_html(raw_notes).strip()
        if len(notes_clean) > 200:
            show_notes_text = notes_clean[:1500]

    # Auto-classify show style if missing
    if feed.get("summary_style") is None:
        try:
            style = summarizer.classify_show_style(
                feed.get("title", show_id),
                feed.get("description", ""),
                api_key,
                model,
            )
            feed["summary_style"] = style
            # Persist classification back to feeds.json
            vault.save_vault(feeds_path, feeds_data)
        except Exception as exc:
            print(f"[on_demand] WARNING: style classification failed: {exc}", file=sys.stderr)

    effective_style = summary_style_override or feed.get("summary_style")

    try:
        summary = summarizer.summarize(
            episode,
            transcript,
            effective_style,
            depth,
            api_key,
            model,
            source_quality=source_quality,
            summary_paragraphs=feed.get("summary_paragraphs", 0),
            show_notes=show_notes_text,
        )
    except Exception as exc:
        return {"status": "error", "message": f"Summarization failed: {exc}"}

    enriched = {
        **episode,
        "show_name": feed.get("title", show_id),
        "show_id": show_id,
        "source_quality": source_quality,
        "summary_style": feed.get("summary_style"),
        "processed_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    # Store extended summaries under a separate key so the standard summary is preserved
    if depth == "extended":
        enriched["summary_extended"] = summary
        # Carry forward the existing standard summary if present
        if episode.get("summary"):
            enriched["summary"] = episode["summary"]
        else:
            enriched["summary"] = summary
    else:
        enriched["summary"] = summary

    # Persist to episodes.json — update existing entry if present, else append
    existing_ids = {ep["id"]: i for i, ep in enumerate(episodes_data["episodes"])}
    ep_id = enriched.get("id", "")
    if ep_id in existing_ids:
        episodes_data["episodes"][existing_ids[ep_id]] = enriched
    else:
        episodes_data["episodes"].append(enriched)
    vault.save_vault(episodes_path, episodes_data)

    # 6. Write to health store for health-tagged feeds or when explicitly requested
    if save_to_health or feed.get("health_tier") in ("always", "sometimes"):
        import health_store
        try:
            health_store.append_entry(
                {
                    "show": feed.get("title", show_id),
                    "episode_title": episode.get("title", ""),
                    "episode_number": _extract_episode_number(episode.get("title", "")),
                    "date": episode.get("pub_date", "")[:10],
                    "source": "podcast",
                    "source_quality": source_quality,
                    "summary": summary,
                    "tagged_by": "auto",
                },
                api_key,
                model,
            )
        except Exception as hs_exc:
            print(f"[on_demand] WARNING: health_store failed: {hs_exc}", file=sys.stderr)

    # 7. Email result immediately (never return inline per SKILL.md)
    _email_result(enriched, env, digest_emailer)

    return {
        "status": "ok",
        "message": summary,
        "source_quality": source_quality,
        "episode_title": episode.get("title", ""),
        "show_name": feed.get("title", show_id),
        "cached": False,
    }


def _extract_episode_number(title: str) -> str:
    """Return a normalised episode number string from a title, or '' if not found."""
    m = re.search(r"#(\d+)", title)
    if m:
        return f"#{m.group(1)}"
    m = re.search(r"\bEp(?:isode)?\.?\s+(\d+)", title, re.IGNORECASE)
    if m:
        return f"#{m.group(1)}"
    return ""


def _email_result(enriched: dict, env: dict, digest_emailer) -> None:
    """Send the on-demand summary email. Logs warning on failure — never raises."""
    to_email = env.get("PODCAST_DIGEST_TO_EMAIL", "")
    evernote_email = env.get("PODCAST_EVERNOTE_EMAIL", "")

    if not to_email and not evernote_email:
        print("[on_demand] WARNING: no email destination configured — skipping email", file=sys.stderr)
        return

    smtp_kwargs = dict(
        from_email=env.get("SMTP_FROM_EMAIL"),
        smtp_password=env.get("GMAIL_APP_PASSWORD"),
    )

    if to_email:
        try:
            digest_emailer.send_digest([enriched], to_email=to_email, **smtp_kwargs)
        except Exception as exc:
            print(f"[on_demand] WARNING: email failed: {exc}", file=sys.stderr)

    if evernote_email:
        show = enriched.get("show_name", enriched.get("show", "Podcast"))
        title = enriched.get("episode_title", enriched.get("title", "Episode"))
        evernote_subject = f"{show}: {title} #podcasts"
        try:
            digest_emailer.send_digest(
                [enriched],
                to_email=evernote_email,
                subject_override=evernote_subject,
                **smtp_kwargs,
            )
        except Exception as exc:
            print(f"[on_demand] WARNING: evernote email failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process a specific podcast episode on demand.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 on_demand.py --query "Peter Attia episode 312"
  python3 on_demand.py --query "#224"
  python3 on_demand.py --query "https://example.com/ep312.mp3"
        """,
    )
    parser.add_argument("--query", required=True, help="Episode search query: title, #number, or URL.")
    parser.add_argument(
        "--agent",
        metavar="NAME",
        default="sample-agent",
        help="Agent name used to locate .env (default: sample-agent).",
    )
    parser.add_argument(
        "--depth",
        choices=["standard", "extended"],
        default="standard",
        help="Summary depth: standard (default) or extended for more detail.",
    )
    parser.add_argument(
        "--style",
        metavar="STYLE",
        default=None,
        help="Override summary style (e.g. deep_science, long_form_interview, hunting_outdoor).",
    )
    parser.add_argument(
        "--strategy",
        metavar="STRATEGY",
        nargs="+",
        default=None,
        help="Override transcript strategy (e.g. fetch_openai_whisper show_notes).",
    )
    parser.add_argument(
        "--save-to-health",
        action="store_true",
        default=False,
        help="Save to health_knowledge.json regardless of the feed's health_tier setting.",
    )
    args = parser.parse_args()

    # Dedup: if another on_demand.py is already running for the same episode,
    # exit immediately (exit 0) so the agent gets a fast response.
    # Lock key = episode number extracted from query (digits 1-4 chars).
    _ep_match = re.search(r"\b(\d{1,4})\b", args.query)
    _lock_key = _ep_match.group(1).lstrip("0") or "0" if _ep_match else "noep"
    _lock_path = Path("/tmp/podcast-summary") / f"lock_ep{_lock_key}.lock"
    _lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _lock_path.open("x").close()  # exclusive create — fails if exists
    except FileExistsError:
        print(f"[on_demand] Episode {_lock_key} already queued — exiting.")
        sys.exit(0)

    try:
        result = run(
            args.query,
            agent_name=args.agent,
            depth=args.depth,
            strategy_override=args.strategy,
            save_to_health=args.save_to_health,
            summary_style_override=args.style,
        )
    finally:
        try:
            _lock_path.unlink()
        except OSError:
            pass

    if result["status"] == "ok":
        cached_note = " (cached)" if result.get("cached") else ""
        print(f"[on_demand] OK{cached_note}: {result['episode_title']} — {result['show_name']}")
        print(f"[on_demand] Source quality: {result.get('source_quality', '')}")
        print()
        print(result["message"])
    else:
        print(f"[on_demand] ERROR: {result['message']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
