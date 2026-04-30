#!/usr/bin/env python3
"""
One-time Withings OAuth2 token setup.

Registers app at developer.withings.com first:
  - Redirect URI: http://localhost:8080/callback

Usage:
  python3 scripts/withings-auth.py

Reads WITHINGS_CLIENT_ID and WITHINGS_CLIENT_SECRET from Keychain
(com.ironclaw.withings / client_id + client_secret), falling back to .env.
Writes access_token, refresh_token, token_expiry to Keychain.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).parent.parent
_ENV_PATH = _REPO_ROOT / "agents/sample-agent/.env"
sys.path.insert(0, str(Path(__file__).parent))
from keychain import kc_get, kc_set

AUTH_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
REDIRECT_URI = "http://localhost:8080/callback"
SCOPE = "user.metrics"


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not _ENV_PATH.exists():
        return env
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def update_env(updates: dict[str, str]) -> None:
    """Write/overwrite specific keys in .env without touching other lines."""
    lines = _ENV_PATH.read_text().splitlines() if _ENV_PATH.exists() else []
    updated_keys: set[str] = set()

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # Append any keys that weren't already in the file
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    _ENV_PATH.write_text("\n".join(new_lines) + "\n")


# ---------------------------------------------------------------------------
# Local callback server
# ---------------------------------------------------------------------------

_auth_code: str | None = None
_server_error: str | None = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _auth_code, _server_error
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            _server_error = params["error"][0]
            self._respond("Authorization failed: " + _server_error)
        elif "code" in params:
            _auth_code = params["code"][0]
            self._respond("Authorization successful. You can close this tab.")
        else:
            _server_error = "No code or error in callback"
            self._respond("Unexpected callback: " + self.path)

    def _respond(self, message: str) -> None:
        body = f"<html><body><p>{message}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress access log noise


def _run_server(server: HTTPServer) -> None:
    server.handle_request()  # handle exactly one request then stop


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    """POST to Withings token endpoint; returns parsed response body."""
    resp = requests.post(TOKEN_URL, data={
        "action": "requesttoken",
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    if payload.get("status") != 0:
        raise RuntimeError(f"Token exchange failed: status={payload.get('status')} — {payload}")

    return payload["body"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Prefer Keychain; fall back to .env for initial setup before migration
    client_id = kc_get("com.ironclaw.withings", "client_id") or load_env().get("WITHINGS_CLIENT_ID", "")
    client_secret = kc_get("com.ironclaw.withings", "client_secret") or load_env().get("WITHINGS_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("Error: WITHINGS_CLIENT_ID and WITHINGS_CLIENT_SECRET not found in Keychain or .env",
              file=sys.stderr)
        sys.exit(1)

    # Build authorization URL
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": "withings-auth",
    })
    auth_url = f"{AUTH_URL}?{params}"

    # Start local callback server
    server = HTTPServer(("127.0.0.1", 8080), CallbackHandler)
    thread = threading.Thread(target=_run_server, args=(server,), daemon=True)
    thread.start()

    print("Opening Withings authorization page in your browser...")
    print(f"If it doesn't open automatically, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback (up to 120 seconds)
    thread.join(timeout=120)

    if _server_error:
        print(f"Authorization failed: {_server_error}", file=sys.stderr)
        sys.exit(1)

    if not _auth_code:
        print("Timed out waiting for authorization callback.", file=sys.stderr)
        sys.exit(1)

    print("Authorization code received. Exchanging for tokens...")

    try:
        body = exchange_code(client_id, client_secret, _auth_code)
    except Exception as e:
        print(f"Token exchange failed: {e}", file=sys.stderr)
        sys.exit(1)

    access_token = body["access_token"]
    refresh_token = body["refresh_token"]
    expires_in = int(body.get("expires_in", 10800))
    token_expiry = int(time.time()) + expires_in

    kc_set("com.ironclaw.withings", "access_token", access_token)
    kc_set("com.ironclaw.withings", "refresh_token", refresh_token)
    kc_set("com.ironclaw.withings", "token_expiry", str(token_expiry))

    print("Tokens written to Keychain (com.ironclaw.withings)")
    print(f"Access token expires in {expires_in // 3600}h ({expires_in // 60}min)")
    print("Setup complete. You can now run: python3 scripts/withings-sync.py --historical")


if __name__ == "__main__":
    main()
