#!/usr/bin/env python3
"""
engine.py — Podcast Summary Engine coordinator.

Phase 1: load env → load vault → poll feeds → log findings → (dry-run) print summary.
Phase 2: transcript acquisition, summarization, email digest.
Phase 3: health knowledge store integration, newsletter ingestion.

Usage:
    python3 engine.py --dry-run
    python3 engine.py --dry-run --agent sample-agent
    python3 engine.py --no-email
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure scripts/ is on sys.path for sibling imports
scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

# ---------------------------------------------------------------------------
# Path helpers — resolve repo root and env file regardless of cwd
# ---------------------------------------------------------------------------

def find_repo_root() -> Path:
    """Walk up from this file's directory to find the ironclaw repo root.

    Identifies the root by the presence of CLAUDE.md, which only exists
    at the top of the ironclaw repo.
    """
    current = Path(__file__).resolve().parent
    for candidate in [current, *current.parents]:
        if (candidate / "CLAUDE.md").exists():
            return candidate
    raise FileNotFoundError(
        "Cannot locate ironclaw repo root (no CLAUDE.md found walking up from "
        f"{Path(__file__).resolve()})"
    )


def get_env_path(agent_name: str = "sample-agent") -> Path:
    """Return absolute path to agents/{agent_name}/.env."""
    return find_repo_root() / "agents" / agent_name / ".env"


# ---------------------------------------------------------------------------
# .env parser — manual, no external deps
# ---------------------------------------------------------------------------

def load_env(env_path: Path) -> dict:
    """Parse a KEY=VALUE .env file and return a dict.

    Handles:
    - Blank lines and comment lines starting with #
    - Values quoted with single or double quotes (outer quotes stripped)
    - Missing file returns empty dict without error
    """
    env: dict = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


# ---------------------------------------------------------------------------
# Vault loading
# ---------------------------------------------------------------------------

def _get_vault_module():
    """Import vault.py from the scripts/ directory adjacent to this file."""
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import vault
    return vault


def load_vault_data() -> tuple[dict, dict]:
    """Load feeds.json and episodes.json from the podcast_vault.

    Returns (feeds_data, episodes_data) dicts.
    Delegates to vault.py load_vault() for atomic-safe reads and empty
    schema fallback on first run.
    """
    v = _get_vault_module()
    feeds_path = v.get_vault_path("feeds.json")
    episodes_path = v.get_vault_path("episodes.json")
    return v.load_vault(feeds_path), v.load_vault(episodes_path)


# ---------------------------------------------------------------------------
# Status writer
# ---------------------------------------------------------------------------

def write_status(status_data: dict) -> None:
    """Atomically write processing_status.json to the vault."""
    v = _get_vault_module()
    status_path = v.get_vault_path("processing_status.json")
    v.save_vault(status_path, status_data)


# ---------------------------------------------------------------------------
# Core: check_new_episodes
# ---------------------------------------------------------------------------

def check_new_episodes(
    feeds_data: dict,
    episodes_data: dict,
    dry_run: bool = False,
) -> list[dict]:
    """Poll all active feeds and return a list of new episode dicts.

    For each active feed:
    1. Calls rss_poller.poll(feed) — wrapped in try/except so one bad
       feed never kills the whole run.
    2. Filters out episodes already present in episodes.json.
    3. Updates feed["last_checked"] to now (ISO 8601).
    4. Updates feed["last_episode_pub_date"] to the newest episode's
       pub_date if new episodes were found.

    In dry-run mode prints findings to stdout but does NOT write feeds.json.
    In live mode writes the updated feeds.json after all feeds are checked.

    Returns the accumulated list of new episode dicts (oldest-first per feed).
    """
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import rss_poller

    v = _get_vault_module()

    feeds = feeds_data.get("feeds", [])
    existing_episode_ids = {ep["id"] for ep in episodes_data.get("episodes", [])}

    active_feeds = [f for f in feeds if f.get("state") in ("active", "one-off")]
    inactive_feeds = [f for f in feeds if f.get("state") not in ("active", "one-off")]

    if dry_run:
        print(f"=== Podcast Summary Engine — DRY RUN ===")
        print(f"Checking {len(active_feeds)} active feeds...\n")

    all_new_episodes: list[dict] = []
    error_count = 0
    total_new = 0
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    for feed in active_feeds:
        feed_id = feed.get("id", "unknown")
        feed_title = feed.get("title", feed_id)

        # Ensure last_episode_pub_date field exists in the feed dict
        if "last_episode_pub_date" not in feed:
            feed["last_episode_pub_date"] = None

        try:
            episodes = rss_poller.poll(feed)
        except Exception as exc:
            error_count += 1
            rss_url = feed.get("rss_url", "")
            if dry_run:
                print(f"[ERROR] {feed_title}: {exc}")
                if rss_url:
                    print(f"        URL: {rss_url}")
            else:
                print(
                    f"[engine] ERROR polling {feed_title}: {exc}",
                    file=sys.stderr,
                )
            # Still update last_checked so we record that we tried
            feed["last_checked"] = now_iso
            continue

        # Filter episodes already in the cache
        new_episodes = [ep for ep in episodes if ep["id"] not in existing_episode_ids]

        # one-off feeds: take only the single newest episode, then flip to inactive
        if feed.get("state") == "one-off":
            if new_episodes:
                newest_ep = max(new_episodes, key=lambda ep: ep.get("pub_date", ""))
                new_episodes = [newest_ep]
            if not dry_run:
                feed["state"] = "inactive"
                print(f"[engine] one-off complete — {feed_title} set to inactive")

        # Update feed timestamps
        feed["last_checked"] = now_iso
        if new_episodes:
            # newest episode is the one with the latest pub_date
            newest = max(new_episodes, key=lambda ep: ep.get("pub_date", ""))
            feed["last_episode_pub_date"] = newest["pub_date"]

        if dry_run:
            n = len(new_episodes)
            if n == 0:
                # Only show "up to date" for feeds with no errors — suppress
                # zero-result feeds to keep output readable (print nothing)
                pass
            elif n == 1:
                print(f"[DRY RUN] {feed_title}: 1 new episode")
                ep = new_episodes[0]
                print(f'  - "{ep["title"]}" ({ep["pub_date"][:10]})')
            else:
                print(f"[DRY RUN] {feed_title}: {n} new episodes")
                for ep in new_episodes:
                    print(f'  - "{ep["title"]}" ({ep["pub_date"][:10]})')

        all_new_episodes.extend(new_episodes)
        total_new += len(new_episodes)

    # Print inactive feeds in dry-run for visibility
    if dry_run:
        for feed in inactive_feeds:
            state = feed.get("state", "inactive")
            print(f"[SKIP] {feed.get('title', feed.get('id', '?'))} (state={state})")

    # Write updated feeds.json only in live mode
    if not dry_run:
        feeds_data["last_updated"] = now_iso
        feeds_path = v.get_vault_path("feeds.json")
        v.save_vault(feeds_path, feeds_data)

    if dry_run:
        print()
        print("=== Summary ===")
        print(f"Active feeds checked: {len(active_feeds)}")
        print(f"New episodes found:   {total_new}")
        print(f"Feeds with errors:    {error_count}")
        print("[DRY RUN] No changes written.")

    return all_new_episodes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_episode_number(title: str) -> str:
    """Return a normalised episode number string from a title, or '' if not found.

    Matches patterns like '#312', 'Ep 312', 'Ep. 312', 'Episode 312' (case-insensitive).
    Always returns the number in '#NNN' form so callers get a consistent format.
    """
    import re
    # #NNN — e.g. "#312", "#1024"
    m = re.search(r"#(\d+)", title)
    if m:
        return f"#{m.group(1)}"
    # Ep / Ep. / Episode followed by a number
    m = re.search(r"\bEp(?:isode)?\.?\s+(\d+)", title, re.IGNORECASE)
    if m:
        return f"#{m.group(1)}"
    return ""


# ---------------------------------------------------------------------------
# Phase 2: process_episodes
# ---------------------------------------------------------------------------

def process_episodes(
    new_episodes: list[dict],
    feeds_data: dict,
    episodes_data: dict,
    env: dict,
    no_email: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Fetch transcripts, summarize, persist, and optionally email the digest.

    Imported lazily to keep --dry-run fast (no whisper/openai imports on startup).

    Returns (processed_episodes, errors) where errors is a list of
    {"episode_id": ..., "error": ...} dicts.
    """
    import transcript_fetcher
    import summarizer
    import digest_emailer

    v = _get_vault_module()
    episodes_path = v.get_vault_path("episodes.json")
    feeds_path = v.get_vault_path("feeds.json")

    api_key = env.get("OPENAI_API_KEY", "")
    model = env.get("PODCAST_SUMMARY_MODEL", "gpt-4o-mini")

    feeds_by_id = {feed["id"]: feed for feed in feeds_data.get("feeds", [])}

    processed: list[dict] = []
    errors: list[dict] = []
    total = len(new_episodes)

    for i, episode in enumerate(new_episodes, start=1):
        show_id = episode.get("show_id", "unknown")
        title = episode.get("title", "unknown")
        feed = feeds_by_id.get(show_id, {})

        print(f'[engine] Processing {i}/{total}: "{title}" ({show_id})')

        try:
            transcript, source_quality = transcript_fetcher.fetch(episode, feed)

            # Build show-notes topic map — only when the transcript isn't the show notes itself
            show_notes_text = ""
            if source_quality != "show_notes":
                raw_notes = episode.get("full_notes") or episode.get("description") or ""
                notes_clean = transcript_fetcher.strip_html(raw_notes).strip()
                if len(notes_clean) > 200:
                    show_notes_text = notes_clean[:1500]

            # Auto-classify show style if not yet set
            if feed.get("summary_style") is None:
                style = summarizer.classify_show_style(
                    feed.get("title", show_id),
                    feed.get("description", ""),
                    api_key,
                    model,
                )
                feed["summary_style"] = style
                # Persist the classification immediately so a crash doesn't re-classify
                v.save_vault(feeds_path, feeds_data)

            summary = summarizer.summarize(
                episode,
                transcript,
                feed.get("summary_style"),
                "extended",
                api_key,
                model,
                source_quality=source_quality,
                summary_paragraphs=feed.get("summary_paragraphs", 0),
                show_notes=show_notes_text,
            )

            enriched = {
                **episode,
                "show_name": feed.get("title", show_id),
                "show_id": show_id,
                "source_quality": source_quality,
                "summary": summary,
                "summary_style": feed.get("summary_style"),
                "processed_at": datetime.now(tz=timezone.utc).isoformat(),
            }

            episodes_data["episodes"].append(enriched)
            v.save_vault(episodes_path, episodes_data)

            processed.append(enriched)
            print(f'[engine] \u2713 "{title}" \u2014 {source_quality}')

            # Health store — only for feeds tagged as health content
            if feed.get("health_tier") in ("always", "sometimes"):
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
                            "raw_transcript": transcript,
                        },
                        api_key,
                        model,
                    )
                except Exception as hs_exc:
                    print(
                        f"[engine] WARNING: health_store failed for \"{title}\": {hs_exc}",
                        file=sys.stderr,
                    )

        except Exception as exc:
            errors.append({"episode_id": episode.get("id", title), "error": str(exc)})
            print(f'[engine] \u2717 "{title}" \u2014 error: {exc}', file=sys.stderr)

    return processed, errors


