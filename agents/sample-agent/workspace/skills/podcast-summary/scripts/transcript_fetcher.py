#!/usr/bin/env python3
"""
transcript_fetcher.py — Transcript Acquisition Pipeline

Implements per-show transcript strategies dispatched from feed_dict["transcript_strategy"].
Tries each strategy in order until one succeeds; always falls back to show_notes.

Primary entry point:
    fetch(episode_dict, feed_dict) -> tuple[str, str]
    Returns (transcript_text, source_quality)

source_quality values:
    "published_transcript"   — full transcript from show's own site
    "third_party_transcript" — e.g. podscript.ai, HappyScribe
    "whisper_large"          — delegated to whisper_client.py (large-v3)
    "whisper_small"          — delegated to whisper_client.py (small.en)
    "show_notes"             — description / content:encoded fallback

Usage (CLI test mode):
    python3 transcript_fetcher.py --show the-tim-ferriss-show \\
        --episode-title "Naval Ravikant" --pub-date "2024-01-15"
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Optional whisper_client import — graceful fallback when not yet present
# ---------------------------------------------------------------------------

try:
    import whisper_client  # type: ignore
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_STRATEGY: list[str] = ["check_transcript_tag", "show_notes"]

# Strategies that are considered "whisper" and need whisper_client.py
WHISPER_STRATEGIES = {"whisper_large", "whisper_small"}

# Number of days to skip a strategy that previously failed
STRATEGY_FAILURE_CACHE_DAYS = 7

# User-Agent sent with all HTTP requests to reduce CDN 403s
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTML / text utilities
# ---------------------------------------------------------------------------


def strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities from text."""
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def strip_vtt_timestamps(text: str) -> str:
    """Remove WebVTT / SRT timestamp lines and header, leaving only spoken text."""
    # Remove WEBVTT header
    text = re.sub(r"^WEBVTT.*?\n", "", text, flags=re.MULTILINE)
    # Remove SRT/VTT timestamp lines like "00:00:01.000 --> 00:00:04.000"
    text = re.sub(r"\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[.,]\d{3}[^\n]*", "", text)
    # Remove bare numeric cue identifiers (SRT block numbers)
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)
    # Remove VTT cue settings lines (align:, position:, etc.)
    text = re.sub(r"^(align|position|size|line|region):[^\n]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_slug(title: str) -> str:
    """Convert episode title to a URL slug: lowercase, words joined with hyphens."""
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    return slug


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _http_get(url: str, timeout: int = 20) -> tuple[bytes, str]:
    """
    Fetch URL with urllib.request. Returns (body_bytes, content_type).

    Raises urllib.error.HTTPError on non-2xx status.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get_content_type() or ""
        return resp.read(), content_type


# ---------------------------------------------------------------------------
# Strategy: check_transcript_tag  (Task 2.1.5)
# ---------------------------------------------------------------------------


def check_transcript_tag(
    episode_dict: dict, feed_dict: dict
) -> Optional[tuple[str, str]]:
    """
    Fetch the podcast:transcript URL from the episode feed tag, if present.

    Supports:
        text/plain          → returned as-is
        text/vtt            → timestamps stripped
        application/x-subrip → timestamps stripped
        text/html           → HTML tags stripped

    Returns (text, "published_transcript") or None.
    """
    url = episode_dict.get("transcript_tag_url")
    if not url:
        return None

    try:
        body, content_type = _http_get(url)
    except Exception as exc:
        log.warning("check_transcript_tag: fetch failed for %s: %s", url, exc)
        return None

    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        text = body.decode("latin-1", errors="replace")

    if "vtt" in content_type or "x-subrip" in content_type:
        text = strip_vtt_timestamps(text)
    elif "html" in content_type:
        text = strip_html(text)
    # text/plain: use as-is (already a string)

    text = text.strip()
    if not text:
        log.warning("check_transcript_tag: fetched empty body from %s", url)
        return None

    return text, "published_transcript"


# ---------------------------------------------------------------------------
# Strategy: fetch_tim_blog  (Task 2.1.2)
# ---------------------------------------------------------------------------


def _parse_date_parts(pub_date: str) -> tuple[str, str, str]:
    """
    Parse pub_date (ISO 8601 or RFC 2822) and return (YYYY, MM, DD).
    Falls back to today if parsing fails.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(pub_date[:19].replace("T", "T"), fmt)
            return dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%d")
        except (ValueError, AttributeError):
            pass
    # Last resort: RFC 2822 via email.utils
    try:
        import email.utils
        dt_tuple = email.utils.parsedate(pub_date)
        if dt_tuple:
            dt = datetime(*dt_tuple[:6])
            return dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%d")
    except Exception:
        pass
    now = datetime.now()
    return now.strftime("%Y"), now.strftime("%m"), now.strftime("%d")


def _fetch_tim_blog_direct(slug: str, yyyy: str, mm: str, dd: str) -> Optional[str]:
    """Try the canonical tim.blog transcript URL and return body text or None."""
    url = f"https://tim.blog/{yyyy}/{mm}/{dd}/{slug}-transcript/"
    try:
        body, _ = _http_get(url)
        text = strip_html(body.decode("utf-8", errors="replace"))
        return text if len(text) > 200 else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.debug("fetch_tim_blog: 404 for %s", url)
        else:
            log.warning("fetch_tim_blog: HTTP %s for %s", e.code, url)
        return None
    except Exception as exc:
        log.warning("fetch_tim_blog: error fetching %s: %s", url, exc)
        return None


def _fetch_tim_blog_via_feed(episode_title: str) -> Optional[str]:
    """
    Search tim.blog/feed/ for a transcript post matching episode title keywords.
    Returns HTML-stripped body text or None.
    """
    try:
        body, _ = _http_get("https://tim.blog/feed/", timeout=30)
        feed_text = body.decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning("fetch_tim_blog: cannot fetch tim.blog/feed/: %s", exc)
        return None

    # Extract all <link> elements that contain "transcript"
    transcript_links = re.findall(r"<link>(https://tim\.blog/[^<]+transcript[^<]*)</link>", feed_text)
    if not transcript_links:
        return None

    # Score links by how many title words they contain
    title_words = set(re.sub(r"[^\w\s]", "", episode_title.lower()).split())
    best_url: Optional[str] = None
    best_score = 0
    for link in transcript_links:
        score = sum(1 for w in title_words if w in link.lower())
        if score > best_score:
            best_score = score
            best_url = link

    if not best_url or best_score == 0:
        return None

    try:
        body, _ = _http_get(best_url, timeout=30)
        text = strip_html(body.decode("utf-8", errors="replace"))
        return text if len(text) > 200 else None
    except Exception as exc:
        log.warning("fetch_tim_blog: error fetching feed-discovered URL %s: %s", best_url, exc)
        return None


def fetch_tim_blog(
    episode_dict: dict, feed_dict: dict
) -> Optional[tuple[str, str]]:
    """
    Fetch transcript from tim.blog.

    1. Build slug from audio URL or title; try direct URL with pub_date.
    2. Fall back to scanning tim.blog/feed/ for matching transcript posts.

    Returns (text, "published_transcript") or None.
    """
    title = episode_dict.get("title", "")
    pub_date = episode_dict.get("pub_date", "")
    audio_url = episode_dict.get("audio_url", "")
    yyyy, mm, dd = _parse_date_parts(pub_date)

    # Prefer slug extracted from audio URL path
    slug = ""
    if audio_url:
        # Audio URLs often look like .../episode-name-transcript.mp3 or .../slug.mp3
        path_part = audio_url.rstrip("/").rsplit("/", 1)[-1]
        path_part = re.sub(r"\.(mp3|m4a|ogg|wav)$", "", path_part, flags=re.IGNORECASE)
        slug = make_slug(path_part)

    if not slug and title:
        slug = make_slug(title)

    text: Optional[str] = None

    # Try direct URL first
    if slug:
        text = _fetch_tim_blog_direct(slug, yyyy, mm, dd)

    # Fall back to feed scan
    if not text and title:
        text = _fetch_tim_blog_via_feed(title)

    if not text:
        return None

    return text, "published_transcript"


# ---------------------------------------------------------------------------
# Strategy: fetch_podscript_ai  (Task 2.1.3)
# ---------------------------------------------------------------------------


def fetch_podscript_ai(
    episode_dict: dict, feed_dict: dict
) -> Optional[tuple[str, str]]:
    """
    Fetch Huberman Lab transcript from podscript.ai.

    URL pattern: https://podscript.ai/podcasts/huberman-lab-podcast/{slug}/

    Detects 403 → logs failure, returns None.
    Returns (text, "third_party_transcript") or None.
    """
    title = episode_dict.get("title", "")
    if not title:
        return None

    slug = make_slug(title)
    url = f"https://podscript.ai/podcasts/huberman-lab-podcast/{slug}/"

    try:
        body, _ = _http_get(url, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            log.warning("fetch_podscript_ai: 403 bot-protection — marking strategy failed")
            _cache_strategy_result(feed_dict, "fetch_podscript_ai", "failed")
        else:
            log.warning("fetch_podscript_ai: HTTP %s for %s", e.code, url)
        return None
    except Exception as exc:
        log.warning("fetch_podscript_ai: error fetching %s: %s", url, exc)
        return None

    html = body.decode("utf-8", errors="replace")

    # Try to extract the main content area before falling back to full strip
    main_match = re.search(
        r'<(?:main|article|div)[^>]*(?:class|id)="[^"]*(?:transcript|content|article|main)[^"]*"[^>]*>(.*?)</(?:main|article|div)>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if main_match:
        text = strip_html(main_match.group(1))
    else:
        text = strip_html(html)

    if len(text) < 200:
        log.warning("fetch_podscript_ai: content too short (%d chars) for %s", len(text), url)
        return None

    return text, "third_party_transcript"


# ---------------------------------------------------------------------------
# Strategy: fetch_happyscribe  (Task 2.1.4)
# ---------------------------------------------------------------------------


def fetch_happyscribe(
    episode_dict: dict, feed_dict: dict
) -> Optional[tuple[str, str]]:
    """
    Fetch Peter Attia Drive transcript from podcasts.happyscribe.com.

    URL pattern: https://podcasts.happyscribe.com/the-peter-attia-drive/{slug}/

    Detects 403 → marks strategy "failed" in cache, returns None.
    Returns (text, "third_party_transcript") or None.
    """
    title = episode_dict.get("title", "")
    if not title:
        return None

    slug = make_slug(title)
    url = f"https://podcasts.happyscribe.com/the-peter-attia-drive/{slug}/"

    try:
        body, _ = _http_get(url, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            log.warning("fetch_happyscribe: 403 — marking strategy failed in cache")
            _cache_strategy_result(feed_dict, "fetch_happyscribe", "failed")
        else:
            log.warning("fetch_happyscribe: HTTP %s for %s", e.code, url)
        return None
    except Exception as exc:
        log.warning("fetch_happyscribe: error fetching %s: %s", url, exc)
        return None

    html = body.decode("utf-8", errors="replace")

    # Try to extract main content area
    main_match = re.search(
        r'<(?:main|article|div)[^>]*(?:class|id)="[^"]*(?:transcript|content|article|main)[^"]*"[^>]*>(.*?)</(?:main|article|div)>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if main_match:
        text = strip_html(main_match.group(1))
    else:
        text = strip_html(html)

    if len(text) < 200:
        log.warning("fetch_happyscribe: content too short (%d chars) for %s", len(text), url)
        return None

    return text, "third_party_transcript"


# ---------------------------------------------------------------------------
# Whisper dispatch  (Task 2.1.1)
# ---------------------------------------------------------------------------


def _dispatch_whisper(
    model_tier: str, episode_dict: dict, feed_dict: dict
) -> Optional[tuple[str, str]]:
    """
    Delegate to whisper_client.transcribe() if available.

    model_tier: "whisper_large" or "whisper_small"
    Returns (text, model_tier) or None if whisper_client is not available or fails.
    """
    if not WHISPER_AVAILABLE:
        log.warning("whisper strategies skipped: whisper_client.py not importable")
        return None

    audio_url = episode_dict.get("audio_url")
    if not audio_url:
        log.warning("%s: no audio_url in episode_dict", model_tier)
        return None

    # whisper_client.transcribe() expects "large" or "small",
    # not the strategy names "whisper_large" / "whisper_small".
    client_tier = "large" if model_tier == "whisper_large" else "small"

    try:
        text = whisper_client.transcribe(audio_url, client_tier)
    except Exception as exc:
        log.warning("%s: whisper_client.transcribe failed: %s", model_tier, exc)
        return None

    if not text or not text.strip():
        log.warning("%s: whisper_client returned empty transcript", model_tier)
        return None

    return text.strip(), model_tier


# ---------------------------------------------------------------------------
# Strategy: show_notes  (always-last fallback)
# ---------------------------------------------------------------------------


def show_notes(
    episode_dict: dict, feed_dict: dict
) -> tuple[str, str]:
    """
    Return show notes / description as plain text. Never returns None.

    Tries full_notes first, then description.
    Strips HTML from whichever field is used.
    """
    raw = episode_dict.get("full_notes") or episode_dict.get("description") or ""
    return strip_html(raw), "show_notes"


# ---------------------------------------------------------------------------
# Strategy caching helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_strategy_result(feed_dict: dict, strategy_name: str, result: str) -> None:
    """
    Record a strategy result in feed_dict["transcript_strategy_cache"].

    result: "success" | "failed" | "not_found"
    """
    cache = feed_dict.setdefault("transcript_strategy_cache", {})
    cache[strategy_name] = {
        "last_result": result,
        "last_tested": _now_iso(),
    }


def _strategy_recently_failed(feed_dict: dict, strategy_name: str) -> bool:
    """
    Return True if strategy_name is marked "failed" in the cache
    and was tested within the last STRATEGY_FAILURE_CACHE_DAYS days.
    """
    cache = feed_dict.get("transcript_strategy_cache", {})
    entry = cache.get(strategy_name)
    if not entry:
        return False
    if entry.get("last_result") != "failed":
        return False

    last_tested_str = entry.get("last_tested", "")
    try:
        last_tested = datetime.fromisoformat(last_tested_str)
        if last_tested.tzinfo is None:
            last_tested = last_tested.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=STRATEGY_FAILURE_CACHE_DAYS)
        return last_tested > cutoff
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Strategy: fetch_openai_whisper — cloud transcription via OpenAI Whisper API
# ---------------------------------------------------------------------------

_OPENAI_WHISPER_MAX_BYTES = 24 * 1024 * 1024  # 24 MB (API limit is 25 MB)
_OPENAI_WHISPER_SEGMENT_MINUTES = 15           # Default max segment duration (cap)
_OPENAI_WHISPER_TARGET_SEGMENT_MB = 20         # Target segment size to stay under limit


def _get_audio_duration_secs(input_path: str) -> Optional[float]:
    """Return audio duration in seconds via ffprobe, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                input_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def _load_openai_api_key() -> str:
    """Load OPENAI_API_KEY — checks os.environ first, then walks to repo root .env."""
    # Check environment first (works inside Docker where .env is not accessible)
    env_val = os.environ.get("OPENAI_API_KEY", "")
    if env_val:
        return env_val
    # Fall back to .env file walk (works on host)
    try:
        current = Path(__file__).resolve().parent
        for candidate in [current, *current.parents]:
            if (candidate / "CLAUDE.md").exists():
                env_path = candidate / "agents" / "sample-agent" / ".env"
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("OPENAI_API_KEY="):
                            val = line[len("OPENAI_API_KEY="):]
                            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                                val = val[1:-1]
                            return val
    except Exception:
        pass
    return ""


def _download_audio(url: str, dest_path: str) -> int:
    """Stream-download audio URL to dest_path. Returns file size in bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp:
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(512 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    return os.path.getsize(dest_path)


def _split_audio_ffmpeg(input_path: str) -> list[str]:
    """Split audio into size-aware segments via ffmpeg.

    Segment duration is calculated dynamically: target _OPENAI_WHISPER_TARGET_SEGMENT_MB
    per chunk based on actual file bitrate, capped at _OPENAI_WHISPER_SEGMENT_MINUTES.
    Returns sorted list of temp file paths (caller must delete them).
    """
    file_size = os.path.getsize(input_path)
    segment_secs = _OPENAI_WHISPER_SEGMENT_MINUTES * 60  # default cap

    duration = _get_audio_duration_secs(input_path)
    if duration and duration > 0:
        target_bytes = _OPENAI_WHISPER_TARGET_SEGMENT_MB * 1024 * 1024
        calculated = int(target_bytes * duration / file_size)
        segment_secs = max(60, min(calculated, segment_secs))
        log.info(
            "_split_audio_ffmpeg: file=%.1f MB, duration=%.0fs, target segment=%ds",
            file_size / 1_048_576, duration, segment_secs,
        )

    tmp_dir = tempfile.mkdtemp(prefix="podcast_whisper_")
    pattern = os.path.join(tmp_dir, "seg_%04d.mp3")
    cmd = [
        "ffmpeg", "-i", input_path,
        "-f", "segment",
        "-segment_time", str(segment_secs),
        "-c", "copy",
        "-reset_timestamps", "1",
        pattern,
        "-loglevel", "error",
        "-y",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("_split_audio_ffmpeg: failed: %s", exc)
        return []

    return sorted(
        os.path.join(tmp_dir, f)
        for f in os.listdir(tmp_dir)
        if f.startswith("seg_")
    )


def _transcribe_segment_openai(audio_path: str, api_key: str) -> Optional[str]:
    """POST one audio file to OpenAI /v1/audio/transcriptions. Returns plain text."""
    boundary = "----PodcastTranscriptBoundary7x"
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    filename = os.path.basename(audio_path)
    parts: list[bytes] = [
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n'
        ).encode(),
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="response_format"\r\n\r\ntext\r\n'
        ).encode(),
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: audio/mpeg\r\n\r\n"
        ).encode() + audio_bytes + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.read().decode("utf-8").strip()
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        log.warning("_transcribe_segment_openai: HTTP %d: %s", e.code, err_body[:200])
        return None
    except Exception as exc:
        log.warning("_transcribe_segment_openai: %s", exc)
        return None


def fetch_openai_whisper(
    episode_dict: dict, feed_dict: dict
) -> Optional[tuple[str, str]]:
    """Transcribe episode audio via OpenAI Whisper API (whisper-1 = whisper-large-v2).

    Downloads audio, splits files >24 MB into 15-minute segments with ffmpeg,
    transcribes each segment, and returns the concatenated result.

    Returns (text, "whisper_large") or None on failure.
    """
    api_key = _load_openai_api_key()
    if not api_key:
        log.warning("fetch_openai_whisper: OPENAI_API_KEY not found — skipping")
        return None

    audio_url = episode_dict.get("audio_url")
    if not audio_url:
        log.warning("fetch_openai_whisper: no audio_url in episode_dict")
        return None

    tmp_audio = tempfile.mktemp(suffix=".mp3", prefix="podcast_dl_")
    segment_paths: list[str] = []

    try:
        log.info("fetch_openai_whisper: downloading %s", audio_url)
        file_size = _download_audio(audio_url, tmp_audio)
        log.info("fetch_openai_whisper: downloaded %.1f MB", file_size / 1024 / 1024)

        if file_size <= _OPENAI_WHISPER_MAX_BYTES:
            files_to_transcribe = [tmp_audio]
        else:
            log.info("fetch_openai_whisper: file >24 MB — splitting with ffmpeg")
            segment_paths = _split_audio_ffmpeg(tmp_audio)
            if not segment_paths:
                log.warning("fetch_openai_whisper: ffmpeg split produced no segments")
                return None
            files_to_transcribe = segment_paths
            log.info(
                "fetch_openai_whisper: %d segments to transcribe",
                len(files_to_transcribe),
            )

        transcripts: list[str] = []
        for i, seg_path in enumerate(files_to_transcribe, 1):
            log.info(
                "fetch_openai_whisper: transcribing segment %d/%d",
                i, len(files_to_transcribe),
            )
            text = _transcribe_segment_openai(seg_path, api_key)
            if text:
                transcripts.append(text)
            else:
                log.warning("fetch_openai_whisper: segment %d returned no text", i)

        if not transcripts:
            return None

        full_transcript = " ".join(transcripts)
        log.info(
            "fetch_openai_whisper: complete — %d chars", len(full_transcript)
        )
        return full_transcript, "whisper_large"

    except Exception as exc:
        log.warning("fetch_openai_whisper: unexpected error: %s", exc)
        return None

    finally:
        try:
            if os.path.exists(tmp_audio):
                os.unlink(tmp_audio)
        except Exception:
            pass
        if segment_paths:
            try:
                import shutil
                shutil.rmtree(os.path.dirname(segment_paths[0]), ignore_errors=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Strategy dispatch table
# ---------------------------------------------------------------------------

_STRATEGY_FUNCS: dict[str, object] = {
    "check_transcript_tag": check_transcript_tag,
    "fetch_tim_blog": fetch_tim_blog,
    "fetch_podscript_ai": fetch_podscript_ai,
    "fetch_happyscribe": fetch_happyscribe,
    "fetch_openai_whisper": fetch_openai_whisper,
    "show_notes": show_notes,
    # whisper strategies are handled inline in fetch()
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch(episode_dict: dict, feed_dict: dict) -> tuple[str, str]:
    """
    Fetch a transcript for the given episode using the show's strategy list.

    Tries each strategy in order, skipping ones recently marked as failed.
    Always returns (text, source_quality) — never raises, never returns empty.

    The final fallback is show_notes which is guaranteed non-None.
    """
    strategies: list[str] = feed_dict.get("transcript_strategy") or DEFAULT_STRATEGY

    # Ensure show_notes is always available as a final fallback even if not listed
    strategies_with_fallback = list(strategies)
    if "show_notes" not in strategies_with_fallback:
        strategies_with_fallback.append("show_notes")

    for strategy_name in strategies_with_fallback:
        # Skip strategies known to be broken within the cache window
        if _strategy_recently_failed(feed_dict, strategy_name):
            log.info("Skipping recently-failed strategy: %s", strategy_name)
            continue

        result: Optional[tuple[str, str]] = None

        # Whisper strategies delegate to whisper_client.py
        if strategy_name in WHISPER_STRATEGIES:
            result = _dispatch_whisper(strategy_name, episode_dict, feed_dict)
        elif strategy_name == "show_notes":
            # show_notes never returns None — use it directly as final fallback
            text, quality = show_notes(episode_dict, feed_dict)
            _cache_strategy_result(feed_dict, strategy_name, "success")
            return text, quality
        else:
            func = _STRATEGY_FUNCS.get(strategy_name)
            if func is None:
                log.warning("Unknown transcript strategy: %s — skipping", strategy_name)
                continue
            try:
                result = func(episode_dict, feed_dict)  # type: ignore[operator]
            except Exception as exc:
                log.warning("Strategy %s raised unexpectedly: %s", strategy_name, exc)
                result = None

        if result is not None:
            text, quality = result
            if text and text.strip():
                _cache_strategy_result(feed_dict, strategy_name, "success")
                return text.strip(), quality

        # Strategy produced nothing
        _cache_strategy_result(feed_dict, strategy_name, "not_found")

    # Absolute safety net — show_notes is always above but be defensive
    text, quality = show_notes(episode_dict, feed_dict)
    return text, quality


# ---------------------------------------------------------------------------
# CLI test mode
# ---------------------------------------------------------------------------


def _build_cli_episode(args: argparse.Namespace) -> tuple[dict, dict]:
    """Construct minimal episode_dict and feed_dict from CLI arguments."""
    episode_dict = {
        "title": args.episode_title or "",
        "pub_date": args.pub_date or datetime.now().strftime("%Y-%m-%d"),
        "audio_url": args.audio_url or "",
        "description": f"CLI test episode: {args.episode_title}",
        "full_notes": "",
        "transcript_tag_url": args.transcript_url or None,
    }

    # Look up the feed strategy from a real feeds.json if possible
    feed_dict: dict = {
        "id": args.show or "unknown",
        "transcript_strategy": None,  # will trigger default
    }

    if args.show:
        try:
            import json
            from pathlib import Path
            vault_path = Path(__file__).parent.parent / "podcast_vault" / "feeds.json"
            if vault_path.exists():
                with open(vault_path) as f:
                    feeds_data = json.load(f)
                for feed in feeds_data.get("feeds", []):
                    if feed.get("id") == args.show or feed.get("title", "").lower() == args.show.lower():
                        feed_dict = feed
                        break
        except Exception as exc:
            print(f"[cli] Warning: could not load feeds.json: {exc}", file=sys.stderr)

    return episode_dict, feed_dict


def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)s [%(name)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Test transcript_fetcher.py for a single episode."
    )
    parser.add_argument("--show", help="Feed ID or title (e.g. the-tim-ferriss-show)")
    parser.add_argument("--episode-title", help="Episode title")
    parser.add_argument("--pub-date", help="Publication date YYYY-MM-DD")
    parser.add_argument("--audio-url", help="Direct audio URL")
    parser.add_argument("--transcript-url", help="Direct transcript tag URL to test")
    parser.add_argument(
        "--strategy",
        help="Override strategy list (comma-separated, e.g. check_transcript_tag,show_notes)",
    )
    args = parser.parse_args()

    episode_dict, feed_dict = _build_cli_episode(args)

    if args.strategy:
        feed_dict["transcript_strategy"] = [s.strip() for s in args.strategy.split(",")]

    print(f"\n[cli] Show:     {feed_dict.get('id')}")
    print(f"[cli] Episode:  {episode_dict['title']}")
    print(f"[cli] PubDate:  {episode_dict['pub_date']}")
    print(f"[cli] Strategy: {feed_dict.get('transcript_strategy') or DEFAULT_STRATEGY}")
    print()

    text, quality = fetch(episode_dict, feed_dict)

    print(f"[cli] Source quality: {quality}")
    print(f"[cli] Text length:    {len(text)} chars")
    print(f"[cli] Preview:\n{text[:500]}")
    if len(text) > 500:
        print("...")


if __name__ == "__main__":
    main()
