#!/usr/bin/env python3.13
"""
OpenAI-compatible Whisper transcription server using mlx-whisper.
Runs on Apple Silicon via the MLX framework (Neural Engine / Metal).

Endpoint: POST /v1/audio/transcriptions
  - multipart/form-data with 'file' and optional 'model' fields
  - Returns: {"text": "...", "language": "en"}

Health check: GET /health  →  200 {"status": "ok", "model": "..."}

Usage:
    python3.13 faster-whisper-server.py [--port 18797] [--model large-v3]

MLX model names: tiny, base, small, medium, large-v2, large-v3 (default: large-v3)
"""
import argparse
import email.parser
import email.policy
import json
import logging
import os
import queue
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

_model_name = None

# All mlx_whisper calls must happen on the same dedicated thread.
# HTTP handlers submit jobs here and block waiting for the result.
_transcribe_queue: queue.Queue = queue.Queue()


def _transcription_worker():
    """Single dedicated thread that owns the MLX GPU context."""
    import mlx_whisper
    mlx_model = f"mlx-community/whisper-{_model_name}-mlx"
    log.info("Transcription worker ready (model=%s)", mlx_model)

    while True:
        audio_bytes, filename, result_event, result_box = _transcribe_queue.get()
        suffix = Path(filename).suffix or ".mp3"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            log.info("Transcribing %s (%.1f MB) via MLX …", filename, len(audio_bytes) / 1_048_576)
            result = mlx_whisper.transcribe(tmp_path, path_or_hf_repo=mlx_model, language="en")
            text = result.get("text", "").strip()
            language = result.get("language", "en")
            log.info("Done: %d chars, language=%s", len(text), language)
            result_box.append({"text": text, "language": language})
        except Exception as e:
            log.error("Transcription error: %s", e)
            result_box.append({"error": str(e)})
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            result_event.set()
            _transcribe_queue.task_done()


def parse_multipart(body: bytes, content_type: str) -> dict:
    """Parse multipart/form-data. Returns {name: (filename_or_None, bytes)}."""
    msg_bytes = f"Content-Type: {content_type}\r\n\r\n".encode() + body
    parser = email.parser.BytesParser(policy=email.policy.compat32)
    msg = parser.parsebytes(msg_bytes)

    result = {}
    for part in msg.walk():
        cd = part.get("Content-Disposition", "")
        if not cd or part.get_content_maintype() == "multipart":
            continue
        name = filename = None
        for param in cd.split(";"):
            param = param.strip()
            if param.startswith("name="):
                name = param[5:].strip('"')
            elif param.startswith("filename="):
                filename = param[9:].strip('"')
        if name:
            result[name] = (filename, part.get_payload(decode=True) or b"")
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

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

        try:
            fields = parse_multipart(body, content_type)
        except Exception as e:
            self.send_json(400, {"error": f"Failed to parse multipart: {e}"})
            return

        if "file" not in fields:
            self.send_json(400, {"error": "Missing 'file' field"})
            return

        filename, audio_bytes = fields["file"]
        filename = filename or "audio.mp3"

        if not audio_bytes:
            self.send_json(400, {"error": "Empty file field"})
            return

        # Submit to the dedicated MLX thread and wait for result
        result_event = threading.Event()
        result_box: list = []
        _transcribe_queue.put((audio_bytes, filename, result_event, result_box))
        result_event.wait(timeout=1800)  # 30 min max

        if not result_box:
            self.send_json(500, {"error": "Transcription timed out"})
            return

        result = result_box[0]
        if "error" in result:
            self.send_json(500, result)
        else:
            self.send_json(200, result)


def main():
    global _model_name
    parser = argparse.ArgumentParser(description="mlx-whisper OpenAI-compatible server")
    parser.add_argument("--port", type=int, default=18797)
    parser.add_argument("--model", default="large-v3")
    args = parser.parse_args()
    _model_name = args.model

    # Start the dedicated MLX transcription thread
    worker = threading.Thread(target=_transcription_worker, daemon=True)
    worker.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log.info("Listening on 127.0.0.1:%d (model=%s, backend=mlx)", args.port, _model_name)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
