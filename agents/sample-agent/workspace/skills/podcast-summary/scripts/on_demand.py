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

    # Search for that number in the title using common patterns
    title_lower = title.lower()
    patterns = [
        rf"#{number}\b",
        rf"\bep(?:isode)?\.?\s+{number}\b",
        rf"\b{number}\b",
    ]
    return any(re.search(p, title_lower, re.IGNORECASE) for p in patterns)


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
    return _match_by_title(query, episodes)


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

    # Prefer feeds whose title overlaps with query words
    def _feed_relevance(feed: dict) -> int:
        feed_words = set(re.sub(r"[^a-z0-9 ]", " ", feed.get("title", "").lower()).split())
        return len(feed_words & query_words)

    ranked = sorted(active_feeds, key=_feed_relevance, reverse=True)

    for feed in ranked:
        try:
            # Poll the feed with no cutoff (first-run style) to get all episodes
            first_run_feed = {**feed, "last_episode_pub_date": None, "last_episode_guid": None}
            episodes = rss_poller.poll(first_run_feed)
        except Exception as exc:
            print(f"[on_demand] WARNING: could not poll {feed.get('title', feed.get('id'))}: {exc}", file=sys.stderr)
            continue

        match = _find_episode_in_vault(query, episodes)
        if match:
            return match, feed

    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(query: str, agent_name: str = "sample-agent", depth: str = "standard") -> dict:
    """Process a specific episode on demand.

    Returns {"status": "ok", "message": summary_text, ...} or
            {"status": "error", "message": reason}.

    After successfully summarizing, emails the result immediately via
    digest_emailer — per SKILL.md: summaries are NEVER returned inline.
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

    try:
        transcript, source_quality = transcript_fetcher.fetch(episode, feed)
    except Exception as exc:
        return {"status": "error", "message": f"Transcript fetch failed: {exc}"}

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

    try:
        summary = summarizer.summarize(
            episode,
            transcript,
            feed.get("summary_style"),
            depth,
            api_key,
            model,
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

    # 6. Email result immediately (never return inline per SKILL.md)
    _email_result(enriched, env, digest_emailer)

    return {
        "status": "ok",
        "message": summary,
        "source_quality": source_quality,
        "episode_title": episode.get("title", ""),
        "show_name": feed.get("title", show_id),
        "cached": False,
    }


def _email_result(enriched: dict, env: dict, digest_emailer) -> None:
    """Send the on-demand summary email. Logs warning on failure — never raises."""
    to_email = env.get("PODCAST_DIGEST_TO_EMAIL", "")
    if not to_email:
        print("[on_demand] WARNING: PODCAST_DIGEST_TO_EMAIL not set — skipping email", file=sys.stderr)
        return

    try:
        digest_emailer.send_digest(
            [enriched],
            to_email=to_email,
            from_email=env.get("SMTP_FROM_EMAIL"),
            smtp_password=env.get("GMAIL_APP_PASSWORD"),
        )
    except Exception as exc:
        print(f"[on_demand] WARNING: email failed: {exc}", file=sys.stderr)


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
    args = parser.parse_args()

    result = run(args.query, agent_name=args.agent, depth=args.depth)

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
