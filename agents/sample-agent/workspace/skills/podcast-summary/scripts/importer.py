#!/usr/bin/env python3
"""
OPML importer for the podcast-summary skill.

Parses an OPML file and populates podcast_vault/feeds.json with feed entries.
Merge-safe: existing entries are never overwritten — only new feeds are added.

Usage:
    python3 importer.py <path-to-opml>
    python3 importer.py <path-to-opml> --dry-run
    python3 importer.py <path-to-opml> --agent other-agent
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

# Resolve vault.py relative to this script so imports work regardless of cwd.
_SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPTS_DIR))
from vault import load_vault, save_vault, get_vault_path  # noqa: E402


def slugify(title: str) -> str:
    """
    Convert a show title to a stable kebab-case ID.

    Apostrophes and curly quotes are removed (not replaced with hyphens) so
    "Member's" → "members" rather than "member-s".  All other non-alphanumeric
    runs become a single hyphen.  Result is lowercased and stripped of
    leading/trailing hyphens.
    """
    slug = title.lower()
    # Remove apostrophes and curly quotes so contractions stay joined
    slug = re.sub(r"['\u2018\u2019]", "", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def parse_opml(opml_path: Path) -> list[dict]:
    """
    Parse an OPML file and return a list of RSS feed dicts.

    Each dict has keys: title, rss_url.
    Only <outline type="rss"> elements are included.
    """
    try:
        tree = ET.parse(opml_path)
    except ET.ParseError as e:
        print(f"ERROR: Cannot parse OPML file: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"ERROR: Cannot read OPML file: {e}", file=sys.stderr)
        sys.exit(1)

    root = tree.getroot()
    feeds = []

    # OPML structure: <opml><body><outline ...><outline type="rss" .../></outline></body></opml>
    # Walk all elements — outlines may be at any nesting depth.
    for elem in root.iter("outline"):
        if elem.get("type", "").lower() != "rss":
            continue
        title = elem.get("text") or elem.get("title", "")
        rss_url = elem.get("xmlUrl", "")
        if not title or not rss_url:
            continue
        feeds.append({"title": title.strip(), "rss_url": rss_url.strip()})

    return feeds


def make_feed_entry(title: str, rss_url: str) -> dict:
    """Build a new feed entry with all required schema fields."""
    return {
        "id": slugify(title),
        "title": title,
        "rss_url": rss_url,
        "state": "active",
        "summary_style": None,
        "health_tier": None,
        "whisper_model": None,
        "transcript_strategy": [],
        "transcript_strategy_last_tested": None,
        "last_checked": None,
        "last_episode_guid": None,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }


def run_import(opml_path: Path, feeds_path: Path, dry_run: bool) -> None:
    """Main import logic — parse OPML, merge into feeds.json."""
    parsed_feeds = parse_opml(opml_path)
    if not parsed_feeds:
        print("No RSS feeds found in OPML file.")
        return

    vault = load_vault(feeds_path)
    existing_ids = {f["id"] for f in vault.get("feeds", [])}

    to_add = []
    to_skip = []

    for item in parsed_feeds:
        candidate_id = slugify(item["title"])
        if candidate_id in existing_ids:
            to_skip.append((item["title"], candidate_id))
        else:
            to_add.append(make_feed_entry(item["title"], item["rss_url"]))
            existing_ids.add(candidate_id)  # guard against duplicates within OPML

    # Print results table
    for title, feed_id in to_skip:
        print(f"WOULD SKIP : {title} ({feed_id})" if dry_run else f"SKIP       : {title} ({feed_id})")
    for entry in to_add:
        print(f"WOULD ADD  : {entry['title']} ({entry['id']})" if dry_run else f"ADD        : {entry['title']} ({entry['id']})")

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Summary: {len(to_add)} to add, {len(to_skip)} already present.")

    if dry_run:
        print("[DRY RUN] No changes written.")
        return

    if not to_add:
        print("Nothing new to write.")
        return

    vault["feeds"] = vault.get("feeds", []) + to_add
    vault["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_vault(feeds_path, vault)
    print(f"Wrote {len(to_add)} new feeds to {feeds_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import RSS feeds from an OPML file into podcast_vault/feeds.json"
    )
    parser.add_argument("opml_file", help="Path to the OPML file to import")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be added/skipped without writing anything",
    )
    parser.add_argument(
        "--agent",
        default="sample-agent",
        help="Agent name for locating the correct workspace (default: sample-agent)",
    )
    args = parser.parse_args()

    opml_path = Path(args.opml_file)
    if not opml_path.exists():
        print(f"ERROR: OPML file not found: {opml_path}", file=sys.stderr)
        sys.exit(1)

    feeds_path = get_vault_path("feeds.json")
    run_import(opml_path, feeds_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
