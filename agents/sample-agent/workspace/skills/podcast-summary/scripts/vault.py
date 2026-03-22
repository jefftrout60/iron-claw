#!/usr/bin/env python3
"""
Shared vault utilities for the podcast-summary skill.

Provides atomic JSON load/save and path resolution for podcast_vault/.
Works from both the Mac host and the Docker container — the vault directory
is resolved relative to this file's own location (../podcast_vault/), so the
same relative structure works on both sides of the volume mount.
"""

import json
import os
import sys
from pathlib import Path

# podcast_vault/ is one level up from scripts/
_VAULT_DIR = Path(__file__).parent.parent / "podcast_vault"

# Empty skeletons returned when a file is missing or corrupt
_EMPTY_SCHEMAS = {
    "feeds.json":             {"version": 1, "last_updated": None, "feeds": []},
    "episodes.json":          {"version": 1, "episodes": []},
    "health_knowledge.json":  {"version": 1, "entries": []},
    "processing_status.json": {
        "version": 1,
        "run_date": None,
        "status": "never_run",
        "completed_at": None,
        "episodes_processed": 0,
        "shows": [],
        "newsletters_archived": 0,
        "errors": [],
    },
}


def get_vault_path(filename: str) -> Path:
    """Return absolute path to podcast_vault/{filename}."""
    return _VAULT_DIR / filename


def load_vault(file_path) -> dict:
    """
    Load JSON from file_path and return it as a dict.

    Returns the appropriate empty skeleton on missing file or parse error
    so callers can always treat the result as a valid dict without checking.
    Logs a warning to stderr on any error.
    """
    file_path = Path(file_path)
    filename = file_path.name

    if not file_path.exists():
        return _empty_schema(filename)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[vault] WARNING: {file_path} is corrupt ({e}) — returning empty schema", file=sys.stderr)
        return _empty_schema(filename)
    except OSError as e:
        print(f"[vault] WARNING: cannot read {file_path} ({e}) — returning empty schema", file=sys.stderr)
        return _empty_schema(filename)


def save_vault(file_path, data: dict) -> None:
    """
    Atomically write data as JSON to file_path.

    Writes to file_path + '.tmp' first, then os.replace() so a crash
    mid-write never leaves a corrupt file.  Creates parent directories
    if they do not exist.
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = Path(str(file_path) + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, file_path)


def _empty_schema(filename: str) -> dict:
    """Return a deep copy of the empty skeleton for a given filename."""
    schema = _EMPTY_SCHEMAS.get(filename)
    if schema is None:
        return {}
    # json round-trip is the simplest deep copy with no external deps
    return json.loads(json.dumps(schema))
