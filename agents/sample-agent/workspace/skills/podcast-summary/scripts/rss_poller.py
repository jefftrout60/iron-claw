#!/usr/bin/env python3
"""
RSS/Atom feed poller for the podcast-summary skill.

Provides poll(feed_dict) -> list[dict] returning new episode dicts for
a given feed entry from feeds.json.  Uses Python stdlib only — no feedparser,
no requests, no BeautifulSoup.

Usage (CLI test mode):
    python3 rss_poller.py "https://feeds.megaphone.fm/hubermanlab"
    python3 rss_poller.py '{"id":"test","rss_url":"https://...","last_episode_guid":null}'
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# ---------------------------------------------------------------------------
# XML namespace map used across all podcast feeds
# ---------------------------------------------------------------------------
NS = {
    "itunes":  "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "podcast": "https://podcastindex.org/namespace/1.0",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media":   "http://search.yahoo.com/mrss/",
}

# Atom feed root tag
_ATOM_FEED_TAG = "{http://www.w3.org/2005/Atom}feed"
_ATOM_NS = "http://www.w3.org/2005/Atom"

# HTTP request timeout in seconds
_HTTP_TIMEOUT = 30

# Fallback window when last_episode_pub_date is absent and last_episode_guid
# is also absent — return episodes from the last N days on first run.
_FIRST_RUN_DAYS = 7


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def poll(feed_dict: dict) -> list:
    """
    Fetch and parse the RSS/Atom feed described by feed_dict.

    Returns a list of new episode dicts ordered oldest-first.
    "New" means pub_date is strictly after feed_dict["last_episode_pub_date"]
    (preferred), or after feed_dict["last_episode_guid"] lookup, or — on a
    true first run (both absent/None) — all episodes in the feed.

    Raises on unrecoverable HTTP or XML errors.
    """
    url = feed_dict["rss_url"]
    xml_bytes = _fetch_url(url)
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"XML parse failed for {url}: {exc}") from exc

    if root.tag == _ATOM_FEED_TAG:
        episodes = _parse_atom(root, feed_dict)
    else:
        episodes = _parse_rss(root, feed_dict)

    cutoff = _resolve_cutoff(feed_dict)
    if cutoff is None:
        # True first run — return everything from the feed
        return episodes

    new_episodes = [ep for ep in episodes if _parse_iso(ep["pub_date"]) > cutoff]

    skip_patterns = feed_dict.get("skip_title_contains", [])
    if skip_patterns:
        new_episodes = [
            ep for ep in new_episodes
            if not any(p.lower() in ep.get("title", "").lower() for p in skip_patterns)
        ]

    return new_episodes


# ---------------------------------------------------------------------------
# HTTP fetching
# ---------------------------------------------------------------------------

def _fetch_url(url: str) -> bytes:
    """Fetch URL and return raw bytes.  Raises on non-200 status."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PodcastSummaryBot/1.0"},
    )
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        code = resp.getcode()
        if code != 200:
            raise IOError(f"HTTP {code} fetching {url}")
        return resp.read()


# ---------------------------------------------------------------------------
# RSS 2.0 parser
# ---------------------------------------------------------------------------

def _parse_rss(root: ET.Element, feed_dict: dict) -> list:
    """Parse an RSS 2.0 tree and return all episodes with audio URLs."""
    channel = root.find("channel")
    if channel is None:
        return []

    episodes = []
    for item in channel.findall("item"):
        ep = _extract_rss_episode(item, feed_dict)
        if ep is None:
            continue
        episodes.append(ep)

    return episodes


