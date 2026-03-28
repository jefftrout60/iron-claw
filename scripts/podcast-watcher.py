#!/usr/bin/env python3
"""
podcast-watcher.py — Host-side watcher that monitors OpenClaw session JSONL
files for podcast summary requests and fires on_demand.py inside the agent
container via docker exec.

Architecture: the agent gives a short Telegram preview naturally; this watcher
detects the same incoming message and fires the full Whisper transcription +
email pipeline independently.

Usage: python3 scripts/podcast-watcher.py <agent-name> [--poll N]
"""

import argparse
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

POLL_INTERVAL = 5       # seconds between session-dir scans
ON_DEMAND_TIMEOUT = 900 # seconds before giving up on a docker exec call

# Words that, combined with a feed name match, indicate a podcast request
INTENT_TRIGGERS = frozenset({
    "episode", "episodes", "ep.", "summarize", "summarise", "summary",
    "summaries", "podcast", "listen", "transcrib",
})

# Internal messages to skip (learning bridge payloads, heartbeat telemetry)
SKIP_PHRASES = (
    "Evaluate this completed run telemetry",
    "Return compact JSON only",
    "embedded run done",
)

# ── Path resolution (mirrors lib.sh) ─────────────────────────────────────────

def resolve_agent(agent_name: str) -> dict:
    script_dir = Path(__file__).resolve().parent
    ironclaw_root = script_dir.parent
    agent_dir = ironclaw_root / "agents" / agent_name

    if not agent_dir.is_dir():
        raise FileNotFoundError(f"Agent '{agent_name}' not found in agents/")
    conf_path = agent_dir / "agent.conf"
    if not conf_path.exists():
        raise FileNotFoundError(f"Missing agent.conf for '{agent_name}'")

    conf = {}
    for line in conf_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            conf[k.strip()] = v.strip()

    return {
        "agent_name": agent_name,
        "agent_dir": agent_dir,
        "agent_container": conf.get("AGENT_CONTAINER", f"{agent_name}_secure"),
        "agent_log_dir": agent_dir / "logs",
        "agent_sessions": agent_dir / "config-runtime" / "agents" / "main" / "sessions",
        "agent_workspace": agent_dir / "workspace",
    }

# ── Feed keyword extraction ───────────────────────────────────────────────────

# Words to exclude when extracting keywords from feed titles
_TITLE_STOP = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "with",
    "for", "on", "at", "by", "from", "as", "your", "my", "this",
    "its", "all", "are", "was", "has", "had", "let", "ask",
})


def load_feed_keywords(workspace: Path) -> list:
    """
    Load feeds.json and return list of (feed_id, feed_title, keyword_set).
    keyword_set contains lowercase significant words from the title (len >= 3,
    not stop words, not pure digits).
    """
    feeds_path = workspace / "skills" / "podcast-summary" / "podcast_vault" / "feeds.json"
    if not feeds_path.exists():
        return []
    try:
        data = json.loads(feeds_path.read_text())
    except Exception:
        return []

    result = []
    for feed in data.get("feeds", []):
        title = feed.get("title", "")
        fid = feed.get("id", "")
        words = re.findall(r"[a-z0-9]+", title.lower())
        keywords = frozenset(
            w for w in words
            if len(w) >= 3 and w not in _TITLE_STOP and not w.isdigit()
        )
        if keywords:
            result.append((fid, title, keywords))
    return result


