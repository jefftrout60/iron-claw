#!/usr/bin/env python3
"""
Webhook filter proxy for BlueBubbles → OpenClaw.
Drops outgoing messages before they reach OpenClaw,
preventing the bot from looping on its own replies.

BlueBubbles fires three webhook events for every bot reply:
  1. isFromMe=True, isDelivered=False  (message just queued via API)
  2. isFromMe=True, isDelivered=True   (message delivered to recipient)
  3. isFromMe=False, new ROWID         (iMessage sync echo — this would loop)

Jeff's own messages from his phone arrive as:
  1. isFromMe=True, isDelivered=True   (already sent from phone — no isDelivered=False step)
  2. isFromMe=False                    (the actual incoming — we want this)

Strategy: cache text when isFromMe=True AND isDelivered=False (bot sends only).
Drop any isFromMe=False whose text is in that cache (it's a send-echo, not a real reply).

Usage: python3 bluebubbles-filter-proxy.py [listen_port] [forward_port]
Defaults: listen=18796, forward=18792
"""
import http.server
import urllib.request
import json
import sys
import threading
import time
import datetime

LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18796
FORWARD_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 18792
SENT_TTL = 60  # seconds to remember a sent text


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# --- sent-text dedup cache ---
_sent_lock = threading.Lock()
_sent_texts: dict[str, float] = {}  # text -> expiry timestamp


def _cache_sent(text: str) -> None:
    if not text:
        return
    with _sent_lock:
        _sent_texts[text] = time.monotonic() + SENT_TTL
        # Prune expired entries
        now = time.monotonic()
        for k in [k for k, v in _sent_texts.items() if v < now]:
            del _sent_texts[k]


def _was_sent(text: str) -> bool:
    if not text:
        return False
    with _sent_lock:
        expiry = _sent_texts.get(text)
        return expiry is not None and expiry > time.monotonic()


class FilterHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default access log

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        try:
            payload = json.loads(body)
            msg = payload.get("data", payload)

            is_from_me = msg.get("isFromMe") or msg.get("is_from_me")
            is_delivered = msg.get("isDelivered") or msg.get("is_delivered")
            text = msg.get("text") or ""
            event_type = payload.get("type", "?")

            if is_from_me is True:
                # When the bot sends via API the first webhook has isDelivered=False.
                # Cache the text so we can drop the iMessage sync echo below.
                if is_delivered is not True:
                    _cache_sent(text)
                log(f"DROPPED isFromMe=True delivered={is_delivered} type={event_type!r} text={text[:60]!r}")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return

            if _was_sent(text):
                log(f"DROPPED echo isFromMe=False type={event_type!r} text={text[:60]!r}")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                return

            log(f"FORWARD isFromMe=False type={event_type!r} text={text[:60]!r}")

        except Exception as e:
            log(f"PARSE ERROR: {e} — forwarding anyway")

        # Forward to OpenClaw
        target_url = f"http://127.0.0.1:{FORWARD_PORT}{self.path}"
        req = urllib.request.Request(target_url, data=body, method="POST")
        for key, val in self.headers.items():
            if key.lower() not in ("host", "content-length"):
                req.add_header(key, val)
        req.add_header("Content-Length", str(len(body)))

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                self.end_headers()
                self.wfile.write(resp_body)
        except Exception as e:
            log(f"FORWARD ERROR: {e}")
            self.send_response(502)
            self.end_headers()
            self.wfile.write(b"forward error")

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"bluebubbles-filter-proxy ok")


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), FilterHandler)
    log(f"listening on 127.0.0.1:{LISTEN_PORT} → forwarding to 127.0.0.1:{FORWARD_PORT}")
    server.serve_forever()
