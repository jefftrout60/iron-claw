#!/usr/bin/env python3.13
"""
OpenAI-compatible Whisper transcription server using faster-whisper.

Endpoint: POST /v1/audio/transcriptions
  - multipart/form-data with 'file' and optional 'model' fields
  - Returns: {"text": "...", "language": "en"}

Health check: GET /health  →  200 {"status": "ok", "model": "..."}

Usage:
    python3.13 faster-whisper-server.py [--port 18797] [--model large-v3]

Models: tiny, base, small, medium, large-v2, large-v3 (default: large-v3)
Device: auto-detects Metal on Apple Silicon via CoreML
"""
import argparse
try:
    import cgi
except ImportError:
    import legacy_cgi as cgi  # Python 3.13+
import io
import json
import logging
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [whisper-server] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("whisper-server")

# Global model instance (loaded once at startup)
_model = None
_model_name = None
_model_lock = threading.Lock()


def load_model(model_name: str):
    global _model, _model_name
    from faster_whisper import WhisperModel

    log.info("Loading model %s …", model_name)

    # Use CoreML on Apple Silicon for Neural Engine acceleration.
    # Falls back to CPU if CoreML is unavailable.
    try:
        model = WhisperModel(model_name, device="auto", compute_type="auto")
        log.info("Model %s loaded (device=auto)", model_name)
    except Exception as e:
        log.warning("device=auto failed (%s), falling back to cpu", e)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        log.info("Model %s loaded (device=cpu)", model_name)

    with _model_lock:
        _model = model
        _model_name = model_name


def transcribe_audio(audio_bytes: bytes, filename: str, model_override: str | None = None) -> dict:
    with _model_lock:
        model = _model

    if model is None:
        raise RuntimeError("Model not loaded")

    suffix = Path(filename).suffix or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        log.info("Transcribing %s (%.1f MB) …", filename, len(audio_bytes) / 1_048_576)
        segments, info = model.transcribe(tmp_path, language="en", beam_size=5)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        log.info("Done: %d chars, language=%s", len(text), info.language)
        return {"text": text, "language": info.language}
    finally:
        os.unlink(tmp_path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default access log

    def send_json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/"):
            self.send_json(200, {"status": "ok", "model": _model_name})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/audio/transcriptions":
            self.send_json(404, {"error": "not found"})
            return

        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Parse multipart form
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(length),
        }
        try:
            form = cgi.FieldStorage(
                fp=io.BytesIO(body),
                environ=environ,
                keep_blank_values=True,
            )
        except Exception as e:
            self.send_json(400, {"error": f"Failed to parse form: {e}"})
            return

        file_field = form.get("file")
        if file_field is None:
            self.send_json(400, {"error": "Missing 'file' field"})
            return

        audio_bytes = file_field.file.read() if hasattr(file_field, "file") else file_field.value
        filename = getattr(file_field, "filename", None) or "audio.mp3"
        model_field = form.getvalue("model")

        try:
            result = transcribe_audio(audio_bytes, filename, model_field)
            self.send_json(200, result)
        except Exception as e:
            log.error("Transcription error: %s", e)
            self.send_json(500, {"error": str(e)})


def main():
    parser = argparse.ArgumentParser(description="faster-whisper OpenAI-compatible server")
    parser.add_argument("--port", type=int, default=18797)
    parser.add_argument("--model", default="large-v3")
    args = parser.parse_args()

    load_model(args.model)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log.info("Listening on 127.0.0.1:%d (model=%s)", args.port, args.model)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
