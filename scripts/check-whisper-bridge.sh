#!/usr/bin/env bash
# check-whisper-bridge.sh — Report whether the whisper-server bridge is up.
#
# Usage:
#   ./scripts/check-whisper-bridge.sh [PORT]
#
# Arguments:
#   PORT   Port to check (default: 18797)
#
# Exit codes:
#   0  — bridge is up
#   1  — bridge is down or unreachable

PORT="${1:-18797}"
URL="http://localhost:$PORT/"

# Curl with short timeout; -s silent, -o capture body, -w get HTTP code
RESPONSE=$(curl -s --max-time 5 --write-out "\n%{http_code}" "$URL" 2>/dev/null)
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [[ "$HTTP_CODE" =~ ^[0-9]+$ ]] && [[ "$HTTP_CODE" -ge 200 && "$HTTP_CODE" -lt 500 ]]; then
  echo "whisper-bridge: UP  (port $PORT, HTTP $HTTP_CODE)"

  # Try to extract model info from the response body if present.
  # whisper-server returns an HTML test page; look for model path in the page.
  MODEL_LINE=$(echo "$BODY" | python3 -c "
import sys, re
body = sys.stdin.read()
# Look for model path reference in whisper-server HTML
m = re.search(r'(ggml-[a-zA-Z0-9._-]+\.bin)', body)
if m:
    print('  model: ' + m.group(1))
" 2>/dev/null || true)

  [[ -n "$MODEL_LINE" ]] && echo "$MODEL_LINE"
  exit 0
else
  echo "whisper-bridge: DOWN  (port $PORT, no response)"
  echo "  Start with: launchctl load ~/Library/LaunchAgents/com.ironclaw.whisper-bridge.plist"
  echo "  Logs:       tail -f ~/Library/Logs/whisper-bridge.log"
  exit 1
fi
