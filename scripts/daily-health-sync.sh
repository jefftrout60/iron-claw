#!/usr/bin/env bash
# Daily health data sync: backup → Oura → Withings
# Run by launchd daily at 03:00. Logs to /tmp/health-sync.log.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON=/usr/local/bin/python3.13

echo "=== Health sync $(date) ==="

# Backup first — abort if it fails
"$REPO_ROOT/scripts/backup-health-db.sh"

# Oura sync (incremental)
echo "--- Oura sync ---"
$PYTHON "$REPO_ROOT/scripts/oura-sync.py"

# Withings sync (incremental)
echo "--- Withings sync ---"
$PYTHON "$REPO_ROOT/scripts/withings-sync.py"

echo "=== Health sync complete $(date) ==="
