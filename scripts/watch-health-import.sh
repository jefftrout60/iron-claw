#!/bin/bash
# Triggered by launchd WatchPaths when iCloud Drive Auto Export folder changes.
# Scans both Health DB export/ and State of Mind/ for new .json files,
# validates, imports, and archives processed files.

AUTO_EXPORT_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Auto Export"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/Library/Logs/ironclaw/health-watch.log"

mkdir -p "$(dirname "$LOG")"

for WATCH_DIR in "$AUTO_EXPORT_DIR/Health DB export" "$AUTO_EXPORT_DIR/State of Mind"; do
    ARCHIVE_DIR="$WATCH_DIR/archive"
    mkdir -p "$ARCHIVE_DIR"

    for f in "$WATCH_DIR"/*.json; do
        [[ -f "$f" ]] || continue  # skip if glob matched nothing
        echo "[$(date -Iseconds)] Found: $(basename "$f") in $(basename "$WATCH_DIR")" >> "$LOG"

        # Validate JSON before processing (handles partial iCloud sync artifacts)
        python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$f" 2>/dev/null || {
            echo "[$(date -Iseconds)] SKIP: invalid/incomplete JSON, will retry next change" >> "$LOG"
            continue
        }

        python3 "$REPO_DIR/scripts/import-apple-health-json.py" --file "$f" >> "$LOG" 2>&1
        if [[ $? -eq 0 ]]; then
            STAMP=$(date +%Y%m%d_%H%M%S)
            mv "$f" "$ARCHIVE_DIR/export_${STAMP}.json"
            echo "[$(date -Iseconds)] Archived: export_${STAMP}.json" >> "$LOG"
        else
            echo "[$(date -Iseconds)] FAILED: import error, file left for manual retry" >> "$LOG"
        fi
    done
done
