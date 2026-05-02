#!/usr/bin/env bash
# Backup health.db before any import run.
# Keeps last 7 daily backups. Exits non-zero if backup fails.
# Usage: ./scripts/backup-health-db.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB_PATH="$REPO_ROOT/agents/sample-agent/workspace/health/health.db"
BACKUP_DIR="$REPO_ROOT/agents/sample-agent/workspace/health/backups"

if [[ ! -f "$DB_PATH" ]]; then
    echo "health.db not found at $DB_PATH — nothing to back up" >&2
    exit 0
fi

mkdir -p "$BACKUP_DIR"

BACKUP_FILE="$BACKUP_DIR/health-$(date +%F).db"
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"
echo "Backed up to $BACKUP_FILE"

# Prune backups older than 30 days
find "$BACKUP_DIR" -name "health-*.db" -mtime +30 -delete
REMAINING=$(find "$BACKUP_DIR" -name "health-*.db" | wc -l | xargs)
echo "Backup directory: $REMAINING file(s) retained"