def _extract_rss_episode(item: ET.Element, feed_dict: dict) -> dict | None:
    """Extract one episode dict from an RSS <item>.  Returns None if no audio."""
    enc = item.find("enclosure")
    if enc is None:
        title = _text(item, "title") or "(no title)"
        return None  # no audio — skip silently

    audio_url = enc.get("url", "").strip()
    if not audio_url:
        return None

    raw_guid = _text(item, "guid") or audio_url
    feed_id = feed_dict.get("id", "unknown")
    ep_id = f"{feed_id}_{hashlib.md5(raw_guid.encode()).hexdigest()[:8]}"

    pub_date_raw = _text(item, "pubDate") or ""
    pub_date_iso = _parse_pub_date(pub_date_raw)

    description = strip_html(_text(item, "description"))
    full_notes_el = item.find(f"{{{NS['content']}}}encoded")
    full_notes = (full_notes_el.text or "").strip() if full_notes_el is not None else None

    transcript_el = item.find(f"{{{NS['podcast']}}}transcript")
    transcript_url = transcript_el.get("url") if transcript_el is not None else None
    transcript_type = transcript_el.get("type") if transcript_el is not None else None

    duration_raw = _ns_text(item, "itunes", "duration")
    duration_seconds = _parse_duration(duration_raw)

    return {
        "id": ep_id,
        "show_id": feed_dict.get("id", "unknown"),
        "title": _text(item, "title") or "",
        "pub_date": pub_date_iso,
        "audio_url": audio_url,
        "duration_seconds": duration_seconds,
        "description": description,
        "full_notes": full_notes,
        "transcript_tag_url": transcript_url,
        "transcript_tag_type": transcript_type,
        "guid": raw_guid,
    }


# ---------------------------------------------------------------------------
# Atom parser
# ---------------------------------------------------------------------------

def _parse_atom(root: ET.Element, feed_dict: dict) -> list:
    """Parse an Atom feed tree and return all episodes with audio links."""
    episodes = []
    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        ep = _extract_atom_episode(entry, feed_dict)
        if ep is None:
            continue
        episodes.append(ep)
    return episodes


def _extract_atom_episode(entry: ET.Element, feed_dict: dict) -> dict | None:
    """Extract one episode dict from an Atom <entry>.  Returns None if no audio."""
    # Find audio link: <link rel="enclosure" href="...">
    audio_url = None
    for link in entry.findall(f"{{{_ATOM_NS}}}link"):
        if link.get("rel") == "enclosure":
            audio_url = link.get("href", "").strip()
            break

    if not audio_url:
        title = _atom_text(entry, "title") or "(no title)"
        return None  # no audio — skip silently

    id_el = entry.find(f"{{{_ATOM_NS}}}id")
    raw_guid = (id_el.text or "").strip() if id_el is not None else audio_url
    feed_id = feed_dict.get("id", "unknown")
    ep_id = f"{feed_id}_{hashlib.md5(raw_guid.encode()).hexdigest()[:8]}"

    # Prefer <updated>, fall back to <published>
    date_raw = _atom_text(entry, "updated") or _atom_text(entry, "published") or ""
    pub_date_iso = _parse_pub_date_atom(date_raw)

    description = strip_html(_atom_text(entry, "summary"))
    content_el = entry.find(f"{{{_ATOM_NS}}}content")
    full_notes = (content_el.text or "").strip() if content_el is not None else None

    transcript_el = entry.find(f"{{{NS['podcast']}}}transcript")
    transcript_url = transcript_el.get("url") if transcript_el is not None else None
    transcript_type = transcript_el.get("type") if transcript_el is not None else None

    duration_raw = _ns_text(entry, "itunes", "duration")
    duration_seconds = _parse_duration(duration_raw)

    return {
        "id": ep_id,
        "show_id": feed_dict.get("id", "unknown"),
        "title": _atom_text(entry, "title") or "",
        "pub_date": pub_date_iso,
        "audio_url": audio_url,
        "duration_seconds": duration_seconds,
        "description": description,
        "full_notes": full_notes,
        "transcript_tag_url": transcript_url,
        "transcript_tag_type": transcript_type,
        "guid": raw_guid,
    }


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

def _parse_pub_date(raw: str) -> str:
    """Parse RFC 2822 pubDate (RSS 2.0) to ISO 8601 string.  Falls back to epoch."""
    if not raw:
        return _epoch_iso()
    try:
        dt = parsedate_to_datetime(raw)
        return dt.isoformat()
    except Exception:
        return _epoch_iso()


def _parse_pub_date_atom(raw: str) -> str:
    """Parse an Atom date string (RFC 3339 / ISO 8601) to ISO 8601 string."""
    if not raw:
        return _epoch_iso()
    # Atom dates are already ISO 8601; normalise slightly for consistency
    try:
        # Replace 'Z' suffix for fromisoformat compatibility on Python <3.11
        normalised = raw.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        return dt.isoformat()
    except Exception:
        # Fall back to email.utils for unusual formats
        return _parse_pub_date(raw)


