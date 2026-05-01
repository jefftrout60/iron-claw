#!/bin/bash
# Runs every 5 minutes via launchd StartInterval.
# Scans Health DB export/ and State of Mind/ for new .json files,
# validates, imports, and archives processed files.

AUTO_EXPORT_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Auto Export"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/Library/Logs/ironclaw/health-watch.log"
LOCKDIR="$HOME/Library/Logs/ironclaw/.health-watch.lock"

mkdir -p "$(dirname "$LOG")"

# Prevent overlapping runs — mkdir is atomic on macOS (flock not available)
mkdir "$LOCKDIR" 2>/dev/null || exit 0
trap 'rmdir "$LOCKDIR"' EXIT

for WATCH_DIR in "$AUTO_EXPORT_DIR/Health DB export" "$AUTO_EXPORT_DIR/State of Mind"; do
    ARCHIVE_DIR="$HOME/Library/Logs/ironclaw/health-archive/$(basename "$WATCH_DIR")"
    mkdir -p "$ARCHIVE_DIR"

    for f in "$WATCH_DIR"/*.json; do
        [[ -f "$f" ]] || continue  # skip if glob matched nothing

        # Stability check: skip files still being written by iCloud sync
        size1=$(stat -f%z "$f" 2>/dev/null)
        sleep 2
        size2=$(stat -f%z "$f" 2>/dev/null)
        if [[ "$size1" != "$size2" ]]; then
            echo "[$(date -Iseconds)] SKIP: still syncing $(basename "$f")" >> "$LOG"
            continue
        fi

        # Validate JSON before processing
        python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" 2>/dev/null || {
            echo "[$(date -Iseconds)] SKIP: invalid JSON $(basename "$f")" >> "$LOG"
            continue
        }

        echo "[$(date -Iseconds)] Importing $(basename "$f") from $(basename "$WATCH_DIR")" >> "$LOG"
        python3 "$REPO_DIR/scripts/import-apple-health-json.py" --file "$f" >> "$LOG" 2>&1
        if [[ $? -eq 0 ]]; then
            STAMP=$(date +%Y%m%d_%H%M%S)
            mv "$f" "$ARCHIVE_DIR/export_${STAMP}.json"
            echo "[$(date -Iseconds)] Archived export_${STAMP}.json" >> "$LOG"
        else
            echo "[$(date -Iseconds)] FAILED: left in place for retry" >> "$LOG"
        fi
    done
done
