#!/usr/bin/env python3
"""
add_feed.py — Add, remove, or update a podcast feed via CLI or agent call.

Handles Telegram intents: "add podcast X" / "remove podcast X" / "set Y to one-off".

Usage:
    python3 add_feed.py --add --title "My Podcast" --url "https://feeds.example.com/pod.rss"
    python3 add_feed.py --remove --id "my-podcast"
    python3 add_feed.py --state one-off --id "my-podcast"
    python3 add_feed.py --style deep_science --id "my-podcast"
    python3 add_feed.py --list
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Ensure scripts/ is on sys.path for sibling imports
scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import vault


# ---------------------------------------------------------------------------
# Slug helper — same pattern as health_store.py
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Lowercase, replace non-alphanumeric runs with a single hyphen."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text


# ---------------------------------------------------------------------------
# Shared load/save helpers
# ---------------------------------------------------------------------------

def _load_feeds() -> tuple[dict, Path]:
    feeds_path = vault.get_vault_path("feeds.json")
    return vault.load_vault(feeds_path), feeds_path


def _find_feed(feeds: list[dict], feed_id: str) -> dict | None:
    """Find a feed by exact id or case-insensitive title contains."""
    for feed in feeds:
        if feed.get("id") == feed_id:
            return feed
    needle = feed_id.lower()
    for feed in feeds:
        if needle in feed.get("title", "").lower():
            return feed
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_feed(
    title: str,
    rss_url: str,
    state: str = "active",
    health_tier: str = "never",
    summary_style: str | None = None,
) -> dict:
    """Add a new podcast feed to feeds.json.

    Returns {"status": "added", "id": new_id, "title": title} on success,
    or {"status": "already_exists", "id": existing_id} if the URL is already tracked.
    """
    feeds_data, feeds_path = _load_feeds()
    feeds = feeds_data.get("feeds", [])

    # Check for duplicate RSS URL
    for feed in feeds:
        if feed.get("rss_url") == rss_url:
            return {"status": "already_exists", "id": feed["id"]}

    new_id = _slugify(title)

    # If the slug already exists (different URL), append a short suffix
    existing_ids = {f["id"] for f in feeds}
    if new_id in existing_ids:
        # Append last path segment of URL as disambiguator
        suffix = _slugify(rss_url.rstrip("/").rsplit("/", 1)[-1])[:8]
        new_id = f"{new_id}-{suffix}" if suffix else f"{new_id}-2"

    new_feed: dict = {
        "id": new_id,
        "title": title,
        "rss_url": rss_url,
        "state": state,
        "summary_style": summary_style,
        "health_tier": health_tier,
        "whisper_model": None,
        "transcript_strategy": [],
        "last_checked": None,
        "last_episode_pub_date": None,
        "transcript_strategy_cache": {},
    }

    feeds_data["feeds"].append(new_feed)
    vault.save_vault(feeds_path, feeds_data)

    return {"status": "added", "id": new_id, "title": title}


def remove_feed(feed_id: str) -> dict:
    """Soft-delete a feed by setting its state to "inactive".

    Finds by exact id or case-insensitive title contains.
    Returns {"status": "deactivated", "id": ..., "title": ...} or {"status": "not_found"}.
    """
    feeds_data, feeds_path = _load_feeds()
    feed = _find_feed(feeds_data.get("feeds", []), feed_id)

    if feed is None:
        return {"status": "not_found"}

    feed["state"] = "inactive"
    vault.save_vault(feeds_path, feeds_data)

    return {"status": "deactivated", "id": feed["id"], "title": feed.get("title", "")}


VALID_STYLES = ["deep_science", "long_form_interview", "commentary", "hunting_outdoor", "devotional"]


def set_style(feed_id: str, new_style: str) -> dict:
    """Update the summary_style of a feed.

    Valid styles: deep_science, long_form_interview, commentary, hunting_outdoor, devotional.
    Raises ValueError if feed not found or style not valid.
    Returns {"status": "ok", "feed_id": ..., "title": ..., "summary_style": new_style}.
    """
    if new_style not in VALID_STYLES:
        raise ValueError(f"Invalid style '{new_style}'. Must be one of: {', '.join(VALID_STYLES)}")

    feeds_data, feeds_path = _load_feeds()
    feed = _find_feed(feeds_data.get("feeds", []), feed_id)

    if feed is None:
        raise ValueError(f"Feed not found: {feed_id}")

    feed["summary_style"] = new_style
    vault.save_vault(feeds_path, feeds_data)

    return {"status": "ok", "feed_id": feed["id"], "title": feed.get("title", ""), "summary_style": new_style}


def set_state(feed_id: str, new_state: str) -> dict:
    """Update the state of a feed.

    Valid states: "active", "one-off", "inactive".
    Returns {"status": "updated", "id": ..., "state": new_state} or {"status": "not_found"}
    or {"status": "invalid_state", "message": ...}.
    """
    valid_states = {"active", "one-off", "inactive"}
    if new_state not in valid_states:
        return {
            "status": "invalid_state",
            "message": f"Invalid state '{new_state}'. Must be one of: {', '.join(sorted(valid_states))}",
        }

    feeds_data, feeds_path = _load_feeds()
    feed = _find_feed(feeds_data.get("feeds", []), feed_id)

    if feed is None:
        return {"status": "not_found"}

    feed["state"] = new_state
    vault.save_vault(feeds_path, feeds_data)

    return {"status": "updated", "id": feed["id"], "state": new_state}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_feed_table(feeds: list[dict]) -> None:
    if not feeds:
        print("No feeds found.")
        return

    id_w = max(len(f.get("id", "")) for f in feeds)
    title_w = max(len(f.get("title", "")) for f in feeds)
    state_w = max(len(f.get("state", "")) for f in feeds)
    tier_w = max(len(f.get("health_tier", "")) for f in feeds)

    # Column headers
    id_w = max(id_w, 2)
    title_w = max(title_w, 5)
    state_w = max(state_w, 5)
    tier_w = max(tier_w, 11)

    header = f"{'ID':<{id_w}}  {'Title':<{title_w}}  {'State':<{state_w}}  {'Health Tier':<{tier_w}}"
    print(header)
    print("-" * len(header))
    for feed in feeds:
        print(
            f"{feed.get('id', ''):<{id_w}}  "
            f"{feed.get('title', ''):<{title_w}}  "
            f"{feed.get('state', ''):<{state_w}}  "
            f"{feed.get('health_tier', ''):<{tier_w}}"
        )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Add, remove, or update a podcast feed.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 add_feed.py --add --title "My Podcast" --url "https://feeds.example.com/pod.rss"
  python3 add_feed.py --remove --id "my-podcast"
  python3 add_feed.py --state one-off --id "my-podcast"
  python3 add_feed.py --style deep_science --id "my-podcast"
  python3 add_feed.py --list
        """,
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--add", action="store_true", help="Add a new feed.")
    action.add_argument("--remove", action="store_true", help="Deactivate a feed (soft delete).")
    action.add_argument("--state", metavar="STATE", help="Set feed state (active/one-off/inactive).")
    action.add_argument("--list", action="store_true", help="List all feeds.")
    action.add_argument(
        "--style",
        metavar="STYLE",
        help=(
            "Set feed summary_style. Valid values: "
            + ", ".join(VALID_STYLES)
        ),
    )

    parser.add_argument("--title", help="Feed title (required with --add).")
    parser.add_argument("--url", help="RSS URL (required with --add).")
    parser.add_argument("--id", dest="feed_id", help="Feed ID or title substring (required with --remove/--state).")
    parser.add_argument("--health-tier", default="never", help="Health tier (default: never).")
    parser.add_argument("--summary-style", default=None, help="Summary style (optional).")

    args = parser.parse_args()

    if args.list:
        feeds_data, _ = _load_feeds()
        _print_feed_table(feeds_data.get("feeds", []))
        return

    if args.add:
        if not args.title or not args.url:
            parser.error("--add requires --title and --url")
        result = add_feed(
            title=args.title,
            rss_url=args.url,
            health_tier=args.health_tier,
            summary_style=args.summary_style,
        )
        if result["status"] == "added":
            print(f"Added: {result['title']} (id={result['id']})")
        elif result["status"] == "already_exists":
            print(f"Already exists: id={result['id']}")
        return

    if args.remove:
        if not args.feed_id:
            parser.error("--remove requires --id")
        result = remove_feed(args.feed_id)
        if result["status"] == "deactivated":
            print(f"Deactivated: {result['title']} (id={result['id']})")
        elif result["status"] == "not_found":
            print(f"Not found: {args.feed_id}", file=sys.stderr)
            sys.exit(1)
        return

    if args.state:
        if not args.feed_id:
            parser.error("--state requires --id")
        result = set_state(args.feed_id, args.state)
        if result["status"] == "updated":
            print(f"Updated: id={result['id']} → state={result['state']}")
        elif result["status"] == "not_found":
            print(f"Not found: {args.feed_id}", file=sys.stderr)
            sys.exit(1)
        elif result["status"] == "invalid_state":
            print(f"Error: {result['message']}", file=sys.stderr)
            sys.exit(1)
        return

    if args.style:
        if not args.feed_id:
            parser.error("--style requires --id")
        import json
        try:
            result = set_style(args.feed_id, args.style)
        except ValueError as exc:
            print(json.dumps({"status": "error", "message": str(exc)}))
            sys.exit(1)
        print(json.dumps(result))
        return


if __name__ == "__main__":
    main()
