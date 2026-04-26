#!/usr/bin/env python3
"""
CLI for adding a specific episode to the health knowledge store.

Called by SKILL.md Intent 6:
  python3 health_store_cmd.py --episode-id "{id}" --tagged-by user

The episode-id corresponds to the 'id' field in episodes.json.
"""

import argparse
import os
import sys
from pathlib import Path

# Scripts dir contains vault and health_store; health_db lives in workspace/health/
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "health"))
from vault import get_vault_path, load_vault
import health_store


def main() -> None:
    parser = argparse.ArgumentParser(description="Add an episode to the health knowledge store")
    parser.add_argument("--episode-id", required=True, help="Episode 'id' field from episodes.json")
    parser.add_argument("--tagged-by", default="user", help="Who tagged this episode (default: user)")
    args = parser.parse_args()

    episodes_path = get_vault_path("episodes.json")
    data = load_vault(episodes_path)
    episodes = data.get("episodes", [])

    episode = next((ep for ep in episodes if ep.get("id") == args.episode_id), None)
    if episode is None:
        print(f"[health_store_cmd] Episode not found: {args.episode_id!r}", file=sys.stderr)
        sys.exit(1)

    title = episode.get("title", "")
    summary = episode.get("summary_extended") or episode.get("summary") or ""
    if not summary:
        print(
            f"[health_store_cmd] No summary available for {title!r} — run on_demand.py first",
            file=sys.stderr,
        )
        sys.exit(1)

    entry_data = {
        "show": episode.get("show_title") or episode.get("show") or "",
        "episode_title": title,
        "episode_number": episode.get("episode_number", ""),
        "date": (episode.get("pub_date") or "")[:10],
        "source": "podcast",
        "source_quality": episode.get("source_quality", "show_notes"),
        "summary": summary,
        "tagged_by": args.tagged_by,
    }

    api_key = os.environ.get("OPENAI_API_KEY", "")
    result = health_store.append_entry(entry_data, api_key=api_key, model="gpt-4o-mini")

    if result is None:
        print(f"Already in health store — {title!r} was previously archived.")
    else:
        print(f"Added to health store — {title!r} is now in your health knowledge archive.")


if __name__ == "__main__":
    main()