def is_podcast_request(text: str, feed_keywords: list) -> tuple:
    """
    Returns (is_podcast: bool, query: str).
    query is the full message text (or URL if one was found).
    """
    lower = text.lower()

    # URL → always treat as a specific-episode request
    url_match = re.search(r"https?://\S+", text)
    if url_match:
        return True, url_match.group(0)

    # Look for episode number patterns as a strong intent signal
    has_ep_number = bool(re.search(
        r"(\bep\.?\s*#?\s*\d+|\bepisode\s+\d+|#\s*\d{2,4})", lower
    ))

    # Tokenize the message for intent trigger matching
    msg_tokens = set(re.findall(r"[a-z]+", lower))
    has_intent = bool(msg_tokens & INTENT_TRIGGERS) or has_ep_number

    if not has_intent:
        return False, ""

    # Check for feed name keyword match
    msg_word_set = set(re.findall(r"[a-z0-9]+", lower))
    for fid, title, keywords in feed_keywords:
        overlap = keywords & msg_word_set
        # Require at least 1 significant keyword match (for single-distinctive-word
        # shows like "huberman", "philosophize", "triggernometry") or 2 for
        # generic-word shows (avoids "thinking" alone matching Just Thinking)
        min_overlap = 1 if any(len(w) >= 7 for w in keywords) else 2
        if len(overlap) >= min_overlap:
            return True, text

    return False, ""

# ── OpenClaw metadata stripping ──────────────────────────────────────────────

def extract_user_text(text: str) -> str:
    """
    OpenClaw prepends Telegram metadata blocks to every user message:

        Conversation info (untrusted metadata):
        ```json
        {...}
        ```

        Sender (untrusted metadata):
        ```json
        {...}
        ```

        <actual user message>

    Split on the ``` fence marker and take everything after the last one.
    If there are no fences, return the text unchanged.
    """
    if "```" not in text:
        return text
    # Split at most into [preamble, block1, sep, block2, ..., actual_message]
    # taking everything after the last ``` fence
    parts = text.split("```")
    last = parts[-1].strip()
    return last if last else text


# ── State management ──────────────────────────────────────────────────────────

def load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except Exception:
            pass
    return {"files": {}, "initialized": False}


def save_state(state_path: Path, state: dict) -> None:
    tmp = state_path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2))
        tmp.rename(state_path)
    except Exception:
        pass  # non-fatal; we'll retry next cycle

# ── Docker exec ───────────────────────────────────────────────────────────────

def fire_on_demand(query: str, container: str, agent_name: str,
                   log: logging.Logger) -> None:
    """Run on_demand.py inside the container. Blocks until complete (runs in thread)."""
    on_demand = (
        "/home/openclaw/.openclaw/workspace/skills/podcast-summary/scripts/on_demand.py"
    )
    cmd = [
        "docker", "exec", container,
        "python3", on_demand,
        "--query", query,
        "--agent", agent_name,
    ]
    log.info("on_demand start: query=%r", query[:100])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=ON_DEMAND_TIMEOUT,
        )
        if result.returncode == 0:
            log.info("on_demand ok. stdout=%r", (result.stdout or "")[:200])
        else:
            log.warning(
                "on_demand exit=%d stdout=%r stderr=%r",
                result.returncode,
                (result.stdout or "")[:300],
                (result.stderr or "")[:200],
            )
    except subprocess.TimeoutExpired:
        log.warning("on_demand timed out after %ds", ON_DEMAND_TIMEOUT)
    except Exception as exc:
        log.error("on_demand error: %s", exc)

# ── Session scan ──────────────────────────────────────────────────────────────

def initialize_offsets(sessions_dir: Path, state: dict,
                        log: logging.Logger) -> None:
    """
    On first run, skip all historical content by setting offsets to current
    end-of-file. Only new messages written after this point will be processed.
    """
    count = 0
    for jsonl_path in sessions_dir.glob("*.jsonl"):
        fname = jsonl_path.name
        if fname not in state["files"]:
            try:
                size = jsonl_path.stat().st_size
                state["files"][fname] = {"offset": size}
                count += 1
            except OSError:
                pass
    state["initialized"] = True
    log.info("Initialized: skipped history in %d existing session files", count)


