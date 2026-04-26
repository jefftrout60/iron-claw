#!/usr/bin/env python3
"""
One-shot migration: health_knowledge.json → podcast_vault/health.db

Run from the repo root:
    /usr/local/bin/python3.13 scripts/migrate_health_knowledge.py --dry-run
    /usr/local/bin/python3.13 scripts/migrate_health_knowledge.py

Idempotent — safe to re-run; already-inserted entries are skipped via
INSERT OR IGNORE on the unique (show, episode_title, date) index.
"""

import argparse
import json
import sys
from pathlib import Path

# Add the skill scripts directory to sys.path so health_db and vault import cleanly
_SCRIPTS_DIR = (
    Path(__file__).parent.parent
    / "agents/sample-agent/workspace/skills/podcast-summary/scripts"
)
sys.path.insert(0, str(_SCRIPTS_DIR))

import health_db  # noqa: E402
import vault      # noqa: E402


def _load_entries() -> list[dict]:
    """Load all entries from health_knowledge.json via vault."""
    vault_path = vault.get_vault_path("health_knowledge.json")
    data = vault.load_vault(vault_path)
    return data.get("entries", [])


def _summarise(entries: list[dict]) -> None:
    """Print a dry-run summary: count, date range, unique shows."""
    if not entries:
        print("No entries found in health_knowledge.json.")
        return

    shows = sorted({e["show"] for e in entries})
    dates = sorted(e["date"] for e in entries)
    print(f"Entries to migrate : {len(entries)}")
    print(f"Date range         : {dates[0]} → {dates[-1]}")
    print(f"Unique shows ({len(shows):>2})  :")
    for show in shows:
        count = sum(1 for e in entries if e["show"] == show)
        print(f"    {count:>3}  {show}")


def _migrate(entries: list[dict]) -> tuple[int, int]:
    """
    Insert all entries into health_knowledge via INSERT OR IGNORE.

    Returns (inserted, skipped) counts.
    """
    conn = health_db.get_connection()
    inserted = 0
    skipped = 0

    for entry in entries:
        topics_json = json.dumps(entry.get("topics") or [])
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO health_knowledge
                (id, show, episode_title, episode_number, date,
                 source, source_quality, topics, summary, tagged_by)
            VALUES
                (:id, :show, :episode_title, :episode_number, :date,
                 :source, :source_quality, :topics, :summary, :tagged_by)
            """,
            {
                "id":             entry["id"],
                "show":           entry["show"],
                "episode_title":  entry["episode_title"],
                "episode_number": entry.get("episode_number"),
                "date":           entry["date"],
                "source":         entry["source"],
                "source_quality": entry.get("source_quality"),
                "topics":         topics_json,
                "summary":        entry["summary"],
                "tagged_by":      entry.get("tagged_by"),
            },
        )
        if cursor.rowcount == 1:
            inserted += 1
        else:
            skipped += 1

    conn.commit()

    conn.close()

    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate health_knowledge.json → podcast_vault/health.db"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be migrated without writing to the DB",
    )
    args = parser.parse_args()

    entries = _load_entries()

    if args.dry_run:
        print("=== DRY RUN — no writes ===")
        _summarise(entries)
        return

    inserted, skipped = _migrate(entries)
    print(f"Migration complete: {inserted} inserted, {skipped} skipped")


if __name__ == "__main__":
    main()