# ---------------------------------------------------------------------------
# Phase 3: process_newsletters
# ---------------------------------------------------------------------------

def _newsletter_show_name(sender_slug: str) -> str:
    """Expand a sender slug to a human-readable newsletter title."""
    if "attia" in sender_slug:
        return "Peter Attia Newsletter"
    if "foundmyfitness" in sender_slug or "rhonda" in sender_slug:
        return "FoundMyFitness Newsletter"
    return sender_slug


def process_newsletters(env: dict) -> tuple[int, list[str]]:
    """Fetch, summarize, and archive health newsletters from Gmail.

    Returns (count_processed, [show_names]) for use in the digest footer.
    Requires GMAIL_IMAP_EMAIL and GMAIL_IMAP_APP_PASSWORD in env.
    """
    import gmail_fetcher
    import summarizer
    import health_store

    imap_email = env.get("GMAIL_IMAP_EMAIL", "")
    imap_password = env.get("GMAIL_IMAP_APP_PASSWORD", "")
    if not imap_email or not imap_password:
        print(
            "[engine] WARNING: GMAIL_IMAP_EMAIL or GMAIL_IMAP_APP_PASSWORD not set"
            " — skipping newsletter ingestion",
            file=sys.stderr,
        )
        return 0, []

    api_key = env.get("OPENAI_API_KEY", "")
    model = env.get("PODCAST_SUMMARY_MODEL", "gpt-4o-mini")

    newsletters = gmail_fetcher.fetch_newsletters(imap_email, imap_password)

    count = 0
    show_names: list[str] = []

    for nl in newsletters:
        try:
            summary = summarizer.summarize(
                {"title": nl["subject"]},
                nl["body"],
                "deep_science",
                "extended",
                api_key,
                model,
            )

            show_name = _newsletter_show_name(nl["sender_slug"])

            stored = health_store.append_entry(
                {
                    "show": show_name,
                    "episode_title": nl["subject"],
                    "episode_number": "",
                    "date": nl["date"][:10],
                    "source": "newsletter",
                    "source_quality": "newsletter_" + nl["content_type"],
                    "summary": summary,
                    "tagged_by": "auto",
                },
                api_key,
                model,
            )

            if stored is not None:
                count += 1
                show_names.append(show_name)
            print(f'[engine] \u2713 newsletter archived: "{nl["subject"]}" ({show_name})')

        except Exception as exc:
            print(
                f"[engine] WARNING: newsletter processing failed for"
                f" \"{nl.get('subject', '?')}\": {exc}",
                file=sys.stderr,
            )

    return count, show_names


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Podcast Summary Engine — nightly coordinator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 engine.py --dry-run
  python3 engine.py --dry-run --agent sample-agent
  python3 engine.py --no-email
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Poll feeds, print findings, write nothing.",
    )
    parser.add_argument(
        "--episode",
        metavar="ID",
        help="Process a single episode by ID (not yet implemented — TODO: Task 4.1).",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Process transcripts and summaries but skip sending the email digest.",
    )
    parser.add_argument(
        "--agent",
        metavar="NAME",
        default="sample-agent",
        help="Agent name used to locate .env (default: sample-agent).",
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # 1. Load environment
    # ------------------------------------------------------------------
    env_path = get_env_path(args.agent)
    env = load_env(env_path)

    # ------------------------------------------------------------------
    # 2. Load vault
    # ------------------------------------------------------------------
    feeds_data, episodes_data = load_vault_data()

    # ------------------------------------------------------------------
    # 3. Check new episodes
    # ------------------------------------------------------------------
    # TODO: Task 4.1 — add real --episode support here
    new_episodes = check_new_episodes(
        feeds_data,
        episodes_data,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        return

    # ------------------------------------------------------------------
    # 4. Process episodes (transcript → summarize → persist)
    # ------------------------------------------------------------------
    processed, errors = process_episodes(
        new_episodes,
        feeds_data,
        episodes_data,
        env,
        no_email=args.no_email,
    )

    # ------------------------------------------------------------------
    # 4b. Ingest newsletters (Gmail → summarize → health store)
    # ------------------------------------------------------------------
    newsletters_count, newsletter_names = process_newsletters(env)

    # Send a single digest email after all processing is complete (episodes +
    # newsletters). Newsletter counts are included in the footer when present.
    if processed and not args.no_email:
        to_email = env.get("PODCAST_DIGEST_TO_EMAIL", "")
        if to_email:
            import digest_emailer
            try:
                digest_emailer.send_digest(
                    processed,
                    to_email=to_email,
                    from_email=env.get("SMTP_FROM_EMAIL"),
                    smtp_password=env.get("GMAIL_APP_PASSWORD"),
                    newsletter_count=newsletters_count,
                    newsletter_names=newsletter_names,
                )
            except Exception as exc:
                print(f"[engine] WARNING: digest email failed: {exc}", file=sys.stderr)
        else:
            print("[engine] WARNING: PODCAST_DIGEST_TO_EMAIL not set — skipping email", file=sys.stderr)

    # ------------------------------------------------------------------
    # 5. Prune episodes.json (30d summaries, 90d metadata)
    # ------------------------------------------------------------------
    v = _get_vault_module()
    episodes_path = v.get_vault_path("episodes.json")
    episodes_data = v.load_vault(episodes_path)
    episodes_data, stripped, removed = v.prune_episodes(episodes_data)
    if stripped or removed:
        v.save_vault(episodes_path, episodes_data)
        print(f"[engine] Pruned episodes.json: {stripped} summaries stripped, {removed} episodes removed.")

    show_ids = list({ep.get("show_id", "") for ep in processed})
    status = {
        "version": 1,
        "run_date": datetime.now(tz=timezone.utc).date().isoformat(),
        "status": "complete",
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        "episodes_processed": len(processed),
        "shows": show_ids,
        "newsletters_archived": newsletters_count,
        "errors": errors,
    }
    write_status(status)

    print(
        f"[engine] Done. {len(processed)} episode(s) processed, "
        f"{newsletters_count} newsletter(s) archived, "
        f"{len(errors)} error(s).",
    )


if __name__ == "__main__":
    main()