def scan_sessions(
    sessions_dir: Path,
    state: dict,
    feed_keywords: list,
    container: str,
    agent_name: str,
    log: logging.Logger,
    fired_keys: set,
) -> None:
    if not sessions_dir.exists():
        return

    # Evict state keys for session files that no longer exist on disk
    existing = {p.name for p in sessions_dir.glob("*.jsonl")}
    stale = [k for k in list(state["files"]) if k not in existing]
    for k in stale:
        del state["files"][k]

    for jsonl_path in sorted(sessions_dir.glob("*.jsonl")):
        fname = jsonl_path.name
        offset = state["files"].get(fname, {}).get("offset", 0)

        try:
            # If stored offset exceeds file size (file was truncated/rotated),
            # reset to current end so we don't miss new content.
            try:
                file_size = jsonl_path.stat().st_size
                if offset > file_size:
                    offset = file_size
            except OSError:
                pass

            with open(jsonl_path, "rb") as f:
                f.seek(offset)
                for raw_bytes in f:
                    raw_line = raw_bytes.decode("utf-8", errors="replace").strip()
                    if not raw_line:
                        continue
                    try:
                        obj = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    if obj.get("type") != "message":
                        continue
                    msg = obj.get("message", {})
                    if msg.get("role") != "user":
                        continue

                    # Extract text from content blocks
                    content = msg.get("content", [])
                    parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                        elif isinstance(item, str):
                            parts.append(item)
                    text = " ".join(parts).strip()

                    if not text:
                        continue
                    if any(phrase in text for phrase in SKIP_PHRASES):
                        continue

                    # Strip OpenClaw's Telegram metadata wrapper; use only
                    # the actual user message text for detection and as query
                    text = extract_user_text(text)
                    if not text:
                        continue

                    is_pod, query = is_podcast_request(text, feed_keywords)
                    if not is_pod:
                        continue

                    # Dedup within this process lifetime
                    msg_id = obj.get("id", "")
                    key = f"{fname}:{msg_id}"
                    if key in fired_keys:
                        continue
                    fired_keys.add(key)

                    log.info(
                        "Podcast request in session %s msg=%s: %r",
                        fname[:8], msg_id[:8], text[:120],
                    )
                    threading.Thread(
                        target=fire_on_demand,
                        args=(query, container, agent_name, log),
                        daemon=True,
                    ).start()

                state["files"][fname] = {"offset": f.tell()}

        except OSError:
            pass

# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor session files and fire on_demand.py for podcast requests"
    )
    parser.add_argument("agent", help="Agent name (e.g. sample-agent)")
    parser.add_argument("--poll", type=int, default=POLL_INTERVAL,
                        help="Seconds between scans (default: %(default)s)")
    args = parser.parse_args()

    try:
        agent = resolve_agent(args.agent)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    log_dir: Path = agent["agent_log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.handlers.RotatingFileHandler(
            log_dir / "podcast-watcher.log",
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
        )],
    )
    log = logging.getLogger("podcast-watcher")
    log.info(
        "Starting podcast-watcher agent=%s container=%s poll=%ds",
        args.agent, agent["agent_container"], args.poll,
    )

    state_path = log_dir / "podcast-watcher.state"
    state = load_state(state_path)

    feed_keywords = load_feed_keywords(agent["agent_workspace"])
    log.info("Loaded %d feed keyword sets", len(feed_keywords))

    fired_keys: set = set()
    sessions_dir: Path = agent["agent_sessions"]

    # First pass: if not yet initialized, skip all existing history
    if not state.get("initialized"):
        initialize_offsets(sessions_dir, state, log)
        save_state(state_path, state)

    try:
        while True:
            scan_sessions(
                sessions_dir=sessions_dir,
                state=state,
                feed_keywords=feed_keywords,
                container=agent["agent_container"],
                agent_name=args.agent,
                log=log,
                fired_keys=fired_keys,
            )
            save_state(state_path, state)
            time.sleep(args.poll)
    except KeyboardInterrupt:
        log.info("Podcast-watcher stopped.")
        save_state(state_path, state)


if __name__ == "__main__":
    main()
