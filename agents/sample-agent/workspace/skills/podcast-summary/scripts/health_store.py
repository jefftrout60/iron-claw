from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

# health_db.py lives in workspace/health/ (one level up from skills/)
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "health"))
import health_db


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
    Build a complete entry, insert it into health.db, and return it.
    Topics are extracted via the OpenAI API when api_key is provided.
    Returns None if a duplicate entry (same show + title + date) already exists.
    """
    topics = extract_topics(entry_data["summary"], api_key, model)
    enrichment_status = 'done' if topics else 'failed'
    topics_text = ' '.join(topics) if topics else ''

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
        "raw_transcript": entry_data.get("raw_transcript"),
    }

    conn = health_db.get_connection()
    cursor = conn.execute(
        """INSERT OR IGNORE INTO health_knowledge
             (id, show, episode_title, episode_number, date, source,
              source_quality, topics, summary, tagged_by, raw_transcript,
              enrichment_status, topics_text)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry["id"],
            entry["show"],
            entry["episode_title"],
            entry["episode_number"],
            entry["date"],
            entry["source"],
            entry["source_quality"],
            json.dumps(entry["topics"]),
            entry["summary"],
            entry["tagged_by"],
            entry["raw_transcript"],
            enrichment_status,
            topics_text,
        ),
    )
    if cursor.rowcount == 0:
        print(
            f"[health_store] Skipping duplicate: {entry['episode_title']!r} ({entry['date']})",
            file=sys.stderr,
        )
        conn.close()
        return None

    conn.commit()
    conn.close()
    return entry

