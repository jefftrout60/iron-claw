#!/usr/bin/env python3
"""
whisper_client.py — HTTP client for the whisper-server bridge.

Downloads episode audio (cached in /tmp/podcast-summary/), POSTs multipart
form to localhost:18797/inference, returns transcript text.  Switches to the
correct Whisper model before transcribing and restores the default afterward.

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
import shutil
import subprocess
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
TRANSCRIBE_TIMEOUT = 1800    # 30 minutes per chunk (handles long segments)

# Audio files larger than this threshold are split into chunks before
# transcription. Pre-M5 Macs use CPU inference (no Metal tensor API), so
# processing a full 1.5-hour episode in one shot exceeds the connection
# timeout. 20 MB ≈ 20-30 min at typical podcast bitrates.
CHUNK_THRESHOLD_BYTES = 20 * 1024 * 1024  # 20 MB
CHUNK_DURATION_SECS = 300                 # 5-minute chunks (safer for CPU inference)

# Shows that use large-v3 for better accuracy on dense scientific content.
WHISPER_LARGE_SHOWS = {
    "the-peter-attia-drive",
    "huberman-lab",
    "foundmyfitness-members-feed",
    "valley-to-peak-nutrition-podcast",
    "barbell-shrugged",
    "better-brain-fitness",
}

# ---------------------------------------------------------------------------
# Path / env helpers — same pattern as engine.py
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    """Walk up from this file to find the ironclaw repo root (has CLAUDE.md)."""
    current = Path(__file__).resolve().parent
    for candidate in [current, *current.parents]:
        if (candidate / "CLAUDE.md").exists():
            return candidate
    raise FileNotFoundError(
        "Cannot locate ironclaw repo root (no CLAUDE.md found walking up from "
        f"{Path(__file__).resolve()})"
    )


def _load_env(agent_name: str = "sample-agent") -> dict[str, str]:
    """Parse agents/{agent_name}/.env and return key→value dict.

    Checks os.environ first (works inside Docker container where .env is
    injected as environment variables). Falls back to file-based lookup
    for host-side execution where the repo root is accessible.

    Handles blank lines, # comments, and quoted values.
    Missing file returns empty dict without raising.
    """
    _KNOWN_KEYS = (
        "OPENAI_API_KEY", "PODCAST_SUMMARY_MODEL",
        "DIGEST_TO_EMAIL", "SMTP_FROM_EMAIL", "GMAIL_APP_PASSWORD",
    )
    env_from_environ = {k: os.environ[k] for k in _KNOWN_KEYS if k in os.environ}
    if env_from_environ.get("OPENAI_API_KEY"):
        return env_from_environ

    # Fall back to .env file — works on host
    env: dict[str, str] = {}
    try:
        env_path = _find_repo_root() / "agents" / agent_name / ".env"
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except (FileNotFoundError, OSError):
        pass
    return env


# ---------------------------------------------------------------------------
# Multipart form builder — stdlib only, no requests
# ---------------------------------------------------------------------------

def _build_multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    """Build a multipart/form-data body from plain fields and file uploads.

    Args:
        fields: {name: value} text fields.
        files:  {name: (filename, data_bytes)} binary file fields.

    Returns:
        (body_bytes, content_type_header_value)
    """
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
# Model path helper
# ---------------------------------------------------------------------------

def _get_model_path(model_tier: str, model_dir: str) -> str:
    """Return the absolute path to the ggml model file for model_tier.

    Args:
        model_tier: "large" or "small"
        model_dir:  Value of WHISPER_MODEL_DIR env var.

    Raises:
        ValueError: if model_tier is not recognised.
    """
    if model_tier == "large":
        return str(Path(model_dir) / "ggml-large-v3.bin")
    if model_tier == "small":
        return str(Path(model_dir) / "ggml-small.en.bin")
    raise ValueError(f"Unknown model_tier: {model_tier!r} (expected 'large' or 'small')")


# ---------------------------------------------------------------------------
# Model switching
# ---------------------------------------------------------------------------

def _switch_model(model_path: str, whisper_url: str) -> None:
    """POST /load to switch the running whisper-server to model_path.

    Logs a warning and returns without raising if the endpoint fails — some
    whisper-server versions do not implement /load and this must be soft.
    """
    # /load expects multipart/form-data (same as /inference), not JSON.
    body, content_type = _build_multipart(
        fields={"model": model_path},
        files={},
    )
    req = urllib.request.Request(
        url=f"{whisper_url}/load",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                log.warning(
                    "Model switch to %s returned HTTP %s — continuing with current model",
                    model_path,
                    resp.status,
                )
            else:
                log.info("Switched whisper model to %s", model_path)
    except Exception as exc:
        log.warning(
            "Model switch to %s failed (%s) — continuing with current model",
            model_path,
            exc,
        )


# ---------------------------------------------------------------------------
# Audio download
# ---------------------------------------------------------------------------

def _download_audio(audio_url: str) -> Path:
    """Download audio_url to /tmp/podcast-summary/ep_{hash}.mp3.

    Skips the download if the file already exists (resume-safe).

    Args:
        audio_url: Full HTTP(S) URL to the audio file.

    Returns:
        Path to the local .mp3 file.

    Raises:
        urllib.error.URLError / OSError on download failure.
    """
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


# ---------------------------------------------------------------------------
# Audio cleanup
# ---------------------------------------------------------------------------

def _delete_audio(audio_path: Path) -> None:
    """Delete the cached audio file after successful transcription.

    Logs a warning on failure but never raises — cleanup is best-effort.
    """
    try:
        audio_path.unlink()
        log.info("Deleted cached audio: %s", audio_path)
    except Exception as exc:
        log.warning("Could not delete cached audio %s: %s", audio_path, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _split_audio_chunks(audio_path: Path) -> list[Path]:
    """Split audio_path into CHUNK_DURATION_SECS segments using ffmpeg -c copy.

    Returns an ordered list of chunk paths inside a temp directory.
    Caller must clean up the directory after use.

    Raises:
        RuntimeError: if ffmpeg segmentation fails.
    """
    chunk_dir = audio_path.parent / f"chunks_{audio_path.stem}"
    chunk_dir.mkdir(exist_ok=True)
    chunk_pattern = str(chunk_dir / "chunk_%03d.mp3")

    # Use full path so ffmpeg is found when running under cron's minimal PATH
    ffmpeg_bin = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
    cmd = [
        ffmpeg_bin, "-i", str(audio_path),
        "-f", "segment",
        "-segment_time", str(CHUNK_DURATION_SECS),
        "-c", "copy",
        "-reset_timestamps", "1",
        chunk_pattern,
        "-y",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg segmentation failed: {result.stderr.decode(errors='replace')[:400]}"
        )

    chunks = sorted(chunk_dir.glob("chunk_*.mp3"))
    if not chunks:
        raise RuntimeError(f"ffmpeg produced no chunks in {chunk_dir}")
    log.info("Split %s into %d chunk(s) of ~%ds each", audio_path.name, len(chunks), CHUNK_DURATION_SECS)
    return chunks


def _post_to_inference(audio_path: Path, whisper_url: str) -> str:
    """POST a single audio file to /inference and return the transcript text.

    Raises:
        RuntimeError: on non-200 response or empty/missing text field.
    """
    audio_bytes = audio_path.read_bytes()
    body, content_type = _build_multipart(
        fields={"response_format": "json", "language": "en", "temperature": "0.0"},
        files={"file": (audio_path.name, audio_bytes)},
    )
    req = urllib.request.Request(
        url=f"{whisper_url}/inference",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    log.info("POSTing to %s/inference (%.1f MB)", whisper_url, len(audio_bytes) / 1_048_576)
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
    return text


def is_available(whisper_url: str = DEFAULT_WHISPER_URL) -> bool:
    """Return True if the whisper-server is reachable (GET / returns 200).

    Never raises — returns False on any error.
    """
    try:
        with urllib.request.urlopen(whisper_url + "/", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def transcribe(
    audio_url: str,
    model_tier: str,
    whisper_url: str = DEFAULT_WHISPER_URL,
) -> str:
    """Download audio and transcribe it via the whisper-server bridge.

    Steps:
    1. Download audio to /tmp/podcast-summary/ep_{hash}.mp3 (skips if cached).
    2. Switch to the correct Whisper model via POST /load.
    3. POST multipart form to {whisper_url}/inference.
    4. Parse JSON response and return data["text"].
    5. Restore small.en default model.
    6. Delete the local audio file.

    Args:
        audio_url:   Full HTTP(S) URL to the episode audio.
        model_tier:  "large" or "small".
        whisper_url: Base URL for the whisper-server bridge.

    Returns:
        Transcript text string.

    Raises:
        RuntimeError: on non-200 HTTP response or missing/empty "text" in response.
        urllib.error.URLError: on network errors.
    """
    # Resolve model dir from env (allow override via env var for testing)
    env = _load_env()
    model_dir = os.environ.get("WHISPER_MODEL_DIR") or env.get("WHISPER_MODEL_DIR", "")
    if not model_dir:
        log.warning("WHISPER_MODEL_DIR not set — model switching disabled")

    # Resolve whisper_url from env if not overridden by caller
    if whisper_url == DEFAULT_WHISPER_URL:
        env_url = os.environ.get("WHISPER_BRIDGE_URL") or env.get("WHISPER_BRIDGE_URL", "")
        if env_url:
            whisper_url = env_url.rstrip("/")

    # Inside Docker, 127.0.0.1 points to the container itself, not the host.
    # Remap to host.docker.internal so the container can reach the host whisper server.
    if os.path.exists("/.dockerenv") and "127.0.0.1" in whisper_url:
        whisper_url = whisper_url.replace("127.0.0.1", "host.docker.internal")

    # 1. Download audio
    audio_path = _download_audio(audio_url)

    # 2. Switch to the correct model before transcribing
    if model_dir:
        target_model_path = _get_model_path(model_tier, model_dir)
        _switch_model(target_model_path, whisper_url)

    # 3. POST to /inference — chunk large files to avoid CPU timeout on pre-M5 Macs
    chunk_dir: Path | None = None
    try:
        file_size = audio_path.stat().st_size
        log.info("Audio size: %.1f MB, model=%s", file_size / 1_048_576, model_tier)

        if file_size > CHUNK_THRESHOLD_BYTES:
            log.info(
                "File exceeds %.0f MB threshold — splitting into %ds chunks",
                CHUNK_THRESHOLD_BYTES / 1_048_576,
                CHUNK_DURATION_SECS,
            )
            chunks = _split_audio_chunks(audio_path)
            chunk_dir = chunks[0].parent
            parts: list[str] = []
            for i, chunk in enumerate(chunks, 1):
                log.info("Transcribing chunk %d/%d (%s)…", i, len(chunks), chunk.name)
                parts.append(_post_to_inference(chunk, whisper_url))
            text = " ".join(parts)
        else:
            text = _post_to_inference(audio_path, whisper_url)

        log.info("Transcription complete: %d characters", len(text))

    finally:
        # 4. Clean up chunk directory if we split the audio.
        if chunk_dir is not None:
            try:
                shutil.rmtree(chunk_dir)
                log.info("Deleted chunk directory: %s", chunk_dir)
            except Exception as exc:
                log.warning("Could not delete chunk directory %s: %s", chunk_dir, exc)

        # 5. Restore default small.en model regardless of transcription outcome.
        # This runs even if transcription failed so the server is left in a
        # known state for the next call.
        if model_dir:
            small_path = _get_model_path("small", model_dir)
            _switch_model(small_path, whisper_url)

    # 6. Delete cached audio after successful transcription
    _delete_audio(audio_path)

    return text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="whisper_client — transcribe a podcast episode via whisper-server",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Health-check the whisper-server and exit.",
    )
    parser.add_argument(
        "--url",
        metavar="AUDIO_URL",
        help="HTTP(S) URL of the episode audio to transcribe.",
    )
    parser.add_argument(
        "--model",
        metavar="TIER",
        default="small",
        choices=["small", "large"],
        help="Whisper model tier: 'small' (default) or 'large'.",
    )
    parser.add_argument(
        "--whisper-url",
        metavar="URL",
        default=DEFAULT_WHISPER_URL,
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
