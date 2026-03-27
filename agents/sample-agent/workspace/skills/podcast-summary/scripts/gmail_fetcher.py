#!/usr/bin/env python3
"""
gmail_fetcher.py — Fetch health newsletter emails from Gmail via IMAP.

Connects to Gmail, selects the `health-newsletters` label folder, fetches
UNSEEN messages, parses them into newsletter dicts, and moves them to Trash.

Primary entry point:
    fetch_newsletters(imap_email, app_password) -> list[dict]

Newsletter dict schema:
    {
        "subject":      str,
        "from_addr":    str,   # full From header
        "sender_slug":  str,   # slugified sender domain (e.g. "peterattia")
        "date":         str,   # ISO 8601
        "body":         str,   # plain text body (teaser-fetched if applicable)
        "content_type": str,   # "full" or "teaser_only"
        "raw_size":     int,   # len(body)
    }

Usage (CLI check mode):
    python3 gmail_fetcher.py --check
    Counts UNSEEN messages in health-newsletters label. Reads credentials from
    agents/sample-agent/.env keys GMAIL_IMAP_EMAIL and GMAIL_IMAP_APP_PASSWORD.
"""

from __future__ import annotations

import argparse
import imaplib
import re
import sys
import urllib.request
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMAP_HOST = "imap.gmail.com"
LABEL_FOLDER = "health-newsletters"
TEASER_THRESHOLD = 500  # chars; bodies shorter than this with a URL get expanded
FETCH_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (podcast-summary-newsletter/1.0)"


# ---------------------------------------------------------------------------
# HTML / text utilities
# ---------------------------------------------------------------------------


def _strip_html(html: str) -> str:
    """Remove HTML tags and decode common entities from text."""
    text = re.sub(r"<[^>]+>", "", html or "")
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# URL fetch helper
# ---------------------------------------------------------------------------