def _parse_iso(iso_str: str) -> datetime:
    """Parse an ISO 8601 string back to a timezone-aware datetime."""
    try:
        normalised = iso_str.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _epoch_iso() -> str:
    return datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat()


def _resolve_cutoff(feed_dict: dict) -> datetime | None:
    """
    Determine the pub_date cutoff for "new" episodes.

    Priority:
    1. feed_dict["last_episode_pub_date"] — explicit ISO date stored by engine
    2. feed_dict["last_episode_guid"] is None → true first run, return None
       (caller will include all episodes)
    3. last_episode_guid present but no pub_date → use 7-day fallback
    """
    last_pub = feed_dict.get("last_episode_pub_date")
    if last_pub:
        return _parse_iso(last_pub)

    last_guid = feed_dict.get("last_episode_guid")
    if last_guid is None:
        # True first run — no cutoff, return all
        return None

    # guid known but no pub_date stored — return episodes from last N days
    from datetime import timedelta
    return datetime.now(tz=timezone.utc) - timedelta(days=_FIRST_RUN_DAYS)


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

def _parse_duration(raw: str | None) -> int | None:
    """
    Convert itunes:duration to integer seconds.

    Accepts:
        "HH:MM:SS"  → 3-part
        "MM:SS"     → 2-part
        "3600"      → plain integer string
        None / ""   → returns None
    """
    if not raw:
        return None
    raw = raw.strip()
    if ":" in raw:
        parts = raw.split(":")
        try:
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + int(s)
            elif len(parts) == 2:
                m, s = parts
                return int(m) * 60 + int(s)
        except ValueError:
            return None
    else:
        try:
            return int(raw)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

def strip_html(text: str | None) -> str:
    """Strip HTML tags and return plain text."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


# ---------------------------------------------------------------------------
# ElementTree convenience helpers
# ---------------------------------------------------------------------------

def _text(el: ET.Element, tag: str) -> str | None:
    """Return text of a direct child element, or None."""
    child = el.find(tag)
    if child is None:
        return None
    return (child.text or "").strip() or None


def _atom_text(el: ET.Element, tag: str) -> str | None:
    """Return text of an Atom-namespaced child element, or None."""
    child = el.find(f"{{{_ATOM_NS}}}{tag}")
    if child is None:
        return None
    return (child.text or "").strip() or None


def _ns_text(el: ET.Element, ns_key: str, tag: str) -> str | None:
    """Return text of a namespaced child element, or None."""
    child = el.find(f"{{{NS[ns_key]}}}{tag}")
    if child is None:
        return None
    return (child.text or "").strip() or None


# ---------------------------------------------------------------------------
# CLI mode — manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 rss_poller.py <rss_url_or_feed_json>", file=sys.stderr)
        sys.exit(1)

    arg = sys.argv[1].strip()

    # Accept either a raw URL or a JSON feed dict
    if arg.startswith("{"):
        try:
            feed = json.loads(arg)
        except json.JSONDecodeError as e:
            print(f"ERROR: invalid JSON feed dict: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Treat the argument as a plain RSS URL; synthesise a minimal feed dict
        feed = {
            "id": "cli-test",
            "rss_url": arg,
            "last_episode_guid": None,
            "last_episode_pub_date": None,
        }

    print(f"Polling: {feed['rss_url']}", file=sys.stderr)

    try:
        episodes = poll(feed)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if not episodes:
        print("No new episodes found.")
    else:
        print(f"Found {len(episodes)} episode(s):\n")
        for ep in episodes:
            dur = ep.get("duration_seconds")
            dur_str = f"  ({dur // 60}m)" if dur else ""
            print(f"  [{ep['pub_date']}]{dur_str}  {ep['title']}")
            if ep.get("audio_url"):
                print(f"    audio: {ep['audio_url']}")
            if ep.get("transcript_tag_url"):
                print(f"    transcript: {ep['transcript_tag_url']} ({ep.get('transcript_tag_type', '?')})")
            print()
