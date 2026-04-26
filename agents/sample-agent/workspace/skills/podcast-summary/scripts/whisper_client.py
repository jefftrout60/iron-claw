#!/usr/bin/env python3
"""
whisper_client.py — HTTP client for faster-whisper-server.

Downloads episode audio (cached in /tmp/podcast-summary/), POSTs multipart
form to localhost:18797/v1/audio/transcriptions (OpenAI-compatible endpoint),
returns transcript text.

Usage:
    python3 whisper_client.py --check
    python3 whisper_client.py --url https://example.com/episode.mp3 --model small
    python3 whisper_client.py --url https://... --model large
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [whisper_client] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("whisper_client")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_WHISPER_URL = "http://localhost:18797"
DOWNLOAD_DIR = Path("/tmp/podcast-summary")
DOWNLOAD_TIMEOUT = 600       # 10 minutes for download
TRANSCRIBE_TIMEOUT = 1800    # 30 minutes (handles long episodes)

# Shows that use large-v3 for better accuracy on dense scientific content.
WHISPER_LARGE_SHOWS = {
    "the-peter-attia-drive",
    "huberman-lab",
    "foundmyfitness-members-feed",
    "valley-to-peak-nutrition-podcast",
    "barbell-shrugged",
    "better-brain-fitness",
}

# Model tier → faster-whisper model name
MODEL_NAMES = {
    "large": "large-v3",
    "small": "small",
}

# ---------------------------------------------------------------------------
# Multipart form builder — stdlib only, no requests
# ---------------------------------------------------------------------------

def _build_multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = (
        "----PodcastSummaryBoundary"
        + hashlib.md5(str(fields).encode()).hexdigest()[:16]
    )
    body: list[bytes] = []

    for name, value in fields.items():
        body.append(f"--{boundary}".encode())
        body.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        body.append(b"")
        body.append(value.encode() if isinstance(value, str) else value)

    for name, (filename, data) in files.items():
        body.append(f"--{boundary}".encode())
        body.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
        )
        body.append(b"Content-Type: audio/mpeg")
        body.append(b"")
        body.append(data)

    body.append(f"--{boundary}--".encode())
    return b"\r\n".join(body), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------
# Audio download
# ---------------------------------------------------------------------------

def _download_audio(audio_url: str) -> Path:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.md5(audio_url.encode()).hexdigest()[:12]
    dest = DOWNLOAD_DIR / f"ep_{url_hash}.mp3"

    if dest.exists():
        log.info("Audio already cached, skipping download: %s", dest)
        return dest

    log.info("Downloading audio from %s", audio_url)
    req = urllib.request.Request(
        audio_url,
        headers={"User-Agent": "Mozilla/5.0 (podcast-summary/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(512 * 1024)
                if not chunk:
                    break
                f.write(chunk)

    size = dest.stat().st_size
    log.info("Audio saved to %s (%d bytes)", dest, size)
    return dest


def _delete_audio(audio_path: Path) -> None:
    try:
        audio_path.unlink()
        log.info("Deleted cached audio: %s", audio_path)
    except Exception as exc:
        log.warning("Could not delete cached audio %s: %s", audio_path, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available(whisper_url: str = DEFAULT_WHISPER_URL) -> bool:
    try:
        with urllib.request.urlopen(whisper_url + "/health", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def transcribe(
    audio_url: str,
    model_tier: str,
    whisper_url: str = DEFAULT_WHISPER_URL,
) -> str:
    """Download audio and transcribe via faster-whisper-server.

    Args:
        audio_url:   Full HTTP(S) URL to the episode audio.
        model_tier:  "large" or "small".
        whisper_url: Base URL for the whisper server.

    Returns:
        Transcript text string.
    """
    # Resolve whisper_url from env if not overridden by caller
    if whisper_url == DEFAULT_WHISPER_URL:
        env_url = os.environ.get("WHISPER_BRIDGE_URL", "")
        if env_url:
            whisper_url = env_url.rstrip("/")

    # Inside Docker, remap 127.0.0.1 → host.docker.internal
    if os.path.exists("/.dockerenv") and "127.0.0.1" in whisper_url:
        whisper_url = whisper_url.replace("127.0.0.1", "host.docker.internal")

    model_name = MODEL_NAMES.get(model_tier, model_tier)

    # Download audio
    audio_path = _download_audio(audio_url)

    try:
        audio_bytes = audio_path.read_bytes()
        file_size = audio_path.stat().st_size
        log.info("Audio size: %.1f MB, model=%s (%s)", file_size / 1_048_576, model_tier, model_name)

        body, content_type = _build_multipart(
            fields={"model": model_name, "language": "en", "response_format": "json"},
            files={"file": (audio_path.name, audio_bytes)},
        )

        req = urllib.request.Request(
            url=f"{whisper_url}/v1/audio/transcriptions",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        log.info("POSTing to %s/v1/audio/transcriptions (%.1f MB)", whisper_url, len(audio_bytes) / 1_048_576)

        with urllib.request.urlopen(req, timeout=TRANSCRIBE_TIMEOUT) as resp:
            status = resp.status
            raw = resp.read()

        if status != 200:
            raise RuntimeError(
                f"whisper-server returned HTTP {status}: {raw[:200].decode(errors='replace')}"
            )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"whisper-server response is not valid JSON: {exc}. "
                f"Raw (first 200 bytes): {raw[:200].decode(errors='replace')}"
            ) from exc

        text = data.get("text", "")
        if not text or not text.strip():
            raise RuntimeError("whisper-server returned empty or missing 'text' field")

        log.info("Transcription complete: %d characters", len(text))
        return text

    finally:
        _delete_audio(audio_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="whisper_client — transcribe a podcast episode via faster-whisper-server",
    )
    parser.add_argument("--check", action="store_true", help="Health-check and exit.")
    parser.add_argument("--url", metavar="AUDIO_URL", help="HTTP(S) URL of the episode audio.")
    parser.add_argument(
        "--model", metavar="TIER", default="small", choices=["small", "large"],
        help="Whisper model tier: 'small' (default) or 'large'.",
    )
    parser.add_argument(
        "--whisper-url", metavar="URL", default=DEFAULT_WHISPER_URL,
        help=f"Whisper-server base URL (default: {DEFAULT_WHISPER_URL}).",
    )
    args = parser.parse_args()

    if args.check:
        available = is_available(args.whisper_url)
        if available:
            print(f"OK — whisper-server is available at {args.whisper_url}")
            sys.exit(0)
        else:
            print(f"UNAVAILABLE — cannot reach whisper-server at {args.whisper_url}")
            sys.exit(1)

    if not args.url:
        parser.error("--url is required when not using --check")

    try:
        text = transcribe(args.url, args.model, args.whisper_url)
        print(text)
    except Exception as exc:
        log.error("Transcription failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