def _fetch_full_content(url: str) -> str:
    """Fetch a URL and return its text content, stripping HTML. Returns "" on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read()
        try:
            html = raw.decode("utf-8", errors="replace")
        except Exception:
            html = raw.decode("latin-1", errors="replace")
        return _strip_html(html)
    except Exception as exc:
        print(f"[gmail_fetcher] _fetch_full_content failed for {url}: {exc}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


def _parse_date(date_header: str) -> str:
    """Parse an email Date header to ISO 8601. Falls back to now on failure."""
    try:
        return parsedate_to_datetime(date_header).isoformat()
    except Exception:
        return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Sender slug
# ---------------------------------------------------------------------------


def _sender_slug(from_addr: str) -> str:
    """
    Extract domain from a From header and return its first label as a slug.

    E.g. "Newsletter <news@peterattia.com>" -> "peterattia"
         "hello@morningbrew.com"             -> "morningbrew"
    """
    # Pull the email address from angle brackets if present
    match = re.search(r"@([\w.\-]+)", from_addr)
    if not match:
        return re.sub(r"[^\w]", "", from_addr.lower())[:32]
    domain = match.group(1).lower()
    # Use the second-level domain label (strip TLD and subdomains)
    parts = domain.rstrip(".").split(".")
    if len(parts) >= 2:
        return parts[-2]
    return parts[0]


# ---------------------------------------------------------------------------
# Email body extraction
# ---------------------------------------------------------------------------


def _extract_body(msg) -> tuple[str, str]:
    """
    Walk email MIME parts and return (body_text, mime_type_used).

    Prefers text/plain; falls back to stripping text/html.
    Returns ("", "none") if nothing useful is found.
    """
    plain: str = ""
    html: str = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not plain:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    plain = payload.decode(charset, errors="replace")
            elif ct == "text/html" and not html:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
    else:
        ct = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ct == "text/plain":
                plain = text
            elif ct == "text/html":
                html = text

    if plain:
        return plain.strip(), "text/plain"
    if html:
        return _strip_html(html), "text/html"
    return "", "none"


# ---------------------------------------------------------------------------
# IMAP folder selection (handles quoting quirks)
# ---------------------------------------------------------------------------


def _select_folder(imap: imaplib.IMAP4_SSL, folder: str) -> bool:
    """
    Try selecting the IMAP folder. Gmail sometimes needs the name double-quoted.
    Returns True on success.
    """
    # Attempt 1: plain name
    status, _ = imap.select(folder)
    if status == "OK":
        return True

    # Attempt 2: double-quoted name (required when folder name contains hyphens on some servers)
    status, _ = imap.select(f'"{folder}"')
    if status == "OK":
        return True

    return False


# ---------------------------------------------------------------------------
# Move email to Trash
# ---------------------------------------------------------------------------


def _move_to_trash(imap: imaplib.IMAP4_SSL, msg_id: bytes) -> None:
    """Move a message to Gmail Trash and remove it from the inbox.

    Gmail's IMAP MOVE from a label folder only removes that label — it does NOT
    remove the INBOX label, so the message stays visible in the inbox.  After
    moving to Trash we explicitly strip the Inbox label using Gmail's
    X-GM-LABELS extension to make the message disappear from the inbox.
    """
    try:
        imap.uid("MOVE", msg_id, "[Gmail]/Trash")
    except Exception:
        # Fallback: mark Deleted and expunge (uses UID, not sequence number)
        imap.uid("STORE", msg_id, "+FLAGS", "\\Deleted")
        imap.expunge()
        return

    # Explicitly remove the Inbox label so the message no longer appears in
    # the inbox view (Gmail MOVE from a label folder does not do this).
    try:
        imap.uid("STORE", msg_id, "-X-GM-LABELS", "\\Inbox")
    except Exception:
        pass  # Not critical — message is in Trash, inbox removal is best-effort


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_newsletters(imap_email: str, app_password: str) -> list[dict]:
    """
    Connect to Gmail IMAP, fetch UNSEEN messages from health-newsletters label,
    parse each into a newsletter dict, move them to Trash, and return the list.

    Never raises — returns [] on any error.
    """
    newsletters: list[dict] = []

    try:
        print(f"[gmail_fetcher] Connecting to {IMAP_HOST} as {imap_email}")
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(imap_email, app_password)
        print("[gmail_fetcher] Login successful")

        if not _select_folder(imap, LABEL_FOLDER):
            print(
                f"[gmail_fetcher] ERROR: could not select folder '{LABEL_FOLDER}'",
                file=sys.stderr,
            )
            imap.logout()
            return []

        # Search for ALL messages using UIDs (newsletters arrive pre-read via
        # MS365 transfer).  Must use uid("SEARCH") so that the IDs returned
        # are UIDs, consistent with the uid("FETCH") / uid("MOVE") calls below.
        status, data = imap.uid("SEARCH", None, "ALL")
        if status != "OK":
            print(f"[gmail_fetcher] SEARCH failed: {data}", file=sys.stderr)
            imap.logout()
            return []

        msg_ids = data[0].split() if data[0] else []
        print(f"[gmail_fetcher] Found {len(msg_ids)} message(s)")

        for msg_id in msg_ids:
            try:
                newsletters.append(_process_message(imap, msg_id))
            except Exception as exc:
                print(
                    f"[gmail_fetcher] ERROR processing message {msg_id}: {exc}",
                    file=sys.stderr,
                )
                # Still try to trash the message so it doesn't loop forever
                try:
                    _move_to_trash(imap, msg_id)
                except Exception:
                    pass

        imap.logout()
        print(f"[gmail_fetcher] Done. {len(newsletters)} newsletter(s) fetched.")

    except Exception as exc:
        print(f"[gmail_fetcher] FATAL: {exc}", file=sys.stderr)
        return []

    return newsletters


# ---------------------------------------------------------------------------
# Per-message processing (extracted so fetch_newsletters stays readable)
# ---------------------------------------------------------------------------


def _process_message(imap: imaplib.IMAP4_SSL, msg_id: bytes) -> dict:
    """Fetch, parse, and trash a single message. Returns a newsletter dict."""
    status, raw_data = imap.uid("FETCH", msg_id, "(RFC822)")
    if status != "OK" or not raw_data:
        raise ValueError(f"fetch status={status}")

    # Gmail IMAP sometimes returns a mix of tuples and bare bytes (e.g. b')').
    # Find the first tuple element — that's the (response_code, message_bytes) pair.
    message_bytes = None
    for part in raw_data:
        if isinstance(part, tuple) and len(part) >= 2:
            message_bytes = part[1]
            break
    if not message_bytes:
        raise ValueError(f"no message data in fetch response for msg {msg_id}")
    msg = message_from_bytes(message_bytes)

    subject = str(make_header(decode_header(msg.get("Subject", "") or "")))
    from_addr = str(make_header(decode_header(msg.get("From", "") or "")))
    date_str = _parse_date(msg.get("Date", ""))
    sender_slug = _sender_slug(from_addr)

    body, _ = _extract_body(msg)

    # Teaser detection: short body that contains a URL → try to fetch full content
    url_match = re.search(r"https?://\S+", body)
    if len(body) < TEASER_THRESHOLD and url_match:
        url = url_match.group(0).rstrip(".,)")  # strip common trailing punctuation
        print(f"[gmail_fetcher] Teaser detected for '{subject}' — fetching {url}")
        full = _fetch_full_content(url)
        if full:
            body = full
            content_type = "full"
        else:
            content_type = "teaser_only"
    else:
        content_type = "full"

    newsletter = {
        "subject": subject,
        "from_addr": from_addr,
        "sender_slug": sender_slug,
        "date": date_str,
        "body": body,
        "content_type": content_type,
        "raw_size": len(body),
    }

    print(
        f"[gmail_fetcher] Processed: '{subject}' from {sender_slug} "
        f"({newsletter['raw_size']} chars, {content_type})"
    )

    _move_to_trash(imap, msg_id)

    return newsletter


# ---------------------------------------------------------------------------
# .env loading — walk up from __file__ to find repo root via CLAUDE.md
# ---------------------------------------------------------------------------


def _load_env() -> dict[str, str]:
    """
    Walk up from this file's directory to find the ironclaw repo root
    (identified by CLAUDE.md), then load agents/sample-agent/.env.
    """
    current = Path(__file__).resolve().parent
    for candidate in [current, *current.parents]:
        if (candidate / "CLAUDE.md").exists():
            env_path = candidate / "agents" / "sample-agent" / ".env"
            break
    else:
        print("[gmail_fetcher] WARNING: could not find repo root (CLAUDE.md)", file=sys.stderr)
        return {}

    env: dict[str, str] = {}
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        print(f"[gmail_fetcher] WARNING: .env not found at {env_path}", file=sys.stderr)
    return env


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gmail IMAP fetcher for health-newsletters label."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Count UNSEEN messages in health-newsletters (no fetch/move/mark).",
    )
    args = parser.parse_args()

    if args.check:
        env = _load_env()
        imap_email = env.get("GMAIL_IMAP_EMAIL", "")
        app_password = env.get("GMAIL_IMAP_APP_PASSWORD", "")

        if not imap_email or not app_password:
            print(
                "[gmail_fetcher] ERROR: GMAIL_IMAP_EMAIL or GMAIL_IMAP_APP_PASSWORD "
                "not set in agents/sample-agent/.env",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            imap = imaplib.IMAP4_SSL(IMAP_HOST)
            imap.login(imap_email, app_password)

            if not _select_folder(imap, LABEL_FOLDER):
                print(
                    f"[gmail_fetcher] ERROR: could not select folder '{LABEL_FOLDER}'",
                    file=sys.stderr,
                )
                imap.logout()
                sys.exit(1)

            status, data = imap.search(None, "ALL")
            count = len(data[0].split()) if status == "OK" and data[0] else 0
            imap.logout()

            print(f"[gmail_fetcher] Messages in '{LABEL_FOLDER}': {count}")
        except Exception as exc:
            print(f"[gmail_fetcher] ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
