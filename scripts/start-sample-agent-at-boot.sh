#!/usr/bin/env bash
# start-sample-agent-at-boot.sh — Wait for host IP, then run compose-up.sh sample-agent -d.
# Used by systemd at boot so sample-agent starts only after the network (e.g. Wi‑Fi) has an IP.
#
# Usage: run by ironclaw-sample-agent.service (absolute path). No args.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

WAIT_TIMEOUT=90
WAIT_INTERVAL=5
ELAPSED=0

while [[ $ELAPSED -lt $WAIT_TIMEOUT ]]; do
  IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  if [[ -n "$IP" ]]; then
    break
  fi
  sleep "$WAIT_INTERVAL"
  ELAPSED=$((ELAPSED + WAIT_INTERVAL))
done

if [[ -z "$IP" ]]; then
  echo "start-sample-agent-at-boot: No host IP after ${WAIT_TIMEOUT}s (hostname -I empty). Aborting." >&2
  exit 1
fi

"$SCRIPT_DIR/compose-up.sh" sample-agent -d
