from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

# vault.py is in the same directory
sys.path.insert(0, str(Path(__file__).parent))
from vault import get_vault_path, load_vault, save_vault

_KNOWLEDGE_FILE = "health_knowledge.json"


def slugify(text: str) -> str:
    """Lowercase, replace non-alphanumeric runs with a single hyphen."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text


def extract_topics(summary: str, api_key: str, model: str) -> list[str]:
    """
    Call OpenAI Chat Completions to extract 3-8 health/science topic tags.
    Returns [] on any failure — never raises.
    """
    if not api_key:
        print("[health_store] WARNING: no api_key provided, skipping topic extraction", file=sys.stderr)
        return []

    prompt = (
        "Extract 3-8 specific health and science topic tags from the following podcast/newsletter summary.\n"
        'Return ONLY a JSON array of lowercase strings. Examples: ["apob", "cardiovascular", "sleep", "statins", "vo2max", "zone 2 training"]\n'
        f"Summary: {summary[:2000]}"
    )

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    for attempt in range(3):
        try:
            if attempt:
                time.sleep(2 ** attempt)  # 2s, 4s backoff
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = (body["choices"][0]["message"]["content"] or "").strip()
            topics = json.loads(content)
            if isinstance(topics, list):
                return [str(t).lower() for t in topics]
            print("[health_store] WARNING: topic extraction returned non-list JSON", file=sys.stderr)
            return []
        except Exception as exc:
            if attempt == 2:
                print(f"[health_store] WARNING: topic extraction failed ({exc})", file=sys.stderr)
    return []


def append_entry(entry_data: dict, api_key: str = "", model: str = "gpt-4o-mini") -> dict:
    """
    Build a complete entry, append it to health_knowledge.json, and return it.
    Topics are extracted via the OpenAI API when api_key is provided.
    """
    topics = extract_topics(entry_data["summary"], api_key, model)

    source = entry_data["source"]
    show_slug = slugify(entry_data["show"])
    date_part = entry_data["date"][:10]
    content_hash = hashlib.md5(entry_data["summary"].encode()).hexdigest()[:8]
    entry_id = f"{source}-{show_slug}-{date_part}-{content_hash}"

    entry: dict = {
        "id": entry_id,
        "show": entry_data["show"],
        "episode_title": entry_data.get("episode_title", ""),
        "episode_number": entry_data.get("episode_number", ""),
        "date": entry_data["date"],
        "source": source,
        "source_quality": entry_data.get("source_quality", ""),
        "topics": topics,
        "summary": entry_data["summary"],
        "tagged_by": entry_data.get("tagged_by", "auto"),
    }

    vault_path = get_vault_path(_KNOWLEDGE_FILE)
    data = load_vault(vault_path)

    # Dedup: skip if an entry with the same show + title + date already exists.
    # Keyed on stable fields so re-runs with different LLM summaries don't duplicate.
    new_key = (entry["show"], entry["episode_title"], entry["date"])
    for existing in data.get("entries", []):
        if (existing.get("show"), existing.get("episode_title"), existing.get("date")) == new_key:
            print(
                f"[health_store] Skipping duplicate: {entry['episode_title']!r} ({entry['date']})",
                file=sys.stderr,
            )
            return None

    data["entries"].append(entry)
    save_vault(vault_path, data)

    return entry


def load_all() -> list[dict]:
    """Return all entries sorted by date descending (newest first)."""
    vault_path = get_vault_path(_KNOWLEDGE_FILE)
    data = load_vault(vault_path)
    return sorted(data.get("entries", []), key=lambda e: e.get("date", ""), reverse=True)


def find_by_show(show: str) -> list[dict]:
    """Return all entries whose show name contains the given string (case-insensitive), newest first."""
    needle = show.lower()
    matches = [e for e in load_all() if needle in e.get("show", "").lower()]
    return matches  # load_all() already returns sorted


def _cli_test() -> None:
    entries = load_all()
    print(f"health_knowledge.json — {len(entries)} entry(s)")
    if entries:
        print("First entry:")
        print(json.dumps(entries[0], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Health knowledge store CLI")
    parser.add_argument("--test", action="store_true", help="Print current contents of health_knowledge.json")
    args = parser.parse_args()

    if args.test:
        _cli_test()
    else:
        parser.print_help()
